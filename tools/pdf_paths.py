#!/usr/bin/env python3
"""Paths for the PDF rendered from a Markdown source."""
from __future__ import annotations

import os


def output_path(source_path):
    import config as cf
    source_path = os.path.abspath(os.path.expanduser(source_path))
    relative = os.path.relpath(source_path, cf.HOME)
    if relative.startswith(os.pardir + os.sep):
        relative = os.path.join('_外部', source_path.lstrip(os.sep))
    return os.path.join(cf.HOME, '.rendered', os.path.splitext(relative)[0] + '.pdf')


def is_markdown(path):
    return bool(path) and path.lower().endswith(('.md', '.markdown'))


def sent_file(path):
    return output_path(path) if is_markdown(path) else path
