import hashlib
import json
import multiprocessing
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
import board_check  # noqa: E402
import dev_browser  # noqa: E402


def _axe_source_process(home, fake, sha256, ready, release, download_barrier, results, gate=False):
    import builtins

    path = os.path.join(home, '.cache', 'jobsalvo', f'axe-{board_check.AXE_VERSION}.min.js')
    original_open, original_replace = builtins.open, os.replace

    class Response:
        def read(self):
            if download_barrier is not None and not gate:
                download_barrier.wait(20)
            return fake

    def urlopen(*_args, **_kwargs):
        return Response()

    def gated_open(file, mode='r', *args, **kwargs):
        handle = original_open(file, mode, *args, **kwargs)
        if gate and os.path.abspath(os.fspath(file)) == path and 'w' in mode:
            ready.set()
            if not release.wait(20):
                handle.close()
                raise TimeoutError('等待快取競爭測試解除閘門逾時')
        return handle

    def gated_replace(source, destination):
        if gate and os.path.abspath(destination) == path:
            ready.set()
            if not release.wait(20):
                raise TimeoutError('等待快取競爭測試解除閘門逾時')
        return original_replace(source, destination)

    try:
        with mock.patch.dict(os.environ, HOME=home), \
                mock.patch.object(board_check, 'AXE_SHA256', sha256), \
                mock.patch('urllib.request.urlopen', urlopen):
            if gate:
                with mock.patch('builtins.open', gated_open), \
                        mock.patch.object(os, 'replace', gated_replace):
                    source = board_check.axe_source()
            else:
                source = board_check.axe_source()
        results.put(('ok', source))
    except (OSError, RuntimeError, ValueError) as error:
        results.put(('error', repr(error)))


class BoardCheckSetup(unittest.TestCase):
    def test_confirmed_submission_case_seeds_its_own_weak_evidence_card(self):
        import board_doc
        import demo
        with tempfile.TemporaryDirectory(prefix='board-check-confirmed-') as directory:
            board = demo.build(os.path.join(directory, 'board.html'))

            result = board_check.confirmed_submission_evidence_case(board)

            with open(board, encoding='utf-8') as source:
                parsed = board_doc.parse(source.read())
            feedback = json.loads(parsed['fb'])
        self.assertEqual(result['source_ref'], 'email:board-check')
        self.assertNotIn('ev', feedback[result['id']])
        self.assertEqual(feedback[result['id']]['replies']['items'][0]['source_ref'],
                         'email:board-check')

    def test_seed_resume_waits_for_a_slow_local_server(self):
        state = {}

        class Handler(BaseHTTPRequestHandler):
            def respond(self, body=b'{}'):
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    # The red test hits the old 10s client timeout before this delayed reply.
                    return

            def do_GET(self):
                self.respond(json.dumps({'settings': {'resume': {'langs': ['zh']}}}).encode())

            def do_PUT(self):
                state['file'] = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                threading.Event().wait(10.25)
                self.respond()

            def do_POST(self):
                payload = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                state['settings'] = json.loads(payload)['settings']
                self.respond()

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            board_check.seed_check_resume(f'http://127.0.0.1:{server.server_address[1]}')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(state['file'], '示範履歷：供看板找缺規矩檢查。'.encode('utf-8'))
        self.assertEqual(state['settings']['resume']['resumes'][0]['files']['zh'],
                         'resume/board-check.txt')


BOARD = os.path.join(HERE, '..', 'board')
CHECK = board_check.__file__


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def board_buttons(js):
    """看板上按得到的每一種按鈕:點擊處理照 data-* 找的那幾種(board.js 的 closest('[data-x]'),在 click 監聽裡的)。"""
    out = set()
    for m in re.finditer(r"closest\('\[(data-[a-z0-9-]+)", js):
        ev = re.findall(r"addEventListener\('(\w+)'", js[:m.start()])
        if ev and ev[-1] == 'click':
            out.add(m.group(1))
    return out


def board_made(js):
    """看板畫得出來的 data-*(寫在 HTML 字串裡的屬性,不算處理程式裡的選擇器)。"""
    return set(re.findall(r"""(?<=[\s"'])(data-[a-z0-9-]+)""", js))


def check_refs(src):
    """board_check 找、按的每一種(T.absent 裡的是「不該出現」,不算)。"""
    return set(re.findall(r"\[(data-[a-z0-9-]+)", re.sub(r"T\.absent\('[^']*'", '', src)))


class EveryButtonIsPressed(unittest.TestCase):
    """刪掉看板上任何一顆按鈕,board_check 一定紅(#407 R10):不用開瀏覽器就證得出來。
    兩個方向:看板上每一種按鈕 board_check 都按得到(新加的按鈕沒有檢查就紅);board_check 按的每一種看板都還畫得出來
    (刪掉一顆按鈕,按它的那條檢查一定找不到 → 這裡先紅)。檢查裡找不到就失敗(T.press、不准 return '' 略過)。"""

    def setUp(self):
        self.js = _read(os.path.join(BOARD, 'board.js')) + _read(os.path.join(BOARD, 'header.html'))
        self.src = _read(CHECK)

    def test_every_button_on_the_board_is_pressed_by_some_check(self):
        self.assertEqual(sorted(board_buttons(self.js) - check_refs(self.src)), [])

    def test_every_button_a_check_presses_is_still_on_the_board(self):
        self.assertEqual(sorted(check_refs(self.src) - board_made(self.js)), [])

    def test_removing_any_button_from_the_board_turns_it_red(self):
        for kind in sorted(board_buttons(self.js)):
            with self.subTest(kind=kind):
                gone = re.sub(r"""(?<=[\s"'])""" + re.escape(kind) + r'\b', 'data-removed', self.js)
                self.assertIn(kind, check_refs(self.src) - board_made(gone))

    def test_checks_never_wait_a_fixed_time_or_skip_when_a_button_is_missing(self):
        self.assertNotIn('T.sleep(', self.src)
        self.assertEqual(re.findall(r"if\(![\w.()\[\]'\"]+\)\{?\s*(?:[\w.()\[\]'\"=]+;\s*)?return '';", self.src), [])
        self.assertNotIn('{click:function(){}}', self.src)

    def test_every_check_compiles(self):
        """每一條檢查的 JS 都編得過(語法錯不用等開瀏覽器才知道)。"""
        import subprocess
        bodies = json.dumps([['HELPERS', board_check.HELPERS], *[[c[0], c[1]] for c in board_check.CHECKS]])
        script = ("const A=Object.getPrototypeOf(async function(){}).constructor, bad=[];"
                  "for(const [n,b] of JSON.parse(require('fs').readFileSync(0,'utf8'))){try{new A('P',b);}catch(e){bad.push(n+': '+e.message);}}"
                  "if(bad.length){console.error(bad.join('\\n'));process.exit(1);}")
        r = subprocess.run(['node', '-e', script], input=bodies, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class CheckSelection(unittest.TestCase):
    def test_fast_keeps_explicitly_selected_slow_checks(self):
        selected = board_check.checks_for_run('步數', fast=True)

        self.assertTrue(selected)
        self.assertTrue(all(item[0].startswith('步數:') for item in selected))

    def test_fast_without_only_skips_slow_checks(self):
        selected = board_check.checks_for_run(fast=True)

        self.assertTrue(selected)
        self.assertFalse(any(item[0].startswith(board_check.SLOW) for item in selected))

    def test_no_matching_check_exits_nonzero_with_an_explanation(self):
        with mock.patch.object(sys, 'argv', ['board_check.py', '--only', 'no-such-check']):
            with self.assertRaises(SystemExit) as result:
                board_check.main()

        self.assertNotEqual(result.exception.code, 0)
        self.assertIn('沒有符合的檢查', str(result.exception))

    def test_empty_shard_exits_nonzero_with_an_explanation(self):
        with mock.patch.object(sys, 'argv', ['board_check.py', '--only', '第一屏', '--shard', '1/100']):
            with self.assertRaises(SystemExit) as result:
                board_check.main()

        self.assertNotEqual(result.exception.code, 0)
        self.assertIn('沒有分到檢查', str(result.exception))


class AxeSource(unittest.TestCase):
    """#616:好幾組同時第一次跑,後到的那組會讀到先到的那組寫到一半的快取檔,把空字串當 axe 裝進頁面。"""
    FAKE = '/* fake axe */window.axe={run:function(){}};'.encode('utf-8')

    def run_axe_source(self, cache, network=True):
        with tempfile.TemporaryDirectory(prefix='axe-source-') as home:
            path = os.path.join(home, '.cache', 'jobsalvo', f'axe-{board_check.AXE_VERSION}.min.js')
            if cache is not None:
                os.makedirs(os.path.dirname(path))
                with open(path, 'wb') as f:
                    f.write(cache)
            fetched = mock.Mock()
            fetched.return_value.read.return_value = self.FAKE
            if not network:
                fetched.side_effect = AssertionError('快取是好的,不該下載')
            with mock.patch.dict(os.environ, HOME=home), \
                    mock.patch.object(board_check, 'AXE_SHA256', hashlib.sha256(self.FAKE).hexdigest()), \
                    mock.patch('urllib.request.urlopen', fetched):
                source = board_check.axe_source()
            with open(path, 'rb') as f:
                saved = f.read()
            leftovers = [n for n in os.listdir(os.path.dirname(path)) if n != os.path.basename(path)]
        return source, saved, leftovers

    def test_an_empty_or_half_written_cache_is_not_used(self):
        for broken in (b'', self.FAKE[:10]):
            with self.subTest(cache=broken):
                source, saved, _ = self.run_axe_source(broken)
                self.assertEqual(source, self.FAKE.decode('utf-8'))
                self.assertEqual(saved, self.FAKE)

    def test_a_good_cache_is_used_without_downloading(self):
        source, _, _ = self.run_axe_source(self.FAKE, network=False)
        self.assertEqual(source, self.FAKE.decode('utf-8'))

    def test_a_cold_cache_downloads_once_and_leaves_no_temp_file(self):
        source, saved, leftovers = self.run_axe_source(None)
        self.assertEqual((source, saved, leftovers), (self.FAKE.decode('utf-8'), self.FAKE, []))

    def test_a_failed_replace_removes_the_temporary_file(self):
        with tempfile.TemporaryDirectory(prefix='axe-source-failure-') as home:
            path = os.path.join(home, '.cache', 'jobsalvo', f'axe-{board_check.AXE_VERSION}.min.js')
            fetched = mock.Mock()
            fetched.return_value.read.return_value = self.FAKE
            with mock.patch.dict(os.environ, HOME=home), \
                    mock.patch.object(board_check, 'AXE_SHA256', hashlib.sha256(self.FAKE).hexdigest()), \
                    mock.patch('urllib.request.urlopen', fetched), \
                    mock.patch.object(board_check.os, 'replace', side_effect=OSError('rename failed')):
                with self.assertRaisesRegex(OSError, 'rename failed'):
                    board_check.axe_source()
            self.assertEqual(os.listdir(os.path.dirname(path)), [])

    def test_concurrent_processes_share_a_cold_cache_safely(self):
        context = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory(prefix='axe-source-processes-') as home:
            path = os.path.join(home, '.cache', 'jobsalvo', f'axe-{board_check.AXE_VERSION}.min.js')
            os.makedirs(os.path.dirname(path))
            ready, release, results = context.Event(), context.Event(), context.Queue()
            download_barrier = context.Barrier(4)
            sha256 = hashlib.sha256(self.FAKE).hexdigest()
            processes = []
            writer = context.Process(
                target=_axe_source_process,
                args=(home, self.FAKE, sha256, ready, release, download_barrier, results, True),
            )
            processes.append(writer)
            try:
                writer.start()
                self.assertTrue(ready.wait(20), '寫入行程沒有到達快取寫入閘門')

                readers = [context.Process(
                    target=_axe_source_process,
                    args=(home, self.FAKE, sha256, ready, release, download_barrier, results),
                ) for _ in range(4)]
                processes.extend(readers)
                for process in readers:
                    process.start()
                reader_results = [results.get(timeout=20) for _ in readers]
                release.set()
                writer_result = results.get(timeout=20)
                for process in processes:
                    process.join(20)
                    self.assertEqual(process.exitcode, 0)

                expected = self.FAKE.decode('utf-8')
                self.assertEqual(reader_results, [('ok', expected)] * len(readers))
                self.assertEqual(writer_result, ('ok', expected))
                with open(path, 'rb') as cached:
                    self.assertEqual(cached.read(), self.FAKE)
                self.assertEqual(os.listdir(os.path.dirname(path)), [os.path.basename(path)])
            finally:
                release.set()
                for process in processes:
                    if process.pid is not None:
                        process.join(2)
                        if process.is_alive():
                            process.terminate()
                            process.join()
                results.close()
                results.join_thread()


class ChromeRun(unittest.TestCase):
    def test_reload_after_successful_result_does_not_override_it(self):
        class ReloadingPage:
            time_origin = 1

            def wait_for_load_state(self, *_args, **_kwargs):
                return None

            def evaluate(self, expression):
                if expression == 'performance.timeOrigin':
                    return self.time_origin
                if expression.startswith(dev_browser.DIALOGS):
                    return 1
                self.time_origin = 2
                return 42

        chrome = dev_browser.Chrome(ReloadingPage(), mock.Mock())
        self.assertEqual(chrome.run('42'), 42)

    def test_reload_after_prelude_reports_the_reload_error(self):
        class ReloadingPage:
            time_origin = 1

            def wait_for_load_state(self, *_args, **_kwargs):
                return None

            def evaluate(self, expression):
                if expression == 'performance.timeOrigin':
                    return self.time_origin
                if expression.startswith(dev_browser.DIALOGS):
                    self.time_origin = 2
                    return 1
                raise RuntimeError('axe is not defined')

        chrome = dev_browser.Chrome(ReloadingPage(), mock.Mock())
        with self.assertRaisesRegex(RuntimeError, '頁面在檢查途中重新載入了: axe is not defined'):
            chrome.run('axe.run()', prelude='window.axe={};')

    def test_load_wait_failure_does_not_hide_check_error(self):
        class LoadFailingPage:
            load_waits = 0

            def wait_for_load_state(self, *_args, **_kwargs):
                self.load_waits += 1
                if self.load_waits > 1:
                    raise RuntimeError('reload wait failed')

            def evaluate(self, expression):
                if expression == 'performance.timeOrigin':
                    return 1
                if expression.startswith(dev_browser.DIALOGS):
                    return 1
                raise RuntimeError('axe is not defined')

        chrome = dev_browser.Chrome(LoadFailingPage(), mock.Mock())
        with self.assertRaisesRegex(RuntimeError, 'axe is not defined'):
            chrome.run('axe.run()')

    def test_time_origin_failure_does_not_hide_check_error(self):
        class TimeOriginFailingPage:
            time_origin_reads = 0

            def wait_for_load_state(self, *_args, **_kwargs):
                return None

            def evaluate(self, expression):
                if expression == 'performance.timeOrigin':
                    self.time_origin_reads += 1
                    if self.time_origin_reads > 1:
                        raise RuntimeError('timeOrigin read failed')
                    return 1
                if expression.startswith(dev_browser.DIALOGS):
                    return 1
                raise RuntimeError('axe is not defined')

        chrome = dev_browser.Chrome(TimeOriginFailingPage(), mock.Mock())
        with self.assertRaisesRegex(RuntimeError, 'axe is not defined'):
            chrome.run('axe.run()')


class FirstScreenTargets(unittest.TestCase):
    def test_removed_page_targets_restore_actions_not_the_persistent_hint(self):
        removed = next(row for row in board_check.FIRST_SCREEN_TARGETS if row[0] == 'rm')

        self.assertEqual(removed[2], [
            '#app .rmback button[data-rmback]',
            '#app article[data-fid] [data-rm="0"]',
        ])
        self.assertIn("count&&Number(count.textContent)===0&&visible(empty)", board_check.FIRST_SCREEN_JS)
