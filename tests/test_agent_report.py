#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent 回報給他的管道(agent_report):做不到的事一定寫得進看板、同一件事不洗版、處理好的不會被舊回報蓋回來。

跑法(repo 根目錄):python3 -m unittest discover -s tests
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import agent_report as ar      # noqa: E402
import agent_run               # noqa: E402

U = 'https://www.104.com.tw/job/911o4'


class Report(unittest.TestCase):
    def test_a_report_lands_in_the_inbox_with_what_he_must_do(self):
        fb = {}
        it = ar.apply_report(fb, '代投', '104 被機器人驗證擋住', need='改用你看得到的視窗跑', job=U, now='2026-09-22T05:00:00')
        self.assertEqual(ar.open_items(fb), [it])
        self.assertEqual((it['from'], it['job'], it['need'], it['n']), ('代投', U, '改用你看得到的視窗跑', 1))

    def test_the_same_open_report_is_counted_not_repeated(self):
        fb = {}
        ar.apply_report(fb, '代投', '要登入', job=U, now='t1')
        it = ar.apply_report(fb, '代投', '要登入', job=U, now='t2')
        self.assertEqual((len(fb['__inbox__']), it['n'], it['at']), (1, 2, 't2'))

    def test_after_he_handles_it_a_new_report_is_a_new_item(self):
        fb = {}
        ar.apply_report(fb, '代投', '要登入', job=U, now='t1')['done'] = '2026-09-22'
        ar.apply_report(fb, '代投', '要登入', job=U, now='t2')
        self.assertEqual(len(ar.open_items(fb)), 1)
        self.assertEqual(len(fb['__inbox__']), 2)

    def test_a_report_can_carry_what_his_done_confirms(self):
        fb, first, later = {}, [{'item': 'field:0'}], [{'item': 'field:1'}]
        ar.apply_report(fb, '代投', '平台上有原稿沒寫的內容', job=U, now='t1', approve=first)
        it = ar.apply_report(fb, '代投', '平台上有原稿沒寫的內容', job=U, now='t2', approve=later)
        self.assertEqual((it['n'], it['approve']), (2, later))      # 同一則再報:帶的是這一輪的清單

    def test_empty_report_is_refused(self):
        with self.assertRaises(ValueError):
            ar.apply_report({}, '代投', '  ')

    def test_the_program_only_reports_what_he_has_to_do(self):
        """回報 = 非他本人不可的事(GLOSSARY)。2026-10-12 回報區被「不用你處理」灌滿:程式擋下 agent 交件、
        讀不到頁的每一張各報一則,他一則都處理不了。虛擬碼:程式裡每一個發回報的地方都寫了要他做什麼,
        而且不是「不用你處理」;那種原因記進紀錄和證據。"""
        import ast
        import glob
        need_at = {'agent_report.report': 2, 'agent_report.apply_report': 3, 'self.report': 2, 'writer': 2,
                   '_report': 1}                  # 各檔自己的 _report(msg, need, board)
        wrappers = {('board_server.py', 'agent_report.report')}  # 轉手的,need 照呼叫的
        bad = []
        for f in glob.glob(os.path.join(os.path.dirname(HERE), 'tools', '*.py')):
            for n in ast.walk(ast.parse(open(f, encoding='utf-8').read())):
                at = need_at.get(ast.unparse(n.func)) if isinstance(n, ast.Call) else None
                if at is None:
                    continue
                need = next((k.value for k in n.keywords if k.arg == 'need'), n.args[at] if len(n.args) > at else None)
                where = (os.path.basename(f), ast.unparse(n.func))
                if (need is None and where not in wrappers) or (need is not None and '不用你' in ast.unparse(need)):
                    bad.append(f'{where[0]}:{n.lineno}')
        self.assertEqual(bad, [])

    def test_every_agent_prompt_carries_the_report_rule(self):
        for model in ('main', 'alt'):
            argv, _ = agent_run.argv_for(model, 'P', '/repo')
            # codex 的 prompt 從 stdin 餵(prompt_stdin);command-code 還在參數裡
            prompt = argv[argv.index('-p') + 1] if model == 'alt' else agent_run.prompt_stdin(model, 'P')
            self.assertIn('agent_report.py', prompt)
        fed = agent_run.prompt_stdin('main', 'P', browser=agent_run.apply_overrides(), chrome=True)
        self.assertIn('agent_report.py', fed)



class Resolve(unittest.TestCase):
    def test_success_later_closes_that_jobs_old_reports_only(self):
        fb = {}
        ar.apply_report(fb, '代投', '上傳被擋', job='J1', now='2026-09-22T07:00:00')
        ar.apply_report(fb, '代投', '別張的問題', job='J2', now='2026-09-22T07:00:00')
        self.assertEqual(ar.apply_resolve(fb, 'J1', '送出成功', '2026-09-22'), 1)
        j1, j2 = fb['__inbox__']
        self.assertEqual((j1['done'], j1['res']), ('2026-09-22', '送出成功'))
        self.assertNotIn('done', j2)

    def test_success_of_one_kind_does_not_close_other_kinds(self):
        # 可投遞夾重建成功、重填成功,不能把「送出沒確認成功」一起收掉(他還沒確認到底送出沒有)
        fb = {}
        ar.apply_report(fb, '代投', '送出沒確認成功:沒看到成功頁面', job='J1', now='t1')
        ar.apply_report(fb, '可投遞夾建置', '可投遞夾本輪建置失敗', job='J1', now='t1')
        self.assertEqual(ar.apply_resolve(fb, 'J1', '建好了', 'd', only=lambda it: it.get('from') == '可投遞夾建置'), 1)
        sent, built = fb['__inbox__']
        self.assertNotIn('done', sent)
        self.assertEqual(built['done'], 'd')

    def test_handled_reports_do_not_pile_up_forever(self):
        # 處理好的只留最近幾百則:看板每次都整份讀寫這一格,以前永遠不清
        keep = getattr(ar, 'KEEP_DONE', 200)
        fb = {'__inbox__': [{'id': 'd%03d' % i, 'from': '代投', 'msg': str(i), 'at': 't', 'done': '2026-01-%02d' % (i % 28 + 1)}
                            for i in range(keep + 50)]}
        fb['__inbox__'].append({'id': 'open', 'from': '代投', 'msg': '還開著', 'at': 't'})
        ar.apply_report(fb, '代投', '新的一則', job='J1', now='t2')
        box = fb['__inbox__']
        self.assertEqual(sum(1 for it in box if it.get('done')), keep)
        self.assertEqual({it['msg'] for it in box if not it.get('done')}, {'還開著', '新的一則'})
        self.assertIn('2026-01-28', {it['done'] for it in box if it.get('done')})     # 留的是最近處理的

if __name__ == '__main__':
    unittest.main()


class RunControl(unittest.TestCase):
    """看板上的 ⏸ 暫停 / ▶ 繼續 / ⏹ 停止:整串行程(含 agent 的子行程)一起凍住、解凍、收掉。"""

    def test_pause_resume_stop_the_whole_tree(self):
        import subprocess, tempfile, time, jobrun
        d = self.enterContext(tempfile.TemporaryDirectory(prefix='runctl-'))
        p = subprocess.Popen(['/bin/sh', '-c', 'sleep 60 & wait'], start_new_session=True)
        time.sleep(0.3)
        path = os.path.join(d, 'st.json')
        jobrun.write(path, {'phase': 'run', 'pid': p.pid})
        state = lambda pid: subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        kids = [x for x in jobrun.tree(p.pid) if x != p.pid]
        self.assertTrue(kids)                                   # sleep 是它的子行程
        self.assertTrue(jobrun.control(path, ('sh',), 'pause')[0])
        self.assertTrue(state(kids[0]).startswith('T'))         # 子行程也凍住了
        self.assertTrue(jobrun.read(path, ('sh',))['paused'])
        jobrun.control(path, ('sh',), 'resume')
        self.assertFalse(state(kids[0]).startswith('T'))
        jobrun.control(path, ('sh',), 'stop')
        p.wait(timeout=5)
        self.assertEqual(jobrun.read(path, ('sh',))['phase'], 'stopped')
