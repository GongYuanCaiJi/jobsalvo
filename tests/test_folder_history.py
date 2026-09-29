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

    def test_existing_repository_at_the_data_folder_is_never_committed(self):
        with _without_git_environment():
            git = folder_history._git()
            for has_remote in (False, True):
                with self.subTest(has_remote=has_remote), tempfile.TemporaryDirectory(
                        prefix='foreign-history-') as home:
                    subprocess.run([git, 'init', '--initial-branch=main'], cwd=home,
                                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    if has_remote:
                        subprocess.run([git, 'remote', 'add', 'origin',
                                        'https://example.invalid/project.git'], cwd=home, check=True)
                    with open(os.path.join(home, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
                        f.write('{"resume":{}}')

                    self.assertFalse(folder_history.flush_now(home))
                    status = folder_history.status(home)

                    self.assertIn('不是 jobsalvo 建立', status['message'])
                    self.assertNotEqual(subprocess.run(
                        [git, 'rev-parse', '--verify', 'HEAD'], cwd=home,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode, 0)
                    self.assertFalse(os.path.exists(os.path.join(home, '.git',
                                                                   'jobsalvo-data-repository')))

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
