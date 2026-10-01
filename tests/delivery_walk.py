"""連續很多步的隨機走法(#302 補充驗收):照固定亂數種子產生一串一串的動作,每一步都經過正式的入口
(看板按鈕的規則、apply_run 的關卡、自動流程、平台對帳、Chrome 關過),記下每一步照狀態表送了哪些事件、改了哪些欄位。

後台測試(tests/test_delivery_sequences.py)每一步驗「永遠要成立的事」。
種子、張數、步數不准亂改:失敗訊息印的是種子和第幾串,重跑得出同一串。"""
import copy
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))

import apply_run
import autopilot
import delivery_state as ds
import form_record as fr

CAP = 2
STATUS = {'schema_version': 2, 'at': '2026-01-01 00:00', 'checked_links': True, 'issues': []}
JOBS = [{'id': 'https://walk.example/a', 'target': '**Role A · Acme**'},
        {'id': 'https://walk.example/b', 'target': '**Role B · Acme**'},
        {'id': 'https://walk.example/c', 'target': '**Role C · Beta**'}]
URLS = [j['id'] for j in JOBS]
BY = {j['id']: j for j in JOBS}
FORM = {'plat': 'x', 'f': [{'q': 'Why?', 'src': 'bank', 'k': 'k1'}]}


def start():
    fb = {u: {'app': 'ship', 'form': copy.deepcopy(FORM)} for u in URLS}
    fb['__ans__'] = [{'k': 'k1', 'q': 'Why?', 'v': 'Because 0.', 'zh': '因為 0。', 'at': '2026-01-01'}]
    fb['__auto__'] = {'since': '2025-12-31T00:00:00', 'skip': [], 'tried': [], 'seen': {}}
    return fb


class Walk:
    """一串動作的世界:三張卡(兩張同一家公司),停著的頁上限 CAP。ops 是每一步的低階紀錄(哪一張、送了什麼)。"""

    def __init__(self, seed):
        self.rnd = random.Random(seed)
        self.fb = start()
        self.ops = []            # [{'op': 'fire'|'set'|'del'|'undo'|'bank', ...}]
        self.log = []            # 人看的:第幾步做了什麼
        self.n = 0
        self.last = None         # 最近一次看板上的動作(復原用):(url, 改之前那一張, 改完的狀態部分, 復原不了時送的事件)
        self.was_sent = set()

    # ---- 低階:照狀態表送事件、改一般欄位,同時記進 ops ----
    def fire(self, u, event, **data):
        ds.fire(self.fb, u, event, **data)
        self.ops.append({'op': 'fire', 'u': u, 'ev': event, 'data': copy.deepcopy(data)})

    def put(self, u, key, v):
        self.fb.setdefault(u, {})[key] = v
        self.ops.append({'op': 'set', 'u': u, 'k': key, 'v': copy.deepcopy(v)})

    def drop(self, u, key):
        self.fb.get(u, {}).pop(key, None)
        self.ops.append({'op': 'del', 'u': u, 'k': key})

    def at(self):
        self.n += 1
        return f'2026-02-01T00:{self.n // 60:02d}:{self.n % 60:02d}'

    def user(self, u, before):
        """看板上的一個動作做完了:記下來給「復原」用。"""
        self.last = (u, before, ds.part(self.fb[u]))

    # ---- 系統那一邊(apply_run、自動流程、Chrome、平台對帳)----
    def sys_fill(self, u):
        if u in apply_run.eligible(BY, self.fb, 'fill', url=u, status=STATUS):
            self.fire(u, 'fill_start', apply={'stage': 'fill', 'at': self.at(), 'issues': ['這一輪還沒跑完']})

    def sys_fix(self, u):
        if u in apply_run.eligible(BY, self.fb, 'fix', url=u, status=STATUS):
            self.fire(u, 'fix_start', apply={'stage': 'fix', 'at': self.at(), 'issues': ['這一輪還沒跑完']})

    def sys_done(self, u, how):
        if ds.state(self.fb[u]) != 'running':
            return
        a = dict(self.fb[u].get('apply') or {}, at=self.at(), session='s' + str(self.n),
                 delivery={'method': 'direct_upload'})
        if how != 'nopage' and not self.fb[u].get('form'):
            self.put(u, 'form', copy.deepcopy(FORM))        # 真的填表一定由 agent 記下表單
        if how == 'ok':
            a.update(issues=[], tab_id=str(self.n))
            self.fire(u, 'fill_ok', apply=a)
            fr.apply_clear_refill(self.fb, u)
            self.ops.append({'op': 'unrefill', 'u': u})
        elif how == 'bad':
            a.update(issues=['必填欄位沒填'], tab_id=str(self.n))
            self.fire(u, 'fill_bad', apply=a)
        else:
            a.update(issues=['連不上'], tab_id='')
            self.fire(u, 'fill_nopage', apply=a)

    def sys_submit(self, u):
        before = copy.deepcopy(self.fb[u])
        if fr.start_submit(self.fb, u, STATUS) is None:           # 正式的關卡(apply_run 派 agent 送出前走的那一支)
            self.ops.append({'op': 'fire', 'u': u, 'ev': 'submit_start', 'data': {}})
            return before
        return None

    def sys_submit_done(self, u, how):
        if ds.state(self.fb[u]) != 'sending':
            return
        if how == 'ok':
            self.fire(u, 'submit_ok', by='agent', sent_at='2026-02-02', evidence={'at': self.at(), 'text': 'received'},
                      ev='送出頁是目前唯一證據')
        elif how == 'unsure':
            self.fire(u, 'submit_unsure', evidence={'at': self.at(), 'problems': ['沒看到成功頁面'], 'clicked': True})
        else:
            self.fire(u, 'submit_not_started')

    def sys_page_lost(self, u):
        if ds.page_up(self.fb[u]):
            self.fire(u, 'page_lost', issues=[ds.GONE])

    def sys_platform(self, u):
        rec = 'p:' + u + ':2026-01-30'
        if rec not in (self.fb[u].get('sync_no') or []):      # sync_sent.plan:他退回過的那一筆不再拉回
            self.fire(u, 'platform_found', by='platform', at=self.at(), sent_at='2026-01-30', rec=rec)

    def sys_settle(self):
        for u in URLS:
            s = ds.state(self.fb[u])
            if s == 'running':
                a = dict(self.fb[u].get('apply') or {}, at=self.at(), issues=['這一輪沒跑完'])
                self.fire(u, 'fill_bad' if a.get('tab_id') else 'fill_nopage', apply=a)
            elif s == 'sending':
                self.fire(u, 'submit_unsure', evidence={'at': self.at(), 'problems': ['送出途中停掉了'], 'clicked': None})

    def sys_translate(self):
        """後台照他改的中文重翻英文(form_record.apply_translate):英文變了,頁還在的表單標重打;不送事件。"""
        v = 'Because %d.' % self.n
        changed = v != self.fb['__ans__'][0].get('v')
        fr.apply_translate(self.fb, 'k1', en=v)
        self.ops += [{'op': 'bank', 'v': v}] + ([{'op': 'refill'}] if changed else [])   # 英文沒變就不標重打

    # ---- 他按的按鈕(看板送的事件,後台照下一步收或擋)----
    def busy(self, u):
        return ds.state(self.fb[u]) in ('running', 'sending', 'unsure')

    def user_confirm(self, u):
        if fr.confirm_problem(self.fb, u, STATUS) is None:
            b = copy.deepcopy(self.fb[u])
            self.fire(u, 'confirm', approve=fr.approval(self.fb, u, self.at()))
            self.user(u, b)

    def user_simple(self, u, event, **data):
        b = copy.deepcopy(self.fb[u])
        if ds.allowed(self.fb[u], event):
            self.fire(u, event, **data)
            self.user(u, b)

    def user_answer(self):
        """他在常用答案改了一條:用到它的「你已確認」確認作廢,頁還在的表單標重打(後台 form_record.changed)。"""
        v = 'Because %d.' % self.n
        if any(ds.state(self.fb[u]) == 'unsure' for u in URLS):
            return                                              # 送出結果不明的卡在用:答案先不給改
        self.fb['__ans__'][0]['v'] = v
        self.ops.append({'op': 'bank', 'v': v})
        for u in URLS:
            m = self.fb[u]
            if ds.state(m) == 'confirmed' and (m.get('approve') or {}).get('snap') != fr.snapshot(self.fb, u):
                self.fire(u, 'answers_changed')
        fr.mark_refill(self.fb, 'k1')
        self.ops.append({'op': 'refill'})

    def user_files(self, u):
        if not self.busy(u):
            b = copy.deepcopy(self.fb[u])
            self.put(u, 'lang', 'en' if self.fb[u].get('lang') != 'en' else 'zh')
            if ds.allowed(self.fb[u], 'files_changed'):
                self.fire(u, 'files_changed', why='語言換了')
            self.user(u, b)

    def user_remove(self, u):
        if self.fb[u].get('rm'):
            b = copy.deepcopy(self.fb[u])
            self.drop(u, 'rm')
            self.user(u, b)
        elif not self.busy(u) and ds.allowed(self.fb[u], 'leave'):
            b = copy.deepcopy(self.fb[u])
            self.fire(u, 'leave')
            self.put(u, 'rm', 1)
            self.user(u, b)

    def user_block(self):
        if isinstance(self.fb.get('__block__'), list) and self.fb['__block__']:
            self.fb['__block__'] = []
            self.ops.append({'op': 'block', 'v': []})
            return
        if any(self.busy(u) for u in URLS[:2]):
            return                                              # 這家有卡 agent 正在做或結果不明:先擋
        for u in URLS[:2]:                                     # 封鎖了:流程裡的卡一起退出,確認作廢
            if self.fb[u].get('app') == 'ship':
                self.fire(u, 'leave')
            if self.fb[u].get('app') in ('prep', 'ready', 'ship'):
                self.drop(u, 'app')
        self.fb['__block__'] = ['Acme']
        self.ops.append({'op': 'block', 'v': ['Acme']})

    def user_push(self, u):
        """他把一張不在流程裡的卡推回「可以投了」(看板的階段按鈕)。"""
        if self.fb[u].get('app') not in ('ship', 'sent') and not self.fb[u].get('rm'):
            b = copy.deepcopy(self.fb[u])
            self.put(u, 'app', 'ship')
            self.user(u, b)

    def user_manual_sent(self, u):
        if not self.busy(u):
            self.user_simple(u, 'sent_manual', by='manual', at=self.at(), sent_at='2026-02-03')

    def user_undo(self):
        if not self.last:
            return
        u, before, after = self.last
        self.last = None
        try:
            ds.undo(self.fb, u, ds.part(before), after)
        except ds.Forbidden:
            return
        self.fb[u] = copy.deepcopy(before)
        self.ops.append({'op': 'undo', 'u': u, 'prev': copy.deepcopy(before), 'after': after})
        return before

    # ---- 一步 ----
    def step(self):
        # 走到一半的卡多挑幾次(不然大多時間都在還沒填、已送出打轉)
        u = self.rnd.choice([v for v in URLS for _ in range(1 if ds.state(self.fb[v]) in ('todo', 'sent') else 4)])
        acts = [
            ('讓 agent 填', lambda: self.sys_fill(u)), ('要 agent 改', lambda: self.sys_fix(u)),
            ('填好', lambda: self.sys_done(u, 'ok')), ('填了卡住', lambda: self.sys_done(u, 'bad')),
            ('沒開到頁', lambda: self.sys_done(u, 'nopage')),
            ('確認送出', lambda: self.user_confirm(u)), ('取消確認', lambda: self.user_simple(u, 'unconfirm')),
            ('送出', lambda: self.sys_submit(u)), ('送出成功', lambda: self.sys_submit_done(u, 'ok')),
            ('送出結果不明', lambda: self.sys_submit_done(u, 'unsure')),
            ('agent 沒開起來', lambda: self.sys_submit_done(u, 'not_started')),
            ('確認沒送出', lambda: self.user_simple(u, 'not_sent', at=self.at(), issues=[ds.GONE])),
            ('其實送出了', lambda: self.user_simple(u, 'actually_sent', by='agent', sent_at='2026-02-03',
                                               evidence=(self.fb[u].get('apply') or {}).get('submit_fail'))),
            ('Chrome 關過', lambda: self.sys_page_lost(u)), ('平台對帳找到', lambda: self.sys_platform(u)),
            ('我已在外部送出', lambda: self.user_manual_sent(u)),
            ('退回', lambda: self.user_simple(u, 'back', at=self.at(), to='ship')),
            ('沒送成', lambda: self.user_simple(u, 'undo_sent', at=self.at())),
            ('再投一次', lambda: self.user_simple(u, 'retry')),
            ('換語言', lambda: self.user_files(u)), ('移除/放回', lambda: self.user_remove(u)),
            ('推回可以投了', lambda: self.user_push(u)),
            ('封鎖/解除封鎖 Acme', self.user_block), ('改答案', self.user_answer), ('後台重翻', self.sys_translate),
            ('伺服器重開', self.sys_settle), ('復原', self.user_undo),
        ]
        # 大多照這張現在的狀態挑「畫面上會有的那幾顆、會發生的那幾件事」(不然隨機亂按很少走到正在送出);
        # 另外一成照全部亂挑,驗每一個入口在不該動的時候都不動
        likely = {
            'todo': ['讓 agent 填', '讓 agent 填', '我已在外部送出', '換語言'],
            'running': ['填好', '填好', '填好', '填了卡住', '沒開到頁', '伺服器重開', '換語言', '改答案'],
            'nopage': ['讓 agent 填'], 'stuck': ['讓 agent 填', '要 agent 改', 'Chrome 關過', '換語言'],
            'stale': ['讓 agent 填', 'Chrome 關過'], 'gone': ['讓 agent 填'],
            'parked': ['確認送出', '確認送出', '確認送出', '要 agent 改', 'Chrome 關過', '換語言', '改答案', '移除/放回'],
            'confirmed': ['送出', '送出', '送出', '取消確認', '改答案', '後台重翻', '換語言', 'Chrome 關過', '移除/放回',
                          '封鎖/解除封鎖 Acme'],
            'sending': ['送出成功', '送出結果不明', '送出結果不明', 'agent 沒開起來', '伺服器重開', '平台對帳找到', '改答案'],
            'unsure': ['確認沒送出', '確認沒送出', '其實送出了', 'Chrome 關過', '平台對帳找到', '換語言', '改答案'],
            'sent': ['沒送成', '退回', '再投一次', '平台對帳找到'],
        }[ds.state(self.fb[u])]
        if self.fb[u].get('rm') or self.fb[u].get('app') not in ('ship', 'sent'):
            likely = ['移除/放回', '推回可以投了', '封鎖/解除封鎖 Acme']
        by = dict(acts)
        if self.rnd.random() < 0.1:
            name, act = self.rnd.choice(acts)
        else:
            name = self.rnd.choice(likely + ['復原'])
            act = by[name]
        before = copy.deepcopy(self.fb)
        n_ops = len(self.ops)
        result = act()
        self.log.append(f'{len(self.log) + 1}. {name}' + ('' if name in ('封鎖/解除封鎖 Acme', '改答案', '後台重翻', '伺服器重開', '復原')
                                                          else ' ' + u.rsplit('/', 1)[-1]))
        return name, u, before, result, self.ops[n_ops:]

    def held(self):
        return [u for u in URLS if ds.held(self.fb, u, BY[u])]

    def auto_pick(self):
        cfg = {'auto_prep': False, 'auto_advance': False, 'auto_fill': True, 'fill_max': CAP, 'replies_at': ''}
        return autopilot.plan({'jobs': JOBS, 'status': STATUS}, copy.deepcopy(self.fb), verified_gen=0,
                              build_running=False, running={}, real=False, cfg=cfg).get('fill')


SEED, WALKS, STEPS = 302, 1500, 30

