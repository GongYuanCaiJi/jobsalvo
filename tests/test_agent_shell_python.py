"""agent 的 login shell 要找得到程式自己的 Python:拍照工具的 shebang 是 python3,系統那支版本不夠就跑不起來。"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

import _env  # noqa: F401
import agent_run


@unittest.skipUnless(shutil.which('zsh'), 'agent 的 login shell 是 zsh,這台沒有')
class AgentShell(unittest.TestCase):
    def test_capture_tool_runs_on_the_programs_python_after_the_system_reorders_the_path(self):
        # macOS 的 /etc/zprofile(path_helper)會把系統路徑排回最前面。這裡直接把一支壞的 python3 排到最前面造出同一個狀態。
        with tempfile.TemporaryDirectory() as folder:
            wrong = os.path.join(folder, 'python3')
            with open(wrong, 'w', encoding='utf-8') as target:
                target.write('#!/bin/sh\necho 系統的舊 python3\nexit 9\n')
            os.chmod(wrong, stat.S_IRWXU)
            env = agent_run.agent_env(os.path.join(folder, 'board.html'), (), 'no workspace in this prompt', chrome=True)
            env['PATH'] = os.pathsep.join((folder, env['PATH']))
            result = subprocess.run(['zsh', '-lc', 'jobsalvo-before'], env=env, capture_output=True, text=True,
                                    timeout=60, check=False)
        self.assertNotIn('系統的舊 python3', result.stdout + result.stderr)
        self.assertIn('JOBSALVO_EGO_WORKSPACE', result.stdout + result.stderr, '工具本身要跑起來,才會問工作區')

    def test_before_and_after_run_from_outside_tools_without_a_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            env = agent_run.agent_env(os.path.join(folder, 'board.html'), (), 'no workspace in this prompt', chrome=True)
            for command in ('jobsalvo-before', 'jobsalvo-after'):
                with self.subTest(command=command):
                    result = subprocess.run([os.path.join(agent_run.HERE, command)], cwd=folder, env=env,
                                            capture_output=True, text=True, timeout=60, check=False)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('沒有可拍的工作區：缺少 JOBSALVO_EGO_WORKSPACE', result.stderr)


if __name__ == '__main__':
    unittest.main()
