#!/usr/bin/env python3
"""Debounced, local-only Git history for the user's data folder."""
import os
import json
import shutil
import subprocess
import threading


GENERATED = (
    'ship/', 'prepare/', '.rendered/', '.previews/', '.preview-cache/',
    'card-summaries/', 'company-cache/', '.research/', 'posted-cache.json',
    '.reconcile-manifest.json', '.reconcile.lock', '.reconcile.lock.*',
    'board-server.log', 'agent-runs.jsonl', '*.tmp',
)
_REPOSITORY_MARKER = 'jobsalvo-data-repository'
_REPOSITORY_MARKER_CONTENT = 'jobsalvo local data repository\n'
_lock = threading.Lock()
_timers = {}
_pending = set()
_last_errors = {}
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
    return os.path.realpath(root.stdout.strip()) if root.returncode == 0 else None


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


def _repository_block_reason(git, home):
    if _repository_root(git, home) != os.path.realpath(home):
        return ''
    if not _is_jobsalvo_repository(git, home):
        return ('資料夾已是既有 Git repo，不是 jobsalvo 建立；為避免把求職資料提交到程式碼 repo，'
                'jobsalvo 不會 commit。')
    if _has_remote(git, home):
        return 'jobsalvo 資料 repo 已設定 remote；為避免求職資料被推送，版本紀錄已停用。'
    return ''


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


def _ensure_repository(git, home):
    os.makedirs(home, exist_ok=True)
    root = _repository_root(git, home)
    if root == os.path.realpath(home):
        reason = _repository_block_reason(git, home)
        if reason:
            raise RuntimeError(reason)
    else:
        _run(git, home, 'init', '--initial-branch=data')
        marker = _marker_path(git, home)
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, 'w', encoding='utf-8') as target:
            target.write(_REPOSITORY_MARKER_CONTENT)
    _exclude_generated(git, home)


def _commit(git, home):
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
    _run(git, home, *identity, 'commit', '-m', 'jobsalvo save')
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


def status(home):
    git = _git()
    home = os.path.realpath(home)
    with _lock:
        pending = home in _pending
        last_error = _last_errors.get(home, '')
    if not git:
        return {'available': False, 'message': '沒有版本紀錄，因為找不到 git', 'pending': False}
    blocked = _repository_block_reason(git, home)
    if blocked:
        return {'available': True, 'message': '版本紀錄未啟用：' + blocked, 'pending': pending}
    if not _is_home_repository(git, home):
        return {'available': True, 'message': '版本紀錄失敗：' + last_error if last_error else
                '第一次儲存後會建立本機版本紀錄', 'pending': pending}
    # 給人看的:每次存檔自動留一版、最近一次什麼時候(不給 git 的版本代碼,沒人看得懂)
    result = _run(git, home, 'log', '-1', '--format=%cd', '--date=format:%Y-%m-%d %H:%M', check=False)
    if result.returncode:
        return {'available': True, 'message': '版本紀錄失敗：' + last_error if last_error else
                '第一次儲存後會建立本機版本紀錄', 'pending': pending}
    return {'available': True, 'message': '每次存檔會自動留一版,最近一次 ' + result.stdout.strip(), 'pending': pending}
