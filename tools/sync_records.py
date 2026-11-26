#!/usr/bin/env python3
"""Persistence for sync versions, review state, visuals, and registered slots."""
from __future__ import annotations

import hashlib
import json
import time

import profile_registry


def slots(platform=None):
    return [(name, key.partition('/')[0], key.partition('/')[2], entry)
            for name, entries in profile_registry.load().items()
            if not str(name).startswith('_') and isinstance(entries, dict) and (not platform or name == platform)
            for key, entry in entries.items() if '/' in key and isinstance(entry, dict) and entry.get('read')]


def where(platform, lang, variant):
    return (profile_registry.load().get(platform) or {}).get(f'{lang}/{variant}')


def versions(slot):
    platform, rest = slot.key.split(':', 1)
    lang, variant = rest.split('/', 1)
    return versions_for(platform, lang, variant)


def _legacy_target_text(lang, variant, baseline):
    import source_sync
    import sync_material

    source = str(baseline.get('source') or '')
    parts = variant.split('+')
    bodies = {}
    if len(parts) == 1:
        bodies[parts[0]] = source
    else:
        cursor = 0
        for index, part in enumerate(parts):
            marker = f'# {part}\n'
            start = source.find(marker, cursor)
            if start < 0:
                continue
            body_start = start + len(marker)
            following = [source.find(f'# {name}\n', body_start) for name in parts[index + 1:]]
            following = [position for position in following if position >= 0]
            end = min(following) if following else len(source)
            bodies[part] = source[body_start:end]
            cursor = end

    text = {}
    for part, body in bodies.items():
        for where, value, _links, _body, _labels in sync_material.md_sections(body):
            text[f'{part}: {where}'] = value
    for answer in baseline.get('answers') or []:
        if isinstance(answer, dict):
            text[f"答案: {answer.get('q') or answer.get('k') or ''}"] = str(answer.get('v') or '')

    attachment_names = [name for name, _path in sync_material.slot_attachments(lang, variant)]
    current_attachment_fp = sync_material.attachments_fingerprint(lang, variant)
    old_attachment_fp = baseline.get('attachments')
    text['附件'] = ('\n'.join(attachment_names)
                    if old_attachment_fp in (None, current_attachment_fp) else '')
    master = baseline.get('master')
    if master is None:
        master = '+'.join(source_sync.master_fingerprint(lang, part) for part in parts)
    attachment_fp = current_attachment_fp if old_attachment_fp is None else old_attachment_fp
    text['__target_fingerprint__'] = '+'.join([str(master), str(attachment_fp)])
    return text


def _legacy_versions(entry, platform, lang, variant):
    baseline = entry.get('sync_baseline')
    current_version = version(entry)
    reviewed_version = str(entry.get('sync_reviewed_version') or '')
    pending = entry.get('sync_pending_baseline') or {}
    rows = []
    if isinstance(baseline, dict):
        reviewed_id = reviewed_version or 'legacy-baseline'
        text = _legacy_target_text(lang, variant, baseline)
        target = hashlib.sha256(json.dumps(text, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode('utf-8')).hexdigest()
        # main 沒有「看過這一版」:只有本人真的按過(sync_reviewed_version)才算看過(#601 第 19 條)
        rows.append({'round_id': reviewed_id, 'target': target, 'target_text': text,
                     'reviewed_at': reviewed_version or None})
    elif reviewed_version:
        rows.append({'round_id': reviewed_version, 'target': '', 'target_text': {},
                     'reviewed_at': reviewed_version})
    if current_version and current_version != (rows[-1]['round_id'] if rows else ''):
        source = pending if isinstance(pending, dict) and pending else baseline
        text = _legacy_target_text(lang, variant, source) if isinstance(source, dict) else {}
        target = hashlib.sha256(json.dumps(text, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode('utf-8')).hexdigest()
        rows.append({'round_id': current_version, 'target': target, 'target_text': text,
                     'reviewed_at': None})
    return rows


def versions_for(platform, lang, variant):
    registry = profile_registry.load()
    entry = (registry.get(platform) or {}).get(f'{lang}/{variant}')
    if not isinstance(entry, dict):
        return []
    existing = entry.get('sync_versions')
    has_legacy = any(key in entry for key in ('sync_baseline', 'sync_pending_baseline',
                                               'sync_reviewed_version', 'sync_version'))
    if isinstance(existing, list) and (existing or not has_legacy):
        return [dict(row) for row in existing if isinstance(row, dict)]
    rows = _legacy_versions(entry, platform, lang, variant)
    if not rows:
        return []

    def migrate(current):
        target = (current.get(platform) or {}).get(f'{lang}/{variant}')
        if isinstance(target, dict):
            current_rows = target.get('sync_versions')
            legacy = any(key in target for key in ('sync_baseline', 'sync_pending_baseline',
                                                    'sync_reviewed_version', 'sync_version'))
            if not isinstance(current_rows, list) or (not current_rows and legacy):
                target['sync_versions'] = rows
        return target.get('sync_versions') if isinstance(target, dict) else rows

    saved = profile_registry.update(migrate)
    return [dict(row) for row in saved or rows if isinstance(row, dict)]


def version(entry):
    versions = entry.get('sync_versions') or []
    return str((versions[-1].get('round_id') if versions and isinstance(versions[-1], dict) else None)
               or entry.get('sync_version') or next(
        (row.get('evidence') for row in reversed(entry.get('sync_visuals') or [])
         if isinstance(row, dict) and row.get('evidence')), '') or '')


def remember_version(platform, lang, variant, round_id, target, target_text):
    def change(registry):
        entry = (registry.get(platform) or {}).get(f'{lang}/{variant}')
        if not isinstance(entry, dict):
            raise ValueError('平台履歷未登記')
        rows = list(entry.get('sync_versions') or
                    _legacy_versions(entry, platform, lang, variant))
        rows.append({'round_id': str(round_id), 'target': str(target),
                     'target_text': dict(target_text), 'reviewed_at': None})
        entry['sync_versions'] = rows
        entry['sync_version'] = str(round_id)
    profile_registry.update(change)


def remember_visuals(platform, lang, variant, visuals):
    def change(registry):
        entry = (registry.get(platform) or {}).get(f'{lang}/{variant}')
        if not isinstance(entry, dict):
            raise ValueError('平台履歷未登記')
        previous = list(entry.get('sync_visuals') or [])
        old_card = entry.pop('sync_visuals_card', None)
        if old_card:
            for row in previous:
                row.setdefault('card', old_card)
        entry['sync_visuals'] = previous + list(visuals)
    profile_registry.update(change)


def mark_reviewed(platform, lang, variant, wanted_version):
    versions_for(platform, lang, variant)
    def change(registry):
        entry = (registry.get(platform) or {}).get(f'{lang}/{variant}')
        if not isinstance(entry, dict) or not wanted_version or version(entry) != str(wanted_version):
            return False
        versions = entry.get('sync_versions') or []
        latest = versions[-1] if versions and isinstance(versions[-1], dict) else None
        if latest is None:
            raise ValueError('這份平台履歷還沒有同步版本可以記下,請先按同步')
        latest['reviewed_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        return True
    return profile_registry.update(change)
