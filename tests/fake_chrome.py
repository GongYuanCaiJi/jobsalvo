#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試用的假 agent 的 Chrome(chrome_door 的門路),和唯一一份手寫的假 Claude 工作紀錄(stream-json)。

填表、送出、查應徵進度的流程測試用 FakeChrome:回固定的頁面、記下被叫了什麼,能設定哪幾件事「現在做不到」
(丟 chrome_door.NotNow),不碰真的 Chrome、不派真的 agent。用 installed() 把它換進 chrome_door 的三個入口。

claude_log / claude_lines 只給 Claude 那條的測試(tests/test_chrome_door.py 等)組它那一輪的紀錄。
"""
import json
import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools')))
import chrome_door  # noqa: E402


class FakeChrome(chrome_door._Door):
    """假的門路。pages:{讀取網址: 頁面}(平台履歷、查應徵進度的來源);page:讀那一頁回的頁面。
    not_now:這一家現在做不到的事(方法名集合,例如 {'read_profile', 'read_pages'}),呼叫就丟 NotNow。
    up:派工前 Chrome 準備好了沒 (好了沒, 原因)。calls 記下每一次呼叫 (方法名, 參數…)。"""

    def __init__(self, runtime='codex', *, page=None, pages=None, not_now=(), from_log=(), up=(True, ''),
                 prepared=None, tail='', agent_id='primary', need='按「🔌 連接」'):
        self.runtime = runtime
        self.page = page if page is not None else {'url': '', 'fields': [], 'lines': []}
        self.pages = dict(pages or {})
        self.not_now = set(not_now)
        self.from_log = set(from_log)      # 這幾件事要等 agent 那一輪跑完、從它的紀錄拿(沒給紀錄就是現在做不到)
        self.up = up
        self.need = need
        self.program_reads = not {'read_pages', 'read_profile'} & (self.not_now | self.from_log)
        self.reads_live_page = 'read_page' not in self.not_now | self.from_log
        self.prepared = prepared
        self.tail = tail
        self.agent_id = agent_id
        self.calls = []

    def _can(self, name, *args, logs=None):
        self.calls.append((name,) + args)
        if name in self.not_now or (name in self.from_log and not logs):
            raise chrome_door.NotNow(f'假的 {self.runtime} 現在做不到 {name}')

    def ready(self, board=None):
        self._can('ready')
        return (*self.up, self.need)

    def configured(self):
        return bool(self.up[0])

    def open_for_agent(self, url, old_tab=None):
        self._can('open_for_agent', url, old_tab)
        return self.prepared

    def apply_rule(self):
        return ''

    def fetch_rule(self):
        return f'【{self.runtime} 的取檔方式】'

    def task_tail(self, stage):
        return self.tail if stage in ('fill', 'fix') else ''

    def profile_reread(self, read_url):
        return f'\n(假的:把 {read_url} 讀給程式)' if not self.program_reads else ''

    def page_reread(self, tab_id):
        return f'\n(假的:把分頁 {tab_id} 讀給程式)' if not self.reads_live_page else ''

    def read_page(self, session, tab_id, logs=None):
        self._can('read_page', session, tab_id, logs, logs=logs)
        return self.page

    def read_profile(self, read_url, logs=None, board=None):
        self._can('read_profile', read_url, logs, logs=logs)
        if read_url not in self.pages:
            raise LookupError(f'假的 Chrome 沒有 {read_url}')
        return self.pages[read_url]

    def read_pages(self, urls, board=None, ready=None, settle=0):
        self._can('read_pages', tuple(urls))
        return {u: self.pages.get(u, {}) for u in urls}

    def attachment_hashes(self, logs):
        self.calls.append(('attachment_hashes', logs))
        return None

    def shot(self, session, tab_id, out):
        self._can('shot', session, tab_id)
        with open(out, 'wb') as f:
            f.write(b'PNG')
        return out

    def release(self, session, tab_id):
        self._can('release', session, tab_id)

    def waiting(self, tabs, board=None):
        self.calls.append(('waiting', tuple(sorted(tabs))))
        return sorted(tabs)


@contextmanager
def installed(*fakes, agents=None, chrome_id=None):
    """把 chrome_door 換成這幾個假的門路(一家一個):of(那一家) 回它,current()、for_card() 照真的規則挑。
    agents:設定裡的 agent 清單;沒給就是每個假的門路一個會用 Chrome 的 agent(id 照 fake.agent_id)。
    chrome_id:假的 agent Chrome 是哪一個程序(記分頁時一起記);沒給就是一個一直開著的假程序,
    「頁還在不在」(agent_chrome.gone_pages)照它判斷,不去問這台電腦上真的 agent Chrome。"""
    import config as cf
    by_runtime = {f.runtime: f for f in fakes}
    if agents is None:
        agents = [{'id': f.agent_id, 'runtime': f.runtime, 'model': '', 'effort': 'max', 'browser': True} for f in fakes]
    def of(runtime, agent_id=None):
        fake = by_runtime.get(runtime)
        if fake is not None and agent_id is not None:
            fake.agent_id = agent_id          # 真的 of 用它建門路;假的一家只有一個,記在同一個上
        return fake
    running = dict(chrome_id or {'pid': 4242, 'start': 1.0})
    with patch.object(chrome_door, 'of', of), \
         patch.dict(cf.C, {'agent': dict(cf.C.get('agent') or {}, agents=agents)}), \
         patch('agent_chrome.chrome_id', return_value=dict(chrome_id if chrome_id is not None else running)), \
         patch('agent_chrome.pid', return_value=running.get('pid')), \
         patch('agent_chrome.started_at', return_value=running.get('start')):
        yield fakes[0] if len(fakes) == 1 else fakes


# ---- 假的 Claude 工作紀錄(claude -p --output-format stream-json 的格式):程式只收工具真的回傳的字 ----

def js_calls_for_self_read(whole, data):
    """Claude 照規矩分段跑一支唯讀函式(whole 是回傳 JSON 字串的那一段):先長度、再每 CHUNK 字一段。"""
    import apply_tab
    text = json.dumps(data, ensure_ascii=False)
    calls = [('String(' + whole + '.length)', str(len(text)))]
    for i in range(-(-len(text) // apply_tab.CHUNK)):
        calls.append((apply_tab.chunk_js(i, whole), f'{i}:' + text[i * apply_tab.CHUNK:(i + 1) * apply_tab.CHUNK]))
    return calls


def page_read(page):
    """它在申請表那一頁跑填完的自讀(apply_tab.CLAUDE_SELF_READ)。"""
    import apply_tab
    return js_calls_for_self_read(apply_tab._WHOLE, page)


def profile_read(page):
    """它在平台履歷頁跑讀平台履歷給程式那一支(apply_tab.CLAUDE_PROFILE_READ 第 1、2 步)。"""
    import apply_tab
    return js_calls_for_self_read(apply_tab._PROFILE_WHOLE, page)


def attach_read(url, files, code=None):
    """它在平台履歷頁跑算附件雜湊那一段(CLAUDE_PROFILE_READ 第 3 步);code 給了是它改過的程式碼。"""
    import apply_tab
    return [(code or apply_tab.ATTACH_JS, json.dumps({'url': url, 'files': files}, ensure_ascii=False))]


def claude_lines(calls, tab_context=True):
    """[(程式碼, 工具回傳的字)] → stream-json 每一行(dict)。tab_context:工具回傳後面照真的一樣帶「Tab Context」。"""
    lines = []
    for k, (code, out) in enumerate(calls, 1):
        lines.append({'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': f'u{k}', 'name': 'mcp__claude-in-chrome__javascript_tool',
             'input': {'action': 'javascript_exec', 'text': code}}]}})
        lines.append({'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': f'u{k}', 'content': [
                {'type': 'text', 'text': out + ('\n\nTab Context:\n- 1 分頁' if tab_context else '')}]}]}})
    return lines


def claude_log(path, calls, tab_context=True):
    """把 claude_lines 寫成一份紀錄檔,回路徑。"""
    with open(path, 'w', encoding='utf-8') as f:
        for line in claude_lines(calls, tab_context):
            f.write(json.dumps(line, ensure_ascii=False) + '\n')
    return path
