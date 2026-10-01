#!/usr/bin/env python3
"""Create and serve content-addressed PDF thumbnails (macOS Quick Look, or poppler pdftoppm elsewhere)."""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile


SIZE = 960


class PreviewError(RuntimeError):
    pass


def _home(home=None):
    if home:
        return os.path.abspath(os.path.expanduser(home))
    import config as cf
    return cf.HOME


def _content_hash(path):
    with open(path, 'rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _is_pdf(path):
    try:
        with open(path, 'rb') as source:
            return source.read(5) == b'%PDF-'
    except OSError:
        return False


def cache_dir(home=None):
    return os.path.join(_home(home), '.previews')


def cache_path(key, home=None):
    if not re.fullmatch(r'[0-9a-f]{64}', str(key or '')):
        return None
    return os.path.join(cache_dir(home), key + '.png')


def url(key):
    return '/api/preview/' + str(key) + '.png'


def generate(path, home=None):
    """Return the thumbnail key, or None when the input is not a readable PDF."""
    if not os.path.isfile(path) or not _is_pdf(path):
        return None
    content = _content_hash(path)
    key = hashlib.sha256(f'{content}:{SIZE}'.encode('ascii')).hexdigest()
    destination = cache_path(key, home)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if os.path.isfile(destination) and os.path.getsize(destination):
        return key

    qlmanage = shutil.which('qlmanage')
    pdftoppm = None if qlmanage else shutil.which('pdftoppm')
    if not qlmanage and not pdftoppm:
        raise PreviewError('找不到做 PDF 預覽的工具(macOS 的 qlmanage 或 poppler 的 pdftoppm)')
    with tempfile.TemporaryDirectory(prefix='pdf-preview-', dir=cache_dir(home)) as output:
        if qlmanage:
            command = [qlmanage, '-t', '-s', str(SIZE), '-o', output, os.path.abspath(path)]
        else:   # 不是 Mac:第一頁轉成長邊 SIZE 的 PNG
            command = [pdftoppm, '-png', '-singlefile', '-f', '1', '-l', '1', '-scale-to', str(SIZE),
                       os.path.abspath(path), os.path.join(output, 'page')]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        except Exception as exc:
            raise PreviewError(f'PDF 預覽產生失敗:{str(exc)[:160]}') from exc
        candidates = [os.path.join(output, name) for name in os.listdir(output)
                      if name.lower().endswith('.png')]
        if result.returncode or not candidates:
            detail = (result.stderr or result.stdout or '').strip()[-240:]
            raise PreviewError('PDF 預覽產生失敗' + (f':{detail}' if detail else ''))
        source = max(candidates, key=os.path.getsize)
        os.replace(source, destination)   # 暫存資料夾跟目的地在同一個預覽資料夾:直接換名
    return key


def read(key, home=None):
    path = cache_path(key, home)
    if not path or not os.path.isfile(path):
        return None
    with open(path, 'rb') as source:
        return source.read()
