#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代投(apply_run)的閘門:最後一關一定是他核准,核准後答案一改就作廢,送出要有確認頁證據才算。
這些是程式守的,不靠 agent 自律,所以要測。

跑法(repo 根目錄):python3 -m unittest discover -s tests
"""
import os, sys, json, time, tempfile, unittest, contextlib, copy
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import form_record as fr      # noqa: E402
import agent_run as ar        # noqa: E402
import apply_run as run       # noqa: E402
import delivery_state as ds   # noqa: E402
import chrome_door            # noqa: E402
import fake_door as fc      # noqa: E402

U = 'https://jobs.lever.co/x/1'
T = run.today()
# 投遞前驗收跑過、沒有問題。核准規則也看它;這支測的是代投自己的閘門,驗收沒過擋送出的在 test_board_rules
OKST = {'schema_version': 2, 'checked_links': True, 'issues': []}
_passed = patch.object(fr, 'board_status', return_value=OKST)


def setUpModule():
    _passed.start()           # 測試用的看板路徑是假的(/tmp/board.html),讀不到驗收結果


def tearDownModule():
    _passed.stop()


def board():
    return {'__ans__': [{'k': 'nat', 'q': '國籍', 'v': 'Taiwan', 'zh': '台灣', 'at': T}],
            U: {'app': 'ship', 'form': {'plat': 'Lever', 'at': run.now(), 'f': [
                {'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
                {'q': 'Nationality', 'src': 'bank', 'k': 'nat'}]}}}


# 填好、停著的那一頁(程式確認前、送出前讀它跟確認時的樣子比,#316)
FORM_PAGE = {'url': U + '/apply', 'fields': [{'label': 'Full name', 'value': 'Alex Chen'},
                                             {'label': 'Nationality', 'value': 'Taiwan'}], 'lines': []}
FILLED = {'stage': 'fill', 'issues': [], 'tab_id': '7', 'session': 'S1', 'runtime': 'codex',
          'delivery': {'method': 'direct_upload'}}


def park(fb):
    """agent 填好了、停著等他(還沒確認)。"""
    confirm(fb)
    fb[U].pop('approve')
    fb[U]['ds'] = 'parked'


def confirm(fb):
    """他看過那一頁、按了確認送出(停著等你 → 你已確認)。卡上已經有的填表紀錄照留。"""
    m = fb[U]
    m['apply'] = {**FILLED, 'at': run.now(), **(m.get('apply') or {})}
    m['ds'] = 'confirmed'
    m['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}


@contextlib.contextmanager
def loaded(jobs, fb, d):
    """run_one 讀到的看板是 (jobs, fb)、指示寫在 d、寫回看板直接改 fb。"""
    with patch.object(run, 'load', return_value=(jobs, fb)), \
         patch.object(run, 'prompt_for', return_value=('prompt', d, None)), \
         patch.object(run.fr, 'record_fill', return_value=[]), \
         patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)):
        yield


class Approval(unittest.TestCase):
    def test_not_approved_is_refused(self):
        self.assertEqual(fr.approval_problem(board(), U, OKST), '還沒確認送出')

    def test_workspace_is_saved_before_the_agent_starts_and_survives_startup_failure(self):
        fb = board()
        workspace = {'id': 42, 'name': 'jobsalvo-card', 'page': 'p1'}
        fake = fc.FakeDoor(prepared={'workspace': workspace, 'tab_id': '42:p1', 'page': FORM_PAGE})
        observed = []
        def unavailable(*args, **kwargs):
            observed.append(copy.deepcopy((fb[U].get('apply') or {}).get('workspace')))
            return ar.AgentResult('unavailable', reason='startup', agent_id='primary')
        with tempfile.TemporaryDirectory() as out, fc.installed(fake), loaded({U: {'id': U}}, fb, out), \
             patch.object(ar, 'run', side_effect=unavailable), \
             patch.object(run.agent_report, 'report'), patch.object(run.agent_report, 'resolve'):
            ok, _message = run._run_one('fill', U, '/tmp/board.html')
        self.assertFalse(ok)
        self.assertEqual(observed, [workspace])
        self.assertEqual(fb[U]['apply']['workspace'], workspace)

    def test_filled_card_keeps_the_program_workspace_instead_of_the_claimed_binding(self):
        fb = board()
        workspace = {'id': 42, 'name': 'jobsalvo-card', 'page': 'p1'}
        fake = fc.FakeDoor(prepared={'workspace': workspace, 'tab_id': '42:p1', 'page': FORM_PAGE})
        result = {'tab_id': '42:p1', 'workspace': {'id': 99, 'name': 'untrusted', 'page': 'p1'}}
        with tempfile.TemporaryDirectory() as out, fc.installed(fake), loaded({U: {'id': U}}, fb, out), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, agent_id='primary')), \
             patch.object(ar, 'session_id', return_value='S1'), patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], result)), \
             patch.object(run, '_profile_check', return_value=[]), \
             patch.object(run.agent_report, 'report'), patch.object(run.agent_report, 'resolve'):
            run._run_one('fill', U, '/tmp/board.html')
        self.assertEqual(fb[U]['apply']['workspace'], workspace)

    def test_approved_snapshot_is_valid_until_an_answer_changes(self):
        fb = board()
        confirm(fb)
        self.assertIsNone(fr.approval_problem(fb, U, OKST))
        fb['__ans__'][0]['v'] = 'ROC'                        # 核准之後他(或我)改了答案
        self.assertIn('要重新確認送出', fr.approval_problem(fb, U, OKST))

    def test_approval_binds_the_workspace_even_when_the_page_handle_is_unchanged(self):
        fb = board()
        confirm(fb)
        fb[U]['apply']['workspace'] = {'id': 42, 'name': 'jobsalvo-first', 'page': 'p1'}
        fb[U]['approve'] = fr.approval(fb, U)
        self.assertIsNone(fr.approval_problem(fb, U, OKST))
        fb[U]['apply']['workspace']['reader'] = 'p2'
        self.assertIsNone(fr.approval_problem(fb, U, OKST))
        fb[U]['apply']['workspace']['name'] = 'jobsalvo-another'
        self.assertIn('要重新確認送出', fr.approval_problem(fb, U, OKST))

    def test_switching_agent_family_keeps_the_page_without_resuming_the_other_clis_session(self):
        fb = board()
        park(fb)
        workspace = {'id': 42, 'name': 'jobsalvo-first', 'page': 'p1'}
        fb[U]['apply'].update(workspace=workspace, tab_id='42:p1')
        fake = fc.FakeDoor(runtime='claude-code', agent_id='claude')
        with tempfile.TemporaryDirectory() as out, fc.installed(fake), loaded({U: {'id': U}}, fb, out), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, agent_id='claude')) as launch, \
             patch.object(ar, 'session_id', return_value='CLAUDE-NEW'), patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], {'tab_id': '42:p1'})), \
             patch.object(run, '_profile_check', return_value=[]), \
             patch.object(run.agent_report, 'report'), patch.object(run.agent_report, 'resolve'):
            run._run_one('fix', U, '/tmp/board.html')
        self.assertIsNone(launch.call_args.kwargs['resume'])
        self.assertEqual(fb[U]['apply']['workspace'], workspace)
        self.assertEqual(fb[U]['apply']['session'], 'CLAUDE-NEW')
        self.assertEqual(fb[U]['apply']['runtime'], 'claude-code')

    def test_exhausted_screenshot_warmup_is_not_retried_again_by_the_caller(self):
        fake = fc.FakeDoor(not_now={'shot'})
        with tempfile.TemporaryDirectory() as out, patch.object(run.time, 'sleep'):
            run.shoot('S1', out, 'fill', fake, tab_id='42:p1')
            with open(os.path.join(out, 'shot-error.txt')) as report:
                self.assertIn('NotNow', report.read())
        self.assertEqual(len([c for c in fake.calls if c[0] == 'shot']), 1)

    def test_pending_or_untranslated_answers_block_submission(self):
        fb = board()
        confirm(fb)
        fb['__ans__'][0]['inf'] = T; del fb['__ans__'][0]['at']
        self.assertIn('等你確認', fr.approval_problem(fb, U, OKST))
        fb['__ans__'][0].pop('inf'); fb['__ans__'][0]['at'] = T; fb['__ans__'][0]['tr'] = 1
        self.assertIn('重翻', fr.approval_problem(fb, U, OKST))

    def test_rerecording_the_form_drops_the_approval(self):
        fb = board()
        confirm(fb)
        fr.apply_record(fb, U, 'Lever', [{'q': 'Nationality', 'k': 'nat'}], today=T)
        self.assertNotIn('approve', fb[U])

    def test_mark_sent_locks_and_keeps_the_approval_as_evidence(self):
        fb = board()
        confirm(fb)
        fb[U]['form']['f'][1]['refill'] = 1
        ds.fire(fb, U, 'submit_start')
        ds.fire(fb, U, 'submit_ok', by='agent', sent_at=T, evidence={'text': 'Application submitted'})
        run.ship.record_sent(fb, U, version='en-general')      # 寄出的是哪一份:只有 ship.record_sent 寫
        m = fb[U]
        self.assertEqual((m['app'], m['form']['lock'], m['sent_v']), ('sent', 1, 'en-general'))
        self.assertNotIn('refill', m['form']['f'][1])
        self.assertNotIn('approve', m)                       # 送出了,那一頁是已收到申請:確認和分頁編號拿掉
        self.assertEqual(m['apply']['tab_id'], '')
        self.assertEqual(fr.approval_problem(fb, U, OKST), '已經送出了')


    def test_an_answer_changed_after_filling_blocks_approval_until_agent_retypes_it(self):
        fb = board()
        confirm(fb)
        fb[U]['form']['f'][1]['refill'] = 1                  # 答案庫改了,網頁上還是舊字
        self.assertIn('先讓 agent 改', fr.approval_problem(fb, U, OKST))
        fr.apply_clear_refill(fb, U)                         # agent 在原本那一頁改好了
        self.assertIsNone(fr.approval_problem(fb, U, OKST))

    def test_an_unconfirmed_submit_blocks_resending_until_he_clears_it(self):
        fb = board()
        confirm(fb)
        ds.fire(fb, U, 'submit_start')
        ds.fire(fb, U, 'submit_unsure', evidence={'clicked': True, 'problems': ['沒看到成功頁面']})
        self.assertIn('先確認到底送出沒有', fr.approval_problem(fb, U, OKST))     # 不然可能投兩次
        ds.fire(fb, U, 'not_sent', at=T)                                          # 他確認過沒送出:可以重送
        self.assertEqual(fr.approval_problem(fb, U, OKST), '還沒確認送出')        # 不直接回你已確認:重新看過再確認
        ds.fire(fb, U, 'confirm', approve=fr.approval(fb, U, T))
        self.assertIsNone(fr.approval_problem(fb, U, OKST))


class Eligible(unittest.TestCase):
    def test_fill_takes_unapproved_and_skips_ones_already_filled(self):
        fb = board(); jobs = {U: {'id': U}}
        self.assertEqual(run.eligible(jobs, fb, 'fill', status=OKST), [U])
        fb[U].update(ds='parked', apply=dict(FILLED))
        self.assertEqual(run.eligible(jobs, fb, 'fill', status=OKST), [])            # 停著等你的不重填
        self.assertEqual(run.eligible(jobs, fb, 'fill', U, status=OKST), [])         # 指定也一樣(狀態表不准)
        self.assertEqual(run.eligible(jobs, fb, 'submit', status=OKST), [])          # 沒確認不會送
        fb[U]['ds'] = 'stuck'
        self.assertEqual(run.eligible(jobs, fb, 'fill', U, status=OKST), [U])        # 卡住的可以重填

    def test_submit_takes_only_valid_approvals(self):
        fb = board(); jobs = {U: {'id': U}}
        confirm(fb)
        self.assertEqual(run.eligible(jobs, fb, 'submit', status=OKST), [U])
        self.assertEqual(run.eligible(jobs, fb, 'fill', status=OKST), [])

    def test_fix_needs_the_same_agent_and_something_to_fix(self):
        fb = board(); jobs = {U: {'id': U}}
        self.assertEqual(run.eligible(jobs, fb, 'fix', U, status=OKST), [])                 # 沒有 agent 填過的對話:叫不回來
        fb[U].update(ds='parked', apply={'stage': 'fill', 'session': 'S1', 'tab_id': '7'})
        self.assertEqual(run.eligible(jobs, fb, 'fix', status=OKST), [])                    # 批次:沒有待重打的不跑
        self.assertEqual(run.eligible(jobs, fb, 'fix', U, status=OKST), [U])                # 指定那一張(他寫了話)
        fb[U]['form']['f'][1]['refill'] = 1
        self.assertEqual(run.eligible(jobs, fb, 'fix', status=OKST), [U])


class Checks(unittest.TestCase):
    def setUp(self):
        self.d = self.enterContext(tempfile.TemporaryDirectory(prefix='applyrun-'))

    def write(self, name, obj=None):
        p = os.path.join(self.d, name)
        with open(p, 'w' if obj is not None else 'wb') as f:
            f.write(json.dumps(obj) if obj is not None else b'png')
        return p

    def test_prepared_application_allows_its_required_profile_and_authorization_pages(self):
        prepared = {'workspace': {'id': 42, 'name': 'jobsalvo-card', 'page': 'p1'},
                    'tab_id': '42:p1', 'page': {'url': U, 'fields': [], 'lines': []}}
        for runtime in ('codex', 'claude-code'):
            with self.subTest(runtime=runtime):
                prompt, _, _n = run.prompt_for('fill', U, {'id': U}, board(), '/tmp/board.html',
                                           prepared=prepared, door=chrome_door.of(runtime))
                self.assertIn('同一工作區內可另開必要分頁', prompt)
                self.assertIn('履歷同步、附件管理與登入授權', prompt)
                self.assertIn('其他卡與使用者的分頁不要動', prompt)
                self.assertIn('不要另開分頁重做申請表', prompt)
                self.assertNotIn('其他分頁不要動', prompt)

    def test_fill_prompt_lists_registered_profile_pages_for_links_missing_from_the_master(self):
        with patch.object(run, 'profile_pages', return_value='- linkedin en/one:https://www.linkedin.com/in/someone/'):
            prompt, _, _n = run.prompt_for('fill', U, {'id': U}, board(), '/tmp/board.html', door=chrome_door.of('codex'))
        self.assertIn('https://www.linkedin.com/in/someone/', prompt)
        self.assertIn('母稿沒寫的個人連結用程式登記的', prompt)
        self.assertIn('"qz"', prompt)                       # 題目不是他讀的語言時,交件附中文題目

    def test_profile_pages_list_the_registry_and_an_unreadable_registry_is_not_shown_as_none(self):
        reg = {'_說明': 'x', 'linkedin': {'en/one': {'read': 'https://www.linkedin.com/in/someone/'}, 'en/two': {}},
               'cake': {'zh/one': {'read': 'https://www.cake.me/me/someone'}}}
        with patch('profile_sync.registry', return_value=reg):
            self.assertEqual(run.profile_pages(U), '- linkedin en/one:https://www.linkedin.com/in/someone/\n'
                                                   '- cake zh/one:https://www.cake.me/me/someone')
        with patch('profile_sync.registry', side_effect=ValueError('平台履歷登記資料不是物件')), \
                self.assertRaisesRegex(ValueError, '平台履歷登記資料不是物件'):
            run.profile_pages(U)

    def test_login_and_resume_try_an_already_signed_in_provider_before_requesting_credentials(self):
        for runtime in ('codex', 'claude-code'):
            for stage in ('fill', 'fix'):
                with self.subTest(runtime=runtime, stage=stage):
                    prompt, _, _n = run.prompt_for(stage, U, {'id': U}, board(), '/tmp/board.html',
                                               door=chrome_door.of(runtime))
                    # 登入規則只有一份,在每個開瀏覽器的 agent 派工時都會帶的那段(agent_run 啟動時接上;同步平台履歷的 agent 也拿這段)
                    login = chrome_door.of(runtime).apply_rule()
                    self.assertIn('【登入與授權】', login)
                    self.assertNotIn('Sign in with Apple', prompt)          # 填表指示裡不再另抄一份
                    for words in ('Continue with Google', 'Sign in with Apple', '已登入的帳號完成', '一鍵帶入也這樣完成',
                                  '只剩密碼、二步驗證、驗證碼'):
                        self.assertIn(words, login)
                    self.assertNotIn('需要登入、遇到驗證碼、頁面要密碼:停下', prompt)
                    self.assertNotIn('遇到登入、密碼或真人驗證,停下', prompt)

    def test_the_current_sso_rule_takes_precedence_over_an_old_stop_at_login_instruction(self):
        rules = os.path.join(self.d, 'apply-rules.md')
        with open(rules, 'w', encoding='utf-8') as output:
            output.write('需要登入、遇到驗證碼、頁面要密碼:停下。')
        with patch.object(run.cf, 'APPLY_RULES', rules):
            prompt, _, _n = run.prompt_for('fix', U, {'id': U}, board(), '/tmp/board.html',
                                       door=chrome_door.current())
        self.assertIn('需要登入、遇到驗證碼、頁面要密碼:停下', prompt)               # 他的舊做法照樣附上
        self.assertIn('這一段優先於填表做法與平台筆記', chrome_door.current().apply_rule())

    def test_the_agent_sent_to_fill_or_fix_gets_the_sso_rule_over_an_old_stop_at_login_note(self):
        # #372:他的填表做法寫著「要登入就停下」,派出去的那一份(跟 _run 一樣 chrome=True 接上規矩)要同時帶著優先的 SSO 規則
        rules = os.path.join(self.d, 'apply-rules.md')
        with open(rules, 'w', encoding='utf-8') as output:
            output.write('需要登入、遇到驗證碼、頁面要密碼:停下。')
        for stage in ('fill', 'fix'):
            with self.subTest(stage=stage), patch.object(run.cf, 'APPLY_RULES', rules):
                p, _, _n = run.prompt_for(stage, U, {'id': U}, board(), '/tmp/board.html', door=chrome_door.current())
                fed = ar.prompt_stdin('main', p, browser=ar.apply_overrides(), board='/tmp/board.html', chrome=True)
                self.assertIn('需要登入、遇到驗證碼、頁面要密碼:停下', fed)
                for words in ('這一段優先於填表做法與平台筆記', 'Continue with Google', '已登入的帳號完成'):
                    self.assertIn(words, fed)

    def test_fill_and_login_resume_follow_the_users_import_rules_before_batch_fill(self):
        rules = os.path.join(self.d, 'apply-rules.md')
        with open(rules, 'w', encoding='utf-8') as output:
            output.write('表單有 Apply with LinkedIn 就先用;帶入的現職保留。')
        with patch.object(run.cf, 'APPLY_RULES', rules):
            for stage in ('fill', 'fix'):
                with self.subTest(stage=stage):
                    prompt, _, _n = run.prompt_for(stage, U, {'id': U}, board(), '/tmp/board.html',
                                               door=chrome_door.current())
                    self.assertIn('表單有 Apply with LinkedIn 就先用;帶入的現職保留。', prompt)
                    self.assertNotIn('不為可選資料開外部授權頁', prompt)
                    self.assertIn('先完成使用者要求的資料帶入', prompt)
                    self.assertIn('使用者明確要求保留的帶入值依其規則保留', prompt)

    def test_a_second_run_does_not_start_while_another_one_is_live(self):
        # 2026-10-14:命令列手動跑的那一輪撞斷了看板自動流程正在填的那一張
        other = {'running': True, 'pid': os.getpid() + 99999, 'stage': 'fill', 'url': 'https://x/1'}
        with patch('jobrun.read', return_value=other), patch.object(run, 'load') as load, \
                patch.object(sys, 'argv', ['apply_run.py', '--stage', 'fix', '--url', U]):
            run.main()
        load.assert_not_called()
        with patch('jobrun.read', return_value=dict(other, pid=os.getppid())), \
                patch.object(run, 'load', return_value=([], {})) as load, patch.object(run, 'eligible', return_value=[]), \
                patch.object(sys, 'argv', ['apply_run.py', '--stage', 'fix', '--url', U]):
            run.main()                                         # 看板替這一輪記的就是它自己:照常跑
        load.assert_called_once()

    def test_the_resume_crop_from_the_employer_page_is_kept_as_evidence(self):
        import evidence
        class Door:
            def shot_proof(self, tab, out, names, links=()):
                with open(out, 'wb') as f:
                    f.write(b'\x89PNG\r\n\x1a\n')
                return {'url': 'https://job-boards.example/apply', 'name': names[0]}
        out = tempfile.mkdtemp()
        with patch.object(run, 'proof_names', return_value=['EN-noCEX.pdf']), \
                evidence.opened('apply', 'fill', [U], self.board if hasattr(self, 'board') else None):
            got = run._proof_shot(U, out, time.time() - 5, Door(), '1:p1')
        self.assertEqual((got['name'], got['url']), ('EN-noCEX.pdf', 'https://job-boards.example/apply'))
        self.assertTrue(got['ev'].endswith('.png'))

    def test_a_chosen_platform_resume_is_found_by_its_id_in_the_page_links(self):
        import profile_sync as ps
        decision = {'profile_kind': 'fixed', 'fixed_url': 'https://pda.example/profile/preview?vno=abc123'}
        with patch.object(ps, 'decided', return_value=decision), \
                patch.object(ps, 'ID_PARAMS', {'example': 'vno'}):
            self.assertEqual(run.proof_links(U, {'id': U}, {}), ['vno=abc123'])
        self.assertEqual(run.proof_links(U, None, {}), [])

    def test_fix_after_a_login_barrier_gets_the_current_upload_paths(self):
        folder = os.path.join(self.d, 'Acceptance-' + run.card.card_id_from_url(U))
        os.makedirs(folder)
        for name in ('resume.pdf', 'support.pdf', 'merged.pdf'):
            with open(os.path.join(folder, name), 'wb') as output:
                output.write(b'local upload fixture')
        with open(os.path.join(folder, 'ship.json'), 'w') as output:
            json.dump({'files': ['resume.pdf', 'support.pdf'], 'merged': 'merged.pdf'}, output)
        with patch.object(run.cf, 'SHIP_DIR', self.d):
            prompt, _out, _n = run.prompt_for('fix', U, {'id': U}, board(), '/tmp/board.html',
                                         note='本人已完成登入,接著填', door=chrome_door.current())
        for name in ('resume.pdf', 'support.pdf', 'merged.pdf'):
            self.assertIn(os.path.join(folder, name), prompt)
        self.assertIn('k="nat"', prompt)
        self.assertIn('Taiwan', prompt)
        self.assertIn(fr.answer_fingerprint(board()['__ans__'][0]), prompt)

    def test_normal_form_files_can_be_ready_before_submit_without_claiming_a_server_upload(self):
        for runtime in ('codex', 'claude-code'):
            for stage in ('fill', 'fix'):
                with self.subTest(runtime=runtime, stage=stage):
                    prompt, _, _n = run.prompt_for(stage, U, {'id': U}, board(), '/tmp/board.html',
                                               door=chrome_door.of(runtime))
                    self.assertIn('檔案欄已選妥', prompt)
                    self.assertIn('非同步上傳', prompt)
                    self.assertIn('不可為了確認傳送而按送出', prompt)
                    self.assertNotIn('讀回確認網站已收到檔', prompt)
                    self.assertNotIn('只有申請表實際收到的檔名', prompt)

    def test_agent_process_gets_its_selected_board_without_changing_parent(self):
        # agent 拿到的是代號、不是看板檔的位置(#307);代號對回的是這一輪選的那一份
        import board_doc as bd
        output = self.write('agent.log', {})
        copy = os.path.join(self.d, 'copy-board.html')
        with patch.dict(os.environ, {'AGENT_BOARD': 'outer-board'}), \
                patch.object(ar, 'argv_for', return_value=(['agent'], None)), \
                patch.object(ar.subprocess, 'Popen', return_value='proc') as popen:
            result = ar.launch('prompt', output, 'repo', board=copy)
            self.assertEqual(os.environ.get('AGENT_BOARD'), 'outer-board')

        self.assertEqual(result, 'proc')
        env = popen.call_args.kwargs['env']
        self.assertNotIn('AGENT_BOARD', env)
        with patch.dict(os.environ, {bd.BOARD_ID: env[bd.BOARD_ID]}):
            self.assertEqual(bd.target(), os.path.realpath(copy))

    def page(self, **vals):
        labels = {'name': 'Full name', 'nat': 'Nationality'}
        return {'url': U + '/apply', 'fields': [dict({'name': k, 'value': v}, **({'type': 'file'} if isinstance(v, list) else {'label': labels.get(k, k)}))
                                               for k, v in vals.items()]}

    def test_radio_display_answer_belongs_to_its_question(self):
        import apply_tab
        q = 'How did you hear about this opening?'
        answer = 'Company career page'
        fb = {'__ans__': [{'k': 'source', 'v': answer}],
              U: {'form': {'f': [{'q': q, 'src': 'bank', 'k': 'source'}]}}}
        radio = lambda label, name='source', context=q: {'type': 'radio', 'name': name, 'label': label, 'value': '',
                                                          'context': context, 'choiceControl': True}
        li, choice = radio('LinkedIn'), radio(answer)
        choice.update(value=answer, shown=answer)
        page = {'url': U, 'fields': [li, choice], 'lines': [q, 'LinkedIn', answer]}
        self.assertEqual(apply_tab.page_problems(page, fb, U), [])
        other = dict(radio(answer, 'other', 'Preferred channel?'), value=answer, shown=answer)
        page['fields'].append(other)
        choice.update(value='', shown=None)
        li.update(value='LinkedIn', shown='LinkedIn')
        self.assertTrue(apply_tab.page_problems(page, fb, U))  # 別題同值不算。
        li.update(value='', shown=None)
        self.assertTrue(apply_tab.page_problems(page, fb, U))  # 沒選,可見選項也不算。
        choice.update(value=answer, shown=answer)
        li['context'] = choice['context'] = ''
        page['fields'].append({'type': 'textarea', 'label': 'Other notes', 'value': answer})
        self.assertTrue(apply_tab.page_problems(page, fb, U))  # 讀不到題目關聯不算(題目在頁上,下一行不是答案)。
        li['context'] = choice['context'] = q + '\nLinkedIn\n' + answer   # 選項字排在選項前面:這一組自己的選項字不算題目
        self.assertEqual(apply_tab.page_problems(page, fb, U), [])
        fb['__ans__'][0]['v'] = 'Do not choose Company career page; choose LinkedIn'
        self.assertTrue(apply_tab.page_problems(page, fb, U))  # 答案庫變了要擋。
        fb['__ans__'][0]['v'] = answer
        choice.update(value='', shown=None)
        self.assertTrue(apply_tab.page_problems(page, fb, U))  # 單語也不能採信全頁選項文字。

    def test_text_field_with_one_language_of_a_bilingual_answer_matches(self):
        import apply_tab
        q = 'How did you hear about this job?'
        fb = {'__ans__': [{'k': 'source', 'v': '公司徵才頁 / company career page'}],
              U: {'form': {'f': [{'q': q, 'src': 'bank', 'k': 'source'}]}}}
        field = {'type': 'text', 'name': 'q1', 'label': q, 'value': 'Company career page', 'context': q,
                 'choiceControl': False}
        page = {'url': U, 'fields': [field], 'fieldContexts': True, 'lines': [q]}
        self.assertEqual(apply_tab.page_problems(page, fb, U), [])
        fb['__ans__'][0]['v'] = 'Mandarin / English'                    # 不是一中一英:只填一段不算
        field['value'] = 'Mandarin'
        self.assertTrue(apply_tab.page_problems(page, fb, U))

    def test_bank_value_differing_without_choice_is_sent_back_at_handoff(self):
        q = 'How did you hear about our job opening?'
        entry = {'k': 'src', 'q': '來源', 'v': '公司徵才頁 / Company career page', 'at': '2026-09-20'}
        fb = {'__ans__': [entry]}
        field = {'q': q, 'src': 'bank', 'k': 'src', 'value': 'Referral'}
        with self.assertRaisesRegex(ValueError, 'choice'):   # 忘了附對應:交件就退回,不等最後驗收擋
            fr.apply_record(fb, U, 'Lever', fr.fields_from_fill({'fields': [field]}))
        self.assertNotIn('form', fb.get(U, {}))
        field['value'] = 'Company career page'               # 是原文裡完整的一段:驗收也認得(同一支 matches),不退回、不用對應
        fr.apply_record(fb, U, 'Lever', fr.fields_from_fill({'fields': [field]}))
        self.assertNotIn('choice', fb[U]['form']['f'][0])
        field['value'] = '公司徵才頁 / Company career page'   # 跟原文一樣不用對應
        fr.apply_record(fb, U, 'Lever', fr.fields_from_fill({'fields': [field]}))
        self.assertNotIn('page_value', fb[U]['form']['f'][0])
        field['value'] = '<redacted>'                         # 外掛遮掉的值讀不到,不能拿來比
        fr.apply_record(fb, U, 'Lever', fr.fields_from_fill({'fields': [field]}))

    def test_native_choice_mapping_keeps_bank_source_and_field_binding(self):
        import apply_tab
        q = 'How did you learn about this opportunity?'
        entry = {'k': 'source', 'q': '來源', 'v': '公司徵才頁', 'at': '2026-09-20'}
        fb = {'__ans__': [entry]}
        field = {'q': q, 'src': 'bank', 'k': 'source', 'value': 'Example Career Page',
                 'choice': {'name': 'question_17', 'source_hash': fr.answer_fingerprint(entry),
                            'why': '公司名稱標示的徵才頁與原稿公司徵才頁同義'}}
        before = dict(entry)
        fr.apply_record(fb, U, 'Example', fr.fields_from_fill({'fields': [field]}))
        self.assertEqual({k: v for k, v in entry.items() if k != 'qs'}, before)
        native = {'name': 'question_17', 'label': q + ' *', 'type': 'text', 'value': '',
                  'shown': 'Example Career Page', 'choiceControl': True}
        page = {'url': U, 'fields': [native]}
        self.assertEqual(apply_tab.page_problems(page, fb, U), [])
        native['name'] = ''                      # 程式用題目綁控制項,不靠 agent 給的 name
        self.assertEqual(apply_tab.page_problems(page, fb, U), [])
        native['choiceControl'] = False          # 一般文字欄不能用選項對應
        self.assertTrue(apply_tab.page_problems(page, fb, U))
        native['choiceControl'] = True
        native['label'] = 'Preferred channel?'
        page['lines'] = [q, 'LinkedIn']           # 題目在頁上,選中的那一格卻是別題
        self.assertTrue(apply_tab.page_problems(page, fb, U))
        native['label'] = q
        page.pop('lines')
        native['shown'] = 'LinkedIn'
        self.assertTrue(apply_tab.page_problems(page, fb, U))
        native['shown'] = 'Example Career Page'
        entry['redo'] = '2026-10-02'
        fb[U]['form']['f'][0]['choice']['source_hash'] = fr.answer_fingerprint(entry)
        self.assertTrue(apply_tab.page_problems(page, fb, U))
        entry.pop('redo')
        fb[U]['form']['f'][0]['choice']['source_hash'] = fr.answer_fingerprint(entry)
        fb[U]['form']['f'][0]['choice']['why'] = ''
        self.assertTrue(apply_tab.page_problems(page, fb, U))
        fb[U]['form']['f'][0]['choice']['why'] = field['choice']['why']
        entry['v'] = 'LinkedIn'
        self.assertTrue(apply_tab.page_problems(page, fb, U))

    def test_complete_question_binding_and_fix_source_version(self):
        import apply_tab
        entry = {'k': 'name', 'q': 'Name', 'v': 'Alex Chen', 'zh': '陳亞力'}
        choice = {'name': 'company', 'value': 'Alex Chen', 'source_hash': fr.answer_fingerprint(entry), 'why': 'test'}
        field = {'q': 'Name', 'src': 'bank', 'k': 'name', 'choice': choice, 'refill': True}
        fb = {'__ans__': [entry], U: {'form': {'f': [field]}}}
        page = {'url': U, 'fieldContexts': True, 'fields': [
            {'name': 'name', 'label': 'Name', 'value': ''},
            {'name': 'company', 'label': 'Company name', 'value': 'Alex Chen'}]}
        self.assertTrue(apply_tab.page_problems(page, fb, U))
        field.pop('choice')
        self.assertTrue(apply_tab.page_problems(page, fb, U))
        old_hash = fr.answer_fingerprint(entry)
        entry['v'] = 'New value'
        changed = run.changed_fields(fb, U)['Name']
        self.assertEqual(changed['k'], 'name')
        self.assertEqual(changed['source_hash'], fr.answer_fingerprint(entry))
        self.assertNotEqual(changed['source_hash'], old_hash)

    def test_choice_does_not_override_contact_or_plain_text(self):
        import apply_tab
        entry = {'k': 'email', 'q': 'Contact email', 'v': 'approved@example.test', 'zh': 'approved@example.test'}
        choice = {'name': 'email', 'value': 'other@example.test', 'source_hash': fr.answer_fingerprint(entry), 'why': 'test'}
        fb = {'__ans__': [entry], U: {'form': {'f': [{'q': 'Contact email', 'src': 'bank', 'k': 'email', 'choice': choice}]}}}
        native = {'name': 'email', 'label': 'Contact email', 'type': 'email', 'value': choice['value']}
        page = {'url': U, 'fieldContexts': True, 'fields': [native]}
        self.assertTrue(apply_tab.page_problems(page, fb, U))
        native['choiceControl'] = True
        self.assertTrue(apply_tab.page_problems(page, fb, U))
        native.update(type='text', choiceControl=False, value='Different text')
        entry.update(v='Original text', zh='原文')
        choice.update(value='Different text', source_hash=fr.answer_fingerprint(entry))
        self.assertTrue(apply_tab.page_problems(page, fb, U))

    def test_upload_blocked_by_codex_says_which_file_to_edit(self):
        # Codex 回「could not complete the permission request」:卡上照實講是網站沒被允許、要改哪個檔
        t0 = time.time() - 1
        self.write('fill.png')
        self.write('fill.json', {'fields': [], 'submitted': False, 'tab_id': '7', 'tab_url': U + '/apply', 'handoff': True,
                                 'problems': ['jobs.lever.co: could not complete the permission request to upload files']})
        bad = ' '.join(run.check_fill(board(), U, self.d, t0, 'S1', reader=lambda t: self.page())[0])
        self.assertIn('could not complete the permission request', bad)

    def test_fill_output_is_checked_against_the_bank(self):
        t0 = time.time() - 1
        fb = board()
        ok = {'fields': [{'q': 'Nationality', 'value': 'Taiwan', 'k': 'nat'}], 'submitted': False,
              'tab_id': '7', 'tab_url': U + '/apply', 'handoff': True, 'uploaded': ['cv.pdf']}
        good = self.page(name='Alex Chen', nat='Taiwan', cv=['cv.pdf'])
        self.write('fill.png')
        self.write('fill.json', ok)
        self.assertEqual(run.check_fill(fb, U, self.d, t0, 'S1', reader=lambda t: good)[0], [])
        self.write('fill.json', dict(ok, fields=[{'q': 'Nationality', 'value': 'ROC', 'k': 'nat'}], submitted=True))
        bad = ' '.join(run.check_fill(fb, U, self.d, t0, 'S1', reader=lambda t: good)[0])
        self.assertNotIn('常用答案是', bad)          # 頁面上是對的:以頁面為準,agent 回報寫法不同不算錯
        self.assertIn('已送出', bad)
        wrong = self.page(name='Alex Chen', nat='ROC', cv=['cv.pdf'])
        self.assertTrue(run.check_fill(fb, U, self.d, t0, 'S1', reader=lambda t: wrong)[0])   # 頁面上真的錯

        def unreadable(_t):
            raise RuntimeError('tab gone')
        bad = ' '.join(run.check_fill(fb, U, self.d, t0, 'S1', reader=unreadable)[0])
        self.assertIn('常用答案是', bad)              # 讀不到頁面時才退回比 agent 回報的值
        self.write('fill.json', dict(ok, handoff=False))                                  # 分頁沒留下來
        self.assertIn('沒有留在他的 Chrome', ' '.join(run.check_fill(fb, U, self.d, t0)[0]))

    def test_another_cards_broken_form_is_not_this_cards_problem(self):
        # 另一張在外部送出時還標著 refill:以前這一張填完也被判「沒完成」
        t0 = time.time() - 1
        fb = board()
        fb['https://ex.test/other'] = {'form': {'plat': 'x', 'lock': 1,
                                                'f': [{'q': 'Nationality', 'src': 'bank', 'k': 'nat', 'refill': 1}]}}
        ok = {'fields': [{'q': 'Nationality', 'value': 'Taiwan', 'k': 'nat'}], 'submitted': False,
              'tab_id': '7', 'tab_url': U + '/apply', 'handoff': True, 'uploaded': ['cv.pdf']}
        self.write('fill.png')
        self.write('fill.json', ok)
        good = self.page(name='Alex Chen', nat='Taiwan', cv=['cv.pdf'])
        self.assertEqual(run.check_fill(fb, U, self.d, t0, 'S1', reader=lambda t: good)[0], [])

    def test_a_form_recorded_in_an_earlier_round_the_same_day_does_not_count(self):
        # 同一天第二次填:agent 這一輪沒跑 form_record,不能拿早上那一輪記的表單當成這一輪的
        fb = board()
        fr.apply_record(fb, U, 'Lever', [{'q': 'Nationality', 'src': 'bank', 'k': 'nat'}])   # 早上那一輪
        t0 = time.time() + 2                                                                  # 這一輪在那之後才開始
        self.write('fill.png')
        self.write('fill.json', {'fields': [], 'tab_id': '7', 'handoff': True})
        bad = run.check_fill(fb, U, self.d, t0)[0]
        self.assertIn('表單沒有記進看板', bad)

    def test_platform_profile_attachments_are_not_treated_as_form_uploads(self):
        t0 = time.time() - 1
        fb = board()
        report = {
            'fields': [{'q': 'Nationality', 'value': 'Taiwan', 'k': 'nat'}],
            'submitted': False, 'tab_id': '7', 'tab_url': U + '/apply', 'handoff': True,
            'delivery': {'method': 'platform_profile'},   # 用哪一份、固定版還是客製版程式決定,agent 不寫(#313)
            'uploaded': ['resume.pdf', 'support.pdf'],
        }
        self.write('fill.png')
        self.write('fill.json', report)
        page = self.page(name='Alex Chen', nat='Taiwan')

        problems = run.check_fill(
            fb, U, self.d, t0, 'S1', reader=lambda _tab: page,
        )[0]

        self.assertEqual(problems, [])

    def test_a_one_click_apply_page_with_only_a_send_button_is_not_treated_as_submitted(self):
        fb = board()
        import apply_tab
        nothing = apply_tab.page_problems({'url': U + '/apply', 'fields': [], 'lines': []}, fb, U)
        self.assertIn('沒有任何欄位', ' '.join(nothing))                       # 什麼都沒有:可能送出了,要人看
        one_click = apply_tab.page_problems({'url': U + '/apply', 'fields': [], 'lines': [],
                                             'submits': ['Send Application']}, fb, U)
        self.assertNotIn('沒有任何欄位', ' '.join(one_click))                  # 還看得到送出鍵:就是停在送出前

    def test_question_text_with_required_mark_and_counter_is_the_same_question(self):
        import apply_tab
        field = {'label': '', 'context': '您是否已詳閱職缺描述？*(16/1500)'}   # 必填記號、字數計數器不是題目
        (asked,) = apply_tab.questions([field])
        self.assertEqual(apply_tab.qkey(asked), apply_tab.qkey('您是否已詳閱職缺描述？'))
        self.assertNotEqual(apply_tab.qkey(asked), apply_tab.qkey('您是否可以出差？'))

    def read_form(self, nodes, dom=None, frames_fail=()):
        """在 node 跑 READ_FORM:假的 page 照 CDP 回無障礙樹(nodes)、DOM 細節(dom:{backend: 屬性})。"""
        import apply_tab, subprocess
        script = """
const NODES = %s, DOM = %s, FAIL = %s;
const page = {
  cdp: async (m, p) => {
    if (m === 'Page.getFrameTree') return {frameTree: {frame: {id: 'F'}, childFrames: [{frame: {id: 'X'}}]}};
    if (m === 'Accessibility.getFullAXTree') { if (FAIL.includes(p.frameId)) throw new Error('跨網域'); return {nodes: p.frameId === 'F' ? NODES : []}; }
    if (m === 'DOM.describeNode') { const a = DOM[p.backendNodeId] || {}; return {node: {nodeName: a.tag || 'INPUT', attributes: Object.entries(a).filter(([k]) => k !== 'tag').flat()}}; }
  },
  evaluate: async () => [], url: async () => 'https://jobs.example.test/apply', title: async () => 't'};
(READER)(page).then(r => console.log(JSON.stringify(r)));
""".replace('READER', apply_tab.READ_FORM) % (json.dumps(nodes), json.dumps(dom or {}), json.dumps(list(frames_fail)))
        out = subprocess.run(['node', '-'], input=script, text=True, capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    @staticmethod
    def ax(*rows):
        """(角色, 名稱, 值, 子節點 id 清單, 其他) → CDP 的無障礙樹節點;id 從 1 起,第一個是根。"""
        nodes = []
        for i, (role, name, value, kids, extra) in enumerate(rows, 1):
            n = {'nodeId': str(i), 'role': {'value': role}, 'name': {'value': name}, 'childIds': [str(k) for k in kids],
                 'backendDOMNodeId': i, 'properties': [{'name': k, 'value': {'value': v}} for k, v in (extra or {}).items() if k != 'ignored']}
            if value is not None:
                n['value'] = {'value': value}
            if (extra or {}).get('ignored'):
                n['ignored'] = True
            nodes.append(n)
        for n in nodes:
            for k in n['childIds']:
                nodes[int(k) - 1]['parentId'] = n['nodeId']
        return nodes

    def test_the_form_is_read_from_the_accessibility_tree_in_reading_order(self):
        # 題目沒綁在格子上(名稱空的):照閱讀順序,前面那幾行字就是題目;格子裡的字不是下一題的題目;只有 ✱ 的不算一行
        nodes = self.ax(
            ('RootWebArea', '', None, [2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14], None),
            ('StaticText', 'Email', None, [], None),
            ('StaticText', '✱', None, [], None),
            ('textbox', 'Email', 'a@b.test', [5], {'required': True}),
            ('StaticText', 'a@b.test', None, [], None),
            ('StaticText', 'Are you eligible?', None, [], None),
            ('radio', 'Yes', None, [], {'checked': 'true'}),
            ('StaticText', 'Yes', None, [], None),
            ('radio', 'No', None, [], {'checked': 'false'}),
            ('StaticText', 'Cover letter', None, [], None),
            ('textbox', '', '您好,我是候選人', [], {'multiline': True}),   # 富文字編輯器:瀏覽器一樣給它角色和值
            ('StaticText', 'hidden', None, [], {'ignored': True}),
            ('textbox', 'Hidden field', '', [], {'ignored': True}),       # 藏起來的不在樹上
            ('button', 'Submit application', None, [], None),
        )
        dom = {7: {'name': 'elig', 'type': 'radio'}, 9: {'name': 'elig', 'type': 'radio'}, 11: {'tag': 'DIV'}}
        r = self.read_form(nodes, dom, frames_fail=['X'])                     # 跨網域 iframe 讀不到:略過,不整支壞掉
        f = {x['label'] or x['context']: x for x in r['fields']}
        self.assertEqual(f['Email']['value'], 'a@b.test')
        self.assertEqual(f['Yes']['context'], 'Are you eligible?')
        self.assertEqual(f['No']['context'], 'Are you eligible?')             # 同一組的選項帶同一題
        self.assertEqual((f['Yes']['value'], f['No']['value']), ('Yes', ''))
        self.assertEqual(f['Cover letter']['value'], '您好,我是候選人')
        self.assertEqual(f['Cover letter']['type'], 'textarea')
        self.assertNotIn('Hidden field', f)
        self.assertNotIn('✱', r['lines'])
        self.assertNotIn('a@b.test', r['lines'])
        import apply_tab
        self.assertEqual(apply_tab.questions([f['Yes'], f['No']]), ['Are you eligible?'] * 2)

    def test_a_rich_editor_and_the_second_box_of_one_question_are_read(self):
        # 2026-10-06 Cake 實測:求職信是沒標角色的編輯區塊(瀏覽器標成可編輯);電話是國碼選單加號碼兩格共用一題
        nodes = self.ax(
            ('RootWebArea', '', None, [2, 3, 4, 5, 6], None),
            ('StaticText', '電話號碼', None, [], None),
            ('combobox', 'Phone number country', 'Japan (+81)', [], None),
            ('textbox', '', '090 1234 5678', [], None),
            ('StaticText', '求職信', None, [], None),
            ('generic', '', None, [7, 9], {'editable': 'richtext', 'focusable': True}),
            ('paragraph', '', None, [8], {'editable': 'richtext'}),
            ('StaticText', '您好,', None, [], None),
            ('StaticText', '我是候選人。', None, [], None),
        )
        r = self.read_form(nodes, {6: {'tag': 'DIV'}})
        import apply_tab
        tel = next(f for f in r['fields'] if f['value'] == '090 1234 5678')
        letter = next(f for f in r['fields'] if f['type'] == 'textarea')
        self.assertEqual(letter['value'], '您好,我是候選人。')
        self.assertEqual(apply_tab.questions([tel, letter]), ['電話號碼', '求職信'])
        self.assertNotIn('我是候選人。', r['lines'])                          # 編輯區塊裡的字是那一格的值

    def test_an_option_that_is_one_part_of_a_bilingual_answer_matches_without_a_mapping(self):
        # 2026-10-07 Lever 實測:沒有這條,程式判對不上、又叫 agent 改一輪(333 秒)
        import apply_tab
        fb = board()
        fb['__ans__'].append({'k': 'src', 'q': '從哪裡得知', 'v': '公司徵才頁 / Company career page'})
        fb[U]['form']['f'] = [{'q': 'How did you hear?', 'src': 'bank', 'k': 'src'}]
        radio = lambda label, on: {'label': label, 'type': 'radio', 'value': label if on else '', 'context': 'How did you hear?',
                                   'choiceControl': True, 'shown': label if on else None}
        page = {'url': U + '/apply', 'fieldContexts': True, 'lines': ['How did you hear?'], 'fields': [
            radio('LinkedIn job posting', False), radio('Company career page', True)]}
        self.assertEqual(apply_tab.page_problems(page, fb, U), [])
        page['fields'] = [radio('LinkedIn job posting', True), radio('Company career page', False)]
        self.assertTrue(apply_tab.page_problems(page, fb, U))                # 選錯照樣擋

    def test_a_picker_that_is_not_a_form_field_shows_its_value_on_the_next_line(self):
        # 2026-10-06 實測:「選擇履歷」不是表單欄位,選好的那一份只是下一行字
        import apply_tab
        fb = board()
        fb['__ans__'].append({'k': 'pick_resume', 'q': '選擇履歷', 'v': '紅隊｜中文'})
        fb[U]['form']['f'] = [{'q': '選擇履歷', 'src': 'bank', 'k': 'pick_resume', 'v': '紅隊｜中文'}]
        page = {'url': U + '/apply', 'fieldContexts': True, 'submits': ['送出'], 'fields': [],
                'lines': ['選擇履歷', '紅隊｜中文', '預覽履歷']}
        self.assertEqual(apply_tab.page_problems(page, fb, U), [])
        page['lines'] = ['選擇履歷', '藍隊｜中文', '預覽履歷']
        self.assertTrue(apply_tab.page_problems(page, fb, U))                # 選的是別份:照樣擋
        # 下一格(求職信)的前文把這一題也包進去:照樣看下一行
        page['lines'] = ['選擇履歷', '紅隊｜中文', '自我推薦信']
        page['fields'] = [{'label': '', 'type': 'textarea', 'value': '您好', 'context': '選擇履歷\n紅隊｜中文\n自我推薦信'}]
        self.assertEqual(apply_tab.page_problems(page, fb, U), [])
        # 對到的是選項:下一行可能只是沒被選的選項,不採信
        page['lines'] = ['選擇履歷', '紅隊｜中文', '藍隊｜中文']
        page['fields'] = [{'label': '紅隊｜中文', 'type': 'radio', 'value': '', 'context': '選擇履歷', 'choiceControl': True},
                          {'label': '藍隊｜中文', 'type': 'radio', 'value': '藍隊｜中文', 'context': '選擇履歷', 'choiceControl': True}]
        self.assertTrue(apply_tab.page_problems(page, fb, U))

    def test_a_rich_editor_reading_back_paragraphs_joined_still_matches(self):
        # 2026-10-06 Cake 實測:編輯區塊讀回來分段處接在一起,答案庫是分段寫的
        import apply_tab
        fb = board()
        fb['__ans__'].append({'k': 'letter', 'q': '求職信', 'v': '您好,我是候選人。\n我做過計費漏洞研究。'})
        fb[U]['form']['f'] = [{'q': '求職信', 'src': 'bank', 'k': 'letter'}]
        page = {'url': U + '/apply', 'fieldContexts': True, 'lines': ['求職信'], 'fields': [
            {'label': '', 'type': 'textarea', 'context': '求職信', 'value': '您好,我是候選人。我做過計費漏洞研究。'}]}
        self.assertEqual(apply_tab.page_problems(page, fb, U), [])
        page['fields'][0]['value'] = '您好,我是候選人。'
        self.assertTrue(apply_tab.page_problems(page, fb, U))                # 少了一段:照樣擋

    def test_a_dropdown_showing_only_part_of_the_picked_option_still_matches(self):
        import apply_tab
        self.assertTrue(apply_tab.matches('Japan +81', '+81', 'box'))         # 選單框裡只顯示國碼
        self.assertFalse(apply_tab.matches('Japan +81', '+8', 'box'))         # 不是完整的一段
        self.assertFalse(apply_tab.matches('No, but willing', 'No', 'box'))   # 太短的不算
        self.assertFalse(apply_tab.matches('Japan +81', '+81', 'option'))     # radio、勾選是整個選項
        self.assertFalse(apply_tab.matches('Japan +81', '+81'))               # 文字格要整段
        self.assertTrue(apply_tab.matches('United Kingdom (+44)', '+44', 'option'))
        self.assertFalse(apply_tab.matches('+44 7700 900123', '+44', 'box'))  # 電話不放寬
        self.assertTrue(apply_tab.matches('公司徵才頁 / Company career page', 'Company career page'))  # 雙語填一種
        self.assertFalse(apply_tab.matches('第一段。\n第二段。', '第一段。'))   # 分段的長答案少一段不算

    def test_a_custom_dropdown_shows_its_choice_in_its_own_box(self):
        # 自製下拉選單(2026-10-06 Greenhouse 實測):瀏覽器給這一格的值是空的,選好的字在同一個框裡;
        # 播報區「option X, selected.」不算頁面上的字
        nodes = self.ax(
            ('RootWebArea', '', None, [2, 3, 6], None),
            ('StaticText', 'How did you learn about this opportunity?', None, [], None),
            ('generic', '', None, [4, 5], None),
            ('StaticText', 'Career Page', None, [], None),
            ('combobox', 'How did you learn about this opportunity?', None, [], {'expanded': False}),
            ('generic', '', None, [7], {'live': 'polite'}),
            ('StaticText', 'option Career Page, selected.', None, [], None),
        )
        r = self.read_form(nodes)
        (f,) = r['fields']
        self.assertEqual((f['value'], f['shown'], f['choiceControl']), ('Career Page', 'Career Page', True))
        self.assertNotIn('option Career Page, selected.', r['lines'])

    def test_a_text_answer_carrying_an_option_mapping_is_checked_as_text(self):
        import apply_tab
        fb = board()
        fb[U]['form']['f'][1]['choice'] = {'value': 'Taiwan', 'why': '格式不同'}       # 文字格卻帶了選項對應
        page = {'url': U + '/apply', 'lines': [], 'fieldContexts': True, 'fields': [
            {'label': 'Full name', 'value': 'Alex Chen', 'type': 'text'},
            {'label': 'Nationality', 'value': 'Taiwan', 'type': 'textarea'}]}
        self.assertEqual(apply_tab.page_problems(page, fb, U), [])

    def test_a_downloaded_readback_stands_in_for_a_visible_file_name(self):
        import gate_apply, gate
        page = {'url': U + '/apply', 'fields': [], 'lines': [], 'submits': ['送出']}
        truth = gate.Truth(U, {}, board(), page=page)
        sheet = {'delivery': {'method': 'direct_upload'}, 'upload_readback': 'downloaded',
                 'uploaded_files': [{'name': 'merged.pdf', 'path': '/tmp/x/merged.pdf'}]}
        self.assertIsNone(gate_apply._uploaded(['merged.pdf'], sheet, truth))    # 下載回來的位元組由程式比
        self.assertTrue(gate_apply._uploaded(['merged.pdf'], dict(sheet, upload_readback='unavailable'), truth))

    def test_an_answer_on_an_earlier_step_of_a_multi_step_form_is_not_called_wrong(self):
        import apply_tab
        fb = board()
        last_step = {'url': U + '/apply', 'fieldContexts': True, 'lines': ['Years in security?'], 'fields': [
            {'label': '', 'context': 'Years in security?', 'value': '3', 'type': 'textarea'}]}
        self.assertEqual(apply_tab.page_problems(last_step, fb, U), [])     # 國籍在前一步:這一頁沒有這一題
        same_page = dict(last_step, lines=['Nationality'], fields=[{'label': 'Nationality', 'value': 'Japan', 'type': 'text'}])
        self.assertTrue(apply_tab.page_problems(same_page, fb, U))           # 題目就在這一頁,答案不對:照樣擋

    def test_a_page_that_is_another_job_blocks_approval(self):
        """agent 打開申請頁判斷不是這張卡的缺(或關了):不算填好,核准按不下去。"""
        t0 = time.time() - 1
        self.write('fill.png')
        self.write('fill.json', {'fields': [], 'submitted': False, 'tab_id': '7', 'tab_url': U + '/apply', 'handoff': True,
                                 'posting': {'title': 'DevSecOps Engineer', 'same_job': False}})
        bad = run.check_fill(board(), U, self.d, t0, reader=lambda _t: {'url': U + '/apply', 'fields': [], 'lines': []})[0]
        self.assertIn('不是這張卡的職缺', ' '.join(bad))
        self.assertNotIn('沒有任何欄位', ' '.join(bad))                     # 沒填就沒欄位:不另外列

    def test_same_job_with_a_stale_card_name_is_renamed_keeping_the_link(self):
        path = _env.make_board(os.path.join(self.d, 'b.html'), jobs=[
            {'id': U, 'target': f'Northwind Graduate · Risk Operations Specialist (SQL)（[Lever]({U})）'}])
        run.rename(U, 'Northwind Accelarator Program - Risk Analyst', 'Northwind', path)
        t = _env.read_board(path)['data']['jobs'][0]['target']
        self.assertEqual(t, f'Northwind Accelarator Program - Risk Analyst（[Lever]({U})）')
        run.rename(U, 'Northwind Accelarator Program - Risk Analyst', 'Northwind', path)     # 已經對了就不動
        self.assertEqual(_env.read_board(path)['data']['jobs'][0]['target'], t)

    def test_the_page_itself_is_read_not_agents_report(self):
        """agent 說它填好了不算數:程式自己去讀那一頁。分頁不見、值不對、檔沒選上、那一頁已經換掉(可能被送出),都要擋。"""
        import apply_tab
        t0 = time.time() - 1
        fb = board()
        self.write('fill.png')
        self.write('fill.json', {'fields': [], 'tab_id': '7', 'tab_url': U + '/apply', 'handoff': True, 'uploaded': ['cv.pdf']})

        def gone(s, t):
            raise LookupError('Tab 7 not found')
        self.assertIn('讀不到', ' '.join(run.check_fill(fb, U, self.d, t0, 'S1', reader=gone)[0]))
        wrong = self.page(name='Alex Chen', nat='ROC', cv=[])
        bad = ' '.join(run.check_fill(fb, U, self.d, t0, 'S1', reader=lambda t: wrong)[0])
        self.assertIn('Nationality', bad)
        self.assertIn('上傳欄裡沒有 cv.pdf', bad)
        # 頁面上裝不了擋送出(Codex 外掛不准改頁面),只能事後查:網址換了、欄位不見了 = 可能被送出了
        sent = {'url': U + '/thanks', 'fields': []}
        self.assertIn('可能被送出了', ' '.join(apply_tab.page_problems(sent, fb, U, tab_url=U + '/apply')))
        self.assertIn('可能被送出了', ' '.join(apply_tab.page_problems({'url': U + '/apply', 'fields': []}, fb, U)))
        # 停在網站的真人驗證(Cloudflare):照實講,不說成「可能被送出了」,卡上給他自己投的路
        blocked = {'url': U + '/apply', 'title': '請稍候...', 'lines': ['正在執行安全驗證', '驗證您是人類'], 'fields': []}
        self.assertEqual(apply_tab.page_problems(blocked, fb, U, tab_url=U + '/apply'), [apply_tab.HUMAN_CHECK])
        self.assertFalse(apply_tab.human_check({'title': 'Risk Analyst', 'lines': ['Submit your application']}))
        # Lever 傳完會把上傳欄清空,檔名改顯示在標籤上:這樣也算傳上去了
        lever = {'url': U + '/apply', 'fields': [{'type': 'file', 'value': [], 'label': 'Resume/CV CV'}, {'value': 'Alex Chen'}, {'value': 'Taiwan'}]}
        self.assertEqual(apply_tab.page_problems(lever, fb, U, ['cv.pdf']), [])
        # 程式讀到空的 email 必須擋住;不再套外掛藏個資的例外。
        hid = {'url': U + '/apply', 'fields': [{'type': 'email', 'label': 'Email', 'value': ''},
                                               {'type': 'text', 'label': 'Full name', 'value': 'Someone'}]}
        fb2 = board()
        fb2[U]['form']['f'].append({'q': 'Email', 'src': 'rz', 'v': 'you@example.com'})
        probs = apply_tab.page_problems(hid, fb2, U)
        self.assertTrue([p for p in probs if 'Email' in p])
        self.assertTrue([p for p in probs if 'Full name' in p])
        # 中文表單填的是中文翻譯,也算對上;下拉選單看顯示的字
        zh = {'url': U + '/apply', 'fields': [{'label': 'Full name', 'value': 'Alex Chen'},
                                              {'label': 'Nationality', 'value': '2', 'shown': '台灣', 'choiceControl': True}]}
        self.assertEqual(apply_tab.page_problems(zh, fb, U), [])
        # Greenhouse 的 Email 即使是一般文字框也必須有實際值。
        gh = {'url': U + '/apply', 'fields': [
            {'type': 'text', 'label': 'Email', 'value': 'you@example.com'}, {'label': 'Full name', 'value': 'Alex Chen'},
            {'type': 'combobox', 'label': 'Country', 'value': '', 'shown': '+44', 'choiceControl': True}]}
        fb3 = board()
        fb3[U]['form']['f'] = [{'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
                               {'q': 'Email', 'src': 'rz', 'v': 'you@example.com'},
                               {'q': 'Country', 'src': 'rz', 'v': 'United Kingdom (+44)'}]
        self.assertEqual(apply_tab.page_problems(gh, fb3, U), [])
        # 放寬的是「顯示的字是答案的一段」,不是什麼都放:國碼選錯還是抓得到
        gh['fields'][2]['shown'] = '+81'
        self.assertIn('Country', ' '.join(apply_tab.page_problems(gh, fb3, U)))
        gh['fields'][2]['shown'] = '+44'
        # 電話讀不到或不同都要擋住;國碼選單不能充當電話。
        gh['fields'].append({'type': 'tel', 'label': 'Phone', 'value': '<redacted>'})
        fb3[U]['form']['f'].append({'q': 'Phone', 'src': 'rz', 'v': '+44 7700 900000'})
        self.assertIn('Phone', ' '.join(apply_tab.page_problems(gh, fb3, U)))
        gh['fields'][-1]['value'] = '+44 7700 900000'
        self.assertEqual(apply_tab.page_problems(gh, fb3, U), [])
        gh['fields'][-1] = {'type': 'text', 'label': 'Mobile', 'value': 'Alex Chen'}
        gh['lines'] = ['Phone', 'Mobile']           # 題目在頁上,卻沒有一格是它
        self.assertIn('Phone', ' '.join(apply_tab.page_problems(gh, fb3, U)))
        gh.pop('lines')
        # 104「選擇履歷」不是表單欄位,選好的值只是一行字:整行一樣才算;選到別份還是抓得到
        fb4 = board()
        fb4[U]['form']['f'] = [{'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
                               {'q': '選擇履歷', 'src': 'rz', 'v': '紅隊｜中文'}]
        p104 = {'url': U + '/apply', 'fields': [{'label': 'Full name', 'value': 'Alex Chen'}],
                'fieldContexts': True, 'lines': ['選擇履歷', '紅隊｜中文', '預覽履歷']}
        self.assertEqual(apply_tab.page_problems(p104, fb4, U), [])
        p104['lines'] = ['選擇履歷', '紅隊｜English', '預覽履歷']
        self.assertIn('選擇履歷', ' '.join(apply_tab.page_problems(p104, fb4, U)))
        # 上傳完上傳欄不見了、檔名只用文字顯示(Greenhouse):頁面上看得到檔名也算選上了
        gh2 = {'url': U + '/apply', 'fields': [{'value': 'Alex Chen'}, {'value': 'Taiwan'}], 'shownFiles': ['merged.pdf']}
        self.assertEqual(apply_tab.page_problems(gh2, fb, U, ['merged.pdf']), [])
        self.assertIn('上傳欄裡沒有 other.pdf', ' '.join(apply_tab.page_problems(gh2, fb, U, ['other.pdf'])))

    def test_the_page_is_read_through_the_family_that_filled_it(self):
        """填這張的那一家用它自己的門路讀那一頁,驗收標準每一家一樣:讀不到就不算填好。"""
        t0 = time.time() - 1
        fb = board()
        self.write('fill.json', {'fields': [], 'submitted': False, 'tab_id': '1234', 'tab_url': U + '/apply',
                                 'handoff': True, 'uploaded': []})
        self.write('fill.png')
        door = fc.FakeDoor('claude-code', not_now={'read_page'})
        bad = ' '.join(run.check_fill(fb, U, self.d, t0, 'S1', door=door)[0])
        self.assertEqual(door.calls, [('read_page', '1234')])
        self.assertIn('讀不到', bad)
        door = fc.FakeDoor('codex', page={'url': U + '/apply', 'fields': [{'name': 'name', 'value': 'Alex Chen'},
                                                                            {'name': 'nat', 'value': 'Taiwan'}]})
        self.assertEqual(run.check_fill(fb, U, self.d, t0, 'S1', door=door)[0], [])
        self.assertIn('讀不到', ' '.join(run.check_fill(fb, U, self.d, t0, 'S1')[0]))    # 不知道是哪一家開的:讀不到

    def test_submit_needs_program_screenshot(self):
        t0 = time.time() - 1
        self.write('submit.json', {'submitted': True, 'confirm_text': 'Application submitted'})
        self.assertFalse(run.check_submit(self.d, t0)[0])                     # 不管哪一家:沒有程式截的圖不算

    def test_submit_needs_confirmation_evidence(self):
        t0 = time.time() - 1
        self.write('submit.json', {'submitted': True, 'confirm_text': ''})
        self.write('submit.png')
        self.assertFalse(run.check_submit(self.d, t0)[0])
        page = {'url': U + '/thanks', 'lines': ['Application submitted'], 'fields': []}
        self.write('submit.json', {'submitted': True, 'reason': '本次申請已收到',
                                  'confirm_url': page['url'], 'confirm_text': 'Application submitted'})
        self.assertTrue(run.check_submit(self.d, t0, U, page)[0])


class Dispatch(unittest.TestCase):
    """代投用 Codex 的 Chrome 外掛:playwright MCP 關掉(它會自己開一個看得到的瀏覽器搶他的螢幕),
    一張一隻 agent,修改和送出是 codex exec resume 叫回同一段對話。"""

    def setUp(self):
        # lean() 讀使用者的 Codex 設定;測試用一份假的,不依賴這台機器裝了什麼。
        self._cfg = tempfile.NamedTemporaryFile('w', suffix='.toml', delete=False)
        self._cfg.write('[mcp_servers.playwright]\ncommand = "x"\n[mcp_servers.node_repl]\ncommand = "y"\n'
                        '[plugins."chrome@openai-bundled"]\nenabled = true\n[plugins."other@x"]\nenabled = true\n')
        self._cfg.close()
        self._patch = patch.object(ar, 'CODEX_CONFIG', self._cfg.name)
        self._patch.start()


    def tearDown(self):
        self._patch.stop()
        os.unlink(self._cfg.name)

    def test_plugins_are_disabled_with_unquoted_names(self):
        """外掛名稱加引號,Codex 會默默忽略這個設定,外掛就沒關掉。"""
        ov = ar.lean()
        self.assertIn('plugins.other@x.enabled=false', ov)
        self.assertIn('plugins.chrome@openai-bundled.enabled=false', ov)
        self.assertFalse(any('plugins."' in x for x in ov))
        self.assertIn('plugins.chrome@openai-bundled.enabled=false', ar.apply_overrides())

    def test_apply_uses_the_codex_chrome_plugin_and_never_playwright(self):
        ov = ar.apply_overrides()
        self.assertIn('mcp_servers.playwright.enabled=false', ov)
        self.assertIn('mcp_servers.node_repl.enabled=false', ov)       # 它的 Chrome 介面會把分頁群組放進他的視窗
        argv, cwd = ar.argv_for('main', 'P', '/repo', browser=ov, chrome=True)
        self.assertEqual([os.path.basename(argv[0])] + argv[1:2], ['codex', 'exec'])
        self.assertTrue(ar.prompt_stdin('main', 'P', browser=ov, chrome=True).startswith(ar.apply_rule('codex')))   # 換成代投的鐵律
        self.assertIn('ego-browser', ar.apply_rule('codex'))
        self.assertEqual(ar.apply_rule('codex'), ar.apply_rule('claude-code'))
        with self.assertRaises(ValueError):
            ar.argv_for('alt', 'P', '/repo', browser=ov, chrome=True)

    def test_fix_and_submit_resume_the_same_agent(self):
        argv, cwd = ar.argv_for('main', 'P', '/repo', browser=ar.apply_overrides(), resume='S1', chrome=True)
        self.assertEqual([os.path.basename(argv[0])] + argv[1:4], ['codex', 'exec', 'resume', 'S1'])
        self.assertNotIn('-C', argv)                                   # resume 不收 -C / -s:工作目錄用 cwd
        self.assertNotIn('-s', argv)
        self.assertIn('sandbox_mode="danger-full-access"', argv)
        self.assertEqual(cwd, '/repo')

    def test_fix_and_submit_dispatch_to_the_filling_agent(self):
        fb = board()
        fb[U]['apply'] = {'session': 'S1', 'agent_id': 'browser-two', 'runtime': 'codex'}
        park(fb)
        jobs = {U: {'id': U, 'target': 'X'}}
        calls = []
        outcome = ar.AgentResult('failed', 1, 42, agent_id='browser-two')
        with tempfile.TemporaryDirectory(prefix='apply-agent-pin-') as d, \
             fc.installed(fc.FakeDoor('codex', agent_id='browser-two', page=copy.deepcopy(FORM_PAGE))), \
             loaded(jobs, fb, d), \
             patch.object(run.agent_report, 'report'), \
             patch.object(ar, 'run', side_effect=lambda *args, **kwargs: calls.append(kwargs) or outcome):
            self.assertFalse(run.run_one('fix', U, '/tmp/board.html')[0])
            self.assertEqual(fb[U]['apply']['issues'], [outcome.message()])   # 沒跑成的原因寫在卡上
            self.assertEqual(ds.state(fb[U]), 'stuck')                      # 修改沒成功時本來就不准送;另起一張看送出
            fb[U] = board()[U]
            fb[U]['apply'] = {'session': 'S1', 'agent_id': 'browser-two', 'runtime': 'codex'}
            confirm(fb)
            self.assertFalse(run.run_one('submit', U, '/tmp/board.html')[0])

        self.assertEqual([call['agent_id'] for call in calls], ['browser-two', 'browser-two'])
        self.assertEqual([call['resume'] for call in calls], ['S1', 'S1'])
        self.assertTrue(all(call['browser_required'] for call in calls))

    def test_an_unreadable_profile_registry_fails_this_card_with_its_reason_instead_of_ending_the_batch(self):
        # 登記表壞了就不填(指示要列他的平台履歷頁);但卡已經記成正在填,這一張要照實記失敗,批次的下一張照跑
        import profile_sync as ps
        fb = board()
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        with tempfile.TemporaryDirectory(prefix='apply-registry-') as directory:
            broken = os.path.join(directory, 'profiles.json')
            with open(broken, 'w', encoding='utf-8') as f:
                f.write('{"104": ')
            with fc.installed(fc.FakeDoor('codex', page=copy.deepcopy(FORM_PAGE))), \
                 patch.object(ps, 'REG', broken), \
                 patch.object(run, 'load', return_value=(jobs, fb)), \
                 patch.object(run, 'out_dir', return_value=directory), \
                 patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
                 patch.object(run, '_run_agent', side_effect=AssertionError('登記表讀不到還派了 agent')), \
                 patch.object(run, 'shoot'), \
                 patch.object(run.agent_report, 'report'), \
                 patch.object(run.agent_report, 'resolve'):
                ok, msg = run.run_one('fill', U, '/tmp/board.html')
        self.assertFalse(ok)
        self.assertIn('平台履歷登記表讀不出來', msg)
        self.assertEqual(fb[U]['apply']['issues'], [msg])                  # 卡上是這個原因,不是「這一輪沒跑完」

    def test_fill_that_hits_the_time_limit_is_wrapped_up_in_the_same_conversation(self):
        # 填完、交接了分頁,卻在寫交件檔前被 40 分鐘上限砍掉:不整輪作廢,叫回同一段對話把結果寫下來
        fb = board()
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        calls = []

        def agent(prompt, log, home, board_path, **kw):
            calls.append(dict(kw, prompt=prompt))
            if len(calls) == 1:
                return SimpleNamespace(ok=False, status='timeout', agent_id='browser-two',
                                       message=lambda: '逾時')
            return SimpleNamespace(ok=True, status='completed', agent_id='browser-two')

        with tempfile.TemporaryDirectory(prefix='apply-wrapup-') as directory, \
             fc.installed(fc.FakeDoor('codex', agent_id='browser-two', page=copy.deepcopy(FORM_PAGE))), \
             loaded(jobs, fb, directory), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(ar, 'session_id', return_value='S9'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], {'tab_id': '7'})), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'):
            ok, _message = run.run_one('fill', U, '/tmp/board.html')

        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        wrap = calls[1]
        self.assertEqual((wrap['resume'], wrap['agent_id']), ('S9', 'browser-two'))
        self.assertLessEqual(wrap['timeout'], 10 * 60)
        self.assertIn('時間到了', wrap['prompt'])
        self.assertIn('不要送出', wrap['prompt'])

    def test_a_fill_handed_to_the_other_vendor_keeps_the_same_prompt_and_records_who_filled(self):
        # #288:填表第一家(Codex)額度用完、換手到 Claude。以前 prompt 照第一家組(交件寫法不同),換手後卡上寫填表卡住。
        # 現在兩家交件同一種寫法(最後一則訊息就是交件單),同一份 prompt;卡上記的是真的填的那一家
        fb = board()
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        codex = fc.FakeDoor('codex', prepared={'tab_id': '5', 'page': {}})
        claude = fc.FakeDoor('claude-code', agent_id='cc')
        got = []

        def agent(prompt, log, home, board_path, **kw):
            got.append((prompt, kw))
            with open(kw['output_last_message'], 'w', encoding='utf-8') as f:   # 換手那一家最後一則訊息存成交件單
                f.write('{"tab_id": "7", "handoff": true, "fields": []}')
            return SimpleNamespace(ok=True, status='completed', agent_id='cc')

        self.d = self.enterContext(tempfile.TemporaryDirectory(prefix='apply-handoff-'))
        with fc.installed(codex, claude), \
             patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('FILL', self.d, None)) as built, \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(ar, 'session_id', return_value='S9'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], {'tab_id': '7'})), \
             patch.object(run, '_profile_check', return_value=[]), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'):
            ok, _message = run.run_one('fill', U, '/tmp/board.html')
        self.assertTrue(ok, _message)
        self.assertEqual(built.call_count, 1)                             # prompt 只組一次,不照換手那一家重組
        (prompt, kw), = got
        self.assertEqual(kw['output_last_message'], os.path.join(self.d, 'fill.json'))
        self.assertNotIn('prepare', kw)
        self.assertEqual(fb[U]['apply']['runtime'], 'claude-code')         # 卡上記的是真的填的那一家

    def test_preview_is_exactly_what_agent_gets(self):
        """看板按鈕底下顯示的 prompt,跟真的派出去的一字不差。"""
        fb = board(); jobs = {U: {'id': U, 'target': 'X'}}
        real = run.load
        run.load = lambda b: (jobs, fb)
        # 每一次交付有自己的交付代號(numbered.material_id);預覽跟派出去的除了代號一字不差
        self.enterContext(patch('numbered.secrets.token_hex', return_value='abc123'))
        try:
            shown = run.preview('fill', U, '/tmp/board.html')
            door = chrome_door.current()
            p, _, _n = run.prompt_for('fill', U, jobs[U], fb, '/tmp/board.html', door=door)
        finally:
            run.load = real
        # 同一份看板底下比才有意義:那句「自己決定派幾隻」是看板上的開關決定的
        fed = ar.prompt_stdin('main', p, browser=ar.apply_overrides(), board='/tmp/board.html', chrome=True)
        self.assertEqual(shown, fed)
        run.load = lambda b: (jobs, board())
        try:
            self.assertEqual(run.preview_meta('fill', '/tmp/board.html'), {'url': U, 'title': 'X'})
            self.assertIsNone(run.preview_meta('submit', '/tmp/board.html'))
            self.assertIn('沒有確認過、可以送出的卡', run.preview('submit', None, '/tmp/board.html'))
        finally:
            run.load = real

    def test_upload_manifest_supplies_card_paths_and_requires_merged(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix='apply-uploads-'))
        merged = os.path.join(directory, 'merged.pdf')
        resume = os.path.join(directory, 'resume.pdf')
        attachment = os.path.join(directory, 'letter.pdf')
        for path in (merged, resume, attachment):
            with open(path, 'wb') as f:
                f.write(b'%PDF test')
        rule = run._upload_rule(directory, {
            'files': ['resume.pdf', 'letter.pdf'],
            'merged': 'merged.pdf',
        })
        self.assertIn('申請頁核准的上傳檔', rule)
        self.assertNotIn('不要上傳合併版', rule)
        self.assertIn(merged, rule)
        self.assertIn(resume, rule)
        self.assertIn(attachment, rule)
        os.unlink(merged)
        self.assertIn('停止並回報', run._upload_rule(directory, {'files': ['resume.pdf'], 'merged': 'merged.pdf'}))

    def test_without_the_same_agent_nothing_is_fixed_or_sent(self):
        """那段對話找不回來:不改、不送,核准作廢(他核准的是那一頁,新的 agent 找不回來)。"""
        fb = board()
        confirm(fb)
        fb[U]['apply'].pop('session')
        real = (run.load, run.bd.set_fb, ar.run)
        seen = {}
        run.load = lambda b: ({U: {'id': U, 'target': 'X'}}, fb)
        run.bd.set_fb = lambda mut, live=None, by='': mut(fb)
        ar.run = lambda *a, **k: seen.setdefault('launched', True)
        try:
            ok, msg = run.run_one('submit', U, '/tmp/board.html')
        finally:
            run.load, run.bd.set_fb, ar.run = real
        self.assertFalse(ok)
        self.assertIn('找不回來', msg)
        self.assertNotIn('launched', seen)
        self.assertNotIn('approve', fb[U])


class SubmitOutcomes(unittest.TestCase):
    def setUp(self):
        self.d = self.enterContext(tempfile.TemporaryDirectory(prefix='apply-outcome-'))
        self.fb = board()
        self.fb[U]['apply'] = {'session': 'S1', 'tab_id': '7', 'runtime': 'codex'}
        confirm(self.fb)
        self.jobs = {U: {'id': U, 'target': 'X'}}
        self.door = fc.FakeDoor('codex', page=copy.deepcopy(FORM_PAGE))
        self._chrome = fc.installed(self.door)
        self._chrome.__enter__()

    def tearDown(self):
        self._chrome.__exit__(None, None, None)

    def test_confirmation_evidence_wins_over_answers_edited_during_send(self):

        original = run.bd.set_fb
        calls = []
        def edit_during_send(mut, live=None, by=''):
            if calls:                                  # 開始送出之後才改(修正 15:正在送出時改答案)
                self.fb['__ans__'][0]['v'] = 'Edited after approval'
            calls.append(1)
            mut(self.fb)
        run.bd.set_fb = edit_during_send
        try:
            with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
                 patch.object(run, 'prompt_for', return_value=('prompt', self.d, None)), \
                 patch.object(run, 'shoot'), \
                 patch.object(run, 'check_submit', return_value=(True, {'confirm_text': 'Application submitted'})), \
                 patch.object(run.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
                 patch.object(run.ar, 'wait_done', return_value=[ar.AgentResult('completed', 0, 123)]), \
                 patch.object(run.ar, 'session_id', return_value='S1'), \
                 patch.object(run.agent_report, 'report') as report, \
                 patch.object(run.agent_report, 'resolve'), \
                 patch.object(run.ship, 'folder', return_value=self.d), \
                 patch.object(run.ship, 'read_info', return_value={}), \
                 patch.object(chrome_door, 'close_if_idle') as closed:
                ok, msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
            self.assertTrue(ok)
            self.assertEqual(self.fb[U]['app'], 'sent')
            self.assertIn('唯一證據', self.fb[U]['ev'])
            sent = self.fb[U]['apply']['sent']
            self.assertIn('Nationality', sent['not_sent_questions'])
            self.assertIn('Nationality', msg)
            self.assertTrue(any('Nationality' in call.args[1] for call in report.call_args_list))
            closed.assert_called_once()                               # 送出成功:那一頁的工作區排入收尾
        finally:
            run.bd.set_fb = original

    def test_sent_version_is_the_one_on_the_page_not_one_switched_to_during_send(self):
        # 送出那幾分鐘他換了履歷、可投遞夾重建:記到成效統計的要是頁面上實際送出去的那一份
        ran = {}
        read_info = lambda _d: {'lang': 'zh', 'variant': 'new'} if ran else {'lang': 'en', 'variant': 'old'}

        def launch(*_a, **_k):
            ran['yes'] = True
            return SimpleNamespace(pid=123)
        with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d, None)), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_submit', return_value=(True, {'confirm_text': 'Application submitted'})), \
             patch.object(run.ar, 'launch', side_effect=launch), \
             patch.object(run.ar, 'wait_done', return_value=[ar.AgentResult('completed', 0, 123)]), \
             patch.object(run.ar, 'session_id', return_value='S1'), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.object(run.ship, 'folder', return_value=self.d), \
             patch.object(run.ship, 'read_info', side_effect=read_info), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)):
            ok, _msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
        self.assertTrue(ok)
        self.assertEqual(self.fb[U]['sent_v'], 'en-old')

    def test_a_send_that_resumed_the_wrong_conversation_says_so(self):
        # 送出那一輪接到的不是填這張的那段對話:照樣擋重送(它可能在哪一頁按了送出),但卡上要講真正的原因,
        # 不是沿用「送出途中停掉了」
        with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d, None)), \
             patch.object(run.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
             patch.object(run.ar, 'wait_done', return_value=[ar.AgentResult('completed', 0, 123)]), \
             patch.object(run.ar, 'session_id', return_value='S-OTHER'), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)):
            ok, _msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
        self.assertFalse(ok)
        sf = self.fb[U]['apply']['submit_fail']
        self.assertFalse(sf.get('cleared'))
        self.assertIn('不是填這張的那段對話', sf['problems'][0])
        self.assertIsNotNone(fr.approval_problem(self.fb, U, OKST))

    def test_a_crash_after_sending_started_leaves_the_send_result_unknown_with_the_real_reason(self):
        # 送出開始後程式自己出錯:可能已經按了送出,卡要停在送出結果不明,原因是這次的錯,不是之後收尾寫的「送出途中停掉了」
        with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d, None)), \
             patch.object(run, '_run_agent', side_effect=RuntimeError('送出執行器斷線')), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)):
            ok, msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
        self.assertFalse(ok)
        self.assertIn('送出執行器斷線', msg)
        run.settle(self.fb, None)
        self.assertEqual(ds.state(self.fb[U]), 'unsure')
        self.assertIn('送出執行器斷線', self.fb[U]['apply']['submit_fail']['problems'][0])

    def test_runner_failure_does_not_read_partial_submit_evidence_or_release_tab(self):
        os.makedirs(self.d, exist_ok=True)
        with open(os.path.join(self.d, 'submit.json'), 'w', encoding='utf-8') as f:
            json.dump({'submitted': True, 'confirm_text': 'partial'}, f)
        with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d, None)), \
             patch.object(run, 'check_submit') as check, \
             patch.object(run.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
             patch.object(run.ar, 'wait_done', return_value=[ar.AgentResult('failed', 7, 123)]), \
             patch.object(run.agent_report, 'report') as report, \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)), \
             patch.object(run.ar, 'session_id', return_value='S1'):
            ok, msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
        self.assertFalse(ok)
        self.assertIn('結束碼 7', msg)
        self.assertEqual(self.fb[U]['app'], 'ship')
        self.assertEqual(self.fb[U]['apply']['submit_fail']['runner_outcome'], 'failed')
        check.assert_not_called()
        report.assert_not_called()          # 送出結果不明不叫他去查:查應徵進度會去查(#389)

    def test_the_checklist_is_run_again_the_moment_sending_starts(self):
        """你已確認 → 正在送出 那一刻再跑一次檢查清單(修正 5):按確認之後答案又改了,就不送、留在你已確認。"""
        launched = []

        def edit_then(mut, live=None, by=''):
            self.fb['__ans__'][0]['v'] = 'Edited after approval'
            mut(self.fb)
        with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d, None)), \
             patch.object(run.ar, 'launch', side_effect=lambda *a, **k: launched.append(1) or SimpleNamespace(pid=1)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.ship, 'folder', return_value=self.d), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run.bd, 'set_fb', side_effect=edit_then):
            ok, msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
        self.assertFalse(ok)
        self.assertIn('要重新確認送出', msg)
        self.assertEqual(launched, [])
        self.assertEqual(ds.state(self.fb[U]), 'confirmed')

    def test_a_confirmation_page_on_another_site_is_not_proof(self):
        """agent 回報送出成功,確認頁網址卻不是這張卡的網站 → 送出結果不明,不當成已送出(修正 19)。"""
        with open(os.path.join(self.d, 'submit.json'), 'w', encoding='utf-8') as f:
            json.dump({'submitted': True, 'reason': '本次申請已收到',
                       'confirm_url': 'https://other-company.example/thanks', 'confirm_text': 'Thanks'}, f)
        with open(os.path.join(self.d, 'submit.png'), 'wb') as f:
            f.write(b'png')
        page = {'url': 'https://other-company.example/thanks', 'lines': ['Thanks'], 'fields': []}
        ok, res = run.check_submit(self.d, 0, 'https://jobs.lever.co/x/1', page)
        self.assertFalse(ok)
        self.assertIn('不是這張卡的網站', res['problems'][-1])
        self.assertTrue(run.check_submit(self.d, 0, 'https://www.other-company.example/job/1', page)[0])
        self.assertEqual(run._site('https://www.104.com.tw/job/1'), '104.com.tw')

    def test_missing_confirmation_evidence_keeps_the_tab_and_marks_submit_failed(self):
        with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d, None)), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_submit', return_value=(False, {'problems': ['no confirmation'], 'clicked': True})), \
             patch.object(run.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
             patch.object(run.ar, 'wait_done', return_value=[ar.AgentResult('completed', 0, 123)]), \
             patch.object(run.ar, 'session_id', return_value='S1'), \
             patch.object(run.agent_report, 'report') as report, \
             patch.object(run.ship, 'folder', return_value=self.d), \
             patch.object(run.ship, 'read_info', return_value={}):
            with patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)):
                ok, _msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
        self.assertFalse(ok)
        self.assertEqual(self.fb[U]['app'], 'ship')
        self.assertTrue(self.fb[U]['apply']['submit_fail']['clicked'])
        report.assert_not_called()          # 送出結果不明不叫他去查:查應徵進度會去查(#389)



def filled(state='confirmed', **apply):
    """一張 agent 填好的卡(頁面還在);預設他也確認過了。"""
    fb = board()
    fb[U]['apply'] = dict({'stage': 'fill', 'issues': [], 'session': 'S1', 'agent_id': 'primary', 'runtime': 'codex',
                           'tab_id': '7', 'at': '2026-01-05T09:00:00', 'delivery': {'method': 'direct_upload'}}, **apply)
    confirm(fb)
    if state != 'confirmed':
        fb[U].pop('approve')
        fb[U]['ds'] = state
    return fb


class RoundInProgress(unittest.TestCase):
    """填/改派出去之前,卡上先寫「這一輪還沒跑完」。中途被按停止(SIGTERM,程式來不及寫)或當掉時,
    卡上不能還是上一輪的「填好了」、還能確認送出:Codex 重填時那一頁已經被重新載入了。"""

    def _stopped(self, stage, fb, chrome=None):
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        chrome = chrome or fc.FakeDoor()
        seen = {}

        def agent(*_a, **_k):
            seen['apply'] = copy.deepcopy(fb[U].get('apply'))
            seen['state'] = ds.state(fb[U])
            seen['problem'] = fr.approval_problem(fb, U, OKST)
            raise KeyboardInterrupt                   # 他按了停止:這之後程式什麼都寫不了

        with tempfile.TemporaryDirectory(prefix='apply-stop-') as directory, \
             loaded(jobs, fb, directory), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             fc.installed(chrome):
            with contextlib.suppress(KeyboardInterrupt):   # 這個情境就是在半路按停
                run.run_one(stage, U, '/tmp/board.html')
        return seen

    def test_a_stopped_refill_does_not_leave_the_old_filled_page_approvable(self):
        fb = filled('stale', stale='履歷換過了')
        seen = self._stopped('fill', fb)
        self.assertEqual(seen['state'], 'running')                # agent 一開始跑,卡上就不是「填好了」
        self.assertIn('沒跑完', seen['apply']['issues'][0])
        self.assertIsNotNone(seen['problem'])
        self.assertNotIn('stale', seen['apply'])                  # 這一輪照現在的履歷填:換履歷的記號由這一輪接手
        self.assertNotIn('session', seen['apply'])                # 舊對話填的那一頁正被重開:下一步是重填,不是叫它改
        self.assertNotIn('tab_id', seen['apply'])                 # 上一輪的分頁不再算停著的頁
        run.settle(fb, None)                                      # 伺服器發現那一輪不在跑了
        self.assertEqual(ds.state(fb[U]), 'nopage')               # 這一輪沒交出分頁:沒填成(修正 13)
        self.assertIn('沒跑完', fb[U]['apply']['issues'][0])
        self.assertIsNotNone(fr.approval_problem(fb, U, OKST))         # 被停掉之後:要重填,不能確認送出

    def test_a_first_fill_that_is_stopped_is_not_left_queued_forever(self):
        # 第一次填被停掉:以前什麼都沒寫,自動流程的記號 fill:<id>:new 已經用掉,卡上卻一直寫「排隊中」
        fb = board()
        seen = self._stopped('fill', fb)
        self.assertEqual(seen['apply']['stage'], 'fill')
        ds.fire(fb, U, 'files_changed', why='履歷換過了')          # 填到一半換履歷,也標得上
        run.settle(fb, None)
        self.assertEqual(ds.state(fb[U]), 'nopage')               # 還沒開到頁就被停掉:沒填成,不是一直排隊

    def test_a_failed_automatic_refill_is_not_dispatched_again(self):
        # 頁面不見了,自動流程重填一次;那一輪 Chrome 沒連上就失敗。以前失敗會把 apply.at 換新,
        # 下一次看又是一把新記號,同一張卡一輪接一輪重派(回報每輪多一筆)
        import autopilot as ap
        fb = filled('gone', tab_id='', issues=[chrome_door.GONE])
        fb['__auto__'] = {'since': '2026-01-01T00:00:00', 'skip': [], 'tried': [], 'seen': {}}
        data = {'jobs': [{'id': U}], 'status': {'schema_version': 2, 'checked_links': True, 'issues': []}}
        cfg = {'auto_fill': True}

        def plan():
            return ap.plan(data, fb, running={}, cfg=cfg)
        first = plan()
        self.assertEqual(first['fill'], U)
        fb['__auto__']['tried'] = first['tried']
        down = fc.FakeDoor(up=(False, 'agent 的 Chrome 沒連上'))
        with patch.object(run, 'load', return_value=({U: {'id': U, 'target': 'X'}}, fb)), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             fc.installed(down):
            ok, _msg = run.run_one('fill', U, '/tmp/board.html')
        self.assertFalse(ok)
        self.assertIn('沒連上', fb[U]['apply']['issues'][0])       # 原因寫在卡上,等他
        self.assertIsNone(plan()['fill'])


class WriteBackKeepsWhatOthersMarked(unittest.TestCase):
    """填/改那一輪跑完寫回 apply 時,不能把這段期間別人記在卡上的東西一起蓋掉:
    他換了履歷(stale)、上次送出結果不明(submit_fail)。蓋掉了卡上就又能確認送出。"""

    def _finish(self, stage, fb, during=None):
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}

        def agent(*_a, **_k):
            if during:
                during(fb)
            return SimpleNamespace(ok=True, status='completed', agent_id='primary')

        res = {'tab_id': '7', 'handoff': True, 'delivery': {'method': 'direct_upload'}}
        with tempfile.TemporaryDirectory(prefix='apply-writeback-') as directory, \
             loaded(jobs, fb, directory), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(ar, 'session_id', return_value='S1' if stage == 'fix' else 'S2'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], res)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             fc.installed(fc.FakeDoor()):
            return run.run_one(stage, U, '/tmp/board.html')

    def test_a_resume_change_during_the_refill_is_not_wiped_by_its_result(self):
        fb = filled('stuck')
        ok, _msg = self._finish('fill', fb, during=lambda d: ds.fire(d, U, 'files_changed', why='履歷換過了'))
        self.assertTrue(ok)
        self.assertEqual(ds.state(fb[U]), 'stale')                 # 網頁上傳的可能還是舊的那份
        self.assertEqual(fb[U]['apply'].get('stale'), '履歷換過了')
        self.assertIn('履歷換過了', fr.approval_problem(fb, U, OKST))

    def test_an_uncertain_submit_survives_a_later_fix(self):
        # 送出沒確認成功(可能其實送出去了),之後自動流程照新答案重打:擋重送的記號不能跟著不見,不然會投兩次
        sf = {'at': T, 'problems': ['沒看到成功頁面'], 'clicked': True}
        fb = filled('unsure', submit_fail=sf)
        fb[U]['form']['f'][1]['refill'] = 1
        ok, msg = self._finish('fix', fb)
        self.assertFalse(ok)                                        # 送出結果不明時不准叫它改
        self.assertIn('送出結果不明', msg)
        self.assertEqual((ds.state(fb[U]), fb[U]['apply'].get('submit_fail')), ('unsure', sf))
        self.assertIn('送出沒確認成功', fr.approval_problem(fb, U, OKST))


class SelfFix(unittest.TestCase):
    """填表核對沒過、agent 改得了的:同一隻 agent 在原頁照問題改一次,不停下來等他按「修改」。"""

    def test_a_correctable_fill_failure_gets_one_fix_round(self):
        rounds = []
        def round_(stage, url, board, dry, status, note):
            rounds.append((stage, note))
            return (False, '頁面上找不到「Nationality」的答案') if stage == 'fill' else (True, '改好了')
        with patch.object(run, '_run_one_round', side_effect=round_), \
             patch.object(run, '_self_fixable', return_value=['頁面上找不到「Nationality」的答案 \'taiwan\'']):
            self.assertEqual(run.run_one('fill', U, '/tmp/b.html'), (True, '改好了'))
        self.assertEqual([r[0] for r in rounds], ['fill', 'fix'])
        self.assertTrue(rounds[1][1].startswith(run.SELF_FIX) and 'Nationality' in rounds[1][1])
        rounds.clear()
        with patch.object(run, '_run_one_round', side_effect=round_), patch.object(run, '_self_fixable', return_value=[]):
            self.assertFalse(run.run_one('fill', U, '/tmp/b.html')[0])
        self.assertEqual([r[0] for r in rounds], ['fill'])                 # 改不了的不自動改

    def test_only_problems_the_agent_can_fix_on_the_page_qualify(self):
        import apply_tab
        from gate_cells import Problem
        # 看問題的種類(程式產生時就標好),不看字:字改了、或別的問題剛好寫成同樣的開頭,判斷都不變
        def card(**apply):
            return {U: {'app': 'ship', 'ds': 'stuck', 'apply': dict({'session': 'S1', 'tab_id': '7'}, **apply)}}
        wrong = Problem('頁面上找不到「Nationality」的答案 \'taiwan\'', 'answer')
        for fb, issues, want in ((card(), [wrong], [wrong]),
                                 (card(), [Problem('答案沒打對', 'answer'), Problem('上傳欄裡少一份', 'upload')], 2),
                                 (card(), [wrong, Problem('卡住:要本人登入', 'stuck')], []),      # agent 自己說卡住
                                 (card(workspace={'handoff_page': 'p2'}), [wrong], []),           # 停在要本人登入的頁
                                 (card(), [wrong, '頁面上找不到「平台履歷」的答案'], []),          # 沒標種類(平台履歷核對等)不自動改
                                 (card(), [apply_tab.HUMAN_CHECK], []),
                                 (card(tab_id=''), [wrong], []),                                  # 頁已經不在
                                 (dict(card(), __inbox__=[{'id': 'r1', 'job': U, 'msg': '要登入', 'agent': True}]), [wrong], [])):
            with patch.object(run, 'load', return_value=({}, fb)):
                got = run._self_fixable('/tmp/b.html', U, issues)
                self.assertEqual(len(got) if isinstance(want, int) else got, want)

    def test_a_wrong_answer_the_page_check_finds_gets_one_fix_round(self):
        # #387:填表核對讀到 Nationality 不是答案(agent 改得了),同一隻 agent 接著在原頁改一次。
        # 問題的種類從 page_problems 一路帶到 run_one 的那一輪結果,中間沒被變回純字
        import apply_tab
        fb = board()
        page = copy.deepcopy(FORM_PAGE)
        page['fields'][1]['value'] = 'Japan'
        res = {'tab_id': '7', 'handoff': True, 'delivery': {'method': 'direct_upload'}}
        rounds = []
        with tempfile.TemporaryDirectory(prefix='apply-selffix-') as d, fc.installed(fc.FakeDoor()), \
             loaded({U: {'id': U, 'target': 'Example · Engineer'}}, fb, d), \
             patch.object(run, 'prompt_for', side_effect=lambda stage, url, job, fb_, board_, note='', **k:
                          rounds.append((stage, note)) or ('prompt', d, None)), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')), \
             patch.object(ar, 'session_id', return_value='S1'), patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', side_effect=lambda fb_, url, *a, **k: (apply_tab.page_problems(page, fb_, url), res)), \
             patch.object(run.agent_report, 'report'):
            run.run_one('fill', U, '/tmp/board.html')
        self.assertEqual([stage for stage, _ in rounds], ['fill', 'fix'])
        self.assertTrue(rounds[1][1].startswith(run.SELF_FIX) and 'Nationality' in rounds[1][1])


class RefillOnlyClosesItsOwnReports(unittest.TestCase):
    """重填、修改只收「代投」自己的回報:客製流程、可投遞夾建置那些不是填表解決的,不能寫成「作廢」「已經解決」。
    送出沒確認成功要他確認過才收;送出前平台履歷比對沒過,重填、改好了就算解決。"""

    def test_login_popup_reports_its_website_and_keeps_the_application_page_for_resume(self):
        from gate_cells import Problem
        fb = board()
        workspace = {'id': 42, 'name': 'jobsalvo-card', 'page': 'p1'}
        fake = fc.FakeDoor(prepared={'workspace': workspace, 'tab_id': '42:p1', 'page': FORM_PAGE})
        action = {'page': 'p2', 'site': 'www.linkedin.com', 'need': '在 www.linkedin.com 完成登入'}
        fake.human_action = lambda: action
        result = {'tab_id': '42:p1', 'handoff': True, 'delivery': {'method': 'direct_upload'}}
        with tempfile.TemporaryDirectory() as directory, fc.installed(fake), \
             loaded({U: {'id': U, 'target': 'Example · Engineer'}}, fb, directory), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')), \
             patch.object(ar, 'session_id', return_value='S2'), patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([Problem('卡住:要本人登入', 'stuck'), Problem('申請表少了「cv.pdf」', 'upload')],
                                                           result)), \
             patch.object(run.agent_report, 'report') as report:
            ok, _ = run.run_one('fill', U, '/tmp/board.html')
        self.assertFalse(ok)
        self.assertEqual(ds.state(fb[U]), 'stuck')
        self.assertEqual(fb[U]['apply']['tab_id'], '42:p1')
        self.assertEqual(fb[U]['apply']['workspace']['page'], 'p1')
        self.assertEqual(fb[U]['apply']['workspace'].get('handoff_page'), 'p2')
        self.assertIn('在 www.linkedin.com 完成登入', '\n'.join(fb[U]['apply']['issues']))
        self.assertIn('卡住:要本人登入', fb[U]['apply']['issues'])
        self.assertNotIn('申請表少了「cv.pdf」', fb[U]['apply']['issues'])      # 還沒登入就還沒開始填:不另外列
        self.assertIn('在 www.linkedin.com 完成登入', report.call_args.kwargs['need'])
        self.assertIn('修改', report.call_args.kwargs['need'])

    def test_other_flows_stay_open(self):
        # 「送出沒確認成功」那類回報現在不會再產生(送出結果不明記在卡上、由查應徵進度查);
        # 那張卡在查清楚之前狀態表也不准重填,所以重填只收自己流程(代投)的
        import agent_report
        fb = filled('stuck')
        for src, msg in (('客製流程', '沒有可收下的客製版'), ('可投遞夾建置', '可投遞夾本輪建置失敗'),
                         ('代投', '送出前平台履歷或附件比對沒通過:附件不符'), ('代投', '填表沒完成:舊的')):
            agent_report.apply_report(fb, src, msg, job=U, now='2026-01-01T00:00:00')
        jobs = {U: {'id': U, 'target': 'X'}}
        res = {'tab_id': '7', 'handoff': True, 'delivery': {'method': 'direct_upload'}}
        with tempfile.TemporaryDirectory(prefix='apply-reports-') as directory, \
             loaded(jobs, fb, directory), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')), \
             patch.object(ar, 'session_id', return_value='S2'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], res)), \
             fc.installed(fc.FakeDoor()):
            self.assertTrue(run.run_one('fill', U, '/tmp/board.html')[0])
        still = sorted(it['msg'] for it in fb['__inbox__'] if not it.get('done'))
        self.assertEqual(still, ['可投遞夾本輪建置失敗', '沒有可收下的客製版'])


class RetypedMarksOnly(unittest.TestCase):
    """改好了才清「雇主網頁待重打」:只清程式核對頁面時就是這個值的那幾欄。
    核對要讀頁、比附件(可到一兩分鐘),這段期間他又改的答案,網頁上還是舊字,標記要留著。"""

    def test_an_answer_changed_while_the_page_is_being_checked_stays_marked(self):
        fb = filled('parked')
        fb[U]['form']['f'][1]['refill'] = 1
        jobs = {U: {'id': U, 'target': 'X'}}
        res = {'tab_id': '7', 'handoff': True, 'delivery': {'method': 'direct_upload'}}

        def checking(*_a, **_k):              # 核對頁面的時候(頁面上是 Taiwan),他在看板又改了一次
            fb['__ans__'][0]['v'] = 'Taiwan (R.O.C.)'
            fb[U]['form']['f'][1]['refill'] = 1
            return [], res
        with tempfile.TemporaryDirectory(prefix='apply-retype-') as directory, \
             patch.object(run, 'load', side_effect=lambda _b: (jobs, copy.deepcopy(fb))), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory, None)), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')), \
             patch.object(ar, 'session_id', return_value='S1'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', side_effect=checking), \
             patch.object(run.fr, 'record_fill', return_value=[]), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             fc.installed(fc.FakeDoor()):
            self.assertTrue(run.run_one('fix', U, '/tmp/board.html')[0])
        self.assertEqual(fb[U]['form']['f'][1].get('refill'), 1)
        confirm(fb)
        self.assertIn('網頁上還是舊的', fr.approval_problem(fb, U, OKST))

    def test_what_the_page_was_checked_against_is_cleared(self):
        fb = filled('parked')
        fb[U]['form']['f'][1]['refill'] = 1
        res = {'tab_id': '7', 'handoff': True, 'delivery': {'method': 'direct_upload'}}
        with tempfile.TemporaryDirectory(prefix='apply-retype-') as directory, \
             patch.object(run, 'load', side_effect=lambda _b: ({U: {'id': U, 'target': 'X'}}, copy.deepcopy(fb))), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory, None)), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')), \
             patch.object(ar, 'session_id', return_value='S1'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], res)), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.fr, 'record_fill', return_value=[]), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             fc.installed(fc.FakeDoor()):
            self.assertTrue(run.run_one('fix', U, '/tmp/board.html')[0])
        self.assertNotIn('refill', fb[U]['form']['f'][1])


class EachCardIsCheckedWhenItsTurnComes(unittest.TestCase):
    """批次跑(修正 18):每一張開始前重新看一次狀態,不在允許的狀態就跳過,不派 agent。"""

    def test_a_card_that_moved_on_is_skipped(self):
        fb = filled('parked')                                # 排進這一批之後,他已經看過、停著等確認
        launched = []
        with patch.object(run, 'load', return_value=({U: {'id': U, 'target': 'X'}}, fb)), \
             patch.object(run, '_run_agent', side_effect=lambda *a, **k: launched.append(1)), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), patch.object(run.agent_report, 'resolve'):
            ok, msg = run.run_one('fill', U, '/tmp/board.html')
        self.assertFalse(ok)
        self.assertIn('停著等你', msg)
        self.assertEqual((launched, ds.state(fb[U])), ([], 'parked'))


class SubmittedWhileFilling(unittest.TestCase):
    """填表那一輪 agent 違規按了送出、頁面已經是已收到申請 → 已送出(來源 agent,附註違規),並回報(修正 20)。"""

    def test_it_is_recorded_as_sent_and_reported(self):
        fb = filled('stuck')
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        res = {'tab_id': '7', 'handoff': True, 'submitted': True, 'submit_claimed': True,
               'confirm_text': 'Application received'}
        with tempfile.TemporaryDirectory(prefix='apply-violation-') as directory, \
             loaded(jobs, fb, directory), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')), \
             patch.object(ar, 'session_id', return_value='S2'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=(['⚠ 填表階段回報「已送出」,要人看'], res)), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run.agent_report, 'report') as report, \
             patch.object(run.agent_report, 'resolve'), \
             fc.installed(fc.FakeDoor()):
            ok, msg = run.run_one('fill', U, '/tmp/board.html')
        self.assertFalse(ok)
        m = fb[U]
        self.assertEqual((ds.state(m), m['app'], m['sent_by']), ('sent', 'sent', 'agent'))
        self.assertIn('違規', m['apply']['sent']['note'])
        self.assertTrue(report.called)

    def test_explicit_unknown_from_check_fill_goes_to_unsure_and_cannot_be_refilled(self):
        fb = filled('stuck')
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        with tempfile.TemporaryDirectory(prefix='apply-unknown-') as directory, loaded(jobs, fb, directory):
            with open(os.path.join(directory, 'fill.json'), 'w') as f:
                json.dump({'submitted': None, 'clicked': False, 'reason': '無法確認本次是否已收到'}, f)
            checked = run.check_fill(fb, U, directory, 0)
            self.assertTrue(checked[1]['submit_claimed'])
            with patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')), \
                 patch.object(ar, 'session_id', return_value='S2'), patch.object(run, 'shoot'), \
                 patch.object(run, 'check_fill', return_value=checked), \
                 patch.object(run.ship, 'read_info', return_value={}), \
                 patch.object(run.agent_report, 'report'), patch.object(run.agent_report, 'resolve'), \
                 fc.installed(fc.FakeDoor()):
                ok, _message = run.run_one('fill', U, '/tmp/board.html')
        self.assertFalse(ok)
        self.assertEqual(ds.state(fb[U]), 'unsure')
        self.assertFalse(ds.allowed(fb[U], 'fill_start'))
        self.assertFalse(ds.allowed(fb[U], 'fix_start'))


class AgentThatNeverStarted(unittest.TestCase):
    """修改、送出固定找開那一頁的那一家(卡上記的)。那一家停用、移除,或卡上沒記是哪一家:一步都沒做,
    不能寫成「送出沒確認成功、去信箱查」,也不能讓他一直按一個永遠派不出去的按鈕;送「填這張的 agent 接不回來」→ 頁面不見了。
    那一家還能用(換成同一家的另一個 agent 也算):舊頁由它繼續改、送出。"""
    CODEX = {'id': 'primary', 'runtime': 'codex', 'model': '', 'effort': 'max', 'browser': True}
    CLAUDE = {'id': 'cc', 'runtime': 'claude-code', 'model': '', 'effort': 'max', 'browser': True}

    def _run(self, stage, fb, agents, outcome=None):
        called = []

        def agent(*_a, **kw):
            called.append(kw)
            return outcome or ar.AgentResult('failed', 1, 42, agent_id=kw.get('agent_id'))
        with tempfile.TemporaryDirectory(prefix='apply-gone-agent-') as directory, \
             fc.installed(fc.FakeDoor('codex', page=copy.deepcopy(FORM_PAGE)),
                          fc.FakeDoor('claude-code', agent_id='cc', page=copy.deepcopy(FORM_PAGE)), agents=agents), \
             patch.object(run, 'load', return_value=({U: {'id': U, 'target': 'X'}}, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory, None)), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report') as report, \
             patch.object(run.agent_report, 'resolve'):
            ok, msg = run.run_one(stage, U, '/tmp/board.html')
        return ok, msg, called, report

    def assert_gone(self, fb, called, why=chrome_door.AGENT_SWAPPED):
        self.assertEqual(called, [])                              # 派都沒派
        a = fb[U]['apply']
        self.assertNotIn('submit_fail', a)                       # 不是「送出結果不明」
        self.assertEqual(a['issues'], [why])
        self.assertEqual(ds.state(fb[U]), 'gone')                # 接不回來了:下一步是重填,不是再按送出
        self.assertNotIn('approve', fb[U])

    def test_submit_with_the_filling_family_disabled_is_not_an_uncertain_submit(self):
        fb = filled()
        ok, _msg, called, report = self._run('submit', fb, [dict(self.CODEX, browser=False), self.CLAUDE])
        self.assertFalse(ok)
        self.assert_gone(fb, called)
        self.assertFalse([c for c in report.call_args_list if '送出沒確認成功' in str(c)])

    def test_fix_with_the_filling_family_removed_says_refill(self):
        fb = filled('parked')
        fb[U]['form']['f'][1]['refill'] = 1
        ok, _msg, called, _report = self._run('fix', fb, [self.CLAUDE])
        self.assertFalse(ok)
        self.assert_gone(fb, called)

    def test_the_same_family_still_usable_keeps_fixing_and_sending_the_old_page(self):
        # 原本填這張的 agent 拿掉了,換成同一家(Codex)的另一個:那段對話還是 Codex 的,由新的那個接回去改、送出
        fb = filled('parked')
        fb[U]['form']['f'][1]['refill'] = 1
        other = dict(self.CODEX, id='codex-2')
        ok, _msg, called, _report = self._run('fix', fb, [dict(self.CLAUDE, browser=False), other])
        self.assertEqual([(c['agent_id'], c['resume']) for c in called], [('codex-2', 'S1')])
        fb = filled()
        self._run('submit', fb, [other])
        self.assertNotEqual(ds.state(fb[U]), 'gone')

    def test_an_agent_switched_to_another_family_in_settings_does_not_take_the_old_page(self):
        # 設定頁改了執行環境、代號沒改:卡上是 Codex 開的頁,不能叫 Claude 用同一個代號接回一段 Codex 的對話
        fb = filled('parked')
        fb[U]['form']['f'][1]['refill'] = 1
        _ok, _msg, called, _report = self._run('fix', fb, [dict(self.CLAUDE, id='primary')])
        self.assert_gone(fb, called)

    def test_a_card_that_never_recorded_its_family_is_not_taken_as_codex(self):
        fb = filled()
        fb[U]['apply'].pop('runtime')
        _ok, _msg, called, _report = self._run('submit', fb, [self.CODEX])
        self.assert_gone(fb, called, chrome_door.BEFORE_UPDATE)      # 更新前填的:不是換掉了(#311 預設 A)

    def test_submit_whose_agent_failed_to_launch_keeps_the_approval_and_no_pending(self):
        fb = filled()
        never = ar.AgentResult('unavailable', reason='all_unavailable', agent_id='primary')   # 行程沒開起來(pid 空)
        ok, msg, called, report = self._run('submit', fb, [self.CODEX], never)
        self.assertFalse(ok)
        self.assertEqual(len(called), 1)
        self.assertNotIn('submit_fail', fb[U]['apply'])          # 一步都沒做:不用他去信箱查
        self.assertIsNone(fr.approval_problem(fb, U, OKST))            # 核准照樣有效,再按一次送出
        self.assertFalse([c for c in report.call_args_list if '送出沒確認成功' in str(c)])


class PreparedPageIsNotReloaded(unittest.TestCase):
    """程式先開好的申請頁,agent 接手時不准重新載入(#229:驗收時同一頁被載入兩次)。"""

    def test_prompt_says_do_not_reload(self):
        step = run.prepared_step({'workspace': {'id': 42, 'name': 'jobsalvo-card', 'page': 'p1'},
                                  'tab_id': '42:p1', 'page': {'title': 'Apply', 'url': 'https://jobs.example/apply',
                                                           'fields': [], 'lines': []}})
        self.assertIn('不要重新載入', step)
        self.assertIn('不要另開分頁', step)
        self.assertIn('taskSpace(42)', step)
        self.assertIn('task.page("p1")', step)
        self.assertIn('42:p1', step)

    def test_shared_prompts_use_ego_without_extension_workarounds(self):
        prompt = run.FILL + run.FIX + run.SUBMIT + run.BATCH_FILL + run.OPEN_STEP
        for obsolete in ('markHandoff', 'cua.', 'getByLabel', 'setChecked', 'curl multipart', '外掛'):
            self.assertNotIn(obsolete, prompt)
        self.assertIn('--fill', run.BATCH_FILL)          # 配好的格子程式自己填(form_plan --fill)
        self.assertIn('--set', run.BATCH_FILL)
        self.assertIn('check', run.BATCH_FILL)

    def test_prompt_tells_the_truth_about_the_old_tabs(self):
        step = run.prepared_step({'workspace': {'id': 42, 'name': 'jobsalvo-card', 'page': 'p1'},
                                  'tab_id': '42:p1', 'page': {}})
        self.assertNotIn('關了', step)
        self.assertIn('其他卡與使用者的分頁不要動', step)


class Blocking(unittest.TestCase):
    """填表沒成功時,擋住它的是職缺或網站的事實才記成 block(職缺關了或換了、要本人登入);程式或 agent 的錯不記(#446)。"""

    def test_only_facts_about_the_job_or_site_block_a_card(self):
        closed = {'posting': {'same_job': False, 'title': '別的職缺'}}
        self.assertEqual(run.blocking(['卡住'], closed, None)['kind'], 'posting')
        self.assertIn('別的職缺', run.blocking(['卡住'], closed, None)['text'])
        self.assertEqual(run.blocking(['要登入'], {}, {'need': '在那一頁登入', 'page': 'https://x'}),
                         {'kind': 'human', 'text': '在那一頁登入'})
        self.assertIsNone(run.blocking(['沒有這一輪的截圖'], {'posting': {'same_job': True}}, None))
        self.assertIsNone(run.blocking([], closed, None))


class StaleFolderIsNotSent(unittest.TestCase):
    """卡上換了履歷、可投遞夾還是舊的那一份:不派 agent,不然它把舊檔傳上去,卡上還寫填好了(#448)。"""

    def test_fill_and_fix_wait_for_the_rebuild(self):
        for stage in ('fill', 'fix'):
            fb = board()
            fb[U]['apply'] = {'session': 'S1', 'agent_id': 'browser-two', 'runtime': 'codex'}
            jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d, \
                    fc.installed(fc.FakeDoor('codex', agent_id='browser-two', page=copy.deepcopy(FORM_PAGE))), \
                    loaded(jobs, fb, d), patch.object(run.agent_report, 'report'), \
                    patch('ship.chosen_not_built', return_value='看板選的是 B 版,可投遞夾裡是 A 版'), \
                    patch.object(ar, 'run') as agent:
                ok, msg = run.run_one(stage, U, '/tmp/board.html')
                self.assertFalse(ok)
                self.assertIn('可投遞夾還沒照卡上的選擇建好', msg)
                agent.assert_not_called()


class TakeUnmet(unittest.TestCase):
    """agent 列的不符(#449):不寄的附件記在卡上(值是原句),要改的檔排一輪自動客製,指示就是那句要求。"""

    def test_drop_and_adapt_become_card_changes(self):
        fb = {U: {'app': 'ship'}}
        info = {'files': ['cv.pdf', 'a.pdf'], 'sources': {'resume:r:zh': 'cv.pdf', 'attachment:att-a': 'a.pdf'}}
        docs = [{'id': 'resume:r:zh', 'kind': 'resume', 'name': '履歷'}, {'id': 'attachment:att-a:zh', 'kind': 'attachment', 'name': 'A'}]
        unmet = [{'quote': '履歷兩頁以內', 'file': 'a.pdf', 'act': 'drop'},
                 {'quote': '履歷兩頁以內', 'file': 'cv.pdf', 'act': 'adapt', 'why': '內文提到附件'}]
        with patch('ship.info', return_value=info), patch('ship.documents', return_value=docs):
            queued = run.take_unmet(fb, U, {'id': U}, unmet)
        self.assertEqual(fb[U]['drop_att'], {'att-a': '履歷兩頁以內'})
        self.assertEqual(queued, ['resume:r:zh'])
        entry = fb[U]['custom_docs']['resume:r:zh']
        self.assertEqual((entry['status'], entry['auto']), ('queued', True))
        self.assertIn('「履歷兩頁以內」', entry['feedbacks'][0]['text'])


class LiveViewIsNotEvidence(unittest.TestCase):
    """👀 看現在的頁面每幾秒截一次:不記成證據的一輪,不然每張卡只留 30 輪,題目附的截圖會被擠掉(#447)。"""

    def test_live_shots_open_no_round_but_reads_do(self):
        import apply_tab
        door = unittest.mock.Mock()
        for cmd, rounds in (('shot', 0), ('read', 1)):
            with self.subTest(cmd=cmd), patch.object(sys, 'argv', ['apply_tab', cmd, '--url', 'https://x/1']), \
                    patch.object(apply_tab, '_lookup', return_value=('t', door)), \
                    patch('evidence.opened') as opened, patch('builtins.print'):
                door.read_page.return_value = {}
                apply_tab.main()
                self.assertEqual(opened.call_count, rounds)


class EvidenceStaysWhileUsed(unittest.TestCase):
    """每張卡只留最近幾輪,但看板還指著的那一輪(題目、卡上附的截圖)不刪;讀不到看板就都不刪(#447)。"""

    def test_prune_keeps_rounds_the_board_points_at(self):
        import evidence
        home = tempfile.mkdtemp()
        names = [f'20260101-0000{i:02d}-000000-apply-fill' for i in range(evidence.KEEP + 3)]
        for n in names:
            os.makedirs(os.path.join(home, n))
        board = '{"ev": {"f": "' + names[1] + '/shot-1.png"}}'
        with patch('board_doc.read_doc', return_value=board):
            evidence._prune(home)
        self.assertEqual(sorted(os.listdir(home)), sorted([names[1]] + names[3:]))
        with patch('board_doc.read_doc', side_effect=OSError('讀不到')), patch('builtins.print'):
            evidence._prune(home)
        self.assertIn(names[1], os.listdir(home))


if __name__ == '__main__':
    unittest.main()


class PauseDoesNotEatTheTimeLimit(unittest.TestCase):
    """⏸ 暫停把整串行程凍住;以前填表時限照牆上時鐘算,暫停得比剩下的時間久,按繼續的那一刻整張就算逾時被砍(#308)。"""

    def test_paused_time_is_not_counted(self):
        sp = self.enterContext(tempfile.TemporaryDirectory(prefix='apply-pause-'))
        status = os.path.join(sp, run.STATUS)
        run.jobrun.write(status, {'phase': 'fill'})          # 這一輪派 agent 前先寫進度(真的流程一定先寫)
        started = time.monotonic()

        class Agent:
            pid = 4321
            returncode = None

            def poll(self):
                elapsed = time.monotonic() - started
                if elapsed > 0.15 and not os.path.exists(status + '.pausedsum'):
                    with open(status + '.pausedsum', 'w') as f:
                        f.write('0.6')                     # 這之間他暫停了 0.6 秒、按了繼續
                if elapsed > 0.5:
                    self.returncode = 0
                return self.returncode

        with patch.object(run, 'SP', sp), patch.object(ar, '_eligible_agents',
                                                       return_value=([{'id': 'a', 'runtime': 'codex'}], '')), \
             patch.object(ar, '_record'), patch.object(ar, '_stop_tree'), \
             patch.object(ar, 'argv_for', return_value=(['/fake/codex'], None)):
            out = run._run_agent('p', os.path.join(sp, 'fill.log'), sp, os.path.join(sp, 'b.html'),
                                 timeout=0.3, launcher=lambda *a, **k: Agent())
        self.assertEqual(out.status, 'completed', '暫停的時間不算進填表時限')

    def test_still_times_out_without_a_pause(self):
        sp = self.enterContext(tempfile.TemporaryDirectory(prefix='apply-pause-'))

        class Agent:
            pid = 4321
            returncode = None

            def poll(self):
                return None

        with patch.object(run, 'SP', sp), patch.object(ar, '_eligible_agents',
                                                       return_value=([{'id': 'a', 'runtime': 'codex'}], '')), \
             patch.object(ar, '_record'), patch.object(ar, '_stop_tree'), \
             patch.object(ar, 'argv_for', return_value=(['/fake/codex'], None)):
            out = run._run_agent('p', os.path.join(sp, 'fill.log'), sp, os.path.join(sp, 'b.html'),
                                 timeout=0.2, launcher=lambda *a, **k: Agent())
        self.assertEqual(out.status, 'timeout')


class CannotReachThePage(unittest.TestCase):
    """修改、送出要叫回開那一頁的那一家/那段對話:叫不回來時卡上怎麼記(以前這兩條沒走過)。"""

    def _run(self, stage, fb, **patches):
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        with patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run, '_run_agent', side_effect=AssertionError('叫不回來就不該派 agent')), \
             patch.object(run.agent_report, 'report'), patch.object(run.agent_report, 'resolve'):
            if patches:
                with patch.object(chrome_door, 'for_card', **patches):
                    return run.run_one(stage, U, '/tmp/board.html')
            return run.run_one(stage, U, '/tmp/board.html')

    def test_family_that_cannot_be_judged_leaves_the_card_alone(self):
        # 設定檔讀不懂:判斷不了那一家還在不在,只講原因,不准因此把卡標成頁面不見了
        fb = filled('parked')
        before = json.loads(json.dumps(fb[U]))
        ok, msg = self._run('fix', fb, side_effect=chrome_door.Unreachable('設定檔讀不懂', sure=False))
        self.assertFalse(ok)
        self.assertIn('設定檔讀不懂', msg)
        self.assertEqual(fb[U], before)

    def test_a_round_still_marked_running_without_its_session_becomes_no_page(self):
        fb = filled('running', session='')
        ok, msg = self._run('fix', fb)
        self.assertFalse(ok)
        self.assertIn('找不回來', msg)
        self.assertEqual(ds.state(fb[U]), 'nopage')
        self.assertIn(run.NO_SESSION, fb[U]['apply']['issues'])


class LateResult(unittest.TestCase):
    """agent 還在填,平台對帳已經找到這張送出了:填表結果晚到,只收進投遞歷史,卡照樣是已送出。"""

    def test_fill_result_after_the_platform_says_it_was_sent(self):
        fb = board()
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}

        def agent(*_a, **_k):
            ds.fire(fb, U, 'platform_found', by='platform', sent_at=T, rec='104:abc:' + T)   # 平台對帳找到了
            return SimpleNamespace(ok=True, status='completed', agent_id='a')
        with tempfile.TemporaryDirectory(prefix='apply-late-') as directory, \
             fc.installed(fc.FakeDoor('claude-code', agent_id='a')), \
             loaded(jobs, fb, directory), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(ar, 'session_id', return_value='S9'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], {'tab_id': '7'})), \
             patch.object(run, '_profile_check', return_value=[]), \
             patch.object(run.agent_report, 'report'), patch.object(run.agent_report, 'resolve'):
            run.run_one('fill', U, '/tmp/board.html')
        self.assertEqual(ds.state(fb[U]), 'sent')
        self.assertIn('fill_ok', [h.get('event') for h in fb[U].get('history') or [] if h.get('late')])
    def test_native_record_errors_repair_once_and_still_block_on_failure(self):
        for errors, expected_ok in (([None], True), ([ValueError('bad source'), None], True),
                                    ([ValueError('bad source'), ValueError('still bad')], False)):
            with self.subTest(errors=len(errors), expected_ok=expected_ok):
                fb = filled('parked')
                jobs = {U: {'id': U, 'target': 'X'}}
                result = {'tab_id': '7', 'handoff': True, 'delivery': {'method': 'direct_upload'}}
                with tempfile.TemporaryDirectory() as directory, loaded(jobs, fb, directory), \
                     fc.installed(fc.FakeDoor()), \
                     patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')) as agent, \
                     patch.object(ar, 'session_id', return_value='S1'), \
                     patch.object(run.fr, 'record_fill', side_effect=errors) as record, \
                     patch.object(run, 'shoot'), \
                     patch.object(run, 'check_fill', return_value=([], result)), \
                     patch.object(run.agent_report, 'report'), patch.object(run.agent_report, 'resolve'):
                    ok, _message = run.run_one('fix', U, '/tmp/board.html')
                    self.assertEqual(ok, expected_ok)
                    self.assertEqual(record.call_count, len(errors))
                    self.assertEqual(agent.call_count, len(errors))
                    self.assertEqual(agent.call_args.kwargs['output_last_message'], os.path.join(directory, 'fill.json'))
                    self.assertEqual(agent.call_args.kwargs['resume'], 'S1')
