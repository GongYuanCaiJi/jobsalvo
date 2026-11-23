#!/bin/sh
# 唯讀檢查本輪 localhost server 和隔離資料副本。
set -eu
RUN=${1:?用法: doctor.sh <跑次資料夾>}
ROOT=$(CDPATH= cd "$(dirname "$0")/../../.." && pwd)
PORT=$(cat "$RUN/port")
HOME_COPY=$(cat "$RUN/home.path")
STATE=$(cat "$RUN/state.path")
SERVER_PID=$(cat "$RUN/server.pid")
GROUP_PID=$(cat "$RUN/group.pid")
CODE=$RUN/code

case "$PORT" in *[!0-9]*|'') echo "本輪連接埠不是數字。" >&2; exit 1 ;; esac
case "$PORT" in 8898|8899) echo "本輪連接埠落在使用者保留範圍。" >&2; exit 1 ;; esac
kill -0 "$SERVER_PID" 2>/dev/null || { echo "本輪 server 已結束。" >&2; exit 1; }
[ "$(ps -o pgid= -p "$SERVER_PID" | tr -d ' ')" = "$GROUP_PID" ] || { echo "監聽 PID 不屬於本輪 process group。" >&2; exit 1; }
PROCESS_COMMAND=$(ps -o command= -p "$SERVER_PID") || { echo "無法讀取本輪 server 的程序指令。" >&2; exit 1; }
case " $PROCESS_COMMAND " in
  *" $CODE/tools/board_server.py "*) ;;
  *) echo "本輪 server 的程序指令不是本輪 code/tools/board_server.py，可能跑的不是本輪程式快照。" >&2; exit 1 ;;
esac
[ -f "$RUN/commit" ] || { echo "本輪缺少程式快照 commit 記錄。" >&2; exit 1; }
RUN_COMMIT=$(cat "$RUN/commit")
# 程式快照要跟目前工作樹一字不差；launch 之後又改了程式，就是在驗舊程式。
python3 - "$ROOT" "$CODE" <<'PY'
import filecmp, os, subprocess, sys
root, code = sys.argv[1:]
names = subprocess.run(['git', '-C', root, 'ls-files', '-z', '-co', '--exclude-standard'], capture_output=True, check=True).stdout
for name in filter(None, map(os.fsdecode, names.split(b'\0'))):
    src, dst = os.path.join(root, name), os.path.join(code, name)
    if os.path.lexists(src) != os.path.lexists(dst): same = False
    elif not os.path.lexists(src): same = True
    elif os.path.islink(src) or os.path.islink(dst): same = os.path.islink(src) and os.path.islink(dst) and os.readlink(src) == os.readlink(dst)
    else: same = filecmp.cmp(src, dst, shallow=False)
    if not same: raise SystemExit(f'程式快照和目前工作樹不同：{name}；launch 之後改過程式，Cleanup 後重新 Launch')
PY
lsof -nP -a -p "$SERVER_PID" -iTCP:"$PORT" -sTCP:LISTEN >/dev/null || { echo "本輪 server 沒有監聽 $PORT。" >&2; exit 1; }
[ -f "$STATE" ] || { echo "本輪 state 檔不存在。" >&2; exit 1; }
case "$STATE" in "$HOME_COPY"/*) ;; *) echo "state 檔不在本輪資料副本內。" >&2; exit 1 ;; esac

python3 - "$PORT" "$HOME_COPY" "$STATE" "$RUN_COMMIT" "$RUN/changes" <<'PY'
import json, sys, urllib.request
port, home, state, commit, changes = sys.argv[1:]
changes = open(changes).read().strip()
base = f'http://127.0.0.1:{port}'
with urllib.request.urlopen(base + '/', timeout=5) as r:
    if r.status != 200: raise SystemExit(f'首頁 HTTP {r.status}')
with urllib.request.urlopen(base + '/api/rev', timeout=5) as r:
    rev = json.load(r)
with urllib.request.urlopen(base + '/api/state', timeout=5) as r:
    marks = json.load(r)
if rev.get('building'):
    raise SystemExit('看板仍在背景建置')
print(f"ok: port={port} commit={commit[:12]}{'+未commit改動' if changes else ''} cards={len(marks)} local-copy=yes")
PY
