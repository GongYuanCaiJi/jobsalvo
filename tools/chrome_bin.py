#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chrome_bin —— 開發工具(看板檢查、介面截圖)用哪個 Chrome。使用者會跑到的程式都走 ego,不用它。"""
import os, shutil

GUI = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


def find():
    """Chrome 執行檔;找不到回 ''。順序:CHROME_BIN、/Applications、~/Applications,再來 PATH 上的。"""
    mac = [os.environ.get('CHROME_BIN'), GUI,
           os.path.expanduser('~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')]
    # Google Chrome 優先:Ubuntu 的 chromium 是 snap,有自己的 /tmp,讀不到程式放在 /tmp 的網頁、PDF 也寫不回來。
    names = ('google-chrome', 'google-chrome-stable', 'chromium-browser', 'chromium')
    return (next((p for p in mac if p and os.path.isfile(p)), '')
            or next((p for p in map(shutil.which, names) if p), ''))


def chrome():
    """無頭用:回 (執行檔, 要加的旗標)。"""
    exe = find()
    if not exe:
        raise RuntimeError('這台機器沒有 Chrome')
    return exe, ['--headless=new']




if __name__ == '__main__':
    print(*chrome())
