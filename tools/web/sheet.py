"""一個 Schema 同時產生交件單格式並收件核對;核對不過也永遠收下。"""
from __future__ import annotations

import dataclasses
import enum
import json
import re
from typing import Any

from web.marks import Mark, joined, say, say_each
from web.numbered import Accept, Miscited, Numbered, pick


class How(enum.Enum):
    CITE = 'cite'
    KNOWN = 'known'
    ENUM = 'enum'
    WORDS = 'words'
    NUMBER = 'number'


@dataclasses.dataclass(frozen=True)
class Field:
    name: str
    how: How
    what: str
    accept: Accept | None = None
    known: str | None = None
    one_of: tuple[str, ...] = ()
    required: bool = False
    many: bool = False
    sub: tuple['Field', ...] = ()


@dataclasses.dataclass(frozen=True)
class Schema:
    task: str
    fields: tuple[Field, ...]

    def format_text(self) -> str:
        """產生 JSON 格式與欄位說明;brief 不另寫交件單格式。"""
        def example(field: Field):
            if field.sub:
                return dict((child.name, [example(child)] if child.many else example(child)) for child in field.sub)
            if field.how is How.CITE:
                row = {'material_id': '交付代號', 'ids': [1]}
                if field.accept and field.accept.value:
                    row[field.accept.value] = '頁面上的原字'
                return row
            if field.how is How.ENUM:
                return field.one_of[0] if field.one_of else '值'
            if field.how is How.NUMBER:
                return int(field.one_of[0]) if field.one_of else 0
            if field.how is How.KNOWN:
                return '照材料中的名稱原樣填寫'
            return '原話'

        shape = {field.name: [example(field)] if field.many else example(field) for field in self.fields}
        lines = [f'交件單格式({self.task}):回傳完整 JSON 物件,不加 Markdown。',
                 json.dumps(shape, ensure_ascii=False, indent=2), '欄位說明:']

        def describe(fields, indent):
            # 子欄位跟最上層一樣寫說明、必填、可填的值:agent 只看得到這裡寫的
            for field in fields:
                suffix = ' (必填)' if field.required else ''
                lines.append(f'{indent}- {field.name}: {field.what}{suffix}')
                if field.how is How.CITE and not field.sub:
                    lines.append(f'{indent}  引段欄寫 material_id 和 ids;每段編號照這次提供的材料。')
                elif field.how is How.ENUM or field.how is How.NUMBER and field.one_of:
                    lines.append(f'{indent}  可填: {"、".join(field.one_of)}')
                describe(field.sub, indent + '  ')
        describe(self.fields, '')
        return '\n'.join(lines)


@dataclasses.dataclass(frozen=True)
class Context:
    materials: tuple[Numbered, ...]
    known: dict[str, frozenset[str]]


class Verdict(enum.Enum):
    VERIFIED = 'verified'
    LISTED = 'listed'
    PARSED = 'parsed'
    WORDS = 'words'


@dataclasses.dataclass(frozen=True)
class Cell:
    field: str
    said: Any
    value: Any
    verdict: Verdict
    marks: tuple[Mark, ...] = ()


@dataclasses.dataclass(frozen=True)
class Sheet:
    cells: dict[str, Cell | list[dict[str, Any]]]
    extra: dict[str, Any]
    broken: Mark | None = None

    def said(self, name: str) -> Any:
        """那一格 agent 寫的原值;沒有那一格、或那一格是清單就是 None。"""
        cell = self.cells.get(name)
        return cell.said if isinstance(cell, Cell) else None

    def rows(self, name: str) -> list[dict[str, Any]]:
        """清單那一格每一列 agent 寫的原值;不是清單就是空的。"""
        cell = self.cells.get(name)
        return [{key: child.said for key, child in row.items()} for row in cell] if isinstance(cell, list) else []

    def ok(self, name: str) -> bool:
        cell = self.cells.get(name)      # 只用在單格欄位(填表的送出、交付方式、職缺與公司名稱)
        return isinstance(cell, Cell) and cell.verdict in (Verdict.VERIFIED, Verdict.PARSED)


def _listed(field: Field, raw: Any, code: str, said: Any = None, **params) -> Cell:
    details = dict(params)
    if said is not None:
        details['said'] = joined(said)
    marks = (say(code, **details), *say_each('AGENT_WORDS', said, 'words'))
    return Cell(field.name, raw, raw, Verdict.LISTED, marks)


def quote(cell: Cell) -> str:
    """agent 引的那幾段在程式材料裡的原文;沒有可核對的段落時是空字串。"""
    return '\n'.join(part.text for part in getattr(cell.value, 'parts', ()))


def _read_field(field: Field, raw: Any, context: Context, row: dict | None = None) -> Cell:
    if raw is None:
        if field.how is How.CITE:
            return _listed(field, raw, 'CITE_NONE')
        if field.required:
            return _listed(field, raw, 'CITE_NONE')
        raw = ''
    if field.how is How.WORDS:
        return Cell(field.name, raw, raw, Verdict.WORDS)
    if field.how is How.ENUM:
        if raw in field.one_of:
            return Cell(field.name, raw, raw, Verdict.PARSED)
        return _listed(field, raw, 'SAID_VS_ACTUAL', said=raw, actual='不在可選的值裡',
                       why='只能填：' + '、'.join(field.one_of))
    if field.how is How.NUMBER:
        text = str(raw) if isinstance(raw, int) and not isinstance(raw, bool) else raw
        if isinstance(text, str) and re.fullmatch('[0-9]+', text) and (not field.one_of or str(int(text)) in field.one_of):
            return Cell(field.name, raw, int(text), Verdict.PARSED)
        actual = '不是數字' if not isinstance(text, str) or not re.fullmatch('[0-9]+', text) else '不在可選的數字裡'
        return _listed(field, raw, 'SAID_VS_ACTUAL', said=raw, actual=actual,
                       why='必須是整數' if not field.one_of else '只能填：' + '、'.join(field.one_of))
    if field.how is How.KNOWN:
        known = context.known.get(field.known or '', frozenset())
        if raw == '' and not field.required:
            return Cell(field.name, raw, raw, Verdict.PARSED)
        if isinstance(raw, str) and raw in known:
            return Cell(field.name, raw, raw, Verdict.VERIFIED)
        return _listed(field, raw, 'SAID_VS_ACTUAL', said=raw, actual='不在給 agent 的清單裡', why='不在你的清單')
    if field.how is How.CITE:
        if not isinstance(raw, dict):
            return _listed(field, raw, 'CITE_BAD', said=raw, why='引用格式不是物件')
        if 'ids' not in raw:            # 只寫了字、沒給段落編號:該引沒引(A-29)
            return _listed(field, raw, 'CITE_NONE', said=raw)
        material = next((item for item in context.materials if item.material_id == raw.get('material_id')), None)
        if material is None:
            return _listed(field, raw, 'CITE_BAD', said=raw, why='交付代號不是這次提供的材料')
        accept = field.accept or Accept()
        try:
            cited = pick(material, raw.get('ids'), accept, row=row, item=raw)
        except (Miscited, TypeError, ValueError) as error:
            marks = (say('CITE_BAD', why=str(error)), *say_each('AGENT_WORDS', raw, 'words'))
            try:
                evidence = pick(material, raw.get('ids'), dataclasses.replace(accept, value=None), row=row, item=raw)
            except (Miscited, TypeError, ValueError):
                evidence = None
            if accept.value is not None and evidence is not None and evidence.parts:
                return Cell(field.name, raw, evidence, Verdict.LISTED, marks)
            return Cell(field.name, raw, raw, Verdict.LISTED, marks)
        if not cited.parts:
            return _listed(field, raw, 'CITE_NONE')
        return Cell(field.name, raw, cited, Verdict.VERIFIED)
    return _listed(field, raw, 'SAID_VS_ACTUAL', said=raw, actual='程式不認得這種核對方式', why='未知核對方式')


def _read_many(field: Field, raw: Any, context: Context) -> Cell | list[dict[str, Cell]]:
    if not isinstance(raw, list):
        if raw is None:
            if field.required:
                return _listed(field, raw, 'CITE_NONE')
            raw = []
        else:
            return _listed(field, raw, 'SAID_VS_ACTUAL', said=raw, actual='不是清單', why='必須提供清單')
    if not all(isinstance(item, dict) for item in raw):
        # 有一項不是物件:整格照 agent 原字列,不當資料讀(A-30)
        return _listed(field, raw, 'SAID_VS_ACTUAL', said=raw, actual='清單裡有不是物件的項目', why='每一項必須是物件')
    return [{child.name: _read_many(child, item.get(child.name), context) if child.many
             else _read_field(child, item.get(child.name), context, item) for child in field.sub} for item in raw]


def read(path: str, schema: Schema, context: Context) -> Sheet:
    try:
        with open(path, encoding='utf-8') as source:
            raw = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return Sheet({}, {}, say('SHEET_BROKEN', why=f'{type(error).__name__}: {error}'))
    if not isinstance(raw, dict):
        return Sheet({}, {}, say('SHEET_BROKEN', why='交件單最外層必須是 JSON 物件'))
    fields = {field.name: field for field in schema.fields}
    cells = {}
    for field in schema.fields:
        value = raw.get(field.name)
        if field.many:
            cells[field.name] = _read_many(field, value, context)
        else:
            cells[field.name] = _read_field(field, value, context)
    extra = {name: value for name, value in raw.items() if name not in fields}
    return Sheet(cells, extra)
