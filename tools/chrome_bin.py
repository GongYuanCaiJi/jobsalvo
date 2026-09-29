#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chrome_bin —— 哪個 Chrome、有哪些設定檔。印 PDF / 截圖 / 跑 JS、開 agent 的 Chrome、環境檢查都照這裡找。"""
import os, json, shutil

GUI = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
# macOS 上 Chrome 放使用者資料的地方;設定檔清單在它的 Local State(Chrome 自己的設定檔選單讀的就是這份)。
USER_DATA = os.path.expanduser('~/Library/Application Support/Google/Chrome')
# Chrome 線上應用程式商店上的官方擴充功能(ID 固定):Codex 的叫 ChatGPT。
EXTENSIONS = {'codex': ('hehggadaopoacecdllhhajmbjkdcmajg', 'ChatGPT'),
              'claude': ('fcoeoabgfenejglbffodgkkbkcdhcgfn', 'Claude')}


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


def profiles(root=None):
    """這台電腦 Chrome 的設定檔:[{dir: 資料夾名, name: Chrome 右上角看到的名字, ext: 裝了哪些官方擴充功能}]。
    讀不到(沒裝 Chrome、不是 Mac)回 []。"""
    root = root or USER_DATA
    try:
        with open(os.path.join(root, 'Local State'), encoding='utf-8') as f:
            cache = json.load(f)['profile']['info_cache']
    except (OSError, ValueError, KeyError, TypeError):
        return []
    out = [{'dir': d, 'name': (v or {}).get('name') or d,
            'ext': [k for k, (ext_id, _) in EXTENSIONS.items()
                    if os.path.isdir(os.path.join(root, d, 'Extensions', ext_id))]}
           for d, v in cache.items() if os.path.isdir(os.path.join(root, d))]   # 刪掉的設定檔 Chrome 還會記著名字
    names = [p['name'] for p in out]
    for p in out:                        # 同名的(好幾個「daniel」)附上資料夾名,才分得出來
        if names.count(p['name']) > 1:
            p['name'] += f"({p['dir']})"
    return sorted(out, key=lambda p: p['name'].lower())


if __name__ == '__main__':
    print(*chrome())
