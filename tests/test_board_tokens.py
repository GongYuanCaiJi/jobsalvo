# -*- coding: utf-8 -*-
"""看板的 token 跟規格對得上:board.css 的 :root 定義的每個 token 都寫在 docs/board-design.md、
規格列的每個 token 都有定義、board.css / board.js 用到的每個 var(--x) 都有定義。
寫死的值擋在 stylelint(.stylelintrc.json),這裡只對名字。"""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


CSS = re.sub(r'/\*.*?\*/', '', read('board/board.css'), flags=re.S)
DEFINED = {n for blk in re.findall(r':root(?:\[[^\]]*\])?\s*\{([^}]*)\}', CSS) for n in re.findall(r'(--[\w-]+)\s*:', blk)}
DOC = set(re.findall(r'`(--[\w-]+)`', read('docs/board-design.md')))
USED = set(re.findall(r'var\(\s*(--[\w-]+)', CSS + read('board/board.js')))

COMPONENT_SECTION = re.search(
    r'^## 元件\s*$([\s\S]*?)(?=^## |\Z)', read('docs/board-design.md'), flags=re.M
).group(1)
COMPONENT_SNIPPETS = re.findall(r'`([^`]+)`', COMPONENT_SECTION)
DOC_CLASSES = {n for s in COMPONENT_SNIPPETS for n in re.findall(r'\.([A-Za-z_][\w-]*)', s)}
DOC_IDS = {n for s in COMPONENT_SNIPPETS for n in re.findall(r'#([A-Za-z_][\w-]*)', s)}
DOC_DATA_ATTRIBUTES = {n for s in COMPONENT_SNIPPETS for n in re.findall(r'\b(data-[\w-]+)\b', s)}
BOARD_SOURCE = '\n'.join(read(path) for path in (
    'board/board.js', 'board/header.html', 'tools/board_doc.py',
))
BOARD_SOURCE = re.sub(r'/\*.*?\*/|<!--.*?-->', '', BOARD_SOURCE, flags=re.S)
BOARD_SOURCE = re.sub(r'(?m)^[ \t]*//.*$', '', BOARD_SOURCE)
BOARD_SOURCE = re.sub(
    r"""(?:\.(?:querySelector|querySelectorAll|closest|matches)|\.classList\.contains|getElementById|\$)\(\s*(['"]).*?\1\s*\)""",
    '', BOARD_SOURCE,
)
ID_SOURCE = ' '.join(re.findall(r"""(?:\bid\s*=\s*|\.id\s*=\s*)['"]([^'"]+)['"]""", BOARD_SOURCE))


class BoardTokens(unittest.TestCase):
    def test_root_has_tokens(self):
        self.assertGreater(len(DEFINED), 20)

    def test_every_token_is_in_spec(self):
        self.assertEqual(sorted(DEFINED - DOC), [], 'board.css 定義了、docs/board-design.md 沒寫')

    def test_every_spec_token_is_defined(self):
        self.assertEqual(sorted(DOC - DEFINED), [], 'docs/board-design.md 寫了、board.css 沒定義')

    def test_every_var_is_defined(self):
        self.assertEqual(sorted(USED - DEFINED), [], '用了沒定義的 var(--…)')

    def test_no_custom_property_outside_root(self):
        outside = re.sub(r':root(?:\[[^\]]*\])?\s*\{[^}]*\}', '', CSS)
        self.assertEqual(re.findall(r'(?<![\w-])(--[\w-]+)\s*:', outside), [], 'token 只准定義在 :root(深色主題那段是 :root[data-theme="dark"])')


class BoardComponents(unittest.TestCase):
    def test_every_documented_component_name_exists_in_board_code(self):
        checks = ((DOC_CLASSES, BOARD_SOURCE), (DOC_IDS, ID_SOURCE),
                  (DOC_DATA_ATTRIBUTES, BOARD_SOURCE))
        missing = sorted({name for names, source in checks for name in names
                          if not re.search(r'(?<![\w-])' + re.escape(name) + r'(?![\w-])', source)})
        self.assertEqual(missing, [], 'docs/board-design.md 的「元件」列了、產生來源找不到：' + ', '.join(missing))

if __name__ == '__main__':
    unittest.main()
