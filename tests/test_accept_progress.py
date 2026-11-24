"""查應徵進度的驗收:從 progress.py 入口加假瀏覽器邊界跑 acceptance_inputs 的組合,斷言行為清單 P 段。

一輪是兩個回合:agent 開好來源頁交件,程式在那一刻讀工作區一次、編號交給同一個 agent 引段(P-02)。
"""
import contextlib
import copy
import dataclasses
import datetime
import io
import itertools
import os
import re
import unittest

import acceptance_inputs as I
from acceptance_home import LANG, Home
import fake_browser
from fake_browser import FakeBrowser, Script, Tab
import agent_report
import config as cf
import json
import profile_sync
import progress
import website_notes
from web import listing
from web.brief import EGO

TODAY = datetime.date.today()
MAIL_URL, RECORD_URL = 'https://mail.example/inbox', 'https://jobs.example/me/applications'
MAIL, RECORD = '信箱', 'jobs.example'
CARD, OTHER, UNSURE = 'https://jobs.example/jobs/1', 'https://jobs.example/jobs/3', 'https://jobs.example/jobs/9'
OLD, ROUND_ID = 'https://jobs.example/jobs/11', '20261028-101500-000000-fill-submit'
SUBJECT, QUOTE = 'Re: Engineer at Acme', 'Thank you for applying to Acme'
RECORD_LINE = 'Engineer at Acme - Application received'
KIND_CODE = {'確認信': 'confirm', '拒絕': 'reject', '面試或測驗邀請': 'interview', '錄取': 'offer'}
OC = {'reject': 'rej', 'interview': 'iv', 'offer': 'offer'}


def expect(test, behavior, actual, wanted, case=()):
    test.assertEqual(actual, wanted, f'{behavior} {case}')


def sections(prompt):
    """指示的每一段:【標題】→ 內容。"""
    return dict(re.findall(r'【([^】]+)】\n(.*?)(?=\n\n【|\Z)', prompt, re.S))


def stored(kind, date, verified=True, src=MAIL, link=MAIL_URL, subject=SUBJECT, quote=QUOTE, source_type='email'):
    """卡上存的一則回音(I-10):核對到照頁上原字;種類、日期照 agent 原字,沒寫日期記今天加「紀錄沒寫」。"""
    item = {'src': src, 'kind': KIND_CODE.get(kind, kind), 'date': date or f'{TODAY.isoformat()} (紀錄沒寫)', 'todo': '',
            'subject': subject, 'quote': quote, 'snippet': quote, 'reason': '', 'link': link if verified else '',
            'source_type': source_type}
    return dict(item, id=listing.reply_id(item))


def day(n):
    return (TODAY - datetime.timedelta(days=n)).isoformat()


def mail_tab(state=I.READ, *extra):
    return Tab('p1', MAIL_URL, state, (SUBJECT, QUOTE, *extra))


def record_tab(state=I.READ):
    return Tab('p2', RECORD_URL, state, (RECORD_LINE,))


def nav(tabs, sources=None, end=I.HANDED):
    """第一回合:agent 開好來源頁,交件單寫每個分頁是哪個來源。"""
    rows = sources if sources is not None else [{'tab': t.label, 'source': MAIL if t.url == MAIL_URL else RECORD}
                                                for t in tabs]
    return Script((('open', tabs),), {'pages': rows, 'needs_user': '', 'stopped': ''}, end)


def cite(replies=(), checked=(CARD,), unreadable=(), end=I.HANDED):
    """第二回合:照程式讀到的編號材料引段。replies 每一列 (卡, 來源, 種類, 主旨字, 引句字, 日期, 引得到沒有[, 其他欄]);
    其他欄是吃編號材料、回其他欄的函式;replies 是字串時整格照寫(不是清單)。"""
    def build(printed):
        material = printed[0]
        bad = {'material_id': material.material_id, 'ids': [9999]}
        rows = replies if isinstance(replies, str) else [
            {'card': card, 'source': source, 'kind': kind,
             'subject': material.cite(subject) if ok is True else ok or bad,
             'quote': material.cite(quote) if ok is True else ok or bad,
             'date': date, 'todo': '', **(more[0](material) if more else {})}
            for card, source, kind, subject, quote, date, ok, *more in replies]
        return {'checked': [{'card': card} for card in checked], 'replies': rows,
                'unreadable': [{'source': s, 'reason': '登入過期'} for s in unreadable], 'needs_user': '', 'stopped': ''}
    return Script((), build, end)


class ProgressCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.home = Home(self)
        settings = copy.deepcopy(self.home.settings)
        settings['replies'] = {'mail_url': MAIL_URL, 'ghost_days': 30}
        settings['accept'] = {'facts': {'name': ['Alex Example'], 'email': ['alex@example.com']}}
        with open(os.path.join(self.home.home, cf.NAME), 'w', encoding='utf-8') as target:
            json.dump(settings, target, ensure_ascii=False)
        cf.reload(self.home.home)

    def start(self, cards=None, record=True):
        path = os.path.join(self.home.home, 'profiles.json')
        if os.path.exists(path):
            os.remove(path)
        if record:
            profile_sync.remember_application_history(RECORD, RECORD_URL)
        cards = cards if cards is not None else {CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(40)}}
        jobs = [{'id': url, 'target': 'Engineer - Acme', 'title': 'Engineer', 'company': 'Acme'} for url in cards]
        self.home.write_board(jobs, copy.deepcopy(cards))

    def go(self, *scripts, browser=None):
        browser = browser or FakeBrowser(*scripts)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.code = progress.main(['--board', self.home.board], browser=browser)
        self.out = out.getvalue()
        return self.code, browser

    def card(self, url=CARD):
        return self.home.fb()[url]

    def replies(self, url=CARD):
        return self.card(url).get('replies') or {}


class SourceTest(ProgressCase):
    """P-08、P-09、P-10、P-11、P-17、P-20:每個來源讀得到、讀不到、沒開、開在別的網址、agent 沒標、agent 說讀不到,
    受影響的卡照實標;每個沒查到的來源回報一則,上一輪的回報收掉。"""

    SOURCES = {MAIL: (MAIL_URL, ('p1', 'p:3'), ''),   # 分頁名是工作區編號後面整段,本身可以有冒號
               RECORD: (RECORD_URL, ('p2', 'p4'), RECORD_URL)}

    def tabs(self, name, state):
        url, labels, _check = self.SOURCES[name]
        return {'read': [Tab(labels[0], url)], 'agent_says_unread': [Tab(labels[0], url)],
                'unreadable': [Tab(labels[0], url, I.UNREADABLE)], 'not_open': [],
                'other_url': [Tab(labels[0], url + '?page=2')], 'unlabeled': [Tab(labels[0], url)],
                'twice_unreadable': [Tab(label, url, I.UNREADABLE) for label in labels]}[state]

    def program_rows(self, name, state):
        url, _labels, check = self.SOURCES[name]
        missing = [{'source': name, 'url': check, 'by': 'program',
                    'reason': '應徵紀錄頁不在程式讀到的工作區' if check else '交件單沒有把工作區的分頁標成這個來源'}]
        unread = [{'source': name, 'url': url, 'by': 'program',
                   'reason': f'{fake_browser.shown(label, url)}讀取失敗：{fake_browser.READ_FAIL}'}
                  for label in _labels]
        return {'unreadable': unread[:1], 'twice_unreadable': unread, 'not_open': missing,
                'other_url': missing if check else [], 'unlabeled': [] if check else missing}.get(state, [])

    def test_each_source_state_marks_every_card(self):
        for mail_state, record_state in itertools.product(I.SOURCE_PAGES, I.SOURCE_PAGES):
            case = (mail_state, record_state)
            states = {MAIL: mail_state, RECORD: record_state}
            self.start({CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5)},
                        OTHER: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5)}})
            old = {'id': 'r0', 'at': '2026-01-01T00:00:00', 'from': '查應徵進度', 'msg': '上一輪的', 'n': 1}
            other = dict(old, id='r9', **{'from': '找缺'})
            import board_doc
            board_doc.set_fb(lambda fb: fb.update({agent_report.KEY: [dict(old), dict(other)]}), live=self.home.board)
            tabs = [tab for name in self.SOURCES for tab in self.tabs(name, states[name])]
            labelled = [{'tab': tab.label, 'source': name} for name in self.SOURCES for tab in self.tabs(name, states[name])
                        if states[name] != 'unlabeled']
            agent_unread = [name for name in self.SOURCES if states[name] == 'agent_says_unread']
            self.go(nav(tuple(tabs), labelled), cite(checked=(CARD, OTHER), unreadable=agent_unread))
            program = [row for name in self.SOURCES for row in self.program_rows(name, states[name])]
            said = [{'source': name, 'reason': '登入過期', 'url': self.SOURCES[name][0], 'by': 'agent'} for name in agent_unread]
            visible_program = [{key: row[key] for key in ('source', 'url', 'by')} for row in program]
            for url in (CARD, OTHER):
                visible_rows = [{key: row[key] for key in ('source', 'url', 'by', 'reason') if key in row}
                                for row in self.replies(url).get('unchecked_sources')]
                expect(self, 'P-10 P-20', visible_rows,
                       visible_program + said, (case, url))
                expect(self, 'P-11', self.replies(url).get('at'), TODAY.isoformat() if not program else None, (case, url))
            expect(self, 'P-09' if 'not_open' in case else 'P-08', (self.home.status_file('reply')['phase'], self.code),
                   ('done' if not program else 'incomplete', 0), case)
            reports = self.home.fb()[agent_report.KEY]
            expect(self, 'P-17', [dict(r, id='', at='') for r in reports], [
                dict(old, id='', at='', done=TODAY.isoformat(), res='這一輪重查過了'), dict(other, id='', at='')], case)
            if program:
                page_reads = []
                for root, _dirs, files in os.walk(self.home.dir):
                    if 'events.jsonl' in files:
                        with open(os.path.join(root, 'events.jsonl'), encoding='utf-8') as source:
                            page_reads.extend(json.loads(line) for line in source if line.strip())
                reasons = {row.get('reason') for row in page_reads if row.get('kind') == 'page_read'
                           and row.get('reason')}
                page_errors = [row['reason'] for row in program if fake_browser.READ_FAIL in row['reason']]
                expect(self, 'P-17', all(reason in reasons for reason in page_errors), True, case)


class ReplyTest(ProgressCase):
    """P-04、P-05、P-06、P-07、A-27:每則回音照核對過的原文存;種類、日期照 agent 原字;四種以外不改卡。"""

    def test_every_reply_kind_cite_and_date(self):
        for kind, cited, date in itertools.product(I.REPLY_KINDS, I.REPLY_CITES, ('2026-11-01', ' 2026-11-01 ', '')):
            case = (kind, cited, date)
            self.start()
            _code, browser = self.go(nav((mail_tab(),)), cite([(CARD, MAIL, kind, SUBJECT, QUOTE, date, cited == 'verified')]))
            verified = cited == 'verified'
            bad = {'material_id': browser.printed[-1].material_id, 'ids': [9999]}
            item = stored(kind, date) if verified else dict(
                stored(kind, date, False, subject=f'{bad["material_id"]}、9999',
                       quote=f'{bad["material_id"]}、9999'), unverified=True,
                agent_said={'card': CARD, 'source': MAIL, 'kind': kind, 'subject': bad, 'quote': bad, 'date': date,
                            'todo': '', 'reason': None})
            expect(self, 'P-04 P-05 P-07', self.replies()['items'], [item], case)
            moved = verified and KIND_CODE.get(kind) in OC
            when = date if date == date.strip() and date else TODAY.isoformat()
            expect(self, 'P-06 P-07 P-16', {key: self.card().get(key) for key in ('oc', 'oc_at', 'oc_auto')},
                   {'oc': OC[KIND_CODE[kind]], 'oc_at': {OC[KIND_CODE[kind]]: when},
                    'oc_auto': {'s': OC[KIND_CODE[kind]], 'from': '', 'at': TODAY.isoformat(), 'by': item['id']}}
                   if moved else {'oc': None, 'oc_at': None, 'oc_auto': None}, case)


    def test_a_source_not_on_his_list_is_kept_as_unchecked(self):
        """回音寫了不在登記清單的來源:那一則照 agent 寫的存成沒核對到,其他則照收,這一輪不停(A-29、P-04)。"""
        self.start()
        code, browser = self.go(nav((mail_tab(I.READ, 'Re: Other'),)), cite([(CARD, 'Gmail', '確認信', SUBJECT, QUOTE, day(1), True),
                                                          (CARD, MAIL, '拒絕', 'Re: Other', QUOTE, day(1), True)]))
        material = browser.printed[-1]
        gmail = dict(stored('確認信', day(1), src='Gmail', source_type=''), unverified=True, agent_said={
            'card': CARD, 'source': 'Gmail', 'kind': '確認信', 'subject': material.cite(SUBJECT),
            'quote': material.cite(QUOTE), 'date': day(1), 'todo': '', 'reason': None})
        expect(self, 'A-29 P-04', (code, sorted(self.replies()['items'], key=lambda item: item['src'])),
               (0, [gmail, stored('拒絕', day(1), subject='Re: Other')]), 'Gmail')

    def test_what_he_must_do_and_the_rejection_reason(self):
        self.start()
        self.go(nav((mail_tab(I.READ, 'Please complete the survey', 'We went with another candidate'),)),
                cite([(CARD, MAIL, '拒絕', SUBJECT, QUOTE, day(1), True,
                       lambda m: {'todo': '填問卷', 'reason': m.cite('We went with another candidate')})]))
        expect(self, 'P-04', self.replies()['items'],
               [dict(stored('拒絕', day(1)), todo='填問卷', reason='We went with another candidate')])

    def test_replies_that_are_not_a_list(self):
        self.start()
        self.go(nav((mail_tab(),)), cite('沒有回音'))
        expect(self, 'A-30', (self.code, self.replies()['items']), (0, []))


class GhostTest(ProgressCase):
    """P-12:送出滿天數、程式真的查過、agent 在 checked 列了、沒有核對過的回音,才標沒下文;之後有回音改回來。"""

    # 送出那天(幾天前、時間戳記)、他按過「不是沒下文」、已經有結果
    PRIORS = {'waiting': ({'sent_at': day(5)}, None), 'sent_long_ago': ({'sent_at': day(40)}, 40),
              'exactly_due': ({'sent_at': day(30)}, 30), 'timestamped': ({'sent_at': day(40) + 'T09:30:00'}, 40),
              'no_sent_day': ({}, None), 'user_said_no': ({'sent_at': day(40), 'ghost_no': 1}, None),
              'interviewing': ({'sent_at': day(40), 'oc': 'iv', 'oc_at': {'iv': day(9)}}, None)}

    def test_no_reply_only_after_a_real_check(self):
        for prior, checked, all_read, reply in itertools.product(self.PRIORS, I.CHECKED, (True, False),
                                                                ('none', 'verified', 'unverified')):
            case = (prior, checked, all_read, reply)
            extra, age = self.PRIORS[prior]
            self.start({CARD: {'app': 'sent', 'ds': 'sent', **copy.deepcopy(extra)}})
            tabs = (mail_tab(), record_tab(I.READ if all_read else I.UNREADABLE))
            rows = [] if reply == 'none' else [(CARD, MAIL, '確認信', SUBJECT, QUOTE, '', reply == 'verified')]
            self.go(nav(tabs), cite(rows, checked=(CARD,) if checked else ()))
            ghost = age is not None and checked and all_read and reply != 'verified'
            expect(self, 'P-12', {key: self.card().get(key) for key in ('oc', 'oc_at', 'oc_auto')},
                   {'oc': 'ghost', 'oc_at': {'ghost': TODAY.isoformat()},
                    'oc_auto': {'s': 'ghost', 'from': '', 'at': TODAY.isoformat(), 'by': f'送出 {age} 天沒有回音'}}
                   if ghost else {key: extra.get(key) for key in ('oc', 'oc_at', 'oc_auto')}, case)
            expect(self, 'P-18', self.home.status_file('reply')['msg'],
                   f'查了 1 張,查完 {int(checked and all_read)} 張;新回音 {int(reply != "none")} 則;改了狀態 {int(ghost)} 張',
                   case)
            if ghost:      # 之後收到確認信(兩封,照最後一封):改回等回音,沒下文那天照記在 from_at
                letters = [(CARD, MAIL, '確認信', SUBJECT, line, '', True) for line in (QUOTE, RECORD_LINE)]
                self.go(nav((mail_tab(I.READ, RECORD_LINE), record_tab())), cite(letters))
                expect(self, 'P-12 P-16', {key: self.card().get(key) for key in ('oc', 'oc_auto')},
                       {'oc': None, 'oc_auto': {'s': '', 'from': 'ghost', 'at': TODAY.isoformat(),
                                              'by': stored('確認信', '', quote=RECORD_LINE)['id'],
                                              'from_at': {'ghost': TODAY.isoformat()}}}, case)


class MovedTest(ProgressCase):
    """P-12、P-16、P-18:一輪改了幾張照實數;程式記的沒下文收到拒絕,改成拒絕。"""

    def test_two_cards_go_quiet(self):
        self.start({url: {'app': 'sent', 'ds': 'sent', 'sent_at': day(40)} for url in (CARD, OTHER)})
        self.go(nav((mail_tab(), record_tab())), cite(checked=(CARD, OTHER)))
        expect(self, 'P-18', ([self.card(url).get('oc') for url in (CARD, OTHER)], self.home.status_file('reply')['msg']),
               (['ghost', 'ghost'], '查了 2 張,查完 2 張;新回音 0 則;改了狀態 2 張'))

    def test_quiet_cards_get_a_rejection(self):
        ghost = {'app': 'sent', 'ds': 'sent', 'sent_at': day(40), 'oc': 'ghost', 'oc_at': {'ghost': day(2)},
                 'oc_auto': {'s': 'ghost', 'from': '', 'at': day(2), 'by': '送出 38 天沒有回音'}}
        self.start({url: copy.deepcopy(ghost) for url in (CARD, OTHER)})
        rows = [(CARD, MAIL, '拒絕', SUBJECT, QUOTE, day(1), True), (OTHER, MAIL, '拒絕', SUBJECT, RECORD_LINE, day(1), True)]
        self.go(nav((mail_tab(I.READ, RECORD_LINE), record_tab())), cite(rows, checked=(CARD, OTHER)))
        for url, line in ((CARD, QUOTE), (OTHER, RECORD_LINE)):
            expect(self, 'P-16', {key: self.card(url).get(key) for key in ('oc', 'oc_at', 'oc_auto')}, {
                'oc': 'rej', 'oc_at': {'rej': day(1)},        # 換另一種結案取代
                'oc_auto': {'s': 'rej', 'from': 'ghost', 'at': TODAY.isoformat(), 'by': stored('拒絕', day(1), quote=line)['id'],
                            'from_at': {'ghost': day(2)}}}, url)
        expect(self, 'P-18', self.home.status_file('reply')['msg'], '查了 2 張,查完 2 張;新回音 2 則;改了狀態 2 張')


class UnsureTest(ProgressCase):
    """P-13:送出結果不明的卡,有核對過的確認信或紀錄就記成已送出;都查過、checked 列了卻沒有就改成要重填;核實不了的回報。"""

    TRIED = {'day': (day(2), day(2)),
             'zoned': ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).isoformat(), day(2)),
             'round_id': ('20261028-101500-000000-fill-submit', TODAY.isoformat()),
             'just_now': (datetime.datetime.now().isoformat(), TODAY.isoformat())}

    def test_unsure_cards_follow_the_evidence(self):
        for reply, checked, all_read, tried in itertools.product(('none', 'verified', 'unverified', 'quote_only'), I.CHECKED,
                                                                 (True, False), self.TRIED):
            case = (reply, checked, all_read, tried)
            at, sent_day = self.TRIED[tried]
            prior = {'app': 'ship', 'ds': 'unsure', 'resume_id': 'one',
                     'apply': {'at': at, 'submit_fail': {'at': at}, 'tab_id': ''}}
            open_report = {'id': 'r1', 'at': '2026-01-01T00:00:00', 'from': agent_report.FROM_APPLY, 'msg': '送出結果不明',
                           'job': UNSURE, 'n': 1}
            self.start({})      # 登記應徵紀錄頁
            others = [dict(open_report, id='r2', job=OTHER), dict(open_report, id='r3', **{'from': '找缺'})]
            self.home.write_board([{'id': UNSURE, 'target': 'Engineer - Acme', 'title': 'Engineer', 'company': 'Acme'}],
                                  {UNSURE: copy.deepcopy(prior), agent_report.KEY: [dict(open_report), *copy.deepcopy(others)]})
            tabs = (mail_tab(), record_tab(I.READ if all_read else I.UNREADABLE))
            wrong_subject = (lambda m: {'subject': {'material_id': m.material_id, 'ids': [9999]}},) if reply == 'quote_only' else ()
            rows = [] if reply == 'none' else [(UNSURE, MAIL, '確認信', SUBJECT, QUOTE, '', reply in ('verified', 'quote_only'), *wrong_subject)]
            self.go(nav(tabs), cite(rows, checked=(UNSURE,) if checked else ()))
            card = {key: value for key, value in self.card(UNSURE).items() if key != 'replies'}
            stamp = (card.get('history') or [{}])[-1].get('at', '')
            reports = self.home.fb()[agent_report.KEY]
            expect(self, 'P-13', reports[1:3], others, case)       # 別張卡的、別的流程開的不收
            reports = reports[:1] + [r for r in reports[3:] if r.get('job') == UNSURE]
            if reply == 'verified':     # 那一則就是送出的證據;寄的那一份照卡上記(統計表用)
                expect(self, 'P-13', (card, re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d', stamp) is not None), (
                    {'app': 'sent', 'ds': 'sent', 'resume_id': 'one', 'sent_by': 'agent', 'sent_at': sent_day,
                     'sent_v': f'{LANG}-one', 'sent_att': [], 'history': [{'event': 'actually_sent', 'at': stamp}],
                     'apply': {'at': at, 'tab_id': '', 'sent': dict(stored('確認信', ''), card=UNSURE, by='reply', at=stamp)}},
                    True), case)
                expect(self, 'P-13', reports, [dict(open_report, done=TODAY.isoformat(),
                                                    res='查應徵進度查到送出的證據,已改成已投出')], case)
            elif reply == 'none' and checked and all_read and tried in ('day', 'zoned'):
                expect(self, 'P-13', card, {
                    'app': 'ship', 'ds': 'gone', 'resume_id': 'one', 'apply': {'at': at, 'tab_id': '', 'issues': [progress.NOT_SENT]},
                    'history': [{'event': 'not_sent', 'at': stamp, 'apply.submit_fail': {'at': at}}]}, case)
                expect(self, 'P-13', reports, [dict(open_report, done=TODAY.isoformat(),
                                                    res='查應徵進度查過了,沒有送出的證據')], case)
            else:       # 讀不懂按送出的時間、或還沒等滿:算不出等了多久,不判沒送出(會重送);核實不了的回報附引句那頁,沒有就附職缺
                expect(self, 'P-13', card, prior, case)
                new = [dict(r, id='', at='') for r in reports if r['from'] == '查回音']
                expect(self, 'P-13', new, [{'id': '', 'at': '', 'from': '查回音', 'msg': progress.UNCLEAR, 'n': 1,
                                            'need': progress.UNCLEAR_NEED, 'job': UNSURE,
                                            'url': MAIL_URL if reply == 'quote_only' else UNSURE}]
                       if reply in ('unverified', 'quote_only') else [], case)


class RecordTest(ProgressCase):
    """P-21、P-07、P-13:核對過的一則來自應徵紀錄頁,那張卡記成平台找到的已投遞,投遞日照紀錄上一字不差的那一天;
    退回過的那一筆、不晚於上一次投遞的那一筆不拉回;沒核對到、或是信箱來的不算。"""

    PRIORS = {'waiting': {'app': 'sent', 'ds': 'sent', 'sent_at': day(40), 'sent_by': 'agent'},
              'same_day': {'app': 'sent', 'ds': 'sent', 'sent_at': day(3), 'sent_by': 'agent'},
              'backed_out': {'app': 'sent', 'ds': 'sent', 'sent_at': day(1), 'sent_by': 'agent',
                             'sync_no': [f'{CARD}:{day(3)}']},
              'unsure': {'app': 'ship', 'ds': 'unsure', 'apply': {'at': day(2), 'submit_fail': {'at': day(2)}}},
              'reapplied': {'app': 'ship', 'ds': 'unsure', 'apply': {'at': day(2), 'submit_fail': {'at': day(2)}},
                            'tries': [{'sent_at': day(3)}]},
              'reapplied_later': {'app': 'ship', 'ds': 'unsure', 'apply': {'at': day(2), 'submit_fail': {'at': day(2)}},
                                  'tries': [{'sent_at': day(5)}]},
              'tried_unknown': {'app': 'ship', 'ds': 'unsure', 'apply': {'at': day(2), 'submit_fail': {'at': day(2)}},
                                'tries': [{}]}}

    def test_a_cited_application_record_marks_the_card_sent(self):
        import delivery_core
        for prior, date, cited, source in itertools.product(self.PRIORS, (day(3), '上週', ''), (True, False),
                                                            (RECORD, MAIL)):
            case = (prior, date, cited, source)
            before = dict(copy.deepcopy(self.PRIORS[prior]), resume_id='one')
            self.start({CARD: before})
            line = RECORD_LINE if source == RECORD else QUOTE
            self.go(nav((mail_tab(), record_tab())), cite([(CARD, source, '確認信', line, line, date, cited)]))
            card = self.card()
            iso = date == day(3)
            unsure = before['app'] == 'ship'
            found = cited and source == RECORD and {
                'waiting': iso, 'same_day': False, 'backed_out': False, 'unsure': True, 'reapplied': False,
                'reapplied_later': iso, 'tried_unknown': True}[prior]
            if found:
                sent_at = day(3) if iso else TODAY.isoformat()
                expect(self, 'P-21', (delivery_core.state(card), card.get('sent_by'), card.get('sent_rec')),
                       ('sent', 'platform' if unsure else 'agent', f'{CARD}:{sent_at}'), case)   # 已投遞的只改日期
                expect(self, 'P-07', card.get('sent_at'), sent_at, case)
                expect(self, 'P-21', card.get('sent_v'), f'{LANG}-one', case)      # 寄的那一份(統計表用)
                if unsure:      # 改成已投遞那一步記進卡的歷程
                    stamp = card['history'][-1]
                    expect(self, 'P-21', (stamp['event'], re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d', stamp['at']) is not None),
                           ('platform_found', True), case)
            elif unsure:
                expect(self, 'P-13', (delivery_core.state(card) == 'sent', card.get('sent_by')), (cited, 'agent' if cited else None), case)
            else:       # 卡上什麼都沒動(沒下文另外判)
                expect(self, 'P-21', {key: value for key, value in card.items() if key not in ('replies', 'oc', 'oc_at', 'oc_auto')},
                       before, case)


class OrderTest(ProgressCase):
    """P-06、P-07、P-16:一輪好幾則核對過的回音照日期一則一則改卡,最後一則的身分記在 by;改之前的日期記在 from_at。"""

    def test_replies_apply_in_date_order(self):
        for prior in (None, 'iv'):
            self.start({CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(40),
                               **({'oc': 'iv', 'oc_at': {'iv': day(20)}} if prior else {})}})
            rows = [(CARD, MAIL, '拒絕', SUBJECT, RECORD_LINE, day(3), True),
                    (CARD, MAIL, '面試或測驗邀請', SUBJECT, QUOTE, day(9), True)]
            self.go(nav((mail_tab(I.READ, RECORD_LINE), record_tab())), cite(rows))
            reject = stored('拒絕', day(3), quote=RECORD_LINE)
            expect(self, 'P-06 P-16', {key: self.card().get(key) for key in ('oc', 'oc_at', 'oc_auto')}, {
                'oc': 'rej', 'oc_at': {'iv': day(20) if prior else day(9), 'rej': day(3)},   # 已有的日期不改
                'oc_auto': {'s': 'rej', 'from': prior or '', 'at': TODAY.isoformat(), 'by': reject['id'],
                            **({'from_at': {'iv': day(20)}} if prior else {})}}, prior)


class NotesTest(ProgressCase):
    """P-01、A-37:兩個回合交件單寫的網站筆記照主機記下;網址或那一句空的不記。要本人處理的那一頁交到他面前(C-11)。"""

    def test_notes_from_both_rounds_and_hand_over(self):
        self.start()
        first = Script((('open', (mail_tab(), record_tab())),),
                       {'pages': [{'tab': 'p1', 'source': MAIL}, {'tab': 'p2', 'source': RECORD}], 'stopped': '',
                        'needs_user': '要你登入', 'needs_user_tab': '7:p1', 'needs_user_quote': SUBJECT,
                        'website_notes': [{'url': MAIL_URL, 'note': '用寄件人搜尋'}]})

        def second(printed):
            return dict(cite().sheet(printed), website_notes=[{'url': RECORD_URL, 'note': '要捲到底'},
                                                              {'url': '', 'note': '沒網址'}, {'url': RECORD_URL, 'note': ''}])
        _code, browser = self.go(first, Script((), second))
        expect(self, 'A-37', {host: [note['text'] for note in website_notes.for_url(url)]
                              for host, url in (('mail', MAIL_URL), ('jobs', RECORD_URL))},
               {'mail': ['用寄件人搜尋'], 'jobs': ['要捲到底']})
        expect(self, 'C-11', browser.handed, ['7:p1'])


class StoredTest(ProgressCase):
    """P-22、P-06、P-16:卡上已經有的回音(舊版存的也一樣)這一輪又回報:不重複加、不重放,他按過「不對,復原」的卡不再改;
    舊版存的沒核對回音這一輪核對到了,原地補成核對過、他按過的照留;舊版記著還沒套用的才照它改卡,其他分不出套用過沒,不重放。"""

    LEGACY = {'src': MAIL, 'source_type': 'email', 'source_ref': 'email:abc', 'date': day(5), 'subject': SUBJECT,
              'quote': QUOTE, 'snippet': QUOTE, 'kind': 'reject', 'id': 'historical-id', 'maybe_ok': True, 'done': True}

    def stored(self, how):
        """先存好那一則:這一版上一輪真的跑出來的,或舊版存的(舊的身分算法)。"""
        if how == 'verified':
            self.start({CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(10)}})
            self.go(nav((mail_tab(),)), cite([(CARD, MAIL, '拒絕', SUBJECT, QUOTE, day(5), True)]))
            return self.card()
        said = {'card': CARD, 'source': MAIL, 'kind': '拒絕'}
        item = dict(self.LEGACY, **{'legacy_pending': {'unverified': True, 'effect_pending': True, 'agent_said': said},
                                    'legacy_unverified': {'unverified': True, 'agent_said': said}}.get(how, {}))
        card = {'app': 'sent', 'ds': 'sent', 'sent_at': day(10), 'replies': {'items': [item], 'at': day(5)}}
        return dict(card, oc='rej', oc_at={'rej': day(5)}, oc_auto={'s': 'rej', 'from': '', 'by': 'historical-id'}) \
            if how == 'legacy_verified' else card

    def test_a_reply_already_on_the_card(self):
        for how, vetoed in itertools.product(('verified', 'legacy_unverified', 'legacy_pending', 'legacy_verified'),
                                             (False, True)):
            case = (how, vetoed)
            card = self.stored(how)
            item = card['replies']['items'][0]
            if vetoed:                               # 他按過「不對,復原」:那一則標他說不對,卡回到原本的狀態
                item['effect_rejected'] = True
                for key in ('oc', 'oc_at', 'oc_auto'):
                    card.pop(key, None)
            before = copy.deepcopy(item)
            jobs = [{'id': CARD, 'target': 'Engineer - Acme', 'title': 'Engineer', 'company': 'Acme'}]
            self.home.write_board(jobs, {CARD: copy.deepcopy(card)})
            self.go(nav((mail_tab(),)), cite([(CARD, MAIL, '拒絕', SUBJECT, QUOTE, day(5), True)]))
            items = self.replies()['items']
            expect(self, 'P-22', len(items), 1, case)
            fresh = {key: value for key, value in stored('拒絕', day(5)).items() if key != 'id'}
            expect(self, 'P-22', items[0], dict({key: value for key, value in before.items()
                                                  if key not in ('unverified', 'agent_said', 'effect_pending')}, **fresh)
                   if before.get('unverified') else before, case)
            moved = not vetoed and how != 'legacy_unverified'
            expect(self, 'P-16' if vetoed else 'P-06', self.card().get('oc') or '', 'rej' if moved else '', case)
            if moved:
                expect(self, 'P-16', (self.card()['oc_auto']['s'], self.card()['oc_auto']['by'], self.card()['oc_at']),
                       ('rej', before['id'], {'rej': day(5)}), case)

    def test_another_source_subject_or_quote_is_another_reply(self):
        """回音的身分只看來源、主旨、引句:三樣有一樣不同就是另一則,種類、日期不同還是同一則。"""
        jobs = [{'id': CARD, 'target': 'Engineer - Acme', 'title': 'Engineer', 'company': 'Acme'}]
        for key, value in (('src', RECORD), ('subject', 'Re: Other'), ('quote', 'Another line'), ('kind', 'confirm'),
                           ('date', day(9))):
            held = dict(self.LEGACY, **{key: value})
            self.home.write_board(jobs, {CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(10),
                                                'replies': {'items': [held], 'at': day(5)}}})
            self.go(nav((mail_tab(),)), cite([(CARD, MAIL, '拒絕', SUBJECT, QUOTE, day(5), True)]))
            expect(self, 'P-22', [item[key] for item in self.replies()['items']],
                   [value, stored('拒絕', day(5))[key]] if key in ('src', 'subject', 'quote') else [value], key)

    def test_the_same_unchecked_reply_again(self):
        """同一則沒核對到的回音下一輪又一樣沒核對到:卡上那一則照留(第一輪 agent 寫的),不重複加。"""
        wrong = {'material_id': 'old', 'ids': [9999]}
        self.start({CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(10)}})
        self.go(nav((mail_tab(),)), cite([(CARD, MAIL, '拒絕', SUBJECT, QUOTE, '', wrong)]))
        first = self.replies()['items']
        self.go(nav((mail_tab(),)), cite([(CARD, MAIL, '拒絕', SUBJECT, QUOTE, day(1), wrong)]))
        expect(self, 'P-22', (len(first), self.replies()['items']), (1, first))


class EndTest(ProgressCase):
    """P-14、P-18、A-16:任一回合沒做完,卡不動;狀態檔照實寫 nothing、done、incomplete、failed。"""

    def test_an_unfinished_round_leaves_cards_alone(self):
        for which, end in itertools.product(('nav', 'cite'), I.ENDS):
            case = (which, end)
            self.start({CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(40)}})
            tabs = (mail_tab(), record_tab())
            first = nav(tabs, end=end if which == 'nav' else I.HANDED)
            second = cite([(CARD, MAIL, '拒絕', SUBJECT, QUOTE, '', True)], end=end if which == 'cite' else I.HANDED)
            self.go(first, second)
            finished = end == I.HANDED
            display_end = {I.HANDED: '交件', I.NO_SHEET: '沒交件', I.BROKEN_SHEET: '交件',
                           I.TIMEOUT: '逾時', I.CRASHED: '當掉',
                           I.STOPPED_BY_USER: '被停止', I.UNAVAILABLE: '沒有啟動'}[end]
            card = self.card()
            expect(self, 'P-14', (bool(self.replies().get('at')), bool(self.replies().get('items')), card.get('oc') or ''),
                   (True, True, 'rej') if finished else (False, False, ''), case)
            expect(self, 'P-18', self.status(), {
                'phase': 'done' if finished else 'failed', 'n': 1, 'done': int(finished),
                'msg': '查了 1 張,查完 1 張;新回音 1 則;改了狀態 1 張' if finished else
                       f'這一輪{display_end},卡沒有改'},
                case)
            expect(self, 'P-18', (self.code, self.out), (0 if finished else 1, self.status()['msg'] + '\n'), case)
            expect(self, '#807', self.stuck_reports(), [], case)        # 沒做完明天照排程重查,不打擾
            if finished:        # 看板流水帳記是誰改的
                import board_doc
                with open(board_doc.journal_path(self.home.board), encoding='utf-8') as marks:
                    last = json.loads(marks.readlines()[-1])
                expect(self, 'P-18', (last['by'], CARD in last['d']), (progress.FROM, True), case)

    def stuck_reports(self):
        import agent_report
        return [r for r in agent_report.todo(self.home.fb()) if 'progress' in r.get('parts', {})]

    def test_only_a_page_handed_to_the_user_reaches_agent_report(self):
        """agent 交出核對得到的那一頁才當輪進要你做,點下去打開那一頁;只寫要本人處理、停下都算沒做完,
        明天照排程重查,不打擾;下一輪乾淨查完,這則收掉(#807)。回音用確認信:卡不結案,下一輪還會查。"""
        host = MAIL_URL.split('/')[2]
        words = {'needs_user': {'needs_user': '來源要重新登入'}, 'stopped': {'stopped': '停在登入頁'},
                 'handed': {'needs_user': '來源要重新登入', 'needs_user_tab': '7:p1', 'needs_user_quote': SUBJECT}}
        for which, cell in itertools.product(('nav', 'cite'), words):
            case = (which, cell)
            self.start({CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(40)}})
            first, second = nav((mail_tab(),)), cite([(CARD, MAIL, '確認信', SUBJECT, QUOTE, '', True)])
            said = first if which == 'nav' else second
            build = said.sheet
            said = dataclasses.replace(said, sheet=(lambda p, build=build: dict(build(p), **words[cell]))
                                       if callable(build) else dict(build, **words[cell]))
            self.go(*((said, second) if which == 'nav' else (first, said)))
            expect(self, '#807', [(r['msg'].splitlines()[0], '來源要重新登入' in r['msg'], r.get('url'))
                                  for r in self.stuck_reports()],
                   [(f'{host} 要你本人處理：', True, MAIL_URL)] if cell == 'handed' else [], case)
            self.go(nav((mail_tab(),)), cite([(CARD, MAIL, '確認信', SUBJECT, QUOTE, '', True)]))
            expect(self, '#807', self.stuck_reports(), [], case)

    def test_the_second_round_cannot_continue_the_same_agent(self):
        """第一回合交了件,第二回合接不上同一個 agent(沒有 session)或一叫就出錯:卡不動,照實講。"""
        for how, said in (('no_session', '沒有啟動'), ('raises', '當掉')):
            self.start({CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(40)}})
            browser = FakeBrowser(nav((mail_tab(),)), cite([(CARD, MAIL, '拒絕', SUBJECT, QUOTE, '', True)]))
            real, calls = browser.run_agent, []

            def run(prompt, how=how, real=real, calls=calls, **kw):
                calls.append(kw)
                if len(calls) == 2:
                    raise ValueError('派 agent 的指示或環境裡有看板檔的位置')
                result = real(prompt, **kw)
                result.session = None if how == 'no_session' else result.session
                return result
            browser.run_agent = run
            self.go(browser=browser)
            expect(self, 'P-14 P-18', (self.status(), len(calls), self.replies()),
                   ({'phase': 'failed', 'n': 1, 'done': 0,
                     'msg': f'這一輪{"沒有啟動" if how == "no_session" else "當掉"},卡沒有改'},
                    1 if how == 'no_session' else 2,
                    {}), how)

    def status(self):
        """看板讀的狀態檔(I-04):時間與程序號另外核對。"""
        status = self.home.status_file('reply')
        expect(self, 'I-04', (sorted(status), status['pid'], status['t0'] <= status['finished_at'] <= status['at']),
               (['at', 'done', 'finished_at', 'msg', 'n', 'phase', 'pid', 't0'], os.getpid(), True))
        return {key: status[key] for key in ('phase', 'n', 'done', 'msg')}

    def test_nothing_to_check(self):
        self.start({OTHER: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5)}})
        import board_doc
        board_doc.set_data(lambda data, _fb: data.update(jobs=[]), live=self.home.board)   # 卡片已經不在看板上
        _code, browser = self.go()
        expect(self, 'P-18', (self.status(), browser.prompts, self.code),
               ({'phase': 'nothing', 'n': 0, 'done': 0, 'msg': '沒有要查的卡'}, [], 0))

    def test_running_status_then_limit_and_one_card(self):
        """看板按「跑幾張」(--limit)、「只查這一張」(--url);跑的時候狀態檔寫 run。"""
        path = os.path.join(self.home.tmp['reply'], progress.STATUS)
        for argv, wanted in (([], [CARD, OTHER]), (['--limit', '1'], [CARD]), (['--url', OTHER], [OTHER])):
            self.start({CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5)},
                        OTHER: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5)}})
            peek = Script((('tool', lambda _b: json.load(open(path, encoding='utf-8'))), ('open', (mail_tab(), record_tab()))),
                          nav((mail_tab(), record_tab())).sheet)
            browser = FakeBrowser(peek, cite(checked=wanted))
            with contextlib.redirect_stdout(io.StringIO()):
                self.code = progress.main(['--board', self.home.board, *argv], browser=browser)
            running = browser.outputs[0]
            expect(self, 'I-04', ({key: running[key] for key in ('phase', 'n', 'done', 'pid')}, sorted(running)),
                   ({'phase': 'run', 'n': len(wanted), 'done': 0, 'pid': os.getpid()}, ['at', 'done', 'n', 'phase', 'pid', 't0']),
                   argv)
            expect(self, 'P-18', self.status()['n'], len(wanted), argv)
            expect(self, 'P-01', json.loads(sections(browser.prompts[1])['材料'].split('\n')[1]), wanted, argv)


class RemovedTest(ProgressCase):
    """P-15:查到一半被移除的卡,跑完不改它。"""

    def test_a_card_removed_during_the_round_is_left_alone(self):
        import board_doc
        self.start({CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5)},
                    OTHER: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5)}})

        def remove_then_cite(printed):
            board_doc.set_fb(lambda fb: fb[CARD].update(rm=1), live=self.home.board)
            return cite([(url, MAIL, '拒絕', SUBJECT, QUOTE, '', True) for url in (CARD, OTHER)],
                        checked=(CARD, OTHER)).sheet(printed)
        self.go(nav((mail_tab(), record_tab())), Script((), remove_then_cite))
        expect(self, 'P-15', (self.card().get('oc'), self.replies().get('items'), self.replies().get('at')),
               (None, None, None))
        expect(self, 'P-15', self.replies(OTHER).get('at'), TODAY.isoformat())


class MaybeTest(ProgressCase):
    """P-19:一封信掛到好幾張卡,不自動改,卡上寫可能是這封並附原文。"""

    def test_one_letter_on_several_cards(self):
        for cards in I.CARDS_PER_MAIL:
            urls = (CARD, OTHER)[:cards]
            self.start({url: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5)} for url in urls})
            rows = [(url, MAIL, '拒絕', SUBJECT, QUOTE, '', True) for url in urls]
            self.go(nav((mail_tab(), record_tab())), cite(rows, checked=urls))
            for url in urls:
                others = sorted(set(urls) - {url})
                expect(self, 'P-19', (self.card(url).get('oc'), self.replies(url)['items']),
                       ('rej', [stored('拒絕', '')]) if cards == 1 else (None, [dict(stored('拒絕', ''), maybe=others)]),
                       (cards, url))
            expect(self, 'P-18', self.home.status_file('reply')['msg'],
                   f'查了 {cards} 張,查完 {cards} 張;新回音 {cards} 則;改了狀態 {int(cards == 1)} 張', cards)


    def test_only_verified_outcome_letters_count(self):
        """確認信、沒核對到的、應徵紀錄頁上的那一行掛到兩張卡:不標可能是這封(不會因此改卡)。"""
        for kind, source, line, ok in (('確認信', MAIL, QUOTE, True), ('拒絕', MAIL, QUOTE, False),
                                       ('拒絕', RECORD, RECORD_LINE, True)):
            case = (kind, source, ok)
            self.start({url: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5)} for url in (CARD, OTHER)})
            self.go(nav((mail_tab(), record_tab())), cite([(url, source, kind, line, line, '', ok) for url in (CARD, OTHER)],
                                                          checked=(CARD, OTHER)))
            expect(self, 'P-19', [[item.get('maybe') for item in self.replies(url)['items']] for url in (CARD, OTHER)],
                   [[None], [None]], case)


class PromptTest(ProgressCase):
    """P-01、P-02、P-03、A-32:兩個回合的指示逐字(目標、材料、工具、界線);材料有每張卡從哪天查起、用哪份履歷寄的、
    他的名字與信箱、登記的來源、網站筆記;程式不開頁,只在交件那一刻讀一次工作區;每回合限時,指示記進這一輪的證據夾。"""

    def test_material_and_single_read(self):
        self.home.board = os.path.join(self.home.home, 'copy.html')        # 看板副本(沙箱):證據不放進使用者的資料夾
        tried = 'https://jobs.example/jobs/工程師-13'
        skipped = [f'https://jobs.example/jobs/2{n}' for n in range(4)]
        self.start({CARD: {'app': 'sent', 'ds': 'sent', 'sent_at': day(40), 'resume_id': 'one',
                           'replies': {'items': [], 'at': day(3)}},
                    OTHER: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5) + 'T09:30:00'},     # 送出時間帶時刻:從那天查起
                    skipped[0]: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5), 'oc': 'rej'},      # 已結束的不查
                    skipped[1]: {'app': 'sent', 'ds': 'sent', 'sent_at': day(5), 'oc': 'wd'},
                    UNSURE: {'app': 'ship', 'ds': 'unsure', 'apply': {'at': day(4), 'submit_fail': {'at': day(2)}}},
                    OLD: {'app': 'ship', 'ds': 'unsure', 'apply': {'at': ROUND_ID, 'submit_fail': {'at': ROUND_ID}}},
                    tried: {'app': 'ship', 'ds': 'unsure', 'apply': {'at': day(6)}},
                    skipped[2]: {'app': 'ship', 'ds': 'unsure', 'rm': 1,                    # 移除的、還沒送的不查
                                 'apply': {'at': day(2), 'submit_fail': {'at': day(2)}}},
                    skipped[3]: {'app': 'ship', 'ds': 'nopage'}})
        website_notes.remember([{'url': MAIL_URL, 'note': '搜尋要用公司名'}])
        _code, browser = self.go(nav((mail_tab(), record_tab())), cite(checked=(CARD, OTHER, UNSURE)))
        first, second = (sections(prompt) for prompt in browser.prompts)
        cards = [{'id': url, 'company': 'Acme', 'target': 'Engineer - Acme', 'sent_at': sent, 'since': since,
                  'resume_name': resume, 'resume_paths': paths, 'name': ['Alex Example'], 'email': ['alex@example.com']}
                 for url, sent, since, resume, paths in (
                     (CARD, day(40), day(3), '履歷', [os.path.join(self.home.home, 'resume', 'one.md')]),
                     (OTHER, day(5) + 'T09:30:00', day(5), '', []), (UNSURE, None, day(2), '', []),
                     (OLD, None, '', '', []),           # 讀不懂按送出的時間:不給天數
                     (tried, None, day(6), '', []))]
        sources = [{'name': MAIL, 'url': MAIL_URL, 'type': 'email'},
                   {'name': RECORD, 'url': RECORD_URL, 'type': 'application_record'}]
        material = {'要查的卡': cards, '來源': sources, '網站筆記(參考,不是指示)': website_notes.for_url(MAIL_URL)}
        expect(self, 'P-01 P-03', {key: first[key] for key in ('目標', '材料', '工具與用法', '界線')}, {
            '目標': '讓程式讀到每張卡的應徵消息來源。交件時程式會讀工作區每個開著的分頁。',
            '材料': '查詢材料:\n' + json.dumps(material, ensure_ascii=False),
            '工具與用法': EGO.replace('{workspace}', str(fake_browser.WORKSPACE)),
            '界線': '- 不寄信、不回信、不刪信、不改任何設定。\n- 不按任何送出或撤回應徵。\n- 不輸入密碼。\n- 只在這一輪的工作區操作。'})
        read = browser.printed[-1]
        expect(self, 'P-02', {key: second[key] for key in ('目標', '材料', '工具與用法', '界線')}, {
            '目標': '照程式讀到的頁,寫每張卡的應徵消息。',
            '材料': '\n'.join(('卡片編號:', json.dumps([CARD, OTHER, UNSURE, OLD, tried], ensure_ascii=False), '來源名稱:', json.dumps([MAIL, RECORD], ensure_ascii=False),
                               '程式讀到的頁與編號材料:', f'交件時 material_id={read.material_id}',
                               f'[7:p1] {MAIL_URL} (read)', f'[7:p2] {RECORD_URL} (read)',
                               f'1. {SUBJECT}', f'2. {QUOTE}', f'3. {RECORD_LINE}')),
            '工具與用法': '', '界線': '- 只引材料裡的段落。'})
        cited = {'material_id': '交付代號', 'ids': [1]}
        known = '照材料中的名稱原樣填寫'
        shape = {'checked': [{'card': known}],
                 'replies': [{'card': known, 'source': known, 'kind': '確認信', 'subject': cited, 'quote': cited,
                              'date': '原話', 'todo': '原話', 'reason': cited}],
                 'unreadable': [{'source': known, 'reason': '原話'}], 'website_notes': [{'url': '原話', 'note': '原話'}],
                 'needs_user': '原話', 'needs_user_tab': '原話', 'needs_user_quote': '原話', 'stopped': '原話'}
        how_to_cite = '    引段欄寫 material_id 和 ids;每段編號照這次提供的材料。'
        expect(self, 'P-02 P-04', second['交件單格式'], '\n'.join((
            '交件單格式(progress_replies):回傳完整 JSON 物件,不加 Markdown。', json.dumps(shape, ensure_ascii=False, indent=2),
            '欄位說明:', '- checked: 你查過的卡片編號', '  - card: 卡片編號 (必填)', '- replies: 每則回音',
            '  - card: 卡片編號 (必填)', '  - source: 來源名稱(照材料寫) (必填)',
            '  - kind: 種類;只看得出已收到申請的應徵紀錄算確認信 (必填)', '    可填: 確認信、拒絕、面試或測驗邀請、錄取',
            '  - subject: 主旨的段落編號 (必填)', how_to_cite, '  - quote: 引文的段落編號 (必填)', how_to_cite,
            '  - date: 那一天 YYYY-MM-DD;沒有就留空', '  - todo: 對方要求本人完成而且尚未完成的事;沒有就留空',
            '  - reason: 拒絕理由的段落編號;沒有就留空', how_to_cite, '- unreadable: 你讀不到的來源與原因',
            '  - source: 來源名稱(照材料寫) (必填)', '  - reason: 你讀不到的原因 (必填)',
            '- website_notes: 這一輪實際看到、下次有用的網站筆記', '  - url: 來源網址 (必填)', '  - note: 下次有用的一句 (必填)',
            '- needs_user: 需要使用者本人處理的事;沒有就空',
            '- needs_user_tab: 要你本人處理的那一頁的分頁(工作區編號:Page 標籤);沒有就空',
            '- needs_user_quote: 那一頁上要本人處理的原文,一字不差照抄;沒有就空', '- stopped: 沒做完就停下:停在哪、為什麼')))
        expect(self, 'P-01', browser.fetches, [])
        expect(self, 'P-02', (browser.reads, browser.timeouts, browser.resumed),     # 第二回合接同一個 agent
               (1, [progress.TIMEOUT] * 2, [(None, None), ('fake-session', 'fake-agent')]))
        import evidence
        root = evidence.root(self.home.board)
        recorded = {}
        for folder, _dirs, names in os.walk(root):          # 證據夾照流程分
            for name in (name for name in names if name.startswith('instruction')):
                recorded.setdefault(os.path.relpath(folder, root).split(os.sep)[0], []).append(
                    open(os.path.join(folder, name), encoding='utf-8').read())
        expect(self, 'A-32', ({key: sorted(texts) for key, texts in recorded.items()}, root.startswith(self.home.home)),
               ({'_progress': sorted(browser.prompts)}, False))


if __name__ == '__main__':
    unittest.main()
