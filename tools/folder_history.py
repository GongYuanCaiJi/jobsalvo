#!/usr/bin/env python3
"""Debounced, local-only Git history for the user's data folder."""
import os
import json
import shutil
import subprocess
import threading
import time


GENERATED = (
    'ship/', 'prepare/', '.rendered/', '.previews/', '.preview-cache/',
    'card-summaries/', 'company-cache/', '.research/', 'posted-cache.json',
    '.reconcile-manifest.json', '.reconcile.lock', '.reconcile.lock.*',
    'board-server.log', 'agent-runs.jsonl', '*.tmp', 'before-conversion-backups/',
    'evidence/',                                   # 每一輪的證據(截圖、動作紀錄):大、而且只給查錯用(#315)
)
BACKUP_DIR = 'before-conversion-backups'   # 存不了版時,轉換前的備份放資料夾裡這一格
_REPOSITORY_MARKER = 'jobsalvo-data-repository'
_REPOSITORY_MARKER_CONTENT = 'jobsalvo local data repository\n'
_lock = threading.Lock()
_timers = {}
_pending = set()
_last_errors = {}
_conversion_problems = {}
_git_override = None
_REPOSITORY_ENV = {
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE',
    'GIT_PREFIX', 'GIT_SUPER_PREFIX', 'GIT_CEILING_DIRECTORIES',
    'GIT_DISCOVERY_ACROSS_FILESYSTEM', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT',
}


def _git():
    return _git_override or shutil.which('git')


def _run(git, home, *args, check=True):
    env = {key: value for key, value in os.environ.items()
           if key not in _REPOSITORY_ENV and
           not key.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_'))}
    result = subprocess.run([git, *args], cwd=home, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'git 指令失敗').strip())
    return result


def _repository_root(git, home):
    root = _run(git, home, 'rev-parse', '--show-toplevel', check=False)
    return os.path.realpath(root.stdout.strip()) if root.returncode == 0 and root.stdout.strip() else None


def _is_home_repository(git, home):
    return _repository_root(git, home) == os.path.realpath(home)


def _marker_path(git, home):
    path = _run(git, home, 'rev-parse', '--git-path', _REPOSITORY_MARKER).stdout.strip()
    return path if os.path.isabs(path) else os.path.join(home, path)


def _is_jobsalvo_repository(git, home):
    try:
        with open(_marker_path(git, home), encoding='utf-8') as marker:
            return marker.read() == _REPOSITORY_MARKER_CONTENT
    except OSError:
        return False


def _has_remote(git, home):
    result = _run(git, home, 'remote', check=False)
    if result.returncode:
        raise RuntimeError(result.stderr or 'git 無法確認遠端設定')
    return bool(result.stdout.strip())


def _repository_problem(git, home):
    """不自動存版的原因和怎麼處理 (reason, fix);能存版回 ('', '')。
    資料夾本身就是 repo 根目錄、沒有 remote、裡面有 jobsalvo.json(從舊系統搬來的資料夾)就接手照常存版。"""
    root = _repository_root(git, home)
    if root is None:
        return '', ''
    if root != os.path.realpath(home):
        return (f'資料夾在另一個 Git repo（{root}）裡面，是它的子資料夾；為避免把求職資料提交到那個 repo，'
                'jobsalvo 不自動存版。',
                '把資料夾搬到那個 repo 外面（再到設定頁改資料夾位置）；搬好後下一次存檔就會自動存版。')
    if _has_remote(git, home):
        return ('資料夾的 Git repo 設了 remote（會推到別處）；為避免求職資料被推送，jobsalvo 不自動存版。',
                '確定這個 repo 只放求職資料的話，在資料夾裡跑 git remote -v 看是哪一個，再用 git remote remove <名稱> 拿掉；'
                '不確定就把求職資料搬到另一個資料夾。拿掉後下一次存檔就會自動存版。')
    if _is_jobsalvo_repository(git, home) or os.path.isfile(os.path.join(home, 'jobsalvo.json')):
        return '', ''
    return ('資料夾已是既有 Git repo，裡面沒有 jobsalvo.json，不像 jobsalvo 的資料夾；為避免把別的東西提交進去，'
            'jobsalvo 不自動存版。',
            '確認資料夾位置對不對（設定頁）；jobsalvo 的資料夾裡會有 jobsalvo.json。')


def _generated_patterns(home):
    patterns = set(GENERATED)
    config_path = os.path.join(home, 'jobsalvo.json')
    try:
        with open(config_path, encoding='utf-8') as source:
            config = json.load(source)
    except (OSError, ValueError):
        return sorted(patterns)
    configured = (
        ('resume', 'ship_dir', True), ('resume', 'prepare_dir', True),
        ('paths', 'summaries', True), ('paths', 'company_cache', True),
        ('paths', 'research', True), ('paths', 'posted_cache', False),
        ('paths', 'log', False), ('paths', 'tmp', True),
    )
    root = os.path.realpath(home)
    for section, key, is_dir in configured:
        value = str((config.get(section) or {}).get(key) or '')
        if not value:
            continue
        path = os.path.expanduser(value)
        path = path if os.path.isabs(path) else os.path.join(home, path)
        try:
            if os.path.commonpath((root, os.path.realpath(path))) != root:
                continue
        except ValueError:
            continue
        relative = os.path.relpath(path, home).replace(os.sep, '/')
        if relative in ('.', '..') or relative.startswith('../'):
            continue
        relative = ''.join('\\' + char if char in r'\*?[]#! ' else char for char in relative)
        patterns.add(relative.rstrip('/') + ('/' if is_dir else ''))
    return sorted(patterns)


def _exclude_generated(git, home):
    exclude = _run(git, home, 'rev-parse', '--git-path', 'info/exclude').stdout.strip()
    if not os.path.isabs(exclude):
        exclude = os.path.join(home, exclude)
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    try:
        with open(exclude, encoding='utf-8') as source:
            current = source.read().splitlines()
    except OSError:
        current = []
    missing = [pattern for pattern in _generated_patterns(home) if pattern not in current]
    if missing:
        with open(exclude, 'a', encoding='utf-8') as target:
            if current and current[-1] != '':
                target.write('\n')
            target.write('# jobsalvo generated files\n' + '\n'.join(missing) + '\n')


def _write_marker(git, home):
    marker = _marker_path(git, home)
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, 'w', encoding='utf-8') as target:
        target.write(_REPOSITORY_MARKER_CONTENT)


def _ensure_repository(git, home):
    os.makedirs(home, exist_ok=True)
    root = _repository_root(git, home)
    reason = _repository_problem(git, home)[0]
    if reason:
        raise RuntimeError(reason)
    if root == os.path.realpath(home):
        if not _is_jobsalvo_repository(git, home):
            _write_marker(git, home)   # 從舊系統搬來的資料夾:接手成 jobsalvo 的資料 repo
    else:
        _run(git, home, 'init', '--initial-branch=data')
        _write_marker(git, home)
    _exclude_generated(git, home)


def _commit(git, home, message='jobsalvo save'):
    _ensure_repository(git, home)
    _run(git, home, 'add', '-A')
    changed = _run(git, home, 'diff', '--cached', '--quiet', check=False)
    if changed.returncode == 0:
        return False
    if changed.returncode != 1:
        raise RuntimeError(changed.stderr or 'git diff 無法確認變更')
    identity = []
    for key, fallback in (('user.name', 'jobsalvo local history'),
                          ('user.email', 'jobsalvo@localhost')):
        configured = _run(git, home, 'config', key, check=False)
        if not configured.stdout.strip():
            identity.extend(('-c', f'{key}={fallback}'))
    _run(git, home, *identity, 'commit', '-m', message)
    return True


def _flush(home):
    home = os.path.realpath(home)
    if not os.path.isdir(home):
        with _lock:
            _timers.pop(home, None)
            _pending.discard(home)
        return False
    with _lock:
        _timers.pop(home, None)
        _pending.add(home)
    git = _git()
    if not git:
        with _lock:
            _pending.discard(home)
        return False
    try:
        committed = _commit(git, home)
        with _lock:
            _last_errors.pop(home, None)
        return committed
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        with _lock:
            _last_errors[home] = str(exc)[:160]
        return False
    finally:
        with _lock:
            _pending.discard(home)


def note_saved(home, delay=1.0):
    """Schedule one local snapshot after writes have been quiet for `delay` seconds."""
    home = os.path.realpath(home)
    git_available = bool(_git())
    with _lock:
        previous = _timers.pop(home, None)
        if previous:
            previous.cancel()
        if not git_available:
            _pending.discard(home)
            return
        timer = threading.Timer(delay, _flush, args=(home,))
        timer.daemon = True
        _timers[home] = timer
        _pending.add(home)
        timer.start()


def flush_now(home):
    """Flush pending work; exposed so lifecycle callers and tests can await it."""
    return _flush(home)


def _save_version(git, home, files, why):
    """同步存一版,確認每個檔案都在這一版裡、跟磁碟上一模一樣;回傳版本代碼。"""
    relatives = []
    for path in files:
        real = os.path.realpath(path)
        try:
            inside = os.path.commonpath((home, real)) == home
        except ValueError:
            inside = False
        if not inside:
            raise RuntimeError(f'{os.path.basename(path)} 不在資料夾裡，版本紀錄收不到它')
        relatives.append(os.path.relpath(real, home))
    with _lock:
        timer = _timers.pop(home, None)
        if timer:
            timer.cancel()
        _pending.discard(home)
    _commit(git, home, message='jobsalvo 轉換前：' + why)
    for relative in relatives:
        _run(git, home, 'ls-files', '--error-unmatch', '--', relative)
        if _run(git, home, 'status', '--porcelain', '--', relative).stdout.strip():
            raise RuntimeError(f'{relative} 沒有完整存進這一版')
    return _run(git, home, 'rev-parse', '--short', 'HEAD').stdout.strip()


def _back_up(home, files, why):
    if not files:
        raise OSError('沒有檔案可以備份')
    folder = os.path.join(home, BACKUP_DIR)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    label = ''.join('-' if char in '/\\:' else char for char in why)
    copies = []
    for path in files:
        target = os.path.join(folder, f'{stamp}-{label}-{os.path.basename(path)}')
        number = 1
        while os.path.exists(target):
            number += 1
            target = os.path.join(folder, f'{stamp}-{label}-{number}-{os.path.basename(path)}')
        shutil.copy2(path, target)
        if os.path.getsize(target) != os.path.getsize(path):
            raise OSError(f'{target} 沒有完整複製')
        copies.append(target)
    return copies


def convert(home, files, why, do):
    """回不了頭的資料轉換一律走這裡:先留退回點,有退回點才呼叫 do() 轉。
    退回點:先存一版(轉換前那一版就是舊格式);存不了版,就把 files 備份到資料夾內 BACKUP_DIR。
    兩樣都做不到就不轉,原因留給設定頁和環境檢查看。回傳 {'done', 'restore', 'reason'}。"""
    home = os.path.realpath(home)
    files = [path for path in files if os.path.isfile(path)]
    restore, reasons = '', []
    git = _git()
    if git:
        try:
            restore = '轉換前的版本 ' + _save_version(git, home, files, why)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            reasons.append('存版失敗：' + str(exc)[:160])
    else:
        reasons.append('找不到 git，存不了版')
    if not restore:
        try:
            restore = '轉換前的備份 ' + '、'.join(_back_up(home, files, why))
        except OSError as exc:
            reasons.append('備份失敗：' + str(exc)[:160])
    if not restore:
        reason = '；'.join(reasons)
        with _lock:
            _conversion_problems.setdefault(home, {})[why] = f'{why}還沒轉換（沒有退回點）：{reason}'
        return {'done': False, 'restore': '', 'reason': reason}
    with _lock:
        _conversion_problems.get(home, {}).pop(why, None)
    do()
    note_saved(home)   # 轉完的新格式也留一版
    return {'done': True, 'restore': restore, 'reason': ''}


def status(home):
    home = os.path.realpath(home)
    with _lock:
        conversion = '；'.join(_conversion_problems.get(home, {}).values())
    result = _status(home)
    if conversion:
        result['conversion'] = conversion
    return result


def _status(home):
    git = _git()
    with _lock:
        pending = home in _pending
        last_error = _last_errors.get(home, '')
    if not git:
        return {'available': False, 'message': '沒有版本紀錄，因為找不到 git', 'pending': False}
    blocked, fix = _repository_problem(git, home)
    if blocked:
        return {'available': True, 'message': '版本紀錄未啟用：' + blocked, 'fix': fix, 'pending': pending}
    if not _is_home_repository(git, home):
        return {'available': True, 'message': '版本紀錄失敗：' + last_error if last_error else
                '第一次儲存後會建立本機版本紀錄', 'pending': pending}
    # 給人看的:每次存檔自動留一版、最近一次什麼時候(不給 git 的版本代碼,沒人看得懂)
    result = _run(git, home, 'log', '-1', '--format=%cd', '--date=format:%Y-%m-%d %H:%M', check=False)
    if result.returncode:
        return {'available': True, 'message': '版本紀錄失敗：' + last_error if last_error else
                '第一次儲存後會建立本機版本紀錄', 'pending': pending}
    return {'available': True, 'message': '每次存檔會自動留一版,最近一次 ' + result.stdout.strip(), 'pending': pending}
