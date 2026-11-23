#!/bin/sh
# 檢查功能清單的結構：README 與功能頁一一對應、沒有重複列、每頁五個章節依序、Coverage 的檔案和 commit 都存在、連結都指得到。
set -eu
[ "$#" -eq 0 ] || { echo "用法: map-check.sh" >&2; exit 2; }
SKILL=$(CDPATH= cd "$(dirname "$0")" && pwd)
python3 - "$SKILL" <<'PY'
import os, re, subprocess, sys
skill = sys.argv[1]; feat = os.path.join(skill, 'features'); root = os.path.dirname(os.path.dirname(os.path.dirname(skill)))
H2 = ['Sub-features', 'How to get to it (user POV)', 'Driving it with ego lite', 'Gotchas', 'Coverage']
errs = []
pages = sorted(f for f in os.listdir(feat) if f.endswith('.md') and f != 'README.md')
readme = open(os.path.join(feat, 'README.md'), encoding='utf-8').read()
linked = set(re.findall(r'\]\(([a-z0-9-]+\.md)(?:#[^)]*)?\)', readme))
for p in pages:
    if p not in linked: errs.append(f'README 沒列 {p}')
for p in sorted(linked - set(pages)): errs.append(f'README 連到不存在的 {p}')
rows = re.findall(r'^\| ([^|]+?) \|', readme, re.M)
for r in sorted({r for r in rows if rows.count(r) > 1}): errs.append(f'README 重複列了「{r}」')
for p in pages:
    text = open(os.path.join(feat, p), encoding='utf-8').read()
    heads = re.findall(r'^## (.+)$', text, re.M)
    if heads != H2: errs.append(f'{p}: 章節是 {heads}，應為 {H2}')
    cov = text.split('\n## Coverage', 1)[1] if '\n## Coverage' in text else ''
    files = re.findall(r'^- Files: (.+)$', cov, re.M)
    if not files: errs.append(f'{p}: Coverage 沒有 Files')
    for f in re.findall(r'`([^`]+)`', files[0] if files else ''):
        if not os.path.exists(os.path.join(root, f)): errs.append(f'{p}: Files 列的 {f} 不存在')
    sync = re.findall(r'^- Sync commit: `([0-9a-f]{7,40})`', cov, re.M)
    if not sync: errs.append(f'{p}: Coverage 沒有 Sync commit')
    elif subprocess.run(['git', '-C', root, 'cat-file', '-e', sync[0] + '^{commit}'], capture_output=True).returncode:
        errs.append(f'{p}: Sync commit {sync[0]} 不存在')
    if not re.search(r'^- Break: .+Fails at ', cov, re.M): errs.append(f'{p}: Coverage 沒有記錄破壞（- Break: … Fails at …）')
for name in ['SKILL.md'] + [os.path.join('features', p) for p in ['README.md'] + pages] + [os.path.join('references', r) for r in os.listdir(os.path.join(skill, 'references'))]:
    base = os.path.dirname(os.path.join(skill, name))
    for link in re.findall(r'\]\(([^)#:]+)(?:#[^)]*)?\)', open(os.path.join(skill, name), encoding='utf-8').read()):
        if not os.path.exists(os.path.normpath(os.path.join(base, link))): errs.append(f'{name}: 連結 {link} 指不到')
for e in errs: print(e)
print(f'map-check: {len(pages)} 個功能頁，{len(errs)} 個問題')
raise SystemExit(bool(errs))
PY
