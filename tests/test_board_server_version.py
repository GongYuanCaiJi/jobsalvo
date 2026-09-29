import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')))
import board_server
import install_service


class BoardServerVersion(unittest.TestCase):
    def test_occupied_port_cannot_start_pilot(self):
        with tempfile.TemporaryDirectory(prefix='board-bind-') as root:
            state = os.path.join(root, 'fake-board.html')
            with open(state, 'w', encoding='utf-8') as output:
                output.write('fake board')
            old_state, old_allow = board_server.STATE, board_server.ALLOW_AGENT[0]
            try:
                with mock.patch.object(sys, 'argv', ['board_server.py', '--state', state, '--port', '8898']), \
                        mock.patch.object(board_server, 'is_real', return_value=True), \
                        mock.patch.object(board_server, 'resolve_hosts', return_value=['127.0.0.1']), \
                        mock.patch.object(board_server, 'ThreadingHTTPServer', side_effect=OSError('occupied')), \
                        mock.patch.object(board_server, 'start_pilot') as pilot:
                    with self.assertRaises(OSError):
                        board_server.main()
                pilot.assert_not_called()
            finally:
                board_server.STATE = old_state
                board_server.ALLOW_AGENT[0] = old_allow

    def test_code_version_changes_with_server_or_browser_code(self):
        with tempfile.TemporaryDirectory(prefix='board-version-') as root:
            tools = os.path.join(root, 'tools')
            board = os.path.join(root, 'board')
            os.makedirs(tools)
            os.makedirs(board)
            server = os.path.join(tools, 'board_server.py')
            page = os.path.join(board, 'board.js')
            with open(server, 'w', encoding='utf-8') as f:
                f.write('version one')
            with open(page, 'w', encoding='utf-8') as f:
                f.write('page one')
            first = board_server.code_version(root)
            self.assertEqual(board_server.code_version(root), first)
            with open(page, 'w', encoding='utf-8') as f:
                f.write('page two')
            self.assertNotEqual(board_server.code_version(root), first)

    def test_watch_requests_shutdown_when_code_version_changes_and_launchd_restarts(self):
        stopped = threading.Event()

        class Server:
            def shutdown(self):
                stopped.set()

        with mock.patch.object(board_server, 'code_version', return_value='new-version'):
            watcher = threading.Thread(target=board_server._watch_code,
                                       args=([Server()], 'old-version', 0.01), daemon=True)
            watcher.start()
            watcher.join(timeout=1)
        self.assertTrue(stopped.is_set())
        self.assertIn('<key>KeepAlive</key><true/>', install_service.plist())
        self.assertIn('<key>JOBSALVO_LAUNCHD</key><string>1</string>', install_service.plist())


if __name__ == '__main__':
    unittest.main()
