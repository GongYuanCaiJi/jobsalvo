#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
interview_bank —— 「🎤 面試準備」那一頁在網頁上直接新增、編輯、刪除題目。

題庫存在看板資料的 bank(格式見 docs/interview-bank.md)。網頁送來的都是純文字,這裡轉成看板要的結構:
  題面、考點        純文字 → 跳脫後包成 <p>(不收任何 HTML)
  逐字稿            空一行 = 下一段;「## 」開頭的一行 = 小標;小標把逐字稿切成好幾塊(A/B 版、60/90 秒版)
  站                todo(還沒答)/ first(答過第一輪)/ wip(磨合中)/ ok(定案)
轉回來(給編輯框用)走同一套規則,編輯後再存不會變形。
"""
import re, html, time, os

DEFAULT_CATS = ['自我介紹', '你這個人', '職涯方向與動機', '你怎麼做事', '你做過的事', '待遇與條件', '反問面試官', '其他']
STAGES = ('todo', 'first', 'wip', 'ok')


def _plain(h):
    """看板存的 HTML → 編輯框的純文字。"""
    t = re.sub(r'<br\s*/?>', '\n', str(h or ''), flags=re.I)
    t = re.sub(r'</p\s*>', '\n\n', t, flags=re.I)
    return html.unescape(re.sub(r'<[^>]+>', '', t)).strip()


def _para_html(text):
    ps = [p.strip() for p in re.split(r'\n\s*\n', str(text or '')) if p.strip()]
    return ''.join('<p>' + html.escape(p).replace('\n', '<br>') + '</p>' for p in ps)


def script_to_blocks(text):
    """逐字稿純文字 → [['h', 小標 html], ['s', [段落…]], …]。"""
    blocks, paras = [], []
    def flush():
        if paras:
            blocks.append(['s', list(paras)]); paras.clear()
    for chunk in re.split(r'\n\s*\n', str(text or '').strip()):
        lines = chunk.strip().split('\n')
        rest = []
        for l in lines:
            if l.startswith('## '):
                if rest:
                    paras.append('\n'.join(rest)); rest = []
                flush(); blocks.append(['h', html.escape(l[3:].strip())])
            else:
                rest.append(l)
        if rest and '\n'.join(rest).strip():
            paras.append('\n'.join(rest).strip())
    flush()
    return blocks


def blocks_to_script(blocks):
    out = []
    for b in blocks or []:
        if b and b[0] == 'h':
            out.append('## ' + _plain(b[1]))
        elif b and b[0] == 's':
            out.extend(b[1])
    return '\n\n'.join(out)


def to_form(it):
    """看板裡的一題 → 編輯框的欄位。"""
    st = it.get('st')
    stage = 'ok' if st == 'ok' else 'wip' if st == 'wip' else ('first' if it.get('raw') else 'todo')
    return {'id': it.get('id', ''), 't': it.get('t', ''), 'cat': it.get('cat', ''), 'sub': it.get('sub', ''),
            'ask': _plain(it.get('ask')), 'focus': _plain(it.get('focus')), 'lim': it.get('lim') or 0,
            'stage': stage, 'asked': ', '.join(it.get('asked') or []), 'script': blocks_to_script(it.get('b'))}


def from_form(f, old=None):
    """編輯框送來的欄位 → 看板裡的一題(沒動到的欄位照舊,例如子題按鈕 picks)。"""
    it = dict(old or {})
    stage = f.get('stage') if f.get('stage') in STAGES else 'todo'
    it.update({
        'id': (old or {}).get('id') or f.get('id') or 'q' + str(int(time.time() * 1000)),
        't': str(f.get('t') or '').strip()[:300] or '(沒有題目)',
        'cat': str(f.get('cat') or '其他').strip()[:40],
        'sub': str(f.get('sub') or '').strip()[:60],
        'ask': _para_html(f.get('ask')),
        'focus': html.escape(str(f.get('focus') or '').strip()),
        'lim': max(0, min(3600, int(f.get('lim') or 0))),
        'st': {'ok': 'ok', 'wip': 'wip'}.get(stage, 'todo'),
        'raw': stage in ('first', 'wip', 'ok'),
        'asked': [a.strip() for a in re.split(r'[,，、]', str(f.get('asked') or '')) if a.strip()][:10],
        'b': script_to_blocks(f.get('script')),
    })
    it.setdefault('covers', '')
    it.setdefault('picks', [])
    if stage in ('wip', 'ok') and not any(b[0] == 's' for b in it['b']):
        it['st'] = 'todo'                       # 沒有逐字稿就不算磨合中/定案
    return it


def apply(bank, op, form=None, item_id=None):
    """op='put' 新增或改一題;op='del' 刪一題。回新的 bank。"""
    bank = dict(bank or {})
    bank.setdefault('cps', 4.2)
    bank['cats'] = list(bank.get('cats') or DEFAULT_CATS)
    items = list(bank.get('items') or [])
    if op == 'del':
        items = [it for it in items if it.get('id') != item_id]
    elif op == 'put':
        fid = (form or {}).get('id')
        idx = next((i for i, it in enumerate(items) if fid and it.get('id') == fid), None)
        it = from_form(form or {}, items[idx] if idx is not None else None)
        if idx is None:
            items.append(it)
        else:
            items[idx] = it
        if it['cat'] not in bank['cats']:
            bank['cats'].insert(max(0, len(bank['cats']) - 1), it['cat'])
    bank['items'] = items
    return bank


def export_markdown(bank):
    """Readable, one-way export of the canonical board bank."""
    bank = bank if isinstance(bank, dict) else {}
    cats = list(bank.get('cats') or DEFAULT_CATS)
    items = list(bank.get('items') or [])
    rank = {name: index for index, name in enumerate(cats)}
    items.sort(key=lambda item: (rank.get(item.get('cat'), len(rank)), str(item.get('t') or '').casefold(),
                                 str(item.get('id') or '')))
    stages = {'todo': '還沒答', 'first': '答過第一輪', 'wip': '磨合中', 'ok': '定案'}
    lines = ['# 面試題庫', '', '題庫資料以看板的 bank 為準；此檔是唯讀匯出，不會反向匯入。',
             '', '## 題目']
    for item in items:
        form = to_form(item)
        lines.extend(['', f'### {form["t"]}', '', f'- ID：{form["id"]}',
                      f'- 分類：{form["cat"]}', f'- 子題：{form["sub"] or "無"}',
                      f'- 狀態：{stages.get(form["stage"], form["stage"])}',
                      f'- 預計秒數：{form["lim"] or "未設定"}',
                      f'- 面試輪次：{form["asked"] or "未設定"}', '', '#### 題面',
                      form['ask'] or '（空白）', '', '#### 考點', form['focus'] or '（空白）',
                      '', '#### 回答草稿', form['script'] or '（空白）'])
    return '\n'.join(lines).rstrip() + '\n'


def export_file(bank, home):
    """Atomically refresh the Markdown export, preserving mtime when content is unchanged."""
    path = os.path.join(home, 'interview-bank.md')
    content = export_markdown(bank)
    if os.path.exists(path):
        with open(path, encoding='utf-8') as source:
            if source.read() == content:
                return path
    os.makedirs(home, exist_ok=True)
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as target:
        target.write(content)
    os.replace(temporary, path)
    return path
