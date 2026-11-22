"""找缺與貼網址加卡的驗收:從 find.py、add_job.py 入口加假瀏覽器邊界跑 acceptance_inputs 的組合,斷言行為清單 J 段。

找缺是搜尋 agent 交候選網址 → 程式讀每個網址(附截圖)→ 判斷 agent 照程式讀到的編號材料引段 → 成卡。
"""
import contextlib
import copy
import io
import itertools
import json
import os
import re
import unittest
from unittest import mock

import acceptance_expect as E
import acceptance_inputs as I
from acceptance_home import Home
from fake_browser import SHOT_FAIL, FakeBrowser, Script
import add_job
import config as cf
import delivery_state
import find
import page_fetch

URL = 'https://jobs.example/jobs/11'
URL2 = 'https://jobs.example/jobs/12'
TITLE, COMPANY = 'Backend Engineer', 'Acme Robotics'
PARTS = (TITLE, COMPANY, 'Posted 2026-11-01', '12 applicants', 'Apply by 2026-12-01')
FIELD_TEXT = {'title': TITLE, 'company': COMPANY, 'posted_at': '2026-11-01', 'applicants': '12', 'status': 'Apply by'}
UNREAD = '頁面讀不到，欄位由 agent 判斷'


def expect(test, behavior, actual, wanted, case=()):
    test.assertEqual(actual, wanted, f'{behavior} {case}')


def fetched(url, read, parts=PARTS):
    """raw_fetch 回的樣子(web.fetch.PageResult)。"""
    base = {'url': url, 'via': 'direct', 'title': '', 'errors': []}
    return {'ok': dict(base, status='ok', text='\n'.join(parts), http_status=200),
            'partial': dict(base, status='partial', text='\n'.join(parts), http_status=200, errors=['超過讀取上限']),
            'unknown': dict(base, status='unknown', text='', http_status=0, errors=['要登入']),
            'http_404': dict(base, status='unknown', text='', http_status=404, errors=['HTTP 404'])}[read]


def search(*urls, end=I.HANDED):
    return Script((), {'candidates': [{'url': u, 'why': '符合', 'via': 'search', 'angle': '後端'} for u in urls],
                       'notes': '往後端找', 'stopped': ''}, end)


def row(material, url, keep='true', cites='verified', **extra):
    """判斷交件的一列:每一欄照 cites 引(verified 引得到、not_on_page 引的段沒有那個值、missing 不寫)。"""
    out = {'url': url, 'keep': keep, 'score': '4', 'why': '對味', 'risk': 'none'}
    for name, text in FIELD_TEXT.items():
        if cites == 'verified':
            out[name] = material.cite(text, text=text)
        elif cites == 'not_on_page':
            out[name] = material.cite(PARTS[0], text=f'{text} 以外的字')
    out.update(extra)
    return out


def judge(rows=None, end=I.HANDED, **sheet):
    def build(printed):
        material = printed[0]
        return {'candidates': rows(material) if rows else [], 'terms_missed': [], 'stopped': '', **sheet}
    return Script((), build, end)


class FindCase(unittest.TestCase):
    maxDiff = None
    def setUp(self):
        self.home = Home(self)

    def configure(self, **search):
        def change(settings):
            settings.setdefault('search', {}).update(search)
            settings.setdefault('board', {})['company_alias'] = {'Acme': COMPANY}
        self.rewrite(change)

    def rewrite(self, change):
        """改一份設定(change 就地改)再寫回他的資料夾,跟他在設定頁存檔一樣。"""
        settings = copy.deepcopy(self.home.settings)
        change(settings)
        with open(os.path.join(self.home.home, cf.NAME), 'w', encoding='utf-8') as target:
            json.dump(settings, target, ensure_ascii=False)
        cf.reload(self.home.home)

    def go(self, *scripts, reads=None, shots_fail=(), argv=(), entry='find'):
        browser = FakeBrowser(*scripts, fetched={url: fetched(url, read) for url, read in (reads or {}).items()})
        browser.url_shot_fail = set(shots_fail)
        if entry == 'find':
            code = find.main(['--board', self.home.board, *(() if '--mode' in argv else ('--mode', 'both')), *argv],
                             browser=browser)     # 看板每次都帶找法
        else:
            code = add_job.main(['--board', self.home.board, *argv], browser=browser)
        return code, browser

    def card(self, url=URL):
        return self.home.jobs().get(url)

    def research(self):
        import board_doc
        return board_doc.load(self.home.board)['data'].get('research') or []


class ReadTest(FindCase):
    """J-02、J-03、J-08、J-09、J-10:每個網址照讀的結果標;讀不到照樣交 agent 判斷;截圖失敗不中斷。"""

    def test_every_read_state_and_screenshot(self):
        for read, shot, keep in itertools.product(I.URL_READ, I.SHOTS, I.KEEP):
            case = (read, shot, keep)
            self.home.write_board()
            _code, browser = self.go(search(URL), judge(lambda m, keep=keep: [row(m, URL, 'true' if keep == 'keep' else 'false')]),
                                     reads={URL: read}, shots_fail=() if shot else (URL,))
            expect(self, 'J-02', browser.fetches, [URL], case)
            card = self.card()
            expect(self, 'J-08' if read != 'ok' else 'J-13', card is not None, keep == 'keep', case)
            if card is None:
                continue
            verification = card['src']['verification']
            expect(self, 'J-09' if read == 'partial' else 'J-03', verification['page_status'],
                   {'ok': 'ok', 'partial': 'partial'}.get(read, 'unknown'), case)
            unread = read in ('unknown', 'http_404')
            marks = [m['text'] for m in verification.get('marks') or []]
            expect(self, 'J-03', any(text.startswith(UNREAD) for text in marks), unread, case)
            if read == 'http_404':
                expect(self, 'J-03', any('404' in text for text in marks), True, case)
            expect(self, 'J-10', (bool(verification.get('screenshot_file')), verification.get('screenshot_error')),
                   (shot, None if shot else f'OSError: {SHOT_FAIL}'), case)
            expect(self, 'J-09', card['title'], TITLE if read in ('ok', 'partial') else '', case)
            if read == 'ok':                       # 卡名照共用卡名規則(看板、填表同一個),找缺不另組字
                expect(self, 'J-04', card['target'], f'{TITLE} · {COMPANY}', case)


class FieldTest(FindCase):
    """J-04、J-05、J-06、J-07、A-27:每一欄引得到才用;引的段沒有那個值就並列;沒寫標 agent 說頁上沒寫;沒核對到不拿來決定。"""

    def test_values_that_did_not_check_out_stay_off_the_card(self):
        """應徵人數、截止日引的段沒有那個值:不記人數與截止日、條件標沒核對到;摘要照 agent 說的與核對原因列;
        同職稱不同公司的舊卡不算重貼。"""
        other = {'id': 'https://jobs.example/jobs/o', 'target': 'x', 'title': TITLE, 'company': 'Other Co'}
        self.home.write_board([other])
        self.configure(terms={'applicants_max': 50})
        parts = (*PARTS, 'NT$100k')
        rows = lambda m: [row(m, URL, salary=m.cite('NT$100k'), applicants=m.cite(TITLE, text='30'),  # noqa: E731
                              deadline=m.cite(TITLE, text='2026-12-01'))]
        find.main(['--board', self.home.board, '--mode', 'both'],
                  browser=FakeBrowser(search(URL), judge(rows), fetched={URL: fetched(URL, 'ok', parts)}))
        card = self.card()
        verification = card['src']['verification']
        bad = 'agent 引的段落無法核對：text 要寫頁面上那幾個字(字串),agent 寫的是 (沒寫)'
        expect(self, 'J-06', (card['sum'], 'applicants' in card, 'deadline' in card, 'repost_hint' in card['src'],
                              verification['marks'], verification['state']),
               ({'salary': bad, 'deadline': '2026-12-01'}, False, False, False,
                [{'code': 'TERM_UNCHECKED', 'source': E.PROGRAM, 'params': {'term': '應徵人數'}, 'text': '條件「應徵人數」沒有核對到'}],
                'review'))

    def test_a_cite_written_in_the_wrong_shape(self):
        """引段寫成字串、text 寫成數字:不當 agent 說的值,照核對結果標無法核對;引得到的段照列。"""
        self.configure(terms={'applicants_max': 50})
        self.go(search(URL), judge(lambda m: [row(m, URL, title=TITLE, applicants=m.cite('12 applicants', text=12))]),
                reads={URL: 'ok'})
        fields = self.card()['src']['verification']['fields']
        bad = 'agent 引的段落無法核對：'
        expect(self, 'A-29', [(fields[name]['claim'], fields[name]['raw'], fields[name]['state'],
                               [mark['text'] for mark in fields[name]['marks']]) for name in ('title', 'applicants')],
               [('', '', '沒核對到', [bad + '引用格式不是物件']),
                ('', '12 applicants', '沒核對到', [bad + 'text 要寫頁面上那幾個字(字串),agent 寫的是 12'])])

    def test_a_cite_of_several_paragraphs_keeps_each_line(self):
        self.home.write_board()

        def rows(m):
            t, c = m.cite(TITLE)['ids'][0], m.cite(COMPANY)['ids'][0]
            return [row(m, URL, title={'material_id': m.material_id, 'ids': [t, c], 'text': TITLE})]
        find.main(['--board', self.home.board, '--mode', 'both'],
                  browser=FakeBrowser(search(URL), judge(rows), fetched={URL: fetched(URL, 'ok', PARTS)}))
        title = self.card()['src']['verification']['fields']['title']
        expect(self, 'A-29', (title['claim'], title['raw'], title['state']), (TITLE, f'{TITLE}\n{COMPANY}', '程式核對過'))

    def test_numbers_that_point_at_the_wrong_paragraphs(self):
        """段落編號不是整數清單、超出範圍、重複、引別頁的段、引舊卡那一塊:那一欄沒核對到,原因逐條列(A-29、J-04)。"""
        old = {'id': 'https://jobs.example/jobs/o', 'target': 'Old Card', 'title': TITLE, 'company': 'Other Co'}
        self.home.write_board([old])
        url2 = 'https://jobs.example/jobs/2'
        long = 'Posted 2026-10-01 ' + 'remote friendly ' * 6           # 原因裡的段落超過 80 字就截斷

        def rows(m):
            n = m.cite(COMPANY)['ids'][0]
            return [row(m, URL, title={'material_id': m.material_id, 'ids': ['1'], 'text': TITLE},
                        company={'material_id': m.material_id, 'ids': [9999, n, n], 'text': COMPANY},
                        posted_at=m.cite('Posted 2026-10-01', text='2026-10-01'),
                        applicants=m.cite('jobs.example/jobs/o', text='Old Card'),
                        status=m.cite('不在頁上', text='Apply by'), loc=m.cite('Apply by', text=''))]
        find.main(['--board', self.home.board, '--mode', 'both'], browser=FakeBrowser(search(URL, url2), judge(rows), fetched={
            URL: fetched(URL, 'ok', PARTS), url2: fetched(url2, 'ok', ('Data Analyst', long))}))
        verification = self.card()['src']['verification']
        fields = dict(verification['fields'], loc=verification['summary']['fields']['loc'])
        n = PARTS.index(COMPANY) + 1
        last = len(PARTS) + 2 + 1
        bad = '段落編號無效:'
        expect(self, 'A-29', {name: [mark['params']['why'] for mark in fields[name]['marks']]
                              for name in ('title', 'company', 'posted_at', 'applicants', 'status', 'loc')},
               {'title': [f"{bad}要寫整數清單(沒有可引的寫 []),agent 寫的是 ['1']"],
                'company': [f'{bad}9999 不在這一次給的 1-{last} 裡; {bad}{n} 重複'],
                'posted_at': [f'{bad}第 {len(PARTS) + 2} 段是「{long[:80]}…」,屬於 {url2},這一格只收 {URL} 的'],
                'status': ['text 跟段落編號要一起寫或一起空:寫了「Apply by」,指了 []'],
                'loc': [f'指了 [{PARTS.index("Apply by 2026-12-01") + 1}] 段,卻沒寫 text'],
                'applicants': [f'{bad}第 {last} 段是「Old Card｜{old["id"]}｜當時表態：未表態」,'
                               f'是相似舊卡的表態與原話,這一格只收頁面段落;屬於 整批共用,這一格只收 {URL} 的']})

    def test_every_field_cite_shape(self):
        for cites in I.FIELD_CITES:
            self.home.write_board()
            self.configure(terms={'applicants_max': 50, 'posted_days': 30})
            self.go(search(URL), judge(lambda m, cites=cites: [row(m, URL, cites=cites)]), reads={URL: 'ok'})
            card = self.card()
            expect(self, 'J-07', card is not None, True, cites)
            fields = card['src']['verification']['fields']
            for name, text in FIELD_TEXT.items():      # 看板畫的:agent 說的、程式讀到的原文、核對狀態、標記
                shown = (fields[name]['claim'], fields[name]['raw'], fields[name]['state'],
                         [(mark['code'], mark['params']) for mark in fields[name]['marks']])
                want = {'verified': (text, next(part for part in PARTS if text in part), '程式核對過', [('CLAIM_MATCH', {})]),
                        'not_on_page': (f'{text} 以外的字', TITLE, '沒核對到',
                                        [('FIELD_SIDE_BY_SIDE', {'said': f'{text} 以外的字', 'quote': TITLE})]),
                        'missing': ('', '', '沒核對到', [('FIELD_NOT_ON_PAGE', {})])}[cites]
                expect(self, {'verified': 'J-04', 'not_on_page': 'J-05', 'missing': 'J-06'}[cites], shown, want,
                       (cites, name))
                for mark in fields[name]['marks']:
                    expect(self, 'A-27', mark['source'], E.source_of(mark['code']), (cites, name))
            summary = card['src']['verification']['summary']['fields']     # 沒寫的摘要欄:agent 說頁上沒寫
            expect(self, 'J-06', {(row['claim'], row['raw'], row['state'], tuple(m['code'] for m in row['marks']))
                                  for row in summary.values()}, {('', '', '沒核對到', ('FIELD_NOT_ON_PAGE',))}, cites)
            verified = cites == 'verified'
            expect(self, 'J-04', (card['title'], card['company'], card.get('applicants')),
                   (TITLE, COMPANY, 12) if verified else ('', '', None), cites)
            terms = [m['text'] for m in card['src']['verification'].get('marks') or [] if m['code'] == 'TERM_UNCHECKED']
            expect(self, 'J-07', bool(terms), not verified, cites)
            expect(self, 'A-40', ('posted_at' in card, 'status' in card), (verified, verified), cites)


class CiteTest(FindCase):
    """A-29:引的段不在這份材料、交付代號對不上、該引沒引、引的不是清單,那一格列給使用者看並標明,其他照收。"""

    def test_every_cite_shape(self):
        for shape in I.CITES:
            self.home.write_board()

            def rows(m, shape=shape):
                title = {'good': m.cite(TITLE, text=TITLE), 'none': {'text': TITLE},
                         'bad_id': {'material_id': m.material_id, 'ids': [999], 'text': TITLE},
                         'other_material': {'material_id': 'zzzzzz', 'ids': [1], 'text': TITLE},
                         'not_list': {'material_id': m.material_id, 'ids': 1, 'text': TITLE}}[shape]
                return [row(m, URL, title=title)]
            self.go(search(URL), judge(rows), reads={URL: 'ok'})
            card = self.card()
            fields = card['src']['verification']['fields']
            codes = [mark['code'] for mark in fields['title']['marks']]
            expect(self, 'A-29', codes, {'good': ['CLAIM_MATCH'], 'none': ['CITE_NONE']}.get(shape, ['CITE_BAD']), shape)
            expect(self, 'A-29', [mark['code'] for mark in fields['company']['marks']], ['CLAIM_MATCH'], shape)


class DeadlineCrowdTest(FindCase):
    """J-14、J-15:截止日只收 YYYY-MM-DD;應徵人數只收整數或「沒有上限」,別的寫法算查不到。"""

    def test_deadline_and_applicants_are_taken_only_in_their_exact_shape(self):
        for deadline, crowd in itertools.product(I.DEADLINE, I.CROWD):
            case = (deadline, crowd)
            parts = (TITLE, COMPANY, f'applicants: {crowd}', f'deadline: {deadline}')
            self.home.write_board()
            self.configure(terms={'applicants_max': 50})

            def rows(m, deadline=deadline, crowd=crowd):
                return [row(m, URL, applicants=m.cite(f'applicants: {crowd}', text=str(crowd)),
                            deadline=m.cite(f'deadline: {deadline}', text=deadline))]
            browser = FakeBrowser(search(URL), judge(rows), fetched={URL: fetched(URL, 'ok', parts)})
            find.main(['--board', self.home.board, '--mode', 'both'], browser=browser)
            card = self.card()
            expect(self, 'J-14', card.get('deadline'), deadline if deadline == '2026-12-01' else None, case)
            expect(self, 'J-15', card.get('applicants'), crowd if crowd in (12, '沒有上限') else None, case)
            unchecked = [m['params'].get('term') for m in card['src']['verification'].get('marks') or []
                         if m['code'] == 'TERM_UNCHECKED']
            expect(self, 'J-15', '應徵人數' in unchecked, crowd == '很多人', case)    # 條件照看板上的名字列


class BlockTest(FindCase):
    """J-12:封鎖名稱跟核對過的公司欄、或使用者的公司別名一字不差相等才擋;封鎖名單交給 agent。"""

    def test_block_only_on_exact_company_or_alias(self):
        names = {None: [], 'company_exact': [COMPANY], 'company_alias': ['Acme'], 'company_other_spelling': ['ACME Robotics']}
        for blocked, cited in itertools.product(I.BLOCKED, (True, False)):
            case = (blocked, cited)
            self.configure()
            self.home.write_board(fb={'__block__': names[blocked]})
            _code, browser = self.go(search(URL), judge(lambda m, c=cited: [row(m, URL, cites='verified' if c else 'missing')]),
                                     reads={URL: 'ok'})
            fb = self.home.fb()
            hit = cited and blocked in ('company_exact', 'company_alias')
            expect(self, 'J-12', delivery_state.company_blocked(fb, self.card()), hit, case)
            expect(self, 'J-12', all(name in browser.prompts[-1] for name in names[blocked]), True, case)


class RowTest(FindCase):
    """J-08、J-11:判斷 agent 每一種結束、漏列、重複列,程式讀到的候選照「沒核對到」進待評估,一張不丟。"""

    def test_missing_or_twice_listed_rows_and_unfinished_judges(self):
        shapes = {'one': lambda m: [row(m, URL), row(m, URL2)], 'unlisted': lambda m: [row(m, URL2)],
                  'twice': lambda m: [row(m, URL), row(m, URL), row(m, URL2)], 'not_list': lambda m: '每一列',
                  'stranger': lambda m: [row(m, URL), row(m, URL2), row(m, 'https://jobs.example/jobs/99')]}
        for end, shape in itertools.product(I.ENDS, shapes):
            case = (end, shape)
            self.home.write_board()
            self.go(search(URL, URL2), judge(shapes[shape], end=end), reads={URL: 'ok', URL2: 'ok'})
            card = self.card()
            if end == I.UNAVAILABLE:
                expect(self, 'J-08', card is not None, True, case)
                continue
            expect(self, 'J-08', card is not None, True, case)
            issue = (card['src']['verification'].get('report_issue') or {}).get('code')
            handed = end == I.HANDED
            clean = shape in ('one', 'stranger')
            want = None if handed and clean else (
                {'unlisted': 'ROW_UNLISTED', 'twice': 'ROW_TWICE', 'not_list': 'SHEET_BROKEN'}.get(shape) if handed
                else issue)
            if handed:
                expect(self, 'J-11', issue, want, case)
            expect(self, 'J-08', card['src']['verification']['state'],
                   'verified' if handed and clean else 'review', case)
            if handed and shape == 'stranger':      # 對不上任何候選的列照實回報,其他列照收(A-29)
                expect(self, 'A-29', '另有 1 列' in self.home.status_file('converge')['msg'], True, case)


class RowIssueTest(FindCase):
    """J-08、J-11、A-29:判斷沒交齊時,卡上記的原因、狀態檔那一句、每輪紀錄判了幾張,逐字照實。"""

    def test_what_an_incomplete_judge_leaves(self):
        unread = 'agent 說「每一列」，程式讀到「必須提供清單」'
        keep = 'agent 說「maybe」，程式讀到「true、false」'
        cases = {
            ('handed', 'not_list'): (lambda m: '每一列', {'code': 'SHEET_BROKEN', 'source': E.PROGRAM, 'params': {'why': unread},
                                                       'text': f'交件單無法讀取：{unread}'}, '有 2 張候選的判斷交件未完整，已列為待確認', 0),
            ('handed', 'bad_keep'): (lambda m: [row(m, URL, keep='maybe'), row(m, URL2)],
                                     {'code': 'ROW_FIELD_BAD', 'source': E.PROGRAM, 'params': {'field': 'keep', 'why': keep},
                                      'text': f'這一列的 keep 欄無法核對：{keep}'}, '有 1 張候選的判斷交件未完整，已列為待確認', 1),
            ('handed', 'stranger'): (lambda m: [row(m, URL), row(m, URL2), row(m, 'https://jobs.example/jobs/99')], None,
                                     '另有 1 列判斷交件無法配對候選', 2),
            ('timeout', 'one'): (lambda m: [row(m, URL), row(m, URL2)],
                                 {'code': 'ENDED', 'source': E.PROGRAM, 'params': {'end': '逾時'}, 'text': '這一輪逾時'},
                                 '有 2 張候選的判斷交件未完整，已列為待確認（判斷 agent 逾時）', 0)}
        for (end, shape), (rows, issue, msg, judged) in cases.items():
            case = (end, shape)
            self.home.write_board()
            self.go(search(URL, URL2), judge(rows, end=I.HANDED if end == 'handed' else I.TIMEOUT), reads={URL: 'ok', URL2: 'ok'})
            expect(self, 'J-11', (self.card()['src']['verification'].get('report_issue'), self.home.status_file('converge')['msg'],
                                  self.research()[-1]['judged']), (issue, msg, judged), case)
            card = self.card()      # 列裡一欄壞了其他欄照記、整張待確認;整份讀不了或沒交完就沒有分數與頁上值
            expect(self, 'J-11', (card['note'], card['src']['fit'], 'applicants' in card, card['src']['verification']['state']),
                   {'not_list': ('0', 0, False, 'review'), 'bad_keep': ('4', 4, True, 'review'),
                    'stranger': ('4', 4, True, 'verified'), 'one': ('0', 0, False, 'review')}[shape],
                   case)


class JudgeRowTest(FindCase):
    """J-17、J-21:留不留、分數、理由、分類、角度、履歷、風險、可能重貼的舊卡都照列標 agent 說的;風險引句核對到才標程式讀到。"""

    def test_judgement_cells_are_recorded_as_the_agent_said(self):
        old = {'id': 'https://jobs.example/jobs/old', 'target': f'{TITLE} - {COMPANY}', 'title': TITLE, 'company': COMPANY}
        other = {'id': 'https://jobs.example/jobs/other', 'target': 'Other', 'title': 'Other', 'company': 'Other Co'}
        for risk_cited in (True, False):
            self.home.write_board([old, other])

            def rows(m, risk_cited=risk_cited):
                return [row(m, URL, risk='ghost', risk_why='刊很久', cat='後端', resume='one/zh', pick_why='中文缺',
                            risk_quote=m.cite(PARTS[2]) if risk_cited else {'material_id': m.material_id, 'ids': [999]},
                            reposts=[{'old': m.cite(old['id']), 'why': '同名'}])]
            self.go(search(URL), judge(rows), reads={URL: 'ok'})
            card = self.card()
            expect(self, 'J-17', card['src'].get('repost_hint'),
                   {'cards': [old['id']], 'text': '公司與職稱跟這張舊卡一字不差,程式沒查過是不是同一個缺'}, risk_cited)
            risk = card['src']['risk']
            expect(self, 'J-21', (risk['kind'], risk['by'], risk['why']), ('ghost', 'agent', '刊很久'), risk_cited)
            expect(self, 'J-21', risk['marks'][0]['source'], E.PROGRAM if risk_cited else E.AGENT, risk_cited)
            expect(self, 'J-21', (card['src']['fit'], card['cat'], card['resume'].get('recommend')), (4, '後端', 'one'),
                   risk_cited)
            expect(self, 'J-17', [r['marks'][0]['source'] for r in card['src']['reposts']], [E.PROGRAM], risk_cited)


class RoundTest(FindCase):
    """J-01、J-13、J-18、J-20、J-22、J-23:範圍照使用者選的;時間上限照設定;狀態檔與每輪紀錄照實;agent 說不投的不成卡。"""

    def test_scope_limits_status_and_history(self):
        self.home.write_board(research=[{'round': str(n)} for n in range(25)])
        _code, browser = self.go(
            search(URL, URL2), judge(lambda m: [row(m, URL), row(m, URL2, keep='false')],
                                     terms_missed=[{'term': '薪資', 'count': 3}]),
            reads={URL: 'ok', URL2: 'ok'},
            argv=['--mode', 'dir', '--direction', '機器人', '--seed-co', 'Acme', '--minutes', '7', '--limit', '1',
                  '--terms', '{"exp_max": 3}'])
        first = browser.prompts[0]
        expect(self, 'J-22', ('機器人' in first, 'Acme' in first, '指定方向' in first, '使用者條件:\n{"exp_max": 3}\n' in first),
               (True, True, True, True))
        expect(self, 'J-18', max(browser.timeouts) <= 7 * 60, True)
        expect(self, 'J-01', browser.fetches, [URL])          # 跑幾張:只讀前一張
        expect(self, 'J-13', (self.card() is not None, self.card(URL2) is None), (True, True))
        status = json.load(open(os.path.join(self.home.tmp['converge'], 'converge_status.json'), encoding='utf-8'))
        expect(self, 'J-20', (status['phase'], status['added'], status['found']), ('done', 1, 1))
        history = self.research()
        expect(self, 'J-20', (len(history), history[-1]['added']), (20, [URL]))
        expect(self, 'J-23', history[-1]['terms_missed'], {'薪資': 3})


class JudgeMaterialTest(FindCase):
    """J-04、J-17:給判斷 agent 的履歷選項照他勾的(沒取名的列代號、寫了什麼時候用就列);每一頁各一段;
    看板沒指定就用現行看板。"""

    def test_resume_choices_and_one_block_per_page(self):
        import board_doc
        two = {'id': 'two', 'files': {'zh': 'resume/two.md'}, 'enabled': True, 'when': '英文職缺'}
        section = '程式讀到的職缺頁(每頁的段落只能用在那一頁的列)'
        for checked, wanted in ((True, '- one/zh:履歷;什麼時候用:(沒寫)\n- two/zh:two;什麼時候用:英文職缺'),
                                (False, '(目前沒有已勾選的履歷;resume 留空)')):
            self.home.write_board()

            def change(settings, checked=checked):
                resumes = settings['resume']['resumes']
                settings['resume']['resumes'] = [*resumes, two] if checked else [dict(r, enabled=False) for r in resumes]
            self.rewrite(change)
            with mock.patch.object(board_doc, 'LIVE', self.home.board):      # 看板啟動找缺時不帶 --board
                browser = FakeBrowser(search(URL, URL2), judge(lambda m: [row(m, URL), row(m, URL2)]),
                                      fetched={URL: fetched(URL, 'ok'), URL2: fetched(URL2, 'ok')})
                find.main(['--mode', 'both'], browser=browser)
            prompt = RoundCase.sections(browser.prompts[1])['材料']
            page = lambda start: '\n'.join(f'{n}. {text}' for n, text in enumerate(PARTS, start))   # noqa: E731
            expect(self, 'J-17', (prompt.split('可選的履歷與語言:\n')[1].split('\n交付代號:')[0],
                                  prompt.split(f'{section}:\n')[1].split('\n看板上的舊卡')[0], self.card(URL2) is not None),
                   (wanted, f'=== {URL} ===\n{page(1)}\n\n=== {URL2} ===\n{page(6)}', True), checked)


class ClockTest(FindCase):
    """J-18:搜尋、判斷從同一個截止時間扣;暫停的時間不計;時間用完就不派判斷,照讀到的收尾。"""

    def test_the_judge_gets_what_is_left_of_the_round(self):
        import time
        import jobrun
        real, clock = time.time, {'later': 0, 'paused': 100}

        def search_takes(seconds, paused):
            def tool(_browser):
                clock.update(later=seconds, paused=paused)
                return ''
            return Script((('tool', tool),), search(URL).sheet)
        with mock.patch('time.time', lambda: real() + clock['later']), \
                mock.patch.object(jobrun, 'current_paused', lambda: clock['paused']):
            for took, paused, left in ((3600, 3700, True), (120, 100, False)):     # 搜尋一小時其中暫停一小時;搜尋兩分鐘沒暫停
                case = (took, paused)
                clock.update(later=0, paused=100)
                self.home.write_board()
                _code, browser = self.go(search_takes(took, paused), judge(lambda m: [row(m, URL)]), reads={URL: 'ok'},
                                         argv=['--minutes', '1'])
                expect(self, 'J-18', [55 <= t <= 60 for t in browser.timeouts], [True, True] if left else [True], case)
                expect(self, 'J-18', self.home.status_file('converge')['msg'],
                       '' if left else '有 1 張候選的判斷交件未完整，已列為待確認（判斷 agent 沒有啟動）', case)


class RoundCase(FindCase):
    """一輪找缺的看板與 agent 收到的共用材料。"""

    OLD, SIDE, THIRD, FOURTH, FIFTH, SIXTH, SEVENTH = (f'https://jobs.example/jobs/{n}' for n in (5, 6, 7, 8, 9, 10, 15))
    EXTRA = ('Build robots', 'Remote', 'NT$100k', '3 years', 'Reposted weekly')
    LAST = {'round': '2026-11-01 10:00', 'mode': 'deep', 'direction': '機器人', 'judged': 3, 'notes': '上一輪',
            'terms_missed': {'薪資': 1}, 'seeds': [{'k': 'co', 'v': 'Acme'}]}

    def board(self):
        self.configure(exclude_words=['外包'], exclude_title='^Intern', flag_words=['急徵'], flag_title='Lead',
                       terms={'posted_days': 30, 'salary_min': 90000, 'location': '台北'})
        jobs = [{'id': self.OLD, 'target': f'{TITLE} · {COMPANY}', 'title': TITLE, 'company': COMPANY, 'src': {'angle': '後端'}},
                {'id': self.SIDE, 'target': 'Designer · Beta', 'title': 'Designer', 'company': 'Beta', 'src': {'angle': '設計'}},
                {'id': self.THIRD, 'target': 'SRE · Gamma', 'title': 'SRE', 'company': 'Gamma', 'src': {'angle': '後端'}},
                {'id': self.FOURTH, 'target': 'UX · Delta', 'title': 'UX', 'company': 'Delta', 'src': {'angle': '設計'}},
                {'id': self.FIFTH, 'target': 'QA · Eps', 'title': 'QA', 'company': 'Eps', 'src': {'angle': '設計'}},
                {'id': self.SIXTH, 'target': 'PM · Zeta', 'title': 'PM', 'company': 'Zeta', 'src': {'angle': '設計'}},
                {'id': self.SEVENTH, 'target': 'Ops · Eta', 'title': 'Ops', 'company': 'Eta', 'src': {'angle': '後端'}}]
        fb = {self.OLD: {'s': 'like', 'n': '喜歡這種'}, self.SIDE: {'s': 'grow'}, self.THIRD: {'s': 'dislike'},
              self.FOURTH: {'s': 'like', 'app': 1}, self.SEVENTH: {'app': 1},
              self.FIFTH: {'rm': True}, '__block__': ['Evil Inc'], '__research__': {'mute': ['行銷']}}
        research = [{'round': f'r{n}', 'added': []} for n in range(8)] + [
            dict(self.LAST, added=[self.OLD, self.THIRD, self.FOURTH, self.SIDE, self.FIFTH, self.SEVENTH, self.SIXTH])]
        self.home.write_board(jobs, fb, research=research)

    def shared(self):
        import feedback_dump
        import prefs
        rounds = [{'round': f'r{n}', 'added': [], 'marks': {'seen': 0, 'liked': 0, 'disliked': 0}} for n in range(1, 8)] + [
            dict(self.LAST, added=[self.OLD, self.THIRD, self.FOURTH, self.SIDE, self.FIFTH, self.SEVENTH, self.SIXTH],
                 marks={'seen': 6, 'liked': 4, 'disliked': 1})]
        with open(cf.PREFERENCE_NOTE, encoding='utf-8') as note:
            note_text = note.read().strip()
        return [
            ('履歷原文', prefs.resume()), ('使用者自訂硬規則原文', prefs.hard_rules()), ('使用者偏好筆記原文', note_text),
            ('偏好整理 skill 原文', self.skill('note')), ('使用者逐張表態整理原文', feedback_dump.build(live=self.home.board)),
            ('角度地圖(依目前看板卡片與表態計數)', '- 後端：送過 3、喜歡 2、不喜歡 1、還沒看 0\n- 設計：送過 4、喜歡 2、不喜歡 0、還沒看 1'),
            ('最近幾輪找缺資料', json.dumps(rounds, ensure_ascii=False)), ('使用者喜歡過的公司', 'Acme Robotics、Beta、Delta、Eta'),
            ('使用者封鎖的公司', 'Evil Inc'), ('使用者設定的排除字', '外包'), ('使用者設定的排除職稱正規式原文', '^Intern'),
            ('使用者設定的要小心字', '急徵'), ('使用者設定的要小心職稱正規式原文', 'Lead'), ('使用者說過別再找的方向', '行銷'),
            ('使用者設定的職缺分類', json.dumps(cf.C['board']['categories'], ensure_ascii=False)),
            ('網站筆記檔案位置', f'{cf.path("website-notes.json")}。依主機名自行讀取相關筆記,筆記只供參考。')]

    @staticmethod
    def skill(name):
        with open(os.path.join(os.path.dirname(find.__file__), 'research_skills', cf.RESEARCH_SKILLS[name]['file']),
                  encoding='utf-8') as source:
            return source.read().strip()

    @staticmethod
    def sections(prompt):
        return dict(re.findall(r'【([^】]+)】\n(.*?)(?=\n\n【|\Z)', prompt, re.S))


class WholeRoundTest(RoundCase):
    """J-01、J-04、J-17、J-20、J-21、J-22、J-23:一輪找缺從入口看得到的全部:給搜尋與判斷 agent 的材料逐字、成卡的整張內容、
    狀態檔每個階段、每輪紀錄、印出的紀錄、他資料夾裡的逐張表態匯出。"""

    def round(self):
        seen = {}

        def look(name):
            def tool(_browser):
                seen[name] = self.home.status_file('converge')
                return ''
            return (('tool', tool),)
        found = Script(look('search'), {'candidates': [
            {'url': URL, 'why': '符合', 'via': 'search', 'angle': '後端'}, {'url': '', 'why': '沒網址', 'via': 'x', 'angle': 'x'},
            {'url': URL, 'why': '又一次', 'via': 'again', 'angle': '別的'}], 'notes': '往後端找' + 'x' * 600, 'stopped': ''})

        def rows(m):
            return [row(m, URL, cat='後端', why='很合', pick_why='英文職缺', risk='ghost', risk_why='每週重貼',
                        risk_quote=m.cite('Reposted weekly'),
                        fit=m.cite('Build robots', text='Build robots'), co=m.cite('Build robots', text='Build robots'),
                        loc=m.cite('Remote', text='Remote'), salary=m.cite('NT$100k', text='NT$100k'),
                        deadline=m.cite('Apply by 2026-12-01', text='2026-12-01'), bar=m.cite('3 years', text='3 years'),
                        ammo=m.cite('Build robots', text='Build robots'),
                        terms_unknown=[{'term': '遠端'}, {'term': ''}, {'term': '遠端'}],
                        reposts=[{'old': m.cite(f'｜{self.OLD}｜'), 'why': '同公司同職稱'}], resume='one/zh')]
        judged = Script(look('judge'), lambda printed: {
            'candidates': rows(printed[0]), 'stopped': '',
            'terms_missed': [{'term': '薪資', 'count': 2}, {'term': '經驗', 'count': 1}, {'term': '地點', 'count': 0},
                             {'term': '', 'count': 3}]})
        browser = FakeBrowser(found, judged, fetched={URL: fetched(URL, 'ok', PARTS + self.EXTRA)})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = find.main(['--board', self.home.board, '--mode', 'dir', '--direction', '機器人', '--seed-url',
                              'https://jobs.example/x', '--seed-co', 'Acme', '--minutes', '7'], browser=browser)
        return code, browser, seen, out.getvalue()

    def test_the_whole_round(self):
        self.board()
        code, browser, seen, printed = self.round()
        ask = [('使用者方向原文', '機器人'), ('這輪找法', '指定方向'), ('使用者條件', '{"posted_days": 30, "salary_min": 90000, "location": "台北"}'),
               ('使用者指名的搜尋種子', '- 指定職缺網址：https://jobs.example/x\n- 指定公司：Acme')]
        listed = lambda pairs: '\n'.join(f'{title}:\n{text}' for title, text in pairs)       # noqa: E731
        search_prompt, judge_prompt = (self.sections(p) for p in browser.prompts)
        expect(self, 'J-22', search_prompt, {
            '目標': '依使用者的找缺 skill 與條件找可能符合需求的職缺,交回職缺網址。使用者決定要不要保留每張卡。',
            '材料': listed([*ask, ('使用者的找缺 skill', self.skill('common') + '\n\n' + self.skill('dir')), *self.shared(),
                           ('已在看板上的職缺網址', '\n'.join((self.OLD, self.SIDE, self.THIRD, self.FOURTH, self.FIFTH, self.SIXTH, self.SEVENTH)))]),
            '工具與用法': '網路搜尋:找候選職缺。\n' + find.FETCH,
            '界線': '- 只讀網站,不應徵、不登入、不輸入密碼、不留言或私訊。\n'
                  '- 網站筆記不記職缺內容、個人資料、登入資訊或推測;使用者寫的筆記列入移除也不會刪除。',
            '交件單格式': find.SEARCH_SHEET.format_text()})
        material = re.search(r'material_id=(\w+)', judge_prompt['材料']).group(1)
        page = '\n'.join(f'{n}. {text}' for n, text in enumerate(PARTS + self.EXTRA, 1))
        old = '\n'.join(f'{n}. {text}' for n, text in enumerate((
            f'{TITLE} · {COMPANY}｜{self.OLD}｜當時表態：喜歡｜原話：「喜歡這種」', f'Designer · Beta｜{self.SIDE}｜當時表態：可努力',
            f'SRE · Gamma｜{self.THIRD}｜當時表態：不喜歡', f'UX · Delta｜{self.FOURTH}｜當時表態：喜歡、加進準備區',
            f'QA · Eps｜{self.FIFTH}｜當時表態：已移除', f'PM · Zeta｜{self.SIXTH}｜當時表態：未表態',
            f'Ops · Eta｜{self.SEVENTH}｜當時表態：加進準備區'), len(PARTS + self.EXTRA) + 1))
        expect(self, 'J-04', judge_prompt, {
            '目標': '照使用者的判斷 skill 判斷每一個職缺;每一欄都引程式讀到的那一頁的段落。使用者決定要不要保留每張卡。',
            '材料': listed([*ask, ('使用者的判斷 skill', self.skill('judge')), *self.shared(),
                           ('可選的履歷與語言', '- one/zh:履歷;什麼時候用:(沒寫)'), ('交付代號', f'material_id={material}'),
                           ('程式讀到的職缺頁(每頁的段落只能用在那一頁的列)', f'=== {URL} ===\n{page}'),
                           ('看板上的舊卡(重貼引這裡的段落)', old)]),
            '工具與用法': find.NO_TOOLS, '界線': '- 只引材料裡的段落。\n- 頁面讀不到、沒有文字或不完整,也照樣交那一列。',
            '交件單格式': find.JUDGE_SHEET.format_text()})

        card = self.card()
        stamp = card['src']['round']
        today = card['added']
        verification = card['src']['verification']
        for group in ('fields', 'summary'):
            cells = verification[group] if group == 'fields' else verification[group]['fields']
            verification[group] = {name: (cell['claim'], [m['code'] for m in cell['marks']]) for name, cell in cells.items()}
        shot = {key: verification.pop(key) for key in ('page_file', 'screenshot_file')}
        import evidence
        expect(self, 'J-10', [re.fullmatch(r'\d{8}-\d{6}-\d{6}-find-review/(page|shot)-1\.(json|png)', rel) is not None
                              for rel in shot.values()], [True, True])
        with open(evidence.path(URL, shot['page_file'], self.home.board), encoding='utf-8') as source:
            read = json.load(source)
        expect(self, 'J-10', (read, os.path.isfile(evidence.path(URL, shot['screenshot_file'], self.home.board) or '')), ({
            'tab': '', 'url': URL, 'status': 'read', 'why': '', 'parts': list(PARTS + self.EXTRA), 'shot': read['shot'],
            'shot_why': '', 'via': 'direct', 'title': '', 'material': {'http_status': 200}}, True))
        import prefs
        said = {'state': '程式核對過', 'claim': ''}
        expect(self, 'J-04', card, {
            'id': URL, 'target': f'{TITLE} · {COMPANY}', 'title': TITLE, 'company': COMPANY, 'cat': '後端', 'chan': '官方',
            'ammo': '', 'note': '4', 'bk': False, 'dead': False, 'added': today,
            'src': {'round': stamp, 'mode': 'dir', 'direction': '機器人', 'via': 'search', 'angle': '後端', 'why': '很合',
                    'fit': 4, 'site': 'jobs.example',
                    'verification': {
                        'page_status': 'ok', 'page_excerpt': '\n'.join(PARTS + self.EXTRA), 'marks': [], 'state': 'verified',
                        'fields': {name: (text, ['CLAIM_MATCH']) for name, text in FIELD_TEXT.items()},
                        'summary': {'fit': ('Build robots', ['CLAIM_MATCH']), 'co': ('Build robots', ['CLAIM_MATCH']),
                                    'loc': ('Remote', ['CLAIM_MATCH']), 'salary': ('NT$100k', ['CLAIM_MATCH']),
                                    'deadline': ('2026-12-01', ['CLAIM_MATCH']), 'bar': ('3 years', ['CLAIM_MATCH']),
                                    'ammo': ('Build robots', ['CLAIM_MATCH'])}},
                    'risk': dict(said, kind='ghost', why='每週重貼', by='agent', raw='Reposted weekly', marks=[
                        {'code': 'CLAIM_MATCH', 'source': E.PROGRAM, 'params': {}, 'text': 'agent 說的改動與程式讀到的相符'},
                        {'code': 'AGENT_WORDS', 'source': E.AGENT, 'params': {'words': '每週重貼'}, 'text': 'agent 說：每週重貼'}]),
                    'reposts': [dict(said, raw=f'{TITLE} · {COMPANY}｜{self.OLD}｜當時表態：喜歡｜原話：「喜歡這種」', marks=[
                        {'code': 'CLAIM_MATCH', 'source': E.PROGRAM, 'params': {}, 'text': 'agent 說的改動與程式讀到的相符'},
                        {'code': 'AGENT_WORDS', 'source': E.AGENT, 'params': {'words': '同公司同職稱'},
                         'text': 'agent 說：同公司同職稱'}])],
                    'repost_hint': {'cards': [self.OLD], 'text': find.REPOST_HINT}},
            'sum': {'fit': 'Build robots', 'co': 'Build robots', 'loc': 'Remote', 'salary': 'NT$100k', 'deadline': '2026-12-01',
                    'bar': '3 years', 'ammo': 'Build robots'},
            'resume': {'selection_signature': prefs.resume_selection_signature(prefs.checked_resumes()),
                       'feedback_signature': prefs.feedback_signature(''), 'recommend': 'one', 'lang': 'zh',
                       'pick_why': '英文職缺'},
            'terms_unknown': ['遠端'], 'deadline': '2026-12-01', 'posted_at': '2026-11-01', 'posted_src': 'Posted 2026-11-01',
            'status': 'Apply by', 'applicants': 12, 'applicants_at': today, 'applicants_log': [[today, 12]],
            'like': 100})       # 有新卡才重算看板的喜好分數(prefs.refresh_like)
        expect(self, 'J-20', (re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d', stamp) is not None, today == stamp[:10]), (True, True))

        final = self.home.status_file('converge')
        running = {'pid': os.getpid(), 'mode': 'dir', 'direction': '機器人', 'minutes': 7, 'graceful': True}
        clocks = [status.pop(key) for status in (*seen.values(), final) for key in ('t0', 'at')] + [final.pop('finished_at')]
        expect(self, 'J-20', (seen, final, code), ({'search': dict(running, phase='start'),
                                                    'judge': dict(running, phase='agent', step='核對 agent 引的職缺頁原文')},
                                                   dict(running, phase='done', added=1, msg='', found=1), 0))
        expect(self, 'J-20', clocks[0] == clocks[2] == clocks[4] <= clocks[1] <= clocks[3] <= clocks[5], True)
        history = self.research()
        expect(self, 'J-23', (len(history), history[-1]), (10, {
            'round': stamp, 'mode': 'dir', 'direction': '機器人', 'judged': 1, 'terms_missed': {'薪資': 2, '經驗': 1},
            'notes': ('往後端找' + 'x' * 600)[:600], 'seeds': [{'k': 'job', 'v': 'https://jobs.example/x'}, {'k': 'co', 'v': 'Acme'}],
            'added': [URL]}))
        expect(self, 'J-20', printed.splitlines()[-2:], ['找缺：完成', '這輪新增 1 筆職缺(在看板「🆕 待評估」)'])
        with open(cf.PREFS, encoding='utf-8') as export:
            expect(self, 'J-19', '喜歡這種' in export.read(), True)


class EmptyRoundTest(RoundCase):
    """J-03、J-20、J-22:什麼都還沒有的看板:材料照寫沒有;讀不到的頁照列原因交判斷;沒有候選照實收尾。"""

    def go_empty(self, *scripts):
        self.home.write_board()
        browser = FakeBrowser(*scripts, fetched={URL: fetched(URL, 'unknown')}, can_open=False)   # 找缺不開瀏覽器工作區
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = find.main(['--board', self.home.board, '--mode', 'both'], browser=browser)
        return code, browser, out.getvalue()

    def test_an_empty_board(self):
        _code, browser, _printed = self.go_empty(search(URL), judge(lambda m: [row(m, URL, cites='missing')]))
        none = '(目前沒有)'
        shared = dict(self.shared())
        shared.update({'角度地圖(依目前看板卡片與表態計數)': '(目前沒有角度表態)', '最近幾輪找缺資料': '[]',
                       **{title: none for title in ('使用者喜歡過的公司', '使用者封鎖的公司', '使用者設定的排除字',
                                                    '使用者設定的排除職稱正規式原文', '使用者設定的要小心字',
                                                    '使用者設定的要小心職稱正規式原文', '使用者說過別再找的方向')}})
        ask = [('使用者方向原文', '(沒有寫)'), ('這輪找法', '更深＋更廣'), ('使用者條件', '{}'), ('使用者指名的搜尋種子', none)]
        listed = lambda pairs: '\n'.join(f'{title}:\n{text}' for title, text in pairs)       # noqa: E731
        search_prompt, judge_prompt = (self.sections(p)['材料'] for p in browser.prompts)
        skills = '\n\n'.join(self.skill(name) for name in ('common', 'deep', 'wide'))
        expect(self, 'J-22', search_prompt, listed([*ask, ('使用者的找缺 skill', skills), *shared.items(),
                                                    ('已在看板上的職缺網址', none)]))
        material = re.search(r'material_id=(\w+)', judge_prompt).group(1)
        expect(self, 'J-03', judge_prompt, listed([
            *ask, ('使用者的判斷 skill', self.skill('judge')), *shared.items(), ('可選的履歷與語言', '- one/zh:履歷;什麼時候用:(沒寫)'),
            ('交付代號', f'material_id={material}'),
            ('程式讀到的職缺頁(每頁的段落只能用在那一頁的列)', f'=== {URL} ===\n{URL}讀不到：要登入\n{UNREAD}'),
            ('看板上的舊卡(重貼引這裡的段落)', none)]))
        source = self.card()['src']
        expect(self, 'J-03', (source['why'], source['verification']['state'], self.card()['target']),
               ('頁面無法讀取', 'review', '職缺頁待確認'))

    def test_no_candidates(self):
        code, _browser, printed = self.go_empty(Script((), {'candidates': [], 'notes': '', 'stopped': ''}))
        final = self.home.status_file('converge')
        for key in ('t0', 'at', 'finished_at'):
            final.pop(key)
        expect(self, 'J-20', (code, final, printed.splitlines()[-2:], [dict(row, round='') for row in self.research()]), (
            0, {'pid': os.getpid(), 'mode': 'both', 'direction': '', 'minutes': None, 'graceful': True, 'phase': 'done', 'added': 0, 'msg': '',
                'found': 0}, ['找缺：完成', '這輪新增 0 筆職缺(在看板「🆕 待評估」)'],
            [{'round': '', 'mode': 'both', 'direction': '', 'judged': 0, 'terms_missed': {}, 'notes': '', 'seeds': [],
              'added': []}]))


class FindNotesTest(FindCase):
    """A-38、A-39:搜尋 agent 寫的網站筆記照收、記這一輪;使用者寫的拿不掉,狀態檔照實講。"""

    def test_search_notes(self):
        import website_notes
        self.home.write_board()
        website_notes.user_action('add', URL, '使用者的筆記')
        user_id = next(n['id'] for n in website_notes.for_url(URL) if n.get('author') == 'user')
        found = Script((), {'candidates': [{'url': u, 'why': '符合', 'via': 'search', 'angle': '後端'} for u in (URL, URL2)],
                            'notes': '', 'stopped': '', 'website_notes': [{'url': URL, 'note': '要捲動'}],
                            'website_notes_remove': [{'id': user_id}]})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _code, browser = self.go(found, judge(lambda m: [row(m, URL)]), reads={URL: 'ok', URL2: 'ok'})
        msg = '有 1 張候選的判斷交件未完整，已列為待確認；agent 試圖移除 1 則使用者網站筆記，程式已保留；請到網站筆記設定確認'
        expect(self, 'A-39', out.getvalue().splitlines()[-2], f'找缺：{msg}')
        agent = [(n['text'], n['card_id'], bool(n['round_id'])) for n in website_notes.for_url(URL) if n['author'] == 'agent']
        expect(self, 'A-38', (agent, any(n['id'] == user_id for n in website_notes.for_url(URL)),
                              self.home.status_file('converge')['msg']),
               ([('要捲動', None, True)], True, msg))


class SearchEndTest(FindCase):
    """J-18、J-20:搜尋 agent 逾時照這一輪讀到的收尾(沒找到);沒交件、交件單壞掉照實寫進狀態檔;看板讀不了不停在進行中。"""

    def test_search_endings(self):
        said = '搜尋 agent 沒交件（{}）：{}'
        messages = {I.HANDED: '', I.TIMEOUT: '時間到，這輪沒找到',
                    I.NO_SHEET: said.format('沒有交件單', '假 agent:no_sheet'),
                    I.BROKEN_SHEET: said.format('交件', "交件單無法讀取：JSONDecodeError: Expecting ',' delimiter: line 1 column 6 (char 5)"),
                    I.CRASHED: said.format('當掉', '假 agent:crashed'), I.STOPPED_BY_USER: said.format('被停止', '假 agent:stopped_by_user'),
                    I.UNAVAILABLE: said.format('沒有啟動', '假瀏覽器:沒有可用的 agent')}
        for end in I.ENDS:
            self.home.write_board()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code, browser = self.go(search(URL, end=end), judge(lambda m: [row(m, URL)]), reads={URL: 'ok'})
            status = self.home.status_file('converge')
            handed = end == I.HANDED
            want = 'done' if handed or end == I.TIMEOUT else 'failed'
            msg = messages[end]
            expect(self, 'J-20', (code, status['phase'], status['msg'], status['finished_at'] >= status['t0']),
                   (0 if want == 'done' else 1, want, msg, True), end)
            expect(self, 'J-20', out.getvalue().splitlines()[-2:],
                   [f'找缺：{msg}' if msg else '找缺：完成', f'這輪新增 {int(handed)} 筆職缺(在看板「🆕 待評估」)'], end)
            history = self.research()[-1]
            expect(self, 'J-23', dict(history, round=''), {
                'round': '', 'mode': 'both', 'direction': '', 'judged': int(handed), 'terms_missed': {},
                'notes': '往後端找' if handed else '', 'seeds': [], 'added': [URL] if handed else []}, end)
            expect(self, 'J-18', browser.timeouts, [4 * 3600, 3600] if handed else [4 * 3600], end)   # 沒給時間上限:搜尋 4 小時、判斷 1 小時
            if not handed:
                rest = {key: value for key, value in status.items() if key not in ('msg', 't0', 'at', 'finished_at', 'pid')}
                expect(self, 'J-20', rest, {'mode': 'both', 'direction': '', 'minutes': None, 'graceful': True, 'phase': want,
                                            'added': 0, 'found': 0, 'timeup': end == I.TIMEOUT}, end)
            expect(self, 'J-20', (self.card() is not None, browser.fetches), (handed, [URL] if handed else []), end)

    def test_unreadable_board(self):
        missing = os.path.join(self.home.dir, 'gone.html')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = find.main(['--board', missing, '--mode', 'both'], browser=FakeBrowser())
        status = self.home.status_file('converge')
        msg = f"看板讀不了,這一輪沒做:[Errno 2] No such file or directory: '{missing}'"
        expect(self, 'J-20', (code, out.getvalue(), status['finished_at'] >= status['t0'],
                              {key: value for key, value in status.items() if key not in ('t0', 'at', 'finished_at')}),
               (1, msg + '\n', True, {'msg': msg, 'phase': 'failed', 'pid': os.getpid(), 'mode': 'both', 'direction': '',
                                      'minutes': None, 'graceful': True}))

    def test_the_judge_skill_he_selected_or_the_built_in_one_when_it_cannot_be_read(self):
        builtin = RoundCase.skill('judge')
        os.makedirs(os.path.join(self.home.home, 'custom'), exist_ok=True)
        with open(os.path.join(self.home.home, 'custom', 'judge.md'), 'w', encoding='utf-8') as target:
            target.write('# 我的判斷\n只要遠端\n')
        for selected, wanted in (('custom/judge.md', '# 我的判斷\n只要遠端'), ('custom/gone.md', builtin)):
            self.home.write_board()
            self.rewrite(lambda settings, selected=selected: settings.setdefault('research', {}).update(skills={'judge': selected}))
            _code, browser = self.go(search(URL), judge(lambda m: [row(m, URL)]), reads={URL: 'ok'})
            expect(self, 'J-22', RoundCase.sections(browser.prompts[-1])['材料'].split('使用者的判斷 skill:\n')[1].split(
                '\n履歷原文:')[0], wanted, selected)


class PreferenceTest(FindCase):
    """J-19:整理喜好時,使用者自訂區 agent 改不動;給 agent 的喜好材料只放使用者說過的話。"""

    def test_user_section_survives_the_note_round(self):
        import board_doc
        import prefs
        note = f'{prefs.NOTE_CUSTOM}\n不要外包\n{prefs.NOTE_AGENT}\n'
        with open(cf.PREFERENCE_NOTE, 'w', encoding='utf-8') as target:
            target.write(note)
        old = {'id': 'https://jobs.example/jobs/old', 'target': 'Old', 'title': 'Old', 'company': 'Old Co'}
        self.home.write_board([old])
        board_doc.set_fb(lambda fb: fb.setdefault(old['id'], {}).update(s='dislike', w='外包'), live=self.home.board)
        import feedback_dump
        changed = f'{prefs.NOTE_CUSTOM}\n可以外包\n{prefs.NOTE_AGENT}\n- 不愛外包\n'
        during = {}

        def look(_browser):
            during.update(self.home.status_file('converge'))
            return ''
        note_round = Script((('tool', look),), {'note': changed, 'cards': [{'card': old['id'], 'hypothesis': '- 不愛外包'}]})
        delta = feedback_dump.feedback_delta(self.home.board, cf.PREFERENCE_NOTE)[0]
        _code, browser = self.go(search(), note_round)
        expect(self, 'J-20', (during['phase'], during['step'], browser.timeouts), ('agent', '整理新表態偏好', [4 * 3600] * 2))
        prompt = RoundCase.sections(browser.prompts[1])
        expect(self, 'J-19', prompt, {
            '目標': '依使用者選擇的整理偏好筆記 skill,整理上次整理後的新表態,讓下一輪找缺使用更新後的角度。',
            '材料': f'整理偏好筆記 skill:\n{RoundCase.skill("note")}\n偏好筆記原文:\n{note.rstrip()}\n新表態原文:\n{delta.rstrip()}',
            '工具與用法': find.NO_TOOLS,
            '界線': '- 只更新 Agent 假設區;使用者自訂區逐字保留。\n- 每張新表態都要歸屬;沒列齊時程式不更新筆記,下一輪再整理。',
            '交件單格式': find.NOTE_SHEET.format_text()})
        expect(self, 'J-19', self.home.status_file('converge')['msg'], '偏好筆記的使用者自訂區未變；agent 嘗試修改的內容已忽略')
        with open(cf.PREFERENCE_NOTE, encoding='utf-8') as source:
            text = source.read()
        expect(self, 'J-19', ('不要外包' in text, '可以外包' in text, '不愛外包' in text), (True, False, True))
        expect(self, 'J-19', '外包' in browser.prompts[-1], True)

    def test_note_stays_when_the_agent_leaves_cards_unattributed_or_drops_a_section(self):
        import board_doc
        import prefs
        note = f'{prefs.NOTE_CUSTOM}\n不要外包\n{prefs.NOTE_AGENT}\n'
        old, other = ({'id': f'https://jobs.example/jobs/{n}', 'target': n, 'title': n, 'company': 'Old Co'} for n in ('old', 'other'))
        said = lambda card, text='- 不愛外包': {'card': card['id'], 'hypothesis': text}     # noqa: E731
        sheets = {'unattributed': {'note': f'{note}- 不愛外包\n', 'cards': [said(other)]},
                  'not_in_note': {'note': f'{note}- 不愛外包\n', 'cards': [said(old, '- 別的'), said(other)]},
                  'twice': {'note': f'{note}- 不愛外包\n', 'cards': [said(old), said(old), said(other)]},
                  'no_sections': {'note': '- 不愛外包', 'cards': [{'card': card['id'], 'unclear': '看不出來'} for card in (old, other)]},
                  'updated': {'note': f'{note}- 不愛外包\n', 'cards': [said(old), said(other)]}}
        for case, sheet in sheets.items():
            with open(cf.PREFERENCE_NOTE, 'w', encoding='utf-8') as target:
                target.write(note)
            self.home.write_board([old, other])
            for card in (old, other):
                board_doc.set_fb(lambda fb, card=card: fb.setdefault(card['id'], {}).update(s='dislike', w='外包'),
                                 live=self.home.board)
            self.go(search(), Script((), sheet))
            with open(cf.PREFERENCE_NOTE, encoding='utf-8') as source:
                expect(self, 'J-19', source.read(), case == 'updated' and
                       f'# 偏好筆記\n\n{prefs.NOTE_CUSTOM}\n\n不要外包\n\n{prefs.NOTE_AGENT}\n\n- 不愛外包\n' or note, case)
            unattributed = '偏好筆記未更新(交件)：有 1 張新表態沒有完整歸屬'
            expect(self, 'J-19', self.home.status_file('converge')['msg'], {
                'unattributed': unattributed, 'not_in_note': unattributed, 'twice': unattributed, 'updated': '',
                'no_sections': '偏好筆記未更新：偏好筆記缺少使用者自訂或 Agent 假設區'}[case], case)

    def test_what_he_writes_in_his_section_during_the_note_round_stays(self):
        import board_doc
        import prefs
        note = f'{prefs.NOTE_CUSTOM}\n不要外包\n{prefs.NOTE_AGENT}\n'
        mine = f'{prefs.NOTE_CUSTOM}\n不要外包\n也不要派遣\n{prefs.NOTE_AGENT}\n'
        with open(cf.PREFERENCE_NOTE, 'w', encoding='utf-8') as target:
            target.write(note)
        old = {'id': 'https://jobs.example/jobs/old', 'target': 'Old', 'title': 'Old', 'company': 'Old Co'}
        self.home.write_board([old])
        board_doc.set_fb(lambda fb: fb.setdefault(old['id'], {}).update(s='dislike', w='外包'), live=self.home.board)

        def he_edits(_browser):
            with open(cf.PREFERENCE_NOTE, 'w', encoding='utf-8') as target:
                target.write(mine)
            return ''
        self.go(search(), Script((('tool', he_edits),), {'note': f'{note}- 不愛外包\n',
                                                         'cards': [{'card': old['id'], 'hypothesis': '- 不愛外包'}]}))
        with open(cf.PREFERENCE_NOTE, encoding='utf-8') as source:
            expect(self, 'J-19', (source.read(), self.home.status_file('converge')['msg']),
                   (f'# 偏好筆記\n\n{prefs.NOTE_CUSTOM}\n\n不要外包\n也不要派遣\n\n{prefs.NOTE_AGENT}\n\n- 不愛外包\n', ''))


class AddJobTest(FindCase):
    """J-16:貼網址加卡跟找缺同一套讀頁與核對;貼進去的網址一律成卡。"""

    def add(self, *scripts, reads=None, argv=()):
        """從入口貼網址跑一次;回 (結束碼, 假瀏覽器, 依序寫進狀態檔的每一版, 印出的字)。"""
        import jobrun
        writes, real = [], jobrun.write

        def record(path, data):
            if path.endswith(add_job.STATUS):
                writes.append(dict(data))
            return real(path, data)
        out = io.StringIO()
        with mock.patch.object(jobrun, 'write', record), contextlib.redirect_stdout(out):
            code, browser = self.go(*scripts, reads=reads, argv=list(argv), entry='add')
        t0 = writes[0].get('t0')
        for data in writes:
            self.assertEqual((data.get('t0'), data.get('pid', os.getpid())), (t0, os.getpid()))
            if 'finished_at' in data:
                self.assertGreaterEqual(data['finished_at'], t0)
            data.pop('pid', None), data.pop('t0', None), data.pop('finished_at', None)
        return code, browser, writes, out.getvalue()

    @staticmethod
    def steps(n, last):
        return [{'phase': 'fold', 'n': n, 'step': '檢查網址'},
                {'phase': 'judge', 'n': n, 'step': 'agent 讀取職缺並寫卡片摘要'}, last]

    def test_one_paste_from_entry_to_card(self):
        """貼的網址去重、板上已有的(含已移除)不讀;給判斷 agent 的找法是「使用者自己貼的網址」;狀態檔每一版、成卡整張、印出的字。"""
        import evidence
        import prefs
        self.home.write_board([{'id': URL2, 'target': 'Old', 'title': 'Old', 'company': 'Old Co'}], fb={URL2: {'rm': True}})
        code, browser, writes, out = self.add(judge(lambda m: [row(m, URL, resume='one/zh', pick_why='中文職缺')]),
                                              reads={URL: 'ok'}, argv=['--url', URL, '--url', URL, '--url', URL2])
        expect(self, 'J-16', (code, browser.fetches, out, browser.timeouts), (None, [URL], '新增 1 張\n', [3600]))
        expect(self, 'J-16', writes, self.steps(1, {'phase': 'done', 'added': 1, 'n': 1, 'msg': '加進新職缺 1 張'}))
        material = RoundCase.sections(browser.prompts[0])['材料']
        expect(self, 'J-16', material.split('\n使用者的判斷 skill:')[0],
               '使用者方向原文:\n(沒有寫)\n這輪找法:\n使用者自己貼的網址\n使用者條件:\n{}\n使用者指名的搜尋種子:\n(目前沒有)')
        expect(self, 'J-16', re.findall(r'^=== (.*) ===$', material, re.M), [URL])
        card = self.card()
        verification = card['src']['verification']
        verification['fields'] = {name: (cell['claim'], [m['code'] for m in cell['marks']])
                                  for name, cell in verification['fields'].items()}
        verification['summary'] = {name: (cell['claim'], [m['code'] for m in cell['marks']])
                                   for name, cell in verification['summary']['fields'].items()}
        files = {key: verification.pop(key) for key in ('page_file', 'screenshot_file')}
        expect(self, 'J-10', [(re.fullmatch(r'\d{8}-\d{6}-\d{6}-add-review/(page|shot)-1\.(json|png)', rel) is not None,
                               os.path.isfile(evidence.path(URL, rel, self.home.board) or '')) for rel in files.values()],
               [(True, True)] * 2)
        today, stamp = card['added'], card['src']['round']
        expect(self, 'J-16', (re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d', stamp) is not None, stamp[:10]), (True, today))
        expect(self, 'J-16', card, {
            'id': URL, 'target': f'{TITLE} · {COMPANY}', 'title': TITLE, 'company': COMPANY, 'cat': '', 'chan': '直投',
            'ammo': '', 'note': '4', 'bk': False, 'dead': False, 'added': today,
            'src': {'round': stamp, 'mode': 'add', 'via': 'manual', 'angle': '', 'why': '對味', 'fit': 4, 'site': 'jobs.example',
                    'verification': {
                        'page_status': 'ok', 'page_excerpt': '\n'.join(PARTS), 'marks': [], 'state': 'verified',
                        'fields': {name: (text, ['CLAIM_MATCH']) for name, text in FIELD_TEXT.items()},
                        'summary': {name: ('', ['FIELD_NOT_ON_PAGE'])
                                    for name in ('fit', 'co', 'loc', 'salary', 'deadline', 'bar', 'ammo')}}},
            'sum': {}, 'resume': {'selection_signature': prefs.resume_selection_signature(prefs.checked_resumes()),
                                  'feedback_signature': prefs.feedback_signature(''), 'recommend': 'one', 'lang': 'zh',
                                  'pick_why': '中文職缺'},
            'posted_at': '2026-11-01', 'posted_src': 'Posted 2026-11-01', 'status': 'Apply by', 'applicants': 12,
            'applicants_at': today, 'applicants_log': [[today, 12]]})

    def test_a_held_page_whose_shot_is_gone_is_written_with_the_paste_and_says_so(self):
        """agent 不能用:存成待寫(讀到的頁與這張卡證據夾裡的截圖)、狀態與印出的字照講;
        下一次先寫待寫的、再讀新貼的,截圖不見了卡上照實寫。"""
        import evidence
        self.home.write_board()
        code, _browser, writes, out = self.add(reads={URL: 'ok'}, argv=['--url', URL])
        why = 'agent 都不能用(假瀏覽器:沒有可用的 agent)'
        expect(self, 'J-16', (code, writes, out), (None, self.steps(1, {
            'phase': 'waiting', 'added': 0, 'n': 1, 'msg': f'1 張還沒寫卡片:{why}'}),
            f'1 張還沒寫卡片:{why};每隔一小時會自動接著寫\n新增 0 張\n'))
        with open(add_job.pending_path(), encoding='utf-8') as source:
            [held] = json.load(source)
        page = held.pop('page')
        page.pop('shot')
        shot = held.pop('shot')
        expect(self, 'J-16', (held, page), ({'url': URL}, {
            'tab': '', 'url': URL, 'status': 'read', 'why': '', 'parts': list(PARTS), 'shot_why': '', 'via': 'direct',
            'title': '', 'material': {'http_status': 200}}))
        expect(self, 'J-10', (os.path.dirname(os.path.dirname(shot)), os.path.basename(shot), os.path.isfile(shot)),
               (evidence.card_dir(URL, self.home.board), 'shot-1.png', True))
        os.remove(shot)
        code, browser, writes, out = self.add(judge(lambda m: [row(m, URL)]), reads={URL2: 'ok'}, argv=['--url', URL2])
        expect(self, 'J-16', (code, browser.fetches, out, add_job.waiting()), (None, [URL2], '新增 2 張\n', 0))
        expect(self, 'J-16', writes, self.steps(2, {'phase': 'done', 'added': 2, 'n': 2, 'msg': '加進新職缺 2 張'}))
        expect(self, 'J-16', re.findall(r'^=== (.*) ===$', RoundCase.sections(browser.prompts[0])['材料'], re.M), [URL, URL2])
        verification = self.card()['src']['verification']
        with open(evidence.path(URL, verification['page_file'], self.home.board), encoding='utf-8') as source:
            stored = json.load(source)
        expect(self, 'J-16', (stored.pop('shot'), stored), ('', dict(page, shot_why='待寫期間讀頁那一刻的截圖不見了')))
        gone = '待寫期間讀頁那一刻的截圖不見了'
        expect(self, 'J-10', (verification.get('screenshot_file'), verification['screenshot_error'], verification['marks']),
               (None, gone, [{'code': 'SHOT_MISSING', 'source': E.NOT_COMPARED, 'params': {'where': URL, 'why': gone},
                              'text': f'{URL}截圖沒取得：{gone}'}]))
        expect(self, 'J-16', (self.card(URL2)['title'], self.card(URL2)['src']['verification']['state']), ('', 'review'))

    def test_pasted_urls_always_become_cards(self):
        for read, keep in itertools.product(I.URL_READ, I.KEEP):
            case = (read, keep)
            self.home.write_board()
            _code, browser = self.go(judge(lambda m, keep=keep: [row(m, URL, 'true' if keep == 'keep' else 'false')]),
                                     reads={URL: read}, argv=['--url', URL], entry='add')
            card = self.card()
            expect(self, 'J-16', (card is not None, browser.fetches), (True, [URL]), case)
            expect(self, 'J-16', card['src']['verification']['page_status'],
                   {'ok': 'ok', 'partial': 'partial'}.get(read, 'unknown'), case)

    def test_every_route_that_failed_is_named(self):
        result = dict(fetched(URL, 'unknown'), errors=['direct: HTTP 403', 'reader: 要登入'])     # 讀取那一側每條路的原因
        find.main(['--board', self.home.board, '--mode', 'both'],
                  browser=FakeBrowser(search(URL), judge(lambda m: [row(m, URL, cites='missing')]), fetched={URL: result}))
        verification = self.card()['src']['verification']
        expect(self, 'J-03', (verification['page_status'], [m['text'] for m in verification['marks']]),
               ('unknown', [URL + '讀不到：direct: HTTP 403; reader: 要登入', '頁面讀不到，欄位由 agent 判斷']))

    def test_pages_wait_when_no_agent_can_run_and_are_written_next_time(self):
        for shot in I.SHOTS:
            self.home.write_board()
            _code, first = self.go(reads={URL: 'ok'}, shots_fail=() if shot else (URL,), argv=['--url', URL], entry='add')
            expect(self, 'J-16', (self.card(), self.home.status_file('add')['phase'], add_job.waiting(), first.fetches),
                   (None, 'waiting', 1, [URL]), shot)
            _code, second = self.go(judge(lambda m: [row(m, URL)]), entry='add')
            verification = self.card()['src']['verification']
            expect(self, 'J-16', (second.fetches, self.card()['title'], add_job.waiting()), ([], TITLE, 0), shot)
            expect(self, 'J-10', (bool(verification.get('screenshot_file')), verification.get('screenshot_error')),
                   (shot, None if shot else f'OSError: {SHOT_FAIL}'), shot)

    def test_held_pages_in_a_format_it_cannot_read_are_read_again(self):
        self.home.write_board()
        os.makedirs(os.path.dirname(add_job.pending_path()), exist_ok=True)
        with open(add_job.pending_path(), 'w', encoding='utf-8') as target:
            json.dump([{'url': URL, 'page': {'status': 'ok'}, 'shot': ''}], target)
        _code, browser = self.go(judge(lambda m: [row(m, URL)]), reads={URL: 'ok'}, entry='add')
        expect(self, 'J-16', (browser.fetches, self.card()['title']), ([URL], TITLE))

    def test_urls_already_on_the_board(self):
        old = {'id': URL, 'target': 'Old', 'title': 'Old', 'company': 'Old Co'}
        for removed, argv in itertools.product((False, True), (['--url', URL], [])):
            case = (removed, argv)
            self.home.write_board([old], fb={URL: {'rm': True}} if removed else {})
            _code, browser, writes, out = self.add(argv=argv)
            msg = ('這幾個網址板上都已經有了' + ('(其中 1 張在「🗑 已移除」,要的話到那一頁按「↩︎ 放回看板」)' if removed else '')
                   if argv else '沒有網址')
            expect(self, 'J-16', (browser.fetches, writes, out), ([], [{'phase': 'nothing', 'msg': msg}], '新增 0 張\n'), case)

    def test_unfinished_judges_still_add_the_card_and_say_so(self):
        import agent_report
        for end in [end for end in I.ENDS if end != I.UNAVAILABLE]:
            self.home.write_board()
            self.go(judge(lambda m: [row(m, URL)], end=end), reads={URL: 'ok'}, argv=['--url', URL], entry='add')
            reported = [(r['msg'], r['need']) for r in self.home.fb().get(agent_report.KEY, []) if r.get('from') == '貼網址加入']
            ended = {I.NO_SHEET: '沒有交件單', I.TIMEOUT: '逾時', I.CRASHED: '當掉', I.STOPPED_BY_USER: '被停止'}.get(end)
            problem = f'判斷 agent 沒有交件({ended})' if ended else ''
            expect(self, 'J-16', (self.card() is not None, reported, self.home.status_file('add')['msg'],
                                  bool(self.card()['src']['verification'].get('report_issue'))),
                   (True, [(f'判斷沒有完成:{problem}', '在「貼網址加入」按「看紀錄」確認後再重試')] if problem else [],
                    '加進新職缺 1 張' + (f';判斷沒有完成:{problem}' if problem else ''), end != I.HANDED), end)
            if end != I.HANDED:      # 沒交那一列:每一欄都是沒有引段
                fields = self.card()['src']['verification']['fields']
                expect(self, 'J-06', {(r['claim'], r['raw'], r['state'], tuple(m['code'] for m in r['marks']))
                                      for r in fields.values()}, {('', '', '沒核對到', ('CITE_NONE',))}, end)

    def test_unreadable_board(self):
        gone = os.path.join(self.home.dir, 'gone.html')
        with self.assertRaises(OSError):
            add_job.main(['--board', gone, '--url', URL], browser=FakeBrowser())
        status = self.home.status_file('add')
        expect(self, 'J-16', (status['phase'], status['msg'], status['finished_at'] > 0),
               ('failed', f"沒加成:[Errno 2] No such file or directory: '{gone}'", True))



class PageFetchTest(FindCase):
    """J-02、I-13:agent 叫的 page_fetch 印出程式讀到的編號材料;讀不到印原因、結束碼 1。"""

    def test_prints_numbered_material_or_why(self):
        for read in I.URL_READ:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = page_fetch.main([URL], browser=FakeBrowser(fetched={URL: fetched(URL, read)}))
            text, readable = out.getvalue(), read in ('ok', 'partial')
            status = {'ok': 'read', 'partial': 'too_long'}.get(read, 'unreadable')
            why = {'partial': ['超過讀取上限'], 'unknown': ['要登入'], 'http_404': ['HTTP 404']}.get(read, [])
            lines = text.splitlines()
            expect(self, 'J-02', (code, lines[:2], re.fullmatch(r'讀網址 material_id=\w+', lines[2]) is not None,
                                  lines[3:]),
                   (0 if readable else 1, [page_fetch.READ_HINT if readable else page_fetch.UNREAD_HINT, ''], True,
                    [f'[] {URL} ({status})', *why,
                     *([f'{n}. {part}' for n, part in enumerate(PARTS, 1)] if readable else [])]), read)

    def test_browser_flag_reads_with_the_agent_browser_only(self):
        made = []

        def real(**kwargs):
            made.append(kwargs)
            return FakeBrowser(fetched={URL: fetched(URL, 'ok')})
        for argv, wanted in (([URL], False), ([URL, '--browser'], True)):
            with mock.patch.object(page_fetch, 'Real', real), contextlib.redirect_stdout(io.StringIO()):
                page_fetch.main(argv)
            expect(self, 'I-13', made.pop(), {'browser_only': wanted}, argv)


if __name__ == '__main__':
    unittest.main()
