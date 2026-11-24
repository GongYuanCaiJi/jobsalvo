"""同步平台履歷的驗收:從 sync.py 入口加假瀏覽器邊界跑 acceptance_inputs 的組合,斷言行為清單 A、S 段。

#601 的三種錯都不准發生:頁上的變動清單漏列(A-08、A-14)、標記標錯(A-04、A-27、S-05–S-07)、
使用者沒按「看過了」就顯示跟上(S-11–S-13)。句子照 tools/web/marks.py 的 TEMPLATES 一字不差。
"""
import datetime
import itertools
import json
import os
import shutil
import unittest
from collections import Counter

from hypothesis import example, given, settings

import acceptance_expect as E
import acceptance_inputs as I
from acceptance_home import LANG, PLATFORM, PROFILE_URL, Home
import fake_browser
from fake_browser import OPEN_FAIL, FakeBrowser, Script, Tab, shown
import agent_report
import evidence
import form_record
import profile_sync
import sync
import sync_material

VARIANT = 'one'
HOST = PROFILE_URL.split('/')[2]
SLOT = f'{LANG}/{VARIANT}'
OLD, NEW = '舊的一行', '新的一行'
PAGE_CODES = {'PAGE_UNREADABLE', 'PAGE_TOO_LONG', 'PAGE_NO_TEXT', 'SHOT_MISSING'}
HANDOVER_CODES = {'HANDED_OVER', 'HANDED_QUOTE_UNMATCHED', 'HANDOFF_UNVERIFIED'}
NO_CAPTURE = {'before': '沒有拍改前', 'after': '沒有拍改後，沒比到改後'}
ON_PAGE = '原稿在頁上一字不差讀到，那一頁沒逐處比'
RESTORE_REFUSED = {'stale_version': '同步清單已更新,請重新載入',
                   'unknown_change': '恢復清單已更新,請重新載入後選取',
                   'unregistered': '平台履歷未登記', 'evidence_gone': '恢復清單已更新,請重新載入後選取'}
EXAMPLES = int(os.environ.get('JOBSALVO_ACCEPT_EXAMPLES', 200))


def expect(test, behavior, actual, wanted, case=()):
    test.assertEqual(actual, wanted, f'{behavior} {case}')


def codes(marks):
    return Counter(mark['code'] for mark in marks)


def rows_of(visual):
    """看板畫一列用到的:那一頁、逐處比了沒、原因(跟清單上那個標記同一句)、兩邊的分頁與網址(標題)、兩頁自己的標記。"""
    said = {(m['code'], m['text']) for m in visual['marks']}
    return Counter((row['page_tab_id'], row['page_url'], row['compared'], row.get('reason_code') or '',
                    (row['reason_code'], row['reason']) in said if 'reason_code' in row else row['reason'] == '',
                    (row['before_tab_id'], row['before_url'], row['after_tab_id'], row['after_url'],
                     tuple(sorted({m['code'] for m in row['marks']}))))
                   for row in visual['pages_compared'])


def expected_rows(before, after):
    def pages(tabs):
        return tabs if isinstance(tabs, (tuple, list)) else ()

    old = {(tab.tab_id, tab.url): tab for tab in pages(before)}
    new = {(tab.tab_id, tab.url): tab for tab in pages(after)}

    def page_mark(tab):
        if tab is None:
            return ()
        return E.page_marks(tab)

    rows = []
    for _side, tab_id, url, compared, code, sides in E.pairing(before, after):
        shown = {}
        for tabs, page_id, page_url in ((old, sides[0], sides[1]), (new, sides[2], sides[3])):
            for mark in page_mark(tabs.get((page_id, page_url))):
                shown.setdefault(page_url, set()).add(mark)
        rows.append((tab_id, url, compared, code, True,
                     (*sides[:-1], tuple(sorted({mark for marks in shown.values() for mark in marks})))))
    return Counter(rows)


def changes_by_tab(visual):
    out = {}
    for row in visual['page_changes']:
        removed, added = out.setdefault(row['page_tab_id'], (Counter(), Counter()))
        removed.update(row['before_parts'])
        added.update(row['after_parts'])
    return {tab: pair for tab, pair in out.items() if any(pair)}


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.home = Home(self, resume=f'# 經歷\n{NEW}\n')

    def register(self, kind='page'):
        path = os.path.join(self.home.home, 'profiles.json')
        if os.path.exists(path):
            os.remove(path)
        if kind != 'none':
            profile_sync.remember(PLATFORM, LANG, VARIANT, PROFILE_URL)
        if kind == 'file':
            profile_sync.set_kind(PLATFORM, SLOT, 'file')

    def run_sync(self, *scripts, argv=None, can_open=True, browser=None):
        browser = browser or FakeBrowser(*scripts, can_open=can_open)
        argv = ['--platform', PLATFORM, '--lang', LANG, '--variant', VARIANT] if argv is None else argv
        code = sync.main(['--board', self.home.board, *argv], browser=browser)
        return code, self.visual(), browser

    def visual(self):
        entry = (self.home.registry().get(PLATFORM) or {}).get(SLOT) or {}
        visuals = entry.get('sync_visuals') or []
        return visuals[-1] if visuals else None

    def status(self):
        return next(row for row in profile_sync.status(self.home.board) if row['platform'] == PLATFORM)

    def press_reviewed(self):
        return profile_sync.mark_sync_reviewed(PLATFORM, LANG, VARIANT, self.status()['sync_review']['version'])

    def evidence_dir(self, visual):
        return evidence.flow_round_dir('sync', visual['evidence'])

    def forget(self):
        """同一個測試跑好幾種情況:前一種留下的回報與連續輪數清掉,每一種都從第一輪算。"""
        import board_doc
        board_doc.set_fb(lambda fb: [fb.pop(key, None) for key in (agent_report.KEY, agent_report.RETRY)],
                         live=self.home.board)

    def assert_sources(self, visual, case):
        for mark in [*visual['marks'], *visual['list_marks'],
                     *(mark for row in visual['pages_compared'] for mark in row['marks'])]:
            expect(self, 'A-27', mark['source'], E.source_of(mark['code']), (case, mark['code']))


class PairingTest(SyncCase):
    """A-04、A-06、A-08–A-12、A-14、A-27:每一種配對關係 × 兩邊每一種讀取狀態 × 截圖成不成功。"""

    def test_every_page_is_listed_with_its_true_relation_and_marks(self):
        self.register()
        for case in itertools.product(I.RELATIONS, I.TAB_STATES, I.TAB_STATES, I.SHOTS, I.SHOTS):
            before, after = E.relation_tabs(*case, before_parts=('甲', '共同'), after_parts=('共同', '乙'))
            _code, visual, browser = self.run_sync(Script((('before', before), ('after', after)), sheet={}))
            expect(self, 'A-08 A-09 A-10 A-11', rows_of(visual), expected_rows(before, after), case)
            expect(self, 'A-02', [[text for _n, text in printed.parts] for printed in browser.printed],
                   # 超過讀取上限的頁讀頁那一側不給內容,不截短冒充完整(A-04)
                   [[part for tab in tabs if tab.state == I.READ for part in tab.parts]
                    for tabs in (before, after)], case)
            expect(self, 'A-07', (browser.fetches, browser.reads), ([], 2), case)
            expect(self, 'A-42', browser.tabs, after, case)
            compared = [m for m in visual['marks'] if m['code'] == 'PAGES_COMPARED']
            expect(self, 'A-17', [m['params']['count'] for m in compared],     # 句子參數是字(marks.say)
                   [str(sum(1 for r in E.pairing(before, after) if r[0] == 'pair'))], case)
            expect(self, 'A-24', ('改後那一刻讀到的頁面' in json.dumps(visual, ensure_ascii=False),
                                  '已同步' in json.dumps(visual, ensure_ascii=False)), (True, False), case)
            wanted = Counter(code for tab in (*before, *after) for code in E.page_marks(tab))
            page_marks = Counter({code: n for code, n in codes(visual['marks']).items() if code in PAGE_CODES})
            expect(self, 'A-04 A-06', page_marks, wanted, case)
            pairs = [row for row in E.pairing(before, after) if row[0] == 'pair']
            expect(self, 'A-14', changes_by_tab(visual),
                   {tab_id: (Counter({'甲': 1}), Counter({'乙': 1})) for _s, tab_id, _u, _c, _r, _p in pairs}, case)
            urls = {tab_id: url for _s, tab_id, url, _c, _r, _p in pairs}
            folder = self.evidence_dir(visual)
            for row in visual['page_changes']:
                shown = {key: row.pop(key) for key in ('change_id', 'before_shot', 'after_shot',
                                                       'before_title', 'after_title')}
                expect(self, 'A-14', (row, bool(shown['change_id']),
                                      [os.path.isfile(os.path.join(folder, shown[side])) for side in ('before_shot', 'after_shot')]),
                       ({'page_tab_id': row['page_tab_id'], 'page_url': urls[row['page_tab_id']], 'before_parts': ['甲'],
                         'after_parts': ['乙'], 'before_text': '甲', 'after_text': '乙', 'item': '頁面段落',
                         'verdicts': ['頁面內容有變動'], 'restorable': True}, True, [before[0].shot, after[0].shot]), case)
            for row in visual['pages_compared']:      # 沒逐處比的那一頁也附程式拍到的截圖,不叫他自己去找(#783)
                if row['compared']:
                    continue
                for side, tabs in (('before', before), ('after', after)):
                    tab = next((t for t in tabs if (t.tab_id, t.url) == (row[side + '_tab_id'], row[side + '_url'])), None)
                    shot = row.get(side + '_shot', '')
                    expect(self, '#783', bool(shot) and os.path.isfile(os.path.join(folder, os.path.basename(shot))),
                           bool(tab and 'SHOT_MISSING' not in E.page_marks(tab)), (case, side))
            self.assert_sources(visual, case)

    def test_several_pages_each_keep_every_relation(self):
        self.register()
        u = 'https://jobs.example/profile/'
        before = (Tab('p1', u + '1', parts=('甲',)), Tab('p2', u + '2', parts=('甲',)),
                  Tab('p3', u + '3', I.UNREADABLE, ('甲',)), Tab('p5', u + '3', parts=('甲',)))
        after = (Tab('p1', u + '1', parts=('乙',)), Tab('p3', u + '3', I.UNREADABLE, ('乙',)),
                 Tab('p4', u + '1', parts=('乙',)), Tab('p2', u + '5', parts=('乙',)))
        _code, visual, _b = self.run_sync(Script((('before', before), ('after', after)), sheet={}))
        expect(self, 'A-08 A-09 A-10 A-11', rows_of(visual), expected_rows(before, after))
        expect(self, 'A-12', [(row['page_tab_id'], row['reason']) for row in visual['pages_compared']
                              if row.get('reason_code') == 'SIDE_UNREADABLE'],
               [(f'{fake_browser.WORKSPACE}:p3', '有一邊讀不到，沒逐處比')] * 2)
        for side, moments in (('改前', (None, after)), ('改後', (before, None))):
            _code, visual, _b = self.run_sync(Script((('before', moments[0]), ('after', moments[1])), sheet={}))
            expect(self, 'A-05', [row['reason'] for row in visual['pages_compared'] if row.get('reason_code') == 'WORKSPACE_UNREAD'],
                   [f'{side}整個工作區沒比到'], side)


class MomentTest(SyncCase):
    """A-03、A-05、A-15、A-19:叫了幾次拍改前、拍改後,每一次那一刻工作區的樣子。"""

    def tabs(self, moment, parts):
        return {'tabs': (Tab('p1', PROFILE_URL, parts=parts),), 'empty': I.WORKSPACE_EMPTY,
                'unread': I.WORKSPACE_UNREAD}[moment]

    def test_first_before_and_last_after_count_and_every_call_is_recorded(self):
        self.register()
        sequences = [seq for n in I.CALLS for seq in itertools.product(I.MOMENTS, repeat=n)]
        for befores, afters in itertools.product(sequences, sequences):
            case = (befores, afters)
            events = tuple(('before', self.tabs(m, (f'前{n}',))) for n, m in enumerate(befores))
            events += tuple(('after', self.tabs(m, (f'後{n}',))) for n, m in enumerate(afters))
            _code, visual, _browser = self.run_sync(Script(events, sheet={}))
            expect(self, 'A-19', visual['capture_status'], {'before': '已拍改前' if befores else NO_CAPTURE['before'],
                                                          'after': '已拍改後' if afters else NO_CAPTURE['after']}, case)
            first = self.tabs(befores[0], ('前0',)) if befores else 'none'
            last = self.tabs(afters[-1], (f'後{len(afters) - 1}',)) if afters else 'none'
            expect(self, 'A-15', rows_of(visual), expected_rows(first, last), case)
            expect(self, 'A-05', codes(visual['list_marks'])['WORKSPACE_EMPTY'],
                   int(first == I.WORKSPACE_EMPTY) + int(last == I.WORKSPACE_EMPTY), case)
            folder = self.evidence_dir(visual)
            expect(self, 'A-03', len([name for name in os.listdir(folder) if name.startswith('snapshot-')]),
                   len(befores) + len(afters), case)
            self.assert_sources(visual, case)


class EndTest(SyncCase):
    """A-16、A-18、A-20、A-21、A-28、A-35、S-21、S-24:一輪怎麼結束、停下、要本人處理、拍改前之前的步驟。"""

    ENDED = {I.HANDED: '交件', I.NO_SHEET: '沒交件', I.BROKEN_SHEET: '交件', I.TIMEOUT: '逾時',
             I.CRASHED: '當掉', I.STOPPED_BY_USER: '被停止'}
    HAND = {'none': (), 'quote_on_page': ('HANDED_OVER',), 'quote_not_on_page': ('HANDED_QUOTE_UNMATCHED',),
            'tab_gone': ('HANDOFF_UNVERIFIED',)}

    def test_every_round_end_produces_a_list_that_says_how_it_ended(self):
        self.register()
        page = Tab('p1', PROFILE_URL, parts=('請輸入驗證碼', '其他'))
        for case in itertools.product(I.ENDS, I.STOPS, I.NEEDS_USER, I.STEPS_BEFORE_FIRST, I.STEP_SHOTS):
            end, stop, needs, early_step, step_shot = case
            self.forget()
            sheet = {'stopped': '卡在驗證碼' if stop else ''}
            if needs != 'none':
                sheet.update(needs_user='要你輸入驗證碼', needs_user_tab='7:p9' if needs == 'tab_gone' else page.tab_id,
                             needs_user_quote='頁上沒有這句' if needs == 'quote_not_on_page' else '請輸入驗證碼')
            events = ((('open', (page,)), ('step', '打開設定頁', step_shot)) if early_step else ()) + (
                ('before', (page,)), ('step', '改經歷', step_shot), ('after', (page,)))
            _code, visual, browser = self.run_sync(Script(events, sheet=sheet, end=end))
            status = self.home.status_file('sync')
            expect(self, 'S-24', status['phase'], 'done', case)
            if end == I.UNAVAILABLE:
                folder = self.evidence_dir(visual)
                with open(os.path.join(folder, 'events.jsonl'), encoding='utf-8') as source:
                    events = [json.loads(line) for line in source]
                expect(self, 'A-35', (any(row['msg'] == '這一輪沒有啟動' for row in status['results']),
                                      any(row.get('kind') == 'round_end' and '假瀏覽器:沒有可用的 agent'
                                          in row.get('reason', '') for row in events)), (True, True), case)
                continue
            marks = codes(visual['list_marks'])
            expect(self, 'A-16', [m['text'] for m in visual['list_marks'] if m['code'] == 'ENDED'],
                   [f'這一輪{self.ENDED[end]}'], case)
            expect(self, 'A-16', marks['SHEET_BROKEN'], int(end == I.BROKEN_SHEET), case)
            handed = end == I.HANDED
            expect(self, 'A-21', marks['AGENT_STOPPED'], int(handed and stop), case)
            expect(self, 'A-28', marks['NEEDS_USER'], int(handed and needs != 'none'), case)
            want = self.HAND[needs] if handed else ()
            expect(self, 'A-28', sorted(c for c in marks if c.startswith('HAND')), sorted(want), case)
            expect(self, 'S-21', browser.handed, [page.tab_id] if want and want[0] != 'HANDOFF_UNVERIFIED' else [],
                   case)
            expect(self, 'A-18', codes(visual['marks'])['STEPS_BEFORE_FIRST'], int(early_step), case)
            # 只有交接成立(引句核對到)才當輪進要你做;其他沒做完的照實留在清單上,下次按全部同步重做(#807)
            expect(self, '#807', [(r['msg'].splitlines()[0], '要你輸入驗證碼' in r['msg'], r.get('url'))
                                  for r in agent_report.todo(self.home.fb())],
                   [(f'{HOST} 要你本人處理：', True, PROFILE_URL)] if want == ('HANDED_OVER',) else [], case)
            steps = visual['agent_steps']
            expect(self, 'A-20', [s['action'] for s in steps], (['打開設定頁'] if early_step else []) + ['改經歷'], case)
            for step in steps:
                shots = [(bool(shot['shot']), bool(shot['screenshot_error'])) for shot in step['screenshots']]
                expect(self, 'A-20', shots, [(step_shot, not step_shot)], case)
            self.assert_sources(visual, case)


    def test_each_step_says_where_it_falls_and_extra_words_are_listed(self):
        self.register()
        page = Tab('p1', PROFILE_URL, parts=('甲',))
        sheet = {'stopped': '', 'needs_user': '要你看', 'needs_user_tab': page.tab_id, 'needs_user_quote': '甲', 'note': '順便說'}
        between = '拍改前到拍改後之間'
        runs = {   # 兩次拍改後:以最後一次為界;沒拍改後:拍改前之後的步驟另一種說法
            'two afters': ((('open', (page,)), ('step', '打開設定頁', True), ('step', '點經歷', True), ('before', (page,)),
                            ('step', '改經歷', True), ('after', (page,)), ('step', '再看一次', True), ('after', (page,)),
                            ('step', '關掉', True)),
                           [('打開設定頁', '拍改前之前', False), ('點經歷', '拍改前之前', False), ('改經歷', between, True),
                            ('再看一次', between, True), ('關掉', '拍改後之後', False)],
                           ['agent 說：打開設定頁；點經歷']),
            'no after': ((('before', (page,)), ('step', '改經歷', True)), [('改經歷', '拍改前之後、拍改後之前', False)], [])}
        for case, (events, steps, early) in runs.items():
            _code, visual, _b = self.run_sync(Script(events, sheet=sheet))
            expect(self, 'A-18 A-20', ([(s['action'], s['phase'], s['compared']) for s in visual['agent_steps']],
                                       [m['text'] for m in visual['marks'] if m['code'] == 'STEPS_BEFORE_FIRST']),
                   (steps, early), case)
            # 要他處理的那幾格另有標記,不再當 agent 說的列一次;交件單多寫的欄照原字列
            expect(self, 'A-28 A-30', [m['text'] for m in visual['list_marks'] if m['code'] == 'AGENT_WORDS'],
                   ['agent 說：順便說'], case)


class SheetTest(SyncCase):
    """A-22、A-30:交件單一格寫成什麼樣子都不中斷,那一格照 agent 原字列;說的改動對照程式讀到的。"""

    VALUES = {'list': ['甲', {'欄': ['乙', None, '', []]}],
              'object': {'哪裡': '丙', '裡面': [{'子項': '丁'}, 8], '空': []},
              'number': 7, 'true': True}
    CLAIMS = {'match': ((OLD,), (NEW,), OLD, NEW), 'mismatch': ((OLD,), (NEW,), OLD, '別的字'),
              'unchecked': ((OLD,), None, OLD, NEW)}

    def test_any_cell_shape_is_listed_as_the_agent_wrote_it(self):
        self.register()
        for field, shape, claim in itertools.product(('changes', 'stopped', 'registered', 'rewrites'),
                                                     I.CELL_SHAPES, self.CLAIMS):
            case = (field, shape, claim)
            before_parts, after_parts, said_before, said_after = self.CLAIMS[claim]
            sheet = {'changes': [{'item': '經歷', 'before': said_before, 'after': said_after}]}
            if shape == 'missing':
                sheet.pop(field, None)
            elif shape == 'null':
                sheet[field] = None
            elif shape != 'words':
                sheet[field] = self.VALUES[shape]
            after = (('after', (Tab('p1', PROFILE_URL, parts=after_parts),)),) if after_parts else (('after', ()),)
            events = (('before', (Tab('p1', PROFILE_URL, parts=before_parts),)),) + after
            _code, visual, _b = self.run_sync(Script(events, sheet=sheet))
            marks = codes(visual['list_marks'])
            expect(self, 'A-16', marks['ENDED'], 1, case)
            if field == 'registered' and shape in self.VALUES:
                words = [m['params'].get('words', '') for m in visual['list_marks'] if m['code'] == 'AGENT_WORDS']
                expected = {'list': ['agent 說：甲', 'agent 說：乙'],
                            'object': ['丙', '丁', '8'], 'number': ['7'], 'true': ['true']}[shape]
                found = ' '.join(words)
                expect(self, 'A-30', all(word.removeprefix('agent 說：') in found for word in expected), True, case)
                if shape in ('list', 'object'):
                    expect(self, 'A-30', json.dumps(self.VALUES[shape], ensure_ascii=False) in found, False, case)
            claims = Counter({c: n for c, n in marks.items() if c.startswith('CLAIM_')})
            if field == 'changes' and shape != 'words':
                expect(self, 'A-22', claims, Counter(), case)
                continue
            want = {'match': 'CLAIM_MATCH', 'mismatch': 'CLAIM_MISMATCH', 'unchecked': 'CLAIM_UNCHECKED'}[claim]
            expect(self, 'A-22', claims, Counter({want: 1}), case)
            if claim == 'mismatch':
                text = next(m['text'] for m in visual['list_marks'] if m['code'] == 'CLAIM_MISMATCH')
                expect(self, 'A-22', (f'agent 說「{OLD} → 別的字」' in text,
                                      f'程式讀到「{OLD} → {NEW}」' in text), (True, True), case)
            self.assert_sources(visual, case)


    def test_each_said_change_is_checked_against_what_changed(self):
        self.register()
        said = [('甲', '丁'), ('', '戊'), ('丙', ''), ('乙', '丁'), ('', '')]    # 兩邊都對、只寫改後、只寫改前、改前寫錯、都沒寫
        sheet = {'changes': [{'item': '經歷', 'before': before, 'after': after} for before, after in said]}
        events = (('before', (Tab('p1', PROFILE_URL, parts=('甲', '乙', '丙')),)),
                  ('after', (Tab('p1', PROFILE_URL, parts=('丁', '乙', '戊')),)))
        _code, visual, _b = self.run_sync(Script(events, sheet=sheet))
        match = 'agent 說的改動與程式讀到的相符'
        expect(self, 'A-22', [m['text'] for m in visual['list_marks'] if m['code'].startswith('CLAIM_')],
               [match, match, match, 'agent 說「乙 → 丁」，程式讀到「甲、丙 → 丁、戊」',
                'agent 說「沒填改前或改後文字」，程式讀到「甲、丙 → 丁、戊」'])

    def test_second_round_claim_with_no_new_change_names_what_the_program_read(self):
        """A-22：第二輪頁面已是前輪改後內容時，不把空差異寫成空箭頭。"""
        self.register()
        changed = (Tab('p1', PROFILE_URL, parts=('舊內容',)),)
        updated = (Tab('p1', PROFILE_URL, parts=('Email：新的聯絡方式',)),)
        self.run_sync(Script((('before', changed), ('after', updated)), sheet={}))

        sheet = {'changes': [{'item': '聯絡方式', 'before': '', 'after': 'Email：新的聯絡方式'}]}
        _code, visual, _browser = self.run_sync(
            Script((('before', updated), ('after', updated)), sheet=sheet))
        claim = next(mark for mark in visual['list_marks'] if mark['code'] == 'CLAIM_MISMATCH')
        self.assertEqual(claim['text'],
                         'agent 說「沒填改前，改後是 Email：新的聯絡方式」，程式讀到「這一處沒有改動」')
        self.assertNotIn(' → ', claim['text'])


class SavedTest(SyncCase):
    """A-23:agent 說它有沒有存好、怎麼確認的,照列並標 agent 說的。"""

    def test_agent_saved_words_are_listed_as_its_words(self):
        self.register()
        said = '按了儲存,頁上出現「已更新」'
        _code, visual, _b = self.run_sync(Script((('before', ()), ('after', ())), sheet={'saved': said}))
        rows = [m for m in visual['list_marks'] if said in m['text']]
        expect(self, 'A-23', [m['source'] for m in rows], [E.AGENT])


class StandingTest(SyncCase):
    """S-01、S-04、S-10–S-14:跟上只在使用者按了最新一版的「看過了,沒問題」之後;沒按就照跟上的那一版全部再送。"""

    PAGE = Tab('p1', PROFILE_URL, parts=(NEW,))
    ROUNDS = {'handed': (('before', (PAGE,)), ('after', (PAGE,))), 'stopped': (('before', (PAGE,)),),
              'crashed': (('before', (PAGE,)),)}
    TEXT = {'unchanged': f'# 經歷\n{NEW}\n', 'text': f'# 經歷\n{NEW}\n# 技能\n再加一行\n',
            'comment_only': f'# 經歷\n{NEW}\n<!-- 註解 -->\n'}

    def script(self, kind):
        return Script(self.ROUNDS[kind], sheet={'stopped': '卡住'} if kind == 'stopped' else {},
                      end=I.CRASHED if kind == 'crashed' else I.HANDED)

    def test_caught_up_only_after_the_user_reviews_the_latest_list(self):
        for case in itertools.product(self.ROUNDS, I.REVIEW, I.SOURCE, I.REVIEW):
            kind, review_first, source, review_second = case
            self.register()
            self.home.write_resume(self.TEXT['unchanged'])
            expect(self, 'S-14', self.status()['state'], 'unchecked', case)
            _c, first, _b = self.run_sync(self.script(kind))
            expect(self, 'S-10', first is not None, True, case)
            expect(self, 'S-13', self.status()['state'], 'master-changed', case)
            if review_first:
                self.press_reviewed()
                expect(self, 'S-11' if kind == 'handed' else 'S-12', self.status()['state'], 'ok', case)
            self.home.write_resume(self.TEXT[source])
            behind = not review_first or source != 'unchanged'
            expect(self, 'S-11', self.status()['state'], 'master-changed' if behind else 'ok', case)
            _c, second, browser = self.run_sync(self.script(kind), argv=[])
            if not behind:
                expect(self, 'S-01', browser.prompts, [], case)
                expect(self, 'S-24', self.home.status_file('sync')['phase'], 'nothing', case)
                continue
            if source == 'comment_only' and review_first:
                expect(self, 'S-04', browser.prompts, [], case)
                expect(self, 'S-04', [m['text'] for m in second['list_marks'] if m['code'] == 'NOT_STARTED'],
                       ['這一輪沒有啟動'], case)
                expect(self, 'S-04', (codes(second['list_marks'])['SHEET_BROKEN'], second['capture_status']),
                       (0, NO_CAPTURE), case)
            else:
                prompt = browser.prompts[-1]
                expect(self, 'S-11', ('再加一行' in prompt, NEW in prompt), (source == 'text', not review_first), case)
            expect(self, 'S-13', self.status()['state'], 'master-changed', case)
            if review_second:
                self.press_reviewed()
                expect(self, 'S-12', self.status()['state'], 'ok', case)


class IntendedTest(SyncCase):
    """S-05–S-07:原稿這次改的字、刪掉的字,在改後那一刻的頁上讀不讀得到。"""

    def test_intended_text_marks_follow_what_the_program_read(self):
        for where, removed in itertools.product(I.INTENDED_WHERE, I.REMOVED_WHERE):
            case = (where, removed)
            self.register()
            self.home.write_resume(f'# 經歷\n{OLD}\n# 技能\n要刪的一行\n')
            self.run_sync(Script((('before', ()), ('after', ())), sheet={}))
            self.press_reviewed()
            self.home.write_resume(f'# 經歷\n{NEW}\n')
            before_parts = (('要刪的一行',) * (removed != 'never') + ((NEW,) if where == 'already' else ())) or ('別段',)
            after_parts = ((NEW,) if where in ('added', 'already') else ()) + (
                ('要刪的一行',) if removed == 'still' else ()) or ('別段',)
            before = ((Tab('p3', 'https://jobs.example/old', parts=before_parts),) if removed == 'unpaired'
                      else (Tab('p1', PROFILE_URL, parts=before_parts),))
            after = (Tab('p1', PROFILE_URL, parts=after_parts),)
            if where == 'unpaired_read':
                after += (Tab('p2', 'https://jobs.example/other', parts=(NEW,)),)
            if where == 'unpaired_unread':
                after += (Tab('p2', 'https://jobs.example/other', state=I.UNREADABLE),)
            _code, visual, _b = self.run_sync(Script((('before', before), ('after', after)), sheet={}))
            marks = {row['item']: row['marker'] for row in visual['intended_changes']}
            paired = removed != 'unpaired'
            want = {'added': '原稿一字不差找到' if paired else ON_PAGE,
                    'already': '原本就有' if paired else ON_PAGE,
                    'unpaired_read': ON_PAGE, 'unpaired_unread': '沒比到',
                    'nowhere': '原稿沒在頁上一字不差找到'}[where]
            expect(self, 'S-05' if where in ('added', 'already', 'nowhere') else 'S-06',
                   marks[f'{VARIANT}: 經歷[0]'], want, case)
            after_read = '讀得到' if removed == 'still' else '沒比到' if where == 'unpaired_unread' else '沒讀到'
            gone = ('原稿沒在頁上一字不差找到' if removed == 'never' else
                    f'刪除內容改前讀得到、改後{after_read}')
            expect(self, 'S-07', marks[f'{VARIANT}: 技能[1]'], gone, case)
            expect(self, 'S-05 S-07', sorted(visual['intended_changes'], key=lambda row: row['item']), [   # 看板畫的:改前改後原文、頁面標記
                {'item': f'{VARIANT}: 技能[1]', 'before': '要刪的一行', 'after': '',
                 'marker': gone,
                 **({'before_read': True, 'after_read': after_read == '讀得到'}
                    if removed != 'never' and after_read != '沒比到' else {})},
                {'item': f'{VARIANT}: 經歷[0]', 'before': OLD, 'after': NEW, 'marker': want}], case)


class CrossTabTest(SyncCase):
    """A-13:同一段字在別的分頁出現,不抵銷這一頁的消失;段落換位置就是這裡少了、那裡多了。"""

    def test_paragraphs_do_not_cancel_across_tabs(self):
        self.register()
        other = 'https://jobs.example/profile/2'
        before = (Tab('p1', PROFILE_URL, parts=('甲', '丙')), Tab('p2', other, parts=('乙',)))
        after = (Tab('p1', PROFILE_URL, parts=('乙', '丙')), Tab('p2', other, parts=('甲',)))
        _code, visual, _b = self.run_sync(Script((('before', before), ('after', after)), sheet={}))
        expect(self, 'A-13', changes_by_tab(visual), {'7:p1': (Counter({'甲': 1}), Counter({'乙': 1})),
                                                      '7:p2': (Counter({'乙': 1}), Counter({'甲': 1}))})
        moved = (Tab('p1', PROFILE_URL, parts=('甲', '丙')),), (Tab('p1', PROFILE_URL, parts=('丙', '甲')),)
        _code, visual, _b = self.run_sync(Script((('before', moved[0]), ('after', moved[1])), sheet={}))
        removed, added = changes_by_tab(visual)['7:p1']     # 換位置:這裡少了、那裡多了(哪一段算移動都對)
        expect(self, 'A-13', (E.diff_ok(('甲', '丙'), ('丙', '甲'), removed, added), removed == added,
                              sum(removed.values())), (True, True, 1))


class TextTest(SyncCase):
    """A-12、A-13、A-14、A-26:同一分頁的任何文字,消失的段與新出現的段一字不差、一段不漏。"""

    def test_listed_changes_equal_the_paragraph_difference(self):
        @settings(max_examples=EXAMPLES, deadline=None)
        @given(I.TEXT)
        @example((['甲', '乙', '甲'], ['甲', '甲', '甲']))         # 兩邊頭尾都一樣、中間換了一段
        @example((['甲', '甲', '甲'], ['甲', '乙', '甲']))
        @example((['甲', '乙', '丙', '丙'], ['丙', '丙', '甲']))   # 最長共同的是後面那兩段,不是開頭那一段
        def check(text):       # 每次呼叫一個新函式:同一行程跑兩次(mutmut)也不算不同執行者
            self.register()
            before_parts, after_parts = text

            def tab(parts):
                return Tab('p1', PROFILE_URL, parts=tuple(parts)) if parts else Tab('p1', PROFILE_URL, state=I.BLANK)
            _code, visual, _b = self.run_sync(Script((('before', (tab(before_parts),)),
                                                      ('after', (tab(after_parts),))), sheet={}))
            if not before_parts or not after_parts:
                expect(self, 'A-12', [row['reason_code'] for row in visual['pages_compared']],
                       ['SIDE_UNREADABLE', 'SIDE_UNREADABLE'], text)
                return
            removed, added = changes_by_tab(visual).get('7:p1', (Counter(), Counter()))
            expect(self, 'A-26', E.diff_ok(before_parts, after_parts, removed, added), True, text)
        check()


class AttachmentTest(SyncCase):
    """S-08:平台上的附件當成頁面段落一起比,換掉就有那一處,沒換就不列。"""

    def test_attachment_paragraph_changes_are_listed(self):
        for replaced in (False, True):
            self.register()
            before = (Tab('p1', PROFILE_URL, parts=('經歷', 'report.pdf')),)
            after = (Tab('p1', PROFILE_URL, parts=('經歷', 'report-new.pdf' if replaced else 'report.pdf')),)
            _code, visual, _b = self.run_sync(Script((('before', before), ('after', after)), sheet={}))
            wanted = {'7:p1': (Counter({'report.pdf': 1}), Counter({'report-new.pdf': 1}))} if replaced else {}
            expect(self, 'S-08', changes_by_tab(visual), wanted, replaced)


class RestoreTest(SyncCase):
    """S-15–S-20:恢復派 agent、這一輪自己的改前改後照配對規則比、不擋、不判成敗、要按看過才跟上。"""

    def test_restore_tells_the_agent_every_paragraph_to_put_back(self):
        self.register()
        self.run_sync(Script((('before', (Tab('p1', PROFILE_URL, parts=('甲', '乙', '丙')),)),
                              ('after', (Tab('p1', PROFILE_URL, parts=('丁', '戊', '丙')),))), sheet={}))
        payload = {'platform': PLATFORM, 'lang': LANG, 'variant': VARIANT, 'version': self.status()['sync_review']['version'],
                   'change_ids': [row['change_id'] for row in self.visual()['page_changes']]}
        _code, _restored, browser = self.run_sync(Script((('before', ()), ('after', ())), sheet={}),
                                                  argv=['--restore-json', json.dumps(payload)])
        expect(self, 'S-15', f'恢復 {PROFILE_URL}\n改前:丁\n戊\n改後:甲\n乙' in browser.prompts[0], True)

    def test_restore_round_lists_its_own_comparison(self):
        first_before = Tab('p1', PROFILE_URL, parts=(OLD, '別段'))
        first_after = Tab('p1', PROFILE_URL, parts=('別段',))
        outcomes = {'back': (Tab('p1', PROFILE_URL, parts=(OLD, '別段')),),
                    'elsewhere': (first_after, Tab('p2', 'https://jobs.example/x', parts=(OLD,))),
                    'unread': (Tab('p1', PROFILE_URL, state=I.UNREADABLE),), 'gone': ()}
        # 工作區空了是程式沒比到(A-05、S-06),不是每一頁都讀到了還沒有
        marker = {'back': '原稿一字不差找到', 'elsewhere': ON_PAGE, 'unread': '沒比到', 'gone': '沒比到'}
        # S-18:恢復那一輪開始時的頁跟清單那一版(first_after)一樣、或已經被改過
        starts = {'as_listed': first_after, 'changed': Tab('p1', PROFILE_URL, parts=('別段', '後來改的'))}
        for picks, outcome, start in itertools.product(I.RESTORE_PICKS, outcomes, starts):
            case = (picks, outcome, start)
            self.register()
            self.run_sync(Script((('before', (first_before,)), ('after', (first_after,))), sheet={}))
            version = self.status()['sync_review']['version']
            change_ids = [row['change_id'] for row in self.visual()['page_changes']]
            payload = {'platform': 'nope.example' if picks == 'unregistered' else PLATFORM, 'lang': LANG, 'variant': VARIANT,
                       'version': 'old' if picks == 'stale_version' else version,
                       'change_ids': ['nope'] if picks == 'unknown_change' else change_ids}
            if picks == 'evidence_gone':
                shutil.rmtree(evidence.flow_round_dir('sync', version))
            after = outcomes[outcome]
            _code, restored, browser = self.run_sync(
                Script((('before', (starts[start],)), ('after', after)),
                       sheet={'stopped': '卡在登入', 'needs_user': '要你登入', 'needs_user_tab': '7:p1',
                              'needs_user_quote': '別段'}),
                argv=['--restore-json', json.dumps(payload)])
            if picks != 'valid':
                expect(self, 'S-15', browser.prompts, [], case)
                expect(self, 'S-24', [(row['ok'], row['msg']) for row in self.home.status_file('sync')['results']],
                       [(False, RESTORE_REFUSED[picks])], case)
                continue
            expect(self, 'S-15 S-18', len(browser.prompts), 1, case)
            expect(self, 'S-15', (sync.RESTORE_GOAL in browser.prompts[0], restored['task']), (True, 'restore'), case)
            # 程式自己讀的只有兩次拍照和交接時重讀那一分頁(C-11)
            expect(self, 'S-18', (browser.fetches, browser.reads), ([], 3), case)
            marks = codes(restored['list_marks'])
            expect(self, 'S-19', ([m['text'] for m in restored['list_marks'] if m['code'] == 'ENDED'],
                                  marks['AGENT_STOPPED'], marks['NEEDS_USER']), (['這一輪交件'], 1, 1), case)
            # A-28:分頁在工作區就交;引文核對到標 HANDED_OVER,沒核對到標 HANDED_QUOTE_UNMATCHED;分頁不在才沒交接
            present = any(tab.label == 'p1' for tab in after)
            on_page = any('別段' in tab.parts and tab.label == 'p1' and tab.state == I.READ for tab in after)
            expect(self, 'S-19', (marks['HANDED_OVER'], marks['HANDED_QUOTE_UNMATCHED'], marks['HANDOFF_UNVERIFIED'],
                                  browser.handed),
                   (int(on_page), int(present and not on_page), int(not present), ['7:p1'] if present else []), case)
            expect(self, 'S-16', rows_of(restored), expected_rows((starts[start],), after), case)
            expect(self, 'S-17', [row['marker'] for row in restored['intended_changes']], [marker[outcome]], case)
            expect(self, 'S-20', self.status()['sync_review']['reviewed'], False, case)
            over = f'要你本人處理的這一頁「{PROFILE_URL}」已交給你；agent 說：別段'
            expect(self, 'S-19', [m['text'] for m in restored['list_marks'] if m['code'] in HANDOVER_CODES], [{
                'back': over, 'elsewhere': over,
                'unread': f'要你本人處理的這一頁「{PROFILE_URL}」已交給你；agent 說「別段」，那一頁讀不到',
                'gone': '要你本人處理的那一頁沒有交接：那一頁'}[outcome]], case)
            texts = ' '.join(m['text'] for m in restored['list_marks'])
            expect(self, 'S-20', ('已恢復' in texts, '沒做成' in texts), (False, False), case)


class HandOverTest(SyncCase):
    """S-19、A-28:要本人處理的那一頁交出去了但引文不在頁上、或交不出去,照實講。"""

    def test_quote_missing_or_handover_failing(self):
        self.register()
        tab = Tab('p1', PROFILE_URL, parts=('甲',))
        for fail, wanted in (('', f'要你本人處理的這一頁「{PROFILE_URL}」已交給你；agent 說「乙」，頁上沒有一字不差的這段字'),
                             ('ego 叫不到前景', '要你本人處理的那一頁沒有交接：那一頁')):
            browser = FakeBrowser(Script((('before', (tab,)), ('after', (tab,))),
                                         sheet={'needs_user': '要你登入', 'needs_user_tab': '7:p1', 'needs_user_quote': '乙'}))
            browser.hand_fail = fail
            _code, visual, _b = self.run_sync(browser=browser)
            expect(self, 'S-19 A-28', ([m['text'] for m in visual['list_marks'] if m['code'] in HANDOVER_CODES], browser.handed),
                   ([wanted], [] if fail else ['7:p1']), fail)


class AttentionTest(SyncCase):
    """#807 第 2、3 點:沒做完不進要你做,連續 3 輪沒做完才開一則寫出試了幾輪、卡在哪;被停止的那一輪不算;乾淨收尾就收掉。"""

    def round(self, sheet, end=I.HANDED):
        page = Tab('p1', PROFILE_URL, parts=('甲',))
        self.run_sync(Script((('before', (page,)), ('after', (page,))), sheet=sheet, end=end))
        return [(r['msg'].splitlines()[0], bool(r.get('need'))) for r in agent_report.todo(self.home.fb())]

    def test_three_unfinished_rounds_in_a_row_open_one_item(self):
        self.register()
        stuck = {'stopped': '卡在驗證碼'}
        head = f'{PLATFORM} {SLOT} 平台履歷同步連續 3 輪沒做完：'
        expect(self, '#807', [self.round(stuck) for _ in range(3)], [[], [], [(head, True)]])
        expect(self, '#807', 'agent 停下：卡在驗證碼' in agent_report.todo(self.home.fb())[0]['msg'], True)
        expect(self, '#807', self.round({}), [])

    def test_a_round_the_user_stopped_does_not_count(self):
        self.register()
        stuck = {'stopped': '卡在驗證碼'}
        rounds = [self.round(stuck), self.round(stuck), self.round(stuck, end=I.STOPPED_BY_USER), self.round(stuck)]
        expect(self, '#807', [len(r) for r in rounds], [0, 0, 0, 1])


class RegisterTest(SyncCase):
    """S-22、S-23:agent 登記的平台履歷標明,不改網域清單;使用者登記在別的主機上的,那個主機算這個平台。"""

    def test_agent_registration_is_labelled_and_leaves_hosts(self):
        for registered, user_other in itertools.product(I.REGISTERED_BY_AGENT, (False, True)):
            case = (registered, user_other)
            self.register()
            if user_other:
                profile_sync.remember(PLATFORM, LANG, 'two', 'https://user-host.example/p')
            hosts = self.home.registry()[PLATFORM]['hosts']
            url = {None: '', 'same_host': 'https://jobs.example/profile/9',
                   'other_host': 'https://agent-host.example/p'}[registered]
            sheet = {'registered': {'name': '英文履歷', 'url': url}} if url else {}
            self.run_sync(Script((('before', ()), ('after', ())), sheet=sheet))
            expect(self, 'S-23', [profile_sync.platform_of(f'https://{host}/q') == PLATFORM
                                  for host in ('user-host.example', 'www.user-host.example', 'xuser-host.example')],
                   [user_other, user_other, False], case)       # 子網域也算;只是字尾相同的別的主機不算
            expect(self, 'S-22', profile_sync.platform_of('https://agent-host.example/q'), None, case)
            expect(self, 'S-22', self.home.registry()[PLATFORM]['hosts'], hosts, case)
            entry = self.home.registry()[PLATFORM][SLOT]
            expect(self, 'S-22', (entry['read'], entry.get('by'), entry.get('name')),
                   (url, 'agent', '英文履歷') if url else (PROFILE_URL, None, None), case)
        profile_sync.remember('別的平台', LANG, 'one', 'https://user-host.example/x')     # 同一個主機兩個平台:不猜
        expect(self, 'S-23', profile_sync.platform_of('https://user-host.example/q'), None)


class RewritesTest(SyncCase):
    """S-09、S-14:agent 改寫了的內容列成待你確認的代填答案,設定頁說代填答案等你確認。"""

    def test_rewrites_wait_for_confirmation(self):
        self.register()
        sheet = {'rewrites': [{'original': NEW, 'rewritten': '新的', 'why': '平台限 2 字'}]}
        self.run_sync(Script((('before', ()), ('after', ())), sheet=sheet))
        fb, _shared = sync_material.shared_answers(self.home.board)
        waiting = [answer for answer, _users in form_record.waiting(fb)
                   if answer.get('platform') == PLATFORM and answer.get('profile') == SLOT]
        # 照常用答案規則:我推論的那一條標推論的日期(form_record._mark)
        expect(self, 'S-09', [(answer['v'], answer.get('inf')) for answer in waiting],
               [('新的', datetime.date.today().isoformat())])
        self.press_reviewed()
        expect(self, 'S-14', (self.status()['state'], self.status()['confirmation_pending']), ('ok', True))


class PromptTest(SyncCase):
    """S-02、S-03、A-32、A-35:目標與材料進指示、指示整段記進證據夾;沒登記、開不了工作區直接講原因。"""

    def test_brief_and_setup_problems(self):
        for registry, can_open, master in itertools.product(I.REGISTRY, (True, False), I.MASTER):
            case = (registry, can_open, master)
            self.register(registry)
            master_file = os.path.join(self.home.home, 'resume', 'one.md')
            if master == 'missing':
                os.replace(master_file, master_file + '.gone')
            _code, visual, browser = self.run_sync(Script((('before', ()),), sheet={}), can_open=can_open)
            messages = ' '.join(row['msg'] for row in self.home.status_file('sync')['results'])
            if master == 'missing':
                os.replace(master_file + '.gone', master_file)
            if registry == 'none':
                expect(self, 'A-35', ('平台履歷未登記' in messages, browser.prompts), (True, []), case)
                continue
            if master == 'missing':
                expect(self, 'A-35', ('這一輪沒完成' in messages, browser.prompts), (True, []), case)
                continue
            if not can_open:
                folder = self.evidence_dir(self.visual())
                with open(os.path.join(folder, 'events.jsonl'), encoding='utf-8') as source:
                    events = [json.loads(line) for line in source]
                expect(self, 'A-35', ('這一輪沒有啟動' in messages, browser.prompts,
                                      any(row.get('kind') == 'program_error' and OPEN_FAIL in row.get('reason', '')
                                          for row in events)), (True, [], True), case)
                continue
            prompt = browser.prompts[0]
            expect(self, 'S-02', (PROFILE_URL in prompt, NEW in prompt), (True, True), case)
            expect(self, 'S-03', browser.printed[0].parts, [], case)
            folder = self.evidence_dir(visual)
            recorded = ''.join(open(os.path.join(folder, name), encoding='utf-8').read()
                               for name in os.listdir(folder) if name.startswith('instruction'))
            expect(self, 'A-32', prompt in recorded, True, case)


class PrintedTest(SyncCase):
    """A-02、C-04:拍照工具印給 agent 的整段:階段、每一頁的狀態、讀不到與截圖沒取得的原因、編號段落;空工作區照講。"""

    def test_what_the_snap_command_prints_for_every_page_state(self):
        self.register()
        url = 'https://jobs.example/p/'
        tabs = (Tab('p1', PROFILE_URL, I.READ, ('甲',)), Tab('p2', url + '2', I.READ, ('乙',), shot=False),
                Tab('p3', url + '3', I.UNREADABLE), Tab('', url + '4', I.NO_LABEL), Tab('p5', url + '5', I.BLANK),
                Tab('p6', url + '6', I.TOO_LONG, ('丙',)))
        _code, visual, browser = self.run_sync(Script((('before', tabs), ('after', ())), sheet={}))
        before, after = browser.printed
        no_label = 'ego 沒給這個分頁標籤,無法指名讀取或截圖'
        expect(self, 'A-02 C-04', (before.text.splitlines(), after.text.splitlines()), ([
            f'before material_id={before.material_id}', f'[7:p1] {PROFILE_URL} (read)',
            f'[7:p2] {url}2 (read)', '截圖沒取得：假瀏覽器:截圖失敗',
            f'[7:p3] {url}3 (unreadable)', f'{shown("p3", url + "3")}讀取失敗：假瀏覽器:讀頁失敗',
            f'[7:] {url}4 (unreadable)', f'{shown("", url + "4")}讀取失敗：{no_label}', f'截圖沒取得：{no_label}',
            f'[7:p5] {url}5 (no_text)', f'{shown("p5", url + "5")}沒有可讀文字',
            f'[7:p6] {url}6 (too_long)', f'{shown("p6", url + "6")}的讀取材料超過 100000 字元上限', '1. 甲', '2. 乙'],
            [f'after material_id={after.material_id}', '工作區沒有可拍的分頁']))
        # 清單上每一頁自己的標記照證據夾存的那一刻寫(A-04、A-06)
        expect(self, 'A-04 A-06', [(m['code'], m['text']) for m in visual['marks'] if m['code'] in PAGE_CODES], [
            ('SHOT_MISSING', f'{shown("p2", url + "2")}沒有截圖'), ('PAGE_UNREADABLE', f'{shown("p3", url + "3")}讀不到'),
            ('PAGE_UNREADABLE', f'{shown("", url + "4")}讀不到'), ('SHOT_MISSING', f'{shown("", url + "4")}沒有截圖'),
            ('PAGE_NO_TEXT', f'{shown("p5", url + "5")}讀不出字'), ('PAGE_TOO_LONG', f'{shown("p6", url + "6")}太長沒讀完')])


class SnapCommandTest(unittest.TestCase):
    """I-13:agent 在指令列叫 jobsalvo-before / jobsalvo-after;拍不了就在錯誤輸出講原因,不丟一串程式錯誤。"""

    def run_command(self, command, env):
        import contextlib
        import io
        from unittest import mock
        import chrome_door
        from web import snap
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(chrome_door.EgoDoor, 'workspaces', return_value=[]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            for key in ('JOBSALVO_EGO_WORKSPACE', 'JOBSALVO_EGO_EVIDENCE_DIRS'):
                if key not in env:
                    os.environ.pop(key, None)
            code = snap.main([command])
        return code, out.getvalue(), err.getvalue()

    def test_what_the_agent_sees_when_it_cannot_shoot(self):
        dirs = json.dumps([self.id()])
        for command, env, wanted in (
                ('/bin/jobsalvo-before', {}, (0, '', '沒有可拍的工作區：缺少 JOBSALVO_EGO_WORKSPACE\n')),
                ('/bin/jobsalvo-after', {'JOBSALVO_EGO_WORKSPACE': '9', 'JOBSALVO_EGO_EVIDENCE_DIRS': dirs},
                 (1, '', '找不到工作區 9\n'))):
            expect(self, 'I-13', self.run_command(command, env), wanted, (command, env))


if __name__ == '__main__':
    unittest.main()
