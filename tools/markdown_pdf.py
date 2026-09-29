#!/usr/bin/env python3
"""Render Markdown with headless Chrome, using only an explicitly supplied stylesheet."""
import os
import shutil
import subprocess
import sys
import tempfile
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))


class RenderError(RuntimeError):
    pass


def output_path(source_path):
    """Stable private cache path for a Markdown source in the data folder."""
    source_path = os.path.abspath(os.path.expanduser(source_path))
    identity = hashlib.sha256(source_path.encode('utf-8')).hexdigest()[:20]
    import config as cf
    return os.path.join(cf.HOME, '.rendered', identity,
                        os.path.splitext(os.path.basename(source_path))[0] + '.pdf')


def _python_with_markdown():
    """轉 Markdown 用的 python:就是現在這個(整個程式跑在 uv 建的 .venv 裡,Markdown 套件在)。"""
    return sys.executable


def render(source_path, destination, style_path=None, lang=''):
    """Write a PDF atomically; without style_path, leave browser defaults in charge."""
    source_path = os.path.abspath(os.path.expanduser(source_path))
    destination = os.path.abspath(os.path.expanduser(destination))
    style_path = os.path.abspath(os.path.expanduser(style_path)) if style_path else ''
    if not os.path.isfile(source_path):
        raise RenderError('找不到 Markdown 原稿')
    if style_path and not os.path.isfile(style_path):
        raise RenderError('找不到指定的樣式檔')
    try:
        from chrome_bin import chrome
        chrome()
    except Exception as exc:
        raise RenderError('找不到可用的 Chrome') from exc

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    try:
        interpreter = _python_with_markdown()
        with tempfile.TemporaryDirectory(prefix='.markdown-pdf-', dir=os.path.dirname(destination)) as work:
            html_path = os.path.join(work, 'resume.html')
            pdf_path = os.path.join(work, 'resume.pdf')
            try:
                subprocess.run(
                    [interpreter, os.path.join(HERE, 'markdown_html.py'), source_path,
                     style_path, html_path, str(lang or '')], check=True, capture_output=True, timeout=120)
                import browser   # 共用瀏覽器(tools/browser.py):不再每份 PDF 開一整個 Chrome
                browser.print_pdf(html_path, pdf_path)
            except RenderError:
                raise
            except Exception as exc:
                raise RenderError('Markdown 原稿無法排成 PDF') from exc
            if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) < 5:
                raise RenderError('Chrome 沒有產生 PDF')
            with open(pdf_path, 'rb') as output:
                if output.read(5) != b'%PDF-':
                    raise RenderError('Chrome 產生的檔案不是 PDF')
            temporary = destination + '.tmp'
            shutil.copyfile(pdf_path, temporary)
            os.replace(temporary, destination)
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError('Markdown 排版失敗') from exc


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4):
        sys.exit('用法: markdown_pdf.py 原稿.md 輸出.pdf [樣式.css]')
    render(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None)
