#!/usr/bin/env python3
"""The local resume text, answers, and attachments that a sync round compares."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile

import config as cf
import pdf_paths


_LINK = re.compile(r'\[([^\]]*)\]\(([^)\s]+)\)')


def md_sections(text):
    """Markdown source as plain text sections; headings name sections, not page fields."""
    text = re.sub(r'<style\b.*?</style>|<!--.*?-->|<img\b[^>]*>', '', text, flags=re.S | re.I)
    out, head, buf = [], '', []

    def flush():
        current = re.sub(r'`([^`]*)`', r'\1', ' '.join(buf)).strip()
        if current:
            linked = _LINK.findall(current)
            out.append((f'{head or "開頭"}[{len(out)}]', _LINK.sub(r'\1', current).replace('**', ''),
                        [url for _label, url in linked], _LINK.sub('', current).replace('**', '').strip(),
                        [label for label, _url in linked]))
        buf.clear()

    for line in text.splitlines():
        current = line.strip()
        if current.startswith('#'):
            flush()
            head = current.lstrip('#').strip()
            continue
        if not current:
            flush()
            continue
        if re.match(r'^([-*+]|\d+\.)\s', current):
            flush()
            buf.append(re.sub(r'^([-*+]|\d+\.)\s+', '', current))
            continue
        buf.append(current)
    flush()
    return out


def capture_source(path):
    """Read one immutable source copy and return its exact text and byte fingerprint."""
    if not path or not os.path.isfile(path) or not path.lower().endswith(('.md', '.markdown', '.txt', '.pdf')):
        raise ValueError('找不到可讀的指定履歷原稿')
    try:
        with open(path, 'rb') as source:
            data = source.read()
        if path.lower().endswith('.pdf'):
            import pdf_text
            with tempfile.NamedTemporaryFile(suffix='.pdf') as copy:
                copy.write(data)
                copy.flush()
                text = pdf_text.extract(copy.name)
        else:
            text = data.decode('utf-8')
    except (OSError, UnicodeError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(f'讀不出母稿 {os.path.basename(path)}({str(error)[:80]})') from error
    if not text.strip():
        raise ValueError('指定履歷原稿沒有可讀文字')
    return {'sha256': hashlib.sha256(data).hexdigest(), 'text': text}


def slot_attachments(lang, variant):
    ids = set(variant.split('+'))
    return [(str(item.get('name') or os.path.basename(item['files'][lang])),
             pdf_paths.sent_file(cf.path(item['files'][lang])))
            for item in cf.ATTACHMENTS
            if item.get('enabled', True) and (item.get('files') or {}).get(lang)
            and (not item.get('resume_ids') or ids & set(item['resume_ids']))]


def attachments_fingerprint(lang, variant):
    values = []
    for name, filename in slot_attachments(lang, variant):
        try:
            with open(filename, 'rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
        except OSError:
            digest = None
        values.append(f'{name}:{digest}')
    return '+'.join(values)


def platform_answers(fb, platform, lang, variant, profile_url=''):
    target = profile_url if profile_url else ''
    return [{key: str(entry.get(key) or '') for key in ('k', 'q', 'v', 'zh')}
            for entry in fb.get('__ans__') or [] if isinstance(entry, dict)
            and entry.get('platform') == platform and entry.get('profile') == f'{lang}/{variant}'
            and ((target and entry.get('profile_url') == target)
                 or (not target and not entry.get('profile_url')))]


def shared_answers(board):
    import board_doc
    import form_record_state
    fb = __import__('json').loads(board_doc.load(board)['fb'])
    rows = [{key: str(answer.get(key) or '') for key in ('k', 'q', 'v', 'zh')}
            for answer in form_record_state.find_shared(fb)]
    return fb, rows


def source(slot, board=None):
    rows = {}
    fingerprints = []
    for part in slot.variant.split('+'):
        captured = capture_source(cf.master(part, slot.lang))
        fingerprints.append(captured['sha256'])
        for where, text, _links, _body, _labels in md_sections(captured['text']):
            rows[f'{part}: {where}'] = text
    attachments = slot_attachments(slot.lang, slot.variant) if slot.kind != '附件' else ()
    rows['附件'] = '\n'.join(name for name, _filename in attachments)
    if slot.kind != '附件':
        try:
            fb, shared = shared_answers(board)
        except (OSError, ValueError) as error:
            raise ValueError(f'這一輪讀不到看板,常用答案沒帶上,先不同步(看板:{error})') from error
        answers = shared + platform_answers(fb, slot.platform, slot.lang, slot.variant, slot.url)
        for answer in answers:
            rows[f"答案: {answer.get('q') or answer.get('k') or ''}"] = str(answer.get('v') or '')
    rows['__target_fingerprint__'] = '+'.join(fingerprints + [attachments_fingerprint(slot.lang, slot.variant)])
    return rows
