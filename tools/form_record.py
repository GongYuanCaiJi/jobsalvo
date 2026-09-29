#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
form_record —— agent 填雇主表單時,把每一欄記進看板的唯一入口。

答案只有一個真相:表單答案庫(__ans__)。表單上的欄位只是指標,agent 只看答案庫一個地方。
  · src='rz'   履歷直接對上的(姓名、Email、履歷檔…):v 留在表單上。它的真相是履歷,不進答案庫。
  · src='skip' 刻意不填的:留在表單上,why 寫為什麼。
  · 其他全部是答案(src 給 'bank' 或 'new' 都行):值、依據(why)、中文翻譯(zh)、題型(kind)
    放答案庫那一條,表單上只留 {q, src:'bank', k, opt?, lim?, refill?}。

記的時候:
  1. 給了 k:答案庫有那條就指過去。這次給的值跟庫裡不一樣就擋下來(要嘛不給值、直接用庫裡的,
     要嘛換一個 k 開新的一條),不能悄悄蓋掉他確認過的答案。庫裡沒有就用這次的值開一條。
  2. 沒給 k:同一題(問法大小寫、標點不計)而且同一個值的「共用」答案,接到那一條;值不一樣就開新的一條,
     在看板上跟同一題的其他答案排在一起。這張表單上次同一題指的那條如果只有這張在用,直接改那條。
  2.5 每條答案分「共用」和「這缺專用」(pj=1)。例如「為什麼我適合這個職位」很明顯是針對特定 JD 的。
     agent 先判斷,最後由使用者決定。記的時候 agent 判斷(pj、pjw 寫理由;沒給就照題型猜,
     短文與意見算這缺專用),他在看板上按一下切換。這缺專用的不會被自動接到別的職缺,看板上連到那個 JD。
     填新表單前先讀 --shared(他確認過的共用答案)。
  3. 新開或被我改過值的一條標「我推論的」(inf),他在看板上確認一次才算數。他在對話裡親口講過的
     才給 his=True(直接算確認)。
  4. 已投遞(鎖住)的表單不能改,那是送出時的紀錄。

送出的答案(v)跟他看得懂的語言(設定 resume.read_lang,預設中文)不是同一套文字,就一定附他看得懂的那份(zh 欄位,
名字沿用舊資料):看板上他看、他改的是那一份,v 是送出用的。中文使用者就是「英文答案一定附中文」。validate 會擋沒翻譯的英文答案。他在看板上改了中文,那條標 tr:代投的 agent 在填表或修改那一輪照中文重翻英文(apply_run 把這幾條放進 prompt),
一律用 translate(k, en=...) 記回來,不要手改。

看板上他改了某條答案,還沒送出的表單裡用到它的欄位會標 refill:雇主網頁上還是舊字。
送出前先把標 tr 的翻掉,再照答案庫重打網頁,用 `--clear-refill` 清掉。

用法(agent 呼叫):
  uv run python tools/form_record.py --from-fill <out>/fill.json --url URL --board B
                                   # 代投:agent 只寫一份 fill.json,從它的 fields 記進看板(見 fields_from_fill)
  import form_record as fr
  fr.record(url, plat, fields)     # 寫入/覆蓋這張卡的 form;回傳這次新開的 k(英文答案要給 zh)
  fr.translate(k, en=..., zh=...)  # 照他改的中文重翻英文 / 替英文答案補中文
CLI:
  uv run python tools/form_record.py --shared               # 填新表單前先讀:他確認過的共用答案
  uv run python tools/form_record.py --pending              # 答案庫裡等他確認的(連同哪幾張表單在用)
  uv run python tools/form_record.py --refills              # 送出前:英文待重翻的、雇主網頁還沒重打的
  uv run python tools/form_record.py --clear-refill URL [--q 片段]   # 我重打完網頁後清掉標記
  uv run python tools/form_record.py --check                # 檢查「答案只在答案庫」這條有沒有被破壞
"""
import os, sys, json, re, datetime, argparse, unicodedata, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd

SRCS = ('rz', 'bank', 'skip')
KINDS = ('txt', 'op', 'pick', 'val', 'ck')
BANK_FIELD = ('q', 'src', 'k', 'opt', 'lim', 'refill')     # 表單上指向答案庫的欄位只能有這些
PLAIN_FIELD = ('q', 'src', 'v', 'why', 'opt', 'lim')        # rz / skip 留在表單上的


def _today():
    return datetime.date.today().isoformat()


def _bank(fb):
    return fb.setdefault('__ans__', [])


def _entry(fb, k):
    for e in _bank(fb):
        if e.get('k') == k:
            return e
    return None


def page_up(m):
    """agent 填好的那一頁還在它的 Chrome 裡(記到分頁、沒被標成不見)。答案改了要不要標 refill 看這個:
    還沒填過(或頁面不見了、要整張重填)的卡,雇主網頁上沒有舊答案可以重打,重填時照新的答案填就好。
    跟看板的 pageUp 同一條。"""
    a = (m.get('apply') or {}) if isinstance(m, dict) else {}
    return a.get('stage') in ('fill', 'fix') and bool(a.get('tab_id')) and not a.get('gone') and not a.get('sent')


def mark_refill(fb, k):
    """答案 k 改了值:還沒送出、頁面還在的表單裡用到它的欄位標 refill(雇主網頁上還是舊字,送出前照答案庫重打)。"""
    for url, f in _forms(fb):
        if not f.get('lock') and page_up(fb[url]):
            for x in f.get('f', []):
                if x.get('k') == k:
                    x['refill'] = 1


def _forms(fb):
    for url, m in fb.items():
        f = m.get('form') if isinstance(m, dict) else None
        if f:
            yield url, f


def users(fb, k):
    """哪幾張表單在用這條答案:{職缺網址: 是否已投遞}。"""
    return {url: bool(f.get('lock')) for url, f in _forms(fb)
            if any(x.get('k') == k for x in f.get('f', []))}


_CJK = r'[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]'


def read_lang():
    """他看得懂的語言(設定 resume.read_lang,預設 zh)。"""
    try:
        import config as cf
        return str((cf.C.get('resume') or {}).get('read_lang') or 'zh')
    except Exception:
        return 'zh'


def _reads_cjk(lang):
    return str(lang or 'zh').split('-')[0].lower() in ('zh', 'ja', 'ko')


def lang_words(lang=None):
    """(他看得懂的語言, 另一套文字) 在訊息裡怎麼叫。中文使用者:中文、英文。"""
    lang = lang or read_lang()
    read = {'zh': '中文', 'en': '英文', 'ja': '日文', 'ko': '韓文'}.get(lang.split('-')[0].lower(), lang)
    return read, ('英文' if _reads_cjk(lang) else '原文')


def needs_translation(s, lang=None):
    """這個答案跟他看得懂的語言不是同一套文字(中日韓文字 vs 拼音文字),要附他看得懂的那份。
    中文使用者:有英文字母、沒有中文字的答案。"""
    s = str(s or '')
    if _reads_cjk(lang or read_lang()):
        return bool(re.search(r'[A-Za-z]', s)) and not re.search(_CJK, s)
    return bool(re.search(_CJK, s))


def _nq(s):
    """問法的比對用寫法:全形半形、大小寫、標點空白都不算差別。"""
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', str(s or '')).lower())


def _same_question(fb, *phrasings):
    """同一題的共用答案。這缺專用(pj)的不算:針對某個 JD 寫的答案,不能被接到別的職缺。"""
    ns = {_nq(p) for p in phrasings if _nq(p)}
    return [e for e in _bank(fb) if not e.get('pj')
            and ns & ({_nq(e.get('q'))} | {_nq(q) for q in e.get('qs', [])})]


def _mark(e, x, today):
    """這條的值是我這次給的:他親口講過的算確認,其餘標「我推論的」。"""
    if x.get('his'):
        e.pop('inf', None); e['at'] = today
    else:
        e.pop('at', None); e['inf'] = today


def _absorb(e, x):
    """記下這次見到的問法,補上缺的題型、中文翻譯、依據。不動值。"""
    q = x.get('q')
    if q and q != e.get('q') and q not in e.setdefault('qs', []):
        e['qs'].append(q)
    for kk in ('kind', 'zh', 'why'):
        if x.get(kk) and not e.get(kk):
            e[kk] = x[kk]


def _new_k(fb, x):
    base = 'a' + hashlib.sha1(((x.get('bank_q') or x['q']) + '\0' + x.get('v', '')).encode()).hexdigest()[:8]
    k, i = base, 1
    while _entry(fb, k) is not None:
        i += 1; k = f'{base}_{i}'
    return k


def _answer(fb, url, x, prev_k, today):
    """一個答案欄位 → 答案庫的那一條(找得到就用,找不到就開)。"""
    if x.get('kind') is not None and x['kind'] not in KINDS:
        raise ValueError(f"kind 要是 {KINDS} 其中之一:{x.get('q')!r}")
    v = x.get('v', '')
    if x.get('k'):
        e = _entry(fb, x['k'])
        if e is not None:
            if 'v' in x and v != e.get('v', ''):
                raise ValueError(f"{x['k']} 答案庫裡是 {e.get('v')!r},這張給的是 {v!r}。"
                                 "要用庫裡的就不要給值;答案真的不一樣就換一個 k 開新的一條。")
            _absorb(e, x)
            return e
        k = x['k']
    else:
        # 這張上次同一題指的那條,只有這張在用:我改寫的是同一個答案,直接改那條,不要留孤兒
        e = _entry(fb, prev_k) if prev_k else None
        if e is not None and set(users(fb, prev_k)) <= {url}:
            if v != e.get('v', ''):
                e['v'] = v
                for kk in ('zh', 'why'):
                    if kk in x: e[kk] = x[kk]
                _mark(e, x, today)
            _absorb(e, x)
            return e
        for e in _same_question(fb, x['q'], x.get('bank_q')):
            if e.get('v', '') == v:
                _absorb(e, x)
                return e
        k = _new_k(fb, x)
    e = {'k': k, 'q': x.get('bank_q') or x['q'], 'v': v, 'why': x.get('why', '')}
    for kk in ('kind', 'zh'):
        if x.get(kk): e[kk] = x[kk]
    # 共用還是這缺專用:我先判斷,他在看板上按一下切換就算他決定。沒講就照題型猜:
    # 短文、意見多半是針對這個 JD 寫的;事實、選項多半是關於他本人、每張都一樣。
    if x.get('pj', x.get('kind') in ('txt', 'op')):
        e['pj'] = 1
    if x.get('pjw'):
        e['pjw'] = x['pjw']
    _mark(e, x, today)
    _bank(fb).append(e)
    _absorb(e, x)
    return e


def apply_record(fb, url, plat, fields, at=None, today=None):
    """純函式版(測試用):把一張表單記進 fb。回傳這次新開的 k 清單。"""
    today = today or _today()
    cur = fb.setdefault(url, {})
    old = cur.get('form') or {}
    if old.get('lock'):
        raise ValueError('這張表單已鎖(已投遞),是送出時的紀錄,不能改。')
    prev = {x.get('q'): x.get('k') for x in old.get('f', []) if x.get('k')}
    before = {e.get('k') for e in _bank(fb)}
    out = []
    for x in fields:
        src = x.get('src') or 'bank'
        if src in ('rz', 'skip'):
            if x.get('k'):
                raise ValueError(f"{src} 欄位不能有 k:{x.get('q')!r}(有 k 就是答案,答案在答案庫)")
            out.append({kk: x[kk] for kk in PLAIN_FIELD if kk in x})
            continue
        if src not in ('bank', 'new'):
            raise ValueError(f"src 要是 rz / skip / bank 其中之一:{x.get('q')!r}")
        e = _answer(fb, url, x, prev.get(x.get('q')), today)
        y = {'q': x['q'], 'src': 'bank', 'k': e['k']}
        for kk in ('opt', 'lim'):
            if x.get(kk): y[kk] = x[kk]
        out.append(y)
    # 記到秒:代投核對「這一輪有沒有記表單」要跟這一輪開始的時間比,只有日期的話同一天第二輪沒記也會過
    cur['form'] = {'plat': plat, 'f': out, 'at': at or datetime.datetime.now().isoformat(timespec='seconds')}
    cur.pop('approve', None)          # 表單重記過,他核准的就不是這一份了,要重新核准
    bad = validate(fb, [url])
    if bad:
        raise ValueError('記完不對:' + ';'.join(bad))
    return [e['k'] for e in _bank(fb) if e.get('k') not in before]


def record(url, plat, fields, at=None, live=None):
    """寫進看板(走 set_fb:在鎖裡讀現行看板、改、寫回,不蓋掉他手機上的標記)。"""
    made = []
    kw = {'live': live} if live else {}
    bd.set_fb(lambda fb: made.extend(apply_record(fb, url, plat, fields, at)), **kw)
    return made


def fields_from_fill(fill):
    """代投的 fill.json 的 fields → record 收的欄位。
    以前 agent 同一張表單要寫兩份:form_record 一份、fill.json 一份,每一輪多想一兩分鐘,兩份還可能對不上。
    現在只寫 fill.json:每欄照抄頁面上的值(value),再標來源(src、k;新答案多給 zh、why、kind、pj)。
    答案庫現成的(有 k)不帶值,值以答案庫為準;其他沒另外給 v 的,送出的值就是頁面上的值。"""
    out = []
    for x in (fill or {}).get('fields') or []:
        if not isinstance(x, dict) or not x.get('q'):
            continue
        y = {kk: vv for kk, vv in x.items() if kk != 'value' and not (kk == 'k' and not vv)}
        if not y.get('k') and 'v' not in y and x.get('value') is not None:
            y['v'] = str(x['value'])
        out.append(y)
    return out


def record_fill(path, url, live=None):
    with open(path, encoding='utf-8') as f:
        fill = json.load(f)
    return record(url, fill.get('platform') or '', fields_from_fill(fill), live=live)


def validate(fb, urls=None):
    """「答案只在答案庫」有沒有被破壞。回傳問題清單,空的就是沒事。
    urls:只看這幾張表單(加上答案庫本身);None 是整個看板(--check)。記表單、翻譯只看自己動到的:
    以前一張壞掉(例如外部送出時還標著 refill),之後每一張都記不進去、每一次都翻不了。"""
    bad = []
    ks = [e.get('k') for e in _bank(fb)]
    dup = sorted({k for k in ks if ks.count(k) > 1})
    if dup:
        bad.append(f'答案庫有重複的 k:{dup}')
    have = set(ks)
    for url, f in _forms(fb):
        if urls is not None and url not in urls:
            continue
        for x in f.get('f', []):
            src, q = x.get('src'), x.get('q')
            if src not in SRCS:
                bad.append(f'{url}「{q}」src={src!r}(只能是 {SRCS})')
            elif src == 'bank':
                # 已投遞的那條可以被他從答案庫刪掉(送出時的原字在流水帳);還沒送出的一定要找得到答案
                if x.get('k') not in have and not f.get('lock'):
                    bad.append(f'{url}「{q}」指的 {x.get("k")} 答案庫裡沒有')
                extra = sorted(set(x) - set(BANK_FIELD))
                if extra:
                    bad.append(f'{url}「{q}」表單上帶了 {extra}(答案只能放答案庫)')
            elif x.get('k'):
                bad.append(f'{url}「{q}」是 {src} 卻有 k')
            if x.get('refill') and f.get('lock'):
                bad.append(f'{url}「{q}」已投遞還標著 refill')
    for e in _bank(fb):
        if e.get('inf') and e.get('at'):
            bad.append(f'{e.get("k")} 同時標了「我推論的」和「他確認」')
        if needs_translation(e.get('v')) and not str(e.get('zh') or '').strip():
            read, other = lang_words()
            bad.append(f'{e.get("k")} 是{other}答案卻沒有{read}翻譯(zh),他看不懂')
    return bad


def apply_translate(fb, k, en=None, zh=None):
    """寫回翻譯。en:照他改過的中文重翻的英文(清掉 tr;英文變了,還沒送出的表單標 refill)。
    zh:替英文答案補中文。回傳那一條。"""
    e = _entry(fb, k)
    if e is None:
        raise ValueError(f'答案庫裡沒有 {k}')
    if zh is not None:
        e['zh'] = zh
    if en is not None:
        changed = en != e.get('v', '')
        e['v'] = en
        e.pop('tr', None)
        if changed:
            mark_refill(fb, k)
    bad = validate(fb, [])            # 只動到答案庫這一條和沒送出表單的 refill 標記
    if bad:
        raise ValueError('翻完不對:' + ';'.join(bad))
    return e


def translate(k, en=None, zh=None, live=None):
    kw = {'live': live} if live else {}
    bd.set_fb(lambda fb: apply_translate(fb, k, en, zh), **kw)


def find_translate(fb):
    """要我翻的:他改了中文、英文待重翻的(tr),和還沒附中文的英文答案。"""
    return [e for e in _bank(fb)
            if e.get('tr') or (needs_translation(e.get('v')) and not str(e.get('zh') or '').strip())]


def find_refills(fb):
    """答案改了、雇主網頁還沒跟著改的欄位:[(職缺網址, 問題)]。"""
    return [(url, x.get('q')) for url, f in _forms(fb) if not f.get('lock')
            for x in f.get('f', []) if x.get('refill')]


def mark_stale(fb, url, why):
    """agent 填好之後,要上傳的檔換了(收下客製版、改回原始檔、換履歷或語言):網頁上傳的還是舊的那份。
    標在 apply 上,核准擋下(approval_problem),自動流程會替他重填一次;重填寫新的 apply 就清掉。"""
    m = fb.get(url) if isinstance(fb.get(url), dict) else None
    a = (m or {}).get('apply') or {}
    if a.get('stage') in ('fill', 'fix') and not ((m or {}).get('form') or {}).get('lock'):
        a['stale'] = why
        m.pop('approve', None)  # 換過上傳檔後，舊核准不再適用；重填後須重新核准
        return True
    return False


def apply_clear_refill(fb, url, q=None):
    n = 0
    for x in (fb.get(url, {}).get('form') or {}).get('f', []):
        if x.get('refill') and (q is None or q in (x.get('q') or '')):
            del x['refill']
            n += 1
    return n


def _blank(s):
    """空白也算空,跟看板的 trim() 一樣連 BOM(\\ufeff)一起去掉:Python 的 strip() 不去 BOM。"""
    return not str(s or '').replace('\ufeff', '').strip()


def _empty(e):
    return _blank(e.get('v')) and _blank(e.get('zh'))


def answers_pending(fb, url):
    """這張表單用到的答案還有沒有在等他(推論的、空的、或答案庫裡沒有那一條;看板的 fmTodo)。"""
    ks = {x.get('k') for x in ((fb.get(url) or {}).get('form') or {}).get('f', []) if x.get('src') == 'bank'}
    return any(e['k'] in ks for e, _ in find_pending(fb)) or bool(ks - {e.get('k') for e in _bank(fb)})


def find_pending(fb):
    """答案庫裡等他的(跟看板的 ⚠ 同一個算法):我推論的(inf),或答案還空著、而且有還沒送出的表單
    在用(或沒有表單在用)。[(那一條, 在用它的職缺網址)]。"""
    out = []
    for e in _bank(fb):
        us = users(fb, e['k'])
        if e.get('inf') or (_empty(e) and (not us or not all(us.values()))):
            out.append((e, sorted(us)))
    return out


def snapshot(fb, url):
    """核准時的答案快照:這張表單每一題 → 會送出去的值(答案庫那一條的英文 v;履歷直接對上的用表單上的 v)。
    看板的「✅ 核准送出」存的是同一個算法算出來的東西(board.js ansSnap),兩邊要一起改。"""
    bank = {e.get('k'): e for e in _bank(fb)}
    out = {}
    for x in ((fb.get(url) or {}).get('form') or {}).get('f', []):
        e = bank.get(x.get('k')) if x.get('src') == 'bank' else x
        out[x.get('q') or ''] = (e or {}).get('v') or ''
    return out


def board_status(board):
    """看板上投遞前驗收的結果(看板資料的 status),核准規則要看它。讀不到回 None:核准一律擋(不知道驗收過沒有)。"""
    try:
        with open(board, encoding='utf-8') as f:
            return bd.parse(f.read())['data'].get('status')
    except (OSError, ValueError, KeyError, TypeError):
        return None


def approval_problem(fb, url, status=None):
    """這張的核准還能不能拿去送出。None = 可以;不行就回原因(看板顯示同一句)。
    status:看板資料的 status(投遞前驗收的結果,board_status 讀);沒給就當成還沒驗收過,擋。"""
    m = fb.get(url) or {}
    f = m.get('form')
    if not f:
        return '這張還沒有表單紀錄'
    if f.get('lock'):
        return '已經送出了'
    if m.get('rm'):
        return '這張已經移除了'
    sf = (m.get('apply') or {}).get('submit_fail')
    if sf and not sf.get('cleared'):
        return '上次送出沒確認成功,先確認到底送出沒有'     # 不確定就重送,可能變成投兩次
    if (m.get('apply') or {}).get('sent'):
        # agent 在這一頁按過送出(已投出又退回來的卡):那一頁現在是「已收到申請」,不是表單;重填會換新的紀錄
        return '這張 agent 已經送出過了;要再投一次,先讓 agent 重填'
    # 投遞前驗收(職缺下架、要寄的檔案有問題、客製檔還沒處理完):確認之後才驗收失敗也要擋送出。
    # 跟看板 shipBlocked 同一支規則(autopilot.ship_blocked)
    import autopilot
    gate = autopilot.ship_blocked(fb, {'id': url}, status)
    if gate:
        return gate
    apply = m.get('apply') or {}
    if apply.get('stage') in ('fill', 'fix'):
        if not apply.get('ok'):
            return (apply.get('issues') or [''])[0] or '填表檢查還有問題,先讓 agent 改好'     # 空字串也用預設句(跟看板一樣)
        delivery = apply.get('delivery') or {}
        if delivery.get('method') not in ('direct_upload', 'no_profile', 'platform_profile'):
            return '填表檢查沒有回報這次直接上傳或使用哪一份平台履歷'
        if apply.get('stale'):
            return '履歷換過了,網頁上傳的還是舊的,先讓 agent 重填'
    if not m.get('approve'):
        return '還沒確認送出'
    ks = {x.get('k') for x in f.get('f', []) if x.get('src') == 'bank'}
    if answers_pending(fb, url):
        return '還有答案等你確認或填寫'
    if any(e.get('tr') for e in _bank(fb) if e.get('k') in ks):
        return '有答案你改了{},{}還沒照著重翻'.format(*lang_words())
    if any(x.get('refill') for x in f.get('f', [])):
        return '有答案改過,網頁上還是舊的,先讓 agent 改'       # 他核准的要是網頁上真的那一頁
    if (m['approve'].get('snap') or {}) != snapshot(fb, url):
        return '確認之後答案改過,要重新確認送出'
    return None


def apply_mark_sent(fb, url, evidence, today=None, sent_v=None):
    """送出成功(有確認頁證據)才走這裡:搬到已投遞、鎖表單、清掉待重打。核准紀錄留著當證據。"""
    today = today or _today()
    m = fb[url]
    m['app'] = 'sent'
    m.setdefault('sent_at', today)
    if sent_v:
        m.setdefault('sent_v', sent_v)
    m['form']['lock'] = 1
    for x in m['form'].get('f', []):
        x.pop('refill', None)
    m.setdefault('apply', {})['sent'] = evidence
    return m


def find_shared(fb):
    """填新表單之前先讀這份:他確認過、共用、有答案的。這缺專用的不在裡面(不能接到別的職缺)。"""
    return [e for e in _bank(fb) if not e.get('pj') and not e.get('inf') and not _empty(e)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--pending', action='store_true', help='答案庫裡等他確認的')
    ap.add_argument('--refills', action='store_true', help='答案改了、雇主網頁還沒重打的欄位')
    ap.add_argument('--clear-refill', metavar='URL', help='我重打完那張的網頁後,清掉標記')
    ap.add_argument('--q', help='只清問題文字含這段的欄位')
    ap.add_argument('--check', action='store_true', help='檢查答案有沒有跑到答案庫外面')
    ap.add_argument('--shared', action='store_true', help='填新表單前先讀:他確認過的共用答案')
    ap.add_argument('--from-fill', metavar='FILL_JSON', help='代投:把 fill.json 的 fields 記成 --url 那張的表單')
    ap.add_argument('--url', help='--from-fill 記到哪一張')
    ap.add_argument('--board', default=os.environ.get('AGENT_BOARD') or bd.LIVE,
                    help='哪一份看板(預設 AGENT_BOARD 或現行看板;測試給副本)')
    a = ap.parse_args()
    bd_live = a.board
    if a.from_fill:
        if not a.url:
            sys.exit('--from-fill 要給 --url')
        try:
            made = record_fill(a.from_fill, a.url, live=bd_live)
        except (ValueError, OSError) as e:
            sys.exit(f'沒記進去:{e}\n照訊息改 fill.json 的 fields,再跑一次同一個指令。')
        print('記好了' + (f',新開的答案:{", ".join(made)}' if made else ''))
        return
    with open(bd_live, encoding='utf-8') as f:
        fb = json.loads(bd.parse(f.read())['fb'])
    if a.clear_refill:
        n = []
        bd.set_fb(lambda d: n.append(apply_clear_refill(d, a.clear_refill, a.q)), live=bd_live)
        print(f'清掉 {n[0]} 欄')
    elif a.refills:
        ts, rs = find_translate(fb), find_refills(fb)
        if ts:
            print('先翻(他改了中文,英文照著重翻;或英文答案還沒附中文):用 translate(k, en=..., zh=...)')
            print('\n'.join(f"  {e['k']}: 中文 {e.get('zh')!r} / 英文 {e.get('v')!r}" for e in ts))
        print('\n'.join(f'{u}\n   {q}' for u, q in rs) if rs else '沒有需要重打的欄位')
    elif a.check:
        bad = validate(fb)
        print('\n'.join(bad) if bad else '沒問題:表單只有指標,答案都在答案庫')
        sys.exit(1 if bad else 0)
    elif a.shared:
        print('\n'.join(f"{e['k']}: {e.get('q')}\n   中文 {e.get('zh') or e.get('v')!r}\n   送出 {e.get('v')!r}"
                        for e in find_shared(fb)))
    else:
        ps = find_pending(fb)
        print('\n'.join(f"{e['k']}: {e.get('q')} = {e.get('zh') or e.get('v')!r}"
                        f"({'推論於 ' + e['inf'] if e.get('inf') else '空白,等他寫'};{'這缺專用' if e.get('pj') else '共用'})\n   " + '\n   '.join(us)
                        for e, us in ps) if ps else '答案庫裡沒有待確認的')


if __name__ == '__main__':
    main()
