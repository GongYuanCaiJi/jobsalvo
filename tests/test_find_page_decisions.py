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

    def browser_read(self, argv, later_ms, filler=0, nav=0):
        """換掉跑 ego 腳本那一支(瀏覽器邊界):頁一載完只有骨架,later_ms 毫秒後清單才畫出來;腳本叫它等多久就給那時的頁。"""
        import contextlib
        import io
        import re
        import chrome_door
        import page_fetch
        import web.fetch
        url = 'https://example.invalid/list'
        job = 'https://example.invalid/job/42'

        def ego(script, timeout=None):
            waited = sum(int(ms) for ms in re.findall(r'waitForTimeout\((\d+)\)', script))
            drawn = waited >= later_ms
            page = {'url': url, 'title': '職缺清單',
                    'text': '職缺清單\n' + ('Platform Engineer' if drawn else '載入中') + '\n更多職缺' * filler,
                    'anchors': [{'href': f'https://example.invalid/nav/{n}?{"x" * 80}', 'text': '導覽'} for n in range(nav)]
                               + ([{'href': job, 'text': 'Platform Engineer'}] if drawn else [])}
            return {'pages': {url: page}, 'reader': 'p1'}
        out = io.StringIO()
        with patch.object(web.fetch, '_assert_public_url', return_value='203.0.113.1'), \
                patch.object(web.fetch, '_direct', side_effect=AssertionError('agent 指定用瀏覽器讀')), \
                patch.object(chrome_door, '_ego', side_effect=ego), contextlib.redirect_stdout(out):
            code = page_fetch.main([*argv, url])
        return code, out.getvalue(), job

    def test_the_agent_can_ask_the_browser_to_wait_before_reading(self):
        # #4:要跑 JS 才畫出清單的頁,瀏覽器一載完就讀只有骨架;agent 讀了覺得沒載完,可以叫它多等幾秒再讀
        code, early, job = self.browser_read(['--browser'], 3000)
        self.assertEqual((code, 'Platform Engineer' in early, job in early), (0, False, False))
        code, late, job = self.browser_read(['--wait', '3'], 3000)
        self.assertEqual((code, 'Platform Engineer' in late), (0, True))
        self.assertIn('--wait 秒數', early)

    def test_a_browser_read_hands_the_agent_every_link_on_the_page(self):
        # #4:瀏覽器讀到的字不含連結網址,清單頁上的職缺網址要照樣交給 agent
        _code, out, job = self.browser_read(['--wait', '3'], 0)
        self.assertIn(f'Platform Engineer ⟦網址 {job}⟧', out)
        _code, long_page, job = self.browser_read(['--wait', '3'], 0, filler=30000)   # 超過頁面材料上限被截
        self.assertIn('超過', long_page)
        self.assertIn(f'⟦網址 {job}⟧', long_page)
        _code, many_links, _job = self.browser_read(['--wait', '3'], 0, nav=2000)   # 連結多到超過上限:頁上的字照樣留著
        self.assertIn('個連結太多沒列', many_links)
        self.assertRegex(many_links, r'\d+\. Platform Engineer')




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
