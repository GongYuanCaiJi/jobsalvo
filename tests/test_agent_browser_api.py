#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""給 agent 的瀏覽器指示只能用 ego 真的有的 Page 方法(ego-browser skill 列的那一份)。
2026-10-07 實測:指示寫「勾選用 check/uncheck」,ego 沒有這個方法,agent 照做失敗再繞路,一張多花幾分鐘。"""
import os, re, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
import apply_run, chrome_door, profile_sync  # noqa: E402

# ego-browser skill「Page operations」列的方法(加上 mouse / keyboard 物件)
EGO_PAGE = {'label', 'spaceId', 'openedBy', 'targetId', 'url', 'title', 'info', 'snapshot', 'screenshot', 'goto', 'reload',
            'waitForURL', 'waitForEvent', 'waitForSelector', 'waitForLoadState', 'waitForFunction', 'waitForTimeout',
            'click', 'dblclick', 'hover', 'dragAndDrop', 'fill', 'selectOption', 'focus', 'press', 'setInputFiles',
            'waitForFileChooser', 'close', 'acceptDialog', 'dismissDialog', 'mouse', 'keyboard', 'evaluate', 'fetch', 'cdp'}


def texts():
    door = chrome_door.EgoDoor.__new__(chrome_door.EgoDoor)
    yield door.apply_rule()
    yield door.fetch_rule()
    for mod in (apply_run, profile_sync):
        for v in vars(mod).values():
            if isinstance(v, str) and 'page.' in v:
                yield v


class AgentBrowserApi(unittest.TestCase):
    def test_instructions_only_name_page_methods_ego_has(self):
        used = {m for t in texts() for m in re.findall(r'\bpage\.(\w+)\s*\(', t)}
        self.assertEqual(sorted(used - EGO_PAGE), [])
        self.assertFalse(any(re.search(r'用 check|check/uncheck', t) and 'ego 沒有' not in t for t in texts()))

    def test_current_profile_page_reader_uses_editing_tab_without_navigation(self):
        door = chrome_door.EgoDoor(agent_id='primary')
        door.workspace = {'id': 7, 'name': 'fake', 'page': 'p2'}
        page = {'url': 'https://example.test/resume', 'text': '文字', 'fields': [], 'links': []}
        with mock.patch.object(chrome_door, '_ego', return_value=page) as ego:
            self.assertEqual(door.read_profile_page('7:p2'), page)
        script = ego.call_args.args[0]
        self.assertIn('binding.page', script)
        self.assertIn('el.value', script)
        self.assertIn('document.links', script)
        self.assertNotIn('page.goto(', script)


if __name__ == '__main__':
    unittest.main()
