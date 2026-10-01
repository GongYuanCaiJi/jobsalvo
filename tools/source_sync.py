#!/usr/bin/env python3
"""Track configured resume and attachment inputs and refresh their PDF previews."""
import hashlib
import json
import os
import traceback

import config as cf
import pdf_preview
import markdown_pdf

PAGE_KEY = 'file-pages:'


def files(board=None):
    for item in cf.RESUMES.values():
        rid = str(item.get('id') or '')
        for lang, raw in (item.get('files') or {}).items():
            if isinstance(raw, str) and raw:
                style = (item.get('styles') or {}).get(lang)
                yield {'kind': 'resume', 'id': rid, 'lang': str(lang), 'path': cf.path(raw),
                       'style_path': cf.path(style) if isinstance(style, str) and style else None}
    for item in cf.ATTACHMENTS:
        if not isinstance(item, dict):
            continue
        aid = str(item.get('id') or '')
        for lang, raw in (item.get('files') or {}).items():
            if isinstance(raw, str) and raw:
                style = (item.get('styles') or {}).get(lang)
                yield {'kind': 'attachment', 'id': aid, 'lang': str(lang), 'path': cf.path(raw),
                       'style_path': cf.path(style) if isinstance(style, str) and style else None}
    if not board:
        return
    try:
        import board_doc as bd
        with open(board, encoding='utf-8') as source:
            parsed = bd.parse(source.read())
        fb = json.loads(parsed['fb'])
    except (OSError, ValueError, KeyError):
        return
    for job in parsed['data'].get('jobs') or []:
        url = str(job.get('id') or '')
        raw = (fb.get(url) or {}).get('custom_file')
        if url and isinstance(raw, str) and raw:
            yield {'kind': 'custom', 'id': hashlib.sha256(url.encode('utf-8')).hexdigest(),
                   'lang': 'file', 'path': cf.path(raw)}


def input_key(entry):
    return 'file:' + ':'.join((entry['kind'], entry['id'], entry['lang']))


def preview_key(entry):
    return 'file-preview:' + ':'.join((entry['kind'], entry['id'], entry['lang']))


def preview_attempt_key(entry):
    return 'file-preview-attempt:' + ':'.join((entry['kind'], entry['id'], entry['lang']))


def page_key(entry):
    return PAGE_KEY + ':'.join((entry['kind'], entry['id'], entry['lang']))


def fingerprint(path):
    if not os.path.isfile(path):
        return 'MISSING'
    with open(path, 'rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def entry_fingerprint(entry):
    digest = hashlib.sha256()
    digest.update(fingerprint(entry['path']).encode('ascii'))
    style = entry.get('style_path')
    if style:
        digest.update(b'\0style\0')
        digest.update(fingerprint(style).encode('ascii'))
    return digest.hexdigest()


def effective_path(entry):
    if entry['path'].lower().endswith(('.md', '.markdown')):
        return markdown_pdf.output_path(entry['path'])
    return entry['path']


def read_manifest(path=None):
    if path is None:
        path = os.path.join(cf.HOME, '.reconcile-manifest.json')
    try:
        with open(path, encoding='utf-8') as source:
            data = json.load(source)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def stale(path=None, manifest=None, board=None):
    manifest = manifest if manifest is not None else read_manifest(path)
    active = list(files(board))
    keys = {input_key(entry) for entry in active}
    for entry in active:
        current = entry_fingerprint(entry)
        if manifest.get(input_key(entry)) != current:
            return True
        output = effective_path(entry)
        if entry['path'].lower().endswith(('.md', '.markdown')) and not os.path.isfile(output):
            return True
        if output.lower().endswith('.pdf') and os.path.isfile(output):
            cached = manifest.get(preview_key(entry))
            preview_path = pdf_preview.cache_path(cached) if cached else None
            if cached and (not preview_path or not os.path.isfile(preview_path)):
                return True
            if not cached and manifest.get(preview_attempt_key(entry)) != current:
                return True
        if entry['path'].lower().endswith(('.md', '.markdown')) and manifest.get(page_key(entry)) is None:
                return True
    return any(k.startswith('file:') and k.count(':') == 3 and k not in keys
               for k in manifest)


def _diagnostic(category, error):
    """Summarize an error with tool-owned code location, excluding copied data and exception text."""
    tools_dir = os.path.realpath(os.path.dirname(__file__))
    chain = []
    current = error
    seen = set()
    while current is not None and id(current) not in seen and len(chain) < 3:
        seen.add(id(current))
        location = ''
        if current.__traceback__:
            for frame in reversed(traceback.extract_tb(current.__traceback__)):
                filename = os.path.realpath(frame.filename)
                try:
                    is_tool_frame = os.path.commonpath((tools_dir, filename)) == tools_dir
                except ValueError:
                    is_tool_frame = False
                if is_tool_frame:
                    location = f'{os.path.basename(filename)}:{frame.lineno} {frame.name}'
                    break
        summary = type(current).__name__
        chain.append(f'{summary} at {location}' if location else summary)
        current = current.__cause__ or current.__context__
    return f'{category} ' + ' <- '.join(chain)


def refresh(manifest, check_only=False, board=None, diagnostics=None):
    """Render Markdown sources, update source fingerprints, page counts and PDF previews."""
    active = list(files(board))
    active_keys = {input_key(entry) for entry in active}
    changed = []
    errors = []
    seen_kinds = {}
    for entry in active:
        kind = entry.get('kind') or 'source'
        seen_kinds[kind] = seen_kinds.get(kind, 0) + 1
        # 真實資料副本檢查跑完會刪掉副本;診斷只寫「第幾份、哪種、哪個語言」,不寫檔名。
        label = f'{kind} #{seen_kinds[kind]} lang={entry.get("lang") or "?"}'
        key = input_key(entry)
        preview = preview_key(entry)
        current = entry_fingerprint(entry)
        output = effective_path(entry)
        is_markdown = entry['path'].lower().endswith(('.md', '.markdown'))
        cached = manifest.get(preview)
        cached_path = pdf_preview.cache_path(cached) if cached else None
        needs_preview = (output.lower().endswith('.pdf') and os.path.isfile(output)
                         and ((cached and (not cached_path or not os.path.isfile(cached_path)))
                              or (not cached and manifest.get(preview_attempt_key(entry)) != current)))
        needs_render = is_markdown and (manifest.get(key) != current or not os.path.isfile(output))
        needs_pages = is_markdown and manifest.get(page_key(entry)) is None
        if manifest.get(key) == current and not needs_preview and not needs_render and not needs_pages:
            continue
        changed.append(entry)
        if check_only:
            continue
        if is_markdown and needs_render:
            try:
                markdown_pdf.render(entry['path'], output, entry.get('style_path'), entry.get('lang'))
            except markdown_pdf.RenderError as exc:
                errors.append(f'{os.path.basename(entry["path"])}:{exc}')
                if diagnostics is not None:
                    diagnostics.append(_diagnostic('Markdown render', exc))
                continue
        preview_hash = None
        attempted = False
        if output.lower().endswith('.pdf') and os.path.isfile(output):
            try:
                preview_hash = pdf_preview.generate(output)
                attempted = True
                if not preview_hash and diagnostics is not None:
                    diagnostics.append(f'PDF preview {label}: 檔案不是可讀的 PDF')
            except pdf_preview.PreviewError as exc:
                errors.append(f'{os.path.basename(entry["path"])}:{exc}')
                if diagnostics is not None:
                    diagnostics.append(_diagnostic(f'PDF preview {label}', exc))
        elif output.lower().endswith('.pdf') and diagnostics is not None:
            diagnostics.append(f'PDF preview {label}: 找不到要產生預覽的 PDF')
        if preview_hash:
            manifest[preview] = preview_hash
        else:
            manifest.pop(preview, None)
        if attempted:
            manifest[preview_attempt_key(entry)] = current
        elif not output.lower().endswith('.pdf'):
            manifest.pop(preview_attempt_key(entry), None)
        if is_markdown:
            try:
                import settings_api
                manifest[page_key(entry)] = settings_api.pdf_pages(output)
            except Exception as exc:  # noqa: BLE001 — 讀不了的原因照實寫進這一份的錯誤清單
                manifest.pop(page_key(entry), None)
                errors.append(f'{os.path.basename(entry["path"])}:PDF 頁數讀取失敗({str(exc)[:80]})')
                if diagnostics is not None:
                    diagnostics.append(_diagnostic('PDF pages', exc))
        manifest[key] = current

    if not check_only:
        for key in list(manifest):
            if key.startswith('file:') and key.count(':') == 3 and key not in active_keys:
                manifest.pop(key, None)
            elif key.startswith('file-preview:') and key.count(':') == 3:
                input_key_name = key.replace('file-preview:', 'file:', 1)
                if input_key_name not in active_keys:
                    manifest.pop(key, None)
            elif key.startswith('file-preview-attempt:') and key.count(':') == 3:
                input_key_name = key.replace('file-preview-attempt:', 'file:', 1)
                if input_key_name not in active_keys:
                    manifest.pop(key, None)
            elif key.startswith(PAGE_KEY) and key.count(':') == 3:
                input_key_name = key.replace(PAGE_KEY, 'file:', 1)
                if input_key_name not in active_keys:
                    manifest.pop(key, None)
    return changed, errors


def source_preview_hash(kind, item_id, lang, path=None):
    manifest = read_manifest(path)
    entry = {'kind': kind, 'id': str(item_id), 'lang': str(lang)}
    return manifest.get(preview_key(entry))
