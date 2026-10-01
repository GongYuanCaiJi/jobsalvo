import json
import os
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
