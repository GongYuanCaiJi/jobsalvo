#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_tab —— 程式自己看 agent 留著的那一頁(沒有 AI):讀頁面用的函式、讀回來之後的比對,和指令列的 read / shot。

真的去讀、去截由這張卡的門路做(chrome_door.for_card:那一頁所在的 ego 工作區);這裡放兩邊共用的東西:
  PAGE_FN / PROFILE_FN  在頁面裡跑的唯讀函式(申請表每一格的值與上傳欄;平台履歷的文字、連結、媒體)
  page_problems         讀回的申請表跟答案、上傳檔對不上的地方
  human_check           頁面要本人登入或驗證

用法:
  uv run python tools/apply_tab.py read --url 職缺網址 [--board B]
  uv run python tools/apply_tab.py shot --url 職缺網址 --out 檔案.png [--board B]
"""
import os, sys, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from profile_sync import ID_PARAMS    # 平台履歷的編號放在哪個網址參數(讀頁時留下連到那一份的連結)
import gate_cells                     # noqa: E402








# 頁面上每一個欄位:題目(label / aria-label / name)、型別、現在的值;上傳欄給檔名。只讀,不改頁面。
PAGE_FN = r"""() => {
  const selectedValue = '.multiselect__single,.form-control--category-menu .text-region';
  const headingSelector = 'legend,h1,h2,h3,h4,h5,h6,[role=heading],.h1,.h2,.h3,.h4,.h5,.h6';
  const lab = el => {
    const f = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    const w = el.closest('label');
    return ((f && f.innerText) || el.getAttribute('aria-label') || (w && w.innerText) || el.name || el.id || '').trim().slice(0, 200);
  };
  const fieldContext = el => {
    let text = '';
    // 一個控制項(或同名 radio 群組)是一題;不包到其他題目的控制項。
    for (let branch = el, p = el.parentElement; p && !['BODY', 'FORM'].includes(p.tagName); branch = p, p = p.parentElement) {
      // 平台把標題與控制項放成兄弟節點時,只取這個控制項前面最近的標題。
      let precedingHeading;
      for (let s = branch.previousElementSibling; s; s = s.previousElementSibling) {
        if (s.matches(headingSelector) && s.getClientRects().length && getComputedStyle(s).visibility !== 'hidden') {
          precedingHeading = s.innerText.trim(); break;
        }
      }
      const controls = Array.from(p.querySelectorAll('input,select,textarea,' + selectedValue))
        .filter(c => c.getClientRects().length && getComputedStyle(c).visibility !== 'hidden');
      if (controls.some(c => c !== el && c.type !== 'hidden'
          && !(el.type === 'radio' && el.name && c.type === 'radio' && c.name === el.name))) {
        if (precedingHeading) return precedingHeading + '\n' + text;
        break;
      }
      text = (p.innerText || '').trim().slice(0, 2000);
      if (precedingHeading) return precedingHeading + '\n' + text;
      const headings = [...p.querySelectorAll(headingSelector)]
        .filter(h => h.getClientRects().length && getComputedStyle(h).visibility !== 'hidden');
      if (headings.length === 1) break;
    }
    return text;
  };
  const fields = [];
  document.querySelectorAll('input,select,textarea,' + selectedValue).forEach(el => {
    if (['hidden', 'password', 'submit', 'button', 'image', 'reset'].includes(el.type)) return;
    const style = getComputedStyle(el);
    if (!el.getClientRects().length || style.display === 'none' || style.visibility === 'hidden') return;
    // 上傳欄:外掛這邊拿不到 el.files(undefined),只拿得到 value「C:\\fakepath\\檔名」;
    // 以前只看 files,檔明明選上了也讀成空的,每一張都被判「上傳欄裡沒有」。value 只有第一個檔名。
    const customSelect = el.matches(selectedValue);
    const v = customSelect ? el.innerText.trim() : el.type === 'file' ? (el.files ? Array.from(el.files).map(f => f.name)
                                            : (el.value ? [el.value.split(/[\\/]/).pop()] : []))
            : (el.type === 'checkbox' || el.type === 'radio') ? (el.checked ? (el.value || 'on') : '')
            : el.value;
    // 自製下拉選單(react-select 等,Greenhouse 新版表單):輸入框是空的,選好的值是旁邊顯示的字
    const combo = el.getAttribute('role') === 'combobox' || el.hasAttribute('aria-autocomplete');
    const box = combo && (el.closest('[class*="control"]') || el.parentElement);
    const shown = customSelect ? v : el.type === 'radio' ? (el.checked ? lab(el) : undefined)
                : el.tagName === 'SELECT' && el.selectedOptions[0] ? el.selectedOptions[0].text
                : box ? box.innerText.trim().slice(0, 200) : undefined;
    fields.push({label: lab(el), name: el.name || el.id || '', type: customSelect ? 'select-one' : el.type || el.tagName.toLowerCase(), value: v, shown,
                 context: el.type !== 'radio' || el.checked ? fieldContext(el) : undefined,
                 choiceControl: customSelect || ['radio', 'checkbox'].includes(el.type) || el.tagName === 'SELECT'
                   || (combo && ['text', 'search'].includes(el.type) && el.getAttribute('aria-expanded') === 'false' && Boolean(shown))});
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
  return {url: location.href, title: document.title, fields, shownFiles, lines, profileLinks, fieldContexts: true};
}""".replace('ID_PARAMS', json.dumps(sorted(set(ID_PARAMS.values()))))

PROFILE_FN = """() => {
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
    const value = (el.isContentEditable ? el.innerText :
      (el.tagName === 'SELECT' ? [...el.selectedOptions].map(x => x.text).join(' ') : el.value));
    return {label, section, role, type, autocomplete, occurrence, value: value || ''};
  });
  const emailQuery = new URL(location.href).searchParams;
  const emailThreadPrintView = emailQuery.get('view') === 'pt' &&
    emailQuery.get('search') === 'all' && emailQuery.has('th') &&
    Boolean(document.querySelector('.bodycontainer'));
  const emailMessages = emailThreadPrintView ? [...document.querySelectorAll('.bodycontainer .message')] : [];
  const emailBodies = emailMessages.map(el => (el.innerText || el.textContent || '').trim());
  // 收起來的內容(「更多」、摺疊區)常已在 DOM 裡只是沒畫出來;innerText 讀不到,判讀就說「無法確認」。
  // 把沒畫出來的文字接在後面(跳過程式碼類標籤),不靠各網站的按鈕名稱。
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const collapsed = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const p = n.parentElement, s = (n.nodeValue || '').trim();
    if (!s || !p || p.closest('script,style,noscript,template,svg') || p.getClientRects().length) continue;
    collapsed.push(s);
  }
  return {url: location.href, title: document.title, readyState: document.readyState,
    text: document.body.innerText + (collapsed.length ? '\\n' + collapsed.join('\\n') : ''),
    emailThreadPrintView,
    emailMessageCount: emailMessages.length,
    emailBodies,
    links: [...document.links].map(a => a.href),
    media: [...document.querySelectorAll('iframe,video,audio,source,embed,object')].map(el => el.src || el.data).filter(Boolean),
    anchors: [...document.links].map(a => ({href: a.href, text: (a.innerText || '').trim()})), fields};
}"""


def _lookup(url, board=None):
    """看板上這張卡記的那段對話、分頁,和開那一頁的那一家的門路(chrome_door.for_card:沒記到、那一家不能用了丟 Unreachable)。"""
    import board_doc as bd
    fb = json.loads(bd.load(board)['fb'])
    a = (fb.get(url) or {}).get('apply') or {}
    if not (a.get('session') or a.get('workspace')) or not a.get('tab_id'):
        raise LookupError('看板上沒有記這張是哪一段對話、哪個分頁')
    import chrome_door
    if chrome_door.gone_pages({url: fb[url]}):
        # 分頁編號每個 Chrome 程序從頭數:Chrome 重開過,記著的編號可能剛好是別張卡的頁,不能拿它去截、去讀
        raise LookupError(chrome_door.GONE)
    import chrome_door
    return a.get('session'), a['tab_id'], chrome_door.for_card(a)

















































def _norm(s):
    return ' '.join(str(s or '').split()).casefold()


_CONTACT = __import__('re').compile(r'@|^\+?[\d\s()-]{7,}$')   # 長得像 email 或電話的答案


# 網站的真人驗證(Cloudflare 的「請稍候…」「驗證您是人類」這類):agent 不替他按,也不規避。
# 程式控制的瀏覽器常被擋、他在別的視窗過了也接不回來(驗證綁著瀏覽器身分),這種網站讓他自己投。
HUMAN_CHECK = '這個網站要本人登入或驗證:按卡上的「👀」接手,處理完按「修改」接著填'
HUMAN_CHECK_WORDS = ('請稍候', '驗證您是人類', '正在執行安全驗證', 'verify you are human', 'just a moment',
                     'performing security verification', 'checking your browser')


def human_check(page):
    """這一頁是不是停在網站的真人驗證(看標題和頁面上前幾行字)。"""
    text = ' '.join([str(page.get('title') or '')] + [str(x) for x in (page.get('lines') or [])[:12]]).lower()
    return any(w in text for w in HUMAN_CHECK_WORDS)


def _same_question(q, field):
    import form_record as fr
    text = [field.get('label')] + str(field.get('context') or '').splitlines()
    return bool(fr._nq(q)) and any(fr._nq(q) == fr._nq(line) for line in text)


def page_problems(page, fb, url, uploaded=(), tab_url=None):
    """那一頁現在的值跟答案庫對不對得上。回問題清單(空的就是對上了)。
    先看它還是不是填好的那一頁:網址變了、欄位不見了,就是換頁了或被送出了(Codex 外掛不准在頁面上裝擋送出,只能事後查)。
    答案庫的答案(英文 v 或中文 zh)要出現在頁面某一格;履歷直接對上的(rz)短答案也一樣;上傳的檔要真的選在上傳欄。
    radio 按本輪題目與原生同名群組核對,其他欄位沿用值與顯示文字。"""
    vals, shown = set(), set()
    lines = {_norm(x) for x in page.get('lines') or []}   # 頁面上整行的字(104 選單選好的值)
    lines.difference_update(_norm(f.get('label')) for f in page.get('fields') or [] if f.get('type') == 'radio')
    for f in page.get('fields') or []:
        if f.get('type') == 'radio':
            continue  # 選項只能證明它自己的題目;另一題的同值不能充當答案。
        v = f.get('value')
        if isinstance(v, list):
            continue
        for x in (v, f.get('shown')):
            if _norm(x):
                vals.add(_norm(x))
        if _norm(f.get('shown')):
            shown.add(_norm(f.get('shown')))
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
            if 'choice' in x:
                import form_record as fr
                choice = x.get('choice')
                q = _norm(x.get('q'))
                # 程式自己讀的頁面知道每個控制項屬於哪一題,不靠 agent 交 name(有些工具讀不到 name)
                bound = [f for f in page.get('fields') or []
                         if isinstance(choice, dict) and f.get('choiceControl') is True
                         and _same_question(x.get('q'), f)
                         and _norm(choice.get('value')) in {_norm(f.get('value')), _norm(f.get('shown'))}]
                valid = (isinstance(choice, dict) and bool(e) and not fr.empty_answer(e)
                         and not (e.get('inf') or e.get('tr') or e.get('redo'))
                         and choice.get('source_hash') == fr.answer_fingerprint(e)
                         and isinstance(choice.get('why'), str) and bool(choice['why'].strip())
                         and isinstance(choice.get('value'), str) and bool(choice['value'].strip())
                         and len(bound) == 1
                         and bound[0].get('choiceControl') is True
                         and not any(_CONTACT.search(w) for w in want)
                         and _norm(choice['value']) in {_norm(bound[0].get('value')), _norm(bound[0].get('shown'))})
                if not valid:
                    bad.append(f'「{str(x.get("q"))[:40]}」的選項對應缺有效来源、理由或同一題的原生選取證據')
                continue
        elif x.get('src') == 'rz' and '\n' not in str(x.get('v') or '') and '.pdf' not in str(x.get('v') or '').lower():
            want = [w for w in (_norm(x.get('v')),) if w]
        else:
            continue
        q = _norm(x.get('q'))
        scoped = [f for f in page.get('fields') or [] if _same_question(x.get('q'), f)]
        values, display, text_lines = vals, shown, lines
        if page.get('fieldContexts') or any(f.get('type') == 'radio' for f in page.get('fields') or []):
            values = {_norm(v) for f in scoped for v in (f.get('value'), f.get('shown')) if _norm(v)}
            display = {_norm(f.get('shown')) for f in scoped
                       if f.get('type') != 'radio' and _norm(f.get('shown'))}
            text_lines = set()  # 未選中的選項也在頁面文字裡,不能採信。
            if x.get('src') == 'rz' and not scoped:
                visible = [_norm(s) for s in page.get('lines') or []]
                text_lines = {visible[i + 1] for i, line in enumerate(visible[:-1]) if line == q}
        contact = bool(want) and all(_CONTACT.search(w) for w in want)
        # 選單常只顯示答案的一段(國碼選單顯示「+44」,答案是「United Kingdom (+44)」):顯示的字整段出現在答案裡也算。
        # email/電話不適用:電話號碼裡本來就有國碼,這樣放會讓讀不到的電話冒充對上。
        if want and not any(w in values or (not contact and (w in text_lines or any(len(s) >= 2 and s in w for s in display)))
                            for w in want):
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
        import evidence
        with evidence.opened('apply_check', a.cmd, [a.url], a.board):
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
