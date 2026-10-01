#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台上的履歷(profile_sync):讀回來跟母稿比,只列對不上的;排版差異不算,內容差一個字要抓到。"""
import os, sys, unittest
import tempfile, shutil
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import profile_sync as ps     # noqa: E402
import apply_run as run       # noqa: E402

WANT = [('projects[0]', '訂單對帳自動化(Python|附件):每日對帳從人工兩小時改成排程五分鐘;錯帳率由 3% 降至接近 0%。',
         ['https://github.com/x/pull/1']),
        ('education[0].start', '2020/09', [])]
PAGE = {'text': '訂單對帳自動化（Python｜附件）\n\n每日對帳從人工兩小時改成排程五分鐘；錯帳率由 3% 降至接近 0%。\n2020/9~2026/6',
        'links': ['https://github.com/x/pull/1/']}


class Diff(unittest.TestCase):
    def test_layout_differences_do_not_count(self):
        self.assertEqual(ps.diff(PAGE, WANT), [])      # 全形半形、分行、月份補零、連結結尾斜線

    def test_a_changed_number_or_missing_link_is_caught(self):
        ds = ps.diff(dict(PAGE, text=PAGE['text'].replace('3%', '8%'), links=[]), WANT)
        self.assertEqual([d['where'] for d in ds], ['projects[0]'])
        self.assertTrue(ds[0]['missing'])
        self.assertEqual(ds[0]['links'], ['https://github.com/x/pull/1'])

    def test_label_value_line_is_compared_cell_by_cell_and_contact_is_skipped(self):
        # 母稿把幾格擠在一行,平台一格一格放、標籤也不同:每格的值都在頁面上就算對;姓名、Email、電話不比
        want = [('個人名片[0]', '姓名：王小明｜Email：ming@example.test｜手機：0912-345-678｜兵役：免役｜居住地：台中市西屯區', []),
                ('個人名片[1]', 'GitHub：[github.com/ming](https://github.com/ming)｜學歷：示範大學 物理學系', ['https://github.com/ming'])]
        page = {'text': '基本資料\n兵役狀況\n免役\n現居地\n台中市西屯區\n學歷\n示範大學\n物理學系',
                'links': ['https://github.com/ming']}
        self.assertEqual(ps.diff(page, ps.md_sections('## 個人名片\n\n- ' + want[0][1] + '\n- ' + want[1][1])), [])
        moved = dict(page, text=page['text'].replace('西屯區', '南屯區'))
        ds = ps.diff(moved, ps.md_sections('## 個人名片\n\n- ' + want[0][1]))
        self.assertEqual([d['missing'] for d in ds], [['居住地：台中市西屯區']])   # 值改了照樣抓到,只列那一格

    def test_platform_is_recognised_from_the_job_url(self):
        self.assertEqual(ps.platform_of('https://www.104.com.tw/job/8bbbb'), '104')
        self.assertIsNone(ps.platform_of('https://attacker.example/path/104.com.tw/profile'))
        self.assertIsNone(ps.platform_of('https://jobs.lever.co/northwind/1'))

    def test_local_fake_platform_uses_its_explicit_platform_query(self):
        self.assertEqual(ps.platform_of('http://127.0.0.1:9000/apply?platform=104.com.tw'), '104')


class Step(unittest.TestCase):
    def test_agent_is_told_only_the_sections_that_differ(self):
        w = {'read': 'R', 'edit': 'E'}
        known = {'platform': '104', 'lang': 'zh', 'variant': 'general', 'profile_kind': 'fixed', 'fixed_url': 'R'}
        self.assertIn('內容不用動', run.profile_step('https://www.104.com.tw/job/1', known, 'M', (w, [], '')))
        s = run.profile_step('https://www.104.com.tw/job/1', known, 'M', (w, ps.diff({'text': '', 'links': []}, WANT[:1]), ''))
        self.assertIn('只處理這幾格', s)
        self.assertIn('projects[0]', s)
        unknown = {'platform': 'cake', 'lang': 'zh', 'variant': 'general', 'profile_kind': 'fixed'}
        self.assertIn('profile.url', run.profile_step('https://www.cake.me/jobs/1', unknown, 'M', (None, [], '')))
        # 有差異時告訴它:平台只是用自己的說法寫的,回報 equivalents 就好,不用改平台;格式寫明,不用讀原始碼
        self.assertIn('profile.equivalents', s)
        self.assertIn('not_shown', s)
        self.assertIn('不用去讀程式原始碼', s)
        self.assertNotIn('field_mapping', s)


class Equivalents(unittest.TestCase):
    """平台用自己說法寫的格子:agent 回報「母稿這一格 ＝ 頁面上這幾個字」,程式驗過才記,以後照記下的比。"""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='profile-equivalents-')
        self.old_reg = ps.REG
        ps.REG = os.path.join(self.tmp, 'profiles.json')
        self.master = os.path.join(self.tmp, 'resume.md')
        self._master('可上班日：錄取後一個月內可上班｜兵役：免役｜希望地點：台中市')
        self.url = 'https://alpha.example/profile'
        self.page = {'url': self.url, 'links': [],
                     'text': '求職條件\n可上班日\n一個月內\n希望地點\n台中市\n自傳\n做過很多專案,從頭到尾自己負責。'}
        ps.remember('alpha.example', 'zh', 'general', self.url)

    def tearDown(self):
        ps.REG = self.old_reg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _master(self, card):
        with open(self.master, 'w', encoding='utf-8') as f:
            f.write('# 候選人\n\n## 個人名片\n\n- ' + card + '\n\n## 自傳\n\n做過很多專案,從頭到尾自己負責。\n')

    def _check(self, reported=None, page=None, **kw):
        with patch.object(ps.cf, 'master', return_value=self.master):
            return ps.check('alpha.example', 'zh', 'general', reader=lambda _u: page or self.page,
                            reported=reported, **kw)

    def test_unreadable_master_is_a_problem_not_a_match(self):
        # 以前母稿讀不出來就當成沒有要比的段落:什麼都沒比,結果卻是「跟母稿對得上」
        pdf = os.path.join(self.tmp, 'resume.pdf')
        with open(pdf, 'wb') as f:
            f.write(b'not a pdf')
        with patch.object(ps.cf, 'master', return_value=pdf), \
             patch('settings_api.pdf_text', side_effect=ValueError('PDF 壞掉了')):
            _w, ds, problem = ps.check('alpha.example', 'zh', 'general', reader=lambda _u: self.page)
        self.assertEqual(ds, [])
        self.assertIn('母稿', problem)
        self.assertIn('PDF 壞掉了', problem)

    def test_platform_wording_is_accepted_once_and_reused(self):
        _w, ds, _p = self._check()
        self.assertEqual(ds[0]['missing'], ['可上班日：錄取後一個月內可上班', '兵役：免役'])
        _w, ds, _p = self._check([
            {'master': '可上班日：錄取後一個月內可上班', 'platform': '一個月內', 'why': '104 的下拉選項'},
            {'master': '兵役：免役', 'not_shown': True, 'why': '預覽頁沒有兵役'},
        ])
        self.assertEqual(ds, [])
        _w, ds, _p = self._check()                                   # 下一次不用再回報
        self.assertEqual(ds, [])
        saved = ps.where('alpha.example', 'zh', 'general')['equivalents']
        self.assertEqual({v['master'] for v in saved.values()}, {'可上班日：錄取後一個月內可上班', '兵役：免役'})

    def test_wording_that_is_not_on_the_page_is_refused(self):
        _w, ds, _p = self._check([{'master': '可上班日：錄取後一個月內可上班', 'platform': '隨時可上班'}])
        self.assertIn('頁面上找不到這幾個字', ' '.join(r for d in ds for r in d.get('rejected', [])))
        self.assertIn('可上班日：錄取後一個月內可上班', [m for d in ds for m in d['missing']])
        self.assertIn('上一輪回報的說法沒收下', ps.describe(ds))          # 下一輪的 agent 看得到為什麼

    def test_long_content_cannot_be_waved_through_as_not_shown(self):
        self._master('兵役：免役')
        page = dict(self.page, text='個人名片\n兵役\n免役')
        _w, ds, _p = self._check([{'master': '做過很多專案,從頭到尾自己負責', 'not_shown': True}], page=page)
        self.assertIn('太長,不能說平台不顯示', ' '.join(r for d in ds for r in d.get('rejected', [])))

    def test_an_unlabeled_cell_split_from_a_line_can_be_not_shown(self):
        # 「求職條件：台北市｜全職｜應屆畢業」的「應屆畢業」沒有標籤,但它是一行裡分出來的短格子,可以說平台不顯示
        self._master('可上班日：錄取後一個月內可上班｜兵役：免役｜應屆畢業')
        _w, ds, _p = self._check([{'master': '可上班日：錄取後一個月內可上班', 'platform': '一個月內'},
                                  {'master': '兵役：免役', 'not_shown': True},
                                  {'master': '應屆畢業', 'not_shown': True}])
        self.assertEqual(ds, [])

    def test_a_cell_that_already_matches_is_ignored_not_blessed(self):
        # 回報了本來就對得上的格子(例如這一輪剛改好的):不記、也不算錯,不能擋住這張卡
        _w, ds, _p = self._check([{'master': '希望地點：台中市', 'platform': '台中市'}])
        self.assertEqual([m for d in ds for m in d['missing']], ['可上班日：錄取後一個月內可上班', '兵役：免役'])
        self.assertFalse([r for d in ds for r in d.get('rejected', [])])
        self.assertFalse(ps.where('alpha.example', 'zh', 'general').get('equivalents'))

    def test_whole_line_is_matched_to_its_only_missing_cell(self):
        # agent 常把整行貼成 master:整行裡只剩一格比不過就對到那一格;好幾格就請它一格一筆
        line = '可上班日：錄取後一個月內可上班｜兵役：免役｜希望地點：台中市'
        _w, ds, _p = self._check([{'master': line, 'platform': '一個月內'}])
        self.assertIn('一格一筆', ' '.join(r for d in ds for r in d.get('rejected', [])))
        _w, ds, _p = self._check([{'master': '兵役：免役', 'not_shown': True}, {'master': line, 'platform': '一個月內'}])
        self.assertEqual(ds, [])

    def test_editing_the_master_or_the_platform_brings_the_difference_back(self):
        self._check([{'master': '可上班日：錄取後一個月內可上班', 'platform': '一個月內'},
                     {'master': '兵役：免役', 'not_shown': True}])
        self._master('可上班日：錄取後兩週內可上班｜兵役：免役｜希望地點：台中市')         # 改母稿:那一格要重新對
        _w, ds, _p = self._check()
        self.assertEqual([m for d in ds for m in d['missing']], ['可上班日：錄取後兩週內可上班'])
        self._master('可上班日：錄取後一個月內可上班｜兵役：免役｜希望地點：台中市')
        _w, ds, _p = self._check(page=dict(self.page, text=self.page['text'].replace('一個月內', '隨時')))
        self.assertEqual([m for d in ds for m in d['missing']], ['可上班日：錄取後一個月內可上班'])   # 平台被改掉

    def test_a_changed_sentence_is_still_caught(self):
        _w, ds, _p = self._check(page=dict(self.page, text=self.page['text'].replace('自己負責', '別人負責')))
        self.assertIn('自傳[1]', [d['where'] for d in ds])

    def test_loopback_fake_platform_is_opt_in(self):
        url = 'http://127.0.0.1:8765/profile?platform=104.com.tw'
        ps.remember('104', 'zh', 'general', url)
        page = dict(self.page, url=url)
        with patch.object(ps.cf, 'master', return_value=self.master):
            _w, _ds, problem = ps.check('104', 'zh', 'general',
                                        reader=lambda _u: self.fail('normal runs must reject loopback platforms'))
            self.assertIn('讀取網址不安全', problem)
            _w, _ds, problem = ps.check('104', 'zh', 'general', reader=lambda _u: page, test_allow_local=True)
            self.assertEqual(problem, '')

    def test_redirect_to_another_platform_is_refused(self):
        _w, _ds, problem = self._check(page=dict(self.page, url='https://attacker.example/profile'))
        self.assertIn('跳到不屬於原平台', problem)

    def test_application_history_page_must_belong_to_the_platform(self):
        self.assertFalse(ps.remember_application_history('alpha.example', 'https://attacker.example/applications'))
        self.assertFalse(ps.remember_application_history('alpha.example', 'javascript:alert(1)'))
        self.assertTrue(ps.remember_application_history('alpha.example', 'https://alpha.example/applications'))
        self.assertEqual(ps.application_record_pages(),
                         [{'platform': 'alpha.example', 'url': 'https://alpha.example/applications'}])


if __name__ == '__main__':
    unittest.main()
