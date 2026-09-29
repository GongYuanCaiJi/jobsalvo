#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_chrome —— agent 專用的 Chrome:自己一個資料夾(browser.data_dir)、自己一個 Chrome 程序,跟使用者本人的 Chrome 分開。
裝 Codex(商店上叫 ChatGPT)或 Claude 的官方擴充功能;agent 只准在這裡動,不准碰使用者本人的 Chrome。

它絕對不能跳到前景(docs/adr/0003):
  · 平常:一般的 Chrome(有真的視窗),另開一個程序、不搶前景地開,不開除錯埠、不用無頭(Cloudflare 會擋)。
    外掛開分頁、網頁自己開的小視窗都不會放上螢幕,但照常畫、截得到圖(看板的「👀 看現在的頁面」)。
  · 他自己要看:show() 請那個 Chrome 開一個新視窗,這時才出現在他面前(只有他按了才會)。
  · 用完 close_if_idle():沒有頁面在等他就把整個程序關掉,並且確認真的關了才這樣講;下次開之前清掉上一輪的分頁群組。

認外掛:外掛給每個 Chrome 的編號每次連線都可能不一樣,一律用外掛的固定身分(extensionInstanceId)認;
這個身分存在 agent 自己的資料夾裡(外掛沒有就自己產生一個),所以別的 Chrome 不會有同一個。
設定放 browser.state(預設 ~/.cache/jobsalvo/agent-chrome.json):{"instance": 外掛身分, "dir": 認的時候用的資料夾}。

用法:python3 tools/agent_chrome.py [--status | --setup | --show | --close]
"""
import os, sys, json, uuid, time, shutil, argparse, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
# 設定在看板上可以改(設定頁存了之後伺服器會 reload),所以每次用的時候現讀,不在 import 時記死。
def _conf_path():
    return cf.BROWSER_STATE


def data_dir():
    return cf.BROWSER_DIR


def _profile():
    """第一次建 agent 的 Chrome 時,從使用者 Chrome 的哪個設定檔複製登入狀態。"""
    return cf.C['browser'].get('profile') or ''


# Codex 外掛自己的儲存區:裡面放它的固定身分。複製設定檔時不帶過去,外掛會自己產生一個新的(見 prepare)。
CODEX_STORE = os.path.join('Local Extension Settings', 'hehggadaopoacecdllhhajmbjkdcmajg')
# Claude 擴充功能的儲存區:裡面有 Claude Code 認的瀏覽器編號(bridgeDeviceId)。帶過去的話兩邊編號一樣,
# 原本的設定檔一開著,Claude 就可能去操作使用者自己的 Chrome;帶過去的登入也會失效(2026-09-29 實測 identity 是空的),
# 所以不帶,在 agent 的 Chrome 裡登入、按 Connect 一次。
CLAUDE_EXT_STORE = os.path.join('Local Extension Settings', 'fcoeoabgfenejglbffodgkkbkcdhcgfn')
# Sync Data:Chrome 存「關掉的分頁群組」的地方(沒登入 Google 也存;這個設定檔裡只有分頁群組和幾筆網頁 App 紀錄)。
# 每一輪 agent 都開一個群組,不清就一直累積;Chrome 沒有「不要存」的開關(Google 社群多串都說沒有)。
COPY_SKIP = ('Cache', 'Code Cache', 'GPUCache', 'Service Worker', 'Singleton*', 'LOCK', '*.lock', 'Sync Data')
SAVED_GROUPS = os.path.join('Default', 'Sync Data')
# 視窗不在螢幕上時 Chrome 預設不畫畫面、放慢計時器;這三個是 Playwright 開 Chrome 的預設參數,讓它照常畫、截得到圖。
BACKGROUND_OK = ('--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
                 '--disable-background-timer-throttling')
# 翻譯提示:英文頁一載好,Chrome 有時自己彈出「翻成中文?」的小框,連帶把 agent 的視窗放上他的螢幕
# (2026-09-29 抓到兩次;關掉翻譯的啟動參數擋不住,要關 Chrome 設定裡的「使用 Google 翻譯」)。agent 不需要翻譯。
AGENT_HOSTS = ('com.openai.codexextension.json', 'com.anthropic.claude_code_browser_extension.json')


def _quiet_prefs():
    """Chrome 關著時改 agent 設定檔的偏好:不用翻譯(翻譯提示會把視窗帶上螢幕)。"""
    path = os.path.join(data_dir(), 'Default', 'Preferences')
    try:
        with open(path, encoding='utf-8') as f:
            prefs = json.load(f)
    except (OSError, ValueError):
        return
    if (prefs.get('translate') or {}).get('enabled') is False:
        return
    prefs.setdefault('translate', {})['enabled'] = False
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(prefs, f)


def conf():
    try:
        with open(_conf_path(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(c):
    os.makedirs(os.path.dirname(_conf_path()), exist_ok=True)
    with open(_conf_path(), 'w', encoding='utf-8') as f:
        json.dump(c, f, ensure_ascii=False, indent=1)


def _app():
    """Chrome 的 .app(open -a 要的是 app,不是裡面的執行檔)。"""
    import chrome_bin
    exe = chrome_bin.find()
    return exe.split('.app/')[0] + '.app' if '.app/' in exe else exe


def pid():
    """agent 這個 Chrome 的主程序;沒在跑回 None。只認用 agent 資料夾開的那一個,使用者的 Chrome 不算。"""
    flag = f'--user-data-dir={data_dir()}'
    out = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True).stdout
    for line in out.splitlines():
        p, _, cmd = line.strip().partition(' ')
        if flag in cmd and '--type=' not in cmd and 'Google Chrome' in cmd:
            return int(p)
    return None


def prepare():
    """建 agent 的 Chrome 資料夾。回一句話說做了什麼(沒事可做回 '')。
    · 第一次:從使用者選的設定檔(browser.profile)複製過來,登入狀態和擴充功能不用重來;快取不帶,
      Codex 外掛和 Claude 擴充功能的儲存區也不帶(帶過去的話兩邊的身分一樣,分不出誰是誰)。
    · 每次:把 Chrome 的原生訊息設定(擴充功能跟 Codex、Claude Code 講話用的)連進來;
      用自己資料夾的 Chrome 只讀自己資料夾底下的這一份。"""
    import chrome_bin
    d, note = data_dir(), ''
    os.makedirs(d, exist_ok=True)
    src = os.path.join(chrome_bin.USER_DATA, _profile()) if _profile() else ''
    if not os.path.isdir(os.path.join(d, 'Default')) and src and os.path.isdir(src):
        if pid():
            raise RuntimeError('agent 的 Chrome 正在跑,不能換資料')
        shutil.copytree(src, os.path.join(d, 'Default'), symlinks=True,
                        ignore=shutil.ignore_patterns(*COPY_SKIP))
        for store in (CODEX_STORE, CLAUDE_EXT_STORE):
            shutil.rmtree(os.path.join(d, 'Default', store), ignore_errors=True)
        note = f'從 Chrome 的「{_profile()}」複製了登入狀態和擴充功能'
    hosts = os.path.join(chrome_bin.USER_DATA, 'NativeMessagingHosts')
    mine = os.path.join(d, 'NativeMessagingHosts')
    os.makedirs(mine, exist_ok=True)
    # 只接 agent 要用的兩家(Codex、Claude Code)。Claude Desktop 的那份跟 Claude Code 共用同一個擴充功能,
    # 擴充功能會先接 Desktop,Claude Code 就連不上(anthropics/claude-code#88395),所以不接、之前接過的拿掉
    for name in os.listdir(mine):
        if name not in AGENT_HOSTS and os.path.islink(os.path.join(mine, name)):
            os.remove(os.path.join(mine, name))
    for name in AGENT_HOSTS:
        link = os.path.join(mine, name)
        if os.path.exists(os.path.join(hosts, name)) and not os.path.lexists(link):
            os.symlink(os.path.join(hosts, name), link)
    return note


def _not_background(p):
    """這個程序不是照現在的開法開的:舊版的無頭模式,或開著除錯埠的舊開法(Cloudflare 會擋)。"""
    cmd = subprocess.run(['ps', '-o', 'command=', '-p', str(p)], capture_output=True, text=True).stdout
    return '--headless' in cmd or '--remote-debugging-port' in cmd


def launch(wait=15):
    """在背景把 agent 的 Chrome 開起來(已經在跑就不動)。回主程序編號;開不起來回 None。
    一般的 Chrome(有真的視窗),另開一個程序:open -n 另開、-g 不搶前景、--no-startup-window 不開視窗。
    不開除錯埠、不用無頭:這兩個都會讓 Chrome 對網頁標記「正被自動化控制」,104、claude.ai 的 Cloudflare 因此擋下
    (2026-09-29 實測,docs/adr/0003)。agent 只透過官方擴充功能(Codex、Claude)操作。
    這樣開的 Chrome 沒被叫到前面過,外掛開分頁、網頁自己開的小視窗都不會放上螢幕,但照常畫、截得到圖。
    開之前清掉上一輪留下的分頁群組、關掉翻譯提示。"""
    p = pid()
    if p and not _not_background(p) and not conf().get('shown'):
        return p
    if p:
        if waiting_pages():
            return p                         # 有頁面在等他:不重開(那幾頁會不見),照現在的開著用
        quit_chrome()                        # 舊開法、或他叫出來看過(之後的視窗會上螢幕):關掉重開成背景的
    prepare()
    shutil.rmtree(os.path.join(data_dir(), SAVED_GROUPS), ignore_errors=True)
    _quiet_prefs()
    save({**conf(), 'shown': False})
    subprocess.run(['open', '-n', '-g', '-a', _app(), '--args', f'--user-data-dir={data_dir()}',
                    '--no-startup-window', '--no-first-run', '--no-default-browser-check', *BACKGROUND_OK],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(wait * 5):
        p = pid()
        if p:
            return p
        time.sleep(0.2)
    return None


def _running_app(js, p):
    """對 agent 這個程序(NSRunningApplication)做一件事:只動它,使用者的 Chrome 是另一個程序,不受影響。"""
    script = ('ObjC.import("AppKit"); const a = $.NSRunningApplication.runningApplicationWithProcessIdentifier('
              f'{int(p)}); if (a.isNil()) {{ "gone" }} else {{ {js}; "ok" }}')
    return subprocess.run(['osascript', '-l', 'JavaScript', '-e', script],
                          capture_output=True, text=True, timeout=10).stdout.strip() == 'ok'


def quit_chrome(wait=10):
    """把 agent 的 Chrome 整個關掉,確認程序真的不在了才回 True。先照 Chrome 正常結束的方式(SIGTERM),關不掉才強制。"""
    import signal
    p = pid()
    if not p:
        return True
    os.kill(p, signal.SIGTERM)
    for _ in range(wait * 5):
        if not pid():
            return True
        time.sleep(0.2)
    try:
        os.kill(p, signal.SIGKILL)
    except ProcessLookupError:
        pass                                 # 剛好在這之間自己結束了
    time.sleep(1)
    return not pid()


def browser_id(t, instance):
    """外掛現在給 agent 那個 Chrome 的編號;沒連上就回 None。t 是 apply_tab.Session。"""
    raw = t.js('const bs = await cua.listBrowsers({emit:false}); nodeRepl.write("@@" + JSON.stringify('
               'bs.map(b => [b.id, b.metadata && b.metadata.extensionInstanceId])))')
    for bid, inst in json.loads(raw.split('@@')[-1]):
        if inst == instance:
            return bid
    return None


def _mine(c=None):
    """記下的外掛身分是不是現在這個 agent 資料夾的(換過資料夾、或還是舊的「使用者 Chrome 裡的設定檔」做法,就要重新連接)。"""
    c = conf() if c is None else c
    return bool(c.get('instance')) and c.get('dir') == data_dir()


def connected():
    """agent 的 Chrome 有沒有在跑、外掛有沒有連上。"""
    if not _mine() or not pid() or not _codex_ready():
        return False
    try:
        import apply_tab
        t = apply_tab.Session(str(uuid.uuid4()))
    except Exception:
        return False
    try:
        return bool(browser_id(t, conf()['instance']))
    except Exception:
        return False
    finally:
        t.close()


def protected_tabs(board=None, runtime=None):
    """看板上記著的 agent 分頁(填好等他核准、要改、要送的那幾頁),一律不准關。runtime 給了只算那一家開的。"""
    import board_doc as bd
    try:
        with open(board or os.environ.get('AGENT_BOARD') or bd.LIVE, encoding='utf-8') as f:
            fb = json.loads(bd.parse(f.read())['fb'])
    except Exception:
        return None                      # 讀不到看板就當全部都要保護(見 close_if_idle)
    return {str(m['apply']['tab_id']) for m in fb.values()
            if isinstance(m, dict) and (m.get('apply') or {}).get('tab_id') and not (m.get('form') or {}).get('lock')
            and (runtime is None or (m['apply'].get('runtime') or 'codex') == runtime)}


def ensure(board=None, wait=30):
    """派 agent 之前:agent 的 Chrome 在背景開著、外掛連上。回 (連上了沒, 一句話)。"""
    import apply_tab
    c = conf()
    if not _mine(c):
        return False, '還沒連接 agent 的 Chrome:到看板「⚙ 設定 → 🤖 Agent 與瀏覽器」按「🔌 連接 Codex」。'
    if not launch():
        return False, 'agent 的 Chrome 開不起來。'
    t = apply_tab.Session(str(uuid.uuid4()))
    try:
        for _ in range(wait):
            if browser_id(t, c['instance']):
                return True, 'agent 的 Chrome 在背景開著(不在你的螢幕上)。'
            time.sleep(1)
        return False, f'agent 的 Chrome 開了,但 Codex 外掛 {wait} 秒內沒連上。'
    finally:
        t.close()


def tabs(t, bid):
    """agent 的 Chrome 裡所有分頁的 id:沒人認領的,和某一段 agent 對話正在控制的都算(cua.listTabs 兩種都列)。"""
    raw = t.js(f'const ts = await cua.listTabs({{browser: "{bid}", emit: false}}); '
               'nodeRepl.write("@@" + JSON.stringify(ts.map(x => String(x.id))))')
    return json.loads(raw.split('@@')[-1])


def waiting_pages(board=None):
    """agent 的 Chrome 裡還在等他的頁(看板上還沒送出的卡記著的分頁)。沒在跑回 [];讀不到當成有。"""
    import apply_tab
    c = conf()
    if not pid():
        return []
    # Claude 開的那幾頁在它的分頁群組,程式沒有便宜的門路逐一確認(要接回那段對話、跑一次模型):
    # 看板上記著、還沒送出的就當成還在等他,寧可留著 Chrome,也不要把他要核對的那一頁關掉
    claude = sorted(protected_tabs(board, 'claude-code') or [])
    if claude:
        return claude
    keep = protected_tabs(board)
    if not _mine(c) or keep == set():
        return []
    t = apply_tab.Session(str(uuid.uuid4()))
    try:
        bid = browser_id(t, c['instance'])
        return [x for x in (tabs(t, bid) if bid else []) if keep is None or x in keep]
    except Exception:
        return ['?']                         # 讀不到分頁清單:當成有頁面在等他
    finally:
        t.close()


def close_if_idle(board=None):
    """沒有頁面在等他(看板上還沒送出的卡記著的分頁都不在了),就把 agent 的 Chrome 整個關掉。
    這次的工作做完了就該收掉,不留一個開著的瀏覽器。回一句話說做了什麼;沒關掉就照實講。"""
    if not pid():
        return 'agent 的 Chrome 本來就關著'
    waiting = waiting_pages(board)
    if waiting:
        return f'還有 {len(waiting)} 頁在等他,agent 的 Chrome 留著(在背景,不在螢幕上)'
    if quit_chrome():
        return '沒有頁面在等他,agent 的 Chrome 關掉了'
    return 'agent 的 Chrome 關不掉(程序還在)'


PAGE_JS = """{const r = await __g.playwright.evaluate(() => {
  const labelFor = el => {
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)
      .map(id => document.getElementById(id)?.innerText || '').join(' ');
    const labels = el.labels ? [...el.labels].map(x => x.innerText || '').join(' ') : '';
    return (el.getAttribute('aria-label') || labelled || labels || el.getAttribute('placeholder') || '').trim();
  };
  const sectionFor = el => {
    const group = el.closest('fieldset,section,[role=group]');
    const heading = group && group.querySelector('legend,[role=heading],h1,h2,h3,h4,h5');
    return heading ? (heading.innerText || '').trim() : '';
  };
  const controls = [...document.querySelectorAll('input,textarea,select,[role=textbox],[role=combobox],[contenteditable=true]')]
    .filter(el => !['hidden','password','submit','button','file','image','reset'].includes((el.type || '').toLowerCase()))
    .filter(el => {const s = getComputedStyle(el); return el.getClientRects().length && s.display !== 'none' && s.visibility !== 'hidden';});
  const seen = new Map();
  const fields = controls.map(el => {
    const label = labelFor(el), section = sectionFor(el);
    const role = el.getAttribute('role') || (el.tagName === 'TEXTAREA' ? 'textbox' :
      el.tagName === 'SELECT' ? 'combobox' : el.isContentEditable ? 'textbox' : 'textbox');
    const type = (el.type || el.tagName.toLowerCase()).toLowerCase();
    const autocomplete = (el.getAttribute('autocomplete') || '').trim();
    const key = JSON.stringify([label.toLocaleLowerCase(), section.toLocaleLowerCase(), role.toLowerCase(), type, autocomplete.toLowerCase()]);
    const occurrence = seen.get(key) || 0; seen.set(key, occurrence + 1);
    const privateField = ['email','tel'].includes(type) || /(^|\\s)(email|tel)(\\s|$)/i.test(autocomplete);
    const value = privateField ? '' : (el.isContentEditable ? el.innerText :
      (el.tagName === 'SELECT' ? [...el.selectedOptions].map(x => x.text).join(' ') : el.value));
    return {label, section, role, type, autocomplete, occurrence, value: value || ''};
  });
  const emailQuery = new URL(location.href).searchParams;
  const emailThreadPrintView = emailQuery.get('view') === 'pt' &&
    emailQuery.get('search') === 'all' && emailQuery.has('th') &&
    Boolean(document.querySelector('.bodycontainer'));
  const emailMessages = emailThreadPrintView ? [...document.querySelectorAll('.bodycontainer .message')] : [];
  const emailBodies = emailMessages.map(el => (el.innerText || el.textContent || '').trim());
  return {url: location.href, title: document.title, readyState: document.readyState,
    text: document.body.innerText,
    emailThreadPrintView,
    emailMessageCount: emailMessages.length,
    emailBodies,
    links: [...document.links].map(a => a.href),
    anchors: [...document.links].map(a => ({href: a.href, text: (a.innerText || '').trim()})), fields};
}); nodeRepl.write('@@' + JSON.stringify(r));}"""


def open_for_agent(url, old_tab=None, wait=20):
    """代投前程式先把申請頁開好、讀好,交給 agent 接手。回 {'tab_id', 'page'};開不起來回 None(agent 照舊自己開)。
    以前 agent 每輪開頭要自己連瀏覽器、找舊分頁、關、開新的、讀頁面,五到十步,每步都要想;這些程式做得到。
    分頁用 markHandoff 交出去,任何一段對話都接得回來(瀏覽器編號每段對話不同,agent 要自己 listBrowsers 找)。
    old_tab:這張職缺上一輪留下的分頁,先關掉,使用者只會看到這一輪的。"""
    import time
    import apply_tab
    instance = conf().get('instance')
    if not instance:
        return None                           # 還沒連接 agent 的 Chrome:不用開 codex 去試
    t = apply_tab.Session(str(uuid.uuid4()))
    try:
        bid = browser_id(t, instance)
        if not bid:
            return None
        if old_tab:
            try:
                t.js(f'await (await cua.getTab({json.dumps(str(old_tab))}, {{browser: "{bid}"}})).close(); nodeRepl.write("ok")')
            except Exception:  # noqa: S110
                pass                          # 已經不在了或接不回來:不影響這一輪
        t.js(f'globalThis.__t = await cua.createBrowserTab("{bid}", {json.dumps(url)}, {{emit: false}}); nodeRepl.write("ok")')
        tab_id = t.js('nodeRepl.write("@@" + String(__t.id))').split('@@')[-1].strip()
        page = {}
        for _ in range(wait):
            time.sleep(1)
            try:
                page = json.loads(t.js(apply_tab.READ_JS, timeout_ms=8000))
            except Exception:
                continue
            if page.get('fields') or len(page.get('lines') or []) > 20:
                break
        return {'tab_id': tab_id, 'page': page}
    except Exception:
        return None
    finally:
        try:
            t.end_turn(keep=['__t'])
        finally:
            t.close()


def read_pages(urls, board=None, ready=None, wait=25, settle=0):
    """程式自己在 agent 的 Chrome 打開這些網址、等載好、抄下文字和連結、關掉(只讀,不點任何東西)。
    回 {網址: {url, title, text, emailThreadPrintView, emailMessageCount, emailBodies, links, anchors, fields, _ready}};打不開的給空的。
    ready(r) 回 True 代表載好了;settle 要求條件成立後再看到幾次相同頁面。"""
    import time
    import apply_tab
    up, msg = ensure(board)
    if not up:
        raise RuntimeError(msg)
    t = apply_tab.Session(str(uuid.uuid4()))
    out = {}
    try:
        bid = browser_id(t, conf().get('instance'))
        for u in urls:
            t.js(f'globalThis.__g = await cua.createBrowserTab("{bid}", {json.dumps(u)}, {{emit: false}}); nodeRepl.write("ok")')
            r = {}
            previous = None
            stable = 0
            ready_met = False
            try:
                for _ in range(wait * 2):
                    time.sleep(0.5)
                    try:
                        r = json.loads(t.js(PAGE_JS, timeout_ms=8000).split('@@')[-1]) or r
                    except Exception:
                        continue               # 還在載入,外掛讀頁面逾時:下一輪再讀
                    signature = (r.get('url'), r.get('title'), r.get('text'),
                                 r.get('emailThreadPrintView'), r.get('emailMessageCount'),
                                 tuple(r.get('emailBodies') or ()),
                                 tuple(r.get('links') or ()))
                    stable = stable + 1 if signature == previous else 0
                    previous = signature
                    is_ready = (ready or (lambda x: len(x.get('text', '')) > 200))(r)
                    if is_ready and stable >= max(0, int(settle)):
                        ready_met = True
                        break
            finally:
                try:
                    t.js('await __g.close(); nodeRepl.write("ok")')
                except Exception:  # noqa: S110
                    pass
            r['_ready'] = ready_met
            out[u] = r
    finally:
        t.end_turn(keep=[])
        t.close()
    return out


def list_browsers(t):
    """外掛現在連得到的每一個 Chrome:[(編號, 外掛身分)]。"""
    raw = t.js('const bs = await cua.listBrowsers({emit:false}); nodeRepl.write("@@" + JSON.stringify('
               'bs.map(b => [b.id, b.metadata && b.metadata.extensionInstanceId])))')
    return [tuple(x) for x in json.loads(raw.split('@@')[-1])]


def _codex_ready():
    """Codex 的 Chrome 外掛裝了沒(apply_tab 要靠它的 cua_repl 跟 Chrome 講話)。"""
    import apply_tab
    return os.path.isdir(apply_tab.PLUGIN)


def setup(wait=90):
    """看板「⚙ 設定」的「🔌 連接 Codex」:把 agent 的 Chrome 在背景開起來,認出它的 Codex 外掛身分,記進設定。
    第一次開的時候外掛要自己產生新的身分,實測要三十秒以上才連上,所以等久一點。
    agent 的 Chrome 是程式自己開的程序:開之前先關掉(它是我們的,不是使用者的),開之後多出來的那一個外掛就是它。
    回 (成功了沒, 一句話)。"""
    import apply_tab, chrome_bin
    if not _codex_ready():
        return False, '這台電腦還沒裝 Codex(或它的 Chrome 外掛):先裝好 Codex CLI,再按一次。'
    try:
        note = prepare()
    except RuntimeError:
        quit_chrome()
        note = prepare()
    ext = os.path.join(data_dir(), 'Default', 'Extensions', chrome_bin.EXTENSIONS['codex'][0])
    if not os.path.isdir(ext):
        return False, ('agent 的 Chrome 還沒裝 Codex 的擴充功能(商店上叫 ChatGPT):按「🔑 打開 agent 的 Chrome」,'
                       '在那個視窗到 Chrome 線上應用程式商店裝好,再按一次連接。')
    c = conf()
    try:
        t = apply_tab.Session(str(uuid.uuid4()))
    except Exception as e:
        return False, f'接不上 Codex 的 Chrome 外掛({str(e)[:80]})'
    try:
        if _mine(c) and pid() and browser_id(t, c['instance']):
            return True, 'agent 的 Chrome 已經連上了。'
        if not quit_chrome():
            return False, 'agent 的 Chrome 關不掉,沒辦法重新連接。'
        before = {inst for _, inst in list_browsers(t)}
        if not launch():
            return False, 'agent 的 Chrome 開不起來。'
        for _ in range(wait):
            time.sleep(1)
            new = [inst for _, inst in list_browsers(t) if inst and inst not in before]
            if len(new) == 1:
                save({'instance': new[0], 'dir': data_dir()})
                return True, '連上了:agent 的 Chrome 在背景跑,不會出現在你的畫面上。' + (f'({note})' if note else '')
            if len(new) > 1:
                return False, '同時連上了好幾個 Chrome,認不出哪個是 agent 的:把其他 Chrome 關掉再按一次。'
        return False, (f'{wait} 秒內 agent 的 Chrome 的 Codex 外掛沒連上:按「🔑 打開 agent 的 Chrome」,'
                       '到擴充功能頁確認 Codex(ChatGPT)是開著的,再按一次連接。')
    finally:
        t.close()


CLAUDE_STORE = 'https://chromewebstore.google.com/detail/claude/fcoeoabgfenejglbffodgkkbkcdhcgfn'
CLAUDE_LOGIN = 'https://claude.ai/login'
CLAUDE_EXT = 'fcoeoabgfenejglbffodgkkbkcdhcgfn'


def claude_state():
    """Claude Code 自己記的:第一次用 Chrome 的說明看過了沒、Claude Code 上次配對的瀏覽器名字。只讀,不寫(那是 Claude Code 的設定檔)。"""
    try:
        with open(os.path.expanduser('~/.claude.json'), encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False, None
    return bool(d.get('hasCompletedClaudeInChromeOnboarding')), (d.get('chromeExtension') or {}).get('pairedDeviceName')


def show(*urls):
    """他自己要看 agent 的 Chrome(登入網站、裝擴充功能、看它開著的頁):只有他按了才會出現在他面前。
    請那個 Chrome 自己開一個新視窗(同一個資料夾的 Chrome 會把這個請求交給已經在跑的那一個),填好的分頁都還在。
    被叫出來過的 Chrome 之後開的視窗會上螢幕,所以記下來:下次 agent 要用、又沒有頁面在等他時,launch 會關掉重開成背景的。
    回 (成功了沒, 一句話)。"""
    import chrome_bin
    save({**conf(), 'shown': True})
    subprocess.Popen([chrome_bin.find(), f'--user-data-dir={data_dir()}', '--no-first-run', '--no-default-browser-check',
                      '--new-window', *urls],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True, ('打開了 agent 的 Chrome。在那個視窗登入要用的網站、裝擴充功能,或看它填好的頁;'
                  '用完關掉那個視窗就好,下次 agent 用的時候會自己換回背景。')


def claude_device():
    """agent 的 Chrome 裡 Claude 擴充功能的瀏覽器編號;沒裝、沒登入過回 None。
    Claude Code 的 list_connected_browsers 列的 deviceId 就是這個編號,代投時用它選 agent 的 Chrome,
    不靠 Claude Code 記的配對(他自己的 Chrome 也裝了 Claude 時,配對可能指向那一個)。
    讀的是擴充功能存在自己資料夾裡的 bridgeDeviceId(不是公開格式,擴充功能改版要跟著看)。"""
    import glob
    import re
    ids = []
    for f in sorted(glob.glob(os.path.join(data_dir(), 'Default', CLAUDE_EXT_STORE, '*.log')) +
                    glob.glob(os.path.join(data_dir(), 'Default', CLAUDE_EXT_STORE, '*.ldb')), key=os.path.getmtime):
        with open(f, 'rb') as fh:
            ids += re.findall(rb'bridgeDeviceId.{0,8}"([0-9a-f-]{36})"', fh.read())
    return ids[-1].decode() if ids else None


def claude_connected(device, wait=120):
    """Claude Code 現在看不看得到這個瀏覽器(問它一次 list_connected_browsers)。"""
    import agent_run as ar
    try:
        out = subprocess.run([ar.claude_bin(), '-p', '呼叫 list_connected_browsers 一次,把結果原樣印出來,不要做別的事。',
                              '--chrome', '--max-turns', '3', '--dangerously-skip-permissions'] + ar.claude_lean(chrome=True),
                             capture_output=True, text=True, timeout=wait, stdin=subprocess.DEVNULL, cwd=cf.HOME).stdout or ''
    except subprocess.TimeoutExpired:
        return False
    return bool(device) and device in out


def wait_claude(wait=90):
    """用 Claude 代投、查回音之前:agent 的 Chrome 剛開起來時,Claude 擴充功能大約 20 秒才連得上(2026-09-29 實測),
    沒等就派工,Claude 會說看不到瀏覽器、什麼都沒做。每 5 秒問一次,最多等 wait 秒。回 (連上了沒, 一句話)。"""
    if not launch():
        return False, 'agent 的 Chrome 開不起來。'
    dev = conf().get('claude_device') or claude_device()
    if not dev:
        return False, '還沒連接 Claude:到看板「⚙ 設定 → 🤖 Agent 與瀏覽器」按「🔌 連接 Claude」。'
    end = time.time() + wait
    while True:
        if claude_connected(dev):
            return True, 'Claude 連上 agent 的 Chrome 了(在背景,不在你的螢幕上)。'
        if time.time() > end:
            return False, (f'Claude {wait} 秒內看不到 agent 的 Chrome:到看板「⚙ 設定 → 🤖 Agent 與瀏覽器」按「🔌 連接 Claude」,'
                           '沒登入會把它開在你面前讓你登入。')
        time.sleep(5)


def claude_setup():
    """看板「⚙ 設定」的「連接 Claude」:agent 的 Chrome 裡的 Claude 擴充功能登入了、Claude Code 也看得到它,
    就記下它的瀏覽器編號,代投時照這個編號選 agent 的 Chrome(不用在側邊欄按 Connect)。
    沒裝或沒登入:把 agent 的 Chrome 開在他面前,他登入完再按一次。回 (成功了沒, 一句話)。"""
    import agent_run as ar
    if not ar.claude_bin():
        return False, '這台電腦還沒裝 Claude Code(claude 指令):先裝好、登入,再按一次。'
    onboarded, _ = claude_state()
    if not onboarded:
        return False, ('第一次讓 Claude 用 Chrome 要在終端機確認一次:打開終端機輸入 claude --chrome,'
                       '出現「Claude in Chrome」說明畫面按 Enter,關掉終端機,再回來按一次「連接 Claude」。')
    if not launch():
        return False, 'agent 的 Chrome 開不起來。'
    dev = claude_device()
    if dev and claude_connected(dev):
        save({**conf(), 'claude_device': dev, 'claude_checked': time.strftime('%Y-%m-%dT%H:%M:%S')})
        return True, '連上了:Claude 代投時會用 agent 的 Chrome(在背景,不在你的螢幕上)。'
    if dev is None:
        show(CLAUDE_STORE, CLAUDE_LOGIN)
        _wait_for_claude()
        return False, ('agent 的 Chrome 還沒裝 Claude 擴充功能:在跳出來的視窗按「加到 Chrome」,登入 claude.ai,'
                       '再點工具列上的 Claude 圖示登入。登入好它會自己連上,不用再按。')
    show(CLAUDE_LOGIN)
    _wait_for_claude()
    return False, ('agent 的 Chrome 裡的 Claude 還沒登入:在跳出來的視窗登入 claude.ai,再點工具列上的 Claude 圖示登入。'
                   '登入好它會自己連上,不用再按。')


def _wait_for_claude(every=20, limit=900):
    """他在跳出來的視窗登入時,每 20 秒問 Claude Code 一次看不看得到;看得到就記下(設定頁重新整理就顯示連上了)。
    最多等 15 分鐘;不再叫他按第二次「連接 Claude」。"""
    import threading

    def poll():
        end = time.time() + limit
        while time.time() < end:
            time.sleep(every)
            dev = claude_device()
            if dev and claude_connected(dev):
                save({**conf(), 'claude_device': dev, 'claude_checked': time.strftime('%Y-%m-%dT%H:%M:%S')})
                return
    threading.Thread(target=poll, daemon=True).start()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--status', action='store_true', help='只看有沒有連上,不動任何東西')
    ap.add_argument('--close', action='store_true', help='沒有頁面在等他就整個關掉')
    ap.add_argument('--setup', action='store_true', help='在背景開 agent 的 Chrome、認出它的外掛,記進設定')
    ap.add_argument('--show', action='store_true', help='打開 agent 的 Chrome 讓你登入')
    a = ap.parse_args()
    if a.setup or a.show:
        ok, msg = setup() if a.setup else show()
        print(msg); sys.exit(0 if ok else 1)
    if a.status:
        print('agent 的 Chrome:', ('連上了' if connected() else '在跑,外掛沒連上') if pid() else '沒在跑')
        return
    print(close_if_idle() if a.close else ensure()[1])


if __name__ == '__main__':
    main()
