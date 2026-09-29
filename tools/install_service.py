#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_service —— 讓看板開機自己起、被殺自己回來(macOS launchd)。

產生 ~/Library/LaunchAgents/dev.jobsalvo.board-server.plist,指向這份 jobsalvo 和你的資料夾(JOBSALVO_HOME),
再 launchctl 載入。跑 tools/board_serve.sh 而不是直接跑 python:每次起動都會在紀錄檔多一行,
「被殺又自動重開」的無限迴圈才看得出來(數起動行數就知道)。

  uv run python tools/install_service.py            # 裝好並啟動
  uv run python tools/install_service.py --print    # 只印 plist,不裝
  uv run python tools/install_service.py --remove   # 停掉並移除

查狀態:grep -c 起動 <紀錄檔>(數字一直長 = 在無限重啟);launchctl list | grep jobsalvo(第二欄是上次的結束碼)。
"""
import os, sys, argparse, subprocess
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf

DEFAULT_HOME = '~/jobsearch'


def label(home=None):
    """開機啟動的服務名稱。預設資料夾(~/jobsearch)照舊叫 dev.jobsalvo.board-server;同一台電腦的其他資料夾
    (再裝一份試用)各自加上資料夾短碼,不然裝第二份會蓋掉第一份的服務、把它指到另一個資料夾。"""
    import hashlib
    home = os.path.realpath(os.path.expanduser(home or cf.HOME))
    if home == os.path.realpath(os.path.expanduser(DEFAULT_HOME)):
        return 'dev.jobsalvo.board-server'
    return 'dev.jobsalvo.board-server.' + hashlib.blake2b(home.encode('utf-8'), digest_size=4).hexdigest()


def plist_path(home=None):
    return os.path.expanduser(f'~/Library/LaunchAgents/{label(home)}.plist')


LABEL = label()
PLIST = plist_path()


def plist():
    e = lambda s: escape(str(s))
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>{e(os.path.join(HERE, 'board_serve.sh'))}</string>
  </array>
  <key>WorkingDirectory</key><string>{e(cf.HOME)}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>JOBSALVO_HOME</key><string>{e(cf.HOME)}</string>
    <key>JOBSALVO_LAUNCHD</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <!-- 起來就崩的話至少隔 30 秒再試,不要變成每秒重開的迴圈 -->
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>{e(cf.LOG)}</string>
  <key>StandardErrorPath</key><string>{e(cf.LOG)}</string>
</dict>
</plist>
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--print', dest='pr', action='store_true')
    ap.add_argument('--remove', action='store_true')
    a = ap.parse_args()
    if a.pr:
        print(plist()); return 0
    dom = f'gui/{os.getuid()}'
    subprocess.run(['launchctl', 'bootout', f'{dom}/{LABEL}'], capture_output=True)
    if a.remove:
        if os.path.exists(PLIST):
            os.remove(PLIST)
        print('已停掉並移除。'); return 0
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    with open(PLIST, 'w', encoding='utf-8') as f:
        f.write(plist())
    r = subprocess.run(['launchctl', 'bootstrap', dom, PLIST])
    print(f'{"已裝好" if r.returncode == 0 else "裝好了但啟動失敗"}:{PLIST}\n看板:http://localhost:{cf.PORT}')
    return r.returncode


if __name__ == '__main__':
    sys.exit(main())
