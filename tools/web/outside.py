"""瀏覽器是唯一的外部邊界(ADR-0012):讀頁、截圖、讀網址、申請表、工作區與 agent 都經由這裡。

讀頁的方法一律 `raw_` 開頭,只有 web/ 核心叫得到(semgrep 規則 page-content)。
正式版 Real 接 ego、直接抓與閱讀代理、agent_run;測試在任務入口換成假的(tests/fake_browser.py)。
"""
from __future__ import annotations

import dataclasses
import os
from types import SimpleNamespace
from typing import Protocol




class Browser(Protocol):
    def raw_tabs(self) -> list[dict]:
        """工作區每一個分頁讀頁那一側回的一列(chrome_door.workspace_pages 的格式)。"""

    def raw_shot(self, tab: str, path: str) -> str:
        """截那一分頁存到 path;截不到就丟例外,原因在訊息裡。"""

    def raw_shot_url(self, url: str, path: str) -> str:
        """打開一個網址截圖存到 path;截不到就丟例外。"""

    def raw_fetch(self, url: str) -> dict:
        """讀一個網址:url、status(ok、partial、unknown)、text、via、title、http_status、errors。"""

    def raw_form(self, tab: str) -> list[dict]:
        """申請表每一格瀏覽器給的名稱與值(apply_tab.READ_FORM 的格式)。"""

    def open_workspace(self) -> dict:
        """開一個只有空白分頁的工作區,回 {'id', 'name'}。"""

    def hand_to_user(self, tab: str) -> str:
        """把那一分頁交到使用者面前,回分頁標籤。"""

    def fill_form(self, tab: str, items: list[dict]) -> list[dict]:
        """程式替 agent 填這幾格:[{backend, how, value}] → 每格 {backend, ok}。"""

    def run_agent(self, prompt: str, *, directories, timeout: int, resume=None, agent_id=None):
        """跑一輪 agent,交件單寫在 directories[0]/handoff.json;回 status、session、why、agent_id。"""

    def exchange_rates(self) -> dict:
        """今天的匯率:{幣別三碼: 一元新台幣換多少};拿不到回空的。"""


class Real:
    """接真的 ego 工作區、讀網址的三條路與 agent_run;validate_url 是公開網址檢查(測試台換成准它自己的主機)。"""

    def __init__(self, door=None, validate_url=None, browser_only=False):
        self.door = door
        self.validate_url = validate_url
        self.browser_only = browser_only    # 只用 agent 的瀏覽器讀網址(page_fetch --browser)

    @classmethod
    def for_workspace(cls, workspace_id: int, name: str = ''):
        """接已經開著的工作區;卡上記了它的名字就照卡上的,沒記才問 ego。"""
        import chrome_door
        if not name:
            space = next((row for row in chrome_door.EgoDoor.workspaces() if row.get('id') == workspace_id), None)
            if not space:
                raise RuntimeError(f'找不到工作區 {workspace_id}')
            name = space.get('name')
        door = chrome_door.EgoDoor()
        door.workspace = {'id': workspace_id, 'name': name, 'page': 'p1'}
        return cls(door)

    def raw_tabs(self):
        return self._ego(self.door.read_workspace_pages)

    def raw_shot(self, tab, path):
        return self._ego(self.door.shot, tab, path)

    @staticmethod
    def _ego(call, *args):
        """ego 回頁面不見了:照 ego 自己的原因報(填表卡上那一句不是讀頁這一側的原因)。"""
        import chrome_door
        try:
            return call(*args)
        except chrome_door.Unreachable as error:
            raise OSError(error.why) from error

    def raw_shot_url(self, url, path):
        from web.fetch import screenshot
        return screenshot(url, path, validate_url=self.validate_url)

    def raw_fetch(self, url):
        from web.fetch import fetch_with_browser, request_page
        read = fetch_with_browser if self.browser_only else request_page
        return dataclasses.asdict(read(url, validate_url=self.validate_url))

    def raw_form(self, tab):
        return self.door.read_page(tab)

    def open_workspace(self):
        import chrome_door
        self.door = chrome_door.EgoDoor()
        opened = self.door.new_workspace(blank=True)['workspace']
        return {'id': int(opened['id']), 'name': str(opened.get('name') or '')}

    def hand_to_user(self, tab):
        label = str(tab).partition(':')[2]
        self.door.workspace['handoff_page'] = label
        self.door.hand_off()
        return label

    def fill_form(self, tab, items):
        return self.door.fill_fields(tab, items)

    def exchange_rates(self):
        """ExchangeRate-API 的 Open Access 匯率:免金鑰、每天更新一次、准快取,要標出處(看板找缺條件旁)。
        一天抓一次存在使用者資料夾;抓不到用上次存的,從沒抓到過就回空的(外幣薪資算查不到)。"""
        import json
        import time
        import urllib.request

        import config as cf
        path = os.path.join(cf.RESEARCH, 'exchange-rates.json')
        today = time.strftime('%Y-%m-%d')
        try:
            with open(path, encoding='utf-8') as source:
                kept = json.load(source)
        except (OSError, ValueError):
            kept = {}
        if kept.get('day') == today:
            return kept.get('rates') or {}
        try:
            # 網址是寫死的 https(ExchangeRate-API 免金鑰版,ADR-0013),沒有外來的值能換成 file://
            with urllib.request.urlopen('https://open.er-api.com/v6/latest/TWD', timeout=20) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
                body = json.load(response)
        except (OSError, ValueError):
            return kept.get('rates') or {}
        if body.get('result') != 'success' or not isinstance(body.get('rates'), dict):
            return kept.get('rates') or {}
        os.makedirs(cf.RESEARCH, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as target:
            json.dump({'day': today, 'rates': body['rates']}, target)
        return body['rates']

    def run_agent(self, prompt, *, directories, timeout, resume=None, agent_id=None):
        import agent_run
        log = os.path.join(directories[0], 'agent.log')
        result = agent_run.run(prompt, log, os.getcwd(), timeout=timeout, chrome=self.door is not None,
                               browser_required=self.door is not None, resume=resume, agent_id=agent_id,
                               output_last_message=os.path.join(directories[0], 'handoff.json'), website_task=True)
        message = getattr(result, 'message', None)
        why = message() if callable(message) else getattr(result, 'reason', None) or getattr(result, 'returncode', '')
        return SimpleNamespace(status=getattr(result, 'status', ''), session=agent_run.session_id(log),
                               why=str(why or ''), agent_id=getattr(result, 'agent_id', None) or agent_id)
