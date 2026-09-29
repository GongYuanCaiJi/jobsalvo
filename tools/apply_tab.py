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
不點、不打字、不關分頁。用完照外掛的規矩收尾(Session.end_turn):那一頁重新標 markHandoff、宣告這一輪結束,
它才會好好停著,他看得到、agent 下一輪也接得回來。

用法:
  uv run python tools/apply_tab.py read --url 職缺網址 [--board B]
  uv run python tools/apply_tab.py shot --url 職缺網址 --out 檔案.png [--board B]
"""
import os, sys, json, time, uuid, select, argparse, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PLUGIN = os.path.expanduser('~/.codex/plugins/cache/openai-bundled/unified-computer-use')


def _server():
    """外掛的 cua_repl 怎麼啟動:照 codex 自己用的那份設定(版本號資料夾取最新的)。"""
    vers = sorted(d for d in os.listdir(PLUGIN) if os.path.isfile(os.path.join(PLUGIN, d, '.mcp.json')))
    if not vers:
        raise RuntimeError('找不到 Codex 的 Chrome 外掛(unified-computer-use)')
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
            try:
                self.js(f'await {var}.markHandoff(); nodeRepl.write("ok")')
            except Exception:  # noqa: S110
                pass
        m = self.meta['x-codex-turn-metadata']
        self._rpc('tools/call', {'name': 'turn_ended', '_meta': self.meta,
                                 'arguments': {'hook_event_name': 'Stop', 'session_id': m['session_id'], 'turn_id': m['turn_id']}})

    def close(self):
        try:
            self.p.kill()
        except Exception:  # noqa: S110
            pass


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
        except Exception as e:
            self.close()
            raise LookupError(str(e)[-300:] or '拿不到那個分頁')


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
  return {url: location.href, title: document.title, fields, shownFiles, lines};
}"""
READ_JS = 'const r = await __t.playwright.evaluate(' + PAGE_FN + ');\nnodeRepl.write(JSON.stringify(r));'


def _lookup(url, board=None):
    import board_doc as bd
    with open(board or os.environ.get('AGENT_BOARD') or bd.LIVE, encoding='utf-8') as f:
        fb = json.loads(bd.parse(f.read())['fb'])
    a = (fb.get(url) or {}).get('apply') or {}
    if not a.get('session') or not a.get('tab_id'):
        raise LookupError('看板上沒有記這張是哪一段對話、哪個分頁')
    return a['session'], a['tab_id'], a.get('runtime') or 'codex'


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
                t.end_turn(keep=['__t'])          # 那一頁還要留給他看、留給 agent 下一輪
                t.close()
        except (RuntimeError, LookupError, TimeoutError) as e:
            if attempt == tries - 1 or not ('retry' in str(e).lower() or isinstance(e, TimeoutError)):
                raise
            time.sleep(3)


def read(session, tab_id, tries=3, runtime='codex', log=None):
    if runtime == 'claude-code':
        return page_from_log(log)
    return _on_tab(session, tab_id, lambda t: json.loads(t.js(READ_JS)), tries)


def release(session, tab_id, runtime='codex'):
    """那一頁不用再留:以那段對話的身分接上、這一輪不重新標它就收尾,外掛會把它關掉。送成功之後用。"""
    if runtime == 'claude-code':
        _claude_tools(session, [('tabs_close_mcp', {'tabId': int(tab_id)})])
        return
    t = Tab(session, tab_id)
    try:
        t.end_turn(keep=[])
    finally:
        t.close()


# ---- Claude:分頁在 Claude in Chrome 替那段對話開的分頁群組裡,只有同一段對話拿得到(新的對話看不到)。
# 官方沒有「程式直接接擴充功能」的門路,只有 claude -p --resume 那段對話(2026-09-29 實測:接回去看得到、截得到;
# 開新對話看不到)。模型只負責照指示呼叫工具;頁面內容和截圖直接從工具的回傳拿(stream-json),不採信模型轉述。
CLAUDE_READER_MODEL = 'sonnet'     # 照單呼叫兩三個工具就好;haiku 用不了 Claude in Chrome(回「requires permission」,2026-09-29 實測)


def _claude_run(session, steps_text, names, timeout=240):
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
    prompt = (f'(求職看板的自動核對,{time.strftime("%H:%M:%S")})你在 agent 的 Chrome 裡留著的那一頁,'
              '看板要再看一次現在的樣子(核對欄位、給使用者看截圖;頁面可能跟上次不一樣)。只讀,不改頁面、不點東西。'
              '請照下面做,做完簡短說一聲就好。\n' + first + steps_text)
    r = subprocess.run([exe, '-p', '--chrome', '--resume', session, '--model', CLAUDE_READER_MODEL,
                        '--output-format', 'stream-json', '--verbose', '--tools=',     # 內建工具全關,只剩 Chrome 的
                        # Chrome 每個動作有自己一道權限檢查,跟代投一樣要 skip + claude_lean(chrome) 的允許規則才過
                        '--dangerously-skip-permissions',
                        '--allowedTools=' + ','.join(f'mcp__claude-in-chrome__{n}' for n in names),
                        *ar.claude_lean(chrome=True)],
                       input=prompt, capture_output=True, text=True, timeout=timeout)
    uses, out = {}, []
    for line in r.stdout.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        msg = o.get('message') if isinstance(o, dict) else None
        for c in (msg.get('content') or []) if isinstance(msg, dict) and isinstance(msg.get('content'), list) else []:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'tool_use':
                uses[c.get('id')] = c.get('name', '').rsplit('__', 1)[-1]
            elif c.get('type') == 'tool_result':
                content = c.get('content') if isinstance(c.get('content'), list) else [{'type': 'text', 'text': str(c.get('content') or '')}]
                out.append((uses.get(c.get('tool_use_id')), content, bool(c.get('is_error'))))
    return out


def _text(content):
    return ''.join(c.get('text', '') for c in content if c.get('type') == 'text')


def _claude_tools(session, calls, timeout=240):
    """依序呼叫固定的幾個工具,回每個的回傳內容;沒呼叫到或出錯就是讀不到。"""
    steps = '\n'.join(f'{i}. mcp__claude-in-chrome__{n},參數 {json.dumps(a, ensure_ascii=False)}' for i, (n, a) in enumerate(calls, 1))
    got = _claude_run(session, steps, [n for n, _ in calls], timeout)
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
LEN_JS = 'String(' + _WHOLE + '.length)'


def chunk_js(i):
    return str(int(i)) + ' + ":" + ' + _WHOLE + f'.slice({int(i)} * {CHUNK}, ({int(i)} + 1) * {CHUNK})'


# 接在 Claude 代投規矩後面(apply_rule);大括號很多,不走 str.format
CLAUDE_SELF_READ = ('【填完、改完的最後一步】寫 fill.json 之前,在你留著的那一頁用 javascript_tool 跑一次下面這支唯讀函式,'
                    '讓程式自己核對頁面上的欄位(你不用看結果,也不用照它改什麼)。javascript_tool 的回傳超過 1000 字會被截掉,所以分段:\n'
                    '1. text 一字不改用:' + LEN_JS + '\n它回傳整串的長度 L。\n'
                    f'2. 對 I = 0、1…到 ceil(L / {CHUNK}) - 1 各跑一次,text 用下面這串、把開頭和 slice 裡的 I 換成那個數字(其他一字不改):\n'
                    + chunk_js(0).replace('0 + ":"', 'I + ":"', 1).replace(f'slice(0 * {CHUNK}, (0 + 1) * {CHUNK})', f'slice(I * {CHUNK}, (I + 1) * {CHUNK})')
                    + '\n這幾次可以在同一則訊息裡一起送出。\n\n')


def _same_js(a, b):
    """程式碼一樣(只容許空白、換行不同)。"""
    import re
    return re.sub(r'\s+', '', a or '') == re.sub(r'\s+', '', b)


def page_from_log(log):
    """從 Claude 那一輪的紀錄拿它最後一次跑 CLAUDE_SELF_READ 的結果,拼回那一頁;沒有完整的一次就是讀不到。"""
    import re
    uses, calls = {}, []
    with open(log, encoding='utf-8', errors='replace') as f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            msg = o.get('message') if isinstance(o, dict) else None
            for c in (msg.get('content') or []) if isinstance(msg, dict) and isinstance(msg.get('content'), list) else []:
                if not isinstance(c, dict):
                    continue
                if c.get('type') == 'tool_use' and c.get('name', '').endswith('javascript_tool'):
                    uses[c.get('id')] = (c.get('input') or {}).get('text') or ''
                elif c.get('type') == 'tool_result' and c.get('tool_use_id') in uses and not c.get('is_error'):
                    content = c.get('content') if isinstance(c.get('content'), list) else [{'type': 'text', 'text': str(c.get('content') or '')}]
                    calls.append((uses[c['tool_use_id']], _text(content).split('\n\nTab Context:')[0]))
    starts = [i for i, (code, _) in enumerate(calls) if _same_js(code, LEN_JS)]
    for s0 in reversed(starts):                   # 從最後一次往回找第一個完整的
        m = re.match(r'\s*"?(\d+)', calls[s0][1])
        if not m:
            continue
        n, parts = -(-int(m.group(1)) // CHUNK), {}
        for code, text in calls[s0 + 1:]:
            if _same_js(code, LEN_JS):
                break
            m2 = re.match(r'(\d+):', text)
            if m2 and int(m2.group(1)) < n and _same_js(code, chunk_js(m2.group(1))):
                parts[int(m2.group(1))] = text[m2.end():]
        if sorted(parts) == list(range(n)):
            page = json.loads(''.join(parts[i] for i in range(n)))
            if isinstance(page, dict) and 'fields' in page:
                return page
    raise LookupError('Claude 這一輪沒有把那一頁完整讀給程式(紀錄裡找不到完整的一次)')


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
HUMAN_CHECK = '這個網站要真人驗證,agent 沒辦法代投:按卡上的「🌐 在我的瀏覽器打開」自己投'
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
    vals, shown, files, flabels, blank = set(), set(), set(), [], []
    lines = {_norm(x) for x in page.get('lines') or []}   # 頁面上整行的字(104 選單選好的值)
    hidden = [f for f in page.get('fields') or [] if f.get('type') in HIDDEN_TYPES and not f.get('value')]
    for f in page.get('fields') or []:
        v = f.get('value')
        if isinstance(v, list):
            files.update(_norm(x) for x in v)
            flabels.append(_norm(f.get('label')))   # Lever 傳完會清空上傳欄,檔名改顯示在旁邊的標籤
            continue
        for x in (v, f.get('shown')):
            if _norm(x):
                vals.add(_norm(x))
        if _norm(f.get('shown')):
            shown.add(_norm(f.get('shown')))
        if not _norm(v) or _norm(v) == REDACTED:
            blank.append(_norm(f.get('label')))
    same = lambda a, b: str(a or '').split('#')[0].rstrip('/') == str(b or '').split('#')[0].rstrip('/')
    if human_check(page):
        return [HUMAN_CHECK]
    if tab_url and not same(page.get('url'), tab_url):
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
    shown_files = {_norm(x) for x in page.get('shownFiles') or []}
    for n in uploaded or ():
        stem = _norm(os.path.splitext(n)[0])[:18]
        if _norm(n) not in files | shown_files and not any(stem and stem in l for l in flabels):
            bad.append(f'上傳欄裡沒有 {n}')
    return bad


def shot(session, tab_id, out, tries=3, runtime='codex'):
    if runtime == 'claude-code':
        return claude_shot(session, tab_id, out)
    _, imgs = _on_tab(session, tab_id,
                      lambda t: t.call('await nodeRepl.emitImage(await __t.screenshot({fullPage: true}));'), tries)
    if not imgs:
        raise RuntimeError('外掛沒有傳回截圖')
    with open(out, 'wb') as f:
        f.write(imgs[-1])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=('read', 'shot'))
    ap.add_argument('--url', required=True)
    ap.add_argument('--board')
    ap.add_argument('--out')
    a = ap.parse_args()
    try:
        sid, tid, rt = _lookup(a.url, a.board)
        if a.cmd == 'read':
            print(json.dumps(read(sid, tid, runtime=rt), ensure_ascii=False, indent=1))
        else:
            shot(sid, tid, a.out or 'tab.png', runtime=rt)
            print(a.out or 'tab.png')
    except Exception as e:
        print(f'看不到那一頁:{e}', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
