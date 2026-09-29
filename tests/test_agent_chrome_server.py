#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
看板伺服器碰 agent 專用 Chrome 的那幾條路(👀、連接鈕、頁面不見了的判斷)。
agent 的 Chrome 資料夾整台電腦共用一個:副本看板不准碰它;代投、查應徵進度正在用它時,不准把它關掉、也不准再叫一個 agent 進去。
全部 mock 掉 agent_chrome,不開、不關、不連任何 Chrome。
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import test_board as tb  # noqa: E402
import board_server as bs  # noqa: E402

U = tb.JOBS[0]['id']


class SandboxNeverTouchesTheAgentChrome(tb.HttpBase):

    def test_eye_on_a_sandbox_never_marks_pages_gone_from_the_real_chrome(self):
        # 副本看板的 page_gone 以前會拿真機 agent Chrome 的程序判斷,把副本的卡標成「頁面不見了」
        with mock.patch('agent_chrome.gone_pages', return_value=[U]) as gone:
            self.assertFalse(bs.page_gone(U))
        gone.assert_not_called()
        self.assertNotIn('apply', tb.read_fb(self.path).get(U, {}))

    def test_sandbox_buttons_never_open_or_close_the_real_agent_chrome(self):
        # agent 的 Chrome 資料夾整台電腦只有一個:副本上按「🔌 連接」會把真的那一個關掉重開,連等他的頁一起
        with mock.patch('agent_chrome.setup') as setup, mock.patch('agent_chrome.show') as show, \
             mock.patch('agent_chrome.claude_setup') as claude:
            for act in ('setup', 'claude', 'show'):
                code, raw, _ = self.req('/api/settings/browser', {'act': act})
                self.assertEqual(code, 400, act)
                self.assertIn('副本', raw.decode('utf-8'))
        for m in (setup, show, claude):
            m.assert_not_called()


class ConnectWhileChromeIsInUse(tb.HttpBase):
    def setUp(self):
        super().setUp()
        real = bs.is_real
        bs.is_real = lambda: True
        self.addCleanup(setattr, bs, 'is_real', real)

    def test_connect_waits_while_apply_or_replies_is_using_the_chrome(self):
        # 代投填到一半按連接:正在填的那張還沒記上看板,會跟著 Chrome 一起被關掉
        for kind in ('apply', 'replies'):
            with mock.patch.object(bs, 'run_status', side_effect=lambda k, kind=kind: {'running': k == kind}), \
                 mock.patch('agent_chrome.setup') as setup, mock.patch('agent_chrome.claude_setup') as claude:
                for act in ('setup', 'claude'):
                    code, raw, _ = self.req('/api/settings/browser', {'act': act})
                    self.assertEqual(code, 409, (kind, act))
                    self.assertIn('跑完', raw.decode('utf-8'))
            setup.assert_not_called()
            claude.assert_not_called()

    def test_closing_pages_waiting_for_him_needs_his_confirmation(self):
        import json
        with mock.patch.object(bs, 'run_status', return_value={}), \
             mock.patch('agent_chrome.setup', return_value=(None, '還有 2 頁填好等你核對,這幾頁會不見。確定要連接嗎?')) as setup:
            code, raw, _ = self.req('/api/settings/browser', {'act': 'setup'})
            d = json.loads(raw)
            self.assertEqual((code, d['ok'], d['confirm']), (409, False, True))
            self.assertEqual(setup.call_args.kwargs.get('force'), False)
            self.req('/api/settings/browser', {'act': 'setup', 'force': True})
            self.assertEqual(setup.call_args.kwargs.get('force'), True)
            self.assertEqual(setup.call_args.args[0], self.path)       # 算等他的頁用這一份看板



class OneClaudeAtATimeInTheAgentChrome(tb.HttpBase):
    """同一時間只准一個 agent 用 agent 的 Chrome:Claude 的 👀 要再叫一個 Claude 進去截圖,
    「連接 Claude」的背景輪詢也是。代投、查應徵進度正在跑時都不准。"""

    def setUp(self):
        super().setUp()
        import board_doc as bd
        def mut(fb):
            fb[U] = {'app': 'ship', 'apply': {'stage': 'fill', 'ok': True, 'session': 's', 'tab_id': '5', 'runtime': 'claude-code'}}
        bd.set_fb(mut, live=self.path, by='test')

    def test_claude_eye_waits_while_another_job_uses_the_chrome(self):
        for kind in ('apply', 'replies'):
            st = lambda k, kind=kind: {'running': k == kind, 'url': 'https://other.example/job'}
            with mock.patch.object(bs, 'run_status', side_effect=st), \
                 mock.patch.object(bs, '_run_then_stop', side_effect=AssertionError('不該再叫一個 Claude')):
                code, raw, _ = self.req('/api/live?u=' + U)
            self.assertEqual(code, 409, kind)
            self.assertIn('跑完', raw.decode('utf-8'))

    def test_claude_eye_shows_the_page_from_when_it_was_filled_while_the_chrome_is_busy(self):
        # Codex 的 👀 跟代投同時截得到;Claude 不行(再叫一個 Claude 進去會把正在跑的那個的瀏覽器選走)。
        # 以前只回 409 叫他晚點再按:代投一批跑很久,這段時間他什麼都看不到。
        # 改成先給那一頁填好時截的圖(填完、交接之後 agent 不再動它),標明是幾點的
        import apply_run
        shot = os.path.join(apply_run.out_dir(U, self.path, bs.run_sp('apply')), 'fill.png')
        os.makedirs(os.path.dirname(shot), exist_ok=True)
        with open(shot, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\nFILLED')
        st = lambda k: {'running': k == 'apply', 'url': 'https://other.example/job'}
        with mock.patch.object(bs, 'run_status', side_effect=st), \
             mock.patch.object(bs, '_run_then_stop', side_effect=AssertionError('不該再叫一個 Claude')):
            code, raw, headers = self.req('/api/live?u=' + U)
        self.assertEqual(code, 200)
        self.assertEqual(raw, b'\x89PNG\r\n\x1a\nFILLED')
        self.assertEqual(headers.get('X-Refresh'), '0')
        self.assertTrue(headers.get('X-Shot-At'))

    def test_connect_claude_polling_knows_when_the_chrome_is_busy(self):
        real = bs.is_real
        bs.is_real = lambda: True
        self.addCleanup(setattr, bs, 'is_real', real)
        with mock.patch.object(bs, 'run_status', return_value={}), \
             mock.patch('agent_chrome.claude_setup', return_value=(False, '登入')) as claude:
            self.req('/api/settings/browser', {'act': 'claude'})
        busy = claude.call_args.kwargs['busy']
        with mock.patch.object(bs, 'run_status', side_effect=lambda k: {'running': k == 'apply'}):
            self.assertTrue(busy())
        with mock.patch.object(bs, 'run_status', return_value={}):
            self.assertFalse(busy())


class EyeGivesTheTabBackWhenItTimesOut(tb.HttpBase):
    """👀 截圖逾時:以前直接強制結束 apply_tab(SIGKILL),它的 finally 來不及交接、結束這一輪,
    等他核對的那一頁接在死掉的程序上,外掛跟 agent 的 Chrome 斷線。"""

    def test_time_limit_asks_apply_tab_to_stop_before_killing_it(self):
        import tempfile, textwrap, time
        d = tempfile.mkdtemp(prefix='fake-tools-')
        mark = os.path.join(d, 'handed-off')
        with open(os.path.join(d, 'apply_tab.py'), 'w', encoding='utf-8') as f:
            f.write(textwrap.dedent(f"""
                import signal, sys, time
                signal.signal(signal.SIGTERM, lambda *_: sys.exit(3))
                try:
                    time.sleep(90)
                finally:
                    open({mark!r}, 'w').write('ok')      # 真的 apply_tab 在這裡交接那一頁、結束這一輪
            """))
        old = bs.HERE
        bs.HERE = d
        try:
            with mock.patch.object(bs, 'LIVE_TIMEOUT', {'codex': 1, 'claude-code': 1}, create=True):
                t0 = time.time()
                code, _, _ = self.req('/api/live?u=' + U)
        finally:
            bs.HERE = old
        self.assertEqual(code, 503)
        self.assertTrue(os.path.exists(mark), '時間到直接強制結束,apply_tab 的 finally 沒跑到')
        self.assertLess(time.time() - t0, 30)

    def test_apply_tab_turns_a_stop_request_into_a_normal_exit(self):
        import subprocess, tempfile
        d = tempfile.mkdtemp(prefix='apply-tab-term-')
        mark = os.path.join(d, 'finally-ran')
        tools = os.path.abspath(os.path.join(HERE, '..', 'tools'))
        code = (f'import sys, time; sys.path.insert(0, {tools!r}); import apply_tab; apply_tab._stop_on_term()\n'
                f'try:\n    print("ready", flush=True); time.sleep(30)\nfinally:\n    open({mark!r}, "w").write("ok")\n')
        p = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        p.stdout.readline()                      # 等它裝好再送
        p.terminate()
        p.wait(timeout=20)
        self.assertTrue(os.path.exists(mark), p.stderr.read()[-300:])


if __name__ == '__main__':
    unittest.main()
