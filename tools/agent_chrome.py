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
import os, sys, json, uuid, time, shutil, argparse, subprocess, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import chrome_bin
# 設定在看板上可以改(設定頁存了之後伺服器會 reload),所以每次用的時候現讀,不在 import 時記死。
def _conf_path():
    return cf.BROWSER_STATE


def data_dir():
    return cf.BROWSER_DIR


def _profile():
    """第一次建 agent 的 Chrome 時,從使用者 Chrome 的哪個設定檔複製登入狀態。"""
    return cf.C['browser'].get('profile') or ''


# Codex 外掛自己的儲存區:裡面放它的固定身分。複製設定檔時不帶過去,外掛會自己產生一個新的(見 prepare)。
CODEX_STORE = os.path.join('Local Extension Settings', chrome_bin.EXTENSIONS['codex'][0])
# Claude 擴充功能的儲存區:裡面有 Claude Code 認的瀏覽器編號(bridgeDeviceId)。帶過去的話兩邊編號一樣,
# 原本的設定檔一開著,Claude 就可能去操作使用者自己的 Chrome;帶過去的登入也會失效(2026-09-29 實測 identity 是空的),
# 所以不帶,在 agent 的 Chrome 裡登入、按 Connect 一次。
CLAUDE_EXT_STORE = os.path.join('Local Extension Settings', chrome_bin.EXTENSIONS['claude'][0])
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


def downloads_dir():
    """agent 的 Chrome 下載到哪:jobsalvo 自己的暫存資料夾,不是使用者的「下載」。"""
    return os.path.join(cf.TMP, 'agent-chrome-downloads')


def _quiet_prefs():
    """Chrome 關著時改 agent 設定檔的偏好:
    · 不用翻譯(翻譯提示會把視窗帶上螢幕)
    · 下載存到 jobsalvo 自己的暫存資料夾、不問存哪:Codex 的 downloadMedia 存進 Chrome 的下載資料夾,
      以前是他自己的「下載」,逾時沒搬走的檔就留在那裡(2026-09-29 看到)。規矩照舊只搬 downloadMedia 回傳的路徑。"""
    path = os.path.join(data_dir(), 'Default', 'Preferences')
    try:
        with open(path, encoding='utf-8') as f:
            prefs = json.load(f)
    except (OSError, ValueError):
        return
    want = {('translate', 'enabled'): False, ('download', 'default_directory'): downloads_dir(),
            ('download', 'prompt_for_download'): False}
    os.makedirs(downloads_dir(), exist_ok=True)      # 暫存被清掉過也補回來:資料夾不在,Chrome 會改存回預設的「下載」
    if all((prefs.get(k) or {}).get(n) == v for (k, n), v in want.items()):
        return
    for (k, n), v in want.items():
        prefs.setdefault(k, {})[n] = v
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(prefs, f)


# Codex 上傳、下載前會先問「允許嗎?」,背景沒人能按就卡住:要先在它自己的這一份設定允許網站(docs/agent-chrome.md 2A 第 4 步)
CODEX_BROWSER_CONFIG = '~/.codex/browser/config.toml'
SITES_HINT = ('Codex 上傳履歷、下載平台附件前要先允許網站(它會先問「允許嗎?」,背景沒人能按):'
              '看板「⚙ 設定」的環境檢查會列出你要投的網站還缺哪幾個,和要貼進 ~/.codex/browser/config.toml 的那一段。')


def _host(url):
    from urllib.parse import urlsplit
    return (urlsplit(str(url or '')).hostname or '').casefold()


def codex_sites_needed(board=None):
    """這個人要讓 Codex 上傳、下載的網站,從他自己的看板推:待你決定、可以投了的卡(填表時上傳履歷)、
    登記過的平台履歷(核對附件時下載)。開源給每個人用:不寫死任何一家求職網站。"""
    import board_doc as bd, profile_sync
    try:
        fb = json.loads(bd.load(board)['fb'])
        ups = {_host(u) for u, m in fb.items() if isinstance(m, dict) and m.get('app') in ('ready', 'ship') and not m.get('rm')}
    except (OSError, ValueError, KeyError, bd.Tampered):  # 讀不到、或被偷改過:沒有卡可推,環境檢查照實講
        ups = set()
    downs = {_host(e.get('read')) for k, v in profile_sync.registry().items() if not k.startswith('_') and isinstance(v, dict)
             for e in v.values() if isinstance(e, dict)}
    return {'uploads': sorted(h for h in ups if h), 'downloads': sorted(h for h in downs if h)}


def _codex_config():
    import tomllib
    try:
        with open(os.path.expanduser(CODEX_BROWSER_CONFIG), 'rb') as f:
            return tomllib.load(f)
    except (OSError, ValueError):            # 沒有這個檔、或讀不懂(TOMLDecodeError 是 ValueError):當成沒設
        return {}


def _allowed(d, k):
    v = (d.get(k) if isinstance(d.get(k), dict) else {}).get('allowed')
    return [str(x) for x in v] if isinstance(v, list) else []


def codex_sites_missing(board=None):
    """{'uploads': [還沒允許的網站], 'downloads': [...]},只列有缺的那一段。只讀,不寫(那是 Codex 的設定)。
    比對照 Codex 的寫法:「*.example.com」也涵蓋 example.com 本身。"""
    import fnmatch
    d, need = _codex_config(), codex_sites_needed(board)
    def ok(h, pats):
        return any(fnmatch.fnmatch(h, p.casefold()) or (p.startswith('*.') and h == p[2:].casefold()) for p in pats)
    out = {k: [h for h in need[k] if not ok(h, _allowed(d, k))] for k in ('uploads', 'downloads')}
    return {k: v for k, v in out.items() if v}


def codex_sites_snippet(missing):
    """要貼進 ~/.codex/browser/config.toml 的那一段:原本允許的留著,補上缺的。"""
    d, out = _codex_config(), []
    for k in ('uploads', 'downloads'):
        if missing.get(k):
            sites = _allowed(d, k) + [h for h in missing[k] if h not in _allowed(d, k)]
            out.append(f'[{k}]\nallowed = [' + ', '.join(json.dumps(x) for x in sites) + ']')
    return '\n\n'.join(out)


def explain_blocked(problem):
    """agent 回報的問題是 Codex 的「could not complete the permission request」:照實講是網站沒被允許、要改哪個檔。"""
    p = str(problem)
    if 'permission request' not in p.lower():
        return p
    return p + '(Codex 沒被允許在這個網站上傳或下載:到 ~/.codex/browser/config.toml 的 [uploads] 或 [downloads] 的 allowed 加上這個網站,設定教學 2A 第 4 步)'


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


NO_CHROME = '這台電腦找不到 Google Chrome:先裝好 Chrome,再回來按「🔌 連接」。'


def _cant_launch():
    """launch() 回 None 時怎麼跟他講:沒裝 Chrome 就直說(以前一律說「開不起來」,還叫他按連接)。"""
    return NO_CHROME if not _app() else 'agent 的 Chrome 開不起來。'


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
        # 舊開法、或他叫出來看過(之後的視窗會上螢幕):關掉重開成背景的;有頁面在等他、或他叫出來還開著(正在登入)就不重開
        if not quit_if_safe()[0]:
            return p
    if not _app():
        return None                          # 這台電腦沒有 Chrome:不用 open 一個空的再白等 15 秒
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
    with contextlib.suppress(ProcessLookupError):   # 剛好在這之間自己結束了
        os.kill(p, signal.SIGKILL)
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


def configured():
    """代投、查應徵進度開跑前的檢查:用 Chrome 的那一家(設定裡勾「用它操作 Chrome」的那一個)連接設定過了沒。
    不要求 Chrome 正在跑:每批做完都會把它關掉,下一輪由流程裡的門路自己開(以前這裡用 connected(),
    Chrome 一關就再也開不了跑;只裝 Claude 的人因為只認 Codex,永遠開不了)。"""
    import chrome_door
    door = chrome_door.current()
    return bool(door and door.configured())


def connected():
    """agent 的 Chrome 有沒有在跑、外掛有沒有連上。"""
    if not _mine() or not pid() or not _codex_ready():
        return False
    try:
        import apply_tab
        t = apply_tab.Session(str(uuid.uuid4()))
    except Exception:  # noqa: BLE001 — 只是問「連上了沒」:接不上 Codex 的元件就是沒連上,畫面會叫他按連接
        return False
    try:
        return bool(browser_id(t, conf()['instance']))
    except Exception:  # noqa: BLE001 — 同上:外掛問不到就是沒連上
        return False
    finally:
        t.close()


def protected_tabs(board=None):
    """看板上記著的 agent 分頁(填好等他核准、要改、要送的那幾頁),一律不准關。"""
    held = held_tabs(board)
    return None if held is None else {t for tabs in held.values() for t in tabs}


def held_tabs(board=None):
    """看板上停著的頁,照開它的那一家分:{那一家(卡上沒記到是 None): {分頁…}};讀不到(或被偷改過)看板回 None。"""
    import board_doc as bd
    try:
        p = bd.load(board)
        fb = json.loads(p['fb'])
    except Exception:  # noqa: BLE001 — 讀不到(或被偷改過)看板就當全部都要保護,往不關 Chrome 那邊錯(見 close_if_idle)
        return None
    # 只保護停著的頁(投遞狀態是綠底那五種;自動流程的上限、看板同一條)。退回、移除、封鎖的卡留下的舊 tab_id 不算,
    # 不然 Chrome 永遠關不掉。封鎖公司要看公司名,所以要職缺資料。
    jobs = {j['id']: j for j in p['data'].get('jobs') or [] if isinstance(j, dict) and j.get('id')}
    out = {}
    for u, m in fb.items():
        if isinstance(m, dict) and (m.get('apply') or {}).get('tab_id') and ds.held(fb, u, jobs.get(u, {'id': u})):
            out.setdefault(m['apply'].get('runtime'), set()).add(str(m['apply']['tab_id']))
    return out


def ensure(board=None, wait=30):
    """派 agent 之前:agent 的 Chrome 在背景開著、外掛連上。回 (連上了沒, 一句話)。"""
    import apply_tab
    c = conf()
    if not _mine(c):
        return False, '還沒連接 agent 的 Chrome:到看板「⚙ 設定 → 🤖 Agent 與瀏覽器」按「🔌 連接 Codex」。'
    if not launch():
        return False, _cant_launch()
    t = apply_tab.Session(str(uuid.uuid4()))
    try:
        for _ in range(wait):
            if browser_id(t, c['instance']):
                # 記下最近一次實際連上的時間:設定頁照這個講,不拿「記過外掛身分」當成連得上
                save({**conf(), 'codex_checked': time.strftime('%Y-%m-%dT%H:%M:%S')})
                return True, 'agent 的 Chrome 在背景開著(不在你的螢幕上)。'
            time.sleep(1)
        return False, f'agent 的 Chrome 開了,但 Codex 的擴充功能 {wait} 秒內沒連上。'
    finally:
        t.close()


def tabs(t, bid):
    """agent 的 Chrome 裡所有分頁的 id:沒人認領的,和某一段 agent 對話正在控制的都算(cua.listTabs 兩種都列)。"""
    raw = t.js(f'const ts = await cua.listTabs({{browser: "{bid}", emit: false}}); '
               'nodeRepl.write("@@" + JSON.stringify(ts.map(x => String(x.id))))')
    return json.loads(raw.split('@@')[-1])


def waiting_pages(board=None):
    """agent 的 Chrome 裡還在等他的頁(看板上停著的頁,各家用各家的門路確認還在不在)。沒在跑回 [];讀不到當成有。"""
    import chrome_door
    if not pid():
        return []
    held = held_tabs(board)
    if held is None:
        return ['?']                         # 讀不到看板:當成有頁面在等他
    out = []
    for runtime, tabs in held.items():
        door = chrome_door.of(runtime)
        # 卡上沒記是哪一家開的:接不回來、也沒門路問,當成還在等他(寧可留著 Chrome)
        out += door.waiting(tabs, board) if door else sorted(tabs)
    return out


def codex_waiting(keep):
    """Codex 開的這幾頁現在還在不在:問外掛 agent 的 Chrome 裡現在有哪些分頁;問不到當成都在(['?'])。"""
    import apply_tab
    c = conf()
    if not keep:
        return []
    if not _mine(c):
        return ['?']                         # 看板記著 Codex 開的頁,卻沒有可以問的外掛身分:不確定就當成還在等他
    t = apply_tab.Session(str(uuid.uuid4()))
    try:
        bid = browser_id(t, c['instance'])
        if not bid:
            return ['?']                     # 外掛跟這個 Chrome 斷線、問不到分頁:當成有頁面在等他(2026-09-29 就是這樣把等他的頁關掉)
        return [x for x in tabs(t, bid) if x in keep]
    except Exception:  # noqa: BLE001 — 讀不到分頁清單就當成有頁面在等他,往不關 Chrome 那邊錯
        return ['?']
    finally:
        t.close()


def started_at(p=None):
    """agent 的 Chrome 這個程序什麼時候開的(epoch 秒);沒在跑或問不到回 None。
    用 ps 的 etime(開了多久,[[天-]時:]分:秒)倒推:lstart 會照語系印日期,中文語系下(從終端機起看板)解析不了。"""
    p = p or pid()
    if not p:
        return None
    try:
        out = subprocess.run(['ps', '-o', 'etime=', '-p', str(int(p))], capture_output=True, text=True, timeout=5).stdout.strip()
        days, _, hms = out.rpartition('-')
        secs = 0
        for x in hms.split(':'):
            secs = secs * 60 + int(x)
        return time.time() - (int(days or 0) * 86400 + secs)
    except (OSError, subprocess.SubprocessError, ValueError):   # ps 跑不了、逾時、印出來的看不懂
        return None


def chrome_id():
    """現在這個 agent Chrome 程序是哪一個:{'pid', 'start'};沒在跑回 {}。
    記分頁編號時一起記:分頁編號每個程序從頭數,換了程序,記著的那一頁就不在了(編號還可能剛好是別張卡的頁)。"""
    p = pid()
    st = started_at(p) if p else None
    return {'pid': p, 'start': st} if st else {}


import delivery_state as ds  # noqa: E402
from delivery_state import GONE  # noqa: E402  (卡上寫的那一句,狀態表那邊也用)


def gone_pages(fb, running_url='', proc=None):
    """看板上記著分頁、但那一頁一定已經不在的卡:agent 的 Chrome 沒在跑,或不是開那一頁的那個程序了。
    只看程序,不問外掛:外掛一時連不上不代表頁面不在。正在跑的那張不算(那一輪會自己寫)。
    proc=(程序編號, 開始時間):先問好的現在這個 agent Chrome(sweep_gone 在鎖外問,鎖內照現在的看板算);沒給就現在問。"""
    import datetime
    if proc is None:
        p = pid()
        st = started_at(p) if p else None
    else:
        p, st = proc
    if p and not st:
        return []                            # 問不到什麼時候開的:不確定,不動
    out = []
    for u, m in fb.items():
        a = (m.get('apply') or {}) if isinstance(m, dict) else {}
        if u == running_url or not ds.page_up(m):
            continue
        c = a.get('chrome') or {}
        if c.get('pid'):
            # ps 只算到秒,倒推的起算時間前後會差一兩秒;同一個編號、起算差這麼多就是另一個程序
            if not p or c['pid'] != p or abs((c.get('start') or 0) - st) > 5:
                out.append(u)
            continue
        try:                                 # 以前的紀錄沒記是哪個程序:Chrome 在最後寫紀錄之後才開的,頁一定不在
            at = datetime.datetime.fromisoformat(a['at']).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
        if not p or st > at:
            out.append(u)
    return out


def unreachable_pages(fb, running_url=''):
    """看板上記著頁、開它的那一家卻確定接不回來的卡:{網址: 卡上要寫的原因}(chrome_door.for_card 說的:
    卡上沒記是哪一家、那一家停用或移除)。判斷不了的(設定檔讀不懂)不算。正在跑的那張不算(那一輪會自己寫)。"""
    import chrome_door
    out = {}
    for u, m in fb.items():
        if u == running_url or not isinstance(m, dict) or not ds.page_up(m):
            continue
        try:
            chrome_door.for_card(m.get('apply'))
        except chrome_door.Unreachable as e:
            if e.sure:
                out[u] = str(e)
    return out


def sweep_gone(live, running_url='', only=None, by='agent_chrome', unreachable=False):
    """掃「頁面不見了」並標上(自動流程每分鐘、👀 截不到時):Chrome 是哪個程序在鎖外先問好(ps 要時間,不佔看板鎖),
    哪幾張不見了在看板鎖內照現在的看板重算再標。以前拿鎖外的舊快照算好清單才寫,這之間剛填好的新頁會被誤標成不見(#308)。
    unreachable=True 也標開那一頁的那一家確定接不回來的(unreachable_pages)。
    only 給了就只看那一張。回傳 {標了哪一張: 原因};沒有就不寫看板。"""
    import board_doc as bd
    p = pid()
    proc = (p, started_at(p) if p else None)

    def put(d):
        got = dict.fromkeys(gone_pages(d['fb'], running_url, proc), GONE)
        if unreachable:
            got = dict(unreachable_pages(d['fb'], running_url), **got)
        got = {u: why for u, why in got.items() if only is None or u == only}
        if not got:
            return bd.SKIP
        for u, why in got.items():
            mark_gone(d['fb'], [u], why)
        return got
    got = bd.rewrite(put, bd.target(live), by)
    return {} if got is bd.SKIP else got


def mark_gone(fb, urls, why=GONE):
    """這幾張的頁不在了:送「Chrome 關過、agent 接不回來」事件(停著的頁 → 頁面不見了、確認作廢;送出結果不明的只是頁不在了)。"""
    for u in urls:
        ds.try_fire(fb, u, 'page_lost', issues=[why])


def user_has_it_open(p=None):
    """他按「🔑 打開 agent 的 Chrome」叫出來、視窗還開著(可能正在登入網站):這時不准背景流程把它關掉或重開。
    用 macOS 公開的視窗清單(CGWindowListCopyWindowInfo)數這個程序在螢幕上有幾個真的視窗(Chrome 自己有幾個 1×1 的隱形視窗,不算)。
    只數在螢幕上的:agent 開的分頁放在不上螢幕的視窗裡(ADR 0003),以前連這些也算,他叫出來看過一次之後
    Chrome 就再也不換回背景、也不收掉,之後 agent 開的視窗都上他的螢幕。"""
    p = p or pid()
    if not p or not conf().get('shown'):
        return False
    script = ('ObjC.import("CoreGraphics"); const a = ObjC.castRefToObject($.CGWindowListCopyWindowInfo('
              '$.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, 0)); let n = 0; '
              'for (let i = 0; i < a.count; i++) { const w = a.objectAtIndex(i); '
              f'if (ObjC.unwrap(w.objectForKey("kCGWindowOwnerPID")) !== {int(p)} || ObjC.unwrap(w.objectForKey("kCGWindowLayer")) !== 0) continue; '
              'if (ObjC.deepUnwrap(w.objectForKey("kCGWindowBounds")).Width > 1) n++; } String(n)')
    try:
        out = subprocess.run(['osascript', '-l', 'JavaScript', '-e', script], capture_output=True, text=True, timeout=10).stdout
        return int(out.strip() or 0) > 0
    except (OSError, subprocess.SubprocessError, ValueError):   # 問不到:當成他還在用,寧可不關
        return True


def quit_if_safe(board=None, *, ask_extension=True, his_window_blocks=True, force=False):
    """把 agent 的 Chrome 整個關掉的唯一入口(#293):會把填好、等他核對的頁一起關掉,所以先確定沒有頁在等他。
    不確定(讀不到看板、問不到外掛)一律當成有頁在等他、不關。回 (True, '') 關掉了/本來就關著;
    (None, 原因) 有頁在等他、不關;(False, 原因) 程序關不掉。
    ask_extension=False:只看看板、不問外掛(按「🔌 連接」時外掛多半斷線,問了一定問不到,那就看板記著的全算)。
    his_window_blocks=False:他叫出來的視窗不擋(照教學先按 🔑 裝擴充功能再按連接,關掉那個視窗本來就是這一步)。
    force=True:他在看板上確認過「這幾頁會不見也要連接」。"""
    if not pid():
        return True, ''
    if not force:
        if his_window_blocks and user_has_it_open():
            return None, '他叫出來的 agent 的 Chrome 還開著'
        waiting = waiting_pages(board) if ask_extension else sorted(protected_tabs(board) or []) or \
            (['?'] if protected_tabs(board) is None else [])
        if waiting:
            return None, (f'還有{"" if waiting == ["?"] else f" {len(waiting)} "}頁在等他'
                          + ('(不確定有沒有,當成有)' if waiting == ['?'] else ''))
    if quit_chrome():
        return True, ''
    return False, 'agent 的 Chrome 關不掉(程序還在)'


def close_if_idle(board=None):
    """沒有頁面在等他(看板上還沒送出的卡記著的分頁都不在了),就把 agent 的 Chrome 整個關掉。
    這次的工作做完了就該收掉,不留一個開著的瀏覽器。回一句話說做了什麼;沒關掉就照實講。"""
    if not pid():
        return 'agent 的 Chrome 本來就關著'
    ok, why = quit_if_safe(board)
    if ok is None:
        return why + (',不關' if '叫出來' in why else ',agent 的 Chrome 留著(在背景,不在螢幕上)')
    return '沒有頁面在等他,agent 的 Chrome 關掉了' if ok else why


# 每次讀頁在 JS 裡自己限時,比交給外掛的 timeout_ms 早回來:一次 js 超過它自己的 timeout_ms,外掛的 REPL 就被重置
# (全域變數沒了、沒交接的分頁接不回來,之後每次讀都是 ReferenceError,最後也交接不了,外掛跟這個 Chrome 斷線;
# 見 profile_sync 的 FETCH_FILE_RULE)。還在載入、讀不完的頁回 null,下一輪再讀;讀頁出錯也回 null,不留沒人接的錯誤。
READ_MS, READ_TIMEOUT_MS = 6000, 8000


def _bounded(expr):
    return f'Promise.race([({expr}).catch(() => null), new Promise(ok => setTimeout(() => ok(null), {READ_MS}))])'


_PAGE_READER = """() => {
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
}"""
PAGE_JS = '{const r = await ' + _bounded('__g.playwright.evaluate(' + _PAGE_READER + ')') + "; nodeRepl.write('@@' + JSON.stringify(r));}"


def open_for_agent(url, old_tab=None, wait=20):
    """代投前程式先把申請頁開好、讀好,交給 agent 接手。回 {'tab_id', 'page'};開不起來回 None(agent 照舊自己開)。
    以前 agent 每輪開頭要自己連瀏覽器、找舊分頁、關、開新的、讀頁面,五到十步,每步都要想;這些程式做得到。
    分頁用 markHandoff 交出去,任何一段對話都接得回來(瀏覽器編號每段對話不同,agent 要自己 listBrowsers 找)。
    old_tab:這張職缺上一輪留下的分頁:拿它重新載入申請頁,不關(程式這邊關分頁,Codex 外掛會跟 Chrome 斷線,
    見 read_pages);接不回來才另開一頁。"""
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
        reused = False
        if old_tab:
            try:
                t.js(f'globalThis.__t = await cua.getTab({json.dumps(str(old_tab))}, {{browser: "{bid}"}}); '
                     f'await __t.goto({json.dumps(url)}); nodeRepl.write("ok")')
                reused = True
            except Exception:  # noqa: BLE001, S110 — 舊分頁已經不在了或接不回來:下面另開一頁
                pass
        if not reused:
            t.js(f'globalThis.__t = await cua.createBrowserTab("{bid}", {json.dumps(url)}, {{emit: false}}); nodeRepl.write("ok")')
        tab_id = t.js('nodeRepl.write("@@" + String(__t.id))').split('@@')[-1].strip()
        page = {}
        read = 'const r = await ' + _bounded('__t.playwright.evaluate(' + apply_tab.PAGE_FN + ')') + ';\nnodeRepl.write(JSON.stringify(r));'
        for _ in range(wait):
            time.sleep(1)
            try:
                page = json.loads(t.js(read, timeout_ms=READ_TIMEOUT_MS)) or page
            except Exception:  # noqa: BLE001, S112 — 還在載入、外掛讀頁面逾時:下一秒再讀,讀滿 wait 次照樣交給 agent
                continue
            if page.get('fields') or len(page.get('lines') or []) > 20:
                break
        return {'tab_id': tab_id, 'page': page}
    except Exception:  # noqa: BLE001 — 程式先開好只是省 agent 的步數:開不起來回 None,agent 照舊自己開
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
    # 程式自己讀頁只有 Codex 的外掛做得到:只從 chrome_door 的 Codex 那一條叫到這裡(Claude 那一條照實回做不到)
    up, msg = ensure(board)
    if not up:
        raise RuntimeError(msg)
    t = apply_tab.Session(str(uuid.uuid4()))
    out = {}
    try:
        bid = browser_id(t, conf().get('instance'))
        # 整批只用一個分頁,換網址用 goto;讀完換成空白頁、標成交接留著,不關:
        # 程式這一段自己關分頁(close,或這一輪結束時不交接),Codex 外掛會跟這個 Chrome 斷線、不會自己連回來
        # (2026-09-29 實測:代投前讀平台履歷、讀完關頁,接著代投就等不到外掛;查應徵進度讀第二個網址就讀不到)。
        # 留下的空白頁不是他在等的頁,這一批結束時 close_if_idle 會連 Chrome 一起收掉
        opened = False
        for u in urls:
            if not opened:
                t.js(f'globalThis.__g = await cua.createBrowserTab("{bid}", {json.dumps(u)}, {{emit: false}}); nodeRepl.write("ok")')
                opened = True
            else:
                t.js(f'await __g.goto({json.dumps(u)}); nodeRepl.write("ok")')
            r = {}
            previous = None
            stable = 0
            ready_met = False
            for _ in range(wait * 2):
                time.sleep(0.5)
                try:
                    r = json.loads(t.js(PAGE_JS, timeout_ms=READ_TIMEOUT_MS).split('@@')[-1]) or r
                except Exception:  # noqa: BLE001, S112 — 還在載入,外掛讀頁面逾時:下一輪再讀
                    continue
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
            r['_ready'] = ready_met
            out[u] = r
    finally:
        keep = []
        if opened:
            try:  # noqa: SIM105 — 理由同下一行
                t.js('await __g.goto("about:blank"); nodeRepl.write("ok")')
            except Exception:  # noqa: BLE001, S110 — 讀完把共用分頁換回空白頁只是收尾,換不回去下一次讀會直接蓋過
                pass
            keep = ['__g']
        t.end_turn(keep=keep)
        t.close()
    return out


def list_browsers(t):
    """外掛現在連得到的每一個 Chrome:[(編號, 外掛身分)]。"""
    raw = t.js('const bs = await cua.listBrowsers({emit:false}); nodeRepl.write("@@" + JSON.stringify('
               'bs.map(b => [b.id, b.metadata && b.metadata.extensionInstanceId])))')
    return [tuple(x) for x in json.loads(raw.split('@@')[-1])]


# ChatGPT 擴充功能(Codex 用它操作 Chrome)的商店頁;編號跟 chrome_bin 認擴充功能用的同一個
CODEX_STORE_URL = 'https://chromewebstore.google.com/detail/' + chrome_bin.EXTENSIONS['codex'][0]


def codex_configured():
    """Codex 那一家設定好了沒:連接過這個 agent 資料夾的外掛,外掛的程式也裝了。"""
    return bool(_mine() and _codex_ready())


def _codex_ready():
    """Codex 的 Chrome 外掛裝了沒(apply_tab 要靠它的 cua_repl 跟 Chrome 講話)。"""
    import apply_tab
    return os.path.isdir(apply_tab.PLUGIN)


def _connect_confirm(why):
    return ('agent 的 Chrome 裡' + why.replace('在等他', '填好、等你核對或送出') + '。'
            '重新連接要把 agent 的 Chrome 關掉重開,這幾頁會不見(卡上會改成要重填)。確定要連接嗎?')


def setup(board=None, wait=90, force=False):
    """看板「⚙ 設定」的「🔌 連接 Codex」:把 agent 的 Chrome 在背景開起來,認出它的 Codex 外掛身分,記進設定。
    第一次開的時候外掛要自己產生新的身分,實測要三十秒以上才連上,所以等久一點。
    agent 的 Chrome 是程式自己開的程序:開之前先關掉(它是我們的,不是使用者的),開之後多出來的那一個外掛就是它。
    關掉會連填好、等他核對的頁一起關:看板上記著這種頁時先不關,回 (None, 一句話) 要他確認,他確認了再帶 force=True 叫一次。
    回 (成功了沒, 一句話)。"""
    import apply_tab, chrome_bin
    if not _codex_ready():
        return False, '這台電腦還沒裝 Codex(或它的 Chrome 元件):先裝好 Codex CLI,再按一次。'
    try:
        note = prepare()
    except RuntimeError:
        ok, why = quit_if_safe(board, ask_extension=False, his_window_blocks=False, force=force)
        if ok is None:
            return None, _connect_confirm(why)
        note = prepare()
    ext = os.path.join(data_dir(), 'Default', 'Extensions', chrome_bin.EXTENSIONS['codex'][0])
    if not os.path.isdir(ext):
        return False, ('agent 的 Chrome 還沒裝 Codex 的擴充功能(商店上叫 ChatGPT):按「🔑 打開 agent 的 Chrome」,'
                       f'在那個視窗打開 {CODEX_STORE_URL} 按「加到 Chrome」,再按一次連接。')
    c = conf()
    try:
        t = apply_tab.Session(str(uuid.uuid4()))
    except Exception as e:  # noqa: BLE001 — 原因照實回給看板的連接鈕
        return False, f'接不上 Codex 的 Chrome 元件({str(e)[:80]})'
    try:
        if _mine(c) and pid() and browser_id(t, c['instance']):
            return True, 'agent 的 Chrome 已經連上了。'
        # 只看看板、不問外掛:他按連接多半就是因為外掛斷線了,這時問外掛一定問不到。
        # 他自己叫出來的視窗不擋:照教學是先按 🔑 裝好擴充功能再按連接,關掉那個視窗本來就是這一步
        ok, why = quit_if_safe(board, ask_extension=False, his_window_blocks=False, force=force)
        if ok is None:
            return None, _connect_confirm(why)
        if not ok:
            return False, 'agent 的 Chrome 關不掉,沒辦法重新連接。'
        before = {inst for _, inst in list_browsers(t)}
        if not launch():
            return False, _cant_launch()
        for _ in range(wait):
            time.sleep(1)
            new = [inst for _, inst in list_browsers(t) if inst and inst not in before]
            if len(new) == 1:
                save({'instance': new[0], 'dir': data_dir(), 'codex_checked': time.strftime('%Y-%m-%dT%H:%M:%S')})
                close_if_idle(board)         # 認好了就收掉(沒有頁在等他的話):要用時 ensure 會在背景再開
                return True, ('連上了:之後 agent 要用時會在背景開它自己的 Chrome,不會出現在你的畫面上。'
                              + (f'({note})' if note else '') + (SITES_HINT if codex_sites_missing() else ''))
            if len(new) > 1:
                return False, '同時連上了好幾個 Chrome,認不出哪個是 agent 的:把其他 Chrome 關掉再按一次。'
        return False, (f'{wait} 秒內 agent 的 Chrome 裡 Codex 的擴充功能沒連上:按「🔑 打開 agent 的 Chrome」,'
                       '到擴充功能頁確認 Codex(ChatGPT)是開著的,再按一次連接。')
    finally:
        t.close()


CLAUDE_STORE = 'https://chromewebstore.google.com/detail/claude/' + chrome_bin.EXTENSIONS['claude'][0]
CLAUDE_LOGIN = 'https://claude.ai/login'


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
    Chrome 沒在跑時先 prepare():教學是先按這顆裝擴充功能、再按連接;不先準備的話 Chrome 自己建一個空的 Default,
    之後連接就不再複製他選的設定檔(登入狀態、擴充功能),擴充功能也還沒接上跟 Codex、Claude Code 講話的設定。
    回 (成功了沒, 一句話)。"""
    import chrome_bin
    if not chrome_bin.find():
        return False, NO_CHROME
    note = '' if pid() else prepare()
    save({**conf(), 'shown': True})
    subprocess.Popen([chrome_bin.find(), f'--user-data-dir={data_dir()}', '--no-first-run', '--no-default-browser-check',
                      '--new-window', *urls],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True, ('打開了 agent 的 Chrome' + (f'({note})' if note else '') + '。在那個視窗登入要用的網站、裝擴充功能,或看它填好的頁;'
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
    """Claude Code 現在看不看得到這個瀏覽器(問它一次 list_connected_browsers)。
    用設定裡操作 Chrome 的那個 agent 的模型問:不帶的話用帳號預設的模型,選了用不了 Chrome 的模型也會顯示連得上。"""
    import agent_run as ar
    agents, _ = ar._eligible_agents(True)
    model = next((a.get('model') for a in agents if a.get('runtime') == 'claude-code'), '') or ''
    try:
        out = subprocess.run([ar.claude_bin(), '-p', '呼叫 list_connected_browsers 一次,把結果原樣印出來,不要做別的事。',
                              '--chrome', '--max-turns', '3', '--dangerously-skip-permissions']
                             + (['--model', model] if model else []) + ar.claude_lean(chrome=True),
                             capture_output=True, text=True, timeout=wait, stdin=subprocess.DEVNULL, cwd=cf.HOME).stdout or ''
    except subprocess.TimeoutExpired:
        return False
    return bool(device) and device in out


def wait_claude(wait=90):
    """用 Claude 代投、查回音之前:agent 的 Chrome 剛開起來時,Claude 擴充功能大約 20 秒才連得上(2026-09-29 實測),
    沒等就派工,Claude 會說看不到瀏覽器、什麼都沒做。每 5 秒問一次,最多等 wait 秒。回 (連上了沒, 一句話)。"""
    if not launch():
        return False, _cant_launch()
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


def claude_setup(busy=None, board=None):
    """看板「⚙ 設定」的「連接 Claude」:agent 的 Chrome 裡的 Claude 擴充功能登入了、Claude Code 也看得到它,
    就記下它的瀏覽器編號,代投時照這個編號選 agent 的 Chrome(不用在側邊欄按 Connect)。
    沒裝或沒登入:把 agent 的 Chrome 開在他面前,他登入完再按一次。busy() 回 True 時背景輪詢先跳過(見 _wait_for_claude)。
    回 (成功了沒, 一句話)。"""
    import agent_run as ar
    if not ar.claude_bin():
        return False, '這台電腦還沒裝 Claude Code(claude 指令):先裝好、登入,再按一次。'
    onboarded, _ = claude_state()
    if not onboarded:
        return False, ('第一次讓 Claude 用 Chrome 要在終端機確認一次:打開終端機輸入 claude --chrome,'
                       '出現「Claude in Chrome」說明畫面按 Enter,關掉終端機,再回來按一次「連接 Claude」。')
    if not launch():
        return False, _cant_launch()
    dev = claude_device()
    if dev and claude_connected(dev):
        save({**conf(), 'claude_device': dev, 'claude_checked': time.strftime('%Y-%m-%dT%H:%M:%S')})
        close_if_idle(board)                 # 確認好了就收掉(沒有頁在等他、他沒叫出來的話):代投時 wait_claude 會再開
        return True, '連上了:Claude 幫你填表時會用 agent 的 Chrome(在背景,不在你的螢幕上)。'
    if dev is None:
        show(CLAUDE_STORE, CLAUDE_LOGIN)
        _wait_for_claude(busy=busy)
        return False, ('agent 的 Chrome 還沒裝 Claude 擴充功能:在跳出來的視窗按「加到 Chrome」,登入 claude.ai,'
                       '再點工具列上的 Claude 圖示登入。登入好它會自己連上,不用再按。')
    show(CLAUDE_LOGIN)
    _wait_for_claude(busy=busy)
    return False, ('agent 的 Chrome 裡的 Claude 還沒登入:在跳出來的視窗登入 claude.ai,再點工具列上的 Claude 圖示登入。'
                   '登入好它會自己連上,不用再按。')


def _wait_for_claude(every=20, limit=900, busy=None):
    """他在跳出來的視窗登入時,每 20 秒問 Claude Code 一次看不看得到;看得到就記下(設定頁重新整理就顯示連上了)。
    最多等 15 分鐘;不再叫他按第二次「連接 Claude」。
    每問一次就是叫一個 claude -p --chrome 進 agent 的 Chrome:busy() 回 True(代投、查應徵進度正在用它)時那一次跳過,
    同一時間只准一個 agent 在裡面。"""
    import threading

    def poll():
        end = time.time() + limit
        while time.time() < end:
            time.sleep(every)
            if busy and busy():
                continue
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
    ap.add_argument('--force', action='store_true', help='跟 --setup 一起用:有填好等你的頁也照樣關掉重開')
    ap.add_argument('--show', action='store_true', help='打開 agent 的 Chrome 讓你登入')
    a = ap.parse_args()
    if a.setup or a.show:
        ok, msg = setup(force=a.force) if a.setup else show()
        print(msg); sys.exit(0 if ok else 1)
    if a.status:
        print('agent 的 Chrome:', ('連上了' if connected() else '在跑,擴充功能沒連上') if pid() else '沒在跑')
        return
    print(close_if_idle() if a.close else ensure()[1])


if __name__ == '__main__':
    main()
