# 面試與職缺檢視

使用者在面試分頁檢視每場面試的題目和答案、編輯題目，並在已投出分頁的漏斗看投遞結果。

## Sub-features

- `I1` 面試分頁 `button[data-tab="iv"]`、面試／共有題／題庫切換 `[data-ivview="iv|share|bank"]` 和面試卡片 `[data-ivopen]`。
- `I2` 題目逐字稿、版本 `[data-ivpk]`、大字讀稿 `[data-ivread]`、題目編輯 `[data-ivedit]`，以及每場的追問 `[data-ivkid]` 和從題庫加題 `[data-ivpick]`。
- `I3` 題目階段 `[data-ivstage]`、題庫新增 `[data-ivnew]`，以及已投出分頁的漏斗 `#funnel`。
- `I4` 單場頁的面試時間：新增 `.iv-round-acts [data-ivround][data-ivat=""]`、編輯 `[data-ivround]`、刪除 `[data-ivrdel]`。
- `I5` 單場頁的「從這場拿掉」`[data-ivlink]` 和提示裡的 `.snack-undo` 復原。
- `I6` 單場頁的「預覽、匯出面試資料」`button[data-ivsheet="<職缺網址>"]`：視窗內嵌 PDF 預覽 `iframe.iv-sheet-frame`（依序是自己的履歷、平台履歷、附件、面試備忘），附「下載 PDF」「開啟 PDF」。

## How to get to it (user POV)

選擇 `[data-tab="iv"]` 進入面試分頁，點一場面試再點題目展開答案；「已投出」分頁下方的漏斗列出投出、回音、面試和 Offer 數。

## Driving it with ego lite

Preconditions: 副本有可用的面試資料或答案記錄，面試分頁和統計摘要可以顯示。

- `I1`: 點 `button[data-tab="iv"]`；畫面在單場頁時先點 `[data-ivopen=""]` 回清單。點 `[data-ivview="bank"]`，出現 `details[data-ivst]` 四站；點 `[data-ivview="share"]`，出現 `.iv-sharenote`「每一場面試都會有的題」說明和共有題清單；再點 `[data-ivview="iv"]`，出現 `.iv-jrow` 場次列。
- `I2`: 看板記得上次的檢視，先點 `[data-ivview="iv"]` 回場次清單，再點一個 `.iv-jrow[data-ivopen="<職缺網址>"]`（值是完整職缺網址，不是卡片 ID），出現 `h2.iv-ptitle`。題目列預設收合，點 `details.ivrow[data-iv="<題目 id>"] > summary` 展開一題有 `[data-ivpk]` 的題（版本鈕只在有兩個以上版本時出現；挑有兩顆以上 `[data-ivpk]` 的題，都沒有就在 `verification-notes.txt` 記下版本切換沒驗到）；點未選的版本，它變成 `.on`、逐字稿換成該版。點 `[data-ivread]`（每個版本一顆，取第一顆），`#rzmodal .rd-t` 顯示題目；題目有多個版本時是「<題目> · <版本>」。按 `#rzmodal .rzm-x` 關閉，同一題仍展開。點同一題的 `[data-ivedit]`，把限時 `#ive-lim` 改成另一個值，按 `[data-ivesave="1"]`，提示「存好了」；重開編輯確認新值，再改回原值存檔並重開確認。`#rzmodal` 關閉後保留內容，以 `display:none` 判斷關閉。點 `[data-ivpick]` 出現「加題到…」和 `[data-ivpkq]` 題目清單，點 `[data-ivkid]` 出現「…的追問」且 `#ive-parent` 是該題；兩者都按 `.rzm-x` 關閉、不存。
- `I3`: 點同一題 summary 上的 `[data-ivstage="<題目 id>"]`，出現 `#ivstmenu` 四個 `[data-ivst-set]`；選任一項會寫入題庫，所以按 `Esc` 關閉。按 `[data-ivview="bank"]` 到題庫頁，按 `[data-ivnew="1"]`，在 `#ive-t` 填唯一測試題後按 `[data-ivesave]`：`#snack` 顯示「存好了」，題庫多一個 `[data-ivedit]`；按這題的 `[data-ivedit="<題目 id>"]`，在編輯視窗按 `[data-ivedel]`，題目消失、題數回到原本。點 `[data-tab="sent"]`，展開 `#funnel > summary.fn-sum`，出現 `.fn-t`。完成時題庫沒有新增題目，漏斗數字可讀。
- `I4`: 在有面試時間的單場頁記下 `.iv-round` 個數。按 `.iv-round-acts [data-ivround][data-ivat=""]`，填 `#ivr-date`、`#ivr-time`，用 `selectOption` 選 `#ivr-how`（下拉選單），按 `#rzmodal [data-ivrsave]`，提示「面試時間記好了」。重新整理後 `.iv-round` 多一個，新的那輪 `.iv-round-t` 顯示剛填的日期、時間和方式。按該輪的 `[data-ivround]`，編輯視窗帶出同樣三個值；按 `[data-ivrdel]`，提示「刪掉這一輪了」。重新整理後 `.iv-round` 回到原個數。
- `I5`: 記下單場頁題目數和第一個 `[data-ivlink]` 所在的 `[data-ivsrcgrp]`。按它，題目數少 1，`#snack.on` 顯示「從這場拿掉了」；在提示消失前按 `.snack-undo`。重新整理後題目數回到原值，同一個 `[data-ivlink]` 回到原本的 `[data-ivsrcgrp]`。
- `I6`: 在單場頁按 `button[data-ivsheet]`，等 `#rzmodal` 出現 `iframe.iv-sheet-frame` 且 `#ivsheet-wait` 消失；「下載 PDF」「開啟 PDF」兩個連結的 `href` 都是 `/api/iv/pack?job=<職缺網址>`。只看不下載，按 `.rzm-x` 關閉。PDF 內容含答案，遮罩沒有驗證過能蓋住 iframe 裡的畫面，這個視窗不截圖，在 `verification-notes.txt` 用文字記下按鈕和連結。

Evidence: 面試摘要、答案區前後畫面、職缺卡片 ID、計數與結果摘要；答案證據只保留欄位名和狀態，隱私遮罩依主 skill Evidence。`.iv-round-w` 是面試地點備註，常含對方窗口姓名電話，截圖要遮；已投出漏斗的「履歷」列顯示履歷版本名，用遮罩截圖的 `text` 遮掉。

## Gotchas

若副本沒有面試或答案資料，對應入口記為 `NOT VERIFIED`；找不到有兩個以上版本的題時，`I2` 的版本切換記為 `NOT VERIFIED`。編輯視窗比畫面高，點裡面的欄位前先捲到它。答案頁中的表單答案屬於履歷個資，證據只保留欄位名和狀態。外站登入和實際頁面比較依平台功能頁執行。

## Coverage

- Files: `board/board.js`、`tools/board_server.py`、`tools/iv_sheet.py`。
- Sync commit: `90a486a`。
- Break: 面試資料視窗的連結改指 `/api/iv/sheet`（`ivOpenSheet` 的網址）。Fails at **I6**：「下載 PDF」「開啟 PDF」的 `href` 不是 `/api/iv/pack?job=<職缺網址>`。
- Break: 「刪掉這一輪」只跳提示不刪（`data-ivrdel` 處理不送 `ivPost`）。Fails at **I4**：重新整理後輪數沒回到原本。
