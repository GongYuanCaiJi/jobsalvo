#!/usr/bin/env bash
# 規格:把 jobsalvo 從這台 Mac 拿掉。停看板、移除開機自動啟動、清掉快取和暫存、刪掉安裝的程式資料夾。
# 你的求職資料夾(履歷、看板、設定)不動;要一起刪才加 --data。跟 Homebrew 一樣:移除程式,資料另外問。
#   bash tools/uninstall.sh            # 移除程式,留下資料
#   bash tools/uninstall.sh --data     # 連資料夾一起刪(刪了拿不回來)
set -euo pipefail

purge_data=0
[[ "${1:-}" == "--data" ]] && purge_data=1
data_home="${JOBSALVO_HOME:-$HOME/jobsearch}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_dir="${JOBSALVO_APP_DIR:-$(cd "$script_dir/.." && pwd)}"
python="$(command -v python3 || true)"

# 只刪看起來對的路徑:空的、根目錄、家目錄本身一律不碰
safe() { [[ -n "$1" && "$1" != "/" && "$1" != "$HOME" && "$1" != "$HOME/" ]]; }

# 1. 停掉看板(安裝指令起的那一個)
pidfile="$data_home/.jobsalvo-server.pid"
if [[ -f "$pidfile" ]]; then
  kill "$(cat "$pidfile")" 2>/dev/null || true
  rm -f "$pidfile"
  echo "停掉看板"
fi

# 2. 開機自動啟動(launchd)
if [[ -n "$python" && -f "$app_dir/tools/install_service.py" ]]; then
  JOBSALVO_HOME="$data_home" "$python" "$app_dir/tools/install_service.py" --remove >/dev/null 2>&1 || true
fi
rm -f "$HOME"/Library/LaunchAgents/dev.jobsalvo.board-server*.plist
echo "移除開機自動啟動"

# 3. 快取、舊版的 agent Chrome 資料夾(換成 ego 之後不再用,docs/adr/0006;只刪 agent-chrome,同一層可能有他的資料)和暫存
rm -rf "$HOME/.cache/jobsalvo" "$HOME/Library/Application Support/jobsalvo/agent-chrome"
tmp=''
if [[ -n "$python" && -f "$app_dir/tools/config.py" ]]; then
  tmp="$(cd "$app_dir/tools" && JOBSALVO_HOME="$data_home" "$python" -c 'import config; print(config.TMP)' 2>/dev/null || true)"
fi
if safe "$tmp" && [[ "$tmp" == /tmp/* || "$tmp" == /private/tmp/* || "$tmp" == "$HOME"/.cache/* ]]; then
  rm -rf "$tmp"
fi
echo "清掉快取和暫存"

# 4. 程式資料夾:只刪安裝指令裝的那個位置;開發用的資料夾(自己 clone 的)不刪
if [[ "$app_dir" == "$HOME/Applications/jobsalvo" ]] && safe "$app_dir"; then
  rm -rf "$app_dir"
  echo "刪掉程式資料夾 $app_dir"
else
  echo "程式資料夾 $app_dir 不是安裝指令裝的位置,留著(要刪自己刪)"
fi

# 5. 求職資料夾
if [[ "$purge_data" == 1 ]] && safe "$data_home"; then
  rm -rf "$data_home"
  echo "刪掉求職資料夾 $data_home"
else
  echo "你的求職資料夾留著:$data_home(要一起刪:bash tools/uninstall.sh --data)"
fi
echo "jobsalvo 已移除"
