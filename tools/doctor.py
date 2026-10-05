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
    'ps': 'macOS 內建',
    'open': 'macOS 內建', 'lsappinfo': 'macOS 內建', 'launchctl': 'macOS 內建',
    'osascript': 'macOS 內建', 'qlmanage': 'macOS 內建', 'screencapture': 'macOS 內建',
    '/usr/bin/log': 'macOS 內建;只有驗收工具查是不是使用者自己切了桌面',
    'gh': '只有開發時開 issue 用,使用者不需要',
    'tailscale': '選用:沒裝就只開在本機 127.0.0.1',
    'pdftoppm': '只在 Linux 的 CI 上代替 macOS 的 qlmanage',
    'google-chrome': '開發用(看板檢查、介面截圖);使用者的功能都走 ego', 'google-chrome-stable': '同上',
    'chromium': '同上', 'chromium-browser': '同上',
}
# 下面實際檢查的外部指令
CHECKED = {'git', 'codex', 'claude', 'command-code', 'uv', 'ego-browser'}


FIREWALL = '/usr/libexec/ApplicationFirewall/socketfilterfw'


def _answers(host, port):
    """連 host:port 拿一次看板首頁的回應;連不上、連上被切斷都回 False。"""
    import http.client
    try:
        c = http.client.HTTPConnection(host, port, timeout=5)
        c.request('HEAD', '/')
        c.getresponse()
        c.close()
        return True
    except (OSError, http.client.HTTPException):
        return False


def firewall_problem(ip, port, answers=_answers):
    """看板開給手機(Tailscale)時,macOS 防火牆有沒有擋掉看板。回 None 或一句原因。
    不看防火牆清單:uv 的 Python 沒有正式身分,清單上的路徑對不上實際放行的那一支(3.14 會沿用 3.11 那一條),照清單判斷會誤報。
    改成實際連:這台 Mac 連自己的 Tailscale 位址會經過防火牆,被擋就是連上就被切斷;本機 127.0.0.1 不經過防火牆。"""
    if answers(ip, port):
        return None
    if not answers('127.0.0.1', port):
        return None   # 看板本身沒在跑,沒辦法判斷
    return '本機連得到看板,走 Tailscale 位址就連不到:多半是 Mac 防火牆擋掉了跑看板的 Python,手機會連不上'


def _blocked_pythons(read=None):
    """防火牆清單裡設成擋掉的 Python。uv 的 Python 沒有正式身分,好幾個版本共用清單上的同一條,
    路徑可能是別的版本(實測:3.14 被擋時,清單上擋掉的那一條寫的是 3.11 的路徑;對 3.14 的路徑下解除沒有作用)。"""
    import re
    try:
        out = read() if read else subprocess.run([FIREWALL, '--listapps'], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [p for p, v in re.findall(r'^\s*\d+\s*:\s*(.+?)\s*\n\s*\((Allow|Block) incoming connections\)', out or '', re.M)
            if v == 'Block' and os.path.basename(p).startswith('python')]


def firewall_fix(exe, read=None):
    """給他貼的解法:清單上有擋掉的 Python 就解除那幾條(解除要對清單上的路徑);沒有就把這一支加進去並允許。"""
    blocked = _blocked_pythons(read)
    lines = ([f'sudo {FIREWALL} --unblockapp {shlex.quote(p)}' for p in blocked] if blocked else
             [f'sudo {FIREWALL} --add {shlex.quote(exe)}', f'sudo {FIREWALL} --unblockapp {shlex.quote(exe)}'])
    return ('在畫面下方的終端機一次貼一行、按 Enter;出現 Password: 就打開機密碼(畫面不會顯示,照打再按 Enter):\n'
            + '\n'.join(lines) + '\n做完重新整理手機上的看板。'
            '或到「系統設定 → 網路 → 防火牆 → 選項」,把 Python 那一條改成「允許連入連線」。')


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


def _folder_history_row():
    """資料夾不自動存版、或舊資料因為沒有退回點沒轉:照實寫原因和怎麼處理。沒事(或資料夾還沒建)不列。
    不擋安裝(required False),但看板上標 ⚠️ 並攤開(warn)。"""
    import folder_history
    if not os.path.isdir(cf.HOME):
        return None
    try:
        status = folder_history.status(cf.HOME)
    except (OSError, RuntimeError) as exc:
        status = {'message': '版本紀錄狀態讀不到：' + str(exc)[:120], 'fix': ''}
    problems, fixes = [], []
    if status.get('conversion'):
        problems.append(status['conversion'])
        fixes.append('先照這一列說的讓版本紀錄能存，或讓資料夾裡能建 ' + folder_history.BACKUP_DIR +
                     ' 資料夾，再重新啟動看板：會先留退回點再轉。還沒轉之前照舊格式在跑（看板舊卡片的投遞狀態可能不準）。')
    if status.get('fix') or status['message'].startswith(('版本紀錄未啟用', '版本紀錄失敗', '版本紀錄狀態讀不到')):
        problems.append(status['message'])
        fixes.append(status.get('fix') or '')
    if not problems:
        return None
    return {'key': 'folder_history', 'label': '資料夾版本紀錄', 'ok': False, 'required': False, 'warn': True,
            'detail': '；'.join(problems), 'fix': ' '.join(f for f in fixes if f)}


def check_environment(agents=None):
    """Check Python and only the runtimes in the configured agent list.

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

    git = shutil.which('git')
    checks.append({
        'key': 'git', 'label': 'git', 'ok': bool(git),
        'detail': git or '找不到 git 指令(更新、資料夾版本紀錄都靠它)',
        'fix': '' if git else '在終端機跑 xcode-select --install 裝好 Apple 的開發者工具(裡面有 git)。',
    })

    history = _folder_history_row()
    if history:
        checks.append(history)

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

    # 手機走 Tailscale 連看板:macOS 防火牆會安靜擋掉跑看板的 Python(換 Python 版本後實際發生過,手機只會轉圈)
    if sys.platform == 'darwin' and os.path.isfile(venv):
        import board_server
        ip = board_server.tailscale_ip()
        if ip:
            exe = os.path.realpath(venv)
            problem = firewall_problem(ip, cf.PORT)
            checks.append({
                'key': 'firewall', 'label': '手機連得到看板(Mac 防火牆)', 'ok': not problem, 'required': False,
                'detail': problem or '從 Tailscale 位址連得到看板(看板沒在跑時不檢查)',
                'fix': '' if not problem else firewall_fix(exe),
            })

    # 看板是背景服務時,agent 的登入位置、PATH 要從登入 shell 補(shell_env,#384);讀不到,agent 會被當成沒登入
    import shell_env
    if shell_env.STATUS is not None:
        checks.append({
            'key': 'service_env', 'label': '看板讀到你終端機的設定(agent 登入位置、PATH)', 'ok': not shell_env.STATUS,
            'required': False,
            'detail': shell_env.STATUS or '開看板時從登入 shell 讀到了',
            'fix': '' if not shell_env.STATUS else '在終端機確認登入 shell 打得開(echo $SHELL),再到「⚙ 其他」關掉、重新開機自動啟動看板。',
        })

    # 設定勾了會用 Chrome、而且那一種真的能用才算(以前只看設定:沒裝任何 agent 也寫「已設定」)
    import chrome_door
    browser_runtimes = {agent.get('runtime') for agent in agents
                        if isinstance(agent, dict) and agent.get('browser') is True
                        and agent.get('runtime') in chrome_door.DOORS and states[agent['runtime']][0]}
    browser_ready = bool(browser_runtimes)
    checks.append({
        'key': 'browser_agent',
        'label': '瀏覽器 agent（選用）',
        'ok': browser_ready,
        'required': False,
        'detail': ('已設定會操作瀏覽器的 agent' if browser_ready else
                   '清單中沒有支援瀏覽器的 agent；幫你填表與查應徵進度無法使用，找缺不受影響。'),
        'fix': ('' if browser_ready else
                '需要幫你填表或查應徵進度時，在設定頁新增或啟用會操作瀏覽器的 Codex 或 Claude Code agent。'),
    })
    if browser_ready:
        # agent 清單設好了還不夠:ego 要裝好、匯入過、開著,指令真的跑得起來(門路的 ready 一項一項看,講缺哪一項、怎麼補)
        linked, reason, need = chrome_door.EgoDoor().ready()
        checks.append({
            'key': 'ego',
            'label': '幫你填表用的 ego（選用）',
            'ok': linked,
            'required': False,
            'detail': '已連上 ego lite' if linked else reason,
            'fix': '' if linked else need + ';安裝與匯入的步驟見 docs/agent-browser.md。',
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
