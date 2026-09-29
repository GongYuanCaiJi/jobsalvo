#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
privacy_scan —— commit 前擋私人字串:你的名字、Email、帳號、自己的路徑……不小心寫進程式就在這裡攔下。

字詞清單放在 .git/info/private-words(一行一個,# 開頭是註解)。它在 .git 裡面,不會被 commit、不會被 push,
所以清單本身不會外流。英數字詞比對時不分大小寫、要是完整的詞(Doe 不會中 Doer);其他字(中文)照原樣找。
以斜線開頭或結尾的條目是路徑前綴,照原樣找(不套完整的詞:路徑後面一定接著帳號名,套了就永遠比對不到)。

  uv run python tools/privacy_scan.py            # 掃這次要 commit 的內容(pre-commit 用)
  uv run python tools/privacy_scan.py --all      # 掃整個 repo 現在的檔案
  uv run python tools/privacy_scan.py --all --words 清單檔 --message "commit 訊息"
                                           # 公開鏡像同步前(.github/workflows/mirror.yml):清單從 CI 密鑰寫出來,
                                           # 讀不到或是空的就算失敗;commit 訊息會公開,照送出 repo 以外的文字那套掃

要送出 repo 以外的文字(GitHub issue)用 scan_text():清單不在就當作不安全,另外也擋 Email 與家目錄路徑。
"""
import os, re, sys, subprocess, argparse


def git(*a):
    return subprocess.run(['git', *a], capture_output=True, text=True, check=True).stdout


def words(path=None):
    """(比對規則, 清單路徑);清單不在回 (None, 路徑)。path:指定清單檔(公開鏡像同步時從 CI 密鑰寫出來的那份)。"""
    p = path or os.path.join(git('rev-parse', '--git-common-dir').strip(), 'info', 'private-words')
    try:
        with open(p, encoding='utf-8') as f:
            ws = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    except OSError:
        return None, p
    pats = []
    for w in ws:
        if w.startswith('/') or w.endswith('/'):
            pats.append((w, re.compile(re.escape(w), re.I)))
        elif re.fullmatch(r'[\x20-\x7e]+', w):
            pats.append((w, re.compile(r'(?<![A-Za-z0-9])' + re.escape(w) + r'(?![A-Za-z0-9])', re.I)))
        else:
            pats.append((w, re.compile(re.escape(w))))
    return pats, p


def staged_lines():
    """這次 commit 新加的每一行:(檔名, 行號, 內容)。"""
    out, f, n = [], None, 0
    for l in git('diff', '--cached', '-U0', '--no-color', '--diff-filter=ACMR').splitlines():
        if l.startswith('+++ '):
            f = l[6:] if l.startswith('+++ b/') else None
        elif l.startswith('@@'):
            n = int(re.search(r'\+(\d+)', l).group(1))
        elif l.startswith('+') and f:
            out.append((f, n, l[1:])); n += 1
    names = [l for l in git('diff', '--cached', '--name-only', '--diff-filter=ACR').splitlines()]
    out += [(x, 0, x) for x in names]           # 檔名本身也算
    return out


def all_lines():
    out = []
    for f in git('ls-files').splitlines():
        out.append((f, 0, f))
        try:
            with open(f, encoding='utf-8') as fh:
                for i, l in enumerate(fh, 1):
                    out.append((f, i, l.rstrip('\n')))
        except (OSError, UnicodeDecodeError):
            pass
    return out


# 送出 repo 以外的文字才加的通用規則:清單再完整也列不完每個 Email 與路徑
GENERIC = [('Email', re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')),
           ('家目錄路徑', re.compile(r'/(Users|home)/[^/\s]+'))]


def scan_text(texts, path=None):
    """[(來源, 文字)…] → [(來源, 行號, 命中的字, 那一行)…]。沒有字詞清單(或清單是空的)就回一筆問題:寧可不送。"""
    pats, where = words(path)
    if not pats:
        return [('', 0, '沒有字詞清單', f'{where} 不在,不能確定安全')]
    hits = []
    for src, text in texts:
        for i, line in enumerate((text or '').splitlines(), 1):
            for w, rx in pats + GENERIC:
                if rx.search(line):
                    hits.append((src, i, w, line.strip()[:120]))
    return hits


# 原封不動抄來的第三方字典:繁簡字典本來就收了每一個字(姓氏也在裡面),一定會中單字的私人字詞。只放過這幾個檔。
VENDORED = ('third_party/opencc/',)


def vendored(f):
    return f.startswith(VENDORED) and f.endswith('.txt')


def scan_lines(lines, pats):
    """[(檔名, 行號, 內容)…](行號 0 = 檔名本身)→ 命中的 [(檔名, 行號, 字, 那一行)…]。repo 裡的檔案用這套。"""
    hits = []
    for f, n, line in lines:
        if n and vendored(f):
            continue                    # 檔名照樣掃,內容不掃
        for w, rx in pats:
            if rx.search(line):
                hits.append((f, n, w, line.strip()[:120]))
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--words', help='用這份字詞清單;讀不到或是空的就算失敗(不像本機沒清單時跳過)')
    ap.add_argument('--message', help='一併掃這段 commit 訊息')
    a = ap.parse_args()
    pats, where = words(a.words)
    if not pats and a.words:
        print(f'❌ 字詞清單 {where} 讀不到或是空的,不能確定安全')
        return 1
    if pats is None:
        print(f'privacy_scan:沒有 {where},跳過(建議建一份,見 tools/privacy_scan.py 開頭)。')
        return 0
    hits = scan_lines(all_lines() if a.all else staged_lines(), pats)
    if a.message is not None:
        hits += scan_text([('commit 訊息', a.message)], a.words)
    if not hits:
        return 0
    print('❌ 有私人字串(清單在 .git/info/private-words):')
    for f, n, w, line in hits[:50]:
        print(f'  {f}:{n}  「{w}」  {line}')
    if len(hits) > 50:
        print(f'  …還有 {len(hits) - 50} 處')
    return 1


if __name__ == '__main__':
    sys.exit(main())
