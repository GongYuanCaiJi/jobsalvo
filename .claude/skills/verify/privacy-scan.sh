#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf '用法：%s EVIDENCE_DIR\n' "$0" >&2
  exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)

uv run --project "$REPO_ROOT" python - "$1" <<'PY'
from pathlib import Path
import sys
from tools.privacy_scan import scan_text

root = Path(sys.argv[1]).resolve()
if not root.is_dir():
    raise SystemExit(f"證據目錄不存在：{root}")

text_exts = {".txt", ".md", ".json", ".jsonl", ".log", ".csv", ".tsv", ".html", ".htm", ".xml", ".yaml", ".yml"}
files = sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink() and p.suffix.lower() in text_exts)
if not files:
    raise SystemExit(f"證據目錄沒有可掃描的文字檔：{root}")

texts = []
for path in files:
    rel = path.relative_to(root).as_posix()
    texts.extend([(rel, rel), (rel, path.read_text(encoding="utf-8"))])

hits = scan_text(texts)
for source, line, word, text in hits:
    print(f"{source}:{line}  「{word}」  {text}")
print(f"privacy_scan: {len(files)} 個證據文字檔，{len(hits)} 處符合")
raise SystemExit(bool(hits))
PY
