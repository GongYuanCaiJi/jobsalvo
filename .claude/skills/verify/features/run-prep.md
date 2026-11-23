# 準備與客製

使用者把職缺送進準備區、檢視準備狀態，並能從卡片打開客製履歷視窗。

## Sub-features

- `P1` 從職缺卡片按 `button[data-adv="prep"]` 加入準備區。
- `P2` 準備區批次按鈕 `button[data-prep="1"]` 和單卡按鈕 `button[data-runone="prep|<fid>"]`。
- `P3` 「待你決定」卡片的「先客製」`button[data-cust-open="<fid>"]`、檔案選擇 `input[data-custom-select]` 和 `button[data-customize-start="<url>"]`；「可以投了」的卡只在客製版過期或失敗時才有「重新客製 / 上傳」「重跑這份」。
- `P4` 設定「按「喜歡」就送去準備履歷」`input[data-cfcheck="flow.like_to_prep"]` 開著時，按讚直接進準備區，再按一次讚取消。
- `P5` 準備頁的開關「以後新進的也自動準備」`button[data-autoprep="1"]`（只在自動準備關著、有等待中的卡時出現）。

## How to get to it (user POV)

從新職缺、喜歡或普通分頁選一張職缺卡，按「送去準備履歷」，卡片移到「準備履歷」分頁。在卡片 `⋯` 按「只準備這張」或在上方按「準備這 N 張」；卡片準備完會依設定進「待你決定」或「可以投了」；「待你決定」的卡片上有「先客製」，按下打開客製視窗。

## Driving it with ego lite

Preconditions: Launch 和 Doctor 通過；網址是 `example.test` 的卡是副本假卡，不拿來當目標卡。副本有一張可送去準備的真實職缺卡，準備區有可執行卡片；客製項目需有可用履歷檔。記下操作前卡片 ID 和狀態。

- `P1` 在 `[data-tab="none"]` 找到卡片，按 `article[data-fid="<fid>"] button[data-adv="prep"]`。完成時卡片進入準備區，準備數量和 `/api/state` 的 `app` 狀態一致。
- `P2` 在 `[data-tab="prep"]` 展開該卡的公司列，對單卡點 `button[data-omore="1"]`，再按 `article[data-fid="<fid>"] button[data-runone="prep|<fid>"]`；等 `/api/rev` 的 `prep.phase="done"`，該卡依副本流程設定離開準備區（API `app` 變成 `ready` 或 `ship`）。再從 `none` 送兩張卡進準備區，於 `input[data-runn="prep"]` 填 `1`（準備區至少 2 張時才有這格），確認批次按鈕顯示「1 張」後按 `button[data-prep="1"]`，等 `/api/rev` 的 `prep.finished_at` 更新且 `phase="done"`。完成時兩次的 `prep.ready` 都是 1。
- `P3` 到 `[data-tab="ready"]`，逐家展開公司列找一張有 `button[data-cust-open]` 的卡（P2 那張若進了 `ship` 就換一張），按它；確認 `#rzmodal input[data-custom-select]` 至少勾了一個，按 `#rzmodal button[data-customize-start="<fid>"]`。完成時視窗關閉，`/api/rev` 的 `customize.phase="done"`、`customize.url` 等於該卡，`msg` 為「沙箱只模擬客製派工，不會產生 PDF」；副本的卡片不會出現客製狀態。
- `P4` 從 `GET /api/settings` 記下 `effective.flow.like_to_prep`。到 `[data-tab="cfg"]` 展開 `details[data-fold="cfg:flow"]`，勾 `input[data-cfcheck="flow.like_to_prep"]`，按 `button[data-cfsave="1"]`，等 `#cfg-st` 顯示「沒有改動」且 API 為 `true`。在 `[data-tab="none"]` 選一張未表態的卡，按 `.fb-b[data-s="like"]`；`/api/state` 該卡 `s="like"`、`app="prep"`、`likeprep` 有值，`#snack` 顯示「也送去準備履歷了,Agent 會接著準備」。到 `[data-tab="prep"]` 找同一卡，展開 `details[data-fold="cmore:<fid>"]`，再按有 `.on` 的喜歡；API 的 `s`、`app`、`likeprep` 都清掉，卡回到 `none`。最後把設定改回原值存檔。
- `P5` 在 `[data-tab="prep"]` 按 `button[data-autoprep="1"]`；`/api/settings` 的 `effective.flow.auto_prep` 變 `true`，`#snack` 顯示「自動準備打開了,這 N 張也交給它」，開關消失。到 `[data-tab="cfg"]` 展開 `details[data-fold="cfg:flow"]`，取消勾選 `input[data-cfcheck="flow.auto_prep"]` 存檔，API 回到 `false`。

Evidence: 記錄操作前後畫面、卡片 ID、按鈕 selector、進度、保存狀態與 API 摘要；履歷內文和上傳檔名依主 skill Evidence 的規則遮罩。

## Gotchas

副本的 `flow.auto_advance`、`flow.auto_fill` 開著時，準備完的卡會直接進「可以投了」並被假填表；`flow.like_to_prep`、`flow.auto_prep` 開著時，按讚或進準備區就會自動跑。操作前從 `GET /api/settings` 的 `effective.flow` 記下這四個值。客製視窗的附件名稱含姓名，截圖要遮罩。缺少準備卡片、可用履歷或可勾選項目時，將對應入口記為 `NOT VERIFIED`。本機入口、真 agent 和真平台結果依[驗收基準](README.md#驗收基準)及相關平台功能頁記錄。

## Coverage

- Files: `board/board.js`、`tools/board_server.py`、`tools/job_fake.py`、`tools/customize.py`。
- Sync commit: `90a486a`。
- Break: 「先客製」按了不開客製視窗（`data-cust-open` 點擊處理直接 return）。Fails at **P3**：`#rzmodal input[data-custom-select]` 沒出現。
- Break: 打開自動準備只跳提示不存設定（`autoPrepOn` 不呼叫 `cfgSave`）。Fails at **P5**：`effective.flow.auto_prep` 沒變 `true`。
