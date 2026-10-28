#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""面試備忘:把一場面試的題目和回答排成一份 PDF,面試時掃一眼就找得到。

  iv_sheet.py 海華            印出 <資料夾>/面試備忘/<公司>-<職稱>.pdf,回路徑
  iv_sheet.py 海華 --md       只印 Markdown(檢查內容用)

版面:第一頁是速查表(每題一行:題目、一句話、要點),接著「要問面試官」,完整回答放附錄。
附錄每題一塊:題目大字、一句話放在醒目的框裡、完整回答、追問縮在原題底下。內部 id 不印,場次資訊不印。
一句話、要點與完整回答的分界是逐字稿裡單獨一行的 ──────;分界前以【要點】開頭的一段是要點,其餘是一句話。
沒有分界的題,整份逐字稿就是回答。
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import interview_bank as ib

HERE = os.path.dirname(os.path.abspath(__file__))
STYLE = os.path.join(HERE, 'interview_sheet.css')
DIVIDER = '──────'
KEYS = '【要點】'
GLANCE = 90      # 沒有分界的題,逐字稿不超過這個字數才整份放進速查表


def _job(data, job_id):
    return next(j for j in data.get('jobs') or [] if isinstance(j, dict) and j.get('id') == job_id)


def _split(script):
    """逐字稿 → (一句話, 要點, 完整回答的 Markdown)。分界線之前的段落,【要點】開頭的是要點,其餘是一句話。
    小標 ## 降成粗體行,不跟題目標題搶層級。"""
    parts = re.split(r'\n\s*' + DIVIDER + r'\s*\n', '\n' + str(script or '').strip() + '\n', maxsplit=1)
    head, rest = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ('', parts[0].strip())
    paras = [p.strip() for p in re.split(r'\n\s*\n', head) if p.strip()]
    kw = ' '.join(p[len(KEYS):].strip() for p in paras if p.startswith(KEYS))
    one = '\n\n'.join(p for p in paras if not p.startswith(KEYS))
    flat = lambda t: re.sub(r'^## (.+)$', r'**\1**', t, flags=re.M)
    return flat(one), kw, flat(rest)


def _glance(oneline, kw, full):
    """速查表的一句話與要點兩格。都沒有的題,逐字稿夠短才整份放在一句話那格,否則請他翻附錄。"""
    cell = lambda t: re.sub(r'\*\*|\n', ' ', t or '').strip().replace('|', '/')
    points = '<br>'.join(x for x in re.split(r'\s*(?=[①-⑩])', cell(kw)) if x)  # 每個要點自己一行
    return cell(oneline or (full if len(full) <= GLANCE else '')) or '(見附錄)', points


def _question(it, level, kids_of):
    f = ib.to_form(it)
    one, kw, full = _split(f['script'])
    out = ['#' * level + ' ' + f['t'], '']
    if one:
        out += ['> ' + one.replace('\n', '\n> '), '']
    if full:
        out += [full, '']
    elif not one:
        out += ['(這題還沒有逐字稿)', '']
    if f['focus']:
        out += ['**對方在看**　' + f['focus'].replace('\n', ' '), '']
    for k in kids_of.get(f['id'], []):
        out += _question(k, level + 1, kids_of)
    return out


def build(data, job_id):
    """一場面試 → (Markdown, 檔名用的標題)。"""
    bank = ib.normalize(data.get('bank'))
    job = _job(data, job_id)
    ids = ib.job_qids(bank, job_id)                       # 這場自己勾的 + 共有的
    items = {q: ib.effective(bank, job_id, q) for q in ids}   # 有這場版的題,用這場的逐字稿和考點
    asked = set(ids)
    kids_of = {}
    for q in ids:
        p = items[q].get('parent')
        if p in asked:
            kids_of.setdefault(p, []).append(items[q])
    tops = [items[q] for q in ids if items[q].get('parent') not in asked]
    rank = {c: i for i, c in enumerate(bank['cats'])}
    tops.sort(key=lambda it: rank.get(it.get('cat'), len(rank)))
    title = f"{job.get('company') or ''} {job.get('title') or ''}".strip()

    is_ask = lambda it: '反問' in str(it.get('cat') or '')
    asks = [it for it in tops if is_ask(it)]
    tops = [it for it in tops if not is_ask(it)]

    md = ['| 題目 | 一句話 | 要點 |', '|---|---|---|']
    def walk(it, depth, out):
        out.append((it, depth))
        for k in kids_of.get(it['id'], []):
            walk(k, depth + 1, out)
    cat = None
    for it in tops:
        if it.get('cat') != cat:
            cat = it.get('cat')
            md.append(f'| **【{cat}】** | | |')
        flat = []
        walk(it, 0, flat)
        for q, depth in flat:
            f = ib.to_form(q)
            one, kw, full = _split(f['script'])
            g1, g2 = _glance(one, kw, full)
            md.append(f"| {'↳ ' * depth}{f['t'].replace('|', '/')} | {g1} | {g2} |")
    md.append('')
    if asks:
        md += ['## 要問面試官', '']
        for it in asks:
            one, kw, full = _split(ib.to_form(it)['script'])
            md.append('- ' + re.sub(r'\s+', ' ', one or full or it['t']))
        md.append('')
    md += ['## 附錄:完整回答', '']
    cat = None
    for it in tops:
        if it.get('cat') != cat:
            cat = it.get('cat')
            md += [f'### {cat}', '']
        md += _question(it, 4, kids_of)
    return '\n'.join(md).rstrip() + '\n', title


def render(data, job_id, home):
    """排成 PDF,回路徑。"""
    import markdown_pdf
    md, title = build(data, job_id)
    safe = re.sub(r'[\\/:*?"<>|\s]+', '-', title).strip('-') or 'interview'
    folder = os.path.join(home, '面試備忘')
    os.makedirs(folder, exist_ok=True)
    src = os.path.join(folder, f'{safe}-面試備忘.md')
    with open(src, 'w', encoding='utf-8') as fh:
        fh.write(md)
    pdf = src[:-3] + '.pdf'
    markdown_pdf.render(src, pdf, STYLE, 'zh')
    return pdf


def main():
    import board_doc as bd, config as cf, iv
    data = bd.load()['data']
    job = iv.card(sys.argv[1], data)['id'] if len(sys.argv) > 1 else sys.exit(__doc__)
    if '--md' in sys.argv:
        print(build(data, job)[0], end='')
    else:
        print(render(data, job, cf.HOME))


if __name__ == '__main__':
    main()
