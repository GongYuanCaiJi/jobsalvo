import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
import board_check  # noqa: E402


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
