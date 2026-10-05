#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_apply —— 幫你填表那幾張交件單(填表、修改的 fill.json、送出的 submit.json)
每一格登記的核對方式,和程式自己讀那一頁的判斷(還停在申請表、送出了沒、頁面變了沒)。入口在 gate。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_tab               # noqa: E402
import profile_sync as ps      # noqa: E402
from gate_cells import CHECK, DECIDED, JUDGED, UNUSED, WORDS, Cell, norm, same_url, short  # noqa: E402

def _card_url(said, sheet, truth):
    """同一張職缺:路徑相同,卡片網址原有的參數都在(職缺編號可能就在參數裡);agent 多帶的參數(例如 ?apply=form)不算換了職缺。"""
    from urllib.parse import urlsplit, parse_qsl
    if same_url(said, truth.url):
        return None
    a, b = urlsplit(str(said or '').split('#')[0]), urlsplit(str(truth.url or '').split('#')[0])
    same_page = (a.netloc, a.path.rstrip('/')) == (b.netloc, b.path.rstrip('/'))
    return None if same_page and set(parse_qsl(b.query)) <= set(parse_qsl(a.query)) else truth.url


def _tab(said, sheet, truth):
    if truth.page is None:
        return [f'程式讀不到留在他 Chrome 的那一頁({str(truth.page_why)[:80]})'] if truth.page_why else None
    if truth.tab_id is not None and str(said) != str(truth.tab_id):
        return f'分頁 {truth.tab_id}(程式讀的那一頁)'
    return None


def _handoff(said, sheet, truth):
    if said is not True:
        return '沒有交接,填好的分頁沒有留在他的 Chrome(他要在真的頁面上檢查)'
    return None


def _tab_url(said, sheet, truth):
    if truth.page is None:
        return None                       # 讀不到的原因在「分頁」那一格講過
    if not same_url(truth.page.get('url'), said):
        return f'{str(truth.page.get("url"))[:80]}(那一頁已經不是填好的申請表了,可能被送出了,要人看)'
    return None


def _on_page(said, sheet, truth):
    if not str(said or '').strip():
        return None
    if truth.page is None:
        return UNUSED                  # 程式沒讀到那一頁:這一格這一次不收;讀不到的原因在「分頁」那一格講過
    return None if norm(said) in truth.text() else '頁面上沒有這幾個字'


def _same_job(said, sheet, truth):
    if said is False:   # agent 自己說不是這個缺、沒填:沒有東西可以收(它沒填,不是拿它當事實放行)
        return [('agent 判斷這一頁不是這張卡的職缺(或已經關了),沒有填:'
                 + str((sheet.get('posting') or {}).get('title') or '')[:60])]
    return None


def _platform_url(said, sheet, truth):
    if not str(said or '').strip():
        return None
    plat = truth.decision.get('platform')
    if not plat or not ps.same_platform_url(said, plat, allow_local=ps.loopback_url(truth.url)):
        return f'不是這個平台({plat or "沒有登記平台"})的網址'
    return None


def _fixed_location(said, sheet, truth):
    """平台履歷看得到全文的那一頁:程式登記過就要是那一份;沒登記過,之後讀回來跟原始履歷比(apply_run.profile_after)。"""
    bad = _platform_url(said, sheet, truth)
    if bad:
        return bad
    d = truth.decision
    custom = str((sheet.get('delivery') or {}).get('profile_url') or '')
    if d.get('fixed_url') and said and ps.identity(said) not in (ps.identity(d['fixed_url']), ps.identity(custom)):
        return f'{d["fixed_url"]}(程式登記的那一份)'
    return None


def _profile_name(said, sheet, truth):
    d = truth.decision
    if d.get('name') and said and norm(said) != norm(d['name']) and d.get('profile_kind') == 'fixed':
        return f'「{d["name"]}」(程式記的名稱)'
    return None


def _method(said, sheet, truth):
    if said not in ('direct_upload', 'no_profile', 'platform_profile'):
        return '只能是 direct_upload、no_profile、platform_profile'
    if truth.page is None or not truth.decision.get('platform'):
        return None
    if said == 'platform_profile':
        return None        # 選的是不是該選的那一份:程式讀回那一份平台履歷之後自己讀申請頁核對(apply_run._picked_problem)
    ids = ps.linked(truth.page, truth.decision['platform'])
    if ids:
        return f'頁面上選的是平台履歷({sorted(ids)[0]})'
    names = {slot: e['name'] for slot, e in (ps.registry().get(truth.decision['platform']) or {}).items()
             if isinstance(e, dict) and e.get('name')}
    hit = ps.picked(truth.page, names)
    return f'頁面上選的是平台履歷「{names[sorted(hit)[0]]}」' if hit else None


def _delivery(said, sheet, truth):
    """投遞方式:申請頁上看得到的(選了哪一份平台履歷)跟它說的一樣;申請表直接上傳的,
    申請表實際收到的檔(下載回來、或讀不回時放進上傳欄的本機檔)逐位元組跟這張卡的檔比。"""
    bad = _method(said, sheet, truth)
    if bad or truth.job is None or said == 'platform_profile':
        return bad
    return _attachments(sheet, truth, **dict(truth.attachments, verify_profile=False))


def _profile_kind(said, sheet, truth):
    if (sheet.get('delivery') or {}).get('method') != 'platform_profile':
        return None
    want = truth.decision.get('profile_kind')
    return None if said == want else f'{want}(程式照這張卡決定的)'


def _profile_url(said, sheet, truth):
    if (sheet.get('delivery') or {}).get('method') != 'platform_profile':
        return None
    d = truth.decision
    if d.get('profile_kind') == 'fixed':
        if d.get('fixed_url') and said and ps.identity(said) != ps.identity(d['fixed_url']):
            return f'{d["fixed_url"]}(程式登記的固定版)'
        return None
    return _platform_url(said, sheet, truth)     # 客製版是 agent 這一輪新開的:之後逐位元組比附件


def _uploaded(said, sheet, truth):
    if (sheet.get('delivery') or {}).get('method') == 'platform_profile':
        return UNUSED            # 用平台上存好的履歷:申請表上沒有收檔,它寫的檔名(常是平台履歷上的附件)不用
    names = [n for n in (said if isinstance(said, list) else []) if isinstance(n, str) and n]
    if not names:
        return None
    if truth.page is None:
        return UNUSED                  # 程式沒讀到那一頁:這一格這一次不收;讀不到的原因在「分頁」那一格講過
    return apply_tab.upload_problems(truth.page, names) or None



def _profile_attachments(said, sheet, truth):
    """平台履歷上的附件(送出前核對那一輪下載或算雜湊):逐位元組跟這張卡該附的檔比;投遞方式要跟核准時一樣。"""
    if truth.job is None:
        return None
    return _attachments(sheet, truth, **truth.attachments)


def _attachments(sheet, truth, **options):
    """實際收到的檔逐位元組跟這張卡該附的比(profile_sync.check_attachments);比對出錯照實當成這張的問題。"""
    try:
        return ps.check_attachments(truth.job, truth.fb, truth.url, sheet, truth.download_dir, **options) or None
    except Exception as e:  # noqa: BLE001 — 比對出錯照實當成這張的問題
        return [f'平台附件比對出錯({str(e)[:80]})']


def _fields(said, sheet, truth):
    """每一欄的值:程式讀得到那一頁就看頁面上常用答案在不在;讀不到才拿 agent 抄的值跟常用答案比。"""
    rows = [x for x in (said if isinstance(said, list) else []) if isinstance(x, dict)]
    if truth.page is None:
        bank = {e.get('k'): e for e in truth.fb.get('__ans__', []) if isinstance(e, dict)}
        out = []
        for x in rows:
            e = bank.get(x.get('k'))
            if e and e.get('v') and norm(x.get('value')) not in (norm(e.get('v')), norm(e.get('zh'))):
                out.append(f'「{x.get("q")}」頁面上是 {str(x.get("value"))[:40]!r},常用答案是 {str(e.get("v"))[:40]!r}')
        return out or None
    # agent 這一輪說頁面上現在的值:常用答案的那幾題看常用答案(看板上記的表單那一段由程式另外比),其他照它抄的值要在頁面上
    copied = {truth.url: {'form': {'f': [{'q': x.get('q'), 'src': 'rz', 'v': x.get('value')} for x in rows
                                     if x.get('src') != 'skip' and not x.get('k') and not isinstance(x.get('value'), list)]}}}
    return apply_tab.page_problems(truth.page, copied, truth.url) or None


def _not_sent_in_fill(said, sheet, truth):
    """保留違規送出宣稱；不能以表單外觀否定它。證據不足由主線進 unsure。"""
    if said is False and not (sheet.get('clicked') is True or sheet.get('confirm_url') or sheet.get('confirm_text')):
        return None  # 正常填表、未操作送出，不需要送出結果證據
    return _submitted(said, sheet, truth)


def _submitted(said, sheet, truth):
    """結果意思由 Agent 判讀；程式核對本輪引用來源，未知不當未送出。"""
    if said is None:
        return None
    if type(said) is not bool:
        return 'submitted 要是 true、false 或 null'
    if truth.page is None:
        return '讀不到本輪結果頁面，無法核實送出結果'
    if not str(sheet.get('reason') or '').strip():
        return '要說明這次結果 reason'
    if not str(sheet.get('confirm_url') or '').strip() or not str(sheet.get('confirm_text') or '').strip():
        return '要附本輪結果頁網址與逐字引用'
    if said and sheet.get('clicked') is False:
        return '已送出與沒有按送出互相矛盾'
    return _confirm_url(sheet['confirm_url'], sheet, truth) or _confirm_text(sheet['confirm_text'], sheet, truth)


def _confirm_text(said, sheet, truth):
    if not str(said or '').strip() or truth.page is None:
        return None
    return None if norm(said) in norm(truth.text()) else '頁面上沒有這句話'


def _confirm_url(said, sheet, truth):
    if not str(said or '').strip() or truth.page is None:
        return None
    return None if same_url(said, truth.page.get('url')) else f'{str(truth.page.get("url"))[:80]}(程式讀到的那一頁)'


def _board_tab(said, sheet, truth):
    return None if truth.tab_id is None or str(said) == str(truth.tab_id) else f'分頁 {truth.tab_id}(看板上記的)'


def _problems(said, sheet, truth):
    return [f'卡住:{p}' for p in (said if isinstance(said, list) else [said] if said else [])] or None


def _delivery_required(truth):
    """投遞方式是一定要寫的格子(比附件要看它):有這張卡時沒寫就停。"""
    return truth.job is not None and ps.NO_DELIVERY


FIELD_ITEMS = ('q', 'value', 'src', 'k', 'why', 'zh', 'kind', 'pj', 'pjw', 'bank_q', 'v', 'choice', 'name', 'source_hash')
FILE_ITEMS = ('name', 'path')
_TAB_GONE = '填好的分頁沒有留在他的 Chrome(他要在真的頁面上檢查)'

FILL = {
    'url': Cell(CHECK, '職缺網址', '跟這張卡的網址一樣', _card_url),
    'platform': Cell(WORDS, '平台名稱', '只當表單紀錄上的顯示名稱'),
    'tab_id': Cell(CHECK, '分頁', '程式自己用這個分頁讀得到那一頁', _tab, required=_TAB_GONE),
    'handoff': Cell(CHECK, '交接分頁', '要交接,而且程式讀得到那一頁', _handoff, required=_TAB_GONE),
    'tab_url': Cell(CHECK, '分頁網址', '程式讀到的那一頁網址', _tab_url),
    'posting.title': Cell(CHECK, '頁面上的職稱', '程式讀到的那一頁上有這幾個字(才拿來改卡片名字)', _on_page),
    'posting.company': Cell(CHECK, '頁面上的公司', '程式讀到的那一頁上有這幾個字', _on_page),
    'posting.same_job': Cell(JUDGED, '是不是同一個缺', 'Agent 理解頁面與指定職缺；說不是就停', _same_job),
    'profile.needed': Cell(WORDS, '要不要平台履歷', '只給人看;用不用平台履歷看程式讀到的申請頁'),
    'profile.updated': Cell(WORDS, '改了平台履歷哪幾段', '只給人看；程式另交當前原稿與讀回頁面供 Agent 判讀'),
    'profile.url': Cell(CHECK, '平台履歷全文頁', '這個平台的網址;程式登記過就要是那一份;沒登記過,程式讀回來跟原始履歷比才收',
                        _fixed_location),
    'profile.edit': Cell(CHECK, '平台履歷編輯頁', '這個平台的網址', _platform_url),
    'profile.name': Cell(CHECK, '平台履歷名稱', '程式記過就要一樣;沒記過,在那一份的頁面上看到才記(profile_sync.check)',
                         _profile_name),
    'profile.application_history_url': Cell(CHECK, '應徵紀錄頁', '這個平台的網址', _platform_url),
    'profile.note': Cell(WORDS, '平台履歷備註', '只給人看'),
    'delivery.method': Cell(CHECK, '投遞方式', '程式讀到的申請頁:選了平台履歷就要是 platform_profile,而且是該選的那一份;'
                            '直接上傳的,申請表收到的檔逐位元組跟這張卡的檔比', _delivery,
                            required=_delivery_required),
    'delivery.profile_kind': Cell(DECIDED, '固定版還是客製版', '程式照這張卡有沒有收下的客製檔決定(profile_sync.decided)',
                                  _profile_kind),
    'delivery.profile_url': Cell(CHECK, '用的平台履歷', '固定版:程式登記的那一份;客製版:這個平台的網址,之後逐位元組比附件',
                                 _profile_url),
    'uploaded': Cell(CHECK, '申請表收到的檔名', '程式讀到的那一頁上傳欄裡有這幾個檔', _uploaded),
    'uploaded_files': Cell(CHECK, '申請表收到的檔', '下載回來逐位元組跟這張卡的檔比(在「投遞方式」那一格一起比)',
                           items=FILE_ITEMS),
    'uploaded_from': Cell(CHECK, '放進上傳欄的本機檔', '要在這張卡要寄的檔案裡、跟這張卡的檔一樣(同上)', items=FILE_ITEMS),
    'upload_readback': Cell(CHECK, '讀不讀得回上傳檔', '讀不回就改比放進上傳欄的本機檔(同上)'),
    'fields': Cell(CHECK, '表單每一欄', '程式讀到的那一頁上有常用答案(讀不到才比 agent 抄的值)', _fields,
                   items=FIELD_ITEMS),
    'blank_for_him': Cell(WORDS, '留給他填的欄', '只給人看'),
    'problems': Cell(WORDS, '卡住的地方', 'agent 說卡住就停(不拿它放行任何事)', _problems),
    'notes': Cell(WORDS, '其他觀察', '只給人看'),
    'platform_notes': Cell(WORDS, '平台做法', '存起來,下一輪標成「參考」附在指示裡'),
    'platform_notes_remove': Cell(WORDS, '不對的平台做法', '從參考裡拿掉那一句'),
    'fixed': Cell(WORDS, '改了哪幾格', '只給人看(修改那一輪)'),
    'submitted': Cell(JUDGED, '已送出', '填表不准送出；有宣稱就核實或禁止重送，不以表單外觀推論', _not_sent_in_fill),
    'clicked': Cell(WORDS, '有沒有按下送出', '有操作宣稱就核實或禁止重送'),
    'reason': Cell(WORDS, '送出結果說明', '只給人看；有送出宣稱時必須說明'),
    'confirm_url': Cell(CHECK, '確認頁網址', '程式讀到的那一頁網址', _confirm_url),
    'confirm_text': Cell(CHECK, '確認頁的字', '程式讀到的那一頁上有這句話', _confirm_text),
    'fixed_profile.url': Cell(CHECK, '固定平台履歷', '程式登記的那一份', _fixed_location),
    'fixed_profile.attachments': Cell(CHECK, '固定平台履歷上的附件', '逐位元組跟這張卡該附的檔比(送出前核對那一輪)',
                                      items=FILE_ITEMS),
}

SUBMIT = {
    'submitted': Cell(JUDGED, '送出結果', 'Agent 判讀本輪结果；程式核對引用來源', _submitted, required='沒有 submitted 結果'),
    'clicked': Cell(WORDS, '有沒有按下送出', '只當證據給人看;送出沒有看它判斷'),
    'confirm_url': Cell(CHECK, '確認頁網址', '程式讀到的那一頁網址,而且是這張卡的網站', _confirm_url),
    'confirm_text': Cell(CHECK, '確認頁的字', '程式讀到的那一頁上有這句話', _confirm_text),
    'tab_id': Cell(CHECK, '分頁', '看板上記的那一頁', _board_tab),
    'problems': Cell(WORDS, '卡住的地方', '只給人看'),
    'notes': FILL['notes'],
    'reason': Cell(WORDS, '送出結果說明', '說明 sent／not_sent／unknown', required='沒有 reason 結果說明'),
}


def _review_status(said, sheet, truth):
    return None if said in ('complete', 'issues', 'unknown') else 'status 只能是 complete、issues 或 unknown'


def _review_reason(said, sheet, truth):
    return None if isinstance(said, str) and said.strip() else 'reason 要是非空文字'


def _review_quotes(said, sheet, truth):
    if sheet.get('status') != 'complete':
        return None
    if not truth.given or not isinstance(said, dict):
        return '沒有本輪指定頁面的引用'
    def quoted(quote, text):
        # 引用可以跳著摘(2026-10-04 104:agent 把好幾段接成一段),但每一行都要在那一頁讀得到,不准編
        # 行首行尾的「」/引號是 agent 標引用的記號;中間用 … 省略的,每一段各自要讀得到
        parts = [norm(part.strip(' \t「」『』"“”\'')) for line in quote.splitlines()
                 for part in re.split(r'…|\.\.\.', line)]
        parts = [p for p in parts if p]
        return bool(parts) and all(p in norm(text) for p in parts)
    missing = [url for url, text in truth.given.items()
               if not isinstance(said.get(url), str) or not quoted(said[url], text)]
    return '指定頁面缺少可驗引用：' + '、'.join(missing) if missing else None


def _review_checks(said, sheet, truth):
    """只驗逐頁覆蓋與引用身分；每項意思仍是 Agent 判讀。"""
    pages = (truth.attachments or {}).get('profile_items')
    if not pages or not isinstance(said, dict) or set(said) != set(pages):
        return '逐項判讀沒有涵蓋全部指定頁面'
    for url, items in pages.items():
        rows = said[url]
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            return f'{url} 的逐項判讀格式不正確'
        ids = [r.get('id') for r in rows]
        wanted = {i for i, item in items.items() if item['kind'] != 'approved'}
        if any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)) or set(ids) != wanted:
            return f'{url} 的逐項判讀有漏項、重複或不屬於本輪的項目'
        for row in rows:
            status, basis = row.get('status'), row.get('basis')
            # unsourced:頁面上有、原稿與核准來源都沒有的內容,交使用者在回報確認一次;原稿寫了卻不一樣是 issues
            if status not in ('complete', 'issues', 'unknown', 'unsourced') or _review_reason(row.get('reason'), sheet, truth):
                return f'{url} {row["id"]} 沒有有效判讀結果或理由'
            if not isinstance(basis, list) or any(not isinstance(i, str) or i not in items for i in basis):
                return f'{url} {row["id"]} 引用的依據不在本輪材料'
            if status == 'complete' and not basis:
                return f'{url} {row["id"]} 的 complete 沒有判讀依據'
            if sheet.get('status') == 'complete' and status != 'complete':
                return f'{url} {row["id"]} 尚有 {status}：{row["reason"]}'
    return None


PROFILE_REVIEW = {
    'status': Cell(JUDGED, '履歷內容是否完整', 'Agent 對本輪指定原稿與讀回內容的判斷', _review_status, required='沒有 status 判讀結果'),
    'reason': Cell(WORDS, '判讀說明', '保留給人核對', _review_reason, required='沒有 reason 判讀說明'),
    'quotes': Cell(CHECK, '各頁引用', '每一份指定讀回頁面上的逐字引用', _review_quotes, required='沒有 quotes 引用'),
    'checks': Cell(JUDGED, '逐項內容判讀', 'Agent 判斷；程式核對覆蓋與本輪依據', _review_checks, required='沒有 checks 逐項判讀'),
}


# ---- 程式自己讀的那一頁 ----



# 送出成功的字(給 agent 的送出指示也照這幾句):程式讀到的那一頁上有其中一句,而且已經不是申請表,就是送出了




def _key(field):
    return ' '.join(str(field.get('label') or field.get('name') or '').split())


def _value(field):
    value = field.get('value')
    if isinstance(value, list):
        return ', '.join(str(x) for x in value)
    shown = field.get('shown')
    return str(shown if shown not in (None, '') else value if value is not None else '')


def seen(page):
    """驗收時核對過的那一頁,記成比得起來的樣子:{'url', 'fields': [[題目, 值]], 'files': [看得到的檔名]}。
    同一個題目出現好幾次(單選的每個選項)照順序各記一筆。"""
    page = page or {}
    return {'url': page.get('url') or '',
            'fields': [[_key(f), _value(f)] for f in page.get('fields') or [] if isinstance(f, dict)],
            'files': sorted(norm(x) for x in page.get('shownFiles') or [])}


def page_changes(before, page):
    """程式剛讀到的那一頁,跟驗收時核對過的樣子(seen 記的)比:回每一格「從什麼變成什麼」;一樣回空清單。"""
    if not before:
        return []
    now = seen(page)
    out = []
    if before.get('url') and not same_url(before['url'], now['url']):
        out.append(f'網址從 {short(before["url"], 80)} 變成 {short(now["url"], 80)}(可能被送出或換頁了)')
        return out
    old, new = {}, {}
    for box, rows in ((old, before.get('fields') or []), (new, now['fields'])):
        for k, v in rows:
            box.setdefault(k, []).append(v)
    for k in list(old) + [k for k in new if k not in old]:
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        label = k or '(沒有標籤的欄)'
        if b is None:
            out.append(f'「{label}」不見了')
        elif a is None:
            out.append(f'多了「{label}」')
        else:
            pairs = [(x, y) for x, y in zip(a, b) if x != y] or [(', '.join(a), ', '.join(b))]
            x, y = pairs[0]
            out.append(f'「{label}」從「{short(x)}」變成「{short(y)}」')
    if (before.get('files') or []) != now['files']:
        out.append(f'看得到的檔從 {before.get("files") or "(沒有)"} 變成 {now["files"] or "(沒有)"}')
    return out
