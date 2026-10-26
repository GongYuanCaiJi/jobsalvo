#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adr_frozen —— 擋「改寫已定案的 ADR」(#549)。ADR 是決策紀錄:決策改了就新增一份,舊的只改檔頭狀態
(status: superseded by ADR-NNNN);狀態是 proposed 的還在討論,照常可改。

比的是 main 上已定案的每一份 ADR,在現在的 docs/adr/ 裡內文還原封不動(只看內文、不看檔名,所以只改檔名會過)。
pre-commit 跑之前會先收起沒 stage 的修改,磁碟上就是這次要 commit 的內容;CI 上磁碟就是 PR 的版本。

  python3 tools/adr_frozen.py
"""
import glob, re, subprocess, sys

FRONT = re.compile(r'\A---\n(.*?)\n---\n', re.S)


def body(text):
    return FRONT.sub('', text, count=1).strip()


def proposed(text):
    m = FRONT.match(text)
    return bool(m and re.search(r'^status:\s*proposed\s*$', m.group(1), re.M))


def rewritten(base, now):
    """base 上已定案、現在找不到同樣內文的 ADR(base、now 都是 {路徑: 全文})。"""
    kept = {body(text) for text in now.values()}
    return [path for path, text in base.items() if not proposed(text) and body(text) not in kept]


def git(*a):
    return subprocess.run(['git', *a], capture_output=True, text=True, check=True).stdout


def main():
    try:
        base = git('merge-base', 'HEAD', 'origin/main').strip()
    except subprocess.CalledProcessError:   # 沒有 origin/main(剛 clone 的別人的 fork)就跟上一個 commit 比
        base = 'HEAD'
    old = {path: git('show', f'{base}:{path}') for path in git('ls-tree', '--name-only', base, 'docs/adr/').split()
           if path.endswith('.md')}
    now = {path: open(path, encoding='utf-8').read() for path in glob.glob('docs/adr/*.md')}
    bad = rewritten(old, now)
    for path in bad:
        print(f'{path}:已定案的 ADR 只准改檔頭狀態。決策改了請新增一份 ADR,舊的標 status: superseded by ADR-NNNN')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
