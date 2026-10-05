#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""幫你填表整條流程過安檢門(#316):填表 → 程式驗收 → 他按確認送出 → 程式送出。

用兩個 CLI 共用的瀏覽器門路各跑一次,判斷標準一樣:
  · agent 照做:一路到已送出。
  · agent 做完之後頁面上一格被改掉:他按確認前被擋、程式送出前也被擋,
    卡上寫出哪一格從什麼變成什麼和下一步。
  · 按了送出跳出驗證碼，agent 有當次證據確認沒送出:記成沒送出；表單還在本身不能判斷結果。
  · 送出結果不明的卡他按「確認沒送出」:這張跟送出有關的回報全部收掉。
  · agent 回報時自己寫了別的來源:照程式給的來源記;這張重填成功後,那一則收掉。

假的 agent 是換掉 agent_run 開行程那一步(subprocess.Popen):程式給它的環境(看板代號、回報來源)照真的傳進來,
它照指示寫交件單、自己跑 form_record 記表單、跑 agent_report 回報,寫一份動作紀錄;agent_run.run 本身照真的跑。"""
import copy
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾
from _env import read_fb  # noqa: E402
import agent_report           # noqa: E402
import agent_run as ar        # noqa: E402
import apply_run as run       # noqa: E402
import board_server as bs     # noqa: E402
import chrome_door            # noqa: E402
import delivery_state as ds   # noqa: E402
import fake_door as fc      # noqa: E402
import form_record as fr      # noqa: E402

U = 'https://jobs.lever.co/gate/1'
OKST = {'schema_version': 2, 'at': '2026-01-01 00:00', 'checked_links': True, 'issues': []}
FORM_PAGE = {'url': U + '/apply', 'title': 'Engineer - Example', 'lines': ['Engineer', 'Example'],
             'fields': [{'label': 'Full name', 'name': 'name', 'type': 'text', 'value': 'Alex Chen'},
                        {'label': 'Nationality', 'name': 'nat', 'type': 'text', 'value': 'Taiwan'}]}
HONEST = {'url': U, 'platform': 'Lever', 'tab_id': '7:p1', 'handoff': True, 'tab_url': U + '/apply',
          'posting': {'title': 'Engineer', 'company': 'Example', 'same_job': True},
          'delivery': {'method': 'no_profile'}, 'uploaded': [],
          'fields': [{'q': 'Full name', 'value': 'Alex Chen', 'src': 'rz'},
                     {'q': 'Nationality', 'value': 'Taiwan', 'src': 'bank', 'k': 'nat'}],
          'problems': [], 'notes': [], 'submitted': False}
THANKS = {'url': U + '/thanks', 'title': 'Thanks', 'lines': ['Application submitted'], 'fields': []}
SENT = {'submitted': True, 'clicked': True, 'reason': '本次申請已收到',
        'confirm_url': U + '/thanks', 'confirm_text': 'Application submitted'}


def captcha(page):
    """按了送出,網站跳出真人驗證:還是同一頁、表單還在。"""
    page['lines'] = ['Verify you are human'] + page['lines']


class CodexFlow(unittest.TestCase):
    RUNTIME = chrome_door.CODEX

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory(prefix='gate-flow-'))
        self.board = os.path.join(self.tmp, 'board.html')
        self.out = os.path.join(self.tmp, 'out')
        os.makedirs(self.out)
        fb = {'__ans__': [{'k': 'nat', 'q': 'Nationality', 'v': 'Taiwan', 'zh': '台灣', 'at': run.today()}],
              U: {'app': 'ship'}}
        _env.make_board(self.board, fb, data={'jobs': [{'id': U, 'target': '**Engineer · Example**'}], 'status': OKST},
                        sty='')
        self.page = copy.deepcopy(FORM_PAGE)
        self.sheet = copy.deepcopy(HONEST)
        self.submit_sheet = dict(SENT)
        self.after_submit = lambda page: page.clear() or page.update(copy.deepcopy(THANKS))
        self.said_from = None          # agent 回報時自己寫的來源
        self.rounds = []
        self.door = self.make_door()
        for p in self.patches():
            self.enterContext(p)

    def make_door(self):
        return fc.FakeDoor(self.RUNTIME, page=self.page, prepared={
            'workspace': {'id': 7, 'name': 'jobsalvo-gate-test', 'page': 'p1'},
            'tab_id': '7:p1', 'page': self.page})

    def patches(self):
        return [fc.installed(self.door),
                patch.object(run, 'out_dir', return_value=self.out),
                patch.object(run, 'prompt_for', side_effect=lambda stage, *a, **k: (f'指示:{stage}', self.out)),
                patch.object(ar.subprocess, 'Popen', side_effect=self.agent),
                patch.object(ar, 'wait_done', side_effect=lambda procs, timeout=None, paused=None: [
                    SimpleNamespace(status='completed', returncode=0, pid=4242)]),
                patch.object(bs, 'STATE', self.board), patch.object(bs, 'trigger_build', lambda: None),
                patch.object(bs, 'note_saved', lambda: None), patch.object(bs, 'is_real', lambda: True),
                patch('time.sleep')]

    # ---- 假的 agent(一個行程):照這一輪的指示做,環境是程式給的 ----
    def agent(self, argv, cwd=None, stdin=None, stdout=None, stderr=None, start_new_session=None, env=None):
        stage = os.path.basename(stdout.name).split('.')[0]          # fill / submit
        self.rounds.append(stage)
        with patch.dict(os.environ, env, clear=True):
            if stage == 'fill':
                with open(os.path.join(self.out, 'fill.json'), 'w', encoding='utf-8') as f:
                    json.dump(self.sheet, f, ensure_ascii=False)
                fr.record_fill(os.path.join(self.out, 'fill.json'), U)          # 它自己跑 form_record --from-fill
                if self.said_from:
                    with patch.object(sys, 'argv', ['agent_report.py', '--from', self.said_from, '--job', U,
                                                    '--need', '看一下', '這一頁要他本人登入']):
                        agent_report.main()
            elif stage == 'submit':
                self.after_submit(self.page)
                with open(os.path.join(self.out, 'submit.json'), 'w', encoding='utf-8') as f:
                    json.dump(self.submit_sheet, f, ensure_ascii=False)
        stdout.write(self.log_lines())
        stdout.flush()
        return SimpleNamespace(pid=4242)

    def log_lines(self):
        return ''.join(json.dumps(x) + '\n' for x in ({'type': 'thread.started', 'thread_id': 'S1'},
                                                       {'type': 'turn.completed'}))

    # ---- 他在看板上按的、程式跑的 ----
    def card(self):
        m = read_fb(self.board)[U]
        self.assertEqual(ds.problems(m), [], '卡上的資料跟投遞狀態對不上')   # 每一步都是狀態表允許、資料對得回狀態的樣子
        return m

    def open_reports(self):
        return [it for it in read_fb(self.board).get('__inbox__', []) if not it.get('done') and it.get('job') == U]

    def fill(self):
        return run.run_one('fill', U, self.board)

    def press(self, event, **data):
        rejected = []
        self.assertEqual(bs.write_fb({}, events=[{'u': U, 'ev': event, 'data': data}], rejected=rejected), [])
        return rejected

    def confirm(self):
        return self.press('confirm', approve=fr.approval(read_fb(self.board), U, run.now()))

    def submit(self):
        return run.run_one('submit', U, self.board)

    def filled_and_confirmed(self):
        ok, msg = self.fill()
        self.assertTrue(ok, msg)
        self.assertEqual(self.confirm(), [])
        self.assertEqual(ds.state(self.card()), 'confirmed')

    # ---- 情境 ----
    def test_an_honest_round_goes_all_the_way_to_sent(self):
        ok, msg = self.fill()
        self.assertTrue(ok, msg)
        self.assertEqual(ds.state(self.card()), 'parked')
        self.assertEqual(self.card()['apply']['seen']['fields'][1], ['Nationality', 'Taiwan'])   # 驗收時核對過的樣子
        self.assertEqual(self.confirm(), [])
        ok, msg = self.submit()
        self.assertTrue(ok, msg)
        self.assertEqual(ds.state(self.card()), 'sent')

    def test_a_field_changed_after_the_agent_is_stopped_before_he_can_confirm(self):
        ok, msg = self.fill()
        self.assertTrue(ok, msg)
        self.page['fields'][1]['value'] = 'Japan'                       # agent 做完之後,頁面上一格被改掉
        rejected = self.confirm()
        self.assertEqual(len(rejected), 1)
        self.assertIn('「Nationality」從「Taiwan」變成「Japan」', rejected[0]['msg'])
        m = self.card()
        self.assertEqual(ds.state(m), 'stuck')                          # 不是卡在按不下去:要 agent 改或重填
        self.assertIn('「Nationality」從「Taiwan」變成「Japan」', m['apply']['issues'][0])
        self.assertEqual(m['apply']['issues'][-1], run.NEXT_STEP)
        self.assertTrue(ds.allowed(m, 'fix_start') and ds.allowed(m, 'fill_start'))

    def test_verified_negative_after_an_accidental_click_is_not_changed_to_unknown(self):
        self.page['lines'].append('Submission blocked by validation')
        self.sheet.update(submitted=False, clicked=True, reason='驗證擋住，沒有送出',
                          confirm_url=self.page['url'], confirm_text='Submission blocked by validation')
        ok, message = self.fill()
        self.assertTrue(ok, message)
        self.assertEqual(ds.state(self.card()), 'parked')

    def test_invalid_send_result_type_still_prevents_another_fill(self):
        self.sheet['submitted'] = 'true'
        ok, message = self.fill()
        self.assertFalse(ok, message)
        self.assertEqual(ds.state(self.card()), 'unsure')
        self.assertFalse(ds.allowed(self.card(), 'fill_start'))
        self.assertFalse(ds.allowed(self.card(), 'fix_start'))

    def test_invalid_negative_result_type_still_prevents_another_fill(self):
        self.sheet['submitted'] = 'false'
        ok, message = self.fill()
        self.assertFalse(ok, message)
        self.assertEqual(ds.state(self.card()), 'unsure')
        self.assertFalse(ds.allowed(self.card(), 'fill_start'))
        self.assertFalse(ds.allowed(self.card(), 'fix_start'))

    def test_unverified_negative_after_an_accidental_click_is_unsure(self):
        self.sheet.update(submitted=False, clicked=True, reason='沒有送出')
        ok, _message = self.fill()
        self.assertFalse(ok)
        self.assertEqual(ds.state(self.card()), 'unsure')
        self.assertFalse(ds.allowed(self.card(), 'fill_start'))

    def test_a_missing_bound_workspace_is_not_compared_or_confirmed(self):
        # 門路查到綁定的工作區不見了,不拿另一張頁去比;這張要重填。
        ok, msg = self.fill()
        self.assertTrue(ok, msg)
        with patch.object(chrome_door, 'gone_pages', return_value=[U]):
            rejected = self.confirm()
        self.assertEqual(len(rejected), 1)
        self.assertEqual(ds.state(self.card()), 'gone')
        self.assertFalse([c for c in getattr(self.door, 'calls', []) if c[0] == 'read_page'][1:])  # 驗收讀過一次,確認時沒再讀

    def test_a_field_changed_after_he_confirmed_is_stopped_before_sending(self):
        self.filled_and_confirmed()
        self.page['fields'][1]['value'] = 'Japan'
        ok, msg = self.submit()
        self.assertFalse(ok)
        self.assertIn('「Nationality」從「Taiwan」變成「Japan」', msg)
        self.assertNotIn('submit', self.rounds)                         # 送出那一輪根本沒派
        m = self.card()
        self.assertEqual(ds.state(m), 'stuck')
        self.assertIn('送出前', m['apply']['issues'][0])
        self.assertEqual(m['apply']['issues'][-1], run.NEXT_STEP)

    def test_a_send_left_on_the_form_by_a_captcha_is_not_sent_not_unsure(self):
        self.filled_and_confirmed()
        self.after_submit = captcha
        self.submit_sheet = {'submitted': False, 'clicked': True, 'reason': '真人驗證擋住，沒送出',
                             'confirm_url': U + '/apply', 'confirm_text': 'Verify you are human', 'problems': []}
        ok, msg = self.submit()
        self.assertFalse(ok)
        m = self.card()
        self.assertEqual(ds.state(m), 'stuck')                          # Agent 明確判定沒送出，網址與引用由程式核實
        self.assertIn('沒送出', m['apply']['issues'][0])
        self.assertIn('真人驗證', m['apply']['issues'][0])
        self.assertFalse([it for it in self.open_reports() if '信箱' in it['msg'] + it.get('need', '')])

    def test_a_send_claim_with_a_different_confirmation_url_is_unsure(self):
        self.filled_and_confirmed()
        self.after_submit = lambda page: None                          # 頁面沒動,它卻說送出了
        ok, _msg = self.submit()
        self.assertFalse(ok)
        self.assertEqual(ds.state(self.card()), 'unsure')

    def test_a_thank_you_page_does_not_override_an_unknown_result(self):
        self.filled_and_confirmed()
        self.submit_sheet = {'submitted': None, 'clicked': True, 'reason': '不確定有沒有送出',
                             'confirm_url': THANKS['url'], 'confirm_text': 'Application submitted', 'problems': []}
        ok, msg = self.submit()
        self.assertFalse(ok, msg)
        self.assertEqual(ds.state(self.card()), 'unsure')

    def test_a_form_that_is_still_there_under_a_new_address_is_not_called_sent_by_its_thank_you_line(self):
        # 按了送出,驗證沒過、網址多了一段,表單每一格都還在,頁首寫「Thank you for your interest」:不是送出成功
        self.filled_and_confirmed()
        self.after_submit = lambda page: page.update(url=U + '/apply?step=2', lines=['Thank you for your interest'] + page['lines'])
        self.submit_sheet = {'submitted': False, 'clicked': True, 'problems': ['不確定有沒有送出']}
        ok, _msg = self.submit()
        self.assertFalse(ok)
        self.assertNotEqual(ds.state(self.card()), 'sent')

    def test_retained_form_does_not_override_a_verified_agent_result(self):
        self.filled_and_confirmed()
        self.after_submit = lambda page: page.update(url=U + '/apply?step=2', lines=['Application received'] + page['lines'])
        self.submit_sheet = dict(SENT, confirm_url=U + '/apply?step=2', confirm_text='Application received')
        ok, _msg = self.submit()
        self.assertTrue(ok)
        self.assertEqual(ds.state(self.card()), 'sent')

    def test_an_unsure_send_marked_not_sent_clears_its_reports(self):
        self.filled_and_confirmed()
        self.after_submit = lambda page: page.clear() or page.update(
            {'url': U + '/apply/processing', 'title': 'Processing', 'lines': ['Processing'], 'fields': []})
        self.submit_sheet = {'submitted': False, 'clicked': True, 'problems': ['按了送出,不知道成功沒']}
        ok, _msg = self.submit()
        self.assertFalse(ok)
        self.assertEqual(ds.state(self.card()), 'unsure')               # 頁換了、沒有確認頁的字:程式也判斷不了
        self.assertTrue([it for it in self.open_reports() if '信箱' in it['need']])
        self.assertEqual(self.press('not_sent', at=run.now()), [])       # 他查過了:確認沒送出
        self.assertEqual(self.open_reports(), [])

    def test_the_agents_own_report_is_filed_under_the_programs_source_and_cleared_by_a_good_refill(self):
        self.said_from = '(Engineer)核准送出'
        self.page['fields'][1]['value'] = ''                            # 第一輪 Nationality 沒填上:驗收不過
        ok, _msg = self.fill()
        self.assertFalse(ok)
        mine = [it for it in self.open_reports() if '本人登入' in it['msg']]
        self.assertEqual([it['from'] for it in mine], [run.REPORT_FROM])  # 照程式給的來源記,不是它自己寫的
        self.said_from = None
        self.page['fields'][1]['value'] = 'Taiwan'
        ok, msg = self.fill()                                           # 重填成功
        self.assertTrue(ok, msg)
        self.assertEqual(self.open_reports(), [])


class ClaudeFlow(CodexFlow):
    """Claude 的對話紀錄格式不同,瀏覽器門路與即時核對行為相同。"""
    RUNTIME = chrome_door.CLAUDE

    def log_lines(self):
        lines = [{'type': 'system', 'subtype': 'init', 'session_id': 'S1'}]
        lines.append({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '@@DONE@@',
                      'session_id': 'S1'})
        return ''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in lines)


if __name__ == '__main__':
    unittest.main()
