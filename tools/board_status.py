#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_status —— 看板現在卡在哪,以及「可投遞」之前的機械把關。

兩個用途,同一份判斷:
  1. 接手時先跑它,一眼看出各階段幾張、哪張卡住、卡在什麼。
     以前要翻舊對話猜狀態,猜錯就重做。
  2. --write 把結果寫進看板,他打開就看得到,不用問人。

把關的規矩:
  待你決定／可投遞 → 可投遞夾要建好、選的履歷與語言跟夾裡一致、檔案都在(ship.check)。
  --links 預抓職缺頁文字;直連 HTTP 404/410 或 104 官方資料端點明確下架時直接判定,
          其他可讀文字由 agent 判斷;抓不到或無法確認只提示,不擋批次。

用法:
  uv run python tools/board_status.py                 # 看報告
  uv run python tools/board_status.py --write         # 順便寫進看板
  uv run python tools/board_status.py --links         # 加驗連結還活著
"""
import sys, os, json, argparse, datetime, tempfile, time
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd
import config as cf
import ship
import card

STAGE = {'prep': '準備履歷中', 'ready': '已準備', 'ship': '可以投了'}
SENT = {'like': '喜歡', 'meh': '普通', 'dislike': '不喜歡', 'grow': '差一點', 'techerr': '出錯了'}
LINK_FETCH_WORKERS = 6
LINK_AGENT_BATCH = 8   # 一次把幾十頁全文塞給同一隻 agent 容易整批判不出來,分小批問
ISSUES_FOUND = 3  # 卡片有阻擋驗收問題；程式本身沒有失敗


# 同一個網址、頁面原文一個字都沒變,agent 的判斷也不會變:記著,這段時間內直接沿用。
# 以前每重建一次就把已準備、可投遞的每一頁全部重問一次(一次約 25 秒,改一個字就重建一次)。
LINK_VERDICT_DAYS = 3


def _verdict_cache_path():
    return os.path.join(cf.TMP, 'link-verdicts.json')


def _verdict_key(url, result):
    import hashlib
    return hashlib.sha256((str(url) + '\0' + str(result.text or '')).encode('utf-8')).hexdigest()


def _agent_link_verdicts(pages, board, run_agent=None, now=None):
    """Ask an agent to judge readable page text, a small batch at a time; never infer closure from keywords."""
    now = time.time() if now is None else now
    try:
        with open(_verdict_cache_path(), encoding='utf-8') as fh:
            cache = json.load(fh)
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    cache = {k: v for k, v in cache.items()
             if isinstance(v, list) and len(v) in (3, 4) and now - float(v[2]) < LINK_VERDICT_DAYS * 86400}
    verdicts, ask = {}, {}
    for url, result in pages.items():
        hit = cache.get(_verdict_key(url, result))
        if hit:
            verdicts[url] = (hit[0], hit[1]) + ((hit[3],) if len(hit) > 3 and hit[3] else ())
        else:
            ask[url] = result
    items = list(ask.items())
    for start in range(0, len(items), LINK_AGENT_BATCH):
        got = _agent_link_batch(dict(items[start:start + LINK_AGENT_BATCH]), board, run_agent)
        verdicts.update(got)
        for url, (status, reason, *quote) in got.items():
            # agent 自己失敗的(沒交、格式錯、跟頁面對不上)不記,下次重問;它看完原文判「不確定」的照樣記
            if not reason.startswith(AGENT_FAILED):
                cache[_verdict_key(url, ask[url])] = [status, reason, now] + quote[:1]
    if ask:
        try:
            os.makedirs(os.path.dirname(_verdict_cache_path()), exist_ok=True)
            tmp = _verdict_cache_path() + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(cache, fh, ensure_ascii=False)
            os.replace(tmp, _verdict_cache_path())
        except OSError:   # 記不下來只是下次多問一次,這一輪的判斷已經拿到了
            pass
    return verdicts


AGENT_FAILED = ('agent 判斷失敗', 'agent 沒有交', 'agent 回覆格式', 'agent 交的判斷跟頁面對不上')


def _agent_link_batch(pages, board, run_agent=None):
    """一小批交給 agent 判斷;這一批的指示、動作紀錄、交件單、安檢門的比對結果記進每一張卡的證據(#315、#317)。"""
    if not pages:
        return {}
    import agent_run as ar
    import evidence
    with evidence.opened('link_status', 'judge', list(pages), board):
        with tempfile.TemporaryDirectory(prefix='jobsalvo-link-gate-') as directory:
            output = os.path.join(directory, 'verdicts.json')
            log = os.path.join(directory, 'agent.log')
            lines = []
            for i, (url, result) in enumerate(pages.items(), 1):
                lines.append(f'=== J{i} ===\nURL: {url}\n來源路徑: {result.via}\n'
                             f'職缺頁原文:\n--- 開始 ---\n{result.text}\n--- 結束 ---')
            prompt = (
                '只依照下方程式提供的頁面原文,判斷網址所指的這一個職缺是否已經關閉或移除。'
                '不可開瀏覽器、不可請求網址。只看整體語意;頁面提及「expired」「已關閉」等字眼, '
                '若描述的是憑證、帳號或其他內容,不代表職缺已關。證據不足就填 uncertain。'
                '判 closed 一定要在 quote 逐字抄頁面上講職缺關了的那一句,程式會拿它跟原文比,原文裡找不到的不收。\n\n'
                + '\n\n'.join(lines) + '\n\n'
                + f'把 JSON 寫到 {output}:{{"jobs":[{{"id":"J1","status":"live|closed|uncertain",'
                  '"quote":"頁面原文逐字抄的那一句(closed 必填)","reason":"一句話說明,或不確定的原因"}]}}。每個 id 恰好出現一次。')
            try:
                if run_agent is None:
                    outcome = ar.run(prompt, log, cf.HOME, browser_required=False, web=False, board=board)
                else:
                    outcome = run_agent(prompt, log, False)
                if outcome is not None:
                    ar.require_success(outcome)
            except Exception as error:  # noqa: BLE001 — 判斷不了就照實標成不確定(寫上原因),不改卡
                return {url: ('uncertain', f'agent 判斷失敗: {str(error)[:160]}') for url in pages}
            import gate
            if evidence.active() is not None:
                evidence.active().handoff(output)
            payload, _missing = gate.read(directory, 'link_status', where=output)   # 交件單只經安檢門讀
            if payload is None:   # 沒交或寫壞了:照實標成不確定
                return {url: ('uncertain', 'agent 沒有交可讀的判斷') for url in pages}
        ids = {f'J{i}': url for i, url in enumerate(pages, 1)}
        verdict = gate.inspect('link_status', payload,
                               gate.Truth(given={jid: pages[url].text or '' for jid, url in ids.items()}))
        if not isinstance(payload.get('jobs'), list):
            return {url: ('uncertain', 'agent 回覆格式無效') for url in pages}
        rows = {r.key: r for r in verdict.rows.get('jobs', []) if r.key in ids}
        result = {}
        for jid, url in ids.items():
            row = rows.get(jid)
            if row is not None and not row.ok:        # 安檢門擋下:講清楚哪一格、agent 說什麼、實際是什麼
                result[url] = ('uncertain', ('agent 交的判斷跟頁面對不上:' + '；'.join(row.problems))[:240])
                continue
            status = (row.judged if row else {}).get('status')
            if status not in ('live', 'closed', 'uncertain'):
                status = 'uncertain'
            reason = str((row.facts if row else {}).get('reason') or '')[:240]
            quote = str((row.facts if row else {}).get('quote') or '').strip()[:240]
            result[url] = (status, reason) + ((quote,) if status == 'closed' and quote else ())
        return result


def _link_issues(stages, board, fetch_page=None, run_agent=None, timings=None, fb=None):
    import page_fetch
    fetch_page = fetch_page or page_fetch.fetch
    issues, revived, revived_ids, readable = [], [], set(), {}
    stage_for = {}
    pending = []
    for stage in ('ready', 'ship'):
        for job in stages[stage]:
            url = job.get('id')
            if not str(url or '').startswith('http'):
                continue
            pending.append((stage, job, url))

    def read_page(item):
        stage, job, url = item
        try:
            return stage, job, url, fetch_page(url), None
        except Exception as error:  # noqa: BLE001 — 一頁抓不到不擋其他頁;錯誤交回去照實報
            return stage, job, url, None, error

    started = time.perf_counter()
    if pending:
        with ThreadPoolExecutor(max_workers=min(LINK_FETCH_WORKERS, len(pending))) as pool:
            results = pool.map(read_page, pending)
            for stage, job, url, page, error in results:
                stage_for[url] = stage
                if error is not None:
                    issues.append({'jid': url, 't': card.name(job), 'stage': stage,
                                   'kind': 'unverified', 'soft': True,
                                   'msg': f'職缺頁擷取失敗,狀態未知:{str(error)[:180]}'})
                    continue
                if page.status == 'closed':
                    reason = ('104 資料端點明確標示職缺已關閉或不存在' if page.via == '104'
                              else f'來源頁直連回 HTTP {page.http_status},職缺已下架')
                    issues.append({'jid': url, 't': card.name(job), 'stage': stage,
                                   'kind': 'closed', 'msg': reason})
                elif page.verified_live:
                    if job.get('dead'):
                        job['dead'] = False
                        revived.append(card.name(job))
                        revived_ids.add(url)
                elif page.readable:
                    readable[url] = page
                else:
                    issues.append({'jid': url, 't': card.name(job), 'stage': stage,
                                   'kind': 'unverified', 'soft': True,
                                   'msg': f'職缺頁擷取未知;路徑 {page.via or "無"}, '
                                          f'HTTP {page.http_status or "無回應"}'})
    if timings is not None:
        timings['連結擷取'] = time.perf_counter() - started

    started = time.perf_counter()
    verdicts = _agent_link_verdicts(readable, board, run_agent)
    if timings is not None:
        timings['agent 判讀'] = time.perf_counter() - started
    for url, page in readable.items():
        status, reason, *quote = verdicts.get(url, ('uncertain', 'agent 沒有判斷'))
        stage = stage_for[url]
        job = next(j for j in stages[stage] if j.get('id') == url)
        if status == 'closed':
            # agent 判斷(程式核對不了職缺真的關了,只核對它抄的那一句真的在頁面上):標明、附原文;
            # 他在卡上按「不對,職缺還在」(judged_no.closed)就不擋,只留一行提示
            said = f'agent 判斷職缺已關閉,頁面原文:「{quote[0]}」' if quote else 'agent 判斷職缺已關閉'
            said += f'({reason})' if reason else ''
            undone = ((fb or {}).get(url) or {}).get('judged_no', {}).get('closed')
            issues.append(dict({'jid': url, 't': card.name(job), 'stage': stage, 'kind': 'closed',
                                'judged': quote[0] if quote else '',
                                'msg': said + ('。你說職缺還在,不擋' if undone else '')},
                               **({'soft': True} if undone else {})))
        elif status == 'uncertain':
            issues.append({'jid': url, 't': card.name(job), 'stage': stage,
                           'kind': 'unverified', 'soft': True,
                           'msg': 'agent 無法確認職缺狀態' + (f': {reason}' if reason else '')})
        elif job.get('dead'):
            job['dead'] = False
            revived.append(card.name(job))
            revived_ids.add(url)
    return issues, revived, revived_ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--board', default=bd.LIVE)
    ap.add_argument('--write', action='store_true', help='把結果寫進看板,打開就看得到')
    ap.add_argument('--links', action='store_true', help='另外連外驗職缺連結還活著(慢)')
    ap.add_argument('--timings', action='store_true', help='列出連結驗收各階段耗時')
    a = ap.parse_args()

    p = bd.load(a.board)
    jobs, FB = p['data']['jobs'], json.loads(p['fb'])

    stages = {k: [] for k in STAGE}
    sent = {k: 0 for k in SENT}
    untouched = 0
    for j in jobs:
        f = FB.get(j['id']) or {}
        if f.get('rm'):
            continue                   # 他已經移除的卡不驗(也不連外問 agent);以前照樣列進「N 張沒過驗收」,頁面上卻看不到那張
        if f.get('app') in stages:
            stages[f['app']].append(j)
        elif f.get('s'):
            sent[f['s']] = sent.get(f['s'], 0) + 1
        else:
            untouched += 1

    issues = []
    for st in ('ready', 'ship'):
        for j in stages[st]:
            for m in ship.check(j, FB):   # 只驗使用者真正要投的那一份(履歷 × 語言,看板上的決定優先)
                issues.append({'jid': j['id'], 't': card.name(j), 'stage': st,
                               'kind': 'files', 'msg': m})

    revived, revived_ids = [], set()
    if a.links:
        link_timings = {} if a.timings else None
        link_issues, revived, revived_ids = _link_issues(stages, a.board, timings=link_timings, fb=FB)
        issues.extend(link_issues)
        if link_timings is not None:
            print('計時:' + '、'.join(f'{name} {seconds:.1f}s'
                                     for name, seconds in link_timings.items()))

    if revived:
        print(f'連結其實活著,拿掉失效標記 {len(revived)} 個:' + '、'.join(revived[:6]))
    print(f'看板:{len(jobs)} 個職缺 · 未看 {untouched}')
    print('  已標:' + '、'.join(f'{SENT[k]} {v}' for k, v in sent.items() if v))
    print('  管線:' + '、'.join(f'{STAGE[k]} {len(v)}' for k, v in stages.items()))
    blocking = [x for x in issues if not x.get('soft')]
    notes = [x for x in issues if x.get('soft')]
    if not blocking:
        print('\n✅ 管線裡的每一張都過關(要寄的檔案'
              + ('、連結' if a.links else '') + ')。')
    else:
        print(f'\n⚠ {len(blocking)} 個問題:')
        for x in blocking:
            print(f'  [{STAGE[x["stage"]]}] {x["t"]} ← {x["msg"]}')
    if notes:
        print(f'\nℹ {len(notes)} 張還沒確認職缺還在不在(不擋):')
        for x in notes:
            print(f'  [{STAGE[x["stage"]]}] {x["t"]} ← {x["msg"]}')

    if a.write:
        status = {
            'schema_version': 2,
            'at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
            'checked_links': bool(a.links),
            'issues': [dict({'jid': x['jid'], 't': x['t'], 'stage': x['stage'],
                             'kind': x['kind'], 'msg': x['msg']},
                            **({'soft': True} if x.get('soft') else {}),
                            **({'judged': x['judged']} if 'judged' in x else {})) for x in issues],
        }
        old = p['data'].get('status') or {}
        old_comparable = {k: old.get(k) for k in ('schema_version', 'checked_links', 'issues')}
        new_comparable = {k: status[k] for k in ('schema_version', 'checked_links', 'issues')}
        if old_comparable == new_comparable:
            print('\n看板驗收狀態沒有變化,不重寫看板。')
            return ISSUES_FOUND if blocking else 0
        # 在鎖裡只改 status 這一欄(其他資料以現行看板為準,開跑之後別人寫進去的不會被蓋掉)
        def put(data, fb):
            data['status'] = status
            for j in data['jobs']:
                if j.get('id') in revived_ids:
                    j['dead'] = False
        bd.set_data(put, live=a.board)
        print(f'\n已寫進看板({len(blocking)} 個問題、{len(notes)} 張還沒確認),重整就看得到。')
    return ISSUES_FOUND if blocking else 0


if __name__ == '__main__':
    sys.exit(main())
