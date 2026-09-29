"""Per-card resume and attachment customization workflow."""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
from _env import read_board, read_fb  # noqa: E402
sys.path.insert(0, TOOLS)
import agent_report  # noqa: E402
import board_doc as bd  # noqa: E402
import config as cf  # noqa: E402
import customize  # noqa: E402
import settings_api as sa  # noqa: E402
import ship  # noqa: E402


class Customization(unittest.TestCase):
    url = 'test://jobs/12'
    other_url = 'test://jobs/13'

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='customize-test-')
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(self.home)
        self.old_home_env = os.environ.get('JOBSALVO_HOME')
        self.old_home = cf.HOME
        self.settings = copy.deepcopy(cf.DEFAULTS)
        self.settings['board']['file'] = 'board.html'
        self.settings['resume']['base'] = 'resume'
        self.settings['resume']['ship_dir'] = 'ship'
        self.settings['resume']['langs'] = ['zh']
        self.settings['resume']['resumes'] = [{
            'id': 'general', 'name': '通用履歷', 'files': {'zh': 'resume/base.pdf'},
            'enabled': True, 'when': '', 'skill': '',
        }]
        self.settings['resume']['attachments'] = [{
            'id': 'portfolio', 'name': '作品集', 'files': {'zh': 'resume/portfolio.pdf'},
            'enabled': True, 'skill': 'custom/skills/portfolio.md',
        }]
        self._write_settings()
        os.environ['JOBSALVO_HOME'] = self.home
        cf.reload(self.home)
        self.board = os.path.join(self.home, 'board.html')
        os.makedirs(os.path.join(self.home, 'resume'), exist_ok=True)
        for name in ('base.pdf', 'portfolio.pdf'):
            self._write_pdf(os.path.join(self.home, 'resume', name), 2)
        skill_path = os.path.join(self.home, 'custom', 'skills', 'portfolio.md')
        os.makedirs(os.path.dirname(skill_path), exist_ok=True)
        with open(skill_path, 'w', encoding='utf-8') as f:
            f.write('<!-- jobsalvo-skill: 作品集規則 -->\n\n突出作品與可查證成果。\n')
        jobs = [
            {'id': self.url, 'target': '工程師 · Acme'},
            {'id': self.other_url, 'target': '工程師 · Beta'},
        ]
        fb = {url: {'app': 'ready', 'resume_id': 'general', 'lang': 'zh'}
              for url in (self.url, self.other_url)}
        data = bd.assemble(':root{}', '<b id="stat-first">0</b>', '', {'jobs': jobs},
                           json.dumps(fb, ensure_ascii=False), '/*app v1*/')
        with open(self.board, 'w', encoding='utf-8') as f:
            f.write(data)

    def tearDown(self):
        if self.old_home_env is None:
            os.environ.pop('JOBSALVO_HOME', None)
        else:
            os.environ['JOBSALVO_HOME'] = self.old_home_env
        cf.reload(self.old_home)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_settings(self):
        with open(os.path.join(self.home, cf.NAME), 'w', encoding='utf-8') as f:
            json.dump(self.settings, f, ensure_ascii=False)

    def _fb(self):
        return read_fb(self.board)

    def _job(self, url=None):
        url = url or self.url
        data = read_board(self.board)['data']
        return next(j for j in data['jobs'] if j['id'] == url)

    @staticmethod
    def _pages(path):
        return len(sa.pdf._pypdf().PdfReader(path).pages)

    @staticmethod
    def _write_pdf(path, pages):
        writer = sa.pdf._pypdf().PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=612, height=792)
        with open(path, 'wb') as f:
            writer.write(f)

    def _agent(self, captured, output_pages=None):
        output_pages = output_pages or {}

        def launch(prompt, output_log, home, **_kwargs):
            captured.append(prompt)
            marker = '本輪檔案與使用者指示(JSON):\n'
            payload = prompt.split(marker, 1)[1].split('\n\n其他卡片尚未處理', 1)[0]
            for item in json.loads(payload):
                count = output_pages.get(item['id'], 1)
                self._write_pdf(item['output_pdf'], count)
            report_path = prompt.split('整理到 ', 1)[1].split('，格式', 1)[0]
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump({'reports': []}, f)
            return object()

        return launch

    @staticmethod
    def _waiter(_processes, timeout):
        return [SimpleNamespace(ok=True, message=lambda: '')]

    def _run(self, ids, captured=None, output_pages=None):
        captured = captured if captured is not None else []
        with mock.patch.object(sa, 'pdf_pages', side_effect=self._pages):
            result = customize.run_customization(
                self.board, self.url, ids, sp=os.path.join(self.tmp, 'runs'),
                launcher=self._agent(captured, output_pages), waiter=self._waiter,
            )
        return result, captured

    def test_one_agent_customizes_selected_files_and_accepted_files_enter_package(self):
        files = customize.list_files(self.url, board=self.board)
        by_id = {item['id']: item for item in files}
        self.assertTrue(by_id['attachment:portfolio']['default_checked'])
        self.assertFalse(by_id['resume:general']['default_checked'])
        self.assertEqual(by_id['resume:general']['skill'], '')

        result, prompts = self._run(['resume:general', 'attachment:portfolio'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(prompts), 1)  # 一張卡的多份檔案共用一個 agent
        marker = '本輪檔案與使用者指示(JSON):\n'
        payload = json.loads(prompts[0].split(marker, 1)[1].split('\n\n其他卡片尚未處理', 1)[0])
        self.assertEqual({item['id'] for item in payload}, {'resume:general', 'attachment:portfolio'})
        self.assertIn('突出作品與可查證成果', next(x for x in payload if x['id'] == 'attachment:portfolio')['skill_text'])
        self.assertTrue(next(x for x in payload if x['id'] == 'resume:general')['skill_text'])  # 使用產品通用 skill

        fb = self._fb()
        self.assertEqual(fb[self.url]['custom_docs']['resume:general']['status'], 'review')
        self.assertEqual(fb[self.url]['custom_docs']['attachment:portfolio']['status'], 'review')
        self.assertTrue(ship.customization_problem(self._job(), fb))
        _folder, problems = ship.build_default(self._job(), fb)
        self.assertIn('等你看', problems[0])

        self.assertEqual(customize.accept(self.url, 'resume:general', board=self.board), (True, ''))
        self.assertTrue(ship.customization_problem(self._job(), self._fb()))  # 另一份仍待審
        self.assertEqual(customize.accept(self.url, 'attachment:portfolio', board=self.board), (True, ''))
        fb = self._fb()
        self.assertEqual(ship.customization_problem(self._job(), fb), '')
        resume, attachments, _own = ship.sources(self._job(), fb)
        self.assertIn('/custom/', resume)
        self.assertEqual(len(attachments), 1)
        _folder, problems = ship.build_default(self._job(), fb)
        self.assertEqual(problems, [])
        self.assertEqual(len(ship.info(self.url)['files']), 2)

    def test_page_limit_failure_is_recorded_and_reported(self):
        result, _prompts = self._run(['resume:general'], output_pages={'resume:general': 3})
        self.assertFalse(result['ok'])
        self.assertIn('超過原檔', result['failures'][0])
        fb = self._fb()
        entry = fb[self.url]['custom_docs']['resume:general']
        self.assertEqual(entry['status'], 'failed')
        self.assertIn('超過原檔', entry['error'])
        reports = agent_report.open_items(fb)
        self.assertEqual(len(reports), 1)
        self.assertIn('超過原檔', reports[0]['msg'])
        self.assertEqual(ship.customization_problem(self._job(), fb), '')

    def test_agent_start_failure_does_not_leave_the_card_locked(self):
        def fail_to_launch(*_args, **_kwargs):
            raise RuntimeError('sandbox unavailable')

        with mock.patch.object(sa, 'pdf_pages', side_effect=self._pages):
            result = customize.run_customization(
                self.board, self.url, ['resume:general'], sp=os.path.join(self.tmp, 'runs'),
                launcher=fail_to_launch, waiter=self._waiter,
            )
        self.assertFalse(result['ok'])
        entry = self._fb()[self.url]['custom_docs']['resume:general']
        self.assertEqual(entry['status'], 'failed')
        self.assertIn('sandbox unavailable', entry['error'])
        self.assertEqual(ship.customization_problem(self._job(), self._fb()), '')

    def test_rejected_feedback_is_included_in_the_next_agent_prompt(self):
        self._run(['resume:general'])
        self.assertEqual(customize.reject(self.url, 'resume:general', '把技能年資放回摘要', board=self.board), (True, ''))
        self.assertIn('退回重寫中', ship.customization_problem(self._job(), self._fb()))
        result, prompts = self._run(['resume:general'])
        self.assertTrue(result['ok'], result)
        self.assertIn('把技能年資放回摘要', prompts[0])
        self.assertEqual(self._fb()[self.url]['custom_docs']['resume:general']['status'], 'review')

    def test_manual_pdf_upload_is_already_accepted(self):
        saved, error = customize.upload_custom(
            self.url, 'resume:general', 'manual.pdf', b'%PDF-1.4\npages=2\nmanual', board=self.board)
        self.assertFalse(error)
        self.assertTrue(os.path.isfile(sa.safe_rel(saved)))
        fb = self._fb()
        entry = fb[self.url]['custom_docs']['resume:general']
        self.assertEqual(entry['status'], 'accepted')
        self.assertEqual(ship.documents(self._job(), fb)[0]['effective_path'], sa.safe_rel(saved))
        self.assertNotIn(saved, sa._files())
        self.assertEqual(ship.customization_problem(self._job(), fb), '')

    def test_repeated_feedback_from_different_cards_becomes_one_report(self):
        feedback_ids = ['first-feedback', 'second-feedback']

        def add_feedback(fb):
            for url, feedback_id in zip((self.url, self.other_url), feedback_ids):
                fb[url]['custom_docs'] = {'resume:general': {
                    'name': '通用履歷', 'feedbacks': [{'id': feedback_id, 'text': '成果要更明確'}],
                }}
        bd.set_fb(add_feedback, live=self.board, by='test')
        report_path = os.path.join(self.tmp, 'reports.json')
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump({'reports': [{
                'issue': '多張卡都需要更明確的成果描述',
                'recommendation': '檢查通用履歷的成果段落 skill', 'occurrences': 2,
            }]}, f, ensure_ascii=False)

        self.assertTrue(customize.process_feedback_reports(self.board, report_path, feedback_ids))
        fb = self._fb()
        reports = agent_report.open_items(fb)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['n'], 2)
        self.assertIn('檢查通用履歷', reports[0]['need'])
        for url, feedback_id in zip((self.url, self.other_url), feedback_ids):
            feedback = fb[url]['custom_docs']['resume:general']['feedbacks'][0]
            self.assertEqual(feedback['id'], feedback_id)
            self.assertTrue(feedback.get('processed_at'))

    def test_custom_skills_are_created_and_validated_inside_the_skill_folder(self):
        created, error = sa.create_skill('產品經理履歷', '只調整可驗證的產品成果。')
        self.assertFalse(error)
        self.assertTrue(os.path.isfile(sa.safe_rel(created['path'])))
        self.assertIn(created, sa.skill_files())
        self.assertNotIn(created['path'], sa._files())
        settings = copy.deepcopy(self.settings)
        settings['resume']['resumes'][0]['skill'] = created['path']
        self.assertEqual(sa._check(settings), [])
        settings['resume']['resumes'][0]['skill'] = 'custom/elsewhere.md'
        self.assertTrue(any('改履歷的規則找不到' in problem for problem in sa._check(settings)))


if __name__ == '__main__':
    unittest.main()
