#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update —— 設定頁的「更新」:跟 main 最新的那一版。

裝好的程式就是 main 的一份 clone(install.sh 重跑時 git pull --ff-only 也是同一件事)。按鈕發現遠端 main
比這份新,就顯示「有新版」;按下去 = fetch + 快轉 main。不發版、不貼標籤,合進 main 的東西朋友按一下就拿到。
為什麼不做「正式版」見 docs/adr/0001-update-follows-main.md。

  uv run python tools/update.py            # 看現在是哪一版、遠端有沒有新的、能不能更新
  uv run python tools/update.py --apply    # 更新到遠端 main 最新

檢查每小時最多問一次遠端(結果存在設定的 tmp,不寫進看板檔;以前記一整天,當天合進 main 的修正整天看不到)。git 一律不准跳出帳密提示、60 秒逾時:
伺服器裡的網頁請求卡在帳密提示上就永遠不回了。
"""
import os, json, time, shutil, subprocess, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
BRANCH = 'main'
CHECK_EVERY = 3600


def _git(*args, cwd=APP, timeout=60):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    return subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True,
                          timeout=timeout, env=env)


def _label(cwd, rev):
    """一版怎麼叫:短碼加日期(例:a1b2c3d · 2026-09-27)。讀不到回空字串。"""
    try:
        r = _git('log', '-1', '--format=%h · %cs', rev, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return r.stdout.strip() if r.returncode == 0 else ''


def current(cwd=APP):
    return _label(cwd, 'HEAD')


def latest_sha(cwd=APP):
    """遠端 main 最新那一版的完整碼;查不到(沒網路、沒權限)回 None。"""
    try:
        r = _git('ls-remote', '--heads', 'origin', BRANCH, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.split()[0]


def _has(cwd, sha):
    """這份程式裡已經有那一版(一樣新,或比它還新)。"""
    try:
        r = _git('merge-base', '--is-ancestor', sha, 'HEAD', cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def blocker(cwd=APP):
    """不能從看板更新的原因;可以更新回空字串。"""
    if not os.path.exists(os.path.join(cwd, '.git')):
        return '這份程式不是用 git 裝的,重新跑一次安裝指令來更新'
    try:
        branch = _git('rev-parse', '--abbrev-ref', 'HEAD', cwd=cwd).stdout.strip()
        dirty = _git('status', '--porcelain', '--untracked-files=no', cwd=cwd).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return '讀不到程式資料夾的 git 狀態'
    if branch != BRANCH:
        return f'程式資料夾切在 {branch} 分支,不是 {BRANCH},用 git 自己更新'
    if dirty:
        return '程式資料夾有還沒提交的變更,先處理再更新'
    return ''


def _cache_path():
    try:
        import config as cf
        return os.path.join(cf.TMP, 'update-check.json')
    except Exception:
        return ''


def check(cwd=APP, cache=True, now=None, force=False):
    """{current, latest, new, blocked, checked};latest 為 None 表示這次查不到遠端,checked 是那個答案是幾點查的。
    force:設定頁按了「現在檢查」,不看記著的答案、當場問遠端,問到的照樣記下來。"""
    now = time.time() if now is None else now
    path = _cache_path() if cache else ''
    sha, fresh, checked = None, False, now
    if path and not force and os.path.isfile(path):
        try:
            saved = json.load(open(path, encoding='utf-8'))
            if now - saved.get('at', 0) < CHECK_EVERY and saved.get('cwd') == cwd:
                sha, fresh, checked = saved.get('sha'), True, saved.get('at', now)
        except (OSError, ValueError):   # 快取壞了就當沒有,底下直接問遠端
            pass
    if not fresh:
        sha = latest_sha(cwd)
        if path and sha:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                json.dump({'sha': sha, 'at': now, 'cwd': cwd}, open(path, 'w', encoding='utf-8'))
            except OSError:   # 快取寫不進去只是下次多問一次遠端,結果照樣回給畫面
                pass
    new = bool(sha) and not _has(cwd, sha)
    # 遠端那一版還沒抓下來時讀不到日期,先只給短碼
    latest = None if sha is None else (_label(cwd, sha) or sha[:7])
    return {'current': current(cwd), 'latest': latest, 'new': new, 'blocked': blocker(cwd), 'checked': checked}


def apply(cwd=APP):
    """快轉到遠端 main。{ok, msg, version, restart}:restart=True 表示要重新啟動看板才生效。"""
    why = blocker(cwd)
    if why:
        return {'ok': False, 'msg': why}
    try:
        r = _git('fetch', 'origin', BRANCH, cwd=cwd)
        if r.returncode != 0:
            return {'ok': False, 'msg': '連不到程式的來源,等一下再試:' + (r.stderr.strip()[:160] or '沒有訊息')}
        r = _git('merge', '--ff-only', f'origin/{BRANCH}', cwd=cwd)
        if r.returncode != 0:
            return {'ok': False, 'msg': '沒辦法直接快轉到新版(這份程式有自己的 commit,跟來源分岔了):' + r.stderr.strip()[:160]}
    except (OSError, subprocess.TimeoutExpired):
        return {'ok': False, 'msg': '更新逾時,等一下再試'}
    # 新版的套件照 uv.lock 對齊(https://docs.astral.sh/uv/concepts/projects/sync/)。還沒裝 uv 就講清楚要裝
    need_uv = False
    uv = shutil.which('uv')
    if uv:
        try:
            subprocess.run([uv, 'sync', '--frozen', '--no-dev', '--inexact'], cwd=cwd, capture_output=True, timeout=1800)
        except (OSError, subprocess.TimeoutExpired):
            need_uv = True
    else:
        need_uv = True
    for old in ('venv', 'venv-test', 'pdf-venv', 'shot-venv', 'fetch-venv'):   # 以前自己建的環境用不到了
        shutil.rmtree(os.path.expanduser(f'~/.cache/jobsalvo/{old}'), ignore_errors=True)
    path = _cache_path()
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:   # 刪不掉的舊快取一天內會過期,更新本身已經成功
            pass
    version = current(cwd)
    restart = os.environ.get('JOBSALVO_LAUNCHD') != '1'
    msg = f'更新到 {version or "最新版"} 了' + (',重新啟動看板才會生效' if restart else ',看板會自己重新啟動')
    if need_uv:
        msg += '。轉 PDF、截圖要用的套件還沒準備好:先裝 uv(brew install uv),再重新整理看板'
    return {'ok': True, 'msg': msg, 'version': version, 'restart': restart}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)
    if a.apply:
        r = apply()
        print(r['msg'])
        return 0 if r['ok'] else 1
    c = check(cache=False)
    print(f"現在:{c['current'] or '讀不到'};遠端:{c['latest'] if c['latest'] is not None else '查不到'}"
          + (';有新版' if c['new'] else ''))
    if c['blocked']:
        print('不能從看板更新:' + c['blocked'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
