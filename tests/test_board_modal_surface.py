"""看板對話框(#rzmodal 裡的 .rzm-box)要有自己的底色:遮罩是半透明的,沒底色的框,字會直接壓在底下的頁面上。"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = (ROOT / 'board/board.js').read_text(encoding='utf-8')
CSS = re.sub(r'/\*.*?\*/', '', (ROOT / 'board/board.css').read_text(encoding='utf-8'), flags=re.S)
# 讀稿模式整個遮罩換成頁面底色(#rzmodal.rd),框本身不用再有底
OWN_BACKDROP = {'rd-box'}


class ModalSurface(unittest.TestCase):
    def test_every_modal_box_has_a_background(self):
        boxes = set(re.findall(r'rzm-box ([\w-]+)', JS)) - OWN_BACKDROP
        self.assertTrue(boxes)
        bare = sorted(b for b in boxes
                      if not any('background' in body for sel, body in re.findall(r'([^{}]+)\{([^}]*)\}', CSS)
                                 if re.search(r'\.' + re.escape(b) + r'(?![\w-])', sel)))
        self.assertEqual(bare, [], '這些對話框沒有底色,字會壓在半透明遮罩和底下的頁面上')


if __name__ == '__main__':
    unittest.main()
