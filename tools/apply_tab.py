#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_tab —— 程式自己看 agent 留著的那一頁(沒有 AI):讀頁面用的函式、讀回來之後的比對,和指令列的 read / shot。

真的去讀、去截由這張卡的門路做(chrome_door.for_card:那一頁所在的 ego 工作區);讀頁、填格子在頁面裡跑的函式(READ_FORM、FILL_FN、PROFILE_FN)也在 chrome_door。

用法:
  uv run python tools/apply_tab.py read --url 職缺網址 [--board B]
  uv run python tools/apply_tab.py shot --url 職缺網址 --out 檔案.png [--board B]
"""
import os, sys, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_cells                     # noqa: E402


def _lookup(url, board=None):
    """看板上這張卡記的分頁,和開那一頁的那一家的門路(chrome_door.for_card:沒記到、那一家不能用了丟 Unreachable)。"""
    import board_doc as bd
    fb = json.loads(bd.load(board)['fb'])
    a = (fb.get(url) or {}).get('apply') or {}
    if a.get('workspace') and not a.get('tab_id'):
        raise LookupError('程式不知道是哪一頁:agent 沒寫填表的是哪個分頁,或那個分頁不在最後一次拍改後裡')
    if not (a.get('session') or a.get('workspace')) or not a.get('tab_id'):
        raise LookupError('看板上沒有記這張是哪一段對話、哪個分頁')
    import chrome_door
    return a['tab_id'], chrome_door.for_card(a)


# 表單題目與頁面欄位的關聯只定義一次，配答案與記錄觀察共用。
Problem = gate_cells.Problem
CHOICE = ('radio', 'checkbox')


def group_key(field, i):
    """同一組選項(radio、勾選同一個 name)是同一題;其他每一格自己一題。"""
    return (field.get('type'), field.get('name')) if field.get('type') in CHOICE and field.get('name') else i


def questions(fields):
    """每一格的題目(跟 fields 一一對應):瀏覽器替讀屏軟體算好的名稱,字照讀到的樣子。不是選項類的看格子自己的名稱;
    選項類(radio、勾選,名稱是選項本身)看這一組的名稱(fieldset 的 legend、radiogroup)。瀏覽器沒給名稱的格子是空字串:
    前面哪一行是它的題目由 agent 看頁面判斷,程式不照閱讀順序猜(ADR-0011)。"""
    return [str(f.get('group') or '') if f.get('type') in CHOICE else str(f.get('label') or '') for f in fields]


def _stop_on_term():
    """看板的 👀 時間到會先送 SIGTERM:轉成一般的結束,正在跑的 ego-browser 子程序跟著收掉(subprocess.run 碰到例外會殺子程序)。"""
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(3))


def main():
    _stop_on_term()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=('read', 'shot'))
    ap.add_argument('--url', required=True)
    ap.add_argument('--board')
    ap.add_argument('--out')
    a = ap.parse_args()
    try:
        import contextlib
        import evidence
        # 👀 看現在的頁面每幾秒截一次,只是給他看、不是證據:記成一輪的話,看幾分鐘就把每張卡只留 30 輪的額度擠滿,
        # 填表那一輪(題目附的截圖)被刪掉(#447)
        with (contextlib.nullcontext() if a.cmd == 'shot' else evidence.opened('apply_check', a.cmd, [a.url], a.board)):
            tid, door = _lookup(a.url, a.board)
            if a.cmd == 'read':
                from web import seen
                from web.outside import Real
                print(json.dumps(seen.form(Real(door), tid), ensure_ascii=False, indent=1))
            else:
                door.shot(tid, a.out or 'tab.png')
                print(a.out or 'tab.png')
    except Exception as e:  # noqa: BLE001 — 指令列最外層:原因照實印出、結束碼 2(看板的 👀 照結束碼講)
        print(f'看不到那一頁:{e}', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
