#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prefs —— 判斷一個職缺對不對味時,給 agent 看的「使用者本人的東西」。只挑,不改寫。

為什麼不整包給:偏好檔標過幾百張之後會有上百 KB,agent 讀不完;讀完了注意力也被稀釋(輸入越長,模型越容易
漏看中間)。為什麼不給摘要:摘要可能寫錯,而且錯了看不出來。
所以這裡做的是「選」:對每一個候選職缺,從他標過的卡裡挑出最相關的十幾張,連同他的原話原封不動
拿給 agent,再加上兩樣他自己的東西——硬規則原文、上傳履歷的文字。挑了哪幾張會留下編號,查得到。

挑法(全部機械、看得懂):
  同一間公司的卡先進(他對這間公司講過什麼最相關);
  其餘照「字面有多像」排(中文用兩字一組、英文用單字,BM25),
  喜歡類(喜歡/可努力/加進準備區)跟不喜歡類(不喜歡/還好)各取一半,判斷時看得到兩邊。
"""
import os, re, sys, json, math, collections, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd      # noqa: E402
import config as cf         # noqa: E402
import card                # noqa: E402

PREF = cf.PREFERENCE_NOTE
MARK = {'like': '喜歡', 'grow': '可努力', 'meh': '還好', 'dislike': '不喜歡'}

NOTE_CUSTOM = '## 使用者自訂'
NOTE_AGENT = '## Agent 假設'


def _note_section(text, heading):
    lines = text.splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        return None
    end = next((i for i in range(start, len(lines)) if lines[i].startswith('## ')), len(lines))
    return '\n'.join(lines[start:end]).strip('\n')


def _legacy_rules(text):
    lines = text.splitlines()
    heading = next((i for i, line in enumerate(lines)
                    if re.match(r'^##\s+硬規則.*$', line.strip())), None)
    if heading is None:
        return None
    start = heading + 1
    end = next((i for i in range(start, len(lines))
                if re.match(r'^##(?!#)\s+', lines[i]) or '<!-- 以下由' in lines[i]), len(lines))
    return '\n'.join(lines[start:end]).strip('\n')


def _render_note(custom, agent):
    custom = str(custom or '').strip('\n')
    agent = str(agent or '').strip('\n')
    return f'# 偏好筆記\n\n{NOTE_CUSTOM}\n\n{custom}\n\n{NOTE_AGENT}\n\n{agent}\n'


def _write_note(custom, agent, path=None):
    path = path or PREF
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(_render_note(custom, agent))
    os.replace(tmp, path)


def ensure_note(path=None, legacy_path=None):
    """第一次使用時把舊硬規則搬進使用者自訂區,保留逐張表態匯出。"""
    path = path or PREF
    try:
        with open(path, encoding='utf-8') as f:
            current = f.read()
    except OSError:
        current = ''
    custom = _note_section(current, NOTE_CUSTOM)
    agent = _note_section(current, NOTE_AGENT)
    if custom is not None and agent is not None:
        return current
    if custom is None:
        custom = _legacy_rules(current)
        if custom is None and current and NOTE_AGENT not in current:
            custom = current.strip('\n')
    if agent is None:
        agent = ''
    if not current:
        source = legacy_path or cf.PREFS
        if source != path:
            try:
                with open(source, encoding='utf-8') as f:
                    legacy = f.read()
            except OSError:
                legacy = ''
            migrated = _legacy_rules(legacy)
            if migrated is not None:
                custom = migrated
    _write_note(custom or '', agent or '', path)
    return _render_note(custom or '', agent or '')


def note_sections(path=None):
    text = ensure_note(path)
    return (_note_section(text, NOTE_CUSTOM) or '', _note_section(text, NOTE_AGENT) or '')





def save_custom_text(custom, path=None):
    old_custom, agent = note_sections(path)
    _write_note(custom, agent, path)


TRACKING = '### 新表態逐張核對'


def without_tracking(text):
    """拿掉「新表態逐張核對」那段:那是整理筆記的 agent 交件時給程式核對用的(每張新表態歸到哪條假設),
    核對完就沒用了。以前照樣存進筆記、每輪越積越長,判斷的 prompt 有一半以上是它(#76)。"""
    head, marker, rest = str(text or '').partition(TRACKING)
    if not marker:
        return text
    nxt = re.search(r'(?m)^## ', rest)
    return head.rstrip('\n') + ('\n\n' + rest[nxt.start():] if nxt else '\n')


def apply_agent_note(candidate, path=None, base=None):
    """接受 agent 的假設更新,但逐字保留使用者自訂區。核對用的那段不存。回:agent 有沒有動自訂區。
    base:交給 agent 的那一份筆記。整理跟找缺同時跑、可以跑很久,這段時間他在看板上改的以現在的為準:
    自訂區一律留現在的,跟交給它的那份比才知道 agent 有沒有動(以前跟現在的比,他自己加的也算成 agent 改的);
    他在這段時間刪掉(或改寫成自訂)的假設,agent 照舊版原樣寫回來的那幾行不要。"""
    path = path or PREF
    old_custom, old_agent = note_sections(path)
    new_custom = _note_section(candidate, NOTE_CUSTOM)
    new_agent = _note_section(candidate, NOTE_AGENT)
    if new_custom is None or new_agent is None:
        raise ValueError('偏好筆記缺少使用者自訂或 Agent 假設區')
    new_agent = without_tracking(new_agent).rstrip('\n')
    base_custom, base_agent = old_custom, old_agent
    if base is not None:
        base_custom = _note_section(base, NOTE_CUSTOM) or ''
        base_agent = _note_section(base, NOTE_AGENT) or ''
    dropped = {line for line in base_agent.splitlines() if line.strip()} - set(old_agent.splitlines())
    if dropped:
        new_agent = '\n'.join(line for line in new_agent.splitlines() if line not in dropped)
    _write_note(old_custom, new_agent, path)
    return new_custom != base_custom


def save_note_from_ui(custom, agent, path=None):
    """使用者改動過的 agent 假設提升成自訂;沒改的條目仍標成假設。"""
    old_custom, old_agent = note_sections(path)
    old_items = {line for line in old_agent.splitlines() if line.strip()}
    kept, promoted = [], []
    for line in str(agent or '').splitlines():
        if not line.strip():
            continue
        (kept if line in old_items else promoted).append(line)
    custom = str(custom or '').strip('\n')
    if promoted:
        custom = '\n'.join(x for x in (custom, '\n'.join(promoted)) if x)
    _write_note(custom, '\n'.join(kept), path)


def hard_rules():
    """舊呼叫仍讀得到使用者自訂區;研究與判斷使用整份偏好筆記。"""
    custom, _agent = note_sections()
    return custom or '(還沒寫硬規則)'


# 履歷檔裡可能有給建置用的東西:<style> 排版 CSS、照片 <img>、產檔用的 <!-- --> 標記。
# 那些不是「他是誰」,原封不動丟給 agent 等於拿版面 CSS 當判斷依據,佔 token 又沒用。
# 只拿掉標記本身,標記之間的正文照樣留著。
_BUILD_ONLY = re.compile(r'<style\b.*?</style>|<img\b[^>]*>|<!--.*?-->', re.S | re.I)


def resume():
    t = os.environ.get('JOBSALVO_RESUME_TEXT', '')
    if not t.strip():
        import settings_api
        t, problem = settings_api.resume_material()
        if not t:
            return '(讀不到上傳履歷文字:請把履歷內容貼上來)'
    return re.sub(r'\n{3,}', '\n\n', _BUILD_ONLY.sub('', t)).strip()


def checked_resumes(resumes=None):
    resumes = cf.RESUMES.values() if resumes is None else resumes
    return [item for item in resumes if item.get('enabled', True)]


def resume_choice_lines(resumes=None):
    items = checked_resumes(resumes)
    if not items:
        return '  (目前沒有已勾選的履歷)'
    lines = []
    for item in items:
        files = item.get('files') or {}
        choices = [f'{lang}: {cf.path(files[lang])}' for lang in cf.LANGS if files.get(lang)]
        when = item.get('when') or '(沒寫什麼時候用)'
        line = f'  {item.get("id")}  {item.get("name") or item.get("id")}: 什麼時候用:{when}'
        lines.append(line + '\n    ' + ('; '.join(choices) if choices else '(沒有語言檔)'))
    return '\n'.join(lines)


def resume_selection_signature(resumes=None):
    items = checked_resumes(resumes)
    snapshot = []
    for item in items:
        files = item.get('files') or {}
        hashes = {}
        for lang in sorted(set(cf.LANGS)):
            ref = files.get(lang)
            digest = None
            if ref:
                try:
                    with open(cf.path(ref), 'rb') as f:
                        digest = hashlib.file_digest(f, 'sha256').hexdigest()
                except OSError:
                    pass
            hashes[lang] = {'configured': bool(ref), 'sha256': digest}
        snapshot.append({
            'id': item.get('id', ''),
            'name': item.get('name', ''),
            'when': item.get('when', ''),
            'files': hashes,
        })
    value = {'languages': sorted(set(cf.LANGS)), 'resumes': snapshot}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def feedback_signature(value):
    return hashlib.sha256(str(value or '').strip().encode('utf-8')).hexdigest()


def valid_resume_pick(resume_id, lang, resumes=None):
    if not isinstance(lang, str) or lang not in cf.LANGS:
        return False
    item = next((r for r in checked_resumes(resumes) if r.get('id') == resume_id), None)
    path = (item.get('files') or {}).get(lang) if item else None
    return bool(path)


# 卡上「原因」那格有些被 agent 接著寫了投遞紀錄(表單怎麼填、送出頁長怎樣),甚至整格都是。
# 那不是他的反應。從紀錄開頭的固定字樣切掉,只留前面他的話;只切不改,切錯頂多少一句。
AGENT_LOG=re.compile(r'送出頁|表單送出|頁面顯示|(?:Lever|Greenhouse|Ashby|104) ?表單|投的時候|Apply with LinkedIn|'
                     r'有連 LinkedIn|連結欄|之前被標已投遞|20\d\d-\d\d 投的|實際送出的是')

def his_words(note):
    note = (note or '').strip()
    m = AGENT_LOG.search(note)
    return (note[:m.start()] if m else note).rstrip(' 　,，。;；:：\n')


# 板上的標題有「職稱·公司」也有「公司 · 職稱」;哪一段像職稱,另一段就是公司。跟看板分組同一份清單(card.TITLE_WORDS + 設定)。
TITLE_KW = card.titleish((cf.C.get('board') or {}).get('title_words'))


def company_of(j):
    """卡片歸在哪一家:跟看板分組同一條規則(card.company,套設定的公司別名);認不出來回空字串。"""
    board = cf.C.get('board') or {}
    co = card.company(j, board.get('company_alias') or {}, board.get('title_words'))
    return '' if co == '其他' else co


def company_segment(j):
    """卡名裡寫公司的那一段(原字,不正規化):找「同一個缺換了網址」要拿它回去對卡名。"""
    t = card.name(j)
    segs = [x.strip() for x in re.split(r'[·・]', t) if x.strip()]
    if len(segs) < 2:
        return ''
    if TITLE_KW.search(segs[-1]) and not TITLE_KW.search(segs[0]):
        return segs[0][:40]          # 公司·職稱
    return segs[-1][:40]             # 職稱·公司


def cards(fb, jobs):
    """他表過態的卡:喜歡/可努力/還好/不喜歡,或加進了準備區以後的階段。技術錯誤、移除的不算表態。"""
    out = []
    for j in jobs:
        v = fb.get(j['id'])
        if not isinstance(v, dict) or v.get('rm'):
            continue
        s = v.get('s')
        if s == 'techerr':
            continue
        pos = bool(v.get('app')) or s in ('like', 'grow')
        neg = s in ('meh', 'dislike') and not v.get('app')
        if not (pos or neg):
            continue
        mark = MARK.get(s) or ''
        if v.get('app'):
            mark = (mark + '、' if mark else '') + '加進準備區'
        note = his_words(v.get('n'))
        summary = j.get('sum') if isinstance(j.get('sum'), dict) else {}
        src = j.get('src') if isinstance(j.get('src'), dict) else {}
        out.append({'id': j['id'], 'title': card.name(j), 'company': company_of(j), 'cat': j.get('cat') or '',
                    'mark': mark, 'pos': pos, 'note': note, 'summary': summary,
                    'angle': src.get('angle') or '',
                    'match': ' '.join([card.name(j), j.get('cat') or '', note,
                                       json.dumps(summary, ensure_ascii=False)[:1500]])})
    return out


def toks(text):
    text = (text or '').lower()
    out = re.findall(r'[a-z][a-z0-9+#.]{1,}', text)
    han = re.findall(r'[一-鿿]+', text)
    for h in han:
        out += [h[i:i + 2] for i in range(len(h) - 1)] or [h]
    return out


class Index:
    """BM25。標準參數(k1=1.5, b=0.75)。"""

    def __init__(self, docs):
        self.docs = [collections.Counter(toks(d)) for d in docs]
        self.len = [sum(c.values()) for c in self.docs]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1
        df = collections.Counter()
        for c in self.docs:
            df.update(c.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + .5) / (f + .5)) for t, f in df.items()}

    def score(self, query):
        q = collections.Counter(toks(query))
        res = []
        for c, L in zip(self.docs, self.len):
            s = 0.0
            for t in q:
                f = c.get(t)
                if f:
                    s += self.idf[t] * f * 2.5 / (f + 1.5 * (1 - .75 + .75 * L / self.avg))
            res.append(s)
        return res


def neighbors(cand_title, cand_text, cand_company, cs, k=12, exclude=()):
    """回 [(編號, 卡)]。編號是 c<序號>,判斷時拿來引用。"""
    pool = [(i, c) for i, c in enumerate(cs) if c['id'] not in exclude]
    if not pool:
        return []
    idx = Index([c['match'] for _, c in pool])
    sc = idx.score(' '.join([cand_title] * 3) + ' ' + (cand_text or '')[:2500])   # 職稱加重
    ranked = sorted(range(len(pool)), key=lambda i: -sc[i])
    same = [pool[i] for i in ranked if cand_company and pool[i][1]['company']
            and cand_company.lower() in pool[i][1]['company'].lower()][:4]
    chosen = list(same)
    half = max(1, (k - len(chosen)) // 2)
    for want in (True, False):
        got = [p for p in (pool[i] for i in ranked) if p[1]['pos'] == want and p not in chosen][:half]
        chosen += got
    return [(f'c{i}', c) for i, c in chosen[:k]]


def render(nb):
    """挑出來的卡,一張一行:編號、表態、職稱·公司、公司內容、門檻與原話。"""
    lines = []
    for cid, c in nb:
        summary = c.get('summary') or {}
        company = summary.get('co') or '查無'
        threshold = summary.get('bar') or '查無'
        line = f"[{cid}] {c['mark']}｜{c['title']}｜公司在做什麼：{company}｜門檻：{threshold}"
        if c['note']:
            line += f"｜他說「{c['note']}」"
        lines.append(line)
    return '\n'.join(lines)


def like_scores(fb, jobs):
    """每張職缺「像他喜歡過的程度 − 像他不喜歡過的程度」,換成 0-100 的百分位。不用 agent,算一次不到一秒。
    比的是職稱(去掉公司名)加卡片摘要。拿他表過態的 145 張驗過(每次拿掉一張當成沒看過):
    AUC 0.82,只比職稱是 0.63。看板「最可能喜歡」那個排法,沒被判斷過的缺照這個排。"""
    byid = {j['id']: j for j in jobs}
    cs = cards(fb, jobs)

    def text(j):
        title, company = card.legacy_score_name_parts(j, TITLE_KW)
        return _role_text(title, company) + ' ' + json.dumps(j.get('sum') or {}, ensure_ascii=False)[:800]
    P = [text(byid[c['id']]) for c in cs if c['pos'] and c['id'] in byid]
    N = [text(byid[c['id']]) for c in cs if not c['pos'] and c['id'] in byid]
    if not P or not N:
        return {}
    ip, inn = Index(P), Index(N)
    raw = {}
    for j in jobs:
        q = text(j)
        raw[j['id']] = sum(sorted(ip.score(q), reverse=True)[:3]) - sum(sorted(inn.score(q), reverse=True)[:3])
    order = sorted(raw, key=lambda k: raw[k])
    return {k: round(100 * i / max(1, len(order) - 1)) for i, k in enumerate(order)}


def _role_text(title, company):
    t = title or ''
    for w in re.split(r'[\s_·・/()（）\-]+', company or ''):
        if len(w) >= 2:
            t = re.sub(re.escape(w), ' ', t, flags=re.I)
    return t


def refresh_like(live=None):
    """把 like_scores 寫進看板資料(j.like)。找缺每輪收尾、reconcile 都會跑。"""
    def mut(data, fb):
        sc = like_scores(fb, data['jobs'])
        for j in data['jobs']:
            if j['id'] in sc:
                j['like'] = sc[j['id']]
    bd.set_data(mut, live=live or bd.LIVE)


def load(live=None):
    with open(live or bd.LIVE, encoding='utf-8') as f: p = bd.parse(f.read())
    return json.loads(p['fb']), p['data']['jobs']


if __name__ == '__main__':
    # 試看看:python3 prefs.py <職缺網址>  印出會拿給 agent 看的那幾張
    fb, jobs = load()
    cs = cards(fb, jobs)
    byid = {j['id']: j for j in jobs}
    for u in sys.argv[1:]:
        j = byid.get(u, {'target': u})
        print(render(neighbors(card.name(j), json.dumps(j.get('sum') or {}, ensure_ascii=False),
                               company_of(j), cs, exclude={u})))
