#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch readable job-page text and report whether and how it was retrieved."""
from dataclasses import dataclass
from html.parser import HTMLParser
import datetime
import http.client
import ipaddress
import os
import socket
import threading
import time
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from url_origin import origin as _origin

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
API_ROOTS = {
    '104': 'https://www.104.com.tw/job/ajax/content',
    'lever': 'https://api.lever.co/v0/postings',
    'ashby': 'https://api.ashbyhq.com/posting-api/job-board',
    'greenhouse': 'https://boards-api.greenhouse.io/v1/boards',
}
HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class PageResult:
    """One fetch result. `unknown` always means the page could not be verified."""

    url: str
    status: str
    text: str = ''
    via: str = ''
    title: str = ''
    posted_at: str = ''
    posted_source: str = ''
    http_status: int = 0
    verified_live: bool = False  # 官方結構化欄位明確標示仍在徵
    errors: tuple = ()

    @property
    def readable(self):
        return self.status == 'ok' and bool(self.text.strip())


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
        if not self.hidden:
            self.parts.append(data)


_LD_JSON = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)


def _job_posting_date(html):
    """schema.org JobPosting 的 datePosted:職缺頁給搜尋引擎(Google for Jobs)看的標準欄位,大多數徵才系統都有放。"""
    def walk(node):
        if isinstance(node, list):
            for item in node:
                yield from walk(item)
        elif isinstance(node, dict):
            kind = node.get('@type')
            if kind == 'JobPosting' or (isinstance(kind, list) and 'JobPosting' in kind):
                yield node
            yield from walk(node.get('@graph'))
    for block in _LD_JSON.findall(html or ''):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for posting in walk(data):
            day = _date(posting.get('datePosted'))
            if day:
                return day
    return ''


def _visible_text(body):
    parser = _VisibleText()
    parser.feed(body)
    text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
    title = ' '.join(' '.join(parser.title).split())
    return text.strip(), title


def _assert_public_url(url):
    """Validate a target and return the exact IP address a connection must use."""
    parts = urllib.parse.urlsplit(str(url))
    if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password:
        raise ValueError('target must be an HTTP(S) URL without credentials')
    host = (parts.hostname or '').rstrip('.').lower()
    if not host:
        raise ValueError('target hostname is missing')
    try:
        port = parts.port if parts.port is not None else (443 if parts.scheme == 'https' else 80)
    except ValueError as error:
        raise ValueError('target has an invalid port') from error
    test_loopback = os.environ.get('JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH') == '1'
    if port not in (80, 443) and not test_loopback:
        raise ValueError('target port must be 80 or 443')
    if (host in ('localhost', 'metadata.google.internal') or
            host.endswith(('.localhost', '.local', '.internal'))):
        raise ValueError('local and metadata hostnames are blocked')
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise ValueError(f'target hostname did not resolve: {host}') from error
        addresses = [ipaddress.ip_address(row[4][0].split('%', 1)[0]) for row in resolved]
    allowed = lambda address: address.is_global or (test_loopback and address.is_loopback)
    if not addresses or any(not allowed(address) for address in addresses):
        raise ValueError('target resolves to a non-public IP address')
    return addresses[0].compressed


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
    def __init__(self, pinned_url='', pinned_ip=''):
        super().__init__()
        self.pinned_origin = _origin(pinned_url) if pinned_url else None
        self.pinned_ip = pinned_ip

    def address_for(self, url):
        if self.pinned_origin and _origin(url) == self.pinned_origin:
            return self.pinned_ip
        return _assert_public_url(url)

    def open_pinned(self, req, connection, **connection_args):
        ip = self.address_for(req.full_url)
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
    if not pinned_ip:
        pinned_ip = _assert_public_url(url)
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


def _field_text(value):
    """Turn the common ATS description shapes into readable text."""
    if isinstance(value, dict):
        return _field_text(value.get('description') or value.get('name') or '')
    if isinstance(value, list):
        return '\n'.join(text for item in value if (text := _field_text(item)))
    if value is None:
        return ''
    text = str(value).strip()
    return _visible_text(text)[0] if '<' in text and '>' in text else text


def _date(value):
    """Normalize documented ATS dates without changing their stated meaning."""
    value = str(value or '').strip()
    if re.fullmatch(r'\d{8}', value):
        value = f'{value[:4]}-{value[4:6]}-{value[6:]}'
    candidate = value[:10]
    try:
        return datetime.date.fromisoformat(candidate).isoformat()
    except ValueError:
        return ''


def _api_json(endpoint, headers=None):
    status, _final_url, _content_type, charset, body = _request(
        endpoint, TIMEOUTS['direct'], headers or {'User-Agent': UA, 'Accept': 'application/json'})
    if not 200 <= status < 300:
        raise RuntimeError(f'ATS API HTTP {status}')
    return status, json.loads(_decode(body, charset))


def _104(url):
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or '').lower()
    match = re.fullmatch(r'/job/([A-Za-z0-9]+)/*', parts.path)
    if not (host == '104.com.tw' or host.endswith('.104.com.tw')) or not match:
        return None
    code = match.group(1)
    endpoint = f"{API_ROOTS['104'].rstrip('/')}/{code}"
    headers = {'User-Agent': UA, 'Accept': 'application/json',
               'Referer': f'https://www.104.com.tw/job/{code}'}
    status, _final_url, _content_type, charset, body = _request(
        endpoint, TIMEOUTS['direct'], headers)
    if status == 404:
        try:
            error = json.loads(_decode(body, charset))
        except (ValueError, UnicodeError):
            error = {}
        if isinstance(error, dict) and (error.get('error') or {}).get('code') == 11201:
            return PageResult(url, 'closed', via='104', http_status=status)
    if not 200 <= status < 300:
        raise RuntimeError(f'104 API HTTP {status}')
    payload = json.loads(_decode(body, charset))
    data = payload.get('data') or {}
    if data.get('switch') == 'off':
        return PageResult(url, 'closed', via='104', http_status=status)
    header = data.get('header') or {}
    detail = data.get('jobDetail') or {}
    condition = data.get('condition') or {}
    rows = [
        ('職稱', header.get('jobName')),
        ('公司', header.get('custName')),
        ('曝光日期', header.get('appearDate')),
        ('地點', _field_text(detail.get('addressRegion')) + _field_text(detail.get('addressDetail'))),
        ('薪資', detail.get('salary')),
        ('工作內容', detail.get('jobDescription')),
        ('工作經歷', condition.get('workExp')),
        ('學歷', condition.get('edu')),
        ('其他條件', condition.get('other')),
    ]
    text = '\n'.join(f'{label}: {_field_text(value)}' for label, value in rows
                     if _field_text(value))
    if not text:
        raise RuntimeError('104 API returned no job text')
    return PageResult(url, 'ok', text=text, via='104',
                      title=_field_text(header.get('jobName')),
                      posted_at=_date(header.get('appearDate')),
                      posted_source='104 appearDate' if _date(header.get('appearDate')) else '',
                      http_status=status, verified_live=data.get('switch') == 'on')


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


def _direct(url, pinned_ip=''):
    headers = {'User-Agent': UA, 'Accept-Language': accept_language()}
    status, final_url, content_type, charset, body = _request(
        url, TIMEOUTS['direct'], headers, pinned_ip)
    if status in (404, 410):
        return PageResult(url, 'closed', via='direct', http_status=status)
    if not 200 <= status < 400:
        raise RuntimeError(f'HTTP {status}')
    raw = _decode(body, charset)
    posted = ''
    if content_type == 'text/html' or raw.lstrip().startswith('<'):
        text, title = _visible_text(raw)
        posted = _job_posting_date(raw)
    else:
        text, title = raw.strip(), ''
    if not text:
        raise RuntimeError('response contained no readable text')
    return PageResult(url, 'ok', text=text, via='direct', title=title,
                      http_status=status, posted_at=posted,
                      posted_source='JobPosting datePosted' if posted else '')


def _reader(url):
    reader_url = READER_ROOT.rstrip('/') + '/' + url
    status, _final_url, _content_type, charset, body = _request(
        reader_url, TIMEOUTS['reader'], {'User-Agent': UA})
    if not 200 <= status < 400:
        raise RuntimeError(f'reader HTTP {status}')
    text = _decode(body, charset).strip()
    if not text:
        raise RuntimeError('reader returned no text')
    title = ''
    if text.startswith('Title:'):
        title = text.splitlines()[0][len('Title:'):].strip()
    return PageResult(url, 'ok', text=text, via='reader', title=title,
                      http_status=status)


def _lever(url):
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or '').lower()
    if not (host == 'lever.co' or host.endswith('.lever.co')):
        return None
    path = [urllib.parse.unquote(part) for part in parts.path.split('/') if part]
    if len(path) < 2:
        return None
    endpoint = f"{API_ROOTS['lever'].rstrip('/')}/{path[0]}/{path[1]}?mode=json"
    status, data = _api_json(endpoint)
    title = str(data.get('text') or data.get('title') or '').strip()
    sections = []
    description = data.get('descriptionPlain') or data.get('description')
    if description:
        sections.append(_field_text(description))
    for item in data.get('lists') or []:
        if isinstance(item, dict):
            heading = str(item.get('text') or '').strip()
            content = str(item.get('content') or item.get('contentPlain') or '').strip()
            visible = _field_text(content)
            if heading or visible:
                sections.append('\n'.join(part for part in (heading, visible) if part))
    text = '\n'.join(part for part in [title, *sections] if part).strip()
    if not text:
        raise RuntimeError('Lever API returned no job text')
    return PageResult(url, 'ok', text=text, via='lever', title=title, http_status=status)


def _ashby(url):
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or '').lower()
    path = [urllib.parse.unquote(part) for part in parts.path.split('/') if part]
    if host not in ('jobs.ashbyhq.com', 'jobs.eu.ashbyhq.com') or len(path) < 2:
        return None
    endpoint = f"{API_ROOTS['ashby'].rstrip('/')}/{urllib.parse.quote(path[0])}"
    status, payload = _api_json(endpoint)
    rows = payload.get('jobs') or []
    wanted = urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                                      parts.path.rstrip('/'), '', ''))
    match = None
    for row in rows:
        job_url = urllib.parse.urlsplit(str(row.get('jobUrl') or ''))
        actual = urllib.parse.urlunsplit((job_url.scheme.lower(), job_url.netloc.lower(),
                                          job_url.path.rstrip('/'), '', ''))
        if actual == wanted:
            match = row
            break
    if not match:
        raise RuntimeError('Ashby API did not return this posting')
    title = _field_text(match.get('title'))
    sections = [title, _field_text(match.get('descriptionPlain') or match.get('descriptionHtml'))]
    text = '\n'.join(section for section in sections if section).strip()
    if not text:
        raise RuntimeError('Ashby API returned no job text')
    posted_at = _date(match.get('publishedAt'))
    return PageResult(url, 'ok', text=text, via='ashby', title=title,
                      posted_at=posted_at,
                      posted_source='Ashby publishedAt (last published)' if posted_at else '',
                      http_status=status)


def _greenhouse(url):
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or '').lower()
    match = re.fullmatch(r'/([^/]+)/jobs/(\d+)/*', parts.path)
    if host not in ('boards.greenhouse.io', 'job-boards.greenhouse.io',
                    'job-boards.eu.greenhouse.io') or not match:
        return None
    board, job_id = match.groups()
    endpoint = f"{API_ROOTS['greenhouse'].rstrip('/')}/{urllib.parse.quote(board)}/jobs/{job_id}?content=true"
    status, data = _api_json(endpoint)
    title = _field_text(data.get('title'))
    description = _field_text(data.get('content'))
    text = '\n'.join(part for part in (title, description) if part).strip()
    if not text:
        raise RuntimeError('Greenhouse API returned no job text')
    posted_at = _date(data.get('first_published'))
    return PageResult(url, 'ok', text=text, via='greenhouse', title=title,
                      posted_at=posted_at,
                      posted_source='Greenhouse first_published' if posted_at else '',
                      http_status=status)


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


def fetch_many(urls, fetch_page=None):
    """照順序回每個網址的 PageResult;抓的時候出例外也回一個 unknown,不讓一個壞網址中斷整批。"""
    from concurrent.futures import ThreadPoolExecutor
    fetch_page = fetch_page or fetch
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


def fetch(url):
    """Return page text, the route used, and an explicit ok/closed/unknown status."""
    try:
        parts = urllib.parse.urlsplit(str(url or '').strip())
        _ = parts.port
    except ValueError as error:
        return PageResult(str(url or ''), 'unknown', errors=(f'malformed URL: {str(error)[:180]}',))
    if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password:
        return PageResult(str(url or ''), 'unknown', errors=('URL must be an HTTP(S) URL without credentials',))

    errors = []
    for attempt in range(RETRIES):
        try:
            pinned_ip = _assert_public_url(parts.geturl())
        except Exception as error:  # noqa: BLE001 — 查不到位址、不是公開網址:原因照實放進這一頁的結果
            return PageResult(parts.geturl(), 'unknown', errors=(f'target blocked: {str(error)[:180]}',))
        for via, route in (('104', _104), ('lever', _lever),
                           ('ashby', _ashby), ('greenhouse', _greenhouse)):
            try:
                result = route(parts.geturl())
                if result and result.status == 'closed':
                    return result
                if result and result.readable:
                    return result
            except Exception as error:  # noqa: BLE001 — 換下一條路抓;每一條的原因照實放進結果
                errors.append(f'{via}: {type(error).__name__}: {str(error)[:180]}')
        routes = (('direct', lambda url: _direct(url, pinned_ip)),
                  ('reader', _reader), ('ego', _ego))
        for via, route in routes:
            try:
                result = route(parts.geturl())
                if result.status == 'closed':
                    return result
                if result.readable:
                    return result
                errors.append(f'{via}: no readable text')
            except Exception as error:  # noqa: BLE001 — 換下一條路抓;每一條的原因照實放進結果
                errors.append(f'{via}: {type(error).__name__}: {str(error)[:180]}')
        if attempt + 1 < RETRIES:
            time.sleep(RETRY_WAIT)
    return PageResult(parts.geturl(), 'unknown', errors=tuple(errors))


def main(argv=None):
    """給沒有瀏覽器的 agent 用(#287):`python3 page_fetch.py <網址>`,印出程式抓到的文字;抓不到照實說每條路敗在哪。"""
    import argparse
    ap = argparse.ArgumentParser(description='抓一個網頁的文字(直接抓 → 閱讀代理 → agent 的瀏覽器 ego)')
    ap.add_argument('url')
    result = fetch(ap.parse_args(argv).url)
    if result.readable:
        print(f'狀態: ok;路徑: {result.via};標題: {result.title}\n\n{result.text}')
        return 0
    if result.status == 'closed':
        print(f'狀態: closed(職缺已下架,HTTP {result.http_status or "無"};路徑: {result.via})')
        return 1
    print('狀態: 讀不到這一頁(可能要登入、或網站擋程式讀取);不要猜內容,要登入的照規矩回報。\n'
          + '\n'.join('- ' + e for e in result.errors))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
