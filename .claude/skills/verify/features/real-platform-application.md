# 平台填表

驗收 `fill.py --stage fill` 讓 agent 讀平台申請頁、填入表單，程式拍改前改後並核對 agent 引的字，頁面停在送出前。

## Sub-features

- `F1` 指定副本卡片、平台申請頁、登入和驗證碼。
- `F2` 表單欄位與答案、履歷上傳和填表記錄（agent 叫 `form_plan.py` 配答案）。
- `F3` 填表後的頁面留存（卡上的 `apply.workspace`）、TaskSpace 內重讀和看板進度摘要。

## How to get to it (user POV)

從可投遞卡片取得平台 URL、履歷版本和填表入口。先跑 [Automated coverage](../SKILL.md#automated-coverage) 列的對應測試並在副本操作相同入口，再啟動真實流程。

## Driving it with ego lite

Preconditions: Launch 和 Doctor 通過，副本設定及附件已複製，`$JOB_URL` 是投遞狀態表（`tools/delivery_state.json`）允許 `fill_start`、而且可投遞夾已照卡上的選擇建好的卡；不符合時程式不派 agent，`apply_status.json` 的 `n` 是 0。使用者已登入，本機 agent CLI 可用。`APPLY_TMP` 指向看板讀取進度的 `$HOME_COPY/.apply`，`#applybar` 才會顯示這一輪。TaskSpace 和平台前置計數依[共用驗收](../references/real-platform-common.md#操作邊界與完成條件)記錄。

- Action: 對一個平台的指定卡片執行一次 `fill`；讓頁面停在送出前，等待程式結束並保留該 TaskSpace。
- Command: `CODE=$(cat "$RUN/code.path"); HOME_COPY=$(cat "$RUN/home.path"); STATE=$(cat "$RUN/state.path"); TMPROOT="$HOME_COPY/.verify-tmp"; JOBSALVO_HOME="$HOME_COPY" AGENT_BOARD="$STATE" EVIDENCE_TMP="$HOME_COPY" TMPDIR="$TMPROOT" TMP="$TMPROOT" TEMP="$TMPROOT" APPLY_TMP="$HOME_COPY/.apply" uv run --project "$CODE" python "$CODE/tools/fill.py" --stage fill --board "$STATE" --url "$JOB_URL"`。只用 `--stage fill`；`submit` 會送出申請，`confirm` 屬於看板確認送出的流程，驗收不跑。
- Observe: `$HOME_COPY/.apply/apply_status.json` 的 `phase` 是 `done`、`results` 有這張卡；卡片顯示填表狀態；申請頁畫面停在送出前，平台申請筆數和訊息數維持流程前的值。結果判定依[共用驗收](../references/real-platform-common.md#操作邊界與完成條件)。
- Evidence: 保存交件單摘要（證據輪裡 agent 交回的原檔叫 `handoff-*.json`）、頁面截圖及申請／訊息前後計數。原始表單答案和履歷附件留在副本；平台證據路徑依[證據位置](../references/real-platform-common.md#證據位置)。

## Gotchas

填表頁停在送出前；頁面結果和前置條件依[真實平台共用驗收](../references/real-platform-common.md#操作邊界與完成條件)確認。`flow.fill_parallel` 決定同時填幾張；指定 `--url` 時只有那一張。表單需要額外人工步驟時，記下未完成的條件並停止本平台流程。

## Coverage

- Files: `tools/fill.py`、`tools/form_plan.py`、`tools/form_record.py`、`tools/delivery_state.json`。
- Sync commit: `90a486a`。
- Break: 可投遞夾沒照卡上的選擇建好也照填（`fill.eligible` 拿掉 `ship.chosen_not_built`）。Fails at **Automated coverage**：`test_accept_fill` 的 `PickMoreTest.test_a_folder_built_for_another_resume_is_not_filled`。真實平台這一跑記為 unreachable：要使用者已登入平台、有可填的卡。
- Break: 交件單列了未達條件仍算填好（`interpret` 不看 `unmet`）。Fails at **Automated coverage**：`test_accept_fill` 的 `UnmetTest.test_unmet_requirements_are_listed_with_their_source`。
