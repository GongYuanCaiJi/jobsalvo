#!/usr/bin/env python3
"""Convert one Markdown source to a temporary, self-contained HTML document."""
import base64
import html
import mimetypes
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import markdown
from markdown.inlinepatterns import InlineProcessor
from markdown.preprocessors import Preprocessor


IMAGE = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])(.*?)(\2)', re.I | re.S)
HREF = re.compile(r'(\bhref\s*=\s*)(["\'])(.*?)(\2)', re.I | re.S)
SAFE_IMAGES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp'}
LEADING_STYLE = re.compile(r'\A\ufeff?\s*<style\s*>(.*?)</style\s*>', re.IGNORECASE | re.DOTALL)


def css_is_safe(css):
    if re.search(r'</style\b', css, flags=re.IGNORECASE):
        return False
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    css = re.sub(r'\\([0-9a-fA-F]{1,6}\s?|.)',
                 lambda match: chr(int(match[1].strip(), 16)) if match[1][0] in '0123456789abcdefABCDEF'
                 else match[1], css)
    return not re.search(r'@import\b|url\s*\(', css, flags=re.IGNORECASE)


class _ImageTag(HTMLParser):
    def __init__(self):
        super().__init__()
        self.attrs = None
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if tag != 'img' or self.attrs is not None:
            self.invalid = True
        else:
            self.attrs = attrs

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, _tag):
        self.invalid = True


class _SafeRawImage(InlineProcessor):
    """Permit one inert local image tag while Markdown escapes all other raw HTML."""

    def handleMatch(self, match, data):
        tag = _ImageTag()
        tag.feed(match.group(0))
        attrs = tag.attrs or []
        names = [name for name, _value in attrs]
        if (tag.invalid or len(names) != len(set(names)) or
                set(names) - {'src', 'alt', 'class'}):
            return None, None, None
        values = dict(attrs)
        source = values.get('src') or ''
        parsed = urlsplit(source)
        if (not source or parsed.netloc or (parsed.scheme and parsed.scheme != 'data') or
                (not parsed.scheme and os.path.isabs(unquote(parsed.path)))):
            return None, None, None
        classes = values.get('class') or ''
        if classes and not re.fullmatch(r'[A-Za-z0-9_-]+(?:\s+[A-Za-z0-9_-]+)*', classes):
            return None, None, None
        image = ElementTree.Element('img')
        if classes:
            image.set('class', classes)
        image.set('src', source)
        if 'alt' in values:
            image.set('alt', values['alt'])
        return image, match.start(0), match.end(0)


class _InertComment(InlineProcessor):
    def handleMatch(self, match, data):
        return '', match.start(0), match.end(0)


class _LeadingCommentBeforeList(Preprocessor):
    """Expose a list marker hidden behind a same-line HTML comment."""

    def run(self, lines):
        pattern = r'^([ ]{0,3})<!--.*?-->(?=[ \t]*(?:[-*+]|\d+[.)])[ \t]+)'
        return [re.sub(pattern, r'\1', line) for line in lines]


def inline_local_images(body, base_dir):
    base_dir = os.path.realpath(base_dir)

    def replace(match):
        source = html.unescape(match.group(3)).strip()
        quote = match.group(2)
        if source.startswith('data:'):
            mime = source[5:].split(';', 1)[0].lower()
            return match.group(0) if mime in SAFE_IMAGES and ';base64,' in source else match.group(1) + quote + quote
        parsed = urlsplit(source)
        if parsed.scheme and parsed.scheme != 'file':
            return match.group(1) + quote + quote
        image_path = unquote(parsed.path)
        if parsed.scheme == 'file' and image_path.startswith('//'):
            image_path = image_path[1:]
        if not os.path.isabs(image_path):
            image_path = os.path.join(base_dir, image_path)
        image_path = os.path.realpath(image_path)
        try:
            if os.path.commonpath((base_dir, image_path)) != base_dir:
                return match.group(1) + quote + quote
        except ValueError:
            return match.group(1) + quote + quote
        mime = mimetypes.guess_type(image_path)[0] or ''
        if mime not in SAFE_IMAGES or not os.path.isfile(image_path):
            return match.group(1) + quote + quote
        try:
            with open(image_path, 'rb') as image_file:
                encoded = base64.b64encode(image_file.read()).decode('ascii')
        except OSError:
            return match.group(1) + quote + quote
        return f'{match.group(1)}{quote}data:{mime};base64,{encoded}{quote}'

    return IMAGE.sub(replace, body)


def remove_unsafe_links(body):
    def replace(match):
        value = html.unescape(match.group(3)).strip()
        scheme = urlsplit(value).scheme.lower()
        if scheme and scheme not in {'http', 'https', 'mailto', 'tel'}:
            return ''
        if value.startswith('//'):
            return ''
        return match.group(0)

    return HREF.sub(replace, body)


def html_lang(code):
    """設定裡的履歷語言代碼 → HTML lang。字型、斷字跟著它走。zh 沿用原本的 zh-Hant;認不出來的也是。"""
    code = str(code or '').strip()
    known = {'zh': 'zh-Hant', 'zh-tw': 'zh-Hant', 'zh-hk': 'zh-Hant', 'zh-cn': 'zh-Hans'}
    if code.lower() in known:
        return known[code.lower()]
    return code if re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z0-9]+)*', code) else 'zh-Hant'


def convert(source_path, style_path, output_path, lang=''):
    source_path = os.path.realpath(source_path)
    base_dir = os.path.dirname(source_path)
    with open(source_path, encoding='utf-8') as source:
        raw = source.read()
        leading_style = LEADING_STYLE.match(raw)
        embedded_css = ''
        if leading_style and css_is_safe(leading_style.group(1)):
            embedded_css = leading_style.group(1)
            raw = raw[leading_style.end():]
        parser = markdown.Markdown(extensions=['tables', 'fenced_code', 'nl2br', 'sane_lists'])
        # Keep Markdown-generated tags, but render source-authored HTML as text.
        parser.preprocessors.deregister('html_block')
        parser.preprocessors.register(_LeadingCommentBeforeList(parser),
                                      'leading_comment_before_list', 24)
        parser.inlinePatterns.deregister('html')
        parser.inlinePatterns.register(_InertComment(r'<!--.*?-->', parser),
                                       'inert_comment', 171)
        parser.inlinePatterns.register(_SafeRawImage(r'(?i)<img\b[^>]*>', parser),
                                       'safe_raw_image', 170)
        body = parser.convert(raw)
    body = inline_local_images(body, base_dir)
    body = remove_unsafe_links(body)
    base_url = Path(base_dir).as_uri().rstrip('/') + '/'
    style = ''
    if style_path:
        style_url = Path(os.path.realpath(style_path)).as_uri()
        style = f'<link rel="stylesheet" href="{html.escape(style_url, quote=True)}">'
    if embedded_css:
        style += f'<style>{embedded_css}</style>'
    document = (f'<!doctype html><html lang="{html_lang(lang)}"><head><meta charset="utf-8">'
                f'<base href="{html.escape(base_url, quote=True)}">{style}'
                f'</head><body>{body}</body></html>')
    with open(output_path, 'w', encoding='utf-8') as output:
        output.write(document)


def main(argv):
    if len(argv) not in (3, 4):
        return 2
    try:
        convert(argv[0], argv[1] or None, argv[2], argv[3] if len(argv) == 4 else '')
    except Exception as e:  # noqa: BLE001 — 子程式最外層:原因印到 stderr(呼叫的 markdown_pdf 放進錯誤訊息),回 1
        print(f'{type(e).__name__}: {str(e)[:200]}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
