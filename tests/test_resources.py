# -*- coding: utf-8 -*-
"""資源用量:開過的無頭 Chrome 用完要收乾淨,不能留下在背景吃 CPU 和記憶體的程序。
每一個會開無頭 Chrome 的地方(讀職缺頁、轉 PDF)各跑一次,前後數一次。"""
import os, subprocess, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
import chrome_bin  # noqa: E402


def headless():
    out = subprocess.run(['ps', '-A', '-o', 'pid=,command='], capture_output=True, text=True).stdout
    return {line.split(None, 1)[0] for line in out.splitlines()
            if '--headless' in line and ('hrome' in line or 'hromium' in line)}


def settle(before, wait=6):
    """等到沒有多出來的無頭 Chrome,最多等幾秒;回還留著的。"""
    left = headless() - before
    end = time.time() + wait
    while left and time.time() < end:
        time.sleep(0.5)
        left = headless() - before
    return left


@unittest.skipUnless(chrome_bin.chrome(), '沒有 Chrome')
class NoLeftoverChrome(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='resources-')
        self.page = os.path.join(self.dir, 'page.html')
        with open(self.page, 'w', encoding='utf-8') as f:
            f.write('<html><body><h1>測試用的職缺</h1><p>工作內容</p></body></html>')

    def _run(self, code):
        """在另一個程式裡用共用瀏覽器做事;那個程式結束後,它開的 Chrome 要全部收掉。"""
        before = headless()
        env = dict(os.environ, PYTHONPATH=TOOLS)
        done = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, done.stderr[-600:])
        return before, done.stdout

    def test_reading_pages_leaves_no_chrome_behind(self):
        before, out = self._run("import page_fetch\n"
                                "for u in ('https://example.com/', 'https://example.org/'):\n"
                                "    print(page_fetch._chrome(u).text[:40].replace(chr(10), ' '))")
        # 這條要驗的是程式結束後不留下 Chrome;頁面內容只確認兩頁都讀到了。example.com 會改字
        # (2026-09 拿掉了「Example Domain」標題,CI 全紅),不比對特定的字
        self.assertEqual(len([x for x in out.splitlines() if x.strip()]), 2, out)
        self.assertEqual(settle(before), set(), '讀完頁面、程式結束後還留著無頭 Chrome')

    def test_rendering_pdfs_leaves_no_chrome_behind(self):
        src = os.path.join(self.dir, 'resume.md')
        with open(src, 'w', encoding='utf-8') as f:
            f.write('# 測試履歷\n\n一行字。\n')
        out = os.path.join(self.dir, 'resume.pdf')
        before, _ = self._run(f"import markdown_pdf\nfor _ in range(2): markdown_pdf.render({src!r}, {out!r})")
        self.assertTrue(os.path.getsize(out) > 0)
        self.assertEqual(settle(before), set(), '轉完 PDF、程式結束後還留著無頭 Chrome')

if __name__ == '__main__':
    unittest.main()
