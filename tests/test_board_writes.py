#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寫看板檔撞在一起(#308):每一個寫看板檔的地方都拿同一把鎖、走同一個寫入,
同時寫不會掉資料、不會拼出狀態表不准存在的組合。

「同時」不靠碰運氣:第一個寫入讀完看板、正要寫回的那一刻(組回文件那一步),另一個寫入插進來;
插進來的那一個最多等半秒(拿不到鎖就是被擋住了,等第一個寫完才輪到它)。"""
import copy
import functools
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾
from _env import read_fb, read_board  # noqa: E402

import board_doc as bd  # noqa: E402

JOBS = [{'id': 'https://ex.test/job/%d' % i, 'target': 'Job %d' % i} for i in range(1, 4)]
U = JOBS[0]['id']
CSS = ':root{--a:1}'
JS = 'JSON.parse(x); renderApp();'
HDR = '<div id="savebar"></div><b id="stat-first">0</b>'


make_board = functools.partial(_env.make_board, jobs=JOBS, sty=CSS)


def race(first, second, wait=0.5):
    """first 讀完看板、組回文件要寫之前,second 在另一條執行緒插進來寫(最多等 wait 秒)。"""
    real = bd.assemble
    fired = []

    def hooked(*a, **k):
        if not fired:
            fired.append(threading.Thread(target=second))
            fired[0].start()
            fired[0].join(wait)
        return real(*a, **k)
    with mock.patch.object(bd, 'assemble', hooked):
        first()
    for t in fired:
        t.join(5)
    return fired


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory(prefix='boardwrites-'))
        self.path = os.path.join(self.dir, 'board.html')
        make_board(self.path, {U: {'s': 'like'}})

    def add_mark(self, key='__new__', value=1):
        return lambda: bd.set_fb(lambda fb: fb.__setitem__(key, value), live=self.path, by='test')


TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
# 組出一份看板文件、卻不是寫回現行看板的地方:回給瀏覽器的頁面、從零生一份新的看板(還沒有人在用)
MAKES_A_NEW_DOC = {
    ('board_server.py', 'serve_doc'),                      # 送頁面(不寫檔)
    ('init.py', 'empty_board'), ('demo.py', 'build'),      # 第一次建看板、示範資料
    ('apply_profile_accept.py', '_acceptance_board_document'),
    ('board_check.py', 'check_bank_export_and_history'),   # 看板檢查在暫存資料夾生全新的看板
}


class SameLockSameWrite(Tmp):
    def test_only_board_doc_writes_the_board_file(self):
        """組看板文件(bd.assemble)寫回檔案只准在 board_doc.rewrite 裡:其他地方各自「讀 → 組 → 換檔」就沒拿同一把鎖。"""
        import ast
        found = set()
        for name in sorted(os.listdir(TOOLS)):
            if not name.endswith('.py') or name == 'board_doc.py':
                continue
            with open(os.path.join(TOOLS, name), encoding='utf-8') as f:
                tree = ast.parse(f.read())

            def walk(node, fn):
                for c in ast.iter_child_nodes(node):
                    inner = c.name if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)) and not fn else fn
                    if isinstance(c, ast.Call) and ast.unparse(c.func).split('.')[-1] == 'assemble':
                        found.add((name, inner))
                    walk(c, inner)
            walk(tree, '')
        self.assertEqual(found - MAKES_A_NEW_DOC, set(), '這些地方自己組看板文件寫檔:改走 board_doc.rewrite / set_fb / set_data')


    def test_irreversible_conversions_go_through_the_restore_point_entry(self):
        """伺服器起來時的轉換(清過時的回報與答案)回不了頭:只准在 board_server.migrate_marks 裡做,
        而且它寫回看板只准透過 folder_history.convert(先留退回點,沒有就不轉)。新增的轉換照樣要掛進 migrate_marks。"""
        import ast
        conversions = {'sweep'}
        found = set()
        for name in sorted(os.listdir(TOOLS)):
            if not name.endswith('.py') or name == 'board_check.py':
                continue
            with open(os.path.join(TOOLS, name), encoding='utf-8') as f:
                tree = ast.parse(f.read())
            outer = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            outer += [m for cls in tree.body if isinstance(cls, ast.ClassDef) for m in cls.body
                      if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            for fn in outer:   # 算在最外層那個函式頭上(migrate_marks 裡的 mut 算 migrate_marks)
                if fn.name in conversions:
                    continue
                for c in ast.walk(fn):
                    if isinstance(c, ast.Call) and ast.unparse(c.func).split('.')[-1] in conversions:
                        found.add((name, fn.name))
        found -= {('board_server.py', 'migrate_marks')}
        self.assertEqual(found, set(), '這些地方自己做舊資料轉換:掛進 board_server.migrate_marks')

        with open(os.path.join(TOOLS, 'board_server.py'), encoding='utf-8') as f:
            tree = ast.parse(f.read())
        marks = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'migrate_marks')
        converts = [c for c in ast.walk(marks) if isinstance(c, ast.Call) and ast.unparse(c.func) == 'folder_history.convert']
        inside = {id(c) for conv in converts for c in ast.walk(conv)}
        writes = [c for c in ast.walk(marks) if isinstance(c, ast.Call)
                  and ast.unparse(c.func).split('.')[-1] in ('set_fb', 'rewrite', 'set_data', 'write_doc')]
        self.assertTrue(writes)
        self.assertEqual([ast.unparse(c) for c in writes if id(c) not in inside], [],
                         'migrate_marks 寫回看板要包在 folder_history.convert 裡')

    def test_reinstalling_the_shell_keeps_a_mark_saved_meanwhile(self):
        """可投遞夾重建時重灌外殼(apply_shell.apply_to)以前沒拿看板鎖:讀完看板、寫回之前他在手機上存的標記被蓋掉。"""
        import apply_shell
        race(lambda: apply_shell.apply_to(self.path, CSS, JS, HDR), self.add_mark())
        self.assertEqual(read_fb(self.path).get('__new__'), 1)
        self.assertIn('renderApp', read_board(self.path)['app'])


class ManualSent(unittest.TestCase):
    """「我已在外部送出」每一階都能按:不用走完準備履歷、待你決定、可以投了;但要記寄出的是哪一份,沒挑履歷就擋。"""

    def setUp(self):
        import board_server as bs
        _env.use_home(self, resume={'langs': ['zh', 'en'], 'resumes': [
            {'id': 'a', 'name': 'A', 'files': {'zh': 'resume/a-zh.md', 'en': 'resume/a-en.md'}, 'enabled': True}]})
        self.bs = bs
        self.n = 0
        self.enterContext(mock.patch.object(bs, 'trigger_build', lambda: None))

    def sent_manually(self, mark):
        """他在還沒進流程、準備履歷中、待你決定任何一階按「我已在外部送出」。回 (被擋的事件, 存好的卡)。"""
        self.n += 1
        self.path = os.path.join(self.tmp, f'sent{self.n}.html')
        make_board(self.path, {U: dict(mark)})
        self.enterContext(mock.patch.object(self.bs, 'STATE', self.path))
        rejected = self.bs.write_fb([{'u': U, 'ev': 'sent_manual',
                                      'data': {'by': 'manual', 'at': '2026-10-02T10:00:00', 'sent_at': '2026-10-02'}}])
        return rejected, read_fb(self.path)[U]

    def test_every_stage_can_mark_sent_with_the_resume_the_user_picked(self):
        for app in (None, 'prep', 'ready', 'ship'):
            mark = {'s': 'like', 'resume_id': 'a', 'lang': 'en'}
            if app:
                mark['app'] = app
            rejected, card = self.sent_manually(mark)
            self.assertEqual((rejected, card['app'], card['sent_v']), ([], 'sent', 'en-a'), app)

    def test_marking_sent_without_a_resume_asks_which_one_and_changes_nothing(self):
        for app in (None, 'prep', 'ready'):
            rejected, card = self.sent_manually({'s': 'like', **({'app': app} if app else {})})
            self.assertEqual([r['ev'] for r in rejected], ['sent_manual'], app)
            self.assertIn('挑一份履歷', rejected[0]['msg'])
            self.assertNotEqual(card.get('app'), 'sent')


with open(os.path.join(HERE, 'fixtures', 'delivery-cards.json'), encoding='utf-8') as _f:
    FIX = json.load(_f)
CARD = FIX['url']


class TwoDevices(unittest.TestCase):
    """看板存檔跟後台(agent、程式)同時改同一張卡:看板只送事件,後台照卡片當下的狀態套表;
    結果一定是狀態表允許的一種狀態、卡上的資料對得回那個狀態(delivery_state.problems 是空的),沒有事件被蓋掉。"""

    def setUp(self):
        import board_server as bs
        self.bs = bs
        self.dir = self.enterContext(tempfile.TemporaryDirectory(prefix='twodevices-'))
        self.path = os.path.join(self.dir, 'board.html')
        self._state, self._tb = bs.STATE, bs.trigger_build
        bs.STATE, bs.trigger_build = self.path, (lambda: None)

    def tearDown(self):
        self.bs.STATE, self.bs.trigger_build = self._state, self._tb

    def board(self, state):
        _env.make_board(self.path, {CARD: copy.deepcopy(FIX['cards'][state]), '__ans__': copy.deepcopy(FIX['ans'])},
                        jobs=[{'id': CARD, 'target': 'x'}], sty=CSS)
        return copy.deepcopy(FIX['cards'][state])

    def card(self):
        return read_fb(self.path).get(CARD) or {}

    def backend(self, event, **data):
        """後台(agent 回報、程式偵測)送一個事件:跟 apply_run、chrome_door 同一條路。"""
        import delivery_state as ds
        d = dict(copy.deepcopy(FIX['data'].get(event) or {}), **data)
        bd.set_fb(lambda fb: ds.try_fire(fb, CARD, event, **d), live=self.path, by='test')

    def phone(self, event, sets=None, extra=None):
        """手機上的看板(載入之後別處已經改過這張卡)按了一下:送指令(事件 + 跟著改的欄)。回被擋的那幾道。"""
        cmd = dict({'ev': event, 'data': copy.deepcopy(FIX['data'].get(event) or {})} if event else {}, u=CARD)
        if sets:
            cmd['set'] = sets
        return self.bs.write_fb(([cmd] if event or sets else []) + (extra or []))

    def assertAllowed(self, want):
        import delivery_state as ds
        m = self.card()
        self.assertEqual(ds.state(m), want)
        self.assertEqual(ds.problems(m), [])
        return m

    # 手機按「退回」(離開可以投了)的同時 agent 送出成功
    def test_back_while_the_agent_is_sending_then_it_succeeds(self):
        self.board('confirmed')
        self.backend('submit_start')                       # 8 秒到了,agent 開始送
        rejected = self.phone('leave', {'app': 'ready'})
        self.assertEqual([r['ev'] for r in rejected], ['leave'])
        m = self.assertAllowed('sending')
        self.assertEqual(m.get('app'), 'ship', '退回沒發生,卡不能一半在退回、一半在正在送出')
        self.backend('submit_ok')
        m = self.assertAllowed('sent')
        self.assertTrue(m['apply'].get('sent'), 'agent 的送出證據留著')

    def test_back_arrives_after_the_agent_already_sent(self):
        self.board('confirmed')
        self.backend('submit_start')
        self.backend('submit_ok')
        self.phone('leave', {'app': 'ready'})
        m = self.assertAllowed('sent')
        self.assertEqual(m.get('app'), 'sent')
        self.assertTrue(m['apply'].get('sent'))

    def test_back_first_then_the_timer_cannot_send(self):
        import form_record as fr
        self.board('confirmed')
        self.phone('leave', {'app': 'ready'})
        why = []
        bd.set_fb(lambda fb: why.append(fr.start_submit(fb, CARD)), live=self.path, by='test')
        self.assertTrue(why[0], '退回之後不能再開始送出')
        m = self.assertAllowed('parked')
        self.assertEqual(m.get('app'), 'ready')

    # 同時改答案與 agent 記表單
    def test_answer_change_while_the_agent_records_a_new_form(self):
        self.board('parked')
        new_form = {'plat': 'x', 'f': [{'q': 'Why?', 'src': 'bank', 'k': 'k1'},
                                       {'q': 'When?', 'src': 'bank', 'k': 'k2'}]}
        bd.set_fb(lambda fb: fb[CARD].__setitem__('form', copy.deepcopy(new_form)), live=self.path, by='agent')
        rejected = self.phone(None, extra=[{'refill': 'k1'}])    # 看板不送表單,只送「這條答案改了」
        self.assertEqual(rejected, [], 'agent 記表單不算撞到他改答案')
        m = self.assertAllowed('parked')
        self.assertEqual([x['k'] for x in m['form']['f']], ['k1', 'k2'], 'agent 剛記的新表單不能被換回手機上的舊表單')
        self.assertEqual([x.get('refill') for x in m['form']['f']], [1, None], '改過的答案在新表單上照樣標重打')

    def test_refill_mark_only_where_the_page_is_still_up(self):
        self.board('parked')
        bd.set_fb(lambda fb: fb[CARD]['form']['f'][0].pop('refill'), live=self.path, by='test')
        self.backend('page_lost')
        self.phone(None, extra=[{'refill': 'k1'}])
        m = self.assertAllowed('gone')
        self.assertFalse(any(x.get('refill') for x in m['form']['f']), '頁已經不在,沒有網頁可重打')

    # 同時確認與頁面不見了
    def test_confirm_after_the_page_was_lost(self):
        self.board('parked')
        self.backend('page_lost')
        rejected = self.phone('confirm')
        self.assertEqual([r['ev'] for r in rejected], ['confirm'])
        m = self.assertAllowed('gone')
        self.assertNotIn('approve', m)

    def test_page_lost_after_confirm(self):
        self.board('parked')
        self.phone('confirm')
        self.backend('page_lost')
        m = self.assertAllowed('gone')
        self.assertNotIn('approve', m)

    # 8 秒計時器和取消確認撞在一起
    def test_unconfirm_after_the_timer_started_sending(self):
        self.board('confirmed')
        self.backend('submit_start')
        rejected = self.phone('unconfirm')
        self.assertEqual([r['ev'] for r in rejected], ['unconfirm'])
        self.assertAllowed('sending')

    def test_timer_after_unconfirm_does_not_send(self):
        import form_record as fr
        self.board('confirmed')
        self.phone('unconfirm')
        why = []
        bd.set_fb(lambda fb: why.append(fr.start_submit(fb, CARD)), live=self.path, by='test')
        self.assertTrue(why[0])
        self.assertAllowed('parked')


class GoneSweepInLock(Tmp):
    """掃「頁面不見了」以前拿舊快照算、直接寫:算完到寫進去之間剛填好的新頁(新的工作區)會被誤標成不見。"""
    OLD, NEW = {'id': 1, 'name': 'old', 'page': 'p1'}, {'id': 2, 'name': 'new', 'page': 'p1'}

    def seed(self, workspace):
        rec = {'stage': 'fill', 'at': '2026-09-29T17:06:02', 'tab_id': '1:p1', 'workspace': workspace, 'runtime': 'codex'}
        make_board(self.path, {U: {'app': 'ship', 'ds': 'parked', 'apply': rec}})

    def sweep(self, snapshot):
        import autopilot as ap
        import chrome_door
        old_gone = lambda fb, *a, **k: [u for u, m in fb.items()                       # noqa: E731 — 只有舊工作區不在了
                                        if ((m.get('apply') or {}).get('workspace') or {}).get('id') == self.OLD['id']]
        pilot = ap.Pilot(self.path, None, lambda k: {}, None, lambda: True)
        import board_doc as bd
        with mock.patch.object(chrome_door, 'gone_pages', old_gone), mock.patch.object(bd, 'LIVE', self.path):
            pilot._sweep_gone(snapshot)                             # 這份是現行看板(副本 sweep_gone 不動)

    def test_a_page_filled_after_the_snapshot_is_not_marked_gone(self):
        self.seed(self.OLD)
        stale = read_fb(self.path)
        self.seed(self.NEW)                                         # 快照之後,新的工作區重填好了這一張
        self.sweep(stale)
        self.assertEqual(read_fb(self.path)[U]['ds'], 'parked', '剛填好的新頁不能被當成不見了')

    def test_a_page_from_a_closed_workspace_is_still_marked(self):
        self.seed(self.OLD)
        self.sweep(read_fb(self.path))
        self.assertEqual(read_fb(self.path)[U]['ds'], 'gone')


class NoPauseWhileSending(unittest.TestCase):
    """正在送出時不准暫停(#308):凍在按下送出的半路,他查不到送出去沒有,解凍時可能早就逾時。"""

    def control(self, stage):
        import board_server as bs
        d = self.enterContext(tempfile.TemporaryDirectory(prefix='nopause-'))
        make_board(os.path.join(d, 'board.html'))
        with mock.patch.object(bs, 'STATE', os.path.join(d, 'board.html')), mock.patch.object(bs, 'run_status', return_value={'running': True, 'stage': stage}), \
             mock.patch('jobrun.control', return_value=(True, '暫停了')) as ctl:
            code, st = bs.control_run('apply', 'pause')
        return code, st, ctl

    def test_pause_is_refused_while_sending(self):
        code, st, ctl = self.control('submit')
        self.assertEqual(code, 409)
        self.assertIn('正在送出', st['msg'])
        ctl.assert_not_called()

    def test_pause_while_filling_still_works(self):
        code, _st, ctl = self.control('fill')
        self.assertEqual(code, 200)
        ctl.assert_called_once()


if __name__ == '__main__':
    unittest.main()


class SaveEdges(Tmp):
    """看板的指令(write_fb)還沒走過的幾條:別的裝置改過別的欄、沒指定是哪一張的事件、被擋下的那一道整道不做。"""

    def setUp(self):
        super().setUp()
        import board_server as bs
        self.bs = bs
        patch = mock.patch.multiple(bs, STATE=self.path, trigger_build=lambda: None)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_card_changed_elsewhere_keeps_what_was_changed_there(self):
        # 這個分頁載入時心情是別的;它只送它改的那一欄(筆記),別處改的心情留著
        self.assertEqual(self.bs.write_fb([{'u': U, 'set': {'n': '我的筆記'}}]), [])
        self.assertEqual(read_fb(self.path)[U], {'s': 'like', 'n': '我的筆記'})

    def test_an_event_without_a_card_is_refused_with_the_reason(self):
        rejected = self.bs.write_fb([{'u': '', 'ev': 'confirm'}, {'u': '__ans__', 'ev': 'confirm'}])
        self.assertEqual([r['msg'] for r in rejected], ['沒有指定是哪一張'] * 2)

    def test_the_fields_sent_with_a_refused_click_are_not_written(self):
        # 這一下被狀態表擋了:跟著那一下改的欄也不做
        rejected = self.bs.write_fb([{'u': U, 'ev': 'confirm', 'set': {'s': None}}])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(read_fb(self.path)[U], {'s': 'like'})


class BackgroundBuild(Tmp):
    """背景建可投遞夾:同時只跑一個,跑的期間又有人存,就記著、跑完再補一次。"""

    def setUp(self):
        super().setUp()
        import board_server as bs
        self.bs = bs
        state = dict(bs._build_state)
        self.addCleanup(bs._build_state.update, state)
        patch = mock.patch.multiple(bs, STATE=self.path, PILOT=None)
        patch.start()
        self.addCleanup(patch.stop)
        live = mock.patch.object(bd, 'LIVE', self.path)
        live.start()
        self.addCleanup(live.stop)

    def wait_idle(self):
        for _ in range(200):
            with self.bs._BUILD_LOCK:
                if not self.bs._build_state['running']:
                    return
            threading.Event().wait(0.02)
        self.fail('背景建置沒停下來')

    def test_a_save_during_a_build_runs_one_more_round(self):
        calls = []

        def reconcile(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                self.bs.trigger_build()           # 跑的期間他又存了一次:不開第二個,記著
            return mock.Mock(returncode=0)
        gen = self.bs._build_state['gen']
        with mock.patch('subprocess.run', side_effect=reconcile):
            self.bs.trigger_build()
            self.wait_idle()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.bs._build_state['gen'], gen + 2)

    def test_the_flow_sees_when_the_last_finished_round_started(self):
        # 自動流程照「最近跑完那一輪幾點開始」判斷卡進待你決定之後驗收跑過了沒;沒跑完的那一輪只算試過
        codes = iter([0, 1])
        before = time.time()
        with mock.patch('subprocess.run', side_effect=lambda *a, **k: mock.Mock(returncode=next(codes))):
            self.bs.trigger_build(); self.wait_idle()
            done = self.bs.build_state()
            self.bs.trigger_build(); self.wait_idle()
        failed = self.bs.build_state()
        self.assertGreaterEqual(done['built'], before)
        self.assertEqual(failed['built'], done['built'])
        self.assertGreater(failed['tried'], done['built'])

    def test_a_sandbox_never_builds(self):
        with mock.patch.object(bd, 'LIVE', os.path.join(self.dir, 'other.html')), \
             mock.patch('subprocess.run', side_effect=AssertionError('副本不該建')):
            self.bs.trigger_build()
        self.assertFalse(self.bs._build_state['running'])


class SandboxPageLost(Tmp):
    def test_sandbox_card_is_not_marked_lost(self):
        import board_server as bs
        with mock.patch.multiple(bs, STATE=self.path, is_real=lambda: False), \
             mock.patch.object(bd, 'set_fb', side_effect=AssertionError('副本不該改')):
            self.assertEqual(bs.agent_swapped(U, '換掉了'), '')

    def test_a_sandbox_is_never_swept_whoever_calls(self):
        # ego 整台電腦只有一個,副本的卡不是它填的:不問 ego、不動副本的卡(以前每個呼叫端自己先問,👀 那條忘了問過,c49541a)
        import chrome_door
        make_board(self.path, {U: {'app': 'ship', 'ds': 'parked', 'apply': {'stage': 'fill', 'tab_id': '1:p1',
                                                                          'workspace': {'id': 1, 'name': 'w', 'page': 'p1'}}}})
        with mock.patch.object(bd, 'LIVE', os.path.join(self.dir, 'other.html')), \
             mock.patch.object(chrome_door, '_ego', side_effect=AssertionError('副本不該問 ego')):
            self.assertEqual(chrome_door.sweep_gone(self.path, unreachable=True), {})
        self.assertEqual(read_fb(self.path)[U]['ds'], 'parked')




class SendQueue(unittest.TestCase):
    """確認送出之後 8 秒由後台送(分頁關掉照樣送);那之前按了復原、取消確認,時間到就不送。"""
    setUp0, tearDown, card, phone, assertAllowed = (TwoDevices.setUp, TwoDevices.tearDown, TwoDevices.card,
                                                    TwoDevices.phone, TwoDevices.assertAllowed)

    def board(self, state):
        m = copy.deepcopy(FIX['cards'][state])
        for x in m['form']['f']:
            x.pop('refill', None)                            # 網頁照現在的答案填的:可以確認送出
        _env.make_board(self.path, {CARD: m, '__ans__': copy.deepcopy(FIX['ans'])},
                        data={'jobs': [{'id': CARD, 'target': 'x'}],
                              'status': {'schema_version': 2, 'checked_links': True, 'issues': []}}, sty=CSS)

    def setUp(self):
        self.setUp0()
        self.sent, self.reply = [], [(200, {})]
        self.enterContext(mock.patch.object(self.bs, 'start_run',
                                            lambda kind, args: self.sent.append((kind, args)) or self.reply.pop(0)))
        self.enterContext(mock.patch.object(self.bs, 'SERVING', [True]))
        self.timers = []
        self.enterContext(mock.patch.object(self.bs.threading, 'Timer',
                                            lambda _s, fn, args: mock.Mock(start=lambda: self.timers.append((fn, args)))))

    def time_is_up(self):
        timers, self.timers = self.timers, []
        for fn, args in timers:
            fn(*args)

    def confirm(self):
        out = {}
        self.assertEqual(self.bs.write_fb([{'u': CARD, 'ev': 'confirm'}], out), [])
        return out['undo'][0]

    def test_time_is_up_with_no_tab_open_it_is_sent(self):
        self.board('parked')
        self.confirm()
        self.assertEqual(self.sent, [])
        self.time_is_up()
        self.assertEqual(self.sent, [('apply', {'stage': 'submit', 'url': CARD})])

    def test_undo_within_the_wait_is_not_sent(self):
        self.board('parked')
        slip = self.confirm()
        self.assertEqual(self.bs.write_fb(slip), [])
        self.time_is_up()
        self.assertAllowed('parked')
        self.assertEqual(self.sent, [])

    def test_another_tab_unconfirming_within_the_wait_is_not_sent(self):
        self.board('parked')
        self.confirm()
        self.phone('unconfirm')
        self.time_is_up()
        self.assertEqual(self.sent, [])

    def test_another_round_using_the_browser_waits_then_sends(self):
        """時間到時另一輪代投(或查應徵進度)正在用 agent 的瀏覽器:等它跑完照樣送,不叫他再按。"""
        self.board('parked')
        self.confirm()
        self.reply = [(409, {'running': True}), (409, {'other': True, 'msg': '查應徵進度正在用'}), (200, {})]
        self.time_is_up()
        self.time_is_up()
        self.time_is_up()
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.timers, [])
        self.assertEqual([x for x in read_fb(self.path).get('__inbox__', []) if not x.get('done')], [])

    def test_it_cannot_be_sent_for_another_reason_it_says_so(self):
        """沒有 ego 這種原因送不出去:照實寫進看板的回報(他看得到為什麼停在你已確認),不悄悄不送。"""
        self.board('parked')
        self.confirm()
        self.reply = [(409, {'needs_browser': True, 'msg': '這個動作需要 agent 的瀏覽器(ego)'})]
        self.time_is_up()
        self.assertEqual(self.timers, [])
        box = [x for x in read_fb(self.path).get('__inbox__', []) if x.get('job') == CARD]
        self.assertEqual(len(box), 1)
        self.assertIn('需要 agent 的瀏覽器', box[0]['msg'])
        self.assertAllowed('confirmed')


class ServedVersion(TwoDevices):
    """首頁帶的版本(sig)跟輪詢的 /api/rev 同一種算法:沒人改就對得上(載入後不再重抓大包),改了就對不上(#606)。
    伺服器記著的那份看板(snapshot)在別的程式寫檔之後也要跟著換。"""

    def test_page_version_matches_poll_until_someone_writes(self):
        self.board('confirmed')
        served = bd.parse(self.bs.serve_doc(self.bs.snapshot(), 3))['data']['sig']
        rev, data = self.bs.content_sig()
        self.assertEqual(served, {'rev': rev, 'gen': data + '.3'})
        bd.set_fb(lambda fb: fb.setdefault(CARD, {}).update(note='x'), self.path)
        self.assertNotEqual(self.bs.content_sig()[0], served['rev'])
        self.assertEqual(self.bs.snapshot()['fb'][CARD]['note'], 'x')

    def test_poll_version_moves_when_only_the_profile_registry_changes(self):
        # 每張卡的下一步也照平台履歷登記表算:看板檔沒變、登記表換了,頁面也要重拿下一步
        import profile_sync as ps
        self.board('confirmed')
        with mock.patch.object(ps, 'REG', os.path.join(self.dir, 'profiles.json')):
            before = self.bs.content_sig()[0]
            ps._save_registry({'104.com.tw': {}})
            self.assertNotEqual(self.bs.content_sig()[0], before)

    def test_save_answer_lists_only_the_cards_it_touched(self):
        self.board('confirmed')
        other = 'https://ex.test/other'
        bd.set_data(lambda data, fb: data['jobs'].append({'id': other, 'target': 'y'}), self.path)
        full = self.bs.next_steps(self.bs.snapshot())
        only = self.bs.next_steps(self.bs.snapshot(), {CARD})
        self.assertIn(other, full)
        self.assertNotIn(other, only)
        self.assertEqual(only[CARD], full[CARD])
