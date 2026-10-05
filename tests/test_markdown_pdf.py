import os
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
import markdown_html  # noqa: E402
import markdown_pdf  # noqa: E402
import pdf_tools  # noqa: E402
import settings_api  # noqa: E402
import chrome_door  # noqa: E402


def _html(source, output, lang=''):
    """原稿排成 HTML,回排好的內容。"""
    markdown_html.convert(source, None, output, lang)
    with open(output, encoding='utf-8') as f:
        return f.read()


class OutputPath(unittest.TestCase):
    def test_the_pdf_lands_where_the_source_name_says(self):
        _env.use_home(self)
        self.assertEqual(markdown_pdf.output_path(os.path.join(self.home, 'resume', 'master.md')),
                         os.path.join(self.home, '.rendered', 'resume', 'master.pdf'))
        # 資料夾外面的原稿照完整路徑放,不能跑出 .rendered
        self.assertEqual(markdown_pdf.output_path('/elsewhere/cv.md'),
                         os.path.join(self.home, '.rendered', '_外部', 'elsewhere', 'cv.pdf'))


class MarkdownPdf(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(chrome_door, 'ego_bin', _env.REAL_EGO_BIN))

    @unittest.skipUnless(os.environ.get('JOBSALVO_EGO_INTEGRATION') == '1', '真 ego 排版需明確啟用')
    def test_markdown_prints_with_chrome_and_user_css_controls_page_size(self):
        with tempfile.TemporaryDirectory(prefix='markdown-pdf-') as directory:
            source = os.path.join(directory, 'resume.md')
            style = os.path.join(directory, 'style.css')
            native_pdf = os.path.join(directory, 'native.pdf')
            styled_pdf = os.path.join(directory, 'styled.pdf')
            with open(source, 'w', encoding='utf-8') as f:
                f.write('# Headless renderer\n\n| Field | Value |\n| --- | --- |\n| title | table marker |\n\n'
                        'First line\nSecond line\n\n```text\ncode marker\n```\n')

            markdown_pdf.render(source, native_pdf)

            with open(native_pdf, 'rb') as generated:
                self.assertEqual(generated.read(5), b'%PDF-')
            self.assertEqual(settings_api.pdf_pages(native_pdf), 1)
            text = settings_api.pdf_text(native_pdf)
            self.assertIn('Headless renderer', text)
            self.assertIn('table marker', text)
            self.assertIn('code marker', text)
            reader = pdf_tools.pypdf.PdfReader(native_pdf)
            native_size = (round(float(reader.pages[0].mediabox.width)),
                           round(float(reader.pages[0].mediabox.height)))

            with open(style, 'w', encoding='utf-8') as f:
                f.write('@page { size: A4; margin: 0; }')
            markdown_pdf.render(source, styled_pdf, style_path=style)
            reader = pdf_tools.pypdf.PdfReader(styled_pdf)
            styled_size = (round(float(reader.pages[0].mediabox.width)),
                            round(float(reader.pages[0].mediabox.height)))

            self.assertNotEqual(native_size, styled_size)
            self.assertEqual(styled_size, (595, 842))

    def test_markdown_html_is_inert_and_images_stay_under_source_directory(self):
        with tempfile.TemporaryDirectory(prefix='markdown-html-') as directory:
            source_dir = os.path.join(directory, 'resume')
            os.makedirs(source_dir)
            source = os.path.join(source_dir, 'resume.md')
            output = os.path.join(directory, 'resume.html')
            inside = os.path.join(source_dir, 'inside.png')
            outside = os.path.join(directory, 'secret.png')
            with open(inside, 'wb') as f:
                f.write(b'local synthetic image')
            with open(outside, 'wb') as f:
                f.write(b'outside secret marker')
            with open(source, 'w', encoding='utf-8') as f:
                f.write('![inside](inside.png)\n\n![outside](../secret.png)\n\n'
                        '![remote](https://example.invalid/remote.png)\n\n'
                        '<script>window.location="https://example.invalid/steal"</script>\n\n'
                        '<img src="inside.png" onerror="alert(1)">\n\n'
                        '[unsafe](javascript:alert(1))')

            rendered = _html(source, output)
            self.assertIn('data:image/png;base64,bG9jYWwgc3ludGhldGljIGltYWdl', rendered)
            self.assertNotIn('outside secret marker', rendered)
            self.assertNotIn('c2VjcmV0', rendered)
            self.assertNotIn('https://example.invalid/remote.png', rendered)
            self.assertNotIn('<script', rendered.lower())
            self.assertIn('&lt;script&gt;', rendered)
            self.assertIn('&lt;img src=', rendered)
            self.assertNotIn('<img src="inside.png" onerror=', rendered)
            self.assertNotIn('href="javascript:', rendered.lower())

    def test_markdown_html_keeps_safe_raw_image_class_without_enabling_html(self):
        with tempfile.TemporaryDirectory(prefix='markdown-raw-image-') as directory:
            source = os.path.join(directory, 'resume.md')
            image = os.path.join(directory, 'portrait.png')
            output = os.path.join(directory, 'resume.html')
            with open(image, 'wb') as target:
                target.write(b'synthetic image')
            with open(source, 'w', encoding='utf-8') as target:
                target.write('<img class="headshot" src="portrait.png">\n\n'
                             '```html\n<img class="headshot" src="portrait.png">\n```\n\n'
                             '<img src="portrait.png" onerror="alert(1)">\n')

            rendered = _html(source, output)
            self.assertRegex(rendered, r'<img[^>]*class="headshot"[^>]*src="data:image/png;base64,')
            self.assertIn('&lt;img class=&quot;headshot&quot; src=&quot;portrait.png&quot;&gt;',
                          rendered)
            self.assertIn('&lt;img src="portrait.png" onerror="alert(1)"&gt;', rendered)

    def test_markdown_html_applies_safe_leading_style_but_not_external_css(self):
        with tempfile.TemporaryDirectory(prefix='markdown-leading-style-') as directory:
            source = os.path.join(directory, 'resume.md')
            output = os.path.join(directory, 'resume.html')
            with open(source, 'w', encoding='utf-8') as target:
                target.write('<style>.headshot { float: right; }</style>\n\n# Synthetic resume\n')
            rendered = _html(source, output)
            self.assertIn('<head>', rendered)
            self.assertIn('<style>.headshot { float: right; }</style>', rendered)
            self.assertNotIn('&lt;style&gt;', rendered)
            self.assertIn('<h1>Synthetic resume</h1>', rendered)

            with open(source, 'w', encoding='utf-8') as target:
                target.write('<style>@import url(https://example.invalid/unsafe.css);</style>\n'
                             '# Synthetic resume\n')
            rendered = _html(source, output)
            self.assertNotIn('<style>@import', rendered)
            self.assertIn('&lt;style&gt;@import', rendered)

    def test_markdown_html_rejects_style_end_tag_inside_embedded_css(self):
        with tempfile.TemporaryDirectory(prefix='markdown-style-end-tag-') as directory:
            source = os.path.join(directory, 'resume.md')
            output = os.path.join(directory, 'resume.html')
            with open(source, 'w', encoding='utf-8') as target:
                target.write('<style>p{color:red}</style foo><script>alert(1)</script></style>\n'
                             '# Synthetic resume\n')
            rendered = _html(source, output)
            self.assertNotIn('<script>', rendered)
            self.assertIn('Synthetic resume', rendered)

    def test_markdown_that_cannot_be_converted_says_why(self):
        # 以前排版子程式出錯只回結束碼 1:他只看到「無法排成 PDF」,不知道是原稿哪裡有問題
        with tempfile.TemporaryDirectory(prefix='markdown-bad-bytes-') as directory:
            source = os.path.join(directory, 'resume.md')
            with open(source, 'wb') as target:
                target.write(b'# \xff\xfe not utf-8\n')
            with self.assertRaises(markdown_pdf.RenderError) as caught:
                markdown_pdf.render(source, os.path.join(directory, 'resume.pdf'))
        self.assertIn('UnicodeDecodeError', str(caught.exception))

    def test_markdown_html_hides_comments_outside_code_blocks(self):
        with tempfile.TemporaryDirectory(prefix='markdown-comments-') as directory:
            source = os.path.join(directory, 'resume.md')
            output = os.path.join(directory, 'resume.html')
            with open(source, 'w', encoding='utf-8') as target:
                target.write('Visible <!-- hidden inline --> text.\n\n'
                             '<!-- hidden standalone -->\n\n'
                             '<!-- hidden before list -->- **Bullet marker**\n\n'
                             '```html\n<!-- visible code example -->- still code\n```\n\n'
                             '    <!-- kept indented code -->- still code\n')
            rendered = _html(source, output)
            self.assertNotIn('hidden inline', rendered)
            self.assertNotIn('hidden standalone', rendered)
            self.assertIn('visible code example', rendered)
            self.assertIn('Visible', rendered)
            self.assertIn('<li><strong>Bullet marker</strong></li>', rendered)
            self.assertIn('still code', rendered)
            self.assertIn('&lt;!-- kept indented code --&gt;- still code', rendered)

    @unittest.skipUnless(os.environ.get('JOBSALVO_EGO_INTEGRATION') == '1', '真 ego 排版需明確啟用')
    def test_printing_cannot_reach_the_network(self):
        """排 PDF 只准讀本機檔:原稿裡的網址圖片不能真的去連(會洩漏在看誰的履歷,也會被卡住)。"""
        import http.server
        import threading
        import chrome_door
        hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix='markdown-network-') as directory:
                html = os.path.join(directory, 'in.html')
                with open(html, 'w', encoding='utf-8') as f:
                    f.write(f'<p>x</p><img src="http://127.0.0.1:{server.server_address[1]}/leak.png">')
                out = os.path.join(directory, 'out.pdf')
                chrome_door.EgoDoor().print_pdf(html, out, timeout=60)
                with open(out, 'rb') as f:
                    self.assertEqual(f.read(5), b'%PDF-')
        finally:
            server.shutdown()
        self.assertEqual(hits, [])

    def test_markdown_html_lang_follows_resume_language(self):
        """履歷 HTML 的 lang 跟著那份檔的語言(字型、斷字看它);沒給或認不出來照原本 zh-Hant。"""
        with tempfile.TemporaryDirectory(prefix='markdown-lang-') as directory:
            source = os.path.join(directory, 'resume.md')
            output = os.path.join(directory, 'resume.html')
            with open(source, 'w', encoding='utf-8') as target:
                target.write('# Synthetic resume\n')
            for lang, want in (('en', 'lang="en"'), ('ja', 'lang="ja"'), ('zh', 'lang="zh-Hant"'),
                               ('', 'lang="zh-Hant"'), ('file', 'lang="zh-Hant"')):
                with self.subTest(lang=lang):
                    self.assertIn(want, _html(source, output, lang))

if __name__ == '__main__':
    unittest.main()
