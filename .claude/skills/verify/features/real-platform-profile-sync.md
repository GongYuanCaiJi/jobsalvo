# 平台履歷同步

驗收 `sync.py` 把原始履歷這次改的字交給 agent 存進一份登記的平台履歷；agent 叫拍改前改後，程式比對並照實列出。

## Sub-features

- `PFS1` 選定副本 `profiles.json` 裡登記的平台鍵（例如 `104.com.tw`、`linkedin.com`、`1111.com.tw`），可再用 `--lang`、`--variant` 縮到一份。
- `PFS2` 一輪同步：改前改後截圖、逐處比對與交件單；沒有要改的就不派 agent。
- `PFS3` 看板副本同步狀態、版本清單、「看過這一版」之後才算跟上，以及恢復（`--restore-json`，看板的復原按鈕）。

## How to get to it (user POV)

設定頁「你的履歷」列出已登記的平台和版本，按「全部同步」或單份同步（看板副本只演練畫面）。先跑 [Automated coverage](../SKILL.md#automated-coverage) 列的對應測試並在副本操作相同入口。

## Driving it with ego lite

Preconditions: 副本設定登記至少一份平台履歷；`$PLATFORM` 是副本 `profiles.json` 的平台鍵（`python3 -c 'import json,sys; print([k for k in json.load(open(sys.argv[1])) if not k.startswith("_")])' "$HOME_COPY/profiles.json"`）；使用者已登入，並在場同意這一份要真的存進平台。

- Action: 這個命令會把改動存進使用者的真實平台履歷，沒有只比對不存的模式。使用者在場並指定要存的那一份時，對它執行一次；其他情況不跑，記為 `NOT VERIFIED`（原因：同步會改真實平台履歷，需要使用者在場同意）。
- Command: `CODE=$(cat "$RUN/code.path"); HOME_COPY=$(cat "$RUN/home.path"); STATE=$(cat "$RUN/state.path"); TMPROOT="$HOME_COPY/.verify-tmp"; JOBSALVO_HOME="$HOME_COPY" AGENT_BOARD="$STATE" EVIDENCE_TMP="$HOME_COPY" TMPDIR="$TMPROOT" TMP="$TMPROOT" TEMP="$TMPROOT" SYNC_TMP="$HOME_COPY/.sync" uv run --project "$CODE" python "$CODE/tools/sync.py" --board "$STATE" --platform "$PLATFORM" --lang "$LANG_KEY" --variant "$VARIANT"`。`SYNC_TMP` 必填，沒設時程式直接出錯。
- Observe: `$HOME_COPY/.sync/sync_status.json` 的 `phase` 是 `done`（沒有要改的是 `nothing`），`results` 每份一列 `ok` 和 `msg`；`$HOME_COPY/evidence/_sync/<輪>/` 有改前改後截圖和比對結果。用本輪驗證 TaskSpace 重讀同一份平台履歷，核對程式列出的每處改動。結果判定依[共用驗收](../references/real-platform-common.md#操作邊界與完成條件)。
- Evidence: 保存平台名稱、命令結束碼、遮罩後比對欄位和平台前後讀取結果；證據路徑依[證據位置](../references/real-platform-common.md#證據位置)。

## Gotchas

`TMPDIR`、`TMP`、`TEMP` 和 `SYNC_TMP` 指向副本，履歷文字只寫進副本的暫存目錄。跟上只在使用者按了最新一版「看過這一版」之後成立；同步完還沒按時，看板顯示待看過，不算失敗。

## Coverage

- Files: `tools/sync.py`、`tools/profile_sync.py`、`tools/sync_records.py`。
- Sync commit: `90a486a`。
- Break: 最新同步清單沒看過也不提醒（`profile_sync` 拿掉「平台履歷最新同步清單尚未看過」的判斷）。Fails at **Automated coverage**：`test_profile_sync` 的 `ConfirmScreenSyncNotice.test_a_behind_profile_with_an_unreviewed_list_shows_both`。真實平台這一跑記為 unreachable：同步會寫到真實平台，要使用者在場同意。
- Break: 同步清單沒按「看過這一版」也算看過（`status` 的 `reviewed` 不看 `reviewed_at`）。Fails at **Automated coverage**：`test_profile_sync` 的 `SyncBaseline` 兩項、`test_accept_sync` 的 `RestoreTest`；看板 R3「看過這一版」0 顆。
