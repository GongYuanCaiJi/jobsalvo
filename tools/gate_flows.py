#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_flows —— 其他會叫 agent 的流程(分類建議、客製版、準備履歷)
交件單每一格登記的核對方式。這幾種沒開 agent 的瀏覽器,真相是程式交給 agent 的編號材料(程式自己印、自己編號的段落,agent 只回號碼,字由程式用同一份編號解回)和程式自己知道的事(Truth)。
入口在 gate。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cf            # noqa: E402
import prefs                   # noqa: E402
from gate_cells import CHECK, DECIDED, JUDGED, UNUSED, WORDS, Cell  # noqa: E402
from numbered import Accept  # noqa: E402

def _regex(said, row, truth):
    """比對規則:看板用它比對職稱和內文,要是寫得出來的正規表示式(空的只有「其他」可以,那一類程式自己加)。"""
    try:
        import settings_api
        settings_api.compile_match(str(said or ''))
    except (re.error, ValueError) as e:
        return f'不是正規表示式({str(e)[:60]})'
    return None


def _named(said, row, truth):
    return None if str(said or '').strip() else '沒有名字'


SUGGEST_CATS = {
    'categories.name': Cell(JUDGED, '類別', '只是建議:設定頁標明是 agent 判斷的建議,他看過、按套用才寫進設定', _named),
    'categories.icon': Cell(WORDS, '類別圖示', '只給人看'),
    'categories.match': Cell(CHECK, '比對規則', '寫得出來的正規表示式', _regex),
    'tags.name': Cell(JUDGED, '標籤', '同上,只是建議', _named),
    'tags.match': Cell(CHECK, '比對規則', '寫得出來的正規表示式', _regex),
    'why': Cell(WORDS, '這樣分的理由', '只給人看'),
}

def _feedback_ids(said, row, truth):
    """歸進這個問題的回饋:都要是這一輪交給它的,而且至少兩張卡提到(一張卡提過的不算重複的問題)。"""
    given = truth.feedback
    ids = said if isinstance(said, list) else []
    stray = [str(i) for i in ids if i not in given]
    if stray or not ids:
        return f'這一輪沒給它 {"、".join(stray[:3])} 這幾則回饋' if stray else '沒有列出是哪幾則回饋'
    cards = {given[i].get('url') for i in ids}
    return None if len(cards) >= 2 else '只有一張卡提到(至少兩張卡都提到才算重複的問題)'


def _occurrences(said, row, truth):
    ids = row.get('feedback_ids') if isinstance(row.get('feedback_ids'), list) else []
    return None if said in (None, '') or str(said) == str(len(ids)) else f'{len(ids)}(程式照它列的回饋數的)'


CUSTOMIZE = {
    'reports.issue': Cell(JUDGED, '同一個問題', '哪幾則回饋算同一個問題是 agent 判斷:回報裡標明、附那幾則回饋的原文;'
                          '他在回報按已處理就收掉', _named),
    'reports.recommendation': Cell(WORDS, '建議', '照原文放進回報給他看'),
    'reports.feedback_ids': Cell(CHECK, '歸進來的回饋', '都是這一輪交給它的,而且至少兩張卡提到', _feedback_ids),
    'reports.occurrences': Cell(DECIDED, '提到幾次', '程式照它列的回饋數(agent 不用寫;寫了要一樣)', _occurrences),
}

def CARD(row, truth):
    """準備履歷一張卡一張交件單:只收這張卡的 JD 原文。"""
    return {truth.url}


def _resume_pick(said, sheet, truth):
    """挑哪一份是 agent 判斷;只核對是勾選的、有那個語言檔的那幾份之一。"""
    if sheet.get('skip'):
        return UNUSED
    if not str(said or '').strip():
        return None                               # 沒挑:流程自己照「沒挑到可用的履歷」處理
    if not prefs.checked_resumes(truth.resumes):
        return UNUSED                             # 沒有勾選的履歷,指示叫它不要挑:它寫了什麼都不用
    if prefs.valid_resume_pick(str(said or '').strip(), sheet.get('lang'), truth.resumes):
        return None
    return f'不是勾選、有 {sheet.get("lang") or "那個語言"} 檔的履歷'


def _jd_lang(said, sheet, truth):
    """JD 是哪種語言是 agent 判斷(程式不猜):只核對是設定裡的其中一個語言。"""
    if sheet.get('skip') or not str(said or '').strip():
        return UNUSED if sheet.get('skip') else None
    return None if said in cf.LANGS else f'只能是 {"、".join(cf.LANGS)} 其中一個'


def _skip(said, sheet, truth):
    if said is True and not str(sheet.get('reason') or '').strip():
        return '跳過卻沒寫原因'
    return None


_PICK = '挑哪一份是 agent 判斷(卡上寫「🤖 判」、附挑選理由,他可以改);只核對是勾選、有那個語言檔的'
PREPARE = {
    'resume': Cell(JUDGED, '挑的履歷', _PICK, _resume_pick),
    'variant': Cell(JUDGED, '挑的履歷(舊寫法)', _PICK, _resume_pick),
    'lang': Cell(JUDGED, '語言', 'JD 是哪種語言是 agent 判斷;只核對是設定裡的語言', _jd_lang),
    'why': Cell(WORDS, '挑選理由', '卡上照原文給人看'),
    'content_problem': Cell(JUDGED, '履歷內容問題', '卡上標明是 agent 判斷,只是提示(不擋、不放行)'),
    'real_title': Cell(CHECK, '頁面上的名字', '寫出名字、附這張卡 JD 原文那幾段的編號;名字要一字不差在那幾段裡(才拿來改卡片名字)',
                       items=('text', 'cite'), cite={'cite': Accept(owner=CARD, value='text')}),
    'skip': Cell(JUDGED, '跳過', '跳過與原因是 agent 判斷,照原文顯示在卡上', _skip),
    'reason': Cell(WORDS, '跳過的原因', '照原文顯示在卡上'),
}
