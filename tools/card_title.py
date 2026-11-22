"""Display names for cards and their associated files."""
from __future__ import annotations

import re


SOURCE_LINK_SUFFIX = re.compile(r'\s*[\uFF08(]\s*\[[^\]]+\]\([^)]*\)\s*[\uFF09)]\s*$')
MARKDOWN_LINK = re.compile(r'\[([^\]]+)\]\([^)]*\)')


def name(value, company=None):
    target = value.get('target') if isinstance(value, dict) else value
    target = str(target or '').strip()
    company = str(company or '').strip()
    if company and company.casefold() not in target.casefold():
        target = f'{target} · {company}' if target else company
    target = SOURCE_LINK_SUFFIX.sub('', target)
    target = MARKDOWN_LINK.sub(r'\1', target)
    target = re.sub(r'[*`]', '', target)
    target = re.sub(r'\s*[\u00b7\u30fb]\s*', ' · ', target)
    return re.sub(r'\s+', ' ', target).strip()


def with_name(value, replacement):
    target = value.get('target') if isinstance(value, dict) else value
    target = str(target or '')
    suffix = SOURCE_LINK_SUFFIX.search(target)
    return name(replacement) + (suffix.group(0) if suffix else '')
