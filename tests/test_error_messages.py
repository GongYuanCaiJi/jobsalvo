# -*- coding: utf-8 -*-
"""錯誤訊息三要素:發生什麼、為什麼、下一步怎麼做。看板上的提示和伺服器回給看板的訊息,
像錯誤的每一則都要講下一步(再試、去哪裡、按什麼、改什麼),不能只丟一句「沒存成」。"""
import os, re, unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
FAIL = re.compile(r'失敗|沒成功|沒送出|存不起來|讀不到|找不到|不能|錯誤|壞了|沒跑成|沒跑完|沒存|不支援|無法|查不到|連不到|逾時|不對')
NEXT = re.compile(r'再試|再按|請|先|去「|到「|按「|按一下|改|檢查|看紀錄|重新|換|等|填|寫|用|裝|設定頁|點|打開|跑|登入|確認|選|上傳|貼')
SOURCES = (
    ('board/board.js', re.compile(r"snack\(('[^\n]{2,300})")),
    ('tools/board_server.py', re.compile(r"'msg':\s*('[^\n]{2,300})")),
)
END = re.compile(r"\);|,\s*null|,\s*function|'\}|\}\)")


def whole_message(expr):
    """提示常寫成 '沒存成(' + err.message + '),再按一次':把這一則裡所有字串接起來再判斷。"""
    cut = END.search(expr)
    return ''.join(re.findall(r"'([^']*)'", expr[:cut.start() + 1] if cut else expr))


def offenders():
    bad = []
    for rel, pat in SOURCES:
        with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
            src = f.read()
        for m in pat.finditer(src):
            text = whole_message(m.group(1))
            if FAIL.search(text) and not NEXT.search(text):
                bad.append(f'{rel}:{src.count(chr(10), 0, m.start()) + 1} {text}')
    return bad


class ErrorMessages(unittest.TestCase):
    def test_every_error_message_says_what_to_do_next(self):
        self.assertEqual(offenders(), [], '這些錯誤訊息沒講下一步怎麼做')


if __name__ == '__main__':
    unittest.main()
