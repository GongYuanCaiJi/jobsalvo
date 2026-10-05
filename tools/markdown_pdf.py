#!/usr/bin/env python3
"""Render Markdown with the browser door, using only an explicitly supplied stylesheet."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


class RenderError(RuntimeError):
    pass


def output_path(source_path):
    """排出來的 PDF 放哪:<資料夾>/.rendered/<原稿在資料夾裡的相對路徑>.pdf,從原稿就推得出來。
    原稿在資料夾外面的,照它的完整路徑放在 .rendered/_外部/ 底下。"""
    import config as cf
    source_path = os.path.abspath(os.path.expanduser(source_path))
    relative = os.path.relpath(source_path, cf.HOME)
    if relative.startswith(os.pardir + os.sep):
        relative = os.path.join('_外部', source_path.lstrip(os.sep))
    return os.path.join(cf.HOME, '.rendered', os.path.splitext(relative)[0] + '.pdf')


def render(source_path, destination, style_path=None, lang=''):
    """Write a PDF atomically; without style_path, leave browser defaults in charge."""
    source_path = os.path.abspath(os.path.expanduser(source_path))
    destination = os.path.abspath(os.path.expanduser(destination))
    style_path = os.path.abspath(os.path.expanduser(style_path)) if style_path else ''
    if not os.path.isfile(source_path):
        raise RenderError('找不到 Markdown 原稿')
    if style_path and not os.path.isfile(style_path):
        raise RenderError('找不到指定的樣式檔')
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.markdown-pdf-', dir=os.path.dirname(destination)) as work:
            html_path = os.path.join(work, 'resume.html')
            pdf_path = os.path.join(work, 'resume.pdf')
            try:
                subprocess.run(
                    [sys.executable, os.path.join(HERE, 'markdown_html.py'), source_path,
                     style_path, html_path, str(lang or '')], check=True, capture_output=True, timeout=120)
                if os.environ.get('JOBSALVO_DEV_PDF') == '1':     # 開發工具的副本(看板檢查、截圖),見 dev_pdf
                    import dev_pdf
                    dev_pdf.print_pdf(html_path, pdf_path)
                else:
                    import chrome_door
                    chrome_door.EgoDoor().print_pdf(html_path, pdf_path)
            except RenderError:
                raise
            except Exception as exc:
                stderr = getattr(exc, 'stderr', b'') or b''
                why = (stderr.decode('utf-8', 'replace') if isinstance(stderr, bytes) else stderr).strip().splitlines()
                if not why:
                    why = [str(exc)]
                raise RenderError('Markdown 原稿無法排成 PDF' + (f':{why[-1][:200]}' if why else '')) from exc
            if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) < 5:
                raise RenderError('瀏覽器沒有產生 PDF')
            with open(pdf_path, 'rb') as output:
                if output.read(5) != b'%PDF-':
                    raise RenderError('瀏覽器產生的檔案不是 PDF')
            os.replace(pdf_path, destination)   # 暫存資料夾就在目的地同層:直接換名
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError('Markdown 排版失敗') from exc


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4):
        sys.exit('用法: markdown_pdf.py 原稿.md 輸出.pdf [樣式.css]')
    render(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None)
