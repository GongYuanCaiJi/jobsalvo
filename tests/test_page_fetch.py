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
        import time as _time
        import web.fetch as page_fetch
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

class AcceptLanguage(unittest.TestCase):
    """跟網站要哪種語言的頁面,照設定的履歷語言;中文、英文的人跟原本一樣。"""

    def test_follows_resume_languages(self):
        import web.fetch as page_fetch
        self.assertEqual(page_fetch.accept_language(['zh', 'en']), 'zh-TW,zh;q=0.9,en;q=0.8')
        self.assertEqual(page_fetch.accept_language(['en']), 'en')
        self.assertEqual(page_fetch.accept_language(['ja', 'en']), 'ja,en;q=0.9')

class PageFetchRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.chrome_requests = 0

        HTML, TEXT = 'text/html; charset=utf-8', 'text/plain; charset=utf-8'
        routes = {   # 路徑 → (狀態碼, 內容類型, 內容);內容類型 None = 只回狀態碼
            '/blocked': (403, None, b''),
            '/gone404': (404, None, b''),
            '/gone410': (410, None, b''),
            '/title-only': (200, HTML, b'<html><head><title>JobSite Remote Hiring</title></head>'
                                       b'<body><div id="app"></div><script src="/app.js"></script></body></html>'),
            '/oversized': (200, HTML, b'<html><head><title>Large job</title></head><body>'
                                      + b'x' * 4096 + b'</body></html>'),
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
        import web.fetch as page_fetch

        with patch.object(page_fetch, 'READER_ROOT', self.base + '/reader'), \
             patch.object(page_fetch, 'RETRY_WAIT', 0):
            result = page_fetch.request_page(self.base + '/blocked')

        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.via, 'reader')
        self.assertIn('active role handles expired credentials', result.text)
        self.assertEqual(self.requests, ['/blocked', '/reader/http://127.0.0.1:%d/blocked' % self.server.server_port])

    def test_page_with_only_its_title_falls_back_to_reader(self):
        import web.fetch as page_fetch

        with patch.object(page_fetch, 'READER_ROOT', self.base + '/reader'), \
             patch.object(page_fetch, 'RETRY_WAIT', 0):
            result = page_fetch.request_page(self.base + '/title-only')

        self.assertEqual(result.via, 'reader')
        self.assertIn('active role handles expired credentials', result.text)

    def test_oversized_page_returns_readable_prefix_and_its_boundary(self):
        import chrome_door
        import web.fetch as page_fetch

        with patch.object(chrome_door, 'PAGE_MATERIAL_CHAR_LIMIT', 1024), \
             patch.object(page_fetch, 'RETRY_WAIT', 0):
            result = page_fetch.request_page(self.base + '/oversized')

        self.assertEqual(result.status, 'partial')
        self.assertTrue(result.readable)
        self.assertEqual(result.title, 'Large job')
        self.assertTrue(result.text.startswith('xxxx'))
        self.assertLess(len(result.text), 4096)
        self.assertIn('其餘內容未提供', ' '.join(result.errors))
        self.assertIn('由 agent 判斷', ' '.join(result.errors))
        self.assertLessEqual(len(json.dumps(result.__dict__, ensure_ascii=False)), 1024)

    def test_title_only_page_is_unreadable_on_every_route(self):
        import web.fetch as page_fetch

        self.assertEqual(page_fetch._visible_text('<html><head><title>JobSite Remote Hiring</title></head>'
                                                  '<body><div id="app"></div></body></html>'),
                         ('', 'JobSite Remote Hiring'))
        for via in ('direct', 'reader', 'ego'):
            shell = page_fetch.PageResult('https://ex.test/a', 'ok', text=' \n', title='JobSite Remote Hiring', via=via)
            self.assertFalse(shell.readable, via)
            same = page_fetch.PageResult('https://ex.test/a', 'ok', text='JobSite Remote Hiring',
                                         title='JobSite Remote Hiring', via=via)
            self.assertTrue(same.readable, f'{via}: 頁上的字剛好跟標題一樣也是讀到的字,交給 agent 判斷')

    def test_reader_keeps_only_the_markdown_content(self):
        import web.fetch as page_fetch

        with patch.object(page_fetch, '_request', return_value=(
                200, '', 'text/plain', 'utf-8',
                b'Title: JobSite\n\nURL Source: https://ex.test/a\n\nMarkdown Content:\n')):
            shell = page_fetch._reader('https://ex.test/a')
        self.assertEqual((shell.title, shell.text), ('JobSite', ''))
        self.assertFalse(shell.readable)

    def test_404_and_410_are_pages_the_program_could_not_read_not_closed_jobs(self):
        import web.fetch as page_fetch

        for path, code in (('/gone404', 404), ('/gone410', 410)):
            with self.subTest(path=path):
                result = page_fetch.request_page(self.base + path)
            self.assertEqual(result.status, 'unknown')
            self.assertIn(f'direct: RuntimeError: HTTP {code}', result.errors)
            self.assertFalse(result.readable)

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
        import web.fetch as page_fetch

        with patch.object(page_fetch, 'READER_ROOT', self.base + '/reader-fail'), \
             patch.object(page_fetch, 'RETRY_WAIT', 0), self.fake_ego():
            result = page_fetch.request_page(self.base + '/retry')

        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.via, 'ego')
        self.assertIn('Retry recovered', result.text)
        self.assertEqual(self.requests.count('/retry'), 4)
        self.assertEqual(PageFetchRoutes.chrome_requests, 2)

    def test_non_public_targets_and_redirects_are_blocked(self):
        import web.fetch as page_fetch
        blocked_urls = ('http://127.0.0.1/job', 'http://169.254.169.254/latest',
                        'http://[::1]/job', 'http://service.local/job',
                        'https://jobs.example:8080/job')
        with patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '0'}):
            for url in blocked_urls:
                with self.subTest(url=url):
                    result = page_fetch.request_page(url)
                    self.assertEqual(result.status, 'unknown')
                    self.assertTrue(result.errors)
            private_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.9', 443))]
            with patch.object(page_fetch.socket, 'getaddrinfo', return_value=private_dns):
                result = page_fetch.request_page('https://jobs.example/job')
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(self.requests, [])

    def test_malformed_url_is_unknown_without_raising(self):
        import web.fetch as page_fetch

        result = page_fetch.request_page('http://[abc')
        self.assertEqual(result.status, 'unknown')
        self.assertIn('malformed URL', result.errors[0])

    def test_direct_redirect_to_local_target_is_blocked_before_request(self):
        import web.fetch as page_fetch
        with patch.object(page_fetch, 'READER_ROOT', self.base + '/reader-deny'), \
             patch.object(page_fetch, 'RETRIES', 1), patch.object(page_fetch, 'RETRY_WAIT', 0):
            result = page_fetch.request_page(self.base + '/redirect-internal')

        self.assertEqual(result.status, 'unknown')
        self.assertNotIn('/admin', self.requests)
        self.assertEqual(self.requests.count('/redirect-internal'), 1)      # 測試裡沒有 ego,只有直接抓那一次

    def test_an_ego_page_that_ends_up_on_a_local_address_is_not_used(self):
        # ego 照網頁導向走:最後停在內網、本機的頁不收(fetch 開始前網址本身已確認是公開的)
        import chrome_door
        import web.fetch as page_fetch
        landed = {'url': 'http://10.0.0.9/admin', 'title': 'admin', 'text': 'internal secrets'}
        with patch.object(chrome_door.EgoDoor, 'read_pages', lambda _d, urls, *a, **k: {u: dict(landed) for u in urls}), \
             patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '0'}):
            with self.assertRaises(Exception):
                page_fetch._ego_now('https://jobs.example/job')

    def test_ego_reads_javascript_generated_text(self):
        import web.fetch as page_fetch

        with patch.object(page_fetch, 'READER_ROOT', self.base + '/reader-fail'), \
             patch.object(page_fetch, 'RETRIES', 1), patch.object(page_fetch, 'RETRY_WAIT', 0), \
             self.fake_ego(rendered='Dynamic job content'):
            result = page_fetch.request_page(self.base + '/script')

        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.via, 'ego')
        self.assertEqual(result.text, 'Dynamic job content')

    def test_https_handler_opens_a_pinned_connection(self):
        # Python 3.14 的 HTTPSHandler 沒有 _check_hostname 了;以前照抄那個屬性,https 的職缺頁一律抓不到
        import urllib.request
        import web.fetch as page_fetch
        handler = page_fetch._PinnedHTTPSHandler('https://jobs.example.com/a', '203.0.113.9')
        opened = {}

        def fake_do_open(connect, request):
            opened['conn'] = connect('jobs.example.com', timeout=5)

        with patch.object(handler, 'do_open', fake_do_open):
            handler.https_open(urllib.request.Request('https://jobs.example.com/a'))
        self.assertEqual(opened['conn'].pinned_ip, '203.0.113.9')

    def test_connection_uses_the_ip_that_was_validated(self):
        import web.fetch as page_fetch

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
                result = page_fetch.request_page(url)
            self.assertEqual(result.status, 'ok')
            self.assertIn('validated address', result.text)
            self.assertEqual(len(hostname_calls), 1)
            self.assertEqual(first.requests, ['/job'])
            self.assertEqual(second.requests, [])
        finally:
            for server, thread in ((first, first_thread), (second, second_thread)):
                server.shutdown(); server.server_close(); thread.join(timeout=2)


class FetchMany(unittest.TestCase):
    """好幾個缺一起抓:一個慢或壞的頁不卡住整批,順序照原本的。"""

    def test_parallel_in_order_and_errors_become_unknown(self):
        import time as _t
        import web.fetch as page_fetch

        def slow(url):
            _t.sleep(0.4)
            if url.endswith('/bad'):
                raise RuntimeError('boom')
            return page_fetch.PageResult(url, 'ok', text=url)
        urls = [f'https://example.test/{i}' for i in range(5)] + ['https://example.test/bad']
        t0 = _t.time()
        got = page_fetch.read_batch(urls, slow)
        self.assertLess(_t.time() - t0, 1.2)          # 一個一個抓要 2.4 秒
        self.assertEqual([g.url for g in got], urls)
        self.assertEqual(got[-1].status, 'unknown')
        self.assertIn('boom', got[-1].errors[0])
        self.assertEqual(page_fetch.read_batch([], slow), [])

if __name__ == '__main__':
    unittest.main()
