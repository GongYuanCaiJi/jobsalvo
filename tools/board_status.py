#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_status —— 看板現在卡在哪,以及「可投遞」之前的檔案檢查。

兩個用途,同一份判斷:
  1. 接手時先跑它,一眼看出各階段幾張、哪張卡住、卡在什麼。
     以前要翻舊對話猜狀態,猜錯就重做。
  2. --write 把結果寫進看板,他打開就看得到,不用問人。

把關的規矩:
  待你決定／可投遞 → 可投遞夾要建好、選的履歷與語言跟夾裡一致、檔案都在(ship.check)。

用法:
  uv run python tools/board_status.py                 # 看報告
  uv run python tools/board_status.py --write         # 順便寫進看板
"""
import sys, os, json, argparse, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd
import ship
import card

STAGE = {'prep': '準備履歷', 'ready': '已準備', 'ship': '可以投了'}
SENT = {'like': '喜歡', 'meh': '普通', 'dislike': '不喜歡', 'grow': '差一點', 'techerr': '出錯了'}
ISSUES_FOUND = 3  # 卡片有阻擋驗收問題；程式本身沒有失敗
SCHEMA_VERSION = 3  # 寫進看板的 status 格式;讀的一方(form_record.ship_blocked)認這個號碼


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--board', default=bd.LIVE)
    ap.add_argument('--write', action='store_true', help='把結果寫進看板,打開就看得到')
    a = ap.parse_args()

    p = bd.load(a.board)
    jobs, FB = p['data']['jobs'], json.loads(p['fb'])

    stages = {k: [] for k in STAGE}
    sent = {k: 0 for k in SENT}
    untouched = 0
    for j in jobs:
        f = FB.get(j['id']) or {}
        if f.get('rm'):
            continue                   # 他已經移除的卡不列入目前管線
        if f.get('app') in stages:
            stages[f['app']].append(j)
        elif f.get('s'):
            sent[f['s']] = sent.get(f['s'], 0) + 1
        else:
            untouched += 1

    issues = []
    for st in ('ready', 'ship'):
        for j in stages[st]:
            for m in ship.check(j, FB):   # 只驗使用者真正要投的那一份(履歷 × 語言,看板上的決定優先)
                issues.append({'jid': j['id'], 't': card.name(j), 'stage': st,
                               'kind': 'files', 'msg': m})

    print(f'看板:{len(jobs)} 個職缺 · 未看 {untouched}')
    print('  已標:' + '、'.join(f'{SENT[k]} {v}' for k, v in sent.items() if v))
    print('  管線:' + '、'.join(f'{STAGE[k]} {len(v)}' for k, v in stages.items()))
    if not issues:
        print('\n✅ 管線裡每張卡的要寄檔案都已建好。')
    else:
        print(f'\n⚠ {len(issues)} 個問題:')
        for x in issues:
            print(f'  [{STAGE[x["stage"]]}] {x["t"]} ← {x["msg"]}')

    if a.write:
        status = {
            'schema_version': SCHEMA_VERSION,
            'at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
            'issues': [dict({'jid': x['jid'], 't': x['t'], 'stage': x['stage'],
                             'kind': x['kind'], 'msg': x['msg']}) for x in issues],
        }
        old = p['data'].get('status') or {}
        old_comparable = {k: old.get(k) for k in ('schema_version', 'issues')}
        new_comparable = {k: status[k] for k in ('schema_version', 'issues')}
        if old_comparable == new_comparable:
            print('\n看板驗收狀態沒有變化,不重寫看板。')
            return ISSUES_FOUND if issues else 0
        # 在鎖裡只改 status 這一欄(其他資料以現行看板為準,開跑之後別人寫進去的不會被蓋掉)
        def put(data, fb):
            data['status'] = status
        bd.set_data(put, live=a.board)
        print(f'\n已寫進看板({len(issues)} 個問題),重整就看得到。')
    return ISSUES_FOUND if issues else 0


if __name__ == '__main__':
    sys.exit(main())
