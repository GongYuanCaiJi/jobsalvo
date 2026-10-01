#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_job —— 看板上「➕ 貼網址加入」:使用者自己找到的職缺,貼網址就進「🆕 待評估」。

跟找缺那一輪同一套(research):程式抓取職缺頁文字,agent 依收到的文字對照使用者原話寫卡片摘要與對味程度。
差別只有一個:這張是使用者自己要的,判斷結果不決定要不要加,一律加進去(判斷只拿來寫卡片)。
抓不到頁面文字也照加,卡片標示待確認並寫進回報;只有直連 HTTP 404/410 才不加。

用法:python3 tools/add_job.py --url <網址> [--url <網址> …] [--board B]
"""
import os, sys, time, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import jobrun
from research import web_agent     # 跟找缺同一支:不給操作 Chrome 的能力(#287)

SP = os.environ.get('ADD_TMP') or cf.TMP
STATUS = 'add_status.json'



def _report(msg, need, board, job=''):
    """要他知道的事寫進看板最上面的「📣 回報」;寫不進看板至少印進這一輪的紀錄(「看紀錄」看得到)。"""
    try:
        import agent_report
        agent_report.report('貼網址加入', msg, need=need, job=job, live=board)
    except Exception as e:  # noqa: BLE001 — 回報寫不進看板:印進這一輪的紀錄
        print(f'⚠ 這則回報寫不進看板({str(e)[:120]}):{msg} → {need}')

def run(urls, board, browser_required=True, run_agent=None):
    import research as rs, prefs, page_fetch
    st = lambda d: jobrun.write(os.path.join(SP, STATUS), d)
    t0 = time.time()
    fb, jobs = prefs.load(board)
    have = {j['id'] for j in jobs}
    urls = [u.strip() for u in urls if u.strip().startswith('http')]
    new = [u for u in dict.fromkeys(urls) if u not in have]
    if not new:
        # 已移除的卡也算板上有(去重用全部卡);只說「都有了」他在看板上找不到,要講它在哪、怎麼放回來
        gone = sum(1 for u in dict.fromkeys(urls) if (fb.get(u) or {}).get('rm'))
        msg = '這幾個網址板上都已經有了' + (
            f'(其中 {gone} 張在「🗑 已移除」,要的話到那一頁按「↩︎ 放回看板」)' if gone else '')
        st({'phase': 'nothing', 'msg': msg if urls else '沒有網址'})
        return 0

    st({'phase': 'fold', 'pid': os.getpid(), 't0': t0, 'n': len(new), 'step': '檢查網址'})
    cands, gone = [], []
    for url, fetched in zip(new, page_fetch.fetch_many(new)):
        if fetched.status == 'closed':
            gone.append(url)
            continue
        cands.append({
            'url': url,
            'title': fetched.title or '職缺頁待確認',
            'company': '',
            'via': '手動加入',
            'jd': fetched.text if fetched.readable else '',
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
            res = rs.judge(cands, prefs.cards(fb, jobs), rd, False, run_agent, mode='add', board=board)
        except ar.AgentRunError as e:
            agent_error = f'判斷沒有完成:{e}'
            _report(agent_error, '在「貼網址加入」按「看紀錄」確認後再重試', board)

    src = {'round': time.strftime('%Y-%m-%d %H:%M', time.localtime(t0)), 'mode': 'add', 'via': 'manual'}
    empty = {'keep': False, 'fit': 0, 'why': 'agent 無法讀取職缺頁,請確認網址與登入狀態',
             'cite': [], 'bad_cite': [], 'cat': '其他', 'card': {}}
    entries = []
    for candidate in cands:
        result = res.get(candidate['url']) or empty
        if not agent_error and result.get('wrong'):     # 安檢門擋下:卡照樣加(他自己貼的),摘要不用、原因照實講
            _report('agent 交的判斷跟職缺頁對不上,卡片摘要沒寫:' + '；'.join(result['wrong'][:2])[:300],
                    '不用你處理:卡已經加了;要查原因先打開那張卡的證據', board, job=candidate['url'])
        elif not agent_error and not result.get('readable'):
            _report(f'agent 無法確認職缺頁內容:{candidate["url"]}',
                    '確認職缺網址;要登入才看得到的職缺頁程式讀不到,請自己打開看', board, job=candidate['url'])
        entry = rs.job_entry(candidate, result, src)
        entry['chan'] = '直投'
        entries.append(entry)
    added = rs.add_entries(entries, board) if entries else 0
    if gone:
        _report(f'{len(gone)} 個網址確定已下架(HTTP 404/410 或官方資料端點),沒有加:' + '、'.join(gone[:3]),
                '確認網址對不對', board)
    msg = f'加進待評估 {added} 張' + (f',{len(gone)} 張已下架沒加' if gone else '')
    if agent_error:
        msg += f';{agent_error}'
    st({'phase': 'done', 't0': t0, 'finished_at': time.time(), 'added': added, 'n': len(new), 'msg': msg})
    return added


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--url', action='append', default=[])
    ap.add_argument('--board', default=cf.LIVE)
    a = ap.parse_args()
    try:
        print('新增', run(a.url, a.board), '張')
    except Exception as e:
        jobrun.write(os.path.join(SP, STATUS), {'phase': 'failed', 'msg': f'沒加成:{e}', 'finished_at': time.time()})
        raise


if __name__ == '__main__':
    main()
