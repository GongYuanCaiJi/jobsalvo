"""舊記號組合(投遞狀態上線前,一張「可以投了」的卡靠十幾個記號拼出來):轉換測試的輸入。

以前的 tools/state_matrix.py(#293)照固定亂數種子抽這些組合;改成一張卡只存一個投遞狀態(docs/adr/0004)之後,
它只剩一個用途:舊資料轉成投遞狀態時,畫出來要跟轉換前一樣。轉換前的畫面存在 fixtures/legacy-render.json
(main dc17bf5 的 board.js 畫的),組合要跟當時一模一樣,所以種子、維度、順序都不准改。"""
import copy
import random

URL = 'https://matrix.example/job/1'
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
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        out.append({k: (v[i % len(v)] if i < 3 * len(DIMS) else rnd.choice(v)) for k, v in DIMS.items()})
    return out


def snapshot(fb, url):
    """確認時的答案快照(當時 form_record.snapshot 的算法;這裡照抄,不跟著之後的程式變)。"""
    bank = {e.get('k'): e for e in fb.get('__ans__') or []}
    out = {}
    for x in ((fb.get(url) or {}).get('form') or {}).get('f', []):
        e = bank.get(x.get('k')) if x.get('src') == 'bank' else x
        out[x.get('q') or ''] = (e or {}).get('v') or ''
    return out


def state_of(c):
    """組合 → 舊記號的看板資料(fb)、投遞前驗收結果。"""
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
        m['approve'] = {'at': '2026-01-01T00:07:00', 'snap': snapshot(fb, URL)}
    return fb, (CLEAN if c['status'] == 'clean' else BLOCKING)
