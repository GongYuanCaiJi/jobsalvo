#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台上的履歷(profile_sync):讀回來跟母稿比,只列對不上的;排版差異不算,內容差一個字要抓到。"""
import os, sys, unittest
import tempfile
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
    def test_unselected_option_text_does_not_override_the_observed_profile_selection(self):
        names = {'en/general': '固定平台履歷'}
        custom = {'fields': [{'label': 'Platform resume (required)', 'type': 'select-one',
                              'value': 'custom-1', 'shown': '驗收合成履歷'}],
                  'lines': ['Select a platform resume...', '固定平台履歷', '驗收合成履歷']}
        self.assertEqual(ps.picked(custom, names), set())
        fixed = dict(custom, fields=[{'label': 'Platform resume (required)', 'type': 'select-one',
                                     'value': 'fixed', 'shown': '固定平台履歷'}])
        self.assertEqual(ps.picked(fixed, names), {'en/general'})

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

    def test_account_data_is_checked_loosely_and_only_warns(self):
        want = [('個人名片[0]', '姓名：王小明｜Email：ming.wang@example.test｜手機：0912-345-678｜居住地：台中市西屯區', [])]
        page = {'text': '王小明\n聯絡電話 0912345678\n電子信箱 ming.wan…\n聯絡地址 台中市 西屯區', 'fields': []}
        self.assertEqual(ps.account(page, want), [])        # 信箱截斷、電話沒有連字號、地址拆開都算對
        bad = {'text': '王小明\n聯絡電話 0988000111\n電子信箱 other@x.test\n聯絡地址 高雄市 左營區', 'fields': []}
        self.assertEqual([m.split(':')[0] for m in ps.account(bad, want)], ['Email', '電話', '居住地'])

    def test_account_check_does_not_assume_the_master_layout_or_language(self):
        # 母稿沒有「標籤：值」的聯絡行、全英文:信箱、電話在整份裡找,姓名取第一個標題
        want = [('header', '# Jane Doe\n\njane.doe@example.test | +1 415 555 0134 | Berlin', [])]
        ok = {'text': 'Jane Doe\nPhone\n(415) 555-0134\njane.doe@exa…', 'fields': []}
        self.assertEqual(ps.account(ok, want), [])
        self.assertEqual([m.split(':')[0] for m in ps.account({'text': 'John Roe', 'fields': []}, want)],
                         ['Email', '電話', '姓名'])

    def test_extras_reads_the_shown_text_of_a_dropdown_and_ignores_unset_numbers(self):
        want = [('x', '學歷：示範大學 物理學系', [])]
        fields = [{'label': 'Degree', 'type': 'select-one', 'value': '3', 'shown': '博士'},
                  {'label': 'Height', 'type': 'number', 'value': '0'},
                  {'label': 'Pick one', 'type': 'select-one', 'value': '', 'shown': 'Please select'}]
        fields += [{'label': 'x', 'type': 'select-one', 'value': '請選擇（年）'}, {'label': 'y', 'type': 'radio', 'value': 'false'}]
        self.assertEqual(ps.extras({'fields': fields}, want), ['Degree=博士'])      # 全形占位字、radio 的 false 都不算

    def test_a_confirmed_extra_is_remembered_in_the_registry(self):
        ps.remember('example.test', 'zh', 'a', 'https://example.test/p', 'https://example.test/e')
        self.assertEqual(ps.accept_extra('example.test', 'zh', 'a', '婚姻狀況', '不提供'), '婚姻狀況=不提供')
        self.assertEqual(ps.where('example.test', 'zh', 'a')['accepted'], ['婚姻狀況=不提供'])
        with self.assertRaises(ValueError):
            ps.accept_extra('example.test', 'zh', 'nope', 'x', 'y')

    def test_account_warning_never_blocks_approval(self):
        w = {'read': 'https://x/', 'edit': 'https://x/'}
        warn = {'where': '帳號資料(只提醒)', 'want': '電話:不一致', 'missing': [], 'links': [], 'warn': True}
        delivery = {'method': 'platform_profile', 'profile_kind': 'fixed', 'profile_url': 'https://www.104.com.tw/p'}
        with patch.object(run, 'profile_check', return_value=(w, [warn], '')), \
                patch.object(ps, 'profile_key', return_value='104'), patch.object(run, '_pick', return_value=('zh', 'a')):
            self.assertEqual(run.profile_after('https://www.104.com.tw/job/1', {'delivery': delivery, 'profile': {}}), [])

    def test_a_field_the_master_never_wrote_is_caught(self):
        # 反向:平台頁面上有值、母稿沒有的欄位(身高、婚姻…)要列出來,不能存檔就算過
        want = [('個人名片[0]', '學歷：示範大學 物理學系｜兵役：免役', [])]
        fields = [{'label': '身高', 'type': 'number', 'value': '171'},
                  {'label': '兵役狀況', 'type': 'text', 'value': '免役'},
                  {'label': '手機號碼', 'type': 'tel', 'value': '0912345678'},
                  {'label': '體重', 'type': 'number', 'value': '0'}]
        self.assertEqual(ps.extras({'fields': fields}, want), ['身高=171'])   # 母稿有的、聯絡方式、空值都不算

    def test_a_long_text_field_is_checked_sentence_by_sentence(self):
        # 自傳欄:整段不會原樣出現在母稿(多了標題、換了段),一句一句都找得到才算有來源;多一句就列出來
        want = [('自傳[0]', '碰到說不通的東西，我沒辦法放著不管。別人覺得正常的地方，只要有一點對不上，我就會一直挖到弄懂它。', [])]
        body = '【自傳】\n碰到說不通的東西，我沒辦法放著不管。\n別人覺得正常的地方，只要有一點對不上，我就會一直挖到弄懂它。'
        self.assertEqual(ps.extras({'fields': [{'label': '自傳', 'type': 'textarea', 'value': body}]}, want), [])
        more = ps.extras({'fields': [{'label': '自傳', 'type': 'textarea', 'value': body + '\n我有十年的帶人經驗，管過五十人團隊。'}]}, want)
        self.assertEqual(len(more), 1)
        self.assertIn('這欄有 1 句母稿沒有', more[0])

    def test_headings_and_links_the_master_has_are_a_source_too(self):
        want = [('專案成就[0]', '開源貢獻：修了一個回報了很久的問題，被維護者採用。', ['https://example.test/pull/1'])]
        body = '【專案成就】\n開源貢獻：修了一個回報了很久的問題，被維護者採用。 https://example.test/pull/1'
        self.assertEqual(ps.extras({'fields': [{'label': '自傳', 'type': 'textarea', 'value': body}]}, want), [])

    def test_an_extra_he_confirmed_is_not_asked_again(self):
        want = [('個人名片[0]', '兵役：免役', [])]
        page = {'fields': [{'label': '婚姻狀況', 'type': 'radio', 'value': '不提供'}]}
        self.assertEqual(ps.extras(page, want), ['婚姻狀況=不提供'])
        self.assertEqual(ps.extras(page, want, ['婚姻狀況=不提供']), [])

    def test_platform_is_recognised_from_the_job_url(self):
        self.assertEqual(ps.platform_of('https://www.104.com.tw/job/8bbbb'), '104')
        self.assertIsNone(ps.platform_of('https://attacker.example/path/104.com.tw/profile'))
        self.assertIsNone(ps.platform_of('https://jobs.lever.co/northwind/1'))

    def test_local_fake_platform_uses_its_explicit_platform_query(self):
        self.assertEqual(ps.platform_of('http://127.0.0.1:9000/apply?platform=104.com.tw'), '104')


class Step(unittest.TestCase):
    def test_agent_judges_current_master_without_a_program_generated_missing_list(self):
        w = {'read': 'R', 'edit': 'E'}
        known = {'platform': '104', 'lang': 'zh', 'variant': 'general', 'profile_kind': 'fixed', 'fixed_url': 'R'}
        s = run.profile_step('https://www.104.com.tw/job/1', known, 'M', (w, {'url': 'R', 'text': '目前頁面'}, ''))
        self.assertIn('目前原始履歷 M', s)
        self.assertIn('新增或修改的內容也要同步', s)
        self.assertIn('呈現不同不代表缺漏', s)
        self.assertNotIn('只處理這幾格', s)
        unknown = {'platform': 'cake', 'lang': 'zh', 'variant': 'general', 'profile_kind': 'fixed'}
        self.assertIn('profile.url', run.profile_step('https://www.cake.me/jobs/1', unknown, 'M', (None, [], '')))
        self.assertNotIn('profile.equivalents', s)
        self.assertNotIn('field_mapping', s)


    def test_only_what_changed_in_the_master_since_the_last_passed_sync_is_handed_to_the_agent(self):
        known = {'platform': '104', 'lang': 'zh', 'variant': 'general', 'profile_kind': 'fixed', 'fixed_url': 'R'}
        old = '# 履歷\n求職條件：台北市或遠端｜錄取後一個月內可上班\n專案：Delta\n'
        with tempfile.TemporaryDirectory() as d:
            master = os.path.join(d, 'master.md')
            def step(base):
                with patch.object(ps, 'synced_source', return_value=base), \
                        patch.object(ps, 'where', return_value={'read': 'R', 'edit': 'E'}):
                    return run.profile_step('https://www.104.com.tw/job/1', known, master)
            with open(master, 'w', encoding='utf-8') as f:
                f.write(old)
            same = step(old)                                   # 原稿沒改:整步跳過
            self.assertIn('不用打開、不用核對、不用改平台履歷的文字', same)
            self.assertNotIn('逐段核對', same)
            self.assertIn('不用打開', step(old + '\n\n'))       # 只差空白也算沒改
            with open(master, 'w', encoding='utf-8') as f:
                f.write(old.replace('台北市或遠端｜錄取後一個月內', '台北市、新北市或遠端｜錄取後兩週內'))
            delta = step(old)                                  # 改了一句:只交那一句
            self.assertIn('-求職條件：台北市或遠端｜錄取後一個月內可上班', delta)
            self.assertIn('+求職條件：台北市、新北市或遠端｜錄取後兩週內可上班', delta)
            self.assertNotIn('專案：Delta', delta)
            self.assertNotIn('逐段核對', delta)
            self.assertIn('只到 E', delta)
            self.assertIn('platform_notes', delta)                # 試出來的操作方法記下來,下次照做
            self.assertIn(run.SAME_MEANING, delta)                # 同步和判讀用同一份「一樣」的定義
            self.assertIn(run.SAME_MEANING, run.PROFILE_REVIEW_RULES)
            self.assertIn('逐段核對', step(None))              # 沒記過上次那一版:整份對一遍
            self.assertIn('逐段核對', step('\n'.join(f'第 {i} 行' for i in range(60))))   # 大改:整份對一遍

class Equivalents(unittest.TestCase):
    """平台用自己說法寫的格子:agent 回報「母稿這一格 ＝ 頁面上這幾個字」,程式驗過才記,以後照記下的比。"""
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory(prefix='profile-equivalents-'))
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

    def _master(self, card):
        with open(self.master, 'w', encoding='utf-8') as f:
            f.write('# 候選人\n\n## 個人名片\n\n- ' + card + '\n\n## 自傳\n\n做過很多專案,從頭到尾自己負責。\n')

    def _check(self, reported=None, page=None, **kw):
        with patch.object(ps.cf, 'master', return_value=self.master):
            return ps.check('alpha.example', 'zh', 'general', reader=lambda _u: page or self.page,
                            reported=reported, **kw)

    def test_status_follows_the_master_without_anyone_registering_a_change(self):
        def state():
            with patch.object(ps.cf, 'master', return_value=self.master):
                return [r['state'] for r in ps.status()]
        judged = lambda ok: ps._remember_check('alpha.example', 'zh', 'general', ok)   # agent 判讀完記下(apply_run)
        with patch.object(ps.cf, 'master', return_value=self.master):
            self._master('希望地點：台中市')
            self.assertEqual(state(), ['unchecked'])                  # 登記了、還沒判讀過
            self._check()
            self.assertEqual(state(), ['unchecked'])                  # 舊的字句診斷不算判讀
            judged(True)
            self.assertEqual(state(), ['ok'])
            self._master('希望地點：高雄市')
            self.assertEqual(state(), ['master-changed'])             # 母稿一改,當下就變落後,沒有誰要記得去標
            judged(False)
            self.assertEqual(state(), ['differs'])
            reg = ps.registry()                                       # 舊版字句診斷留下的紀錄(沒有 by)開看板時清掉
            reg['_profile_checks']['alpha.example#zh/general'].pop('by')
            ps._save_registry(reg)
            self.assertEqual(ps.drop_legacy_checks(), 1)
            self.assertEqual(state(), ['unchecked'])

    def test_status_skips_a_platform_whose_master_is_not_set_up(self):
        with patch.object(ps.cf, 'master', return_value=None):
            self.assertEqual(ps.status(), [])

    def test_a_combined_profile_must_hold_every_master_it_names(self):
        other = os.path.join(self.tmp, 'other.md')
        with open(other, 'w', encoding='utf-8') as f:
            f.write('# 候選人\n\n## 個人名片\n\n- 希望地點：台中市\n\n## 專案\n\n交易所體驗金漏洞分析：多帳號把不可提領的點數換成可提領資產。\n')
        self._master('希望地點：台中市')
        page = dict(self.page, text=self.page['text'])
        with patch.object(ps.cf, 'master', side_effect=lambda v, _l: other if v == 'b' else self.master):
            want = ps.expected('alpha.example', 'zh', 'a+b')
            self.assertEqual(len([w for w in want if '希望地點' in w[1]]), 1)      # 兩份都有的同一句只算一次
            miss = [d['where'] for d in ps.diff(page, want)]
            self.assertTrue(any(w.startswith('b:') for w in miss))                 # 只有 b 才有的內容要在頁面上
            full = dict(page, text=page['text'] + '\n交易所體驗金漏洞分析\n多帳號把不可提領的點數換成可提領資產')
            self.assertFalse([d for d in ps.diff(full, want) if d['where'].startswith('b:')])
            self.assertNotEqual(ps._master_fp('zh', 'a+b'), ps._master_fp('zh', 'a'))

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
