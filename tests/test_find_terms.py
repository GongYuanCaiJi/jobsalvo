# -*- coding: utf-8 -*-
"""找缺條件:空著是不限;程式讀得到的(上架天數、應徵人數)程式判;agent 讀出來的(薪資、經驗、地點)程式比;
查不到的預設留著、勾了「查不到就排除」才丟。"""
import datetime, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import find_terms as ft  # noqa: E402

TODAY = datetime.date(2026, 10, 16)


class Terms(unittest.TestCase):
    def test_blank_or_bad_values_mean_no_limit(self):
        self.assertEqual(ft.normalize({}), {'strict': []})
        self.assertEqual(ft.normalize({'posted_days': '', 'salary_min': 'abc', 'exp_max': None, 'location': '  '}),
                         {'strict': []})
        self.assertEqual(ft.normalize({'exp_max': 0, 'applicants_max': 0})['exp_max'], 0, '經驗 0 = 只要經歷不拘')
        self.assertEqual(ft.normalize({'posted_days': 7, 'strict': ['posted_days', 'salary_min']})['strict'],
                         ['posted_days'], '沒填的條件不會變成查不到就排除')

    def test_problem_rejects_values_the_board_cannot_mean(self):
        self.assertEqual(ft.problem({'posted_days': 7, 'salary_min': 50000, 'exp_max': 0, 'location': '台北'}), '')
        self.assertIn('上架天數', ft.problem({'posted_days': 0}))
        self.assertIn('薪資', ft.problem({'salary_min': '5'}))
        self.assertTrue(ft.problem({'strict': ['nope']}))

    def test_page_terms_are_judged_by_the_program(self):
        terms = ft.normalize({'posted_days': 7, 'applicants_max': 10})
        self.assertEqual(ft.check_page({'posted_at': '2026-10-12', 'applicants': '兩週內 6~10 人'}, terms, TODAY),
                         ([], []))
        self.assertEqual(ft.check_page({'posted_at': '2026-09-01', 'applicants': '兩週內 30 人以上'}, terms, TODAY),
                         (['上架天數', '應徵人數'], []))
        self.assertEqual(ft.check_page({'applicants': '不到 25 人'}, terms, TODAY), (['應徵人數'], ['上架天數']),
                         '「不到 25 人」上限是 24,超過 10;上架日查不到就留著標出來')

    def test_unknown_is_dropped_only_when_strict(self):
        terms = ft.normalize({'posted_days': 7, 'strict': ['posted_days']})
        self.assertEqual(ft.check_page({}, terms, TODAY), (['上架天數'], []))

    def test_judged_terms_are_compared_by_the_program(self):
        terms = ft.normalize({'salary_min': 50000, 'exp_max': 3, 'location': '台北 遠端'})
        self.assertEqual(ft.check_judged({'salary_month': 60000, 'exp_years': 2, 'location': '符合'}, terms), ([], []))
        self.assertEqual(ft.check_judged({'salary_month': 40000, 'exp_years': 5, 'location': '不符'}, terms),
                         (['薪資', '經驗', '地點'], []))
        self.assertEqual(ft.check_judged({}, terms), ([], ['薪資', '經驗', '地點']))

    def test_describe_lists_only_the_terms_that_are_set(self):
        text = ft.describe(ft.normalize({'posted_days': 7, 'salary_min': 50000}))
        self.assertIn('上架最多 7 天', text)
        self.assertIn('50000', text)
        self.assertNotIn('經驗', text)


if __name__ == '__main__':
    unittest.main()
