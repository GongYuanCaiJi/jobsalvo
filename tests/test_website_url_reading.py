import contextlib
import io
import os
import socket
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import _env
from website_testbench import WebsiteTestBench

REAL_EGO = _env.REAL_EGO_BIN()

@unittest.skipIf(REAL_EGO and os.environ.get('JOBSALVO_EGO_INTEGRATION') != '1',
                 '本機真 ego 測試需明確設定 JOBSALVO_EGO_INTEGRATION=1')
class WebsiteUrlReading(unittest.TestCase):
    def setUp(self):
        if os.environ.get('JOBSALVO_EGO_INTEGRATION') == '1':
            import chrome_door
            self.enterContext(patch.object(chrome_door, 'ego_bin', _env.REAL_EGO_BIN))

    def test_known_website_uses_the_same_direct_page_reader(self):
        import web.fetch as page_fetch

        page = ('<!doctype html><title>一般職缺</title><body>'
                '<h1>一般職缺</h1><p>Fake website page content</p></body>')
        with WebsiteTestBench(pages={'/acme/fake-id': page}) as bench:
            port = urlsplit(bench.server.url('/')).port
            url = bench.server.url('/acme/fake-id').replace('127.0.0.1', 'jobs.lever.co')
            real_getaddrinfo = socket.getaddrinfo
            real_create_connection = socket.create_connection
            connections = []

            def resolve(_host, port, *args, **kwargs):
                return real_getaddrinfo('127.0.0.1', port, *args, **kwargs)

            def connect(address, *args, **kwargs):
                connections.append(address)
                return real_create_connection(address, *args, **kwargs)

            with patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '1'}), \
                 patch('socket.getaddrinfo', side_effect=resolve), \
                 patch('socket.create_connection', side_effect=connect), \
                 patch.object(page_fetch, 'RETRIES', 1), patch.object(page_fetch, 'RETRY_WAIT', 0):
                result = page_fetch.request_page(url)

            self.assertEqual(result.status, 'ok')
            self.assertEqual(result.via, 'direct')
            self.assertIn('Fake website page content', result.text)
            self.assertEqual(connections, [('127.0.0.1', port)])

    def test_oversized_url_material_returns_prefix_and_marks_the_unread_boundary(self):
        import web.fetch as page_fetch
        import chrome_door

        page = '<!doctype html><title>過長頁</title><body>' + ('字' * 100_001) + '</body>'
        with WebsiteTestBench(pages={'/long': page}) as bench:
            with patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '1'}), \
                 patch.object(page_fetch, 'RETRIES', 1), patch.object(page_fetch, 'RETRY_WAIT', 0):
                result = page_fetch.request_page(bench.server.url('/long'))

            self.assertEqual(result.status, 'partial')
            self.assertTrue(result.readable)
            self.assertEqual(result.title, '過長頁')
            self.assertTrue(result.text.startswith('字字'))
            self.assertLess(len(result.text), 100_001)
            self.assertIn('其餘內容未提供', ' '.join(result.errors))
            self.assertIn('由 agent 判斷', ' '.join(result.errors))
            self.assertLessEqual(len(json.dumps(result.__dict__, ensure_ascii=False)),
                                 chrome_door.PAGE_MATERIAL_CHAR_LIMIT)

    def test_hidden_structured_metadata_is_not_returned_as_page_text(self):
        import web.fetch as page_fetch

        page = ('<!doctype html><title>工程師</title><body><p>Visible job description</p>'
                '<script type="application/ld+json">'
                '{"@type":"JobPosting","datePosted":"2026-09-01"}</script></body>')
        with WebsiteTestBench(pages={'/job': page}) as bench:
            with patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '1'}), \
                 patch.object(page_fetch, 'RETRIES', 1), patch.object(page_fetch, 'RETRY_WAIT', 0):
                result = page_fetch.request_page(bench.server.url('/job'))

            self.assertEqual(result.status, 'ok')
            self.assertNotIn('2026-09-01', result.text)



class ResumePageReading(unittest.TestCase):
    def test_preparing_a_resume_from_a_cut_page_tells_the_agent_only_part_was_read(self):
        # #591「太長…一律照實標」:準備履歷以前把部分內容寫成「可讀」。從 cut_tailor 入口跑到要交給 agent 的那份指示。
        import cut_tailor
        import web.fetch as page_fetch
        page = ('<!doctype html><title>Cut page</title><body><p>Platform Engineer</p>'
                f'<script>{"x" * page_fetch.MAX_BYTES}</script></body>')
        _env.use_home(self)
        temp = self.enterContext(tempfile.TemporaryDirectory())
        board = os.path.join(temp, 'board.html')
        runs = os.path.join(temp, 'runs')
        with WebsiteTestBench(pages={'/cut': page}) as bench:
            url = bench.server.url('/cut')
            _env.make_board(board, {url: {'app': 'prep'}}, jobs=[{'id': url, 'target': 'Cut job'}])
            with patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '1'}), \
                    patch.object(cut_tailor, 'RUNS', runs), patch.object(cut_tailor, 'SP', temp), \
                    patch.object(cut_tailor, 'OUT', os.path.join(temp, 'out')), \
                    patch.object(cut_tailor, 'PIDF', os.path.join(temp, 'pid')), \
                    patch.object(cut_tailor, 'STATUSF', os.path.join(temp, 'status.json')), \
                    patch.object(cut_tailor.subprocess, 'Popen'), \
                    patch.object(sys, 'argv', ['cut_tailor.py', '--board', board]), \
                    contextlib.redirect_stdout(io.StringIO()):
                cut_tailor.main()
        run, = os.listdir(runs)
        with open(os.path.join(runs, run, 'prompt.txt'), encoding='utf-8') as source:
            prompt = source.read()
        self.assertIn('只讀到部分頁面', prompt)
        self.assertIn('其餘內容未提供', prompt)
        self.assertNotIn('程式擷取:可讀', prompt)




if __name__ == '__main__':
    unittest.main()
