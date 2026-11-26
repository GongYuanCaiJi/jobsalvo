"""一輪:工作區、agent、快照與交件單共用入口。"""
from __future__ import annotations

import dataclasses
import enum
import json
import os
import shutil

from web import cite, seen, snap
from web.brief import Brief
from web.marks import Mark, say, say_each
from web.sheet import Context, Field, How, Sheet, read as read_sheet
from web.snap import Snapshot


class End(enum.Enum):
    HANDED = '交件'
    TIMEOUT = '逾時'
    CRASHED = '當掉'
    STOPPED_BY_USER = '被停止'
    NO_SHEET = '沒有交件單'
    NOT_STARTED = '沒有啟動'


def end_label(end: End) -> str:
    """給使用者看的輪次結束方式;不把內部的交件單名稱帶上看板。"""
    return '沒交件' if end is End.NO_SHEET else end.value


# 交件單裡要本人處理的那一頁:程式重讀同一分頁、引文一字不差在頁上才交接(ADR-0009)
NEEDS_USER_PAGE = (
    Field('needs_user_tab', How.WORDS, '要你本人處理的那一頁的分頁(工作區編號:Page 標籤);沒有就空'),
    Field('needs_user_quote', How.WORDS, '那一頁上要本人處理的原文,一字不差照抄;沒有就空'),
)


@dataclasses.dataclass(frozen=True)
class Shot:
    tab: str
    url: str
    title: str
    file: str
    why: str


@dataclasses.dataclass(frozen=True)
class Step:
    """agent 的一次 ego-browser nodejs 指令:轉接器在 events.jsonl 記下的截圖或截圖失敗原因。"""
    at: str
    command: str
    command_id: str
    shots: tuple[Shot, ...] = ()


@dataclasses.dataclass(frozen=True)
class RoundRecord:
    id: str
    task: str
    evidence_dir: str
    brief_text: str
    workspace: int
    befores: tuple[Snapshot, ...]
    afters: tuple[Snapshot, ...]
    handoff: tuple[seen.Seen, ...] | None
    steps: tuple[Step, ...]
    end: End
    end_why: str
    sheet: Sheet
    session: str | None
    agent_id: str | None = None
    runtime: str | None = None
    handoff_page: str = ''
    handoff_marks: tuple[Mark, ...] = ()


def steps(directory: str) -> tuple[Step, ...]:
    """照轉接器記的 shot 與 ego_screenshot_failed 重建 agent 的每一次瀏覽器指令(同一個 command_id 一步)。"""
    import evidence
    rows = []
    try:
        with open(os.path.join(directory, evidence.EVENTS), encoding='utf-8') as source:
            for line in source:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get('command_id') and (
                        row.get('kind') == 'shot' or row.get('what') == 'ego_screenshot_failed'):
                    rows.append(row)
    except OSError:
        return ()
    found: dict[str, Step] = {}
    for row in rows:
        workspace, page = str(row.get('workspace') or ''), str(row.get('page') or '')
        shot = Shot(f'{workspace}:{page}' if workspace and page else page, str(row.get('url') or ''),
                    str(row.get('title') or ''), str(row.get('file') or '') if row.get('kind') == 'shot' else '',
                    '' if row.get('kind') == 'shot' else str(row.get('reason') or ''))
        key = str(row['command_id'])
        step = found.get(key) or Step(str(row.get('at') or ''), str(row.get('action') or ''), key)
        found[key] = dataclasses.replace(step, shots=(*step.shots, shot))
    return tuple(found.values())


SHEET_FILE = 'sheet.json'     # brief.sheet_file 的 agent 自己寫的交件檔;時間到也收(#198)


def _record(brief: Brief, task: str, directory: str, *, workspace=0, end=End.NOT_STARTED,
            why='', handoff=None, session=None, agent_id=None, runtime=None, schema=None, known=None, materials=()):
    selected = schema or brief.handoff
    snapshots_before, snapshots_after = snap.load(directory, 'before'), snap.load(directory, 'after')
    path = os.path.join(directory, 'handoff.json')
    # 沒有交件單是這一輪怎麼結束(沒有交件單、沒啟動),不另記成交件單壞掉(A-16)
    sheet = Sheet({}, {}) if not os.path.isfile(path) else read_sheet(
        path, selected, Context((*(snapshot.material for snapshot in (*snapshots_before, *snapshots_after)), *materials), known or {}))
    return RoundRecord(os.path.basename(directory), task, directory, _render(brief, workspace, directory), workspace,
                       tuple(snapshots_before), tuple(snapshots_after), handoff, steps(directory), end, why,
                       sheet, session,
                       agent_id, runtime)


def run(task: str, brief: Brief, browser, *, timeout_s: int, board=None, read_at_handoff: bool = False,
        resume: str | None = None, agent_id: str | None = None, resume_runtime: str | None = None,
        on_workspace=None, workspace: dict | None = None,
        known: dict[str, frozenset[str]] | None = None, materials=()) -> RoundRecord:
    """開一個空白工作區(或沿用給的 workspace:修改、送出接著同一個)、派 agent、讀回它叫的拍照與交件單;
    交件後只在要求時讀一次工作區(查應徵進度)。"""
    import evidence

    evidence_round = evidence.Round(task, task, board=board)
    directory = evidence_round.dirs[0]
    try:
        opened = workspace or browser.open_workspace()
        workspace = int(opened['id'])
        if on_workspace:
            on_workspace({'id': workspace, 'name': str(opened.get('name') or ''), 'page': 'p1'})
    except Exception as error:  # noqa: BLE001 - a round that cannot start is listed with its reason
        evidence_round.note('program_error', reason=str(error) or type(error).__name__)
        return _record(brief, task, directory, end=End.NOT_STARTED, why=str(error) or type(error).__name__)

    brief_text = _render(brief, workspace, directory)
    evidence_round.instruction(brief_text)
    if resume and agent_id and resume_runtime and _runtime(agent_id) != resume_runtime:
        resume = None       # 設定裡那個 agent 換了 runtime,原本的 session 接不回去
    try:
        with evidence.activated(evidence_round):
            result = browser.run_agent(brief_text, directories=evidence_round.dirs, timeout=timeout_s,
                                       resume=resume, agent_id=agent_id)
    except (OSError, RuntimeError, ValueError) as error:
        end, why, session, used_agent = End.CRASHED, str(error), None, agent_id
    else:
        _take_sheet_file(brief, directory)
        end = _end(result.status, os.path.join(directory, 'handoff.json'))
        why, session, used_agent = result.why, result.session, result.agent_id or agent_id
    if end is not End.HANDED and why:
        evidence_round.note('round_end', end=end.value, reason=str(why))

    handoff_path = os.path.join(directory, 'handoff.json')
    if os.path.isfile(handoff_path):
        evidence_round.handoff(handoff_path)
    pages = seen.read_workspace(browser) if read_at_handoff and end is End.HANDED else None
    if pages is not None:
        seen.record_reads(pages, evidence_round)
    record = _record(brief, task, directory, workspace=workspace, end=end, why=str(why), handoff=pages,
                     session=session, agent_id=used_agent, runtime=_runtime(used_agent), known=known,
                     materials=materials)
    return _hand_over(record, browser)


def _render(brief: Brief, workspace: int, directory: str) -> str:
    return brief.render(workspace=workspace, sheet_file=os.path.join(directory, SHEET_FILE))


def _take_sheet_file(brief: Brief, directory: str) -> None:
    """agent 自己寫的交件檔就是這一輪的交件單(最後一則訊息可能只是一句話);逾時也照收。"""
    written = os.path.join(directory, SHEET_FILE)
    if brief.sheet_file and os.path.isfile(written):
        shutil.copyfile(written, os.path.join(directory, 'handoff.json'))


def _hand_over(record: RoundRecord, browser) -> RoundRecord:
    """交件單寫了要本人處理的那一分頁:交到他面前,記下交接標記(cite.hand_over);兩個回合都一樣(C-11)。"""
    if record.end is not End.HANDED:
        return record
    tab, quote = (str(getattr(record.sheet.cells.get(name), 'said', '') or '')
                  for name in ('needs_user_tab', 'needs_user_quote'))
    if not tab and not quote:
        return record
    page, marks = cite.hand_over(browser, tab, quote)
    return dataclasses.replace(record, handoff_page=page, handoff_marks=marks)


def record_unstarted(brief: Brief, task: str, board, why: str) -> RoundRecord:
    """沒派 agent 的一輪(例如沒有要同步的):一樣有證據夾和清單,寫明沒啟動的原因。"""
    import evidence
    return _record(brief, task, evidence.Round(task, task, board=board).dirs[0], why=why)


def _end(status: str, handoff_path: str) -> End:
    if status == 'completed':
        return End.HANDED if os.path.isfile(handoff_path) else End.NO_SHEET
    return {'timeout': End.TIMEOUT, 'unavailable': End.NOT_STARTED, 'stopped': End.STOPPED_BY_USER,
            'cancelled': End.STOPPED_BY_USER}.get(status, End.CRASHED)


def _runtime(agent_id):
    if not agent_id:
        return None
    try:
        import config as cf
        return next((row.get('runtime') for row in (cf.C.get('agent') or {}).get('agents', [])
                     if row.get('id') == agent_id), None)
    except (AttributeError, TypeError):
        return None


def stops(record: RoundRecord) -> tuple[Mark, ...]:
    """agent 停下、要本人處理、交接的標記;這一輪怎麼結束由呼叫端各寫一次。"""
    marks = []
    stopped = record.sheet.cells.get('stopped')
    if getattr(stopped, 'said', ''):
        marks.extend(say_each('AGENT_STOPPED', stopped.said, 'why'))
    needs_user = record.sheet.cells.get('needs_user')
    if getattr(needs_user, 'said', False):
        marks.extend(say_each('NEEDS_USER', needs_user.said, 'why'))
    marks.extend(record.handoff_marks)
    return tuple(marks)


def stuck(record: RoundRecord) -> tuple[Mark, ...]:
    """這一輪沒乾淨收尾的原因:不是交件結束、交件單讀不了、agent 停下、要本人處理與交接(#783)。
    同步、填表、查應徵進度照它決定要不要打斷他(agent_report.settle)。"""
    if record.end is End.HANDED:
        ended = ()
    else:
        ended = (say('NOT_STARTED', why=record.end_why) if record.end is End.NOT_STARTED
                 else say('ENDED', end=end_label(record.end)),)
    return (*ended, *((record.sheet.broken,) if record.sheet.broken else ()), *stops(record))



def continue_with(record: RoundRecord, brief: Brief, browser, *, timeout_s: int, board=None, materials=(),
                  known=None) -> RoundRecord:
    """同一個 agent session 的下一回合(查應徵進度第二回合、找缺時間還沒用完):不開新工作區,交件單另存一個夾;
    materials:這一回合印給 agent 的編號材料(引段照它核對),known:KNOWN 格的清單。"""
    if not record.session:
        return dataclasses.replace(record, end=End.NOT_STARTED, end_why='沒有可繼續的 agent session')
    import evidence
    evidence_round = evidence.Round(record.task, 'continue', board=board)
    directory = evidence_round.dirs[0]
    prompt = _render(brief, record.workspace, directory)
    evidence_round.instruction(prompt)
    try:
        with evidence.activated(evidence_round):
            result = browser.run_agent(prompt, directories=evidence_round.dirs, timeout=timeout_s,
                                       resume=record.session, agent_id=record.agent_id)
    except (OSError, RuntimeError, ValueError) as error:
        evidence_round.note('round_end', end=End.CRASHED.value, reason=str(error))
        return _record(brief, record.task, directory, workspace=record.workspace, end=End.CRASHED, why=str(error),
                       session=record.session, agent_id=record.agent_id, runtime=record.runtime)
    _take_sheet_file(brief, directory)
    if result.why:
        evidence_round.note('round_end', end=_end(result.status, os.path.join(directory, 'handoff.json')).value,
                            reason=str(result.why))
    handoff_path = os.path.join(directory, 'handoff.json')
    if os.path.isfile(handoff_path):
        evidence_round.handoff(handoff_path)
    return _hand_over(_record(brief, record.task, directory, workspace=record.workspace,
                              end=_end(result.status, handoff_path), why=str(result.why), session=record.session,
                              agent_id=record.agent_id, runtime=record.runtime, known=known, materials=materials),
                      browser)
