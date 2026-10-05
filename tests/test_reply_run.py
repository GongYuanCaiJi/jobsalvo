#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查回音(reply_run)的規則:照證據改狀態、30 天沒回音記沒下文、他自己決定的不動、看不懂的不改只標出來。"""
import contextlib
from types import SimpleNamespace
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import reply_run as rr     # noqa: E402

U = 'https://jobs.lever.co/x/1'
DAY = '2026-10-30'


def board(**m):
    return {U: dict({'app': 'sent', 'sent_at': '2026-09-22'}, **m)}


class Findings(unittest.TestCase):
    def test_reject_email_moves_to_not_hired_and_can_be_undone(self):
        fb = board()
        rr.apply_findings(fb, {U: [{'src': 'gmail', 'date': '2026-10-01', 'subject': 'Update on your application', 'kind': 'reject'}]}, DAY)
        self.assertEqual(fb[U]['oc'], 'rej')
        self.assertEqual(fb[U]['oc_auto']['from'], '')            # 看板靠這個復原
        self.assertEqual(len(fb[U]['replies']['items']), 1)
        self.assertNotIn('reason', fb[U]['replies']['items'][0])
        self.assertEqual(fb[U]['app'], 'sent')

    def test_rejection_reason_is_kept_verbatim_and_template_reason_is_omitted(self):
        other = 'https://jobs.example/template'
        reason = '拒絕信原文：技能要求尚未符合。\n目前團隊需要有相關經驗的人選。'
        data = {
            'checked': [U, other],
            'findings': [
                {'url': U, 'source': 'gmail', 'date': DAY, 'summary': '拒絕通知', 'source_ref': 'email:reason1',
                 'link': 'https://mail.example/reason', 'kind': 'reject', 'quote': '技能要求尚未符合', 'reason': reason},
                {'url': other, 'source': 'gmail', 'date': DAY, 'summary': '制式拒絕通知', 'source_ref': 'email:tmpl1',
                 'link': 'https://mail.example/template', 'kind': 'reject', 'quote': '制式拒絕通知', 'reason': ''},
            ],
        }
        parsed = rr.parse_result(data, [U, other], source_types={'email:reason1': 'email', 'email:tmpl1': 'email'},
                                 texts={'email:reason1': reason + '\n' + DAY, 'email:tmpl1': '制式拒絕通知\n' + DAY})
        fb = {U: {'app': 'sent'}, other: {'app': 'sent'}}
        rr.apply_findings(fb, parsed.findings, DAY)
        self.assertEqual(fb[U]['replies']['items'][0]['reason'], reason)
        self.assertNotIn('reason', fb[other]['replies']['items'][0])
        self.assertEqual([fb[url]['oc'] for url in (U, other)], ['rej', 'rej'])
        self.assertEqual([fb[url]['app'] for url in (U, other)], ['sent', 'sent'])

    def test_recheck_adds_reason_to_existing_rejection_without_duplicate_or_status_change(self):
        fb = board()
        existing = {'src': 'gmail', 'date': DAY, 'subject': 'Update',
                    'link': 'https://mail.example/reason', 'kind': 'reject'}
        existing['id'] = rr._key(existing)
        fb[U].update({'oc': 'rej', 'replies': {'items': [existing]}})
        reason = '原文理由'
        rr.apply_findings(fb, {U: [dict(existing, reason=reason)]}, DAY)
        self.assertEqual(len(fb[U]['replies']['items']), 1)
        self.assertEqual(fb[U]['replies']['items'][0]['reason'], reason)
        self.assertEqual(fb[U]['oc'], 'rej')
        self.assertEqual(fb[U]['app'], 'sent')

    def test_one_agent_delivery_records_reject_interview_and_offer_separately(self):
        urls = [U, 'https://jobs.example/interview', 'https://jobs.example/offer']
        fb = {url: {'app': 'sent'} for url in urls}
        kinds = ['reject', 'interview', 'offer']
        found = {url: [{'src': 'email', 'date': '2026-10-01', 'subject': f'Update {i}',
                        'snippet': 'application update', 'link': f'https://mail.example/{i}',
                        'kind': kind}] for i, (url, kind) in enumerate(zip(urls, kinds))}
        rr.apply_findings(fb, found, DAY)
        self.assertEqual([fb[url]['oc'] for url in urls], ['rej', 'iv', 'offer'])
        for url in urls:
            self.assertEqual(len(fb[url]['replies']['items']), 1)
            self.assertEqual(fb[url]['oc_auto']['from'], '')

    def test_interview_then_offer_keeps_the_history(self):
        fb = board()
        rr.apply_findings(fb, {U: [{'src': 'gmail', 'date': '2026-10-01', 'subject': 'Interview', 'kind': 'interview'}]}, DAY)
        rr.apply_findings(fb, {U: [{'src': 'gmail', 'date': '2026-10-10', 'subject': 'Offer', 'kind': 'offer'}]}, DAY)
        self.assertEqual(fb[U]['oc'], 'offer')
        self.assertIn('iv', fb[U]['oc_at'])

    def test_something_he_must_do_is_kept_as_a_todo_without_asking_him_to_look(self):
        fb = board()
        done = rr.apply_findings(fb, {U: [{'src': 'gmail', 'date': '2026-10-01', 'subject': 'Next step', 'kind': 'interview',
                                           'todo': '簽保密協議,對方才會寄測驗'}]}, DAY)
        self.assertEqual(fb[U]['replies']['items'][0]['todo'], '簽保密協議,對方才會寄測驗')
        self.assertNotIn('look', fb[U]['replies'])
        self.assertIn('要你做', done[U])

    def test_rejected_after_interview_keeps_the_interview_date(self):
        fb = board()
        rr.apply_findings(fb, {U: [{'src': 'gmail', 'date': '2026-08-17', 'subject': 'AI 面試邀請', 'kind': 'interview'},
                                   {'src': 'gmail', 'date': '2026-09-17', 'subject': '感謝信', 'kind': 'reject'}]}, DAY)
        self.assertEqual(fb[U]['oc'], 'rej')
        self.assertEqual(fb[U]['oc_at'], {'iv': '2026-08-17', 'rej': '2026-09-17'})

    def test_next_run_only_searches_after_the_last_check(self):
        fb = board(replies={'at': '2026-10-05', 'items': []})
        self.assertIn('2026-10-05 起', rr.prompt_for(fb, {}, [U], '/tmp/x.json'))

    def test_same_mail_twice_is_recorded_once(self):
        fb = board()
        it = {'src': 'gmail', 'date': '2026-10-01', 'subject': 'Thanks for applying', 'kind': 'confirm'}
        rr.apply_findings(fb, {U: [it]}, DAY)
        self.assertEqual(rr.apply_findings(fb, {U: [it]}, DAY), {})
        self.assertEqual(len(fb[U]['replies']['items']), 1)

    def test_same_thread_same_day_confirm_and_reject_are_two_replies(self):
        """同一串信同一天先「已收到」再「很遺憾」:程式讀的是整串,兩則原文連結、日期都一樣,拒絕不能被當成重複。"""
        fb = board()
        link = 'https://mail.google.com/mail/u/0/?ui=2&view=pt&search=all&th=abcdef1'
        common = {'src': 'Gmail', 'date': DAY, 'link': link, 'source_ref': 'email:abcdef1', 'source_type': 'email'}
        rr.apply_findings(fb, {U: [dict(common, kind='confirm', snippet='Thanks for applying'),
                                   dict(common, kind='reject', snippet='Unfortunately')]}, DAY)
        self.assertEqual([x['kind'] for x in fb[U]['replies']['items']], ['confirm', 'reject'])
        self.assertEqual(fb[U]['oc'], 'rej')

    def test_undone_reply_reported_again_in_other_words_does_not_move_the_card_back(self):
        """他按「不對,復原」之後,agent 下一輪把同一封信的來源換個寫法再報一次:不能又改回「沒錄取」、也不多一則。"""
        fb = board()
        it = {'src': 'Gmail', 'date': DAY, 'kind': 'reject', 'snippet': 'x', 'source_ref': 'email:abcdef1',
              'source_type': 'email', 'link': 'https://mail.google.com/mail/u/0/?ui=2&view=pt&search=all&th=abcdef1'}
        rr.apply_findings(fb, {U: [dict(it)]}, DAY)
        f = fb[U]
        rr.set_outcome(f, f['oc_auto']['from'], DAY)                  # 看板的「不對,復原」
        del f['oc_auto']
        again = dict(it, src='Gmail 郵件', subject='Re: 應徵結果',
                     link='https://mail.google.com/mail/u/0/#all/abcdef1')
        self.assertEqual(rr.apply_findings(fb, {U: [again]}, DAY), {})
        self.assertNotIn('oc', f)
        self.assertEqual(len(f['replies']['items']), 1)

    def test_summary_names_the_status_change_not_the_evidence_note(self):
        """只有送出頁證據的卡收到拒絕信:跑完那句要講「等回音 → 沒錄取」,不是「已找到確認信」。"""
        fb = board(ev='送出頁是目前唯一證據')
        res = rr.apply_findings(fb, {U: [{'src': 'Gmail', 'date': '2026-10-20', 'kind': 'reject',
                                          'link': 'https://mail.google.com/mail/u/0/#all/abcdef1',
                                          'source_ref': 'email:abcdef1', 'source_type': 'email',
                                          '_source_verified': True}]}, DAY)
        line = rr.summary(res, {U: {'target': 'Engineer · Acme'}})
        self.assertIn('等回音 → 沒錄取', line)
        self.assertNotIn('改了狀態:Engineer · Acme 已找到', line)

    def test_confirming_email_clears_weak_submission_evidence(self):
        fb = board(ev='送出頁是目前唯一證據;待查信箱與平台應徵紀錄')
        rr.apply_findings(fb, {U: [{'src': 'gmail', 'date': DAY, 'subject': 'Application received',
                                   'kind': 'confirm', 'source_type': 'email',
                                   '_source_verified': True}]}, DAY)
        self.assertNotIn('ev', fb[U])

    def test_application_record_clears_weak_submission_evidence(self):
        fb = board(ev='已查信箱,仍未找到;目前只有送出頁證據')
        rr.apply_findings(fb, {U: [{'src': '104 應徵紀錄', 'date': DAY, 'subject': '已應徵',
                                   'kind': 'confirm', 'source_type': 'application_record',
                                   '_source_verified': True}]}, DAY)
        self.assertNotIn('ev', fb[U])

    def test_checked_sources_update_weak_evidence_with_not_found_result(self):
        fb = board(ev='送出頁是目前唯一證據;待查信箱與平台應徵紀錄')
        rr.apply_checked_evidence(fb, {U})
        self.assertIn('已查信箱', fb[U]['ev'])
        self.assertIn('未找到確認信或平台紀錄', fb[U]['ev'])


class OneLetterManyCards(unittest.TestCase):
    """同一家投了兩個缺,一封沒寫職稱的「很遺憾」信被 agent 同時掛到兩張(#289 決定 1):
    分不出是哪一張就不自動改狀態;回音照記(兩張都有,不會被記成沒下文),卡上標「可能是這封」讓他自己按。"""
    V = 'https://jobs.lever.co/x/2'

    def letter(self, **kw):
        return dict({'src': 'Gmail', 'date': DAY, 'kind': 'reject', 'source_type': 'email',
                     'source_ref': 'email:abc123', 'link': 'https://mail.google.com/mail/u/0/#all/abc123',
                     'snippet': '很遺憾這次無法進一步', '_source_verified': True}, **kw)

    def test_same_letter_on_two_cards_changes_neither(self):
        fb = dict(board(), **{self.V: {'app': 'sent', 'sent_at': '2026-09-22'}})
        res, ghosts, _ = rr.apply_results(fb, {U: [self.letter()], self.V: [self.letter()]}, {U, self.V}, DAY)
        for url, other in ((U, self.V), (self.V, U)):
            self.assertNotIn('oc', fb[url])
            self.assertNotIn('oc_auto', fb[url])
            (item,) = fb[url]['replies']['items']
            self.assertEqual(item['maybe'], [other])       # 卡上寫「同一封也對到哪一張」
        self.assertEqual(ghosts, [])
        self.assertNotIn('→', ';'.join(res.values()))
        self.assertIn('可能是同一封', rr.summary(res, {}))

    def test_interview_letter_on_two_auto_ghosted_cards_moves_neither(self):
        ghost = {'oc': 'ghost', 'oc_at': {'ghost': '2026-10-20'}, 'oc_auto': {'s': 'ghost', 'from': '', 'at': '2026-10-20', 'by': '送出 30 天沒有回音'}}
        fb = {U: dict(board()[U], **ghost), self.V: dict(board()[U], **ghost)}
        rr.apply_findings(fb, {U: [self.letter(kind='interview')], self.V: [self.letter(kind='interview')]}, DAY)
        self.assertEqual([fb[U]['oc'], fb[self.V]['oc']], ['ghost', 'ghost'])

    def test_one_letter_one_card_still_moves_it(self):
        fb = dict(board(), **{self.V: {'app': 'sent', 'sent_at': '2026-09-22'}})
        rr.apply_findings(fb, {U: [self.letter()], self.V: [self.letter(source_ref='email:zzz999', link='https://mail.google.com/mail/u/0/#all/zzz999')]}, DAY)
        self.assertEqual([fb[U]['oc'], fb[self.V]['oc']], ['rej', 'rej'])
        self.assertNotIn('maybe', fb[U]['replies']['items'][0])

    def test_the_platform_records_page_is_one_source_for_many_cards_and_is_not_ambiguous(self):
        """平台應徵紀錄頁整頁是同一個 source_ref:兩張同一天投的 104 卡各有一筆確認,不是同一封信。"""
        fb = dict(board(ev='只有送出頁'), **{self.V: {'app': 'sent', 'sent_at': '2026-09-22', 'ev': '只有送出頁'}})
        rec = dict(self.letter(kind='confirm', source_type='application_record', source_ref='application_record:104',
                               link='https://pda.104.com.tw/applyRecord'))
        rr.apply_findings(fb, {U: [dict(rec)], self.V: [dict(rec)]}, DAY)
        self.assertNotIn('ev', fb[U]); self.assertNotIn('ev', fb[self.V])
        self.assertNotIn('maybe', fb[U]['replies']['items'][0])


class CheckedDates(unittest.TestCase):
    def test_completed_check_without_echo_remembers_date_for_next_search(self):
        fb = board()
        rr.apply_results(fb, {}, {U}, DAY)

        self.assertEqual(fb[U]['replies']['at'], DAY)
        self.assertEqual(rr.since_of(fb, U), DAY)
        self.assertIn(f'{DAY} 起', rr.prompt_for(fb, {}, [U], '/tmp/replies.json'))

    def test_inaccessible_source_keeps_previous_date_even_when_another_source_found_echo(self):
        previous = '2026-10-05'
        fb = board(replies={'items': [], 'at': previous})
        parsed = rr.parse_result({
            'checked': [U],
            'findings': [{'url': U, 'source': 'Gmail', 'date': DAY, 'source_ref': 'email:thread-1',
                          'summary': '面試邀請', 'link': 'https://mail.example/thread/1',
                          'kind': 'interview'}],
            'inaccessible': [{'source': '104 應徵紀錄', 'reason': '頁面讀取失敗',
                              'need': '稍後重試', 'jobs': [U]}],
        }, [U], source_types={'email:thread-1': 'email'})

        rr.apply_results(fb, parsed.findings, parsed.checked, DAY)

        self.assertEqual(parsed.checked, set())
        self.assertEqual(fb[U]['replies']['at'], previous)
        self.assertEqual(rr.since_of(fb, U), previous)
        self.assertEqual(len(fb[U]['replies']['items']), 1)
        self.assertIn(f'{previous} 起', rr.prompt_for(fb, {}, [U], '/tmp/replies.json'))
        query, _ = rr.gmail_query(fb, {U: {'company': 'Acme Cloud'}}, [U])
        self.assertIn('after:2026/10/04', query)


class Ghost(unittest.TestCase):
    def test_thirty_days_without_any_reply_is_ghosted(self):
        fb = board()
        self.assertEqual(rr.apply_ghost(fb, '2026-10-21', checked={U}), [])  # 29 天
        self.assertEqual(rr.apply_ghost(fb, '2026-10-22', checked={U}), [U]) # 30 天
        self.assertEqual(fb[U]['oc'], 'ghost')

    def test_a_late_still_reviewing_mail_moves_an_auto_ghosted_card_back_to_waiting(self):
        """自動記成沒下文之後公司來信「還在審」:確認信也是回音,照回音改回「等回音」,可以復原。"""
        fb = board(sent_at='2026-09-01')
        rr.apply_ghost(fb, '2026-10-05', checked={U})
        res = rr.apply_findings(fb, {U: [{'src': 'Gmail', 'date': '2026-10-10', 'kind': 'confirm',
                                          'link': 'https://mail.example/still', 'snippet': 'still reviewing'}]},
                                '2026-10-10')
        self.assertNotIn('oc', fb[U])
        self.assertEqual((fb[U]['oc_auto']['s'], fb[U]['oc_auto']['from']), ('', 'ghost'))
        self.assertIn('沒下文 → 等回音', res[U])

    def test_auto_change_keeps_the_dates_from_before_so_undo_can_put_them_back(self):
        """沒下文被回音改成面試:改之前的日期存著,他按「不對,復原」時看板原封放回,不留面試日期。"""
        fb = board(sent_at='2026-09-01')
        rr.apply_ghost(fb, '2026-10-05', checked={U})
        rr.apply_findings(fb, {U: [{'src': 'Gmail', 'date': '2026-10-10', 'kind': 'interview',
                                    'link': 'https://mail.example/iv', 'snippet': 'iv'}]}, '2026-10-10')
        self.assertEqual(fb[U]['oc'], 'iv')
        self.assertEqual(fb[U]['oc_auto']['from_at'], {'ghost': '2026-10-05'})

    def test_a_late_reply_moves_a_ghosted_card_back(self):
        fb = board()
        rr.apply_ghost(fb, DAY, checked={U})
        rr.apply_findings(fb, {U: [{'src': 'gmail', 'date': '2026-11-01', 'subject': 'Interview', 'kind': 'interview'}]}, '2026-11-01')
        self.assertEqual(fb[U]['oc'], 'iv')
        self.assertNotIn('ghost', fb[U]['oc_at'])


class AgentResults(unittest.TestCase):
    def test_preview_uses_copied_source_rules_without_browser(self):
        from unittest.mock import patch
        jobs, fb = {U: {'target': 'Security Engineer'}}, {U: {'app': 'sent', 'sent_at': DAY}}
        with patch.object(rr, 'load', return_value=(jobs, fb)), \
             patch.object(rr, 'waiting', return_value=[U]), \
             patch.object(rr.ar, 'rules_for', return_value='SHARED-BROWSER-RULES\n') as rules:
            prompt = rr.preview('/tmp/board.html')
        rules.assert_called_once_with('main', board='/tmp/board.html', web=False)
        self.assertTrue(prompt.startswith('SHARED-BROWSER-RULES\n'))
        self.assertIn('複製到', prompt)
        self.assertIn('不要開瀏覽器、搜尋網路', prompt)
        self.assertNotIn('"source_type"', prompt)      # 來源是信還是平台紀錄,程式照 source_ref 自己認(#317)
        self.assertIn('"quote"', prompt)

    def test_replies_use_the_same_agent_as_every_other_job(self):
        # 查應徵進度挑哪一家要跟填表一樣(設定裡第一個用瀏覽器的那一個)。以前只看「有沒有勾了瀏覽器的 Codex」:
        # 排第一的是 Claude、後面也有一個勾了的 Codex 時,填表用 Claude、查應徵進度卻用 Codex
        from unittest.mock import patch
        import chrome_door
        import fake_door as fc
        jobs, fb = {U: {'target': 'Security Engineer · Acme'}}, {U: {'app': 'sent', 'sent_at': DAY}}
        claude, codex = fc.FakeDoor('claude-code', agent_id='cc', up=(False, '看不到')), fc.FakeDoor('codex', agent_id='cx')
        with fc.installed(claude, codex), \
             patch.object(rr, 'load', return_value=(jobs, fb)), \
             patch.object(rr, 'waiting', return_value=[U]), \
             patch.object(chrome_door, 'close_if_idle'), \
             patch.object(rr.agent_report, 'report'), \
             patch.object(rr.jobrun, 'write'), \
             patch('sys.argv', ['reply_run.py', '--board', '/tmp/board.html']):
            self.assertEqual(rr.main(), 1)
        self.assertEqual([c[0] for c in claude.calls], ['ready'])
        self.assertEqual(codex.calls, [])

    def test_findings_and_104_ids_are_validated_against_the_copied_sources(self):
        other = 'https://jobs.example/2'
        raw = {
            'checked': [U, other],
            'findings': [{'url': U, 'source': 'email', 'date': '2026-09-12',
                          'summary': '邀請面試', 'link': 'https://mail.example/thread/1',
                          'kind': 'interview', 'todo': '回覆可面談時段'}],
            'job_ids': [{'id': 'AbC123', 'applied_at': '2026-09-11', 'title': 'ignored'}],
            'inaccessible': [{'source': '104', 'reason': '需要登入', 'need': '登入後重跑',
                              'jobs': [other]}],
        }
        raw['findings'][0]['source_ref'] = 'email:thread-001'
        parsed = rr.parse_result(raw, [U, other], source_types={'email:thread-001': 'email'},
                                 texts={'application_record:104': '應徵紀錄 AbC123 資料工程師 2026-09-11 已應徵'})
        self.assertEqual(parsed.findings[U][0]['link'], 'https://mail.example/thread/1')
        self.assertEqual(parsed.findings[U][0]['kind'], 'interview')
        self.assertEqual(parsed.findings[U][0]['todo'], '回覆可面談時段')
        self.assertEqual(parsed.findings[U][0]['source_type'], 'email')
        self.assertEqual(parsed.findings[U][0]['source_ref'], 'email:thread-001')
        self.assertEqual(parsed.checked, {U})
        self.assertEqual(parsed.job_ids, [{'id': 'AbC123', 'title': 'ignored', 'applied_at': '2026-09-11'}])
        self.assertEqual(parsed.inaccessible[0]['jobs'], [other])

    def test_unknown_inaccessible_scope_prevents_ghost_inference(self):
        raw = {'checked': [U], 'findings': [], 'job_ids': [],
               'inaccessible': [{'source': 'mail', 'reason': '連線失敗', 'need': '稍後重試'}]}
        parsed = rr.parse_result(raw, [U])
        fb = {U: {'app': 'sent', 'sent_at': '2026-08-01'}}
        self.assertEqual(parsed.checked, set())
        self.assertEqual(rr.apply_ghost(fb, day='2026-10-01', checked=parsed.checked), [])
        self.assertNotIn('oc', fb[U])

    def test_source_type_is_bound_to_program_captured_source_ref(self):
        """來源種類程式照 source_ref 自己認;寫錯種類、編一個程式沒給的來源代號,那一則都不收、那張不算查完(#317)。"""
        other, third = 'https://jobs.example/2', 'https://jobs.example/3'
        raw = {'checked': [U, other, third], 'findings': [
            {'url': U, 'source_ref': 'email:thread-001',
             'source': 'Gmail', 'date': DAY, 'summary': 'Application received',
             'link': 'https://mail.google.com/thread/1', 'kind': 'confirm'},
            {'url': other, 'source_ref': 'invented',
             'source': 'Gmail', 'date': DAY, 'summary': 'Application received',
             'link': 'https://mail.google.com/thread/2', 'kind': 'confirm'},
            {'url': third, 'source_ref': 'email:thread-001', 'source_type': 'application_record',
             'source': 'Gmail', 'date': DAY, 'summary': 'Application received',
             'link': 'https://mail.google.com/thread/1', 'kind': 'confirm'},
        ]}
        parsed = rr.parse_result(raw, [U, other, third], source_types={'email:thread-001': 'email'},
                                 texts={'email:thread-001': 'Application received\n' + DAY})
        self.assertEqual(parsed.findings[U][0]['source_type'], 'email')
        self.assertTrue(parsed.findings[U][0]['_source_verified'])
        self.assertNotIn(other, parsed.findings)
        self.assertNotIn(third, parsed.findings)
        self.assertEqual(parsed.checked, {U})
        said = '\n'.join(parsed.dropped)
        self.assertIn('invented', said)
        self.assertIn('程式沒給它這個來源', said)
        self.assertIn('來源種類', said)

        fb = {U: {'app': 'sent', 'ev': '只有送出頁證據'}}
        rr.apply_findings(fb, parsed.findings, DAY)
        self.assertNotIn('ev', fb[U])

    def test_specific_gmail_fallback_url_alone_does_not_clear_marker(self):
        fallback_sources = [{
            'source_ref': 'email:search', 'source_type': 'email', 'source': 'Gmail',
            'reason': '程式讀不到 Gmail 搜尋頁', 'need': '用 agent 專用 Chrome 補查', 'jobs': [U],
        }]
        source_types = rr._source_type_index({}, fallback_sources)
        raw = {'checked': [U], 'findings': [{
            'url': U, 'source_ref': 'email:search',
            'source': 'Gmail', 'date': DAY, 'summary': 'Application received',
            'link': 'https://mail.google.com/mail/u/0/#all/thread-001', 'kind': 'confirm',
        }]}
        parsed = rr.parse_result(raw, [U], source_types=source_types)
        finding = parsed.findings[U][0]
        self.assertEqual(finding['source_type'], 'email')
        self.assertEqual(finding['source_ref'], 'email:thread-001')
        self.assertFalse(finding['_source_verified'])
        self.assertTrue(finding['unverified'])

        fb = {U: {'app': 'sent', 'ev': '只有送出頁證據'}}
        rr.apply_findings(fb, parsed.findings, DAY)
        self.assertIn('ev', fb[U])

    def test_a_reply_from_a_source_the_program_never_read_is_marked_unverified(self):
        """agent 補查、程式沒讀到全文的來源:核對不了原文,那一則標 unverified(卡上照實講「程式沒讀到原文」)。"""
        fallback_sources = [{'source_ref': 'email:search', 'source_type': 'email', 'source': 'Gmail',
                             'reason': '讀不到', 'need': '補查', 'jobs': [U]}]
        raw = {'checked': [U], 'findings': [{
            'url': U, 'source_ref': 'email:search', 'source': 'Gmail', 'date': DAY, 'summary': '面試邀請',
            'link': 'https://mail.google.com/mail/u/0/#all/thread-001', 'kind': 'interview', 'quote': '邀請面試'}]}
        parsed = rr.parse_result(raw, [U], source_types=rr._source_type_index({}, fallback_sources), texts={})
        self.assertTrue(parsed.findings[U][0]['unverified'])
        fb = board()
        rr.apply_findings(fb, parsed.findings, DAY)
        self.assertTrue(fb[U]['replies']['items'][0]['unverified'])

    def test_gmail_fallback_cannot_clear_marker_with_non_gmail_link(self):
        fallback_sources = [{
            'source_ref': 'email:search', 'source_type': 'email', 'source': 'Gmail',
            'reason': '程式讀不到 Gmail 搜尋頁', 'need': '用 agent 專用 Chrome 補查', 'jobs': [U],
        }]
        for link in (
            'https://mail.google.com.attacker.example/mail/u/0/#all/thread-001',
            'https://mail.google.com/mail/u/0/#search/thread-001',
        ):
            with self.subTest(link=link):
                raw = {'checked': [U], 'findings': [{
                    'url': U, 'source_ref': 'email:search', 'source_type': 'email',
                    'source': 'Gmail', 'date': DAY, 'summary': 'Application received',
                    'link': link, 'kind': 'confirm',
                }]}
                parsed = rr.parse_result(raw, [U], source_types=rr._source_type_index({}, fallback_sources))
                finding = parsed.findings[U][0]
                self.assertEqual(finding['source_type'], 'fallback')
                self.assertFalse(finding['_source_verified'])
                fb = {U: {'app': 'sent', 'ev': '只有送出頁證據'}}
                rr.apply_findings(fb, parsed.findings, DAY)
                self.assertIn('ev', fb[U])

    def test_one_malformed_row_is_dropped_alone_and_its_card_is_not_counted_as_checked(self):
        """交件 20 則都對、只有一筆日期寫成 10/03:只丟那一筆(那張卡這輪不算查完,不推論沒下文),其他照收。"""
        other, third = 'https://jobs.example/2', 'https://jobs.example/3'
        raw = {'checked': [U, other, third],
               'findings': [
                   {'url': U, 'source': 'Gmail', 'date': DAY, 'summary': '面試邀請', 'source_ref': 'email:iv1',
                    'link': 'https://mail.example/iv', 'kind': 'interview'},
                   {'url': other, 'source': 'Gmail', 'date': '10/03', 'summary': '拒絕', 'source_ref': 'email:rej1',
                    'link': 'https://mail.example/rej', 'kind': 'reject'}],
               'job_ids': [{'platform': '104', 'id': 'good1', 'applied_at': '2026-10-01'},
                           {'platform': '104', 'id': 'bad1', 'applied_at': '10/03'}],
               'inaccessible': [{'source': '104', 'reason': '要登入', 'jobs': [third]}]}   # 缺 need
        parsed = rr.parse_result(raw, [U, other, third], source_types={'email:iv1': 'email', 'email:rej1': 'email'},
                                 texts={'application_record:104': 'good1 2026-10-01 bad1 2026-10-03'})
        self.assertEqual(parsed.findings[U][0]['kind'], 'interview')
        self.assertNotIn(other, parsed.checked)
        self.assertNotIn(third, parsed.checked)
        self.assertIn(U, parsed.checked)
        self.assertEqual([x['id'] for x in parsed.job_ids], ['good1'])
        self.assertEqual(len(parsed.dropped), 3)

    def test_a_finding_that_is_not_an_object_leaves_no_card_counted_as_checked(self):
        """看不出是哪張卡的壞資料:這輪哪張都不算查完(不推論沒下文),但不丟掉其他卡查到的回音。"""
        raw = {'checked': [U], 'findings': ['not an object', {
            'url': U, 'source': 'Gmail', 'date': DAY, 'summary': '面試邀請', 'source_ref': 'email:iv1',
            'link': 'https://mail.example/iv', 'kind': 'interview'}]}
        parsed = rr.parse_result(raw, [U], source_types={'email:iv1': 'email'})
        self.assertEqual(parsed.checked, set())
        self.assertEqual(parsed.findings[U][0]['kind'], 'interview')
        self.assertEqual(len(parsed.dropped), 1)

    def test_findings_older_than_each_cards_last_check_are_ignored(self):
        other = 'https://jobs.example/2'
        raw = {'checked': [U, other], 'findings': [
            {'url': U, 'source': 'Gmail', 'date': '2026-10-04', 'summary': 'Old mail', 'source_ref': 'email:old1',
             'link': 'https://mail.example/old', 'kind': 'reject'},
            {'url': other, 'source': 'Gmail', 'date': '2026-10-10', 'summary': 'New mail', 'source_ref': 'email:new1',
             'link': 'https://mail.example/new', 'kind': 'interview'},
        ]}
        parsed = rr.parse_result(raw, [U, other], source_types={'email:old1': 'email', 'email:new1': 'email'},
                                 since_dates={U: '2026-10-05', other: '2026-10-09'})
        self.assertEqual(parsed.findings[U], [])
        self.assertEqual(parsed.findings[other][0]['date'], '2026-10-10')


# 程式複製的那一封信(安檢門拿它核對 agent 交回的每一則,#317)
MAIL = {'source_ref': 'email:iv0001', 'source_type': 'email', 'source_id': 'iv0001', 'url': 'https://mail.example/iv',
        'title': '面試邀請', 'text': f'Acme 人資 {DAY}\n您好,邀請您參加第一輪面試。'}


class MainRun(unittest.TestCase):
    """reply_run.main 整輪跑一次:不開 Chrome、不派 agent、不寫真的看板,agent 交件用假的。"""
    IV = {'url': U, 'source': 'Gmail', 'date': DAY, 'summary': '面試邀請', 'source_ref': 'email:iv0001',
          'link': 'https://mail.example/iv', 'kind': 'interview', 'quote': '邀請您參加第一輪面試'}


    def run_main(self, delivery=None, *, chrome_up=(True, ''), close_error=None, fb=None, unavailable=(),
                 argv=(), log_lines=(), reread=None, real_run=False, result=('completed', 0)):
        import json, tempfile, chrome_door
        import fake_door as fc
        from unittest.mock import patch
        self.door = fc.FakeDoor('codex', up=chrome_up, need='打開 ego lite')
        d = self.enterContext(tempfile.TemporaryDirectory(prefix='reply-main-'))
        fb = fb if fb is not None else {U: {'app': 'sent', 'sent_at': '2026-09-01'}}
        jobs = {u: {'id': u, 'target': 'Engineer · Acme', 'company': 'Acme'} for u in fb if u.startswith('http')}
        self.statuses, self.reports, self.writes = [], [], []

        def fake_run(prompt, log, home, **kw):
            self.prompt, self.run_kw = prompt, kw
            with open(log, 'w', encoding='utf-8') as f:
                f.write(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in log_lines))
            if delivery is not None:
                with open(os.path.join(d, 'replies.json'), 'w', encoding='utf-8') as f:
                    json.dump(delivery, f)
            return rr.ar.AgentResult(*result)

        def fake_set_fb(fn, live=None, by=''):
            fn(fb)
            self.writes.append(by)

        def launch(prompt, log, home, agent, **kw):
            fake_run(prompt, log, home)
            return SimpleNamespace(pid=4242)
        # real_run:agent_run.run 照真的跑(只換掉開行程、等行程),看它記下的證據
        dispatch = ([patch.object(rr.ar, 'launch', side_effect=launch),
                     patch.object(rr.ar, 'wait_done', return_value=[SimpleNamespace(status='completed', returncode=0, pid=4242)]),
                     patch.dict(rr.cf.C, {'agent': dict(rr.cf.C.get('agent') or {}, agents=[
                         {'id': 'cx', 'runtime': 'codex', 'model': '', 'effort': 'max', 'browser': True}])})]
                    if real_run else [patch.object(rr.ar, 'run', side_effect=fake_run)])

        with fc.installed(self.door), patch.object(rr, 'SP', d), patch.object(rr, 'load', return_value=(jobs, fb)), \
             patch.object(rr, '_copy_only_agent_id', return_value='cx'), \
             patch.object(rr, '_read_pages', side_effect=lambda urls, *a, **k: {u: (reread or {}).get(u, {}) for u in urls}), \
             patch.object(rr, 'collect_sources', return_value=(
                 {'gmail': {'threads': [MAIL]}, 'application_records': []}, list(unavailable))), \
             patch.object(chrome_door, 'close_if_idle', side_effect=close_error), \
             patch.object(rr.jobrun, 'write', side_effect=lambda _p, data: self.statuses.append(data)), \
             patch.object(rr.agent_report, 'report', side_effect=lambda *a, **k: self.reports.append((a, k))), \
             patch.object(rr.bd, 'set_fb', side_effect=fake_set_fb), \
             contextlib.ExitStack() as stack, \
             patch.object(rr.ar, 'lean', return_value=[]), \
             patch.object(rr.ar, 'apply_overrides', return_value=[]), \
             patch('sys.argv', ['reply_run.py', '--board', os.path.join(d, 'board.html'), *argv]):
            for p in dispatch:
                stack.enter_context(p)
            code = rr.main()
        self.board_path = os.path.join(d, 'board.html')
        return code, fb

    def test_partial_findings_are_not_read_when_agent_fails(self):
        code, fb = self.run_main({'checked': [U], 'findings': [self.IV]}, result=('failed', 9, 123))
        self.assertEqual(code, 1)
        self.assertEqual(self.run_kw['board'], self.board_path)
        self.assertEqual(self.writes, [])
        self.assertNotIn('oc', fb[U])
        self.assertEqual((self.statuses[-1]['phase'], self.statuses[-1]['done']), ('failed', 0))
        self.assertTrue(any('結束碼 9' in a[1] for a, _k in self.reports))

    def test_the_round_leaves_its_evidence_in_every_checked_cards_folder(self):
        """#315:查應徵進度這一輪查了哪幾張,每一張的證據夾都有:程式讀的來源、指示、動作紀錄、交件單,同一種放法。"""
        other = 'https://jobs.example/2'
        fb = {U: {'app': 'sent', 'sent_at': '2026-09-01'}, other: {'app': 'sent', 'sent_at': '2026-09-01'}}
        code, fb = self.run_main({'checked': [U, other], 'findings': [self.IV]}, fb=fb, real_run=True)
        self.assertEqual(code, 0)
        for u in (U, other):
            rnd = _env.evidence_rounds(u, self.board_path)[-1]                  # 這一輪(同一個暫存夾裡還有前面測試的)
            self.assertIn('-reply-', os.path.basename(rnd))
            kinds = [e['kind'] for e in _env.evidence_events(rnd)]
            self.assertEqual([k for k in kinds if k in ('page', 'instruction', 'agent_log', 'handoff', 'check')],
                             ['page', 'instruction', 'agent_log', 'handoff', 'check'])   # 安檢門的比對結果(#317)

    def test_a_reply_whose_quote_is_not_in_the_mail_does_not_move_the_card(self):
        """安檢門(#317):agent 說是拒絕信,抄的那一句程式複製的信裡沒有:不改狀態、那張這一輪不算查完、照實寫進紀錄。"""
        lie = dict(self.IV, kind='reject', quote='很遺憾通知您')
        code, fb = self.run_main({'checked': [U], 'findings': [lie]})
        self.assertEqual(code, 0)
        self.assertNotIn('oc', fb[U])
        self.assertNotIn('replies', fb[U])
        self.assertEqual(self.statuses[-1]['phase'], 'incomplete')

    def test_an_auto_change_carries_the_line_it_was_judged_from(self):
        """信算哪一種是 agent 判斷:照舊自動改,回音上附它抄的那一句(程式核對過在信裡),卡上給他看、可以復原。"""
        code, fb = self.run_main({'checked': [U], 'findings': [self.IV]})
        self.assertEqual(code, 0)
        self.assertEqual(fb[U]['oc'], 'iv')
        (item,) = fb[U]['replies']['items']
        self.assertEqual(item['quote'], '邀請您參加第一輪面試')
        self.assertNotIn('unverified', item)
        self.assertEqual(fb[U]['oc_auto']['by'], item['id'])

    def test_chrome_cleanup_error_after_the_agent_finished_keeps_its_results(self):
        """agent 已經交件了,收尾(關 agent 的 Chrome)出錯:照樣收它交的回音,不能整輪丟掉還說 agent 沒完成。"""
        code, fb = self.run_main({'checked': [U], 'findings': [self.IV]}, close_error=RuntimeError('boom'))
        self.assertEqual(code, 0)
        self.assertEqual(fb[U]['oc'], 'iv')
        self.assertEqual(self.statuses[-1]['phase'], 'done')
        self.assertIn('收尾', self.statuses[-1]['msg'])
        self.assertFalse([a for a, _k in self.reports if 'agent 沒完成' in a[1]])

    def test_one_malformed_row_does_not_throw_away_the_whole_delivery(self):
        other = 'https://jobs.example/2'
        fb = {U: {'app': 'sent', 'sent_at': '2026-09-01'}, other: {'app': 'sent', 'sent_at': '2026-09-01'}}
        bad = dict(self.IV, url=other, date='10/03')
        code, fb = self.run_main({'checked': [U, other], 'findings': [self.IV, bad]}, fb=fb)
        self.assertEqual(code, 0)
        self.assertEqual(fb[U]['oc'], 'iv')
        self.assertNotIn('replies', fb[other])                      # 那張這輪不算查完,查過日期不往前推
        self.assertEqual(self.statuses[-1]['phase'], 'incomplete')
        self.assertIn('格式不對', self.statuses[-1]['msg'])

    def test_one_unreachable_source_is_one_report_and_the_older_ones_are_settled(self):
        """Gmail 進不去、等回音的卡 3 張:回報只要一則(不是一張一則);前幾輪留下、這輪已經重查過的舊回報收掉。"""
        urls = [U, 'https://jobs.example/2', 'https://jobs.example/3']
        fb = {u: {'app': 'sent', 'sent_at': '2026-09-01'} for u in urls}
        fb['__inbox__'] = [{'id': 'old', 'at': '2026-01-01T00:00:00', 'from': '查回音', 'n': 1,
                            'msg': '104 應徵紀錄 無法進入:要登入', 'job': urls[1]}]
        code, fb = self.run_main({'checked': [], 'findings': [], 'inaccessible': [
            {'source': 'Gmail', 'reason': '要登入', 'need': '在 agent 的 Chrome 登入 Gmail', 'jobs': urls}]}, fb=fb)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.reports), 1)
        self.assertTrue(fb['__inbox__'][0].get('done'))

    def test_a_one_card_round_only_settles_that_cards_reports(self):
        other = 'https://jobs.example/2'
        fb = {U: {'app': 'sent', 'sent_at': '2026-09-01'}, other: {'app': 'sent', 'sent_at': '2026-09-01'}}
        fb['__inbox__'] = [{'id': f'old{i}', 'at': '2026-01-01T00:00:00', 'from': '查回音', 'n': 1,
                            'msg': '104 應徵紀錄 無法進入:要登入', 'job': u} for i, u in enumerate((U, other))]
        self.run_main({'checked': [U], 'findings': []}, fb=fb, argv=['--url', U])
        self.assertEqual([bool(it.get('done')) for it in fb['__inbox__']], [True, False])

    def test_unusable_delivery_points_to_the_log_button_on_the_page_that_has_it(self):
        """交件整份不能用:回報叫他去看紀錄,指的分頁名要是現在的「📮 已投出」(那一列沒跑成時有「看紀錄」)。"""
        code, _fb = self.run_main({'findings': []})                    # 缺 checked
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses[-1]['phase'], 'failed')
        need = self.reports[-1][1]['need']
        self.assertIn('📮 已投出', need)
        self.assertIn('看紀錄', need)

    def test_browser_not_up_tells_him_what_to_do(self):
        """agent 的瀏覽器沒準備好:回報照門路講的要做什麼。"""
        code, _fb = self.run_main(chrome_up=(False, 'ego 指令連不上'))
        self.assertEqual(code, 1)
        self.assertIn('打開 ego lite', self.reports[-1][1]['need'])


SEARCH = 'https://mail.google.com/mail/u/0/#search/' + 'after%3A2026%2F01%2F01%20(%22Acme%22)'
THREAD = 'https://mail.google.com/mail/u/0/?ui=2&view=pt&search=all&th=abc123def'


def gmail_pages(threads=('abc123def',), bodies=('Thanks for applying',)):
    """程式自己讀到的 Gmail 搜尋頁和列印檢視(完整的一頁,跟門路的 read_pages 回的一樣)。"""
    n = len(threads)
    search = {'url': SEARCH, 'title': 'Search results - Gmail', 'readyState': 'complete',
              'text': f'Inbox\n1–{n} of {n}\n' + '\n'.join(f'mail {t}' for t in threads) if n else 'No messages matched your search',
              'anchors': [{'href': f'https://mail.google.com/mail/u/0/#all/{t}', 'text': t} for t in threads]}
    pages = {SEARCH: search}
    for t in threads:
        url = f'https://mail.google.com/mail/u/0/?ui=2&view=pt&search=all&th={t}'
        pages[url] = {'url': url, 'title': 'Gmail', 'readyState': 'complete', 'text': '\n'.join(bodies),
                      'emailThreadPrintView': True, 'emailMessageCount': len(bodies), 'emailBodies': list(bodies)}
    return pages


class VerifiedFallback(unittest.TestCase):
    """#289 決定 2:程式讀不到、交給 agent 補查的來源,agent 說「查過了」不夠;程式跑完自己再讀一次,
    核實過那個來源真的讀完了,那幾張卡才推論沒下文。"""
    run_main = MainRun.run_main
    OLD = {U: {'app': 'sent', 'sent_at': '2026-01-02'}}
    GMAIL = {'source_ref': 'email:search', 'source_type': 'email', 'url': SEARCH, 'source': 'Gmail',
             'reason': '程式讀的時候還沒登入', 'need': '用 agent 的瀏覽器補查 Gmail', 'jobs': [U]}

    def fb(self):
        return {U: dict(self.OLD[U])}

    def test_agent_saying_checked_without_a_verified_read_is_not_ghosted(self):
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[self.GMAIL])
        self.assertEqual(code, 0)
        self.assertNotEqual(fb[U].get('oc'), 'ghost')
        self.assertNotIn('at', fb[U]['replies'])  # 未核實不能縮小下一輪搜尋範圍
        self.assertIn('沒核實', self.statuses[-1]['msg'])

    def test_one_mail_missing_from_the_programs_reread_is_not_enough(self):
        pages = gmail_pages(threads=('abc123def', 'zzz999yyy'))
        pages.pop('https://mail.google.com/mail/u/0/?ui=2&view=pt&search=all&th=zzz999yyy')
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[self.GMAIL],
                                 reread=pages)
        self.assertNotEqual(fb[U].get('oc'), 'ghost')

    def test_codex_fallback_is_verified_by_the_program_reading_it_again(self):
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[self.GMAIL],
                                 reread=gmail_pages())
        self.assertEqual(fb[U].get('oc'), 'ghost')

    def test_codex_fallback_still_unreadable_by_the_program_is_not_ghosted(self):
        login = {SEARCH: {'url': 'https://accounts.google.com/signin', 'title': 'Sign in', 'readyState': 'complete',
                          'text': 'Sign in'}}
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[self.GMAIL], reread=login)
        self.assertNotEqual(fb[U].get('oc'), 'ghost')
        self.assertIn('沒核實', self.statuses[-1]['msg'])

    def test_partial_search_keeps_the_checked_date_to_avoid_missing_older_replies(self):
        """搜尋結果一頁放不下，不能縮小下一輪搜尋範圍、漏掉未讀的舊信。"""
        pages = gmail_pages()
        pages[SEARCH]['text'] = 'Inbox\n1–1 of 234\nmail abc123def'
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[self.GMAIL],
                                 reread=pages)
        self.assertNotEqual(fb[U].get('oc'), 'ghost')
        self.assertNotIn('at', fb[U]['replies'])

    def test_a_source_without_a_page_the_program_can_check_never_infers_ghost(self):
        """不是 Gmail 的信箱、公司名沒有能搜的字:程式核實不了,只能照回音改,不推論沒下文。"""
        other = dict(self.GMAIL, url='https://outlook.office.com/mail/', reason='設定的信箱不是 Gmail')
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[other],
                                 reread=gmail_pages())
        self.assertNotEqual(fb[U].get('oc'), 'ghost')


class SourceCapture(unittest.TestCase):
    THREAD = {'title': 'Gmail', 'text': 'FULL THREAD TEXT', 'emailThreadPrintView': True,
              'emailMessageCount': 1, 'emailBodies': ['FULL THREAD TEXT']}

    def collect(self, search_page):
        """一張 Acme 的卡:搜尋網址回 search_page,其他網址(點進去的信)都回完整的信。回 (sources, missing)。"""
        import profile_sync as ps
        from urllib.parse import quote
        from unittest.mock import patch
        jobs = {U: {'id': U, 'company': 'Acme'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        query, _ = rr.gmail_query(fb, jobs, [U])
        search_url = rr.GMAIL + '#search/' + quote(query, safe='')
        page = dict(search_page, url=search_url, title='Gmail')
        with patch.object(ps, 'application_record_pages', return_value=[]):
            return rr.collect_sources(fb, jobs, [U], reader=lambda urls: {
                u: page if u == search_url else dict(self.THREAD, url=u) for u in urls})

    def test_company_search_includes_all_gmail_categories(self):
        jobs = {U: {'id': U, 'company': 'Acme Systems'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}

        query, _ = rr.gmail_query(fb, jobs, [U])

        for category in ('promotions', 'social', 'forums'):
            self.assertNotIn(f'-category:{category}', query)
        self.assertIn('Acme Systems', query)

    def test_program_copies_full_mail_and_application_record_text(self):
        import profile_sync as ps
        from urllib.parse import quote
        from unittest.mock import patch

        jobs = {U: {'id': U, 'target': 'Security Engineer · Acme Systems', 'company': 'Acme Systems'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        query, search_companies = rr.gmail_query(fb, jobs, [U])
        self.assertIn('after:2026/09/30', query)
        self.assertIn('Acme Systems', query)
        self.assertEqual(search_companies, ['Acme Systems'])
        search_url = rr.GMAIL + '#search/' + quote(query, safe='')
        email_url = f'{rr.GMAIL}?ui=2&view=pt&search=all&th=thread-001'
        record_url = 'https://jobs.104.com.tw/applications'
        texts = {
            search_url: {'url': search_url, 'title': 'Gmail', 'text': '1-1 of 1',
                         'anchors': [{'href': rr.GMAIL + '#all/thread-001', 'text': 'Acme application'}]},
            email_url: {'url': email_url, 'title': 'Gmail', 'text': 'FULL THREAD TEXT ' + 'details ' * 500,
                        'emailThreadPrintView': True, 'emailMessageCount': 1,
                        'emailBodies': ['FULL THREAD TEXT ' + 'details ' * 500]},
            record_url: {'url': record_url, 'title': '104 應徵紀錄', 'text': 'Applied: Acme Systems, job id A104XYZ'},
        }
        with patch.object(ps, 'application_record_pages', return_value=[
                {'platform': 'jobs.104.com.tw', 'url': record_url}]), \
             patch.object(ps, 'profile_key', side_effect=lambda value: 'jobs.104.com.tw'):
            sources, missing = rr.collect_sources(fb, jobs, [U], reader=lambda urls: {u: texts[u] for u in urls})
        self.assertEqual(missing, [])
        self.assertEqual(len(sources['gmail']['threads']), 1)
        self.assertIn('details ' * 500, sources['gmail']['threads'][0]['text'])
        self.assertEqual(sources['gmail']['threads'][0]['source_ref'], 'email:thread-001')
        self.assertIn('A104XYZ', sources['application_records'][0]['text'])
        self.assertEqual(sources['application_records'][0]['source_ref'], 'application_record:jobs.104.com.tw')

    def test_company_search_covers_the_short_name_and_his_aliases(self):
        """「甲科技股份有限公司」的信常只署名「甲科技」;他在設定寫的公司別名也要搜。搜不到就會被當成沒回音、記成沒下文。"""
        from unittest.mock import patch
        import config as cf
        settings = dict(cf.C, board=dict(cf.C.get('board') or {}, company_alias={'jia tech': '甲科技'}))
        with patch.object(cf, 'C', settings):
            terms = rr._gmail_terms('甲科技股份有限公司')
        self.assertIn('甲科技', terms)
        self.assertIn('jia tech', terms)

    def test_a_common_first_word_is_not_searched_alone(self):
        """「The Foo Desk」多搜一個 "The" 幾乎每封信都中,結果一頁放不下,整個 Gmail 搜尋被判沒讀完。"""
        self.assertNotIn('The', rr._gmail_terms('The Foo Desk'))
        self.assertIn('Acme', rr._gmail_terms('Acme Systems Inc.'))

    def test_company_search_literals_cannot_add_gmail_operators(self):
        jobs = {U: {'id': U, 'company': 'Acme") OR from:other@example.test'}}
        query, _ = rr.gmail_query({U: {'sent_at': '2026-10-01'}}, jobs, [U])
        self.assertNotIn('from:', query)
        self.assertNotIn('@', query)
        self.assertEqual(query.count('('), 1)
        self.assertEqual(query.count(')'), 1)

    def test_missing_company_never_runs_an_unbounded_gmail_search(self):
        import profile_sync as ps
        from unittest.mock import patch
        jobs = {U: {'id': U, 'target': 'Security Engineer'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        query, companies = rr.gmail_query(fb, jobs, [U])
        self.assertEqual((query, companies), ('', []))
        with patch.object(ps, 'application_record_pages', return_value=[]):
            sources, missing = rr.collect_sources(
                fb, jobs, [U], reader=lambda _urls: self.fail('must not search every Gmail message'))
        self.assertEqual(sources['gmail']['threads'], [])
        self.assertEqual(missing[0]['jobs'], [U])
        self.assertIn('公司名稱', missing[0]['reason'])

    def test_company_without_searchable_terms_is_marked_uncovered_in_a_batch(self):
        import profile_sync as ps
        from urllib.parse import quote
        from unittest.mock import patch
        other = 'https://jobs.lever.co/x/2'
        jobs = {
            U: {'id': U, 'company': 'Acme'},
            other: {'id': other, 'company': '!!!'},
        }
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'},
              other: {'app': 'sent', 'sent_at': '2026-10-02'}}
        query, _ = rr.gmail_query(fb, jobs, [U, other])
        search_url = rr.GMAIL + '#search/' + quote(query, safe='')
        with patch.object(ps, 'application_record_pages', return_value=[]):
            sources, missing = rr.collect_sources(
                fb, jobs, [U, other],
                reader=lambda targets: {search_url: {
                    'url': search_url, 'title': 'Gmail', 'text': '0-0 of 0', 'anchors': [],
                }},
            )
        self.assertEqual(sources['gmail']['threads'], [])
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['jobs'], [other])
        self.assertIn('沒有可安全用於 Gmail 的搜尋詞', missing[0]['reason'])

    def test_partial_gmail_search_results_are_reported_inaccessible(self):
        sources, missing = self.collect({'text': '1-50 of 100', 'anchors': [
            {'href': rr.GMAIL + '#all/thread-001', 'text': 'Acme application 1'},
            {'href': rr.GMAIL + '#all/thread-002', 'text': 'Acme application 2'}]})
        self.assertEqual(sources['gmail']['threads'], [])
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['source_ref'], 'email:search')
        self.assertEqual(missing[0]['jobs'], [U])
        self.assertIn('完整', missing[0]['reason'])

    def test_gmail_next_page_prevents_a_complete_search_claim(self):
        sources, missing = self.collect({'text': '1-2 of 3', 'anchors': [
            {'href': rr.GMAIL + '#all/thread-001', 'text': 'Acme application 1'},
            {'href': rr.GMAIL + '#all/thread-002', 'text': 'Acme application 2'}]})
        self.assertEqual(sources['gmail']['threads'], [])
        self.assertEqual([item['source_ref'] for item in missing], ['email:search'])
        self.assertEqual(missing[0]['jobs'], [U])
        self.assertIn('尚未讀完', missing[0]['reason'])

    def test_gmail_search_without_a_result_count_is_not_treated_as_empty(self):
        _sources, missing = self.collect({'text': 'Gmail loading shell ' * 20, 'anchors': []})
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['source_ref'], 'email:search')
        self.assertIn('確認', missing[0]['reason'])

    def test_gmail_empty_search_with_zero_result_count_is_complete(self):
        self.assertEqual(self.collect({'text': '0-0 of 0', 'anchors': []})[1], [])

    def test_gmail_readiness_requires_complete_result_page_and_loaded_thread(self):
        import chrome_door
        from unittest import mock
        one = [{'href': rr.GMAIL + '#all/thread-001'}]
        for page, ready in (({'text': '1-50 of 100', 'anchors': one}, False),           # 只列了一部分
                            ({'text': '1-1 of 1', 'anchors': one}, True),
                            ({'text': '0-0 of 0', 'anchors': []}, True),
                            ({'text': 'No conversations match your search', 'anchors': []}, True),
                            ({'text': '會話群組\n找不到與你的搜尋條件相符的郵件。建議你在寄件者…', 'anchors': []}, True),   # 繁中介面
                            ({'text': '1-2 封，共 2 封', 'anchors': one + [{'href': rr.GMAIL + '#all/thread-002'}]}, True)):
            with self.subTest(search=page['text']):
                self.assertEqual(rr._gmail_search_ready(dict(page, readyState='complete')), ready)

        def thread(text, count, bodies, state='complete'):
            return {'readyState': state, 'text': text, 'emailThreadPrintView': True,
                    'emailMessageCount': count, 'emailBodies': bodies}
        for page, ready in ((thread('Email', 1, ['Email'], state='interactive'), False),   # 還沒載完
                            ({'readyState': 'complete', 'text': 'Email body'}, False),     # 不是列印檢視
                            (thread('Email shell', 0, []), False),                         # 一封都沒有
                            (thread('Email body', 1, ['Email body']), True),
                            (thread('Email body', 1, ['hidden body text']), False),        # 內文不在頁上
                            (thread('Email body', 1, 'Email body'), False),                # 內文格式不對
                            (thread('Message one', 2, ['Message one']), False),            # 少一封
                            (thread('Message one\nMessage two', 2, ['Message one', 'Message two']), True)):
            with self.subTest(thread=page):
                self.assertEqual(rr._gmail_thread_ready(page), ready)
        with mock.patch.object(chrome_door.EgoDoor, 'read_pages', return_value={}) as read_pages:
            rr._read_pages(['https://mail.google.com/mail/u/0/'], ready=rr._gmail_thread_ready,
                           settle=2)
        read_pages.assert_called_once_with(['https://mail.google.com/mail/u/0/'], None,
                                           ready=rr._gmail_thread_ready, settle=2)

    def test_partial_gmail_thread_shell_is_reported_inaccessible(self):
        import profile_sync as ps
        from urllib.parse import quote
        from unittest.mock import patch
        jobs = {U: {'id': U, 'company': 'Acme'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        query, _ = rr.gmail_query(fb, jobs, [U])
        search_url = rr.GMAIL + '#search/' + quote(query, safe='')
        email_url = f'{rr.GMAIL}?ui=2&view=pt&search=all&th=thread-001'
        pages = {
            search_url: {'url': search_url, 'title': 'Gmail', 'text': '1-1 of 1',
                         'anchors': [{'href': rr.GMAIL + '#all/thread-001'}]},
            email_url: {'url': email_url, 'title': 'Gmail', 'readyState': 'complete',
                        'text': 'Subject: Acme application\nMessage is still loading'},
        }
        with patch.object(ps, 'application_record_pages', return_value=[]):
            sources, missing = rr.collect_sources(
                fb, jobs, [U], reader=lambda urls: {url: pages[url] for url in urls})
        self.assertEqual(sources['gmail']['threads'], [])
        self.assertEqual([item['source_ref'] for item in missing], ['email:thread-001'])
        self.assertIn('無法確認', missing[0]['reason'])

    def test_platform_record_redirect_to_another_host_is_never_copied(self):
        import profile_sync as ps
        import urllib.parse
        from unittest.mock import patch
        jobs = {U: {'id': U, 'company': 'Acme'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        record_url = 'https://www.104.com.tw/applications'
        query, _ = rr.gmail_query(fb, jobs, [U])
        search_url = rr.GMAIL + '#search/' + urllib.parse.quote(query, safe='')
        pages = {
            search_url: {'url': search_url, 'title': 'Gmail', 'text': '0-0 of 0', 'anchors': []},
            record_url: {'url': 'https://attacker.example/applications', 'title': 'Record', 'text': 'private page'},
        }
        with patch.object(ps, 'application_record_pages', return_value=[
                {'platform': '104', 'url': record_url}]), \
             patch.object(ps, 'profile_key', side_effect=lambda value: '104' if '104.com.tw' in value or value == U else 'jobs.lever.co'):
            sources, missing = rr.collect_sources(
                fb, jobs, [U], reader=lambda targets: {target: pages[target] for target in targets},
            )
        self.assertEqual(sources['application_records'], [])
        self.assertEqual(missing[0]['source_type'], 'application_record')
        self.assertIn('跳到其他網站', missing[0]['reason'])

    def test_unreadable_source_is_reported_for_agent_fallback(self):
        import profile_sync as ps
        jobs = {U: {'id': U, 'target': 'Engineer · Acme', 'company': 'Acme'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        from unittest.mock import patch
        with patch.object(ps, 'application_record_pages', return_value=[]), \
             patch.object(rr, '_read_pages', side_effect=RuntimeError('Chrome disconnected')):
            sources, missing = rr.collect_sources(fb, jobs, [U])
        self.assertEqual(sources['gmail']['threads'], [])
        self.assertEqual(missing[0]['source'], 'Gmail')
        self.assertEqual(missing[0]['jobs'], [U])
        self.assertIn('agent 專用 Chrome', missing[0]['need'])



class Mailbox(unittest.TestCase):
    """信箱在設定 replies.mail_url:Gmail 任一個帳號程式自己讀,其他信箱交給 agent 補查。"""
    OUTLOOK = ('https://outlook.office.com/mail/', False)

    def test_gmail_account_and_other_mailboxes(self):
        self.assertEqual(rr.mailbox('https://mail.google.com/mail/u/0/'), ('https://mail.google.com/mail/u/0/', True))
        self.assertEqual(rr.mailbox('https://mail.google.com/mail/u/1/#inbox'), ('https://mail.google.com/mail/u/1/', True))
        self.assertEqual(rr.mailbox('https://outlook.office.com/mail/'), self.OUTLOOK)

    def test_other_mailbox_goes_to_agent_without_reading_gmail(self):
        from unittest.mock import patch
        jobs = {U: {'id': U, 'target': 'Engineer · Acme Systems', 'company': 'Acme Systems'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        read = []
        with patch.object(rr, 'mailbox', return_value=self.OUTLOOK):
            sources, missing = rr.collect_sources(fb, jobs, [U], reader=lambda urls: read.extend(urls) or {})
        self.assertFalse([u for u in read if 'mail.google.com' in u])
        self.assertEqual(sources['gmail']['threads'], [])
        self.assertEqual(missing[0]['source_ref'], 'email:search')
        self.assertEqual(missing[0]['url'], 'https://outlook.office.com/mail/')
        self.assertEqual(missing[0]['jobs'], [U])

    def test_agent_link_into_the_same_mailbox_counts_as_mail(self):
        from unittest.mock import patch
        fallback = [{'source_ref': 'email:search', 'source_type': 'email', 'source': '信箱',
                     'reason': '不是 Gmail', 'need': '用 agent 專用 Chrome 補查', 'jobs': [U]}]
        types = rr._source_type_index({}, fallback)

        def parse(link):
            raw = {'checked': [U], 'findings': [{
                'url': U, 'source_ref': 'email:search', 'source_type': 'email', 'source': 'Outlook',
                'date': DAY, 'summary': 'Application received', 'link': link, 'kind': 'confirm'}]}
            with patch.object(rr, 'mailbox', return_value=self.OUTLOOK):
                return rr.parse_result(raw, [U], source_types=types).findings[U][0]
        got = parse('https://outlook.office.com/mail/inbox/id/AAQkAGI2')
        self.assertEqual(got['source_type'], 'email')
        self.assertFalse(got['_source_verified'])  # 正確信箱連結仍不是已捕獲的信件原文
        self.assertTrue(got['source_ref'].startswith('email:'))
        for link in ('https://outlook.office.com.attacker.example/mail/inbox/id/AAQk', 'https://outlook.office.com/mail/'):
            with self.subTest(link=link):
                self.assertFalse(parse(link)['_source_verified'])


if __name__ == '__main__':
    unittest.main()
