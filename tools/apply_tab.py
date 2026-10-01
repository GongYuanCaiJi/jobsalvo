#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_tab —— 不經過 agent、直接看 agent 在他 Chrome 裡留著的那一頁(程式,沒有 AI)。

agent 用 Codex 的 Chrome 外掛開分頁,那個分頁屬於開它的那一段對話(session):別的對話拿不到
(外掛會回「Tab … is already part of browser session …」)。這支拿看板上記的那段對話 id 和分頁 id,
自己接上外掛(跟 codex 一樣啟動外掛的 cua_repl,用同一段對話的身分),只做兩件唯讀的事:
  read  讀那一頁現在的網址、每一格的值、上傳欄選了什麼檔。apply_run 填完用它自己對一遍,
        不採信 agent 自己寫的 fill.json。外掛會把 type=email / tel / password 的欄位讀成空的(藏個資,
        實測:頁面上真的有值,截圖也看得到),這幾格只能看截圖,page_problems 不拿它們判對錯。
  shot  當場截那一頁的整頁圖。看板的「👀 看現在的頁面」用它:agent 的視窗開在螢幕外(不搶他的畫面),
        他要看就看這張,是那一刻真的頁面,手機也看得到。
Claude 填的分頁在 Claude in Chrome 替那段對話開的分頁群組,程式拿不到:讀,是 Claude 那一輪最後自己跑一次唯讀函式、
程式從紀錄拿工具的回傳(page_from_log);截,是接回同一段對話叫它截一張(claude_shot)。都不採信模型轉述。
哪一張卡走哪一條由 chrome_door 挑(卡上記的那一家);這裡是兩條的底層。
不點、不打字、不關分頁。用完照外掛的規矩收尾(Session.end_turn):那一頁重新標 markHandoff、宣告這一輪結束,
它才會好好停著,他看得到、agent 下一輪也接得回來。

用法:
  uv run python tools/apply_tab.py read --url 職缺網址 [--board B]      # Codex 填的頁;Claude 填的直接說做不到、改看 👀
  uv run python tools/apply_tab.py shot --url 職缺網址 --out 檔案.png [--board B]
"""
import os, sys, json, time, uuid, select, argparse, subprocess, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from profile_sync import ID_PARAMS    # 平台履歷的編號放在哪個網址參數(讀頁時留下連到那一份的連結)
import gate_cells                     # noqa: E402
PLUGIN = os.path.expanduser('~/.codex/plugins/cache/openai-bundled/unified-computer-use')


def _server():
    """外掛的 cua_repl 怎麼啟動:照 codex 自己用的那份設定(版本號資料夾取最新的)。"""
    vers = sorted(d for d in os.listdir(PLUGIN) if os.path.isfile(os.path.join(PLUGIN, d, '.mcp.json')))
    if not vers:
        raise RuntimeError('找不到 Codex 的 Chrome 元件(unified-computer-use)')
    with open(os.path.join(PLUGIN, vers[-1], '.mcp.json'), encoding='utf-8') as f:
        return json.load(f)['mcpServers']['cua_repl']


class Session:
    """以某一段對話的身分接上外掛(跟 codex 一樣啟動外掛的 cua_repl)。用完 close()。"""

    def __init__(self, session):
        cfg = _server()
        env = dict(os.environ)
        env.update(cfg.get('env') or {})
        self.p = subprocess.Popen([cfg['command']] + cfg.get('args', []), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        self.meta = {'x-codex-turn-metadata': {'session_id': session, 'turn_id': str(uuid.uuid4())}}
        self.n = 0
        self._rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                 'clientInfo': {'name': 'apply_tab', 'version': '1'}})
        self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def _send(self, o):
        self.p.stdin.write(json.dumps(o) + '\n')
        self.p.stdin.flush()

    def _rpc(self, method, params, timeout=90):
        self.n += 1
        self._send({'jsonrpc': '2.0', 'id': self.n, 'method': method, 'params': params})
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([self.p.stdout], [], [], 1)
            if r:
                line = self.p.stdout.readline()
                if not line:
                    break
                m = json.loads(line)
                if m.get('id') == self.n:
                    return m
        raise TimeoutError(method)

    def call(self, code, timeout_ms=60000):
        """跑一段 js,回 (文字, [圖片 bytes])。外掛的沙箱不准它寫檔,圖片一律走 nodeRepl.emitImage 傳回來。"""
        import base64
        # 外掛偶爾回「Unable to load browser request-header policy. Retry the browser command.」:指令根本沒跑,
        # 照它說的重送。放在這一層,開分頁、關分頁、讀頁、截圖每一處都吃得到(以前只有讀頁會重試,
        # 修改那一輪收分頁時碰到一次就整輪中斷)
        for attempt in range(3):
            m = self._rpc('tools/call', {'name': 'js', 'arguments': {'code': code, 'timeout_ms': timeout_ms}, '_meta': self.meta},
                          timeout=timeout_ms / 1000 + 30)
            res = m.get('result') or {}
            cs = res.get('content') or []
            text = ''.join(c.get('text', '') for c in cs if c.get('type') == 'text')
            if not (res.get('isError') and 'retry the browser command' in text.lower() and attempt < 2):
                break
            time.sleep(3)
        if res.get('isError'):
            raise RuntimeError(text[-300:])
        return text, [base64.b64decode(c['data']) for c in cs if c.get('type') == 'image' and c.get('data')]

    def js(self, code, timeout_ms=60000):
        return self.call(code, timeout_ms)[0]

    def end_turn(self, keep=()):
        """照外掛的規矩收尾,跟 codex 每一輪結束時一樣:要留著的分頁在這一輪重新標 markHandoff,再宣告這一輪結束。
        不宣告就直接結束行程,分頁會卡在「接在一個已經死掉的行程上」,下一個人(包括 agent 自己下一輪)拿它都是
        Debugger unattached。沒重新標的分頁,這一輪結束時外掛會把它收掉。"""
        for var in keep:
            try:  # noqa: SIM105 — 理由同下一行
                self.js(f'await {var}.markHandoff(); nodeRepl.write("ok")')
            except Exception:  # noqa: BLE001, S110 — 標不到的分頁這一輪結束時外掛會收掉;之後要讀那一頁讀不到時,讀的那一方照實回報
                pass
        m = self.meta['x-codex-turn-metadata']
        self._rpc('tools/call', {'name': 'turn_ended', '_meta': self.meta,
                                 'arguments': {'hook_event_name': 'Stop', 'session_id': m['session_id'], 'turn_id': m['turn_id']}})

    def close(self):
        with contextlib.suppress(OSError):   # 已經結束了
            self.p.kill()


class Tab(Session):
    """以那一段對話的身分拿到它在 agent 的 Chrome 裡的那個分頁。"""

    def __init__(self, session, tab_id):
        super().__init__(session)
        import agent_chrome
        bid = agent_chrome.browser_id(self, agent_chrome.conf().get('instance'))
        if not bid:
            self.close()
            raise LookupError('agent 的 Chrome 沒連上')
        try:
            self.js(f'globalThis.__t = await cua.getTab({json.dumps(str(tab_id))}, {{browser: "{bid}"}}); nodeRepl.write("ok")')
        except Exception as e:  # noqa: BLE001 — 外掛丟的什麼都有;收好後換成 LookupError 照實往上丟
            self.close()
            raise LookupError(str(e)[-300:] or '拿不到那個分頁') from e


# 頁面上每一個欄位:題目(label / aria-label / name)、型別、現在的值;上傳欄給檔名。只讀,不改頁面。
PAGE_FN = r"""() => {
  const lab = el => {
    const f = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    const w = el.closest('label');
    return ((f && f.innerText) || el.getAttribute('aria-label') || (w && w.innerText) || el.name || el.id || '').trim().slice(0, 200);
  };
  const fields = [];
  document.querySelectorAll('input,select,textarea').forEach(el => {
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(el.type)) return;
    // 上傳欄:外掛這邊拿不到 el.files(undefined),只拿得到 value「C:\\fakepath\\檔名」;
    // 以前只看 files,檔明明選上了也讀成空的,每一張都被判「上傳欄裡沒有」。value 只有第一個檔名。
    const v = el.type === 'file' ? (el.files ? Array.from(el.files).map(f => f.name)
                                            : (el.value ? [el.value.split(/[\\/]/).pop()] : []))
            : (el.type === 'checkbox' || el.type === 'radio') ? (el.checked ? (el.value || 'on') : '')
            : el.value;
    // 自製下拉選單(react-select 等,Greenhouse 新版表單):輸入框是空的,選好的值是旁邊顯示的字
    const combo = el.getAttribute('role') === 'combobox' || el.hasAttribute('aria-autocomplete');
    const box = combo && (el.closest('[class*="control"]') || el.parentElement);
    const shown = el.tagName === 'SELECT' && el.selectedOptions[0] ? el.selectedOptions[0].text
                : box ? box.innerText.trim().slice(0, 200) : undefined;
    fields.push({label: lab(el), name: el.name || el.id || '', type: el.type || el.tagName.toLowerCase(), value: v, shown});
  });
  // 頁面上看得到的短文字行(去掉圖示字):104「選擇履歷」這種選單不是表單欄位,選好的值只是一行字;
  // Greenhouse 上傳完把上傳欄拿掉、只用文字顯示檔名
  const lines = document.body.innerText.split('\n')
    .map(s => s.replace(/[\ue000-\uf8ff]/g, '').replace(/[×✕]\s*$/, '').trim())
    .filter(s => s && s.length <= 120).slice(0, 1500);
  const shownFiles = lines.filter(s => /\.(pdf|docx?|rtf|odt|txt)$/i.test(s)).slice(0, 30);
  // 連到平台上某一份履歷的連結(104 應徵彈窗的「預覽履歷」帶著那一份的編號):程式照編號核對選的是哪一份
  const profileLinks = Array.from(new Set(Array.from(document.links).map(a => a.href).filter(h => {
    try { const q = new URL(h).searchParams; return ID_PARAMS.some(k => q.has(k)); } catch (e) { return false; }
  }))).slice(0, 20);
  return {url: location.href, title: document.title, fields, shownFiles, lines, profileLinks};
}""".replace('ID_PARAMS', json.dumps(sorted(set(ID_PARAMS.values()))))
READ_JS = 'const r = await __t.playwright.evaluate(' + PAGE_FN + ');\nnodeRepl.write(JSON.stringify(r));'


def _lookup(url, board=None):
    """看板上這張卡記的那段對話、分頁,和開那一頁的那一家的門路(chrome_door.for_card:沒記到、那一家不能用了丟 Unreachable)。"""
    import board_doc as bd
    fb = json.loads(bd.load(board)['fb'])
    a = (fb.get(url) or {}).get('apply') or {}
    if not a.get('session') or not a.get('tab_id'):
        raise LookupError('看板上沒有記這張是哪一段對話、哪個分頁')
    import agent_chrome
    if agent_chrome.gone_pages({url: fb[url]}):
        # 分頁編號每個 Chrome 程序從頭數:Chrome 重開過,記著的編號可能剛好是別張卡的頁,不能拿它去截、去讀
        raise LookupError(agent_chrome.GONE)
    import chrome_door
    return a['session'], a['tab_id'], chrome_door.for_card(a)


def _on_tab(session, tab_id, fn, tries=3):
    # 外掛偶爾回「Unable to load browser request-header policy. Retry the browser command.」這種叫你重試的暫時錯誤;
    # 讀一次、截一次就放棄的話,整張填好的表會被判沒填好、沒有截圖
    import time
    for attempt in range(tries):
        try:
            t = Tab(session, tab_id)
            try:
                return fn(t)
            finally:
                try:
                    t.end_turn(keep=['__t'])      # 那一頁還要留給他看、留給 agent 下一輪;出錯也要交接,不然外掛會跟 Chrome 斷線
                finally:
                    t.close()
        except (RuntimeError, LookupError, TimeoutError) as e:
            if attempt == tries - 1 or not ('retry' in str(e).lower() or isinstance(e, TimeoutError)):
                raise
            time.sleep(3)


def read(session, tab_id, tries=3):
    """Codex 開的那一頁現在的網址、每一格的值、上傳欄選了什麼檔。"""
    return _on_tab(session, tab_id, lambda t: json.loads(t.js(READ_JS)), tries)


def release(session, tab_id):
    """Codex 開的那一頁不用再留(送成功之後用):換成空白頁、照樣交接留著。
    不關、也不讓這一輪結束時被收掉:程式這邊收分頁,Codex 外掛會跟 agent 的 Chrome 斷線、不會自己連回來
    (2026-09-29 實測),一次送好幾張時下一張就連不上。空白頁不是他在等的頁,close_if_idle 會連 Chrome 一起收掉。"""
    _on_tab(session, tab_id, lambda t: t.js('await __t.goto("about:blank"); nodeRepl.write("ok")'), tries=1)


# ---- Claude:分頁在 Claude in Chrome 替那段對話開的分頁群組裡,只有同一段對話拿得到(新的對話看不到)。
# 官方沒有「程式直接接擴充功能」的門路,只有 claude -p --resume 那段對話(2026-09-29 實測:接回去看得到、截得到;
# 開新對話看不到)。模型只負責照指示呼叫工具;頁面內容和截圖直接從工具的回傳拿(stream-json),不採信模型轉述。
CLAUDE_READER_MODEL = 'sonnet'     # 照單呼叫兩三個工具就好;haiku 用不了 Claude in Chrome(回「requires permission」,2026-09-29 實測)
# 開場白照實講這一次要做什麼:收分頁那次也寫「只讀,不改頁面」、接著又要它關分頁,前後矛盾,Claude 可能因此拒絕
CLAUDE_LOOK = ('看板要再看一次現在的樣子(核對欄位、給使用者看截圖;頁面可能跟上次不一樣)。只讀,不改頁面、不點東西。')
CLAUDE_RELEASE = ('這一張已經送出了,那一頁不用再留:照下面把那一個分頁關掉就好,不點頁面上的東西、不動其他分頁。')


def _claude_run(session, steps_text, names, timeout=240, why=CLAUDE_LOOK):
    """以那段對話的身分跑一次 claude -p,照 steps_text 呼叫 Claude in Chrome 的工具;
    回每一個工具呼叫的 (工具名, 回傳內容, 有沒有出錯),照呼叫的順序。"""
    import agent_chrome, agent_run as ar
    exe = ar.claude_bin()
    if not exe:
        raise RuntimeError('找不到 claude')
    dev = agent_chrome.conf().get('claude_device')
    first = f'先用 mcp__claude-in-chrome__select_browser 選 deviceId {dev} 那個瀏覽器(agent 專用的 Chrome)。\n' if dev else ''
    names = sorted(set(names) | ({'select_browser'} if dev else set()))
    # 照實講這是誰要的、要做什麼:講得像命令、藏東西,Claude 會當成注入拒絕(實測)
    # 每次帶時間:同一段對話裡同樣的請求重複好幾次,Claude 會起疑、不做(實測)
    prompt = (f'(求職看板的自動核對,{time.strftime("%H:%M:%S")})你在 agent 的 Chrome 裡留著的那一頁,' + why +
              '請照下面做,做完簡短說一聲就好。\n' + first + steps_text)
    r = subprocess.run([exe, '-p', '--chrome', '--resume', session, '--model', CLAUDE_READER_MODEL,
                        '--output-format', 'stream-json', '--verbose', '--tools=',     # 內建工具全關,只剩 Chrome 的
                        # Chrome 每個動作有自己一道權限檢查,跟代投一樣要 skip + claude_lean(chrome) 的允許規則才過
                        '--dangerously-skip-permissions',
                        '--allowedTools=' + ','.join(f'mcp__claude-in-chrome__{n}' for n in names),
                        *ar.claude_lean(chrome=True)],
                       input=prompt, capture_output=True, text=True, timeout=timeout)
    uses, out = {}, []
    for c in _tool_events(r.stdout.splitlines()):
        if c['type'] == 'tool_use':
            uses[c.get('id')] = c.get('name', '').rsplit('__', 1)[-1]
        else:
            out.append((uses.get(c.get('tool_use_id')), c['content'], bool(c.get('is_error'))))
    return out


def _tool_events(lines):
    """stream-json 紀錄裡的每一個 tool_use / tool_result 區塊,照順序;tool_result 的 content 一律整理成清單。"""
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        msg = o.get('message') if isinstance(o, dict) else None
        for c in (msg.get('content') or []) if isinstance(msg, dict) and isinstance(msg.get('content'), list) else []:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'tool_use':
                yield c
            elif c.get('type') == 'tool_result':
                content = c.get('content') if isinstance(c.get('content'), list) else [{'type': 'text', 'text': str(c.get('content') or '')}]
                yield dict(c, content=content)


def _text(content):
    return ''.join(c.get('text', '') for c in content if c.get('type') == 'text')


def _claude_tools(session, calls, timeout=240, why=CLAUDE_LOOK):
    """依序呼叫固定的幾個工具,回每個的回傳內容;沒呼叫到或出錯就是讀不到。"""
    steps = '\n'.join(f'{i}. mcp__claude-in-chrome__{n},參數 {json.dumps(a, ensure_ascii=False)}' for i, (n, a) in enumerate(calls, 1))
    got = _claude_run(session, steps, [n for n, _ in calls], timeout, why)
    out = []
    for name, _ in calls:
        mine = [x for x in got if x[0] == name]
        if not mine:
            raise LookupError(f'Claude 沒有做 {name}(那段對話可能接不回來)')
        _, content, err = mine[-1]
        if err or 'is not in Claude\'s tab group' in _text(content):
            raise LookupError(_text(content)[:300] or f'{name} 失敗')
        out.append(content)
    return out


# Claude 填的那一頁,程式怎麼核對:讓 Claude 在它自己那一輪的最後,跑一次下面這支唯讀函式,程式從那一輪的紀錄
# (stream-json)裡拿工具的回傳,而且只收程式碼跟這裡一字不差的那幾次(內容是工具給的,模型改不了、也編不出來)。
# 不另外接回那段對話叫它讀:同樣的請求重複幾次,Claude 會當成可疑、拒絕,而且拒絕會留在對話裡越來越難叫(2026-09-29 實測)。
# javascript_tool 的回傳超過 1000 字會被截斷(擴充功能自己截的),所以先拿長度、再每 900 字一段拿。
# 不壓縮、不編碼、不把資料存在頁面上:那樣看起來像在偷資料,安全過濾會擋(實測)。
CHUNK = 900
# 給 Claude 的版本不帶註解、不帶跳脫字:它會自己把註解刪掉、把 \\ue000 換成字(實測),程式碼就對不上了
_CLAUDE_FN = __import__('re').sub(r'^\s*//.*\n', '', PAGE_FN, flags=__import__('re').M).replace(
    '/[\\ue000-\\uf8ff]/g', 'new RegExp("[" + String.fromCharCode(57344) + "-" + String.fromCharCode(63743) + "]", "g")')
_WHOLE = 'JSON.stringify((' + _CLAUDE_FN + ')())'


def chunk_js(i, whole=_WHOLE):
    return str(int(i)) + ' + ":" + ' + whole + f'.slice({int(i)} * {CHUNK}, ({int(i)} + 1) * {CHUNK})'


def self_read_steps(whole, what):
    """叫 Claude 分段跑一支程式寫好的唯讀函式(whole 是回傳 JSON 字串的那一段)的步驟;查應徵進度核實補查來源也用這一套。"""
    return ('1. text 一字不改用:String(' + whole + '.length)\n它回傳整串的長度 L。\n'
            f'2. 對 I = 0、1…到 ceil(L / {CHUNK}) - 1 各跑一次,text 用下面這串、把開頭和 slice 裡的 I 換成那個數字(其他一字不改):\n'
            + chunk_js(0, whole).replace('0 + ":"', 'I + ":"', 1).replace(f'slice(0 * {CHUNK}, (0 + 1) * {CHUNK})', f'slice(I * {CHUNK}, (I + 1) * {CHUNK})')
            + f'\n這幾次可以在同一則訊息裡一起送出。{what}\n\n')


# 接在 Claude 代投規矩後面(apply_rule);大括號很多,不走 str.format
CLAUDE_SELF_READ = ('【填完、改完的最後一步】寫 fill.json 之前,在你留著的那一頁用 javascript_tool 跑一次下面這支唯讀函式,'
                    '讓程式自己核對頁面上的欄位(你不用看結果,也不用照它改什麼)。javascript_tool 的回傳超過 1000 字會被截掉,所以分段:\n'
                    + self_read_steps(_WHOLE, ''))

# 平台上存好的那份履歷(104 等),程式要跟母稿逐段比(profile_sync.check):Codex 由程式自己開頁讀(agent_chrome.read_pages);
# Claude 的分頁只有它那段對話拿得到,所以比照上面,讓它在那一輪自己打開那一頁跑唯讀函式,程式從紀錄拿(#288)。
# 比對只用得到網址、全文、連結;不帶註解、不帶反斜線(Claude 會改寫,程式碼就對不上)
PROFILE_FN = ('() => ({url: location.href, title: document.title, text: document.body.innerText, '
              'links: Array.from(document.links).map(a => a.href)})')
_PROFILE_WHOLE = 'JSON.stringify((' + PROFILE_FN + ')())'


# 平台履歷上的附件(#294):Claude 沒有把檔完整取回來的工具(回傳超過 1000 字截斷、把檔編碼回傳被安全過濾擋),
# 所以不取檔:在那一頁裡對每個檔案連結算 SHA-256,只回網址、檔名、大小、雜湊(一個檔約 120 字),程式跟本機檔比。
# javascript_tool 不等 Promise(回 {}),但收頂層 await:所以寫成「存進 window 再回它」(實測)。
# 只算看起來是檔的連結(連結字或路徑有副檔名、或標了 download):平台自己「下載整份履歷」那種不算附件。
# 2026-09-30 實測:平台履歷頁三個附件,算出來的雜湊跟本機三個檔一模一樣。不寫死任何平台:同網域、看起來是檔案的連結都算。
ATTACH_JS = ('window.__jsAttach = await (async () => { const files = []; const seen = new Set(); '
             'const ext = /[.](pdf|docx?|pptx?|odt|rtf|zip|png|jpe?g)$/i; '
             'for (const a of Array.from(document.querySelectorAll("a[href]"))) { '
             'const u = new URL(a.href, location.href); const name = (a.innerText || "").trim().split(String.fromCharCode(10))[0]; '
             'if (u.origin !== location.origin || seen.has(u.href)) continue; '
             'if (!(a.hasAttribute("download") || ext.test(name) || ext.test(u.pathname))) continue; '
             'seen.add(u.href); const r = await fetch(u.href, {credentials: "include"}); '
             'if (!r.ok || /text[/]html/i.test(r.headers.get("content-type") || "")) continue; '
             'const b = await r.arrayBuffer(); '
             'const h = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", b))).map(x => x.toString(16).padStart(2, "0")).join(""); '
             'files.push({name: (name || u.pathname).slice(0, 60), size: b.byteLength, sha256: h}); } '
             'return JSON.stringify({url: location.href, files: files}); })(); window.__jsAttach')


CLAUDE_PROFILE_READ = ('【讀平台履歷給程式】用平台上存好的那份履歷投遞(delivery.method 是 platform_profile)時,程式要把平台上那一份'
                       '跟母稿逐段比,但它自己打不開你的分頁,要你讀給它:在你的分頁群組另開一個分頁,打開程式給的平台履歷讀取網址'
                       '(沒給就用你寫進 profile.url、看得到全文的那一頁),等它載好,在那個分頁用 javascript_tool 跑下面這支唯讀函式'
                       '(你不用看結果)。申請表那一頁不要關。填表、修改那一輪在寫 fill.json 之前做;送出前核對那一輪在寫 pre-submit.json 之前做。\n'
                       + self_read_steps(_PROFILE_WHOLE, '')
                       + '3. 同一個分頁再用 javascript_tool 跑一次下面這段(一字不改),它只回平台上每個附件檔的檔名、大小和雜湊,'
                       '程式拿來跟本機的附件比(不用下載、不用看結果):\n' + ATTACH_JS + '\n\n')


def _same_js(a, b):
    """程式碼一樣(只容許空白、換行不同)。"""
    import re
    return re.sub(r'\s+', '', a or '') == re.sub(r'\s+', '', b)


def page_from_log(log):
    """從 Claude 那一輪的紀錄拿它最後一次跑 CLAUDE_SELF_READ 的結果,拼回那一頁;沒有完整的一次就是讀不到。
    log 可以是好幾份(逾時後收尾的那一輪接在原本那一輪後面),照順序讀,取最後一次完整的。"""
    for page in reversed(self_reads(log)):
        if isinstance(page, dict) and 'fields' in page:
            return page
    raise LookupError('Claude 這一輪沒有把那一頁完整讀給程式(紀錄裡找不到完整的一次)')


def _same_page(a, b):
    from urllib.parse import urlsplit
    key = lambda u: (lambda p: (p.netloc.lower(), p.path.rstrip('/'), p.query))(urlsplit(str(u or '')))
    return key(a) == key(b)


def profile_from_log(log, url):
    """Claude 照 CLAUDE_PROFILE_READ 讀的平台履歷頁,而且是程式要核對的那一份(網址一樣);沒有就是讀不到。"""
    seen = []
    for page in reversed(self_reads(log, _PROFILE_WHOLE)):
        if isinstance(page, dict) and 'text' in page:
            seen.append(page.get('url'))
            if _same_page(page.get('url'), url):
                return page
    if seen:
        raise LookupError(f'Claude 讀的是 {seen[0]},不是要核對的 {url}')
    raise LookupError('Claude 這一輪沒有把平台履歷那一頁完整讀給程式')


def _js_calls(log):
    """紀錄裡每一次 javascript_tool 的 (程式碼, 工具真的回傳的字),照順序;出錯的不算。"""
    uses, calls = {}, []
    lines = []
    for one in ([log] if isinstance(log, str) else list(log or [])):
        try:
            with open(one, encoding='utf-8', errors='replace') as f:
                lines += f.readlines()
        except OSError:
            continue
    for c in _tool_events(lines):
        if c['type'] == 'tool_use':
            if c.get('name', '').endswith('javascript_tool'):
                uses[c.get('id')] = (c.get('input') or {}).get('text') or ''
        elif c.get('tool_use_id') in uses and not c.get('is_error'):
            calls.append((uses[c['tool_use_id']], _text(c['content']).split('\n\nTab Context:')[0]))
    return calls


def attachments_from_log(log, url):
    """Claude 照 CLAUDE_PROFILE_READ 第 3 步在平台履歷頁算的附件雜湊:只收程式碼一字不差、網址是要核對的那一份的最後一次。
    回 [{name, size, sha256}];沒有就是沒核對到(LookupError)。"""
    seen = []
    for code, text in reversed(_js_calls(log)):
        if not _same_js(code, ATTACH_JS):
            continue
        try:
            got = json.loads(text[text.index('{'):text.rindex('}') + 1])
        except ValueError:
            continue
        seen.append(got.get('url'))
        if _same_page(got.get('url'), url) and isinstance(got.get('files'), list):
            return [f for f in got['files'] if isinstance(f, dict) and f.get('sha256')]
    if seen:
        raise LookupError(f'Claude 算附件雜湊的是 {seen[0]},不是要核對的 {url}')
    raise LookupError('Claude 這一輪沒有在平台履歷頁算附件雜湊')


def self_reads(log, whole=_WHOLE):
    """紀錄裡每一次完整跑完那支唯讀函式(whole)的結果,照順序;只收程式碼跟程式寫的一字不差、工具真的回傳的那幾次。"""
    import re
    len_js = 'String(' + whole + '.length)'
    calls = _js_calls(log)
    out = []
    starts = [i for i, (code, _) in enumerate(calls) if _same_js(code, len_js)]
    for s0 in starts:
        m = re.match(r'\s*"?(\d+)', calls[s0][1])
        if not m:
            continue
        n, parts = -(-int(m.group(1)) // CHUNK), {}
        for code, text in calls[s0 + 1:]:
            if _same_js(code, len_js):
                break
            m2 = re.match(r'(\d+):', text)
            if m2 and int(m2.group(1)) < n and _same_js(code, chunk_js(m2.group(1), whole)):
                parts[int(m2.group(1))] = text[m2.end():]
        if sorted(parts) == list(range(n)):
            try:
                out.append(json.loads(''.join(parts[i] for i in range(n))))
            except ValueError:
                continue                          # 分段之間頁面變了,拼起來不是完整的一次
    return out


def claude_release(session, tab_id):
    """Claude 開的那一頁不用再留(送成功之後):接回那段對話叫它關掉。"""
    _claude_tools(session, [('tabs_close_mcp', {'tabId': int(tab_id)})], why=CLAUDE_RELEASE)


def claude_shot(session, tab_id, out):
    import base64
    (content,) = _claude_tools(session, [('computer', {'tabId': int(tab_id), 'action': 'screenshot'})])
    imgs = [c['source']['data'] for c in content if c.get('type') == 'image' and (c.get('source') or {}).get('data')]
    if not imgs:
        raise RuntimeError('Claude 沒有傳回截圖')
    with open(out, 'wb') as f:
        f.write(base64.b64decode(imgs[-1]))
    return out


def _norm(s):
    return ' '.join(str(s or '').split()).casefold()


HIDDEN_TYPES = ('email', 'tel', 'password')           # 外掛讀回來一律是空的欄位種類
REDACTED = '<redacted>'                                  # 外掛遮掉 email/電話時讀回來的字(一般文字框也會)
_CONTACT = __import__('re').compile(r'@|^\+?[\d\s()-]{7,}$')   # 長得像 email 或電話的答案


# 網站的真人驗證(Cloudflare 的「請稍候…」「驗證您是人類」這類):agent 不替他按,也不規避。
# 程式控制的瀏覽器常被擋、他在別的視窗過了也接不回來(驗證綁著瀏覽器身分),這種網站讓他自己投。
HUMAN_CHECK = '這個網站要真人驗證,agent 沒辦法幫你填表:按卡上的「🌐 在我的瀏覽器打開」自己投'
HUMAN_CHECK_WORDS = ('請稍候', '驗證您是人類', '正在執行安全驗證', 'verify you are human', 'just a moment',
                     'performing security verification', 'checking your browser')


def human_check(page):
    """這一頁是不是停在網站的真人驗證(看標題和頁面上前幾行字)。"""
    text = ' '.join([str(page.get('title') or '')] + [str(x) for x in (page.get('lines') or [])[:12]]).lower()
    return any(w in text for w in HUMAN_CHECK_WORDS)


def page_problems(page, fb, url, uploaded=(), tab_url=None):
    """那一頁現在的值跟答案庫對不對得上。回問題清單(空的就是對上了)。
    先看它還是不是填好的那一頁:網址變了、欄位不見了,就是換頁了或被送出了(Codex 外掛不准在頁面上裝擋送出,只能事後查)。
    答案庫的答案(英文 v 或中文 zh)要出現在頁面某一格;履歷直接對上的(rz)短答案也一樣;上傳的檔要真的選在上傳欄。
    頁面上怎麼對應到題目各平台不一樣,這裡只問「這個值在不在頁面上」,不猜哪一格是哪一題。"""
    vals, shown, blank = set(), set(), []
    lines = {_norm(x) for x in page.get('lines') or []}   # 頁面上整行的字(104 選單選好的值)
    hidden = [f for f in page.get('fields') or [] if f.get('type') in HIDDEN_TYPES and not f.get('value')]
    for f in page.get('fields') or []:
        v = f.get('value')
        if isinstance(v, list):
            continue
        for x in (v, f.get('shown')):
            if _norm(x):
                vals.add(_norm(x))
        if _norm(f.get('shown')):
            shown.add(_norm(f.get('shown')))
        if not _norm(v) or _norm(v) == REDACTED:
            blank.append(_norm(f.get('label')))
    if human_check(page):
        return [HUMAN_CHECK]
    if tab_url and not gate_cells.same_url(page.get('url'), tab_url):
        return [f'那一頁已經不是填好的申請表了(現在是 {str(page.get("url"))[:80]}),可能被送出了,要人看']
    if not page.get('fields'):
        return ['那一頁上沒有任何欄位(可能被送出了、或換頁了),要人看']
    bad = []
    bank = {e.get('k'): e for e in fb.get('__ans__', [])}
    for x in ((fb.get(url) or {}).get('form') or {}).get('f', []):
        if x.get('src') == 'bank':
            e = bank.get(x.get('k')) or {}
            want = [w for w in (_norm(e.get('v')), _norm(e.get('zh'))) if w]
        elif x.get('src') == 'rz' and '\n' not in str(x.get('v') or '') and '.pdf' not in str(x.get('v') or '').lower():
            want = [w for w in (_norm(x.get('v')),) if w]
        else:
            continue
        contact = bool(want) and all(_CONTACT.search(w) for w in want)
        # 選單常只顯示答案的一段(國碼選單顯示「+44」,答案是「United Kingdom (+44)」):顯示的字整段出現在答案裡也算。
        # email/電話不適用:電話號碼裡本來就有國碼,這樣放會讓讀不到的電話冒充對上。
        if want and not any(w in vals or (not contact and (w in lines or any(len(s) >= 2 and s in w for s in shown)))
                            for w in want):
            q = _norm(x.get('q'))
            if contact and (hidden or any(q and l and (q in l or l in q) for l in blank)):
                continue                   # 外掛把 email/電話讀成空的或「<redacted>」(不管欄位型別),讀不到;看截圖
            bad.append(f'頁面上找不到「{str(x.get("q"))[:40]}」的答案 {str(want[0])[:40]!r}')
    return bad + upload_problems(page, uploaded)


def upload_problems(page, uploaded=()):
    """說上傳了的檔是不是真的選在上傳欄(或上傳完顯示在旁邊的檔名)。回問題清單。"""
    files, flabels = set(), []
    for f in page.get('fields') or []:
        if isinstance(f.get('value'), list):
            files.update(_norm(x) for x in f['value'])
            flabels.append(_norm(f.get('label')))   # Lever 傳完會清空上傳欄,檔名改顯示在旁邊的標籤
    shown_files = {_norm(x) for x in page.get('shownFiles') or []}
    bad = []
    for n in uploaded or ():
        stem = _norm(os.path.splitext(n)[0])[:18]
        if _norm(n) not in files | shown_files and not any(stem and stem in l for l in flabels):
            bad.append(f'上傳欄裡沒有 {n}')
    return bad


def shot(session, tab_id, out, tries=3):
    """Codex 開的那一頁當場截整頁圖。"""
    _, imgs = _on_tab(session, tab_id,
                      lambda t: t.call('await nodeRepl.emitImage(await __t.screenshot({fullPage: true}));'), tries)
    if not imgs:
        raise RuntimeError('Codex 的 Chrome 元件沒有傳回截圖')
    with open(out, 'wb') as f:
        f.write(imgs[-1])
    return out


def _stop_on_term():
    """看板的 👀 時間到會先送 SIGTERM:轉成一般的結束,finally 裡的交接、宣告這一輪結束才跑得到
    (被強制殺掉就跑不到,那一頁接在死掉的程序上,外掛跟 agent 的 Chrome 斷線)。
    正在跑的 claude -p 也跟著收掉(subprocess.run 碰到例外會把子程序殺掉)。"""
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(3))


def main():
    _stop_on_term()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=('read', 'shot'))
    ap.add_argument('--url', required=True)
    ap.add_argument('--board')
    ap.add_argument('--out')
    a = ap.parse_args()
    try:
        sid, tid, door = _lookup(a.url, a.board)
        if a.cmd == 'read':
            print(json.dumps(door.read_page(sid, tid), ensure_ascii=False, indent=1))
        else:
            door.shot(sid, tid, a.out or 'tab.png')
            print(a.out or 'tab.png')
    except Exception as e:  # noqa: BLE001 — 指令列最外層:原因照實印出、結束碼 2(看板的 👀 照結束碼講)
        print(f'看不到那一頁:{e}', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
