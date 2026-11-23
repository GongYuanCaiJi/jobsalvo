# 看板外框

使用者在每一頁都看得到的外框：首頁的「要你處理」、回報、我的想法、外觀和收起側欄。

## Sub-features

- `H1` 首頁「新職缺」上方的「要你處理」`.waitrow`：格子 `button.wt-b[data-gotab="<ship|ready|prep>"]` 跳到對應分頁，`[data-ansq="1"]` 打開逐條確認答案，`[data-openinbox="<id>"]` 跳到回報那一則。
- `H2` 回報 `#inboxbar details.inbox-d`：各則 `[data-ib="<id>"]`；設定頁的「看回報」也是 `[data-openinbox]`。
- `H3` 頂列「我的想法」`#ntog`：新增 `#note-add`、存 `.note-save`、刪 `.note-del`（提示附復原），存在 `/api/state` 的 `__notes__`。
- `H4` 外觀 `[data-themeopen]` 跳出 `[data-themeset="light|dark|system"]`（淺色／深色／跟系統），存在瀏覽器 `localStorage` 的 `jobsalvo.theme`。
- `H5` 收起／展開側欄 `[data-sidetog]`，`body.side-off` 表示收起，存在 `localStorage` 的 `jobsalvo.side`，重新整理後保留。

## How to get to it (user POV)

打開看板就在首頁「新職缺」；「要你處理」在清單上方，只在有事要他做時出現。頂列有「我的想法」和外觀按鈕，左上角品牌旁是收起側欄。回報在清單上方的收合區。

## Driving it with ego lite

Preconditions: Launch 和 Doctor 通過。`H1`、`H2` 需要副本有待處理項目或回報；沒有時記為 `NOT VERIFIED`。

- `H1`: 在 `[data-tab="none"]` 記下 `.waitrow .wt-b` 各格的數字和文字。按一格 `[data-gotab]`，分頁切到它指的那一頁；回首頁。有 `[data-ansq="1"]` 時按它，`#rzmodal .ansq-pos` 顯示「第 1 / N 條」，按 `Esc` 關閉（逐條確認的細節在[幫你填表](application-flow.md) A4）。有 `[data-openinbox]` 格時按它，接 `H2`。
- `H2`: 按 `[data-openinbox="<id>"]` 後，`#inboxbar details.inbox-d` 變成展開，`[data-ib="<id>"]` 那一則在畫面內（是 `details` 時也展開）。回報內文含聯絡資料，截圖用主 skill 的遮罩。
- `H3`: 按 `#ntog`，`aria-expanded="true"`；按 `#note-add` 多一張 `.note-card`，在 `.note-t` 是空的那一張打一行測試字（畫面重畫後自己加的標記會掉，用空白認新卡，免得蓋掉既有筆記），按 `.note-save`，等 `#savebar.clean` 後 `/api/state` 的 `__notes__` 有這一筆。按它的 `.note-del`，該筆從畫面消失，等 `#savebar.clean` 後 `__notes__` 也少了它；在提示消失前按 `.snack-undo`，筆記回來；再刪一次並等提示消失，`__notes__` 回到操作前。
- `H4`: 記下 `localStorage` 的 `jobsalvo.theme`（沒有就是跟系統）。按 `[data-themeopen]`，按 `[data-themeset="light"]`，`html[data-theme="light"]` 且該鈕 `aria-pressed="true"`；重新整理後仍是淺色。最後按回原本那一個（通常是 `system`；原本沒有值時，按回後 `localStorage` 記成 `system`，畫面一樣）。
- `H5`: 側欄展開時只有 `#tabs [data-sidetog]` 看得到（`#hdr` 那顆大小是 0），按它；`body` 有 `side-off`、`localStorage` 的 `jobsalvo.side` 是 `off`。重新整理後仍收起，這時只有 `#hdr [data-sidetog]` 看得到、`aria-label` 是「展開側欄」；按它展開，`side-off` 和 `jobsalvo.side` 都清掉。

Evidence: 每項操作前後畫面和 API／`localStorage` 摘要；回報與筆記內容依主 skill Evidence 的規則遮罩。

## Gotchas

手機版面（底部導覽、搜尋層）要模擬窄螢幕，ego lite 沒有設定視窗大小的入口，這部分記為 `NOT VERIFIED`。外觀和側欄存在瀏覽器裡，換 TaskSpace 就會回到預設值。筆記有三條存檔路徑互相備援：儲存鈕、離開輸入框即存（`focusout`）、2 秒自動存檔；只弄壞其中一條時使用者看不出差別，H3 也不分辨是哪一條存的。

## Coverage

- Files: `board/board.js`、`board/board.css`、`tools/agent_report.py`、`tools/board_server.py`。
- Sync commit: `90a486a`。
- Break: 筆記輸入時不把文字寫進資料（`board.js` 筆記 `input` 處理拿掉 `n.t=ta.value`）。Fails at **H3**。
- Break: 筆記改動不標成要存（`markNotesDirty` 拿掉 `dirty['__notes__']=true`）。Fails at **H3**：`__notes__` 沒有這一筆。
- Break: 收起側欄時不寫 `localStorage` 的 `jobsalvo.side`。Fails at **H5**：重新整理後側欄又展開。
- Break: 換主題時不寫 `localStorage` 的 `jobsalvo.theme`。Fails at **H4**：重新整理後回到原本的主題。
