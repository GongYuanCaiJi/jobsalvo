#!/usr/bin/env python3
"""同步平台履歷:把原始履歷這次改的字交給 agent 存進一份登記的平台履歷;agent 叫拍改前改後,程式比對、照實列出。

行為清單 S 段。跟上只在使用者按了最新一版「看過了,沒問題」之後(S-10–S-13);恢復也是同一種一輪(S-15–S-20)。
"""
from __future__ import annotations

import argparse
import json
import os
import time

import profile_sync
from profile_sync import Slot, slot_of, standing, target
import sync_material
import sync_records
from web import listing
from web.brief import AFTER, BEFORE, EGO, SHELL, Brief
from web.changelist import Intended, build, restore_intents
from web.marks import say
from web.outside import Real
from web.round import NEEDS_USER_PAGE, End, RoundRecord, record_unstarted, run as run_round, stuck
from web.sheet import Field, How, Schema

TIMEOUT = 40 * 60
GOAL = '把改動存進平台履歷。'
RESTORE_GOAL = '把使用者選的頁面內容恢復成清單上的改前文字,存進平台履歷。'

SHEET = Schema('sync', (
    Field('changes', How.WORDS, '你改了哪些地方,每項寫項目、改前的字、改後的字', many=True, sub=(
        Field('item', How.WORDS, '項目'),
        Field('before', How.WORDS, '改前的字'),
        Field('after', How.WORDS, '改後的字'),
    )),
    Field('rewrites', How.WORDS, '平台放不下原字、你改寫了的內容', many=True, sub=(
        Field('original', How.WORDS, '原字'),
        Field('rewritten', How.WORDS, '你寫上去的字'),
        Field('why', How.WORDS, '原因'),
    )),
    Field('registered', How.WORDS, '這一輪你在平台上新登記的這份平台履歷:{"name": 名稱, "url": 網址};沒有就空'),
    Field('saved', How.WORDS, '你有沒有存好、怎麼確認的'),
    Field('needs_user', How.WORDS, '需要使用者本人處理的事及原因'),
    *NEEDS_USER_PAGE,
    Field('stopped', How.WORDS, '沒做完就停下的話,停在哪、為什麼'),
))


def slots(platform=None, lang=None, variant=None) -> list[Slot]:
    return [slot_of(p, l, v, entry) for p, l, v, entry in profile_sync.slots(platform)
            if (lang is None or l == lang) and (variant is None or v == variant)]


def intended(slot: Slot, source: dict[str, str]) -> tuple[Intended, ...]:
    """這次要改的:跟最後一版使用者按過「看過了」的比;沒按過就全部再送(S-11)。"""
    versions = sync_records.versions_for(slot.platform, slot.lang, slot.variant)
    prior = next((row.get('target_text') or {} for row in reversed(versions) if row.get('reviewed_at')), {})
    keys = (set(prior) | set(source)) - {'__target_fingerprint__'}
    return tuple(Intended(key, prior.get(key), source.get(key)) for key in sorted(keys)
                 if prior.get(key) != source.get(key))


def brief(slot: Slot, intents: tuple[Intended, ...], goal: str = GOAL) -> Brief:
    """目標、材料(這次要改什麼、哪一份平台履歷與網址、附件、網站筆記)、工具、界線、交件單(S-02、S-03)。"""
    import website_notes
    attachments = sync_material.slot_attachments(slot.lang, slot.variant) if slot.kind != '附件' else ()
    materials = (
        ('平台履歷', f'平台:{slot.platform}\n網址:{slot.url}\n版本:{slot.lang}/{slot.variant}'),
        ('這次要改的', '\n'.join(f'{row.item}\n改前:{row.before or "(沒有)"}\n改後:{row.after or "(刪除)"}'
                             for row in intents)),
        ('附件', '\n'.join(f'{name}: {path}' for name, path in attachments)),
        ('使用者寫的網站筆記', json.dumps(website_notes.users_words([slot.url]), ensure_ascii=False)),
    )
    limits = ('只動材料列出的這份平台履歷。', '不輸入密碼。', '原稿和附件不改。')
    return Brief(goal, materials, (EGO, BEFORE, AFTER, SHELL), limits, SHEET)


def one_round(slot: Slot, intents: tuple[Intended, ...], source: dict[str, str], browser, *, board, task: str,
              goal: str = GOAL) -> RoundRecord:
    """跑一輪、記下這一版清單(要按看過才跟上)、存交件單上使用者要接手的東西。沒有要改的就不派 agent(S-04)。"""
    what = brief(slot, intents, goal)
    if intents:
        record = run_round('sync', what, browser, timeout_s=TIMEOUT, board=board)
    else:
        record = record_unstarted(what, 'sync', board, say('NOTHING_TO_SYNC').text)
    sync_records.remember_version(slot.platform, slot.lang, slot.variant, record.id, target(source), source)
    visual = listing.visual(build(record, intents), record, intents, task=task)
    sync_records.remember_visuals(slot.platform, slot.lang, slot.variant, [visual])
    if record.end is End.HANDED:
        _registered(slot, record)
        _rewrites(slot, record, board)
    return record


REPORT_FROM = '平台履歷同步'
REPORT_NEED = '照上面處理好，再按全部同步'


def _report(slot: Slot, record: RoundRecord, board) -> None:
    """要不要打斷他照 agent_report.settle(#807);這一份的清單照實列出每個原因,下次按全部同步重做。他自己停掉的不算一輪。"""
    if record.end is End.STOPPED_BY_USER:
        return
    import agent_report
    import board_doc
    board_doc.set_fb(lambda fb: agent_report.settle(
        fb, REPORT_FROM, f'sync:{slot.platform}/{slot.lang}/{slot.variant}', stuck(record), url=slot.url,
        head=f'{slot.platform} {slot.lang}/{slot.variant} 平台履歷同步', need=REPORT_NEED), live=board, by='sync')


def _registered(slot: Slot, record: RoundRecord) -> None:
    """agent 登記的平台履歷:記下並標明 agent 登記的,網域清單不動(S-22)。"""
    said = record.sheet.said('registered')
    if isinstance(said, dict) and isinstance(said.get('url'), str) and said['url']:
        profile_sync.remember(slot.platform, slot.lang, slot.variant, said['url'], by='agent',
                              name=str(said.get('name') or ''))


def _rewrites(slot: Slot, record: RoundRecord, board) -> None:
    """agent 改寫了的內容照常用答案規則記成待你確認的代填答案(S-09)。"""
    said = [row for row in record.sheet.rows('rewrites') if isinstance(row['rewritten'], str) and row['rewritten']]
    if not said:
        return
    import board_doc
    import form_record

    def change(fb):
        for row in said:
            form_record.add_inferred(fb, str(row['original']), row['rewritten'], str(row['why']),
                                     platform=slot.platform, profile=f'{slot.lang}/{slot.variant}')
    board_doc.set_fb(change, live=board, by='sync')


def restore(payload: dict, browser, *, board) -> RoundRecord:
    """把使用者選的那幾處恢復成清單上的改前文字:只認畫面上那一版清單,選的要在那一版裡(S-15)。"""
    import evidence
    found = slots(*(str(payload[name]) for name in ('platform', 'lang', 'variant')))
    if not found:
        raise ValueError('平台履歷未登記')
    slot = found[0]
    version = str(payload['version'])
    if sync_records.version(sync_records.where(slot.platform, slot.lang, slot.variant)) != version:
        raise ValueError('同步清單已更新,請重新載入')
    directory, fixed = evidence.flow_round_dir('sync', version), ''
    if directory is None:       # 填表發的那一版(F-11)
        directory = evidence.flow_round_dir('fill', version, board)
        fixed = sync_records.where(slot.platform, slot.lang, slot.variant)['read']
    intents = restore_intents(directory, tuple(payload['change_ids']), fixed)
    return one_round(slot, intents, sync_material.source(slot, board), browser, board=board, task='restore',
                     goal=RESTORE_GOAL)


def main(argv=None, browser=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board')
    parser.add_argument('--platform')
    parser.add_argument('--lang')
    parser.add_argument('--variant')
    parser.add_argument('--restore-json')
    args = parser.parse_args(argv)
    browser = browser or Real()
    output = os.environ['SYNC_TMP']
    os.makedirs(output, exist_ok=True)
    started, rows = time.time(), {}

    def status(phase, total):
        import jobrun
        jobrun.write(os.path.join(output, 'sync_status.json'), {
            'phase': phase, 'pid': os.getpid(), 't0': started, 'n': total, 'done': len(rows),
            'msg': '原始履歷沒有可同步的文字變更，沒派 agent' if phase == 'nothing' else f'已處理 {len(rows)}/{total} 份',
            'results': [{'platform': key.partition(':')[0], 'lang': key.partition(':')[2].partition('/')[0],
                         'variant': key.partition('/')[2], 'ok': ok, 'msg': msg[:200]}
                        for key, (ok, msg) in rows.items()],
            **({'finished_at': time.time()} if phase in ('done', 'nothing') else {})})

    def record(key, run):
        try:
            done = run()
            rows[key] = (True, '這一輪沒有啟動' if done.end is End.NOT_STARTED else '')
        except (OSError, RuntimeError, ValueError) as error:
            import evidence
            evidence.Round('sync', 'error', board=args.board).note('program_error', reason=str(error))
            safe_restore_messages = {
                '同步清單已更新,請重新載入',
                '恢復清單已更新,請重新載入後選取',
                '平台履歷未登記',
            }
            rows[key] = (False, str(error) if str(error) in safe_restore_messages else '這一輪沒完成')

    if args.restore_json:
        status('agent', 1)
        record('restore', lambda: restore(json.loads(args.restore_json), browser, board=args.board))
        status('done', 1)
        return 0 if all(ok for ok, _msg in rows.values()) else 1

    chosen = slots(args.platform, args.lang, args.variant)
    if args.platform and not chosen:
        import evidence
        evidence.Round('sync', 'error', board=args.board).note('program_error', reason='平台履歷未登記')
        rows[f'{args.platform}:{args.lang or ""}/{args.variant or ""}'] = (False, '平台履歷未登記')
        status('done', 1)
        return 1
    todo = [slot for slot in chosen if standing(slot, args.board).code != 'STANDING_OK']
    status('agent' if todo else 'nothing', len(todo))
    for slot in todo:
        def run(slot=slot):
            source = sync_material.source(slot, args.board)
            intents = intended(slot, source)
            done = one_round(slot, intents, source, browser, board=args.board, task='sync')
            if intents:     # 恢復也走 one_round,它的結果不收、不開同步的回報
                _report(slot, done, args.board)
            return done
        record(slot.key, run)
        status('agent', len(todo))
    status('done' if todo else 'nothing', len(todo))
    return 0 if all(ok for ok, _msg in rows.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
