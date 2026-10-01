"""連續很多步的隨機測試(#302 補充驗收):狀態表的測試每一格只驗一個事件,有些問題要好幾步才出現
(確認 → 換履歷 → 復原 → 送出)。固定種子產生幾千串動作(tests/delivery_walk.py),每一步都驗:

- 沒有對「現在這一頁、現在的答案」的確認,絕不會走到正在送出
- 同一個職缺絕不送兩次(已送出之後,除非經過沒送成、退回或再投一次)
- 停著的頁到上限時,自動流程不填新的(看板那邊的數量由看板檢查逐步比)
- 每一步之後卡片都恰好在一種投遞狀態,存的資料對得回狀態表
- 任何一步之後按復原,卡片回到上一步的樣子

失敗時印出種子、第幾串和那一串做了什麼,重跑得出同一串。"""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾

import delivery_state as ds
import delivery_walk as dw
import form_record as fr


class Walks(unittest.TestCase):
    def fail_walk(self, w, i, why):
        self.fail(f'種子 {dw.SEED}、第 {i} 串(delivery_walk.Walk({dw.SEED * 100000 + i}))第 {len(w.log)} 步:{why}\n'
                  + '\n'.join(w.log))

    def test_invariants_hold_at_every_step(self):
        undone = 0
        for i in range(dw.WALKS):
            w = dw.Walk(dw.SEED * 100000 + i)
            for _ in range(dw.STEPS):
                name, u, before, result, _ops = w.step()
                for v in dw.URLS:
                    s0, s1 = ds.state(before.get(v)), ds.state(w.fb[v])
                    m = w.fb[v]
                    # 每一步之後恰好一種狀態,存的資料對得回狀態表
                    bad = ds.problems(m)
                    if bad:
                        self.fail_walk(w, i, f'{v} {bad}')
                    if s1 == 'sending' and s0 != 'sending':
                        # 走到正在送出:一定是「送出」這一步,而且那一刻對現在這一頁、現在的答案有確認
                        if name != '送出' or s0 != 'confirmed':
                            self.fail_walk(w, i, f'{v} 從「{ds.label(s0)}」被「{name}」帶到正在送出')
                        if not ds.page_up(before[v]) or fr.approval_problem(before, v, dw.STATUS) is not None:
                            self.fail_walk(w, i, f'{v} 沒有對現在這一頁、現在的答案的確認就送出')
                        if v in w.was_sent:
                            self.fail_walk(w, i, f'{v} 已經送出過,又送一次')
                    if s1 == 'sent':
                        w.was_sent.add(v)
                    if s0 == 'sent' and s1 != 'sent':
                        w.was_sent.discard(v)           # 沒送成、退回、再投一次(或剛標錯按了復原):這一次重新算
                # 停著的頁到上限:自動流程不填新的(已經停著的那張重填不算新的)
                held = w.held()
                pick = w.auto_pick()
                if len(held) >= dw.CAP and pick and pick not in held:
                    self.fail_walk(w, i, f'停著的頁 {len(held)} 張已到上限,自動流程還要填 {pick}')
                # 復原:回到上一步的樣子
                if name == '復原' and result is not None:
                    undone += 1
                    if w.fb[u if w.ops[-1].get('u') is None else w.ops[-1]['u']] != result:
                        self.fail_walk(w, i, '復原之後卡片跟上一步不一樣')
        self.assertGreater(undone, 100)                   # 真的有走到復原

    def test_every_undo_right_after_the_step_gives_back_the_card(self):
        """任何一步(看板上的動作)之後馬上按復原,卡片回到按之前的樣子。"""
        n = 0
        for i in range(300):
            w = dw.Walk(dw.SEED * 100000 + 50000 + i)
            for _ in range(dw.STEPS):
                w.last = None
                name, u, before, _res, _ops = w.step()
                if not w.last:
                    continue
                v, card, _after = w.last
                snapshot = copy.deepcopy(w.fb)
                got = w.user_undo()
                if got is None:
                    self.fail_walk(w, i, f'「{name}」之後馬上復原卻被擋下')
                if w.fb[v] != card:
                    self.fail_walk(w, i, f'「{name}」復原之後卡片不一樣')
                n += 1
                w.fb = snapshot                                  # 回到做完那一步的樣子,繼續走
        self.assertGreater(n, 500)

    def test_the_same_walk_twice_is_the_same(self):
        """失敗訊息印的種子重跑得出同一串。"""
        def run(seed):
            w = dw.Walk(seed)
            for _ in range(20):
                w.step()
            return w.log, w.ops, w.fb
        for i in range(3):
            self.assertEqual(run(dw.SEED * 100000 + i), run(dw.SEED * 100000 + i))


if __name__ == '__main__':
    unittest.main()
