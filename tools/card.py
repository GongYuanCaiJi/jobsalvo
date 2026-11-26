#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical card names and URL identifiers."""
import functools
import hashlib
import re


def card_id_from_url(url):
    return hashlib.sha1(str(url or '').encode('utf-8')).hexdigest()[:12]


def legacy_id(url):
    return hashlib.sha1(str(url or '').encode('utf-8')).hexdigest()[:6]


def name(value, company=None):
    from card_title import name as display_name
    return display_name(value, company)


def legacy_score_name_parts(value, title_keywords):
    """Return the original title and company text used by like-score ranking."""
    target = value.get('target') if isinstance(value, dict) else value
    title = re.sub(r'（\[.*', '', target or '').strip()
    cleaned = re.sub(r'\[|\]\([^)]*\)|\*\*|[（(][^）)]*[）)]', '', title)
    segments = [part.strip() for part in re.split(r'[·・]', cleaned) if part.strip()]
    company = ''
    if len(segments) >= 2:
        if title_keywords.search(segments[-1]) and not title_keywords.search(segments[0]):
            company = segments[0][:40]
        else:
            company = segments[-1][:40]
    return title, company


def with_name(value, replacement):
    from card_title import with_name as display_with_name
    return display_with_name(value, replacement)


# ── 公司名(卡片分組、封鎖名單、指名要找的都用它)──────────────────────
# 只有這一份:看板拿的是 label_jobs 算好跟著職缺送去的公司名,不自己算(#343)。
# 標題有「職稱 · 公司」也有「公司 · 職稱」:哪一段像職稱,另一段就是公司。看板(label_jobs)、
# 你的喜好(prefs)、自動流程都用這一份。
# 各行各業常見的職稱字都放;使用者在設定 board.title_words 再加自己領域的字(當一般文字比對,不是正規表示式)。
# 短的英文字前後要有字界,不然會吃到公司名(例:Agent 會中 AgentOps);常出現在公司名裡的中文字(律師、護理、會計)不放。
TITLE_WORDS = [
    # 中文
    '工程師', '專員', '經理', '副理', '協理', '襄理', '組長', '主任', '主管', '總監', '分析', '架構', '顧問', '人員',
    '幹部', '計畫', '培育', '儲備', '規劃', '企劃', '監控', '視覺', '防制', '科學家', '研究員', '研發', '負責人',
    '特助', '助理', '秘書', '稽核', '助教', '店長', '技術員', '司機', '實習', '滲透', '紅隊',
    # 「…師」(教師、律師、設計師…)只認結尾:公司名常夾著這些字(範例律師事務所、範例護理之家)
    '師$',
    # 英文
    'Manager', 'Engineer', 'Analyst', 'Specialist', 'Researcher', 'Lead', 'Associate', 'Consultant', 'Developer',
    'Scientist', 'Operations', r'\bops\b', 'Planner', 'Trainee', 'Graduate', 'Builder', 'Officer', 'Program',
    'Staff', 'Intern', 'Review', 'Development', r'\bMT\b', r'\bMA\b', r'\bSA\b', r'PM\b', 'Sales', 'expert',
    'advocate', 'architect', 'investigator', 'curation', 'fellowship', r'\bgrant\b',
    'Designer', 'Director', 'Coordinator', 'Administrator', 'Assistant', 'Representative', r'\bExecutive\b',
    'Accountant', 'Auditor', 'Bookkeeper', 'Controller', 'Nurse', 'Teacher', 'Tutor', 'Instructor', 'Lecturer',
    'Professor', 'Lawyer', 'Attorney', 'Paralegal', 'Counsel', 'Writer', 'Editor', 'Copywriter', 'Translator',
    'Interpreter', 'Technician', 'Therapist', 'Pharmacist', 'Physician', 'Recruiter', 'Marketer', 'Strategist',
    r'\bProducer\b', r'\bArtist\b', 'Illustrator', 'Animator', 'Photographer', r'\bClerk\b', 'Receptionist',
    'Supervisor', r'\bHead of\b', r'\bVP\b', r'\bChief\b', 'Advisor', 'Adviser', r'\bBuyer\b', 'Cashier',
    r'\bChef\b', r'\bDriver\b', r'\bOperator\b', 'Mechanic', 'Electrician', 'Counselor', 'Economist',
    'Statistician', 'Actuary', 'Underwriter', r'\bTrader\b',
    # 資安、研究這一類
    'Bounty', r'red.?team', 'teamer', 'injection', 'prompt', 'hunter',
]


def title_words(extra=None):
    """內建清單加上使用者自己加的字(一般文字,跳脫後比對)。頁面和 Python 用同一份。"""
    return TITLE_WORDS + [re.escape(str(w).strip()) for w in (extra or []) if str(w).strip()]


def titleish(extra=None):
    return re.compile('|'.join(title_words(extra)), re.I | re.A)


_TITLEISH = titleish()
# 法律字尾:同一家常被寫成「甲科技」跟「甲科技股份有限公司」、「Acme」跟「Acme, Inc.」。
# 英文字尾前面一定要有空白或逗號(不然 Unlimited 會被切成 Un);只切結尾,不切中間。
# 「公司」「分公司」這類不切:那常是名字的一部分。中文簡稱跟全名不是機械規則,交給公司別名。
_CO_SUFFIX = re.compile(
    r'(?:[\s,，]*(?:股份有限公司|有限責任公司|有限公司)'
    r'|[\s,，]+(?:co\.?,?\s*ltd\.?|pte\.?\s*ltd\.?|inc\.?|incorporated|ltd\.?|limited|llc|l\.l\.c\.|'
    r'corp\.?|corporation|gmbh|plc))+$', re.I)


def company_norm(value):
    """舊卡(升級前、沒有 agent 引的公司欄)才用:去掉公司名結尾的法律字尾;去完什麼都不剩就原樣。"""
    text = str(value or '').strip()
    return _CO_SUFFIX.sub('', text).strip() or text


def same_company(a, b):
    """兩個公司名是不是同一家:一字不差相同。別的寫法算同一家,由使用者在公司別名(board.company_alias)列。"""
    return bool(a) and a == b


def legacy_same_company(a, b):
    """升級前的舊卡照舊:大小寫、法律字尾不同也算同一家(卡上的公司是程式從卡名切出來的,不是頁上的字)。"""
    return bool(a) and bool(b) and company_norm(a).casefold() == company_norm(b).casefold()


def cited_company(job):
    """agent 從職缺頁一字不差引的公司(找缺進板時存的);升級前的舊卡沒有,回空字串。"""
    value = job.get('company') if isinstance(job, dict) else None
    return value if isinstance(value, str) else ''


def aliased(text, alias=None):
    """使用者的公司別名:這個寫法列成哪一家就是哪一家;沒列就照原字。設定頁存的鍵是小寫。"""
    alias = alias or {}
    value = alias.get(text, alias.get(text.lower()))
    return value if isinstance(value, str) and value else text


def company(job, alias=None, extra_titles=None):
    """卡片歸在哪一家。有 agent 引的公司(cited_company)就是那串字,套使用者的公司別名;
    舊卡才從卡名切:alias = 設定 board.company_alias(鍵是小寫),使用者自訂的寫法優先;
    extra_titles = 設定 board.title_words(使用者自己加的職稱字)。找不出來回 '其他'(看板的分組名)。
    只看卡的網址和職稱:同樣的輸入記住答案(看板每算一次每張卡的下一步,每張要問好幾次,每次都是一串正規表示式)。"""
    if cited_company(job):
        return aliased(cited_company(job), alias)
    alias_items = tuple(sorted((str(k), v) for k, v in (alias or {}).items()))
    titles = tuple(extra_titles or ())
    if not all(isinstance(v, str) for _, v in alias_items) or not all(isinstance(x, str) for x in titles):
        return _company(job, alias, extra_titles)
    if isinstance(job, dict):
        url, target = str(job.get('id') or ''), job.get('target')
    else:
        url, target = '', job
    return _company_of(url, str(target or ''), alias_items, titles)


@functools.lru_cache(maxsize=8192)
def _company_of(url, target, alias_items, titles):
    return _company({'id': url, 'target': target}, dict(alias_items), list(titles))


def _company(job, alias=None, extra_titles=None):
    title_rx = titleish(extra_titles) if extra_titles else _TITLEISH
    alias = {str(k).lower(): v for k, v in (alias or {}).items()}

    def norm(s):
        low = re.sub(r'\s+', ' ', re.sub(r'[-_]+', ' ', s.lower())).strip()
        if low in alias:
            return alias[low]
        s = re.sub(r'\s+', ' ', re.sub(r'[-_]', ' ', s)).strip()
        s = re.sub(r'\b\w', lambda m: m.group(0).upper(), s)   # 字邊界照 Unicode:Straße 不會變 StraßE
        stripped = company_norm(s)
        return alias.get(stripped.lower(), stripped)

    t = re.sub(r'[\[\]]', '', name(job)).strip()
    low = t.lower()
    for key, value in alias.items():
        if key in low:
            return value
    parts = [x.strip() for x in re.split(r'·|／|\||｜', t) if x.strip()]
    if len(parts) > 1:
        for part in reversed(parts):
            if not title_rx.search(part):
                return norm(part)
        return '其他'
    if t and not title_rx.search(t):
        return company_norm(re.sub(r'^[\[（(]+', '', t))[:22]
    return '其他'


def platform(job):
    """已投遞成效的來源標記:沿用找缺存的主機名,沒有就讀職缺網址;沒有主機時保留原值或回「不明」。"""
    j = job if isinstance(job, dict) else {}
    src = j.get('src') if isinstance(j.get('src'), dict) else {}
    s = str(j.get('source_platform') or src.get('platform') or src.get('site') or j.get('platform') or '').strip()
    if not s and re.match(r'^https?://[^/?#]+', str(j.get('id') or '').strip(), re.I):
        s = str(j.get('id')).strip()
    if not s:
        return '不明'
    host = re.split(r'[/?#]', re.sub(r'^https?://', '', s, flags=re.I))[0].lower()
    host = host[4:] if host.startswith('www.') else host
    return host if '.' in host else s


def label(j):
    """卡片給人看的名字:有公司和職稱就用,沒有(例如從應徵紀錄同步進來的卡)就用卡名。"""
    return f"{j.get('company') or ''} {j.get('title') or ''}".strip() or name(j)


def lookup(q, data):
    """用 id 或卡名的一段對到唯一一張卡;對不到或對到好幾張就停下,列出對到的。"""
    jobs = [j for j in data.get('jobs') or [] if isinstance(j, dict)]
    exact = [j for j in jobs if j.get('id') == q]
    hit = exact or [j for j in jobs if q in str(j.get('id')) or q in label(j)]
    if len(hit) != 1:
        names = '、'.join(f"{label(j)} ({j.get('id')})" for j in hit[:6]) or '沒有'
        raise SystemExit(f'「{q}」要對到唯一一張卡,現在對到:{names}')
    return hit[0]


def label_jobs(jobs, alias=None, extra_titles=None):
    """送給看板的卡:每張帶上卡名(name)、公司名(co)、來源平台(src_plat)、死線那一天(dl),看板直接用,不自己算(#343)。
    有 agent 引的公司的卡,co 就是那串字(套使用者的別名);升級前的舊卡照舊:同一家(legacy_same_company)
    在舊卡之間只用一個寫法(先看到的那張)。看板拿 co 分組、比同一家就是比字串。不改原本的 list。"""
    seen, out = {}, []
    for j in jobs or []:
        if not isinstance(j, dict):
            out.append(j)
            continue
        co = company(j, alias, extra_titles)
        if not cited_company(j):
            co = seen.setdefault(company_norm(co).casefold(), co)
        out.append(dict(j, name=name(j), co=co, src_plat=platform(j), dl=deadline(j)))
    return out


VERIFIED = '程式核對過'


def claims(job):
    """卡上 agent 寫的每一個值 → 一筆紀錄 {claim, raw, state, marks}(找缺用 find._record 寫在 src.verification)。
    讀卡上 agent 寫的值一律走這一支:state 是 VERIFIED 才是事實,其他是 agent 說的、沒核對到。
    沒有紀錄的欄位(升級前的舊卡、手動加的卡)只有 sum 的原字,state 是 None:程式沒判斷過,照舊用。"""
    job = job if isinstance(job, dict) else {}
    src = job.get('src') if isinstance(job.get('src'), dict) else {}
    checked = src.get('verification') if isinstance(src.get('verification'), dict) else {}
    summary = checked.get('summary') if isinstance(checked.get('summary'), dict) else {}
    recorded = {key: record for part in (checked.get('fields'), summary.get('fields')) if isinstance(part, dict)
                for key, record in part.items() if isinstance(record, dict)}
    old = job.get('sum') if isinstance(job.get('sum'), dict) else {}
    return {**{key: {'claim': str(value), 'raw': '', 'state': None, 'marks': []}
               for key, value in old.items() if value is not None}, **recorded}


def said_each(record):
    """一筆紀錄逐項的字,跟看板 claimSaid 同一套:核對過的是主張原字;其他照紀錄的標記逐項(agent 說：…),
    沒有標記(舊卡只有 sum 的原字)才把主張標成 agent 說的;都沒有回 []。"""
    record = record if isinstance(record, dict) else {}
    claim = str(record.get('claim') or '')
    if record.get('state') == VERIFIED:
        return [claim] if claim else []
    marks = [mark for mark in record.get('marks') or [record.get('mark')]
             if isinstance(mark, dict) and mark.get('text')]
    agent_marks = [str((mark.get('params') or {}).get('words') or mark['text'].removeprefix('agent 說：'))
                   for mark in marks if mark.get('code') == 'AGENT_WORDS']
    if agent_marks:
        return [f"agent 說：{'、'.join(agent_marks)}"]
    for mark in marks:
        if mark.get('code') in ('SAID_VS_ACTUAL', 'FIELD_SIDE_BY_SIDE', 'CLAIM_MISMATCH', 'ROW_FIELD_BAD'):
            said = (mark.get('params') or {}).get('said')
            if said:
                return [f'agent 說：{said}']
    return [mark['text'] for mark in marks] or ([f'agent 說：{claim}'] if claim else [])


def said(record):
    """said_each 逐項用「、」接成看板摘要列的一段字。"""
    return '、'.join(said_each(record))


def iso_day(value):
    """agent 寫的日期:一字不差是 YYYY-MM-DD 的一天才算;其他寫法回 ''(程式不從句子裡抓日期)。"""
    import datetime
    text = value if isinstance(value, str) else ''
    try:
        return text if datetime.date.fromisoformat(text).isoformat() == text else ''
    except ValueError:
        return ''


def deadline(job):
    """死線是哪一天(YYYY-MM-DD,沒有是 ''):找缺卡的截止日紀錄程式核對過、一字不差是一天才算。
    沒有紀錄的卡照舊:有 deadline 欄就用它,升級前的舊卡從摘要的截止日認(可能寫在一句話裡:2026.9.26 前)。
    看板拿它比今天(當地時間):那一天整天都還來得及。"""
    record = claims(job).get('deadline') or {}
    if record.get('state') is not None:
        return iso_day(record['claim']) if record['state'] == VERIFIED else ''
    if 'deadline' in job:
        return iso_day(job.get('deadline'))
    m = re.search(r'(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})', str(record.get('claim') or ''))
    return '%s-%02d-%02d' % (m[1], int(m[2]), int(m[3])) if m else ''
