#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_service —— 讓看板開機自己起、被殺自己回來(macOS launchd)。

產生 ~/Library/LaunchAgents/dev.jobsalvo.board-server.plist,指向這份 jobsalvo 和你的資料夾(JOBSALVO_HOME),
再 launchctl 載入。跑 tools/board_serve.sh 而不是直接跑 python:每次起動都會在紀錄檔多一行,
「被殺又自動重開」的無限迴圈才看得出來(數起動行數就知道)。

  uv run python tools/install_service.py            # 裝好並啟動(安裝指令在背景起的看板會先停掉,由它接手)
  uv run python tools/install_service.py --print    # 只印 plist,不裝
  uv run python tools/install_service.py --remove   # 停掉並移除

查狀態:grep -c 起動 <紀錄檔>(數字一直長 = 在無限重啟);launchctl list | grep jobsalvo(第二欄是上次的結束碼)。
"""
import os, sys, time, signal, argparse, subprocess, contextlib, plistlib

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
    return plistlib.dumps({
        'Label': LABEL,
        'ProgramArguments': ['/bin/sh', os.path.join(HERE, 'board_serve.sh')],
        'WorkingDirectory': cf.HOME,
        'EnvironmentVariables': {'JOBSALVO_HOME': cf.HOME, 'JOBSALVO_LAUNCHD': '1'},
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 30,   # 起來就崩的話至少隔 30 秒再試,不要變成每秒重開的迴圈
        'StandardOutPath': cf.LOG,
        'StandardErrorPath': cf.LOG,
    }).decode('utf-8')


def stop_manual_board(home=None):
    """安裝指令用 nohup 在背景起的看板(pid 記在 <資料夾>/.jobsalvo-server.pid)沒有終端機可以按 Ctrl-C。
    裝開機自動啟動之前先把它停掉:不然兩個搶同一個埠,launchd 起的那個綁不到、每 30 秒重試一次。
    只停真的是看板的那個程序(pid 可能早被別的程式拿去用);叫這支程式的就是那個看板時(設定頁的按鈕)不動它,
    看板回完話會自己關。回停掉的 pid,沒停回 None。"""
    pidfile = os.path.join(home or cf.HOME, '.jobsalvo-server.pid')
    try:
        with open(pidfile, encoding='utf-8') as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    if pid == os.getppid():
        return None
    command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout or ''
    if 'board_server.py' not in command:
        return None
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):              # 等它真的放掉埠,launchd 那個一起來就綁得到
            time.sleep(0.1)
            os.kill(pid, 0)
    except ProcessLookupError:
        pass
    with contextlib.suppress(OSError):   # 已經不在了
        os.remove(pidfile)
    return pid


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
    stopped = stop_manual_board()
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    with open(PLIST, 'w', encoding='utf-8') as f:
        f.write(plist())
    r = subprocess.run(['launchctl', 'bootstrap', dom, PLIST])
    print(f'{"已裝好" if r.returncode == 0 else "裝好了但啟動失敗"}:{PLIST}\n看板:http://localhost:{cf.PORT}'
          + (f'\n原本在背景跑的看板(pid {stopped})停掉了,改由開機自動啟動接手。' if stopped else ''))
    return r.returncode


if __name__ == '__main__':
    sys.exit(main())
