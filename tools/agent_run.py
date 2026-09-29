#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_run —— 派 agent + 等它完成的唯一入口。找職缺、判斷、跑準備區、代投、查回音全部走這裡,
不管任務是什麼,換模型只在這一處。
每個 agent 脫離 session(start_new_session)、可寫檔、web 可用。

三種 runtime(設定的 agent):
  codex        → codex exec(預設;唯一能操作瀏覽器、能代投的)
  command-code → command-code -p(找缺用網路搜尋、判斷、準備)
  claude-code  → claude -p(同上;做法照 codex:過程即時寫進紀錄、只關使用者自己裝的東西、可以接續對話)

完成判定:直接 poll 自己派出去的 Popen。
  - 不用 os.kill(pid,0):PID 會被回收再利用,假成功卡死。
  - 不用「.out 檔大小停止增長」:command-code -p 只在結束時才一次吐文字,跑的過程檔案一直是 0,會被誤判已停。
"""
import subprocess,time,os,re,sys,signal,json
from dataclasses import dataclass
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import config as cf
MODELS=['main','alt']
END_MARK="@@ROUND_DONE@@"

class AgentStartError(RuntimeError):
    def __init__(self, error):
        self.error = error
        super().__init__(str(error))


_REASON_LABELS = {
    'startup': '啟動失敗', 'quota': '額度用完', 'rate_limit': '遭到限流',
    'service': '服務中斷', 'authentication': '登入失效',
}


@dataclass(frozen=True)
class AgentResult:
    status: str
    returncode: object = None
    pid: object = None
    reason: str = None
    agent_id: str = None

    @property
    def ok(self):
        return self.status == 'completed'

    def message(self):
        if self.status == 'timeout':
            return f'{cf.AGENT} 超時(pid {self.pid})'
        if self.status == 'failed':
            return f'{cf.AGENT} 非零結束(結束碼 {self.returncode})'
        if self.status == 'unavailable':
            labels = {
                **_REASON_LABELS,
                'all_unavailable': f'所有符合條件的 {cf.AGENT} 都不能使用',
                'no_browser_agent': f'沒有設定會操作瀏覽器的 {cf.AGENT}',
                'pinned_agent_missing': f'原本的 {cf.AGENT} 已不在設定清單',
                'pinned_agent_incapable': f'原本的 {cf.AGENT} 不符合這件工作的瀏覽器需求',
            }
            return labels.get(self.reason, f'{cf.AGENT} 不能使用')
        return f'{cf.AGENT} 完成'


class AgentRunError(RuntimeError):
    def __init__(self, results):
        self.results = tuple(results)
        super().__init__('；'.join(result.message() for result in self.results))


def require_success(results):
    """阻止 caller 在 agent 失敗或超時後讀取部分輸出。"""
    if results is None:
        return results  # 舊的注入式測試 runner 以 None 表示已完成。
    batch = results if isinstance(results, (list, tuple)) else [results]
    failed = [r for r in batch if isinstance(r, AgentResult) and not r.ok]
    if failed:
        raise AgentRunError(failed)
    return results

# 每個 agent 的 prompt 前面都加這段:抓網頁走設定裡的抓網頁指令(或 curl),不准動使用者正在用的瀏覽器。
def browser_rule():
    return (
        '【抓網頁鐵律】讀取會改版的職缺、回音或其他網頁時,由你直接用可用的瀏覽器或 web tools 閱讀;'
        '不要呼叫程式預抓頁面內容的指令,不要用 shell 抓網頁正文或解析 DOM。'
        '只有程式固定檢查 HTTP 狀態或職缺代號時才交給程式。'
        '需要登入或遇到 CAPTCHA 時停止並照實回報,不要嘗試繞過。\\n\\n'
    )

# 代投(填表單、送出)是唯一需要真的操作網頁的任務。它用 Codex 自己的 Chrome 外掛,
# 只在 agent 專用的那個 Chrome 設定檔裡操作,不准碰使用者本人的 Chrome。
# 外掛開的分頁在螢幕外的視窗,不會跳出來搶畫面;設定檔怎麼藏起來看 agent_chrome.py。
# 填好的分頁標 markHandoff() 留著;核准或要改時,程式用 codex exec resume 叫回同一段對話,在原本那頁上送出或改,
# 不是做完就結束、換一隻新的從頭來。
# 這一輪關掉 playwright MCP(會自己開一個看得到的瀏覽器)和 node_repl(它的 Chrome 介面會把分頁群組放進使用者正在用的視窗),
# 只留外掛的 cua_repl。computer use(點螢幕)規矩裡禁止。
APPLY_RULE=('【瀏覽器鐵律(代投)】只准用 cua_repl 的 cua 操作 {agent} 專用的 Chrome:先跑 `await cua.listBrowsers()`,'
 '用 metadata.extensionInstanceId 是 {instance} 的那一個的 id 開分頁。其他 Chrome 是使用者本人的,絕對不准碰(不准列它的分頁、不准開、不准關)。'
 '找不到 {agent} 的 Chrome 就停下回報,不要開任何視窗。分頁一律在背景開,不要設定 visibility、不要把 Chrome 叫到前面。'
 '禁止 computer use(點螢幕、打鍵盤)、禁止啟動 /Applications 裡的任何瀏覽器、禁止 open 指令開網址、禁止 kill/pkill 任何瀏覽器行程。'
 '遇到驗證碼(CAPTCHA、hCaptcha、reCAPTCHA)或要輸入密碼:停下,照實寫進輸出,不要嘗試繞過,也不要猜密碼。\n\n')

# Claude Code 用 Claude in Chrome 擴充功能操作同一個 agent 專用的 Chrome。代投指示是照 Codex 外掛的說法寫的,
# 這裡給一張對照表,不另外維護第二份指示(兩邊一起改,不會分岔)。
# 選哪個瀏覽器:agent 的 Chrome 裡 Claude 擴充功能自己的編號(「連接 Claude」時讀出來記在 agent_chrome 的狀態檔)。
# 不用 Claude Code 記的配對:使用者自己的 Chrome 也裝了 Claude 時,配對可能指向那一個;名字(Browser 1…)會變,只認 deviceId。
APPLY_RULE_CLAUDE=('【瀏覽器鐵律(代投)】只准用 Claude in Chrome 的工具操作 {agent} 專用的 Chrome:先 list_connected_browsers,'
 '找 deviceId 是 {device} 的那一個;它的 inUse 不是 true 就用 select_browser 選它(不要用 switch_browser、不要發配對請求)。'
 '清單裡沒有這個 deviceId:立刻停下回報,一個動作都不要做。'
 '其他瀏覽器是使用者本人的,絕對不准碰。分頁用 tabs_context_mcp(createIfEmpty)開在你自己的分頁群組,不要把 Chrome 叫到前面。'
 '禁止操作瀏覽器以外的 App、禁止啟動 /Applications 裡的任何瀏覽器、禁止 open 指令開網址、禁止 kill/pkill 任何瀏覽器行程。'
 '遇到驗證碼(CAPTCHA、hCaptcha、reCAPTCHA)或要輸入密碼:停下,照實寫進輸出,不要嘗試繞過,也不要猜密碼。\n'
 # 網頁自己開的彈出視窗在 Claude 的分頁群組外,Claude 看不到(anthropics/claude-code#96516):按了只會卡住
 '網頁自己開的彈出視窗(「Apply with LinkedIn」「Sign in with Google」這類一鍵帶入、授權)你看不到也操作不了:不要按,'
 '改填頁面上的一般表單;只有這條路能投時停下,在 problems 寫明。\n'
 '下面的指示是用 Codex 外掛的說法寫的,你照這張表換成 Claude in Chrome 的做法:\n'
 '- 開背景分頁(cua.createBrowserTab)→ tabs_context_mcp createIfEmpty 或 tabs_create_mcp,再 navigate\n'
 '- 讀頁面(domSnapshot、getAXState、playwright.evaluate)→ read_page、find、get_page_text、javascript_tool\n'
 '- 填欄位、點按鈕(setValue、click、fill)→ form_input、computer(左鍵點 ref);同一頁好幾欄用 browser_batch 一次做完\n'
 '- 選檔上傳(playwright filechooser + setFiles)→ file_upload,給上傳欄的 ref 和檔案的絕對路徑\n'
 '- 交接分頁(markHandoff())→ 那個分頁不要關,把它的 tabId 寫進 fill.json 的 tab_id\n'
 '- 取平台上的檔(downloadMedia)→ javascript_tool 在頁面裡 fetch(href, {{credentials: "include"}}) 轉成 base64 回傳,'
 '再用 Bash 解碼寫進指定的暫存資料夾\n\n')


def claude_paired_device():
    """agent 的 Chrome 裡 Claude 擴充功能的 deviceId(「連接 Claude」時記下的);還沒連接回 None。"""
    import agent_chrome
    return agent_chrome.conf().get('claude_device') or None


def apply_rule(runtime='codex'):
    import json
    if runtime == 'claude-code':
        import apply_tab
        return APPLY_RULE_CLAUDE.format(agent=cf.AGENT, device=claude_paired_device()
                                        or '(還沒連接 agent 專用的 Chrome,先停下回報)') + apply_tab.CLAUDE_SELF_READ
    try:
        with open(cf.BROWSER_STATE,encoding='utf-8') as f: inst=json.load(f).get('instance')
    except (OSError,ValueError):
        inst=None
    return APPLY_RULE.format(agent=cf.AGENT,instance=inst or '(還沒設定,先停下回報)')

# 每個 agent 都要能回報給使用者:它做不到、需要本人處理的事,不能只寫在自己的紀錄檔裡。
# 回報寫進看板最上面的「📣 回報」(agent_report.py)。
def report_rule():
    return ('【回報】只回報非使用者本人不可的事:要他登入、要他給權限、要他本人點的驗證碼,而且你找不到別的路。'
     '抓不到就換別的方法抓;判斷得出來就自己判斷,照這一輪的規矩寫進產出;'
     '已經繞過去的不用報。要回報時:'
     f'`python3 {cf.tool("agent_report.py")} --from <這一輪在做什麼> --job <職缺網址,沒有就不給> --need "<他要做什麼>" "<發生了什麼>"`。'
     '用繁體中文、白話、一句講清楚。回報完照原本的規矩繼續做能做的部分。\n\n')

# 使用者在看板上撥的開關(__agentfree__):要不要放手讓 agent 自己決定派幾隻。
# 放在這裡而不是各支 prompt 裡各接一次:這裡是派工的唯一入口,一處寫完,找缺、判斷、
# 準備區、代投、查回音全部吃得到。
FREE_RULE=('【要派幾隻你自己決定】用什麼方法做、要不要拆給並行的 sub-agent、拆幾隻,全部你自己決定。'
 '你的執行環境如果派得出並行的 sub-agent,要不要用也由你決定。')
FREE_CODEX=('(這個環境有 multi_agent_v1__spawn_agent / wait_agent / send_input,'
 '要並行就在同一個 exec 裡 Promise.all 同時 spawn。)')

def agent_free(board=None):
    """開關有沒有撥開。讀正在處理的那份看板(派 agent 的程式會設 AGENT_BOARD)。"""
    try:
        import json as _j, board_doc as _bd
        live=board or os.environ.get('AGENT_BOARD') or _bd.LIVE
        with open(live,encoding='utf-8') as f:
            return bool(_j.loads(_bd.parse(f.read())['fb']).get('__agentfree__'))
    except Exception:
        return False

CODEX_CONFIG='~/.codex/config.toml'

def lean(chrome=False):
    """派 codex 時只開這一輪用得到的工具。全域設定可能掛了十幾個 MCP 和外掛,agent 一開頭會照全域指示
    先去啟用它們、讀操作手冊、翻 repo,啟動慢到拖垮整輪。這裡把 MCP 全關、外掛全關;代投(chrome=True)只留 Chrome 外掛。
    hooks 也關:全域 hooks 多半是規劃文件、壓縮 context 這類輔助,開著的話每一輪開頭都會被塞跟這些流程無關的指示。
    網頁搜尋是內建工具,不受影響。"""
    try:
        with open(os.path.expanduser(CODEX_CONFIG),encoding='utf-8') as f:
            cfg=f.read()
    except OSError:
        cfg=''
    keep={'computer-use@openai-bundled','chrome@openai-bundled',
          'unified-computer-use@openai-bundled'} if chrome else set()
    # 記憶也關:agent 每輪開頭會去搜使用者的個人記憶檔(判斷職缺那輪 7 步裡 3 步在讀它),跟這份工作無關,
    # 還會把他其他專案的東西帶進來。claude 那邊一樣關(claude_lean 的 autoMemoryEnabled)
    out=['-c','features.hooks=false','-c','features.plugin_hooks=false','-c','features.memories=false']
    for n in dict.fromkeys(re.findall(r'^\[mcp_servers\.([^.\]]+)\]',cfg,re.M)):
        out+=['-c',f'mcp_servers.{n}.enabled=false']
    for n in dict.fromkeys(re.findall(r'^\[plugins\."([^"]+)"\]',cfg,re.M)):
        if n not in keep:
            out+=['-c',f'plugins.{n}.enabled=false']   # 名稱加引號 Codex 會默默忽略,外掛就沒關掉
    # skill 也關:codex 會把這台電腦裝的所有 skill 列給 agent,它看到就想先讀說明
    # (找缺那一輪去讀了一份不相干的搜尋 skill)。這些流程要做什麼 prompt 裡都寫了。官方做法是 [[skills.config]] enabled=false
    skills=codex_skills()
    if skills:
        out+=['-c','skills.config=['+','.join('{path=%s,enabled=false}'%json.dumps(p) for p in skills)+']']
    return out

CODEX_SKILL_DIRS = ('~/.codex/skills', '~/.agents/skills')


def codex_skills():
    """codex 會自動列給 agent 的 skill(每個 SKILL.md 的完整路徑)。"""
    found,seen=[],set()
    for base in CODEX_SKILL_DIRS:
        # 很多 skill 是捷徑(symlink)資料夾:要跟進去,codex 也會;記走過的真實路徑,捷徑繞圈也不會走不完
        for root,dirs,files in os.walk(os.path.expanduser(base),followlinks=True):
            real=os.path.realpath(root)
            if real in seen:
                dirs[:]=[]
                continue
            seen.add(real)
            dirs.sort()
            if 'SKILL.md' in files:
                found.append(os.path.join(root,'SKILL.md'))
    return found

CLAUDE_SETTINGS = '~/.claude/settings.json'


def claude_lean(chrome=False):
    """跟 lean 同一件事,給 claude -p:使用者自己裝的 MCP、外掛、hooks、自動記憶這一輪都不載入,內建工具(含網頁搜尋)照常。
    不用 --safe-mode:它連 skills、CLAUDE.md、輸出風格都關,範圍比 codex 那邊大。"""
    import json
    try:
        with open(os.path.expanduser(CLAUDE_SETTINGS), encoding='utf-8') as f:
            plugins = (json.load(f).get('enabledPlugins') or {})
    except (OSError, ValueError, AttributeError):
        plugins = {}
    off = {'disableAllHooks': True, 'autoMemoryEnabled': False,
           'enabledPlugins': {name: False for name in plugins}}
    if chrome:      # 代投:Claude in Chrome 的工具和所有網站都允許(只在 agent 專用的 Chrome 裡,鐵律另外管)
        off['permissions'] = {'allow': ['mcp__claude-in-chrome', 'ClaudeInChromeDomain(*)']}
    return ['--strict-mcp-config', '--settings', json.dumps(off, ensure_ascii=False)]


def apply_overrides():
    """代投:只留 Chrome 外掛(cua_repl),其他 MCP、外掛全關(見 lean)。"""
    return lean(True)

def _events(text):
    """紀錄裡 CLI 自己吐的結構化事件(codex exec --json、claude -p stream-json 都是一行一個 JSON)。
    agent 讀到的網頁、檔案內容只會出現在事件的欄位裡面,不會變成一行事件,所以判斷只看事件本身。"""
    import json
    out = []
    for line in (text or '').splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def session_id(outfile):
    """這段對話的 id:codex 在 thread.started 事件的 thread_id,claude 在串流每一行的 session_id。
    以前在整份紀錄裡撈「session id: …」字樣,agent 讀到的頁面裡剛好有這種字就會撈錯(#109)。有好幾段(換手、接續)取最後一段。"""
    try:
        with open(outfile, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return None
    ids = [e.get('thread_id') if e.get('type') == 'thread.started' else e.get('session_id')
           for e in _events(text)]
    ids = [i for i in ids if isinstance(i, str) and i]
    return ids[-1] if ids else None

def _runtime(model):
    agent = _agent_entry(model)
    return agent.get('runtime', 'codex'), agent.get('model') or ''

def rules_for(model=None, browser=None, board=None, web=True):
    """派出去的 prompt 前面固定加的規矩;預覽與實際派工共用。"""
    codex_tools = FREE_CODEX if _runtime(model)[0] == 'codex' else ''
    free = (FREE_RULE + codex_tools + '\n\n') if agent_free(board) else ''
    return (apply_rule(_runtime(model)[0]) if browser else (browser_rule() if web else '')) + report_rule() + free


def prompt_stdin(model, prompt, browser=None, board=None, web=True, chrome=False):
    """要從 stdin 餵的完整 prompt(規矩 + 任務);prompt 放在指令參數裡的執行環境(command-code)回 None。"""
    agent = _agent_entry(model)
    if _runtime(agent)[0] not in ('claude-code', 'codex'):
        return None
    return rules_for(agent, bool(chrome and browser), board, web) + prompt


def argv_for(model, prompt, repo, effort=None, browser=None, resume=None, board=None, web=True,
             chrome=False):
    """回 (argv, cwd)。model 可為設定清單項目、項目 id 或舊的 main/alt 選擇器。
    codex、claude-code 的 prompt 不在 argv 裡,由 prompt_stdin 給。"""
    agent = _agent_entry(model)
    effort = effort or agent.get('effort') or 'max'
    has_browser = bool(chrome and browser)
    prompt = rules_for(agent, has_browser, board, web) + prompt
    rt, m = _runtime(agent)
    if rt not in RUNTIMES:
        raise ValueError(f'不支援的 agent 執行環境: {rt}')
    if rt == 'claude-code':
        # 瀏覽器:Claude in Chrome(--chrome)。codex 那份 browser 參數(-c 外掛開關)對它沒意義,不用。
        # Chrome 的每個動作有自己一道權限檢查,--dangerously-skip-permissions 也要搭配允許規則才過(claude_lean(chrome))。
        # 只有 Sonnet、Opus 過得了這道檢查;Haiku 在背景模式下 Chrome 動作一律被擋。
        # stream-json:跟 codex exec 一樣邊跑邊把每一步寫進紀錄(看紀錄、按停止都看得到跑到哪),最後一行是結果。
        # 權限全開跟 codex 的 danger-full-access 對齊:它要在資料夾裡寫檔、跑 python。
        # prompt 不放在指令參數:裡面有履歷和個資,ps 看得到;太長也會超過參數上限。launch 從 stdin 餵(見 prompt_stdin)。
        # 對話 id 由程式先給(新對話)或指定接哪一段(resume),不用事後從輸出裡撈。
        import uuid
        return ([claude_bin() or "claude", "-p", "--output-format", "stream-json", "--verbose",
                 "--dangerously-skip-permissions", "--effort", effort]
                + (["--model", m] if m else [])
                + (["--resume", resume] if resume else ["--session-id", str(uuid.uuid4())])
                + ([] if web else ["--disallowedTools", "WebSearch,WebFetch"])
                + _claude_browser(bool(has_browser or chrome))
                + ["--disable-slash-commands"], repo)      # 跟 codex 的 skills.config 一樣:這台電腦裝的 skill 不列給 agent
    if rt == 'command-code':
        if browser or resume or chrome:
            raise ValueError('Command Code 不支援瀏覽器或接續既有對話')
        tools = ['--tools-all'] if web else []
        return (["command-code", "-p", prompt] + (["-m", m] if m else []) + ["--effort", effort,
                "--skip-onboarding", "-t"] + tools + ["--yolo", "--max-turns", "500"], repo)
    # codex:prompt 從 stdin 餵(引數寫 -),跟 claude 同一個理由:裡面有履歷和個資,ps 看得到,太長也會超過上限。
    # --json:紀錄是一行一個事件,成敗、對話 id 都看事件,不在全文找字(#109)。
    common = (["--json"] + (["-m", m] if m else [])) + ["-c", f'model_reasoning_effort="{effort}"',
             "-c", f'tools.web_search={str(bool(web)).lower()}']
    if agent.get('speed') == 'fast':
        common += ["-c", 'service_tier="priority"']
    common += browser or lean(chrome)
    if resume:
        return ([codex_bin() or "codex", "exec", "resume", resume] + common + ["-c", 'sandbox_mode="danger-full-access"',
                "--skip-git-repo-check", "-"], repo)
    return ([codex_bin() or "codex", "exec"] + common + ["-s", "danger-full-access", "--skip-git-repo-check",
            "-C", repo, "-"], None)

def _claude_browser(on):
    """claude -p 的瀏覽器參數:要用瀏覽器就開 Claude in Chrome(--chrome),不用就關掉它的工具。"""
    return (['--chrome'] if on else ['--no-chrome']) + claude_lean(chrome=on)


RUNTIMES = ('codex', 'command-code', 'claude-code')

CLAUDE_BINS = ('~/.claude/local/claude', '~/.local/bin/claude')


def claude_bin(which=None, bins=CLAUDE_BINS):
    """能執行的 claude:先照 PATH,再看官方安裝程式放的位置;都沒有回 None。"""
    import shutil
    found = (which or shutil.which)('claude')
    if found:
        return found
    return next((p for p in map(os.path.expanduser, bins) if os.path.isfile(p) and os.access(p, os.X_OK)), None)


# ChatGPT 桌面版附的 codex。App 更新時會換位置(2026-09-27 從 Resources/codex 搬到 Resources/codex-cli/bin/codex),
# 使用者 PATH 上指過去的捷徑就斷了,派 agent 全部失敗。PATH 上找不到能執行的,就到這幾個地方找。
CODEX_APP_BINS = (
    '/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex',
    '/Applications/ChatGPT.app/Contents/Resources/codex',
    '/Applications/Codex.app/Contents/Resources/codex',
)


def codex_bin(which=None, app_bins=CODEX_APP_BINS):
    """能執行的 codex:先照 PATH(斷掉的捷徑不算),找不到再看 ChatGPT/Codex App 裡附的;都沒有回 None。"""
    import shutil
    found = (which or shutil.which)('codex')
    if found:
        return found
    return next((p for p in app_bins if os.path.isfile(p) and os.access(p, os.X_OK)), None)


def launch(prompt, outfile, repo, model='main', effort=None, browser=None, resume=None, board=None,
           web=True, chrome=False, append=False):
    agent = _agent_entry(model)
    argv, cwd = argv_for(agent, prompt, repo, effort, browser, resume, board, web, chrome)
    fed = prompt_stdin(agent, prompt, browser, board, web, chrome)
    # stdin 一定不能是開著的管線:CLI 看到會一直等「更多輸入」。要餵 prompt 就給一個檔,讀到尾就結束。
    stdin = subprocess.DEVNULL
    if fed is not None:
        import tempfile
        stdin = tempfile.TemporaryFile()
        stdin.write(fed.encode('utf-8'))
        stdin.seek(0)
    with open(outfile, 'a' if append else 'w', encoding='utf-8') as log:
        try:
            _attempt_line(log, agent)
            log.flush()
            env = os.environ.copy()
            if board:
                env['AGENT_BOARD'] = os.fspath(board)
            return subprocess.Popen(argv, cwd=cwd, stdin=stdin, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True, env=env)
        except OSError as e:
            raise AgentStartError(e) from e
        finally:
            if stdin is not subprocess.DEVNULL:
                stdin.close()        # 子行程已經拿到自己那一份檔案描述子

def _agent_entry(agent):
    agents = (cf.C.get('agent') or {}).get('agents') or []
    if isinstance(agent, dict):
        return agent
    if agent in (None, '', 'main'):
        return agents[0] if agents else {'id': 'primary', 'runtime': 'codex', 'model': '', 'effort': 'max', 'browser': True}
    if agent == 'alt':
        return agents[1] if len(agents) > 1 else {'id': 'secondary', 'runtime': 'command-code', 'model': '', 'effort': 'max', 'browser': False}
    return next((a for a in agents if a.get('id') == agent), agents[0] if agents else {})


def _runtime_supports_browser(runtime):
    return runtime in ('codex', 'claude-code')


def browser_runtime(agent_id=None):
    """要開瀏覽器的工作會派給哪一種執行者(第一個合格的);沒有回 None。
    代投的指示和前置(程式先開好分頁)依執行者不同:Codex 接得了程式開的分頁,Claude 只看得到自己分頁群組裡的。"""
    agents, _err = _eligible_agents(True, agent_id)
    return agents[0].get('runtime') if agents else None


def _can_browse(agent):
    return bool(agent.get('browser') and _runtime_supports_browser(agent.get('runtime')))


def _eligible_agents(browser_required, agent_id=None):
    agents = (cf.C.get('agent') or {}).get('agents') or []
    if agent_id:
        pinned = next((a for a in agents if a.get('id') == agent_id), None)
        if pinned is None:
            return [], 'pinned_agent_missing'
        if browser_required and (not pinned.get('browser') or not _runtime_supports_browser(pinned.get('runtime'))):
            return [], 'pinned_agent_incapable'
        return [pinned], None
    eligible = [a for a in agents if not browser_required or (
        a.get('browser') and _runtime_supports_browser(a.get('runtime'))
    )]
    return eligible, None if eligible else ('no_browser_agent' if browser_required else 'all_unavailable')


def _claude_outcome(text):
    """claude -p 串流最後那一行結果({"type":"result",...});找不到回 None。"""
    import json
    for line in reversed((text or '').splitlines()):
        line = line.strip()
        if line.startswith('{') and '"result"' in line:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get('type') == 'result':
                return row
    return None


def _claude_failed(text):
    """結束碼 0 也要看結果:撞到用量上限、沒登入時 claude -p 可能照樣正常結束,錯誤只寫在結果那一行。"""
    row = _claude_outcome(text)
    return row is None or bool(row.get('is_error')) or row.get('subtype') != 'success'


def _codex_failed(text):
    """結束碼 0 也要看事件:這一輪最後是 turn.failed 或 error,就是沒做完。"""
    ends = [e.get('type') for e in _events(text) if e.get('type') in ('turn.completed', 'turn.failed', 'error')]
    return bool(ends) and ends[-1] != 'turn.completed'


def _codex_error_text(text):
    """codex 自己講的錯:turn.failed / error 事件的訊息,加上不是事件的那幾行(stderr,CLI 自己印的)。
    agent 讀到的職缺頁、信件寫到 rate limit、quota 都在 item 事件裡,不算(以前在全文找字,會誤判成要換手)。"""
    msgs = []
    for line in (text or '').splitlines():
        if not line.strip().startswith('{'):
            msgs.append(line)
    for e in _events(text):
        if e.get('type') == 'turn.failed':
            msgs.append(str((e.get('error') or {}).get('message') or ''))
        elif e.get('type') == 'error':
            msgs.append(str(e.get('message') or ''))
    return '\n'.join(msgs)


def _failure_reason(runtime, returncode, output=''):
    if runtime == 'claude-code':
        row = _claude_outcome(output) or {}
        status = row.get('api_error_status')
        text = str(row.get('result') or '').lower()
        if status == 401 or any(x in text for x in ('not logged in', '/login', 'failed to authenticate',
                                                    'oauth', 'invalid api key')):
            return 'authentication'
        if any(x in text for x in ('hit your', 'usage limit', 'credit balance', 'billing')):
            return 'quota'
        if status == 429 or 'rate limit' in text:
            return 'rate_limit'
        if (isinstance(status, int) and status >= 500) or 'overloaded' in text:
            return 'service'
        return None
    if runtime == 'command-code':
        return {
            3: 'authentication', 5: 'rate_limit', 6: 'service', 7: 'service', 10: 'quota',
        }.get(returncode)
    if runtime != 'codex':
        return None
    text = _codex_error_text(output).lower()
    if any(x in text for x in ('quota exceeded', 'usage limit', 'insufficient credits', 'usage not included')):
        return 'quota'
    if any(x in text for x in ('rate limit', 'rate_limit', '429 too many requests')):
        return 'rate_limit'
    if any(x in text for x in ('unauthorized', 'authentication failed', '401 unauthorized')):
        return 'authentication'
    if any(x in text for x in (
        'server is overloaded', 'at capacity', 'high demand', 'internal server error',
        'service unavailable', 'connection failed', 'connectionfailure', 'unable to connect',
        'exceeded retry limit, last status: 5', 'http 500', 'http 502', 'http 503', 'http 504',
    )):
        return 'service'
    return None


def _log_text(path, offset=0):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(min(offset, size), size - 50000))
            return f.read()
    except OSError:
        return ''


def _agent_label(agent, index):
    runtime = {'codex': 'Codex', 'command-code': 'Command Code',
               'claude-code': 'Claude Code'}.get(agent.get('runtime'), 'agent')
    model = agent.get('model') or '預設模型'
    agents = (cf.C.get('agent') or {}).get('agents') or []
    index = next((i for i, entry in enumerate(agents, 1) if entry.get('id') == agent.get('id')), index)
    return f'{cf.AGENT} 清單第 {index} 個（{runtime}，{model}）'


def _report_authentication(agent, index, board, outfile):
    try:
        import agent_report
        agent_report.report(
            'Agent 登入', f'{_agent_label(agent, index)} 登入失效',
            need='請在這台電腦重新登入這個 agent；只有你能完成登入。',
            live=board,
        )
    except Exception as error:
        try:
            with open(outfile, 'a', encoding='utf-8') as f:
                f.write(f'\n登入失效回報未能寫進看板:{str(error)[:120]}\n')
        except OSError:
            pass


def _handoff_line(outfile, next_agent, next_index, reason):
    label = _REASON_LABELS.get(reason, reason)
    with open(outfile, 'a', encoding='utf-8') as f:
        f.write(f'\n改用 {_agent_label(next_agent, next_index)}，因為{label}\n')


def _attempt_line(log, agent):
    if agent.get('runtime', 'codex') != 'codex':
        return
    mode = '快速模式' if agent.get('speed') == 'fast' else '標準模式'
    log.write(f'{cf.AGENT}（{agent.get("id") or "Codex"}）使用 {mode}\n')


def _failure_line(outfile, agent, index, reason):
    label = _REASON_LABELS.get(reason, reason)
    with open(outfile, 'a', encoding='utf-8') as f:
        f.write(f'\n{_agent_label(agent, index)}：{label}\n')


def runs_log():
    """每派一次 agent 記一行的紀錄檔:放在看板伺服器紀錄檔旁邊(資料夾的版本紀錄不收它)。"""
    return os.path.join(os.path.dirname(cf.LOG), 'agent-runs.jsonl')


def _record(outfile, agent, index, started, result, prompt):
    """哪個流程、哪一步、哪個 agent、幾點開始、花幾秒、結果。之後問「為什麼跑那麼久」查這裡。"""
    import json
    row = {'at': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(started)),
           'secs': round(time.time() - started, 1),
           'flow': os.path.splitext(os.path.basename(sys.argv[0] or ''))[0],
           'step': os.path.splitext(os.path.basename(outfile or ''))[0],
           'agent': agent.get('id'), 'runtime': agent.get('runtime'), 'model': agent.get('model') or '',
           'try': index, 'status': result.status, 'reason': result.reason or '',
           'returncode': result.returncode, 'prompt_chars': len(prompt or ''), 'outfile': outfile}
    try:
        path = runs_log()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    except OSError as e:   # 記不下來不能拖垮 agent 本身的工作;講出來就好
        print(f'agent 執行紀錄寫不進去:{e}', file=sys.stderr)


def run(prompt, outfile, repo, model=None, timeout=4*3600, chrome=False, *,
        browser_required=False, browser=None, resume=None, board=None, web=True,
        agent_id=None, on_start=None, launcher=None, waiter=None, prefer_browser=False):
    """依序試用符合瀏覽器需求的 agent;只在 runtime 明確不可用時換手。
    prefer_browser(找缺):能開瀏覽器的排前面、開著瀏覽器做;沒有的話只能上網搜尋的也可以。"""
    browser_required = bool(browser_required or chrome)
    launch_agent = launcher or launch
    wait_for_agent = waiter or wait_done
    agents, selection_error = _eligible_agents(browser_required, agent_id)
    if prefer_browser and not browser_required:
        agents = sorted(agents, key=lambda a: not _can_browse(a))
    if not agents:
        return AgentResult('unavailable', reason=selection_error)

    for index, agent in enumerate(agents, 1):
        runtime = agent.get('runtime', '')
        try:
            log_offset = os.path.getsize(outfile) if index > 1 else 0
        except OSError:
            log_offset = 0
        started = time.time()
        try:
            proc = launch_agent(prompt, outfile, repo, agent, browser=browser, resume=resume,
                                board=board, web=web, append=index > 1,
                                chrome=browser_required or (prefer_browser and _can_browse(agent)))
        except AgentStartError:
            reason = 'startup'
            _failure_line(outfile, agent, index, reason)
            result = AgentResult('unavailable', reason=reason, agent_id=agent.get('id'))
            _record(outfile, agent, index, started, result, prompt)
        else:
            if on_start:
                on_start(proc)
            raw_result = wait_for_agent([proc], timeout)[0]
            status = getattr(raw_result, 'status', None)
            if status is None:
                status = 'completed' if getattr(raw_result, 'ok', False) else 'failed'
            result = AgentResult(
                status, getattr(raw_result, 'returncode', None),
                getattr(raw_result, 'pid', getattr(proc, 'pid', None)),
                reason=getattr(raw_result, 'reason', None), agent_id=agent.get('id'),
            )
            if result.status == 'timeout':
                _record(outfile, agent, index, started, result, prompt)
                return result
            if result.status == 'completed' and (
                    (runtime == 'claude-code' and _claude_failed(_log_text(outfile, log_offset)))
                    or (runtime == 'codex' and _codex_failed(_log_text(outfile, log_offset)))):
                result = AgentResult('failed', result.returncode, result.pid, agent_id=agent.get('id'))
            if result.status == 'failed':
                reason = _failure_reason(runtime, result.returncode, _log_text(outfile, log_offset))
                if reason is None:
                    _record(outfile, agent, index, started, result, prompt)
                    return result
                _failure_line(outfile, agent, index, reason)
                if reason == 'authentication':
                    _report_authentication(agent, index, board, outfile)
                result = AgentResult('unavailable', result.returncode, result.pid,
                                     reason=reason, agent_id=agent.get('id'))
            _record(outfile, agent, index, started, result, prompt)
            if result.ok:
                return result
            if result.status != 'unavailable':
                return result

        if index < len(agents):
            _handoff_line(outfile, agents[index], index + 1, result.reason)
            continue
        return AgentResult('unavailable', result.returncode, result.pid,
                           reason='all_unavailable', agent_id=result.agent_id)
    return AgentResult('unavailable', reason='all_unavailable')

def _group_alive(pid):
    if os.name != 'posix':
        return False
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_tree(proc, sig):
    if os.name == 'posix':
        try:
            os.killpg(proc.pid, sig)  # launch() gives each agent its own session/process group.
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        proc.terminate() if sig == signal.SIGTERM else proc.kill()
    except OSError:
        pass


def _stop_tree(proc, grace=2):
    """Stop the agent process group, escalate if needed, then reap its leader."""
    _signal_tree(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        _signal_tree(proc, signal.SIGKILL)
        proc.wait()
        return
    # The leader can exit on SIGTERM while a child in its group ignores it.
    if _group_alive(proc.pid):
        _signal_tree(proc, signal.SIGKILL)


def wait_done(procs, timeout=3600):
    """等全部 agent 結束;逐一回報成功、非零結束或超時。"""
    procs = list(procs)
    pending = list(procs)
    results = {}
    deadline = time.monotonic() + max(0, timeout)
    while pending:
        for proc in pending[:]:
            code = proc.poll()
            if code is None:
                continue
            results[id(proc)] = AgentResult('completed' if code == 0 else 'failed', code, proc.pid)
            pending.remove(proc)
        if not pending:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            for proc in pending:
                _stop_tree(proc)
                results[id(proc)] = AgentResult('timeout', None, proc.pid)
            break
        time.sleep(min(0.1, remaining))
    return [results[id(proc)] for proc in procs]
