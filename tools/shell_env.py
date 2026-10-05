#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shell_env —— 背景服務(launchd)補上使用者在 shell 裡設的環境變數。

launchd 啟動的看板不讀 ~/.zshrc、~/.zprofile 這些檔:使用者把 agent 的登入資料放在別處(CODEX_HOME、
CLAUDE_CONFIG_DIR…)、或把 agent 的指令裝在只有 shell 才加進 PATH 的地方,看板派的 agent 就找不到,
被當成沒登入(#384)。做法跟 VS Code 從 Dock 開啟時一樣:開一個登入、互動的 shell 印出環境變數,補進自己的環境。
"""
import os
import subprocess

MARK = '__JOBSALVO_SHELL_ENV__'
SKIP = {'PWD', 'OLDPWD', 'SHLVL', '_', 'TERM', 'TERM_PROGRAM', 'TERM_SESSION_ID'}
STATUS = None        # None:這個行程沒有補(不是背景服務);'':補好了;其他:讀不到的原因(環境檢查照實寫)


def login_env(shell=None, timeout=15):
    """使用者的登入 shell 看到的環境變數。讀 rc 檔時印出來的雜訊用記號隔開,不會混進來。"""
    shell = shell or os.environ.get('SHELL') or '/bin/zsh'
    r = subprocess.run([shell, '-ilc', f'printf %s {MARK}; env -0'], capture_output=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    if MARK.encode() not in r.stdout:
        raise RuntimeError(f'{os.path.basename(shell)} 沒印出環境變數(結束碼 {r.returncode})')
    out = r.stdout.split(MARK.encode(), 1)[1].decode('utf-8', 'replace')
    return dict(x.split('=', 1) for x in out.split('\0') if '=' in x)


def adopt(environ=None, shell=None):
    """把登入 shell 的環境變數補進這個行程(之後派的 agent 都繼承)。JOBSALVO_* 保留服務自己的。回讀不到的原因,好了回 ''。"""
    global STATUS
    environ = os.environ if environ is None else environ
    try:
        env = login_env(shell)
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        STATUS = str(e)[:160] or type(e).__name__
        return STATUS
    for k, v in env.items():
        if k not in SKIP and not k.startswith('JOBSALVO_'):
            environ[k] = v
    STATUS = ''
    return STATUS
