# 平台回音

驗收 `progress.py` 查應徵進度：agent 開好信箱與應徵紀錄頁交件，程式讀一次工作區、核對 agent 引的段落，照核對過的回音更新跑次副本的卡。

## Sub-features

- `Q1` 指定已投出卡片或已投出清單的回音查詢。
- `Q2` 平台來源可讀、不可讀、部分完成、拒絕或無新回音結果。
- `Q3` 由平台證據更新的副本卡片和交件紀錄。

## How to get to it (user POV)

從已投出分頁選一張有平台紀錄的卡片。先跑 [Automated coverage](../SKILL.md#automated-coverage) 列的對應測試並在副本操作相同入口，再啟動真實平台流程。

## Driving it with ego lite

Preconditions: Launch 和 Doctor 通過，副本有已投出卡片 `$JOB_URL`，使用者已登入信箱與平台，本機 agent CLI 可用。TaskSpace 和平台前置計數依[共用驗收](../references/real-platform-common.md#操作邊界與完成條件)記錄。

- Action: 對指定卡片執行一次回音查詢（平台由卡片網址決定，程式沒有平台參數）；沒有要查的卡時程式不派 agent，狀態檔 `msg` 是「沒有要查的卡」。程式結束後重新讀取同一回音頁。
- Command: `CODE=$(cat "$RUN/code.path"); HOME_COPY=$(cat "$RUN/home.path"); STATE=$(cat "$RUN/state.path"); TMPROOT="$HOME_COPY/.verify-tmp"; JOBSALVO_HOME="$HOME_COPY" AGENT_BOARD="$STATE" EVIDENCE_TMP="$HOME_COPY" TMPDIR="$TMPROOT" TMP="$TMPROOT" TEMP="$TMPROOT" REPLY_TMP="$HOME_COPY/.replies" uv run --project "$CODE" python "$CODE/tools/progress.py" --board "$STATE" --url "$JOB_URL"`。
- Observe: `$HOME_COPY/.replies/reply_status.json` 的 `phase` 是 `done`；程式回報與來源頁證據一致；平台申請和訊息計數維持前置值。結果判定依[共用驗收](../references/real-platform-common.md#操作邊界與完成條件)。
- Evidence: 保存去識別命令、結束碼、程式交件狀態、來源頁截圖及平台前後計數；證據路徑依[證據位置](../references/real-platform-common.md#證據位置)。

## Gotchas

回音流程讀取平台頁面並更新副本，平台訊息數維持原值。平台沒有新回音、來源不可讀和流程失敗是不同結果；依共用驗收區分並記錄。

## Coverage

- Files: `tools/progress.py`、`tools/web/round.py`。
- Sync commit: `90a486a`。
- Break: 卡上存著舊版沒核對到的回音，這一輪核對到後一律重放（`progress._verify` 不看 `effect_pending`）。Fails at **Automated coverage**：`test_accept_progress` 的 `StoredTest.test_a_reply_already_on_the_card`。真實平台這一跑記為 unreachable：要使用者已登入平台、agent 有額度。
- Break: 從哪天起查忽略上次查過那天（`since_of` 只看 `sent_at`）。Fails at **Automated coverage**：`test_accept_progress` 的 `PromptTest.test_material_and_single_read`。
