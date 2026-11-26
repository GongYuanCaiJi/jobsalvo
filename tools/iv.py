#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iv —— agent 寫「🎤 面試準備」的唯一入口。規則都在 interview_bank.apply,不合的直接擋下並說要放哪。

  iv.py show                         有在準備的每一場:什麼時候面、幾題、出處分佈
  iv.py show 範例科技                  一場的面試時間和每一題(題目、出處、為什麼;行尾 [ ] 是內部 id)
  iv.py find 薪水                    題庫裡找題(先找,有了就 link,不要再寫一題一樣的)
  iv.py cats                         現有的大類 · 小類和各幾題
  iv.py get  薪水 > q.json           拿一題現在的 json(含 rev);改一題一定先 get,改完再 put
                                     題目參數(get/link/unlink)寫 id 或題目文字的一段都行,對到唯一一題才動;
                                     對使用者說話一律講題目,不講 id(他在看板上看不到 id)
  iv.py put  題目.json [--card 範例科技 --src guess --why "…"]
                                     新增或改一題(json 帶 id 就是改,要帶 get 拿到的 rev);帶 --card 就順便記進那一場
  iv.py link 範例科技 WH3 --src this --cite "面試心得 2026-06-21 https://interview.example/…" --quote "怎麼評估一個模型的好壞" [--why "…"]
  iv.py unlink 範例科技 WH3
  iv.py share 自我介紹               設成共有:每一場面試自動帶上,改一次全部更新(追問跟著原題);iv.py unshare 取消
  iv.py variant 範例科技 自我介紹 --script-file 稿.txt [--focus-file 考點.txt]
                                     只改這場的逐字稿(和考點),共有版不動,不要複製題目;--clear 還原成共有版
  iv.py round 範例科技 --at 2026-10-23T15:00 --how 現場 --where "月球市…" [--note "…"] [--old-at 舊時間]
  iv.py round 範例科技 --del 2026-10-23T15:00
  iv.py check                        整份題庫不合規則的地方(含:磨合中、定案的題,面試備忘速查表上一句話是空的)

面試備忘(印出來帶去面試的 PDF)就是從逐字稿排出來的,所以逐字稿要這樣寫,磨一題就是同時寫備忘:
  第一段                一句話:面試時掃一眼要看的那句。速查表「一句話」那格
  【要點】① … ② …      緊接在後,一段,用 ①②③ 寫幾個短句,每句單獨看得懂。速查表「要點」那格,一句一行
  ──────               單獨一行的分界。以上是備忘,以下是完整回答
  ## 小標 …            完整回答,印在備忘的附錄
沒有分界、逐字稿又超過 90 字,速查表那格會是空的:stage 要設成 wip 或 ok 的題,iv.py 會擋下並說缺什麼。
每次 put / variant 存完,會印出這題在速查表上的那一行,不用另外開 PDF 看。
逐字稿裡可以放表格(| a | b | 加 |---|---|)和圖(![說明](fig:檔名.svg),檔放資料夾的 figures/)。

卡片可以寫網址、網址的一段或公司名,對到唯一一張就好。
題目 json 的欄位就是看板上這一場的這棵樹(iv.py show <卡> 照看板的順序印同一棵):
  分區        --src 決定:共有 → 我被問過 → 這家考過 → 別家考過 → 預想的
  分組標題    cat 大類 · sub 小類。iv.py cats 列出現有的,沿用現有寫法
  一行        t 題目。列表照大類排、不照寫入順序,每一行單獨看就懂在問什麼
  縮在底下    parent 追問(填原題 id):對方聽完原題接著問的那一題。只有一層,原題那行標「追問 N」。\n              原題也在這場:收在原題底下、跟著原題的分區;原題不在這場:自己一行,標「追問自」
  收合區      ask 題面(對方怎麼問)、focus 考點(對方在看什麼、答壞的樣子)、ref 準備材料(答之前要先讀的東西)
  展開內容    script 逐字稿;## 小標是完整回答裡的段落標題
  行尾        stage 走到哪 todo/first/wip/ok、lim 限時秒數(念超過標紅)
  標記        共有:每一場自動帶上,改一次全部更新,它底下的追問也一起到每一場。這場版:variant 換掉的逐字稿和考點
  例:{"t": "曲線下面積是什麼？", "parent": "ML-1", "cat": "技術基本功", "sub": "機器學習", "ask": "…", "focus": "…", "stage": "wip", "script": "…"}
挑功能:
  一串接著問的題                     原題一題,其餘掛成它的追問
  一串接在共有題後面、只屬於這一場  這串的第一題當這場自己的原題,其餘掛在它底下(掛在共有題底下會跟到每一場)
  同一題這場想講得不一樣              variant,題目不複製
  同一題幾種講法                      同一份逐字稿,每種講法一個 ## 小標
出處 --src:me 我被問過 / this 這家考過 / other 別家考過 / guess 預想的(要 --why)。
this、other 要 --cite 那一篇的網址(點了直接開那一篇,不是公司頁)和 --quote 原文(那一篇怎麼寫就貼怎麼寫)。
put / link / variant 存完會印出這題落在那一場的哪裡,和看板上會空著的格子。
全部寫完跑 iv.py show <卡>,照印出來的順序讀一遍:每一行單獨看懂、追問縮在對的原題底下、分區和分組對。
"""
import argparse, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_doc as bd, card as cd, interview_bank as ib, config as cf


def _load():
    d = bd.load()
    return d['data'], (json.loads(d['fb']) if isinstance(d['fb'], str) else d['fb'])


def qref(q, data, loose=False):
    """題目:寫 id,或題目文字的一段,對到唯一一題才動。id 只是內部主鍵,agent 和使用者看到的都是題目文字。"""
    items = ib.normalize(data.get('bank'))['items']
    hit = [it for it in items if it.get('id') == q] or [it for it in items if q.lower() in str(it.get('t') or '').lower()]
    if len(hit) == 1:
        return hit[0]
    if not hit and loose:
        return {'id': q, 't': q}
    names = '、'.join(f"「{it.get('t')}」" for it in hit[:6]) or '沒有'
    raise SystemExit(f'「{q}」要對到唯一一題,現在對到:{names}(再多寫幾個字,或用 iv.py find 找)')


def write(op, form=None, item_id=None, job=None, kind='prep'):
    out = {}
    def mut(data, fb):
        data['bank'] = out['bank'] = ib.apply(data.get('bank'), op, form, item_id, job, kind)
    try:
        bd.set_data(mut)
    except ValueError as e:
        raise SystemExit(f'沒寫進去:{e}')
    ib.export_file(out['bank'], cf.HOME)
    try:
        import folder_history
        folder_history.note_saved(cf.HOME)
    except Exception as e:  # noqa: BLE001 — 資料已經寫進去了,只是沒排上版本紀錄
        print(f'⚠ 資料夾版本紀錄沒排上:{e}')
    _rerender_memos()
    return out['bank']


def _rerender_memos():
    """題庫一改,每一場面試的小抄(面試備忘 PDF)跟著重排:改題庫的所有路徑都經過 write,不靠記得另外排。
    網頁存檔走看板自己的預排;這裡是命令列這一條。"""
    import iv_sheet
    data, fb = _load()
    for u in iv_sheet.interviews(data, fb):
        try:
            iv_sheet.render(data, u, cf.HOME)
        except Exception as e:  # noqa: BLE001 — 題庫已經寫進去了,小抄排不出來要照實說,按預覽時會再排一次
            print(f'⚠ 面試備忘沒排好({u}):{e}')


def memo_check(script, title):
    """磨合中、定案的題,逐字稿要能排出速查表那一行。回要提示的話(空字串 = 沒問題)。"""
    import iv_sheet
    one, kw = iv_sheet.memo_row(script)
    head = re.split(r'\n\s*──────\s*\n', '\n' + str(script or '').strip() + '\n', maxsplit=1)[0].strip()
    first = head.split('\n', 1)[0]
    if one and not first.startswith(('## ', iv_sheet.KEYS)):
        return ''
    return f'「{title}」逐字稿開頭沒有一句話:第一段寫面試時掃一眼要看的那句,接著【要點】① …,再單獨一行 ──────,之後才是完整回答'


def memo_show(script):
    import iv_sheet
    one, kw = iv_sheet.memo_row(script)
    print('面試備忘速查表上這題是:')
    print('  一句話:' + (one or '(空)'))
    print('  要點:' + (kw.replace('<br>', ' / ') or '(沒有)'))


def tree(bank, job):
    """這一場在看板上的樣子(board.js ivJobPageHTML):[(分區, [(大類 · 小類, [(原題, [追問…])…])…])…]。
    追問的原題也在這一區的同一批裡,就收在原題底下、跟著原題的分區;原題不在這場,追問自己佔一行。"""
    bank = ib.normalize(bank)
    sh = set(ib.shared_ids(bank))
    qs = [it for it in (ib.effective(bank, job, q) for q in ib.job_qids(bank, job)) if it]
    src = {q: v.get('src') for q, v in (bank['links'].get(job) or {}).items()}
    own = [it for it in qs if it['id'] not in sh]
    secs = [('共有', [it for it in qs if it['id'] in sh], [it for it in qs if it['id'] in sh])]
    secs += [(name, [it for it in own if src.get(it['id']) == s], own) for s, name in ib.SRCS.items()]
    out = []
    for name, rows, pool in secs:
        ids = {it['id'] for it in pool}
        kids = {}
        for it in pool:
            if it.get('parent') in ids:
                kids.setdefault(it['parent'], []).append(it)
        tops = [it for it in rows if it.get('parent') not in ids]
        cats = list(bank['cats']) + [c for c in dict.fromkeys(it.get('cat') or '其他' for it in tops) if c not in bank['cats']]
        groups = {}
        for c in cats:
            for it in tops:
                if (it.get('cat') or '其他') == c:
                    groups.setdefault(' · '.join(x for x in (c, it.get('sub')) if x), []).append((it, kids.get(it['id'], [])))
        if groups:
            out.append((name, list(groups.items())))
    return out


def landing(bank, job, qid):
    """這題在看板上落在哪裡,和看板上會空著的格子。回要印的幾行。"""
    bank = ib.normalize(bank)
    items = {it['id']: it for it in bank['items']}
    it, path = items[qid], None
    for name, groups in tree(bank, job) if job else []:
        for g, rows in groups:
            for top, kids in rows:
                if top['id'] == qid:
                    it, path = top, [name, g, top.get('t', '')]
                for k in kids:
                    if k['id'] == qid:
                        it, path = k, [name, g, top.get('t', ''), f"追問「{k.get('t', '')}」"]
    if path is None:
        path = ['題庫', ' · '.join(x for x in (it.get('cat'), it.get('sub')) if x), it.get('t', '')]
    out = ['落在:' + ' › '.join(path) + ('  〔這場版〕' if it.get('ov') else '')]
    pa = items.get(it.get('parent'))
    if job and pa and len(path) == 3:
        out.append(f"  看板上單獨一行,標「追問自「{pa.get('t', '')}」」:要收在原題底下,原題也要記進這場")
    if pa and pa['id'] in ib.shared_ids(bank):
        out.append(f"  這題跟著共有題「{pa.get('t', '')}」出現在每一場")
    empty = [n for k, n in (('ask', '題面'), ('focus', '考點')) if not ib._plain(it.get(k))]
    if empty:
        out.append('  看板上這題的' + '、'.join(empty) + '空著')
    return out


def link_form(a):
    if a.src not in ib.SRCS:
        raise SystemExit('--src 只能是 ' + ' / '.join(f'{k}({v})' for k, v in ib.SRCS.items()))
    if a.src == 'guess' and not (a.why or '').strip():
        raise SystemExit('預想的題要 --why 寫為什麼這場會問(從職缺哪一句推的)')
    return {'src': a.src, 'cite': a.cite or '', 'quote': a.quote or '', 'why': a.why or ''}


def when(r):
    return r['at'].replace('T', ' ') + ' ' + r['how'] + (' · ' + r['where'] if r.get('where') else '')


def show(a, data, fb):
    bank = ib.normalize(data.get('bank'))
    items = {it['id']: it for it in bank['items']}
    if not a.card:
        live = [j for j in data.get('jobs') or [] if (fb.get(j.get('id')) or {}).get('oc') in ('iv', 'offer')]
        ids = [j['id'] for j in live] + [k for k in list(bank['links']) + list(bank['rounds']) if k not in {j['id'] for j in live}]
        for jid in dict.fromkeys(ids):
            j = cd.lookup(jid, data)
            qs = bank['links'].get(jid, {})
            srcs = {s: sum(1 for q, v in qs.items() if v['src'] == s and q not in ib.shared_ids(bank)) for s in ib.SRCS}
            nsh = len(ib.shared_ids(bank))
            rs = bank['rounds'].get(jid, [])
            print(f"{cd.label(j)}  [{(fb.get(jid) or {}).get('oc') or '—'}]  {jid}")
            print('   面試:' + ('; '.join(when(r) for r in rs) if rs else '還沒記時間'))
            print(f'   {len(ib.job_qids(bank, jid))} 題 · ' + ' · '.join(
                [f'共有 {nsh}'] * bool(nsh) + [f'{ib.SRCS[s]} {n}' for s, n in srcs.items() if n]))
        return
    j = cd.lookup(a.card, data)
    print(f"{cd.label(j)}  {j['id']}")
    for r in bank['rounds'].get(j['id'], []):
        print('  面試 ' + when(r) + (' · ' + r['note'] if r.get('note') else ''))
    links = bank['links'].get(j['id'], {})
    def row(it, pad):
        st = ib.to_form(items.get(it['id'], it)).get('stage')
        print(f"{pad}{st:5} {it.get('t', '')}{'  〔這場版〕' if it.get('ov') else ''}  [{it['id']}]")
        pa = items.get(it.get('parent'))
        if pa and pad == '      ':
            print(f"{pad}      ↳ 追問自「{pa.get('t', '')}」")
        for k in ('cite', 'quote', 'why'):
            if (links.get(it['id']) or {}).get(k):
                print(f"{pad}      {k}: {links[it['id']][k]}")
    for name, groups in tree(bank, j['id']):
        print(f'  【{name}】')
        for g, rows in groups:
            print(f'    {g}')
            for top, kids in rows:
                row(top, '      ')
                for k in kids:
                    row(k, '        ↳ ')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('show'); p.add_argument('card', nargs='?')
    p = sub.add_parser('find'); p.add_argument('word')
    p = sub.add_parser('get'); p.add_argument('qid')
    p = sub.add_parser('check')
    p = sub.add_parser('cats')
    for name in ('put', 'link'):
        p = sub.add_parser(name)
        if name == 'put':
            p.add_argument('json', help='題目 json 檔(- 是標準輸入)')
            p.add_argument('--card')
        else:
            p.add_argument('card'); p.add_argument('qid')
        p.add_argument('--src', default='guess' if name == 'put' else None, required=name == 'link')
        p.add_argument('--cite'); p.add_argument('--quote'); p.add_argument('--why')
    p = sub.add_parser('unlink'); p.add_argument('card'); p.add_argument('qid')
    for name in ('share', 'unshare'):
        p = sub.add_parser(name); p.add_argument('qid')
    p = sub.add_parser('variant'); p.add_argument('card'); p.add_argument('qid')
    p.add_argument('--script-file'); p.add_argument('--focus-file'); p.add_argument('--clear', action='store_true')
    p = sub.add_parser('round'); p.add_argument('card')
    for k in ('--at', '--how', '--where', '--note', '--old-at', '--del'):
        p.add_argument(k)
    a = ap.parse_args()
    data, fb = _load()

    if a.cmd == 'show':
        return show(a, data, fb)
    if a.cmd == 'find':
        w = a.word.lower()
        for it in ib.normalize(data.get('bank'))['items']:
            f = ib.to_form(it)
            if w in ' '.join(str(v) for v in f.values()).lower():
                kl = ib.kids_line(ib.normalize(data.get('bank'))['items'], f['id'])
                print(f"{f['stage']:5} {f['cat']}/{f['sub']}  {f['t']}" + (f'  ({kl})' if kl else '') + f"  [{f['id']}]")
        return
    if a.cmd == 'cats':
        import collections
        n = collections.Counter((it.get('cat'), it.get('sub') or '') for it in ib.normalize(data.get('bank'))['items'] if not it.get('parent'))
        for (c, sb), k in sorted(n.items()):
            print(f"{c}{' · ' + sb if sb else ''}  {k}")
        return
    if a.cmd == 'get':
        print(json.dumps(ib.to_form(qref(a.qid, data)), ensure_ascii=False, indent=1))
        return
    if a.cmd == 'check':
        bad = ib.problems(data.get('bank'))
        nb = ib.normalize(data.get('bank'))
        for it in nb['items']:
            f = ib.to_form(it)
            if f['stage'] in ('wip', 'ok') and (m := memo_check(f['script'], f['t'])):
                bad.append(m)
            for job, qs in nb['links'].items():     # 這場版:備忘用的是它,不是共有稿
                ov = (qs.get(f['id']) or {}).get('ov')
                if f['stage'] in ('wip', 'ok') and ov and (m := memo_check(ib.blocks_to_script(ov.get('b')), f['t'])):
                    bad.append(f'{m}(這場版:{job})')
        print('\n'.join(bad) or '題庫沒有不合規則的地方')
        return sys.exit(1 if bad else 0)
    if a.cmd == 'put':
        form = json.load(sys.stdin if a.json == '-' else open(a.json, encoding='utf-8'))
        if form.get('stage') in ('wip', 'ok') and (m := memo_check(form.get('script'), form.get('t') or '這題')):
            raise SystemExit('沒寫進去:' + m)
        lf = link_form(a) if a.card else None
        job = cd.lookup(a.card, data)['id'] if a.card else None
        before = {it.get('id') for it in (data.get('bank') or {}).get('items') or []}
        bank = write('put', form)
        qid = form.get('id') or next(it['id'] for it in bank['items'] if it['id'] not in before)
        if job:
            bank = write('link', lf, qid, job, 'asked' if a.src == 'me' else 'prep')
        print(f"寫好了:「{form.get('t') or qref(qid, {'bank': bank})['t']}」[{qid}]" + (f'(記進 {job})' if job else ''))
        memo_show(form.get('script'))
        print('\n'.join(landing(bank, job, qid)))
        return
    if a.cmd in ('share', 'unshare'):
        it = qref(a.qid, data)
        write('share', None, it['id'], None, a.cmd == 'share')
        print(f"{'設成共有' if a.cmd == 'share' else '取消共有'}:「{it['t']}」")
        return
    job = cd.lookup(a.card, data)['id']
    if a.cmd == 'variant':
        it = qref(a.qid, data)
        if a.clear:
            write('variant', None, it['id'], job, None)
            print(f"還原成共有版:「{it['t']}」← {job}")
        else:
            if not a.script_file:
                raise SystemExit('要 --script-file 這場的逐字稿(或 --clear 還原成共有版)')
            read = lambda p: open(p, encoding='utf-8').read() if p else ''
            if ib.to_form(it)['stage'] in ('wip', 'ok') and (m := memo_check(read(a.script_file), it['t'])):
                raise SystemExit('沒寫進去:' + m)
            old = ib.effective(ib.normalize(data.get('bank')), job, it['id'])
            bank = write('variant', {'script': read(a.script_file), 'focus': read(a.focus_file) if a.focus_file else ib._plain(old.get('focus'))},
                  it['id'], job)
            print(f"這場版寫好了:「{it['t']}」→ {job}")
            memo_show(read(a.script_file))
            print('\n'.join(landing(bank, job, it['id'])))
        return
    if a.cmd == 'link':
        it = qref(a.qid, data)
        bank = write('link', link_form(a), it['id'], job, 'asked' if a.src == 'me' else 'prep')
        print(f"記好了:「{it['t']}」→ {job}({ib.SRCS[a.src]})")
        print('\n'.join(landing(bank, job, it['id'])))
    elif a.cmd == 'unlink':
        it = qref(a.qid, data, loose=True)
        write('link', None, it['id'], job, None)
        print(f"拿掉了:「{it['t']}」← {job}")
    elif a.cmd == 'round':
        if a.__dict__['del']:
            write('round', {'old_at': a.__dict__['del']}, None, job, None)
            print('刪掉那一輪了')
        else:
            if not a.at or not a.how:
                raise SystemExit('要 --at 時間和 --how 方式(' + '、'.join(ib.HOWS) + ')')
            write('round', {'at': a.at, 'how': a.how, 'where': a.where, 'note': a.note, 'old_at': a.old_at}, None, job, 'set')
            print(f'記好了:{job} {a.at} {a.how}')


if __name__ == '__main__':
    main()
