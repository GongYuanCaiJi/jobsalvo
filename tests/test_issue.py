# -*- coding: utf-8 -*-
"""對 GitHub issue 寫東西前的私人字串把關:有就不送、清單不在也不送、gh 參數原樣交出去。"""
import contextlib, os, sys, re, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import privacy_scan as ps  # noqa: E402
import issue  # noqa: E402

WORDS = ([('Alice', re.compile(r'(?<![A-Za-z0-9])Alice(?![A-Za-z0-9])', re.I))], '/x/private-words')


class Issue(unittest.TestCase):
    def run_main(self, argv, words=WORDS):
        with mock.patch.object(ps, 'words', return_value=words), \
             mock.patch.object(issue.subprocess, 'run') as run:
            run.return_value.returncode = 0
            rc = issue.main(argv)
        return rc, run

    def test_clean_text_goes_to_gh_unchanged(self):
        rc, run = self.run_main(['create', '--title', '可投遞夾收成一個 module', '--label', 'needs-triage'])
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_args[0][0],
                         ['gh', 'issue', 'create', '--title', '可投遞夾收成一個 module', '--label', 'needs-triage'])

    def test_private_word_blocks(self):
        for argv in (['create', '--title', 'alice 的看板'], ['comment', '3', '--body=見 Alice'],
                     ['create', '-t', 'x', '-b', '寄到 a.b@example.com'],
                     ['edit', '3', '--body', '路徑 /' + 'Users/someone/x']):   # 拆開寫:整段路徑本身會被 commit 前的掃描擋
            rc, run = self.run_main(argv)
            self.assertEqual(rc, 1, argv)
            run.assert_not_called()

    def test_body_file_is_scanned(self):
        p = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'issue-test-body.md')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('第一行\n第二行有 Alice\n')
        try:
            rc, run = self.run_main(['create', '--title', 't', '--body-file', p])
        finally:
            os.remove(p)
        self.assertEqual(rc, 1)
        run.assert_not_called()

    def test_no_word_list_means_not_safe(self):
        rc, run = self.run_main(['create', '--title', '完全沒問題'], words=(None, '/x/private-words'))
        self.assertEqual(rc, 1)
        run.assert_not_called()


class WordListInWorktree(unittest.TestCase):
    """在 git worktree 裡也要找得到主 repo 的字詞清單(以前會找錯地方、安靜跳過)。"""
    def test_found_from_worktree(self):
        import tempfile, subprocess
        d = tempfile.mkdtemp()
        # pre-commit 跑測試時 git 會帶 GIT_DIR/GIT_INDEX_FILE 進來;不清掉,下面的 git 會動到真正的 repo
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        run = lambda *a, cwd=d: subprocess.run(a, cwd=cwd, env=env, check=True, capture_output=True)
        run('git', 'init', '-q', 'main')
        m = os.path.join(d, 'main')
        run('git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'core.hooksPath=/dev/null', 'commit', '-q', '--allow-empty', '-m', 'x', cwd=m)
        with open(os.path.join(m, '.git', 'info', 'private-words'), 'w') as f:
            f.write('Alice\n')
        run('git', 'worktree', 'add', '-q', os.path.join(d, 'wt'), cwd=m)
        with contextlib.chdir(os.path.join(d, 'wt')), mock.patch.dict(os.environ, env, clear=True):
            pats, where = ps.words()
        self.assertEqual([w for w, _ in pats], ['Alice'])


if __name__ == '__main__':
    unittest.main()
