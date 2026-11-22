"""照 docs/research/601-behaviors.md 推預期值;不 import 被考的程式(web/ 核心與四個任務檔)。"""
from collections import Counter

import acceptance_inputs as I

# A-27:每一句的來源。句子代碼照行為清單引的(起點 tools/web/marks.py 的 TEMPLATES)。
PROGRAM, AGENT, NOT_COMPARED, USER = '程式讀到', 'agent 說的', '程式沒比到', '你的設定'
SOURCE = {
    **dict.fromkeys(('AGENT_STOPPED', 'NEEDS_USER', 'AGENT_WORDS', 'SAID_VS_ACTUAL'), AGENT),
    **dict.fromkeys(('NOT_COMPARED', 'NO_BEFORE', 'NO_AFTER', 'STEPS_BEFORE_FIRST', 'WORKSPACE_UNREAD', 'SHOT_MISSING',
                     'PAGE_UNREADABLE', 'PAGE_NO_TEXT', 'PAGE_TOO_LONG', 'SAME_TAB_OTHER_URL', 'OTHER_TAB_SAME_URL',
                     'ONLY_BEFORE', 'ONLY_AFTER', 'SIDE_UNREADABLE', 'FORM_PAGE_UNKNOWN'), NOT_COMPARED),
    **dict.fromkeys(('STANDING_NEVER', 'STANDING_UNREVIEWED', 'STANDING_OK', 'STANDING_BEHIND', 'STANDING_ANSWERS'),
                    USER),
}


def source_of(code):
    return SOURCE.get(code, PROGRAM)


def page_marks(tab):
    """A-04、A-06:一頁自己的標記。"""
    marks = {I.UNREADABLE: {'PAGE_UNREADABLE'}, I.TOO_LONG: {'PAGE_TOO_LONG'}, I.BLANK: {'PAGE_NO_TEXT'},
             I.NO_LABEL: {'PAGE_UNREADABLE', 'SHOT_MISSING'}, I.READ: set()}[tab.state]
    return marks | ({'SHOT_MISSING'} if not tab.shot else set())


def read(tab):
    return tab.state == I.READ


def pairing(before, after):
    """A-05、A-08–A-12:改前那一刻、改後那一刻的分頁 → 每一頁一列 (那一頁的 tab_id, 那一頁的網址, 逐處比了沒, 原因代碼,
    這一列的兩邊 (改前 tab_id, 改前網址, 改後 tab_id, 改後網址, 兩頁自己的標記))。
    before/after 是分頁們、() 空工作區、None 整個工作區讀不到、或 'none' 沒叫拍照。"""
    def sides(o, n):
        return (o.tab_id if o else '', o.url if o else '', n.tab_id if n else '', n.url if n else '',
                tuple(sorted({code for tab in (o, n) if tab for code in page_marks(tab)})))
    rows = []
    for side, tabs in (('before', before), ('after', after)):
        if tabs is None:
            rows.append((side, '', '', False, 'WORKSPACE_UNREAD', sides(None, None)))
    old = list(before or ()) if before != 'none' else []
    new = list(after or ()) if after != 'none' else []
    paired = [(o, n) for o in old for n in new if o.tab_id == n.tab_id and o.url == n.url]
    used_old = {id(o) for o, _n in paired}
    used_new = {id(n) for _o, n in paired}
    for o, n in paired:
        if read(o) and read(n):
            rows.append(('pair', n.tab_id, n.url, True, '', sides(o, n)))
        else:
            rows += [('before', o.tab_id, o.url, False, 'SIDE_UNREADABLE', sides(o, n)),
                     ('after', n.tab_id, n.url, False, 'SIDE_UNREADABLE', sides(o, n))]
    related_old, related_new = set(), set()
    for o in old:
        for n in new:
            if id(o) in used_old and id(n) in used_new:
                continue
            if o.tab_id == n.tab_id and o.url != n.url:
                code = 'SAME_TAB_OTHER_URL'
            elif o.tab_id != n.tab_id and o.url == n.url:
                code = 'OTHER_TAB_SAME_URL'
            else:
                continue
            related_old.add(id(o))
            related_new.add(id(n))
            rows += [('before', o.tab_id, o.url, False, code, sides(o, n)), ('after', n.tab_id, n.url, False, code, sides(o, n))]
    rows += [('before', o.tab_id, o.url, False, 'ONLY_BEFORE', sides(o, None)) for o in old
             if id(o) not in used_old | related_old]
    rows += [('after', n.tab_id, n.url, False, 'ONLY_AFTER', sides(None, n)) for n in new
             if id(n) not in used_new | related_new]
    return rows


def _common(a, b):
    """兩串段落最長的共同子序列有幾段。"""
    row = [0] * (len(b) + 1)
    for x in a:
        prev, row = row, [0]
        for j, y in enumerate(b):
            row.append(prev[j] + 1 if x == y else max(prev[j + 1], row[j]))
    return row[-1]


def diff_ok(before_parts, after_parts, removed, added):
    """A-13、A-14、A-26:列出的消失段與新出現段是照段落順序的逐處比:拿掉消失的、補上新出現的就是改後,
    而且兩邊共同的段一段都沒列(列的段數 = 各自段數 - 最長共同子序列)。共同子序列不只一種時哪一種都算對。"""
    old, new = Counter(before_parts), Counter(after_parts)
    common = _common(list(before_parts), list(after_parts))
    return (removed <= old and added <= new and old - removed == new - added
            and sum(removed.values()) == len(before_parts) - common)


def relation_tabs(relation, before_state, after_state, before_shot, after_shot, before_parts=('甲',),
                  after_parts=('乙',)):
    """一種關係 → (改前那一刻的分頁們, 改後那一刻的分頁們)。只有改前、只有改後:另一邊是空工作區。"""
    from fake_browser import Tab
    u1, u2 = 'https://jobs.example/profile/1', 'https://jobs.example/profile/1?tab=2'
    old = Tab('p1', u1, before_state, tuple(before_parts), before_shot)
    other = {I.SAME: ('p1', u1), I.SAME_TAB_OTHER_URL: ('p1', u2), I.OTHER_TAB_SAME_URL: ('p2', u1),
             I.ONLY_BEFORE: None, I.ONLY_AFTER: ('p2', u2)}[relation]
    new = Tab(*other, after_state, tuple(after_parts), after_shot) if other else None
    if relation == I.ONLY_BEFORE:
        return (old,), ()
    if relation == I.ONLY_AFTER:
        return (), (new,)
    return (old,), (new,)
