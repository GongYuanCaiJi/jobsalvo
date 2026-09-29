# agent 的 Chrome:自己一個正常的 Chrome,一直在背景,只靠官方擴充功能操作

agent 用的 Chrome 是 jobsalvo 自己的一個 Chrome 資料夾(`browser.data_dir`,預設 `~/Library/Application Support/jobsalvo/agent-chrome`),跟使用者本人的 Chrome 是兩個程序。只有一種開法:

- 一般 Chrome,用 `open -n -g` 在背景開(`--no-startup-window`),**不開除錯埠、不用無頭**。
- agent 只透過官方擴充功能操作:Codex 的 Chrome 擴充功能、Claude in Chrome。這跟主流產品一樣(Codex、Claude in Chrome、Playwright MCP 的擴充功能模式)。
- 同一時間只准一個 agent 用它(設定裡只能勾一個「用它操作 Chrome」),要開 Chrome 的工作只交給那一個,不換別的。
- 使用者按「🔑 打開 agent 的 Chrome」才把它開在他面前;程式記下「叫出來過」,下次 agent 要用、而且沒有頁面在等他時,關掉再從背景重開。
- 翻譯提示會把視窗帶上螢幕(實測一次),啟動前在它自己的設定檔關掉翻譯(`translate.enabled=false`;`--disable-features=Translate` 實測沒用)。

程式那一側(填完讀回來核對、截圖、看板的「👀 看現在的頁面」)各家用各家的門路,接回**填那一張的同一段對話**:

- Codex:啟動外掛的 `cua_repl`,帶那段對話的身分,不經過模型。
- Claude:讀頁是 Claude 那一輪最後自己跑一次唯讀函式,程式從那一輪的紀錄(stream-json)拿工具的回傳,只收程式碼一字不差的那幾次;`javascript_tool` 的回傳超過 1000 字會被截斷,所以分段拿。截圖(和看板的「👀」)是 `claude -p --resume <那段對話>` 叫它截一張,圖從工具的回傳拿。Claude 的分頁在它替那段對話開的分頁群組,開新對話看不到(2026-09-29 實測)。每截一次約 12 秒、算一次用量,所以 Claude 的「👀」按一次截一次、不自動重截;模型用 Sonnet(Haiku 不能用 Claude in Chrome,實測回「requires permission」)。
- 試過、不採用:讀頁也用 `--resume` 另外叫它讀。同樣的請求重複幾次,Claude 會當成可疑的注入而拒絕,拒絕還留在對話裡越來越難叫;把資料壓縮、編碼、存在頁面上再分段拿,被安全過濾擋下(看起來像偷資料)。

## Considered Options

- **無頭 Chrome,或開除錯埠 + Chrome DevTools MCP**(之前用過):兩邊能做到完全沒有視窗、Codex 和 Claude 同一套工具。但無頭、`--remote-debugging-pipe`、`--remote-debugging-port=0` 都會讓 `navigator.webdriver` 變成 true(Chromium `content/child/runtime_features.cc`),實測 104 和 claude.ai 的 Cloudflare 都擋下。固定埠號不會讓它變 true,但本機任何程式都能接上,而且還有其他特徵;把瀏覽器改成像一般 Chrome 是在規避機器人檢查,不做。
- **使用者 Chrome 裡的一個設定檔**(最早的做法):使用者正在用 Chrome 時,外掛開分頁建的視窗跟著一起被放到最前面,蓋住他的畫面。
- **一般 Chrome、在背景、官方擴充功能(採用)**:104、claude.ai 正常載入;Codex 按「Apply with LinkedIn」開授權頁、Claude 按按鈕,都沒有東西搶到前景(2026-09-29 實測)。
- **讓視窗保證不上螢幕**:Chrome 對不搶焦點的新視窗下 `orderWindow:NSWindowBelow relativeTo:mainWindow`(Chromium `native_widget_ns_window_bridge.mm`),app 還沒有視窗時等於「排在所有視窗最底下、在螢幕上」;macOS 大多沒照做(把它藏起來),約 50 次啟動有 2 次照做了,視窗出現在沒被蓋住的第二台螢幕,但從沒變成前景。隱藏 app(`open -j`、`hide`)會讓 Chrome 停止畫畫面、截圖逾時;移到別的桌面沒有公開 API(Chromium 原始碼註解 FB22128442);`LSBackgroundOnly` 要改 Chrome 本體、破壞簽章。沒有找到公開、有文件、又能照常畫畫面的做法,這一點照實寫在使用說明。

## Consequences

- Claude 看不到頁面自己開的彈出視窗(在它的分頁群組外,[anthropics/claude-code#96516](https://github.com/anthropics/claude-code/issues/96516) 還開著),用 Claude 時「Apply with LinkedIn」這類一鍵帶入做不到;Codex 做得到。
- Claude 擴充功能在 Chrome 剛開起來後大約 20 秒才連得上(實測 8 秒還沒、20 秒有),派 Claude 之前先等它看得到那個 Chrome(`agent_chrome.wait_claude`),等不到照實回報。
- 擴充功能跟 Codex、Claude Code 講話要靠 Chrome 的原生訊息設定;用自己資料夾的 Chrome 只讀自己資料夾底下的 `NativeMessagingHosts`,程式每次啟動前只把 Codex 和 Claude Code 那兩份連過來。Claude Desktop 那份跟 Claude Code 共用同一個擴充功能,擴充功能會先接它,Claude Code 就連不上([anthropics/claude-code#88395](https://github.com/anthropics/claude-code/issues/88395)),所以不接。
- 從使用者的設定檔複製時,Codex 外掛和 Claude 擴充功能的儲存區都不帶過去:帶過去的話兩邊身分一樣,分不出誰是誰(Claude 會去操作使用者自己的 Chrome)。
- 所有等他的頁都處理完,程式把整個 agent Chrome 關掉。Claude 開的分頁沒有便宜的門路逐一確認,看板上記著、還沒送出的就當成還在等他;每次開之前清掉上一輪留下的分頁群組(`Default/Sync Data`,Chrome 沒有「不要存關掉的群組」的開關,只動 agent 專用的設定檔,而且只在它關著時刪)。
- 網站的真人驗證不替他按、不規避:卡上寫明,讓他在自己的瀏覽器投。
