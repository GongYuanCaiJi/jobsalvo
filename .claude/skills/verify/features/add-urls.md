# 找缺與貼網址

使用者能從看板找職缺、指定搜尋方向，或貼上職缺網址加入待判斷卡片。

## Sub-features

- `U1` 找缺模式 `button[data-find="wide"]`、`deep`、`dir` 和方向欄 `#find-dir`。
- `U2` 貼網址欄 `#add-urls` 與執行入口 `button[data-addurls="1"]`。
- `U3` 完成、卡片數量、未加入原因和新卡的公司／職稱／平台連結。
- `U4` 指名要找的：卡片 `⋯` 的「找類似的」`[data-seed]`、公司列 `⋯` 的「找這家更多」`[data-coseed]`，累積後在找缺面板按「照這 N 個找」`button[data-find="seed"]`，單項用 `[data-seeddel]` 拿掉。

## How to get to it (user POV)

在「新職缺」等找工作分頁，點開清單上方收合的「找新職缺」（`details#finddet`）。裡面有「更深」「更廣」「照這個方向找」和方向欄，下方是貼網址欄和「加進新職缺」；執行後查看新職缺清單和未加入結果。

## Driving it with ego lite

Preconditions: 副本有找缺入口和網址欄，`doctor.sh` 通過，並有可用於本機入口檢查的非敏感測試 URL。

- `U1`: 在 `[data-tab="none"]` 點 `details#finddet > summary` 展開。依序按 `button[data-find="wide"]`、`deep`，每按一個就等 `/api/rev` 的 `research.finished_at` 更新且 `phase="done"`，再等 `#find-st` 顯示「上一輪（更廣）」或「上一輪（更深）」才按下一個；一輪在跑時三顆都停用。`deep` 要有喜歡或差一點的卡才可按，停用時記為 `NOT VERIFIED`，原因寫進本頁 Gotchas。`#find-dir` 空白時按 `dir` 只出現提示「先寫一句要往哪個方向挖」；填入測試方向再按 `dir`，等 `#find-st` 顯示「指定方向「<方向>」」。
- `U2`: 在 `#add-urls` 輸入 `https://example.test/job-a` 和 `https://example.test/job-nojob`（換行分隔），點 `button[data-addurls="1"]`；欄位立即清空。等 `/api/rev` 的 `add.phase="done"`：副本會加入 `job-a`，並把網址含 `nojob` 的列進 `add.unadded` 附原因。完成時 `add.added=1`、`add.n=2`，畫面出現 `.add-res`「貼的 2 個網址,有 1 個沒有加進新職缺」。
- `U3`: 讀 `GET /api/jobs` 的 `jobs` 陣列，確認有 `id="https://example.test/job-a"` 的卡（副本的 `target` 是「(沙箱)手動加入的職缺」、`chan` 是「直投」）；畫面上展開同名公司列，`a.jd-link` 的 `href` 等於該網址。新卡還沒表態，不會出現在 `/api/state`。保存前後畫面和摘要放入證據。
- `U4`: 在 `none` 展開一家公司，從一張卡的 `button[data-omore="1"]` 按 `[data-seed]`，提示「加進「指名要找的」(1 個)」；再按另一家公司列的 `⋯`（`button[aria-label="<公司> 的其他動作"]`）裡的 `[data-coseed]`，提示變成 2 個。展開 `details#finddet`，有兩個 `[data-seeddel]` 且按鈕是「照這 2 個找」；按 `button[data-find="seed"]`，等 `research.phase="done"`。副本跑完後指名清單清空（`[data-seeddel]` 歸零），`#find-st` 照假流程寫「上一輪（更深）」。

## Gotchas

本機入口結果依[驗收基準](README.md#驗收基準)記錄；實際網站搜尋和讀取公開職缺網址依[平台找缺與讀取職缺](real-platform-discovery.md)的命令與證據驗證。

## Coverage

- Files: `board/board.js`、`tools/board_server.py`、`tools/job_fake.py`。
- Sync commit: `90a486a`。
- Break: 按「加入」後不清空網址欄（`data-addurls` 處理拿掉 `ta.value=''`）。Fails at **U2**：`#add-urls` 還留著網址。
- Break: 公司列「找這家更多」按了不加（`data-coseed` 處理直接 return）。Fails at **U4**：提示沒變成 2 個。
