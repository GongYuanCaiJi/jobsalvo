#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check the local tools needed by the configured jobsalvo agents."""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

import config as cf


# runtime → (指令, 名稱, 安裝方式, 登入方式)
RUNTIMES = {
    'codex': ('codex', 'Codex CLI', '安裝 Codex CLI,並確認它在 PATH 裡。', '在終端機跑 codex login'),
    'claude-code': ('claude', 'Claude Code', '安裝 Claude Code(官方安裝指令見 code.claude.com)。',
                    '在終端機跑 claude,照畫面登入'),
    'command-code': ('command-code', 'Command Code', '安裝 Command Code,並確認它在 PATH 裡。', ''),
}

# 程式會呼叫、但環境檢查不用查的外部指令,寫明為什麼(tests/test_doctor_coverage.py 會對:
# 程式裡新呼叫了一個外部指令,既不是下面檢查的那幾項、也不在這張表,CI 就失敗)
NOT_CHECKED = {
    'ps': 'macOS 內建', 'open': 'macOS 內建', 'lsappinfo': 'macOS 內建', 'launchctl': 'macOS 內建',
    'osascript': 'macOS 內建', 'qlmanage': 'macOS 內建', 'screencapture': 'macOS 內建',
    'gh': '只有開發時開 issue 用,使用者不需要',
    'tailscale': '選用:沒裝就只開在本機 127.0.0.1',
    'pdftoppm': '只在 Linux 的 CI 上代替 macOS 的 qlmanage',
}
# 下面實際檢查的外部指令
CHECKED = {'git', 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser',
           'codex', 'claude', 'command-code', 'uv'}


def _runtime_path(runtime, which=None):
    import agent_run
    which = which or shutil.which      # 呼叫時才取:寫成預設值會在定義時綁死,測試換不掉
    if runtime == 'codex':
        return agent_run.codex_bin(which)       # ChatGPT App 更新換了位置時,PATH 上的捷徑會斷,那邊會去 App 裡找
    if runtime == 'claude-code':
        return agent_run.claude_bin(which)      # 官方安裝程式可能放在 ~/.claude/local,不一定在 PATH 上
    return which(RUNTIMES[runtime][0])


def _logged_in(runtime, path):
    """有沒有登入:只讀本機的登入紀錄(官方的 status 指令),不送請求、不花額度;額度是使用者自己的事,不查。
    回 True / False;查不到(指令沒有 status、輸出看不懂、逾時)回 None,當作不擋。"""
    try:
        if runtime == 'codex':      # 官方文件:有憑證時結束碼 0
            return subprocess.run([path, 'login', 'status'], capture_output=True, text=True, timeout=15).returncode == 0
        if runtime == 'claude-code':
            r = subprocess.run([path, 'auth', 'status'], capture_output=True, text=True, timeout=15)
            return bool(json.loads(r.stdout).get('loggedIn'))
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return None
    return None


def agent_state(runtime):
    """(能不能用, 一句話)。能用 = 裝了、而且沒有明確查到沒登入。"""
    command, name, install, login = RUNTIMES[runtime]
    path = _runtime_path(runtime)
    if not path:
        return False, f'找不到 {command} 指令'
    state = _logged_in(runtime, path)
    if state is False:
        return False, f'裝了({path}),但還沒登入'
    return True, path + ('' if state else '(登入狀態查不到,先當能用)')


def check_environment(agents=None):
    """Check Python, Chrome, and only the runtimes in the configured agent list.

    The browser capability row is informational: its absence disables applying
    and reply checks, but does not prevent searching for jobs.
    """
    if agents is None:
        agent_settings = cf.C.get('agent') or {}
        agents = agent_settings.get('agents') or []

    checks = []

    broken = cf.settings_problem()
    if broken:
        checks.append({
            'key': 'settings_file', 'label': '設定檔讀得懂', 'ok': False,
            'detail': f'{cf.NAME} 格式壞了({broken}),現在全部照預設值在跑',
            'fix': f'用文字編輯器照上面說的位置修好 {cf.NAME};或在設定頁存一次,'
                   f'原本那份會留成 {cf.NAME}.broken-日期,可以對照拿回來。',
        })

    py_ok = sys.version_info >= (3, 11)
    checks.append({
        'key': 'python', 'label': 'Python 3.11 以上', 'ok': py_ok,
        'detail': sys.version.split()[0],
        'fix': '' if py_ok else '安裝 Python 3.11 或更新版本，重新執行安裝程式。',
    })

    import chrome_bin
    chrome_path = chrome_bin.find()   # 跟開 agent 的 Chrome、印 PDF 同一套找法
    checks.append({
        'key': 'chrome', 'label': 'Google Chrome', 'ok': bool(chrome_path),
        'detail': chrome_path or '找不到 Chrome',
        'fix': '' if chrome_path else '安裝 Google Chrome 後重新執行；若裝在自訂位置，設定 CHROME_BIN。',
    })

    git = shutil.which('git')
    checks.append({
        'key': 'git', 'label': 'git', 'ok': bool(git),
        'detail': git or '找不到 git 指令(更新、資料夾版本紀錄都靠它)',
        'fix': '' if git else '在終端機跑 xcode-select --install 裝好 Apple 的開發者工具(裡面有 git)。',
    })

    # 要的是「至少有一個能用的 agent」,不是「一定要某一種」:派工時照清單順序,用不了的會換下一個。
    # 每一種各自一列只是講清楚狀態;真正擋人的只有最後那一列。
    configured_runtimes = list(dict.fromkeys(
        agent.get('runtime') for agent in agents
        if isinstance(agent, dict) and agent.get('runtime') in RUNTIMES
    ))
    states = {}
    for runtime in configured_runtimes:
        command, name, install, login = RUNTIMES[runtime]
        ok, detail = states[runtime] = agent_state(runtime)
        path_found = not detail.startswith('找不到')
        path = _runtime_path(runtime) if path_found and not ok else ''
        if path and not shutil.which(command):
            # 只在 App 裡或官方安裝位置找到(不在 PATH 上):照指令名打只會 command not found,寫實際路徑
            login = login.replace(f'跑 {command}', f'跑 {shlex.quote(path)}', 1)
        checks.append({
            'key': runtime.replace('-', '_'),
            'label': f'{name}(設定裡的 agent)',
            'ok': ok, 'required': False, 'detail': detail,
            'fix': '' if ok else (login + '。' if path_found and login else install),
        })
    usable = [r for r in configured_runtimes if states[r][0]]
    agent_row = {'key': 'agent', 'label': '至少一個能用的 agent', 'ok': bool(usable)}
    if usable:
        agent_row.update(detail='會用 ' + '、'.join(RUNTIMES[r][1] for r in usable), fix='')
    else:
        # 設定裡的都用不了,但這台電腦上有別的能用:直接給一顆「改用」
        other = next((r for r in RUNTIMES if r not in configured_runtimes and agent_state(r)[0]), None)
        if other:
            name = RUNTIMES[other][1]
            agent_row.update(detail=f'設定裡的 agent 都用不了,但這台電腦上的 {name} 可以用',
                             fix=f'按「改用 {name}」,或到設定頁「🤖 Agent 與瀏覽器」自己改。',
                             action={'use_runtime': other, 'label': f'改用 {name}'})
        else:
            agent_row.update(detail='沒有裝好、登入好的 agent;找缺、準備履歷、幫你填表都要派 agent',
                             fix='裝 Codex CLI 或 Claude Code 其中一個並登入(Codex:codex login;Claude:在終端機跑 claude)。'
                                 '裝好之後在設定頁「🤖 Agent 與瀏覽器」選它。')
    checks.append(agent_row)

    # 轉 PDF、讀 PDF、截圖用的套件:uv 照 pyproject.toml / uv.lock 裝在 repo 的 .venv(安裝、更新、看板啟動時都會對齊)
    uv = shutil.which('uv')
    venv = os.path.join(os.path.dirname(HERE), '.venv', 'bin', 'python3')
    ready = bool(uv) and os.path.isfile(venv)
    checks.append({
        'key': 'packages', 'label': '轉 PDF、截圖用的套件(選用)', 'ok': ready, 'required': False,
        'detail': ('已經裝好(uv)' if ready else '還沒準備好:這台電腦沒有 uv' if not uv else '還沒準備好:還沒跑過 uv sync'),
        'fix': '' if ready else ('裝 uv:brew install uv,再重新啟動看板。' if not uv else '在程式資料夾跑 uv sync,再重新啟動看板。'),
    })

    # 設定勾了會用 Chrome、而且那一種真的能用才算(以前只看設定:沒裝任何 agent 也寫「已設定」)
    browser_runtimes = {agent.get('runtime') for agent in agents
                        if isinstance(agent, dict) and agent.get('browser') is True
                        and agent.get('runtime') in ('codex', 'claude-code') and states[agent['runtime']][0]}
    browser_ready = bool(browser_runtimes)
    checks.append({
        'key': 'browser_agent',
        'label': '瀏覽器 agent（選用）',
        'ok': browser_ready,
        'required': False,
        'detail': ('已設定可使用 Chrome 的 agent' if browser_ready else
                   '清單中沒有支援瀏覽器的 agent；幫你填表與查應徵進度無法使用，找缺不受影響。'),
        'fix': ('' if browser_ready else
                '需要幫你填表或查應徵進度時，在設定頁新增或啟用可使用 Chrome 的 Codex 或 Claude Code agent。'),
    })
    if browser_ready:
        # agent 清單設好了還不夠:agent 自己的 Chrome 要裝 Codex 外掛、連過一次。只讀本機紀錄,不開 Chrome。
        import agent_chrome
        import agent_run
        linked = (('codex' in browser_runtimes and agent_chrome._mine())
                  or ('claude-code' in browser_runtimes and bool(agent_run.claude_paired_device())))
        checks.append({
            'key': 'agent_chrome',
            'label': '幫你填表用的 Chrome（選用）',
            'ok': linked,
            'required': False,
            'detail': ('已連接過 agent 專用的 Chrome(自己一個程序,在背景開,不會出現在你的畫面上)' if linked else
                       '還沒連接;幫你填表與查應徵進度會先停著，找缺不受影響。'),
            'fix': ('' if linked else
                    '到看板「⚙ 設定 → 🤖 Agent 與瀏覽器」:1. 按「🔑 打開 agent 的 Chrome」,在那個視窗裝 Codex 的擴充功能(商店上叫 ChatGPT)'
                    '(或 Claude 的擴充功能)、登入要用的網站 2. 按「🔌 連接」。'
                    '一步一步的教學:docs/agent-chrome.md'),
        })
    if 'codex' in browser_runtimes:
        # Codex 上傳、下載前會先問「允許嗎?」,背景沒人能按就卡住:只讀它的設定看允許了沒,不替他寫
        import agent_chrome
        # 要允許哪些網站從他自己的卡和平台履歷推出來(每個人投的網站不一樣);沒允許,第一次傳履歷就卡住,所以是必要的
        missing = agent_chrome.codex_sites_missing()
        words = {'uploads': '上傳履歷', 'downloads': '下載平台附件'}
        checks.append({
            'key': 'codex_sites',
            'label': 'Codex 可以在你要投的網站上傳、下載',
            'ok': not missing,
            'detail': ('你要投的網站都允許了(還沒有要投的卡時也算)' if not missing else
                       ';'.join(words[k] + '還沒允許:' + '、'.join(v) for k, v in missing.items())),
            'fix': '' if not missing else ('把下面這一段貼進 ~/.codex/browser/config.toml(已經有這幾段就整段換掉,原本允許的網站都留著):\n'
                                           + agent_chrome.codex_sites_snippet(missing)),
        })

    return {
        'ok': all(item['ok'] for item in checks if item.get('required', True)),
        'checks': checks,
    }


def main(argv=None):
    """結束碼:0 通過;2 只差看板設定頁上的一顆「改用 X」(安裝程式照樣啟動看板讓他按);1 其他沒過。"""
    parser = argparse.ArgumentParser(description='檢查 jobsalvo 的執行環境')
    parser.add_argument('--json', action='store_true', help='輸出機器可讀 JSON')
    args = parser.parse_args(argv)
    result = check_environment()
    failed = [item for item in result['checks'] if item.get('required', True) and not item['ok']]
    board_fix = bool(failed) and all(item.get('action') for item in failed)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result['checks']:
            status = '通過' if item['ok'] else ('提醒' if item.get('required') is False else '未通過')
            print(f"{status}｜{item['label']}：{item['detail']}")
            # 終端機沒有按鈕可按:有「改用 X」的那一列改講去看板哪裡按
            fix = (f"打開看板,在「⚙ 設定」的「🩺 環境檢查」按「{item['action']['label']}」。"
                   if item.get('action') else item['fix'])
            if fix:
                print(f"  處理方式：{fix}")
        print('環境檢查通過' if result['ok'] else '環境尚未就緒')
    return 0 if result['ok'] else 2 if board_fix else 1


if __name__ == '__main__':
    raise SystemExit(main())
