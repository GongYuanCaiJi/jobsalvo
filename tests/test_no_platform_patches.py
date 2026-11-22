#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import ast
import json
import os
import re
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORM = re.compile(
    r'(?<![\w.])(?:104|1111|linkedin|lever|greenhouse|cake(?:resume)?|workday|ashby|wellfound|yourator|gmail)(?![\w])',
    re.I,
)
AUTH_NAMES = {'VERIFY_WORDS', 'HUMAN_CHECK_WORDS', 'human_check'}
DOMAIN = re.compile(r'(?<![\w.@-])(?:[a-z0-9-]+(?:\.|\\\.)+)+(?:[a-z]{2,24}|xn--[a-z0-9-]+)(?![\w(-])', re.I)
# 只列專案自己安裝或呼叫的基礎設施(與保留的範例網域),從不列求職網站。
OWN_HOSTS = re.compile(
    r'(?:^|\.)(?:example(?:\.(?:com|org|net))?|invalid|test|localhost|127\.0\.0\.1|github\.com|'
    r'jsdelivr\.net|w3\.org|claude\.com|ts\.net|jina\.ai|astral\.sh|metadata\.google\.internal)$', re.I,
)
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
REGEX_CALLS = {'compile', 'search', 'match', 'fullmatch', 'findall', 'finditer', 'sub', 'subn', 'split'}
PAGE_FIELDS = {'url', 'status', 'text', 'via', 'title', 'http_status', 'errors'}
FIX = '網站判斷改成 agent 引段，程式用 numbered.pick 核對；遇登入或驗證牆走程式接手流程。'

PAST = [
    ('a59065c', "slash = re.fullmatch(r'(\\d{4})/(\\d{1,2})/(\\d{1,2})', value)"),
    ('baf14b4', "_APPLICANT_LINE = re.compile(r'class=\"num-applicants__[^\"]*\"[^>]*>(.*?)</', re.S)"),
    ('baf14b4', "_APPLICANT_COUNT = re.compile(r'applicant_count\\\\?\"\\s*:\\s*(\\d+)')"),
    ('baf14b4', "class PageResult:\n    url: str\n    applicants: str = ''"),
    ('27c116f', "class PageResult:\n    url: str\n    job_title: str = ''\n    company: str = ''"),
    ('27c116f', "x = PageResult(url, 'ok', text=text, via='lever', title=title, job_title=title)"),
    ('929c4d2', 'bad = check(jobs[url], download_reader=reader, board=board)'),
    ('#472', 'def download_parts(self, tab_id, page, picks, directory):\n    pass'),
    ('ab56bc3', "PAGE = 'a Gmail thread'"),
    ('d3a07ac', "API = 'https://www.104.com.tw/job/ajax/content/'"),
    ('da8621b', "EMBED = 'https://t.me/s/'"),
]


class ClosedSiteAssumptionContract(unittest.TestCase):
    def test_closed_rules_detect_domains_routes_hosts_and_schema_keys(self):
        samples = (
            "URL = 'https://jobs.vendor.com/company/acme/jobs/123'",
            "API = 'https://api.vendor.com/v1/jobs/search'",
            "if urlsplit(url).hostname == 'jobs.vendor.com': pass",
            "PATTERN = re.compile(r'jobs\\.vendor\\.com/companies/([^/]+)/jobs')",
            "def read(page): return page['datePosted']",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(python_hits('sample.py', sample))

    def test_a_file_name_in_a_regex_is_not_a_host_but_a_site_name_is(self):
        self.assertEqual(python_hits('check.py', "JS = r\"if(!/bc-upload\\.md/.test(text))bad.push(1)\""), [])
        site = python_hits('check.py', "JS = r\"if(/jobs\\.vendor\\.com/.test(u))go()\"")
        self.assertEqual({row['rule'] for row in site}, {'外部網域', '廠商網址路徑'})

    def test_scan_detects_schema_fields_used_as_javascript_properties(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'tools')
            os.makedirs(folder)
            with open(os.path.join(folder, 'profile.js'), 'w', encoding='utf-8') as file:
                file.write('const posted = page.datePosted;\n')

            findings = scan_findings(root)

        self.assertTrue(any(row['path'] == 'tools/profile.js' and row['rule'] == 'schema.org 欄位'
                            for row in findings), findings)

    def test_every_past_patch_example_is_detected(self):
        for commit, source in PAST:
            with self.subTest(commit=commit):
                self.assertTrue(python_hits('past.py', source))

    def test_scan_covers_tools_board_and_examples_without_a_file_allowlist(self):
        found = {os.path.relpath(path, ROOT).replace(os.sep, '/') for path in scanned()}
        self.assertIn('tools/page_fetch.py', found)
        self.assertIn('tools/board_check.py', found)
        self.assertIn('board/board.js', found)
        self.assertIn('examples/apply-rules.md', found)
        self.assertIn('tools/demo.py', found)
        self.assertIn('tools/profile_sync.py', found)
        self.assertIn('defaults/platform_hosts.json', found)

    def test_defaults_may_hold_only_host_name_lists(self):
        cases = {
            'platform_hosts.json': ('{"board": ["board.example", "jobs.board.example"]}', False),
            'query.json': ('{"board": {"vno": "id"}}', True),
            'path.json': ('{"board": ["board.example/job/ajax"]}', True),
            'rule.json': ('{"board": ["(?i)board\\\\.example"]}', True),
            'rules.py': ("ID_PARAMS = {'board': 'vno'}\n", True),
        }
        for name, (text, hit) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                os.makedirs(os.path.join(root, 'defaults'))
                with open(os.path.join(root, 'defaults', name), 'w', encoding='utf-8') as file:
                    file.write(text)
                rules = {row['rule'] for row in scan_findings(root) if row['path'] == f'defaults/{name}'}
                self.assertEqual(rules, {DEFAULTS_SHAPE} if hit else set())

    def test_tools_board_and_examples_have_no_hits(self):
        found = scan_findings()
        detail = '\n'.join(f"{row['path']}:{row['line']} {row['rule']}: {row['source']}" for row in found)
        self.assertEqual(found, [], f'命中只能改程式;本來就不違規的改掃描規則;{FIX}\n{detail}')

    def test_failure_points_to_the_shared_agent_quote_and_number_check(self):
        self.assertIn('agent 引段', FIX)
        self.assertIn('numbered.pick', FIX)


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
        return fields - PAGE_FIELDS
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


if __name__ == '__main__':
    unittest.main()
