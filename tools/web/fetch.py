#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch readable job-page text and report whether and how it was retrieved."""
from dataclasses import dataclass
from html.parser import HTMLParser
import http.client
import ipaddress
import os
import socket
import threading
import time
import json
import urllib.error
import urllib.parse
import urllib.request

UA = 'Mozilla/5.0 (compatible; jobsalvo/1)'
READER_ROOT = 'https://r.jina.ai'
RETRIES = 2
RETRY_WAIT = 3
TIMEOUTS = {'direct': 20, 'reader': 45}
# 連結檢查一次抓好幾個缺(board_status 開 6 條);直接抓、閱讀代理很輕,退到 agent 的瀏覽器(ego)那一步
# 每個缺開一個工作區跑 JS,同一時間最多開這麼多個,其他排隊。
EGO_FALLBACK_WORKERS = 2
_EGO_SLOTS = threading.BoundedSemaphore(EGO_FALLBACK_WORKERS)
MAX_BYTES = 5 * 1024 * 1024
HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class PageResult:
    """One fetch result. `unknown` always means the page could not be read; whether a job is closed is the agent's call."""

    url: str
    status: str
    text: str = ''
    via: str = ''
    title: str = ''
    http_status: int = 0
    errors: tuple = ()

    @property
    def readable(self):
        # 每條抓頁路共用:<title> 以外有沒有字(要跑 JS 才有內容的頁只剩 <title>,往下一條路退)。
        # 讀到的字是不是職缺、頁載完了沒,是 agent 讀了才說得出來的
        return self.status in ('ok', 'partial') and bool(self.text.strip())

    @property
    def jd(self):
        """給判斷的職缺頁原文:頁面標題(常寫著職稱、公司、薪資)加內文;沒讀到就是空的。"""
        return '\n'.join(x for x in (self.title, self.text) if x) if self.readable else ''

    @property
    def incomplete_notice(self):
        """Partial page boundary for agent prompts; it is metadata, not source text."""
        if self.status != 'partial':
            return ''
        return '; '.join(self.errors) or '頁面只回傳部分內容，其餘內容未提供；是否繼續由 agent 判斷'


def _partial_page(result, limit):
    title, text = result.title, result.text

    def candidate(title_length, text_length):
        message = (f'頁面材料超過 {limit} 字元；標題回傳前 {title_length}/{len(title)} 字元，'
                   f'內文回傳前 {text_length}/{len(text)} 字元；其餘內容未提供，是否繼續由 agent 判斷')
        return PageResult(result.url, 'partial', text=text[:text_length], via=result.via,
                          title=title[:title_length], http_status=result.http_status,
                          errors=(*result.errors, message))

    def fits(title_length, text_length):
        return len(json.dumps(candidate(title_length, text_length).__dict__, ensure_ascii=False)) <= limit

    title_length = len(title)
    if not fits(title_length, min(1, len(text))):
        low, high = 0, title_length
        while low < high:
            middle = (low + high + 1) // 2
            if fits(middle, min(1, len(text))):
                low = middle
            else:
                high = middle - 1
        title_length = low

    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(title_length, middle):
            low = middle
        else:
            high = middle - 1
    return candidate(title_length, low)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
        self.title_depth = 0
        self.title = []

    def handle_starttag(self, tag, _attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.hidden += 1
        if tag == 'title':
            self.title_depth += 1
        if tag in ('br', 'p', 'div', 'li', 'section', 'article', 'h1', 'h2', 'h3', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg') and self.hidden:
            self.hidden -= 1
        if tag == 'title' and self.title_depth:
            self.title_depth -= 1
        if tag in ('p', 'div', 'li', 'section', 'article', 'h1', 'h2', 'h3', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if self.title_depth:
            self.title.append(data)
        elif not self.hidden:
            self.parts.append(data)


def _visible_text(body):
    parser = _VisibleText()
    parser.feed(body)
    text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
    title = ' '.join(' '.join(parser.title).split())
    return text.strip(), title


def public_url_problem(url: str) -> str:
    """公開網址安全檢查。回傳原因；空字串代表可抓取。"""
    try:
        public_address(url)
    except ValueError as error:
        return str(error)
    return ''


def public_address(url: str) -> str:
    """公開網址安全檢查,回傳查到的公開 IP;連線要接這個 IP,不再查一次(查兩次,第二次可能被換成內網)。
    不能抓就丟 ValueError(原因)。"""
    try:
        parts = urllib.parse.urlsplit(str(url))
    except ValueError as error:
        raise ValueError(f'malformed URL: {error}') from error
    if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password:
        raise ValueError('target must be an HTTP(S) URL without credentials')
    host = (parts.hostname or '').rstrip('.').lower()
    if not host:
        raise ValueError('target hostname is missing')
    try:
        port = parts.port if parts.port is not None else (443 if parts.scheme == 'https' else 80)
    except ValueError:
        raise ValueError('target has an invalid port') from None
    test_loopback = os.environ.get('JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH') == '1'   # 假網站(tests/website_testbench.py)開在本機
    if port not in (80, 443) and not test_loopback:
        raise ValueError('target port must be 80 or 443')
    if host in ('localhost', 'metadata.google.internal') or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('local and metadata hostnames are blocked')
    return public_ip(host, port)


def public_ip(host: str, port: int) -> str:
    """Resolve the host once and return its public address; the connection is pinned to it."""
    test_loopback = os.environ.get('JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH') == '1'
    try:
        addresses = [ipaddress.ip_address(str(host).split('%', 1)[0])]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise ValueError(f'target hostname did not resolve: {host}') from error
        addresses = [ipaddress.ip_address(row[4][0].split('%', 1)[0]) for row in resolved]
    if not addresses or any(not (address.is_global or (test_loopback and address.is_loopback))
                            for address in addresses):
        raise ValueError('target resolves to a non-public IP address')
    return addresses[0].compressed


def _assert_public_url(url):
    """檢查是公開網址,回傳查到的公開 IP(連線就接這個 IP)。"""
    return public_address(url)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _assert_public_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, pinned_ip, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(host, **kwargs)

    def connect(self):
        if self._tunnel_host:
            raise OSError('HTTP proxy tunneling is disabled')
        self.sock = socket.create_connection((self.pinned_ip, self.port), self.timeout,
                                             self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, pinned_ip, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(host, **kwargs)

    def connect(self):
        if self._tunnel_host:
            raise OSError('HTTPS proxy tunneling is disabled')
        raw = socket.create_connection((self.pinned_ip, self.port), self.timeout,
                                       self.source_address)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class _PinnedHandlerMixin:
    """連線接檢查時查到的那個 IP,不在連線時再查一次 DNS(兩次之間可能被換成內網位址)。
    轉址到別的主機:這時檢查一次,接那次查到的 IP。"""
    def __init__(self, pinned_url='', pinned_ip=''):
        super().__init__()
        self.pinned_url, self.pinned_ip = pinned_url, pinned_ip

    def open_pinned(self, req, connection, **connection_args):
        pinned = urllib.request.Request(self.pinned_url) if self.pinned_url and self.pinned_ip else None
        same_origin = pinned is not None and (pinned.type, pinned.host) == (req.type, req.host)
        ip = self.pinned_ip if same_origin else _assert_public_url(req.full_url)
        return self.do_open(lambda host, **kwargs: connection(
            host, pinned_ip=ip, **connection_args, **kwargs), req)


class _PinnedHTTPHandler(_PinnedHandlerMixin, urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.open_pinned(req, _PinnedHTTPConnection)


class _PinnedHTTPSHandler(_PinnedHandlerMixin, urllib.request.HTTPSHandler):
    def https_open(self, req):
        # 要不要驗主機名記在 context 裡(父類別建構時設好);Python 3.14 起 handler 沒有 _check_hostname
        return self.open_pinned(req, _PinnedHTTPSConnection, context=self._context)


def _request(url, timeout, headers=None, pinned_ip=''):
    pinned_ip = pinned_ip or _assert_public_url(url)
    request = urllib.request.Request(url, headers=headers or {'User-Agent': UA})
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _PinnedHTTPHandler(url, pinned_ip), _PinnedHTTPSHandler(url, pinned_ip),
            _SafeRedirect())
        with opener.open(request, timeout=timeout) as response:
            return (response.status, response.geturl(), response.headers.get_content_type(),
                    response.headers.get_content_charset(), response.read(MAX_BYTES + 1))
    except urllib.error.HTTPError as error:
        status, final_url = error.code, error.geturl()
        content_type = error.headers.get_content_type()
        charset = error.headers.get_content_charset()
        body = error.read(MAX_BYTES + 1)
        error.close()
        return status, final_url, content_type, charset, body


def _decode(body, charset):
    return body.decode(charset or 'utf-8', 'replace')


def accept_language(langs=None):
    """跟網站要哪幾種語言的頁面:照設定的履歷語言順序(zh 送 zh-TW,zh)。沒設定就照原本的中文、英文。"""
    if langs is None:
        try:
            import config as cf
            langs = cf.LANGS
        except (ImportError, AttributeError):   # 單獨拿這支出去用(沒有 jobsalvo 的設定):照原本的中文、英文
            langs = None
    tags = []
    for code in langs or ['zh', 'en']:
        code = str(code or '').strip()
        for tag in (['zh-TW', 'zh'] if code.lower() == 'zh' else [code]):
            if tag and tag not in tags:
                tags.append(tag)
    return ','.join(tag if i == 0 else f'{tag};q={max(1, 10 - i) / 10:.1f}' for i, tag in enumerate(tags))


def _cut_off(body):
    """回應超過 MAX_BYTES 只收了前段,照實標,不冒充完整。"""
    return (f'頁面回應超過 {MAX_BYTES} 位元組；內文只來自前 {MAX_BYTES} 位元組，其餘內容未提供，是否繼續由 agent 判斷',
            ) if len(body) > MAX_BYTES else ()


def _direct(url, pinned_ip='', request_fn=None):
    headers = {'User-Agent': UA, 'Accept-Language': accept_language()}
    status, final_url, content_type, charset, body = (request_fn or _request)(
        url, TIMEOUTS['direct'], headers, pinned_ip)
    raw = _decode(body, charset)
    if content_type == 'text/html' or raw.lstrip().startswith('<'):
        text, title = _visible_text(raw)
    else:
        text, title = raw.strip(), ''
    if not text:
        reason = f'HTTP {status}' if not 200 <= status < 400 else 'response contained no readable text'
        raise RuntimeError(reason)
    cut = _cut_off(body)
    return PageResult(url, 'partial' if cut else 'ok', text=text, via='direct', title=title,
                      http_status=status, errors=cut)


def _reader(url, request_fn=None, reader_root=None):
    reader_url = (reader_root or READER_ROOT).rstrip('/') + '/' + url
    status, _final_url, _content_type, charset, body = (request_fn or _request)(
        reader_url, TIMEOUTS['reader'], {'User-Agent': UA})
    if not 200 <= status < 400:
        raise RuntimeError(f'reader HTTP {status}')
    text = _decode(body, charset).strip()
    if not text:
        raise RuntimeError('reader returned no text')
    title = ''
    if text.startswith('Title:'):
        title = text.splitlines()[0][len('Title:'):].strip()
    # 閱讀代理的格式是 Title / URL Source / Markdown Content 三段:只拿內文,空殼頁才判得出來沒讀到
    head, sep, content = text.partition('Markdown Content:')
    if sep and head.startswith('Title:'):
        text = content.strip()
    cut = _cut_off(body)
    return PageResult(url, 'partial' if cut else 'ok', text=text, via='reader', title=title,
                      http_status=status, errors=cut)


def _ego(url):
    with _EGO_SLOTS:                     # 先排到位子才開工作區
        return _ego_now(url)


def _ego_now(url):
    """要跑 JS 才有字的頁:在 agent 的瀏覽器(ego)開一個暫時的工作區讀,讀完就收(跟使用者在自己的瀏覽器點開職缺一樣)。
    網址在 fetch() 已確認是公開網址;載完後最後停在的網址也要是公開的(被導去內網、本機就不收)。"""
    import chrome_door
    page = chrome_door.EgoDoor().read_pages([url])[url]
    _assert_public_url(str(page.get('url') or url))
    text = str(page.get('text') or '').strip()
    if not text:
        raise RuntimeError('ego returned no readable text')
    return PageResult(url, 'ok', text=text, via='ego', title=str(page.get('title') or ''))

# 好幾個缺一起抓:大多時間在等網路,一個一個抓的話,一個打不開的頁(逾時加重試最多四分鐘)會卡住後面全部。
# ego 那一步另外有 _EGO_SLOTS 限制同時幾個,電腦不會被拖垮。
FETCH_WORKERS = 6


def read_batch(urls, fetch_page=None):
    """照順序回每個網址的 PageResult;抓的時候出例外也回一個 unknown,不讓一個壞網址中斷整批。"""
    from concurrent.futures import ThreadPoolExecutor
    fetch_page = fetch_page or request_page
    urls = list(urls)

    def one(url):
        try:
            return fetch_page(url)
        except Exception as error:  # noqa: BLE001 — 一頁抓不到不擋其他頁;原因照實放進這一頁的結果
            return PageResult(url, 'unknown', errors=(f'{type(error).__name__}: {str(error)[:180]}',))
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(urls))) as pool:
        return list(pool.map(one, urls))


def request_page(url, *, direct_fn=None, reader_fn=None, ego_fn=None, validate_url=None,
                 retries=None, retry_wait=None):
    """Return page text, the route used, and an explicit ok/partial/unknown status."""
    target = str(url or '').strip()
    try:
        pinned_ip = (validate_url or _assert_public_url)(target)   # 直接抓接這次查到的 IP,不再查一次
    except Exception as error:  # noqa: BLE001 - a failed URL check is a page read result
        return PageResult(target, 'unknown', errors=(f'target blocked: {str(error)[:180]}',))
    errors = []
    for attempt in range(RETRIES if retries is None else max(1, int(retries))):
        routes = (('direct', lambda page: (direct_fn or _direct)(page, pinned_ip=pinned_ip)),
                  ('reader', reader_fn or _reader), ('ego', ego_fn or _ego))
        for via, route in routes:
            try:
                result = route(target)
                if result.readable:
                    import chrome_door
                    if len(json.dumps(result.__dict__, ensure_ascii=False)) > chrome_door.PAGE_MATERIAL_CHAR_LIMIT:
                        return _partial_page(result, chrome_door.PAGE_MATERIAL_CHAR_LIMIT)
                    return result
                errors.append(f'{via}: no readable text')
            except Exception as error:  # noqa: BLE001 — 換下一條路抓;每一條的原因照實放進結果
                errors.append(f'{via}: {type(error).__name__}: {str(error)[:180]}')
        if attempt + 1 < (RETRIES if retries is None else max(1, int(retries))):
            time.sleep(RETRY_WAIT if retry_wait is None else max(0, float(retry_wait)))
    return PageResult(target, 'unknown', errors=tuple(errors))


def screenshot(url, out, validate_url=None, quote='', *, with_status=False):
    """Capture the URL through the final browser route after checking its final public URL."""
    (validate_url or _assert_public_url)(url)
    import chrome_door
    door = chrome_door.EgoDoor()
    opened = None
    try:
        opened = door.open_for_agent(url)
        final_url = str((opened.get('page') or {}).get('url') or url)
        (validate_url or _assert_public_url)(final_url)
        result = door.shot(opened['tab_id'], out, quote)
        return {'file': result, 'cropped': door.last_shot_cropped} if with_status else result
    finally:
        if opened and opened.get('tab_id'):
            door.release(opened['tab_id'])


def fetch_with_browser(url, validate_url=None, ego_fn=None):
    """只用 agent 的瀏覽器(ego)讀:agent 讀了直接抓的字,說頁還沒載完、要跑 JS 才有內容時用。"""
    try:
        (validate_url or _assert_public_url)(url)
        result = (ego_fn or _ego)(url)
    except Exception as error:  # noqa: BLE001 — 讀不到:原因照實放進結果
        return PageResult(str(url), 'unknown', errors=(f'ego: {type(error).__name__}: {str(error)[:180]}',))
    if not result.readable:
        return PageResult(str(url), 'unknown', errors=('ego: no readable text',))
    import chrome_door
    if len(json.dumps(result.__dict__, ensure_ascii=False)) > chrome_door.PAGE_MATERIAL_CHAR_LIMIT:
        return _partial_page(result, chrome_door.PAGE_MATERIAL_CHAR_LIMIT)
    return result


