"""Small PDF operations shared by migration and delivery-package code."""

import pypdf


def merge(paths, destination):
    """Write the pages from each PDF to one PDF, preserving source order."""
    writer = pypdf.PdfWriter()
    readers = [pypdf.PdfReader(path, strict=False) for path in paths]
    for reader in readers:
        writer.append(reader)
    if not writer.pages:
        raise ValueError('沒有可合併的 PDF 頁面')
    with open(destination, 'wb') as f:
        writer.write(f)


def _normal(value, pypdf, stack=None):
    """Canonicalize a page resource tree without PDF object numbers or compression."""
    stack = stack if stack is not None else set()
    indirect = pypdf.generic.IndirectObject
    if isinstance(value, indirect):
        return _normal(value.get_object(), pypdf, stack)
    if isinstance(value, pypdf.generic.StreamObject):
        identity = id(value)
        if identity in stack:
            return ('cycle', 'stream')
        stack.add(identity)
        metadata = tuple(sorted(
            (str(k), _normal(v, pypdf, stack)) for k, v in value.items()
            if str(k) not in ('/Length', '/Filter', '/DecodeParms')))
        try:
            data = value.get_data()
        finally:
            stack.remove(identity)
        return ('stream', metadata, data)
    if isinstance(value, pypdf.generic.DictionaryObject):
        identity = id(value)
        if identity in stack:
            return ('cycle', 'dictionary')
        stack.add(identity)
        items = tuple(sorted((str(k), _normal(v, pypdf, stack)) for k, v in value.items()))
        stack.remove(identity)
        return ('dict', items)
    if isinstance(value, (pypdf.generic.ArrayObject, list, tuple)):
        return ('array', tuple(_normal(v, pypdf, stack) for v in value))
    if isinstance(value, pypdf.generic.NameObject):
        return ('name', str(value))
    if isinstance(value, bytes):
        return ('bytes', bytes(value))
    if isinstance(value, str):
        return ('text', str(value))
    return (type(value).__name__, str(value))


def _page_signature(page, pypdf):
    visible = ('/Contents', '/Resources', '/Group', '/Annots')
    geometry = ('/MediaBox', '/CropBox', '/BleedBox', '/TrimBox', '/ArtBox',
                '/Rotate', '/UserUnit')
    return (tuple((key, _normal(page.get(key), pypdf)) for key in geometry),
            tuple((key, _normal(page.get(key), pypdf)) for key in visible))


def same_pages(candidate, parts, page_cache=None):
    """Return true only when candidate contains the same ordered page content as parts."""
    if len(parts) < 2:
        return False
    cache = page_cache if page_cache is not None else {}
    try:
        def signatures(path):
            if path not in cache:
                try:
                    with open(path, 'rb') as f:
                        reader = pypdf.PdfReader(f, strict=False)
                        cache[path] = tuple(_page_signature(page, pypdf) for page in reader.pages)
                except Exception:  # noqa: BLE001 — pypdf 讀壞檔丟的例外五花八門;讀不了就算「不是同一份」,檔案照舊留著(往不刪那邊錯)
                    cache[path] = None
            return cache[path]

        expected = []
        for path in parts:
            pages = signatures(path)
            if pages is None:
                return False
            expected.extend(pages)
        actual = signatures(candidate)
        return bool(expected) and actual is not None and actual == tuple(expected)
    except Exception:  # noqa: BLE001 — 同上:判斷不了就算「不是合併出來的」,檔案照舊留著
        return False
