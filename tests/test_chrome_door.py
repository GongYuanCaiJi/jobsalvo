#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent 的瀏覽器的門路(chrome_door):「這張卡記的那一家」怎麼挑(換了 agent 之後舊頁找誰),
以及「是哪一家」的判斷只准寫在門路裡。ego 本身的門路測試在 tests/test_ego_door.py。
"""
import os
import re
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
import chrome_door  # noqa: E402
import config as cf  # noqa: E402



def agents(*rows):
    return {'agent': dict(cf.C.get('agent') or {}, agents=[dict({'model': '', 'effort': 'max'}, **r) for r in rows])}


class WhichFamilyACardUses(unittest.TestCase):
    """每張卡記著那一頁是哪一家開的;之後看、改、送出、關掉都找同一家。沒記到、那一家不能用了:接不回來。"""

    def test_a_card_filled_before_the_update_says_so_not_that_the_agent_was_swapped(self):
        # 更新前填的卡沒記是哪一家開的:接不回來,但不是「換掉了」(#311 預設 A)
        with patch.dict(cf.C, agents({'id': 'primary', 'runtime': 'codex', 'browser': True})):
            with self.assertRaises(chrome_door.Unreachable) as e:
                chrome_door.for_card({'session': 'S1', 'tab_id': '7', 'agent_id': 'primary'})
        self.assertIn('更新前', str(e.exception))
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


class OnlyTheDoorAsksWhichFamily(unittest.TestCase):
    """「如果是 Claude 就……」只准寫在 chrome_door 裡面。其他地方要嘛拿門路來用,要嘛是「怎麼跑 agent」本身
    (啟動指令、失敗原因、設定檢查、安裝檢查),那幾處列在下面。新加的判斷寫到別處,這條會紅。"""
    # 下面這幾處是「怎麼跑 agent」本身(啟動指令、失敗原因、額度換手)或設定、安裝檢查,不是 agent 的瀏覽器的門路
    ALLOWED = {
        'agent_run.py': ('def rules_for', 'def prompt_stdin', 'def argv_for', 'def _failure_reason', 'def run(',
                         'def _run(', 'def _attempt_line'),   # _run 是 run 拆出來的本體(#315),同一段成敗判斷
        'reply_run.py': ('def _copy_only_agent_id',), # 只讀分析固定用 Codex(關得掉瀏覽器和搜尋),不是 Chrome 的門路
    }

    def test_only_dev_tools_launch_a_browser_themselves(self):
        # 使用者會跑到的程式要開網頁、截圖、印 PDF 一律走 ego 的門路;自己開 Playwright/Chrome 的只准是開發工具
        # (看板檢查、介面截圖在 CI 的 Linux 上跑,ego 只有 macOS)。無頭 Chrome 會在 Dock 冒圖示、要使用者另裝 Chrome
        dev = {'board_check.py', 'shot.py', 'chrome_bin.py', 'dev_pdf.py'}
        pat = re.compile(r'^\s*(import|from)\s+(playwright|chrome_bin)\b', re.M)
        tools = os.path.join(HERE, '..', 'tools')
        bad = [name for name in sorted(os.listdir(tools)) if name.endswith('.py') and name not in dev
               and pat.search(open(os.path.join(tools, name), encoding='utf-8').read())]
        self.assertEqual(bad, [])

    def test_no_family_checks_outside_the_door(self):
        tools = os.path.join(HERE, '..', 'tools')
        # 比對是哪一家,和「沒記到就當 Codex」的預設(or 'codex'、runtime='codex')
        pat = re.compile(r"""(==|!=|\bin\b)\s*\(?\s*['"](codex|claude-code)['"]|['"](codex|claude-code)['"]\s*(==|!=)"""
                         r"""|['"](codex|claude-code)['"]\s+(not\s+)?in\b"""
                         r"""|\bor\s+['"]codex['"]|runtime\s*=\s*['"]codex['"]""")
        bad = []
        for name in sorted(os.listdir(tools)):
            # 門路本身;裝了沒、登入了沒(doctor);設定存檔的檢查(settings_api);預設值、舊設定遷移(config)
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
