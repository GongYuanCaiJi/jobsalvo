# -*- coding: utf-8 -*-
"""找缺條件:設定壞值當沒填、存之前擋掉看板表達不了的值;程式只拿核對過的值比使用者的條件,未知欄位不硬排除。"""
import datetime, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import find_terms as ft  # noqa: E402

TODAY = datetime.date(2026, 10, 16)


class Terms(unittest.TestCase):
    def test_blank_or_bad_values_mean_no_limit(self):
        self.assertEqual(ft.normalize({}), {})
        self.assertEqual(ft.normalize({'posted_days': '', 'salary_min': 'abc', 'exp_max': None, 'location': '  '}),
                         {})
        self.assertEqual(ft.normalize({'exp_max': 0, 'applicants_max': 0})['exp_max'], 0, '經驗 0 = 只要經歷不拘')
        self.assertEqual(ft.normalize({'posted_days': 7, 'strict': ['posted_days']}), {'posted_days': 7})

    def test_problem_rejects_values_the_board_cannot_mean(self):
        self.assertEqual(ft.problem({'posted_days': 7, 'salary_min': 50000, 'exp_max': 0, 'location': '台北'}), '')
        self.assertIn('上架天數', ft.problem({'posted_days': 0}))
        self.assertIn('薪資', ft.problem({'salary_min': '5'}))
        self.assertEqual(ft.problem({'strict': ['nope']}), '')

    def test_cited_page_fields_can_filter_and_missing_fields_stay_unknown(self):
        terms = ft.normalize({'posted_days': 7, 'applicants_max': 10})
        self.assertEqual(ft.check_judged({'posted_at': '2026-10-12', 'applicants': '10'}, terms, TODAY), ([], []))
        self.assertEqual(ft.check_judged({'posted_at': '2026-09-01', 'applicants': ft.NO_CAP}, terms, TODAY),
                         (['上架天數', '應徵人數'], []))
        self.assertEqual(ft.check_judged({'posted_at': '2026-10-12', 'applicants': '6~10'}, terms, TODAY),
                         ([], ['應徵人數']), 'agent 沒把範圍寫成上限的整數:程式不從字裡挑數字')
        self.assertEqual(ft.check_judged({}, terms, TODAY), ([], ['上架天數', '應徵人數']))

    def test_judged_terms_are_compared_by_the_program(self):
        terms = ft.normalize({'salary_min': 50000, 'exp_max': 3, 'location': '台北 遠端'})
        self.assertEqual(ft.check_judged({'salary_month': 60000, 'exp_years': 2, 'location': '符合'}, terms), ([], []))
        self.assertEqual(ft.check_judged({'salary_month': 40000, 'exp_years': 5, 'location': '不符'}, terms),
                         (['薪資', '經驗', '地點'], []))
        self.assertEqual(ft.check_judged({}, terms), ([], ['薪資', '經驗', '地點']))


if __name__ == '__main__':
    unittest.main()
