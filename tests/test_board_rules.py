import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, TOOLS)

import board_server as bs
import config as cf
import form_record
import settings_api


with open(os.path.join(HERE, 'fixtures', 'board-rule-cases.json'), encoding='utf-8') as f:
    CASES = json.load(f)


class SharedBoardRules(unittest.TestCase):
    def test_mark_empty_and_conflict_equivalence_cases(self):
        for case in CASES['mark_values']:
            with self.subTest(case=case['name']):
                self.assertEqual(bs._lean(case['value']), case['normalized'])
                self.assertEqual(bs._same(case['value'], case['base']), case['same'])

    def test_approval_cases(self):
        for case in CASES['approvals']:
            with self.subTest(case=case['name']):
                problem = form_record.approval_problem(case['state'], case['url'])
                self.assertEqual(problem, case['problem'])
                self.assertEqual(problem is not None, case['blocked'])

    def test_approve_preview_cases_agree_with_approval_problem(self):
        """看板的核准前預覽(approveBlocker)= 拿現在的答案當核准快照,跑同一套核准規則。
        Python 這邊用 form_record 照做一次,結果要跟案例表(看板那份由 board_check 跑)一樣。
        「agent 還沒填過」那條是看板多擋的一關(真的送出要叫回填表那段對話),Python 沒有,跳過。"""
        import copy
        for case in CASES['approve_blockers']:
            if case.get('problem_contains'):
                continue
            with self.subTest(case=case['name']):
                state = copy.deepcopy(case['state'])
                if (state.get(case['url']) or {}).get('form'):
                    state[case['url']]['approve'] = {'snap': form_record.snapshot(state, case['url'])}
                self.assertEqual(form_record.approval_problem(state, case['url']), case['problem'])

    @staticmethod
    def _settings_for(case):
        fallback = {'name': '其他', 'icon': '•', 'match': ''}
        categories = ([{'name': case['setting_name'], 'icon': '•', 'match': case['pattern']}, fallback]
                      if case['kind'] == 'category' else [fallback])
        tags = ([{'name': case['setting_name'], 'match': case['pattern']}]
                if case['kind'] == 'tag' else [])
        return {'board': {'categories': categories, 'tags': tags}}

    def _save_case(self, case):
        old_home = cf.HOME
        with tempfile.TemporaryDirectory(prefix='board-rule-settings-') as home:
            try:
                cf.reload(home)
                errors = settings_api.save({'settings': self._settings_for(case)})
                path = os.path.join(home, cf.NAME)
                saved = None
                if os.path.isfile(path):
                    with open(path, encoding='utf-8') as f:
                        saved = json.load(f)
                return errors, saved
            finally:
                cf.reload(old_home)

    def test_keyword_acceptance_and_match_cases(self):
        for case in CASES['regex']:
            with self.subTest(case=case['name']):
                try:
                    re.compile(case['pattern'], re.IGNORECASE)
                    python_valid = True
                except re.error:
                    python_valid = False
                self.assertEqual(python_valid, case['python_valid'])

                errors, saved = self._save_case(case)
                self.assertEqual(not errors, case['browser_valid'])
                if errors:
                    label = '類別' if case['kind'] == 'category' else '標籤'
                    self.assertIn(label, errors[0])
                    self.assertIn(case['setting_name'], errors[0])
                    self.assertIsNone(saved)
                    continue

                self.assertEqual(saved['board'], self._settings_for(case)['board'])
                regex = settings_api.compile_match(case['pattern'])
                text = case['target'] if case['kind'] == 'category' else case['body']
                self.assertEqual(regex.search(text) is not None, case['expected_match'])


if __name__ == '__main__':
    unittest.main()
