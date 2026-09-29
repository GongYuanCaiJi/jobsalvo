# 更新跟 main,不發版

裝好的 jobsalvo 就是 main 的一份 clone,設定頁的「更新」按鈕發現遠端 main 比較新就顯示「有新版」,按下去 = fetch + 快轉 main(tools/update.py);安裝指令重跑時的 `git pull --ff-only` 是同一件事。不打版本 tag、不另開 `stable` 分支:使用者是維護者邀請的幾個朋友,每個 PR 合進 main 前都跑過 CI,main 本身就夠穩;多一條「發版」的步驟,維護者每次都得記得做,朋友才拿得到。

## Considered Options

- **只更新到正式版(vX.Y.Z tag + `stable` 分支,Homebrew 的做法)**:#90 做過。使用者多、需要擋住還沒弄穩的改動時才值得;要改回去時 tools/update.py 的判斷換成看 tag、install.sh 新裝時切 `stable` 即可。
- **用 GitHub Releases API 查新版**:repo 是私人的,不帶認證查不到。改用 `git ls-remote`,沿用 clone 時的認證。

## Consequences

- 合進 main 的 bug,朋友按了更新也會拿到;CI 是唯一的關卡。
- 程式資料夾切在別的分支、或有沒提交的變更時,按鈕只說原因、不動。
- 開源後(0002)一般使用者裝的是公開鏡像,「更新」跟的是公開 repo 的 main,也就是私人 main 每次合併後同步過去的那一版。要擋住還沒弄穩的改動時,再回頭考慮正式版。
