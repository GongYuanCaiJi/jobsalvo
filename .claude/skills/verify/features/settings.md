# 設定與分類

使用者在看板設定頁檢視設定、維護類別和標籤、查看分類建議與環境狀態。

## Sub-features

- `S1` 設定入口 `button[data-tab="cfg"]`、欄位編輯、儲存 `button[data-cfsave="1"]` 和重新載入 `button[data-cfreload="1"]`。
- `S2` 分類／標籤折疊區 `[data-fold="cfg:cats"]`、欄位 `[data-cfli]`、新增 `[data-cfliadd]` 和刪除 `[data-cflidel]`。
- `S3` 分類建議 `button[data-cfsuggest="1"]`、套用 `[data-cfsugapply="1"]` 與環境檢查 `[data-fold="cfg:doctor"]`。
- `S4` 設定頁頂端的跳轉列 `button[data-cfjump="<data|find|apply|sys>"]`，以及改了沒存就切分頁時的離開視窗 `button[data-cfleave="save|drop|stay"]`。
- `S5` 「🌐 網站筆記」`details[data-fold="cfg:website-notes"]`：依網站分組列出筆記，每則可改 `[data-wn-text]` 後按「儲存筆記」`[data-wn-save]`、按「刪除」`[data-wn-delete]`；最下面用 `[data-wn-url]`、`[data-wn-new-text]` 和「＋ 新增網站筆記」`[data-wn-add="1"]` 新增。

## How to get to it (user POV)

點側欄最下方的「設定」進入設定頁。展開「分類」維護類別和標籤；同一區有分類建議。設定頁另外列出執行環境檢查結果，以及 agent 和使用者記下的網站筆記。

## Driving it with ego lite

Preconditions: Doctor 通過，副本有至少一個類別、一張職缺卡和可讀的履歷文字（分類建議要用）。記下要改的副本欄位原值。

- `S1` 點 `button[data-tab="cfg"]`，展開 `details[data-fold="cfg:replies"]`，把 `input[data-cfn="replies.ghost_days"]` 原值加 1，`#cfg-st` 顯示「● 有改動還沒存」。點 `button[data-cfsave="1"]`，整頁約 0.7 秒後重載並留在 cfg；等 `#cfg-st` 顯示「沒有改動」，重展開後欄位和 `/api/settings` 的 `settings.replies.ghost_days` 都是新值。再改成任意值，點 `button[data-cfreload="1"]`（放棄改動，只在有改動時顯示），欄位回到存過的值且 `#cfg-st` 顯示「沒有改動」。最後改回原值並存檔，API 回到原值。
- `S2` 分別對 `categories` 和 `tags` 各做一次。展開 `[data-fold="cfg:cats"]`，記下 `/api/settings` 的 `settings.board.<categories|tags>` 原值。點 `[data-cfliadd="<categories|tags>"]` 新增一列；類別的新列插在倒數第二（「其他」留在最後），標籤的新列在最後，以 name 為空的那列取 `<index>`。在新列的 `input[data-cfli="<key>|<index>|name"]` 和 `input[data-cfli="<key>|<index>|match"]` 填入唯一測試值（name 不可空白，match 要是合法 regex）；點 `button[data-cfsave="1"]`，等 `#cfg-st` 顯示 `沒有改動`，若分類區已收合就重展開 `[data-fold="cfg:cats"]`，再讀 `/api/settings` 確認新增列已保存。從該列 `data-cfli` 取 `<index>`，點 `button[data-cflidel="<key>|<index>"]` 刪除它；再次儲存並等待 `#cfg-st` 顯示 `沒有改動`，若分類區已收合就重展開，再讀 `/api/settings` 確認 `settings.board.<key>` 完全回到原值。完成時新增列曾保存、同一列已刪除，設定值與操作前一致。
- `S3` 在展開的分類區點 `button[data-cfsuggest="1"]`，等 `/api/rev` 的 `suggest.phase="done"` 且 `finished_at` 跟按之前不同（副本的建議是 `job_fake.py` 產的假建議，只證入口和畫面反應）；用 `waitForSelector` 等 `[data-cfsugapply="1"]` 出現（`/api/rev` 完成後約一兩秒才畫出來）再點，類別和標籤欄位換成建議內容且 `#cfg-st` 顯示有改動。點 `button[data-cfreload="1"]` 放棄，`effective.board.categories` 與操作前相同。環境檢查全部通過時是 `details[data-fold="cfg:doctor"]`（標題「環境檢查:通過」），有未過項目時改為 `.cfg-ch-h`「環境檢查：」標題加 `[data-fold="cfg:doctor-ok"]`；展開存在的那一個讀各列。環境檢查的每一列是 `.cfg-row`；`.cfg-check` 是常駐的「開始前」清單，不能拿來判斷環境檢查。完成時建議套用後已放棄，環境列可讀，副本設定回到操作前值。
- `S4` 依序按四顆 `button[data-cfjump]`，每次等捲動停下，對應的 `#cfg-g-<data|find|apply|sys>` 在畫面內（頁面不夠長時後面幾組捲不到頂端，只捲到底）。改一個欄位不存（每次填的值都要跟 `GET /api/settings` 的現值不同，不然分不出存了沒），按另一個分頁（例如 `[data-tab="none"]`），出現離開視窗：按 `[data-cfleave="stay"]`，等 3 秒再看（真的丟掉改動時，設定約一秒半後重讀、蓋回存過的值）：留在 cfg，欄位還是剛填的值、`#cfg-st` 還是「● 有改動還沒存」；再切一次分頁，離開視窗要再出現，這次按 `drop`，等設定重讀完到目標分頁（約一秒）且 API 沒變；再改一次切分頁按 `save`，到目標分頁且 API 是新值，最後改回原值存檔。
- `S5` 記下 `GET /api/settings/website_notes` 的 `notes`。展開 `details[data-fold="cfg:website-notes"]`，在 `[data-wn-url]` 填 `https://example.test/verify-note`、`[data-wn-new-text]` 填唯一測試字，按 `[data-wn-add="1"]`：`#snack` 帶「網站筆記已存好」，API 多一則同樣文字的筆記。改這則的 `[data-wn-text="<id>"]` 按 `[data-wn-save="<id>"]`，API 的文字跟著變；按 `[data-wn-delete="<id>"]`，API 回到操作前的筆記。

每次儲存後整頁重載、所有折疊區收合；等 `#cfg-st` 顯示 `沒有改動`、畫面上沒有「讀取設定中…」（截圖也等到這時），重展開再讀 `/api/settings` 確認保存。Evidence: 操作前、新增列保存後、刪除列保存後畫面，以及原值／新增／還原狀態摘要；隱私遮罩依主 skill Evidence。

## Gotchas

存檔後要等畫面重畫完才重展開折疊區，太早點會在重畫時又收起來。用副本值做暫時變更並還原。可操作類別、標籤或建議不存在時，該項記為 `NOT VERIFIED`。隔離 server 的健康檢查使用 [`doctor.sh`](../doctor.sh)；設定頁的環境列只記錄畫面可見狀態。

## Coverage

- Files: `board/board.js`、`tools/board_server.py`、`tools/settings_api.py`、`tools/prefs.py`、`tools/website_notes.py`。
- Sync commit: `90a486a`。
- Break: 「＋ 新增網站筆記」按了沒反應（點擊處理認錯屬性 `data-wn-add`）。Fails at **S5**：沒有「網站筆記已存好」，API 沒多一則。
- Break: 離開視窗按「留在這裡」卻把改動丟掉（`stay` 時清掉改動並重讀設定）。Fails at **S4**：再切分頁時離開視窗沒有再出現。
