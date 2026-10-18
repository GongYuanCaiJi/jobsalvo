"""平台履歷檔案與同步基線：網頁 R0/R1/R2 測試見 test_sync_paragraph_ids。"""
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402
import fake_door  # noqa: E402
import profile_sync as ps  # noqa: E402
import sync_profiles as sp  # noqa: E402


class FileDoor(fake_door.FakeDoor):
    def __init__(self, content):
        super().__init__(agent_id='fake')
        self.content = content

    def fetch_file(self, url, dest):
        self._can('fetch_file', url)
        with open(dest, 'wb') as out:
            out.write(self.content)
        return dest


class FileProfileSync(unittest.TestCase):
    def setUp(self):
        _env.use_home(self, resume={'base': 'resume', 'langs': ['zh'], 'resumes': [
            {'id': 'one', 'name': '通用版', 'files': {'zh': 'resume/one.md'}, 'enabled': True}],
            'attachments': []})
        self.enterContext(mock.patch.object(ps, 'REG', os.path.join(self.home, 'profiles.json')))
        self.url = 'https://example.test/resume/one.pdf'
        ps.remember('example.test', 'zh', 'one', self.url)
        self.source = os.path.join(self.home, 'resume', 'exact.pdf')
        with open(self.source, 'wb') as out:
            out.write(b'new exact bytes')
        self.board = _env.make_board(os.path.join(self.tmp, 'board.html'))

    def run_sync(self, before, after, *, saved=True, verify_only=False):
        door = FileDoor(before)
        calls = []

        def agent(_prompt, log, _home, _board, **kwargs):
            calls.append('agent')
            door.content = after
            with open(log, 'w', encoding='utf-8') as out:
                out.write(json.dumps({'type': 'thread.started', 'thread_id': 'same-agent'}) + '\n')
            with open(kwargs['output_last_message'], 'w', encoding='utf-8') as out:
                json.dump({'saved': saved, 'reason': 'agent 自述'}, out)
            return SimpleNamespace(ok=True, message=lambda: '')

        with fake_door.installed(door), mock.patch.object(sp.apply_run, '_run_agent', side_effect=agent):
            result = sp.sync_slot('example.test', 'zh', 'one', ps.where('example.test', 'zh', 'one'),
                                  self.board, source_file=self.source, verify_only=verify_only)
        return result, door, calls

    def test_exact_download_after_replacement_advances_baseline(self):
        result, door, calls = self.run_sync(b'old bytes', b'new exact bytes')
        self.assertEqual(result, (True, []))
        self.assertEqual(calls, ['agent'])
        self.assertEqual([call[0] for call in door.calls].count('fetch_file'), 2)
        self.assertEqual(ps.where('example.test', 'zh', 'one')['sync_baseline']['source'],
                         'sha256:' + ps._sha_file(self.source))
        change, = ps.where('example.test', 'zh', 'one')['sync_visuals']
        self.assertEqual((change['item'], change['platform_result']), ('履歷檔', '已存上平台'))

    def test_downloaded_bytes_overrule_agents_saved_claim(self):
        result, _, _ = self.run_sync(b'old bytes', b'new exact bytes', saved=False)
        self.assertEqual(result, (True, []))

    def test_wrong_download_keeps_old_baseline(self):
        ps.remember_sync('example.test', 'zh', 'one', 'old baseline', [], 'old-fingerprint')
        result, _, _ = self.run_sync(b'old bytes', b'wrong bytes')
        self.assertFalse(result[0])
        self.assertEqual(ps.where('example.test', 'zh', 'one')['sync_baseline']['source'], 'old baseline')

    def test_verify_only_downloads_even_when_baseline_is_current(self):
        ps.remember_sync('example.test', 'zh', 'one',
                         'sha256:' + ps._sha_file(self.source), [], ps._master_fp('zh', 'one'))
        result, door, calls = self.run_sync(b'changed remotely', b'new exact bytes', verify_only=True)
        self.assertFalse(result[0])
        self.assertEqual(calls, [])
        self.assertEqual([call[0] for call in door.calls].count('fetch_file'), 1)
        change, = ps.where('example.test', 'zh', 'one')['sync_visuals']
        self.assertEqual((change['platform_result'], change['step']), ('沒做成', '驗收檔案 bytes'))

    def test_unavailable_file_agent_hands_the_same_workspace_to_the_next_agent(self):
        door = FileDoor(b'old bytes')
        agents = [{'id': 'fake', 'runtime': 'codex', 'browser': True},
                  {'id': 'backup', 'runtime': 'codex', 'browser': True}]
        calls = []

        def agent(_prompt, log, _home, _board, **kwargs):
            # agent_run 照設定順序挑:第一隻不能用,接手的是 backup
            calls.append(kwargs['agent_id'])
            door.content = b'new exact bytes'
            with open(log, 'w', encoding='utf-8') as out:
                out.write(json.dumps({'type': 'thread.started', 'thread_id': 'backup-session'}) + '\n')
            with open(kwargs['output_last_message'], 'w', encoding='utf-8') as out:
                json.dump({'saved': True}, out)
            return SimpleNamespace(ok=True, status='completed', agent_id='backup', message=lambda: '')

        with fake_door.installed(door, agents=agents), \
                mock.patch.object(sp.apply_run, '_run_agent', side_effect=agent):
            result = sp.sync_slot('example.test', 'zh', 'one', ps.where('example.test', 'zh', 'one'),
                                  self.board, source_file=self.source)
        self.assertEqual(result, (True, []))
        self.assertEqual(calls, [None])                                    # 挑哪一個、不能用換下一個由 agent_run 決定
        self.assertEqual(door.agent_id, 'backup')


if __name__ == '__main__':
    unittest.main()
