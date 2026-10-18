#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不准為單一平台打專屬補丁(2026-10-07 使用者定的規矩):讀頁、核對、填表、查應徵進度的程式碼裡,
註解和說明文字以外不准出現求職平台的名字或網址。要處理某個網站的寫法,就寫成對所有網站都成立的規則。
已知、還沒改成通用的幾處列在 KNOWN,每一處都有票;新加的會在這裡失敗。"""
import ast, os, re, unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools')
FILES = ('apply_tab.py', 'gate_apply.py', 'form_record.py', 'apply_run.py', 'reply_run.py', 'profile_sync.py',
         'next_step.py', 'autopilot.py', 'agent_report.py', 'chrome_door.py', 'delivery_state.py', 'form_plan.py', 'sync_profiles.py')
PLATFORM = re.compile(r'104|linkedin|lever|greenhouse|cake(?:resume)?\b|workday|ashby|wellfound|yourator|1111', re.I)
# 還沒改成通用的(檔名, 那一行開頭):平台履歷的平台對照表與履歷編號參數(#394 改成從登記的平台履歷網址推)
KNOWN = {('profile_sync.py', 'PLATFORMS = {'), ('profile_sync.py', "'104': ('104.com.tw',),"),
         ('profile_sync.py', "'cake': ("), ('profile_sync.py', "'yourator': ("), ('profile_sync.py', "'linkedin': ("),
         ('profile_sync.py', "ID_PARAMS = {'104': 'vno'}")}


def docstrings(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, 'value', None), ast.Constant):
                out.add(id(first.value))
    return out


def hits(name):
    with open(os.path.join(TOOLS, name), encoding='utf-8') as f:
        src = f.read()
    lines = src.splitlines()
    tree = ast.parse(src)
    skip = docstrings(tree)
    out = []
    for node in ast.walk(tree):
        text = (node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
                else node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                else node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else None)
        if text and PLATFORM.search(text):
            line = lines[node.lineno - 1].strip()
            if not any(f == name and line.startswith(head) for f, head in KNOWN):
                out.append(f'{name}:{node.lineno} {line[:100]}')
    return out


class NoPlatformPatches(unittest.TestCase):
    def test_workflow_code_names_no_job_platform(self):
        found = [h for f in FILES for h in hits(f)]
        self.assertEqual(found, [], '程式碼裡出現了求職平台的名字;改成對所有網站都成立的規則:\n' + '\n'.join(found))


if __name__ == '__main__':
    unittest.main()
