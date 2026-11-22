#!/usr/bin/env python3
"""agent 叫的拍照入口(jobsalvo-before、jobsalvo-after):讀工作區每一頁、編號、截圖、存證據夾(C-01、C-04、C-05)。"""
from __future__ import annotations

import dataclasses
import datetime
import json
import os
import sys
import uuid

TOOLS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, TOOLS)

from web import seen  # noqa: E402
from web.marks import Mark, say  # noqa: E402
from web.numbered import Numbered  # noqa: E402


@dataclasses.dataclass(frozen=True)
class Snapshot:
    phase: str
    at: str
    seq: int
    pages: tuple[seen.Seen, ...]
    material: Numbered
    marks: tuple[Mark, ...] = ()


def take(browser, phase: str, directories) -> Snapshot:
    """叫了拍照就一定留一筆紀錄:工作區讀不到也記成一筆沒有分頁編號的頁(C-01)。"""
    dirs = [os.fspath(path) for path in directories]
    for path in dirs:
        os.makedirs(path, exist_ok=True)
    material = Numbered()
    pages = []
    for page in seen.read_workspace(browser):
        seen.number(material, (page,))
        shot, why = '', page._seen_shot_why
        if page._seen_tab and not why:
            name = f'snap-{phase}-{uuid.uuid4().hex[:8]}.png'
            try:
                browser.raw_shot(page._seen_tab, os.path.join(dirs[0], name))
                shot = f'{os.path.basename(dirs[0])}/{name}'
            except Exception as error:  # noqa: BLE001 - a failed screenshot is recorded as the page's shot state
                why = str(error)[:300] or type(error).__name__
        pages.append(seen.with_shot(page, shot, why))
    seq = 1 + sum(name.startswith(f'snapshot-{phase}-') for name in os.listdir(dirs[0]))
    snapshot = Snapshot(phase, datetime.datetime.now().isoformat(timespec='microseconds'), seq, tuple(pages),
                        material, (say('WORKSPACE_EMPTY'),) if not pages else ())
    name = f'snapshot-{phase}-{seq:03}-{uuid.uuid4().hex[:8]}.json'
    payload = {'phase': phase, 'at': snapshot.at, 'seq': seq, 'pages': [seen.dump(page) for page in pages],
               'material': material.dump(), 'marks': [mark.code for mark in snapshot.marks]}
    for directory in dirs:
        with open(os.path.join(directory, name), 'w', encoding='utf-8') as target:
            json.dump(payload, target, ensure_ascii=False, indent=2)
    return snapshot


def load(directory: str, phase: str) -> tuple[Snapshot, ...]:
    """讀回一輪存下的拍照紀錄,照叫的順序。"""
    found = []
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else ():
        if name.startswith(f'snapshot-{phase}-') and name.endswith('.json'):
            with open(os.path.join(directory, name), encoding='utf-8') as source:
                data = json.load(source)
            found.append(Snapshot(data['phase'], data['at'], int(data['seq']),
                                  tuple(seen.load(row) for row in data['pages']), Numbered.load(data['material']),
                                  tuple(say(row['code'] if isinstance(row, dict) else row) for row in data.get('marks') or ())))
    return tuple(sorted(found, key=lambda snapshot: snapshot.seq))


def form_page(afters, said) -> tuple[str, str]:
    """agent 說它填表的那個分頁:一字不差在最後一次拍改後的工作區裡才算(seen.has_tab),回 (分頁, 那一頁的截圖);
    沒寫或核對不到是兩個空字串,不猜、不沿用(F-20)。"""
    last = max(afters, key=lambda snapshot: snapshot.seq, default=None)
    pages = last.pages if last else ()
    tab = said if isinstance(said, str) else ''
    if not seen.has_tab(pages, tab):
        return '', ''
    return tab, next(page._seen_shot for page in pages if page._seen_tab == tab)


def render(phase: str, pages, material) -> str:
    """印給 agent 的:拍到哪幾頁、網址、讀不讀得到、截圖沒取得的原因,加上編號材料(C-04)。"""
    lines = [f'{phase} material_id={material.material_id}']
    if not pages:
        lines.append(say('WORKSPACE_EMPTY').text)
    for page in pages:
        lines.append(f'[{page._seen_tab}] {page._seen_url} ({page._seen_status.value})')
        lines += [page._seen_why] if page._seen_why else []
        lines += [f'截圖沒取得：{page._seen_shot_why}'] if page._seen_shot_why else []
    lines += [f'{part.n}. {part.text}' for part in material.parts]
    return '\n'.join(lines)


def main(argv=None):
    command = os.path.basename((argv or sys.argv)[0])
    phase = 'before' if 'before' in command else 'after'
    if 'JOBSALVO_EGO_WORKSPACE' not in os.environ:
        print('沒有可拍的工作區：缺少 JOBSALVO_EGO_WORKSPACE', file=sys.stderr)
        return 0
    try:
        from web.outside import Real
        shot = take(Real.for_workspace(json.loads(os.environ['JOBSALVO_EGO_WORKSPACE'])), phase,
                    json.loads(os.environ['JOBSALVO_EGO_EVIDENCE_DIRS']))
        print(render(shot.phase, shot.pages, shot.material))
        return 0
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(str(error) or type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
