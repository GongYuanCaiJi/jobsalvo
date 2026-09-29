# 設定幫你填表用的 Chrome

幫你填表(把申請表填到送出前)和查應徵進度,需要 agent 在一個**專門給它用的 Chrome** 裡操作。它是 jobsalvo 自己開的另一個正常的 Chrome(自己的資料夾、自己的程序),一直在背景跑、不會跳到你面前,也碰不到你平常用的 Chrome:你的分頁、帳號、瀏覽紀錄都不會被動到。為什麼這樣做見 [ADR 0003](adr/0003-agent-chrome-own-instance.md)。

- **要看它填的頁**:看板卡上的「👀 看現在的頁面」(手機也能看)。用 Codex 時開著會一直更新;用 Claude 時每按一次截一次(每截一次都要叫 Claude 一次);幫你填表或查應徵進度正在跑時,Claude 不能同時再進去截,先給你那一頁上一輪填好、改好時截的圖(會寫是幾點截的),跑完再按一次看現在的。
- **要親手操作它**:設定頁的「🔑 打開 agent 的 Chrome」。只有你按了才會出現;下次 agent 要用時,程式會把它換回背景。
- **用平台上存好的履歷投遞時的核對**:程式要把平台上那一份讀回來、跟你的履歷逐段比。用 Codex 時程式自己打開那一頁讀;用 Claude 時程式打不開 Claude 的分頁,改由 Claude 在填表那一輪、送出前核對那一輪自己打開那一頁跑一支唯讀函式,程式從紀錄拿結果比(標準一樣)。所以用 Claude 時,這種卡每次送出前會多叫 Claude 一次。
- **只能選一個 AI 操作它**:設定頁「🤖 Agent 與瀏覽器」裡只有一個 agent 能勾「用它操作 Chrome」。填表、查應徵進度都只交給那一個;它不行也不會換別的。Codex、Claude 用哪個都行,不用兩個都裝。

整個設定大約 10 分鐘,只要做一次。

## 1. 選 AI、選要不要複製登入

1. 看板「⚙ 設定 → 🤖 Agent 與瀏覽器」:在要用的那個 agent 勾「用它操作 Chrome」(執行環境 `codex` 或 `claude-code`),按「💾 儲存設定」。
2. (選填)「**從哪個設定檔複製**」:你 Chrome 裡如果已經有一個登入好求職網站、裝好擴充功能的設定檔,選它。第一次建 agent 的 Chrome 時會把登入狀態和擴充功能複製過去(快取不複製)。不選就自己在 agent 的 Chrome 裡登入。

## 2A. 用 Codex

1. 這台電腦要先裝好 Codex(ChatGPT 桌面 App 或 `codex` 指令)。
2. 按「🔑 打開 agent 的 Chrome」,在跳出來的那個視窗打開 [ChatGPT 擴充功能](https://chromewebstore.google.com/detail/hehggadaopoacecdllhhajmbjkdcmajg),按「加到 Chrome」(複製過來、已經裝好的就跳過)。
3. 按「🔌 連接 Codex」,看到「連上了」就好(第一次最多要等一分半)。
4. 允許 Codex 在你要投的網站上傳、下載檔案。Codex 每次上傳履歷、下載平台上的附件都會先問「允許嗎?」,背景跑的時候沒有人能按,就會卡住逾時。要允許哪些網站每個人不一樣:看板「⚙ 設定」的環境檢查會照你的卡(待你決定、可以投了)和登記過的平台履歷,列出還缺哪幾個網站,並給你要貼進 `~/.codex/browser/config.toml` 的那一段(原本允許的網站都留著)。有新網站的卡進來時再看一次就好。

   上傳是填表時傳履歷;下載是核對平台上存好的那份履歷的附件(下載回來跟你電腦上的檔逐位元組比對,確認不是舊版)。

## 2B. 用 Claude Code

1. 這台電腦要先裝好 Claude Code(`claude` 指令),用 `/login` 登入帳號(用 API key 登入的 Claude Code 不能操作 Chrome);要有 Anthropic 的訂閱方案(Pro、Max、Team、Enterprise),Claude in Chrome 才能用([官方說明](https://code.claude.com/docs/en/chrome#prerequisites))。
2. 模型用 Sonnet 或 Opus(空的也可以)。Haiku 不能操作 Chrome(會回「Claude in Chrome requires permission」)。
3. 第一次:打開終端機輸入 `claude --chrome`,出現 Claude in Chrome 的說明畫面按 Enter,然後關掉終端機。沒做這一步,「🔌 連接 Claude」會叫你先做。
4. 按「🔌 連接 Claude」。agent 的 Chrome 還沒裝或沒登入 Claude 擴充功能時,它會把 agent 的 Chrome 開在你面前:在那裡登入 claude.ai、點工具列的 Claude 圖示登入。登入好不用再按一次,設定頁會自己更新成「✅ 確認連得上」。

## 3. 登入你要投的網站

按「🔑 打開 agent 的 Chrome」,在那個視窗登入你要用的網站:查應徵進度用的信箱、你會投的求職平台等。登入一次就會記住。

agent 碰到沒登入、要驗證碼、要密碼的頁面會停下來,出現在看板最上面的「📣 Agent 回報」,請你本人處理,它不會幫你打密碼。

## 做不到、會怎樣

- **網站的真人驗證(Cloudflare 等)**:agent 不替你按、也不規避。卡上會寫「這個網站要真人驗證」,按「🌐 在我的瀏覽器打開」自己投。
- **用 Claude 時的「Apply with LinkedIn」**:網頁自己開的授權小視窗,Claude 看不到(官方還沒解決,[anthropics/claude-code#96516](https://github.com/anthropics/claude-code/issues/96516)),所以用 Claude 時改填一般表單。Codex 看得到那個小視窗。
- **視窗偶爾出現在螢幕上**:Chrome 在 Mac 上開「不搶焦點」的新視窗時,會把它排在所有視窗最底下,但還是在螢幕上;通常 macOS 會把它藏起來,偶爾沒藏,就會在沒被其他視窗蓋住的螢幕(例如第二台螢幕)看到它。它不會搶你正在用的 app、不會搶鍵盤。蘋果沒有公開的方法能保證它不上螢幕又照常畫畫面(見 ADR 0003)。

## 常見問題

- **按「🔌 連接」失敗**:看板會直接講是哪個原因(還沒裝擴充功能、擴充功能沒開、沒登入),照它說的做再按一次。
- **按「🔌 連接 Codex」時問你「還有幾頁填好等你核對」**:重新連接要把 agent 的 Chrome 關掉重開,那幾頁會不見(卡上改成要重填)。先在卡上看完、送出,或確定不要了再按確定。幫你填表、查應徵進度正在跑時不能按連接,等它跑完。
- **用 Claude 幫你填表時說「Claude 90 秒內看不到 agent 的 Chrome」**:agent 的 Chrome 剛開起來時,Claude 擴充功能大約要 20 秒才連得上;超過 90 秒通常是擴充功能被登出了,按「🔌 連接 Claude」重新登入。
