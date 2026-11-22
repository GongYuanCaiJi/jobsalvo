#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_job —— 看板上「➕ 貼網址加入」:使用者自己找到的職缺,貼網址就進「🆕 待評估」。

跟找缺同一套讀頁、核對與成卡(tools/find.py,J-16):程式讀每一頁,判斷 agent 每一欄引那一頁的段落。
差別只有一個:這張是使用者自己要的,判斷結果不決定要不要加,一律加進去。讀不到的頁、交件單壞掉也照加,卡上照實標。
agent 都不能用(額度用完、限流)時不加空卡:讀好的頁存成待寫,下一次跑先寫它們;
自動流程每隔一小時自己再跑一次(autopilot),不用他再貼一次。

用法:python3 tools/add_job.py --url <網址> [--url <網址> …] [--board B]
"""
import argparse
import contextlib
import fcntl
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf  # noqa: E402
import jobrun  # noqa: E402
from web import seen  # noqa: E402
from web.numbered import Numbered  # noqa: E402
from web.outside import Real  # noqa: E402
from web.round import End  # noqa: E402

STATUS = 'add_status.json'


def tmp():
    return os.environ.get('ADD_TMP') or cf.TMP


def pending_path():
    return os.path.join(cf.RESEARCH, 'add_pending.json')


def _pending():
    try:
        with open(pending_path(), encoding='utf-8') as source:
            return json.load(source)
    except (OSError, ValueError):
        return []


def waiting():
    """上一次 agent 都不能用、還沒寫卡片的幾張。"""
    return len(_pending())


def _wait(urls, pages, shots, board):
    """讀好的頁存成待寫(不重讀),連同讀頁那一刻的截圖在這一輪證據夾的位置;回存了幾張。"""
    import evidence
    rows = [{'url': url, 'page': seen.dump(page),
             'shot': evidence.path(url, shots[url].get('screenshot_file'), board) or ''}
            for url, page in zip(urls, pages)]
    os.makedirs(os.path.dirname(pending_path()), exist_ok=True)
    with open(pending_path(), 'w', encoding='utf-8') as target:
        json.dump(rows, target, ensure_ascii=False)
    return len(rows)


def _stored(row):
    """待寫的頁;截圖照讀頁時的放法複製一份(收進這一輪的證據夾),找不到就照實記截圖沒取得。存的格式讀不懂回 None(重讀)。"""
    try:
        page = seen.load(row['page'])
    except (KeyError, TypeError, ValueError):
        return None
    if not os.path.isfile(row['shot']):
        return seen.with_shot(page, '', seen.dump(page)['shot_why'] or '待寫期間讀頁那一刻的截圖不見了')
    shot = os.path.join(tempfile.mkdtemp(prefix='jobsalvo-find-'), 'page.png')
    shutil.copyfile(row['shot'], shot)
    return seen.with_shot(page, shot, '')


def _add(entries, board):
    """把新卡加進看板(已經有的不動);回新增幾張。"""
    import board_doc as bd
    added = []

    def update(data, _fb):
        known = {row['id'] for row in data['jobs']}
        added.extend(entry for entry in entries if entry['id'] not in known)
        data['jobs'].extend(added)
    with open(os.path.join(cf.HOME, '.reconcile.lock'), 'w') as lock:   # 跟 reconcile 同一把
        fcntl.flock(lock, fcntl.LOCK_EX)
        bd.set_data(update, live=board)
    return len(added)


def _store(review_round, page):
    """待寫頁收進這一輪證據夾,跟新讀的頁一樣有讀頁紀錄與截圖。"""
    record = seen.dump(page)
    row = review_round.page(record, record['why'] or record['status'], shot=record['shot'] or None)
    with contextlib.suppress(OSError):
        os.remove(record['shot'])
        os.rmdir(os.path.dirname(record['shot']))
    return {key: review_round.rel(row[name]) for key, name in (('page_file', 'file'), ('screenshot_file', 'shot'))
            if row.get(name)}


def run(urls, board, browser):
    import agent_report
    import evidence
    import find
    import prefs
    os.makedirs(tmp(), exist_ok=True)
    st = lambda d: jobrun.write(os.path.join(tmp(), STATUS), d)
    t0 = time.time()
    fb, jobs = prefs.load(board)
    have = {j['id'] for j in jobs}
    held = {row['url']: _stored(row) for row in _pending() if row['url'] not in have}   # 上一次 agent 不能用、還沒寫的先寫
    pasted = urls                                                                     # 看板只送 http 開頭的網址
    reread = [url for url, page in held.items() if page is None]                      # 存的頁讀不懂:重讀
    held = {url: page for url, page in held.items() if page is not None}
    new = [u for u in dict.fromkeys(reread + pasted) if u not in have and u not in held]
    if not new and not held:
        # 已移除的卡也算板上有(去重用全部卡);只說「都有了」他在看板上找不到,要講它在哪、怎麼放回來
        gone = sum(1 for u in dict.fromkeys(pasted) if (fb.get(u) or {}).get('rm'))
        msg = '這幾個網址板上都已經有了' + (
            f'(其中 {gone} 張在「🗑 已移除」,要的話到那一頁按「↩︎ 放回看板」)' if gone else '')
        st({'phase': 'nothing', 'msg': msg if pasted else '沒有網址'})
        return 0
    st({'phase': 'fold', 'pid': os.getpid(), 't0': t0, 'n': len(new) + len(held), 'step': '檢查網址'})
    targets = list(held) + new
    review_round = evidence.Round('add', 'review', targets, board)
    shots = {url: _store(review_round, page) for url, page in held.items()}
    pages, read = find.read_urls(browser, new, review_round)
    pages, shots = list(held.values()) + pages, {**shots, **read}
    with contextlib.suppress(OSError):
        os.remove(pending_path())

    st({'phase': 'judge', 'pid': os.getpid(), 't0': t0, 'n': len(targets), 'step': 'agent 讀取職缺並寫卡片摘要'})
    shared, feedback, jobs = find.user_materials(board)
    ask = find.ask_of('add')
    material = Numbered()
    judged = find.Run(board, browser, 0).agent(
        find.judge_brief(ask, shared, material, targets, pages, feedback, jobs),
        {'urls': frozenset(targets), 'resumes': frozenset(find.resume_choices())}, 3600, materials=(material,))
    if judged.end is End.NOT_STARTED:
        # agent 都不能用(額度用完、限流、要重新登入):等得到,不加空卡,存成待寫,之後接著寫
        n = _wait(targets, pages, shots, board)
        msg = f'{n} 張還沒寫卡片:agent 都不能用({judged.end_why})'
        print(msg + ';每隔一小時會自動接著寫')
        st({'phase': 'waiting', 't0': t0, 'finished_at': time.time(), 'added': 0, 'n': n, 'msg': msg})
        return 0
    rows, _unmatched = find.rows_by_url(judged, targets)
    run_fields = {'round': time.strftime('%Y-%m-%d %H:%M'), 'mode': 'add'}
    entries = [dict(find.card_of(url, {'via': 'manual'}, page, rows[url][0], shots[url], run_fields,
                                 rows[url][1], jobs), chan='直投')
               for url, page in zip(targets, pages)]
    added = _add(entries, board)
    problem = '' if judged.end is End.HANDED else f'判斷 agent 沒有交件({judged.end.value})'
    if problem:
        agent_report.tell('貼網址加入', f'判斷沒有完成:{problem}', need='在「貼網址加入」按「看紀錄」確認後再重試', live=board)
    msg = f'加進新職缺 {added} 張' + (f';判斷沒有完成:{problem}' if problem else '')
    st({'phase': 'done', 't0': t0, 'finished_at': time.time(), 'added': added, 'n': len(targets), 'msg': msg})
    return added


def main(argv=None, browser=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', action='append', default=[])
    ap.add_argument('--board', required=True)                    # 看板一律帶現行看板
    a = ap.parse_args(argv)
    try:
        print('新增', run(a.url, a.board, browser or Real()), '張')
    except Exception as e:
        jobrun.write(os.path.join(tmp(), STATUS), {'phase': 'failed', 'msg': f'沒加成:{e}', 'finished_at': time.time()})
        raise


if __name__ == '__main__':
    main()
