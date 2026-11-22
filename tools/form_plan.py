#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
form_plan —— 程式配好答案、自己填:讀申請表的每一格(apply_tab.READ_FORM),照答案庫和以前填過的表單配答案,
配好的格子程式自己填(--fill,apply_tab.FILL_FN)。

agent 打開申請表(處理完一鍵帶入、上傳)之後跑 --fill;程式配不到的題目(新題目、要判斷意思的選項)才列給它判斷,
選項和沒有答案的題目由 agent 處理(--set);程式只填空白文字欄。--check 只列仍空白的可自動填欄位。
配法對所有網站都一樣,不分平台:

  題目 → 以前同一題(題目文字一樣)記過的答案:這張卡先,再來別張;再來答案庫的題目
  答案庫一條有兩種文字(英文 v 跟中文 zh,或一格裡寫「中文 / English」)時,用哪一種由 agent 看頁面判斷(--lang);
  沒給 --lang 就只填只有一種文字的答案,兩種的列給 agent(程式不看題目有沒有中文字,ADR-0011)
  選項類(radio、勾選、下拉):交給 agent 依題目和頁面內容處理
  非空白文字欄:保留並列出頁面讀到的值,不判斷是否符合答案

用法(agent 在填表那一輪跑;看板照派它的程式指定的那一份):
  python3 tools/form_plan.py --url 職缺網址 --tab 工作區編號:Page --fill
  python3 tools/form_plan.py --url 職缺網址 --tab 工作區編號:Page --set '[{"q":"題目","value":"值"}]'
  python3 tools/form_plan.py --url 職缺網址 --tab 工作區編號:Page --check
"""
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apply_tab              # noqa: E402
import board_doc as bd        # noqa: E402
import form_record as fr      # noqa: E402

CJK = re.compile(r'[一-鿿]')
BILINGUAL = re.compile(r'\s*[/／]\s*')
CHOICE = apply_tab.CHOICE


def known_answers(fb, url):
    """題目 → 答案:{'src': 'bank', 'k'} 或 {'src': 'rz', 'v'}。這張卡以前記的先,再來別張,最後答案庫的題目。"""
    out = {}

    def add(q, ans):
        if q and str(q) not in out:
            out[str(q)] = ans
    cards = [url] + [u for u in fb if u != url]
    for u in cards:
        for x in ((fb.get(u) or {}).get('form') or {}).get('f') or [] if isinstance(fb.get(u), dict) else []:
            if x.get('src') == 'bank' and x.get('k'):
                add(x.get('q'), {'src': 'bank', 'k': x['k']})
            elif x.get('src') == 'rz' and str(x.get('v') or '').strip():
                add(x.get('q'), {'src': 'rz', 'v': str(x['v'])})
    for e in fb.get('__ans__') or []:
        if e.get('k'):
            add(e.get('q'), {'src': 'bank', 'k': e['k']})
    return out


def _versions(ans, bank):
    """這一條答案的每一種文字:只有一種是 {'': 值},有兩種是 {'en': 送出版 v, 'zh': 中文版 zh};
    一格裡寫了兩種文字(「公司徵才頁 / Company career page」)也拆成兩種。分的是使用者自己的答案,不看頁面。
    答案庫這一條還在等他確認或要重翻:None(不配,交給 agent 照規矩處理)。"""
    if ans['src'] == 'rz':
        return {'': ans['v']}
    e = bank.get(ans['k']) or {}
    if fr.empty_answer(e) or e.get('inf') or e.get('tr') or e.get('redo'):
        return None
    v, zh = str(e.get('v') or ''), str(e.get('zh') or '')
    if v and zh and v != zh:
        return {'en': v, 'zh': zh}
    value = v or zh
    parts = [p.strip() for p in BILINGUAL.split(value) if p.strip()]
    if any(CJK.search(p) for p in parts) and not all(CJK.search(p) for p in parts):
        return {'zh': next(p for p in parts if CJK.search(p)), 'en': next(p for p in parts if not CJK.search(p))}
    return {'': value}


def _value(ans, bank, lang):
    """要填的那一種文字:只有一種就是它;兩種就照 agent 說的這一頁的語言(lang),沒說就 None 交給 agent。"""
    versions = _versions(ans, bank) or {}
    return versions.get('') or versions.get(lang)


def _shown(ans, bank):
    return ' | '.join(f'{lang}: {text}' for lang, text in (_versions(ans, bank) or {}).items())


def build(page, fb, url, lang):
    """回空白欄位、頁面既有值、需 agent 處理的欄位和附件。lang:agent 判斷這一頁表單的語言('zh'、'en')。"""
    known = known_answers(fb, url)
    bank = {e.get('k'): e for e in fb.get('__ans__') or []}
    fields = page.get('fields') or []
    out = {'fill': [], 'observed': [], 'ask': [], 'files': []}
    groups = {}
    for i, (f, q) in enumerate(zip(fields, apply_tab.questions(fields))):
        if f.get('type') == 'file':
            out['files'].append({'field': f.get('label') or f.get('name') or '上傳欄', 'now': f.get('value') or []})
            continue
        groups.setdefault(apply_tab.group_key(f, i), []).append((i, f, q))
    for pairs in groups.values():
        group = [f for _, f, _ in pairs]
        first, title = group[0], pairs[0][2]
        hit = (title, known[title]) if title in known else None
        where = {'q': title, 'id': f'#{pairs[0][0] + 1}', 'type': first.get('type'), 'backend': first.get('backend'),
                 'field': (first.get('name') if first.get('type') in CHOICE else None) or first.get('label') or ''}
        if first.get('type') in CHOICE:
            where['options'] = [f.get('label') for f in group]
            where['backends'] = [f.get('backend') for f in group]
        if not title:
            out['ask'].append(dict(where, near=str(first.get('context') or ''),
                                   why='瀏覽器沒給這一格題目名稱;題目是哪一句由你看頁面判斷'))
            continue
        if not hit:
            out['ask'].append(dict(where, why='答案庫和以前的表單都沒有一字不差的這一題'))
            continue
        q, ans = hit
        if first.get('type') in CHOICE:
            selected = next((f.get('label') for f in group if f.get('value')), '')
            out['ask'].append(dict(where, value=_value(ans, bank, lang) or _shown(ans, bank), now=selected,
                                   why='請依題目和頁面內容處理選項'))
            continue
        now = first.get('value') if first.get('value') not in (None, '') else first.get('shown')
        if isinstance(now, str) and now:     # 頁上原本有的值照讀到的算,只有空白也是值(ADR-0011)
            out['observed'].append(dict(where, **ans, now=now))
            continue
        if first.get('disabled'):
            out['ask'].append(dict(where, why='這一格頁上鎖住(停用),程式不填'))
            continue
        if _versions(ans, bank) is None:
            out['ask'].append(dict(where, why='答案庫這一條還在等確認或要重翻'))
            continue
        value = _value(ans, bank, lang)
        if value is None:
            out['ask'].append(dict(where, value=_shown(ans, bank),
                                   why='答案庫這一條有兩種文字,這一頁用哪一種由你判斷(--lang zh 或 --lang en)'))
            continue
        out['fill'].append(dict(where, value=value, **ans))
    return out


def sheet_fields(plan):
    """清單 → 交件單 fields 的寫法(有 k 的只留來源鍵;其他值取頁面或填入值)。"""
    rows = []
    for x in plan['fill'] + plan['observed']:
        row = {'q': x['q'], 'src': x['src']}
        if x['src'] == 'bank':
            row['k'] = x['k']
        else:
            row['value'] = x.get('now') if 'now' in x else x['value']
        rows.append(row)
    return rows


def render(plan, check):
    L = []
    if check:
        L.append('仍空白、尚未帶入的可用答案欄:%d' % len(plan['fill']))
    else:
        L.append('程式只填空白文字欄:%d;頁面已有值:%d;需要你處理:%d。既有值只列出觀察,不判斷是否符合答案。'
                 % (len(plan['fill']), len(plan['observed']), len(plan['ask'])))
    for x in plan['fill']:
        L.append(f'- 填空白欄 | 題目「{x["q"]}」(格子:{x["field"]},{x["type"]}) → {x["value"]}')
    if check:
        return '\n'.join(L)
    for x in plan['observed']:
        L.append(f'- 頁面已有值 | 題目「{x["q"]}」: {x["now"]}')
    for x in plan['ask']:
        opts = f';選項:{" / ".join(str(o) for o in x["options"])}' if x.get('options') else ''
        now = f';目前選中:{x["now"]}' if x.get('now') else ''
        near = f';這一格前面的字:「{x["near"]}」' if x.get('near') else ''
        title = f'題目「{x["q"]}」' if x['q'] else '沒有題目名稱'
        L.append(f'- 你處理 | 格 {x["id"]} {title}(格子:{x["field"]},{x["type"]}):{x["why"]}{near}{opts}{now}'
                 + (f';答案庫原文:{x["value"]}' if x.get('value') else ''))
    if any(not x['q'] for x in plan['ask']):
        L.append('沒有題目名稱的格子:你看頁面判斷題目,用 --set \'[{"id":"#格子編號","q":"頁上的題目原文","value":"值"}]\';'
                 'q 要一字不差在頁上,不寫 value 就用記過的同一題答案')
    for x in plan['files']:
        L.append(f'- 上傳欄「{x["field"]}」:照上傳規則選檔(現在:{", ".join(x["now"]) or "空的"})')
    L.append('交件單 fields 裡程式配好的這幾格可以直接用(程式配不到的你自己補):')
    L.append(json.dumps(sheet_fields(plan), ensure_ascii=False))
    return '\n'.join(L)


def how(x):
    """程式自己填這一格用哪一種填法(apply_tab.FILL_FN)。"""
    return ('click' if x['type'] in CHOICE else 'select' if x['type'] == 'select-one'
            else 'combo' if x['type'] == 'combobox' else 'text')


def chosen(plan, picks, page, fb, url, lang):
    """agent 判斷好的答案([{q 或 id, value}])→ 按題目或格子編號與實際選項原文定位格子。
    沒有題目名稱的格子由 agent 給格子編號(id)和頁上的題目原文(q):q 要一字不差出現在頁上才算(ADR-0011),
    沒寫值就用以前同一題(一字不差)記過的答案。"""
    asks = {x['id']: x for x in plan['ask']}
    asks.update({x['q']: x for x in plan['ask'] if x['q']})
    lines = [str(line) for line in page.get('lines') or []]
    known = known_answers(fb, url)
    bank = {e.get('k'): e for e in fb.get('__ans__') or []}
    out, miss = [], []
    for p in picks if isinstance(picks, list) else []:
        p = p if isinstance(p, dict) else {}
        q, value = str(p.get('q') or ''), str(p.get('value') or '')
        x = asks.get(str(p['id'])) if p.get('id') else asks.get(q)
        if x and not x['q'] and q:
            if q not in lines:
                miss.append(f'「{x["id"]}」:頁上沒有一字不差的「{q}」,這一格沒填')
                continue
            if not value and q in known:
                value = _value(known[q], bank, lang) or ''
        if not x or not value:
            miss.append(f'沒有「{p.get("id") or q}」這一格要你判斷,或沒寫值(也沒有記過這一題的答案)')
            continue
        if x['type'] in CHOICE:
            i = next((i for i, option in enumerate(x['options']) if value == option), None)
            if i is None:
                miss.append(f'「{x["q"]}」沒有逐字相同的選項「{value}」')
                continue
            out.append(dict(x, value=value, backend=x['backends'][i]))
        else:
            out.append(dict(x, value=value))
    return out, miss


def main(argv=None, browser=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--url', required=True, help='職缺網址(看板上這張卡)')
    ap.add_argument('--tab', required=True, help='申請表所在的 工作區編號:Page 標籤')
    ap.add_argument('--check', action='store_true', help='填完再跑:只印仍空白且有可用答案的欄位')
    ap.add_argument('--fill', action='store_true', help='程式把配好的格子自己填好,再印出還沒對上的和要你判斷的')
    ap.add_argument('--lang', choices=('zh', 'en'),
                    help='這一頁表單用哪一種文字(你看頁面判斷):答案庫有兩種文字的答案,zh 填中文那一份,en 填英文那一份')
    ap.add_argument('--set', help='你判斷好的答案交給程式填:JSON [{"q":"題目","value":"值(選項類寫選項的字)"}];'
                                  '沒有題目名稱的格子寫 {"id":"#格子編號","q":"頁上的題目原文","value":"值(不寫就用記過的答案)"}')
    ap.add_argument('--board', help='看板檔(預設照設定)')
    a = ap.parse_args(argv)
    from web import seen
    from web.outside import Real
    doc = bd.load(a.board or bd.target())
    fb = json.loads(doc['fb'])
    workspace = ((fb.get(a.url) or {}).get('apply') or {}).get('workspace') or {}
    space, _, label = a.tab.partition(':')
    if not workspace.get('name') or str(workspace.get('id')) != space or not label:
        sys.exit('這個 --tab 不是這張卡這一輪的工作區;寫 工作區編號:Page(例如 12:p1)')
    browser = browser or Real.for_workspace(int(space), workspace['name'])
    page = seen.form(browser, a.tab)
    plan = build(page, fb, a.url, a.lang)
    todo, notes = (list(plan['fill']) if a.fill else []), []
    if a.set:
        try:
            picked, notes = chosen(plan, json.loads(a.set), page, fb, a.url, a.lang)
        except ValueError:
            picked, notes = [], ['--set 不是 JSON']
        todo += picked
    if todo:
        done = browser.fill_form(a.tab, [{'backend': x['backend'], 'how': how(x), 'value': x['value']}
                                        for x in todo if x.get('backend')])
        notes += [f'程式填不上「{x["q"]}」:{r.get("why")}(這一格你自己用 ego 填)'
                  for x, r in zip([x for x in todo if x.get('backend')], done) if not r.get('ok')]
        plan = build(seen.form(browser, a.tab), fb, a.url, a.lang)
    print('\n'.join(notes + [render(plan, check=a.check)]))



if __name__ == '__main__':
    main()
