# -*- coding: utf-8 -*-
"""新建的資料夾有自己的暫存資料夾與 agent Chrome 連線紀錄,同一台電腦兩份資料夾不互相踩;已經在用的不改。"""
import json, os, shutil, sys, tempfile, unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import config as cf  # noqa: E402
import init  # noqa: E402


class OwnPaths(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='init-test-')
        home0 = cf.HOME
        self.addCleanup(lambda: cf.reload(home0))
        self.addCleanup(shutil.rmtree, self.root, True)

    def config_of(self, home):
        with open(os.path.join(home, 'jobsalvo.json'), encoding='utf-8') as f:
            return json.load(f)

    def test_two_new_homes_do_not_share_tmp_or_agent_chrome_state(self):
        a, b = os.path.join(self.root, 'a'), os.path.join(self.root, 'b')
        init.scaffold(a)
        init.scaffold(b)
        ca, cb = self.config_of(a), self.config_of(b)
        self.assertNotEqual(ca['paths']['tmp'], cb['paths']['tmp'])
        self.assertNotEqual(ca['browser']['state'], cb['browser']['state'])
        self.assertNotEqual(ca['paths']['tmp'], '/tmp/jobsalvo')
        self.assertEqual(ca['agent']['name'], 'Agent')          # 範例裡原本的設定還在

    def test_board_server_first_run_uses_the_new_homes_own_paths(self):
        # 看板第一次在空資料夾啟動:建好設定後,這個行程要用這份資料夾自己的暫存和連線檔
        import board_server as bs
        home = os.path.join(self.root, 'fresh')
        os.makedirs(home)
        cf.reload(home)
        self.assertEqual(cf.TMP, '/tmp/jobsalvo')
        bs.first_run(cf.LIVE)
        self.assertEqual(cf.TMP, self.config_of(home)['paths']['tmp'])
        self.assertNotEqual(cf.TMP, '/tmp/jobsalvo')
        self.assertIn(os.path.basename(cf.TMP).split('-')[-1], cf.BROWSER_STATE)

    def test_port_given_at_install_is_remembered(self):
        # JOBSALVO_PORT 以前只在安裝那一次用到:開機自動啟動(board_serve.sh 不帶 --port)又回到 8899
        new, old, mine = (os.path.join(self.root, n) for n in ('new', 'old', 'mine'))
        for h, data in ((old, {'agent': {'agents': []}}), (mine, {'board': {'port': 9001}})):
            os.makedirs(h)
            with open(os.path.join(h, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
                json.dump(data, f)
        with patch.dict(os.environ, {'JOBSALVO_PORT': '8123'}), patch.object(init, 'installed_agents', return_value=[]):
            for h in (new, old, mine):
                init.scaffold(h)
        self.assertEqual(self.config_of(new)['board']['port'], 8123)
        self.assertEqual(self.config_of(old)['board']['port'], 8123)
        self.assertEqual(self.config_of(mine)['board']['port'], 9001)     # 他設過的不動
        cf.reload(new)
        self.assertEqual(cf.PORT, 8123)

    def test_existing_home_is_left_as_it_was(self):
        home = os.path.join(self.root, 'old')
        os.makedirs(home)
        with open(os.path.join(home, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
            json.dump({'agent': {'name': 'Agent'}}, f)
        with patch.object(init, 'installed_agents', return_value=[]):     # 這台電腦裝了什麼不影響這一條
            init.scaffold(home)
        self.assertEqual(self.config_of(home), {'agent': {'name': 'Agent'}})


class InstalledAgents(unittest.TestCase):
    """新資料夾的 agent 清單照這台電腦裝了什麼:只裝 Claude Code 的人以前連安裝都過不了(檢查要 codex)。"""

    def test_first_installed_runtime_becomes_the_agent(self):
        from unittest.mock import patch
        import agent_run as ar
        for have, want in (({'codex', 'claude'}, 'codex'), ({'claude'}, 'claude-code'),
                           ({'command-code'}, 'command-code'), (set(), None)):
            which = lambda name, have=have: f'/fake/{name}' if name in have else None
            with patch.object(ar, 'CODEX_APP_BINS', ()), patch.object(ar, 'CLAUDE_BINS', ()):
                agents = init.installed_agents(which)
            self.assertEqual(agents[0]['runtime'] if agents else None, want, have)
            if want == 'command-code':
                self.assertFalse(agents[0]['browser'])          # Command Code 不能開瀏覽器

    def test_new_home_writes_the_detected_list(self):
        from unittest.mock import patch
        root = tempfile.mkdtemp(prefix='init-agents-')
        self.addCleanup(shutil.rmtree, root, True)
        home0 = cf.HOME
        self.addCleanup(lambda: cf.reload(home0))
        claude_only = [{'id': 'primary', 'runtime': 'claude-code', 'model': '', 'effort': 'max',
                        'speed': 'standard', 'browser': True}]
        with patch.object(init, 'installed_agents', return_value=claude_only):
            init.scaffold(os.path.join(root, 'h'))
        with open(os.path.join(root, 'h', 'jobsalvo.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['agent']['agents'], claude_only)

    def test_installed_but_logged_out_is_not_picked_over_a_logged_in_one(self):
        # ChatGPT App 裡附了 codex(沒登入),他用的是 Claude Code:以前照「找得到」選 Codex,環境檢查過不了
        import agent_run as ar
        import doctor
        which = lambda name: f'/fake/{name}' if name in ('codex', 'claude') else None
        state = {'codex': False, 'claude-code': True}
        with patch.object(ar, 'CODEX_APP_BINS', ()), patch.object(ar, 'CLAUDE_BINS', ()), \
                patch.object(doctor, '_logged_in', side_effect=lambda rt, path: state[rt]):
            self.assertEqual(init.installed_agents(which)[0]['runtime'], 'claude-code')
            state['claude-code'] = False                        # 都沒登入:照有沒有裝挑,留給環境檢查講要登入
            self.assertEqual(init.installed_agents(which)[0]['runtime'], 'codex')

    def test_rerun_after_installing_an_agent_passes_the_environment_check(self):
        # 先跑安裝(還沒裝任何 agent)→ 只裝 Claude Code → 重跑安裝:環境檢查要過,不能卡在預設的 Codex
        import doctor
        root = tempfile.mkdtemp(prefix='init-rerun-')
        self.addCleanup(shutil.rmtree, root, True)
        home0 = cf.HOME
        self.addCleanup(lambda: cf.reload(home0))
        home = os.path.join(root, 'h')
        with patch.object(init, 'installed_agents', return_value=[]):
            init.scaffold(home)
        claude_only = [{'id': 'primary', 'runtime': 'claude-code', 'model': '', 'effort': 'max',
                        'speed': 'standard', 'browser': True}]
        with patch.object(init, 'installed_agents', return_value=claude_only):
            init.scaffold(home)
        cf.reload(home)
        with patch.object(doctor, '_runtime_path', side_effect=lambda r, which=None: '/fake/claude' if r == 'claude-code' else None), \
                patch.object(doctor, '_logged_in', return_value=True):
            row = [c for c in doctor.check_environment()['checks'] if c['key'] == 'agent'][0]
        self.assertTrue(row['ok'], row)

    def test_rerun_keeps_an_agent_list_he_saved(self):
        root = tempfile.mkdtemp(prefix='init-keep-')
        self.addCleanup(shutil.rmtree, root, True)
        home0 = cf.HOME
        self.addCleanup(lambda: cf.reload(home0))
        home = os.path.join(root, 'h')
        os.makedirs(home)
        mine = {'agent': {'agents': [{'id': 'mine', 'runtime': 'codex', 'model': 'x', 'effort': 'low',
                                      'speed': 'standard', 'browser': True}]}}
        with open(os.path.join(home, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
            json.dump(mine, f)
        with patch.object(init, 'installed_agents', return_value=[{'id': 'primary', 'runtime': 'claude-code'}]):
            init.scaffold(home)
        with open(os.path.join(home, 'jobsalvo.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f), mine)


class PrivateTmp(unittest.TestCase):
    """暫存資料夾裡有給 agent 的指示、履歷片段、Email;/tmp 全機器共用,只能自己讀得到。"""

    def test_tmp_is_created_or_fixed_to_owner_only(self):
        root = tempfile.mkdtemp(prefix='init-private-')
        self.addCleanup(shutil.rmtree, root, True)
        fresh, old = os.path.join(root, 'fresh'), os.path.join(root, 'old')
        os.makedirs(old)
        os.chmod(old, 0o755)                                    # 以前建出來的樣子
        for d in (fresh, old):
            cf._private_dir(d)
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o700, d)


class BrokenSettings(unittest.TestCase):
    """jobsalvo.json 手改壞了(少一個逗號):不能在設定頁存一次就把整份蓋掉,也要讓他知道設定沒生效。"""

    def test_saving_over_a_broken_file_keeps_the_original_and_doctor_says_so(self):
        import glob, doctor
        home = tempfile.mkdtemp(prefix='init-broken-')
        self.addCleanup(shutil.rmtree, home, True)
        f = os.path.join(home, cf.NAME)
        broken = '{"agent": {"agents": []} "board": {}}'
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(broken)
        old = cf.HOME
        cf.HOME = home
        self.addCleanup(lambda: cf.reload(old))
        self.assertTrue(cf.settings_problem())
        row = [c for c in doctor.check_environment(agents=[])['checks'] if c['key'] == 'settings_file']
        self.assertTrue(row and not row[0]['ok'], row)
        with patch.object(cf, 'reload'):
            cf.save({'agent': {'agents': []}})
        kept = glob.glob(f + '.broken-*')
        self.assertEqual(len(kept), 1)
        with open(kept[0], encoding='utf-8') as fh:
            self.assertEqual(fh.read(), broken)
        self.assertEqual(cf.settings_problem(), '')
        self.assertFalse([c for c in doctor.check_environment(agents=[])['checks'] if c['key'] == 'settings_file'])


class ServiceName(unittest.TestCase):
    """開機啟動的服務:預設資料夾照舊的名字,其他資料夾各自一個,裝第二份不會蓋掉第一份。"""

    def test_default_home_keeps_the_old_name_others_get_their_own(self):
        import install_service as svc
        self.assertEqual(svc.label('~/jobsearch'), 'dev.jobsalvo.board-server')
        a, b = svc.label('/tmp/home-a'), svc.label('/tmp/home-b')
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith('dev.jobsalvo.board-server.'))
        self.assertIn(a, svc.plist_path('/tmp/home-a'))


if __name__ == '__main__':
    unittest.main()
