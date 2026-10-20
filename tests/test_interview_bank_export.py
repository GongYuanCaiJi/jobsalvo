import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')))
import interview_bank


class InterviewBankExport(unittest.TestCase):
    def test_readable_export_is_one_way_and_replaced_from_canonical_bank(self):
        bank = {
            'cats': ['經驗'],
            'items': [interview_bank.from_form({
                'id': 'q1', 't': '怎麼拆問題？', 'cat': '經驗', 'ask': '描述一個例子',
                'focus': '先確認條件', 'stage': 'ok', 'script': '## 先釐清\n問清楚輸入',
            })],
        }
        with tempfile.TemporaryDirectory(prefix='bank-export-') as home:
            path = interview_bank.export_file(bank, home)
            with open(path, encoding='utf-8') as source:
                content = source.read()
            self.assertIn('# 面試題庫', content)
            self.assertIn('怎麼拆問題？', content)
            self.assertIn('問清楚輸入', content)
            mtime = os.stat(path).st_mtime_ns
            interview_bank.export_file(bank, home)
            self.assertEqual(os.stat(path).st_mtime_ns, mtime)
            with open(path, 'w', encoding='utf-8') as target:
                target.write('edited by hand')
            interview_bank.export_file(bank, home)
            with open(path, encoding='utf-8') as source:
                self.assertEqual(source.read(), content)
            self.assertEqual(bank['items'][0]['t'], '怎麼拆問題？')


if __name__ == '__main__':
    unittest.main()


class InterviewLinksTest(unittest.TestCase):
    """題目上不記公司;哪一場要準備、被問過只記在 bank['links']。"""

    def test_link_sets_changes_and_removes_without_touching_the_question(self):
        import interview_bank as ib
        G = {'k': 'prep', 'src': 'guess', 'cite': '', 'quote': '', 'why': ''}
        bank = {'items': [{'id': 'q1', 't': '自我介紹', 'b': [['s', ['稿']]]}]}
        b = ib.apply(bank, 'link', item_id='q1', job='J', kind='prep')
        self.assertEqual(b['links'], {'J': {'q1': G}})
        b = ib.apply(b, 'link', item_id='q1', job='J', kind='asked')
        self.assertEqual(b['links'], {'J': {'q1': {'k': 'asked', 'src': 'me', 'cite': '', 'quote': '', 'why': ''}}})
        self.assertEqual(b['items'], bank['items'])
        b = ib.apply(b, 'link', item_id='q1', job='J', kind=None)
        self.assertEqual(b['links'], {})
        with self.assertRaises(ValueError):
            ib.apply(bank, 'link', item_id='nope', job='J', kind='prep')

    def test_deleting_a_question_removes_it_from_every_interview(self):
        import interview_bank as ib
        b = ib.apply({'items': [{'id': 'q1'}, {'id': 'q2'}], 'links': {'J': {'q1': 'prep', 'q2': 'asked'}}}, 'del', item_id='q1')
        self.assertEqual(list(b['links']['J']), ['q2'])
        self.assertEqual(b['links']['J']['q2']['src'], 'me')

    def test_editing_keeps_links_and_never_writes_a_company_on_the_question(self):
        import interview_bank as ib
        b = ib.apply({'items': [{'id': 'q1', 't': 'a'}], 'links': {'J': {'q1': {'k': 'prep', 'src': 'this', 'cite': 'https://x', 'quote': '原文', 'why': ''}}}}, 'put',
                     form={'id': 'q1', 't': 'b', 'stage': 'todo', 'asked': 'Acme'})
        self.assertEqual(b['links'], {'J': {'q1': {'k': 'prep', 'src': 'this', 'cite': 'https://x', 'quote': '原文', 'why': ''}}})
        self.assertNotIn('asked', b['items'][0])

    def test_old_string_links_read_as_structured_sources(self):
        import interview_bank as ib
        self.assertEqual(ib.norm_link('asked'), {'k': 'asked', 'src': 'me', 'cite': '', 'quote': '', 'why': ''})
        self.assertEqual(ib.norm_link({'k': 'prep', 'note': '職缺寫了微調'})['why'], '職缺寫了微調')

    def test_rules_selftest(self):
        import interview_bank as ib
        ib.selftest()


class StageTest(unittest.TestCase):
    def test_stage_changes_only_the_stage(self):
        import interview_bank as ib
        bank = {'items': [{'id': 'q1', 't': 't', 'st': 'wip', 'raw': True, 'b': [['s', ['稿']]]}]}
        for k, st, raw in (('ok', 'ok', True), ('first', 'todo', True), ('todo', 'todo', False), ('wip', 'wip', True)):
            it = ib.apply(bank, 'stage', item_id='q1', kind=k)['items'][0]
            self.assertEqual((it['st'], it['raw'], it['b']), (st, raw, [['s', ['稿']]]))

    def test_no_script_cannot_be_final(self):
        import interview_bank as ib
        with self.assertRaises(ValueError):
            ib.apply({'items': [{'id': 'q1', 'b': []}]}, 'stage', item_id='q1', kind='ok')
