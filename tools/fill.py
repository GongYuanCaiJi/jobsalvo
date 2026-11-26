#!/usr/bin/env python3
"""幫你填表:填表、修改、送出各跑一輪 agent;確認送出畫面打開時拍一次那張卡的工作區,跟填表最後一次拍改後比。

行為清單 F 段。程式只拍照、比對、核對 agent 引的字;卡停哪一格照 agent 交件單的判斷欄與這一輪怎麼結束,查投遞狀態表。
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import math
import os
import pathlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import card_title
import config as cf
from web import cite, compare, listing, snap
from web.brief import AFTER, BEFORE, EGO, FORM_PLAN, SHELL, Brief
from web.changelist import Intended, build, since
from web.marks import Source, joined, say
from web.outside import Real
from web.round import NEEDS_USER_PAGE, End, RoundRecord, run as run_round, stuck
from web.sheet import Accept, Cell, Field, How, Schema, Verdict, quote

TIMEOUT = 40 * 60
OTHER_JOB = 'agent 說這一頁不是卡片上那個職缺'
FILL_NEED = '照上面處理好，再按「幫你填表」'      # 看板照 GLOSSARY 換成卡上那顆鈕的名字(board.js ibText)
START = {'fill': 'fill_start', 'fix': 'fix_start'}      # 送出的開始事件由 form_record.start_submit 送(run)
LINE = Accept(kinds=('line',), owner=None, value=None)

FILL_SHEET = Schema('fill', (
    Field('posting', How.CITE, '這一頁的職缺名稱:引頁上的段落', accept=LINE),
    Field('company', How.CITE, '這一頁的公司名稱:引頁上的段落', accept=LINE),
    Field('form_tab', How.WORDS, '你填表的那個分頁:照拍照材料印的分頁編號原樣寫(例如 7:p1)', required=True),
    Field('same_job', How.ENUM, '這一頁是不是卡片上那個職缺', one_of=('true', 'false'), required=True),
    Field('delivery', How.ENUM, '你怎麼處理履歷或附件',
          one_of=('direct_upload', 'no_profile', 'platform_profile'), required=True),
    Field('profile', How.KNOWN, '你選的平台履歷名稱(照材料裡的名稱寫)', known='profiles'),
    Field('profile_registration', How.WORDS, '這次第一次提到的平台固定履歷:{"name": 名稱, "url": 網址};沒有就空'),
    Field('application_history_url', How.WORDS, '可讀的應徵紀錄頁網址(有就填)'),
    Field('fields', How.WORDS, '你填的每一格:欄位、你填的值、值的出處(答案庫哪一題/履歷哪一段)', many=True, sub=(
        Field('q', How.WORDS, '頁面上的欄位題目'),
        Field('src', How.ENUM, '值的出處種類:rz 是履歷直接對上的、skip 是刻意不填(why 寫為什麼)、'
              'bank 是答案(答案庫的或這次新寫的)', one_of=('rz', 'skip', 'bank')),
        Field('value', How.WORDS, '頁面上實際填的值'), Field('k', How.WORDS, '答案鍵(適用時)'),
        Field('bank_q', How.WORDS, '答案庫原本題目(適用時)'), Field('why', How.WORDS, '答案理由(新答案時)'),
        Field('zh', How.WORDS, '中文翻譯(新答案時)'), Field('kind', How.WORDS, '答案類型(新答案時)'),
        Field('pj', How.WORDS, '是否為這缺專用'), Field('pjw', How.WORDS, '這缺專用的理由'),
        Field('choice', How.WORDS, '選項原文(適用時)'))),
    Field('uploaded', How.KNOWN, '你上傳的檔名', known='files', many=True, sub=(
        Field('name', How.KNOWN, '檔名', known='files'),)),
    listing.UNMET,
    Field('problems', How.WORDS, '填不了或沒把握的格子與原因', many=True, sub=(
        Field('text', How.WORDS, '格子與原因'),)),
    Field('website_notes', How.WORDS, '本輪實際瀏覽的網站筆記:網址與下次有用的一句', many=True, sub=(
        Field('url', How.WORDS, '本輪實際瀏覽的網址'), Field('note', How.WORDS, '下次可參考的一句筆記'))),
    Field('needs_user', How.WORDS, '需要使用者本人處理的事,沒有就空'),
    *NEEDS_USER_PAGE,
    Field('stopped', How.WORDS, '沒做完就停下的話:停在哪、為什麼'),
    Field('submitted', How.ENUM, '這一輪有沒有按送出', one_of=('false',), required=True),
))
SUBMIT_SHEET = Schema('submit', (
    Field('submitted', How.ENUM, '你按了送出沒有', one_of=('true', 'false'), required=True),
    Field('evidence', How.CITE, '送出之後頁上的段落或沒送出的原因段落', accept=LINE),
    Field('needs_user', How.WORDS, '需要使用者本人處理的事,沒有就空'),
    Field('stopped', How.WORDS, '沒做完就停下的話:停在哪、為什麼'),
))
KEEP = ('stale', 'submit_fail')    # 換過履歷、送出結果不明:寫回這一輪時記號不消失(F-23)
FIELD_KEYS = ('q', 'src', 'value', 'k', 'bank_q', 'why', 'zh', 'kind', 'pj', 'pjw', 'choice')


def _load(board):
    import board_doc
    doc = board_doc.load(board)
    return {row['id']: row for row in doc['data']['jobs']}, json.loads(doc['fb'])


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='microseconds')


def round_apply(stage: str, **fields) -> dict:
    """填表、修改這一輪寫進卡上的 apply 紀錄:哪一種(stage)、哪一輪(at),其他照給的。副本的假流程(job_fake)也用這一支。
    自動重填的記號、確認綁哪一輪、交回來的回報是不是這一輪之後按的,都讀 at。"""
    return {'stage': stage, 'at': _now(), 'issues': [], **fields}


def brief(stage: str, card_id: str, job: dict, fb: dict, note: str) -> Brief:
    """目標、材料(職缺、卡片上的職缺名稱、要寄的檔、常用答案、網站筆記、修改說明、使用者填表規則)、工具、界線、交件單(F-02)。"""
    import card
    import form_record
    import profile_sync
    import ship
    import website_notes
    documents = ship.documents(job, fb)
    here = profile_sync.profile_key(card_id)
    others = [f'- {platform} {lang}/{variant}: {entry["read"]}' for platform, lang, variant, entry in profile_sync.slots()
              if platform != here and not profile_sync.is_file_url(entry['read'])]
    rules = pathlib.Path(cf.APPLY_RULES).read_text(encoding='utf-8').strip() if os.path.isfile(cf.APPLY_RULES) else ''
    materials = (
        ('職缺網址', card_id),
        ('卡片上的職缺', card.name(job)),
        ('要送的檔案', '\n'.join(str(x.get('send_name') or x.get('name')) for x in documents)),
        ('履歷與附件材料檔案', '\n'.join(f'- {x.get("send_name") or x.get("name")}: {x.get("effective_path")}'
                                 for x in documents if x.get('effective_path')) or '(沒有可讀檔案)'),
        ('答案與履歷材料', form_record.shared_text(fb)),
        ('平台履歷名稱', str(profile_sync.decided(job, fb, card_id).get('name') or '')),
        ('其他已登記的平台履歷頁', '\n'.join(others) or '(沒有)'),
        ('待重翻的答案', json.dumps(form_record.to_translate(fb, card_id), ensure_ascii=False)),
        ('使用者寫的網站筆記', json.dumps(website_notes.users_words([card_id]), ensure_ascii=False)),
        ('修改說明', note),
        ('使用者填表規則', rules or '(沒有另外交代)'),
    )
    limits = ('依材料與使用者答案填表。只讀材料列出的履歷、附件與平台履歷；不新增、替換或改寫個人資料。',
              '直接上傳只能用這張卡列出的個別檔案整套，或合併版單一檔案，不混用；不自行產檔、合併或另找檔案。',
              '不輸入密碼。',
              'fill 與 fix 不按送出; submit 只在使用者確認後送出。',
              '填表交件要說明實際改了哪些欄位;不確定或未完成時照實交件。')
    return Brief('依使用者確認送出這張申請。' if stage == 'submit' else '完成這張卡的申請表',
                 materials, (EGO, BEFORE, AFTER, SHELL, FORM_PLAN), limits,
                 SUBMIT_SHEET if stage == 'submit' else FILL_SHEET)


def eligible(jobs: dict, fb: dict, stage: str, url: str | None, note: str, status) -> list[str]:
    """一輪挑哪幾張卡只照投遞狀態表;修改要有修改說明、要翻譯或待重打的答案;可投遞夾沒照卡上的選擇建好的不填(F-29)。"""
    import delivery_core as ds
    import form_record
    import ship
    selected = []
    for card_id, card in fb.items():
        if card_id not in jobs or (url and card_id != url):
            continue
        apply = card.get('apply') or {}
        ready = {
            'fill': lambda: ds.allowed(card, 'fill_start') and not ship.chosen_not_built(jobs[card_id], fb),
            'fix': lambda: (ds.allowed(card, 'fix_start') and bool(apply.get('session'))
                            and not ship.chosen_not_built(jobs[card_id], fb)
                            and bool(note.strip() or form_record.to_translate(fb, card_id)
                                     or any(row.get('refill') for row in (card.get('form') or {}).get('f', [])))),
            'submit': lambda: form_record.approval_problem(fb, card_id, status) is None,
        }[stage]
        if ready():
            selected.append(card_id)
    return selected


def interpret(record: RoundRecord) -> str:
    """填表這一輪的事件:只照這一輪怎麼結束與交件單的判斷欄(A-21、F-17、F-22、F-28)。"""
    sheet = record.sheet
    complete = (sheet.broken is None and sheet.said('same_job') == 'true' and sheet.ok('delivery')
                and sheet.ok('submitted'))
    trouble = any((sheet.rows('problems'), sheet.rows('unmet'), sheet.said('needs_user'), sheet.said('stopped')))
    return ('fill_nopage' if record.end is not End.HANDED else
            'fill_unsure' if sheet.said('submitted') == 'true' else
            'fill_ok' if complete and not trouble else 'fill_bad')


def parallel(flow) -> int:
    """同時填幾張(flow.fill_parallel):空的、看不懂的照預設 3;小數無條件捨去;夾在 1 到 10 之間。"""
    try:
        n = float(str((flow or {}).get('fill_parallel')).strip())
    except ValueError:
        return 3
    return min(10, max(1, math.trunc(n))) if math.isfinite(n) else 3


def run(stage: str, card_id: str, browser, board, note: str, approval_id) -> RoundRecord | str:
    """一張卡跑一輪:開始事件、派 agent(修改、送出接著同一個工作區與對話)、照結果存回卡上。
    送出的開始事件跟確認檢查在同一次寫入(form_record.start_submit);那一次確認作廢就不派 agent,回原因。"""
    import board_doc
    import delivery_core as ds
    import form_record
    jobs, fb = _load(board)
    job, prior = jobs[card_id], (fb.get(card_id) or {}).get('apply') or {}
    what = brief(stage, card_id, job, fb, note)
    checked = {e.get('k'): e.get('v') for e in fb.get('__ans__') or []}
    again = stage != 'fill'
    browser = browser or (Real.for_workspace(int(prior['workspace']['id']), prior['workspace']['name']) if again else Real())
    why = [None]

    def start(data):
        if stage == 'submit':
            why[0] = form_record.start_submit(data, card_id, form_record.board_status(board), approval_id)
        else:
            ds.fire(data, card_id, START[stage])
    board_doc.set_fb(start, live=board, by='fill')
    if why[0]:
        return why[0]

    def remember(binding):
        board_doc.set_fb(lambda data: ds.fire(data, card_id, 'tab_handed', apply={'workspace': binding}),
                         live=board, by='fill')
    names = {
        'profiles': frozenset(x for x in (dict(what.materials)['平台履歷名稱'],) if x),
        'files': frozenset(x for x in dict(what.materials)['要送的檔案'].splitlines() if x),
    }
    record = run_round('fill', what, browser,
                       timeout_s=TIMEOUT, board=board, known=names,
                       resume=prior.get('session') if again else None,
                       agent_id=prior.get('agent_id') if again else None,
                       resume_runtime=prior.get('runtime'),    # 只在接回對話時看
                       workspace=prior.get('workspace') if again else None,
                       on_workspace=None if again else remember)
    if stage == 'submit':
        _save_submit(stage, card_id, record, board, job=job, checked=checked)
    else:
        _save_fill(stage, card_id, record, board, job=job, checked=checked, browser=browser)
    return record


def _intents(record: RoundRecord) -> tuple[Intended, ...]:
    """每一格填的值、每一個上傳的檔名:照 agent 寫的原字,交給核心照實標在頁上一字不差有沒有(F-05、F-15)。"""
    fields = tuple(Intended(joined(row['q']), None, joined(row['value'])) for row in record.sheet.rows('fields'))
    uploads = tuple(Intended(f'上傳檔:{joined(row["name"])}', None, joined(row['name']))
                    for row in record.sheet.rows('uploaded'))
    return fields + uploads


def _save_fill(stage: str, card_id: str, record: RoundRecord, board, *, job: dict, checked: dict, browser) -> None:
    """checked:這一輪開始時答案庫每條的值;這一輪跑的時候他又改的答案,頁上還是舊字,重打標記留著(F-21)。"""
    import agent_report
    import board_doc
    import delivery_core as ds
    import form_record
    import profile_sync
    import ship
    import website_notes
    _jobs, fb = _load(board)
    previous = (fb.get(card_id) or {}).get('apply') or {}
    sheet = record.sheet
    tab, shot = snap.form_page(record.afters, sheet.said('form_tab'))    # 表單那一頁 = agent 說的、核對在拍改後裡的分頁,不猜不沿用(F-20)
    workspace = {key: value for key, value in (previous.get('workspace') or {}).items()
                 if key != 'handoff_page'}
    workspace.update({'page': tab.partition(':')[2]},       # 收尾照這一頁放掉(chrome_door);不知道是哪一頁就是空的
                     **({'handoff_page': record.handoff_page} if record.handoff_page else {}))
    apply = round_apply(stage, session=record.session, round_id=record.id, workspace=workspace, tab_id=tab,
                        agent_id=record.agent_id, runtime=record.runtime, evidence=record.id,
                        **({'ev': shot} if shot else {}), **{key: previous[key] for key in KEEP if previous.get(key)})
    decision = profile_sync.decided(job, fb, card_id)
    method = sheet.said('delivery')
    apply['delivery'] = {'method': method, 'profile_kind': decision['profile_kind'],
                         **({'profile_url': decision['fixed_url']}
                            if method == 'platform_profile' and decision['profile_kind'] == 'fixed'
                            and decision.get('fixed_url') else {})}
    apply['profile'] = joined(sheet.said('profile'))
    apply['uploaded'] = [joined(row['name']) for row in sheet.rows('uploaded')]
    unmet = listing.unmet(sheet)
    apply['unmet'] = unmet
    apply['problems'] = [joined(row['text']) for row in sheet.rows('problems')]
    apply['needs_user'], apply['stopped'] = joined(sheet.said('needs_user')), joined(sheet.said('stopped'))
    trouble = stuck(record)
    ended = [mark.text for mark in trouble if mark.code in ('ENDED', 'NOT_STARTED', 'SHEET_BROKEN')]
    agent_stops = [mark.text for mark in trouble
                   if mark.code in ('AGENT_STOPPED', 'NEEDS_USER', 'HANDED_OVER',
                                    'HANDED_QUOTE_UNMATCHED', 'HANDOFF_UNVERIFIED')]
    apply['issues'] = [text for text in (*ended, *apply['problems'], *agent_stops,
                                         OTHER_JOB if sheet.said('same_job') == 'false' else '') if text]
    intents = _intents(record)
    changes = build(record, intents)
    if decision['profile_kind'] == 'fixed' and decision.get('fixed_url'):
        _publish_fixed(decision, record, intents, card_id)
    said_by_question = {joined(row['q']): joined(row['value']) for row in sheet.rows('fields')}
    said_by_question.update({f'上傳檔:{joined(row["name"])}': joined(row['name']) for row in sheet.rows('uploaded')})
    observations = []
    for index, row in enumerate(changes.intended, 1):
        observation = {'q': row.item, 'code': row.mark.code, 'marker': row.mark.text}
        if row.mark.code in ('NOT_FOUND_VERBATIM', 'SAID_VS_ACTUAL'):
            observation['said'] = said_by_question.get(row.item, '')
            name = f'field-{index}.png'
            path = os.path.join(record.evidence_dir, name)
            try:
                if tab:
                    from web.snap import field_shot
                    capture = field_shot(browser, tab, path, row.item)
                else:
                    raise OSError('這一輪沒有可截圖的那一頁')
            except Exception as error:  # noqa: BLE001 - screenshot failure falls back to the existing whole-page shot
                observation['shot'] = shot
                observation['shot_cropped'] = False
                import evidence as evidence_store
                evidence_store.record_external([record.evidence_dir], 'field_shot_failed', question=row.item,
                                               reason=str(error))
            else:
                observation['shot'] = f'{record.id}/{name}'
                observation['shot_cropped'] = capture['cropped']
                import evidence as evidence_store
                evidence_store.record_external([record.evidence_dir], 'field_shot', file=name, tab=tab,
                                               question=row.item, cropped=observation['shot_cropped'])
        observations.append(observation)
    apply['field_observations'] = observations
    apply['fill_changes'] = listing.report(
        changes, tuple(page for snapshot in (*record.befores, *record.afters) for page in snapshot.pages),
        since_fill=False)
    if not tab:      # 不猜是哪一頁,照實寫在這一輪的頁面紀錄上(F-20)
        apply['fill_changes']['marks'].append(listing.mark_row(say('FORM_PAGE_UNKNOWN')))
    event = interpret(record)
    rows = [{key: text for key in FIELD_KEYS if (text := joined(row.get(key))) or key == 'value'}
            for row in sheet.rows('fields')]
    evidence = {'at': _now(), 'said': sheet.said('submitted'), 'needs_user': apply['needs_user'],
                'stopped': apply['stopped']}

    def record_form(data):
        """這一輪的表單記進卡上、新答案附那一刻拍到有那一題的頁、這一輪跑時他改了的答案標待重打(F-08、F-21、F-25)。"""
        known = {e.get('k') for e in data.get('__ans__') or []}
        new = form_record.apply_record(data, card_id, profile_sync.profile_key(card_id),
                                       form_record.fields_from_fill({'fields': rows}, known))
        form_record.retranslate(data, rows)
        for entry in data.get('__ans__') or []:
            where = cite.shot_showing(record.afters, entry['q']) if entry['k'] in new else None
            entry.update({} if where is None else {'ev': where} if where else {'noev': '這一輪程式拍到的頁上沒有這一題'})
        return data

    def write(data):
        candidate, result, happened = None, apply, event
        try:
            candidate = record_form(copy.deepcopy(data)) if event != 'fill_nopage' else None
        except ValueError as error:      # 記不進去(例如新答案沒附翻譯):照列在卡上,這一輪算卡住(A-21)
            import evidence as evidence_store
            evidence_store.record_external([record.evidence_dir], 'program_error', reason=str(error))
            result = dict(apply, issues=[*apply['issues'], '這一輪沒完成'])
            happened = 'fill_bad'
        fired = ds.try_fire(data, card_id, happened, apply=result, evidence=evidence)
        if fired:
            agent_report.apply_resolve(data, card_id, '這張重新填好了,結果看卡上', src=agent_report.FROM_APPLY,
                                       only=lambda item: 'parts' not in item)     # 要不要打斷他由 settle 管
            if record.end is not End.STOPPED_BY_USER:     # 沒做完自動流程下一輪重填(next_step)
                agent_report.settle(data, agent_report.FROM_APPLY, 'fill:' + card_id, trouble, job=card_id,
                                    head=f'填表「{card_title.name(job) or card_id}」', need=FILL_NEED)
            else:       # 他自己停掉的:不算一輪,也不自動重填,他要接手;之後再沒做完從頭數
                (data.get(agent_report.RETRY) or {}).pop('fill:' + card_id, None)
        if not (fired and candidate):
            return
        bank = {e.get('k'): e.get('v') for e in data.get('__ans__') or []}   # 看板現在的答案:他這一輪改的,或別張卡重翻寫的
        typed = {x['k']: x['value'] for x in rows if x.get('k')}         # 這一輪實際打在頁上的值
        data['__ans__'] = candidate.get('__ans__') or []
        data[card_id]['form'] = candidate[card_id]['form']
        for row in data[card_id]['form']['f']:       # 他改了而頁上還是舊字才重打;現值就是頁上打的字(別張卡剛重翻的)不算
            if row.get('k') in checked and bank.get(row['k']) not in (checked[row['k']], typed.get(row['k'])):
                row['refill'] = 1
        ship.take_unmet(data, card_id, job, [row for row in unmet if row['source'] == Source.PROGRAM.value])
    board_doc.set_fb(write, live=board, by='fill')

    notes = [{key: joined(row[key]) for key in ('url', 'note')} for row in sheet.rows('website_notes')]
    website_notes.remember(notes, card_id=card_id, round_id=record.id)
    history = joined(sheet.said('application_history_url'))
    if history:
        profile_sync.remember_application_history(profile_sync.profile_key(card_id), history)
    registration = sheet.said('profile_registration')
    if isinstance(registration, dict) and profile_sync._safe_page_url(registration.get('url')):
        profile_sync.remember(decision['platform'], decision['lang'], decision['variant'], str(registration['url']),
                              by='agent', name=joined(registration.get('name')), only_new=True)
    if sheet.ok('posting') and sheet.ok('company'):
        _rename(card_id, quote(sheet.cells['posting']), quote(sheet.cells['company']), board)


def _publish_fixed(decision: dict, record: RoundRecord, intents: tuple[Intended, ...], card_id: str) -> None:
    """這張卡用固定版平台履歷:只拿網址跟登記網址一字不差的那幾頁,清單發到那一份,可以恢復(F-10、F-11)。"""
    import sync_records
    only, marks = cite.fixed_profile(record, decision['fixed_url'])
    sync_records.remember_visuals(decision['platform'], decision['lang'], decision['variant'],
                                  [listing.visual(build(only, intents), only, intents, task='fill', card=card_id,
                                                  extra=marks)])


def _rename(card_id: str, title: str, company: str, board) -> None:
    """卡名照核對過的職稱、公司改;可投遞夾跟著改名(F-18)。"""
    import board_doc
    import ship
    new = card_title.name(title, company)

    def update(data, _fb):
        for job in data['jobs']:
            if job.get('id') == card_id:
                job['title'], job['company'] = title, company
                job['target'] = card_title.with_name(job['target'], new)
    board_doc.set_data(update, live=board)
    ship.rename(card_id, new)


def _save_submit(_stage: str, card_id: str, record: RoundRecord, board, job: dict, **_unused) -> None:
    """送出:agent 說送出了、引的段核對到才算送出;說沒送出卡回卡住;沒啟動回你已確認;其他結果不明(F-16、F-17)。"""
    import board_doc
    import delivery_core as ds
    import ship
    sheet = record.sheet
    said = sheet.said('submitted')
    cell = sheet.cells.get('evidence')
    proven = isinstance(cell, Cell) and cell.verdict is Verdict.VERIFIED
    handed = record.end is End.HANDED
    event = ('submit_not_started' if record.end is End.NOT_STARTED else
             'submit_not_sent' if handed and said == 'false' else
             'submit_ok' if handed and said == 'true' and proven else 'submit_unsure')
    evidence = {'at': _now(), 'said': said, 'evidence': sheet.said('evidence'),
                'source': (Source.PROGRAM if proven else Source.AGENT).value,
                'needs_user': joined(sheet.said('needs_user')), 'stopped': joined(sheet.said('stopped'))}
    issues = [text for text in (evidence['needs_user'], evidence['stopped']) if text]
    def write(data):
        if ds.try_fire(data, card_id, event, evidence=evidence, apply={'issues': issues}) and event == 'submit_ok':
            ship.record_sent(data, card_id, job)        # 寄出的是哪一份(統計表、卡上「寄出的那一份」)
    board_doc.set_fb(write, live=board, by='fill')


def confirm(card_id: str, browser, board) -> dict:
    """確認送出畫面:拍一次那張卡的工作區,跟填表最後一次拍改後照配對規則比,變了照列,不擋(F-14)。"""
    import board_doc
    import evidence
    _jobs, fb = _load(board)
    apply = fb[card_id]['apply']
    afters = snap.load(evidence.flow_round_dir('fill', apply['round_id'], board) or '', 'after')
    now = snap.take(browser or Real.for_workspace(int(apply['workspace']['id']), apply['workspace']['name']), 'before',
                    evidence.Round('fill', 'confirm', board=board).dirs)
    report = listing.report(since(afters, now), (*compare.latest(list(afters)), *now.pages), since_fill=True)

    def save(data):
        data[card_id]['apply']['changes_since_fill'] = report
    board_doc.set_fb(save, live=board, by='fill')
    return report


def _halt(data: dict, card_id: str, why: str, board=None) -> None:
    """這一輪在卡上開始之後出錯:正在填的回卡住、正在送出的算結果不明,原因照這次的錯寫在卡上;還沒開始的不動。"""
    import delivery_core as ds
    import evidence
    evidence.Round('fill', 'error', [card_id], board).note('program_error', reason=why)
    state = ds.state(data.get(card_id) or {})
    if state == 'running':
        ds.fire(data, card_id, 'fill_bad', apply={'issues': ['這一輪沒完成']}, evidence={'at': _now()})
    elif state == 'sending':
        ds.fire(data, card_id, 'submit_unsure', evidence={'at': _now(), 'said': '', 'needs_user': '',
                                                          'stopped': '送出結果不明'})


def main(argv=None, browser=None) -> int:
    import board_doc
    import form_record
    import jobrun
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage')
    parser.add_argument('--url')
    parser.add_argument('--note', default='')
    parser.add_argument('--board')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--approval-ids', type=json.loads, help='看板派出這一輪時各卡的確認編號(JSON)')
    args = parser.parse_args(argv)
    board = args.board or board_doc.target()
    if args.stage == 'confirm':
        confirm(args.url, browser, board)
        return 0
    output = os.environ.get('APPLY_TMP') or cf.TMP
    os.makedirs(output, exist_ok=True)
    jobs, fb = _load(board)
    selected = [url for url in eligible(jobs, fb, args.stage, args.url, args.note, form_record.board_status(board))
                if args.approval_ids is None or url in args.approval_ids][:args.limit or None]
    pinned = args.approval_ids if args.approval_ids is not None else {     # 送出綁在派出這一刻的那一次確認
        url: ((fb.get(url) or {}).get('approve') or {}).get('id') for url in selected}
    browser_of = browser if callable(browser) else (lambda _url: browser)    # 函式:網址進、這張卡自己的瀏覽器出
    started, rows, guard = time.time(), [], threading.Lock()      # rows 與狀態檔:同時跑的幾張輪流寫

    def status(phase):
        jobrun.write(os.path.join(output, 'apply_status.json'), {
            'phase': phase, 'pid': os.getpid(), 't0': started, 'n': len(selected), 'done': len(rows),
            'stage': args.stage, 'url': args.url or '', 'results': rows,
            'msg': f'已處理 {len(rows)}/{len(selected)} 張', **({'finished_at': time.time()} if phase == 'done' else {})})

    def one(url):
        try:
            record = run(args.stage, url, browser_of(url), board, args.note, pinned.get(url))
        except Exception as error:   # noqa: BLE001 - 一張出什麼錯都只算這張,寫在那張卡上,別張照跑
            why = f'{type(error).__name__}: {error}'
            record = '這一輪沒完成'
            board_doc.set_fb(lambda data, why=why: _halt(data, url, why, board), live=board, by='fill')
        row = ({'url': url, 'ok': False, 'msg': record} if isinstance(record, str)     # 確認作廢或出錯,這張不算
               else {'url': url, 'ok': record.end is End.HANDED, 'round': record.id})
        with guard:
            rows.append(row)
            status('agent')
    status('agent')
    # 同時幾張看設定;一個瀏覽器物件給全部卡的(測試)是同一個工作區,一次只能一張
    workers = 1 if browser is not None and not callable(browser) else parallel(cf.C.get('flow'))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, selected))
    finally:
        status('done')      # 中途出錯也寫完狀態,錯誤照原樣丟出去給看板記

    return 0 if all(row['ok'] for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
