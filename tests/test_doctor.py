# -*- coding: utf-8 -*-
"""環境檢查:紅燈 ⇔ 真的跑不動。誤報(明明能跑卻擋)跟漏報(跑不動卻通過)一樣要擋;
程式新呼叫的外部指令一定要對到一項檢查或寫明為什麼不查,環境檢查才不會過時。"""
import os, re, sys, glob, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
import doctor  # noqa: E402


def agents(*runtimes):
    return [{'id': r, 'runtime': r, 'browser': False} for r in runtimes]


class AgentCapability(unittest.TestCase):
    def run_case(self, configured, installed):
        """installed:{runtime: 登入狀態 True/False/None};沒列的 = 沒裝。"""
        with mock.patch.object(doctor, '_runtime_path', side_effect=lambda r, which=None: f'/fake/{r}' if r in installed else None), \
                mock.patch.object(doctor, '_logged_in', side_effect=lambda r, p: installed[r]):
            result = doctor.check_environment(agents(*configured))
        return result, {c['key']: c for c in result['checks']}

    def test_red_light_only_when_no_configured_agent_can_run(self):
        cases = [
            # (設定的, 裝了的{runtime: 登入}, 至少一個能用?, 給「改用」哪一種)
            (['codex'], {'codex': True}, True, None),
            (['claude-code'], {'claude-code': True}, True, None),                 # 只有 Claude 也能跑,不要求 Codex
            (['codex'], {'claude-code': True}, False, 'claude-code'),             # 設定寫 Codex、只裝 Claude:一鍵改用
            (['codex'], {'codex': False}, False, None),                           # 裝了沒登入:跑不動
            (['codex', 'claude-code'], {'claude-code': True}, True, None),        # 清單裡有一個能用就好
            (['codex', 'claude-code'], {'codex': False, 'claude-code': True}, True, None),
            (['claude-code'], {'claude-code': None}, True, None),                 # 登入狀態查不到:不擋(不誤報)
            ([], {}, False, None),
            (['codex'], {}, False, None),
        ]
        for configured, installed, ok, switch in cases:
            with self.subTest(configured=configured, installed=installed):
                _result, checks = self.run_case(configured, installed)
                row = checks['agent']
                self.assertEqual(row['ok'], ok)
                self.assertTrue(row.get('required', True))
                self.assertEqual((row.get('action') or {}).get('use_runtime'), switch)
                for rt in configured:            # 各自那一列只講狀態,不單獨擋人
                    self.assertFalse(checks[rt.replace('-', '_')]['required'])

    def test_not_logged_in_says_how_to_log_in_and_missing_says_how_to_install(self):
        _r, checks = self.run_case(['codex'], {'codex': False})
        self.assertIn('還沒登入', checks['codex']['detail'])
        self.assertIn('codex login', checks['codex']['fix'])
        _r, checks = self.run_case(['codex'], {})
        self.assertIn('安裝 Codex CLI', checks['codex']['fix'])

    def test_codex_found_only_inside_the_app_says_the_real_path_to_log_in(self):
        # ChatGPT App 裡附的 codex 不在 PATH 上:叫他在終端機打「codex login」只會得到 command not found
        app = '/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex'
        with mock.patch.object(doctor, '_runtime_path', return_value=app), \
                mock.patch.object(doctor, '_logged_in', return_value=False), \
                mock.patch.object(doctor.shutil, 'which', return_value=None):
            row = {c['key']: c for c in doctor.check_environment(agents('codex'))['checks']}['codex']
        self.assertIn(app + ' login', row['fix'])

    def test_terminal_never_says_press_a_button_and_install_can_go_on_to_the_board(self):
        # 安裝程式在終端機跑環境檢查,那時看板還沒開:不能只叫他「按「改用 X」」。
        # 只差這一顆按鈕時回 2,安裝程式照樣把看板打開讓他按;其他沒過的照舊回 1 停下來。
        import io, contextlib
        def main_with(configured, installed):
            out = io.StringIO()
            with mock.patch.object(doctor, '_runtime_path', side_effect=lambda r, which=None: f'/fake/{r}' if r in installed else None), \
                    mock.patch.object(doctor, '_logged_in', side_effect=lambda r, p: installed[r]), \
                    mock.patch.object(doctor, 'check_environment', wraps=lambda: real(agents(*configured))), \
                    mock.patch('chrome_bin.find', return_value='/fake/chrome'), \
                    contextlib.redirect_stdout(out):
                code = doctor.main([])
            return code, out.getvalue()
        real = doctor.check_environment
        code, text = main_with(['codex'], {'claude-code': True})
        self.assertEqual(code, 2, text)
        line = next(l for l in text.splitlines() if '改用 Claude Code' in l)
        self.assertIn('看板', line)
        code, _ = main_with(['codex'], {})
        self.assertEqual(code, 1)

    def test_login_is_read_from_local_status_commands_only(self):
        ok = mock.Mock(returncode=0, stdout='Logged in using ChatGPT', stderr='')
        no = mock.Mock(returncode=1, stdout='Not logged in', stderr='')
        with mock.patch.object(doctor.subprocess, 'run', return_value=ok) as run:
            self.assertTrue(doctor._logged_in('codex', '/x/codex'))
            self.assertEqual(run.call_args[0][0], ['/x/codex', 'login', 'status'])
        with mock.patch.object(doctor.subprocess, 'run', return_value=no):
            self.assertFalse(doctor._logged_in('codex', '/x/codex'))
        for out, want in (('{"loggedIn": true}', True), ('{"loggedIn": false}', False), ('看不懂', None)):
            with mock.patch.object(doctor.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=out, stderr='')) as run:
                self.assertEqual(doctor._logged_in('claude-code', '/x/claude'), want, out)
                self.assertEqual(run.call_args[0][0], ['/x/claude', 'auth', 'status'])
        with mock.patch.object(doctor.subprocess, 'run', side_effect=doctor.subprocess.TimeoutExpired('x', 15)):
            self.assertIsNone(doctor._logged_in('codex', '/x/codex'))
        self.assertIsNone(doctor._logged_in('command-code', '/x/cc'))   # 沒有官方的查法:不猜


    def test_browser_agent_only_counts_when_that_agent_can_run(self):
        ags = [{'id': 'codex', 'runtime': 'codex', 'browser': True}]
        for installed, want in (({}, False), ({'codex': True}, True)):
            with mock.patch.object(doctor, '_runtime_path', side_effect=lambda r, which=None: f'/fake/{r}' if r in installed else None), \
                    mock.patch.object(doctor, '_logged_in', side_effect=lambda r, p: installed[r]), \
                    mock.patch('agent_chrome.conf', return_value={}):
                checks = {c['key']: c for c in doctor.check_environment(ags)['checks']}
            self.assertEqual(checks['browser_agent']['ok'], want, installed)


class FolderHistoryRow(unittest.TestCase):
    """資料夾不自動存版、或舊資料因為沒有退回點沒轉:環境檢查照實寫原因和怎麼處理(以前只寫在設定頁最底下一行)。"""

    def history_row(self, home):
        import config as cf
        with mock.patch.object(cf, 'HOME', home):
            result = doctor.check_environment([])
        return result, next((c for c in result['checks'] if c['key'] == 'folder_history'), None)

    def test_repository_with_a_remote_is_reported_with_how_to_fix(self):
        import subprocess, tempfile, folder_history
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        with tempfile.TemporaryDirectory(prefix='doctor-history-') as home:
            for args in (('init', '--initial-branch=main'), ('remote', 'add', 'origin', 'https://example.invalid/x.git')):
                subprocess.run([folder_history._git(), *args], cwd=home, env=env, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _, row = self.history_row(home)
        self.assertFalse(row['ok'])
        self.assertIn('remote', row['detail'])
        self.assertTrue(row['fix'])

    def test_conversion_without_a_restore_point_is_reported(self):
        import tempfile, folder_history
        with tempfile.TemporaryDirectory(prefix='doctor-history-') as home:
            with open(os.path.join(home, folder_history.BACKUP_DIR), 'w') as f:
                f.write('x')
            with open(os.path.join(home, 'board.html'), 'w') as f:
                f.write('old')
            with mock.patch.object(folder_history, '_git', return_value=None):
                folder_history.convert(home, [os.path.join(home, 'board.html')], '投遞狀態轉換', lambda: None)
                _, row = self.history_row(home)
        self.assertFalse(row['ok'])
        self.assertIn('沒有退回點', row['detail'])
        self.assertTrue(row['fix'])

    def test_saving_folder_or_missing_folder_is_not_a_warning(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='doctor-history-') as home:
            result, row = self.history_row(home)
            self.assertTrue(row is None or row['ok'])
            _, row = self.history_row(os.path.join(home, 'not-created-yet'))
            self.assertTrue(row is None or row['ok'])


class Coverage(unittest.TestCase):
    def test_shell_variables_are_not_glued_to_non_ascii(self):
        # 「$log。」:Mac 內建的 bash 會把中文標點的位元組當成變數名的一部分 → unbound variable(乾淨的 Mac 上實際崩過)
        bad = []
        for path in glob.glob(os.path.join(TOOLS, '**', '*.sh'), recursive=True):
            with open(path, encoding='utf-8') as f:
                for n, line in enumerate(f, 1):
                    if re.search(r'\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]', line):
                        bad.append(f'{os.path.basename(path)}:{n}')
        self.assertEqual(bad, [], '變數後面直接接中文要寫成 ${var}')

    def test_every_external_command_is_checked_or_explained(self):
        pat = re.compile(r"(?:shutil\.which|which)\(['\"]([\w.-]+)['\"]\)"
                         r"|subprocess\.(?:run|Popen|check_output|call)\(\[['\"]([\w./-]+)['\"]")
        found = {}
        for path in glob.glob(os.path.join(TOOLS, '*.py')):
            with open(path, encoding='utf-8') as f:
                for m in pat.finditer(f.read()):
                    found.setdefault(m.group(1) or m.group(2), os.path.basename(path))
        missing = {cmd: where for cmd, where in found.items()
                   if cmd not in doctor.CHECKED and cmd not in doctor.NOT_CHECKED}
        self.assertEqual(missing, {}, '程式呼叫了這些外部指令,環境檢查沒查、也沒寫為什麼不查(tools/doctor.py 的 CHECKED / NOT_CHECKED)')


if __name__ == '__main__':
    unittest.main()
