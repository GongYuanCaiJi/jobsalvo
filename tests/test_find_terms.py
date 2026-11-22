# -*- coding: utf-8 -*-
"""找缺條件的設定:壞值當沒填、存之前擋掉看板表達不了的值。"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import find_terms as ft  # noqa: E402


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

if __name__ == '__main__':
    unittest.main()
