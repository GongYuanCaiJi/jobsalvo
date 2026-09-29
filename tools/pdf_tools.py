"""Small PDF operations shared by migration and delivery-package code."""
import importlib
import subprocess
import sys


def python():
    """讀 PDF 用的 python:就是現在這個(整個程式跑在 uv 建的 .venv 裡,套件都在)。"""
    return sys.executable


def _pypdf():
    try:
        return importlib.import_module('pypdf')
    except ImportError:
        interpreter = python()
        site = subprocess.check_output(
            [interpreter, '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'], text=True).strip()
        if site not in sys.path:
            sys.path.insert(0, site)
        importlib.invalidate_caches()
        return importlib.import_module('pypdf')


def merge(paths, destination):
    """Write the pages from each PDF to one PDF, preserving source order."""
    pypdf = _pypdf()
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
    if isinstance(value, (int, float, bool)):
        return (type(value).__name__, str(value))
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
        pypdf = _pypdf()

        def signatures(path):
            if path not in cache:
                try:
                    with open(path, 'rb') as f:
                        reader = pypdf.PdfReader(f, strict=False)
                        cache[path] = tuple(_page_signature(page, pypdf) for page in reader.pages)
                except Exception:
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
    except Exception:
        return False
