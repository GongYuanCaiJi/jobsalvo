#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代投(apply_run)的閘門:最後一關一定是他核准,核准後答案一改就作廢,送出要有確認頁證據才算。
這些是程式守的,不靠 agent 自律,所以要測。

跑法(repo 根目錄):python3 -m unittest discover -s tests
"""
import os, sys, json, time, tempfile, shutil, unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import form_record as fr      # noqa: E402
import agent_run as ar        # noqa: E402
import apply_run as run       # noqa: E402

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



class Approval(unittest.TestCase):
    def test_not_approved_is_refused(self):
        self.assertEqual(fr.approval_problem(board(), U, OKST), '還沒確認送出')

    def test_approved_snapshot_is_valid_until_an_answer_changes(self):
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        self.assertIsNone(fr.approval_problem(fb, U, OKST))
        fb['__ans__'][0]['v'] = 'ROC'                        # 核准之後他(或我)改了答案
        self.assertIn('要重新確認送出', fr.approval_problem(fb, U, OKST))

    def test_pending_or_untranslated_answers_block_submission(self):
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        fb['__ans__'][0]['inf'] = T; del fb['__ans__'][0]['at']
        self.assertIn('等你確認', fr.approval_problem(fb, U, OKST))
        fb['__ans__'][0].pop('inf'); fb['__ans__'][0]['at'] = T; fb['__ans__'][0]['tr'] = 1
        self.assertIn('重翻', fr.approval_problem(fb, U, OKST))

    def test_rerecording_the_form_drops_the_approval(self):
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        fr.apply_record(fb, U, 'Lever', [{'q': 'Nationality', 'k': 'nat'}], today=T)
        self.assertNotIn('approve', fb[U])

    def test_mark_sent_locks_and_keeps_the_approval_as_evidence(self):
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        fb[U]['form']['f'][1]['refill'] = 1
        fr.apply_mark_sent(fb, U, {'text': 'Application submitted'}, T, 'en-general')
        m = fb[U]
        self.assertEqual((m['app'], m['form']['lock'], m['sent_v']), ('sent', 1, 'en-general'))
        self.assertNotIn('refill', m['form']['f'][1])
        self.assertIn('approve', m)
        self.assertEqual(fr.approval_problem(fb, U, OKST), '已經送出了')


    def test_an_answer_changed_after_filling_blocks_approval_until_agent_retypes_it(self):
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        fb[U]['form']['f'][1]['refill'] = 1                  # 答案庫改了,網頁上還是舊字
        self.assertIn('先讓 agent 改', fr.approval_problem(fb, U, OKST))
        fr.apply_clear_refill(fb, U)                         # agent 在原本那一頁改好了
        self.assertIsNone(fr.approval_problem(fb, U, OKST))

    def test_an_unconfirmed_submit_blocks_resending_until_he_clears_it(self):
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        fb[U]['apply'] = {'submit_fail': {'clicked': True, 'problems': ['沒看到成功頁面']}}
        self.assertIn('先確認到底送出沒有', fr.approval_problem(fb, U, OKST))     # 不然可能投兩次
        fb[U]['apply']['submit_fail']['cleared'] = True
        self.assertIsNone(fr.approval_problem(fb, U, OKST))


class Eligible(unittest.TestCase):
    def test_fill_takes_unapproved_and_skips_ones_already_filled(self):
        fb = board(); jobs = {U: {'id': U}}
        self.assertEqual(run.eligible(jobs, fb, 'fill', status=OKST), [U])
        fb[U]['apply'] = {'stage': 'fill', 'ok': True}
        self.assertEqual(run.eligible(jobs, fb, 'fill', status=OKST), [])            # 填好等他核准的不重填
        self.assertEqual(run.eligible(jobs, fb, 'fill', U, status=OKST), [U])        # 指定那一張才重填
        self.assertEqual(run.eligible(jobs, fb, 'submit', status=OKST), [])          # 沒核准不會送

    def test_submit_takes_only_valid_approvals(self):
        fb = board(); jobs = {U: {'id': U}}
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        self.assertEqual(run.eligible(jobs, fb, 'submit', status=OKST), [U])
        self.assertEqual(run.eligible(jobs, fb, 'fill', status=OKST), [])

    def test_fix_needs_the_same_agent_and_something_to_fix(self):
        fb = board(); jobs = {U: {'id': U}}
        self.assertEqual(run.eligible(jobs, fb, 'fix', U, status=OKST), [])                 # 沒有 agent 填過的對話:叫不回來
        fb[U]['apply'] = {'stage': 'fill', 'ok': True, 'session': 'S1', 'tab_id': '7'}
        self.assertEqual(run.eligible(jobs, fb, 'fix', status=OKST), [])                    # 批次:沒有待重打的不跑
        self.assertEqual(run.eligible(jobs, fb, 'fix', U, status=OKST), [U])                # 指定那一張(他寫了話)
        fb[U]['form']['f'][1]['refill'] = 1
        self.assertEqual(run.eligible(jobs, fb, 'fix', status=OKST), [U])


class TabRemembersItsChrome(unittest.TestCase):
    """記分頁編號時一起記它是哪一個 agent Chrome 程序開的:agent_chrome.gone_pages 靠它判斷那一頁還在不在
    (以前比最後寫紀錄的時間,改表失敗、送出前擋下都會把時間換新,Chrome 重開過也抓不到)。"""

    def test_fill_record_carries_the_chrome_with_the_tab(self):
        prev = {'tab_id': '5', 'chrome': {'pid': 9, 'start': 1000.0}}
        rec = run.fill_record('fix', prev, {}, ['x'], 'S1', 'fill.png')
        self.assertEqual((rec['tab_id'], rec['chrome']), ('5', prev['chrome']))     # 沿用舊分頁,也沿用它是哪個 Chrome 開的
        rec = run.fill_record('fix', prev, {'tab_id': '7', 'chrome': {'pid': 12, 'start': 2000.0}}, [], 'S1', 'fill.png')
        self.assertEqual((rec['tab_id'], rec['chrome']), ('7', {'pid': 12, 'start': 2000.0}))

    def test_a_filled_tab_is_stamped_with_the_chrome_running_now(self):
        fb = board()
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        now = {'pid': 9, 'start': 1000.0}
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: now)
        with tempfile.TemporaryDirectory(prefix='apply-stamp-') as directory, \
             patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory)), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='a')), \
             patch.object(ar, 'session_id', return_value='S9'), \
             patch.object(ar, 'browser_runtime', return_value='codex'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], {'tab_id': '7'})), \
             patch.object(run, '_profile_check_after_fill', return_value=[]), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            run.run_one('fill', U, '/tmp/board.html')
        self.assertEqual((fb[U]['apply']['tab_id'], fb[U]['apply'].get('chrome')), ('7', now))


class Checks(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='applyrun-')

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, name, obj=None):
        p = os.path.join(self.d, name)
        with open(p, 'w' if obj is not None else 'wb') as f:
            f.write(json.dumps(obj) if obj is not None else b'png')
        return p

    def test_agent_process_gets_its_selected_board_without_changing_parent(self):
        output = self.write('agent.log', {})
        with patch.dict(os.environ, {'AGENT_BOARD': 'outer-board'}), \
                patch.object(ar, 'argv_for', return_value=(['agent'], None)), \
                patch.object(ar.subprocess, 'Popen', return_value='proc') as popen:
            result = ar.launch('prompt', output, 'repo', board='copy-board')
            self.assertEqual(os.environ.get('AGENT_BOARD'), 'outer-board')

        self.assertEqual(result, 'proc')
        self.assertEqual(popen.call_args.kwargs['env']['AGENT_BOARD'], 'copy-board')

    def page(self, **vals):
        return {'url': U + '/apply', 'fields': [{'name': k, 'value': v} for k, v in vals.items()]}

    def test_upload_blocked_by_codex_says_which_file_to_edit(self):
        # Codex 回「could not complete the permission request」:卡上照實講是網站沒被允許、要改哪個檔
        t0 = time.time() - 1
        self.write('fill.png')
        self.write('fill.json', {'fields': [], 'submitted': False, 'tab_id': '7', 'tab_url': U + '/apply', 'handoff': True,
                                 'problems': ['jobs.lever.co: could not complete the permission request to upload files']})
        bad = ' '.join(run.check_fill(board(), U, self.d, t0, 'S1', reader=lambda s, t: self.page())[0])
        self.assertIn('~/.codex/browser/config.toml', bad)

    def test_fill_output_is_checked_against_the_bank(self):
        t0 = time.time() - 1
        fb = board()
        ok = {'fields': [{'q': 'Nationality', 'value': 'Taiwan', 'k': 'nat'}], 'submitted': False,
              'tab_id': '7', 'tab_url': U + '/apply', 'handoff': True, 'uploaded': ['cv.pdf']}
        good = self.page(name='Alex Chen', nat='Taiwan', cv=['cv.pdf'])
        self.write('fill.png')
        self.write('fill.json', ok)
        self.assertEqual(run.check_fill(fb, U, self.d, t0, 'S1', reader=lambda s, t: good)[0], [])
        self.write('fill.json', dict(ok, fields=[{'q': 'Nationality', 'value': 'ROC', 'k': 'nat'}], submitted=True))
        bad = ' '.join(run.check_fill(fb, U, self.d, t0, 'S1', reader=lambda s, t: good)[0])
        self.assertNotIn('答案庫是', bad)          # 頁面上是對的:以頁面為準,agent 回報寫法不同不算錯
        self.assertIn('已送出', bad)
        wrong = self.page(name='Alex Chen', nat='ROC', cv=['cv.pdf'])
        self.assertTrue(run.check_fill(fb, U, self.d, t0, 'S1', reader=lambda s, t: wrong)[0])   # 頁面上真的錯

        def unreadable(_s, _t):
            raise RuntimeError('tab gone')
        bad = ' '.join(run.check_fill(fb, U, self.d, t0, 'S1', reader=unreadable)[0])
        self.assertIn('答案庫是', bad)              # 讀不到頁面時才退回比 agent 回報的值
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
        self.assertEqual(run.check_fill(fb, U, self.d, t0, 'S1', reader=lambda s, t: good)[0], [])

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
            'delivery': {
                'method': 'platform_profile', 'profile_kind': 'custom',
                'profile_url': 'https://example.invalid/profile/custom',
            },
            'uploaded': ['resume.pdf', 'support.pdf'],
        }
        self.write('fill.png')
        self.write('fill.json', report)
        page = self.page(name='Alex Chen', nat='Taiwan')

        problems = run.check_fill(
            fb, U, self.d, t0, 'S1', reader=lambda _session, _tab: page,
        )[0]

        self.assertEqual(problems, [])

    def test_a_page_that_is_another_job_blocks_approval(self):
        """agent 打開申請頁判斷不是這張卡的缺(或關了):不算填好,核准按不下去。"""
        t0 = time.time() - 1
        self.write('fill.png')
        self.write('fill.json', {'fields': [], 'submitted': False, 'tab_id': '7', 'tab_url': U + '/apply', 'handoff': True,
                                 'posting': {'title': 'DevSecOps Engineer', 'same_job': False}})
        self.assertIn('不是這張卡的職缺', ' '.join(run.check_fill(board(), U, self.d, t0)[0]))

    def test_same_job_with_a_stale_card_name_is_renamed_keeping_the_link(self):
        import board_doc as bd
        path = os.path.join(self.d, 'b.html')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(bd.assemble(':root{}', '<b id="stat-first">0</b>', '', {'jobs': [
                {'id': U, 'target': f'Northwind Graduate · Risk Operations Specialist (SQL)（[Lever]({U})）'}]}, '{}', ''))
        run.rename(U, 'Northwind Accelarator Program - Risk Analyst', 'Northwind', path)
        t = bd.parse(bd._read(path))['data']['jobs'][0]['target']
        self.assertEqual(t, f'Northwind Accelarator Program - Risk Analyst（[Lever]({U})）')
        run.rename(U, 'Northwind Accelarator Program - Risk Analyst', 'Northwind', path)     # 已經對了就不動
        self.assertEqual(bd.parse(bd._read(path))['data']['jobs'][0]['target'], t)

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
        bad = ' '.join(run.check_fill(fb, U, self.d, t0, 'S1', reader=lambda s, t: wrong)[0])
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
        # 外掛把 email / tel 欄位讀成空的(藏個資,頁面上其實有值):長得像 email、電話的答案不拿它判錯,其他照抓
        hid = {'url': U + '/apply', 'fields': [{'type': 'email', 'value': ''}, {'type': 'text', 'value': 'Someone'}]}
        fb2 = board()
        fb2[U]['form']['f'].append({'q': 'Email', 'src': 'rz', 'v': 'you@example.com'})
        probs = apply_tab.page_problems(hid, fb2, U)
        self.assertFalse([p for p in probs if 'Email' in p])
        self.assertTrue([p for p in probs if 'Full name' in p])
        # 中文表單填的是中文翻譯,也算對上;下拉選單看顯示的字
        zh = {'url': U + '/apply', 'fields': [{'value': 'Alex Chen'}, {'value': '2', 'shown': '台灣'}]}
        self.assertEqual(apply_tab.page_problems(zh, fb, U), [])
        # Greenhouse:Email 欄是一般文字框,外掛一樣讀成空的;國碼選單只顯示「+44」(答案是 United Kingdom (+44))
        gh = {'url': U + '/apply', 'fields': [
            {'type': 'text', 'label': 'Email', 'value': ''}, {'value': 'Alex Chen'},
            {'type': 'text', 'label': 'Country', 'value': '', 'shown': '+44'}]}
        fb3 = board()
        fb3[U]['form']['f'] = [{'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
                               {'q': 'Email', 'src': 'rz', 'v': 'you@example.com'},
                               {'q': 'Country', 'src': 'rz', 'v': 'United Kingdom (+44)'}]
        self.assertEqual(apply_tab.page_problems(gh, fb3, U), [])
        # 放寬的是「顯示的字是答案的一段」,不是什麼都放:國碼選錯還是抓得到
        gh['fields'][2]['shown'] = '+81'
        self.assertIn('Country', ' '.join(apply_tab.page_problems(gh, fb3, U)))
        gh['fields'][2]['shown'] = '+44'
        # 外掛把電話讀成「<redacted>」:讀不到就不判;但國碼選單的「+44」不能讓電話冒充對上
        gh['fields'].append({'type': 'tel', 'label': 'Phone', 'value': '<redacted>'})
        fb3[U]['form']['f'].append({'q': 'Phone', 'src': 'rz', 'v': '+44 7700 900000'})
        self.assertEqual(apply_tab.page_problems(gh, fb3, U), [])
        gh['fields'][-1] = {'type': 'text', 'label': 'Mobile', 'value': 'Alex Chen'}
        self.assertIn('Phone', ' '.join(apply_tab.page_problems(gh, fb3, U)))
        # 104「選擇履歷」不是表單欄位,選好的值只是一行字:整行一樣才算;選到別份還是抓得到
        fb4 = board()
        fb4[U]['form']['f'] = [{'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
                               {'q': '選擇履歷', 'src': 'rz', 'v': '紅隊｜中文'}]
        p104 = {'url': U + '/apply', 'fields': [{'value': 'Alex Chen'}], 'lines': ['選擇履歷', '紅隊｜中文', '預覽履歷']}
        self.assertEqual(apply_tab.page_problems(p104, fb4, U), [])
        p104['lines'] = ['選擇履歷', '紅隊｜English', '預覽履歷']
        self.assertIn('選擇履歷', ' '.join(apply_tab.page_problems(p104, fb4, U)))
        # 上傳完上傳欄不見了、檔名只用文字顯示(Greenhouse):頁面上看得到檔名也算選上了
        gh2 = {'url': U + '/apply', 'fields': [{'value': 'Alex Chen'}, {'value': 'Taiwan'}], 'shownFiles': ['merged.pdf']}
        self.assertEqual(apply_tab.page_problems(gh2, fb, U, ['merged.pdf']), [])
        self.assertIn('上傳欄裡沒有 other.pdf', ' '.join(apply_tab.page_problems(gh2, fb, U, ['other.pdf'])))

    def test_claude_fill_is_read_through_claude(self):
        """Claude 填的分頁一樣由程式自己讀(接回同一段 Claude 對話),驗收標準跟 Codex 一樣:讀不到就不算填好。"""
        t0 = time.time() - 1
        fb = board()
        self.write('fill.json', {'fields': [], 'submitted': False, 'tab_id': '1234', 'tab_url': U + '/apply',
                                 'handoff': True, 'uploaded': []})
        self.write('fill.png')
        seen = []

        def fake_read(sid, tab, runtime='codex', tries=3, log=None):
            seen.append((sid, tab, runtime))
            raise LookupError('那段對話接不回來')
        with patch('apply_tab.read', fake_read):
            bad = ' '.join(run.check_fill(fb, U, self.d, t0, 'S1', runtime='claude-code')[0])
        self.assertEqual(seen, [('S1', '1234', 'claude-code')])
        self.assertIn('讀不到', bad)

    def test_claude_page_comes_from_its_own_run_log(self):
        """Claude 那一頁:它那一輪最後自己跑唯讀函式,程式從紀錄拿工具的回傳拼回來;
        只收程式碼一字不差的那幾次,模型自己說什麼、改過的程式碼都不算;不完整就是讀不到。"""
        import apply_tab
        page = {'url': U, 'title': 't', 'fields': [{'label': 'Why', 'value': 'x' * 2500}], 'shownFiles': [], 'lines': []}
        whole = json.dumps(page)
        n = -(-len(whole) // apply_tab.CHUNK)
        rows, k = [], 0

        def call(code, result):
            nonlocal k
            k += 1
            rows.append({'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': f'u{k}', 'name': 'mcp__claude-in-chrome__javascript_tool', 'input': {'text': code}}]}})
            rows.append({'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': f'u{k}', 'content': [{'type': 'text', 'text': result + '\n\nTab Context:\n- x'}]}]}})
        call(apply_tab.LEN_JS, str(len(whole)))
        for i in range(n):
            call(apply_tab.chunk_js(i), f'{i}:' + whole[i * apply_tab.CHUNK:(i + 1) * apply_tab.CHUNK])
        call('document.title', '{"fields": ["模型自己寫的"]}')        # 不是那支函式的回傳不收
        rows.append({'type': 'result', 'result': '{"fields": []}'})
        log = os.path.join(self.d, 'fill.log')
        with open(log, 'w') as fh:
            fh.write('\n'.join(json.dumps(r) for r in rows))
        self.assertEqual(apply_tab.read('S1', '7', runtime='claude-code', log=log), page)
        wrap = os.path.join(self.d, 'fill-wrapup.log')                # 逾時收尾那一輪沒再讀:用前一輪的
        with open(wrap, 'w') as fh:
            fh.write(json.dumps({'type': 'result', 'result': 'done'}))
        self.assertEqual(apply_tab.read('S1', '7', runtime='claude-code', log=[log, wrap]), page)
        self.assertGreater(n, 1)                                      # 真的有分段(每段不超過工具的 1000 字上限)
        rows[-5]['message']['content'][0]['input']['text'] = apply_tab.chunk_js(n - 1) + '.trim()'   # 最後一段的程式碼被改過(空白不同不算)
        with open(log, 'w') as fh:
            fh.write('\n'.join(json.dumps(r) for r in rows))
        with self.assertRaises(LookupError):
            apply_tab.read('S1', '7', runtime='claude-code', log=log)
        self.assertIn(apply_tab.LEN_JS, run.ar.apply_rule('claude-code'))    # 規矩裡叫它最後讀一次
        self.assertNotIn(apply_tab.LEN_JS, run.ar.apply_rule('codex'))

    def test_submit_needs_program_screenshot(self):
        t0 = time.time() - 1
        self.write('submit.json', {'submitted': True, 'confirm_text': 'Application submitted'})
        self.assertFalse(run.check_submit(self.d, t0)[0])                     # 不管哪一家:沒有程式截的圖不算

    def test_submit_needs_confirmation_evidence(self):
        t0 = time.time() - 1
        self.write('submit.json', {'submitted': True, 'confirm_text': ''})
        self.write('submit.png')
        self.assertFalse(run.check_submit(self.d, t0)[0])
        self.write('submit.json', {'submitted': True, 'confirm_text': 'Application submitted'})
        self.assertTrue(run.check_submit(self.d, t0)[0])


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
        self.assertNotIn('plugins.chrome@openai-bundled.enabled=false', ar.apply_overrides())

    def test_apply_uses_the_codex_chrome_plugin_and_never_playwright(self):
        ov = ar.apply_overrides()
        self.assertIn('mcp_servers.playwright.enabled=false', ov)
        self.assertIn('mcp_servers.node_repl.enabled=false', ov)       # 它的 Chrome 介面會把分頁群組放進他的視窗
        argv, cwd = ar.argv_for('main', 'P', '/repo', browser=ov, chrome=True)
        self.assertEqual([os.path.basename(argv[0])] + argv[1:2], ['codex', 'exec'])
        self.assertTrue(ar.prompt_stdin('main', 'P', browser=ov, chrome=True).startswith(ar.apply_rule()))   # 換成代投的鐵律
        self.assertIn('extensionInstanceId', ar.apply_rule())         # 只准用 agent 專用的那個 Chrome,用固定身分認
        self.assertIn('computer use', ar.APPLY_RULE)                  # 點螢幕會搶他的畫面,規矩裡禁止
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
        fb[U]['apply'] = {'session': 'S1', 'agent_id': 'browser-two'}
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        jobs = {U: {'id': U, 'target': 'X'}}
        calls = []
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})
        outcome = ar.AgentResult('unavailable', reason='pinned_agent_missing', agent_id='browser-two')
        with tempfile.TemporaryDirectory(prefix='apply-agent-pin-') as d, \
             patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', d)), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(ar, 'browser_runtime', return_value='codex'), \
             patch.object(ar, 'run', side_effect=lambda *args, **kwargs: calls.append(kwargs) or outcome), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            self.assertFalse(run.run_one('fix', U, '/tmp/board.html')[0])
            self.assertEqual(fb[U]['apply']['issues'], [outcome.message()])   # 沒跑成的原因寫在卡上
            fb[U]['apply'] = {'session': 'S1', 'agent_id': 'browser-two'}      # 修改沒成功時本來就不准送;另起一張看送出
            self.assertFalse(run.run_one('submit', U, '/tmp/board.html')[0])

        self.assertEqual([call['agent_id'] for call in calls], ['browser-two', 'browser-two'])
        self.assertEqual([call['resume'] for call in calls], ['S1', 'S1'])
        self.assertTrue(all(call['browser_required'] for call in calls))

    def test_fill_that_hits_the_time_limit_is_wrapped_up_in_the_same_conversation(self):
        # 填完、交接了分頁,卻在寫交件檔前被 40 分鐘上限砍掉:不整輪作廢,叫回同一段對話把結果寫下來
        fb = board()
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})
        calls = []

        def agent(prompt, log, home, board_path, **kw):
            calls.append(dict(kw, prompt=prompt))
            if len(calls) == 1:
                return SimpleNamespace(ok=False, status='timeout', agent_id='browser-two',
                                       message=lambda: '逾時')
            return SimpleNamespace(ok=True, status='completed', agent_id='browser-two')

        with tempfile.TemporaryDirectory(prefix='apply-wrapup-') as directory, \
             patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory)), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(ar, 'session_id', return_value='S9'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], {'tab_id': '7'})), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            ok, _message = run.run_one('fill', U, '/tmp/board.html')

        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        wrap = calls[1]
        self.assertEqual((wrap['resume'], wrap['agent_id']), ('S9', 'browser-two'))
        self.assertLessEqual(wrap['timeout'], 10 * 60)
        self.assertIn('時間到了', wrap['prompt'])
        self.assertIn('不要送出', wrap['prompt'])

    def test_fill_handed_to_the_other_vendor_gets_its_own_prompt_and_chrome_check(self):
        # #288:填表第一家(Codex)額度用完、換手到 Claude:prompt 照 Claude 重組(不帶程式替 Codex 開好的分頁、
        # 加上最後自讀那一步),Chrome 檢查改做 Claude 的(wait_claude)。以前照第一家組的,換手後卡上寫填表卡住
        fb = board()
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        events = []
        up = {'claude': (True, '')}
        chrome = SimpleNamespace(
            ensure=lambda *_: events.append('ensure') or (True, ''),
            wait_claude=lambda *_: events.append('wait_claude') or up['claude'],
            open_for_agent=lambda *_a, **_k: events.append('open') or {'tab_id': '5', 'page': {}},
            conf=lambda: {}, chrome_id=lambda: {})
        got = []

        def prompt_for(stage, url, j, fb_, board_, note='', profile=None, attachment_download_dir=None, prepared=None,
                       runtime='codex'):
            return ('FILL prepared=' + ('yes' if prepared else 'no') + ' for ' + runtime, self.d)

        def agent(prompt, log, home, board_path, **kw):
            got.append(prompt)
            got.append(kw['prepare']({'id': 'primary', 'runtime': 'codex'}))
            got.append(kw['prepare']({'id': 'cc', 'runtime': 'claude-code'}))
            up['claude'] = (False, 'Claude 90 秒內看不到 agent 的 Chrome')
            with self.assertRaises(ar.AgentStartError):
                kw['prepare']({'id': 'cc', 'runtime': 'claude-code'})
            return SimpleNamespace(ok=True, status='completed', agent_id='cc')

        self.d = tempfile.mkdtemp(prefix='apply-handoff-')
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        with patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', side_effect=prompt_for), \
             patch.object(run, 'profile_check', return_value=None), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(ar, 'session_id', return_value='S9'), \
             patch.object(ar, 'browser_runtime', side_effect=lambda agent_id=None: 'claude-code' if agent_id == 'cc' else 'codex'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], {'tab_id': '7'})), \
             patch.object(run, '_profile_check_after_fill', return_value=[]), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            ok, _message = run.run_one('fill', U, '/tmp/board.html')
        self.assertTrue(ok)
        self.assertEqual(got[0], 'FILL prepared=yes for codex')           # 第一家 Codex:程式先開好申請頁
        self.assertEqual(got[1], got[0])                                  # 同一家不重做
        self.assertTrue(got[2].startswith('FILL prepared=no for claude-code'))   # Claude 接不了程式開的分頁,自己開;取檔規則照 Claude 給
        self.assertIn('【填完、改完的最後一步】', got[2])
        self.assertEqual(events, ['ensure', 'open', 'wait_claude', 'wait_claude'])
        self.assertEqual(fb[U]['apply']['runtime'], 'claude-code')         # 卡上記的是真的填的那一家

    def test_fix_rechecks_the_platform_profile_and_keeps_it_blocked_on_a_mismatch(self):
        fb = board()
        delivery = {
            'method': 'platform_profile', 'profile_kind': 'fixed',
            'profile_url': U + '/profile',
        }
        fb[U]['apply'] = {
            'stage': 'fill', 'ok': False, 'session': 'S1', 'agent_id': 'browser-two',
            'tab_id': '7', 'delivery': delivery,
        }
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})
        outcome = SimpleNamespace(ok=True, agent_id='browser-two')
        fix_result = {'delivery': None, 'fixed': ['Summary'], 'tab_id': '7'}

        with tempfile.TemporaryDirectory(prefix='apply-profile-fix-') as directory, \
             patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory)), \
             patch.object(run, '_run_agent', return_value=outcome), \
             patch.object(ar, 'session_id', return_value='S1'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], fix_result)), \
             patch.object(run, 'profile_after', return_value=['Summary still differs']) as verify, \
             patch.object(ar, 'browser_runtime', return_value='codex'), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report') as report, \
             patch.object(run.agent_report, 'resolve') as resolve, \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            ok, message = run.run_one('fix', U, '/tmp/board.html')

        self.assertFalse(ok)
        self.assertIn('Summary still differs', message)
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[1]['delivery'], delivery)
        self.assertFalse(fb[U]['apply']['ok'])
        self.assertEqual(fb[U]['apply']['delivery'], delivery)
        report.assert_called_once()
        # 開跑時收掉這張的舊回報(作廢)是對的;沒改好就不能用「改好了」把問題收掉
        self.assertFalse([c for c in resolve.call_args_list if '已經解決' in str(c.args[1])])
        self.assertTrue([c for c in resolve.call_args_list if '作廢' in str(c.args[1])])   # 重跑時舊的回報收掉,不會越堆越多

    def test_session_id_is_read_from_the_codex_output(self):
        p = os.path.join(tempfile.mkdtemp(prefix='applyrun-'), 'fill.log')
        with open(p, 'w') as f:
            f.write('{"type":"thread.started","thread_id":"01a0c5e6-5a09-7601-b101-5725e37957fd"}\n{"type":"turn.started"}\n')
        self.assertEqual(ar.session_id(p), '01a0c5e6-5a09-7601-b101-5725e37957fd')
        self.assertIsNone(ar.session_id(p + '.missing'))

    def test_preview_is_exactly_what_agent_gets(self):
        """看板按鈕底下顯示的 prompt,跟真的派出去的一字不差。"""
        fb = board(); jobs = {U: {'id': U, 'target': 'X'}}
        real = run.load
        run.load = lambda b: (jobs, fb)
        try:
            shown = run.preview('fill', U, '/tmp/board.html')
            p, _ = run.prompt_for('fill', U, jobs[U], fb, '/tmp/board.html')
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

    def test_fill_prompt_distinguishes_single_and_multiple_upload_fields(self):
        directory = tempfile.mkdtemp(prefix='apply-uploads-')
        try:
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
            self.assertIn('只有一個上傳欄:只上傳合併版', rule)
            self.assertIn('恰好兩個上傳欄、標籤是 Resume 與 Cover Letter', rule)
            self.assertIn('Cover Letter 留空', rule)
            self.assertIn('不可把履歷另存或拼成求職信', rule)
            self.assertIn('除上述兩槽特例外', rule)
            self.assertIn('有兩個以上上傳欄:不要上傳合併版', rule)
            self.assertIn(merged, rule)
            self.assertIn(resume, rule)
            self.assertIn(attachment, rule)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_without_the_same_agent_nothing_is_fixed_or_sent(self):
        """那段對話找不回來:不改、不送,核准作廢(他核准的是那一頁,新的 agent 找不回來)。"""
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
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
        self.d = tempfile.mkdtemp(prefix='apply-outcome-')
        self.fb = board()
        self.fb[U]['apply'] = {'session': 'S1', 'tab_id': '7'}
        self.fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(self.fb, U)}
        self.jobs = {U: {'id': U, 'target': 'X'}}

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_confirmation_evidence_wins_over_answers_edited_during_send(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        original = run.bd.set_fb
        def edit_during_send(mut, live=None, by=''):
            self.fb['__ans__'][0]['v'] = 'Edited after approval'
            mut(self.fb)
        run.bd.set_fb = edit_during_send
        try:
            with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
                 patch.object(run, 'prompt_for', return_value=('prompt', self.d)), \
                 patch.object(run, 'shoot'), \
                 patch.object(run, 'check_submit', return_value=(True, {'confirm_text': 'Application submitted'})), \
                 patch.object(run.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
                 patch.object(run.ar, 'wait_done', return_value=[ar.AgentResult('completed', 0, 123)]), \
                 patch.object(run.ar, 'session_id', return_value='S1'), \
                 patch.object(run.agent_report, 'report') as report, \
                 patch.object(run.agent_report, 'resolve'), \
                 patch.object(run.ship, 'folder', return_value=self.d), \
                 patch.object(run.ship, 'read_info', return_value={}):
                import apply_tab
                with patch.object(apply_tab, 'release') as release, patch('agent_chrome.ensure', return_value=(True, '')):
                    ok, msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
            self.assertTrue(ok)
            self.assertEqual(self.fb[U]['app'], 'sent')
            self.assertIn('唯一證據', self.fb[U]['ev'])
            sent = self.fb[U]['apply']['sent']
            self.assertIn('Nationality', sent['not_sent_questions'])
            self.assertIn('Nationality', msg)
            self.assertTrue(any('Nationality' in call.args[1] for call in report.call_args_list))
            release.assert_called_once_with('S1', '7', 'codex')
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
             patch.object(run, 'prompt_for', return_value=('prompt', self.d)), \
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
            import apply_tab
            with patch.object(apply_tab, 'release'), patch('agent_chrome.ensure', return_value=(True, '')):
                ok, _msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
        self.assertTrue(ok)
        self.assertEqual(self.fb[U]['sent_v'], 'en-old')

    def test_a_send_that_resumed_the_wrong_conversation_says_so(self):
        # 送出那一輪接到的不是填這張的那段對話:照樣擋重送(它可能在哪一頁按了送出),但卡上要講真正的原因,
        # 不是沿用「送出途中停掉了」
        with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d)), \
             patch.object(run.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
             patch.object(run.ar, 'wait_done', return_value=[ar.AgentResult('completed', 0, 123)]), \
             patch.object(run.ar, 'session_id', return_value='S-OTHER'), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)), \
             patch('agent_chrome.ensure', return_value=(True, '')):
            ok, _msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
        self.assertFalse(ok)
        sf = self.fb[U]['apply']['submit_fail']
        self.assertFalse(sf.get('cleared'))
        self.assertIn('不是填這張的那段對話', sf['problems'][0])
        self.assertIsNotNone(fr.approval_problem(self.fb, U, OKST))

    def test_runner_failure_does_not_read_partial_submit_evidence_or_release_tab(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import apply_tab

        os.makedirs(self.d, exist_ok=True)
        with open(os.path.join(self.d, 'submit.json'), 'w', encoding='utf-8') as f:
            json.dump({'submitted': True, 'confirm_text': 'partial'}, f)
        with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d)), \
             patch.object(run, 'check_submit') as check, \
             patch.object(run.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
             patch.object(run.ar, 'wait_done', return_value=[ar.AgentResult('failed', 7, 123)]), \
             patch.object(run.agent_report, 'report') as report, \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)), \
             patch.object(run.ar, 'session_id', return_value='S1'), \
             patch('agent_chrome.ensure', return_value=(True, '')):
            with patch.object(apply_tab, 'release') as release:
                ok, msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
        self.assertFalse(ok)
        self.assertIn('結束碼 7', msg)
        self.assertEqual(self.fb[U]['app'], 'ship')
        self.assertEqual(self.fb[U]['apply']['submit_fail']['runner_outcome'], 'failed')
        check.assert_not_called()
        release.assert_not_called()
        self.assertTrue(report.called)

    def test_missing_confirmation_evidence_keeps_the_tab_and_marks_submit_failed(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import apply_tab

        with patch.object(run, 'load', return_value=(self.jobs, self.fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d)), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_submit', return_value=(False, {'problems': ['no confirmation'], 'clicked': True})), \
             patch.object(run.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
             patch.object(run.ar, 'wait_done', return_value=[ar.AgentResult('completed', 0, 123)]), \
             patch.object(run.ar, 'session_id', return_value='S1'), \
             patch.object(run.agent_report, 'report') as report, \
             patch.object(run.ship, 'folder', return_value=self.d), \
             patch.object(run.ship, 'read_info', return_value={}):
            with patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)), \
                 patch.object(apply_tab, 'release') as release, patch('agent_chrome.ensure', return_value=(True, '')):
                ok, _msg = run.run_one('submit', U, os.path.join(self.d, 'board.html'))
        self.assertFalse(ok)
        self.assertEqual(self.fb[U]['app'], 'ship')
        self.assertTrue(self.fb[U]['apply']['submit_fail']['clicked'])
        release.assert_not_called()
        self.assertTrue(report.called)



def claude_self_read_log(path, reads):
    """假的 Claude stream-json 紀錄:reads 是 [(長度那支, 分段那支, 頁面)],照順序跑過一次完整的自讀。"""
    import apply_tab
    rows, k = [], [0]

    def call(code, result):
        k[0] += 1
        rows.append({'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': f'u{k[0]}', 'name': 'mcp__claude-in-chrome__javascript_tool', 'input': {'text': code}}]}})
        rows.append({'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': f'u{k[0]}', 'content': [{'type': 'text', 'text': result + '\n\nTab Context:\n- x'}]}]}})
    for len_js, chunk, page in reads:
        whole = json.dumps(page)
        call(len_js, str(len(whole)))
        for i in range(-(-len(whole) // apply_tab.CHUNK)):
            call(chunk(i), f'{i}:' + whole[i * apply_tab.CHUNK:(i + 1) * apply_tab.CHUNK])
    rows.append({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'done', 'session_id': 'S1'})
    with open(path, 'w') as fh:
        fh.write('\n'.join(json.dumps(r) for r in rows))
    return path


class ClaudeReadsThePlatformProfile(unittest.TestCase):
    """#288:只用 Claude 時,平台上存好的履歷也能讀回核對。比照填表的自讀:Claude 在那一輪自己跑唯讀函式讀平台履歷頁,
    程式從 stream-json 紀錄拿工具的回傳(只收程式碼一字不差的那幾次)再跟母稿比。以前一律寫「用 Claude 時讀不回,改用 Codex」。"""
    READ = 'https://pda.104.com.tw/profile/preview?vno=1'

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='apply-claude-profile-')
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def profile_page(self, url=None):
        return {'url': url or self.READ, 'title': '我的履歷', 'text': '工作經歷 ' + 'x' * 2500, 'links': ['https://ex.test/p']}

    def test_the_profile_page_comes_from_the_claude_run_log(self):
        import apply_tab
        fill = {'url': U, 'title': 't', 'fields': [], 'shownFiles': [], 'lines': []}
        log = claude_self_read_log(os.path.join(self.d, 'fill.log'), [
            (apply_tab.LEN_JS, apply_tab.chunk_js, fill),
            (apply_tab.PROFILE_LEN_JS, apply_tab.profile_chunk_js, self.profile_page()),
        ])
        self.assertEqual(apply_tab.profile_from_log([log], self.READ), self.profile_page())
        self.assertEqual(apply_tab.page_from_log(log), fill)              # 兩種自讀混在同一份紀錄裡互不干擾
        # 讀的不是程式要核對的那一份(例如這張卡另開的客製版):不算,講出兩個網址
        with self.assertRaises(LookupError) as e:
            apply_tab.profile_from_log([log], 'https://pda.104.com.tw/profile/preview?vno=2')
        self.assertIn('vno=1', str(e.exception))
        rule = run.ar.apply_rule('claude-code')
        self.assertIn(apply_tab.PROFILE_LEN_JS, rule)                      # 規矩裡教它怎麼讀給程式
        self.assertNotIn(apply_tab.PROFILE_LEN_JS, run.ar.apply_rule('codex'))
        self.assertNotIn('\\', apply_tab.PROFILE_LEN_JS)                  # Claude 會改寫跳脫字,程式碼就對不上

    def _fill(self, runtime, agent_log_reads):
        """跑一次填表(外面全換成假的),回 (profile_check 收到的 reader, profile_after 收到的 reader)。"""
        fb = board()
        delivery = {'method': 'platform_profile', 'profile_kind': 'fixed', 'profile_url': self.READ}
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), wait_claude=lambda *_: (True, ''),
                                 open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})
        seen = {}

        def agent(prompt, log, home, board_path, **kw):
            seen['prompt'] = prompt
            claude_self_read_log(log, agent_log_reads)
            return SimpleNamespace(ok=True, status='completed', agent_id='cc')

        def pre(url, board_path, delivery=None, reported=None, reader=None):
            seen['pre_reader'] = reader
            return None

        def after(url, res, board_path=None, reader=None):
            seen['after_reader'] = reader
            return []
        with patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d)), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(ar, 'session_id', return_value='S9'), \
             patch.object(ar, 'browser_runtime', return_value=runtime), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], {'tab_id': '7', 'delivery': delivery})), \
             patch.object(run, 'profile_check', side_effect=pre), \
             patch.object(run, 'profile_after', side_effect=after), \
             patch.object(run, '_profile_check_after_fill', return_value=[]), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            run.run_one('fill', U, '/tmp/board.html')
        return seen

    def test_claude_fill_checks_the_profile_from_its_own_log(self):
        import apply_tab
        seen = self._fill('claude-code', [(apply_tab.PROFILE_LEN_JS, apply_tab.profile_chunk_js, self.profile_page())])
        self.assertIsNotNone(seen['after_reader'])
        self.assertEqual(seen['after_reader'](self.READ), self.profile_page())
        # 填表前程式讀不到(Claude 的分頁只有它那段對話拿得到):照實講由它讀給程式,不叫他改用 Codex
        with self.assertRaises(Exception) as e:
            seen['pre_reader'](self.READ)
        self.assertNotIn('Codex', str(e.exception))
        self.assertIn('讀平台履歷給程式', seen['prompt'])                  # 最後再提醒一次,跟填表頁的自讀一樣
        codex = self._fill('codex', [])
        self.assertIsNone(codex['after_reader'])                          # Codex 照舊由程式自己開頁讀
        self.assertIsNone(codex['pre_reader'])

    def test_claude_submit_rereads_the_profile_in_a_check_round_before_sending(self):
        # 送出前要再核對一次平台履歷。Codex 由程式自己讀;Claude 叫回同一段對話讀一次(送出前的核對那一輪),
        # 程式從那一輪的紀錄拿結果比,對不上就不送。附件用現在的檔核對過就不再下載
        import apply_tab
        fb = board()
        delivery = {'method': 'platform_profile', 'profile_kind': 'fixed', 'profile_url': self.READ}
        fb[U]['apply'] = {'session': 'S1', 'tab_id': '7', 'agent_id': 'cc', 'delivery': delivery}
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        jobs = {U: {'id': U, 'target': 'X'}}
        calls, checked = [], []
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), wait_claude=lambda *_: (True, ''), chrome_id=lambda: {})

        def agent(prompt, log, home, board_path, **kw):
            calls.append((os.path.basename(log), prompt))
            if log.endswith('submit-check.log'):
                claude_self_read_log(log, [(apply_tab.PROFILE_LEN_JS, apply_tab.profile_chunk_js, self.profile_page())])
            return SimpleNamespace(ok=True, status='completed', agent_id='cc')

        def check(url, board_path, delivery=None, reported=None, reader=None):
            self.assertIsNotNone(reader, '用 Claude 時送出前不能叫程式自己開頁讀')
            checked.append(reader(self.READ))
            return ({'read': self.READ, 'edit': self.READ}, [{'where': '經歷', 'missing': ['x']}], '')
        with patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', self.d)), \
             patch.object(run, 'out_dir', return_value=self.d), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(ar, 'session_id', return_value='S1'), \
             patch.object(ar, 'browser_runtime', return_value='claude-code'), \
             patch.object(run, 'profile_check', side_effect=check), \
             patch('profile_sync.profile_attachments_fresh', return_value=True), \
             patch('profile_sync.where', return_value={'read': self.READ, 'edit': self.READ}), \
             patch.object(run, '_pick', return_value=('zh', 'general')), \
             patch('profile_sync.attachment_step', return_value=''), \
             patch.object(run, '_check_delivery_attachments', side_effect=AssertionError('附件核對過了,不用再下載')), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            ok, message = run.run_one('submit', U, '/tmp/board.html')
        self.assertEqual(checked, [self.profile_page()])
        self.assertEqual([c[0] for c in calls], ['submit-check.log'])     # 對不上:送出那一輪沒派
        self.assertIn('讀平台履歷給程式', calls[0][1])
        self.assertIn(self.READ, calls[0][1])
        self.assertFalse(ok)
        self.assertIn('經歷', message)


class TabRead(unittest.TestCase):
    def test_a_retry_hint_from_the_extension_is_retried(self):
        # 外掛偶爾回「Retry the browser command.」:讀一次就放棄,整張填好的表會被判沒填好
        import apply_tab
        calls = []

        class FakeTab:
            def __init__(self, _session, _tab):
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError('Unable to load browser request-header policy. Retry the browser command.')

            def js(self, _code):
                return json.dumps({'url': 'https://example.test/apply', 'fields': []})

            def end_turn(self, keep=()):
                pass

            def close(self):
                pass

        with patch.object(apply_tab, 'Tab', FakeTab), patch.object(time, 'sleep', lambda _s: None):
            self.assertEqual(apply_tab.read('S1', '7')['url'], 'https://example.test/apply')
        self.assertEqual(len(calls), 2)

    def test_the_screenshot_retries_the_same_way(self):
        # 截圖以前沒有重試:碰到同一句暫時錯誤就沒有圖,填好的表被判「沒有這一輪的截圖」
        import apply_tab
        calls = []

        class FakeTab:
            def __init__(self, _session, _tab):
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError('Unable to load browser request-header policy. Retry the browser command.')

            def call(self, _code):
                return '', [b'PNG']

            def end_turn(self, keep=()):
                pass

            def close(self):
                pass

        out = os.path.join(tempfile.mkdtemp(prefix='shot-'), 'fill.png')
        with patch.object(apply_tab, 'Tab', FakeTab), patch.object(time, 'sleep', lambda _s: None):
            apply_tab.shot('S1', '7', out)
        with open(out, 'rb') as f:
            self.assertEqual(f.read(), b'PNG')
        self.assertEqual(len(calls), 2)

    def test_every_extension_call_resends_on_the_retry_hint(self):
        # 放在最底層:開分頁、關分頁這些不經過 read/shot 的呼叫也要重送(修改那一輪收分頁時碰到一次就整輪中斷)
        import apply_tab
        s = apply_tab.Session.__new__(apply_tab.Session)
        s.meta = {}
        replies = [{'result': {'isError': True, 'content': [
                        {'type': 'text', 'text': 'Unable to load browser request-header policy. Retry the browser command.'}]}},
                   {'result': {'content': [{'type': 'text', 'text': 'ok'}]}}]
        with patch.object(s, '_rpc', side_effect=lambda *a, **k: replies.pop(0), create=True), \
                patch.object(time, 'sleep', lambda _s: None):
            self.assertEqual(s.js('1'), 'ok')
        self.assertEqual(replies, [])

    def test_other_errors_are_not_retried(self):
        import apply_tab

        def broken(_session, _tab):
            raise LookupError('拿不到那個分頁')

        with patch.object(apply_tab, 'Tab', broken), self.assertRaises(LookupError):
            apply_tab.read('S1', '7')


def filled(**apply):
    """一張 agent 填好、他也確認過的卡(頁面還在)。"""
    fb = board()
    fb[U]['apply'] = dict({'stage': 'fill', 'ok': True, 'issues': [], 'session': 'S1', 'agent_id': 'primary',
                           'tab_id': '7', 'at': '2026-01-05T09:00:00', 'delivery': {'method': 'direct_upload'}}, **apply)
    fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
    return fb


class RoundInProgress(unittest.TestCase):
    """填/改派出去之前,卡上先寫「這一輪還沒跑完」。中途被按停止(SIGTERM,程式來不及寫)或當掉時,
    卡上不能還是上一輪的「填好了」、還能確認送出:Codex 重填時那一頁已經被重新載入了。"""

    def _stopped(self, stage, fb, chrome=None):
        import copy
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        chrome = chrome or SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})
        seen = {}

        def agent(*_a, **_k):
            seen['apply'] = copy.deepcopy(fb[U].get('apply'))
            seen['problem'] = fr.approval_problem(fb, U, OKST)
            raise KeyboardInterrupt                   # 他按了停止:這之後程式什麼都寫不了

        with tempfile.TemporaryDirectory(prefix='apply-stop-') as directory, \
             patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory)), \
             patch.object(run, 'profile_check', return_value=None), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            try:
                run.run_one(stage, U, '/tmp/board.html')
            except KeyboardInterrupt:
                pass
        return seen

    def test_a_stopped_refill_does_not_leave_the_old_filled_page_approvable(self):
        fb = filled(stale='履歷換過了')
        seen = self._stopped('fill', fb)
        self.assertFalse(seen['apply']['ok'])                     # agent 一開始跑,卡上就不是「填好了」
        self.assertIn('沒跑完', seen['apply']['issues'][0])
        self.assertIsNotNone(seen['problem'])
        self.assertNotIn('stale', seen['apply'])                  # 這一輪照現在的履歷填:換履歷的記號由這一輪接手
        self.assertNotIn('session', seen['apply'])                # 舊對話填的那一頁正被重開:下一步是重填,不是叫它改
        self.assertEqual(seen['apply']['tab_id'], '7')
        self.assertIsNotNone(fr.approval_problem(fb, U, OKST))         # 被停掉之後:要重填,不能確認送出

    def test_a_stopped_fix_keeps_the_resume_mark(self):
        fb = filled(stale='履歷換過了')
        seen = self._stopped('fix', fb)
        self.assertFalse(seen['apply']['ok'])
        self.assertEqual(seen['apply']['stale'], '履歷換過了')     # 修改不換上傳檔,記號留著
        self.assertIsNotNone(fr.approval_problem(fb, U, OKST))

    def test_a_first_fill_that_is_stopped_is_not_left_queued_forever(self):
        # 第一次填被停掉:以前什麼都沒寫,自動流程的記號 fill:<id>:new 已經用掉,卡上卻一直寫「排隊中」
        fb = board()
        seen = self._stopped('fill', fb)
        self.assertEqual(seen['apply']['stage'], 'fill')
        self.assertFalse(fb[U]['apply']['ok'])
        self.assertTrue(fr.mark_stale(fb, U, '履歷換過了'))       # 填到一半換履歷,也標得上

    def test_a_failed_automatic_refill_is_not_dispatched_again(self):
        # 頁面不見了,自動流程重填一次;那一輪 Chrome 沒連上就失敗。以前失敗會把 apply.at 換新,
        # 下一次看又是一把新記號,同一張卡一輪接一輪重派(回報每輪多一筆)
        import autopilot as ap
        import agent_chrome
        fb = filled(ok=False, gone=True, tab_id='', issues=[agent_chrome.GONE])
        del fb[U]['approve']
        fb['__auto__'] = {'since': '2026-01-01T00:00:00', 'skip': [], 'tried': [], 'seen': {}}
        data = {'jobs': [{'id': U}], 'status': {'schema_version': 2, 'checked_links': True, 'issues': []}}
        cfg = {'auto_fill': True}

        def plan():
            return ap.plan(data, fb, verified_gen=0, build_running=False, running={}, cfg=cfg)
        first = plan()
        self.assertEqual(first['fill'], U)
        fb['__auto__']['tried'] = first['tried']
        down = SimpleNamespace(ensure=lambda *_: (False, 'agent 的 Chrome 沒連上'), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})
        with patch.object(run, 'load', return_value=({U: {'id': U, 'target': 'X'}}, fb)), \
             patch.object(run, 'profile_check', return_value=None), \
             patch.object(ar, 'browser_runtime', return_value='codex'), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': down}):
            ok, _msg = run.run_one('fill', U, '/tmp/board.html')
        self.assertFalse(ok)
        self.assertIn('沒連上', fb[U]['apply']['issues'][0])       # 原因寫在卡上,等他
        self.assertIsNone(plan()['fill'])


class WriteBackKeepsWhatOthersMarked(unittest.TestCase):
    """填/改那一輪跑完寫回 apply 時,不能把這段期間別人記在卡上的東西一起蓋掉:
    他換了履歷(stale)、上次送出結果不明(submit_fail)。蓋掉了卡上就又能確認送出。"""

    def _finish(self, stage, fb, during=None):
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})

        def agent(*_a, **_k):
            if during:
                during(fb)
            return SimpleNamespace(ok=True, status='completed', agent_id='primary')

        res = {'tab_id': '7', 'handoff': True, 'delivery': {'method': 'direct_upload'}}
        with tempfile.TemporaryDirectory(prefix='apply-writeback-') as directory, \
             patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory)), \
             patch.object(run, 'profile_check', return_value=None), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(ar, 'session_id', return_value='S1' if stage == 'fix' else 'S2'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], res)), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            return run.run_one(stage, U, '/tmp/board.html')

    def test_a_resume_change_during_the_refill_is_not_wiped_by_its_result(self):
        fb = filled()
        ok, _msg = self._finish('fill', fb, during=lambda d: fr.mark_stale(d, U, '履歷換過了'))
        self.assertTrue(ok)
        self.assertEqual(fb[U]['apply'].get('stale'), '履歷換過了')   # 網頁上傳的可能還是舊的那份
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        self.assertIn('履歷換過了', fr.approval_problem(fb, U, OKST))

    def test_a_fix_does_not_clear_the_resume_mark(self):
        # 修改是在原頁上改,不換上傳檔:換過履歷的記號要等重填才清
        fb = filled(stale='履歷換過了')
        fb[U]['form']['f'][1]['refill'] = 1
        self.assertTrue(self._finish('fix', fb)[0])
        self.assertEqual(fb[U]['apply'].get('stale'), '履歷換過了')

    def test_an_uncertain_submit_survives_a_later_fix(self):
        # 送出沒確認成功(可能其實送出去了),之後自動流程照新答案重打:擋重送的記號不能跟著不見,不然會投兩次
        sf = {'at': T, 'problems': ['沒看到成功頁面'], 'clicked': True}
        fb = filled(submit_fail=sf)
        fb[U]['form']['f'][1]['refill'] = 1
        self.assertTrue(self._finish('fix', fb)[0])
        self.assertEqual(fb[U]['apply'].get('submit_fail'), sf)
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        self.assertIn('送出沒確認成功', fr.approval_problem(fb, U, OKST))

    def test_the_fake_flow_record_keeps_an_uncertain_submit_too(self):
        # 副本的假流程也用 fill_record 組紀錄
        sf = {'at': T, 'problems': ['x']}
        rec = run.fill_record('fix', {'submit_fail': sf, 'session': 'S1'}, {}, [], 'S1', '')
        self.assertEqual(rec.get('submit_fail'), sf)


class RefillOnlyClosesItsOwnReports(unittest.TestCase):
    """重填、修改只收「代投」自己的回報:客製流程、可投遞夾建置那些不是填表解決的,不能寫成「作廢」「已經解決」。
    送出沒確認成功要他確認過才收;送出前平台履歷比對沒過,重填、改好了就算解決。"""

    def test_other_flows_and_uncertain_submits_stay_open(self):
        import agent_report
        fb = filled()
        for src, msg in (('客製流程', '沒有可收下的客製版'), ('可投遞夾建置', '可投遞夾本輪建置失敗'),
                         ('代投', '送出前平台履歷或附件比對沒通過:附件不符'), ('代投', '送出沒確認成功:沒看到成功頁面'),
                         ('代投', '填表沒完成:舊的')):
            agent_report.apply_report(fb, src, msg, job=U, now='2026-01-01T00:00:00')
        jobs = {U: {'id': U, 'target': 'X'}}
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})
        res = {'tab_id': '7', 'handoff': True, 'delivery': {'method': 'direct_upload'}}
        with tempfile.TemporaryDirectory(prefix='apply-reports-') as directory, \
             patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory)), \
             patch.object(run, 'profile_check', return_value=None), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')), \
             patch.object(ar, 'session_id', return_value='S2'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], res)), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            self.assertTrue(run.run_one('fill', U, '/tmp/board.html')[0])
        still = sorted(it['msg'] for it in fb['__inbox__'] if not it.get('done'))
        self.assertEqual(still, ['可投遞夾本輪建置失敗', '沒有可收下的客製版', '送出沒確認成功:沒看到成功頁面'])


class RetypedMarksOnly(unittest.TestCase):
    """改好了才清「雇主網頁待重打」:只清程式核對頁面時就是這個值的那幾欄。
    核對要讀頁、比附件(可到一兩分鐘),這段期間他又改的答案,網頁上還是舊字,標記要留著。"""

    def test_an_answer_changed_while_the_page_is_being_checked_stays_marked(self):
        import copy
        fb = filled()
        fb[U]['form']['f'][1]['refill'] = 1
        jobs = {U: {'id': U, 'target': 'X'}}
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})
        res = {'tab_id': '7', 'handoff': True, 'delivery': {'method': 'direct_upload'}}

        def checking(*_a, **_k):              # 核對頁面的時候(頁面上是 Taiwan),他在看板又改了一次
            fb['__ans__'][0]['v'] = 'Taiwan (R.O.C.)'
            fb[U]['form']['f'][1]['refill'] = 1
            return [], res
        with tempfile.TemporaryDirectory(prefix='apply-retype-') as directory, \
             patch.object(run, 'load', side_effect=lambda _b: (jobs, copy.deepcopy(fb))), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory)), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')), \
             patch.object(ar, 'browser_runtime', return_value='codex'), \
             patch.object(ar, 'session_id', return_value='S1'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', side_effect=checking), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            self.assertTrue(run.run_one('fix', U, '/tmp/board.html')[0])
        self.assertEqual(fb[U]['form']['f'][1].get('refill'), 1)
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        self.assertIn('網頁上還是舊的', fr.approval_problem(fb, U, OKST))

    def test_what_the_page_was_checked_against_is_cleared(self):
        import copy
        fb = filled()
        fb[U]['form']['f'][1]['refill'] = 1
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})
        res = {'tab_id': '7', 'handoff': True, 'delivery': {'method': 'direct_upload'}}
        with tempfile.TemporaryDirectory(prefix='apply-retype-') as directory, \
             patch.object(run, 'load', side_effect=lambda _b: ({U: {'id': U, 'target': 'X'}}, copy.deepcopy(fb))), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory)), \
             patch.object(run, '_run_agent', return_value=SimpleNamespace(ok=True, status='completed', agent_id='primary')), \
             patch.object(ar, 'browser_runtime', return_value='codex'), \
             patch.object(ar, 'session_id', return_value='S1'), \
             patch.object(run, 'shoot'), \
             patch.object(run, 'check_fill', return_value=([], res)), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            self.assertTrue(run.run_one('fix', U, '/tmp/board.html')[0])
        self.assertNotIn('refill', fb[U]['form']['f'][1])


class AgentThatNeverStarted(unittest.TestCase):
    """修改、送出固定叫回填這張的那個 agent。它根本沒啟動(設定裡拿掉了、改成不能開瀏覽器、程式開不起來)
    就是一步都沒做:不能寫成「送出沒確認成功、去信箱查」,也不能讓他一直按一個永遠派不出去的按鈕。"""

    def _run(self, stage, fb, runtime, outcome=None):
        called = []

        def agent(*_a, **_k):
            called.append(1)             # 沒有另外給就照 agent_run 真的會回的:指定的那個不在設定裡
            return outcome or ar.AgentResult('unavailable', reason='pinned_agent_missing')
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None, chrome_id=lambda: {})
        with tempfile.TemporaryDirectory(prefix='apply-gone-agent-') as directory, \
             patch.object(run, 'load', return_value=({U: {'id': U, 'target': 'X'}}, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', directory)), \
             patch.object(ar, 'browser_runtime', return_value=runtime), \
             patch.object(run, '_run_agent', side_effect=agent), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report') as report, \
             patch.object(run.agent_report, 'resolve'), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            ok, msg = run.run_one(stage, U, '/tmp/board.html')
        return ok, msg, called, report

    def test_submit_with_the_filling_agent_removed_is_not_an_uncertain_submit(self):
        fb = filled()
        ok, msg, called, report = self._run('submit', fb, None)
        self.assertFalse(ok)
        self.assertEqual(called, [])                              # 派都沒派
        a = fb[U]['apply']
        self.assertNotIn('submit_fail', a)                       # 不是「送出結果不明」
        self.assertIn('重新填', a['issues'][0])
        self.assertNotIn('session', a)                           # 那段對話叫不回來了:下一步是重填,不是再按送出
        self.assertNotIn('approve', fb[U])
        self.assertFalse([c for c in report.call_args_list if '送出沒確認成功' in str(c)])

    def test_fix_with_the_filling_agent_removed_says_refill(self):
        fb = filled()
        fb[U]['form']['f'][1]['refill'] = 1
        ok, _msg, called, _report = self._run('fix', fb, None)
        self.assertFalse(ok)
        self.assertEqual(called, [])
        self.assertIn('重新填', fb[U]['apply']['issues'][0])
        self.assertNotIn('session', fb[U]['apply'])

    def test_submit_whose_agent_failed_to_launch_keeps_the_approval_and_no_pending(self):
        fb = filled()
        never = ar.AgentResult('unavailable', reason='all_unavailable', agent_id='primary')   # 行程沒開起來(pid 空)
        ok, msg, called, report = self._run('submit', fb, 'codex', never)
        self.assertFalse(ok)
        self.assertEqual(called, [1])
        self.assertNotIn('submit_fail', fb[U]['apply'])          # 一步都沒做:不用他去信箱查
        self.assertIsNone(fr.approval_problem(fb, U, OKST))            # 核准照樣有效,再按一次送出
        self.assertFalse([c for c in report.call_args_list if '送出沒確認成功' in str(c)])


class PreparedPageIsNotReloaded(unittest.TestCase):
    """程式先開好的申請頁,agent 接手時不准重新載入(#229:驗收時同一頁被載入兩次)。"""

    def test_prompt_says_do_not_reload(self):
        step = run.prepared_step({'tab_id': 'T9', 'page': {'title': 'Apply', 'url': 'https://jobs.example/apply',
                                                            'fields': [], 'lines': []}}, 'INST')
        self.assertIn('不要重新載入', step)
        self.assertIn('不要另開分頁', step)

    def test_upload_is_prepared_like_the_download(self):
        # 上傳前 Codex 會先問「允許嗎?」,網站不在允許清單上就卡住;超過 js 預設的 30 秒 REPL 被重置,
        # 申請表那一頁沒交接過就接不回來(下載那條 7bd8cb5 已經這樣修,上傳沒有)
        step2 = run.FILL.split('\n2. ', 1)[1].split('\n3. ', 1)[0]
        self.assertIn('markHandoff', step2)
        self.assertLess(step2.index('markHandoff'), step2.index('filechooser'))    # 上傳之前先交接
        self.assertIn('timeout_ms: 120000', step2)
        self.assertIn('permission request', step2)                                 # 被擋就停下照抄那句話

    def test_prompt_tells_the_truth_about_the_old_tabs(self):
        # 程式是拿這張以前的分頁重新載入(接不回來才另開),舊的沒關;以前跟 agent 說「以前的分頁也關了」
        step = run.prepared_step({'tab_id': 'T9', 'page': {}}, 'INST')
        self.assertNotIn('關了', step)
        self.assertIn('其他分頁不要動', step)


if __name__ == '__main__':
    unittest.main()
