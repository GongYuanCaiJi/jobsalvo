#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_marks —— 看標記流水帳、把被蓋掉的標記還原回來。

看板只留最後一個狀態。board_server 每次存檔會把真的變了的那幾筆
附加一行到 <看板檔名>-marks.jsonl(只附加、不覆寫)。這支是它的讀取與還原端。

  uv run python tools/board_marks.py                 # 最近 20 筆改動
  uv run python tools/board_marks.py --since 30m     # 近 30 分鐘(或 --since 2026-09-21T07:46)
  uv run python tools/board_marks.py --undo-since 07:46   # 把那個時間點之後的改動全部倒回去

還原是照時間倒著把每一筆改回原值,所以同一筆被改過好幾次也會回到最早那個值。
"""
import os, sys, json, argparse, datetime, re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd
import card

LIVE = bd.LIVE

journal_path = bd.journal_path   # 流水帳的位置與格式只有 board_doc 一份

def load(live):
    p = journal_path(live)
    if not os.path.isfile(p): sys.exit(f'還沒有流水帳({p})。board_server 存過一次才會有。')
    out = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try: out.append(json.loads(line))
                except Exception: pass  # noqa: S110
    return out

def titles(live):
    p = bd.parse(open(live, encoding='utf-8').read())
    t = {}
    for j in p['data'].get('jobs', []):
        t[j['id']] = card.name(j) or j['id']   # 卡片名字只由 card.py 算
    return t

def parse_when(s):
    """吃 '30m' / '2h' / '07:46' / 完整 ISO。"""
    now = datetime.datetime.now()
    m = re.fullmatch(r'(\d+)([mh])', s)
    if m:
        n = int(m.group(1))
        return now - datetime.timedelta(minutes=n if m.group(2) == 'm' else n * 60)
    if re.fullmatch(r'\d{1,2}:\d{2}', s):
        h, mi = s.split(':')
        return now.replace(hour=int(h), minute=int(mi), second=0, microsecond=0)
    return datetime.datetime.fromisoformat(s)

FIELD = {'s': '心情', 'app': '階段', 'rm': '移除', 'n': '備註', 'cust': '客製', 'sent_at': '投遞日'}
def brief(v):
    if v is None: return '(沒有)'
    if isinstance(v, dict):
        return '、'.join(f'{FIELD.get(k,k)}={v[k]}' for k in v if k != 'n') or '(空)'
    return str(v)

def show(recs, tt, limit):
    for r in recs[-limit:]:
        print(f"\n{r['t']}  改了 {r['n']} 筆" + (f"({r['by']})" if r.get('by') else ''))
        for jid, (old, new) in r['d'].items():
            print(f"  {tt.get(jid, jid)[:40]}")
            print(f"      {brief(old)}  →  {brief(new)}")

def undo(recs, live, cut):
    back = {}
    for r in recs:                                   # 舊到新掃一遍
        if datetime.datetime.fromisoformat(r['t']) < cut: continue
        for jid, (old, new) in r['d'].items():
            back.setdefault(jid, old)                # 只記第一次看到的舊值 = 最早的值
    if not back:
        print('那個時間點之後沒有改動。'); return
    def mut(fb):
        for jid, old in back.items():
            if old is None: fb.pop(jid, None)
            else: fb[jid] = old
    bd.set_fb(mut, live, by='board_marks 倒回')
    print(f'倒回了 {len(back)} 筆(以 {cut.isoformat(timespec="seconds")} 之前的值為準)。重整看板就看得到。')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live', default=LIVE)
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--since')
    ap.add_argument('--undo-since')
    a = ap.parse_args()
    recs = load(a.live); tt = titles(a.live)
    if a.undo_since:
        undo(recs, a.live, parse_when(a.undo_since)); return
    if a.since:
        cut = parse_when(a.since)
        recs = [r for r in recs if datetime.datetime.fromisoformat(r['t']) >= cut]
        print(f'{cut.isoformat(timespec="seconds")} 之後有 {len(recs)} 次存檔')
    show(recs, tt, a.limit if not a.since else len(recs))

if __name__ == '__main__':
    main()
