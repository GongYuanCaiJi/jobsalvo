"""給 agent 的任務指示:目標、材料、工具與用法、界線、交件單格式。"""
from __future__ import annotations

import dataclasses

from web.sheet import Schema


EGO = ('ego-browser:操作 agent 的瀏覽器。這一輪的工作區是 taskSpace({workspace}),一開始只有一個空白分頁;'
       '要看的頁都由你自己打開。')
BEFORE = ('jobsalvo-before:拍下你叫它那一刻工作區裡每一個開著的分頁(文字和截圖),當作改之前的樣子。'
          '印出拍到哪幾頁、各頁網址、讀不讀得到,以及編號材料(交付代號和每段「n. 文字」);交件單要引段就引這份。'
          '一輪可以叫好幾次。')
AFTER = ('jobsalvo-after:拍下你叫它那一刻工作區裡每一個開著的分頁(文字和截圖),當作改完的樣子,跟拍改前比。'
         '印出的內容跟 jobsalvo-before 一樣。一輪可以叫好幾次,以最後一次為準。')
SHELL = '上面兩個指令各自用獨立的 shell 指令執行,不要放在 ego-browser 的 Node.js 腳本或 child_process 裡。'
FORM_PLAN = ('form_plan:程式替你填它知道答案的格子。打開申請表之後跑 --fill:程式讀這一頁的每一格,題目跟答案庫或以前填過的題目一字不差的空白文字格由程式填好,其他格子列給你;'
             '--set 把你填的格子記下來;--check 列出還空著、程式能填的格子。')
FETCH = 'page_fetch <網址>:程式讀這個網址,印出讀到的編號材料或讀不到的原因。'


@dataclasses.dataclass(frozen=True)
class Brief:
    goal: str
    materials: tuple[tuple[str, str], ...]
    tools: tuple[str, ...]
    limits: tuple[str, ...]
    handoff: Schema
    sheet_file: bool = False      # 交件單也由 agent 自己寫進檔(時間到時程式收這個檔,#198)

    def render(self, *, workspace: int, sheet_file: str = '') -> str:
        handoff = self.handoff.format_text()
        if self.sheet_file:
            path = sheet_file or '(這一輪的交件檔)'
            handoff += (f'\n交件單同時寫進 {path}:每多一列就寫一次整份,先寫到 {path}.tmp 再改名成 {path};'
                        '時間到時程式收這個檔裡最後寫的那一份。')
        sections = (
            ('目標', self.goal),
            ('材料', '\n'.join(f'{title}:\n{value}' for title, value in self.materials)),
            ('工具與用法', '\n'.join(tool.replace('{workspace}', str(workspace)) for tool in self.tools)),
            ('界線', '\n'.join(f'- {line}' for line in self.limits)),
            ('交件單格式', handoff),
        )
        return '\n\n'.join(f'【{title}】\n{body}' for title, body in sections)
