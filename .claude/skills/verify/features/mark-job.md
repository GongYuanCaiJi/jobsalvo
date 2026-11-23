# 看板瀏覽與表態

使用者在真實職缺卡片上瀏覽公司、職稱和職缺內容，再用表態、篩選和移除動作整理清單。

## Sub-features

- `B1` 分頁 `#tabs button[data-tab]`、公司列 `summary.cohead`、搜尋 `#ftog`／`#q`，以及 `range`、`open`、`crowd`、`cat`、`tag`、`sort` 篩選。
- `B2` 卡片上的 `.fb-b[data-s="like"]`、`.fb-b[data-s="meh"]`、`.fb-b[data-s="dislike"]`、`.fb-b[data-s="grow"]`，畫面由左到右是不喜歡、普通、差一點、喜歡。
- `B3` `button[data-rm="1"]` 移除、`button[data-rm="0"]` 放回，以及動作提示中的 `.snack-undo` 復原。
- `B4` 清單上方「移除 N 張可能已關的」`button[data-riskrm="1"]`（勾選清單 `input[data-bulkx="risk"]`），以及「已移除」頁每個移除日期一顆的整批放回 `button[data-rmback="<YYYY-MM-DD>"]`。
- `B5` 「一張一張看」`button[data-rvstart="1"]`：`←`／`→` 換卡，`1`–`4` 依序是不喜歡、普通、差一點、喜歡，`P` 送去準備履歷，`Esc` 離開。
- `B6` 篩選列的「已封鎖 N 家」`button[data-blocklist="1"]` 打開封鎖清單，每家一個收合的公司列 `details.blkgrp`，點開才看到「解除封鎖」`[data-unblock]`。

## How to get to it (user POV)

從左側欄的分頁瀏覽職缺，展開 `summary.cohead` 公司列並查看 `article[data-fid]` 卡片。搜尋和篩選列在職缺清單上方；每張可操作卡片都有表態按鈕，右側 `button[data-omore="1"]` 打開 `⋯` 選單後可移除。

## Driving it with ego lite

Preconditions: `doctor.sh` 通過，副本有至少一張可表態職缺卡片，且其公司、職稱可辨識。B1 先完成分頁遍歷並回到 `none`，再展開第一個公司列選目標卡。B1 搜尋及 B2、B3a、B3b 全程使用同一張卡，其他分頁都用 `article[data-fid="<target fid>"]` 找回，不換卡。

- `B1` 先逐一讀取 `#tabs button[data-tab]` 的 `data-tab` 值並點開每個分頁；點完後回到 `#tabs button[data-tab="none"]`。若 `#ftog` 的 `aria-expanded` 不是 `true` 才點 `#ftog`（收合時 `#q` 仍算顯示，只是大小 0），並清空 `#q`；再展開第一個 `summary.cohead`，依 DOM 順序選其下第一張網址不是 `example.test`（副本假卡）且包含 `.fb-b[data-s="like"]`、`.fb-b[data-s="meh"]`、`.fb-b[data-s="dislike"]`、`.fb-b[data-s="grow"]` 的 `article[data-fid]`，記下公司、職稱和 `data-fid`。在 `#q` 輸入目標卡職稱（搜尋目前不比對公司名稱欄，見 jobsalvo-dev#805），目標卡仍在清單上，再清空。依序點 `button[data-ctl="range"]`、`open`、`crowd`、`cat`、`tag`、`sort`；篩選會疊加，`range`、`open`、`crowd`、`cat`、`tag` 各選一個可用的 `#ctlmenu .ctlopt:not(.on)`，再點 `[data-clearall="1"] >> nth=0` 復原；若 `#facetf .ff` 仍顯示「篩選:<標籤>」晶片，點 `#facetf .ff b` 的 `✕` 並確認晶片消失，才選下一個。清單可能同時有兩個 `[data-clearall="1"]`；選第一個篩選列按鈕。疊到 0 筆時 `tag` 會消失、`cat` 沒有選項。`sort` 變更前記下 `#ctlmenu .ctlopt.on` 的 `data-val`，選一個不同的 `#ctlmenu .ctlopt:not(.on)` 並確認排序改變，再開 `sort` 選單點 `#ctlmenu .ctlopt[data-val="<原 data-val>"]` 還原。`tag` 沒有標籤可選時記為 `NOT VERIFIED`。完成時各入口都回到預期分頁或清單，沒有 console/UI 錯誤。完成操作後以以下程式列出過濾後的 console 錯誤：

  ```js
  const errors = (await page.events()).filter(e => e.method === 'Runtime.exceptionThrown' || (e.method === 'Runtime.consoleAPICalled' && e.params?.type === 'error'));
  console.log(errors.map(e => e.method === 'Runtime.exceptionThrown' ? e.params?.exceptionDetails?.text : (e.params?.args || []).map(a => a.value ?? a.description ?? a.type).join(' ')));
  ```

- `B2` 依序點喜歡、普通、不喜歡、差一點；每次先依最新 `GET /api/state` 的目標卡狀態選分頁，分頁值為 `rm ? "rm" : (app || s || "none")`，切頁後用 `waitForSelector` 等同一個 `article[data-fid="<target fid>"]` 出現，再點其中相應的 `.fb-b[data-s="like"]`、`.fb-b[data-s="meh"]`、`.fb-b[data-s="dislike"]`、`.fb-b[data-s="grow"]`。按讚若 API 回傳 `app="prep"`（「按 👍 就送去準備履歷中」開啟時），下一步到 `prep` 找同一卡；流程分頁的表態按鈕收在卡上的 `details[data-fold="cmore:<target fid>"]`，先點開它。在 `prep` 按普通或不喜歡會讓卡片退出流程（API 的 `app` 清掉）並立刻離開 `prep` 畫面，依最新 API 切到 `s` 的分頁再確認 `.on`。每次點擊後確認剛點的按鈕有 `.on`，且 `/api/state` 中目標卡的 `s` 等於該按鈕的 `data-s`。完成時只有 `grow` 按鈕有 `.on`，且 API `s="grow"`。
- `B3a` 在目標卡目前的狀態分頁，從 `article[data-fid="<target fid>"]` 內點 `button[data-omore="1"]`，再只點同一個 `article` 內的 `button[data-rm="1"]`；確認 API 中該卡 `rm` 有值且卡片離開目前清單後，在 8 秒提示期限內點 `.snack-undo`。依 Gotchas 的 API 輪詢方式確認 `rm` 未設定且 `s="grow"`，再切到 `grow` 用 `waitForSelector` 確認同一卡片出現。
- `B3b` 在同一張卡再次移除，確認 API 中 `rm` 有值；先等到 `#snack.on` 出現且其中有 `.snack-undo`，再等到 `#snack` 不再有 `.on`，才記為這次提示消失。再點 `[data-tab="rm"]`。在 `rm` 頁比對 `summary.cohead` 上的公司文字，找到目標公司列；若其所屬 `<details>` 收合就點開並確認已展開。於 `article[data-fid="<target fid>"]` 內點 `button[data-rm="0"]`；依 Gotchas 的 API 輪詢方式確認 `rm` 未設定且 `s="grow"`。存檔重畫會讓卡片立即離開 `rm` 畫面；切到 `none` 再回到 `rm` 確認同一卡片不在，再切到 `grow` 確認它出現。
- `B4` 在 `none` 展開「看是哪 N 張,可取消勾選」，只留兩個 `input[data-bulkx="risk"]` 勾選，按鈕變成「移除 2 張可能已關的」；按之前數 `/api/state` 裡 `rm` 是今天的卡（不在 `/api/state` 的卡算沒有 `rm`）；按 `button[data-riskrm="1"]`，這兩張的 `rm` 是今天日期，今天的張數剛好多 2，其他沒勾的不變。到 `rm` 頁按 `button[data-rmback="<今天>"]`（「放回 <月/日> 移除的 2 張」），兩張的 `rm` 都清掉。
- `B5` 在 `none` 按 `button[data-rvstart="1"]`，`body.reviewing` 出現。按 `→` 換到下一張、`←` 回原卡（比對 `#app .rv-card article` 的 `data-fid`）；按 `3`，API 原卡 `s="grow"` 並自動換下一張；在提示消失前按 `.snack-undo`，`s` 清掉且畫面回到原卡。按 `Esc` 離開，`body.reviewing` 消失。
- `B6` 按 `button[data-blocklist="1"]`，`#rzmodal.block-list` 列出已封鎖的公司 `details.blkgrp`；點開第一家的 `summary`，`[data-unblock]` 出現。只讀不按，用 `.rzm-x` 關閉。

Evidence: 看板證據存於 `$EVIDENCE/mark-job/`。依序保存操作前 `before.png`、`B1-tabs-filters.png`、`B2-reactions.png`、`B3a` 復原後 `B3a-after-undo.png`、`B3b` 放回後 `B3b-after-restore.png`、`B4` 已移除頁 `B4-rmback.png`、`B5` 讀卡畫面 `B5-review.png`；另存 `state-summary.txt` 和 `verification-notes.txt`。選不到目標卡時，保存當下畫面為 `B1-no-target.png`，在 `verification-notes.txt` 記下目前分頁、`summary.cohead` 個數、`article[data-fid]` 個數和 `body.reviewing` 是否存在，B1 其餘、B2、B3 記為 `NOT VERIFIED`。`state-summary.txt` 記公司、職稱、完整卡片 ID、入口和 `s`／移除狀態。

## Gotchas

自動保存是非同步的；每次表態、移除、復原或放回後，每 500 毫秒輪詢 `GET /api/state`，最多等 15 秒，直到目標卡預期的 `s`、`app`、`rm` 欄位一致；回傳的鍵就是卡片 ID，直接讀 `state["<target fid>"]`。逾時就記為 `NOT VERIFIED` 並停止該項。移除或放回後切換分頁，以畫面確認卡片去向。沒有可操作卡片或某個動態篩選沒有選項時，把該項記為 `NOT VERIFIED`。

## Coverage

- Files: `board/board.js`、`tools/board_server.py`、`tools/board_doc.py`、`tools/card.py`。
- Sync commit: `90a486a`。
- Break: 封鎖清單不畫「解除封鎖」（`openBlockList` 的按鈕屬性 `data-unblock` 改名）。Fails at **B6**：點開公司列後沒有 `[data-unblock]`。
- Break: 「移除 N 張可能已關的」不看勾選，整批移掉（`riskRemove` 拿掉 `bulkPick`）。Fails at **B4**：今天移除的張數多了不只 2。
