#!/bin/sh
# 建立真實資料的隔離副本，並在專用連接埠啟動本機看板驗收服務。
set -eu
umask 077
[ "$#" -eq 0 ] || { echo "用法: launch.sh" >&2; exit 2; }

SOURCE=${VERIFY_SOURCE:-"$HOME/jobsearch"}
ROOT=$(CDPATH= cd "$(dirname "$0")/../../.." && pwd)
RUN_ROOT=$HOME/.cache/jobsalvo-verify
# 沒指定 VERIFY_PORT 就向系統要一個沒人用的連接埠；每輪各用自己的連接埠和資料夾，好幾輪可以同時跑。
PORT=${VERIFY_PORT:-$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')}
case "$PORT" in *[!0-9]*|''|0*) echo "VERIFY_PORT 必須是不帶前導零的數字。" >&2; exit 2 ;; esac
[ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || { echo "VERIFY_PORT 範圍為 1024–65535。" >&2; exit 2; }
case "$PORT" in 8898|8899) echo "這個連接埠保留給使用者看板。" >&2; exit 2 ;; esac
# tools/board_doc.py 的 target() 優先採用這兩個環境變數；realdata_check._check_copy 會呼叫 set_fb，不能寫到呼叫者指定的看板。
unset AGENT_BOARD JOBSALVO_BOARD_ID
[ -d "$SOURCE" ] && [ -f "$SOURCE/jobsalvo.json" ] || { echo "找不到來源資料夾或 jobsalvo.json: $SOURCE" >&2; exit 2; }
mkdir -p "$RUN_ROOT"
python3 - "$RUN_ROOT" "$SOURCE" <<'PY'
import os, sys
run_root, source = map(os.path.realpath, sys.argv[1:])
if os.path.commonpath((run_root, source)) in (run_root, source):
    raise SystemExit('跑次資料夾與來源資料夾必須彼此分開')
PY
RUN=$(mktemp -d "$RUN_ROOT/run-XXXXXX")
CODE=$RUN/code
HOME_COPY=$RUN/home

FAILED=1
cleanup_on_error() {
  trap '' HUP INT TERM
  [ "$FAILED" -eq 1 ] && "$ROOT/.claude/skills/verify/cleanup.sh" "$RUN" || true
}
trap cleanup_on_error EXIT
trap 'exit 1' HUP INT TERM
mkdir "$CODE" "$RUN/.private-tmp"

EVIDENCE_ROOT=$RUN_ROOT/evidence
mkdir -p "$EVIDENCE_ROOT"
EVIDENCE=$EVIDENCE_ROOT/$(date +%Y%m%d-%H%M%S)-${RUN##*/run-}
mkdir "$EVIDENCE"
printf '%s\n' "$EVIDENCE" >"$RUN/evidence.path"

python3 - "$PORT" <<'PY'
import socket, sys
try:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', int(sys.argv[1])))
except OSError as exc:
    raise SystemExit(f'連接埠 {sys.argv[1]} 有程序在聽或無法使用：{exc}')
PY

# 程式快照取目前工作樹：HEAD 加上還沒 commit 的改動，.gitignore 忽略的檔不帶。
git -C "$ROOT" rev-parse HEAD >"$RUN/commit"
git -C "$ROOT" status --porcelain >"$RUN/changes"
python3 - "$ROOT" "$CODE" <<'PY'
import os, shutil, subprocess, sys
root, code = sys.argv[1:]
names = subprocess.run(['git', '-C', root, 'ls-files', '-z', '-co', '--exclude-standard'], capture_output=True, check=True).stdout
for name in filter(None, map(os.fsdecode, names.split(b'\0'))):
    if os.path.lexists(os.path.join(root, name)):
        os.makedirs(os.path.dirname(os.path.join(code, name)), exist_ok=True)
        shutil.copy2(os.path.join(root, name), os.path.join(code, name), follow_symlinks=False)
PY
PYTHONPATH="$CODE/tools" JOBSALVO_HOME="$SOURCE" \
  TMPDIR="$RUN/.private-tmp" TMP="$RUN/.private-tmp" TEMP="$RUN/.private-tmp" \
  uv run --project "$CODE" python - "$SOURCE" "$HOME_COPY" <<'PY'
import sys, realdata_check
raise SystemExit(realdata_check._check_copy(sys.argv[1], sys.argv[2], True))
PY

BOARD=$(JOBSALVO_HOME="$HOME_COPY" PYTHONPATH="$CODE/tools" uv run --project "$CODE" python -c 'import config; print(config.LIVE)')
case "$BOARD" in "$HOME_COPY"/*) ;; *) echo "副本設定的看板路徑不在副本內: $BOARD" >&2; exit 1 ;; esac
[ -f "$BOARD" ] || { echo "副本看板不存在: $BOARD" >&2; exit 1; }
STATE=$HOME_COPY/.verify-board.html
cp "$BOARD" "$STATE"
rm -rf "$HOME_COPY/evidence"
mkdir -p "$HOME_COPY/.verify-tmp" "$HOME_COPY/evidence"
chmod 700 "$HOME_COPY/.verify-tmp" "$HOME_COPY/evidence"

JOBSALVO_HOME="$HOME_COPY" AGENT_BOARD="$STATE" PYTHONPATH="$CODE/tools" \
  TMPDIR="$HOME_COPY/.verify-tmp" TMP="$HOME_COPY/.verify-tmp" TEMP="$HOME_COPY/.verify-tmp" \
  uv run --project "$CODE" python - "$STATE" <<'PY'
import sys, board_doc as bd
def clear_workspaces(fb):
    fb.pop('__browser_cleanup__', None)
    for card in fb.values():
        apply = card.get('apply') if isinstance(card, dict) else None
        if isinstance(apply, dict): apply.pop('workspace', None)
bd.set_fb(clear_workspaces, live=sys.argv[1], by='verify-launch')
PY
JOBSALVO_HOME="$HOME_COPY" PYTHONPATH="$CODE/tools" uv run --project "$CODE" python - "$STATE" <<'PY'
import sys, board_doc, config
if board_doc.is_live(sys.argv[1], config.LIVE): raise SystemExit('驗證狀態檔仍指向正式看板')
PY

printf '%s\n' "$PORT" >"$RUN/port"
printf '%s\n' "$CODE" >"$RUN/code.path"
printf '%s\n' "$HOME_COPY" >"$RUN/home.path"
printf '%s\n' "$BOARD" >"$RUN/board.path"
printf '%s\n' "$STATE" >"$RUN/state.path"
SERVER_PYTHON=$(uv run --project "$CODE" python -c 'import sys; print(sys.executable)')
python3 - "$RUN" "$PORT" "$CODE" "$HOME_COPY" "$STATE" "$SERVER_PYTHON" <<'PY'
import json, os, subprocess, sys, time
run, port, code, home, state, server_python = sys.argv[1:]
env = dict(os.environ, JOBSALVO_HOME=home, AGENT_BOARD=state, EVIDENCE_TMP=home,
           TMPDIR=os.path.join(home, '.verify-tmp'), TMP=os.path.join(home, '.verify-tmp'),
           TEMP=os.path.join(home, '.verify-tmp'), PYTHONUNBUFFERED='1')
with open(os.path.join(run, 'server.log'), 'w', encoding='utf-8') as log:
    process = subprocess.Popen([server_python, os.path.join(code, 'tools', 'board_server.py'),
        '--state', state, '--port', port, '--host', '127.0.0.1'], cwd=code, env=env,
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
identity = None
for _ in range(40):
    result = subprocess.run(['ps', '-p', str(process.pid), '-o', 'pid=,pgid=,lstart=,stat='], capture_output=True, text=True)
    fields = result.stdout.split()
    if result.returncode == 0 and len(fields) == 8 and int(fields[0]) == process.pid and int(fields[1]) == process.pid and not fields[7].startswith('Z'):
        identity = {'pid': process.pid, 'pgid': process.pid, 'started': ' '.join(fields[2:7])}
        break
    if process.poll() is not None: break
    time.sleep(.05)
if identity is None: raise RuntimeError('無法記錄看板 PID、PGID 和啟動時間')
with open(os.path.join(run, '.server.json'), 'w', encoding='utf-8') as stream: json.dump(identity, stream); stream.write('\n')
for name, value in (('server.pid', identity['pid']), ('group.pid', identity['pgid'])):
    with open(os.path.join(run, name), 'w', encoding='utf-8') as stream: stream.write(f'{value}\n')
PY

python3 - "$PORT" "$RUN/server.pid" <<'PY'
import json, os, sys, time, urllib.request
base = f'http://127.0.0.1:{sys.argv[1]}'
pid = int(open(sys.argv[2]).read())
for _ in range(90):
    try: os.kill(pid, 0)
    except OSError: raise SystemExit('本輪看板程序已結束，錯誤摘要請見本輪 server.log')
    try:
        with urllib.request.urlopen(base + '/', timeout=2) as response:
            if response.status != 200: raise RuntimeError(f'首頁 HTTP {response.status}')
        with urllib.request.urlopen(base + '/api/rev', timeout=2) as response: rev = json.load(response)
        if not rev.get('building'): break
    except Exception: pass
    time.sleep(1)
else: raise SystemExit('90 秒內看板未就緒，錯誤摘要請見本輪 server.log')
PY

FAILED=0
trap - EXIT HUP INT TERM
echo "http://127.0.0.1:$PORT"
echo "程式快照: $CODE"
echo "資料副本: $HOME_COPY"
echo "本輪資料夾: $RUN"
echo "證據資料夾: $EVIDENCE"
