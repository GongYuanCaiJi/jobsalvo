"""從 ATS 日期或程式擷取的頁面文字補上可確認的刊登日期。"""
import os
import time, re, sys, json, argparse, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_doc as bd
import config as cf
import agent_run as ar

CACHE = cf.POSTED_CACHE
RESULT = os.path.join(cf.TMP, 'posted_age_results.json')
LOG = os.path.join(cf.TMP, 'posted_age.log')
API_DATE_SOURCES = {
    '104 appearDate',
    'JobPosting datePosted',          # schema.org 標準欄位(page_fetch 從頁面的 JSON-LD 讀)
    'Ashby publishedAt (last published)',
    'Greenhouse first_published',
}


def _provider_date(source):
    return str(source or '').strip() in API_DATE_SOURCES


# 一輪最多交給 agent 幾頁;剩下的留在快取,下一輪接著做。以前整個看板幾百頁一次塞進去(150 萬字),agent 根本開不起來。
AGENT_BATCH = 20
# 只給 agent 頁面上「看起來跟日期有關」的那幾行,不給整頁原文
_DATE_HINT = re.compile(
    r'\d{4}\s*[-/.年]\s*\d{1,2}|\d{1,2}\s*[-/.月]\s*\d{1,2}\s*(?:日|,?\s*\d{4})|'
    r'posted|published|date|ago|刊登|發布|發佈|更新|日期|天前|小時前|'
    r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d', re.I)


def date_lines(text, limit=1500):
    """頁面文字裡看起來跟日期有關的行;一行都沒有就回空字串(那頁不用問 agent)。"""
    out, size = [], 0
    for line in str(text or '').splitlines():
        line = line.strip()
        if line and len(line) < 300 and _DATE_HINT.search(line):
            out.append(line)
            size += len(line)
            if size > limit:
                break
    return '\n'.join(out)


def prompt_for(jobs, urls, out):
    cards = '\n\n'.join(
        f'- {url} | {jobs.get(url, {}).get("target") or "職缺"}\n'
        f'頁面上跟日期有關的幾行(程式從原文挑出來的):\n--- 開始 ---\n{date_lines(urls[url].text)}\n--- 結束 ---'
        for url in urls)
    return (
        '請只依照程式提供的頁面文字,補上頁面清楚標示的刊登日期;不可開啟來源網址或使用瀏覽器。'
        '不要把更新日期、轉載日期或猜測當成刊登日期。找不到明確日期時,請在 inaccessible 說明原因;不要用空字串表示未確認日期。\n\n'
        f'職缺與頁面文字:\n{cards}\n\n'
        f'把結果寫入 {out},格式如下:\n'
        '{{"dates":[{{"url":"原網址","posted_at":"YYYY-MM-DD",'
        '"source":"頁面上日期欄位的名稱"}}],'
        '"inaccessible":[{"url":"原網址","reason":"無法讀取的原因",'
        '"need":"本人要做的事;不需要本人處理就寫空字串"}]}}\n'
        '每個網址只能出現在 dates 或 inaccessible 其中一處。dates 只列能確認日期的網址;其他網址都要在 inaccessible 說明原因。只寫這份 JSON 檔,不要改看板,最後一行印 @@DONE@@。\n'
    )


def parse_result(data, urls):
    if not isinstance(data, dict) or not isinstance(data.get('dates'), list):
        raise ValueError('缺少 dates 清單')
    if not isinstance(data.get('inaccessible', []), list):
        raise ValueError('inaccessible 必須是清單')
    allowed = set(urls)
    dates = {}
    unconfirmed = {}
    for row in data['dates']:
        if not isinstance(row, dict):
            raise ValueError('dates 裡有非物件資料')
        url = str(row.get('url') or '')
        if url not in allowed:
            continue
        if url in dates or url in unconfirmed:
            raise ValueError(f'dates 裡有重複網址:{url}')
        value = str(row.get('posted_at') or '').strip()
        if not value:
            unconfirmed[url] = {
                'url': url,
                'reason': 'agent 沒有提供可確認的刊登日期',
                'need': '',
            }
            continue
        try:
            parsed = datetime.date.fromisoformat(value)
        except ValueError as e:
            raise ValueError(f'刊登日期必須是 YYYY-MM-DD:{url}') from e
        if parsed.isoformat() != value:
            raise ValueError(f'刊登日期必須是 YYYY-MM-DD:{url}')
        dates[url] = {'posted_at': value, 'src': str(row.get('source') or '').strip()[:80]}
    inaccessible_by_url = dict(unconfirmed)
    explicit_inaccessible = set()
    for row in data.get('inaccessible', []):
        if not isinstance(row, dict):
            raise ValueError('inaccessible 裡有非物件資料')
        url = str(row.get('url') or '')
        if url not in allowed:
            continue
        if url in dates or url in explicit_inaccessible:
            raise ValueError(f'網址必須只出現在 dates 或 inaccessible 其中一處:{url}')
        reason = str(row.get('reason') or '').strip()
        need = str(row.get('need') or '').strip()
        if not reason:
            raise ValueError(f'無法讀取的來源缺少原因:{url}')
        inaccessible_by_url[url] = {'url': url, 'reason': reason[:240], 'need': need[:240]}
        explicit_inaccessible.add(url)
    missing = allowed.difference(dates, inaccessible_by_url)
    if missing:
        raise ValueError(f'有網址沒有日期或無法確認說明:{", ".join(sorted(missing))}')
    return dates, list(inaccessible_by_url.values())


def _report(items, board):
    """📣 回報只放要使用者本人處理的(例如要登入)。頁面沒寫日期、抓不到頁面,他什麼都做不了,
    卡上的上架日本來就顯示不明;以前每張都報,一輪找缺回報區就被這些塞滿。"""
    import agent_report
    for item in items:
        if not item.get('need'):
            continue
        agent_report.report(
            '刊登日期', f'無法確認刊登日期:{item["reason"]}',
            need=item['need'], job=item['url'], live=board
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--refresh', action='store_true', help='重新查全部網址')
    ap.add_argument('--board', default=bd.LIVE)
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args(argv)
    board = os.path.abspath(a.board)
    started = time.strftime('%Y-%m-%dT%H:%M:%S')
    cache = {}
    if os.path.exists(CACHE) and not a.refresh:
        try:
            with open(CACHE, encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    if not isinstance(cache, dict):
        cache = {}
    cache = {url: record for url, record in cache.items() if isinstance(record, dict)}
    with open(board, encoding='utf-8') as f:
        parsed = bd.parse(f.read())
    jobs = {j['id']: j for j in parsed['data']['jobs'] if str(j.get('id', '')).startswith('http')}
    # Old card dates came from the agent. Recheck them once so a documented ATS
    # date can replace them; only known provider dates may seed a completed cache.
    if not a.refresh:
        for url, job in jobs.items():
            if (url not in cache and str(job.get('posted_at') or '').strip()
                    and _provider_date(job.get('posted_src'))):
                cache[url] = {'posted_at': job['posted_at'], 'src': job.get('posted_src') or '',
                              'processed': True, 'provider_checked': True, 'date_complete': True}

    def save_cache():
        cache_dir = os.path.dirname(CACHE)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        with open(CACHE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)

    def complete_fetch(record):
        return bool(record.get('provider_checked') or
                    (_provider_date(record.get('src')) and str(record.get('posted_at') or '').strip()))

    pending_cache = [url for url, record in cache.items()
                      if not a.refresh and url in jobs and record.get('provider_checked')
                      and not record.get('date_complete') and record.get('text')]
    fresh = [url for url in jobs if a.refresh or not complete_fetch(cache.get(url, {}))]
    todo = list(dict.fromkeys(pending_cache + fresh))
    if a.limit:
        todo = todo[:a.limit]
    todo_set = set(todo)
    fresh = [url for url in fresh if url in todo_set]
    pending_cache = [url for url in pending_cache if url in todo_set]
    import page_fetch
    from page_fetch import PageResult
    new_agent_pages, inaccessible = {}, []
    for url, fetched in zip(fresh, page_fetch.fetch_many(fresh)):
        record = {'processed': True, 'provider_checked': True,
                  'fetch_status': fetched.status, 'via': fetched.via,
                  'http_status': fetched.http_status, 'date_complete': True}
        if fetched.status == 'closed':
            record['reason'] = f'來源頁直連回 HTTP {fetched.http_status}'
            inaccessible.append({'url': url, 'reason': record['reason'], 'need': ''})
        elif not fetched.readable:
            record['reason'] = '; '.join(fetched.errors) or '沒有可讀頁面文字'
            inaccessible.append({'url': url, 'reason': record['reason'][:240], 'need': ''})
        elif fetched.posted_at:
            record.update(posted_at=fetched.posted_at, src=fetched.posted_source)
        else:
            # Keep the fetched text locally. If an agent run is interrupted, the
            # next run can retry date extraction without making another request.
            record.update(date_complete=False, text=fetched.text)
            new_agent_pages[url] = fetched
        cache[url] = record

    # Reuse an unfinished text extraction without fetching that page again.
    agent_pages = dict(new_agent_pages)
    for url in pending_cache:
        record = cache[url]
        agent_pages[url] = PageResult(url, 'ok', text=str(record['text']),
                                      via=str(record.get('via') or 'cache'),
                                      http_status=int(record.get('http_status') or 0))
    # 頁面上連一行像日期的字都沒有:不用問 agent。只是程式猜的,不能當成「確認沒有日期」:
    # 不標 date_complete(那會讓 write_board_dates 把卡上原本的日期刪掉),只把原文丟掉、不再排隊。
    for url in [u for u, page in agent_pages.items() if not date_lines(page.text)]:
        agent_pages.pop(url)
        record = cache.setdefault(url, {})
        record.pop('text', None)
        record.update(no_date_lines=True, reason='頁面上沒有看得到的日期')
    agent_pages = dict(list(agent_pages.items())[:AGENT_BATCH])
    save_cache()  # persist the completed fetch before the external agent call
    cached_count = sum(1 for url in jobs if complete_fetch(cache.get(url, {})))
    print(f'看板 {len(jobs)} 筆 | 已處理快取 {cached_count} | agent 要讀 {len(agent_pages)}')

    changed = []

    def write_board_dates():
        def update(data, fb):
            for job in data['jobs']:
                record = cache.get(job.get('id')) or {}
                if record.get('posted_at'):
                    if (job.get('posted_at') != record['posted_at'] or
                            job.get('posted_src') != record.get('src')):
                        job['posted_at'] = record['posted_at']
                        if record.get('src'):
                            job['posted_src'] = record['src']
                        else:
                            job.pop('posted_src', None)
                        changed.append(job['id'])
                elif (record.get('date_complete') and job.get('posted_at') and
                      not _provider_date(job.get('posted_src'))):
                    job.pop('posted_at', None)
                    job.pop('posted_src', None)
                    changed.append(job['id'])

        wants_change = any(
            (record.get('posted_at') and (
                jobs.get(url, {}).get('posted_at') != record.get('posted_at') or
                jobs.get(url, {}).get('posted_src') != record.get('src')))
            or (record.get('date_complete') and jobs.get(url, {}).get('posted_at') and
                not _provider_date(jobs.get(url, {}).get('posted_src')))
            for url, record in cache.items() if url in jobs
        )
        if wants_change:
            bd.set_data(update, live=board)

    # Provider dates are mechanical facts. Save them before any unrelated agent
    # work so one page with no textual date cannot block the rest of the board.
    write_board_dates()

    dates, agent_inaccessible = {}, []
    if agent_pages:
        os.makedirs(os.path.dirname(RESULT), exist_ok=True)
        if os.path.exists(RESULT):
            os.remove(RESULT)
        prompt = prompt_for(jobs, agent_pages, RESULT)
        outcome = ar.run(prompt, LOG, cf.HOME, browser_required=False, web=False, board=board)
        if not outcome.ok:
            msg = f'刊登日期 agent 沒完成:{outcome.message()}'
            import agent_report
            agent_report.report('刊登日期', msg, need='在「找新職缺」按「看紀錄」確認後再重跑', live=board)
            print(msg)
            save_cache()
            write_board_dates()
            return 1
        try:
            with open(RESULT, encoding='utf-8') as f:
                dates, agent_inaccessible = parse_result(json.load(f), list(agent_pages))
        except Exception as e:
            msg = f'刊登日期 agent 交件無法使用:{str(e)[:200]}'
            import agent_report
            agent_report.report('刊登日期', msg, need='在「找新職缺」按「看紀錄」確認後再重跑', live=board)
            print(msg)
            save_cache()
            write_board_dates()
            return 1
        for url in agent_pages:
            record = cache[url]
            record['date_complete'] = True
            record.pop('text', None)
            if url in dates:
                record.update(dates[url])
            else:
                item = next((row for row in agent_inaccessible if row['url'] == url), None)
                record['reason'] = (item or {}).get('reason') or '頁面沒有清楚標示刊登日期'
        save_cache()
    inaccessible.extend(agent_inaccessible)
    import agent_report                  # 這一輪跑完了:上一輪「agent 沒完成」這類回報收掉(還讀不到的下一行會再報)
    agent_report.resolve_from('刊登日期', '後來那一輪查刊登日期跑完了', started, live=board)
    _report(inaccessible, board)

    write_board_dates()
    got = sum(1 for url in jobs if cache.get(url, {}).get('posted_at'))
    print(f'寫進看板:{len(changed)} 筆有變動 | 查得到刊登日:{got}/{len(jobs)}')
    if inaccessible:
        print(f'{len(inaccessible)} 個來源讀不到(要你處理的才放進「📣 agent 回報」)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
