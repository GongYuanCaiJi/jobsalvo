#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
form_record 的回歸測試:「答案只有一個真相,就是表單答案庫;表單只留指標」這條由程式擋,不靠誰記得。

跑法(repo 根目錄):python3 -m unittest discover -s tests
"""
import os, sys, json, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import form_record as fr        # noqa: E402

U1, U2, U3 = 'https://ex.test/job/1', 'https://ex.test/job/2', 'https://ex.test/job/3'
NAT = {'q': 'What is your nationality?', 'v': 'Taiwan', 'zh': '台灣', 'k': 'nationality',
       'kind': 'val', 'why': '履歷沒寫,我推的', 'bank_q': '你的國籍'}
WHY = {'q': 'Why this role?', 'v': 'Because A.', 'kind': 'txt', 'zh': '因為 A。', 'bank_q': '為什麼對這個職位有興趣'}
NAME = {'q': 'Full name', 'v': 'Alex Chen', 'src': 'rz'}
T = '2026-09-22'


def fields(fb, url):
    return fb[url]['form']['f']


class PageValuesAreObservations(unittest.TestCase):
    def test_page_values_are_not_compared_with_bank_answers(self):
        fb = {'__ans__': [{'k': 'src', 'q': '從哪裡得知', 'v': '公司徵才頁 / Company career page'}]}
        fr.apply_record(fb, U1, 'x', [{'q': 'How did you hear?', 'k': 'src', 'page_value': 'Company career page'}], today=T)
        (f,) = fields(fb, U1)
        self.assertEqual(f, {'q': 'How did you hear?', 'src': 'bank', 'k': 'src'})
        fr.apply_record(fb, U2, 'x', [{'q': 'How did you hear?', 'k': 'src', 'page_value': 'LinkedIn'}], today=T)
        self.assertEqual(fields(fb, U2)[0]['k'], 'src')


class OneTruth(unittest.TestCase):
    def test_answers_live_only_in_the_bank(self):
        fb = {}
        fr.apply_record(fb, U1, 'Lever', [NAME, NAT, WHY], today=T)
        name, nat, why = fields(fb, U1)
        self.assertEqual(name, {'q': 'Full name', 'v': 'Alex Chen', 'src': 'rz'})   # 履歷的真相留在表單上
        self.assertEqual(nat, {'q': 'What is your nationality?', 'src': 'bank', 'k': 'nationality'})
        self.assertEqual(set(why), {'q', 'src', 'k'})                                   # 短文也只剩指標
        e = [x for x in fb['__ans__'] if x['k'] == why['k']][0]
        self.assertEqual((e['q'], e['v'], e['zh'], e['kind'], e['inf']),
                         ('為什麼對這個職位有興趣', 'Because A.', '因為 A。', 'txt', T))
        self.assertEqual(fr.validate(fb, list(fb)), [])

    def test_a_translated_question_stays_on_the_form_for_the_confirm_screen(self):
        fb = {}
        fr.apply_record(fb, U1, 'Lever', [dict(NAME, qz='全名'), dict(NAT, qz='你的國籍？')], today=T)
        name, nat = fields(fb, U1)
        self.assertEqual((name['qz'], nat['qz']), ('全名', '你的國籍？'))
        self.assertEqual(fr.validate(fb, list(fb)), [])

    def test_a_recorded_answer_is_inferred_until_he_confirms_it_on_the_board(self):
        fb = {}
        fr.apply_record(fb, U1, 'x', [NAT], today=T)
        nat, = fb['__ans__']
        self.assertEqual(nat['inf'], T); self.assertNotIn('at', nat)

    def test_same_question_same_value_is_one_entry_across_forms(self):
        fb = {}
        fr.apply_record(fb, U1, 'x', [dict(WHY, v='', pj=False)], today=T)
        fr.apply_record(fb, U2, 'x', [{'q': 'Why this role?', 'v': '', 'pj': False}], today=T)   # 問法一字不差(ADR-0011)
        self.assertEqual(len(fb['__ans__']), 1)
        self.assertEqual(fields(fb, U1)[0]['k'], fields(fb, U2)[0]['k'])

    def test_same_question_other_value_is_its_own_entry(self):
        fb = {}
        fr.apply_record(fb, U1, 'x', [WHY], today=T)
        fr.apply_record(fb, U2, 'x', [dict(WHY, v='Because B.')], today=T)
        self.assertEqual(len(fb['__ans__']), 2)
        self.assertNotEqual(fields(fb, U1)[0]['k'], fields(fb, U2)[0]['k'])

    def test_given_k_reuses_and_never_overwrites_a_different_value(self):
        fb = {}
        fr.apply_record(fb, U1, 'x', [NAT], today=T)
        fr.apply_record(fb, U2, 'x', [{'q': 'Nationality', 'k': 'nationality'}], today=T)   # 不給值 = 用庫裡的
        self.assertEqual(len(fb['__ans__']), 1)
        self.assertIn('Nationality', fb['__ans__'][0]['qs'])
        with self.assertRaises(ValueError):
            fr.apply_record(fb, U3, 'x', [{'q': 'Nationality', 'k': 'nationality', 'v': 'ROC'}], today=T)

    def test_reusing_a_confirmed_entry_keeps_it_confirmed(self):
        fb = {'__ans__': [{'k': 'nationality', 'q': '你的國籍', 'v': 'Taiwan', 'why': '', 'at': '2026-09-20'}]}
        fr.apply_record(fb, U1, 'x', [NAT], today=T)
        self.assertEqual(fb['__ans__'][0].get('at'), '2026-09-20')
        self.assertNotIn('inf', fb['__ans__'][0])

    def test_rewriting_my_own_answer_updates_it_instead_of_leaving_an_orphan(self):
        fb = {}
        fr.apply_record(fb, U1, 'x', [WHY], today='2026-09-20')
        fb['__ans__'][0].pop('inf'); fb['__ans__'][0]['at'] = '2026-09-20'     # 他在看板上確認
        fr.apply_record(fb, U1, 'x', [dict(WHY, v='Because C.')], today=T)
        self.assertEqual(len(fb['__ans__']), 1)
        e = fb['__ans__'][0]
        self.assertEqual((e['v'], e['inf']), ('Because C.', T))    # 我改了他確認過的,要他重看
        self.assertNotIn('at', e)

    def test_rewriting_a_shared_answer_does_not_change_the_other_form(self):
        fb = {}
        W = dict(WHY, pj=False)
        fr.apply_record(fb, U1, 'x', [W], today=T)
        fr.apply_record(fb, U2, 'x', [W], today=T)
        fr.apply_record(fb, U1, 'x', [dict(W, v='Because C.')], today=T)
        self.assertEqual(len(fb['__ans__']), 2)
        k2 = fields(fb, U2)[0]['k']
        self.assertEqual([e['v'] for e in fb['__ans__'] if e['k'] == k2], ['Because A.'])

    def test_essays_default_to_this_job_only_and_never_attach_to_another_job(self):
        fb = {}
        fr.apply_record(fb, U1, 'x', [WHY, NAT], today=T)
        why, nat = fb['__ans__']
        self.assertEqual((why.get('pj'), nat.get('pj')), (1, None))     # 短文猜這缺專用,事實猜共用
        fr.apply_record(fb, U2, 'x', [WHY], today=T)                      # 同一題、同一個字,換一個職缺
        self.assertEqual(len(fb['__ans__']), 3)                           # 不能接到 U1 那篇
        self.assertNotEqual(fields(fb, U1)[0]['k'], fields(fb, U2)[0]['k'])
        fr.apply_record(fb, U3, 'x', [dict(WHY, pj=False, pjw='關於他本人,哪張都能用')], today=T)
        e = [x for x in fb['__ans__'] if x['k'] == fields(fb, U3)[0]['k']][0]
        self.assertEqual((e.get('pj'), e.get('pjw')), (None, '關於他本人,哪張都能用'))

    def test_shared_list_is_what_i_read_before_filling(self):
        fb = {'__ans__': [{'k': 'a', 'q': 'A', 'v': '是', 'at': T},
                          {'k': 'b', 'q': 'B', 'v': 'Yes', 'zh': '是', 'inf': T},
                          {'k': 'c', 'q': 'C', 'v': 'Because.', 'zh': '因為', 'pj': 1, 'at': T},
                          {'k': 'd', 'q': 'D', 'v': '', 'at': T}]}
        self.assertEqual([e['k'] for e in fr.find_shared(fb)], ['a'])

    def test_resume_or_skip_field_cannot_have_k(self):
        with self.assertRaises(ValueError):
            fr.apply_record({}, U1, 'x', [dict(NAME, k='name')])

    def test_english_answer_without_chinese_is_refused(self):
        with self.assertRaises(ValueError):                 # 他看不懂英文:英文答案一定附中文
            fr.apply_record({}, U1, 'x', [{'q': 'Do you need a visa?', 'v': 'No', 'kind': 'pick'}], today=T)
        fr.apply_record({}, U1, 'x', [{'q': '要簽證嗎', 'v': '否'}], today=T)      # 中文答案不用翻

    def test_locked_form_is_refused(self):
        fb = {U1: {'form': {'plat': 'x', 'f': [], 'lock': 1}}}
        with self.assertRaises(ValueError):
            fr.apply_record(fb, U1, 'x', [NAT])


class Validate(unittest.TestCase):
    def test_catches_answers_outside_the_bank_and_dangling_keys(self):
        fb = {'__ans__': [{'k': 'a', 'q': 'Q', 'v': 'x', 'at': T}],
              U1: {'form': {'plat': 'x', 'f': [{'q': 'Q', 'src': 'bank', 'k': 'a', 'v': 'x'},
                                               {'q': 'R', 'src': 'bank', 'k': 'gone'},
                                               {'q': 'S', 'src': 'new', 'v': 'y'}]}},
              U2: {'form': {'plat': 'x', 'lock': 1, 'f': [{'q': 'Q', 'src': 'bank', 'k': 'a', 'refill': 1}]}}}
        bad = ' '.join(fr.validate(fb, list(fb)))
        for want in ("帶了 ['v']", 'gone 答案庫裡沒有', "src='new'", '已投遞還標著 refill'):
            self.assertIn(want, bad)

    def test_he_may_delete_an_answer_only_sent_forms_used(self):
        fb = {'__ans__': [], U1: {'form': {'plat': 'x', 'lock': 1, 'f': [{'q': 'Q', 'src': 'bank', 'k': 'gone'}]}}}
        self.assertEqual(fr.validate(fb, list(fb)), [])          # 送出時的原字在流水帳

    def test_an_emptied_answer_only_sent_forms_use_is_not_his_todo(self):
        fb = {'__ans__': [{'k': 'a', 'q': 'A', 'v': '', 'at': T}],
              U1: {'form': {'plat': 'x', 'lock': 1, 'f': [{'q': 'A', 'src': 'bank', 'k': 'a'}]}}}
        self.assertEqual(fr.find_pending(fb), [])
        fb[U2] = {'form': {'plat': 'x', 'f': [{'q': 'A', 'src': 'bank', 'k': 'a'}]}}
        self.assertEqual([e['k'] for e, _ in fr.find_pending(fb)], ['a'])


class OneBrokenFormDoesNotBlockTheOthers(unittest.TestCase):
    """一張表單壞掉(他在外部送出時還標著 refill、存檔衝突後指到答案庫沒有的那條),
    以前之後每一張「記表單」「翻譯」都失敗:記的時候檢查的是整個看板。只檢查這一張跟答案庫本身。"""

    def board(self):
        return {'__ans__': [{'k': 'n', 'q': '國籍', 'v': 'Taiwan', 'zh': '台灣', 'at': T}],
                U2: {'form': {'plat': 'x', 'lock': 1, 'f': [{'q': 'Nationality', 'src': 'bank', 'k': 'n', 'refill': 1}]}},
                U3: {'form': {'plat': 'x', 'f': [{'q': 'Why?', 'src': 'bank', 'k': 'lost'}]}}}

    def test_another_card_still_records(self):
        fb = self.board()
        fr.apply_record(fb, U1, 'x', [dict(NAT, k='n', v='Taiwan')])
        self.assertEqual(fields(fb, U1)[0]['k'], 'n')

    def test_this_card_is_still_checked(self):
        fb = self.board()
        with self.assertRaises(ValueError):
            fr.apply_record(fb, U1, 'x', [{'q': 'S', 'src': 'new', 'v': 'y', 'k': 'n'}])   # 給了 k 卻是別的值
        self.assertTrue(fr.validate(fb, list(fb)))               # 整個看板照樣查得出來

class Translate(unittest.TestCase):
    def test_missing_chinese_is_listed(self):
        fb = {'__ans__': [{'k': 'y', 'q': '全職', 'v': 'Yes', 'at': T}]}
        self.assertIn('沒有中文翻譯', ' '.join(fr.validate(fb, list(fb))))


class Refill(unittest.TestCase):
    def test_pending_and_clear(self):
        fb = {'__ans__': [{'k': 'n', 'q': 'N', 'v': 'X', 'at': T}],
              U1: {'form': {'plat': 'x', 'f': [{'q': 'Nationality', 'src': 'bank', 'k': 'n', 'refill': 1},
                                               {'q': 'Other', 'v': 'Y', 'src': 'rz'}]}},
              U2: {'form': {'plat': 'x', 'lock': 1, 'f': [{'q': 'Old', 'src': 'bank', 'k': 'n'}]}}}
        self.assertEqual(fr.find_pending(fb), [])
        fb['__ans__'].append({'k': 'e', 'q': 'E', 'v': ' ', 'at': T})      # 空白的答案也算等他(看板上不給 ✓)
        self.assertEqual([e['k'] for e, _ in fr.find_pending(fb)], ['e'])
        self.assertEqual(fr.users(fb, 'n'), {U1: False, U2: True})
        self.assertEqual(fr.apply_clear_refill(fb, U1, 'Nation'), 1)
        self.assertEqual(fr.apply_clear_refill(fb, U1), 0)


class FromFill(unittest.TestCase):
    """代投只寫一份 fill.json:fields 每欄照抄頁面上的值,再標來源;form_record --from-fill 從它記進看板。"""

    FILL = {'platform': 'Lever', 'fields': [
        {'q': 'Full name', 'value': 'Alex Chen', 'src': 'rz', 'k': None},
        {'q': 'What is your nationality?', 'value': 'Taiwan', 'src': 'bank', 'k': 'nationality'},
        {'q': 'Why this role?', 'value': 'Because A.', 'src': 'bank', 'zh': '因為 A。', 'kind': 'txt',
         'bank_q': '為什麼對這個職位有興趣'},
        {'q': 'Salary', 'value': '', 'src': 'skip', 'why': '他說面談再談'},
    ]}

    def test_bank_page_value_is_not_recorded_as_an_answer(self):
        name, nat, why, sal = fr.fields_from_fill(self.FILL)
        self.assertEqual(name, {'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'})
        # value 是頁面觀察,銀行答案欄只保留來源鍵
        self.assertEqual(nat, {'q': 'What is your nationality?', 'src': 'bank', 'k': 'nationality'})
        self.assertEqual(why['v'], 'Because A.')
        self.assertEqual((sal['v'], sal['why']), ('', '他說面談再談'))

class ReadLang(unittest.TestCase):
    """他看得懂的語言(設定 resume.read_lang):送出的答案跟它不是同一套文字才要附翻譯。"""

    def test_chinese_reader_is_unchanged(self):
        self.assertTrue(fr.needs_translation('Five years', 'zh'))
        self.assertFalse(fr.needs_translation('五年', 'zh'))
        self.assertFalse(fr.needs_translation('5', 'zh'))
        self.assertEqual(fr.lang_words('zh'), ('中文', '英文'))

    def test_english_reader_needs_no_translation_for_english(self):
        self.assertFalse(fr.needs_translation('Five years', 'en'))
        self.assertTrue(fr.needs_translation('五年', 'en'))
        self.assertTrue(fr.needs_translation('5年', 'en'))
        self.assertTrue(fr.needs_translation('Five years', 'ja'))

    def test_validate_follows_the_setting(self):
        from unittest.mock import patch
        fb = {'__ans__': [{'k': 'a1', 'q': 'Years', 'v': 'Five years'}]}
        with patch.object(fr, 'read_lang', return_value='zh'):
            bad = fr.validate(fb, list(fb))
            self.assertEqual(len(bad), 1)
            self.assertIn('a1', bad[0])
        with patch.object(fr, 'read_lang', return_value='en'):
            self.assertEqual(fr.validate(fb, list(fb)), [])


if __name__ == '__main__':
    unittest.main()


class RefusedInput(unittest.TestCase):
    """agent 交來的表單欄位哪裡不對,就擋在哪裡、講是哪一欄;以前這幾條擋的路一次都沒走過。"""

    def test_unknown_kind_or_src_is_refused(self):
        for bad, want in (({'q': 'Q', 'v': 'x', 'kind': 'poem'}, 'kind 要是'),
                          ({'q': 'Q', 'v': 'x', 'src': 'guess'}, 'src 要是')):
            with self.subTest(want):
                fb = {}
                with self.assertRaises(ValueError) as e:
                    fr.apply_record(fb, U1, 'x', [bad], today=T)
                self.assertIn(want, str(e.exception))
                self.assertNotIn('form', fb.get(U1, {}))

    def test_given_k_with_the_same_value_reuses_the_entry(self):
        fb = {}
        fr.apply_record(fb, U1, 'x', [NAT], today=T)
        fr.apply_record(fb, U2, 'x', [dict(NAT, q='Nationality')], today=T)
        self.assertEqual([e['k'] for e in fb['__ans__']], ['nationality'])
        self.assertEqual(fields(fb, U2)[0]['k'], 'nationality')

    def test_validate_names_the_other_broken_shapes(self):
        fb = {'__ans__': [{'k': 'a', 'q': 'Q', 'v': '台灣'}, {'k': 'a', 'q': 'Q2', 'v': '台灣'},
                          {'k': 'b', 'q': 'R', 'v': '是', 'inf': T, 'at': T}],
              U1: {'form': {'plat': 'x', 'f': [{'q': 'Name', 'src': 'rz', 'k': 'a'}]}}}
        bad = ' '.join(fr.validate(fb, list(fb)))
        for want in ('重複的 k', '是 rz 卻有 k', '同時標了'):
            self.assertIn(want, bad)

    def test_recording_the_same_answer_again_changes_nothing(self):
        fb = {}
        fr.apply_record(fb, U1, 'x', [WHY], today=T)
        bank = json.loads(json.dumps(fb['__ans__']))
        fr.apply_record(fb, U1, 'x', [WHY], today='2026-12-31')
        self.assertEqual(fb['__ans__'], bank)

    def test_fill_rows_without_a_question_are_skipped(self):
        got = fr.fields_from_fill({'fields': ['亂寫的', {'value': 'x'}, {'q': 'Name', 'value': 'Alex', 'src': 'rz'}]})
        self.assertEqual(got, [{'q': 'Name', 'src': 'rz', 'v': 'Alex'}])


class Commands(unittest.TestCase):
    """填表交件單改由 fill.py 的共用 Schema 收件。"""
