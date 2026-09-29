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


def board():
    return {'__ans__': [{'k': 'nat', 'q': '國籍', 'v': 'Taiwan', 'zh': '台灣', 'at': T}],
            U: {'app': 'ship', 'form': {'plat': 'Lever', 'at': T, 'f': [
                {'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
                {'q': 'Nationality', 'src': 'bank', 'k': 'nat'}]}}}



class Approval(unittest.TestCase):
    def test_not_approved_is_refused(self):
        self.assertEqual(fr.approval_problem(board(), U), '還沒確認送出')

    def test_approved_snapshot_is_valid_until_an_answer_changes(self):
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        self.assertIsNone(fr.approval_problem(fb, U))
        fb['__ans__'][0]['v'] = 'ROC'                        # 核准之後他(或我)改了答案
        self.assertIn('要重新確認送出', fr.approval_problem(fb, U))

    def test_pending_or_untranslated_answers_block_submission(self):
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        fb['__ans__'][0]['inf'] = T; del fb['__ans__'][0]['at']
        self.assertIn('等你確認', fr.approval_problem(fb, U))
        fb['__ans__'][0].pop('inf'); fb['__ans__'][0]['at'] = T; fb['__ans__'][0]['tr'] = 1
        self.assertIn('重翻', fr.approval_problem(fb, U))

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
        self.assertEqual(fr.approval_problem(fb, U), '已經送出了')


    def test_an_answer_changed_after_filling_blocks_approval_until_agent_retypes_it(self):
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        fb[U]['form']['f'][1]['refill'] = 1                  # 答案庫改了,網頁上還是舊字
        self.assertIn('先讓 agent 改', fr.approval_problem(fb, U))
        fr.apply_clear_refill(fb, U)                         # agent 在原本那一頁改好了
        self.assertIsNone(fr.approval_problem(fb, U))

    def test_an_unconfirmed_submit_blocks_resending_until_he_clears_it(self):
        fb = board()
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        fb[U]['apply'] = {'submit_fail': {'clicked': True, 'problems': ['沒看到成功頁面']}}
        self.assertIn('先確認到底送出沒有', fr.approval_problem(fb, U))     # 不然可能投兩次
        fb[U]['apply']['submit_fail']['cleared'] = True
        self.assertIsNone(fr.approval_problem(fb, U))


class Eligible(unittest.TestCase):
    def test_fill_takes_unapproved_and_skips_ones_already_filled(self):
        fb = board(); jobs = {U: {'id': U}}
        self.assertEqual(run.eligible(jobs, fb, 'fill'), [U])
        fb[U]['apply'] = {'stage': 'fill', 'ok': True}
        self.assertEqual(run.eligible(jobs, fb, 'fill'), [])            # 填好等他核准的不重填
        self.assertEqual(run.eligible(jobs, fb, 'fill', U), [U])        # 指定那一張才重填
        self.assertEqual(run.eligible(jobs, fb, 'submit'), [])          # 沒核准不會送

    def test_submit_takes_only_valid_approvals(self):
        fb = board(); jobs = {U: {'id': U}}
        fb[U]['approve'] = {'at': T, 'snap': fr.snapshot(fb, U)}
        self.assertEqual(run.eligible(jobs, fb, 'submit'), [U])
        self.assertEqual(run.eligible(jobs, fb, 'fill'), [])

    def test_fix_needs_the_same_agent_and_something_to_fix(self):
        fb = board(); jobs = {U: {'id': U}}
        self.assertEqual(run.eligible(jobs, fb, 'fix', U), [])                 # 沒有 agent 填過的對話:叫不回來
        fb[U]['apply'] = {'stage': 'fill', 'ok': True, 'session': 'S1', 'tab_id': '7'}
        self.assertEqual(run.eligible(jobs, fb, 'fix'), [])                    # 批次:沒有待重打的不跑
        self.assertEqual(run.eligible(jobs, fb, 'fix', U), [U])                # 指定那一張(他寫了話)
        fb[U]['form']['f'][1]['refill'] = 1
        self.assertEqual(run.eligible(jobs, fb, 'fix'), [U])


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
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None)
        outcome = ar.AgentResult('unavailable', reason='pinned_agent_missing', agent_id='browser-two')
        with tempfile.TemporaryDirectory(prefix='apply-agent-pin-') as d, \
             patch.object(run, 'load', return_value=(jobs, fb)), \
             patch.object(run, 'prompt_for', return_value=('prompt', d)), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(fb)), \
             patch.object(run.agent_report, 'report'), \
             patch.object(ar, 'run', side_effect=lambda *args, **kwargs: calls.append(kwargs) or outcome), \
             patch.dict(sys.modules, {'agent_chrome': chrome}):
            self.assertFalse(run.run_one('fix', U, '/tmp/board.html')[0])
            self.assertFalse(run.run_one('submit', U, '/tmp/board.html')[0])

        self.assertEqual([call['agent_id'] for call in calls], ['browser-two', 'browser-two'])
        self.assertEqual([call['resume'] for call in calls], ['S1', 'S1'])
        self.assertTrue(all(call['browser_required'] for call in calls))

    def test_fill_that_hits_the_time_limit_is_wrapped_up_in_the_same_conversation(self):
        # 填完、交接了分頁,卻在寫交件檔前被 40 分鐘上限砍掉:不整輪作廢,叫回同一段對話把結果寫下來
        fb = board()
        jobs = {U: {'id': U, 'target': 'Example · Engineer'}}
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None)
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
        chrome = SimpleNamespace(ensure=lambda *_: (True, ''), open_for_agent=lambda *_a, **_k: None)
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
        resolve.assert_not_called()

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


class PreparedPageIsNotReloaded(unittest.TestCase):
    """程式先開好的申請頁,agent 接手時不准重新載入(#229:驗收時同一頁被載入兩次)。"""

    def test_prompt_says_do_not_reload(self):
        step = run.prepared_step({'tab_id': 'T9', 'page': {'title': 'Apply', 'url': 'https://jobs.example/apply',
                                                            'fields': [], 'lines': []}}, 'INST')
        self.assertIn('不要重新載入', step)
        self.assertIn('不要另開分頁', step)


if __name__ == '__main__':
    unittest.main()
