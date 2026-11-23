import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')))
import interview_bank as ib
import iv


def _bank():
    item = lambda i, t, **kw: ib.from_form({'id': i, 't': t, 'cat': '你做過的事', 'sub': 'jobsalvo', 'stage': 'todo', **kw})
    b = {'items': [item('top', '最困難的事', ask='說一件最難的事', focus='拆解'), item('kid', '那你怎麼解？', parent='top'),
                   item('own', '為什麼不用 104 就好？')], 'links': {}, 'rounds': {}, 'cats': ['你做過的事']}
    b = ib.apply(b, 'share', None, 'top', None, True)
    return ib.apply(b, 'link', {'src': 'guess', 'why': 'x'}, 'own', 'J', 'prep')


class IvLandingTest(unittest.TestCase):
    def test_says_section_group_and_empty_fields_the_board_will_show(self):
        out = '\n'.join(iv.landing(_bank(), 'J', 'own'))
        self.assertIn('預想的 › 你做過的事 · jobsalvo › 為什麼不用 104 就好？', out)
        self.assertIn('題面', out)
        self.assertIn('考點', out)

    def test_follow_up_of_a_shared_question_says_it_goes_to_every_interview(self):
        out = '\n'.join(iv.landing(_bank(), 'J', 'kid'))
        self.assertIn('共有 › 你做過的事 · jobsalvo › 最困難的事 › 追問「那你怎麼解？」', out)
        self.assertIn('每一場', out)

    def test_follow_up_sits_in_its_parents_section_like_the_board(self):
        b = _bank()
        b['items'].append(ib.from_form({'id': 'p2', 't': '原題', 'cat': '你做過的事', 'stage': 'todo'}))
        b['items'].append(ib.from_form({'id': 'k2', 't': '接著問', 'parent': 'p2', 'cat': '技術基本功', 'stage': 'todo'}))
        b = ib.apply(b, 'link', {'src': 'this', 'cite': 'https://e.x/a', 'quote': 'q'}, 'p2', 'J', 'prep')
        b = ib.apply(b, 'link', {'src': 'guess', 'why': 'x'}, 'k2', 'J', 'prep')
        self.assertIn('這家考過 › 你做過的事 › 原題 › 追問「接著問」', iv.landing(b, 'J', 'k2')[0])

    def test_follow_up_whose_parent_is_not_in_this_interview_is_its_own_line(self):
        b = _bank()
        b['items'].append(ib.from_form({'id': 'p3', 't': '別場的原題', 'cat': '你做過的事', 'stage': 'todo'}))
        b['items'].append(ib.from_form({'id': 'k3', 't': '接著問', 'parent': 'p3', 'cat': '技術基本功', 'stage': 'todo'}))
        b = ib.apply(b, 'link', {'src': 'guess', 'why': 'x'}, 'k3', 'J', 'prep')
        out = '\n'.join(iv.landing(b, 'J', 'k3'))
        self.assertIn('預想的 › 技術基本功 › 接著問', out)
        self.assertIn('追問自「別場的原題」', out)

    def test_question_with_ask_and_focus_has_no_empty_field_note(self):
        out = '\n'.join(iv.landing(_bank(), 'J', 'top'))
        self.assertNotIn('空著', out)


if __name__ == '__main__':
    unittest.main()
