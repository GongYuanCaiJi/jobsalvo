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
"""
import os, sys, re, json, html, argparse, unicodedata, hashlib, shlex
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
            return json.load(f)
    except (OSError, ValueError):
        return {}



def _save_registry(reg):
    folder = os.path.dirname(REG)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(REG, 'w', encoding='utf-8') as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


def attachment_check(profile_url):
    key = str(profile_url or '').strip()
    return (registry().get('_attachment_checks') or {}).get(key) or {}


def remember_attachment_check(profile_url, fingerprint, profile_kind, matched):
    key = str(profile_url or '').strip()
    if not key:
        return
    reg = registry()
    reg.setdefault('_attachment_checks', {})[key] = {
        'fingerprint': fingerprint,
        'profile_kind': profile_kind,
        'matched': bool(matched),
    }
    _save_registry(reg)


def invalidate_attachment_check(profile_url):
    saved = attachment_check(profile_url)
    if not saved or not saved.get('matched'):
        return
    remember_attachment_check(
        profile_url, saved.get('fingerprint'), saved.get('profile_kind'), False,
    )


def remember(platform, lang, variant, read_url, edit_url=None):
    """agent 第一次告訴我們那一份在哪:記下來,下次程式自己讀。"""
    reg = registry()
    entry = dict((reg.get(platform) or {}).get(f'{lang}/{variant}') or {})
    entry.update(read=read_url, edit=edit_url or read_url)
    reg.setdefault(platform, {})[f'{lang}/{variant}'] = entry
    _save_registry(reg)


def where(platform, lang, variant):
    return (registry().get(platform) or {}).get(f'{lang}/{variant}')



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


def expected(platform, lang, variant):
    """回 [(哪一段, 內容, 連結清單)]。"""
    path = cf.master(variant, lang)
    if not path or not os.path.isfile(path) or not path.lower().endswith(('.md', '.markdown', '.txt', '.pdf')):
        return []
    try:
        if path.lower().endswith('.pdf'):
            import settings_api
            text = settings_api.pdf_text(path)
        else:
            with open(path, encoding='utf-8') as f:
                text = f.read()
    except Exception:
        return []
    return md_sections(text)


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


def check(platform, lang, variant, board=None, reader=None, test_allow_local=False, reported=None):
    """讀回來比。loopback 只供本機假頁測試明確開啟;正式呼叫保持關閉。
    reported:agent 這一輪回報的「平台用自己說法寫」清單,程式在同一頁上驗過才收下,再一起比。"""
    w = where(platform, lang, variant)
    if not w:
        return None, [], ''
    import agent_chrome
    read_url = w['read']
    if not _same_platform_url(read_url, platform, allow_local=test_allow_local):
        return w, [], '讀取網址不安全'
    reader = reader or (lambda u: agent_chrome.read_pages(
        [u], board, ready=lambda r: len(r.get('text', '')) > 800, settle=2
    )[u])
    try:
        page = reader(read_url)
    except Exception as e:
        return w, [], f'讀不到平台上那一份({str(e)[:80]})'
    final_url = page.get('url') or read_url
    if not _same_platform_url(final_url, platform, allow_local=test_allow_local):
        return w, [], '平台履歷讀取後跳到不屬於原平台的網址'
    if 'login' in str(final_url).lower() or not page.get('text'):
        return w, [], f'讀不到平台上那一份(可能要登入):{page.get("url") or read_url}'
    want = expected(platform, lang, variant)
    rejected = accept_equivalents(platform, lang, variant, page, want, reported) if reported else {}
    ds = diff(page, want, (where(platform, lang, variant) or {}).get('equivalents'))
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
    digest = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _safe_page_url(value):
    parsed = urlsplit(str(value or '').strip())
    host = (parsed.hostname or '').casefold()
    return bool(parsed.scheme == 'https' and host) or bool(
        parsed.scheme == 'http' and host in ('localhost', '127.0.0.1', '::1')
    )


def _loopback_url(value):
    try:
        return (urlsplit(str(value or '').strip()).hostname or '').casefold() in (
            'localhost', '127.0.0.1', '::1'
        )
    except ValueError:
        return False


def _same_platform_url(value, platform, allow_local=False):
    """Loopback 是測試 fixture 專用;正式網頁必須符合平台網域。"""
    if not _safe_page_url(value):
        return False
    if _loopback_url(value):
        return allow_local
    return profile_key(value) == platform


# 平台用自己的格式存的格子(下拉選單、日期寫法、自己的用詞):母稿「錄取後一個月內可上班」、
# 104 顯示「錄取後一個月可上班」。字面永遠比不過,但意思一樣。agent 回報「母稿這一格 ＝ 頁面上這幾個字」,
# 程式確認那幾個字真的在頁面上才記下(存在 profiles.json,照母稿那一格的原文當 key)。
# 以前的做法是欄位對照:列出平台編輯頁每一格對到母稿哪一段,綁整份母稿雜湊。104 的內容在一格一格的編輯視窗裡,
# agent 要打開每一個視窗(一次 13 分鐘),改母稿一個字就整份重做,還會因為一格帳號欄位名稱沒見過就整份作廢。
NOT_SHOWN_MAX = 24          # 「平台不顯示」只收短格子(兵役：免役、應屆畢業這種);一般句子、整段內容不能這樣帶過


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


def remember_application_history(platform, url, test_allow_local=False):
    """平台的應徵紀錄頁(查回音用)。網址要屬於這個平台才記。"""
    if not url or not _same_platform_url(url, platform, allow_local=test_allow_local):
        return False
    reg = registry()
    reg.setdefault('_application_records', {})[platform] = url
    _save_registry(reg)
    return True


def application_record_pages(test_allow_local=False):
    """已知平台的應徵紀錄頁;loopback 僅供測試 fixture 明確開啟。"""
    records = registry().get('_application_records') or {}
    return [{'platform': str(platform), 'url': str(url)}
            for platform, url in records.items()
            if isinstance(url, str) and url and _safe_page_url(url)
            and _same_platform_url(url, platform, allow_local=test_allow_local)]


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
        delivery.get('profile_url'),
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
                               force=False):
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
    if field == 'fixed_profile_attachments':
        fixed = report.get('fixed_profile')
        if not isinstance(fixed, dict):
            problems.append('agent 沒回報固定平台履歷的網址和附件')
            remember_attachment_check(profile_url, fingerprint, profile_kind, False)
            return problems
        if str(fixed.get('url') or '').strip() != profile_url:
            problems.append('固定平台履歷回報網址與已登記網址不同,附件下載不採信')
            remember_attachment_check(profile_url, fingerprint, profile_kind, False)
            return problems
        source_report = {'fixed_profile_attachments': fixed.get('attachments')}

    actual = _reported_downloads(source_report, field, download_dir, problems)
    expected = []
    for item in attachment_sources(job, fb, delivery):
        source = item.get('effective_path')
        name = str(item.get('name') or os.path.basename(source or '') or '未命名附件')
        if not source or not os.path.isfile(source):
            problems.append(f'本機附件「{name}」不見了,無法比對平台附件')
        else:
            expected.append((item, name, source))

    pairs, remaining = _pair_reported_files(expected, actual)
    label = '固定平台履歷附件' if field == 'fixed_profile_attachments' else '平台附件'
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


def check_attachments(job, fb, url, report, download_dir, force=False,
                      expected_delivery=None, verify_profile=True):
    """依 agent 回報的投遞方式檢查平台附件或申請表實際上傳檔。
    verify_profile=False:平台履歷上的附件這一輪沒下載,不核對(填完後、送出前另外核對)。"""
    if not isinstance(report, dict):
        return ['agent 沒回報這次直接上傳或使用哪一份平台履歷']
    delivery = report.get('delivery')
    if not isinstance(delivery, dict):
        return ['agent 沒回報這次直接上傳或使用哪一份平台履歷']
    method = delivery.get('method')
    if method in ('direct_upload', 'no_profile'):
        if expected_delivery and _delivery_key(delivery) != _delivery_key(expected_delivery):
            return ['送出前回報的投遞方式或平台履歷與核准時不同']
        if method == 'no_profile' and not has_custom_documents(job, fb):
            return []
        return _check_uploaded_files(job, fb, url, report, download_dir)
    if method != 'platform_profile':
        return ['agent 回報的投遞方式無法辨認']

    profile_url = str(delivery.get('profile_url') or '').strip()
    profile_kind = delivery.get('profile_kind')
    if not profile_url or profile_kind not in ('fixed', 'custom'):
        return ['agent 沒說明平台履歷網址,或它是固定版還是客製版']
    if expected_delivery and _delivery_key(delivery) != _delivery_key(expected_delivery):
        return ['送出前回報的平台履歷與核准時不同']
    if has_custom_resume(job, fb) and profile_kind == 'fixed':
        return ['這張卡有已收下的客製檔,不能把它放進固定平台履歷']
    if not verify_profile:
        return []

    problems = _check_profile_attachments(
        job, fb, report, download_dir, delivery, 'profile_attachments',
        force=force,
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
                'fixed_profile_attachments', force=True,
            ))
    return problems


def profile_attachments_fresh(job, fb, url):
    """這張卡用的平台履歷,是不是已經用「現在的履歷和附件」核對過而且對得上。
    指紋是履歷、語言、附件設定和檔案內容,不是每張卡各一份:更新之後核對過一次,後面的卡都沿用。
    客製平台履歷每張卡不同,一律回 False(送出前照舊核對)。"""
    apply = (fb.get(url) or {}).get('apply') or {}
    delivery = apply.get('delivery') or {}
    if delivery.get('method') != 'platform_profile' or delivery.get('profile_kind') != 'fixed':
        return False
    saved = attachment_check(delivery.get('profile_url'))
    return bool(saved.get('matched')
                and saved.get('fingerprint') == attachment_fingerprint(job, fb, delivery)
                and saved.get('profile_kind') == 'fixed')


# 平台上的檔要拿回來比對時怎麼取。三條路都試過:
# 點下載連結 → Chrome 一般下載,Mac 上會把 Chrome 叫到最前面、切走使用者的畫面;
# 在頁面裡 fetch → 外掛跑 evaluate 的環境根本沒有 fetch/XMLHttpRequest;
# locator.downloadMedia() → 外掛自己的下載,不搶前景、回傳確切路徑,但同一頁下載第二個檔會跳「要下載多個檔案」的詢問,
# 沒人按就卡到逾時(實測卡 120 秒、REPL 被重置)。所以一個檔開一個新分頁。
FETCH_FILE_RULE = (
    '取檔方式(一定要照做):一個檔開一個新的背景分頁(cua.createBrowserTab 開那個檔所在的頁面),'
    '在那個分頁對那個檔的連結呼叫 tab.playwright.locator(...).downloadMedia(),它會回傳存好的完整路徑;'
    '用 REPL 的 await import("node:fs") 把「回傳的那個路徑」搬(renameSync,不是複製)進暫存資料夾,'
    '再關掉那個分頁,下一個檔再開新分頁。同一個分頁連續下載第二個檔會跳「要下載多個檔案」的詢問,沒人按就卡住。'
    '不要點下載連結、不要把檔案網址開成分頁(會觸發 Chrome 的一般下載,Chrome 會跳到最前面、把使用者的畫面切走);'
    '也不要去翻使用者的「下載」資料夾找檔,只搬 downloadMedia 回傳的那個路徑。'
    '取不到(逾時、被拒)就把原因寫進 problems 並停止,不要改用點連結或開分頁。'
)


def attachment_step(job, fb, url, download_dir, force=False, verify_profile=True):
    """告訴 agent 回報投遞方式,並核對實際使用的附件。
    verify_profile=False(填表、修改):平台履歷附件這一輪不下載核對,程式填完後只在需要時另外核對。"""
    import ship
    try:
        host = (urlsplit(str(url)).hostname or '').casefold()
    except ValueError:
        host = ''
    local_acceptance = host in ('localhost', '127.0.0.1', '::1')
    apply = (fb.get(url) or {}).get('apply') or {}
    reported_delivery = apply.get('delivery') or {}
    custom_resume = has_custom_resume(job, fb)
    custom_docs = has_custom_documents(job, fb)
    delivery = reported_delivery
    if custom_resume:
        profile_delivery = (
            delivery if delivery.get('method') == 'platform_profile'
            and delivery.get('profile_kind') == 'custom'
            else {'profile_kind': 'custom'}
        )
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

    root = download_dir or '(執行時建立暫存資料夾)'
    route = (
        '請在程式指定的 JSON 回報檔中,delivery 必填:'
        '{"method":"direct_upload"}、{"method":"no_profile"}，或 '
        '{"method":"platform_profile","profile_kind":"fixed 或 custom","profile_url":"平台履歷網址"}。'
    )
    if custom_resume:
        route += (
            '這張卡有已收下的客製履歷:申請表能直接上傳時用這張卡的檔;沒有可見的檔案欄時,開申請頁提供的'
            '「管理平台履歷」連結的 href,在同一個 Agent Chrome 另開分頁,保留原申請頁不動。'
            '在管理頁另外建立客製平台履歷、上傳這張卡的檔,再回原申請頁選新履歷。'
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
            '\n若使用平台履歷,請把該履歷頁上看到的全部附件逐一下載到這個暫存資料夾:'
            + root + '\n'
            '取檔方式照下面「取檔方式」那段。'
            '在 profile_attachments 回報全部下載檔,格式為 '
            '{"profile_attachments":[{"name":"平台顯示名稱","path":"暫存資料夾內的完整路徑"}]}。'
            '沒有附件時也要回報空清單;不可只回報清單上的附件,不可用本機正確檔冒充下載檔。'
            '不要用檔名或大小判斷內容。'
        )
    request += (
        '\n若 delivery.method=direct_upload,或這張卡有客製文件而回報 no_profile,'
        '請把申請表實際收到的每個檔下載到同一暫存資料夾,在 uploaded_files 回報 '
        '{"uploaded_files":[{"name":"申請表上傳檔名","path":"暫存資料夾內的完整路徑"}]}。'
        '不可用本機來源檔複製冒充上傳結果。平台上傳後只顯示檔名、沒有下載回來的入口時(例如 Greenhouse),'
        'uploaded_files 留空清單、寫 "upload_readback":"unavailable",並在 uploaded_from 回報你放進上傳欄的本機檔 '
        '{"uploaded_from":[{"name":"申請表上顯示的檔名","path":"你交給 setFiles 的完整路徑"}]},程式會自己核對那個檔;'
        '這不算卡住,不要寫進 problems。'
    ) + FETCH_FILE_RULE
    request += (
        '需要選本機檔案時,使用 Agent Chrome 的 Playwright filechooser:先 waitForEvent("filechooser"),'
        '再點檔案欄位,最後對 chooser 呼叫 setFiles([完整檔案路徑])。選擇後要確認頁面已收到檔案;'
        '若 input.files.length 仍是 0,不能宣稱成功或直接交接。'
    )
    if local_acceptance:
        request += (
            '本輪網址在 loopback。驗收用申請頁、它連到的同站管理頁與履歷頁都會顯示「本機假驗收頁」標記。'
            '只有目前頁面可見這個標記、主機仍是 loopback,而且你從該頁讀到實際 input 所屬 form 的 action 和 name 時,'
            '才可用 curl multipart 對該 action 上傳清單所列合成測試檔;檢查 HTTP 狀態並重整頁面確認結果。'
            '下載這種本機假頁的附件時,從頁面讀出下載連結 href,用 curl -fL 將遠端回傳內容存到指定暫存夾;'
            '不可用本機來源檔複製冒充下載檔。不可猜 endpoint,也不可改 Chrome 權限或開 chrome://extensions。'
        )
    else:
        request += (
            '若 filechooser 沒選入檔案,把選檔失敗寫入 problems 並停止;不要改 Chrome 權限,'
            '不要使用其他方式假裝已上傳。'
        )
    request += (
        'tab.setValue 只填文字欄,不能代替選檔。沒有可見上傳欄時,到平台履歷管理頁完成需要的附件操作。'
        '只有確認上傳成功或確認平台回報失敗並寫入 problems 後,才可 markHandoff;'
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
                '\n若 delivery.method=platform_profile 且 profile_kind=custom,另外重新打開固定平台履歷 '
                + fixed['profile_url']
                + '。只讀不改;即使以前比對過,本輪仍要把它上面的全部附件重新下載到 '
                + root
                + '。在 fixed_profile 回報網址和附件,格式為 '
                '{"fixed_profile":{"url":"固定平台履歷網址","attachments":'
                '[{"name":"平台顯示名稱","path":"暫存資料夾內的完整路徑"}]}}。'
            )
        else:
            request += (
                '\n若 delivery.method=platform_profile 且 profile_kind=custom,程式目前找不到已登記的固定平台履歷。'
                '先停止並在 problems 說明,不要建立或修改固定履歷。'
            )
    request += (
        '不要刪除任何平台履歷或附件,也不要修改固定平台履歷。平台格子滿了,'
        '或多出來的檔需要清理時,停止並寫入 problems 回報使用者;不要自己處理。'
    )
    report_command = (
        f'python3 {shlex.quote(cf.tool("agent_report.py"))} --from 代投 '
        f'--job {shlex.quote(str(url))} --need "清理平台多出附件" '
        '"平台履歷多出附件：<完整檔名>；已保留未刪，請使用者處理。"'
    )
    # 多出來的附件要下載回來逐位元組比才判得準;平台上顯示的是檔名,跟看板上取的名字本來就不一樣,
    # 填表那一輪只看名字會把正確的檔當成多出來的(2026-09-27 e2e 一張 104 就這樣卡住)。只在核對那一輪做。
    if verify_profile:
        request += (
            '若任何固定或客製平台履歷有本輪正確附件清單以外的檔案（包含舊版），'
            '必須在 JSON 的 problems 寫明平台履歷類型、完整檔名及需使用者處理的原因，'
            '並另外執行標準回報指令: ' + report_command + '。'
            '只列在 profile_attachments、fixed_profile.attachments 或 notes 不算回報；'
            'problems 與看板回報兩者完成前不得 markHandoff。保留平台原檔，不可刪除或覆寫。'
        )
    if profile_expected and (verify_profile or custom_profile):
        request += '\n這張卡要用的平台履歷附件:\n' + '\n'.join(rows(profile_expected))
    elif profile_expected:
        # 列了清單,agent 就會自己拿平台上的檔名去比(檔名跟看板上取的名字本來就不同),寫成「卡住」
        request += ('\n這一輪不用看平台履歷上的附件:平台上顯示的檔名跟看板上的名稱不同是正常的,'
                    '不要比、也不要寫進 problems;程式填完後會另外下載核對。')
    request += '\n這張卡直接上傳申請表時要用的檔:\n' + '\n'.join(
        rows(ship.documents(job, fb))
    )
    return request


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--platform', required=True)
    ap.add_argument('--lang', choices=cf.LANGS, default=cf.LANGS[0])
    ap.add_argument('--resume', '--variant', dest='resume', choices=list(cf.RESUMES) or None, required=True)
    a = ap.parse_args()
    w, ds, prob = check(a.platform, a.lang, a.resume)
    if prob:
        sys.exit(prob)
    if not w:
        sys.exit(f'還不知道 {a.platform} {a.lang}/{a.resume} 那一份在哪({REG} 沒有)')
    print(f'{w["read"]}:' + ('跟母稿對得上' if not ds else f'{len(ds)} 段對不上\\n' + describe(ds, 99)))
    try:
        import agent_chrome
        agent_chrome.close_if_idle()
    except Exception:  # noqa: S110
        pass


if __name__ == '__main__':
    main()
