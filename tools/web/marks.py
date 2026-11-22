"""使用者看得到的事實與句子集中在一張字表。"""
from __future__ import annotations

import dataclasses
import json
from enum import Enum


class Source(str, Enum):
    PROGRAM = '程式讀到'
    AGENT = 'agent 說的'
    NOT_COMPARED = '程式沒比到'
    USER = '你的設定'


@dataclasses.dataclass(frozen=True)
class Mark:
    code: str
    source: Source
    params: tuple[tuple[str, str], ...] = ()
    text: str = ''


TEMPLATES = {
    'PAGE_UNREADABLE': '{where}讀不到：{why}', 'PAGE_NO_TEXT': '{where}讀不出字：{why}',
    'PAGE_TOO_LONG': '{where}超過讀取上限：{why}', 'SHOT_MISSING': '{where}截圖沒取得：{why}',
    'WORKSPACE_EMPTY': '工作區沒有可拍的分頁', 'WORKSPACE_UNREAD': '{side}整個工作區程式沒比到：{why}',
    'ENDED': '這一輪{end}',
    'NOT_STARTED': '這一輪沒有啟動：{why}', 'AGENT_STOPPED': 'agent 停下：{why}',
    'NEEDS_USER': '需要你本人處理：{why}', 'SAID_VS_ACTUAL': 'agent 說「{said}」，程式讀到「{actual}」',
    'CITE_BAD': 'agent 引的段落無法核對：{why}', 'CITE_NONE': 'agent 沒有引段',
    'AGENT_WORDS': 'agent 說：{words}', 'SHEET_BROKEN': '交件單無法讀取：{why}',
    'NOTHING_TO_SYNC': '原始履歷沒有可同步的文字變更，沒派 agent', 'NO_BEFORE': '沒有拍改前',
    'NO_AFTER': '沒有拍改後,程式沒比對改後', 'BEFORE_TAKEN': '已拍改前', 'AFTER_TAKEN': '已拍改後', 'STEPS_BEFORE_FIRST': '拍改前之前的瀏覽器步驟：{steps}',
    'AFTER_MEANS': '改後那一刻讀到的頁面', 'PAGES_COMPARED': '逐處比較 {count} 個分頁',
    'SAME_TAB_OTHER_URL': '同一分頁網址不同，沒逐處比', 'OTHER_TAB_SAME_URL': '不同分頁網址相同，沒逐處比',
    'ONLY_BEFORE': '只有改前，沒比到', 'ONLY_AFTER': '只有改後，沒比到',
    'SIDE_UNREADABLE': '有一邊讀不到，沒逐處比：{why}', 'FOUND_VERBATIM': '原稿一字不差找到',
    'ALREADY_THERE': '原本就有', 'NOT_FOUND_VERBATIM': '原稿沒在頁上一字不差找到',
    'NOT_COMPARED': '程式沒比到', 'REMOVED_READ': '刪除內容改前{before}、改後{after}',
    'ON_PAGE_NOT_COMPARED': '原稿在頁上一字不差讀到，那一頁沒逐處比',
    'PAGE_CHANGED_SINCE_FILL': '填好後頁面有變化，列出供你查看', 'SUBMIT_UNKNOWN': '送出結果不明',
    'CHECKED_ON': '查過：{date}', 'SOURCE_UNREAD': '本輪沒查過來源「{name}」：{why}',
    'FIELD_SIDE_BY_SIDE': 'agent 說「{said}」；引文：{quote}', 'FIELD_NOT_ON_PAGE': 'agent 說頁上沒寫',
    'FIELD_UNREAD_PAGE': '頁面讀不到，欄位由 agent 判斷', 'TERM_UNCHECKED': '條件「{term}」沒有核對到',
    'STANDING_NEVER': '還沒同步', 'STANDING_UNREVIEWED': '最新清單你還沒看過',
    'STANDING_OK': '已跟上', 'STANDING_BEHIND': '原始履歷已落後', 'STANDING_ANSWERS': '代填答案等你確認',
    'CLAIM_MATCH': 'agent 說的改動與程式讀到的相符',
    'CLAIM_MISMATCH': 'agent 說「{said}」，程式讀到「{actual}」',
    'CLAIM_UNCHECKED': 'agent 說的改動沒有可比較的頁面',
    'ROW_UNLISTED': '判斷交件未列這一頁', 'ROW_TWICE': '判斷交件重複列了這一頁',
    'ROW_FIELD_BAD': '這一列的 {field} 欄無法核對：{why}',
    'HANDED_OVER': '要你本人處理的那一頁({page})程式重讀核對到 agent 引的原文，交給你的是這一頁',
    'HANDOFF_UNVERIFIED': '要你本人處理的那一頁沒有交接：{why}',
    'NO_FIXED_PROFILE_PAGE': '沒有固定履歷頁',
    'FORM_PAGE_UNKNOWN': 'agent 沒寫填表的是哪個分頁，或那個分頁不在最後一次拍改後裡，程式不知道是哪一頁',
    'HANDED_QUOTE_UNMATCHED': '要你本人處理的那一頁({page})交給你了，程式重讀沒核對到 agent 引的原文：{why}',
}

SOURCE = {code: Source.PROGRAM for code in TEMPLATES}
SOURCE.update({code: Source.AGENT for code in ('AGENT_STOPPED', 'NEEDS_USER', 'AGENT_WORDS', 'SAID_VS_ACTUAL')})
SOURCE.update({code: Source.NOT_COMPARED for code in ('NOT_COMPARED', 'NO_BEFORE', 'NO_AFTER', 'STEPS_BEFORE_FIRST',
                                                       'WORKSPACE_UNREAD', 'SHOT_MISSING', 'PAGE_UNREADABLE',
                                                       'PAGE_NO_TEXT', 'PAGE_TOO_LONG',
                                                       'SAME_TAB_OTHER_URL', 'OTHER_TAB_SAME_URL', 'ONLY_BEFORE',
                                                       'ONLY_AFTER', 'SIDE_UNREADABLE', 'NO_FIXED_PROFILE_PAGE',
                                                       'FORM_PAGE_UNKNOWN')})
SOURCE.update({code: Source.USER for code in ('STANDING_NEVER', 'STANDING_UNREVIEWED', 'STANDING_OK',
                                               'STANDING_BEHIND', 'STANDING_ANSWERS')})


def say(code: str, source: Source | str | None = None, **params) -> Mark:
    selected = Source(source) if source is not None else SOURCE[code]
    values = tuple((key, str(value)) for key, value in params.items())
    return Mark(code, selected, values, TEMPLATES[code].format(**dict(values)))


def words(value) -> tuple[str, ...]:
    """agent 交件單上的一個值照原字取出:字串照原字,其他(清單、物件、數字)照它寫的整個 JSON,不拆、不組句子(A-30)。"""
    if value is None or value == '':
        return ()
    return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),)


def joined(value) -> str:
    """agent 交件單上的一個值放進只收一段字的地方(卡上的一欄、程式的一句話):逐項原字,用「、」隔開。"""
    return '、'.join(words(value))


def say_each(code: str, value, key: str, **params) -> tuple[Mark, ...]:
    """agent 交件單上的值畫成標記:每一項原字各一個標記。"""
    return tuple(say(code, **{key: word}, **params) for word in words(value))

