#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""找缺:搜尋 agent 交網址,程式讀每一頁(附截圖),判斷 agent 每一欄引程式讀到的段落;每個值照核對結果標(行為清單 J 段)。
使用者的設定(板上已有、判過不送、一定不要、封鎖公司、條件)由程式拿核對過的值判,不交給 agent(ADR-0013)。"""
from __future__ import annotations

import argparse
import collections
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

import board_doc as bd
import card as cards
import config as cf
import delivery_state
import find_terms
from web import listing, seen
from web.brief import FETCH, Brief
from web.marks import joined, say
from web.numbered import Numbered
from web.outside import Real
from web.round import End, record_unstarted, run as run_round
from web.sheet import Field, How, Schema, Verdict

STATUS = 'converge_status.json'
ROUND_HISTORY_LIMIT = 20
NO_WORKSPACE = {'id': 0, 'name': ''}        # 找缺的 agent 不用瀏覽器工作區:搜尋用網路搜尋與 page_fetch,判斷只看材料
MODE_LABEL = {'both': '更深＋更廣', 'deep': '更深', 'wide': '更廣', 'dir': '指定方向', 'add': '使用者自己貼的網址'}
PAGE_WHY = {'partial': '部分頁面', 'unknown': '頁面無法讀取'}
PAGE_FIELDS = (('title', '職稱'), ('company', '公司'), ('posted_at', '刊登日期'),
               ('applicants', '應徵人數(text 寫最多幾人的整數,或「沒有上限」)'), ('status', '招募狀態'))
SUMMARY_FIELDS = (('fit', '為什麼適合你'), ('co', '公司在做什麼'), ('loc', '地點'), ('salary', '薪資'),
                  ('deadline', '截止日(text 寫 YYYY-MM-DD)'), ('bar', '門檻'), ('ammo', '彈藥'))
JUDGED_TERM = {'bar': 'exp_years', 'loc': 'location'}  # 引段那一欄 → 程式拿來比條件的判斷欄;薪資另由程式換算(salary_month)
REPOST_HINT = '公司與職稱跟這張舊卡一字不差,程式沒查過是不是同一個缺'

WEBSITE_NOTES = (
    Field('website_notes', How.WORDS, '這輪實際造訪、下次有用的讀頁經驗', many=True, sub=(
        Field('url', How.WORDS, '這輪實際造訪的網址'), Field('note', How.WORDS, '可重用的讀頁經驗'))),
    Field('website_notes_remove', How.WORDS, '要移除的過時 agent 網站筆記', many=True, sub=(
        Field('id', How.WORDS, '網站筆記的 ID'),)),
)
SEARCH_SHEET = Schema('find-search', (
    Field('candidates', How.WORDS, '找到的職缺', many=True, sub=(
        Field('url', How.WORDS, '職缺網址'), Field('why', How.WORDS, '為什麼可能符合'),
        Field('via', How.WORDS, '從哪裡找到'), Field('angle', How.WORDS, '從哪個角度找到'))),
    Field('notes', How.WORDS, '這輪的搜尋方向與後續建議'),
    Field('stopped', How.WORDS, '沒做完就停下的話:停在哪、為什麼'),
    *WEBSITE_NOTES,
))
JUDGED_ROW = (
    Field('score', How.NUMBER, '對味分數', one_of=('1', '2', '3', '4', '5')),
    Field('why', How.WORDS, '判斷理由'),
    Field('cat', How.WORDS, '職缺分類(照使用者設定的分類名稱寫)'),
    *(Field(name, How.CITE, f'{label}:引這一頁的段落,text 寫頁上一字不差的字;頁上沒寫就不寫這一欄',
            accept=listing.ON_ITS_PAGE) for name, label in PAGE_FIELDS + SUMMARY_FIELDS),
    Field('salary_amount', How.NUMBER, '薪資下限的金額,照頁上寫的幣別與月薪年薪,不換算(4萬寫 40000);薪資那一欄沒引就不寫'),
    Field('salary_currency', How.WORDS, '那個金額的幣別三碼代號(新台幣 TWD、美元 USD);薪資那一欄沒引就不寫'),
    Field('salary_period', How.ENUM, '那個金額是月薪、年薪,還是其他(時薪、日薪、週薪…);薪資那一欄沒引就不寫', one_of=('月', '年', '其他')),
    Field('exp_years', How.NUMBER, '門檻要求的最少年資數字(不拘寫 0);門檻那一欄沒引就不寫'),
    Field('location', How.ENUM, '地點符不符合使用者條件的地點;地點那一欄沒引就不寫', one_of=('符合', '不符', '看不出')),
    Field('risk', How.ENUM, '詐騙或幽靈職缺的跡象', one_of=('none', 'ghost', 'scam')),
    Field('risk_why', How.WORDS, '你判斷有這個跡象的理由'),
    listing.RISK,
    Field('resume', How.KNOWN, '選的履歷與語言,照材料的選項原樣抄;沒有適合的留空', known='resumes'),
    Field('pick_why', How.WORDS, '選這份履歷的理由'),
)


def _judge_sheet(task, *ask):
    return Schema(task, (
        Field('candidates', How.WORDS, '每個職缺一列', many=True, required=True, sub=(
            Field('url', How.KNOWN, '職缺頁網址,照材料原樣抄', known='urls', required=True), *ask, *JUDGED_ROW)),
        Field('stopped', How.WORDS, '沒做完就停下的話:停在哪、為什麼'),
    ))


JUDGE_SHEET = _judge_sheet('find-judge', Field('keep', How.ENUM, '要不要送進看板', one_of=('true', 'false'), required=True))
ADD_SHEET = _judge_sheet('add-judge')       # 使用者自己貼的網址一律成卡(J-16),不問送不送
NOTE_SHEET = Schema('preference-note', (
    Field('note', How.WORDS, '完整新版偏好筆記', required=True),
    Field('cards', How.WORDS, '每張新表態的歸屬,每張恰好一列', many=True, required=True, sub=(
        Field('card', How.KNOWN, '卡片代號', known='cards', required=True),
        Field('hypothesis', How.WORDS, '歸到新版筆記 Agent 假設區的哪一條(照那條的原字寫)'),
        Field('unclear', How.WORDS, '還看不出來的原因'))),
))
NO_TOOLS = '這一步不用工具,只用上面的材料。'


def tmp() -> str:
    return os.environ.get('CONVERGE_TMP') or cf.TMP


def _read_text(path):
    try:
        with open(path, encoding='utf-8') as source:
            return source.read().strip()
    except OSError:
        return ''


def _skill(name):
    """使用者在設定選的 skill 檔;沒選、選的讀不到就用內建的。"""
    import settings_api
    path = settings_api.safe_rel(((cf.C.get('research') or {}).get('skills') or {}).get(name))
    return (path and _read_text(path)) or _read_text(os.path.join(HERE, 'research_skills', cf.RESEARCH_SKILLS[name]['file']))


def _search_skills(mode):
    selected = ('deep', 'wide') if mode == 'both' else (mode,)
    return '\n\n'.join(_skill(name) for name in ('common', *selected))


def _marks(feedback, url):
    import prefs
    state = feedback.get(url) or {}
    words = [prefs.MARK.get(state.get('s'))]
    words += ['加進準備區'] * bool(state.get('app')) + ['已移除'] * bool(state.get('rm'))
    return '、'.join(word for word in words if word) or '未表態', prefs.his_words(state.get('n'))


def _round_history(rounds, feedback):
    """最近 8 輪每輪的紀錄原樣交出,加上那輪進看板的卡後來他看了幾張、喜歡幾張、不喜歡幾張。"""
    rows = []
    for record in rounds[-8:]:
        states = [feedback.get(url) or {} for url in record.get('added') or []]
        rows.append(dict(record, marks={'seen': sum(bool(s.get('s') or s.get('app') or s.get('rm')) for s in states),
                                        'liked': sum(bool(s.get('app') or s.get('s') in ('like', 'grow')) for s in states),
                                        'disliked': sum(s.get('s') == 'dislike' for s in states)}))
    return json.dumps(rows, ensure_ascii=False)


def _angle_map(feedback, jobs):
    angles = {}
    for job in jobs:
        angle = ((job.get('src') or {}).get('angle') or '').strip()
        state = feedback.get(job['id']) or {}
        counts = angles.setdefault(angle, {'sent': 0, 'liked': 0, 'disliked': 0, 'unseen': 0})
        counts['sent'] += 1
        counts['liked'] += bool(state.get('app') or state.get('s') in ('like', 'grow'))
        counts['disliked'] += state.get('s') == 'dislike'
        counts['unseen'] += not (state.get('s') or state.get('app') or state.get('rm'))
    return '\n'.join(f"- {angle}：送過 {row['sent']}、喜歡 {row['liked']}、不喜歡 {row['disliked']}、還沒看 {row['unseen']}"
                     for angle, row in angles.items() if angle) or '(目前沒有角度表態)'


def _seed_text(seeds):
    return '\n'.join(f"- 指定{'職缺網址' if seed['k'] == 'job' else '公司'}：{seed['v']}"
                     for seed in seeds) or '(目前沒有)'


def _listed(values):
    return '、'.join(values) or '(目前沒有)'


def user_materials(board):
    """兩個 agent 共用的使用者材料:履歷、偏好、表態與最近幾輪。「一定不要」與條件由程式判(find_terms),不交 agent;
    封鎖名單照交(別的寫法、簡稱由 agent 判),程式另擋一字不差的(J-12)。"""
    import feedback_dump
    import prefs
    feedback, jobs = prefs.load(board)
    search = cf.C.get('search') or {}
    rounds = (bd.load(board).get('data') or {}).get('research') or []
    liked = dict.fromkeys(row['company'] for row in prefs.cards(feedback, jobs) if row['pos'] and row['company'])
    return (
        ('履歷原文', prefs.resume()),
        ('使用者自訂硬規則原文', prefs.hard_rules()),
        ('使用者偏好筆記原文', _read_text(cf.PREFERENCE_NOTE)),
        ('偏好整理 skill 原文', _skill('note')),
        ('使用者逐張表態整理原文', feedback_dump.build(live=board)),
        ('角度地圖(依目前看板卡片與表態計數)', _angle_map(feedback, jobs)),
        ('最近幾輪找缺資料', _round_history(rounds, feedback)),
        ('使用者喜歡過的公司', _listed(liked)),
        ('使用者封鎖的公司', _listed(str(x) for x in feedback.get('__block__') or () if x)),
        ('使用者設定的要小心字', _listed(str(x) for x in search.get('flag_words') or () if x)),
        ('使用者設定的要小心職稱正規式原文', search.get('flag_title') or '(目前沒有)'),
        ('使用者說過別再找的方向', _listed(str(x) for x in (feedback.get('__research__') or {}).get('mute') or () if x)),
        ('使用者設定的職缺分類', json.dumps((cf.C.get('board') or {}).get('categories') or [], ensure_ascii=False)),
        ('網站筆記檔案位置', f'{cf.path("website-notes.json")}。依主機名自行讀取相關筆記,筆記只供參考。'),
    ), feedback, jobs


def _ask(ask):
    return (('使用者方向原文', ask['direction'] or '(沒有寫)'),
            ('這輪找法', MODE_LABEL.get(ask['mode'], ask['mode'])),   # 看板預覽判斷那份 prompt 帶 judge
            ('使用者條件', ask['terms']),
            ('使用者指名的搜尋種子', _seed_text(ask['seeds'])))


def ask_of(mode, direction='', seeds=(), terms=None):
    """這一輪使用者選的範圍(J-22);條件沒給或壞掉就用設定裡的。"""
    try:
        given = json.loads(terms) if terms else None
    except ValueError:
        given = None
    if not isinstance(given, dict) or find_terms.problem(given):
        terms = json.dumps((cf.C.get('search') or {}).get('terms') or {}, ensure_ascii=False)
    return {'mode': mode, 'direction': direction, 'seeds': tuple(seeds), 'terms': terms}


def round_terms(ask):
    """這輪條件,程式拿核對過的值比的就是這一份(find_terms.check_judged)。"""
    return find_terms.normalize(json.loads(ask['terms']))


def search_brief(ask, shared, jobs):
    urls = '\n'.join(dict.fromkeys(job['id'] for job in jobs))
    return Brief(
        '依使用者的找缺 skill 與條件找可能符合需求的職缺,交回職缺網址。使用者決定要不要保留每張卡。',
        (*_ask(ask), ('使用者的找缺 skill', _search_skills(ask['mode'])), *shared,
         ('已在看板上的職缺網址', urls or '(目前沒有)')),
        ('網路搜尋:找候選職缺。', FETCH),
        ('只讀網站,不應徵、不登入、不輸入密碼、不留言或私訊。',
         '網站筆記不記職缺內容、個人資料、登入資訊或推測;使用者寫的筆記列入移除也不會刪除。'),
        SEARCH_SHEET)


def resume_choices():
    """已勾選且有語言檔的每一份履歷;選項字串就是交件要抄的值。"""
    import prefs
    resumes = prefs.checked_resumes()
    return {f"{item.get('id')}/{lang}": (str(item.get('id')), lang, item) for item in resumes
            for lang in cf.LANGS if prefs.valid_resume_pick(item.get('id'), lang)}


def judge_brief(ask, shared, material, urls, pages, feedback, jobs, sheet=JUDGE_SHEET):
    """每一頁的讀取標記與編號段落各自一塊,段落只屬於那一頁的列(J-04、C-16);職缺頁在前,舊卡與當時表態接著編號(判斷 skill 拿表態當使用者原話)。"""
    blocks = ['\n'.join(text for text in (f'=== {url} ===', *(row['text'] for row in listing.fetched(page)['marks']),
                                          seen.number(material, [page], owner=url)) if text)
              for url, page in zip(urls, pages)]
    old = []
    for job in jobs:
        mark, words = _marks(feedback, job['id'])
        old.append(f"{cards.name(job)}｜{job['id']}｜當時表態：{mark}" + (f'｜原話：「{words}」' if words else ''))
    old_block = material.add(old, 'old') or '(目前沒有)'
    choices = '\n'.join(f'- {key}:{item.get("name") or item.get("id")};什麼時候用:{item.get("when") or "(沒寫)"}'
                        for key, (_id, _lang, item) in resume_choices().items())
    return Brief(
        '照使用者的判斷 skill 判斷每一個職缺;每一欄都引程式讀到的那一頁的段落。使用者決定要不要保留每張卡。' if sheet is JUDGE_SHEET
        else '這幾個網址是使用者自己貼的,一律進看板,不用判送不送;照使用者的判斷 skill 寫每一張,每一欄都引程式讀到的那一頁的段落。',
        (*_ask(ask), ('使用者的判斷 skill', _skill('judge')), *shared,
         ('可選的履歷與語言', choices or '(目前沒有已勾選的履歷;resume 留空)'),
         ('交付代號', f'material_id={material.material_id}'),
         ('程式讀到的職缺頁(每頁的段落只能用在那一頁的列)', '\n\n'.join(blocks)),
         ('看板上的舊卡與當時表態', old_block)),
        (NO_TOOLS,),
        ('只引材料裡的段落。', '頁面讀不到、沒有文字或不完整,也照樣交那一列。'),
        sheet)


def note_brief(skill, base, delta):
    return Brief(
        '依使用者選擇的整理偏好筆記 skill,整理上次整理後的新表態,讓下一輪找缺使用更新後的角度。',
        (('整理偏好筆記 skill', skill), ('偏好筆記原文', base.rstrip()), ('新表態原文', delta.rstrip())),
        (NO_TOOLS,),
        ('只更新 Agent 假設區;使用者自訂區逐字保留。', '每張新表態都要歸屬;沒列齊時程式不更新筆記,下一輪再整理。'),
        NOTE_SHEET)


def read_urls(browser, urls, review_round):
    """程式讀每個網址並截圖(J-02、J-10);讀頁紀錄與截圖存進這一輪每張卡的證據夾,回 (每一頁, 每張卡指向證據的欄)。"""
    pages, shots = [], {}
    for url in urls:
        directory = tempfile.mkdtemp(prefix='jobsalvo-find-')
        page = seen.fetch(browser, url, os.path.join(directory, 'page.png'))
        record = seen.dump(page)
        row = review_round.page(record, record['why'] or record['status'], shot=record['shot'] or None)
        shots[url] = {key: review_round.rel(row[name]) for key, name in
                      (('page_file', 'file'), ('screenshot_file', 'shot')) if row.get(name)}
        with contextlib.suppress(OSError):
            os.remove(record['shot'])
        os.rmdir(directory)
        pages.append(page)
    return pages, shots


def _cells(cell):
    """清單格的每一列格子;agent 沒交成清單時是一格,當作沒有列。"""
    return cell if isinstance(cell, list) else []


def _ok(cell):
    return cell.verdict in (Verdict.VERIFIED, Verdict.PARSED)


def rows_by_url(record, urls):
    """每個候選對到恰好一列(J-11、C-16);判斷沒交件、交件單壞掉,每個候選都帶著原因照「沒核對到」進待評估(J-08)。
    回 ({網址: (那一列, 問題)}, 對不上候選的列數)。"""
    issue = (record.sheet.broken or (say('ENDED', end=record.end.value) if record.end is not End.HANDED else None))
    cells = record.sheet.cells.get('candidates')
    if issue or not isinstance(cells, list):
        issue = issue or say('SHEET_BROKEN', why=cells.marks[0].text)
        return {url: ({}, issue) for url in urls}, 0
    listed = {}
    good = [row for row in cells if _ok(row['url'])]
    for row in good:
        listed.setdefault(row['url'].said, []).append(row)
    out = {}
    for url in urls:
        found = listed.get(url) or []
        row = found[0] if len(found) == 1 else {}
        out[url] = (row, say('ROW_UNLISTED') if not found else say('ROW_TWICE') if len(found) > 1 else
                    None if 'keep' not in row or _ok(row['keep']) else
                    say('ROW_FIELD_BAD', field='keep', why=row['keep'].marks[0].text))
    return out, len(cells) - len(good)


def _applicants(record):
    """應徵人數只收最多幾人的整數或「沒有上限」;別的寫法算查不到(J-15)。"""
    claim = record['claim'] if record['state'] == cards.VERIFIED else ''
    return int(claim) if re.fullmatch('[0-9]+', claim) else claim if claim == find_terms.NO_CAP else None


def term_values(row, rates):
    """使用者條件拿來比的值:那一欄引段核對過,才收它或它附的判斷;其他的條件查不到(J-07)。
    薪資由程式換成新台幣月薪:agent 交頁上的金額、幣別、月薪或年薪,匯率是今天的(rates)。"""
    records = {name: listing.claim(row.get(name)) for name in ('posted_at', 'applicants', 'salary', *JUDGED_TERM)}
    verified = {name for name, record in records.items() if record['state'] == cards.VERIFIED}
    values = {'posted_at': records['posted_at']['claim']} if 'posted_at' in verified else {}
    applicants = _applicants(records['applicants'])
    values.update({'applicants': applicants} if applicants is not None else {})
    values.update({judged: row[judged].value for cited, judged in JUDGED_TERM.items()
                   if cited in verified and row.get(judged) is not None and _ok(row[judged])})
    amount, currency, period = (row.get(name) for name in ('salary_amount', 'salary_currency', 'salary_period'))
    month = find_terms.twd_month(amount.value, joined(currency.said), period.said, rates) if (
        'salary' in verified and amount is not None and _ok(amount) and period is not None and _ok(period)
        and currency is not None) else None
    values.update({'salary_month': month} if month is not None else {})
    return values


def card_of(url, candidate, page, row, shot, run, issue, jobs, terms, rates):
    """一列交件 → 一張卡;沒交的列是空的,每一欄照核對結果標,沒核對到的值不拿來決定(J-04–J-07、J-14、J-15、J-17、J-21)。"""
    import prefs
    fields = {name: listing.claim(row.get(name)) for name, _label in PAGE_FIELDS}
    summary = {name: listing.claim(row.get(name)) for name, _label in SUMMARY_FIELDS}
    records = {**fields, **summary}
    facts = {name: record['claim'] for name, record in records.items() if record['state'] == cards.VERIFIED}
    claims = [record for name, record in records.items() if row.get(name) is not None and row[name].said is not None]
    applicants = _applicants(fields['applicants'])
    _missed, unknown = find_terms.check_judged(term_values(row, rates), terms)
    verification = listing.fetched(page)
    verification['marks'] += [listing.mark_row(say('TERM_UNCHECKED', term=term)) for term in unknown]
    verification.update(
        state='verified' if (verification['page_status'] == 'ok' and not issue and claims
                             and all(record['state'] == cards.VERIFIED for record in claims)) else 'review',
        fields=fields, summary={'fields': summary}, **shot,
        **({'report_issue': listing.mark_row(issue)} if issue else {}))
    score = row['score'].value if row and _ok(row['score']) else 0
    said = {name: row[name].said if row else None for name in ('why', 'cat', 'risk', 'risk_why', 'pick_why')}
    source = {**run, 'via': candidate['via'], 'angle': candidate.get('angle', ''),
              'why': PAGE_WHY.get(verification['page_status']) or joined(said['why']), 'fit': score,
              'site': seen.host(url), 'verification': verification}
    if row and _ok(row['risk']) and said['risk'] != 'none':
        source['risk'] = listing.risk(row, said['risk'], joined(said['risk_why']))
    same = [job.get('id') for job in jobs if facts.get('title') and facts.get('company')
            and job.get('title') == facts['title'] and job.get('company') == facts['company']]
    source.update({'repost_hint': {'cards': same, 'text': REPOST_HINT}} if same else {})
    choice = resume_choices().get(row['resume'].said) if row and row['resume'].verdict is Verdict.VERIFIED else None
    today = time.strftime('%Y-%m-%d')
    deadline = summary['deadline']['claim'] if summary['deadline']['state'] == cards.VERIFIED else ''
    entry = {'id': url, 'target': cards.name(facts.get('title') or '職缺頁待確認', facts.get('company')),
             'title': facts.get('title', ''), 'company': facts.get('company', ''),
             'cat': joined(said['cat']), 'chan': '官方', 'ammo': '', 'note': str(score), 'bk': False, 'dead': False,
             'added': today, 'src': source,
             'sum': {name: record['claim'] or cards.said(record) for name, record in summary.items()
                     if row.get(name) is not None and row[name].said is not None},
             'resume': {'selection_signature': prefs.resume_selection_signature(),
                        'feedback_signature': prefs.feedback_signature(''),
                        **({'recommend': choice[0], 'lang': choice[1], 'pick_why': joined(said['pick_why'])}
                           if choice else {})}}
    entry.update({'terms_unknown': unknown} if unknown else {})
    entry.update({'deadline': deadline} if cards.iso_day(deadline) == deadline and deadline else {})
    entry.update({'posted_at': facts['posted_at'], 'posted_src': fields['posted_at']['raw']} if 'posted_at' in facts else {})
    entry.update({'status': facts['status']} if 'status' in facts else {})
    entry.update({'applicants': applicants, 'applicants_at': today, 'applicants_log': [[today, applicants]]}
                 if applicants is not None else {})
    return entry


TURNED = 'judged-no.jsonl'      # 判過、agent 說不投或判成詐騙的網址:下一輪找到同一張不再讀、不再判(J-13),看板逐張列出(J-24)


def turned_down():
    """以前判過、agent 說不投的網址 → 那一次的紀錄。同一張被不同輪重複找到就重複付一次,而且結論一定一樣(J-13)。"""
    out = {}
    with contextlib.suppress(OSError), open(os.path.join(cf.RESEARCH, TURNED), encoding='utf-8') as source:
        for line in source:
            with contextlib.suppress(ValueError, KeyError, TypeError, AttributeError):   # 寫到一半的那一行:跳過
                row = json.loads(line)
                out[row['url']] = row
    return out


def card_shot(job):
    """看板上那張卡讀頁時拍的截圖(證據夾的 <輪>/<檔名>);舊卡沒有回空的。"""
    return ((job.get('src') or {}).get('verification') or {}).get('screenshot_file') or ''


def turned_add(rows):
    if not rows:
        return
    os.makedirs(cf.RESEARCH, exist_ok=True)
    with open(os.path.join(cf.RESEARCH, TURNED), 'a', encoding='utf-8') as target:
        target.writelines(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)


AGENT_DROPS = ('agent 說不投', '詐騙')   # 照 agent 的判斷丟的;其餘是程式照使用者設定丟的


def dropped(row, entry, feedback, terms, rates):
    """完整交件的一列要不要成卡,回不成卡的原因(None = 成卡)、agent 的話、不符的條件。
    agent 說不投或判成詐騙照它;其餘照使用者的設定,只拿核對過的值比(J-07、J-12、J-13)。"""
    if row['keep'].said != 'true':
        return 'agent 說不投', joined(row['why'].said), []
    if row['risk'].said == 'scam' and entry['src']['verification']['state'] == 'verified':
        return '詐騙', joined(row['risk_why'].said), []
    if find_terms.excluded(entry['title']):
        return '硬排除', '', []
    if entry['company'] and delivery_state.company_blocked(feedback, entry):
        return '封鎖公司', '', []
    missed, _unknown = find_terms.check_judged(term_values(row, rates), terms)
    return ('不符條件' if missed else None), '', missed


def gone(url, rule, why='', target='', shot='', facts=None, missed=()):
    """丟掉的一張,看板逐張列出:原因、截圖、程式核對過的刊登日、人數、換算的月薪、年資、agent 說的地點符不符,讓使用者自己對照(J-24)。"""
    keep = ('posted_at', 'applicants', 'salary_month', 'exp_years', 'location')
    return {'id': url, 'rule': rule, 'why': why, 'target': target, 'shot': shot, **({'missed': list(missed)} if missed else {}),
            **{name: value for name, value in (facts or {}).items() if name in keep}}


def _save(board, entries, history_row):
    added_ids = []

    def update(data, _fb):
        known = {row['id'] for row in data['jobs']}
        added = [entry for entry in entries if entry['id'] not in known]
        data['jobs'].extend(added)
        added_ids.extend(entry['id'] for entry in added)
        history = data.get('research') or []
        data['research'] = history[-(ROUND_HISTORY_LIMIT - 1):] + [dict(history_row, added=list(added_ids))]
    bd.set_data(update, live=board)
    return added_ids


def _save_website_notes(sheet, round_id):
    import website_notes
    additions = [{'url': joined(row['url']), 'note': joined(row['note'])} for row in sheet.rows('website_notes')]
    removals = [joined(row['id']) for row in sheet.rows('website_notes_remove')]
    protected = website_notes.remember(additions, removals, round_id=round_id)
    return (f'agent 試圖移除 {len(protected)} 則使用者網站筆記，程式已保留；請到網站筆記設定確認'
            if protected else '')


class Run:
    """一輪找缺共用的時間上限:搜尋、整理偏好、判斷都從同一個截止時間扣;暫停的時間不計(J-18)。"""

    def __init__(self, board, browser, minutes):
        import jobrun
        self.board, self.browser = board, browser
        self.started = time.time()
        self.deadline = self.started + minutes * 60 if minutes else None
        self.paused0 = jobrun.current_paused()

    def paused(self):
        import jobrun
        return jobrun.current_paused() - self.paused0

    def unpaused_seconds(self):
        return int(time.time() - self.started - self.paused())

    def left(self, default):
        return default if self.deadline is None else max(0, int(self.deadline + self.paused() - time.time()))

    def agent(self, brief, known, default, materials=()):
        """派一個 agent;時間用完就不派,這一輪照讀到的收尾。"""
        seconds = self.left(default)
        return run_round('find', brief, self.browser, timeout_s=seconds, board=self.board, workspace=NO_WORKSPACE,
                         known=known, materials=materials) if seconds else record_unstarted(
            brief, 'find', self.board, '找缺的時間到了')


def _update_preference_note(current):
    """整理喜好:每張新表態都歸屬了才更新筆記;使用者自訂區 agent 改不動(J-19)。"""
    import feedback_dump
    import prefs
    delta, listed, journal_lines = feedback_dump.feedback_delta(current.board, cf.PREFERENCE_NOTE)
    if journal_lines <= 0 or not listed:
        return ''
    base = prefs.ensure_note(cf.PREFERENCE_NOTE)
    record = current.agent(note_brief(_skill('note'), base, delta), {'cards': frozenset(listed)}, 4 * 3600)
    note = joined(record.sheet.said('note'))
    agent_part = prefs._note_section(note, prefs.NOTE_AGENT) or ''
    rows = [row for row in _cells(record.sheet.cells.get('cards')) if row['card'].verdict is Verdict.VERIFIED]
    counts = collections.Counter(row['card'].said for row in rows)
    covered = {row['card'].said for row in rows if counts[row['card'].said] == 1
               and (row['unclear'].said or joined(row['hypothesis'].said) and joined(row['hypothesis'].said) in agent_part)}
    if len(covered) < len(listed):
        return f'偏好筆記未更新({record.end.value})：有 {len(listed) - len(covered)} 張新表態沒有完整歸屬'
    try:
        changed_custom = prefs.apply_agent_note(note, cf.PREFERENCE_NOTE, base=base)
    except ValueError as error:
        return f'偏好筆記未更新：{error}'
    feedback_dump.save_feedback_checkpoint(journal_lines, cf.PREFERENCE_NOTE)
    return '偏好筆記的使用者自訂區未變；agent 嘗試修改的內容已忽略' if changed_custom else ''


def run(ask, board, browser, limit, minutes):
    """一輪找缺。看板讀不了就照實寫進狀態檔、結束碼 1,不讓這一輪停在「進行中」。"""
    import evidence
    import jobrun
    os.makedirs(tmp(), exist_ok=True)
    started = time.time()
    stamp = time.strftime('%Y-%m-%d %H:%M')

    def status(phase, **values):
        jobrun.write(os.path.join(tmp(), STATUS), dict(values, phase=phase, pid=os.getpid(), mode=ask['mode'],
                                                       direction=ask['direction'], minutes=minutes, t0=started,
                                                       graceful=True))

    try:
        bd.load(board)
    except (bd.BoardError, OSError) as error:
        message = f'看板讀不了,這一輪沒做:{error}'
        status('failed', msg=message, finished_at=time.time())
        print(message)
        return 1

    def finish(entries, judged, notes, messages, gone_rows=(), **values):
        drop = collections.Counter(row['rule'] for row in gone_rows)
        terms_missed = collections.Counter(term for row in gone_rows for term in row.get('missed', ()))
        added = _save(board, entries, {'round': stamp, 'mode': ask['mode'], 'direction': ask['direction'],
                                       'judged': judged, 'dropped': dict(drop), 'gone': list(gone_rows),
                                       'terms_missed': dict(terms_missed),
                                       'notes': notes[:600], 'seeds': list(ask['seeds'])})
        if added:
            import prefs
            prefs.refresh_like(board)
        message = '；'.join(value for value in messages if value)
        print(f'找缺：{message}' if message else '找缺：完成')
        status(values.pop('phase', 'done'), added=len(added), dropped=sum(drop.values()), msg=message,
               finished_at=time.time(), took=current.unpaused_seconds(), notes=notes[:600], **values)
        print(f'這輪新增 {len(added)} 筆職缺(在看板「🆕 待評估」)')

    status('start')
    subprocess.run([sys.executable, os.path.join(HERE, 'feedback_dump.py'), '--live', board])   # 更新他資料夾裡的逐張表態匯出
    shared, feedback, jobs = user_materials(board)
    current = Run(board, browser, minutes)
    search = current.agent(search_brief(ask, shared, jobs), {}, 4 * 3600)
    notes = joined(search.sheet.said('notes'))
    if search.end is not End.HANDED or search.sheet.broken:
        timeup = search.end is End.TIMEOUT
        why = search.sheet.broken.text if search.sheet.broken else search.end_why
        finish([], 0, notes, ['時間到，這輪沒找到' if timeup else f'搜尋 agent 沒交件（{search.end.value}）：{why}'],
               phase='done' if timeup else 'failed', found=0, timeup=timeup)
        return 0 if timeup else 1
    status('agent', step='整理新表態偏好')
    messages = [_save_website_notes(search.sheet, search.id), _update_preference_note(current)]
    candidates = {}
    for row in search.sheet.rows('candidates'):
        candidates.setdefault(joined(row['url']), {key: joined(value) for key, value in row.items()})
    candidates.pop('', None)
    have, turned = {job['id']: job for job in jobs}, turned_down()
    gone_rows = [gone(url, '板上已經有', target=have[url].get('target', ''), shot=card_shot(have[url])) if url in have else
                 gone(url, '判過不送', f"{turned[url].get('at', '')} 那輪 {turned[url].get('rule', 'agent 說不投')}:{turned[url].get('why', '')}",
                      turned[url].get('target', ''), turned[url].get('shot', ''))
                 for url in candidates if url in have or url in turned]
    urls = [url for url in candidates if url not in have and url not in turned][:limit or None]   # 跑幾張:清掉的不算
    if not urls:
        finish([], 0, notes, messages, gone_rows, found=len(candidates))
        return 0
    review_round = evidence.Round('find', 'review', urls, board)
    pages, shots = read_urls(browser, urls, review_round)
    status('agent', step='核對 agent 引的職缺頁原文')
    material = Numbered()
    judged = current.agent(judge_brief(ask, shared, material, urls, pages, feedback, jobs),
                           {'urls': frozenset(urls), 'resumes': frozenset(resume_choices())}, 3600, materials=(material,))
    rows, unmatched = rows_by_url(judged, urls)
    run_fields = {'round': stamp, 'mode': ask['mode'], 'direction': ask['direction']}
    terms, rates, entries, rejected = round_terms(ask), browser.exchange_rates(), [], []
    for url, page in zip(urls, pages):
        row, issue = rows[url]
        entry = card_of(url, candidates[url], page, row, shots[url], run_fields, issue, jobs, terms, rates)
        rule, why, missed = (None, '', []) if issue else dropped(row, entry, feedback, terms, rates)
        if rule is None:
            entries.append(entry)
            continue
        shot = entry['src']['verification'].get('screenshot_file', '')
        gone_rows.append(gone(url, rule, why, entry['target'], shot, term_values(row, rates), missed))
        # agent 說不投的記下來;照使用者設定擋的每輪重算,不記(J-13)
        if rule in AGENT_DROPS and entry['src']['verification']['state'] == 'verified':
            rejected.append({'url': url, 'at': stamp, 'mode': ask['mode'], 'rule': rule, 'why': why[:200],
                             'target': entry['target'], 'shot': shot})
    turned_add(rejected)
    incomplete = sum(issue is not None for _row, issue in rows.values())
    problems = []
    if incomplete:
        problems.append(f'有 {incomplete} 張候選的判斷交件未完整，已列為待確認'
                        + ('' if judged.end is End.HANDED else f'（判斷 agent {judged.end.value}）'))
    if unmatched:
        problems.append(f'另有 {unmatched} 列判斷交件無法配對候選')
    messages[:0] = problems
    finish(entries, len(urls) - incomplete, notes, messages, gone_rows, found=len(candidates))
    return 0


def main(argv=None, browser=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--board', default=bd.LIVE)
    parser.add_argument('--mode')
    parser.add_argument('--direction', default='')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--terms')
    parser.add_argument('--minutes', type=int)
    parser.add_argument('--seed-url', action='append', default=[])
    parser.add_argument('--seed-co', action='append', default=[])
    args = parser.parse_args(argv)
    seeds = [{'k': 'job', 'v': value} for value in args.seed_url] + [{'k': 'co', 'v': value} for value in args.seed_co]
    return run(ask_of(args.mode, args.direction, seeds, args.terms), args.board, browser or Real(), args.limit,
               args.minutes)


if __name__ == '__main__':
    raise SystemExit(main())
