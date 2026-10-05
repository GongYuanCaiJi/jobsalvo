import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import apply_profile_accept as acceptance


class AcceptanceIsolation(unittest.TestCase):
    def test_each_case_finishes_its_own_space_when_the_case_raises(self):
        import chrome_door
        run_id = 'edfa36e2-3223-44d1-885a-e10c139c49b0'
        user = {'id': 1, 'name': 'user-space', 'ownership': 'user'}
        ours = {'id': 2, 'name': f'jobsalvo-test-{run_id}-card', 'ownership': 'agent'}
        run = acceptance.ProfileAcceptance('/tmp/out', '/tmp/source', '/tmp/source/board',
                                           '/tmp/source/ship', '/tmp/runtime')
        with patch.object(chrome_door.uuid, 'uuid4', return_value=run_id), \
                patch.object(chrome_door.EgoDoor, 'workspaces', side_effect=[[user], [user, ours], [user]], create=True), \
                patch.object(chrome_door.EgoDoor, 'release') as release, \
                patch.object(run, '_run_case', side_effect=ValueError('驗收例外'), create=True):
            with self.assertRaisesRegex(ValueError, '驗收例外'):
                run.run_case('normal')
        release.assert_called_once_with(None, None)
        self.assertTrue(run.results['normal']['workspace_counts']['returned_to_baseline'])

    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix='profile-accept-test-'))
        self.source = os.path.join(self.temporary, 'source')
        os.makedirs(self.source)
        self.settings = {
            'board': {'file': 'board.html'},
            'browser': {'profile': 'Agent', 'state': os.path.join(self.temporary, 'agent.json')},
            'resume': {
                'base': 'resume.md', 'ship_dir': 'ship', 'prepare_dir': 'prepare',
                'resumes': [], 'attachments': [],
            },
            'paths': {
                'summaries': 'summaries', 'company_cache': 'company-cache',
                'research': '.research', 'prefs': 'prefs.md', 'apply_rules': 'apply-rules.md',
                'posted_cache': 'posted-cache.json', 'log': 'board-server.log',
                'tmp': '/tmp/jobsalvo-test-accept',
            },
        }
        with open(os.path.join(self.source, 'jobsalvo.json'), 'w', encoding='utf-8') as target:
            json.dump(self.settings, target)
        with open(os.path.join(self.source, 'board.html'), 'w', encoding='utf-8') as target:
            target.write('private board')
        with open(os.path.join(self.source, 'resume.md'), 'w', encoding='utf-8') as target:
            target.write('private resume')
        with open(os.path.join(self.source, 'prefs.md'), 'w', encoding='utf-8') as target:
            target.write('private prefs')
        with open(os.path.join(self.source, 'apply-rules.md'), 'w', encoding='utf-8') as target:
            target.write('private rules')
        with open(os.path.join(self.temporary, 'agent.json'), 'w', encoding='utf-8') as target:
            target.write('{"instance":"test"}')

    def test_clone_redirects_runtime_paths_and_browser_state(self):
        runtime = os.path.join(self.temporary, 'runtime')
        acceptance._clone_home(self.source, runtime)
        with open(os.path.join(runtime, 'jobsalvo.json'), encoding='utf-8') as source:
            settings = json.load(source)
        for value in (
            settings['board']['file'], settings['resume']['ship_dir'],
            settings['resume']['prepare_dir'], *settings['paths'].values(),
            settings['browser']['state'], settings['resume']['base'],
        ):
            resolved = value if os.path.isabs(value) else os.path.join(runtime, value)
            self.assertTrue(acceptance._inside(runtime, resolved), value)
        with open(os.path.join(runtime, settings['browser']['state']), encoding='utf-8') as source:
            self.assertEqual(json.load(source), {'instance': 'test'})
        with open(os.path.join(runtime, settings['resume']['base']), encoding='utf-8') as source:
            self.assertEqual(source.read(), 'private resume')
        with open(os.path.join(self.source, 'jobsalvo.json'), encoding='utf-8') as source:
            self.assertEqual(json.load(source), self.settings)

    def test_acceptance_refuses_to_run_on_the_source_home(self):
        runtime = os.path.join(self.temporary, 'runtime')
        run = acceptance.ProfileAcceptance(
            os.path.join(self.temporary, 'out'), self.source,
            os.path.join(self.source, 'board.html'), os.path.join(self.source, 'ship'),
            runtime,
        )
        run.runtime_home = os.path.realpath(self.source)
        with self.assertRaisesRegex(RuntimeError, '副本'):
            run.setup()
        self.assertFalse(os.path.exists(run.out))

    def test_acceptance_clone_exposes_only_synthetic_resume_and_rules(self):
        runtime = os.path.join(self.temporary, 'runtime')
        acceptance._clone_home(self.source, runtime)
        acceptance._sanitize_acceptance_home(runtime)

        with open(os.path.join(runtime, 'jobsalvo.json'), encoding='utf-8') as source:
            settings = json.load(source)
        resumes = settings['resume']['resumes']
        self.assertEqual([item['id'] for item in resumes], ['acceptance'])
        self.assertEqual(set(resumes[0]['files'].values()), {'resume/acceptance-profile.md'})
        self.assertEqual(settings['resume']['attachments'], [])
        self.assertFalse(os.path.exists(os.path.join(runtime, 'resume.md')))
        with open(os.path.join(runtime, 'resume/acceptance-profile.md'), encoding='utf-8') as source:
            self.assertIn('SYNTHETIC ACCEPTANCE PROFILE', source.read())
        with open(os.path.join(runtime, settings['paths']['prefs']), encoding='utf-8') as source:
            self.assertNotIn('private prefs', source.read())
        with open(os.path.join(self.source, 'resume.md'), encoding='utf-8') as source:
            self.assertEqual(source.read(), 'private resume')

    def test_public_summary_keeps_safe_evidence_and_omits_private_details(self):
        out = os.path.join(self.temporary, 'public')
        checks = [{
            'step': '合成情境',
            'what': '檔案上傳有伺服器紀錄',
            'ok': False,
            'evidence': 'private dialogue and form content',
            'public_evidence': '假平台上傳失敗事件=1；核准 ok=False',
        }]

        self.assertFalse(acceptance._write_public_summary(out, checks))
        with open(os.path.join(out, 'acceptance-summary.md'), encoding='utf-8') as source:
            summary = source.read()
        self.assertIn('假平台上傳失敗事件=1', summary)
        self.assertNotIn('private dialogue', summary)

    def test_public_blocker_reports_category_without_form_values(self):
        evidence = {
            'run_ok': False,
            'apply': {'issues': [
                '「private-question」頁面上是 \'private-answer\',常用答案是 \'private-bank-value\'',
            ]},
        }

        summary = acceptance.ProfileAcceptance._public_blockers(evidence)

        self.assertEqual(summary, '表單欄位核對')
        self.assertNotIn('private-', summary)

    def test_public_blocker_distinguishes_fixed_profile_attachment_errors(self):
        evidence = {
            'run_ok': False,
            'apply': {'issues': ['固定平台履歷附件「private-file.pdf」內容不同']},
        }

        summary = acceptance.ProfileAcceptance._public_blockers(evidence)

        self.assertIn('附件', summary)
        self.assertNotIn('private-file', summary)

    def test_acceptance_profile_and_answer_fixture_are_synthetic_and_complete(self):
        self.assertIn(b'SYNTHETIC ACCEPTANCE PROFILE', acceptance.CUSTOM_RESUME)
        self.assertIn(b'test.candidate@example.invalid', acceptance.CUSTOM_RESUME)
        answers = acceptance.synthetic_answers('2026-09-24')
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]['k'], 'acceptance-why')
        self.assertFalse(answers[0]['pj'])
        self.assertEqual(answers[0]['at'], '2026-09-24')
        self.assertIn('驗收', answers[0]['why'])

    def test_acceptance_board_uses_current_repo_shell_with_empty_data(self):
        import board_doc
        import config

        document = acceptance._acceptance_board_document()
        shell = board_doc.parse(document)
        for field, filename in (
            ('sty', 'board.css'), ('thdr', 'header.html'), ('app', 'board.js'),
        ):
            with open(os.path.join(config.BOARD_SRC, filename), encoding='utf-8') as source:
                self.assertEqual(shell[field], source.read())
        self.assertEqual(shell['data'], {'jobs': []})
        self.assertEqual(json.loads(shell['fb']), {})
        self.assertIn('.ap-approve:disabled', shell['sty'])


if __name__ == '__main__':
    unittest.main()
