"""行為清單 → 驗收斷言的對照表:掃 tests/test_accept_*.py 裡每個帶編號的斷言,寫 docs/research/601-behaviors-map.md。

對照欄「入口」的每一條要至少一個斷言、「既有」的要寫明且找得到測試,缺號就結束碼 1(行為清單旁的對照表由這支產生,不手改)。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEHAVIORS = os.path.join(ROOT, 'docs', 'research', '601-behaviors.md')
MAP = os.path.join(ROOT, 'docs', 'research', '601-behaviors-map.md')
TESTS = os.path.join(ROOT, 'tests')
ID = r'[ASFPJ]-\d\d'


def rows():
    """對照欄每一列 (編號, 誰驗),照清單順序。"""
    with open(BEHAVIORS, encoding='utf-8') as source:
        text = source.read()
    table = text.split('## 對照欄', 1)[1].split('\n## ', 1)[0]
    return [(m.group(1), m.group(2).strip()) for m in re.finditer(rf'^\| ({ID}) \|[^|]*\|([^|]*)\|', table, re.M)]


def existing(who):
    """誰驗寫的「既有」測試 `檔名::測試名`;回 (寫到的, 找不到的)。"""
    refs = re.findall(r'`(tests/[\w/]+\.py)::(\w+)`', who)
    gone = []
    for path, name in refs:
        full = os.path.join(ROOT, path)
        text = open(full, encoding='utf-8').read() if os.path.isfile(full) else ''
        if not re.search(rf'^\s*def {name}\(', text, re.M):
            gone.append(f'{path}::{name}')
    return refs, gone


def assertions():
    """{編號: [(檔名, 類別)]}:斷言呼叫那一行寫到的編號(expect(self, 'X-NN …', …),含依情況選編號的寫法)。"""
    found = {}
    for name in sorted(os.listdir(TESTS)):
        if not (name.startswith('test_accept_') and name.endswith('.py')):
            continue
        cls = ''
        with open(os.path.join(TESTS, name), encoding='utf-8') as source:
            for line in source:
                head = re.match(r'class (\w+)\(', line)
                cls = head.group(1) if head else cls
                call = re.search(r'(?:expect\(self, |assert\w*\()(.*)', line)
                for behavior in re.findall(ID, call.group(1)) if call else ():
                    where = (name, cls)
                    if where not in found.setdefault(behavior, []):
                        found[behavior].append(where)
    return found


def main():
    found = assertions()
    lines = ['# 行為清單 → 驗收斷言對照表', '',
             '由 `tests/behavior_map.py` 產生,不手改。對照欄每一列:「入口」列出斷言它的驗收測試,「既有」列出它指到的測試,'
             '其他(lint、review、真 agent)照對照欄寫。', '', '| 編號 | 誰驗 | 斷言所在 |', '|---|---|---|']
    missing = []
    for behavior, who in rows():
        cells = []
        if '入口' in who:
            where = found.get(behavior)
            cells.append('、'.join(f'`{name}` {cls}' for name, cls in where) if where else '**缺**')
            missing += [] if where else [behavior]
        if '既有' in who:
            refs, gone = existing(who)
            cells.append('、'.join(f'`{path}::{name}`' for path, name in refs) if refs else '**沒寫測試名**')
            missing += [f'{behavior}({ref} 不存在)' for ref in gone] + ([] if refs else [f'{behavior}(既有沒寫測試名)'])
        kinds = re.sub(r'[(（].*?[)）]', '', who)
        lines.append(f'| {behavior} | {kinds} | {"；".join(cells) or "—"} |')
    with open(MAP, 'w', encoding='utf-8') as target:
        target.write('\n'.join(lines) + '\n')
    if missing:
        print('沒有斷言的行為:' + '、'.join(missing), file=sys.stderr)
        return 1
    print(f'{len(lines) - 6} 條行為都對到')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
