#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent 不能繞過投遞狀態表直接改看板檔(#307):
  · 派出去的 agent 拿不到看板檔的位置,它用的小工具自己知道要改哪一份;
  · 小工具只收狀態表允許的事件;
  · 不經正常寫入改過的看板檔,下一次讀就被抓到、不照改過的內容做事;使用者自己在看板上改照常。

跑法(repo 根目錄):python3 -m unittest tests.test_board_guard
"""
import contextlib
import functools
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402  測試跑在暫存資料夾
import agent_run as ar        # noqa: E402
import board_doc as bd        # noqa: E402
import config as cf           # noqa: E402

TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
U = 'https://jobs.lever.co/acme/1'
K = 'a1b2c3d4'


make_board = functools.partial(_env.make_board, jobs=[{'id': U}])


def filling_fb():
    """一張正在填的卡,答案庫有一條他改了中文、英文待重翻的。"""
    return {U: {'app': 'ship', 'ds': 'running', 'apply': {'stage': 'fill', 'at': '2026-09-30T10:00:00'}},
            '__ans__': [{'k': K, 'q': 'Why us?', 'v': 'Old answer.', 'zh': '新的中文', 'tr': 1,
                         'at': '2026-09-01'}]}


# 假 agent:只用派它時拿到的環境,照 prompt 教的三條路改看板(記表單、寫回翻譯、回報問題)。
FAKE_AGENT = textwrap.dedent('''
    import json, os, subprocess, sys
    tools, work = sys.argv[1], sys.argv[2]
    with open(os.path.join(work, 'env.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(os.environ), f)
    fill = os.path.join(work, 'fill.json')
    with open(fill, 'w', encoding='utf-8') as f:
        json.dump({'platform': 'Lever', 'fields': [{'q': 'Name', 'value': 'Ann', 'src': 'rz'}]}, f)
    runs = [
        [sys.executable, os.path.join(tools, 'form_record.py'), '--from-fill', fill, '--url', sys.argv[3]],
        [sys.executable, '-c', 'import sys; sys.path.insert(0, %r); import form_record as fr; '
                               'fr.translate(%r, en="New answer.")' % (tools, sys.argv[4])],
        [sys.executable, os.path.join(tools, 'agent_report.py'), '--from', '代投', '--job', sys.argv[3],
         '這家要你本人登入'],
    ]
    for argv in runs:
        r = subprocess.run(argv, capture_output=True, text=True)
        sys.stdout.write(r.stdout + r.stderr)
        if r.returncode:
            sys.exit(r.returncode)
''')


class AgentHasNoBoardLocation(unittest.TestCase):
    def setUp(self):
        self.d = self.enterContext(tempfile.TemporaryDirectory(prefix='board-guard-'))
        self.copy = make_board(os.path.join(self.d, 'copy', 'board.html'), filling_fb())
        self.work = os.path.join(self.d, 'work')
        os.makedirs(self.work)
        self.script = os.path.join(self.d, 'fake_agent.py')
        with open(self.script, 'w', encoding='utf-8') as f:
            f.write(FAKE_AGENT)
        # 現行看板(暫存家目錄裡那一份):小工具要是找錯看板,就會寫到這裡
        self.made_live = not os.path.exists(cf.LIVE)
        if self.made_live:
            make_board(cf.LIVE, {})
        with open(cf.LIVE, encoding='utf-8') as f:
            self.live_before = f.read()

    def tearDown(self):
        if self.made_live:
            for p in (cf.LIVE, cf.LIVE + '.sha256', cf.LIVE + '.lock'):
                if os.path.exists(p):
                    os.remove(p)

    def launch(self, prompt='填這張'):
        argv = [sys.executable, self.script, TOOLS, self.work, U, K]
        with mock.patch.dict(os.environ, {'AGENT_BOARD': self.copy}), \
                mock.patch.object(ar, 'argv_for', return_value=(argv, cf.HOME)), \
                mock.patch.object(ar, 'prompt_stdin', return_value=None):
            proc = ar.launch(prompt, os.path.join(self.work, 'agent.log'), cf.HOME, board=self.copy)
        proc.wait(60)
        with open(os.path.join(self.work, 'agent.log'), encoding='utf-8') as f:
            log = f.read()
        self.assertEqual(proc.returncode, 0, log)

    def test_fake_agent_changes_the_card_without_knowing_where_the_board_is(self):
        self.launch()
        with open(os.path.join(self.work, 'env.json'), encoding='utf-8') as f:
            env = json.load(f)
        leaks = {k: v for k, v in env.items()
                 if self.copy in v or os.path.realpath(self.copy) in v or v.endswith('board.html')}
        self.assertEqual(leaks, {})
        fb = _env.read_fb(self.copy)
        self.assertEqual(fb[U]['form']['plat'], 'Lever')                         # 記表單
        self.assertEqual(fb['__ans__'][0]['v'], 'New answer.')                   # 翻譯寫回答案庫
        self.assertNotIn('tr', fb['__ans__'][0])
        self.assertEqual([x['msg'] for x in fb['__inbox__']], ['這家要你本人登入'])   # 回報
        with open(cf.LIVE, encoding='utf-8') as f:
            self.assertEqual(f.read(), self.live_before)                          # 現行看板沒被碰

    def test_a_prompt_that_names_the_board_file_is_not_sent(self):
        with self.assertRaises(ValueError):
            self.launch(prompt=f'改完寫回 {self.copy}')

    def test_fill_and_fix_prompts_do_not_name_the_board_file(self):
        import apply_run
        import chrome_door
        fb = filling_fb()
        fb[U]['form'] = {'plat': 'Lever', 'f': [{'q': 'Why us?', 'src': 'bank', 'k': K}], 'at': '2026-09-30'}
        with mock.patch.object(apply_run.ship, 'folder', return_value=''), \
                mock.patch.object(apply_run.ship, 'read_info', return_value={}), \
                mock.patch.object(apply_run.fr, 'shared_text', return_value=''):
            for runtime in ('codex', 'claude-code'):
                for stage in ('fill', 'fix'):
                    prompt, _out = apply_run.prompt_for(stage, U, {'id': U}, fb, self.copy, door=chrome_door.of(runtime))
                    self.assertIn('最後直接回傳一個完整交件 JSON' if runtime == 'codex' else 'form_record.py', prompt)
                    self.assertNotIn(self.copy, prompt)
                    self.assertNotIn('--board', prompt)


class ToolsOnlyTakeAllowedEvents(unittest.TestCase):
    """agent 呼叫的小工具只收狀態表允許的事件:不准的擋下、講原因,卡片不動。"""

    def setUp(self):
        self.d = self.enterContext(tempfile.TemporaryDirectory(prefix='board-guard-ev-'))
        self.fill = os.path.join(self.d, 'fill.json')
        with open(self.fill, 'w', encoding='utf-8') as f:
            json.dump({'platform': 'Lever', 'fields': [{'q': 'Name', 'value': 'Ann', 'src': 'rz'}]}, f)

    def record(self, card):
        board = make_board(os.path.join(self.d, 'board.html'), {U: card})
        before = _env.read_fb(board)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'form_record.py'), '--from-fill', self.fill,
                            '--url', U], env=ar.agent_env(board), capture_output=True, text=True)
        return r, before, _env.read_fb(board)

    def test_recording_a_form_on_a_card_nobody_is_filling_is_refused_with_the_reason(self):
        r, before, after = self.record({'app': 'ship', 'ds': 'parked', 'apply': {'stage': 'fill', 'tab_id': '1'},
                                         'form': {'plat': 'Old', 'f': [], 'at': '2026-09-01'}})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('停著等你', r.stderr)                 # 講原因:這張現在是什麼狀態
        self.assertEqual(after, before)                    # 卡片沒被動到

    def test_recording_a_form_while_the_agent_is_filling_is_accepted(self):
        r, _before, after = self.record({'app': 'ship', 'ds': 'running', 'apply': {'stage': 'fix'}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(after[U]['form']['plat'], 'Lever')
        self.assertEqual(after[U]['ds'], 'running')


def sneak(path, mutate):
    """不經正常寫入直接改看板檔(agent 自己開檔改的樣子)。"""
    with open(path, encoding='utf-8') as f:
        p = bd.parse(f.read())
    fb = json.loads(p['fb'])
    mutate(fb)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(bd.assemble(p['sty'], p['thdr'], p['tail'], p['data'], json.dumps(fb, ensure_ascii=False), p['app']))


def confirm_behind_the_back(fb):
    fb[U].update(ds='confirmed', approve={'at': '2026-09-30'})


class SneakedEditsAreCaught(unittest.TestCase):
    def setUp(self):
        self.d = self.enterContext(tempfile.TemporaryDirectory(prefix='board-guard-fp-'))
        self.board = make_board(os.path.join(self.d, 'board.html'), filling_fb())
        bd.set_fb(lambda fb: fb[U].__setitem__('s', 'like'), live=self.board)     # 正常寫過一次

    def test_the_next_program_write_stops_and_leaves_the_sneaked_file_alone(self):
        sneak(self.board, confirm_behind_the_back)
        with open(self.board, encoding='utf-8') as f:
            sneaked = f.read()
        seen = []
        with self.assertRaises(bd.Tampered) as caught:
            bd.set_fb(seen.append, live=self.board)
        self.assertEqual(seen, [])                             # 沒拿改過的內容做事
        self.assertIn('繞過', str(caught.exception))
        self.assertNotIn(self.d, str(caught.exception))        # 回報不帶看板檔的位置(agent 也會看到這句)
        with open(self.board, encoding='utf-8') as f:
            self.assertEqual(f.read(), sneaked)                # 也沒被蓋成「正常」的樣子
        with self.assertRaises(bd.Tampered):
            bd.set_data(lambda data, fb: None, live=self.board)

    def test_the_dispatcher_does_not_act_on_a_sneaked_board(self):
        import apply_run
        sneak(self.board, confirm_behind_the_back)
        with self.assertRaises(bd.Tampered):
            apply_run.load(self.board)

    def test_the_agent_tool_reports_it_and_changes_nothing(self):
        sneak(self.board, lambda fb: fb[U].__setitem__('n', '偷寫的'))
        fill = os.path.join(self.d, 'fill.json')
        with open(fill, 'w', encoding='utf-8') as f:
            json.dump({'platform': 'Lever', 'fields': [{'q': 'Name', 'value': 'Ann', 'src': 'rz'}]}, f)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'form_record.py'), '--from-fill', fill, '--url', U],
                           env=ar.agent_env(self.board), capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('繞過', r.stderr)
        self.assertNotIn(self.d, r.stdout + r.stderr)
        self.assertNotIn('form', _env.read_fb(self.board)[U])

    def test_he_can_say_the_current_content_is_fine_but_an_agent_cannot(self):
        sneak(self.board, lambda fb: fb[U].__setitem__('n', '我自己用編輯器改的'))
        cli = [sys.executable, os.path.join(TOOLS, 'board_doc.py'), '--trust', self.board]
        r = subprocess.run(cli, env=ar.agent_env(self.board), capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        with self.assertRaises(bd.Tampered):
            bd.set_fb(lambda fb: None, live=self.board)
        env = {k: v for k, v in os.environ.items() if k not in ('AGENT_BOARD', bd.BOARD_ID)}
        r = subprocess.run(cli, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        bd.set_fb(lambda fb: None, live=self.board)
        self.assertEqual(_env.read_fb(self.board)[U]['n'], '我自己用編輯器改的')


class NormalWritesAreNotMistakenForSneaking(unittest.TestCase):
    def setUp(self):
        self.d = self.enterContext(tempfile.TemporaryDirectory(prefix='board-guard-ok-'))
        self.board = make_board(os.path.join(self.d, 'board.html'), filling_fb())

    def test_every_normal_writer_in_a_row(self):
        import apply_shell
        bd.set_fb(lambda fb: fb[U].__setitem__('s', 'like'), live=self.board)
        bd.set_data(lambda data, fb: data['jobs'].append({'id': U + '2'}), live=self.board)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_shell.apply_to(self.board, ':root{--b:2}', '/*app2*/', '<b id="stat-first">0</b>')
        bd.set_fb(lambda fb: fb[U].__setitem__('s', 'meh'), live=self.board)
        self.assertEqual(_env.read_fb(self.board)[U]['s'], 'meh')

    def test_a_board_never_written_normally_is_trusted(self):
        """升級後第一次、剛複製出來的副本:還沒有指紋,照常用。"""
        sneak(self.board, lambda fb: fb[U].__setitem__('s', 'like'))
        bd.set_fb(lambda fb: fb[U].__setitem__('s', 'meh'), live=self.board)
        self.assertEqual(_env.read_fb(self.board)[U]['s'], 'meh')

    def test_an_empty_fingerprint_file_is_not_called_sneaking(self):
        open(self.board + '.sha256', 'w').close()
        bd.set_fb(lambda fb: fb[U].__setitem__('s', 'meh'), live=self.board)
        self.assertEqual(_env.read_fb(self.board)[U]['s'], 'meh')

    def test_copying_a_board_over_an_old_copy(self):
        other = make_board(os.path.join(self.d, 'other.html'), {U: {'s': 'like'}})
        bd.set_fb(lambda fb: None, live=other)
        bd.set_fb(lambda fb: None, live=self.board)
        bd.copy_board(self.board, other)                      # 沙箱那種:同一個位置一再複製
        bd.set_fb(lambda fb: fb[U].__setitem__('s', 'meh'), live=other)

    def test_a_crash_between_the_two_files_is_not_called_sneaking(self):
        bd.set_fb(lambda fb: None, live=self.board)
        real, calls = os.replace, []

        def replace(src, dst):                                 # 指紋先記好、看板檔還沒換上去就斷電
            calls.append(dst)
            if dst == self.board:
                raise OSError('斷電')
            return real(src, dst)
        with mock.patch.object(bd._os, 'replace', side_effect=replace):
            with self.assertRaises(OSError):
                bd.set_fb(lambda fb: fb[U].__setitem__('s', 'meh'), live=self.board)
        bd.set_fb(lambda fb: fb[U].__setitem__('s', 'like'), live=self.board)
        self.assertEqual(_env.read_fb(self.board)[U]['s'], 'like')


from test_board import HttpBase  # noqa: E402


class BoardPage(HttpBase):
    def test_his_own_saves_keep_working(self):
        u = next(iter(_env.read_fb(self.path)))
        for s in ('meh', 'like', 'dislike'):
            code, body, _h = self.req('/api/save', {'__rev__': self.rev()['rev'], u: {'s': s}})
            self.assertEqual(code, 200, body)
        self.assertEqual(self.req('/')[0], 200)
        self.assertEqual(_env.read_fb(self.path)[u]['s'], 'dislike')

    def test_after_a_sneaked_edit_the_page_says_so_and_saving_stops(self):
        u = next(iter(_env.read_fb(self.path)))
        self.req('/api/save', {'__rev__': self.rev()['rev'], u: {'s': 'meh'}})
        rev = self.rev()['rev']
        sneak(self.path, lambda fb: fb[u].__setitem__('s', 'grow'))
        code, body, _h = self.req('/api/rev')                            # 頁面輪詢也知道
        self.assertEqual((code, json.loads(body)['err']), (423, 'tampered'))
        code, body, _h = self.req('/api/save', {'__rev__': rev, u: {'s': 'like'}})
        self.assertEqual(code, 423)
        self.assertEqual(json.loads(body)['err'], 'tampered')
        self.assertIn('繞過', json.loads(body)['msg'])
        self.assertEqual(_env.read_fb(self.path)[u]['s'], 'grow')     # 沒照改過的內容再寫一次
        code, body, _h = self.req('/')
        self.assertEqual(code, 423)
        self.assertIn('繞過', body.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()


class BoardIdentity(unittest.TestCase):
    """派出去的 agent 只拿代號:同一份看板永遠同一個代號;代號對不到就停,不能默默改到現行看板。"""

    def test_same_board_same_id_and_a_stale_record_is_rewritten(self):
        d = self.enterContext(tempfile.TemporaryDirectory(prefix='board-id-'))
        board = make_board(os.path.join(d, 'board.html'), {})
        tok = bd.board_id(board)
        self.assertEqual(bd.board_id(board), tok)
        with open(os.path.join(bd._ids_dir(), tok), 'w', encoding='utf-8') as f:
            f.write('/somewhere/else.html')
        self.assertEqual(bd.board_id(board), tok)
        with mock.patch.dict(os.environ, {bd.BOARD_ID: tok}):
            self.assertEqual(bd.target(), os.path.realpath(board))

    def test_a_malformed_id_stops_instead_of_writing_the_live_board(self):
        with mock.patch.dict(os.environ, {bd.BOARD_ID: '../../etc'}):
            with self.assertRaises(SystemExit) as e:
                bd.target()
        self.assertIn('找不到這一輪的看板', str(e.exception))


class TrustByName(unittest.TestCase):
    def test_trust_takes_a_board_name_next_to_the_live_board(self):
        folder = os.path.dirname(bd.LIVE)
        os.makedirs(folder, exist_ok=True)
        board = make_board(os.path.join(folder, 'trust-by-name.html'), {U: {'s': 'like'}})
        self.addCleanup(lambda: os.path.exists(board) and os.remove(board))
        sneak(board, lambda fb: fb[U].__setitem__('n', '我自己改的'))
        env = {k: v for k, v in os.environ.items() if k not in ('AGENT_BOARD', bd.BOARD_ID)}
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'board_doc.py'), '--trust', 'trust-by-name.html'],
                           env=env, capture_output=True, text=True, cwd=tempfile.gettempdir())
        self.assertEqual(r.returncode, 0, r.stderr)
        bd.set_fb(lambda fb: None, live=board)
