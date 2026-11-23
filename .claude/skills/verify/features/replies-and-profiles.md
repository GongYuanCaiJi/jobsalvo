# 回音與平台履歷

使用者從看板查詢已投出職缺回音，並檢視平台履歷同步狀態。

## Sub-features

- `R1` 「查應徵進度」批次入口 `button[data-replyrun="1"]` 和卡片單筆入口 `button[data-runone="replies|<fid>"]`。
- `R2` 卡上的回音區：查過日期、回音清單、agent 改狀態的說明與 `button[data-ocundo="<fid>"]`「不對,復原」，以及 `[data-rpmaybe]`「知道了」、`[data-rpdone]`「做好了」。
- `R3` 設定頁「你的履歷」`details[data-fold="cfg:resumes"]` 的平台履歷狀態列 `.cfg-row.cfg-sync`、「全部同步」`button[data-cfsync="{}"]`、每份的「同步這份履歷」`button[data-cfsync='{"platform":…,"lang":…,"variant":…}']`，以及同步清單裡的「看過這一版」`[data-cfsyncreview]` 和「恢復選取段落」`[data-cfsyncrestore]`。
- `R4` 已投出卡片的結果列 `button[data-oc="<|iv|offer|rej|ghost|wd>"]`：等回音、面試中、Offer、沒錄取、沒下文、我不去了。

## How to get to it (user POV)

從已投出分頁操作「查應徵進度」並檢視卡片回音狀態，在卡片的結果列手動改結果；從「設定」的「你的履歷」查看各平台履歷的同步狀態。

## Driving it with ego lite

Preconditions: 網址是 `example.test` 的卡是副本假卡，不拿來當目標卡。副本有一張已投出、結果不是沒錄取或我不去了、且有 agent 判斷狀態（卡上有「不對,復原」）的卡片；設定頁有登記的平台履歷。

- `R1`: 進入 `[data-tab="sent"]`，展開目標卡的公司列，先點 `button[data-omore="1"]`，再點 `button[data-runone="replies|<fid>"]`；等 `/api/rev` 的 `replies.phase="done"`。再按批次 `button[data-replyrun="1"]`，等 `replies.finished_at` 更新。副本不連外，兩個入口都只回 `msg`「副本未執行查應徵進度」、`replies.n` 是 0。完成時 `#replybar` 顯示「上一輪 … 查完:副本未執行查應徵進度」，時間跟 `/api/rev` 批次那一輪的 `replies.finished_at` 同一分鐘（比 `/api/rev` 晚約 5 秒才重畫；單筆跑完它已經是「查完」，舊的那行不算），卡片的 `/api/state` 與操作前相同。
- `R2`: 在目標卡記下 `/api/state` 的 `oc`、`oc_auto`，按 `button[data-ocundo="<fid>"]`「不對,復原」。等 API 的 `oc_auto` 清掉、`oc` 回到 agent 改之前的值，`#savebar.clean` 後確認卡上復原鈕消失、狀態鈕回到對應狀態。卡上有 `[data-rpmaybe]` 或 `[data-rpdone]` 時各按一次並確認按鈕消失；沒有時記下「本卡沒有」。
- `R3`: 點 `[data-tab="cfg"]`，展開 `details[data-fold="cfg:resumes"]`，記下每個 `.cfg-row.cfg-sync` 的「平台 · 語言 · 版本」與狀態字。按 `button[data-cfsync="{}"]`，等 `/api/rev` 的 `sync.phase` 出現且不在跑（副本是 `incomplete`）。副本只演練畫面，每份結果都是「副本只演練畫面，未實際同步」。完成時 `sync.n` 是按下時等待同步的列數。再按第一個單份 `button[data-cfsync='{"platform":…}']`：副本不帶選擇，仍演練全部登記的份數，每份同樣是「副本只演練畫面，未實際同步」。按第一個 `[data-cfsyncreview]`：`#snack` 顯示「已記下你看過這一版同步清單」，`GET /api/settings` 那份 `profile_status[].sync_review.reviewed` 變成 `true`，「看過這一版」少一顆（只寫副本的登記表）。沒勾段落按 `[data-cfsyncrestore]`，`#snack` 顯示「先勾選要恢復的頁面段落」；勾一個 `[data-cfsync-change]` 再按，`#snack` 帶「恢復平台履歷只可從現行看板執行」，不開始同步。副本沒有尚未看過的清單或可恢復的段落時，該項記為 `NOT VERIFIED`；Automated coverage 的 `test_profile_sync` 紅時，0 顆不是缺資料，是看過狀態算錯，照壞掉記。真的恢復只能從現行看板執行，記為 unreachable；真實同步走[平台履歷同步](real-platform-profile-sync.md)。
- `R4`: 在 `[data-tab="sent"]` 找一張結果是「等回音」的卡，記下 `/api/state` 的 `oc`。按 `button[data-oc="iv"]`，`#snack` 顯示「已記成「面試中」」，API 的 `oc="iv"` 且按鈕變 `.on`；在提示消失前按 `.snack-undo`，等 `#savebar.clean`（約 2 秒）後 API 回到原值。

Evidence: 操作前後畫面、卡片 ID 和去識別狀態摘要；實際平台流程依[平台回音](real-platform-replies.md)與[平台履歷同步](real-platform-profile-sync.md)執行。

## Gotchas

結果不明、頁面無法連線和沒有新回音是不同狀態，證據照畫面及程式回報記錄。本機入口結果依[驗收基準](README.md#驗收基準)記錄；真 agent 和真平台流程依相關平台功能頁驗證。

## Coverage

- Files: `board/board.js`、`tools/board_server.py`、`tools/job_fake.py`。
- Sync commit: `90a486a`。
- Break: 結果列按「面試中」送出空結果（`outcome` 事件的 `s` 寫成空字串）。Fails at **R4**：沒有「已記成「面試中」」提示，API 的 `oc` 沒變成 `iv`。
- Break: 「不對,復原」把結果記回 agent 改的值（`outcome` 事件用 `oa.s` 不用 `oa.from`）。Fails at **R2**：`oc` 沒回到改之前。
