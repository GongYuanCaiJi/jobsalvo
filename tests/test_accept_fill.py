"""幫你填表的驗收:從 fill.py 入口(--stage fill、fix、submit、confirm)與 agent 叫的 form_plan 入口,加假瀏覽器邊界,
跑 acceptance_inputs 的組合,斷言行為清單 F 段與 A 段在填表上的那幾條。句子照 tools/web/marks.py 的 TEMPLATES 一字不差。
"""
import contextlib
import copy
import dataclasses
import datetime
import itertools
import json
import os
import pathlib
import re
import shutil
import threading
import time
import unittest
from unittest import mock
from collections import Counter

import acceptance_expect as E
import acceptance_inputs as I
from acceptance_home import LANG, PLATFORM, PROFILE_URL, Home
from fake_browser import FakeBrowser, Script, Tab
import agent_report
import evidence
import board_status
import form_record
import profile_sync
import ship
import fill
import form_plan
import sync

URL = 'https://jobs.example/jobs/1'
TITLE, COMPANY = 'Engineer', 'Acme'
QUESTION, ANSWER = 'Work authorization', 'Yes'
REQUIREMENT = 'Please attach a portfolio PDF'
JOB = {'id': URL, 'target': f'{TITLE} - {COMPANY}', 'title': TITLE, 'company': COMPANY,
       'resume': {'recommend': 'one', 'lang': LANG}}
APPLY = {'at': 'T0', 'round_id': 'r0', 'session': 'sess-1', 'tab_id': '7:p1', 'agent_id': 'fake-agent',
         'workspace': {'id': 7, 'name': 'accept', 'page': 'p1'},
         'delivery': {'method': 'direct_upload'}}   # 填好的卡一定記了這次上傳方式(form_record.approval_problem)
BANK = [{'k': 'k1', 'q': QUESTION, 'v': ANSWER, 'zh': '是', 'at': '2026-01-01'}]   # 英文答案附中文(form_record.validate)
FORM_ROWS = [{'q': 'Full name', 'src': 'rz', 'v': 'Ann'}, {'q': QUESTION, 'src': 'bank', 'k': 'k1'}]
PAGE_CODES = {'PAGE_UNREADABLE', 'PAGE_TOO_LONG', 'PAGE_NO_TEXT', 'SHOT_MISSING'}
CHANGED = '填好後頁面有變化，列出供你查看'
FOUND, ALREADY, ON_PAGE, NOT_FOUND, NOT_COMPARED = (
    '原稿一字不差找到', '原本就有', '原稿在頁上一字不差讀到，那一頁沒逐處比',
    '原稿沒在頁上一字不差找到', '沒比到')


def expect(test, behavior, actual, wanted, case=()):
    test.assertEqual(actual, wanted, f'{behavior} {case}')


def sections(prompt):
    """指示的每一段:【標題】→ 內容。"""
    return dict(re.findall(r'【([^】]+)】\n(.*?)(?=\n\n【|\Z)', prompt, re.S))


def form_tab(*parts, state=I.READ, shot=True, label='p1', url=URL + '/apply'):
    return Tab(label, url, state, parts or (f'{TITLE} at {COMPANY}', QUESTION), shot)


def sheet(**cells):
    """一張乾淨的填表交件單;cells 蓋掉其中幾格。"""
    out = {'same_job': 'true', 'delivery': 'direct_upload', 'profile': '', 'fields': [], 'uploaded': [],
           'unmet': [], 'problems': [], 'needs_user': '', 'stopped': '', 'submitted': 'false'}
    out.update(cells)
    return out


def filled(build=None, before=None, after=None, end=I.HANDED, extra=()):
    """填表那一輪:開申請表、拍改前、(extra)、拍改後、交件。build(印出的材料) → 交件單。"""
    before = before or (form_tab(),)
    after = after or (form_tab(f'{TITLE} at {COMPANY}', QUESTION, ANSWER),)
    def cells(printed):
        out = build(printed) if build else sheet()
        # 沒特別寫 form_tab 的測試,agent 照常寫它填的那個分頁(拍改後最後一頁)
        return out if not isinstance(out, dict) or 'form_tab' in out else dict(out, form_tab=after[-1].tab_id if after else '')
    return Script((('open', before), ('before', before), *extra, ('after', after)), cells, end)


class FillCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.home = Home(self, resume='# 經歷\n新的一行\n', attachments=True)

    def start(self, state='todo', card=None, bank=BANK, job=None, registered=False, status=True):
        path = os.path.join(self.home.home, 'profiles.json')
        if os.path.exists(path):
            os.remove(path)
        if registered:
            profile_sync.remember(PLATFORM, LANG, 'one', PROFILE_URL)
        fb = {URL: dict({'app': 'ship', 'ds': state}, **copy.deepcopy(card or {}))}
        if bank is not None:
            fb['__ans__'] = copy.deepcopy(bank)
        extra = {'status': {'schema_version': board_status.SCHEMA_VERSION, 'issues': []}} if status else {}
        self.home.write_board([dict(JOB, **(job or {})), {'id': 'https://jobs.example/jobs/2', 'target': 'Other'}],
                              fb, **extra)

    def go(self, stage, *scripts, note='', browser=None):
        browser = browser or FakeBrowser(*scripts)
        code = fill.main(['--stage', stage, '--url', URL, '--board', self.home.board,
                          *(['--note', note] if note else [])], browser=browser)
        return code, browser

    def card(self):
        return self.home.fb()[URL]

    def state(self):
        import delivery_core
        return delivery_core.state(self.card())

    def confirmed(self, card=None):
        """停在「你已確認」的一張卡:看板記下使用者按確認時的那一輪、那一頁、那個工作區、每一題的答案。"""
        seeded = dict({'apply': copy.deepcopy(APPLY), 'form': {'plat': PLATFORM, 'f': FORM_ROWS, 'at': 'T0'}},
                      **(card or {}))
        self.start('confirmed', seeded)
        import board_doc

        def approve(fb):
            fb[URL]['approve'] = form_record.approval(fb, URL, '2026-01-03')
        board_doc.set_fb(approve, live=self.home.board)


class StateTest(FillCase):
    """F-04、F-16、F-17、F-22、F-28、A-16、A-21:填表與送出每一種結束 × 交件單每一種說法,卡停在狀態表說的那一格。"""

    FILL = {'clean': {}, 'problem': {'problems': [{'text': '填不了這格'}]}, 'needs_user': {'needs_user': '要本人登入'},
            'stopped': {'stopped': '停在驗證碼'}, 'other_job': {'same_job': 'false'}, 'submitted': {'submitted': 'true'},
            'untranslated': {'fields': [{'q': 'Visa', 'src': 'bank', 'value': 'No', 'why': '照履歷推的'}]}}
    SAID = {'problem': '填不了這格', 'needs_user': 'agent 說要你本人處理：要本人登入',
            'stopped': 'agent 停下：停在驗證碼',
            'other_job': 'agent 說這一頁不是卡片上那個職缺',
            'untranslated': '這一輪沒完成'}

    def test_fill_rounds_end_where_the_state_table_says(self):
        for end, outcome in itertools.product(I.ENDS, self.FILL):
            case = (end, outcome)
            self.start('todo')
            self.go('fill', filled(lambda _p, cells=self.FILL[outcome]: sheet(**cells), end=end))
            got = self.state()
            fb = self.home.fb()
            reports = [r for r in agent_report.open_items(fb) if r['from'] == agent_report.FROM_APPLY and r.get('job') == URL]
            # 沒交出核對得到的那一頁,都算 agent 沒做完:原因照實留在卡上,第一輪不進要你做(#807)
            expect(self, '#807', reports, [], case)
            if end != I.HANDED:
                expect(self, '#783', bool(fb[URL]['apply']['issues']), True, case)
            if end == I.UNAVAILABLE or end not in (I.HANDED, I.BROKEN_SHEET):
                expect(self, 'F-22', got, 'nopage', case)
                expect(self, 'F-22', form_record.approval_problem(self.home.fb(), URL, None) is None, False, case)
                continue
            want = ('stuck' if end == I.BROKEN_SHEET else 'unsure' if outcome == 'submitted'
                    else 'parked' if outcome == 'clean' else 'stuck')
            expect(self, 'F-04' if outcome == 'clean' else 'F-17' if outcome == 'submitted' else 'A-21', got, want, case)
            if end == I.HANDED and outcome in self.SAID:
                shown = ' '.join(self.card()['apply']['issues'])
                expect(self, 'F-28' if outcome == 'other_job' else 'A-21', self.SAID[outcome] in shown, True, case)

    def test_submit_rounds_follow_the_agents_reading_and_the_verified_cite(self):
        receipt = 'Application received'
        for end, said in itertools.product(I.ENDS, I.SUBMIT_SAID):
            case = (end, said)
            self.confirmed()

            def build(printed, said=said):
                if said == 'silent':
                    return None
                out = {'submitted': 'false' if said == 'not_submitted' else 'true', 'needs_user': '', 'stopped': ''}
                if said == 'submitted_cited':
                    out['evidence'] = printed[-1].cite(receipt)
                elif said == 'submitted_uncited':
                    out['evidence'] = {'material_id': 'not-this-round', 'ids': [1]}
                return out
            page = (form_tab(receipt),)
            self.go('submit', Script((('before', page), ('after', page)), build, end))
            got = self.state()
            if end == I.UNAVAILABLE:
                expect(self, 'F-17', got, 'confirmed', case)
                continue
            handed = end == I.HANDED and said != 'silent'
            want = ('sent' if handed and said == 'submitted_cited' else
                    'stuck' if handed and said == 'not_submitted' else 'unsure')
            expect(self, 'F-16' if want != 'unsure' else 'F-17', got, want, case)
            if want == 'sent':
                expect(self, 'F-16', self.card()['apply']['sent']['source'], E.PROGRAM, case)
            if want == 'unsure':        # 看板「幾月幾日按了送出」、查應徵進度從那天查起、等滿一小時,都讀這個時間
                at = self.card()['apply']['submit_fail']['at']
                expect(self, 'F-17', datetime.datetime.fromisoformat(at).date(), datetime.date.today(), case)


class ApplyRecordTest(FillCase):
    """I-09、F-23、F-28、A-21:每一輪寫進卡上的 apply 整筆照這一輪:哪一輪、對話與 agent、工作區與停的那一頁(收尾照它放掉)、
    要你處理的那一頁、交件單每一格;換過履歷、送出結果不明的記號留著;卡上記的交件內容進歷程。"""

    PREVIOUS = dict(APPLY, stale=1, submit_fail={'at': 'T-1'}, tab_id='7:p3',
                    workspace={'id': 7, 'name': 'accept', 'page': 'p3', 'handoff_page': 'p9'})

    def run_round(self, how):
        if how == 'first':
            self.start('todo')
            profile_sync.remember(PLATFORM, LANG, 'one', PROFILE_URL, name='英文履歷')
            cells = sheet(fields=[{'q': QUESTION, 'src': 'bank', 'value': ANSWER, 'k': 'k1'}], uploaded=[{'name': 'report.pdf'}],
                          profile='英文履歷')
            return self.go('fill', filled(lambda _p: cells, after=(form_tab(f'{TITLE} at {COMPANY}', QUESTION, ANSWER, label='p2'),)))
        previous = dict(self.PREVIOUS, **{'other_tab': {'tab_id': '8:p1', 'workspace': {'id': 7, 'name': 'accept'}},
                                          'kept_page': {'tab_id': '8:p1'}}.get(how, {}))
        self.start('parked', {'apply': copy.deepcopy(previous)})
        cells = sheet() if how == 'kept_page' else sheet(
            same_job='false', problems=[{'text': '填不了這格'}], needs_user='要本人登入', stopped='停在驗證碼',
            needs_user_tab='7:p2', needs_user_quote=QUESTION)
        page = (form_tab(), form_tab(label='p2'))
        return self.go('fix', Script((('open', page), ('before', page)), cells), note='改電話')

    def test_the_whole_apply_record(self):
        """kept_page:這一輪沒拍改後、也沒要你處理的頁:不知道填表的是哪一頁,分頁留空、不沿用上一輪的(F-20),上一輪要你處理的頁不留。"""
        import config as cf
        from unittest import mock
        for how in ('first', 'fix', 'other_tab', 'kept_page'):
            with mock.patch.dict(cf.C, {'agent': {'agents': [{'id': 'fake-agent', 'runtime': 'claude'}]}}):
                _code, _browser = self.run_round(how)
            card = self.card()
            apply = {key: value for key, value in card['apply'].items() if key not in ('fill_changes', 'at')}
            round_id = apply['round_id']
            first, quiet = how == 'first', how in ('first', 'kept_page')
            wanted = {
                'stage': 'fill' if first else 'fix', 'session': 'fake-session', 'round_id': round_id, 'evidence': round_id,
                'agent_id': 'fake-agent', 'runtime': 'claude',
                'workspace': {'id': 7, 'name': 'accept',
                              'page': {'first': 'p2', 'fix': '', 'other_tab': '', 'kept_page': ''}[how],
                              **({} if quiet else {'handoff_page': 'p2'})},
                'tab_id': {'first': '7:p2', 'fix': '', 'other_tab': '', 'kept_page': ''}[how],
                'delivery': {'method': 'direct_upload', 'profile_kind': 'fixed'}, 'profile': '英文履歷' if first else '',
                'uploaded': ['report.pdf'] if first else [], 'unmet': [],
                'problems': [] if quiet else ['填不了這格'], 'needs_user': '' if quiet else '要本人登入',
                'stopped': '' if quiet else '停在驗證碼',
                'issues': [] if quiet else ['填不了這格', 'agent 停下：停在驗證碼',
                                            'agent 說要你本人處理：要本人登入',
                                            f'要你本人處理的這一頁「{URL}/apply」已交給你；agent 說：Work authorization',
                                            fill.OTHER_JOB],
                'field_observations': [{'q': QUESTION, 'code': 'ON_PAGE_NOT_COMPARED', 'marker': ON_PAGE},
                                       {'q': '上傳檔:report.pdf', 'code': 'NOT_FOUND_VERBATIM',
                                        'marker': NOT_FOUND, 'said': 'report.pdf',
                                        'shot': f'{round_id}/field-2.png', 'shot_cropped': False}]
                # 改後拍在另一個分頁:沒逐處比
                if first else [],
                **({'ev': apply.get('ev')} if first else {'stale': 1, 'submit_fail': {'at': 'T-1'}})}
            expect(self, 'I-09 F-23', apply, wanted, how)
            expect(self, 'F-09', card['apply']['fill_changes']['heading'], '這一輪填表的頁面紀錄', how)
            if first:
                expect(self, 'I-09', apply['ev'].startswith(f'{round_id}/snap-after-'), True, how)

    def test_a_result_arriving_after_the_card_was_sent_goes_to_history(self):
        """P-21、F-16:填表跑的時候平台應徵紀錄找到已投遞:這一輪的結果收進投遞歷史(交件單說的),卡不動。"""
        import board_doc
        import delivery_core
        self.start('todo')

        def sent_meanwhile(_browser):
            board_doc.set_fb(lambda fb: delivery_core.fire(fb, URL, 'platform_found', by='platform', at='2026-01-02',
                                                            sent_at='2026-01-02', rec=f'{URL}:2026-01-02'), live=self.home.board)
            return ''
        cells = sheet(needs_user='要本人登入', stopped='停在驗證碼',
                      fields=[{'q': 'Why us', 'src': 'bank', 'value': 'Robots', 'why': '履歷', 'zh': '機器人'}])
        self.go('fill', filled(lambda _p: cells, extra=(('tool', sent_meanwhile),)))
        expect(self, 'F-16', ('form' in self.card(), self.home.fb()['__ans__']), (False, BANK))
        late = self.card()['history'][-1]
        expect(self, 'F-16', (self.state(), late['event'], late['late'], late['evidence'], late['apply']['issues']),
               ('sent', 'fill_bad', 1, {'at': late['evidence']['at'], 'said': 'false', 'needs_user': '要本人登入',
                                        'stopped': '停在驗證碼'}, ['agent 停下：停在驗證碼', 'agent 說要你本人處理：要本人登入']))

    def test_submitting_releases_the_page_the_fill_stopped_on(self):
        self.run_round('first')
        import board_doc

        def approve(fb):
            fb[URL]['ds'] = 'confirmed'
            fb[URL]['approve'] = form_record.approval(fb, URL, '2026-01-03')
        board_doc.set_fb(approve, live=self.home.board)
        page = (form_tab('Application received', label='p2'),)
        self.go('submit', Script((('before', page), ('after', page)),
                                 lambda printed: {'submitted': 'true', 'evidence': printed[-1].cite('Application received'),
                                                  'needs_user': '', 'stopped': ''}))
        expect(self, 'F-16', (self.state(), self.home.fb().get('__browser_cleanup__')),
               ('sent', [{'workspace': {'id': 7, 'name': 'accept', 'page': 'p2'}, 'reason': 'submit_ok'}]))


class PageStateTest(FillCase):
    """F-09、A-25、A-04:申請表那一頁讀不到、太長、沒字、截圖沒取得,照實標程式沒比到,卡照交件單停,不由程式判失敗。"""

    def test_unreadable_form_pages_are_listed_not_judged(self):
        for state, shot in itertools.product(I.TAB_STATES, I.SHOTS):
            case = (state, shot)
            self.start('todo')
            before = (form_tab(state=state, shot=shot),)
            after = (form_tab(f'{TITLE} at {COMPANY}', QUESTION, ANSWER, state=state, shot=shot),)
            self.go('fill', filled(before=before, after=after))
            expect(self, 'A-25', self.state(), 'parked', case)
            report = self.card()['apply']['fill_changes']
            wanted = Counter(code for tab in (*before, *after) for code in E.page_marks(tab))
            got = Counter(m['code'] for m in report['marks'] if m['code'] in PAGE_CODES)
            expect(self, 'F-09', got, wanted, case)
            expect(self, 'A-27', [m['source'] for m in report['marks']], [E.source_of(m['code']) for m in report['marks']],
                   case)


class FieldTest(FillCase):
    """F-05、F-06、F-08、F-25:每一格照原字記進卡上的表單;新答案標推論、附那一刻拍到有那一題的頁;
    題目一字不差才接到答案庫同一條;答案有沒有一字不差在頁上照實標。"""

    def test_every_field_row_is_recorded_and_marked(self):
        spacings = {'exact': QUESTION, 'spaced': QUESTION.replace(' ', '  ')}
        for src, spacing, on_page, shot in itertools.product(I.FILL_FIELD_SRC, spacings, (True, False), I.SHOTS):
            case = (src, spacing, on_page, shot)
            self.start('todo')
            q = spacings[spacing]
            value = {'bank': ANSWER, 'said': 'Maybe', 'unknown': ''}[src]
            row = {'bank': {'q': q, 'src': 'bank', 'value': ANSWER, 'k': 'k1'} if spacing == 'exact'
                   else {'q': q, 'src': 'bank', 'value': ANSWER, 'zh': '是'},
                   'said': {'q': q, 'src': 'bank', 'value': value, 'why': '照履歷推的', 'zh': '也許'},
                   'unknown': {'q': q, 'src': 'skip', 'value': '', 'why': '不知道'}}[src]
            after = (form_tab(f'{TITLE} at {COMPANY}', q, *((value,) if on_page and value else ()), shot=shot),)
            _code, browser = self.go('fill', filled(lambda _p, row=row: sheet(fields=[row]),
                                                     before=(form_tab(f'{TITLE} at {COMPANY}', q),), after=after))
            fb = self.home.fb()
            form = fb[URL]['form']['f']
            expect(self, 'F-25', [(r['q'], r['src']) for r in form], [(q, 'bank' if src != 'unknown' else 'skip')], case)
            bank = {e['k']: e for e in fb['__ans__']}
            if src == 'unknown':
                continue
            linked = bank[form[0]['k']]
            same = spacing == 'exact' and value == ANSWER
            expect(self, 'F-06', form[0]['k'] == 'k1', same, case)
            if not same:
                expect(self, 'F-25', (linked['q'], linked['v'], bool(linked.get('inf'))), (q, value, True), case)
                expect(self, 'F-08', bool(linked.get('noev')), not shot, case)
            observed = {row['q']: row['marker'] for row in fb[URL]['apply']['field_observations']}
            expect(self, 'F-05', observed[q], FOUND if on_page else NOT_FOUND, case)
            if src != 'unknown' and not on_page and shot:
                import evidence
                field = next(row for row in fb[URL]['apply']['field_observations'] if row['q'] == q)
                expect(self, 'F-05', (field.get('shot'),
                                      os.path.isfile(evidence.path(URL, field.get('shot'), self.home.board) or ''),
                                      any(request[0] == 'tab' and request[2] == q and request[3]
                                          for request in browser.shot_requests),
                                      field.get('said'), field.get('shot_cropped')),
                       (field.get('shot'), True, True, value, True), case)


class FormRecordTest(FillCase):
    """F-06、F-08、F-21、F-25、A-21:這一輪的表單整筆記進卡上(刻意不填的留空值);答案庫已有的那條不動,新答案附那一刻拍到有那一題的頁,
    拍到的頁上沒有那一題照寫;沒交件的一輪卡上的表單與答案庫不動;記不進去的照列原因、卡住,這一輪的 apply 照記。"""

    def test_the_whole_form_and_answer_bank(self):
        self.start('todo')
        rows = [{'q': QUESTION, 'src': 'bank', 'value': ANSWER, 'k': 'k1'},
                {'q': 'Nickname', 'src': 'skip', 'value': '', 'why': '不填'},
                {'q': 'Why us', 'src': 'bank', 'value': 'Robots', 'why': '履歷', 'zh': '機器人', 'kind': 'txt'},
                {'q': 'Hidden', 'src': 'bank', 'value': 'X', 'why': 'w', 'zh': '叉'},
                {'q': 'Visa', 'src': 'bank', 'value': 'No', 'k': 'k9', 'why': 'w', 'zh': '否'}]    # 答案庫沒有的答案鍵:照這次的值開一條
        after = (form_tab(f'{TITLE} at {COMPANY}', QUESTION, 'Why us', 'Robots'),)
        self.go('fill', filled(lambda _p: sheet(fields=rows), after=after))
        fb = self.home.fb()
        form = fb[URL]['form']
        form.pop('at')
        keys = [row.get('k') for row in form['f']]
        today = datetime.date.today().isoformat()
        expect(self, 'F-25', form, {'plat': PLATFORM, 'f': [
            {'q': QUESTION, 'src': 'bank', 'k': 'k1'}, {'q': 'Nickname', 'src': 'skip', 'v': '', 'why': '不填'},
            {'q': 'Why us', 'src': 'bank', 'k': keys[2]}, {'q': 'Hidden', 'src': 'bank', 'k': keys[3]},
            {'q': 'Visa', 'src': 'bank', 'k': keys[4]}]})
        expect(self, 'F-08', fb['__ans__'], [
            BANK[0],
            {'k': keys[2], 'q': 'Why us', 'v': 'Robots', 'why': '履歷', 'kind': 'txt', 'zh': '機器人', 'pj': 1, 'inf': today,
             'ev': fb[URL]['apply']['ev']},
            {'k': keys[3], 'q': 'Hidden', 'v': 'X', 'why': 'w', 'zh': '叉', 'inf': today, 'noev': '這一輪程式拍到的頁上沒有這一題'},
            {'k': keys[4], 'q': 'Visa', 'v': 'No', 'why': 'w', 'zh': '否', 'inf': today, 'noev': '這一輪程式拍到的頁上沒有這一題'}])

    def test_an_empty_choice_is_listed_as_said(self):
        self.start('todo')
        self.go('fill', filled(lambda _p: sheet(delivery='')))
        marks = self.home.fb()[URL]['apply']['fill_changes']['marks']
        expect(self, 'A-30', [m['text'] for m in marks if m['code'] == 'SAID_VS_ACTUAL'],
               ['agent 說「」，程式讀到「不在可選的值裡」'])

    def test_the_last_after_snapshot_decides_the_evidence(self):
        rows = [{'q': 'Why us', 'src': 'bank', 'value': 'Robots', 'why': '履歷', 'zh': '機器人', 'kind': 'txt'},
                {'q': 'Hidden', 'src': 'bank', 'value': 'X', 'why': 'w', 'zh': '叉'}]
        early = (form_tab(f'{TITLE} at {COMPANY}', 'Why us', 'Robots'),)
        late = (form_tab(f'{TITLE} at {COMPANY}', 'Hidden', 'X'),)
        none = '這一輪程式拍到的頁上沒有這一題'
        for case, script in (('two afters', filled(lambda _p: sheet(fields=rows), extra=(('after', early),), after=late)),
                             ('no after', Script((('open', early), ('before', early)), lambda _p: sheet(fields=rows), I.HANDED))):
            self.start('todo')
            self.go('fill', script)
            fb = self.home.fb()
            last = fb[URL]['apply'].get('ev')
            expect(self, 'F-08', [({'noev': e['noev']} if 'noev' in e else {'ev': e['ev']}) for e in fb['__ans__'][1:]],
                   [{'noev': none}, {'ev': last} if case == 'two afters' else {'noev': none}], case)

    def test_a_refilled_card_closes_only_its_own_fill_reports(self):
        import agent_report
        import board_doc
        other = 'https://jobs.example/jobs/2'
        reports = [{'id': 'a', 'from': agent_report.FROM_APPLY, 'job': URL, 'msg': '填不上'},
                   {'id': 'b', 'from': agent_report.FROM_APPLY, 'job': other, 'msg': '填不上'},
                   {'id': 'c', 'from': '網站筆記', 'job': URL, 'msg': '筆記'}]
        self.start('todo')
        board_doc.set_fb(lambda fb: fb.__setitem__(board_doc.KEY, copy.deepcopy(reports)), live=self.home.board)
        self.go('fill', filled())
        today = datetime.date.today().isoformat()
        expect(self, 'F-04', self.home.fb()[board_doc.KEY],
               [dict(reports[0], done=today, res='這張重新填好了,結果看卡上'), reports[1], reports[2]])

    def test_a_round_without_a_sheet_leaves_the_form_alone(self):
        seeded = {'apply': copy.deepcopy(APPLY), 'form': {'plat': PLATFORM, 'f': FORM_ROWS, 'at': 'T0'}}
        self.start('stuck', seeded)
        self.go('fill', filled(end=I.UNAVAILABLE))
        fb = self.home.fb()
        expect(self, 'F-22', (self.state(), fb[URL]['form'], fb['__ans__']), ('nopage', seeded['form'], BANK))

    def test_a_form_that_cannot_be_recorded_is_listed(self):
        self.start('todo')
        bad = {'q': 'Why us', 'src': 'bank', 'value': 'Robots', 'why': '履歷', 'zh': '機器人', 'kind': 'text'}
        self.go('fill', filled(lambda _p: sheet(fields=[bad])))
        card = self.card()
        expect(self, 'A-21', (self.state(), card['apply']['issues'], card['apply']['session'], 'form' in card,
                              self.home.fb()['__ans__']),
               ('stuck', ['這一輪沒完成'], 'fake-session',
                False, BANK))


class AttentionTest(FillCase):
    """#807 第 2–4 點:交接成立當輪進要你做,同一個網站卡住幾張只開一則、寫出幾張卡,不同網站各一則;
    沒做完自動流程下一輪自己重填,連續 3 輪才開一則。"""
    OTHER, ELSEWHERE = 'https://jobs.example/jobs/2', 'https://careers.example/jobs/3'

    def seed(self, *urls):
        import board_doc
        self.start('todo')
        board_doc.set_data(lambda data, _fb: data['jobs'].extend({'id': url, 'target': url} for url in urls
                                                                  if url not in {j['id'] for j in data['jobs']}),
                           live=self.home.board)
        board_doc.set_fb(lambda fb: fb.update({url: {'app': 'ship', 'ds': 'todo'} for url in urls}, __auto__={}),
                         live=self.home.board)     # 自動流程開著

    def fill(self, url, cells, end=I.HANDED):
        page = form_tab('請先登入才能填表', url=url + '/apply')
        fill.main(['--stage', 'fill', '--url', url, '--board', self.home.board],
                  browser=FakeBrowser(filled(lambda _p: sheet(**cells), before=(page,), after=(page,), end=end)))

    def test_handoffs_on_one_website_become_one_item(self):
        self.seed(self.OTHER, self.ELSEWHERE)
        login = {'needs_user': '要你登入', 'needs_user_tab': '7:p1', 'needs_user_quote': '請先登入才能填表'}
        for url in (URL, self.OTHER, self.ELSEWHERE):
            self.fill(url, login)
        items = agent_report.todo(self.home.fb())
        expect(self, '#807', sorted((r['msg'].splitlines()[0], r['url']) for r in items),
               [('careers.example 要你本人處理：', self.ELSEWHERE + '/apply'),
                ('jobs.example 要你本人處理，卡住 2 張卡：', URL + '/apply')])
        self.fill(URL, {})
        expect(self, '#807', sorted(r['msg'].splitlines()[0] for r in agent_report.todo(self.home.fb())),
               ['careers.example 要你本人處理：', 'jobs.example 要你本人處理：'])

    def test_a_card_handled_elsewhere_leaves_the_merged_item(self):
        """併成一則的兩張卡,一張你在外部送出了:那一則只剩另一張,不再寫卡住 2 張卡。"""
        self.seed(self.OTHER)
        login = {'needs_user': '要你登入', 'needs_user_tab': '7:p1', 'needs_user_quote': '請先登入才能填表'}
        for url in (URL, self.OTHER):
            self.fill(url, login)
        agent_report.resolve(URL, '你標了已在外部送出', live=self.home.board)
        expect(self, '#807', [(r['msg'].splitlines()[0], r.get('job')) for r in agent_report.todo(self.home.fb())],
               [('jobs.example 要你本人處理：', self.OTHER)])

    def test_different_things_on_one_website_stay_apart(self):
        """併的鍵是網站加交件單的原字(#807 第 4 點):同一網站、要做的事不同,各一則。"""
        self.seed(self.OTHER)
        for url, why in ((URL, '要你登入'), (self.OTHER, '要你通過驗證碼')):
            self.fill(url, {'needs_user': why, 'needs_user_tab': '7:p1', 'needs_user_quote': '請先登入才能填表'})
        expect(self, '#807', sorted(r['msg'].splitlines()[1].rpartition('：')[2] for r in agent_report.todo(self.home.fb())),
               ['要你登入', '要你通過驗證碼'])

    def test_an_unfinished_round_is_refilled_by_the_next_round(self):
        import next_step
        self.seed()
        lines = []
        for _ in range(3):
            self.fill(URL, {'stopped': '停在驗證碼'})
            fb = self.home.fb()
            nx = next_step.of(fb, list(self.home.jobs().values()), flow={'auto_fill': True})[URL]
            lines.append(((nx['auto'] or {}).get('line'), nx['view']['fill']['kind'], len(agent_report.todo(fb))))
        # 填表進度那一列:會自動重做的放「會自動重做」,不算沒填成;第 3 輪才回到沒填成、進要你做
        expect(self, '#807', lines, [('這一輪沒做完，Agent 下一輪會自動重做（已連續 1 輪沒做完，3 輪才找你）', 'redo', 0),
                                     ('這一輪沒做完，Agent 下一輪會自動重做（已連續 2 輪沒做完，3 輪才找你）', 'redo', 0), (None, 'bad', 1)])

    def test_a_round_the_user_stopped_is_not_refilled_by_itself(self):
        """他自己停掉的那一輪:不算一輪,自動流程也不接著重填(停了就是他要接手)。"""
        import next_step
        self.seed()
        self.fill(URL, {'stopped': '停在驗證碼'})
        self.fill(URL, {'stopped': '停在驗證碼'}, end=I.STOPPED_BY_USER)
        nx = next_step.of(self.home.fb(), list(self.home.jobs().values()), flow={'auto_fill': True})[URL]
        expect(self, '#807', (nx['auto'], nx['view']['fill']['kind'], len(agent_report.todo(self.home.fb()))),
               (None, 'todo', 0))


class FormPlanTest(FillCase):
    """F-01、C-23:agent 叫的表單工具只把題目一字不差在答案庫、空白的文字格由程式填;其他格照列給 agent。"""

    def test_program_fills_only_blank_text_fields_whose_name_matches_exactly(self):
        kinds = ('blank_exact', 'blank_spaced', 'has_value', 'no_label', 'choice')
        for kind in kinds:
            self.start('todo', {'apply': copy.deepcopy(APPLY)})
            field = {'blank_exact': {'type': 'text', 'label': QUESTION, 'value': '', 'backend': 'b1'},
                     'blank_spaced': {'type': 'text', 'label': QUESTION + ' ', 'value': '', 'backend': 'b1'},
                     'has_value': {'type': 'text', 'label': QUESTION, 'value': 'No', 'backend': 'b1'},
                     'no_label': {'type': 'text', 'label': '', 'value': '', 'backend': 'b1', 'context': QUESTION},
                     'choice': {'type': 'radio', 'name': 'auth', 'group': QUESTION, 'label': ANSWER, 'value': '',
                                'backend': 'b1'}}[kind]
            browser = FakeBrowser(filled(extra=(('tool', lambda b: self.plan(b)),)))
            browser.forms['7:p1'] = {'fields': [field], 'lines': [QUESTION]}
            self.go('fill', browser=browser)
            want = [('7:p1', [{'backend': 'b1', 'how': 'text', 'value': ANSWER}])] if kind == 'blank_exact' else []
            expect(self, 'F-01', browser.filled, want, kind)
            # 列給 agent 的每一格一行(form_plan.render:「- 你處理 | …」「- 頁面已有值 | …」);開頭那行只是計數
            expect(self, 'F-01', ('- 你處理 |' in browser.outputs[0], '- 頁面已有值 |' in browser.outputs[0]),
                   (kind in ('blank_spaced', 'no_label', 'choice'), kind == 'has_value'), kind)

    def plan(self, browser):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            # agent 照指示帶 --lang(行為清單 I-13):答案庫英文答案附中文,填哪一種由 agent 看頁面說
            form_plan.main(['--url', URL, '--tab', '7:p1', '--fill', '--lang', 'en', '--board', self.home.board],
                           browser=browser)
        return out.getvalue()


class FormPlanToolTest(FillCase):
    """F-01、C-23、I-13:表單工具每一種用法(--fill、--lang、--set、--check)從入口照答案庫與卡上記過的表單配,
    一字不差才給答案;配不到、兩種文字沒說用哪一種、還在等確認的、鎖住的、選項、沒題目名稱的照列給 agent。"""

    FIELDS = [
        {'type': 'text', 'label': 'Q1', 'value': '', 'backend': 'b0'},
        {'type': 'text', 'label': 'Q2', 'value': '', 'backend': 'b1'},
        {'type': 'text', 'label': 'Q3', 'value': '', 'backend': 'b2'},
        {'type': 'text', 'label': 'Q4', 'value': '', 'backend': 'b3'},
        {'type': 'text', 'label': 'Name', 'name': 'fullname', 'value': '', 'backend': 'b4'},
        {'type': 'text', 'label': 'City', 'value': '', 'backend': 'b5'},
        {'type': 'text', 'label': 'Q4', 'value': '', 'backend': 'b6', 'disabled': True},
        {'type': 'file', 'label': 'Resume', 'value': ['a.pdf']},
        {'type': 'radio', 'name': 'auth', 'group': 'Auth', 'label': 'Yes', 'value': '', 'backend': 'r1'},
        {'type': 'radio', 'name': 'auth', 'group': 'Auth', 'label': 'No', 'value': '', 'backend': 'r2'},
        {'type': 'text', 'label': '', 'value': '', 'backend': 'b10', 'context': 'Your name'},
        {'type': 'text', 'label': 'Phone', 'value': '', 'shown': '0912', 'backend': 'b11'},
        {'type': 'select-one', 'label': 'Q5', 'value': '', 'backend': 'b12'},
        {'type': 'text', 'label': 'Q6', 'value': '', 'backend': 'b13'},
        {'type': 'text', 'label': 'Q7', 'value': '', 'backend': 'b14'},
        {'type': 'text', 'label': 'Q8', 'value': '', 'backend': 'b15'},
        {'type': 'text', 'label': 'Q9', 'value': '', 'backend': 'b16'},
        {'type': 'select-one', 'label': 'Q10', 'value': '', 'backend': 'b17'},
        {'type': 'combobox', 'label': 'Q11', 'value': '', 'backend': 'b18'},
        {'type': 'text', 'label': 'Q12', 'value': '', 'shown': '', 'backend': 'b19'},
        {'type': 'file', 'name': 'cover', 'value': []},
        {'type': 'file', 'value': ['a.pdf', 'b.pdf']},
        {'type': 'checkbox', 'group': 'Consent', 'label': 'I agree', 'value': 'on', 'backend': 'c1'},
        {'type': 'text', 'label': '', 'value': '', 'backend': 'b23'},
    ]
    TOOL_BANK = [{'k': 'k1', 'q': 'Q1', 'v': 'Yes', 'zh': '是'}, {'k': 'k2', 'q': 'Q2', 'v': 'No', 'zh': '否', 'inf': 1},
                 {'k': 'k3', 'q': 'Q3', 'v': 'Taipei / 台北'}, {'k': 'k4', 'q': 'Q4', 'v': '是'},
                 {'k': 'k5', 'q': 'Q5', 'v': 'A', 'zh': '甲', 'tr': 1}, {'q': 'no key'},
                 {'k': 'k6', 'q': 'Auth', 'v': 'Yes', 'zh': '是'}, {'k': 'k7', 'q': 'Q6', 'v': 'x', 'redo': 1},
                 {'k': 'k8', 'q': 'Q7', 'zh': '只有中文'}, {'k': 'k11', 'q': 'Q8', 'v': 'Remote'},
                 {'k': 'k12', 'q': 'Q9', 'v': 'Full-time / Part-time'}, {'k': 'k13', 'q': 'Q10', 'v': 'B'},
                 {'k': 'k14', 'q': 'Q11', 'v': 'C'}, {'k': 'k15', 'q': 'Q12', 'v': 'D'}, {'k': 'k16', 'q': 'Consent', 'v': 'I agree'},
                 {'k': 'k17', 'q': 'Your name', 'v': 'Bob B', 'zh': '鮑伯'}]

    def tool(self, *args, fail=False, fields=None):
        import contextlib
        import io
        other = 'https://jobs.example/jobs/2'
        fb = {other: {'form': {'f': [{'q': 'City', 'src': 'rz'}, {'q': 'City', 'src': 'rz', 'v': ' '},     # 別張先列:這張的照樣先用
                                     {'q': 'City', 'src': 'rz', 'v': '台中'}, {'q': 'Name', 'src': 'rz', 'v': 'Other'}]}},
              URL: {'app': 'ship', 'ds': 'running', 'apply': copy.deepcopy(APPLY),
                    'form': {'f': [{'q': 'Name', 'src': 'rz', 'v': 'Ann'}, {'q': 'Q1', 'src': 'bank', 'k': 'k1'},
                                   {'q': '', 'src': 'bank', 'k': 'k9'}, {'q': 'Phone', 'src': 'rz', 'v': '0912'},
                                   {'q': 'Q12', 'src': 'bank', 'k': 'k4'}, {'q': 'Lost', 'src': 'bank'}]}},
              '__ans__': copy.deepcopy(self.TOOL_BANK)}
        self.home.write_board([JOB, {'id': other, 'target': 'Other'}], fb)
        browser = FakeBrowser()
        browser.forms['7:p1'] = {'fields': copy.deepcopy(self.FIELDS if fields is None else fields),
                                 'lines': ['Name', 'Your name']}
        if fail:
            browser.fill_form = lambda tab, items: [{'backend': item['backend'], 'ok': False, 'why': '假瀏覽器:填不上'}
                                                    for item in items]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            form_plan.main(['--url', URL, *args, '--board', self.home.board], browser=browser)
        return out.getvalue(), browser

    def test_a_question_nobody_answered_is_asked_and_the_rest_still_fill(self):
        import contextlib
        import io
        import json
        unknown = {'type': 'text', 'label': '期望薪資', 'value': '', 'backend': 'b20'}
        text, browser = self.tool('--tab', '7:p1', '--fill', fields=[unknown, self.FIELDS[3], self.FIELDS[5]])
        expect(self, 'I-13', (browser.filled, text.splitlines()), (
            [('7:p1', [{'backend': 'b3', 'how': 'text', 'value': '是'}, {'backend': 'b5', 'how': 'text', 'value': '台中'}])],
            ['程式只填空白文字欄:2;頁面已有值:0;需要你處理:1。既有值只列出觀察,不判斷是否符合答案。',
             '- 填空白欄 | 題目「Q4」(格子:Q4,text) → 是', '- 填空白欄 | 題目「City」(格子:City,text) → 台中',
             '- 你處理 | 格 #1 題目「期望薪資」(格子:期望薪資,text):答案庫和以前的表單都沒有一字不差的這一題',
             '交件單 fields 裡程式配好的這幾格可以直接用(程式配不到的你自己補):',
             json.dumps([{'q': 'Q4', 'src': 'bank', 'k': 'k4'}, {'q': 'City', 'src': 'rz', 'value': '台中'}], ensure_ascii=False)]))
        with self.assertRaises(SystemExit) as wrong:
            self.tool('--tab', '8:p1', '--fill')
        expect(self, 'I-13', wrong.exception.code, '這個 --tab 不是這張卡這一輪的工作區;寫 工作區編號:Page(例如 12:p1)')
        with self.assertRaises(SystemExit) as lang, contextlib.redirect_stderr(io.StringIO()):
            self.tool('--tab', '7:p1', '--lang', 'fr')
        expect(self, 'I-13', lang.exception.code, 2)

    def test_every_way_the_agent_calls_the_form_tool(self):
        import json
        bilingual = '答案庫這一條有兩種文字,這一頁用哪一種由你判斷(--lang zh 或 --lang en)'
        waiting = '答案庫這一條還在等確認或要重翻'
        same = [('Q4', 'text', '是', 'b3', 'k4'), ('Name', 'text', 'Ann', 'b4', None), ('City', 'text', '台中', 'b5', None),
                ('Q7', 'text', '只有中文', 'b14', 'k8'), ('Q8', 'text', 'Remote', 'b15', 'k11'),
                ('Q9', 'text', 'Full-time / Part-time', 'b16', 'k12'), ('Q10', 'select-one', 'B', 'b17', 'k13'),
                ('Q11', 'combobox', 'C', 'b18', 'k14'), ('Q12', 'text', '是', 'b19', 'k4')]
        zh = [('Q1', 'text', '是', 'b0', 'k1'), ('Q3', 'text', '台北', 'b2', 'k3')]
        how = {'text': 'text', 'select-one': 'select', 'combobox': 'combo'}

        def filled(rows):
            return [{'backend': b, 'how': how[t], 'value': v} for _q, t, v, b, _k in rows]

        def fill_lines(rows):
            return [f'- 填空白欄 | 題目「{q}」(格子:{q},{t}) → {v}' for q, t, v, _b, _k in rows]

        def sheet(rows):
            return json.dumps([{'q': q, 'src': 'bank', 'k': k} if k else {'q': q, 'src': 'rz', 'value': v}
                               for q, _t, v, _b, k in rows] + [{'q': 'Phone', 'src': 'rz', 'value': '0912'}], ensure_ascii=False)

        def lines(rows, auth, both_open=True):
            asks = [f'- 你處理 | 格 #1 題目「Q1」(格子:Q1,text):{bilingual};答案庫原文:en: Yes | zh: 是'] * both_open + [
                f'- 你處理 | 格 #2 題目「Q2」(格子:Q2,text):{waiting}'] + [
                f'- 你處理 | 格 #3 題目「Q3」(格子:Q3,text):{bilingual};答案庫原文:zh: 台北 | en: Taipei'] * both_open + [
                '- 你處理 | 格 #7 題目「Q4」(格子:Q4,text):這一格頁上鎖住(停用),程式不填',
                f'- 你處理 | 格 #9 題目「Auth」(格子:auth,radio):請依題目和頁面內容處理選項;選項:Yes / No;答案庫原文:{auth}',
                '- 你處理 | 格 #11 沒有題目名稱(格子:,text):瀏覽器沒給這一格題目名稱;題目是哪一句由你看頁面判斷;這一格前面的字:「Your name」',
                f'- 你處理 | 格 #13 題目「Q5」(格子:Q5,select-one):{waiting}', f'- 你處理 | 格 #14 題目「Q6」(格子:Q6,text):{waiting}',
                '- 你處理 | 格 #23 題目「Consent」(格子:I agree,checkbox):請依題目和頁面內容處理選項;選項:I agree;目前選中:I agree;'
                '答案庫原文:I agree',
                '- 你處理 | 格 #24 沒有題目名稱(格子:,text):瀏覽器沒給這一格題目名稱;題目是哪一句由你看頁面判斷']
            return [f'程式只填空白文字欄:{len(rows)};頁面已有值:1;需要你處理:{len(asks)}。既有值只列出觀察,不判斷是否符合答案。',
                    *fill_lines(rows), '- 頁面已有值 | 題目「Phone」: 0912', *asks,
                    '沒有題目名稱的格子:你看頁面判斷題目,用 --set \'[{"id":"#格子編號","q":"頁上的題目原文","value":"值"}]\';'
                    'q 要一字不差在頁上,不寫 value 就用記過的同一題答案',
                    '- 上傳欄「Resume」:照上傳規則選檔(現在:a.pdf)', '- 上傳欄「cover」:照上傳規則選檔(現在:空的)',
                    '- 上傳欄「上傳欄」:照上傳規則選檔(現在:a.pdf, b.pdf)', '交件單 fields 裡程式配好的這幾格可以直接用(程式配不到的你自己補):',
                    sheet(rows)]

        # 這張卡記過的先(即使別張先列);別張記過的補上;答案庫一格裡寫兩種文字、只有中文、兩段都是英文、各種格子的填法
        text, browser = self.tool('--tab', '7:p1', '--fill')
        expect(self, 'F-01', (browser.filled, text.splitlines()), ([('7:p1', filled(same))], lines(same, 'en: Yes | zh: 是')))
        text, browser = self.tool('--tab', '7:p1', '--fill', '--lang', 'zh')
        both = zh + same
        expect(self, 'I-13', (browser.filled, text.splitlines()), ([('7:p1', filled(both))], lines(both, '是', both_open=False)))
        text, browser = self.tool('--tab', '7:p1', '--check')
        expect(self, 'I-13', text.splitlines(), [f'仍空白、尚未帶入的可用答案欄:{len(same)}', *fill_lines(same)])

        picks = [{'q': 'Auth', 'value': 'No'}, {'q': 'Auth', 'value': 'Maybe'}, {'id': '#11', 'q': 'Name', 'value': ''},
                 {'id': '#11', 'q': 'Your name', 'value': 'Bob'}, {'id': '#11', 'q': 'Your name', 'value': ''},
                 {'id': '#11', 'q': 'Not on page', 'value': 'x'}, {'q': 'Nope', 'value': 'x'}, 'junk']
        text, browser = self.tool('--tab', '7:p1', '--lang', 'en', '--set', json.dumps(picks))
        expect(self, 'I-13', browser.filled, [('7:p1', [{'backend': 'r2', 'how': 'click', 'value': 'No'},
                                                        {'backend': 'b10', 'how': 'text', 'value': 'Ann'},
                                                        {'backend': 'b10', 'how': 'text', 'value': 'Bob'},
                                                        {'backend': 'b10', 'how': 'text', 'value': 'Bob B'}])])
        nothing = '沒有「{}」這一格要你判斷,或沒寫值(也沒有記過這一題的答案)'
        expect(self, 'I-13', text.split('\n程式只填空白文字欄')[0].splitlines(),
               ['「Auth」沒有逐字相同的選項「Maybe」', '「#11」:頁上沒有一字不差的「Not on page」,這一格沒填',
                nothing.format('Nope'), nothing.format('')])
        no_lang = [{'id': '#11', 'q': 'Your name'}]      # 記過的答案有兩種文字、沒說用哪一種:不填
        for picked, first in ((json.dumps(no_lang), nothing.format('#11')), ('{"q": "Auth"}', lines(same, '')[0]),
                              ('not json', '--set 不是 JSON')):
            text, browser = self.tool('--tab', '7:p1', '--set', picked)
            expect(self, 'I-13', (text.splitlines()[0], browser.filled), (first, []), picked)
        text, browser = self.tool('--tab', '7:p1', fields=[self.FIELDS[3]])
        expect(self, 'I-13', text.splitlines(), [
            '程式只填空白文字欄:1;頁面已有值:0;需要你處理:0。既有值只列出觀察,不判斷是否符合答案。', *fill_lines(same[:1]),
            '交件單 fields 裡程式配好的這幾格可以直接用(程式配不到的你自己補):',
            json.dumps([{'q': 'Q4', 'src': 'bank', 'k': 'k4'}], ensure_ascii=False)])
        text, browser = self.tool('--tab', '7:p1', '--fill', '--lang', 'zh', fail=True)
        expect(self, 'I-13', text.splitlines()[0], '程式填不上「Q1」:假瀏覽器:填不上(這一格你自己用 ego 填)')
        with self.assertRaises(SystemExit, msg='I-13'):
            self.tool('--tab', '8:p1', '--fill')


class FixedProfileTest(FillCase):
    """F-10、F-11、F-12、F-13、A-14:固定版平台履歷那一頁的清單發到那一份、可恢復、在確認畫面;落後只標不擋。"""

    def test_fixed_profile_pages_are_published_by_exact_url(self):
        pages = {None: (), 'pages_match': (Tab('p2', PROFILE_URL, parts=('經歷', '新的一行')),),
                 'pages_other_url': (Tab('p2', PROFILE_URL + '/', parts=('經歷', '新的一行')),), 'not_shot': ()}
        for fixed in I.FIXED_PROFILE:
            case = fixed
            self.start('todo', registered=fixed is not None)   # 沒登記那一份就不是固定版(profile_sync.decided 沒有 fixed_url)
            before = (form_tab(), *(Tab(t.label, t.url, parts=('經歷',)) for t in pages[fixed]))
            after = (form_tab(f'{TITLE} at {COMPANY}', QUESTION, ANSWER), *pages[fixed])
            _code, browser = self.go('fill', filled(lambda _p: sheet(delivery='platform_profile', profile=''),
                                                    before=before, after=after))
            expect(self, 'F-13', self.state(), 'parked', case)
            entry = (self.home.registry().get(PLATFORM) or {}).get(f'{LANG}/one') or {}   # 沒登記就沒有那一份
            visuals = [v for v in entry.get('sync_visuals') or [] if v.get('task') == 'fill']
            if fixed is None:
                expect(self, 'F-10', visuals, [], case)
                continue
            expect(self, 'F-10', ('jobsalvo-before' in browser.prompts[0], 'jobsalvo-after' in browser.prompts[0]),
                   (True, True), case)
            visual = visuals[-1]
            expect(self, 'F-10', visual['card'], URL, case)
            expect(self, 'F-10', {row['page_url'] for row in visual['pages_compared']},
                   {PROFILE_URL} if fixed == 'pages_match' else set(), case)
            expect(self, 'F-10', [m['text'] for m in visual['list_marks'] if m['code'] == 'NO_FIXED_PROFILE_PAGE'],
                   [] if fixed == 'pages_match' else ['沒有固定履歷頁'], case)
            if fixed == 'pages_match':
                changes = visual['page_changes']
                expect(self, 'F-11', [(r['after_parts'], r['restorable'], bool(r['after_shot'])) for r in changes],
                       [(['新的一行'], True, True)], case)
                report = self.card()['apply']['fill_changes']
                expect(self, 'F-11', any(p['page_url'] == PROFILE_URL for p in report['pages']), True, case)
                # 從填表發的那一版清單恢復:讀填表那一輪的證據夾,只比固定版那一頁,選的編號才對得上
                payload = {'platform': PLATFORM, 'lang': LANG, 'variant': 'one', 'version': visual['evidence'],
                           'change_ids': [row['change_id'] for row in changes]}
                again = FakeBrowser(Script((('before', pages[fixed]), ('after', pages[fixed])), sheet={}))
                sync.main(['--board', self.home.board, '--restore-json', json.dumps(payload)], browser=again)
                restored = self.home.registry()[PLATFORM][f'{LANG}/one']['sync_visuals'][-1]
                expect(self, 'F-11', (restored['task'], [row['item'] for row in restored['intended_changes']],
                                      self.home.status_file('sync')['results'][0]['ok']),
                       ('restore', [f'恢復 {PROFILE_URL}'], True), case)
            notice = profile_sync.sync_notice_for_url(self.home.fb(), URL, JOB)
            expect(self, 'F-12', bool(notice), True, case)


class UploadTest(FillCase):
    """F-07、F-15:選的平台履歷與上傳欄由 agent 引段;頁上顯示的檔名只跟這張卡要寄的檔名一字不差對照。"""

    def test_uploaded_names_and_profile_choice(self):
        for uploaded in I.UPLOADED:
            self.start('todo')
            names = [str(d.get('send_name') or d.get('name')) for d in ship.documents(JOB, self.home.fb())]
            shown = {'same_name': names[0], 'other_name': 'old-resume.pdf', 'none': ''}[uploaded]
            after = (form_tab(f'{TITLE} at {COMPANY}', *((f'已上傳 {shown}',) if shown else ())),)
            self.go('fill', filled(lambda _p: sheet(uploaded=[{'name': names[0]}] if uploaded != 'none' else []),
                                   after=after))
            observed = {row['q']: row['marker'] for row in self.card()['apply']['field_observations']}
            expect(self, 'F-15', observed.get(f'上傳檔:{names[0]}'),
                   (FOUND if uploaded == 'same_name' else NOT_FOUND) if uploaded != 'none' else None, uploaded)
            expect(self, 'F-07', self.card()['apply']['uploaded'], [names[0]] if uploaded != 'none' else [], uploaded)


class ListedNameTest(FillCase):
    """F-07、F-15:上傳檔名、平台履歷名稱不在這張卡的清單裡,照列 agent 說的跟程式讀到不在清單。"""

    def test_names_outside_the_card_list_are_marked(self):
        for upload, profile in (('report.pdf', '英文履歷'), ('old.pdf', '英文履歷'), ('report.pdf', 'Nope')):
            case = (upload, profile)
            self.start('todo')
            profile_sync.remember(PLATFORM, LANG, 'one', PROFILE_URL, name='英文履歷')
            self.go('fill', filled(lambda _p, u=upload, p=profile: sheet(uploaded=[{'name': u}], profile=p)))
            said = [m['params']['said'] for m in self.card()['apply']['fill_changes']['marks']
                    if m['code'] == 'SAID_VS_ACTUAL' and m['params']['actual'] == '不在給 agent 的清單裡']
            expect(self, 'F-15', said, [name for name in (upload, profile) if name not in ('report.pdf', '英文履歷')], case)


class ConfirmTest(FillCase):
    """F-14、F-09:確認送出畫面打開時拍一次那張卡的工作區,跟填表最後一次拍改後照配對規則比,變了照列,不擋。"""

    def test_confirm_screen_lists_what_changed_since_the_fill(self):
        import board_doc
        import evidence
        self.home.board = os.path.join(self.home.home, 'copy.html')       # 看板副本:填表那一輪的證據在暫存夾,照那裡找
        last = form_tab(f'{TITLE} at {COMPANY}', QUESTION, ANSWER)
        now = {'same': (last,), 'changed': (form_tab(f'{TITLE} at {COMPANY}', QUESTION, 'No'),),
               'unread': (form_tab(state=I.UNREADABLE),), 'gone': ()}
        for page in I.CONFIRM_PAGE:
            self.start('todo')
            self.go('fill', filled(after=(last,)))
            browser = FakeBrowser()
            browser.tabs = now[page]
            had = self.shots(evidence.root(self.home.board))
            os.remove(board_doc.journal_path(self.home.board))
            code, _browser = self.go('confirm', browser=browser)
            expect(self, 'F-14', (self.state(), code), ('parked', 0), page)
            with open(board_doc.journal_path(self.home.board), encoding='utf-8') as marks:
                expect(self, 'F-14', [json.loads(line).get('by') for line in marks], ['fill'], page)
            shots = self.shots(evidence.root(self.home.board)) - had       # 這一次確認拍的,放在填表的證據夾
            expect(self, 'F-14', sorted(os.path.basename(os.path.dirname(path))[-12:] for path in shots),
                   ['fill-confirm'] * len(now[page]), page)
            report = self.card()['apply']['changes_since_fill']
            tab, url = last.tab_id, last.url
            side = {'page_tab_id': tab, 'page_url': url, 'before_tab_id': tab, 'before_url': url, 'compared': False}
            expect(self, 'F-14', (report['heading'], [{key: value for key, value in row.items()
                                                       if key not in ('marks', 'reason', 'changes', 'before_shot', 'after_shot',
                                                                      'page_title', 'title', 'before_title', 'after_title')}
                                                      for row in report['pages']]),
                   ('填好後到送出前的頁面紀錄', {'same': [], 'changed': [{'page_tab_id': tab, 'page_url': url, 'compared': True}],
                                                'unread': [dict(side, after_tab_id=tab, after_url=url)] * 2,
                                                'gone': [dict(side, after_tab_id='', after_url='')]}[page]), page)
            for row in report['pages']:         # 沒逐處比的那一頁也附程式拍到的截圖,看板開得到(#783)
                if not row['compared']:
                    expect(self, '#783', [bool(row.get(side + '_tab_id')) == bool(evidence.path(URL, row.get(side + '_shot'), self.home.board))
                                          for side in ('before', 'after')], [True, True], page)
            codes = [m['code'] for m in report['marks']]
            expect(self, 'F-14', codes.count('PAGE_CHANGED_SINCE_FILL'), int(page == 'changed'), page)
            if page == 'changed':
                expect(self, 'F-14', report['marks'][0]['text'], CHANGED, page)
                expect(self, 'F-14', [(c['before_parts'], c['after_parts']) for p in report['pages'] if p['compared']
                                      for c in p['changes']], [([ANSWER], ['No'])], page)
            reasons = sorted(p.get('reason', '') for p in report['pages'] if not p['compared'])
            want = {'same': [], 'changed': [], 'unread': ['有一邊讀不到，沒逐處比'] * 2,
                    'gone': ['只有改前，沒比到']}[page]
            expect(self, 'F-09' if page == 'unread' else 'F-14', reasons, want, page)
            status = form_record.board_status(self.home.board)
            expect(self, 'F-13', form_record.approval_problem(self.home.fb(), URL, status), '還沒確認送出', page)

    def test_a_fill_page_that_could_not_be_read_stays_listed(self):
        self.start('todo')
        self.go('fill', filled(after=(form_tab(state=I.UNREADABLE),)))
        browser = FakeBrowser()
        browser.tabs = (form_tab(state=I.UNREADABLE),)
        self.go('confirm', browser=browser)
        marks = self.card()['apply']['changes_since_fill']['marks']
        expect(self, 'F-09', [m['code'] for m in marks if m['code'] in PAGE_CODES], ['PAGE_UNREADABLE'] * 2)

    @staticmethod
    def shots(root):
        return {os.path.join(folder, name) for folder, _dirs, names in os.walk(root) for name in names if name.endswith('.png')}

    def why(self, report):
        return next((m['params'].get('why', '') for m in report['marks'] if m['code'] == 'SIDE_UNREADABLE'), '')


class UnmetTest(FillCase):
    """F-26:職缺的檔案要求照列,要求原句在引的段裡核對到標程式讀到,不然標 agent 說的;有沒照做的要求卡停在卡住;
    程式讀到的要求才交給可投遞夾:不寄的記下不寄、要改的排進自動客製。"""

    def test_unmet_requirements_are_listed_with_their_source(self):
        import card_identity
        import config as cf
        folder = os.path.join(cf.SHIP_DIR, f'card-{card_identity.card_id_from_url(URL)}')
        os.makedirs(folder)
        with open(os.path.join(folder, 'ship.json'), 'w', encoding='utf-8') as target:
            json.dump({'files': ['resume.pdf', 'report.pdf'], 'variant': 'one', 'lang': LANG,
                       'sources': {f'resume:one:{LANG}': 'resume.pdf', 'attachment:report': 'report.pdf'}}, target)
        asked = f'這家職缺要求:「{REQUIREMENT}」(沒有作品集)。只改為了符合這句要求必要的地方,其他一個字都不動。'
        for cited, act in itertools.product(I.FIELD_CITES, ('adapt', 'drop')):
            case = (cited, act)
            self.start('todo')
            after = (form_tab(f'{TITLE} at {COMPANY}', REQUIREMENT),)

            def build(printed, cited=cited, act=act):
                quote = {'verified': printed[-1].cite(REQUIREMENT), 'not_on_page': {'material_id': 'x', 'ids': [99]},
                         'missing': None}[cited]
                return sheet(unmet=[{'quote': quote, 'file': 'report.pdf', 'act': act, 'why': '沒有作品集'}])
            self.go('fill', filled(build, after=after))
            card = self.card()
            unmet = card['apply']['unmet']
            expect(self, 'F-26', [(row['file'], row['source']) for row in unmet],
                   [('report.pdf', E.PROGRAM if cited == 'verified' else E.AGENT)], case)
            expect(self, 'F-26', (unmet[0]['quote'] == REQUIREMENT, self.state()), (cited == 'verified', 'stuck'), case)
            verified = cited == 'verified'
            asks = [(row['auto'], row['text']) for doc in (card.get('custom_docs') or {}).values() for row in doc['feedbacks']]
            expect(self, 'F-26', (card.get('drop_att'), asks),
                   ({'report': REQUIREMENT} if verified and act == 'drop' else None,
                    [('unmet', asked)] if verified and act == 'adapt' else []), case)


class CardTest(FillCase):
    """F-18、F-19、F-20、F-27:卡名照核對過的職稱改;交接那一頁記在卡上;卡上的截圖連到拍改後那一頁;應徵紀錄頁與平台履歷登記照收。"""

    def test_rename_handoff_shot_and_registrations(self):
        for title, cited, shot in itertools.product((TITLE, 'Engineer II'), (True, False), I.SHOTS):
            case = (title, cited, shot)
            self.start('todo')
            after = (form_tab(title, COMPANY, QUESTION, ANSWER, shot=shot),)

            def build(printed, cited=cited, title=title):
                bad = {'material_id': 'x', 'ids': [99]}
                return sheet(posting=printed[-1].cite(title) if cited else bad,
                             company=printed[-1].cite(COMPANY) if cited else bad,
                             needs_user='要你登入', needs_user_tab='7:p1', needs_user_quote=QUESTION,
                             application_history_url='https://jobs.example/me/applications',
                             profile_registration={'name': '英文履歷', 'url': 'https://jobs.example/profile/9'})
            _code, browser = self.go('fill', filled(build, after=after))
            job = self.home.jobs()[URL]
            renamed = cited and title != TITLE
            expect(self, 'F-18', (job['title'], job['company']), (title if renamed else TITLE, COMPANY), case)
            apply = self.card()['apply']
            expect(self, 'F-19', (apply['workspace'].get('handoff_page'), browser.handed), ('p1', ['7:p1']), case)
            expect(self, 'F-20', bool(apply.get('ev')), shot, case)
            reg = self.home.registry()
            expect(self, 'F-27', reg.get('_application_records', {}).get(PLATFORM),
                   'https://jobs.example/me/applications', case)
            entry = reg[PLATFORM][f'{LANG}/one']
            expect(self, 'F-27', (entry['read'], entry.get('by'), entry.get('name')),
                   ('https://jobs.example/profile/9', 'agent', '英文履歷'), case)


class FormPageTest(FillCase):
    """F-20:表單那一頁是 agent 說的、核對在最後一次拍改後裡的分頁,不是工作區裡最後列的那一頁;沒寫或對不上就不猜、不沿用。"""

    def test_the_form_page_is_the_one_the_agent_named(self):
        other = Tab('p9', 'https://jobs.example/company', I.READ, (f'{TITLE} 公司介紹',), True)    # 職缺名稱也出現在這一頁
        for where, said in itertools.product(('first', 'middle', 'last'), ('right', 'missing', 'not_there', 'other_workspace')):
            self.start('todo')
            form = form_tab(TITLE, COMPANY, QUESTION, ANSWER, label='p3')
            after = {'first': (other, form), 'middle': (other, form, other), 'last': (form, other)}[where]
            after = tuple(dataclasses.replace(tab, label=f'p{n}') if tab is other else tab for n, tab in enumerate(after, 5))
            cells = sheet(form_tab={'right': '7:p3', 'not_there': '7:p77', 'other_workspace': '8:p3'}.get(said))     # missing:null
            self.go('fill', filled(lambda _p, cells=cells: cells, after=after))
            apply = self.card()['apply']
            known = said == 'right'
            unknown = [m['code'] for m in apply['fill_changes']['marks'] if m['code'] == 'FORM_PAGE_UNKNOWN']
            expect(self, 'F-20', (apply['tab_id'], apply['workspace']['page'], bool(apply.get('ev')), unknown),
                   ('7:p3' if known else '', 'p3' if known else '', known, [] if known else ['FORM_PAGE_UNKNOWN']), (where, said))

    def test_an_unknown_form_page_is_said_so_downstream(self):
        import apply_tab
        import next_step
        self.home.board = os.path.join(self.home.home, 'copy.html')
        self.start('todo')
        self.go('fill', filled(lambda _p: sheet(form_tab='7:p77'), after=(form_tab(TITLE, COMPANY, QUESTION, ANSWER),)))
        fb = self.home.fb()
        expect(self, 'F-20', (fb[URL]['apply']['tab_id'], fb[URL]['apply']['workspace']['id']), ('', 7))
        with self.assertRaises(LookupError) as raised:
            apply_tab._lookup(URL, self.home.board)
        expect(self, 'F-20', '程式不知道是哪一頁' in str(raised.exception), True)
        fb[URL]['ds'] = 'unsure'
        fb[URL]['apply']['submit_fail'] = {'at': '2026-01-02'}
        view = next_step.card(fb, URL)['view']
        text = ' '.join(row[1] for row in view['line'])
        expect(self, 'F-20', ('程式不知道是哪一頁' in text, '已經不在了' in text), (True, False))
        self.start('todo')
        self.go('fill', filled(lambda _p: sheet(form_tab='7:p77'), after=(form_tab(TITLE, COMPANY, QUESTION, ANSWER),)))
        code, _browser = self.go('confirm', browser=FakeBrowser())
        expect(self, 'F-20', (code, self.state()), (0, 'parked'))


    def test_a_page_the_program_did_not_compare_still_opens_from_the_card(self):
        """#783:改後才出現的那一頁沒逐處比,卡上照樣附它的截圖、點得開,不叫他自己去工作區找。"""
        import next_step
        self.start('todo')
        login = form_tab('請先登入', label='p2', url=URL + '/login')
        self.go('fill', filled(after=(form_tab(TITLE, COMPANY, QUESTION, ANSWER), login)))
        view = next_step.card(self.home.fb(), URL)['view']
        opened = [(row['source'], word) for row in view['evidence'] if URL + '/login' in row['text']
                  for shot, word in row.get('shots', ()) if evidence.path(URL, shot, self.home.board)]
        expect(self, '#783 #807', opened, [('程式沒比到', '改後截圖')])     # 在「看證據」裡,標程式沒比到

    def test_a_workspace_with_no_known_form_page_is_still_closed_after_the_card_leaves(self):
        """F-20:不知道是哪一頁時 workspace.page 是空的;卡離開排進收尾後,收尾只核對工作區身分,工作區照樣關掉,不留在清單裡重試。"""
        import board_doc
        import chrome_door
        import delivery_core
        self.start('todo')
        self.go('fill', filled(lambda _p: sheet(form_tab='7:p77'), after=(form_tab(TITLE, COMPANY, QUESTION, ANSWER),)))
        board_doc.set_fb(lambda fb: delivery_core.fire(fb, URL, 'leave'), live=self.home.board)
        queued = self.home.fb().get('__browser_cleanup__')
        expect(self, 'F-20', [item['workspace'].get('page') for item in queued or []], [''])
        reply = mock.Mock(returncode=0, stderr='', stdout='@@jobsalvo-ego@@' + json.dumps({'ok': True, 'value': {'closedSpace': True}}))
        with mock.patch.object(chrome_door, 'ego_bin', return_value='/bin/ego'), mock.patch('subprocess.run', return_value=reply):
            chrome_door.close_if_idle(self.home.board)
        expect(self, 'F-20', self.home.fb().get('__browser_cleanup__') or [], [])


class CardKeepTest(FillCase):
    """F-18、F-27:這個平台已登記的那一份不被 agent 說的蓋掉;職稱、公司只有一個核對到,卡名不改。"""

    def test_a_registered_profile_and_a_half_verified_name_stay(self):
        self.start('todo', registered=True)
        after = (form_tab('Engineer II', COMPANY, QUESTION),)
        self.go('fill', filled(lambda p: sheet(posting=p[-1].cite('Engineer II'), company={'material_id': 'x', 'ids': [99]},
                                               profile_registration={'name': '英文履歷', 'url': 'https://jobs.example/profile/9'}),
                               after=after))
        entry = self.home.registry()[PLATFORM][f'{LANG}/one']
        expect(self, 'F-27', (entry['read'], entry.get('name')), (PROFILE_URL, None))
        expect(self, 'F-18', self.home.jobs()[URL], JOB)


class RefillTest(FillCase):
    """F-21、F-23:這一輪跑的時候改了的答案重新標待重打、確認擋住;送出結果不明與換履歷的記號寫回時不消失。"""

    def test_marks_set_during_the_round_survive_the_write_back(self):
        import board_doc
        import delivery_core
        for during in ('answer', 'files', 'submit_fail'):
            card = {'apply': dict(copy.deepcopy(APPLY), **({'submit_fail': {'at': 'T1'}} if during == 'submit_fail' else {})),
                    'form': {'plat': PLATFORM, 'f': FORM_ROWS, 'at': 'T0'}}
            self.start('stuck', card)

            def build(_printed, during=during):
                def change(fb):
                    if during == 'answer':
                        fb['__ans__'][0]['v'] = 'No'
                    elif during == 'files':
                        delivery_core.fire(fb, URL, 'files_changed', why='換了履歷')
                board_doc.set_fb(change, live=self.home.board)
                return sheet(fields=[{'q': QUESTION, 'src': 'bank', 'k': 'k1'}])
            self.go('fix', filled(build), note='改一下')
            card = self.card()
            expect(self, 'F-21', [row.get('refill') for row in card['form']['f'] if row.get('k') == 'k1'],
                   [1 if during == 'answer' else None], during)       # 沒改的答案不標待重打
            if during == 'answer':
                self.assertIsNotNone(form_record.approval_problem(self.home.fb(), URL, None), 'F-21')
            elif during == 'files':
                expect(self, 'F-23', (self.state(), bool(card['apply'].get('stale'))), ('stale', True))
            else:
                expect(self, 'F-23', card['apply'].get('submit_fail'), {'at': 'T1'})


class PickTest(FillCase):
    """F-29、F-30:一輪挑哪幾張卡只照投遞狀態表;修改把修改說明交給 agent,接著同一個工作區與對話。"""

    PICKED = {'fill': {'todo', 'stuck'}, 'fix': {'parked', 'stuck'}, 'submit': {'confirmed'}}

    def test_cards_are_picked_by_the_state_table_only(self):
        for stage, state, note in itertools.product(('fill', 'fix', 'submit'), I.DELIVERY, ('', '請改成英文')):
            case = (stage, state, note)
            card = {'apply': copy.deepcopy(APPLY), 'form': {'plat': PLATFORM, 'f': FORM_ROWS, 'at': 'T0'}}
            if state == 'confirmed':
                self.confirmed()
            else:
                self.start(state, card)
            _code, browser = self.go(stage, filled(), note=note)
            want = state in self.PICKED[stage] and (stage != 'fix' or bool(note))
            expect(self, 'F-29', bool(browser.prompts), want, case)
            if want and stage == 'fix':
                expect(self, 'F-30', (note in browser.prompts[0], browser.resumed[0]), (True, ('sess-1', 'fake-agent')),
                       case)
            expect(self, 'F-24', browser.fetches, [], case)


class ContinueTest(FillCase):
    """F-30:填表開一個新的工作區、新的對話;修改接著卡上的工作區與對話,設定裡那個 agent 換了 runtime 就開新對話。"""

    def test_fill_starts_fresh_and_fix_continues(self):
        card = {'apply': copy.deepcopy(APPLY), 'form': {'plat': PLATFORM, 'f': FORM_ROWS, 'at': 'T0'}}
        for stage, can_open in (('fill', True), ('fill', False), ('fix', False)):
            case = (stage, can_open)
            self.start('stuck', card)
            browser = FakeBrowser(filled(), can_open=can_open)
            self.go(stage, browser=browser, note='改一下')
            expect(self, 'F-30', (browser.resumed, self.state()),
                   {('fill', True): ([(None, None)], 'parked'), ('fill', False): ([], 'nopage'),
                    ('fix', False): ([('sess-1', 'fake-agent')], 'parked')}[case], case)

    def test_a_fix_after_the_agent_changed_runtime_starts_a_new_conversation(self):
        import config as cf
        from unittest import mock
        for runtime in ('claude', 'codex'):
            self.start('parked', {'apply': dict(copy.deepcopy(APPLY), runtime='claude'),
                                  'form': {'plat': PLATFORM, 'f': FORM_ROWS, 'at': 'T0'}})
            with mock.patch.dict(cf.C, {'agent': {'agents': [{'id': 'fake-agent', 'runtime': runtime}]}}):
                _code, browser = self.go('fix', filled(), note='改一下')
            expect(self, 'F-30', browser.resumed, [('sess-1' if runtime == 'claude' else None, 'fake-agent')], runtime)


class StatusTest(FillCase):
    """F-29、I-04:看板讀的狀態檔(跑的時候 agent、跑完 done 與每張結果)、跑幾張(--limit)、只跑這一張(--url);
    看板上沒有的卡不跑;結束碼照每張有沒有交件;看板流水帳記是填表改的;沒給瀏覽器就開真的(填表開新的,修改、確認接卡上的工作區)。"""

    OTHER = 'https://jobs.example/jobs/2'

    def board(self):
        cards = {url: {'app': 'ship', 'ds': 'todo'} for url in (URL, self.OTHER, 'https://jobs.example/jobs/9')}
        self.home.write_board([dict(JOB), dict(JOB, id=self.OTHER)], dict(cards, __ans__=copy.deepcopy(BANK)),
                              status={'schema_version': board_status.SCHEMA_VERSION, 'issues': []})

    def status(self):
        with open(os.path.join(self.home.tmp['apply'], 'apply_status.json'), encoding='utf-8') as source:
            return json.load(source)

    def test_nothing_to_run_still_names_the_requested_card(self):
        """自動流程派這張去填、這張卻沒得跑:進度檔照樣寫這張的網址,自動流程才認得出派過了、不一直重派(main ea4ac831)。"""
        self.board()
        missing = 'https://jobs.example/jobs/9'
        code = fill.main(['--stage', 'fill', '--board', self.home.board, '--url', missing], browser=FakeBrowser())
        final = self.status()
        expect(self, 'F-29', (code, final['phase'], final['stage'], final['url'], final['n'], final['results']),
               (0, 'done', 'fill', missing, 0, []))

    def test_status_file_limit_and_one_card(self):
        import board_doc
        for argv, picked, second in (([], [URL, self.OTHER], I.TIMEOUT), (['--limit', '1'], [URL], I.HANDED),
                                     (['--url', self.OTHER], [self.OTHER], I.HANDED)):
            case = (argv, second)
            self.board()
            scripts = [filled(extra=(('tool', lambda _b: self.status()),)),
                       filled(end=second, extra=(('tool', lambda _b: self.status()),))][:len(picked)]
            browser = FakeBrowser(*scripts)
            code = fill.main(['--stage', 'fill', '--board', self.home.board, *argv], browser=browser)
            running, final = browser.outputs[0], self.status()
            started = {key: final.pop(key) for key in ('pid', 't0', 'at', 'finished_at')}
            fb = self.home.fb()
            rows = [{'url': url, 'ok': not (n and second != I.HANDED), 'round': fb[url]['apply']['round_id']}
                    for n, url in enumerate(picked)]
            expect(self, 'I-04', ({key: running[key] for key in running if key not in ('pid', 't0', 'at')}, started['pid'],
                                  started['t0'] <= started['finished_at'] <= started['at']),
                   ({'phase': 'agent', 'n': len(picked), 'done': 0, 'stage': 'fill', 'url': ''.join(argv[1:]) if '--url' in argv else '',
                     'results': [], 'msg': f'已處理 0/{len(picked)} 張'}, os.getpid(), True), case)
            if len(picked) == 2:       # 第二張跑的時候:第一張的結果已經在狀態檔
                middle = browser.outputs[1]
                expect(self, 'I-04', (middle['phase'], middle['done'], middle['results'], middle['msg']),
                       ('agent', 1, rows[:1], '已處理 1/2 張'), case)
            expect(self, 'F-29', (final, code), ({'phase': 'done', 'n': len(picked), 'done': len(picked), 'stage': 'fill',
                                                  'url': ''.join(argv[1:]) if '--url' in argv else '', 'results': rows,
                                                  'msg': f'已處理 {len(picked)}/{len(picked)} 張'},
                                                 int(any(not row['ok'] for row in rows))), case)
            with open(board_doc.journal_path(self.home.board), encoding='utf-8') as marks:
                by = {json.loads(line).get('by') for line in marks}
            expect(self, 'I-04', by, {'fill'}, case)
            os.remove(board_doc.journal_path(self.home.board))

    def test_the_real_browser_opens_where_the_card_left_off(self):
        from unittest import mock
        self.start('parked', {'apply': copy.deepcopy(APPLY), 'form': {'plat': PLATFORM, 'f': FORM_ROWS, 'at': 'T0'}})
        with mock.patch.object(fill, 'Real') as real:
            real.return_value, real.for_workspace.return_value = FakeBrowser(filled()), FakeBrowser(filled(), filled())
            fill.main(['--stage', 'fix', '--url', URL, '--board', self.home.board, '--note', '改一下'])
            fill.main(['--stage', 'confirm', '--url', URL, '--board', self.home.board])
            self.start('todo')
            fill.main(['--stage', 'fill', '--url', URL, '--board', self.home.board])
        expect(self, 'F-30', (real.call_args_list, real.for_workspace.call_args_list),
               ([mock.call()], [mock.call(7, 'accept'), mock.call(7, 'accept')]))


class PickMoreTest(FillCase):
    """F-29:修改要有一個理由(修改說明、要重翻的答案、待重打的答案);可投遞夾沒照卡上的選擇建好的不填、不改。"""

    def test_fix_needs_a_reason(self):
        """no_form:表單沒記進去而卡住的卡(沒有 form),沒理由一樣不修改。"""
        for reason in ('none', 'note', 'translate', 'refill', 'no_form'):
            rows = [dict(row, **({'refill': 1} if reason == 'refill' and row.get('k') else {})) for row in FORM_ROWS]
            form = {} if reason == 'no_form' else {'form': {'plat': PLATFORM, 'f': rows, 'at': 'T0'}}
            self.start('parked' if form else 'stuck', dict({'apply': copy.deepcopy(APPLY)}, **form),
                       bank=[dict(BANK[0], **({'tr': 1} if reason == 'translate' else {}))])
            _code, browser = self.go('fix', filled(), note='改一下' if reason == 'note' else '')
            expect(self, 'F-29', bool(browser.prompts), reason not in ('none', 'no_form'), reason)

    def test_a_folder_built_for_another_resume_is_not_filled(self):
        import card_identity
        import config as cf
        for stage, built in itertools.product(('fill', 'fix'), ('one', 'two')):
            case = (stage, built)
            self.start('todo' if stage == 'fill' else 'parked',
                       {'apply': copy.deepcopy(APPLY), 'form': {'plat': PLATFORM, 'f': FORM_ROWS, 'at': 'T0'}})
            folder = os.path.join(cf.SHIP_DIR, f'card-{card_identity.card_id_from_url(URL)}')
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, 'ship.json'), 'w', encoding='utf-8') as target:
                json.dump({'files': ['resume.pdf'], 'variant': built, 'lang': LANG}, target)
            _code, browser = self.go(stage, filled(), note='改一下')
            expect(self, 'F-29', bool(browser.prompts), built == 'one', case)
            shutil.rmtree(folder)


class SubmitRecordTest(FillCase):
    """F-16、F-17:送出那一輪的紀錄:送出了記在 apply.sent(agent 說的、引的段、程式讀到、要本人處理的、停在哪),
    沒送出卡回卡住、要本人處理的與停在哪列在卡上。"""

    def test_what_the_submit_round_leaves_on_the_card(self):
        receipt = 'Application received'
        form = {'plat': PLATFORM, 'f': FORM_ROWS, 'at': 'T0'}
        import board_doc
        for said in ('true', 'false', 'uncited'):
            self.confirmed()
            os.remove(board_doc.journal_path(self.home.board))
            page = (form_tab(receipt),)
            cited = []

            def build(printed, said=said):
                cited.append(printed[-1].cite(receipt) if said != 'uncited' else {'material_id': 'x', 'ids': [1]})
                return {'submitted': 'true' if said == 'uncited' else said, 'evidence': cited[-1], 'needs_user': '要你看信',
                        'stopped': '停在感謝頁'}
            self.go('submit', Script((('before', page), ('after', page)), build))
            with open(board_doc.journal_path(self.home.board), encoding='utf-8') as marks:
                expect(self, 'I-04', {json.loads(line).get('by') for line in marks}, {'fill'}, said)
            card = self.card()
            if said == 'true':
                at = card['apply']['sent'].pop('at')
                expect(self, 'F-16', (card, datetime.datetime.fromisoformat(at).date()), ({
                    'app': 'sent', 'ds': 'sent', 'form': dict(form, lock=1),
                    'sent_v': 'zh-one', 'sent_att': ['report'],         # 寄出的那一份(統計表、卡上顯示)
                    'apply': dict({key: value for key, value in APPLY.items() if key != 'workspace'}, tab_id='',
                                  sent={'said': 'true', 'evidence': cited[-1], 'source': E.PROGRAM,
                                        'needs_user': '要你看信', 'stopped': '停在感謝頁'})}, datetime.date.today()))
            elif said == 'false':
                expect(self, 'F-17', card, {'app': 'ship', 'ds': 'stuck', 'form': form,
                                            'apply': dict(APPLY, issues=['要你看信', '停在感謝頁'])})
            else:
                expect(self, 'F-17', (self.state(), card['apply']['submit_fail']['source']), ('unsure', E.AGENT))


class RenameTest(FillCase):
    """F-18:卡名照核對過的職稱、公司改(職稱、公司、卡片標題,標題後面的來源連結留著),可投遞夾跟著改名。"""

    def test_the_card_and_its_folder_take_the_verified_name(self):
        import card_identity
        import config as cf
        link = ' ([jobs.example](https://jobs.example/jobs/1))'
        self.start('todo', job={'target': f'{TITLE} - {COMPANY}{link}'})
        os.makedirs(os.path.join(cf.SHIP_DIR, f'old-{card_identity.card_id_from_url(URL)}'))
        after = (form_tab('Engineer II', '艾克米', QUESTION),)
        self.go('fill', filled(lambda printed: sheet(posting=printed[-1].cite('Engineer II'), company=printed[-1].cite('艾克米')),
                               after=after))
        expect(self, 'F-18', (self.home.jobs()[URL], os.listdir(cf.SHIP_DIR)),
               (dict(JOB, title='Engineer II', company='艾克米', target=f'Engineer II · 艾克米{link}'),
                [f'Engineer_II_·_艾克米-{card_identity.card_id_from_url(URL)}']))


class NotesTest(FillCase):
    """A-38、A-39:agent 寫的網站筆記照主機歸檔、每主機留最新 12 條、照寫的收;下一輪交給 agent 的只有使用者寫的(#831),
    agent 寫的只給使用者看,agent 拿不到筆記 ID、刪不掉任何一條。"""

    def test_website_notes(self):
        import website_notes
        self.start('todo')
        website_notes.user_action('add', URL, '使用者的筆記')
        notes = [{'url': URL, 'note': f'第 {n} 條'} for n in range(13)] + [{'url': 'https://other.example/y', 'note': '別站'}]
        self.go('fill', filled(lambda _p: sheet(website_notes=notes)))
        kept = website_notes.for_url(URL)
        agent = [n['text'] for n in kept if n.get('author') != 'user']
        expect(self, 'A-38', (len(agent), '第 12 條' in agent, '第 0 條' in agent), (12, True, False))
        expect(self, 'A-38', [n['text'] for n in kept if n.get('author') == 'user'], ['使用者的筆記'])
        expect(self, 'A-39', [n['text'] for n in website_notes.for_url('https://other.example/z')], ['別站'])
        round_id = self.card()['apply']['round_id']
        expect(self, 'A-38', {(n['card_id'], n['round_id']) for n in kept if n.get('author') != 'user'}, {(URL, round_id)})
        self.start('todo')
        _code, browser = self.go('fill', filled())
        given = re.search(r'使用者寫的網站筆記:\n(.*)', browser.prompts[0]).group(1)
        expect(self, 'A-38', (json.loads(given), '第 12 條' in browser.prompts[0], 'website_notes_remove' in browser.prompts[0]),
               ({'jobs.example': ['使用者的筆記']}, False, False))


class PromptTest(FillCase):
    """F-02、A-32:給填表 agent 的指示逐字(bare:卡上沒選履歷,沒有要寄的檔):目標照這一輪是填表還是送出;材料有職缺、卡片資料、要寄的檔(送出檔名優先)與讀得到的檔、
    常用答案、這張卡選的平台履歷名稱、其他平台登記的履歷頁(不含檔案)、待重翻的答案、網站筆記、修改說明、使用者填表規則;
    工具、界線、交件單格式;整段指示記進這一輪的證據夾(看板副本放暫存夾)。"""

    LIMITS = ('依材料與使用者答案填表。只讀材料列出的履歷、附件與平台履歷；不新增、替換或改寫個人資料。',
              '直接上傳只能用這張卡列出的個別檔案整套，或合併版單一檔案，不混用；不自行產檔、合併或另找檔案。',
              '不輸入密碼。', 'fill 與 fix 不按送出; submit 只在使用者確認後送出。',
              '填表交件要說明實際改了哪些欄位;不確定或未完成時照實交件。')

    def full(self):
        """修改那一輪:送出檔名、填表規則、選好的平台履歷有名稱、別的平台登記了網頁與檔案、要重翻的答案、網站筆記。"""
        import config as cf
        import website_notes
        path = os.path.join(self.home.home, cf.NAME)
        with open(path, encoding='utf-8') as source:
            settings = json.load(source)
        settings['resume']['resumes'][0]['send_names'] = {LANG: 'Ann-CV'}
        with open(path, 'w', encoding='utf-8') as target:
            json.dump(settings, target, ensure_ascii=False)
        cf.reload(self.home.home)
        with open(cf.APPLY_RULES, 'w', encoding='utf-8') as rules:
            rules.write('  先填英文  \n')
        self.start('parked', {'apply': copy.deepcopy(APPLY), 'form': {'plat': PLATFORM, 'f': FORM_ROWS, 'at': 'T0'}},
                   bank=[dict(BANK[0], tr=1)], job={'memo': '遠端'})
        profile_sync.remember(PLATFORM, LANG, 'one', PROFILE_URL, name='英文履歷')
        profile_sync.remember('other.example', LANG, 'one', 'https://other.example/me/cv')
        profile_sync.remember('files.example', LANG, 'one', 'https://files.example/cv.pdf')
        profile_sync.remember('third.example', LANG, 'one', 'https://third.example/me')
        registry = os.path.join(self.home.home, 'profiles.json')
        with open(registry, encoding='utf-8') as source:
            reg = json.load(source)
        reg['files.example'][f'{LANG}/one']['kind'] = 'file'
        with open(registry, 'w', encoding='utf-8') as target:
            json.dump(reg, target, ensure_ascii=False)
        website_notes.user_action('add', URL, '先登入')
        website_notes.remember([{'url': URL, 'note': 'agent 寫的'}])     # agent 寫的只給使用者看(#831)
        resume = os.path.join(self.home.home, '.rendered', 'resume', 'one.pdf')
        return {'要送的檔案': 'Ann-CV\nreport.pdf', '履歷與附件材料檔案': f'- Ann-CV: {resume}\n- report.pdf: {self.report}',
                '平台履歷名稱': '英文履歷', '其他已登記的平台履歷頁': f'- other.example {LANG}/one: https://other.example/me/cv\n'
                                                                f'- third.example {LANG}/one: https://third.example/me',
                '待重翻的答案': json.dumps([{'k': 'k1', 'zh': '是', 'old_en': ANSWER}], ensure_ascii=False),
                '使用者寫的網站筆記': '{"jobs.example": ["先登入"]}',
                '修改說明': '改一下', '使用者填表規則': '先填英文'}

    def test_brief_carries_the_material_and_is_recorded(self):
        import config as cf
        import evidence
        from web.brief import AFTER, BEFORE, EGO, FORM_PLAN, SHELL
        self.report = os.path.join(self.home.home, 'resume', 'report.pdf')
        resume = os.path.join(self.home.home, '.rendered', 'resume', 'one.pdf')
        plain = {'要送的檔案': '履歷\nreport.pdf', '履歷與附件材料檔案': f'- 履歷: {resume}\n- report.pdf: {self.report}',
                 '平台履歷名稱': '', '其他已登記的平台履歷頁': '(沒有)', '待重翻的答案': '[]',
                 '使用者寫的網站筆記': '{}', '修改說明': '', '使用者填表規則': '(沒有另外交代)'}
        for how in ('fill', 'submit', 'bare', 'fix'):
            self.home.board = os.path.join(self.home.home, f'copy-{how}.html')
            if how == 'fix':
                wanted = self.full()
            else:
                if os.path.exists(cf.APPLY_RULES):
                    os.remove(cf.APPLY_RULES)
                self.confirmed() if how == 'submit' else self.start('todo', job={'resume': {}} if how == 'bare' else None)
                wanted = dict(plain, **({'要送的檔案': '', '履歷與附件材料檔案': '(沒有可讀檔案)'} if how == 'bare' else {}))
            job, fb = self.home.jobs()[URL], self.home.fb()
            shutil.rmtree(evidence.root(self.home.board), ignore_errors=True)       # 副本共用一個暫存證據夾
            script = filled() if how != 'submit' else Script((), {'submitted': 'false', 'needs_user': '', 'stopped': ''})
            _code, browser = self.go('fill' if how == 'bare' else how, script, note='改一下' if how == 'fix' else '')
            got = sections(browser.prompts[0])
            materials = {'職缺網址': URL, '卡片資料': json.dumps(job, ensure_ascii=False), **dict(wanted),
                         '答案與履歷材料': form_record.shared_text(fb)}
            order = ('職缺網址', '卡片資料', '要送的檔案', '履歷與附件材料檔案', '答案與履歷材料', '平台履歷名稱',
                     '其他已登記的平台履歷頁', '待重翻的答案', '使用者寫的網站筆記', '修改說明', '使用者填表規則')
            expect(self, 'F-02', got, {
                '目標': '依使用者確認送出這張申請。' if how == 'submit' else '完成這張卡的申請表',
                '材料': '\n'.join(f'{title}:\n{materials[title]}' for title in order),
                '工具與用法': '\n'.join(tool.replace('{workspace}', '7') for tool in (EGO, BEFORE, AFTER, SHELL, FORM_PLAN)),
                '界線': '\n'.join(f'- {line}' for line in self.LIMITS),
                '交件單格式': (fill.SUBMIT_SHEET if how == 'submit' else fill.FILL_SHEET).format_text()}, how)
            root = evidence.root(self.home.board)
            recorded = [pathlib.Path(folder, name).read_text(encoding='utf-8') for folder, _dirs, names
                        in os.walk(os.path.join(root, '_fill')) for name in names if name.startswith('instruction')]
            expect(self, 'A-32', (recorded, browser.timeouts, root.startswith(self.home.home)),
                   ([browser.prompts[0]], [fill.TIMEOUT], False), how)


class ApprovalIdTest(FillCase):
    """#739:送出綁在派出那一刻的那一次確認;派出之後取消又確認,舊的那一輪不送、卡留在你已確認。"""

    def test_a_submit_sends_only_for_the_confirmation_it_was_dispatched_for(self):
        receipt = 'Application received'
        for pinned in ('same', 'stale', 'absent'):
            self.confirmed()
            approval_id = self.card()['approve']['id']
            ids = {'same': [json.dumps({URL: approval_id})], 'stale': [json.dumps({URL: 'cancelled-then-reconfirmed'})],
                   'absent': []}[pinned]
            page = (form_tab(receipt),)
            browser = FakeBrowser(Script((('before', page), ('after', page)), lambda printed: {
                'submitted': 'true', 'evidence': printed[-1].cite(receipt), 'needs_user': '', 'stopped': ''}))
            code = fill.main(['--stage', 'submit', '--url', URL, '--board', self.home.board,
                              *(['--approval-ids', *ids] if ids else [])], browser=browser)
            expect(self, 'F-16', (code, self.state(), len(browser.printed) > 0),
                   (1, 'confirmed', False) if pinned == 'stale' else (0, 'sent', True), pinned)

    def test_cancel_and_reconfirm_voids_the_first_id_and_undo_keeps_it(self):
        import delivery_state as ds
        self.confirmed()
        fb = self.home.fb()
        status = form_record.board_status(self.home.board)
        first = fb[URL]['approve']['id']
        before = copy.deepcopy(fb)
        ds.fire(fb, URL, 'unconfirm')
        undone = copy.deepcopy(fb)
        ds.restore(undone, next(slip for slip in ds.slips(before, fb) if slip['u'] == URL))
        ds.fire(fb, URL, 'confirm', approve=form_record.approval(fb, URL, '2026-01-04'))
        second = fb[URL]['approve']['id']
        stale = form_record.start_submit(copy.deepcopy(fb), URL, status, first)
        expect(self, 'F-16', (ds.state(undone[URL]), undone[URL]['approve']['id'] == first, second != first, stale,
                              form_record.start_submit(fb, URL, status, second), ds.state(fb[URL])),
               ('confirmed', True, True, '這次確認已作廢,不會送出', None, 'sending'), 'reconfirm')

    def test_each_card_keeps_the_id_it_had_when_the_round_was_dispatched(self):
        import board_doc
        receipt, second = 'Application received', 'https://jobs.example/jobs/2'
        self.confirmed()

        def copy_card(fb):
            fb[second] = copy.deepcopy(fb[URL])
            fb[second]['approve'] = form_record.approval(fb, second, '2026-01-03')
        board_doc.set_fb(copy_card, live=self.home.board)

        def reconfirm_second(_browser):      # 第一張送出中,他把第二張取消又確認
            board_doc.set_fb(lambda fb: fb[second].update(approve=form_record.approval(fb, second, '2026-01-04')),
                             live=self.home.board)
            return ''
        page = (form_tab(receipt),)

        def sent(printed):
            return {'submitted': 'true', 'evidence': printed[-1].cite(receipt), 'needs_user': '', 'stopped': ''}
        browser = FakeBrowser(Script((('before', page), ('tool', reconfirm_second), ('after', page)), sent),
                              Script((('before', page), ('after', page)), sent))
        code = fill.main(['--stage', 'submit', '--board', self.home.board], browser=browser)
        import delivery_core
        expect(self, 'F-16', (code, delivery_core.state(self.card()), delivery_core.state(self.home.fb()[second])),
               (1, 'sent', 'confirmed'), 'two cards')


class OneCardErrorTest(FillCase):
    """一張卡在卡上開始之後出錯:這張照狀態收掉(正在送出 → 結果不明、正在填 → 卡住)並寫原因,下一張照跑。"""

    def test_an_error_on_one_card_settles_it_and_the_next_card_still_runs(self):
        import board_doc
        import delivery_core
        from unittest import mock
        receipt, second = 'Application received', 'https://jobs.example/jobs/2'
        for stage in ('submit', 'fill'):
            if stage == 'submit':
                self.confirmed()
            else:
                self.start('todo')

            def copy_card(fb):
                fb[second] = copy.deepcopy(fb[URL])
                if stage == 'submit':
                    fb[second]['approve'] = form_record.approval(fb, second, '2026-01-03')
            board_doc.set_fb(copy_card, live=self.home.board)
            page = (form_tab(receipt),)
            script = (Script((('before', page), ('after', page)), lambda printed: {
                'submitted': 'true', 'evidence': printed[-1].cite(receipt), 'needs_user': '', 'stopped': ''})
                if stage == 'submit' else filled())
            real, calls = fill.run_round, []

            def broken_first(*args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError('ego 斷線')
                return real(*args, **kwargs)
            with mock.patch.object(fill, 'run_round', side_effect=broken_first):
                code = fill.main(['--stage', stage, '--board', self.home.board], browser=FakeBrowser(script))
            first, after = self.card(), self.home.fb()[second]
            why = '送出結果不明' if stage == 'submit' else '這一輪沒完成'
            said = first['apply']['submit_fail']['stopped'] if stage == 'submit' else first['apply'].get('issues')
            expect(self, 'F-16 F-17', (code, delivery_core.state(first), said,
                                        delivery_core.state(after)),
                   (1, 'unsure' if stage == 'submit' else 'stuck', why if stage == 'submit' else [why],
                    'sent' if stage == 'submit' else 'parked'),
                   stage)


class ParallelTest(FillCase):
    """#777:一次選好幾張,同時跑(flow.fill_parallel,預設 3)。每張卡拿到自己的瀏覽器(browser 給函式,網址進、瀏覽器出),
    不靠網址或頁面內容分辨哪頁是哪張卡的;結果跟一張一張跑(fill_parallel=1)逐字相同,看板沒有一筆遺失。"""

    URLS = (URL, 'https://jobs.example/jobs/2', 'https://jobs.example/jobs/3')

    class Flight:
        """同時有幾支假 agent 在跑。進場的先等到 parties 支都進來才往下走,同時跑不靠各支剛好在 wait 的秒數裡重疊;
        真的沒有那麼多支同時跑時,等滿 60 秒放行,peak 照實少。"""
        def __init__(self, parties):
            self.lock, self.now, self.peak = threading.Lock(), 0, 0
            self.gate = threading.Barrier(parties, timeout=60)

        def enter(self, _browser):
            with self.lock:
                self.now += 1
                self.peak = max(self.peak, self.now)
            with contextlib.suppress(threading.BrokenBarrierError):
                self.gate.wait()
            return ''

        def leave(self, _browser):
            with self.lock:
                self.now -= 1
            return ''

    def board(self, count, bank=None):
        urls = self.URLS[:count]
        cards = {url: {'app': 'ship', 'ds': 'todo'} for url in urls}
        self.home.write_board([dict(JOB, id=url) for url in urls], dict(cards, __ans__=copy.deepcopy(bank or BANK)),
                              status={'schema_version': board_status.SCHEMA_VERSION, 'issues': []})

    def run_all(self, workers, scripts, bank=None):
        """每張卡一支自己的假瀏覽器、一份自己的劇本;回 (結束碼, 秒數, 各卡瀏覽器)。"""
        import config as cf
        self.board(len(scripts), bank)
        browsers = {url: FakeBrowser(script) for url, script in zip(self.URLS, scripts)}
        started = time.monotonic()
        with mock.patch.dict(cf.C['flow'], {'fill_parallel': workers}):
            code = fill.main(['--stage', 'fill', '--board', self.home.board], browser=browsers.__getitem__)
        return code, time.monotonic() - started, browsers

    def card_script(self, n, flight=None, seconds=0, same_page=False, more=(), **cells):
        page = ('', '') if same_page else (f'Question {n}', f'Value {n}')
        extra = (('tool', flight.enter), ('wait', seconds), ('tool', flight.leave)) if flight else ()
        return filled(lambda _p: sheet(fields=[{'q': page[0] or QUESTION, 'src': 'rz', 'value': page[1] or ANSWER}, *more],
                                       **cells),
                      before=(form_tab(f'{TITLE} at {COMPANY}', page[0] or QUESTION),),
                      after=(form_tab(f'{TITLE} at {COMPANY}', page[0] or QUESTION, page[1] or ANSWER),), extra=extra)

    def outcome(self, count):
        """看板上這幾張卡與答案庫、卡片資料、狀態檔的每筆結果;時間與輪編號換成固定字。"""
        fb = self.home.fb()
        status = self.home.status_file('apply')
        status['results'] = sorted(({key: row[key] for key in ('url', 'ok')} for row in status['results']),
                                   key=lambda row: row['url'])
        text = json.dumps({'cards': {url: fb[url] for url in self.URLS[:count]}, 'bank': sorted(fb['__ans__'], key=lambda entry: entry['k']),
                           'jobs': self.home.jobs(),
                           'status': {key: status[key] for key in ('phase', 'n', 'done', 'results', 'msg')}},
                          ensure_ascii=False, sort_keys=True, indent=1)
        return re.sub(r'\d{8}-\d{6}-\d{6}|\d{4}-\d\d-\d\dT[\d:.]+|(?<=snap-before-)[0-9a-f]{8}|(?<=snap-after-)[0-9a-f]{8}', 'X', text)

    SHARED = {'q': 'Notice period', 'src': 'bank', 'value': 'Two weeks', 'zh': '兩週', 'why': '履歷', 'kind': 'val'}
    EXISTING = {'q': QUESTION, 'src': 'bank', 'value': ANSWER, 'k': 'k1'}

    def own_answers(self, n):
        """三張卡都寫同一題新答案(合成一條)、其中兩張各寫自己的新答案、其中兩張引答案庫原有的 k1。"""
        return [self.SHARED,
                *([{'q': f'Salary {n}', 'src': 'bank', 'value': f'{n}00', 'zh': f'{n}百', 'why': '履歷', 'kind': 'val'}]
                  if n < 2 else []),
                *([self.EXISTING] if n > 0 else [])]

    def test_three_at_once_leave_the_board_exactly_as_one_by_one(self):
        seen = {}
        for workers in (1, 3):
            flight = self.Flight(workers)
            # 越前面的越晚交件:同時跑時寫看板的順序跟選的順序相反
            scripts = [self.card_script(n, flight, (0.4, 0.2, 0.05)[n], more=self.own_answers(n),
                                        problems=[{'text': f'填不了 {n}'}] if n < 2 else []) for n in range(3)]
            code, _seconds, browsers = self.run_all(workers, scripts)
            seen[workers] = (code, self.outcome(3), flight.peak)
            expect(self, 'F-29', (code, [len(b.printed) for b in browsers.values()]), (0, [2, 2, 2]), workers)
        bank = json.loads(seen[3][1])['bank']
        expect(self, 'F-25', sorted(entry['q'] for entry in bank),
               sorted([QUESTION, 'Notice period', 'Salary 0', 'Salary 1']), 'every new answer is on the board once')
        expect(self, 'F-21', any('refill' in row for card in json.loads(seen[3][1])['cards'].values()
                                 for row in card['form']['f']), False, 'no sibling write read as a user edit')
        expect(self, 'F-29', (seen[1][0], seen[1][1]), (seen[3][0], seen[3][1]), 'same board')
        expect(self, 'F-29', (seen[1][2], seen[3][2]), (1, 3), 'cards at once')

    def test_cards_whose_pages_look_the_same_each_keep_their_own_result(self):
        import evidence
        flight = self.Flight(3)
        scripts = [self.card_script(0, flight, 0.4, same_page=True, problems=[{'text': '填不了 0'}]),
                   self.card_script(1, flight, 0.3, same_page=True, needs_user='要本人登入 1'),
                   self.card_script(2, flight, 0.2, same_page=True)]      # 越前面的越晚交件;頁面逐字相同
        code, _seconds, _browsers = self.run_all(3, scripts)
        fb, said = self.home.fb(), []
        for url in self.URLS:
            apply = fb[url]['apply']
            handoff = pathlib.Path(evidence.flow_round_dir('fill', apply['round_id'], self.home.board), 'handoff.json')
            said.append((self.state_of(fb[url]), apply['issues'], json.loads(handoff.read_text(encoding='utf-8'))['problems']))
        expect(self, 'F-29', (code, flight.peak, said),
               (0, 3, [('stuck', ['填不了 0'], [{'text': '填不了 0'}]),
                       ('stuck', ['agent 說要你本人處理：要本人登入 1'], []),
                       ('parked', [], [])]), 'identical pages')

    def state_of(self, card):
        import delivery_core
        return delivery_core.state(card)

    def test_one_card_breaking_settles_only_that_card_while_the_others_finish(self):
        import delivery_core
        flight, real = self.Flight(2), fill.run_round      # 第二張在派 agent 前就斷線,只有兩支進場
        scripts = [self.card_script(n, flight, 0.3) for n in range(3)]
        self.board(3)
        browsers = {url: FakeBrowser(script) for url, script in zip(self.URLS, scripts)}

        def broken_second(task, what, browser, **kwargs):      # 照瀏覽器認哪張卡:同時跑時先後不一定
            if browser is browsers[self.URLS[1]]:
                raise RuntimeError('ego 斷線')
            return real(task, what, browser, **kwargs)
        with mock.patch.object(fill, 'run_round', broken_second):
            code = fill.main(['--stage', 'fill', '--board', self.home.board], browser=browsers.__getitem__)
        fb, why = self.home.fb(), '這一輪沒完成'
        expect(self, 'F-16 F-17', (code, [delivery_core.state(fb[url]) for url in self.URLS], fb[self.URLS[1]]['apply']['issues'],
                                   flight.peak, sorted((row['url'], row['ok']) for row in self.home.status_file('apply')['results'])),
               (1, ['parked', 'stuck', 'parked'], [why], 2, [(url, url != self.URLS[1]) for url in self.URLS]), 'one broken')

    def test_each_card_submits_only_for_the_confirmation_it_was_dispatched_for(self):
        import board_doc
        import delivery_core
        receipt = 'Application received'
        self.confirmed()

        def more_cards(data, _fb):
            data['jobs'].append(dict(JOB, id=self.URLS[2]))
        board_doc.set_data(more_cards, live=self.home.board)

        def copy_cards(fb):
            for url in self.URLS[1:]:
                fb[url] = copy.deepcopy(fb[URL])
                fb[url]['approve'] = form_record.approval(fb, url, '2026-01-03')
        board_doc.set_fb(copy_cards, live=self.home.board)
        ids = {url: self.home.fb()[url]['approve']['id'] for url in self.URLS}
        page = (form_tab(receipt),)

        def sent(printed):
            return {'submitted': 'true', 'evidence': printed[-1].cite(receipt), 'needs_user': '', 'stopped': ''}
        flight = self.Flight(2)      # 第二張取消又確認過,不派 agent
        browsers = {url: FakeBrowser(Script((('before', page), ('tool', flight.enter), ('wait', 0.3), ('tool', flight.leave),
                                             ('after', page)), sent)) for url in self.URLS}
        pinned = dict(ids, **{self.URLS[1]: 'cancelled-then-reconfirmed'})      # 第二張派出之後取消又確認過
        code = fill.main(['--stage', 'submit', '--board', self.home.board, '--approval-ids', json.dumps(pinned)],
                         browser=browsers.__getitem__)
        fb = self.home.fb()
        expect(self, 'F-16', (code, [delivery_core.state(fb[url]) for url in self.URLS], [len(b.printed) for b in browsers.values()],
                              flight.peak),
               (1, ['sent', 'confirmed', 'sent'], [2, 0, 2], 2), 'pinned ids')

    def test_a_shared_answer_one_card_retranslated_is_not_read_as_a_user_edit_by_the_others(self):
        """他改了中文、答案 k1 標待重翻;三張都引 k1 並打上重翻後的英文。誰先交件誰把新英文記回答案庫,
        另外兩張不能把它當成他這一輪又改了(F-21),不管幾張同時跑,三張都不標待重打。"""
        seen = {}
        bank = [dict(BANK[0], tr=1)]
        for workers in (1, 3):
            flight = self.Flight(workers)
            more = [{'q': QUESTION, 'src': 'bank', 'value': 'Yes, authorized', 'k': 'k1'}]
            scripts = [self.card_script(n, flight, (0.4, 0.2, 0.05)[n], more=more) for n in range(3)]
            self.run_all(workers, scripts, bank)
            out = json.loads(self.outcome(3))
            seen[workers] = ([[row.get('refill') for row in card['form']['f']] for card in out['cards'].values()],
                             [(entry['k'], entry.get('v'), entry.get('tr')) for entry in out['bank']])
        expect(self, 'F-21', seen[3], seen[1], 'same as one by one')
        expect(self, 'F-21', seen[3], ([[None, None]] * 3, [('k1', 'Yes, authorized', None)]), 'nobody asked to retype')

    def test_any_kind_of_error_on_one_card_is_written_on_that_card_and_the_others_still_run(self):
        import delivery_core
        flight, real = self.Flight(2), fill.run_round
        scripts = [self.card_script(n, flight, 0.2) for n in range(3)]
        self.board(3)
        browsers = {url: FakeBrowser(script) for url, script in zip(self.URLS, scripts)}

        def broken_second(task, what, browser, **kwargs):
            if browser is browsers[self.URLS[1]]:
                raise TypeError('程式寫錯')
            return real(task, what, browser, **kwargs)
        with mock.patch.object(fill, 'run_round', broken_second):
            code = fill.main(['--stage', 'fill', '--board', self.home.board], browser=browsers.__getitem__)
        fb, why = self.home.fb(), '這一輪沒完成'
        status = self.home.status_file('apply')
        expect(self, 'F-16 F-17', (code, [delivery_core.state(fb[url]) for url in self.URLS], fb[self.URLS[1]]['apply']['issues'],
                                   status['phase'], sorted((row['url'], row['ok']) for row in status['results'])),
               (1, ['parked', 'stuck', 'parked'], [why], 'done', [(url, url != self.URLS[1]) for url in self.URLS]), 'type error')

    def test_two_cards_registering_the_same_platform_profile_at_once_keep_the_first_one(self):
        """同一個平台履歷兩張卡同時登記:先寫進登記表的算,後來的不蓋掉。讀登記表放慢,讓兩張同時查到「還沒登記」。"""
        import profile_registry
        flight, saved, load, save = self.Flight(3), [], profile_registry.load, profile_registry.save

        def slow_load(*args, **kwargs):
            time.sleep(0.15)
            return load(*args, **kwargs)

        def watch(registry, *args, **kwargs):
            entry = (registry.get(PLATFORM) or {}).get(f'{LANG}/one')
            if entry:
                saved.append(entry['read'])
            return save(registry, *args, **kwargs)
        scripts = [self.card_script(n, flight, 0.1, profile_registration={
            'name': f'履歷 {n}', 'url': f'https://jobs.example/profile/{n}'}) for n in range(3)]
        with mock.patch.object(profile_registry, 'load', slow_load), mock.patch.object(profile_registry, 'save', watch):
            self.run_all(3, scripts)
        expect(self, 'S-22', (self.home.registry()[PLATFORM][f'{LANG}/one']['read'], len(set(saved))), (saved[0], 1), saved)

    def test_three_cards_take_about_as_long_as_one(self):
        _code, one, _browsers = self.run_all(3, [self.card_script(0, self.Flight(1), 0.5)])
        flight = self.Flight(3)
        _code, three, _browsers = self.run_all(3, [self.card_script(n, flight, 0.5) for n in range(3)])
        self.assertEqual(flight.peak, 3)
        self.assertLess(three, one * 2, f'1 張 {one:.2f} 秒、3 張 {three:.2f} 秒')


if __name__ == '__main__':
    unittest.main()
