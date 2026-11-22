#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_tab —— 程式自己看 agent 留著的那一頁(沒有 AI):讀頁面用的函式、讀回來之後的比對,和指令列的 read / shot。

真的去讀、去截由這張卡的門路做(chrome_door.for_card:那一頁所在的 ego 工作區);這裡放兩邊共用的東西:
  READ_FORM             讀申請表:照瀏覽器的無障礙樹讀每一格的題目、值、勾選(在 ego 的 Node 端跑)
  PROFILE_FN            在頁面裡跑的唯讀函式(平台履歷的文字、連結、媒體)

用法:
  uv run python tools/apply_tab.py read --url 職缺網址 [--board B]
  uv run python tools/apply_tab.py shot --url 職缺網址 --out 檔案.png [--board B]
"""
import os, sys, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_cells                     # noqa: E402


# 讀申請表:照瀏覽器替讀屏軟體整理好的無障礙樹(Chrome 的 Accessibility.getFullAXTree)讀,不自己猜網頁結構。
# 每一格的角色、名稱、目前的值、勾選與必填都是瀏覽器算好的;藏起來的不在樹上。網站沒把題目綁在格子上時,
# 題目就照讀屏軟體的閱讀順序:這一格前面、上一格之後的那幾行字(context)。同網域的 iframe 一起讀。
# 在 ego 的 Node 端跑(拿 page),回傳的形狀給表單規劃使用。
READ_FORM = r"""async (page) => {
  const FIELD = new Set(['textbox','searchbox','combobox','listbox','checkbox','radio','switch','spinbutton','slider','menulistpopup']);
  const MAX_LINES = 1500;
  const val = x => (x && x.value !== undefined) ? x.value : undefined;
  const props = n => Object.fromEntries((n.properties || []).map(p => [p.name, val(p.value)]));
  const trees = [];
  let frames = [];
  try {
    const walk = f => { frames.push(f.frame.id); (f.childFrames || []).forEach(walk); };
    walk((await page.cdp('Page.getFrameTree', {})).frameTree);
  } catch (e) { frames = [undefined]; }
  for (const frameId of frames) {
    try { trees.push((await page.cdp('Accessibility.getFullAXTree', frameId ? {frameId} : {})).nodes || []); }
    catch (e) { /* 跨網域的 iframe 這一頁讀不到:跟讀屏軟體在這一頁一樣,略過 */ }
  }
  const fields = [], lines = [], uploads = [];
  let pending = [];                    // 上一格之後、這一格之前讀到的字(閱讀順序)
  let group = {key: null, context: ''};
  for (const nodes of trees) {
    const byId = new Map(nodes.map(n => [n.nodeId, n]));
    const roots = nodes.filter(n => !n.parentId || !byId.has(n.parentId));
    const order = [];
    const live = n => ['polite', 'assertive'].includes(props(n).live);
    // 富文字編輯區塊(contenteditable 沒標角色):瀏覽器標成可編輯(editable),最外層那一個算一格文字框
    const editRoot = n => !!props(n).editable && !(n.parentId && byId.get(n.parentId) && props(byId.get(n.parentId)).editable)
      && !FIELD.has(val(n.role));
    // 題目組(fieldset 的 legend、radiogroup):瀏覽器算好的這一組名稱,由近到遠
    const visit = (n, anc, grp) => {
      if (!n) return;
      order.push([n, anc, grp]);
      const role = val(n.role);
      // 讀屏軟體的播報區(aria-live,例如「option X, selected.」)不是頁面上的字
      const next = n.ignored ? anc : anc.concat([live(n) ? 'live' : editRoot(n) ? 'textbox' : role]);
      const name = String(val(n.name) || '');
      const g = !n.ignored && ['group', 'radiogroup'].includes(role) && name ? [[n.nodeId, name]].concat(grp) : grp;
      for (const c of n.childIds || []) visit(byId.get(c), next, g);
    };
    // 自製下拉選單:瀏覽器給這一格的值常是空的,選好的字顯示在同一個框裡。往上找最近一層有字的框,
    // 那一層裡只有這一格才算(碰到別的格子就停),播報區和題目本身不算
    const texts = (n, out) => {
      if (!n) return out;
      const role = val(n.role);
      if (role !== 'StaticText' && !n.ignored && (FIELD.has(role) || live(n))) return out;
      if (role === 'StaticText') out.push(String(val(n.name) || ''));
      for (const c of n.childIds || []) texts(byId.get(c), out);
      return out;
    };
    const holds = (n, self) => n && (n !== self && FIELD.has(val(n.role)) && !n.ignored || (n.childIds || []).some(c => holds(byId.get(c), self)));
    const selection = (n, label) => {
      for (let a = byId.get(n.parentId), i = 0; a && i < 6; a = byId.get(a.parentId), i++) {
        if (holds(a, n)) return '';
        const t = texts(a, []).join('');
        if (t && t !== label) return t;
      }
      return '';
    };
    roots.forEach(r => visit(r, [], []));
    for (const [n, anc, grp] of order) {
      if (n.ignored) continue;
      const rich = editRoot(n);
      const role = rich ? 'textbox' : val(n.role), name = String(val(n.name) || ''), p = props(n);
      if (role === 'StaticText' || role === 'staticText') {
        // 格子裡面的字是那一格的值,不是下一題的題目;其他的字照讀到的樣子算一行(ADR-0011,不替網站去掉記號)
        if (anc.some(r => FIELD.has(r) || r === 'live')) continue;
        if (name && lines[lines.length - 1] !== name) { lines.push(name); pending.push(name); }
        continue;
      }
      let dom = {};
      if (FIELD.has(role) || role === 'button') {
        try {
          const d = (await page.cdp('DOM.describeNode', {backendNodeId: n.backendDOMNodeId})).node || {};
          const a = d.attributes || [];
          for (let i = 0; i + 1 < a.length; i += 2) dom[a[i]] = a[i + 1];
          dom.tag = String(d.nodeName || '').toLowerCase();
        } catch (e) { /* 拿不到 DOM 細節:照無障礙樹的就好 */ }
      }
      if (role === 'button' && dom.type === 'file') {
        // 檔名從 DOM 的 files 讀:瀏覽器畫在按鈕旁邊的字(「No file chosen」「2 files」)跟介面語言有關,不是檔名
        try {
          const {object} = await page.cdp('DOM.resolveNode', {backendNodeId: n.backendDOMNodeId});
          const files = ((await page.cdp('Runtime.callFunctionOn', {objectId: object.objectId, returnByValue: true,
            functionDeclaration: 'function(){return [...(this.files || [])].map(f => f.name);}'})).result || {}).value || [];
          if (files.length) uploads.push({label: name, files});
        } catch (e) {
          uploads.push({label: name, files: [], read_error: '程式讀不到這一格選了哪些檔'});
        }
        continue;
      }
      if (!FIELD.has(role)) continue;
      if (dom.type === 'password' || dom.type === 'hidden') continue;
      const checked = p.checked === true || p.checked === 'true' || p.checked === 'mixed';
      const v = rich ? texts(n, []).join('')
        : String(val(n.value) ?? '') || (['combobox', 'listbox'].includes(role) ? selection(n, name) : '');
      const choice = ['radio', 'checkbox', 'switch', 'listbox', 'combobox', 'menulistpopup'].includes(role);
      const value = role === 'radio' ? (checked ? (dom.value || name) : '') : ['checkbox', 'switch'].includes(role) ? (checked ? (dom.value || 'on') : '') : v;
      const shown = role === 'radio' || ['checkbox', 'switch'].includes(role) ? (checked ? name : undefined) : (role === 'combobox' || role === 'listbox' ? v : undefined);
      const type = role === 'radio' ? 'radio' : ['checkbox', 'switch'].includes(role) ? 'checkbox'
        : ['combobox', 'listbox', 'menulistpopup'].includes(role) ? (dom.tag === 'select' ? 'select-one' : 'combobox')
        : (rich || dom.tag === 'textarea' || p.multiline) ? 'textarea' : (dom.type || 'text');
      // 同一組選項(radio、checkbox 同一個 name)是同一題:題目是這一組第一個選項前面的字,組裡每個選項都帶著它;
      // 選項之間的字是選項自己的標籤,不算題目
      const key = (role === 'radio' || role === 'checkbox') && dom.name ? role + ':' + dom.name : null;
      // 這一格前面的字照讀到的記下(不截短);前面沒有字就是沒有,不沿用上一格的(ADR-0011)
      if (!key || key !== group.key) group = {key, context: pending.join('\n')};
      fields.push({label: name, name: dom.name || dom.id || '', type, value, shown,
                   context: group.context || undefined, choiceControl: choice, required: p.required === true,
                   disabled: p.disabled === true || undefined,
                   backend: n.backendDOMNodeId, _key: key || fields.length, _grp: grp});   // backend:程式自己填這一格用
      pending = [];
    }
  }
  // 選項類的題目組名稱:只收只裝這一題(同一組 radio/勾選)的那一層;外層大區塊(「個人資料」)裝好幾題,不是題目
  const inGroup = new Map();
  for (const f of fields) for (const [id] of f._grp) (inGroup.get(id) || inGroup.set(id, new Set()).get(id)).add(f._key);
  for (const f of fields) {
    const hit = ['radio', 'checkbox'].includes(f.type) && f._grp.find(([id]) => inGroup.get(id).size === 1);
    if (hit) f.group = hit[1];
    delete f._key; delete f._grp;
  }
  const read = {url: await page.url(), title: await page.title(), fields, uploads, lines};
  if (lines.length > MAX_LINES) {
    // 太長不截短冒充完整:照實標程式沒比到(#591 ID「拍改前、拍改後工具」)
    return {...read, lines: [], fields: [], read_status: 'too_large',
            read_error: `頁面超過 ${MAX_LINES} 行,程式沒比到`};
  }
  return read;
}"""


# 程式自己填格子(form_plan --fill / --set):每一格照讀頁時記下的 backend 找回同一個元素,照種類用一種填法,
# 都是真的滑鼠、鍵盤事件(網站的程式看到的跟人操作一樣)。跑在 ego 的 Node 端:收 page 和 [{backend, how, value}]。
#   text  點那一格、全選、insertText(取代原本的字)
#   combo 點那一格、全選、一個字一個字打(才會跳建議清單),等看得到的 role=option 裡字一字不差的那一項點下去;
#         有建議清單卻沒有一字不差的那一項就回填不上,交給 agent(ADR-0011);沒有建議清單的就留著打好的字
#   select 原生下拉:選項的字一字不差才選,送 input、change;沒有就回填不上,交給 agent
#   click  選項類(radio、勾選):點那一項
FILL_FN = r"""async (page, items) => {
  const out = [];
  for (const it of items) {
    try {
      const {object} = await page.cdp('DOM.resolveNode', {backendNodeId: it.backend});
      const call = async (fn, ...args) => ((await page.cdp('Runtime.callFunctionOn', {objectId: object.objectId,
        functionDeclaration: fn, arguments: args.map(value => ({value})), awaitPromise: true, returnByValue: true})).result || {}).value;
      const box = await call(`function(){this.scrollIntoView({block:'center'}); const r=this.getBoundingClientRect();
        return r.width && r.height ? {x: r.x + r.width / 2, y: r.y + r.height / 2} : null;}`);
      if (it.how === 'click') {
        if (box) await page.mouse.click(box.x, box.y); else await call('function(){this.click();}');
      } else if (it.how === 'select') {
        const ok = await call(`function(want){const o=[...this.options].find(o=>o.text===want);
          if(!o) return false; Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(this,o.value);
          this.dispatchEvent(new Event('input',{bubbles:true})); this.dispatchEvent(new Event('change',{bubbles:true})); return true;}`, it.value);
        if (!ok) throw new Error('選單裡沒有一字不差的這一項');
      } else {
        if (box) await page.mouse.click(box.x, box.y);
        await call(`function(){this.focus(); if (typeof this.select === 'function') this.select();
          else {const r=document.createRange(); r.selectNodeContents(this); const s=getSelection(); s.removeAllRanges(); s.addRange(r);}}`);
        if (it.how === 'combo') {
          await page.keyboard.type(it.value);
          let pos = null;
          for (let i = 0; i < 15 && !(pos && pos.x !== undefined); i++) {
            await page.waitForTimeout(200);
            pos = await page.evaluate(want => {
              const os = [...document.querySelectorAll('[role=option]')].filter(o => o.getClientRects().length);
              const o = os.find(o => o.innerText === want);
              if (!o) return {shown: os.length};
              o.scrollIntoView({block: 'nearest'}); const r = o.getBoundingClientRect();
              return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }, it.value);
          }
          if (pos && pos.x !== undefined) await page.mouse.click(pos.x, pos.y);
          else if (pos && pos.shown) throw new Error('建議清單裡沒有一字不差的這一項');
        } else {
          await page.keyboard.insertText(it.value);
        }
      }
      out.push({backend: it.backend, ok: true});
    } catch (e) {
      out.push({backend: it.backend, ok: false, why: String(e && e.message || e).slice(0, 200)});
    }
  }
  return out;
}"""




def _lookup(url, board=None):
    """看板上這張卡記的分頁,和開那一頁的那一家的門路(chrome_door.for_card:沒記到、那一家不能用了丟 Unreachable)。"""
    import board_doc as bd
    fb = json.loads(bd.load(board)['fb'])
    a = (fb.get(url) or {}).get('apply') or {}
    if a.get('workspace') and not a.get('tab_id'):
        raise LookupError('程式不知道是哪一頁:agent 沒寫填表的是哪個分頁,或那個分頁不在最後一次拍改後裡')
    if not (a.get('session') or a.get('workspace')) or not a.get('tab_id'):
        raise LookupError('看板上沒有記這張是哪一段對話、哪個分頁')
    import chrome_door
    return a['tab_id'], chrome_door.for_card(a)


# 表單題目與頁面欄位的關聯只定義一次，配答案與記錄觀察共用。
Problem = gate_cells.Problem
CHOICE = ('radio', 'checkbox')


def group_key(field, i):
    """同一組選項(radio、勾選同一個 name)是同一題;其他每一格自己一題。"""
    return (field.get('type'), field.get('name')) if field.get('type') in CHOICE and field.get('name') else i


def questions(fields):
    """每一格的題目(跟 fields 一一對應):瀏覽器替讀屏軟體算好的名稱,字照讀到的樣子。不是選項類的看格子自己的名稱;
    選項類(radio、勾選,名稱是選項本身)看這一組的名稱(fieldset 的 legend、radiogroup)。瀏覽器沒給名稱的格子是空字串:
    前面哪一行是它的題目由 agent 看頁面判斷,程式不照閱讀順序猜(ADR-0011)。"""
    return [str(f.get('group') or '') if f.get('type') in CHOICE else str(f.get('label') or '') for f in fields]


def _stop_on_term():
    """看板的 👀 時間到會先送 SIGTERM:轉成一般的結束,正在跑的 ego-browser 子程序跟著收掉(subprocess.run 碰到例外會殺子程序)。"""
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
        import contextlib
        import evidence
        # 👀 看現在的頁面每幾秒截一次,只是給他看、不是證據:記成一輪的話,看幾分鐘就把每張卡只留 30 輪的額度擠滿,
        # 填表那一輪(題目附的截圖)被刪掉(#447)
        with (contextlib.nullcontext() if a.cmd == 'shot' else evidence.opened('apply_check', a.cmd, [a.url], a.board)):
            tid, door = _lookup(a.url, a.board)
            if a.cmd == 'read':
                from web import seen
                from web.outside import Real
                print(json.dumps(seen.form(Real(door), tid), ensure_ascii=False, indent=1))
            else:
                door.shot(tid, a.out or 'tab.png')
                print(a.out or 'tab.png')
    except Exception as e:  # noqa: BLE001 — 指令列最外層:原因照實印出、結束碼 2(看板的 👀 照結束碼講)
        print(f'看不到那一頁:{e}', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
