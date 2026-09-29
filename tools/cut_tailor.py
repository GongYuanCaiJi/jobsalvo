#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cut_tailor —— 跑準備區:一隻 agent 直接讀「正在準備」每張卡的 JD,判這張用哪份履歷、哪個語言。

程式先抓取職缺頁文字;只有直連 HTTP 404/410 會直接標記下架,其他狀態交由 agent 依原文判斷。
agent 每張寫一個 <resume.prepare_dir>/<jid>/fill.json(variant、lang,名字不對時 real_title,
對不上或關了就 skip+reason)。收尾把判斷寫進卡片、把有結果的卡推到「待你決定」,再跑 reconcile
建可投遞夾(見 ship.py:Markdown 原稿會先排成 PDF)。

fill.json 裡 worker 只擁有 WORKER_KEYS 那幾欄;其他欄位(例如自己的產線寫的客製內容、approved)
是別人寫的,跑之前備份、跑完原封不動放回去。

用法:python3 tools/cut_tailor.py --board <看板檔> [--limit N] [--scope prep|all]
"""
import sys,os,json,argparse,subprocess,uuid
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,HERE); import board_doc as bd
import agent_run as ar
import jobrun
import config as cf
import card
import prefs

jid = card.card_id_from_url
# 暫存狀態放哪:預設設定的 tmp。看板的副本(沙箱、測試)各自指一個地方,才不會蓋到真的那一份的進度。
SP=os.environ.get('CUT_TAILOR_TMP') or cf.TMP
os.makedirs(SP,exist_ok=True)
OUT=cf.PREPARE_DIR
PIDF=os.path.join(SP,'cut_tailor.pid')
WORKER_PIDF=os.path.join(SP,'cut_tailor_worker.pid')
RECONCILE=os.path.join(HERE,'reconcile.py')
ROWSF=os.path.join(SP,'cut_tailor_rows.json')
CHOICESF=os.path.join(SP,'cut_tailor_choices.json')
CACHEDF=os.path.join(SP,'cut_tailor_cached.json')
STATUSF=os.path.join(SP,'cut_tailor_status.json')
RUNS=os.path.join(SP,'cut_tailor_runs')


NAMES=('cut_tailor.py','job_fake.py')   # 看板副本上跑的是 job_fake(見那支)

def _is_ours(pid):
    return jobrun.is_ours(pid,NAMES)


def stop_previous():
    """上一個 worker 還活著就先收掉。

    worker 是脫離 session 的,不跟著這支結束。同時跑兩個會一起寫準備區的輸出夾,後寫的
    蓋掉先寫的,而且從外面看不出來:檔案有、時間新、內容是舊 prompt 產的。"""
    try:
        with open(PIDF) as f: pid=int(f.read().strip())
    except Exception:
        return
    if not _is_ours(pid):
        return   # 已經結束了,或這個 pid 已經是別的程式在用
    print(f'上一個 worker(pid {pid})還在跑,先收掉,免得兩個一起寫同一個目錄。')
    import signal,time
    pids=jobrun.tree(pid)
    for child in pids:
        try: os.kill(child,signal.SIGTERM)
        except OSError: pass
    for _ in range(20):
        alive=[]
        for child in pids:
            try: os.kill(child,0); alive.append(child)
            except OSError: pass
        if not alive: return
        time.sleep(0.25)
    for child in alive:
        try: os.kill(child,signal.SIGKILL)
        except OSError: pass





def _techerr(ff, u):
    """把一張卡標成「技術錯誤」(看板本來就有那一頁,不另外開地方寫原因)。
    技術錯誤跟喜歡/還好是同一個欄位,直接寫就把他原本的標記蓋掉了,先另存到 s0。"""
    e = ff.setdefault(u, {})
    if e.get('s') and e['s'] != 'techerr': e['s0'] = e['s']
    e['s'] = 'techerr'; e.pop('app', None)


def jd_verdict(u, board_title='', fetched=None):
    """回傳頁面擷取結果;HTTP 404/410 或官方資料端點明確下架時由程式標記。"""
    if fetched is None:
        import page_fetch
        fetched = page_fetch.fetch(u)
    return ('dead' if fetched.status == 'closed' else fetched.status), fetched





PREV=os.path.join(SP,'cut_tailor_prev')
KEEPF=os.path.join(PREV,'kept.json')

# fill.json 裡 worker 這一輪只擁有這幾欄。其他欄位是別人寫的(自己的產線寫的客製內容、approved…),
# 跑之前備份、跑完原封不動放回去:防覆蓋不能靠誰記得標 approved。
WORKER_KEYS=('resume','variant','lang','why','content_problem','real_title','skip','reason')

def keep_others(rows):
    """跑之前把 fill.json 裡不屬於 worker 的欄位備份一份,跑完放回去(見 restore_others)。"""
    os.makedirs(PREV,exist_ok=True); keep={}
    for u,_ in rows:
        f=os.path.join(OUT,card.card_id_from_url(u),'fill.json')
        try: d=json.load(open(f,encoding='utf-8'))
        except Exception: continue
        k={x:v for x,v in d.items() if x not in WORKER_KEYS and v not in (None,'',[],{})}
        if k: keep[card.card_id_from_url(u)]=k
    json.dump(keep,open(KEEPF,'w',encoding='utf-8'),ensure_ascii=False)
    if keep: print(f'{len(keep)} 張的 fill.json 有別人寫的欄位,先備份,跑完原封不動放回去')
    return keep


def restore_others(rows, out_dir=None):
    """把這一輪寫出的 fill.json 存回正式位置,並保留非 worker 欄位。"""
    out_dir=out_dir or OUT
    try: keep=json.load(open(KEEPF,encoding='utf-8'))
    except Exception: return 0
    n=0
    for u,_ in rows:
        jid=card.card_id_from_url(u)
        src=os.path.join(out_dir,jid,'fill.json')
        try: d=json.load(open(src,encoding='utf-8'))
        except Exception: continue
        if not isinstance(d,dict): continue
        if not d.get('skip'):
            for x,v in keep.get(jid,{}).items():
                if d.get(x)!=v: d[x]=v
        dst=os.path.join(OUT,jid,'fill.json')
        os.makedirs(os.path.dirname(dst),exist_ok=True)
        with open(dst,'w',encoding='utf-8') as fh:
            json.dump(d,fh,ensure_ascii=False,indent=2)
        n+=1
    if n: print(f'放回別人寫的欄位 {n} 張')
    return n


def check_written(rows,t0,out_dir=None):
    """每個 jid 都要有這一輪寫出來的檔。沒寫就是沒做,不能算跑完。"""
    out_dir=out_dir or OUT
    bad=[]
    for u,t in rows:
        f=os.path.join(out_dir,card.card_id_from_url(u),'fill.json')
        try:
            fresh=os.path.isfile(f) and os.path.getmtime(f)>t0
            with open(f,encoding='utf-8') as fh: data=json.load(fh)
            if not fresh or not isinstance(data,dict): bad.append((card.card_id_from_url(u),t))
        except Exception:
            bad.append((card.card_id_from_url(u),t))
    return bad


def row_line(u, t, verdict=None, note=''):
    """一個職缺在 prompt 裡的幾行,包含擷取狀態與收到的原文。"""
    v, fetched = verdict or ('', None)
    if fetched is not None and getattr(fetched, 'readable', False):
        check = f'可讀;路徑 {fetched.via}; HTTP {fetched.http_status or "未知"}'
        body = f'\n    JD 原文開始:\n{fetched.text}\n    JD 原文結束'
    elif v == 'unknown':
        check = (f'無法擷取;嘗試路徑 {getattr(fetched, "via", "") or "無"}; '
                 f'HTTP {getattr(fetched, "http_status", 0) or "無回應"}; '
                 f'原因 {"; ".join(getattr(fetched, "errors", ()) or ()) or "沒有可讀文字"}')
        body = '\n    沒有可讀職缺頁原文;不可猜測頁面內容'
    elif not verdict:
        check, body = '預覽未執行抓取', '\n    預覽中沒有職缺頁原文'
    else:
        check, body = '沒有可讀職缺頁原文', '\n    不可猜測頁面內容'
    return (f'  {card.card_id_from_url(u)}  看板上的名字:{t}\n'
            f'    來源URL:{u}\n    程式擷取:{check}{body}'
            + (f'\n    使用者對上一版的回饋(照著調整這一次的判斷):{note.strip()[:600]}'
               if (note or '').strip() else ''))


def resume_lines(resumes=None):
    """設定裡已勾選履歷的 id、名稱、適用說明與可用語言。"""
    return prefs.resume_choice_lines(resumes)


def notes_of(fb):
    """卡上「這份履歷哪裡不對」寫的話:{網址: 回饋}。"""
    return {u:v.get('rzfb') for u,v in (fb or {}).items() if isinstance(v,dict) and (v.get('rzfb') or '').strip()}


def _effective_pick(job, fb):
    resume = job.get('resume') or {}
    entry = fb.get(job.get('id')) or {}
    picked = entry.get('resume_id') or entry.get('variant')
    return (picked or resume.get('recommend'), entry.get('lang') or resume.get('lang'))


def _needs_reselection(job, fb, signature, resumes=None):
    resume = job.get('resume') or {}
    if not resume.get('selection_signature') or resume.get('selection_signature') != signature:
        return True
    note = (fb.get(job.get('id')) or {}).get('rzfb')
    if resume.get('feedback_signature') != prefs.feedback_signature(note):
        return True
    if prefs.checked_resumes(resumes):
        if not str(resume.get('pick_why') or '').strip():
            return True
        resume_id, lang = _effective_pick(job, fb)
        if not prefs.valid_resume_pick(resume_id, lang, resumes):
            return True
    return False


def _promote_cached(rows, board):
    urls = {u for u, _ in rows}
    moved = set()

    def mark(fb):
        for u in urls:
            entry = fb.get(u)
            if isinstance(entry, dict) and entry.get('app') == 'prep':
                entry['app'] = 'ready'
                moved.add(u)

    bd.set_fb(mark, live=board, by='cut_tailor:沿用有效履歷選擇')
    if moved:
        def clear_note(data, fb):
            for job in data['jobs']:
                if job.get('id') in moved:
                    job.pop('prep_note', None)
        bd.set_data(clear_note, live=board)
    return len(moved)


def prompt(rows, verdicts=None, notes=None, out_dir=None, resumes=None):
    """判斷履歷與語言;agent 只能使用程式附上的職缺頁文字。"""
    out_dir = out_dir or OUT
    jobs = [row_line(u, t, (verdicts or {}).get(u), (notes or {}).get(u, '')) for u, t in rows]
    available = list(cf.RESUMES.values()) if resumes is None else list(resumes)
    available = [item for item in available if item.get('enabled', True)]
    langs = '、'.join(cf.LANGS)
    if available:
        choices = ('使用者勾選的履歷(只從這些選;各份檔案程式會交給你自己讀):\n'
                   + resume_lines(available) + '\n\n'
                   '每個職缺挑一份有該語言檔的履歷,並說明為什麼。')
        selection_fields = (
            '  resume                上面履歷的 id\n'
            f'  lang                  JD 原文的語言,只能是 {langs} 其中一個\n'
            '  why                   一句話:為什麼挑這份履歷\n')
    else:
        choices = '目前沒有已勾選的履歷。這輪照常判斷職缺,不選履歷、不選語言。'
        selection_fields = ''
    return (
      choices + '\n\n'
      f'以下 {len(rows)} 個職缺,請依程式提供的 JD 原文判斷,不可開啟來源網址或使用瀏覽器:\n\n'
      + '\n'.join(jobs) + '\n\n'
      '只有程式直連來源頁得到 HTTP 404/410 時會自動跳過。其他狀態不能單獨證明職缺已關;'
      '請讀取提供的頁面文字,由你判斷職缺是否真的已關。若頁面文字未取得,不得猜測或標記已關;'
      '在 fill.json 留下 skip 與「抓不到 JD:」開頭的理由。\n'
      '每一個都先判斷:網址那一頁跟「看板上的名字」是不是同一個職缺(名字可能是舊的、寫法不同、中英不同)。\n'
      '  · 同一個缺、只是看板上的名字不對或過時:照做,並在 fill.json 多寫 "real_title": "照頁面寫的正確名字"。\n'
      '  · 網址指到的是別的職缺,或職缺已經關了:把 {"skip": true, "reason": "…"} 寫進那個 jid 的 fill.json,\n'
      '    reason 開頭寫「網址指到的是別的職缺:」或「職缺已關:」,講清楚你看到什麼。\n'
      '    寫「職缺已關:」的,程式會把卡標下架、移出準備區。\n'
      '真的讀不到 JD,也寫 skip,reason 開頭寫「抓不到 JD:」。其他跳過的卡會留在使用者的準備區,\n'
      '原因原封不動顯示在卡上,所以理由要寫他看得懂的一句話。\n\n'
      '只有在使用者回饋指出履歷內容本身需要修改、重挑版本與語言解決不了時,content_problem 才填 true;這表示要開客製。沒有這種回饋填 false。\n\n'
      '不要去找、也不要讀任何既有的 fill.json 或舊產出。每個職缺都從 JD 重新判一次。\n'
      '每個職缺都要處理並寫出 fill.json;真的對不上/已關/讀不到就寫 skip+理由。\n\n'
      f'每個職缺寫一個檔 {out_dir}/<JID>/fill.json:\n'
      + selection_fields +
      '  content_problem      true/false;有履歷內容問題且重挑無法解決時填 true,其他填 false\n'
      '  real_title            (只有看板上的名字不對時才寫)\n\n'
      '寫一個存一個。全部做完 stdout 只印 @@ROUND_DONE@@。\n')


def preview(live=None, model='main'):
    """組出「按下『▶ 跑準備區』現在會送給 agent 的那一份 prompt」。不跑閘門、不抓網路、不派 agent。
    跟真的跑同一支 prompt();差別只在這裡不先驗職缺還在不在(那一步要連網)。"""
    live = live or bd.LIVE
    with open(live, encoding='utf-8') as f: d = bd.parse(f.read())
    fb = json.loads(d['fb']); jobs = d['data'].get('jobs', [])
    rows = [(j['id'], j.get('target') or '') for j in jobs
            if isinstance(fb.get(j['id']), dict) and fb[j['id']].get('app') == 'prep']
    return ar.rules_for(model, board=live) + prompt(rows, notes=notes_of(fb))


def _status(d, sp=None):
    """進度狀態(格式見 jobrun):start → fetching_pages → agent(判斷)→ reconcile(建包)→ done。
    看板上的「跑準備區」就是讀這個。"""
    jobrun.write(os.path.join(sp,'cut_tailor_status.json') if sp else STATUSF, d)

def progress(sp=None):
    """現在跑到哪。看板伺服器每次輪詢都問這裡,不自己猜。"""
    sp=sp or SP
    st=jobrun.read(os.path.join(sp,'cut_tailor_status.json'),NAMES)
    if st.get('phase')=='agent' and 'done' not in st:
        try:
            with open(os.path.join(sp,'cut_tailor_rows.json'),encoding='utf-8') as f: rows=json.load(f)
            st['done']=len(rows)-len(check_written([tuple(r) for r in rows],float(st.get('t0') or 0),st.get('out_dir')))
        except Exception: pass  # noqa: S110
    return st


_CHECK=[False]   # --check 只是驗上一輪,不動進度狀態

def _stop(msg):
    """沒東西可跑:寫進狀態再結束,看板上按了鈕的人看得到為什麼沒跑。"""
    if not _CHECK[0]: _status({'phase':'nothing','msg':msg})
    sys.exit(msg)


def _fills(rows, out_dir=None):
    out_dir=out_dir or OUT; out={}
    for u,t in rows:
        try:
            with open(os.path.join(out_dir,card.card_id_from_url(u),'fill.json'),encoding='utf-8') as fh:
                data=json.load(fh)
            if isinstance(data,dict): out[u]=data
        except Exception: pass  # noqa: S110
    return out


def _apply_stages(rows, board, out_dir=None, selection_signature=None, feedback_signatures=None):
    """把這一輪的履歷判斷落到看板,只接受已勾選且有該語言檔的履歷。"""
    fills = _fills(rows, out_dir)
    for u, _ in rows:
        if u not in fills:
            fills[u] = {'skip': True, 'reason': '這一輪沒判到'}
    active = {rid: item for rid, item in cf.RESUMES.items() if item.get('enabled', True)}
    selection_signature = selection_signature or prefs.resume_selection_signature(active.values())
    feedback_signatures = feedback_signatures or {}

    def selected(f):
        return str(f.get('resume') or f.get('variant') or '').strip()

    def valid(f):
        rid = selected(f)
        lang = f.get('lang')
        item = active.get(rid)
        return bool(not f.get('skip') and item and
                    prefs.valid_resume_pick(rid, lang, active.values()))

    ok = {u: f for u, f in fills.items() if valid(f)}

    def mut(fb):
        for u, f in fills.items():
            e = fb.get(u)
            if not isinstance(e, dict):
                continue
            if f.get('skip') and str(f.get('reason', '')).startswith('職缺已關'):
                _techerr(fb, u)
                closed.add(u)
                moved['closed'] += 1
            elif u not in ok:
                moved['skipped'] += 1
            elif e.get('app') == 'prep':
                e['app'] = 'ready'
                moved['ready'] += 1

    moved = {'ready': 0, 'skipped': 0, 'closed': 0}
    closed = set()
    bd.set_fb(mut, live=board, by='cut_tailor 收尾')

    def card(data, fb):
        for j in data['jobs']:
            u = j.get('id')
            f = fills.get(u)
            if f is None:
                continue
            if u in closed:
                j['dead'] = True
            if f.get('skip'):
                j['prep_note'] = str(f.get('reason') or '這一輪沒有判斷履歷與語言').strip()[:200]
                continue
            rz = dict(j.get('resume') or {})
            for key in ('recommend', 'lang', 'pick_why'):
                rz.pop(key, None)
            rz.update(
                selection_signature=selection_signature,
                feedback_signature=feedback_signatures.get(
                    u, prefs.feedback_signature((fb.get(u) or {}).get('rzfb'))),
                content_problem=f.get('content_problem') is True,
            )
            if u in ok:
                j.pop('prep_note', None)
                rid = selected(f)
                lang = f['lang']
                rz.update(recommend=rid, lang=lang,
                          pick_why=str(f.get('why') or 'agent 沒有說明挑選這份履歷的理由').strip())
                j['resume'] = rz
            else:
                j['resume'] = rz
                rid = selected(f)
                item = active.get(rid)
                if not active:
                    why = '目前沒有已勾選的履歷,這輪只找職缺'
                elif rid and not item:
                    why = f'選的履歷 {rid!r} 沒有勾選'
                elif item and (not isinstance(f.get('lang'), str) or
                               f.get('lang') not in (item.get('files') or {})):
                    why = f'履歷「{item.get("name") or rid}」沒有 {f.get("lang") or "指定語言"} 的檔'
                elif item and not str(f.get('why') or '').strip():
                    why = 'agent 沒有說明挑選這份履歷的理由'
                else:
                    why = 'agent 沒有挑選可用的履歷與語言'
                j['prep_note'] = str(f.get('reason') or why).strip()[:200]

    bd.set_data(card, live=board)
    return moved


def apply_real_titles(rows, board, out_dir=None):
    """agent 判斷「同一個缺、只是看板上的名字不對」時寫的 real_title,改進卡片名字。
    只換名字那一段,後面的「（[Lever](網址)）」原樣留著;可投遞夾照名字取名,跟著改。回改了幾張。"""
    fix={u:str(f.get('real_title') or '').strip() for u,f in _fills(rows,out_dir).items()
         if str(f.get('real_title') or '').strip() and not f.get('skip')}
    moved=[]
    def mut(data, fb):
        for j in data['jobs']:
            if j.get('id') in fix:
                old=j.get('target') or ''
                new=card.with_name(old, fix[j['id']])
                if new!=old: j['target']=new; moved.append((j['id'],new))
    if fix: bd.set_data(mut, live=board)
    if moved and bd.is_live(board):
        import ship
        for u,new in moved: ship.rename(u, card.name(new))
    return len(moved)


def _report(msg, need, board):
    """要他知道的事寫進看板最上面的「📣 agent 回報」(見 agent_report)。"""
    try:
        import agent_report
        agent_report.report('跑準備區', msg, need=need, live=board)
    except Exception:  # noqa: S110
        pass


def run_finish(board, out_dir=None):
    """收尾 supervisor(脫離 session,由 main 派出來)。等 agent worker 跑完 → 驗每個 jid
    真的重寫過 → 判斷落到看板 → 跑 reconcile 建可投遞夾 → 寫狀態檔。按一下就做到底,不用人接手。"""
    import time
    t0=float(open(os.path.join(SP,'cut_tailor_t0')).read())
    rows=[tuple(x) for x in json.load(open(ROWSF,encoding='utf-8'))]
    try: choice_state=json.load(open(CHOICESF,encoding='utf-8'))
    except Exception: choice_state={}
    try: cached_urls=json.load(open(CACHEDF,encoding='utf-8'))
    except Exception: cached_urls=[]
    out_dir=out_dir or OUT
    p=open(f'{SP}/cut_tailor_prompt.txt',encoding='utf-8').read()
    of=f'{SP}/cut_tailor.out'
    me=os.getpid()
    # graceful:看板按停止時先收工,只停 agent,這裡把已經寫好的 fill.json 照常推進(見 jobrun.control)
    _status({'phase':'agent','pid':me,'t0':t0,'n':len(rows),'out_dir':out_dir,'graceful':True})
    def worker_started(proc):
        with open(WORKER_PIDF, 'w') as fh:
            fh.write(str(proc.pid))
    result=ar.run(p, of, cf.HOME, timeout=4*3600, browser_required=False, web=False,
                  board=board, on_start=worker_started)
    stopping=not result.ok and jobrun.finishing(STATUSF)
    if not result.ok and not stopping:
        msg=f'準備履歷 agent 沒完成:{result.message()}'
        _status({'phase':'failed','t0':t0,'n':len(rows),'worker_outcome':result.status,
                 'worker_rc':result.returncode,'finished_at':time.time(),'msg':msg})
        _report(msg,'在「準備履歷中」按「看紀錄」確認後再重跑',board)
        for path in (PIDF,WORKER_PIDF):
            try: os.remove(path)
            except OSError: pass
        return result
    restore_others(rows,out_dir)   # 只存這一輪真的寫出的檔,並保留非 worker 欄位
    bad=check_written(rows,t0,out_dir)
    # 有判斷 = 已準備:先把這輪剛判的推到 ready,再跑 reconcile。
    # 順序很重要:先推 ready,reconcile 建可投遞夾那步才會把這幾張一起建好。
    moved=_apply_stages(rows, board, out_dir,
                        selection_signature=choice_state.get('selection_signature'),
                        feedback_signatures=choice_state.get('feedback_signatures'))
    cached_ready=_promote_cached([(u, '') for u in cached_urls], board) if cached_urls else 0
    moved['cached_ready']=cached_ready
    moved['ready']+=cached_ready
    renamed=apply_real_titles(rows, board, out_dir)
    if renamed: print(f'照網址那一頁改正名字 {renamed} 張')
    if bad and not stopping:
        names='、'.join(t for _,t in bad[:5])
        msg=f'這一輪有 {len(bad)} 張沒判到,留在「正在準備」:{names}'
        _report(msg,'下次再準備履歷;這些卡這一輪不會推進',board)
    _status({'phase':'reconcile','pid':me,'t0':t0,'n':len(rows),'missing':[k for k,_ in bad],
             'out_dir':out_dir})
    rc=subprocess.run([sys.executable,RECONCILE,'--board',board],cwd=cf.HOME)
    if rc.returncode:
        msg=f'判斷已寫進看板,但要寄的檔案重建失敗(結束碼 {rc.returncode})'
        _report(msg,'在「準備履歷中」按「看紀錄」確認後重跑',board)
    elif bad:
        msg=f'這一輪完成:{moved["ready"]} 張已推進,{len(bad)} 張留在「正在準備」(這一輪沒判到)'
    else:
        msg=f'履歷準備完成:{moved["ready"]} 張已推進'
    if stopping and not rc.returncode:
        msg=f'你按了停止:{moved["ready"]} 張已推進,{len(bad)} 張留在「正在準備」,下次再跑'
        jobrun.clear_finish(STATUSF)
    phase='failed' if rc.returncode else ('stopped' if stopping else ('incomplete' if bad else 'done'))
    _status({'phase':phase,'t0':t0,'n':len(rows),
             'missing':[k for k,_ in bad],'worker_outcome':result.status,'worker_rc':result.returncode,
             'reconcile_rc':rc.returncode,'finished_at':time.time(),'msg':msg,**moved})
    for path in (PIDF,WORKER_PIDF):
        try: os.remove(path)
        except OSError: pass
    print('@@CUT_TAILOR_FINISHED@@')
    return result


def skip_approved(rows, out_dir=None):
    """已核准的 fill.json 是使用者的決定,不交給下一輪 worker 覆寫。"""
    out_dir = out_dir or OUT
    kept = []
    for u, title in rows:
        path = os.path.join(out_dir, card.card_id_from_url(u), 'fill.json')
        try:
            if json.load(open(path, encoding='utf-8')).get('approved'):
                print(f'  跳過(他認可過,不重跑):{title[:44]} {card.card_id_from_url(u)}')
                continue
        except Exception:  # noqa: S110
            pass
        kept.append((u, title))
    return kept


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--board',required=True); ap.add_argument('--limit',type=int,default=0)
    ap.add_argument('--scope',choices=['prep','all'],default='prep')
    ap.add_argument('--jids',default='',help='只跑這些 jid(逗號分隔),無視 scope/status——重生用')
    ap.add_argument('--check',action='store_true',help='不跑,只驗上一輪指定的 jid 是不是每個都真的重寫過')
    ap.add_argument('--finish',action='store_true',help='(內部)收尾 supervisor:等 worker 跑完→驗→跑 reconcile,不用手動下')
    ap.add_argument('--out-dir',default='',help=argparse.SUPPRESS)
    a=ap.parse_args()
    if a.finish:
        try:
            run_finish(a.board, a.out_dir or OUT)
        except Exception as e:
            if progress().get('phase')!='failed':
                import time
                _status({'phase':'failed','msg':f'收尾出錯:{e}','finished_at':time.time()})
                _report(f'跑準備區沒跑完:{e}', '可以再按一次;一直失敗就在「正在準備」按「看紀錄」', a.board)
            raise
        return 1 if progress().get('phase') in ('failed','incomplete') else 0
    _CHECK[0]=a.check
    if not a.check:
        _status({'phase':'start','pid':os.getpid()})
    d=bd.parse(open(a.board,encoding='utf-8').read()); FB=json.loads(d['fb'])
    only=set(x.strip() for x in a.jids.split(',') if x.strip())
    def keep(j):
        if not str(j.get('id','')).startswith('http'): return False
        if only: return card.card_id_from_url(j['id']) in only
        e=FB.get(j['id']); return a.scope=='all' or (isinstance(e,dict) and e.get('app')=='prep')
    def title(j): return card.name(j)
    rows=[(j['id'],title(j)) for j in d['data']['jobs'] if keep(j)]
    if a.limit: rows=rows[:a.limit]
    if not rows and not a.check:
        _stop('「正在準備」沒有卡' if not only else '指定的 jid 一個都不在看板上')
    # 不覆寫使用者已核准的內容。
    rows=skip_approved(rows)
    if not rows: _stop('要跑的都被標成他認可過了,沒有東西要跑。要重跑就先把 fill.json 的 approved 拿掉。')
    if a.check:
        try: t0=float(open(os.path.join(SP,'cut_tailor_t0')).read())
        except Exception: sys.exit('找不到上一輪的啟動時間,沒得驗')
        bad=check_written(rows,t0)
        if bad:
            print(f'❌ {len(bad)} 個沒重寫(檔案是上一輪留下的):')
            for k,t in bad: print(f'   {k} {t[:46]}')
            sys.exit(1)
        print(f'✅ {len(rows)} 個都是這一輪寫出來的'); return
    stop_previous()
    _status({'phase':'fetching_pages','pid':os.getpid(),'n':len(rows)})
    # 直連 404/410 由程式收掉;其他關閉語意由 agent 依附上的頁面原文判斷。
    import page_fetch   # 好幾個缺一起抓(page_fetch.fetch_many)
    verdicts={u:jd_verdict(u,t,f) for (u,t),f in zip(rows,page_fetch.fetch_many([u for u,_ in rows]))}
    closed=[(u,t) for u,t in rows if verdicts[u][0] in ('closed','dead')]
    if closed:
        cids = {u for u, _ in closed}
        # 寫入走 set_fb / set_data:在鎖裡重讀現行看板(檢查期間使用者可能在手機上標記),
        # 而且標記的改動會進流水帳。
        bd.set_fb(lambda ff:[_techerr(ff,u) for u in cids], live=a.board, by='cut_tailor 閘門:直連回 404/410')
        bd.set_data(lambda data, fb: [j.__setitem__('dead', True) for j in data['jobs'] if j.get('id') in cids],
                    live=a.board)
        print(f'閘門:{len(closed)} 個直連回 404/410 → 自動標死缺、不跑:')
        for u, t in closed: print(f'   ✗關  {t[:40]}  {card.card_id_from_url(u)}')
    rows = [(u,t) for u,t in rows if verdicts[u][0] not in ('closed','dead')]
    if not rows:
        _stop('所有職缺的來源頁都直連回 HTTP 404/410,沒有可跑的職缺。')
    notes = notes_of(FB)
    resumes = prefs.checked_resumes()
    signature = prefs.resume_selection_signature(resumes)
    jobs_by_id = {j.get('id'): j for j in d['data']['jobs']}
    reselect = [(u, t) for u, t in rows
                if _needs_reselection(jobs_by_id.get(u) or {}, FB, signature, resumes)]
    cached = []
    for u, t in rows:
        if (u, t) not in reselect:
            rid, lang = _effective_pick(jobs_by_id.get(u) or {}, FB)
            if prefs.valid_resume_pick(rid, lang, resumes):
                cached.append((u, t))
    feedback_signatures = {u: prefs.feedback_signature(notes.get(u, '')) for u, _ in reselect}
    with open(CHOICESF, 'w', encoding='utf-8') as f:
        json.dump({'selection_signature': signature,
                   'feedback_signatures': feedback_signatures}, f, ensure_ascii=False)
    with open(CACHEDF, 'w', encoding='utf-8') as f:
        json.dump([u for u, _ in cached], f, ensure_ascii=False)
    if not reselect:
        import time
        started = time.time()
        _status({'phase':'reconcile','pid':os.getpid(),'t0':started,'n':len(rows),'reselected':0})
        cached_ready = _promote_cached(cached, a.board) if cached else 0
        try:
            rc = subprocess.run([sys.executable, RECONCILE, '--board', a.board], cwd=cf.HOME)
            code = rc.returncode
        except Exception as e:
            code = 1
            _report(f'可投遞夾重建失敗:{e}', '確認錯誤後再跑準備區', a.board)
        if code:
            _report(f'可投遞夾重建失敗(結束碼 {code})', '在「正在準備」按「看紀錄」確認後重跑', a.board)
        msg = (f'履歷準備完成:{cached_ready} 張沿用有效選擇並重新整理要寄的檔案'
               if not code else f'可投遞夾重建失敗(結束碼 {code})')
        _status({'phase':'failed' if code else 'done','t0':started,'n':len(rows),
                 'reselected':0,'ready':cached_ready,'reconcile_rc':code,
                 'finished_at':time.time(),'msg':msg})
        return
    rows = reselect
    keep_others(rows)
    os.makedirs(OUT,exist_ok=True)
    run_out_dir=os.path.join(RUNS,uuid.uuid4().hex)
    os.makedirs(run_out_dir,exist_ok=True)
    p=prompt(rows,verdicts,notes,out_dir=run_out_dir,resumes=resumes); open(f'{SP}/cut_tailor_prompt.txt','w',encoding='utf-8').write(p)
    json.dump([[u,t] for u,t in rows],open(ROWSF,'w',encoding='utf-8'),ensure_ascii=False)
    print(f'準備履歷 {len(rows)} 個職缺 · 1 個 agent')
    for u,t in rows: print('  -',t[:40],card.card_id_from_url(u))
    of=f'{SP}/cut_tailor.out'; open(of,'w').close()   # 清掉上一輪輸出
    t0=__import__('time').time()
    open(os.path.join(SP,'cut_tailor_t0'),'w').write(str(t0))
    # 派一支脫離 session 的收尾 supervisor:它啟動 agent → 等跑完 → 驗過 → 自己跑 reconcile,
    # 把履歷/看板/可投遞包全部建好。以前這後半是人手動接的,現在按一下全自動。
    sup=subprocess.Popen([sys.executable,os.path.abspath(__file__),'--finish','--board',a.board,
                          '--out-dir',run_out_dir],
                         stdout=open(f'{SP}/cut_tailor_finish.out','w'),stderr=subprocess.STDOUT,
                         start_new_session=True)
    open(PIDF,'w').write(str(sup.pid))
    # 這支馬上就要結束;先把進度交給收尾那支的 pid,免得中間空檔被讀成「死在半路」
    _status({'phase':'agent','pid':sup.pid,'t0':t0,'n':len(rows),'out_dir':run_out_dir})
    print(f'worker + 自動收尾已啟動(脫離 session,pid {sup.pid})。')
    print(f'  跑完會自己建好履歷、更新看板 —— 你手機重整看板,卡片顯示「✅ 履歷已產出」就是好了。')
    print(f'  進度 {STATUSF};worker 輸出 {of}。')

if __name__=='__main__': sys.exit(main() or 0)
