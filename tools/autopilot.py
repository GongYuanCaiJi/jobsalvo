#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autopilot —— 流程自動往下跑:他只在要做決定的地方被叫到(表態、核准)。

以前一張卡從「加入準備」到「可以核准」,中間每一段都要他回看板按一次:
跑準備區 → 待你決定按「可投遞」→ 可投遞按「讓 agent 填表單」。按鈕背後跑的東西沒變,
這裡只是在該按的時候替他按(走的是同一支 board_server.start_run:單飛、真／假腳本、
Chrome 沒連的檢查都一樣)。

  準備區有新卡        → 跑準備區(cut_tailor)
  待你決定、驗收過了  → 推到可投遞(跟「🚀 全部變成可投遞」同一條規則;被擋的留著、原因寫在卡上)
  可投遞、還沒填過    → 讓 agent 填這一張(一次一張,停在送出前)
  回報按了處理好了    → 那張沒填成、卡住、頁不見的接著重填(next_step.handed,同一則只接一次)
  送出結果不明        → 送出一小時後查應徵進度查這一張:查到證據改已投出,查完沒有改成要重填
  填好了、答案又改過  → 他停手一分鐘後,叫回填這張的 agent 照新答案重打(一次一張)
  每天 flow.replies_at → 查回音;那一輪沒跑成(failed/died),當天每小時再試一次,最多再試 REPLY_RETRY_MAX 次

鐵律:
  · 不會自動送出。送出永遠要他在卡上按「✅ 核准送出」(form_record.approval_problem 也擋)。
  · 同一段只替同一張卡按一次:失敗了(準備區沒產出、填表卡住)就停,原因寫在卡上,等他決定。
  · 開啟那一刻已經在流程裡的卡(backlog)不碰:真實看板上一開就可能是幾十張,
    一次全部派 agent 會把他的 Chrome 和額度吃光。要交給自動跑,在看板上按一下。
  · 推進可投遞之前,要等這張卡進待你決定之後的那一輪可投遞夾建置＋投遞前驗收(reconcile)跑完,
    不拿舊的驗收結果放行。
狀態記在標記的 __auto__(跟看板同一份檔,手機電腦都看得到,流水帳查得到)。
"""
import copy, datetime, json, threading, time

import board_doc as bd
import jobrun
import delivery_state as ds
import config as cf
import next_step

KEY = '__auto__'
CRASH = '自動流程這一步出錯了:'
# 排程的查應徵進度沒跑成:隔多久再試、當天最多再試幾次(#289 決定)。看板那一列用同一組數字寫「幾點再試」
REPLY_RETRY_MAX = 3
REPLY_RETRY_GAP = 3600
ADD_RETRY_GAP = 3600     # 貼網址加入碰到 agent 都不能用(額度、限流):隔一小時自己再接著寫


def flow():
    f = dict(cf.DEFAULTS.get('flow') or {})
    f.update(cf.C.get('flow') or {})
    return f


def flow_modes(cfg):
    return {key: bool(cfg.get(key)) for key in ('auto_prep', 'auto_advance', 'auto_fill')}


def plan(data, fb, *, running, build=None, now=None, cfg=None, real=True, replies_last=None,
         add_waiting=0, add_last=None):
    """看現在的看板,決定要替他按哪幾下。純函式:不寫檔、不派東西(測試直接測它)。

    build:伺服器建置(reconcile,含投遞前驗收)的狀態,{'built': 最近跑完那一輪幾點開始的, 'tried': 最近一輪(不管跑完沒)
    幾點開始的, 'running': 現在有沒有在跑}。running:{'prep': bool, 'apply': bool, 'replies': bool}。
    real:是不是真的看板(副本沒有建置)。
    replies_last:上一輪查應徵進度的狀態(phase、finished_at/t0),沒跑成的當天要不要再試看它。
    add_waiting、add_last:貼網址加入上次 agent 都不能用、存著還沒寫卡片的張數,和上一輪的狀態。
    回傳 {'init':第一次的 __auto__ 或 None, 'seen':{id: 幾點看到它進待你決定}, 'advance':[ids], 'build': 要不要補建一輪,
          'prep': 'all'|[id]|None, 'fix': id|None, 'fill': id|None, 'unsure': 送出結果不明要查的那一張|None, 'replies': bool, 'replies_retry': bool, 'add': bool, 'tried':[key],
          'rf':{id: {sig, since}} 新看到的待重打, 'wait': 幾秒後再看一次(等他停手)|None}。"""
    cfg = cfg or flow()
    now = now or datetime.datetime.now()
    build = build or {'built': 0, 'tried': 0, 'running': False}
    auto = fb.get(KEY) if isinstance(fb.get(KEY), dict) else None
    jobs = {j['id']: j for j in data.get('jobs') or []}

    def stage(i):
        m = fb.get(i)
        return m.get('app') if isinstance(m, dict) else None

    def live(i):
        return i in jobs and ds.app_of(fb.get(i)) != 'rm'

    out = {'init': None, 'seen': {}, 'advance': [], 'build': False, 'prep': None, 'fix': None, 'fill': None, 'unsure': None,
           'customize': None, 'accept': [],
           'replies': False, 'replies_retry': False, 'add': False,
           'tried': [], 'rf': {}, 'wait': None}
    # 貼網址加入上次 agent 都不能用、存著還沒寫的:隔一小時自己接著寫(是他自己貼的,不管自動流程開沒開)
    last = add_last or {}
    out['add'] = bool(add_waiting) and not running.get('add') and \
        now.timestamp() - (last.get('finished_at') or last.get('t0') or 0) >= ADD_RETRY_GAP
    if auto is None:
        # 第一次:當下已經在流程裡的卡記成 backlog,之後不碰
        backlog = sorted(i for i in jobs if stage(i) in ('prep', 'ready', 'ship') and live(i))
        out['init'] = {'since': now.isoformat(timespec='seconds'), 'skip': backlog, 'tried': [],
                       'seen': {}, 'flow': flow_modes(cfg)}
        return out
    tried, seen = set(auto.get('tried') or []), auto.get('seen') or {}
    nexts = next_step.of(fb, data.get('jobs'), data.get('status'), flow=cfg, now=now, real=real, build=build)

    def does(i):
        return (nexts[i]['auto'] or {}).get('do') if i in nexts else None

    if cfg.get('auto_advance'):
        for i in jobs:
            # 新進待你決定的卡:記下幾點看到的,要等這之後才開始的那一輪建置跑完;被把關擋住的也記。
            # 推過一次、他又手動退回來的,留在原地
            if stage(i) == 'ready' and i not in seen and next_step.mine(fb, jobs[i]) and ('adv:' + i) not in tried:
                out['seen'][i] = now.timestamp()
        # 推哪幾張、等不等驗收跑完只照下一步(被投遞前把關擋住的留著,原因寫在卡上)
        for i, x in nexts.items():
            if does(i) == 'advance' and not x['auto']['wait']:
                out['advance'].append(i)
                out['tried'].append(x['auto']['mark'])
        # 有卡在等建置,而看到它之後還沒有開始過任何一輪(剛進來、伺服器重開過):補建一輪。
        # 開始過但沒跑完的(來源壞了)不在這裡重試,改了東西存檔會再建
        since = dict(seen, **out['seen'])
        out['build'] = not build['running'] and any(
            does(i) == 'advance' and x['auto']['wait'] and float(since[i]) >= build['tried'] for i, x in nexts.items())

    if cfg.get('auto_prep') and not running.get('prep'):
        todo = [i for i in jobs if does(i) == 'prep']
        if todo:
            others = [i for i in jobs if stage(i) == 'prep' and live(i) and i not in todo]
            # 準備區只有這幾張:一輪跑完(cut_tailor 一次判一批最省);混著 backlog 或失敗過的:一次只跑一張
            batch = todo if not others else todo[:1]
            out['prep'] = 'all' if not others else batch
            out['tried'] += ['prep:' + i for i in batch]

    # 幫你填表和查應徵進度都用 agent 的 Chrome,一邊收尾會把 Chrome 關掉:一次只排一件
    chrome_busy = any(running.get(k) for k in jobrun.BROWSER_JOBS)

    if cfg.get('auto_fill') and not chrome_busy:
        # 先重打:這幾張離確認只差一步。只重打停著等你的(填好了):卡住的原因在卡上等他;
        # 上傳的是舊檔的整張重填,走下面那條。同一組「哪幾條、什麼值」只重打一次:
        # 重打完還標著(agent 沒翻、沒打好)就停在卡上等他。不能用 apply.at 當記號——
        # 每重打一次 at 就換新,會變成同一張一直重打、後面的永遠排不到(副本上真的發生過)。
        rfs = auto.get('rf') or {}
        for i, x in nexts.items():
            x = x['auto'] or {}
            if x.get('do') != 'fix':
                continue
            if (rfs.get(i) or {}).get('sig') != x['sig']:
                out['rf'][i] = {'sig': x['sig'], 'since': now.isoformat(timespec='seconds')}   # 新的改動:從現在開始等他停手
            if x['wait'] > 0:
                out['wait'] = x['wait'] if out['wait'] is None else min(out['wait'], x['wait'])
                continue
            out['fix'] = i
            break

    if cfg.get('auto_fill'):
        # 要寄的檔跟職缺要求不符(#449):自動客製(不用 Chrome,跟填表可以同時)、通過的自動收下、可投遞夾補建
        for i, x in nexts.items():
            x = x['auto'] or {}
            if x.get('do') == 'customize' and not running.get('customize') and not out['customize']:
                out['customize'] = {'url': i, 'items': x['items']}
                out['tried'].append(x['mark'])
            elif x.get('do') == 'accept':
                out['accept'].append((i, x['items']))
            elif x.get('do') == 'build':
                out['build'] = True

    if cfg.get('auto_fill') and not chrome_busy and not out['fix']:
        # 填哪一張只照下一步(next_step 的 auto):卡上寫「排隊中、會自動重填」的就是這幾張,停著的頁上限也在那裡算
        for i, x in nexts.items():
            if (x['auto'] or {}).get('do') == 'fill':
                out['fill'] = i
                out['tried'].append(x['auto']['mark'])
                break

    today = now.date().isoformat()
    retry = auto.get('replies_retry') if isinstance(auto.get('replies_retry'), dict) else {}
    last = replies_last or {}
    ended = last.get('finished_at') or last.get('t0')
    # 今天排程那一輪沒跑成、今天還會再試(沒試滿 REPLY_RETRY_MAX 次):送出結果不明的卡交給那一輪一起查,不另外排
    # (單查一張會蓋掉那一輪的結果,看起來像跑成了,其他卡今天就不再試)。試滿了就照常單查
    retry_left = (auto.get('replies_day') == today and last.get('phase') in ('failed', 'died')
                  and isinstance(ended, (int, float))
                  and datetime.datetime.fromtimestamp(ended).date() == now.date()
                  and (retry.get('n', 0) if retry.get('day') == today else 0) < REPLY_RETRY_MAX)
    if not chrome_busy and not out['fill'] and not out['fix'] and not retry_left:
        # 送出結果不明的卡:按下送出過了 UNSURE_WAIT(確認信來得及到),就叫查應徵進度去信箱和平台應徵紀錄查這一張。
        # 每一次送出只查一次(記號帶送出時間);沒查出結果的,每天那一輪查應徵進度會再一起查
        import reply_run
        for i in reply_run.unsure_cards(fb):
            tried_at = reply_run.submit_tried_at(fb[i])
            mark = 'unsure:' + i + ':' + tried_at
            t = reply_run._local_time(tried_at)
            due = t is None or (now - t).total_seconds() >= reply_run.UNSURE_WAIT
            if due and mark not in tried and i in jobs:
                out['unsure'] = i
                out['tried'].append(mark)
                break

    at = cf.clock(cfg.get('replies_at'))
    if at and not chrome_busy and not out['fill'] and not out['fix'] and not out['unsure']:
        due = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
        # 今天排程那一輪已經開跑過:只有上一輪是今天沒跑成的(不是部分完成),隔滿一小時、今天還沒再試滿幾次,才再試
        again = retry_left and now.timestamp() - ended >= REPLY_RETRY_GAP
        if now >= due and (auto.get('replies_day') != today or again):
            waiting = [i for i in jobs if (stage(i) == 'sent' and (fb.get(i) or {}).get('oc') not in ('rej', 'wd')
                                           or stage(i) == 'ship' and ds.state(fb.get(i)) == 'unsure') and live(i)]
            out['replies'] = bool(waiting)
            out['replies_retry'] = bool(waiting and again)
    return out


class Pilot:
    """board_server 裡的那一個。存檔之後、每一輪跑完、伺服器起來、每分鐘(只為了查回音的時間)看一次。"""

    def __init__(self, state, start_run, run_status, build_state, is_real, report=None, build=None):
        self.state, self.start_run, self.run_status = state, start_run, run_status
        self.build_state, self.is_real, self.report, self.build = build_state, is_real, report, build
        self._lock = threading.Lock()
        self._step_lock = threading.RLock()
        self._timer = None
        self._stopped = False
        self._crash_cleared = False
        self.last = {}

    def kick(self, delay=1.5):
        """合併連續的觸發(存檔常常一次好幾筆),停一下再看一次。"""
        with self._lock:
            if self._stopped:
                return
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(delay, self._safe_step)
            self._timer.daemon = True
            self._timer.start()

    def stop(self):
        """伺服器要關了:不再排下一次,等正在跑的那一次做完才回。副本的暫存資料夾要在它寫完之後才刪得乾淨,
        不然刪到一半它又寫進新檔,資料夾就留下來了。"""
        with self._lock:
            self._stopped = True
            if self._timer:
                self._timer.cancel()
                self._timer = None
        with self._step_lock:
            pass

    def _safe_step(self):
        if self._stopped:
            return
        started = datetime.datetime.now().isoformat(timespec='seconds')
        # 這個行程第一次整步跑完、或上一次出錯:收掉之前的「這一步出錯了」(伺服器重開前留下的也算)
        recheck = not self._crash_cleared or bool((self.last or {}).get('error'))
        try:
            self.step()
        except Exception as e:  # noqa: BLE001 — 自動流程最外層:出錯不能拖垮伺服器、下一次觸發再試,原因照實寫進看板的回報
            self.last = {'error': str(e)[:200], 't': time.time()}
            if self.report:                          # 同一句還沒處理前再報只加次數,不會每分鐘洗版
                self.report('自動流程', CRASH + f'{type(e).__name__}: {str(e)[:160]}',
                            need='下一次存檔或每分鐘會再試;一直出現就重開看板伺服器')
            return
        if recheck and self.report:
            # 之前那幾則講的是已經不在的問題(多半是程式改版前的錯)
            import agent_report
            agent_report.resolve(None, '後來自動流程整步跑完沒出錯', live=self.state, src='自動流程', before=started,
                                 only=lambda it: str(it.get('msg', '')).startswith(CRASH))
            self._crash_cleared = True
            if (self.last or {}).get('error'):
                self.last = {}

    def _sync_flow(self, data, fb):
        a = fb.get(KEY)
        if not isinstance(a, dict): return
        modes = flow_modes(flow())
        previous = a.get('flow')
        if previous == modes: return
        stages = {'auto_prep': 'prep', 'auto_advance': 'ready', 'auto_fill': 'ship'}
        newly_enabled = {stage for key, stage in stages.items()
                         if modes[key] and isinstance(previous, dict) and not previous.get(key)}
        skip = set(a.get('skip') or [])
        if newly_enabled:
            skip.update(j['id'] for j in data.get('jobs') or [] if isinstance(j, dict)
                        and ds.app_of(fb.get(j['id'])) in newly_enabled)
        def mutate(current):
            item = current.get(KEY)
            if isinstance(item, dict):
                item['flow'] = modes
                if newly_enabled:
                    item['skip'] = sorted(set(item.get('skip') or []) | skip)
        bd.set_fb(mutate, live=self.state, by='autopilot')
        a['flow'] = modes
        if newly_enabled: a['skip'] = sorted(skip)

    def _sweep_gone(self, fb):
        """agent 的 Chrome 關掉或重開過:那之前填好的頁一定不在了,卡上改成要重填(👀、要它改都不再顯示)。
        開那一頁的那一家確定接不回來(設定換了 agent、更新前填的沒記是哪一家):一樣改成要重填,卡上寫哪一種。
        以前要等他按 👀 截不到、按送出才發現,這之間卡上一直寫填好了、還能按 👀。只看真的機器上的 Chrome,副本不看。"""
        import chrome_door
        st = self.run_status('apply')
        running = st.get('url') if st.get('running') else ''
        # 在看板鎖內照現在的看板算(不是這一輪開頭讀的快照):快照之後剛填好的新頁不會被誤標(#308)
        got = chrome_door.sweep_gone(self.state, running, by='autopilot', unreachable=True)
        if got:
            # 這一輪接下來照看板現在的樣子排(不在舊快照上再標一次:快照之後別處改過的卡,那樣會算錯)
            fb.clear()
            fb.update(json.loads(bd.parse(bd.read_doc(self.state))['fb']))
        return list(got)

    def _settle(self, fb):
        """卡停在正在填、正在送出,那一輪卻已經不在跑(被停止、當掉):照狀態表收尾(apply_run.settle)。
        在看板鎖裡再看一次有沒有在跑:剛開跑的那一輪先寫了進度才動卡,不會被當成停掉的。"""
        import apply_run
        if not any(ds.state(m) in ds.WORKING for k, m in fb.items() if isinstance(m, dict) and not k.startswith('__')):
            return

        def mut(d):
            st = self.run_status('apply') or {}
            apply_run.settle(d, (st.get('url') or '*') if st.get('running') else None)
            fb.clear()
            fb.update(copy.deepcopy(d))
        bd.set_fb(mut, live=self.state, by='autopilot')

    def save_settings(self, saver):
        """設定儲存與流程切換共用鎖，避免開關之間的 timer 先派出舊卡。"""
        with self._step_lock:
            bad = saver()
            if not bad:
                with open(self.state, encoding='utf-8') as source:
                    p = bd.parse(source.read())
                self._sync_flow(p['data'], json.loads(p['fb']))
            return bad

    def step(self):
        with self._step_lock:
            return self._step()

    def _step(self):
        with open(self.state, encoding='utf-8') as f:
            p = bd.parse(f.read())
        fb = json.loads(p['fb'])
        self._sync_flow(p['data'], fb)
        bs = self.build_state()
        status = {k: self.run_status(k) or {} for k in ('prep', 'add', 'customize') + jobrun.BROWSER_JOBS}
        running = {k: bool(st.get('running')) for k, st in status.items()}
        self._settle(fb)
        self._sweep_gone(fb)
        import add_job
        pl = plan(p['data'], fb, build=bs, running=running,
                  real=self.is_real(), replies_last=status['replies'],
                  add_waiting=add_job.waiting() if self.is_real() else 0, add_last=status['add'])
        self.last = {'plan': {k: v for k, v in pl.items() if k != 'init'}, 't': time.time()}
        started = {}
        if pl['prep']:
            started['prep'] = self.start_run('prep', {} if pl['prep'] == 'all' else {'url': pl['prep'][0]})
        if pl['fix']:
            started['fix'] = self.start_run('apply', {'stage': 'fix', 'url': pl['fix']})
        elif pl['fill']:
            started['fill'] = self.start_run('apply', {'stage': 'fill', 'url': pl['fill']})
        if pl['unsure']:
            started['unsure'] = self.start_run('replies', {'url': pl['unsure']})
        elif pl['replies']:
            started['replies'] = self.start_run('replies', {})
        if pl['add']:
            started['add'] = self.start_run('add', {})
        if pl['customize']:
            started['cust'] = self.start_run('customize', pl['customize'])
        if pl['accept']:
            import customize as cu
            for url, items in pl['accept']:
                for item in items:
                    cu.accept(url, item, board=self.state)
        if pl['accept'] and not pl['build'] and self.build and self.is_real():
            self.build()                              # 收下的客製版、不寄的附件要進可投遞夾(要建的話下面那一輪一起建)
        ok = {k: code == 200 for k, (code, _st) in started.items()}
        need_browser = [k for k, (code, st) in started.items() if code == 409 and (st or {}).get('needs_browser')]
        was_blocked = (fb.get(KEY) or {}).get('blocked')
        today = datetime.date.today().isoformat()

        def mut(f):
            a = f.get(KEY) if isinstance(f.get(KEY), dict) else None
            if a is None:
                if pl['init']:
                    f[KEY] = pl['init']
                return
            seen = dict(a.get('seen') or {})
            seen.update(pl['seen'])
            a['seen'] = {i: g for i, g in seen.items() if (f.get(i) or {}).get('app') == 'ready'}
            # 只有真的發動了才算「試過」;沒發動(Chrome 沒連、另一輪在跑)下一次再來
            done = [k for k in pl['tried'] if k.startswith('adv:') or ok.get(k.split(':', 1)[0])]
            rf = dict(a.get('rf') or {})
            rf.update(pl['rf'])
            if ok.get('fix') and pl['fix'] in rf:          # 真的叫了才算:同一次改動不再叫第二次
                rf[pl['fix']] = dict(rf[pl['fix']], sent=True)
            # 這張沒有待重打的了(重打好了,或他自己按了改):那一筆拿掉。他之後把答案改回重打過的值,是新的一次改動
            a['rf'] = {i: v for i, v in rf.items() if (f.get(i) or {}).get('app') == 'ship' and next_step.fix_sig(f, i)}
            a['tried'] = (list(a.get('tried') or []) + done)[-500:]
            if ok.get('replies'):
                a['replies_day'] = today
                # 當天第幾次再試:看板那一列照這個寫「還會再試」或「今天不再試了」
                r = a.get('replies_retry') if isinstance(a.get('replies_retry'), dict) else {}
                a['replies_retry'] = {'day': today, 'n': (r.get('n', 0) if r.get('day') == today else 0)
                                      + (1 if pl.get('replies_retry') else 0)}
            if need_browser:
                a['blocked'] = 'ego 沒準備好'
            elif ok.get('fill') or ok.get('fix') or ok.get('replies') or ok.get('unsure'):
                a.pop('blocked', None)
            for i in pl['advance']:
                if (f.get(i) or {}).get('app') == 'ready':
                    f[i]['app'] = 'ship'
            f[KEY] = a
        A = fb.get(KEY) if isinstance(fb.get(KEY), dict) else {}
        expire = any(not next_step.fix_sig(fb, i) for i in A.get('rf') or {})   # 有張卡的待重打清掉了,那一筆要跟著拿掉
        if pl['init'] or pl['seen'] or pl['advance'] or pl['rf'] or started or expire:
            bd.set_fb(mut, live=self.state, by='autopilot')
        if was_blocked and not need_browser and (ok.get('fill') or ok.get('fix') or ok.get('replies')):
            # 連上了、自動的步驟接著跑:之前「ego 沒準備好」那則回報收掉,不留著讓他以為還要處理
            import agent_report
            agent_report.resolve(None, 'agent 的瀏覽器準備好了,自動的步驟接著跑', live=self.state, src='自動流程')
        if need_browser and self.report and not was_blocked:
            # 只講一次:連上之前不會每一分鐘都再講
            self.report('自動流程', '要讓 agent 填表或查回音,但 agent 的瀏覽器(ego)沒準備好,自動的這幾步先停著',
                        need='到「⚙ 設定 → 🤖 Agent 與瀏覽器」按「連接」')
        if pl['build'] and self.build:
            # 準備區跑完是 cut_tailor 直接寫檔推進待你決定,不經過存檔,伺服器不會自己建;這裡補一輪
            self.build()
        if pl['advance'] or pl['init']:
            self.kick()                               # 剛推進可投遞的卡,接著就能填
        elif pl['wait']:
            self.kick(delay=pl['wait'] + 0.5)         # 等他停手改答案;中間再存檔會重新算
        return pl

    def start(self):
        """伺服器起來時看一次(補上停機期間該做的),之後每分鐘看一次時間(查回音)。"""
        with open(self.state, encoding='utf-8') as source:
            existing = json.loads(bd.parse(source.read())['fb'])
        if not isinstance(existing.get(KEY), dict):
            self.step()  # 首次盤點必須成功；已有流程的派工留到 HTTP 開始服務後
        self.kick(delay=3)

        def tick():
            while not self._stopped:
                time.sleep(60)
                self.kick(delay=0.1)
        threading.Thread(target=tick, daemon=True).start()
