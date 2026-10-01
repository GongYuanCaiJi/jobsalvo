#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""證據放在對的位置(#315):每張卡每一輪的證據集中放在那張卡自己的資料夾,所有會叫 agent 的流程同一種放法。

一輪的資料夾裡有:程式給的指示、agent 的動作紀錄、交件單、程式的比對結果、之後每次讀那一頁看到的樣子(連同程式自己截的圖),
events.jsonl 照時間排。要使用者確認的事都附程式自己截的那一頁;沒有就不叫他做,改回報「缺證據」。

測法:用假的 agent 的 Chrome(tests/fake_chrome.py)和假的 agent(換掉 agent_run 開行程、等行程那兩支,
agent_run.run 本身照真的跑),跑正式的入口,再打開那張卡的證據資料夾看。
"""
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
import agent_run as ar        # noqa: E402
import apply_run as run       # noqa: E402
import board_doc as bd        # noqa: E402
import evidence               # noqa: E402
import fake_chrome as fc      # noqa: E402
import form_record as fr      # noqa: E402

U = 'https://jobs.lever.co/evidence/1'
OKST = {'schema_version': 2, 'checked_links': True, 'issues': []}
PAGE = {'url': U + '/apply', 'fields': [
    {'label': 'Full name', 'value': 'Alex Chen'},
    {'label': 'Nationality', 'value': 'Taiwan'},
    {'label': 'Why do you want to join?', 'value': 'I like the mission.'}], 'lines': []}


def board():
    return {'__ans__': [{'k': 'nat', 'q': 'Nationality', 'v': 'Taiwan', 'zh': '台灣', 'at': run.today()}],
            U: {'app': 'ship', 'form': {'plat': 'Lever', 'at': '2020-01-01', 'f': [
                {'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
                {'q': 'Nationality', 'src': 'bank', 'k': 'nat'}]}}}


class FakeAgent:
    """假的 agent:寫一份 codex 格式的動作紀錄、照 fill 把交件單寫進輸出資料夾、把表單記進看板(agent 本來用 form_record 記)。
    fill:交件單(fill.json 的內容);fields:它記進看板的欄位;inferred:它推論、要他確認的答案。"""

    def __init__(self, fb, out, fill=None, fields=None, report=None):
        self.fb, self.out, self.report = fb, out, report      # report:(發生了什麼, 要他做什麼),它自己跑 agent_report 回報
        self.fill = fill if fill is not None else {'tab_id': '7', 'handoff': True, 'tab_url': U + '/apply',
                                                   'delivery': {'method': 'direct_upload'}}
        self.fields = fields
        self.tasks = []

    def launch(self, task, outfile, repo, agent, **kw):
        self.tasks.append(task)
        with open(outfile, 'a' if kw.get('append') else 'w', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'thread.started', 'thread_id': 'S1'}) + '\n')
            f.write(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '選了 Taiwan'}}, ensure_ascii=False) + '\n')
            f.write(json.dumps({'type': 'turn.completed'}) + '\n')
        os.makedirs(self.out, exist_ok=True)
        with open(os.path.join(self.out, 'fill.json'), 'w', encoding='utf-8') as f:
            json.dump(self.fill, f, ensure_ascii=False)
        fr.apply_record(self.fb, U, 'Lever', self.fields or [
            {'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
            {'q': 'Nationality', 'src': 'bank', 'k': 'nat'}], at=run.now())
        if self.report:
            import agent_report
            with patch.dict(os.environ, {ar.REPORT_FROM_ENV: ar._REPORT_FROM.get() or 'x'}), \
                 patch.object(sys, 'argv', ['agent_report.py', '--from', 'x', '--job', U, '--need', self.report[1],
                                            self.report[0]]):
                agent_report.main()
        return SimpleNamespace(pid=4242)

    @staticmethod
    def wait(procs, timeout=None, paused=None):
        return [SimpleNamespace(status='completed', returncode=0, pid=4242)]


class Fill(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(tempfile.TemporaryDirectory(prefix='evidence-'))
        self.out = os.path.join(self.home, 'apply-out')
        root = patch.object(evidence, 'root', return_value=os.path.join(self.home, 'evidence'))
        root.start()
        self.addCleanup(root.stop)

    def fill(self, door, agent=None, fb=None):
        fb = fb if fb is not None else board()
        jobs = {U: {'id': U, 'target': 'Engineer · Example'}}
        agent = agent or FakeAgent(fb, self.out)
        with fc.installed(door), \
             patch.object(fr, 'board_status', return_value=OKST), \
             patch.object(run, 'load', side_effect=lambda b: (jobs, fb)), \
             patch.object(run, 'out_dir', return_value=self.out), \
             patch.object(run, 'prompt_for', return_value=('指示:照答案庫填 Nationality', self.out)), \
             patch('profile_sync.check_attachments', return_value=[]), \
             patch.object(ar, 'launch', side_effect=agent.launch), \
             patch.object(ar, 'wait_done', side_effect=agent.wait), \
             patch.object(bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch('time.sleep'):                                         # 截不到圖時的重試不用真的等
            result = run.run_one('fill', U, '/tmp/board.html')
        return result, fb

    def test_a_fill_round_leaves_its_whole_evidence_in_the_cards_folder_in_time_order(self):
        (ok, msg), fb = self.fill(fc.FakeChrome('codex', page=PAGE))
        self.assertTrue(ok, msg)
        rounds = _env.evidence_rounds(U, '/tmp/board.html')
        self.assertEqual(len(rounds), 1)
        rnd = rounds[0]
        self.assertEqual(os.path.dirname(rnd), evidence.card_dir(U, '/tmp/board.html'))
        kinds = [e['kind'] for e in _env.evidence_events(rnd)]
        # 照發生的順序:先給指示、agent 做完交件、程式讀頁核對、最後是比對結果
        self.assertEqual([k for k in kinds if k in ('instruction', 'agent_log', 'handoff', 'page', 'check')],
                         ['instruction', 'agent_log', 'handoff', 'page', 'check'])
        by = {e['kind']: e for e in _env.evidence_events(rnd)}
        with open(os.path.join(rnd, by['instruction']['file']), encoding='utf-8') as f:
            self.assertIn('照答案庫填 Nationality', f.read())
        with open(os.path.join(rnd, by['agent_log']['file']), encoding='utf-8') as f:
            self.assertIn('選了 Taiwan', f.read())                     # agent 的動作紀錄一定找得到:複製進來
        with open(os.path.join(rnd, by['handoff']['file']), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['tab_id'], '7')
        page = by['page']
        with open(os.path.join(rnd, page['file']), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['fields'][1]['value'], 'Taiwan')
        self.assertTrue(os.path.isfile(os.path.join(rnd, page['shot'])))   # 讀頁跟程式截的圖綁在同一筆
        self.assertEqual(by['check']['problems'], [])


WHY = {'q': 'Why do you want to join?', 'src': 'new', 'v': 'I like the mission.', 'zh': '我喜歡這個使命。',
       'why': '照他的履歷推論', 'kind': 'txt'}


class QuestionsForHimCarryTheScreenshot(unittest.TestCase):
    """agent 推論、要他確認的題目:附程式自己截的那一頁,題目要在程式同一刻讀到的頁面上。
    沒有截圖、或頁面上找不到這一題:不叫他確認(不在要你處理的清單),改回報「缺證據」;這張照樣不能確認送出。"""

    setUp = Fill.setUp
    fill = Fill.fill

    def agent(self, fb):
        return FakeAgent(fb, self.out, fields=[{'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
                                                {'q': 'Nationality', 'src': 'bank', 'k': 'nat'}, WHY])

    def inferred(self, fb):
        return next(e for e in fb['__ans__'] if e.get('q') == WHY['q'])

    def reports(self, fb):
        return [it for it in fb.get('__inbox__', []) if not it.get('done')]

    def test_a_question_on_the_page_it_shot_is_asked_with_that_screenshot(self):
        fb = board()
        (ok, msg), fb = self.fill(fc.FakeChrome('codex', page=PAGE), self.agent(fb), fb)
        e = self.inferred(fb)
        self.assertIn(e, [x for x, _ in fr.find_pending(fb)])            # 照樣叫他確認
        shot = evidence.path(U, e['ev']['f'], '/tmp/board.html')          # 看板點得開的那張,在這張卡的證據夾
        self.assertTrue(shot and os.path.isfile(shot))
        rnd = os.path.dirname(shot)
        page = next(x for x in _env.evidence_events(rnd) if x['kind'] == 'page')
        self.assertEqual(page['shot'], os.path.basename(shot))             # 就是程式讀到那一頁的同一刻截的
        self.assertNotIn('缺證據', ' '.join(it['msg'] for it in self.reports(fb)))

    def test_no_screenshot_means_it_is_not_asked_and_is_reported_as_missing_evidence(self):
        fb = board()
        (ok, msg), fb = self.fill(fc.FakeChrome('codex', page=PAGE, not_now={'shot'}), self.agent(fb), fb)
        e = self.inferred(fb)
        self.assertNotIn(e, [x for x, _ in fr.find_pending(fb)])          # 不在要你處理的清單
        self.assertNotIn('ev', e)
        self.assertTrue(fr.answers_pending(fb, U))                         # 還沒人確認:這張照樣不能確認送出
        missing = [it for it in self.reports(fb) if '缺證據' in it['msg']]
        self.assertEqual(len(missing), 1)
        self.assertIn(WHY['q'], missing[0]['msg'])
        self.assertEqual(missing[0]['job'], U)

    def test_a_question_that_is_not_on_the_page_it_read_is_not_asked(self):
        fb = board()
        page = dict(PAGE, fields=PAGE['fields'][:2])                      # agent 說有這一題,頁面上沒有
        (ok, msg), fb = self.fill(fc.FakeChrome('codex', page=page), self.agent(fb), fb)
        e = self.inferred(fb)
        self.assertNotIn(e, [x for x, _ in fr.find_pending(fb)])
        self.assertTrue(any('缺證據' in it['msg'] and WHY['q'] in it['msg'] for it in self.reports(fb)))



class EveryThingForHimCarriesTheScreenshot(unittest.TestCase):
    """要他確認或處理的每一種(agent 回報、沒跑完的那一輪、送出結果):附程式自己截的那一頁,存在那張卡的證據夾,看板點得開。"""

    setUp = Fill.setUp
    fill = Fill.fill

    def opens(self, rel):
        path = evidence.path(U, rel, '/tmp/board.html')
        self.assertTrue(path and os.path.isfile(path), rel)
        self.assertTrue(os.path.dirname(os.path.dirname(path)) == evidence.card_dir(U, '/tmp/board.html'))
        return path

    def test_a_report_after_a_round_carries_the_page_it_shot(self):
        page = dict(PAGE, fields=[PAGE['fields'][0]])                     # Nationality 沒填上:驗收不過,程式回報
        (ok, msg), fb = self.fill(fc.FakeChrome('codex', page=page))
        self.assertFalse(ok)
        it = next(x for x in fb['__inbox__'] if not x.get('done') and x.get('job') == U)
        self.opens(it['ev'])

    def test_an_agent_report_without_a_program_screenshot_is_missing_evidence_not_a_todo(self):
        # 查應徵進度、找缺這種程式沒截那一頁的流程:agent 自己的回報照樣列出來,但標「缺證據」,不叫他照做(#315)
        import agent_report
        fb = board()
        with patch.dict(os.environ, {ar.REPORT_FROM_ENV: '查回音'}), \
             patch.object(sys, 'argv', ['agent_report.py', '--from', '查回音', '--job', U, '--need', '去登入信箱',
                                        '信箱要本人登入']), \
             patch.object(bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)):
            agent_report.main()
        it = next(x for x in fb['__inbox__'] if x['msg'] == '信箱要本人登入')
        self.assertTrue(it.get('noev'))
        self.assertIn(it, agent_report.open_items(fb))                     # 看得到
        self.assertNotIn(it, agent_report.todo(fb))                        # 不是要你處理的
        program = agent_report.apply_report(fb, '查回音', '查回音 agent 沒完成', need='看紀錄', job=U)
        self.assertIn(program, agent_report.todo(fb))                      # 程式自己驗出來的照舊要你處理

    def test_the_agents_own_report_during_a_fill_carries_the_page_it_shot(self):
        fb = board()
        agent = FakeAgent(fb, self.out, report=('這一頁要他本人登入', '登入'))
        page = dict(PAGE, fields=[PAGE['fields'][0]])                     # 沒填完:這張的回報留著給他看
        (ok, msg), fb = self.fill(fc.FakeChrome('codex', page=page), agent, fb)
        it = next(x for x in fb['__inbox__'] if x['msg'] == '這一頁要他本人登入')
        self.opens(it['ev'])
        self.assertNotIn('noev', it)
        import agent_report
        self.assertIn(it, agent_report.todo(fb))

    def test_the_card_line_after_a_round_links_the_page_it_shot(self):
        (ok, msg), fb = self.fill(fc.FakeChrome('codex', page=PAGE))
        self.assertTrue(ok, msg)
        self.opens(fb[U]['apply']['ev'])

    def test_a_round_that_did_not_finish_carries_the_page_as_it_was_left(self):
        fb = board()
        fb[U]['apply'] = {'stage': 'fill', 'tab_id': '7', 'session': 'S0', 'runtime': 'codex', 'at': '2020-01-01'}
        agent = FakeAgent(fb, self.out)
        agent.wait = lambda procs, timeout=None, paused=None: [SimpleNamespace(status='failed', returncode=1, pid=4242)]
        (ok, msg), fb = self.fill(fc.FakeChrome('codex', page=PAGE), agent, fb)
        self.assertFalse(ok)
        self.assertIn(fb[U]['ds'], ('stuck', 'nopage'))                   # 這一輪沒成
        self.opens(fb[U]['apply']['ev'])                                   # agent 開到一半的那一頁,程式自己截的

    def test_the_submit_result_carries_the_page_it_shot(self):
        fb = board()
        fr.apply_record(fb, U, 'Lever', [{'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
                                          {'q': 'Nationality', 'src': 'bank', 'k': 'nat'}], at=run.now())
        fb[U]['apply'] = {'stage': 'fill', 'issues': [], 'tab_id': '7', 'session': 'S1', 'runtime': 'codex',
                          'tab_url': U + '/apply', 'delivery': {'method': 'direct_upload'}, 'at': run.now()}
        fb[U]['ds'] = 'confirmed'
        fb[U]['approve'] = {'at': run.today(), 'snap': fr.snapshot(fb, U)}
        agent = FakeAgent(fb, self.out, fill=None)
        door = fc.FakeChrome('codex', page=PAGE)

        def launch(task, outfile, repo, agent_entry, **kw):
            # 按完送出之後頁面換了、又沒有確認頁的字:程式也判斷不了,才是送出結果不明(#316)
            door.page = {'url': U + '/apply/processing', 'fields': [], 'lines': ['Processing']}
            with open(outfile, 'w', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'thread.started', 'thread_id': 'S1'}) + '\n')
            os.makedirs(self.out, exist_ok=True)
            with open(os.path.join(self.out, 'submit.json'), 'w', encoding='utf-8') as f:
                json.dump({'submitted': False, 'problems': ['按了送出,頁面沒反應']}, f, ensure_ascii=False)
            return SimpleNamespace(pid=4242)
        agent.launch = launch
        jobs = {U: {'id': U, 'target': 'Engineer · Example'}}
        with fc.installed(door), \
             patch.object(fr, 'board_status', return_value=OKST), \
             patch.object(run, 'load', side_effect=lambda b: (jobs, fb)), \
             patch.object(run, 'out_dir', return_value=self.out), \
             patch.object(ar, 'launch', side_effect=agent.launch), \
             patch.object(ar, 'wait_done', side_effect=agent.wait), \
             patch.object(bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)):
            ok, msg = run.run_one('submit', U, '/tmp/board.html')
        self.assertFalse(ok)
        self.assertEqual(fb[U]['ds'], 'unsure')
        self.opens(fb[U]['apply']['submit_fail']['ev'])



def agent_writes(handoffs=None, session='S1'):
    """換掉 agent_run 開行程、等行程那兩支(agent_run.run 本身照真的跑):寫一份動作紀錄;
    handoffs(outfile) 回 {路徑: 內容},這一輪 agent 交的檔。"""
    def launch(task, outfile, repo, agent, **kw):
        with open(outfile, 'a' if kw.get('append') else 'w', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'thread.started', 'thread_id': session}) + '\n')
            f.write(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}}) + '\n')
            f.write(json.dumps({'type': 'turn.completed'}) + '\n')
        for path, data in (handoffs(outfile) if handoffs else {}).items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        return SimpleNamespace(pid=4242)
    return (patch.object(ar, 'launch', side_effect=launch),
            patch.object(ar, 'wait_done', return_value=[SimpleNamespace(status='completed', returncode=0, pid=4242)]))


def same_shape(test, rnd, kinds=('instruction', 'agent_log', 'handoff')):
    """每一種流程同一種格式:一輪一夾、events.jsonl 照時間排、檔都在夾裡。"""
    test.assertRegex(os.path.basename(rnd), evidence.ROUND)
    rows = _env.evidence_events(rnd)
    got = [e['kind'] for e in rows]
    for kind in kinds:
        test.assertIn(kind, got)
    for e in rows:
        if e.get('file'):
            test.assertTrue(os.path.isfile(os.path.join(rnd, e['file'])), e)
    return rows


class OtherFlows(unittest.TestCase):
    """其他會叫 agent 的流程,各跑一個最小情境:證據放在同一種位置(那張卡的證據夾;沒有卡的放那個流程自己的夾)、同一種格式。"""

    setUp = Fill.setUp

    def test_an_agent_dispatched_without_a_round_still_leaves_its_evidence(self):
        a, b = agent_writes()
        with a, b, patch('sys.argv', ['some_flow.py']):
            self.assertTrue(ar.run('做一件事', os.path.join(self.home, 'x.out'), self.home).ok)
        home = evidence.flow_dir('some_flow')
        [rnd] = [os.path.join(home, n) for n in os.listdir(home)]
        same_shape(self, rnd, ('instruction', 'agent_log'))

    def test_one_round_about_several_cards_is_in_each_cards_folder(self):
        cards = ['https://ex.test/job/1', 'https://ex.test/job/2']
        a, b = agent_writes()
        with a, b, evidence.opened('reply', 'check', cards) as rnd:
            ar.run('查這兩張', os.path.join(self.home, 'r.out'), self.home)
            rnd.handoff(os.path.join(self.home, 'none.json'))
        for u in cards:
            [one] = _env.evidence_rounds(u)
            rows = same_shape(self, one, ('instruction', 'agent_log'))
            self.assertIn('none.json', [e.get('missing') for e in rows])          # 沒交件單也照實記

    def test_find_jobs_judging_puts_each_batch_in_its_cards_folders(self):
        import research as rs
        cands = [{'url': 'https://ex.test/job/%d' % i, 'title': 'Engineer', 'company': 'Acme', 'jd': 'JD'} for i in range(2)]
        rd = os.path.join(self.home, 'round')
        os.makedirs(rd)
        a, b = agent_writes(lambda of: {of.replace('.out', '.json'): []})
        with a, b:
            rs.judge(cands, [], rd, False, lambda p, of, req: ar.run(p, of, self.home), mode='add')
        for c in cands:
            [one] = _env.evidence_rounds(c['url'])
            same_shape(self, one, ('instruction', 'agent_log', 'handoff', 'check'))      # 安檢門的比對結果也在同一輪(#317)

    def test_find_jobs_search_goes_to_its_own_folder(self):
        import research as rs
        rd = os.path.join(self.home, 'round')
        os.makedirs(rd)
        a, b = agent_writes(lambda of: {of.replace('.out', '.json'): []})
        with a, b, patch.object(rs, 'search_prompt', return_value='去找缺'), \
             patch.object(rs, 'search_files', return_value={}):
            rs.run_search('deep', '', [], [], '', rd, False, lambda p, of, req: ar.run(p, of, self.home))
        home = evidence.flow_dir('research')
        [rnd] = [os.path.join(home, n) for n in os.listdir(home)]
        same_shape(self, rnd, ('instruction', 'agent_log', 'handoff', 'check'))


    def board_file(self, jobs, fb=None):
        return _env.make_board(os.path.join(self.home, 'board.html'), fb, jobs=jobs)

    def test_category_suggestions_have_no_card_and_go_to_their_own_folder(self):
        import suggest_cats
        board_path = self.board_file([{'id': 'https://ex.test/job/1', 'target': 'Engineer · Acme'}])
        out = os.path.join(self.home, 'suggest.json')
        a, b = agent_writes(lambda of: {out: {'categories': [{'name': '後端'}]}})
        # 指示照常組不起來(suggest_cats.prompt 叫了 prefs 裡沒有的 title_of,另案);這裡只看證據放哪
        with a, b, patch.object(suggest_cats, 'SP', self.home), patch.object(suggest_cats.jobrun, 'write'), \
             patch.object(suggest_cats, 'prompt', return_value='幫他分類'), \
             patch('sys.argv', ['suggest_cats.py', '--board', board_path]):
            suggest_cats.main()
        home = evidence.flow_dir('suggest_cats')
        [rnd] = [os.path.join(home, n) for n in os.listdir(home)]
        same_shape(self, rnd, ('instruction', 'agent_log', 'handoff', 'check'))

    def test_posting_dates_go_to_each_cards_folder(self):
        import page_fetch
        import posted_age
        u = 'https://ex.test/job/1'
        board_path = self.board_file([{'id': u, 'target': 'Engineer · Acme'}])
        result = os.path.join(self.home, 'posted.json')
        page = page_fetch.PageResult(u, 'ok', text='Posted on 2026-09-01\nWe build things', via='direct')
        a, b = agent_writes(lambda of: {result: {'dates': []}})
        with a, b, patch.object(posted_age, 'CACHE', os.path.join(self.home, 'cache.json')), \
             patch.object(posted_age, 'RESULT', result), patch.object(posted_age, 'LOG', os.path.join(self.home, 'p.log')), \
             patch.object(page_fetch, 'fetch_many', return_value=[page]):
            posted_age.main(['--board', board_path])
        [rnd] = _env.evidence_rounds(u, board_path)
        same_shape(self, rnd, ('instruction', 'agent_log', 'handoff', 'check'))

    def test_job_closed_checks_go_to_each_cards_folder_with_the_gate_result(self):
        """職缺關了沒(#317):每一小批的指示、動作紀錄、交件單、安檢門的比對結果記進每一張卡的證據夾。"""
        import board_status
        import page_fetch
        u = 'https://ex.test/job/1'
        board_path = self.board_file([{'id': u, 'target': 'Engineer · Acme'}])
        page = page_fetch.PageResult(u, 'ok', text='Engineer. Apply now.', via='direct')

        def out_of(of):
            return {os.path.join(os.path.dirname(of), 'verdicts.json'): {'jobs': [{'id': 'J1', 'status': 'live'}]}}
        a, b = agent_writes(out_of)
        with a, b:
            got = board_status._agent_link_batch({u: page}, board_path)
        self.assertEqual(got[u][0], 'live')
        [rnd] = _env.evidence_rounds(u, board_path)
        same_shape(self, rnd, ('instruction', 'agent_log', 'handoff', 'check'))

    def test_preparing_resumes_puts_each_cards_own_handoff_in_its_folder(self):
        import cut_tailor
        rows = [['https://ex.test/job/1', 'A'], ['https://ex.test/job/2', 'B']]
        sp = os.path.join(self.home, 'sp')
        os.makedirs(sp)
        for name, data in (('cut_tailor_rows.json', rows),):
            with open(os.path.join(sp, name), 'w', encoding='utf-8') as f:
                json.dump(data, f)
        with open(os.path.join(sp, 'cut_tailor_t0'), 'w') as f:
            f.write('0')
        with open(os.path.join(sp, 'cut_tailor_prompt.txt'), 'w', encoding='utf-8') as f:
            f.write('準備這兩張')
        out = os.path.join(self.home, 'prepare')
        own = os.path.join(out, cut_tailor.card.card_id_from_url(rows[0][0]), 'fill.json')
        a, b = agent_writes(lambda of: {own: {'keep': True}})
        with a, b, patch.multiple(cut_tailor, SP=sp, ROWSF=os.path.join(sp, 'cut_tailor_rows.json'),
                                  CHOICESF=os.path.join(sp, 'c.json'), CACHEDF=os.path.join(sp, 'd.json'),
                                  STATUSF=os.path.join(sp, 's.json'), PIDF=os.path.join(sp, 'p'),
                                  WORKER_PIDF=os.path.join(sp, 'w')), \
             patch.object(ar, 'wait_done', return_value=[SimpleNamespace(status='failed', returncode=1, pid=4242)]), \
             patch.object(cut_tailor, '_report'):
            cut_tailor.run_finish(None, out_dir=out)
        first, second = (same_shape(self, _env.evidence_rounds(u)[0], ('instruction', 'agent_log', 'handoff'))
                         for u, _t in rows)
        self.assertEqual([e['src'] for e in first if e['kind'] == 'handoff'], ['fill.json'])   # 只有它自己那一份
        self.assertEqual([e.get('missing') for e in second if e['kind'] == 'handoff'], ['fill.json'])


if __name__ == '__main__':
    unittest.main()
