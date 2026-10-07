#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
profile_sync —— 平台上自己存一份履歷的(104、Cake、Yourator、LinkedIn…):讀回來跟母稿比,只列對不上的地方。

讓 agent 從零把平台上那份打一遍,失敗率很高、常常有錯或遺漏。所以代投不叫它從頭打、自己說好了就算:
  1. 填表前,程式在 agent 的 Chrome 打開那一份的全文頁(只讀),抄下文字和連結,跟母稿逐段比。
  2. 只把對不上的那幾段交給 agent 改;都對得上就跳過,不動。
  3. agent 改完,程式再讀一次、再比一次。還對不上的列成問題,核准按不下去。
比對是機械的:母稿的每一段(切成句子,一行好幾格「標籤：值」的再拆成一格一格)要出現在頁面上。
空白、全形半形、冒號直線括號這些排版差異不算;月份補零(2020/09 對 2020/9)不算;連結看頁面上的超連結。
聯絡方式(姓名、Email、電話)是帳號資料,不拿來判。
平台用自己的格式存的格子(下拉選單、自己的用詞),agent 回報「母稿這一格 ＝ 頁面上這幾個字」,程式驗過那幾個字
真的在頁面上才記下,之後照記下的說法比;母稿那一格改了,就又列成差異。

母稿是設定裡該履歷、該語言的 markdown、text 或 PDF(resume.resumes.<id>.files.<lang>)。

每個平台每一份在哪裡看、在哪裡改:<home>/profiles.json。agent 第一次碰到某個平台時把網址寫進輸出的
profile.url,程式記進去,下次起就由程式讀。

用法:
  uv run python tools/profile_sync.py --platform 104 --lang zh --resume <id>       # 讀回來比,印對不上的
  (程式自己開頁讀,只有 Codex 做得到;用 Claude 時直接說做不到,改在看板讓 agent 填表或送出時讀)
"""
import os, sys, re, json, html, argparse, unicodedata, hashlib, subprocess, contextlib
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
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


def registry():
    try:
        with open(REG, encoding='utf-8') as f:
            reg = json.load(f)
        if not isinstance(reg, dict):
            raise ValueError('平台履歷登記資料不是物件')
        return reg
    except FileNotFoundError:
        return {}



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


def _checks(reg):
    """核對紀錄照身分整理:同一份的舊重複紀錄合成一筆,通過的留下(寫回時就只剩一筆)。"""
    merged = {}
    for key, rec in (reg.get('_attachment_checks') or {}).items():
        ident = identity(key) if '://' in str(key) else key     # 舊紀錄的鍵是完整網址,新的已經是身分
        if ident and isinstance(rec, dict) and (ident not in merged or rec.get('matched')
                                                and not merged[ident].get('matched')):
            merged[ident] = rec
    return merged


def attachment_check(profile_url):
    return _checks(registry()).get(identity(profile_url)) or {}


@_registry_writer
def remember_attachment_check(profile_url, fingerprint, profile_kind, matched):
    key = identity(profile_url)
    if not key:
        return
    reg = registry()
    checks = _checks(reg)
    checks[key] = {
        'fingerprint': fingerprint,
        'profile_kind': profile_kind,
        'matched': bool(matched),
    }
    reg['_attachment_checks'] = checks
    _save_registry(reg)


def invalidate_attachment_check(profile_url):
    saved = attachment_check(profile_url)
    if not saved or not saved.get('matched'):
        return
    remember_attachment_check(
        profile_url, saved.get('fingerprint'), saved.get('profile_kind'), False,
    )


@_registry_writer
def remember(platform, lang, variant, read_url, edit_url=None):
    """agent 第一次告訴我們那一份在哪:記下來,下次程式自己讀。"""
    reg = registry()
    entry = dict((reg.get(platform) or {}).get(f'{lang}/{variant}') or {})
    entry.update(read=read_url, edit=edit_url or read_url)
    reg.setdefault(platform, {})[f'{lang}/{variant}'] = entry
    _save_registry(reg)


def where(platform, lang, variant):
    return (registry().get(platform) or {}).get(f'{lang}/{variant}')


@_registry_writer
def accept_extra(platform, lang, variant, label, value):
    """他確認過「母稿沒有、但平台上這一格可以留」:記進那一份的 accepted,下次不再問。"""
    reg = registry()
    entry = (reg.get(platform) or {}).get(f'{lang}/{variant}')
    if not entry:
        raise ValueError(f'{platform} {lang}/{variant} 沒登記')
    pair = f'{str(label).strip()}={str(value).strip()}'
    if pair not in entry.setdefault('accepted', []):
        entry['accepted'].append(pair)
        _save_registry(reg)
    return pair


def _master_fp(lang, variant):
    import source_sync
    paths = [cf.master(part, lang) for part in variant.split('+')]
    return '+'.join(source_sync.fingerprint(p) for p in paths) if all(paths) else ''


@_registry_writer
def _remember_check(platform, lang, variant, matched):
    """讀回來比過一次:記下比的是哪一版母稿、對不對得上。母稿之後再改,status() 自己看得出落後。"""
    reg = registry()
    reg.setdefault('_profile_checks', {})[f'{platform}#{lang}/{variant}'] = {
        'master': _master_fp(lang, variant), 'matched': bool(matched), 'by': 'review'}
    _save_registry(reg)


@_registry_writer
def drop_legacy_checks():
    """舊版逐字比對(check)記下的「對不上」:不是 agent 判讀的結果,清掉;下次填這個平台的卡時重新判讀。回清掉幾筆。"""
    reg = registry()
    checks = reg.get('_profile_checks') or {}
    old = [k for k, v in checks.items() if not (isinstance(v, dict) and v.get('by') == 'review')]
    for k in old:
        del checks[k]
    if old:
        _save_registry(reg)
    return len(old)


def status():
    """登記過的每一份平台履歷現在跟母稿的關係:ok / master-changed(母稿改了、還沒讀回比) / differs(上次比有差) / unchecked。
    不靠誰記得:母稿一改,狀態當下就變,不用有人去登記。"""
    reg = registry()
    saved = reg.get('_profile_checks') or {}
    out = []
    for platform, slots in reg.items():
        if platform.startswith('_') or not isinstance(slots, dict):
            continue
        for slot, entry in slots.items():
            lang, _, variant = slot.partition('/')
            fp = _master_fp(lang, variant) if variant and isinstance(entry, dict) else ''
            if not fp:                                    # 沒設定母稿:沒有東西可比
                continue
            rec = saved.get(f'{platform}#{slot}') or {}
            state = ('unchecked' if not rec else 'master-changed' if rec.get('master') != fp
                     else 'ok' if rec.get('matched') else 'differs')
            out.append({'platform': platform, 'lang': lang, 'variant': variant, 'state': state,
                        'url': entry.get('read') or ''})
    return out


@_registry_writer
def remember_name(platform, lang, variant, name):
    """平台上那一份叫什麼(申請頁選平台履歷時顯示的字):程式在那一份的頁面上看到過才記。"""
    reg = registry()
    slot = (reg.get(platform) or {}).get(f'{lang}/{variant}')
    if isinstance(slot, dict) and slot.get('name') != name:
        slot['name'] = name
        _save_registry(reg)


def picked(page, names):
    """申請頁上選好的是哪幾份(names:{哪一份: 平台上的名稱})。先看下拉選單選好的字;
    104 這種選單不是表單欄位,選好的值只是一行字:一行裡只出現一份的名稱才算選了它(列出全部選項的那行不算)。"""
    fields = page.get('fields') or []
    shown = {_n(f.get('shown') or f.get('value')) for f in fields
             if not isinstance(f.get('value'), list)}
    hit = {slot for slot, n in names.items() if _n(n) in shown}
    profile_choice = any(
        (str(f.get('type') or '').startswith('select') or f.get('role') == 'combobox')
        and re.search(r'resume|履歷|\bcv\b|\bprofile\b|profile_id',
                      str(f.get('label') or '') + ' ' + str(f.get('name') or ''), re.I)
        for f in fields
    )
    # 原生選單已讀到選取值時,body 內未選的 option 文字不能推翻它。
    if hit or profile_choice:
        return hit
    for line in page.get('lines') or []:
        inside = {slot for slot, n in names.items() if _n(n) and _n(n) in _n(line)}
        if len(inside) == 1:
            hit |= inside
    return hit


def linked(page, platform):
    """申請頁上連到這個平台某一份平台履歷的連結(apply_tab.PAGE_FN 的 profileLinks,例如 104 應徵彈窗的「預覽履歷」)
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


def fixed_profile_delivery(job, fb, url):
    """回傳這張卡所選語言/履歷的固定平台履歷投遞方式。"""
    import ship
    platform = profile_key(url)
    resume_id, lang = ship.resolve(job, fb)
    profile = where(platform, lang, resume_id) if platform and resume_id else None
    if not profile or not profile.get('read'):
        return None
    return {
        'method': 'platform_profile',
        'profile_url': profile['read'],
        'profile_kind': 'fixed',
    }


def _n(s):
    s = unicodedata.normalize('NFKC', html.unescape(str(s or '')))
    s = re.sub(r'(\d{4})/0(\d)\b', r'\1/\2', s)
    return re.sub(r'[\s:：|｜()\uFF08\uFF09\[\]【】「」"\'、,，.。;；!！?？*`#>_\-–—~]+', '', s).lower()


def _pieces(text):
    """一段切成句子(太短的片段不拿來比,會到處都對得上)。"""
    return [p for p in re.split(r'[。;；!！?？\n]|(?<=[a-z])\.\s', str(text or '')) if len(_n(p)) >= 6]


_LINK = re.compile(r'\[([^\]]*)\]\(([^)\s]+)\)')


def md_sections(text):
    """markdown 母稿 → [(哪一段, 純文字, 連結)]。一個條列項或一個段落算一段,標題跟著當位置。"""
    text = re.sub(r'<style\b.*?</style>|<!--.*?-->|<img\b[^>]*>', '', text, flags=re.S | re.I)
    out, head, buf = [], '', []
    def flush():
        t = ' '.join(buf).strip()
        if t:
            links = [u for _, u in _LINK.findall(t)]
            out.append((f'{head or "開頭"}[{len(out)}]', _LINK.sub(r'\1', t).replace('**', ''), links))
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


def approved_facts(platform):
    """人類核准的補充來源；跨履歷提供上下文，不直接放行。"""
    facts = (registry().get(platform) or {}).get('approved_facts', [])
    if not isinstance(facts, list) or any(not _valid_fact(f, platform) for f in facts):
        raise ValueError('平台補充來源格式不正確')
    return [f for f in facts if f.get('scope') == os.path.realpath(cf.HOME)]


def _valid_fact(fact, platform):
    """驗核准紀錄的必要結構與身分，不解釋聲明意思。"""
    if not isinstance(fact, dict) or not isinstance(fact.get('statement'), str) or not fact['statement'].strip():
        return False
    context = fact.get('context')
    if not isinstance(fact.get('scope'), str) or not isinstance(context, dict):
        return False
    page, resume = context.get('page'), context.get('resume')
    if (not isinstance(page, dict) or not isinstance(page.get('url'), str)
            or not isinstance(page.get('text'), str) or not page['text'].strip()
            or not isinstance(resume, dict) or resume.get('platform') != platform
            or not all(isinstance(resume.get(k), str) and resume[k] for k in ('lang', 'variant'))
            or context.get('profile_identity') != identity(page['url'])
            or not same_platform_url(page['url'], platform)
            or not isinstance(context.get('source_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', context['source_sha256'])):
        return False
    try:
        item = review_items('', page).get(context.get('item_id'))
    except (ValueError, TypeError, KeyError):
        return False
    return bool(item and item['kind'] in ('text', 'field') and item == context.get('item'))


def review_items(source, page, approved=()):
    """本次項目編號：只分資料出處，不分類語意，也不略過空值或 0。"""
    if not isinstance(source, str) or not isinstance(page, dict) or not isinstance(page.get('text'), str) or not page['text'].strip():
        raise ValueError('讀回結果沒有完整文字')
    if not isinstance(page.get('fields'), list) or any(not isinstance(f, dict) for f in page['fields']):
        raise ValueError('讀回結果沒有完整的欄位清單，不能確認結構化欄位')
    # 讀完沒有由這裡另訂:讀頁程式(chrome_door.read_pages)不在「載入中」、內容連續幾次一樣才交出來,沒讀完標 _ready=False
    if page.get('readyState') == 'loading' or page.get('_ready') is False:
        raise ValueError('平台履歷尚未讀取完成')
    items = {}
    for kind, values in (('source', source.split('\n\n')), ('text', page['text'].splitlines()),
                         ('field', page['fields']), ('approved', approved)):
        for i, value in enumerate(values):
            if isinstance(value, str) and not value.strip():
                continue
            items[f'{kind}:{i}'] = {'kind': kind, 'value': value}
    return items


@_registry_writer
def approve_fact(platform, material, page_url, item_id, statement):
    """人工 CLI：核准完整聲明並保留當時上下文。Agent 不可自行呼叫。"""
    binding = material.get('binding') or {}
    scope = os.path.realpath(cf.HOME)
    if binding.get('platform') != platform or material.get('scope') != scope:
        raise ValueError('核准材料不屬於這個平台或使用者資料範圍')
    if not isinstance(statement, str) or not statement.strip():
        raise ValueError('要提供人類明確核准的完整聲明')
    target = next((p for p in material.get('material', []) if p.get('url') == page_url), None)
    if target is None:
        raise ValueError('核准材料沒有指定頁面')
    items = review_items(target['source'], target['page'])
    item = items.get(item_id)
    if not item or item['kind'] not in ('text', 'field'):
        raise ValueError('要指定這次實際讀回的文字或欄位項目')
    fact = {'statement': statement.strip(), 'scope': scope, 'context': {
        'item_id': item_id, 'item': item, 'page': target['page'], 'profile_identity': identity(page_url),
        'resume': binding, 'source_sha256': material['source_hashes'][target['source_path']],
    }}
    if not _valid_fact(fact, platform):
        raise ValueError('核准材料缺少完整上下文或履歷身分')
    reg = registry()
    facts = reg.setdefault(platform, {}).setdefault('approved_facts', [])
    if fact not in facts:
        facts.append(fact)
        _save_registry(reg)
    return fact


@_registry_writer
def remember_review(platform, lang, variant, read_url, fingerprint, sheet, source=None, items=None, rules=None):
    """只記指定履歷最近一次已核對的判讀；核准來源另外保存。
    source:這次判讀通過時的原稿全文,下次填表只把原稿跟它不一樣的地方交給 agent(synced_source)。
    items、rules:這次逐項判讀的項目和規則;下次內容沒變的項目沿用(apply_run._carry_over)。"""
    reg = registry()
    slot = (reg.get(platform) or {}).get(f'{lang}/{variant}') or {}
    if identity(slot.get('read')) != identity(read_url):
        return
    slot['content_review'] = {'fingerprint': fingerprint, 'sheet': sheet}
    if isinstance(source, str):
        slot['content_review']['source'] = source
    if isinstance(items, dict):
        slot['content_review'].update(items=items, rules=rules)
    _save_registry(reg)


def synced_source(platform, lang, variant):
    """平台上這一份最近一次判讀通過時對的是哪一版原稿(全文);沒有回 None。"""
    review = (where(platform, lang, variant) or {}).get('content_review')
    source = review.get('source') if isinstance(review, dict) else None
    return source if isinstance(source, str) else None


def expected(platform, lang, variant):
    """舊 CLI 診斷用的片語；投遞內容判讀使用 source_text 全文。
    variant 可以用 + 串幾份(例如 'a+b'):這一份平台履歷是綜合版,頁面要有每一份母稿的全部內容,
    同一句在幾份母稿裡都有只算一次。"""
    if '+' in variant:
        out, seen = [], set()
        for part in variant.split('+'):
            for where, text, links in expected(platform, lang, part):
                if _n(text) not in seen:
                    seen.add(_n(text))
                    out.append((f'{part}:{where}', text, links))
        return out
    path = cf.master(variant, lang)
    if not path or not os.path.isfile(path):
        return []
    return md_sections(capture_source(path)['text'])


def _flat(x, path):
    if isinstance(x, dict):
        if 'text' in x and isinstance(x.get('text'), str):
            yield (path, x['text'], list(x.get('links') or []))
            return
        for k, c in x.items():
            yield from _flat(c, f'{path}.{k}')
    elif isinstance(x, list):
        for i, c in enumerate(x):
            yield from _flat(c, f'{path}[{i}]')
    elif isinstance(x, str) and x.strip():
        yield (path, x, [])


# 聯絡方式是帳號資料,平台放在帳號設定、顯示方式各有各的,不拿來比(姓名、Email、電話)
_CONTACT_LABELS = {'姓名', '名字', 'name', 'fullname', 'email', 'e-mail', '電子郵件', '信箱',
                   '手機', '電話', '行動電話', '聯絡電話', 'phone', 'mobile', 'tel'}
_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_PHONE = re.compile(r'\+?\d[\d\s()-]{7,}\d')
_LABELED = re.compile(r'^\s*([^：:]{1,12})[：:]\s*(.+)$', re.S)


def _segments(piece):
    """一句再照「｜」拆開,每一格回 (整格, 冒號後的值, 是不是聯絡方式)。
    母稿常把幾個「標籤：值」擠在同一行(姓名｜Email｜兵役｜居住地),平台卻一格一格放、標籤寫法也不同;
    整行當一句比,每一格都在頁面上也永遠對不上。"""
    out = []
    for seg in re.split(r'[｜|]', piece):
        seg = seg.strip()
        if not _n(seg):
            continue
        m = _LABELED.match(seg)
        label, value = (m.group(1), m.group(2)) if m else ('', seg)
        contact = (_n(label) in {_n(x) for x in _CONTACT_LABELS}
                   or bool(_EMAIL.search(value)) or bool(_PHONE.fullmatch(value.strip())))
        out.append((seg, value, contact))
    return out


_BRACKET_N = re.compile(r'\[\d+\]$')
_NO_VALUE = {'', '-', '--', 'n/a', 'none', 'true', 'false', 'on', 'off'}     # 沒填、或只是開關(radio/checkbox 的 value),不是聲明
_PLACEHOLDER = ('請選擇', 'select', 'please select', 'choose')            # 下拉選單還沒選的占位字
_SKIP_TYPES = {'hidden', 'file', 'password', 'button', 'submit'}


def _zero(v):
    try:
        return float(v) == 0
    except ValueError:
        return False


def extras(page, want, accepted=None):
    """反向檢查:頁面上有值、母稿卻沒有的欄位(身高、體重、婚姻…)。回 ['標籤=值'] 。
    diff 只查「母稿有、頁面沒有」;頁面上多出他沒寫的東西不會被看到,存檔就等於替他背書。
    母稿裡找得到那個值就算有來源;聯絡方式是帳號資料不比;他確認過可以留的記在 accepted(['標籤=值'])。"""
    # 母稿的來源:每段文字、它的連結、段落標題(使用者貼進長文字欄時常加標題、把連結寫成網址)
    master = _n(' '.join(_BRACKET_N.sub('', str(where)) + ' ' + text + ' ' + ' '.join(links)
                         for where, text, links in want))
    okay = {_n(x) for x in accepted or []}
    out = []
    for f in page.get('fields') or []:
        if not isinstance(f, dict) or f.get('type') in _SKIP_TYPES:
            continue
        value = str(f.get('shown') or f.get('value') or '').strip()     # 下拉選單、自製選單看得到的字優先
        label = str(f.get('label') or f.get('name') or '').strip()
        if value.casefold() in _NO_VALUE or value.casefold().startswith(_PLACEHOLDER) or _n(value) == '' or (f.get('type') == 'number' and _zero(value)):
            continue
        contact = (_n(label) in {_n(x) for x in _CONTACT_LABELS} or bool(_EMAIL.search(value))
                   or bool(_PHONE.fullmatch(value)))
        pair = f'{label}={value}'
        if contact or _n(pair) in okay or _n(value) in master:
            continue
        if len(_n(value)) > 40:        # 長文字欄(自傳):整段不會原樣出現在母稿,一句一句找,每句都有來源才算
            miss = [p for p in _pieces(value) if _n(p) not in master]
            if not miss:
                continue
            pair = f'{label}={miss[0][:60]}…(這欄有 {len(miss)} 句母稿沒有)'
        out.append(pair)
    return out


def account(page, want):
    """帳號資料(姓名、Email、電話、居住地)只做寬鬆比對,回提醒清單;不擋核准。
    這幾格是帳號層級:平台只存一份、各家顯示方式不同(信箱截斷、電話分區碼、地址拆縣市區),agent 也改不了驗證過的手機和信箱。
    所以不逐字比,各抓一個關鍵片段確認在頁面上:信箱開頭、電話後六碼、姓名、縣市加區。
    不假設母稿怎麼排:信箱、電話在整份母稿裡找(任何語言都一樣),姓名取第一個標題,居住地才靠標籤(中英文都認)。"""
    text = '\n'.join(t for _, t, _ in want)
    got = _n(page.get('text')) + ' ' + _n(' '.join(str(f.get('shown') or f.get('value') or '')
                                                   for f in page.get('fields') or [] if isinstance(f, dict)))
    digits = re.sub(r'\D', '', got)
    out = []
    m = _EMAIL.search(text)
    if m and _n(m.group(0).split('@')[0][:8]) not in got:
        out.append(f'Email:母稿是「{m.group(0)}」,頁面上沒看到開頭')
    m = _PHONE.search(text)
    if m and re.sub(r'\D', '', m.group(0))[-6:] not in digits:
        out.append(f'電話:母稿是「{m.group(0).strip()}」,頁面上沒看到後六碼')
    m = re.search(r'^\s*#\s+(\S.*)$', text, re.M) or re.search(r'(?:姓名|名字|name)\s*[：:]\s*([^｜|\n]+)', text, re.I)
    if m and len(_n(m.group(1))) >= 2 and _n(m.group(1)) not in got:
        out.append(f'姓名:母稿是「{m.group(1).strip()}」,頁面上沒看到')
    m = re.search(r'(?:居住地|地址|location|address|city)\s*[：:]\s*([^｜|\n]+)', text, re.I)
    if m:
        place = re.match(r'\s*(\S{2,3}[縣市])\s*(\S{1,3}[區鄉鎮市])?', m.group(1))
        parts = [x for x in place.groups() if x] if place else [x.strip() for x in m.group(1).split(',')[:1]]
        if any(_n(x) not in got for x in parts if _n(x)):
            out.append(f'居住地:母稿是「{m.group(1).strip()}」,頁面上沒看到')
    return out


def _same_meaning(equivalents, seg, got):
    """這一格有「平台用自己的說法寫」的紀錄,而且那段字現在還在頁面上(或記的是平台根本不顯示這一格)。"""
    e = (equivalents or {}).get(_n(seg))
    if not isinstance(e, dict):
        return False
    shown = _n(e.get('platform'))
    return bool(e.get('not_shown')) or (len(shown) >= 2 and shown in got)


def diff(page, want, equivalents=None):
    """頁面(read_pages 的一筆)跟母稿比。回 [{'where', 'want', 'missing': [沒出現的句子], 'links': [沒出現的連結]}]。
    一句整句出現在頁面上就算對;沒有的話拆成「｜」分開的格子,每一格的值出現在頁面上(或是連結)也算對;
    平台用自己說法寫的格子(equivalents:agent 回報、程式驗過)照記下的說法比。"""
    got = _n(page.get('text'))
    hrefs = {str(h).rstrip('/') for h in page.get('links') or []}
    got_links = _n(' '.join(hrefs))
    out = []
    for where_, text, links in want:
        miss = []
        for p in _pieces(text) or [text]:
            if not _n(p) or _n(p) in got:
                continue
            for seg, value, contact in _segments(p):
                v = _n(value)
                if (contact or len(v) < 2 or _n(seg) in got or v in got or v in got_links
                        or _same_meaning(equivalents, seg, got)):
                    continue
                miss.append(seg)
        lmiss = [l for l in links if l.rstrip('/') not in hrefs and _n(l) not in got]
        if miss or lmiss:
            out.append({'where': where_, 'want': text, 'missing': miss, 'links': lmiss})
    return out


def read(platform, lang, variant, board=None, reader=None, test_allow_local=False, name=None, read_url=None):
    """讀回指定平台履歷，驗來源與可讀性，不判斷內容意思。loopback 只供假頁測試。
    name:agent 回報這一份在平台上的名稱;在這一頁上真的看得到才記下(之後程式讀申請頁核對選的是哪一份)。"""
    specific_page = bool(read_url)
    w = {'read': read_url, 'edit': read_url} if read_url else where(platform, lang, variant)
    if not w:
        return None, [], ''
    read_url = w['read']
    if not same_platform_url(read_url, platform, allow_local=test_allow_local):
        return w, [], '讀取網址不安全'
    if is_file_url(read_url):       # 一份檔:內容由程式下載逐位元組比(apply_run._file_profile_problems),這裡不讀文字
        return w, {'url': read_url, 'file': True, 'text': ''}, ''
    if reader is None:                                    # 沒給就用現在用 Chrome 的那一家自己開頁讀
        import chrome_door
        door = chrome_door.current()
        if door is None:
            return w, [], chrome_door.NO_BROWSER_AGENT
        reader = door.profile_reader(board=board)
    try:
        page = reader(read_url)
    except Exception as e:  # noqa: BLE001 — 各家門路丟的例外不一樣;原因照實回報成這一份的問題
        return w, [], f'讀不到平台上那一份({str(e)[:80]})'
    final_url = page.get('url') or ''
    if not final_url:
        return w, [], '讀回結果沒有實際頁面網址，無法確認是哪份履歷'
    if not same_platform_url(final_url, platform, allow_local=test_allow_local):
        return w, [], '平台履歷讀取後跳到不屬於原平台的網址'
    if identity(final_url) != identity(read_url):
        return w, [], '讀回的是同平台的另一份履歷，無法確認指定版本'
    if 'login' in str(final_url).lower() or not page.get('text'):
        return w, [], f'讀不到平台上那一份(可能要登入):{page.get("url") or read_url}'
    if not specific_page and name and len(_n(name)) >= 2 and _n(name) in _n(page.get('text')):
        remember_name(platform, lang, variant, str(name).strip()[:80])
    return w, page, ''


def check(platform, lang, variant, board=None, reader=None, test_allow_local=False, reported=None, name=None):
    """舊片語比較僅供 CLI 診斷；投遞驗收使用 read＋Agent 判讀，不以這裡放行。"""
    w, page, problem = read(platform, lang, variant, board, reader, test_allow_local, name)
    if not w or problem:
        return w, [], problem
    try:
        want = expected(platform, lang, variant)
    except ValueError as e:
        return w, [], str(e)
    rejected = accept_equivalents(platform, lang, variant, page, want, reported) if reported else {}
    ds = diff(page, want, (where(platform, lang, variant) or {}).get('equivalents'))
    more = extras(page, want, (w or {}).get('accepted'))
    if more:                                              # 反向:頁面上有、母稿沒有的欄位,他回答之前不放行
        ds.append({'where': '頁面上多出母稿沒有的欄位', 'want': '', 'missing': [], 'links': [], 'unexpected': more})
    for m in account(page, want):                         # 帳號資料:寬鬆比、只提醒(warn),不擋核准
        ds.append({'where': '帳號資料(只提醒)', 'want': m, 'missing': [], 'links': [], 'warn': True})
    for d in ds:                                          # 沒收下的原因掛在那一格上,讓下一輪知道怎麼改
        why = [f'「{m[:40]}」{rejected[_n(m)]}' for m in d['missing'] if _n(m) in rejected]
        if why:
            d['rejected'] = why
    return w, ds, ''


def describe(ds, limit=12):
    """給 agent 看、也給看板看的一段話:哪一段、母稿怎麼寫、頁面上缺哪幾句。"""
    L = []
    for d in ds[:limit]:
        # 找不到的格子一格一個「」:agent 回報說法對照時照抄這一格(不是整行)
        L.append(f"- {d['where']}:母稿是「{d['want'][:300]}」"
                 + (f";頁面上找不到的格子:{'、'.join('「' + m[:120] + '」' for m in d['missing'][:8])}"
                    if d['missing'] else '')
                 + (f";上一輪回報的說法沒收下:{'; '.join(d['rejected'][:4])}" if d.get('rejected') else '')
                 + (f";欄位多出其他格內容:{' / '.join(x[:80] for x in d['unexpected'][:4])}"
                    if d.get('unexpected') else '')
                 + (f";缺連結:{', '.join(d['links'])}" if d['links'] else ''))
    if len(ds) > limit:
        L.append(f'…另外還有 {len(ds) - limit} 段')
    return '\n'.join(L)


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


# 平台用自己的格式存的格子(下拉選單、日期寫法、自己的用詞):母稿「錄取後一個月內可上班」、
# 104 顯示「錄取後一個月可上班」。字面永遠比不過,但意思一樣。agent 回報「母稿這一格 ＝ 頁面上這幾個字」,
# 程式確認那幾個字真的在頁面上才記下(存在 profiles.json,照母稿那一格的原文當 key)。
# 以前的做法是欄位對照:列出平台編輯頁每一格對到母稿哪一段,綁整份母稿雜湊。104 的內容在一格一格的編輯視窗裡,
# agent 要打開每一個視窗(一次 13 分鐘),改母稿一個字就整份重做,還會因為一格帳號欄位名稱沒見過就整份作廢。
NOT_SHOWN_MAX = 24          # 「平台不顯示」只收短格子(兵役：免役、應屆畢業這種);一般句子、整段內容不能這樣帶過


@_registry_writer
def accept_equivalents(platform, lang, variant, page, want, reported):
    """驗 agent 回報的說法對照,收下合格的。回 {那一格(正規化): 沒收下的原因}。
    只收「字面比不過的那一格」;platform 那幾個字要在頁面上;not_shown 只收短的「標籤：值」格子。
    agent 常把整行貼成 master:整行裡只有一格比不過,就當作那一格;有好幾格就請它一格一筆。
    回報了本來就對得上的格子(例如這一輪剛改好的)不算錯,直接略過。"""
    got = _n(page.get('text')) + _n(' '.join(str(h) for h in page.get('links') or []))
    entry = where(platform, lang, variant) or {}
    missing = {_n(m): m for d in diff(page, want, entry.get('equivalents')) for m in d['missing']}
    current = {_n(seg) for _w, text, _l in want for p in (_pieces(text) or [text]) for seg, _v, _c in _segments(p)}
    kept = {k: v for k, v in (entry.get('equivalents') or {}).items() if k in current}   # 母稿已經沒有的格子丟掉
    # 「平台不顯示」只收一行裡用「｜」分開的格子或「標籤：值」:這些是條件、狀態這類短資料,不是一般句子
    cells = {_n(seg) for _w, text, _l in want for p in (_pieces(text) or [text])
             for seg, _v, _c in _segments(p) if len(_segments(p)) > 1 or _LABELED.match(seg)}
    rejected = {}
    items = [x for x in (reported if isinstance(reported, list) else []) if isinstance(x, dict)]
    items.sort(key=lambda x: _n(x.get('master')) not in missing)     # 一格一筆的先收,整行的再對剩下的格子
    for item in items:
        key = _n(item.get('master'))
        if key not in missing:
            inside = [k for k in missing if k and k in key and k not in kept]
            if len(inside) > 1:
                for k in inside:
                    rejected[k] = '回報時整行貼在一起了,要一格一筆(master 只放這一格)'
                continue
            if not inside:
                continue                                   # 本來就對得上:不用記
            key = inside[0]
        master = missing[key]
        why = str(item.get('why') or '').strip()[:200]
        if item.get('not_shown'):
            m = _LABELED.match(master)
            if key not in cells or len(_n(m.group(2) if m else master)) > NOT_SHOWN_MAX:
                rejected[key] = '太長,不能說平台不顯示;請把內容放上平台'
                continue
            kept[key] = {'master': master, 'not_shown': True, 'why': why}
            continue
        shown = str(item.get('platform') or '').strip()
        if len(_n(shown)) < 2 or _n(shown) not in got:
            rejected[key] = f'說平台寫成「{shown[:40]}」,但頁面上找不到這幾個字'
            continue
        kept[key] = {'master': master, 'platform': shown, 'why': why}
        rejected.pop(key, None)
    reg = registry()
    slot = reg.setdefault(platform, {}).setdefault(f'{lang}/{variant}', {})
    slot['equivalents'] = kept
    slot.pop('field_mapping', None)
    _save_registry(reg)
    return rejected


@_registry_writer
def remember_application_history(platform, url):
    """平台的應徵紀錄頁(查回音用)。網址要屬於這個平台才記。"""
    if not url or not same_platform_url(url, platform):
        return False
    reg = registry()
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
            dict(item, effective_path=item.get('source') or item['effective_path'])
            for item in sources
        ]
    elif profile_kind == 'custom':
        sources.extend(
            item for item in _custom_documents(job, fb)
            if item.get('kind') == 'resume' and item.get('effective_path')
        )
    return sources


def attachment_fingerprint(job, fb, delivery=None):
    """清單選擇與這份平台履歷要用的檔案內容指紋,用來決定是否需要重新下載。"""
    import ship
    resume_id, lang = ship.resolve(job, fb)
    choices = []
    for item in cf.ATTACHMENTS:
        files = {}
        for file_lang, relative in sorted((item.get('files') or {}).items()):
            path = cf.path(relative) if relative else None
            files[file_lang] = {'path': relative, 'sha256': _sha_file(path)}
        choices.append({
            'id': str(item.get('id') or ''),
            'enabled': item.get('enabled', True),
            'resume_ids': list(item.get('resume_ids') or []),
            'files': files,
        })
    effective = [{
        'id': item.get('id'), 'name': item.get('name'),
        'path': os.path.relpath(item['effective_path'], cf.HOME)
                if item.get('effective_path') else None,
        'sha256': _sha_file(item.get('effective_path')),
    } for item in attachment_sources(job, fb, delivery)]
    content = json.dumps({
        'resume_id': resume_id, 'lang': lang, 'choices': choices, 'effective': effective,
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(content).hexdigest()


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
    profile = field in ('profile_attachments', 'fixed_profile_attachments')
    label = ('固定平台履歷附件' if field == 'fixed_profile_attachments'
             else '平台附件' if profile else '申請表上傳檔')
    missing = ('本輪需要重新下載平台履歷上的全部附件,agent 沒有回報下載檔'
               if field == 'profile_attachments'
               else '需要重新下載固定平台履歷上的全部附件,agent 沒有回報下載檔'
               if field == 'fixed_profile_attachments'
               else '申請表上傳檔沒有回報下載檔,不能驗內容')
    invalid = (f'{label}有無效的附件下載回報'
               if profile else '申請表有無效的上傳檔下載回報')
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




def _check_profile_attachments(job, fb, report, download_dir, delivery, field,
                               force=False, download_reader=None):
    profile_url = str(delivery.get('profile_url') or '').strip()
    profile_kind = delivery.get('profile_kind')
    fingerprint = attachment_fingerprint(job, fb, delivery)
    saved = attachment_check(profile_url)
    if (not force and saved.get('matched')
            and saved.get('fingerprint') == fingerprint
            and saved.get('profile_kind') == profile_kind):
        return []

    problems = []
    source_report = report
    if field == 'fixed_profile_attachments' and not download_reader:
        fixed = report.get('fixed_profile')
        if not isinstance(fixed, dict):
            problems.append('agent 沒回報固定平台履歷的網址和附件')
            remember_attachment_check(profile_url, fingerprint, profile_kind, False)
            return problems
        if identity(fixed.get('url')) != identity(profile_url):
            problems.append('固定平台履歷回報網址與已登記網址不同,附件下載不採信')
            remember_attachment_check(profile_url, fingerprint, profile_kind, False)
            return problems
        source_report = {'fixed_profile_attachments': fixed.get('attachments')}

    label = '固定平台履歷附件' if field == 'fixed_profile_attachments' else '平台附件'
    if download_reader:
        try:
            downloaded = download_reader(profile_url, download_dir)
            failed = downloaded.get('problems') or []
            if failed:
                raise LookupError('; '.join(str(x) for x in failed))
            actual = _reported_downloads({field: downloaded['files']}, field, download_dir, problems)
        except (LookupError, OSError, ValueError, KeyError) as e:
            remember_attachment_check(profile_url, fingerprint, profile_kind, False)
            return [f'{label}未核對:{e}']
    else:
        actual = _reported_downloads(source_report, field, download_dir, problems)
    expected = []
    for item in attachment_sources(job, fb, delivery):
        source = item.get('effective_path')
        name = str(item.get('name') or os.path.basename(source or '') or '未命名附件')
        if not source or not os.path.isfile(source):
            problems.append(f'本機附件「{name}」不見了,無法比對平台附件')
        else:
            expected.append((item, name, source))

    agent_problems = [str(x).strip() for x in (report.get('problems') or []) if str(x).strip()]
    if expected and not actual and agent_problems:
        # 一個都沒拿到、agent 自己講了原因(下載逾時、被拒):平台上不一定少,是沒核對到。照它的原因寫,不寫「少了」
        problems.append(f'{label}沒下載到,內容沒核對:{agent_problems[0][:150]}')
        remember_attachment_check(profile_url, fingerprint, profile_kind, False)
        return problems
    pairs, remaining = _pair_reported_files(expected, actual)
    for (item, name, source), downloaded, same_bytes in pairs:
        if downloaded is None:
            problems.append(f'{label}「{name}」少了')
        elif not same_bytes:
            custom = os.path.realpath(item.get('source') or '') != os.path.realpath(source)
            overwritten = profile_kind == 'fixed' and not custom
            detail = '內容不同（固定版被蓋掉）' if overwritten else '內容不同'
            problems.append(f'{label}「{name}」{detail}')
    for downloaded in remaining:
        problems.append(f'{label}「{downloaded["name"]}」多出')

    remember_attachment_check(profile_url, fingerprint, profile_kind, not problems)
    return problems


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


def check_attachments(job, fb, url, report, download_dir, force=False,
                      expected_delivery=None, verify_profile=True, download_reader=None):
    """依 agent 回報的投遞方式檢查平台附件或申請表實際上傳檔。
    verify_profile=False:平台履歷上的附件這一輪沒下載,不核對(填完後、送出前另外核對)。"""
    if not isinstance(report, dict):
        return [NO_DELIVERY]
    delivery = report.get('delivery')
    if not isinstance(delivery, dict):
        return [NO_DELIVERY]
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
    report = dict(report, delivery=delivery)
    profile_url = str(delivery.get('profile_url') or '').strip()
    profile_kind = delivery['profile_kind']
    if expected_delivery:
        expected_delivery = delivery_for(job, fb, url, expected_delivery)
        if _delivery_key(delivery) != _delivery_key(expected_delivery):
            return ['送出前用的平台履歷與核准時不同']
    if not verify_profile:
        return []
    if not profile_url:
        return (['交件單上沒寫它新開的客製平台履歷網址'] if profile_kind == 'custom'
                else ['程式不知道這張卡該用的固定平台履歷在哪,沒辦法核對附件'])

    problems = _check_profile_attachments(
        job, fb, report, download_dir, delivery, 'profile_attachments',
        force=force, download_reader=download_reader,
    )
    if profile_kind == 'custom':
        fixed = fixed_profile_delivery(job, fb, url)
        if fixed:
            fixed['original_only'] = True
        if not fixed:
            problems.append('找不到這張卡對應的固定平台履歷,無法重新下載附件確認它沒有被改')
        else:
            problems.extend(_check_profile_attachments(
                job, fb, report, download_dir, fixed,
                'fixed_profile_attachments', force=True, download_reader=download_reader,
            ))
    return problems


def profile_attachments_fresh(job, fb, url):
    """這張卡用的平台履歷,是不是已經用「現在的履歷和附件」核對過而且對得上。
    指紋是履歷、語言、附件設定和檔案內容,不是每張卡各一份:更新之後核對過一次,後面的卡都沿用。
    客製平台履歷每張卡不同,一律回 False(送出前照舊核對)。"""
    apply = (fb.get(url) or {}).get('apply') or {}
    delivery = delivery_for(job, fb, url, apply.get('delivery'))
    if delivery.get('method') != 'platform_profile' or delivery.get('profile_kind') != 'fixed':
        return False
    saved = attachment_check(delivery.get('profile_url'))
    return bool(saved.get('matched')
                and saved.get('fingerprint') == attachment_fingerprint(job, fb, delivery)
                and saved.get('profile_kind') == 'fixed')







def attachment_step(job, fb, url, download_dir, door, force=False, verify_profile=True):
    """告訴 agent 回報投遞方式,並核對實際使用的附件。
    verify_profile=False(填表、修改):平台履歷附件這一輪不下載核對,程式填完後只在需要時另外核對。
    door:這一輪用 agent 的 Chrome 的那一家(chrome_door);取檔、選檔的做法照它給(Codex 外掛的做法 Claude 做不到)。"""
    apply = (fb.get(url) or {}).get('apply') or {}
    # 用哪一份、固定版還是客製版照程式決定的;上一輪交件單只拿投遞方式和 agent 新開的客製版網址(#313)
    delivery = delivery_for(job, fb, url, apply.get('delivery'))
    custom_resume = decided(job, fb, url)['profile_kind'] == 'custom'
    custom_docs = has_custom_documents(job, fb)
    if custom_resume:
        profile_delivery = delivery if delivery.get('method') == 'platform_profile' else {'profile_kind': 'custom'}
    else:
        if delivery.get('method') != 'platform_profile':
            delivery = fixed_profile_delivery(job, fb, url) or delivery
        profile_delivery = delivery

    fingerprint = attachment_fingerprint(job, fb, profile_delivery)
    profile_expected = attachment_sources(job, fb, profile_delivery)
    profile_url = delivery.get('profile_url')
    saved = attachment_check(profile_url)
    skip = (
        not force
        and delivery.get('method') == 'platform_profile'
        and saved.get('matched')
        and saved.get('fingerprint') == fingerprint
        and saved.get('profile_kind') == delivery.get('profile_kind')
    )

    def rows(items):
        return [
            f'  - id={json.dumps(item.get("id"), ensure_ascii=False)}; '
            f'名稱={item.get("name")}; 正確檔={item.get("effective_path")}'
            for item in items
        ]

    route = (
        '請在程式指定的 JSON 回報檔中寫 delivery.method,照申請頁實際怎麼交履歷:'
        '{"method":"direct_upload"}(申請表上直接上傳檔)、{"method":"no_profile"}(不用平台履歷也不用上傳),'
        '或 {"method":"platform_profile"}(在申請頁選平台上存好的履歷)。'
        '選哪一份平台履歷、固定版還是客製版,程式已經照這張卡決定好了(第 0 步),照做就好,不用回報。'
    )
    if custom_resume:
        route += (
            '這張卡有已收下的客製履歷:申請表能直接上傳時用這張卡的檔;沒有可見的檔案欄時,開申請頁提供的'
            '「管理平台履歷」連結的 href,在同一個 Agent Chrome 另開分頁,保留原申請頁不動。'
            '在管理頁另外建立客製平台履歷、上傳這張卡的履歷和下面列出的全部平台履歷附件,'
            '逐檔讀回確認收到,再回原申請頁選新履歷;'
            '新開那一份的網址只有你知道,寫在 delivery.profile_url。'
            '不可只填一般表單就交接,也不可改選固定平台履歷來代替客製版。'
        )
    elif custom_docs:
        route += (
            '這張卡有已收下的客製附件:申請表能直接上傳時用這張卡的檔;沒有可見的檔案欄時,另開申請頁上'
            '「管理平台履歷」連結的 href,保留原申請頁不動,到平台履歷管理頁'
            '確認實際要用的附件,需要客製附件就另開客製履歷,不可改動或刪除固定版。'
        )
    request = '\n\n平台履歷與客製檔核對(程式逐位元組檢查):\n' + route
    if not verify_profile and not skip:
        request += (
            '平台履歷上的附件這一輪不用下載核對,專心把申請表填好;程式填完後,'
            '只在你的履歷或附件更新過時另外核對一次。仍要回報 delivery。'
        )
    elif skip:
        request += (
            '這份平台履歷的清單和本機檔案指紋未變,本輪不必下載附件;仍要回報 delivery。'
        )
    else:
        request += (
            '\n若使用平台履歷,全部附件（包含清單外檔）由程式從登記頁面下載、比對實際內容。'
            'profile_attachments 留空,不用自行下載或算雜湊。'
        )
    request += (
        '\n若 delivery.method=direct_upload,或這張卡有客製文件而回報 no_profile,'
        f'請把申請表實際收到的每個檔下載到這個暫存資料夾:{download_dir or "(本輪沒有,寫 upload_readback unavailable)"},在 uploaded_files 回報 '
        '{"uploaded_files":[{"name":"申請表上傳檔名","path":"暫存資料夾內的完整路徑"}]}。'
        '不可用本機來源檔複製冒充上傳結果。平台上傳後只顯示檔名、沒有下載回來的入口時(例如 Greenhouse),'
        'uploaded_files 留空清單、寫 "upload_readback":"unavailable",並在 uploaded_from 回報你放進上傳欄的本機檔 '
        '{"uploaded_from":[{"name":"申請表上顯示的檔名","path":"你交給 setFiles 的完整路徑"}]},程式會自己核對那個檔;'
        '這不算卡住,不要寫進 problems。'
    ) + door.fetch_rule()
    request += '若上傳欄沒選入檔案,把選檔失敗寫入 problems;不可用 HTTP 上傳代替瀏覽器選檔。'
    request += (
        '填文字欄的做法不能代替選檔。沒有可見上傳欄時,到平台履歷管理頁完成需要的附件操作。'
        '本輪需要上傳時,只有確認上傳成功或確認平台回報失敗並寫入 problems 後,才可完成交件;'
        '空欄位、未選檔或空檔都不算上傳成功。'
    )
    custom_profile = custom_resume or custom_docs or (
        delivery.get('method') == 'platform_profile'
        and delivery.get('profile_kind') == 'custom'
    )
    if custom_profile:
        fixed = fixed_profile_delivery(job, fb, url)
        if fixed:
            request += (
                '\n若 delivery.method=platform_profile 且 profile_kind=custom,固定平台履歷 '
                + fixed['profile_url']
                + '只讀不改;程式本輪會重新下載它的附件確認原稿沒有被改。'
            )
        else:
            request += (
                '\n若 delivery.method=platform_profile 且 profile_kind=custom,程式目前找不到已登記的固定平台履歷。'
                '先停止並在 problems 說明,不要建立或修改固定履歷。'
            )
    # 本機是唯一真相,平台履歷只是它的鏡像(像 push):固定版在填表/修改時照本機同步,可新增、覆寫、刪除。
    # 核對輪只取檔;客製版那一輪固定版是別張卡共用的,只讀。
    mirror = bool(profile_expected) and not verify_profile and not custom_profile and not skip
    if mirror:
        request += (
            '\n第 0 步同步固定平台履歷附件:程式無法確認平台上的附件是最新版(檔名相同不代表內容相同),'
            '所以下面清單每一份都要重傳:刪掉平台上對應的舊檔,再上傳正確檔(或用平台的取代功能);清單外的平台附件也刪掉。'
            '本機檔是唯一真相,平台上的只是鏡像。只動這份平台履歷的附件,本機檔不要改。'
            '格子不夠時先刪再傳。不用自己比內容,程式之後會下載全部附件逐位元組核對。\n'
            + '\n'.join(rows(profile_expected))
        )
    request += (
        '平台履歷文字依本輪填表／修改指示。'
        + ('這一輪不要刪除或覆寫平台上的附件,' if not mirror else '')
        + ('核對輪或使用客製檔時,不要修改固定平台履歷;平台格子滿了需要清理時,停止並寫入 problems 回報。'
           if verify_profile or custom_profile else '')
    )
    if verify_profile:
        request += (
            '本輪平台只讀不改,程式會自己取回實站 bytes,回報全部不符或未核對項目。'
        )
    if profile_expected and custom_profile:
        request += ('\n客製平台履歷必須上傳以下全部附件,完成後由程式下載核對:\n'
                    + '\n'.join(rows(profile_expected)))
    elif profile_expected and not verify_profile and not mirror:
        # 列了清單,agent 就會自己拿平台上的檔名去比(檔名跟看板上取的名字本來就不同),寫成「卡住」
        request += ('\n這一輪不用看平台履歷上的附件:平台上顯示的檔名跟看板上的名稱不同是正常的,'
                    '不要比、也不要寫進 problems;程式填完後會另外下載核對。')
    if not verify_profile:
        request += (
            '\n申請表直接上傳時,只從這張卡可投遞夾的 ship.json 中 files/merged 選檔,'
            '以投遞夾內的完整路徑交給 setFiles。一次交付只能是整套 files 或單一 merged,不可混搭、漏交或重複。'
            '先一起確認欄位標籤與說明、必填、單檔／多檔容量與頁面明示的檔案限制;'
            '個別檔能全部放進用途相符的欄位時用 files;容量不足時,先按標籤與說明確認履歷／文件欄可收本輪合併材料,'
            '且格式、大小與其他必填欄仍符合時,用既有 merged。不能只因 accept=pdf 就認定用途相符。'
            '只有 Resume 與 Cover Letter 兩個檔案欄、沒有附件欄時,merged 放 Resume,求職信檔案欄留空;'
            '有求職信文字框才填既有固定文字,不可把履歷或附件拆進去。'
            '另一必填檔案欄若不能由選定的完整檔案集合合法滿足,或求職信檔案欄必填,停止並回報。'
            '不要自行產檔或合併;遇到無法確認是否符合的限制、缺少必填材料或上傳失敗時停止並回報。'
        )
    return request




def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--status', action='store_true', help='每一份平台履歷現在跟母稿的關係(不開瀏覽器)')
    ap.add_argument('--platform')
    ap.add_argument('--lang', choices=cf.LANGS, default=cf.LANGS[0])
    ap.add_argument('--resume', '--variant', dest='resume', help='哪一份母稿;綜合版的平台履歷用 + 串,例如 a+b')
    ap.add_argument('--accept', help='人類明確核准的完整聲明；Agent 不可自行使用')
    ap.add_argument('--material', help='程式捕獲的 profile-material.json')
    ap.add_argument('--page', help='材料裡的指定頁面 URL')
    ap.add_argument('--item', help='材料裡的 text:N 或 field:N')
    ap.add_argument('--accept-extra', metavar='標籤=值',
                    help='字句診斷用:母稿沒有、但他確認平台上這一格可以留(例如 "婚姻狀況=不提供"):記起來,下次不再問')
    a = ap.parse_args()
    if a.status:
        for r in status():
            print(f'{r["platform"]} {r["lang"]}/{r["variant"]}: {r["state"]}')
        return
    if not a.platform:
        ap.error('要 --platform(或只看狀態用 --status)')
    if a.accept is not None:
        if not all((a.material, a.page, a.item)):
            ap.error('--accept 要同時指定 --material、--page、--item，保留原核准上下文')
        try:
            with open(a.material, encoding='utf-8') as f:
                material = json.load(f)
            approve_fact(a.platform, material, a.page, a.item, a.accept)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            sys.exit('無法核准補充來源：' + str(e))
        print('已保存人類核准聲明及上下文；平台履歷仍由 Agent 判讀。')
        return
    if not a.resume:
        ap.error('核對履歷要指定 --resume')
    if not all(part in cf.RESUMES for part in a.resume.split('+')):
        ap.error(f'--resume 要是這幾份之一(可用 + 串起來):{", ".join(cf.RESUMES)}')
    if a.accept_extra:
        label, _, value = a.accept_extra.partition('=')
        try:
            print('已記下可以留:' + accept_extra(a.platform, a.lang, a.resume, label, value))
        except ValueError as e:
            sys.exit(str(e))
        return
    import chrome_door
    door = chrome_door.current()
    if door is None:
        sys.exit(chrome_door.NO_BROWSER_AGENT)
    w, ds, prob = check(a.platform, a.lang, a.resume)
    if prob:
        sys.exit(prob)
    if not w:
        sys.exit(f'還不知道 {a.platform} {a.lang}/{a.resume} 那一份在哪({REG} 沒有)')
    print(f'{w["read"]}:字句診斷（不決定能否送出）:' + ('字句可找到' if not ds else f'{len(ds)} 段未命中\\n' + describe(ds, 99)))
    try:
        import chrome_door
        chrome_door.close_if_idle()
    except Exception:  # noqa: BLE001, S110 — 核對結果上面已經印出;收尾關 agent 的 Chrome 失敗不影響結果,下一批做完會再關
        pass


if __name__ == '__main__':
    main()
