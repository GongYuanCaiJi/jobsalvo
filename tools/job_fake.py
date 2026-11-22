#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job_fake —— 看板副本上按「跑準備區」「找新職缺」時跑的假流程(沙箱、測試、board_check 都是副本)。

真的那兩支(cut_tailor、find)會派 agent、寫 repo 裡的檔、改現行看板。副本上按一下就跑真的,
等於在測試時動到真的東西、花掉 agent。所以 board_server 只在服務真的看板時才跑真的,
其餘一律跑這支。

它照同一套格式寫進度(jobrun),最後對副本做跟真的收尾一樣的事:
  prep     把副本「正在準備」的卡推到「待你決定」
  research 在副本加兩筆假職缺(🆕 待評估看得到)
  apply    fill:把副本可投遞的卡標成「agent 填好了」;submit:核准有效的卡搬到已投遞(假確認頁)。
           核准規則照真的 form_record.approval_problem,沒核准的一張都不會動。
  add      貼網址加入:每個網址加一張假卡進待評估
  suggest  分類建議:寫一份假的建議(兩個類別、一個標籤)
介面整條路走得完,但不花 agent、不碰任何真的檔案。每一步停多久看環境變數 JOB_FAKE_STEP(秒,預設 1)。
"""
import os, sys, json, time, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd      # noqa: E402
import jobrun               # noqa: E402
import config as cf         # noqa: E402


def sync(board, sp, step):
    """看板副本只演練背景狀態；不接瀏覽器、不改平台履歷登記。"""
    import sync
    path = os.path.join(sp, 'sync_status.json')
    started = time.time()
    slots = [slot for slot in sync.slots() if sync.standing(slot, board).code != 'STANDING_OK']
    time.sleep(step)
    results = [{'platform': slot.platform, 'lang': slot.lang, 'variant': slot.variant,
                'ok': False, 'msg': '副本只演練畫面，未實際同步'} for slot in slots]
    phase = 'nothing' if not slots else 'incomplete'
    jobrun.write(path, {'phase': phase, 'pid': os.getpid(), 't0': started, 'n': len(slots),
                        'done': 0, 'msg': '沒有要同步的' if not slots else '副本未實際同步',
                        'results': results, 'finished_at': time.time()})


def prep(board, sp, step, limit=0, url=''):
    import cut_tailor as ct     # 進度檔的位置跟真的 cut_tailor 同一套
    st = lambda d: ct._status(d, sp=sp)
    with open(board, encoding='utf-8') as f:
        fb = json.loads(bd.parse(f.read())['fb'])
    rows = [k for k, v in fb.items() if isinstance(v, dict) and v.get('app') == 'prep' and (not url or k == url)]
    rows = rows[:limit] if limit else rows      # 看板上選的「跑幾張」、卡片 ⋯ 的「只準備這張」照真的一樣算
    t0, me = time.time(), os.getpid()
    if not rows:
        st({'phase': 'nothing', 'msg': '「正在準備」沒有卡', 't0': t0}); return
    st({'phase': 'fetching_pages', 'pid': me, 'n': len(rows), 't0': t0}); time.sleep(step)
    for i in range(len(rows) + 1):
        st({'phase': 'agent', 'pid': me, 'n': len(rows), 'done': i, 't0': t0}); time.sleep(step / 4)
    st({'phase': 'reconcile', 'pid': me, 'n': len(rows), 't0': t0}); time.sleep(step)

    def mut(f):
        for k in rows:
            if (f.get(k) or {}).get('app') == 'prep':
                f[k]['app'] = 'ready'
    bd.set_fb(mut, live=board)
    st({'phase': 'done', 'n': len(rows), 'ready': len(rows), 'skipped': 0, 'missing': [],
        't0': t0, 'finished_at': time.time()})


def research(board, sp, step, mode, direction, minutes=0):
    path = os.path.join(sp, 'converge_status.json')
    t0, me = time.time(), os.getpid()
    base = {'pid': me, 'mode': mode, 'direction': direction, 't0': t0, 'minutes': minutes}
    jobrun.write(path, dict(base, phase='start')); time.sleep(step)
    jobrun.write(path, dict(base, phase='agent', which=mode)); time.sleep(step * 2)
    jobrun.write(path, dict(base, phase='fold')); time.sleep(step)
    today = time.strftime('%Y-%m-%d')
    new = [{'id': f'https://example.test/fake/{int(t0)}-{i}', 'cat': '其他',
            'target': f'(沙箱假職缺 {i})測試用·沙箱公司', 'chan': '官方', 'ammo': '', 'note': '',
            'bk': False, 'dead': False, 'added': today,
            'sum': {'fit': '沙箱假資料', 'co': '沙箱', 'loc': '', 'deadline': '', 'salary': '',
                    'bar': '', 'posted': '', 'ammo': ''}} for i in (1, 2)]
    bd.set_data(lambda data, fb: data['jobs'].extend(new), live=board)
    jobrun.write(path, dict(base, phase='done', added=len(new), found=len(new) + 1, dropped=1, finished_at=time.time()))


# 假的 agent 回報:副本上的狀態只用來演練看板流程,不讀或改任何網站。
def _fake_runtime():
    """假的「開這一頁的那一家」:副本設定裡用 Chrome 的那一家(副本的卡也跟真的一樣記著,沒記到的卡算接不回來)。"""
    import chrome_door
    door = chrome_door.current()
    return door.runtime if door else None


FAKE_FILL = {'delivery': {'method': 'direct_upload'}}


def apply(board, sp, step, stage, url, limit=0):
    import fill
    import form_record as fr
    path = os.path.join(sp, 'apply_status.json')
    t0, me = time.time(), os.getpid()
    base = {'pid': me, 't0': t0, 'stage': stage, 'url': url}
    jobrun.write(path, dict(base, phase='run')); time.sleep(step)
    today = datetime.date.today().isoformat()
    done = []
    status = fr.board_status(board)          # 核准規則也看投遞前驗收,跟真的一樣

    import delivery_state as ds

    def mut(fb):
        # 跟真的 fill 一樣只送狀態表的事件:開始 → 填好 / 看到已收到申請頁
        for u, m in list(fb.items()):
            if ds.app_of(m) != 'ship' or (url and u != url):
                continue
            if stage == 'fill':
                if (limit and len(done) >= limit) or not ds.allowed(m, 'fill_start'):     # 「跑幾張」:填滿就停
                    continue
                ds.fire(fb, u, 'fill_start', apply={'stage': 'fill', 'at': today, 'issues': []})
                # 真的填表一定由 agent 用 form_record 重記這張表單(記的當天日期);
                # 沒有表單紀錄的話副本上新進可投遞的卡永遠走不到確認。
                old = m.get('form') or {}
                m['form'] = {'plat': old.get('plat') or '沙箱', 'f': old.get('f') or [], 'at': today}
                ds.fire(fb, u, 'fill_ok', apply=fill.round_apply(
                    'fill', session='sandbox-' + str(me), agent_id='sandbox', runtime=_fake_runtime(), profile='',
                    delivery=FAKE_FILL['delivery'], uploaded=[], workspace={'id': 1, 'name': 'sandbox', 'page': 'p1'},
                    tab_id='1:p1'))
                done.append(u)
            elif stage == 'fix':
                if (m.get('apply') or {}).get('session') and ds.allowed(m, 'fix_start'):
                    ds.fire(fb, u, 'fix_start', apply={'stage': 'fix', 'at': today, 'issues': []})
                    ds.fire(fb, u, 'fill_ok', apply=fill.round_apply(
                        'fix', session=m['apply']['session'], agent_id='sandbox', runtime=_fake_runtime(),
                        delivery=FAKE_FILL['delivery'], workspace=m['apply'].get('workspace'),
                        tab_id=m['apply'].get('tab_id')))
                    fr.apply_clear_refill(fb, u)
                    done.append(u)
            elif fr.approval_problem(fb, u, status) is None:
                ev = {'at': today, 'said': 'true', 'evidence': '(沙箱假確認頁)'}
                ds.fire(fb, u, 'submit_start')
                ds.fire(fb, u, 'submit_ok', by='agent', sent_at=today, evidence=ev)
                import ship
                ship.record_sent(fb, u)
                done.append(u)
    bd.set_fb(mut, live=board, by='job_fake')
    jobrun.write(path, dict(base, phase='done', n=len(done), done=len(done), finished_at=time.time(),
                            results=[{'url': u, 'ok': True, 'msg': '沙箱'} for u in done]))


def add(board, sp, step, urls):
    path = os.path.join(sp, 'add_status.json')
    t0 = time.time()
    jobrun.write(path, {'phase': 'fold', 'pid': os.getpid(), 't0': t0, 'n': len(urls)}); time.sleep(step)
    today = time.strftime('%Y-%m-%d')
    # 網址帶 nojob 的當成頁面上看不到這個職缺(跟真的那支一樣不加卡,結果放在貼網址那一格底下)
    unadded = [{'url': u, 'title': '(沙箱)' + u.rstrip('/').rsplit('/', 1)[-1], 'why': '頁面內文是一整串職缺列表,沒有這個職缺'}
               for u in urls if 'nojob' in u]
    urls = [u for u in urls if 'nojob' not in u]

    def mut(data, fb):
        have = {j['id'] for j in data['jobs']}
        for u in urls:
            if u not in have:
                data['jobs'].append({'id': u, 'cat': '其他', 'target': f'(沙箱)手動加入的職缺', 'chan': '直投', 'ammo': '',
                                     'note': '', 'bk': False, 'dead': False, 'added': today,
                                     'sum': {'fit': '沙箱假資料'}, 'src': {'mode': 'add', 'via': 'manual'}})
    bd.set_data(mut, live=board)
    jobrun.write(path, {'phase': 'done', 't0': t0, 'finished_at': time.time(), 'added': len(urls),
                        'n': len(urls) + len(unadded), 'msg': f'加進新職缺 {len(urls)} 張', 'unadded': unadded})


def suggest(board, sp, step):
    path = os.path.join(sp, 'suggest_status.json')
    t0 = time.time()
    jobrun.write(path, {'phase': 'agent', 'pid': os.getpid(), 't0': t0}); time.sleep(step)
    with open(os.path.join(sp, 'suggestion.json'), 'w', encoding='utf-8') as f:    # 安檢門核對過的樣子
        json.dump({'categories': [{'name': '工程', 'icon': '⚙', 'match': 'engineer|工程師'},
                                  {'name': '其他', 'icon': '•', 'match': ''}],
                   'tags': [{'name': '遠端', 'match': 'remote|遠端'}], 'why': '沙箱假建議', 'problems': [],
                   'by': 'agent 判斷'}, f, ensure_ascii=False)
    jobrun.write(path, {'phase': 'done', 't0': t0, 'finished_at': time.time(), 'msg': '建議好了'})


def customize(board, sp, step, url, items):
    """Sandbox run: exercise dispatch and status polling without writing deliverables."""
    t0, me = time.time(), os.getpid()
    path = os.path.join(sp, 'customize_status.json')
    jobrun.write(path, {'phase': 'agent', 'pid': me, 't0': t0, 'n': len(items), 'url': url})
    time.sleep(step)
    jobrun.write(path, {'phase': 'done', 'n': len(items), 'done': 0,
                        'msg': '沙箱只模擬客製派工，不會產生 PDF',
                        't0': t0, 'finished_at': time.time(), 'url': url})


def replies(sp):
    """副本不連外查回音:只寫一份「沒查」的進度,卡不動。"""
    os.makedirs(sp, exist_ok=True)
    jobrun.write(os.path.join(sp, 'reply_status.json'), {'phase': 'done', 'n': 0, 'done': 0, 'msg': '副本未執行查應徵進度',
                                                         't0': time.time(), 'finished_at': time.time()})
    print('副本未執行查應徵進度')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kind', choices=['prep', 'customize', 'research', 'apply', 'add', 'suggest', 'sync', 'replies'], required=True)
    ap.add_argument('--board', required=True)
    ap.add_argument('--mode', default='')
    ap.add_argument('--direction', default='')
    ap.add_argument('--stage', default='')
    ap.add_argument('--url', action='append', default=[])
    ap.add_argument('--item', action='append', default=[])
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--minutes', type=int, default=0)   # 找缺時間上限:副本記在進度裡,看板檢查看得到帶了幾分鐘
    a = ap.parse_args()
    step = float(os.environ.get('JOB_FAKE_STEP', '1'))
    sp = os.environ.get({'prep': 'CUT_TAILOR_TMP', 'customize': 'CUSTOMIZE_TMP', 'research': 'CONVERGE_TMP', 'apply': 'APPLY_TMP',
                         'add': 'ADD_TMP', 'suggest': 'SUGGEST_TMP', 'sync': 'SYNC_TMP', 'replies': 'REPLY_TMP'}[a.kind]) or cf.TMP
    if a.kind == 'sync':
        return sync(a.board, sp, step)
    if a.kind == 'replies':
        return replies(sp)
    if a.kind == 'customize':
        return customize(a.board, sp, step, a.url[-1] if a.url else '', a.item)
    if a.kind == 'apply':
        return apply(a.board, sp, step, a.stage, a.url[-1] if a.url else '', a.limit)
    if a.kind == 'add':
        return add(a.board, sp, step, a.url)
    if a.kind == 'suggest':
        return suggest(a.board, sp, step)
    if os.environ.get('JOB_FAKE_DIE'):          # 測「跑到一半死掉」用
        status = {'prep': 'cut_tailor_status.json', 'customize': 'customize_status.json'}.get(a.kind, 'converge_status.json')
        path = os.path.join(sp, status)
        jobrun.write(path, {'phase': 'agent', 'pid': os.getpid(), 't0': time.time()})
        os._exit(1)
    if a.kind == 'prep':
        prep(a.board, sp, step, a.limit, a.url[-1] if a.url else '')
    else:
        research(a.board, sp, step, a.mode, a.direction, a.minutes)


if __name__ == '__main__':
    main()
