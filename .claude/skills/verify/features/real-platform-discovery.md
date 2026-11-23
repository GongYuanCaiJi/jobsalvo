# 平台找缺與讀取職缺

驗收 `find.py` 的真實平台找缺流程和 `add_job.py` 讀取指定職缺頁；搜尋 agent 交網址，程式讀每一頁並核對 agent 引的段落，結果寫入跑次副本。

## Sub-features

- `D1` `find.py` 的 `deep`、`wide`、`dir` 模式、`--direction` 搜尋方向，以及指名要找的 `--seed-url`／`--seed-co`（照看板，走 `--mode deep`，範圍縮到這幾張或這幾家）。
- `D2` `add_job.py --url` 讀取指定職缺頁，不論判斷結果一律加進副本（讀不到的頁也照加，卡上照實標）。
- `D3` 找缺結果、來源網址、公司、職稱、每一欄的核對結果與最終卡片。

## How to get to it (user POV)

從「找新職缺」選模式或按「照這 N 個找」，或從看板貼網址欄提供職缺 URL。先跑 [Automated coverage](../SKILL.md#automated-coverage) 列的對應測試並在副本操作相同入口，再執行本頁命令。

## Driving it with ego lite

Preconditions: Launch 和 Doctor 通過，選定一個搜尋模式或一個職缺 `$JOB_URL`，副本有可讀的履歷文字，本機 agent CLI 可用（派 agent 需要額度）。TaskSpace 和平台前置計數依[共用驗收](../references/real-platform-common.md#操作邊界與完成條件)記錄。

- Action: 找缺每個模式各執行一次、指定 URL 每個網址執行一次（程式沒有平台參數，來源由 agent 決定）；程式結束後重新讀取來源職缺頁，核對公司、職稱、網址和平台寫入數。
- Command: `CODE=$(cat "$RUN/code.path"); HOME_COPY=$(cat "$RUN/home.path"); STATE=$(cat "$RUN/state.path"); TMPROOT="$HOME_COPY/.verify-tmp"; export JOBSALVO_HOME="$HOME_COPY" AGENT_BOARD="$STATE" EVIDENCE_TMP="$HOME_COPY" TMPDIR="$TMPROOT" TMP="$TMPROOT" TEMP="$TMPROOT"; CONVERGE_TMP="$HOME_COPY/.research" uv run --project "$CODE" python "$CODE/tools/find.py" --board "$STATE" --mode wide`。`deep` 或 `dir` 時把 `--mode` 換成該模式，`dir` 加 `--direction "$DIRECTION"`；指名要找的用 `--mode deep` 加 `--seed-url "$JOB_URL"` 或 `--seed-co "$COMPANY"`（可重複）。指定 URL 用 `ADD_TMP="$HOME_COPY/.add" uv run --project "$CODE" python "$CODE/tools/add_job.py" --board "$STATE" --url "$JOB_URL"`。`CONVERGE_TMP`、`ADD_TMP` 指向看板讀進度的資料夾，看板的找缺列才會顯示這一輪。
- Observe: 程式結束碼為 0，`$HOME_COPY/.research/converge_status.json`（貼網址是 `$HOME_COPY/.add/add_status.json`）的 `phase` 是 `done`；副本新卡與職缺頁一致；申請、訊息和平台履歷寫入數維持前置值。結果判定依[共用驗收](../references/real-platform-common.md#操作邊界與完成條件)。
- Evidence: 保存去識別命令、結束碼、平台頁面、卡片 ID 和結果摘要；證據路徑依[證據位置](../references/real-platform-common.md#證據位置)（搜尋那一輪在 `_find/`，每張讀到的職缺頁在該卡的資料夾）。

## Gotchas

平台找缺以公開頁面為來源，副本只更新找缺結果；核對申請、訊息和平台履歷寫入數維持前置值。agent 都不能用（額度用完、限流）時 `add_job.py` 不加空卡，讀好的頁存成待寫；這時記為 `NOT VERIFIED` 並寫原因。結果不符時依共用驗收記錄並停止本平台流程。

## Coverage

- Files: `tools/find.py`、`tools/add_job.py`、`tools/web/round.py`。
- Sync commit: `90a486a`。
- Break: 找缺指示不帶封鎖名單（`find.py` 讀 `__block__` 改讀不存在的鍵）。Fails at **Automated coverage**：`test_accept_find` 的 `BlockTest.test_block_only_on_exact_company_or_alias`、`WholeRoundTest.test_the_whole_round`。真實平台這一跑記為 unreachable：要使用者已登入平台、agent 有額度，副本裡無法觸發。
- Break: 找缺指示不帶使用者的排除字（`user_materials` 讀 `exclude_words` 改讀不存在的鍵）。Fails at **Automated coverage**：`test_accept_find` 的 `WholeRoundTest.test_the_whole_round`。
