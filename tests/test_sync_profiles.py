#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不靠卡片的平台履歷同步(sync_profiles):登記過的每一份都同步;不同平台同時跑、同一平台一次一份;
跟原稿不符的同一隻 agent 改一次,只剩平台有、原稿沒寫的交給他確認。"""
import contextlib, io, os, sys, threading, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
import sync_profiles as sp  # noqa: E402
import sync_report  # noqa: E402

REG = {'_說明': 'x', 'alpha': {'zh/a': {'read': 'https://alpha.test/r/1'}, 'zh/b': {'read': 'https://alpha.test/r/2'}},
       'beta': {'en/a': {'read': 'https://beta.test/r/1'}, 'en/c': {}}}


class Slots(unittest.TestCase):
    def test_every_registered_profile_with_a_page_is_a_slot(self):
        with mock.patch.object(sp.ps, 'registry', return_value=REG):
            self.assertEqual([s[:3] for s in sp.slots()], [('alpha', 'zh', 'a'), ('alpha', 'zh', 'b'), ('beta', 'en', 'a')])
            self.assertEqual([s[:3] for s in sp.slots('beta')], [('beta', 'en', 'a')])

    def test_different_pages_run_together_and_one_page_runs_one_at_a_time(self):
        registered = [('alpha', 'zh', 'a', {'read': 'https://alpha.test/r/1'}),
                      ('alpha', 'en', 'b', {'read': 'https://alpha.test/r/2'}),
                      ('beta', 'zh', 'c', {'read': 'https://beta.test/r/1'}),
                      ('beta', 'zh', 'd', {'read': 'https://beta.test/r/1'})]
        running, peak, same = [], [0], []
        lock = threading.Lock()

        def fake(plat, lang, variant, entry, board):
            with lock:
                same.append(any(page == entry['read'] for page in running))
                running.append(entry['read'])
                peak[0] = max(peak[0], len(running))
            time.sleep(0.05)
            with lock:
                running.remove(entry['read'])
            return True, []
        with mock.patch.object(sp, 'slots', return_value=registered), \
                mock.patch.object(sp, 'behind', return_value=True), \
                mock.patch.object(sp, 'sync_slot', side_effect=fake):
            results = sp.sync_all('/tmp/b.html')
        self.assertEqual(len(results), 4)
        self.assertEqual(peak[0], 3)                  # 三個不同頁面同時跑
        self.assertFalse(any(same))                   # 同一頁沒有兩份同時跑

    def test_all_sync_skips_profiles_that_are_already_current(self):
        with mock.patch.object(sp, 'slots', return_value=[('alpha', 'zh', 'a', {'read': 'https://alpha.test/r/1'})]), \
                mock.patch.object(sp, 'behind', return_value=False), \
                mock.patch.object(sp, 'sync_slot') as one:
            results = sp.sync_all('/tmp/b.html')
        self.assertEqual(results, {})
        one.assert_not_called()

    def test_real_site_verification_stops_only_later_profiles_on_the_failed_page(self):
        registered = [('alpha', 'zh', 'a', {'read': 'https://alpha.test/r/1'}),
                      ('alpha', 'zh', 'b', {'read': 'https://alpha.test/r/1'}),
                      ('beta', 'zh', 'c', {'read': 'https://beta.test/r/1'})]
        barrier = threading.Barrier(2)

        def fake(plat, lang, variant, entry, board, *, verify_only):
            self.assertTrue(verify_only)
            barrier.wait(timeout=5)
            return (False, ['比對錯誤']) if plat == 'alpha' else (True, [])

        with mock.patch.object(sp, 'slots', return_value=registered), \
                mock.patch.object(sp, 'sync_slot', side_effect=fake) as one:
            results = sp.sync_all('/tmp/b.html', verify_only=True)
        self.assertEqual(set(results), {('alpha', 'zh', 'a'), ('beta', 'zh', 'c')})
        self.assertFalse(results[('alpha', 'zh', 'a')][0])
        self.assertTrue(results[('beta', 'zh', 'c')][0])
        self.assertEqual(one.call_count, 2)

    def test_verify_only_prints_the_generated_report_path(self):
        output = io.StringIO()
        with mock.patch.object(sp, 'slots', return_value=[('alpha', 'zh', 'a', {'read': 'https://alpha.test/r/1'})]), \
                mock.patch.object(sp, 'sync_all', return_value={('alpha', 'zh', 'a'): (True, [])}), \
                mock.patch.object(sync_report, 'write_report', return_value='/tmp/sync-report.html') as report, \
                mock.patch.object(sys, 'argv', ['sync_profiles.py', '--verify-only', '--board', '/tmp/b.html']), \
                contextlib.redirect_stdout(output):
            self.assertEqual(sp.main(), 0)
        report.assert_called_once_with([])
        self.assertIn('驗收報告：/tmp/sync-report.html', output.getvalue())


class FailureOwner(unittest.TestCase):
    """同步沒過:他做得到的才列進要你處理;程式或 agent 自己的錯只當結果,這一份之後通過了就自己收掉(#446)。"""

    def test_only_what_he_can_fix_is_his(self):
        # 看問題的種類,不看字(#407 R2):同一句話換了種類,歸屬跟著變;沒有種類的,字寫得像登入也不算他的
        from gate_cells import Problem
        for problems, his in ((['要你登入/驗證：https://x/login'], False), ([Problem('同一句', 'login')], True),
                              ([Problem('同一句', 'setup')], True), ([Problem('同一句', 'damage')], True),
                              (['同一句'], False)):
            with self.subTest(problems=problems, kind=getattr(problems[0], 'kind', None)), \
                    mock.patch('agent_report.report') as said:
                sp._failure('p', 'zh', 'v', {'read': 'https://x/r'}, 'b.html', problems)
                self.assertEqual(said.call_args.kwargs['info'], not his)
                self.assertEqual(bool(said.call_args.kwargs['need']), his)

    def test_the_next_step_names_the_button_that_started_the_sync(self):
        from gate_cells import Problem
        for card_url, kind, step in (('https://x/job', 'login', '幫你填表'), ('', 'login', '全部同步'),
                                     ('https://x/job', 'setup', '幫你填表')):
            with self.subTest(card_url=card_url, kind=kind), mock.patch('agent_report.report') as said:
                sp._failure('p', 'zh', 'v', {'read': 'https://x/r'}, 'b.html', [Problem('同一句', kind)], card_url)
                need = said.call_args.kwargs['need']
                self.assertIn(step, need)
                self.assertEqual('登入' in need, kind == 'login')

    def test_a_passing_sync_closes_that_profiles_old_reports(self):
        from unittest import mock
        import sync_profiles as sp
        for passed in (False, True):
            with mock.patch.object(sp, '_sync_slot', return_value=(passed, [])), \
                    mock.patch('agent_report.resolve') as closed:
                sp.sync_slot('p', 'zh', 'v', {'read': 'https://x/r'}, 'b.html')
                self.assertEqual(closed.called, passed)
        only = closed.call_args.kwargs['only']
        self.assertTrue(only({'msg': 'p zh/v 平台履歷同步還有：\nagent 沒有交回頁面'}))
        self.assertFalse(only({'msg': 'p zh/other 平台履歷同步還有：\nagent 沒有交回頁面'}))


if __name__ == '__main__':
    unittest.main()

