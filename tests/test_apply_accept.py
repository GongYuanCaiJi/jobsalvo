import os
import tempfile
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import _env  # noqa: F401
import apply_accept as acceptance


class FakeApplicationPreparation(unittest.TestCase):
    def test_setup_failure_still_reports_cleanup_and_a_failed_acceptance(self):
        import chrome_door
        _env.use_home(self)
        empty = self.enterContext(tempfile.TemporaryDirectory())
        out = self.enterContext(tempfile.TemporaryDirectory())
        with patch.object(acceptance.cf, 'HOME', empty), \
             patch.object(chrome_door.EgoDoor, 'workspaces', return_value=[]), \
             patch.object(sys, 'argv', ['apply_accept.py', '--out', out]):
            with self.assertRaises(SystemExit) as stopped:
                acceptance.main()
        self.assertEqual(stopped.exception.code, 1)
        report = json.loads(Path(out, 'report.json').read_text())
        self.assertTrue(report['workspace_counts']['returned_to_baseline'])
        self.assertTrue(any(not check['ok'] for check in report['checks']))

    def test_builds_a_real_verified_package_inside_a_disposable_home(self):
        _env.use_home(self)
        original_home = acceptance.cf.HOME
        original_board = acceptance.bd.LIVE
        _env.make_board(original_board, {})
        original_bytes = Path(original_board).read_bytes()
        old_home_env = os.environ.get('JOBSALVO_HOME')
        out = self.enterContext(tempfile.TemporaryDirectory(prefix='apply-accept-test-'))
        run = acceptance.Accept(out)

        def render(_source, destination, *args, **kwargs):
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(acceptance.make_pdf('prepared resume fixture'))

        try:
            with patch.object(acceptance.ship.markdown_pdf, 'render', side_effect=render), \
                    patch.object(acceptance.ship, '_record_package_previews'):
                run.setup()
            self.assertNotEqual(acceptance.cf.HOME, original_home)
            self.assertTrue(Path(acceptance.cf.HOME).is_relative_to(out))
            parsed = acceptance.bd.load(run.board)
            job = next(j for j in parsed['data']['jobs'] if j['id'] == run.url)
            self.assertEqual(acceptance.ship.check(job, run.fb()), [])
            folder = acceptance.ship.folder(run.url, root=run.ship_root)
            self.assertTrue(Path(folder, acceptance.MERGED).is_file())
            self.assertEqual(len(acceptance.ship.read_info(folder)['files']), 2)
            self.assertEqual(Path(original_board).read_bytes(), original_bytes)
        finally:
            if hasattr(run, 'srv'):
                run.srv.stop()
            if hasattr(run, 'screen'):
                run.screen.stop()
            acceptance.cf.reload(original_home)
            acceptance.bd.LIVE = original_board
            if old_home_env is None:
                os.environ.pop('JOBSALVO_HOME', None)
            else:
                os.environ['JOBSALVO_HOME'] = old_home_env


if __name__ == '__main__':
    unittest.main()
