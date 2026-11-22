#!/usr/bin/env python3
"""Read and atomically update the shared platform profile registry."""
from __future__ import annotations

import json
import os
import tempfile

import config as cf


def path() -> str:
    return cf.path('profiles.json')


def load(filename: str | None = None) -> dict:
    filename = filename or path()
    try:
        with open(filename, encoding='utf-8') as source:
            registry = json.load(source)
    except FileNotFoundError:
        return {}
    except ValueError as error:
        raise ValueError(f'平台履歷登記表讀不出來({os.path.basename(filename)}:{error})') from error
    if not isinstance(registry, dict):
        raise ValueError('平台履歷登記資料不是物件')
    return registry


def save(registry: dict, filename: str | None = None) -> None:
    filename = filename or path()
    folder = os.path.dirname(filename)
    if folder:
        os.makedirs(folder, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=folder or '.', encoding='utf-8', delete=False) as output:
            temporary = output.name
            json.dump(registry, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, filename)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def update(change, filename: str | None = None):
    """Run one read/change/write under the registry's process lock."""
    filename = filename or path()
    import board_doc
    with board_doc.live_lock(filename):
        registry = load(filename)
        result = change(registry)
        save(registry, filename)
        return result
