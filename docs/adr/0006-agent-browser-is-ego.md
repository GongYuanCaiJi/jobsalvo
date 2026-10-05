# agent 的瀏覽器換成 ego lite:一張卡一個工作區,程式和兩家 agent 用同一套指令

取代 ADR-0003。agent 的瀏覽器改用 [ego lite](https://lite.ego.app/):一個專門給 agent 用、跟使用者自己的瀏覽器分開的瀏覽器,第一次從使用者的瀏覽器匯入登入狀態。每張卡在裡面有自己的工作區;Codex 和 Claude 都用 `ego-browser nodejs` 跑腳本操作它,一段腳本能把一整頁讀完、填完、上傳完;程式自己也用同一套指令讀頁、截圖、下載平台履歷的附件,不再叫 agent 代讀、不再從 agent 的工作紀錄撈結果。程式這一側只有一個門路(`chrome_door.EgoDoor`)。

為什麼換:舊做法(自己一個 Chrome、靠官方擴充功能)兩家能力不同,程式讀頁、截圖要繞回 agent 那一輪,上傳下載要一長串繞路規則,新使用者要裝擴充功能、接原生訊息、改允許清單;Lever 一張表 agent 要叫 31 次工具、將近 16 分鐘。0003 不用除錯埠的理由是被 104、Cloudflare 擋,但擋的原因是無頭和除錯管道讓 `navigator.webdriver` 變成 true,不是 CDP 本身;ego 實測 `navigator.webdriver=false`,104 平台履歷頁與 Lever 都正常(2026-10-04)。

## Considered Options

- **留在 0003 的做法,補更多繞路**:Claude 看不到頁面自己開的彈出視窗、程式讀不到 Claude 的分頁,這兩件繞不掉。
- **Playwright 自己開 Chromium**:無頭或自動化旗標一樣讓 `navigator.webdriver` 變 true,被擋。
- **ego lite(採用)**:兩家同一套指令,程式自己讀得到每一頁;工作區之間共用登入,不搶使用者的畫面。

## Consequences

- 瀏覽器本體閉源、免費下載,公證開發者 CITRO LABS PTE. LIMITED;`ego-browser` 指令與 skill 是 MIT。上游停更或改授權時,要退回 0003 的做法(見下)。
- 只支援 macOS(本專案本來就只支援 macOS)。
- 不清使用者的狀態:程式與給 agent 的指示一律不呼叫清 cookie、快取、儲存區的操作——從工作區清 cookie 會清到主空間的登入([citrolabs/ego-lite#303](https://github.com/citrolabs/ego-lite/issues/303)),腳本也讀得到瀏覽器存的憑證([#315](https://github.com/citrolabs/ego-lite/issues/315))。`tests/test_ego_door.py` 掃程式與指示,出現就失敗。
- 新開、還沒捲動過的頁截圖會逾時([citrolabs/ego-lite#183](https://github.com/citrolabs/ego-lite/issues/183)):同一個分頁最多重試 6 次,不換工作區。
- 卡在登入或驗證碼時沿用接手:回報講明要做什麼,使用者按「👀」把那一頁叫到面前(`open -a "ego lite"`),處理完按「修改」,agent 在同一個工作區接著做。
- 退回 0003:舊的門路(`CodexDoor`、`ClaudeDoor`、`tools/agent_chrome.py`)在 #374 刪除,要退回就從那個 commit 之前的版本把它們拿回來,設定頁和環境檢查一起還原;卡上記的是工作區,舊門路接不回來,停著的頁要重填。
