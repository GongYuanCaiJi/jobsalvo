# -*- coding: utf-8 -*-
"""更新跟 main:遠端 main 比較新就能快轉;切在別的分支、有沒提交變更的不動;查不到不炸。不連網,用暫存的假來源。"""
import os, sys, shutil, subprocess, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import update  # noqa: E402

# 全域 git hook 每次 clone/checkout 都會跑,測試裡的假 repo 不需要
NO_HOOKS = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.hooksPath', 'GIT_CONFIG_VALUE_0': '/dev/null',
            'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.test',
            'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.test'}


def git(cwd, *args):
    subprocess.run(['git', '-C', cwd, *args], check=True, capture_output=True, env=dict(os.environ, **NO_HOOKS))


class Update(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 假來源只建一次;每個測試複製整個資料夾,遠端用相對路徑所以複製後還對得上
        cls.base = tempfile.mkdtemp(prefix='update-test-base-')
        origin, work, app = (os.path.join(cls.base, n) for n in ('origin.git', 'maintainer', 'app'))
        env = dict(os.environ, **NO_HOOKS)
        subprocess.run(['git', 'init', '-q', '--template=', '--bare', '-b', 'main', origin], check=True, env=env)
        subprocess.run(['git', 'init', '-q', '--template=', '-b', 'main', work], check=True, env=env)
        git(work, 'remote', 'add', 'origin', '../origin.git')
        with open(os.path.join(work, 'f.txt'), 'w') as f:
            f.write('1')
        git(work, 'add', 'f.txt')
        git(work, 'commit', '-q', '-m', 'one')
        git(work, 'push', '-q', 'origin', 'main')
        subprocess.run(['git', 'clone', '-q', '--template=', origin, app], check=True, env=env)
        git(app, 'remote', 'set-url', 'origin', '../origin.git')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def setUp(self):
        env = mock.patch.dict(os.environ, NO_HOOKS)
        env.start()
        self.addCleanup(env.stop)
        self.root = tempfile.mkdtemp(prefix='update-test-')
        shutil.rmtree(self.root)
        shutil.copytree(self.base, self.root, symlinks=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.work, self.clone = os.path.join(self.root, 'maintainer'), os.path.join(self.root, 'app')

    def push_new_commit(self):
        with open(os.path.join(self.work, 'f.txt'), 'w') as f:
            f.write('2')
        git(self.work, 'commit', '-q', '-am', 'two')
        git(self.work, 'push', '-q', 'origin', 'main')

    def test_up_to_date_then_new_commit_on_main(self):
        c = update.check(self.clone, cache=False)
        self.assertEqual((c['new'], c['blocked']), (False, ''))
        self.push_new_commit()
        c = update.check(self.clone, cache=False)
        self.assertTrue(c['new'])
        self.assertTrue(c['latest'])

    def test_cached_answer_expires_within_the_hour_and_says_when(self):
        # 以前記一整天:早上查過,下午合進 main 的修正整天看不到,設定頁還說「已經是最新」
        cache = os.path.join(self.root, 'update-check.json')
        with mock.patch.object(update, '_cache_path', return_value=cache):
            first = update.check(self.clone, now=1000)
            self.push_new_commit()
            self.assertFalse(update.check(self.clone, now=1000 + 60)['new'])      # 一分鐘內用上次的答案
            later = update.check(self.clone, now=1000 + 2 * 3600)
        self.assertTrue(later['new'])
        self.assertEqual(first['checked'], 1000)
        self.assertEqual(later['checked'], 1000 + 2 * 3600)

    def test_apply_fast_forwards_main(self):
        self.push_new_commit()
        with mock.patch.dict(os.environ, {'JOBSALVO_LAUNCHD': '1'}):
            r = update.apply(self.clone)
        self.assertTrue(r['ok'], r)
        self.assertFalse(r['restart'])                      # 開機自動啟動的看板會自己重啟
        self.assertFalse(update.check(self.clone, cache=False)['new'])
        with open(os.path.join(self.clone, 'f.txt')) as f:
            self.assertEqual(f.read(), '2')

    def test_without_launchd_says_restart_needed(self):
        self.push_new_commit()
        with mock.patch.dict(os.environ, {'JOBSALVO_LAUNCHD': ''}):
            r = update.apply(self.clone)
        self.assertTrue(r['restart'])
        self.assertIn('重新啟動看板', r['msg'])

    def test_other_branch_is_left_alone(self):
        git(self.clone, 'checkout', '-q', '-b', 'feature')
        r = update.apply(self.clone)
        self.assertFalse(r['ok'])
        self.assertIn('feature 分支', r['msg'])

    def test_dirty_tree_is_refused(self):
        self.push_new_commit()
        with open(os.path.join(self.clone, 'f.txt'), 'w') as f:
            f.write('local edit')
        r = update.apply(self.clone)
        self.assertFalse(r['ok'])
        self.assertIn('還沒提交的變更', r['msg'])

    def test_unreachable_origin_reports_unknown(self):
        git(self.clone, 'remote', 'set-url', 'origin', '../missing.git')
        c = update.check(self.clone, cache=False)
        self.assertIsNone(c['latest'])
        self.assertFalse(c['new'])


if __name__ == '__main__':
    unittest.main()
