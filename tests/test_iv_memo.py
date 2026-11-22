import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import iv


class MemoCheckTest(unittest.TestCase):
    def test_one_liner_and_short_script_pass(self):
        ok = '做過。\n\n【要點】① 一 ② 二\n\n──────\n\n## 完整\n\n很長的回答' + '字' * 200
        self.assertEqual(iv.memo_check(ok, 't'), '')
        self.assertEqual(iv.memo_check('短答案', 't'), '')      # 不超過 90 字,整份就是一句話

    def test_long_script_without_one_liner_is_refused(self):
        self.assertIn('沒有一句話', iv.memo_check('字' * 200, 't'))

    def test_heading_or_keypoints_do_not_count_as_one_liner(self):
        self.assertIn('沒有一句話', iv.memo_check('## 完整回答\n\n──────\n\n' + '字' * 120, 't'))
        self.assertIn('沒有一句話', iv.memo_check('【要點】① 一\n\n──────\n\n' + '字' * 120, 't'))


if __name__ == '__main__':
    unittest.main()
