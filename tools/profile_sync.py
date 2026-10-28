#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台履歷登記、同步基準、專用答案與附件核對。"""
import os, sys, re, json, html, argparse, unicodedata, hashlib, subprocess, contextlib
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import markdown_pdf
REG = cf.path('profiles.json')
PLATFORMS = {
    '104': ('104.com.tw',),
    'cake': ('cake.me', 'cake.com', 'cakeresume.com'),
    'yourator': ('yourator.co',),
    'linkedin': ('linkedin.com',),
}


def platform_of(url):
    value = str(url or '').strip().casefold()
    if value in PLATFORMS:
        return value
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').rstrip('.').casefold()
    except ValueError:
        return None
    if host in ('localhost', '127.0.0.1', '::1'):
        query_platform = (parse_qs(parsed.query).get('platform') or [''])[0].casefold()
        for name, domains in PLATFORMS.items():
            if any(query_platform == domain for domain in domains):
                return name
    for name, domains in PLATFORMS.items():
        if any(host == domain or host.endswith('.' + domain) for domain in domains):
            return name
    return None



def profile_key(url):
    """用已知平台名稱或申請頁網域定位平台履歷;是否比對由 delivery.method 決定。"""
    platform = platform_of(url)
    if platform:
        return platform
    from urllib.parse import urlsplit
    return (urlsplit(url or '').hostname or '').casefold()


def _load_registry():
    """從檔案讀一份新的;要改登記表的(_registry_writer 裡)用這個,改的是自己那一份。"""
    try:
        with open(REG, encoding='utf-8') as f:
            reg = json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise ValueError(f'平台履歷登記表讀不出來({os.path.basename(REG)}:{e})') from e
    if not isinstance(reg, dict):
        raise ValueError('平台履歷登記資料不是物件')
    return reg


_REG_CACHE = (None, None)


def stamp():
    """登記表現在是哪一份:換檔(inode)、內容被改(修改時間、狀態變更時間、大小)都會變;沒有這個檔回 None。"""
    try:
        st = os.stat(REG)
    except FileNotFoundError:
        return None
    return (REG, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)


def registry():
    """只讀用:檔案沒換過就用上次解析好的那一份,回傳的不能改(#607)。
    登記表有幾十 MB,看板每算一次卡片下一步就要讀好幾次。"""
    global _REG_CACHE
    key = stamp()
    if key is None:
        return {}
    cached_key, reg = _REG_CACHE
    if cached_key != key:
        reg = _load_registry()
        _REG_CACHE = (key, reg)
    return reg



def _registry_writer(method):
    """共用既有檔案鎖，保護每個完整的讀→改→寫。"""
    from functools import wraps

    @wraps(method)
    def write(*args, **kwargs):
        import board_doc
        os.makedirs(os.path.dirname(REG) or '.', exist_ok=True)
        with board_doc.live_lock(REG):
            return method(*args, **kwargs)
    return write


def _save_registry(reg):
    import tempfile
    folder = os.path.dirname(REG)
    if folder:
        os.makedirs(folder, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=folder or '.', encoding='utf-8', delete=False) as f:
            temporary = f.name
            json.dump(reg, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, REG)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


# 平台履歷的身分:平台 + 那一份的編號。同一份的網址常有不同寫法(多一個參數、參數順序不同),
# 以前照完整網址記核對紀錄,同一份變成兩筆:一筆通過、一筆從來沒通過(#313)。
# 編號放在網址參數裡的平台照這張表拿;其他平台用網址路徑(去掉追蹤用的參數、參數排順序)。
ID_PARAMS = {'104': 'vno'}
_NOISE_PARAMS = ('utm_', 'ref', 'from', 'source', 'lang', 'locale')


def identity(profile_url):
    """平台履歷網址 → 身分字串(同一份不管怎麼寫都一樣)。空的回空字串。"""
    value = str(profile_url or '').strip()
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    platform = platform_of(value) or (parsed.hostname or '').casefold()
    query = parse_qs(parsed.query)
    param = ID_PARAMS.get(platform)
    if param and query.get(param):
        return f'{platform}#{query[param][0]}'
    kept = sorted(f'{k}={v}' for k, vs in query.items() for v in vs
                  if not k.casefold().startswith(_NOISE_PARAMS))
    return f'{platform}#{parsed.path.rstrip("/")}' + ('?' + '&'.join(kept) if kept else '')


@_registry_writer
def remember(platform, lang, variant, read_url, edit_url=None):
    """agent 第一次告訴我們那一份在哪:記下來,下次程式自己讀。"""
    reg = _load_registry()
    entry = dict((reg.get(platform) or {}).get(f'{lang}/{variant}') or {})
    entry.update(read=read_url, edit=edit_url or read_url)
    reg.setdefault(platform, {})[f'{lang}/{variant}'] = entry
    _save_registry(reg)


def where(platform, lang, variant):
    return (registry().get(platform) or {}).get(f'{lang}/{variant}')


def _master_fp(lang, variant):
    import source_sync
    paths = [cf.master(part, lang) for part in variant.split('+')]
    return '+'.join(source_sync.fingerprint(p) for p in paths) if all(paths) else ''


def slot_attachments(lang, variant):
    """固定平台履歷上該有的附件(本機是真相,平台是鏡像):這份履歷、這個語言設定要附的每一份。
    回 [(名稱, 卡片實際會寄的檔)]:Markdown 原稿換成它排好的 PDF。同步的比對、叫 agent 上傳、指紋都吃這一份。"""
    ids = set(variant.split('+'))
    return [(str(a.get('name') or os.path.basename(a['files'][lang])), markdown_pdf.sent_file(cf.path(a['files'][lang])))
            for a in cf.ATTACHMENTS
            if a.get('enabled', True) and (a.get('files') or {}).get(lang)
            and (not a.get('resume_ids') or ids & set(a['resume_ids']))]


def _attachments_fp(lang, variant):
    return '+'.join(f'{name}:{_sha_file(path)}' for name, path in slot_attachments(lang, variant))


@_registry_writer
def remember_sync(platform, lang, variant, source, answers, master_fp=None, visuals=(), used_answers=None, attachments=None):
    """存檔成功後才更新這一份的原始履歷、常用答案、母稿與附件指紋和待看的截圖。這一份「是不是最新」只看這一筆(sync_baseline)。"""
    reg = _load_registry()
    entry = (reg.get(platform) or {}).get(f'{lang}/{variant}')
    if not isinstance(entry, dict):
        raise ValueError('平台履歷未登記')
    old = entry.get('sync_baseline') or {}
    entry['sync_baseline'] = {'source': source, 'answers': list(answers),
                              'used_answers': list(used_answers if used_answers is not None
                                                   else old.get('used_answers', [e.get('k') for e in answers])),
                              'master': master_fp if master_fp is not None else _master_fp(lang, variant),
                              'attachments': attachments if attachments is not None else _attachments_fp(lang, variant)}
    if visuals:
        entry['sync_visuals'] = list(entry.get('sync_visuals') or []) + list(visuals)
    _save_registry(reg)


@_registry_writer
def remember_sync_visuals(platform, lang, variant, visuals):
    """只追加本輪逐處紀錄；驗收或失敗也要讓下一張卡看得到，不前進同步基準。"""
    reg = _load_registry()
    entry = (reg.get(platform) or {}).get(f'{lang}/{variant}')
    if not isinstance(entry, dict):
        raise ValueError('平台履歷未登記')
    previous = list(entry.get('sync_visuals') or [])
    old_card = entry.pop('sync_visuals_card', None)
    if old_card:
        for row in previous:
            row.setdefault('card', old_card)
    entry['sync_visuals'] = previous + list(visuals)
    _save_registry(reg)


@_registry_writer
def attach_sync_visuals(platform, lang, variant, card_url, board):
    """每一輪截圖只交給其後第一張用這份履歷的卡。"""
    import shutil
    import evidence
    reg = _load_registry()
    entry = (reg.get(platform) or {}).get(f'{lang}/{variant}') or {}
    rows = entry.get('sync_visuals') or []
    old_card = entry.pop('sync_visuals_card', None)
    if old_card:
        for row in rows:
            row.setdefault('card', old_card)
    chosen = [row for row in rows if row.get('card') in (None, card_url)]
    if not chosen:
        return []
    card_home = evidence.card_dir(card_url, board)
    flow_home = evidence.flow_dir('sync_profiles', board)
    for round_id in {row.get('evidence') for row in chosen
                     if isinstance(row.get('evidence'), str) and evidence.ROUND.fullmatch(row['evidence'])}:
        source = os.path.join(flow_home, round_id)
        target = os.path.join(card_home, round_id)
        if os.path.isdir(source) and not os.path.isdir(target):
            shutil.copytree(source, target)
    available = [row for row in chosen if (row.get('item') and isinstance(row.get('evidence'), str)
                 and evidence.ROUND.fullmatch(row['evidence'])
                 and os.path.isdir(os.path.join(card_home, row['evidence'])))
                 or evidence.path(card_url, row.get('shot'), board)]
    changed = bool(old_card)
    for row in available:
        if not row.get('card'):
            row['card'] = card_url
            changed = True
    if changed:
        _save_registry(reg)
    return available


@_registry_writer
def clear_sync_visuals(platform, lang, variant, card_url, board=None):
    """送出後只移除待看索引；逐處文字與截圖仍留在證據夾供報告重建。"""
    reg = _load_registry()
    entry = (reg.get(platform) or {}).get(f'{lang}/{variant}')
    if isinstance(entry, dict):
        old_card = entry.pop('sync_visuals_card', None)
        rows = entry.get('sync_visuals') or []
        for row in rows:
            if old_card:
                row.setdefault('card', old_card)
        claimed = [row for row in rows if row.get('card') == card_url]
        if claimed:
            left = [row for row in rows if row.get('card') != card_url]
            if left:
                entry['sync_visuals'] = left
            else:
                entry.pop('sync_visuals', None)
        if claimed or old_card:
            _save_registry(reg)


def changed_answers(baseline, current):
    """已用在這份平台履歷的常用答案有變才需再同步。"""
    old = {e['k']: e for e in baseline.get('answers') or [] if e.get('k')}
    now = {e['k']: e for e in current if e.get('k')}
    used = set(baseline.get('used_answers', old)) | {key for key, entry in old.items() if entry.get('platform')}
    keys = ('q', 'v', 'zh')
    return [(key, old[key], new) for key, new in now.items() if key in old and key in used
            and any(str(old[key].get(field) or '') != str(new.get(field) or '') for field in keys)]


def platform_answers(fb, platform, lang, variant, profile_url=''):
    """這份平台履歷專用的常用答案；確認日期不參與內容版本比對。"""
    target = identity(profile_url) if profile_url else ''
    return [{key: str(entry.get(key) or '') for key in ('k', 'q', 'v', 'zh')}
            for entry in fb.get('__ans__') or [] if isinstance(entry, dict)
            and entry.get('platform') == platform and entry.get('profile') == f'{lang}/{variant}'
            and ((target and identity(entry.get('profile_url')) == target)
                 or (not target and not entry.get('profile_url')))]


def slots(platform=None):
    """登記過的每一份平台履歷:[(平台, 語言, 履歷, 那一筆)]。"""
    return [(p, key.partition('/')[0], key.partition('/')[2], entry) for p, entries in registry().items()
            if not str(p).startswith('_') and isinstance(entries, dict) and (not platform or p == platform)
            for key, entry in entries.items() if '/' in key and isinstance(entry, dict) and entry.get('read')]


def _shared_answers(board):
    import board_doc
    import form_record
    fb = json.loads(board_doc.load(board)['fb'])
    return fb, [{k: str(e.get(k) or '') for k in ('k', 'q', 'v', 'zh')} for e in form_record.find_shared(fb)]


def state(platform, lang, variant, entry, board=None, shared=None):
    """這一份平台履歷跟本機的關係:ok / master-changed(原始履歷、附件或用到的常用答案改了,還沒同步) / unchecked(沒同步過)。
    不靠誰記得:本機一改,狀態當下就變。board 給了才看常用答案(shared:已經讀好的 _shared_answers)。"""
    baseline = entry.get('sync_baseline') or {}
    fp = _master_fp(lang, variant)
    if not fp or 'master' not in baseline:
        return 'unchecked'
    if baseline['master'] != fp or baseline.get('attachments') != _attachments_fp(lang, variant):
        return 'master-changed'
    if board:
        fb, answers = shared or _shared_answers(board)
        if changed_answers(baseline, answers + platform_answers(fb, platform, lang, variant)):
            return 'master-changed'
    return 'ok'


def _confirmation_pending(fb, platform, lang, variant, entry):
    """這一份同步時放進去、跟原文不一樣的寫法(agent 代填、記成這個平台專用的常用答案)還沒等到他確認。"""
    slot = f'{lang}/{variant}'
    synced = {answer.get('k') for answer in (entry.get('sync_baseline') or {}).get('answers') or []}
    return any(answer.get('inf') and answer.get('k') in synced and answer.get('platform') == platform
               and answer.get('profile') == slot and not answer.get('profile_url')
               for answer in fb.get('__ans__') or [] if isinstance(answer, dict))


def status(board=None):
    """登記過、有原始履歷的每一份平台履歷的狀態(看板設定頁「全部同步」那一區),以及是否仍有代填內容等使用者確認。"""
    shared = _shared_answers(board) if board else None
    out = []
    for p, lang, v, entry in slots():
        if not _master_fp(lang, v):
            continue
        st = state(p, lang, v, entry, board, shared)
        out.append({'platform': p, 'lang': lang, 'variant': v, 'state': st, 'url': entry['read'],
                    'confirmation_pending': bool(st == 'ok' and shared
                                                 and _confirmation_pending(shared[0], p, lang, v, entry))})
    return out


@_registry_writer
def remember_name(platform, lang, variant, name):
    """平台上那一份叫什麼(申請頁選平台履歷時顯示的字):程式在那一份的頁面上看到過才記。"""
    reg = _load_registry()
    slot = (reg.get(platform) or {}).get(f'{lang}/{variant}')
    if isinstance(slot, dict) and slot.get('name') != name:
        slot['name'] = name
        _save_registry(reg)


def profile_field(field):
    """申請表上選平台履歷的那一格(原生或自製下拉選單,名稱寫著履歷、CV、profile)。"""
    return (isinstance(field, dict)
            and (str(field.get('type') or '').startswith('select') or field.get('type') == 'combobox'
                 or field.get('role') == 'combobox')
            and bool(re.search(r'resume|履歷|\bcv\b|\bprofile\b|profile_id',
                               str(field.get('label') or '') + ' ' + str(field.get('name') or ''), re.I)))


def picked(page, names):
    """申請頁上選好的是哪幾份(names:{哪一份: 平台上的名稱})。先看下拉選單選好的字;
    104 這種選單不是表單欄位,選好的值只是一行字:一行裡只出現一份的名稱才算選了它(列出全部選項的那行不算)。"""
    fields = page.get('fields') or []
    shown = {_n(f.get('shown') or f.get('value')) for f in fields
             if not isinstance(f.get('value'), list)}
    hit = {slot for slot, n in names.items() if _n(n) in shown}
    profile_choice = any(profile_field(f) for f in fields)
    # 原生選單已讀到選取值時,body 內未選的 option 文字不能推翻它。
    if hit or profile_choice:
        return hit
    for line in page.get('lines') or []:
        inside = {slot for slot, n in names.items() if _n(n) and _n(n) in _n(line)}
        if len(inside) == 1:
            hit |= inside
    return hit


def linked(page, platform):
    """申請頁上連到這個平台某一份平台履歷的連結(apply_tab.READ_FORM 的 profileLinks,例如應徵彈窗的「預覽履歷」)
    → 那幾份的身分。編號放在網址參數裡的平台(ID_PARAMS)才認得出來;其他平台回空集合。"""
    param = ID_PARAMS.get(platform)
    if not param:
        return set()
    out = set()
    for href in page.get('profileLinks') or []:
        with contextlib.suppress(ValueError):   # 寫壞的網址:不是那一份
            if platform_of(href) == platform and parse_qs(urlsplit(str(href)).query).get(param):
                out.add(identity(href))
    return out


def _label(slots, slot):
    """給人看的「哪一份」:平台上的名稱(記過才有),不然是語言、履歷和那一份的網址。"""
    e = slots.get(slot) or {}
    if e.get('name'):
        return f'「{e["name"]}」'
    lang, _, var = slot.partition('/')
    return f'{LANG_WORDS.get(lang, lang)}「{cf.resume_name(var) or var}」那一份({e.get("read") or slot})'


def picked_problem(page, job, fb, url, d=None):
    """程式自己讀申請頁:選的平台履歷是不是這張卡該用的那一份(decided;算好了就從 d 給)。對了回空字串,不是就回原因。
    認得出編號的平台(104)照頁面上連到那一份的連結(身分)比;認不出的照平台上的名稱比(名稱在那一份的頁面上看到過才記)。"""
    d = d or decided(job, fb, url)
    slots = {slot: e for slot, e in (registry().get(d['platform']) or {}).items() if isinstance(e, dict)}
    want = f"{d['lang']}/{d['variant']}"
    ids = linked(page, d['platform'])
    if ids:
        by_id = {identity(e['read']): slot for slot, e in slots.items() if e.get('read')}
        shown = lambda i: _label(slots, by_id[i]) if i in by_id else i
        if d['profile_kind'] == 'custom':
            fixed = sorted(i for i in ids if i in by_id)
            return f'選錯平台履歷:該選這張卡新開的客製版、頁面上是{shown(fixed[0])}' if fixed else ''
        want_id = identity(d.get('fixed_url'))
        if ids == {want_id}:
            return ''
        if want_id in ids:
            return f'申請頁上連到好幾份平台履歷,看不出選的是哪一份(該選{_label(slots, want)})'
        return f'選錯平台履歷:該選{_label(slots, want)}、頁面上是{shown(sorted(ids)[0])}'
    names = {slot: e['name'] for slot, e in slots.items() if e.get('name')}
    hit = picked(page, names)
    if d['profile_kind'] == 'custom':
        wrong = sorted(names[s] for s in hit)
        return f'選錯平台履歷:該選這張卡新開的客製版、頁面上是「{wrong[0]}」' if wrong else ''
    if want not in names:
        if ID_PARAMS.get(d['platform']):
            return f'申請頁上看不出選的是哪一份平台履歷(該選{_label(slots, want)})'
        if len(slots) == 1 and want in slots and any(
                field.get('type') == 'select-one' and field.get('value')
                for field in page.get('fields') or [] if isinstance(field, dict)):
            return ''
        return f'程式不知道該選的平台履歷({want})在申請頁上叫什麼,沒辦法核對選的是哪一份'
    if want in hit:
        return ''
    if hit:
        return f'選錯平台履歷:該選「{names[want]}」、頁面上是「{names[sorted(hit)[0]]}」'
    return f'申請頁上看不出選的是哪一份平台履歷(該選「{names[want]}」)'



LANG_WORDS = {'zh': '中文', 'en': '英文', 'ja': '日文', 'ko': '韓文'}


def decided(job, fb, url):
    """這張卡該用哪一份平台履歷,由程式照這張卡現在挑的履歷、語言,和有沒有收下的客製履歷決定;不問 agent,也不看舊紀錄。
    {'platform', 'lang', 'variant', 'profile_kind', 'fixed_url'(固定版已登記才有), 'name'(平台上那份的名稱,記過才有)}"""
    import ship
    platform = profile_key(url)
    resume_id, lang = ship.resolve(job, fb)
    out = {'platform': platform, 'lang': lang, 'variant': resume_id,
           'profile_kind': 'custom' if has_custom_resume(job, fb) else 'fixed'}
    entry = where(platform, lang, resume_id) if platform and resume_id else None
    if entry and entry.get('read'):
        out['fixed_url'] = entry['read']
        if entry.get('name'):
            out['name'] = entry['name']
    return out


def delivery_for(job, fb, url, reported):
    """交件單上的投遞方式 → 程式認的那一份。直接上傳、不用平台履歷照 agent 說的(申請頁上有沒有上傳欄它看得到);
    用平台履歷時,固定版還是客製版、用哪一份,一律照程式決定的(decided)。
    客製版是 agent 這一輪在平台上新開的,網址只有它知道,只收那一格。"""
    reported = reported if isinstance(reported, dict) else {}
    if reported.get('method') != 'platform_profile':
        return dict(reported)
    d = decided(job, fb, url)
    out = {'method': 'platform_profile', 'profile_kind': d['profile_kind']}
    chosen = d.get('fixed_url') if d['profile_kind'] == 'fixed' else str(reported.get('profile_url') or '').strip()
    if chosen:
        out['profile_url'] = chosen
    return out


def _n(value):
    """平台履歷選單顯示名稱的排版差異不改變選到哪一份。"""
    value = unicodedata.normalize('NFKC', html.unescape(str(value or '')))
    value = value.translate(str.maketrans('', '', '（）'))
    return re.sub(r'[\s:：|｜()\[\]【】「」"\'、,，.。;；!！?？*`#>_\-–—~]+', '', value).lower()


_LINK = re.compile(r'\[([^\]]*)\]\(([^)\s]+)\)')


def md_sections(text):
    """markdown 原稿 → 每條或每段的文字與連結；標題只作任務名稱，不定位平台欄位。"""
    text = re.sub(r'<style\b.*?</style>|<!--.*?-->|<img\b[^>]*>', '', text, flags=re.S | re.I)
    out, head, buf = [], '', []
    def flush():
        # 平台是純文字:粗體、行內程式碼的記號不打上去,只留字
        t = re.sub(r'`([^`]*)`', r'\1', ' '.join(buf)).strip()
        if t:
            linked = _LINK.findall(t)
            out.append((f'{head or "開頭"}[{len(out)}]', _LINK.sub(r'\1', t).replace('**', ''),
                        [url for _label, url in linked], _LINK.sub('', t).replace('**', '').strip(),
                        [label for label, _url in linked]))
        buf.clear()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('#'):
            flush(); head = s.lstrip('#').strip(); continue
        if not s:
            flush(); continue
        if re.match(r'^([-*+]|\d+\.)\s', s):
            flush(); buf.append(re.sub(r'^([-*+]|\d+\.)\s+', '', s)); continue
        buf.append(s)
    flush()
    return out


def capture_source(path):
    """從同一份不可變 bytes 算雜湊並取文字；PDF 亦從捕獲副本抽取。"""
    import hashlib
    import tempfile
    if not path or not os.path.isfile(path) or not path.lower().endswith(('.md', '.markdown', '.txt', '.pdf')):
        raise ValueError('找不到可讀的指定履歷原稿')
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if path.lower().endswith('.pdf'):
            import settings_api
            with tempfile.NamedTemporaryFile(suffix='.pdf') as copy:
                copy.write(data)
                copy.flush()
                text = settings_api.pdf_text(copy.name)
        else:
            text = data.decode('utf-8')
    except (OSError, UnicodeError, subprocess.SubprocessError, ValueError) as e:
        raise ValueError(f'讀不出母稿 {os.path.basename(path)}({str(e)[:80]})') from e
    if not text.strip():
        raise ValueError('指定履歷原稿沒有可讀文字')
    return {'sha256': hashlib.sha256(data).hexdigest(), 'text': text}


def _sha_file(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, 'rb') as f:
            return hashlib.file_digest(f, 'sha256').hexdigest()
    except OSError:
        return None


FILE_EXT = ('.pdf', '.doc', '.docx')


def is_file_url(value):
    """平台上存的「履歷」是一份檔(不是頁面):核對靠下載下來逐位元組比,不讀文字。"""
    return urlsplit(str(value or '')).path.lower().endswith(FILE_EXT)


def _safe_page_url(value):
    parsed = urlsplit(str(value or '').strip())
    host = (parsed.hostname or '').casefold()
    return bool(parsed.scheme == 'https' and host) or bool(
        parsed.scheme == 'http' and host in ('localhost', '127.0.0.1', '::1')
    )


def loopback_url(value):
    try:
        return (urlsplit(str(value or '').strip()).hostname or '').casefold() in (
            'localhost', '127.0.0.1', '::1'
        )
    except ValueError:
        return False


def same_platform_url(value, platform, allow_local=False):
    """Loopback 是測試 fixture 專用;正式網頁必須符合平台網域。"""
    if not _safe_page_url(value):
        return False
    if loopback_url(value):
        return allow_local
    return profile_key(value) == platform


@_registry_writer
def remember_application_history(platform, url):
    """平台的應徵紀錄頁(查回音用)。網址要屬於這個平台才記。"""
    if not url or not same_platform_url(url, platform):
        return False
    reg = _load_registry()
    reg.setdefault('_application_records', {})[platform] = url
    _save_registry(reg)
    return True


def application_record_pages():
    """已知平台的應徵紀錄頁。"""
    records = registry().get('_application_records') or {}
    return [{'platform': str(platform), 'url': str(url)}
            for platform, url in records.items()
            if isinstance(url, str) and url and _safe_page_url(url)
            and same_platform_url(url, platform)]


def _custom_documents(job, fb):
    import ship
    return [
        item for item in ship.documents(job, fb)
        if (item.get('entry') or {}).get('status') == 'accepted'
        or item.get('id') == 'resume:legacy'
    ]


def has_custom_resume(job, fb):
    return any(item.get('kind') == 'resume'
               for item in _custom_documents(job, fb))


def has_custom_documents(job, fb):
    return bool(_custom_documents(job, fb))


def attachment_sources(job, fb, delivery=None):
    """這張卡要放在指定平台履歷上的附件;必要時可指定固定版原始檔。"""
    import ship
    sources = [
        item for item in ship.documents(job, fb)
        if item.get('kind') == 'attachment' and item.get('effective_path')
    ]
    profile_kind = (delivery or {}).get('profile_kind')
    if profile_kind == 'fixed' and (delivery or {}).get('original_only'):
        sources = [
            dict(item, effective_path=markdown_pdf.sent_file(item['source']))
            for item in sources
        ]
    elif profile_kind == 'custom':
        sources.extend(
            item for item in _custom_documents(job, fb)
            if item.get('kind') == 'resume' and item.get('effective_path')
        )
    return sources


def _same_bytes(left, right):
    try:
        with open(left, 'rb') as a, open(right, 'rb') as b:
            while True:
                aa, bb = a.read(1024 * 1024), b.read(1024 * 1024)
                if aa != bb:
                    return False
                if not aa:
                    return True
    except OSError:
        return False


def _delivery_key(delivery):
    return (
        delivery.get('method'),
        identity(delivery.get('profile_url')),
        delivery.get('profile_kind'),
    )


def _reported_downloads(report, field, download_dir, problems):
    """下載回來的檔(在暫存資料夾裡、真的存在)才收;report[field] = [{name, path}]。"""
    label = '申請表上傳檔' if field == 'uploaded_files' else '平台附件'
    missing = '申請表上傳檔沒有回報下載檔,不能驗內容' if field == 'uploaded_files' else '平台附件沒有下載檔'
    invalid = f'{label}有無效的下載回報'
    if field not in report or not isinstance(report.get(field), list):
        problems.append(missing)
        return []
    root = os.path.realpath(download_dir) if download_dir else None
    actual = []
    for item in report[field]:
        if not isinstance(item, dict):
            problems.append(invalid)
            continue
        name = str(item.get('name') or item.get('id') or '未命名附件')
        prefix = f'{label}「{name}」'
        path = item.get('path')
        if not isinstance(path, str) or not path or not root:
            problems.append(f'{prefix}沒有下載檔,不能當作一致')
            continue
        candidate = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
        try:
            inside = os.path.commonpath([root, candidate]) == root
        except ValueError:
            inside = False
        own = os.path.join(root, os.path.basename(path))
        if not inside and os.path.isfile(own):
            # 暫存夾是程式自己指定的:agent 抄錯資料夾名(少一個字)時,照檔名在這個夾裡找,不照抄它的完整路徑
            candidate, inside = own, True
        if not inside:
            problems.append(f'{prefix}回報路徑在暫存夾外,不採信')
            continue
        if not os.path.isfile(candidate):
            problems.append(f'{prefix}下載檔不存在,不能當作一致')
            continue
        actual.append({'name': name, 'path': candidate})
    return actual


def _same_file_set(actual_paths, expected_paths):
    if len(actual_paths) != len(expected_paths):
        return False
    remaining = list(expected_paths)
    for path in actual_paths:
        match = next((i for i, expected in enumerate(remaining)
                      if _same_bytes(path, expected)), None)
        if match is None:
            return False
        remaining.pop(match)
    return not remaining


def _pair_reported_files(expected, actual, positional_fallback=False):
    """先按位元組配對,再按檔名兜底;回傳每份預期檔的配對與多出檔。"""
    remaining = list(actual)
    pairs = [None] * len(expected)
    unmatched = []
    for index, entry in enumerate(expected):
        source = entry[2]
        match = next((i for i, downloaded in enumerate(remaining)
                      if _same_bytes(source, downloaded['path'])), None)
        if match is None:
            unmatched.append((index, entry))
        else:
            pairs[index] = (entry, remaining.pop(match), True)

    for offset, (index, entry) in enumerate(unmatched):
        _item, name, source = entry
        aliases = {
            os.path.normcase(os.path.basename(name)).casefold(),
            os.path.normcase(os.path.basename(source)).casefold(),
        }
        match = next((i for i, downloaded in enumerate(remaining)
                      if os.path.normcase(os.path.basename(downloaded['name'])).casefold()
                      in aliases), None)
        if (match is None and positional_fallback
                and len(remaining) == len(unmatched) - offset):
            match = 0
        pairs[index] = (
            entry, remaining.pop(match) if match is not None else None, False,
        )
    return pairs, remaining




def downloaded_problems(download_reader, profile_url, download_dir, expected, label='平台附件', overwritten=False):
    """程式自己從平台履歷頁下載全部附件,逐位元組跟 expected [(名稱, 本機路徑)] 比:少了、內容不同、多出都點名。
    下載不到就是「未核對」,不說成少了。"""
    problems = []
    try:
        got = download_reader(profile_url, download_dir)
        if got.get('problems'):
            raise LookupError('; '.join(str(x) for x in got['problems']))
        actual = _reported_downloads({'files': got['files']}, 'files', download_dir, problems)
    except (LookupError, OSError, ValueError, KeyError) as e:
        return [f'{label}未核對:{e}']
    have = []
    for name, path in expected:
        if path and os.path.isfile(path):
            have.append((None, name, path))
        else:
            problems.append(f'本機附件「{name}」不見了,無法比對{label}')
    pairs, remaining = _pair_reported_files(have, actual)
    for (_item, name, _path), downloaded, same_bytes in pairs:
        if downloaded is None:
            problems.append(f'{label}「{name}」少了')
        elif not same_bytes:
            problems.append(f'{label}「{name}」內容不同' + ('(固定版被蓋掉)' if overwritten else ''))
    return problems + [f'{label}「{downloaded["name"]}」多出' for downloaded in remaining]


def _check_profile_attachments(job, fb, delivery, download_dir, download_reader):
    """這張卡該附的檔(客製版;original_only 是固定版原本該有的)跟平台履歷上實際的比。"""
    expected = [(str(item.get('name') or os.path.basename(item.get('effective_path') or '') or '未命名附件'),
                 item.get('effective_path')) for item in attachment_sources(job, fb, delivery)]
    fixed = bool(delivery.get('original_only'))
    return downloaded_problems(download_reader, str(delivery.get('profile_url') or '').strip(), download_dir, expected,
                               '固定平台履歷附件' if fixed else '平台附件', overwritten=fixed)


def _reported_local_uploads(report, folder, problems):
    """平台傳完只顯示檔名、沒有下載回來的入口(Greenhouse):改看 agent 放進上傳欄的本機檔。
    只收這張卡投遞夾裡的檔,別處的一律不採信。"""
    root = os.path.realpath(folder) if folder else None
    actual = []
    for item in report.get('uploaded_from') or []:
        name = str((item or {}).get('name') or '未命名檔案')
        path = (item or {}).get('path') if isinstance(item, dict) else None
        candidate = os.path.realpath(path) if isinstance(path, str) and path else ''
        try:
            inside = bool(root and candidate) and os.path.commonpath([root, candidate]) == root
        except ValueError:
            inside = False
        if not inside:
            problems.append(f'申請表上傳檔「{name}」回報的本機檔在這張卡的投遞夾外,不採信')
        elif not os.path.isfile(candidate):
            problems.append(f'申請表上傳檔「{name}」回報的本機檔不存在')
        else:
            actual.append({'name': name, 'path': candidate})
    if not actual and not problems:
        problems.append('平台讀不回上傳檔,agent 也沒回報放進上傳欄的是哪個本機檔')
    return actual


def _check_uploaded_files(job, fb, url, report, download_dir):
    import ship
    problems = []
    if report.get('upload_readback') == 'unavailable' and not report.get('uploaded_files'):
        actual = _reported_local_uploads(report, ship.folder(url), problems)
    else:
        actual = _reported_downloads(report, 'uploaded_files', download_dir, problems)
    if problems:
        return problems
    expected = []
    for item in ship.documents(job, fb):
        path = item.get('effective_path')
        name = str(item.get('name') or os.path.basename(path or '') or '未命名檔案')
        if not path or not os.path.isfile(path):
            problems.append(f'這張卡要送的檔「{name}」本機檔不見了,無法核對申請表上傳檔')
        else:
            expected.append((item, name, path))
    if problems:
        return problems
    if not expected:
        return ['這張卡沒有可核對的投遞檔']

    folder = ship.folder(url)
    info = ship.read_info(folder)
    separate = [
        os.path.join(folder, name)
        for name in info.get('files', [])
        if isinstance(name, str) and os.path.isfile(os.path.join(folder, name))
    ]
    separate = separate or [path for _item, _name, path in expected]
    variants = [separate]
    merged_name = info.get('merged')
    merged = os.path.join(folder, merged_name) if folder and isinstance(merged_name, str) else None
    if merged and os.path.isfile(merged):
        variants.append([merged])
    if any(_same_file_set([item['path'] for item in actual], variant)
           for variant in variants):
        return []

    if len(actual) == 1 and merged and os.path.isfile(merged):
        return [f'申請表上傳檔「{actual[0]["name"]}」內容不同']

    pairs, remaining = _pair_reported_files(
        expected, actual, positional_fallback=True,
    )
    bad = []
    for (_item, name, _source), downloaded, same_bytes in pairs:
        if downloaded is None:
            bad.append(f'申請表少了「{name}」')
        elif not same_bytes:
            bad.append(f'申請表上傳檔「{downloaded["name"]}」內容不同')
    bad.extend(f'申請表上傳檔「{item["name"]}」多出' for item in remaining)
    return bad or ['申請表上傳的檔跟這張卡要送的檔內容不同']


NO_DELIVERY = '交件單上沒寫這次直接上傳還是用哪一份平台履歷'   # 投遞方式沒寫:比附件要看它,安檢門也用這一句


def check_attachments(job, fb, url, report, download_dir, expected_delivery=None, verify_profile=True,
                      download_reader=None, board=None):
    """依投遞方式核對實際交出去的檔。直接上傳:申請表實際收到的檔。固定平台履歷:同步紀錄是最新(附件跟本機一樣,
    由 sync_profiles.sync_slot 負責,填表、送出不碰它)。客製平台履歷:下載它的附件逐位元組比,固定版也要沒被改。
    verify_profile=False:平台履歷上的附件這一輪不核對(填表那一輪)。"""
    if not isinstance(report, dict) or not isinstance(report.get('delivery'), dict):
        return [NO_DELIVERY]
    delivery = report['delivery']
    method = delivery.get('method')
    if method in ('direct_upload', 'no_profile'):
        if expected_delivery and _delivery_key(delivery) != _delivery_key(expected_delivery):
            return ['送出前回報的投遞方式或平台履歷與核准時不同']
        if method == 'no_profile' and not has_custom_documents(job, fb):
            return []
        return _check_uploaded_files(job, fb, url, report, download_dir)
    if method != 'platform_profile':
        return ['agent 回報的投遞方式無法辨認']
    # 用哪一份、固定版還是客製版由程式決定,不看 agent 填的(#313)
    delivery = delivery_for(job, fb, url, delivery)
    if expected_delivery and _delivery_key(delivery) != _delivery_key(delivery_for(job, fb, url, expected_delivery)):
        return ['送出前用的平台履歷與核准時不同']
    if not verify_profile:
        return []
    if delivery['profile_kind'] == 'fixed':
        import sync_profiles
        chosen = decided(job, fb, url)
        slot = (chosen['platform'], chosen['lang'], chosen['variant'])
        entry = where(*slot)
        if not entry:
            return ['程式不知道這張卡該用的固定平台履歷在哪,沒辦法核對']
        return ['這份平台履歷還沒跟本機同步(履歷或附件改過),先同步'] if sync_profiles.behind(*slot, entry, board) else []
    if not delivery.get('profile_url'):
        return ['交件單上沒寫它新開的客製平台履歷網址']
    if not download_reader:
        return ['客製平台履歷的附件這一輪沒有下載核對']
    problems = _check_profile_attachments(job, fb, delivery, download_dir, download_reader)
    fixed = decided(job, fb, url).get('fixed_url')
    if not fixed:
        return problems + ['找不到這張卡對應的固定平台履歷,無法確認它沒有被改']
    return problems + _check_profile_attachments(
        job, fb, {'profile_url': fixed, 'profile_kind': 'fixed', 'original_only': True}, download_dir, download_reader)


def attachment_step(job, fb, url, download_dir, door, delivery_method=None):
    """填表、修改那一輪給 agent 的交檔指示:回報投遞方式;客製版怎麼開;直接上傳怎麼選檔。
    固定平台履歷(文字和附件)由 sync_profiles.sync_slot 在填表前同步好,這一輪只選它、不改它。"""
    custom_resume = decided(job, fb, url)['profile_kind'] == 'custom'
    custom = (custom_resume or has_custom_documents(job, fb)) and delivery_method not in ('direct_upload', 'no_profile')
    request = (
        '\n\n交檔(程式之後逐位元組核對):在交件單寫 delivery.method,照申請頁實際怎麼交履歷:'
        '{"method":"direct_upload"}(申請表上直接上傳檔)、{"method":"no_profile"}(不用平台履歷也不用上傳),'
        '或 {"method":"platform_profile"}(在申請頁選平台上存好的履歷)。選哪一份、固定版還是客製版,程式已經決定好了(第 0 步)。\n'
        '固定平台履歷(文字和附件)程式在填表前已經跟本機同步好:只選它,不改它的文字和附件。'
    )
    if custom:
        rows = '\n'.join(f'  - 名稱={item.get("name")}; 正確檔={item.get("effective_path")}'
                         for item in attachment_sources(job, fb, {'profile_kind': 'custom'}))
        fixed = decided(job, fb, url).get('fixed_url')
        request += (
            '\n這張卡有已收下的客製' + ('履歷' if custom_resume else '附件') + ':申請表能直接上傳時用這張卡的檔;沒有可見的檔案欄時,'
            '在同一個工作區另開申請頁上「管理平台履歷」連結的 href(原申請頁不動),另外建立客製平台履歷,'
            '上傳這張卡的履歷和下面全部附件,再回原申請頁選它;平台上已有的每一份平台履歷都原樣留著,'
            '放不下新的那一份(格子滿了)就停下,在 problems 寫明;新那一份的網址寫在 delivery.profile_url,'
            '程式會下載它的附件逐位元組核對。\n' + rows +
            ('\n固定平台履歷 ' + fixed + ' 是別張卡共用的:只讀不改,程式會下載確認它沒被改。' if fixed else
             '\n程式找不到已登記的固定平台履歷:先停下,在 problems 說明,不要建立或修改固定履歷。')
        )
    request += (
        '\n直接上傳時:只從這張卡可投遞夾 ship.json 的 files 或 merged 選檔,完整路徑交給 setFiles;一次交整套 files 或單一 merged,'
        '不混搭、不漏、不重複。先看檔案欄有幾個、每欄收幾個檔與格式大小限制:個別檔都放得進用途相符的欄位就用 files;'
        '放不下(欄位不夠、或每欄只收一個)就把 merged 放進履歷欄,其他選填檔案欄留空,merged 本來就是為這種表單做的。'
        '選檔前先對照職缺和申請頁對要寄的檔寫的要求(任何一種:頁數、大小、格式、語言、要附或不要附什麼…),'
        '逐份檢查要交的檔;有不符就一個檔都不上傳,把每一條寫進 unmet 後停下:quote 照頁面原文抄那句要求、'
        'file 寫 ship.json 的 files 裡哪一份(個別檔的檔名,不寫 merged)、act 寫 drop(這張不寄這份附件)或 adapt(照這句要求改這份檔)、why 寫哪裡不符;'
        '不寄附件後履歷裡提到那些附件的地方也要寫一條 adapt。都符合就給空清單(每張都要回答,#449)。'
        '求職信檔案欄選填就留空;有求職信文字框才填既有固定文字。'
        '必填檔案欄沒辦法用這張卡的檔合法滿足、或上傳失敗,就停下寫進 problems;不要自己產檔或合併。'
        f'\n傳完把申請表實際收到的每個檔下載到 {download_dir or "(本輪沒有暫存資料夾)"},在 uploaded_files 回報 '
        '[{"name":"申請表上的檔名","path":"暫存資料夾內的完整路徑"}];平台只顯示檔名、沒有下載入口時,uploaded_files 留空、寫 '
        '"upload_readback":"unavailable",並在 uploaded_from 回報 [{"name":"申請表上顯示的檔名","path":"交給 setFiles 的完整路徑"}],'
        '程式會自己核對那個檔,這不算卡住。'
    ) + door.fetch_rule()
    return request


def main():
    ap = argparse.ArgumentParser(description='查看每份平台履歷的同步狀態；執行同步請用 sync_profiles.py')
    ap.add_argument('--board')
    args = ap.parse_args()
    for row in status(args.board):
        print(f"{row['platform']} {row['lang']}/{row['variant']}: {row['state']}")


if __name__ == '__main__':
    main()
