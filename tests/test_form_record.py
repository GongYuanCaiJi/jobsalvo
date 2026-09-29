#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
form_record 的回歸測試:「答案只有一個真相,就是表單答案庫;表單只留指標」這條由程式擋,不靠誰記得。

跑法(repo 根目錄):python3 -m unittest discover -s tests
"""
import os, sys, json, tempfile, shutil, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
from _env import read_board, read_fb  # noqa: E402
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import board_doc as bd          # noqa: E402
import form_record as fr        # noqa: E402

U1, U2, U3 = 'https://ex.test/job/1', 'https://ex.test/job/2', 'https://ex.test/job/3'
NAT = {'q': 'What is your nationality?', 'v': 'Taiwan', 'zh': '台灣', 'k': 'nationality',
       'kind': 'val', 'why': '履歷沒寫,我推的', 'bank_q': '你的國籍'}
WHY = {'q': 'Why this role?', 'v': 'Because A.', 'kind': 'txt', 'zh': '因為 A。', 'bank_q': '為什麼對這個職位有興趣'}
NAME = {'q': 'Full name', 'v': 'Alex Chen', 'src': 'rz'}
T = '2026-09-22'


def fields(fb, url):
    return fb[url]['form']['f']


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
        self.assertEqual(fr.validate(fb), [])

    def test_inferred_answer_is_pending_his_words_are_confirmed(self):
        fb = {}
        fr.apply_record(fb, U1, 'x', [NAT, dict(WHY, his=True)], today=T)
        nat, why = fb['__ans__']
        self.assertEqual(nat['inf'], T); self.assertNotIn('at', nat)
        self.assertEqual(why['at'], T); self.assertNotIn('inf', why)

    def test_same_question_same_value_is_one_entry_across_forms(self):
        fb = {}
        fr.apply_record(fb, U1, 'x', [dict(WHY, v='', pj=False)], today=T)
        fr.apply_record(fb, U2, 'x', [{'q': 'why this role', 'v': '', 'pj': False}], today=T)   # 問法大小寫標點不計
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
        fr.apply_record(fb, U1, 'x', [dict(WHY, his=True)], today='2026-09-20')
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
        bad = ' '.join(fr.validate(fb))
        for want in ("帶了 ['v']", 'gone 答案庫裡沒有', "src='new'", '已投遞還標著 refill'):
            self.assertIn(want, bad)

    def test_he_may_delete_an_answer_only_sent_forms_used(self):
        fb = {'__ans__': [], U1: {'form': {'plat': 'x', 'lock': 1, 'f': [{'q': 'Q', 'src': 'bank', 'k': 'gone'}]}}}
        self.assertEqual(fr.validate(fb), [])          # 送出時的原字在流水帳

    def test_an_emptied_answer_only_sent_forms_use_is_not_his_todo(self):
        fb = {'__ans__': [{'k': 'a', 'q': 'A', 'v': '', 'at': T}],
              U1: {'form': {'plat': 'x', 'lock': 1, 'f': [{'q': 'A', 'src': 'bank', 'k': 'a'}]}}}
        self.assertEqual(fr.find_pending(fb), [])
        fb[U2] = {'form': {'plat': 'x', 'f': [{'q': 'A', 'src': 'bank', 'k': 'a'}]}}
        self.assertEqual([e['k'] for e, _ in fr.find_pending(fb)], ['a'])


class Translate(unittest.TestCase):
    def test_he_edits_chinese_i_retranslate_and_unsent_forms_get_retyped(self):
        fb = {'__ans__': [{'k': 'n', 'q': '國籍', 'v': 'Taiwan', 'zh': '中華民國', 'tr': 1, 'at': T}],
              U1: {'form': {'plat': 'x', 'f': [{'q': 'Nationality', 'src': 'bank', 'k': 'n'}]}},
              U2: {'form': {'plat': 'x', 'lock': 1, 'f': [{'q': 'Nationality', 'src': 'bank', 'k': 'n'}]}}}
        self.assertEqual([e['k'] for e in fr.find_translate(fb)], ['n'])
        fr.apply_translate(fb, 'n', en='Republic of China (Taiwan)')
        e = fb['__ans__'][0]
        self.assertEqual((e['v'], 'tr' in e), ('Republic of China (Taiwan)', False))
        self.assertEqual(fb[U1]['form']['f'][0].get('refill'), 1)
        self.assertNotIn('refill', fb[U2]['form']['f'][0])          # 已投遞的不動
        self.assertEqual(fr.find_translate(fb), [])

    def test_missing_chinese_is_listed_and_can_be_filled(self):
        fb = {'__ans__': [{'k': 'y', 'q': '全職', 'v': 'Yes', 'at': T}]}
        self.assertIn('沒有中文翻譯', ' '.join(fr.validate(fb)))
        self.assertEqual([e['k'] for e in fr.find_translate(fb)], ['y'])
        fr.apply_translate(fb, 'y', zh='是')
        self.assertEqual(fr.validate(fb), [])


class Refill(unittest.TestCase):
    def test_find_and_clear(self):
        fb = {'__ans__': [{'k': 'n', 'q': 'N', 'v': 'X', 'at': T}],
              U1: {'form': {'plat': 'x', 'f': [{'q': 'Nationality', 'src': 'bank', 'k': 'n', 'refill': 1},
                                               {'q': 'Other', 'v': 'Y', 'src': 'rz'}]}},
              U2: {'form': {'plat': 'x', 'lock': 1, 'f': [{'q': 'Old', 'src': 'bank', 'k': 'n'}]}}}
        self.assertEqual(fr.find_refills(fb), [(U1, 'Nationality')])
        self.assertEqual(fr.find_pending(fb), [])
        fb['__ans__'].append({'k': 'e', 'q': 'E', 'v': ' ', 'at': T})      # 空白的答案也算等他(看板上不給 ✓)
        self.assertEqual([e['k'] for e, _ in fr.find_pending(fb)], ['e'])
        self.assertEqual(fr.users(fb, 'n'), {U1: False, U2: True})
        self.assertEqual(fr.apply_clear_refill(fb, U1, 'Nation'), 1)
        self.assertEqual(fr.find_refills(fb), [])


class ThroughTheBoardFile(unittest.TestCase):
    """真的走 set_fb 寫檔:他手機上已經有的標記不能被蓋掉。"""

    def test_record_keeps_his_marks(self):
        d = tempfile.mkdtemp(prefix='formrec-')
        try:
            p = os.path.join(d, 'board.html')
            doc = bd.assemble(':root{--a:1}', '<b id="stat-first">0</b>', '', {'jobs': [{'id': U1}]},
                              json.dumps({U1: {'s': 'like', 'app': 'ship'}}, ensure_ascii=False), '/*app*/')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(doc)
            made = fr.record(U1, 'Lever', [NAT], live=p)
            self.assertEqual(made, ['nationality'])
            fb = read_fb(p)
            self.assertEqual(fb[U1]['s'], 'like')
            self.assertEqual(fb[U1]['form']['f'][0], {'q': NAT['q'], 'src': 'bank', 'k': 'nationality'})
            self.assertTrue(fb['__ans__'][0]['inf'])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_agent_board_is_the_default_write_target(self):
        d = tempfile.mkdtemp(prefix='formrec-agent-board-')
        try:
            target = os.path.join(d, 'agent-board.html')
            live = os.path.join(d, 'live-board.html')
            initial = bd.assemble(':root{--a:1}', '<b id="stat-first">0</b>', '',
                                 {'jobs': [{'id': U1}]}, '{}', '/*app*/')
            for path in (target, live):
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(initial)
            old_live = bd.LIVE
            bd.LIVE = live
            try:
                previous = os.environ.get('AGENT_BOARD')
                os.environ['AGENT_BOARD'] = target
                try:
                    self.assertEqual(fr.record(U1, 'Lever', [NAT]), ['nationality'])
                    bd.set_fb(lambda fb: fb.setdefault(U1, {}).__setitem__('s', 'like'), live=live)
                    bd.set_data(lambda data, _fb: data['jobs'].append({'id': U2}), live=live)
                finally:
                    if previous is None:
                        os.environ.pop('AGENT_BOARD', None)
                    else:
                        os.environ['AGENT_BOARD'] = previous
            finally:
                bd.LIVE = old_live
            target_data = read_board(target)
            target_fb = json.loads(target_data['fb'])
            live_data = read_board(live)
            live_fb = json.loads(live_data['fb'])
            self.assertIn('form', target_fb[U1])
            self.assertEqual(target_fb[U1]['s'], 'like')
            self.assertEqual([job['id'] for job in target_data['data']['jobs']], [U1, U2])
            self.assertEqual(live_fb, {})
            self.assertEqual([job['id'] for job in live_data['data']['jobs']], [U1])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class FromFill(unittest.TestCase):
    """代投只寫一份 fill.json:fields 每欄照抄頁面上的值,再標來源;form_record --from-fill 從它記進看板。"""

    FILL = {'platform': 'Lever', 'fields': [
        {'q': 'Full name', 'value': 'Alex Chen', 'src': 'rz', 'k': None},
        {'q': 'What is your nationality?', 'value': 'Taiwan', 'src': 'bank', 'k': 'nationality'},
        {'q': 'Why this role?', 'value': 'Because A.', 'src': 'bank', 'zh': '因為 A。', 'kind': 'txt',
         'bank_q': '為什麼對這個職位有興趣'},
        {'q': 'Salary', 'value': '', 'src': 'skip', 'why': '他說面談再談'},
    ]}

    def test_page_value_becomes_the_value_except_for_bank_answers(self):
        name, nat, why, sal = fr.fields_from_fill(self.FILL)
        self.assertEqual(name, {'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'})
        self.assertEqual(nat, {'q': 'What is your nationality?', 'src': 'bank', 'k': 'nationality'})   # 值以答案庫為準
        self.assertEqual(why['v'], 'Because A.')
        self.assertEqual((sal['v'], sal['why']), ('', '他說面談再談'))

    def _board(self, d):
        p = os.path.join(d, 'board.html')
        fb = {U1: {'app': 'ship'}, '__ans__': [dict(NAT, at=T)]}
        with open(p, 'w', encoding='utf-8') as f:
            f.write(bd.assemble(':root{--a:1}', '<b id="stat-first">0</b>', '', {'jobs': [{'id': U1}]},
                                json.dumps(fb, ensure_ascii=False), '/*app*/'))
        return p

    def _run(self, board, fill, d):
        import subprocess
        fp = os.path.join(d, 'fill.json')
        with open(fp, 'w', encoding='utf-8') as f:
            json.dump(fill, f, ensure_ascii=False)
        r = subprocess.run([sys.executable, os.path.join(HERE, '..', 'tools', 'form_record.py'),
                            '--from-fill', fp, '--url', U1, '--board', board], capture_output=True, text=True)
        return r, read_fb(board)

    def test_cli_records_once_and_a_changed_answer_needs_a_new_approval(self):
        d = tempfile.mkdtemp(prefix='formrec-fill-')
        try:
            board = self._board(d)
            r, fb = self._run(board, self.FILL, d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual([x['src'] for x in fb[U1]['form']['f']], ['rz', 'bank', 'bank', 'skip'])
            self.assertEqual(fb[U1]['form']['plat'], 'Lever')
            self.assertEqual(fr.validate(fb), [])
            # 他核准了這一版;agent 修改那一輪重寫 fill.json、答案不一樣了 → 核准作廢,送不出去
            bd.set_fb(lambda x: x[U1].__setitem__('approve', {'at': T, 'snap': fr.snapshot(x, U1)}), live=board)
            fill2 = json.loads(json.dumps(self.FILL))
            fill2['fields'][2]['value'] = 'Because B.'
            fill2['fields'][2]['zh'] = '因為 B。'
            r, fb = self._run(board, fill2, d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn('approve', fb[U1])
            self.assertEqual(fr.approval_problem(fb, U1), '還沒確認送出')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cli_says_which_field_is_wrong(self):
        d = tempfile.mkdtemp(prefix='formrec-fill-bad-')
        try:
            board = self._board(d)
            bad = {'platform': 'Lever', 'fields': [
                {'q': 'What is your nationality?', 'value': 'Japan', 'v': 'Japan', 'src': 'bank', 'k': 'nationality'}]}
            r, fb = self._run(board, bad, d)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('nationality', r.stderr)
            self.assertNotIn('form', fb[U1])
        finally:
            shutil.rmtree(d, ignore_errors=True)


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
            self.assertEqual(fr.validate(fb), ['a1 是英文答案卻沒有中文翻譯(zh),他看不懂'])
        with patch.object(fr, 'read_lang', return_value='en'):
            self.assertEqual(fr.validate(fb), [])


if __name__ == '__main__':
    unittest.main()
