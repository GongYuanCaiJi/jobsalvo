"""
reply_run —— 「📬 查回音」:已投遞的卡,誰回了、回了什麼。

agent 在自己的 Chrome 搜尋適合的來源,回傳回音證據、平台應徵紀錄上的職缺和無法讀取的來源。
程式只驗證交件形狀,再依證據更新看板、對帳應徵紀錄(sync_sent)、記錄待辦和無下文狀態。
"""
import contextlib, os, sys, json, time, argparse, datetime, hashlib, re, urllib.parse
from dataclasses import dataclass, field
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd
import agent_run as ar
import evidence
import jobrun
import agent_report
import config as cf
import card

SP = os.environ.get('REPLY_TMP', cf.TMP)
STATUS = 'reply_status.json'
TIMEOUT = 60 * 60
GHOST_DAYS = int((cf.C.get('replies') or {}).get('ghost_days', 30))
# 信箱在設定 replies.mail_url(預設 Gmail 第一個帳號)。是 Gmail(任一個帳號 /mail/u/N/)程式自己搜尋、複製全文;
# 其他信箱(Outlook、公司信箱…)程式讀不懂畫面,整份交給 agent 用它的 Chrome 打開那個信箱補查。
GMAIL_DEFAULT = 'https://mail.google.com/mail/u/0/'


def mailbox(url=None):
    """(信箱網址, 是不是 Gmail)。Gmail 網址整理成 https://mail.google.com/mail/u/N/。"""
    url = str(url if url is not None else ((cf.C.get('replies') or {}).get('mail_url') or GMAIL_DEFAULT)).strip()
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return GMAIL_DEFAULT, True
    if (parsed.hostname or '').casefold() in ('mail.google.com', 'gmail.google.com'):
        m = re.match(r'/mail/u/(\d+)', parsed.path or '')
        return f'https://mail.google.com/mail/u/{m.group(1) if m else 0}/', True
    return url, False


def mailbox_message_ref(link, box=None):
    """不是 Gmail 的信箱:agent 補查回來的原文連結指向同一個信箱(同一台主機、https),才算信件來源。"""
    base, is_gmail = box or mailbox()
    if is_gmail:
        return ''
    try:
        got, want = urllib.parse.urlsplit(str(link or '')), urllib.parse.urlsplit(base)
    except ValueError:
        return ''
    if (got.scheme != 'https' or got.username or got.password or not got.hostname
            or got.hostname.casefold() != (want.hostname or '').casefold() or str(link).rstrip('/') == base.rstrip('/')):
        return ''
    return hashlib.blake2b(str(link).encode('utf-8'), digest_size=8).hexdigest()


GMAIL, _IS_GMAIL = mailbox()
MAX_MAILS = 60
OC_RANK = {'': 0, 'iv': 1, 'offer': 2}
OC_END = {'rej', 'ghost', 'wd'}
KIND_TO_OC = {'reject': 'rej', 'interview': 'iv', 'offer': 'offer'}
OC_WORD = {'': '等回音', 'iv': '面試中', 'offer': 'Offer', 'rej': '沒錄取', 'ghost': '沒下文', 'wd': '我不去了'}
KINDS = {'confirm', 'reject', 'interview', 'offer'}


@dataclass(frozen=True)
class EchoReadResult:
    findings: dict
    checked: set
    job_ids: list
    inaccessible: list
    dropped: list = field(default_factory=list)   # 格式不對、單獨丟掉的那幾筆(寫進紀錄)


def today():
    return datetime.date.today().isoformat()


def load(board):
    with open(board, encoding='utf-8') as f:
        p = bd.parse(f.read())
    return {j['id']: j for j in p['data']['jobs']}, json.loads(p['fb'])


def set_outcome(f, s, day):
    """跟看板 board.js 的 setOutcome 同一套:每個結果第一次出現的日期留在 oc_at。"""
    at = dict(f.get('oc_at') or {})
    for k in list(at):
        if (k in OC_END and k != s) if s in OC_END else (k in OC_END or OC_RANK.get(k, 0) > OC_RANK.get(s, 0)):
            del at[k]
    if s and s not in at:
        at[s] = day
    if s:
        f['oc'] = s
    else:
        f.pop('oc', None)
    if at:
        f['oc_at'] = at
    else:
        f.pop('oc_at', None)


def waiting(fb):
    """已投遞、還沒結束的卡;面試中和 Offer 也要查後續回音。"""
    return [u for u, m in fb.items() if isinstance(m, dict) and m.get('app') == 'sent' and not m.get('rm')
            and (m.get('oc') or '') not in ('rej', 'wd')]


def since_of(fb, url):
    item = fb.get(url) or {}
    return str((item.get('replies') or {}).get('at') or item.get('sent_at') or '')[:10]


def _company(jobs, url):
    import prefs
    job = jobs.get(url) or {}
    # 搜信範圍只用明確公司或卡名中的公司段；徵才網址 slug 不能當成公司名稱。
    company = str(job.get('company') or prefs.company_segment(job) or '').strip()
    if not company:
        parts = card.name(job).split('·')
        company = parts[-1].strip() if len(parts) > 1 else ''
    return company


def _companies(jobs, urls):
    out = []
    for url in urls:
        company = _company(jobs, url)
        if company and company not in out:
            out.append(company)
    return out


def _gmail_term(value):
    """Keep company names as quoted literals, stripping Gmail query syntax characters."""
    value = re.sub(r"[^\w\s&.',-]", ' ', str(value or ''), flags=re.UNICODE)
    return ' '.join(value.split())[:100]


def _gmail_terms(company):
    """Return the safe Gmail literals that actually cover one company name.
    一家要搜的字:原名、去掉法律字尾的名字(「甲科技股份有限公司」的信常只署名「甲科技」)、他在設定寫的公司別名,
    再加英文名的第一個字。第一個字是冠詞(The)不單獨搜:幾乎每封信都中,結果一頁放不下,整個搜尋被判沒讀完。
    (短的像 LG 照搜:少搜到信會被當成沒回音、記成沒下文,比搜太多交給 agent 補查更糟。)"""
    short = card.company_norm(company)
    names = [company, short]
    for k, v in ((cf.C.get('board') or {}).get('company_alias') or {}).items():
        if card.same_company(v, company) or card.same_company(k, company):
            names += [k, v]
    words = str(short or '').split()
    if len(words) > 1 and words[0].casefold() not in ('the', 'a', 'an'):
        names.append(words[0])
    terms = []
    for value in names:
        literal = _gmail_term(value)
        if literal and literal not in terms:
            terms.append(literal)
    return terms


def gmail_query(fb, jobs, urls):
    """一次搜尋所有公司,用最早的上次查詢日期,再逐封郵件交給 agent 配對。"""
    days = sorted(day for day in (since_of(fb, url) for url in urls) if day)
    start = (datetime.date.fromisoformat(days[0]) - datetime.timedelta(days=1)
             if days else datetime.date.today() - datetime.timedelta(days=60))
    companies = _companies(jobs, urls)
    terms = []
    for company in companies:
        for literal in _gmail_terms(company):
            if literal and literal not in terms:
                terms.append(literal)
    query = (f'after:{start:%Y/%m/%d} ('
             + ' OR '.join(f'"{term}"' for term in terms) + ')') if terms else ''
    return query, companies


def _read_pages(urls, board=None, reader=None, ready=None, settle=0):
    """程式自己讀這幾頁:照現在用 Chrome 的那一家的門路(做不到的那一家照實丟「現在做不到」,那幾個來源交給 agent 補查)。"""
    if reader:
        return reader(urls)
    import chrome_door
    door = chrome_door.current()
    if door is None:
        raise RuntimeError(chrome_door.NO_BROWSER_AGENT)
    return door.read_pages(urls, board, ready=ready, settle=settle)


def _page_problem(page):
    url = str((page or {}).get('url') or '').casefold()
    title = str((page or {}).get('title') or '').casefold()
    text = str((page or {}).get('text') or '')
    if not page or not text:
        return '程式讀不到頁面文字'
    if 'accounts.google.com' in url or any(x in title for x in ('sign in', 'log in', '登入')):
        return '來源要求登入'
    return ''


def _gmail_page_problem(page, source_url):
    if isinstance(page, dict) and page.get('_ready') is False:
        return 'Gmail 頁面在等待時間內未確認完整載入'
    problem = _page_problem(page)
    if problem:
        return problem
    final_url = (page or {}).get('url') or source_url
    try:
        parsed = urllib.parse.urlsplit(final_url)
    except ValueError:
        return 'Gmail 來源網址格式錯誤'
    if parsed.scheme != 'https' or (parsed.hostname or '').casefold() not in ('mail.google.com', 'gmail.google.com'):
        return 'Gmail 來源讀取後跳到其他網站'
    return ''


def gmail_message_id(value):
    """Accept only a specific Gmail conversation URL and return its thread id."""
    try:
        parsed = urllib.parse.urlsplit(str(value or ''))
    except ValueError:
        return ''
    host = (parsed.hostname or '').casefold()
    if (parsed.scheme != 'https' or host not in ('mail.google.com', 'gmail.google.com')
            or parsed.username or parsed.password
            or not re.fullmatch(r'/mail/u/\d+/?', parsed.path)):
        return ''
    query_id = (urllib.parse.parse_qs(parsed.query).get('th') or [''])[0]
    if re.fullmatch(r'[A-Za-z0-9_-]{6,}', query_id):
        return query_id
    parts = parsed.fragment.split('/')
    if (len(parts) == 2 and parts[0] in {
            'all', 'inbox', 'sent', 'starred', 'important', 'drafts', 'spam', 'trash',
    } and re.fullmatch(r'[A-Za-z0-9_-]{6,}', parts[1])):
        return parts[1]
    if (len(parts) >= 3 and parts[0] in ('label', 'category')
            and re.fullmatch(r'[A-Za-z0-9_-]{6,}', parts[-1])):
        return parts[-1]
    return ''


_GMAIL_RESULT_RANGE = re.compile(
    r'(?<!\d)(\d[\d,]*)\s*[-–—]\s*(\d[\d,]*)\D{0,16}(?:of|共|/)\D{0,8}(\d[\d,]*)(\+?)(?!\d)',
    re.I,
)
_GMAIL_EMPTY_MARKERS = (
    'no conversations match your search', 'no messages matched your search',
    'no results found', '沒有符合搜尋條件的郵件', '沒有符合搜尋條件的對話',
    '找不到符合搜尋條件的郵件', '找不到符合條件的結果', '沒有符合搜尋條件的結果',
)


def _gmail_result_range(text):
    for match in _GMAIL_RESULT_RANGE.finditer(str(text or '')):
        start, end, total = (int(value.replace(',', '')) for value in match.groups()[:3])
        approximate = bool(match.group(4))
        if start == end == total == 0:
            return 0, 0, 0, approximate
        if start > 0 and end >= start and total >= end:
            return start, end, total, approximate
    return None


def _gmail_search_thread_ids(page):
    anchors = (page or {}).get('anchors') or [
        {'href': link, 'text': ''} for link in (page or {}).get('links') or []
    ]
    return list(dict.fromkeys(
        thread_id for anchor in anchors if isinstance(anchor, dict)
        if (thread_id := gmail_message_id(anchor.get('href')))
    ))


def _gmail_search_ready(page):
    if (page or {}).get('readyState') != 'complete':
        return False
    thread_ids = _gmail_search_thread_ids(page)
    text = str((page or {}).get('text') or '')
    result_range = _gmail_result_range(text)
    if result_range and result_range[:3] == (0, 0, 0):
        return not thread_ids
    if not thread_ids and any(marker in text.casefold() for marker in _GMAIL_EMPTY_MARKERS):
        return True
    if not result_range:
        return False
    start, end, _total, _approximate = result_range
    return start == 1 and len(thread_ids) == end - start + 1


def _page_ready(page):
    page = page or {}
    if page.get('readyState') != 'complete':
        return False
    text = str(page.get('text') or '').strip()
    return bool(text and text.casefold() not in {'loading', 'loading...', '載入中', '載入中...'})


def _gmail_thread_ready(page):
    return _page_ready(page) and not _gmail_thread_problem(page)


def _gmail_thread_problem(page):
    """Require the full thread print view and a text block for every message node."""
    page = page or {}
    if page.get('emailThreadPrintView') is not True:
        return 'Gmail 對話沒有載入完整列印檢視,無法確認每封郵件'
    raw_bodies = page.get('emailBodies')
    if not isinstance(raw_bodies, list):
        return 'Gmail 對話郵件區塊尚未載入,無法確認每封郵件'
    bodies = [str(body).strip() for body in raw_bodies
              if str(body).strip()]
    count = page.get('emailMessageCount')
    if not isinstance(count, int) or isinstance(count, bool) or count < 1 or count != len(bodies):
        return 'Gmail 對話郵件數與已讀正文數不一致,無法確認每封郵件'
    text = str(page.get('text') or '')
    if any(body not in text for body in bodies):
        return 'Gmail 對話有正文沒有完整包含在複製文字中'
    return ''


def _gmail_search_problem(page):
    thread_ids = _gmail_search_thread_ids(page)
    result_range = _gmail_result_range((page or {}).get('text'))
    if result_range and result_range[:3] == (0, 0, 0):
        return '' if not thread_ids else 'Gmail 空結果筆數與頁面郵件連結不一致'
    if not result_range:
        text = str((page or {}).get('text') or '').casefold()
        if not thread_ids and any(marker in text for marker in _GMAIL_EMPTY_MARKERS):
            return ''
        return '無法確認 Gmail 搜尋結果是否完整'
    start, end, total, approximate = result_range
    visible = end - start + 1
    if start != 1:
        return 'Gmail 搜尋結果不是從第一封開始,無法確認前面的郵件'
    if len(thread_ids) != visible:
        return f'Gmail 搜尋頁顯示 {visible} 封,但只讀到 {len(thread_ids)} 封,結果尚未完整載入'
    if approximate or total > visible:
        return f'Gmail 搜尋共有至少 {total} 封,目前只複製 {visible} 封,尚未讀完'
    return ''


# ---- 補查來源的核實(#289 決定 2):agent 說查過了不算數,程式核實過來源真的讀完,那幾張卡才推論沒下文 ----
# 核實只看一份摘要(不是全文):網址、標題、載完沒、搜尋頁上的筆數那幾行和每封信的連結、列印檢視每封信都有正文。
# Codex:程式自己再讀一次(read_pages 的全文照 _digest 算出摘要)。只用 Claude:Claude 在每一頁跑下面這支唯讀函式,
# 程式從它那一輪的紀錄拿工具的回傳(apply_tab.self_reads,跟代投核對頁面同一套:只收程式碼一字不差的那幾次,
# 內容是工具給的、模型改不了)。只回摘要是因為分段拿(每段 900 字),一封信的全文要拿好幾十次。
# 函式裡不用反斜線:Claude 會自己把跳脫字換掉(apply_tab 實測),程式碼就對不上了。
_LINE_RE = re.compile('[0-9][0-9,]*.{0,3}[-–—].{0,3}[0-9]')
_MAIL_LINK = 'https://mail.google.com/mail/u/'
VERIFY_FN = (
    "() => {const text = (document.body && document.body.innerText) || '';"
    " const marks = " + json.dumps(list(_GMAIL_EMPTY_MARKERS), ensure_ascii=False) + ";"
    " const lines = text.split(String.fromCharCode(10)).map(l => l.trim()).filter(l => l && l.length <= 80 &&"
    " (new RegExp(" + json.dumps(_LINE_RE.pattern, ensure_ascii=False) + ").test(l) || marks.some(m => l.toLowerCase().includes(m))));"
    " const links = [...new Set([...document.links].map(a => a.href).filter(h => h.startsWith(" + json.dumps(_MAIL_LINK) + ")"
    " && (h.includes('#') || h.includes('th='))))];"
    " const q = new URL(location.href).searchParams;"
    " const printView = q.get('view') === 'pt' && q.get('search') === 'all' && q.has('th') && Boolean(document.querySelector('.bodycontainer'));"
    " const bodies = (printView ? [...document.querySelectorAll('.bodycontainer .message')] : []).map(el => (el.innerText || el.textContent || '').trim());"
    " const full = bodies.filter(b => b);"
    " return {url: location.href, title: document.title, readyState: document.readyState, hasText: text.trim().length > 0,"
    " lines, links, printView, messageCount: bodies.length, bodies: full.length, bodiesInText: full.every(b => text.includes(b))};}")
_VERIFY_WHOLE = 'JSON.stringify((' + VERIFY_FN + ')())'


def _digest(page):
    """程式自己讀到的整頁(read_pages)算成跟 VERIFY_FN 一樣的摘要。"""
    page = page or {}
    text = str(page.get('text') or '')
    lines = [line.strip() for line in text.split('\n')]
    lines = [line for line in lines if line and len(line) <= 80 and (
        _LINE_RE.search(line) or any(m in line.casefold() for m in _GMAIL_EMPTY_MARKERS))]
    hrefs = page.get('links') or [a.get('href') for a in page.get('anchors') or [] if isinstance(a, dict)]
    links = list(dict.fromkeys(h for h in hrefs if isinstance(h, str) and h.startswith(_MAIL_LINK)
                               and ('#' in h or 'th=' in h)))
    bodies = [str(b).strip() for b in page.get('emailBodies') or []]
    full = [b for b in bodies if b]
    return {'url': page.get('url') or '', 'title': page.get('title') or '', 'readyState': page.get('readyState'),
            'hasText': bool(text.strip()), 'lines': lines, 'links': links,
            'printView': page.get('emailThreadPrintView') is True,
            'messageCount': page.get('emailMessageCount') if isinstance(page.get('emailMessageCount'), int) else len(bodies),
            'bodies': len(full), 'bodiesInText': all(b in text for b in full)}


def _thread_url(box, thread_id):
    return f'{box}?ui=2&view=pt&search=all&th={thread_id}'


def _digest_problem(d, kind, platform=''):
    """核實這一頁讀完了沒;沒問題回空字串。kind:search(Gmail 搜尋頁)、thread(一封信的列印檢視)、record(平台應徵紀錄頁)。"""
    if not isinstance(d, dict):
        return '程式沒有核實讀到這一頁'
    url, title = str(d.get('url') or ''), str(d.get('title') or '').casefold()
    if d.get('readyState') != 'complete' or not d.get('hasText'):
        return '頁面沒載完'
    if 'accounts.google.com' in url.casefold() or any(x in title for x in ('sign in', 'log in', '登入')):
        return '來源要求登入'
    if kind == 'record':
        import profile_sync as ps
        return '' if ps.same_platform_url(url, platform) else '平台應徵紀錄讀取後跳到其他網站'
    problem = _gmail_page_problem({'url': url, 'text': 'x'}, url)
    if problem:
        return problem
    if kind == 'search':
        return _gmail_search_problem({'text': '\n'.join(str(x) for x in d.get('lines') or []),
                                      'anchors': [{'href': h} for h in d.get('links') or []]})
    count = d.get('messageCount')
    if (d.get('printView') is not True or not isinstance(count, int) or isinstance(count, bool) or count < 1
            or count != d.get('bodies') or d.get('bodiesInText') is not True):
        return 'Gmail 對話沒有完整讀到每封信'
    return ''


def _same_page(asked, got, kind):
    """核實讀到的那一頁是不是補查清單上的這一個來源(Claude 的摘要只有它最後停在的網址)。"""
    try:
        a, b = urllib.parse.urlsplit(asked), urllib.parse.urlsplit(got)
    except ValueError:
        return False
    if (a.hostname or '').casefold() != (b.hostname or '').casefold() or a.path.rstrip('/') != b.path.rstrip('/'):
        return False
    if kind == 'search':
        def q(u):
            return urllib.parse.unquote(u.fragment.replace('+', ' '))
        return q(a) == q(b)
    if kind == 'thread':
        return urllib.parse.parse_qs(a.query).get('th') == urllib.parse.parse_qs(b.query).get('th') \
            and urllib.parse.parse_qs(b.query).get('view') == ['pt']
    return True


def unverified_cards(unavailable, digests, box=None):
    """補查清單上程式沒核實讀完的來源,影響到的卡(這一輪不推論沒下文、查過日期不往前推)。
    digests:核實讀到的摘要(可帶 _asked = 程式要讀的網址)。沒有網頁可核實的來源(不是 Gmail 的信箱、
    公司名沒有能搜的字)永遠算沒核實:那些卡只照回音改,不推論沒下文。"""
    box = box or mailbox()[0]
    digests = [d for d in digests or [] if isinstance(d, dict)]

    def find(url, kind):
        hit = [d for d in digests if d.get('_asked') == url] or \
              [d for d in digests if '_asked' not in d and _same_page(url, str(d.get('url') or ''), kind)]
        return hit[-1] if hit else None

    def ok(item):
        ref, url = str(item.get('source_ref') or ''), str(item.get('url') or '')
        if not url:
            return False
        if ref.startswith('application_record:'):
            return not _digest_problem(find(url, 'record'), 'record', ref.split(':', 1)[1])
        if ref in ('email:search', 'email:search-more'):
            if not mailbox(url)[1]:
                return False
            page = find(url, 'search')
            if _digest_problem(page, 'search'):
                return False
            ids = _gmail_search_thread_ids({'anchors': [{'href': h} for h in page.get('links') or []]})
            return all(not _digest_problem(find(_thread_url(box, t), 'thread'), 'thread') for t in ids)
        if ref.startswith('email:'):
            return not _digest_problem(find(url, 'thread'), 'thread')
        return False

    out = set()
    for item in unavailable or []:
        if not ok(item):
            out.update(item.get('jobs') or [])
    return out


def reread_fallback(unavailable, board=None, reader=None, max_mails=MAX_MAILS, texts=None):
    """Codex:agent 補查完,程式自己把補查清單上的網頁再讀一次(它可能剛登入好了),算成摘要給 unverified_cards。
    讀不到的就沒有摘要,那幾張卡照樣不推論沒下文。「超過每輪上限」那一條不重讀(本來就讀不完)。
    texts:給了就把程式讀到的全文記進去({來源代號: 全文}),安檢門拿它核對 agent 補查交回的回音。"""
    box = mailbox()[0]
    got = []
    refs = {it['url']: str(it.get('source_ref') or '') for it in unavailable or [] if it.get('url')}

    def read(urls, **kw):
        try:
            pages = _read_pages(urls, board, reader, **kw)
        except Exception as e:  # noqa: BLE001 — 各家門路丟的例外不一樣;原因照實印進這一輪的紀錄,這批當成沒讀到
            print(f'程式核實補查來源讀取失敗:{str(e)[:120]}')
            return {}
        got.extend(dict(_digest(pages.get(u) or {}), _asked=u) for u in urls)
        if texts is not None:
            for u in urls:
                text = str((pages.get(u) or {}).get('text') or '')
                th = (urllib.parse.parse_qs(urllib.parse.urlsplit(u).query).get('th') or [''])[0]
                ref = f'email:{th}' if th else refs.get(u, '')
                if not (th or ref.startswith('application_record:')):
                    continue                              # 搜尋頁本身不是一封信
                if text and not _digest_problem(_digest(pages.get(u) or {}),
                                                        'thread' if th else 'record', ref.split(':', 1)[-1]):
                    texts[ref] = text
        return pages

    items = [it for it in unavailable or [] if it.get('url') and it.get('source_ref') != 'email:search-more']
    threads = [it['url'] for it in items if str(it.get('source_ref')).startswith('email:')
               and it.get('source_ref') != 'email:search']
    for it in items:
        if it.get('source_ref') == 'email:search' and mailbox(it['url'])[1]:
            page = read([it['url']], ready=_gmail_search_ready, settle=2).get(it['url']) or {}
            ids = _gmail_search_thread_ids(page)
            if not _gmail_search_problem(page) and len(ids) <= max_mails:
                threads += [_thread_url(box, t) for t in ids]
    threads = list(dict.fromkeys(threads))
    if threads:
        read(threads, ready=_gmail_thread_ready, settle=2)
    records = [it['url'] for it in items if str(it.get('source_ref')).startswith('application_record:')]
    if records:
        read(records, ready=_page_ready)
    return got


def claude_verified_reads(log):
    """只用 Claude:它在每一頁跑 VERIFY_FN 的結果(從那一輪的紀錄拿工具的回傳)。"""
    import apply_tab
    return [d for d in apply_tab.self_reads(log, _VERIFY_WHOLE) if isinstance(d, dict)]


def collect_sources(fb, jobs, urls, board=None, reader=None, max_mails=MAX_MAILS):
    """由程式複製一輪郵件全文與已登記的平台應徵紀錄,回傳文字和需補查來源。"""
    import profile_sync as ps
    box_url, is_gmail = mailbox()
    query, companies = gmail_query(fb, jobs, urls) if is_gmail else ('', [])
    result = {'gmail': {'query': query, 'companies': companies, 'threads': []},
              'application_records': []}
    inaccessible = []
    if not is_gmail and urls:
        inaccessible.append({'source_ref': 'email:search', 'source_type': 'email', 'url': box_url,
                             'source': '信箱 ' + (urllib.parse.urlsplit(box_url).hostname or box_url),
                             'reason': '設定的信箱不是 Gmail,程式讀不懂它的畫面',
                             'need': '用 agent 專用 Chrome 打開這個信箱,搜尋這些卡片公司的來信,原文連結記錄該封信的網址',
                             'jobs': list(urls)})
    unsearchable = [url for url in urls if is_gmail and not _gmail_terms(_company(jobs, url))]
    searchable_urls = [url for url in urls if url not in unsearchable]
    thread_ids = []
    if unsearchable:
        inaccessible.append({'source_ref': 'email:search', 'source_type': 'email',
                             'source': 'Gmail 公司搜尋',
                             'reason': '這些卡片的公司名稱沒有可安全用於 Gmail 的搜尋詞',
                             'need': '用 agent 專用 Chrome 補查相關郵件,並記錄實際郵件來源',
                             'jobs': unsearchable})
    if query:
        search_url = box_url + '#search/' + urllib.parse.quote(query, safe='')
        try:
            search_page = _read_pages([search_url], board, reader,
                                      ready=_gmail_search_ready, settle=2).get(search_url) or {}
        except Exception as e:  # noqa: BLE001 — 原因照實記進 search_error,跟著結果回報
            search_page = {}
            search_error = f'Gmail 搜尋頁讀取失敗:{str(e)[:120]}'
        else:
            search_error = (_gmail_page_problem(search_page, search_url)
                            or _gmail_search_problem(search_page))
        if search_error:
            inaccessible.append({'source_ref': 'email:search', 'source_type': 'email', 'url': search_url,
                                 'source': 'Gmail', 'reason': search_error,
                                 'need': '用 agent 專用 Chrome 補查 Gmail,並記錄查到的郵件來源',
                                 'jobs': searchable_urls})
        else:
            thread_ids = _gmail_search_thread_ids(search_page)
            if len(thread_ids) > max_mails:
                inaccessible.append({'source_ref': 'email:search-more', 'source_type': 'email', 'url': search_url,
                                     'source': 'Gmail 搜尋結果',
                                     'reason': f'搜尋結果超過每輪 {max_mails} 封上限,仍有郵件未讀',
                                     'need': '用 agent 專用 Chrome 補查剩餘郵件',
                                     'jobs': searchable_urls})
            thread_ids = thread_ids[:max_mails]

    record_pages = ps.application_record_pages()
    relevant_platforms = {ps.profile_key(url) for url in urls}
    record_pages = [item for item in record_pages if item.get('platform') in relevant_platforms]
    read_specs = [(f'{box_url}?ui=2&view=pt&search=all&th={thread_id}', 'email', thread_id,
                   f'email:{thread_id}')
                  for thread_id in thread_ids]
    read_specs.extend((item['url'], 'application_record', item['platform'],
                       f'application_record:{item["platform"]}') for item in record_pages)
    page_results = {}
    page_errors = {}
    for source_type in ('email', 'application_record'):
        specs = [spec for spec in read_specs if spec[1] == source_type]
        if not specs:
            continue
        targets = [spec[0] for spec in specs]
        try:
            page_results.update(_read_pages(
                targets, board, reader,
                ready=_gmail_thread_ready if source_type == 'email' else _page_ready,
                settle=2 if source_type == 'email' else 0,
            ))
        except Exception as e:  # noqa: BLE001 — 原因照實記進每一頁的錯誤,跟著結果回報
            error = f'來源頁讀取失敗:{str(e)[:120]}'
            page_errors.update({target: error for target in targets})
    for source_url, source_type, source_id, source_ref in read_specs:
        page = page_results.get(source_url) or {}
        problem = page_errors.get(source_url) or (
            _gmail_page_problem(page, source_url) if source_type == 'email'
            else _page_problem(page)
        )
        if not problem and source_type == 'email':
            problem = _gmail_thread_problem(page)
        if not problem and source_type == 'application_record':
            final_url = page.get('url') or source_url
            if not ps.same_platform_url(final_url, source_id):
                problem = '平台應徵紀錄讀取後跳到其他網站'
        if problem:
            source_label = ('Gmail 郵件 ' + source_id if source_type == 'email'
                            else str(source_id) + ' 應徵紀錄')
            affected = (searchable_urls if source_type == 'email' else
                        [url for url in urls if ps.profile_key(url) == source_id])
            inaccessible.append({'source_ref': source_ref, 'source_type': source_type,
                                 'url': source_url, 'source': source_label, 'reason': problem,
                                 'need': '用 agent 專用 Chrome 補查此來源,並記錄查到的來源網址',
                                 'jobs': affected})
            continue
        record = {'source_type': source_type, 'source_id': source_id, 'source_ref': source_ref,
                  'url': source_url, 'title': page.get('title') or '',
                  'text': page.get('text') or ''}
        if source_type == 'email':
            result['gmail']['threads'].append(record)
        else:
            result['application_records'].append(record)
    return result, inaccessible


def _key(it):
    """同一則回音認得出來:程式驗過的來源(email:{對話}、application_record:{平台})+ 種類 + 日期;
    沒驗過來源的才用原文連結。agent 自己寫的來源名稱、標題每輪可能換寫法,不放進來,
    不然他按「不對,復原」之後換個寫法又被改回去;種類要放進來,同一串信同一天的確認和拒絕才分得開。"""
    ref = it.get('source_ref') if it.get('source_type') in ('email', 'application_record') else ''
    return hashlib.sha256(json.dumps([ref or '', '' if ref else it.get('link') or '', it.get('kind'), it.get('date')],
                                  ensure_ascii=False).encode()).hexdigest()[:10]


def _source_type(value):
    source = str(value or '').casefold()
    if any(word in source for word in ('gmail', 'email', 'mail', '信箱', '郵件')):
        return 'email'
    if any(word in source for word in ('application', '應徵紀錄', '104')):
        return 'application_record'
    return 'fallback'


def apply_findings(fb, found, day=None):
    """把 agent 的回音證據併進看板,照證據改狀態。回 {url: 做了什麼}。"""
    day = day or today()
    done = {}
    shared = _shared_letters(found)
    for url, items in (found or {}).items():
        m = fb.get(url)
        # 卡清單是這一輪開頭定的,跑的時候他可能把卡移除了:已移除的不再碰(跟 waiting() 同一條)
        if not isinstance(m, dict) or m.get('app') != 'sent' or m.get('rm'):
            continue
        rp = m.setdefault('replies', {'items': []})
        # 已存的照欄位重算身分再比,不看存著的 id:舊版的 id 是用舊算法算的;存著的 id 不動,自動改狀態的 by 還指得到
        by_id = {_key(x): x for x in rp['items'] if isinstance(x, dict)}
        new = []
        enriched = False
        evidence_confirmed = False
        for it in items or []:
            it.setdefault('source_type', _source_type(it.get('src')))
            others = sorted(shared.get(_key(it), set()) - {url})
            evidence_confirmed = evidence_confirmed or (
                not others and it.get('_source_verified')
                and it.get('source_type') in ('email', 'application_record')
                and it.get('kind') in KINDS
            )
            it = {k: it.get(k) for k in
                  ('src', 'source_type', 'source_ref', 'date', 'subject', 'snippet', 'link', 'kind',
                   'todo', 'reason', 'quote', 'unverified') if it.get(k)}
            if it.get('kind') != 'reject':
                it.pop('reason', None)
            it['id'] = _key(it)
            if others:
                it['maybe'] = others              # 同一封也對到這幾張:分不出是哪一張,不自動改,卡上給他按
            existing = by_id.get(it['id'])
            if existing:
                if (it.get('kind') == 'reject' and it.get('reason')
                        and existing.get('kind') == 'reject' and not existing.get('reason')):
                    existing['reason'] = it['reason']
                    enriched = True
                continue
            rp['items'].append(it)
            new.append(it)
            by_id[it['id']] = it
        ev_cleared = bool(evidence_confirmed and m.pop('ev', None))
        if not new and not enriched and not ev_cleared:
            continue
        what = []
        maybe = [x for x in new if x.get('maybe')]
        if len(new) > len(maybe):
            what.append(f'新回音 {len(new) - len(maybe)} 則')
        if maybe:
            what.append(f'可能是同一封回音 {len(maybe)} 則,沒自動改')
        if enriched:
            what.append('補上拒絕理由')
        if ev_cleared:
            what.append('已找到確認信或平台應徵紀錄')
        decisive = sorted([x for x in new if x.get('kind') in KIND_TO_OC and not x.get('maybe')],
                          key=lambda x: x.get('date') or '')
        cur = m.get('oc') or ''
        steps = [(KIND_TO_OC[x['kind']], x) for x in decisive]
        # 沒下文的前提是「查過、一則回音都沒有」。程式自己記的沒下文,後來收到確認信(「還在審」)也是回音,
        # 照回音改回等回音(GLOSSARY「沒下文」)。他自己按的沒下文(沒有 oc_auto)是他的決定,不動
        if not steps and cur == 'ghost' and (m.get('oc_auto') or {}).get('s') == 'ghost':
            steps = [('', x) for x in new if x.get('kind') == 'confirm' and not x.get('maybe')][-1:]
        if steps and cur != 'wd':                     # 「我不去了」是他的決定,不自動改
            at0 = dict(m.get('oc_at') or {})
            for s, x in steps:                        # 照日期一則一則走,保留中途狀態
                set_outcome(m, s, (x.get('date') or day)[:10])
            s = m.get('oc') or ''
            if s != cur:
                m['oc_auto'] = {'s': s, 'from': cur, 'at': day, 'by': steps[-1][1]['id']}
                if at0:                               # 看板「不對,復原」原封放回改之前的日期,不照 from 重算
                    m['oc_auto']['from_at'] = at0
                what.append(f'{OC_WORD[cur]} → {OC_WORD[s]}')
        if any(x.get('todo') for x in new):
            what.append('有要你做的事')
        done[url] = ';'.join(what)
    return done


def _shared_letters(found):
    """同一封信(同一個來源 + 種類 + 日期)被掛到好幾張卡:{回音身分: {卡}},只留對到兩張以上的。
    只看信和 agent 補查的來源;平台應徵紀錄頁整頁是同一個來源,一張卡一筆,不是同一封。
    確認信不算:一封信確認好幾份申請是常有的,而且確認信不改結果。"""
    cards = {}
    for url, items in (found or {}).items():
        for it in items or []:
            kind = it.get('source_type') or _source_type(it.get('src'))
            if it.get('kind') in KIND_TO_OC and kind != 'application_record':
                cards.setdefault(_key(dict(it, source_type=kind)), set()).add(url)
    return {k: v for k, v in cards.items() if len(v) > 1}


def apply_checked_evidence(fb, checked):
    """完成來源檢查仍沒有郵件或平台紀錄時,更新送出頁弱證據的查找狀態。"""
    updated = []
    for url in checked or []:
        item = fb.get(url)
        if isinstance(item, dict) and item.get('app') == 'sent' and not item.get('rm') and item.get('ev'):
            item['ev'] = '已查信箱與可讀平台應徵紀錄,仍未找到確認信或平台紀錄;目前只有送出頁證據'
            updated.append(url)
    return updated


def summary(res, jobs):
    """看板那一行:講人話,不列網址。"""
    if not res:
        return '沒有新回音'
    def name(url):
        return card.name(jobs.get(url) or {})[:24] or url.split('/')[2]
    # 取有「→」的那一段:前面可能還有「補上拒絕理由」「已找到確認信」,不能固定取第幾段
    moved = [f'{name(url)} {next(p for p in what.split(";") if "→" in p)}' for url, what in res.items() if '→' in what]
    todo = [name(url) for url, what in res.items() if '要你做' in what]
    maybe = [name(url) for url, what in res.items() if '可能是同一封' in what]
    out = f'{len(res)} 張有新回音'
    if moved:
        out += ';改了狀態:' + '、'.join(moved)
    if todo:
        out += ';要你做的:' + '、'.join(todo)
    if maybe:
        out += ';可能是同一封回音、分不出是哪一張,沒自動改,在卡上自己按結果:' + '、'.join(maybe)
    return out


def apply_ghost(fb, day=None, checked=None):
    """只有本輪確認過的卡,送出滿 GHOST_DAYS 天且沒有回音才記成沒下文。"""
    day = day or today()
    checked = set(checked or [])
    d0 = datetime.date.fromisoformat(day)
    out = []
    for u in waiting(fb):
        if u not in checked:
            continue
        m = fb[u]
        if (m.get('oc') or '') or (m.get('replies') or {}).get('items') or not m.get('sent_at') or m.get('ghost_no'):
            continue
        try:
            age = (d0 - datetime.date.fromisoformat(str(m['sent_at'])[:10])).days
        except ValueError:
            continue
        if age >= GHOST_DAYS:
            m['oc_auto'] = {'s': 'ghost', 'from': '', 'at': day, 'by': f'送出 {age} 天沒有回音'}
            set_outcome(m, 'ghost', day)
            out.append(u)
    return out


def apply_results(fb, findings, checked, day=None, unverified=()):
    """套用本輪回音;只替來源完整查完的卡保存查過日期。
    unverified:補查來源程式沒核實讀完的卡(unverified_cards):照樣算查過(查過日期往前推,下一輪搜尋範圍才會縮),
    只是不推論沒下文。不然搜尋結果一頁放不下的那種,每一輪都從送出日搜起、永遠放不下。"""
    day = day or today()
    checked = set(checked or [])
    result = apply_findings(fb, findings, day)
    for url in checked:
        item = fb.get(url)
        if not isinstance(item, dict) or item.get('app') != 'sent' or item.get('rm'):
            continue
        replies = item.setdefault('replies', {'items': []})
        replies['at'] = day
    weak_evidence = apply_checked_evidence(fb, checked)
    ghosts = apply_ghost(fb, day=day, checked=checked - set(unverified or ()))
    return result, ghosts, weak_evidence


PROMPT = """你是查回音的 agent。程式已經搜尋信箱並把可讀來源全文複製到 {source_file};通常只要分析這份文字,不要開瀏覽器、搜尋網路或查看其他來源。
只有程式列出的「需要補查來源」可以用 agent 專用 Chrome 嘗試讀取;只查列出的來源,並照抄該項 source_ref,把實際來源名稱和網址記錄在 finding.source/link。不要碰使用者本人的 Chrome。

每張卡附網址、卡名、送出或上次查回音的日期。用職缺名稱、公司和對話內容確認回音屬於哪張卡,只回傳清單裡的卡片網址。逐卡套用自己的上次查詢日期,忽略更早的郵件。

卡片:
{cards}

需要補查來源:
{fallback_sources}

請分析複製的郵件全文和平台應徵紀錄頁全文:
1. 回音要給卡片網址、source_ref、來源、日期、摘要、原文連結、種類、信裡那一句和待辦。source_ref 必須照抄來源全文中的值,或照抄補查清單的值;來源是信還是平台紀錄程式照 source_ref 自己認,不用寫。kind 只能是 confirm(確認收到或仍在處理)、reject(沒錄取)、interview(面試、測驗或作業邀請)、offer(錄取)。quote 逐字抄信裡(或紀錄上)決定是這一種的那一句。待辦只記對方要求本人完成且尚未完成的事。同一家有好幾張卡、一封信看不出是哪一張時,不要自己挑一張:每張可能的卡各給一則(同一個 source_ref、同一個原文連結),程式會在卡上標「可能是這封」讓本人判斷。
2. 讀平台應徵紀錄頁時,將能確認已投遞的記為 confirm(source_ref 照抄那一頁的);每筆應徵紀錄放進 job_ids:有職缺連結就照抄連結,沒有就給平台名稱和頁面上的職缺代號(逐字照抄),程式只用平台+代號比對。
3. 只有檢查完信箱搜尋全文和該卡可用的應徵紀錄來源,且沒有未解的存取問題,才把卡列入 checked。沒有找到回音也可列入 checked。
4. 無法進入的來源要列 source、reason、need 和受影響卡片網址;影響範圍不明時不要把任何卡放進 checked。

輸出 JSON 檔 {out},格式如下:
{{
  "checked": ["本輪已完成來源檢查、沒有未解存取問題的卡片網址"],
  "findings": [{{"url":"卡片網址","source_ref":"複製來源或補查來源中的識別值","source":"來源名稱","date":"YYYY-MM-DD","summary":"重點,200字內","link":"原文連結","kind":"confirm|reject|interview|offer","quote":"信裡決定是這一種的那一句,逐字抄","reason":"拒絕信明確寫出的理由原文;制式信留空","todo":"尚未完成的待辦,沒有填空字串"}}],
  "job_ids": [{{"url":"應徵紀錄上的職缺連結,沒有就空字串","platform":"平台,例:104、linkedin","id":"頁面上的職缺代號","applied_at":"YYYY-MM-DD","title":"方便人閱讀,不參與配對"}}],
  "inaccessible": [{{"source":"來源","reason":"進不去的原因","need":"本人需要做什麼","jobs":["受影響的卡片網址"]}}]
}}

程式會拿日期、原文連結、quote、拒絕理由、應徵紀錄的代號跟它複製的全文比,對不上的那一則不收、那張卡這一輪不算查完。
不要直接改看板或呼叫 agent_report.py;程式會記錄回報。只寫這份 JSON 檔,其他檔案不要改,最後一行印 @@DONE@@。
"""


def self_read_rule():
    """只用 Claude 補查時接在 prompt 後面:每一頁跑一次程式給的唯讀函式,程式從紀錄核實來源真的讀完(#289 決定 2)。"""
    import apply_tab
    return ('【讓程式核實你真的讀完】補查的每一頁(補查清單給的 Gmail 搜尋頁 url、搜尋結果裡每一封信、平台應徵紀錄頁)打開、'
            '載完之後,在那一頁用 javascript_tool 跑一次下面這支唯讀函式;程式只靠它的回傳確認來源讀完了,'
            '沒跑、沒讀完的來源影響到的卡,這一輪不會推論沒下文。回傳超過 1000 字會被截掉,所以分段:\n'
            + apply_tab.self_read_steps(_VERIFY_WHOLE, '')
            + 'Gmail:打開補查清單給的 url,不要自己改搜尋字。搜尋頁那次回傳的 links 裡每一封信(連結最後一段是信的代號),'
            f'都要打開 {mailbox()[0]}?ui=2&view=pt&search=all&th=信的代號 這個列印檢視讀全文,每一封各跑一次上面兩步。\n')


def prompt_for(fb, jobs, urls, out, source_file='(預覽時尚未擷取)', inaccessible=None, self_read=False):
    cards = '\n'.join(
        f'- {url} | {card.name(jobs.get(url) or {})} | {since_of(fb, url) or "?"} 起'
        for url in urls
    )
    fallback = json.dumps(inaccessible or [], ensure_ascii=False, indent=2) if inaccessible else '(沒有;不要開瀏覽器)'
    return PROMPT.format(cards=cards, out=out, source_file=source_file, fallback_sources=fallback) + (
        '\n' + self_read_rule() if self_read and inaccessible else '')


def source_texts(sources):
    """程式複製的來源全文:{來源代號: 全文}(信、平台應徵紀錄頁)。"""
    records = list(((sources or {}).get('gmail') or {}).get('threads') or [])
    records += list((sources or {}).get('application_records') or [])
    return {r['source_ref']: str(r.get('text') or '') for r in records
            if isinstance(r, dict) and r.get('source_ref') and r.get('text')}


def _source_type_index(sources, inaccessible):
    index = {}
    records = list((sources.get('gmail') or {}).get('threads') or [])
    records += list(sources.get('application_records') or [])
    for record in records:
        ref, kind = record.get('source_ref'), record.get('source_type')
        if ref and kind in ('email', 'application_record'):
            index[ref] = kind
    for item in inaccessible or []:
        ref, kind = item.get('source_ref'), item.get('source_type')
        if ref and kind == 'email' and ref in ('email:search', 'email:search-more'):
            index[ref] = kind
        elif ref and item.get('url') and kind in ('email', 'application_record'):
            index[ref] = kind
    return index


def _iso_date(value):
    value = str(value or '').strip()
    try:
        return datetime.date.fromisoformat(value[:10]).isoformat()
    except ValueError as e:
        raise ValueError(f'日期不是 YYYY-MM-DD: {value}') from e


def parse_result(data, urls, source_types=None, since_dates=None, texts=None):
    """交件單經安檢門(gate.inspect):每一則回音、每一筆應徵紀錄跟程式複製的來源全文比(texts {來源代號: 全文})。
    整份的形狀不對才整批不收;一則對不上(或格式不對)只丟那一則,那張卡這一輪不算查完(不推論沒下文、查過日期不往前推),
    看不出是哪張卡的就每張都不算查完。程式沒讀到全文的來源(agent 補查的)核對不了:那一則標 unverified,卡上照實講。
    平台應徵紀錄的代號核對不了就不收(不拿 agent 的話把卡標成已投遞)。"""
    import gate
    if not isinstance(data, dict) or not isinstance(data.get('checked'), list):
        raise ValueError('缺少 checked 卡片清單')
    for key in ('findings', 'job_ids', 'inaccessible'):
        if not isinstance(data.get(key, []), list):
            raise ValueError(f'{key} 必須是陣列')
    data = dict(data, job_ids=[{'id': r} if isinstance(r, str) else r for r in data.get('job_ids', [])])

    allowed = set(urls)
    texts = dict(texts or {})
    verdict = gate.inspect('reply', data, gate.Truth(cards=allowed, source_types=source_types, texts=texts))
    box = mailbox()
    checked = {str(url) for url in data['checked'] if str(url) in allowed}
    found, dropped, unsure = {}, [p for p in verdict.problems if '「查完的卡」' in p], set()
    shape = [p for p in verdict.problems if '不是物件' in p]
    if shape:
        dropped += shape
        unsure.add('*')
    for row in verdict.rows.get('findings', []):
        url = row.key
        if url not in allowed:                           # 清單外的卡不能改狀態(安檢門已經記下這一則對不上)
            dropped += row.problems
            continue
        try:
            if not row.ok:
                raise ValueError('；'.join(row.problems))
            item = _finding(row.row, url, source_types, since_dates, box)
        except ValueError as e:
            dropped.append(str(e)[:300])
            unsure.add(url)
            continue
        if item:
            if not texts.get(resolved_ref(row.row)):
                item['unverified'] = True                # agent 補查、程式沒讀到原文:卡上照實講
            found.setdefault(url, []).append(item)

    job_ids = []
    for row in verdict.rows.get('job_ids', []):
        try:
            if not row.ok:
                raise ValueError('；'.join(row.problems))
            if not any(k in row.facts for k in ('id', 'url')):
                continue                                  # 程式沒讀到應徵紀錄全文:核對不了,不拿來標已投遞
            rec = _job_id(dict(row.facts, title=row.row.get('title')))
        except ValueError as e:
            dropped.append(str(e)[:300])
            continue
        if rec:
            job_ids.append(rec)

    inaccessible = []
    for row in verdict.rows.get('inaccessible', []):
        jobs = row.row.get('jobs', [])
        affected = [str(url) for url in jobs if str(url) in allowed] if isinstance(jobs, list) else []
        try:
            if not row.ok:
                raise ValueError('；'.join(row.problems))
            source = str(row.row.get('source') or '').strip()
            reason = str(row.row.get('reason') or '').strip()
            need = str(row.row.get('need') or '').strip()
            if not isinstance(jobs, list):
                raise ValueError('inaccessible.jobs 必須是陣列')
            if not source or not reason or not need:
                raise ValueError('無法讀取的來源必須包含來源、原因和本人待辦')
        except ValueError as e:
            dropped.append(str(e)[:300])
            unsure.update(affected or ['*'])
            continue
        if affected:
            checked.difference_update(affected)
        else:
            checked.clear()                            # 未知影響範圍時不對任何卡推斷沒下文
        inaccessible.append({'source': source[:100], 'reason': reason[:240],
                             'need': need[:240], 'jobs': affected})

    if '*' in unsure:
        checked.clear()
    checked.difference_update(unsure)
    for url in checked:
        found.setdefault(url, [])
    for url in list(found):
        if url not in checked and not found[url]:
            del found[url]
    return EchoReadResult(found, checked, job_ids, inaccessible, dropped)


SEARCH_REFS = ('email:search', 'email:search-more')   # agent 補查信箱時的來源代號(程式沒先複製那一封)


def resolved_ref(row, box=None):
    """一則回音對到哪一個來源:照抄的 source_ref;補查信箱的照原文連結對到那一封(email:<代號>),對不到回原樣。
    安檢門核對原文(gate)和整理成看板格式(_finding)都用這一條。"""
    ref = str(row.get('source_ref') or '').strip()
    if ref in SEARCH_REFS:
        thread = gmail_message_id(row.get('link')) or mailbox_message_ref(row.get('link'), box)
        return f'email:{thread}' if thread else ref
    return ref


def _finding(row, url, source_types, since_dates, box):
    """一則回音驗過、整理成看板格式;比這張卡上次查過還舊的回 None;格式不對丟 ValueError。"""
    kind = str(row.get('kind') or '').strip()
    source = str(row.get('source') or '').strip()
    summary = str(row.get('summary') or '').strip()
    link = str(row.get('link') or '').strip()
    if kind not in KINDS or not source or not summary or not link:
        raise ValueError(f'回音資料缺種類、來源、摘要或原文連結: {url}')
    date = _iso_date(row.get('date'))
    since = str((since_dates or {}).get(url) or '')[:10]
    if since and date < since:
        return None
    source_ref = str(row.get('source_ref') or '').strip()
    trusted_source_type = (source_types or {}).get(source_ref, 'fallback')
    source_verified = bool(source_ref and source_ref in (source_types or {}))
    if trusted_source_type == 'email' and source_ref in SEARCH_REFS:
        resolved = resolved_ref(row, box)
        if resolved != source_ref:
            source_ref = resolved
        else:
            trusted_source_type = 'fallback'
            source_verified = False
    item = {
        'src': source[:80],
        'date': date,
        'subject': str(row.get('subject') or '').strip()[:160],
        'snippet': summary[:200],
        'link': link,
        'kind': kind,
        'source_ref': source_ref,
        'source_type': trusted_source_type,
        '_source_verified': source_verified,
    }
    reason = row.get('reason')
    if kind == 'reject' and reason is not None and not isinstance(reason, str):
        raise ValueError(f'拒絕理由必須是文字: {url}')
    if kind == 'reject' and isinstance(reason, str) and reason.strip():
        item['reason'] = reason
    todo = str(row.get('todo') or '').strip()
    if todo:
        item['todo'] = todo[:240]
    quote = str(row.get('quote') or '').strip()
    if quote:
        item['quote'] = quote[:240]          # 信算哪一種是 agent 判斷:卡上附它抄的那一句
    return item


def _job_id(row):
    """平台應徵紀錄上的一筆;沒有代號也沒有連結的回 None;格式不對丟 ValueError。"""
    if isinstance(row, str):
        row = {'id': row}
    if not isinstance(row, dict):
        raise ValueError('job_ids 裡有非物件資料')
    jid = str(row.get('id') or '').strip()
    link = str(row.get('url') or '').strip()
    if not jid and not link:
        return None
    rec = {'id': jid, 'title': str(row.get('title') or '').strip()[:160]}
    if link:
        rec['url'] = link[:500]
    if str(row.get('platform') or '').strip():
        rec['platform'] = str(row['platform']).strip()[:40]
    if row.get('applied_at'):
        rec['applied_at'] = _iso_date(row['applied_at'])
    return rec


def preview(board=None):
    """顯示會交給 agent 的查回音 prompt;不連網。"""
    board = board or bd.LIVE
    jobs, fb = load(board)
    urls = waiting(fb)
    if not urls:
        return '現在沒有在等回音的卡。'
    return ar.rules_for('main', board=board, web=False) + prompt_for(
        fb, jobs, urls, os.path.join(SP, 'replies.json'))


# 跑不成時叫他去的地方:「📮 已投出」最上面那一列沒跑成時有「看紀錄」
RETRY_NEED = '在「📮 已投出」最上面按「看紀錄」看過,再按一次「📬 查應徵進度」'


def _report_inaccessible(items, board):
    """同一個來源進不去只報一則:等回音的卡可能幾十張,一張一則會變成幾十則都叫他登入同一個信箱。"""
    for item in items:
        jobs = item['jobs']
        agent_report.report(
            '查回音', f'{item["source"]} 無法進入:{item["reason"]}' + (f'(影響 {len(jobs)} 張卡)' if len(jobs) > 1 else ''),
            need=item['need'], job=jobs[0] if len(jobs) == 1 else '', live=board
        )


def _copy_only_agent_id():
    """Copy-only analysis requires Codex so browser plugins and web search can both stay off."""
    agents = ((cf.C.get('agent') or {}).get('agents') or [])
    agent = next((item for item in agents if item.get('runtime') == 'codex'), None)
    return agent.get('id') if agent else None




def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--board', default=bd.LIVE)
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--no-agent', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--url', help='只查這一張')
    ap.add_argument('--limit', type=int, default=0, help='這一輪最多查幾張')
    a = ap.parse_args()
    board = os.path.abspath(a.board)
    st = os.path.join(SP, STATUS)
    base = {'t0': time.time(), 'pid': os.getpid()}
    jobs, fb = load(board)
    urls = everyone = waiting(fb)
    if a.url:
        urls = [url for url in urls if url == a.url]
    if a.limit:
        urls = urls[:a.limit]
    if not urls:
        print('現在沒有在等回音的卡。')
        jobrun.write(st, dict(base, phase='nothing', n=0, done=0, msg='現在沒有在等回音的卡'))
        return 0

    out = os.path.join(SP, 'replies.json')
    if a.dry:
        print(ar.rules_for('main', board=board, web=False) + prompt_for(fb, jobs, urls, out))
        return 0
    if a.no_agent:
        msg = '測試副本未查外部來源'
        jobrun.write(st, dict(base, phase='done', n=len(urls), done=0, msg=msg,
                              finished_at=time.time()))
        print(msg)
        return 0

    jobrun.write(st, dict(base, phase='run', n=len(urls), done=0))
    started = datetime.datetime.now().isoformat(timespec='seconds')
    source_file = os.path.join(SP, f'reply-sources-{os.getpid()}.json')
    # 收尾(刪複製的來源檔、關 agent 的 Chrome)出錯不等於 agent 沒做完:記下來、寫進這一輪的訊息,
    # agent 交的件照樣讀。以前在這裡改丟例外,整輪結果被丟掉,還說「agent 沒完成」
    cleanup_errors = []
    verified = []
    rnd = None
    try:
        import agent_chrome, chrome_door
        # 查應徵進度跟其他工作一樣,交給設定裡用 Chrome 的那一家。程式自己讀得到的那一家先由程式讀;
        # 讀不到的那一家(Claude)每個來源都交給它在 agent 的 Chrome 裡讀,程式從紀錄核實。agent 的 Chrome 照樣在背景藏著開
        door = chrome_door.current()
        up, msg, need = door.ready(board) if door else (False, chrome_door.NO_BROWSER_AGENT, '勾一個「用它操作 Chrome」的 agent')
        program_reads = bool(door and door.program_reads)
        if not up:
            agent_report.report('查回音', msg, live=board, need='到「⚙ 設定 → 🤖 Agent 與瀏覽器」' + need)
            jobrun.write(st, dict(base, phase='failed', n=len(urls), done=0, msg=msg,
                                  finished_at=time.time()))
            print(msg)
            return 1
        try:
            sources, program_unavailable = collect_sources(fb, jobs, urls, board)
            texts = source_texts(sources)     # 安檢門拿程式複製的全文核對 agent 交回的每一則
            os.makedirs(os.path.dirname(source_file), exist_ok=True)
            with open(source_file, 'w', encoding='utf-8') as f:
                json.dump(sources, f, ensure_ascii=False)
            prompt = prompt_for(fb, jobs, urls, out, source_file, program_unavailable, self_read=not program_reads)
            if os.path.exists(out):
                os.remove(out)
            log = os.path.join(SP, 'replies.log')
            copy_agent = _copy_only_agent_id()
            if not program_unavailable and not copy_agent:
                raise RuntimeError('查應徵進度需要 Codex agent 只讀分析程式複製的來源')
            overrides = ar.apply_overrides() if program_unavailable else ar.lean()
            # 證據(#315):這一輪查的每一張卡各記一份:程式讀到的來源、指示、動作紀錄、交件單
            with evidence.opened('reply', 'check', urls, board) as rnd:
                rnd.text('page', 'sources', sources, ext='.json', why='程式讀的來源')
                outcome = ar.run(prompt, log, cf.HOME, timeout=TIMEOUT,
                                 browser_required=bool(program_unavailable),
                                 browser=overrides + ['-c', 'tools.web_search=false'],
                                 board=board, web=False,
                                 agent_id=None if program_unavailable else copy_agent)
                rnd.handoff(out)
            # agent 補查過的來源,程式自己核實讀完了沒(要在收掉 agent 的 Chrome 之前):程式讀得到的那一家再讀一次,
            # 讀不到的那一家看它的紀錄
            if program_unavailable and outcome.ok:
                verified = (reread_fallback(program_unavailable, board, texts=texts) if program_reads
                            else claude_verified_reads(log))
        finally:
            try:
                os.remove(source_file)
            except FileNotFoundError:
                pass  # Collection may fail before creating the copied source file.
            except OSError as e:
                cleanup_errors.append(f'無法清除複製的查應徵進度來源檔({type(e).__name__})')
            try:
                agent_chrome.close_if_idle(board)      # 用 Claude 讀的那條也收:以前只有程式自己讀的會收,Chrome 一直開著
            except Exception as e:  # noqa: BLE001 — 收尾失敗照實記進 cleanup_errors 回報
                cleanup_errors.append(f'agent Chrome 收尾失敗({type(e).__name__})')
    except Exception as e:  # noqa: BLE001 — 這一輪最外層:原因照實寫進看板的回報與進度
        outcome = None
        error = f'查回音 agent 沒完成:{str(e)[-200:]}'
    if outcome is not None and not outcome.ok:
        error = '查回音 agent 沒完成:' + outcome.message()
    if cleanup_errors:
        print('收尾沒做完:' + '；'.join(cleanup_errors))
    if outcome is None or not outcome.ok:
        agent_report.report('查回音', error, need=RETRY_NEED, live=board)
        jobrun.write(st, dict(base, phase='failed', n=len(urls), done=0, msg=error,
                              finished_at=time.time()))
        print(error)
        return 1

    unverified = unverified_cards(program_unavailable, verified) & set(urls)
    try:
        import gate
        sheet, missing = gate.read(os.path.dirname(out), 'reply', where=out)   # 交件單只經安檢門讀
        if sheet is None:
            raise ValueError(missing)
        with (evidence.activated(rnd) if rnd is not None else contextlib.nullcontext()):   # 比對結果記進同一輪
            parsed = parse_result(
                sheet, urls,
                source_types=_source_type_index(sources, program_unavailable),
                since_dates={url: since_of(fb, url) for url in urls},
                texts=texts,
            )
    except Exception as e:  # noqa: BLE001 — agent 交的檔什麼樣子都有可能;原因照實寫進看板的回報
        error = f'查回音 agent 交件無法使用:{str(e)[:200]}'
        agent_report.report('查回音', error, need=RETRY_NEED, live=board)
        jobrun.write(st, dict(base, phase='failed', n=len(urls), done=0, msg=error,
                              finished_at=time.time()))
        print(error)
        return 1

    msgs = []
    _report_inaccessible(parsed.inaccessible, board)
    if parsed.job_ids:
        import sync_sent
        msg = sync_sent.sync(board, parsed.job_ids)
        if msg:
            msgs.append(msg)
            jobs, fb = load(board)

    result, ghosts, weak_evidence = {}, [], []
    def apply(fbx):
        applied, became_ghosts, updated_weak_evidence = apply_results(
            fbx, parsed.findings, parsed.checked, unverified=unverified
        )
        result.update(applied)
        ghosts.extend(became_ghosts)
        weak_evidence.extend(updated_weak_evidence)
    bd.set_fb(apply, live=board, by='reply_run')
    # 之前留下的查應徵進度回報:這一輪重查過的就過時了(問題還在的,這一輪上面已經再報一次,時間是這一輪的)。
    # 查了全部還在等的卡就全收;只查了幾張(「只查這一張」「跑幾張」)只收那幾張的,別張沒重查不能替它收
    why = '後來那一輪查應徵進度重查過了'
    if len(urls) == len(everyone):
        agent_report.resolve_from('查回音', why, started, live=board)
    elif parsed.checked:
        bd.set_fb(lambda f: [agent_report.apply_resolve(f, url, why, only=lambda it: str(it.get('from', '')).startswith('查回音')
                                                        and str(it.get('at', '')) < started) for url in parsed.checked],
                  live=board, by='agent_report')
    if result:
        msgs.append(summary(result, jobs))
    if weak_evidence:
        msgs.append(f'{len(weak_evidence)} 張已查信箱與可讀平台紀錄,仍只有送出頁證據')
    if ghosts:
        msgs.append(f'{len(ghosts)} 張送出超過 {GHOST_DAYS} 天,本輪確認沒有回音,記成沒下文')
    if parsed.inaccessible:
        msgs.append(f'{len(parsed.inaccessible)} 個來源進不去,已寫入「📣 agent 回報」')
    if unverified:
        msgs.append(f'{len(unverified)} 張靠 agent 補查、程式沒核實到來源讀完,這一輪不推論沒下文')
    if parsed.dropped:
        print('agent 交件這幾筆格式不對,先略過:\n' + '\n'.join(parsed.dropped))
        msgs.append(f'agent 交件有 {len(parsed.dropped)} 筆格式不對,先略過,那幾張下一輪再查')
    if cleanup_errors:
        msgs.append('收尾沒做完:' + '；'.join(cleanup_errors))
    missing = len(urls) - len(parsed.checked)
    phase = 'incomplete' if parsed.inaccessible or missing else 'done'
    message = '；'.join(msgs) or '沒有新回音'
    jobrun.write(st, dict(base, phase=phase, n=len(urls), done=len(parsed.checked), msg=message,
                          finished_at=time.time()))
    print(message)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
