#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chrome_door —— 程式這一側碰「agent 的瀏覽器」(ego lite)的門路,收在這一處(docs/adr/0006)。

呼叫的地方不問「是不是 Claude」,只拿一個門路來用(兩家共用 EgoDoor;runtime 只決定叫回哪個 agent):
  of(runtime)      那一家的門路(不能開瀏覽器的回 None)
  current()        設定裡勾「用它操作 ego」的那一家(派新工作、查應徵進度用)
  for_card(apply)  這張卡記的那一家和工作區(之後看、改、送出、關掉都找同一個);那一家停用或移除、卡上沒記 → Unreachable

門路對外提供的事(做不到的丟 NotNow,訊息講現在做不到、改用什麼):
  讀那一頁 read_page、讀平台履歷頁 read_profile、截圖 shot、
  放掉分頁 release、派工前確認 ego 準備好 ready、程式先開好申請頁 open_for_agent、給 agent 的瀏覽器用法
  (apply_rule、fetch_task_tools)、查應徵進度讀頁 read_pages、接手 hand_off / resume、設定好了沒 configured、
  看板 👀 怎麼截(live_timeout、live_refresh)、工作區不在了 gone_pages、收尾 close_if_idle。
"""
import os, sys, shutil, json, subprocess, uuid, time, contextlib
import base64
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CODEX, CLAUDE = 'codex', 'claude-code'
PAGE_MATERIAL_CHAR_LIMIT = 100_000



# 讀一頁的唯讀函式,在頁面裡跑(瀏覽器那一側,ADR-0012:轉成段落在 web/seen.py)
PROFILE_FN = "() => {\n  const labelFor = el => {\n    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)\n      .map(id => document.getElementById(id)?.innerText || '').join(' ');\n    const labels = el.labels ? [...el.labels].map(x => x.innerText || '').join(' ') : '';\n    return (el.getAttribute('aria-label') || labelled || labels || el.getAttribute('placeholder') || '').trim();\n  };\n  const sectionFor = el => {\n    const group = el.closest('fieldset,section,[role=group],[role=radiogroup],[role=listbox]');\n    const heading = group && group.querySelector('legend,[role=heading],h1,h2,h3,h4,h5');\n    return group ? (labelFor(group) || (heading && heading.innerText) || '').trim() : '';\n  };\n  const fileNames = el => [...(el.files || [])].map(file => file.name).join(', ');\n  // 保留欄位上下文與選取狀態(勾選、aria),不猜哪些值是個人聲明;送出、按鈕類 input 不是資料\n  const fields = [...document.querySelectorAll('input,textarea,select,[role=textbox],[role=combobox],[role=checkbox],[role=radio],[role=switch],[role=listbox],[role=option],[role=slider],[role=spinbutton],[contenteditable=true],[aria-selected],[aria-checked],[aria-pressed],[aria-valuenow],[aria-valuetext],[aria-activedescendant],[aria-expanded]')]\n    .filter(el => !['hidden','password'].includes((el.type || '').toLowerCase()))\n    .filter(el => !(el.tagName === 'INPUT' && ['submit','button','image','reset'].includes((el.type || '').toLowerCase())))\n    .filter(el => {const s = getComputedStyle(el); return el.getClientRects().length && s.display !== 'none' && s.visibility !== 'hidden';})\n    .map(el => ({id: el.id || '', label: labelFor(el), section: sectionFor(el), role: el.getAttribute('role') || '',\n      type: (el.type || el.tagName.toLowerCase()).toLowerCase(), autocomplete: el.getAttribute('autocomplete') || '',\n      text: el.innerText || '', parentText: el.parentElement?.innerText || '',\n      value: el.type === 'file' ? fileNames(el) : (el.isContentEditable ? el.innerText : el.value) ?? '',\n      shown: el.tagName === 'SELECT' ? [...el.selectedOptions].map(x => x.text).join(' ') : undefined,\n      checked: ['checkbox','radio'].includes(el.type) ? el.checked : undefined,\n      aria: Object.fromEntries(['aria-selected','aria-checked','aria-pressed','aria-expanded','aria-activedescendant',\n        'aria-valuenow','aria-valuetext','aria-controls','aria-labelledby','aria-describedby'].map(a => [a, el.getAttribute(a)]))}));\n  // 收起來的內容(「更多」、摺疊區)常已在 DOM 裡只是沒畫出來;innerText 讀不到,判讀就說「無法確認」。\n  // 把沒畫出來的文字接在後面(跳過程式碼類標籤),不靠各網站的按鈕名稱。\n  // display:contents 本身沒有外框,但祖先有外框時子節點仍然可見。\n  const drawn = el => !!el && (el.getClientRects().length > 0\n    || (getComputedStyle(el).display === 'contents' && drawn(el.parentElement)));\n  const body = document.body || document.documentElement;\n  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);\n  const collapsed = [];\n  for (let n = walker.nextNode(); n; n = walker.nextNode()) {\n    const p = n.parentElement, s = (n.nodeValue || '').trim();\n    if (!s || !p || p.closest('script,style,noscript,template,svg') || drawn(p)) continue;\n    collapsed.push(s);\n  }\n  const readingParts = [], readingRects = [];\n  const documentRect = node => {\n    const boxes = node.nodeType === 3 ? (() => {\n      const range = document.createRange(); range.selectNodeContents(node);\n      return [...range.getClientRects()];\n    })() : [node.getBoundingClientRect()];\n    const visible = boxes.filter(r => r.width > 0 && r.height > 0);\n    if (!visible.length) return null;\n    const x = Math.min(...visible.map(r => r.left)) + scrollX;\n    const y = Math.min(...visible.map(r => r.top)) + scrollY;\n    return {x, y, width: Math.max(...visible.map(r => r.right)) + scrollX - x,\n      height: Math.max(...visible.map(r => r.bottom)) + scrollY - y};\n  };\n  // 欄位值是使用者打的字,照讀到的樣子收(前後空白、只有空白都是值,ADR-0011);\n  // 文字節點前後的空白是排版用的原始碼,畫出來看不到,才去掉\n  const addReading = (value, node, asIs = false) => {\n    const text = String(value ?? '');\n    if (asIs ? !text : !text.trim()) return;\n    readingParts.push(asIs ? text : text.trim());\n    readingRects.push(documentRect(node));\n  };\n  const TOGGLES = [['aria-checked', {true: '已勾選', false: '未勾選', mixed: '部分勾選'}],\n                   ['aria-pressed', {true: '已按下', false: '未按下', mixed: '部分按下'}]];\n  const readableUrl = value => { try { return decodeURIComponent(value); } catch (_error) { return value; } };\n  const visit = node => {\n    if (node.nodeType === 3) { addReading(node.nodeValue, node); return; }\n    if (node.nodeType !== 1) return;\n    const el = node;\n    if (el.closest('script,style,noscript,template,svg') || !drawn(el)) return;\n    const tag = el.tagName;\n    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) {\n      if (el.type === 'file') { addReading(fileNames(el), el, true); return; }\n      if (['hidden', 'password', 'submit', 'button', 'image', 'reset'].includes((el.type || '').toLowerCase())) return;\n      if (el.type === 'checkbox' || el.type === 'radio') addReading(`⟦欄位 ${el.checked ? '已勾選' : '未勾選'}⟧`, el);\n      else addReading(tag === 'SELECT' ? [...el.selectedOptions].map(x => x.text).join(' ') : el.value, el, true);\n      return;\n    }\n    for (const child of el.childNodes) visit(child);\n    if (tag === 'A' && el.href) addReading(`⟦網址 ${readableUrl(el.href)}⟧`, el);\n    if (['IFRAME', 'VIDEO', 'AUDIO', 'SOURCE', 'EMBED', 'OBJECT'].includes(tag)) {\n      const url = el.src || el.data;\n      if (url) addReading(`⟦網址 ${readableUrl(url)}⟧`, el);\n    }\n    if (['option', 'radio', 'checkbox'].includes(el.getAttribute('role')) && el.hasAttribute('aria-selected'))\n      addReading(`⟦欄位 ${el.getAttribute('aria-selected') === 'true' ? '已選取' : '未選取'}⟧`, el);\n    // 自製開關(role=switch 等)、切換鈕的狀態只在 aria-checked、aria-pressed 上,畫面上的字不變;不認得的值照原樣寫\n    for (const [attr, words] of TOGGLES) if (el.hasAttribute(attr)) {\n      const state = el.getAttribute(attr);\n      addReading(`⟦欄位 ${words[state] || attr + '=' + state}⟧`, el);\n    }\n  };\n  visit(body);\n  return {url: location.href, title: document.title, readyState: document.readyState, sinceLoad: performance.now(),\n    documentKey: performance.timeOrigin,\n    readingParts,\n    readingRects,\n    documentSize: {width: Math.max(document.documentElement?.scrollWidth || 0, body?.scrollWidth || 0),\n      height: Math.max(document.documentElement?.scrollHeight || 0, body?.scrollHeight || 0)},\n    reading: readingParts.join(' ').replace(/\\s+/g, ' ').trim(),\n    text: (body.innerText || '') + (collapsed.length ? '\\n' + collapsed.join('\\n') : ''),\n    links: [...document.links].map(a => a.href),\n    media: [...document.querySelectorAll('iframe,video,audio,source,embed,object')].map(el => el.src || el.data).filter(Boolean),\n    anchors: [...document.links].map(a => ({href: a.href, text: (a.innerText || '').trim()})), fields};\n}"

class NotNow(LookupError):
    """這一家現在做不到(不是壞了):訊息講做不到、改用什麼。"""


class Unreachable(LookupError):
    """填這張的 agent 接不回來:卡上沒記是哪一家,或那一家已經停用、移除(之後這一頁算頁面不見了,要重填)。
    訊息就是卡上要寫的那一句。sure=False:現在判斷不了(設定檔讀不懂),不准因此把卡標成頁面不見了。
    why:ego 自己說的原因;不是填表卡的地方(拍照工具)照它記,不記卡上那一句。"""

    def __init__(self, msg, sure=True, why=''):
        super().__init__(msg)
        self.sure = sure
        self.why = why or msg


AGENT_SWAPPED = '填這張的 agent 換掉了,要重填'
NO_BROWSER_AGENT = '設定裡沒有會操作瀏覽器的 agent:到看板「⚙ 設定 → 🤖 Agent 與瀏覽器」勾一個「用它操作 ego」'
# 開始記「哪一家開的」之前填的卡(#311 預設 A):不是換掉了,是程式不知道;一樣接不回來
BEFORE_UPDATE = '這張是更新前填的,程式不知道是哪個 agent 開的頁,要重填'
# ego 關掉最後一頁會留一個沒有標籤的空白分頁:沒有標籤就沒辦法叫它讀或截圖
NO_TAB_LABEL = 'ego 沒給這個分頁標籤,無法指名讀取或截圖'
SETTINGS_UNREADABLE = ('設定檔 jobsalvo.json 讀不懂({why}),現在不知道填這張的 agent 還在不在;'
                       '先到看板「⚙ 設定」存一次(或修好那個檔)再試')


def configured():
    return EgoDoor().configured()


def gone_pages(fb, running_url='', proc=None):
    import delivery_core as ds
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
    """頁不在 ego 裡的卡改標成「頁面不見了,要重填」。副本不動:ego 整台電腦只有一個,副本的卡不是它填的。"""
    import board_doc as bd
    import delivery_core as ds
    if not bd.is_live(live):
        return {}
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
            receipt = door.release(f'{w["id"]}:{w["page"]}' if w.get('page') else None)    # 不知道是哪一頁(F-20)就只核對工作區
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


from delivery_core import GONE as GONE  # noqa: E402 — 卡上的失聯訊息由狀態表決定


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
    """launchd 的 PATH 也能找到 onboarding 安裝的指令;PATH 上的截圖轉接器(tools/ego-browser)不算,它要轉交的就是這一支。"""
    wrapper = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ego-browser'))
    search = os.pathsep.join(part for part in os.environ.get('PATH', os.defpath).split(os.pathsep)
                             if os.path.realpath(os.path.join(part, 'ego-browser')) != wrapper)
    found = shutil.which('ego-browser', path=search)
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
                raise Unreachable(GONE, why=str(message.get('error') or ''))
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


def workspace_pages(tabs):
    """工作區每個分頁讀到的東西 → 給任務的頁面清單:讀不到、太長、沒有字的頁照實標出,不丟掉;沒有分頁就是空清單。
    tabs:每個分頁一筆 {tab_id, url, title} 加上 error(讀取丟錯),或 size 與 data(size 沒超過上限才有 data)。"""
    pages = []
    for tab in tabs:
        tab_id, url = str(tab['tab_id']), str(tab.get('url') or '')
        where = f'工作區分頁 {tab_id}（{url or "網址不明"}）'
        row = {'tab_id': tab_id, 'url': url, 'title': str(tab.get('title') or '')}
        if 'error' in tab:
            pages.append(dict(row, read_status='unreadable', read_error=f'{where}讀取失敗：{tab["error"]}',
                              screenshot_error=str(tab.get('screenshot_error') or '')))
        elif tab['size'] > PAGE_MATERIAL_CHAR_LIMIT:
            pages.append(dict(row, read_status='too_large',
                              read_error=f'{where}的讀取材料超過 {PAGE_MATERIAL_CHAR_LIMIT} 字元上限'))
        elif not str(tab['data'].get('reading') or tab['data'].get('text') or '').strip():
            pages.append(dict(tab['data'], tab_id=tab_id, read_status='no_text', read_error=f'{where}沒有可讀文字'))
        else:
            pages.append(dict(tab['data'], tab_id=tab_id, read_status='readable'))
    return pages


class EgoDoor:
    """agent 的瀏覽器(ego lite)的門路。Codex、Claude 兩個 CLI 共用;runtime 只用於選 agent 和接回對話。"""
    live_refresh = 4             # 看板 👀 幾秒後自動再截
    SHOT_TIMEOUT = 40  # 包住本機 SDK 的 30 秒 CDP 逾時,不用等待取代重試。
    live_timeout = SHOT_TIMEOUT + 10

    def __init__(self, agent_id=None, runtime=CODEX):
        self.agent_id = agent_id     # 要叫的那一個 agent(修改、送出叫回填這張的那一個)
        self.runtime = runtime
        self.workspace = None

    @staticmethod
    def workspaces():
        return _ego('return (await listTaskSpaces()).map(s=>({id:s.id,name:s.name,ownership:s.ownership}));')

    def apply_rule(self):
        """操作 agent 瀏覽器的任務共用的前綴:只有工具用法與界線,不教網站怎麼操作、什麼時候停(#601 Solution 1)。"""
        return (
            '【agent 的瀏覽器工具】ego lite;用 ego-browser nodejs 執行 Node.js 腳本:'
            "`ego-browser nodejs <<'EOF' … EOF`;console.log 的內容是回傳值;本機檔案用 await import(\"node:fs/promises\")。"
            f'API 參考:{os.path.dirname(EGO_SKILL)}/references/api.md。\n'
            '工作區:const task = await taskSpace(工作區編號); const page = task.page("Page 標籤");編號與標籤照本輪材料。'
            'task.tabs() 列出分頁。頁面 API:page.goto(url)、page.snapshot()(回 @ref;'
            'page.snapshot({scope:"subtree",root:"@ref"}) 取某個元素裡面的 @ref)、page.fill(selector,text)、'
            'page.selectOption(selector,{label})、page.click(selector)、page.keyboard.type(text)、'
            'page.keyboard.insertText(text)、page.setInputFiles(selector,paths)、page.waitForSelector(selector)、'
            'page.waitForURL(url)、page.waitForFunction(fn)、page.waitForEvent(name)、page.evaluate(fn,args)、'
            'page.waitForFileChooser()(回傳的 chooser 用 setFiles(paths))。'
            'selector 可用 @ref、loc=role:button[name="…"]、loc=css:… 或 text=…;'
            '一個 selector 應對到一個元素。點擊回傳的 receipt.popups 用 task.page(label) 取得新分頁。'
            'waitForEvent 的 promise 要在觸發動作之前取得。每次執行是新 Node 行程,變數不保留;工作區與 Page 編號會保留。\n'
            '【界線】只用 ego-browser nodejs 與本輪指定的工作區;不操作使用者本人的 Chrome、桌面、App 或其他工作區。'
            '不清 cookie、快取或儲存區,不讀取憑證;不索取、不輸入使用者密碼。'
            '輸入文字不用 keyboard.paste(它在 Mac 上送出真的 ⌘V,ego 會把工作區轉給使用者)。'
            '不得呼叫 task.handOff 或 takeOverTaskSpace,程式指定的工作區不要 finish;交接與接回只由程式做:'
            '頁面要本人處理時(密碼、二步驗證、驗證碼、真人驗證),照任務交件格式逐字引用那一頁的原文、寫本人要做的事,'
            '程式重讀同一分頁核對引文後才交接。\n\n'
        )

    def fetch_task_tools(self):
        return (
            '附件 API：page.setInputFiles(selector,[完整路徑])、page.waitForFileChooser()、'
            'chooser.setFiles(paths)。'
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

    def new_workspace(self, *, blank=True):
        """開一個新的 agent 工作區;網站任務從一個空白分頁開始。"""
        if not blank:
            raise ValueError('新工作區只允許從空白分頁開始')
        if self.workspace is not None:
            raise NotNow('這個瀏覽器轉接器已經綁定工作區')
        name = _workspace_name()
        opened = _ego(
            f'const task = await taskSpace({json.dumps(name)});\n'
            'const page = task.page("p1");\n'
            'return {workspace:{id:task.spaceId,name:task.name,page:page.label},'
            'tab_id:task.spaceId+":"+page.label};'
        )
        self.workspace = opened['workspace']
        return opened

    def open_for_agent(self, url, old_tab=None, on_open=None):
        import apply_tab
        if self.workspace is not None:
            return _ego(self._task(old_tab) + f'const data=await ({apply_tab.READ_FORM})(page);\n'
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
                    + f'const data=await ({apply_tab.READ_FORM})(page);\n'
                    'return {workspace:binding,tab_id:binding.id+":"+binding.page,page:data};')

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
        """核對工作區身分,再確認指定分頁屬於該工作區。"""
        w = self.workspace
        # 只讀整個工作區(page=False)不用記著哪一頁:工作區的頁都關了,拍照工具這時也要讀得到它
        if (not isinstance(w, dict) or not isinstance(w.get('id'), int) or not w.get('name')
                or (page and tab_id is None and not w.get('page'))):
            raise Unreachable(BEFORE_UPDATE)
        binding = dict(w)
        if tab_id is not None:
            space, separator, label = str(tab_id).partition(':')
            if not separator or space != str(w['id']) or not label:
                raise NotNow('回報的頁面不在這張卡記著的工作區')
            binding['page'] = label
        script = (
            f'const binding = {json.dumps(binding)};\n'
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

    def fill_fields(self, tab_id, items):
        """程式自己填這幾格(apply_tab.FILL_FN):[{backend, how, value}] → 每一格 {backend, ok, why}。"""
        import apply_tab
        return _ego(self._task(tab_id) + f'return await ({apply_tab.FILL_FN})(page, {json.dumps(items)});',
                    timeout=60 + 10 * len(items))

    def read_page(self, tab_id):
        import apply_tab
        return _ego(self._task(tab_id) + f'return await ({apply_tab.READ_FORM})(page);')

    def read_profile_page(self, tab_id):
        """讀 agent 正在編輯的同一頁，保留尚未按儲存的欄位值與實際連結。"""
        script = self._task(tab_id) + f'return await page.evaluate({PROFILE_FN});'
        return _ego(script)

    def read_workspace_pages(self):
        script = self._task(page=False) + (
            'const rows = [];\n'
            'for (const tab of await task.tabs()) {\n'
            'const row = {tab_id:`${binding.id}:${tab.label || ""}`, url:tab.url || "", title:tab.title || ""};\n'
            f'if (!tab.label) {{ row.error = row.screenshot_error = {json.dumps(NO_TAB_LABEL)}; rows.push(row); continue; }}\n'
            'try {\n'
            'const data = {...await task.page(tab.label).evaluate(' + PROFILE_FN + '), tab_id:row.tab_id};\n'
            'row.url = data.url || row.url; row.title = data.title || row.title;\n'
            'row.size = JSON.stringify(data).length;\n'
            f'if (row.size <= {PAGE_MATERIAL_CHAR_LIMIT}) row.data = data;\n'
            '} catch (error) { row.error = String(error.message || error); }\n'
            'rows.push(row);\n'
            '}\nreturn rows;\n'
        )
        try:
            tabs = _ego(script, timeout=60)
        except Exception as error:  # noqa: BLE001 — 工作區目錄讀不到也要留下原因並繼續
            return [{'tab_id': '', 'url': '', 'title': '', 'read_status': 'unavailable',
                     'read_error': f'工作區讀取失敗:{str(error)[:180]}'}]
        if not isinstance(tabs, list) or not all(isinstance(tab, dict) and tab.get('tab_id') for tab in tabs):
            return [{'tab_id': '', 'url': '', 'title': '', 'read_status': 'unavailable',
                     'read_error': '工作區讀頁回傳格式錯誤'}]
        return workspace_pages(tabs)

    def reload_profile_page(self, tab_id, url=None):
        """重新載入並丟棄未存檔編輯；重新開啟編輯處後才讀持久內容。"""
        return _ego(self._task(tab_id) +
                    f'const receipt = await page.goto({json.dumps(url) if url else "await page.url()"}, '
                    '{waitUntil:"domcontentloaded",timeout:30000});\n'
                    'if (receipt?.dialog || (await page.info())?.dialog) await page.acceptDialog();\n'
                    'return {url: await page.url()};')

    def shot(self, tab_id, out):
        import evidence
        out = os.path.abspath(out)
        with contextlib.suppress(FileNotFoundError):
            os.remove(out)
        record = evidence.active()
        started, ok, reason = time.monotonic(), False, ''
        try:
            _ego(self._task(tab_id) + f'return await page.screenshot({{path:{json.dumps(out)},fullPage:true}});',
                 timeout=self.SHOT_TIMEOUT)
            with open(out, 'rb') as f:
                if f.read(8) != b'\x89PNG\r\n\x1a\n':
                    raise NotNow('ego 沒有傳回有效頁面截圖')
            ok = True
            return out
        except Exception as error:
            reason = str(error)
            raise
        finally:
            if not ok:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(out)
            if record:
                record.note('shot_attempt', workspace=dict(self.workspace or {}), attempt=1,
                            limit=1, retry_count=0, ok=ok, reason=reason,
                            seconds=round(time.monotonic() - started, 3),
                            elapsed_seconds=round(time.monotonic() - started, 3))

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

    def read_pages(self, urls, board=None):
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
            f'pages[url]=await reader.evaluate({PROFILE_FN}); }}\n'
        )
        script += 'return {pages,reader:reader.label};\n'
        if temporary:
            script += '} finally {await task.finish({keep:[]});}\n'
        result = _ego(script, timeout=max(60, len(urls) * 60))
        if not temporary:
            self.workspace['reader'] = result['reader']
        return result['pages']

    def read_profile(self, read_url, board=None):
        return self.read_pages([read_url], board)[read_url]

    def release(self, tab_id):
        # 收尾只發生在這張卡送出、移除、頁面不見之後:他接手過也收(不然工作區永遠留著)
        receipt = _ego(self._task(tab_id, page=False, take_back=True) + 'return await task.finish({keep:[]});')
        self.workspace = None
        return receipt

    def human_action(self, tab_id, quote, need='', page=None):
        workspace_id, separator, label = str(tab_id or '').partition(':')
        workspace = self.workspace
        if not separator or workspace_id != str((workspace or {}).get('id')) or not label:
            raise NotNow('agent 回報的分頁不在這張工作區裡')
        previous_page = workspace['page']
        try:
            if label != previous_page:
                self.follow_page(tab_id)
            page = page if page is not None else self.read_profile_page(tab_id)
            cited = str(quote or '')
            if not cited.strip() or cited not in str((page or {}).get('text') or ''):
                raise NotNow('agent 引段未在程式讀到的同一分頁核對通過')
            return {'page': label, 'url': str((page or {}).get('url') or ''),
                    'quote': cited, 'need': str(need or '')}
        finally:
            workspace['page'] = previous_page

    def hand_off(self):
        """使用者在卡上按 👀 時把那一頁交給他:ego 裡切到那一頁,再把 ego 叫到他面前。"""
        w = self.workspace or {}
        if not (w.get('handoff_page') or w.get('page')):
            raise NotNow('程式不知道是哪一頁:agent 沒寫填表的是哪個分頁,或那個分頁不在最後一次拍改後裡')
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
