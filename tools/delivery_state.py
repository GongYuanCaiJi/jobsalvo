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
import copy, json

from delivery_core import *


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


def company_blocked(fb, job):
    """這張卡的公司被封鎖了沒。"""
    return company_block(fb, job) is not None


def company_block(fb, job):
    """這張卡落在封鎖名單的哪一條:卡上的公司(agent 引的那串字,套使用者的公司別名)跟名單一字不差;
    升級前的舊卡照舊(legacy_same_company)。沒被封鎖回 None。看板照它畫、解除封鎖照它拿掉,不自己比公司名(#343)。"""
    blocked = fb.get('__block__')
    if not isinstance(blocked, list) or not job:
        return None
    import card, config as cf
    board = cf.C.get('board') or {}
    name = card.company(job, board.get('company_alias'), board.get('title_words'))
    same = card.same_company if card.cited_company(job) else card.legacy_same_company
    # 封鎖名稱也套使用者的公司別名:他把「Acme」列成哪一家,封鎖「Acme」就擋那一家(J-12)
    return next((x for x in blocked if isinstance(x, str) and same(card.aliased(x, board.get('company_alias')), name)),
                None)


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
    if TABLE['states'][state(m)].get('held_tab') and not page_up(m):
        return False                    # 表上 held_tab:頁還開著才算(送出結果不明、頁已經不在了:等他查,但不佔一頁)
    return not company_blocked(fb, job)
