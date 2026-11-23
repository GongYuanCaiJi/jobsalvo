---
name: verify
description: 驗證 jobsalvo 看板畫面與真實平台流程。改到看板、board_server、找缺、準備、填表、回音或履歷同步時，依功能清單在真實資料副本上操作並保留去識別證據。
---

# 驗證 jobsalvo

## Launch

每輪各有自己的跑次資料夾、連接埠和證據資料夾，好幾個 agent 可以同時各跑一輪。從 repo 根目錄啟動：

~~~sh
.claude/skills/verify/launch.sh
~~~

啟動器向系統要一個沒人用的連接埠（要指定就設 `VERIFY_PORT`；`8898`、`8899` 保留給使用者，會被拒絕），在 `~/.cache/jobsalvo-verify/run-XXXXXX/` 建本輪的程式快照和資料副本，最後依序印出看板網址、程式快照、資料副本、本輪資料夾、證據資料夾。程式快照取自目前工作樹：HEAD 加上還沒 commit 的改動，`.gitignore` 忽略的檔不帶。啟動失敗時它自己清掉這一輪。後面每一步都用這一輪的值：

~~~sh
RUN=<啟動器印的本輪資料夾>
URL=http://127.0.0.1:$(cat "$RUN/port")
EVIDENCE=$(cat "$RUN/evidence.path")
~~~

**完成條件：**啟動器印出五行並以 0 結束，`$RUN/port` 和 `$RUN/evidence.path` 存在。

## Doctor

~~~sh
.claude/skills/verify/doctor.sh "$RUN"
~~~

唯讀檢查本輪 server 的 PID、process group、程序指令來自 `$RUN/code/`、程式快照與目前工作樹一字不差、連接埠、首頁、`/api/state` 和副本 state 路徑；所有 HTTP 請求只打本輪 localhost。檢查未通過時記為 `NOT VERIFIED`，執行 Cleanup，修正後重新 Launch 和 Doctor。

**完成條件：**輸出 `ok:`，且 server PID、監聽連接埠、程式快照與資料副本路徑都符合本輪記錄。

## Automated coverage

先跑和改動相關的測試並沿用結果，再到 Drive 驅動其餘預期。從程式快照跑，結果對應本輪的程式：

~~~sh
set -- test_board test_delivery_state   # 換成下表該功能頁的模組（bash、zsh 都照字拆開）
echo "modules: $*" >"$EVIDENCE/automated.txt"
(cd "$RUN/code" && PYTHONPATH=tests uv run python -m unittest "$@") 2>&1 \
  | sed -E -e 's/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/<email>/g' -e 's#/(Users|home)/[^/ ]+#/<home>#g' -e 's#/(private/)?var/folders/[^ ]*#<tmp>#g' \
  >>"$EVIDENCE/automated.txt"
grep -E '^(Ran |OK|FAILED)' "$EVIDENCE/automated.txt" | tail -2
~~~

| 功能頁 | 測試模組 | 證明的預期 |
|---|---|---|
| [mark-job](features/mark-job.md) | `test_board` `test_board_block_undo` `test_board_techerr_return` `test_delivery_state` `test_next_step` | 看板資料路徑回歸、封鎖批次移除與復原、出錯頁放回、投遞狀態 × 事件表、卡片下一步 |
| [board-shell](features/board-shell.md) | `test_agent_report` `test_board_theme_transition` `test_board_modal_surface` `test_glossary_words` `test_board_run_notifications` | 回報寫得進看板且不洗版、淺深色同步換色、對話框底色、畫面不用避用詞、輪詢與完成通知 |
| [settings](features/settings.md) | `test_settings_save` `test_board_settings_focus` `test_board_save_order` `test_board_tags` `test_doctor` `test_website_notes` | 存檔不蓋掉別處的值並擋錯值、儲存列焦點、存檔倒序、標籤切換、環境檢查紅燈、網站筆記增改刪 |
| [add-urls](features/add-urls.md) | `test_web_only` `test_find_terms` `test_find_time` `test_job_fake` | 貼網址加入、找缺條件、找缺時間上限、副本假流程照張數跑 |
| [run-prep](features/run-prep.md) | `test_customize` `test_board_custom_diff` `test_autopilot` | 客製流程、客製差異視窗、流程自動往下跑 |
| [application-flow](features/application-flow.md) | `test_fill` `test_gate` `test_form_plan` `test_form_record` `test_board_answer_draft` `test_board_answer_jump` `test_board_answer_search_empty` `test_send_name_swap` | 填表交件單、安檢門、程式配答案、答案只有一個真相、逐條確認的草稿與跳轉、送出檔名改了要重填 |
| [replies-and-profiles](features/replies-and-profiles.md) | `test_accept_progress` `test_profile_sync` | 查應徵進度改卡規則、平台履歷登記與同步基準 |
| [interview-and-insights](features/interview-and-insights.md) | `test_iv_sheet` `test_interview_bank_export` `test_board_iv_table` | 面試備忘、題庫匯出、逐字稿表格 |
| [real-platform-discovery](features/real-platform-discovery.md) | `test_accept_find` `test_find_terms` `test_find_page_decisions` | 找缺與貼網址從入口跑的行為清單 J 段、找缺條件、讀網址指令 |
| [real-platform-application](features/real-platform-application.md) | `test_accept_fill` `test_fill_page_decisions` `test_gate` | 填表、修改、送出從入口跑的行為清單 F 段、依頁面的判斷、安檢門 |
| [real-platform-replies](features/real-platform-replies.md) | `test_accept_progress` | 照核對過的回音改卡、看不懂的不改（行為清單 P 段） |
| [real-platform-profile-sync](features/real-platform-profile-sync.md) | `test_accept_sync` `test_profile_sync` | 同步從入口跑的行為清單 A、S 段、登記與同步基準 |

這些測試跑在假資料和假流程上（看板副本的派工走 `job_fake.py`；四件網站任務的驗收換上假瀏覽器邊界，見 `test_fake_browser`），不證明真實資料上的畫面路徑，那部分由 Drive 證明。

`test_fill_page_decisions` 要用 Google Chrome 開頁面（CI 有裝）；機器上沒有 Chrome 時它每一項都報「這台機器沒有 Chrome」，這時沿用同一個 commit 的 CI 結果，沒有就記為 `NOT VERIFIED` 並寫原因。不要把 `CHROME_BIN` 指到 Ego Lite。

整套測試和 `tools/board_check.py` 由 CI（`.github/workflows/tests.yml`）在每次 push 跑；同一個 commit、沒有未 commit 改動時，CI 綠燈可以沿用。有測試失敗時，表上列了那個模組的每個功能頁都記 `NOT VERIFIED`，附失敗的測試名和斷言；真實平台頁也一樣，測試紅了就是壞了，不記成無法到達。看板功能頁的入口照樣到 Drive 逐條操作、各自記結果，測試紅不代表畫面不用驗。能改程式的修好後重跑。live 驗出測試沒抓到的缺口時，在 `tests/` 補回歸測試，跟產品修正放同一個 PR。

**完成條件：**改到的功能頁所列模組都跑過，`automated.txt` 裡 `Ran N tests` 下一行是 `OK`（或照上面條件沿用 CI 綠燈）；不是時，失敗模組對應的功能頁已記 `NOT VERIFIED`。

## Drive

先讀 [`features/README.md`](features/README.md)，選擇與改動相符的功能頁和每次都要跑的 [看板外框](features/board-shell.md)，依序操作每頁列出的每個使用者入口。

本輪在 Ego Lite 只用一個 TaskSpace，名稱取證據資料夾名。第一次執行 `ego-browser nodejs` 時建立並記下 `spaceId`，之後每次執行都用同一個 id 接回，在 `p1` 打開本輪看板網址：

~~~js
const task = await taskSpace("jobsalvo-verify-<證據資料夾名>"); // 之後每次改成 taskSpace(<spaceId>)
const page = task.page("p1");
await page.goto("<URL>");
console.log(task.spaceId);
~~~

操作時沿用可辨識的標籤、分頁、公司列和職缺卡片。看板會記住上次的分頁、篩選和「一張一張看」；開頁後先確認目前分頁，`body.reviewing` 存在時按 `Esc` 離開。各分頁的卡片收在 `summary.cohead` 公司列下，點開公司列才有 `article[data-fid]`。

會派工的入口（準備、填表、找缺、貼網址、查應徵進度、同步、客製、分類建議）以 `GET /api/rev` 對應欄位的 `phase` 和 `finished_at` 判斷這一輪跑完。畫面上的狀態列比 `/api/rev` 晚幾秒才重畫；要點同一列的按鈕前，先等它顯示「上一輪」。新加的卡片在 `GET /api/jobs`，`GET /api/state` 只有表態過的卡。最後一次驅動結束、執行 Cleanup 前呼叫 `await task.finish({ keep: [] })`。真實平台流程使用功能頁指定的命令與前置條件；結果分類依 [驗收基準](features/README.md#驗收基準) 記錄。副本缺少某個入口需要的前置資料時，該入口記為 `NOT VERIFIED`，原因寫進該功能頁的 Gotchas。

設定存檔後整頁重載，舊的 `page.fetch` 會失效：讀 API 用 Node `fetch("<URL>/...")`，存檔後先 `waitForLoadState` 再操作。`GET /api/jobs` 很大，在 Ego Lite 的 Node 裡讀會讓程序崩掉，從 shell 讀。`#rzmodal` 打開時有動畫，等它顯示後再等約 0.7 秒才點裡面的按鈕。`ego-browser nodejs` 從 stdin 讀腳本（`ego-browser nodejs < drive.js`）。Ego Lite 的 `page.click`、`page.fill` 碰不到畫面外或被蓋住的元素（設定頁捲動後常被底部儲存列蓋住），先把元素捲進畫面，還是被蓋住時改在 `page.evaluate` 裡設值並送 `input`、`change` 事件；它點完會把畫面捲回按鈕所在，要量捲動位置的入口（設定頁跳轉列）改用元素的 `click()`。`page.evaluate` 沒有參數時不要傳 `undefined`。副本的 `flow` 設定照真實資料，`auto_advance`、`auto_fill` 開著時卡片會自己往後走；操作前記下 `GET /api/settings` 的 `effective.flow`，判斷卡片位置時把它算進去。

**完成條件：**所選功能頁的每個入口都有操作、結果和證據記錄，每個入口標成 `VERIFIED` 或 `NOT VERIFIED` 並寫原因。

## Evidence

所有交付證據放在 `$EVIDENCE/`，位置是 `~/.cache/jobsalvo-verify/evidence/<YYYYmmdd-HHMMSS>-<跑次>/`。看板操作保存操作前、操作後截圖，以及只含該職缺公司、職稱、卡片 ID 與驗收欄位的狀態摘要；實際值必須與畫面一致。看板證據依功能頁名稱放在 `$EVIDENCE/<feature-name>/`；卡片 ID 記在 `state-summary.txt`，不放進路徑；證據檔名照功能頁的規定，沒有規定時用入口代號開頭（例如 `S1-saved.png`）。平台證據的位置和格式依[真實平台共用驗收](references/real-platform-common.md#證據位置)。

保存前逐張檢視截圖與證據檔，遮罩姓名、Email、電話、表單答案、履歷內文與上傳檔名；公司名、職稱和職缺內容照原樣保留。卡片上「為什麼適合你」、agent 的判讀和挑選理由是 agent 依履歷寫的，屬履歷內文；附件名稱、履歷版本名含姓名或私密詞，屬上傳檔名；面試場次的對方窗口姓名電話和來信引文也要遮。看板截圖一律依[遮罩截圖流程](references/evidence-capture.md)先遮再截，看過後複製到 `$EVIDENCE/<feature-name>/`。

`$RUN/home/raw/` 的原始截圖由 Cleanup 清除。完整 `/api/state`、表單記錄、平台回報和暫存輸出留在 `$RUN/home/`；所有 CLI 的 `TMPDIR`、`TMP`、`TEMP` 指向副本。執行附帶的隱私掃描器：

~~~sh
.claude/skills/verify/privacy-scan.sh "$EVIDENCE"
~~~

`privacy-scan.sh` 依 repo 的私密詞清單與內建 Email／家目錄路徑規則掃描證據文字。命令需輸出 `0 處符合` 並以成功狀態結束；圖片證據逐張目視檢查。

**完成條件：**每個結果都有操作前後證據與可重看的狀態摘要；證據文字掃描輸出 `0 處符合`，圖片逐張檢視，並依驗收基準標註結果。

## Cleanup

從 repo 根目錄執行清理器，只清這一輪：它停止身分符合本輪記錄的看板並刪除 `$RUN`；`$EVIDENCE` 保留。看板停不掉時它保留 `$RUN`、印出原因並以非零結束。保存命令輸出：

~~~sh
PORT=$(cat "$RUN/port")
if CLEANUP_OUTPUT=$(.claude/skills/verify/cleanup.sh "$RUN" 2>&1); then
  CLEANUP_STATUS=0
else
  CLEANUP_STATUS=$?
fi
printf '%s\n' "$CLEANUP_OUTPUT"
printf '%s\n' "$CLEANUP_OUTPUT" | sed -e "s|$HOME|~|g" >"$EVIDENCE/cleanup.txt"
printf 'cleanup status: %s\n' "$CLEANUP_STATUS" >>"$EVIDENCE/cleanup.txt"
~~~

**完成條件：**Drive 結尾已呼叫 `task.finish({ keep: [] })`、`CLEANUP_STATUS` 是 0、`$RUN` 已刪除、`lsof -nP -iTCP:$PORT -sTCP:LISTEN` 沒有輸出，且 `$EVIDENCE` 和證據仍存在。

## Helpers

- [`launch.sh`](launch.sh)：建立本輪副本並啟動看板；用法見 [Launch](#launch)。
- [`doctor.sh`](doctor.sh) `<跑次資料夾>`：唯讀檢查本輪 server 與副本；用法見 [Doctor](#doctor)。
- [`cleanup.sh`](cleanup.sh) `<跑次資料夾>`：停止本輪 server 並刪除本輪資料夾；用法見 [Cleanup](#cleanup)。
- [`privacy-scan.sh`](privacy-scan.sh) `<證據資料夾>`：掃描交付證據文字；用法見 [Evidence](#evidence)。
- [`mask-shot.mjs`](mask-shot.mjs)：在 Ego Lite 內遮罩私密區塊後截圖；用法見[遮罩截圖流程](references/evidence-capture.md)。
- [`map-check.sh`](map-check.sh)：檢查功能清單的結構（README 與功能頁一一對應、五個章節依序、`Coverage` 的檔案和 commit 存在、每頁有記錄的破壞、連結都指得到）；改功能頁後從 repo 根目錄跑 `.claude/skills/verify/map-check.sh`，要印 `0 個問題` 並以 0 結束。

## Rules

- 每輪驅動改到的功能頁，加上每次都要跑的[看板外框](features/board-shell.md)。
- 同一個失敗修三次還不過，停下來問使用者。
- 不准跳過、重跑或等時間讓檢查變綠；等待只等功能頁寫明的條件（API 欄位、selector、提示文字）。
- 正式看板 `8899`、沙箱板 `8898` 和原始資料 `~/jobsearch` 不准驅動、不准寫入；`~/jobsearch` 只給啟動器讀取來建副本。
