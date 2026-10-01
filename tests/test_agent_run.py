import contextlib
import os
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾(派 agent 的執行紀錄才不會寫進程式資料夾)

import agent_run as ar
import chrome_door
import research


class FakeProcess:
    def __init__(self, pid=123, returncode=None):
        self.pid = pid
        self.returncode = returncode
        self.wait_calls = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.returncode is None and timeout is not None:
            raise ar.subprocess.TimeoutExpired('fake-agent', timeout)
        if self.returncode is None:
            self.returncode = -9
        return self.returncode


class Outcomes(unittest.TestCase):
    def test_completed_and_nonzero_process_results_are_distinct(self):
        done, failed = FakeProcess(returncode=0), FakeProcess(returncode=7)
        results = ar.wait_done([done, failed], timeout=0)
        self.assertEqual([r.status for r in results], ['completed', 'failed'])
        self.assertEqual(results[1].returncode, 7)
        with self.assertRaisesRegex(ar.AgentRunError, '結束碼 7'):
            ar.require_success(results)

    def test_timeout_stops_and_reaps_the_process_tree(self):
        proc = FakeProcess()
        sent = []
        with patch.object(ar.time, 'monotonic', return_value=10), \
             patch.object(ar, '_signal_tree', side_effect=lambda p, sig: sent.append(sig)), \
             patch.object(ar, '_group_alive', return_value=False):
            result = ar.wait_done([proc], timeout=0)[0]
        self.assertEqual(result.status, 'timeout')
        self.assertEqual(sent, [ar.signal.SIGTERM, ar.signal.SIGKILL])
        self.assertEqual(proc.wait_calls, [2, None])
        self.assertEqual(proc.returncode, -9)

    def test_search_does_not_consume_partial_output_after_failure(self):
        with tempfile.TemporaryDirectory(prefix='search-outcome-') as rd:
            def failed_agent(_prompt, outfile, _browser_required):
                with open(outfile[:-4] + '.json', 'w', encoding='utf-8') as f:
                    f.write('[{"url":"https://partial.example/job","title":"Partial"}]')
                return ar.AgentResult('failed', 7, 123)

            with self.assertRaises(ar.AgentRunError):
                research.run_search('wide', '', [], {}, '', rd, 'main', failed_agent)
            self.assertTrue(os.path.exists(os.path.join(rd, 'search_wide.json')))


class BrowserCapability(unittest.TestCase):
    def test_application_agent_keeps_the_cua_plugin_enabled(self):
        agent = {'id': 'browser', 'runtime': 'codex', 'model': '', 'effort': 'low', 'browser': True}
        with tempfile.TemporaryDirectory(prefix='agent-browser-config-') as d:
            config = os.path.join(d, 'config.toml')
            with open(config, 'w', encoding='utf-8') as f:
                f.write('[plugins."unified-computer-use@openai-bundled"]\n'
                        'enabled = true\n'
                        '[plugins."pdf@openai-primary-runtime"]\n'
                        'enabled = true\n')
            with patch.object(ar, 'CODEX_CONFIG', config):
                argv, _ = ar.argv_for(agent, 'TASK', '/repo',
                                      browser=ar.apply_overrides(), chrome=True)

        self.assertNotIn('plugins.unified-computer-use@openai-bundled.enabled=false', argv)
        self.assertIn('plugins.pdf@openai-primary-runtime.enabled=false', argv)

    def test_reading_pages_enables_chrome_without_using_application_rules(self):
        with patch.object(ar, '_runtime', return_value=('codex', '')) as runtime, \
             patch.object(ar, 'lean', return_value=['CHROME-PLUGIN-ENABLED']) as lean:
            argv, cwd = ar.argv_for('main', 'TASK', '/repo', chrome=True)
            prompt = ar.prompt_stdin('main', 'TASK', chrome=True)

        self.assertIsNone(cwd)
        self.assertIn('CHROME-PLUGIN-ENABLED', argv)
        lean.assert_called_once_with(True)
        runtime.assert_called()
        self.assertIn('【抓網頁鐵律】', prompt)
        self.assertNotIn('【瀏覽器鐵律(代投)】', prompt)

class LeanParity(unittest.TestCase):
    def test_codex_and_claude_both_skip_personal_memory(self):
        # 兩個執行者要一致:都不讀使用者的個人記憶(每輪開頭會去翻,跟這份工作無關)
        with patch.object(ar.os.path, 'expanduser', return_value='/nonexistent/config'):
            codex = ar.lean()
            claude = ar.claude_lean()
        self.assertIn('features.memories=false', codex)
        self.assertFalse(json.loads(claude[claude.index('--settings') + 1])['autoMemoryEnabled'])
        self.assertIn('features.hooks=false', codex)
        self.assertTrue(json.loads(claude[claude.index('--settings') + 1])['disableAllHooks'])


    def test_both_hide_the_installed_skills(self):
        # 這台電腦裝的 skill(常是捷徑資料夾)全部不列給 agent:找缺那輪去讀了一份不相干的搜尋 skill。
        # 實測:關之前 agent 看到 110 個,只關一般資料夾剩 13 個(都是捷徑),跟進捷徑後 0 個
        with tempfile.TemporaryDirectory(prefix='skills-') as d:
            real = os.path.join(d, 'elsewhere', 'reach'); os.makedirs(real)
            open(os.path.join(real, 'SKILL.md'), 'w').close()
            home = os.path.join(d, 'agents'); os.makedirs(os.path.join(home, 'plain'))
            open(os.path.join(home, 'plain', 'SKILL.md'), 'w').close()
            os.symlink(real, os.path.join(home, 'reach'))
            os.symlink(home, os.path.join(home, 'loop'))                 # 繞回自己的捷徑也要走得完
            with patch.object(ar, 'CODEX_SKILL_DIRS', (home,)):
                found = ar.codex_skills()
                with patch.object(ar, 'CODEX_CONFIG', os.path.join(d, 'none.toml')):
                    codex = ar.lean()
        self.assertEqual(sorted(os.path.basename(os.path.dirname(p)) for p in found), ['plain', 'reach'])
        cfg = codex[codex.index(next(x for x in codex if x.startswith('skills.config=')))]
        self.assertIn('enabled=false', cfg)
        self.assertIn(os.path.join(home, 'reach', 'SKILL.md'), cfg)
        argv, _ = ar.argv_for({'id': 'c', 'runtime': 'claude-code', 'model': '', 'effort': 'low'}, 'hi', d)
        self.assertIn('--disable-slash-commands', argv)


class CodexBinary(unittest.TestCase):
    """ChatGPT App 更新會把附的 codex 換位置,PATH 上的捷徑就斷了;斷了要去 App 裡找,不是整個派不出 agent。"""

    def test_path_first_then_app_bundle_then_none(self):
        with tempfile.TemporaryDirectory(prefix='codex-bin-') as d:
            app = os.path.join(d, 'codex')
            with open(app, 'w') as f:
                f.write('#!/bin/sh\n')
            os.chmod(app, 0o755)
            self.assertEqual(ar.codex_bin(lambda _n: '/on/path/codex', (app,)), '/on/path/codex')
            self.assertEqual(ar.codex_bin(lambda _n: None, (os.path.join(d, 'gone'), app)), app)
            self.assertIsNone(ar.codex_bin(lambda _n: None, (os.path.join(d, 'gone'),)))


class OrderedDispatch(unittest.TestCase):
    def settings(self, agents):
        return patch.multiple(ar.cf, C={**ar.cf.C, 'agent': {'name': 'Beacon', 'agents': agents}},
                              AGENT='Beacon')

    @staticmethod
    def entry(agent_id, runtime='codex', browser=False, speed='standard'):
        return {'id': agent_id, 'runtime': runtime, 'model': '', 'effort': 'max',
                'browser': browser, 'speed': speed}

    @staticmethod
    def fake_launch(seen, write=None):
        """假的派工:記下派了誰(和是不是接續寫同一份紀錄),write={agent id: 寫進紀錄的字}。"""
        def launch(_prompt, outfile, _repo, agent, **kwargs):
            seen.append((agent['id'], kwargs.get('append', False)))
            with open(outfile, 'a' if kwargs.get('append') else 'w', encoding='utf-8') as f:
                f.write((write or {}).get(agent['id'], ''))
            return FakeProcess(pid=100 + len(seen))
        return launch

    def dispatch(self, entries, waits, launch, prompt='task', **run_kw):
        """照 entries 的設定跑一次 ar.run;waits:每一輪 wait_done 回的(單一個 AgentResult = 每輪都一樣)。
        回 (結果, 紀錄檔文字)。"""
        if isinstance(waits, ar.AgentResult):
            waits = [[waits]] * len(entries)
        with contextlib.ExitStack() as stack:
            d = stack.enter_context(tempfile.TemporaryDirectory(prefix='agent-run-'))
            stack.enter_context(self.settings(entries))
            stack.enter_context(patch.object(ar, 'launch', side_effect=launch))
            stack.enter_context(patch.object(ar, 'wait_done', side_effect=waits))
            log = os.path.join(d, 'task.log')
            result = ar.run(prompt, log, d, **run_kw)
            with open(log, encoding='utf-8') as f:
                return result, f.read()

    def test_known_command_code_unavailability_hands_off_once(self):
        seen = []
        result, text = self.dispatch(
            [self.entry('first', 'command-code'), self.entry('second')],
            [[ar.AgentResult('failed', 10, 101)], [ar.AgentResult('completed', 0, 102)]], self.fake_launch(seen))
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.agent_id, 'second')
        self.assertEqual([agent_id for agent_id, _ in seen], ['first', 'second'])
        self.assertTrue(seen[1][1])  # 第二個 agent 接續寫同一份紀錄
        self.assertEqual(text.count('改用'), 1)
        self.assertIn('Beacon 清單第 2 個', text)
        self.assertIn('額度用完', text)

    def test_browser_task_skips_agents_without_browser_capability(self):
        seen = []
        result, text = self.dispatch(
            [self.entry('text-only', 'command-code'), self.entry('browser-first', browser=True),
             self.entry('browser-next', browser=True)],
            [[ar.AgentResult('failed', 1, 101)], [ar.AgentResult('completed', 0, 102)]],
            self.fake_launch(seen, write={'browser-first': 'quota exceeded'}), browser_required=True)
        self.assertEqual(result.agent_id, 'browser-next')
        self.assertEqual([agent_id for agent_id, _ in seen], ['browser-first', 'browser-next'])
        self.assertIn('Beacon 清單第 3 個', text)

    def test_timeout_and_unknown_failure_do_not_handoff(self):
        for outcome in (ar.AgentResult('timeout', pid=101), ar.AgentResult('failed', 9, 101)):
            with self.subTest(status=outcome.status, returncode=outcome.returncode):
                seen = []
                result, _ = self.dispatch([self.entry('first'), self.entry('second')], outcome,
                                          self.fake_launch(seen))
                self.assertEqual(result.status, outcome.status)
                self.assertEqual([agent_id for agent_id, _ in seen], ['first'])

    def test_previous_attempt_output_does_not_reclassify_the_next_runtime(self):
        seen = []
        result, _ = self.dispatch(
            [self.entry('first', 'command-code'), self.entry('second')],
            [[ar.AgentResult('failed', 10, 101)], [ar.AgentResult('failed', 9, 102)]],
            self.fake_launch(seen, write={'first': 'quota exceeded', 'second': 'task failed'}))
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.agent_id, 'second')
        self.assertEqual([agent_id for agent_id, _ in seen], ['first', 'second'])

    def test_every_attempt_is_timed_in_the_runs_log(self):
        """每派一次 agent 記一行:哪一步、哪個 agent、花幾秒、結果;換手的兩次各一行。"""
        entries = [self.entry('first', 'command-code'), self.entry('second')]

        def launch(_prompt, outfile, _repo, agent, **kwargs):
            with open(outfile, 'a' if kwargs.get('append') else 'w', encoding='utf-8') as f:
                f.write('quota exceeded' if agent['id'] == 'first' else 'done')
            return FakeProcess(pid=200)

        with (
            tempfile.TemporaryDirectory(prefix='agent-runs-log-') as d,
            self.settings(entries),
            patch.object(ar, 'runs_log', return_value=os.path.join(d, 'agent-runs.jsonl')),
            patch.object(ar, 'launch', side_effect=launch),
            patch.object(ar, 'wait_done', side_effect=[
                [ar.AgentResult('failed', 10, 201)], [ar.AgentResult('completed', 0, 202)],
            ]),
        ):
            ar.run('task prompt', os.path.join(d, 'judge_3.out'), d)
            with open(os.path.join(d, 'agent-runs.jsonl'), encoding='utf-8') as f:
                rows = [json.loads(line) for line in f]
        self.assertEqual([(r['agent'], r['try'], r['status'], r['reason']) for r in rows],
                         [('first', 1, 'unavailable', 'quota'), ('second', 2, 'completed', '')])
        self.assertEqual({r['step'] for r in rows}, {'judge_3'})
        self.assertTrue(all(isinstance(r['secs'], float) and r['at'] for r in rows))

    def test_handoff_uses_each_codex_agents_speed_and_logs_it(self):
        entries = [self.entry('quick', speed='fast'), self.entry('standard')]
        commands = []

        def start(argv, **kwargs):
            commands.append(argv)
            if len(commands) == 1:
                kwargs['stdout'].write('quota exceeded')
            return FakeProcess(pid=100 + len(commands))

        with (
            tempfile.TemporaryDirectory(prefix='agent-speed-') as d,
            self.settings(entries),
            patch.object(ar.subprocess, 'Popen', side_effect=start),
            patch.object(ar, 'wait_done', side_effect=[
                [ar.AgentResult('failed', 1, 101)], [ar.AgentResult('completed', 0, 102)],
            ]),
        ):
            log = os.path.join(d, 'task.log')
            result = ar.run('task', log, d)
            with open(log, encoding='utf-8') as f:
                text = f.read()

        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.agent_id, 'standard')
        self.assertIn('service_tier="priority"', commands[0])
        self.assertNotIn('service_tier="priority"', commands[1])
        self.assertIn('快速模式', text)
        self.assertIn('標準模式', text)

    def test_startup_failure_hands_off_and_logs_target_and_reason(self):
        launch = self.fake_launch([])

        def start(_prompt, outfile, repo, agent, **kwargs):
            if agent['id'] == 'first':
                raise ar.AgentStartError('missing executable')
            return launch(_prompt, outfile, repo, agent, **kwargs)

        result, text = self.dispatch([self.entry('first'), self.entry('second')],
                                     ar.AgentResult('completed', 0, 102), start)
        self.assertEqual(result.agent_id, 'second')
        self.assertEqual(text.count('改用'), 1)
        self.assertIn('Beacon 清單第 2 個', text)
        self.assertIn('啟動失敗', text)

    def test_each_agent_gets_the_task_prepared_for_it(self):
        # #288:代投第一家不能用、換手到另一家(Codex↔Claude)時,prompt 和 Chrome 檢查要照新的那一家做。
        # prepare(agent) 回給那一家的 prompt;那一家的 Chrome 沒準備好就丟 AgentStartError,照啟動失敗換下一個
        entries = [self.entry('cx', browser=True), self.entry('cc', 'claude-code', browser=True),
                   self.entry('cx2', browser=True)]
        prompts = []

        def launch(prompt, outfile, _repo, agent, **kwargs):
            prompts.append((agent['id'], prompt))
            with open(outfile, 'a' if kwargs.get('append') else 'w', encoding='utf-8') as f:
                if agent['id'] == 'cx':
                    f.write(json.dumps({'type': 'turn.failed', 'error': {'message': 'Quota exceeded'}}) + '\n')
            return FakeProcess(pid=100 + len(prompts))

        def prepare(agent):
            if agent['id'] == 'cc':
                raise ar.AgentStartError('Claude 90 秒內看不到 agent 的 Chrome')
            return 'TASK FOR ' + agent['id']

        result, text = self.dispatch(entries, ar.AgentResult('completed', 0, 101), launch, prompt='ORIGINAL',
                                     browser_required=True, prepare=prepare)
        self.assertEqual(prompts, [('cx', 'TASK FOR cx'), ('cx2', 'TASK FOR cx2')])
        self.assertEqual(result.agent_id, 'cx2')
        self.assertIn('Claude 90 秒內看不到 agent 的 Chrome', text)      # 為什麼沒用 Claude,紀錄裡講得出來

    def test_pinned_agent_never_falls_through_to_another_entry(self):
        seen = []
        result, _ = self.dispatch([self.entry('primary'), self.entry('secondary')], ar.AgentResult('failed', 1, 101),
                                  self.fake_launch(seen, write={'primary': 'quota exceeded'}), agent_id='primary')
        self.assertEqual(result.status, 'unavailable')
        self.assertEqual([agent_id for agent_id, _ in seen], ['primary'])

    def test_runtime_failure_categories_are_narrow(self):
        for code, reason in ((3, 'authentication'), (5, 'rate_limit'), (6, 'service'),
                             (7, 'service'), (10, 'quota')):
            with self.subTest(code=code):
                self.assertEqual(ar._failure_reason('command-code', code), reason)
        self.assertEqual(ar._failure_reason('codex', 1, 'quota exceeded'), 'quota')
        self.assertEqual(ar._failure_reason('codex', 1, 'rate limit reached'), 'rate_limit')
        self.assertIsNone(ar._failure_reason('codex', 1, 'the task failed'))


class ClaudeCodeRuntime(unittest.TestCase):
    """Claude Code(claude -p)當 agent:找缺、判斷、準備都能用;還不能開瀏覽器、不能接續對話。"""
    entry = staticmethod(OrderedDispatch.entry)
    settings = OrderedDispatch.settings
    dispatch = OrderedDispatch.dispatch

    @staticmethod
    def result_line(**kw):
        row = {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'ok',
               'session_id': '0b1c2d3e-0000-4000-8000-000000000001'}
        row.update(kw)
        return json.dumps(row)

    def test_argv_mirrors_codex_exec(self):
        # 跟 codex exec 一樣:過程即時寫進紀錄、只關使用者自己裝的 MCP/外掛/hooks、權限全開、可以接續對話
        agent = {'id': 'cc', 'runtime': 'claude-code', 'model': 'opus', 'effort': 'high', 'browser': False}
        with tempfile.TemporaryDirectory(prefix='claude-settings-') as d:
            path = os.path.join(d, 'settings.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'enabledPlugins': {'helper@market': True}, 'hooks': {'Stop': []}}, f)
            with patch.object(ar, 'claude_bin', return_value='/bin/claude'), \
                    patch.object(ar, 'CLAUDE_SETTINGS', path):
                argv, cwd = ar.argv_for(agent, 'TASK', '/repo', web=True)
                no_web, _ = ar.argv_for(agent, 'TASK', '/repo', web=False)
                again, _ = ar.argv_for(agent, 'MORE', '/repo', resume='S1')
                fed = ar.prompt_stdin(agent, 'TASK')
        self.assertEqual(argv[:2], ['/bin/claude', '-p'])
        # prompt 有履歷和個資:不放指令參數(ps 看得到、太長會爆),從 stdin 餵
        self.assertFalse(any('TASK' in a for a in argv))
        self.assertTrue(fed.endswith('TASK'))
        self.assertIsNone(ar.prompt_stdin({'runtime': 'command-code'}, 'TASK'))
        # 新對話的 id 由程式先給;接續就指定那一段
        __import__('uuid').UUID(argv[argv.index('--session-id') + 1])   # 是合法的 UUID
        self.assertNotIn('--session-id', again)
        self.assertEqual(argv[argv.index('--output-format') + 1], 'stream-json')
        self.assertIn('--verbose', argv)                       # stream-json 要 --verbose 才會一步一行
        self.assertIn('--dangerously-skip-permissions', argv)
        self.assertIn('--strict-mcp-config', argv)
        self.assertNotIn('--safe-mode', argv)                  # 會連 skills、CLAUDE.md 都關,範圍比 codex 大
        off = json.loads(argv[argv.index('--settings') + 1])
        # 個人記憶跟 codex 那邊一樣關:它每輪開頭會去翻,跟這份工作無關
        self.assertEqual(off, {'disableAllHooks': True, 'autoMemoryEnabled': False,
                               'enabledPlugins': {'helper@market': False}})
        self.assertEqual(argv[argv.index('--model') + 1], 'opus')
        self.assertEqual(argv[argv.index('--effort') + 1], 'high')
        self.assertEqual(no_web[no_web.index('--disallowedTools') + 1], 'WebSearch,WebFetch')
        self.assertNotIn('--disallowedTools', argv)
        self.assertEqual(again[again.index('--resume') + 1], 'S1')
        self.assertEqual(cwd, '/repo')
        # 代投:Claude in Chrome(--chrome),Chrome 動作另外要允許規則;codex 的外掛開關(browser)對它沒意義
        with patch.object(ar, 'claude_paired_device', return_value='dev-agent'):
            browse, _ = ar.argv_for(agent, 'TASK', '/repo', chrome=True, browser=['x'])
            rules = ar.prompt_stdin(agent, 'TASK', browser=['x'], chrome=True)
        self.assertIn('--chrome', browse)
        self.assertNotIn('x', browse)
        self.assertIn('--no-chrome', argv)                      # 不用瀏覽器的工作不載入 Chrome 工具
        allow = json.loads(browse[browse.index('--settings') + 1])['permissions']['allow']
        self.assertEqual(allow, ['mcp__claude-in-chrome', 'ClaudeInChromeDomain(*)'])
        self.assertIn('dev-agent', rules)                       # 只准用配對過的那個(agent 專用的 Chrome)
        self.assertIn('markHandoff()', rules)                   # 指示裡 Codex 的說法有對照表
        self.assertIn('file_upload', rules)

    def test_launch_feeds_the_prompt_through_stdin(self):
        agent = {'id': 'cc', 'runtime': 'claude-code', 'model': '', 'effort': 'max', 'browser': False}
        got = {}

        def popen(argv, **kw):
            got['argv'], got['stdin'] = argv, kw['stdin'].read().decode('utf-8')
            return FakeProcess()

        with tempfile.TemporaryDirectory(prefix='claude-stdin-') as d, \
                patch.object(ar, 'claude_bin', return_value='/bin/claude'), \
                patch.object(ar.subprocess, 'Popen', side_effect=popen):
            ar.launch('SECRET TASK', os.path.join(d, 'log'), d, agent)
        self.assertTrue(got['stdin'].endswith('SECRET TASK'))
        self.assertFalse(any('SECRET' in a for a in got['argv']))

    def test_exit_zero_with_an_error_result_is_a_failure_and_hands_off(self):
        # claude -p 撞到用量上限時結束碼可能還是 0,錯誤寫在最後那個 JSON 裡:不能只看結束碼
        entries = [self.entry('cc', 'claude-code'), self.entry('cx')]
        seen = []

        def launch(_prompt, outfile, _repo, agent, **kwargs):
            seen.append(agent['id'])
            with open(outfile, 'a' if kwargs.get('append') else 'w', encoding='utf-8') as f:
                if agent['id'] == 'cc':
                    f.write(self.result_line(subtype='success', is_error=True,
                                             result="You've hit your session limit · resets 3pm") + '\n')
            return FakeProcess(pid=100 + len(seen))

        result, _ = self.dispatch(entries, ar.AgentResult('completed', 0, 101), launch)
        self.assertEqual(seen, ['cc', 'cx'])
        self.assertEqual(result.agent_id, 'cx')

    def test_clean_result_counts_as_completed(self):
        entries = [self.entry('cc', 'claude-code')]

        def launch(_prompt, outfile, _repo, agent, **kwargs):
            with open(outfile, 'w', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'system', 'subtype': 'init',
                                    'session_id': '0b1c2d3e-0000-4000-8000-000000000001'}) + '\n')
                f.write(json.dumps({'type': 'assistant', 'message': {'content': []}}) + '\n')
                f.write(self.result_line() + '\n')
            return FakeProcess(pid=101)

        with (
            tempfile.TemporaryDirectory(prefix='agent-cc-') as d,
            self.settings(entries),
            patch.object(ar, 'launch', side_effect=launch),
            patch.object(ar, 'wait_done', return_value=[ar.AgentResult('completed', 0, 101)]),
        ):
            log = os.path.join(d, 'task.log')
            self.assertEqual(ar.run('task', log, d).status, 'completed')
            self.assertEqual(ar.session_id(log), '0b1c2d3e-0000-4000-8000-000000000001')

    def test_failure_reasons_come_from_the_result(self):
        cases = (
            (self.result_line(is_error=True, result='Not logged in · Please run /login'), 'authentication'),
            (self.result_line(is_error=True, api_error_status=401, result='x'), 'authentication'),
            (self.result_line(is_error=True, result="You've hit your weekly limit"), 'quota'),
            (self.result_line(is_error=True, api_error_status=429, result='x'), 'rate_limit'),
            (self.result_line(is_error=True, api_error_status=529, result='Overloaded'), 'service'),
            (self.result_line(subtype='error_max_turns', result=''), None),
            ('Error: something odd', None),
        )
        for text, reason in cases:
            with self.subTest(text=text[:60]):
                self.assertEqual(ar._failure_reason('claude-code', 1, text), reason)

    def test_claude_code_with_chrome_can_take_browser_work(self):
        # 代投:勾了「可使用 Chrome」的 Claude Code 也接得了要開瀏覽器的工作(Claude in Chrome)
        seen = []

        def launch(_prompt, outfile, _repo, agent, **kwargs):
            seen.append((agent['id'], kwargs.get('chrome')))
            open(outfile, 'a' if kwargs.get('append') else 'w').close()
            return FakeProcess(pid=100 + len(seen))

        with (
            tempfile.TemporaryDirectory(prefix='agent-claude-browser-') as d,
            self.settings([self.entry('cc', 'claude-code', browser=True)]),
            patch.object(ar, 'launch', side_effect=launch),
            patch.object(ar, 'wait_done', return_value=[ar.AgentResult('completed', 0, 101)]),
            patch.object(ar, '_claude_outcome', return_value={'is_error': False, 'subtype': 'success'}),
        ):
            self.assertTrue(ar.run('task', os.path.join(d, 't.log'), d, browser_required=True).ok)
            self.assertEqual(chrome_door.current().runtime, 'claude-code')
        self.assertEqual(seen, [('cc', True)])
        with self.settings([self.entry('cm', 'command-code', browser=True)]):
            self.assertIsNone(chrome_door.current())               # Command Code 還是不能開瀏覽器

    def test_search_and_add_never_get_chrome_even_when_the_first_agent_can_browse(self):
        # #287:找缺、加職缺不給操作 Chrome 的能力(Codex、Claude 都一樣);網頁由程式的 page_fetch 抓。
        # 以前能開瀏覽器的 agent 排前面、開著 Chrome 做,沒有代投那把鎖,也沒限制只准用 agent 專用的 Chrome
        import add_job
        seen = []

        def launch(_prompt, outfile, _repo, agent, **kwargs):
            seen.append((agent['id'], kwargs.get('chrome'), kwargs.get('browser')))
            open(outfile, 'a' if kwargs.get('append') else 'w').close()
            return FakeProcess(pid=100 + len(seen))

        for entries in ([self.entry('cx', browser=True), self.entry('cc', 'claude-code')],
                        [self.entry('cc', 'claude-code', browser=True)]):
            seen.clear()
            with (
                tempfile.TemporaryDirectory(prefix='agent-search-') as d,
                self.settings(entries),
                patch.object(ar, 'launch', side_effect=launch),
                patch.object(ar, 'wait_done', return_value=[ar.AgentResult('completed', 0, 101)]),
                patch.object(ar, '_claude_outcome', return_value={'is_error': False, 'subtype': 'success'}),
            ):
                self.assertTrue(research.web_agent(d)('task', os.path.join(d, 's.log'), True).ok)
                self.assertIs(add_job.web_agent, research.web_agent)      # 加職缺跟找缺同一支
            self.assertEqual(seen, [(entries[0]['id'], False, None)])


class NoChromeOutsideApply(unittest.TestCase):
    """#287:不是代投的工作派出去時,兩家都真的沒有操作 Chrome 的工具(不只是 prompt 叫它別用)。"""

    def test_codex_without_chrome_turns_off_the_browser_plugins_even_if_config_never_mentions_them(self):
        agent = {'id': 'cx', 'runtime': 'codex', 'model': '', 'effort': 'max', 'browser': True}
        with tempfile.TemporaryDirectory(prefix='codex-cfg-') as d:
            with patch.object(ar, 'CODEX_CONFIG', os.path.join(d, 'none.toml')), \
                    patch.object(ar, 'CODEX_SKILL_DIRS', ()), \
                    patch.object(ar, 'codex_bin', return_value='/bin/codex'):
                argv, _ = ar.argv_for(agent, 'TASK', '/repo', web=True)
                apply_argv, _ = ar.argv_for(agent, 'TASK', '/repo', chrome=True, browser=ar.apply_overrides())
        for name in ('chrome@openai-bundled', 'computer-use@openai-bundled', 'unified-computer-use@openai-bundled'):
            self.assertIn(f'plugins.{name}.enabled=false', argv)
            self.assertNotIn(f'plugins.{name}.enabled=false', apply_argv)   # 代投照舊留著

    def test_claude_without_chrome_has_no_claude_in_chrome(self):
        agent = {'id': 'cc', 'runtime': 'claude-code', 'model': '', 'effort': 'max', 'browser': True}
        with tempfile.TemporaryDirectory(prefix='claude-settings-') as d:
            with patch.object(ar, 'claude_bin', return_value='/bin/claude'), \
                    patch.object(ar, 'CLAUDE_SETTINGS', os.path.join(d, 'none.json')):
                argv, _ = ar.argv_for(agent, 'TASK', '/repo', web=True)
        self.assertIn('--no-chrome', argv)
        self.assertNotIn('--chrome', argv)
        self.assertNotIn('claude-in-chrome', ' '.join(argv))

    def test_web_rule_says_no_browser_and_points_at_the_program_fetcher(self):
        rule = ar.browser_rule()
        self.assertIn('【抓網頁鐵律】', rule)
        self.assertIn('page_fetch.py', rule)                 # 照規則呼叫程式抓
        self.assertIn('不准操作任何瀏覽器', rule)
        self.assertNotIn('不要呼叫程式預抓', rule)            # 舊規矩叫它自己開瀏覽器、別叫程式抓


class AgentName(unittest.TestCase):
    def test_outcome_messages_use_the_global_agent_name(self):
        with patch.object(ar.cf, 'AGENT', 'Beacon'):
            self.assertIn('Beacon', ar.AgentResult('timeout', pid=42).message())
            self.assertIn('Beacon', ar.AgentResult('unavailable', reason='no_browser_agent').message())


class CodexDispatch(unittest.TestCase):
    """#109:prompt 走 stdin、成敗看事件、對話 id 從事件拿。"""
    AGENT = {'id': 'cx', 'runtime': 'codex', 'model': '', 'effort': 'high', 'browser': True}

    def test_prompt_is_fed_on_stdin_not_argv(self):
        with patch.object(ar, 'codex_bin', return_value='/bin/codex'):
            argv, _ = ar.argv_for(self.AGENT, 'RESUME-AND-PERSONAL-DATA', '/repo')
            again, _ = ar.argv_for(self.AGENT, 'MORE', '/repo', resume='S1')
        fed = ar.prompt_stdin(self.AGENT, 'RESUME-AND-PERSONAL-DATA')
        self.assertFalse(any('RESUME-AND-PERSONAL-DATA' in a for a in argv))   # ps 看不到
        self.assertEqual(argv[-1], '-')
        self.assertEqual(again[-1], '-')
        self.assertIn('--json', argv)
        self.assertTrue(fed.endswith('RESUME-AND-PERSONAL-DATA'))

    def test_page_text_saying_rate_limit_is_not_a_handoff(self):
        page = json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message',
                           'text': 'The job page says: rate limit exceeded, quota exceeded, 401 unauthorized'}})
        log = '\n'.join([json.dumps({'type': 'thread.started', 'thread_id': 'T1'}), page,
                         json.dumps({'type': 'turn.failed', 'error': {'message': 'stream disconnected'}})])
        self.assertIsNone(ar._failure_reason('codex', 1, log))
        real = log.replace('stream disconnected', "You've hit your usage limit")
        self.assertEqual(ar._failure_reason('codex', 1, real), 'quota')

    def test_exit_zero_with_failed_turn_is_failure(self):
        ok = '\n'.join([json.dumps({'type': 'thread.started', 'thread_id': 'T1'}),
                        json.dumps({'type': 'item.completed', 'item': {'type': 'error', 'message': 'config warning'}}),
                        json.dumps({'type': 'turn.completed'})])
        self.assertFalse(ar._codex_failed(ok))    # 設定檔的警告是 item,不算失敗
        self.assertTrue(ar._codex_failed(ok.replace('"turn.completed"', '"turn.failed"')))

    def test_session_id_comes_from_the_event(self):
        page = json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message',
                           'text': 'session id: 11111111-2222-3333-4444-555555555555'}})
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'fill.log')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'thread.started', 'thread_id': '01a0e5b4-c1be-7a50-a5c1-c498707bfd42'})
                        + '\n' + page + '\n')
            self.assertEqual(ar.session_id(p), '01a0e5b4-c1be-7a50-a5c1-c498707bfd42')
            self.assertIsNone(ar.session_id(p + '.missing'))


if __name__ == '__main__':
    unittest.main()
