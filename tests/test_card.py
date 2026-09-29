import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, TOOLS)
import card


class CardName(unittest.TestCase):
    def test_shared_name_cases(self):
        path = os.path.join(HERE, 'fixtures', 'board-rule-cases.json')
        with open(path, encoding='utf-8') as f:
            cases = json.load(f)['card_names']
        for case in cases:
            with self.subTest(target=case['target']):
                self.assertEqual(card.name(case['target']), case['expected'])

    def test_shared_company_cases(self):
        """跟看板 companyOf 同一張案例表(board_check 的「共用規則案例表」跑頁面那份)。"""
        path = os.path.join(HERE, 'fixtures', 'board-rule-cases.json')
        with open(path, encoding='utf-8') as f:
            cases = json.load(f)
        for case in cases['companies']:
            with self.subTest(target=case['target'], alias=case.get('alias')):
                job = {'id': case.get('id', ''), 'target': case['target']}
                self.assertEqual(card.company(job, case.get('alias') or {}), case['expected'])
        for case in cases['company_same']:
            with self.subTest(a=case['a'], b=case['b']):
                self.assertEqual(card.same_company(case['a'], case['b']), case['same'])

    def test_own_title_words_from_settings(self):
        """內建清單認不出的職稱(咖啡師 Barista),使用者在設定加了就認得,那一段不會被當成公司。"""
        job = {'id': '', 'target': 'Acme · Barista'}
        self.assertEqual(card.company(job), 'Barista')
        self.assertEqual(card.company(job, {}, ['Barista']), 'Acme')
        self.assertEqual(card.company({'id': '', 'target': 'Acme · C++ Dev'}, {}, ['C++ Dev']), 'Acme')

    def test_name_adds_missing_company_once(self):
        self.assertEqual(card.name('Engineer', 'Acme'), 'Engineer · Acme')
        self.assertEqual(card.name('Engineer · Acme', 'Acme'), 'Engineer · Acme')

    def test_name_key_and_replacement_preserve_source_suffix(self):
        target = 'Engineer · Acme（[LinkedIn](https://example.test/job)）'
        self.assertEqual(card.name_key(target), card.name_key('Engineer, Acme'))
        self.assertEqual(card.with_name(target, 'Senior Engineer · Acme'),
                         'Senior Engineer · Acme（[LinkedIn](https://example.test/job)）')

    def test_url_identifier_is_shared_twelve_character_prefix(self):
        url = 'https://example.test/jobs/123'
        self.assertEqual(len(card.card_id_from_url(url)), 12)
        self.assertTrue(card.card_id_from_url(url).startswith(card.legacy_id(url)))


if __name__ == '__main__':
    unittest.main()
