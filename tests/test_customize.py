"""Per-card resume and attachment customization workflow."""
import copy
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import pypdf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
from _env import read_board, read_fb  # noqa: E402
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
        _env.use_home(self, board={'file': 'board.html'}, resume={
            'base': 'resume', 'ship_dir': 'ship', 'langs': ['zh'],
            'resumes': [{'id': 'general', 'name': '通用履歷', 'files': {'zh': 'resume/base.pdf'},
                         'enabled': True, 'when': '', 'skill': ''}],
            'attachments': [{'id': 'portfolio', 'name': '作品集', 'files': {'zh': 'resume/portfolio.pdf'},
                             'enabled': True, 'skill': 'custom/skills/portfolio.md'}],
        })
        self.board = os.path.join(self.home, 'board.html')
        os.makedirs(os.path.join(self.home, 'resume'), exist_ok=True)
        for name in ('base.pdf', 'portfolio.pdf'):
            _env.tiny_pdf(os.path.join(self.home, 'resume', name), pages=2)
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
        _env.make_board(self.board, fb, jobs=jobs)
        # 職缺頁由程式先抓好給 agent(#287);測試不連網、不開無頭 Chrome
        import page_fetch
        self.page = page_fetch.PageResult(self.url, 'ok', text='JD-TEXT: build secure systems', via='direct')
        fetch = mock.patch.object(page_fetch, 'fetch', side_effect=lambda _url: self.page)
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)

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
        return len(pypdf.PdfReader(path).pages)

    def _agent(self, captured, output_pages=None):
        output_pages = output_pages or {}

        def launch(prompt, output_log, home, **_kwargs):
            captured.append(prompt)
            marker = '本輪檔案與使用者指示(JSON):\n'
            payload = prompt.split(marker, 1)[1].split('\n\n其他卡片尚未處理', 1)[0]
            for item in json.loads(payload):
                count = output_pages.get(item['id'], 1)
                _env.tiny_pdf(item['output_pdf'], pages=count)
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

    def test_the_round_leaves_its_evidence_in_the_cards_folder(self):
        # #315:客製版這一輪的指示、動作紀錄、交件單,跟幫你填表同一種放法,放在那張卡的證據夾
        result, _prompts = self._run(['attachment:portfolio:zh'])
        self.assertTrue(result['ok'], result)
        [rnd] = _env.evidence_rounds(self.url, self.board)
        kinds = [e['kind'] for e in _env.evidence_events(rnd)]
        self.assertEqual([k for k in kinds if k in ('instruction', 'agent_log', 'handoff')],
                         ['instruction', 'agent_log', 'handoff'])
        self.assertEqual(_env.evidence_rounds(self.other_url, self.board), [])

    def test_one_agent_customizes_selected_files_and_accepted_files_enter_package(self):
        files = customize.list_files(self.url, board=self.board)
        by_id = {item['id']: item for item in files}
        self.assertTrue(by_id['attachment:portfolio:zh']['default_checked'])
        self.assertFalse(by_id['resume:general:zh']['default_checked'])
        self.assertEqual(by_id['resume:general:zh']['skill'], '')

        result, prompts = self._run(['resume:general:zh', 'attachment:portfolio:zh'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(prompts), 1)  # 一張卡的多份檔案共用一個 agent
        marker = '本輪檔案與使用者指示(JSON):\n'
        payload = json.loads(prompts[0].split(marker, 1)[1].split('\n\n其他卡片尚未處理', 1)[0])
        self.assertEqual({item['id'] for item in payload}, {'resume:general:zh', 'attachment:portfolio:zh'})
        self.assertIn('突出作品與可查證成果', next(x for x in payload if x['id'] == 'attachment:portfolio:zh')['skill_text'])
        self.assertTrue(next(x for x in payload if x['id'] == 'resume:general:zh')['skill_text'])  # 使用產品通用 skill

        fb = self._fb()
        self.assertEqual(fb[self.url]['custom_docs']['resume:general:zh']['status'], 'review')
        self.assertEqual(fb[self.url]['custom_docs']['attachment:portfolio:zh']['status'], 'review')
        self.assertTrue(ship.customization_problem(self._job(), fb))
        _folder, problems = ship.build_default(self._job(), fb)
        self.assertIn('等你看', problems[0])

        self.assertEqual(customize.accept(self.url, 'resume:general:zh', board=self.board), (True, ''))
        self.assertTrue(ship.customization_problem(self._job(), self._fb()))  # 另一份仍待審
        self.assertEqual(customize.accept(self.url, 'attachment:portfolio:zh', board=self.board), (True, ''))
        fb = self._fb()
        self.assertEqual(ship.customization_problem(self._job(), fb), '')
        resume, attachments, _own = ship.sources(self._job(), fb)
        self.assertIn('/custom/', resume)
        self.assertEqual(len(attachments), 1)
        _folder, problems = ship.build_default(self._job(), fb)
        self.assertEqual(problems, [])
        self.assertEqual(len(ship.info(self.url)['files']), 2)

    def test_diff_says_which_file_could_not_be_read(self):
        # 以前讀不出文字就當成空的:差異變成「整份都刪掉了」,他看不出其實是檔案讀不了
        result, _prompts = self._run(['resume:general:zh'])
        self.assertTrue(result['ok'], result)
        with mock.patch.object(sa, 'pdf_text', side_effect=ValueError('PDF 壞掉了')):
            with self.assertRaises(ValueError) as e:
                customize.diff_for(self.url, 'resume:general:zh', board=self.board)
        self.assertIn('抽不出文字', str(e.exception))
        self.assertIn('PDF 壞掉了', str(e.exception))

    def test_page_limit_failure_is_recorded_and_reported(self):
        result, _prompts = self._run(['resume:general:zh'], output_pages={'resume:general:zh': 3})
        self.assertFalse(result['ok'])
        self.assertIn('超過原檔', result['failures'][0])
        fb = self._fb()
        entry = fb[self.url]['custom_docs']['resume:general:zh']
        self.assertEqual(entry['status'], 'failed')
        self.assertIn('超過原檔', entry['error'])
        reports = agent_report.open_items(fb)
        self.assertEqual(len(reports), 1)
        self.assertIn('超過原檔', reports[0]['msg'])
        self.assertEqual(ship.customization_problem(self._job(), fb), '')

    def test_customize_agent_gets_the_fetched_jd_and_no_chrome(self):
        # #287:客製不給操作 Chrome 的能力(Codex、Claude 都一樣);JD 由程式用 page_fetch 抓好放進 prompt。
        # 設定裡第一個 agent 能開瀏覽器也一樣:以前客製要求瀏覽器、開著 agent 的 Chrome 做,卻不在代投那把鎖裡
        import agent_run
        seen = []

        def launch(prompt, outfile, _repo, agent, **kwargs):
            seen.append(kwargs)
            self._agent([])(prompt, outfile, _repo)
            open(outfile, 'w').close()
            return SimpleNamespace(pid=1)

        with mock.patch.object(sa, 'pdf_pages', side_effect=self._pages), \
                mock.patch.object(agent_run, 'launch', side_effect=launch), \
                mock.patch.object(agent_run, 'wait_done', return_value=[agent_run.AgentResult('completed', 0, 1)]):
            result = customize.run_customization(self.board, self.url, ['resume:general:zh'],
                                                 sp=os.path.join(self.tmp, 'runs'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].get('chrome'))
        self.assertIsNone(seen[0].get('browser'))
        self.fetch.assert_called_once_with(self.url)
        prompts = []
        self._run(['resume:general:zh'], prompts)
        self.assertIn('JD-TEXT: build secure systems', prompts[0])
        self.assertNotIn('Chrome', prompts[0])

    def test_unreadable_jd_page_reports_instead_of_opening_a_browser(self):
        # 抓不到(要登入、擋程式):不派 agent、不去開任何 Chrome,卡上和 📣 照實講
        import page_fetch
        self.page = page_fetch.PageResult(self.url, 'unknown', errors=('direct: HTTP 401',))
        captured = []
        with mock.patch.object(customize.agent_report, 'report') as report:
            result, _ = self._run(['resume:general:zh'], captured)
        self.assertFalse(result['ok'])
        self.assertEqual(captured, [])                       # 沒派 agent
        entry = self._fb()[self.url]['custom_docs']['resume:general:zh']
        self.assertEqual(entry['status'], 'failed')
        self.assertIn('讀不到', entry['error'])
        self.assertIn('登入', entry['error'])
        self.assertTrue(report.called)

    def test_agent_start_failure_does_not_leave_the_card_locked(self):
        def fail_to_launch(*_args, **_kwargs):
            raise RuntimeError('sandbox unavailable')

        with mock.patch.object(sa, 'pdf_pages', side_effect=self._pages):
            result = customize.run_customization(
                self.board, self.url, ['resume:general:zh'], sp=os.path.join(self.tmp, 'runs'),
                launcher=fail_to_launch, waiter=self._waiter,
            )
        self.assertFalse(result['ok'])
        entry = self._fb()[self.url]['custom_docs']['resume:general:zh']
        self.assertEqual(entry['status'], 'failed')
        self.assertIn('sandbox unavailable', entry['error'])
        self.assertEqual(ship.customization_problem(self._job(), self._fb()), '')

    def test_rejected_feedback_is_included_in_the_next_agent_prompt(self):
        self._run(['resume:general:zh'])
        self.assertEqual(customize.reject(self.url, 'resume:general:zh', '把技能年資放回摘要', board=self.board), (True, ''))
        self.assertIn('退回重寫中', ship.customization_problem(self._job(), self._fb()))
        result, prompts = self._run(['resume:general:zh'])
        self.assertTrue(result['ok'], result)
        self.assertIn('把技能年資放回摘要', prompts[0])
        self.assertEqual(self._fb()[self.url]['custom_docs']['resume:general:zh']['status'], 'review')

    def test_accept_and_reject_at_the_same_time_keep_one_of_them(self):
        # 兩台裝置同時對同一份客製版按收下、退回(#308):以前兩邊都在鎖外讀「在等你看」,後寫的整份蓋掉先寫的,
        # 收下的那一份不見了、兩邊都說成功。現在在看板鎖內讀:先寫的算數,後到的照現在的狀態回原因
        import threading
        self._run(['resume:general:zh'])
        real, got = bd.assemble, {}

        def hooked(*a, **k):
            if 'reject' not in got:
                got['reject'] = None
                t = threading.Thread(target=lambda: got.__setitem__(
                    'reject', customize.reject(self.url, 'resume:general:zh', '重寫', board=self.board)))
                t.start()
                t.join(0.5)
                got['thread'] = t
            return real(*a, **k)
        with mock.patch.object(bd, 'assemble', hooked):
            self.assertEqual(customize.accept(self.url, 'resume:general:zh', board=self.board), (True, ''))
        got['thread'].join(5)
        self.assertEqual(self._fb()[self.url]['custom_docs']['resume:general:zh']['status'], 'accepted')
        self.assertEqual(got['reject'], (False, '這份客製版目前不在等你看'))

    def test_failed_rerun_of_an_accepted_file_keeps_the_filled_page_and_approval(self):
        # 已收下的客製版再客製一次,agent 沒完成:檔案沒換(退回原本收下的那份),填好的頁和他的確認送出都不動。
        # 以前一律當成「換成客製版了」,取消確認、叫 agent 整張重填,重填的還是同一份檔
        self._run(['resume:general:zh'])
        self.assertEqual(customize.accept(self.url, 'resume:general:zh', board=self.board), (True, ''))
        accepted = self._fb()[self.url]['custom_docs']['resume:general:zh']['path']

        def filled(fb):
            fb[self.url].update(approve={'snap': {}}, form={'f': []},
                                apply={'stage': 'fill', 'ok': True, 'session': 's1', 'tab_id': '7'})
        bd.set_fb(filled, live=self.board, by='test')
        failed = lambda _processes, timeout: [SimpleNamespace(ok=False, message=lambda: '額度用完')]
        with mock.patch.object(sa, 'pdf_pages', side_effect=self._pages), \
                mock.patch.object(customize.agent_report, 'report'):
            result = customize.run_customization(
                self.board, self.url, ['resume:general:zh'], sp=os.path.join(self.tmp, 'runs'),
                launcher=self._agent([]), waiter=failed)
        self.assertFalse(result['ok'])
        state = self._fb()[self.url]
        entry = state['custom_docs']['resume:general:zh']
        self.assertEqual((entry['status'], entry['path']), ('accepted', accepted))
        self.assertFalse(state['apply'].get('stale'))
        self.assertIn('approve', state)

    def _upload_while_agent_runs(self, ok):
        """agent 在跑的時候,他等不及直接上傳自己的 PDF;之後 agent 才交件(或失敗)。"""
        agent = self._agent([])
        uploaded = []

        def launch(prompt, output_log, home, **kwargs):
            saved, error = customize.upload_custom(
                self.url, 'resume:general:zh', 'mine.pdf', b'%PDF-1.4\nmine', board=self.board)
            self.assertFalse(error)
            uploaded.append(saved)
            return agent(prompt, output_log, home, **kwargs)
        waiter = lambda _p, timeout: [SimpleNamespace(ok=ok, message=lambda: '' if ok else '額度用完')]
        with mock.patch.object(sa, 'pdf_pages', side_effect=self._pages), \
                mock.patch.object(customize.agent_report, 'report') as report:
            customize.run_customization(self.board, self.url, ['resume:general:zh'],
                                        sp=os.path.join(self.tmp, 'runs'), launcher=launch, waiter=waiter)
        return uploaded[0], report

    def test_upload_during_customization_is_not_replaced_when_the_agent_finishes(self):
        # 他自己上傳的就是他的決定:agent 之後交的那份不收、不把卡改回「等你看」,也不回報 agent 失敗
        for ok in (True, False):
            with self.subTest(agent_ok=ok):
                bd.set_fb(lambda fb: fb[self.url].pop('custom_docs', None), live=self.board, by='test')
                mine, report = self._upload_while_agent_runs(ok)
                entry = self._fb()[self.url]['custom_docs']['resume:general:zh']
                self.assertEqual((entry['status'], entry['path']), ('accepted', mine))
                self.assertNotIn('candidate_path', entry)
                self.assertFalse(entry.get('error'))
                report.assert_not_called()
                self.assertEqual(ship.customization_problem(self._job(), self._fb()), '')

    def test_old_resume_custom_record_can_be_cleared_after_switching_resume(self):
        # 客製版等你看的時候換了履歷:那筆紀錄不再寄出去,收下/退回都不行,但要能清掉(卡上給的唯一一顆)。
        # 清掉只拿掉紀錄,不動現在要寄的檔,也不取消他已經確認送出的核准
        self.settings['resume']['resumes'].append({
            'id': 'second', 'name': '第二版', 'files': {'zh': 'resume/base.pdf'}, 'enabled': True, 'when': '', 'skill': ''})
        self._write_settings()
        cf.reload(self.home)
        self._run(['resume:general:zh'])

        def switch(fb):
            fb[self.url].update(resume_id='second', approve={'snap': {}},
                                apply={'stage': 'fill', 'ok': True, 'session': 's1'})
        bd.set_fb(switch, live=self.board, by='test')
        self.assertEqual(ship.customization_problem(self._job(), self._fb()), '')
        self.assertEqual(customize.accept(self.url, 'resume:general:zh', board=self.board), (False, '這張卡沒有這份檔案'))
        self.assertEqual(customize.clear(self.url, 'resume:general:zh', board=self.board), (True, ''))
        state = self._fb()[self.url]
        self.assertNotIn('custom_docs', state)
        self.assertIn('approve', state)
        self.assertFalse(state['apply'].get('stale'))
        self.assertEqual(customize.clear(self.url, 'resume:nothing', board=self.board), (False, '這張卡沒有這份檔案'))

    def test_manual_pdf_upload_is_already_accepted(self):
        saved, error = customize.upload_custom(
            self.url, 'resume:general:zh', 'manual.pdf', b'%PDF-1.4\npages=2\nmanual', board=self.board)
        self.assertFalse(error)
        self.assertTrue(os.path.isfile(sa.safe_rel(saved)))
        fb = self._fb()
        entry = fb[self.url]['custom_docs']['resume:general:zh']
        self.assertEqual(entry['status'], 'accepted')
        self.assertEqual(ship.documents(self._job(), fb)[0]['effective_path'], sa.safe_rel(saved))
        self.assertNotIn(saved, sa._files())
        self.assertEqual(ship.customization_problem(self._job(), fb), '')

    def test_files_cannot_change_while_sending(self):
        # #338 正在送出時換檔:卡上換了、狀態沒標「上傳的是舊檔」,之後送出去的是頁上的舊檔。
        # 狀態表在正在送出不收「換檔」:上傳、改回原始檔都不准,卡一點都不動
        def sending(fb):
            fb[self.url].update(app='ship', ds='sending', custom_file='custom/old.pdf',
                                approve={'at': '2026-01-01T00:00:00', 'snap': {}},
                                apply={'stage': 'submit', 'session': 's1', 'tab_id': '5'})
        bd.set_fb(sending, live=self.board, by='test')
        before = self._fb()[self.url]
        saved, error = customize.upload_custom(
            self.url, 'resume:general:zh', 'mine.pdf', b'%PDF-1.4\nmine', board=self.board)
        self.assertIsNone(saved)
        self.assertIn('正在送出', error)
        ok, why = customize.clear(self.url, 'resume:legacy', board=self.board)
        self.assertFalse(ok)
        self.assertIn('正在送出', why)
        self.assertEqual(self._fb()[self.url], before)

    def test_accepted_custom_is_kept_per_language(self):
        # 中文那份收下了客製版,換成英文:寄英文原始檔、卡上那份不算英文的客製版;換回中文,中文的客製版還在
        self.settings['resume']['langs'] = ['zh', 'en']
        self.settings['resume']['resumes'][0]['files']['en'] = 'resume/base-en.pdf'
        _env.tiny_pdf(os.path.join(self.home, 'resume', 'base-en.pdf'), pages=1)
        self._write_settings()
        cf.reload(self.home)
        saved, error = customize.upload_custom(
            self.url, 'resume:general:zh', 'manual.pdf', b'%PDF-1.4\nmanual', board=self.board)
        self.assertFalse(error)
        bd.set_fb(lambda fb: fb[self.url].update(lang='en'), live=self.board, by='test')
        resume = ship.documents(self._job(), self._fb())[0]
        self.assertEqual(resume['id'], 'resume:general:en')
        self.assertEqual(resume['entry'], {})
        self.assertEqual(resume['effective_path'], cf.path('resume/base-en.pdf'))
        bd.set_fb(lambda fb: fb[self.url].update(lang='zh'), live=self.board, by='test')
        self.assertEqual(ship.documents(self._job(), self._fb())[0]['effective_path'], sa.safe_rel(saved))

    def test_repeated_feedback_from_different_cards_becomes_one_report(self):
        feedback_ids = ['first-feedback', 'second-feedback']

        def add_feedback(fb):
            for url, feedback_id in zip((self.url, self.other_url), feedback_ids):
                fb[url]['custom_docs'] = {'resume:general:zh': {
                    'name': '通用履歷', 'feedbacks': [{'id': feedback_id, 'text': '成果要更明確'}],
                }}
            # 只有一張卡提過的問題:這一輪不回報,也不能標成處理過,之後別張卡也提到才湊得到兩次
            fb[self.url]['custom_docs']['attachment:portfolio:zh'] = {
                'name': '作品集', 'feedbacks': [{'id': 'only-once', 'text': '作品集太長'}]}
        bd.set_fb(add_feedback, live=self.board, by='test')
        report_path = os.path.join(self.tmp, 'reports.json')
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump({'reports': [{
                'issue': '多張卡都需要更明確的成果描述',
                'recommendation': '檢查通用履歷的成果段落 skill', 'occurrences': 2,
                'feedback_ids': feedback_ids,
            }]}, f, ensure_ascii=False)

        self.assertTrue(customize.process_feedback_reports(self.board, report_path, feedback_ids + ['only-once']))
        fb = self._fb()
        reports = agent_report.open_items(fb)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['n'], 2)
        self.assertIn('檢查通用履歷', reports[0]['need'])
        for url, feedback_id in zip((self.url, self.other_url), feedback_ids):
            feedback = fb[url]['custom_docs']['resume:general:zh']['feedbacks'][0]
            self.assertEqual(feedback['id'], feedback_id)
            self.assertTrue(feedback.get('processed_at'))
        once = fb[self.url]['custom_docs']['attachment:portfolio:zh']['feedbacks'][0]
        self.assertFalse(once.get('processed_at'))
        self.assertEqual([x['id'] for x in customize.feedback_records(fb)], ['only-once'])

    def test_a_report_that_cites_feedback_it_was_not_given_is_not_taken(self):
        """安檢門(#317):整理裡列了這一輪沒給它的回饋:這一則不收、照實回報,回饋也不標成處理過。"""
        def add_feedback(fb):
            for url, feedback_id in zip((self.url, self.other_url), ('first', 'second')):
                fb[url]['custom_docs'] = {'resume:general:zh': {
                    'name': '通用履歷', 'feedbacks': [{'id': feedback_id, 'text': '成果要更明確'}]}}
        bd.set_fb(add_feedback, live=self.board, by='test')
        report_path = os.path.join(self.tmp, 'reports.json')
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump({'reports': [{'issue': '成果不明確', 'recommendation': '改 skill',
                                    'feedback_ids': ['first', 'made-up']}]}, f, ensure_ascii=False)
        self.assertTrue(customize.process_feedback_reports(self.board, report_path, ['first', 'second']))
        fb = self._fb()
        said = ' '.join(x['msg'] for x in agent_report.open_items(fb))
        self.assertIn('made-up', said)
        self.assertIn('這一輪沒給它', said)
        self.assertEqual(sorted(x['id'] for x in customize.feedback_records(fb)), ['first', 'second'])

    def test_a_grouped_report_is_marked_as_the_agents_judgment_with_the_feedback_text(self):
        def add_feedback(fb):
            for url, feedback_id in zip((self.url, self.other_url), ('first', 'second')):
                fb[url]['custom_docs'] = {'resume:general:zh': {
                    'name': '通用履歷', 'feedbacks': [{'id': feedback_id, 'text': '成果要更明確'}]}}
        bd.set_fb(add_feedback, live=self.board, by='test')
        report_path = os.path.join(self.tmp, 'reports.json')
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump({'reports': [{'issue': '成果不明確', 'recommendation': '改 skill',
                                    'feedback_ids': ['first', 'second']}]}, f, ensure_ascii=False)
        self.assertTrue(customize.process_feedback_reports(self.board, report_path, ['first', 'second']))
        said = agent_report.open_items(self._fb())[0]['msg']
        self.assertIn('agent 判斷', said)
        self.assertIn('成果要更明確', said)

    def test_prompt_asks_which_feedback_each_report_covers(self):
        prompt = customize.build_prompt('test://jobs/12', '工程師', [], [], '/tmp/r.json')
        self.assertIn('feedback_ids', prompt)

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
