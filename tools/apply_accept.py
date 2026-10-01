#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_accept —— 代投整條流程的驗收。真的派 agent、真的在 agent 專用的 Chrome 裡操作,對象是本機的假職缺表單
(apply_fakeform),看板用副本。證據全部來自程式自己看得到的地方,不採信 agent 的回報:
  · 假表單伺服器:頁面上每一格現在的值、是不是同一頁(inst、GET 次數)、分頁還在不在、有沒有被送出、送出了什麼。
  · 螢幕上的視窗清單(macOS CGWindowList,每 0.5 秒一次):agent 的分頁所在的視窗有沒有出現在螢幕上。
  · 看板副本:表單紀錄、答案庫、apply 紀錄、卡上「原因」有沒有被動;現行看板有沒有被碰到。

跑的順序跟實際用的一樣:
  1 填表     apply_run --stage fill(新的一段對話)
  2 補答案   照看板的做法寫進答案庫(「為什麼想做這份工作」是本人才能答的,agent 應該留空)→ 欄位標 refill
  3 修改     apply_run --stage fix(叫回同一段對話,在原本那一頁上改)
  4 沒核准就送 apply_run --stage submit → 應該連 agent 都不派
  5 核准送出 照看板的做法存核准快照 → apply_run --stage submit(叫回同一段對話,在同一頁送出)
每一條寫 ✅/❌ 和證據,存成 --out 資料夾裡的 report.md / report.json(外加每一步的截圖)。

用法:python3 tools/apply_accept.py [--out 資料夾]
要跑十幾分鐘(三輪 agent)。現行看板、真的可投遞夾都不會被動到。
驗「照母稿填」要知道母稿上的事實:設定的 accept.facts(name/email/phone/links,各一個清單);
沒寫就跳過那幾條。
"""
import os, sys, re, json, time, signal, hashlib, argparse, datetime, threading, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd          # noqa: E402
import config as cf             # noqa: E402
import card                     # noqa: E402
import form_record as fr        # noqa: E402
import delivery_state as ds     # noqa: E402
import ship                     # noqa: E402
from apply_fakeform import FakeForm, FIELDS   # noqa: E402
from apply_profile_accept import make_pdf     # noqa: E402

# 假職缺用兩份 PDF 組成可投遞夾,只會上傳到本機假表單。
MERGED = ship.MERGED_FILE
FACTS = (cf.C.get('accept') or {}).get('facts') or {}


def tiny_pdf(path, text='jobsalvo acceptance test'):
    """一頁、一行字的合法 PDF(不靠任何套件;跟平台履歷驗收同一份做法)。"""
    with open(path, 'wb') as f:
        f.write(make_pdf(text))
WHY_V = ('I want to do risk work where finding anomalies directly protects users, and this role pairs that with '
         'SQL-driven investigation. (acceptance-test answer)')
WHY_ZH = '我想做能直接保護使用者的風險工作,這個職位把它和用 SQL 查案結合在一起。(驗收用的測試答案)'
NOTE = '驗收用的假職缺,不要動這格'
SEED_ANS = {
    'nationality': {'k': 'nationality', 'q': 'What is your nationality?', 'v': 'Taiwanese', 'zh': '台灣',
                    'why': '驗收用的共用答案', 'kind': 'val'},
    'visa_sponsor': {'k': 'visa_sponsor', 'q': 'Will you require visa sponsorship to work in Taiwan?', 'v': 'No',
                     'zh': '不需要', 'why': '驗收用的共用答案', 'kind': 'pick'},
}

JXA_WINDOWS = r'''
ObjC.import("CoreGraphics"); ObjC.import("Foundation");
const a = ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, 0));
const o = [];
for (let i = 0; i < a.count; i++) { const w = a.objectAtIndex(i);
  if (ObjC.unwrap(w.objectForKey("kCGWindowLayer")) !== 0) continue;
  const b = w.objectForKey("kCGWindowBounds");
  o.push([ObjC.unwrap(w.objectForKey("kCGWindowOwnerName")), ObjC.unwrap(w.objectForKey("kCGWindowNumber")),
          ObjC.unwrap(b.objectForKey("X")), ObjC.unwrap(b.objectForKey("Y")), ObjC.unwrap(b.objectForKey("Width")), ObjC.unwrap(b.objectForKey("Height")),
          ObjC.unwrap(w.objectForKey("kCGWindowOwnerPID"))]); }
JSON.stringify(o)'''


class Screen(threading.Thread):
    """每 0.5 秒記一次螢幕上看得到的視窗(app、編號、位置大小、哪個程序)和前台 app。只記變化。
    agent 的 Chrome 是自己一個程序:它的視窗一出現在螢幕上(不是他打開總覽時),當場把整個 agent Chrome 藏起來並記下來
    (這是驗收時保護他畫面的保險,不是修法;藏起來之後 Chrome 不畫畫面,後面的截圖會跟著失敗)。"""

    def __init__(self, out):
        super().__init__(daemon=True)
        self.out = out
        self.snaps, self.stop_ev, self.hid, self.overview = [], threading.Event(), [], []
        self.dock_at = 0

    def sample(self):
        try:
            w = json.loads(subprocess.run(['osascript', '-l', 'JavaScript', '-e', JXA_WINDOWS],
                                          capture_output=True, text=True, timeout=5).stdout or '[]')
        except (OSError, subprocess.SubprocessError, ValueError):   # 問不到視窗清單:這一次取樣當成沒有
            w = []
        try:
            asn = subprocess.run(['lsappinfo', 'front'], capture_output=True, text=True, timeout=3).stdout.strip()
            nm = subprocess.run(['lsappinfo', 'info', '-only', 'name', '-only', 'pid', asn],
                                capture_output=True, text=True, timeout=3).stdout
            front = (re.search(r'"LSDisplayName"="(.*?)"', nm) or [None, ''])[1]
            fpid = int((re.search(r'"pid"=(\d+)', nm) or [None, 0])[1])
        except (OSError, subprocess.SubprocessError, ValueError):   # 問不到最前面的 app:這一次取樣當成沒有
            front, fpid = '', 0
        return front, [tuple(x) for x in w], fpid

    def run(self):
        import agent_chrome
        last = None
        while not self.stop_ev.is_set():
            s = self.sample()
            if any(x[0] == 'Dock' for x in s[1]):
                self.dock_at = time.time()
            agent = agent_chrome.pid()
            seen = [x for x in s[1] if agent and len(x) > 6 and x[6] == agent]
            overview = False
            if seen:
                # 他自己打開總覽(Mission Control)時,所有視窗都會縮小排出來、Dock 蓋滿螢幕;那不是 agent 跳出來
                # (總覽開、關各有一段動畫,所以前後兩秒內看過 Dock 都算)
                for _ in range(4):
                    if any(x[0] == 'Dock' for x in s[1]) or time.time() - self.dock_at < 2:
                        break
                    time.sleep(0.5)
                    s = self.sample()
                    if any(x[0] == 'Dock' for x in s[1]):
                        self.dock_at = time.time()
                overview = time.time() - self.dock_at < 2
                if overview:
                    self.overview.append((time.time(), seen))
                else:
                    # 藏之前先把那幾個視窗各截一張(只存在驗收資料夾),事後才知道跳出來的是什麼
                    shots = []
                    for x in seen:
                        f = os.path.join(self.out, f'跳出來-{int(time.time())}-{x[1]}.png')
                        subprocess.run(['screencapture', '-x', '-o', f'-l{x[1]}', f], capture_output=True)
                        shots.append(f if os.path.exists(f) else None)
                    agent_chrome._running_app('a.hide', agent)
                    self.hid.append((time.time(), seen, shots))
            s = s + (agent, overview)
            if s != last:
                self.snaps.append((time.time(), s))
                last = s
            time.sleep(0.5)

    def stop(self):
        self.stop_ev.set()


class Accept:
    def __init__(self, out):
        self.out = out
        os.makedirs(out, exist_ok=True)
        self.checks, self.log = [], []

    # ---- 紀錄 ----
    def ok(self, step, what, cond, evidence=''):
        self.checks.append({'step': step, 'what': what, 'ok': bool(cond), 'evidence': str(evidence)[:400]})
        print(('  ✅ ' if cond else '  ❌ ') + what + (f' — {str(evidence)[:160]}' if evidence else ''), flush=True)
        return bool(cond)

    def say(self, s):
        self.log.append(s)
        print(s, flush=True)

    # ---- 準備:假表單、看板副本、假的可投遞夾 ----
    def setup(self):
        self.srv = FakeForm().start()
        self.url = self.srv.url('accept')
        self.board = os.path.join(self.out, 'board-accept.html')
        bd.copy_board(bd.LIVE, self.board)
        live_fb = json.loads(bd.load(bd.LIVE)['fb'])
        self.live_ans = len(live_fb.get('__ans__', []))
        today = datetime.date.today().isoformat()

        def add_job(data, fb):
            data['jobs'].append({'id': self.url, 'cat': '驗收', 'target': 'Acceptance Test Co · Risk Analyst(驗收用假職缺)',
                                 'chan': '直投', 'ammo': '', 'note': '', 'added': today})
        bd.set_data(add_job, live=self.board)
        def seed(fb):
            fb[self.url] = {'app': 'ship', 's': 'like', 'n': NOTE}
            # 他確認過的共用答案(國籍、簽證):驗收自己放,不靠現行看板剛好有這兩條
            bank = [e for e in fb.setdefault('__ans__', []) if e.get('k') not in SEED_ANS]
            fb['__ans__'] = bank + [dict(e, at=today) for e in SEED_ANS.values()]
        bd.set_fb(seed, live=self.board, by='apply_accept')
        # 假的可投遞夾:兩份個別檔 + 程式合成的 PDF + ship.json
        self.ship_root = os.path.join(self.out, 'ship')
        dst = os.path.join(self.ship_root, 'Acceptance-Test-Risk-Analyst-' + card.card_id_from_url(self.url))
        os.makedirs(dst, exist_ok=True)
        resume_file, attachment_file = 'acceptance-base.pdf', 'acceptance-supporting-document.pdf'
        tiny_pdf(os.path.join(dst, resume_file), 'acceptance resume')
        tiny_pdf(os.path.join(dst, attachment_file), 'acceptance attachment')
        var = next((rid for rid, item in cf.RESUMES.items() if item.get('enabled', True)), '')
        problems = ship._write_merged(dst, {
            'variant': var, 'lang': 'en', 'files': [resume_file, attachment_file],
        })
        if problems:
            raise RuntimeError('無法建立假表單的合併檔: ' + '；'.join(problems))
        with open(os.path.join(dst, MERGED), 'rb') as f:
            self.pdf_sha1 = hashlib.sha1(f.read()).hexdigest()
        self.env = dict(os.environ, APPLY_TMP=os.path.join(self.out, 'sp'), APPLY_SHIP_ROOT=self.ship_root)
        os.makedirs(self.env['APPLY_TMP'], exist_ok=True)
        self.screen = Screen(self.out)
        self.base_front, self.base_wins, _ = self.screen.sample()
        self.screen.start()
        self.say(f'假職缺:{self.url}\n看板副本:{self.board}')

    def fb(self):
        return json.loads(bd.load(self.board)['fb'])

    def run(self, stage, *extra):
        t0 = time.time()
        p = subprocess.run([sys.executable, os.path.join(HERE, 'apply_run.py'), '--stage', stage, '--url', self.url,
                            '--board', self.board] + list(extra), cwd=cf.HOME, env=self.env, capture_output=True, text=True)
        out = (p.stdout + p.stderr).strip()
        self.say(f'  [{stage}] {round(time.time() - t0)} 秒:{out[-300:]}')
        return t0, out

    def apply_out(self):
        import apply_run
        return apply_run.out_dir(self.url, self.board, self.env['APPLY_TMP'])

    def session_of(self, stage):
        import agent_run
        return agent_run.session_id(os.path.join(self.apply_out(), stage + '.log'))

    def keep_shot(self, name, step=None):
        """跟看板「👀 看現在的頁面」走同一條路(apply_tab._lookup 照看板的紀錄找那一頁)截一張;
        step 給了就當一條檢查:他在外面要看得到 agent 填好的那一頁。"""
        import apply_tab
        err = ''
        try:
            session, tab, door = apply_tab._lookup(self.url, self.board)
            door.shot(session, tab, os.path.join(self.out, name + '.jpg'))
        except Exception as e:  # noqa: BLE001 — 各家門路丟的例外不一樣;截不到照實印進驗收紀錄
            err = str(e)[:120]
            self.say(f'  (截不到 {name}:{err})')
        if step is not None:
            self.ok(step, '「👀 看現在的頁面」截得到那一頁', not err, err or name + '.jpg')

    # ---- 共同的檢查 ----
    def no_submit_since(self, step, t0):
        s = self.srv.submits(t0)
        self.ok(step, '這一輪伺服器沒收到任何送出', not s, f'{len(s)} 次')

    def page(self):
        return self.srv.latest(self.inst) if getattr(self, 'inst', None) else self.srv.latest()

    def tab_alive(self, step, t_end):
        time.sleep(4)
        mine = [b for b in self.srv.beacons(t_end - 1) if b.get('inst') == self.inst]
        hidden = [b for b in mine if b.get('ev') == 'hide']
        self.ok(step, 'agent 結束後那一頁還開著(頁面還在回報、沒有關掉的訊號)', mine and not hidden,
                f'結束後收到 {len(mine)} 次回報,關閉訊號 {len(hidden)} 次')

    def same_page(self, step, t0):
        loads = [b for b in self.srv.beacons(t0) if b.get('ev') == 'load']
        new = sorted({b['inst'] for b in self.srv.beacons(t0)} - {self.inst})
        self.ok(step, '在原本那一頁上做(沒有重新載入、沒有開新分頁)', not loads and not new,
                f'這一輪新的載入 {len(loads)} 次、新的頁面 {new}')

    # ---- 1 填表 ----
    def step_fill(self):
        self.say('\n1 填表(新的一段對話)')
        t0, out = self.run('fill')
        t_end = time.time()
        fb = self.fb()
        a = (fb.get(self.url) or {}).get('apply') or {}
        self.sid = a.get('session')
        self.ok(1, 'apply_run 判定填好了(包含程式自己讀那一頁對答案庫)', ds.state(fb.get(self.url)) == 'parked', a.get('issues'))
        self.ok(1, '看板記下了那段對話和分頁', self.sid and a.get('tab_id'), f"session={self.sid} tab={a.get('tab_id')}")
        loads = [b for b in self.srv.beacons(t0) if b.get('ev') == 'load']
        self.inst = loads[-1]['inst'] if loads else None
        self.ok(1, '這一輪只載入一次申請表', len(loads) == 1, f'{len(loads)} 次')
        self.no_submit_since(1, t0)
        b = self.page() or {}
        v = b.get('vals') or {}
        self.after_fill = dict(v)
        for k, want in FACTS.items():
            self.ok(1, f'「{k}」照母稿填', v.get(k) in want, repr(v.get(k)))
        self.ok(1, '「Current location」有填', (v.get('location') or '').strip(), repr(v.get('location')))
        bank = {e['k']: e for e in fb.get('__ans__', [])}
        self.ok(1, '國籍用答案庫那一條(nationality)', v.get('nationality') == (bank.get('nationality') or {}).get('v'),
                repr(v.get('nationality')))
        self.ok(1, '簽證用答案庫那一條(visa_sponsor)', v.get('visa') == (bank.get('visa_sponsor') or {}).get('v'), repr(v.get('visa')))
        files = v.get('resume') or []
        self.ok(1, '上傳欄選了這張指定的合併版 PDF', any(f.get('n') == MERGED for f in files), files)
        # 答案庫:年資、「為什麼」都是推論的新答案(標 inf、附中文);「為什麼」是這缺專用
        form = (fb.get(self.url) or {}).get('form') or {}
        fq = {x.get('q', ''): x for x in form.get('f', [])}
        yx = next((x for q, x in fq.items() if 'years' in q.lower()), None)
        ye = bank.get((yx or {}).get('k')) or {}
        self.ok(1, '年資有填,答案庫多一條 agent 推論的(標「我推論的」、附中文翻譯)',
                (v.get('years') or '').strip() and ye.get('inf') and ye.get('zh'),
                f"頁面 {v.get('years')!r};答案庫 {ye.get('k')} inf={ye.get('inf')} zh={str(ye.get('zh'))[:30]!r}")
        wx = next((x for q, x in fq.items() if 'why' in q.lower()), None)
        we = bank.get((wx or {}).get('k')) or {}
        self.why_k = (wx or {}).get('k')
        self.ok(1, '「為什麼想做這份工作」幫他填好了,答案庫那條標「我推論的」、這缺專用,等他改',
                (v.get('why') or '').strip() and wx and wx.get('src') == 'bank' and we.get('inf') and we.get('pj')
                and ' '.join((v.get('why') or '').split()) == ' '.join((we.get('v') or '').split()),
                f"頁面 {str(v.get('why'))[:40]!r};答案庫 {we.get('k')} inf={we.get('inf')} pj={we.get('pj')}")
        self.ok(1, '卡上「原因」沒被動', (fb.get(self.url) or {}).get('n') == NOTE, repr((fb.get(self.url) or {}).get('n')))
        self.ok(1, '答案庫格式沒壞(英文都有中文、沒有指到不存在的答案)', not fr.validate(fb), fr.validate(fb)[:3])
        self.tab_alive(1, t_end)
        self.keep_shot('1-填好', 1)

    # ---- 2 他補答案 ----
    def step_answer(self):
        self.say('\n2 在答案庫確認 agent 推論的答案、補上「為什麼想做這份工作」(測試用答案)')
        k = self.why_k
        today = datetime.date.today().isoformat()

        def mut(fb):
            ks = {x.get('k') for x in fb[self.url]['form'].get('f', []) if x.get('src') == 'bank'}
            for e in fb['__ans__']:            # 看板的「確認」:推論的變成他確認過的,值不變
                if e.get('k') in ks and e.get('inf') and e.get('k') != k:
                    e['at'] = today
                    e.pop('inf', None)
            e = next(e for e in fb['__ans__'] if e['k'] == k)
            e.update(v=WHY_V, zh=WHY_ZH, at=today)
            e.pop('inf', None)
            fr.mark_refill(fb, k)              # 看板的 ansRefill
        if not k:
            return self.ok(2, '表單裡有「為什麼」那一欄可以補', False, '填表時沒記這一欄')
        bd.set_fb(mut, live=self.board, by='apply_accept')
        fb = self.fb()
        st = fr.board_status(self.board)
        self.ok(2, '網頁還是舊的時候不能核准', fr.approval_problem(fb, self.url, st) is not None, fr.approval_problem(fb, self.url, st))

    # ---- 3 修改 ----
    def step_fix(self):
        self.say('\n3 修改(叫回同一段對話,在原本那一頁上改)')
        t0, out = self.run('fix')
        fb = self.fb()
        a = (fb.get(self.url) or {}).get('apply') or {}
        self.ok(3, '叫回的是同一段對話', self.session_of('fix') == self.sid and a.get('session') == self.sid,
                f"fix.log {self.session_of('fix')} / 看板 {a.get('session')}")
        self.ok(3, 'apply_run 判定改好了', a.get('stage') == 'fix' and ds.state(fb.get(self.url)) == 'parked', a.get('issues'))
        self.same_page(3, t0)
        self.no_submit_since(3, t0)
        v = (self.page() or {}).get('vals') or {}
        self.ok(3, '「為什麼」現在是他寫的答案', ' '.join((v.get('why') or '').split()) == ' '.join(WHY_V.split()), repr(v.get('why'))[:80])
        moved = {k: (self.after_fill.get(k), v.get(k)) for k in self.after_fill if k != 'why' and self.after_fill.get(k) != v.get(k)}
        self.ok(3, '其他欄位沒被動', not moved, moved)
        self.ok(3, '待重打的標記清掉了', not fr.find_refills({self.url: fb[self.url]}), fr.find_refills({self.url: fb[self.url]}))
        self.tab_alive(3, time.time())
        self.keep_shot('3-改好', 3)

    # ---- 4 沒核准就送 ----
    def step_unapproved(self):
        self.say('\n4 沒核准就按送出')
        sub = os.path.join(self.apply_out(), 'submit.log')
        t0, out = self.run('submit')
        self.ok(4, '沒核准:連 agent 都沒派', not os.path.exists(sub) and '沒有要跑的卡' in out, out[-80:])
        self.no_submit_since(4, t0)

    # ---- 5 核准送出 ----
    def step_submit(self):
        self.say('\n5 他核准 → 同一段對話在同一頁送出')

        def approve(fb):                      # 看板的「✅ 確認送出」:照狀態表送確認事件
            ds.fire(fb, self.url, 'confirm', approve=fr.approval(fb, self.url, datetime.datetime.now().isoformat(timespec='seconds')))
        bd.set_fb(approve, live=self.board, by='apply_accept')
        fb = self.fb()
        st = fr.board_status(self.board)
        self.ok(5, '核准有效', fr.approval_problem(fb, self.url, st) is None, fr.approval_problem(fb, self.url, st))
        snap = fr.snapshot(fb, self.url)
        t0, out = self.run('submit')
        fb = self.fb()
        m = fb.get(self.url) or {}
        self.ok(5, '叫回的是同一段對話', self.session_of('submit') == self.sid, self.session_of('submit'))
        subs = self.srv.submits(t0)
        self.ok(5, '伺服器剛好收到一次送出', len(subs) == 1, f'{len(subs)} 次')
        self.same_page(5, t0)
        if subs:
            got = subs[0]['fields']
            byq = {q: got.get(n) for n, q, _ in FIELDS}
            diff = {q: (snap[q], byq.get(q)) for q in snap if q in byq and ' '.join(str(snap[q]).split()) != ' '.join(str(byq.get(q) or '').split())}
            self.ok(5, '送出去的每一格 = 他核准的快照', not diff, diff or f'{len([q for q in snap if q in byq])} 格都一樣')
            fl = (subs[0]['files'] or {}).get('resume') or {}
            self.ok(5, '送出去的檔就是指定的那份 PDF(sha1 一樣)', fl.get('sha1') == self.pdf_sha1, f"{fl.get('n')} {fl.get('sha1')}")
        self.ok(5, '看板搬到「已投遞」、表單鎖住、留了確認頁證據',
                m.get('app') == 'sent' and (m.get('form') or {}).get('lock') and (m.get('apply') or {}).get('sent'),
                (m.get('apply') or {}).get('sent'))
        self.ok(5, '卡上「原因」沒被動', m.get('n') == NOTE, repr(m.get('n')))

    # ---- 全程 ----
    def step_global(self):
        self.say('\n全程')
        self.screen.stop()
        # agent 的 Chrome 是自己一個程序:用程序編號認它的視窗,不再用位置猜
        on = [(round(t, 1), w) for t, (_, ws, _, agent, overview) in self.screen.snaps for w in ws
              if agent and not overview and len(w) > 6 and w[6] == agent]
        self.ok('全程', 'agent 的 Chrome 從頭到尾沒有任何視窗出現在他的螢幕上', not on and not self.screen.hid,
                f'出現過 {len(on)} 次' + (f',第一次:{on[0]}' if on else '') +
                (f';驗收當場藏起來 {len(self.screen.hid)} 次' if self.screen.hid else ''))
        for _, ws, shots in self.screen.hid:
            self.say(f'  (跳出來的視窗 {ws} → 截圖 {shots})')
        if self.screen.overview:
            self.say(f'  (他打開總覽(Mission Control)時看得到 agent 的視窗 {len(self.screen.overview)} 次,不算跳出來)')
        fronts = sorted({fp for _, (_, _, fp, agent, _) in self.screen.snaps if agent and fp == agent})
        self.ok('全程', '前台 app 從來沒變成 agent 的 Chrome', not fronts,
                f'開始時 {self.base_front};agent 的 Chrome 當過前台 {len(fronts)} 次')
        live = json.loads(bd.load(bd.LIVE)['fb'])
        self.ok('全程', '現行看板沒被碰到', self.url not in live and len(live.get('__ans__', [])) == self.live_ans,
                f"答案庫 {self.live_ans} → {len(live.get('__ans__', []))} 條")
        inbox = [it for it in self.fb().get('__inbox__', []) if it.get('job') == self.url]
        self.say(f'  (agent 回報 {len(inbox)} 則:' + '; '.join(it.get('msg', '')[:60] for it in inbox) + ')')

    def report(self):
        n_ok = sum(c['ok'] for c in self.checks)
        lines = [f'# 代投驗收 {datetime.datetime.now():%Y-%m-%d %H:%M}', '',
                 f'{n_ok}/{len(self.checks)} 條通過。假職缺 {self.url}(本機測試表單),看板副本,agent 在它專用的 Chrome 裡操作。', '']
        step = None
        for c in self.checks:
            if c['step'] != step:
                step = c['step']
                lines.append(f'\n## {step}')
            lines.append(f"- {'✅' if c['ok'] else '❌'} {c['what']}" + (f"(證據:{c['evidence']})" if c['evidence'] else ''))
        with open(os.path.join(self.out, 'report.md'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        with open(os.path.join(self.out, 'report.json'), 'w', encoding='utf-8') as f:
            json.dump({'checks': self.checks, 'log': self.log, 'events': self.srv.events(),
                       # 螢幕時間軸:(時間, (前台 app, 螢幕上的視窗[app, 編號, x, y, 寬, 高, 程序], 前台程序, agent Chrome 程序))
                       'screen': self.screen.snaps, 'hid': self.screen.hid},
                      f, ensure_ascii=False, indent=1, default=str)
        self.say(f'\n{n_ok}/{len(self.checks)} 條通過 → {self.out}/report.md')
        return n_ok == len(self.checks)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=os.path.join('/tmp', 'apply-accept-' + time.strftime('%m%d-%H%M')))
    a = ap.parse_args()
    acc = Accept(os.path.abspath(a.out))
    # 被中途停掉(Ctrl-C、kill)也要收尾:下面的 finally 會照看板把 agent 的 Chrome 收掉,不留測試頁在他電腦上
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    acc.setup()
    try:
        acc.step_fill()
        if acc.sid and acc.inst:
            acc.step_answer()
            acc.step_fix()
            acc.step_unapproved()
            acc.step_submit()
        else:
            acc.say('填表沒有留下對話或頁面,後面幾步跑不了')
    finally:
        acc.step_global()
        ok = acc.report()
        acc.srv.stop()
        import agent_chrome
        acc.say('收尾:' + agent_chrome.close_if_idle(bd.LIVE))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
