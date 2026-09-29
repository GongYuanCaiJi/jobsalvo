#!/usr/bin/env bash
# 規格：更新或取得 jobsalvo 程式、檢查環境、啟動並打開看板。重跑會以 git pull --ff-only 更新既有安裝。
# 只重用能回應 jobsalvo health check 的看板；埠被其他服務佔用或狀態不明時停止並說明。
set -euo pipefail

repo='GongYuanCaiJi/jobsalvo'
port="${JOBSALVO_PORT:-8899}"
data_home="${JOBSALVO_HOME:-$HOME/jobsearch}"
script_dir=''
script_source="${BASH_SOURCE[0]:-}"
if [[ -n "$script_source" && -f "$script_source" ]]; then
  script_dir="$(cd "$(dirname "$script_source")" && pwd)"
fi
source_app=''
if [[ -n "$script_dir" && -f "$script_dir/board_server.py" ]]; then
  source_app="$(cd "$script_dir/.." && pwd)"
fi

if [[ -n "${JOBSALVO_APP_DIR:-}" ]]; then
  app_dir="$JOBSALVO_APP_DIR"
elif [[ -n "$source_app" ]]; then
  app_dir="$source_app"
else
  app_dir="$HOME/Applications/jobsalvo"
fi

if [[ -d "$app_dir" ]]; then
  if [[ -n "${JOBSALVO_APP_DIR:-}" || "$app_dir" == "$source_app" ]]; then
    : # Explicit source directory, used by local verification.
  elif [[ -d "$app_dir/.git" ]]; then
    if [[ -n "$(git -C "$app_dir" status --porcelain)" ]]; then
      echo "安裝中止：$app_dir 有未提交的變更，請先處理後再更新。" >&2
      exit 1
    fi
    git -C "$app_dir" pull --ff-only
  else
    echo "安裝中止：$app_dir 已存在但不是 jobsalvo Git 資料夾。" >&2
    exit 1
  fi
else
  # 公開 repo(docs/adr/0002):不用 GitHub 帳號、不用登入
  mkdir -p "$(dirname "$app_dir")"
  git clone "https://github.com/$repo.git" "$app_dir"
fi

# 套件與 Python 交給 uv(https://docs.astral.sh/uv/getting-started/installation/):沒有就照官方方式裝
if ! command -v uv >/dev/null 2>&1; then
  echo '安裝 uv(管 Python 和套件的工具)…'
  if command -v brew >/dev/null 2>&1; then
    brew install uv
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi
echo '準備 Python 和套件(第一次要一兩分鐘)…'
(cd "$app_dir" && uv sync --frozen --no-dev)
python="$app_dir/.venv/bin/python3"
# 先建好資料夾與設定(agent 清單照這台電腦裝了哪個 CLI 來定),再做環境檢查:
# 以前檢查的是預設清單(Codex),只裝 Claude Code 的人在這裡就被擋掉
mkdir -p "$data_home"
JOBSALVO_HOME="$data_home" "$python" "$app_dir/tools/init.py" "$data_home" >/dev/null
JOBSALVO_HOME="$data_home" "$python" "$app_dir/tools/doctor.py"
url="http://127.0.0.1:$port"

probe_port() {
  "$python" - "$port" <<'PY'
import http.client, json, sys

conn = http.client.HTTPConnection('127.0.0.1', int(sys.argv[1]), timeout=0.4)
try:
    conn.request('GET', '/api/health')
    response = conn.getresponse()
    body = response.read(1024)
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    print('ready' if response.status == 200 and payload.get('ok') is True
          and payload.get('app') == 'jobsalvo' else 'occupied')
except ConnectionRefusedError:
    print('free')
except OSError:
    print('unknown')
except Exception:
    print('occupied')
finally:
    conn.close()
PY
}

port_state="$(probe_port)"
server_ready=0
if [[ "$port_state" == "ready" ]]; then
  server_ready=1
elif [[ "$port_state" == "occupied" ]]; then
  echo "安裝中止：$url 已有其他服務或無法確認是 jobsalvo。請停止該服務或改用 JOBSALVO_PORT 後重跑。" >&2
  exit 1
elif [[ "$port_state" == "unknown" ]]; then
  echo "安裝中止：無法確認 $url 的埠狀態；請檢查本機網路設定或改用 JOBSALVO_PORT 後重跑。" >&2
  exit 1
elif [[ "$port_state" == "free" ]]; then
  log="$data_home/.jobsalvo-server.log"
  nohup env JOBSALVO_HOME="$data_home" "$python" "$app_dir/tools/board_server.py" \
    --host 127.0.0.1 --port "$port" >>"$log" 2>&1 </dev/null &
  echo "$!" > "$data_home/.jobsalvo-server.pid"
  for _ in {1..20}; do
    port_state="$(probe_port)"
    if [[ "$port_state" == "ready" ]]; then
      server_ready=1
      break
    elif [[ "$port_state" == "occupied" ]]; then
      echo "安裝中止：$url 回應的不是 jobsalvo 看板；請檢查佔用該埠的服務。" >&2
      exit 1
    fi
    # unknown:看板剛起來那一兩秒連線可能被重設,不算失敗,繼續等(乾淨的 Mac 上實際遇到過);等滿還不行才停
    sleep 1
  done
else
  echo "安裝中止：收到無法辨識的埠狀態 ($port_state)，未啟動或打開看板。" >&2
  exit 1
fi
if [[ "$server_ready" != 1 ]]; then
  echo "安裝中止：看板伺服器沒有在 $url 就緒；請查看 ${log}。" >&2
  exit 1
fi

if [[ "${JOBSALVO_INSTALL_NO_OPEN:-0}" != 1 ]]; then
  open "$url"
fi
echo "jobsalvo 已就緒：$url"
