#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安檢門(#316):agent 交回來的事實只從一個入口進到程式,程式自己看到的真相核對過才用。

會說謊的假 agent:一張全部照實寫的交件單收下;交件單上登記要核對的每一格各故意填錯一次(含把固定版標成客製版),
每一次都擋下,原因寫出哪一格、agent 說什麼、實際是什麼。另有結構測試:程式裡繞過安檢門直接讀交件單、
指示裡叫 agent 寫、卻沒登記核對方式的格子,測試就失敗。"""
import ast
import copy
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import gate                   # noqa: E402
import profile_sync as ps     # noqa: E402

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
    'profile.application_history_url': ('https://evil.example/h', ['應徵紀錄頁', 'evil.example', '不是這個平台']),
    'delivery.method': ('direct_upload', ['投遞方式', 'direct_upload', '中文履歷']),
    'delivery.profile_kind': ('custom', ['固定版還是客製版', 'custom', 'fixed']),
    'delivery.profile_url': (OTHER, ['用的平台履歷', 'vno=2', MINE]),
    'uploaded': (['ghost.pdf'], ['上傳欄裡沒有 ghost.pdf'], 'direct'),   # 申請表直接上傳時才看它
    'fields': ([{'q': '姓名', 'value': '李大華', 'src': 'rz'}], ['姓名', '李大華']),
    'submitted': (True, ['已送出', 'true', '頁面還停在申請表']),
    'confirm_url': ('https://www.104.com.tw/thanks', ['確認頁網址', 'thanks', FORM]),
    'confirm_text': ('應徵成功', ['確認頁的字', '應徵成功', '頁面上沒有這句話']),
    'fixed_profile.url': (OTHER, ['固定平台履歷', 'vno=2', MINE]),
    'problems': (['需要登入'], ['卡住', '需要登入']),
}
# 核對要讀別的東西(平台履歷頁、下載回來的檔)的那幾格:謊話在那一支核對函式自己的測試裡擋(模組.類別.測試;
# 找不到那一條測試,這支測試就失敗,不會變成一行沒人管的註解)
_PA = 'test_apply_profile_attachments.ProfileAttachments.'
ELSEWHERE = {
    'profile.equivalents': 'test_profile_sync.Equivalents.test_wording_that_is_not_on_the_page_is_refused',
    'uploaded_files': _PA + 'test_direct_upload_with_a_different_custom_file_is_a_problem',
    'uploaded_from': _PA + 'test_platform_without_readback_rejects_a_file_outside_the_card_folder',
    'upload_readback': _PA + 'test_platform_without_readback_checks_the_local_file_put_into_the_form',
    'fixed_profile.attachments': _PA + 'test_custom_profile_rechecks_fixed_attachments_and_names_overwrite',
    'profile_attachments': _PA + 'test_missing_changed_and_extra_attachments_are_named',
    # 送出那一輪:按完送出後程式讀的那一頁還停在申請表,它說送出了也不算(整條流程)
    'submit:submitted': 'test_gate_flow.CodexFlow.test_a_send_the_agent_calls_a_success_is_not_believed_while_the_form_is_still_there',
}
THANKS = {'url': 'https://www.104.com.tw/apply/done', 'title': '應徵成功', 'lines': ['應徵成功', '謝謝'], 'fields': []}
SUBMIT_HONEST = {'submitted': True, 'clicked': True, 'confirm_url': THANKS['url'], 'confirm_text': '應徵成功',
                 'tab_id': '7', 'problems': []}
SUBMIT_LIES = {
    'confirm_url': ('https://www.104.com.tw/other', ['確認頁網址', '/other', THANKS['url']]),
    'confirm_text': ('Application received', ['確認頁的字', 'Application received', '頁面上沒有這句話']),
    'tab_id': ('9', ['分頁', '9', '分頁 7']),
}
PRE_SUBMIT_HONEST = {'delivery': {'method': 'platform_profile'}, 'profile_attachments': [], 'problems': []}
PRE_SUBMIT_LIES = {
    'delivery.profile_kind': ('custom', ['固定版還是客製版', 'custom', 'fixed']),
    'delivery.profile_url': (OTHER, ['用的平台履歷', 'vno=2', MINE]),
    'fixed_profile.url': (OTHER, ['固定平台履歷', 'vno=2', MINE]),
    'problems': (['平台履歷多出附件'], ['卡住', '平台履歷多出附件']),
}


# ---- 其他 7 種流程(#317):真相是程式交給 agent 的原文;一列一列的交件單,謊話填在第一列 ----
A, B = 'https://ex.test/job/a', 'https://ex.test/job/b'
FLOWS = {
    # 刊登日期:程式從頁面挑出跟日期有關的那幾行交給它
    'posted_at': dict(
        honest={'dates': [{'url': A, 'posted_at': '2026-09-07', 'source': 'Posted on'}],
                'inaccessible': [{'url': B, 'reason': '原文沒有刊登日期', 'need': ''}]},
        truth=lambda: gate.Truth(given={A: 'Posted on September 7, 2026\nUpdated 3 days ago', B: 'Apply by Oct 1'},
                                 fetched_on={A: '2026-09-20', B: '2026-09-20'}),
        lies={'dates.url': ('https://ex.test/job/other', ['職缺網址', 'job/other', '這一輪沒有交給它']),
              'dates.posted_at': ('2026-09-08', ['刊登日期', '2026-09-08', '2026-09-07']),
              'dates.source': ('First published', ['日期欄位的名稱', 'First published', '原文裡沒有']),
              'inaccessible.url': ('https://ex.test/job/other', ['職缺網址', 'job/other', '這一輪沒有交給它'])}),
    # 職缺關了沒:程式抓好的頁面原文交給它判斷;關了沒是 agent 判斷,它抄的那一句要在原文裡
    'link_status': dict(
        honest={'jobs': [{'id': 'J1', 'status': 'closed', 'quote': 'This position has been filled', 'reason': '徵到人了'},
                         {'id': 'J2', 'status': 'live', 'reason': ''}]},
        truth=lambda: gate.Truth(given={'J1': 'Data Engineer. This position has been filled.', 'J2': 'Apply now'}),
        lies={'jobs.id': ('J9', ['職缺代號', 'J9', '這一輪沒有交給它']),
              'jobs.quote': ('Applications closed', ['頁面原文', 'Applications closed', '原文裡沒有']),
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
    # 準備履歷:程式抓好的 JD 原文交給它挑履歷;JD 是哪種語言是 agent 判斷(程式不猜),只核對寫法
    'prepare': dict(
        honest={'resume': 'general', 'lang': 'zh', 'why': 'JD 要資料管線', 'content_problem': False,
                'real_title': '資深資料工程師'},
        variants={'closed': {'skip': True, 'reason': '職缺已關:頁面寫已額滿', 'quote': '本職缺已額滿'},
                  'old': {'variant': 'general', 'lang': 'zh', 'why': '舊的寫法'}},
        truth=lambda: gate.Truth(A, given={A: '資深資料工程師 好公司 負責資料管線。本職缺已額滿。'},
                                 resumes=[{'id': 'general', 'files': {'zh': 'r/zh.pdf', 'en': 'r/en.pdf'}}]),
        lies={'real_title': ('廚師', ['頁面上的名字', '廚師', 'JD 原文裡沒有']),
              'resume': ('secret', ['挑的履歷', 'secret', '不是勾選']),
              'variant': ('secret', ['挑的履歷(舊寫法)', 'secret', '不是勾選']),
              'lang': ('klingon', ['語言', 'klingon', '只能是']),
              'reason': ('抓不到 JD:讀不到', ['跳過的原因', '抓不到 JD', '程式有給它 JD 原文'], 'closed'),
              'quote': ('徵才中', ['頁面原文', '徵才中', 'JD 原文裡沒有'], 'closed'),
              'skip': (True, ['跳過', '沒寫原因'])},
        judged={'resume': 'general', 'lang': 'zh', 'content_problem': False, 'variant': ('general', 'old'),
                'skip': (True, 'closed')}),
    # 找缺:agent 自己上網找的候選;程式之後抓回每一頁,摘錄要在頁面原文裡
    'research_search': dict(
        honest={'candidates': [{'url': A, 'title': '資料工程師', 'company': '好公司', 'why': '做資料管線',
                                'via': '搜尋', 'angle': '資料管線', 'jd_excerpt': '負責資料管線'}]},
        truth=lambda: gate.Truth(given={A: '資料工程師 好公司 負責資料管線與排程。'}),
        lies={'candidates.url': ('https://ex.test/jobs/search?keyword=data', ['職缺網址', 'keyword=data',
                                                                              '不是單一職缺頁']),
              'candidates.jd_excerpt': ('負責帶領百人團隊', ['JD 摘錄', '負責帶領百人團隊', '原文裡沒有'])},
        judged={'candidates.angle': '資料管線'}),
    # 找缺的判斷:程式抓回的 JD 原文、附的舊卡和他的原話交給它判斷
    'research_judge': dict(
        honest={'jobs': [{'id': 'J1', 'title': '資料工程師', 'company': '好公司', 'keep': True, 'fit': 4,
                          'cite': ['c1'], 'why': '他喜歡資料管線',
                          'risk': {'kind': '', 'why': ''},
                          'reasons': [{'text': '他說喜歡管線', 'citation': '很喜歡資料管線', 'basis': '使用者原話'}],
                          'resume': 'general', 'lang': 'zh', 'pick_why': '中文 JD', 'cat': '工程',
                          'card': {'fit': '管線經驗對上', 'co': '做電商', 'loc': '台北', 'deadline': '2026-10-31',
                                   'salary': '月薪 50,000 起', 'bar': '三年', 'posted': '無', 'ammo': '直投'}}]},
        variants={'ghost': {'jobs': [{'id': 'J1', 'keep': False, 'risk': {'kind': 'ghost', 'why': '刊登已經 200 天'}}]},
                  'old': {'jobs': [{'id': 'J1', 'variant': 'general', 'lang': 'zh', 'pick_why': '舊的寫法'}]}},
        truth=lambda: gate.Truth(
            given={'J1': '資料工程師 好公司 負責資料管線。月薪 50,000 起。應徵截止 2026/10/31。'},
            cites={'J1': {'c1', 'c2'}},
            sources={'J1': {'JD 原文': ['資料工程師 好公司 負責資料管線'], '使用者原話': ['很喜歡資料管線'], '偏好筆記': ['']}},
            flags={'J1': ['刊登已經 200 天(超過 90 天):可能是幽靈職缺']},
            resumes=[{'id': 'general', 'files': {'zh': 'r/zh.pdf'}}]),
        lies={'jobs.id': ('J7', ['職缺代號', 'J7', '這一輪沒有交給它']),
              'jobs.title': ('廚師', ['頁面上的職稱', '廚師', '原文裡沒有']),
              'jobs.company': ('別家', ['頁面上的公司', '別家', '原文裡沒有']),
              'jobs.cite': (['c9'], ['引用的舊卡', 'c9', '程式沒附給它']),
              'jobs.reasons': ([{'text': '他說過', 'citation': '他最愛管理職', 'basis': '使用者原話'}],
                               ['理由的引用', '他最愛管理職', '找不到']),
              'jobs.resume': ('secret', ['挑的履歷', 'secret', '不是勾選']),
              'jobs.variant': ('secret', ['挑的履歷(舊寫法)', 'secret', '不是勾選']),
              'jobs.lang': ('klingon', ['語言', 'klingon', '只能是']),
              'jobs.risk': ({'kind': 'scam', 'why': '要先匯保證金'}, ['詐騙或幽靈缺', '保證金', '都沒有']),
              'jobs.card': ({'deadline': '2026-12-01', 'salary': '月薪 90,000'},
                            ['卡片摘要', '截止日 2026-12-01', '薪資', '90000'])},
        judged={'jobs.keep': True, 'jobs.fit': 4, 'jobs.cat': '工程', 'jobs.resume': 'general', 'jobs.lang': 'zh',
                'jobs.variant': ('general', 'old'), 'jobs.risk': ({'kind': 'ghost', 'why': '刊登已經 200 天'}, 'ghost'),
                'jobs.card': {'fit': '管線經驗對上', 'co': '做電商', 'loc': '台北', 'deadline': '2026-10-31',
                              'salary': '月薪 50,000 起', 'bar': '三年', 'posted': '無', 'ammo': '直投'}}),
    # 查應徵進度:程式複製的信和平台應徵紀錄全文交給它;信算哪一種、要他做的事是 agent 判斷
    'reply': dict(
        honest={'checked': [A],
                'findings': [{'url': A, 'source_ref': 'email:thr001', 'source': 'Gmail', 'date': '2026-09-03',
                              'subject': '面試邀請', 'summary': '邀請面試', 'kind': 'interview',
                              'link': 'https://mail.google.com/mail/u/0/#all/thr001', 'quote': '邀請您參加面試',
                              'reason': '', 'todo': '回覆可面談時段'}],
                'job_ids': [{'id': 'abc123', 'url': 'https://www.104.com.tw/job/abc123', 'platform': '104',
                             'applied_at': '2026-09-01', 'title': '資料工程師'}],
                'inaccessible': [{'source': 'LinkedIn', 'reason': '要登入', 'need': '登入後重查', 'jobs': [B]}]},
        truth=lambda: gate.Truth(
            cards={A, B}, source_types={'email:thr001': 'email', 'application_record:104': 'application_record'},
            texts={'email:thr001': '面試邀請\n2026年9月3日 好公司人資\n您好,邀請您參加面試,請回覆可面談時段。',
                   'application_record:104': '應徵紀錄\n資料工程師 abc123 2026/09/01 已讀'}),
        lies={'checked': (['https://ex.test/job/zzz'], ['查完的卡', 'job/zzz', '不是這一輪查的卡']),
              'findings.url': ('https://ex.test/job/zzz', ['卡片網址', 'job/zzz', '不是這一輪查的卡']),
              'findings.source_ref': ('email:madeup', ['來源代號', 'email:madeup', '程式沒給它這個來源']),
              'findings.source_type': ('application_record', ['來源種類', 'application_record', 'email(程式照']),
              'findings.date': ('2026-09-09', ['日期', '2026-09-09', '原文裡沒有這一天']),
              'findings.subject': ('錄取通知', ['標題', '錄取通知', '原文裡沒有']),
              'findings.link': ('https://mail.google.com/mail/u/0/#all/other99', ['原文連結', 'other99',
                                                                                 'email:thr001 那一封']),
              'findings.quote': ('恭喜您錄取', ['信裡那一句', '恭喜您錄取', '原文裡沒有']),
              'findings.reason': ('經驗不足', ['拒絕理由', '經驗不足', '原文裡沒有']),
              'findings.kind': ('hired', ['信算哪一種', 'hired', 'confirm、reject']),
              'job_ids.id': ('zzz999', ['職缺代號', 'zzz999', '應徵紀錄裡沒有這個代號']),
              'job_ids.url': ('https://www.104.com.tw/job/zzz999', ['職缺連結', 'zzz999', '應徵紀錄裡沒有這個職缺']),
              'job_ids.platform': ('linkedin', ['平台', 'linkedin', '程式讀了 104']),
              'job_ids.applied_at': ('2026-08-15', ['應徵日期', '2026-08-15', '沒有這一天']),
              'inaccessible.jobs': (['https://ex.test/job/zzz'], ['影響到的卡', 'job/zzz', '不是這一輪查的卡'])},
        judged={'findings.kind': 'interview', 'findings.todo': '回覆可面談時段'}),
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
        self.tmp = tempfile.mkdtemp(prefix='gate-')
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
        shutil.rmtree(self.tmp, ignore_errors=True)

    def truth(self, page=PAGE):
        # 下載回來逐位元組比檔的那一段(job 給了才做)在 test_apply_profile_attachments 測
        return gate.Truth(URL, None, self.fb, page=copy.deepcopy(page), tab_id='7', decision=dict(DECISION))

    def test_an_honest_handoff_sheet_is_accepted(self):
        verdict = gate.inspect('fill', copy.deepcopy(HONEST), self.truth())
        self.assertEqual(verdict.problems, [])
        self.assertEqual(verdict.facts['delivery'], {'method': 'platform_profile'})
        verdict = gate.inspect('fill', copy.deepcopy(DIRECT), self.truth(DIRECT_PAGE))
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
        lies = {'fill': LIES, 'submit': SUBMIT_LIES, 'pre_submit': PRE_SUBMIT_LIES}
        lies.update({kind: flow['lies'] for kind, flow in FLOWS.items()})
        for kind, table in gate.SHEETS.items():
            checked = {n for n, c in table.items() if c.how in (gate.CHECK, gate.DECIDED)}
            missing = checked - set(lies[kind]) - set(ELSEWHERE) - {n.split(':')[1] for n in ELSEWHERE
                                                                      if n.startswith(kind + ':')}
            if kind == 'pre_submit':
                missing -= {'delivery.method'}     # 投遞方式那一格就是逐位元組比附件(profile_attachments 那一條測)
            self.assertEqual(missing, set(), f'{kind} 這幾格沒有說謊的測試')

    def test_submit_and_pre_submit_lies_are_stopped(self):
        for kind, honest, lies, page in (('submit', SUBMIT_HONEST, SUBMIT_LIES, THANKS),
                                         ('pre_submit', PRE_SUBMIT_HONEST, PRE_SUBMIT_LIES, None)):
            self.assertEqual(gate.inspect(kind, copy.deepcopy(honest), self.truth(page)).problems, [], kind)
            for name, (lie, words) in lies.items():
                with self.subTest(kind=kind, cell=name):
                    sheet = copy.deepcopy(honest)
                    _set(sheet, name, lie)
                    said = '\n'.join(gate.inspect(kind, sheet, self.truth(page)).problems)
                    for w in words:
                        self.assertIn(w, said)

    def test_every_checked_cell_is_lied_about_once_and_each_lie_is_stopped(self):
        checked = {n for n, c in gate.FILL.items() if c.how in (gate.CHECK, gate.DECIDED)}
        self.assertEqual(checked - set(LIES) - set(ELSEWHERE), set(), '這幾格沒有說謊的測試')
        for name, (lie, words, *kind) in LIES.items():
            with self.subTest(cell=name):
                direct = kind == ['direct']
                sheet = copy.deepcopy(DIRECT if direct else HONEST)
                _set(sheet, name, lie)
                verdict = gate.inspect('fill', sheet, self.truth(DIRECT_PAGE if direct else PAGE))
                self.assertFalse(verdict.ok, f'{name} 說謊沒被擋下')
                said = '\n'.join(verdict.problems)
                for w in words:
                    self.assertIn(w, said)

    def test_marking_the_fixed_resume_as_custom_is_stopped_with_the_reason(self):
        sheet = copy.deepcopy(HONEST)
        sheet['delivery'] = {'method': 'platform_profile', 'profile_kind': 'custom', 'profile_url': MINE}
        verdict = gate.inspect('fill', sheet, self.truth())
        self.assertIn('交件單「固定版還是客製版」:agent 說 custom,實際是 fixed(程式照這張卡決定的)', verdict.problems)

    def test_cells_nobody_registered_never_reach_the_program(self):
        sheet = dict(copy.deepcopy(HONEST), chrome={'pid': 1}, secret_decision='submit it')
        verdict = gate.inspect('fill', sheet, self.truth())
        self.assertEqual(verdict.problems, [])
        self.assertNotIn('chrome', verdict.facts)
        self.assertNotIn('secret_decision', verdict.facts)
        self.assertEqual(sorted(verdict.unregistered), ['chrome', 'secret_decision'])


class OtherFlowsLie(unittest.TestCase):
    """其他 7 種流程:照實寫的交件單收下;每一個要核對的格子各填錯一次,那一列被擋下、原因寫對,別列照收。"""

    def test_an_honest_sheet_of_every_flow_is_accepted(self):
        for kind, flow in FLOWS.items():
            for name, sheet in dict(flow.get('variants') or {}, honest=flow['honest']).items():
                with self.subTest(kind=kind, sheet=name):
                    verdict = gate.inspect(kind, copy.deepcopy(sheet), flow['truth']())
                    self.assertEqual(verdict.problems, [])

    def test_every_lie_in_every_flow_is_stopped_with_the_reason(self):
        for kind, flow in FLOWS.items():
            for name, (lie, words, *variant) in flow['lies'].items():
                with self.subTest(kind=kind, cell=name):
                    sheet = copy.deepcopy(flow['variants'][variant[0]] if variant else flow['honest'])
                    _set(sheet, name, lie)
                    verdict = gate.inspect(kind, sheet, flow['truth']())
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
        judged = {kind: flow['judged'] for kind, flow in FLOWS.items() if flow.get('judged')}
        for kind, table in gate.SHEETS.items():
            for name, cell in table.items():
                if cell.how == gate.JUDGED:
                    self.assertIn(name, judged.get(kind, {}), f'{kind} 的 {name} 是 agent 判斷,要有測試')
        for kind, cells in judged.items():
            for name, value in cells.items():
                value, *variant = value if isinstance(value, tuple) else (value,)
                sheet = FLOWS[kind]['variants'][variant[0]] if variant else FLOWS[kind]['honest']
                verdict = gate.inspect(kind, copy.deepcopy(sheet), FLOWS[kind]['truth']())
                with self.subTest(kind=kind, cell=name):
                    head, _, sub = name.partition('.')
                    if head in (gate.ROWS.get(kind) or {}):
                        row = verdict.rows[head][0]
                        self.assertEqual(row.judged.get(sub), value)
                        self.assertNotIn(sub, row.facts)
                    else:
                        self.assertEqual(verdict.judged.get(name), value)
                        self.assertNotIn(name, verdict.facts)

    def test_feedback_from_one_card_is_not_a_repeated_problem(self):
        sheet = {'reports': [{'issue': '成果', 'recommendation': 'x', 'feedback_ids': ['f1', 'f3']}]}
        verdict = gate.inspect('customize', sheet, FLOWS['customize']['truth']())
        self.assertIn('只有一張卡提到', '\n'.join(verdict.problems))

    def test_a_date_written_as_days_ago_counts_from_the_day_the_page_was_fetched(self):
        flow = FLOWS['posted_at']
        sheet = {'dates': [{'url': A, 'posted_at': '2026-09-17', 'source': 'Updated'}]}
        self.assertEqual(gate.inspect('posted_at', sheet, flow['truth']()).problems, [])


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
            said['pre_submit'] = [run.pre_submit_prompt(URL, job, fb, self.id(), '/tmp/x', door, after_fill=False)[0]]
        for kind, prompts in said.items():
            table = gate.SHEETS[kind]
            known = set(table) | {n.split('.')[0] for n in table} | {i for c in table.values() for i in c.items}
            known |= {n.split('.')[1] for n in table if '.' in n}
            for text in prompts:
                asked = set(re.findall(r'"([a-z_]+)"\s*:', text))
                asked |= {m for m in re.findall(r'\b(?:delivery|profile|posting|fixed_profile)\.([a-z_]+)', text)}
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
                                                   {A: '2026-09-20'})],
                'link_status': seen,
                'suggest_cats': [__import__('suggest_cats').prompt('/tmp/x.json', {}, [])],
                'customize': [__import__('customize').build_prompt(A, '資料工程師', [], [], '/tmp/r.json', 'JD')],
                'prepare': [__import__('cut_tailor').prompt(
                    [(A, '資料工程師')], {A: ('ok', page)}, out_dir='/tmp/prep',
                    resumes=[{'id': 'general', 'name': '通用', 'files': {'zh': 'r/zh.pdf', 'en': 'r/en.pdf'}}])],
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
            decided = {n.split('.')[-1] for n, c in table.items() if c.how == gate.DECIDED}
            for text in prompts:
                with self.subTest(kind=kind):
                    asked = set(re.findall(r'"([a-z_]+)"\s*:', text))
                    self.assertEqual(asked - known, set(), f'{kind} 的指示叫 agent 寫這幾格,安檢門沒登記核對方式')
                    # 程式已經知道答案的格子(程式決定):指示裡不叫 agent 寫
                    self.assertEqual(asked & decided, set(), f'{kind} 的指示還叫 agent 寫程式已經決定的格子')

    def test_every_other_flow_is_covered(self):
        """7 種流程每一種都有交件單登記、說謊測試和指示測試(新加一種流程就要補齊)。"""
        flows = set(gate.SHEETS) - {'fill', 'pre_submit', 'submit'}
        # 7 種流程:查應徵進度、準備履歷、客製版、找缺(找、判)、職缺關了沒、刊登日期、分類建議
        self.assertEqual(flows, {'reply', 'prepare', 'customize', 'research_search', 'research_judge', 'link_status',
                                 'posted_at', 'suggest_cats'})
        self.assertEqual(flows, set(FLOWS))
        self.assertEqual(flows, set(self.other_prompts()))
        self.assertEqual(flows, set(gate.FILES) - {'fill', 'pre_submit', 'submit'})


if __name__ == '__main__':
    unittest.main()
