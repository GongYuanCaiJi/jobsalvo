import os
import json
import subprocess
import tempfile
import time
import unittest
from contextlib import contextmanager
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import folder_history


@contextmanager
def _without_git_environment():
    inherited = {key: value for key, value in os.environ.items() if key.startswith('GIT_')}
    for key in inherited:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        os.environ.update(inherited)


class FolderHistory(unittest.TestCase):
    def test_git_commands_ignore_the_callers_repository_environment(self):
        result = subprocess.CompletedProcess(['git'], 0, stdout='', stderr='')
        with mock.patch.dict(os.environ, {'GIT_DIR': '/outer/.git',
                                          'GIT_WORK_TREE': '/outer/worktree'}):
            with mock.patch.object(folder_history.subprocess, 'run', return_value=result) as run:
                folder_history._run('git', '/data/home', 'status')
        child_env = run.call_args.kwargs['env']
        self.assertNotIn('GIT_DIR', child_env)
        self.assertNotIn('GIT_WORK_TREE', child_env)

    def test_saves_coalesce_into_local_commits_and_ignore_generated_outputs(self):
        class ManualTimer:
            def __init__(self, _delay, callback, args=(), kwargs=None):
                self.callback = callback
                self.args = args
                self.kwargs = kwargs or {}
                self.cancelled = False

            def start(self):
                pass

            def cancel(self):
                self.cancelled = True

            def fire(self):
                if not self.cancelled:
                    self.callback(*self.args, **self.kwargs)

        with tempfile.TemporaryDirectory(prefix='folder-history-') as home:
            env = {'GIT_AUTHOR_NAME': 'Test User', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
                   'GIT_COMMITTER_NAME': 'Test User', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
            with _without_git_environment(), mock.patch.dict(os.environ, env):
                timers = []

                def create_timer(delay, callback, args=(), kwargs=None):
                    timer = ManualTimer(delay, callback, args, kwargs)
                    timers.append(timer)
                    return timer

                with mock.patch.object(folder_history.threading, 'Timer', side_effect=create_timer):
                    with open(os.path.join(home, 'first.md'), 'w', encoding='utf-8') as f:
                        f.write('first')
                    with open(os.path.join(home, 'interview-bank.md'), 'w', encoding='utf-8') as f:
                        f.write('# Interview bank\n')
                    folder_history.note_saved(home, delay=0.2)
                    with open(os.path.join(home, 'second.md'), 'w', encoding='utf-8') as f:
                        f.write('second')
                    os.makedirs(os.path.join(home, 'ship'), exist_ok=True)
                    with open(os.path.join(home, 'ship', 'delivery.pdf'), 'wb') as f:
                        f.write(b'generated')
                    for directory, filename in (('company-cache', 'company.json'),
                                                 ('card-summaries', 'summary.json'),
                                                 ('.research', 'round.json')):
                        os.makedirs(os.path.join(home, directory), exist_ok=True)
                        with open(os.path.join(home, directory, filename), 'w', encoding='utf-8') as f:
                            f.write('generated')
                    with open(os.path.join(home, 'posted-cache.json'), 'w', encoding='utf-8') as f:
                        f.write('{}')
                    folder_history.note_saved(home, delay=0.2)
                    self.assertEqual(len(timers), 2)
                    self.assertTrue(timers[0].cancelled)
                    self.assertFalse(timers[1].cancelled)
                    self.assertFalse(os.path.exists(os.path.join(home, '.git')))
                    timers[1].fire()

                self.assertTrue(os.path.exists(os.path.join(home, '.git')))
                self.assertRegex(folder_history.status(home)['message'], r'最近一次 \d{4}-\d{2}-\d{2} \d{2}:\d{2}$')

            with _without_git_environment():
                git = folder_history._git()
                commits = subprocess.check_output(
                    [git, 'rev-list', '--count', 'HEAD'], cwd=home, text=True).strip()
                self.assertEqual(commits, '1')
                tracked = subprocess.check_output([git, 'ls-files'], cwd=home, text=True).splitlines()
                self.assertEqual(tracked, ['first.md', 'interview-bank.md', 'second.md'])
                self.assertEqual(subprocess.check_output([git, 'remote', '-v'], cwd=home, text=True), '')
                self.assertTrue(folder_history.status(home)['message'])
                marker = os.path.join(home, '.git', 'jobsalvo-data-repository')
                self.assertTrue(os.path.isfile(marker))

    def _git_in(self, cwd, *args):
        return subprocess.run([folder_history._git(), *args], cwd=cwd, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _old_system_repository(self, home, remote=False):
        """從舊系統搬來的資料夾:本來就是 git repo、有 jobsalvo.json、有舊的 commit,沒有 jobsalvo 標記。"""
        env = {'GIT_AUTHOR_NAME': 'Old', 'GIT_AUTHOR_EMAIL': 'old@example.invalid',
               'GIT_COMMITTER_NAME': 'Old', 'GIT_COMMITTER_EMAIL': 'old@example.invalid'}
        self._git_in(home, 'init', '--initial-branch=main')
        with open(os.path.join(home, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
            f.write('{"resume":{}}')
        self._git_in(home, 'add', '-A')
        subprocess.run([folder_history._git(), 'commit', '-m', 'old system'], cwd=home, check=True,
                       env={**os.environ, **env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if remote:
            self._git_in(home, 'remote', 'add', 'origin', 'https://example.invalid/project.git')
        with open(os.path.join(home, 'board.html'), 'w', encoding='utf-8') as f:
            f.write('changed after the move')

    def _commits(self, home):
        return int(self._git_in(home, 'rev-list', '--count', 'HEAD').stdout.strip() or 0)

    def test_moved_folder_repository_is_adopted_and_keeps_saving(self):
        with _without_git_environment(), tempfile.TemporaryDirectory(prefix='moved-history-') as home:
            self._old_system_repository(home)

            self.assertTrue(folder_history.flush_now(home))

            self.assertEqual(self._commits(home), 2)
            self.assertTrue(os.path.isfile(os.path.join(home, '.git', 'jobsalvo-data-repository')))
            status = folder_history.status(home)
            self.assertRegex(status['message'], r'最近一次 \d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
            self.assertFalse(status.get('fix'))

    def test_repository_with_a_remote_is_never_committed(self):
        with _without_git_environment(), tempfile.TemporaryDirectory(prefix='remote-history-') as home:
            self._old_system_repository(home, remote=True)

            self.assertFalse(folder_history.flush_now(home))

            self.assertEqual(self._commits(home), 1)
            self.assertFalse(os.path.exists(os.path.join(home, '.git', 'jobsalvo-data-repository')))
            status = folder_history.status(home)
            self.assertIn('remote', status['message'])
            self.assertTrue(status['fix'])

    def test_folder_inside_another_repository_is_never_committed(self):
        with _without_git_environment(), tempfile.TemporaryDirectory(prefix='outer-repo-') as outer:
            self._git_in(outer, 'init', '--initial-branch=main')
            home = os.path.join(outer, 'jobsearch')
            os.makedirs(home)
            with open(os.path.join(home, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
                f.write('{"resume":{}}')

            self.assertFalse(folder_history.flush_now(home))

            self.assertFalse(os.path.exists(os.path.join(home, '.git')))
            self.assertNotEqual(self._git_in(outer, 'rev-parse', '--verify', 'HEAD').returncode, 0)
            status = folder_history.status(home)
            self.assertIn('子資料夾', status['message'])
            self.assertTrue(status['fix'])

    def _board(self, home, text='old format'):
        path = os.path.join(home, 'board.html')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        with open(os.path.join(home, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
            f.write('{"resume":{}}')
        return path

    @staticmethod
    def _rewrite(path):
        def do():
            with open(path, 'w', encoding='utf-8') as f:
                f.write('new format')
        return do

    def test_conversion_saves_a_version_of_the_old_format_first(self):
        with _without_git_environment(), tempfile.TemporaryDirectory(prefix='convert-') as home:
            board = self._board(home)

            result = folder_history.convert(home, [board], '投遞狀態', self._rewrite(board))

            self.assertTrue(result['done'])
            self.assertEqual(self._git_in(home, 'show', 'HEAD:board.html').stdout, 'old format')
            self.assertIn('版本', result['restore'])
            with open(board, encoding='utf-8') as f:
                self.assertEqual(f.read(), 'new format')

    def test_conversion_without_version_history_backs_the_board_up_inside_the_folder(self):
        for name in ('沒有 git', '看板檔不在資料夾裡(版本紀錄收不到它)'):
            with self.subTest(name), _without_git_environment(), \
                    tempfile.TemporaryDirectory(prefix='convert-') as home, \
                    tempfile.TemporaryDirectory(prefix='elsewhere-') as elsewhere:
                board = self._board(home if name == '沒有 git' else elsewhere)
                self._board(home)
                no_git = mock.patch.object(folder_history, '_git', return_value=None)
                with no_git if name == '沒有 git' else mock.patch.dict(os.environ):
                    result = folder_history.convert(home, [board], '投遞狀態', self._rewrite(board))

                self.assertTrue(result['done'])
                folder = os.path.join(home, folder_history.BACKUP_DIR)
                backups = [os.path.join(folder, n) for n in os.listdir(folder)]
                self.assertEqual(len(backups), 1)
                with open(backups[0], encoding='utf-8') as f:
                    self.assertEqual(f.read(), 'old format')
                self.assertIn(backups[0], result['restore'])

    def test_conversion_without_any_restore_point_does_not_convert(self):
        with tempfile.TemporaryDirectory(prefix='convert-') as home:
            board = self._board(home)
            with open(os.path.join(home, folder_history.BACKUP_DIR), 'w', encoding='utf-8') as f:
                f.write('a file where the backup folder should go')
            with mock.patch.object(folder_history, '_git', return_value=None):
                result = folder_history.convert(home, [board], '投遞狀態', self._rewrite(board))

                self.assertFalse(result['done'])
                self.assertTrue(result['reason'])
                with open(board, encoding='utf-8') as f:
                    self.assertEqual(f.read(), 'old format')
                status = folder_history.status(home)
                self.assertIn('投遞狀態', status['conversion'])

    def test_one_conversion_succeeding_does_not_hide_another_that_was_skipped(self):
        with tempfile.TemporaryDirectory(prefix='convert-') as home:
            board = self._board(home)
            settings = os.path.join(home, 'jobsalvo.json')
            with mock.patch.object(folder_history, '_git', return_value=None):
                with mock.patch.object(folder_history, '_back_up', side_effect=OSError('disk full')):
                    folder_history.convert(home, [settings], '舊設定格式轉換', lambda: None)
                folder_history.convert(home, [board], '投遞狀態轉換', self._rewrite(board))
                conversion = folder_history.status(home)['conversion']
            self.assertIn('舊設定格式轉換', conversion)
            self.assertNotIn('投遞狀態轉換', conversion)

    def test_conversion_with_nothing_to_keep_is_not_a_restore_point(self):
        with tempfile.TemporaryDirectory(prefix='convert-') as home:
            called = []
            with mock.patch.object(folder_history, '_git', return_value=None):
                result = folder_history.convert(home, [os.path.join(home, 'missing.html')], '投遞狀態',
                                                lambda: called.append(1))
            self.assertFalse(result['done'])
            self.assertEqual(called, [])

    def test_configured_generated_paths_are_ignored_when_they_stay_inside_home(self):
        with tempfile.TemporaryDirectory(prefix='folder-history-') as home:
            settings = {
                'paths': {'summaries': 'agent output/summaries', 'posted_cache': 'cache/posted.json',
                          'company_cache': '../outside-cache'},
            }
            with open(os.path.join(home, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
                json.dump(settings, f)
            patterns = folder_history._generated_patterns(home)
            self.assertIn(r'agent\ output/summaries/', patterns)
            self.assertIn('cache/posted.json', patterns)
            self.assertNotIn('../outside-cache/', patterns)

    def test_missing_git_is_reported_without_blocking_the_save(self):
        with tempfile.TemporaryDirectory(prefix='folder-history-') as home:
            with mock.patch.object(folder_history, '_git', return_value=None):
                self.assertFalse(folder_history.flush_now(home))
                self.assertEqual(folder_history.status(home)['message'],
                                 '沒有版本紀錄，因為找不到 git')
                with open(os.path.join(home, 'saved.md'), 'w', encoding='utf-8') as f:
                    f.write('still saved')
                with open(os.path.join(home, 'saved.md'), encoding='utf-8') as source:
                    self.assertEqual(source.read(), 'still saved')
                folder_history.note_saved(home, delay=0.01)
                time.sleep(0.05)
                self.assertFalse(folder_history.status(home)['pending'])

    def test_missing_git_identity_uses_a_local_history_identity(self):
        with tempfile.TemporaryDirectory(prefix='folder-history-') as home, \
                tempfile.NamedTemporaryFile(prefix='empty-git-config-') as global_config:
            env = {'GIT_CONFIG_GLOBAL': global_config.name, 'GIT_CONFIG_NOSYSTEM': '1'}
            with _without_git_environment(), mock.patch.dict(os.environ, env):
                with open(os.path.join(home, 'saved.md'), 'w', encoding='utf-8') as f:
                    f.write('saved')
                self.assertTrue(folder_history.flush_now(home))
                git = folder_history._git()
                identity = subprocess.check_output(
                    [git, 'log', '-1', '--format=%an <%ae>'], cwd=home, text=True).strip()
                self.assertEqual(identity, 'jobsalvo local history <jobsalvo@localhost>')


if __name__ == '__main__':
    unittest.main()
