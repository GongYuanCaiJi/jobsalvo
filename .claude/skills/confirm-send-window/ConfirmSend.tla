---------------------------- MODULE ConfirmSend ----------------------------
(* 一張卡從「你已確認」到 agent 真的按下送出(docs/adr/0008:確認送出後 8 秒才送)。
   每個動作對應一段程式,對照表在 SKILL.md。狀態轉換照 tools/delivery_state.json:
   check.py 每次跑都從那個檔重新產生 Table.tla,這裡不抄。
   時間:一格 = 一秒。只記「過了幾秒」(到 SendAfter 就不再數)和「是不是同一秒」,不記絕對時間:
   approve.at 是 isoformat(timespec='seconds'),兩次確認的 at 相等就是同一秒按的。 *)
EXTENDS Naturals, FiniteSets, Table

CONSTANTS
  SendAfter,     \* board_server.SEND_AFTER
  SendRetry,     \* board_server.SEND_RETRY
  MaxRounds,     \* 最多按幾次確認(模型的界)
  SettleEvent,   \* apply_run.settle 把「正在送出、那一輪卻不在跑」的卡送哪個事件
  ApprovalOK     \* form_record.approval_problem 放行的狀態(它第一段只看狀態)

Rounds == 1..MaxRounds
None == [at |-> 0, r |-> 0]
NoSend == [r |-> 0, kind |-> "none", authR |-> 0, age |-> 0, cancelled |-> FALSE, again |-> FALSE]
Idle == [phase |-> "idle", kind |-> "none", r |-> 0]
Min(a, b) == IF a < b THEN a ELSE b

VARIABLES
  ds,         \* 卡的投遞狀態(模型的 6 種,見 Table.tla)
  approve,    \* 卡上的 approve:at(第幾秒)與 r(模型給每一次確認的編號,程式裡沒有,只拿來判規則)
  pageR,      \* 頁上最近一次確認的編號(取消後還留著:頁面還是那一頁)
  slips,      \* 取消確認後留在看板的復原單:每一張放回一份 approve
  timers,     \* board_server.QUEUED 的計時器:{[at, left(還差幾秒), r]}
  run,        \* 這張卡「幫你填表・送出」那一輪(apply_run 子行程)
  other,      \* 別的工作正在用 agent 的瀏覽器(另一張卡的填表、查應徵進度、全部同步)
  rounds,     \* 按過幾次確認
  sec,        \* 第 r 次確認是哪一秒按的(只比相等)
  ticked,     \* 上一次確認之後時間走過沒
  age,        \* 第 r 次確認按下去過了幾秒(到 SendAfter 為止)
  cancelled,  \* 取消或作廢、而且沒被復原的確認編號
  clicked,    \* 之前有一隻 agent 可能已經按了送出,還沒有人(程式或他)說沒送成
  lastSend    \* 最近一次「開始送出」那一刻的紀錄(規則看它)

vars == <<ds, approve, pageR, slips, timers, run, other, rounds, sec, ticked, age, cancelled, clicked, lastSend>>
clock == <<rounds, sec, ticked, age>>

Init ==
  /\ ds = "parked" /\ approve = None /\ pageR = 0 /\ slips = {}
  /\ timers = {} /\ run = Idle /\ other = FALSE /\ rounds = 0
  /\ sec = [r \in Rounds |-> 0] /\ ticked = TRUE /\ age = [r \in Rounds |-> 0]
  /\ cancelled = {} /\ clicked = FALSE /\ lastSend = NoSend

Holds(s) == s \in {"confirmed", "sending", "unsure"}   \* delivery_state.problems:帶著確認只能在這三種

\* 照表送一個事件(delivery_state.fire):表上沒有那一格就不准。approve 照 problems() 的規矩跟著狀態走。
Go(ev) == <<ds, ev>> \in DOMAIN Cell /\ ds' \in Cell[<<ds, ev>>]
Fire(ev) ==
  /\ Go(ev)
  /\ approve' = IF Holds(ds') THEN approve ELSE None
  /\ cancelled' = IF Holds(ds) /\ ~Holds(ds') /\ approve.r > 0 THEN cancelled \cup {approve.r} ELSE cancelled

-----------------------------------------------------------------------------
(* 過了一秒。 *)
Tick ==
  /\ ticked' = TRUE
  /\ age' = [r \in Rounds |-> IF r <= rounds THEN Min(age[r] + 1, SendAfter) ELSE 0]
  /\ timers' = {[t EXCEPT !.left = IF t.left > 0 THEN t.left - 1 ELSE 0] : t \in timers}
  /\ UNCHANGED <<ds, approve, pageR, slips, run, other, rounds, sec, cancelled, clicked, lastSend>>

(* 他在看板按「✅ 確認送出」:write_fb 一包指令在鎖內套用(_event 用後台時間記 approve.at),
   放開鎖後 queue_send 排計時器。queue_send 讀的是剛存好的 approve.at,所以合成一步不改變結果。 *)
YouConfirm ==
  /\ rounds < MaxRounds
  /\ Go("confirm")
  /\ LET r == rounds + 1
         s == IF rounds = 0 THEN 1 ELSE IF ticked THEN sec[rounds] + 1 ELSE sec[rounds]
     IN /\ rounds' = r
        /\ sec' = [sec EXCEPT ![r] = s]
        /\ age' = [age EXCEPT ![r] = 0]
        /\ approve' = [at |-> s, r |-> r]
        /\ pageR' = r
        /\ timers' = timers \cup {[at |-> s, left |-> SendAfter, r |-> r]}
  /\ ticked' = FALSE
  /\ UNCHANGED <<slips, run, other, cancelled, clicked, lastSend>>

(* 他取消:按「取消確認、復原」、把卡移走(leave)、改了答案(answers_changed)。都留一張能放回確認的復原單。 *)
YouCancel ==
  /\ \E ev \in {"unconfirm", "leave", "answers_changed"} :
       /\ Fire(ev)
       /\ slips' = IF ds = "confirmed" /\ ds' # "confirmed" THEN slips \cup {approve} ELSE slips
  /\ UNCHANGED <<pageR, timers, run, other, clock, clicked, lastSend>>

(* 確認被作廢:換了要寄的檔、頁不見了、讀頁發現變了。卡離開這段流程,要重填才回得來。 *)
ConfirmVoided ==
  /\ \E ev \in {"files_changed", "page_lost", "check_failed"} : Fire(ev)
  /\ UNCHANGED <<pageR, slips, timers, run, other, clock, clicked, lastSend>>

(* 他按復原單「復原」(delivery_state.restore):卡還是取消後的樣子才放回,狀態那幾欄一起放回。不經狀態表。 *)
YouRestore ==
  /\ ds = "parked" /\ approve = None
  /\ \E s \in slips :
       /\ ds' = "confirmed" /\ approve' = s /\ pageR' = s.r
       /\ cancelled' = cancelled \ {s.r}
  /\ UNCHANGED <<slips, timers, run, other, clock, clicked, lastSend>>

(* 重填回到停著等你(填表、修改那一整輪;這裡只當一步)。新的一輪填表,頁上沒有確認過。 *)
Refilled ==
  /\ ds = "left" /\ run.phase = "idle"
  /\ ds' = "parked" /\ pageR' = 0
  /\ UNCHANGED <<approve, slips, timers, run, other, clock, cancelled, clicked, lastSend>>

(* 他標了已在外部送出、平台對帳找到了。 *)
SentElsewhere ==
  /\ \E ev \in {"sent_manual", "platform_found"} : Fire(ev)
  /\ UNCHANGED <<pageR, slips, timers, run, other, clock, clicked, lastSend>>

(* 他在「送出結果不明」查過:沒送成、其實送出了。 *)
YouSayNotSent ==
  /\ Fire("not_sent")
  /\ clicked' = FALSE
  /\ UNCHANGED <<pageR, slips, timers, run, other, clock, lastSend>>
YouSayActuallySent ==
  /\ Fire("actually_sent")
  /\ UNCHANGED <<pageR, slips, timers, run, other, clock, clicked, lastSend>>

(* 已送出的卡他按「沒送成」「退回」「重試」:回到還沒填,之後重新填、重新確認。 *)
YouReopenSent ==
  /\ \E ev \in {"undo_sent", "back", "retry"} : Fire(ev)
  /\ clicked' = FALSE /\ pageR' = 0
  /\ UNCHANGED <<slips, timers, run, other, clock, lastSend>>

-----------------------------------------------------------------------------
(* start_run 的單飛:同一種在跑、或別的工作在用 agent 的瀏覽器,回 409。 *)
Busy == run.phase # "idle" \/ other

(* 計時器到點(_send_confirmed):不拿鎖讀一次看板,還是「你已確認」而且 approve.at 對得上才 start_run。
   409 就用同一個 at 再排一次(SEND_RETRY);其他原因開不起來就寫回報,卡留在你已確認。 *)
TimerFires ==
  /\ \E t \in timers :
       /\ t.left = 0
       /\ IF ds = "confirmed" /\ approve.at = t.at
          THEN \/ /\ Busy
                  /\ timers' = (timers \ {t}) \cup {[t EXCEPT !.left = SendRetry]}
                  /\ UNCHANGED run
               \/ /\ ~Busy
                  /\ timers' = timers \ {t}
                  /\ run' = [phase |-> "launched", kind |-> "timer", r |-> t.r]
               \/ /\ timers' = timers \ {t}      \* start_run 回 409 以外的錯(沒有 ego、來源檢查不過)
                  /\ UNCHANGED run
          ELSE /\ timers' = timers \ {t} /\ UNCHANGED run
  /\ UNCHANGED <<ds, approve, pageR, slips, other, clock, cancelled, clicked, lastSend>>

(* 他按「▶ 送出」(/api/run apply submit):後台照 approval_problem 放行,start_run 單飛。
   看板在 8 秒內不顯示這顆按鈕(view.due),但後台不擋;這裡照後台。 *)
YouPressSend ==
  /\ ds \in ApprovalOK /\ ~Busy
  /\ run' = [phase |-> "launched", kind |-> "manual", r |-> approve.r]
  /\ UNCHANGED <<ds, approve, pageR, slips, timers, other, clock, cancelled, clicked, lastSend>>

(* apply_run._run_one 送出那一輪:讀頁比對(慢,不拿鎖)之後,bd.set_fb 裡 form_record.start_submit
   在同一把鎖內重跑 approval_problem 並送 submit_start。它不看 approve.at。 *)
StartSubmit ==
  /\ run.phase = "launched"
  /\ IF ds \in ApprovalOK /\ <<ds, "submit_start">> \in DOMAIN Cell
     THEN /\ Fire("submit_start")
          /\ run' = [run EXCEPT !.phase = "agent"]
          /\ clicked' = TRUE
          /\ lastSend' = [r |-> pageR, kind |-> run.kind, authR |-> run.r,
                          age |-> IF pageR = 0 THEN 0 ELSE age[pageR],
                          cancelled |-> pageR \in cancelled, again |-> clicked]
     ELSE /\ run' = Idle                         \* start_submit 回原因,卡不動
          /\ UNCHANGED <<ds, approve, cancelled, clicked, lastSend>>
  /\ UNCHANGED <<pageR, slips, timers, other, clock>>

(* 送出前讀頁發現頁面變了(_page_changed → check_failed),或子行程在送 submit_start 之前就死了。 *)
LaunchFails ==
  /\ run.phase = "launched"
  /\ run' = Idle
  /\ \/ /\ ds = "confirmed" /\ Fire("check_failed")
     \/ UNCHANGED <<ds, approve, cancelled>>
  /\ UNCHANGED <<pageR, slips, timers, other, clock, clicked, lastSend>>

(* agent 送完回來(都是 try_fire:卡已經不是正在送出就只記進歷史)。 *)
AgentReturns ==
  /\ run.phase = "agent"
  /\ run' = Idle
  /\ \E ev \in {"submit_ok", "submit_unsure", "submit_not_started", "submit_not_sent"} :
       IF <<ds, ev>> \in DOMAIN Cell
       THEN /\ Fire(ev)
            /\ clicked' = IF ev \in {"submit_not_started", "submit_not_sent"} THEN FALSE ELSE clicked
       ELSE UNCHANGED <<ds, approve, cancelled, clicked>>
  /\ UNCHANGED <<pageR, slips, timers, other, clock, lastSend>>

(* 按 ⏹ 停止、子行程當掉:來不及寫任何事件。 *)
AgentKilled ==
  /\ run.phase = "agent"
  /\ run' = Idle
  /\ UNCHANGED <<ds, approve, pageR, slips, timers, other, clock, cancelled, clicked, lastSend>>

(* apply_run.settle(伺服器啟動、自動流程每一輪):正在送出、那一輪卻不在跑。
   它不知道 agent 停下來之前按了送出沒,所以不管送哪個事件,「可能按過」都還在。 *)
Settle ==
  /\ ds = "sending" /\ run.phase = "idle"
  /\ Fire(SettleEvent)
  /\ UNCHANGED <<pageR, slips, timers, run, other, clock, clicked, lastSend>>

(* 伺服器重開:QUEUED 和計時器都沒了(子行程 start_new_session,照樣跑)。 *)
ServerRestarts ==
  /\ timers # {} /\ timers' = {}
  /\ UNCHANGED <<ds, approve, pageR, slips, run, other, clock, cancelled, clicked, lastSend>>

OtherJobToggles ==
  /\ other' = ~other
  /\ UNCHANGED <<ds, approve, pageR, slips, timers, run, clock, cancelled, clicked, lastSend>>

Next ==
  \/ Tick \/ YouConfirm \/ YouCancel \/ ConfirmVoided \/ YouRestore \/ Refilled \/ SentElsewhere
  \/ YouSayNotSent \/ YouSayActuallySent \/ YouReopenSent
  \/ TimerFires \/ YouPressSend \/ StartSubmit \/ LaunchFails \/ AgentReturns \/ AgentKilled
  \/ Settle \/ ServerRestarts \/ OtherJobToggles

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------
(* 規則一:取消(或作廢)了的那一次確認,不會被送出去。 *)
CancelledNeverSent == ~lastSend.cancelled

(* 規則二:agent 可能已經按過送出、還沒有人說沒送成之前,不會再開始送一次(不會投兩次)。 *)
NoDoubleSend == ~lastSend.again

(* 規則三:每一次開始送出,送的那一次確認都已經過了 8 秒反悔期;
   例外是他在那一次確認有效時自己按了「▶ 送出」。 *)
WindowKept ==
  \/ lastSend.kind = "none"
  \/ lastSend.age >= SendAfter
  \/ lastSend.kind = "manual" /\ lastSend.authR = lastSend.r

TypeOK ==
  /\ ds \in ModelStates
  /\ run.phase \in {"idle", "launched", "agent"}
=============================================================================
