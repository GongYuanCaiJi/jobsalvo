# jobsalvo

**全自動求職 agent:從找缺到投履歷,一條龍。**

找缺、判斷對不對味、挑履歷、填申請表、查應徵進度都交給 AI agent。只支援 macOS。

> salvo = 齊射。準備履歷時一次裝填一批,你下令,一批一起送出。

## 它怎麼運作

```
🔎 找新職缺 → 🆕 新職缺 → 👍 你表態 → 📝 準備履歷中 → 🤔 待你決定 → 🚀 可以投了 → 📮 已投出 → 🎤 面試準備
   (agent)                            (agent 挑履歷)              (agent 填表,你確認才送)  (agent 查應徵進度)
```

- **看板**是一個自帶資料的 HTML 檔(`board.html`),由本機伺服器服務;電腦、手機、agent 讀寫的是同一份。
- **找缺**有三種:更深(你按過 👍、💪 的公司和同型的缺)、更廣(板上還沒出現過的職能)、照你寫的方向。
  程式先清掉重複的、職稱撞到「一定不要」的,agent 再逐張拿 JD 對照你對相似舊卡的表態,決定要不要送到你眼前。每輪預設最多 15 分鐘(可改,0 = 不限)。
  有詐騙徵兆的(照 FTC 的求職詐騙說明,例如要先繳錢、要敏感個資)不送;看起來掛著沒在招人的幽靈職缺照樣送,但卡上標出來。
- **準備履歷**:agent 讀每張的 JD,從你勾選的履歷裡挑一份、挑語言,程式建好要寄的檔案。想為某個缺改履歷,可以交給 agent 客製,或自己上傳。
- **幫你填表**:agent 在它專用的 Chrome(跟你的 Chrome 分開、平常在背景跑,不會跳到你面前)裡把表單填好、停在送出前。你按卡上的「👀 看現在的頁面」看它填好的樣子(手機也行),要親手看就在設定頁打開 agent 的 Chrome,
  按「✅ 確認送出」後,同一段對話才在同一頁送出,8 秒內可以復原。**程式不會自動送出。**
- **查應徵進度**:程式先在 agent 的 Chrome 讀 Gmail 和各平台的應徵紀錄,交給 agent 判斷誰回了什麼;讀不到的來源才讓 agent 自己去看。程式照證據改狀態(可復原)。
- **🔁 自動流程**(預設全開):按 👍 就開始準備、準備好自動進「可以投了」、自動填表、每天 09:00 查應徵進度;都停在送出前。
- agent 做不到、要你本人處理的事(登入、驗證碼),會出現在看板最上面的「📣 回報」。

## 需要什麼

- macOS、Google Chrome、git(沒有的話跑 `xcode-select --install`)。
- 至少一個裝好、登入好的 agent:Codex CLI(`codex`)、Claude Code(`claude`)或 Command Code(`command-code`)。
  環境檢查只讀本機的登入紀錄,不送請求、不查額度。
- 三種都能找缺、判斷、準備履歷;Command Code 不能用瀏覽器。會碰瀏覽器的兩件事:
  - **幫你填表**:Codex,或 Claude Code(模型要 Sonnet 或 Opus,Haiku 會被 Claude 的擴充功能擋)。兩種都一樣由程式自己讀回那一頁核對、截圖;只裝其中一種就好。用 Claude 時「Apply with LinkedIn」這類網頁自己開的授權小視窗做不到(Claude 看不到),改填一般表單。
  - **查應徵進度**:Codex 或 Claude Code 都可以。用 Codex 時程式先自己讀信箱和平台頁;用 Claude 時交給 Claude 在 agent 的 Chrome 裡讀。
  - 同一時間只讓一個 agent 用 Chrome(設定裡只能勾一個)。
- 幫你填表與查應徵進度要一個 agent 專用的 Chrome:jobsalvo 自己開的另一個正常的 Chrome(自己的資料夾、自己的程序,一直在背景跑、不搶你的畫面),步驟見 [設定幫你填表用的 Chrome](docs/agent-chrome.md)。沒有時仍可找缺。

Python 和套件(Markdown、pypdf、Playwright)由 [uv](https://docs.astral.sh/uv/)(管 Python 和套件的工具)裝在程式資料夾的 `.venv`,
版本鎖在 `uv.lock`;安裝程式會在沒有 uv 時自動裝。Playwright 用這台電腦的 Chrome,不另外下載瀏覽器。

## 開始用

```bash
bash -o pipefail -c 'curl -fsSL https://raw.githubusercontent.com/GongYuanCaiJi/jobsalvo/main/tools/install.sh | bash'
```

程式裝在 `~/Applications/jobsalvo`,你的資料放在 `~/jobsearch`,看板開在 http://127.0.0.1:8899。
要換地方(例如同一台電腦再裝一份試用),在指令前面加 `JOBSALVO_APP_DIR=…`、`JOBSALVO_HOME=…`、`JOBSALVO_PORT=…`;
新建的資料夾有自己的暫存資料夾和 agent Chrome 連線紀錄,不會互相踩。

安裝程式依序:下載程式(已裝過就 `git pull --ff-only`)、裝 uv 並 `uv sync`、建資料夾(agent 清單照這台電腦裝了哪個 CLI 來定)、
跑環境檢查(Chrome、git、至少一個能用的 agent;必要項沒過就列出處理方式並停止)、啟動看板並打開瀏覽器。
第一次打開會落在「⚙ 設定」;「🚦 開始前」只要求上傳一份履歷。設定頁的「🩺 環境檢查」顯示同一份檢查結果。

安裝程式啟動的看板只開在本機,重開機就不在了。要開機自動啟動,到「⚙ 其他」打開;這樣起的看板有裝 [Tailscale](https://tailscale.com/) 時也開在 Tailscale 位址,手機才連得到。

| 設定頁的哪一塊 | 做什麼 |
|---|---|
| 📄 你的履歷 | 上傳履歷(每份各語言一個檔,可附 CSS),寫一句「什麼時候用」;你看得懂的語言(選简体中文時看板介面也換成簡體) |
| 📎 你的附件 | 隨履歷一起投的檔(作品集、求職信);勾選能搭哪幾份履歷 |
| 🪄 改履歷的規則 | 你自己寫的客製做法;每份履歷與附件可以各指定一份,留白用產品附的通用規則 |
| 🔎 找缺 | 職稱一出現就不要的字、要小心的字 |
| 🔎 找缺與判斷的做法 | 五份做法(找缺共通、更深、更廣、指定方向、判斷),可以拿預設改一份自己的 |
| 🧭 你的喜好 | 分「使用者自訂」和「Agent 假設」;agent 不改你寫的,你改過的假設會轉成自訂 |
| 🗂 分類 | 職缺類別與標籤;可以讓 agent 照你的履歷和表態建議一份 |
| 🏢 公司別名 | 同一家公司的不同寫法;內建清單認不出你這一行的職稱時,在這裡加職稱字 |
| 🔁 自動流程 | 上面「它怎麼運作」那幾步各自開關;最多停幾張等你確認送出(預設 5) |
| 📝 填表做法 | 連結欄填什麼、哪些勾選框要勾…幫你填表時原文交給 agent |
| 📬 查應徵進度 | 沒下文的天數、查應徵進度的信箱網址(Gmail 任一帳號,或其他信箱) |
| 🤖 Agent 與瀏覽器 | agent 清單(執行環境、模型、思考強度,由上往下試;只能勾一個「用它操作 Chrome」);第一次建立時從哪個 Chrome 設定檔複製登入狀態;連接勾了的那一家;打開 agent 的 Chrome 去登入或親手看 |
| ⚙ 其他 | 開機自動啟動、程式版本與「更新」、資料夾自動留版的時間 |

看板上:「🔎 找新職缺」可以貼自己找到的職缺網址;卡片 ⋯ 可以改類別;待你決定、可以投了的卡可以「📎 這張用自己的檔」;
「🎤 面試準備」可以新增、編輯題目和逐字稿;哪一種「跑」失敗了,旁邊有「看紀錄」。

每一種「跑」都有 ⏸ 暫停、⏹ 停止。找新職缺、準備履歷、客製按停止會先收工,已經做完的收下,收工中再按一次才強制停;
幫你填表、查應徵進度按停止直接停,已寫進卡上的照樣留著。

想先看看長什麼樣子(假資料,按鈕跑的是假流程):

```bash
cd ~/Applications/jobsalvo
uv run python tools/demo.py /tmp/demo.html
uv run python tools/board_server.py --state /tmp/demo.html --port 8898
```

## 更新

「⚙ 其他」會顯示程式版本;GitHub 上的 main 有新的,就會出現「更新到 …」,按一下會快轉到最新的 main 並 `uv sync`。
看板是開機自動啟動的會自己重啟;不是的話照提示重新啟動看板。重跑一次安裝指令也是同一件事。
程式資料夾切在別的分支、或有沒提交的變更時,按鈕只說原因、不動,請自己用 git。

## 移除

```bash
bash ~/Applications/jobsalvo/tools/uninstall.sh
```

停掉看板、移除開機自動啟動、清掉快取和暫存、刪掉程式資料夾(含 `.venv`)。你的求職資料夾留著;要一起刪加 `--data`(刪了拿不回來)。

## 程式與資料分開

程式資料夾不放你的任何資料。資料夾的位置依序看:`JOBSALVO_HOME`、目前目錄往上找到的第一個 `jobsalvo.json`、目前目錄。裡面有:

```
jobsalvo.json      設定(設定頁寫的,也可以手改,一般的 JSON)
board.html         看板與全部職缺、你的標記
prefs.md  preference-note.md  apply-rules.md   表態原話、你的喜好、填表做法
resume/            上傳的履歷、附件、CSS
custom/            改履歷的規則(skills/)、每張卡自己的檔與客製版
card-summaries/    每個職缺研究到什麼
prepare/           準備履歷時 agent 的判斷(fill.json)
ship/              要寄的檔案:<卡名>-<網址 sha1 前 12 碼>/
.research/         找缺每一輪的紀錄
```

看板每次存檔後,程式會在資料夾的本機 Git 版本庫記一筆(不設定遠端)。找缺或分類建議讀不出上傳的履歷時,可以貼上文字,存在 `.resume-paste.md`;
舊資料夾的 `resume.md` 也會當備援讀。

## 履歷

- Markdown 原稿由無頭 Chrome 排成 PDF,有上傳 CSS 就用它,沒有就用瀏覽器預設樣式(產品不附版型);PDF 原樣使用。排出來超過一頁時設定頁會提醒。
- **要寄的檔案** `ship/<卡名>-<id>/`:履歷和附件的個別檔、程式產的合併 PDF(`merged.pdf`),以及 `ship.json`(`variant`、`lang`、`files`、`merged`);原稿或 CSS 改了會重做。
- **客製**:「待你決定」和「可以投了」的卡可以一次選多份履歷或附件交給同一個 agent,照各檔的改履歷的規則修改。
  輸出要通過 PDF 與頁數檢查,等你看過收下才替換要寄的檔案;退回重寫或檢查中不能送出。直接上傳自己的 PDF 算已收下。
- 面試題庫的正本在看板,資料夾的 `interview-bank.md` 是單向匯出;要從別處匯入,格式見 [docs/interview-bank.md](docs/interview-bank.md)。

## 改了東西之後

```bash
uv run python tools/reconcile.py      # 冪等:換上新的看板外殼、整理要寄的檔案、驗收、清過期夾
uv run python -m unittest discover -s tests
uv run python tools/board_check.py    # 用無頭 Chrome 在示範看板上實際點一遍;改介面時先跑 --fast
pre-commit install                    # commit 前的檢查(.pre-commit-config.yaml,pre-commit 要另外裝);私人字串清單放 .git/info/private-words
```

完整測試和看板檢查由 CI 在 PR 上跑。動到設定、要寄的檔案、找缺與準備的 PR,合併前跑一次 `uv run python tools/realdata_check.py`(只動真實資料的副本)。
每支程式最上面的說明就是它的規格;要改規則,改程式,不要只改文件。
