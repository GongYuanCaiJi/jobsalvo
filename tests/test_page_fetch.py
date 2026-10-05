import json
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ORIGINAL_GETADDRINFO = socket.getaddrinfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401


class EgoFallbackLimit(unittest.TestCase):
    """連結檢查 6 條一起跑,退到 ego 的同一時間最多 EGO_FALLBACK_WORKERS 個工作區。"""

    def test_at_most_two_workspaces_at_once(self):
        import page_fetch, time as _time
        from concurrent.futures import ThreadPoolExecutor
        live, peak, lock = [0], [0], threading.Lock()

        def fake_now(url):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            _time.sleep(0.2)
            with lock:
                live[0] -= 1
            return page_fetch.PageResult(url, 'ok', text='x', via='ego')

        with patch.object(page_fetch, '_ego_now', side_effect=fake_now):
            with ThreadPoolExecutor(6) as pool:
                list(pool.map(page_fetch._ego, [f'https://ex.test/{i}' for i in range(12)]))
        self.assertEqual(peak[0], page_fetch.EGO_FALLBACK_WORKERS)


class CommandLine(unittest.TestCase):
    """找缺的 agent 沒有瀏覽器(#287):要讀某一頁就跑 page_fetch.py <網址>,程式抓好印出來。"""

    def test_prints_the_page_text_and_says_plainly_when_it_cannot(self):
        import io, page_fetch
        from contextlib import redirect_stdout
        ok = page_fetch.PageResult('https://ex.test/a', 'ok', text='Careers: Backend Engineer', title='Jobs', via='reader')
        bad = page_fetch.PageResult('https://ex.test/b', 'unknown', errors=('direct: HTTP 401', 'reader: no readable text'))
        for result, code, want in ((ok, 0, ['Careers: Backend Engineer', 'reader']),
                                   (bad, 1, ['讀不到', 'HTTP 401', '登入'])):
            out = io.StringIO()
            with patch.object(page_fetch, 'fetch', return_value=result), redirect_stdout(out):
                self.assertEqual(page_fetch.main([result.url]), code)
            for w in want:
                self.assertIn(w, out.getvalue())


class AcceptLanguage(unittest.TestCase):
    """跟網站要哪種語言的頁面,照設定的履歷語言;中文、英文的人跟原本一樣。"""

    def test_follows_resume_languages(self):
        import page_fetch
        self.assertEqual(page_fetch.accept_language(['zh', 'en']), 'zh-TW,zh;q=0.9,en;q=0.8')
        self.assertEqual(page_fetch.accept_language(['en']), 'en')
        self.assertEqual(page_fetch.accept_language(['ja', 'en']), 'ja,en;q=0.9')


class PageFetchRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.chrome_requests = 0

        JSON, HTML, TEXT = 'application/json; charset=utf-8', 'text/html; charset=utf-8', 'text/plain; charset=utf-8'
        greenhouse = json.dumps({'title': 'Platform Engineer', 'content': '<p>Build platform systems.</p>',
                                 'first_published': '2026-09-03T10:00:00-07:00',
                                 'updated_at': '2026-09-04T10:00:00-07:00'}).encode()
        routes = {   # 路徑 → (狀態碼, 內容類型, 內容);內容類型 None = 只回狀態碼
            '/api/acme/fake-id?mode=json': (200, JSON, b'{"text":"Security Work","descriptionPlain":'
                                                       b'"Build controls for active internet services."}'),
            '/api/104/ABC1': (200, JSON, json.dumps({'data': {
                'switch': 'on', 'header': {'jobName': 'Platform Engineer', 'custName': 'Example', 'appearDate': '20260901'},
                'jobDetail': {'jobDescription': '<p>Build secure services.</p>'}}}).encode()),
            '/api/104/CLOSED': (200, JSON, json.dumps({'data': {'switch': 'off', 'header': {'jobName': 'Platform Engineer'}}}).encode()),
            '/api/ashby/acme': (200, JSON, json.dumps({'jobs': [{
                'title': 'Security Engineer', 'jobUrl': 'https://jobs.ashbyhq.com/acme/posting-1',
                'publishedAt': '2026-09-02T12:00:00.000Z', 'descriptionPlain': 'Investigate product security.'}]}).encode()),
            '/api/greenhouse/acme/jobs/123': (200, JSON, greenhouse),
            '/api/104/GONE': (404, JSON, b'{"error":{"code":11201}}'),
            '/blocked': (403, None, b''),
            '/gone404': (404, None, b''),
            '/gone410': (410, None, b''),
            '/script': (200, HTML, b'<html><head><script>setTimeout(function(){'
                                   b"document.body.innerText='Dynamic job content';},200);"
                                   b'</script></head><body></body></html>'),
        }

        class Handler(BaseHTTPRequestHandler):
            def send(self, code, kind=None, body=b'', headers=()):
                self.send_response(code)
                for k, v in ((('Content-Type', kind), ('Content-Length', str(len(body)))) if kind else ()) + tuple(headers):
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                PageFetchRoutes.requests.append(self.path)
                path = self.path
                if path.startswith('/api/greenhouse/acme/jobs/123?'):
                    path = '/api/greenhouse/acme/jobs/123'
                if path.startswith('/reader/'):
                    return self.send(200, TEXT, b'Title: Security Engineer\nJob Description: This active role handles expired credentials.')
                if path == '/redirect-internal':
                    return self.send(302, headers=[('Location', 'http://10.0.0.9/admin')])
                if path == '/retry':
                    if 'jobsalvo/' in self.headers.get('User-Agent', ''):
                        return self.send(503)
                    PageFetchRoutes.chrome_requests += 1
                    return self.send(200, HTML, b'<html><body>Retry recovered</body></html>'
                                     if PageFetchRoutes.chrome_requests == 2 else b'<html><body></body></html>')
                self.send(*routes.get(path, (404,)))

            def log_message(self, *_args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.requests.clear()
        PageFetchRoutes.chrome_requests = 0
        self.loopback = patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '1'})
        self.loopback.start()
        self.addCleanup(self.loopback.stop)

    def test_blocked_direct_fetch_falls_back_to_reader_text(self):
        import page_fetch

        with patch.object(page_fetch, 'READER_ROOT', self.base + '/reader'), \
             patch.object(page_fetch, 'RETRY_WAIT', 0):
            result = page_fetch.fetch(self.base + '/blocked')

        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.via, 'reader')
        self.assertIn('active role handles expired credentials', result.text)
        self.assertEqual(self.requests, ['/blocked', '/reader/http://127.0.0.1:%d/blocked' % self.server.server_port])

    def test_only_direct_404_and_410_are_hard_closed(self):
        import page_fetch

        for path, code in (('/gone404', 404), ('/gone410', 410)):
            with self.subTest(path=path):
                result = page_fetch.fetch(self.base + path)
            self.assertEqual(result.status, 'closed')
            self.assertEqual(result.http_status, code)
            self.assertEqual(result.via, 'direct')
        self.assertEqual(self.requests, ['/gone404', '/gone410'])

    def fake_ego(self, rendered=None):
        """假的 agent 的瀏覽器:用一般瀏覽器的 UA 讀本機假伺服器,回 read_pages 的樣子;rendered 是跑完 JS 後的字。"""
        import chrome_door, urllib.request, re as _re

        def read_pages(_door, urls, *a, **k):
            out = {}
            for url in urls:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 ego'})
                body = urllib.request.urlopen(req, timeout=5).read().decode('utf-8', 'replace')  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected  本機假伺服器
                text = rendered if rendered is not None else _re.sub(r'<[^>]+>', ' ', body).strip()
                out[url] = {'url': url, 'title': '', 'text': text}
            return out
        return patch.object(chrome_door.EgoDoor, 'read_pages', read_pages)

    def test_full_route_ladder_retries_once_and_recovers(self):
        import page_fetch

        with patch.object(page_fetch, 'READER_ROOT', self.base + '/reader-fail'), \
             patch.object(page_fetch, 'RETRY_WAIT', 0), self.fake_ego():
            result = page_fetch.fetch(self.base + '/retry')

        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.via, 'ego')
        self.assertIn('Retry recovered', result.text)
        self.assertEqual(self.requests.count('/retry'), 4)
        self.assertEqual(PageFetchRoutes.chrome_requests, 2)

    def test_non_public_targets_and_redirects_are_blocked(self):
        import page_fetch
        blocked_urls = ('http://127.0.0.1/job', 'http://169.254.169.254/latest',
                        'http://[::1]/job', 'http://service.local/job',
                        'https://jobs.example:8080/job')
        with patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '0'}):
            for url in blocked_urls:
                with self.subTest(url=url):
                    result = page_fetch.fetch(url)
                    self.assertEqual(result.status, 'unknown')
                    self.assertTrue(result.errors)
            private_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.9', 443))]
            with patch.object(page_fetch.socket, 'getaddrinfo', return_value=private_dns):
                result = page_fetch.fetch('https://jobs.example/job')
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(self.requests, [])

    def test_malformed_url_is_unknown_without_raising(self):
        import page_fetch

        result = page_fetch.fetch('http://[abc')
        self.assertEqual(result.status, 'unknown')
        self.assertIn('malformed URL', result.errors[0])

    def test_direct_redirect_to_local_target_is_blocked_before_request(self):
        import page_fetch
        with patch.object(page_fetch, 'READER_ROOT', self.base + '/reader-deny'), \
             patch.object(page_fetch, 'RETRIES', 1), patch.object(page_fetch, 'RETRY_WAIT', 0):
            result = page_fetch.fetch(self.base + '/redirect-internal')

        self.assertEqual(result.status, 'unknown')
        self.assertNotIn('/admin', self.requests)
        self.assertEqual(self.requests.count('/redirect-internal'), 1)      # 測試裡沒有 ego,只有直接抓那一次

    def test_an_ego_page_that_ends_up_on_a_local_address_is_not_used(self):
        # ego 照網頁導向走:最後停在內網、本機的頁不收(fetch 開始前網址本身已確認是公開的)
        import chrome_door, page_fetch
        landed = {'url': 'http://10.0.0.9/admin', 'title': 'admin', 'text': 'internal secrets'}
        with patch.object(chrome_door.EgoDoor, 'read_pages', lambda _d, urls, *a, **k: {u: dict(landed) for u in urls}), \
             patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '0'}):
            with self.assertRaises(Exception):
                page_fetch._ego_now('https://jobs.example/job')

    def test_ego_reads_javascript_generated_text(self):
        import page_fetch

        with patch.object(page_fetch, 'READER_ROOT', self.base + '/reader-fail'), \
             patch.object(page_fetch, 'RETRIES', 1), patch.object(page_fetch, 'RETRY_WAIT', 0), \
             self.fake_ego(rendered='Dynamic job content'):
            result = page_fetch.fetch(self.base + '/script')

        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.via, 'ego')
        self.assertEqual(result.text, 'Dynamic job content')

    def test_lever_public_postings_api_precedes_the_job_page(self):
        result = self._api_fetch('lever', 'https://jobs.lever.co/acme/fake-id', '/api')

        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.via, 'lever')
        self.assertIn('Build controls for active internet services.', result.text)
        self.assertEqual(self.requests, ['/api/acme/fake-id?mode=json'])

    def test_ats_api_connects_to_the_address_validated_for_its_endpoint(self):
        import page_fetch
        api_dns_calls = []

        def rebinding_dns(host, port, *args, **kwargs):
            if host == 'jobs.lever.co':
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                         '', ('8.8.8.8', port))]
            if host == 'api-rebinding.test':
                api_dns_calls.append(host)
                address = '127.0.0.1' if len(api_dns_calls) == 1 else '127.0.0.2'
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                         '', (address, port))]
            return ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)

        endpoint = f'http://api-rebinding.test:{self.server.server_port}/api'
        with patch.dict(page_fetch.API_ROOTS, {'lever': endpoint}), \
             patch.object(socket, 'getaddrinfo', side_effect=rebinding_dns):
            result = page_fetch.fetch('https://jobs.lever.co/acme/fake-id')

        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.via, 'lever')
        self.assertEqual(api_dns_calls, ['api-rebinding.test'])
        self.assertEqual(self.requests, ['/api/acme/fake-id?mode=json'])

    def test_104_api_returns_page_text_and_exposure_date_first(self):
        result = self._api_fetch('104', 'https://www.104.com.tw/job/ABC1')

        self.assertEqual(result.via, '104')
        self.assertTrue(result.verified_live)
        self.assertIn('Build secure services.', result.text)
        self.assertEqual(result.posted_at, '2026-09-01')
        self.assertEqual(result.posted_source, '104 appearDate')
        self.assertEqual(self.requests, ['/api/104/ABC1'])

    def test_104_official_switch_off_is_closed_without_reading_page(self):
        result = self._api_fetch('104', 'https://www.104.com.tw/job/CLOSED')

        self.assertEqual(result.status, 'closed')
        self.assertEqual(result.via, '104')
        self.assertEqual(self.requests, ['/api/104/CLOSED'])

    def test_104_official_gone_code_is_closed_without_reading_page(self):
        result = self._api_fetch('104', 'https://www.104.com.tw/job/GONE')

        self.assertEqual(result.status, 'closed')
        self.assertEqual(result.via, '104')
        self.assertEqual(self.requests, ['/api/104/GONE'])

    def test_ashby_last_published_date_comes_from_the_public_posting_api(self):
        result = self._api_fetch('ashby', 'https://jobs.ashbyhq.com/acme/posting-1')

        self.assertEqual(result.via, 'ashby')
        self.assertIn('Investigate product security.', result.text)
        self.assertEqual(result.posted_at, '2026-09-02')
        self.assertEqual(result.posted_source, 'Ashby publishedAt (last published)')
        self.assertEqual(self.requests, ['/api/ashby/acme'])

    def test_greenhouse_uses_first_published_not_updated_at(self):
        result = self._api_fetch('greenhouse', 'https://boards.greenhouse.io/acme/jobs/123')

        self.assertEqual(result.via, 'greenhouse')
        self.assertIn('Build platform systems.', result.text)
        self.assertEqual(result.posted_at, '2026-09-03')
        self.assertEqual(result.posted_source, 'Greenhouse first_published')
        self.assertEqual(self.requests, ['/api/greenhouse/acme/jobs/123?content=true'])

    def test_greenhouse_eu_job_board_uses_public_api_first(self):
        result = self._api_fetch('greenhouse', 'https://job-boards.eu.greenhouse.io/acme/jobs/123')

        self.assertEqual(result.via, 'greenhouse')
        self.assertEqual(result.posted_at, '2026-09-03')
        self.assertEqual(self.requests, ['/api/greenhouse/acme/jobs/123?content=true'])

    def _api_fetch(self, kind, url, root=None):
        """用本機假的 ATS 官方 API(kind 那一家)抓 url;職缺網域一律當成公開位址。"""
        import page_fetch
        with patch.dict(page_fetch.API_ROOTS, {kind: self.base + (root or '/api/' + kind)}), \
             patch.object(socket, 'getaddrinfo', side_effect=self._public_dns):
            return page_fetch.fetch(url)

    def _public_dns(self, host, port, *args, **kwargs):
        if host in ('jobs.lever.co', 'www.104.com.tw', 'jobs.ashbyhq.com',
                    'boards.greenhouse.io', 'job-boards.eu.greenhouse.io'):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     '', ('8.8.8.8', port))]
        return ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)

    def test_https_handler_opens_a_pinned_connection(self):
        # Python 3.14 的 HTTPSHandler 沒有 _check_hostname 了;以前照抄那個屬性,https 的職缺頁一律抓不到
        import urllib.request
        import page_fetch
        handler = page_fetch._PinnedHTTPSHandler('https://jobs.example.com/a', '203.0.113.9')
        opened = {}

        def fake_do_open(connect, request):
            opened['conn'] = connect('jobs.example.com', timeout=5)

        with patch.object(handler, 'do_open', fake_do_open):
            handler.https_open(urllib.request.Request('https://jobs.example.com/a'))
        self.assertEqual(opened['conn'].pinned_ip, '203.0.113.9')

    def test_connection_uses_the_ip_that_was_validated(self):
        import page_fetch

        class AddressHandler(BaseHTTPRequestHandler):
            def __init__(self, body, *args, **kwargs):
                self.body = body
                super().__init__(*args, **kwargs)

            def do_GET(self):
                self.server.requests.append(self.path)
                body = self.body.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        def make_server(address, body, port=0):
            server = ThreadingHTTPServer((address, port),
                lambda *args, **kwargs: AddressHandler(body, *args, **kwargs))
            server.requests = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            return server, thread

        first, first_thread = make_server('127.0.0.1', 'validated address')
        try:
            second, second_thread = make_server('127.0.0.2', 'rebound address', first.server_port)
        except OSError as error:
            first.shutdown(); first.server_close(); first_thread.join(timeout=2)
            self.skipTest(f'loopback alias is unavailable: {error}')
        try:
            original = socket.getaddrinfo
            hostname_calls = []

            def changing_dns(host, port, *args, **kwargs):
                if host == 'rebinding.test':
                    hostname_calls.append(host)
                    address = '127.0.0.1' if len(hostname_calls) == 1 else '127.0.0.2'
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]
                return original(host, port, *args, **kwargs)

            url = f'http://rebinding.test:{first.server_port}/job'
            with patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '1'}), \
                 patch.object(socket, 'getaddrinfo', side_effect=changing_dns):
                result = page_fetch.fetch(url)
            self.assertEqual(result.status, 'ok')
            self.assertIn('validated address', result.text)
            self.assertEqual(len(hostname_calls), 1)
            self.assertEqual(first.requests, ['/job'])
            self.assertEqual(second.requests, [])
        finally:
            for server, thread in ((first, first_thread), (second, second_thread)):
                server.shutdown(); server.server_close(); thread.join(timeout=2)


class PostedAgeCache(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix='posted-age-test-'))
        self.board = os.path.join(self.directory, 'board.html')
        self.url = 'https://jobs.lever.co/acme/cache-test'
        import test_board
        test_board.make_board(self.board, {}, jobs=[{'id': self.url, 'target': 'Platform Engineer'}])


    def _patch_paths(self, posted_age):
        return patch.multiple(
            posted_age, CACHE=os.path.join(self.directory, 'posted-cache.json'),
            RESULT=os.path.join(self.directory, 'agent-result.json'),
            LOG=os.path.join(self.directory, 'agent.log'))

    def test_ats_date_skips_agent_and_cached_rerun_skips_fetch(self):
        import config as cf, page_fetch, posted_age
        page = page_fetch.PageResult(self.url, 'ok', text='Platform Engineer JD', via='lever',
                                     posted_at='2026-09-05', posted_source='ATS documented date')
        with self._patch_paths(posted_age), patch.object(cf, 'HOME', self.directory), \
             patch.object(page_fetch, 'fetch', return_value=page) as fetch, \
             patch.object(posted_age.ar, 'run', side_effect=AssertionError('API date skips agent')):
            self.assertEqual(posted_age.main(['--board', self.board]), 0)
            self.assertEqual(posted_age.main(['--board', self.board]), 0)

        self.assertEqual(fetch.call_count, 1)
        with open(self.board, encoding='utf-8') as f:
            data = __import__('board_doc').parse(f.read())['data']['jobs'][0]
        self.assertEqual(data['posted_at'], '2026-09-05')
        self.assertEqual(data['posted_src'], 'ATS documented date')

    def test_legacy_card_and_cache_date_are_rechecked_against_provider(self):
        import config as cf, page_fetch, posted_age, board_doc
        old = {'posted_at': '2024-01-02', 'posted_src': 'Published on'}
        import test_board
        test_board.make_board(self.board, {}, jobs=[{
            'id': self.url, 'target': 'Platform Engineer', **old,
        }])
        with open(os.path.join(self.directory, 'posted-cache.json'), 'w', encoding='utf-8') as f:
            json.dump({self.url: {'posted_at': old['posted_at'], 'src': old['posted_src'],
                                  'processed': True}}, f)
        current = page_fetch.PageResult(
            self.url, 'ok', text='Platform Engineer JD', via='ashby',
            posted_at='2026-09-05', posted_source='Ashby publishedAt (last published)')
        with self._patch_paths(posted_age), patch.object(cf, 'HOME', self.directory), \
             patch.object(page_fetch, 'fetch', return_value=current) as fetch, \
             patch.object(posted_age.ar, 'run', side_effect=AssertionError('provider date needs no agent')):
            self.assertEqual(posted_age.main(['--board', self.board]), 0)

        self.assertEqual(fetch.call_count, 1)
        with open(self.board, encoding='utf-8') as f:
            job = board_doc.parse(f.read())['data']['jobs'][0]
        self.assertEqual(job['posted_at'], current.posted_at)
        self.assertEqual(job['posted_src'], current.posted_source)

    def test_provider_date_is_saved_even_if_text_agent_fails(self):
        import config as cf, page_fetch, posted_age, board_doc
        import agent_report
        from types import SimpleNamespace
        api_url = self.url
        text_url = 'https://example.invalid/jobs/text-only'
        import test_board
        test_board.make_board(self.board, {}, jobs=[
            {'id': api_url, 'target': 'Platform Engineer'},
            {'id': text_url, 'target': 'Security Engineer'},
        ])
        results = {
            api_url: page_fetch.PageResult(
                api_url, 'ok', text='Platform Engineer JD', via='ashby',
                posted_at='2026-09-05', posted_source='Ashby publishedAt (last published)'),
            text_url: page_fetch.PageResult(
                text_url, 'ok', text='Security Engineer JD with no date', via='direct'),
        }
        failed = SimpleNamespace(ok=False, message=lambda: 'agent unavailable')
        with self._patch_paths(posted_age), patch.object(cf, 'HOME', self.directory), \
             patch.object(page_fetch, 'fetch', side_effect=lambda url: results[url]), \
             patch.object(posted_age.ar, 'run', return_value=failed), \
             patch.object(agent_report, 'report'):
            self.assertEqual(posted_age.main(['--board', self.board]), 1)

        with open(self.board, encoding='utf-8') as f:
            jobs = {job['id']: job for job in board_doc.parse(f.read())['data']['jobs']}
        self.assertEqual(jobs[api_url]['posted_at'], '2026-09-05')
        self.assertEqual(jobs[api_url]['posted_src'], 'Ashby publishedAt (last published)')
        with open(os.path.join(self.directory, 'posted-cache.json'), encoding='utf-8') as f:
            cache = json.load(f)
        self.assertEqual(cache[api_url]['posted_at'], '2026-09-05')
        self.assertTrue(cache[api_url]['provider_checked'])

    def test_fallback_agent_gets_fetched_text_without_browser_and_caches_empty_date(self):
        import config as cf, page_fetch, posted_age
        from types import SimpleNamespace
        page = page_fetch.PageResult(self.url, 'ok',
                                     text='Published September 7, 2026. Active platform engineer role.',
                                     via='reader')
        calls = []

        def run_agent(prompt, _log, _home, **kwargs):
            calls.append((prompt, kwargs))
            with open(posted_age.RESULT, 'w', encoding='utf-8') as f:
                json.dump({'dates': [], 'inaccessible': [
                    {'url': self.url, 'reason': '原文沒有刊登日期', 'need': ''}]}, f)
            return SimpleNamespace(ok=True, message=lambda: 'completed')

        with self._patch_paths(posted_age), patch.object(cf, 'HOME', self.directory), \
             patch.object(page_fetch, 'fetch', return_value=page) as fetch, \
             patch.object(posted_age.ar, 'run', side_effect=run_agent), \
             patch.object(posted_age, '_report'):
            self.assertEqual(posted_age.main(['--board', self.board]), 0)
            self.assertEqual(posted_age.main(['--board', self.board]), 0)

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn(page.text, calls[0][0])
        self.assertIn('不可開啟來源網址或使用瀏覽器', calls[0][0])
        self.assertFalse(calls[0][1]['browser_required'])
        self.assertFalse(calls[0][1]['web'])
        with open(os.path.join(self.directory, 'posted-cache.json'), encoding='utf-8') as f:
            cached = json.load(f)[self.url]
        self.assertTrue(cached['processed'])
        self.assertTrue(cached['date_complete'])
        self.assertNotIn('posted_at', cached)

    def test_only_dates_that_need_the_user_are_reported(self):
        import posted_age
        import agent_report
        items = [{'url': 'https://ex.test/a', 'reason': '原文沒有刊登日期', 'need': ''},
                 {'url': 'https://ex.test/b', 'reason': '要登入才看得到', 'need': '登入 104'}]
        with patch.object(agent_report, 'report') as report:
            posted_age._report(items, self.board)
        self.assertEqual([c.kwargs['job'] for c in report.call_args_list], ['https://ex.test/b'])

    def test_fallback_agent_date_is_written_from_supplied_text(self):
        import config as cf, page_fetch, posted_age, board_doc
        from types import SimpleNamespace
        page = page_fetch.PageResult(self.url, 'ok',
                                     text='Posted on September 7, 2026. Platform Engineer.',
                                     via='reader')

        def run_agent(prompt, _log, _home, **kwargs):
            self.assertIn(page.text, prompt)
            self.assertFalse(kwargs['browser_required'])
            self.assertFalse(kwargs['web'])
            with open(posted_age.RESULT, 'w', encoding='utf-8') as f:
                json.dump({'dates': [{'url': self.url, 'posted_at': '2026-09-07',
                                      'source': 'Posted on'}], 'inaccessible': []}, f)
            return SimpleNamespace(ok=True, message=lambda: 'completed')

        with self._patch_paths(posted_age), patch.object(cf, 'HOME', self.directory), \
             patch.object(page_fetch, 'fetch', return_value=page), \
             patch.object(posted_age.ar, 'run', side_effect=run_agent):
            self.assertEqual(posted_age.main(['--board', self.board]), 0)

        with open(self.board, encoding='utf-8') as f:
            job = board_doc.parse(f.read())['data']['jobs'][0]
        self.assertEqual(job['posted_at'], '2026-09-07')
        self.assertEqual(job['posted_src'], 'Posted on')


    def test_an_invalid_calendar_date_is_not_written_and_is_reported(self):
        """Agent 解釋文字日期；程式仍拒收不合法的日曆日期。"""
        import config as cf, page_fetch, posted_age, board_doc, agent_report
        from types import SimpleNamespace
        page = page_fetch.PageResult(self.url, 'ok', text='Posted on September 7, 2026. Platform Engineer.',
                                     via='reader')

        def run_agent(prompt, _log, _home, **kwargs):
            with open(posted_age.RESULT, 'w', encoding='utf-8') as f:
                json.dump({'dates': [{'url': self.url, 'posted_at': '2026-02-31', 'source': 'Posted on'}],
                           'inaccessible': []}, f)
            return SimpleNamespace(ok=True, message=lambda: 'completed')

        with self._patch_paths(posted_age), patch.object(cf, 'HOME', self.directory), \
             patch.object(page_fetch, 'fetch', return_value=page), \
             patch.object(posted_age.ar, 'run', side_effect=run_agent), \
             patch.object(agent_report, 'report') as report:
            self.assertEqual(posted_age.main(['--board', self.board]), 0)

        with open(self.board, encoding='utf-8') as f:
            job = board_doc.parse(f.read())['data']['jobs'][0]
        self.assertNotIn('posted_at', job)
        said = [c.args[1] for c in report.call_args_list if c.kwargs.get('job') == self.url]
        self.assertTrue(any('刊登日期' in m and '2026-02-31' in m and 'YYYY-MM-DD' in m for m in said), said)

    # 一輪最多交 20 頁給 agent、只給跟日期有關的那幾行;連一行日期都沒有的頁不問 agent。
    def test_agent_gets_small_prompt(self):
        import config as cf, page_fetch, posted_age
        import test_board
        from types import SimpleNamespace
        urls = [f'https://example.invalid/jobs/{i}' for i in range(25)] + ['https://example.invalid/jobs/nodate']
        test_board.make_board(self.board, {}, jobs=[{'id': u, 'target': 'Engineer'} for u in urls])
        filler = '\n'.join(f'Responsibility line {k}' for k in range(200))
        pages = {u: page_fetch.PageResult(u, 'ok', text=f'{filler}\nPosted on 2026-09-0{i % 9 + 1}', via='direct')
                 for i, u in enumerate(urls[:-1])}
        pages[urls[-1]] = page_fetch.PageResult(urls[-1], 'ok', text=filler, via='direct')
        seen = {}

        def agent(prompt, *a, **k):
            seen['prompt'] = prompt
            return SimpleNamespace(ok=False, message=lambda: 'stop here')
        with self._patch_paths(posted_age), patch.object(cf, 'HOME', self.directory), \
             patch.object(page_fetch, 'fetch', side_effect=lambda url: pages[url]), \
             patch.object(posted_age.ar, 'run', side_effect=agent), \
             patch('agent_report.report'):
            posted_age.main(['--board', self.board])
        prompt = seen['prompt']
        self.assertEqual(prompt.count('Posted on'), posted_age.AGENT_BATCH)
        self.assertNotIn('Responsibility line', prompt)
        self.assertNotIn(urls[-1], prompt)
        self.assertLess(len(prompt), 20000)


    # 程式猜「這頁沒有日期」只是省得問 agent,不能把卡上原本的日期刪掉(#235 在副本上刪掉 128 張)。
    def test_page_without_date_lines_keeps_existing_date(self):
        import config as cf, page_fetch, posted_age, board_doc
        import test_board
        test_board.make_board(self.board, {}, jobs=[{
            'id': self.url, 'target': 'Platform Engineer', 'posted_at': '2026-08-01', 'posted_src': 'lever'}])
        page = page_fetch.PageResult(self.url, 'ok', text='Platform Engineer\nBuild platforms', via='direct')
        with self._patch_paths(posted_age), patch.object(cf, 'HOME', self.directory), \
             patch.object(page_fetch, 'fetch', return_value=page), \
             patch.object(posted_age.ar, 'run', side_effect=AssertionError('沒有日期行不用問 agent')):
            self.assertEqual(posted_age.main(['--board', self.board]), 0)
            self.assertEqual(posted_age.main(['--board', self.board]), 0)   # 第二輪也不會再排隊、不會刪
        with open(self.board, encoding='utf-8') as f:
            job = board_doc.parse(f.read())['data']['jobs'][0]
        self.assertEqual(job.get('posted_at'), '2026-08-01')


class FetchMany(unittest.TestCase):
    """好幾個缺一起抓:一個慢或壞的頁不卡住整批,順序照原本的。"""

    def test_parallel_in_order_and_errors_become_unknown(self):
        import time as _t
        import page_fetch

        def slow(url):
            _t.sleep(0.4)
            if url.endswith('/bad'):
                raise RuntimeError('boom')
            return page_fetch.PageResult(url, 'ok', text=url)
        urls = [f'https://example.test/{i}' for i in range(5)] + ['https://example.test/bad']
        t0 = _t.time()
        got = page_fetch.fetch_many(urls, slow)
        self.assertLess(_t.time() - t0, 1.2)          # 一個一個抓要 2.4 秒
        self.assertEqual([g.url for g in got], urls)
        self.assertEqual(got[-1].status, 'unknown')
        self.assertIn('boom', got[-1].errors[0])
        self.assertEqual(page_fetch.fetch_many([], slow), [])


class JobPostingDate(unittest.TestCase):
    """刊登日期先讀 schema.org JobPosting 的 datePosted(標準欄位),不用問 agent。"""

    def test_reads_date_posted_from_json_ld(self):
        import page_fetch
        html = ('<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":['
                '{"@type":"Organization","name":"X"},{"@type":"JobPosting","title":"SRE",'
                '"datePosted":"2026-09-20T08:00:00Z"}]}</script><body>SRE</body></html>')
        self.assertEqual(page_fetch._job_posting_date(html), '2026-09-20')
        self.assertEqual(page_fetch._job_posting_date('<script type="application/ld+json">{bad</script>'), '')
        self.assertEqual(page_fetch._job_posting_date('<p>no data</p>'), '')


if __name__ == '__main__':
    unittest.main()
