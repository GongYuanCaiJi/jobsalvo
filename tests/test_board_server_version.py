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


class BackgroundBoardHandsOverToLaunchd(unittest.TestCase):
    """安裝指令用 nohup 在背景起的看板沒有終端機可以按 Ctrl-C:打開開機自動啟動時要有人把它停掉,
    不然兩個搶同一個埠,launchd 那個每 30 秒重試一次、永遠起不來。launchctl 一律換成假的,不真的跑。"""

    def _run_main(self, home, ps_command, ppid=1):
        import signal
        calls = []
        alive = {'pid': True}

        def run(argv, *a, **k):
            calls.append(list(argv))
            return mock.Mock(returncode=0, stdout=ps_command if argv[0] == 'ps' else '', stderr='')

        def kill(pid, sig):
            calls.append(['kill', pid, sig])
            if sig == 0 and not alive['pid']:
                raise ProcessLookupError
            if sig == signal.SIGTERM:
                alive['pid'] = False
        with mock.patch.object(install_service.subprocess, 'run', side_effect=run), \
                mock.patch.object(install_service.os, 'kill', side_effect=kill), \
                mock.patch.object(install_service.os, 'getppid', return_value=ppid), \
                mock.patch.object(install_service.cf, 'HOME', home), \
                mock.patch.object(install_service, 'PLIST', os.path.join(home, 'x.plist')), \
                mock.patch('time.sleep'), \
                mock.patch.object(sys, 'argv', ['install_service.py']):
            install_service.main()
        return calls

    def test_installing_autostart_stops_the_board_the_installer_started(self):
        import signal
        with tempfile.TemporaryDirectory(prefix='svc-') as home:
            with open(os.path.join(home, '.jobsalvo-server.pid'), 'w') as f:
                f.write('4321\n')
            calls = self._run_main(home, '/x/.venv/bin/python3 /x/tools/board_server.py --host 127.0.0.1 --port 8899')
            kill = calls.index(['kill', 4321, signal.SIGTERM])
            boot = next(i for i, c in enumerate(calls) if c[:2] == ['launchctl', 'bootstrap'])
            self.assertLess(kill, boot, '要先停掉背景的看板,launchd 起的那個才綁得到埠')
            self.assertFalse(os.path.exists(os.path.join(home, '.jobsalvo-server.pid')))

    def test_a_reused_pid_or_the_calling_board_is_left_alone(self):
        import signal
        with tempfile.TemporaryDirectory(prefix='svc-') as home:
            pidfile = os.path.join(home, '.jobsalvo-server.pid')
            for command, ppid in (('/usr/bin/some-other-program', 1),                     # pid 被別的程式拿去用了
                                  ('/x/python3 /x/tools/board_server.py', 4321)):        # 看板設定頁按的:看板自己回完話會關
                with self.subTest(command=command):
                    with open(pidfile, 'w') as f:
                        f.write('4321')
                    calls = self._run_main(home, command, ppid=ppid)
                    self.assertNotIn(['kill', 4321, signal.SIGTERM], calls)


    def test_the_board_itself_shuts_down_and_clears_its_pid(self):
        # 設定頁按的:install_service 不動叫它的看板,看板回完話自己停掉伺服器(main 的收尾照常跑)
        stopped = threading.Event()

        class Server:
            def shutdown(self):
                stopped.set()
        with tempfile.TemporaryDirectory(prefix='svc-') as home:
            pidfile = os.path.join(home, '.jobsalvo-server.pid')
            with open(pidfile, 'w') as f:
                f.write(str(os.getpid()))
            with mock.patch.object(board_server.cf, 'HOME', home), \
                    mock.patch.object(board_server, 'SERVERS', [Server()], create=True):
                board_server.hand_over_to_launchd(delay=0).join(timeout=1)
                self.assertTrue(stopped.wait(1))
            self.assertFalse(os.path.exists(pidfile))


if __name__ == '__main__':
    unittest.main()
