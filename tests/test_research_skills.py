# -*- coding: utf-8 -*-
"""產品附的預設找缺 skill 用「使用者」指使用者,不用「他」(每個使用者都會拿到這份,也看得到)。"""
import glob, os, re, unittest

SKILLS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools', 'research_skills'))


class NeutralUser(unittest.TestCase):
    def test_default_skills_do_not_call_the_user_he(self):
        bad = []
        for path in sorted(glob.glob(os.path.join(SKILLS, '*.md'))):
            with open(path, encoding='utf-8') as f:
                for n, line in enumerate(f, 1):
                    if re.search(r'(?<!其)他', line):
                        bad.append(f'{os.path.basename(path)}:{n}')
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
