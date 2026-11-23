# 看板遮罩截圖

看板上私密文字和公司名、職稱常排在同一列，裁切分不開。截圖改用 [`mask-shot.mjs`](../mask-shot.mjs)：先把私密元素在 DOM 上塗成灰底、文字隱藏，截 viewport，再還原畫面。先 `mkdir -p "$RUN/home/raw"`，再在 `ego-browser nodejs` 內用絕對路徑載入，路徑加 `?v=<數字>` 避免載到舊版：

~~~js
const { shot } = await import("file:///<repo 絕對路徑>/.claude/skills/verify/mask-shot.mjs?v=1");
const fs = await import("node:fs/promises");
const words = (await fs.readFile("<repo 的 git common dir>/info/private-words", "utf8"))
  .split("\n").map(s => s.trim()).filter(s => s && !s.startsWith("#"));
const TEXT = words.map(w => w.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")).join("|");
await shot(page, "<RUN>/home/raw/<name>.png", { text: TEXT });
await shot(page, "<RUN>/home/raw/<name>.png", { text: TEXT, extra: ["<額外 selector>"] });
~~~

`MASK` 列出已知的私密區塊：agent 的判讀與挑選理由（`.srcwhy`、`.vdmini`、`.vdmini-why`、`.vdsum`、`.vd-agent`、`.vd-ship`、`.sr.fitrow`）、履歷內文與答案（`.iv-*` 稿件、`textarea`、`.ansq-why`、`.ansq-en`）、面試對方窗口（`.iv-round-w`）、來信引文（`.fm-line.rp-auto .q`）、回報內文（`.ib-msg`、`.ib-do`、`.ib-full`、`.ib-said`，常含 Email 和電話；首頁「要你處理」格的說明 `.wt-l` 也引回報內文）、設定頁檔案路徑（`.cfg-file`，保留 `#cfg-st`）。`text` 每次都要傳：它塗掉自己的文字（不論有沒有子元素）或整段文字（沒有子元素時）符合 regex 的元素和 `input`，擋住履歷版本名這類散在各處的字（例如設定頁的履歷列和已投出漏斗的「履歷」列）。私密詞清單在 repo 的 git common dir 下（`git rev-parse --git-common-dir` 加 `/info/private-words`，和 `privacy-scan.sh` 用同一份），一行一個詞，`#` 開頭是註解。

截完逐張打開檢查。還看得到私密字就刪掉原圖，把那個 selector 加進 `extra` 或 `MASK` 後重截；整塊灰掉看不出操作結果時，改遮更小的子元素。看過的圖複製到證據目錄：

~~~sh
mkdir -p "$EVIDENCE/<feature-name>"
cp "$RUN/home/raw/<name>.png" "$EVIDENCE/<feature-name>/<name>.png"
~~~

遮不掉又必要的畫面不保存，該入口記為 `NOT VERIFIED`（證據保存不了），在該功能頁證據資料夾的 `verification-notes.txt` 用文字記下畫面上的狀態。
