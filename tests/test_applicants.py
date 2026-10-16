# -*- coding: utf-8 -*-
"""每輪找缺後自動更新應徵人數:有人數的重抓、變了記下來;沒查過的查一次;頁面沒寫的不再重查;抓不到的下輪再試。"""
import os, sys, tempfile, unittest
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import applicants  # noqa: E402
import page_fetch  # noqa: E402

KNOWN, NEW, SILENT, CHECKED, BLOCKED, CLOSED = (
    'https://www.104.com.tw/job/KNOWN', 'https://tw.linkedin.com/jobs/view/1', 'https://jobs.lever.co/acme/1',
    'https://jobs.lever.co/acme/2', 'https://tw.linkedin.com/jobs/view/2', 'https://jobs.lever.co/acme/3')


class Update(unittest.TestCase):
    def run_once(self, path, now):
        asked, routes = [], []

        def fake_many(urls, fetch_page=None):
            asked.extend(urls)
            routes.append(fetch_page)
            return [page_fetch.PageResult(u, 'unknown') if u == BLOCKED else
                    page_fetch.PageResult(u, 'closed') if u == CLOSED else
                    page_fetch.PageResult(u, 'ok', text='x', applicants=now.get(u, '')) for u in urls]
        with patch.object(page_fetch, 'fetch_many', side_effect=fake_many):
            applicants.main(['--board', path])
        return asked, routes, {j['id']: j for j in _env.read_board(path)['data']['jobs']}

    def test_refreshes_known_checks_new_once_and_logs_changes(self):
        path = _env.make_board(os.path.join(tempfile.mkdtemp(), 'board.html'), jobs=[
            {'id': KNOWN, 'applicants': '兩週內 0~5 人', 'applicants_at': '2026-10-01'},
            {'id': NEW}, {'id': SILENT}, {'id': CHECKED, 'applicants_at': '2026-10-01'}, {'id': BLOCKED},
            {'id': CLOSED}])
        asked, routes, jobs = self.run_once(path, {KNOWN: '兩週內 11~30 人', NEW: '45 人'})
        self.assertEqual(sorted(asked), sorted([KNOWN, NEW, SILENT, BLOCKED, CLOSED]), '查過、頁面沒寫人數的不再重查')
        self.assertEqual(routes, [page_fetch.fetch_light], '不退到瀏覽器')
        self.assertEqual(jobs[KNOWN]['applicants_log'], [['2026-10-01', '兩週內 0~5 人'],
                                                         [jobs[KNOWN]['applicants_at'], '兩週內 11~30 人']])
        self.assertEqual(jobs[NEW]['applicants'], '45 人')
        self.assertNotIn('applicants', jobs[SILENT])
        self.assertTrue(jobs[SILENT]['applicants_at'], '頁面沒寫人數:記下查過了')
        self.assertNotIn('applicants_at', jobs[BLOCKED], '抓不到不算查過,下輪再試')
        self.assertTrue(jobs[CLOSED]['applicants_at'], '下架的頁也記下查過,不每輪重抓')

        asked, _, jobs = self.run_once(path, {KNOWN: '兩週內 11~30 人', NEW: '45 人'})
        self.assertEqual(sorted(asked), sorted([KNOWN, NEW, BLOCKED]))
        self.assertEqual(len(jobs[KNOWN]['applicants_log']), 2, '數字沒變不多記')


if __name__ == '__main__':
    unittest.main()
