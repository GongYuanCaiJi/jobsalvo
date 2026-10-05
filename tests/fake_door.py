#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試用的假門路(chrome_door.EgoDoor 的替身)。

填表、送出、查應徵進度的流程測試用 FakeDoor:回固定的頁面、記下被叫了什麼,能設定哪幾件事「現在做不到」
(丟 chrome_door.NotNow),不碰真的 ego、不派真的 agent。用 installed() 把它換進 chrome_door 的入口。
"""

import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools')))
import chrome_door  # noqa: E402


class FakeDoor:
    """假的門路。pages:{讀取網址: 頁面}(平台履歷、查應徵進度的來源);page:讀那一頁回的頁面。
    not_now:這一家現在做不到的事(方法名集合,例如 {'read_profile', 'read_pages'}),呼叫就丟 NotNow。
    up:派工前 Chrome 準備好了沒 (好了沒, 原因)。calls 記下每一次呼叫 (方法名, 參數…)。"""

    live_refresh = 4
    live_timeout = 10

    def __init__(self, runtime='codex', *, page=None, pages=None, not_now=(), up=(True, ''),
                 prepared=None, agent_id='primary', need='打開 ego lite', attachments=()):
        self.runtime = runtime
        self.native_json_output = runtime == 'codex'
        self.page = page if page is not None else {'url': '', 'fields': [], 'lines': []}
        self.pages = dict(pages or {})
        self.not_now = set(not_now)
        self.up = up
        self.need = need
        self.prepared = prepared
        self.agent_id = agent_id
        self.calls = []
        self.workspace = {'id': 1, 'name': 'fake', 'page': 'p1'}
        self.attachments = attachments

    def _can(self, name, *args):
        self.calls.append((name,) + args)
        if name in self.not_now:
            raise chrome_door.NotNow(f'假的 {self.runtime} 現在做不到 {name}')

    def ready(self, board=None):
        self._can('ready')
        return (*self.up, self.need)

    def configured(self):
        return bool(self.up[0])

    def open_for_agent(self, url, old_tab=None, on_open=None):
        self._can('open_for_agent', url, old_tab)
        if self.prepared and on_open:
            on_open(self.prepared)
        return self.prepared

    def apply_rule(self):
        return ''

    def fetch_rule(self):
        return f'【{self.runtime} 的取檔方式】'

    def profile_reader(self, logs=None, board=None):
        return lambda read_url: self.read_profile(read_url, logs, board)

    def resume(self):
        self._can('resume')

    def read_page(self, session, tab_id, logs=None):
        self._can('read_page', session, tab_id, logs)
        return self.page

    def read_profile(self, read_url, logs=None, board=None):
        self._can('read_profile', read_url, logs)
        if read_url not in self.pages:
            raise LookupError(f'假的門路沒有 {read_url}')
        return self.pages[read_url]

    def read_pages(self, urls, board=None, ready=None, settle=0):
        self._can('read_pages', tuple(urls))
        return {u: self.pages.get(u, {}) for u in urls}

    def download_attachments(self, read_url, directory):
        self._can('download_attachments', read_url)
        os.makedirs(directory, exist_ok=True)
        files = []
        for i, item in enumerate(self.attachments):
            path = os.path.join(directory, f'attachment-{i}.bin')
            with open(path, 'wb') as f:
                f.write(item['bytes'])
            files.append({'name': item['name'], 'path': path})
        return {'files': files, 'problems': []}

    def shot(self, session, tab_id, out):
        self._can('shot', session, tab_id)
        with open(out, 'wb') as f:
            f.write(b'PNG')
        return out

    def release(self, session, tab_id):
        self._can('release', session, tab_id)

    def hand_off(self):
        self._can('hand_off')
        return {'ownership': 'user'}

    def human_action(self):
        self._can('human_action')
        return None

    def waiting(self, tabs, board=None):
        self.calls.append(('waiting', tuple(sorted(tabs))))
        return sorted(tabs)


@contextmanager
def installed(*fakes, agents=None):
    """把 chrome_door 換成這幾個假的門路(一家一個):of(那一家) 回它,current()、for_card() 照真的規則挑。
    agents:設定裡的 agent 清單;沒給就是每個假的門路一個會用瀏覽器的 agent(id 照 fake.agent_id)。
    「頁還在不在」(chrome_door.gone_pages)一律當還在、收尾(close_if_idle)不做,不去碰這台電腦上真的 ego。"""
    import config as cf
    by_runtime = {f.runtime: f for f in fakes}
    if agents is None:
        agents = [{'id': f.agent_id, 'runtime': f.runtime, 'model': '', 'effort': 'max', 'browser': True} for f in fakes]
    def of(runtime, agent_id=None):
        fake = by_runtime.get(runtime)
        if fake is not None and agent_id is not None:
            fake.agent_id = agent_id          # 真的 of 用它建門路;假的一家只有一個,記在同一個上
        return fake
    with patch.object(chrome_door, 'of', of), \
         patch.object(chrome_door, 'gone_pages', lambda *a, **k: []), \
         patch.object(chrome_door, 'close_if_idle', lambda *a, **k: ''), \
         patch.dict(cf.C, {'agent': dict(cf.C.get('agent') or {}, agents=agents)}):
        yield fakes[0] if len(fakes) == 1 else fakes
