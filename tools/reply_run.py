"""
reply_run —— 「📬 查回音」:已投遞的卡,誰回了、回了什麼。

agent 在自己的 Chrome 搜尋適合的來源,回傳回音證據、平台應徵紀錄上的職缺和無法讀取的來源。
程式只驗證交件形狀,再依證據更新看板、對帳應徵紀錄(sync_sent)、記錄待辦和無下文狀態。
"""
import os, sys, json, time, argparse, datetime, hashlib, re, urllib.parse
from dataclasses import dataclass
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd
import agent_run as ar
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


def _mailbox_message_ref(link, box=None):
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
    return [u for u, m in fb.items() if isinstance(m, dict) and m.get('app') == 'sent'
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
    """Return the safe Gmail literals that actually cover one company name."""
    terms = []
    words = str(company or '').split()
    for value in (company, words[0] if words else ''):
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
    if reader:
        return reader(urls)
    import agent_chrome
    return agent_chrome.read_pages(urls, board, ready=ready, settle=settle)


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


def _gmail_message_id(value):
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
        if (thread_id := _gmail_message_id(anchor.get('href')))
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
        except Exception as e:
            search_page = {}
            search_error = f'Gmail 搜尋頁讀取失敗:{str(e)[:120]}'
        else:
            search_error = (_gmail_page_problem(search_page, search_url)
                            or _gmail_search_problem(search_page))
        if search_error:
            inaccessible.append({'source_ref': 'email:search', 'source_type': 'email',
                                 'source': 'Gmail', 'reason': search_error,
                                 'need': '用 agent 專用 Chrome 補查 Gmail,並記錄查到的郵件來源',
                                 'jobs': searchable_urls})
        else:
            thread_ids = _gmail_search_thread_ids(search_page)
            if len(thread_ids) > max_mails:
                inaccessible.append({'source_ref': 'email:search-more', 'source_type': 'email',
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
        except Exception as e:
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
            if not ps._same_platform_url(final_url, source_id):
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
    return hashlib.sha1(json.dumps([it.get('src'), it.get('date'), it.get('subject'), it.get('link')],
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
    for url, items in (found or {}).items():
        m = fb.get(url)
        if not isinstance(m, dict) or m.get('app') != 'sent':
            continue
        rp = m.setdefault('replies', {'items': []})
        by_id = {x.get('id'): x for x in rp['items'] if isinstance(x, dict)}
        new = []
        enriched = False
        evidence_confirmed = False
        for it in items or []:
            it.setdefault('source_type', _source_type(it.get('src')))
            evidence_confirmed = evidence_confirmed or (
                it.get('_source_verified')
                and it.get('source_type') in ('email', 'application_record')
                and it.get('kind') in KINDS
            )
            it = {k: it.get(k) for k in
                  ('src', 'source_type', 'source_ref', 'date', 'subject', 'snippet', 'link', 'kind',
                   'todo', 'reason') if it.get(k)}
            if it.get('kind') != 'reject':
                it.pop('reason', None)
            it['id'] = _key(it)
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
        if new:
            what.append(f'新回音 {len(new)} 則')
        if enriched:
            what.append('補上拒絕理由')
        if ev_cleared:
            what.append('已找到確認信或平台應徵紀錄')
        decisive = sorted([x for x in new if x.get('kind') in KIND_TO_OC], key=lambda x: x.get('date') or '')
        cur = m.get('oc') or ''
        if decisive and cur != 'wd':                  # 「我不去了」是他的決定,不自動改
            for x in decisive:                        # 照日期一則一則走,保留中途狀態
                set_outcome(m, KIND_TO_OC[x['kind']], (x.get('date') or day)[:10])
            s = m.get('oc') or ''
            if s != cur:
                m['oc_auto'] = {'s': s, 'from': cur, 'at': day, 'by': decisive[-1]['id']}
                what.append(f'{OC_WORD[cur]} → {OC_WORD[s]}')
        if any(x.get('todo') for x in new):
            what.append('有要你做的事')
        done[url] = ';'.join(what)
    return done


def apply_checked_evidence(fb, checked):
    """完成來源檢查仍沒有郵件或平台紀錄時,更新送出頁弱證據的查找狀態。"""
    updated = []
    for url in checked or []:
        item = fb.get(url)
        if isinstance(item, dict) and item.get('app') == 'sent' and item.get('ev'):
            item['ev'] = '已查信箱與可讀平台應徵紀錄,仍未找到確認信或平台紀錄;目前只有送出頁證據'
            updated.append(url)
    return updated


def summary(res, jobs):
    """看板那一行:講人話,不列網址。"""
    if not res:
        return '沒有新回音'
    def name(url):
        return card.name(jobs.get(url) or {})[:24] or url.split('/')[2]
    moved = [f'{name(url)} {what.split(";")[1]}' for url, what in res.items() if '→' in what]
    todo = [name(url) for url, what in res.items() if '要你做' in what]
    out = f'{len(res)} 張有新回音'
    if moved:
        out += ';改了狀態:' + '、'.join(moved)
    if todo:
        out += ';要你做的:' + '、'.join(todo)
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


def apply_results(fb, findings, checked, day=None):
    """套用本輪回音;只替來源完整查完的卡保存查過日期。"""
    day = day or today()
    checked = set(checked or [])
    result = apply_findings(fb, findings, day)
    for url in checked:
        item = fb.get(url)
        if not isinstance(item, dict) or item.get('app') != 'sent':
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
1. 回音要給卡片網址、source_ref、來源、日期、摘要、原文連結、種類、source_type 和待辦。source_ref 必須照抄來源全文中的值,或照抄補查清單的值。kind 只能是 confirm(確認收到或仍在處理)、reject(沒錄取)、interview(面試、測驗或作業邀請)、offer(錄取)。source_type 只能是 email、application_record、fallback。待辦只記對方要求本人完成且尚未完成的事。
2. 讀平台應徵紀錄頁時,將能確認已投遞的記為 confirm/source_type=application_record;每筆應徵紀錄放進 job_ids:有職缺連結就照抄連結,沒有就給平台名稱和頁面上的職缺代號,程式只用平台+代號比對。
3. 只有檢查完信箱搜尋全文和該卡可用的應徵紀錄來源,且沒有未解的存取問題,才把卡列入 checked。沒有找到回音也可列入 checked。
4. 無法進入的來源要列 source、reason、need 和受影響卡片網址;影響範圍不明時不要把任何卡放進 checked。

輸出 JSON 檔 {out},格式如下:
{{
  "checked": ["本輪已完成來源檢查、沒有未解存取問題的卡片網址"],
  "findings": [{{"url":"卡片網址","source_ref":"複製來源或補查來源中的識別值","source":"來源名稱","source_type":"email|application_record|fallback","date":"YYYY-MM-DD","summary":"重點,200字內","link":"原文連結","kind":"confirm|reject|interview|offer","reason":"拒絕信明確寫出的理由原文;制式信留空","todo":"尚未完成的待辦,沒有填空字串"}}],
  "job_ids": [{{"url":"應徵紀錄上的職缺連結,沒有就空字串","platform":"平台,例:104、linkedin","id":"頁面上的職缺代號","applied_at":"YYYY-MM-DD","title":"方便人閱讀,不參與配對"}}],
  "inaccessible": [{{"source":"來源","reason":"進不去的原因","need":"本人需要做什麼","jobs":["受影響的卡片網址"]}}]
}}

不要直接改看板或呼叫 agent_report.py;程式會記錄回報。只寫這份 JSON 檔,其他檔案不要改,最後一行印 @@DONE@@。
"""


def prompt_for(fb, jobs, urls, out, source_file='(預覽時尚未擷取)', inaccessible=None):
    cards = '\n'.join(
        f'- {url} | {card.name(jobs.get(url) or {})} | {since_of(fb, url) or "?"} 起'
        for url in urls
    )
    fallback = json.dumps(inaccessible or [], ensure_ascii=False, indent=2) if inaccessible else '(沒有;不要開瀏覽器)'
    return PROMPT.format(cards=cards, out=out, source_file=source_file, fallback_sources=fallback)


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


def parse_result(data, urls, source_types=None, since_dates=None):
    """驗證 agent 交件並將回音整理成看板格式;不接受清單外的卡片。"""
    if not isinstance(data, dict) or not isinstance(data.get('checked'), list):
        raise ValueError('缺少 checked 卡片清單')
    for key in ('findings', 'job_ids', 'inaccessible'):
        if not isinstance(data.get(key, []), list):
            raise ValueError(f'{key} 必須是陣列')

    allowed = set(urls)
    box = mailbox()
    checked = {str(url) for url in data['checked'] if str(url) in allowed}
    found = {}
    for row in data.get('findings', []):
        if not isinstance(row, dict):
            raise ValueError('findings 裡有非物件資料')
        url = str(row.get('url') or '')
        if url not in allowed:                       # 清單外的卡不能改狀態
            continue
        kind = str(row.get('kind') or '').strip()
        source = str(row.get('source') or '').strip()
        summary = str(row.get('summary') or '').strip()
        link = str(row.get('link') or '').strip()
        if kind not in KINDS or not source or not summary or not link:
            raise ValueError(f'回音資料缺種類、來源、摘要或原文連結: {url}')
        date = _iso_date(row.get('date'))
        since = str((since_dates or {}).get(url) or '')[:10]
        if since and date < since:
            continue
        source_ref = str(row.get('source_ref') or '').strip()
        trusted_source_type = (source_types or {}).get(source_ref, 'fallback')
        source_verified = bool(source_ref and source_ref in (source_types or {}))
        if trusted_source_type == 'email' and source_ref in ('email:search', 'email:search-more'):
            thread_id = _gmail_message_id(link) or _mailbox_message_ref(link, box)
            if thread_id:
                source_ref = f'email:{thread_id}'
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
        found.setdefault(url, []).append(item)

    job_ids = []
    for row in data.get('job_ids', []):
        if isinstance(row, str):
            row = {'id': row}
        if not isinstance(row, dict):
            raise ValueError('job_ids 裡有非物件資料')
        jid = str(row.get('id') or '').strip()
        link = str(row.get('url') or '').strip()
        if not jid and not link:
            continue
        rec = {'id': jid, 'title': str(row.get('title') or '').strip()[:160]}
        if link:
            rec['url'] = link[:500]
        if str(row.get('platform') or '').strip():
            rec['platform'] = str(row['platform']).strip()[:40]
        if row.get('applied_at'):
            rec['applied_at'] = _iso_date(row['applied_at'])
        job_ids.append(rec)

    inaccessible = []
    for row in data.get('inaccessible', []):
        if not isinstance(row, dict):
            raise ValueError('inaccessible 裡有非物件資料')
        source = str(row.get('source') or '').strip()
        reason = str(row.get('reason') or '').strip()
        need = str(row.get('need') or '').strip()
        if not isinstance(row.get('jobs', []), list):
            raise ValueError('inaccessible.jobs 必須是陣列')
        if not source or not reason or not need:
            raise ValueError('無法讀取的來源必須包含來源、原因和本人待辦')
        affected = [str(url) for url in row.get('jobs', []) if str(url) in allowed]
        if affected:
            checked.difference_update(affected)
        else:
            checked.clear()                            # 未知影響範圍時不對任何卡推斷沒下文
        inaccessible.append({'source': source[:100], 'reason': reason[:240],
                             'need': need[:240], 'jobs': affected})

    for url in checked:
        found.setdefault(url, [])
    for url in list(found):
        if url not in checked and not found[url]:
            del found[url]
    return EchoReadResult(found, checked, job_ids, inaccessible)


def preview(board=None):
    """顯示會交給 agent 的查回音 prompt;不連網。"""
    board = board or bd.LIVE
    jobs, fb = load(board)
    urls = waiting(fb)
    if not urls:
        return '現在沒有在等回音的卡。'
    return ar.rules_for('main', board=board, web=False) + prompt_for(
        fb, jobs, urls, os.path.join(SP, 'replies.json'))


def _report_inaccessible(items, board):
    for item in items:
        jobs = item['jobs'] or ['']
        for url in jobs:
            agent_report.report(
                '查回音', f'{item["source"]} 無法進入:{item["reason"]}',
                need=item['need'], job=url, live=board
            )


def _copy_only_agent_id():
    """Copy-only analysis requires Codex so browser plugins and web search can both stay off."""
    agents = ((cf.C.get('agent') or {}).get('agents') or [])
    agent = next((item for item in agents if item.get('runtime') == 'codex'), None)
    return agent.get('id') if agent else None


def _program_can_read():
    """程式自己讀信箱、平台應徵紀錄,走的是 Codex 的 Chrome 外掛;清單裡沒有能開瀏覽器的 Codex 就沒有這條路。"""
    agents = ((cf.C.get('agent') or {}).get('agents') or [])
    return any(item.get('runtime') == 'codex' and item.get('browser') for item in agents)


def _no_program_reader(urls):
    raise RuntimeError('只裝 Claude Code:程式讀不了,改由 agent 在它的 Chrome 裡讀')


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
    urls = waiting(fb)
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
    try:
        import agent_chrome
        program_reads = _program_can_read()
        # 只裝 Claude Code:不碰 Codex 外掛那一套,每個來源都交給 Claude 在 agent 的 Chrome 裡讀(Claude in Chrome);
        # agent 的 Chrome 照樣在背景藏著開
        up, msg = agent_chrome.ensure(board) if program_reads else agent_chrome.wait_claude()
        if not up:
            agent_report.report('查回音', msg, need='打開 Chrome 的「agent」設定檔並完成登入', live=board)
            jobrun.write(st, dict(base, phase='failed', n=len(urls), done=0, msg=msg,
                                  finished_at=time.time()))
            print(msg)
            return 1
        try:
            sources, program_unavailable = collect_sources(
                fb, jobs, urls, board, reader=None if program_reads else _no_program_reader)
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
            outcome = ar.run(prompt, log, cf.HOME, timeout=TIMEOUT,
                             browser_required=bool(program_unavailable),
                             browser=overrides + ['-c', 'tools.web_search=false'],
                             board=board, web=False,
                             agent_id=None if program_unavailable else copy_agent)
        finally:
            cleanup_errors = []
            try:
                os.remove(source_file)
            except FileNotFoundError:
                pass  # Collection may fail before creating the copied source file.
            except OSError as e:
                cleanup_errors.append(f'無法清除複製的查應徵進度來源檔({type(e).__name__})')
            try:
                if program_reads:
                    agent_chrome.close_if_idle(board)
            except Exception as e:
                cleanup_errors.append(f'agent Chrome 收尾失敗({type(e).__name__})')
            if cleanup_errors:
                raise RuntimeError('；'.join(cleanup_errors))
    except Exception as e:
        outcome = None
        error = f'查回音 agent 沒完成:{str(e)[-200:]}'
    if outcome is not None and not outcome.ok:
        error = '查回音 agent 沒完成:' + outcome.message()
    if outcome is None or not outcome.ok:
        agent_report.report('查回音', error, need='在「已投遞」按「看紀錄」確認後再重跑', live=board)
        jobrun.write(st, dict(base, phase='failed', n=len(urls), done=0, msg=error,
                              finished_at=time.time()))
        print(error)
        return 1

    try:
        with open(out, encoding='utf-8') as f:
            parsed = parse_result(
                json.load(f), urls,
                source_types=_source_type_index(sources, program_unavailable),
                since_dates={url: since_of(fb, url) for url in urls},
            )
    except Exception as e:
        error = f'查回音 agent 交件無法使用:{str(e)[:200]}'
        agent_report.report('查回音', error, need='在「已投遞」按「看紀錄」確認後再重跑', live=board)
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
            fbx, parsed.findings, parsed.checked
        )
        result.update(applied)
        ghosts.extend(became_ghosts)
        weak_evidence.extend(updated_weak_evidence)
    bd.set_fb(apply, live=board, by='reply_run')
    if not parsed.inaccessible and parsed.checked == set(urls):
        agent_report.resolve_from('查回音', '後來那一輪查回音跑完了', started, live=board)
    if result:
        msgs.append(summary(result, jobs))
    if weak_evidence:
        msgs.append(f'{len(weak_evidence)} 張已查信箱與可讀平台紀錄,仍只有送出頁證據')
    if ghosts:
        msgs.append(f'{len(ghosts)} 張送出超過 {GHOST_DAYS} 天,本輪確認沒有回音,記成沒下文')
    if parsed.inaccessible:
        msgs.append(f'{len(parsed.inaccessible)} 個來源進不去,已寫入「📣 agent 回報」')
    missing = len(urls) - len(parsed.checked)
    phase = 'incomplete' if parsed.inaccessible or missing else 'done'
    message = '；'.join(msgs) or '沒有新回音'
    jobrun.write(st, dict(base, phase=phase, n=len(urls), done=len(parsed.checked), msg=message,
                          finished_at=time.time()))
    print(message)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
