#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""程式查得到、做得到的不交給他(#389):
送出結果不明由查應徵進度去查、回報附他要去的那一頁、他在回報按「處理好了」就有 agent 接著重填。"""
import datetime, os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import agent_report             # noqa: E402
import autopilot as ap          # noqa: E402
import delivery_state as ds     # noqa: E402
import reply_run as rr          # noqa: E402

U = 'https://jobs.lever.co/x/1'
PAGE = U + '/apply'
NOW = datetime.datetime(2026, 1, 5, 10, 0)
AFTER = {U: ['abc']}                    # 照按送出那一秒搜到的對話
CFG = {'auto_prep': True, 'auto_advance': True, 'auto_fill': True, 'replies_at': ''}


def unsure(at='2026-01-05T08:00:00'):
    """agent 按了送出、沒看到成功頁(跳出人機驗證),頁已經不在。"""
    return {'app': 'ship', 'ds': 'unsure',
            'apply': {'stage': 'submit', 'at': at, 'tab_id': '', 'tab_url': PAGE,
                      'submit_fail': {'at': at, 'problems': ['按下送出後出現人機驗證'], 'clicked': True}}}


def letter(**kw):
    return dict({'src': 'Gmail', 'date': '2026-01-05', 'snippet': '我們收到你的申請', 'link': 'https://mail.google.com/mail/u/0/#all/abc',
                 'kind': 'confirm', 'source_ref': 'email:abc', 'source_type': 'email', '_source_verified': True,
                 'quote': 'Thank you for applying'}, **kw)


def plan(fb, now=NOW):
    jobs = [{'id': k} for k in fb if not k.startswith('__')]
    return ap.plan({'jobs': jobs, 'status': {'schema_version': 2, 'checked_links': True, 'issues': []}}, fb,
                   running={}, now=now, cfg=CFG, real=True)


class UnsureIsCheckedByTheProgram(unittest.TestCase):
    def test_unsure_cards_are_part_of_the_reply_check_from_the_day_of_the_send(self):
        fb = {U: unsure(), 'https://s/1': {'app': 'sent', 'sent_at': '2026-01-01'}}
        self.assertEqual(sorted(rr.to_check(fb)), sorted([U, 'https://s/1']))
        self.assertEqual(rr.since_of(fb, U), '2026-01-05')

    def test_a_verified_confirmation_letter_means_it_was_sent(self):
        fb = {U: unsure()}
        agent_report.apply_report(fb, agent_report.FROM_APPLY, '送出沒確認成功:人機驗證', job=U)
        res, _, _ = rr.apply_results(fb, {U: [letter()]}, {U}, '2026-01-05', now=NOW, after=AFTER)
        self.assertEqual(ds.state(fb[U]), 'sent')
        self.assertEqual(fb[U]['sent_at'], '2026-01-05')
        self.assertEqual(fb[U]['apply']['sent']['link'], letter()['link'])     # 查到的那一則是送出證據
        self.assertEqual(ds.problems(fb[U]), [])
        self.assertEqual(agent_report.open_items(fb), [])                       # 之前那則送出沒確認成功收掉
        self.assertIn(('unsure', rr.UNSURE_WORDS['sent']), res[U])

    def test_nothing_found_after_a_full_check_means_not_sent_and_refill(self):
        fb = {U: unsure()}
        res, _, _ = rr.apply_results(fb, {U: []}, {U}, '2026-01-05', now=NOW)
        self.assertEqual(ds.state(fb[U]), 'gone')                                # 頁不在了:要重填
        self.assertIn(rr.NOT_SENT, fb[U]['apply']['issues'])
        self.assertEqual(ds.problems(fb[U]), [])
        self.assertIn(('unsure', rr.UNSURE_WORDS['not_sent']), res[U])

    def test_too_soon_after_the_send_or_not_fully_checked_changes_nothing(self):
        fb = {U: unsure(at='2026-01-05T09:30:00')}
        rr.apply_results(fb, {U: []}, {U}, '2026-01-05', now=NOW)               # 送出才半小時:確認信可能還沒到
        self.assertEqual(ds.state(fb[U]), 'unsure')
        fb = {U: unsure()}
        rr.apply_results(fb, {}, set(), '2026-01-05', now=NOW)                   # 來源沒查完
        self.assertEqual(ds.state(fb[U]), 'unsure')

    def test_one_confirmation_letter_listed_under_two_cards_proves_neither(self):
        V = 'https://jobs.lever.co/x/2'
        fb = {U: unsure(), V: unsure()}
        rr.apply_results(fb, {U: [letter()], V: [letter()]}, {U, V}, '2026-01-05', now=NOW)
        self.assertEqual((ds.state(fb[U]), ds.state(fb[V])), ('unsure', 'unsure'))   # 分不出是哪一張的申請
        self.assertEqual(len(agent_report.todo(fb)), 2)

    def test_the_status_line_names_what_happened_to_each_unsure_card(self):
        fb = {U: unsure()}
        res, _, _ = rr.apply_results(fb, {U: []}, {U}, '2026-01-05', now=NOW)
        line = rr.summary(res, {U: {'id': U, 'target': '風控專員 · 甲公司'}})
        self.assertIn('改成要重填', line)
        self.assertNotIn('有新回音', line)

    def test_one_letter_judged_as_two_kinds_for_two_cards_proves_neither(self):
        V = 'https://jobs.lever.co/x/2'
        fb = {U: unsure(), V: unsure()}
        rr.apply_results(fb, {U: [letter()], V: [letter(kind='interview')]}, {U, V}, '2026-01-05', now=NOW)
        self.assertEqual((ds.state(fb[U]), ds.state(fb[V])), ('unsure', 'unsure'))

    def test_a_letter_already_kept_on_another_card_or_an_earlier_try_proves_nothing(self):
        V = 'https://jobs.lever.co/x/2'
        fb = {U: unsure(), V: {'app': 'sent', 'ds': 'sent', 'sent_by': 'agent', 'sent_at': '2026-01-04',
                               'replies': {'items': [{'source_ref': 'email:abc', 'kind': 'confirm'}]}}}
        rr.apply_results(fb, {U: [letter()]}, {U}, '2026-01-05', now=NOW, after=AFTER)   # 只查這一張,那封之前記在別張
        self.assertEqual(ds.state(fb[U]), 'unsure')
        fb = {U: dict(unsure(), tries=[{'apply.sent': {'source_ref': 'email:abc'}}])}
        rr.apply_results(fb, {U: [letter()]}, {U}, '2026-01-05', now=NOW, after=AFTER)   # 上一次投遞的確認信
        self.assertEqual(ds.state(fb[U]), 'unsure')

    def test_an_old_reply_kept_only_as_a_link_is_the_same_letter(self):
        V = 'https://jobs.lever.co/x/2'
        link = 'https://mail.google.com/mail/u/0/#all/1a0ec55be680a479'
        fb = {U: unsure(), V: {'app': 'sent', 'ds': 'sent', 'sent_by': 'agent', 'sent_at': '2026-01-04',
                               'replies': {'items': [{'link': link, 'kind': 'confirm'}]}}}
        rr.apply_results(fb, {U: [letter(source_ref='email:1a0ec55be680a479', link=link)]}, {U}, '2026-01-05', now=NOW,
                         after={U: ['1a0ec55be680a479']})
        self.assertEqual(ds.state(fb[U]), 'unsure')

    def test_a_resubmitted_card_counts_only_the_letter_after_this_press(self):
        # 1/5 07:00 送過一次、1/5 08:00 又按了送出,兩封確認信同一天:照秒數搜到的(after)才是這一次的
        retry = lambda: {U: dict(unsure(at='2026-01-05T08:00:00'), tries=[{'apply.submit_fail': {'at': '2026-01-05T07:00:00'}}])}
        fb = retry()
        rr.apply_results(fb, {U: [letter(source_ref='email:thread-old', link='')]}, {U}, '2026-01-05', now=NOW, after={U: ['thread-new']})
        self.assertEqual(ds.state(fb[U]), 'unsure')                             # 上一次的信:不算,回報給他
        self.assertTrue(agent_report.todo(fb))
        fb = retry()
        rr.apply_results(fb, {U: [letter(source_ref='email:thread-new', link='')]}, {U}, '2026-01-05', now=NOW, after={U: ['thread-new']})
        self.assertEqual(ds.state(fb[U]), 'sent')                               # 這一次之後才有的信:算

    def test_the_program_searches_from_the_second_of_the_press(self):
        fb = {U: unsure(at='2026-01-05T08:00:00')}
        asked = []

        def reader(urls):
            asked.extend(urls)
            return {u: {'url': u, 'title': 'Gmail', 'readyState': 'complete', 'text': '1-1 of 1', 'emailThreads': ['thread-new']}
                    for u in urls}
        with mock.patch.object(rr, 'mailbox', return_value=(rr.GMAIL, True)):
            sources, _ = rr.collect_sources(fb, {U: {'id': U, 'company': '甲公司'}}, [U], reader=reader)
        sec = int(datetime.datetime(2026, 1, 5, 8, 0).timestamp())
        self.assertTrue(any(f'after%3A{sec}%20' in u for u in asked), asked)
        self.assertEqual(sources['gmail']['after'], {U: ['thread-new']})

    def test_an_unrelated_date_in_the_cards_history_does_not_block_its_proof(self):
        fb = {U: dict(unsure(), history=[{'event': 'answers_changed', 'at': '2026-01-05T07:00:00'}])}
        rr.apply_results(fb, {U: [letter()]}, {U}, '2026-01-05', now=NOW, after=AFTER)
        self.assertEqual(ds.state(fb[U]), 'sent')

    def test_records_read_by_the_agent_and_verified_count_as_read(self):
        u = 'https://www.104.com.tw/job/abc'
        fb = {u: unsure()}
        rr.apply_results(fb, {u: []}, {u}, '2026-01-05', now=NOW, accounts={'104'}, records_read={'104'})
        self.assertEqual(ds.state(fb[u]), 'gone')

    def test_not_sent_with_an_old_file_on_the_open_page_says_it_will_refill(self):
        card = unsure()
        card['apply'].update(tab_id='t1', stale='履歷換過了')
        fb = {U: card}
        res, _, _ = rr.apply_results(fb, {U: []}, {U}, '2026-01-05', now=NOW)
        self.assertEqual(ds.state(fb[U]), 'stale')
        self.assertIn(('unsure', rr.UNSURE_WORDS['not_sent_stale']), res[U])

    def test_a_platform_he_has_an_account_on_must_have_its_records_read(self):
        u = 'https://www.104.com.tw/job/abc'
        fb = {u: unsure()}
        res, _, _ = rr.apply_results(fb, {u: []}, {u}, '2026-01-05', now=NOW, accounts={'104'})
        self.assertEqual(ds.state(fb[u]), 'unsure')                             # 只查了信箱:還不能說沒送出
        self.assertIn(('unsure', rr.UNSURE_WORDS['unread']), res[u])
        rr.apply_results(fb, {u: []}, {u}, '2026-01-05', now=NOW, accounts={'104'}, records_read={'104'})
        self.assertEqual(ds.state(fb[u]), 'gone')

    def test_not_sent_with_the_page_still_open_says_so(self):
        card = unsure()
        card['apply']['tab_id'] = 't1'
        fb = {U: card}
        res, _, _ = rr.apply_results(fb, {U: []}, {U}, '2026-01-05', now=NOW)
        self.assertEqual(ds.state(fb[U]), 'parked')
        self.assertIn(('unsure', rr.UNSURE_WORDS['not_sent_page']), res[U])

    def test_a_send_time_with_a_timezone_is_compared_not_crashed(self):
        fb = {U: unsure(at='2026-01-05T08:00:00+00:00')}
        rr.apply_results(fb, {U: []}, {U}, '2026-01-05', now=datetime.datetime(2026, 1, 6, 10, 0))
        self.assertEqual(ds.state(fb[U]), 'gone')

    def test_sent_after_files_changed_keeps_the_record_but_drops_the_mark(self):
        card = unsure()
        card['apply']['stale'] = '履歷換過了'
        fb = {U: card}
        rr.apply_results(fb, {U: [letter()]}, {U}, '2026-01-05', now=NOW, after=AFTER)
        self.assertEqual(ds.state(fb[U]), 'sent')
        self.assertEqual(ds.problems(fb[U]), [])

    def test_evidence_the_program_could_not_verify_is_reported_with_its_link(self):
        fb = {U: unsure()}
        rr.apply_results(fb, {U: [letter(unverified=True, _source_verified=False)]}, {U}, '2026-01-05', now=NOW)
        self.assertEqual(ds.state(fb[U]), 'unsure')
        (it,) = agent_report.todo(fb)
        self.assertEqual((it['job'], it['url']), (U, letter()['link']))

    def test_a_failed_daily_check_waiting_to_retry_takes_the_unsure_card_along(self):
        fb = {'__auto__': {'since': '2026-01-01T00:00:00', 'skip': [], 'tried': [], 'seen': {},
                           'replies_day': '2026-01-05'}, U: unsure()}
        jobs = [{'id': U}]
        p = ap.plan({'jobs': jobs, 'status': {}}, fb, running={}, now=NOW,
                    cfg=CFG, real=True, replies_last={'phase': 'failed', 'finished_at': NOW.timestamp() - 600})
        self.assertIsNone(p['unsure'])

    def test_daily_retries_used_up_do_not_block_the_unsure_check(self):
        fb = {'__auto__': {'since': '2026-01-01T00:00:00', 'skip': [], 'tried': [], 'seen': {},
                           'replies_day': '2026-01-05', 'replies_retry': {'day': '2026-01-05', 'n': ap.REPLY_RETRY_MAX}},
              U: unsure()}
        p = ap.plan({'jobs': [{'id': U}], 'status': {}}, fb, running={}, now=NOW,
                    cfg=CFG, real=True, replies_last={'phase': 'failed', 'finished_at': NOW.timestamp() - 600})
        self.assertEqual(p['unsure'], U)

    def test_autopilot_checks_an_unsure_card_once_an_hour_after_the_send(self):
        fb = {'__auto__': {'since': '2026-01-01T00:00:00', 'skip': [U], 'tried': [], 'seen': {}}, U: unsure()}
        p = plan(fb)
        self.assertEqual(p['unsure'], U)                                        # 開自動流程前的舊卡也查(只是查)
        self.assertIsNone(plan(fb, now=datetime.datetime(2026, 1, 5, 8, 30))['unsure'])
        fb['__auto__']['tried'] = p['tried']
        self.assertIsNone(plan(fb)['unsure'])                                   # 同一次送出只排一次


class ReportsCarryWhereToGo(unittest.TestCase):
    def test_a_card_report_links_the_page_the_agent_stopped_on(self):
        fb = {U: {'app': 'ship', 'apply': {'tab_url': PAGE}}}
        self.assertEqual(agent_report.apply_report(fb, '代投', '要設密碼', need='登入', job=U)['url'], PAGE)
        fb = {U: {'app': 'ship'}}
        self.assertEqual(agent_report.apply_report(fb, '代投', '要設密碼 2', job=U)['url'], U)
        self.assertEqual(agent_report.apply_report(fb, '代投', '要登入', job=U, url='https://a/login')['url'],
                         'https://a/login')


class DoneHandsTheCardBack(unittest.TestCase):
    def card(self):
        return {'app': 'ship', 'ds': 'nopage', 'apply': {'stage': 'fill', 'at': '2026-01-04T08:00:00',
                                                         'issues': ['申請表要設密碼才能繼續']}}

    def board(self, done):
        fb = {'__auto__': {'since': '2026-01-01T00:00:00', 'skip': [U], 'tried': [], 'seen': {}}, U: self.card()}
        it = agent_report.apply_report(fb, '代投', '申請表要設密碼才能繼續', need='登入', job=U, now='2026-01-04T08:00:00')
        if done:
            it['done'] = done
        return fb, it

    def test_pressing_done_queues_one_refill_even_for_an_old_card(self):
        fb, it = self.board('2026-01-05')
        it['done_at'] = '2026-01-05T09:00:00'
        p = plan(fb)
        self.assertEqual(p['fill'], U)
        fb['__auto__']['tried'] = p['tried']
        self.assertIsNone(plan(fb)['fill'])                                     # 同一則只接一次,不會一直重填

    def test_not_pressed_or_closed_by_the_program_does_nothing(self):
        fb, it = self.board(None)
        self.assertIsNone(plan(fb)['fill'])
        it['done'], it['res'] = '2026-01-05', '程式自動收掉'
        self.assertIsNone(plan(fb)['fill'])

    def test_same_day_done_before_the_last_fill_does_not_count(self):
        fb, it = self.board('2026-01-04')
        it['done_at'] = '2026-01-04T07:00:00'                                   # 最後一次填表是 08:00
        self.assertIsNone(plan(fb)['fill'])
        it['done_at'] = '2026-01-04T09:00:00'
        self.assertEqual(plan(fb)['fill'], U)

    def test_an_old_report_with_only_a_date_on_the_same_day_does_not_count(self):
        fb, it = self.board('2026-01-04')                                       # 舊資料:只有日期,跟最後一次填表同一天
        self.assertIsNone(plan(fb)['fill'])

    def test_a_url_that_cannot_be_opened_falls_back_to_the_card(self):
        fb = {U: {'app': 'ship', 'apply': {'tab_url': 'about:blank'}}}
        self.assertEqual(agent_report.apply_report(fb, '代投', '要登入', job=U, url='javascript:alert(1)')['url'], U)

    def test_done_before_the_last_fill_does_not_count(self):
        fb, it = self.board('2026-01-03')
        it['done_at'] = '2026-01-03T09:00:00'
        self.assertIsNone(plan(fb)['fill'])


if __name__ == '__main__':
    unittest.main()
