#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查回音(reply_run)的規則:照證據改狀態、30 天沒回音記沒下文、他自己決定的不動、看不懂的不改只標出來。"""
import json, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
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
                {'url': U, 'source': 'gmail', 'date': DAY, 'summary': '拒絕通知',
                 'link': 'https://mail.example/reason', 'kind': 'reject', 'reason': reason},
                {'url': other, 'source': 'gmail', 'date': DAY, 'summary': '制式拒絕通知',
                 'link': 'https://mail.example/template', 'kind': 'reject', 'reason': ''},
            ],
        }
        parsed = rr.parse_result(data, [U, other])
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

    def test_his_own_decision_is_never_overwritten(self):
        fb = board(oc='wd', oc_at={'wd': '2026-09-30'})
        rr.apply_findings(fb, {U: [{'src': 'gmail', 'date': '2026-10-01', 'subject': 'Interview', 'kind': 'interview'}]}, DAY)
        self.assertEqual(fb[U]['oc'], 'wd')

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


class Removed(unittest.TestCase):
    def test_card_removed_while_the_round_was_running_is_not_written(self):
        """查應徵進度跑到一半(一輪最多 60 分鐘)他把卡移除了:跑完不能再改它的結果、加回音、推查過日期。"""
        fb = board(rm=1)
        before = dict(fb[U])
        rr.apply_results(fb, {U: [{'src': 'Gmail', 'date': DAY, 'kind': 'interview',
                                   'link': 'https://mail.example/iv', 'snippet': 'iv'}]}, {U}, DAY)
        self.assertEqual(fb[U], before)


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
            'findings': [{'url': U, 'source': 'Gmail', 'date': DAY,
                          'summary': '面試邀請', 'link': 'https://mail.example/thread/1',
                          'kind': 'interview'}],
            'inaccessible': [{'source': '104 應徵紀錄', 'reason': '頁面讀取失敗',
                              'need': '稍後重試', 'jobs': [U]}],
        }, [U])

        rr.apply_results(fb, parsed.findings, parsed.checked, DAY)

        self.assertEqual(parsed.checked, set())
        self.assertEqual(fb[U]['replies']['at'], previous)
        self.assertEqual(rr.since_of(fb, U), previous)
        self.assertEqual(len(fb[U]['replies']['items']), 1)
        self.assertIn(f'{previous} 起', rr.prompt_for(fb, {}, [U], '/tmp/replies.json'))
        query, _ = rr.gmail_query(fb, {U: {'company': 'Acme Cloud'}}, [U])
        self.assertIn('after:2026/10/04', query)


class Ghost(unittest.TestCase):
    def test_old_check_does_not_ghost_a_card_without_a_successful_check_this_round(self):
        fb = board(replies={'at': '2026-09-22', 'items': []})

        self.assertEqual(rr.apply_ghost(fb, DAY), [])
        self.assertNotIn('oc', fb[U])

    def test_thirty_days_without_any_reply_is_ghosted(self):
        fb = board()
        self.assertEqual(rr.apply_ghost(fb, '2026-10-21', checked={U}), [])  # 29 天
        self.assertEqual(rr.apply_ghost(fb, '2026-10-22', checked={U}), [U]) # 30 天
        self.assertEqual(fb[U]['oc'], 'ghost')

    def test_any_reply_even_a_confirmation_stops_the_ghost_clock(self):
        fb = board(replies={'items': [{'kind': 'confirm'}]})
        self.assertEqual(rr.apply_ghost(fb, DAY), [])

    def test_after_he_undoes_a_ghost_it_is_not_ghosted_again(self):
        fb = board(ghost_no=1)                                         # 看板「復原」會留這個
        self.assertEqual(rr.apply_ghost(fb, DAY, checked={U}), [])

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

    def test_a_confirmation_never_undoes_a_ghost_he_set_himself(self):
        fb = board(oc='ghost', oc_at={'ghost': '2026-10-05'})      # 他自己按的沒下文(沒有 oc_auto)
        rr.apply_findings(fb, {U: [{'src': 'Gmail', 'date': '2026-10-10', 'kind': 'confirm',
                                    'link': 'https://mail.example/still', 'snippet': 'still reviewing'}]}, '2026-10-10')
        self.assertEqual(fb[U]['oc'], 'ghost')

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
        self.assertIn('source_type', prompt)

    def test_claude_only_hands_every_source_to_the_browser_agent(self):
        """只裝 Claude Code(#225):不碰 Codex 外掛,信箱和平台頁都交給 Claude 在它的 Chrome 裡讀。"""
        from unittest.mock import patch
        import config as cf
        import agent_chrome
        jobs, fb = {U: {'target': 'Security Engineer · Acme'}}, {U: {'app': 'sent', 'sent_at': DAY}}
        runs = []

        def fake_run(prompt, log, home, **kw):
            runs.append((prompt, kw))
            return rr.ar.AgentResult('failed', reason='test_stop')

        settings = dict(cf.C, agent={'agents': [{'id': 'cc', 'runtime': 'claude-code', 'browser': True}]})
        with patch.object(cf, 'C', settings), \
             patch.object(rr, 'load', return_value=(jobs, fb)), \
             patch.object(rr, 'waiting', return_value=[U]), \
             patch.object(rr, 'mailbox', return_value=('https://mail.google.com/mail/u/0/', True)), \
             patch.object(agent_chrome, 'ensure', side_effect=AssertionError('不該碰 Codex 外掛')), \
             patch.object(agent_chrome, 'wait_claude', return_value=(True, 'ok')), \
             patch.object(agent_chrome, 'read_pages', side_effect=AssertionError('不該碰 Codex 外掛')), \
             patch.object(agent_chrome, 'close_if_idle', side_effect=AssertionError('不該碰 Codex 外掛')), \
             patch.object(rr.agent_report, 'report'), \
             patch.object(rr.ar, 'run', side_effect=fake_run), \
             patch('sys.argv', ['reply_run.py', '--board', '/tmp/board.html']):
            rr.main()
        self.assertEqual(len(runs), 1)
        prompt, kw = runs[0]
        self.assertTrue(kw['browser_required'])
        self.assertFalse(kw['web'])
        self.assertIsNone(kw['agent_id'])
        self.assertIn('只裝 Claude Code', prompt)

    def test_findings_and_104_ids_are_validated_without_reading_sources(self):
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
        parsed = rr.parse_result(raw, [U, other], source_types={'email:thread-001': 'email'})
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
        other = 'https://jobs.example/2'
        raw = {'checked': [U, other], 'findings': [
            {'url': U, 'source_ref': 'email:thread-001', 'source_type': 'application_record',
             'source': 'Gmail', 'date': DAY, 'summary': 'Application received',
             'link': 'https://mail.google.com/thread/1', 'kind': 'confirm'},
            {'url': other, 'source_ref': 'invented', 'source_type': 'email',
             'source': 'Gmail', 'date': DAY, 'summary': 'Application received',
             'link': 'https://mail.google.com/thread/2', 'kind': 'confirm'},
        ]}
        parsed = rr.parse_result(raw, [U, other], source_types={'email:thread-001': 'email'})
        self.assertEqual(parsed.findings[U][0]['source_type'], 'email')
        self.assertTrue(parsed.findings[U][0]['_source_verified'])
        self.assertEqual(parsed.findings[other][0]['source_type'], 'fallback')
        self.assertFalse(parsed.findings[other][0]['_source_verified'])

        fb = {U: {'app': 'sent', 'ev': '只有送出頁證據'},
              other: {'app': 'sent', 'ev': '只有送出頁證據'}}
        rr.apply_findings(fb, parsed.findings, DAY)
        self.assertNotIn('ev', fb[U])
        self.assertIn('ev', fb[other])

    def test_specific_gmail_fallback_message_clears_marker_after_url_validation(self):
        fallback_sources = [{
            'source_ref': 'email:search', 'source_type': 'email', 'source': 'Gmail',
            'reason': '程式讀不到 Gmail 搜尋頁', 'need': '用 agent 專用 Chrome 補查', 'jobs': [U],
        }]
        source_types = rr._source_type_index({}, fallback_sources)
        raw = {'checked': [U], 'findings': [{
            'url': U, 'source_ref': 'email:search', 'source_type': 'application_record',
            'source': 'Gmail', 'date': DAY, 'summary': 'Application received',
            'link': 'https://mail.google.com/mail/u/0/#all/thread-001', 'kind': 'confirm',
        }]}
        parsed = rr.parse_result(raw, [U], source_types=source_types)
        finding = parsed.findings[U][0]
        self.assertEqual(finding['source_type'], 'email')
        self.assertEqual(finding['source_ref'], 'email:thread-001')
        self.assertTrue(finding['_source_verified'])

        fb = {U: {'app': 'sent', 'ev': '只有送出頁證據'}}
        rr.apply_findings(fb, parsed.findings, DAY)
        self.assertNotIn('ev', fb[U])

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
                   {'url': U, 'source': 'Gmail', 'date': DAY, 'summary': '面試邀請',
                    'link': 'https://mail.example/iv', 'kind': 'interview'},
                   {'url': other, 'source': 'Gmail', 'date': '10/03', 'summary': '拒絕',
                    'link': 'https://mail.example/rej', 'kind': 'reject'}],
               'job_ids': [{'platform': '104', 'id': 'good1', 'applied_at': '2026-10-01'},
                           {'platform': '104', 'id': 'bad1', 'applied_at': '10/03'}],
               'inaccessible': [{'source': '104', 'reason': '要登入', 'jobs': [third]}]}   # 缺 need
        parsed = rr.parse_result(raw, [U, other, third])
        self.assertEqual(parsed.findings[U][0]['kind'], 'interview')
        self.assertNotIn(other, parsed.checked)
        self.assertNotIn(third, parsed.checked)
        self.assertIn(U, parsed.checked)
        self.assertEqual([x['id'] for x in parsed.job_ids], ['good1'])
        self.assertEqual(len(parsed.dropped), 3)

    def test_a_finding_that_is_not_an_object_leaves_no_card_counted_as_checked(self):
        """看不出是哪張卡的壞資料:這輪哪張都不算查完(不推論沒下文),但不丟掉其他卡查到的回音。"""
        raw = {'checked': [U], 'findings': ['not an object', {
            'url': U, 'source': 'Gmail', 'date': DAY, 'summary': '面試邀請',
            'link': 'https://mail.example/iv', 'kind': 'interview'}]}
        parsed = rr.parse_result(raw, [U])
        self.assertEqual(parsed.checked, set())
        self.assertEqual(parsed.findings[U][0]['kind'], 'interview')
        self.assertEqual(len(parsed.dropped), 1)

    def test_findings_older_than_each_cards_last_check_are_ignored(self):
        other = 'https://jobs.example/2'
        raw = {'checked': [U, other], 'findings': [
            {'url': U, 'source': 'Gmail', 'date': '2026-10-04', 'summary': 'Old mail',
             'link': 'https://mail.example/old', 'kind': 'reject'},
            {'url': other, 'source': 'Gmail', 'date': '2026-10-10', 'summary': 'New mail',
             'link': 'https://mail.example/new', 'kind': 'interview'},
        ]}
        parsed = rr.parse_result(raw, [U, other],
                                 since_dates={U: '2026-10-05', other: '2026-10-09'})
        self.assertEqual(parsed.findings[U], [])
        self.assertEqual(parsed.findings[other][0]['date'], '2026-10-10')


class MainRun(unittest.TestCase):
    """reply_run.main 整輪跑一次:不開 Chrome、不派 agent、不寫真的看板,agent 交件用假的。"""
    IV = {'url': U, 'source': 'Gmail', 'date': DAY, 'summary': '面試邀請',
          'link': 'https://mail.example/iv', 'kind': 'interview'}

    def run_main(self, delivery=None, *, chrome_up=(True, ''), close_error=None, fb=None, unavailable=(),
                 argv=(), program_reads=True, log_lines=(), reread=None):
        import json, tempfile, shutil, agent_chrome
        from unittest.mock import patch
        d = tempfile.mkdtemp(prefix='reply-main-')
        self.addCleanup(shutil.rmtree, d, True)
        fb = fb if fb is not None else {U: {'app': 'sent', 'sent_at': '2026-09-01'}}
        jobs = {u: {'id': u, 'target': 'Engineer · Acme', 'company': 'Acme'} for u in fb if u.startswith('http')}
        self.statuses, self.reports, self.writes = [], [], []

        def fake_run(prompt, log, home, **kw):
            self.prompt = prompt
            with open(log, 'w', encoding='utf-8') as f:
                f.write(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in log_lines))
            if delivery is not None:
                with open(os.path.join(d, 'replies.json'), 'w', encoding='utf-8') as f:
                    json.dump(delivery, f)
            return rr.ar.AgentResult('completed', 0)

        def fake_set_fb(fn, live=None, by=''):
            fn(fb)
            self.writes.append(by)

        with patch.object(rr, 'SP', d), patch.object(rr, 'load', return_value=(jobs, fb)), \
             patch.object(rr, '_program_can_read', return_value=program_reads), \
             patch.object(rr, '_copy_only_agent_id', return_value='cx'), \
             patch.object(rr, '_read_pages', side_effect=lambda urls, *a, **k: {u: (reread or {}).get(u, {}) for u in urls}), \
             patch.object(agent_chrome, 'wait_claude', return_value=chrome_up), \
             patch.object(rr, 'collect_sources', return_value=(
                 {'gmail': {'threads': []}, 'application_records': []}, list(unavailable))), \
             patch.object(agent_chrome, 'ensure', return_value=chrome_up), \
             patch.object(agent_chrome, 'close_if_idle', side_effect=close_error), \
             patch.object(rr.jobrun, 'write', side_effect=lambda _p, data: self.statuses.append(data)), \
             patch.object(rr.agent_report, 'report', side_effect=lambda *a, **k: self.reports.append((a, k))), \
             patch.object(rr.bd, 'set_fb', side_effect=fake_set_fb), \
             patch.object(rr.ar, 'run', side_effect=fake_run), \
             patch.object(rr.ar, 'lean', return_value=[]), \
             patch.object(rr.ar, 'apply_overrides', return_value=[]), \
             patch('sys.argv', ['reply_run.py', '--board', os.path.join(d, 'board.html'), *argv]):
            code = rr.main()
        return code, fb

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

    def test_chrome_not_up_tells_him_the_button_that_exists_now(self):
        """agent 的 Chrome 沒起來:回報要叫他按現在真的有的「🔌 連接 Codex」,不是已經廢掉的「agent 設定檔」(ADR 0003)。"""
        code, _fb = self.run_main(chrome_up=(False, '還沒連接 agent 的 Chrome'))
        self.assertEqual(code, 1)
        need = self.reports[-1][1]['need']
        self.assertNotIn('設定檔', need)
        self.assertIn('連接 Codex', need)


SEARCH = 'https://mail.google.com/mail/u/0/#search/' + 'after%3A2026%2F01%2F01%20(%22Acme%22)'
THREAD = 'https://mail.google.com/mail/u/0/?ui=2&view=pt&search=all&th=abc123def'


def gmail_pages(threads=('abc123def',), bodies=('Thanks for applying',)):
    """程式自己讀到的 Gmail 搜尋頁和列印檢視(完整的一頁,跟 agent_chrome.read_pages 回的一樣)。"""
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


def claude_log(pages):
    """Claude 那一輪的 stream-json 紀錄:它在每一頁跑程式給的那支唯讀函式(先拿長度、再分段)。"""
    import apply_tab
    lines, k = [], 0
    for page in pages:
        whole = json.dumps(rr._digest(page), ensure_ascii=False)
        calls = [(rr.VERIFY_LEN_JS, str(len(whole)))]
        calls += [(apply_tab.chunk_js(i, rr._VERIFY_WHOLE), f'{i}:' + whole[i * apply_tab.CHUNK:(i + 1) * apply_tab.CHUNK])
                  for i in range(-(-len(whole) // apply_tab.CHUNK))]
        for code, out in calls:
            k += 1
            lines.append({'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': f't{k}', 'name': 'mcp__claude-in-chrome__javascript_tool',
                 'input': {'action': 'javascript_exec', 'text': code}}]}})
            lines.append({'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': f't{k}', 'content': [{'type': 'text', 'text': out}]}]}})
    return lines


class VerifiedFallback(unittest.TestCase):
    """#289 決定 2:程式讀不到、交給 agent 補查的來源,agent 說「查過了」不夠;程式核實過那個來源真的讀完了,
    那幾張卡才推論沒下文。Codex:程式跑完自己再讀一次;只用 Claude:Claude 在每一頁跑程式給的唯讀函式,
    程式從紀錄拿工具的回傳(跟代投核對頁面同一套),不採信它的轉述。"""
    run_main = MainRun.run_main
    OLD = {U: {'app': 'sent', 'sent_at': '2026-01-02'}}
    GMAIL = {'source_ref': 'email:search', 'source_type': 'email', 'url': SEARCH, 'source': 'Gmail',
             'reason': '只裝 Claude Code:程式讀不了', 'need': '用 agent 專用 Chrome 補查 Gmail', 'jobs': [U]}

    def fb(self):
        return {U: dict(self.OLD[U])}

    def test_claude_saying_checked_without_a_verified_read_is_not_ghosted(self):
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[self.GMAIL],
                                 program_reads=False)
        self.assertEqual(code, 0)
        self.assertNotEqual(fb[U].get('oc'), 'ghost')
        self.assertEqual(fb[U]['replies']['at'], rr.today())          # 查過日期照樣往前推,下一輪搜尋範圍才會縮
        self.assertIn('沒核實', self.statuses[-1]['msg'])

    def test_claude_read_verified_from_its_own_tool_results_is_ghosted(self):
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[self.GMAIL],
                                 program_reads=False, log_lines=claude_log(gmail_pages().values()))
        self.assertEqual(code, 0)
        self.assertEqual(fb[U].get('oc'), 'ghost')
        self.assertEqual(self.statuses[-1]['phase'], 'done')
        self.assertIn(rr.VERIFY_LEN_JS, self.prompt)                  # prompt 交代它跑哪一支

    def test_claude_skipping_one_mail_on_the_search_page_is_not_enough(self):
        pages = gmail_pages(threads=('abc123def', 'zzz999yyy'))
        pages.pop('https://mail.google.com/mail/u/0/?ui=2&view=pt&search=all&th=zzz999yyy')
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[self.GMAIL],
                                 program_reads=False, log_lines=claude_log(pages.values()))
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

    def test_more_results_than_one_page_is_not_ghosted_but_the_checked_date_moves_on(self):
        """搜尋結果一頁放不下(1–50 of 234):核實不了,不推論沒下文;但查過日期要往前推,
        不然下一輪又從送出日搜起、又放不下,永遠卡住。"""
        pages = gmail_pages()
        pages[SEARCH]['text'] = 'Inbox\n1–1 of 234\nmail abc123def'
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[self.GMAIL],
                                 program_reads=False, log_lines=claude_log(pages.values()))
        self.assertNotEqual(fb[U].get('oc'), 'ghost')
        self.assertEqual(fb[U]['replies']['at'], rr.today())

    def test_a_source_without_a_page_the_program_can_check_never_infers_ghost(self):
        """不是 Gmail 的信箱、公司名沒有能搜的字:程式核實不了,只能照回音改,不推論沒下文。"""
        other = dict(self.GMAIL, url='https://outlook.office.com/mail/', reason='設定的信箱不是 Gmail')
        code, fb = self.run_main({'checked': [U], 'findings': []}, fb=self.fb(), unavailable=[other],
                                 program_reads=False, log_lines=claude_log(gmail_pages().values()))
        self.assertNotEqual(fb[U].get('oc'), 'ghost')


class SourceCapture(unittest.TestCase):
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
        import profile_sync as ps
        from urllib.parse import quote
        from unittest.mock import patch
        jobs = {U: {'id': U, 'company': 'Acme'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        query, _ = rr.gmail_query(fb, jobs, [U])
        search_url = rr.GMAIL + '#search/' + quote(query, safe='')
        page = {
            'url': search_url, 'title': 'Gmail', 'text': '1-50 of 100',
            'anchors': [
                {'href': rr.GMAIL + '#all/thread-001', 'text': 'Acme application 1'},
                {'href': rr.GMAIL + '#all/thread-002', 'text': 'Acme application 2'},
            ],
        }

        def reader(urls):
            return {url: (page if url == search_url else {
                'url': url, 'title': 'Gmail', 'text': 'FULL THREAD TEXT',
                'emailThreadPrintView': True, 'emailMessageCount': 1,
                'emailBodies': ['FULL THREAD TEXT'],
            }) for url in urls}

        with patch.object(ps, 'application_record_pages', return_value=[]):
            sources, missing = rr.collect_sources(fb, jobs, [U], reader=reader)

        self.assertEqual(sources['gmail']['threads'], [])
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['source_ref'], 'email:search')
        self.assertEqual(missing[0]['jobs'], [U])
        self.assertIn('完整', missing[0]['reason'])

    def test_gmail_next_page_prevents_a_complete_search_claim(self):
        import profile_sync as ps
        from urllib.parse import quote
        from unittest.mock import patch
        jobs = {U: {'id': U, 'company': 'Acme'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        query, _ = rr.gmail_query(fb, jobs, [U])
        search_url = rr.GMAIL + '#search/' + quote(query, safe='')
        page = {
            'url': search_url, 'title': 'Gmail', 'text': '1-2 of 3',
            'anchors': [
                {'href': rr.GMAIL + '#all/thread-001', 'text': 'Acme application 1'},
                {'href': rr.GMAIL + '#all/thread-002', 'text': 'Acme application 2'},
            ],
        }
        with patch.object(ps, 'application_record_pages', return_value=[]):
            sources, missing = rr.collect_sources(
                fb, jobs, [U], reader=lambda urls: {
                    url: page if url == search_url else {
                    'url': url, 'title': 'Gmail', 'text': 'FULL THREAD TEXT',
                    'emailThreadPrintView': True, 'emailMessageCount': 1,
                    'emailBodies': ['FULL THREAD TEXT'],
                } for url in urls})

        self.assertEqual(sources['gmail']['threads'], [])
        self.assertEqual([item['source_ref'] for item in missing], ['email:search'])
        self.assertEqual(missing[0]['jobs'], [U])
        self.assertIn('尚未讀完', missing[0]['reason'])

    def test_gmail_search_without_a_result_count_is_not_treated_as_empty(self):
        import profile_sync as ps
        from urllib.parse import quote
        from unittest.mock import patch
        jobs = {U: {'id': U, 'company': 'Acme'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        query, _ = rr.gmail_query(fb, jobs, [U])
        search_url = rr.GMAIL + '#search/' + quote(query, safe='')
        page = {'url': search_url, 'title': 'Gmail', 'text': 'Gmail loading shell ' * 20,
                'anchors': []}

        with patch.object(ps, 'application_record_pages', return_value=[]):
            _sources, missing = rr.collect_sources(
                fb, jobs, [U], reader=lambda urls: {url: page for url in urls})

        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['source_ref'], 'email:search')
        self.assertIn('確認', missing[0]['reason'])

    def test_gmail_empty_search_with_zero_result_count_is_complete(self):
        import profile_sync as ps
        from urllib.parse import quote
        from unittest.mock import patch
        jobs = {U: {'id': U, 'company': 'Acme'}}
        fb = {U: {'app': 'sent', 'sent_at': '2026-10-01'}}
        query, _ = rr.gmail_query(fb, jobs, [U])
        search_url = rr.GMAIL + '#search/' + quote(query, safe='')
        page = {'url': search_url, 'title': 'Gmail', 'text': '0-0 of 0', 'anchors': []}

        with patch.object(ps, 'application_record_pages', return_value=[]):
            _sources, missing = rr.collect_sources(
                fb, jobs, [U], reader=lambda urls: {url: page for url in urls})

        self.assertEqual(missing, [])

    def test_gmail_readiness_requires_complete_result_page_and_loaded_thread(self):
        import agent_chrome
        from unittest import mock
        partial = {'readyState': 'complete', 'text': '1-50 of 100', 'anchors': [
            {'href': rr.GMAIL + '#all/thread-001'},
        ]}
        full_page = {'readyState': 'complete', 'text': '1-1 of 1', 'anchors': [
            {'href': rr.GMAIL + '#all/thread-001'},
        ]}
        self.assertFalse(rr._gmail_search_ready(partial))
        self.assertTrue(rr._gmail_search_ready(full_page))
        self.assertTrue(rr._gmail_search_ready({
            'readyState': 'complete', 'text': '0-0 of 0', 'anchors': [],
        }))
        self.assertTrue(rr._gmail_search_ready({
            'readyState': 'complete', 'text': 'No conversations match your search', 'anchors': [],
        }))
        self.assertTrue(rr._gmail_search_ready({
            'readyState': 'complete', 'text': '1-2 封，共 2 封', 'anchors': [
                {'href': rr.GMAIL + '#all/thread-001'},
                {'href': rr.GMAIL + '#all/thread-002'},
            ],
        }))
        self.assertFalse(rr._gmail_thread_ready({'readyState': 'interactive', 'text': 'Email',
                                                 'emailThreadPrintView': True, 'emailMessageCount': 1,
                                                 'emailBodies': ['Email']}))
        self.assertFalse(rr._gmail_thread_ready({'readyState': 'complete', 'text': 'Email body'}))
        self.assertFalse(rr._gmail_thread_ready({'readyState': 'complete', 'text': 'Email shell',
                                                 'emailThreadPrintView': True, 'emailMessageCount': 0,
                                                 'emailBodies': []}))
        self.assertTrue(rr._gmail_thread_ready({'readyState': 'complete', 'text': 'Email body',
                                                'emailThreadPrintView': True, 'emailMessageCount': 1,
                                                'emailBodies': ['Email body']}))
        self.assertFalse(rr._gmail_thread_ready({'readyState': 'complete', 'text': 'Email body',
                                                 'emailThreadPrintView': True, 'emailMessageCount': 1,
                                                 'emailBodies': ['hidden body text']}))
        self.assertFalse(rr._gmail_thread_ready({'readyState': 'complete', 'text': 'Email body',
                                                 'emailThreadPrintView': True, 'emailMessageCount': 1,
                                                 'emailBodies': 'Email body'}))
        self.assertFalse(rr._gmail_thread_ready({'readyState': 'complete', 'text': 'Message one',
                                                 'emailThreadPrintView': True, 'emailMessageCount': 2,
                                                 'emailBodies': ['Message one']}))
        self.assertTrue(rr._gmail_thread_ready({'readyState': 'complete',
                                                'text': 'Message one\nMessage two',
                                                'emailThreadPrintView': True, 'emailMessageCount': 2,
                                                'emailBodies': ['Message one', 'Message two']}))
        with mock.patch.object(agent_chrome, 'read_pages', return_value={}) as read_pages:
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
        self.assertTrue(got['_source_verified'])
        self.assertTrue(got['source_ref'].startswith('email:'))
        for link in ('https://outlook.office.com.attacker.example/mail/inbox/id/AAQk', 'https://outlook.office.com/mail/'):
            with self.subTest(link=link):
                self.assertFalse(parse(link)['_source_verified'])


if __name__ == '__main__':
    unittest.main()
