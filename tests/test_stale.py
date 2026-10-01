#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""過時的東西要清掉(#314、GLOSSARY「過時」):產生它的那條規則改了、造成它的那個 bug 修掉了,
agent 的回報、agent 推出來而使用者還沒確認的常用答案就清掉底下那一筆(不只是畫面上那一行);
那一題之後照正常流程由 agent 代填。使用者確認過或改過的常用答案一條都不動。
更新後伺服器起來時(board_server.migrate_marks)清。"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾
from _env import read_fb  # noqa: E402

import agent_report  # noqa: E402
import board_server as bs  # noqa: E402
import config as cf  # noqa: E402
import form_record as fr  # noqa: E402

U1, U2 = 'https://ex.test/job/1', 'https://ex.test/job/2'
JOBS = [{'id': U1, 'target': 'Job 1'}, {'id': U2, 'target': 'Job 2'}]
# 使用者確認過、改過的:永遠不算過時
HIS = [
    {'k': 'his', 'q': '期望薪資', 'v': '面議', 'at': '2026-09-20'},
    {'k': 'his_edit', 'q': '可上班日', 'v': '兩週內', 'at': '2026-09-18', 'why': '他自己改的'},
]


def seed():
    return {
        '__ds__': 1,
        '__ans__': copy.deepcopy(HIS) + [
            # 「每一題都要代填」那條規則之前,agent 留空不代寫(規則改了 → 過時)
            {'k': 'refused', 'q': '你為什麼想轉職?', 'v': '', 'why': '這是你的看法,我不代寫', 'inf': '2026-09-21'},
            # 同一條規則之前留空、現在只剩已送出的表單在用:紀錄留著沒用,整條拿掉
            {'k': 'refused_sent', 'q': '你的缺點?', 'v': '', 'why': '我不代寫', 'inf': '2026-09-20'},
            # 規則改了之後 agent 推的答案:不是那條規則產生的,不動
            {'k': 'later', 'q': '通勤方式', 'v': '捷運', 'why': '住在捷運站旁', 'inf': '2026-09-25'},
            {'k': 'later_empty', 'q': '興趣', 'v': '', 'inf': '2026-09-25'},
        ],
        U1: {'app': 'ship', 'form': {'plat': '104', 'at': '2026-09-21T10:00:00', 'f': [
            {'q': '你為什麼想轉職?', 'src': 'bank', 'k': 'refused'},
            {'q': '期望薪資', 'src': 'bank', 'k': 'his'},
        ]}},
        U2: {'app': 'sent', 'form': {'plat': '104', 'lock': 1, 'at': '2026-09-20T10:00:00', 'f': [
            {'q': '你的缺點?', 'src': 'bank', 'k': 'refused_sent'},
        ]}},
        agent_report.KEY: [
            # 選平台履歷交給 agent、附件拿錯的那一份比的那個 bug 產生的回報(bug 修掉了 → 過時)
            {'id': 'r1', 'at': '2026-09-29T14:20:00', 'from': '代投', 'job': U1, 'n': 1,
             'msg': '填表沒完成:平台附件「求職信」少了'},
            # 跟那些規則、bug 無關的回報:留著
            {'id': 'r2', 'at': '2026-09-29T14:21:00', 'from': '找缺', 'n': 1, 'msg': '要你登入 104 才找得到缺'},
        ],
    }


class StaleIsCleared(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='stale-')
        self.path = os.path.join(self.dir, 'board.html')
        with open(os.path.join(self.dir, cf.NAME), 'w', encoding='utf-8') as f:
            f.write('{}')
        for patcher in (mock.patch.object(cf, 'HOME', self.dir), mock.patch.object(bs, 'STATE', self.path)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)
        _env.make_board(self.path, seed(), jobs=JOBS)

    def test_after_an_update_stale_agent_output_is_gone_and_his_answers_are_untouched(self):
        bs.migrate_marks(self.path)
        fb = read_fb(self.path)
        bank = {e['k']: e for e in fb['__ans__']}

        # 過時的答案不在要你處理的清單,底下那一筆也不留「不代寫」;還沒送出的表單那一題留給 agent 代填
        self.assertNotIn('refused', [e['k'] for e, _ in fr.find_pending(fb)])
        self.assertNotIn('不代寫', json.dumps(fb['__ans__'], ensure_ascii=False))
        self.assertFalse(fr.answers_pending(fb, U1))
        self.assertIn('refused', bank)
        self.assertEqual(bank['refused']['v'], '')
        self.assertNotIn('refused_sent', bank)
        self.assertEqual(fr.validate(fb), [])

        # 使用者確認過或改過的一條都沒變;不是那條規則產生的也不動
        self.assertEqual([bank[e['k']] for e in HIS], HIS)
        self.assertEqual(bank['later']['v'], '捷運')
        self.assertIn('later_empty', [e['k'] for e, _ in fr.find_pending(fb)])

        # 那個 bug 產生的回報收掉了(底下那一筆記成已處理),無關的留著
        self.assertEqual([it['id'] for it in agent_report.open_items(fb)], ['r2'])

    def test_clearing_twice_changes_nothing_more(self):
        # 清過之後同一條規則不會再清到後來的東西:再起來一次,看板不動
        bs.migrate_marks(self.path)
        first = read_fb(self.path)
        bs.migrate_marks(self.path)
        self.assertEqual(read_fb(self.path), first)

if __name__ == '__main__':
    unittest.main()
