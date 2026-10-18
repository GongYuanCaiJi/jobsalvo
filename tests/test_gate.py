#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安檢門(#316):agent 交回來的事實只從一個入口進到程式,程式自己看到的真相核對過才用。

會說謊的假 agent:一張全部照實寫的交件單收下;交件單上登記要核對的每一格各故意填錯一次(含把固定版標成客製版),
每一次都擋下,原因寫出哪一格、agent 說什麼、實際是什麼。另有結構測試:程式裡繞過安檢門直接讀交件單、
指示裡叫 agent 寫、卻沒登記核對方式的格子,測試就失敗。"""
import ast
import copy
import dataclasses
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import gate                   # noqa: E402
import profile_sync as ps     # noqa: E402
from gate_cells import Cell   # noqa: E402
from numbered import ROW, Accept, Cited, Numbered  # noqa: E402
from unittest import mock     # noqa: E402

TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
URL = 'https://www.104.com.tw/job/abc'
FORM = URL + '/apply'
MINE = 'https://pda.104.com.tw/profile/preview?vno=1'
OTHER = 'https://pda.104.com.tw/profile/preview?vno=2'
PAGE = {'url': FORM, 'title': '資料工程師 - 好公司', 'lines': ['資料工程師', '好公司', '選擇履歷', '中文履歷'],
        'fields': [{'label': '姓名', 'name': 'name', 'type': 'text', 'value': '王小明'},
                   {'label': '自我推薦信', 'name': 'cover', 'type': 'textarea', 'value': '我擅長資料管線。'}]}
DECISION = {'platform': '104', 'lang': 'zh', 'variant': 'general', 'profile_kind': 'fixed',
            'fixed_url': MINE, 'name': '中文履歷'}
HONEST = {
    'url': URL, 'platform': '104', 'tab_id': '7', 'handoff': True, 'tab_url': FORM,
    'posting': {'title': '資料工程師', 'company': '好公司', 'same_job': True},
    'profile': {'url': MINE, 'edit': 'https://pda.104.com.tw/profile/edit?vno=1', 'name': '中文履歷',
                'application_history_url': 'https://pda.104.com.tw/applyRecord', 'needed': True, 'updated': [],
                'note': ''},
    'delivery': {'method': 'platform_profile'},
    'uploaded': [], 'fields': [{'q': '姓名', 'value': '王小明', 'src': 'rz'},
                               {'q': '自我推薦信', 'value': '我擅長資料管線。', 'src': 'bank', 'k': 'a1'}],
    'blank_for_him': [], 'problems': [], 'notes': [], 'platform_notes': [], 'platform_notes_remove': [],
    'submitted': False,
}
# 申請表直接上傳的那一種(Greenhouse、Lever):頁面上沒有平台履歷,上傳欄裡有檔
DIRECT_PAGE = dict(PAGE, lines=['資料工程師', '好公司'],
                   fields=PAGE['fields'] + [{'label': 'Resume', 'name': 'resume', 'type': 'file', 'value': ['cv.pdf']}])
DIRECT = dict(HONEST, delivery={'method': 'direct_upload'}, uploaded=['cv.pdf'])
# 每一格的謊話 → 擋下的原因裡要看得到的字(格子名、它說的、實際的)
LIES = {
    'url': ('https://www.104.com.tw/job/other', ['職缺網址', 'job/other', URL]),
    'tab_id': ('8', ['分頁', '8', '分頁 7']),
    'handoff': (False, ['交接分頁', 'false']),
    'tab_url': (URL + '/other', ['分頁網址', URL + '/other', FORM]),
    'posting.title': ('廚師', ['頁面上的職稱', '廚師', '頁面上沒有']),
    'posting.company': ('別家公司', ['頁面上的公司', '別家公司', '頁面上沒有']),
    'posting.same_job': (False, ['不是這張卡的職缺']),
    'profile.url': (OTHER, ['平台履歷全文頁', 'vno=2', MINE]),
    'profile.edit': ('https://evil.example/edit', ['平台履歷編輯頁', 'evil.example', '不是這個平台']),
    'profile.name': ('English Resume', ['平台履歷名稱', 'English Resume', '中文履歷']),
    'delivery.method': ('direct_upload', ['投遞方式', 'direct_upload', '中文履歷']),
    'delivery.profile_kind': ('custom', ['固定版還是客製版', 'custom', 'fixed']),
    'delivery.profile_url': (OTHER, ['用的平台履歷', 'vno=2', MINE]),
    'uploaded': (['ghost.pdf'], ['上傳欄裡沒有 ghost.pdf'], 'direct'),   # 申請表直接上傳時才看它
    'fields': ([{'q': '姓名', 'value': '李大華', 'src': 'rz'}], ['姓名', '李大華']),
    'submitted': (True, ['已送出', 'true', 'reason']),
    'confirm_url': ('https://www.104.com.tw/thanks', ['確認頁網址', 'thanks', FORM]),
    'confirm_text': ('應徵成功', ['確認頁的字', '應徵成功', '頁面上沒有這句話']),
    'unmet': ([{'quote': '履歷請控制在兩頁以內', 'file': 'cv.pdf', 'act': 'adapt'}],
              ['跟職缺要求不符的地方', '頁面上沒有這句要求']),
    'problems': (['需要登入'], ['卡住', '需要登入']),
}
# 不是申請表的一部分、只給之後參考的格子:對不上就不收(不進 facts),不擋這一輪
DROPPED = {'profile.application_history_url': 'https://evil.example/h'}
# 核對要讀別的東西(平台履歷頁、下載回來的檔)的那幾格:謊話在那一支核對函式自己的測試裡擋(模組.類別.測試;
# 找不到那一條測試,這支測試就失敗,不會變成一行沒人管的註解)
_PA = 'test_apply_profile_attachments.ProfileAttachments.'
ELSEWHERE = {
    'profile_choice:tab_id': 'test_profile_choice.ProfileChoice.test_tab_outside_the_card_space_is_blocked',
    'uploaded_files': _PA + 'test_direct_upload_with_a_different_custom_file_is_a_problem',
    'uploaded_from': _PA + 'test_platform_without_readback_rejects_a_file_outside_the_card_folder',
    'upload_readback': _PA + 'test_platform_without_readback_checks_the_local_file_put_into_the_form',
    'submit:submitted': 'test_agent_workflow_boundaries.Responsibilities.test_send_result_is_not_inferred_from_success_words_or_retained_form',
}
THANKS = {'url': 'https://www.104.com.tw/apply/done', 'title': '應徵成功', 'lines': ['應徵成功', '謝謝'], 'fields': []}
SUBMIT_HONEST = {'submitted': True, 'clicked': True, 'reason': '本次申請已收到', 'confirm_url': THANKS['url'], 'confirm_text': '應徵成功',
                 'tab_id': '7', 'problems': []}
SUBMIT_LIES = {
    'confirm_url': ('https://www.104.com.tw/other', ['確認頁網址', '/other', THANKS['url']]),
    'confirm_text': ('Application received', ['確認頁的字', 'Application received', '頁面上沒有這句話']),
    'tab_id': ('9', ['分頁', '9', '分頁 7']),
}


# ---- 其他 7 種流程(#317):真相是程式交給 agent 的原文;一列一列的交件單,謊話填在第一列 ----
A, B = 'https://ex.test/job/a', 'https://ex.test/job/b'


def numbered_of(*blocks, mark=''):
    """程式印進指示的編號材料:(種類, 哪一列, 原文) 一行一段;mark 加在每段前面(test_cell_functions_never_see_handed_text)。"""
    numbered = Numbered()
    for kind, owner, text in blocks:
        numbered.add([mark + line for line in text.splitlines()], kind, owner)
    return numbered


FLOWS = {
    # 刊登日期:程式從頁面挑出跟日期有關的那幾行,編號交給它;它引那幾行的編號
    'posted_at': dict(
        honest={'dates': [{'url': A, 'posted_at': '2026-09-07', 'source': [1]}],
                'inaccessible': [{'url': B, 'reason': '原文沒有刊登日期', 'need': ''}]},
        numbered=lambda mark='': numbered_of(('date', A, 'Posted on September 7, 2026\nUpdated 3 days ago'),
                                             ('date', B, 'Apply by Oct 1'), mark=mark),
        truth=lambda: gate.Truth(handed={A, B}, fetched_on={A: '2026-09-20', B: '2026-09-20'}),
        lies={'dates.url': ('https://ex.test/job/other', ['職缺網址', 'job/other', '這一輪沒有交給它']),
              'dates.posted_at': ('2026-02-31', ['刊登日期', '2026-02-31', 'YYYY-MM-DD']),
              'inaccessible.url': ('https://ex.test/job/other', ['職缺網址', 'job/other', '這一輪沒有交給它'])},
        judged={'dates.posted_at': '2026-09-07'}),
    # 職缺關了沒:程式抓好的頁面原文編號交給它判斷;關了沒是 agent 判斷,它引那一頁講關了的那一段的編號
    'link_status': dict(
        honest={'jobs': [{'id': 'J1', 'status': 'closed', 'quote': [2], 'reason': '徵到人了'},
                         {'id': 'J2', 'status': 'live', 'reason': ''}]},
        numbered=lambda mark='': numbered_of(('text', 'J1', 'Data Engineer.\nThis position has been filled.'),
                                             ('text', 'J2', 'Apply now'), mark=mark),
        truth=lambda: gate.Truth(handed={'J1', 'J2'}),
        lies={'jobs.id': ('J9', ['職缺代號', 'J9', '這一輪沒有交給它']),
              'jobs.status': ('gone', ['職缺還在不在', 'gone', 'live、closed、uncertain'])},
        judged={'jobs.status': 'closed'}),
    # 分類建議:整份都是建議(他看過才套用);比對規則要是寫得出來的正規表示式
    'suggest_cats': dict(
        honest={'categories': [{'name': '工程', 'icon': '⚙', 'match': 'engineer|工程師'},
                               {'name': '資料', 'icon': '📊', 'match': 'data|資料'}],
                'tags': [{'name': '遠端', 'match': 'remote|遠端'}], 'why': '照履歷分'},
        truth=lambda: gate.Truth(),
        lies={'categories.match': ('engineer(|工程師', ['比對規則', 'engineer(|工程師', '不是正規表示式']),
              'tags.match': ('remote[', ['比對規則', 'remote[', '不是正規表示式'])},
        judged={'categories.name': '工程', 'tags.name': '遠端'}),
    # 客製版:其他卡還沒處理的回饋交給它,整理出至少兩張卡都提到的問題
    'customize': dict(
        honest={'reports': [{'issue': '成果不夠明確', 'recommendation': '檢查成果段落的 skill',
                             'feedback_ids': ['f1', 'f2'], 'occurrences': 2}]},
        truth=lambda: gate.Truth(feedback={'f1': {'url': A, 'text': '成果要更明確'},
                                           'f2': {'url': B, 'text': '成果寫具體一點'},
                                           'f3': {'url': A, 'text': '作品集太長'}}),
        lies={'reports.feedback_ids': (['f1', 'f9'], ['歸進來的回饋', 'f9', '這一輪沒給它']),
              'reports.occurrences': (5, ['提到幾次', '5', '2(程式照它列的回饋數的)'])},
        judged={'reports.issue': '成果不夠明確'}),
    # 準備履歷:程式抓好的 JD 原文(一輪好幾張卡,一起編號)交給它挑履歷;JD 是哪種語言是 agent 判斷(程式不猜),只核對寫法
    'prepare': dict(
        honest={'resume': 'general', 'lang': 'zh', 'why': 'JD 要資料管線', 'content_problem': False,
                'real_title': {'text': '資深資料工程師', 'cite': [1]}},
        variants={'closed': {'skip': True, 'reason': '職缺已關:頁面寫已額滿', 'quote': [3]},
                  'old': {'variant': 'general', 'lang': 'zh', 'why': '舊的寫法'}},
        numbered=lambda mark='': numbered_of(('jd', A, '資深資料工程師\n好公司 負責資料管線。\n本職缺已額滿。'),
                                             ('jd', B, '後端工程師'), mark=mark),
        truth=lambda: gate.Truth(A, resumes=[{'id': 'general', 'files': {'zh': 'r/zh.pdf', 'en': 'r/en.pdf'}}]),
        lies={'resume': ('secret', ['挑的履歷', 'secret', '不是勾選']),
              'variant': ('secret', ['挑的履歷(舊寫法)', 'secret', '不是勾選']),
              'lang': ('klingon', ['語言', 'klingon', '只能是']),
              'reason': ('抓不到 JD:讀不到', ['跳過的原因', '抓不到 JD', '程式有給它 JD 原文'], 'closed'),
              'skip': (True, ['跳過', '沒寫原因'])},
        judged={'resume': 'general', 'lang': 'zh', 'content_problem': False, 'variant': ('general', 'old'),
                'skip': (True, 'closed')}),
    # 找缺:agent 自己上網找的候選;程式之後抓回每一頁
    'research_search': dict(
        honest={'candidates': [{'url': A, 'title': '資料工程師', 'company': '好公司', 'why': '做資料管線',
                                'via': '搜尋', 'angle': '資料管線'}]},
        truth=lambda: gate.Truth(),
        lies={'candidates.url': ('https://ex.test/jobs/search?keyword=data', ['職缺網址', 'keyword=data',
                                                                              '不是單一職缺頁'])},
        judged={'candidates.angle': '資料管線'}),
    # 找缺的判斷:程式抓回的 JD 原文、程式提醒(各張自己的)和偏好筆記、相似舊卡(整批共用)編號交給它判斷
    'research_judge': dict(
        honest={'jobs': [{'id': 'J1', 'title': {'text': '資料工程師', 'cite': [2]}, 'company': {'text': '好公司', 'cite': [3]},
                          'keep': True, 'fit': 4,
                          'cite': ['c1'], 'why': '他喜歡資料管線',
                          'risk': {'kind': '', 'why': '', 'cite': []},
                          'reasons': [{'text': '他說喜歡管線', 'citation': [1], 'basis': '使用者原話'},
                                      {'text': '要做管線', 'citation': [3], 'basis': 'JD 原文'}],
                          'resume': 'general', 'lang': 'zh', 'pick_why': '中文 JD', 'cat': '工程',
                          'terms': {'salary_month': 50000, 'salary_quote': [4], 'exp_years': None,
                                    'exp_quote': [], 'location': '看不出', 'location_quote': []},
                          'card': {'fit': '管線經驗對上', 'co': '做電商', 'loc': '台北', 'deadline': '2026-10-31',
                                   'salary': '月薪 50,000 起', 'bar': '三年', 'posted': '無', 'ammo': '直投',
                                   'quote': [4, 5]}}]},
        variants={'ghost': {'jobs': [{'id': 'J1', 'keep': False,
                                      'risk': {'kind': 'ghost', 'why': '刊登已經 200 天', 'cite': [6]}}]},
                  'old': {'jobs': [{'id': 'J1', 'variant': 'general', 'lang': 'zh', 'pick_why': '舊的寫法'}]},
                  'missing': {'jobs': [{'id': 'J1', 'missing': '網站首頁,沒有職缺內容', 'keep': False}]},
                  'terms': {'jobs': [{'id': 'J1', 'terms': {'exp_years': 0, 'exp_quote': [3],
                                                            'location': '符合', 'location_quote': [3]}}]}},
        numbered=lambda mark='': numbered_of(
            ('note', None, '很喜歡資料管線'),
            ('jd', 'J1', '資料工程師\n好公司 負責資料管線。\n月薪 50,000 起。\n應徵截止 2026/10/31。'),
            ('flag', 'J1', '刊登已經 200 天(超過 90 天):可能是幽靈職缺'), ('jd', 'J2', '後端工程師'),
            ('old', None, '- 資料平台工程師｜當時表態:喜歡'), mark=mark),
        truth=lambda: gate.Truth(handed={'J1', 'J2'}, cites={'J1': {'c1', 'c2'}},
                                 resumes=[{'id': 'general', 'files': {'zh': 'r/zh.pdf'}}]),
        lies={'jobs.id': ('J7', ['職缺代號', 'J7', '這一輪沒有交給它']),
              'jobs.cite': (['c9'], ['引用的舊卡', 'c9', '程式沒附給它']),
              'jobs.reasons': ([{'text': '他說過', 'citation': [], 'basis': '亂寫'}], ['理由的引用', '亂寫', '根據類型']),
              'jobs.resume': ('secret', ['挑的履歷', 'secret', '不是勾選']),
              'jobs.variant': ('secret', ['挑的履歷(舊寫法)', 'secret', '不是勾選']),
              'jobs.lang': ('klingon', ['語言', 'klingon', '只能是']),
              'jobs.risk': ({'kind': 'scam', 'why': '要先匯保證金', 'cite': []}, ['詐騙或幽靈缺', '保證金', '沒有引']),
              'jobs.card': ({'deadline': '2026-12-01', 'salary': '月薪 90,000', 'quote': []},
                            ['卡片摘要', '月薪 90,000', 'quote']),
              'jobs.terms': ({'salary_month': 90000, 'salary_quote': []},
                             ['這輪條件讀到的值', 'salary_month', '編號'])},
        judged={'jobs.missing': ('網站首頁,沒有職缺內容', 'missing'), 'jobs.keep': True, 'jobs.fit': 4, 'jobs.cat': '工程', 'jobs.resume': 'general', 'jobs.lang': 'zh',
                'jobs.variant': ('general', 'old'),
                'jobs.risk': ({'kind': 'ghost', 'why': '刊登已經 200 天',
                               'cite': '刊登已經 200 天(超過 90 天):可能是幽靈職缺'}, 'ghost'),
                'jobs.card': {'fit': '管線經驗對上', 'co': '做電商', 'loc': '台北', 'deadline': '2026-10-31',
                              'salary': '月薪 50,000 起', 'bar': '三年', 'posted': '無', 'ammo': '直投',
                              'quote': '月薪 50,000 起。\n應徵截止 2026/10/31。'}}),
    # 查應徵進度:程式複製的信和平台應徵紀錄全文交給它;信算哪一種、要他做的事是 agent 判斷
    'reply': dict(
        honest={'checked': [A],
                'findings': [{'url': A, 'source_ref': 'email:thr001', 'source': 'Gmail', 'date': '2026-09-03',
                              'subject': {'text': '面試邀請', 'cite': [1]}, 'summary': '邀請面試', 'kind': 'interview',
                              'link': 'https://mail.google.com/mail/u/0/#all/thr001', 'quote': [3],
                              'reason': [], 'todo': '回覆可面談時段'}],
                'job_ids': [{'platform': '104', 'at': [5], 'applied_at': '2026-09-01', 'title': '資料工程師'}],
                'inaccessible': [{'source': 'LinkedIn', 'reason': '要登入', 'need': '登入後重查', 'jobs': [B]}]},
        variants={'reject': {'checked': [A], 'findings': [
            {'url': A, 'source_ref': 'email:thr001', 'source': 'Gmail', 'date': '2026-09-03',
             'subject': {'text': '面試邀請', 'cite': [1]},
             'summary': '沒錄取', 'kind': 'reject', 'link': 'https://mail.google.com/mail/u/0/#all/thr001',
             'quote': [2], 'reason': [3], 'todo': ''}]}},
        numbered=lambda mark='': numbered_of(
            ('mail', 'email:thr001', '面試邀請\n2026年9月3日 好公司人資\n您好,邀請您參加面試,請回覆可面談時段。'),
            ('record', 'application_record:104', '應徵紀錄\n資料工程師 abc123 2026/09/01 已讀'),
            ('record', 'application_record:linkedin', 'Applied 4012345678 Senior Designer'), mark=mark),
        truth=lambda: gate.Truth(
            cards={A, B}, source_types={'email:thr001': 'email', 'application_record:104': 'application_record'}),
        lies={'checked': (['https://ex.test/job/zzz'], ['查完的卡', 'job/zzz', '不是這一輪查的卡']),
              'findings.url': ('https://ex.test/job/zzz', ['卡片網址', 'job/zzz', '不是這一輪查的卡']),
              'findings.source_ref': ('email:madeup', ['來源代號', 'email:madeup', '程式沒給它這個來源']),
              'findings.source_type': ('application_record', ['來源種類', 'application_record', 'email(程式照']),
              'findings.date': ('2026-02-31', ['日期', '2026-02-31', 'YYYY-MM-DD']),
              'findings.link': ('https://mail.google.com/mail/u/0/#all/other99', ['原文連結', 'other99',
                                                                                 'email:thr001 那一封']),
              'findings.kind': ('hired', ['信算哪一種', 'hired', 'confirm、reject']),
              'job_ids.platform': ('yourator', ['平台', 'yourator', '程式讀了 104']),
              'job_ids.applied_at': ('2026-02-31', ['應徵日期', '2026-02-31', 'YYYY-MM-DD']),
              'inaccessible.jobs': (['https://ex.test/job/zzz'], ['影響到的卡', 'job/zzz', '不是這一輪查的卡'])},
        judged={'findings.kind': 'interview', 'findings.todo': '回覆可面談時段',
                'findings.date': '2026-09-03', 'job_ids.applied_at': '2026-09-01'}),
}


def _set(sheet, name, value):
    head, _, sub = name.partition('.')
    if sub and isinstance(sheet.get(head), list):
        sheet[head][0][sub] = value
    elif sub:
        sheet.setdefault(head, {})[sub] = value
    else:
        sheet[head] = value


class LyingAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory(prefix='gate-'))
        self.old_reg = ps.REG
        ps.REG = os.path.join(self.tmp, 'profiles.json')
        ps.remember('104', 'zh', 'general', MINE)
        ps.remember_name('104', 'zh', 'general', '中文履歷')
        ps.remember('104', 'en', 'general', OTHER)
        ps.remember_name('104', 'en', 'general', 'English Resume')
        self.fb = {'__ans__': [{'k': 'a1', 'q': '自我推薦信', 'v': '我擅長資料管線。'}],
                   URL: {'form': {'f': [{'q': '姓名', 'src': 'rz', 'v': '王小明'},
                                        {'q': '自我推薦信', 'src': 'bank', 'k': 'a1'}]}}}

    def tearDown(self):
        ps.REG = self.old_reg

    def truth(self, page=PAGE):
        # 下載回來逐位元組比檔的那一段(job 給了才做)在 test_apply_profile_attachments 測
        return gate.Truth(URL, None, self.fb, page=copy.deepcopy(page), tab_id='7', decision=dict(DECISION))

    def inspect(self, kind, sheet, page=PAGE):
        """照填表流程交件:指示印的平台筆記(編號材料)跟著交給安檢門,交件單抄回它的交付代號。"""
        numbered = fill_notes()
        return gate.inspect(kind, dict(sheet, material_id=numbered.material_id), self.truth(page), numbered)

    def test_an_honest_handoff_sheet_is_accepted(self):
        verdict = self.inspect('fill', copy.deepcopy(HONEST))
        self.assertEqual(verdict.problems, [])
        self.assertEqual(verdict.facts['delivery'], {'method': 'platform_profile'})
        verdict = self.inspect('fill', copy.deepcopy(DIRECT), DIRECT_PAGE)
        self.assertEqual(verdict.problems, [])
        self.assertEqual(verdict.facts['uploaded'], ['cv.pdf'])

    def test_every_cell_lied_about_elsewhere_names_a_real_test(self):
        import importlib
        for cell, where in ELSEWHERE.items():
            with self.subTest(cell=cell):
                module, cls, name = where.split('.')
                mod = importlib.import_module(module)  # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import  模組名來自本檔寫死的 ELSEWHERE
                self.assertTrue(hasattr(getattr(mod, cls), name), where)

    def test_every_checked_cell_of_every_sheet_has_a_lie(self):
        lies = {'fill': LIES, 'submit': SUBMIT_LIES, 'profile_choice': {}}
        lies.update({kind: flow['lies'] for kind, flow in FLOWS.items()})
        for kind, table in gate.SHEETS.items():
            checked = {n for n, c in table.items() if c.how in (gate.CHECK, gate.DECIDED) and c.cite is None}
            missing = checked - set(lies[kind]) - set(ELSEWHERE) - set(DROPPED if kind == 'fill' else ()) - {n.split(':')[1] for n in ELSEWHERE
                                                                      if n.startswith(kind + ':')}
            self.assertEqual(missing, set(), f'{kind} 這幾格沒有說謊的測試')

    def test_submit_lies_are_stopped(self):
        for kind, honest, lies, page in (('submit', SUBMIT_HONEST, SUBMIT_LIES, THANKS),):
            self.assertEqual(self.inspect(kind, copy.deepcopy(honest), page).problems, [], kind)
            for name, (lie, words) in lies.items():
                with self.subTest(kind=kind, cell=name):
                    sheet = copy.deepcopy(honest)
                    _set(sheet, name, lie)
                    said = '\n'.join(self.inspect(kind, sheet, page).problems)
                    for w in words:
                        self.assertIn(w, said)

    def test_submit_quote_can_span_visible_lines_but_must_match_the_page(self):
        page = dict(THANKS, lines=['應徵成功', '感謝您的申請，我們會與您聯絡。'])
        sheet = dict(SUBMIT_HONEST, confirm_text='應徵成功\n\n感謝您的申請，我們會與您聯絡。')
        self.assertEqual(self.inspect('submit', sheet, page).problems, [])
        sheet['confirm_text'] = '應徵成功\n\n已安排面試。'
        self.assertTrue(any('頁面上沒有這句話' in problem
                            for problem in self.inspect('submit', sheet, page).problems))

    def test_every_checked_cell_is_lied_about_once_and_each_lie_is_stopped(self):
        checked = {n for n, c in gate.FILL.items() if c.how in (gate.CHECK, gate.DECIDED) and c.cite is None}
        self.assertEqual(checked - set(LIES) - set(ELSEWHERE) - set(DROPPED), set(), '這幾格沒有說謊的測試')
        for name, lie in DROPPED.items():
            with self.subTest(cell=name):
                sheet = copy.deepcopy(HONEST)
                _set(sheet, name, lie)
                verdict = self.inspect('fill', sheet, PAGE)
                self.assertTrue(verdict.ok, verdict.problems)
                self.assertNotIn(lie, json.dumps(verdict.facts))
        for name, (lie, words, *kind) in LIES.items():
            with self.subTest(cell=name):
                direct = kind == ['direct']
                sheet = copy.deepcopy(DIRECT if direct else HONEST)
                _set(sheet, name, lie)
                verdict = self.inspect('fill', sheet, DIRECT_PAGE if direct else PAGE)
                self.assertFalse(verdict.ok, f'{name} 說謊沒被擋下')
                said = '\n'.join(verdict.problems)
                for w in words:
                    self.assertIn(w, said)

    def test_marking_the_fixed_resume_as_custom_is_stopped_with_the_reason(self):
        sheet = copy.deepcopy(HONEST)
        sheet['delivery'] = {'method': 'platform_profile', 'profile_kind': 'custom', 'profile_url': MINE}
        verdict = self.inspect('fill', sheet)
        self.assertIn('交件單「固定版還是客製版」:agent 說 custom,實際是 fixed(程式照這張卡決定的)', verdict.problems)

    def test_cells_nobody_registered_never_reach_the_program(self):
        sheet = dict(copy.deepcopy(HONEST), chrome={'pid': 1}, secret_decision='submit it')
        verdict = self.inspect('fill', sheet)
        self.assertEqual(verdict.problems, [])
        self.assertNotIn('chrome', verdict.facts)
        self.assertNotIn('secret_decision', verdict.facts)
        self.assertEqual(sorted(verdict.unregistered), ['chrome', 'secret_decision'])


def inspect_flow(kind, flow, sheet):
    """照流程交件:有編號材料的流程,交件單抄回那一份的交付代號,同一個物件交給安檢門。"""
    numbered = flow['numbered']() if 'numbered' in flow else None
    if numbered is not None:
        sheet = dict(sheet, material_id=numbered.material_id)
    return gate.inspect(kind, sheet, flow['truth'](), numbered)


class OtherFlowsLie(unittest.TestCase):
    """其他 7 種流程:照實寫的交件單收下;每一個要核對的格子各填錯一次,那一列被擋下、原因寫對,別列照收。"""

    def test_an_honest_sheet_of_every_flow_is_accepted(self):
        for kind, flow in FLOWS.items():
            for name, sheet in dict(flow.get('variants') or {}, honest=flow['honest']).items():
                with self.subTest(kind=kind, sheet=name):
                    verdict = inspect_flow(kind, flow, copy.deepcopy(sheet))
                    self.assertEqual(verdict.problems, [])

    def test_every_lie_in_every_flow_is_stopped_with_the_reason(self):
        for kind, flow in FLOWS.items():
            for name, (lie, words, *variant) in flow['lies'].items():
                with self.subTest(kind=kind, cell=name):
                    sheet = copy.deepcopy(flow['variants'][variant[0]] if variant else flow['honest'])
                    _set(sheet, name, lie)
                    verdict = inspect_flow(kind, flow, sheet)
                    self.assertFalse(verdict.ok, f'{kind} 的 {name} 說謊沒被擋下')
                    said = '\n'.join(verdict.problems)
                    for w in words:
                        self.assertIn(w, said)
                    head = name.partition('.')[0]
                    if head in (gate.ROWS.get(kind) or {}):
                        self.assertFalse(verdict.rows[head][0].ok)          # 說謊的那一列不收
                        self.assertTrue(all(r.ok for r in verdict.rows[head][1:]))

    def test_judgments_are_marked_and_never_become_facts(self):
        """程式核對不了的判斷(職缺關了沒、信算拒絕還是面試…):放在 judged,不在 facts,程式不能拿它當事實用。"""
        flows = dict(FLOWS, fill={'honest': HONEST, 'numbered': fill_notes,
                                'truth': lambda: gate.Truth(URL, page=PAGE, decision=DECISION),
                                'judged': {'posting.same_job': True, 'submitted': False}},
                     submit={'honest': SUBMIT_HONEST, 'truth': lambda: gate.Truth(URL, page=THANKS),
                             'judged': {'submitted': True}})
        judged = {kind: flow['judged'] for kind, flow in flows.items() if flow.get('judged')}
        for kind, table in gate.SHEETS.items():
            for name, cell in table.items():
                if cell.how == gate.JUDGED:
                    self.assertIn(name, judged.get(kind, {}), f'{kind} 的 {name} 是 agent 判斷,要有測試')
        for kind, cells in judged.items():
            for name, value in cells.items():
                value, *variant = value if isinstance(value, tuple) else (value,)
                sheet = flows[kind]['variants'][variant[0]] if variant else flows[kind]['honest']
                verdict = inspect_flow(kind, flows[kind], copy.deepcopy(sheet))
                with self.subTest(kind=kind, cell=name):
                    head, _, sub = name.partition('.')
                    if head in (gate.ROWS.get(kind) or {}):
                        row = verdict.rows[head][0]
                        self.assertEqual(row.judged.get(sub), value)
                        self.assertNotIn(sub, row.facts)
                    else:
                        judgments = verdict.judged.get(head, {}) if sub else verdict.judged
                        facts = verdict.facts.get(head, {}) if sub else verdict.facts
                        self.assertEqual(judgments.get(sub or name), value)
                        self.assertNotIn(sub or name, facts)

    def test_feedback_from_one_card_is_not_a_repeated_problem(self):
        sheet = {'reports': [{'issue': '成果', 'recommendation': 'x', 'feedback_ids': ['f1', 'f3']}]}
        verdict = gate.inspect('customize', sheet, FLOWS['customize']['truth']())
        self.assertIn('只有一張卡提到', '\n'.join(verdict.problems))

    def test_a_date_written_as_days_ago_counts_from_the_day_the_page_was_fetched(self):
        flow = FLOWS['posted_at']
        sheet = {'dates': [{'url': A, 'posted_at': '2026-09-17', 'source': [2]}]}
        verdict = inspect_flow('posted_at', flow, sheet)
        self.assertEqual(verdict.problems, [])
        self.assertEqual(verdict.rows['dates'][0].facts['source'], 'Updated 3 days ago')


# ---- 段落編號(#465):有 cite 的格只收程式這一次交付的編號 ----
# 預期契約:(單, 格, items 裡的鍵或 None),每一格「應該只收編號」的格;跟 gate.SHEETS 推出來的兩邊互比。
CITING = {('fill', 'platform_notes_remove', None), ('posted_at', 'dates.source', None),
          ('link_status', 'jobs.quote', None), ('prepare', 'real_title', 'cite'), ('prepare', 'quote', None),
          ('research_judge', 'jobs.title', 'cite'), ('research_judge', 'jobs.company', 'cite'),
          ('research_judge', 'jobs.reasons', 'citation'), ('research_judge', 'jobs.risk', 'cite'),
          ('research_judge', 'jobs.card', 'quote'), ('research_judge', 'jobs.terms', 'salary_quote'),
          ('research_judge', 'jobs.terms', 'exp_quote'), ('research_judge', 'jobs.terms', 'location_quote'),
          ('reply', 'findings.subject', 'cite'),
          ('reply', 'findings.quote', None), ('reply', 'findings.reason', None), ('reply', 'job_ids.at', None)}
MARK = '⟪程式交出去的字⟫'


def registered_cites(sheets):
    """從 SHEETS 推:每一個有 cite 的格(Accept → 鍵是 None;{鍵: Accept} → 每個鍵一列)。"""
    out = set()
    for kind, table in sheets.items():
        for name, cell in table.items():
            if isinstance(cell.cite, Accept):
                out.add((kind, name, None))
            elif cell.cite:
                out |= {(kind, name, key) for key in cell.cite}
    return out


def contract_gaps(sheets, contract):
    got = registered_cites(sheets)
    return [f'{x} 登記了 cite,契約沒寫' for x in sorted(got - contract, key=str)] + \
           [f'{x} 契約寫了,SHEETS 沒登記 cite' for x in sorted(contract - got, key=str)]


def fill_notes(mark=''):
    """填表指示印的平台筆記(prompt_for 的 numbered)。"""
    numbered = Numbered()
    numbered.add([mark + '自訂經歷的編輯器要先全選再輸入才會存', mark + '推薦信從下拉選單選'], 'pnote')
    return numbered


def cite_fixtures():
    """有 numbered 的流程:照實交件單(含 variants)、編號材料(mark 加在每段前面)、真相(吃那一份材料)。"""
    flows = dict(FLOWS, fill={'honest': dict(HONEST, platform_notes_remove=[2]), 'numbered': fill_notes,
                              'truth': lambda: gate.Truth(URL, page=copy.deepcopy(PAGE), tab_id='7',
                                                          decision=dict(DECISION))})
    return {kind: {'sheets': [flow['honest'], *(flow.get('variants') or {}).values()],
                   'numbered': flow['numbered'], 'truth': lambda numbered, flow=flow: flow['truth']()}
            for kind, flow in flows.items() if 'numbered' in flow}


def _cited_spots(kind, sheet, name, cell):
    """交件單裡這一格每一個寫了非空編號的地方:(路徑, 列的序號或 None, 那一筆或 None, 鍵或 None)。"""
    head, _, sub = name.partition('.')
    if head in (gate.ROWS.get(kind) or {}):
        cells = [([head, i, sub], i, r.get(sub)) for i, r in enumerate(sheet.get(head) or []) if sub in r]
    else:
        cells = [([name], None, sheet.get(name))] if name in sheet else []
    for path, row, value in cells:
        if isinstance(cell.cite, Accept):
            if value:
                yield path, row, None, None
            continue
        items = list(enumerate(value)) if isinstance(value, list) else [(None, value)]
        for at, item in items:
            for key in cell.cite:
                if isinstance(item, dict) and item.get(key):
                    yield path + ([] if at is None else [at]) + [key], row, item, key


def _get(sheet, path):
    for step in path:
        sheet = sheet[step]
    return sheet


def _taken(kind, verdict, name, row):
    """這一格有沒有被收下(進 facts 或 judged)。row 是那一列的序號,None 是整張的一格。"""
    head, _, sub = name.partition('.')
    if row is None:
        return name in verdict.facts or name in verdict.judged
    got = verdict.rows.get(head, [])[row]
    return sub in got.facts or sub in got.judged


def cite_lies(sheets, fixtures):
    """每一張單、每一個有 cite 的格(與鍵)自動產生這幾種說謊:抄原字、超出範圍、別一列的號碼(owner 有限定時)、
    為別次交付寫的號碼;{鍵: Accept} 的格再加整格或清單裡的一筆寫成字串。回擋不下的(空清單是全擋下)。"""
    failures = []
    for kind, table in sheets.items():
        cites = {n: c for n, c in table.items() if c.cite is not None}
        if cites and kind not in fixtures:
            failures.append(f'{kind} 有 cite 格,卻沒有 fixture')
            continue
        for name, cell in cites.items():
            for key in ([None] if isinstance(cell.cite, Accept) else list(cell.cite)):
                failures += _lie_about(kind, name, cell, key, fixtures[kind])
    return failures


def _lie_about(kind, name, cell, key, fx):
    accept = cell.cite if key is None else cell.cite[key]
    label = f'{kind} 的「{cell.name}」' + (f'.{key}' if key else '')
    numbered = fx['numbered']()
    check = lambda sheet: gate.inspect(kind, sheet, fx['truth'](numbered), numbered)
    spots = [(dict(copy.deepcopy(sheet), material_id=numbered.material_id), spot)
             for sheet in fx['sheets'] for spot in _cited_spots(kind, sheet, name, cell) if spot[3] == key]
    if not spots:
        return [f'{label}沒有照實引用的範例']
    owns = lambda found: any(numbered.parts[n - 1].owner is not None for n in _get(found[0], found[1][0]))
    sheet, (path, row, item, _key) = next((found for found in spots if owns(found)), spots[0])
    verdict = check(copy.deepcopy(sheet))
    if not _taken(kind, verdict, name, row):
        return [f'{label}的照實範例沒有收下:{verdict.problems}']
    ids = _get(sheet, path)
    cited = [numbered.parts[n - 1] for n in ids]
    other = Numbered([dataclasses.replace(p, text='別次交付:' + p.text) for p in numbered.parts])
    lies = {'抄原字': ('\n'.join(p.text for p in cited), None), '超出範圍': ([len(numbered.parts) + 1], None),
            '別次交付': (ids, other.material_id)}
    at = {}
    if key is not None:     # {鍵: Accept} 的格:整格、清單裡的那一筆寫成字串,沒有鍵可換
        in_list = isinstance(_get(sheet, path[:-2]), list)
        at['錯形狀'] = path[:-2] if in_list else path[:-1]
        lies['錯形狀'] = ('任意 agent 字', None)
        if in_list:
            at['一筆錯形狀'] = path[:-1]
            lies['一筆錯形狀'] = ('任意 agent 字', None)
    if accept.value:        # 要當值用的格:值不在指的段落裡、指了段落卻沒寫值,都不收
        at['值不在段落'] = at['值空了'] = path[:-1] + [accept.value]
        lies['值不在段落'] = ('程式材料裡沒有的字', None)
        lies['值空了'] = ('', None)
    failures = []
    if accept.owner is not None:
        kinds = accept.kinds(item or {}) if callable(accept.kinds) else accept.kinds
        owned = {p.owner for p in cited} - {None}
        elsewhere = [p.n for p in numbered.parts
                     if p.owner is not None and p.owner not in owned and (kinds is None or p.kind in kinds)]
        if not owned:
            failures.append(f'{label}的照實範例只引了整批共用的段,看不出這一列是誰')
        elif not elsewhere:
            failures.append(f'{label}的 fixture 裡沒有別一列的段')
        else:
            lies['別一列'] = ([elsewhere[0]], None)
    for lie, (value, material) in lies.items():
        bad = copy.deepcopy(sheet)
        if material:
            bad['material_id'] = material
        where = at.get(lie, path)
        _get(bad, where[:-1])[where[-1]] = value
        verdict = check(bad)
        said = '\n'.join(verdict.problems)
        if _taken(kind, verdict, name, row):
            failures.append(f'{label}收了「{lie}」的號碼')
        elif lie == '別次交付' and not (numbered.material_id in said and material in said):
            failures.append(f'{label}擋下別次交付,但問題沒寫出兩個代號:{said}')
        elif lie != '別次交付' and cell.name not in said:
            failures.append(f'{label}擋下「{lie}」,但問題沒寫出格名:{said}')
    return failures


def _reaches(value, mark, seen):
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, str):
        return mark in value
    if isinstance(value, dict):
        return any(_reaches(k, mark, seen) or _reaches(v, mark, seen) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_reaches(v, mark, seen) for v in value)
    if hasattr(value, '__dict__') and not callable(value):
        return any(_reaches(v, mark, seen) for v in vars(value).values())
    return False


def leaks(sheets, fixtures):
    """每一份 fixture 的段落字前面加記號,包住每一格的 fn 記下它收到的東西(值、整張、真相與 RowTruth.sheet);
    找到記號就是格子函式拿得到程式交出去的字。回拿得到的格。"""
    failures = []
    for kind, fx in fixtures.items():
        if kind not in sheets:
            continue
        numbered = fx['numbered'](MARK)
        seen = []

        def wrap(name, cell):
            def fn(said, whole, truth, inner=cell.fn):
                seen.append((name, (said, whole, truth)))
                return inner(said, whole, truth)
            out = copy.copy(cell)
            out.fn = fn
            return out
        with mock.patch.dict(sheets[kind], {n: wrap(n, c) for n, c in sheets[kind].items() if c.fn}):
            for sheet in fx['sheets']:
                gate.inspect(kind, dict(copy.deepcopy(sheet), material_id=numbered.material_id),
                             fx['truth'](numbered), numbered)
        for name in sorted({n for n, args in seen if _reaches(args, MARK, set())}):
            failures.append(f'{kind} 的「{sheets[kind][name].name}」格子函式拿得到程式交出去的字')
    return failures


FAKE_BASIS = {'JD 原文': frozenset({'jd'}), '偏好筆記': frozenset({'note'})}


def fake_numbered(mark=''):
    numbered = Numbered()
    numbered.add(mark + '他喜歡資料管線', 'note')
    numbered.add([mark + '資料工程師', mark + '月薪 5 萬'], 'jd', owner='J1')
    numbered.add(mark + '後端工程師', 'jd', owner='J2')
    numbered.add(mark + '刊登超過 90 天', 'flag', owner='J3')
    return numbered


def fake_sheet(seen=None, title=Accept(frozenset({'jd'}), ROW)):
    """一張假單:職稱引 JD、理由照自己的 basis 引、卡片摘要(agent 判斷)引 JD;沒有 JD 的列摘要用不到。"""
    def raw(said, row, truth):
        if seen is not None:
            seen.append(said)
    return {
        'items.id': Cell(gate.CHECK, '代號', '這一輪交給它的', lambda s, r, t: None if t.key in t.handed else '沒交給它'),
        'items.title': Cell(gate.CHECK, '頁面上的職稱', 'JD 的編號', raw, cite=title),
        'items.reasons': Cell(gate.CHECK, '理由的引用', '照根據收', items=('text', 'citation', 'basis'),
                              cite={'citation': Accept(lambda item: FAKE_BASIS.get(item.get('basis'), frozenset()), ROW)}),
        'items.card': Cell(gate.JUDGED, '卡片摘要', 'JD 的編號',
                           lambda s, r, t: None if (t.key, 'jd') in t.read else gate.UNUSED,
                           items=('fit', 'quote'), cite={'quote': Accept(frozenset({'jd'}), ROW)}),
        'items.note': Cell(gate.WORDS, '它的話', '只給人看'),
    }


FAKE_HONEST = {'items': [
    {'id': 'J1', 'title': [2], 'note': '看起來不錯', 'card': {'fit': '對', 'quote': [3]},
     'reasons': [{'text': '對味', 'citation': [1], 'basis': '偏好筆記'},
                 {'text': '要管線', 'citation': [2], 'basis': 'JD 原文'}]},
    {'id': 'J3', 'card': {'fit': '不知道', 'quote': []}}]}
FAKE_CONTRACT = {('fake', 'items.title', None), ('fake', 'items.reasons', 'citation'), ('fake', 'items.card', 'quote')}


def fake_fixture(truth=lambda numbered: gate.Truth(handed={'J1', 'J2', 'J3'})):
    return {'fake': {'sheets': [FAKE_HONEST], 'numbered': fake_numbered, 'truth': truth}}


class CitedCells(unittest.TestCase):
    """有 cite 的格:交件單寫段落編號,安檢門用 pick 解回程式的字;格子函式只看到編號。"""

    def inspect(self, sheet, table=None, numbered=None, material=None):
        numbered = numbered or fake_numbered()
        sheet = copy.deepcopy(sheet)
        if material is not False:
            sheet['material_id'] = material or numbered.material_id
        with mock.patch.dict(gate.SHEETS, fake=table or fake_sheet()), mock.patch.dict(gate.ROWS, fake={'items': 'id'}):
            return gate.inspect('fake', sheet, gate.Truth(handed={'J1', 'J2', 'J3'}), numbered)

    def test_cite_cell_puts_program_text_in_facts_and_raw_ids_in_fn(self):
        seen = []
        verdict = self.inspect(FAKE_HONEST, fake_sheet(seen))
        self.assertEqual(verdict.problems, [])
        title = verdict.rows['items'][0].facts['title']
        self.assertEqual(title, '資料工程師')
        self.assertIsInstance(title, Cited)
        self.assertEqual(seen, [[2]])
        self.assertNotIn('material_id', verdict.unregistered)

    def test_a_cited_cell_without_the_programs_material_is_a_program_error(self):
        """有引用格,程式卻沒交編號材料:是程式的錯,停下;不是悄悄不核、把 agent 的判斷照收。"""
        sheet = {'jobs': [{'id': 'J1', 'status': 'closed', 'quote': [1], 'reason': '徵到人了'}]}
        with self.assertRaisesRegex(RuntimeError, '頁面原文.*程式的錯'):
            gate.inspect('link_status', sheet, gate.Truth(handed={'J1'}))

    def test_judged_cite_keeps_the_object_in_judged_and_swaps_only_the_key(self):
        sheet = copy.deepcopy(FAKE_HONEST)
        row = self.inspect(sheet).rows['items'][0]
        self.assertEqual(row.judged['card'], {'fit': '對', 'quote': '月薪 5 萬'})
        self.assertIsInstance(row.judged['card']['quote'], Cited)
        self.assertNotIn('card', row.facts)
        self.assertEqual(sheet, FAKE_HONEST)

    def test_list_cite_kinds_come_from_each_item(self):
        row = self.inspect(FAKE_HONEST).rows['items'][0]
        self.assertEqual([r['citation'] for r in row.facts['reasons']], ['他喜歡資料管線', '資料工程師'])
        sheet = copy.deepcopy(FAKE_HONEST)
        sheet['items'][0]['reasons'][0]['citation'] = [2]      # 根據寫偏好筆記,引的卻是 JD
        verdict = self.inspect(sheet)
        self.assertNotIn('reasons', verdict.rows['items'][0].facts)
        self.assertTrue(any('理由的引用' in p and '資料工程師' in p for p in verdict.problems), verdict.problems)

    def test_a_keyed_cite_cell_of_the_wrong_shape_is_refused_not_kept_raw(self):
        for cell, value in (('reasons', '任意 agent 字'), ('reasons', ['任意 agent 字']), ('card', '任意 agent 字')):
            with self.subTest(cell=cell, value=value):
                sheet = copy.deepcopy(FAKE_HONEST)
                sheet['items'][0][cell] = value
                verdict = self.inspect(sheet)
                row = verdict.rows['items'][0]
                self.assertFalse(row.ok)
                self.assertNotIn(cell, row.facts)
                self.assertNotIn(cell, row.judged)
                name = fake_sheet()[f'items.{cell}'].name
                self.assertTrue(any(name in p for p in verdict.problems), verdict.problems)

    def test_a_sheet_with_another_deliverys_id_is_refused(self):
        numbered, other = fake_numbered(), fake_numbered('別次:')
        for material in (other.material_id, False):
            with self.subTest(material=material):
                verdict = self.inspect(FAKE_HONEST, numbered=numbered, material=material)
                row = verdict.rows['items'][0]
                self.assertFalse(row.ok)
                for cell in ('title', 'reasons'):
                    self.assertNotIn(cell, row.facts)
                self.assertNotIn('card', row.judged)
                self.assertEqual(row.facts['note'], '看起來不錯')
                said = '\n'.join(verdict.problems)
                self.assertIn(numbered.material_id, said)
                if material:
                    self.assertIn(material, said)

    def test_has_a_jd_means_owner_and_kind(self):
        rows = self.inspect(FAKE_HONEST).rows['items']
        self.assertIn('card', rows[0].judged)
        self.assertNotIn('card', rows[1].judged)       # J3 只有程式提醒,沒有 JD

    def test_cite_must_name_items(self):
        for kw in ({'cite': {'quote': Accept()}}, {'items': ('quote',), 'cite': Accept()},
                   {'items': ('quote',), 'cite': {'why': Accept()}}, {'cite': 'quote'}):
            with self.subTest(kw=kw), self.assertRaises(TypeError):
                Cell(gate.CHECK, '格', '說明', **kw)
        Cell(gate.CHECK, '格', '說明', cite=Accept())
        Cell(gate.JUDGED, '格', '說明', items=('quote', 'fit'), cite={'quote': Accept()})

    def test_enumerated_guards_bite_on_a_fake_sheet(self):
        sheets = {'fake': fake_sheet()}
        with mock.patch.dict(gate.ROWS, fake={'items': 'id'}), mock.patch.dict(gate.SHEETS, sheets):
            self.assertEqual(cite_lies(sheets, fake_fixture()), [])
            self.assertEqual(leaks(sheets, fake_fixture()), [])
            self.assertEqual(contract_gaps(sheets, FAKE_CONTRACT), [])
            loose = {'fake': fake_sheet(title=Accept(frozenset({'jd'}), lambda row, t: {'J1', 'J2', None}))}
            with mock.patch.dict(gate.SHEETS, loose):
                self.assertEqual(cite_lies(loose, fake_fixture()), ['fake 的「頁面上的職稱」收了「別一列」的號碼'])
            with self.assertRaises(TypeError):           # 真相不收材料:格子函式拿不到程式交出去的字
                gate.Truth(given={'J1': '原文'})
            given = fake_fixture(lambda n: gate.Truth(handed={'J1', 'J2', 'J3'},
                                                      feedback={'J1': '\n'.join(p.text for p in n.parts)}))
            self.assertIn('fake 的「頁面上的職稱」格子函式拿得到程式交出去的字', leaks(sheets, given))
            self.assertEqual(contract_gaps(sheets, FAKE_CONTRACT - {('fake', 'items.card', 'quote')}),
                             ["('fake', 'items.card', 'quote') 登記了 cite,契約沒寫"])
            self.assertEqual(cite_lies(sheets, {}), ['fake 有 cite 格,卻沒有 fixture'])

    def test_citing_cells_match_the_expected_contract(self):
        self.assertEqual(contract_gaps(gate.SHEETS, CITING), [])

    def test_every_citing_cell_refuses_copied_text_other_rows_made_up_numbers_and_another_deliverys_ids(self):
        self.assertEqual(cite_lies(gate.SHEETS, cite_fixtures()), [])

    def test_cell_functions_never_see_handed_text(self):
        self.assertEqual(leaks(gate.SHEETS, cite_fixtures()), [])


class ReadSaysWhatWentWrong(unittest.TestCase):
    def test_malformed_sheet_is_not_reported_as_missing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(gate.read(out, 'fill'), (None, '沒有寫出 fill.json'))
            def wrote(text):
                with open(gate.path(out, 'fill'), 'w', encoding='utf-8') as f:
                    f.write(text)
                return gate.read(out, 'fill')
            # 物件完整、只多打收尾括號(2026-10-04、10-05 104 實測):意思不變,照收
            self.assertEqual(wrote('{"status": "complete", "quotes": {}}}'), ({'status': 'complete', 'quotes': {}}, ''))
            # 後面接了別的東西、或被截斷:講寫壞了,不說成沒寫
            for broken in ('{"status": "complete"} 補充說明', '{"status": "complete", "quotes": {'):
                sheet, why = wrote(broken)
                self.assertIsNone(sheet)
                self.assertIn('寫壞了', why)


class CardUrl(unittest.TestCase):
    def test_extra_query_from_the_site_is_still_the_same_job_but_the_job_id_must_stay(self):
        import gate_apply as ga
        truth = gate.Truth(url='https://www.example.test/job/8i7j7')
        self.assertIsNone(ga._card_url('https://www.example.test/job/8i7j7?apply=form', {}, truth))
        self.assertTrue(ga._card_url('https://www.example.test/job/other', {}, truth))
        truth = gate.Truth(url='https://boards.example.test/acme/jobs?gh_jid=1')
        self.assertIsNone(ga._card_url('https://boards.example.test/acme/jobs?gh_jid=1&src=x', {}, truth))
        self.assertTrue(ga._card_url('https://boards.example.test/acme/jobs?gh_jid=2', {}, truth))


class FlowsUseTheGate(unittest.TestCase):
    """沒有自己測試檔的流程:交件單經安檢門,對不上的那一項不收、原因照實給人看。"""

    def test_a_category_suggestion_with_a_broken_rule_is_left_out_and_named(self):
        import json
        import suggest_cats
        tmp = self.enterContext(tempfile.TemporaryDirectory(prefix='gate-'))
        out = os.path.join(tmp, 'suggest.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump({'categories': [{'name': '工程', 'icon': '⚙', 'match': 'engineer|工程師'},
                                      {'name': '資料', 'icon': '📊', 'match': 'data('},
                                      {'name': '其他', 'icon': '?', 'match': 'x'}],
                       'tags': [], 'why': '照履歷'}, f, ensure_ascii=False)
        shown = suggest_cats.checked(out, [{'name': '其他', 'icon': '•'}])
        self.assertEqual([c['name'] for c in shown['categories']], ['工程', '其他'])
        self.assertEqual(shown['categories'][-1], {'name': '其他', 'icon': '•', 'match': ''})   # 程式自己加的
        self.assertEqual(shown['by'], 'agent 判斷')
        self.assertTrue(any('比對規則' in p and 'data(' in p for p in shown['problems']), shown['problems'])


class NoBypass(unittest.TestCase):
    """程式裡只有 gate.py 打開交件單;指示裡叫 agent 寫的每一格都登記了核對方式。"""

    @staticmethod
    def _sheet_re():
        """每一種交件單的檔名(* 那一段每一輪不一樣):寫在程式裡可能是字串、f 字串或拼起來的。"""
        parts = [re.escape(name).replace(r'\*', r"[^'\"]*") for name in gate.FILES.values()]
        return re.compile('(' + '|'.join(parts) + ')')

    def test_every_inspect_of_a_sheet_with_cited_cells_passes_the_material(self):
        """有引用格的交件單,呼叫安檢門的地方都交這一次的編號材料(第四個參數或 numbered=);種類要寫成字串才看得出是哪一種。"""
        citing = {kind for kind, table in gate.SHEETS.items() if any(c.cite is not None for c in table.values())}
        seen, missing = set(), []
        for name in sorted(os.listdir(TOOLS)):
            if not name.endswith('.py') or name == 'gate.py':
                continue
            with open(os.path.join(TOOLS, name), encoding='utf-8') as f:
                tree = ast.parse(f.read())
            for c in ast.walk(tree):
                if not (isinstance(c, ast.Call) and ast.unparse(c.func) == 'gate.inspect'):
                    continue
                kind = c.args[0].value if c.args and isinstance(c.args[0], ast.Constant) else None
                seen.add(kind)
                if kind is None or (kind in citing and len(c.args) < 4
                                    and not any(k.arg == 'numbered' for k in c.keywords)):
                    missing.append(f'{name}:{c.lineno} {ast.unparse(c)[:100]}')
        self.assertEqual(missing, [], '這幾處呼叫安檢門沒交編號材料')
        self.assertEqual(citing - seen, set(), '有引用格的交件單,程式裡找不到呼叫安檢門的地方')

    def test_only_the_gate_opens_a_handoff_sheet(self):
        """打開檔案的地方(open、json.load…),參數裡直接寫交件單檔名、或用了指到交件單的變數,都算自己打開交件單。
        指到交件單的變數:模組最上面、同一個函式裡由交件單檔名拼出來的,或同一個檔裡別的函式把交件單路徑傳進來的參數。"""
        sheet = self._sheet_re()
        found = set()
        for name in sorted(os.listdir(TOOLS)):
            if not name.endswith('.py') or name == 'gate.py':
                continue
            with open(os.path.join(TOOLS, name), encoding='utf-8') as f:
                tree = ast.parse(f.read())

            def mentions(text, known):
                return bool(sheet.search(text)) or any(re.search(rf'\b{re.escape(k)}\b', text) for k in known)

            def names_of(body, known):
                known = set(known)
                for _ in range(3):                       # 變數拼變數:多走幾遍
                    for n in body:
                        for a in ast.walk(n):
                            if isinstance(a, (ast.Assign, ast.AnnAssign)) and a.value is not None \
                                    and mentions(ast.unparse(a.value), known):
                                for t in (a.targets if isinstance(a, ast.Assign) else [a.target]):
                                    known.update(x.id for x in ast.walk(t) if isinstance(x, ast.Name))
                return known

            top = names_of([n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))], ())
            fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            by_name = {fn.name: fn for fn in fns}
            passed = {fn.name: set() for fn in fns}      # 別的函式把交件單路徑傳進來的參數
            for _ in range(3):
                for fn in fns:
                    local = names_of(fn.body, top | passed[fn.name])
                    for c in ast.walk(fn):
                        callee = by_name.get(ast.unparse(c.func)) if isinstance(c, ast.Call) else None
                        if callee is None:
                            continue
                        params = [x.arg for x in callee.args.args]
                        for i, arg in enumerate(c.args):
                            if i < len(params) and mentions(ast.unparse(arg), local):
                                passed[callee.name].add(params[i])
                        for kw in c.keywords:
                            if kw.arg in params and mentions(ast.unparse(kw.value), local):
                                passed[callee.name].add(kw.arg)
            for fn in fns:
                local = names_of(fn.body, top | passed[fn.name])
                for c in ast.walk(fn):
                    if not isinstance(c, ast.Call):
                        continue
                    if ast.unparse(c.func).split('.')[-1] not in ('open', 'load', 'loads', 'read_text'):
                        continue
                    mode = [a for a in c.args[1:2] if isinstance(a, ast.Constant)] + \
                           [k.value for k in c.keywords if k.arg == 'mode' and isinstance(k.value, ast.Constant)]
                    if mode and set(str(mode[0].value)) & set('wax'):
                        continue                         # 寫進去(程式自己放回原位)不是讀 agent 交的東西
                    if mentions(' '.join(ast.unparse(a) for a in c.args), local):
                        found.add((name, fn.name))
        self.assertEqual(found, set(), '這些地方自己打開交件單:改走 gate.read,核對過的才用')

    def test_every_cell_the_instructions_ask_for_is_registered(self):
        import apply_run as run
        import chrome_door
        from unittest.mock import patch
        door = chrome_door.of('codex')
        job = {'id': URL}
        fb = {URL: {'form': {'f': []}, 'apply': {'delivery': {'method': 'platform_profile'}}}}
        with patch.object(run.ship, 'folder', return_value=''), \
             patch.object(run.ship, 'read_info', return_value={'lang': 'zh', 'variant': 'general'}), \
             patch.object(run.fr, 'shared_text', return_value=''), \
             patch.object(ps, 'decided', return_value=dict(DECISION, profile_kind='custom')):
            said = {kind: [run.prompt_for(stage, URL, job, fb, self.id(), door=door)[0] for stage in stages]
                    for kind, stages in (('fill', ('fill', 'fix')), ('submit', ('submit',)))}
        for kind, prompts in said.items():
            table = gate.SHEETS[kind]
            known = set(table) | {n.split('.')[0] for n in table} | {i for c in table.values() for i in c.items}
            known |= {n.split('.')[1] for n in table if '.' in n}
            known |= {'material_id'} if any(c.cite is not None for c in table.values()) else set()
            for text in prompts:
                asked = set(re.findall(r'"([a-z_]+)"\s*:', text))
                asked |= {m for m in re.findall(r'\b(?:delivery|profile|posting)\.([a-z_]+)', text)}
                with self.subTest(kind=kind):
                    self.assertEqual(asked - known, set(), f'{kind} 的指示叫 agent 寫這幾格,安檢門沒登記核對方式')

    @staticmethod
    def other_prompts():
        """其他 7 種流程給 agent 的指示(用假的資料產生)。"""
        import posted_age
        import page_fetch
        page = page_fetch.PageResult(A, 'ok', text='Posted on September 7, 2026')
        import board_status
        seen = []

        def agent(prompt, _log, _browser):
            seen.append(prompt)
            raise RuntimeError('只要指示')
        board_status._agent_link_batch({A: page}, None, agent)
        return {'posted_at': [posted_age.prompt_for({A: {'target': '資料工程師'}}, {A: page}, '/tmp/x.json',
                                                   {A: '2026-09-20'})[0]],
                'link_status': seen,
                'suggest_cats': [__import__('suggest_cats').prompt('/tmp/x.json', {}, [])],
                'customize': [__import__('customize').build_prompt(A, '資料工程師', [], [], '/tmp/r.json', 'JD')],
                'prepare': [__import__('cut_tailor').prompt(
                    [(A, '資料工程師')], {A: ('ok', page)}, out_dir='/tmp/prep',
                    resumes=[{'id': 'general', 'name': '通用', 'files': {'zh': 'r/zh.pdf', 'en': 'r/en.pdf'}}])[0]],
                'research_search': [__import__('research').search_prompt('wide', '', [], {}, '', '/tmp/s.json',
                                                                          '/tmp/s.md')],
                'research_judge': [__import__('research').judge_prompt(
                    [{'url': A, 'title': '資料工程師', 'company': '好公司', 'jd': '資料工程師 好公司 負責資料管線'}],
                    [], '/tmp/j.json', resumes=[{'id': 'general', 'name': '通用', 'files': {'zh': 'r/zh.pdf'}}])[0]],
                'reply': [__import__('reply_run').prompt_for({}, {A: {'target': '資料工程師'}}, [A], '/tmp/r.json')]}

    def test_every_cell_other_flows_ask_for_is_registered(self):
        for kind, prompts in self.other_prompts().items():
            table = gate.SHEETS[kind]
            known = set(table) | {n.split('.')[0] for n in table} | {n.split('.')[1] for n in table if '.' in n}
            known |= {i for c in table.values() for i in c.items}
            known |= {'material_id'} if any(c.cite is not None for c in table.values()) else set()
            decided = {n.split('.')[-1] for n, c in table.items() if c.how == gate.DECIDED}
            for text in prompts:
                with self.subTest(kind=kind):
                    asked = set(re.findall(r'"([a-z_]+)"\s*:', text))
                    self.assertEqual(asked - known, set(), f'{kind} 的指示叫 agent 寫這幾格,安檢門沒登記核對方式')
                    # 程式已經知道答案的格子(程式決定):指示裡不叫 agent 寫
                    self.assertEqual(asked & decided, set(), f'{kind} 的指示還叫 agent 寫程式已經決定的格子')

    def test_every_other_flow_is_covered(self):
        """7 種流程每一種都有交件單登記、說謊測試和指示測試(新加一種流程就要補齊)。"""
        apply_flows = {'fill', 'submit'}
        flows = set(gate.SHEETS) - apply_flows - {'profile_choice'}
        # 7 種流程:查應徵進度、準備履歷、客製版、找缺(找、判)、職缺關了沒、刊登日期、分類建議
        self.assertEqual(flows, {'reply', 'prepare', 'customize', 'research_search', 'research_judge', 'link_status',
                                 'posted_at', 'suggest_cats'})
        self.assertEqual(flows, set(FLOWS))
        self.assertEqual(flows, set(self.other_prompts()))
        self.assertEqual(flows, set(gate.FILES) - apply_flows - {'profile_choice'})
        self.assertIn('profile_choice', gate.FILES)


class CompanyNameOnThePage(__import__('unittest').TestCase):
    def test_a_bilingual_company_name_counts_when_one_part_is_on_the_page(self):
        # 2026-10-07 LinkedIn 實測:agent 寫「聯發科技 (MediaTek)」,頁面上只有 MediaTek,以前整句找不到就擋
        import gate_apply
        truth = type('T', (), {'page': {}, 'text': lambda self: 'sr. product security engineer at mediatek'})()
        self.assertIsNone(gate_apply._on_page('聯發科技 (MediaTek)', {}, truth))
        self.assertEqual(gate_apply._on_page('台積電 (TSMC)', {}, truth), '頁面上沒有這幾個字')


class UnmetRequirement(unittest.TestCase):
    """跟職缺要求不符的地方(#449):原句要在頁面上(斷成兩行也認得)、檔要是這張要寄的、做法只有不寄或照要求改,履歷本身不能不寄。"""

    def test_each_entry_is_checked_without_knowing_the_kind_of_requirement(self):
        import gate_apply, gate_cells
        from unittest import mock
        truth = gate_cells.Truth(URL, None, {}, page=dict(PAGE, lines=['履歷內容請務必控制在', '兩頁以內(超過兩頁將直接視為資格不符)']))
        ok = {'quote': '履歷內容請務必控制在兩頁以內(超過兩頁將直接視為資格不符)', 'file': 'a.pdf', 'act': 'drop'}
        with mock.patch('ship.info', return_value={'files': ['cv.pdf', 'a.pdf']}):
            self.assertIsNone(gate_apply._unmet([], {}, truth))
            self.assertIsNone(gate_apply._unmet([ok], {}, truth))
            # 說明段落只出現在欄位周圍那一段(程式讀頁的 context),一樣算在頁面上
            near = gate_cells.Truth(URL, None, {}, page=dict(PAGE, lines=[], fields=[{'label': 'Resume', 'context': ok['quote']}]))
            self.assertIsNone(gate_apply._unmet([ok], {}, near))
            for change, want in (({'quote': '請附作品集'}, '頁面上沒有這句要求'), ({'file': 'x.pdf'}, '要寄的檔裡沒有'),
                                 ({'act': 'rewrite'}, '不知道怎麼處理'), ({'file': 'cv.pdf'}, '履歷本身不能不寄')):
                with self.subTest(change=change):
                    self.assertIn(want, gate_apply._unmet([dict(ok, **change)], {}, truth))


class ConfirmTextNextToField(unittest.TestCase):
    """送出後頁面把說明放在欄位旁(例如「驗證碼已寄到信箱」只在驗證碼欄周圍),agent 引用這句一樣核對得到,
    不會因為程式只看一般的行就把「沒送出」判成送出結果不明。"""

    def test_quote_only_in_field_context_is_on_the_page(self):
        import gate_apply, gate_cells
        said = 'A verification code was sent to you. To submit your application, enter the 8-character code.'
        page = dict(PAGE, url=URL, lines=['Security code', 'Submit application'],
                    fields=[{'label': 'Security code', 'value': '', 'context': said + '\nSecurity code'}])
        truth = gate_cells.Truth(URL, None, {}, page=page)
        sheet = {'submitted': False, 'clicked': True, 'reason': '要輸入驗證碼才能送出', 'confirm_url': URL, 'confirm_text': said}
        self.assertIsNone(gate_apply._submitted(False, sheet, truth))
        self.assertEqual(gate_apply._confirm_text('Application received', sheet, truth), '頁面上沒有這句話')


if __name__ == '__main__':
    unittest.main()
