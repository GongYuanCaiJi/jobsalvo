"""共用門路的公開行為;ego 指令是外部邊界,不啟動真瀏覽器。"""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock
import json
import subprocess
import ast
import re
import base64
import os
import _env  # noqa: F401
import chrome_door as cd

PAGE = {'url': 'https://jobs.test/apply', 'title': 'Apply',
        'fields': [{'label': 'Email', 'type': 'email', 'value': 'a@jobs.test'}],
        'shownFiles': ['resume.pdf'], 'lines': ['Apply', 'resume.pdf']}
WORKSPACE = {'id': 42, 'name': 'jobsalvo-test-card', 'page': 'p1'}

RESET_CALL = re.compile(r'(?:clearCookies|clearCache|clearStorage|clearBrowserCookies|clearBrowserCache|'
                        r'clearDataForOrigin|deleteAllCookies|browsingData[.]remove|'
                        r'(?:localStorage|sessionStorage)[.]clear)\b')


def response(value):
    return Mock(returncode=0, stderr='', stdout='@@jobsalvo-ego@@' + json.dumps({'ok': True, 'value': value}))


def failed_response(message):
    return Mock(returncode=0, stderr='', stdout='@@jobsalvo-ego@@' + json.dumps({'ok': False, 'error': message}))


class SharedBrowser(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        state = Path(self.tmp) / 'Local State'
        state.write_text(json.dumps({'ego': {'onboarding_imported_browser_data': 1}}))
        self.enterContext(patch.object(cd, 'EGO_STATE', str(state), create=True))
        self.enterContext(patch.object(cd, 'ego_bin', return_value='/fake/ego-browser', create=True))
        skill = Path(self.tmp) / 'SKILL.md'          # 不靠這台電腦有沒有裝 ego-browser skill(CI 上沒有)
        skill.write_text('ego-browser')
        self.enterContext(patch.object(cd, 'EGO_SKILL', str(skill)))

    def test_local_pdf_is_written_from_egos_bytes_and_its_own_workspace_is_released(self):
        source, output = Path(self.tmp, 'resume.html'), Path(self.tmp, 'resume.pdf')
        source.write_text('<p>PDF fixture</p>')
        content = b'%PDF-1.7\nsynthetic print result'
        with patch.object(cd.EgoDoor, 'open_for_agent', return_value={'tab_id': '42:p1'}), \
                patch.object(cd.EgoDoor, '_task', return_value=''), \
                patch.object(cd, '_ego', return_value={'data': base64.b64encode(content).decode()}), \
                patch.object(cd.EgoDoor, 'release') as release:
            cd.EgoDoor().print_pdf(str(source), str(output))
        self.assertEqual(output.read_bytes(), content)
        release.assert_called_once_with('42:p1')

    def test_failed_pdf_keeps_the_previous_file_and_releases_its_own_workspace(self):
        source, output = Path(self.tmp, 'resume.html'), Path(self.tmp, 'resume.pdf')
        source.write_text('<p>PDF fixture</p>')
        output.write_bytes(b'%PDF-previous')
        with patch.object(cd.EgoDoor, 'open_for_agent', return_value={'tab_id': '42:p1'}), \
                patch.object(cd.EgoDoor, '_task', return_value=''), \
                patch.object(cd, '_ego', return_value={'data': base64.b64encode(b'not a PDF').decode()}), \
                patch.object(cd.EgoDoor, 'release') as release:
            with self.assertRaisesRegex(cd.NotNow, 'PDF'):
                cd.EgoDoor().print_pdf(str(source), str(output))
        self.assertEqual(output.read_bytes(), b'%PDF-previous')
        release.assert_called_once_with('42:p1')

    def test_both_agents_receive_the_same_browser_rules(self):
        codex, claude = cd.of('codex'), cd.of('claude-code')
        self.assertEqual(codex.apply_rule(), claude.apply_rule())
        self.assertIn('ego-browser nodejs', codex.apply_rule())
        self.assertEqual(codex.fetch_rule(), claude.fetch_rule())

    def test_missing_browser_names_the_required_installation(self):
        with patch.object(cd, 'ego_bin', return_value=None, create=True):
            ok, reason, need = cd.of('claude-code').ready()
        self.assertFalse(ok)
        self.assertIn('沒有安裝', reason)
        self.assertIn('ego', need)

    def test_incomplete_import_stops_before_any_browser_command(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(cd, 'EGO_STATE', str(Path(tmp) / 'Local State'), create=True), \
             patch.object(cd, 'ego_bin', return_value='/fake/ego-browser'), \
             patch('subprocess.run', side_effect=AssertionError('匯入前不能操作瀏覽器')):
            ok, reason, _need = cd.of('codex').ready()
        self.assertFalse(ok)
        self.assertIn('匯入', reason)

    def test_disconnected_or_timed_out_browser_is_not_ready(self):
        for response in (Mock(returncode=1, stdout='', stderr='service unavailable'),
                         subprocess.TimeoutExpired('ego-browser', 10)):
            with self.subTest(response=type(response).__name__), \
                 patch('subprocess.run', **({'side_effect': response} if isinstance(response, Exception)
                                           else {'return_value': response})):
                ok, reason, _need = cd.of('codex').ready()
            self.assertFalse(ok)
            self.assertIn('ego', reason)

    def test_runtime_console_results_on_stderr_are_received_without_a_model(self):
        reply = response([])
        reply.stderr, reply.stdout = reply.stdout, ''
        with patch('subprocess.run', return_value=reply):
            self.assertTrue(cd.of('claude-code').ready()[0])

    def test_program_browser_calls_leave_their_result_and_duration_in_the_active_evidence(self):
        import evidence
        record = Mock()
        with patch.object(evidence, 'active', return_value=record), \
             patch('subprocess.run', return_value=response([])):
            self.assertTrue(cd.of('codex').ready()[0])
        self.assertEqual(record.text.call_args.args[0], 'browser_call')
        self.assertEqual(record.note.call_args.args[0], 'browser_result')
        self.assertTrue(record.note.call_args.kwargs['ok'])
        self.assertGreaterEqual(record.note.call_args.kwargs['seconds'], 0)

    def test_opening_a_card_returns_its_workspace_and_the_program_read_page(self):
        with patch('subprocess.run', return_value=response({'workspace': WORKSPACE, 'tab_id': '42:p1', 'page': PAGE})):
            opened = cd.of('claude-code').open_for_agent(PAGE['url'])
        self.assertEqual(opened['workspace']['id'], 42)
        self.assertEqual(opened['workspace']['page'], 'p1')
        self.assertEqual(opened['page']['fields'][0]['value'], 'a@jobs.test')

    def test_navigation_failure_keeps_the_created_workspace_for_the_card(self):
        saved = []
        door = cd.of('codex')
        with patch('subprocess.run', side_effect=[
            response({'workspace': WORKSPACE, 'tab_id': '42:p1'}),
            subprocess.TimeoutExpired('ego-browser', 30),
        ]):
            with self.assertRaises(cd.NotNow):
                door.open_for_agent(PAGE['url'], on_open=lambda opened: saved.append(opened['workspace']))
        self.assertEqual(saved, [WORKSPACE])
        self.assertEqual(door.workspace, WORKSPACE)

    def test_cleanup_does_not_close_a_workspace_restored_to_an_active_card(self):
        import board_doc as bd
        fb = {'https://jobs.test/1': {'ds': 'parked', 'apply': {'workspace': WORKSPACE}},
              '__browser_cleanup__': [{'workspace': WORKSPACE, 'reason': 'leave'}]}
        with patch.object(bd, 'load', return_value={'fb': json.dumps(fb)}), \
             patch.object(bd, 'set_fb', side_effect=lambda mutate, **kw: mutate(fb)), \
             patch.object(cd.EgoDoor, 'release') as finish:
            cd.close_if_idle('/copy/board.html')
        finish.assert_not_called()
        self.assertNotIn('__browser_cleanup__', fb)

    def test_both_agents_read_the_live_page_without_conversation_logs(self):
        for runtime in ('codex', 'claude-code'):
            with self.subTest(runtime=runtime), patch('subprocess.run', side_effect=[
                response({'workspace': WORKSPACE, 'tab_id': '42:p1'}),
                response({'workspace': WORKSPACE, 'tab_id': '42:p1', 'page': PAGE}), response(PAGE),
            ]):
                door = cd.of(runtime)
                opened = door.open_for_agent(PAGE['url'])
                self.assertEqual(door.read_page(opened['tab_id']), PAGE)

    def test_program_screenshot_is_a_file_in_the_requested_directory(self):
        path = Path(self.tmp) / 'shots' / 'fill.png'
        def screenshot_command(*args, **kwargs):
            path.parent.mkdir()
            path.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            return response(str(path))
        with patch('subprocess.run', return_value=response({'workspace': WORKSPACE, 'tab_id': '42:p1', 'page': PAGE})):
            door = cd.of('claude-code')
            opened = door.open_for_agent(PAGE['url'])
        with patch('subprocess.run', side_effect=screenshot_command):
            saved = door.shot(opened['tab_id'], str(path))
        self.assertEqual(Path(saved).read_bytes()[:8], b'\x89PNG\r\n\x1a\n')

    def test_cold_page_screenshot_retries_until_success_and_records_each_attempt(self):
        import evidence
        door = cd.of('codex')
        door.workspace = dict(WORKSPACE)
        path = Path(self.tmp) / 'warm.png'
        record, calls = Mock(), []
        def capture(*args, **kwargs):
            calls.append(True)
            if len(calls) < 4:
                return failed_response('CDP request timed out: Page.captureScreenshot')
            path.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            return response(str(path))
        with patch('subprocess.run', side_effect=capture), patch.object(evidence, 'active', return_value=record):
            self.assertEqual(door.shot('42:p1', str(path)), str(path))
        attempts = [call.kwargs for call in record.note.call_args_list if call.args[0] == 'shot_attempt']
        self.assertEqual([a['attempt'] for a in attempts], [1, 2, 3, 4])
        self.assertEqual([a['ok'] for a in attempts], [False, False, False, True])
        self.assertTrue(all(a['workspace'] == WORKSPACE and a['seconds'] >= 0 for a in attempts))
        self.assertEqual(attempts[-1]['retry_count'], 3)
        self.assertGreaterEqual(attempts[-1]['elapsed_seconds'], attempts[-1]['seconds'])

    def test_cold_screenshot_retry_limit_preserves_failure_without_an_old_image(self):
        door = cd.of('claude-code')
        door.workspace = dict(WORKSPACE)
        path = Path(self.tmp) / 'failed.png'
        path.write_bytes(b'\x89PNG\r\n\x1a\nold image')
        with patch('subprocess.run', return_value=failed_response('CDP request timed out: Page.captureScreenshot')) as command:
            with self.assertRaisesRegex(cd.NotNow, '6 次'):
                door.shot('42:p1', str(path))
        self.assertEqual(command.call_count, 6)
        self.assertFalse(path.exists())

    def test_screenshot_errors_other_than_the_cold_capture_timeout_are_not_retried(self):
        door = cd.of('codex')
        door.workspace = dict(WORKSPACE)
        with patch('subprocess.run', return_value=failed_response('connection unavailable')) as command:
            with self.assertRaisesRegex(cd.NotNow, 'connection unavailable'):
                door.shot('42:p1', str(Path(self.tmp) / 'offline.png'))
        self.assertEqual(command.call_count, 1)

    def test_failed_screenshot_does_not_leave_a_file_that_could_be_mistaken_for_evidence(self):
        door = cd.of('codex')
        door.workspace = dict(WORKSPACE)
        path = Path(self.tmp) / 'broken.png'
        def broken_image(*args, **kwargs):
            path.write_text('capture failed')
            return response(str(path))
        with patch('subprocess.run', side_effect=broken_image):
            with self.assertRaisesRegex(cd.NotNow, '有效頁面截圖'):
                door.shot('42:p1', str(path))
        self.assertFalse(path.exists())

    def test_profile_reader_keeps_fields_links_and_readiness_in_the_cards_workspace(self):
        url = 'https://profile.test/preview?id=3'
        profile = {'url': url, 'title': '履歷', 'text': '已存的履歷內容 ' * 150,
                   'fields': [{'label': '求職條件', 'section': '工作', 'role': 'textbox', 'value': '全職'}],
                   'links': ['https://profile.test/file/cv.pdf'], 'anchors': [], 'readyState': 'complete'}
        with patch('subprocess.run', side_effect=[
            response({'workspace': WORKSPACE, 'tab_id': '42:p1'}),
            response({'workspace': WORKSPACE, 'tab_id': '42:p1', 'page': PAGE}),
            response({'pages': {url: profile}, 'reader': 'p2'}),
        ]):
            door = cd.of('claude-code')
            door.open_for_agent(PAGE['url'])
            read = door.profile_reader()(url)
        self.assertEqual(read['fields'][0]['value'], '全職')
        self.assertEqual(read['links'], ['https://profile.test/file/cv.pdf'])
        self.assertTrue(read['_ready'])

    def test_a_short_complete_profile_is_ready_for_attachment_verification(self):
        url = 'http://127.0.0.1/profile?resumeId=fixed'
        profile = {'url': url, 'title': '履歷', 'text': '履歷與求職條件\n' * 40,
                   'fields': [], 'links': [], 'readyState': 'complete'}
        door = cd.of('codex')
        door.workspace = dict(WORKSPACE)
        with patch('subprocess.run', return_value=response({'pages': {url: profile}, 'reader': 'p2'})):
            self.assertTrue(door.read_profile(url)['_ready'])

    def test_a_card_workspace_survives_switching_the_enabled_agent_family(self):
        import config as cf
        with patch.dict(cf.C, {'agent': {'agents': [{'id': 'cc', 'runtime': 'claude-code', 'browser': True}]}}), \
             patch('subprocess.run', return_value=response(PAGE)):
            door = cd.for_card({'runtime': 'codex', 'agent_id': 'old', 'session': 'old-conversation',
                                'workspace': WORKSPACE, 'tab_id': '42:p1'})
            self.assertEqual(door.read_page('42:p1'), PAGE)
        self.assertEqual(door.agent_id, 'cc')

    def test_downloaded_attachment_hash_is_computed_from_the_saved_remote_bytes(self):
        url = 'https://profile.test/preview?id=3'
        path = Path(self.tmp) / 'attachment.bin'
        profile = {'url': url, 'text': '履歷內容' * 300, 'fields': [], 'links': [], 'readyState': 'complete'}
        def downloaded(*args, **kwargs):
            path.write_bytes(b'abc')
            return response({'files': [{'name': 'cv.pdf', 'path': str(path), 'sha256': 'untrusted'}], 'problems': []})
        replies = iter([
            response({'workspace': WORKSPACE, 'tab_id': '42:p1'}),
            response({'workspace': WORKSPACE, 'tab_id': '42:p1', 'page': PAGE}),
            response({'pages': {url: profile}, 'reader': 'p2'}), downloaded,
        ])
        def command(*args, **kwargs):
            reply = next(replies)
            return reply(*args, **kwargs) if callable(reply) and not isinstance(reply, Mock) else reply
        with patch('subprocess.run', side_effect=command):
            door = cd.of('claude-code')
            door.open_for_agent(PAGE['url'])
            result = door.download_attachments(url, self.tmp)
        self.assertEqual(result['files'][0]['sha256'],
                         'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
        self.assertEqual(result['files'][0]['size'], 3)
        self.assertEqual(Path(result['files'][0]['path']).read_bytes(), b'abc')

    def test_completed_card_finishes_only_its_bound_workspace(self):
        with patch('subprocess.run', side_effect=[
            response({'workspace': WORKSPACE, 'tab_id': '42:p1'}),
            response({'workspace': WORKSPACE, 'tab_id': '42:p1', 'page': PAGE}),
            response({'closed': ['p1', 'p2'], 'retained': ['p3']}),
        ]):
            door = cd.of('codex')
            opened = door.open_for_agent(PAGE['url'])
            receipt = door.release(opened['tab_id'])
        self.assertEqual(receipt['retained'], ['p3'])

    def test_disconnected_browser_does_not_erase_a_parked_card(self):
        fb = {'https://jobs.test/1': {'ds': 'parked', 'apply': {'workspace': WORKSPACE, 'tab_id': '42:p1'}}}
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('ego-browser', 15)):
            self.assertEqual(cd.gone_pages(fb), [])
        self.assertEqual(fb['https://jobs.test/1']['ds'], 'parked')

    def test_missing_page_is_detected_by_workspace_identity_not_a_chrome_process(self):
        fb = {'https://jobs.test/1': {'ds': 'parked', 'apply': {'workspace': WORKSPACE, 'tab_id': '42:p1'}}}
        with patch('subprocess.run', return_value=response({'https://jobs.test/1': 'missing'})):
            self.assertEqual(cd.gone_pages(fb), ['https://jobs.test/1'])

    def test_removal_preserves_a_cleanup_binding_after_the_card_loses_its_page(self):
        import delivery_state as ds
        url = 'https://jobs.test/1'
        fb = {url: {'ds': 'parked', 'apply': {'workspace': WORKSPACE, 'tab_id': '42:p1'}}}
        ds.fire(fb, url, 'leave')
        self.assertEqual(fb['__browser_cleanup__'][0]['workspace'], WORKSPACE)

    def test_cleanup_takes_back_a_workspace_he_took_over(self):
        # 他接手後卡被刪掉:收尾要先接回再關,不然工作區永遠留著(審查 #376)
        door = cd.of('codex')
        door.workspace = dict(WORKSPACE)
        with patch('subprocess.run', return_value=response({'closedSpace': True})) as run:
            door.release('42:p1')
        script = run.call_args.kwargs.get('input') or ''
        self.assertIn('takeOverTaskSpace', script)
        self.assertNotIn('正由使用者接手', script)

    def test_handoff_says_so_when_ego_cannot_be_brought_up(self):
        door = cd.of('codex')
        door.workspace = dict(WORKSPACE)
        with patch('subprocess.run', side_effect=[response({'ownership': 'user'}),
                                                  subprocess.CalledProcessError(1, ['open'])]):
            with self.assertRaisesRegex(cd.NotNow, '叫不出 ego lite'):
                door.hand_off()

    def test_handoff_and_explicit_resume_keep_the_same_workspace(self):
        door = cd.of('codex')
        door.workspace = dict(WORKSPACE)
        with patch('subprocess.run', side_effect=[response({'ownership': 'user'}), Mock(returncode=0), response(WORKSPACE)]):
            door.hand_off()             # 中間那次是把 ego 叫到前面(他按了 👀 才做)
            door.resume()
        self.assertEqual(door.workspace, WORKSPACE)


class BrowserPolicy(unittest.TestCase):
    def test_a_space_already_gone_does_not_prevent_finishing_the_other_test_spaces(self):
        run_id = '17d4db78-073c-44b8-bc66-95481cfac842'
        user = {'id': 1, 'name': 'user-space', 'ownership': 'user'}
        prefix = f'jobsalvo-test-{run_id}-'
        gone = {'id': 42, 'name': prefix + 'gone', 'ownership': 'agent'}
        ours = {'id': 43, 'name': prefix + 'preview', 'ownership': 'agent'}
        with patch.object(cd.uuid, 'uuid4', return_value=run_id), \
             patch.object(cd.EgoDoor, 'workspaces', side_effect=[[user], [user, gone, ours], [user]]), \
             patch.object(cd.EgoDoor, 'release', side_effect=[cd.Unreachable(cd.GONE), None]) as release:
            with cd.test_workspaces() as counts:
                pass
        self.assertEqual(release.call_count, 2)
        self.assertTrue(counts['returned_to_baseline'], counts)

    def test_an_explicitly_pending_real_handoff_is_retained_but_other_test_spaces_finish(self):
        run_id = '17d4db78-073c-44b8-bc66-95481cfac842'
        prefix = f'jobsalvo-test-{run_id}-'
        pending = {'id': 42, 'name': prefix + 'real-login', 'ownership': 'user'}
        finished = {'id': 43, 'name': prefix + 'preview', 'ownership': 'agent'}
        with patch.object(cd.uuid, 'uuid4', return_value=run_id), \
             patch.object(cd.EgoDoor, 'workspaces', side_effect=[[], [pending, finished], [pending]]), \
             patch.object(cd.EgoDoor, 'release') as release:
            with cd.test_workspaces(keep=[{'id': 42, 'name': pending['name']}]) as counts:
                pass
        release.assert_called_once()
        self.assertEqual(counts['retained'], [pending])
        self.assertEqual(counts['remaining'], [])
        self.assertEqual(counts['after_count'], 1)
        self.assertFalse(counts['returned_to_baseline'])

    def test_acceptance_cleanup_finishes_only_its_own_spaces_on_every_exit(self):
        run_id = '17d4db78-073c-44b8-bc66-95481cfac842'
        user = {'id': 1, 'name': 'user-space', 'ownership': 'user'}
        parked = {'id': 2, 'name': 'jobsalvo-real-parked', 'ownership': 'agent'}
        ours = {'id': 3, 'name': f'jobsalvo-test-{run_id}-card', 'ownership': 'agent'}
        released = []
        def release(door, *_args):
            released.append(dict(door.workspace))
        for outcome in ('success', 'failure', 'exception'):
            released.clear()
            with self.subTest(outcome=outcome), \
                    patch.object(cd.uuid, 'uuid4', return_value=run_id), \
                    patch.object(cd.EgoDoor, 'workspaces', side_effect=[[user, parked], [user, parked, ours], [user, parked]], create=True), \
                    patch.object(cd.EgoDoor, 'release', autospec=True, side_effect=release):
                try:
                    with cd.test_workspaces() as counts:
                        if outcome == 'exception':
                            raise ValueError('驗收例外')
                        verdict = outcome == 'success'
                except ValueError as error:
                    self.assertEqual(str(error), '驗收例外')
                else:
                    self.assertEqual(verdict, outcome == 'success')
                self.assertEqual([space['id'] for space in released], [3])
                self.assertEqual(counts['before_count'], 2)
                self.assertEqual(counts['after_count'], 2)
                self.assertTrue(counts['returned_to_baseline'])
                self.assertEqual(counts['errors'], [])

    def test_reset_guard_detects_python_and_embedded_javascript_operations(self):
        for operation in ('page.clearCookies()', 'Network.clearBrowserCache',
                          'Storage.clearDataForOrigin', 'localStorage.clear()', 'browsingData.remove()'):
            self.assertIsNotNone(RESET_CALL.search(operation))

    def test_active_browser_code_and_prompts_cannot_clear_a_profile(self):
        root = Path(__file__).resolve().parents[1]
        bad = []
        for path in (root / 'tools').rglob('*.py'):
            source = path.read_text()
            for node in ast.walk(ast.parse(source)):
                text = node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else \
                    ast.unparse(node.func) if isinstance(node, ast.Call) else ''
                if RESET_CALL.search(text):
                    bad.append(f'{path.name}:{node.lineno}')
        if RESET_CALL.search((root / 'board' / 'board.js').read_text()):
            bad.append('board.js')
        self.assertEqual(bad, [])


@unittest.skipUnless(os.environ.get('JOBSALVO_EGO_INTEGRATION') == '1', '真 ego 接手需明確啟用')
class NativeLoginHandoff(unittest.TestCase):
    def test_sibling_question_headings_bind_custom_menu_selection_without_borrowing_hidden_options(self):
        import apply_tab
        from urllib.parse import quote
        # 104 現場 DOM:兩題標題與控制項是同一個容器的兄弟節點。
        html = ('<div role=dialog><div class=apply-popup__body>'
                '<div class="h3 mb-2">選擇履歷</div><div class=d-flex>'
                '<div class=select-resume><div tabindex=0>'
                '<div class="form-control form-control--category-menu">'
                '<span class=text-region>Known fixed profile</span></div>'
                '<ul style="display:none"><li>Known fixed profile</li></ul></div></div>'
                '<a href="/profile/preview?vno=fixture">預覽履歷</a></div>'
                '<div class="h3 mt-6 mb-2">自我推薦信</div><div class=apply-msg>'
                '<div class="form-control form-control--category-menu">'
                '<span class=text-region>Saved cover</span></div></div>'
                '<div><textarea>Known recommendation</textarea><span>20/2000</span></div>'
                '<label><input type=checkbox style="display:none">儲存此次修改</label></div></div>')
        fb = {'__ans__': [{'k': 'recommend', 'q': '自我推薦信', 'v': 'Known recommendation'}],
              PAGE['url']: {'form': {'f': [
                  {'q': '選擇履歷', 'src': 'rz', 'v': 'Known fixed profile'},
                  {'q': '自我推薦信', 'src': 'bank', 'k': 'recommend'}]}}}
        with patch.object(cd, 'ego_bin', _env.REAL_EGO_BIN), cd.test_workspaces() as counts:
            door = cd.EgoDoor()
            opened = door.open_for_agent('data:text/html;charset=utf-8,' + quote(html))
            self.assertEqual(apply_tab.page_problems(opened['page'], fb, PAGE['url']), [])
            script = (f'const task=await taskSpace({opened["workspace"]["id"]});'
                      'await task.page("p1").evaluate(()=>{'
                      'document.querySelector(".select-resume .text-region").innerText="Wrong profile";'
                      'document.querySelector("textarea").value="Known fixed profile";});')
            subprocess.run([cd.ego_bin(), 'nodejs', '-e', script], check=True, capture_output=True, timeout=60)
            page = door.read_page(opened['tab_id'])
            problems = apply_tab.page_problems(page, fb, PAGE['url'])
            self.assertTrue(any('選擇履歷' in problem for problem in problems), problems)
            self.assertTrue(any('自我推薦信' in problem for problem in problems), problems)
        self.assertTrue(counts['returned_to_baseline'], counts)

    def test_custom_dropdown_readback_binds_the_selection_and_saved_answer_to_their_own_questions(self):
        import apply_tab
        from urllib.parse import quote
        html = ('<div role=dialog><div><div class=h3>選擇履歷</div>'
                '<div class=multiselect><span class=multiselect__single>Known fixed profile</span>'
                '<input type=text hidden></div></div>'
                '<div><div class=h3>自我推薦信</div><div class=multiselect>'
                '<span class=multiselect__single>Saved cover</span></div>'
                '<div><textarea>Known recommendation</textarea></div></div></div>')
        fb = {'__ans__': [{'k': 'recommend', 'q': '自我推薦信', 'v': 'Known recommendation'}],
              PAGE['url']: {'form': {'f': [
                  {'q': '選擇履歷', 'src': 'rz', 'v': 'Known fixed profile'},
                  {'q': '自我推薦信', 'src': 'bank', 'k': 'recommend'}]}}}
        with patch.object(cd, 'ego_bin', _env.REAL_EGO_BIN), cd.test_workspaces() as counts:
            door = cd.EgoDoor()
            opened = door.open_for_agent('data:text/html;charset=utf-8,' + quote(html))
            self.assertEqual(apply_tab.page_problems(opened['page'], fb, PAGE['url']), [])
            script = (f'const task=await taskSpace({opened["workspace"]["id"]});'
                      'await task.page("p1").evaluate(()=>{'
                      'document.querySelector(".multiselect__single").innerText="Wrong profile";'
                      'document.querySelector("textarea").value="Known fixed profile";});')
            subprocess.run([cd.ego_bin(), 'nodejs', '-e', script], check=True, capture_output=True, timeout=60)
            page = door.read_page(opened['tab_id'])
            problems = apply_tab.page_problems(page, fb, PAGE['url'])
            self.assertTrue(any('選擇履歷' in problem for problem in problems), problems)
            self.assertTrue(any('自我推薦信' in problem for problem in problems), problems)
        self.assertTrue(counts['returned_to_baseline'], counts)

    def test_sso_personal_verification_is_handed_off_instead_of_its_waiting_login_opener(self):
        from apply_fakeform import FakeForm
        with patch.object(cd, 'ego_bin', _env.REAL_EGO_BIN), cd.test_workspaces() as counts:
            server = FakeForm(requires_login=True).start()
            self.addCleanup(server.stop)
            door = cd.EgoDoor()
            door.open_for_agent('data:text/html,<h1>Application</h1>')
            door.read_pages([server.url('linkedin')])
            script = (f'const task=await taskSpace({door.workspace["id"]});'
                      f'const provider=await task.newPage();await provider.goto({json.dumps(server.url("google"))});'
                      'await provider.evaluate(()=>{document.title="使用您的密碼金鑰確認登入者是您本人";'
                      'document.getElementById("login-gate").innerHTML='
                      '"<h1>使用您的密碼金鑰確認登入者是您本人</h1>"'
                      '+"<p>裝置將要求您使用指紋、臉孔或螢幕鎖定功能驗證身分</p>";});')
            subprocess.run([cd.ego_bin(), 'nodejs', '-e', script], check=True, capture_output=True, timeout=60)
            action = door.human_action()
            self.assertEqual(action['page'], 'p3')
            self.assertIn('本人驗證', action['need'])
            door.workspace['handoff_page'] = action['page']
            self.assertEqual(door.hand_off()['page'], 'p3')
        self.assertTrue(counts['returned_to_baseline'], counts)

    def test_hidden_controls_do_not_disconnect_a_visible_answer_from_its_question(self):
        import apply_tab
        from urllib.parse import quote
        html = ('<form><section><h2>自我推薦信</h2><input type=text hidden>'
                '<div><textarea>Known recommendation</textarea><span>20/2000</span></div></section>'
                '<section><h2>其他備註</h2><textarea>Known recommendation</textarea></section></form>')
        fb = {'__ans__': [{'k': 'recommend', 'q': '自我推薦信', 'v': 'Known recommendation'}],
              PAGE['url']: {'form': {'f': [{'q': '自我推薦信', 'src': 'bank', 'k': 'recommend'}]}}}
        with patch.object(cd, 'ego_bin', _env.REAL_EGO_BIN), cd.test_workspaces() as counts:
            door = cd.EgoDoor()
            opened = door.open_for_agent('data:text/html;charset=utf-8,' + quote(html))
            self.assertEqual(apply_tab.page_problems(opened['page'], fb, PAGE['url']), [])
            script = (f'const task=await taskSpace({opened["workspace"]["id"]});'
                      'await task.page("p1").fill("css=section:first-child textarea","Wrong recommendation");')
            subprocess.run([cd.ego_bin(), 'nodejs', '-e', script], check=True, capture_output=True, timeout=60)
            page = door.read_page(opened['tab_id'])
            self.assertTrue(apply_tab.page_problems(page, fb, PAGE['url']))
        self.assertTrue(counts['returned_to_baseline'], counts)

    def test_eye_button_brings_ego_itself_to_the_front_only_when_pressed(self):
        door = cd.EgoDoor()
        with patch.object(door, '_hand_off_page', return_value={'page': 'p2'}), \
             patch.object(cd.subprocess, 'run') as run:
            self.assertEqual(door.hand_off(), {'page': 'p2'})
        run.assert_called_once_with(['open', '-a', 'ego lite'], check=False, timeout=15)

    def test_a_login_in_another_managed_page_is_handed_off_without_rebinding_the_application(self):
        from apply_fakeform import FakeForm
        with patch.object(cd, 'ego_bin', _env.REAL_EGO_BIN), cd.test_workspaces() as counts:
            server = FakeForm(requires_login=True).start()
            self.addCleanup(server.stop)
            door = cd.EgoDoor()
            application = door.open_for_agent('data:text/html,<h1>Application</h1>')
            door.read_pages([server.url('login')])
            action = door.human_action()
            self.assertEqual(action['page'], 'p2')
            self.assertEqual(action['site'], '127.0.0.1')
            door.workspace['handoff_page'] = action['page']
            receipt = door.hand_off()
            self.assertEqual(receipt['page'], 'p2')
            self.assertEqual(door.workspace['page'], 'p1')
            door.resume()
            self.assertEqual(door.read_page(application['tab_id'])['title'], '')
        self.assertTrue(counts['returned_to_baseline'], counts)



if __name__ == '__main__':
    unittest.main()
