#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stale —— 過時的東西清掉(GLOSSARY「過時」)。

範圍只有兩類:agent 的回報,和 agent 推出來、使用者還沒確認的常用答案。產生它的那條規則改了、
或造成它的那個 bug 修掉了,它就過時:清掉底下那一筆(不只是畫面上那一行,不然下次又冒出來),
那一題之後照正常流程由 agent 代填(form_record.redo)。使用者確認過或改過的常用答案永遠不算過時;也不是整張卡重來。

每改掉一條會留下東西的規則、修掉一個會留下東西的 bug,在 CHANGES 加一筆:
  id       這一筆的名字
  at       規則改掉、bug 修掉的那一天;這之前留下的才算它產生的。
           None:新程式第一次跑的那一刻(記在 <home>/stale.json,之後固定用那個時間,所以只清得到舊程式留下的)
  why      一句話,收掉的回報上寫這句
  answers  'empty':agent 推出來、還沒確認、答案空著的(規則改成每一題都要代填之前,它留空不寫)
  reports  {'from': 回報來源開頭, 'msg': 回報內容的樣子(正規表示式)}
伺服器更新後起來時(board_server.migrate_marks)掃一次。
"""
import datetime
import json
import os
import re

import agent_report
import config as cf
import form_record as fr

CHANGES = (
    {'id': 'answer-every-question', 'at': '2026-09-22',
     'why': '「每一題都要代填」之前 agent 留空沒寫的答案,改由 agent 代填',
     'answers': 'empty'},
    {'id': 'platform-resume-program-decides', 'at': None,
     'why': '以前平台履歷由 agent 選、附件拿錯的那一份比,這則回報是那個 bug 產生的;重填時照程式決定的那一份核對',
     'reports': {'from': agent_report.FROM_APPLY,
                 'msg': r'平台附件「.*」(少了|多出)|平台履歷與核准時不同|固定版還是客製版|不能把它放進固定平台履歷'}},
)


def _answer_stale(e, change, before):
    if change.get('answers') != 'empty' or not isinstance(e, dict):
        return False
    # 使用者確認過(at)或改過的都不是 agent 推的;只看 agent 推出來、還沒確認的
    return bool(e.get('inf')) and not e.get('at') and fr.empty_answer(e) and str(e['inf']) < before


def _report_stale(it, change, before):
    rule = change.get('reports')
    if not rule or it.get('done'):
        return False
    return (str(it.get('from') or '').startswith(rule['from']) and bool(re.search(rule['msg'], str(it.get('msg') or '')))
            and str(it.get('at') or '') < before)


def boundaries(now=None, changes=CHANGES):
    """每一筆算到哪個時間以前:at 寫死的照 at;None 的照新程式第一次跑的那一刻(第一次看到就記進 stale.json)。"""
    now = now or datetime.datetime.now().isoformat(timespec='seconds')
    path = cf.path('stale.json')
    try:
        with open(path, encoding='utf-8') as f:
            seen = json.load(f)
        seen = seen if isinstance(seen, dict) else {}
    except (OSError, ValueError):
        seen = {}
    new = {c['id']: now for c in changes if not c.get('at') and c['id'] not in seen}
    if new:
        seen.update(new)
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(seen, f, ensure_ascii=False, indent=1)
    return {c['id']: c.get('at') or seen[c['id']] for c in changes}


def sweep(fb, before, today=None, changes=CHANGES):
    """把規則改變、bug 修正產生、還留著的東西清掉(before:boundaries 算的每一筆的時間)。回清了幾筆;
    清過的不會再符合(答案變成等 agent 代填、回報收進已處理),同一份看板再掃一次是 0。"""
    today = today or datetime.date.today().isoformat()
    n = 0
    for change in changes:
        for e in list(fb.get('__ans__') or []):
            if _answer_stale(e, change, before[change['id']]):
                fr.redo(fb, e['k'], today)
                n += 1
        for it in fb.get(agent_report.KEY) or []:
            if _report_stale(it, change, before[change['id']]):
                it['done'], it['res'] = today, '過時:' + change['why']
                n += 1
    return n
