# -*- coding: utf-8 -*-
"""畫面文字不用 GLOSSARY 要避免的詞。

掃的是會出現在他面前的字:看板頁面(board.js、header.html)的字串、tools/ 裡 Python 的字串
(不含 docstring、指令列 --help)、README 和 docs/ 的文件。註解不算。
只給開發者跑的工具(看板檢查、驗收腳本)不算畫面。
"""
import ast
import glob
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# 要擋的詞。每個都要在 GLOSSARY 的某一條 _Avoid_ 裡,GLOSSARY 才是用詞的依據
WORDS = ('代投',)

# 只給開發者跑、不會出現在他面前的工具
DEV_ONLY = ('tools/board_check*.py', 'tools/apply_accept.py', 'tools/realdata_check.py')

# 可以留著的:(檔案, 字串裡的一段, 為什麼)
ALLOWED = (
    # 給 agent 看的 prompt,不是畫面文字
    ('tools/apply_run.py', '你是代投 agent', '給 agent 的 prompt'),
)


def _allowed(path, text):
    return any(path == f and part in text for f, part, _ in ALLOWED)


def _docstrings(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _help_strings(tree):
    """argparse 的 help=…:只在終端機 --help 看得到。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == 'help':
                    out.update(id(n) for n in ast.walk(kw.value))
    return out


def python_strings(path):
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    skip = _docstrings(tree) | _help_strings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value


def _code_part(line):
    """一行 JS 去掉 // 註解。照引號狀態找:字串裡的 // (網址)不算註解。"""
    quote = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == '\\':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in '\'"`':
            quote = c
        elif line.startswith('//', i):
            return line[:i]
        i += 1
    return line


def page_lines(path):
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            yield n, _code_part(line) if path.endswith('.js') else re.sub(r'<!--.*?-->', '', line)


def doc_lines(path):
    with open(path, encoding='utf-8') as f:
        yield from enumerate(f, 1)


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, '/')


def hits():
    dev = {p for pat in DEV_ONLY for p in glob.glob(os.path.join(ROOT, pat))}
    found = []
    for path in sorted(glob.glob(os.path.join(ROOT, 'tools', '*.py'))):
        if path in dev:
            continue
        for n, text in python_strings(path):
            found += [(rel(path), n, w, text) for w in WORDS if w in text and text != w]
    for path in (os.path.join(ROOT, 'board', 'board.js'), os.path.join(ROOT, 'board', 'header.html')):
        for n, text in page_lines(path):
            # 整段字串就只是這個詞的('代投'):是存在資料裡的回報來源代號,畫面上顯示時會換成新的叫法
            text = re.sub(r"'(%s)'" % '|'.join(WORDS), '', text)
            found += [(rel(path), n, w, text.strip()) for w in WORDS if w in text]
    for path in [os.path.join(ROOT, 'README.md')] + sorted(glob.glob(os.path.join(ROOT, 'docs', '*.md'))):
        for n, text in doc_lines(path):
            found += [(rel(path), n, w, text.strip()) for w in WORDS if w in text]
    return [h for h in found if not _allowed(h[0], h[3])]


class ScreenWords(unittest.TestCase):
    def test_words_to_avoid_come_from_the_glossary(self):
        with open(os.path.join(ROOT, 'GLOSSARY.md'), encoding='utf-8') as f:
            avoid = ' '.join(line for line in f if line.startswith('_Avoid_'))
        for w in WORDS:
            self.assertIn(w, avoid, f'「{w}」不在 GLOSSARY 的 _Avoid_ 裡')

    def test_screen_text_does_not_use_words_to_avoid(self):
        bad = hits()
        self.assertEqual(bad, [], '畫面文字用了 GLOSSARY 要避免的詞:\n' + '\n'.join(
            f'{p}:{n} 「{w}」 {t[:80]}' for p, n, w, t in bad))

    def test_every_allowed_entry_still_matches_something(self):
        # 改掉之後就從清單拿掉,不留一條永遠用不到的豁免
        dev = {p for pat in DEV_ONLY for p in glob.glob(os.path.join(ROOT, pat))}
        texts = {}
        for path in glob.glob(os.path.join(ROOT, 'tools', '*.py')):
            if path not in dev:
                texts[rel(path)] = [t for _, t in python_strings(path)]
        for f, part, why in ALLOWED:
            with self.subTest(file=f, part=part):
                self.assertTrue(any(part in t and any(w in t for w in WORDS) for t in texts.get(f, [])),
                                f'{f} 已經沒有「{part}」這句({why}),從 ALLOWED 拿掉')


if __name__ == '__main__':
    unittest.main()
