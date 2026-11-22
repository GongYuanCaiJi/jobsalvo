"""改前改後:配對、逐段比、原稿標記、agent 說法對照。純函式,不讀頁、不碰檔案。"""
from __future__ import annotations

import dataclasses

from web.marks import Mark, say
from web.seen import Seen as Page, Status
from web.snap import Snapshot


@dataclasses.dataclass(frozen=True)
class Change:
    id: str
    tab: str
    url: str
    removed: tuple[str, ...]
    added: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Compared:
    before: Page
    after: Page
    changes: tuple[Change, ...]

    @property
    def removed(self) -> tuple[str, ...]:
        return tuple(part for change in self.changes for part in change.removed)

    @property
    def added(self) -> tuple[str, ...]:
        return tuple(part for change in self.changes for part in change.added)


@dataclasses.dataclass(frozen=True)
class NotCompared:
    before: Page | None
    after: Page | None
    mark: Mark


Pairs = tuple[tuple[Compared, ...], tuple[NotCompared, ...]]


def baseline(befores: list[Snapshot]) -> tuple[Page, ...]:
    """逐處比的改前是第一次拍改前那一刻讀到的頁;之後才拍到的頁不拿來當基準。"""
    return min(befores, key=lambda snap: snap.seq).pages if befores else ()


def latest(afters: list[Snapshot]) -> tuple[Page, ...]:
    """拍改後以最後一次為準:那一刻沒拍到的頁(關掉、空工作區、工作區讀不到)不拿前一次的樣子補。"""
    return max(afters, key=lambda snap: snap.seq).pages if afters else ()


def _unreadable(before: Page, after: Page) -> NotCompared:
    """讀頁那一側每一頁讀不到都寫了原因(chrome_door.workspace_pages、seen.read_workspace)。"""
    why = '；'.join(page._seen_why for page in (before, after) if page._seen_status is not Status.READ)
    return NotCompared(before, after, say('SIDE_UNREADABLE', why=why))


def diff(old: tuple[str, ...], new: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """照段落順序逐處比:兩邊最長的共同子序列以外的段,改前的是消失、改後的是新出現(A-13、C-07)。
    段落換位置就是這裡少了、那裡多了;字一字不差比,不先改寫(A-26)。"""
    start = 0
    while start < min(len(old), len(new)) and old[start] == new[start]:
        start += 1
    end = 0
    while end < min(len(old), len(new)) - start and old[-1 - end] == new[-1 - end]:
        end += 1
    a, b = old[start:len(old) - end], new[start:len(new) - end]
    longest = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) - 1, -1, -1):
        for j in range(len(b) - 1, -1, -1):
            longest[i][j] = longest[i + 1][j + 1] + 1 if a[i] == b[j] else max(longest[i + 1][j], longest[i][j + 1])
    removed, added, i, j = [], [], 0, 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i, j = i + 1, j + 1
        elif longest[i + 1][j] >= longest[i][j + 1]:
            removed.append(a[i])
            i += 1
        else:
            added.append(b[j])
            j += 1
    return (*removed, *a[i:]), (*added, *b[j:])


def _diff(before: Page, after: Page, index: int) -> Compared:
    removed, added = diff(before._seen_parts, after._seen_parts)
    changes = (Change(f'{before._seen_tab}:{index}', before._seen_tab, before._seen_url, removed, added),) if removed or added else ()
    return Compared(before, after, changes)


def pair(before: tuple[Page, ...], after: tuple[Page, ...]) -> Pairs:
    """只以 (tab,url) 完全相等配對、逐處比。沒逐處比的頁跟另一邊每一頁的關係都照列(同分頁不同網址、不同分頁同網址),
    一筆關係都沒有才是只有改前或只有改後;兩頁都已逐處比過就不另列。沒有分頁編號的不是一頁,是整個工作區讀不到。"""
    old = [page for page in before if page._seen_tab]
    new = [page for page in after if page._seen_tab]
    compared: list[Compared] = []
    not_compared: list[NotCompared] = [
        NotCompared(page if side == '改前' else None, page if side == '改後' else None,
                    say('WORKSPACE_UNREAD', side=side, why=page._seen_why))
        for side, pages in (('改前', before), ('改後', after)) for page in pages if not page._seen_tab]
    new_by_key = {(page._seen_tab, page._seen_url): i for i, page in enumerate(new)}
    paired_old: set[int] = set()
    paired_new: set[int] = set()
    for old_index, old_page in enumerate(old):
        new_index = new_by_key.get((old_page._seen_tab, old_page._seen_url))
        if new_index is None or new_index in paired_new:
            continue
        paired_old.add(old_index)
        paired_new.add(new_index)
        new_page = new[new_index]
        if old_page._seen_status is not Status.READ or new_page._seen_status is not Status.READ:
            not_compared.append(_unreadable(old_page, new_page))
        else:
            compared.append(_diff(old_page, new_page, len(compared) + 1))

    related_old: set[int] = set()
    related_new: set[int] = set()
    for old_index, old_page in enumerate(old):
        for new_index, new_page in enumerate(new):
            if old_index in paired_old and new_index in paired_new:
                continue
            if old_page._seen_tab == new_page._seen_tab and old_page._seen_url != new_page._seen_url:
                code = 'SAME_TAB_OTHER_URL'
            elif old_page._seen_tab != new_page._seen_tab and old_page._seen_url == new_page._seen_url:
                code = 'OTHER_TAB_SAME_URL'
            else:
                continue
            related_old.add(old_index)
            related_new.add(new_index)
            not_compared.append(NotCompared(old_page, new_page, say(code)))
    not_compared.extend(NotCompared(page, None, say('ONLY_BEFORE')) for i, page in enumerate(old)
                        if i not in paired_old | related_old)
    not_compared.extend(NotCompared(None, page, say('ONLY_AFTER')) for i, page in enumerate(new)
                        if i not in paired_new | related_new)
    return tuple(compared), tuple(not_compared)


@dataclasses.dataclass(frozen=True)
class Verbatim:
    item: str
    mark: Mark


def _seen(text: str, pages: tuple[Page, ...]) -> str:
    """這段字在那一刻的頁上:讀得到(某一頁讀到的段落裡有);沒讀到(那一刻有頁、每一頁都讀到了、都沒有);
    其他(沒拍、工作區空了或讀不到、有頁讀不到)是沒比到。不管那一頁有沒有逐處比。"""
    read = [page for page in pages if page._seen_tab and page._seen_status is Status.READ]
    if any(text in part for page in read for part in page._seen_parts):
        return '讀得到'
    return '沒讀到' if pages and len(read) == len(pages) else '沒比到'


def mark_intended(intended_after: str, intended_before: str | None, item: str, pairs: Pairs,
                  befores: tuple[Page, ...], afters: tuple[Page, ...]) -> Verbatim:
    """原稿標記:新出現或原本就有只在逐處比的頁上分得出;其他頁照那一刻讀不讀得到標(#591 US20、US21,FIX22-B1)。"""
    compared, _ = pairs
    if intended_before is not None and not intended_after:
        was, now = _seen(intended_before, befores), _seen(intended_before, afters)
        mark = say('NOT_FOUND_VERBATIM') if was == '沒讀到' else say('REMOVED_READ', before=was, after=now)
        return Verbatim(item, mark)
    if not intended_after:
        return Verbatim(item, say('NOT_FOUND_VERBATIM'))
    if any(intended_after in part for page in compared for part in page.added):
        return Verbatim(item, say('FOUND_VERBATIM'))
    if any(intended_after in part for page in compared for part in page.after._seen_parts):
        return Verbatim(item, say('ALREADY_THERE'))
    code = {'讀得到': 'ON_PAGE_NOT_COMPARED', '沒讀到': 'NOT_FOUND_VERBATIM', '沒比到': 'NOT_COMPARED'}
    return Verbatim(item, say(code[_seen(intended_after, afters)]))


@dataclasses.dataclass(frozen=True)
class Claim:
    before: str
    after: str


def check_claim(claim: Claim, pairs: Pairs) -> Mark:
    compared, _ = pairs
    actual_before = tuple(part for page in compared for part in page.removed)
    actual_after = tuple(part for page in compared for part in page.added)
    for page in compared:
        if not claim.before and not claim.after:
            break
        before_ok = not claim.before or any(claim.before in part for part in page.removed)
        after_ok = not claim.after or any(claim.after in part for part in page.added)
        if before_ok and after_ok:
            return say('CLAIM_MATCH')
    if not compared:
        return say('CLAIM_UNCHECKED')
    actual = f'{"、".join(actual_before)} → {"、".join(actual_after)}'
    return say('CLAIM_MISMATCH', said=f'{claim.before} → {claim.after}', actual=actual)
