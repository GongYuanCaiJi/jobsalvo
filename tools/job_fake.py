#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job_fake —— 看板副本上按「跑準備區」「找新職缺」時跑的假流程(沙箱、測試、board_check 都是副本)。

真的那兩支(cut_tailor、converge)會派 agent、寫 repo 裡的檔、改現行看板。副本上按一下就跑真的,
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
import os, sys, json, time, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd      # noqa: E402
import jobrun               # noqa: E402
import config as cf         # noqa: E402


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
            'sum': {'fit': '沙箱假資料', 'co': '沙箱', 'loc': '無', 'deadline': '無', 'salary': '未公開',
                    'bar': '無', 'posted': '無', 'ammo': '無'}} for i in (1, 2)]
    bd.set_data(lambda data, fb: data['jobs'].extend(new), live=board)
    jobrun.write(path, dict(base, phase='done', added=len(new), found=len(new) + 1, dropped=1, finished_at=time.time()))


# 假的 agent 回報(fill.json 的形狀):真的填表檢查一定回報這次怎麼交履歷(delivery),
# 少了這欄副本上核准鈕永遠按不下去。紀錄本身用 apply_run.fill_record 組,跟真的同一支。
FAKE_FILL = {'delivery': {'method': 'direct_upload'}, 'tab_id': '1', 'tab_url': '', 'blank_for_him': [],
             'uploaded': [], 'notes': [], 'fixed': []}


def apply(board, sp, step, stage, url, limit=0):
    import form_record as fr
    import apply_run as ar
    path = os.path.join(sp, 'apply_status.json')
    t0, me = time.time(), os.getpid()
    base = {'pid': me, 't0': t0, 'stage': stage, 'url': url}
    jobrun.write(path, dict(base, phase='run')); time.sleep(step)
    today = ar.today()
    done = []
    status = fr.board_status(board)          # 核准規則也看投遞前驗收,跟真的一樣

    def mut(fb):
        for u, m in fb.items():
            if not isinstance(m, dict) or m.get('app') != 'ship' or (url and u != url) or (m.get('form') or {}).get('lock'):
                continue
            if stage == 'fill':
                if limit and len(done) >= limit:     # 「跑幾張」:填滿就停
                    continue
                m['apply'] = ar.fill_record('fill', m.get('apply'), FAKE_FILL, [], 'sandbox-' + str(me), '', 'sandbox')
                # 真的填表一定由 agent 用 form_record 重記這張表單(記的當天日期,並作廢舊的核准);
                # 沒有表單紀錄的話副本上新進可投遞的卡永遠走不到核准。
                old = m.get('form') or {}
                m['form'] = {'plat': old.get('plat') or '沙箱', 'f': old.get('f') or [], 'at': today}
                m.pop('approve', None)
                done.append(u)
            elif stage == 'fix':
                if (m.get('apply') or {}).get('session'):
                    m['apply'] = ar.fill_record('fix', m['apply'], FAKE_FILL, [], m['apply']['session'], '', 'sandbox')
                    fr.apply_clear_refill(fb, u)
                    done.append(u)
            elif fr.approval_problem(fb, u, status) is None:
                ev = ar.submit_evidence({'confirm_text': '(沙箱假確認頁)', 'confirm_url': ''}, '')
                fr.apply_mark_sent(fb, u, ev, today)
                done.append(u)
    bd.set_fb(mut, live=board, by='job_fake')
    jobrun.write(path, dict(base, phase='done', n=len(done), done=len(done), finished_at=time.time(),
                            results=[{'url': u, 'ok': True, 'msg': '沙箱'} for u in done]))


def add(board, sp, step, urls):
    path = os.path.join(sp, 'add_status.json')
    t0 = time.time()
    jobrun.write(path, {'phase': 'fold', 'pid': os.getpid(), 't0': t0, 'n': len(urls)}); time.sleep(step)
    today = time.strftime('%Y-%m-%d')

    def mut(data, fb):
        have = {j['id'] for j in data['jobs']}
        for u in urls:
            if u not in have:
                data['jobs'].append({'id': u, 'cat': '其他', 'target': f'(沙箱)手動加入的職缺', 'chan': '直投', 'ammo': '',
                                     'note': '', 'bk': False, 'dead': False, 'added': today,
                                     'sum': {'fit': '沙箱假資料'}, 'src': {'mode': 'add', 'via': 'manual'}})
    bd.set_data(mut, live=board)
    jobrun.write(path, {'phase': 'done', 't0': t0, 'finished_at': time.time(), 'added': len(urls), 'n': len(urls),
                        'msg': f'加進待評估 {len(urls)} 張'})


def suggest(board, sp, step):
    path = os.path.join(sp, 'suggest_status.json')
    t0 = time.time()
    jobrun.write(path, {'phase': 'agent', 'pid': os.getpid(), 't0': t0}); time.sleep(step)
    with open(os.path.join(sp, 'suggest.json'), 'w', encoding='utf-8') as f:
        json.dump({'categories': [{'name': '工程', 'icon': '⚙', 'match': 'engineer|工程師'},
                                  {'name': '其他', 'icon': '•', 'match': ''}],
                   'tags': [{'name': '遠端', 'match': 'remote|遠端'}], 'why': '沙箱假建議'}, f, ensure_ascii=False)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kind', choices=['prep', 'customize', 'research', 'apply', 'add', 'suggest'], required=True)
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
                         'add': 'ADD_TMP', 'suggest': 'SUGGEST_TMP'}[a.kind]) or cf.TMP
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
