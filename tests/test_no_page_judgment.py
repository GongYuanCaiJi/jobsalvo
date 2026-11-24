#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守住四件網站任務不依頁面、網站或網址自行判斷。"""
from __future__ import annotations

import ast
import json
import os
import re
import runpy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / 'tools'
BOARD = ROOT / 'board'
OWNERS = {
    ('tools/web/fetch.py', 'public_address', 'R1'): '#60 第 1 點:程式抓頁只准公開網址(安全防護)',
    ('tools/web/outside.py', 'Real.raw_tabs', 'R3'): 'ADR-0011:工作區讀頁唯一入口(真瀏覽器那一側)',
    ('tools/web/snap.py', 'take', 'R3'): 'ADR-0011:agent 叫 jobsalvo-before / jobsalvo-after',
    ('tools/web/round.py', 'run', 'R3'): '#591:查應徵進度維持程式讀 agent 開好的頁(交件那一刻讀工作區)',
    ('tools/web/cite.py', 'hand_over', 'R3'): 'ADR-0009、#601「驗證碼、登入頁的既有接手流程照舊」:交接前重讀工作區逐字核對',
    ('tools/web/fetch.py', '_ego_now', 'R3'): 'ADR-0011:直接抓與閱讀代理讀不到時,共用核心讀 agent 瀏覽器已開的頁',
    ('tools/web/fetch.py', 'screenshot', 'R3'): '#601 考試證據:每頁截圖結果與失敗都保留',
    ('tools/chrome_door.py', 'EgoDoor.read_profile', 'R3'): 'ADR-0011:只讀 agent 開啟的平台履歷頁',
    ('tools/chrome_door.py', 'EgoDoor.human_action', 'R3'): 'ADR-0009:交接引文逐字核對 agent 開啟的同一分頁',
    ('tools/find.py', 'read_urls', 'R3'): '#569「程式讀 agent 交回的網址」',
    ('tools/page_fetch.py', 'main', 'R3'): 'ADR-0011:agent 在找缺搜尋時叫的讀網址工具',
    ('tools/form_plan.py', 'main', 'R3'): '#569「程式配好答案、自己填的部分不變」',
    ('tools/customize.py', '_job_page_text', 'R3'): '#449 客製讀職缺文字,不在四件網站任務內',
    ('tools/cut_tailor.py', 'main', 'R3'): '客製檔讀職缺文字,不在四件網站任務內',
    ('tools/chrome_door.py', 'EgoDoor.print_pdf', 'R3'): '印 PDF,不讀頁內容',
    ('tools/dev_browser.py', 'Ego.__init__', 'R3'): '開發用瀏覽器,不在任務流程內',
    ('tools/card.py', 'legacy_score_name_parts', 'R2'): '卡片舊標題顯示整理,不讀職缺頁',
    ('tools/card.py', '<module>', 'R2'): '公司名稱法律字尾正規式,不讀職缺頁',
    ('tools/card.py', '_company', 'R2'): '舊卡標題分組,不讀職缺頁',
    ('tools/ego-browser', '_action_summary', 'R2'): '#569每次瀏覽器指令後截圖,摘要只讀指令',
    ('tools/form_plan.py', '<module>', 'R2'): '#569程式配答案,判斷答案語言不看頁面',
}
OWNED_PAGE_READS = set()   # 逐字引文核對允許讀的節點(檔, 函式, 起行, 起欄, 迄行, 迄欄);目前沒有
URL_FUNCS = {'urlsplit', 'urlparse'}
URL_ATTRS = {'hostname', 'netloc', 'path', 'query'}
READ_NAMES = {'read_workspace', 'fetch_url', 'read_workspace_pages', 'fetch', 'fetch_many',
              'open_for_agent', 'human_action', 'recheck_page', 'follow_page',
              'read_profile_page', 'read_pages'}
PAGE_DOM_FIELDS = {'parts', '_parts', '_dom', 'raw_html'}
PAGE_CONTEXT_WORDS = {'after', 'before', 'body', 'capture', 'dom', 'html', 'jd', 'page', 'snapshot', 'website'}
PAGE_STATUS_WORDS = ('下架', '職缺已關', '職缺已滿', '額滿', '已關閉')

PLATFORM = re.compile(
    r'(?<![\w.])(?:104|1111|linkedin|lever|greenhouse|cake(?:resume)?|workday|ashby|wellfound|yourator|gmail)(?![\w])',
    re.I,
)

AUTH_NAMES = {'VERIFY_WORDS', 'HUMAN_CHECK_WORDS', 'human_check'}

DOMAIN = re.compile(r'(?<![\w.@-])(?:[a-z0-9-]+(?:\.|\\\.)+)+(?:[a-z]{2,24}|xn--[a-z0-9-]+)(?![\w(-])', re.I)

COMMON_TLDS = {'ai', 'app', 'biz', 'ca', 'co', 'com', 'de', 'dev', 'edu', 'fr', 'gov', 'info', 'io', 'jp',
               'me', 'net', 'org', 'tw', 'uk'}

URL_PATH = re.compile(r'(?P<host>(?:[a-z0-9-]+(?:\.|\\\.)+)+[a-z]{2,})(?P<path>/[^\s\'"{}]*)', re.I)

API_HOST = re.compile(r'^api(?:-[a-z0-9-]+)?\.', re.I)

API_PATH = re.compile(r'(?:^|/)(?:api|ajax|graphql|rest|v\d+)(?:/|\?|$)', re.I)

VENDOR_PATHS = {'apply', 'api', 'ajax', 'careers', 'company', 'companies', 'graphql', 'job', 'jobs', 'mail',
                'profile', 'search', 'view'}

JS_STRING = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")

JS_NAME = re.compile(r'(?<![A-Za-z0-9_$])[A-Za-z_$][A-Za-z0-9_$]*(?![A-Za-z0-9_$])')

SCHEMA = re.compile(r'ld\+json|\bJobPosting\b|\bdatePosted\b|og:title|schema\.org', re.I)

SITE_REGEX = re.compile(r'class=|data-[a-z]|applicant_count|num-applicants|\\d\{4\}\)?\\?[/./年]|\\d\S{0,8}[月日]')

TRANSFER = re.compile(r'\b(?:download_reader|download_parts|download_attachments|fetch_file)\b')

HOST_ATTRS = {'hostname', 'netloc'}

LOOPBACK = {'localhost', '127.0.0.1', '::1', ''}
OWN_HOSTS = re.compile(
    r'(?:^|\.)(?:example(?:\.(?:com|org|net))?|invalid|test|localhost|127\.0\.0\.1|github\.com|'
    r'jsdelivr\.net|w3\.org|claude\.com|ts\.net|jina\.ai|er-api\.com|exchangerate-api\.com|astral\.sh|metadata\.google\.internal)$', re.I,
)

REGEX_CALLS = {'compile', 'search', 'match', 'fullmatch', 'findall', 'finditer', 'sub', 'subn', 'split'}

LEGACY_PAGE_FIELDS = {'url', 'status', 'text', 'via', 'title', 'http_status', 'errors'}

FIX = '網站判斷改成 agent 引段，程式用 numbered.pick 核對；遇登入或驗證牆走程式接手流程。'

def _text_rules(text, names=False):
    source = text.replace(r'\.', '.')
    rules = set()
    if PLATFORM.search(source):
        rules.add('平台或信箱名稱')
    if not names:
        urls = _external_urls(text)
        if urls:
            rules.add('外部網域')
        if any(url['path'] for url in urls):
            rules.add('廠商網址路徑')
        if any(API_HOST.search(url['host']) or API_PATH.search(url['path']) for url in urls):
            rules.add('外部 API 端點')
    if SCHEMA.search(source):
        rules.add('schema.org 欄位')
    if TRANSFER.search(source):
        rules.add('平台附件下載介面')
    return rules

def _own_host(value):
    host = value.replace(r'\.', '.').casefold()
    return bool(OWN_HOSTS.search(host))

def _external_urls(source):
    urls = []
    for match in URL_PATH.finditer(source):
        host = match.group('host')
        plain_host = host.replace(r'\.', '.')
        path = match.group('path')
        scheme = bool(re.search(r'https?://(?:www\.)?$', source[:match.start()], re.I))
        escaped = r'\.' in host
        route = path.lstrip('/').split('/', 1)[0].casefold()
        common_tld = plain_host.rsplit('.', 1)[-1].casefold() in COMMON_TLDS
        if _own_host(host) or not (scheme or common_tld and (escaped or plain_host.count('.') >= 2
                                                              or route in VENDOR_PATHS)):
            continue
        urls.append({'host': plain_host, 'path': path})
    for match in DOMAIN.finditer(source):
        host = match.group(0).replace(r'\.', '.')
        suffix = host.rsplit('.', 1)[-1].casefold()
        if suffix not in COMMON_TLDS or _own_host(host):
            continue
        root_url = bool(re.search(r'https?://(?:www\.)?$', source[:match.start()], re.I))
        if source.strip().casefold() == match.group(0).casefold() or root_url:
            if not any(url['host'].casefold() == host.casefold() for url in urls):
                urls.append({'host': host, 'path': ''})
    return urls

def _docstrings(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, 'value', None), ast.Constant):
                out.add(id(first.value))
    return out

def _root_name(node):
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.value if isinstance(node, (ast.Attribute, ast.Subscript)) else node.func
    return node.id if isinstance(node, ast.Name) else ''


def _call_name(node):
    if not isinstance(node, ast.Call):
        return ''
    return node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''


def _request_path_parser(node):
    if _call_name(node) not in URL_FUNCS or not node.args:
        return False
    value = node.args[0]
    return (isinstance(value, ast.Attribute) and value.attr == 'path'
            and isinstance(value.value, ast.Name) and value.value.id == 'self')


def _url_component(node, parsed_names):
    if not isinstance(node, ast.Attribute) or node.attr not in URL_ATTRS:
        return False
    if isinstance(node.value, ast.Call):
        return _call_name(node.value) in URL_FUNCS and not _request_path_parser(node.value)
    return _root_name(node.value) in parsed_names


def _host_compare(node):
    if not isinstance(node, ast.Compare):
        return False
    sides = [node.left, *node.comparators]
    if not any(
        isinstance(side, ast.Attribute) and side.attr in HOST_ATTRS
        or isinstance(side, ast.Call) and isinstance(side.func, ast.Attribute)
        and isinstance(side.func.value, ast.Attribute) and side.func.value.attr in HOST_ATTRS
        for side in sides
    ):
        return False
    values = [item.value for side in sides for item in ast.walk(side)
              if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    return any(value.casefold() not in LOOPBACK for value in values)


def _url_component_compare(node, parsed_names, helpers):
    if not isinstance(node, ast.Compare):
        return False
    return any(
        _url_component(item, parsed_names)
        or isinstance(item, ast.Call) and _call_name(item) in helpers
        for side in [node.left, *node.comparators] for item in ast.walk(side)
    )


def _page_context(node, function):
    names = {function.casefold()}
    names.update(item.id.casefold() for item in ast.walk(node) if isinstance(item, ast.Name))
    names.update(item.attr.casefold() for item in ast.walk(node) if isinstance(item, ast.Attribute))
    words = {word for name in names for word in re.split(r'[^a-z0-9]+', name) if word}
    return bool(words & PAGE_CONTEXT_WORDS)


def _page_status_text(value):
    return any(word in value for word in PAGE_STATUS_WORDS)


def _has_cjk(value):
    return any(ord(char) > 127 for char in value)


def _whole_value_regex(value):
    return value.startswith('^') and value.rstrip().endswith('$') and _has_cjk(value)


def _r2_context(node, function, value):
    if _page_context(node, function) or _page_status_text(value):
        return True
    if (isinstance(node, ast.Call) and _call_name(node) == 'startswith'
            and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
            and node.func.value.id.casefold() in {'said', 'claim', 'agent_said'}
            and re.search(r'(?:抓不到|找不到|沒有|無法).{0,12}(?:JD|職缺|頁面)', value, re.I)):
        return True
    return (any(isinstance(item, ast.Name) and item.id.casefold() == 'text' for item in ast.walk(node))
            and _has_cjk(value))

def _site_regex(node):
    if not (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in REGEX_CALLS
        and isinstance(node.func.value, ast.Name) and node.func.value.id == 're' and node.args
    ):
        return False
    pattern = node.args[0]
    return isinstance(pattern, ast.Constant) and isinstance(pattern.value, str) and SITE_REGEX.search(pattern.value)

def _extra_page_fields(node):
    if isinstance(node, ast.ClassDef) and node.name == 'PageResult':
        fields = {item.target.id for item in node.body
                  if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}
        return fields - LEGACY_PAGE_FIELDS
    return set()

def _finding(path, line, source, rule):
    return {'path': path, 'line': line, 'source': source.strip(), 'rule': rule}

def python_hits(path, source):
    tree = ast.parse(source)
    lines = source.splitlines()
    skip = _docstrings(tree)
    found = {}

    def add(line, rules):
        for rule in rules:
            found[(line, rule)] = _finding(path, line, lines[line - 1], rule)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            add(node.lineno, _text_rules(node.value))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Name, ast.Attribute,
                               ast.arg, ast.keyword)):
            name = (getattr(node, 'name', None) or getattr(node, 'id', None) or getattr(node, 'attr', None)
                    or getattr(node, 'arg', None))
            if name:
                add(node.lineno, _text_rules(name, names=True))
                if name in AUTH_NAMES:
                    add(node.lineno, {'頁面登入驗證字樣判斷'})
        if _host_compare(node):
            add(node.lineno, {'主機名與字面值比較'})
        if _site_regex(node):
            add(node.lineno, {'網站標記或日期規則'})
        if _extra_page_fields(node):
            add(node.lineno, {'讀頁結果含網站專用欄位'})
    return list(found.values())

def _text_hits(path, source):
    ext = os.path.splitext(path)[1]
    if ext in ('.html', '.md'):
        source = re.sub(r'<!--.*?-->', '', source, flags=re.S)
    if ext in ('.css', '.js'):
        source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    found = {}
    for line_no, line in enumerate(source.splitlines(), 1):
        if ext == '.js':
            line = re.sub(r'(?<![:\'\"])//.*$', '', line)
            code = JS_STRING.sub('', line)
            texts = ([(match.group(0)[1:-1], False) for match in JS_STRING.finditer(line)]
                     + [(match.group(0), True) for match in JS_NAME.finditer(code)])
        elif ext == '.sh' and line.lstrip().startswith('#'):
            texts = []
        else:
            texts = [(line, False)]
        for text, names in texts:
            for rule in _text_rules(text, names=names):
                found[(line_no, rule)] = _finding(path, line_no, line, rule)
    return list(found.values())

DEFAULTS_SHAPE = '出廠預設只准平台→主機名清單'

HOSTNAME = re.compile(r'^[a-z0-9-]+(?:\.[a-z0-9-]+)+$')

def _defaults_hits(path, source):
    """defaults/ 是使用者可編輯清單的出廠預設:只准 {平台: [主機名, …]},主機名不帶路徑、參數或規則。
    合格的就是資料、不再逐字掃;不合格的整檔算一筆命中。"""
    try:
        data = json.loads(source) if path.endswith('.json') else None
    except ValueError:
        data = None
    ok = (isinstance(data, dict) and data and all(
        isinstance(name, str) and name and isinstance(hosts, list) and hosts
        and all(isinstance(host, str) and HOSTNAME.match(host) for host in hosts)
        for name, hosts in data.items()))
    return [] if ok else [_finding(path, 1, source.partition('\n')[0], DEFAULTS_SHAPE)]

def scanned(root=ROOT):
    defaults = os.path.join(root, 'defaults')
    for directory, subdirs, names in os.walk(defaults):
        subdirs[:] = sorted(subdirs)
        for name in sorted(names):
            yield os.path.join(directory, name)
    for base in ('tools', 'board', 'examples'):
        folder = os.path.join(root, base)
        if not os.path.isdir(folder):
            continue
        for directory, subdirs, names in os.walk(folder):
            subdirs[:] = sorted(name for name in subdirs if name != '__pycache__')
            for name in sorted(names):
                if '__pycache__' in directory:
                    continue
                path = os.path.join(directory, name)
                if name.endswith(('.py', '.js', '.md', '.html', '.css', '.json', '.sh')):
                    yield path
                elif not os.path.splitext(name)[1] and os.access(path, os.X_OK):
                    with open(path, 'rb') as file:
                        if file.readline().startswith(b'#!'):
                            yield path

def scan_findings(root=ROOT):
    found = []
    for path in scanned(root):
        relative = os.path.relpath(path, root).replace(os.sep, '/')
        with open(path, encoding='utf-8') as file:
            source = file.read()
        if relative.startswith('defaults/'):
            found.extend(_defaults_hits(relative, source))
            continue
        is_python = path.endswith('.py') or source.startswith('#!') and 'python' in source.partition('\n')[0]
        found.extend(python_hits(relative, source) if is_python else _text_hits(relative, source))
    return found


def _py_files(root):
    return sorted(path for path in root.rglob('*.py') if '__pycache__' not in path.parts)


def task_entries():
    """從看板 run_argv 的任務分支推導四個實際入口。"""
    source = (TOOLS / 'board_server.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    runner = next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.name == 'run_argv')

    def scripts_in(node):
        return {item.value for item in ast.walk(node) if isinstance(item, ast.Constant)
                and isinstance(item.value, str) and item.value.endswith('.py') and item.value != 'job_fake.py'}

    found = {}
    for node in ast.walk(runner):
        if not isinstance(node, ast.If):
            continue
        kinds = {side.value for compare in ast.walk(node.test) if isinstance(compare, ast.Compare)
                 and any(isinstance(part, ast.Name) and part.id == 'kind' for part in ast.walk(compare))
                 for side in [compare.left, *compare.comparators]
                 if isinstance(side, ast.Constant) and isinstance(side.value, str)}
        for kind in kinds & {'replies', 'apply', 'sync'}:
            candidates = scripts_in(node)
            if candidates:
                found[kind] = candidates

    # Find is run through run_argv's fallback branch; locate its direct entry.
    fallback_assignments = [node for node in ast.walk(runner) if isinstance(node, (ast.Assign, ast.AnnAssign))
                            and 'find.py' in scripts_in(node)]
    find_candidates = scripts_in(fallback_assignments[-1]) if fallback_assignments else set()
    entries = {
        'research': find_candidates,
        'replies': found.get('replies', set()),
        'apply': found.get('apply', set()),
        'sync': found.get('sync', set()),
    }
    paths = []
    for candidates in entries.values():
        if len(candidates) != 1:
            raise AssertionError(f'run_argv 任務入口不唯一: {sorted(candidates)}')
        paths.append('tools/' + next(iter(candidates)))
    return tuple(sorted(paths))


def _module_imports(path, tree):
    """收集 tools 內可解析的本地 import，包含函式內的延遲 import。"""
    files = _py_files(TOOLS)
    modules = {p.relative_to(TOOLS).with_suffix('').as_posix().replace('/', '.'): p for p in files}
    # Flat imports are also used because tools/ is placed on sys.path by the CLI.
    modules.update({p.stem: p for p in files if len(p.relative_to(TOOLS).parts) == 1})
    rel = path.relative_to(TOOLS).with_suffix('')
    package = list(rel.parts[:-1]) if path.name != '__init__.py' else list(rel.parts[:-1])
    found = set()

    def add_module(name):
        module = modules.get(name)
        if module:
            found.add(module)
            parent = module.parent
            while parent != TOOLS:
                init = parent / '__init__.py'
                if init.is_file():
                    found.add(init)
                parent = parent.parent

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_module(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:max(0, len(package) - node.level + 1)]
                if node.module:
                    base += node.module.split('.')
                candidate = '.'.join(base) if base else ''
            else:
                candidate = node.module or ''
            add_module(candidate)
            for alias in node.names:
                add_module('.'.join(filter(None, (candidate, alias.name))))
                add_module(alias.name)
    return found


def closure(entries):
    pending = [ROOT / entry for entry in entries]
    seen = set()
    while pending:
        path = pending.pop().resolve()
        if path in seen or not path.is_file() or path.suffix != '.py':
            continue
        seen.add(path)
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError):
            continue
        pending.extend(_module_imports(path, tree) - seen)
    return tuple(sorted('tools/' + p.relative_to(TOOLS).as_posix() for p in seen))


def _functions(tree):
    """yield node and dotted lexical function name, including class methods."""
    def walk(node, prefix=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f'{prefix}.{child.name}' if prefix else child.name
                yield child, name
                yield from walk(child, name)
            elif isinstance(child, ast.ClassDef):
                name = f'{prefix}.{child.name}' if prefix else child.name
                yield from walk(child, name)
            else:
                yield from walk(child, prefix)
    yield from walk(tree)


def _r1_r2_r5(path, source):
    tree = ast.parse(source)
    funcs = {id(node): name for node, name in _functions(tree)}
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    parsed_names = set()
    def collect_parsed_names(target, value):
        if _call_name(value) in URL_FUNCS and not _request_path_parser(value):
            parsed_names.update(item.id for item in ast.walk(target) if isinstance(item, ast.Name))
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for target_item, value_item in zip(target.elts, value.elts):
                collect_parsed_names(target_item, value_item)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            continue
        value = node.value
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            collect_parsed_names(target, value)
    helpers = {
        fn.name for fn, _name in _functions(tree)
        if any(_url_component(item, parsed_names) for item in ast.walk(fn)
               if isinstance(item, ast.Attribute))
    }
    found = set()
    def add(rule, node):
        # Find the nearest lexical function containing the node.
        current = node
        while id(current) not in funcs and current in parents:
            current = parents[current]
        function = funcs.get(id(current), '<module>')
        key = (path, function, rule, node.lineno)
        if (path, function, rule) not in OWNERS:
            found.add(key)
    def context(node):
        current = node
        while id(current) not in funcs and current in parents:
            current = parents[current]
        return funcs.get(id(current), '<module>')

    def target_names(target):
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, (ast.Tuple, ast.List)):
            return set().union(*(target_names(item) for item in target.elts))
        return set()

    derived_by_scope = {}
    assignments = [node for node in ast.walk(tree)
                   if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))]
    changed = True
    while changed:
        changed = False
        for node in assignments:
            scope = context(node)
            derived = derived_by_scope.setdefault(scope, set())
            value = node.value
            if value is None:
                continue
            contains_url_component = any(_url_component(item, parsed_names)
                                         for item in ast.walk(value)
                                         if isinstance(item, ast.Attribute))
            carries_derived_key = any(isinstance(item, ast.Name) and item.id in derived
                                      for item in ast.walk(value))
            if not (contains_url_component or carries_derived_key):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = set().union(*(target_names(target) for target in targets))
            if not names <= derived:
                derived.update(names)
                changed = True

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ''
            if name in READ_NAMES:
                add('R3', node)
        if isinstance(node, ast.Attribute):
            if node.attr in READ_NAMES:
                add('R3', node)
            if node.attr in PAGE_DOM_FIELDS and not path.startswith('tools/web/'):
                span = (path, context(node), node.lineno, node.col_offset,
                        node.end_lineno, node.end_col_offset)
                if span not in OWNED_PAGE_READS:
                    add('R5', node)
        if isinstance(node, ast.Name) and node.id in READ_NAMES:
            add('R3', node)
        if isinstance(node, ast.Compare) and node.ops and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            if any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops) and not all(isinstance(c, ast.Constant) for c in node.comparators):
                if (any(ord(ch) > 127 or ch.isspace() for ch in node.left.value)
                        and _r2_context(node, context(node), node.left.value)):
                    add('R2', node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {'startswith','endswith','find','index'}:
            for arg in node.args:
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        and any(ord(ch) > 127 or ch.isspace() for ch in arg.value)
                        and _r2_context(node, context(node), arg.value)):
                    add('R2', node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 're' and node.func.attr in REGEX_CALLS and node.args:
            arg = node.args[0]
            if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    and any(ord(ch) > 127 or ch.isspace() for ch in arg.value)
                    and (_page_context(node, context(node)) or _whole_value_regex(arg.value))):
                add('R2', node)
        tainted_keys = derived_by_scope.get(context(node), set())
        key_compare = isinstance(node, ast.Compare) and any(
            isinstance(item, ast.Name) and item.id in tainted_keys
            for side in [node.left, *node.comparators] for item in ast.walk(side))
        if _url_component_compare(node, parsed_names, helpers) or key_compare:
            add('R1', node)
    return found


def hits(paths_r125, paths_r34):
    found = set()
    for rel in paths_r125:
        path = ROOT / rel
        found |= _r1_r2_r5(rel, path.read_text(encoding='utf-8'))
    # R3 and the legacy platform assumptions cover tools/ and board/ in full.
    for rel in paths_r34:
        path = ROOT / rel
        if path.suffix == '.py' or path.name == 'ego-browser':
            source = path.read_text(encoding='utf-8')
            found |= {row for row in _r1_r2_r5(rel, source) if row[2] == 'R3'}
        if path.suffix == '.json' and rel.startswith('defaults/'):
            continue
    for row in scan_findings(ROOT):
        rel = row['path']
        if rel.startswith('defaults/') or rel.startswith('tools/') or rel.startswith('board/'):
            found.add((rel, '<file>', 'R4', row['line']))
    # R3 must cover all tools/ and board/ regardless of task closure.
    return found


def _all_files():
    for base in (TOOLS, BOARD):
        if base.exists():
            for path in sorted(base.rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts and (path.suffix in {'.py','.js','.md','.html','.css','.json','.sh'} or os.access(path, os.X_OK) and not path.suffix):
                    yield path


def findings():
    """看板每個任務入口的 import 閉包掃 R1/R2/R5,tools/、board/ 全部檔掃 R3/R4。"""
    paths_r34 = tuple(path.relative_to(ROOT).as_posix() for path in _all_files())
    return sorted(hits(closure(task_entries()), paths_r34))


def report():
    findings_ = findings()
    details = [f'{path}:{line} {function} {rule}' for path, function, rule, line in findings_]
    totals = ', '.join(f'{rule}={sum(row[2] == rule for row in findings_)}' for rule in ('R1','R2','R3','R4','R5'))
    details.append(f'合計 {len(findings_)} 筆 ({totals})')
    return '\n'.join(details)


class NoPageJudgment(unittest.TestCase):
    def test_scanner_probe_must_hit_reintroduced_find_patches(self):
        cases = runpy.run_path(str(ROOT / '.audit' / 'scanner-probe.py'))['CASES']
        required = {
            'agent_claim_and_page_status': 'R2',
            'normalized_url_keys': 'R1',
            'second_page_parts_read': 'R5',
        }
        for name, rule in required.items():
            with self.subTest(name=name):
                self.assertIn(rule, {row[2] for row in _r1_r2_r5('tools/find.py', cases[name])})

    def test_scanner_probe_cases_keep_matching_page_decisions(self):
        cases = {
            'same_host': (
                "from urllib.parse import urlparse\n"
                "def f(a,b):\n"
                "    return urlparse(a).hostname == urlparse(b).hostname\n",
                'R1',
            ),
            'netloc_eq': (
                "from urllib.parse import urlsplit\n"
                "def f(a,b):\n"
                "    return urlsplit(a).netloc == urlsplit(b).netloc\n",
                'R1',
            ),
            'host_key': (
                "def host_key(u):\n"
                "    from urllib.parse import urlsplit\n"
                "    return urlsplit(u).hostname\n"
                "def f(a,b):\n"
                "    return host_key(a) != host_key(b)\n",
                'R1',
            ),
            'zh_in_text': (
                "def f(text):\n"
                "    return '已下架' in text\n",
                'R2',
            ),
        }
        for name, (source, rule) in cases.items():
            with self.subTest(name=name):
                self.assertEqual({rule}, {row[2] for row in _r1_r2_r5('tools/x.py', source)})

    def test_rules_catch_past_patches(self):
        fixtures = ROOT / 'tests' / 'fixtures' / 'past_patches'
        expected = {'r1_card_url.patch':'R1', 'r2_capture_delta.patch':'R2', 'r2_placeholder.patch':'R2',
                    'r3_verify_stop.patch':'R3', 'r5_page_parts.patch':'R5'}
        for path in sorted(fixtures.glob('r4_*.patch')):
            expected[path.name] = 'R4'
        self.assertEqual(len(expected), 16)
        self.assertEqual({path.name for path in fixtures.glob('*.patch')}, set(expected))
        for name, rule in expected.items():
            with self.subTest(name=name):
                path = fixtures / name
                source = path.read_text(encoding='utf-8')
                if rule == 'R4':
                    detected = bool(python_hits(path.name, source))
                else:
                    detected = any(row[2] == rule for row in _r1_r2_r5(path.name, source))
                self.assertTrue(detected, source)

    def test_scanner_ignores_board_routing_and_general_text_processing(self):
        source = '''
from urllib.parse import parse_qs, urlparse

def route(self, card):
    query = parse_qs(urlparse(self.path).query)
    host_for_display = urlparse(card['url']).netloc
    return query, host_for_display

def note_section(text):
    return [line for line in text.splitlines() if line.startswith('## ')]

def tokenize(text):
    return re.findall(r'[一-鿿]+', text)

def interview_category(category):
    return '反問' in category
'''
        self.assertEqual(set(), _r1_r2_r5('fixture.py', source))

    def test_exact_citation_reads_have_clause_scoped_owners(self):
        for path in ('tools/find.py',):
            with self.subTest(path=path):
                source = (ROOT / path).read_text(encoding='utf-8')
                self.assertFalse([row for row in _r1_r2_r5(path, source) if row[2] == 'R5'])

    def test_every_task_entry_closure_has_no_page_judgment(self):
        # 找缺、查應徵進度、填表、同步的完成條件「掃描器 0 命中」:跟 --report 同一份,入口從看板 run_argv 推導
        self.assertLessEqual({'tools/find.py', 'tools/fill.py', 'tools/progress.py', 'tools/sync.py'}, set(task_entries()))
        self.assertEqual([], [f'{path}:{line} {function} {rule}' for path, function, rule, line in findings()], FIX)

    def test_every_owner_still_names_existing_code(self):
        # 擁有者表指到已刪或改名的函式就是過期:放行的範圍跟著消失的程式漂走
        for path, function, _rule in OWNERS:
            with self.subTest(path=path, function=function):
                source = (ROOT / path).read_text(encoding='utf-8') if (ROOT / path).exists() else ''
                self.assertTrue(function == '<module>' or re.search(rf'def {re.escape(function.split(".")[-1])}\b', source))

    def test_task_import_closure_includes_package_initializers(self):
        self.assertIn('tools/web/__init__.py', closure(task_entries()))


if __name__ == '__main__':
    if '--report' in sys.argv:
        output = report()
        print(output)
    else:
        unittest.main()
