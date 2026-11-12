---
name: autopilot
description: 用 TLA+ 模型檢查加真程式重演,驗自動流程(tools/autopilot.py)的規則:同一張卡的同一段只替他按一次、失敗了就停;填表記號只用進度檔或 apply.at 變化判定派出,否則留在 pending 等下一輪;推進可以投了要等這張進待你決定之後的那一輪驗收跑完,不拿舊結果放行。改到 autopilot 的 plan / Pilot.step、next_step 的 _move / _fill / of、board_server 的 trigger_build / build_state / start_run、form_record.ship_blocked,或 delivery_state.json 裡 todo 到 nopage 那幾列時用。
---

# 自動流程的兩條鐵律

**承諾一**:同一張卡的同一段(一個「試過了」記號:`adv:<id>`、`fill:<id>:<apply.at>`),自動流程只替他按一次;填表記號要嘛有啟動證據、要嘛還在 `__auto__.pending` 等下一輪查證;自動填的那一輪失敗了(卡住、沒填成)就停,等他決定。
**壞了的害處**:同一張卡被派好幾次 agent,佔住 agent 的瀏覽器(ego)和額度;卡住的原因被蓋掉,他不知道發生過什麼。

**承諾二**:推進「可以投了」用的投遞前驗收,是這張進待你決定之後才開始的那一輪、真的驗到這張;推進那一刻看板上最新的驗收也沒擋它。
**壞了的害處**:驗收說不過(例如職缺已下架)的卡被推進可以投了,接著被自動填表。送出前的核准檢查讀當下的驗收,不會真的送出,但 agent 白填一次、卡上說的跟驗收對不上。

範圍到「填好、停在送出前」為止;確認送出之後歸 `confirm-send-window`。

| 規則(AutoPilot.tla) | 承諾 | 重演(replay.py) |
|---|---|---|
| `OncePerMark` | 一 | R3,守門 G2、G3 |
| `NoOrphanMark` | 一:tried 的填表記號有派工證據或仍在 pending | R4、R5 |
| `StopsAfterFailure` | 一的後半 | R2 |
| `FreshVerdict` | 二 | R1,守門 G1 |

## 1. 這次改動要不要跑

```bash
git diff b9248438 -- tools/autopilot.py tools/next_step.py tools/board_server.py tools/form_record.py tools/delivery_state.json tools/apply_run.py tools/chrome_door.py
```

diff 碰到第 4 節表裡任一處就跑。`b9248438` 是這次確認模型和重演描述的是那時的程式的 commit(同步點)。

## 2. 跑

```bash
python3 .claude/skills/autopilot/check.py --out <暫存資料夾>
```

約 15 秒。第一次會用 mise 裝 TLC 1.7.4 和 Java(要網路)。它做兩件事:從 `tools/delivery_state.json` 重產 `Table.tla`(自動會填的狀態、模型用到的事件)後每條規則各跑一次 TLC;再跑 `replay.py`,照模型的路徑呼叫真的 `Pilot`。

**exit 0 = 跟同步點一樣**。同步點上該看到的結果只寫在 `check.py` 和 `replay.py` 的 `EXPECTED`(註解寫著對應的票)。跟同步點不一樣的那一行後面會標「← 同步點上是 …」,exit 1。

## 3. 讀結果

- **TLC 的結果**用 `/statechart` 讀:把 `--out` 資料夾裡的 `AutoPilot.tla`、`Table.tla`、該規則的 `.cfg` 和這裡的 `view.mjs` 給它,它把每條違規的最短路徑講成看板上的話,通過時畫出整張狀態圖。
- **新的違規**先當成模型錯,直到用真程式重演出來:在 `replay.py` 加一條 R,照路徑一步一步呼叫真的函式(外面只准換 `Pilot` 本來就收的 `start_run` / `run_status` / `build_state`、用 `bd.set_data` 代替 reconcile 寫驗收、`ap.flow`)。再寫一條對照(同樣的步驟少掉關鍵那一步),確認對照不會出事。
  - 重演得出來 → 真的 bug。照 docs/agents/issue-tracker.md 開票,寫路徑和重演結果;把新規則和 R 的預期加進 `check.py` 和 `replay.py` 的 `EXPECTED`。不要為了讓它通過去改規則或模型。
  - 重演不出來 → 模型錯。找出哪一個動作比程式多做或少做(常見:程式裡某兩步其實在同一把鎖裡、某個檢查程式其實有做),照第 4 節改模型,再跑。
- **G 變 BROKEN** → 這次改動把鐵律拿掉了;改程式,不是改守門。
- **R 變 NOT REPRODUCED** → 那張票修好了:對應的規則應該也轉成通過。兩邊都對上,才把 `EXPECTED` 改成新的結果、在票上留言。

## 4. 改到模型涵蓋的程式

| 程式 | 模型的動作 | 怎麼跟著改 |
|---|---|---|
| `delivery_state.json` 的 todo / running / parked / stuck / gone / stale / nopage 列,`AUTO_FILL` | `Go(...)`、`CardStales`、`PilotPlans` 的 `AutoFill` | 自動(check.py 重產 `Table.tla`)。自動會填的狀態多了模型外的,check.py 會停下來叫你加 |
| `Pilot._step` 開頭讀看板檔(`open` + `bd.parse`),plan 前處理 pending;只以 `run_status('apply')` 的該卡 fill 進度和 `apply.at` 變化認定派出,不看投遞狀態 | `PilotReads`、`PilotResolves` | 手改 |
| `build_state()`、`run_status()`、`autopilot.plan`、`next_step._move` / `_fill` | `PilotPlans` | 手改 |
| 填表前一次 `bd.rewrite` 把 `fill:<id>:<apply.at>` 加進 tried 並記 `pending[mark] = 時間`;`start_run` 在 `_RUN_LOCK` 裡 `Popen(start_new_session=True)` 後立刻寫 `phase/start + url + t0`;沒有可填卡時 `phase=nothing` 也帶指定的 `url`;`plan` 要補建時 `self.build()` | `PilotReserves`、`PilotLaunches` | 手改 |
| `Pilot._step` 結尾 `bd.rewrite(mut)`:在 `live_lock` 內重讀最新 data + fb;記 `seen` 和 `adv:`(真的推進才記),推進要最新 `app == 'ready'` 且 `ship_blocked` 不擋;填表 200 清 pending、明確非 200 撤 tried/pending、例外保留 pending | `PilotWrites` | 手改 |
| `board_server.trigger_build`(單飛、`again`、`tried`/`built`) | `YouSave`、`BuildDone`、`BuildFails` | 手改 |
| `reconcile` → `board_status --write`(只驗 ready / ship 的卡,`bd.set_data` 一次寫) | `ReconcileReads`、`ReconcileWrites` | 手改 |
| `form_record.ship_blocked`(沒驗收擋;驗收沒驗到這張不擋) | `Blocks` | 手改 |
| `apply_run._run_one` 的 `fill_start`、`run_done`(進度檔保留 stage、url、t0 和 results);`apply_run.settle` / `run_failed` | `JobFillStart`、`JobEnds`、`Settle` | 事件自動;何時發生手改 |
| `chrome_door.sweep_gone`(`page_up`:`HELD_STATES` 且有 `tab_id`) | `PageLost` | 手改 |
| 伺服器重開:`_build_state` 歸零、填表子行程照跑 | `ServerRestarts` | 手改 |

模型裡一個動作 = 程式裡一段不會被別人插進來的事:一次 `set_fb` / `set_data` / `rewrite`(拿 `live_lock`)、一次不拿鎖的讀、`start_run` 在 `_RUN_LOCK` 裡那一段。`PilotResolves` 每輪在 plan 前處理全部 pending:進度檔 `stage == fill`、`t0 >= pending` 時間且 `url` 或 `results` 指到該卡,或 `apply.at` 已改變,才清 pending 並保留 tried;投遞狀態變化不算證據。沒有證據就一起撤 tried 和 pending,這輪可以再派。結尾 mut 的明確非 200 也撤 tried 和 pending;丟例外不動,留給下一輪。推進的最後檢查和寫入同在 `rewrite` 的鎖內,重演也照這個切法改。

改完模型,用一個故意弄壞的版本確認規則會違規(例:`MaxRestarts = 0` 時 `OncePerMark` 通過;在 `PilotReserves` 留 tried 卻拿掉 pending,`NoOrphanMark` 要違規),再改回來。改到程式的守門,用一個故意弄壞的程式確認 G 會 BROKEN(例:拿掉 `next_step._move` 的 `build['built'] <= float(seen[i])`,G1 要 BROKEN),再改回來。

確認模型和重演描述的是新程式之後,把第 1 節的同步點換成該程式的 commit。

## 範圍

- **檢查到的**:一張卡;最多 7 個記時間的事件(`MaxTime`)、`apply.at` 換 4 次、伺服器重開 1 次;建置隨時可能被叫、驗收對這張過或不過都可能;填表那一輪可能還沒動卡就結束、填完、被停;頁面可能隨時不見;pending 寫好後重開、期間 page_lost 把 stale 改成 gone,仍會再派一次(R5)。投遞狀態改變本身不會被當成派工證據。
- **沒檢查的**:多張卡互相影響(只用「填表子行程在不在跑」代表 agent 的瀏覽器被佔);準備區(`prep:`)、照職缺要求客製(`cust:`)、送出結果不明(`unsure:`)、重打(`rf`)、查應徵進度;他在回報按「處理好了」(handed)、手動推進或退回(G2 用真程式補了退回的那一條);`__auto__.tried` 只留最後 500 個(讀程式看到:超過就丟最舊的,推過退回的卡可能被再推一次,沒重演);重開時正在跑的 reconcile 跟新開的那一輪同時寫;Popen 之後、start_run 寫進度檔之前重開時沒有進度證據,會照沒派出處理。
- **比程式寬的地方**:建置在模型裡任何時候都可能開始(程式裡要有人存檔、打開看板或 Pilot 補建);`Settle` 任何時候都可能做(程式裡在 Pilot 每一步開頭)。只會多找到路徑,不會漏。
