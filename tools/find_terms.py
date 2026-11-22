#!/usr/bin/env python3
"""找缺條件:設定裡的條件怎麼存、存之前怎麼檢查。判斷時原樣交給 agent(tools/find.py)。"""

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

