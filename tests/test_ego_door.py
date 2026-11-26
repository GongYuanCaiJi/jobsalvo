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

REAL_SUBPROCESS_RUN = subprocess.run


def hand_off_test_workspace(door):
    calls = []

    def run(command, *args, **kwargs):
        if command == ['open', '-a', 'ego lite']:
            calls.append((command, kwargs))
            return Mock(returncode=0, stdout='', stderr='')
        return REAL_SUBPROCESS_RUN(command, *args, **kwargs)

    with patch.object(cd.subprocess, 'run', side_effect=run):
        receipt = door.hand_off()
    assert calls == [(['open', '-a', 'ego lite'], {'check': True, 'timeout': 15, 'capture_output': True})]
    return receipt


PAGE = {'url': 'https://jobs.test/apply', 'title': 'Apply',
        'fields': [{'label': 'Email', 'type': 'email', 'value': 'a@jobs.test'}],
        'shownFiles': ['resume.pdf'], 'lines': ['Apply', 'resume.pdf']}
WORKSPACE = {'id': 42, 'name': 'jobsalvo-test-card', 'page': 'p1'}

RESET_CALL = re.compile(r'(?:clearCookies|clearCache|clearStorage|clearBrowserCookies|clearBrowserCache|'
                        r'clearDataForOrigin|deleteAllCookies|browsingData[.]remove|'
                        r'(?:localStorage|sessionStorage)[.]clear)\b')


def response(value):
    return Mock(returncode=0, stderr='', stdout='@@jobsalvo-ego@@' + json.dumps({'ok': True, 'value': value}))


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

    def test_agents_are_told_not_to_send_native_keystrokes(self):
        # keyboard.paste 在 Mac 送真的 ⌘V,ego 當成使用者接手(2026-10-06 Cake 實測):規則要叫它改用 insertText
        rule = cd.EgoDoor().apply_rule()
        self.assertIn('keyboard.insertText', rule)
        self.assertIn('不用 keyboard.paste', rule)

    def test_new_workspace_starts_with_one_blank_page_without_reading_it(self):
        opened = {'workspace': dict(WORKSPACE), 'tab_id': '42:p1'}
        with patch.object(cd, '_workspace_name', return_value='jobsalvo-test-blank'), \
                patch.object(cd, '_ego', return_value=opened) as ego:
            door = cd.EgoDoor()
            self.assertEqual(door.new_workspace(blank=True), opened)
        script = ego.call_args.args[0]
        self.assertIn('taskSpace("jobsalvo-test-blank")', script)
        self.assertIn('task.page("p1")', script)
        self.assertNotIn('goto(', script)
        self.assertNotIn('evaluate(', script)
        self.assertEqual(door.workspace, WORKSPACE)

    def test_new_workspace_rejects_nonblank_or_reused_doors(self):
        with self.assertRaises(ValueError):
            cd.EgoDoor().new_workspace(blank=False)
        door = cd.EgoDoor()
        door.workspace = dict(WORKSPACE)
        with self.assertRaises(cd.NotNow):
            door.new_workspace(blank=True)

    def test_the_card_follows_its_form_into_another_page_of_its_own_workspace_only(self):
        door = cd.EgoDoor()
        door.workspace = {'id': 42, 'name': 'jobsalvo-card', 'page': 'p1'}
        with patch.object(cd, '_ego', return_value=True):
            self.assertEqual(door.follow_page('42:p2'), '42:p2')      # 申請表開在新分頁:改綁那一頁
            self.assertEqual(door.workspace['page'], 'p2')
            with self.assertRaises(cd.NotNow):
                door.follow_page('7:p1')                              # 別的工作區的頁:不收

    def test_profile_reload_accepts_leave_dialog_and_defers_the_r2_read(self):
        door = cd.EgoDoor()
        door.workspace = {'id': 42, 'name': 'jobsalvo-card', 'page': 'p1'}
        with patch.object(cd, '_ego', return_value={'url': 'https://jobs.test/profile'}) as ego:
            door.reload_profile_page('42:p1', 'https://jobs.test/profile')
        script = ego.call_args.args[0]
        self.assertIn('page.acceptDialog()', script)
        self.assertIn('page.goto(', script)
        self.assertNotIn('PROFILE_FN', script)

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
        self.assertIn('ego-browser skill', codex.apply_rule())

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

    def test_profile_attachment_download_is_not_a_browser_door_operation(self):
        self.assertFalse(hasattr(cd.EgoDoor(), 'download_attachments'))

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

    def test_read_profile_keeps_fields_and_links_in_the_cards_workspace(self):
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
            read = door.read_profile(url)
        self.assertEqual(read['fields'][0]['value'], '全職')
        self.assertEqual(read['links'], ['https://profile.test/file/cv.pdf'])

    def test_a_card_workspace_survives_switching_the_enabled_agent_family(self):
        import config as cf
        with patch.dict(cf.C, {'agent': {'agents': [{'id': 'cc', 'runtime': 'claude-code', 'browser': True}]}}), \
             patch('subprocess.run', return_value=response(PAGE)):
            door = cd.for_card({'runtime': 'codex', 'agent_id': 'old', 'session': 'old-conversation',
                                'workspace': WORKSPACE, 'tab_id': '42:p1'})
            self.assertEqual(door.read_page('42:p1'), PAGE)
        self.assertEqual(door.agent_id, 'cc')

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

    def test_handoff_says_the_page_is_unknown_when_the_card_has_none(self):
        door = cd.of('codex')
        door.workspace = {key: value for key, value in WORKSPACE.items() if key != 'page'}
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(cd.NotNow, '程式不知道是哪一頁'):
                door.hand_off()
        run.assert_not_called()

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



# 照 ego 的 API 形狀做的假工作區:p1 是 agent 開的個人頁,另一個是 ego 關掉最後一頁後留下、沒有標籤的空白分頁。
# 讀 p1 之後它就被關掉(讀完到截圖之間頁被關)。ego 拒絕不合規的標籤,錯誤字照 ego 的原字。
FAKE_EGO_WORKSPACE = """
const fs = await import('node:fs');
const closed = `${process.env.FAKE_EGO_DIR}/p1-closed`;  // 每次 ego 指令是新的 node,關掉的頁記在檔案
const tabs = [{label: 'p1', url: 'http://site.test/profile', title: 'Profile'}, {url: 'about:blank', title: ''}]
  .filter(tab => tab.label !== 'p1' || !fs.existsSync(closed));
globalThis.listTaskSpaces = async () => [{id: 620, name: 'jobsalvo-exam', ownership: 'agent'}];
globalThis.taskSpace = async id => ({spaceId: id, tabs: async () => tabs.slice(), page: label => {
  if (typeof label !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]*$/.test(label))
    throw new Error('page label must start with a letter and contain only letters, numbers, _ or -');
  return {
    evaluate: async () => { fs.writeFileSync(closed, '');
                            return {url: 'http://site.test/profile', title: 'Profile', text: 'Profile content'}; },
    screenshot: async ({path}) => { fs.writeFileSync(path, Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
                                    return path; }};
}});
"""


class SnapshotThroughEgo(unittest.TestCase):
    def test_a_page_no_one_can_name_or_one_closed_before_its_screenshot_keeps_egos_own_reason(self):
        # 答案來源:假工作區自己(沒有標籤的分頁、讀完就關掉的 p1)。真 agent 考試第三輪 sync empty-workspace:
        # 沒有標籤的分頁被編成 620:undefined,截圖原因寫成填表卡的「要重填」(.audit/exam-r3-diagnosis.md 第二類)
        from web import snap
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        ego = tmp / 'fake-ego'
        ego.write_text('#!/bin/sh\n{ cat "$(dirname "$0")/workspace.js"; cat; } | FAKE_EGO_DIR="$(dirname "$0")" node -\n')
        ego.chmod(0o755)
        (tmp / 'workspace.js').write_text(FAKE_EGO_WORKSPACE)
        evidence_dir = tmp / 'evidence'
        evidence_dir.mkdir()
        self.enterContext(patch.object(cd, 'ego_bin', return_value=str(ego)))
        self.enterContext(patch.dict(os.environ, {'JOBSALVO_EGO_WORKSPACE': '620',
                                                  'JOBSALVO_EGO_EVIDENCE_DIRS': json.dumps([str(evidence_dir)])}))
        with patch('sys.stdout', new_callable=__import__('io').StringIO) as out:
            self.assertEqual(0, snap.main(['jobsalvo-after']))
        printed = out.getvalue()
        self.assertNotIn(cd.GONE, printed)
        self.assertNotIn('undefined', printed)
        self.assertIn(f'截圖沒取得：{cd.NO_TAB_LABEL}', printed)
        self.assertIn('截圖沒取得：頁面不見了', printed)


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


LOGIN_PAGES = {path: '<!doctype html><title>Sign in</title><p>Sign in to continue</p>' for path in ('/login', '/linkedin', '/google')}


@unittest.skipUnless(os.environ.get('JOBSALVO_EGO_INTEGRATION') == '1', '真 ego 接手需明確啟用')
class NativeLoginHandoff(unittest.TestCase):
    def test_sso_personal_verification_is_handed_off_instead_of_its_waiting_login_opener(self):
        from website_testbench import FakeWebsiteServer
        with patch.object(cd, 'ego_bin', _env.REAL_EGO_BIN), cd.test_workspaces() as counts:
            server = FakeWebsiteServer(pages=LOGIN_PAGES).start()
            self.addCleanup(server.stop)
            door = cd.EgoDoor()
            door.open_for_agent('data:text/html,<h1>Application</h1>')
            door.read_pages([server.url('/linkedin')])
            quote = 'Enter the one-time code shown in your authenticator.'
            script = (f'const task=await taskSpace({door.workspace["id"]});'
                      f'const provider=await task.newPage();await provider.goto({json.dumps(server.url("/google"))});'
                      f'await provider.evaluate(()=>{{document.title="Continue";document.body.innerHTML="<p>{quote}</p>";}});')
            subprocess.run([cd.ego_bin(), 'nodejs', '-e', script], check=True, capture_output=True, timeout=60)
            action = door.human_action(f'{door.workspace["id"]}:p3', quote, '請在這頁輸入一次性代碼')
            self.assertEqual(action['page'], 'p3')
            self.assertEqual(action['quote'], quote)
            self.assertEqual(action['need'], '請在這頁輸入一次性代碼')
            door.workspace['handoff_page'] = action['page']
            self.assertEqual(hand_off_test_workspace(door)['page'], 'p3')
        self.assertTrue(counts['returned_to_baseline'], counts)

    def test_eye_button_brings_ego_itself_to_the_front_only_when_pressed(self):
        door = cd.EgoDoor()
        with patch.object(door, '_hand_off_page', return_value={'page': 'p2'}), \
             patch.object(cd.subprocess, 'run') as run:
            self.assertEqual(door.hand_off(), {'page': 'p2'})
        run.assert_called_once_with(['open', '-a', 'ego lite'], check=True, timeout=15, capture_output=True)

    def test_a_login_in_another_managed_page_is_handed_off_without_rebinding_the_application(self):
        from website_testbench import FakeWebsiteServer
        with patch.object(cd, 'ego_bin', _env.REAL_EGO_BIN), cd.test_workspaces() as counts:
            server = FakeWebsiteServer(pages=LOGIN_PAGES).start()
            self.addCleanup(server.stop)
            door = cd.EgoDoor()
            application = door.open_for_agent('data:text/html,<h1>Application</h1>')
            door.read_pages([server.url('/login')])
            quote = 'Complete the organization check before continuing.'
            script = (f'const task=await taskSpace({door.workspace["id"]});'
                      f'await task.page("p2").evaluate(()=>{{document.body.innerHTML="<p>{quote}</p>";}});')
            subprocess.run([cd.ego_bin(), 'nodejs', '-e', script], check=True, capture_output=True, timeout=60)
            action = door.human_action(f'{door.workspace["id"]}:p2', quote, '請完成頁面上的要求')
            self.assertEqual(action['page'], 'p2')
            self.assertEqual(action['quote'], quote)
            door.workspace['handoff_page'] = action['page']
            receipt = hand_off_test_workspace(door)
            self.assertEqual(receipt['page'], 'p2')
            self.assertEqual(door.workspace['page'], 'p1')
            door.resume()
            self.assertEqual(door.read_page(application['tab_id'])['title'], '')
        self.assertTrue(counts['returned_to_baseline'], counts)


if __name__ == '__main__':
    unittest.main()


class ReadsCurrentPageWithoutStabilityWindow(unittest.TestCase):
    def test_page_reads_do_not_wait_for_repeated_page_state(self):
        door = cd.EgoDoor()
        door.workspace = dict(WORKSPACE)
        seen = []

        def read(script, timeout=None):
            seen.append(script)
            if 'const urls =' in script:
                return {'pages': {'https://jobs.test/p': {'text': ''}}, 'reader': 'p2'}
            return {'url': 'https://jobs.test/p', 'reading': 'current page'}

        with patch.object(cd, '_ego', side_effect=read):
            door.read_profile_page('42:p1')
            door.read_pages(['https://jobs.test/p'])

        self.assertEqual(len(seen), 2)
        for script in seen:
            self.assertNotIn('await settle(', script)
            self.assertNotIn('waitForTimeout(300)', script)
