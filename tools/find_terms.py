#!/usr/bin/env python3
"""找缺條件:使用者在找缺面板填的數字(空著 = 不限),這一輪的硬規則。

  上架天數、應徵人數 → 程式讀得到(page_fetch 抓頁時的上架日、應徵人數),判斷前就過濾,不花 agent
  薪資、經驗、地點   → 判斷的 agent 讀出值、引職缺頁原文那幾段的編號(安檢門照編號取原文),程式比
頁面查不到的那一項:預設留著、卡上標出來;使用者勾了「查不到就排除」(strict)才丟。
"""
import re

# 欄位 → (看板上的名字, 給 agent 看的說法)
TERMS = {
    'posted_days': ('上架天數', '上架最多 {} 天'),
    'applicants_max': ('應徵人數', '應徵最多 {} 人'),
    'salary_min': ('薪資', '月薪至少新台幣 {} 元(年薪除以 12)'),
    'exp_max': ('經驗', '要求的經驗最多 {} 年'),
    'location': ('地點', '地點符合「{}」'),
}
NUMBERS = ('posted_days', 'applicants_max', 'salary_min', 'exp_max')
JUDGED = ('salary_min', 'exp_max', 'location')     # 要 agent 從職缺頁原文讀的那幾項
# 判斷交件單 terms 裡每一項:值的欄位、引原文段落編號的欄位、要它寫什麼
FIELDS = {
    'salary_min': ('salary_month', 'salary_quote', '新台幣月薪下限的數字(年薪除以 12、外幣照當天匯率換算);原文沒寫留 null'),
    'exp_max': ('exp_years', 'exp_quote', '要求的最少年資數字(不拘寫 0);原文沒寫留 null'),
    'location': ('location', 'location_quote', '「符合」「不符」或「看不出」'),
}
LIMITS = {'posted_days': 3650, 'applicants_max': 100000, 'salary_min': 10000000, 'exp_max': 60}
LOW = {'posted_days': 1, 'applicants_max': 0, 'salary_min': 1, 'exp_max': 0}   # 經驗 0 = 只要經歷不拘;應徵 0 = 還沒人應徵


def normalize(raw):
    """設定裡的條件 → {欄位: 值}(只留有填的)+ strict:[查不到就排除的欄位]。壞值當沒填。"""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key in NUMBERS:
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and LOW[key] <= value <= LIMITS[key]:
            out[key] = value
    location = ' '.join(str(raw.get('location') or '').split())[:80]
    if location:
        out['location'] = location
    strict = raw.get('strict') if isinstance(raw.get('strict'), list) else []
    out['strict'] = [k for k in TERMS if k in strict and k in out]
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
    strict = raw.get('strict')
    if strict is not None and (not isinstance(strict, list) or any(k not in TERMS for k in strict)):
        return '「查不到就排除」的欄位不對'
    return ''


def judge_fields(keys):
    """判斷交件單要多交的 terms 物件格式(只列這輪有設的項目)。"""
    parts = []
    for k in keys:
        value, quote, what = FIELDS[k]
        parts.append(f'"{value}":{what},"{quote}":[這一張 JD 原文寫這一項的那幾段的編號,沒寫留 []]')
    return '"terms":{' + ','.join(parts) + '},'


def active(terms):
    return [k for k in TERMS if k in terms]


def describe(terms):
    """給 agent 看的條件,一行一項。"""
    return '\n'.join('- ' + TERMS[k][1].format(terms[k]) for k in active(terms))


def _days_since(day, today):
    import datetime
    try:
        return (today - datetime.date.fromisoformat(str(day)[:10])).days
    except ValueError:
        return None


def crowd_max(label):
    """應徵人數字面上的上限:「兩週內 6~10 人」→10、「不到 25 人」→24、「200 人以上」→None(沒有上限)、查不到 → None。"""
    label = str(label or '')
    numbers = [int(n) for n in re.findall(r'\d+', label)]
    if label.startswith('還沒'):
        return 0
    if not numbers or '以上' in label:
        return None
    if label.startswith('不到'):
        return numbers[0] - 1
    return numbers[-1]


def _verdict(key, ok, terms, misses, unknown):
    """ok:True 符合 / False 不符 / None 查不到。"""
    if ok is False:
        misses.append(TERMS[key][0])
    elif ok is None:
        (misses if key in terms['strict'] else unknown).append(TERMS[key][0])


def check_page(c, terms, today=None):
    """程式讀得到的那幾項(上架天數、應徵人數)。回 (不符的項目, 查不到的項目)。"""
    import datetime
    today = today or datetime.date.today()
    misses, unknown = [], []
    if 'posted_days' in terms:
        n = _days_since(c.get('posted_at'), today) if c.get('posted_at') else None
        _verdict('posted_days', None if n is None else n <= terms['posted_days'], terms, misses, unknown)
    if 'applicants_max' in terms:
        label = c.get('applicants')
        top = crowd_max(label)
        ok = None if not label else (False if top is None else top <= terms['applicants_max'])
        _verdict('applicants_max', ok, terms, misses, unknown)
    return misses, unknown


def check_judged(said, terms):
    """agent 讀出來的那幾項(薪資、經驗、地點),引的段落編號已由安檢門核對過。回 (不符的項目, 查不到的項目)。"""
    said = said if isinstance(said, dict) else {}
    misses, unknown = [], []

    def number(key):
        try:
            return float(said.get(key)) if said.get(key) not in (None, '') else None
        except (TypeError, ValueError):
            return None
    if 'salary_min' in terms:
        n = number('salary_month')
        _verdict('salary_min', None if n is None else n >= terms['salary_min'], terms, misses, unknown)
    if 'exp_max' in terms:
        n = number('exp_years')
        _verdict('exp_max', None if n is None else n <= terms['exp_max'], terms, misses, unknown)
    if 'location' in terms:
        v = str(said.get('location') or '').strip()
        _verdict('location', {'符合': True, '不符': False}.get(v), terms, misses, unknown)
    return misses, unknown
