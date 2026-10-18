#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_tab —— 程式自己看 agent 留著的那一頁(沒有 AI):讀頁面用的函式、讀回來之後的比對,和指令列的 read / shot。

真的去讀、去截由這張卡的門路做(chrome_door.for_card:那一頁所在的 ego 工作區);這裡放兩邊共用的東西:
  READ_FORM             讀申請表:照瀏覽器的無障礙樹讀每一格的題目、值、勾選(在 ego 的 Node 端跑)
  PROFILE_FN            在頁面裡跑的唯讀函式(平台履歷的文字、連結、媒體)
  page_problems         讀回的申請表跟答案、上傳檔對不上的地方
  human_check           頁面要本人登入或驗證

用法:
  uv run python tools/apply_tab.py read --url 職缺網址 [--board B]
  uv run python tools/apply_tab.py shot --url 職缺網址 --out 檔案.png [--board B]
"""
import os, re, sys, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from profile_sync import ID_PARAMS    # 平台履歷的編號放在哪個網址參數(讀頁時留下連到那一份的連結)
import gate_cells                     # noqa: E402


# 讀申請表:照瀏覽器替讀屏軟體整理好的無障礙樹(Chrome 的 Accessibility.getFullAXTree)讀,不自己猜網頁結構。
# 每一格的角色、名稱、目前的值、勾選與必填都是瀏覽器算好的;藏起來的不在樹上。網站沒把題目綁在格子上時,
# 題目就照讀屏軟體的閱讀順序:這一格前面、上一格之後的那幾行字(context)。同網域的 iframe 一起讀。
# 在 ego 的 Node 端跑(拿 page),回傳的形狀給 page_problems / gate_apply 用。
READ_FORM = r"""async (page) => {
  const FIELD = new Set(['textbox','searchbox','combobox','listbox','checkbox','radio','switch','spinbutton','slider','menulistpopup']);
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
  const fields = [], lines = [], submits = [], fileInputs = [];
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
      const name = String(val(n.name) || '').trim();
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
        const t = texts(a, []).join('').replace(/\s+/g, ' ').trim();
        if (t && t !== label) return t;
      }
      return '';
    };
    roots.forEach(r => visit(r, [], []));
    for (const [n, anc, grp] of order) {
      if (n.ignored) continue;
      const rich = editRoot(n);
      const role = rich ? 'textbox' : val(n.role), name = String(val(n.name) || '').trim(), p = props(n);
      if (role === 'StaticText' || role === 'staticText') {
        // 格子裡面的字是那一格的值,不是下一題的題目;只有符號的(必填記號 ✱、*)不算一行
        if (anc.some(r => FIELD.has(r) || r === 'live')) continue;
        const t = name.replace(/[\ue000-\uf8ff]/g, '').replace(/[×✕]\s*$/, '').trim();
        if (t && /[\p{L}\p{N}]/u.test(t) && lines[lines.length - 1] !== t) { lines.push(t); pending.push(t); }
        continue;
      }
      if (role === 'button' && !p.disabled && anc.some(a => ['form', 'dialog', 'alertdialog'].includes(a)) && name) submits.push(name);
      let dom = {};
      if (FIELD.has(role) || role === 'button') {
        try {
          const d = (await page.cdp('DOM.describeNode', {backendNodeId: n.backendDOMNodeId})).node || {};
          const a = d.attributes || [];
          for (let i = 0; i + 1 < a.length; i += 2) dom[a[i]] = a[i + 1];
          dom.tag = String(d.nodeName || '').toLowerCase();
        } catch (e) { /* 拿不到 DOM 細節:照無障礙樹的就好 */ }
      }
      if (role === 'button' && dom.tag === 'input' && dom.type === 'file') { fileInputs.push({backend: n.backendDOMNodeId, label: name}); continue; }
      if (!FIELD.has(role) || p.disabled) continue;
      if (dom.type === 'password' || dom.type === 'hidden') continue;
      const checked = p.checked === true || p.checked === 'true' || p.checked === 'mixed';
      const v = rich ? texts(n, []).join('').trim()
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
      // 沒名稱、前面也沒有字的格子是上一題的第二格(國碼選單後面的電話號碼):沿用上一題
      if (!key || key !== group.key) group = {key, context: pending.join('\n').slice(-2000) || (name ? '' : group.context)};
      fields.push({label: name.slice(0, 200), name: dom.name || dom.id || '', type, value, shown,
                   context: group.context || undefined, choiceControl: choice, required: p.required === true,
                   backend: n.backendDOMNodeId, _key: key || fields.length, _grp: grp});   // backend:程式自己填這一格用
      pending = [];
    }
  }
  // 選項類的題目組名稱:只收只裝這一題(同一組 radio/勾選)的那一層;外層大區塊(「個人資料」)裝好幾題,不是題目
  const inGroup = new Map();
  for (const f of fields) for (const [id] of f._grp) (inGroup.get(id) || inGroup.set(id, new Set()).get(id)).add(f._key);
  for (const f of fields) {
    const hit = ['radio', 'checkbox'].includes(f.type) && f._grp.find(([id]) => inGroup.get(id).size === 1);
    if (hit) f.group = hit[1].slice(0, 200);
    delete f._key; delete f._grp;
  }
  // 上傳欄:檔名照瀏覽器手上的檔(input.files),藏起來的上傳欄(常見的「附加檔案」按鈕後面那一個)也算;
  // 它所在的那一塊沒畫出來(整張表單還在登入後面)就不算這一頁的格子
  const files = await page.evaluate(() => [...document.querySelectorAll('input[type=file]')]
    .filter(el => el.parentElement && el.parentElement.getClientRects().length).map(el => ({
    name: el.name || el.id || '', files: el.files ? [...el.files].map(f => f.name) : (el.value ? [el.value.split(/[\\/]/).pop()] : [])})));
  files.forEach((f, i) => fields.push({label: (fileInputs[i] || {}).label || '', name: f.name, type: 'file', value: f.files}));
  const links = await page.evaluate(() => [...document.links].map(a => a.href)).catch(() => []);
  const profileLinks = [...new Set(links.filter(h => { try { const q = new URL(h).searchParams; return ID_PARAMS.some(k => q.has(k)); } catch (e) { return false; } }))].slice(0, 20);
  return {url: await page.url(), title: await page.title(), fields, lines: lines.filter(s => s.length <= 120).slice(0, 1500),
          shownFiles: lines.filter(s => /\.(pdf|docx?|rtf|odt|txt)$/i.test(s)).slice(0, 30),
          profileLinks, submits: submits.slice(0, 10)};
}""".replace('ID_PARAMS', json.dumps(sorted(set(ID_PARAMS.values()))))


# 程式自己填格子(form_plan --fill / --set):每一格照讀頁時記下的 backend 找回同一個元素,照種類用一種填法,
# 都是真的滑鼠、鍵盤事件(網站的程式看到的跟人操作一樣)。跑在 ego 的 Node 端:收 page 和 [{backend, how, value}]。
#   text  點那一格、全選、insertText(取代原本的字)
#   combo 點那一格、全選、一個字一個字打(才會跳建議清單),等看得到的 role=option 裡字一樣(或開頭一樣)的那一項點下去;
#         沒有建議清單的就留著打好的字
#   select 原生下拉:照選項的字選,送 input、change
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
        const ok = await call(`function(want){const n=s=>String(s||'').replace(/\\s+/g,' ').trim().toLowerCase();
          const os=[...this.options], o=os.find(o=>n(o.text)===n(want))||os.find(o=>n(o.text)&&(n(want).includes(n(o.text))||n(o.text).includes(n(want))));
          if(!o) return false; Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(this,o.value);
          this.dispatchEvent(new Event('input',{bubbles:true})); this.dispatchEvent(new Event('change',{bubbles:true})); return true;}`, it.value);
        if (!ok) throw new Error('選單裡沒有這一項');
      } else {
        if (box) await page.mouse.click(box.x, box.y);
        await call(`function(){this.focus(); if (typeof this.select === 'function') this.select();
          else {const r=document.createRange(); r.selectNodeContents(this); const s=getSelection(); s.removeAllRanges(); s.addRange(r);}}`);
        if (it.how === 'combo') {
          await page.keyboard.type(it.value);
          let pos = null;
          for (let i = 0; i < 15 && !pos; i++) {
            await page.waitForTimeout(200);
            pos = await page.evaluate(want => {
              const n = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const os = [...document.querySelectorAll('[role=option]')].filter(o => o.getClientRects().length);
              const o = os.find(o => n(o.innerText) === n(want)) || os.find(o => n(o.innerText).startsWith(n(want)));
              if (!o) return null;
              o.scrollIntoView({block: 'nearest'}); const r = o.getBoundingClientRect();
              return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }, it.value);
          }
          if (pos) await page.mouse.click(pos.x, pos.y);
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


PROFILE_FN = """() => {
  const labelFor = el => {
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)
      .map(id => document.getElementById(id)?.innerText || '').join(' ');
    const labels = el.labels ? [...el.labels].map(x => x.innerText || '').join(' ') : '';
    return (el.getAttribute('aria-label') || labelled || labels || el.getAttribute('placeholder') || '').trim();
  };
  const sectionFor = el => {
    const group = el.closest('fieldset,section,[role=group],[role=radiogroup],[role=listbox]');
    const heading = group && group.querySelector('legend,[role=heading],h1,h2,h3,h4,h5');
    return group ? (labelFor(group) || (heading && heading.innerText) || '').trim() : '';
  };
  // 保留欄位上下文與選取狀態(勾選、aria),不猜哪些值是個人聲明;送出、按鈕類 input 不是資料
  const fields = [...document.querySelectorAll('input,textarea,select,[role=textbox],[role=combobox],[role=checkbox],[role=radio],[role=switch],[role=listbox],[role=option],[role=slider],[role=spinbutton],[contenteditable=true],[aria-selected],[aria-checked],[aria-pressed],[aria-valuenow],[aria-valuetext],[aria-activedescendant],[aria-expanded]')]
    .filter(el => !['hidden','password','file'].includes((el.type || '').toLowerCase()))
    .filter(el => !(el.tagName === 'INPUT' && ['submit','button','image','reset'].includes((el.type || '').toLowerCase())))
    .filter(el => {const s = getComputedStyle(el); return el.getClientRects().length && s.display !== 'none' && s.visibility !== 'hidden';})
    .map(el => ({id: el.id || '', label: labelFor(el), section: sectionFor(el), role: el.getAttribute('role') || '',
      type: (el.type || el.tagName.toLowerCase()).toLowerCase(), autocomplete: el.getAttribute('autocomplete') || '',
      text: el.innerText || '', parentText: el.parentElement?.innerText || '',
      value: (el.isContentEditable ? el.innerText : el.value) ?? '',
      shown: el.tagName === 'SELECT' ? [...el.selectedOptions].map(x => x.text).join(' ') : undefined,
      checked: ['checkbox','radio'].includes(el.type) ? el.checked : undefined,
      aria: Object.fromEntries(['aria-selected','aria-checked','aria-pressed','aria-expanded','aria-activedescendant',
        'aria-valuenow','aria-valuetext','aria-controls','aria-labelledby','aria-describedby'].map(a => [a, el.getAttribute(a)]))}));
  const emailQuery = new URL(location.href).searchParams;
  const emailThreadPrintView = emailQuery.get('view') === 'pt' &&
    emailQuery.get('search') === 'all' && emailQuery.has('th') &&
    Boolean(document.querySelector('.bodycontainer'));
  const emailMessages = emailThreadPrintView ? [...document.querySelectorAll('.bodycontainer .message')] : [];
  const emailBodies = emailMessages.map(el => (el.innerText || el.textContent || '').trim());
  // Gmail 搜尋結果的每一列不是連結:對話代號在列上的 data-legacy-thread-id(只算畫出來的那幾列,別的檢視藏著的不算)
  const emailThreads = /(^|\\.)mail\\.google\\.com$/.test(location.hostname) ? [...new Set([...document.querySelectorAll('tr [data-legacy-thread-id]')]
    .filter(el => el.closest('tr').getClientRects().length).map(el => el.getAttribute('data-legacy-thread-id')))] : [];
  // 收起來的內容(「更多」、摺疊區)常已在 DOM 裡只是沒畫出來;innerText 讀不到,判讀就說「無法確認」。
  // 把沒畫出來的文字接在後面(跳過程式碼類標籤),不靠各網站的按鈕名稱。
  const body = document.body || document.documentElement;
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const collapsed = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const p = n.parentElement, s = (n.nodeValue || '').trim();
    if (!s || !p || p.closest('script,style,noscript,template,svg') || p.getClientRects().length) continue;
    collapsed.push(s);
  }
  const readingParts = [], readingRects = [];
  const documentRect = node => {
    const boxes = node.nodeType === 3 ? (() => {
      const range = document.createRange(); range.selectNodeContents(node);
      return [...range.getClientRects()];
    })() : [node.getBoundingClientRect()];
    const visible = boxes.filter(r => r.width > 0 && r.height > 0);
    if (!visible.length) return null;
    const x = Math.min(...visible.map(r => r.left)) + scrollX;
    const y = Math.min(...visible.map(r => r.top)) + scrollY;
    return {x, y, width: Math.max(...visible.map(r => r.right)) + scrollX - x,
      height: Math.max(...visible.map(r => r.bottom)) + scrollY - y};
  };
  const addReading = (value, node) => {
    if (!String(value || '').trim()) return;
    readingParts.push(String(value).trim());
    readingRects.push(documentRect(node));
  };
  const readableUrl = value => { try { return decodeURIComponent(value); } catch (_error) { return value; } };
  const visit = node => {
    if (node.nodeType === 3) { addReading(node.nodeValue, node); return; }
    if (node.nodeType !== 1) return;
    const el = node;
    if (el.closest('script,style,noscript,template,svg') || !el.getClientRects().length) return;
    const tag = el.tagName;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) {
      if (['hidden', 'password', 'file', 'submit', 'button', 'image', 'reset'].includes((el.type || '').toLowerCase())) return;
      if (el.type === 'checkbox' || el.type === 'radio') addReading(`⟦欄位 ${el.checked ? '已勾選' : '未勾選'}⟧`, el);
      else addReading(tag === 'SELECT' ? [...el.selectedOptions].map(x => x.text).join(' ') : el.value, el);
      return;
    }
    for (const child of el.childNodes) visit(child);
    if (tag === 'A' && el.href) addReading(`⟦網址 ${readableUrl(el.href)}⟧`, el);
    if (['IFRAME', 'VIDEO', 'AUDIO', 'SOURCE', 'EMBED', 'OBJECT'].includes(tag)) {
      const url = el.src || el.data;
      if (url) addReading(`⟦網址 ${readableUrl(url)}⟧`, el);
    }
    if (['option', 'radio', 'checkbox'].includes(el.getAttribute('role')) && el.hasAttribute('aria-selected'))
      addReading(`⟦欄位 ${el.getAttribute('aria-selected') === 'true' ? '已選取' : '未選取'}⟧`, el);
  };
  visit(body);
  return {url: location.href, title: document.title, readyState: document.readyState, sinceLoad: performance.now(),
    documentKey: performance.timeOrigin,
    readingParts,
    readingRects,
    documentSize: {width: Math.max(document.documentElement?.scrollWidth || 0, body?.scrollWidth || 0),
      height: Math.max(document.documentElement?.scrollHeight || 0, body?.scrollHeight || 0)},
    reading: readingParts.join(' ').replace(/\\s+/g, ' ').trim(),
    text: (body.innerText || '') + (collapsed.length ? '\\n' + collapsed.join('\\n') : ''),
    emailThreadPrintView,
    emailThreads,
    emailMessageCount: emailMessages.length,
    emailBodies,
    links: [...document.links].map(a => a.href),
    media: [...document.querySelectorAll('iframe,video,audio,source,embed,object')].map(el => el.src || el.data).filter(Boolean),
    anchors: [...document.links].map(a => ({href: a.href, text: (a.innerText || '').trim()})), fields};
}"""


def _lookup(url, board=None):
    """看板上這張卡記的分頁,和開那一頁的那一家的門路(chrome_door.for_card:沒記到、那一家不能用了丟 Unreachable)。"""
    import board_doc as bd
    fb = json.loads(bd.load(board)['fb'])
    a = (fb.get(url) or {}).get('apply') or {}
    if not (a.get('session') or a.get('workspace')) or not a.get('tab_id'):
        raise LookupError('看板上沒有記這張是哪一段對話、哪個分頁')
    import chrome_door
    return a['tab_id'], chrome_door.for_card(a)


# 網站的真人驗證(Cloudflare 的「請稍候…」「驗證您是人類」這類):agent 不替他按,也不規避。
# 程式控制的瀏覽器常被擋、他在別的視窗過了也接不回來(驗證綁著瀏覽器身分),這種網站讓他自己投。
HUMAN_CHECK = gate_cells.Problem('這個網站要本人登入或驗證:按卡上的「👀」接手,處理完按「修改」接著填', 'human')
# 頁面上任何地方出現就是真人驗證(程式判斷要本人接手的 chrome_door.human_action 也用這一份)
VERIFY_WORDS = ('驗證您是人類', '正在執行安全驗證', 'verify you are human', 'performing security verification',
                'checking your browser')
# 標題和最前面幾行是「請稍候」也算(Cloudflare 驗證頁的標題);正文裡的「請稍候」不算
HUMAN_CHECK_WORDS = VERIFY_WORDS + ('請稍候', 'just a moment')


def human_check(page):
    """這一頁是不是停在網站的真人驗證(看標題和頁面上前幾行字)。"""
    text = ' '.join([str(page.get('title') or '')] + [str(x) for x in (page.get('lines') or [])[:12]]).lower()
    return any(w in text for w in HUMAN_CHECK_WORDS)


# 題目後面常跟著必填記號和字數計數器(「…？*(18/1500)」、「0/500」),不是題目的一部分
_TAIL = re.compile(r'[\s*＊✱✳]*(?:[(（]?\s*\d+\s*/\s*\d+\s*[)）]?)?[\s*＊✱✳]*$')


# 填表的「題目是哪一格」和「值算不算對上」只在這裡定義一次:配答案(form_plan)、記交件單(form_record)、
# 驗收(page_problems)、安檢門(gate_apply)都呼叫這幾支,不各寫一套。
norm = gate_cells.norm                 # 全形半形、大小寫、空白換行都不算差別
Problem = gate_cells.Problem
_CONTACT = re.compile(r'@|^\+?[\d\s()-]{7,}$')   # 長得像 email 或電話的答案
BILINGUAL = re.compile(r'\s*[/／]\s*')                        # 雙語答案「公司徵才頁 / Company career page」
_PARTS = re.compile(r'\s*[/／|｜;；()（）]\s*')
CHOICE = ('radio', 'checkbox')


def segments(text, sep=_PARTS):
    """答案原文切成一段一段(斜線、直線、分號、括號分開):雙語答案、「United Kingdom (+44)」這類。換行不切:分段的長答案少一段不算對上。"""
    return [norm(p) for p in sep.split(str(text or '')) if norm(p)]


def matches(want, got, kind='text'):
    """頁面上的值 got 算不算答案 want。一樣(空白換行不算差別)一律算;其他看是哪種格子:
      text    文字格:雙語答案的其中一種也算(form_plan 照表單語言只填一種)
      option  選項(radio、勾選、下拉選中的那一項):答案原文裡完整的一段也算(「公司徵才頁 / Company career page」選了英文那段)
      box     選單框裡顯示的字:框常只顯示選項的一段(選「Japan +81」只顯示「+81」),整段出現在答案裡也算
    email、電話只准一樣:電話號碼裡本來就有國碼,放寬會讓讀不到的電話冒充對上。"""
    w, g = norm(want), norm(got)
    if not w or not g:
        return False
    if w.replace(' ', '') == g.replace(' ', ''):
        return True
    if _CONTACT.search(w):
        return False
    if kind == 'text':
        # 只限一中一英兩段的雙語答案(英文表單填英文那段);多段、同一種語言分段的答案只填一段不算
        import form_record as fr
        parts = segments(want, BILINGUAL)
        return len(parts) == 2 and sum(bool(re.search(fr._CJK, p)) for p in parts) == 1 and g in parts
    return g in segments(want) or (kind == 'box' and len(g) >= 3
                                   and bool(re.search(r'(^|\W)' + re.escape(g) + r'($|\W)', w)))


def qkey(text):
    """題目的比對用寫法:去掉題目後面的必填記號和字數計數器,標點空白不算差別。"""
    import form_record as fr
    return fr._nq(_TAIL.sub('', str(text or '')))


def kind_of(field, shown=False):
    """這一格的值照哪種比(matches 的 kind):radio、勾選是整個選項;下拉選單框裡顯示的字(shown)可能只是選項的一段。"""
    if not field.get('choiceControl') and field.get('type') not in CHOICE:
        return 'text'
    return 'box' if shown and field.get('type') not in CHOICE else 'option'


def group_key(field, i):
    """同一組選項(radio、勾選同一個 name)是同一題;其他每一格自己一題。"""
    return (field.get('type'), field.get('name')) if field.get('type') in CHOICE and field.get('name') else i


def _candidates(fields):
    """每一格可能是哪一題,近的在前。不是選項類的:格子自己的名稱;選項類(radio、勾選,名稱是選項本身):
    瀏覽器給這一組的名稱(fieldset 的 legend、radiogroup)。都沒有才往前看閱讀順序上的字:這一組自己的選項字、
    別格的題目(格子名稱、題目組名稱)不算。"""
    options, claimed = {}, set()
    for i, f in enumerate(fields):
        options.setdefault(group_key(f, i), set()).add(qkey(f.get('label')))
        if f.get('label') and f.get('type') not in CHOICE or f.get('group'):
            claimed.add(qkey(f.get('group') if f.get('type') in CHOICE else f.get('label')))
    out = []
    for i, f in enumerate(fields):
        label = str(f.get('label') or '').strip()
        if label and f.get('type') not in CHOICE:
            out.append([_TAIL.sub('', label)])
        elif f.get('group'):
            out.append([_TAIL.sub('', str(f['group']))])
        else:
            skip = options[group_key(f, i)] | claimed
            out.append([_TAIL.sub('', x) for x in reversed(str(f.get('context') or '').splitlines())
                        if qkey(x) and qkey(x) not in skip])
    return out


def questions(fields):
    """配答案用:每一格的題目就是最近的那一個(跟 fields 一一對應)。不往前多看:再前面的字常是上一題,配錯比配不到糟。"""
    return [c[0] if c else '' for c in _candidates(fields)]


def bound(fields, q, recorded=()):
    """驗收用:頁面上屬於題目 q 的格子。最近的就是 q,或往前看到 q 之前沒碰到表單記的別題(recorded,qkey)。
    中間夾著的不是題目的字(自製選單顯示的值、說明)不擋;配答案配到的那一格一定在裡面。"""
    q = qkey(q)
    out = []
    for f, cands in zip(fields, _candidates(fields)):
        for x in cands:
            k = qkey(x)
            if k == q:
                out.append(f)
            if k == q or k in recorded:
                break
    return out if q else []


def page_problems(page, fb, url, uploaded=(), tab_url=None):
    """那一頁現在的值跟答案庫對不對得上。回問題清單(空的就是對上了)。
    先看它還是不是填好的那一頁:網址變了、欄位不見了,就是換頁了或被送出了。
    表單每一題(答案庫的答案、履歷直接對上的短答案)找頁面上同一題的格子(question),值要 matches 答案;上傳的檔要真的選在上傳欄。"""
    if human_check(page):
        return [HUMAN_CHECK]
    if tab_url and not gate_cells.same_url(page.get('url'), tab_url):
        return [Problem(f'那一頁已經不是填好的申請表了(現在是 {str(page.get("url"))[:80]}),可能被送出了,要人看', 'gone')]
    if not page.get('fields') and not page.get('submits'):
        return [Problem('那一頁上沒有任何欄位(可能被送出了、或換頁了),要人看', 'gone')]
    import form_record as fr
    fields = [f for f in page.get('fields') or [] if isinstance(f, dict) and not isinstance(f.get('value'), list)]
    lines = [str(x) for x in page.get('lines') or []]
    form = ((fb.get(url) or {}).get('form') or {}).get('f', [])
    recorded = {qkey(x.get('q')) for x in form}
    bank = {e.get('k'): e for e in fb.get('__ans__', [])}
    bad = []
    for x in form:
        if x.get('src') == 'bank':
            e = bank.get(x.get('k')) or {}
            want = [w for w in (e.get('v'), e.get('zh')) if norm(w)]
        elif x.get('src') == 'rz' and '\n' not in str(x.get('v') or '') and '.pdf' not in str(x.get('v') or '').lower():
            e, want = None, [w for w in (x.get('v'),) if norm(w)]
        else:
            continue
        q = qkey(x.get('q'))
        scoped = bound(fields, x.get('q'), recorded)
        got = [x for f in scoped for x in ((f.get('value'), kind_of(f)), (f.get('shown'), kind_of(f, shown=True)))]
        at = [i for i, line in enumerate(lines[:-1]) if qkey(line) == q]
        if not scoped and not at:
            continue    # 頁面上連這一題都沒有:分好幾步的表單,這一題在前一步,讀不到不等於填錯
        if not any(f.get('choiceControl') or f.get('type') in CHOICE for f in scoped):
            # 不是表單欄位的選單(選好的值只是一行字,例如「選擇履歷」下一行就是選好的那一份):題目下一行就是它的值。
            # 這一題有選項類的格子就不看:下一行可能只是一個沒被選的選項
            got += [(lines[i + 1], 'option') for i in at]
        if any(matches(w, v, kind) for w in want for v, kind in got):
            continue
        choice = x.get('choice')
        # agent 選了跟答案同義的另一句(選項裡沒有答案原文):要交理由,選中的就是它說的那一項,答案還是它當時看的那一版
        if (isinstance(choice, dict) and e and str(choice.get('why') or '').strip()
                and not (fr.empty_answer(e) or e.get('inf') or e.get('tr') or e.get('redo'))
                and choice.get('source_hash') == fr.answer_fingerprint(e)
                and not any(_CONTACT.search(norm(w)) for w in want)
                and len([f for f in scoped if f.get('choiceControl')
                         and any(matches(choice.get('value'), f.get(kk), kind_of(f, kk == 'shown')) for kk in ('value', 'shown'))]) == 1):
            continue
        if isinstance(choice, dict):
            bad.append(Problem(f'「{str(x.get("q"))[:40]}」的選項對應缺有效来源、理由或同一題的原生選取證據', 'choice'))
        else:
            bad.append(Problem(f'頁面上找不到「{str(x.get("q"))[:40]}」的答案 {norm(want[0])[:40]!r}' if want
                               else f'頁面上找不到「{str(x.get("q"))[:40]}」的答案', 'answer'))
    return bad + upload_problems(page, uploaded)


# 讀頁核對對不上、agent 在原頁就改得了的那幾種:答案沒打對、選項沒對上、檔沒選上(page_problems / upload_problems 寫的)
FIXABLE = {'answer', 'choice', 'upload'}


def correctable(problem):
    """agent 在原頁就改得了的:答案沒打對、選項沒對上、檔沒選上。看問題的種類,不看字。"""
    return gate_cells.kind(problem) in FIXABLE


def upload_problems(page, uploaded=()):
    """說上傳了的檔是不是真的選在上傳欄(或上傳完顯示在旁邊的檔名)。回問題清單。"""
    files, flabels = set(), []
    for f in page.get('fields') or []:
        if isinstance(f.get('value'), list):
            files.update(norm(x) for x in f['value'])
            flabels.append(norm(f.get('label')))   # 有的上傳欄傳完會清空,檔名改顯示在旁邊的標籤
    shown_files = {norm(x) for x in page.get('shownFiles') or []}
    bad = []
    for n in uploaded or ():
        stem = norm(os.path.splitext(n)[0])[:18]
        if norm(n) not in files | shown_files and not any(stem and stem in l for l in flabels):
            bad.append(Problem(f'上傳欄裡沒有 {n}', 'upload'))
    return bad


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
                print(json.dumps(door.read_page(tid), ensure_ascii=False, indent=1))
            else:
                door.shot(tid, a.out or 'tab.png')
                print(a.out or 'tab.png')
    except Exception as e:  # noqa: BLE001 — 指令列最外層:原因照實印出、結束碼 2(看板的 👀 照結束碼講)
        print(f'看不到那一頁:{e}', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()


# 送出的履歷在申請頁上的那一塊(他人在外面也看得到的證據):找頁面上寫著這個檔名的那一格,回文件座標的方框。
# 檔名常被網站用 CSS 截短顯示(Lever),DOM 裡還是全名,所以照全名找;同網域 iframe 先不找(少見,找不到就照實說)。
PROOF_FN = r"""(names, links = []) => {
  const want = names.map(n => String(n).toLowerCase()).filter(Boolean);
  const hrefs = links.map(n => String(n).toLowerCase()).filter(Boolean);
  if (!want.length && !hrefs.length) return null;
  let best = null;
  // 選平台上存好的履歷(不上傳檔)時:頁面上連到那份履歷的連結(「預覽履歷」),連同上面的履歷名稱一起截
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.href.toLowerCase(), hit = hrefs.find(n => h.includes(n));
    if (!hit || !a.offsetParent) continue;
    const r = a.getBoundingClientRect();
    if (r.width && r.height && (!best || r.width * r.height < best.area)) best = {area: r.width * r.height, r, name: hit, el: a, left: 640};
  }
  for (const el of document.querySelectorAll('body *')) {
    if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') continue;
    const own = [...el.childNodes].filter(c => c.nodeType === 3).map(c => c.textContent).join('').trim().toLowerCase()
      || (el.getAttribute('title') || '').toLowerCase();
    const hit = own && want.find(n => own.includes(n));
    if (!hit) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (!best || r.width * r.height < best.area) best = {area: r.width * r.height, r, name: hit, el};
  }
  if (!best) return null;
  // 先把那一格捲到畫面中間再量:截圖只截目前畫面(浮在網頁上的申請視窗,截整頁時位置會被重排)
  best.el.scrollIntoView({block: 'center'});
  const r = best.el.getBoundingClientRect();
  // 連同左邊、上面的欄位標題(「Resume/CV」)一起截:他一眼看得出這是哪一格。座標是文件座標(加捲動距離)
  const x = Math.max(0, r.left + scrollX - (best.left || 240)), y = Math.max(0, r.top + scrollY - 70);
  return {name: best.name, url: location.href, x, y, width: Math.min(1100, r.left + scrollX - x + r.width + 120), height: r.height + 110};
}"""
