----------------------------- MODULE AutoPilot -----------------------------
(* 自動流程(tools/autopilot.py 的 Pilot)替一張卡按「推進可以投了」和「讓 agent 填表」,到填好、停在送出前為止。
   送出那一段歸 confirm-send-window,這裡不畫。每個動作對應一段程式,對照表在 SKILL.md。
   投遞狀態照 tools/delivery_state.json:check.py 每次跑都重新產生 Table.tla(AutoFill、Cell),這裡不抄。
   時間:now 是事件的先後(每個要記時間的事件拿一個新的數字),不是秒;程式裡比的也只有先後
   (seen、build_state 的 tried/built、驗收那一輪的開始時間)。 *)
EXTENDS Naturals, FiniteSets, Table

CONSTANTS
  MaxTime,       \* 最多幾個記時間的事件(模型的界)
  MaxAt,         \* apply.at 最多換幾次(填表那一輪開始、結束、收尾都會換)
  MaxRestarts    \* 伺服器最多重開幾次

None == 0       \* 時間從 1 開始,0 = 沒有
NoStatus == [start |-> None, cardIn |-> FALSE, bad |-> FALSE]   \* 看板上還沒有投遞前驗收(schema_version 不是 2)
Adv == [kind |-> "adv", at |-> 0]
Fill(a) == [kind |-> "fill", at |-> a]                       \* next_step._fill 的 'fill:<id>:<apply.at 或 new>'
NoMark == [kind |-> "none", at |-> 0]

\* form_record.ship_blocked:沒有驗收 → 擋(還沒跑完);驗收裡有這張、而且不過 → 擋;驗收裡沒有這張 → 不擋
Blocks(st) == st.start = None \/ (st.cardIn /\ st.bad)

VARIABLES
  now,       \* 下一個事件拿到的時間
  app,       \* 卡在哪一階:before(還沒進待你決定)/ready(待你決定)/ship(可以投了)
  entered,   \* 這張進待你決定的時間
  dsv,       \* 進可以投了之後的投遞狀態(todo、running、parked…,見 Table.tla)
  at,        \* apply.at:第幾版(0 = 還沒填過,記號是 new)
  seen,      \* __auto__.seen[這張]:自動流程看到它在待你決定的時間(存在看板檔,重開還在)
  tried,     \* __auto__.tried:試過的記號(存在看板檔)
  pending,   \* __auto__.pending:已寫 tried、尚未查證是否派出的填表記號
  pendingAt, \* pending 寫進看板的時間
  progress,  \* run_status('apply') 的進度檔(重開還在,_RUN_LOCK 內和 Popen 一起寫啟動紀錄)
  status,    \* 看板資料的 status:哪一輪驗收(start)寫的、那一輪有沒有驗到這張、這張過不過
  build,     \* board_server._build_state(記憶體裡,重開就歸零):running、again、tried、built
  rc,        \* 正在跑的那一輪 reconcile:phase none/started/read,start,讀到這張了沒,還歸不歸這個伺服器管
  job,       \* 這張的填表子行程(start_new_session,伺服器重開它照跑):none/launched(還沒動卡)/started
  pc,        \* Pilot._step 走到哪:idle/read(讀完看板檔)/planned/reserved(記填表記號)/launched(start_run 派完)
  snap,      \* _step 開頭讀的看板檔:app、dsv、at、seen、tried、status
  pl,        \* plan 的結果:advance、fill 的記號、seen 新記的時間、要不要補建一輪、讀到的 job 在不在跑
  ok,        \* start_run 回 200 沒
  restarts,
  launched,  \* (只拿來判規則)派出去過的記號
  dup,       \* (只拿來判規則)同一個記號派了第二次
  advRec,    \* (只拿來判規則)自動流程推進那一刻:用的是哪一份驗收、看板上當下是哪一份、這張幾點進待你決定
  failed,    \* (只拿來判規則)自動填的那一輪失敗過(卡住 stuck、沒填成 nopage),他還沒決定
  refillFail \* (只拿來判規則)失敗過之後自動流程又派了填表

vars == <<now, app, entered, dsv, at, seen, tried, pending, pendingAt, progress, status, build, rc, job, pc, snap, pl, ok, restarts,
          launched, dup, advRec, failed, refillFail>>
card == <<app, entered, dsv, at>>
ghost == <<launched, dup, advRec, failed, refillFail>>

BuildReset == [running |-> FALSE, again |-> FALSE, tried |-> 0, built |-> 0]
RcNone == [phase |-> "none", start |-> 0, cardIn |-> FALSE, owned |-> FALSE]
NoSnap == [app |-> "before", dsv |-> "todo", at |-> 0, seen |-> None, tried |-> {}, status |-> NoStatus]
NoPlan == [adv |-> FALSE, fill |-> NoMark, seenNew |-> None, build |-> FALSE, jobBusy |-> FALSE]
NoProgress == [stage |-> "", url |-> "", t0 |-> 0]
PendingMark == IF pending = {} THEN NoMark ELSE CHOOSE m \in pending : TRUE

Init ==
  /\ now = 1 /\ app = "before" /\ entered = 0 /\ dsv = "todo" /\ at = 0
  /\ seen = None /\ tried = {} /\ pending = {} /\ pendingAt = 0 /\ progress = NoProgress /\ status = NoStatus
  /\ build = BuildReset /\ rc = RcNone /\ job = "none"
  /\ pc = "idle" /\ snap = NoSnap /\ pl = NoPlan /\ ok = FALSE /\ restarts = 0
  /\ launched = {} /\ dup = FALSE /\ advRec = [on |-> FALSE] /\ failed = FALSE /\ refillFail = FALSE

Tick == now < MaxTime /\ now' = now + 1

-----------------------------------------------------------------------------
(* 卡進待你決定(準備區跑完 cut_tailor 寫檔,或他按的)。 *)
Enter ==
  /\ app = "before" /\ Tick
  /\ app' = "ready" /\ entered' = now
  /\ UNCHANGED <<dsv, at, seen, tried, pending, pendingAt, progress, status, build, rc, job, pc, snap, pl, ok, restarts, ghost>>

-----------------------------------------------------------------------------
(* 建置:board_server.trigger_build。單飛;跑的時候又被叫就記 again,跑完再一輪。
   誰都可能叫它(他存檔、打開看板、Pilot 補建),所以任何時候都可能開始。 *)
StartRound(b) == [b EXCEPT !.running = TRUE, !.tried = now]

TriggerBuild ==
  IF build.running
  THEN /\ build' = [build EXCEPT !.again = TRUE] /\ UNCHANGED <<now, rc>>
  ELSE /\ Tick /\ build' = StartRound(build)
       /\ rc' = [phase |-> "started", start |-> now, cardIn |-> FALSE, owned |-> TRUE]

YouSave ==
  /\ TriggerBuild
  /\ UNCHANGED <<card, seen, tried, pending, pendingAt, progress, status, job, pc, snap, pl, ok, restarts, ghost>>

(* reconcile → board_status 讀看板:只驗待你決定、可以投了的卡。 *)
ReconcileReads ==
  /\ rc.phase = "started"
  /\ rc' = [rc EXCEPT !.phase = "read", !.cardIn = app \in {"ready", "ship"}]
  /\ UNCHANGED <<now, card, seen, tried, pending, pendingAt, progress, status, build, job, pc, snap, pl, ok, restarts, ghost>>

(* board_status --write:bd.set_data 一次寫進看板(拿 live_lock)。這張過不過由驗收決定,兩種都可能。 *)
ReconcileWrites ==
  /\ rc.phase = "read"
  /\ \E bad \in BOOLEAN : status' = [start |-> rc.start, cardIn |-> rc.cardIn, bad |-> rc.cardIn /\ bad]
  /\ rc' = [rc EXCEPT !.phase = "written"]
  /\ UNCHANGED <<now, card, seen, tried, pending, pendingAt, progress, build, job, pc, snap, pl, ok, restarts, ghost>>

(* reconcile 結束(結束碼算跑完):built = 那一輪開始的時間;有 again 就馬上再一輪。只有起它的那個伺服器會記。 *)
BuildDone ==
  /\ rc.phase = "written" /\ rc.owned
  /\ IF build.again
     THEN /\ Tick /\ build' = [StartRound(build) EXCEPT !.again = FALSE, !.built = rc.start]
          /\ rc' = [phase |-> "started", start |-> now, cardIn |-> FALSE, owned |-> TRUE]
     ELSE /\ build' = [build EXCEPT !.running = FALSE, !.built = rc.start] /\ rc' = RcNone
          /\ UNCHANGED now
  /\ UNCHANGED <<card, seen, tried, pending, pendingAt, progress, status, job, pc, snap, pl, ok, restarts, ghost>>

(* reconcile 沒跑完(結束碼不是 0 或 PACKAGE_PROBLEMS):built 不動。 *)
BuildFails ==
  /\ rc.phase \in {"started", "read"} /\ rc.owned
  /\ build' = [build EXCEPT !.running = FALSE, !.again = FALSE] /\ rc' = RcNone
  /\ UNCHANGED <<now, card, seen, tried, pending, pendingAt, progress, status, job, pc, snap, pl, ok, restarts, ghost>>

-----------------------------------------------------------------------------
(* 這張的填表子行程(apply_run)。 *)
Go(ev) == <<dsv, ev>> \in DOMAIN Cell /\ dsv' \in Cell[<<dsv, ev>>]
NextAt == at < MaxAt /\ at' = at + 1

(* _run_one 寫下 fill_start:refill → running,apply.at 換成現在。 *)
JobFillStart ==
  /\ job = "launched" /\ app = "ship" /\ Go("fill_start") /\ NextAt
  /\ job' = "started"
  /\ UNCHANGED <<now, app, entered, seen, tried, pending, pendingAt, progress, status, build, rc, pc, snap, pl, ok, restarts, ghost>>

(* 還沒寫 fill_start 就結束(前置檢查沒過、當掉、被停):卡沒動。 *)
JobQuitsEarly ==
  /\ job = "launched" /\ job' = "none"
  /\ UNCHANGED <<now, card, seen, tried, pending, pendingAt, progress, status, build, rc, pc, snap, pl, ok, restarts, ghost>>

(* 填完回報:fill_ok / fill_bad / fill_nopage(run_done 把 apply 換成這一輪的紀錄)。 *)
JobEnds ==
  /\ job = "started" /\ dsv = "running"
  /\ \E ev \in {"fill_ok", "fill_bad", "fill_nopage"} : Go(ev)
  /\ NextAt /\ job' = "none"
  /\ failed' = (failed \/ dsv' \in {"stuck", "nopage"})
  /\ UNCHANGED <<now, app, entered, seen, tried, pending, pendingAt, progress, status, build, rc, pc, snap, pl, ok, restarts, launched, dup, advRec, refillFail>>

JobKilled ==
  /\ job = "started" /\ job' = "none"
  /\ UNCHANGED <<now, card, seen, tried, pending, pendingAt, progress, status, build, rc, pc, snap, pl, ok, restarts, ghost>>

(* apply_run.settle(Pilot._settle 在看板鎖裡做):正在填、那一輪卻不在跑 → run_failed,apply.at 換成現在。 *)
Settle ==
  /\ dsv = "running" /\ app = "ship" /\ job = "none"
  /\ \E ev \in {"fill_bad", "fill_nopage"} : Go(ev)
  /\ NextAt
  /\ failed' = (failed \/ dsv' \in {"stuck", "nopage"})
  /\ UNCHANGED <<now, app, entered, seen, tried, pending, pendingAt, progress, status, build, rc, job, pc, snap, pl, ok, restarts, launched, dup, advRec, refillFail>>

(* 頁面不見了:page_lost。填好停著的卡改成 gone、可以重填;失敗卡維持 stuck,保留原因,等他處理好了再交回來。 *)
PageLost ==
  /\ app = "ship" /\ Go("page_lost")
  /\ UNCHANGED <<now, app, entered, at, seen, tried, pending, pendingAt, progress, status, build, rc, job, pc, snap, pl, ok, restarts, ghost>>

(* 使用者換了這張要寄的檔,投遞狀態變 stale;狀態改變本身不是派工證據。 *)
CardStales ==
  /\ app = "ship" /\ dsv \in AutoFill /\ dsv' = "stale"
  /\ UNCHANGED <<now, app, entered, at, seen, tried, pending, pendingAt, progress, status, build, rc, job, pc, snap, pl, ok, restarts, ghost>>

-----------------------------------------------------------------------------
(* Pilot._step。整步拿 _step_lock(一次只跑一步),但每一小段之間別人都插得進來。 *)

(* T1:open() + bd.parse 讀看板檔(不拿鎖)。status 只在這裡讀一次;_settle、_sweep_gone 換的只有 fb。 *)
PilotReads ==
  /\ pc = "idle"
  /\ snap' = [app |-> app, dsv |-> dsv, at |-> at, seen |-> seen, tried |-> tried, status |-> status]
  /\ pc' = "resolve"
  /\ UNCHANGED <<now, card, seen, tried, pending, pendingAt, progress, status, build, rc, job, pl, ok, restarts, ghost>>

(* 每一輪 plan 前先看上次留下的 pending。進度檔有這張的 fill 啟動紀錄、或 apply.at 已變才算派出;否則撤掉記號,本輪快照也同步更新。 *)
PilotResolves ==
  /\ pc = "resolve"
  /\ LET mark == PendingMark
         sent == pending # {} /\
                 ((progress.stage = "fill" /\ progress.t0 >= pendingAt /\ progress.url = "card")
                  \/ at # mark.at)
     IN /\ IF pending = {}
           THEN /\ UNCHANGED <<tried, launched>>
                /\ snap' = snap
           ELSE IF sent
                THEN /\ UNCHANGED tried
                     /\ launched' = launched \cup {mark}
                     /\ snap' = snap
                ELSE /\ tried' = tried \ {mark}
                     /\ launched' = launched
                     /\ snap' = [snap EXCEPT !.tried = @ \ {mark}]
        /\ pending' = {}
        /\ pendingAt' = 0
  /\ pc' = "read"
  /\ UNCHANGED <<now, app, entered, dsv, at, seen, status, build, rc, job, pl, ok, restarts,
                 dup, advRec, failed, refillFail, progress>>

(* T2:build_state()、run_status(),接著 plan()(純函式,照 T1 的看板算)。
   推進:next_step._move;記 seen、補建:autopilot.plan;填表:next_step._fill。 *)
PilotPlans ==
  /\ pc = "read" /\ Tick
  /\ LET jobBusy == job # "none"                      \* run_status('apply').running(子行程活著)
         canAdv == snap.app = "ready" /\ Adv \notin snap.tried
         wait == snap.seen = None \/ build.running \/ build.built <= snap.seen
         seenNew == IF canAdv /\ snap.seen = None THEN now ELSE None
         since == IF snap.seen = None THEN now ELSE snap.seen
         adv == canAdv /\ ~Blocks(snap.status) /\ ~wait
         mark == Fill(snap.at)
         fill == IF snap.app = "ship" /\ snap.dsv \in AutoFill /\ mark \notin snap.tried /\ ~jobBusy
                 THEN mark ELSE NoMark
     IN pl' = [adv |-> adv, fill |-> fill, seenNew |-> seenNew, jobBusy |-> jobBusy,
               build |-> ~build.running /\ canAdv /\ ~Blocks(snap.status) /\ wait /\ since >= build.tried]
  /\ pc' = "planned"
  /\ UNCHANGED <<app, entered, dsv, at, seen, tried, pending, pendingAt, progress, status, build, rc, job, snap, ok, restarts, ghost>>

(* 填表記號和 pending 同一次 bd.rewrite 持久化,再呼叫 start_run。 *)
PilotReserves ==
  /\ pc = "planned"
  /\ tried' = tried \cup (IF pl.fill # NoMark /\ job = "none" THEN {pl.fill} ELSE {})
  /\ pending' = pending \cup (IF pl.fill # NoMark /\ job = "none" THEN {pl.fill} ELSE {})
  /\ pendingAt' = IF pl.fill # NoMark /\ job = "none" THEN now ELSE pendingAt
  /\ pc' = "reserved"
  /\ UNCHANGED <<now, card, seen, status, build, rc, job, snap, pl, ok, restarts, progress, ghost>>

(* start_run('apply', fill):_RUN_LOCK 裡看 run_status,在跑就 409;沒在跑就 Popen + 寫進度檔,回 200。
   接著 plan 要補建就 self.build()(trigger_build)。 *)
PilotLaunches ==
  /\ pc = "reserved"
  /\ IF pl.fill # NoMark /\ job = "none"
     THEN /\ ok' \in BOOLEAN
          /\ IF ok'
             THEN /\ job' = "launched"
                  /\ progress' = [stage |-> "fill", url |-> "card", t0 |-> now]
                  /\ launched' = launched \cup {pl.fill}
                  /\ dup' = (dup \/ pl.fill \in launched)
                  /\ refillFail' = (refillFail \/ failed)
             ELSE /\ UNCHANGED <<job, progress, launched, dup, refillFail>>
     ELSE /\ ok' = FALSE /\ UNCHANGED <<job, progress, launched, dup, refillFail>>
  /\ IF pl.build THEN TriggerBuild ELSE UNCHANGED <<now, build, rc>>
  /\ pc' = "launched"
  /\ UNCHANGED <<card, seen, tried, pending, pendingAt, status, snap, pl, restarts, advRec, failed>>

(* T3:bd.rewrite(mut),拿 live_lock 讀最新 data + fb 再一次寫完。seen 只留還在待你決定的;
   推進以最新驗收再檢查,只有真的推進才記 adv 試過;填表 mark 在 PilotReserves 先記。 *)
PilotWrites ==
  /\ pc = "launched"
  /\ seen' = IF app = "ready" THEN (IF pl.seenNew # None /\ seen = None THEN pl.seenNew ELSE seen) ELSE None
  /\ tried' = (tried \ (IF pl.fill # NoMark /\ ~ok THEN {pl.fill} ELSE {}))
               \cup (IF pl.adv /\ app = "ready" /\ ~Blocks(status) THEN {Adv} ELSE {})
  /\ pending' = pending \ (IF pl.fill # NoMark THEN {pl.fill} ELSE {})
  /\ pendingAt' = IF pl.fill # NoMark THEN 0 ELSE pendingAt
  /\ IF pl.adv /\ app = "ready" /\ ~Blocks(status)
     THEN /\ app' = "ship" /\ dsv' = "todo"
          /\ launched' = launched \cup {Adv} /\ dup' = (dup \/ Adv \in launched)
          /\ advRec' = [on |-> TRUE, used |-> status, cur |-> status, entered |-> entered]
     ELSE UNCHANGED <<app, dsv, launched, dup, advRec>>
  /\ pc' = "idle" /\ pl' = NoPlan /\ snap' = NoSnap /\ ok' = FALSE
  /\ UNCHANGED <<now, entered, at, status, build, rc, job, restarts, progress, failed, refillFail>>

(* 伺服器重開:這一步做到哪就停在哪;_build_state 歸零;reconcile 可能還在跑、寫得進看板,
   但沒有人會記 built;填表子行程照跑(start_new_session)。 *)
ServerRestarts ==
  /\ restarts < MaxRestarts /\ restarts' = restarts + 1
  /\ pc' = "idle" /\ pl' = NoPlan /\ snap' = NoSnap /\ ok' = FALSE
  /\ build' = BuildReset
  /\ rc' = [rc EXCEPT !.owned = FALSE]
  /\ UNCHANGED <<now, card, seen, tried, pending, pendingAt, progress, status, job, ghost>>

-----------------------------------------------------------------------------
Next ==
  \/ Enter \/ YouSave \/ ReconcileReads \/ ReconcileWrites \/ BuildDone \/ BuildFails
  \/ JobFillStart \/ JobQuitsEarly \/ JobEnds \/ JobKilled \/ Settle \/ PageLost \/ CardStales
  \/ PilotReads \/ PilotResolves \/ PilotPlans \/ PilotReserves \/ PilotLaunches \/ PilotWrites \/ ServerRestarts

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------
TypeOK ==
  /\ app \in {"before", "ready", "ship"}
  /\ dsv \in ModelStates
  /\ pc \in {"idle", "resolve", "read", "planned", "reserved", "launched"}
  /\ job \in {"none", "launched", "started"}

(* 鐵律一:同一張卡的同一段只替他按一次。一段 = 一個試過的記號(adv、fill:<apply.at>)。 *)
OncePerMark == ~dup

(* 試過的填表記號要嘛已派出,要嘛仍待下一輪查證。 *)
NoOrphanMark == \A m \in tried : m.kind = "fill" => m \in launched \/ m \in pending

(* 鐵律一的後半:自動填的那一輪失敗了(卡住、沒填成)就停,等他決定;他在回報按「處理好了」(handed)不在模型裡。 *)
StopsAfterFailure == ~refillFail

(* 鐵律二:推進可以投了,用的驗收要是這張進待你決定之後才開始的那一輪、真的驗到這張;
   而且推進那一刻,看板上最新的驗收沒有擋它(不拿舊的結果放行)。 *)
FreshVerdict ==
  advRec.on => /\ advRec.used.start # None /\ advRec.used.start > advRec.entered /\ advRec.used.cardIn
               /\ ~Blocks(advRec.cur)
=============================================================================
