#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""證據放在對的位置(#315):每張卡每一輪的證據集中放在那張卡自己的資料夾,所有會叫 agent 的流程同一種放法。

一輪的資料夾裡有:程式給的指示、agent 的動作紀錄、交件單、程式的比對結果、之後每次讀那一頁看到的樣子(連同程式自己截的圖),
events.jsonl 照時間排。要使用者確認的事都附程式自己截的那一頁;沒有就不叫他做,改回報「缺證據」。

測法:用假的 agent(換掉 agent_run 開行程、等行程那兩支,agent_run.run 本身照真的跑),跑正式的入口,再打開那張卡的證據資料夾看。
四件網站任務的證據由各自的驗收測試(tests/test_accept_*.py)考。
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
import board_doc as bd        # noqa: E402
import evidence               # noqa: E402

U = 'https://jobs.lever.co/evidence/1'


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(tempfile.TemporaryDirectory(prefix='evidence-'))
        root = patch.object(evidence, 'root', return_value=os.path.join(self.home, 'evidence'))
        root.start()
        self.addCleanup(root.stop)


class AgentReportEvidence(EvidenceTests):
    def test_an_agent_report_without_a_program_screenshot_is_missing_evidence_not_a_todo(self):
        # 查應徵進度、找缺這種程式沒截那一頁的流程:agent 自己的回報照樣列出來,但標「缺證據」,不叫他照做(#315)
        import agent_report
        fb = {U: {'app': 'ship'}}
        with patch.dict(os.environ, {ar.REPORT_FROM_ENV: '查回音'}), \
             patch.object(sys, 'argv', ['agent_report.py', '--from', '查回音', '--job', U, '--need', '去登入信箱',
                                        '信箱要本人登入']), \
             patch.object(bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)):
            agent_report.main()
        it = next(x for x in fb['__inbox__'] if x['msg'] == '信箱要本人登入')
        self.assertTrue(it.get('noev'))
        self.assertIn(it, agent_report.open_items(fb))
        self.assertNotIn(it, agent_report.todo(fb))
        program = bd.apply_report(fb, '查回音', '查回音 agent 沒完成', need='看紀錄', job=U)
        self.assertIn(program, agent_report.todo(fb))


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


class OtherFlows(EvidenceTests):
    """其他會叫 agent 的流程,各跑一個最小情境:證據放在同一種位置(那張卡的證據夾;沒有卡的放那個流程自己的夾)、同一種格式。"""


    def test_an_agent_dispatched_without_a_round_still_leaves_its_evidence(self):
        a, b = agent_writes()
        with a, b, patch('sys.argv', ['some_flow.py']):
            self.assertTrue(ar.run('做一件事', os.path.join(self.home, 'x.out')).ok)
        home = evidence.flow_dir('some_flow')
        [rnd] = [os.path.join(home, n) for n in os.listdir(home)]
        same_shape(self, rnd, ('instruction', 'agent_log'))

    def test_one_round_about_several_cards_is_in_each_cards_folder(self):
        cards = ['https://ex.test/job/1', 'https://ex.test/job/2']
        a, b = agent_writes()
        with a, b, evidence.opened('reply', 'check', cards) as rnd:
            ar.run('查這兩張', os.path.join(self.home, 'r.out'))
            rnd.handoff(os.path.join(self.home, 'none.json'))
        for u in cards:
            [one] = _env.evidence_rounds(u)
            rows = same_shape(self, one, ('instruction', 'agent_log'))
            self.assertIn('none.json', [e.get('missing') for e in rows])          # 沒交件單也照實記

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


    def test_preparing_resumes_puts_each_cards_own_handoff_in_its_folder(self):
        import cut_tailor
        rows = [['https://ex.test/job/1', 'A'], ['https://ex.test/job/2', 'B']]
        sp = os.path.join(self.home, 'sp')
        os.makedirs(sp)
        out = os.path.join(self.home, 'prepare')      # 這一輪的夾:收尾只從這裡讀輪次檔
        os.makedirs(out)
        for name, data in (('rows.json', rows), ('t0', 0), ('choices.json', {}), ('cached.json', [])):
            with open(os.path.join(out, name), 'w', encoding='utf-8') as f:
                json.dump(data, f)
        with open(os.path.join(out, 'prompt.txt'), 'w', encoding='utf-8') as f:
            f.write('準備這兩張')
        own = os.path.join(out, cut_tailor.card.card_id_from_url(rows[0][0]), 'fill.json')
        a, b = agent_writes(lambda of: {own: {'keep': True}})
        with a, b, patch.multiple(cut_tailor, SP=sp,
                                  STATUSF=os.path.join(sp, 's.json'), PIDF=os.path.join(sp, 'p'),
                                  WORKER_PIDF=os.path.join(sp, 'w')), \
             patch.object(ar, 'wait_done', return_value=[SimpleNamespace(status='failed', returncode=1, pid=4242)]), \
             patch.object(cut_tailor, '_report'):
            cut_tailor.run_finish(None, out_dir=out)
        first, second = (same_shape(self, _env.evidence_rounds(u)[0], ('instruction', 'agent_log', 'handoff'))
                         for u, _t in rows)
        self.assertEqual([e['src'] for e in first if e['kind'] == 'handoff'], ['fill.json'])   # 只有它自己那一份
        self.assertEqual([e.get('missing') for e in second if e['kind'] == 'handoff'], ['fill.json'])


class RoundIds(EvidenceTests):
    def test_rounds_opened_in_the_same_microsecond_get_their_own_folders(self):
        # 填表同時跑幾張(#777):時鐘給同一個時間時,兩輪不能共用一夾(交件單會互蓋)
        import datetime
        from concurrent.futures import ThreadPoolExecutor
        same = datetime.datetime(2026, 1, 2, 3, 4, 5, 678901)
        with patch.object(evidence.datetime, 'datetime', wraps=datetime.datetime) as clock:
            clock.now.return_value = same
            clock.min = datetime.datetime.min
            with ThreadPoolExecutor(max_workers=8) as pool:
                rounds = list(pool.map(lambda _n: evidence.Round('fill', 'fill'), range(8)))
        self.assertEqual(len({r.id for r in rounds}), 8)
        self.assertEqual(len({r.dirs[0] for r in rounds}), 8)

    def test_a_round_still_open_is_not_cleared_by_thirty_newer_ones(self):
        # 同時開的輪超過 KEEP(填表同時跑):慢的那輪夾還在用,不能被後開的清掉
        with patch.object(evidence, '_pointed_at', return_value=lambda name: False):
            slow = evidence.Round('fill', 'fill')
            for _ in range(evidence.KEEP):
                evidence.Round('fill', 'fill')
            folder = slow.dirs[0]
            self.assertTrue(os.path.isdir(folder))
            del slow
            import gc
            gc.collect()
            evidence.Round('fill', 'fill')      # 沒人拿著了,下一輪清掉
        self.assertFalse(os.path.isdir(folder))


if __name__ == '__main__':
    unittest.main()
