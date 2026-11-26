#!/usr/bin/env python3
"""找缺條件與「一定不要」:設定怎麼存、存之前怎麼檢查,以及程式怎麼拿核對過的值比使用者的設定。

agent 讀頁、引段交值(tools/find.py 的判斷交件單);值比不比得過使用者的設定由這裡判,不交給 agent(ADR-0013)。"""
import datetime
import re

import config

# 欄位 → (看板上的名字, 給 agent 看的說法)
TERMS = {
    'posted_days': ('上架天數', '上架最多 {} 天'),
    'applicants_max': ('應徵人數', '應徵最多 {} 人'),
    'salary_min': ('薪資', '月薪至少新台幣 {} 元(年薪除以 12)'),
    'exp_max': ('經驗', '要求的經驗最多 {} 年'),
    'location': ('地點', '地點符合「{}」'),
}
NUMBERS = ('posted_days', 'applicants_max', 'salary_min', 'exp_max')
LIMITS = {'posted_days': 3650, 'applicants_max': 100000, 'salary_min': 10000000, 'exp_max': 60}
LOW = {'posted_days': 1, 'applicants_max': 0, 'salary_min': 1, 'exp_max': 0}   # 經驗 0 = 只要經歷不拘;應徵 0 = 還沒人應徵


def normalize(raw):
    """設定裡的條件 → {欄位: 值}(只留有填的)。壞值當沒填。"""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key in NUMBERS:
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and LOW[key] <= value <= LIMITS[key]:
            out[key] = value
    location = ' '.join(str(raw.get('location') or '').split())[:80]
    if location:
        out['location'] = location
    return out


def problem(raw):
    """存設定前的檢查:回問題;沒問題回空字串。"""
    if raw in (None, ''):
        return ''
    if not isinstance(raw, dict):
        return '找缺條件格式不對'
    for key in NUMBERS:
        value = raw.get(key)
        if value in (None, ''):
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not LOW[key] <= value <= LIMITS[key]:
            return f'{TERMS[key][0]}要寫 {LOW[key]}~{LIMITS[key]} 的整數,空著是不限'
    if len(str(raw.get('location') or '')) > 80:
        return '地點最多 80 個字'
    return ''


# 應徵人數沒有上限(頁上寫「200 人以上」這種):agent 寫這幾個字,程式當成超過任何上限
NO_CAP = '沒有上限'


PERIODS = {'月': 1, '年': 12}


def twd_month(amount, currency, period, rates):
    """agent 照頁上原文交的薪資金額換成新台幣月薪:年薪除以 12,外幣照今天的匯率(rates:一元新台幣換多少外幣)。
    幣別不認得、不是月薪或年薪(時薪、日薪)、沒有那個幣別的匯率就回 None,這一項算查不到。"""
    currency = str(currency or '').strip().upper()     # agent 自己寫的代號(不是頁上的字):usd、 TWD 都認
    rate = 1 if currency == 'TWD' else rates.get(currency)
    if not rate or period not in PERIODS:
        return None
    return amount / rate / PERIODS[period]      # 不先取整:89,999.5 不能混過下限 90,000;看板顯示時才取整


def crowd_max(said):
    """agent 給的應徵人數上限:整數;NO_CAP 回無限大;其他寫法(原文、範圍)回 None(查不到),程式不從字裡挑數字。"""
    if isinstance(said, int) and not isinstance(said, bool):
        return said if said >= 0 else None
    value = str(said or '').strip()
    if value == NO_CAP:
        return float('inf')
    return int(value) if re.fullmatch(r'[0-9]+', value) else None


def _days_since(day, today):
    """只認 YYYY-MM-DD;其他寫法(20260901、2026-W40-1、後面多字)比不了,算查不到。"""
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', str(day)):
        return None
    try:
        return (today - datetime.date.fromisoformat(str(day))).days
    except ValueError:
        return None


def check_judged(said, terms, today=None):
    """拿核對過的值比這輪條件,回 (不符的條件, 查不到的條件),條件照看板上的名字。
    said 只放核對過的值:posted_at(YYYY-MM-DD)、applicants(crowd_max 認得的寫法)、salary_month、exp_years、
    location(「符合」「不符」);缺的就是查不到,不硬排除(J-07)。"""
    said = said if isinstance(said, dict) else {}
    today = today or datetime.date.today()
    days = _days_since(said['posted_at'], today) if said.get('posted_at') else None
    top = crowd_max(said.get('applicants'))
    ok = {
        'posted_days': lambda limit: None if days is None else days <= limit,
        'applicants_max': lambda limit: None if top is None else top <= limit,
        'salary_min': lambda limit: None if said.get('salary_month') is None else said['salary_month'] >= limit,
        'exp_max': lambda limit: None if said.get('exp_years') is None else said['exp_years'] <= limit,
        'location': lambda _place: {'符合': True, '不符': False}.get(said.get('location')),
    }
    verdicts = {key: ok[key](terms[key]) for key in TERMS if key in terms}
    return ([TERMS[key][0] for key, value in verdicts.items() if value is False],
            [TERMS[key][0] for key, value in verdicts.items() if value is None])


def _expand_lookbehind_alternatives(pattern):
    def class_end(text, start):
        i = start + 1
        if i < len(text) and text[i] == '^':
            i += 1
        if i < len(text) and text[i] == ']':
            i += 1
        while i < len(text):
            if text[i] == '\\':
                i += 2
            elif text[i] == ']':
                return i
            else:
                i += 1
        return None

    def group_end(text, start):
        depth = 1
        i = start
        while i < len(text):
            ch = text[i]
            if ch == '\\':
                i += 2
            elif ch == '[':
                end = class_end(text, i)
                if end is None:
                    return None
                i = end + 1
            elif ch == '(':
                depth += 1
                i += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return i
                i += 1
            else:
                i += 1
        return None

    def alternatives(body):
        parts = []
        start = 0
        depth = 0
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == '\\':
                i += 2
            elif ch == '[':
                end = class_end(body, i)
                if end is None:
                    return [body]
                i = end + 1
            elif ch == '(':
                depth += 1
                i += 1
            elif ch == ')':
                depth -= 1
                i += 1
            elif ch == '|' and depth == 0:
                parts.append(body[start:i])
                start = i + 1
                i += 1
            else:
                i += 1
        if not parts:
            return [body]
        parts.append(body[start:])
        return parts

    def expand(text):
        out = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == '\\':
                out.append(text[i:i + 2])
                i += 2
                continue
            if ch == '[':
                end = class_end(text, i)
                if end is None:
                    out.append(text[i:])
                    break
                out.append(text[i:end + 1])
                i = end + 1
                continue
            if text.startswith(('(?<=', '(?<!'), i):
                opener = text[i:i + 4]
                end = group_end(text, i + len(opener))
                if end is not None:
                    branches = alternatives(text[i + len(opener):end])
                    if len(branches) > 1:
                        branches = [expand(branch) for branch in branches]
                        if opener == '(?<=':
                            out.append('(?:' + '|'.join(f'(?<={branch})' for branch in branches) + ')')
                        else:
                            out.append(''.join(f'(?<!{branch})' for branch in branches))
                    else:
                        out.append(text[i:end + 1])
                    i = end + 1
                    continue
            out.append(ch)
            i += 1
        return ''.join(out)

    return expand(pattern)


def compile_match(pattern):
    """Compile the Python approximation of a board RegExp (case-insensitive).

    The settings page does the authoritative syntax check with the browser's
    RegExp constructor before POSTing. This server-side check catches Python
    extensions that would otherwise pass Python but silently fail in the
    board, while translating JavaScript named groups for Python's validator.
    """
    pattern = _expand_lookbehind_alternatives(str(pattern))
    out = []
    i = 0
    in_class = False
    while i < len(pattern):
        ch = pattern[i]
        if ch == '\\':
            if i + 1 >= len(pattern):
                out.append(ch)
                i += 1
                continue
            nxt = pattern[i + 1]
            if not in_class:
                if nxt in ('A', 'Z'):
                    raise ValueError(r'Python 的 \A、\Z 錨點不是看板正規式語法')
                if pattern.startswith((r'\N{', r'\g<'), i):
                    raise ValueError('Python 專用跳脫不是看板正規式語法')
                if pattern.startswith(r'\k<', i):
                    m = re.match(r'\\k<([A-Za-z_][A-Za-z0-9_]*)>', pattern[i:])
                    if not m:
                        raise ValueError('命名群組參照格式不對')
                    out.append(f'(?P={m.group(1)})')
                    i += len(m.group(0))
                    continue
            out.extend((ch, nxt))
            i += 2
            continue

        if ch == '[':
            in_class = True
        elif ch == ']' and in_class:
            in_class = False

        if not in_class and pattern.startswith('(?', i):
            if pattern.startswith(('(?P<', '(?P='), i):
                raise ValueError('Python 命名群組不支援；請改用 JavaScript 的 (?<name>...)')
            if pattern.startswith(('(?:', '(?=', '(?!', '(?<=', '(?<!'), i):
                prefix = next(p for p in ('(?<=', '(?<!', '(?:', '(?=', '(?!')
                              if pattern.startswith(p, i))
                out.append(prefix)
                i += len(prefix)
                continue
            m = re.match(r'\(\?<([A-Za-z_][A-Za-z0-9_]*)>', pattern[i:])
            if m:
                out.append(f'(?P<{m.group(1)}>')
                i += len(m.group(0))
                continue
            raise ValueError('Python 專用群組語法不是看板正規式語法')

        if not in_class and ch in '*+?}' and i + 1 < len(pattern) and pattern[i + 1] == '+':
            raise ValueError('Python 專用 possessive quantifier 不是看板正規式語法')
        out.append(ch)
        i += 1
    return re.compile(''.join(out), re.IGNORECASE | re.ASCII)


def excluded(title):
    """使用者設定的「一定不要」(search.exclude_words 一行一個字、進階 exclude_title 正規式):核對過的職稱一出現就不送。"""
    search = config.C.get('search') or {}
    parts = [re.escape(str(word).strip()) for word in search.get('exclude_words') or () if str(word).strip()]
    flags = re.I
    if (search.get('exclude_title') or '').strip():
        compiled = compile_match(search['exclude_title'].strip())
        parts.append(compiled.pattern)
        flags = compiled.flags
    return bool(parts and title and re.search('|'.join(parts), title, flags))
