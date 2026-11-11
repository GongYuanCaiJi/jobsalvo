---
name: confirm-send-window
description: 用 TLA+ 模型檢查「確認送出 → agent 真的按送出」這一段:取消的確認不送、不投兩次、8 秒反悔期。改到 board_server 的確認與排送出、apply_run 的送出那一輪或 settle、form_record 的 approval_problem / start_submit、delivery_state.json 裡你已確認・正在送出・送出結果不明那幾列,或 restore / 復原單時用。
---

# 確認送出的 8 秒反悔期

**承諾**:按「✅ 確認送出」之後 8 秒內取消就不送;取消了的確認不會送出去;同一張卡不會被 agent 送兩次。
**壞了的害處**:他已經反悔的應徵被送出去,或同一個職缺投兩次,收不回來。

模型是 `ConfirmSend.tla`,一張卡、一隻送出的 apply_run、board_server 的計時器、他在看板按的每一顆相關按鈕。三條規則:

| 規則 | 一句話 |
|---|---|
| `CancelledNeverSent` | 取消(或作廢)了的那一次確認,不會被送出去 |
| `NoDoubleSend` | agent 可能按過送出、還沒有人說沒送成之前,不會再開始送一次 |
| `WindowKept` | 每一次開始送出,送的那一次確認都過了 8 秒;例外是他在那次確認有效時自己按了「▶ 送出」 |

## 1. 這次改動要不要跑

```bash
git diff 7d3bd032 -- tools/board_server.py tools/apply_run.py tools/form_record.py tools/delivery_state.py tools/delivery_state.json
```

diff 碰到下面「涵蓋的程式」任一處就跑。`7d3bd032` 是上次確認模型描述的是那時的程式的 commit(同步點)。

## 2. 跑

```bash
python3 .claude/skills/confirm-send-window/check.py --out <暫存資料夾>
```

約 30 秒。第一次會用 mise 裝 TLC 1.7.4 和 Java(要網路)。check.py 每次都從程式重讀這幾樣,不用手抄:`tools/delivery_state.json` 的狀態表(產生 `Table.tla`)、`SEND_AFTER`、`SEND_RETRY`、`apply_run.settle` 對正在送出的卡送的事件。讀不到就停下來叫你更新模型。

**現在該看到的結果**:三條都通過。

## 3. 讀結果

每一個結果都用 `/statechart` 讀:把 `--out` 資料夾裡的 `ConfirmSend.tla`、`Table.tla`、`ConfirmSend.cfg` 和這裡的 `view.mjs` 給它,它把每條違規的最短路徑講成看板上的話,通過時畫出整張狀態圖。

新的違規先當成模型錯,直到用真程式重演出來:照路徑一步一步呼叫真的函式(`delivery_state.fire`、`form_record.start_submit`…),寫法照 `replay_window.py`(#739 的重演:派出這一輪之後他取消又重新確認。`uv run python .claude/skills/confirm-send-window/replay_window.py` 現在印 `NOT REPRODUCED`,印 `REPRODUCED` 就是 #739 又壞了)。

- 重演得出來 → 真的 bug。照 docs/agents/issue-tracker.md 開票,寫路徑和重演結果。不要為了讓它通過去改規則或模型。
- 重演不出來 → 模型錯。找出哪一個動作比程式多做或少做(常見:某一步在程式裡不是在同一把鎖裡做完、某個檢查程式其實有做),照下表改模型,再跑。

## 4. 改到模型涵蓋的程式

| 程式 | 模型的動作 | 怎麼跟著改 |
|---|---|---|
| `delivery_state.json` 的 parked / confirmed / sending / sent / unsure 列 | 所有 `Fire(...)` | 自動(check.py 重產 `Table.tla`)。新事件要模型用到才加進對應動作 |
| `board_server._event` 的 confirm(`form_record.approval` 給每次確認一個 `id`)、`write_fb` 結尾的 `queue_send` | `YouConfirm`(`approve.r` 就是 `id`) | 手改 |
| `board_server.queue_send` / `_send_confirmed`、`SEND_AFTER`、`SEND_RETRY` | `TimerFires`、`Tick` | 秒數自動;檢查條件、409 重排手改 |
| `board_server.start_run` 的單飛(`_RUN_LOCK`、`BROWSER_JOBS`) | `Busy`、`OtherJobToggles` | 手改 |
| `/api/run` apply submit 那段(`approval_problem` 放行,記下當下的 `approval_ids`) | `YouPressSend` | 手改 |
| `apply_run._run_one` submit:`recheck_page`、`bd.set_fb(start)`、`_page_changed` | `StartSubmit`、`LaunchFails` | 手改 |
| `form_record.approval_problem` / `start_submit`,含比對派出時的 `approval_id` | `StartSubmit` 的 `ApprovalOK`(cfg)、`approve.r = run.r` | 手改 cfg 或動作 |
| apply_run 送完的 `try_fire`(submit_ok / unsure / not_started / not_sent) | `AgentReturns` | 手改 |
| `apply_run.settle` | `Settle` | 送哪個事件自動;條件手改 |
| `delivery_state.restore`、`slips`(復原單) | `YouRestore`、`YouCancel` 的 slips | 手改 |

模型裡一個動作 = 程式裡一段不會被別人插進來的事:一次 `bd.rewrite` / `set_fb`(拿 `live_lock`)、一次不拿鎖的讀。改動把一段拆開或合併時,動作也要跟著拆或合。

改完模型,用一個故意弄壞的版本確認相關規則會違規(例:把 `settle` 送的事件換成 `submit_not_started`,`NoDoubleSend` 要違規;拿掉 `StartSubmit` 的 `approve.r = run.r`,`WindowKept` 要違規),再改回來。

確認模型描述的是新程式之後,才把第 1 節和這裡的同步點 `7d3bd032` 換成新的 commit。

## 範圍

- **檢查到的**:一張卡;最多按兩次確認(`MaxRounds = 2`);時間以整秒算,只記過了幾秒(到 8 為止)和兩次確認是不是同一秒;另一個工作佔著 agent 瀏覽器、伺服器重開、送到一半被停、送出前頁面變了,都可能在任何時候發生。
- **沒檢查的**:多張卡互相影響(只用 `other` 代表「別的工作在用瀏覽器」);一秒以內的先後(同一秒的兩次確認算同時);重填那一整輪(`Refilled` 一步帶過);agent 實際在網頁上做了什麼;`approval_problem` 除了狀態以外的檢查(答案、驗收、那一輪、那一頁)當成一律放行;看板畫面(例如 8 秒內不顯示「▶ 送出」),模型照後台實際收什麼。
- **比程式寬的地方**:`YouRestore` 在重填之後還能放回舊的確認;真的 `restore` 會拒絕(`apply` 在 `STATE_FIELDS`,重填後對不上 expect)。違規路徑走了「重填 → 復原」就是這裡,不是 bug。
