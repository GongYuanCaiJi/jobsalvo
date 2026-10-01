#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate —— 安檢門(GLOSSARY「安檢門」「交件單」):agent 交回來的事實進到程式的唯一入口。

每一種會叫 agent 的流程,agent 交一張交件單(幫你填表:填表、修改的 fill.json、送出前核對的 pre-submit.json、
送出的 submit.json;其他 7 種流程各自的一份,見 FILES)。單子本身不是證據:程式只准用這裡核對過的格子。

  read(out, kind)              唯一打開交件單的地方(其他地方直接讀,tests/test_gate.py 的結構測試就失敗)
  inspect(kind, sheet, truth)  每一格跟程式自己看到的真相(Truth:頁面、紀錄、檔案、程式自己的設定)比;
                               回 Verdict:problems 每一條講「哪一格、agent 說什麼、實際是什麼」,facts 只有登記過的格子
  page_changes(seen, page)     按確認送出前、送出前:程式讀到的那一頁跟驗收時核對過的樣子比,哪一格從什麼變成什麼
  after_send(page, form_url)   按了送出之後:程式讀到的那一頁是沒送出(還停在申請表)、送出了(有成功的字),還是判斷不了

每一格都登記核對方式(SHEETS;幫你填表的在 gate_apply,其他 7 種流程的在 gate_flows,型別在 gate_cells),四種:
  核對       跟程式看到的比,對不上就擋
  程式決定   程式已經知道答案的事(用哪一份平台履歷、固定版還是客製版):agent 不用寫;寫了就要跟程式決定的一樣
  agent 的話 agent 自己的觀察(卡住的地方、筆記):照原文給人看,標明是 agent 說的,程式不拿它當事實、不拿它放行
  agent 判斷 程式核對不了的判斷(一封信算拒絕還是面試):不進 facts,用的地方標明「agent 判斷」、附原文、可以復原
沒登記的格子不進 facts,程式用不到(tests/test_gate.py 另有測試:指示裡叫 agent 寫的每一格都要登記)。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence  # noqa: E402
# 用的地方只 import gate:幫你填表的頁面判斷、型別從這裡拿(F401:這幾個是給別處用的)
from gate_apply import (FILL, FORM_STILL, NOT_SENT, PRE_SUBMIT, SENT, SENT_WORDS, SUBMIT, after_send,  # noqa: E402,F401
                        page_changes, seen)
from gate_cells import (CHECK, DECIDED, JUDGED, UNUSED, WORDS, Row, RowTruth, Truth,  # noqa: E402,F401
                        Verdict, short)
from gate_flows import (CUSTOMIZE, LINK_STATUS, POSTED_AT, PREPARE, REPLY, RESEARCH_JUDGE,  # noqa: E402
                        RESEARCH_SEARCH, SUGGEST_CATS)

# 每一種交件單的檔名(* 是每一輪、每一批不一樣的那一段)。程式裡別處打開這些檔,tests/test_gate.py 的結構測試就失敗
FILES = {'fill': 'fill.json', 'pre_submit': 'pre-submit.json', 'submit': 'submit.json',
         'posted_at': 'posted_age_results.json', 'link_status': 'verdicts.json',
         'suggest_cats': 'suggest.json', 'customize': 'feedback_reports.json', 'prepare': 'fill.json',
         'research_search': 'search_*.json', 'research_judge': 'judge_*.json', 'reply': 'replies.json'}
# 整張就是一個清單的交件單(找缺交的候選、判斷):讀進來當成 {清單名: 清單}
LIST_SHEETS = {'research_search': 'candidates', 'research_judge': 'jobs'}
NESTED = ('posting', 'profile', 'delivery', 'fixed_profile')   # 交件單裡這幾格底下還有格子,一格一格登記
# 一列一列的交件單(其他 7 種流程:每一列是一張卡、一個來源):{種類: {清單名: 那一列用哪一格認是誰}}。
# 每一列各自核對:對不上的那一列不收、原因寫出是哪一列,別列照收
ROWS = {'posted_at': {'dates': 'url', 'inaccessible': 'url'}, 'link_status': {'jobs': 'id'},
        'suggest_cats': {'categories': 'name', 'tags': 'name'}, 'customize': {'reports': 'issue'},
        'research_search': {'candidates': 'url'}, 'research_judge': {'jobs': 'id'},
        'reply': {'findings': 'url', 'job_ids': 'id', 'inaccessible': 'source'}}

SHEETS = {'fill': FILL, 'pre_submit': PRE_SUBMIT, 'submit': SUBMIT, 'posted_at': POSTED_AT,
          'link_status': LINK_STATUS, 'suggest_cats': SUGGEST_CATS, 'customize': CUSTOMIZE, 'prepare': PREPARE,
          'research_search': RESEARCH_SEARCH, 'research_judge': RESEARCH_JUDGE, 'reply': REPLY}


def cells(sheet):
    """交件單攤平成 {格子名: 值}(posting.title 這種);不是物件的回空的。"""
    out = {}
    if not isinstance(sheet, dict):
        return out
    for name, value in sheet.items():
        if name in NESTED and isinstance(value, dict):
            for sub, inner in value.items():
                out[f'{name}.{sub}'] = inner
        else:
            out[name] = value
    return out


def _check(table, flat, whole, truth, where=''):
    """一張(或一列)交件單照登記的方式核對:回 (facts, judged, said, problems, unregistered)。"""
    problems, facts, judged, said, unregistered = [], {}, {}, {}, []
    for name, cell in table.items():
        missing = cell.required(truth) if callable(cell.required) else cell.required
        if name not in flat and missing and missing not in problems:
            problems.append(missing)
    for name, value in flat.items():
        cell = table.get(name)
        if cell is None:
            unregistered.append(name)
            continue
        got = cell.fn(value, whole, truth) if cell.fn else None
        if got is UNUSED:
            continue
        if isinstance(got, list):
            problems += [p for p in got if p not in problems]
        elif got:
            problems.append(f'交件單「{cell.name}」{where}:agent 說 {short(value)},實際是 {got}')
        keep = said, (judged if cell.how == JUDGED else facts)
        head, _, sub = name.partition('.')
        for box in (keep if not got or cell.how == WORDS else (said,)):
            if sub:
                box.setdefault(head, {})[sub] = value
            else:
                box[name] = value
    return facts, judged, said, problems, unregistered


def _rows(kind, head, value, truth):
    """一列一列的清單:每一列各自核對。回 ([Row], 整張的問題, 沒登記的格子)。"""
    table = {name.partition('.')[2]: cell for name, cell in SHEETS[kind].items() if name.startswith(head + '.')}
    if not isinstance(value, list):
        return [], [f'交件單「{head}」不是一個清單'], []
    rows, problems, loose = [], [], []
    by = ROWS[kind][head]
    for row in value:
        if not isinstance(row, dict):
            problems.append(f'交件單「{head}」裡有一列不是物件:{short(row)}')
            continue
        key = str(row.get(by) or '')
        facts, judged, _said, bad, unregistered = _check(table, row, row, RowTruth(truth, key), f'({short(key, 80)})')
        rows.append(Row(key, row, facts, judged, bad))
        problems += [p for p in bad if p not in problems]
        loose += [f'{head}.{n}' for n in unregistered if f'{head}.{n}' not in loose]
    return rows, problems, loose


def inspect(kind, sheet, truth):
    """安檢門:交件單的每一格照登記的方式核對。回 Verdict(facts 只有登記過、核對過的格子,judged 是 agent 判斷,
    problems 每一條一句;一列一列的清單每一列各自的結果在 rows)。"""
    table = SHEETS[kind]
    if not isinstance(sheet, dict):
        return Verdict({}, ['交件單不是一個物件'], [])
    lists = ROWS.get(kind) or {}
    flat = {k: v for k, v in cells(sheet).items() if k not in lists}
    top = {n: c for n, c in table.items() if n.partition('.')[0] not in lists}
    facts, judged, said, problems, unregistered = _check(top, flat, sheet, truth)
    rows = {}
    for head in lists:
        rows[head], bad, loose = _rows(kind, head, sheet.get(head, []), truth)
        problems += [p for p in bad if p not in problems]
        unregistered += loose
    verdict = Verdict(facts, problems, unregistered, said, judged, rows)
    _evidence(kind, verdict, truth)
    return verdict


def _evidence(kind, verdict, truth):
    """其他 7 種流程:安檢門的比對結果記進開著的那一輪證據(幫你填表那幾種 apply_run 自己記,講得更細)。
    有卡的(準備履歷一張一張核對)只記進那一張;一列一列的記每一列是誰、收下沒、判斷了什麼。"""
    if kind in ('fill', 'pre_submit', 'submit'):
        return
    rnd = evidence.active()
    if rnd is None:
        return
    rows = [{'list': head, 'key': r.key, 'ok': r.ok, 'judged': r.judged} for head, rs in verdict.rows.items() for r in rs]
    card = truth.url if truth.url and truth.url in (getattr(rnd, 'cards', None) or ()) else None
    rnd.check(verdict.problems, what=f'安檢門:{kind}', card=card, rows=rows, judged=verdict.judged,
              unregistered=verdict.unregistered)


def path(out, kind):
    return os.path.join(out, FILES[kind])


def read(out, kind, where=None):
    """讀這一輪的交件單(唯一打開它的地方)。回 (交件單, 問題);沒寫出來、寫壞了回 (None, 原因)。
    where:agent 自己跑 form_record --from-fill 時給的那個檔(沒給就是 out 底下那一份)。"""
    try:
        with open(where or path(out, kind), encoding='utf-8') as fh:
            sheet = json.load(fh)
    except (OSError, ValueError):   # 沒寫出來或寫壞了
        return None, f'沒有寫出 {FILES[kind]}'
    if kind in LIST_SHEETS and isinstance(sheet, list):
        sheet = {LIST_SHEETS[kind]: sheet}
    if not isinstance(sheet, dict):
        return None, f'{FILES[kind]} 不是一個物件'
    return sheet, ''


def claimed_tab(out, kind):
    """agent 說它留著的是哪一個分頁(還沒核對):程式拿它去讀、去截,讀得到才算核對過(見 Cell 分頁)。"""
    sheet, _ = read(out, kind)
    tid = (sheet or {}).get('tab_id')
    return str(tid) if tid not in (None, '') else None
