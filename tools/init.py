#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init —— 開一個新的資料夾給 jobsalvo 用:設定、空看板、偏好與填表做法的範本。

一般不用手跑:在空資料夾起看板(board_server)時會自己做這一步。要先建好再說才用它:
  uv run python tools/init.py ~/jobsearch        # 已經有的檔不動(只補還沒設過的 agent 清單和埠)

資料夾裡的東西都是你的(看板、職缺摘要、履歷、偏好),jobsalvo 的程式不會放進去,也不會 commit 它們。
"""
import os, sys, shutil, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(os.path.dirname(HERE), 'examples')
sys.path.insert(0, HERE)


def empty_board(path):
    import board_doc as bd
    import config as cf
    read = lambda n: open(os.path.join(cf.BOARD_SRC, n), encoding='utf-8').read()
    data = {'jobs': [], 'status': {}, 'research': []}
    doc = bd.assemble(read('board.css'), bd.stat_first(read('header.html'), data), '', data, '{}', read('board.js'))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(doc)


def installed_agents(which=None):
    """新資料夾的 agent 清單照這台電腦裝了什麼來定。預設是 Codex,只裝了 Claude Code 的人以前連安裝都過不了
    (環境檢查要 codex 指令)。依序看 Codex、Claude Code、Command Code:先挑裝了、而且沒查到沒登入的那一個
    (跟環境檢查同一套判斷;ChatGPT App 裡就附了 codex,只看找不找得到,用 Claude 的人會被選成沒登入的 Codex);
    都沒登入才挑第一個裝了的;都沒裝回 [](留給環境檢查講清楚要裝什麼)。"""
    import agent_run as ar
    import doctor
    which = which or shutil.which
    found = [(rt, path) for rt, path in (('codex', ar.codex_bin(which, ar.CODEX_APP_BINS)),
                                          ('claude-code', ar.claude_bin(which, ar.CLAUDE_BINS)),
                                          ('command-code', which('command-code'))) if path]
    if not found:
        return []
    pick = next((rt for rt, path in found if doctor._logged_in(rt, path) is not False), found[0][0])
    # 幫你填表要 Sonnet 或 Opus(Claude 的 Chrome 擴充功能會擋 Haiku);模型留空用帳號預設。Command Code 不能開瀏覽器
    return [{'id': 'primary', 'runtime': pick, 'model': '', 'effort': 'max', 'speed': 'standard',
             'browser': pick != 'command-code'}]


def own_paths(config_path, home):
    """新資料夾自己的暫存資料夾、agent Chrome 的連線紀錄。預設值(/tmp/jobsalvo、~/.cache/jobsalvo/agent-chrome.json)
    是全機器共用的:同一台電腦第二份資料夾(試用、給家人用)會跟第一份共用進度檔,開跑準備區時還會把
    另一份正在跑的那一隻當成「上一個」殺掉;連 agent Chrome 會蓋掉另一份的連線。已經在用的資料夾不改,
    只有新建的這份寫進自己的路徑。"""
    import hashlib, json
    tag = hashlib.blake2b(home.encode('utf-8'), digest_size=4).hexdigest()
    with open(config_path, encoding='utf-8') as f:
        data = json.load(f)
    data.setdefault('paths', {}).setdefault('tmp', f'/tmp/jobsalvo-{tag}')
    data.setdefault('browser', {}).setdefault('state', f'~/.cache/jobsalvo/agent-chrome-{tag}.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def fill_unset(config_path):
    """他還沒設過的兩樣照現在的狀況補上;設過的是他的,不動。設定檔讀不懂也不動(環境檢查會講壞在哪)。
    - agent 清單(沒有 agent.agents、也沒有舊格式的欄位):以前只在新建資料夾那一次判斷,先跑安裝、之後才裝
      Claude Code 的人,重跑安裝還是預設的 Codex,環境檢查永遠過不了。
    - 埠:安裝時用 JOBSALVO_PORT 換了埠就記進 board.port。以前只有安裝那一次用到,開機自動啟動(board_serve.sh
      不帶 --port)和 install_service 印的網址又回到 8899,撞到另一份或起不來。"""
    import json
    try:
        with open(config_path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict) or any(not isinstance(data.get(k) or {}, dict) for k in ('agent', 'board')):
        return
    changed = False
    agent = data.get('agent') or {}
    if not ('agents' in agent or any(k in agent for k in ('runtime', 'model', 'effort', 'alt_runtime', 'alt_model'))):
        agents = installed_agents()
        if agents:
            data.setdefault('agent', {})['agents'] = agents
            changed = True
    port = os.environ.get('JOBSALVO_PORT', '').strip()
    if port.isdigit() and 'port' not in (data.get('board') or {}):
        data.setdefault('board', {})['port'] = int(port)
        changed = True
    if not changed:
        return
    tmp = config_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, config_path)


def scaffold(home):
    """在 home 建好缺的檔,回新建了哪些。"""
    import config as cf
    home = os.path.abspath(os.path.expanduser(home))
    os.makedirs(home, exist_ok=True)
    made = []
    for n in ('jobsalvo.json', 'prefs.md', 'apply-rules.md'):
        p = os.path.join(home, n)
        if not os.path.exists(p):
            shutil.copyfile(os.path.join(EXAMPLES, n), p)
            made.append(n)
            if n == 'jobsalvo.json':
                own_paths(p, home)
    fill_unset(os.path.join(home, 'jobsalvo.json'))        # 新建的、重跑安裝時還沒設過的都補
    if os.path.abspath(cf.HOME) != home:
        cf.reload(home)
    if not os.path.exists(cf.LIVE):
        empty_board(cf.LIVE)
        made.append(os.path.relpath(cf.LIVE, home))
    for d in (cf.SUMS, cf.SHIP_DIR, cf.PREPARE_DIR):
        os.makedirs(d, exist_ok=True)
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('home', nargs='?', default='.')
    a = ap.parse_args()
    os.environ['JOBSALVO_HOME'] = os.path.abspath(os.path.expanduser(a.home))
    made = scaffold(a.home)
    import config as cf
    print(f'資料夾:{cf.HOME}')
    print('新建:' + ('、'.join(made) if made else '(都已經有了,沒動)'))
    print(f'接下來:cd {cf.HOME} && python3 {os.path.join(HERE, "board_server.py")},打開 http://localhost:{cf.PORT},'
          '到「⚙ 設定」上傳履歷、寫硬規則。')


if __name__ == '__main__':
    main()
