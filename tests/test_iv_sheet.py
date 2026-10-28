import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')))
import interview_bank as ib
import iv_sheet


def _data():
    def item(i, t, script, **kw):
        return ib.from_form({'id': i, 't': t, 'cat': kw.pop('cat', '技術基本功'), 'stage': 'ok', 'script': script, **kw})
    items = [
        item('q1', '什麼是 AOI', '一句話答案。\n\n【要點】① 取像　② 判斷\n\n──────\n\n完整回答第一段。'),
        item('q2', '打光為什麼重要', '短答案', parent='q1'),
        item('q4', '反問：團隊最花時間的是什麼', '想請教，團隊最花時間的是哪一類問題？', cat='反問面試官'),
        item('q3', '自我介紹', '## 90 版\n\n' + '很長的內容。' * 30, cat='自我介紹', focus='不要靠年資'),
    ]
    links = {'J': {q: {'k': 'prep', 'src': 'guess', 'cite': '', 'quote': '', 'why': 'x'} for q in ('q1', 'q2', 'q3', 'q4')}}
    return {'jobs': [{'id': 'J', 'company': '某某公司', 'title': '工程師'}],
            'bank': {'items': items, 'links': links, 'rounds': {'J': [{'at': '2026-10-27T14:00', 'how': '現場', 'where': '新店', 'note': ''}]},
                     'cats': ['自我介紹', '技術基本功']}}


class IvSheetTest(unittest.TestCase):
    def test_shared_question_is_on_every_sheet_and_the_card_variant_replaces_only_that_card(self):
        data = _data()
        data['jobs'].append({'id': 'K', 'company': '另一家', 'title': '工程師'})
        data['bank']['links']['K'] = {}
        data['bank'] = ib.apply(data['bank'], 'share', None, 'q3', None, True)
        data['bank'] = ib.apply(data['bank'], 'variant', {'script': '海華版的自我介紹。', 'focus': ''}, 'q3', 'J', True)
        mj, _ = iv_sheet.build(data, 'J')
        mk, _ = iv_sheet.build(data, 'K')
        self.assertIn('海華版的自我介紹。', mj)
        self.assertNotIn('很長的內容', mj)
        self.assertIn('很長的內容', mk)
        self.assertIn('| 自我介紹 |', mk)
        data['bank'] = ib.apply(data['bank'], 'variant', None, 'q3', 'J', None)
        self.assertIn('很長的內容', iv_sheet.build(data, 'J')[0])

    def test_sheet_puts_one_liner_in_a_quote_and_follow_ups_under_the_parent(self):
        md, title = iv_sheet.build(_data(), 'J')
        self.assertEqual(title, '某某公司 工程師')
        self.assertTrue(md.startswith('| 題目'))
        self.assertIn('#### 什麼是 AOI\n\n> 一句話答案。\n\n完整回答第一段。', md)
        self.assertLess(md.index('#### 什麼是 AOI'), md.index('##### 打光為什麼重要'))
        self.assertLess(md.index('### 自我介紹'), md.index('### 技術基本功'))

    def test_glance_table_uses_the_one_liner_or_short_script_and_never_prints_ids(self):
        md, _ = iv_sheet.build(_data(), 'J')
        self.assertIn('| 什麼是 AOI | 一句話答案。 | ① 取像<br>② 判斷 |', md)
        self.assertIn('| ↳ 打光為什麼重要 | 短答案 |  |', md)
        self.assertIn('| 自我介紹 | (見附錄) |  |', md)
        self.assertIn('**對方在看**　不要靠年資', md)
        self.assertNotIn('q1', md)
        self.assertIn('**90 版**', md)

    def test_questions_to_ask_get_their_own_block_and_full_answers_go_to_the_appendix(self):
        md, _ = iv_sheet.build(_data(), 'J')
        self.assertIn('## 要問面試官\n\n- 想請教，團隊最花時間的是哪一類問題？', md)
        self.assertLess(md.index('| 題目'), md.index('## 要問面試官'))
        self.assertLess(md.index('## 要問面試官'), md.index('## 附錄:完整回答'))
        self.assertNotIn('| 反問', md)


if __name__ == '__main__':
    unittest.main()
