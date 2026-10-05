# 設定幫你填表用的瀏覽器(ego lite)

幫你填表(把申請表填到送出前)和查應徵進度,需要 agent 在一個**專門給它用的瀏覽器**裡操作:[ego lite](https://lite.ego.app/)。它跟你平常用的瀏覽器分開,第一次從你的瀏覽器匯入登入狀態;每張卡在裡面有自己的工作區,agent 在工作區裡做,不搶你的畫面。Codex 和 Claude Code 用同一套做法,用哪個都行。為什麼這樣做見 [ADR 0006](adr/0006-agent-browser-is-ego.md)。

只支援 macOS。設定一次大約 5 分鐘。

## 1. 安裝並完成第一次匯入

1. 到 <https://lite.ego.app/> 下載安裝 ego lite,打開它。
2. 照畫面完成第一次設定:選「從 Chrome(或你在用的瀏覽器)匯入」,登入狀態就會帶過去。這一步也會裝好 `ego-browser` 指令(在 `~/.local/bin`)。
3. 讓 agent 認得 ego 的用法:把 ego lite 附的 `ego-browser` skill 放到 `~/.agents/skills/ego-browser/`(Codex)與 `~/.claude/skills/ego-browser/`(Claude Code)。

## 2. 在看板上勾 agent、檢查

1. 看板「⚙ 設定 → 🤖 Agent 與瀏覽器」:在要用的 agent 勾「用它操作 ego」(執行環境 `codex` 或 `claude-code`),按「💾 儲存設定」。
2. 按「檢查 ego」。沒過的話,環境檢查會講缺哪一樣(沒裝、指令跑不起來、還沒匯入)、怎麼補。

## 平常怎麼用

- **看它填的頁**:卡上的「👀 看現在的頁面」(手機也能看),開著會一直更新。
- **卡在登入或驗證碼**:agent 不會替你輸入密碼、驗證碼。卡上的回報會講要做什麼;按「👀」把那一頁叫到面前,處理完按「修改」,agent 在同一個工作區接著做。能用「用 Google 登入」這類已登入帳號帶過去的,agent 會先試。
- **網站要你重新登入**:直接在 ego lite 裡登入一次,之後每張卡都用得到(工作區之間共用登入)。

## 不會做的事

- 不清 ego 裡的 cookie、快取、儲存區(會清到你的登入),也不碰你自己的瀏覽器。
- 不替你按送出:每一張都停在送出前,等你按「確認送出」。
