"""字表中的每個使用者可見代碼都有唯一模板與來源。"""
import os
import sys
import unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
from web.marks import SOURCE, TEMPLATES, Source, say


class Marks(unittest.TestCase):
    def test_every_code_has_a_source_and_template(self):
        self.assertEqual(set(SOURCE), set(TEMPLATES))
        for code, source in SOURCE.items():
            with self.subTest(code=code):
                self.assertIsInstance(source, Source)
                self.assertTrue(TEMPLATES[code])

    def test_say_uses_the_code_template_and_declared_source(self):
        mark = say('PAGE_NO_TEXT', where='頁面', why='沒有文字')
        # 原斷言「頁面讀不出字：沒有文字」。條文:#662 只准把程式造成的原因留在 params,句子只說是哪一種結果(cf376cda 字表)
        self.assertEqual(mark.text, '頁面讀不出字')
        self.assertEqual(dict(mark.params)['why'], '沒有文字')
        # 原斷言 Source.PROGRAM。條文:#591 ID 第 88 行,讀不到、截圖沒取得照實記成「程式沒比到」。
        self.assertEqual(mark.source, Source.NOT_COMPARED)


if __name__ == '__main__':
    unittest.main()
