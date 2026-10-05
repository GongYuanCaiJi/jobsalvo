#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_flows —— 其他 7 種會叫 agent 的流程(刊登日期、職缺關了沒、分類建議、客製版、準備履歷、找缺、查應徵進度)
交件單每一格登記的核對方式。這幾種沒開 agent 的 Chrome,真相是程式交給 agent 的原文和程式自己知道的事(Truth)。
入口在 gate。
"""
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cf            # noqa: E402
import prefs                   # noqa: E402
import profile_sync as ps      # noqa: E402
import reply_run               # noqa: E402
import research                # noqa: E402
import sync_sent               # noqa: E402
from gate_cells import CHECK, DECIDED, JUDGED, UNUSED, WORDS, Cell, norm  # noqa: E402

def _given_key(said, row, truth):
    """這一列是誰(網址、代號):要是這一輪程式交給它的其中一個。"""
    return None if truth.key in truth.handed else '這一輪沒有交給它這一個'


def _in_given(said, row, truth):
    """agent 說是原文的字:程式交給它的那一列原文裡要逐字看得到(空白、全形半形、大小寫不算)。
    程式沒有原文可以比(沒抓到頁面):核對不了,這一格不用。"""
    if not str(said or '').strip():
        return None
    if not truth.given:
        return UNUSED
    return None if norm(said) in norm(truth.given) else '程式給它的原文裡沒有這段字'


def _posted_date(said, row, truth):
    """日期意思由 Agent 判讀；程式只驗日曆格式與來源引用。"""
    if not str(said or '').strip():
        return None                                   # 沒給日期:當成確認不了(流程照舊記原因)
    try:
        if datetime.date.fromisoformat(str(said)).isoformat() != str(said):
            raise ValueError
    except ValueError:
        return '要寫成 YYYY-MM-DD'
    if not str(row.get('source') or '').strip():
        return '要附原文中的日期說明'
    return _in_given(row['source'], row, truth)


POSTED_AT = {
    'dates.url': Cell(CHECK, '職缺網址', '這一輪交給它的其中一個網址', _given_key),
    'dates.posted_at': Cell(JUDGED, '刊登日期', 'Agent 依指定原文與抓頁日理解日期；程式驗 ISO 格式與引用',
                            _posted_date),
    'dates.source': Cell(CHECK, '日期欄位的名稱', '程式給它的那幾行裡逐字看得到', _in_given),
    'inaccessible.url': Cell(CHECK, '職缺網址', '這一輪交給它的其中一個網址', _given_key),
    'inaccessible.reason': Cell(WORDS, '確認不了的原因', '只給人看'),
    'inaccessible.need': Cell(WORDS, '要他做的事', '照原文放進回報給他看,標明是 agent 說的;程式不拿它判斷什麼'),
}

def _link_status(said, row, truth):
    """職缺還在不在是 agent 判斷(程式核對不了);只核對寫法,判關了要附頁面上逐字的那一句(那一句另外核對)。"""
    if said not in ('live', 'closed', 'uncertain'):
        return '只能是 live、closed、uncertain'
    if said == 'closed' and not str(row.get('quote') or '').strip():
        return [f'交件單「職缺還在不在」({truth.key}):agent 說 closed,卻沒有抄頁面上講職缺關了的那一句,不收']
    return None


LINK_STATUS = {
    'jobs.id': Cell(CHECK, '職缺代號', '這一輪交給它的其中一個(J1、J2…)', _given_key),
    'jobs.status': Cell(JUDGED, '職缺還在不在', '程式核對不了:卡上標明 agent 判斷、附它抄的原文;他按「不對,職缺還在」就不擋',
                        _link_status),
    'jobs.quote': Cell(CHECK, '頁面原文', '程式交給它的那一頁原文裡逐字看得到', _in_given),
    'jobs.reason': Cell(WORDS, '說明', '只給人看'),
}

def _regex(said, row, truth):
    """比對規則:看板用它比對職稱和內文,要是寫得出來的正規表示式(空的只有「其他」可以,那一類程式自己加)。"""
    try:
        import settings_api
        settings_api.compile_match(str(said or ''))
    except (re.error, ValueError) as e:
        return f'不是正規表示式({str(e)[:60]})'
    return None


def _named(said, row, truth):
    return None if str(said or '').strip() else '沒有名字'


SUGGEST_CATS = {
    'categories.name': Cell(JUDGED, '類別', '只是建議:設定頁標明是 agent 判斷的建議,他看過、按套用才寫進設定', _named),
    'categories.icon': Cell(WORDS, '類別圖示', '只給人看'),
    'categories.match': Cell(CHECK, '比對規則', '寫得出來的正規表示式', _regex),
    'tags.name': Cell(JUDGED, '標籤', '同上,只是建議', _named),
    'tags.match': Cell(CHECK, '比對規則', '寫得出來的正規表示式', _regex),
    'why': Cell(WORDS, '這樣分的理由', '只給人看'),
}

def _feedback_ids(said, row, truth):
    """歸進這個問題的回饋:都要是這一輪交給它的,而且至少兩張卡提到(一張卡提過的不算重複的問題)。"""
    given = truth.feedback
    ids = said if isinstance(said, list) else []
    stray = [str(i) for i in ids if i not in given]
    if stray or not ids:
        return f'這一輪沒給它 {"、".join(stray[:3])} 這幾則回饋' if stray else '沒有列出是哪幾則回饋'
    cards = {given[i].get('url') for i in ids}
    return None if len(cards) >= 2 else '只有一張卡提到(至少兩張卡都提到才算重複的問題)'


def _occurrences(said, row, truth):
    ids = row.get('feedback_ids') if isinstance(row.get('feedback_ids'), list) else []
    return None if said in (None, '') or str(said) == str(len(ids)) else f'{len(ids)}(程式照它列的回饋數的)'


CUSTOMIZE = {
    'reports.issue': Cell(JUDGED, '同一個問題', '哪幾則回饋算同一個問題是 agent 判斷:回報裡標明、附那幾則回饋的原文;'
                          '他在回報按已處理就收掉', _named),
    'reports.recommendation': Cell(WORDS, '建議', '照原文放進回報給他看'),
    'reports.feedback_ids': Cell(CHECK, '歸進來的回饋', '都是這一輪交給它的,而且至少兩張卡提到', _feedback_ids),
    'reports.occurrences': Cell(DECIDED, '提到幾次', '程式照它列的回饋數(agent 不用寫;寫了要一樣)', _occurrences),
}

def _card_text(truth):
    """程式交給 agent 的這張卡的原文(JD);沒給過是 None。"""
    return truth.given.get(truth.url)


def _in_jd(said, sheet, truth):
    if not str(said or '').strip():
        return None
    text = _card_text(truth)
    if not text:
        return UNUSED                  # 程式沒給它原文:核對不了,這一格不用
    return None if norm(said) in norm(text) else '程式給它的 JD 原文裡沒有這段字'


def _resume_pick(said, sheet, truth):
    """挑哪一份是 agent 判斷;只核對是勾選的、有那個語言檔的那幾份之一。"""
    if sheet.get('skip'):
        return UNUSED
    if not str(said or '').strip():
        return None                               # 沒挑:流程自己照「沒挑到可用的履歷」處理
    if not prefs.checked_resumes(truth.resumes):
        return UNUSED                             # 沒有勾選的履歷,指示叫它不要挑:它寫了什麼都不用
    if prefs.valid_resume_pick(str(said or '').strip(), sheet.get('lang'), truth.resumes):
        return None
    return f'不是勾選、有 {sheet.get("lang") or "那個語言"} 檔的履歷'


def _jd_lang(said, sheet, truth):
    """JD 是哪種語言是 agent 判斷(程式不猜):只核對是設定裡的其中一個語言。"""
    if sheet.get('skip') or not str(said or '').strip():
        return UNUSED if sheet.get('skip') else None
    return None if said in cf.LANGS else f'只能是 {"、".join(cf.LANGS)} 其中一個'


def _skip(said, sheet, truth):
    if said is True and not str(sheet.get('reason') or '').strip():
        return '跳過卻沒寫原因'
    return None


def _skip_reason(said, sheet, truth):
    """跳過的原因:說「抓不到 JD」,程式要真的沒給它原文;說「職缺已關」,要抄頁面上講關了的那一句(quote)。"""
    if not sheet.get('skip'):
        return UNUSED
    said = str(said or '').strip()
    if said.startswith('抓不到 JD') and _card_text(truth):
        return f'程式有給它 JD 原文({len(_card_text(truth))} 字)'
    if said.startswith('職缺已關') and not str(sheet.get('quote') or '').strip():
        return ['交件單「跳過的原因」:agent 說職缺已關,卻沒有抄頁面上講職缺關了的那一句(quote),不收']
    return None


_PICK = '挑哪一份是 agent 判斷(卡上寫「🤖 判」、附挑選理由,他可以改);只核對是勾選、有那個語言檔的'
PREPARE = {
    'resume': Cell(JUDGED, '挑的履歷', _PICK, _resume_pick),
    'variant': Cell(JUDGED, '挑的履歷(舊寫法)', _PICK, _resume_pick),
    'lang': Cell(JUDGED, '語言', 'JD 是哪種語言是 agent 判斷;只核對是設定裡的語言', _jd_lang),
    'why': Cell(WORDS, '挑選理由', '卡上照原文給人看'),
    'content_problem': Cell(JUDGED, '履歷內容問題', '卡上標明是 agent 判斷,只是提示(不擋、不放行)'),
    'real_title': Cell(CHECK, '頁面上的名字', '程式給它的 JD 原文裡逐字看得到(才拿來改卡片名字)', _in_jd),
    'skip': Cell(JUDGED, '跳過', '跳過是 agent 判斷:原因照原文寫在卡上;判職缺已關的卡標出錯了,他按放回原處就復原', _skip),
    'reason': Cell(CHECK, '跳過的原因', '說抓不到 JD,程式要真的沒給它原文;說職缺已關,要附頁面上那一句', _skip_reason),
    'quote': Cell(CHECK, '頁面原文', '程式給它的 JD 原文裡逐字看得到', _in_jd),
}

def _job_page(said, row, truth):
    ok = str(said or '').startswith('http') and not research.BAD_URL.search(str(said))
    return None if ok else '不是單一職缺頁的網址'


def _cited(said, row, truth):
    """它引用的舊卡代號:要是程式這一張附給它的那幾張之一。"""
    given = set(truth.cites or ())
    stray = [str(x) for x in (said if isinstance(said, list) else [said]) if str(x) not in given]
    return f'程式沒附給它 {"、".join(stray[:3])} 這幾張舊卡' if stray else None


def _reasons(said, row, truth):
    """每段理由的引用:要在程式這一張給它的材料(JD 原文、他的原話、偏好筆記)裡逐字找得到。"""
    _rows, bad = research.checked_reasons(said, truth.sources or {})
    return '引用在程式給它的材料裡找不到(或少了根據類型)' if bad else None


def _risk(said, row, truth):
    """詐騙、幽靈缺是 agent 判斷;它引的那句要在 JD 原文或程式提醒裡。"""
    said = said if isinstance(said, dict) else {}
    if str(said.get('kind') or '') not in ('scam', 'ghost', ''):
        return '風險只能是 scam、ghost 或空字串'
    why = str(said.get('why') or '').strip()
    if not said.get('kind') or not why:
        return None
    seen = norm(truth.given or '') + '\n' + '\n'.join(norm(x) for x in truth.flags or ())
    quotes = [norm(q) for q in re.findall(r'[「『"“](.+?)[」』"”]', why)] or [norm(why)]   # 有引號就看引號裡那幾段
    return None if all(q and q in seen for q in quotes) else '它引的那句 JD 原文和程式提醒裡都沒有'


def _card_summary(said, row, truth):
    """摘要、日期與金額是 Agent 判斷；程式核對它附的 JD 原文。"""
    if not isinstance(said, dict):
        return '摘要要是物件'
    has_details = any(str(said.get(k) or '').strip() not in ('', '無', '未公開', '查無')
                      for k in ('deadline', 'posted', 'salary'))
    if not has_details:
        return None
    if not truth.given:
        return UNUSED
    quote = str(said.get('quote') or '').strip()
    return None if quote and norm(quote) in norm(truth.given) else '日期／薪資摘要要附指定 JD 的逐字引用 quote'


RESEARCH_SEARCH = {
    'candidates.url': Cell(CHECK, '職缺網址', '單一職缺頁的網址(不是列表、搜尋頁)', _job_page),
    'candidates.title': Cell(WORDS, '找的時候看到的職稱', '只給判斷的人參考;卡片名字用判斷時頁面上的職稱'),
    'candidates.company': Cell(WORDS, '找的時候看到的公司', '只給判斷的人參考'),
    'candidates.why': Cell(WORDS, '為什麼可能對味', '只給判斷的人參考'),
    'candidates.via': Cell(WORDS, '怎麼找到的', '只記在卡上的來源'),
    'candidates.angle': Cell(JUDGED, '找缺角度', '角度名稱是 agent 判斷:只拿來統計、記在卡上的來源'),
    'candidates.jd_excerpt': Cell(CHECK, 'JD 摘錄', '程式抓回來的職缺頁原文裡逐字看得到才給判斷的人當原文;'
                                  '抓不到頁面就核對不了、不用', _in_given),
}

RESEARCH_JUDGE = {
    'jobs.id': Cell(CHECK, '職缺代號', '這一批交給它的其中一個(J1、J2…)', _given_key),
    'jobs.title': Cell(CHECK, '頁面上的職稱', '程式給它的 JD 原文裡逐字看得到(才拿來當卡片名字)', _in_given),
    'jobs.company': Cell(CHECK, '頁面上的公司', '程式給它的 JD 原文裡逐字看得到', _in_given),
    'jobs.keep': Cell(JUDGED, '要不要送到他眼前', '送不送是 agent 判斷:卡上標明、附理由,他可以移除'),
    'jobs.fit': Cell(JUDGED, '合不合適', '1-5 是 agent 判斷:只拿來排序,卡上標明'),
    'jobs.why': Cell(WORDS, '一句結論', '卡上照原文給人看'),
    'jobs.cite': Cell(CHECK, '引用的舊卡', '程式這一張附給它的那幾張之一', _cited),
    'jobs.reasons': Cell(CHECK, '理由的引用', '在程式這一張給它的材料裡逐字找得到(research.checked_reasons)', _reasons,
                         items=('text', 'citation', 'basis')),
    'jobs.risk': Cell(JUDGED, '詐騙或幽靈缺', '是不是詐騙、幽靈缺是 agent 判斷;它引的那句要在 JD 原文或程式提醒裡', _risk,
                      items=('kind', 'why')),
    'jobs.resume': Cell(JUDGED, '挑的履歷', _PICK, _resume_pick),
    'jobs.variant': Cell(JUDGED, '挑的履歷(舊寫法)', _PICK, _resume_pick),
    'jobs.lang': Cell(JUDGED, '語言', 'JD 是哪種語言是 agent 判斷;只核對是設定裡的語言', _jd_lang),
    'jobs.pick_why': Cell(WORDS, '挑選理由', '卡上照原文給人看'),
    'jobs.cat': Cell(JUDGED, '類別', '分在哪一類是 agent 判斷:他可以改'),
    'jobs.card': Cell(JUDGED, '卡片摘要', 'Agent 讀 JD 寫的摘要；日期與薪資解釋須附原文引用',
                      _card_summary, items=('fit', 'co', 'loc', 'deadline', 'salary', 'bar', 'posted', 'ammo', 'quote')),
}

# 查應徵進度:程式複製的信和平台應徵紀錄全文(Truth.texts {來源代號: 全文})、這一輪的卡(Truth.cards)、
# 來源代號是哪一種(Truth.source_types,程式自己建的索引)。程式沒讀到全文的來源(agent 補查的)核對不了:
# 要看原文的格子不用,那一則在卡上標明「agent 補查、程式沒讀到原文」

def _in_cards(said, row, truth):
    urls = said if isinstance(said, list) else [said]
    stray = [str(u) for u in urls if str(u) not in truth.cards]
    return f'{"、".join(stray[:2])} 不是這一輪查的卡' if stray else None


def _reply_ref(row, truth):
    """這一則回音對到哪一個來源(reply_run.resolved_ref:補查的信照原文連結對到那一封)。"""
    return reply_run.resolved_ref(row)


def _source_text(row, truth):
    return truth.texts.get(_reply_ref(row, truth))


def _source_ref(said, row, truth):
    return None if str(said or '').strip() in truth.source_types else '程式沒給它這個來源(照抄程式給的 source_ref)'


def _source_type(said, row, truth):
    want = truth.source_types.get(str(row.get('source_ref') or '').strip())
    return None if not said or not want or said == want else f'{want}(程式照 source_ref 認的)'


def _in_source(said, row, truth):
    """原文:程式複製的那一封信、那一頁紀錄的全文裡逐字看得到;程式沒讀到全文的核對不了、不用。"""
    if not str(said or '').strip():
        return None
    text = _source_text(row, truth)
    if not text:
        return UNUSED
    return None if norm(said) in norm(text) else '程式複製的那一封(那一頁)原文裡沒有這段字'


def _reply_date(said, row, truth):
    try:
        if datetime.date.fromisoformat(str(said)[:10]).isoformat() != str(said).strip()[:10]:
            raise ValueError
    except ValueError:
        return '要寫成 YYYY-MM-DD'
    text = _source_text(row, truth)
    if not text:
        return UNUSED
    return None                                      # 日期含義交 Agent；source_ref 與引用由其他格核對


def _reply_link(said, row, truth):
    """原文連結:信要是那一封(Gmail 對話代號跟 source_ref 一樣);平台紀錄要在那個平台的網站上。"""
    ref = str(row.get('source_ref') or '').strip()
    if ref.startswith('email:') and ref not in reply_run.SEARCH_REFS:
        thread = reply_run.gmail_message_id(said)
        if thread and f'email:{thread}' != ref:
            return f'{ref} 那一封的連結(程式照 source_ref 認的)'
        return None
    if ref.startswith('application_record:'):
        return None if ps.same_platform_url(said, ref.split(':', 1)[1]) else f'{ref.split(":", 1)[1]} 網站上的網址'
    return None


def _reply_kind(said, row, truth):
    """信算哪一種是 agent 判斷(程式核對不了):只核對寫法;拒絕、面試、錄取要抄信裡那一句(程式讀得到全文時)。"""
    if said not in ('confirm', 'reject', 'interview', 'offer'):
        return '只能是 confirm、reject、interview、offer'
    if said != 'confirm' and _source_text(row, truth) and not str(row.get('quote') or '').strip():
        return [f'交件單「信算哪一種」({truth.key}):agent 說 {said},卻沒有抄信裡決定是這一種的那一句(quote),不收']
    return None


def _records_text(truth):
    return '\n'.join(text for ref, text in truth.texts.items()
                     if str(ref).startswith('application_record:') and text)


def _record_id(said, row, truth):
    """平台應徵紀錄上的職缺代號:程式複製的應徵紀錄頁全文裡看得到;沒讀到全文核對不了、不用(不拿來標已投遞)。"""
    if not str(said or '').strip():
        return None
    text = _records_text(truth)
    if not text:
        return UNUSED
    return None if norm(said) in norm(text) else '程式複製的平台應徵紀錄裡沒有這個代號'


def _record_url(said, row, truth):
    if not str(said or '').strip():
        return None
    text = _records_text(truth)
    if not text:
        return UNUSED
    jid = sync_sent.job_id(str(said))
    if jid and norm(jid) in norm(text):
        return None
    return '程式複製的平台應徵紀錄裡沒有這個職缺' if jid else '認不出是哪個平台的職缺網址'


def _record_platform(said, row, truth):
    have = {str(ref).split(':', 1)[1] for ref, text in truth.texts.items()
            if str(ref).startswith('application_record:') and text}
    if not have:
        return UNUSED
    return None if str(said or '').strip().casefold() in {h.casefold() for h in have} else f'程式讀了 {"、".join(sorted(have))} 的應徵紀錄'


def _record_date(said, row, truth):
    text = _records_text(truth)
    if not str(said or '').strip():
        return None
    if not text:
        return UNUSED
    try:
        datetime.date.fromisoformat(str(said)[:10])
    except ValueError:
        return '要寫成 YYYY-MM-DD'
    return None                                      # 日期含義交 Agent；紀錄身分仍由程式核對


REPLY = {
    'checked': Cell(CHECK, '查完的卡', '都是這一輪查的卡(程式沒核實讀完的來源影響到的卡另外不推論沒下文)', _in_cards),
    'findings.url': Cell(CHECK, '卡片網址', '這一輪查的其中一張', _in_cards),
    'findings.source_ref': Cell(CHECK, '來源代號', '程式複製的來源或補查清單上的其中一個', _source_ref,
                                required='交件單「來源代號」:agent 沒寫 source_ref,程式對不到是哪一個來源,這一則不收'),
    'findings.source_type': Cell(DECIDED, '來源種類', '程式照來源代號自己認(agent 不用寫;寫了要一樣)', _source_type),
    'findings.source': Cell(WORDS, '來源名稱', '只給人看'),
    'findings.date': Cell(JUDGED, '日期', 'Agent 理解指定原文的日期；程式驗日曆格式與來源', _reply_date),
    'findings.subject': Cell(CHECK, '標題', '程式複製的原文裡逐字看得到', _in_source),
    'findings.summary': Cell(WORDS, '摘要', '卡上照原文給人看'),
    'findings.link': Cell(CHECK, '原文連結', '信要是 source_ref 那一封;平台紀錄要在那個平台的網站上', _reply_link),
    'findings.kind': Cell(JUDGED, '信算哪一種', '確認、拒絕、面試、錄取是 agent 判斷:照舊自動改狀態,卡上標明、附它抄的那一句,'
                          '按「不對,復原」改回去', _reply_kind),
    'findings.quote': Cell(CHECK, '信裡那一句', '程式複製的原文裡逐字看得到', _in_source),
    'findings.reason': Cell(CHECK, '拒絕理由', '程式複製的原文裡逐字看得到', _in_source),
    'findings.todo': Cell(JUDGED, '要他做的事', '是 agent 判斷:卡上寫「要你做」,他按「做好了」就收掉'),
    'job_ids.id': Cell(CHECK, '職缺代號', '程式複製的平台應徵紀錄裡看得到(才拿來標已投遞)', _record_id),
    'job_ids.url': Cell(CHECK, '職缺連結', '連結裡的職缺代號在程式複製的平台應徵紀錄裡看得到', _record_url),
    'job_ids.platform': Cell(CHECK, '平台', '程式讀了應徵紀錄的平台之一', _record_platform),
    'job_ids.applied_at': Cell(JUDGED, '應徵日期', 'Agent 理解平台原文的日期；程式驗日曆格式與紀錄身分', _record_date),
    'job_ids.title': Cell(WORDS, '職稱', '只給人看,不拿來配對'),
    'inaccessible.source': Cell(WORDS, '進不去的來源', '照原文放進回報'),
    'inaccessible.reason': Cell(WORDS, '進不去的原因', '照原文放進回報'),
    'inaccessible.need': Cell(WORDS, '要他做的事', '照原文放進回報,標明是 agent 說的'),
    'inaccessible.jobs': Cell(CHECK, '影響到的卡', '這一輪查的卡', _in_cards),
}
