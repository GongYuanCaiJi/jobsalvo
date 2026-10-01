#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
delivery_state —— 一張卡的投遞狀態(docs/adr/0004、GLOSSARY「投遞狀態」)。

一張卡從「可以投了」到「已投出」同一時間只在一種狀態,存在卡上的 ds(還沒填不存)。狀態之間怎麼走由
delivery_state.json 那張「狀態 × 事件」表定義:所有會改動卡片投遞的地方都送事件(fire),不直接改記號;
沒列的格子就是不准(Forbidden)。看板 board/board.js 的 dsFire 解譯同一份表(伺服器送頁面時附上),兩邊一樣。

「停著的頁」「下一步能做什麼」「自動流程會不會接手」「關 Chrome 時保護哪幾頁」都先看狀態;
能不能確認送出另外看一張固定的檢查清單(form_record.approval_problem),那些不是狀態。

用法:
  import delivery_state as ds
  prev = ds.fire(fb, url, 'confirm', approve={...})   # 回傳改動前的那一張(復原就整張放回)
  ds.state(fb[url])                                   # 'parked'…
  ds.held(fb, url, job)                               # 算不算停著的頁
"""
import copy, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, 'delivery_state.json'), encoding='utf-8') as _f:
    TABLE = json.load(_f)

HELD_STATES = frozenset(s for s, x in TABLE['states'].items() if x['held'])
AUTO_FILL = frozenset(TABLE['auto_fill'])        # 自動流程會自己(重)填的
GONE = '填好的那一頁不見了(agent 的 Chrome 關掉或重開過),要重填'
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
    cell = TABLE['cells'][state(m)].get(event)
    return cell is not None and _guard_ok(m, cell)


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
    """第一段:把要留下的收起來(投遞歷史、上一次的紀錄、對帳不算的那一筆),之後才清。看板那份一樣。"""
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
    """第二段:清 → 設 → 沒有才設 → 併 → 留著的放回 → 表單鎖。看板那份一樣。"""
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


def fire(fb, url, event, **data):
    """送一個事件給這張卡:照表改,回傳改動前的那一張(深拷貝;復原就整張放回)。不准就丟 Forbidden,卡不動。"""
    if event not in TABLE['events']:
        raise ValueError(f'表上沒有這個事件:{event}')
    m = fb.get(url)
    if not isinstance(m, dict):
        m = {}
    s = state(m)
    cell = TABLE['cells'][s].get(event)
    if cell is None:
        raise Forbidden(f'「{label(s)}」時不能「{TABLE["events"][event]}」')
    if not _guard_ok(m, cell):
        raise Forbidden(f'「{label(s)}」這張不能「{TABLE["events"][event]}」')
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


MIGRATED = '__ds__'      # 看板資料轉成投遞狀態了(只轉一次)


def from_legacy(m):
    """舊記號(apply.ok/gone/stale/sent/submit_fail、approve、form.lock…任意組合)→ 一個投遞狀態,回傳轉好的那一張。
    「在可以投了、卻帶著已送出紀錄」的卡照「沒送成」轉:送出證據收進投遞歷史、回到還沒填。"""
    m = copy.deepcopy(m)
    a = m.get('apply') if isinstance(m.get('apply'), dict) else None
    f = m.get('form') if isinstance(m.get('form'), dict) else None
    if m.get('app') == 'sent':
        m['ds'] = 'sent'
        # agent 送出的有證據;沒證據但記了寄哪一份的是你在外部送出;兩樣都沒有的舊資料分不出是外部還是對帳
        m.setdefault('sent_by', 'agent' if (a or {}).get('sent') else 'manual' if m.get('sent_v') else 'legacy')
        if a:
            for k in ('ok', 'gone', 'submit_fail', 'stale'):
                a.pop(k, None)
            if a.get('sent'):
                a['tab_id'] = ''            # 那一頁是「已收到申請」,不是等他的頁
        if m['sent_by'] == 'agent':
            m.pop('approve', None)
        return m
    if (a or {}).get('sent') or (f or {}).get('lock'):
        entry = {'event': 'undo_sent', 'legacy': 1}
        for k in ('apply', 'approve'):
            if k in m:
                entry[k] = m.pop(k)
        m.setdefault('history', []).append(entry)
        if f:
            f.pop('lock', None)
        m.pop('ds', None)
        return m
    if not a:
        m.pop('approve', None)          # 還沒填過的確認送不出去
        m.pop('ds', None)
        return m
    sf = a.get('submit_fail') if isinstance(a.get('submit_fail'), dict) else None
    ok, gone, tab = a.pop('ok', None), a.pop('gone', None), a.get('tab_id')
    if sf and sf.get('cleared'):
        # 他確認過沒送出:當時的證據收進歷史,確認照舊有效
        m.setdefault('history', []).append({'event': 'not_sent', 'legacy': 1, 'apply.submit_fail': a.pop('submit_fail')})
        sf = None
    if sf:
        sf.pop('pending', None)
        s = 'unsure'
        if gone:
            a['tab_id'] = ''                # 頁面也不見了:仍是送出結果不明,只是頁不在了
    elif a.get('stage') not in ('fill', 'fix'):
        s = 'todo'
    elif gone:
        s = 'gone'
    elif a.get('stale'):
        s = 'stale'
    elif ok and not f:
        s = 'stuck'                         # 填好了卻沒有表單紀錄:看不到填了哪些欄,要再填一次
        a['issues'] = a.get('issues') or [NO_FORM]
    elif ok:
        # 確認過、之後卻移除、標出錯了、退出可以投了:確認作廢(以前留著,放回來又變有效)
        out = m.get('rm') or m.get('app') != 'ship'
        s = 'confirmed' if m.get('approve') and not out else 'parked'
    else:
        s = 'stuck' if tab else 'nopage'
    if s not in ('confirmed', 'unsure'):
        m.pop('approve', None)
    if s != 'stale':
        a.pop('stale', None)
    if s == 'todo':
        m.pop('ds', None)
    else:
        m['ds'] = s
    return m


def migrate(fb):
    """整份看板的卡轉成投遞狀態(伺服器起來時做一次)。回傳有沒有改。"""
    if fb.get(MIGRATED):
        return False
    for k, m in list(fb.items()):
        if k.startswith('__') or not isinstance(m, dict):
            continue
        if m.get('apply') or m.get('approve') or m.get('app') == 'sent' or (m.get('form') or {}).get('lock'):
            fb[k] = from_legacy(m)
    fb[MIGRATED] = 1
    return True


OWNED = tuple(TABLE['owned'])


def lean(v):
    """空字串、空陣列、空物件、None 都算「沒有」(看板 lean 同一套;board_server 比對標記也用這一個)。"""
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


def part(m):
    """一張卡歸狀態表管的那一部分:復原時比對、放回的就是這些。"""
    m = m if isinstance(m, dict) else {}
    out = {k: copy.deepcopy(m[k]) for k in OWNED if k in m}
    out['app'] = m.get('app')
    f = m.get('form') if isinstance(m.get('form'), dict) else {}
    out['lock'] = f.get('lock')
    # 鎖表單時清掉的雇主網頁待重打(哪幾欄,照表單上的順序):復原跟著鎖一起放回(#333)
    out['refill'] = [i for i, x in enumerate(f.get('f') or []) if isinstance(x, dict) and x.get('refill')] or None
    return out


# 看板不寫、只有後台寫的欄位:表單是 agent 填表時記的,看板只在上面標重打(送 {refill: 答案鍵},後台在鎖內標在現在那份上)。
# 以前看板存檔整份送回它手上的表單,agent 剛記的新表單就被換回手機上的舊表單(#308)。
SERVER_ONLY = ('form',)


def plain(m):
    """一張卡看板存檔照舊逐張寫的那一部分(心情、原因…):不含狀態表管的、也不含只有後台寫的。"""
    if not isinstance(m, dict):
        return m
    out = {k: copy.deepcopy(v) for k, v in m.items() if k not in OWNED and k not in SERVER_ONLY}
    if out.get('app') == 'sent':
        out.pop('app')
    return out


# 跟離開流程那一類按鈕(退回、移除、出錯了、👎、放回來)一起改的欄位:卡在哪一階、移除、心情、出錯了之前在哪
FLOW_FIELDS = ('app', 'rm', 's', 's0', 'app0', 'live_ok')


def keep_flow(cur, new):
    """看板那一下的事件被狀態表擋下:送來的這張卡,跟著那一下改的欄位照現在的留著(其他欄位照收)。"""
    if not isinstance(new, dict):
        return new
    cur = cur if isinstance(cur, dict) else {}
    out = copy.deepcopy(new)
    for k in FLOW_FIELDS:
        if k in cur:
            out[k] = copy.deepcopy(cur[k])
        else:
            out.pop(k, None)
    return out


def merge_saved(cur, new):
    """看板存檔送來的一張卡(new)併到現在這一張(cur)上:狀態表管的、只有後台寫的那幾欄照現在的留著,只收其他欄。
    進出已送出(app='sent')只能靠事件。new 是 None(整張清掉)時,那幾欄照樣留著。"""
    cur = cur if isinstance(cur, dict) else {}
    out = plain(new) if isinstance(new, dict) else {}
    for k in OWNED + SERVER_ONLY:
        if k in cur:
            out[k] = copy.deepcopy(cur[k])
    if cur.get('app') == 'sent' or (isinstance(new, dict) and new.get('app') == 'sent'):
        out.pop('app', None)
        if 'app' in cur:
            out['app'] = cur['app']
    return out or None


# 後台套完事件之後自己補記的欄位:卡到已送出時記寄出的是哪一份(ship.record_sent,記過不改)。
# 看板照狀態表算的 after 不會有它;復原時比「有沒有被別處改過」不看它,放回照樣照 prev(prev 沒有就清掉)
NOTED_AFTER = ('sent_v',)


def undo(fb, url, prev, after, fallback=None):
    """看板按了「復原」:這張歸狀態表管的那一部分還是按下去之後的樣子(after),就整份放回按之前(prev);
    已經被別處改過(自動流程、另一個分頁)就照 fallback 送一個事件(例如換檔的復原 = 又換了一次檔),
    沒有就不准。准不准只在這裡判,看板不自己判。"""
    m = fb.get(url) if isinstance(fb.get(url), dict) else {}
    def compared(p):
        return {k: v for k, v in (p or {}).items() if k not in NOTED_AFTER}
    if not same(compared(part(m)), compared(after)):
        if fallback:
            return fire(fb, url, fallback['ev'], **(fallback.get('data') or {}))
        raise Forbidden('這張已經被別處改過了,不能復原')
    before = copy.deepcopy(m)
    # 事件收進 tries 的不歸狀態表管的欄位(再投一次收走整份鎖著的表單):從收進去的那一筆拿回來,鎖才有地方放
    for entry in (m.get('tries') or [])[len((prev or {}).get('tries') or []):]:
        for k, v in (entry if isinstance(entry, dict) else {}).items():
            if k not in OWNED and '.' not in k and k not in m:
                m[k] = copy.deepcopy(v)
    for k in OWNED:
        m.pop(k, None)
    for k in OWNED:
        if (prev or {}).get(k) is not None:
            m[k] = copy.deepcopy(prev[k])
    if (prev or {}).get('app'):
        m['app'] = prev['app']
    else:
        m.pop('app', None)
    if isinstance(m.get('form'), dict):
        if (prev or {}).get('lock'):
            m['form']['lock'] = prev['lock']
        else:
            m['form'].pop('lock', None)
        marks = set((prev or {}).get('refill') or [])
        for i, x in enumerate(m['form'].get('f') or []):
            if isinstance(x, dict):
                if i in marks:
                    x['refill'] = 1
                else:
                    x.pop('refill', None)
    fb[url] = m
    return before


def company_blocked(fb, job):
    """這張卡的公司被封鎖了沒(看板的 blockedCo 同一條)。"""
    blocked = fb.get('__block__')
    if not isinstance(blocked, list) or not job:
        return False
    import card, config as cf
    board = cf.C.get('board') or {}
    name = card.company(job, board.get('company_alias'), board.get('title_words'))
    return any(card.same_company(x, name) for x in blocked)


def held(fb, url, job=None):
    """停著的頁:這張的頁還開在 agent 的 Chrome 等他(停著等你、填了卡住、上傳的是舊檔、你已確認、送出結果不明),
    而且卡還在「可以投了」、沒移除、公司沒被封鎖。看板的 held 同一條;自動流程的上限、關 Chrome 時保護的分頁都看它。"""
    m = fb.get(url)
    if not isinstance(m, dict) or m.get('app') != 'ship' or m.get('rm') or state(m) not in HELD_STATES:
        return False
    if TABLE['states'][state(m)].get('held_tab') and not (m.get('apply') or {}).get('tab_id'):
        return False                    # 表上 held_tab:頁還開著才算(送出結果不明、頁已經不在了:等他查,但不佔一頁)
    return not company_blocked(fb, job)


def page_up(m):
    """那一頁還在(停著的頁、記到分頁):👀、叫 agent 在原頁改、答案改了要不要標重打都看這個。"""
    return state(m) in HELD_STATES and bool(((m or {}).get('apply') or {}).get('tab_id'))
