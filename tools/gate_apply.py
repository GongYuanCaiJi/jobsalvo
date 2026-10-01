#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_apply —— 幫你填表那幾張交件單(填表、修改的 fill.json、送出前核對的 pre-submit.json、送出的 submit.json)
每一格登記的核對方式,和程式自己讀那一頁的判斷(還停在申請表、送出了沒、頁面變了沒)。入口在 gate。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_chrome            # noqa: E402
import apply_tab               # noqa: E402
import profile_sync as ps      # noqa: E402
from gate_cells import CHECK, DECIDED, UNUSED, WORDS, Cell, Truth, norm, same_url, short  # noqa: E402

def _card_url(said, sheet, truth):
    return None if same_url(said, truth.url) else truth.url


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
    """填表那一輪說已經送出:程式讀那一頁,還停在申請表就不是真的。"""
    if said is not True:
        return None
    if truth.page is None:
        return UNUSED                  # 程式沒讀到那一頁:這一格這一次不收;讀不到的原因在「分頁」那一格講過
    if still_form(truth.page, sheet.get('tab_url') or truth.url):
        return '頁面還停在申請表'
    return None


def _submitted(said, sheet, truth):
    """送出那一輪說送出了:程式讀那一頁,還停在申請表(或跳出真人驗證)就是沒送出。讀不到的不在這裡判(apply_run 照截圖和說法)。"""
    state, why = after_send(truth.page, truth.form_url or '')
    return why if said is True and state == NOT_SENT else None


def _confirm_text(said, sheet, truth):
    if not str(said or '').strip() or truth.page is None:
        return None
    return None if norm(said) in truth.text() else '頁面上沒有這句話'


def _confirm_url(said, sheet, truth):
    if not str(said or '').strip() or truth.page is None:
        return None
    return None if same_url(said, truth.page.get('url')) else f'{str(truth.page.get("url"))[:80]}(程式讀到的那一頁)'


def _board_tab(said, sheet, truth):
    return None if truth.tab_id is None or str(said) == str(truth.tab_id) else f'分頁 {truth.tab_id}(看板上記的)'


def _problems(said, sheet, truth):
    return [f'卡住:{agent_chrome.explain_blocked(p)}' for p in (said if isinstance(said, list) else [said] if said else [])] or None


def _delivery_required(truth):
    """投遞方式是一定要寫的格子(比附件要看它):有這張卡時沒寫就停。"""
    return truth.job is not None and ps.NO_DELIVERY


FIELD_ITEMS = ('q', 'value', 'src', 'k', 'why', 'zh', 'kind', 'pj', 'pjw', 'bank_q', 'v')
FILE_ITEMS = ('name', 'path')
EQUIV_ITEMS = ('master', 'platform', 'why', 'not_shown')
_TAB_GONE = '填好的分頁沒有留在他的 Chrome(他要在真的頁面上檢查)'

FILL = {
    'url': Cell(CHECK, '職缺網址', '跟這張卡的網址一樣', _card_url),
    'platform': Cell(WORDS, '平台名稱', '只當表單紀錄上的顯示名稱'),
    'tab_id': Cell(CHECK, '分頁', '程式自己用這個分頁讀得到那一頁', _tab, required=_TAB_GONE),
    'handoff': Cell(CHECK, '交接分頁', '要交接,而且程式讀得到那一頁', _handoff, required=_TAB_GONE),
    'tab_url': Cell(CHECK, '分頁網址', '程式讀到的那一頁網址', _tab_url),
    'posting.title': Cell(CHECK, '頁面上的職稱', '程式讀到的那一頁上有這幾個字(才拿來改卡片名字)', _on_page),
    'posting.company': Cell(CHECK, '頁面上的公司', '程式讀到的那一頁上有這幾個字', _on_page),
    'posting.same_job': Cell(CHECK, '是不是同一個缺', '說不是就停(它沒填,沒有東西可以收)', _same_job),
    'profile.needed': Cell(WORDS, '要不要平台履歷', '只給人看;用不用平台履歷看程式讀到的申請頁'),
    'profile.updated': Cell(WORDS, '改了平台履歷哪幾段', '只給人看;程式之後自己讀回來跟原始履歷比'),
    'profile.url': Cell(CHECK, '平台履歷全文頁', '這個平台的網址;程式登記過就要是那一份;沒登記過,程式讀回來跟原始履歷比才收',
                        _fixed_location),
    'profile.edit': Cell(CHECK, '平台履歷編輯頁', '這個平台的網址', _platform_url),
    'profile.name': Cell(CHECK, '平台履歷名稱', '程式記過就要一樣;沒記過,在那一份的頁面上看到才記(profile_sync.check)',
                         _profile_name),
    'profile.application_history_url': Cell(CHECK, '應徵紀錄頁', '這個平台的網址', _platform_url),
    'profile.equivalents': Cell(CHECK, '平台用自己說法寫的格子', '程式讀回那一份平台履歷,字真的在頁面上才收'
                                '(profile_sync.accept_equivalents,在 apply_run.profile_after)', items=EQUIV_ITEMS),
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
    'submitted': Cell(CHECK, '已送出', '填表那一輪不准送出;說送了,程式讀那一頁,還停在申請表就不是真的', _not_sent_in_fill),
    'confirm_url': Cell(CHECK, '確認頁網址', '程式讀到的那一頁網址', _confirm_url),
    'confirm_text': Cell(CHECK, '確認頁的字', '程式讀到的那一頁上有這句話', _confirm_text),
    'fixed_profile.url': Cell(CHECK, '固定平台履歷', '程式登記的那一份', _fixed_location),
    'fixed_profile.attachments': Cell(CHECK, '固定平台履歷上的附件', '逐位元組跟這張卡該附的檔比(送出前核對那一輪)',
                                      items=FILE_ITEMS),
}

PRE_SUBMIT = {
    'delivery.method': Cell(CHECK, '投遞方式', '要跟核准時一樣;平台履歷上的附件、申請表收到的檔逐位元組跟這張卡該附的檔比'
                            '(profile_sync.check_attachments)', _profile_attachments,
                            required=_delivery_required),
    'delivery.profile_kind': FILL['delivery.profile_kind'],
    'delivery.profile_url': FILL['delivery.profile_url'],
    'profile_attachments': Cell(CHECK, '平台履歷上的附件', '下載回來(或算雜湊)逐位元組跟這張卡該附的檔比(在「投遞方式」那一格一起比)',
                                items=FILE_ITEMS),
    'fixed_profile.url': FILL['fixed_profile.url'],
    'fixed_profile.attachments': Cell(CHECK, '固定平台履歷上的附件', '同上,跟固定版該附的檔比', items=FILE_ITEMS),
    'uploaded_files': Cell(CHECK, '申請表收到的檔', '同上,直接上傳的逐位元組跟這張卡的檔比', items=FILE_ITEMS),
    'uploaded_from': FILL['uploaded_from'],
    'upload_readback': FILL['upload_readback'],
    'problems': FILL['problems'],
    'notes': FILL['notes'],
}

SUBMIT = {
    'submitted': Cell(CHECK, '送出成功', '程式讀按完送出後的那一頁:還停在申請表就是沒送出', _submitted),
    'clicked': Cell(WORDS, '有沒有按下送出', '只當證據給人看;送出沒有看它判斷'),
    'confirm_url': Cell(CHECK, '確認頁網址', '程式讀到的那一頁網址,而且是這張卡的網站', _confirm_url),
    'confirm_text': Cell(CHECK, '確認頁的字', '程式讀到的那一頁上有這句話', _confirm_text),
    'tab_id': Cell(CHECK, '分頁', '看板上記的那一頁', _board_tab),
    'problems': Cell(WORDS, '卡住的地方', '只給人看'),
    'notes': FILL['notes'],
}


# ---- 程式自己讀的那一頁 ----

def still_form(page, form_url=''):
    """這一頁還是申請表:網址還是填好的那一頁(給了才比),而且表單欄位還在。"""
    if not (page or {}).get('fields'):
        return False
    return not form_url or same_url(page.get('url'), form_url)


# 送出成功的字(給 agent 的送出指示也照這幾句):程式讀到的那一頁上有其中一句,而且已經不是申請表,就是送出了
SENT_WORDS = ('Application submitted', 'Thank you', '已送出', '應徵成功')
NOT_SENT, SENT, FORM_STILL = 'not_sent', 'sent', 'form_still'


def after_send(page, form_url='', before=None):
    """按了送出之後程式讀到的那一頁,程式自己判斷(不看 agent 說什麼):
    (NOT_SENT, 原因) 還停在申請表(表單還在、或跳出真人驗證);(SENT, 那一句) 申請表不見了,頁面上有送出成功的字;
    (FORM_STILL, 原因) 網址換了,申請表的格子還在:不算送出了(agent 說成功也不算),也不確定沒送出;
    ('', '') 程式判斷不了(讀不到、換了頁卻沒有成功的字)。
    before:驗收時核對過的那一頁(seen 記的);給了就看那幾格還在不在,沒給就要一格都沒有才算申請表不見了。
    (申請表頁首常寫「Thank you for your interest」:網址變了、表單還在,不能因為這句算送出了)"""
    if page is None:
        return '', ''
    human = apply_tab.human_check(page)
    if human or still_form(page, form_url):
        return NOT_SENT, ('跳出真人驗證,' if human else '') + '按了送出,頁面還停在申請表:沒送出'
    was = {label for label, _value in (before or {}).get('fields') or [] if label}
    now = {label for label, _value in seen(page)['fields'] if label}
    if (was & now) if was else now:
        return FORM_STILL, '按了送出,網址換了,可是申請表的格子還在,不算送出成功'
    text = Truth(page=page).text()
    hit = next((w for w in SENT_WORDS if norm(w) in text), '')
    return (SENT, hit) if hit else ('', '')


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
