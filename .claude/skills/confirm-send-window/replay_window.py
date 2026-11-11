#!/usr/bin/env python3
"""用真程式重演 WindowKept 的反例(模型以外的證據):第一次確認的計時器到點、apply_run 已經派出去,
他取消再重新確認;apply_run 走到 form_record.start_submit 時照樣送出第二次確認,沒等 8 秒。
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
        ds.fire(fb, U, 'confirm', approve={'snap': snap, 'round': 'fill-1', 'tab': 't1', 'at': '10:00:00'})
        first_at = fb[U]['approve']['at']
        # 2. 10:00:08 計時器到點:_send_confirmed 的檢查過了(狀態對、at 對),start_run 派出 apply_run
        assert ds.state(fb[U]) == 'confirmed' and fb[U]['approve']['at'] == first_at
        # 3. apply_run 還在送出前讀頁(幾秒),他按「取消確認、復原」,馬上又按確認送出(10:00:09)
        ds.fire(fb, U, 'unconfirm')
        ds.fire(fb, U, 'confirm', approve={'snap': snap, 'round': 'fill-1', 'tab': 't1', 'at': '10:00:09'})
        # 4. apply_run 走到鎖內的 start_submit:只看狀態與確認綁的那一輪、那一頁、答案,不看 at
        problem = fr.start_submit(fb, U)
    sent = problem is None and ds.state(fb[U]) == 'sending'
    print('REPRODUCED' if sent else f'NOT REPRODUCED: {problem!r} / {ds.state(fb[U])}')
    print(f'  送出的是 at={fb[U]["approve"]["at"]} 那一次確認,它按下去才 0 秒;派出這一輪的是 at={first_at} 的計時器')
    return 0 if sent else 1


if __name__ == '__main__':
    sys.exit(main())
