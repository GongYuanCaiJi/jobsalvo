#!/usr/bin/env python3
"""查應徵進度:agent 開好信箱與應徵紀錄頁交件,程式在那一刻讀一次工作區、編號交給同一個 agent 引段;
程式照核對過的回音與自己的查過紀錄改卡(行為清單 P 段)。"""
from __future__ import annotations

import argparse
import datetime
import json
import os

import config as cf
from card import iso_day
from delivery_core import set_outcome
from web import cite, listing, seen, snap
from web.brief import EGO, Brief
from web.numbered import Numbered
from web.outside import Real
from web.round import NEEDS_USER_PAGE, End, RoundRecord, continue_with, end_label, run as run_round, stuck
from web.sheet import Field, How, Schema

STATUS = 'reply_status.json'
FROM = '查應徵進度'
TIMEOUT = 3600
UNSURE_WAIT = 3600      # 按下送出後至少等這麼久(確認信來得及到)才判沒送出
# GLOSSARY「回音」的四種:存檔用左邊的代號,交件單與看板用右邊的字
KIND_WORD = {'confirm': '確認信', 'reject': '拒絕', 'interview': '面試或測驗邀請', 'offer': '錄取'}
KIND_OF_WORD = {word: kind for kind, word in KIND_WORD.items()}
KIND_TO_OC = {'reject': 'rej', 'interview': 'iv', 'offer': 'offer'}
NOT_SENT = '查過信箱和可讀的應徵紀錄,沒有送出的證據(沒有確認信、回音或應徵紀錄):判定沒送出,要重填'
UNCLEAR = '送出結果不明'
UNCLEAR_NEED = '點開附的那一頁看是不是這張:是就在卡上按「其實送出了」,不是就按「確認沒送出,可以重送」'
NOTES = Field('website_notes', How.WORDS, '這一輪實際看到、下次有用的網站筆記', many=True, sub=(
    Field('url', How.WORDS, '來源網址', required=True), Field('note', How.WORDS, '下次有用的一句', required=True)))

NAV_SHEET = Schema('progress_navigation', (
    Field('pages', How.WORDS, '每個你開著的分頁是哪個來源', many=True, sub=(
        Field('tab', How.WORDS, '分頁的 Page 標籤(task.page 用的那個)', required=True),
        Field('source', How.WORDS, '來源名稱(照材料寫)', required=True),)),
    Field('needs_user', How.WORDS, '需要使用者本人處理的事:哪個來源、原因、要他做什麼;沒有就空'),
    *NEEDS_USER_PAGE,
    Field('stopped', How.WORDS, '沒做完就停下:停在哪、為什麼'),
    NOTES,
))
CITE_SHEET = Schema('progress_replies', (
    Field('checked', How.WORDS, '你查過的卡片編號', many=True, sub=(
        Field('card', How.KNOWN, '卡片編號', known='cards', required=True),)),
    listing.replies_field(tuple(KIND_WORD.values())),
    Field('unreadable', How.WORDS, '你讀不到的來源與原因', many=True, sub=(
        Field('source', How.KNOWN, '來源名稱(照材料寫)', known='sources', required=True),
        Field('reason', How.WORDS, '你讀不到的原因', required=True),)),
    NOTES,
    Field('needs_user', How.WORDS, '需要使用者本人處理的事;沒有就空'),
    *NEEDS_USER_PAGE,
    Field('stopped', How.WORDS, '沒做完就停下:停在哪、為什麼'),
))


def waiting(fb):
    """已投遞、還沒結束的卡;面試中和 Offer 也要查後續回音。"""
    import delivery_core as ds
    return [u for u, m in fb.items() if ds.app_of(m) == 'sent' and m.get('oc') not in ('rej', 'wd')]


def unsure_cards(fb):
    """送出結果不明、還在「可以投了」的卡:查應徵進度替他查到底送出沒有。"""
    import delivery_core as ds
    return [u for u, m in fb.items() if isinstance(m, dict) and m.get('app') == 'ship' and not m.get('rm')
            and ds.state(m) == 'unsure']


def to_check(fb):
    """這一輪要查的卡:等回音的,加上送出結果不明的。"""
    return waiting(fb) + unsure_cards(fb)


def submit_tried_at(m):
    """送出結果不明的卡是什麼時候按的送出(ISO)。"""
    a = (m or {}).get('apply') or {}
    return (a.get('submit_fail') or {}).get('at') or a.get('at')


def _local_time(value):
    """ISO 時間 → 本機時間(不帶時區),跟 datetime.now() 比得了;讀不懂回 None。"""
    try:
        t = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return t.astimezone().replace(tzinfo=None)


def since_of(m):
    """從哪天起查:上次真的查過那天;沒查過是送出那天;送出結果不明是按送出那天(P-03)。"""
    m = m or {}
    tried = _local_time(submit_tried_at(m))
    return str((m.get('replies') or {}).get('at') or m.get('sent_at') or (tried.date().isoformat() if tried else ''))[:10]


def sources() -> list[dict]:
    """使用者登記的全部來源:信箱,加上每個平台的應徵紀錄頁(P-10)。"""
    import profile_sync
    mail = cf.C['replies']['mail_url']
    return [{'name': '信箱', 'url': mail, 'type': 'email'}] + [
        {'name': item['platform'], 'url': str(item['url']), 'type': 'application_record'}
        for item in profile_sync.application_record_pages()]


def brief_nav(cards: dict, jobs: dict, registered: list[dict]) -> Brief:
    """第一回合:要查的卡(從哪天查起、用哪份履歷寄的、他的名字與信箱)、登記的來源、網站筆記(P-01、P-03)。"""
    import ship
    import website_notes
    facts = cf.C['accept']['facts']
    rows = []
    for url, card in cards.items():
        job = jobs[url]
        resume_id, lang = ship.resolve(job, cards)
        rows.append({'id': url, 'company': job.get('company'), 'target': job.get('target'),
                     'sent_at': card.get('sent_at'), 'since': since_of(card),
                     'resume_name': cf.resume_name(resume_id),
                     'resume_paths': [path] if (path := cf.master(resume_id, lang)) else [],
                     'name': facts['name'], 'email': facts['email']})
    material = {'要查的卡': rows, '來源': registered,
                '使用者寫的網站筆記': website_notes.users_words([row['url'] for row in registered])}
    return Brief('讓程式讀到每張卡的應徵消息來源。交件時程式會讀工作區每個開著的分頁。',
                 (('查詢材料', json.dumps(material, ensure_ascii=False)),), (EGO,),
                 ('不寄信、不回信、不刪信、不改任何設定。', '不按任何送出或撤回應徵。',
                  '不輸入密碼。', '只在這一輪的工作區操作。'), NAV_SHEET)


def brief_cite(urls: list[str], registered: list[dict], read: str) -> Brief:
    """第二回合:程式在交件那一刻讀到的頁與編號材料,照它引段(P-02、P-04)。"""
    return Brief('照程式讀到的頁,寫每張卡的應徵消息。',
                 (('卡片編號', json.dumps(urls, ensure_ascii=False)),
                  ('來源名稱', json.dumps([row['name'] for row in registered], ensure_ascii=False)),
                  ('程式讀到的頁與編號材料', read)),
                 (), ('只引材料裡的段落。',), CITE_SHEET)


def check(cards: list[str], jobs: dict, fb: dict, browser, board) -> tuple[RoundRecord, dict, tuple]:
    """兩個回合:開來源頁、程式讀一次工作區、同一個 agent 引段。任一回合沒交件或交件單壞掉,卡不動(P-14)。
    第三個回兩個回合沒乾淨收尾的原因(round.stuck)。"""
    registered = sources()
    names = frozenset(row['name'] for row in registered)
    first = run_round('progress', brief_nav({url: fb[url] for url in cards}, jobs, registered), browser,
                      timeout_s=TIMEOUT, board=board, read_at_handoff=True)
    if first.end is not End.HANDED or first.sheet.broken is not None:
        return first, {}, stuck(first)
    pages = first.handoff or ()
    material = Numbered()
    seen.number(material, pages)
    second = continue_with(first, brief_cite(cards, registered, snap.render('交件時', pages, material)),
                           browser, timeout_s=TIMEOUT, board=board, materials=(material,),
                           known={'cards': frozenset(cards), 'sources': names})
    trouble = (*stuck(first), *stuck(second))
    if second.end is not End.HANDED or second.sheet.broken is not None:
        return second, {}, trouble
    return second, save(cards, jobs, first, second, registered, board), trouble


REPORT_NEED = '照上面處理好，再按查應徵進度'


def _report(trouble: tuple, stopped: bool, board) -> None:
    """要不要打斷他照 agent_report.settle(#807):沒做完明天照排程重查;他自己停掉的不算一輪。"""
    if stopped:
        return
    import agent_report
    import board_doc
    board_doc.set_fb(lambda data: agent_report.settle(data, FROM, 'progress', trouble, head=FROM, need=REPORT_NEED),
                     live=board, by=FROM)


def save(cards: list[str], jobs: dict, first: RoundRecord, second: RoundRecord, registered: list[dict], board) -> dict:
    """照核對過的回音、程式自己的查過紀錄改卡;查到一半被移除的不碰(P-04–P-17、P-19、P-20)。"""
    import agent_report
    import board_doc
    import website_notes
    pages = first.handoff or ()
    today = datetime.date.today().isoformat()
    started = datetime.datetime.now().isoformat(timespec='seconds')
    labels: dict[str, set[str]] = {}
    for row in first.sheet.rows('pages'):
        labels.setdefault(str(row['source']), set()).add(str(row['tab']))
    unread = {row['name']: listing.unread_rows(cite.source_unread(
        pages, row['name'], labels=labels.get(row['name'], ()),
        url=row['url'] if row['type'] == 'application_record' else '')) for row in registered}
    by_name = {row['name']: row for row in registered}
    said_unread = [{'source': str(row['source']), 'reason': str(row['reason']),
                    'url': by_name[str(row['source'])]['url'], 'by': 'agent'}
                   for row in second.sheet.rows('unreadable') if str(row['source']) in by_name]
    program = [row for rows in unread.values() for row in rows]
    listed = {str(row['card']) for row in second.sheet.rows('checked')}
    items = listing.replies(second.sheet, pages)
    when = {}
    for item in items:
        when[item['id']] = iso_day(item['date']) or today      # 不是一字不差的一天就用今天(P-07)
        item.update(kind=KIND_OF_WORD.get(item['kind'], item['kind']), date=item['date'] or f'{today} (紀錄沒寫)',
                    source_type=by_name.get(item['src'], {}).get('type', ''))      # 不在清單的來源照沒核對到存(P-04)
    records = {item['card']: iso_day(item['date']) for item in items                # 平台應徵紀錄找到的投遞(P-21)
               if not item.get('unverified') and item['source_type'] == 'application_record'}
    letters: dict[str, set[str]] = {}
    for item in items:
        if not item.get('unverified') and item['kind'] in KIND_TO_OC and item['source_type'] == 'email':
            letters.setdefault(item['id'], set()).add(item['card'])
    result = {'checked': [], 'moved': 0, 'new': 0}

    def write(data):
        eligible = set(to_check(data))
        unsure = set(unsure_cards(data))
        for url in cards:
            if url not in eligible:          # 查到一半被移除(P-15)
                continue
            m = data[url]
            replies = m.setdefault('replies', {'items': []})
            held = {listing.reply_id(row): row for row in replies['items'] if isinstance(row, dict)}
            mine = [item for item in items if item['card'] == url]
            new = [dict(item, **({'maybe': sorted(letters[item['id']] - {url})}
                                 if len(letters.get(item['id'], ())) > 1 else {}))
                   for item in mine if item['id'] not in held]
            replies['items'].extend({key: value for key, value in item.items() if key != 'card'} for item in new)
            replay = [row for row in (_verify(held[item['id']], item, when) for item in mine if item['id'] in held) if row]
            result['new'] += len(new)
            replies['unchecked_sources'] = program + said_unread
            checked = url in listed and not program            # 程式這一輪讀到每個來源、agent 列了這張(P-11)
            replies.update({'at': today} if checked else {})
            result['checked'] += [url] if checked else []
            found = url in records and _record(data, url, records[url], today, jobs.get(url))
            if url in unsure and not found:
                _unsure(data, url, new, checked, jobs, started)
            else:
                _outcome(m, new + replay, when, today, result)
                _ghost(m, checked, today, result)
        # 舊回報收掉(P-17);沒查過的來源照實列在每張卡上(unchecked_sources),要不要打斷他由 _report 管(#807)
        agent_report.apply_resolve(data, None, '這一輪重查過了', src=FROM, only=lambda item: 'parts' not in item)
    board_doc.set_fb(write, live=board, by=FROM)
    website_notes.remember([row for sheet in (first.sheet, second.sheet) for row in sheet.rows('website_notes')])
    return dict(result, complete=all(url in result['checked'] for url in cards))


def _verify(held: dict, item: dict, when: dict) -> dict | None:
    """卡上存著沒核對到的那一則,這一輪核對到了:原地補成核對過,id 與他按過的照留(自動改狀態的 by、「不對,復原」還指得到)。
    舊版記著還沒套用、他也沒否決的,回它讓這一輪照它改卡;其他分不出套用過沒,不重放(P-22)。"""
    if not held.get('unverified') or item.get('unverified'):
        return None
    pending = held.get('effect_pending') and not held.get('effect_rejected')
    for key in ('unverified', 'agent_said', 'effect_pending'):
        held.pop(key, None)
    held.update({key: value for key, value in item.items() if key not in ('card', 'id')})
    when[held.get('id')] = when[item['id']]
    return held if pending else None


def _record(data: dict, url: str, day: str, today: str, job) -> bool:
    """平台應徵紀錄上的這一次投遞:記成平台找到的已投遞、投遞日照紀錄(不是一天的用今天,P-07);已投遞的只在紀錄是一天、
    而且跟投遞日不同時改;他退回過的那一筆、不晚於上一次投遞的那一筆是舊的,不拉回(P-21)。回有沒有記。"""
    import delivery_core as ds
    import ship
    m = data[url]
    rec = f'{url}:{day or today}'
    last = max(['', *(str(row.get('sent_at') or '') for row in m.get('tries') or [] if isinstance(row, dict))])
    if rec in map(str, m.get('sync_no') or []) or (last and (not day or day <= last)) or (
            ds.app_of(m) == 'sent' and (not day or day == m.get('sent_at'))):
        return False
    ds.fire(data, url, 'platform_found', by='platform', at=datetime.datetime.now().isoformat(timespec='seconds'),
            sent_at=day or today, rec=rec)
    ship.record_sent(data, url, job)
    return True


def _outcome(m: dict, new: list[dict], when: dict, today: str, result: dict) -> None:
    """核對過、不是「可能是這封」的拒絕、面試、錄取照日期一則一則改卡;程式記的沒下文收到確認信改回等回音(P-06、P-12、P-16)。"""
    sure = [item for item in new if not item.get('unverified') and not item.get('maybe')]
    steps = [(KIND_TO_OC[item['kind']], item) for item in sorted(sure, key=lambda item: when[item['id']])
             if item['kind'] in KIND_TO_OC]
    cur = m.get('oc') or ''
    if not steps and cur == 'ghost' and (m.get('oc_auto') or {}).get('s') == 'ghost':
        steps = [('', item) for item in sure if item['kind'] == 'confirm']
    before = dict(m.get('oc_at') or {})
    for outcome, item in steps:
        set_outcome(m, outcome, when[item['id']])
    now = m.get('oc') or ''
    if now != cur:
        m['oc_auto'] = {'s': now, 'from': cur, 'at': today, 'by': steps[-1][1]['id'],
                        **({'from_at': before} if before else {})}
        result['moved'] += 1


def ghost_days() -> int:
    """送出幾天沒回音算沒下文(使用者設定 replies.ghost_days)。"""
    return int(cf.C['replies']['ghost_days'])


def _ghost(m: dict, checked: bool, today: str, result: dict) -> None:
    """送出滿設定天數、這一輪真的查過、卻沒有任何核對過的回音:標沒下文(P-12)。"""
    days = ghost_days()
    sent = iso_day(str(m.get('sent_at'))[:10]) or today
    quiet = not any(not item.get('unverified') for item in (m.get('replies') or {}).get('items') or [])
    age = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(sent)).days
    if checked and quiet and not m.get('oc') and not m.get('ghost_no') and age >= days:
        m['oc_auto'] = {'s': 'ghost', 'from': '', 'at': today, 'by': f'送出 {age} 天沒有回音'}
        set_outcome(m, 'ghost', today)
        result['moved'] += 1


def _unsure(data: dict, url: str, new: list[dict], checked: bool, jobs: dict, stamp: str) -> None:
    """送出結果不明:核對過的一則就是送出的證據;來源都查過、agent 列了這張、按送出也過了一陣子卻沒有一則,才算沒送出;
    只有核實不了的就回報(P-13)。"""
    import agent_report
    import board_doc
    import delivery_core as ds
    import ship
    proof = [item for item in new if not item.get('unverified')]
    tried = _local_time(submit_tried_at(data[url]))
    now = datetime.datetime.now()
    if proof:
        ds.fire(data, url, 'actually_sent', by='agent', at=stamp, sent_at=(tried or now).date().isoformat(),
                evidence=dict(proof[0], by='reply', at=stamp))
        ship.record_sent(data, url, jobs.get(url))
        agent_report.apply_resolve(data, url, '查應徵進度查到送出的證據,已改成已投出', src=agent_report.FROM_APPLY)
    elif new:
        board_doc.apply_report(data, '查回音', UNCLEAR, need=UNCLEAR_NEED, job=url,
                               url=new[0]['link'] or url)      # 引句核對到就附那一頁,否則附職缺
    elif checked and tried and (now - tried).total_seconds() >= UNSURE_WAIT:      # 讀不懂按送出的時間就算不出等了多久,不判沒送出
        ds.fire(data, url, 'not_sent', at=stamp, issues=[NOT_SENT])
        agent_report.apply_resolve(data, url, '查應徵進度查過了,沒有送出的證據', src=agent_report.FROM_APPLY)


def main(argv=None, browser=None) -> int:
    import board_doc
    import jobrun
    parser = argparse.ArgumentParser()
    parser.add_argument('--board')
    parser.add_argument('--url')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args(argv)
    board = args.board or board_doc.target()
    output = os.environ.get('REPLY_TMP') or cf.TMP
    os.makedirs(output, exist_ok=True)
    status = os.path.join(output, STATUS)
    started = datetime.datetime.now().timestamp()
    doc = board_doc.load(board)
    jobs = {str(row['id']): row for row in doc['data']['jobs']}
    fb = json.loads(doc['fb'])
    cards = [url for url in to_check(fb) if url in jobs and url == (args.url or url)][:args.limit or None]
    jobrun.write(status, {'phase': 'run', 'pid': os.getpid(), 't0': started, 'n': len(cards), 'done': 0})
    record, result, trouble = check(cards, jobs, fb, browser or Real(), board) if cards else (None, {}, ())
    if cards:
        _report(trouble, record.end is End.STOPPED_BY_USER, board)
    finished = bool(result)
    phase = ('nothing' if not cards else 'failed' if not finished else 'done' if result['complete'] else 'incomplete')
    message = ('沒有要查的卡' if not cards else f'這一輪{end_label(record.end)},卡沒有改'
               if not finished else
               f'查了 {len(cards)} 張,查完 {len(result["checked"])} 張;新回音 {result["new"]} 則;改了狀態 {result["moved"]} 張')
    jobrun.write(status, {'phase': phase, 'pid': os.getpid(), 't0': started, 'n': len(cards),
                          'done': len(result.get('checked') or ()), 'msg': message,
                          'finished_at': datetime.datetime.now().timestamp()})
    print(message)
    return 0 if phase in ('nothing', 'done', 'incomplete') else 1


if __name__ == '__main__':
    raise SystemExit(main())
