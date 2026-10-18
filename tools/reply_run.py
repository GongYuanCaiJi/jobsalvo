"""
reply_run —— 「📬 查回音」:已投遞的卡,誰回了、回了什麼。

程式先在 agent 的瀏覽器讀信箱與平台應徵紀錄,讀不到的交給 agent 補查;agent 搜尋適合的來源,回傳回音證據、平台應徵紀錄上的職缺和無法讀取的來源。
程式只驗證交件形狀,再依證據更新看板、對帳應徵紀錄(sync_sent)、記錄待辦和無下文狀態。
"""
import contextlib, os, sys, json, time, argparse, datetime, hashlib, re, urllib.parse
from dataclasses import dataclass, field
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd
from delivery_state import set_outcome   # 跟看板的「記結果」事件同一支
import agent_run as ar
import evidence
import jobrun
import agent_report
import config as cf
import card
import delivery_state as ds

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


def waiting(fb):
    """已投遞、還沒結束的卡;面試中和 Offer 也要查後續回音。"""
    return [u for u, m in fb.items() if ds.app_of(m) == 'sent' and (m.get('oc') or '') not in ('rej', 'wd')]


def unsure_cards(fb):
    """送出結果不明、還在「可以投了」的卡:agent 按了送出卻沒看到成功頁。查應徵進度替他查到底送出沒有,不交給他。"""
    import delivery_state as ds
    return [u for u, m in fb.items() if isinstance(m, dict) and m.get('app') == 'ship' and not m.get('rm')
            and ds.state(m) == 'unsure']


def to_check(fb):
    """這一輪要查的卡:等回音的,加上送出結果不明的。"""
    return waiting(fb) + unsure_cards(fb)


def submit_tried_at(m):
    """送出結果不明的卡是什麼時候按的送出(ISO)。"""
    a = (m or {}).get('apply') or {}
    return str((a.get('submit_fail') or {}).get('at') or a.get('at') or '')


def since_of(fb, url):
    item = fb.get(url) or {}
    return str((item.get('replies') or {}).get('at') or item.get('sent_at') or submit_tried_at(item))[:10]


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
    if gmail_message_id(source_url) != gmail_message_id(final_url):
        return 'Gmail 讀到的是另一封信,不是要讀的那封'
    if _gmail_search_query(source_url) != _gmail_search_query(final_url):
        return 'Gmail 讀到的是另一個搜尋,不是要搜的那個'
    return ''


def _gmail_search_query(url):
    """網址裡 #search/ 後面的搜尋字;Gmail 載入後會把空白改寫成 +,兩邊都解碼再比。"""
    fragment = str(url or '').partition('#')[2]
    return urllib.parse.unquote_plus(fragment[len('search/'):]) if fragment.startswith('search/') else None


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
    '找不到與你的搜尋條件相符的郵件',     # Gmail 繁中介面實際的字(2026-10-05 用 ego 讀到的)
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


def _thread_links(page):
    """頁面上指向一封信的連結:真的連結,加上 Gmail 搜尋結果每一列的對話代號(列不是連結,讀頁程式另外收 emailThreads)。"""
    page = page or {}
    hrefs = page.get('links') or [a.get('href') for a in page.get('anchors') or [] if isinstance(a, dict)]
    m = re.match(r'https://mail\.google\.com/mail/u/\d+/', str(page.get('url') or ''))
    base = m.group(0) if m else GMAIL
    hrefs = list(hrefs) + [f'{base}#all/{t}' for t in page.get('emailThreads') or [] if isinstance(t, str)]
    return [h for h in hrefs if isinstance(h, str)]


def _gmail_search_thread_ids(page):
    return list(dict.fromkeys(thread_id for href in _thread_links(page) if (thread_id := gmail_message_id(href))))


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


# ---- 補查來源的核實(#289 決定 2):agent 說查過了不算數,程式自己再讀一次、核實過來源真的讀完,那幾張卡才推論沒下文 ----
def _thread_url(box, thread_id):
    return f'{box}?ui=2&view=pt&search=all&th={thread_id}'


def _source_problem(page, url, kind, platform=''):
    """程式自己讀到的整頁,這個來源讀完整了沒;沒問題回空字串。kind:search(Gmail 搜尋頁)、thread(一封信的列印檢視)、
    record(平台應徵紀錄頁,platform:哪個平台)。第一次複製來源、agent 補查後程式再讀一次,都照這一支判。"""
    if kind == 'record':
        import profile_sync as ps
        return _page_problem(page) or ('' if ps.same_platform_url((page or {}).get('url') or url, platform)
                                       else '平台應徵紀錄讀取後跳到其他網站')
    return _gmail_page_problem(page, url) or (_gmail_search_problem(page) if kind == 'search'
                                              else _gmail_thread_problem(page))


def unverified_cards(unavailable, pages, box=None):
    """補查清單上程式沒核實讀完的來源,影響到的卡(這一輪不推論沒下文、查過日期不往前推)。
    pages:程式自己再讀到的整頁 {網址: 頁面}。沒有網頁可核實的來源(不是 Gmail 的信箱、
    公司名沒有能搜的字)永遠算沒核實:那些卡只照回音改,不推論沒下文。"""
    box = box or mailbox()[0]
    pages = pages or {}

    def ok(item):
        ref, url = str(item.get('source_ref') or ''), str(item.get('url') or '')
        if not url:
            return False
        if ref.startswith('application_record:'):
            return not _source_problem(pages.get(url), url, 'record', ref.split(':', 1)[1])
        if ref in ('email:search', 'email:search-more'):
            if not mailbox(url)[1] or _source_problem(pages.get(url), url, 'search'):
                return False
            threads = [_thread_url(box, t) for t in _gmail_search_thread_ids(pages[url])]
            return all(not _source_problem(pages.get(t), t, 'thread') for t in threads)
        if ref.startswith('email:'):
            return not _source_problem(pages.get(url), url, 'thread')
        return False

    out = set()
    for item in unavailable or []:
        if not ok(item):
            out.update(item.get('jobs') or [])
    return out


def reread_fallback(unavailable, board=None, reader=None, max_mails=MAX_MAILS, texts=None):
    """agent 補查完,程式自己把補查清單上的網頁再讀一次(它可能剛登入好了),整頁交給 unverified_cards。
    讀不到的就沒有摘要,那幾張卡照樣不推論沒下文。「超過每輪上限」那一條不重讀(本來就讀不完)。
    texts:給了就把程式讀到的全文記進去({來源代號: 全文}),安檢門拿它核對 agent 補查交回的回音。"""
    box = mailbox()[0]
    got = {}
    refs = {it['url']: str(it.get('source_ref') or '') for it in unavailable or [] if it.get('url')}

    def read(urls, **kw):
        try:
            pages = _read_pages(urls, board, reader, **kw)
        except Exception as e:  # noqa: BLE001 — 各家門路丟的例外不一樣;原因照實印進這一輪的紀錄,這批當成沒讀到
            print(f'程式核實補查來源讀取失敗:{str(e)[:120]}')
            return {}
        got.update({u: pages.get(u) or {} for u in urls})
        if texts is not None:
            for u in urls:
                text = str((pages.get(u) or {}).get('text') or '')
                th = (urllib.parse.parse_qs(urllib.parse.urlsplit(u).query).get('th') or [''])[0]
                ref = f'email:{th}' if th else refs.get(u, '')
                if not (th or ref.startswith('application_record:')):
                    continue                              # 搜尋頁本身不是一封信
                if text and not _source_problem(pages.get(u), u, 'thread' if th else 'record', ref.split(':', 1)[-1]):
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


def _search(box_url, query, board=None, reader=None):
    """程式自己搜一次 Gmail:回 (搜尋頁網址, 對話代號, 沒讀完整的原因)。"""
    url = box_url + '#search/' + urllib.parse.quote(query, safe='')
    try:
        page = _read_pages([url], board, reader, ready=_gmail_search_ready, settle=2).get(url) or {}
    except Exception as e:  # noqa: BLE001 — 原因照實回給呼叫端,跟著結果回報
        return url, [], f'Gmail 搜尋頁讀取失敗:{str(e)[:120]}'
    return url, _gmail_search_thread_ids(page), _gmail_page_problem(page, url) or _gmail_search_problem(page)


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
        search_url, found, search_error = _search(box_url, query, board, reader)
        if search_error:
            inaccessible.append({'source_ref': 'email:search', 'source_type': 'email', 'url': search_url,
                                 'source': 'Gmail', 'reason': search_error,
                                 'need': '用 agent 專用 Chrome 補查 Gmail,並記錄查到的郵件來源',
                                 'jobs': searchable_urls})
        else:
            thread_ids = found
            if len(thread_ids) > max_mails:
                inaccessible.append({'source_ref': 'email:search-more', 'source_type': 'email', 'url': search_url,
                                     'source': 'Gmail 搜尋結果',
                                     'reason': f'搜尋結果超過每輪 {max_mails} 封上限,仍有郵件未讀',
                                     'need': '用 agent 專用 Chrome 補查剩餘郵件',
                                     'jobs': searchable_urls})
            thread_ids = thread_ids[:max_mails]
    # 送出結果不明的卡:照按送出那一秒搜(Gmail 的 after: 收秒數),搜得到的對話才可能是這一次投遞的證據
    for url in [u for u in unsure_cards(fb) if is_gmail and u in searchable_urls]:
        t = _local_time(submit_tried_at(fb[url]))
        if t is None:
            continue
        q = f'after:{int(t.timestamp())} (' + ' OR '.join(f'"{x}"' for x in _gmail_terms(_company(jobs, url))) + ')'
        _, found, problem = _search(box_url, q, board, reader)
        if not problem:                   # 讀不完整就沒有這張的名單,查到的信只能回報給他
            result['gmail'].setdefault('after', {})[url] = found

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
        problem = page_errors.get(source_url) or _source_problem(
            page, source_url, 'thread' if source_type == 'email' else 'record', source_id)
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
    if any(word in source for word in ('application', '應徵紀錄')):
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
        if ds.app_of(m) != 'sent':
            continue
        rp = m.setdefault('replies', {'items': []})
        # 已存的照欄位重算身分再比,不看存著的 id:舊版的 id 是用舊算法算的;存著的 id 不動,自動改狀態的 by 還指得到
        by_id = {_key(x): x for x in rp['items'] if isinstance(x, dict)}
        new = []
        enriched = False
        evidence_confirmed = False
        needs_confirmation = False
        for it in items or []:
            it.setdefault('source_type', _source_type(it.get('src')))
            others = sorted(shared.get(_key(it), set()) - {url})
            evidence_confirmed = evidence_confirmed or (
                not others and it.get('_source_verified') and not it.get('unverified')
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
                if existing.get('unverified') and not it.get('unverified'):
                    pending = existing.pop('effect_pending', False)
                    rejected = existing.get('effect_rejected')
                    old_id = existing.get('id')
                    existing.update(it)
                    if old_id:
                        existing['id'] = old_id  # oc_auto.by 與人類否決仍指向同一則
                    existing.pop('unverified', None)
                    if pending and not rejected:
                        new.append(existing)  # 本版確定從未套用的回音，補驗後才消費
                    elif not rejected:
                        needs_confirmation = True  # legacy 無法分辨是否已被人復原，不重放
                    enriched = True
                    continue
                if (it.get('kind') == 'reject' and it.get('reason')
                        and existing.get('kind') == 'reject' and not existing.get('reason')):
                    existing['reason'] = it['reason']
                    enriched = True
                continue
            if it.get('unverified'):
                it['effect_pending'] = True  # 由程式記錄尚未套用，不能由 Agent 自報
            rp['items'].append(it)
            new.append(it)
            by_id[it['id']] = it
        ev_cleared = bool(evidence_confirmed and m.pop('ev', None))
        if not new and not enriched and not ev_cleared:
            continue
        what = []                                     # (哪一類, 給人看的字):看板那一行照類別組,不拆字串
        maybe = [x for x in new if x.get('maybe')]
        if len(new) > len(maybe):
            what.append(('', f'新回音 {len(new) - len(maybe)} 則'))
        if maybe:
            what.append(('maybe', f'可能是同一封回音 {len(maybe)} 則,沒自動改'))
        if enriched:
            what.append(('', '補充回音證據'))
        if needs_confirmation:
            what.append(('', '舊回音已補驗，結果由你確認'))
        if ev_cleared:
            what.append(('', '已找到確認信或平台應徵紀錄'))
        decisive = sorted([x for x in new if x.get('kind') in KIND_TO_OC and not x.get('maybe')
                          and not x.get('unverified') and not x.get('effect_rejected')],
                          key=lambda x: x.get('date') or '')
        cur = m.get('oc') or ''
        steps = [(KIND_TO_OC[x['kind']], x) for x in decisive]
        # 沒下文的前提是「查過、一則回音都沒有」。程式自己記的沒下文,後來收到確認信(「還在審」)也是回音,
        # 照回音改回等回音(GLOSSARY「沒下文」)。他自己按的沒下文(沒有 oc_auto)是他的決定,不動
        if not steps and cur == 'ghost' and (m.get('oc_auto') or {}).get('s') == 'ghost':
            steps = [('', x) for x in new if x.get('kind') == 'confirm' and not x.get('maybe')
                     and not x.get('unverified') and not x.get('effect_rejected')][-1:]
        if steps and cur != 'wd':                     # 「我不去了」是他的決定,不自動改
            at0 = dict(m.get('oc_at') or {})
            for s, x in steps:                        # 照日期一則一則走,保留中途狀態
                set_outcome(m, s, (x.get('date') or day)[:10])
            s = m.get('oc') or ''
            if s != cur:
                m['oc_auto'] = {'s': s, 'from': cur, 'at': day, 'by': steps[-1][1]['id']}
                if at0:                               # 看板「不對,復原」原封放回改之前的日期,不照 from 重算
                    m['oc_auto']['from_at'] = at0
                what.append(('moved', f'{OC_WORD[cur]} → {OC_WORD[s]}'))
        if any(x.get('todo') for x in new):
            what.append(('todo', '有要你做的事'))
        done[url] = what
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
        if ds.app_of(item) == 'sent' and item.get('ev'):
            item['ev'] = '已查信箱與可讀平台應徵紀錄,仍未找到確認信或平台紀錄;目前只有送出頁證據'
            updated.append(url)
    return updated


def summary(res, jobs):
    """看板那一行:講人話,不列網址。"""
    if not res:
        return '沒有新回音'
    def name(url):
        return card.name(jobs.get(url) or {})[:24] or url.split('/')[2]

    def of(tag):
        return [(name(url), text) for url, what in res.items() for t, text in what if t == tag]
    moved = [f'{n} {text}' for n, text in of('moved')]
    todo = [n for n, _ in of('todo')]
    maybe = [n for n, _ in of('maybe')]
    # 送出結果不明的卡:查完改成什麼,每一張都點名(不是回音,一般那一行講不到)
    out = '；'.join(f'{word}:' + '、'.join(n for n, text in of('unsure') if text == word)
                   for word in UNSURE_WORDS.values() if any(text == word for _, text in of('unsure')))
    res = {url: what for url, what in res.items() if any(t != 'unsure' for t, _ in what)}
    if not res:
        return out
    out += ('；' if out else '') + f'{len(res)} 張有新回音'
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


# 送出結果不明:按下送出後至少等這麼久才拿「查完了都沒有」判定沒送出(確認信常常晚幾分鐘才到)
UNSURE_WAIT = 3600
NOT_SENT = '查過信箱和平台的應徵紀錄,沒有送出的證據(沒有確認信、回音或應徵紀錄):判定沒送出,要重填'
UNCLEAR = '送出結果不明:查到可能是這張的信或紀錄,但程式核實不了是不是這張(原文沒讀到,或同一封對到好幾張)'
UNREAD = '送出結果不明:信箱查過沒有確認信,但你在 {platform} 有帳號,它的應徵紀錄這一輪沒讀到,還不能確定沒送出'
UNREAD_NEED = '點開看那個平台的應徵紀錄:有這一筆就在卡上按「其實送出了」,沒有就按「確認沒送出,可以重送」'
# 送出結果不明的卡查完改成什麼:卡的查應徵進度結果、看板那一行都用這幾句
UNSURE_WORDS = {'sent': '送出結果不明,查到送出的證據,改成已投出', 'not_sent': '送出結果不明,查不到送出的證據,改成要重填',
                'not_sent_page': '送出結果不明,查不到送出的證據,頁還開著,改成停著等你(可以再確認送出)',
                'not_sent_stale': '送出結果不明,查不到送出的證據,頁上傳的是舊檔,會自動照新的重填',
                'unclear': '送出結果不明,查到可能的證據但核實不了,回報給你',
                'unread': '送出結果不明,平台應徵紀錄沒讀到,還不能確定,回報給你'}
UNCLEAR_NEED = '點開附的那一頁看是不是這張:是就在卡上按「其實送出了」,不是就按「確認沒送出,可以重送」'


def _local_time(value):
    """ISO 時間 → 本機時間(不帶時區),跟 datetime.now() 比得了;讀不懂回 None。"""
    try:
        t = datetime.datetime.fromisoformat(str(value or ''))
    except (ValueError, TypeError):
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def _ref(it):
    """哪一封信(或哪一筆紀錄),同一封只有一種寫法:連結或來源代號認得出 Gmail 對話就用 email:<代號>
    (舊回音只存了連結),否則照來源代號、原文連結。不含種類、日期:同一封被判成不同種類還是同一封。"""
    ref = str(it.get('source_ref') or '')
    thread = gmail_message_id(str(it.get('link') or '')) or (ref[6:] if ref.startswith('email:') else '')
    return f'email:{thread}' if thread else ref or str(it.get('link') or '')


def _held(m):
    """一張卡身上已經記著的信(回音、送出證據、之前幾次投遞 tries、history 裡的)。"""
    out = set()

    def walk(x):
        if isinstance(x, dict):
            if x.get('source_ref') or x.get('link'):
                out.add(_ref(x))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk([(m.get('replies') or {}).get('items'), (m.get('apply') or {}).get('sent'), m.get('tries'), m.get('history')])
    return out


def apply_unsure(fb, findings, checked, jobs=None, now=None, records_read=(), accounts=(), after=None):
    """送出結果不明的卡照這一輪查到的改(跟他在卡上按「其實送出了」「確認沒送出」走同一個事件):
      · 程式核實過原文、只屬於這張、這次送出之後才有的確認信或回音 → 已投出,那一則記成送出證據
        (after:{卡: 按送出那一秒之後有信的對話},collect_sources 照秒數搜的);
      · 信箱完整查過(checked)、他有帳號的平台(accounts)應徵紀錄這一輪也讀到了(records_read)、送出後過了
        UNSURE_WAIT,還是一則都沒有 → 沒送出(頁不在了就要重填;還開著就停著等你,可以再確認送出);
      · 只查到核實不了的(agent 補查、程式沒讀到原文;同一封也對到別張、或之前就記在別張、上一次投遞用過):不改,回報給他,附那一則的連結;
      · 他有帳號的平台,應徵紀錄這一輪沒讀到:查不完,不改,回報給他。
    平台應徵紀錄找到的已經在 sync_sent 走 platform_found 改成已投出,這裡不會再看到它。
    回 {url: 'sent'|'not_sent'|'not_sent_page'|'unclear'|'unread'}。"""
    import delivery_state as ds
    import profile_sync as ps
    import ship
    now = now or datetime.datetime.now()
    stamp = now.isoformat(timespec='seconds')
    after = {u: {f'email:{t}' for t in ts} for u, ts in (after or {}).items()}
    owners = {}                                   # 哪一封在哪幾張卡上:這一輪交的
    for u, its in (findings or {}).items():
        for it in its or []:
            if it.get('source_type') != 'application_record' and _ref(it):
                owners.setdefault(_ref(it), set()).add(u)
    held = {u: _held(x) for u, x in fb.items() if isinstance(x, dict) and not u.startswith('__')}
    out = {}
    for url in unsure_cards(fb):
        m = fb[url]
        items = (findings or {}).get(url) or []
        tried = submit_tried_at(m)

        def mine(x):
            # 這一次送出的:按送出那一秒之後才有信的對話(after),這一輪只交到這張、之前沒記在任何一張卡或哪一次投遞上
            ref = _ref(x)
            return ref in after.get(url, ()) and owners.get(ref, {url}) <= {url} and not any(ref in h for h in held.values())
        proof = [x for x in items if x.get('_source_verified') and not x.get('unverified')
                 and x.get('source_type') == 'email' and mine(x)]
        platform = ps.profile_key(url)
        if proof:
            x = min(proof, key=lambda it: it.get('date') or '')
            # 送出的是 agent(跟他在卡上按「其實送出了」一樣);查到的那一則是證據(by: reply)
            ds.fire(fb, url, 'actually_sent', by='agent', at=stamp, sent_at=(tried or x['date'])[:10],
                    evidence={'by': 'reply', 'at': stamp, 'src': x.get('src'), 'source_ref': x.get('source_ref'),
                              'date': x.get('date'), 'link': x.get('link'), 'quote': x.get('quote') or x.get('snippet')})
            ship.record_sent(fb, url, (jobs or {}).get(url))
            agent_report.apply_resolve(fb, url, '查應徵進度查到送出的證據,已改成已投出', src=agent_report.FROM_APPLY)
            out[url] = 'sent'
        elif items:
            x = items[0]
            agent_report.apply_report(fb, '查回音', UNCLEAR, need=UNCLEAR_NEED, job=url, url=x.get('link') or url)
            out[url] = 'unclear'
        elif url in checked and tried:
            t = _local_time(tried)
            if t is None or (now - t).total_seconds() < UNSURE_WAIT:
                continue
            if platform in accounts and platform not in records_read:
                agent_report.apply_report(fb, '查回音', UNREAD.format(platform=platform), need=UNREAD_NEED, job=url)
                out[url] = 'unread'
                continue
            ds.fire(fb, url, 'not_sent', at=stamp, issues=[NOT_SENT])
            agent_report.apply_resolve(fb, url, '查應徵進度查過了,沒有送出的證據', src=agent_report.FROM_APPLY)
            out[url] = {'gone': 'not_sent', 'parked': 'not_sent_page'}.get(ds.state(fb[url]), 'not_sent_stale')
    return out


def apply_results(fb, findings, checked, day=None, unverified=(), jobs=None, now=None, records_read=(), accounts=(),
                  after=None):
    """套用本輪回音;只替來源完整查完的卡保存查過日期。
    unverified:來源尚未核實完整的卡，不推水位、不宣告查完、不推論沒下文。
    送出結果不明的卡先照證據改成已投出或要重填(apply_unsure),改成已投出的接著照一般的回音記。"""
    day = day or today()
    checked = set(checked or []) - set(unverified or ())
    unsure = apply_unsure(fb, findings, checked, jobs, now, records_read, accounts, after)
    result = apply_findings(fb, findings, day)
    for url, what in unsure.items():
        result[url] = [('unsure', UNSURE_WORDS[what])] + result.get(url, [])
    for url in checked:
        item = fb.get(url)
        if ds.app_of(item) != 'sent':
            continue
        replies = item.setdefault('replies', {'items': []})
        replies['at'] = day
    weak_evidence = apply_checked_evidence(fb, checked)
    ghosts = apply_ghost(fb, day=day, checked=checked)
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
4. 無法進入的來源要列 source、reason、need、本人要去處理的那一頁網址 url 和受影響卡片網址;影響範圍不明時不要把任何卡放進 checked。

輸出 JSON 檔 {out},格式如下:
{{
  "checked": ["本輪已完成來源檢查、沒有未解存取問題的卡片網址"],
  "findings": [{{"url":"卡片網址","source_ref":"複製來源或補查來源中的識別值","source":"來源名稱","date":"YYYY-MM-DD","summary":"重點,200字內","link":"原文連結","kind":"confirm|reject|interview|offer","quote":"信裡決定是這一種的那一句,逐字抄","reason":"拒絕信明確寫出的理由原文;制式信留空","todo":"尚未完成的待辦,沒有填空字串"}}],
  "job_ids": [{{"url":"應徵紀錄上的職缺連結,沒有就空字串","platform":"平台名稱,照頁面上的寫法","id":"頁面上的職缺代號","applied_at":"YYYY-MM-DD","title":"方便人閱讀,不參與配對"}}],
  "inaccessible": [{{"source":"來源","reason":"進不去的原因","need":"本人需要做什麼","url":"本人要去處理的那一頁網址(例如登入頁)","jobs":["受影響的卡片網址"]}}]
}}

程式會拿日期、原文連結、quote、拒絕理由、應徵紀錄的代號跟它複製的全文比,對不上的那一則不收、那張卡這一輪不算查完。
不要直接改看板或呼叫 agent_report.py;程式會記錄回報。只寫這份 JSON 檔,其他檔案不要改,最後一行印 @@DONE@@。
"""


def prompt_for(fb, jobs, urls, out, source_file='(預覽時尚未擷取)', inaccessible=None):
    unsure = set(unsure_cards(fb))
    cards = '\n'.join(
        f'- {url} | {card.name(jobs.get(url) or {})} | {since_of(fb, url) or "?"} 起'
        + (' | 送出結果不明:那天按了送出卻沒看到成功頁,找有沒有確認收到申請的信或平台應徵紀錄' if url in unsure else '')
        for url in urls
    )
    fallback = json.dumps(inaccessible or [], ensure_ascii=False, indent=2) if inaccessible else '(沒有;不要開瀏覽器)'
    return PROMPT.format(cards=cards, out=out, source_file=source_file, fallback_sources=fallback)


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
                item['_source_verified'] = False
            found.setdefault(url, []).append(item)

    job_ids = []
    for row in verdict.rows.get('job_ids', []):
        try:
            if not row.ok:
                raise ValueError('；'.join(row.problems))
            if not any(k in row.facts for k in ('id', 'url')):
                continue                                  # 程式沒讀到應徵紀錄全文:核對不了,不拿來標已投遞
            rec = _job_id(dict(row.facts, applied_at=row.judged.get('applied_at'), title=row.row.get('title')))
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
        except ValueError as e:
            dropped.append(str(e)[:300])
            unsure.update(affected or ['*'])
            continue
        if affected:
            checked.difference_update(affected)
        else:
            checked.clear()                            # 未知影響範圍時不對任何卡推斷沒下文
        link = str(row.row.get('url') or '').strip()
        inaccessible.append({'source': source[:100], 'reason': reason[:240],
                             'need': need[:240], 'jobs': affected,
                             'url': link[:500] if link.startswith(('https://', 'http://')) else ''})

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
            need=item['need'], job=jobs[0] if len(jobs) == 1 else '', live=board, url=item.get('url') or ''
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
    urls = everyone = to_check(fb)
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
        import chrome_door
        # 查應徵進度跟其他工作一樣走 agent 的瀏覽器(chrome_door):程式先自己讀,讀不到的來源交給 agent 補查,程式再讀一次核實
        door = chrome_door.current()
        up, msg, need = door.ready(board) if door else (False, chrome_door.NO_BROWSER_AGENT, '勾一個「用它操作 ego」的 agent')
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
            prompt = prompt_for(fb, jobs, urls, out, source_file, program_unavailable)
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
            # agent 補查過的來源,程式自己再讀一次核實讀完了沒
            if program_unavailable and outcome.ok:
                verified = reread_fallback(program_unavailable, board, texts=texts)
        finally:
            try:
                os.remove(source_file)
            except FileNotFoundError:
                pass  # Collection may fail before creating the copied source file.
            except OSError as e:
                cleanup_errors.append(f'無法清除複製的查應徵進度來源檔({type(e).__name__})')
            try:
                chrome_door.close_if_idle(board)      # 收掉排入收尾的工作區
            except Exception as e:  # noqa: BLE001 — 收尾失敗照實記進 cleanup_errors 回報
                cleanup_errors.append(f'agent 的瀏覽器收尾失敗({type(e).__name__})')
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

    unverified.update(url for url, items in parsed.findings.items()
                      if any(item.get('unverified') for item in items))
    parsed.checked.difference_update(unverified)
    msgs = []
    _report_inaccessible(parsed.inaccessible, board)
    if parsed.job_ids:
        import sync_sent
        msg = sync_sent.sync(board, parsed.job_ids)
        if msg:
            msgs.append(msg)
            jobs, fb = load(board)

    import profile_sync as ps
    accounts = {k for k in ps.registry() if not str(k).startswith('_')}   # 他有帳號的平台:沒讀到應徵紀錄不判沒送出
    result, ghosts, weak_evidence = {}, [], []
    def apply(fbx):
        applied, became_ghosts, updated_weak_evidence = apply_results(
            fbx, parsed.findings, parsed.checked, unverified=unverified, jobs=jobs,
            # 讀到了的平台應徵紀錄:程式自己讀到的,加上 agent 補查、程式再讀核實過的(卡在 checked、不在 unverified 就是核實過)
            records_read={r.get('source_id') for r in sources.get('application_records') or []}
            | {x.get('source_ref', '').split(':', 1)[-1] for x in program_unavailable
               if x.get('source_type') == 'application_record'},
            accounts=accounts, after=sources['gmail'].get('after'),
        )
        result.update(applied)
        ghosts.extend(became_ghosts)
        weak_evidence.extend(updated_weak_evidence)
    bd.set_fb(apply, live=board, by='reply_run')
    # 之前留下的查應徵進度回報:這一輪重查過的就過時了(問題還在的,這一輪上面已經再報一次,時間是這一輪的)。
    # 查了全部還在等的卡就全收;只查了幾張(「只查這一張」「跑幾張」)只收那幾張的,別張沒重查不能替它收
    why = '後來那一輪查應徵進度重查過了'
    if len(urls) == len(everyone):
        agent_report.resolve(None, why, live=board, src='查回音', before=started)
    elif parsed.checked:
        bd.set_fb(lambda f: [agent_report.apply_resolve(f, url, why, src='查回音', before=started) for url in parsed.checked],
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
        msgs.append(f'{len(unverified)} 張來源全文或讀取完整性沒核實,這一輪不推論沒下文')
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
