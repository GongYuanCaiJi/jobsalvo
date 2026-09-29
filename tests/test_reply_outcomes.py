import os
import sys
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))

import agent_run as ar
import reply_run as rr


class EchoOutcomes(unittest.TestCase):
    def test_partial_findings_are_not_read_when_agent_fails(self):
        with tempfile.TemporaryDirectory(prefix='reply-outcome-') as d:
            board = os.path.join(d, 'board.html')
            out = os.path.join(d, 'replies.json')
            with open(out, 'w', encoding='utf-8') as f:
                json.dump({'checked': ['https://job.example/1'], 'findings': []}, f)
            statuses, reports, launched = [], [], {}
            fb, jobs = {}, {'https://job.example/1': {'id': 'https://job.example/1'}}
            chrome = SimpleNamespace(ensure=lambda *_: (True, ''), close_if_idle=lambda *_: None)

            def dispatch(_prompt, _log, _repo, **kwargs):
                launched['yes'] = True
                launched['kwargs'] = kwargs
                with open(out, 'w', encoding='utf-8') as f:
                    json.dump({'checked': ['https://job.example/1'], 'findings': [],
                               'job_ids': [], 'inaccessible': []}, f)
                return ar.AgentResult('failed', 9, 123)

            with patch.object(rr, 'SP', d), \
                 patch.object(rr, 'load', return_value=(jobs, fb)), \
                 patch.object(rr, 'waiting', return_value=[next(iter(jobs))]), \
                 patch.object(rr, 'prompt_for', return_value='prompt'), \
                 patch.object(rr.jobrun, 'write', side_effect=lambda _path, data: statuses.append(data)), \
                 patch.object(rr.agent_report, 'report', side_effect=lambda *args, **kwargs: reports.append(args)), \
                 patch.object(rr.bd, 'set_fb') as set_fb, \
                 patch.object(rr.ar, 'run', side_effect=dispatch), \
                 patch.dict(sys.modules, {'agent_chrome': chrome}), \
                 patch.object(sys, 'argv', ['reply_run.py', '--board', board]):
                result = rr.main()

            self.assertEqual(result, 1)
            self.assertTrue(launched.get('yes'))
            self.assertTrue(launched['kwargs']['browser_required'])
            self.assertEqual(launched['kwargs']['board'], board)
            set_fb.assert_not_called()
            self.assertEqual(statuses[-1]['phase'], 'failed')
            self.assertEqual(statuses[-1]['done'], 0)
            self.assertTrue(any('結束碼 9' in args[1] for args in reports))


if __name__ == '__main__':
    unittest.main()
