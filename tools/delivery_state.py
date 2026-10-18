#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
delivery_state —— 一張卡的投遞狀態(docs/adr/0004、GLOSSARY「投遞狀態」)。

一張卡從「可以投了」到「已投出」同一時間只在一種狀態,存在卡上的 ds(還沒填不存)。狀態之間怎麼走由
delivery_state.json 那張「狀態 × 事件」表定義:所有會改動卡片投遞的地方都送事件(fire),不直接改記號;
沒列的格子就是不准(Forbidden)。只有後台套表:看板只送事件,照後台存好的卡和下一步畫(docs/adr/0005)。

「停著的頁」「下一步能做什麼」「自動流程會不會接手」「關 Chrome 時保護哪幾頁」都先看狀態;
能不能確認送出另外看一張固定的檢查清單(form_record.approval_problem),那些不是狀態。

用法:
  import delivery_state as ds
  prev = ds.fire(fb, url, 'confirm', approve={...})   # 回傳改動前的那一張
  ds.state(fb[url])                                   # 'parked'…
  ds.held(fb, url, job)                               # 算不算停著的頁
"""
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


def problems(m):
    """一張卡存的資料對不對得回它的投遞狀態(錯的組合不該存得進去):回傳問題清單,空的就是對。"""
    if not isinstance(m, dict):
        return []
    s, a, bad = state(m), (m.get('apply') or {}) if isinstance(m.get('apply'), dict) else {}, []
    if m.get('ds') is not None and m.get('ds') not in TABLE['states']:
        bad.append(f'狀態 {m.get("ds")!r} 不在表上')
    if (m.get('app') == 'sent') != (s == 'sent'):
        bad.append(f'在「{m.get("app")}」欄卻是「{label(s)}」')
    if (m.get('form') or {}).get('lock') and s != 'sent':
        bad.append('表單鎖著卻不是已送出')
    if bool(m.get('sent_by')) != (s == 'sent'):
        bad.append('送出來源跟狀態對不上')
    if 'approve' in m and s not in ('confirmed', 'sending', 'unsure'):
        bad.append(f'「{label(s)}」卻帶著確認')
    if s == 'confirmed' and 'approve' not in m:
        bad.append('你已確認卻沒有確認紀錄')
    if a.get('sent') and s != 'sent':
        bad.append('帶著送出證據卻不是已送出')
    if bool(a.get('submit_fail')) != (s == 'unsure'):
        bad.append('送出結果不明的紀錄跟狀態對不上')
    if a.get('stale') and s not in ('stale', 'running', 'unsure'):
        bad.append(f'「{label(s)}」卻記著換過檔')
    if s == 'stale' and not a.get('stale'):
        bad.append('上傳的是舊檔卻沒記換了什麼')
    return bad


OWNED = tuple(TABLE['owned'])


def lean(v):
    """空字串、空陣列、空物件、None 都算「沒有」(board_server 比對標記也用這一個)。"""
    if v is None or v == '':
        return None
    if isinstance(v, list):
        return v or None
    if isinstance(v, dict):
        o = {k: lean(x) for k, x in v.items()}
        o = {k: x for k, x in o.items() if x is not None}
        return o or None
    return v


def same(a, b):
    """兩個值是不是同一件事(欄位順序無關,「空」的各種寫法都算一樣)。"""
    return json.dumps(lean(a), sort_keys=True, ensure_ascii=False) == json.dumps(lean(b), sort_keys=True, ensure_ascii=False)


# ---- 看板的指令與復原單(#407 R8)----
# 看板只送指令:{u: 哪一份, ev?: 事件, data?, set?: {欄: 值或 None}};後台套在「現在」那一份上,回一張復原單
# {u, back: {欄: 按之前的值}, expect: {欄: 按之後的值}}。復原就是把復原單當指令送回來(restore)。
# 只改送來的那幾欄,兩個分頁各改各的欄不會互相蓋掉;同一欄後到的贏。
KEYED = {'__ans__': 'k', '__inbox__': 'id', '__notes__': 'id'}   # 這幾份是清單:一條一條照這一欄認
STATE_FIELDS = OWNED + ('app', 'form')    # 狀態表管的(加上只有後台寫的表單):復原時一起放回或一起不動
INTERNAL = ('__browser_cleanup__',)       # 後台自己的排程,不進復原單


def fields(k, v):
    """一份標記拆成「欄」:卡片是它的每一欄;KEYED 的清單一條一欄;其他(封鎖名單…)整份算一欄 '*'。"""
    key = KEYED.get(k)
    if key and isinstance(v, list):
        return {str(x[key]): x for x in v if isinstance(x, dict) and x.get(key) is not None}
    if isinstance(v, dict):
        return v
    return {} if v is None else {'*': v}


def put(fb, k, sets):
    """把幾欄的新值(None = 拿掉)套到 fb[k] 現在的樣子上;整份空了就拿掉那一份。"""
    if '*' in sets:
        v = copy.deepcopy(sets['*'])
    else:
        f = dict(fields(k, fb.get(k)))
        for name, x in sets.items():
            if x in (None, '', [], {}):              # 空的各種寫法都是拿掉那一欄
                f.pop(name, None)
            else:
                f[name] = copy.deepcopy(x)
        v = list(f.values()) if k in KEYED else f
    if v in (None, '', [], {}):
        fb.pop(k, None)
    else:
        fb[k] = v


def diff(k, a, b):
    """a 變成 b 動到哪幾欄:{欄: b 那邊的值(沒有是 None)}。"""
    fa, fb_ = fields(k, a), fields(k, b)
    return {n: fb_.get(n) for n in dict.fromkeys([*fa, *fb_]) if fa.get(n) != fb_.get(n)}


def slips(before, after):
    """一道指令前後的整份看板 → 它的復原單(每一份動到的一張)。"""
    out = []
    for k in dict.fromkeys([*before, *after]):
        if k in INTERNAL or before.get(k) == after.get(k):
            continue
        back = diff(k, after.get(k), before.get(k))
        now = fields(k, after.get(k))
        out.append({'u': k, 'back': back, 'expect': {n: copy.deepcopy(now.get(n)) for n in back}})
    return out


def restore(fb, slip):
    """收到一張復原單:每一欄還是按之後的樣子(沒被別處改過)才放回;卡片狀態表管的那幾欄一起放回或一起不動
    (只放回一半會拼出狀態表不准的組合)。回傳沒放回的那幾欄。"""
    k, back, expect = slip['u'], slip.get('back') or {}, slip.get('expect') or {}
    now = fields(k, fb.get(k))
    ok = [n for n in back if now.get(n) == expect.get(n)]
    if not k.startswith('__') and any(n in STATE_FIELDS and n not in ok for n in back):
        ok = [n for n in ok if n not in STATE_FIELDS]
    put(fb, k, {n: back[n] for n in ok})
    return [n for n in back if n not in ok]


def settable(m, sets):
    """看板的 set 能寫的欄:狀態表管的、只有後台寫的(表單)不收;進出已送出只能靠事件。"""
    m = m if isinstance(m, dict) else {}
    return {n: v for n, v in sets.items() if n not in STATE_FIELDS
            or n == 'app' and 'sent' not in (m.get('app'), v)}


# ---- 投遞後的結果(等回音、面試、offer、沒錄取、沒下文、我不去了):狀態表的「記結果」事件、查應徵進度都用這一支 ----
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


def company_blocked(fb, job):
    """這張卡的公司被封鎖了沒。"""
    return company_block(fb, job) is not None


def company_block(fb, job):
    """這張卡落在封鎖名單的哪一條(同一家:大小寫、特殊字元、法律字尾不同也算);沒被封鎖回 None。
    看板照它畫、解除封鎖照它拿掉,不自己比公司名(#343)。"""
    blocked = fb.get('__block__')
    if not isinstance(blocked, list) or not job:
        return None
    import card, config as cf
    board = cf.C.get('board') or {}
    name = card.company(job, board.get('company_alias'), board.get('title_words'))
    return next((x for x in blocked if card.same_company(x, name)), None)


def app_of(m):
    """這張卡在流程哪一階(app:prep、ready、ship、sent),移除了的是 'rm':移除保留 app 好放回原處,但移除優先。
    後台挑「這一階的卡」一律照它,不各自記得再排除移除的。"""
    m = m if isinstance(m, dict) else {}
    return 'rm' if m.get('rm') else m.get('app')


def place_of(fb, job):
    """這張卡在看板哪一頁(跟 board.js placeOf 同一條):'rm' 已移除;流程四階;'' 公司封鎖了(已投出的照留);其餘照表態。
    後台挑「要處理哪幾張」照它,看板上的張數跟按下去處理的才是同一份。"""
    m = fb.get(job['id']) if isinstance(fb.get(job['id']), dict) else {}
    app = app_of(m)
    if app == 'rm':
        return 'rm'
    if company_blocked(fb, job) and app != 'sent':
        return ''
    return app if app in ('prep', 'ready', 'ship', 'sent') else m.get('s') or 'none'


def held(fb, url, job=None):
    """停著的頁:這張的頁還開在 agent 的 Chrome 等他(停著等你、填了卡住、上傳的是舊檔、你已確認、送出結果不明),
    而且卡還在「可以投了」、沒移除、公司沒被封鎖。下一步、自動流程的上限、關 Chrome 時保護的分頁都看它。"""
    m = fb.get(url)
    if app_of(m) != 'ship' or state(m) not in HELD_STATES:
        return False
    if TABLE['states'][state(m)].get('held_tab') and not (m.get('apply') or {}).get('tab_id'):
        return False                    # 表上 held_tab:頁還開著才算(送出結果不明、頁已經不在了:等他查,但不佔一頁)
    return not company_blocked(fb, job)


def page_up(m):
    """那一頁還在(停著的頁、記到分頁):👀、叫 agent 在原頁改、答案改了要不要標重打都看這個。"""
    return state(m) in HELD_STATES and bool(((m or {}).get('apply') or {}).get('tab_id'))
