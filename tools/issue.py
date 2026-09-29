#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
issue —— 對 GitHub issue 寫任何東西之前,先掃私人字串;有就不送。

issue 留在私人開發 repo,不會同步到公開鏡像(docs/adr/0002);但之後可能挑幾張搬去公開 repo。所以開票、留言、改票一律走這支,
不要直接 gh issue create。掃的規則在 tools/privacy_scan.py(字詞清單 .git/info/private-words,
加上 Email 與家目錄路徑);清單不在就當作不安全,一律不送。

  uv run python tools/issue.py create --title T --body-file F [其他 gh issue create 參數]
  uv run python tools/issue.py comment 12 --body-file F
  uv run python tools/issue.py edit 12 --title T --body-file F
  uv run python tools/issue.py audit        # 掃現有全部 issue 與留言(搬 issue 去公開 repo 前一定要跑)

第一個參數之後的東西原樣交給 gh issue <子命令>;標題、內文、內文檔(--body-file - 是 stdin)都會先掃。
"""
import os, sys, json, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import privacy_scan as ps

TEXT_FLAGS = {'-t': 'title', '--title': 'title', '-b': 'body', '--body': 'body',
              '-F': 'body-file', '--body-file': 'body-file'}


def texts_of(args):
    """從 gh 參數裡挑出會送出去的文字。回 ([(來源, 文字)…], 新參數)。stdin 內文先存成暫存檔,送出時照樣能用。"""
    out, new, i = [], [], 0
    while i < len(args):
        a = args[i]
        k, v = a, None
        if '=' in a and a.split('=', 1)[0] in TEXT_FLAGS:
            k, v = a.split('=', 1)
        elif a in TEXT_FLAGS and i + 1 < len(args):
            v = args[i + 1]; i += 1
        if v is None or k not in TEXT_FLAGS:
            new.append(a); i += 1
            continue
        kind = TEXT_FLAGS[k]
        if kind == 'body-file':
            text = sys.stdin.read() if v == '-' else open(v, encoding='utf-8').read()
            if v == '-':
                fd, v = tempfile.mkstemp(suffix='.md')
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(text)
            out.append(('內文', text))
        else:
            out.append(('標題' if kind == 'title' else '內文', v))
        new += [k, v]
        i += 1
    return out, new


def show(hits):
    print('❌ 沒送出:有私人字串(改掉再送;清單在 .git/info/private-words)')
    for src, n, w, line in hits[:50]:
        print(f'  {src} 第 {n} 行 「{w}」 {line}')


def audit():
    r = subprocess.run(['gh', 'issue', 'list', '--state', 'all', '--limit', '1000',
                        '--json', 'number,title,body,comments'], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr.strip())
        return 2
    texts = []
    for it in json.loads(r.stdout):
        n = it['number']
        texts += [(f'#{n} 標題', it['title']), (f'#{n} 內文', it.get('body') or '')]
        texts += [(f'#{n} 留言', c.get('body') or '') for c in it.get('comments') or []]
    hits = ps.scan_text(texts)
    if hits:
        show(hits)
        return 1
    print(f'✅ {len(texts)} 段文字都沒有私人字串')
    return 0


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    if argv[0] == 'audit':
        return audit()
    texts, args = texts_of(argv[1:])
    hits = ps.scan_text(texts)
    if hits:
        show(hits)
        return 1
    return subprocess.run(['gh', 'issue', argv[0], *args]).returncode


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
