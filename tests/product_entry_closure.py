#!/usr/bin/env python3
"""Report Python import closures for documented product entry points."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_no_page_judgment as scanner  # noqa: E402

TEXT_SUFFIXES = {'.css', '.html', '.js', '.md', '.sh'}
TOOL_PATH = re.compile(r'(?:\.\./)*tools/([A-Za-z0-9_./-]+)')
SCRIPT_NAME = re.compile(r"['\"]([A-Za-z0-9_./-]+\.py)['\"]")


def _tool_file(value: str) -> Path | None:
    path = (TOOLS / value).resolve()
    try:
        path.relative_to(TOOLS.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def product_entries() -> tuple[str, ...]:
    python = {'tools/board_server.py'}
    commands = set()
    sources = [ROOT / 'README.md', ROOT / 'docs', ROOT / 'board']
    for source in sources:
        files = (source,) if source.is_file() else source.rglob('*')
        for path in files:
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            for match in TOOL_PATH.finditer(path.read_text(encoding='utf-8', errors='ignore')):
                target = _tool_file(match.group(1))
                if target is None:
                    continue
                relative = target.relative_to(ROOT).as_posix()
                if target.suffix == '.py':
                    python.add(relative)
                elif not target.suffix and os.access(target, os.X_OK):
                    commands.add(relative)

    for path in TOOLS.iterdir():
        if path.is_file() and not path.suffix and os.access(path, os.X_OK):
            commands.add(path.relative_to(ROOT).as_posix())

    seen = set()
    while python - seen:
        pending = sorted(python - seen)
        seen.update(pending)
        for path in scanner.closure(pending):
            source = (ROOT / path).read_text(encoding='utf-8', errors='ignore')
            references = [match.group(1) for match in TOOL_PATH.finditer(source)]
            references.extend(match.group(1) for match in SCRIPT_NAME.finditer(source))
            for reference in references:
                target = _tool_file(reference)
                if target is None:
                    continue
                relative = target.relative_to(ROOT).as_posix()
                if target.suffix == '.py':
                    python.add(relative)
                elif not target.suffix and os.access(target, os.X_OK):
                    commands.add(relative)
    return tuple(sorted(python | commands))


def main() -> int:
    entries = product_entries()
    python_entries = tuple(path for path in entries if path.endswith('.py'))
    commands = tuple(path for path in entries if not path.endswith('.py'))
    product_closure = scanner.closure(python_entries)
    task_entries = scanner.task_entries()
    task_closure = scanner.closure(task_entries)
    if not set(task_closure) <= set(product_closure):
        raise SystemExit('四個網站任務入口未包含在產品閉包')
    all_python = {path.relative_to(ROOT).as_posix() for path in TOOLS.rglob('*.py')
                  if '__pycache__' not in path.parts}
    outside = tuple(sorted(all_python - set(product_closure)))
    print(f'Python 指令與動態腳本入口 ({len(python_entries)}):')
    print('\n'.join(python_entries))
    print(f'非 Python 指令 ({len(commands)}):')
    print('\n'.join(commands))
    print(f'四個網站任務入口 ({len(task_entries)}):')
    print('\n'.join(task_entries))
    print(f'四個網站任務 import 閉包 ({len(task_closure)}):')
    print('\n'.join(task_closure))
    print(f'產品 Python import 閉包 ({len(product_closure)}):')
    print('\n'.join(product_closure))
    print(f'閉包外 Python 檔案 ({len(outside)}):')
    print('\n'.join(outside))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
