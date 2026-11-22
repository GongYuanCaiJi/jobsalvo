# -*- coding: utf-8 -*-
"""找缺時間設定的輸入格式。"""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
import settings_api  # noqa: E402


class FindMinutesSetting(unittest.TestCase):
    def test_setting_takes_blank_zero_or_1_to_999(self):
        for ok in (None, '', 0, 15, 999):
            self.assertEqual(settings_api.find_minutes_problem(ok), '', ok)
        for bad in (-1, 1000, '15', 1.5, True):
            self.assertTrue(settings_api.find_minutes_problem(bad), bad)


if __name__ == '__main__':
    unittest.main()
