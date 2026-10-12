#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_job —— 看板上「➕ 貼網址加入」:使用者自己找到的職缺,貼網址就進「🆕 待評估」。

跟找缺那一輪同一套(research):程式抓取職缺頁文字,agent 依收到的文字對照使用者原話寫卡片摘要與對味程度。
差別只有一個:這張是使用者自己要的,判斷結果不決定要不要加,一律加進去(判斷只拿來寫卡片)。
抓不到頁面文字也照加,卡片標示待確認並寫進回報;只有直連 HTTP 404/410 才不加。
agent 都不能用(額度用完、限流)時不加空卡:抓好的職缺存成待寫(跟找缺的待判同一套),下一次跑先寫它們;
自動流程每隔一小時自己再跑一次(autopilot),不用他再貼一次。

外面已經判好的(別的 agent 讀過 JD、照找缺判斷的交件單格式寫好,每列多一個 url):用 --judged 交進來,
不再派 agent 讀第二次;程式照樣抓頁、照樣過安檢門,引用對不上的那張照加、摘要不用、原因照實回報。
格式就是看板「🔎 找新職缺」判斷那一段的 📝 prompt(research.preview('judge'))。

用法:python3 tools/add_job.py --url <網址> [--url <網址> …] [--board B]
      python3 tools/add_job.py --judged 判好的.json [--board B]     # [{url, title, company, keep, fit, why, cat, card, reasons…}]
"""
import os, sys, time, argparse, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import jobrun
from research import web_agent     # 跟找缺同一支:不給操作 Chrome 的能力(#287)

SP = os.environ.get('ADD_TMP') or cf.TMP
STATUS = 'add_status.json'


def pending_path():
    import research as rs
    return os.path.join(rs.DIR, 'add_pending.json')


def waiting():
    """上一次 agent 都不能用、還沒寫卡片的幾張。"""
    import research as rs
    return rs.pending_count(pending_path())



def _report(msg, need, board, job=''):
    """要他知道的事寫進看板最上面的「📣 回報」;寫不進看板至少印進這一輪的紀錄(「看紀錄」看得到)。"""
    try:
        import agent_report
        agent_report.report('貼網址加入', msg, need=need, job=job, live=board)
    except Exception as e:  # noqa: BLE001 — 回報寫不進看板:印進這一輪的紀錄
        print(f'⚠ 這則回報寫不進看板({str(e)[:120]}):{msg} → {need}')

def run(urls, board, browser_required=True, run_agent=None, answers=None):
    import research as rs, prefs, page_fetch
    st = lambda d: jobrun.write(os.path.join(SP, STATUS), d)
    t0 = time.time()
    fb, jobs = prefs.load(board)
    have = {j['id'] for j in jobs}
    held = [c for c in rs.pending_take(pending_path()) if c['url'] not in have]   # 上一次 agent 不能用、還沒寫的先寫
    urls = [u.strip() for u in urls if u.strip().startswith('http')]
    new = [u for u in dict.fromkeys(urls) if u not in have and u not in {c['url'] for c in held}]
    if not new and not held:
        # 已移除的卡也算板上有(去重用全部卡);只說「都有了」他在看板上找不到,要講它在哪、怎麼放回來
        gone = sum(1 for u in dict.fromkeys(urls) if (fb.get(u) or {}).get('rm'))
        msg = '這幾個網址板上都已經有了' + (
            f'(其中 {gone} 張在「🗑 已移除」,要的話到那一頁按「↩︎ 放回看板」)' if gone else '')
        st({'phase': 'nothing', 'msg': msg if urls else '沒有網址'})
        return 0

    st({'phase': 'fold', 'pid': os.getpid(), 't0': t0, 'n': len(new) + len(held), 'step': '檢查網址'})
    cands, gone = list(held), []
    for url, fetched in zip(new, page_fetch.fetch_many(new)):
        if fetched.status == 'closed':
            gone.append(url)
            continue
        cands.append({
            'url': url,
            'title': fetched.title or '職缺頁待確認',
            'company': '',
            'via': '手動加入',
            'jd': fetched.jd,
            'page_status': fetched.status,
            'page_via': fetched.via,
            'page_http_status': fetched.http_status,
            'posted_at': fetched.posted_at,
            'posted_src': fetched.posted_source,
            'page_errors': list(fetched.errors),
            'flag': [] if fetched.readable else ['程式無法取得職缺頁文字;保留待確認,不可猜測內容'],
        })

    res = {}
    agent_error = ''
    if cands:
        st({'phase': 'judge', 'pid': os.getpid(), 't0': t0, 'n': len(cands), 'step': 'agent 讀取職缺並寫卡片摘要'})
        import agent_run as ar
        if run_agent is None:
            run_agent = web_agent(board)
        rd = os.path.join(rs.DIR, 'rounds', time.strftime('%Y%m%d-%H%M%S') + '-add')
        os.makedirs(rd, exist_ok=True)
        try:
            # 他撥了「愛派幾隻就派幾隻」:跟找缺一樣,判斷那幾批同時跑
            res = rs.judge(cands, prefs.cards(fb, jobs), rd, False, run_agent, mode='add', board=board,
                           par=bool(fb.get('__agentfree__')), answers=answers)
        except ar.AgentRunError as e:
            if all(r.status == 'unavailable' for r in e.results):
                # agent 都不能用(額度用完、限流、要重新登入):等得到,不加空卡,存成待寫,之後接著寫
                n = rs.pending_save(cands, pending_path())
                msg = f'{n} 張還沒寫卡片:agent 都不能用({e})'
                print(msg + ';每隔一小時會自動接著寫')
                st({'phase': 'waiting', 't0': t0, 'finished_at': time.time(), 'added': 0, 'n': n, 'msg': msg})
                return 0
            agent_error = f'判斷沒有完成:{e}'
            _report(agent_error, '在「貼網址加入」按「看紀錄」確認後再重試', board)

    src = {'round': time.strftime('%Y-%m-%d %H:%M', time.localtime(t0)), 'mode': 'add', 'via': 'manual'}
    empty = {'keep': False, 'fit': 0, 'why': 'agent 無法讀取職缺頁,請確認網址與登入狀態',
             'cite': [], 'bad_cite': [], 'cat': '其他', 'card': {}}
    entries, unseen = [], [(url, '確定已下架') for url in gone]
    for candidate in cands:
        result = res.get(candidate['url']) or empty
        if result.get('missing'):             # 頁面內文看不到這個職缺(首頁、列表、別的職缺):不加卡,它看到什麼一起講
            unseen.append((candidate['url'], '頁面上看不到這個職缺:' + result['missing']))
            continue
        # 卡照樣加(他自己貼的),摘要沒寫成的原因寫在卡上;原因細節在那張卡的證據(回報只放要他做的事)
        if result.get('wrong'):
            result = dict(result, why='摘要沒寫成:agent 交了兩次都跟職缺頁對不上')
            print(f"{candidate['url']}:" + '；'.join(result['wrong'])[:300])
        elif not result.get('readable') and not result.get('why'):
            result = dict(result, why='摘要沒寫成:程式讀不到這一頁(要登入或要跑 JS),請自己打開看')
        entry = rs.job_entry(candidate, result, src)
        entry['chan'] = '直投'
        entries.append(entry)
    added = rs.add_entries(entries, board) if entries else 0
    if unseen:                                # 沒加的一輪講一次:他貼的網址不能默默不見
        _report(f'{len(unseen)} 個網址沒有加成卡:' + '；'.join(f'{u}({why})' for u, why in unseen[:5])[:600],
                '要的話自己打開看;真的是職缺就再貼一次', board)
    msg = f'加進待評估 {added} 張' + (f',{len(unseen)} 張沒加(已下架或頁面上看不到職缺)' if unseen else '')
    if agent_error:
        msg += f';{agent_error}'
    st({'phase': 'done', 't0': t0, 'finished_at': time.time(), 'added': added, 'n': len(cands) + len(gone), 'msg': msg})
    return added


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--url', action='append', default=[])
    ap.add_argument('--judged', help='外面已經判好的交件單(JSON 清單,每列帶 url)')
    ap.add_argument('--board', default=cf.LIVE)
    a = ap.parse_args()
    answers = None
    if a.judged:
        with open(a.judged, encoding='utf-8') as f:
            answers = {r['url']: {k: v for k, v in r.items() if k != 'url'} for r in json.load(f) if r.get('url')}
    try:
        print('新增', run(a.url + list(answers or ()), a.board, answers=answers), '張')
    except Exception as e:
        jobrun.write(os.path.join(SP, STATUS), {'phase': 'failed', 'msg': f'沒加成:{e}', 'finished_at': time.time()})
        raise


if __name__ == '__main__':
    main()
