#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_server —— 本機看板 server。狀態就是同一份 board HTML 檔(單一真相):
手機看、手機存、agent 的產線(cut_tailor、converge、apply_run…)都讀寫這一個檔。

  GET  /            → 服務現行看板(送出前注入設定裡的類別、標籤、可用履歷與附件及各自有哪些語言的檔與預覽、agent 名字)
  POST /api/save    → body 是 data-fb 的 JSON(使用者的標記);逐筆併回看板的 data-fb,存檔
  POST /api/merge   → 存檔撞到別處(409)之後:{keys, base, mine} 照現在的 data-fb 合併(merge_edit),只算不寫
  GET  /api/state   → 回傳目前 data-fb(給 agent/loop 讀狀態用)
  GET  /api/rev     → 小包:標記版本、職缺資料版本、背景建包中、兩個「跑」的進度(頁面輪詢用)
  POST /api/run/prep      → 跑準備區(cut_tailor)
  POST /api/run/research  → 找新職缺 {mode: deep|wide|dir, text: 指定方向}(converge)
                            這些只有使用者按得到;看板副本上跑假的,見 job_fake.py

用法:python3 tools/board_server.py [--port 8899] [--state <board html>] [--host <ip>]
預設綁兩個:127.0.0.1(純本機迴環,本機瀏覽器走 http://localhost:<port>)＋ Tailscale IP
(自己的 tailnet 裝置才連得到,手機走這條)。抓不到 Tailscale IP 就只剩 localhost。
絕不綁 0.0.0.0:那會連同 Wi-Fi/LAN 的陌生人都能讀到你的履歷和求職資料。
"""
import sys,os,re,json,argparse,threading,subprocess,shutil,time,signal,html,contextlib,copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as _HTTPServer
import socketserver


class ThreadingHTTPServer(_HTTPServer):
    """內建的 HTTPServer 綁好埠之後會用 IP 反查電腦名稱(socket.getfqdn),那台 Mac 的 DNS/mDNS 怪一點就卡很久,
    看板一直起不來、也沒有錯誤訊息(乾淨的 Mac 上實際卡住過)。看板只開在指定的 IP 上,用不到那個名稱:跳過。"""
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,HERE)
import board_doc as bd
import config as cf
import card
REPO=cf.HOME

LOCK=threading.Lock()
STATE=None  # board html 檔路徑
RUNNING_VERSION=''
AGENT_UA='Claude/'      # AI 助理的瀏覽器(例如 Claude)會在 UA 帶這段,使用者自己的裝置不會
ALLOW_AGENT=[False]     # 只有沙箱板開這個


def code_version(root=None):
    """Content version for the running service, covering server and browser code."""
    root = os.path.abspath(root or os.path.dirname(HERE))
    digest = _hl.sha256()
    for base in ('tools', 'board'):
        directory = os.path.join(root, base)
        if not os.path.isdir(directory):
            continue
        for current, dirs, files in os.walk(directory):
            dirs[:] = sorted(d for d in dirs if d != '__pycache__')
            for name in sorted(files):
                path = os.path.join(current, name)
                if name.endswith(('.pyc', '.pyo')):
                    continue
                rel = os.path.relpath(path, root).replace(os.sep, '/')
                digest.update(rel.encode('utf-8') + b'\0')
                try:
                    with open(path, 'rb') as source:
                        for chunk in iter(lambda: source.read(65536), b''):
                            digest.update(chunk)
                except OSError:
                    digest.update(b'MISSING')
    return digest.hexdigest()[:16]


def _watch_code(servers, version, interval=10):
    while True:
        time.sleep(interval)
        if code_version() == version:
            continue
        for server in servers:
            threading.Thread(target=server.shutdown, daemon=True).start()
        return

SERVERS=[]   # main 綁好的伺服器;要讓出埠時(交給開機自動啟動)從這裡停

def hand_over_to_launchd(delay=1.0):
    """開機自動啟動裝好了,這個看板卻不是 launchd 起的(安裝指令在背景起的,沒有終端機可以按 Ctrl-C):
    回完話就自己關,讓出埠給 launchd 起的那個(它每 30 秒重試一次)。關法跟程式更新後一樣(_watch_code):
    停掉伺服器,main 的收尾照常跑。安裝指令記的 pid 是自己的就一起清掉。"""
    def go():
        pidfile=os.path.join(cf.HOME,'.jobsalvo-server.pid')
        try:
            with open(pidfile,encoding='utf-8') as f: mine=f.read().strip()==str(os.getpid())
            if mine: os.remove(pidfile)
        except OSError:
            pass
        for server in SERVERS:
            threading.Thread(target=server.shutdown,daemon=True).start()
    t=threading.Timer(delay,go); t.daemon=True; t.start()
    return t

import gzip as _gz, hashlib as _hl
_GZ={}
def _gzip(b):
    k=_hl.md5(b).digest()
    z=_GZ.get(k)
    if z is None:
        z=_gz.compress(b,6)
        if len(_GZ)>16: _GZ.clear()   # 只留最近幾份,檔案一改內容就換
        _GZ[k]=z
    return z

def read_doc():
    """讀看板檔(對過指紋:被繞過正常寫入改過就丟 bd.Tampered,請求那一頭回 423 講原因)。"""
    return bd.read_doc(STATE)

def page_cfg():
    """頁面要知道、但屬於設定不屬於資料的東西。每次送出時從設定現讀,不存進看板檔。"""
    import agent_report, autopilot, card
    C = cf.C
    # 每張卡寄哪幾份、有沒有預覽、客製版還用不用,後台算好放在 ship_files(ship_files_of),頁面不自己判斷;
    # 這裡只給名字(統計表、設定頁摘要用)
    resume_rows = [{'id': item['id'], 'name': item.get('name') or item['id'], 'when': item.get('when', '')}
                   for item in C['resume'].get('resumes', []) if item.get('enabled', True)]
    return {'agent': cf.AGENT, 'langs': cf.LANGS,
            'resumes': resume_rows,
            'categories': C['board']['categories'], 'tags': C['board']['tags'],
            'company_alias': {str(k).lower(): v for k, v in (C['board'].get('company_alias') or {}).items()},
            'title_words': card.title_words(C['board'].get('title_words')),
            'read_lang': C['resume'].get('read_lang') or 'zh',
            'flow': autopilot.flow(),
            'find_minutes': (C.get('search') or {}).get('find_minutes') or 0,
            # 查應徵進度沒跑成當天自動再試幾次、隔多久(autopilot 照這組跑,看板照這組寫);回報來源怎麼叫、去哪一頁
            'reply_retry': {'max': autopilot.REPLY_RETRY_MAX, 'gap': autopilot.REPLY_RETRY_GAP},
            'inbox_from': agent_report.FROM}

def ship_files_of(jobs, fb, only=None):
    """每張卡的要寄的檔案(ship.card_files):頁面載入、輪詢、按了按鈕問的都是這一份,看板不自己挑。
    一張卡算不出來(舊資料格式壞了)只影響那一張,整頁照常。"""
    import ship
    out = {}
    for j in jobs or []:
        if not isinstance(j, dict) or not j.get('id') or (only is not None and j['id'] != only):
            continue
        try:
            out[j['id']] = ship.card_files(j, fb)
        except Exception as e:  # noqa: BLE001 — 算不出來的原因照實放進這張卡的 problem,看板卡上顯示
            out[j['id']] = {'resume_id': '', 'lang': '', 'problem': f'這張卡的要寄的檔案算不出來:{str(e)[:120]}',
                            'files': [], 'choices': [], 'langs': list(cf.LANGS), 'pending': '', 'stale_ids': []}
    return out


def next_steps(doc, only=None):
    """每張卡的下一步(next_step.of),加上常用答案的(__ans__,next_step.answers):頁面載入、輪詢、存檔回來的都是這一份,
    看板照它畫。only:只要這幾張。
    設定跟自動流程同一份(autopilot.flow);停著的頁上限要數整份看板,所以先算全部再挑。"""
    import autopilot, next_step
    fb=json.loads(doc['fb'] or '{}')
    with _BUILD_LOCK: gen,building=_build_state['gen'],_build_state['running']
    out=next_step.of(fb,doc['data'].get('jobs') or [],doc['data'].get('status'),flow=autopilot.flow(),real=is_real(),
                     gen=gen,building=building)
    if only is not None:
        out={k:v for k,v in out.items() if k in only}
    out['__ans__']=next_step.answers(fb)     # 常用答案的:哪幾條按刪除是清掉(還沒送出的表單在用)、等不等你
    import agent_report
    out['__inbox__']={'todo':[it.get('id') for it in agent_report.todo(fb)]}   # 回報裡要你處理的(缺證據的不算)
    return out


# 履歷預覽(建置步驟寫的 j.resume.variants[<語言>-<履歷>].html / pages)只有點「📄」才看得到,
# 卻佔了職缺資料的一大半(真實資料 101 張卡、1.6 MB)。送頁面時抽掉,留一個記號,點開才抓 /api/resume。
LAZY_VARIANT_KEYS=('html','pages')


def lean_jobs(jobs):
    """送給頁面的職缺:履歷預覽的大欄位換成記號(html_lazy / pages_n)。不改原本的 list。"""
    def lean_preview(value):
        value=dict(value)
        if value.get('html'): value['html_lazy']=1
        pages=value.get('pages')
        if isinstance(pages,list) and pages: value['pages_n']=len(pages)
        for k in LAZY_VARIANT_KEYS: value.pop(k,None)
        return value

    out=[]
    for j in jobs or []:
        rz=j.get('resume') if isinstance(j,dict) else None
        if not isinstance(rz,dict):
            out.append(j); continue
        vs=rz.get('variants')
        top=any(k in rz for k in LAZY_VARIANT_KEYS)
        nested=isinstance(vs,dict) and any(isinstance(v,dict) and any(k in v for k in LAZY_VARIANT_KEYS)
                                               for v in vs.values())
        if not top and not nested:
            out.append(j); continue
        lean=lean_preview(rz) if top else dict(rz)
        if isinstance(vs,dict):
            lean['variants']={key:lean_preview(v) if isinstance(v,dict) else v for key,v in vs.items()}
        j=dict(j); j['resume']=lean; out.append(j)
    return out


def page_jobs(jobs):
    """送給頁面的職缺:履歷預覽抽掉(lean_jobs),帶上後台算的卡名、公司名、來源平台(card.label_jobs),看板不自己算。"""
    board=cf.C.get('board') or {}
    return card.label_jobs(lean_jobs(jobs),board.get('company_alias'),board.get('title_words'))


def serve_doc(doc):
    """送出去的那份:注入設定(cfg),並把 masters(大張的預覽圖)與履歷預覽抽掉,
    改成真的展開那一區/點開那份履歷時才另外抓。檔案本身不動,只有這條 HTTP 回應不同。
    直接開檔(file://)的人拿到的是完整的檔,照舊看得到。"""
    try:
        d=bd.parse(doc)
        data=dict(d['data']); data['cfg']=page_cfg()
        import zhconv
        if zhconv.simplified(data['cfg'].get('read_lang')):
            data['cfg']['zh_tables']=zhconv.page_tables()   # 簡體:字典跟著頁面走,頁面自己把畫面上的字轉掉
        if data.get('masters'):
            data['masters_n']=len(data['masters']); data['masters']=[]
        data['ship_files']=ship_files_of(data.get('jobs'),json.loads(d['fb'] or '{}'))
        data['next']=next_steps(d)   # 每張卡的下一步:看板照它畫按鈕能不能按、為什麼不能
        data['jobs']=page_jobs(data.get('jobs'))
        # 頁面程式用程式碼裡現在的那份。看板檔裡存的外殼要等下一次重建材料才會換新:
        # 以前按「更新」、伺服器也重啟了,畫面卻一直是上次重建時的舊版(連「⬆ 更新」鈕都沒有)。
        shell=current_shell()
        if shell:
            css,js,hdr=shell
            return bd.assemble(css,bd.stat_first(hdr,d['data']),d['tail'],data,d['fb'],js)
        return bd.assemble(d['sty'],d['thdr'],d['tail'],data,d['fb'],d['app'])
    except Exception as e:  # noqa: BLE001 — 組不出來照樣送原檔(直接開檔也是這份),並把原因寫進看板的回報
        import agent_report
        agent_report.report('看板',f'看板頁面沒辦法照現在的程式組好,先給你存檔裡的那一份(按鈕可能少了或是舊的):'
                                  f'{type(e).__name__}: {str(e)[:120]}',need='重新整理看板;一直出現就重開看板伺服器',live=STATE)
        return doc

_SHELL={'key':None,'parts':None}
def current_shell():
    """程式碼裡現在的看板外殼(board.css/js、header.html),檔案沒變就用記著的那份;不像外殼就回 None(照舊用看板檔裡的)。"""
    import apply_shell as ash
    try:
        key=tuple(os.stat(p).st_mtime_ns for p in (ash.CSS,ash.JS,ash.HDR))
    except OSError:
        return None
    if _SHELL['key']!=key:
        _SHELL.update(key=key,parts=ash.current())
    return _SHELL['parts']

# rev(標記)與 gen(職缺資料)從檔案內容算,不是這支自己數。
# 會寫這個檔的不只這支:cut_tailor 收尾把卡推到「待你決定」、reconcile 裝新的職缺資料、
# converge 進新缺,全部直接寫檔。只數 /api/save 的話,開著的頁面會一直停在舊的,
# 在舊的上面一改還會被版本檢查擋下來。所以誰寫的都一樣:內容變了,數字就變。
_SIG_LOCK=threading.Lock()
_SIG={'key':None,'fb':'','data':''}
def content_sig():
    st=os.stat(STATE); key=(st.st_mtime_ns,st.st_size)
    with _SIG_LOCK:
        if _SIG['key']!=key:   # 只在檔案真的換過之後才重算,輪詢本身不讀 5MB
            doc=read_doc()
            i3,i4,i5=doc.find(bd.M3),doc.find(bd.M4),doc.find(bd.M5)
            _SIG['data']=_hl.md5(doc[i3:i4].encode('utf-8')).hexdigest()[:12]
            _SIG['fb']=_hl.md5(doc[i4:i5].encode('utf-8')).hexdigest()[:12]
            _SIG['key']=key
        return _SIG['fb'],_SIG['data']

def cur_gen():
    """職缺資料的版本。背景 reconcile 跑完也要換(可投遞包生好了,頁面該重拿),
    就算板子上的資料剛好沒變,所以後面接著建包次數。"""
    with _BUILD_LOCK: n=_build_state['gen']
    return '%s.%d'%(content_sig()[1],n)

# 比對標記值:跟頁面上的 lean() 同一套規則(空字串、空陣列、空物件、None 都算「沒有」),收在 delivery_state 一份。
# 兩邊一定要用同一套:以前頁面把清空的那筆記成 [] 或 {},伺服器這邊是整個 key 拿掉(None),比對起來永遠對不上。
# 結果「標喜歡 → 取消 → 再標還好」第三下被當成衝突丟掉,還跳一條「有 1 筆在別的裝置改過」騙他。
from delivery_state import same as _same  # noqa: E402

def _record_sent_version(fb, url, jobs):
    """看板送來的事件把卡帶到已送出(我已在外部送出、其實送出了):記下實際寄出的是哪一份
    (ship.record_sent;看板不自己寫,記過的不改)。"""
    import ship
    job=next((j for j in jobs if isinstance(j,dict) and j.get('id')==url),None)
    try:
        ship.record_sent(fb,url,job)
    except Exception as e:  # noqa: BLE001 — 記不下來照實寫進看板的回報(卡照樣算已送出),不擋他按的那一下
        import agent_report
        agent_report.apply_report(fb,'看板',f'記不下寄出的是哪一份:{str(e)[:120]}',
                                  need='這張照樣算已送出;要留紀錄就到可投遞夾看寄出的那一份',job=url)

def _manual_sent_problem(job, fb):
    """他按「我已在外部送出」(任何階段都能按):要記寄出的是哪一份履歷、哪個語言,他一定知道,所以還沒挑就擋下來請他挑。"""
    import ship
    if job and not ship.resolve(job,fb)[0]:
        return '先在卡上挑一份履歷和語言,再按「我已在外部送出」(要記寄出的是哪一份)'
    return None

def write_fb(fb_obj, base=None, events=None, rejected=None, out=None):
    """把新的 data-fb 併進 STATE 檔,其餘(jobs/sty/app)不動。單寫者鎖。

    逐筆合併,不是整包覆蓋。以前是整包蓋:任何一個停在舊狀態的分頁(手機擱著沒關、
    我開著沒重整)一自動存,就把整份標記倒回它載入時的樣子,別處的新改動全沒了。
    現在只吃這次真的帶了東西的那幾筆,其餘保留磁碟上的版本。清空一筆要送 null。

    投遞狀態不跟著卡片存:卡上歸狀態表管的那幾欄(delivery_state.OWNED、表單鎖、進出已送出)看板送來的不算數,
    看板只送事件(events:[{u, ev, data}] 或復原 [{u, undo:{prev, after}, else?}]),這裡照磁碟上現在的狀態套表;
    那一格不准的不做事(跟著那一下改的階段、心情也不收),原因放進 rejected 回給看板(狀態表修正 9)。
    表單只有後台寫:看板改答案要標重打就送 {refill: 答案鍵},在鎖內標在現在那份表單上(#308);
    清掉一條答案送 {redo: 答案鍵},清掉還是刪掉照整份看板算(form_record.redo)。
    out:收這一包動到的卡存好的樣子(cards)和復原用的(undo:{卡: {prev, after}},投遞那一部分前後,
    送回來 {u, undo} 就放回去):看板按下去不自己套狀態表,等這裡回話才照存好的畫(#343)。"""
    import delivery_state, next_step
    bad=[]
    # 按「✅ 確認送出」之前:程式自己讀那一頁,跟驗收時核對過的樣子比;變了就不讓他確認,卡上寫哪一格從什麼變成什麼和下一步(#316)。
    # 讀頁很慢(Codex 幾秒),在拿鎖之前做。副本(沙箱、測試、看板檢查)沒有真的 agent 的 Chrome,不讀
    changed={}
    if events and is_real():
        import apply_run
        for e in events:
            u=str((e or {}).get('u') or '') if isinstance(e,dict) else ''
            if u and not u.startswith('__') and e.get('ev')=='confirm':
                problems=apply_run.confirm_check(u,STATE)
                if problems: changed[u]=problems
    def put(d):
        cur=d['fb']
        # 樂觀鎖:這個分頁是照「它載入時看到的值」在改的。如果磁碟上那一筆已經不是那個值,
        # 代表別的裝置(另一支手機、另一個分頁)先改過了,這次就整批不寫,讓它重新拿再改。
        # 這是把 artifact 那套搬過來:publish 不加 force,底下版本比較新就直接擋下來。
        # 全有或全無,不做一半——一半寫進去他無從分辨哪幾筆生效了。狀態表管的那幾欄不比(那一部分看事件)。
        if base:
            bad.extend(k for k,want in base.items() if not (_same(cur.get(k),want) if k.startswith('__') else
                                                               _same(delivery_state.plain(cur.get(k)),delivery_state.plain(want))))
            if bad: return bd.SKIP
        refused=set()
        redo=[]
        evented={str(e.get('u') or '') for e in events or [] if isinstance(e,dict)}
        mine=[k for k in evented|set(fb_obj) if k and not k.startswith('__')]
        was={k:delivery_state.part(cur.get(k)) for k in mine}
        jobs={j.get('id'):j for j in d['data'].get('jobs') or [] if isinstance(j,dict)}
        for e in events or []:
            u=str((e or {}).get('u') or '')
            if isinstance(e,dict) and 'refill' in e and not u:
                redo.append((False,str(e['refill'])))   # 答案改了:等這一包的常用答案收完再標(見下面)。看板不送整份表單
                continue
            if isinstance(e,dict) and 'redo' in e and not u:
                redo.append((True,str(e['redo'])))     # 清掉一條答案:等這一包的常用答案收完再清(見下面)
                continue
            try:
                if not u or u.startswith('__'):
                    raise ValueError('沒有指定是哪一張')
                ev=str(e.get('ev') or '')
                data=e.get('data') if isinstance(e.get('data'),dict) else {}
                # 收不收照這張卡的下一步(#341):擋的原因就是卡上灰掉的按鈕寫的那一句,繞過畫面直接送也一樣
                # 復原只看它帶的退路(換檔的復原 = 又換了一次檔);復原本身准不准由 delivery_state.undo 判
                fallback=e.get('else') if isinstance(e.get('else'),dict) else {}
                fev=str(fallback.get('ev') or '')
                why=((next_step.refuse(cur,u,fev) if fev else None) if 'undo' in e
                     else next_step.refuse(cur,u,ev,d['data'].get('status')))
                if not why and 'undo' not in e and ev=='sent_manual':
                    why=_manual_sent_problem(jobs.get(u),cur)
                if why:
                    raise ValueError(why)
                if 'undo' in e:
                    delivery_state.undo(cur,u,(e['undo'] or {}).get('prev'),(e['undo'] or {}).get('after'),e.get('else'))
                elif ev=='confirm' and u in changed:
                    import apply_run
                    apply_run.page_changed(cur,u,changed[u],'確認前')     # 停著等你 → 填了卡住,原因和下一步寫在卡上
                    raise ValueError('確認前'+apply_run.PAGE_CHANGED+changed[u][0])
                else:
                    if ev=='confirm':
                        # 確認時記下的答案由後台當下算(#340 user story 9):頁面送來的快照不算數,只拿它按下去的時間
                        import form_record
                        data={'approve':form_record.approval(cur,u,(data.get('approve') or {}).get('at'))}
                    if ev=='not_sent':
                        data=dict(data,issues=[delivery_state.GONE])   # 頁已經不在的話卡上寫這一句(看板不抄)
                    if ev=='actually_sent' and 'evidence' not in data:
                        # 他查到其實送出了:證據就是 agent 當時那一份(狀態表這一格先清掉它,所以先抄)
                        data=dict(data,evidence=dict(((cur.get(u) or {}).get('apply') or {}).get('submit_fail') or {},
                                                     you_sent=data.get('at')))
                    delivery_state.fire(cur,u,ev,**data)
                    if e.get('ev')=='not_sent':
                        # 他查過了、確認沒送出:這張跟送出有關的回報(送出沒確認成功、去信箱查)一起收掉(#316)
                        import agent_report, apply_run
                        agent_report.apply_resolve(cur,u,'你確認沒送出',only=lambda it: it.get('from')==apply_run.REPORT_FROM)
                if delivery_state.state(cur.get(u))=='sent': _record_sent_version(cur,u,d['data'].get('jobs') or [])
            except (ValueError,TypeError,KeyError) as x:
                refused.add(u)
                if rejected is not None: rejected.append({'u':u,'ev':(e or {}).get('ev') or 'undo','msg':str(x)[:200]})
        # 事件先套(復原要比的是這一包送來之前的樣子),再收這一包裡卡片的其他欄位。
        # 那一張的事件被擋下:他按的那一下沒發生,跟著那一下改的欄位(退回、移除、出錯了、👎 改的階段和心情)也不收,
        # 不然會拼出「正在送出卻在待你決定」這種狀態表不准存在的組合;同一包裡的筆記照收
        for k,v in fb_obj.items():
            if not k.startswith('__'):
                # 沒帶事件、直接存:忙的卡也不准跟著存檔換檔或離開流程(#341,繞過畫面一樣擋)
                why=None if k in refused else next_step.refuse_save(cur.get(k),v)
                if why:
                    refused.add(k)
                    if rejected is not None: rejected.append({'u':k,'ev':'save','msg':why})
                if k in refused:
                    if v is None: continue          # 整張清掉也是跟著那一下的:不做
                    v=delivery_state.keep_flow(cur.get(k),v)
                v=delivery_state.merge_saved(cur.get(k),v)
            # 換了履歷、語言、自己的檔,這一包又沒帶這張的事件:後台自己看要寄的檔案真的變了沒,變了就送「換檔」
            # (填好的頁上傳的是舊檔,#341)。看板自己送的事件照收,它寫的原因比較清楚
            job=jobs.get(k) if k not in refused and k not in evented and isinstance(v,dict) and any(
                (cur.get(k) or {}).get(f)!=v.get(f) for f in delivery_state.SWAP_FIELDS) else None
            before=_doc_sig(job,cur) if job else None
            old=cur.get(k) if isinstance(cur.get(k),dict) else {}
            if v is None:
                old_apply=old.get('apply')
                delivery_state.queue_workspace(cur,old_apply.get('workspace') if isinstance(old_apply,dict) else None,'card_removed')
                cur.pop(k,None)
            else: cur[k]=v
            if job and _doc_sig(job,cur)!=before:
                delivery_state.try_fire(cur,k,'files_changed',why=_swap_why(old,v))
        # 改了、清掉答案:清掉還是刪掉、哪幾張表單要重打、哪幾張確認作廢,照整份看板算(form_record.redo、changed):
        # 不在看板上畫得出來的卡也算。放在最後:同一包帶來的常用答案(這個分頁手上的)不會把清掉蓋回去,確認快照比的是新答案
        import form_record
        hit=set()
        for clear,k in redo:
            hit|=set(form_record.users(cur,k))
            (form_record.redo if clear else form_record.changed)(cur,k)
        if out is not None:
            out['cards']={k:copy.deepcopy(cur.get(k)) for k in set(mine)|hit}   # 改答案動到的表單也一起回
            out['undo']={k:{'prev':was[k],'after':delivery_state.part(cur.get(k))} for k in mine
                         if not _same(was[k],delivery_state.part(cur.get(k)))}
    with LOCK:
        if bd.rewrite(put,STATE,by='看板') is bd.SKIP:
            return bad
    note_saved()
    import chrome_door
    chrome_door.close_if_idle(STATE)
    return []


# 陣列裡每一條有穩定 id 的,照 id 一條一條合:常用答案的 k、回報的 id
MERGE_BY={'__ans__':'k','__inbox__':'id'}

def merge_edit(base, mine, theirs, key=None):
    """存檔撞到別的裝置(或 agent、程式)先改了同一筆(/api/save 回 409):兩邊都從 base 改起,各改各的格子都留下。
    同一格兩邊改成不一樣才算撞到,留這台的(他剛打的),撞到的格子放進 clash 讓看板給他換回那邊的。
    想法這種陣列整個當一格('*');key:照那一欄一條一條合,agent 新開的答案、程式剛寫的回報不會被這台整份蓋掉。
    兩邊都改了同一格、而且是物件(卡上的 judged_no 這種):再往下一層一欄一欄合,撞到的記成 a.b 這種路徑。
    「一樣」照 delivery_state.same(空的各種寫法都算一樣)。回 {value, clash}。"""
    def keyed(a):
        return a is None or (isinstance(a, list) and all(isinstance(x, dict) and x.get(key) is not None for x in a))
    if key and keyed(base) and keyed(mine) and keyed(theirs):
        B, M, T = ({x[key]: x for x in a or []} for a in (base, mine, theirs))
        out, hit = [], []
        for i in dict.fromkeys(x[key] for x in (theirs or []) + (mine or [])):
            if _same(M.get(i), B.get(i)):
                if i in T: out.append(T[i])
                continue
            if i in M: out.append(M[i])
            if not _same(T.get(i), B.get(i)) and not _same(T.get(i), M.get(i)): hit.append(i)
        return {'value': out, 'clash': hit}
    if not all(v is None or isinstance(v, dict) for v in (base, mine, theirs)):
        if _same(mine, base): return {'value': theirs, 'clash': []}
        return {'value': mine, 'clash': [] if _same(theirs, base) or _same(theirs, mine) else ['*']}
    b, m, t = base or {}, mine or {}, theirs or {}
    out, clash = {}, []
    for k in dict.fromkeys([*b, *m, *t]):
        if _same(m.get(k), b.get(k)):
            if k in t: out[k] = t[k]
        elif (isinstance(m.get(k), dict) and isinstance(t.get(k), dict) and (k not in b or isinstance(b[k], dict))
              and not _same(t.get(k), b.get(k)) and not _same(t.get(k), m.get(k))):
            sub = merge_edit(b.get(k) or {}, m[k], t[k])
            out[k] = sub['value']
            clash += [k if c == '*' else k + '.' + c for c in sub['clash']]
        else:
            if k in m: out[k] = m[k]
            if not _same(t.get(k), b.get(k)) and not _same(t.get(k), m.get(k)): clash.append(k)
    return {'value': out, 'clash': clash}


def _swap_why(old, new):
    """換檔的原因(卡上「上傳的是舊檔」那一句):換了哪一份履歷、哪個語言、自己的檔。"""
    if new.get('custom_file')!=old.get('custom_file'):
        return '換成你自己的檔' if new.get('custom_file') else '改回原始檔'
    if new.get('resume_id')!=old.get('resume_id') and new.get('resume_id'):
        return f'履歷換成「{cf.resume_name(new["resume_id"]) or new["resume_id"]}」'
    if new.get('lang')!=old.get('lang') and new.get('lang'):
        import profile_sync
        return f'語言換成「{profile_sync.LANG_WORDS.get(new["lang"],new["lang"])}」'
    return '要寄的檔案換過了'


def _doc_sig(job, fb):
    """這張卡現在要寄的是哪幾份(哪一份、實際寄的檔):換檔前後比一次。算不出來也是一種樣子,照樣比得出有沒有變。"""
    import ship
    try:
        return [(x.get('id'),x.get('effective_path')) for x in ship.documents(job,fb)]
    except Exception as e:  # noqa: BLE001 — 舊資料格式壞了:記成錯誤樣子,前後照樣比
        return 'err:'+type(e).__name__


def note_saved():
    """Refresh the one-way bank export and queue a local data-folder commit."""
    try:
        import interview_bank as ib
        import folder_history
        bank = bd.parse(read_doc())['data'].get('bank') or {}
        ib.export_file(bank, cf.HOME)
        folder_history.note_saved(cf.HOME)
    except Exception as exc:  # noqa: BLE001 — 存檔已經寫進去了;版本紀錄沒排上照實寫進看板的回報
        import agent_report
        msg=f'資料夾版本紀錄沒排上,這次的修改沒有存成一版:{str(exc)[:120]}'
        print('⚠ '+msg)
        agent_report.report('看板',msg,need='看板上的資料已經存好,只是沒存成一版;下次存檔會再試',live=STATE)


_BUILD_LOCK=threading.Lock()
_build_state={'running':False,'again':False,'gen':0}   # gen:每跑完一次 reconcile +1,給頁面判斷「卡片資料該重拿了」

def trigger_build():
    """有卡片被標成 ready/ship 就在背景跑一次 reconcile,把那張的可投遞包生出來。
    single-flight:同時只有一個 reconcile;跑的期間又有人標,就記著、跑完再補一次。
    這樣卡片一進『待你決定』之後的階段,可投遞夾自己生,不用等誰手動建。
    reconcile 冪等且看雜湊:附件/看板沒變的階段全跳過,實際只會跑到可投遞包那一步。

    只有真正的看板才建:reconcile 動的是他的 board-live.html 和可投遞包,不是沙箱那一份副本。
    以前在沙箱(shot.py、board_check、board_sandbox 起的副本)上存一次「可投遞」相關的改動,
    背景就真的跑一輪 reconcile,CPU 被吃滿,頁面一直輪詢等一個跟這份副本無關的建置。"""
    if os.path.abspath(STATE)!=os.path.abspath(bd.LIVE): return
    with _BUILD_LOCK:
        if _build_state['running']:
            _build_state['again']=True; return
        _build_state['running']=True
    def _run():
        try:
            import reconcile
            while True:
                result=subprocess.run([sys.executable,os.path.join(HERE,'reconcile.py')],cwd=REPO,
                                      stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                # 只有幾張卡的要寄的檔案沒建成也算跑完:驗收結果已寫進看板,那幾張各自被擋。
                # 以前一張卡建不成,輪數就不前進,所有卡的自動推進都停住
                done=result.returncode in (0,reconcile.PACKAGE_PROBLEMS)
                with _BUILD_LOCK:
                    if done:
                        _build_state['gen']+=1
                    if not _build_state['again']:
                        _build_state['running']=False
                        if done and PILOT: PILOT.kick()  # 驗收跑完才可能推進
                        return
                    _build_state['again']=False
        except Exception as e:  # noqa: BLE001 — 背景執行緒最外層:建置沒跑起來照實寫進看板的回報,不然頁面只看到建置停了
            import agent_report
            try:
                agent_report.report('看板',f'可投遞夾背景建置沒跑起來:{type(e).__name__}: {str(e)[:120]}',
                                    need='標了可投遞的卡這次沒有生出可投遞夾;再存一次或重開看板會再建',live=STATE)
            finally:
                with _BUILD_LOCK: _build_state['running']=False
    threading.Thread(target=_run,daemon=True).start()


def trigger_source_sync():
    """Opening the live board, or coming back to its tab, is a synchronization point, not a background watch.
    他改完母稿切回看板那一下就在背景開始建;以前要等他按「讓 agent 填表」才在按鈕裡同步建,一卡五分鐘。"""
    if not STATE or not bd.LIVE:
        return
    if os.path.abspath(STATE or '') != os.path.abspath(bd.LIVE or ''):
        return
    try:
        import reconcile, source_sync
        if source_sync.stale(reconcile.MANIFEST, board=STATE):
            trigger_build()
    except Exception:  # noqa: BLE001 — 打開看板順手的預先建置;按下要跑的那一刻會再檢查一次來源,出錯在那裡照實回報
        return


# 來源檔一直同步失敗(排版要的 Chrome 不見了、原稿壞了)時,自動流程每分鐘都會走到來源檢查。
# 同一批來源檔剛失敗過,就直接回上次的原因,不再同步重跑一整輪 reconcile(排版、連結全部再來一次)。
# 他改了來源檔(大小或修改時間變了)馬上再試;沒改的話隔 SOURCE_RETRY 秒再試一次(可能是環境修好了)。
# 失敗的原因 reconcile 已經寫進 📣 回報(同一句只記一則),這裡不再另外報。
SOURCE_RETRY=600
_source_fail={}

def _source_key():
    import source_sync
    out=[]
    for e in source_sync.files(STATE):
        for p in (e.get('path'),e.get('style_path')):
            if not p: continue
            try: st=os.stat(p); out.append((p,st.st_mtime_ns,st.st_size))
            except OSError: out.append((p,None,None))
    return repr(sorted(out,key=repr))

def _source_preflight():
    """Synchronize changed configured files before an agent uses them."""
    if not STATE or not bd.LIVE:
        return ''
    if os.path.abspath(STATE or '') != os.path.abspath(bd.LIVE or ''):
        return ''
    import reconcile, source_sync
    # 背景已經在建(切回看板那一下開始的):等它建完再比,不要同時再開一個 reconcile 做同一件事
    while True:
        with _BUILD_LOCK:
            if not _build_state['running']:
                break
        time.sleep(1)
    if not source_sync.stale(reconcile.MANIFEST, board=STATE):
        _source_fail.clear()
        return ''
    key=_source_key()
    last=dict(_source_fail)   # 另一條執行緒可能同時清掉它
    if last.get('key')==key and time.time()-last.get('t',0)<SOURCE_RETRY:
        return last['msg']
    result = subprocess.run([sys.executable, os.path.join(HERE, 'reconcile.py'),
                             '--board', STATE], cwd=REPO, capture_output=True, text=True)
    # 來源同步好了、只是有幾張卡的要寄的檔案沒建成(那幾張各自擋、各自回報)不算來源失敗,這一輪照跑
    if result.returncode not in (0, reconcile.PACKAGE_PROBLEMS):
        msg='來源檔更新失敗，請先查看「整理要寄的檔案」回報。'
        _source_fail.update(key=key,t=time.time(),
            msg=msg+f'({time.strftime("%H:%M",time.localtime(time.time()))} 試過;改了來源檔會馬上再試,沒改的話 {SOURCE_RETRY//60} 分鐘後再試)')
        return msg
    _source_fail.clear()
    return ''

# ---- 看板上的「跑」:跑準備區、找新職缺 ----
# 看板本身就是這台電腦上的伺服器,按鈕直接叫 cut_tailor / converge / apply_run / reply_run,跟背景建可投遞夾
# (trigger_build)同一個道理。發動的還是他:按鈕只有他按得到(agent 被擋)。
# 進度格式兩支共用(jobrun),伺服器只負責啟動、單飛、把進度交給頁面。
RUNS={
 'prep':    {'script':'cut_tailor.py','env':'CUT_TAILOR_TMP','status':'cut_tailor_status.json','dir':'.prep'},
 'customize':{'script':'customize.py','env':'CUSTOMIZE_TMP','status':'customize_status.json','dir':'.customize'},
 'research':{'script':'converge.py',  'env':'CONVERGE_TMP',  'status':'converge_status.json',  'dir':'.research'},
 # 代投:填表單(fill)、修改(fix)、送出(submit)共用一個 kind,單飛一起算:三段都是 agent 在他的 Chrome 裡操作,
 # 一次一隻,不會兩隻同時動他的瀏覽器。
 'apply':   {'script':'apply_run.py',  'env':'APPLY_TMP',     'status':'apply_status.json',     'dir':'.apply'},
 # 查回音(已投遞那一頁的「📬 查回音」):agent 在自己的 Chrome 查回音與各平台應徵紀錄,程式照證據改狀態。
 'replies': {'script':'reply_run.py',  'env':'REPLY_TMP',     'status':'reply_status.json',     'dir':'.replies'},
 # 貼網址加入(add_job):agent 讀職缺頁並寫卡片摘要,一律加進待評估。
 'add':     {'script':'add_job.py',    'env':'ADD_TMP',       'status':'add_status.json',       'dir':'.add'},
 # 「⚙ 設定 → 分類」讓 agent 建議類別與標籤(suggest_cats),建議寫在 suggest.json,套用要使用者按。
 'suggest': {'script':'suggest_cats.py','env':'SUGGEST_TMP',  'status':'suggest_status.json',   'dir':'.suggest'},
}
MODES=('deep','wide','dir')
_PROC={}
_RUN_LOCK=threading.Lock()
_PV_CACHE={}           # 「會送出去的 prompt」:同一份材料組過就記著(見 do_GET_prompt)
_PV_LOCK=threading.Lock()

def pv_sig():
    """記住的那份什麼時候該作廢。prompt 是「組它的程式」加「他的材料」一起算出來的,
    兩邊任何一邊變了都要重算。只看看板版本(gen)不夠:改了 apply_run.py 裡的 prompt,
    看板 gen 沒變,他打開看到的還是舊的那份——看起來像真的,其實不是真的會送出去的東西。
    """
    t=0.0
    try:
        for e in os.scandir(HERE):
            if e.name.endswith('.py'):
                t=max(t,e.stat().st_mtime)
    except OSError: pass
    for folder in (os.path.join(HERE, 'research_skills'), os.path.join(cf.HOME, 'custom', 'skills')):
        try:
            for e in os.scandir(folder):
                if e.is_file(): t=max(t,e.stat().st_mtime)
        except OSError: pass  # 沒有自訂 skill 資料夾時,仍可用產品預設。
    for f in (cf.PREFS, cf.PREFERENCE_NOTE, os.path.join(cf.HOME,'resume.md'),
              cf.APPLY_RULES, os.path.join(cf.HOME,cf.NAME)):
        with contextlib.suppress(OSError):   # 還沒建的檔不算
            t=max(t,os.path.getmtime(f))
    return round(t,3)

def is_real():
    return bd.is_live(STATE)

def first_run(state):
    """第一次在這個資料夾起看板:把設定、空看板、範本建好,不用另外跑 init。
    建好要重讀設定:這份資料夾自己的暫存和 agent Chrome 連線檔寫在剛建的設定裡,不重讀的話
    這個行程一直用全機共用的預設,設定頁一存就把共用的寫回設定檔。"""
    if os.path.exists(state):return
    if state!=os.path.abspath(cf.LIVE): sys.exit('找不到狀態檔:'+state)
    import init
    init.scaffold(cf.HOME)
    cf.reload(cf.HOME)

def migrate_marks(state):
    """伺服器起來時收尾,寫回檔案(記進流水帳):伺服器的填表、送出、自動流程讀的都是檔案。
    卡停在正在填、正在送出,那一輪卻已經不在跑(伺服器重開前被停掉、當掉):照狀態表收尾(apply_run.settle)。
    產生它的規則改了、造成它的 bug 修掉了的 agent 回報和還沒確認的答案:清掉(stale.sweep,GLOSSARY「過時」)。"""
    import copy, apply_run, stale
    with open(state,encoding='utf-8') as source: fb0=json.loads(bd.parse(source.read())['fb'])
    probe=copy.deepcopy(fb0)
    stuck=apply_run.settle(probe,_apply_busy())
    before=stale.boundaries()
    outdated=stale.sweep(probe,before)
    if not stuck and not outdated: return
    def mut(fb):
        apply_run.settle(fb,_apply_busy())
        stale.sweep(fb,before)
    # 回不了頭:一律走 folder_history.convert,先留退回點(存一版或備份看板檔),沒有退回點就不轉
    import folder_history
    why='、'.join(w for w,need in (('沒跑完的填表收尾',stuck),('過時的回報與答案',outdated)) if need)+'轉換'
    done=folder_history.convert(cf.HOME,[state],why,lambda: bd.set_fb(mut, live=state, by='board_server'))
    if not done['done']:
        print(f'⚠ {why}沒有做:沒有退回點({done["reason"]})')


def _apply_busy():
    """現在幫你填表那一輪在跑哪一張:None 沒在跑,'*' 整批在跑(不知道是哪一張)。"""
    st=run_status('apply')
    return (st.get('url') or '*') if st.get('running') else None

def sent_files():
    """已經填過、還在等的卡,現在要寄的檔案(哪一份、檔案內容的指紋):換檔之前先記,換完再比。"""
    import delivery_state, ship
    doc=bd.parse(read_doc()); fb=json.loads(doc['fb']); out={}
    for j in doc['data'].get('jobs') or []:
        if delivery_state.state(fb.get(j.get('id'))) not in delivery_state.FILES_MATTER: continue   # 換檔才有意義的狀態
        try:
            docs=ship.documents(j,fb)
        except Exception as e:  # noqa: BLE001 — 算不出來記成錯誤指紋:換檔前後照樣比得出有沒有變,變了卡上會標檔案換了
            docs=[{'id':'?','effective_path':'err:'+type(e).__name__}]
        out[j['id']]=[(d.get('id'),d.get('effective_path'),
                       ship._digest(d['effective_path']) if d.get('effective_path') and os.path.isfile(d['effective_path']) else None)
                      for d in docs]
    return out

def files_changed(before,why):
    """換檔之後(改原始履歷、設定裡刪語言或改附件…):要寄的檔案真的變了的那幾張才送「換檔」事件(狀態表修正 6、16)。"""
    import delivery_state
    after=sent_files()
    changed=[u for u,v in before.items() if u in after and after[u]!=v]
    if changed:
        bd.set_fb(lambda fb: [delivery_state.try_fire(fb,u,'files_changed',why=why) for u in changed],
                  live=STATE, by='board_server')
    return changed


def page_gone(u):
    """👀 截不到那一頁時:那張卡的工作區或頁已經不在 ego 裡,就把它改標成「頁面不見了,要重填」
    (自動流程每分鐘也會做同一件事)。ego 一時連不上這種不確定的情況不動。
    副本不看:ego 整台電腦只有一個,副本的卡不是它填的,拿它判斷會把副本的卡亂標。"""
    import chrome_door
    if not is_real():
        return False
    try:
        return bool(chrome_door.sweep_gone(STATE,only=u,by='board_server'))   # 在看板鎖內照現在的看板算(#308)
    except Exception:  # noqa: BLE001 — 判斷不了就當頁面還在:👀 回「接不上,等一下再按」,自動流程每分鐘會再掃一次
        return False


def agent_swapped(u, why):
    """👀 找不到開這一頁的那一家(停用、移除、卡上沒記):送「填這張的 agent 接不回來」事件,卡上改成頁面不見了、寫原因、要重填。
    副本不動(跟 page_gone 一樣):副本的卡不是真的 agent 填的。
    卡上記不下來(看板檔寫不進去、被改過)回原因,👀 照實告訴他;記下了回空字串。"""
    import delivery_state
    if not is_real():
        return ''
    try:
        bd.set_fb(lambda d: delivery_state.try_fire(d,u,'page_lost',issues=[why]),
                  live=STATE, by='board_server')
    except (OSError,ValueError,RuntimeError) as e:   # 寫檔失敗、看板讀不懂、看板被改過(bd.Tampered)
        return str(e)[:120] or type(e).__name__
    return ''


def _run_then_stop(argv,timeout):
    """跑一支子程序,回結束碼;時間到先請它結束(SIGTERM),等一下還不走才強制,回 None。
    apply_tab 收到 SIGTERM 會照常跑 finally:把那一頁交接、宣告這一輪結束。以前 subprocess.run 的逾時直接強制結束,
    那一頁接在死掉的程序上,外掛跟 agent 的 Chrome 斷線,等他核對的頁接不回來。"""
    p=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.terminate()
        try: p.wait(timeout=20)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
        return None


def run_sp(kind):
    """進度狀態放哪。真的看板用設定的 tmp;副本各自放在自己旁邊,不會蓋到真的那一份。"""
    return cf.TMP if is_real() else os.path.join(os.path.dirname(STATE),RUNS[kind]['dir'])

def run_argv(kind,args):
    """只有真的看板才跑真的。副本(沙箱、測試、board_check)跑 job_fake:真的那兩支會派 agent、
    寫 repo 裡的檔、改現行看板,副本上按一下就動到真的東西。"""
    # 他在看板上選的「跑幾張」(limit)、在卡片 ⋯ 按的「只跑這張」(url):每一種都照樣傳下去
    lim=(['--limit',str(args['limit'])] if args.get('limit') else [])
    one=args.get('url') or ''
    if kind=='add':
        argv=[sys.executable,os.path.join(HERE,'add_job.py' if is_real() else 'job_fake.py'),'--board',STATE]
        if not is_real(): argv+=['--kind','add']
        for u in args.get('urls') or []: argv+=['--url',u]
        return argv
    if kind=='suggest':
        return [sys.executable,os.path.join(HERE,'suggest_cats.py' if is_real() else 'job_fake.py'),'--board',STATE]+(
            [] if is_real() else ['--kind','suggest'])
    if kind=='customize':
        argv=[sys.executable,os.path.join(HERE,'customize.py' if is_real() else 'job_fake.py'),'--board',STATE]
        if not is_real(): argv+=['--kind','customize']
        argv+=['--url',args['url']]
        for item in args.get('items') or []: argv+=['--item',item]
        return argv
    if kind=='replies' and not is_real():    # 副本不連外查回音,也不以未查來源推斷沒下文
        return [sys.executable,os.path.join(HERE,'reply_run.py'),'--no-agent','--board',STATE]
    if not is_real():
        return [sys.executable,os.path.join(HERE,'job_fake.py'),'--kind',kind,'--board',STATE,
                '--mode',args.get('mode',''),'--direction',args.get('text',''),
                '--stage',args.get('stage',''),'--url',args.get('url','')]+lim+(
                ['--minutes',str(args['minutes'])] if args.get('minutes') else [])   # 「跑幾張」「幾分鐘」副本也照著跑
    if kind=='replies':
        return [sys.executable,os.path.join(HERE,'reply_run.py'),'--board',STATE]+lim+(['--url',one] if one else [])
    if kind=='apply':
        return [sys.executable,os.path.join(HERE,'apply_run.py'),'--stage',args['stage'],'--board',STATE]+(
            ['--url',args['url']] if args.get('url') else [])+(['--note',args['note']] if args.get('note') else [])+lim
    if kind=='prep':
        return [sys.executable,os.path.join(HERE,'cut_tailor.py'),'--board',STATE]+lim+(
            ['--jids',card.card_id_from_url(one)] if one else [])
    argv=[sys.executable,os.path.join(HERE,'converge.py'),'--mode',args['mode']]+lim+(
        ['--direction',args['text']] if args['mode']=='dir' else [])
    # 他在卡片/公司列上點的種子:走的還是更深,只是範圍由他指定(converge 那邊統一處理)
    for x in (args.get('seeds') or []):
        argv+=(['--seed-co',x['v']] if x.get('k')=='co' else ['--seed-url',x['v']])
    if args.get('minutes'): argv+=['--minutes',str(args['minutes'])]
    return argv

def run_status(kind):
    import jobrun
    p=_PROC.get(kind)
    if p is not None and p.poll() is not None:
        _PROC[kind]=None     # 收掉結束的子行程;不收的話它變殭屍,pid 查起來永遠「還活著」
    if kind=='prep':
        import cut_tailor as ct     # 準備區另外算「agent 寫到第幾張」
        st=ct.progress(run_sp(kind))
    else:
        st=jobrun.read(os.path.join(run_sp(kind),RUNS[kind]['status']),(RUNS[kind]['script'],'job_fake.py'))
    return {k:st[k] for k in ('phase','running','paused','n','done','msg','missing','ready','skipped','techerr','added',
                               'mode','which','direction','step','t0','finished_at','stage','results','url',
                               'graceful','finishing','pending','found','dropped','timeup','minutes')
            if st.get(k) is not None}

def control_run(kind,act):
    """⏸ 暫停 / ▶ 繼續 / ⏹ 停止 正在跑的那一輪(見 jobrun.control)。回 (HTTP 狀態碼, 現在的進度)。"""
    import jobrun
    if act not in ('pause','resume','stop'): return 400,{'msg':'不知道要暫停、繼續還是停止'}
    with _RUN_LOCK:
        if act=='pause' and kind=='apply' and run_status(kind).get('stage')=='submit':
            # 凍在按下送出的半路:他查不到送出去沒有;解凍時早就過了時限(#308)。要停就按 ⏹,卡會變成送出結果不明
            return 409,dict(run_status(kind),msg='正在送出時不能暫停;要停就按 ⏹ 停止(會記成送出結果不明,要你去查)')
        ok,msg=jobrun.control(os.path.join(run_sp(kind),RUNS[kind]['status']),(RUNS[kind]['script'],'job_fake.py'),act,
                              hold=bd.live_lock(STATE) if STATE else None)
        st=run_status(kind)
    return (200 if ok else 409),dict(st,msg=msg) if not ok else st

def start_run(kind,args):
    """單飛:同一種正在跑就不再開第二個。cut_tailor 一啟動會先殺掉上一個 worker,
    按兩下等於把跑到一半的 agent 砍掉重來;找缺兩輪同時跑,兩隻 agent 會撈同一批。
    回 (HTTP 狀態碼, 內容)。"""
    import jobrun, time
    if kind in ('prep', 'customize', 'apply'):
        problem = _source_preflight()
        if problem:
            return 409, {'msg':problem}
    try: limit=max(0,min(500,int(args.get('limit') or 0)))
    except (TypeError,ValueError): limit=0
    one=str(args.get('url') or '')
    if one and kind in ('prep','replies'):
        fb0=json.loads(bd.parse(read_doc())['fb'])
        want={'prep':'prep','replies':'sent'}[kind]
        if (fb0.get(one) or {}).get('app')!=want: return 400,{'msg':'這張不在'+('準備履歷中' if kind=='prep' else '已投出')}
    if kind in ('research', 'suggest'):
        pasted = str(args.get('resume_text') or '').strip()[:100000]
        if kind == 'research':
            mode=args.get('mode'); text=re.sub(r'\s+',' ',str(args.get('text') or '')).strip()[:300]
            if mode not in MODES and mode!='seed': return 400,{'msg':'不知道要用哪一種找法'}
            if mode=='dir' and not text: return 400,{'msg':'要先寫往哪個方向挖'}
            seeds=[]
            for x in (args.get('seeds') or [])[:20]:
                k=(x or {}).get('k'); v=re.sub(r'\s+',' ',str((x or {}).get('v') or '')).strip()[:300]
                if k in ('co','job') and v: seeds.append({'k':k,'v':v})
            if mode=='seed' or seeds:
                if not seeds: return 400,{'msg':'還沒有指名要找的東西'}
                mode='deep'
        import settings_api as sa
        if pasted:
            try:
                sa.remember_pasted_resume(pasted)
            except OSError:
                return 500, {'msg':'履歷文字暫存失敗，請檢查資料夾權限後重試。'}
            resume_text = pasted
        else:
            resume_text, resume_problem = sa.resume_material()
            if not resume_text:
                if resume_problem.startswith('請先'):
                    return 400, {'msg':resume_problem}
                return 422, {'paste_resume':True, 'msg':resume_problem}
        if kind == 'research':
            # 看板那一格的值跟著請求來(填完馬上按,存設定的請求可能還沒到);沒帶或不合理才讀設定
            minutes=args.get('minutes')
            if minutes is None or sa.find_minutes_problem(minutes):
                minutes=(cf.C.get('search') or {}).get('find_minutes') or 0
            args={'mode':mode,'text':text,'seeds':seeds,'limit':limit,'minutes':minutes,'resume_text':resume_text}
        else:
            args={'resume_text':resume_text}
    if kind=='customize':
        url=str(args.get('url') or '')
        selected=args.get('items')
        if not url or not isinstance(selected,list) or not selected:
            return 400,{'msg':'先選一張卡和至少一份檔案'}
        data=bd.parse(read_doc())
        fb=json.loads(data['fb'])
        job=next((j for j in data['data'].get('jobs',[]) if j.get('id')==url),None)
        state=fb.get(url) or {}
        if not job or state.get('app') not in ('ready','ship'):
            return 400,{'msg':'只有待你決定或可以投了的卡可以客製'}
        import customize as cu, ship
        available={x['id']:x for x in ship.documents(job,fb)}
        items=list(dict.fromkeys(str(x) for x in selected if isinstance(x,str)))[:100]
        if not items or any(item not in available for item in items):
            return 400,{'msg':'客製清單已過時，請重新打開卡片再選'}
        for item_id in items:
            _skill,_err=cu._read_skill(available[item_id])
            if _err:return 400,{'msg':_err}
            if is_real():
                page_source,_err=cu._page_limit_source(url,available[item_id])
                if _err:return 409,{'msg':_err}
                try:
                    if not cu.sa.pdf_pages(page_source):
                        return 409,{'msg':f'{available[item_id]["name"]} 的頁數基準 PDF 沒有可讀取的頁面'}
                except Exception as e:  # noqa: BLE001 — 讀不了照實回給看板(409 訊息)
                    return 409,{'msg':f'{available[item_id]["name"]} 的頁數基準 PDF 讀取失敗:{str(e)[:120]}'}
        args={'url':url,'items':items}
    if kind=='apply':
        # 送出只對他核准過、核准後答案沒變的卡(判斷跟 apply_run、看板按鈕同一支 form_record.approval_problem)。
        # 最後一關一定是人:這裡先擋一次,apply_run 啟動時再擋一次。
        import form_record as fr
        stage=args.get('stage'); url=str(args.get('url') or '')
        note=re.sub(r'\s+',' ',str(args.get('note') or '')).strip()[:500]
        if stage not in ('fill','fix','submit'): return 400,{'msg':'不知道要填表、修改還是送出'}
        doc=bd.parse(read_doc()); fb=json.loads(doc['fb']); status=doc['data'].get('status')
        if stage=='submit':
            urls=[url] if url else [u for u,m in fb.items() if isinstance(m,dict) and m.get('app')=='ship' and not m.get('rm')]
            ok=[u for u in urls if fr.approval_problem(fb,u,status) is None]
            if not ok: return 400,{'msg':fr.approval_problem(fb,url,status) if url else '沒有核准有效的卡'}
        if stage=='fix':
            # 修改是叫回填這張的那一隻 agent 在原本那一頁上改:一次一張,那段對話要在
            m=fb.get(url) or {}
            import delivery_state
            if not url or not ((m.get('apply') or {}).get('session')): return 400,{'msg':'這張沒有 agent 填過的紀錄,先讓 agent 填表單'}
            if not delivery_state.allowed(m,'fix_start'):
                return 400,{'msg':'這張「'+delivery_state.label(delivery_state.state(m))+'」,不能叫 agent 在原頁改'}
            import apply_run
            if not note and not apply_run.to_translate(fb,url) and not any(x.get('refill') for x in (m.get('form') or {}).get('f',[])):
                return 400,{'msg':'寫一下要 agent 改什麼'}
        args={'stage':stage,'url':url,'note':note,'limit':0 if url else limit}
    if kind in ('prep','replies'):
        args={'limit':0 if one else limit,'url':one}
    if kind=='add':
        urls=[u for u in re.split(r'\s+',str(args.get('text') or '')) if re.match(r'https?://',u)][:30]
        if not urls: return 400,{'msg':'貼一個以上的職缺網址(http 開頭)'}
        args={'urls':urls}
    if kind in ('apply', 'replies') and is_real():
        import chrome_door
        if not chrome_door.configured():
            ego = chrome_door.settings_status()['ego']
            return 409, {'needs_browser':True,
                         'msg':f'這個動作需要 agent 的瀏覽器(ego):{ego["reason"]}。到「⚙ 設定 → 🤖 Agent 與瀏覽器」按「檢查 ego」看怎麼補。'}
    with _RUN_LOCK:
        st=run_status(kind)
        if st.get('running'): return 409,st
        # 代投和查應徵進度都用 agent 的瀏覽器,一次只跑一個(電腦資源)
        other={'apply':'replies','replies':'apply'}.get(kind)
        if other and run_status(other).get('running'):
            return 409,{'other':True,'msg':('查應徵進度' if other=='replies' else '幫你填表')+'正在用 agent 的瀏覽器,等它跑完再按'}
        sp=run_sp(kind); os.makedirs(sp,exist_ok=True)
        env=dict(os.environ); env[RUNS[kind]['env']]=sp
        if kind in ('research', 'suggest'): env['JOBSALVO_RESUME_TEXT']=args['resume_text']
        with open(os.path.join(sp,kind+'_launch.out'),'w') as log:
            p=subprocess.Popen(run_argv(kind,args),cwd=REPO,env=env,stdout=log,stderr=subprocess.STDOUT,
                               start_new_session=True)
        _PROC[kind]=p
        if PILOT:   # 這一輪跑完,自動流程接著看下一步(不另開輪詢)
            threading.Thread(target=lambda: (p.wait(), PILOT.kick()),daemon=True).start()
        # 那支自己寫第一筆進度之前,這一下就算開始了:再按一次要看得到「正在跑」
        jobrun.write(os.path.join(sp,RUNS[kind]['status']),
                     {'phase':'start','pid':p.pid,'t0':time.time(),'mode':args.get('mode',''),
                      'direction':args.get('text',''),'stage':args.get('stage',''),'url':args.get('url','')})
        return 200,run_status(kind)


# ---- 流程自動往下跑(tools/autopilot.py):替他按「跑準備區／可投遞／讓 agent 填表／查回音」,不會替他送出 ----
PILOT=None
def start_pilot():
    global PILOT
    import autopilot, agent_report
    def build_state():
        with _BUILD_LOCK: return {'gen':_build_state['gen'],'running':_build_state['running']}
    PILOT=autopilot.Pilot(STATE,start_run,run_status,build_state,is_real,
                          report=lambda *x,**k: agent_report.report(*x,live=STATE,**k),build=trigger_build)
    PILOT.start()


class H(BaseHTTPRequestHandler):
    def _send(self,code,body,ctype='text/html; charset=utf-8'):
        b=body.encode('utf-8') if isinstance(body,str) else body
        # 文字一律壓縮再送,這是每個網頁伺服器預設就會做的事,這裡以前沒做。
        # 首頁 1969KB → 397KB、職缺資料 1743KB → 314KB;他在手機上走 Tailscale,差五倍。
        gz=(len(b)>1024 and 'gzip' in (self.headers.get('Accept-Encoding') or '')
            and (ctype.startswith('text/') or 'json' in ctype))
        if gz: b=_gzip(b)
        self.send_response(code); self.send_header('Content-Type',ctype)
        if gz:
            self.send_header('Content-Encoding','gzip')
            self.send_header('Vary','Accept-Encoding')
        # 一律不准快取。這頁 5MB,手機瀏覽器會存下來重複用,他就在舊材料上做決定:
        # 看到的履歷是上一版、階段數字對不上、以為管線沒跑。這種錯不會報錯,只會誤導。
        self.send_header('Cache-Control','no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma','no-cache')
        self.send_header('Content-Length',str(len(b))); self.end_headers()
        try:
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            # 這一頁 6MB。手機捲到一半關分頁、或還沒收完就重整,連線就斷在這裡。
            # 伺服器沒有掛(ThreadingHTTPServer 每個請求一條執行緒,只有這條結束),
            # 但預設會把整段 traceback 印進日誌,看起來就像當機,真正的問題反而被埋掉。
            self.close_connection=True
    def _tampered(self, e):
        """看板檔被繞過正常寫入改過(bd.Tampered):不照改過的內容送頁面、不存檔,講清楚發生什麼事。"""
        msg=str(e)
        if self.command=='GET' and self.path in ('/','/index.html'):
            page=('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                  '<title>jobsalvo</title><p style="font:16px/1.6 system-ui;max-width:40em;margin:2em auto;padding:0 16px">'
                  +html.escape(msg)+'</p>')
            return self._send(423,page)
        self._json(423,{'ok':False,'err':'tampered','msg':msg})
    def do_GET(self):
        try: self._do_GET()
        except bd.Tampered as e: self._tampered(e)
    def do_POST(self):
        try: self._do_POST()
        except bd.Tampered as e: self._tampered(e)
    def _do_GET(self):
        if self._foreign(False): return
        if self.path in ('/','/index.html'):
            trigger_source_sync()
            self._send(200,serve_doc(read_doc()))
        elif self.path=='/api/health':
            self._json(200,{'ok':True,'app':'jobsalvo','version':RUNNING_VERSION or code_version()})
        elif self.path=='/api/state':
            d=bd.parse(read_doc()); self._send(200,d['fb'],'application/json; charset=utf-8')
        elif self.path.startswith('/api/customize/files?'):
            import customize as cu
            try:self._json(200,{'ok':True,'files':cu.list_files(self._q('u'),board=STATE)})
            except Exception as e:self._json(400,{'ok':False,'msg':str(e)[:200]})  # noqa: BLE001 — 原因照實回給看板
        elif self.path.startswith('/api/customize/diff?'):
            import customize as cu
            try:self._json(200,dict({'ok':True},**cu.diff_for(self._q('u'),self._q('item'),board=STATE)))
            except Exception as e:self._json(400,{'ok':False,'msg':str(e)[:200]})  # noqa: BLE001 — 原因照實回給看板
        elif self.path.startswith('/api/resume?'):
            u=self._q('u'); v=self._q('v'); d=bd.parse(read_doc())
            j=next((x for x in d['data'].get('jobs',[]) if x.get('id')==u),None)
            if j is None: self._json(404,{'ok':False,'msg':'找不到這張卡(可能剛被移除或重建),重新整理看板再試'})
            else:
                rz=j.get('resume') or {}; rz=rz if isinstance(rz,dict) else {}
                variants=rz.get('variants') or {}
                source=rz if v=='__top__' else (variants.get(v) if isinstance(variants,dict) else None)
                preview={k:source[k] for k in LAZY_VARIANT_KEYS if k in source} if isinstance(source,dict) else {}
                if not preview: self._json(404,{'ok':False,'msg':'找不到這份履歷預覽:到設定頁「你的履歷」重新上傳這一份'})
                else: self._json(200,preview)
        elif self.path=='/api/ship-files' or self.path.startswith('/api/ship-files?'):
            self.do_GET_ship_files()
        elif self.path=='/api/masters':
            d=bd.parse(read_doc())
            self._json(200,d['data'].get('masters') or [])
        elif self.path=='/api/jobs':
            self.do_GET_jobs()
        elif self.path=='/api/next':
            self._json(200,next_steps(bd.parse(read_doc())))
        elif self.path.startswith('/api/live?'):
            # 代投那張分頁「現在」的整頁畫面:agent 的視窗開在螢幕外(不搶他的畫面),他要看真的頁面就看這張。
            # apply_tab 用填這張的那段對話的身分當場截,不經過 agent、不動那一頁。
            from urllib.parse import urlparse, parse_qs
            import tempfile
            q=parse_qs(urlparse(self.path).query); u=(q.get('u') or [''])[0]
            ast=run_status('apply')
            if ast.get('running') and ast.get('url')==u:
                # agent 正拿著這一頁在填/改/送:這時用同一段對話去截,會搶它的分頁、甚至把它那一輪收掉
                return self._send(409,'Agent 正在處理這一張,跑完再看(進度在最上面的「🚀 填表進度」)','text/plain; charset=utf-8')
            # 照這張卡記的那一家的門路截(chrome_door):那一家停用、移除或卡上沒記,那一頁接不回來,跟卡上講的一樣要重填
            import chrome_door
            try: door=chrome_door.for_card((json.loads(bd.parse(read_doc())['fb'] or '{}').get(u) or {}).get('apply'))
            except ValueError: door=None
            except chrome_door.Unreachable as gone:
                if not gone.sure:                          # 設定檔讀不懂:判斷不了,卡不動
                    return self._send(503,str(gone),'text/plain; charset=utf-8')
                failed=agent_swapped(u,str(gone))
                if failed:
                    return self._send(404,str(gone)+f'。卡上沒改成要重填({failed}):重新整理看板,再按一次 👀。',
                                      'text/plain; charset=utf-8')
                return self._send(404,str(gone)+':按卡上的「▶ 讓 agent 重填這張」。','text/plain; charset=utf-8')
            if door is None:
                return self._send(503,'這次讀不到看板,等一下再按一次 👀。','text/plain; charset=utf-8')
            fd,out=tempfile.mkstemp(prefix='apply-live-',suffix='.png')
            os.close(fd)
            try:
                try:
                    code=_run_then_stop([sys.executable,os.path.join(HERE,'apply_tab.py'),'shot','--url',u,'--board',STATE,'--out',out],
                                        door.live_timeout)
                    ok=bool(u) and code==0 and os.path.isfile(out)
                except (OSError,subprocess.SubprocessError):   # 截圖程式開不起來:跟截不到同一條,下面照實說頁面不見了或接不上
                    ok=False
                if ok:
                    with open(out,'rb') as fh: body=fh.read()
                    self.send_response(200); self.send_header('Content-Type','image/png')
                    self.send_header('X-Refresh',str(door.live_refresh))
                    self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
                elif page_gone(u):
                    self._send(404,'那一頁已經不在了(agent 的 Chrome 關掉或重開過)。卡上已改成要重填:按卡上的「▶ 讓 agent 重填這張」。',
                               'text/plain; charset=utf-8')
                else:
                    # 程序還是填好那時候的那一個:頁面應該還在,只是這次接不上(外掛一時斷線這類),不叫他重填
                    self._send(503,'這次接不上 agent 的 Chrome 裡的那一頁,頁面應該還在。等一下再按一次 👀。',
                               'text/plain; charset=utf-8')
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(out)
        elif self.path.startswith('/api/shot?'):
            # agent 填表/送出時截的圖(可投遞夾的 .apply/ 裡)。只給這兩張,路徑由網址算,不收任何路徑參數。
            from urllib.parse import urlparse, parse_qs
            import apply_run
            q=parse_qs(urlparse(self.path).query); u=(q.get('u') or [''])[0]; s=(q.get('s') or [''])[0]
            f=os.path.join(apply_run.out_dir(u,STATE,run_sp('apply')),s+'.png') if u and s in ('fill','submit') else ''
            if f and os.path.isfile(f):
                with open(f,'rb') as fh: self._send(200,fh.read(),'image/png')
            else: self._send(404,'沒有截圖','text/plain; charset=utf-8')
        elif self.path.startswith('/api/evidence?'):
            # 要他確認或處理的事附的那張截圖(程式自己截的,在那張卡的證據夾,#315)。只收「<輪>/<檔名>」、只給圖檔
            from urllib.parse import urlparse, parse_qs
            import evidence
            q=parse_qs(urlparse(self.path).query); u=(q.get('u') or [''])[0]; rel=(q.get('f') or [''])[0]
            f=evidence.path(u,rel,STATE) if u and rel.lower().endswith(('.png','.jpg','.jpeg')) else None
            if f:
                with open(f,'rb') as fh: self._send(200,fh.read(),'image/jpeg' if f.lower().endswith(('.jpg','.jpeg')) else 'image/png')
            else: self._send(404,'沒有截圖','text/plain; charset=utf-8')
        elif self.path in ('/api/rev','/api/rev?sync=1'):
            # 切回看板分頁時頁面帶 sync=1:跟打開看板一樣比對一次原稿,變了就在背景先建
            if self.path.endswith('?sync=1'):
                trigger_source_sync()
            # 輪詢只拿這一個小包:版本(標記變了沒)、gen(看板資料重建過沒)、building。
            # 以前頁面每 20 秒抓一次 /api/jobs 只為了讀裡面的 gen,那一包 1.26MB,
            # 等於他手機上每分鐘白流 3.8MB、還要解析同樣大小的 JSON。
            with _BUILD_LOCK: building=_build_state['running']
            self._json(200,{'rev':content_sig()[0],'gen':cur_gen(),'building':building,
                                        'prep':run_status('prep'),'research':run_status('research'),
                                        'customize':run_status('customize'),
                                        'apply':run_status('apply'),'replies':run_status('replies'),
                                       'add':run_status('add'),'suggest':run_status('suggest')})
        elif self.path.startswith('/api/prompt'):
            self.do_GET_prompt()
        elif self.path in ('/api/update','/api/update?now=1'):
            # 設定頁「⚙ 其他」的版本那一列:現在哪一版、遠端 main 有沒有新的、能不能從這裡更新(tools/update.py)。副本不問遠端。
            # ?now=1:他按了「現在檢查」,不等每小時那一次
            if not is_real():
                return self._json(200,{'current':'','latest':'','new':False,'blocked':'副本不檢查更新'})
            import update
            return self._json(200,update.check(force=self.path.endswith('?now=1')))
        elif self.path=='/api/settings':
            import settings_api as sa
            note_existed=os.path.exists(cf.PREFERENCE_NOTE)
            d=sa.get()
            if not note_existed and os.path.exists(cf.PREFERENCE_NOTE):
                note_saved()
            try:
                # 安檢門核對過的那一份(suggest_cats.checked);agent 交的 suggest.json 不直接給看
                with open(os.path.join(run_sp('suggest'),'suggestion.json'),encoding='utf-8') as f: d['suggest']=json.load(f)
            except (OSError,ValueError): d['suggest']=None
            self._json(200,d)
        elif self.path.startswith('/api/bank/form?'):
            import interview_bank as ib
            bank=bd.parse(read_doc())['data'].get('bank') or {}
            it=next((x for x in bank.get('items') or [] if x.get('id')==self._q('id')),None)
            self._json(200 if it else 404,{'ok':bool(it),'form':ib.to_form(it) if it else None,
                                          'cats':bank.get('cats') or ib.DEFAULT_CATS})
        elif self.path.startswith('/api/text?'):
            import settings_api as sa
            t,err=sa.text_of(self._q('path'))
            self._json(200 if not err else 400,{'ok':not err,'text':t,'msg':err})
        elif self.path.startswith('/api/log?'):
            import settings_api as sa
            k=self._q('kind')
            self._send(200,sa.tail_log(k,run_sp(k)) if k in RUNS else '(沒有這一種)','text/plain; charset=utf-8')
        elif self.path.startswith('/api/file?'):
            import settings_api as sa
            f=sa.safe_rel(self._q('path'))
            if f and os.path.isfile(f):
                import mimetypes
                with open(f,'rb') as fh: self._send(200,fh.read(),mimetypes.guess_type(f)[0] or 'application/octet-stream')
            else: self._send(404,'not found','text/plain; charset=utf-8')
        elif self.path.startswith('/api/preview/'):
            import pdf_preview
            from urllib.parse import urlparse
            key=urlparse(self.path).path[len('/api/preview/'):]
            key=key[:-4] if key.endswith('.png') else ''
            data=pdf_preview.read(key)
            if data: self._send(200,data,'image/png')
            else: self._send(404,'沒有 PDF 預覽','text/plain; charset=utf-8')
        elif self.path.startswith('/api/source-preview?'):
            import pdf_preview, reconcile, source_sync
            kind=self._q('kind'); item_id=self._q('id'); lang=self._q('lang')
            key=source_sync.source_preview_hash(kind,item_id,lang,reconcile.MANIFEST)
            data=pdf_preview.read(key) if key else None
            if data: self._send(200,data,'image/png')
            else: self._send(404,'這份來源目前沒有 PDF 預覽','text/plain; charset=utf-8')
        else:
            self._send(404,'not found','text/plain; charset=utf-8')
    # ---- ⚙ 設定、上傳、卡片自己的檔:使用者只碰網頁就能做完所有設定 ----
    def _q(self,k):
        from urllib.parse import urlparse, parse_qs
        return (parse_qs(urlparse(self.path).query).get(k) or [''])[0]
    def _json(self,code,obj):
        self._send(code,json.dumps(obj,ensure_ascii=False),'application/json; charset=utf-8')
    def _body(self):
        n=int(self.headers.get('Content-Length','0') or 0)
        return self.rfile.read(n) if n else b''
    def _json_body(self):
        """看板送來的 JSON 物件。讀不懂(編碼、JSON 壞掉、不是物件)就先回 400,回 None。"""
        try:
            body=json.loads(self._body().decode('utf-8') or '{}')
        except ValueError:   # JSON 或編碼壞掉(UnicodeDecodeError 也是 ValueError)
            body=None
        if not isinstance(body,dict):
            self._json(400,{'ok':False,'msg':'看板送來的資料格式不對,重新整理看板再試一次'})
            return None
        return body
    def _host_ok(self, host):
        """這個 Host(或 Origin 的主機)是不是這個伺服器自己:綁的位址、本機名稱,
        綁在 Tailscale 位址時也認 MagicDNS 名稱(*.ts.net)。"""
        ip, port = self.server.server_address[:2]
        host = (host or '').strip().lower()
        ok = {f'{ip}:{port}'}
        if ip in ('127.0.0.1', '::1'):
            ok |= {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'}
        return host in ok or (str(ip).startswith('100.') and host.endswith(f'.ts.net:{port}'))

    def _foreign(self, write):
        """別的網站冒用看板就擋掉(回 True)。
        看板會派有完整權限的 agent:你開著看板時逛到的網站,可以直接對 127.0.0.1 送 POST(簡單請求不用先問),
        也可以用 DNS rebinding 把自己的網域指到 127.0.0.1 讀資料。
        ① Host 一定要是這個伺服器自己(擋 rebinding,讀寫都擋);
        ② 會改東西的請求:瀏覽器標了跨站(Sec-Fetch-Site)或 Origin 不是自己就擋。
        本機程式(curl、agent 工具)不帶 Origin,照常可以用。"""
        why = ''
        if not self._host_ok(self.headers.get('Host')):
            why = '不認得的主機名稱(可能是別的網站冒用)'
        elif write:
            site = (self.headers.get('Sec-Fetch-Site') or '').lower()
            origin = self.headers.get('Origin')
            if site and site not in ('same-origin', 'none'):
                why = '別的網站送來的請求'
            elif origin and not (origin.lower().startswith('http://')
                                 and self._host_ok(origin.split('://', 1)[1].split('/', 1)[0])):
                why = '別的網站送來的請求'
        if why:
            self._json(403, {'ok': False, 'err': 'foreign', 'msg': why})
            return True
        return False

    def _agent_blocked(self):
        if AGENT_UA in (self.headers.get('User-Agent') or '') and not ALLOW_AGENT[0]:
            self._json(403,{'ok':False,'err':'agent','msg':'這個看板不收 agent 的寫入。'}); return True
        return False
    def do_PUT(self):
        """上傳檔案:body 就是檔案本身(不用 multipart)。/api/file?path=resume/x.pdf 存進資料夾;
        /api/card-file?u=<職缺網址>&name=<檔名> 是「📎 這張用自己的檔」,存進 custom/ 並記在那張卡上。"""
        if self._foreign(True): return
        if self._agent_blocked(): return
        import settings_api as sa
        data=self._body()
        if self.path.startswith('/api/file?'):
            before=sent_files()
            rel,err=sa.put_file(self._q('path'),data)
            if not rel: return self._json(400,{'ok':False,'msg':err})
            note_saved()
            files_changed(before,'「'+os.path.basename(rel)+'」的原始檔換過了')
            trigger_build()
            return self._json(200,{'ok':True,'path':rel})
        if self.path.startswith('/api/card-file?'):
            u=self._q('u'); name=os.path.basename(self._q('name') or 'resume.pdf')
            if not u: return self._json(400,{'ok':False,'msg':'沒有指定是哪一張'})
            item_id=self._q('item')
            if item_id:
                import customize as cu
                rel,err=cu.upload_custom(u,item_id,name,data,board=STATE)
                if not rel:return self._json(400,{'ok':False,'msg':err})
                note_saved()
                trigger_build()
                return self._json(200,{'ok':True,'path':rel})
            rel,err=sa.put_file(f'custom/{card.card_id_from_url(u)}/{name}',data)
            if not rel: return self._json(400,{'ok':False,'msg':err})
            import customize as cu
            def own_file(_data,fb):
                fb.setdefault(u,{})['custom_file']=rel
                return cu.swap_files(fb,u,'這張換成你自己的檔了')
            ok,why=cu._in_lock(STATE,own_file)
            if not ok: return self._json(409,{'ok':False,'msg':why})
            trigger_build()
            return self._json(200,{'ok':True,'path':rel})
        self._send(404,'not found','text/plain; charset=utf-8')
    def do_DELETE(self):
        if self._foreign(True): return
        if self._agent_blocked(): return
        import settings_api as sa
        if self.path.startswith('/api/file?'):
            before=sent_files()
            removed=sa.delete_file(self._q('path'))
            if removed:
                note_saved()
                files_changed(before,'要寄的檔案被刪掉了')
            return self._json(200,{'ok':removed})
        self._send(404,'not found','text/plain; charset=utf-8')
    def do_POST_bank(self):
        """🎤 面試準備:新增/改/刪一題。body = {op:'put', item:{編輯框的欄位}} 或 {op:'del', id}。回整份新的 bank。"""
        if self._agent_blocked(): return
        import interview_bank as ib
        body=self._json_body()
        if body is None: return
        op=body.get('op')
        if op not in ('put','del'): return self._json(400,{'ok':False,'msg':'不知道要做什麼'})
        out={}
        def mut(data,fb):
            data['bank']=ib.apply(data.get('bank'),op,body.get('item'),body.get('id')); out['bank']=data['bank']
        bd.set_data(mut,live=STATE)
        note_saved()
        return self._json(200,{'ok':True,'bank':out['bank']})
    def do_POST_settings(self):
        if self._agent_blocked(): return
        import settings_api as sa
        body=self._json_body()
        if body is None: return
        act=self.path[len('/api/settings/'):] if self.path.startswith('/api/settings/') else ''
        if act=='skill':
            skill,msg=sa.create_skill(body.get('name'),body.get('content'),body.get('kind'))
            if skill: note_saved()
            return self._json(200 if skill else 400,{'ok':bool(skill),'skill':skill,'msg':msg})
        if act=='browser':
            # 設定頁的「檢查 ego」:只看裝好沒、指令跑不跑得起來、匯入了沒,不開也不關任何瀏覽器
            import chrome_door
            try: ok,msg=chrome_door.setup()
            except Exception as e:  # noqa: BLE001 — 原因照實回給看板(沒成功:…)
                ok,msg=False,f'沒成功:{str(e)[:120]}'
            return self._json(200 if ok else 400,{'ok':ok,'msg':msg})
        if act=='use_agent':
            # 環境檢查「至少一個能用的 agent」那一列的「改用 X」:把這台電腦上能用的那一種放到 agent 清單最前面
            import doctor
            rt=body.get('runtime')
            if rt not in doctor.RUNTIMES or not doctor.agent_state(rt)[0]:
                return self._json(400,{'ok':False,'msg':'這一種在這台電腦上用不了'})
            s=cf.user_settings(); ag=s.setdefault('agent',{})
            old=list(ag.get('agents') or (cf.C.get('agent') or {}).get('agents') or [])
            ids={str(x.get('id')) for x in old if isinstance(x,dict)}
            import chrome_door
            chrome=rt in chrome_door.DOORS      # 這一家能用 agent 的 Chrome
            if chrome:   # 只准一個 agent 用 Chrome:換成新的那個
                old=[{**x,'browser':False} if isinstance(x,dict) else x for x in old]
            new_id=next(i for i in [rt]+[f'{rt}-{n}' for n in range(2,99)] if i not in ids)
            ag['agents']=[{'id':new_id,'runtime':rt,'model':'','effort':'max','speed':'standard',
                           'browser':chrome}]+old
            bad=PILOT.save_settings(lambda: sa.save({'settings':s})) if PILOT else sa.save({'settings':s})
            return self._json(400 if bad else 200,{'ok':not bad,'msg':';'.join(bad) or '改好了,之後會先用它'})
        if act=='find_minutes':
            # 找缺那一列的「__ 分鐘」格子:改了就存(同一套驗證、同一條存檔路);空的存成 0 = 不限時
            v=body.get('minutes')
            v=0 if v in (None,'') else v
            s=cf.user_settings(); s.setdefault('search',{})['find_minutes']=v
            bad=PILOT.save_settings(lambda: sa.save({'settings':s})) if PILOT else sa.save({'settings':s})
            return self._json(400 if bad else 200,{'ok':not bad,'msg':';'.join(bad)})
        if act=='service':
            # 開機啟動是整台電腦的服務:副本(沙箱、截圖、介面檢查)按了會停掉或換掉真的那一個
            if not is_real():
                return self._json(400,{'ok':False,'msg':'這是副本,不能改開機自動啟動'})
            r=subprocess.run([sys.executable,os.path.join(HERE,'install_service.py')]+(['--remove'] if body.get('act')=='remove' else []),
                             capture_output=True,text=True,env=dict(os.environ,JOBSALVO_HOME=cf.HOME))
            msg=(r.stdout or r.stderr).strip()[-400:]
            if r.returncode==0 and body.get('act')!='remove' and os.environ.get('JOBSALVO_LAUNCHD')!='1':
                # 開機啟動會自己起一個看板;現在這個不是它起的,兩個搶同一個埠,launchd 那個會一直重試。
                # 安裝指令起的看板在背景、沒有終端機可以按 Ctrl-C:回完話自己關,交給 launchd
                hand_over_to_launchd()
                msg+='\n裝好了。這個看板馬上會自己關掉,交給開機自動啟動接手:大約半分鐘後重整這一頁。'
            return self._json(200 if r.returncode==0 else 400,{'ok':r.returncode==0,'msg':msg})
        before=sent_files()
        bad=PILOT.save_settings(lambda: sa.save(body)) if PILOT else sa.save(body)
        if bad: return self._json(409 if sa.CONFLICT in bad else 400,{'ok':False,'msg':';'.join(bad)})
        note_saved()
        files_changed(before,'設定改了,這張要寄的檔案跟著變了')
        with _PV_LOCK: _PV_CACHE.clear()
        trigger_build()
        if PILOT: PILOT.kick()
        return self._json(200,{'ok':True,'version':sa.version()})   # 同一頁接著再存(上傳後自動存)要帶這一版
    def do_GET_prompt(self):
        """看板上每一顆會派 agent 的按鈕旁邊的「📝 prompt」:按下去會送給 agent 的那一份,當場組給他看。
        prompt 是程式寫的,不用等跑完才知道它拿什麼去問。組的時候不派 agent、不連網、不寫任何檔。
        kind=find(mode=deep|wide|dir,text=方向)、judge(逐張判斷)、prep(跑準備區)、apply(stage=fill|fix|submit)。
        組一份要讀看板、算他的口味索引,不快;同一個看板版本(gen)組過的就記著,他點開才不會頓。"""
        from urllib.parse import urlparse, parse_qs
        q=parse_qs(urlparse(self.path).query)
        kind=(q.get('kind') or [''])[0]; mode=(q.get('mode') or [''])[0]
        stage=(q.get('stage') or [''])[0]
        text=re.sub(r'\s+',' ',(q.get('text') or [''])[0]).strip()[:300]
        key=(cur_gen(),pv_sig(),kind,mode,stage,text)
        with _PV_LOCK:
            hit=_PV_CACHE.get(key)
        if hit is not None:
            self._json(200,hit); return
        # note:這一份哪裡不是最終樣子(用哪張卡當例子、哪一段之後才接上去)。
        # 例子公司容易被看成「prompt 被寫死成某一家」,所以這句話要放在最上面。
        # 由伺服器算、看板只負責顯示在最上面:只有這裡知道實際挑到哪張卡。
        note=''
        try:
            if kind=='prep':
                import cut_tailor as ct; p=ct.preview(STATE)
            elif kind=='judge':
                import research; p=research.preview('judge', live=STATE)
                note='這是骨架。真的跑的時候每批 5 張:每張的 JD 原文、和他對最像的十幾張舊卡的表態與原話,會接在下面。'
            elif kind=='find' and mode in MODES:
                import research; p=research.preview(mode, text or '(你在上面寫的方向會放這裡)', live=STATE)
                if mode=='dir' and not text:
                    note='你還沒寫方向。在框裡寫的那一句,會放進下面「他說:『…』」的位置。'
            elif kind=='replies':
                import reply_run; p=reply_run.preview(STATE)
                note='這份列出「已投出」裡所有還在等的卡;真的跑時照你選的「跑幾張」只查前幾張。卡片清單照你按下去那一刻的看板。'
            elif kind=='apply' and stage in ('fill','fix','submit'):
                import apply_run; p=apply_run.preview(stage, board=STATE)
                meta=getattr(apply_run,'preview_meta',None); m=None
                if meta:
                    m=meta(stage, board=STATE)   # 出錯交給外面那層照實顯示,不當成「沒有符合的卡」
                t=(m or {}).get('title') or ''
                if meta and not m:
                    note='現在沒有符合這個階段的卡,組不出例子。下面是程式給的說明,不是會送出去的 prompt。'
                else:
                    note=('這是例子:'+('用「%s」這張組的'%t if t else '用現在符合這個階段的其中一張組的')+
                          '。真的派出去時每張各派一隻,職缺、網址、檔案換成那張的;規矩的段落每張都一樣。')
            else:
                self._json(400,{'msg':'不知道要看哪一段的 prompt'}); return
        except Exception as e:  # noqa: BLE001 — 原因照實顯示在 prompt 預覽裡
            p='組這份 prompt 的時候出錯了:%s\n(這不影響按鈕,只是這裡看不到。)'%e
        out={'prompt':p,'note':note}
        with _PV_LOCK:
            if len(_PV_CACHE)>40: _PV_CACHE.clear()      # 看板一變 gen 就換一批 key,舊的不用留
            _PV_CACHE[key]=out
        self._json(200,out)
    def do_GET_ship_files(self):
        """要寄的檔案。帶 u:只算那一張;resume_id / lang 帶了就蓋過存檔裡的(他剛按、還沒存的),空的 = 清掉。
        只讀,不寫檔、不重建。"""
        from urllib.parse import urlparse, parse_qs
        q=parse_qs(urlparse(self.path).query,keep_blank_values=True)
        d=bd.parse(read_doc()); fb=json.loads(d['fb'] or '{}'); jobs=d['data'].get('jobs',[])
        u=(q.get('u') or [''])[0]
        if not u:
            return self._json(200,ship_files_of(jobs,fb))
        if not any(isinstance(j,dict) and j.get('id')==u for j in jobs):
            return self._json(404,{'ok':False,'msg':'找不到這張卡(可能剛被移除或重建),重新整理看板再試'})
        mark=dict(fb.get(u) or {}) if isinstance(fb.get(u),dict) else {}
        for k in ('resume_id','lang'):
            if k in q:
                v=q[k][0]
                if v: mark[k]=v
                else: mark.pop(k,None)
        fb=dict(fb); fb[u]=mark
        self._json(200,ship_files_of(jobs,fb,only=u)[u])
    def do_GET_jobs(self):
        with _BUILD_LOCK: building=_build_state['running']
        d=bd.parse(read_doc())
        # 驗收結果(status)也在職缺資料裡,要一起給:以前只給 jobs,重驗過之後頁面上的
        # 「⛔ 驗收未通過」還是舊的那一批,要他自己重整。
        jobs=d['data'].get('jobs',[])
        self._json(200,{'building':building,'gen':cur_gen(),'jobs':page_jobs(jobs),
                                   'ship_files':ship_files_of(jobs,json.loads(d['fb'] or '{}')),
                                   'status':d['data'].get('status'),'research':d['data'].get('research')})

    def _do_POST(self):
        if self._foreign(True): return
        if self.path=='/api/live':
            if self._agent_blocked():return
            body=self._json_body()
            if body is None:return
            u=body.get('u') or ''
            # 每張卡一個工作區:只擋 agent 正在處理的那一張,別張照樣能接手
            if run_status('apply').get('running') and run_status('apply').get('url')==u:
                return self._json(409,{'ok':False,'msg':'Agent 正在處理這一張,等它停下再接手'})
            import chrome_door, delivery_state as ds
            m=json.loads(bd.parse(read_doc())['fb'] or '{}').get(u) or {}
            if ds.state(m)!='stuck' or not (m.get('apply') or {}).get('workspace'):
                return self._json(200,{'ok':True,'handoff':False})
            try:
                door=chrome_door.for_card(m.get('apply'))
                receipt=door.hand_off()
            except (chrome_door.Unreachable,chrome_door.NotNow) as e:
                return self._json(503,{'ok':False,'msg':str(e)})
            return self._json(200,{'ok':True,'handoff':True,'shown':(receipt or {}).get('shown'),
                                  'page':(receipt or {}).get('page'),'site':(receipt or {}).get('site'),
                                  'msg':'已交給你接手這張卡的頁面;處理完按「修改」繼續'})
        if self.path=='/api/settings' or self.path.startswith('/api/settings/'):
            return self.do_POST_settings()
        if self.path=='/api/update':
            if self._agent_blocked():return
            if not is_real():return self._json(400,{'ok':False,'msg':'副本不更新'})
            import update
            r=update.apply()
            return self._json(200 if r.get('ok') else 400,r)
        if self.path=='/api/bank':
            return self.do_POST_bank()
        if self.path=='/api/merge':
            # 存檔撞到之後(409):照伺服器現在的版本,把這個分頁手上的改動疊上去。只算不寫,看板拿結果再存一次
            body=self._json_body()
            if body is None: return
            fb=json.loads(bd.parse(read_doc())['fb'] or '{}')
            base,mine=(x if isinstance(x,dict) else {} for x in (body.get('base'),body.get('mine')))
            return self._json(200,{'fb':fb,'merged':{k:dict(merge_edit(base.get(k),mine.get(k),fb.get(k),MERGE_BY.get(k)),
                                                             key=MERGE_BY.get(k)) for k in map(str,body.get('keys') or mine.keys()|base.keys())}})
        if self.path=='/api/customize':
            if self._agent_blocked():return
            body=self._json_body()
            if body is None: return
            import customize as cu
            if body.get('op')=='accept':
                ok,msg=cu.accept(str(body.get('url') or ''),str(body.get('item') or ''),board=STATE)
                if ok:trigger_build()
            elif body.get('op')=='reject':
                ok,msg=cu.reject(str(body.get('url') or ''),str(body.get('item') or ''),body.get('feedback'),board=STATE)
            elif body.get('op')=='clear':
                ok,msg=cu.clear(str(body.get('url') or ''),str(body.get('item') or ''),board=STATE)
                if ok:trigger_build()
            else:
                return self._json(400,{'ok':False,'msg':'不知道要做什麼'})
            return self._json(200 if ok else 400,{'ok':ok,'msg':msg})
        kind=self.path[len('/api/run/'):] if self.path.startswith('/api/run/') else ''
        act=''
        if '/' in kind: kind,act=kind.split('/',1)
        if kind in RUNS and act:
            if AGENT_UA in (self.headers.get('User-Agent') or '') and not ALLOW_AGENT[0]:
                self._json(403,{'ok':False,'err':'agent'}); return
            code,st=control_run(kind,act)
            self._json(code,dict(st,ok=code==200)); return
        if self.path!='/api/save' and kind not in RUNS:
            self._send(404,'not found','text/plain; charset=utf-8'); return
        # 這份板子上的標記是他的判斷,只有他能寫;跑準備區、找新職缺也只有他能發動。
        # AI 助理的瀏覽器 User-Agent 帶 'Claude/',使用者的手機與瀏覽器不會有,所以這裡認得出來、直接擋掉。
        # 以前擋不住:我在 localhost 點一下喜歡就跟他自己點沒有分別,測試因此蓋掉過他剛標的東西,
        # 而使用者不可能記得自己標過什麼。
        # 我要走介面就開沙箱板(tools/board_sandbox.py,那支會帶 --allow-agent)。
        if AGENT_UA in (self.headers.get('User-Agent') or '') and not ALLOW_AGENT[0]:
            self._json(403,{'ok':False,'err':'agent','msg':'這個看板不收 agent 的寫入。要測介面請開沙箱板(8898)。'}); return
        if kind:
            args=self._json_body()   # 讀不懂就回 400:不當成沒帶參數照跑,該只跑一張的會變成全部都跑
            if args is None: return
            code,st=start_run(kind,args)
            self._json(code,dict(st,ok=code==200)); return
        try:
            n=int(self.headers.get('Content-Length','0'))
            fb=json.loads(self.rfile.read(n).decode('utf-8'))
            assert isinstance(fb,dict)
            # 陳舊分頁防護:整包送(超過 40 筆)而且沒帶版本的,一律擋。
            # 手機擱著沒關的舊分頁會把整份標記倒回它載入時的樣子,別處的改動就沒了。
            keys=[k for k in fb if not k.startswith('__')]
            if '__rev__' not in fb and len(keys)>40:
                self._json(409,{'ok':False,'err':'stale','msg':'這個分頁的程式是舊版,請重新整理再存'}); return
            fb.pop('__rev__',None)
            base=fb.pop('__base__',None)
            events=fb.pop('__events__',None)
            rejected=[]
            got={}
            bad=write_fb(fb, base if isinstance(base,dict) else None,
                         events if isinstance(events,list) else None, rejected, got)
            if bad:
                self._json(409,{'ok':False,'err':'conflict','keys':bad,
                    'msg':'這幾筆在別的地方改過了,先拿最新的再存'}); return
            # 有卡進待你決定/可投遞 → 背景生可投遞包(single-flight),使用者不用再等人建。
            if any(isinstance(v,dict) and v.get('app') in ('ready','ship') for v in fb.values()):
                trigger_build()
            if PILOT and (events or '__auto__' in fb or any(isinstance(v,dict) and v.get('app') in ('prep','ready','ship') for v in fb.values())):
                PILOT.kick()
            with _BUILD_LOCK: building=_build_state['running']
            # 有變動的卡的新下一步:這一包動到的卡;動到常用答案、封鎖這類整份的資料(或改答案的事件)就每一張都重算
            whole=any(k.startswith('__') for k in fb) or any(isinstance(e,dict) and not e.get('u') for e in events or [])
            touched=None if whole else {k for k in fb}|{str(e.get('u') or '') for e in events or [] if isinstance(e,dict)}
            self._json(200,{'ok':True,'building':building,'rejected':rejected,'cards':got.get('cards',{}),
                            'undo':got.get('undo',{}),'next':next_steps(bd.parse(read_doc()),touched)})
        except bd.Tampered:
            raise
        except Exception as e:  # noqa: BLE001 — 存檔失敗的原因照實回給看板(存檔列顯示沒存成)
            self._json(400,{'ok':False,'err':str(e)})
    def log_message(self,*a): pass  # 安靜

def tailscale_ip():
    """抓自己的 Tailscale IPv4;沒 tailscale/沒連上就回 None。"""
    tsc=shutil.which('tailscale') or '/Applications/Tailscale.app/Contents/MacOS/Tailscale'
    try:
        out=subprocess.run([tsc,'ip','-4'],capture_output=True,text=True,timeout=5).stdout.strip()
        ip=out.splitlines()[0].strip() if out else ''
        return ip if ip.startswith('100.') else None
    except (OSError,subprocess.SubprocessError):   # 沒裝 Tailscale、沒開:只綁本機
        return None

def resolve_hosts(arg):
    """一律綁 127.0.0.1(純本機迴環,不經過網路卡),再加上 Tailscale IP 給手機連。
    localhost 這條是給本機瀏覽器用的:裸 IP 的 origin 拿不到「永久允許這個站」,
    每個動作都要重按一次權限;localhost 是瀏覽器認得的固定站名,授權一次就記住。
    仍然絕不綁 0.0.0.0:那會把你的求職資料攤給同 Wi-Fi 的陌生人。"""
    if arg=='auto':
        ip=tailscale_ip()
        if ip: return ['127.0.0.1',ip]
        print('⚠ 抓不到 Tailscale IP,只綁 127.0.0.1(手機連不到)。開 Tailscale 再重起。')
        return ['127.0.0.1']
    if arg in ('0.0.0.0','::'):
        sys.exit('拒絕綁 '+arg+':會把你的求職資料暴露給同 Wi-Fi/LAN 的陌生人。要對外只綁 Tailscale IP。')
    return ['127.0.0.1'] if arg=='127.0.0.1' else ['127.0.0.1',arg]

def sandbox_home():
    """副本(沙箱、截圖、介面檢查、測試)用的暫存資料夾:設定頁的改動與上傳寫進這裡,不碰真的設定。
    設定、偏好筆記、客製 skill,以及設定裡每份履歷與附件指到的檔都複製一份(是複製不是連結:
    副本上傳、改檔寫的都是這一份,原檔一個位元組都不動)。以前沒帶履歷檔,副本上找缺會要你貼上履歷內容、
    設定頁顯示開始前 0/1、每份履歷都標找不到檔案。可投遞夾那些建置產物不複製(幾百 MB,副本用不到)。"""
    import tempfile
    tmp=tempfile.mkdtemp(prefix='jobsalvo-sandbox-home-')
    try:
        home=os.path.realpath(cf.HOME)

        def keep(src, rel):
            # 副本保留設定中的相對路徑；來源可由資料夾內的連結指向外部原稿，目的地只能在新副本內。
            dst=os.path.abspath(os.path.join(tmp,rel))
            if not os.path.isfile(src) or os.path.commonpath((tmp,dst))!=tmp:
                return
            os.makedirs(os.path.dirname(dst),exist_ok=True); shutil.copy2(src,dst)   # 只複製內容,不連回原檔
        for f in (os.path.join(cf.HOME,cf.NAME),cf.PREFS,cf.PREFERENCE_NOTE,
                  cf.APPLY_RULES,os.path.join(cf.HOME,'resume.md'),os.path.join(cf.HOME,'.resume-paste.md')):
            if os.path.commonpath((home,os.path.realpath(f)))==home:
                keep(f,os.path.relpath(os.path.realpath(f),home))
        import settings_api as sa
        def keep_skill(rel):
            if isinstance(rel,str) and rel.startswith('custom/skills/'):
                src=sa.safe_rel(rel)
                if src: keep(src,rel)
        for item in sa.skill_files():
            keep_skill(item['path'])
        for skill in ((cf.C.get('research') or {}).get('skills') or {}).values():
            keep_skill(skill)
        for kind in ('resumes','attachments'):
            for item in (cf.C.get('resume') or {}).get(kind) or []:
                keep_skill((item or {}).get('skill'))
                for field in ('files','styles'):
                    for rel in ((item or {}).get(field) or {}).values():
                        rel=str(rel or '')
                        src=sa.safe_rel(rel,allow_external_symlink=True)
                        if src: keep(src,rel)
        return tmp
    except BaseException:
        shutil.rmtree(tmp,ignore_errors=True)
        raise


def main():
    global STATE,RUNNING_VERSION
    ap=argparse.ArgumentParser()
    ap.add_argument('--port',type=int,default=cf.PORT)
    ap.add_argument('--host',default='auto',help='auto=127.0.0.1 ＋ Tailscale IP(預設,安全);或指定 IP。禁 0.0.0.0')
    ap.add_argument('--state',default=cf.LIVE)
    ap.add_argument('--allow-agent',action='store_true',
                    help='允許 AI 助理的瀏覽器寫入。只有沙箱板該開,正式看板一律不開。')
    a=ap.parse_args(); STATE=os.path.abspath(a.state); ALLOW_AGENT[0]=a.allow_agent
    first_run(STATE)
    tmp=None; srvs=[]; previous_sigterm=None
    try:
        if not is_real():
            tmp=sandbox_home()
            def stop_sandbox(_sig,_frame):
                raise SystemExit(0)
            previous_sigterm=signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM,stop_sandbox)
            os.environ['JOBSALVO_HOME']=tmp; cf.reload(tmp)
        hosts=resolve_hosts(a.host)
        for h in hosts:
            srvs.append(ThreadingHTTPServer((h,a.port),H))
        SERVERS[:]=srvs
        migrate_marks(STATE)   # 舊資料寫回成現在的樣子:自動流程第一次盤點、頁面第一次載入之前
        start_pilot()  # 所有埠先綁好，首次盤點仍在開始接受 HTTP 前完成
        RUNNING_VERSION=code_version()
        launchd=os.environ.get('JOBSALVO_LAUNCHD')=='1'
        if launchd:
            threading.Thread(target=_watch_code,args=(srvs,RUNNING_VERSION),daemon=True).start()
        for h in hosts:
            print(f'看板 server 起在 http://{"localhost" if h=="127.0.0.1" else h}:{a.port}  (狀態檔 {STATE})')
        for s in srvs[1:]:
            threading.Thread(target=s.serve_forever,daemon=True).start()
        srvs[0].serve_forever()
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM,previous_sigterm)
        for server in srvs:
            server.server_close()
        if PILOT is not None:
            PILOT.stop()   # 等自動流程正在跑的那一次寫完,再刪副本資料夾
        if tmp:
            # 刪的同時若還有東西寫進來(慢的機器上時間會重疊),會剩下空殼;再試幾次,真的刪不掉要講出來
            for _ in range(5):
                shutil.rmtree(tmp,ignore_errors=True)
                if not os.path.exists(tmp): break
                time.sleep(0.2)
            else:
                print('副本的暫存資料夾刪不掉,要手動清:'+tmp,file=sys.stderr)
    if launchd and code_version()!=RUNNING_VERSION:
        raise SystemExit(75)

if __name__=='__main__': main()
