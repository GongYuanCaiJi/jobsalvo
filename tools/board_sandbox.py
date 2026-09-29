#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_sandbox —— 複製一份現行看板來測介面,不碰使用者的標記。

改完介面要自己走一遍流程時開這個,不要開正式那一份。正式那一份是使用者真的在標的板子:
在上面點喜歡／加入準備／移除都會寫進他的判斷資料,測試一點就蓋掉,而他不可能記得自己標過什麼。

用法:python3 tools/board_sandbox.py        → http://localhost:<sandbox_port>(預設 8898)
     複製檔在 <tmp>/board-sandbox/board.html,砍掉再跑就是一份新的。
"""
import os, sys, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf

LIVE = cf.LIVE
SBOX = os.path.join(cf.TMP, 'board-sandbox', 'board.html')
PORT = cf.SANDBOX_PORT

def main():
    if not os.path.isfile(LIVE):
        sys.exit(f'沒有看板檔({LIVE}),先跑 uv run python tools/init.py。')
    os.makedirs(os.path.dirname(SBOX), exist_ok=True)
    shutil.copyfile(LIVE, SBOX)
    print(f'複製了一份 → {SBOX}(你在這上面怎麼點都不會動到正式看板)')
    os.execv(sys.executable, [sys.executable, os.path.join(HERE, 'board_server.py'),
                              '--state', SBOX, '--port', str(PORT), '--host', '127.0.0.1',
                              '--allow-agent'])

if __name__ == '__main__':
    main()
