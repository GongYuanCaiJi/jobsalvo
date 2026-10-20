#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
interview_bank —— 「🎤 面試準備」的資料:題目、哪一場用哪一題(和出處)、每一場什麼時候面。

存在看板資料的 bank(格式見 docs/interview-bank.md),分三層,各放各的東西:
  items   題目。只放題目本身:題面(對方怎麼問)、考點、準備材料、答案、走到哪一站。不寫哪一家、不寫出處。
  links   {卡片 id: {題目 id: {k, src, cite, why}}}。這場要準備還是被問過、這題從哪來(出處)、為什麼這場要準備。
  rounds  {卡片 id: [{at, how, where, note}]}。這場面試排在什麼時候、怎麼面、在哪。
看板網頁和 tools/iv.py 都只走這裡的 apply(),規則只寫在這一處:不合規則的寫入直接擋下並說要放哪。
網頁送來的都是純文字,這裡轉成看板要的結構:
  題面、考點、準備材料  純文字 → 跳脫後包成 <p>(不收任何 HTML;連結寫 [文字](網址),看板顯示時才變成連結)
  逐字稿                空一行 = 下一段;「## 」開頭的一行 = 小標;小標把逐字稿切成好幾塊(A/B 版、60/90 秒版)
  站                    todo(還沒答)/ first(答過第一輪)/ wip(磨合中)/ ok(定案)
轉回來(給編輯框用)走同一套規則,編輯後再存不會變形。
"""
import re, html, time, os

DEFAULT_CATS = ['自我介紹', '你這個人', '職涯方向與動機', '你怎麼做事', '你做過的事', '技術基本功', '待遇與條件', '反問面試官', '其他']
STAGES = ('todo', 'first', 'wip', 'ok')
KINDS = ('prep', 'asked')     # 這場要準備 / 這場被問過
# 出處:這題為什麼會出現在這場。順序就是面試頁上分區的順序(越可靠越前面)
SRCS = {
    'me': '我被問過',          # 我在這場被問到(k 一定是 asked)
    'this': '這家考過',        # 查到別人面這家/這個職缺被問過(cite 必填)
    'other': '別家考過',       # 查到別人面別家同類職缺被問過(cite 必填)
    'guess': '預想的',         # 沒查到出處:從職缺內容推的、常見題
}
HOWS = ('現場', '電話', '視訊', 'AI 錄影', '筆試', '其他')
# 題面只放對方怎麼問。出處、教練筆記、日期放錯地方時擋下來,說該放哪
_ASK_META = re.compile(r'答壞的樣子|實際考過|押中|沒有人回報|Claude|查無|出處|20\d\d-\d\d-\d\d')
_AT = re.compile(r'^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$')


def _plain(h):
    """看板存的 HTML → 編輯框的純文字。連結寫成 [文字](網址),存回去還是純文字,看板顯示時才變成連結。"""
    t = re.sub(r'<a\s[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', str(h or ''), flags=re.I | re.S)
    t = re.sub(r'<br\s*/?>', '\n', t, flags=re.I)
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
            'ask': _plain(it.get('ask')), 'focus': _plain(it.get('focus')), 'ref': _plain(it.get('ref')),
            'lim': it.get('lim') or 0, 'parent': it.get('parent') or '',
            'stage': stage, 'script': blocks_to_script(it.get('b'))}


def from_form(f, old=None):
    """編輯框送來的欄位 → 看板裡的一題(沒動到的欄位照舊,例如子題按鈕 picks)。"""
    it = dict(old or {})
    stage = f.get('stage') if f.get('stage') in STAGES else 'todo'
    ask = str(f.get('ask') or '')
    hit = _ASK_META.search(ask)
    if hit:
        raise ValueError(f'題面只放對方怎麼問;「{hit.group(0)}」這類出處、日期或答題提醒要放考點或這場的出處')
    it.update({
        'id': (old or {}).get('id') or f.get('id') or 'q' + str(int(time.time() * 1000)),
        't': str(f.get('t') or '').strip()[:300] or '(沒有題目)',
        'cat': str(f.get('cat') or '其他').strip()[:40],
        'sub': str(f.get('sub') or '').strip()[:60],
        'ask': _para_html(ask),
        'focus': _para_html(f.get('focus')),
        'ref': _para_html(f.get('ref')),
        'lim': max(0, min(3600, int(f.get('lim') or 0))),
        'st': {'ok': 'ok', 'wip': 'wip'}.get(stage, 'todo'),
        'raw': stage in ('first', 'wip', 'ok'),
        'b': script_to_blocks(f.get('script')),
    })
    if 'parent' in f:
        it['parent'] = str(f.get('parent') or '').strip()
    if not it.get('parent'):
        it.pop('parent', None)
    it.pop('covers', None)
    it.setdefault('picks', [])
    if stage in ('wip', 'ok') and not any(b[0] == 's' for b in it['b']):
        it['st'] = 'todo'                       # 沒有逐字稿就不算磨合中/定案
    return it


def norm_link(v):
    """一筆關聯的現行樣子 {k, src, cite, why}。舊資料('prep'/'asked' 字串、note)照這裡補齊。"""
    v = {'k': v} if isinstance(v, str) else dict(v or {})
    if 'note' in v:
        v.setdefault('why', v.pop('note'))
    k = v.get('k') if v.get('k') in KINDS else 'prep'
    src = v.get('src') if v.get('src') in SRCS else ('me' if k == 'asked' else 'guess')
    return {'k': k, 'src': src, 'cite': str(v.get('cite') or '').strip(), 'why': str(v.get('why') or '').strip()}


def check_link(v):
    """一筆關聯合不合規則;不合就 ValueError 說哪裡不對。"""
    if v['k'] not in KINDS:
        raise ValueError('只能是要準備或被問過')
    if v['src'] not in SRCS:
        raise ValueError('出處只能是:' + '、'.join(SRCS.values()))
    if (v['k'] == 'asked') != (v['src'] == 'me'):
        raise ValueError('「被問過」就是「我被問過」;別人被問過的記成要準備,出處選這家考過或別家考過')
    if v['src'] in ('this', 'other') and not v['cite']:
        raise ValueError('查到的考題要附出處(網址,或哪個網站哪一天哪一篇)')
    return v


def norm_round(r):
    r = dict(r or {})
    at = str(r.get('at') or '').strip().replace(' ', 'T')[:16]
    if not _AT.match(at):
        raise ValueError('面試時間寫成 2026-10-23 或 2026-10-23T15:00')
    how = str(r.get('how') or '').strip()
    if how not in HOWS:
        raise ValueError('面試方式只能是:' + '、'.join(HOWS))
    return {'at': at, 'how': how, 'where': str(r.get('where') or '').strip()[:200],
            'note': str(r.get('note') or '').strip()[:300]}


def normalize(bank):
    """整份 bank 照現行格式補齊(舊關聯、舊欄位)。讀和寫都先過這一關。"""
    bank = dict(bank or {})
    bank.setdefault('cps', 4.2)
    bank['cats'] = list(bank.get('cats') or DEFAULT_CATS)
    bank['items'] = list(bank.get('items') or [])
    bank['links'] = {job: {q: norm_link(v) for q, v in (qs or {}).items()}
                     for job, qs in (bank.get('links') or {}).items() if qs}
    bank['rounds'] = {job: sorted((dict(r) for r in rs or []), key=lambda r: r.get('at', ''))
                      for job, rs in (bank.get('rounds') or {}).items() if rs}
    return bank


def apply(bank, op, form=None, item_id=None, job=None, kind='prep'):
    """改 bank,回新的 bank;不合規則就 ValueError。
    op='put'    新增或改一題(form = 編輯框欄位)
    op='del'    刪一題(也從每一場拿掉,也拿掉別題對它的「追問」)
    op='stage'  只改走到哪一站(kind = todo/first/wip/ok)
    op='link'   把一題記進某一場(job = 卡片 id):kind='prep'/'asked',form = {src, cite, why};kind=None 從那一場拿掉
    op='round'  某一場的一輪面試:form = {at, how, where, note, old_at?};kind=None 刪掉 old_at(或 at)那一輪"""
    bank = normalize(bank)
    items, links, rounds = bank['items'], bank['links'], bank['rounds']
    if op == 'del':
        items = [it for it in items if it.get('id') != item_id]
        for it in items:
            if it.get('parent') == item_id:
                it.pop('parent', None)
        for v in links.values():
            v.pop(item_id, None)
    elif op == 'stage':
        # 只改走到哪一站(看板上點那顆狀態就能改),不碰題目和答案;規則跟編輯框同一套(沒有逐字稿不能算磨合中/定案)
        if kind not in STAGES:
            raise ValueError('不知道是哪一站')
        for i, it in enumerate(items):
            if it.get('id') == item_id:
                if kind in ('wip', 'ok') and not any(b[0] == 's' for b in it.get('b') or []):
                    raise ValueError('這題還沒有逐字稿,先寫了才能算磨合中或定案')
                items[i] = dict(it, st={'ok': 'ok', 'wip': 'wip'}.get(kind, 'todo'), raw=kind != 'todo')
                break
        else:
            raise ValueError('找不到這一題')
    elif op == 'link':
        if not job:
            raise ValueError('沒說是哪一場面試')
        if not any(it.get('id') == item_id for it in items):
            raise ValueError('找不到這一題')
        if kind is None:
            links.get(job, {}).pop(item_id, None)
        elif kind in KINDS:
            old = links.get(job, {}).get(item_id) or {}
            f = dict(form or {})
            src = f.get('src') or ('me' if kind == 'asked' else old.get('src') if old.get('src') in ('this', 'other', 'guess') else 'guess')
            v = {'k': kind, 'src': src,
                 'cite': str(f['cite'] if 'cite' in f else old.get('cite') or '').strip(),
                 'why': str(f['why'] if 'why' in f else old.get('why') or '').strip()}
            links.setdefault(job, {})[item_id] = check_link(v)
        else:
            raise ValueError('只能是要準備、被問過或拿掉')
    elif op == 'round':
        if not job:
            raise ValueError('沒說是哪一場面試')
        f = dict(form or {})
        old_at = str(f.get('old_at') or f.get('at') or '').replace(' ', 'T')[:16]
        keep = [r for r in rounds.get(job, []) if r.get('at') != old_at]
        if kind is not None:
            r = norm_round(f)
            keep = [x for x in keep if x.get('at') != r['at']] + [r]
        rounds[job] = sorted(keep, key=lambda r: r['at'])
    elif op == 'put':
        f = dict(form or {})
        fid = f.get('id')
        idx = next((i for i, it in enumerate(items) if fid and it.get('id') == fid), None)
        it = from_form(f, items[idx] if idx is not None else None)
        if heading_problem(it, idx is not None):
            raise ValueError(heading_problem(it, idx is not None))
        if it.get('parent'):
            pa = next((x for x in items if x.get('id') == it['parent'] and x.get('id') != it['id']), None)
            if not pa:
                raise ValueError('追問要掛在題庫裡另一題底下')
            if pa.get('parent'):
                raise ValueError('追問只有一層:「' + pa.get('t', '') + '」本身是追問,掛到它的原題底下')
            if any(x.get('parent') == it['id'] for x in items):
                raise ValueError('這題底下有追問,不能再當別題的追問')
        if idx is None:
            items.append(it)
        else:
            items[idx] = it
        if it['cat'] not in bank['cats']:
            bank['cats'].insert(max(0, len(bank['cats']) - 1), it['cat'])
    else:
        raise ValueError('不知道要做什麼')
    bank['items'] = items
    bank['links'] = {k: v for k, v in links.items() if v}
    bank['rounds'] = {k: v for k, v in rounds.items() if v}
    return bank


def heading_problem(it, saved=True):
    """逐字稿的小標只放同一個答案的段落或版本:寫了「追問」就是把另一題塞進來,有 HTML 就是舊資料沒清。"""
    for blk in it.get('b') or []:
        if blk and blk[0] == 'h':
            h = str(blk[1])
            if re.match(r'^[^（(]*追問', _plain(h)):   # 「可能的追問」;「沒 context 版（…不能賭他會追問）」不算
                pa = f'"parent": "{it.get("id")}"' if saved else '"parent" 填這一題存好後的 id(iv.py put 會印出來)'
                return f'「{it.get("t")}」的逐字稿有小標「{_plain(h)}」:那段拿掉,追問一題一題另寫,題目 json 帶 {pa}'
            if re.search(r'<[a-z/][^>]*>', h):
                return f'「{it.get("t")}」的小標「{_plain(h)}」裡有 HTML,打開編輯再存一次就會清掉'
    return ''


def problems(bank):
    """整份 bank 不合規則的地方(給 board_check 和 iv.py check 用)。回 [說明…]。"""
    bank = bank if isinstance(bank, dict) else {}
    out, ids = [], {it.get('id') for it in bank.get('items') or []}
    for it in bank.get('items') or []:
        hit = _ASK_META.search(_plain(it.get('ask')))
        if hit:
            out.append(f'「{it.get("t")}」的題面有「{hit.group(0)}」,要搬到考點或這場的出處')
        if it.get('covers'):
            out.append(f'「{it.get("t")}」還有舊的 covers 欄位')
        if heading_problem(it):
            out.append(heading_problem(it))
        if it.get('parent') and it['parent'] not in ids:
            out.append(f'「{it.get("t")}」追問的那一題不在題庫裡')
    for job, qs in (bank.get('links') or {}).items():
        for q, v in (qs or {}).items():
            if q not in ids:
                out.append(f'{job} 掛了一題不在題庫裡的 {q}')
                continue
            if not isinstance(v, dict) or 'src' not in v:
                out.append(f'{job} 的 {q} 沒有記出處')
                continue
            try:
                check_link(norm_link(v))
            except ValueError as e:
                out.append(f'{job} 的 {q}:{e}')
    for job, rs in (bank.get('rounds') or {}).items():
        for r in rs or []:
            try:
                norm_round(r)
            except ValueError as e:
                out.append(f'{job} 的面試時間:{e}')
    return out


def export_markdown(bank):
    """Readable, one-way export of the canonical board bank."""
    bank = normalize(bank if isinstance(bank, dict) else {})
    cats = bank['cats']
    items = list(bank['items'])
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
                      f'- 預計秒數：{form["lim"] or "未設定"}', '', '#### 題面',
                      form['ask'] or '（空白）', '', '#### 考點', form['focus'] or '（空白）',
                      '', '#### 準備材料', form['ref'] or '（空白）',
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


def selftest():
    """規則的最小檢查:擋得住該擋的、存得進該存的。board_check 每次都跑。"""
    b = apply({}, 'put', {'t': '自我介紹', 'cat': '自我介紹', 'script': '我是誰'})
    q = b['items'][0]['id']
    for bad in ({'k': 'asked', 'src': 'this', 'cite': 'x'}, {'k': 'prep', 'src': 'other'}):
        try:
            check_link(dict({'cite': '', 'why': ''}, **bad)); raise SystemExit('沒擋到 ' + str(bad))
        except ValueError:
            pass
    try:
        apply(b, 'put', {'t': 'x', 'ask': '2026-08-28 實際考過'}); raise SystemExit('題面沒擋')
    except ValueError:
        pass
    assert apply(b, 'link', None, q, 'K')['links']['K'][q]['src'] == 'guess'   # 從題庫勾題:沒給出處就是預想的
    b = apply(b, 'link', {'src': 'this', 'cite': 'https://x'}, q, 'J')
    assert b['links']['J'][q] == {'k': 'prep', 'src': 'this', 'cite': 'https://x', 'why': ''}
    b = apply(b, 'link', None, q, 'J', 'asked')
    assert b['links']['J'][q]['src'] == 'me'
    b = apply(b, 'round', {'at': '2026-10-23 15:00', 'how': '現場', 'where': '台北'}, None, 'J')
    b = apply(b, 'round', {'at': '2026-10-21T14:30', 'how': '電話'}, None, 'J')
    assert [r['at'] for r in b['rounds']['J']] == ['2026-10-21T14:30', '2026-10-23T15:00']
    b = apply(b, 'round', {'old_at': '2026-10-21T14:30'}, None, 'J', None)
    assert len(b['rounds']['J']) == 1
    assert norm_link('asked') == {'k': 'asked', 'src': 'me', 'cite': '', 'why': ''}
    kid = 'kid'
    b = apply(b, 'put', {'id': kid, 't': '追問', 'parent': q})
    for bad in ({'t': '追問的追問', 'parent': kid}, {'id': q, 't': '自我介紹', 'parent': kid}):
        try:
            apply(b, 'put', bad); raise SystemExit('追問超過一層沒擋 ' + str(bad))
        except ValueError:
            pass
    try:
        apply(b, 'put', {'t': 'x', 'script': '答案\n\n## 可能的追問\n\n問?答'}); raise SystemExit('追問寫進逐字稿沒擋')
    except ValueError:
        pass
    assert problems({'items': [{'id': 'h', 't': 'h', 'b': [['h', '<h6>優點</h6>']]}]}), '小標的 HTML 沒抓到'
    assert not problems(b), problems(b)


if __name__ == '__main__':
    selftest()
    print('ok')
