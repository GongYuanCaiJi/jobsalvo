#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared delivery-state table and transitions used by task entry points."""
import copy, datetime, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, 'delivery_state.json'), encoding='utf-8') as _f:
    TABLE = json.load(_f)


def _marked(flag):
    """表上標了這一項的狀態(每種狀態都要標:沒標 import 不起來)。"""
    return frozenset(s for s, x in TABLE['states'].items() if x[flag])


HELD_STATES = _marked('held')
BUSY = {s: x['busy'] or None for s, x in TABLE['states'].items()}   # 忙的原因(不忙是 None)
SWAP = _marked('swap')              # 能換檔(換履歷、語言、自己的檔、客製版)
LEAVE = _marked('leave')            # 能離開流程(移除、退回、出錯了、👎)
FILLED = _marked('filled')          # 算填好(頁上有他要看的東西)
WORKING = _marked('working')        # agent 正在做
HANDBACK = _marked('handback')      # 他在回報按了處理好了,自動流程接著重填(沒填成、卡住、頁不見、舊檔)
AUTO_FILL = {s: x['auto_fill'] for s, x in TABLE['states'].items() if x.get('auto_fill')}   # 自動流程會自己(重)填的:卡上那一句
SWAP_FIELDS = ('resume_id', 'lang', 'custom_file')     # 卡上換檔的那幾欄:換了就是換檔
# 換檔會動到的狀態(表上 files_changed 那一格不只是原地不動):頁上傳的會變成舊檔,或先記下來
FILES_MATTER = frozenset(s for s, c in TABLE['cells'].items() if c.get('files_changed') not in (None, {'to': s}))
GONE = '填好的那一頁不見了(它的工作區被關掉了),要重填'
NO_FORM = '填好了,卻沒有留下表單紀錄(看不到填了哪些欄),要再填一次'


class Forbidden(ValueError):
    """這個狀態不准發生這個事件(表上沒有那一格)。"""


def state(m):
    """這張卡的投遞狀態;沒存就是還沒填。"""
    s = (m or {}).get('ds') if isinstance(m, dict) else None
    return s if s in TABLE['states'] else 'todo'


def label(s):
    return TABLE['states'][s]['label']


def allowed(m, event):
    """這張卡現在能不能發生這個事件(表上有那一格、守門條件也過)。"""
    return why_not(m, event) is None


def why_not(m, event):
    """這張卡現在不准發生這個事件的原因(表上沒有那一格、守門條件沒過);准就回 None。fire 擋的就是這一句。"""
    if event not in TABLE['events']:
        return f'表上沒有這個事件:{event}'
    s = state(m)
    cell = TABLE['cells'][s].get(event)
    if cell is None:
        return f'「{label(s)}」時不能「{TABLE["events"][event]}」'
    if not _guard_ok(m, cell):
        return f'「{label(s)}」這張不能「{TABLE["events"][event]}」'
    return None


def _guard_ok(m, cell):
    for x in _steps(cell):
        for path, ok in (x.get('guard') or {}).items():
            if _get(m, path) not in ok:
                return False
    return True


def _steps(cell):
    """一格的效果展開成一串:先做它引用的 moves(照順序、可以再引用),最後是它自己的。"""
    out = []
    for name in cell.get('do') or []:
        out += _steps(TABLE['moves'][name])
    out.append(cell)
    return out


_MISSING = object()


def _get(m, path):
    cur = m
    for k in path.split('.'):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _pop(m, path):
    *head, last = path.split('.')
    cur = m
    for k in head:
        if not isinstance(cur, dict) or k not in cur:
            return _MISSING
        cur = cur[k]
    if not isinstance(cur, dict) or last not in cur:
        return _MISSING
    return cur.pop(last)


def _put(m, path, v):
    *head, last = path.split('.')
    cur = m
    for k in head:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur[last] = copy.deepcopy(v)


def _val(v, data):
    """表上寫 $名字 的從事件資料拿;沒給(None)回 _MISSING,那一項就不做。"""
    if isinstance(v, str) and v.startswith('$'):
        x = data.get(v[1:])
        return _MISSING if x is None else x
    return v


def _collect(m, cell, event, data):
    """第一段:把要留下的收起來(投遞歷史、上一次的紀錄、對帳不算的那一筆),之後才清。"""
    for key, box in (('history', 'history'), ('tries', 'tries')):
        paths = cell.get(key)
        if not paths:
            continue
        got = {}
        for p in paths:
            v = _pop(m, p)
            if v is not _MISSING:
                got[p] = v
        if key == 'history':
            entry = dict({'event': event}, **({'at': data['at']} if data.get('at') else {}))
            entry.update(got)
            m.setdefault(box, []).append(entry)
        else:
            m[box] = (m.get(box) or []) + [got]
    for box, p in (cell.get('push') or {}).items():
        v = _get(m, p)
        if v is not None:
            m[box] = (m.get(box) or []) + [copy.deepcopy(v)]


def _apply(m, cell, event, data):
    """第二段:清 → 設 → 沒有才設 → 併 → 留著的放回 → 表單鎖 → 結果。"""
    for p in cell.get('clear') or []:
        _pop(m, p)
    keep = {p: _get(m, p) for p in cell.get('keep') or []}
    for p, v in (cell.get('set') or {}).items():
        v = _val(v, data)
        if v is not _MISSING:
            _put(m, p, v)
    for p, v in (cell.get('setdefault') or {}).items():
        v = _val(v, data)
        if v is not _MISSING and _get(m, p) is None:
            _put(m, p, v)
    for p, v in (cell.get('merge') or {}).items():
        v = _val(v, data)
        if v is not _MISSING:
            cur = _get(m, p)
            _put(m, p, dict(cur if isinstance(cur, dict) else {}, **v))
    for p, v in keep.items():
        if v is not None:
            _put(m, p, v)
    f = m.get('form') if isinstance(m.get('form'), dict) else None
    if cell.get('lock_form') and f is not None:
        f['lock'] = 1
        for x in f.get('f') or []:
            x.pop('refill', None)          # 送出去了就沒有網頁可重打
    if cell.get('unlock_form') and f is not None:
        f.pop('lock', None)
    if 'outcome' in cell:
        v = _val(cell['outcome'], data)
        if v is not _MISSING:
            set_outcome(m, v, data.get('day') or datetime.date.today().isoformat(), data.get('oc_at'))


def queue_workspace(fb, workspace, reason):
    if not isinstance(workspace, dict) or not workspace.get('id'):
        return
    pending = fb.setdefault('__browser_cleanup__', [])
    if not any(x.get('workspace') == workspace for x in pending):
        pending.append({'workspace': copy.deepcopy(workspace), 'reason': reason})


def fire(fb, url, event, **data):
    """送一個事件給這張卡:照表改,回傳改動前的那一張(深拷貝)。不准就丟 Forbidden,卡不動。"""
    if event not in TABLE['events']:
        raise ValueError(f'表上沒有這個事件:{event}')
    m = fb.get(url)
    if not isinstance(m, dict):
        m = {}
    why = why_not(m, event)
    if why:
        raise Forbidden(why)
    s = state(m)
    cell = TABLE['cells'][s][event]
    prev = copy.deepcopy(fb.get(url))
    to = s
    steps = _steps(cell)
    for step in steps:
        _collect(m, step, event, data)
    for step in steps:
        _apply(m, step, event, data)
        to = step.get('to', to)
    for w in cell.get('when') or []:
        hit = bool(_get(m, w['if'])) if 'if' in w else not _get(m, w['unless'])
        if hit:
            _collect(m, w, event, data)
            _apply(m, w, event, data)
            to = w['to']
            break
    if to == 'todo':
        m.pop('ds', None)
    else:
        m['ds'] = to
    fb[url] = m
    if event in ('fill_start', 'page_lost', 'leave', 'back', 'submit_ok', 'fill_submitted',
                 'actually_sent', 'sent_manual', 'platform_found'):
        previous_apply = (prev or {}).get('apply')
        workspace = previous_apply.get('workspace') if isinstance(previous_apply, dict) else None
        if workspace:
            queue_workspace(fb, workspace, event)
            if isinstance(m.get('apply'), dict):
                m['apply'].pop('workspace', None)
    return prev


def try_fire(fb, url, event, **data):
    """背景流程用:不准就不動(回 False)。晚到的結果(卡已經被他標成在外部送出、平台對帳找到了,
    agent 的填表或送出結果才回來)收進投遞歷史,狀態不變。"""
    try:
        fire(fb, url, event, **data)
        return True
    except Forbidden:
        m = fb.get(url)
        if event in LATE and isinstance(m, dict) and state(m) == 'sent':
            m.setdefault('history', []).append(dict({'event': event, 'late': 1}, **copy.deepcopy(data)))
        return False


LATE = frozenset(TABLE['late'])
OC_RANK = {'': 0, 'iv': 1, 'offer': 2}
OC_END = {'rej', 'ghost', 'wd'}


def set_outcome(f, s, day, at=None):
    """每個結果第一次出現的日期留在 oc_at:往前走(面試→offer)留著走過的日期;往回改(記錯了)把後面的日期收掉;
    結案保留之前走到哪,換另一種結案則取代。at:原封放回的日期(「不對,復原」放回改之前的)。"""
    if at is None:
        at = dict(f.get('oc_at') or {})
        for k in list(at):
            if (k in OC_END and k != s) if s in OC_END else (k in OC_END or OC_RANK.get(k, 0) > OC_RANK.get(s, 0)):
                del at[k]
        if s and s not in at:
            at[s] = day
    if s:
        f['oc'] = s
    else:
        f.pop('oc', None)
    if at:
        f['oc_at'] = copy.deepcopy(at)
    else:
        f.pop('oc_at', None)
def app_of(m):
    """這張卡在流程哪一階(app:prep、ready、ship、sent),移除了的是 'rm':移除保留 app 好放回原處,但移除優先。
    後台挑「這一階的卡」一律照它,不各自記得再排除移除的。"""
    m = m if isinstance(m, dict) else {}
    return 'rm' if m.get('rm') else m.get('app')


def page_up(m):
    """Whether a task-managed page is still recorded as open."""
    return state(m) in HELD_STATES and bool(((m or {}).get('apply') or {}).get('tab_id'))


def page_unknown(m):
    """填過、這一輪沒記到是哪個分頁(agent 沒寫或核對不到,F-20):不是頁不在了,是程式不知道。"""
    apply = (m or {}).get('apply') or {}
    return state(m) in HELD_STATES and bool(apply) and not apply.get('tab_id')


def settle(fb: dict, busy: str | None) -> int:
    """收尾 process 不在執行中的網站任務，只依狀態表記錄中斷。"""
    count = 0
    for card_id, card_data in list(fb.items()):
        now = state(card_data) if isinstance(card_data, dict) and not card_id.startswith('__') else None
        if now not in WORKING or busy == '*' or busy == card_id:
            continue
        apply = card_data.get('apply') or {}
        if now == 'running':
            stage = apply.get('stage') if apply.get('stage') in ('fill', 'fix') else 'fill'
            issue = '這一輪' + ('修改' if stage == 'fix' else '填表') + '沒跑完(程序中斷)。'
            count += try_fire(fb, card_id, 'fill_bad', apply=dict(apply, issues=[issue]))
        else:
            count += try_fire(fb, card_id, 'submit_unsure',
                                 evidence={'at': datetime.datetime.now().isoformat(timespec='microseconds'), 'problems': ['送出途中中斷，結果不明'], 'clicked': None})
    return count
