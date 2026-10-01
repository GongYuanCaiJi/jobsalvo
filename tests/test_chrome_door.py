#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent 的 Chrome 的門路(chrome_door):Codex 一條、Claude 一條,各自的測試;
加上「這張卡記的那一家」怎麼挑(換了 agent 之後舊頁找誰)。

Codex 那條:程式自己接外掛的 cua_repl(換成假的分頁,不開真的 Chrome)。
Claude 那條:從它那一輪的紀錄拿工具回傳(tests/fake_chrome 那一份假紀錄),截、關是 claude -p --resume(換成假的指令)。
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
import fake_chrome as fc  # noqa: E402
import chrome_door  # noqa: E402
import config as cf  # noqa: E402

PAGE = {'url': 'https://jobs.example/apply', 'title': 'Apply', 'fields': [{'label': 'Name', 'value': 'A'}],
        'shownFiles': [], 'lines': ['Apply']}
PROFILE = 'https://pda.104.com.tw/profile/preview?vno=1'
PROFILE_PAGE = {'url': PROFILE, 'title': '我的履歷', 'text': '工作經歷 ' + 'x' * 2500, 'links': ['https://ex.test/p']}


def agents(*rows):
    return {'agent': dict(cf.C.get('agent') or {}, agents=[dict({'model': '', 'effort': 'max'}, **r) for r in rows])}


class FakeTab:
    """外掛的一個分頁(apply_tab.Tab 換成它):js 回固定的頁面,截圖回固定的圖。"""
    seen = []

    def __init__(self, session, tab):
        FakeTab.seen.append((session, tab))

    def js(self, code, timeout_ms=60000):
        FakeTab.seen.append(code)
        return json.dumps(PAGE) if 'evaluate' in code else 'ok'

    def call(self, code, timeout_ms=60000):
        return '', [b'PNG-CODEX']

    def end_turn(self, keep=()):
        pass

    def close(self):
        pass


class CodexDoor(unittest.TestCase):
    """Codex:程式帶那段對話的身分自己接外掛,隨時看得到那一頁;平台履歷、查應徵進度的頁程式自己開頁讀。"""

    def setUp(self):
        self.door = chrome_door.of('codex')
        FakeTab.seen = []
        self.d = tempfile.mkdtemp(prefix='door-codex-')

    def test_reads_and_shoots_the_page_through_its_own_session(self):
        import apply_tab
        with patch.object(apply_tab, 'Tab', FakeTab):
            self.assertEqual(self.door.read_page('S1', '7'), PAGE)
            out = self.door.shot('S1', '7', os.path.join(self.d, 'fill.png'))
            self.door.release('S1', '7')
        with open(out, 'rb') as f:
            self.assertEqual(f.read(), b'PNG-CODEX')
        self.assertIn(('S1', '7'), FakeTab.seen)
        self.assertTrue(any('about:blank' in str(x) for x in FakeTab.seen))      # 放掉分頁:換空白頁,不關

    def test_the_program_opens_the_profile_page_itself_before_the_fill(self):
        with patch('agent_chrome.read_pages', return_value={PROFILE: PROFILE_PAGE}) as read:
            self.assertEqual(self.door.profile_reader()(PROFILE), PROFILE_PAGE)   # 填表前也讀得到
        read.assert_called_once()
        self.assertTrue(self.door.program_reads)
        self.assertIsNone(self.door.attachment_hashes(['x.log']))               # 附件取回來逐位元組比,不用雜湊

    def test_prepares_the_application_page_and_asks_the_extension_for_waiting_pages(self):
        with patch('agent_chrome.open_for_agent', return_value={'tab_id': '5'}) as opened:
            self.assertEqual(self.door.open_for_agent('https://jobs.example/1', old_tab='3'), {'tab_id': '5'})
        opened.assert_called_once_with('https://jobs.example/1', old_tab='3')
        with patch('agent_chrome.codex_waiting', return_value=['7']) as asked:
            self.assertEqual(self.door.waiting({'7', '9'}), ['7'])              # 問外掛:9 已經不在了
        asked.assert_called_once()

    def test_its_instructions_are_the_codex_ones(self):
        self.assertIn('extensionInstanceId', self.door.apply_rule())
        self.assertIn('filechooser', self.door.fetch_rule())
        self.assertIn('downloadMedia', self.door.fetch_rule())
        self.assertEqual(self.door.task_tail('fill'), '')
        self.assertEqual(self.door.profile_reread(PROFILE), '')
        self.assertEqual((self.door.live_refresh, self.door.shot_while_busy), (4, True))   # 👀 每 4 秒重截、隨時截得到

    def test_ready_is_the_codex_extension_check(self):
        with patch('agent_chrome.ensure', return_value=(False, '外掛沒連上')):
            self.assertEqual(self.door.ready(), (False, '外掛沒連上', '按「🔌 連接 Codex」'))


class ClaudeDoor(unittest.TestCase):
    """Claude:那一頁只有它那段對話拿得到。讀是從它那一輪的紀錄拿(只收程式碼一字不差的那幾次);
    那一輪還沒跑、程式自己開頁讀這些做不到,照實講、講改用什麼。"""

    def setUp(self):
        self.door = chrome_door.of('claude-code')
        self.d = tempfile.mkdtemp(prefix='door-claude-')

    def log(self, calls, name='fill.log'):
        return fc.claude_log(os.path.join(self.d, name), calls)

    def test_the_page_comes_from_its_own_run_log(self):
        long = dict(PAGE, fields=[{'label': 'Why', 'value': 'x' * 2500}])
        calls = fc.page_read(long)
        self.assertGreater(len(calls), 2)                                     # 真的有分段(每段不超過工具的 1000 字上限)
        calls.append(('document.title', '{"fields": ["模型自己寫的"]}'))        # 不是那支函式的回傳不收
        log = self.log(calls)
        self.assertEqual(self.door.read_page('S1', '7', [log]), long)
        log = self.log(fc.page_read(PAGE))
        self.assertEqual(self.door.read_page('S1', '7', [log]), PAGE)
        wrap = self.log([], 'fill-wrapup.log')                               # 收尾那一輪沒再讀:前一輪的還算
        self.assertEqual(self.door.read_page('S1', '7', [log, wrap]), PAGE)

    def test_a_chunk_whose_code_was_changed_is_not_trusted(self):
        import apply_tab
        calls = fc.page_read(PAGE)
        calls[-1] = (calls[-1][0] + '.trim()', calls[-1][1])                 # 最後一段的程式碼被改過
        with self.assertRaises(LookupError):
            self.door.read_page('S1', '7', [self.log(calls)])
        self.assertIn('String(' + apply_tab._WHOLE + '.length)', self.door.apply_rule())             # 規矩裡叫它最後讀一次

    def test_the_page_cannot_be_read_before_its_run_says_what_to_use_instead(self):
        with self.assertRaises(chrome_door.NotNow) as e:
            self.door.read_page('S1', '7')
        self.assertIn('👀', str(e.exception))

    def test_the_profile_page_comes_from_the_log_and_only_the_one_to_check(self):
        log = self.log(fc.page_read(PAGE) + fc.profile_read(PROFILE_PAGE))
        self.assertEqual(self.door.profile_reader([log])(PROFILE), PROFILE_PAGE)
        self.assertEqual(self.door.read_page('S1', '7', [log]), PAGE)         # 兩種自讀混在同一份紀錄裡互不干擾
        with self.assertRaises(LookupError) as e:
            self.door.read_profile('https://pda.104.com.tw/profile/preview?vno=2', [log])
        self.assertIn('vno=1', str(e.exception))
        with self.assertRaises(chrome_door.NotNow) as e:                   # 填表前:這一輪由它讀給程式
            self.door.profile_reader()(PROFILE)
        self.assertNotIn('Codex', str(e.exception))
        self.assertFalse(self.door.program_reads)
        import apply_tab
        PROFILE_LEN_JS = 'String(' + apply_tab._PROFILE_WHOLE + '.length)'
        self.assertIn(PROFILE_LEN_JS, self.door.apply_rule())            # 規矩裡教它怎麼讀給程式
        self.assertNotIn(PROFILE_LEN_JS, chrome_door.of('codex').apply_rule())
        self.assertNotIn('\\', PROFILE_LEN_JS)                         # 它會改寫跳脫字,程式碼就對不上
        with self.assertRaises(chrome_door.NotNow):
            self.door.read_pages([PROFILE])

    def test_attachments_are_compared_by_the_hashes_it_computed_on_the_page(self):
        f = {'name': 'cv.pdf', 'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}
        log = self.log(fc.attach_read(PROFILE, [f]))
        self.assertEqual(self.door.attachment_hashes([log])(PROFILE), [f])
        changed = self.log(fc.attach_read(PROFILE, [f], code='(() => "x")()'), 'other.log')
        with self.assertRaises(LookupError):
            self.door.attachment_hashes([changed])(PROFILE)

    def test_it_never_takes_a_page_the_program_opened(self):
        self.assertIsNone(self.door.open_for_agent('https://jobs.example/1', old_tab='3'))

    def test_its_instructions_are_the_claude_ones(self):
        rule = self.door.apply_rule()
        self.assertIn('Claude in Chrome', rule)
        self.assertNotIn('filechooser', self.door.fetch_rule())                # Codex 的選檔做法它做不到
        self.assertIn('【填完、改完的最後一步】', self.door.task_tail('fix'))
        # 送出那一輪按完送出也要把那一頁讀給程式:程式自己判斷還停在申請表沒有(#316)
        self.assertIn('【填完、改完的最後一步】', self.door.task_tail('submit'))
        self.assertIn('【填完、改完的最後一步】', self.door.page_reread('7'))       # 送出前核對那一輪也讀申請表那一頁
        self.assertIn(PROFILE, self.door.profile_reread(PROFILE))
        self.assertEqual((self.door.live_refresh, self.door.shot_while_busy), (0, False))

    def test_the_fetch_instructions_agree_on_whether_to_write_problems(self):
        # 對照表叫它「寫進 problems」,取檔規則卻說「不用寫進 problems」:兩句矛盾,它每一輪都多寫一條卡住
        rule = self.door.apply_rule()
        line = next(x for x in rule.splitlines() if 'downloadMedia' in x)
        self.assertNotIn('寫進 problems', line)
        self.assertIn('不用寫進 problems', self.door.fetch_rule())

    def test_the_screenshot_comes_from_the_tool_result_of_the_resumed_conversation(self):
        import base64
        rows = [{'type': 'assistant', 'message': {'content': [
                    {'type': 'tool_use', 'id': 'c1', 'name': 'mcp__claude-in-chrome__computer', 'input': {}}]}},
                {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'c1', 'content': [
                    {'type': 'image', 'source': {'data': base64.b64encode(b'PNG-CLAUDE').decode()}}]}]}}]
        seen = []
        with patch('agent_run.claude_bin', return_value='/bin/claude'), \
             patch('agent_chrome.conf', return_value={'claude_device': 'dev'}), \
             patch('apply_tab.subprocess.run', side_effect=lambda argv, **k: seen.append(argv) or Mock(
                 stdout='\n'.join(json.dumps(r) for r in rows))):
            out = self.door.shot('S1', '7', os.path.join(self.d, 'live.png'))
        with open(out, 'rb') as f:
            self.assertEqual(f.read(), b'PNG-CLAUDE')
        self.assertEqual(seen[0][seen[0].index('--resume') + 1], 'S1')          # 接回填這張的那段對話

    def test_waiting_pages_are_the_ones_on_the_board(self):
        self.assertEqual(self.door.waiting({'9', '7'}), ['7', '9'])


class WhichFamilyACardUses(unittest.TestCase):
    """每張卡記著那一頁是哪一家開的;之後看、改、送出、關掉都找同一家。沒記到、那一家不能用了:接不回來。"""

    def test_a_card_filled_before_the_update_says_so_not_that_the_agent_was_swapped(self):
        # 更新前填的卡沒記是哪一家開的:接不回來,但不是「換掉了」(#311 預設 A)
        with patch.dict(cf.C, agents({'id': 'primary', 'runtime': 'codex', 'browser': True})):
            with self.assertRaises(chrome_door.Unreachable) as e:
                chrome_door.for_card({'session': 'S1', 'tab_id': '7', 'agent_id': 'primary'})
        self.assertEqual(str(e.exception), '這張是更新前填的,程式不知道是哪個 agent 開的頁,要重填')
        self.assertTrue(e.exception.sure)

    def test_a_card_whose_family_was_really_swapped_says_so(self):
        with patch.dict(cf.C, agents({'id': 'cc', 'runtime': 'claude-code', 'browser': True})):
            with self.assertRaises(chrome_door.Unreachable) as e:
                chrome_door.for_card({'runtime': 'codex', 'agent_id': 'primary'})
        self.assertEqual(str(e.exception), chrome_door.AGENT_SWAPPED)
        self.assertTrue(e.exception.sure)

    def test_a_family_this_version_does_not_know_is_unreachable(self):
        # 卡上記的那一家這一版已經不支援(或資料寫壞了):不猜是哪一家,照「換掉了」講要重填
        with patch.dict(cf.C, agents({'id': 'primary', 'runtime': 'codex', 'browser': True})):
            with self.assertRaises(chrome_door.Unreachable) as e:
                chrome_door.for_card({'runtime': 'retired-family', 'agent_id': 'primary'})
        self.assertEqual(str(e.exception), chrome_door.AGENT_SWAPPED)
        self.assertTrue(e.exception.sure)

    def test_the_same_family_still_usable_takes_the_old_page(self):
        with patch.dict(cf.C, agents({'id': 'primary', 'runtime': 'codex', 'browser': True})):
            door = chrome_door.for_card({'runtime': 'codex', 'agent_id': 'primary'})
            self.assertEqual((door.runtime, door.agent_id), ('codex', 'primary'))
        # 換成同一家的另一個 agent(原本那個拿掉了):還是 Codex 那段對話,由新的那個接回去
        with patch.dict(cf.C, agents({'id': 'cc', 'runtime': 'claude-code', 'browser': False},
                                     {'id': 'codex-2', 'runtime': 'codex', 'browser': True})):
            door = chrome_door.for_card({'runtime': 'codex', 'agent_id': 'primary'})
            self.assertEqual((door.runtime, door.agent_id), ('codex', 'codex-2'))

    def test_a_family_no_longer_used_is_unreachable(self):
        with patch.dict(cf.C, agents({'id': 'primary', 'runtime': 'codex', 'browser': False},
                                     {'id': 'cc', 'runtime': 'claude-code', 'browser': True})):
            with self.assertRaises(chrome_door.Unreachable):
                chrome_door.for_card({'runtime': 'codex', 'agent_id': 'primary'})

    def test_changing_an_agents_family_in_settings_does_not_hand_it_the_old_page(self):
        # 設定頁可以改執行環境、不改代號:卡上記的是 Codex 開的頁,不能叫一個 Claude 用同一個代號接回去
        with patch.dict(cf.C, agents({'id': 'primary', 'runtime': 'claude-code', 'browser': True})):
            with self.assertRaises(chrome_door.Unreachable):
                chrome_door.for_card({'runtime': 'codex', 'agent_id': 'primary'})

    def test_new_work_goes_to_the_agent_that_uses_chrome(self):
        with patch.dict(cf.C, agents({'id': 'cmd', 'runtime': 'command-code', 'browser': False},
                                     {'id': 'cc', 'runtime': 'claude-code', 'browser': True})):
            door = chrome_door.current()
            self.assertEqual((door.runtime, door.agent_id), ('claude-code', 'cc'))
        with patch.dict(cf.C, agents({'id': 'cmd', 'runtime': 'command-code', 'browser': False})):
            self.assertIsNone(chrome_door.current())


class CommandLineToolsSayWhatToUseInstead(unittest.TestCase):
    """兩個命令列工具(apply_tab read、profile_sync)用 Claude 時做不到:直接說做不到、改用什麼,不是丟一個看不懂的錯。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='door-cli-')
        cfg = patch.dict(cf.C, agents({'id': 'cc', 'runtime': 'claude-code', 'browser': True}))
        cfg.start()
        self.addCleanup(cfg.stop)

    def run_cli(self, main, argv):
        import io
        err = io.StringIO()
        with patch.object(sys, 'argv', argv), patch('sys.stderr', err), self.assertRaises(SystemExit) as e:
            main()
        return e.exception.code, err.getvalue()

    def test_reading_a_claude_page_from_the_command_line(self):
        import apply_tab
        import board_doc as bd
        board = os.path.join(self.d, 'board.html')
        fb = {'https://jobs.example/1': {'ds': 'parked', 'apply': {'session': 'S1', 'tab_id': '7', 'runtime': 'claude-code'}}}
        with open(board, 'w') as f:
            f.write('x')
        with patch.object(bd, 'parse', return_value={'fb': json.dumps(fb), 'data': {'jobs': []}}), \
             patch('agent_chrome.gone_pages', return_value=[]):
            code, err = self.run_cli(apply_tab.main, ['apply_tab.py', 'read', '--url', 'https://jobs.example/1', '--board', board])
        self.assertEqual(code, 2)
        self.assertIn('現在讀不了', err)
        self.assertIn('👀', err)                                             # 改用什麼

    def test_comparing_the_platform_profile_from_the_command_line(self):
        import profile_sync as ps
        with patch('agent_chrome.read_pages', side_effect=AssertionError('不該走 Codex 外掛那一條')):
            code, _err = self.run_cli(ps.main, ['profile_sync.py', '--platform', '104',
                                                '--resume', next(iter(cf.RESUMES), 'x')])
        self.assertEqual(code, ps.CLI_NOT_NOW)
        self.assertIn('讀不了', code)
        self.assertIn('改在看板', code)                                        # 改用什麼


class OnlyTheDoorAsksWhichFamily(unittest.TestCase):
    """「如果是 Claude 就……」只准寫在 chrome_door 裡面兩條。其他地方要嘛拿門路來用,要嘛是「怎麼跑 agent」本身
    (啟動指令、失敗原因、設定檢查、安裝檢查),那幾處列在下面。新加的判斷寫到別處,這條會紅。"""
    # 下面這幾處是「怎麼跑 agent」本身(啟動指令、失敗原因、額度換手)或設定、安裝檢查,不是 agent 的 Chrome 的門路
    ALLOWED = {
        'agent_run.py': ('def rules_for', 'def prompt_stdin', 'def argv_for', 'def _failure_reason', 'def run(',
                         'def _run(', 'def _attempt_line'),   # _run 是 run 拆出來的本體(#315),同一段成敗判斷
        'agent_chrome.py': ('def claude_connected',), # 連接 Claude 時找 Claude 那個 agent 的模型
        'reply_run.py': ('def _copy_only_agent_id',), # 只讀分析固定用 Codex(關得掉瀏覽器和搜尋),不是 Chrome 的門路
    }

    def test_no_family_checks_outside_the_door(self):
        tools = os.path.join(HERE, '..', 'tools')
        # 比對是哪一家,和「沒記到就當 Codex」的預設(or 'codex'、runtime='codex')
        pat = re.compile(r"""(==|!=|\bin\b)\s*\(?\s*['"](codex|claude-code)['"]|['"](codex|claude-code)['"]\s*(==|!=)"""
                         r"""|['"](codex|claude-code)['"]\s+(not\s+)?in\b"""
                         r"""|\bor\s+['"]codex['"]|runtime\s*=\s*['"]codex['"]""")
        bad = []
        for name in sorted(os.listdir(tools)):
            # 兩條門路本身;裝了沒、登入了沒(doctor);設定存檔的檢查(settings_api);預設值、舊設定遷移(config)
            if not name.endswith('.py') or name in ('chrome_door.py', 'doctor.py', 'settings_api.py', 'config.py'):
                continue
            with open(os.path.join(tools, name), encoding='utf-8') as f:
                lines = f.read().splitlines()
            where = ''
            for i, line in enumerate(lines, 1):
                m = re.match(r'\s*(def \w+\(?|class \w+|[A-Z_]+\s*=)', line)
                if m and not line.startswith(' ' * 8):
                    where = line.strip()
                if line.lstrip().startswith('#') or not pat.search(line):
                    continue
                ok = self.ALLOWED.get(name)
                if ok and any(where.startswith(w) or w in line for w in ok):
                    continue
                bad.append(f'{name}:{i}: {line.strip()[:100]}')
        self.assertEqual(bad, [], '這幾處自己判斷是哪一家,改成用 chrome_door 的門路')


if __name__ == '__main__':
    unittest.main()
