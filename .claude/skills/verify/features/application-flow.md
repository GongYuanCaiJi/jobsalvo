# 幫你填表與結果

使用者檢視待填表與已填表職缺，從佇列或卡片入口啟動填表、修正或結果確認。

## Sub-features

- `A1` 「可以投了」分頁的批次填表 `button[data-applyrun="fill"]`、可收合的「填表進度」`#filllistbar` 與 `button[data-fillgo="<fid>"]`。
- `A2` 卡片單張填表 `button[data-runone="apply|<fid>"]`、修改入口 `button[data-applyfixopen="<fid>"]` 和交付修改 `button[data-applyfix="<fid>"]`。
- `A3` 確認、取消確認與送出佇列 `button[data-approve]`、`button[data-unapprove]`、`button[data-applyrun="submit"]`；驗證板只測本機假路由。
- `A4` 逐條確認答案：`button[data-ansq="1"]`「一條一條確認答案」打開視窗，`[data-ansq-ok]`「這樣可以」、`[data-ansq-skip]`「先跳過」、`[data-ansq-prev]`「上一條」。

## How to get to it (user POV)

從「可以投了」檢視填表佇列和投遞狀態。待處理卡片使用批次填表或「只讓 agent 填這張」；需要修改時展開卡片的修改欄。已填表和待確認卡片從同一分頁查看並使用對應確認入口。

## Driving it with ego lite

Preconditions: Doctor 通過，副本有一張狀態允許填表的真實職缺卡。記下卡片 ID 和操作前 `/api/state` 摘要。

- `A1` 按 `[data-tab="ship"]`，點開 `#filllistbar` 裡收合的「填表進度」`details` 的 `summary`，記錄各個非空狀態列；在 `input[data-runn="apply"]` 填 `1` 後按 `button[data-applyrun="fill"]`，等 `/api/rev` 的 `apply.phase="done"`，再等 `#applybar` 顯示「上一輪填表」（佇列在這之前會重畫）。接著點另一張卡的 `button[data-fillgo="<fid>"]`。完成時分頁是 `ship`，該卡 `article[data-fid]` 有 `.justhere`。
- `A2` 還沒填的卡上有兩顆 `data-runone="apply|<fid>"`：卡上的主按鈕和 `⋯` 選單內的「只讓 Agent 填這張」；已填好的卡只剩 `⋯` 內那一顆。先按卡片的 `button[data-omore="1"]`，再按 `.more-m [data-runone="apply|<fid>"]`，等 API 該卡 `ds="parked"`；在修改狀態卡按 `button[data-applyfixopen="<fid>"]`、填寫 `[data-applynote="<fid>"]` 並按 `[data-applyfix="<fid>"]`。完成時畫面顯示該卡的進度或修改回應。
- `A3` 按 `[data-approve="<fid>"]` 後 8 秒會自動送出，確認和取消放在同一個腳本裡完成：按下後 API 該卡 `ds="confirmed"`；`[data-unapprove="<fid>"]` 約 0.3 秒後才畫出來，等它出現就按（中間不截圖，8 秒內要按到），`data-approve` 重新出現；再等 10 秒確認同一張卡仍在 `ship`、`ds="parked"`，把卡片 ID 和這時的 `ds` 寫進 `state-summary.txt`。接著送出一張：在 A1 填好的卡裡找 `button[data-approve]` 沒停用的（答案還沒確認完時按鈕停用，`title` 寫原因；A1 沒先跑就沒有可按的卡），按下不復原；有已確認的卡時 `button[data-applyrun="submit"]` 才畫出來。8 秒後副本走假確認頁送出，`/api/rev` 的 `apply` 跑完，API 該卡 `ds` 變成 `sent`，卡片移到「已投出」並留在副本。確認又取消的那張回復原狀；真實填表流程依[平台填表](real-platform-application.md)停在送出前。
- `A4` 記下 `/api/state` 的 `__ans__` 待確認數，按第一顆 `button[data-ansq="1"]`（頁面上有兩顆，用 `>> nth=0`），等 `#rzmodal` 顯示 `.ansq-pos`「第 1 / N 條」。按 `[data-ansq-skip]` 換到第 2 條、答案不變；按 `[data-ansq-ok]`，該條的 `inf` 清掉、待確認數少 1。按 `.rzm-x` 結束。

每次動作都等畫面結果出現且 `#savebar.clean` 後重讀 `/api/state`。Evidence: 操作前後畫面、卡片 ID、狀態列和去識別狀態摘要；履歷與表單答案依主 skill Evidence 的規則遮罩。

## Gotchas

本機入口結果依[驗收基準](README.md#驗收基準)記錄。單張填完後畫面重畫、公司列收起，要再點開；卡上的下一步（「要 agent 改」、「確認送出」）由伺服器算，填完後晚幾秒才畫出來，等按鈕出現再按。真實申請表和平台結果依[平台填表](real-platform-application.md)驗證。沒有符合狀態的卡片時記為 `NOT VERIFIED`。「看頁面」`[data-livego]` 會透過 agent 的瀏覽器叫出真實填表頁，副本沒有真實填表 workspace，不要點；這個入口記為 `NOT VERIFIED`（副本沒有真實填表 workspace）。

## Coverage

- Files: `board/board.js`、`tools/board_server.py`、`tools/job_fake.py`、`tools/next_step.py`。
- Sync commit: `90a486a`。
- Break: 逐條確認的「先跳過」不換下一條（`data-ansq-skip` 點擊處理不呼叫 `ansQGo`）。Fails at **A4**：按完仍是「第 1 / N 條」。
- Break: 「取消確認」只跳提示，8 秒後照樣送出（`data-unapprove` 處理不送 `unconfirm`）。Fails at **A3**：10 秒後那張卡 `ds` 是 `sent`。
