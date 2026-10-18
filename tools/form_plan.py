#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
form_plan —— 程式配好答案、自己填:讀申請表的每一格(apply_tab.READ_FORM),照答案庫和以前填過的表單配答案,
配好的格子程式自己填(--fill,apply_tab.FILL_FN)。

agent 打開申請表(處理完一鍵帶入、上傳)之後跑 --fill;程式配不到的題目(新題目、要判斷意思的選項)才列給它判斷,
它判斷好的值也交給程式填(--set)。agent 只決定值,不用自己操作格子。填完再跑一次 --check,印出還沒對上的格子。
配法對所有網站都一樣,不分平台:

  題目 → 以前同一題(題目文字一樣)記過的答案:這張卡先,再來別張;再來答案庫的題目
  英文題目填英文(v),中文題目填中文(zh)
  選項類(radio、勾選):挑跟答案一樣、或是答案原文裡完整一段的那一項;挑不出就交給 agent
  頁面上已經是這個答案的:標「已經對了」,不用動

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
CHOICE = apply_tab.CHOICE
_q = apply_tab.qkey


def known_answers(fb, url):
    """題目 → 答案:{'src': 'bank', 'k'} 或 {'src': 'rz', 'v'}。這張卡以前記的先,再來別張,最後答案庫的題目。"""
    out = {}

    def add(q, ans):
        key = _q(q)
        if key and key not in out:
            out[key] = ans
    cards = [url] + [u for u in fb if u != url]
    for u in cards:
        for x in ((fb.get(u) or {}).get('form') or {}).get('f') or [] if isinstance(fb.get(u), dict) else []:
            if x.get('src') == 'bank' and x.get('k'):
                add(x.get('q'), {'src': 'bank', 'k': x['k']})
            elif x.get('src') == 'rz' and str(x.get('v') or '').strip():
                add(x.get('q'), {'src': 'rz', 'v': str(x['v'])})
    for e in fb.get('__ans__') or []:
        if isinstance(e, dict) and e.get('k'):
            add(e.get('q'), {'src': 'bank', 'k': e['k']})
    return out


def _value(ans, bank, zh):
    if ans['src'] == 'rz':
        return ans['v']
    e = bank.get(ans['k']) or {}
    if fr.empty_answer(e) or e.get('inf') or e.get('tr') or e.get('redo'):
        return None                      # 答案庫這一條還在等他確認或要重翻:不配,交給 agent 照規矩處理
    value = (e.get('zh') if zh else e.get('v')) or e.get('v') or e.get('zh')
    # 雙語答案(「公司徵才頁 / Company career page」):英文表單給英文那一段,中文表單給中文那一段
    parts = [p.strip() for p in apply_tab.BILINGUAL.split(str(value or '')) if p.strip()]
    if len(parts) > 1 and any(CJK.search(p) for p in parts) and not all(CJK.search(p) for p in parts):
        value = next(p for p in parts if bool(CJK.search(p)) == zh)
    return value


def build(page, fb, url):
    """回 {'fill': [...], 'done': [...], 'ask': [...], 'files': [...]}。每一項 {'q','field','type',...}。"""
    known = known_answers(fb, url)
    bank = {e.get('k'): e for e in fb.get('__ans__') or [] if isinstance(e, dict)}
    fields = [f for f in page.get('fields') or [] if isinstance(f, dict)]
    out = {'fill': [], 'done': [], 'ask': [], 'files': []}
    groups = {}
    for i, (f, q) in enumerate(zip(fields, apply_tab.questions(fields))):
        if f.get('type') == 'file':
            out['files'].append({'field': f.get('label') or f.get('name') or '上傳欄', 'now': f.get('value') or []})
            continue
        if f.get('type') == 'search':
            continue                      # 網站自己的搜尋框,不是申請表的題目
        groups.setdefault(apply_tab.group_key(f, i), []).append((f, q))
    for pairs in groups.values():
        group = [f for f, _ in pairs]
        first, title = group[0], pairs[0][1][:120]
        hit = (_q(title), known[_q(title)]) if _q(title) in known else None
        where = {'q': title, 'type': first.get('type'), 'backend': first.get('backend'),
                 'field': (first.get('name') if first.get('type') in CHOICE else first.get('label')) or first.get('label') or ''}
        if first.get('type') in CHOICE:
            where['options'] = [f.get('label') for f in group]
            where['backends'] = [f.get('backend') for f in group]
        if not hit:
            out['ask'].append(dict(where, why='答案庫和以前的表單都沒有這一題'))
            continue
        q, ans = hit
        zh = bool(CJK.search(title))
        value = _value(ans, bank, zh)
        if value is None:
            out['ask'].append(dict(where, why='答案庫這一條還在等確認或要重翻', **ans))
            continue
        item = dict(where, value=value, **{k: v for k, v in ans.items() if k != 'v'})
        if first.get('type') in CHOICE:
            pick = next((f for f in group if apply_tab.norm(f.get('label')).replace(' ', '') == apply_tab.norm(value).replace(' ', '')),
                        None) or next((f for f in group if apply_tab.matches(value, f.get('label'), 'option')), None)
            if not pick:
                out['ask'].append(dict(item, why='選項裡沒有跟答案一樣、或是答案一段的那一項,判斷哪一項同義'))
                continue
            item['pick'], item['backend'] = pick.get('label'), pick.get('backend')
            on = [f for f in group if f.get('value')]
            (out['done'] if len(on) == 1 and on[0] is pick else out['fill']).append(item)
            continue
        now = first.get('value') if first.get('value') not in (None, '') else first.get('shown')
        if apply_tab.matches(value, now, apply_tab.kind_of(first, now is first.get('shown'))):
            out['done'].append(item)
        else:
            out['fill'].append(dict(item, now=now or ''))
    return out


def sheet_fields(plan):
    """清單 → 交件單 fields 的寫法(有 k 的不用寫值;履歷事實寫值)。"""
    rows = []
    for x in plan['fill'] + plan['done']:
        row = {'q': x['q'], 'src': x['src']}
        if x['src'] == 'bank':
            row['k'] = x['k']
        else:
            row['value'] = x['value']
        rows.append(row)
    return rows


def render(plan, check=False):
    L = []
    if check:
        L.append('還沒對上的格子(0 個就是全部對了):%d' % len(plan['fill']))
    else:
        L.append('程式讀了這一頁,配好 %d 格要填、%d 格已經對了;%d 題程式配不到,你自己照規矩判斷。'
                 % (len(plan['fill']), len(plan['done']), len(plan['ask'])))
    for x in plan['fill']:
        how = f'選「{x["pick"]}」' if x.get('pick') else f'填:{x["value"]}'
        L.append(f'- 填 | 題目「{x["q"]}」(格子:{x["field"]},{x["type"]}) → {how}'
                 + (f'(現在是:{str(x.get("now"))[:60]})' if x.get('now') else ''))
    if check:
        return '\n'.join(L)
    for x in plan['done']:
        L.append(f'- 已經對了,不用動 | 題目「{x["q"]}」')
    for x in plan['ask']:
        opts = f';選項:{" / ".join(str(o) for o in x["options"])}' if x.get('options') else ''
        L.append(f'- 你判斷 | 題目「{x["q"]}」(格子:{x["field"]},{x["type"]}):{x["why"]}{opts}'
                 + (f';答案庫原文:{x["value"]}' if x.get('value') else ''))
    for x in plan['files']:
        L.append(f'- 上傳欄「{x["field"]}」:照上傳規則選檔(現在:{", ".join(x["now"]) or "空的"})')
    L.append('交件單 fields 裡程式配好的這幾格可以直接用(程式配不到的你自己補):')
    L.append(json.dumps(sheet_fields(plan), ensure_ascii=False))
    return '\n'.join(L)


def how(x):
    """程式自己填這一格用哪一種填法(apply_tab.FILL_FN)。"""
    return ('click' if x['type'] in CHOICE else 'select' if x['type'] == 'select-one'
            else 'combo' if x['type'] == 'combobox' else 'text')


def chosen(plan, picks):
    """agent 判斷好的答案([{q, value}])→ 要填的格子:照題目對回「你判斷」那一題,選項類照選項的字對回那一項。"""
    asks = {apply_tab.qkey(x['q']): x for x in plan['ask']}
    out, miss = [], []
    for p in picks if isinstance(picks, list) else []:
        x = asks.get(apply_tab.qkey(str((p or {}).get('q') or '')))
        value = str((p or {}).get('value') or '')
        if not x or not value:
            miss.append(f'沒有「{(p or {}).get("q")}」這一題要你判斷,或沒寫值')
            continue
        if x['type'] in CHOICE:
            i = next((i for i, o in enumerate(x['options']) if apply_tab.matches(value, o, 'option')), None)
            if i is None:
                miss.append(f'「{x["q"]}」的選項裡沒有「{value}」')
                continue
            out.append(dict(x, value=value, pick=x['options'][i], backend=x['backends'][i]))
        else:
            out.append(dict(x, value=value))
    return out, miss


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--url', required=True, help='職缺網址(看板上這張卡)')
    ap.add_argument('--tab', required=True, help='申請表所在的 工作區編號:Page 標籤')
    ap.add_argument('--check', action='store_true', help='填完再跑:只印還沒對上的格子')
    ap.add_argument('--fill', action='store_true', help='程式把配好的格子自己填好,再印出還沒對上的和要你判斷的')
    ap.add_argument('--set', help='你判斷好的答案交給程式填:JSON [{"q":"題目","value":"值(選項類寫選項的字)"}]')
    a = ap.parse_args()
    import chrome_door
    doc = bd.load(bd.target())
    fb = json.loads(doc['fb'])
    workspace = ((fb.get(a.url) or {}).get('apply') or {}).get('workspace') or {}
    space, _, label = a.tab.partition(':')
    if not workspace.get('name') or str(workspace.get('id')) != space or not label:
        sys.exit('這個 --tab 不是這張卡這一輪的工作區;寫 工作區編號:Page(例如 12:p1)')
    door = chrome_door.EgoDoor()
    door.workspace = dict(workspace, page=label)
    page = door.read_page(a.tab)
    _keep_proof(door, a, page)
    plan = build(page, fb, a.url)
    todo, notes = (list(plan['fill']) if a.fill else []), []
    if a.set:
        try:
            picked, notes = chosen(plan, json.loads(a.set))
        except ValueError:
            picked, notes = [], ['--set 不是 JSON']
        todo += picked
    if todo:
        done = door.fill_fields(a.tab, [{'backend': x['backend'], 'how': how(x), 'value': x.get('pick') or x['value']}
                                        for x in todo if x.get('backend')])
        notes += [f'程式填不上「{x["q"]}」:{r.get("why")}(這一格你自己用 ego 填)'
                  for x, r in zip([x for x in todo if x.get('backend')], done) if not r.get('ok')]
        plan = build(door.read_page(a.tab), fb, a.url)
    print('\n'.join(notes + [render(plan, check=a.check)]))


def _keep_proof(door, a, page):
    """這一頁上看得到要送的檔名(多步驟表單常只在選檔那一步看得到):程式順手截下那一塊,
    填完驗收時放到卡上給他看。截不到不影響填表。"""
    import apply_run
    names = apply_run.proof_names(a.url)
    doc = bd.load(bd.target())
    job = next((j for j in doc['data'].get('jobs') or [] if j.get('id') == a.url), None)
    links = apply_run.proof_links(a.url, job, json.loads(doc['fb']))
    text = json.dumps(page, ensure_ascii=False).lower()
    if not any(n.lower() in text for n in names + links):
        return
    try:
        shot = apply_run.early_proof(a.url)
        os.makedirs(os.path.dirname(shot), exist_ok=True)
        found = door.shot_proof(a.tab, shot, names, links)
        if found:
            with open(shot[:-4] + '.json', 'w', encoding='utf-8') as f:
                json.dump(found, f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 — 截證據失敗不擋填表;照實印出來,agent 的紀錄看得到
        print(f'(截履歷那一塊沒成功:{str(e)[:80]};不影響填表)', file=sys.stderr)


if __name__ == '__main__':
    main()
