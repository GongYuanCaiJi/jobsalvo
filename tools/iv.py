#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iv —— agent 寫「🎤 面試準備」的唯一入口。規則都在 interview_bank.apply,不合的直接擋下並說要放哪。

  iv.py show                         有在準備的每一場:什麼時候面、幾題、出處分佈
  iv.py show 萬蕙昇                  一場的面試時間和每一題(題目、出處、為什麼;行尾 [ ] 是內部 id)
  iv.py find 薪水                    題庫裡找題(先找,有了就 link,不要再寫一題一樣的)
  iv.py get  薪水 > q.json           拿一題現在的 json(含 rev);改一題一定先 get,改完再 put
                                     題目參數(get/link/unlink)寫 id 或題目文字的一段都行,對到唯一一題才動;
                                     對使用者說話一律講題目,不講 id(他在看板上看不到 id)
  iv.py put  題目.json [--card 萬蕙昇 --src guess --why "…"]
                                     新增或改一題(json 帶 id 就是改,要帶 get 拿到的 rev);帶 --card 就順便記進那一場
  iv.py link 萬蕙昇 WH3 --src this --cite "面試趣 2026-06-21 https://interview.tw/i/…" --quote "怎麼評估一個模型的好壞" [--why "…"]
  iv.py unlink 萬蕙昇 WH3
  iv.py share 自我介紹               設成共有:每一場面試自動帶上,改一次全部更新(追問跟著原題);iv.py unshare 取消
  iv.py variant 萬蕙昇 自我介紹 --script-file 稿.txt [--focus-file 考點.txt]
                                     只改這場的逐字稿(和考點),共有版不動,不要複製題目;--clear 還原成共有版
  iv.py round 萬蕙昇 --at 2026-10-23T15:00 --how 現場 --where "台北市…" [--note "…"] [--old-at 舊時間]
  iv.py round 萬蕙昇 --del 2026-10-23T15:00
  iv.py check                        整份題庫不合規則的地方

卡片可以寫網址、網址的一段或公司名,對到唯一一張就好。
題目 json 的欄位:t 題目、cat 大類、sub 小類、ask 題面(只放對方怎麼問)、focus 考點(含答壞的樣子)、
ref 準備材料、script 逐字稿(## 開頭是小標,只放同一個答案的段落或版本)、stage todo/first/wip/ok、lim 限時秒數、
parent 追問哪一題:對方聽完回答接著問的另一題,一律另寫一題帶 "parent": "原題 id",不寫進原題逐字稿。
  例:{"t": "曲線下面積是什麼？", "parent": "ML-1", "cat": "技術基本功", "sub": "機器學習", "stage": "wip", "script": "…"}
出處 --src:me 我被問過 / this 這家考過 / other 別家考過 / guess 預想的(要 --why)。
this、other 要 --cite 那一篇的網址(點了直接開那一篇,不是公司頁)和 --quote 原文(那一篇怎麼寫就貼怎麼寫)。
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_doc as bd, interview_bank as ib, config as cf


def _load():
    d = bd.load()
    return d['data'], (json.loads(d['fb']) if isinstance(d['fb'], str) else d['fb'])


def card(q, data):
    jobs = [j for j in data.get('jobs') or [] if isinstance(j, dict)]
    exact = [j for j in jobs if j.get('id') == q]
    hit = exact or [j for j in jobs if q in str(j.get('id')) or q in str(j.get('company') or '')]
    if len(hit) != 1:
        names = '、'.join(f"{j.get('company')} {j.get('title')} ({j.get('id')})" for j in hit[:6]) or '沒有'
        raise SystemExit(f'「{q}」要對到唯一一張卡,現在對到:{names}')
    return hit[0]


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
    return out['bank']


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
            j = card(jid, data)
            qs = bank['links'].get(jid, {})
            srcs = {s: sum(1 for q, v in qs.items() if v['src'] == s and q not in ib.shared_ids(bank)) for s in ib.SRCS}
            nsh = len(ib.shared_ids(bank))
            rs = bank['rounds'].get(jid, [])
            print(f"{j.get('company')} {j.get('title')}  [{(fb.get(jid) or {}).get('oc') or '—'}]  {jid}")
            print('   面試:' + ('; '.join(when(r) for r in rs) if rs else '還沒記時間'))
            print(f'   {len(ib.job_qids(bank, jid))} 題 · ' + ' · '.join(
                [f'共有 {nsh}'] * bool(nsh) + [f'{ib.SRCS[s]} {n}' for s, n in srcs.items() if n]))
        return
    j = card(a.card, data)
    print(f"{j.get('company')} {j.get('title')}  {j['id']}")
    for r in bank['rounds'].get(j['id'], []):
        print('  面試 ' + when(r) + (' · ' + r['note'] if r.get('note') else ''))
    qs = bank['links'].get(j['id'], {})
    sh = ib.shared_ids(bank)
    groups = [('共有', [(q, qs.get(q, {})) for q in sh])] + [
        (name, [(q, v) for q, v in qs.items() if v['src'] == s and q not in sh]) for s, name in ib.SRCS.items()]
    for name, rows in groups:
        if rows:
            print(f'  【{name}】')
        for q, v in rows:
            it = items.get(q, {})
            st = ib.to_form(it).get('stage') if it else '?'
            up = f"  ↳ 追問「{items.get(it['parent'], {}).get('t', it['parent'])}」" if it.get('parent') else ''
            up += f'  ({kl})' if (kl := ib.kids_line(bank['items'], q)) else ''
            print(f"    {st:5} {it.get('t', '(題庫裡沒有)')}{up}{'  〔這場版〕' if v.get('ov') else ''}  [{q}]")
            for k in ('cite', 'quote', 'why'):
                if v.get(k):
                    print(f'{"":10}{k}: {v[k]}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('show'); p.add_argument('card', nargs='?')
    p = sub.add_parser('find'); p.add_argument('word')
    p = sub.add_parser('get'); p.add_argument('qid')
    p = sub.add_parser('check')
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
    if a.cmd == 'get':
        print(json.dumps(ib.to_form(qref(a.qid, data)), ensure_ascii=False, indent=1))
        return
    if a.cmd == 'check':
        bad = ib.problems(data.get('bank'))
        print('\n'.join(bad) or '題庫沒有不合規則的地方')
        return sys.exit(1 if bad else 0)
    if a.cmd == 'put':
        form = json.load(sys.stdin if a.json == '-' else open(a.json, encoding='utf-8'))
        lf = link_form(a) if a.card else None
        job = card(a.card, data)['id'] if a.card else None
        before = {it.get('id') for it in (data.get('bank') or {}).get('items') or []}
        bank = write('put', form)
        qid = form.get('id') or next(it['id'] for it in bank['items'] if it['id'] not in before)
        if job:
            write('link', lf, qid, job, 'asked' if a.src == 'me' else 'prep')
        print(f"寫好了:「{form.get('t') or qref(qid, {'bank': bank})['t']}」[{qid}]" + (f'(記進 {job})' if job else ''))
        return
    if a.cmd in ('share', 'unshare'):
        it = qref(a.qid, data)
        write('share', None, it['id'], None, a.cmd == 'share')
        print(f"{'設成共有' if a.cmd == 'share' else '取消共有'}:「{it['t']}」")
        return
    job = card(a.card, data)['id']
    if a.cmd == 'variant':
        it = qref(a.qid, data)
        if a.clear:
            write('variant', None, it['id'], job, None)
            print(f"還原成共有版:「{it['t']}」← {job}")
        else:
            if not a.script_file:
                raise SystemExit('要 --script-file 這場的逐字稿(或 --clear 還原成共有版)')
            read = lambda p: open(p, encoding='utf-8').read() if p else ''
            old = ib.effective(ib.normalize(data.get('bank')), job, it['id'])
            write('variant', {'script': read(a.script_file), 'focus': read(a.focus_file) if a.focus_file else ib._plain(old.get('focus'))},
                  it['id'], job)
            print(f"這場版寫好了:「{it['t']}」→ {job}")
        return
    if a.cmd == 'link':
        it = qref(a.qid, data)
        write('link', link_form(a), it['id'], job, 'asked' if a.src == 'me' else 'prep')
        print(f"記好了:「{it['t']}」→ {job}({ib.SRCS[a.src]})")
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
