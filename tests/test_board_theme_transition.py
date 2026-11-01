"""切換淺深色時，文字與底色必須同步換色。"""
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ThemeTransitionTest(unittest.TestCase):
    def test_company_headers_and_decision_buttons_do_not_transition_colors(self):
        css = (ROOT / 'board/board.css').read_text()
        for selector in ('.cohead', '.fb-b'):
            with self.subTest(selector=selector):
                rule = re.search(r'^' + re.escape(selector) + r'\{([^}]*)\}', css, re.M)
                self.assertIsNotNone(rule, selector + ' CSS 規則不存在')
                self.assertNotRegex(rule.group(1), r'(?<!-)transition\s*:\s*(?!transform(?:\s|,|$))',
                                    selector + ' 在主題切換時仍會插值文字或背景顏色')

