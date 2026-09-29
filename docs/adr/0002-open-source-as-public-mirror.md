# 開源:私人 repo 開發,公開 repo 只當鏡像

開發照舊在私人 repo(改名 `jobsalvo-dev`):issue 當筆記亂寫、PR、CI、截圖分支都在這裡。公開 repo(`jobsalvo`,MIT)只收程式碼:每次合進 main,CI 用 Copybara(Google 開源的 repo 同步工具,設定在 `copy.bara.sky`)把這一版同步過去,一次一個 commit,訊息是 PR 標題(拿掉私人 issue 編號),作者是帳號名。`docs/research/`、`docs/agents/`、`AGENTS.md`、`CLAUDE.md` 不帶過去。issue、PR、留言是 GitHub 另存的資料,不會同步,公開 repo 從零開始。

維護者的筆記和討論都在 issue 裡,又亂又常提到真實求職資料;開發環境和流程一點都不想因為開源改變。私人歷史裡有真名作者欄位和帶真實資料的截圖,鏡像從乾淨的內容開始,不用改寫歷史。

## Considered Options

- **整個 repo 公開、在公開的地方開發**(大多數開源專案的做法):筆記得搬出 issue 另找地方放,歷史要用 `git filter-repo` 改寫、請 GitHub 清快取才能公開。
- **開新的公開 repo、之後改在那裡開發**:歷史乾淨,但筆記一樣得搬走,開發流程整個換地方。

## Consequences

- 公開 repo 不是開發的地方:外人開的 issue、PR 要搬回私人 repo 處理(現在還沒有外人,等有了再定怎麼搬)。
- 同步前照樣掃私人字串(檔案和 commit 訊息);有命中就不推。PR 標題會公開,寫標題時跟檔案一樣不放真實公司名、個人路徑。
- 私人 repo 的 issue 不會公開;issue 仍走 `tools/issue.py` 掃過再送,因為之後可能挑幾張搬去公開 repo。
