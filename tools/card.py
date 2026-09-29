#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical card names and URL identifiers."""
import hashlib
import urllib.parse
import re


SOURCE_LINK_SUFFIX = re.compile(r'\s*[（(]\s*\[[^\]]+\]\([^)]*\)\s*[）)]\s*$')
MARKDOWN_LINK = re.compile(r'\[([^\]]+)\]\([^)]*\)')


def card_id_from_url(url):
    return hashlib.sha1(str(url or '').encode('utf-8')).hexdigest()[:12]


def legacy_id(url):
    return hashlib.sha1(str(url or '').encode('utf-8')).hexdigest()[:6]


def name(value, company=None):
    target = value.get('target') if isinstance(value, dict) else value
    target = str(target or '').strip()
    company = str(company or '').strip()
    if company and company.casefold() not in target.casefold():
        target = f'{target} · {company}' if target else company
    target = SOURCE_LINK_SUFFIX.sub('', target)
    target = MARKDOWN_LINK.sub(r'\1', target)
    target = re.sub(r'[*`]', '', target)
    target = re.sub(r'\s*[·・]\s*', ' · ', target)
    return re.sub(r'\s+', ' ', target).strip()


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


def name_key(value):
    return re.sub(r'[\s·・,|()（）\-–]+', '', name(value)).lower()


def with_name(value, replacement):
    target = value.get('target') if isinstance(value, dict) else value
    target = str(target or '')
    suffix = SOURCE_LINK_SUFFIX.search(target)
    return name(replacement) + (suffix.group(0) if suffix else '')


# ── 公司名(卡片分組、封鎖名單、指名要找的都用它)──────────────────────
# 看板 board/board.js 的 companyOf 是同一條規則的另一份(頁面離線、改別名要當場重畫,不能等伺服器)。
# 兩邊用 tests/fixtures/board-rule-cases.json 的 companies / company_same 鎖住:
# 這裡由 tests/test_card.py 跑,頁面那份由 board_check 的「共用規則案例表」跑。
_CO_URL = [re.compile(p, re.I) for p in (
    r'lever\.co/([^/?#]+)', r'greenhouse\.io/([^/?#]+)', r'ashbyhq\.com/([^/?#]+)',
    r'cake(?:resume)?\.(?:me|com)/companies/([^/?#]+)', r'join\.com/companies/([^/?#]+)',
    r'workable\.com/[^/]*/?([^/?#]+)')]
# 標題有「職稱 · 公司」也有「公司 · 職稱」:哪一段像職稱,另一段就是公司。看板(board.js)、
# 偏好筆記(prefs)、自動流程都用這一份;頁面從設定拿到同一張清單(board_server.page_cfg)。
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
    """去掉公司名結尾的法律字尾;去完什麼都不剩就原樣。"""
    text = str(value or '').strip()
    return _CO_SUFFIX.sub('', text).strip() or text


def same_company(a, b):
    """兩個公司名是不是同一家(封鎖名單、指名要找的舊資料存的可能是還沒去字尾的寫法)。"""
    return bool(a) and bool(b) and company_norm(a).casefold() == company_norm(b).casefold()


def company(job, alias=None, extra_titles=None):
    """卡片歸在哪一家。alias = 設定 board.company_alias(鍵是小寫),使用者自訂的寫法優先;
    extra_titles = 設定 board.title_words(使用者自己加的職稱字)。找不出來回 '其他'(看板的分組名)。"""
    title_rx = titleish(extra_titles) if extra_titles else _TITLEISH
    alias = {str(k).lower(): v for k, v in (alias or {}).items()}

    def norm(s):
        low = re.sub(r'\s+', ' ', re.sub(r'[-_]+', ' ', s.lower())).strip()
        if low in alias:
            return alias[low]
        s = re.sub(r'\s+', ' ', re.sub(r'[-_]', ' ', s)).strip()
        s = re.sub(r'\b\w', lambda m: m.group(0).upper(), s, flags=re.A)
        stripped = company_norm(s)
        return alias.get(stripped.lower(), stripped)

    url = str((job or {}).get('id') or '') if isinstance(job, dict) else ''
    for rx in _CO_URL:
        m = rx.search(url)
        if m:
            try:
                return norm(urllib.parse.unquote(m.group(1), errors='strict'))
            except UnicodeDecodeError:
                return norm(m.group(1))
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
