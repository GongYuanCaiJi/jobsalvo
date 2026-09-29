#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_doc —— 板子 HTML 的「解析 + 組裝」單一真相。
以前 PRE/M1.../SUF 那段接字串在 build_board / fold_pipeline / publish_board 各抄一份;
現在只有這裡一份,誰要組板都 import。改板結構只改這裡。
"""
import re,json,sys,os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as _cf

# 外掛裝進 data 的鍵,不是管線產的;install 一律沿用現行看板那份。
EXTRA = ('bank',)

def pre():
    """文件開頭。"""
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            'initial-scale=1"><title>jobsalvo</title><style id="sty">')
M1='</style></head><body><div class="wrap"><div id="hdr"></div><div id="tabs" class="tabs"></div><div id="app"></div></div><template id="t-hdr">'
M2='</template><template id="tail">'
M3='</template><script id="data-jobs" type="application/json">'
M4='</script><script id="data-fb" type="application/json">'
M5='</script><script id="app-src">'
SUF='</script></body></html>'

def inner(h,p):
    m=re.search(p,h,re.S)
    if not m: sys.exit("找不到區塊 "+p[:40])
    return m.group(1)

def parse(html):
    """把一份板子 html 拆成各區塊。data-jobs 回 dict、data-fb 回原字串(逐字搬他標記,不動)。"""
    return {
      'sty':  inner(html,r'<style id="sty">(.*?)</style>'),
      'thdr': inner(html,r'<template id="t-hdr">(.*?)</template>'),
      'tail': inner(html,r'<template id="tail">(.*?)</template>'),
      'app':  inner(html,r'<script id="app-src">(.*?)</script>'),
      'data': json.loads(inner(html,r'<script id="data-jobs" type="application/json">(.*?)</script>')),
      'fb':   inner(html,r'<script id="data-fb" type="application/json">(.*?)</script>'),
    }

def stat_first(thdr, data):
    """t-hdr 裡 stat-first 數字 = 非 bk 的職缺數。"""
    nonbk=sum(1 for j in data['jobs'] if not j.get('bk'))
    return re.sub(r'(<b id="stat-first">)\d+(</b>)', r'\g<1>%d\g<2>'%nonbk, thdr, count=1)

def assemble(sty, thdr, tail, data, fb_str, app):
    """組回可發布的完整 doc。app 不得含字面 </script>。"""
    assert '</script>' not in app, "app-src 含字面 </script>,會截斷"
    return (pre()+sty+M1+thdr+M2+tail+M3+json.dumps(data,ensure_ascii=False)
            +M4+fb_str+M5+app+SUF)


# ---- 裝回現行看板 ----------------------------------------------------------
# 管線每支都吐一個 merged.html,由這裡把它變成現行看板:驗過形狀才換,而且一律以
# 「現在看板上的標記」為準。管線跑了半小時,這半小時使用者在手機上標的東西不能被半小時前的快照蓋掉。
import os as _os
HOME = _cf.HOME
LIVE = _cf.LIVE

import fcntl as _fcntl
from contextlib import contextmanager as _cm

@_cm
def live_lock(live=LIVE):
    """跨行程序列化對現行看板的『讀→改→寫』。手機存檔(board_server)、裝板(reconcile/ctb)、
    cut_tailor 收尾可能同時想寫同一個檔;os.replace 本身原子,但『讀舊的→改→寫』整段沒鎖
    就會互蓋(A 讀、B 讀、A 寫、B 拿舊的蓋掉 A 的改動)。用檔案鎖把整段包住。"""
    f = open(live + '.lock', 'w')
    try:
        _fcntl.flock(f, _fcntl.LOCK_EX); yield
    finally:
        try: _fcntl.flock(f, _fcntl.LOCK_UN)
        finally: f.close()

def is_live(path, live=None):
    """path 是不是現行看板(不是副本)。任一邊不存在就不是。"""
    try:
        return _os.path.samefile(path, live or LIVE)
    except OSError:
        return False

def _read(p):
    with open(p, encoding='utf-8') as f: return f.read()

def install(src, live=None, quiet=False):
    """把管線產物 src 裝成現行看板。回傳 (職缺數, 保留的標記數)。"""
    live = _write_target(live)
    p = parse(_read(src))
    if not p['data'].get('jobs'):
        sys.exit(f'{src} 裡沒有職缺,不敢裝。')
    if ':root{' not in p['sty'].replace(' ', ''):
        sys.exit(f'{src} 的樣式區看起來不是 CSS,不敢裝。')
    # 管線產物只提供一樣東西:職缺資料。標記、樣式、程式、頁首一律取現行看板。
    # 管線一開跑就複製一份快照,跑一兩個小時才收尾;以前連 sty/app 也照抄快照,
    # 等於把這期間所有 board.css/board.js 的修改整包倒回去(外殼會自己變回舊版,
    # 行為時好時壞)。外殼的真相在 board/*,由 apply_shell 灌進現行看板。
    with live_lock(live):
        if _os.path.isfile(live):
            lp = parse(_read(live))
            fb, sty, tail, app = lp['fb'], lp['sty'], lp['tail'], lp['app']
            thdr = stat_first(lp['thdr'], p['data'])   # 頁首數字要跟著新的職缺數走
            # 不是管線產的資料(例如外掛另外裝進來的分頁內容)在 data 的 EXTRA 鍵裡。
            # 管線的產物裡要嘛沒有、要嘛是開跑時的舊版,一律沿用現行看板那份。
            for k in EXTRA:
                if k in lp['data']:
                    p['data'][k] = lp['data'][k]
        else:
            fb, sty, thdr, tail, app = p['fb'], p['thdr'], p['tail'], p['app']
        json.loads(fb)
        doc = assemble(sty, thdr, tail, p['data'], fb, app)
        tmp = live + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: f.write(doc)
        _os.replace(tmp, live)
    marks = sum(1 for v in json.loads(fb).values() if isinstance(v, dict) and v.get('s'))
    if not quiet:
        print(f'已裝成現行看板 → {live}(職缺 {len(p["data"]["jobs"])} · 保留標記 {marks})')
        print('看板 server 直接讀這個檔,他重整就看得到。不需要再發布到任何地方。')
    return len(p['data']['jobs']), marks

import shutil as _sh
SCRATCH = _os.path.join(_cf.TMP, 'board-pipeline')

def scratch(live=LIVE):
    """管線的中間檔。work 是現行看板的複本(會被就地改動),out 是產物;跑完用 install(out)
    裝回去。中間檔放 /tmp,不進 repo——它們是過程,不是真相。"""
    _os.makedirs(SCRATCH, exist_ok=True)
    work = _os.path.join(SCRATCH, 'work.html')
    out = _os.path.join(SCRATCH, 'merged.html')
    _sh.copyfile(live, work)
    return work, out


# ---- 標記流水帳 ----
# 每一次改到使用者的標記,把真的變了的那幾筆「從什麼變成什麼」附加一行。只附加,不覆寫,不刪。
# 看板只留最後一個狀態,寫壞了沒有第二份可以對;使用者也不可能記得自己標過什麼。
# 有這行就查得出幾點、誰、哪一筆、原本是什麼,而且救得回來(tools/board_marks.py 看紀錄、倒回)。
# 程式直接寫的(cut_tailor 收尾、閘門)也要記,不只看板伺服器的存檔:程式一次蓋掉十幾張標記的時候,
# 沒有這行就只能從舊副本拼回來。
import datetime as _dt

def journal_path(live=LIVE):
    return _os.path.splitext(live)[0] + '-marks.jsonl'

def journal(live, chg, by=''):
    if not chg: return
    try:
        rec = {'t': _dt.datetime.now().isoformat(timespec='seconds'), 'n': len(chg), 'd': chg}
        if by: rec['by'] = by
        with open(journal_path(live), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:  # noqa: S110
        pass   # 流水帳壞掉不能擋住存檔

def fb_diff(old, new):
    return {k: [old.get(k), new.get(k)] for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)}


def _write_target(live):
    """An assigned agent board overrides implicit and explicit caller defaults."""
    return _os.environ.get('AGENT_BOARD') or live or LIVE


def set_fb(mutate, live=None, by=''):
    """改使用者的標記(data-fb)。install() 一律拿現行看板的 fb(那是為了保住他手機上的
    最新標記),所以不能用 install 來改 fb——會把改動丟掉重讀舊的。要改就走這裡。
    mutate 收到 fb dict、就地改、不用回傳。每一次都記進流水帳(by 寫是誰改的,預設是程式名)。"""
    live = _write_target(live)
    with live_lock(live):
        p = parse(_read(live))
        before = json.loads(p['fb'])
        fb = json.loads(p['fb'])
        mutate(fb)
        doc = assemble(p['sty'], p['thdr'], p['tail'], p['data'],
                       json.dumps(fb, ensure_ascii=False), p['app'])
        tmp = live + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: f.write(doc)
        _os.replace(tmp, live)
        journal(live, fb_diff(before, fb), by or _os.path.basename(sys.argv[0] or '') or 'script')
    return fb


def set_data(mutate, live=None):
    """改職缺資料(data-jobs)。在鎖裡讀現行看板、改、寫回,不拿舊快照蓋。
    mutate(data, fb) 就地改 data;fb 只給它讀。"""
    live = _write_target(live)
    with live_lock(live):
        p = parse(_read(live))
        mutate(p['data'], json.loads(p['fb']))
        doc = assemble(p['sty'], stat_first(p['thdr'], p['data']), p['tail'], p['data'], p['fb'], p['app'])
        tmp = live + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: f.write(doc)
        _os.replace(tmp, live)


def add_jobs(src, live=None, quiet=False):
    """找缺那一輪的收尾:只把產物裡「現行看板還沒有的職缺」加進去,既有職缺只換摘要
    (card-summaries 的新內容),其他一律以現行看板為準。回傳新增幾筆。

    為什麼不用 install:install 拿產物的整份職缺資料換掉現行的。找缺的產物是那一輪開跑時
    複製的快照,一輪要跑將近一小時;這段時間準備區產出的履歷文字、投遞前驗收結果、
    連結復活的標記,會整個被倒回開跑那一刻。

    跟 reconcile 拿同一把鎖:reconcile 是「複製一份 → 改 → 整份裝回」,
    中途插進來的新職缺會被它裝回去的那份蓋掉。"""
    live = _write_target(live)
    p = parse(_read(src))
    with open(_os.path.join(HOME, '.reconcile.lock'), 'w') as lk:
        _fcntl.flock(lk, _fcntl.LOCK_EX)
        with live_lock(live):
            lp = parse(_read(live)); data = lp['data']
            have = {j.get('id'): j for j in data.get('jobs', [])}
            added = 0
            for j in p['data'].get('jobs', []):
                lj = have.get(j.get('id'))
                if lj is None:
                    data['jobs'].append(j); have[j.get('id')] = j; added += 1
                    continue
                s = j.get('sum')
                if isinstance(s, dict) and 'fit' in s:
                    s = dict(s); s.pop('cuts', None)
                    cur = (lj.get('sum') or {}).get('cuts')   # 切角判斷是做履歷那步寫的,以現行為準
                    if cur: s['cuts'] = cur
                    lj['sum'] = s
            doc = assemble(lp['sty'], stat_first(lp['thdr'], data), lp['tail'], data, lp['fb'], lp['app'])
            tmp = live + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f: f.write(doc)
            _os.replace(tmp, live)
    if not quiet:
        print(f'新職缺併進現行看板 → {live}(新增 {added} 筆,其餘職缺資料保留現行)')
    return added
