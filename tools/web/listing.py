"""列出:把讀到的原文照原字寫成看板格式(C-14)。標記附來源(程式讀到、agent 說的、程式沒比到、你的設定)。"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os

from web.changelist import BETWEEN, ChangeList, Intended
from web.marks import Mark, Source, joined, say, say_each
from web.round import RoundRecord
from web.seen import Seen, Status
from web.sheet import Accept, Field, How, Sheet, Verdict, quote

CODES = {Status.UNREADABLE: 'PAGE_UNREADABLE', Status.NO_TEXT: 'PAGE_NO_TEXT', Status.TOO_LONG: 'PAGE_TOO_LONG'}


def of_page(page: Seen) -> tuple[Mark, ...]:
    """一頁自己的標記(A-04、A-06)。沒有分頁編號的不是一頁,是整個工作區讀不到,另由 WORKSPACE_UNREAD 標。"""
    if not page._seen_tab and not page._seen_url:
        return ()
    where = (f'{page._seen_title}（{page._seen_url}）' if page._seen_title and page._seen_url
             else page._seen_url or '那一頁')
    out = [say(CODES[page._seen_status], where=where, why=page._seen_why)] if page._seen_status in CODES else []
    if page._seen_shot_why or not page._seen_shot:
        out.append(say('SHOT_MISSING', where=where, why=page._seen_shot_why or '未提供截圖'))
    return tuple(out)


def mark_row(mark: Mark) -> dict:
    return {'code': mark.code, 'source': mark.source.value, 'params': dict(mark.params), 'text': mark.text}


def _page_row(page, before, after, **result):
    """一種關係裡的一頁一列:沒逐處比的兩邊都照列,一頁有幾種關係就有幾列;那兩頁自己的標記附在這一列(#591 ID 第 88 行)。
    兩邊程式拍到的截圖(<輪>/<檔名>)也附上:沒逐處比的那一頁看板開得到,不叫他自己去找(#783)。"""
    marks = {mark.text: mark for side in (before, after) if side is not None for mark in of_page(side)}
    return {'page_tab_id': page._seen_tab, 'page_url': page._seen_url, 'page_title': page._seen_title,
            'before_tab_id': before._seen_tab if before else '', 'after_tab_id': after._seen_tab if after else '',
            'before_url': before._seen_url if before else '', 'after_url': after._seen_url if after else '',
            'before_title': before._seen_title if before else '', 'after_title': after._seen_title if after else '',
            'before_shot': before._seen_shot if before else '', 'after_shot': after._seen_shot if after else '',
            'marks': [mark_row(mark) for mark in marks.values()], **result}


def visual(changes: ChangeList, record: RoundRecord, intended: tuple[Intended, ...], *, task: str,
           card: str = '', extra: tuple[Mark, ...] = ()) -> dict:
    """一輪的改動清單寫成看板格式(平台履歷「同步清單」與填表的確認清單共用)。extra:清單上另外要標的(沒有固定履歷頁)。"""
    page_changes = [
        {'change_id': change.id, 'page_tab_id': change.tab, 'page_url': change.url,
         'before_title': pair.before._seen_title, 'after_title': pair.after._seen_title,
         'before_parts': list(change.removed), 'after_parts': list(change.added),
         'before_text': '\n'.join(change.removed), 'after_text': '\n'.join(change.added),
         'before_shot': os.path.basename(pair.before._seen_shot), 'after_shot': os.path.basename(pair.after._seen_shot),
         'item': '頁面段落', 'verdicts': ['頁面內容有變動'], 'restorable': True}
        for pair in changes.compared for change in pair.changes]
    rows = [_page_row(pair.after, pair.before, pair.after, compared=True, reason='') for pair in changes.compared]
    rows += [_page_row(page, item.before, item.after, compared=False, reason=item.mark.text, reason_code=item.mark.code)
             for item in changes.not_compared for page in (item.before, item.after) if page is not None]
    inputs = {row.item: row for row in intended}
    shown = []
    for row in changes.intended:
        original = inputs[row.item]
        entry = {'item': row.item, 'before': original.before or '', 'after': original.after or '',
                 'marker': row.mark.text}
        reads = dict(row.mark.params) if row.mark.code == 'REMOVED_READ' else {}
        if reads and '沒比到' not in reads.values():
            entry.update(before_read=reads['before'] == '讀得到', after_read=reads['after'] == '讀得到')
        shown.append(entry)
    head = {mark.code: mark.text for mark in changes.head}
    pages = [page for snapshot in (*record.befores, *record.afters) for page in snapshot.pages]
    return {
        'item': '頁面段落', 'task': task, 'card': card, 'evidence': record.id,
        'capture_status': {'before': head.get('NO_BEFORE') or say('BEFORE_TAKEN').text,
                           'after': head.get('NO_AFTER') or say('AFTER_TAKEN').text},
        'marks': [mark_row(mark) for mark in (*changes.head, *changes.stops, *changes.sheet_marks, *changes.claims,
                                               *(row.mark for row in changes.not_compared),
                                               *(row.mark for row in changes.intended),
                                               *(mark for page in pages for mark in of_page(page)), *extra)],
        # 清單上別處沒畫的標記:這一輪怎麼結束、agent 停下與說法、交件單、agent 說的改動核對(#591 ID 第 95 行)
        'list_marks': [mark_row(mark) for mark in (
            *(mark for mark in changes.head if mark.code in ('ENDED', 'NOT_STARTED', 'WORKSPACE_EMPTY')),
            *changes.stops, *changes.sheet_marks, *changes.claims, *extra)],
        'pages_compared': rows,
        'page_changes': page_changes,
        'intended_changes': shown,
        'agent_steps': [{'at': step.at, 'phase': phase, 'command_id': step.command_id,
                         'action': step.command or '操作內容未記錄', 'compared': phase == BETWEEN,
                         'screenshots': [{'page_tab_id': shot.tab, 'page_url': shot.url, 'title': shot.title,
                                          'shot': shot.file, 'screenshot_error': bool(shot.why)} for shot in step.shots]}
                        for phase, step in changes.steps],
    }


def report(changes: ChangeList, pages: tuple[Seen, ...], *, since_fill: bool) -> dict:
    """卡上的頁面紀錄:有變的頁逐處列、沒逐處比的兩邊各一列,加上每一頁自己的標記(F-09、F-14)。
    since_fill:填好後到現在那一份,變了就先標「填好後頁面有變化」;填表那一輪自己的紀錄不標。"""
    rows = [{'page_tab_id': pair.after._seen_tab, 'page_url': pair.after._seen_url,
             'page_title': pair.after._seen_title, 'title': pair.after._seen_title, 'compared': True,
             'changes': [{'before_parts': list(change.removed), 'after_parts': list(change.added)}
                         for change in pair.changes]}
            for pair in changes.compared if pair.changes]
    rows += [_page_row(page, item.before, item.after, compared=False, reason=item.mark.text)
             for item in changes.not_compared for page in (item.before, item.after) if page is not None]
    changed = since_fill and rows and rows[0]['compared']
    marks = [*([say('PAGE_CHANGED_SINCE_FILL')] if changed else []), *changes.head, *changes.stops, *changes.sheet_marks,
             *changes.claims, *(item.mark for item in changes.not_compared), *(mark for page in pages for mark in of_page(page))]
    return {'heading': '填好後到送出前的頁面紀錄' if since_fill else '這一輪填表的頁面紀錄',
            'marks': [mark_row(mark) for mark in marks], 'pages': rows}


# 填表交件單上的職缺檔案要求:要求原句是頁上原文,這一格的寫法跟讀法都在這裡(F-26)
UNMET = Field('unmet', How.CITE, '職缺要求而你的檔案沒有的:引頁上的要求段落', many=True, sub=(
    Field('quote', How.CITE, '職缺要求原句', accept=Accept(kinds=('line',), owner=None, value=None)),
    Field('file', How.WORDS, '不符合的檔名'),
    Field('act', How.ENUM, '處理方式', one_of=('drop', 'adapt')),
    Field('why', How.WORDS, '不符合的原因')))


def unmet(sheet: Sheet) -> list[dict]:
    """職缺的檔案要求:要求原句一字不差在引的段裡才標程式讀到、照頁上原字列,不然照 agent 寫的列、標 agent 說的(F-26)。"""
    cells = sheet.cells.get('unmet')
    out = []
    for row in cells if isinstance(cells, list) else ():
        verified = row['quote'].verdict is Verdict.VERIFIED
        out.append({'quote': quote(row['quote']) if verified else joined(row['quote'].said),
                    **{key: joined(row[key].said) for key in ('file', 'act', 'why')},
                    'source': (Source.PROGRAM if verified else Source.AGENT).value})
    return out


def replies_field(kinds: tuple[str, ...]) -> Field:
    """查應徵進度交件單上的每則回音:主旨、引句是頁上原文(材料只有頁面段落),這一格的寫法跟讀法都在這裡(P-04)。"""
    return Field('replies', How.WORDS, '每則回音', many=True, sub=(
        Field('card', How.KNOWN, '卡片編號', known='cards', required=True),
        Field('source', How.KNOWN, '來源名稱(照材料寫)', known='sources', required=True),
        Field('kind', How.ENUM, '種類;只看得出已收到申請的應徵紀錄算確認信', one_of=kinds, required=True),
        Field('subject', How.CITE, '主旨的段落編號', required=True),
        Field('quote', How.CITE, '引文的段落編號', required=True),
        Field('date', How.WORDS, '那一天 YYYY-MM-DD;沒有就留空'),
        Field('todo', How.WORDS, '對方要求本人完成而且尚未完成的事;沒有就留空'),
        Field('reason', How.CITE, '拒絕理由的段落編號;沒有就留空')))


def reply_id(item: dict) -> str:
    """回音的身分只看來源、主旨、引句;卡上存的(舊版存的也一樣)照欄位重算再比(P-22)。"""
    return hashlib.sha256(json.dumps([item.get('src', ''), item.get('subject', ''), item.get('quote', '')],
                                     ensure_ascii=False).encode('utf-8')).hexdigest()[:20]


def replies(sheet: Sheet, pages: tuple[Seen, ...]) -> list[dict]:
    """每則回音寫成卡上的格式:卡、來源、主旨與引句都核對到才照頁上原字存,不然照 agent 寫的存、標沒核對到;
    種類、日期、要你做的事照 agent 原字(P-04–P-07)。id 只看來源、主旨、引句。"""
    urls = {page._seen_tab: page._seen_url for page in pages}
    cells = sheet.cells.get('replies')
    out = []
    for row in cells if isinstance(cells, list) else ():
        verified = all(row[key].verdict is Verdict.VERIFIED for key in ('card', 'source', 'subject', 'quote'))

        def text(cell):
            return quote(cell) if cell.verdict is Verdict.VERIFIED else joined(cell.said)
        parts = row['quote'].value.parts if row['quote'].verdict is Verdict.VERIFIED else ()
        item = {'card': joined(row['card'].said), 'src': joined(row['source'].said), 'kind': joined(row['kind'].said),
                'date': joined(row['date'].said), 'todo': joined(row['todo'].said),
                'subject': text(row['subject']), 'quote': text(row['quote']), 'snippet': text(row['quote']),
                'reason': quote(row['reason']) if row['reason'].verdict is Verdict.VERIFIED else '',
                'link': urls[parts[0].owner] if parts else ''}
        item['id'] = reply_id(item)
        item.update({} if verified else {'unverified': True, 'agent_said': {key: cell.said for key, cell in row.items()}})
        out.append(item)
    return out


def unread_rows(marks: tuple[Mark, ...]) -> list[dict]:
    """程式標的來源沒查過,寫成卡上 unchecked_sources 的一列(P-08–P-10)。"""
    return [{'source': params['name'], 'url': params['url'], 'by': 'program'}
            for params in (dict(mark.params) for mark in marks)]


# ---- 找缺:讀到的網址與判斷 agent 每一欄(J 段) ----
VERIFIED, UNCHECKED = '程式核對過', '沒核對到'       # 卡上一欄的狀態;看板照「程式核對過」畫事實(card.VERIFIED)
FETCH_STATUS = {Status.READ: 'ok', Status.TOO_LONG: 'partial'}
EXCERPT = 1200
ON_ITS_PAGE = Accept(kinds=frozenset({'line'}), owner=lambda row, _truth: {row.get('url')}, value='text')
RISK = Field('risk_quote', How.CITE, '依據的段落(引這一頁)', accept=dataclasses.replace(ON_ITS_PAGE, value=None))


def fetched(page: Seen) -> dict:
    """讀一個網址寫上卡的樣子:讀取狀態、前段原文、這一頁的標記;讀不到加「欄位由 agent 判斷」(J-03、J-09、J-10)。"""
    marks = of_page(page)
    return {'page_status': FETCH_STATUS.get(page._seen_status, 'unknown'),
            'page_excerpt': '\n'.join(page._seen_parts)[:EXCERPT], 'marks': [mark_row(mark) for mark in marks]}


PARTIAL_NOTICE = '頁面只回傳部分內容，其餘內容未提供；是否繼續由 agent 判斷'


def job_page(page: Seen) -> dict:
    """讀一個網址交給 agent 當職缺原文的樣子(客製、準備履歷):讀得到沒、只讀到部分的邊界、路徑、HTTP、原文、讀不到的原因。"""
    partial = page._seen_status is Status.TOO_LONG
    return {'readable': page._seen_status in (Status.READ, Status.TOO_LONG) and bool(page._seen_parts),
            'partial': (page._seen_why or PARTIAL_NOTICE) if partial else '', 'via': page._seen_via,
            'http_status': page._seen_raw.get('http_status') or 0, 'text': '\n'.join(page._seen_parts),
            'why': page._seen_why}


def _claim_row(claim: str, raw: str, state: str, marks) -> dict:
    return {'claim': claim, 'raw': raw, 'state': state, 'marks': [mark_row(mark) for mark in marks]}


def claim(cell) -> dict:
    """agent 的一欄頁上值:引的段裡一字不差有它寫的字才是程式核對過;引的段沒有那個值就並列;沒寫這一欄是 agent 說
    頁上沒寫;沒交那一列是沒有引段;其他照核對結果標(J-04–J-06、A-29)。"""
    if cell is None:
        return _claim_row('', '', UNCHECKED, (say('CITE_NONE'),))
    if cell.said is None:
        return _claim_row('', '', UNCHECKED, (say('FIELD_NOT_ON_PAGE'),))
    text = cell.said.get('text') if isinstance(cell.said, dict) else ''
    text = text if isinstance(text, str) else ''
    raw = quote(cell)
    if cell.verdict is Verdict.VERIFIED:
        return _claim_row(text, raw, VERIFIED, (say('CLAIM_MATCH'),))
    detail = dict(cell.marks[0].params) if cell.marks else {}
    why = detail.get('why') or detail.get('actual') or ''
    return _claim_row(text, raw, UNCHECKED,
                      (say('FIELD_SIDE_BY_SIDE', said=text, actual=raw, quote=raw, why=why),)
                      if text and raw else cell.marks)


def backed(cell, words: str) -> dict:
    """agent 的判斷附它引的那段:引得到標程式讀到,引不到照它的話標 agent 說的(J-17、J-21)。"""
    said = say_each('AGENT_WORDS', words, 'words')
    if cell.verdict is Verdict.VERIFIED:
        return _claim_row('', quote(cell), VERIFIED, (say('CLAIM_MATCH'), *said))
    return _claim_row('', quote(cell), UNCHECKED, (*said, *cell.marks))


def risk(row: dict, kind: str, why: str) -> dict:
    """詐騙或幽靈職缺的跡象:是 agent 的判斷,附它引的那一句(J-21)。"""
    return {'kind': kind, 'why': why, 'by': 'agent', **backed(row[RISK.name], why)}
