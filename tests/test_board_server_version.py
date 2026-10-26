import errno
import os
import plistlib
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')))
import board_server
import install_service


class BoardServerVersion(unittest.TestCase):
    def test_auto_hosts_include_both_loopbacks_and_tailscale(self):
        with mock.patch.object(board_server, 'tailscale_ip', return_value='100.64.0.1'):
            self.assertEqual(board_server.resolve_hosts('auto'), ['127.0.0.1', '::1', '100.64.0.1'])

    def test_occupied_ipv6_loopback_port_prevents_start(self):
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as blocker:
            try:
                blocker.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                blocker.bind(('::1', 0))
            except OSError as error:
                if error.errno in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.EPROTONOSUPPORT):
                    self.skipTest('IPv6 loopback is unavailable')
                raise
            blocker.listen()
            port = blocker.getsockname()[1]

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(('127.0.0.1', port))
                except OSError as error:
                    if error.errno == errno.EADDRINUSE:
                        self.skipTest('random test port is already occupied on IPv4')
                    raise

            with tempfile.TemporaryDirectory(prefix='board-bind-') as root:
                state = os.path.join(root, 'fake-board.html')
                old_state = board_server.STATE
                old_serving = board_server.SERVING[0]
                try:
                    with mock.patch.object(sys, 'argv', ['board_server.py', '--state', state,
                                                         '--host', 'auto', '--port', str(port)]), \
                            mock.patch.object(board_server, 'is_real', return_value=True), \
                            mock.patch.object(board_server, 'first_run'), \
                            mock.patch.object(board_server, 'resolve_hosts',
                                              return_value=['127.0.0.1', '::1']), \
                            mock.patch.object(board_server, 'start_pilot') as pilot, \
                            mock.patch.object(board_server, 'PILOT', None), \
                            mock.patch.object(board_server, '_drop_own_pidfile'):
                        with self.assertRaises(OSError) as raised:
                            board_server.main()
                    self.assertEqual(raised.exception.errno, errno.EADDRINUSE)
                    pilot.assert_not_called()
                finally:
                    board_server.STATE = old_state
                    board_server.SERVING[0] = old_serving

    def test_unavailable_ipv6_keeps_ipv4_startup(self):
        with tempfile.TemporaryDirectory(prefix='board-bind-') as root:
            state = os.path.join(root, 'fake-board.html')
            old_state = board_server.STATE
            old_serving = board_server.SERVING[0]
            old_version = board_server.RUNNING_VERSION
            real_socket = socket.socket

            def without_ipv6(family, *args, **kwargs):
                if family == socket.AF_INET6:
                    raise OSError(errno.EAFNOSUPPORT, 'IPv6 is unavailable')
                return real_socket(family, *args, **kwargs)

            try:
                with mock.patch.object(sys, 'argv', ['board_server.py', '--state', state,
                                                     '--host', 'auto', '--port', '0']), \
                        mock.patch.object(board_server, 'is_real', return_value=True), \
                        mock.patch.object(board_server, 'first_run'), \
                        mock.patch.object(board_server, 'resolve_hosts',
                                          return_value=['127.0.0.1', '::1']), \
                        mock.patch.object(board_server.ThreadingHTTPServer, 'serve_forever'), \
                        mock.patch.object(board_server, 'migrate_marks'), \
                        mock.patch.object(board_server.cf, 'upgrade_file'), \
                        mock.patch.object(board_server, 'start_pilot') as pilot, \
                        mock.patch.object(board_server, 'code_version', return_value='test-version'), \
                        mock.patch.object(board_server, '_watch'), \
                        mock.patch.object(board_server, 'PILOT', None), \
                        mock.patch.object(board_server, '_drop_own_pidfile'), \
                        mock.patch('socket.socket', side_effect=without_ipv6):
                    board_server.main()
                pilot.assert_called_once()
            finally:
                board_server.STATE = old_state
                board_server.SERVING[0] = old_serving
                board_server.RUNNING_VERSION = old_version

    def test_occupied_port_cannot_start_pilot(self):
        with tempfile.TemporaryDirectory(prefix='board-bind-') as root:
            state = os.path.join(root, 'fake-board.html')
            with open(state, 'w', encoding='utf-8') as output:
                output.write('fake board')
            old_state = board_server.STATE
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
            watcher = threading.Thread(target=board_server._watch,
                                       args=([Server()], 'old-version', True, 0.01), daemon=True)
            watcher.start()
            watcher.join(timeout=1)
        self.assertTrue(stopped.is_set())
        launchd = plistlib.loads(install_service.plist().encode('utf-8'))
        self.assertIs(launchd['KeepAlive'], True)
        self.assertEqual(launchd['EnvironmentVariables']['JOBSALVO_LAUNCHD'], '1')


class BackgroundBoardHandsOverToLaunchd(unittest.TestCase):
    """安裝指令用 nohup 在背景起的看板沒有終端機可以按 Ctrl-C:這個資料夾裝好開機自動啟動(設定頁按的、終端機裝的都一樣)
    它就自己關,不然兩個搶同一個埠,launchd 那個每 30 秒重試一次、永遠起不來。"""

    def test_a_background_board_stops_once_autostart_is_installed(self):
        stopped = threading.Event()

        class Server:
            def shutdown(self):
                stopped.set()
        with tempfile.TemporaryDirectory(prefix='svc-') as home:
            plist = os.path.join(home, 'x.plist')
            with mock.patch.object(install_service, 'plist_path', return_value=plist), \
                    mock.patch.object(board_server, 'code_version', return_value='new-version'):
                watcher = threading.Thread(target=board_server._watch, args=([Server()], 'old-version', False, 0.01),
                                           daemon=True)
                watcher.start()
                self.assertFalse(stopped.wait(0.1), '程式更新了也不關:沒有 launchd 幫它用新程式重起')
                open(plist, 'w').close()
                watcher.join(timeout=1)
        self.assertTrue(stopped.is_set())

    def test_the_board_clears_the_installers_pid_only_when_it_is_its_own(self):
        with tempfile.TemporaryDirectory(prefix='svc-') as home:
            pidfile = os.path.join(home, '.jobsalvo-server.pid')
            with mock.patch.object(board_server.cf, 'HOME', home):
                for pid, kept in ((os.getpid(), False), (4321, True)):
                    with self.subTest(pid=pid):
                        with open(pidfile, 'w') as f:
                            f.write(f'{pid}\n')
                        board_server._drop_own_pidfile()
                        self.assertEqual(os.path.exists(pidfile), kept)
                board_server._drop_own_pidfile()             # 沒有這個檔:不是安裝指令起的


if __name__ == '__main__':
    unittest.main()
