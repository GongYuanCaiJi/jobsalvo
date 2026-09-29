#!/bin/sh
# 起看板伺服器,並且把「它幾點起來、幾點結束、怎麼結束的」寫進紀錄檔(paths.log,預設 <home>/board-server.log)。
#
# 為什麼要這支:伺服器被殺掉時,如果輸出寫在 /tmp、又沒記結束代碼,事後查不出原因。
# 這支把輸出留在資料夾裡,結束時多寫一行結束代碼。重啟由 launchd 負責(tools/install_service.py 裝的,
# KeepAlive),這支本身不重啟。
#
# PATH 要自己補:launchd 起的行程 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin。
# 看板上的按鈕會派 agent(codex、command-code 常裝在 ~/.local/bin 或 /opt/homebrew/bin),
# 少了這行就是按了之後找不到指令。
#
# 用法:JOBSALVO_HOME=<資料夾> sh tools/board_serve.sh     (前景跑,Ctrl-C 結束)
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
TOOLS="$(cd "$(dirname "$0")" && pwd)"
APP="$(dirname "$TOOLS")"
# 用 uv run 跑(https://docs.astral.sh/uv/guides/scripts/):每次起來先照 uv.lock 對齊 .venv 的套件再跑。
# 舊的安裝剛更新過來、還沒裝 uv:退回系統的 python3,看板照樣起得來,環境檢查會講要裝 uv
if command -v uv >/dev/null 2>&1; then
  run() { uv run --frozen --no-dev --project "$APP" python "$@"; }
else
  run() { python3 "$@"; }
fi
LOG="$(run -c 'import sys; sys.path.insert(0, sys.argv[1]); import config; print(config.LOG)' "$TOOLS")" || exit 1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') 起動 pid=$$ ===" >> "$LOG"
run "$TOOLS/board_server.py" "$@" >> "$LOG" 2>&1
rc=$?
# 退出碼 >128 = 被訊號殺掉,128+訊號編號(143=SIGTERM 被 kill,137=SIGKILL 被 kill -9,130=Ctrl-C)。
case "$rc" in
  143) why="被 SIGTERM 殺掉(有人 kill 或 pkill)";;
  137) why="被 SIGKILL 殺掉(kill -9,或系統強制收掉)";;
  130) why="Ctrl-C";;
  0)   why="自己正常結束";;
  *)   why="程式自己結束,退出碼 $rc";;
esac
echo "=== $(date '+%Y-%m-%d %H:%M:%S') 結束 rc=$rc($why) ===" >> "$LOG"
exit "$rc"
