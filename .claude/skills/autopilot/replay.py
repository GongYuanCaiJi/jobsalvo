#!/usr/bin/env python3
"""照 AutoPilot.tla 的路徑呼叫真的 Pilot(tools/autopilot.py),看程式是不是真的會那樣走。
用法:uv run python .claude/skills/autopilot/replay.py(check.py 會自己跑)

每一條印「名稱: 結果」。重演(R)是模型找到的違規:REPRODUCED = 真程式也會;守門(G)是規則該擋住的路:OK = 擋住了。
EXPECTED 是同步點上該看到的結果;不一樣就 exit 1。修好一個違規,把它的 EXPECTED 改成 NOT REPRODUCED。
外面只換掉三樣:伺服器(start_run、run_status、build_state 是 Pilot 本來就收的參數)、reconcile 子行程
(直接用 bd.set_data 寫驗收結果,跟 board_status --write 寫的同一格)、自動流程設定(flow)。"""
import contextlib, json, os, sys, tempfile, time
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import autopilot as ap  # noqa: E402
import board_doc as bd  # noqa: E402
import delivery_state as ds  # noqa: E402
import demo  # noqa: E402
import form_record as fr  # noqa: E402

EXPECTED = {
    'R1 拿舊驗收推進': 'NOT REPRODUCED',  # FreshVerdict,#741
    'R2 卡住的卡頁不見後被自動重填': 'NOT REPRODUCED',   # StopsAfterFailure,#742
    'R3 派出後伺服器重開又派一次': 'NOT REPRODUCED', # OncePerMark(要重開),#743
    'R4 寫記號後、派工前重開': 'NOT REPRODUCED',       # NoOrphanMark,#743
    'R5 pending 後 page_lost 再重開': 'NOT REPRODUCED', # #743；狀態改變不算派工證據
    'G1 驗收跑完前不推進': 'OK',
    'G2 推過退回的不再推': 'OK',
    'G3 同一次填表不派第二次': 'OK',
}
FLOW = {'auto_prep': False, 'auto_advance': True, 'auto_fill': True, 'replies_at': ''}
OK_STATUS = {'schema_version': 2, 'checked_links': True, 'issues': []}
IDLE = {'built': 0.0, 'tried': 0.0, 'running': False}


@contextlib.contextmanager
def board():
    """一份真的看板檔,只留第一張卡給自動流程管(其他算開啟當下就在的舊卡)。回 (路徑, 那張的 id)。"""
    with tempfile.TemporaryDirectory(prefix='autopilot-replay-') as d:
        path = demo.build(os.path.join(d, 'board.html'))
        jobs = bd.parse(bd.read_doc(path))['data']['jobs']
        url = jobs[0]['id']
        others = [j['id'] for j in jobs[1:]]
        bd.set_fb(lambda fb: fb.update({'__auto__': {'since': '2026-01-01T00:00:00', 'skip': others,
                                                     'tried': [], 'seen': {}, 'flow': FLOW}}), live=path)
        with mock.patch.object(ap, 'flow', return_value=FLOW):
            yield path, url


def doc(path):
    p = bd.parse(bd.read_doc(path))
    return p['data'], json.loads(p['fb'])


def card(path, url):
    return doc(path)[1].get(url) or {}


def put(path, url, **m):
    bd.set_fb(lambda fb: fb.__setitem__(url, m), live=path)


def status(path, st):
    bd.set_data(lambda d, fb: d.__setitem__('status', st), live=path)


def blocking(url):
    return dict(OK_STATUS, issues=[{'jid': url, 't': 'x', 'stage': 'ready', 'kind': 'closed', 'msg': '職缺已下架'}])


def pilot(path, start_run=None, run_status=None, build_state=None):
    return ap.Pilot(path, start_run or (lambda kind, args: (200, {})), run_status or (lambda kind: {}),
                    build_state or (lambda: IDLE), lambda: True, build=lambda: None)


def r1_stale_verdict():
    """自動流程讀看板時驗收這張過;build_state 階段新一輪驗收寫成不過並跑完;
    最後的原子看板寫入必須重讀最新驗收,卡留在待你決定。"""
    with board() as (path, url):
        put(path, url, app='ready')
        status(path, OK_STATUS)
        seen = time.time() - 10
        bd.set_fb(lambda fb: fb['__auto__']['seen'].__setitem__(url, seen), live=path)

        def build_state():          # 新的一輪(看到這張之後才開始)剛跑完:board_status 先寫結果,再記 built
            status(path, blocking(url))
            return {'built': seen + 5, 'tried': seen + 5, 'running': False}
        pilot(path, build_state=build_state).step()
        data, fb = doc(path)
        problem = fr.ship_blocked(fb, {'id': url}, data.get('status'))
        return 'REPRODUCED' if fb[url].get('app') == 'ship' and problem else 'NOT REPRODUCED'


def r2_stuck_refilled():
    """自動填了一次、卡住(stuck,頁還開著);ego 裡那個工作區不在了,sweep_gone 送 page_lost → gone;自動流程又派填表。"""
    with board() as (path, url):
        put(path, url, app='ship', ds='stuck',
            apply={'stage': 'fill', 'at': '2026-01-05T10:05:00', 'tab_id': 't1', 'issues': ['卡在必填欄']})
        bd.set_fb(lambda fb: fb['__auto__']['tried'].append('fill:' + url + ':new'), live=path)
        bd.set_fb(lambda fb: ds.try_fire(fb, url, 'page_lost', issues=['頁不見了']), live=path)
        calls = []
        pilot(path, start_run=lambda kind, args: (calls.append((kind, args)), (200, {}))[1]).step()
        return 'REPRODUCED' if ('apply', {'stage': 'fill', 'url': url}) in calls else 'NOT REPRODUCED'


class Crash(BaseException):
    """伺服器在 start_run 回來之後、set_fb 記下試過之前停掉(_safe_step 只接 Exception,接不到這個)。"""


def r3_restart_redispatch():
    with board() as (path, url):
        put(path, url, app='ship')
        calls = []
        progress = {}

        def crashing(kind, args):
            calls.append((kind, args))
            progress.update(phase='start', stage='fill', url=url, t0=time.time())
            raise Crash
        with contextlib.suppress(Crash):
            pilot(path, start_run=crashing).step()
        # 重開:新的 Pilot;那一輪還沒寫 fill_start 就結束了(run_status 不在跑),卡還是 todo
        p = ap.Pilot(path, lambda kind, args: (calls.append((kind, args)), (200, {}))[1],
                     lambda k: progress if k == 'apply' else {}, lambda: IDLE, lambda: True, build=lambda: None)
        p.step()
        fills = [c for c in calls if c == ('apply', {'stage': 'fill', 'url': url})]
        return 'NOT REPRODUCED' if len(fills) == 1 else 'REPRODUCED' if len(fills) > 1 else 'BROKEN'


def r4_restart_before_start_run():
    """記號已寫入 __auto__.pending,start_run 尚未派出;重開先撤回孤立記號,再派一次。"""
    with board() as (path, url):
        put(path, url, app='ship')
        launched = []

        def crash_before_launch(kind, args):          # 還沒 Popen 就停掉
            raise Crash
        with contextlib.suppress(Crash):
            pilot(path, start_run=crash_before_launch).step()
        # 新 Pilot 的 run_status 沒有這張卡的啟動證據,因此清掉 pending/tried 後正常派一次。
        p = ap.Pilot(path, lambda kind, args: (launched.append((kind, args)), (200, {}))[1],
                     lambda k: {}, lambda: IDLE, lambda: True, build=lambda: None)
        p.step()
        fills = [c for c in launched if c == ('apply', {'stage': 'fill', 'url': url})]
        return 'NOT REPRODUCED' if len(fills) == 1 else 'REPRODUCED'      # 0 次 = 永遠不填;2 次 = 重派


def r5_page_lost_after_pending():
    """pending 已持久化、start_run 前重開;停機期間 page_lost 讓 stale 變 gone,新 Pilot 仍須派一次。"""
    with board() as (path, url):
        at = '2026-01-05T10:05:00'

        def stale(fb):
            fb[url] = {'app': 'ship', 'ds': 'stale',
                       'apply': {'stage': 'fill', 'at': at, 'tab_id': 't1', 'issues': ['舊檔']}}
        bd.set_fb(stale, live=path)

        def crash_before_start(kind, args):
            raise Crash
        with contextlib.suppress(Crash):
            pilot(path, start_run=crash_before_start).step()

        mark = 'fill:' + url + ':' + at
        if mark not in doc(path)[1]['__auto__'].get('pending', {}):
            return 'BROKEN'
        bd.set_fb(lambda fb: ds.try_fire(fb, url, 'page_lost', issues=[ds.GONE]), live=path)
        if card(path, url).get('apply', {}).get('at') != at or ds.state(card(path, url)) != 'gone':
            return 'BROKEN'

        launched = []
        p = ap.Pilot(path, lambda kind, args: (launched.append((kind, args)), (200, {}))[1],
                     lambda kind: {}, lambda: IDLE, lambda: True, build=lambda: None)
        p.step()
        fills = [c for c in launched if c == ('apply', {'stage': 'fill', 'url': url})]
        return 'NOT REPRODUCED' if len(fills) == 1 else 'REPRODUCED' if not fills else 'BROKEN'


def g1_waits_for_build():
    with board() as (path, url):
        put(path, url, app='ready')
        status(path, OK_STATUS)
        gen = dict(IDLE)
        p = pilot(path, build_state=lambda: gen)
        old = time.time() - 60                     # 看到這張之前就跑完的那一輪
        gen.update(built=old, tried=old)
        p.step()                                   # 第一次看到:記 seen
        p.step()                                   # 看到之後還沒有新的一輪跑完:舊的那一輪不算
        early = card(path, url).get('app')
        gen.update(built=time.time() + 1, tried=time.time() + 1)
        p.step()
        return 'OK' if early == 'ready' and card(path, url).get('app') == 'ship' else 'BROKEN'


def g2_no_readvance():
    with board() as (path, url):
        put(path, url, app='ready')
        status(path, OK_STATUS)
        gen = dict(IDLE)
        p = pilot(path, build_state=lambda: gen)
        p.step()
        gen.update(built=time.time() + 1, tried=time.time() + 1)
        p.step()
        put(path, url, app='ready')                # 他退回待你決定
        p.step()
        return 'OK' if card(path, url).get('app') == 'ready' else 'BROKEN'


def g3_fill_once():
    with board() as (path, url):
        put(path, url, app='ship')
        calls = []
        p = pilot(path, start_run=lambda kind, args: (calls.append((kind, args)), (200, {}))[1])
        p.step()
        p.step()                                   # 那一輪還沒動卡就結束了:卡還是 todo
        fills = [c for c in calls if c == ('apply', {'stage': 'fill', 'url': url})]
        return 'OK' if len(fills) == 1 else 'BROKEN'


CASES = {'R1 拿舊驗收推進': r1_stale_verdict, 'R2 卡住的卡頁不見後被自動重填': r2_stuck_refilled,
         'R3 派出後伺服器重開又派一次': r3_restart_redispatch,
         'R4 寫記號後、派工前重開': r4_restart_before_start_run,
         'R5 pending 後 page_lost 再重開': r5_page_lost_after_pending,
         'G1 驗收跑完前不推進': g1_waits_for_build,
         'G2 推過退回的不再推': g2_no_readvance, 'G3 同一次填表不派第二次': g3_fill_once}


def main():
    off = 0
    for name, fn in CASES.items():
        got = fn()
        mark = '' if got == EXPECTED[name] else f'  ← 同步點上是 {EXPECTED[name]}'
        off += bool(mark)
        print(f'{name}: {got}{mark}')
    sys.exit(1 if off else 0)


if __name__ == '__main__':
    main()
