import datetime
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


class ReadPagesKeepsTheExtensionConnected(unittest.TestCase):
    def test_one_tab_for_all_urls_never_closed_and_handed_off(self):
        # 程式自己關分頁(close,或這一輪結束時不交接),Codex 外掛會跟 agent 的 Chrome 斷線、不會自己連回來(2026-09-29 實測)
        calls, kept = [], []

        class Session:
            def js(self, code, timeout_ms=60000):
                calls.append(code)
                if code == agent_chrome.PAGE_JS:
                    return '@@' + json.dumps({'url': 'u', 'title': 't', 'text': 'x' * 300})
                return 'ok'

            def end_turn(self, keep=()):
                kept.append(list(keep))

            def close(self):
                pass

        with mock.patch.object(agent_chrome, 'ensure', return_value=(True, '')), \
             mock.patch.object(agent_chrome, 'browser_id', return_value='browser'), \
             mock.patch('apply_tab.Session', return_value=Session()), mock.patch('time.sleep'):
            out = agent_chrome.read_pages(['https://a.example/', 'https://b.example/'])
        self.assertEqual(set(out), {'https://a.example/', 'https://b.example/'})
        self.assertEqual(sum('createBrowserTab' in c for c in calls), 1)          # 只開一個分頁
        self.assertTrue(any('__g.goto("https://b.example/")' in c for c in calls))  # 第二個網址在同一頁換過去
        self.assertFalse(any('.close()' in c for c in calls))                     # 不關分頁
        self.assertTrue(any('about:blank' in c for c in calls))                   # 讀完換成空白頁
        self.assertEqual(kept, [['__g']])                                         # 交接留著,不讓這一輪結束時被收掉


class ReleaseHandsTheTabBack(unittest.TestCase):
    def test_tab_is_handed_back_even_when_blanking_it_fails(self):
        # 送出後收分頁:換空白頁那一下出錯(確認頁還在轉、逾時),以前就不交接、也不結束這一輪,
        # 外掛跟 agent 的 Chrome 斷線,同一批的下一張就連不上
        import apply_tab
        ended = []

        class FakeTab:
            def __init__(self, _session, _tab):
                pass

            def js(self, code, timeout_ms=60000):
                raise RuntimeError('timed out')

            def end_turn(self, keep=()):
                ended.append(list(keep))

            def close(self):
                pass
        with mock.patch.object(apply_tab, 'Tab', FakeTab):
            with self.assertRaises(RuntimeError):
                apply_tab.release('s', '5')
        self.assertEqual(ended, [['__t']])

    def test_claude_is_told_plainly_that_it_should_close_the_sent_page(self):
        # 以前收分頁那次的開場白也寫「只讀,不改頁面」,接著又要它關分頁:Claude 可能因此拒絕,那一頁就一直留著
        import apply_tab
        seen = []
        with mock.patch('agent_run.claude_bin', return_value='/bin/claude'), \
             mock.patch('agent_chrome.conf', return_value={'claude_device': 'dev'}), \
             mock.patch('apply_tab.subprocess.run', side_effect=lambda argv, **k: seen.append(k['input']) or mock.Mock(stdout='')):
            with self.assertRaises(LookupError):
                apply_tab.release('s', '5', runtime='claude-code')
            with self.assertRaises(LookupError):
                apply_tab.claude_shot('s', '5', '/tmp/x.png')
        close, shot = seen
        self.assertNotIn('只讀', close)
        self.assertIn('已經送出', close)
        self.assertIn('tabs_close_mcp', close)
        self.assertIn('只讀', shot)                                   # 截圖那次照舊說只讀


class ReadsNeverOutliveTheirOwnTimeLimit(unittest.TestCase):
    """外掛的 REPL:一次 js 超過它自己帶的 timeout_ms 就被重置,全域變數消失、沒交接的分頁接不回來
    (2026-09-29 下載附件實測,見 profile_sync 的 FETCH_FILE_RULE)。程式讀頁每次只給 8 秒,慢的頁(Gmail、104)一定會碰到:
    以前重置之後每次讀都是 ReferenceError,最後也交接不了,外掛跟 agent 的 Chrome 斷線。"""

    class Repl:
        """照上面那個已知行為模擬的外掛 REPL。"""

        def __init__(self, slow_reads):
            self.g, self.slow, self.reads, self.handed = set(), slow_reads, 0, []

        def js(self, code, timeout_ms=60000):
            import re
            if 'createBrowserTab' in code:
                self.g.add('__g' if '__g' in code else '__t')
                return 'ok'
            var = '__g' if '__g' in code else ('__t' if '__t' in code else None)
            if var and var not in self.g:
                raise RuntimeError(f'ReferenceError: {var} is not defined')
            if 'playwright.evaluate' in code:
                self.reads += 1
                tagged = "'@@'" in code
                if self.reads <= self.slow:             # 頁面還在載入,evaluate 要很久
                    m = re.search(r'setTimeout\(.*?,\s*(\d+)\)', code)
                    if 'Promise.race' in code and m and int(m.group(1)) < timeout_ms:
                        return ('@@' if tagged else '') + 'null'     # JS 自己先回來了
                    self.g.clear()                       # 超過自己的 timeout_ms:REPL 重置
                    raise RuntimeError('js timed out')
                page = {'url': 'https://a.example/', 'text': 'x' * 300, 'fields': [{'label': 'Name'}]}
                return ('@@' if tagged else '') + json.dumps(page)
            if 'markHandoff' in code:
                self.handed.append(var)
                return 'ok'
            if '.id)' in code:
                return '@@7'
            return 'ok'

        def end_turn(self, keep=()):
            for v in keep:
                try:
                    self.js(f'await {v}.markHandoff(); nodeRepl.write("ok")')
                except Exception:  # noqa: S110
                    pass

        def close(self):
            pass

    def test_program_reads_of_a_slow_page_come_back_before_the_repl_is_reset(self):
        repl = self.Repl(slow_reads=1)
        with mock.patch.object(agent_chrome, 'ensure', return_value=(True, '')), \
             mock.patch.object(agent_chrome, 'browser_id', return_value='b1'), \
             mock.patch('apply_tab.Session', return_value=repl), mock.patch('time.sleep'):
            page = agent_chrome.read_pages(['https://a.example/'], wait=2)['https://a.example/']
        self.assertTrue(page['_ready'])
        self.assertEqual(repl.handed, ['__g'])           # 讀完照樣交接,外掛不會斷線

    def test_prepared_page_is_still_handed_to_the_agent_after_a_slow_read(self):
        repl = self.Repl(slow_reads=1)
        with mock.patch.object(agent_chrome, 'conf', return_value={'instance': 'i1'}), \
             mock.patch.object(agent_chrome, 'browser_id', return_value='b1'), \
             mock.patch('apply_tab.Session', return_value=repl), mock.patch('time.sleep'):
            got = agent_chrome.open_for_agent('https://a.example/apply', wait=3)
        self.assertEqual(got['tab_id'], '7')
        self.assertTrue(got['page'].get('fields'))
        self.assertEqual(repl.handed, ['__t'])           # agent 照 prepared_step 去 getTab 接得回來


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
        p = mock.patch('config.TMP', os.path.join(self.tmp, 'tmp'))      # 下載資料夾建在這次的暫存裡
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
        self.assertEqual(prefs['translate'], {'enabled': False})
        self.assertEqual(prefs['other'], 1)                                # 其他設定不動

    def test_downloads_go_to_jobsalvos_own_folder(self):
        # Codex 的 downloadMedia 存進 Chrome 的下載資料夾:以前是他自己的「下載」,逾時沒搬走的檔就留在那裡
        os.makedirs(os.path.join(self.dir, 'Default'))
        with open(os.path.join(self.dir, 'Default', 'Preferences'), 'w') as f:
            json.dump({'translate': {'enabled': False}, 'download': {'directory_upgrade': True}}, f)   # 翻譯早就關了的也要補
        agent_chrome._quiet_prefs()
        with open(os.path.join(self.dir, 'Default', 'Preferences')) as f:
            prefs = json.load(f)
        want = os.path.join(self.tmp, 'tmp', 'agent-chrome-downloads')
        self.assertEqual(prefs['download'], {'directory_upgrade': True, 'default_directory': want, 'prompt_for_download': False})
        self.assertTrue(os.path.isdir(want))
        self.assertFalse(want.startswith(os.path.expanduser('~/Downloads')))

    def test_old_launch_styles_are_replaced(self):
        for cmd, old in (('/c --headless=new', True), ('/c --remote-debugging-port=0', True), ('/c --no-startup-window', False)):
            with mock.patch('subprocess.run', return_value=mock.Mock(stdout=cmd)):
                self.assertEqual(agent_chrome._not_background(1), old, cmd)

    def test_only_pages_still_waiting_for_him_are_protected(self):
        # 退回、移除、送出的卡留下的舊 tab_id 不算「在等他」,不然 agent 的 Chrome 永遠關不掉
        fb = {'a': {'app': 'ship', 'apply': {'stage': 'fill', 'ok': True, 'tab_id': 1}},
              'b': {'app': 'ship', 'apply': {'stage': 'fix', 'ok': False, 'tab_id': 2}},
              'c': {'app': 'prep', 'apply': {'stage': 'fill', 'tab_id': 3}},
              'd': {'app': 'ship', 'rm': 1, 'apply': {'stage': 'fill', 'tab_id': 4}},
              'e': {'app': 'ship', 'form': {'lock': 1}, 'apply': {'stage': 'fill', 'tab_id': 5}},
              'f': {'app': 'ship', 'apply': {'stage': 'fill', 'tab_id': 6, 'runtime': 'claude-code'}},
              # 已投出又退回可以投了:agent 在那頁按過送出,那頁是「已收到申請」,不是等他的
              'g': {'app': 'ship', 'apply': {'stage': 'fill', 'ok': True, 'tab_id': 7, 'sent': {'at': '2026-01-01'}}}}
        path = os.path.join(self.tmp, 'board.html')
        with open(path, 'w') as f:
            f.write('x')
        with mock.patch('board_doc.parse', return_value={'fb': json.dumps(fb)}):
            self.assertEqual(agent_chrome.protected_tabs(path), {'1', '2', '6'})
            self.assertEqual(agent_chrome.protected_tabs(path, 'claude-code'), {'6'})

    def test_window_he_opened_is_never_closed_under_him(self):
        # 他按「🔑 打開」叫出來、視窗還開著(正在登入):背景流程不准重開、收尾也不准關
        agent_chrome.save({'shown': True})
        with mock.patch('agent_chrome.pid', return_value=7), mock.patch('agent_chrome._not_background', return_value=False), \
             mock.patch('agent_chrome.waiting_pages', return_value=[]), \
             mock.patch('agent_chrome.user_has_it_open', return_value=True), \
             mock.patch('agent_chrome.quit_chrome') as q:
            self.assertEqual(agent_chrome.launch(), 7)
            self.assertIn('不關', agent_chrome.close_if_idle('/tmp/board.html'))
        q.assert_not_called()

    def test_only_windows_on_his_screen_count_as_him_using_it(self):
        # agent 開的分頁放在不上螢幕的視窗裡(ADR 0003)。以前連這些不在螢幕上的視窗也算「他開著」:
        # 他叫出來看過一次之後,Chrome 就再也不換回背景、也不收掉,之後 agent 開的視窗都會上他的螢幕
        agent_chrome.save({'shown': True})
        with mock.patch('agent_chrome.pid', return_value=7), \
             mock.patch('subprocess.run', return_value=mock.Mock(stdout='0')) as run:
            self.assertFalse(agent_chrome.user_has_it_open())
        script = run.call_args[0][0][-1]
        self.assertIn('CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly', script)   # 驗收工具(apply_accept)也是這樣數
        self.assertNotIn('CGWindowListCopyWindowInfo(0', script)

    def test_after_he_opened_it_the_next_run_restarts_in_background(self):
        # 他叫出來看過的 Chrome,之後開的視窗會上螢幕:沒有頁面在等他就關掉重開成背景的
        agent_chrome.save({'shown': True})
        pids = iter([7, 7, None, 8])          # launch 看一次、quit_if_safe 看一次、關掉後開新的
        with mock.patch('agent_chrome.pid', side_effect=lambda: next(pids)), \
             mock.patch('agent_chrome._not_background', return_value=False), \
             mock.patch('agent_chrome.waiting_pages', return_value=[]), \
             mock.patch('agent_chrome.user_has_it_open', return_value=False), \
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
        pids = iter([7, 7, None, 8])          # launch 看一次、quit_if_safe 看一次、關掉後開新的
        with mock.patch('agent_chrome.pid', side_effect=lambda: next(pids)), \
             mock.patch('agent_chrome._not_background', return_value=True), \
             mock.patch('agent_chrome.waiting_pages', return_value=[]), \
             mock.patch('agent_chrome.user_has_it_open', return_value=False), \
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

    def _close(self, keep, tabs, quit_ok=True, claude=(), conf=None, bid='b1'):
        class Session:
            def __init__(self, _):
                pass

            def close(self):
                pass
        with mock.patch('agent_chrome.conf', return_value=conf or {'instance': 'x', 'dir': self.dir}), \
             mock.patch('agent_chrome.pid', return_value=9), \
             mock.patch('agent_chrome.protected_tabs', side_effect=lambda b, runtime=None: set(claude) if runtime else keep), \
             mock.patch('apply_tab.Session', Session), \
             mock.patch('agent_chrome.browser_id', return_value=bid), \
             mock.patch('agent_chrome.tabs', return_value=tabs), \
             mock.patch('agent_chrome.quit_chrome', return_value=quit_ok) as q:
            return agent_chrome.close_if_idle('/tmp/board.html'), q

    def test_close_keeps_pages_waiting_for_him(self):
        msg, q = self._close({'t1'}, ['t1', 't2'])
        self.assertIn('還有 1 頁在等他', msg)
        q.assert_not_called()

    def test_gone_pages_only_when_chrome_certainly_lost_them(self):
        fb = {'filled': {'apply': {'stage': 'fill', 'ok': True, 'at': '2026-09-29T17:06:02', 'tab_id': '5'}},
              'running': {'apply': {'stage': 'fill', 'ok': True, 'at': '2026-09-29T17:06:02', 'tab_id': '6'}},
              'sent': {'apply': {'stage': 'fill', 'at': '2026-09-29T17:06:02', 'tab_id': '7'}, 'form': {'lock': 1}},
              'no_tab': {'apply': {'stage': 'fill', 'ok': False, 'at': '2026-09-29T17:06:02', 'tab_id': ''}}}
        at = datetime.datetime(2026, 9, 29, 17, 6, 2).timestamp()
        with mock.patch('agent_chrome.pid', return_value=None):          # Chrome 沒在跑
            self.assertEqual(agent_chrome.gone_pages(fb, 'running'), ['filled'])
        with mock.patch('agent_chrome.pid', return_value=9), mock.patch('agent_chrome.started_at', return_value=at + 3600):
            self.assertEqual(agent_chrome.gone_pages(fb), ['filled', 'running'])   # 填好之後才開的
        with mock.patch('agent_chrome.pid', return_value=9), mock.patch('agent_chrome.started_at', return_value=at - 60):
            self.assertEqual(agent_chrome.gone_pages(fb), [])                      # 填之前就開著:頁還在
        with mock.patch('agent_chrome.pid', return_value=9), mock.patch('agent_chrome.started_at', return_value=None):
            self.assertEqual(agent_chrome.gone_pages(fb), [])                      # 問不到:不動
        agent_chrome.mark_gone(fb, ['filled'])
        self.assertEqual((fb['filled']['apply']['ok'], fb['filled']['apply']['tab_id']), (False, ''))

    def test_close_keeps_chrome_when_the_extension_cannot_list_tabs(self):
        # 2026-09-29:外掛跟 agent 的 Chrome 斷線、問不到分頁,收尾當成「沒有頁在等」,把他還沒核對的那一頁連 Chrome 一起關了
        msg, q = self._close({'t1'}, [], bid=None)
        self.assertIn('在等他', msg)
        q.assert_not_called()

    def test_close_keeps_claude_pages_even_without_codex(self):
        # 只裝 Claude:沒有 Codex 外掛可以列分頁,看板上記著 Claude 填好、還沒送出的那一頁,就當它還在等他
        msg, q = self._close(set(), [], claude={'655'}, conf={'dir': self.dir})
        self.assertIn('還有 1 頁在等他', msg)
        q.assert_not_called()

    def test_close_keeps_chrome_when_the_board_cannot_be_read_even_without_codex(self):
        # 只用 Claude(沒有 Codex 外掛可以問)又讀不到看板:以前當成沒有頁在等,連 Chrome 一起關;說明寫的是讀不到當成有
        with mock.patch('agent_chrome.conf', return_value={'dir': self.dir}), \
             mock.patch('agent_chrome.pid', return_value=9), \
             mock.patch('agent_chrome.protected_tabs', return_value=None), \
             mock.patch('agent_chrome.quit_chrome', return_value=True) as q:
            msg = agent_chrome.close_if_idle('/tmp/board.html')
        self.assertIn('在等他', msg)
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
             mock.patch('agent_chrome.pid', return_value=7), mock.patch('agent_chrome.quit_chrome') as q:
            ok, _ = agent_chrome.show('https://www.104.com.tw/')
        argv = popen.call_args[0][0]
        self.assertTrue(ok)
        self.assertEqual(argv[:2], ['/chrome', f'--user-data-dir={self.dir}'])
        self.assertIn('--new-window', argv)
        self.assertIn('https://www.104.com.tw/', argv)
        self.assertFalse(any(a.startswith('--remote-debugging') for a in argv))
        q.assert_not_called()
        self.assertTrue(agent_chrome.conf().get('shown'))    # 下次 agent 用之前要換回背景

    def test_opening_it_first_still_copies_the_profile_he_chose(self):
        # 照教學先按 🔑(裝擴充功能)再按連接:以前 show() 直接開 Chrome,Chrome 自己建了空的 Default,
        # 之後連接時 prepare() 看到 Default 在就不複製,他選的設定檔(登入狀態、擴充功能)默默沒帶過去
        order = []
        with mock.patch('chrome_bin.find', return_value='/chrome'), \
             mock.patch('subprocess.Popen', side_effect=lambda *a, **k: order.append('open')), \
             mock.patch('agent_chrome.pid', return_value=None), \
             mock.patch('agent_chrome.prepare', side_effect=lambda: order.append('prepare') or '從 Chrome 的「工作用」複製了登入狀態和擴充功能'):
            ok, msg = agent_chrome.show()
        self.assertTrue(ok)
        self.assertEqual(order, ['prepare', 'open'])
        self.assertIn('工作用', msg)                          # 複製了什麼照實講
        order.clear()
        with mock.patch('chrome_bin.find', return_value='/chrome'), \
             mock.patch('subprocess.Popen', side_effect=lambda *a, **k: order.append('open')), \
             mock.patch('agent_chrome.pid', return_value=7), \
             mock.patch('agent_chrome.prepare', side_effect=lambda: order.append('prepare') or ''):
            agent_chrome.show()
        self.assertEqual(order, ['open'])                      # 已經在跑:不能換它的資料,只請它開新視窗


class WhichChromeOpenedThePage(unittest.TestCase):
    """填好的頁還在不在,要看它是不是現在這個 Chrome 程序開的(Chrome 關掉、重開過,頁一定不在)。"""

    def test_start_time_does_not_depend_on_the_locale(self):
        # 中文語系下 ps 的 lstart 印成「二  9月/29 22:40:07 2026」:以前照英文格式解析失敗,gone_pages 永遠回空
        def ps(argv, **_kw):
            if 'lstart=' in argv:
                return mock.Mock(stdout='二  9月/29 22:40:07 2026\n')
            if 'etime=' in argv:
                return mock.Mock(stdout={'9': ' 01-05:33:59\n', '10': '02:03:07\n', '11': '   03:07\n'}[argv[-1]])
            return mock.Mock(stdout='')
        with mock.patch('subprocess.run', side_effect=ps), mock.patch('time.time', return_value=1_000_000.0):
            self.assertEqual(agent_chrome.started_at(9), 1_000_000 - (86400 + 5 * 3600 + 33 * 60 + 59))
            self.assertEqual(agent_chrome.started_at(10), 1_000_000 - (2 * 3600 + 3 * 60 + 7))
            self.assertEqual(agent_chrome.started_at(11), 1_000_000 - 187)

    def test_page_from_an_earlier_chrome_is_gone_even_if_the_record_was_touched_later(self):
        # 改表失敗、送出前擋下的紀錄會把 at 換成現在、tab_id 照留:以前拿 at 跟 Chrome 什麼時候開的比,重開過也抓不到
        now = datetime.datetime.now().isoformat(timespec='seconds')
        fb = {'u': {'apply': {'stage': 'fix', 'ok': False, 'at': now, 'tab_id': '5',
                              'chrome': {'pid': 9, 'start': 1000.0}}}}
        with mock.patch('agent_chrome.pid', return_value=9), mock.patch('agent_chrome.started_at', return_value=5000.0):
            self.assertEqual(agent_chrome.gone_pages(fb), ['u'])       # 同一個程序編號、另一個時間開的:換過程序
        with mock.patch('agent_chrome.pid', return_value=11), mock.patch('agent_chrome.started_at', return_value=1000.0):
            self.assertEqual(agent_chrome.gone_pages(fb), ['u'])
        with mock.patch('agent_chrome.pid', return_value=9), mock.patch('agent_chrome.started_at', return_value=1001.0):
            self.assertEqual(agent_chrome.gone_pages(fb), [])          # 同一個程序(差一秒是 ps 只算到秒)
        with mock.patch('agent_chrome.pid', return_value=None):
            self.assertEqual(agent_chrome.gone_pages(fb), ['u'])       # Chrome 沒在跑

    def test_gone_page_also_drops_his_confirmation(self):
        # 按了確認送出、還沒送就發現頁面不見了:確認留著的話,自動流程看到有確認就整張跳過,永遠不會重填
        fb = {'u': {'approve': {'at': '2026-09-29T17:06:02'}, 'apply': {'stage': 'fill', 'ok': True, 'tab_id': '5'}}}
        agent_chrome.mark_gone(fb, ['u'])
        self.assertNotIn('approve', fb['u'])
        self.assertEqual((fb['u']['apply']['tab_id'], fb['u']['apply']['gone']), ('', True))

    def test_identity_of_the_running_chrome(self):
        with mock.patch('agent_chrome.pid', return_value=9), mock.patch('agent_chrome.started_at', return_value=1000.4):
            self.assertEqual(agent_chrome.chrome_id(), {'pid': 9, 'start': 1000.4})
        with mock.patch('agent_chrome.pid', return_value=None):
            self.assertEqual(agent_chrome.chrome_id(), {})

    def test_eye_never_shows_a_tab_number_from_an_earlier_chrome(self):
        # 分頁編號每個 Chrome 程序從頭數:Chrome 重開後舊編號可能剛好是別張卡的頁,👀 會截到別張
        import apply_tab
        fb = {'u': {'apply': {'stage': 'fill', 'session': 's', 'tab_id': '5', 'at': '2026-09-29T17:06:02',
                              'chrome': {'pid': 9, 'start': 1000.0}}}}
        with mock.patch('board_doc.parse', return_value={'fb': json.dumps(fb)}), \
             mock.patch('builtins.open', mock.mock_open(read_data='x')), \
             mock.patch('agent_chrome.pid', return_value=12), mock.patch('agent_chrome.started_at', return_value=9000.0):
            with self.assertRaises(LookupError):
                apply_tab._lookup('u', '/tmp/board.html')
        with mock.patch('board_doc.parse', return_value={'fb': json.dumps(fb)}), \
             mock.patch('builtins.open', mock.mock_open(read_data='x')), \
             mock.patch('agent_chrome.pid', return_value=9), mock.patch('agent_chrome.started_at', return_value=1000.0):
            self.assertEqual(apply_tab._lookup('u', '/tmp/board.html'), ('s', '5', 'codex'))


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
             mock.patch('agent_chrome.close_if_idle', return_value='') as close, \
             mock.patch('time.sleep'):
            ok, msg = agent_chrome.setup('/tmp/board.html', wait=2)
        self.close = close
        return ok, msg, save, d

    def test_extension_missing(self):
        ok, msg, _, _ = self._setup(has_ext=False)
        self.assertFalse(ok)
        self.assertIn('還沒裝 Codex', msg)

    def test_connects_and_remembers_the_folder(self):
        ok, msg, save, d = self._setup()
        self.assertTrue(ok)
        self.assertIn('不會出現在你的畫面上', msg)
        save.assert_called_once()
        saved = save.call_args[0][0]
        self.assertEqual((saved['instance'], saved['dir']), ('new-id', d))
        self.assertTrue(saved['codex_checked'])                    # 設定頁講「什麼時候確認連得上」
        self.close.assert_called_once_with('/tmp/board.html')    # 連上了就收掉,要用時再在背景開

    def test_reconnecting_never_silently_closes_pages_waiting_for_him(self):
        # 外掛斷線時他按「🔌 連接 Codex」(代投失敗的訊息就叫他這樣按):以前不看有沒有填好的頁在等他,整個 Chrome 直接關掉重開
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, 'Default', 'Extensions', 'hehggadaopoacecdllhhajmbjkdcmajg'))

        class Session:
            def __init__(self, _):
                pass

            def close(self):
                pass
        for keep in ({'655', '656'}, None):          # None:讀不到看板,當成有
            with mock.patch('agent_chrome._codex_ready', return_value=True), \
                 mock.patch('agent_chrome.data_dir', return_value=d), \
                 mock.patch('agent_chrome.prepare', return_value=''), \
                 mock.patch('agent_chrome.conf', return_value={'instance': 'old', 'dir': d}), \
                 mock.patch('agent_chrome.pid', return_value=7), \
                 mock.patch('agent_chrome.browser_id', return_value=None), \
                 mock.patch('agent_chrome.protected_tabs', return_value=keep), \
                 mock.patch('agent_chrome.quit_chrome', return_value=True) as q, \
                 mock.patch('agent_chrome.launch', return_value=7), \
                 mock.patch('apply_tab.Session', Session):
                ok, msg = agent_chrome.setup('/tmp/board.html', wait=1)
            self.assertIsNone(ok)                    # 要他確認,不是失敗
            self.assertIn('不見', msg)
            q.assert_not_called()
        seen = iter([[], [('b2', 'new-id')]])
        with mock.patch('agent_chrome._codex_ready', return_value=True), \
             mock.patch('agent_chrome.data_dir', return_value=d), \
             mock.patch('agent_chrome.prepare', return_value=''), \
             mock.patch('agent_chrome.conf', return_value={'instance': 'old', 'dir': d}), \
             mock.patch('agent_chrome.save'), \
             mock.patch('agent_chrome.pid', return_value=7), \
             mock.patch('agent_chrome.browser_id', return_value=None), \
             mock.patch('agent_chrome.protected_tabs', return_value={'655'}), \
             mock.patch('agent_chrome.quit_chrome', return_value=True) as q, \
             mock.patch('agent_chrome.launch', return_value=8), \
             mock.patch('agent_chrome.list_browsers', side_effect=lambda _t: next(seen)), \
             mock.patch('apply_tab.Session', Session), mock.patch('time.sleep'):
            ok, _ = agent_chrome.setup('/tmp/board.html', wait=2, force=True)    # 他確認過了
        self.assertTrue(ok)
        q.assert_called_once()

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

    def test_login_polling_never_runs_a_second_claude_while_the_chrome_is_busy(self):
        # 他在跳出來的視窗登入時,每 20 秒叫一次 claude -p --chrome 看連上了沒(最多 15 分鐘):
        # 以前不管代投、查應徵進度是不是正拿著 agent 的 Chrome,同一時間就有兩個 Claude 在裡面
        from types import SimpleNamespace
        clock = iter(range(0, 10_000, 20))
        with mock.patch('threading.Thread', side_effect=lambda target, daemon: SimpleNamespace(start=target)), \
             mock.patch('time.time', side_effect=lambda: next(clock)), mock.patch('time.sleep'), \
             mock.patch('agent_chrome.claude_device', return_value='dev'), \
             mock.patch('agent_chrome.claude_connected', return_value=False) as asked:
            agent_chrome._wait_for_claude(every=20, limit=100, busy=lambda: True)
        asked.assert_not_called()

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



class ErrorsSayWhyAndWhichButton(unittest.TestCase):
    """錯誤訊息要講對原因、給他畫面上真的有的那顆鈕。"""

    def test_replies_tell_him_the_button_he_actually_has(self):
        # 查應徵進度連不上時,以前叫他「打開 Chrome 的「agent」設定檔並完成登入」(已經淘汰的做法),不管用哪一家
        import sys
        import tempfile
        from types import SimpleNamespace
        import reply_run as rr
        u = 'https://job.example/1'
        for program_reads, button in ((True, '連接 Codex'), (False, '連接 Claude')):
            chrome = SimpleNamespace(ensure=lambda *_: (False, '連不上'), wait_claude=lambda *_: (False, '看不到'),
                                     close_if_idle=lambda *_: None)
            with tempfile.TemporaryDirectory() as d, \
                 mock.patch.object(rr, 'SP', d), mock.patch.object(rr, 'load', return_value=({u: {'id': u}}, {})), \
                 mock.patch.object(rr, 'waiting', return_value=[u]), mock.patch.object(rr.jobrun, 'write'), \
                 mock.patch.object(rr.agent_report, 'report') as report, \
                 mock.patch.object(rr, '_program_can_read', return_value=program_reads), \
                 mock.patch.dict(sys.modules, {'agent_chrome': chrome}), \
                 mock.patch.object(sys, 'argv', ['reply_run.py', '--board', os.path.join(d, 'board.html')]):
                self.assertEqual(rr.main(), 1)
            need = report.call_args.kwargs['need']
            self.assertIn(button, need)
            self.assertNotIn('設定檔', need)

    def test_claude_users_are_not_told_the_platform_profile_cannot_be_read_back(self):
        # 只用 Claude:程式自己開頁讀走 Codex 的外掛,Claude 沒有這條路;平台履歷改由 Claude 在那一輪讀給程式(#288)。
        # 以前叫他按畫面上根本沒有的「🔌 連接 Codex」,後來寫「讀不回,改用 Codex」:都不對
        with mock.patch('agent_run.browser_runtime', return_value='claude-code'), \
             mock.patch('agent_chrome.conf', return_value={'claude_device': 'dev'}):
            with self.assertRaises(RuntimeError) as e:
                agent_chrome.read_pages(['https://pda.104.com.tw/profile'])
        msg = str(e.exception)
        self.assertNotIn('Codex', msg)
        self.assertNotIn('讀不回', msg)
        self.assertIn('Claude', msg)
        self.assertLessEqual(len(msg), 80)         # profile_sync 只留前 80 字放在卡上

    def test_no_chrome_on_this_mac_is_said_plainly(self):
        # 沒裝 Chrome:以前等 15 秒後說「開不起來」叫他按連接;🔑 則是 Popen('') 丟 OSError,而且先把「叫出來過」記下了
        import tempfile, shutil
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with mock.patch('chrome_bin.find', return_value=''), mock.patch('agent_chrome.pid', return_value=None), \
             mock.patch('agent_chrome.conf', return_value={'instance': 'i', 'dir': agent_chrome.data_dir()}), \
             mock.patch('agent_chrome.save') as save, mock.patch('subprocess.run') as run, \
             mock.patch('subprocess.Popen') as popen, mock.patch('time.sleep') as sleep:
            ok, msg = agent_chrome.ensure()
            self.assertFalse(ok)
            self.assertIn('找不到 Google Chrome', msg)
            ok, msg = agent_chrome.wait_claude()
            self.assertIn('找不到 Google Chrome', msg)
            ok, msg = agent_chrome.show()
            self.assertFalse(ok)
            self.assertIn('找不到 Google Chrome', msg)
        popen.assert_not_called()
        run.assert_not_called()
        sleep.assert_not_called()                         # 不用白等 15 秒
        save.assert_not_called()

    def test_codex_row_shows_when_it_was_last_seen_connected(self):
        # 設定頁 Codex 那一列以前只要記過外掛身分就寫「✅ 已連接」,外掛斷線了也一樣;改成講最近一次實際連上的時間
        class Session:
            def __init__(self, _):
                pass

            def close(self):
                pass
        saved = {}
        with mock.patch('agent_chrome.conf', return_value={'instance': 'i', 'dir': agent_chrome.data_dir()}), \
             mock.patch('agent_chrome.save', side_effect=saved.update), mock.patch('agent_chrome.launch', return_value=7), \
             mock.patch('agent_chrome.browser_id', return_value='b1'), mock.patch('apply_tab.Session', Session):
            self.assertTrue(agent_chrome.ensure()[0])
        self.assertTrue(saved.get('codex_checked'))
        import settings_api as sa
        with mock.patch('agent_chrome.conf', return_value={'instance': 'i', 'dir': agent_chrome.data_dir(),
                                                           'codex_checked': '2026-09-29T17:06:02'}):
            self.assertEqual(sa.get()['browser_ok'], '2026-09-29T17:06:02')



class ChromeIsPutAwayAfterUse(unittest.TestCase):
    """都處理完就把 agent 的 Chrome 收掉(ADR 0003),不留一個開著的瀏覽器在背景。"""

    def test_claude_replies_also_put_the_chrome_away(self):
        # 以前只有程式自己讀(Codex)的那條會收;用 Claude 查應徵進度,做完 Chrome 一直開著
        import sys
        import tempfile
        from types import SimpleNamespace
        import agent_run as ar
        import reply_run as rr
        u = 'https://job.example/1'
        closed = []
        chrome = SimpleNamespace(wait_claude=lambda *_: (True, ''), close_if_idle=lambda b=None: closed.append(b))
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, 'board.html')
            with mock.patch.object(rr, 'SP', d), mock.patch.object(rr, 'load', return_value=({u: {'id': u}}, {})), \
                 mock.patch.object(rr, 'waiting', return_value=[u]), mock.patch.object(rr.jobrun, 'write'), \
                 mock.patch.object(rr.agent_report, 'report'), \
                 mock.patch.object(rr, '_program_can_read', return_value=False), \
                 mock.patch.object(rr, 'collect_sources', return_value=({}, [u])), \
                 mock.patch.object(rr, 'prompt_for', return_value='prompt'), \
                 mock.patch.object(rr.ar, 'run', return_value=ar.AgentResult('failed', 9, 1)), \
                 mock.patch.dict(sys.modules, {'agent_chrome': chrome}), \
                 mock.patch.object(sys, 'argv', ['reply_run.py', '--board', board]):
                rr.main()
        self.assertEqual(closed, [board])

    def test_connect_buttons_put_the_chrome_away_when_done(self):
        # 按完「🔌 連接 Codex / Claude」以前就讓 agent 的 Chrome 一直開在背景
        with mock.patch('agent_run.claude_bin', return_value='/bin/claude'), \
             mock.patch('agent_chrome.claude_state', return_value=(True, None)), \
             mock.patch('agent_chrome.launch', return_value=7), \
             mock.patch('agent_chrome.claude_device', return_value='dev'), \
             mock.patch('agent_chrome.claude_connected', return_value=True), \
             mock.patch('agent_chrome.conf', return_value={}), mock.patch('agent_chrome.save'), \
             mock.patch('agent_chrome.close_if_idle', return_value='') as close:
            self.assertTrue(agent_chrome.claude_setup(board='/tmp/board.html')[0])
        close.assert_called_once_with('/tmp/board.html')



class ClaudeCannotDriveChromeWithHaiku(unittest.TestCase):
    """Haiku 不能操作 Claude in Chrome(回「requires permission」,docs/agent-chrome.md)。
    以前設定頁不擋也不提醒,連接測試又不帶設定的模型,選了 Haiku 也顯示連得上,之後每次填表都失敗。"""

    def test_settings_refuse_haiku_for_the_chrome_agent(self):
        import settings_api as sa

        def conf(model, browser=True):
            return {'agent': {'agents': [{'id': 'c', 'runtime': 'claude-code', 'model': model, 'effort': 'max', 'browser': browser}]}}
        for m in ('haiku', 'claude-haiku-4-5'):
            self.assertTrue([b for b in sa._check(conf(m)) if 'Haiku' in b], m)
        self.assertFalse([b for b in sa._check(conf('sonnet')) if 'Haiku' in b])
        self.assertFalse([b for b in sa._check(conf('')) if 'Haiku' in b])
        self.assertFalse([b for b in sa._check(conf('haiku', browser=False)) if 'Haiku' in b])   # 不操作 Chrome 的照樣可以用

    def test_connect_check_uses_the_model_he_set(self):
        import config as cf
        agents = {'agents': [{'id': 'c', 'runtime': 'claude-code', 'model': 'claude-sonnet-4-5', 'effort': 'max', 'browser': True}]}
        with mock.patch.dict(cf.C, {'agent': agents}), mock.patch('agent_run.claude_bin', return_value='/bin/claude'), \
             mock.patch('subprocess.run', return_value=mock.Mock(stdout='dev-1')) as run:
            self.assertTrue(agent_chrome.claude_connected('dev-1'))
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index('--model') + 1], 'claude-sonnet-4-5')



class CodexSiteAllowList(unittest.TestCase):
    """Codex 上傳、下載前要先在 ~/.codex/browser/config.toml 允許網站,不然每次都問「允許嗎?」,背景沒人按就卡住。
    以前只寫在文件:設定頁、環境檢查、連上的訊息、失敗訊息都沒提,照設定頁操作的朋友第一次填表就卡住。"""

    def setUp(self):
        import tempfile, shutil
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, 'config.toml')
        p = mock.patch.object(agent_chrome, 'CODEX_BROWSER_CONFIG', self.path, create=True)
        p.start(); self.addCleanup(p.stop)

    def write(self, text):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(text)

    def need(self, ups=(), downs=()):
        return mock.patch.object(agent_chrome, 'codex_sites_needed', return_value={'uploads': list(ups), 'downloads': list(downs)})

    def test_only_this_persons_sites_that_are_still_missing(self):
        # 開源:要允許哪些網站從他自己的卡和平台履歷推出來,不是照某個人投的那幾家
        with self.need(['jobs.example-ats.com', 'apply.other.test'], ['profile.jobsite.test']):
            self.assertEqual(agent_chrome.codex_sites_missing(),                              # 沒有這個檔
                             {'uploads': ['jobs.example-ats.com', 'apply.other.test'], 'downloads': ['profile.jobsite.test']})
            self.write('[uploads]\nallowed = ["jobs.example-ats.com", "*.unrelated.test"]\n[downloads]\nallowed = ["*.jobsite.test"]\n')
            self.assertEqual(agent_chrome.codex_sites_missing(), {'uploads': ['apply.other.test']})
            snip = agent_chrome.codex_sites_snippet(agent_chrome.codex_sites_missing())
            self.assertIn('"jobs.example-ats.com", "*.unrelated.test", "apply.other.test"', snip)   # 原本的留著,補上缺的
            self.assertNotIn('104', snip)
            self.write('[uploads\nallowed=')                                                  # 讀不懂:當成沒設
            self.assertEqual(sorted(agent_chrome.codex_sites_missing()), ['downloads', 'uploads'])
        with self.need():                                                                      # 還沒有要投的卡:沒有缺
            self.assertEqual(agent_chrome.codex_sites_missing(), {})

    def test_sites_come_from_the_cards_and_platform_profiles(self):
        import board_doc as bd
        fb = {'https://jobs.example-ats.com/a/1': {'app': 'ship'}, 'https://apply.other.test/2': {'app': 'ready'},
              'https://gone.test/3': {'app': 'ship', 'rm': 1}, 'https://sent.test/4': {'app': 'sent'}}
        reg = {'jobsite': {'zh/x': {'read': 'https://profile.jobsite.test/p?v=1'}}, '_attachment_checks': {}}
        with mock.patch.object(bd, 'parse', return_value={'fb': json.dumps(fb), 'data': {'jobs': []}}), \
             mock.patch('builtins.open', mock.mock_open(read_data='x')), \
             mock.patch('profile_sync.registry', return_value=reg):
            self.assertEqual(agent_chrome.codex_sites_needed('/tmp/b.html'),
                             {'uploads': ['apply.other.test', 'jobs.example-ats.com'], 'downloads': ['profile.jobsite.test']})

    def test_environment_check_says_what_to_add(self):
        import doctor
        agents = [{'id': 'codex', 'runtime': 'codex', 'browser': True}]
        with mock.patch.object(doctor, '_runtime_path', return_value='/fake/codex'), \
             mock.patch.object(doctor, '_logged_in', return_value=True), self.need(['a.test'], ['b.test']):
            row = {c['key']: c for c in doctor.check_environment(agents)['checks']}['codex_sites']
            self.assertFalse(row['ok'])
            self.assertTrue(row.get('required', True))        # 沒允許第一次傳履歷就卡住:不是選用
            self.assertIn('a.test', row['detail'])
            self.assertIn('allowed = ["a.test"]', row['fix'])
            self.write('[uploads]\nallowed = ["a.test"]\n[downloads]\nallowed = ["*.b.test"]\n')
            row = {c['key']: c for c in doctor.check_environment(agents)['checks']}['codex_sites']
            self.assertTrue(row['ok'])

    def test_connect_message_mentions_it_until_it_is_set(self):
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, 'Default', 'Extensions', 'hehggadaopoacecdllhhajmbjkdcmajg'))

        class Session:
            def __init__(self, _):
                pass

            def close(self):
                pass
        seen = iter([[], [('b2', 'new-id')]])
        with mock.patch('agent_chrome._codex_ready', return_value=True), mock.patch('agent_chrome.data_dir', return_value=d), \
             mock.patch('agent_chrome.prepare', return_value=''), mock.patch('agent_chrome.conf', return_value={}), \
             mock.patch('agent_chrome.save'), mock.patch('agent_chrome.pid', return_value=None), \
             mock.patch('agent_chrome.quit_chrome', return_value=True), mock.patch('agent_chrome.launch', return_value=5), \
             mock.patch('agent_chrome.close_if_idle', return_value=''), mock.patch('apply_tab.Session', Session), \
             mock.patch('agent_chrome.list_browsers', side_effect=lambda _t: next(seen)), mock.patch('time.sleep'), \
             self.need(['a.test']):
            ok, msg = agent_chrome.setup(wait=2)
        self.assertTrue(ok)
        self.assertIn('config.toml', msg)

    def test_codex_connect_without_the_extension_gives_the_store_url(self):
        # 連接 Codex 時 agent 的 Chrome 還沒裝 ChatGPT 擴充功能:訊息直接附商店網址,不要他自己去商店找
        import tempfile
        d = tempfile.mkdtemp()
        with mock.patch('agent_chrome._codex_ready', return_value=True), mock.patch('agent_chrome.data_dir', return_value=d), \
             mock.patch('agent_chrome.prepare', return_value=''):
            ok, msg = agent_chrome.setup(wait=2)
        self.assertFalse(ok)
        self.assertIn('https://chromewebstore.google.com/detail/hehggadaopoacecdllhhajmbjkdcmajg', msg)

    def test_a_blocked_download_on_the_card_says_which_file_to_edit(self):
        text = agent_chrome.explain_blocked('downloadMedia: could not complete the permission request to download files')
        self.assertIn('~/.codex/browser/config.toml', text)
        self.assertEqual(agent_chrome.explain_blocked('頁面要登入'), '頁面要登入')


if __name__ == '__main__':
    unittest.main()


class QuitOnlyThroughTheGuard(unittest.TestCase):
    """關掉 agent 的 Chrome 會連等他核對的頁一起關:只准走 quit_if_safe 這一個入口(#293)。"""

    def test_nobody_calls_quit_chrome_directly(self):
        import ast, glob
        tools = os.path.abspath(os.path.join(HERE, '..', 'tools'))
        bad = []
        for path in glob.glob(os.path.join(tools, '*.py')):
            tree = ast.parse(open(path, encoding='utf-8').read())
            for fn in ast.walk(tree):
                if isinstance(fn, ast.FunctionDef) and fn.name != 'quit_if_safe':
                    for node in ast.walk(fn):
                        if isinstance(node, ast.Call) and getattr(node.func, 'id', getattr(node.func, 'attr', '')) == 'quit_chrome':
                            bad.append(f'{os.path.basename(path)}:{node.lineno} {fn.name}')
        # 巢狀函式會在外層再被看到一次:只要不在 quit_if_safe 裡就是違規
        self.assertEqual(sorted(set(bad)), [])

    def test_unsure_means_keep_it(self):
        with mock.patch('agent_chrome.pid', return_value=9), mock.patch('agent_chrome.user_has_it_open', return_value=False), \
             mock.patch('agent_chrome.quit_chrome', return_value=True) as q:
            with mock.patch('agent_chrome.protected_tabs', return_value=None):          # 讀不到看板
                self.assertIsNone(agent_chrome.quit_if_safe('/x', ask_extension=False)[0])
            with mock.patch('agent_chrome.protected_tabs', side_effect=lambda b, runtime=None: set() if runtime else {'7'}), \
                 mock.patch('agent_chrome.conf', return_value={}):                         # 看板記著 Codex 的頁、沒有外掛身分可問
                self.assertIsNone(agent_chrome.quit_if_safe('/x')[0])
            q.assert_not_called()
            with mock.patch('agent_chrome.protected_tabs', return_value=set()):
                self.assertTrue(agent_chrome.quit_if_safe('/x')[0])
            q.assert_called_once()
