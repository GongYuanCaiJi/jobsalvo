import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import agent_chrome  # noqa: E402


class NoVisibilityOption(unittest.TestCase):
    """Codex 外掛接的 Chrome 沒有 visibility 這個能力:createBrowserTab 帶 {visible: ...} 會直接丟
    「Capability is not available: visibility」,代投一開頭就當掉(2026-09-27 #105 犯過)。"""

    def test_program_never_passes_visible_when_opening_tabs(self):
        import agent_run
        self.assertNotIn('visible:', agent_run.apply_rule())


class ReadPages(unittest.TestCase):
    def _read(self, snapshots, ready, settle):
        class Session:
            def __init__(self):
                self.index = 0

            def js(self, code, timeout_ms=60000):
                if code.startswith('globalThis.__g =') or '__g.close()' in code:
                    return 'ok'
                if code == agent_chrome.PAGE_JS:
                    snapshot = snapshots[min(self.index, len(snapshots) - 1)]
                    self.index += 1
                    return '@@' + json.dumps(snapshot)
                return 'ok'

            def end_turn(self, keep=()):
                pass

            def close(self):
                pass

        session = Session()
        with (
            mock.patch.object(agent_chrome, 'ensure', return_value=(True, '')),
            mock.patch.object(agent_chrome, 'browser_id', return_value='browser'),
            mock.patch('apply_tab.Session', return_value=session),
            mock.patch('time.sleep'),
        ):
            result = agent_chrome.read_pages(
                ['https://mail.google.com/mail/u/0/'],
                ready=ready, wait=2, settle=settle,
            )
        return result['https://mail.google.com/mail/u/0/'], session

    def test_specific_readiness_waits_for_stable_content(self):
        partial = {'url': 'https://mail.google.com/mail/u/0/', 'title': 'Gmail',
                   'text': 'search shell'}
        complete = {'url': 'https://mail.google.com/mail/u/0/', 'title': 'Gmail',
                    'text': 'full message body'}

        page, session = self._read([partial, complete, complete, complete],
                                   lambda item: item['text'] == 'full message body', 2)

        self.assertTrue(page['_ready'])
        self.assertEqual(session.index, 4)

    def test_specific_readiness_timeout_is_reported_as_incomplete(self):
        partial = {'url': 'https://mail.google.com/mail/u/0/', 'title': 'Gmail',
                   'text': 'Gmail shell ' * 30}

        page, session = self._read([partial], lambda _item: False, 0)

        self.assertFalse(page['_ready'])
        self.assertEqual(session.index, 4)


class OwnChrome(unittest.TestCase):
    """agent 的 Chrome 是自己一個資料夾、自己一個程序(docs/adr/0003):
    只認用 agent 資料夾開的那一個,在背景開、不上螢幕,要看才叫出來,收掉要確認程序真的不在了。"""

    def setUp(self):
        import tempfile, shutil
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.dir = os.path.join(self.tmp, 'agent-chrome')
        p = mock.patch('agent_chrome.data_dir', return_value=self.dir)
        p.start(); self.addCleanup(p.stop)

    def _ps(self, *rows):
        return mock.Mock(stdout='\n'.join(f'{pid} {cmd}' for pid, cmd in rows))

    def test_pid_is_only_the_agent_main_process(self):
        exe = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        helper = '/Applications/Google Chrome.app/Contents/Frameworks/x/Google Chrome Helper'
        rows = [(11, exe),                                                     # 使用者本人的 Chrome
                (12, f'{helper} --type=renderer --user-data-dir={self.dir}'),  # agent 的子程序
                (13, f'{exe} --user-data-dir={self.dir} --no-startup-window')]  # agent 的主程序
        with mock.patch('subprocess.run', return_value=self._ps(*rows)):
            self.assertEqual(agent_chrome.pid(), 13)
        with mock.patch('subprocess.run', return_value=self._ps(*rows[:2])):
            self.assertIsNone(agent_chrome.pid())

    def test_prepare_copies_logins_but_not_the_extension_identities(self):
        src_root = os.path.join(self.tmp, 'Chrome')
        prof = os.path.join(src_root, 'Profile 7')
        for sub in ('Cookies-dir', 'Cache', 'Sync Data', agent_chrome.CODEX_STORE, agent_chrome.CLAUDE_EXT_STORE,
                    'Extensions/hehggadaopoacecdllhhajmbjkdcmajg'):
            os.makedirs(os.path.join(prof, sub))
        os.makedirs(os.path.join(src_root, 'NativeMessagingHosts'))
        for host in ('com.openai.codexextension.json', 'com.anthropic.claude_browser_extension.json'):
            with open(os.path.join(src_root, 'NativeMessagingHosts', host), 'w') as f:
                f.write('{}')
        os.makedirs(os.path.join(self.dir, 'NativeMessagingHosts'))      # 舊版接過 Claude Desktop 的那份
        os.symlink(os.path.join(src_root, 'NativeMessagingHosts', 'com.anthropic.claude_browser_extension.json'),
                   os.path.join(self.dir, 'NativeMessagingHosts', 'com.anthropic.claude_browser_extension.json'))
        with mock.patch('chrome_bin.USER_DATA', src_root), mock.patch('agent_chrome._profile', return_value='Profile 7'), \
             mock.patch('agent_chrome.pid', return_value=None):
            note = agent_chrome.prepare()
            again = agent_chrome.prepare()
        d = os.path.join(self.dir, 'Default')
        self.assertIn('Profile 7', note)
        self.assertEqual(again, '')                                   # 第二次不重複複製
        self.assertTrue(os.path.isdir(os.path.join(d, 'Cookies-dir')))
        self.assertTrue(os.path.isdir(os.path.join(d, 'Extensions/hehggadaopoacecdllhhajmbjkdcmajg')))
        self.assertFalse(os.path.exists(os.path.join(d, 'Cache')))
        self.assertFalse(os.path.exists(os.path.join(d, 'Sync Data')))   # 舊的分頁群組不帶過來
        self.assertFalse(os.path.exists(os.path.join(d, agent_chrome.CODEX_STORE)))   # 外掛會自己產生新的身分
        self.assertFalse(os.path.exists(os.path.join(d, agent_chrome.CLAUDE_EXT_STORE)))   # Claude 認的瀏覽器編號不能跟他的 Chrome 一樣
        self.assertTrue(os.path.islink(os.path.join(self.dir, 'NativeMessagingHosts', 'com.openai.codexextension.json')))
        # Claude Desktop 那份會搶走 Claude 擴充功能,Claude Code 就連不上(#88395):不接,舊的也拿掉
        self.assertFalse(os.path.lexists(os.path.join(self.dir, 'NativeMessagingHosts', 'com.anthropic.claude_browser_extension.json')))

    def test_launch_is_a_separate_background_instance_with_windows(self):
        os.makedirs(os.path.join(self.dir, 'Default', 'Sync Data', 'LevelDB'))
        pids = iter([None, None, 42])
        with mock.patch('agent_chrome.pid', side_effect=lambda: next(pids)), \
             mock.patch('agent_chrome.prepare', return_value=''), \
             mock.patch('agent_chrome._app', return_value='/Applications/Google Chrome.app'), \
             mock.patch('subprocess.run') as run, mock.patch('time.sleep'):
            self.assertEqual(agent_chrome.launch(), 42)
        self.assertEqual(agent_chrome.conf().get('shown'), False)
        argv = run.call_args[0][0]
        self.assertEqual(argv[:4], ['open', '-n', '-g', '-a'])             # 另開程序、不搶前景
        self.assertNotIn('-j', argv)                                       # 不藏起來:藏起來的 Chrome 不畫畫面、截不到圖
        self.assertNotIn('--headless=new', argv)                           # 有真的視窗,使用者要看叫得出來
        self.assertIn(f'--user-data-dir={self.dir}', argv)
        self.assertIn('--no-startup-window', argv)
        # 不開除錯埠:開了 Chrome 會對網頁標記「正被自動化控制」,104、claude.ai 的 Cloudflare 就擋
        self.assertFalse(any(a.startswith('--remote-debugging') for a in argv))
        for flag in agent_chrome.BACKGROUND_OK:                            # 不在螢幕上也照常畫
            self.assertIn(flag, argv)
        self.assertFalse(any(a.startswith('--profile-directory') for a in argv))
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'Default', 'Sync Data')))   # 上一輪的分頁群組清掉

    def test_launch_does_nothing_when_already_running(self):
        with mock.patch('agent_chrome.pid', return_value=7), mock.patch('agent_chrome._not_background', return_value=False), \
             mock.patch('subprocess.run') as run:
            self.assertEqual(agent_chrome.launch(), 7)
        run.assert_not_called()

    def test_translate_is_turned_off_before_launch(self):
        # 翻譯提示會把視窗帶上螢幕;啟動參數擋不住,要關設定裡的「使用 Google 翻譯」
        os.makedirs(os.path.join(self.dir, 'Default'))
        with open(os.path.join(self.dir, 'Default', 'Preferences'), 'w') as f:
            json.dump({'translate': {'enabled': True}, 'other': 1}, f)
        agent_chrome._quiet_prefs()
        with open(os.path.join(self.dir, 'Default', 'Preferences')) as f:
            prefs = json.load(f)
        self.assertEqual(prefs, {'translate': {'enabled': False}, 'other': 1})

    def test_old_launch_styles_are_replaced(self):
        for cmd, old in (('/c --headless=new', True), ('/c --remote-debugging-port=0', True), ('/c --no-startup-window', False)):
            with mock.patch('subprocess.run', return_value=mock.Mock(stdout=cmd)):
                self.assertEqual(agent_chrome._not_background(1), old, cmd)

    def test_after_he_opened_it_the_next_run_restarts_in_background(self):
        # 他叫出來看過的 Chrome,之後開的視窗會上螢幕:沒有頁面在等他就關掉重開成背景的
        agent_chrome.save({'shown': True})
        pids = iter([7, None, 8])
        with mock.patch('agent_chrome.pid', side_effect=lambda: next(pids)), \
             mock.patch('agent_chrome._not_background', return_value=False), \
             mock.patch('agent_chrome.waiting_pages', return_value=[]), \
             mock.patch('agent_chrome.quit_chrome', return_value=True) as q, \
             mock.patch('agent_chrome.prepare', return_value=''), \
             mock.patch('agent_chrome._app', return_value='/Applications/Google Chrome.app'), \
             mock.patch('subprocess.run'), mock.patch('time.sleep'):
            self.assertEqual(agent_chrome.launch(), 8)
        q.assert_called_once()
        with mock.patch('agent_chrome.pid', return_value=7), mock.patch('agent_chrome._not_background', return_value=True), \
             mock.patch('agent_chrome.waiting_pages', return_value=['655']), mock.patch('agent_chrome.quit_chrome') as q2:
            self.assertEqual(agent_chrome.launch(), 7)     # 有頁面在等他:不重開(那幾頁會不見)
        q2.assert_not_called()

    def test_launch_replaces_an_old_headless_instance(self):
        pids = iter([7, None, 8])
        with mock.patch('agent_chrome.pid', side_effect=lambda: next(pids)), \
             mock.patch('agent_chrome._not_background', return_value=True), \
             mock.patch('agent_chrome.waiting_pages', return_value=[]), \
             mock.patch('agent_chrome.quit_chrome', return_value=True) as q, \
             mock.patch('agent_chrome.prepare', return_value=''), \
             mock.patch('agent_chrome._app', return_value='/Applications/Google Chrome.app'), \
             mock.patch('subprocess.run') as run, mock.patch('time.sleep'):
            self.assertEqual(agent_chrome.launch(), 8)
        q.assert_called_once()
        self.assertNotIn('--headless=new', run.call_args[0][0])

    def test_old_profile_style_state_needs_reconnect(self):
        with mock.patch('agent_chrome.conf', return_value={'instance': 'x', 'keeper': 'k'}):
            self.assertFalse(agent_chrome._mine())
            ok, msg = agent_chrome.ensure()
        self.assertFalse(ok)
        self.assertIn('連接', msg)
        with mock.patch('agent_chrome.conf', return_value={'instance': 'x', 'dir': self.dir}):
            self.assertTrue(agent_chrome._mine())

    def _close(self, keep, tabs, quit_ok=True, claude=(), conf=None):
        class Session:
            def __init__(self, _):
                pass

            def close(self):
                pass
        with mock.patch('agent_chrome.conf', return_value=conf or {'instance': 'x', 'dir': self.dir}), \
             mock.patch('agent_chrome.pid', return_value=9), \
             mock.patch('agent_chrome.protected_tabs', side_effect=lambda b, runtime=None: set(claude) if runtime else keep), \
             mock.patch('apply_tab.Session', Session), \
             mock.patch('agent_chrome.browser_id', return_value='b1'), \
             mock.patch('agent_chrome.tabs', return_value=tabs), \
             mock.patch('agent_chrome.quit_chrome', return_value=quit_ok) as q:
            return agent_chrome.close_if_idle('/tmp/board.html'), q

    def test_close_keeps_pages_waiting_for_him(self):
        msg, q = self._close({'t1'}, ['t1', 't2'])
        self.assertIn('還有 1 頁在等他', msg)
        q.assert_not_called()

    def test_close_keeps_claude_pages_even_without_codex(self):
        # 只裝 Claude:沒有 Codex 外掛可以列分頁,看板上記著 Claude 填好、還沒送出的那一頁,就當它還在等他
        msg, q = self._close(set(), [], claude={'655'}, conf={'dir': self.dir})
        self.assertIn('還有 1 頁在等他', msg)
        q.assert_not_called()

    def test_close_quits_the_whole_agent_chrome_when_nothing_waits(self):
        msg, q = self._close({'t9'}, ['t1', 't2'])     # 等他的那一頁已經不在了
        self.assertIn('關掉了', msg)
        q.assert_called_once()

    def test_close_never_claims_success_when_the_process_is_still_there(self):
        msg, _ = self._close(set(), [], quit_ok=False)
        self.assertNotIn('關掉了', msg)
        self.assertIn('關不掉', msg)

    def test_quit_checks_the_process_is_really_gone(self):
        with mock.patch('agent_chrome.pid', side_effect=[5] + [5] * 60 + [5, 5]), \
             mock.patch('os.kill') as kill, mock.patch('time.sleep'):
            self.assertFalse(agent_chrome.quit_chrome(wait=1))
        self.assertEqual([c[0][1] for c in kill.call_args_list][-1], 9)     # SIGTERM 關不掉才強制

    def test_show_asks_that_chrome_for_a_new_window_without_restarting_it(self):
        # 只有他按了才出現:請同一個資料夾的 Chrome 開新視窗(交給已經在跑的那一個,填好的頁都還在),不關、不重開
        with mock.patch('chrome_bin.find', return_value='/chrome'), mock.patch('subprocess.Popen') as popen, \
             mock.patch('agent_chrome.quit_chrome') as q:
            ok, _ = agent_chrome.show('https://www.104.com.tw/')
        argv = popen.call_args[0][0]
        self.assertTrue(ok)
        self.assertEqual(argv[:2], ['/chrome', f'--user-data-dir={self.dir}'])
        self.assertIn('--new-window', argv)
        self.assertIn('https://www.104.com.tw/', argv)
        self.assertFalse(any(a.startswith('--remote-debugging') for a in argv))
        q.assert_not_called()
        self.assertTrue(agent_chrome.conf().get('shown'))    # 下次 agent 用之前要換回背景


class SetupSaysWhy(unittest.TestCase):
    """連接失敗要直接講是哪個原因,不是等 30 秒後列三件事叫使用者自己猜(#159)。"""

    def _setup(self, has_ext=True, new=(('b2', 'new-id'),), before=()):
        import tempfile
        d = tempfile.mkdtemp()
        if has_ext:
            os.makedirs(os.path.join(d, 'Default', 'Extensions', 'hehggadaopoacecdllhhajmbjkdcmajg'))

        class Session:
            def __init__(self, _):
                pass

            def close(self):
                pass
        seen = iter([list(before)] + [list(before) + list(new)] * 5)
        with mock.patch('agent_chrome._codex_ready', return_value=True), \
             mock.patch('agent_chrome.data_dir', return_value=d), \
             mock.patch('agent_chrome.prepare', return_value=''), \
             mock.patch('agent_chrome.conf', return_value={}), \
             mock.patch('agent_chrome.save') as save, \
             mock.patch('agent_chrome.quit_chrome', return_value=True), \
             mock.patch('agent_chrome.launch', return_value=5), \
             mock.patch('apply_tab.Session', Session), \
             mock.patch('agent_chrome.list_browsers', side_effect=lambda _t: next(seen)), \
             mock.patch('time.sleep'):
            ok, msg = agent_chrome.setup(wait=2)
        return ok, msg, save, d

    def test_extension_missing(self):
        ok, msg, _, _ = self._setup(has_ext=False)
        self.assertFalse(ok)
        self.assertIn('還沒裝 Codex', msg)

    def test_connects_and_remembers_the_folder(self):
        ok, msg, save, d = self._setup()
        self.assertTrue(ok)
        self.assertIn('不會出現在你的畫面上', msg)
        save.assert_called_once_with({'instance': 'new-id', 'dir': d})

    def test_refuses_to_guess_when_several_chromes_appear(self):
        ok, msg, save, _ = self._setup(new=(('b2', 'a'), ('b3', 'b')))
        self.assertFalse(ok)
        self.assertIn('認不出哪個是 agent 的', msg)
        save.assert_not_called()


class ClaudeConnect(unittest.TestCase):
    """「🔌 連接 Claude」:agent 的 Chrome 裡 Claude 擴充功能的編號,Claude Code 看得到就記下;沒登入就把它開在他面前。"""

    def _run(self, dev='dev-agent', seen=True, onboarded=True, claude='/bin/claude'):
        with mock.patch('agent_run.claude_bin', return_value=claude), \
             mock.patch('agent_chrome.claude_state', return_value=(onboarded, None)), \
             mock.patch('agent_chrome.launch', return_value=7), \
             mock.patch('agent_chrome.claude_device', return_value=dev), \
             mock.patch('agent_chrome.claude_connected', return_value=seen), \
             mock.patch('agent_chrome.conf', return_value={'instance': 'i1'}), \
             mock.patch('agent_chrome.save') as save, mock.patch('agent_chrome.show') as show, \
             mock.patch('agent_chrome._wait_for_claude') as wait:
            ok, msg = agent_chrome.claude_setup()
        self.wait = wait
        return ok, msg, save, show

    def test_no_claude(self):
        ok, msg, save, show = self._run(claude=None)
        self.assertFalse(ok)
        self.assertIn('還沒裝 Claude Code', msg)

    def test_first_time_needs_terminal_once(self):
        ok, msg, _, show = self._run(onboarded=False)
        self.assertFalse(ok)
        self.assertIn('claude --chrome', msg)
        show.assert_not_called()

    def test_connected_records_the_agent_chromes_own_device(self):
        ok, msg, save, show = self._run()
        self.assertTrue(ok)
        saved = save.call_args[0][0]
        self.assertEqual((saved['instance'], saved['claude_device']), ('i1', 'dev-agent'))   # 其他狀態留著
        self.assertTrue(saved['claude_checked'])                    # 設定頁只講「什麼時候確認連得上」
        show.assert_not_called()                                     # 已經連上就不打擾他

    def test_not_signed_in_opens_login_in_front_of_him(self):
        ok, msg, save, show = self._run(seen=False)
        self.assertFalse(ok)
        self.assertIn('登入', msg)
        self.assertIn('不用再按', msg)
        self.wait.assert_called_once()                               # 登入好它自己偵測到,不用他按第二次
        show.assert_called_once_with(agent_chrome.CLAUDE_LOGIN)
        save.assert_not_called()

    def test_not_installed_opens_store_and_login(self):
        ok, msg, save, show = self._run(dev=None)
        self.assertFalse(ok)
        self.assertIn('加到 Chrome', msg)
        show.assert_called_once_with(agent_chrome.CLAUDE_STORE, agent_chrome.CLAUDE_LOGIN)

    def test_claude_runs_wait_until_claude_can_see_the_agent_chrome(self):
        # Chrome 剛開起來,Claude 擴充功能要一陣子才連得上:等到看得到才派工,等不到就照實講
        seen = iter([False, False, True])
        with mock.patch('agent_chrome.launch', return_value=7), mock.patch('agent_chrome.conf', return_value={'claude_device': 'd'}), \
             mock.patch('agent_chrome.claude_connected', side_effect=lambda d: next(seen)), mock.patch('time.sleep'):
            self.assertTrue(agent_chrome.wait_claude(wait=60)[0])
        with mock.patch('agent_chrome.launch', return_value=7), mock.patch('agent_chrome.conf', return_value={'claude_device': 'd'}), \
             mock.patch('agent_chrome.claude_connected', return_value=False), mock.patch('time.sleep'), \
             mock.patch('time.time', side_effect=[0, 0, 100]):
            ok, msg = agent_chrome.wait_claude(wait=60)
        self.assertFalse(ok)
        self.assertIn('連接 Claude', msg)

    def test_device_is_read_from_the_extensions_own_folder(self):
        import tempfile, shutil
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        store = os.path.join(d, 'Default', agent_chrome.CLAUDE_EXT_STORE)
        os.makedirs(store)
        with open(os.path.join(store, '000003.log'), 'wb') as f:
            f.write(b'xx\x00bridgeDeviceId&"a9aa7143-c000-4f82-919c-796e60366866"\xd6more')
        with mock.patch('agent_chrome.data_dir', return_value=d):
            self.assertEqual(agent_chrome.claude_device(), 'a9aa7143-c000-4f82-919c-796e60366866')
        with mock.patch('agent_chrome.data_dir', return_value=os.path.join(d, 'none')):
            self.assertIsNone(agent_chrome.claude_device())


if __name__ == '__main__':
    unittest.main()
