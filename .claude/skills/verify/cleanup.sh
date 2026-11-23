#!/bin/sh
# 停止指定跑次記錄的看板並刪除該跑次資料夾；看板停不掉時保留資料夾並以非零結束。
set -eu
[ "$#" -eq 1 ] || { echo "用法: cleanup.sh <跑次資料夾>" >&2; exit 2; }
RUN=$1
[ -d "$RUN" ] || { echo "沒有這個跑次資料夾：$RUN"; exit 0; }
python3 - "$RUN" "$HOME/.cache/jobsalvo-verify" <<'PY'
import json, os, shutil, signal, subprocess, sys, time
run, run_root = map(os.path.realpath, sys.argv[1:])
if os.path.dirname(run) != run_root or not os.path.basename(run).startswith('run-'):
    raise SystemExit(f'只清 {run_root}/run-* 底下的跑次資料夾：{run}')
try:
    with open(os.path.join(run, '.server.json'), encoding='utf-8') as stream: record = json.load(stream)
    pid, pgid, started = record['pid'], record['pgid'], record['started']
    if type(pid) is not int or pid <= 1 or type(pgid) is not int or pgid != pid or not isinstance(started, str):
        raise ValueError('看板記錄無效')
except (OSError, ValueError, KeyError, TypeError) as exc:
    print(f'沒有可安全停止的看板記錄：{exc}')
    record = None

def matches():
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'pid=,pgid=,lstart=,stat='], capture_output=True, text=True)
        fields = result.stdout.split()
        return (result.returncode == 0 and not result.stderr and len(fields) == 8
                and int(fields[0]) == pid and int(fields[1]) == pgid
                and ' '.join(fields[2:7]) == started and not fields[7].startswith('Z'))
    except (OSError, ValueError): return False

def send(sig):
    if not matches():
        print(f'看板 PID={pid} 身分不符或已結束，不送訊號。')
        return False
    try: os.killpg(pgid, sig)
    except OSError as exc:
        print(f'送出 {sig.name} 失敗：{exc}')
        return False
    print(f'已送出 {sig.name} 給本輪看板 PID={pid} PGID={pgid}。')
    return True

if record is not None and send(signal.SIGTERM):
    deadline = time.monotonic() + 10
    while matches() and time.monotonic() < deadline: time.sleep(.1)
    if matches() and send(signal.SIGKILL):
        deadline = time.monotonic() + 3
        while matches() and time.monotonic() < deadline: time.sleep(.1)
    print('看板停止檢查完成。')
if record is not None and matches():
    print(f'看板 PID={pid} 仍在執行；保留跑次資料夾 {run}，查明原因後再清理。')
    raise SystemExit(1)

try:
    evidence = open(os.path.join(run, 'evidence.path'), encoding='utf-8').read().strip()
except OSError: evidence = ''
shutil.rmtree(run)
print(f'清理完成；證據保留：{evidence}' if evidence else '清理完成；沒有證據路徑記錄。')
PY
