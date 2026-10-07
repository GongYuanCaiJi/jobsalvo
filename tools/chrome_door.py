#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chrome_door —— 程式這一側碰「agent 的瀏覽器」(ego lite)的門路,收在這一處(docs/adr/0006)。

呼叫的地方不問「是不是 Claude」,只拿一個門路來用(兩家共用 EgoDoor;runtime 只決定叫回哪個 agent):
  of(runtime)      那一家的門路(不能開瀏覽器的回 None)
  current()        設定裡勾「用它操作 ego」的那一家(派新工作、查應徵進度用)
  for_card(apply)  這張卡記的那一家和工作區(之後看、改、送出、關掉都找同一個);那一家停用或移除、卡上沒記 → Unreachable

門路對外提供的事(做不到的丟 NotNow,訊息講現在做不到、改用什麼):
  讀那一頁 read_page、讀平台履歷頁 read_profile / profile_reader、下載平台附件 download_attachments、截圖 shot、
  放掉分頁 release、派工前確認 ego 準備好 ready、程式先開好申請頁 open_for_agent、給 agent 的瀏覽器用法
  (apply_rule、fetch_rule)、查應徵進度讀頁 read_pages、接手 hand_off / resume、設定好了沒 configured、
  看板 👀 怎麼截(live_timeout、live_refresh)、工作區不在了 gone_pages、收尾 close_if_idle。
"""
import os, sys, shutil, json, subprocess, uuid, hashlib, time, contextlib
import base64
import tempfile
from pathlib import Path

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
NO_BROWSER_AGENT = '設定裡沒有會操作瀏覽器的 agent:到看板「⚙ 設定 → 🤖 Agent 與瀏覽器」勾一個「用它操作 ego」'
# 開始記「哪一家開的」之前填的卡(#311 預設 A):不是換掉了,是程式不知道;一樣接不回來
BEFORE_UPDATE = '這張是更新前填的,程式不知道是哪個 agent 開的頁,要重填'
SETTINGS_UNREADABLE = ('設定檔 jobsalvo.json 讀不懂({why}),現在不知道填這張的 agent 還在不在;'
                       '先到看板「⚙ 設定」存一次(或修好那個檔)再試')


def configured():
    return EgoDoor().configured()


def gone_pages(fb, running_url='', proc=None):
    import delivery_state as ds
    bindings = {u: m['apply']['workspace'] for u, m in fb.items()
                if isinstance(m, dict) and u != running_url and ds.page_up(m)
                and isinstance((m.get('apply') or {}).get('workspace'), dict)}
    if not bindings:
        return []
    try:
        result = _ego(
            f'const bindings={json.dumps(bindings)}, spaces=await listTaskSpaces(), result={{}};\n'
            'for (const [url,w] of Object.entries(bindings)) {\n'
            'const found=spaces.find(s=>s.id===w.id && s.name===w.name);\n'
            'if (!found) {result[url]="missing";continue;}\n'
            'if (found.ownership!=="agent") {result[url]="user";continue;}\n'
            'const task=await taskSpace(w.id);\n'
            'result[url]=(await task.tabs()).some(t=>t.label===w.page)?"present":"missing";\n'
            '}return result;', timeout=30)
    except NotNow:
        return []
    return [u for u in bindings if result.get(u) == 'missing']


def sweep_gone(live, running_url='', only=None, by='chrome_door', unreachable=False):
    import board_doc as bd
    import delivery_state as ds
    before = json.loads(bd.load(live)['fb'])
    missing = dict.fromkeys(gone_pages(before, running_url), GONE)
    if unreachable:
        for u, mark in before.items():
            if u == running_url or not ds.page_up(mark):
                continue
            try:
                for_card(mark.get('apply'))
            except Unreachable as error:
                if error.sure:
                    missing[u] = str(error)
    changed = {}
    def update(current):
        for u, reason in missing.items():
            if only is not None and only != u:
                continue
            a = (current.get(u) or {}).get('apply') or {}
            expected = (before.get(u) or {}).get('apply') or {}
            same = a.get('workspace') == expected.get('workspace') and (expected.get('workspace') or a == expected)
            if same and ds.try_fire(current, u, 'page_lost', issues=[reason]):
                changed[u] = reason
    if missing:
        bd.set_fb(update, live=live, by=by)
        close_if_idle(live)
    return changed


def close_if_idle(board=None):
    """只收看板已排入收尾的工作區,不關瀏覽器或其他工作區。失敗留在看板,下一次重試。"""
    import board_doc as bd
    fb = json.loads(bd.load(board)['fb'])
    pending = fb.get('__browser_cleanup__') or []
    completed, failed = [], {}
    active = [(m.get('apply') or {}).get('workspace') for u, m in fb.items()
              if u != '__browser_cleanup__' and isinstance(m, dict)]
    for item in pending:
        w = item.get('workspace') or {}
        if any(isinstance(current, dict) and all(current.get(k) == w.get(k) for k in ('id', 'name', 'page'))
               for current in active):
            completed.append(item)
            continue
        door = EgoDoor()
        door.workspace = w
        try:
            receipt = door.release(f'{w.get("id")}:{w.get("page")}')
        except Unreachable:
            completed.append(item)
        except (NotNow, OSError, ValueError) as e:
            failed[str(w.get('id'))] = str(e)[:200]
        else:
            item['receipt'] = receipt
            completed.append(item)
    if pending:
        def save(current):
            remaining = []
            for item in current.get('__browser_cleanup__') or []:
                if any(item.get('workspace') == x.get('workspace') for x in completed):
                    continue
                if str((item.get('workspace') or {}).get('id')) in failed:
                    item['error'] = failed[str(item['workspace']['id'])]
                remaining.append(item)
            if remaining:
                current['__browser_cleanup__'] = remaining
            else:
                current.pop('__browser_cleanup__', None)
        bd.set_fb(save, live=board, by='chrome_door')
    return f'收尾 {len(completed)} 個工作區' + (f'; {len(failed)} 個待重試' if failed else '')


def setup(*args, **kwargs):
    ok, reason, need = EgoDoor().ready()
    return ok, 'ego 已連線並完成第一次匯入' if ok else reason + ';' + need


def settings_status():
    installed, synced, skill = bool(ego_bin()), imported(), os.path.isfile(EGO_SKILL)
    reason = ('沒有安裝 ego 的瀏覽器指令' if not installed else 'ego 尚未完成第一次匯入' if not synced
              else '缺少 ego-browser skill' if not skill else '已匯入;開始工作前會再確認連線')
    return {'ego': {'installed': installed, 'imported': synced, 'skill': skill,
                    'ok': installed and synced and skill, 'reason': reason}}


from delivery_state import GONE as GONE  # noqa: E402 — 卡上的失聯訊息由狀態表決定


EGO_SKILL = os.path.expanduser('~/.agents/skills/ego-browser/SKILL.md')
EGO_STATE = os.path.expanduser('~/Library/Application Support/Citro Labs/ego lite/Local State')


def imported():
    """只讀 onboarding 的完成標記,不查看 profile、cookies 或憑證。"""
    try:
        with open(EGO_STATE, encoding='utf-8') as f:
            state = json.load(f)
        return bool((state.get('ego') or {}).get('onboarding_imported_browser_data'))
    except (OSError, ValueError, AttributeError):
        return False


def ego_bin():
    """launchd 的 PATH 也能找到 onboarding 安裝的指令。"""
    found = shutil.which('ego-browser')
    fallback = os.path.expanduser('~/.local/bin/ego-browser')
    return found or (fallback if os.path.isfile(fallback) and os.access(fallback, os.X_OK) else None)


def _ego(script, timeout=60):
    """指令從 stdin 進,只收本輪完整的結構化回傳;錯誤不當頁面已消失。"""
    exe = ego_bin()
    if not exe:
        raise NotNow('沒有安裝 ego 的瀏覽器指令')
    marker = '@@jobsalvo-ego@@'
    source = (
        'async function jobsalvoTask() {\n' + script + '\n}\n'
        'try { const value = await jobsalvoTask();\n'
        f'console.log({json.dumps(marker)} + JSON.stringify({{ok:true,value}}));\n'
        '} catch (e) {\n'
        f'console.log({json.dumps(marker)} + JSON.stringify({{ok:false,error:String(e.message || e),code:e.code}}));\n'
        '}\n'
    )
    import evidence
    record, started, ok = evidence.active(), time.monotonic(), False
    if record:
        record.text('browser_call', 'browser', script, ext='.js', backend='ego', timeout=timeout)
    try:
        try:
            result = subprocess.run([exe, 'nodejs'], input=source, text=True, capture_output=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            raise NotNow(f'ego 指令連不上或逾時:{e}') from e
        if result.returncode:
            raise NotNow('ego 指令沒完成:' + (result.stderr or result.stdout)[-500:])
        lines = (result.stdout + '\n' + result.stderr).splitlines()
        row = next((s[len(marker):] for s in reversed(lines) if s.startswith(marker)), None)
        try:
            message = json.loads(row or '')
        except ValueError as e:
            raise NotNow('ego 指令未傳回完整資料:' + (result.stderr or '')[-300:]) from e
        if not isinstance(message, dict):
            raise NotNow('ego 指令未傳回完整資料')
        if not message.get('ok'):
            if message.get('code') in ('workspace_missing', 'page_missing'):
                raise Unreachable(GONE)
            raise NotNow('ego 讀不到工作區:' + str(message.get('error') or '未知原因'))
        ok = True
        return message.get('value')
    finally:
        if record:
            record.note('browser_result', backend='ego', ok=ok, seconds=round(time.monotonic() - started, 3))


def _workspace_name(kind=''):
    token = os.environ.get('JOBSALVO_BROWSER_TEST_RUN')
    prefix = 'jobsalvo-'
    if token:
        prefix += 'test-' + str(uuid.UUID(token)) + '-'
    return prefix + kind + str(uuid.uuid4())


@contextlib.contextmanager
def test_workspaces(keep=None):
    """每輪收自己的工作區;keep 僅供真人仍在接手的真實驗收,完成後清空。"""
    keep = keep if keep is not None else []
    token = str(uuid.uuid4())
    prefix = f'jobsalvo-test-{token}-'
    previous = os.environ.get('JOBSALVO_BROWSER_TEST_RUN')
    before = EgoDoor.workspaces()
    counts = {'run': token, 'before_count': len(before), 'before': before,
              'finished': [], 'retained': [], 'errors': [], 'returned_to_baseline': False}
    os.environ['JOBSALVO_BROWSER_TEST_RUN'] = token
    try:
        yield counts
    finally:
        try:
            spaces = EgoDoor.workspaces()
            counts['before_cleanup_count'] = len(spaces)
            for space in spaces:
                if not space.get('name', '').startswith(prefix):
                    continue
                if any(space['id'] == w.get('id') and space['name'] == w.get('name') for w in keep):
                    counts['retained'].append(space)
                    continue
                door = EgoDoor()
                door.workspace = dict(id=space['id'], name=space['name'], page='p1')
                try:
                    if space.get('ownership') != 'agent':
                        door.resume()  # 使用者明確授權驗收收掉自己的測試交接頁。
                    # 收尾回條留著:工作區沒收掉時(保留了幾個沒 label 的分頁)查得到原因
                    counts['finished'].append(dict(space, receipt=door.release(None)))
                except NotNow as error:
                    counts['errors'].append({'workspace': space, 'reason': str(error)})
                except Unreachable as error:
                    if error.sure:
                        counts.setdefault('already_gone', []).append(space)
                    else:
                        counts['errors'].append({'workspace': space, 'reason': str(error)})
            # finish 回條說關了,ego 列表還要幾秒才拿掉(關的途中標成 user);等它關完再查。
            # ego 是整台電腦共用的(別的 session 也在開工作區),只看這輪自己開的,不比總數
            for _ in range(20):
                after = EgoDoor.workspaces()
                counts['remaining'] = [space for space in after if space.get('name', '').startswith(prefix)
                                       and not any(space['id'] == r['id'] for r in counts['retained'])]
                if not counts['remaining']:
                    break
                time.sleep(0.5)
            counts['after'] = after
            counts['after_count'] = len(after)
            counts['returned_to_baseline'] = not counts['remaining'] and not counts['errors'] and not counts['retained']
        except NotNow as error:
            counts['errors'].append({'reason': str(error)})
        finally:
            if previous is None:
                os.environ.pop('JOBSALVO_BROWSER_TEST_RUN', None)
            else:
                os.environ['JOBSALVO_BROWSER_TEST_RUN'] = previous


# 讀頁之前等文件有 body:有些網站載完(domcontentloaded)又把整份文件換掉,那一瞬間 body 是空的(2026-10-06 LinkedIn)
BODY_READY = 'await page.waitForFunction(() => document.body, undefined, {timeout: 15000}).catch(() => null);\n'


class EgoDoor:
    """agent 的瀏覽器(ego lite)的門路。Codex、Claude 兩個 CLI 共用;runtime 只用於選 agent 和接回對話。"""
    live_refresh = 4             # 看板 👀 幾秒後自動再截
    SHOT_ATTEMPTS = 6  # #372 明定有上限的同頁暖機;超過約 5 次的上游量測仍失敗就回報。
    SHOT_TIMEOUT = 40  # 包住本機 SDK 的 30 秒 CDP 逾時,不用等待取代重試。
    live_timeout = SHOT_ATTEMPTS * SHOT_TIMEOUT + 10

    def __init__(self, agent_id=None, runtime=CODEX):
        self.agent_id = agent_id     # 要叫的那一個 agent(修改、送出叫回填這張的那一個)
        self.runtime = runtime
        self.native_json_output = runtime == CODEX
        self.workspace = None

    def profile_reader(self, board=None):
        """給 profile_sync.check 的讀頁函式(讀取網址 → 頁面)。"""
        return lambda read_url: self.read_profile(read_url, board)

    @staticmethod
    def workspaces():
        return _ego('return (await listTaskSpaces()).map(s=>({id:s.id,name:s.name,ownership:s.ownership}));')

    def apply_rule(self):
        return (
            '【agent 的瀏覽器】ego lite,用 ego-browser 指令操作;下面是這一輪用得到的全部用法,不用另讀 skill 全文'
            f'(真的需要清單外的 API 才查 {os.path.dirname(EGO_SKILL)}/references/api.md 對應那一段):\n'
            "  ego-browser nodejs <<'EOF' … EOF(腳本跑在 Node.js;console.log 的內容就是回傳;要讀本機檔用 await import(\"node:fs/promises\"))\n"
            '  const task = await taskSpace(工作區編號); const page = task.page("p1");  // 本輪有指定就用指定的編號與 Page\n'
            '  page.goto(url) / page.snapshot()(回 @ref) / page.fill(sel, 文字) / page.selectOption(sel, {label}) / page.click(sel)\n'
            '  sel 可用 @ref、loc=role:button[name="…"]、loc=css:…、text=…;一個 sel 要剛好對到一個元素\n'
            '  按鈕在 iframe 裡:快照裡找到 iframe 的 @ref,再 page.snapshot({scope:"subtree",root:"@ref"}) 拿裡面的 @ref 來點\n'
            '  page.waitForSelector(sel) / page.waitForURL(…) / page.waitForFunction(fn) / page.evaluate(fn, 參數)\n'
            '  page.setInputFiles(sel, [完整路徑]);選檔器:const c = page.waitForFileChooser(); 點上傳; (await c).setFiles(路徑)\n'
            '  點擊回傳 receipt.popups[0].label → task.page(label) 接新開的頁;page.fetch(url,{saveAs}) 取檔\n'
            '  每次執行是新的 Node 行程,變數不保留;工作區和 Page 編號保留。腳本最後印出下一步要用的快照。\n'
            '只用 ego-browser nodejs。本輪有指定工作區與 Page(幫你填表)就只用它,接續用數字編號,不另建;'
            '沒有指定的(查應徵進度、找缺補查):自己開一個 taskSpace("jobsalvo-這一輪做什麼"),整輪只用這一個,做完 finish({keep:[]})。'
            '先依頁面觀察確認目標,同一頁已知的讀取、填寫、上傳在一段腳本做完,等完成狀態再觀察。'
            '點擊後用回傳的 receipt.popups 接到同工作區的新 Page;同頁轉址就繼續用原 Page。'
            'waitForEvent 先保存 promise,做完觸發動作才 await;事件逾時時重新觀察原頁與 task.tabs,換正常方法繼續。'
            '上傳用要寄的檔案的完整路徑;下載在同一段腳本存到程式指定的暫存資料夾。'
            '不清 cookie、快取或儲存區,不讀取憑證;不操作使用者本人的 Chrome、桌面或其他工作區。'
            '不開 App、不用桌面指令。'
            '輸入文字用 fill 或 keyboard.insertText;不用 keyboard.paste(它在 Mac 上送出真的 ⌘V,ego 會當成使用者在操作,'
            '把工作區轉給他,這一輪就停了)。'
            '登入與授權依本輪規矩先試已登入帳號的 SSO;確實需要本人處理時照【回報】寫明網站與要做的事;'
            '保留工作區與頁面,使用者按 👀 接手,按「修改」後才繼續。'
            '本輪規則優先於 skill 的自動交接建議:不得呼叫 task.handOff 或 takeOverTaskSpace;程式指定的工作區也不要 finish,'
            '卡住或結束都維持 agent 控制權,交接與接回只由程式按使用者的按鈕處理。\n\n'
        )

    def fetch_rule(self):
        return (
            '上傳用 page.setInputFiles(selector,[完整路徑]);需要選檔器時先 '
            'page.waitForFileChooser(),再點上傳控制並 chooser.setFiles。讀回當頁檔案狀態。'
            '一般表單的檔案在按送出時才傳送:送出前確認檔案欄已選妥正確檔名與大小,列入 uploaded 及 uploaded_from,'
            'upload_readback 寫 unavailable;不可為了確認傳送而按送出,也不把尚未送出當成上傳失敗。'
            '頁面採非同步上傳時,必須等網站確認上傳完成才列入 uploaded。'
            '取檔用 page.fetch(href,{saveAs:完整暫存路徑}),失敗先看頁面;'
            '需要瀏覽器下載時先 page.waitForEvent("download"),再點現場連結,'
            '並在同一段腳本 await download.saveAs(完整暫存路徑)。不可拿本機檔冒充下載檔。'
        )

    def ready(self, board=None):
        if not ego_bin():
            return False, '沒有安裝 ego 的瀏覽器指令', '安裝 ego lite 並完成第一次匯入'
        if not imported():
            return False, 'ego 尚未完成第一次匯入', '打開 ego lite 完成第一次匯入'
        if not os.path.isfile(EGO_SKILL):
            return False, '缺少 ego-browser skill', '安裝 ego-browser skill 後再試'
        try:
            _ego('return (await listTaskSpaces()).map(s => ({id:s.id, ownership:s.ownership}));', timeout=15)
        except NotNow as e:
            return False, str(e), '確認 ego lite 已開啟並完成第一次匯入'
        return True, '', ''

    def configured(self):
        return bool(ego_bin() and imported() and os.path.isfile(EGO_SKILL))

    def open_for_agent(self, url, old_tab=None, on_open=None):
        import apply_tab
        if self.workspace is not None:
            return _ego(self._task(old_tab) + BODY_READY + f'const data=await page.evaluate({apply_tab.PAGE_FN});\n'
                        'return {workspace:binding,tab_id:binding.id+":"+binding.page,page:data};')
        name = _workspace_name()
        opened = _ego(
            f'const task = await taskSpace({json.dumps(name)});\n'
            'const page = task.page("p1");\n'
            'return {workspace:{id:task.spaceId,name:task.name,page:page.label},'
            'tab_id:task.spaceId+":"+page.label};'
        )
        self.workspace = opened['workspace']
        if on_open:
            on_open(opened)
        return _ego(self._task() +
                    f'await page.goto({json.dumps(url)}, {{waitUntil:"domcontentloaded",timeout:30000}});\n'
                    + BODY_READY + f'const data=await page.evaluate({apply_tab.PAGE_FN});\n'
                    'return {workspace:binding,tab_id:binding.id+":"+binding.page,page:data};')

    def fetch_file(self, url, dest):
        """用這張卡的工作區(帶著登入)把一份檔下載到 dest:先開那個檔的網址,同一個來源再取一次真的位元組。"""
        dest = os.path.abspath(dest)
        script = self._task(page=False) + (
            'const pg = await task.newPage();\n'
            'try {\n'
            f'await pg.goto({json.dumps(url)}, {{waitUntil:"domcontentloaded",timeout:30000}}).catch(() => null);\n'
            f'const r = await pg.fetch({json.dumps(url)}, {{saveAs:{json.dumps(dest)},credentials:"include",timeout:30000}});\n'
            'return {ok:r.ok,status:r.status,type:r.headers["content-type"]||""};\n'
            '} finally { await pg.close(); }\n')
        result = _ego(script, timeout=90)
        if not result.get('ok') or 'text/html' in result.get('type', ''):
            raise NotNow(f'下載回傳 {result.get("status")} {result.get("type")}(可能要登入)')
        return dest

    def follow_page(self, tab_id):
        """agent 把申請表留在這張卡工作區裡的另一頁(新分頁、彈出視窗):改綁那一頁。不是這個工作區的頁丟 NotNow。"""
        w = self.workspace
        space, _, label = str(tab_id).partition(':')
        if not isinstance(w, dict) or space != str(w.get('id')) or not label:
            raise NotNow('agent 說的頁面不在這張卡的工作區裡')
        _ego(self._task(page=False) + f'if (!(await task.tabs()).some(t => t.label==={json.dumps(label)})) '
             'throw new Error("頁面不見了");\nreturn true;')
        w['page'] = label
        return f'{w["id"]}:{label}'

    def _task(self, tab_id=None, page=True, take_back=False):
        """核對數字、名稱、Page 三者;不把重用的工作區編號綁到另一張卡。"""
        w = self.workspace
        if not isinstance(w, dict) or not isinstance(w.get('id'), int) or not w.get('name') or not w.get('page'):
            raise Unreachable(BEFORE_UPDATE)
        if tab_id is not None and str(tab_id) != f'{w["id"]}:{w["page"]}':
            raise NotNow('回報的頁面不是這張卡記著的工作區')
        script = (
            f'const binding = {json.dumps(w)};\n'
            'const found = (await listTaskSpaces()).find(s => s.id===binding.id && s.name===binding.name);\n'
            'if (!found) throw Object.assign(new Error("工作區不見了"),{code:"workspace_missing"});\n'
            + ('if (found.ownership!=="agent") await takeOverTaskSpace(binding.id);\n' if take_back else
               'if (found.ownership!=="agent") throw new Error("工作區正由使用者接手,按修改後才接回");\n')
            + 'const task = await taskSpace(binding.id);\n'
        )
        if page:
            script += (
                'if (!(await task.tabs()).some(t => t.label===binding.page)) '
                'throw Object.assign(new Error("頁面不見了"),{code:"page_missing"});\n'
                'const page = task.page(binding.page);\n'
            )
        return script

    def read_page(self, tab_id):
        import apply_tab
        return _ego(self._task(tab_id) + BODY_READY + f'return await page.evaluate({apply_tab.PAGE_FN});')

    def shot(self, tab_id, out):
        import evidence
        out = os.path.abspath(out)
        with contextlib.suppress(FileNotFoundError):
            os.remove(out)
        record = evidence.active()
        total_started = time.monotonic()
        for attempt in range(1, self.SHOT_ATTEMPTS + 1):
            started, ok, reason = time.monotonic(), False, ''
            try:
                _ego(self._task(tab_id) + f'return await page.screenshot({{path:{json.dumps(out)},fullPage:true}});',
                     timeout=self.SHOT_TIMEOUT)
                with open(out, 'rb') as f:
                    if f.read(8) != b'\x89PNG\r\n\x1a\n':
                        raise NotNow('ego 沒有傳回有效頁面截圖')
                ok = True
                return out
            except NotNow as e:
                reason = str(e)
                if 'CDP request timed out: Page.captureScreenshot' not in reason:
                    raise
                if attempt == self.SHOT_ATTEMPTS:
                    raise NotNow(f'這張頁面截圖暖機 {attempt} 次仍失敗:{reason}') from e
            finally:
                if not ok:
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(out)
                if record:
                    record.note('shot_attempt', workspace=dict(self.workspace or {}), attempt=attempt,
                                limit=self.SHOT_ATTEMPTS, retry_count=attempt - 1, ok=ok, reason=reason,
                                seconds=round(time.monotonic() - started, 3),
                                elapsed_seconds=round(time.monotonic() - total_started, 3))

    def print_pdf(self, html_path, pdf_path, timeout=120):
        """本機排版只用自己的暫時工作區;斷網、印完收掉,不碰卡片的頁。"""
        if self.workspace is not None:
            raise NotNow('PDF 排版必須使用獨立的暫時工作區')
        opened = None
        try:
            opened = self.open_for_agent('about:blank')
            result = _ego(self._task() +
                          'await page.cdp("Network.enable");\n'
                          'await page.cdp("Network.setBlockedURLs",{urls:["http:*","https:*","ftp:*","ws:*","wss:*"]});\n'
                          'await page.cdp("Network.emulateNetworkConditions",{offline:true,latency:0,'
                          'downloadThroughput:-1,uploadThroughput:-1});\n'
                          f'await page.goto({json.dumps(Path(html_path).resolve().as_uri())},'
                          '{waitUntil:"load",timeout:30000});\n'
                          'await page.evaluate(async()=>{await document.fonts.ready;});\n'
                          'return await page.cdp("Page.printToPDF",{preferCSSPageSize:true,'
                          'displayHeaderFooter:false,paperWidth:8.5,paperHeight:11,'
                          'marginTop:0.4,marginBottom:0.4,marginLeft:0.4,marginRight:0.4});', timeout=timeout)
            try:
                content = base64.b64decode(result['data'], validate=True)
            except (KeyError, ValueError, TypeError) as error:
                raise NotNow('ego 沒有傳回有效 PDF') from error
            if not content.startswith(b'%PDF-'):
                raise NotNow('ego 沒有傳回有效 PDF')
            destination = Path(pdf_path).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix='.ego-pdf-', dir=destination.parent)
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    output.write(content)
                os.replace(temporary, destination)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temporary)
        finally:
            if opened or self.workspace:
                self.release((opened or {}).get('tab_id'))

    def read_pages(self, urls, board=None, ready=None, settle=0):
        import apply_tab
        temporary = self.workspace is None
        if temporary:
            script = f'const task = await taskSpace({json.dumps(_workspace_name("read-"))});\n'
            script += 'const reader = task.page("p1");\n'
            script += 'try {\n'
        else:
            script = self._task(page=False)
            script += (
                'const tabs = await task.tabs();\n'
                'const reader = binding.reader && tabs.some(t=>t.label===binding.reader) '
                '? task.page(binding.reader) : await task.newPage();\n'
            )
        script += (
            f'const urls = {json.dumps(list(urls))}, pages = {{}};\n'
            'for (const url of urls) {\n'
            'await reader.goto(url,{waitUntil:"domcontentloaded",timeout:30000});\n'
            'let previous="", stable=0, data;\n'
            'for (let i=0;i<50;i++) {\n'
            f'data = await reader.evaluate({apply_tab.PROFILE_FN});\n'
            'const signature=JSON.stringify(data);\n'
            'stable=signature===previous ? stable+1 : 0; previous=signature;\n'
            f'if (data.readyState!=="loading" && data.text.length>200 && stable>={max(0, int(settle))}) break;\n'
            'await reader.waitForTimeout(500);\n'
            '} pages[url]=data; }\n'
        )
        script += 'return {pages,reader:reader.label};\n'
        if temporary:
            script += '} finally {await task.finish({keep:[]});}\n'
        result = _ego(script, timeout=max(60, len(urls) * 60))
        if not temporary:
            self.workspace['reader'] = result['reader']
        for data in result['pages'].values():
            data['_ready'] = bool((ready or (lambda p: len(p.get('text', '')) > 200))(data))
        return result['pages']

    def read_profile(self, read_url, board=None):
        return self.read_pages([read_url], board, settle=2)[read_url]

    def download_attachments(self, read_url, directory):
        """從目前履歷頁取全部附件的真 bytes,包括多出來的檔;不採信模型的清單或雜湊。"""
        import profile_sync as ps
        data = self.read_profile(read_url)
        if not data.get('_ready') or ps.identity(data.get('url')) != ps.identity(read_url):
            raise NotNow('平台履歷沒讀完整或已轉到另一頁,附件未核對')
        root = os.path.realpath(directory)
        os.makedirs(root, exist_ok=True)
        script = self._task(page=False) + (
            'const page = task.page(binding.reader);\n'
            'const fs = await import("node:fs/promises"), path = await import("node:path");\n'
            f'const root={json.dumps(root)};\n'
            'const links = await page.evaluate(() => {\n'
            'const seen=new Set(), ext=/[.](pdf|docx?|pptx?|odt|rtf|zip|png|jpe?g)$/i;\n'
            'return [...document.querySelectorAll("a[href]")].flatMap((a,i)=>{\n'
            'const u=new URL(a.href,location.href), name=(a.getAttribute("download")||a.innerText||"").trim().split("\\n")[0];\n'
            'if (seen.has(u.href) || !["http:","https:"].includes(u.protocol) || '
            '!(a.hasAttribute("download")||ext.test(name)||ext.test(u.pathname))) return [];\n'
            'seen.add(u.href);return [{href:u.href,name:name||u.pathname.split("/").pop(),selector:"a[href] >> nth="+i}];});});\n'
            'const files=[], problems=[];\n'
            'for (const [i,link] of links.entries()) {\n'
            'const out=path.join(root,"attachment-"+i+"-"+Date.now()+".bin");\n'
            'try {\n'
            'try { const r=await page.fetch(link.href,{saveAs:out,credentials:"include",timeout:30000});\n'
            'if (!r.ok || /text\\/html/i.test(r.headers["content-type"]||"")) throw new Error("下載回傳登入頁或錯誤:"+r.status);\n'
            '} catch (fetchError) {\n'
            'await fs.rm(out,{force:true});\n'
            'const pending=page.waitForEvent("download",{timeout:30000});\n'
            'await page.click(link.selector);\n'
            'const download=await pending;await download.saveAs(out);\n'
            '}\n'
            'files.push({name:link.name,path:out});\n'
            '} catch (e) {problems.push(link.name+":"+String(e.message||e));}\n'
            '} return {files,problems};\n'
        )
        result = _ego(script, timeout=max(60, len(data.get('links') or []) * 60))
        for item in result['files']:
            candidate = os.path.realpath(item['path'])
            if os.path.commonpath([root, candidate]) != root:
                raise NotNow('ego 下載檔不在這一輪的暫存資料夾')
            with open(candidate, 'rb') as f:
                content = f.read()
            if not content or content.lstrip().lower().startswith((b'<!doctype html', b'<html')):
                raise NotNow('ego 下載到空檔或登入頁,附件未核對')
            item.update(path=candidate, size=len(content), sha256=hashlib.sha256(content).hexdigest())
        return result

    def release(self, tab_id):
        # 收尾只發生在這張卡送出、移除、頁面不見之後:他接手過也收(不然工作區永遠留著)
        receipt = _ego(self._task(tab_id, page=False, take_back=True) + 'return await task.finish({keep:[]});')
        self.workspace = None
        return receipt

    def human_action(self):
        return _ego(self._task(page=False) +
                    'for (const tab of (await task.tabs()).reverse()) {\n'
                    'if (!tab.label) continue;\n'
                    'const p=task.page(tab.label);\n'
                    'const action=await p.evaluate(()=>{\n'
                    'const visible=e=>!!(e.getClientRects().length && getComputedStyle(e).visibility!=="hidden");\n'
                    'const password=[...document.querySelectorAll("input[type=password]")].some(visible);\n'
                    'const text=(document.title+" "+document.body.innerText).toLowerCase();\n'
                    'const verify=/驗證您是人類|正在執行安全驗證|verify you are human|performing security verification|checking your browser/.test(text)'
                    ' || /密碼金鑰.*(?:確認|本人)|passkey.*(?:confirm|verify|verification)|(?:confirm|verify).*passkey/.test(document.title.toLowerCase());\n'
                    'const login=password || /sign in required|請本人登入/.test(text);\n'
                    'if (!login && !verify) return null;\n'
                    'return {site:location.hostname,need:"在 "+location.hostname+(verify?" 完成本人驗證":" 完成登入")};\n'
                    '});\n'
                    'if (action) return {page:tab.label,...action};\n'
                    '}return null;')

    def hand_off(self):
        """使用者在卡上按 👀 時把那一頁交給他:ego 裡切到那一頁,再把 ego 叫到他面前。"""
        receipt = self._hand_off_page()
        try:
            subprocess.run(['open', '-a', 'ego lite'], check=True, timeout=15, capture_output=True)
        except (OSError, subprocess.SubprocessError) as e:
            raise NotNow(f'叫不出 ego lite({str(e)[:80]}),請自己打開 ego lite 找這張卡的工作區') from e
        return receipt

    def _hand_off_page(self):
        return _ego(self._task(page=False) +
                    'const label=binding.handoff_page || binding.page;\n'
                    'const target=(await task.tabs()).find(t=>t.label===label);\n'
                    'if (!target) throw Object.assign(new Error("接手頁面不見了"),{code:"page_missing"});\n'
                    'const page=task.page(label);\n'
                    'await page.cdp("Page.bringToFront");\n'
                    'await page.cdp("Runtime.evaluate",{expression:"window.focus()",userGesture:true});\n'
                    'const w=await task.cdp("Browser.getWindowForTarget",{targetId:target.targetId});\n'
                    'const shown=await page.evaluate(()=>({site:location.hostname,focused:document.hasFocus()}));\n'
                    'await task.handOff();return {ownership:"user",page:label,...shown,shown:w.bounds};')

    def resume(self):
        w = self.workspace
        if not w:
            raise Unreachable(BEFORE_UPDATE)
        _ego(f'const binding={json.dumps(w)}, found=(await listTaskSpaces()).find(s=>s.id===binding.id && s.name===binding.name);\n'
             'if (!found) throw Object.assign(new Error("工作區不見了"),{code:"workspace_missing"});\n'
             'if (found.ownership!=="agent") await takeOverTaskSpace(binding.id);\n'
             'return binding;')
        self.workspace.pop('handoff_page', None)


DOORS = {CODEX: EgoDoor, CLAUDE: EgoDoor}


def of(runtime, agent_id=None):
    """那一家的門路(要叫的是 agent_id 那一個);不能開 Chrome 的(Command Code、沒記到)回 None。"""
    kind = DOORS.get(runtime)
    return kind(agent_id, runtime=runtime) if kind else None


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
    workspace = apply.get('workspace')
    if workspace:
        enabled = [a for a in _agents() if a.get('browser') and a.get('runtime') in DOORS]
        if not enabled:
            import config as cf
            broken = cf.settings_problem()
            raise Unreachable(SETTINGS_UNREADABLE.format(why=broken) if broken else NO_BROWSER_AGENT, sure=False)
        same = [a for a in enabled if a.get('runtime') == apply.get('runtime')]
        choices = same or enabled
        chosen = next((a for a in choices if a.get('id') == apply.get('agent_id')), choices[0])
        door = of(chosen['runtime'], chosen['id'])
        door.workspace = dict(workspace)
        return door
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
