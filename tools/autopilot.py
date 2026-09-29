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
import datetime, hashlib, json, re, threading, time

import board_doc as bd
import card
import config as cf
import form_record as fr
import ship

KEY = '__auto__'
# 答案改完多久沒再動才叫 agent 重打:他常常一次改好幾條(或同一條改兩次),
# 改一條就派一次,agent 會在頁面上重打好幾輪。副本沒有真的 agent,等短一點。
QUIET = {True: 60, False: 3}
# 排程的查應徵進度沒跑成:隔多久再試、當天最多再試幾次(#289 決定)。看板那一列用同一組數字寫「幾點再試」
REPLY_RETRY_MAX = 3
REPLY_RETRY_GAP = 3600


def flow():
    f = dict(cf.DEFAULTS.get('flow') or {})
    f.update(cf.C.get('flow') or {})
    return f


def flow_modes(cfg):
    return {key: bool(cfg.get(key)) for key in ('auto_prep', 'auto_advance', 'auto_fill')}


def ship_blocked(fb, job, status):
    """這張能不能進可投遞:不能就回原因。跟看板 board.js 的 shipBlocked 同一條規則。"""
    # 客製只看這張現在會寄的那幾份:換了履歷留下的舊紀錄不擋(以前每一筆都看,卡永遠卡在待你決定)
    waiting = ship.customization_problem(job, fb)
    if waiting:
        return waiting
    st = status or {}
    # 字跟看板一字不差:核准規則(form_record.approval_problem)也用這一支,卡上顯示的就是這句
    if st.get('schema_version') != 2:
        return '投遞前驗收還沒跑完(背景會自己跑,好了這裡會自己更新)'
    if st.get('checked_links') is False:
        return '職缺連結還沒檢查(背景會自己檢查,好了這裡會自己更新)'
    bad = [x.get('msg') for x in st.get('issues') or [] if x.get('jid') == job['id'] and not x.get('soft')]
    return ('驗收未通過：' + '；'.join(bad)) if bad else ''


def company_blocked(fb, job):
    blocked = fb.get('__block__')
    if not isinstance(blocked, list): return False
    board = cf.C.get('board') or {}
    name = card.company(job, board.get('company_alias'), board.get('title_words'))
    return any(card.same_company(x, name) for x in blocked)


def fix_sig(fb, i):
    """這張等著照新答案重打的是哪幾條、答案庫現在寫什麼。空字串 = 沒有要重打的。
    值也算進去:同一條又改一次,要重新等他停手。"""
    import apply_run                                 # 要重翻的判斷只有一份,在 apply_run
    bank = {e.get('k'): [e.get('v'), e.get('zh')] for e in fb.get('__ans__') or [] if isinstance(e, dict)}
    f = ((fb.get(i) or {}).get('form') or {}).get('f') or []
    ks = sorted(set(str(x.get('k') or x.get('q')) for x in f if x.get('refill'))
                | set('tr:' + str(t['k']) for t in apply_run.to_translate(fb, i)))
    if not ks:
        return ''
    raw = json.dumps([[k, bank.get(k[3:] if k.startswith('tr:') else k)] for k in ks], ensure_ascii=False)
    return hashlib.blake2b(raw.encode('utf-8'), digest_size=8).hexdigest()   # 只是「有沒有又改過」的指紋,不是卡片 ID


def plan(data, fb, *, verified_gen, build_running, running, now=None, cfg=None, real=True, replies_last=None):
    """看現在的看板,決定要替他按哪幾下。純函式:不寫檔、不派東西(測試直接測它)。

    verified_gen:伺服器建置(reconcile,含投遞前驗收)已經跑完幾輪;build_running:現在有沒有在跑。
    running:{'prep': bool, 'apply': bool, 'replies': bool}。real:是不是真的看板(副本沒有建置)。
    replies_last:上一輪查應徵進度的狀態(phase、finished_at/t0),沒跑成的當天要不要再試看它。
    回傳 {'init':第一次的 __auto__ 或 None, 'seen':{id: 要等到第幾輪}, 'advance':[ids],
          'prep': 'all'|[id]|None, 'fix': id|None, 'fill': id|None, 'replies': bool, 'replies_retry': bool, 'tried':[key],
          'rf':{id: {sig, since}} 新看到的待重打, 'wait': 幾秒後再看一次(等他停手)|None}。"""
    cfg = cfg or flow()
    now = now or datetime.datetime.now()
    auto = fb.get(KEY) if isinstance(fb.get(KEY), dict) else None
    jobs = {j['id']: j for j in data.get('jobs') or []}

    def stage(i):
        m = fb.get(i)
        return m.get('app') if isinstance(m, dict) else None

    def live(i):
        return i in jobs and not (fb.get(i) or {}).get('rm')

    out = {'init': None, 'seen': {}, 'advance': [], 'prep': None, 'fix': None, 'fill': None, 'replies': False, 'replies_retry': False,
           'tried': [], 'rf': {}, 'wait': None}
    if auto is None:
        # 第一次:當下已經在流程裡的卡記成 backlog,之後不碰
        backlog = sorted(i for i in jobs if stage(i) in ('prep', 'ready', 'ship') and live(i))
        out['init'] = {'since': now.isoformat(timespec='seconds'), 'skip': backlog, 'tried': [],
                       'seen': {}, 'flow': flow_modes(cfg)}
        return out
    skip, tried, seen = set(auto.get('skip') or []), set(auto.get('tried') or []), auto.get('seen') or {}

    def mine(i):
        # 封鎖的公司:畫面上看不到,也不該在背景替它準備、填表
        return live(i) and i not in skip and not company_blocked(fb, jobs[i])

    if cfg.get('auto_advance'):
        for i in jobs:
            if stage(i) != 'ready' or not mine(i) or ('adv:' + i) in tried:
                continue                     # 推過一次、他又手動退回來的,留在原地
            if i not in seen:
                # 等這張進來之後才開始(或正在跑)的那一輪建置跑完
                out['seen'][i] = verified_gen + (1 if build_running else 0)
                if real:
                    continue
            elif real and (build_running or verified_gen <= int(seen[i])):
                continue
            if not company_blocked(fb, jobs[i]) and not ship_blocked(fb, jobs[i], data.get('status')):
                out['advance'].append(i)
                out['tried'].append('adv:' + i)

    if cfg.get('auto_prep') and not running.get('prep'):
        todo = [i for i in jobs if stage(i) == 'prep' and mine(i) and not jobs[i].get('prep_note')
                and ('prep:' + i) not in tried]
        if todo:
            others = [i for i in jobs if stage(i) == 'prep' and live(i) and i not in todo]
            # 準備區只有這幾張:一輪跑完(cut_tailor 一次判一批最省);混著 backlog 或失敗過的:一次只跑一張
            batch = todo if not others else todo[:1]
            out['prep'] = 'all' if not others else batch
            out['tried'] += ['prep:' + i for i in batch]

    # 代投和查應徵進度都用 agent 的 Chrome,一邊收尾會把 Chrome 關掉:一次只排一件
    chrome_busy = running.get('apply') or running.get('replies')

    def unsure(m):
        # 上次送出沒確認成功(可能其實送出去了):不替它重打、重填,等他先確認到底送出沒有
        sf = (m.get('apply') or {}).get('submit_fail')
        return bool(sf) and not sf.get('cleared')
    if cfg.get('auto_fill') and not chrome_busy:
        # 先重打:這幾張離核准只差一步。只重打填好了(ok)的:卡住的原因在卡上等他;
        # 履歷換過(stale)的整張重填,走下面那條。同一組「哪幾條、什麼值」只重打一次:
        # 重打完還標著(agent 沒翻、沒打好)就停在卡上等他。不能用 apply.at 當記號——
        # 每重打一次 at 就換新,會變成同一張一直重打、後面的永遠排不到(副本上真的發生過)。
        rfs = auto.get('rf') or {}
        for i in jobs:
            m = fb.get(i) or {}
            a = m.get('apply') or {}
            if (stage(i) != 'ship' or not mine(i) or (m.get('form') or {}).get('lock') or not a.get('ok')
                    or a.get('stage') not in ('fill', 'fix') or not a.get('session') or a.get('stale') or unsure(m)):
                continue
            if fr.answers_pending(fb, i):
                continue                     # 還有答案等他確認:確認完再一次重打,不然確認一條就要再打一輪
            sig = fix_sig(fb, i)
            k = 'fix:' + i + ':' + sig
            if not sig or k in tried:
                continue
            r = rfs.get(i) or {}
            if r.get('sig') != sig:
                out['rf'][i] = {'sig': sig, 'since': now.isoformat(timespec='seconds')}
                left = QUIET[bool(real)]
            else:
                try:
                    since = datetime.datetime.fromisoformat(r.get('since'))
                except (TypeError, ValueError):
                    since = now
                left = QUIET[bool(real)] - (now - since).total_seconds()
            if left > 0:
                out['wait'] = left if out['wait'] is None else min(out['wait'], left)
                continue
            out['fix'] = i
            out['tried'].append(k)
            break

    def held(i):
        # 填好停在送出前、分頁留在 agent 的 Chrome 等他看的那幾張(卡住的也算:分頁一樣開著)
        m = fb.get(i) or {}
        a = m.get('apply') or {}
        return (stage(i) == 'ship' and live(i) and not (m.get('form') or {}).get('lock')
                and a.get('stage') in ('fill', 'fix') and bool(a.get('tab_id')) and not a.get('sent'))

    try:
        cap = max(0, int(cfg.get('fill_max') if cfg.get('fill_max') is not None else 5))
    except (TypeError, ValueError):
        cap = 5
    # 停著等他的已經到上限:先不填新的(每張佔一個開著的分頁,他也看不完)。他核准、退掉一張就接著填。
    # 已經停著的那張要重填(履歷換過)不算新的,照樣做
    full = bool(cap) and sum(1 for i in jobs if held(i)) >= cap
    if cfg.get('auto_fill') and not chrome_busy and not out['fix']:
        for i in jobs:
            m = fb.get(i) or {}
            if stage(i) != 'ship' or not mine(i) or (m.get('form') or {}).get('lock') or m.get('approve') or unsure(m):
                continue
            a = m.get('apply') or {}
            if a and not a.get('stale') and not a.get('gone'):
                continue                     # 填過了(成功或卡住都一樣:卡住的原因在卡上,等他);頁面不見了的要重填
            if full and not held(i):
                continue
            # 「🔁 再投一次」把上一次的表單和填表紀錄收進 tries:第幾次投要算進記號,
            # 不然跟第一次一樣是 fill:<id>:new,卡上寫排隊中,卻永遠不再填
            n = len(m.get('tries') or [])
            k = 'fill:' + i + ':' + str(a.get('at') or 'new') + (':' + str(n) if n else '')
            if k in tried:
                continue
            out['fill'] = i
            out['tried'].append(k)
            break

    at = str(cfg.get('replies_at') or '').strip()
    mt = re.fullmatch(r'([0-9]{1,2}):([0-9]{2})', at)
    if mt and int(mt.group(1)) < 24 and int(mt.group(2)) < 60 and not chrome_busy \
            and not out['fill'] and not out['fix']:
        due = now.replace(hour=int(mt.group(1)), minute=int(mt.group(2)), second=0, microsecond=0)
        today = now.date().isoformat()
        retry = auto.get('replies_retry') if isinstance(auto.get('replies_retry'), dict) else {}
        last = replies_last or {}
        ended = last.get('finished_at') or last.get('t0')
        # 今天排程那一輪已經開跑過:只有上一輪是今天沒跑成的(不是部分完成),隔滿一小時、今天還沒再試滿幾次,才再試
        again = (auto.get('replies_day') == today and last.get('phase') in ('failed', 'died')
                 and isinstance(ended, (int, float))
                 and datetime.datetime.fromtimestamp(ended).date() == now.date()
                 and now.timestamp() - ended >= REPLY_RETRY_GAP
                 and (retry.get('n', 0) if retry.get('day') == today else 0) < REPLY_RETRY_MAX)
        if now >= due and (auto.get('replies_day') != today or again):
            waiting = [i for i in jobs if stage(i) == 'sent' and live(i)
                       and (fb.get(i) or {}).get('oc') not in ('rej', 'wd')]
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
        self._fresh = True
        self._stopped = False
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
        try:
            self.step()
        except Exception as e:                       # 自動跑出錯不能拖垮伺服器;下一次觸發再試
            self.last = {'error': str(e)[:200], 't': time.time()}

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
                        and isinstance(fb.get(j['id']), dict)
                        and fb[j['id']].get('app') in newly_enabled
                        and not fb[j['id']].get('rm'))
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
        以前要等他按 👀 截不到才發現,這之間卡上一直寫填好了、還能按 👀。只看真的機器上的 Chrome,副本不看。"""
        import agent_chrome
        st = self.run_status('apply')
        gone = agent_chrome.gone_pages(fb, st.get('url') if st.get('running') else '')
        if gone:
            bd.set_fb(lambda d: agent_chrome.mark_gone(d, [u for u in gone if isinstance(d.get(u), dict)]),
                      live=self.state, by='autopilot')
            agent_chrome.mark_gone(fb, gone)
        return gone

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
        if self._fresh:
            # seen 的世代只在本次服務程序有效；重啟後清掉舊基準，再替 ready 卡補跑驗收。
            if isinstance(fb.get(KEY), dict) and fb[KEY].get('seen'):
                def reset_seen(current):
                    if isinstance(current.get(KEY), dict): current[KEY]['seen'] = {}
                bd.set_fb(reset_seen, live=self.state, by='autopilot')
                fb[KEY]['seen'] = {}
            self._fresh = False
        self._sync_flow(p['data'], fb)
        bs = self.build_state()
        status = {k: self.run_status(k) or {} for k in ('prep', 'apply', 'replies')}
        running = {k: bool(st.get('running')) for k, st in status.items()}
        if self.is_real():
            self._sweep_gone(fb)
        pl = plan(p['data'], fb, verified_gen=bs['gen'], build_running=bs['running'], running=running,
                  real=self.is_real(), replies_last=status['replies'])
        self.last = {'plan': {k: v for k, v in pl.items() if k != 'init'}, 't': time.time()}
        started = {}
        if pl['prep']:
            started['prep'] = self.start_run('prep', {} if pl['prep'] == 'all' else {'url': pl['prep'][0]})
        if pl['fix']:
            started['fix'] = self.start_run('apply', {'stage': 'fix', 'url': pl['fix']})
        elif pl['fill']:
            started['fill'] = self.start_run('apply', {'stage': 'fill', 'url': pl['fill']})
        if pl['replies']:
            started['replies'] = self.start_run('replies', {})
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
            if ok.get('fix'):
                rf[pl['fix']] = dict(rf.get(pl['fix']) or {}, done=True)     # 看板據此不再寫「會自動重打」
            # 標記清掉了(重打好了,或他自己按了改)就把這張的記號一起清掉:記號只在還標著的時候算數。
            # 不清的話,他之後把答案改回重打過的值,伺服器認得那組值不再派,卡上卻一直寫「會自動重打」。
            # 重打完還標著的(agent 沒打好)記號留著,不會一直重來。
            marks = set(rf) | {k[4:].rsplit(':', 1)[0] for k in a.get('tried') or [] if k.startswith('fix:')}
            clear = {i for i in marks if not fix_sig(f, i)}
            a['tried'] = [k for k in a.get('tried') or [] if not (k.startswith('fix:') and k[4:].rsplit(':', 1)[0] in clear)]
            a['rf'] = {i: v for i, v in rf.items() if (f.get(i) or {}).get('app') == 'ship' and i not in clear}
            a['tried'] = (list(a.get('tried') or []) + done)[-500:]
            if ok.get('replies'):
                a['replies_day'] = today
                # 當天第幾次再試:看板那一列照這個寫「還會再試」或「今天不再試了」
                r = a.get('replies_retry') if isinstance(a.get('replies_retry'), dict) else {}
                a['replies_retry'] = {'day': today, 'n': (r.get('n', 0) if r.get('day') == today else 0)
                                      + (1 if pl.get('replies_retry') else 0)}
            if need_browser:
                a['blocked'] = 'Chrome 沒連接'
            elif ok.get('fill') or ok.get('fix') or ok.get('replies'):
                a.pop('blocked', None)
            for i in pl['advance']:
                if (f.get(i) or {}).get('app') == 'ready':
                    f[i]['app'] = 'ship'
            f[KEY] = a
        A = fb.get(KEY) if isinstance(fb.get(KEY), dict) else {}
        marked = set(A.get('rf') or {}) | {k[4:].rsplit(':', 1)[0] for k in A.get('tried') or [] if k.startswith('fix:')}
        expire = any(not fix_sig(fb, i) for i in marked)      # 有張卡的待重打清掉了,記號要跟著清
        if pl['init'] or pl['seen'] or pl['advance'] or pl['rf'] or started or expire:
            bd.set_fb(mut, live=self.state, by='autopilot')
        if was_blocked and not need_browser and (ok.get('fill') or ok.get('fix') or ok.get('replies')):
            # 連上了、自動的步驟接著跑:之前「Chrome 沒連接」那則回報收掉,不留著讓他以為還要處理
            import agent_report
            agent_report.resolve_from('自動流程', 'agent 的 Chrome 連上了,自動的步驟接著跑',
                                      datetime.datetime.now().isoformat(timespec='seconds'), live=self.state)
        if need_browser and self.report and not was_blocked:
            # 只講一次:連上之前不會每一分鐘都再講
            self.report('自動流程', '要讓 agent 填表或查回音,但 agent 專用的 Chrome 沒連接,自動的這幾步先停著',
                        need='到「⚙ 設定 → 🤖 Agent 與瀏覽器」按「連接」')
        if pl['seen'] and self.build and self.is_real():
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
