#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr_preview —— 每個 PR 一個預覽看板(#809,ADR 0014)。

挑一個開著的 PR,就另外起一個看板:跑那個 PR 推上去的最新 commit,用真實資料的副本。正式看板不動。
PR 都開在私人的開發 repo(REPO);這台電腦登入的 GitHub 帳號能推送它才有這個功能,其他人看不到、不用設定。

預覽跑的是 PR 分支自己的看板伺服器(舊分支沒有這支程式),所以一切都在這邊做:抓程式、複製看板、挑埠號、
記錄、清理。預覽那邊只用早就有的 --state(看板不是正式那一份 → 它自己當副本:設定、履歷照副本規則從真的
資料夾複製,連外、派 agent、送出不做真的事)與 --port。
"""
import contextlib, json, os, re, shutil, signal, socket, subprocess, sys, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import board_doc as bd

REPO = 'GongYuanCaiJi/jobsalvo-dev'   # 開發和 PR 都在這裡,公開 repo 只是鏡像(ADR 0002)
WRITE = ('ADMIN', 'MAINTAIN', 'WRITE')
READY_TIMEOUT = 300          # 第一次要裝套件,給久一點
_LOCK = threading.Lock()
_JOBS = {}                   # PR 編號 → 這次開預覽做到哪:{'state': preparing/failed, 'msg'}
_PERM = {}                   # repo → (查的時間, 能不能推送);每次開頁都問 GitHub 太慢
_GEN = {}                    # PR 編號 → 第幾次準備;關掉或 PR 關了就加一,還在準備的那一輪看到就不登記
_PROCS = {}                  # 這個看板起的預覽行程:停掉後要收回,不然留下殭屍行程


def root():
    return os.path.join(os.path.expanduser('~/.cache/jobsalvo/pr-preview'), cf.folder_tag())


def remote_url(r):
    return f'https://github.com/{r}.git'


def gh(*args):
    return subprocess.run(['gh', *args], capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, GH_PROMPT_DISABLED='1'))


def git(*args, cwd=None):
    # 帳密一律交給 GitHub CLI 的登入,不跳帳密提示
    return subprocess.run(['git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential', *args],
                          cwd=cwd, capture_output=True, text=True, timeout=600,
                          env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))


def enabled():
    """有沒有 PR 預覽:有 gh、登入的帳號能推送開發 repo。"""
    r = REPO
    if not shutil.which('gh'):
        return False
    hit = _PERM.get(r)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        p = gh('repo', 'view', r, '--json', 'viewerPermission', '-q', '.viewerPermission')
        ok = p.returncode == 0 and p.stdout.strip() in WRITE
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _PERM[r] = (time.time(), ok)
    return ok


# ---- 在跑的預覽:記在檔裡,正式看板重開後還認得 ----
def _reg_path():
    return os.path.join(root(), 'previews.json')


def _load():
    try:
        with open(_reg_path(), encoding='utf-8') as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(reg):
    os.makedirs(root(), exist_ok=True)
    tmp = _reg_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(reg, f)
    os.replace(tmp, _reg_path())


def _answers(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as r:
            return json.load(r).get('app') == 'jobsalvo'
    except (OSError, ValueError):
        return False


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _stop(n, remove=False):
    """關掉 PR n 的預覽;remove:連它的程式資料夾一起刪(PR 已經不是開著的)。只停真的是這份預覽的行程。"""
    reg = _load()
    entry = reg.pop(str(n), None)
    _save(reg)
    wt = os.path.join(root(), f'pr-{n}')
    if entry and entry.get('pid'):
        ps = subprocess.run(['ps', '-o', 'command=', '-p', str(entry['pid'])], capture_output=True, text=True)
        if wt in ps.stdout:
            with contextlib.suppress(OSError):   # 已經停了
                os.killpg(entry['pid'], signal.SIGTERM)
    proc = _PROCS.pop(n, None)
    if proc:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
    shutil.rmtree(os.path.join(root(), f'data-{n}'), ignore_errors=True)
    if remove:
        git('worktree', 'remove', '--force', wt, cwd=os.path.join(root(), 'repo'))
        shutil.rmtree(wt, ignore_errors=True)
        with _LOCK:
            _JOBS.pop(n, None)


def _state(n, entry):
    if entry and _answers(entry['port']):
        return {'state': 'ready', 'port': entry['port'], 'sha': entry.get('sha', '')}
    with _LOCK:
        job = dict(_JOBS.get(n) or {})
    return job or {'state': 'off'}


def states():
    """不問 GitHub,只回這台電腦上每份預覽的狀態(頁面準備中時輪詢用)。"""
    reg = _load()
    ns = {int(k) for k in reg} | set(_JOBS)
    return {str(n): _state(n, reg.get(str(n))) for n in ns}


def list_prs():
    """開著的 PR(只有同一個 repo 的,fork 來的不列)與各自的預覽;已經不是開著的 PR 的預覽順手清掉。"""
    p = gh('pr', 'list', '-R', REPO, '--state', 'open', '--limit', '100',
           '--json', 'number,title,headRefName,headRefOid,updatedAt,url,body,isCrossRepository')
    if p.returncode != 0:
        raise RuntimeError('讀不到 PR 清單:' + (p.stderr or p.stdout).strip()[:200])
    prs = [x for x in json.loads(p.stdout or '[]') if not x.get('isCrossRepository')]
    open_ns = {x['number'] for x in prs}
    with _LOCK:
        mine = {int(k) for k in _load()} | set(_JOBS)
    for n in mine - open_ns:
        # 清單有上限,沒列到不等於關了:問過 GitHub 確定不是開著的才清
        v = gh('pr', 'view', str(n), '-R', REPO, '--json', 'state')
        if v.returncode == 0 and json.loads(v.stdout).get('state') != 'OPEN':
            _cancel(n)
            _stop(n, remove=True)
    reg = _load()
    return [{'number': x['number'], 'title': x['title'], 'branch': x['headRefName'], 'sha': x['headRefOid'],
             'updated': x['updatedAt'], 'url': x['url'], 'diff': x['url'] + '/files', 'summary': _summary(x.get('body')),
             'preview': _state(x['number'], reg.get(str(x['number'])))} for x in prs]


def _summary(text):
    """PR 內文開頭幾句白話:拿掉標題列、程式碼區塊、表格、圖片和 Closes 那一行。"""
    text = re.sub(r'```.*?```', ' ', text or '', flags=re.S)
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith(('#', '|', '!['))
             and not re.match(r'\s*(Closes|Fixes|Resolves) #\d+', line, re.I)]
    s = re.sub(r'\s+', ' ', ' '.join(lines)).strip()
    return s[:160] + ('…' if len(s) > 160 else '')


def open_preview(n):
    """開 PR n 的預覽:同一個 commit 還在跑就直接給;不然在背景準備,先回準備中。"""
    p = gh('pr', 'view', str(n), '-R', REPO, '--json', 'number,title,url,headRefOid,state,isCrossRepository')
    if p.returncode != 0:
        raise RuntimeError('讀不到這個 PR:' + (p.stderr or p.stdout).strip()[:200])
    pr = json.loads(p.stdout)
    if pr.get('state') != 'OPEN' or pr.get('isCrossRepository'):
        raise ValueError('只有這個 repo 開著的 PR 能預覽')
    entry = _load().get(str(n))
    if entry and entry.get('sha') == pr['headRefOid'] and _answers(entry['port']):
        return _state(n, entry)
    with _LOCK:
        if (_JOBS.get(n) or {}).get('state') == 'preparing':
            return dict(_JOBS[n])
        _JOBS[n] = {'state': 'preparing', 'msg': '準備中:抓程式、複製資料、起預覽'}
        gen = _GEN[n] = _GEN.get(n, 0) + 1
    threading.Thread(target=_prepare, args=(n, pr, gen), daemon=True).start()
    return dict(_JOBS[n])


def _cancel(n):
    with _LOCK:
        _GEN[n] = _GEN.get(n, 0) + 1
        _JOBS.pop(n, None)


def close_preview(n):
    _cancel(n)
    _stop(n)


def _prepare(n, pr, gen):
    def _fail(msg):
        with _LOCK:
            if _GEN.get(n) == gen:
                _JOBS[n] = {'state': 'failed', 'msg': msg}
    try:
        _stop(n)
        base = root()
        clone = os.path.join(base, 'repo')
        if not os.path.isdir(os.path.join(clone, '.git')):
            shutil.rmtree(clone, ignore_errors=True)
            os.makedirs(base, exist_ok=True)
            r = git('clone', '-q', '--filter=blob:none', '--no-checkout', remote_url(REPO), clone)
            if r.returncode != 0:
                return _fail('抓不到程式:' + r.stderr.strip()[-200:])
        r = git('fetch', '-q', 'origin', f'+refs/pull/{n}/head:refs/preview/{n}', cwd=clone)
        if r.returncode != 0:
            return _fail('抓不到這個 PR 的程式:' + r.stderr.strip()[-200:])
        sha = git('rev-parse', f'refs/preview/{n}', cwd=clone).stdout.strip()
        wt = os.path.join(base, f'pr-{n}')
        r = (git('checkout', '-q', '--detach', '--force', sha, cwd=wt) if os.path.isdir(wt) else
             git('worktree', 'add', '-q', '--detach', wt, sha, cwd=clone))
        if r.returncode != 0:
            return _fail('換不到這個 PR 的程式:' + r.stderr.strip()[-200:])
        data = os.path.join(base, f'data-{n}')
        os.makedirs(data, exist_ok=True)
        state = os.path.join(data, 'board.html')
        bd.copy_board(cf.LIVE, state)            # 真實資料的副本;設定、履歷由預覽那邊照副本規則複製
        uv = shutil.which('uv')
        if not uv:
            return _fail('這台電腦沒有 uv,裝不了這個 PR 的套件')
        port = _free_port()
        env = {k: v for k, v in os.environ.items() if k not in ('JOBSALVO_LAUNCHD', 'VIRTUAL_ENV')}
        env.update(JOBSALVO_HOME=cf.HOME,
                   JOBSALVO_PREVIEW=json.dumps({'number': n, 'title': pr['title'], 'url': pr['url']}, ensure_ascii=False))
        with open(os.path.join(base, f'pr-{n}.log'), 'ab') as log:
            proc = subprocess.Popen([uv, 'run', '--frozen', '--no-dev', '--project', wt, 'python',
                                     os.path.join(wt, 'tools', 'board_server.py'), '--state', state, '--port', str(port)],
                                    cwd=wt, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + READY_TIMEOUT
        while not _answers(port):
            if proc.poll() is not None or time.monotonic() > deadline:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                proc.wait()
                with open(os.path.join(base, f'pr-{n}.log'), encoding='utf-8', errors='replace') as f:
                    tail = f.read()[-300:].strip()
                return _fail('預覽起不來:' + (tail or '沒有輸出'))
            time.sleep(0.3)
        with _LOCK:
            if _GEN.get(n) == gen:          # 準備中被關掉(或 PR 關了):不登記,起好的也停掉
                reg = _load()
                reg[str(n)] = {'sha': sha, 'port': port, 'pid': proc.pid}
                _PROCS[n] = proc
                _save(reg)
                _JOBS.pop(n, None)
                return
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait()
        shutil.rmtree(data, ignore_errors=True)
    except Exception as e:  # noqa: BLE001 — 失敗原因照實給清單看,正式看板照常跑
        _fail(f'準備失敗:{type(e).__name__}: {str(e)[:200]}')
