#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
numbered —— 程式交給 agent 的材料邊印邊編號(#465):agent 只回整數清單,程式拿同一個物件解回自己的字。

  Numbered.add(text, kind, owner)  組指示的那一處呼叫,回要印進指示的那一塊(每段一行「n. 文字」,號碼接著上一塊)
  pick(numbered, ids, accept, ...) 唯一的號碼核對:只有收(回 Cited,字串是程式的字)或不收(丟 Miscited)
                                   要當值用的格(Accept.value):agent 另寫出值,只在它指的那幾段裡一字不差才收(ADR-0009)
  material_id、check_id(said)      這一次交付的短代號;交件單最上層抄回,check_id 核對不上就是對別次的材料寫的
"""
import dataclasses
import secrets
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:   # gate_cells 匯入這個模組,這裡只給型別用
    from gate_cells import RowTruth, Truth

KINDS = {   # 種類 → 錯誤訊息裡的名字
    'line': '頁面段落', 'date': '日期那幾行', 'text': '職缺頁原文', 'jd': 'JD 原文', 'flag': '程式提醒',
    'note': '偏好筆記', 'old': '相似舊卡的表態與原話', 'mail': '信', 'record': '平台應徵紀錄',
    'pnote': '這個平台以前學到的',
}


@dataclasses.dataclass(frozen=True)
class Part:
    n: int                 # 1 起算;一次交付裡連號,跨區塊不重來
    text: str              # 程式的字:字串來的每行去頭尾空白,清單來的原樣
    kind: str              # KINDS 的鍵
    owner: str | None      # 屬於哪一列(網址、J1、來源代號);None = 整批共用(#421:筆記、舊卡)


class Numbered:
    """程式這一次交給 agent 的編號材料。印進指示的和交給核對的是同一個物件。"""

    def __init__(self, parts=(), material_id=None):
        self.parts = list(parts)
        self.material_id = material_id or secrets.token_hex(3)

    def add(self, text, kind, owner=None):
        """字串一行一段(去頭尾空白);清單一項一段,原樣。空的不產生段,所以也不會出現在 held() 裡。"""
        items = [x.strip() for x in text.splitlines()] if isinstance(text, str) else [str(x) for x in text]
        start = len(self.parts)
        self.parts += [Part(start + i, x, kind, owner) for i, x in enumerate((x for x in items if x.strip()), 1)]
        return '\n'.join(f'{p.n}. {p.text}' for p in self.parts[start:])

    def check_id(self, said):
        """交回的交付代號要是這一次的;不是就丟 Miscited:同樣的號碼在別次交付裡指的是別的字。
        清單是同一批的列各抄了不同的代號(research 的 _write_answers 照實寫成清單)。"""
        if isinstance(said, list):
            raise Miscited(f'這一批的列抄了不同的交付代號({"、".join(map(str, said))}),這一次程式給的材料是 '
                           f'{self.material_id}:分不出是照哪一份材料寫的,整批不收')
        if said != self.material_id:
            raise Miscited(f'交件單的交付代號是 {said or "(沒寫)"},這一次程式給的材料是 {self.material_id}:'
                           '編號是對別次的材料寫的,不收')

    def held(self):
        """有材料的 (owner, kind):格子只知道哪一列有哪一種材料,看不到字。"""
        return frozenset((p.owner, p.kind) for p in self.parts)

    def dump(self):
        return {'material_id': self.material_id, 'parts': [dataclasses.asdict(p) for p in self.parts]}

    @classmethod
    def load(cls, data):
        return cls([Part(**p) for p in data['parts']], data['material_id'])


@dataclasses.dataclass(frozen=True)
class Accept:
    """一格收什麼。kinds:None 是任何種類;KINDS 鍵的集合;或吃那一筆、回那個集合的函式(理由照它自己的根據收不同材料)。
    owner:(那一列, 真相) → 准引的 owner;None 是任何 owner。
    value:要當值用的格,同一筆裡 agent 寫值的那個鍵(例如 'text');None 是只當證據,收下的是整段原文。"""
    kinds: frozenset[str] | Callable[[dict], frozenset[str]] | None = None
    owner: Callable[[dict, 'Truth | RowTruth'], Iterable[str | None]] | None = None
    value: str | None = None


ANY = Accept()


def ROW(row, truth):
    """自己這一列的,加整批共用的。"""
    return {truth.key, None}


class Miscited(ValueError):
    """引用不收:agent 寫的段落編號或交付代號是壞的值。"""


class Cited(str):
    """收下的引用:字串本身是程式的字(多段用換行接;要當值用的格是那幾段裡的那一截),parts 是那幾段;[] 收下是空字串。"""

    def __new__(cls, parts=(), text=None):
        self = super().__new__(cls, '\n'.join(p.text for p in parts) if text is None else text)
        self.parts = tuple(parts)
        return self

    def __getnewargs__(self):
        return (self.parts, str(self))


def _whose(owner):
    return '整批共用' if owner is None else str(owner)


def _cut(text, n=80):
    return text if len(text) <= n else text[:n] + '…'


def pick(numbered, ids, accept=ANY, row=None, truth=None, item=None):
    """唯一的號碼核對:形狀 → 範圍 → 重複 → 種類(item 決定)→ 歸屬(row、truth 決定)→ 值(accept.value 有給時)。
    [] 永遠合法;要不要非空由格子照情況判。交付代號不在這裡核。"""
    if not isinstance(ids, list) or any(type(n) is not int for n in ids):
        raise Miscited(f'段落編號無效:要寫整數清單(沒有可引的寫 []),agent 寫的是 {_cut(repr(ids))}')
    kinds = accept.kinds(item or {}) if callable(accept.kinds) else accept.kinds
    owners = set(accept.owner(row or {}, truth)) if accept.owner else None
    bad, seen = [], set()
    for n in ids:
        if not 1 <= n <= len(numbered.parts):
            bad.append(f'段落編號無效:{n} 不在這一次給的 1-{len(numbered.parts)} 裡')
            continue
        if n in seen:
            bad.append(f'段落編號無效:{n} 重複')
            continue
        seen.add(n)
        p = numbered.parts[n - 1]
        why = []
        if kinds is not None and p.kind not in kinds:
            names = '、'.join(KINDS[k] for k in sorted(kinds))
            why.append(f'是{KINDS[p.kind]},這一格只收{names}' if names else f'是{KINDS[p.kind]},這一格不收任何段落')
        if owners is not None and p.owner not in owners:
            allowed = '、'.join(sorted(map(_whose, owners)))
            why.append(f'屬於 {_whose(p.owner)},這一格只收 {allowed} 的')
        if why:
            bad.append(f'段落編號無效:第 {n} 段是「{_cut(p.text)}」,' + ';'.join(why))
    if bad:
        raise Miscited('; '.join(bad))
    parts = tuple(numbered.parts[n - 1] for n in ids)
    if accept.value is None:
        return Cited(parts)
    said = (item or {}).get(accept.value)
    if not isinstance(said, str):
        raise Miscited(f'{accept.value} 要寫頁面上那幾個字(字串),agent 寫的是 {_cut(repr(said))}')
    text = said.strip()
    if bool(text) != bool(parts):
        raise Miscited(f'{accept.value} 跟段落編號要一起寫或一起空:寫了「{_cut(text)}」,指了 {ids}' if text
                       else f'指了 {ids} 段,卻沒寫 {accept.value}')
    if parts and not any(text in p.text for p in parts):      # 整個值要在其中一段裡:不准把幾段接起來拼出頁面上沒連著的字
        shown = '」「'.join(_cut(p.text) for p in parts)
        raise Miscited(f'「{_cut(text)}」沒有一字不差出現在第 {"、".join(map(str, ids))} 段的任何一段裡(那幾段是「{shown}」)')
    return Cited(parts, text)
