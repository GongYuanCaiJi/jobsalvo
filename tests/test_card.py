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
        """公司名只有後台這一份(看板拿 label_jobs 算好的 co)。"""
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

    def test_the_board_gets_one_company_name_for_the_same_company_whatever_its_letters(self):
        """看板直接用後台給的卡名和公司名(#343、#339 第五條):大小寫、德文的雙 s、法律字尾不同還是同一家,分在同一組。"""
        got = card.label_jobs([{'id': 'a', 'target': 'Engineer · Straße GmbH'}, {'id': 'b', 'target': 'Designer · STRASSE'}])
        self.assertEqual([(j['name'], j['co']) for j in got],
                         [('Engineer · Straße GmbH', 'Straße'), ('Designer · STRASSE', 'Straße')])

    def test_the_board_gets_the_deadline_day_and_it_is_still_in_time_all_that_day(self):
        """死線由後台認出是哪一天(dl),看板只比今天過了沒:那一天整天都還來得及(以前用 UTC 零點,台灣早上 8 點就算過了)。"""
        def dl(text):
            return card.label_jobs([{'id': 'a', 'target': 'x', 'sum': {'deadline': text}}])[0]['dl']
        self.assertEqual([dl('2026-09-27'), dl('2026/9/27'), dl('截止日期:2026.9.26 前'), dl('盡快'), dl('')],
                         ['2026-09-27', '2026-09-27', '2026-09-26', '', ''])
        self.assertEqual(card.label_jobs([{'id': 'a', 'target': 'x'}])[0]['dl'], '')

    def test_a_long_company_name_with_emoji_is_cut_by_characters(self):
        name = '🚀🚀🚀 星際旅行社 Galactic Tours Taipei'
        self.assertEqual(card.label_jobs([{'id': 'a', 'target': name}])[0]['co'], name[:22])

    def test_the_board_gets_the_source_platform(self):
        jobs = [{'id': 'https://www.104.com.tw/job/8bbbb'}, {'id': 'https://jobs.lever.co/northwind/1'},
                {'id': 'https://job-boards.greenhouse.io/northwind/jobs/1'}, {'id': 'old-card-id'},
                {'id': 'x', 'src': {'site': 'www.linkedin.com'}}]
        self.assertEqual([j['src_plat'] for j in card.label_jobs(jobs)], ['104', 'Lever', 'Greenhouse', '不明', 'LinkedIn'])

    def test_url_identifier_is_shared_twelve_character_prefix(self):
        url = 'https://example.test/jobs/123'
        self.assertEqual(len(card.card_id_from_url(url)), 12)
        self.assertTrue(card.card_id_from_url(url).startswith(card.legacy_id(url)))


if __name__ == '__main__':
    unittest.main()
