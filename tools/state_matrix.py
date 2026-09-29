#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
state_matrix —— 把「可以投了」一張卡的狀態組合列出來,每一種算出後端怎麼判斷(#293)。

以前每抓到一個 bug 就補一個只重現那個情況的測試,沒人想到的組合永遠抓不到(頁面不在還給 👀、
送出過還說填好了…)。這裡把旗子的組合照固定亂數種子抽出來,看板檢查拿去畫,逐一驗不變量:
畫面講的跟真實一致、按得下去的後端一定放行、說會自動做的自動流程真的會做。

真實(不看任何一支畫面規則,直接從旗子定義):
  page_exists 填好的那一頁還在 agent 的 Chrome 等他:填過或改過、記到分頁、沒不見、沒送出過、沒鎖
"""
import copy, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

URL = 'https://matrix.example/job/1'
# 指定一份兩邊設定裡都沒有的履歷:看板和 Python 都算不出會寄哪幾份,客製紀錄一律每一筆都算(兩邊在同樣的條件下判斷)
JOB = {'id': URL, 'target': '**Matrix Role · Matrix Co**', 'resume': {'recommend': 'matrix-none', 'lang': 'zh'}}
ANS = [{'k': 'k1', 'q': 'Why?', 'v': 'Because.', 'zh': '因為。', 'at': '2026-01-01'}]
CLEAN = {'schema_version': 2, 'at': '2026-01-01 00:00', 'checked_links': True, 'issues': []}
BLOCKING = dict(CLEAN, issues=[{'jid': URL, 't': 'x', 'stage': 'ship', 'kind': 'closed', 'msg': '職缺已下架'}])

DIMS = {
    'stage': [None, 'fill', 'fix'],
    'ok': [True, False],
    'tab': ['', '7'],
    'session': ['', 's1'],
    'gone': [False, True, 'tab_kept'],     # tab_kept:舊資料標了不見、分頁編號沒清
    'stale': [False, True],
    'sent': [False, True],
    'submit_fail': [None, 'pending', 'cleared'],
    'approve': [False, True],
    'lock': [False, True],
    'refill': [False, True],
    'form': [True, False],
    'busy': [None, 'fill', 'submit'],
    'status': ['clean', 'blocking'],
    'custom': [False, True],
    'auto': [False, True],
}


def combos(n=2500, seed=293):
    """照固定種子抽 n 種組合(每次一樣,失敗了重跑得出同一組);每個維度的每個值都一定出現。"""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        out.append({k: (v[i % len(v)] if i < 3 * len(DIMS) else rnd.choice(v)) for k, v in DIMS.items()})
    return out


def state_of(c):
    """組合 → 看板資料(fb)、驗收結果、自動流程設定。"""
    m = {'app': 'ship'}
    if c['form']:
        f = {'plat': 'x', 'f': [dict({'q': 'Why?', 'src': 'bank', 'k': 'k1'}, **({'refill': 1} if c['refill'] else {}))]}
        if c['lock']:
            f['lock'] = 1
        m['form'] = f
    if c['stage']:
        a = {'stage': c['stage'], 'ok': c['ok'], 'issues': [] if c['ok'] else ['測試用的卡住原因'],
             'at': '2026-01-01T00:00:00', 'delivery': {'method': 'direct_upload'}}
        if c['tab']:
            a['tab_id'] = c['tab']
        if c['session']:
            a['session'] = c['session']
        if c['gone']:
            a.update(gone=True, ok=False, issues=['填好的那一頁不見了(agent 的 Chrome 關掉或重開過),要重填'])
            if c['gone'] is True:
                a['tab_id'] = ''
        if c['stale']:
            a['stale'] = '履歷換過了'
        if c['sent']:
            a['sent'] = {'at': '2026-01-01T00:05:00', 'text': '已收到申請'}
        if c['submit_fail'] == 'pending':
            a['submit_fail'] = {'at': '2026-01-01T00:06:00', 'pending': True, 'problems': ['送出途中停掉了'], 'clicked': None}
        elif c['submit_fail'] == 'cleared':
            a['submit_fail'] = {'at': '2026-01-01T00:06:00', 'problems': ['x'], 'cleared': '2026-01-01'}
        m['apply'] = a
    if c['custom']:
        m['custom_docs'] = {'resume:matrix:zh': {'status': 'review', 'name': '客製履歷'}}
    fb = {URL: m, '__ans__': copy.deepcopy(ANS),
          '__auto__': {'since': '2025-12-31T00:00:00', 'skip': [] if c['auto'] else [URL], 'tried': [], 'seen': {}}}
    if c['approve'] and c['form']:
        import form_record
        m['approve'] = {'at': '2026-01-01T00:07:00', 'snap': form_record.snapshot(fb, URL)}
    return fb, (CLEAN if c['status'] == 'clean' else BLOCKING)


def truth(c):
    """從旗子直接定義的真實,不經過任何一支畫面或後端規則。"""
    return {'page_exists': bool(c['stage'] in ('fill', 'fix') and c['tab'] and not c['gone'] and not c['sent']
                                and not (c['form'] and c['lock']))}


def backend(c):
    """後端怎麼判斷這一種:按了確認送出放不放行、現在直接送出放不放行、讓 agent 填這張收不收、自動流程會不會接。"""
    import apply_run, autopilot, form_record
    fb, status = state_of(c)
    after = copy.deepcopy(fb)
    if (after[URL].get('form')):
        after[URL]['approve'] = {'snap': form_record.snapshot(after, URL)}
    busy = bool(c['busy'])
    pl = autopilot.plan({'jobs': [JOB], 'status': status}, copy.deepcopy(fb), verified_gen=0, build_running=False,
                        running={'apply': busy}, real=False,
                        cfg={'auto_prep': False, 'auto_advance': False, 'auto_fill': True, 'fill_max': 0, 'replies_at': ''})
    return {'problem': form_record.approval_problem(fb, URL, status),
            'page_up': form_record.page_up(fb[URL]),
            'gate': autopilot.ship_blocked(fb, JOB, status),
            'approve_ok': form_record.approval_problem(after, URL, status) is None,
            'submit_ok': form_record.approval_problem(fb, URL, status) is None,
            'fill_ok': URL in apply_run.eligible({URL: JOB}, fb, 'fill', url=URL, status=status),
            'auto_pick': URL in (pl.get('fill'), pl.get('fix')) or bool(pl.get('rf', {}).get(URL))}


def cases(n=2500):
    out = []
    for c in combos(n):
        fb, status = state_of(c)
        out.append({'c': c, 'fb': fb, 'status': status, 'truth': truth(c), 'backend': backend(c)})
    return out


if __name__ == '__main__':
    import collections, json
    cs = cases(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
    print(len(cs), 'cases;', json.dumps(collections.Counter(json.dumps(x['backend'], sort_keys=True) for x in cs).most_common(5)))
