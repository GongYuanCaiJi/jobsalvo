#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chrome_door —— 程式這一側碰「agent 的 Chrome」的門路,收在這一處:Codex 一條、Claude 一條(docs/adr/0003)。

呼叫的地方不問「是不是 Claude」,只拿一個門路來用:
  of(runtime)      那一家的門路(不能開 Chrome 的回 None)
  current()        設定裡勾「用它操作 Chrome」的那一家(派新工作、查應徵進度用)
  for_card(apply)  這張卡記的那一家(之後看、改、送出、關掉都找同一家);那一家停用或移除、卡上沒記 → Unreachable

每一個門路對外提供一樣的事(做不到的丟 NotNow,訊息講現在做不到、改用什麼):
  讀那一頁 read_page、讀平台履歷頁 read_profile / profile_reader、比對附件 attachment_hashes、截圖 shot、
  放掉分頁 release、派工前等 Chrome 準備好 ready、程式先開好申請頁 open_for_agent、給 agent 的指示要多交代什麼
  (apply_rule、fetch_rule、task_tail、profile_reread)、查應徵進度讀頁 read_pages / program_reads、
  關 Chrome 時保護哪幾頁 waiting、設定好了沒 configured、看板 👀 怎麼截(live_timeout、live_refresh、shot_while_busy)。

程式自己這一側的底層(Codex 的 cua_repl、Claude 的紀錄解析和 claude -p --resume)在 apply_tab、agent_chrome;這裡只選門路。
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CODEX, CLAUDE = 'codex', 'claude-code'


class NotNow(LookupError):
    """這一家現在做不到(不是壞了):訊息講做不到、改用什麼。"""


class Unreachable(LookupError):
    """填這張的 agent 接不回來:卡上沒記是哪一家,或那一家已經停用、移除(之後這一頁算頁面不見了,要重填)。
    訊息就是卡上要寫的那一句。sure=False:現在判斷不了(設定檔讀不懂),不准因此把卡標成頁面不見了。"""

    def __init__(self, msg, sure=True):
        super().__init__(msg)
        self.sure = sure


AGENT_SWAPPED = '填這張的 agent 換掉了,要重填'
NO_BROWSER_AGENT = '設定裡沒有能用 Chrome 的 agent:到看板「⚙ 設定 → 🤖 Agent 與瀏覽器」勾一個「用它操作 Chrome」'
# 開始記「哪一家開的」之前填的卡(#311 預設 A):不是換掉了,是程式不知道;一樣接不回來
BEFORE_UPDATE = '這張是更新前填的,程式不知道是哪個 agent 開的頁,要重填'
SETTINGS_UNREADABLE = ('設定檔 jobsalvo.json 讀不懂({why}),現在不知道填這張的 agent 還在不在;'
                       '先到看板「⚙ 設定」存一次(或修好那個檔)再試')


class _Door:
    """各家共用的預設和方法。各家自己要寫:ready、configured、open_for_agent、apply_rule、fetch_rule、read_page、
    read_profile、read_pages、attachment_hashes、shot、release、waiting(寫法看 CodexDoor;沒寫的呼叫到直接 AttributeError)。"""
    runtime = ''
    program_reads = False        # 程式能不能自己開頁讀(平台履歷、查應徵進度的信箱和平台頁)
    reads_live_page = False      # 程式能不能隨時自己讀留著的那一頁(確認前、送出前、送出後);不能的從那一輪的紀錄拿
    live_timeout = 60            # 看板 👀 截一張最多等幾秒
    live_refresh = 0             # 看板 👀 幾秒後自動再截(0:按一次截一次)
    shot_while_busy = True       # 別的工作正在用 agent 的 Chrome 時截不截得到

    def __init__(self, agent_id=None):
        self.agent_id = agent_id     # 要叫的那一個 agent(修改、送出叫回填這張的那一個)

    # ---- 共同的 ----
    def profile_reader(self, logs=None, board=None):
        """給 profile_sync.check 的讀頁函式(讀取網址 → 頁面)。"""
        return lambda read_url: self.read_profile(read_url, logs, board)

    def task_tail(self, stage):
        return ''

    def profile_reread(self, read_url):
        return ''

    def page_reread(self, tab_id):
        """送出前核對那一輪:程式自己讀不到那一頁的那一家,叫它把申請表那一頁讀給程式(docs/adr/0003)。"""
        return ''


class CodexDoor(_Door):
    """Codex:程式啟動外掛的 cua_repl、帶填那一張的那段對話的身分,隨時自己看得到那一頁(不經過模型)。"""
    runtime = CODEX
    program_reads = True
    reads_live_page = True
    live_refresh = 4

    def ready(self, board=None):
        import agent_chrome
        return (*agent_chrome.ensure(board), '按「🔌 連接 Codex」')

    def configured(self):
        import agent_chrome
        return agent_chrome.codex_configured()

    def open_for_agent(self, url, old_tab=None):
        import agent_chrome
        return agent_chrome.open_for_agent(url, old_tab=old_tab)

    def apply_rule(self):
        import config as cf
        import agent_run as ar
        import agent_chrome
        return ar.APPLY_RULE.format(agent=cf.AGENT, instance=agent_chrome.conf().get('instance') or '(還沒設定,先停下回報)')

    def fetch_rule(self):
        import profile_sync as ps
        return ps.FETCH_FILE_RULE + ps.CHOOSE_FILE_RULE

    def read_page(self, session, tab_id, logs=None):
        import apply_tab
        return apply_tab.read(session, tab_id)

    def read_profile(self, read_url, logs=None, board=None):
        import agent_chrome
        return agent_chrome.read_pages([read_url], board, ready=lambda r: len(r.get('text', '')) > 800,
                                       settle=2)[read_url]

    def read_pages(self, urls, board=None, ready=None, settle=0):
        import agent_chrome
        return agent_chrome.read_pages(urls, board, ready=ready, settle=settle)

    def attachment_hashes(self, logs):
        return None              # Codex 把檔取回來,程式逐位元組比(不用雜湊)

    def shot(self, session, tab_id, out):
        import apply_tab
        return apply_tab.shot(session, tab_id, out)

    def release(self, session, tab_id):
        import apply_tab
        return apply_tab.release(session, tab_id)

    def waiting(self, tabs, board=None):
        """這幾頁(看板上記著、Codex 開的)現在還在不在:問外掛現在有哪些分頁;問不到當成都在(['?'])。"""
        import agent_chrome
        return agent_chrome.codex_waiting(tabs)


# 規矩開頭講過,但接回的修改那一輪 Claude 會漏掉(實測);最後再講一次,程式才讀得到那一頁
CLAUDE_TAIL = ('\n\n最後,寫 fill.json 之前,照最前面【填完、改完的最後一步】在那一頁跑那支唯讀函式'
               '(先長度、再分段,程式碼照抄不要改)。這一步沒做,程式讀不到那一頁,這一輪就算沒完成。'
               '用平台上存好的履歷投遞的話,也照【讀平台履歷給程式】把平台上那一份讀給程式。')
# 送出那一輪:按完送出之後,程式要自己看那一頁是不是還停在申請表(#316);Claude 的頁程式只能從紀錄拿
CLAUDE_SUBMIT_TAIL = ('\n\n最後,寫 submit.json 之前(不管送成功沒有),照最前面【填完、改完的最後一步】在那一頁跑那支唯讀函式'
                      '(先長度、再分段,程式碼照抄不要改):程式要自己看按完送出之後那一頁是什麼樣子。')
# 填表前:Claude 的分頁只有它那段對話拿得到,程式自己開不了;這一輪由它讀給程式,填完再比(不是「改用 Codex」)
CLAUDE_PROFILE_LATER = '用 Claude 時程式填表前讀不到,這一輪由你照【讀平台履歷給程式】讀給程式'
CLAUDE_CANT_READ = ('用 Claude 時程式不自己開頁讀:平台履歷由 Claude 在填表、送出前核對那一輪讀給程式;'
                    '查應徵進度由 Claude 在它的 Chrome 裡讀')
CLAUDE_PAGE_LATER = ('用 Claude 時程式只能在它那一輪做完後從紀錄讀那一頁,現在讀不了:'
                     '要看現在的樣子,按看板卡上的「👀 看現在的頁面」')


class ClaudeDoor(_Door):
    """Claude:分頁在 Claude in Chrome 替那段對話開的分頁群組,程式拿不到。讀,是它那一輪最後自己跑唯讀函式、
    程式從紀錄拿工具的回傳;截、關,是 claude -p --resume 那段對話叫它做(約 12 秒、算一次用量)。"""
    runtime = CLAUDE
    live_timeout = 300           # apply_tab 叫 claude -p 最多 240 秒,外層要比它長
    shot_while_busy = False      # 👀 是再叫一個 Claude 進 agent 的 Chrome:同一時間只准一個在裡面

    def ready(self, board=None):
        import agent_chrome
        return (*agent_chrome.wait_claude(), '按「🔌 連接 Claude」')

    def configured(self):
        import agent_chrome
        return bool(agent_chrome.conf().get('claude_device'))

    def open_for_agent(self, url, old_tab=None):
        return None              # 永遠不做:Claude 只看得到自己分頁群組裡的分頁,接不了程式開的

    def apply_rule(self):
        import config as cf
        import agent_run as ar
        import apply_tab
        return (ar.APPLY_RULE_CLAUDE.format(agent=cf.AGENT, device=ar.claude_paired_device()
                                            or '(還沒連接 agent 專用的 Chrome,先停下回報)')
                + apply_tab.CLAUDE_SELF_READ + apply_tab.CLAUDE_PROFILE_READ)

    def fetch_rule(self):
        import profile_sync as ps
        return ps.FETCH_FILE_RULE_CLAUDE

    def task_tail(self, stage):
        return CLAUDE_TAIL if stage in ('fill', 'fix') else CLAUDE_SUBMIT_TAIL if stage == 'submit' else ''

    def profile_reread(self, read_url):
        return (f'\n平台履歷的文字也要重新核對:照【讀平台履歷給程式】打開 {read_url},'
                '在那一頁跑那支唯讀函式(先長度、再分段,程式碼照抄不要改)。')

    def page_reread(self, tab_id):
        return (f'\n送出前程式要自己核對申請表那一頁有沒有變:回到你留著的那一頁(分頁 {tab_id}),不要改任何一格、不要按送出,'
                '照【填完、改完的最後一步】在那一頁跑那支唯讀函式(先長度、再分段,程式碼照抄不要改)。')

    def read_page(self, session, tab_id, logs=None):
        if not logs:
            raise NotNow(CLAUDE_PAGE_LATER)
        import apply_tab
        return apply_tab.page_from_log(logs)

    def read_profile(self, read_url, logs=None, board=None):
        if logs is None:
            raise NotNow(CLAUDE_PROFILE_LATER)
        import apply_tab
        return apply_tab.profile_from_log(logs, read_url)

    def read_pages(self, urls, board=None, ready=None, settle=0):
        raise NotNow(CLAUDE_CANT_READ)

    def attachment_hashes(self, logs):
        import apply_tab
        return lambda profile_url: apply_tab.attachments_from_log(logs, profile_url)

    def shot(self, session, tab_id, out):
        import apply_tab
        return apply_tab.claude_shot(session, tab_id, out)

    def release(self, session, tab_id):
        import apply_tab
        return apply_tab.claude_release(session, tab_id)

    def waiting(self, tabs, board=None):
        # Claude 開的頁在它的分頁群組,程式沒有便宜的門路逐一確認(要接回那段對話、跑一次模型):
        # 看板上記著、還沒送出的就當成還在等他,寧可留著 Chrome,也不要把他要核對的那一頁關掉
        return sorted(tabs)


DOORS = {CODEX: CodexDoor, CLAUDE: ClaudeDoor}


def of(runtime, agent_id=None):
    """那一家的門路(要叫的是 agent_id 那一個);不能開 Chrome 的(Command Code、沒記到)回 None。"""
    kind = DOORS.get(runtime)
    return kind(agent_id) if kind else None


def _agents():
    import config as cf
    return [a for a in ((cf.C.get('agent') or {}).get('agents') or []) if isinstance(a, dict)]


def _browser_agents(runtime):
    return [a for a in _agents() if a.get('browser') and a.get('runtime') == runtime]


def current():
    """現在設定裡用 Chrome 的那一家(第一個勾「用它操作 Chrome」、能開 Chrome 的);沒有回 None。
    派新工作(填表、查應徵進度)、看板的「設定好了沒」都照這一個。"""
    for a in _agents():
        if a.get('browser') and a.get('runtime') in DOORS:
            return of(a['runtime'], a.get('id'))
    return None


def of_agent(agent_id):
    """設定裡這個 agent 那一家的門路(填表換手後,真的填的是哪一家);找不到、不能開 Chrome 回 None。"""
    agent = next((a for a in _agents() if a.get('id') == agent_id), None)
    return of((agent or {}).get('runtime'), agent_id)


def for_card(apply):
    """這張卡記的那一家的門路,agent_id 是要叫回的那一個 agent(卡上記的那個還在就用它,不然同一家裡用 Chrome 的那一個)。
    接不回來丟 Unreachable,訊息是卡上要寫的原因:卡上沒記是哪一家(更新前填的,BEFORE_UPDATE)、
    那一家已經沒有能用 Chrome 的 agent(停用、移除、改成別家,AGENT_SWAPPED);
    設定檔讀不懂時判斷不了,丟 sure=False 的(呼叫的人不准因此把卡標成頁面不見了)。"""
    apply = apply or {}
    if not apply.get('runtime'):
        raise Unreachable(BEFORE_UPDATE)
    runtime = apply.get('runtime')
    if runtime not in DOORS:
        raise Unreachable(AGENT_SWAPPED)
    same = _browser_agents(runtime)
    if not same:
        import config as cf
        broken = cf.settings_problem()
        if broken:
            # 設定檔讀不懂,程式照預設值跑:那一家不在預設裡不代表換掉了,現在判斷不了
            raise Unreachable(SETTINGS_UNREADABLE.format(why=broken), sure=False)
        raise Unreachable(AGENT_SWAPPED)
    pinned = next((a for a in same if a.get('id') == apply.get('agent_id')), same[0])
    return of(runtime, pinned.get('id'))
