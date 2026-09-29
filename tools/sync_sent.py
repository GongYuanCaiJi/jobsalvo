"""
平台應徵紀錄對帳:agent 在各平台的應徵紀錄頁看到的職缺(平台+代號,或職缺連結)和日期,
程式只用「平台+職缺代號」配卡。代號怎麼從職缺網址讀出來,每個平台一條規則(POSTING_ID)。

手動對帳:
  uv run python tools/sync_sent.py --records <agent 回傳的 JSON 檔> [--apply]
"""
import sys, os, re, json, argparse, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd
import card

RETAIN_DAYS = 60


# (平台, 從職缺網址讀代號的規則)。沒有列到的平台,agent 回報時給職缺連結也配不到,只列成「看板沒有」。
POSTING_ID = [
    ('104', r'104\.com\.tw/job/([0-9a-z]+)'),
    ('linkedin', r'linkedin\.com/jobs/view/(?:[^/?#]*?-)?(\d+)'),
    ('linkedin', r'linkedin\.com/jobs/.*[?&]currentJobId=(\d+)'),
    ('lever', r'jobs\.(?:eu\.)?lever\.co/[^/?#]+/([0-9a-f-]{36})'),
    ('greenhouse', r'greenhouse\.io/.*?(?:/jobs/|[?&]gh_jid=)(\d+)'),
    ('ashby', r'jobs\.ashbyhq\.com/[^/?#]+/([0-9a-f-]{36})'),
    ('cake', r'cake(?:resume)?\.(?:me|com)/companies/[^/?#]+/jobs/([^/?#]+)'),
    ('yourator', r'yourator\.co/companies/[^/?#]+/jobs/(\d+)'),
]


def posting_key(url):
    """職缺網址 → (平台, 代號);認不出來回 None。"""
    for platform, rx in POSTING_ID:
        m = re.search(rx, url or '', re.I)
        if m:
            return platform, m.group(1).lower()
    return None


def job_id(url):
    key = posting_key(url)
    return key[1] if key else None


def applied_date(value, today):
    if not value:
        return today.isoformat()
    value = str(value).strip()
    try:
        return datetime.date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        pass
    mo, day = map(int, value.split(' ')[0].split('/'))
    year = today.year if datetime.date(today.year, mo, day) <= today else today.year - 1
    return datetime.date(year, mo, day).isoformat()


def _records(records):
    out = []
    for row in records:
        if isinstance(row, str):
            row = {'id': row}
        if not isinstance(row, dict):
            continue
        # 有職缺連結就照連結讀;只有代號時要有平台(舊的回報格式只有 104 代號,沒寫平台就當 104)
        key = posting_key(str(row.get('url') or ''))
        if not key:
            jid = str(row.get('id') or '').strip().lower()
            key = (str(row.get('platform') or '104').strip().lower(), jid) if jid else None
        if key:
            out.append({'platform': key[0], 'id': key[1], 'applied_at': row.get('applied_at') or '',
                        'title': str(row.get('title') or '')})
    return out


def plan(records, fb, jobs, today):
    """只用職缺代號配卡;日期僅用來避免把舊一輪的記錄誤當成再次投遞。"""
    records = _records(records)
    by_id = {posting_key(j['id']): j for j in jobs if posting_key(j['id'])}
    record_ids = {(r['platform'], r['id']) for r in records}
    record_platforms = {r['platform'] for r in records}
    to_mark, unknown = [], []
    for record in records:
        job = by_id.get((record['platform'], record['id']))
        if not job:
            unknown.append(record)
            continue
        current = fb.get(job['id']) or {}
        if current.get('app') == 'sent':
            continue
        tries = current.get('tries') or []
        last = max((str(t.get('sent_at') or '') for t in tries), default='')
        if last:
            # 沒有日期就無法區分上次重投紀錄,不冒險替這次標已投遞。
            if not record['applied_at'] or applied_date(record['applied_at'], today) <= last:
                continue
        to_mark.append((job, record))

    suspect = []
    for key, job in by_id.items():
        entry = fb.get(job['id']) or {}
        # 只對這次有讀到應徵紀錄的平台懷疑:沒讀到的平台,沒回報不代表沒投
        if key[0] not in record_platforms:
            continue
        if entry.get('app') == 'sent' and key not in record_ids and entry.get('sent_at'):
            if (today - datetime.date.fromisoformat(entry['sent_at'])).days <= RETAIN_DAYS:
                suspect.append((job, entry))
    return to_mark, unknown, suspect


def mark(board, to_mark, today):
    """把 agent 報告的職缺代號配到的卡標成已投遞。"""
    def mut(fb):
        for job, record in to_mark:
            entry = fb.setdefault(job['id'], {})
            entry.pop('rm', None)
            entry['app'] = 'sent'
            entry['sent_at'] = applied_date(record.get('applied_at'), today)
            # 跟手動「📮 我已在外部送出」、代投送出成功一樣:投出去的表單鎖住,不再被當成還沒送出、等他重寫的表單
            if isinstance(entry.get('form'), dict):
                entry['form']['lock'] = 1
    bd.set_fb(mut, live=board, by='平台應徵紀錄對帳')


def sync(board, records):
    """將 agent 回傳的職缺代號對到卡片並更新投遞狀態。"""
    today = datetime.date.today()
    records = _records(records)
    if not records:
        return ''
    with open(board, encoding='utf-8') as f:
        p = bd.parse(f.read())
    fb = json.loads(p['fb'])
    to_mark, _, _ = plan(records, fb, p['data']['jobs'], today)
    if not to_mark:
        return ''
    mark(board, to_mark, today)
    # 點名是哪幾張;原本在「🗑 已移除」的特別講:平台上真的投過了所以放回已投出,他才不會以為移除沒生效
    names = [card.name(job)[:24] for job, _ in to_mark]
    back = [card.name(job)[:24] for job, _ in to_mark if (fb.get(job['id']) or {}).get('rm')]
    return (f'平台應徵紀錄裡有 {len(to_mark)} 張看板還沒標,已標成已投遞:' + '、'.join(names)
            + (f'(其中 {"、".join(back)} 原本在「🗑 已移除」,平台上真的投過了,放回「已投出」)' if back else ''))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--records', required=True, help='agent 回傳的應徵紀錄 JSON 檔')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--board', default=bd.LIVE)
    a = ap.parse_args(argv)
    today = datetime.date.today()
    with open(a.records, encoding='utf-8') as f:
        data = json.load(f)
    records = data.get('job_ids', []) if isinstance(data, dict) else data
    if not records:
        sys.exit('沒有職缺代號,不動看板。')
    p = bd.parse(open(a.board, encoding='utf-8').read())
    to_mark, unknown, suspect = plan(records, json.loads(p['fb']), p['data']['jobs'], today)
    print(f'agent 回報 {len(_records(records))} 筆應徵紀錄')
    for job, record in to_mark:
        print(f'  + 標成已投遞:{record["platform"]} {record["id"]}  {record["title"]}  ({record["applied_at"] or "日期未提供"})  看板:{job["target"][:30]}')
    for record in unknown:
        print(f'  ? 看板沒有這個職缺:{record["platform"]} {record["id"]}  {record["title"]}')
    for job, entry in suspect:
        print(f'  ! 看板標已投遞但 agent 沒回報這個代號:{job_id(job["id"])}  {job["target"][:30]}  (標於 {entry["sent_at"]})')
    if not (to_mark or unknown or suspect):
        print('  全部對齊。')
    if a.apply and to_mark:
        mark(a.board, to_mark, today)
        print(f'已寫入看板 {len(to_mark)} 張。')
    elif to_mark:
        print('(只報告,沒寫入;加 --apply 才會寫)')
    return 1 if suspect else 0


if __name__ == '__main__':
    raise SystemExit(main())
