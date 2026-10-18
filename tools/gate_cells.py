#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_cells —— 安檢門(gate)用的共用型別:交件單上的一格(Cell)、程式自己看到的真相(Truth)、核對結果(Verdict、Row)。

各流程登記的格子放在 gate_apply(幫你填表)、gate_flows(其他 7 種流程);入口只有 gate(read、inspect)。
"""
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profile_sync as ps  # noqa: E402

CHECK, DECIDED, WORDS, JUDGED = '核對', '程式決定', 'agent 的話', 'agent 判斷'
UNUSED = object()     # 核對函式回這個:這一格這一次用不到(例如用平台履歷時的「申請表收到的檔名」),不收、也不算錯


class Cell:
    """交件單上的一格:how 核對方式、name 給人看的名字、what 拿什麼比、fn(agent 說的, 交件單, 真相) → 沒問題回 None;
    對不上回「實際是什麼」(字串),或已經寫好的問題清單(沿用原本的說法)。items:這一格是清單或物件時,裡面每一筆有哪幾格。
    required:沒寫這一格時的問題(字串,或 fn(真相) 回字串;回空的就是可以不寫)。"""

    def __init__(self, how, name, what, fn=None, required=None, items=()):
        self.how, self.name, self.what, self.fn, self.required, self.items = how, name, what, fn, required, tuple(items)


class Truth:
    """程式自己看到的真相。

    幫你填表(開了 agent 的 Chrome):
      page:程式自己讀到的那一頁(讀不到是 None,page_why 是原因);tab_id:程式讀的是哪一個分頁;form_url:填好的申請表網址。
      job、fb:這張卡和看板;download_dir:申請表收到的檔下載到哪(profile_sync.check_attachments 比)。
      decision:這張卡該用哪一份平台履歷(profile_sync.decided);不給就照 job、fb 算。
    其他 7 種流程(沒開 agent 的 Chrome,真相就是程式交給 agent 的東西):
      given:{那一列是誰: 程式給它的原文};一列一列核對時各列拿到自己那一段(RowTruth)。
      下面這幾樣照「那一列是誰」分開放的,一列一列核對時也只給那一列自己的:
        fetched_on 程式抓那一頁的日期(「3 天前」照它算)、cites 程式附給它的舊卡代號、sources 給它引用的材料、
        flags 程式給它的提醒。
      feedback:{回饋代號: 回饋};cards:這一輪查的卡;texts:{來源代號: 程式複製的全文};
      source_types:{來源代號: 哪一種};resumes:勾選的履歷(None 是設定裡的)。"""

    PER_ROW = ('given', 'fetched_on', 'cites', 'sources', 'flags')

    def __init__(self, url=None, job=None, fb=None, page=None, page_why='', tab_id=None, download_dir=None,
                 decision=None, form_url=None, given=None, fetched_on=None, cites=None,
                 sources=None, flags=None, feedback=None, cards=None, texts=None, source_types=None, resumes=None):
        self.url, self.job, self.fb = url, job, fb if fb is not None else {}
        self.page, self.page_why, self.tab_id, self.form_url = page, page_why, tab_id, form_url
        self.download_dir = download_dir
        self._decision = decision
        self.given, self.fetched_on, self.cites = dict(given or {}), dict(fetched_on or {}), dict(cites or {})
        self.sources, self.flags, self.feedback = dict(sources or {}), dict(flags or {}), dict(feedback or {})
        self.cards, self.texts, self.source_types = set(cards or ()), dict(texts or {}), dict(source_types or {})
        self.resumes = resumes

    @property
    def decision(self):
        if self._decision is None:
            self._decision = ps.decided(self.job or {'id': self.url}, self.fb, self.url)
        return self._decision

    def text(self):
        """頁面上看得到的字(標題、每一行、每一格的值、每一格周圍的說明)正規化後接在一起。
        欄位旁的說明(例如「驗證碼已寄到你的信箱」)常只出現在欄位周圍那一段,不在一般的行裡。"""
        p = self.page or {}
        fields = [f for f in p.get('fields') or [] if isinstance(f, dict)]
        parts = [p.get('title')] + list(p.get('lines') or [])
        parts += [f.get('shown') or f.get('value') for f in fields if not isinstance(f.get('value'), list)]
        parts += [f.get('context') for f in fields]
        return '\n'.join(norm(x) for x in parts if x)


class RowTruth:
    """核對一列時的真相:key 是這一列是誰;handed 是這一輪程式交給它的每一列是誰;
    Truth.PER_ROW 那幾樣只給這一列自己的(沒有是 None),其他照整張的真相。"""

    def __init__(self, truth, key):
        self.sheet, self.key = truth, key
        self.handed = set(truth.given)
        for name in Truth.PER_ROW:
            setattr(self, name, getattr(truth, name).get(key))

    def __getattr__(self, name):
        return getattr(self.sheet, name)


class Verdict:
    """facts:核對過、對得上的格子(程式只准用這些);said:登記過的格子 agent 說了什麼(對不上的也在,只拿來記是哪一頁);
    problems:每一條一句;unregistered:沒登記的格子(程式不用)。
    judged:agent 判斷的格子(程式核對不了):不在 facts 裡,用的地方要標明「agent 判斷」、附原文、可以復原。
    rows:一列一列的清單 {清單名: [Row]},每一列各自的結果(problems 也收進整張的 problems)。"""

    def __init__(self, facts, problems, unregistered, said=None, judged=None, rows=None):
        self.facts, self.problems, self.unregistered = facts, problems, unregistered
        self.said = said if said is not None else dict(facts)
        self.judged = judged if judged is not None else {}
        self.rows = rows if rows is not None else {}

    @property
    def ok(self):
        return not self.problems


class Row:
    """一列:key 是誰(網址、代號);facts 核對過的格子;judged agent 判斷的格子;problems 這一列對不上的地方。"""

    def __init__(self, key, row, facts, judged, problems):
        self.key, self.row, self.facts, self.judged, self.problems = key, row, facts, judged, problems

    @property
    def ok(self):
        return not self.problems


class Problem(str):
    """程式驗出來的一個問題:字給他看;kind 給程式分類(要不要自動改、是不是要本人做),不從字去猜。
    存進看板時只剩字(kind 只在這一輪的程式裡用)。"""

    def __new__(cls, msg, kind):
        self = super().__new__(cls, msg)
        self.kind = kind
        return self

    def __getnewargs__(self):          # 深拷貝、跨程序傳時種類跟著走
        return str(self), self.kind


def kind(problem):
    return getattr(problem, 'kind', None)


class Problems(str):
    """好幾條問題接成一句給人看;items 留著每一條(和它的種類)給程式判斷。"""

    def __new__(cls, items, sep='; '):
        self = super().__new__(cls, sep.join(items))
        self.items = list(items)
        return self

    def __getnewargs__(self):
        return (self.items,)


def norm(s):
    s = unicodedata.normalize('NFKC', str(s or ''))
    return re.sub(r'\s+', ' ', s).strip().casefold()


NONE_CITED = '筆記裡沒有相關的'


def none_cited(citation):
    """agent 說筆記裡沒有相關的(後面多個句號也算)。"""
    return str(citation or '').strip().rstrip('。.') == NONE_CITED


def words(s):
    """只留字(文字、數字),比原文用:標點、空白、換行、引號樣式、各種連字號、Markdown 符號都不算。
    agent 抄原文時句尾多個句號、把 ’ 寫成 '、把 ‑ 寫成 -、拿掉 _斜體_、把換行接起來,字一樣就是同一句(2026-10-12 實測)。"""
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', str(s or ''))).casefold()


def quoted(citation, sources):
    """agent 說是逐字引用的那句,在其中一份材料裡找得到(照 words 比)。agent 跳著摘、把幾段接起來(… ｜ ；,
    2026-10-05、10-12 實測),每一段都要在同一份裡;前面標的舊卡代號(「c139：」)不算,有用引號括起來的只比引號裡的,
    「筆記裡沒有相關的」不是引用。理由、卡片摘要、詐騙幽靈缺的引用,職稱、公司都用這一支。"""
    quote = re.sub(r'^\[?c\d+\]?[：:\s]*', '', str(citation or '').strip())
    marked = re.findall(r'[「『“](.+?)[」』”]', quote)       # 有用引號括起來的(刊登：「2 weeks ago」):引號裡的才是原文
    parts = [p for x in (marked or [quote]) for p in re.split(r'[｜|；;\n]|…|\.\.\.', x) if not none_cited(p)]
    parts = [w for w in map(words, parts) if w]
    return bool(parts) and any(all(p in words(s) for p in parts) for s in sources)


def short(value, n=60):
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + '…'


def same_url(a, b):
    cut = lambda u: str(u or '').split('#')[0].rstrip('/')
    return cut(a) == cut(b)
