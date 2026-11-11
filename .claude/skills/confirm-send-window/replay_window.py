#!/usr/bin/env python3
"""用真程式重演 #739(WindowKept 的反例):第一次確認的計時器到點、apply_run 已經派出去,
他取消再重新確認;apply_run 帶著第一次確認的編號走到 form_record.start_submit。
修好之後要擋下(卡留在你已確認,等第二次確認自己的計時器);印 REPRODUCED 就是又壞了。
用法:uv run python .claude/skills/confirm-send-window/replay_window.py   (印 REPRODUCED 或 NOT REPRODUCED)"""
import os, sys
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import delivery_state as ds   # noqa: E402
import form_record as fr      # noqa: E402

U = 'https://example.test/job/1'


def main():
    form = {'f': [{'k': 'q', 'v': 'a'}]}
    fb = {U: {'app': 'ship', 'ds': 'parked', 'form': form,
              'apply': {'at': 'fill-1', 'tab_id': 't1', 'delivery': {'method': 'direct_upload'}}}}
    snap = {'q': 'a'}
    # 投遞前驗收、答案庫那幾關跟這條路徑無關:固定放行,只留 approval_problem 自己對狀態與確認的檢查
    with mock.patch.object(fr, 'ship_blocked', lambda *a, **k: None), \
         mock.patch.object(fr, 'answers_pending', lambda *a, **k: False), \
         mock.patch.object(fr, 'platform_answers_pending', lambda *a, **k: False), \
         mock.patch.object(fr, '_bank', lambda *a, **k: []), \
         mock.patch.object(fr, 'snapshot', lambda *a, **k: snap):
        # 1. 10:00:00 他按確認送出(board_server._event 記 approve.at),queue_send 排 8 秒
        ds.fire(fb, U, 'confirm', approve=fr.approval(fb, U, '10:00:00'))
        first_at, first_id = fb[U]['approve']['at'], fb[U]['approve']['id']
        # 2. 10:00:08 計時器到點:_send_confirmed 的檢查過了(狀態、at、編號都對),start_run 帶著編號派出 apply_run
        assert ds.state(fb[U]) == 'confirmed' and fb[U]['approve']['id'] == first_id
        # 3. apply_run 還在送出前讀頁(幾秒),他按「取消確認、復原」,馬上又按確認送出(10:00:09)
        ds.fire(fb, U, 'unconfirm')
        ds.fire(fb, U, 'confirm', approve=fr.approval(fb, U, '10:00:09'))
        # 4. apply_run 走到鎖內的 start_submit,帶的是派它出來的第一次確認的編號
        problem = fr.start_submit(fb, U, approval_id=first_id)
    sent = problem is None and ds.state(fb[U]) == 'sending'
    if sent:
        print('REPRODUCED')
        print(f'  送出的是 at={fb[U]["approve"]["at"]} 那一次確認,它按下去才 0 秒;派出這一輪的是 at={first_at} 的計時器')
        return 1
    print(f'NOT REPRODUCED: {problem!r} / {ds.state(fb[U])}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
