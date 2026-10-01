import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, TOOLS)

import board_server as bs
import delivery_state
import config as cf
import settings_api


with open(os.path.join(HERE, 'fixtures', 'board-rule-cases.json'), encoding='utf-8') as f:
    CASES = json.load(f)
# 案例沒寫 status 的:投遞前驗收跑過、沒有問題(看板那邊沿用副本的驗收結果,也是這樣)
PASSED = {'schema_version': 2, 'checked_links': True, 'issues': []}


class SharedBoardRules(unittest.TestCase):
    def test_mark_empty_and_conflict_equivalence_cases(self):
        for case in CASES['mark_values']:
            with self.subTest(case=case['name']):
                self.assertEqual(delivery_state.lean(case['value']), case['normalized'])   # 伺服器比對標記用的那一份
                self.assertEqual(bs._same(case['value'], case['base']), case['same'])

    def test_custom_pending_cases(self):
        """客製還沒處理完擋不擋這張:只看這張現在會寄的那幾份(ship.documents)。
        看板不自己算:卡上顯示的是後台 ship.card_files 帶回去的這一句(pending)。"""
        from unittest import mock
        import ship
        table = CASES['custom_pending']
        cfg, job = table['cfg'], table['job']
        with mock.patch.object(cf, 'LANGS', cfg['langs']), \
                mock.patch.object(cf, 'RESUMES', {r['id']: r for r in cfg['resumes']}), \
                mock.patch.object(cf, 'ATTACHMENTS', cfg['attachments']):
            for case in table['cases']:
                with self.subTest(case=case['name']):
                    self.assertEqual(ship.customization_problem(job, {job['id']: case['mark']}), case['problem'])

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
