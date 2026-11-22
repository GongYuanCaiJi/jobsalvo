"""產生 tests/page_noise_words.json:以前程式拿來判斷頁面的寫死字與格式(#776)。

來源三處,全是程式碼與文件,不含任何真平台頁面:
  1. c3dd8322 以後 tools/ 刪掉的程式行裡的字串;
  2. c3dd8322 當時用現在的 .semgrep/ 規則掃出的違規行(tools/ 底下);
  3. 注錯驗尺的補丁(tests/fixtures/past_patches 加的行、docs/research/601-exam.md 補丁表「注入什麼」那一欄)。
字串裡的正規式再取樣成一個吻合的樣子(日期、人數、class 名)。
用法:python3 tests/page_noise_extract.py(需要 git、uvx;結果進版控,平常不用重跑)。
"""
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from re import _parser as sre

START = 'c3dd8322'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'tests', 'page_noise_words.json')
LITERAL = re.compile(r"""(?<![\w])[rbfRBF]{0,2}(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
PLAIN_WORD = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
MIN, MAX = 2, 160


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, cwd=ROOT, **kwargs).stdout


def sample(pattern, rng):
    """取樣一個吻合正規式的字;寫不出來的(反向引用、環視)回 None。"""
    def one(node):
        op, arg = node
        name = str(op)
        if name == 'LITERAL':
            return chr(arg)
        if name == 'NOT_LITERAL':
            return 'x'
        if name == 'ANY':
            return 'a'
        if name == 'IN':
            for kind, value in arg:
                if str(kind) == 'LITERAL':
                    return chr(value)
                if str(kind) == 'RANGE':
                    return chr(rng.randint(*value))
                if str(kind) == 'CATEGORY':
                    return {'CATEGORY_DIGIT': '7', 'CATEGORY_SPACE': ' ', 'CATEGORY_WORD': 'w'}.get(str(value), 'a')
            return 'a'
        if name in ('MAX_REPEAT', 'MIN_REPEAT'):
            low, high, body = arg
            return ''.join(many(body) for _ in range(rng.randint(low, min(high, low + 3))))
        if name == 'SUBPATTERN':
            return many(arg[3])
        if name == 'BRANCH':
            return many(rng.choice(arg[1]))
        if name == 'CATEGORY':
            return {'CATEGORY_DIGIT': '7', 'CATEGORY_SPACE': ' ', 'CATEGORY_WORD': 'w'}.get(str(arg), 'a')
        if name == 'AT':
            return ''
        raise ValueError(name)

    def many(nodes):
        return ''.join(one(node) for node in nodes)
    try:
        return many(sre.parse(pattern))
    except (ValueError, TypeError, re.error, RecursionError):
        return None


def literals(line):
    for match in LITERAL.finditer(line):
        text = match.group(1) if match.group(1) is not None else match.group(2)
        yield text.strip()


def collect(lines, rng, plain=False):
    words = set()
    for line in lines:
        for text in literals(line):
            if not (MIN <= len(text) <= MAX and (plain or not PLAIN_WORD.match(text))):
                continue
            words.add(text)
            if re.search(r'[\\^$()\[\]{}|*+?]', text):
                for _ in range(12):
                    got = sample(text, rng)
                    if got and MIN <= len(got) <= MAX:
                        words.add(got)
    return words


def deleted_lines():
    diff = run('git', 'diff', f'{START}..HEAD', '-U0', '--', 'tools/')
    return [line[1:] for line in diff.splitlines() if line.startswith('-') and not line.startswith('---')]


def start_violation_lines():
    with tempfile.TemporaryDirectory() as tree:
        archive = subprocess.run(['git', 'archive', START], check=True, capture_output=True, cwd=ROOT).stdout
        subprocess.run(['tar', '-x', '-C', tree], input=archive, check=True)
        shutil.copytree(os.path.join(ROOT, '.semgrep'), os.path.join(tree, '.semgrep-now'))   # 現在的規則;規則範例檔不算
        out = subprocess.run(['uvx', 'semgrep', '--config', '.semgrep-now', '--json', '--quiet', '--metrics', 'off', '.'],
                             capture_output=True, text=True, cwd=tree).stdout
        found = []
        for hit in json.loads(out)['results']:
            if not hit['path'].startswith('tools/'):
                continue
            with open(os.path.join(tree, hit['path']), encoding='utf-8') as source:
                rows = source.read().splitlines()
            found.append((hit['path'], hit['start']['line'], rows[hit['start']['line'] - 1:hit['end']['line']]))
        return found


def patch_lines():
    """past_patches 底下是掃描器考題的程式片段(不是 diff),整份都是補丁加的行。"""
    lines = []
    for path in sorted(glob.glob(os.path.join(ROOT, 'tests', 'fixtures', 'past_patches', '*.patch'))):
        with open(path, encoding='utf-8') as source:
            lines += source.read().splitlines()
    return lines


def patch_table_words():
    """601-exam.md 補丁表「注入什麼」那一欄的描述,與裡面「…」括起來的字。"""
    words = set()
    with open(os.path.join(ROOT, 'docs', 'research', '601-exam.md'), encoding='utf-8') as source:
        for line in source:
            cells = [cell.strip() for cell in line.split('|')]
            if len(cells) > 5 and re.fullmatch(r'[SBL]\d\d', cells[1]):
                words.add(cells[3])
                words.update(word for quoted in re.findall(r'「([^」]+)」', cells[3]) for word in (quoted, *quoted.split('、')))
    return words


def main():
    rng = random.Random(776)
    sources = {
        'deleted_lines': collect(deleted_lines(), rng),
        'start_semgrep_violations': collect([row for _p, _l, rows in start_violation_lines() for row in rows], rng, plain=True),
        'past_patches': collect(patch_lines(), rng) | patch_table_words(),
    }
    result = {name: sorted(words) for name, words in sources.items()}
    with open(OUT, 'w', encoding='utf-8') as target:
        json.dump(result, target, ensure_ascii=False, indent=1)
        target.write('\n')
    print({name: len(words) for name, words in result.items()})


if __name__ == '__main__':
    sys.exit(main())
