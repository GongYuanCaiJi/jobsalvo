"""同步、恢復、填表共用的改動清單組裝器。"""
from __future__ import annotations

import dataclasses

from web import compare
from web.compare import Compared, NotCompared, Verbatim
from web.marks import Mark, say, say_each
from web.round import End, RoundRecord, Step, stops as round_stops
from web.sheet import Cell, Verdict


@dataclasses.dataclass(frozen=True)
class Intended:
    item: str
    before: str | None
    after: str | None


@dataclasses.dataclass(frozen=True)
class ChangeList:
    head: tuple[Mark, ...]
    compared: tuple[Compared, ...]
    not_compared: tuple[NotCompared, ...]
    intended: tuple[Verbatim, ...] = ()
    claims: tuple[Mark, ...] = ()
    stops: tuple[Mark, ...] = ()
    sheet_marks: tuple[Mark, ...] = ()
    steps: tuple[tuple[str, Step], ...] = ()


BETWEEN = '拍改前到拍改後之間'


def _phase(at: str, before: str | None, after: str | None) -> str:
    """這一步落在哪一段:以逐處比較用的拍改前基準(最早那一次)、拍改後(最後那一次)為界。"""
    if after and at > after:
        return '拍改後之後'
    if not before or at < before:
        return '拍改前之前'
    return '拍改前之後、拍改後之前' if not after else BETWEEN


def _claim_rows(record: RoundRecord):
    rows = record.sheet.cells.get('changes')
    if not isinstance(rows, list):
        return ()
    claims = []
    for row in rows:
        # 只有 agent 照格式寫的一列才是一筆改動說法;格式不對的那一列已照原字列出,不另組一句改動
        said = [getattr(row.get(side), 'said', None) for side in ('before', 'after')]
        if all(isinstance(text, str) for text in said):
            claims.append(compare.Claim(*said))
    return tuple(claims)


def _listed_marks(record: RoundRecord):
    marks = []
    for value in record.sheet.cells.values():
        cells = [value] if isinstance(value, Cell) else [cell for row in value for cell in row.values()] if isinstance(value, list) else []
        for cell in cells:
            if cell.verdict is Verdict.LISTED:
                marks.extend(cell.marks)
            elif cell.verdict is Verdict.WORDS and cell.field not in ('stopped', 'needs_user', 'needs_user_tab', 'needs_user_quote'):
                marks.extend(say_each('AGENT_WORDS', cell.said, 'words'))
    if record.sheet.broken:
        marks.append(record.sheet.broken)
    marks.extend(say_each('AGENT_WORDS', record.sheet.extra, 'words'))
    return tuple(marks)


def build(record: RoundRecord, intended: tuple[Intended, ...]) -> ChangeList:
    """拍照狀態、頁面配對、原稿與交件單說法全部交由 compare/sheet 負責。"""
    before_snapshots = list(record.befores)
    befores = compare.baseline(before_snapshots)
    afters = compare.latest(list(record.afters))
    compared, not_compared = compare.pair(befores, afters)

    head = []
    head.append(say('NOT_STARTED', why=record.end_why) if record.end is End.NOT_STARTED
                else say('ENDED', end=record.end.value))
    # 有沒有拍改前、拍改後看叫了沒有(有沒有拍照紀錄),不看讀到幾頁:工作區空了或讀不到另有標記。
    if not before_snapshots:
        head.append(say('NO_BEFORE'))
    if not record.afters:
        head.append(say('NO_AFTER'))
    else:
        head.append(say('AFTER_MEANS'))      # 拍了改後就寫明清單是改後那一刻讀到的頁,讀到幾頁另有標記(A-24)
    first_before = min(before_snapshots, key=lambda snapshot: snapshot.seq).at if before_snapshots else None
    last_after = max((snapshot.at for snapshot in record.afters), default=None)
    steps = tuple((_phase(step.at, first_before, last_after), step) for step in record.steps)
    earlier_steps = [step.command for phase, step in steps if phase == '拍改前之前' and step.command]
    if first_before and earlier_steps:
        head.append(say('STEPS_BEFORE_FIRST', steps='；'.join(earlier_steps)))
    head.append(say('PAGES_COMPARED', count=len(compared)))
    # 工作區空了只看拿來比的那兩次:第一次拍改前、最後一次拍改後(A-05、A-15)
    head.extend(mark for snapshot in (*before_snapshots[:1], *record.afters[-1:]) for mark in snapshot.marks)

    marked = tuple(compare.mark_intended(item.after or '', item.before, item.item, (compared, not_compared),
                                         befores, afters) for item in intended)
    claims = tuple(compare.check_claim(claim, (compared, not_compared)) for claim in _claim_rows(record))
    return ChangeList(tuple(head), compared, not_compared, marked, claims,
                      round_stops(record), _listed_marks(record), steps)


def since(fill_afters, now) -> ChangeList:
    """填好後到現在:填表最後一次拍改後,跟現在拍的這一次照配對規則比(F-14)。"""
    compared, not_compared = compare.pair(compare.latest(list(fill_afters)), now.pages)
    return ChangeList(tuple(now.marks), compared, not_compared)


def restore_intents(directory: str | None, picks: tuple[str, ...], fixed_url: str) -> tuple[Intended, ...]:
    """恢復:清單那一輪選的那幾處 → 這一輪要改的(把改後的字改回改前);選的不在那一版清單就不派(S-15)。"""
    from web import cite, snap
    if directory is None:       # 那一版的證據夾不見了
        raise ValueError('恢復清單已更新,請重新載入後選取')
    befores, afters = snap.load(directory, 'before'), snap.load(directory, 'after')
    if fixed_url:               # 填表發的固定版清單:跟發的時候一樣只比那一頁,選的編號才對得上
        befores, afters = cite.at_url(befores, fixed_url), cite.at_url(afters, fixed_url)
    compared, _ = compare.pair(compare.baseline(list(befores)), compare.latest(list(afters)))
    changes = {change.id: change for page in compared for change in page.changes}
    if not picks or any(pick not in changes for pick in picks):
        raise ValueError('恢復清單已更新,請重新載入後選取')
    return tuple(Intended(f'恢復 {changes[pick].url}', '\n'.join(changes[pick].added),
                          '\n'.join(changes[pick].removed)) for pick in picks)
