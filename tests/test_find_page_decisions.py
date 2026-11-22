"""保留 agent 讀網址指令與看板風險天數設定的回歸測試。"""
import json
import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

import _env  # noqa: F401  # adds tools/ to the import path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class PageFetchToolForTheAgent(unittest.TestCase):
    def test_the_agent_can_ask_for_the_page_through_the_browser(self):
        import io
        import contextlib
        import page_fetch
        import web.fetch
        url = 'https://example.invalid/spa'
        out = io.StringIO()
        with patch.object(web.fetch, '_assert_public_url', return_value='203.0.113.1'), \
                patch.object(web.fetch, '_ego', return_value=web.fetch.PageResult(
                    url, 'ok', text='Platform Engineer\nRendered by JavaScript', via='ego')), \
                patch.object(web.fetch, '_direct', side_effect=AssertionError('agent 指定用瀏覽器讀')), \
                contextlib.redirect_stdout(out):
            code = page_fetch.main(['--browser', url])
        self.assertEqual(code, 0)
        self.assertIn('Rendered by JavaScript', out.getvalue())




@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class RiskDaysAreASetting(unittest.TestCase):
    """看板「可能已關」:刊登超過幾天算是使用者的設定(預設照舊 180 天)。"""

    def risky(self, posted_days_ago, saved=None):
        with open(os.path.join(ROOT, 'board', 'board.js'), encoding='utf-8') as source_file:
            source = source_file.read()
        start = source.index('  function isRisky(')
        end = source.index('  function rvBarHTML(', start)
        program = (
            'var CFG={risk_days:' + json.dumps(saved) + ',risk_days_configured:true};'
            'function daysSince(){return ' + str(posted_days_ago) + ';}function isDead(){return false;}'
            'function deadlinePassed(){return false;}var justMarked={},FB={};'
            'function _coOrder(){return {cos:[],byCo:{}};}function sortJobs(x){return x;}\n'
            + source[start:end] + '\nconsole.log(JSON.stringify([isRisky({}),riskWhy({})]));')
        done = subprocess.run(['node', '-e', program], text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_default_keeps_the_old_half_year(self):
        self.assertEqual(self.risky(181)[0], True)
        self.assertEqual(self.risky(180)[0], False)

    def test_the_users_number_of_days_decides(self):
        self.assertEqual(self.risky(200, saved=365), [False, ''])
        risky, why = self.risky(40, saved=30)
        self.assertTrue(risky)
        self.assertIn('30 天', why)


if __name__ == '__main__':
    unittest.main()
