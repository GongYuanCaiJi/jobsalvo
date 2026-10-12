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
        bank = {'items': [{'id': 'q1', 't': '自我介紹', 'b': [['s', ['稿']]]}]}
        b = ib.apply(bank, 'link', item_id='q1', job='J', kind='prep')
        self.assertEqual(b['links'], {'J': {'q1': 'prep'}})
        b = ib.apply(b, 'link', item_id='q1', job='J', kind='asked')
        self.assertEqual(b['links'], {'J': {'q1': 'asked'}})
        self.assertEqual(b['items'], bank['items'])
        b = ib.apply(b, 'link', item_id='q1', job='J', kind=None)
        self.assertEqual(b['links'], {})
        with self.assertRaises(ValueError):
            ib.apply(bank, 'link', item_id='nope', job='J', kind='prep')

    def test_deleting_a_question_removes_it_from_every_interview(self):
        import interview_bank as ib
        b = ib.apply({'items': [{'id': 'q1'}, {'id': 'q2'}], 'links': {'J': {'q1': 'prep', 'q2': 'asked'}}}, 'del', item_id='q1')
        self.assertEqual(b['links'], {'J': {'q2': 'asked'}})

    def test_editing_keeps_links_and_never_writes_a_company_on_the_question(self):
        import interview_bank as ib
        b = ib.apply({'items': [{'id': 'q1', 't': 'a'}], 'links': {'J': {'q1': 'prep'}}}, 'put',
                     form={'id': 'q1', 't': 'b', 'stage': 'todo', 'asked': 'Acme'})
        self.assertEqual(b['links'], {'J': {'q1': 'prep'}})
        self.assertNotIn('asked', b['items'][0])

    def test_migrate_moves_old_for_and_asked_onto_links_and_keeps_what_it_cannot_match(self):
        import interview_bank as ib
        jobs = [{'id': 'A', 'target': 'Engineer · Acme'}, {'id': 'B', 'target': 'PM · Nimbus'}, {'id': 'C', 'target': 'Ops · Nimbus'}]
        bank = {'items': [{'id': 'q1', 'for': ['B']}, {'id': 'q2', 'asked': ['acme']}, {'id': 'q3', 'asked': ['Nimbus']}]}
        b, left = ib.migrate(bank, jobs)
        self.assertEqual(b['links'], {'B': {'q1': 'prep'}, 'A': {'q2': 'asked'}})
        self.assertNotIn('for', b['items'][0])
        self.assertNotIn('asked', b['items'][1])
        self.assertEqual(b['items'][2]['asked'], ['Nimbus'])     # 對到兩張不猜
        self.assertEqual(left, [(None, 'Nimbus')])


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
