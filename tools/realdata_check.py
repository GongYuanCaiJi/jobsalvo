#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
realdata_check —— 在使用者真實資料的副本上跑一次,看改過的程式碰到真的資料會怎樣。

為什麼要有這支:測試用的是假資料,形狀是寫測試的人想像的。真的資料會有想像不到的形狀
(同一份附件在好幾個資料夾各有一份副本、舊的合併檔、幾十個可投遞夾),
只有在它上面跑過才知道會不會變慢、轉錯或靜靜失效。

它只動副本:把資料夾(JOBSALVO_HOME,或第一個參數)整份複製到自動清除的暫存資料夾,
在副本上讀設定(會套用設定轉換)、重建全部可投遞夾,回報花多久、轉出什麼、哪裡失敗。
原本的資料夾一個位元組都不改;設定裡指到資料夾外面、會被寫入的路徑,直接拒跑。

用法:
  uv run python tools/realdata_check.py [資料夾]          # 讀設定 + 全部重建(幾分鐘)
  uv run python tools/realdata_check.py [資料夾] --quick  # 只讀設定
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
WRITTEN = (
    ('board', 'file'), ('resume', 'ship_dir'), ('resume', 'prepare_dir'),
    ('paths', 'summaries'), ('paths', 'company_cache'), ('paths', 'research'),
    ('paths', 'posted_cache'), ('paths', 'log'), ('paths', 'prefs'),
    ('paths', 'preference_note'), ('paths', 'apply_rules'), ('paths', 'tmp'),
)


def outside(copy, settings, skip=()):
    """設定裡會被寫入、卻指到副本外面的路徑。"""
    bad = []
    root = os.path.realpath(copy)
    for section, key in WRITTEN:
        if (section, key) in skip:
            continue
        value = str((settings.get(section) or {}).get(key) or '')
        if not value:
            continue
        path = os.path.expanduser(value)
        path = path if os.path.isabs(path) else os.path.join(copy, path)
        try:
            inside = os.path.commonpath((root, os.path.realpath(path))) == root
        except ValueError:
            inside = False
        if not inside:
            bad.append(f'{section}.{key}（指向資料夾外）')
    return bad


def summary(copy):
    """在副本上讀一次設定,回報花多久、轉出幾份履歷與附件。"""
    code = ('import json,time,sys; sys.path.insert(0,%r); t=time.time(); import config as cf; c=cf.load(%r); '
            'r=c.get("resume") or {}; a=c.get("agent") or {}; '
            'print(json.dumps({"seconds": round(time.time()-t,2), "resumes": len(r.get("resumes") or []), '
            '"attachments": len(r.get("attachments") or []), "agents": len(a.get("agents") or [])}))') % (HERE, copy)
    env = dict(os.environ, JOBSALVO_HOME=copy)
    env.pop('AGENT_BOARD', None)
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env)
    if r.returncode:
        return None, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1]), ''


def reconcile_diagnostics(output):
    """Summarize failures without printing job titles, paths, or other copied data."""
    lines = output.splitlines()
    shell_at = next((i for i, line in enumerate(lines) if '· 看板外殼' in line), len(lines))
    package_at = next((i for i, line in enumerate(lines) if '· 可投遞夾' in line), len(lines))
    status_at = next((i for i, line in enumerate(lines) if line.startswith('看板:')), len(lines))
    finish_at = next((i for i, line in enumerate(lines[status_at + 1:], status_at + 1)
                      if '—— 收尾 ——' in line), len(lines))
    source_warning_lines = [line for line in lines[:shell_at] if line.lstrip().startswith('⚠')]
    source_warning_count = next((int(match.group(1)) for line in lines
                                 if (match := re.match(r'^來源警告數:(\d+)$', line.strip()))), None)
    source_warnings = source_warning_count if source_warning_count is not None else len(source_warning_lines)
    source_diagnostics = [line.split('來源診斷:', 1)[1].strip() for line in lines[:shell_at]
                          if '來源診斷:' in line]
    source_warning_kinds = (
        ('Markdown 原稿缺失', '找不到 Markdown 原稿'),
        ('Markdown 樣式缺失', '找不到指定的樣式檔'),
        ('Markdown 元件不可用', '無法準備 Markdown 排版元件'),
        ('Markdown 轉換失敗', 'Markdown 原稿無法排成 PDF'),
        ('Markdown 排版失敗', 'Markdown 排版失敗'),
        ('Chrome 不可用', '找不到可用的 Chrome'),
        ('Chrome 輸出未完成', 'Chrome 結束前沒有完成 PDF'),
        ('Chrome 逾時', 'Chrome 排版逾時'),
        ('Chrome 未產生 PDF', 'Chrome 沒有產生 PDF'),
        ('Chrome PDF 格式錯誤', 'Chrome 產生的檔案不是 PDF'),
        ('PDF 預覽工具不可用', '找不到做 PDF 預覽的工具'),
        ('PDF 預覽失敗', 'PDF 預覽產生失敗'),
        ('PDF 頁數', 'PDF 頁數讀取失敗'),
    )
    source_kind_labels = [label for label, marker in source_warning_kinds
                          if any(marker in line for line in source_warning_lines)]
    diagnostic_kinds = (
        ('Markdown 轉換失敗', 'Markdown render '),
        ('PDF 預覽失敗', 'PDF preview '),
        ('PDF 頁數', 'PDF pages '),
    )
    source_kind_labels.extend(label for label, prefix in diagnostic_kinds
                              if any(detail.startswith(prefix) for detail in source_diagnostics))
    source_kind_labels = list(dict.fromkeys(source_kind_labels))
    if source_warning_count == 0:
        source_kind_labels = []
    if source_warnings and not source_kind_labels:
        source_kind_labels = ['其他']
    package_warnings = sum(
        line.lstrip().startswith('⚠') and
        not re.match(r'^⚠\s*\d+\s*個問題', line.lstrip()) and
        '排序分數沒算成' not in line
        for line in lines[package_at + 1:]
    )
    package_successes = sum('✓ 可投遞夾:' in line for line in lines)
    status_problems = sum(int(value) for value in re.findall(
        r'⚠\s*(\d+)\s*個問題', '\n'.join(lines[status_at:finish_at]),
    ))
    sort_warnings = sum('排序分數沒算成' in line for line in lines)
    tracebacks = 'Traceback (most recent call last)' in output
    traceback_details = _traceback_details(lines)
    stage_results = [line.strip() for line in lines if line.strip().startswith('階段結果:')]
    timings = [line.strip() for line in lines if line.strip().startswith('計時:')]
    return (
        f'診斷:來源警告 {source_warnings}、可投遞夾警告 {package_warnings}、'
        f'來源警告類型 {"、".join(source_kind_labels) if source_kind_labels else "無"}、'
        f'可投遞夾成功 {package_successes}、看板驗收問題 {status_problems}、'
        f'排序警告 {sort_warnings}、例外堆疊 {"有" if tracebacks else "無"}'
        + (f'（{traceback_details}）' if traceback_details else '')
        + (f'；{"；".join(stage_results)}' if stage_results else '')
        + (f'；{"；".join(timings)}' if timings else '')
        + (f'；來源診斷 {"、".join(source_diagnostics)}' if source_diagnostics else '')
    )


def _traceback_details(lines):
    """Show exception classes and code locations without printing paths or messages."""
    summaries = []
    for index, line in enumerate(lines):
        if 'Traceback (most recent call last)' not in line:
            continue
        frame = None
        tool_frames = []
        error_type = None
        for candidate in lines[index + 1:]:
            match = re.match(r'^\s*File "([^"]+)", line (\d+), in (.+)$', candidate)
            if match:
                frame = f'{os.path.basename(match.group(1))}:{match.group(2)} {match.group(3)}'
                if '/tools/' in match.group(1).replace('\\', '/'):
                    tool_frames.append(frame)
                continue
            if candidate and not candidate[:1].isspace():
                match = re.match(r'^([A-Za-z_][A-Za-z0-9_.]*)(?::|$)', candidate)
                if match:
                    error_type = match.group(1).rsplit('.', 1)[-1]
                break
        if error_type or frame:
            detail = ' '.join(part for part in (error_type, f'at {frame}' if frame else '') if part)
            if tool_frames:
                detail += ' from ' + ', '.join(tool_frames[-3:])
            summaries.append(detail)
    return '；'.join(summaries[-3:])


def source_sync_failure_kinds(entries, manifest):
    """Classify stale generated state without exposing copied filenames or exception text."""
    import pdf_preview
    import source_sync
    kinds = set()
    for entry in entries:
        current = source_sync.entry_fingerprint(entry)
        output = source_sync.effective_path(entry)
        markdown = entry['path'].lower().endswith(('.md', '.markdown'))
        if markdown and manifest.get(source_sync.input_key(entry)) != current:
            kinds.add('Markdown PDF 未更新')
        elif markdown and manifest.get(source_sync.page_key(entry)) is None:
            kinds.add('PDF 頁數未記錄')
        if not output.lower().endswith('.pdf') or not os.path.isfile(output):
            continue
        cached = manifest.get(source_sync.preview_key(entry))
        preview = pdf_preview.cache_path(cached) if cached else None
        if not cached or not preview or not os.path.isfile(preview):
            kinds.add('PDF 預覽未生成')
    return sorted(kinds)


def _source_sync_failure_kinds_in_copy(copy, board):
    """Read only the disposable clone's manifest and configured sources in a child process."""
    code = r'''import json,sys
sys.path.insert(0,sys.argv[1])
import config as cf,source_sync
cf.reload(sys.argv[2])
manifest=source_sync.read_manifest()
entries=list(source_sync.files(sys.argv[3]))
print(json.dumps({'manifest':manifest,'entries':entries},ensure_ascii=False))
'''
    env = dict(os.environ, JOBSALVO_HOME=copy)
    env.pop('AGENT_BOARD', None)
    result = subprocess.run([sys.executable, '-c', code, HERE, copy, board],
                            capture_output=True, text=True, env=env)
    if result.returncode:
        return []
    try:
        state = json.loads(result.stdout.strip().splitlines()[-1])
        return source_sync_failure_kinds(state['entries'], state['manifest'])
    except (ValueError, KeyError, IndexError, TypeError):
        return []
def _configured_input_paths(copy, settings):
    paths = []
    resume = settings.get('resume') or {}
    if resume.get('base'):
        paths.append(resume['base'])
    for group in ('resumes', 'attachments'):
        for item in resume.get(group) or []:
            if not isinstance(item, dict):
                continue
            paths.extend((item.get('files') or {}).values())
            paths.extend((item.get('styles') or {}).values())
    board_value = str((settings.get('board') or {}).get('file') or 'board.html')
    board_path = board_value if os.path.isabs(os.path.expanduser(board_value)) else os.path.join(copy, board_value)
    try:
        import sys as _sys
        _sys.path.insert(0, HERE)
        import board_doc as bd
        with open(board_path, encoding='utf-8') as source:
            fb = json.loads(bd.parse(source.read())['fb'])
        for mark in fb.values():
            if not isinstance(mark, dict):
                continue
            if mark.get('custom_file'):
                paths.append(mark['custom_file'])
            for entry in (mark.get('custom_docs') or {}).values():
                if isinstance(entry, dict) and entry.get('path'):
                    paths.append(entry['path'])
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError('副本看板無法解析，無法檢查卡片使用的外部履歷來源') from exc
    return [value for value in paths if isinstance(value, str) and value]


def _materialize_external_files(copy, settings):
    """Replace configured file links in the clone with copied bytes before code reads them."""
    root = os.path.realpath(copy)
    count = 0
    for rel in _configured_input_paths(copy, settings):
        expanded = os.path.expanduser(rel)
        lexical = os.path.abspath(expanded if os.path.isabs(expanded) else os.path.join(copy, expanded))
        try:
            target = os.path.realpath(lexical)
            inside = os.path.commonpath((root, target)) == root
        except ValueError:
            inside = False
        if inside or not os.path.isfile(target):
            continue
        with open(target, 'rb') as source:
            content = source.read()
        relative = os.path.relpath(lexical, copy)
        if relative == '..' or relative.startswith('..' + os.sep):
            continue
        parent = os.path.dirname(lexical)
        chain = []
        current = parent
        while current != copy and current.startswith(copy + os.sep):
            chain.append(current)
            current = os.path.dirname(current)
        for directory in reversed(chain):
            if os.path.islink(directory):
                os.unlink(directory)
                os.makedirs(directory, exist_ok=True)
        if os.path.islink(lexical):
            os.unlink(lexical)
        os.makedirs(parent, exist_ok=True)
        temporary = lexical + '.realdata-check.tmp'
        with open(temporary, 'wb') as destination:
            destination.write(content)
        shutil.copystat(target, temporary, follow_symlinks=True)
        os.replace(temporary, lexical)
        count += 1
    return count


def _redirect_absolute_inputs(copy, settings):
    """Copy configured absolute input files into the clone and rewrite only its settings/marks."""
    root = os.path.realpath(copy)
    count = 0

    def redirect(value):
        nonlocal count
        if isinstance(value, dict):
            return {key: redirect(child) for key, child in value.items()}
        if isinstance(value, list):
            return [redirect(child) for child in value]
        if not isinstance(value, str) or not os.path.isabs(os.path.expanduser(value)):
            return value
        source = os.path.abspath(os.path.expanduser(value))
        try:
            if os.path.commonpath((root, os.path.realpath(source))) == root:
                return value
        except ValueError:  # Different path volumes cannot be common-pathed; copy into the isolated clone.
            pass
        identity = hashlib.sha256(os.path.realpath(source).encode('utf-8')).hexdigest()[:16]
        rel = os.path.join('.realdata-inputs', identity, os.path.basename(source))
        destination = os.path.join(copy, rel)
        if os.path.isfile(source):
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(source, destination)
            count += 1
        return rel.replace(os.sep, '/')

    resume = settings.get('resume') or {}
    for key in ('base', 'variants', 'resumes', 'attachments'):
        if key in resume:
            resume[key] = redirect(resume[key])
    paths = settings.get('paths') or {}
    for key in ('prefs', 'preference_note', 'apply_rules', 'tmp'):
        if key in paths:
            paths[key] = redirect(paths[key])
    browser = settings.get('browser') or {}
    if 'state' in browser:
        # Never copy or consult the user's agent-browser session in this check.
        browser['state'] = '.realdata-check-agent-state.json'
    config_path = os.path.join(copy, 'jobsalvo.json')
    with open(config_path, 'w', encoding='utf-8') as target:
        json.dump(settings, target, ensure_ascii=False, indent=2)

    board_value = str((settings.get('board') or {}).get('file') or 'board.html')
    board_path = board_value if os.path.isabs(os.path.expanduser(board_value)) else os.path.join(copy, board_value)
    try:
        import sys as _sys
        _sys.path.insert(0, HERE)
        import board_doc as bd
        with open(board_path, encoding='utf-8') as source:
            fb = json.loads(bd.parse(source.read())['fb'])
    except (OSError, ValueError, KeyError):
        return count
    replacements = {}
    for url, mark in fb.items():
        if not isinstance(mark, dict):
            continue
        updated = dict(mark)
        if 'custom_file' in updated:
            updated['custom_file'] = redirect(updated['custom_file'])
        if isinstance(updated.get('custom_docs'), dict):
            updated['custom_docs'] = redirect(updated['custom_docs'])
        if updated != mark:
            replacements[url] = updated
    if replacements:
        bd.set_fb(lambda current: current.update(replacements), live=board_path, by='realdata_check')
    return count


def _check_copy(src, copy, quick):
    shutil.copytree(src, copy, symlinks=True, ignore=shutil.ignore_patterns('.git', '.reconcile.lock'))
    config_path = os.path.join(copy, 'jobsalvo.json')
    if os.path.islink(config_path):
        with open(config_path, 'rb') as source:
            content = source.read()
        temporary_config = config_path + '.realdata-check.tmp'
        with open(temporary_config, 'wb') as target:
            target.write(content)
        os.replace(temporary_config, config_path)
    with open(config_path, encoding='utf-8') as f:
        settings = json.load(f)
    # The normal default lives outside the data folder; the validation run must
    # keep all scratch writes under this automatically removed clone.
    settings.setdefault('paths', {})['tmp'] = '.realdata-check-tmp'
    redirectable = {('paths', 'prefs'), ('paths', 'preference_note'),
                    ('paths', 'apply_rules'), ('browser', 'state')}
    bad = outside(copy, settings, skip=redirectable)
    if bad:
        print('設定裡有會被寫入、卻指到資料夾外面的路徑,在副本上跑會改到原本的檔,不跑:')
        print('\n'.join('  ' + b for b in bad))
        return 1
    redirected = _redirect_absolute_inputs(copy, settings)
    bad = outside(copy, settings)
    if bad:
        print('副本重導後仍有資料夾外的寫入路徑,不跑:')
        print('\n'.join('  ' + b for b in bad))
        return 1
    materialized = _materialize_external_files(copy, settings)
    if redirected or materialized:
        print(f'副本:已複製 {redirected + materialized} 份資料夾外的原稿到副本')
    os.makedirs(os.path.join(copy, settings['paths']['tmp']), exist_ok=True)
    with open(os.path.join(copy, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    print(f'副本:{copy}(原本的 {src} 不動)')
    info, err = summary(copy)
    if info is None:
        print(f'❌ 讀設定失敗:\n{err}')
        return 1
    print(f'讀設定 {info["seconds"]} 秒:履歷 {info["resumes"]} 份、附件 {info["attachments"]} 份、agent {info["agents"]} 個')
    if quick:
        return 0
    t = time.time()
    board_value = str((settings.get('board') or {}).get('file') or 'board.html')
    board_path = os.path.expanduser(board_value)
    if not os.path.isabs(board_path):
        board_path = os.path.join(copy, board_path)
    env = dict(os.environ, JOBSALVO_HOME=copy, AGENT_BOARD=os.path.abspath(board_path),
               PYTHONUNBUFFERED='1')
    r = subprocess.run([sys.executable, os.path.join(HERE, 'reconcile.py'), '--force', '--timings'],
                       capture_output=True, text=True,
                       env=env)
    print(f'全部重建 {time.time() - t:.0f} 秒,結束碼 {r.returncode}')
    kinds = _source_sync_failure_kinds_in_copy(copy, board_path) if r.returncode else []
    print(reconcile_diagnostics(r.stdout + '\n' + r.stderr) +
          (f'、manifest 狀態分類 {"、".join(kinds)}' if kinds else ''))
    print(status_summary(board_path))
    return r.returncode


def status_summary(board_path):
    """Only issue counts from the copied board; never print job names or copied values."""
    import board_doc as bd
    try:
        with open(board_path, encoding='utf-8') as source:
            status = bd.parse(source.read())['data'].get('status')
    except Exception as error:
        return f'⚠ 看板驗收:讀不到副本結果({type(error).__name__})'
    if not isinstance(status, dict) or 'issues' not in status:
        return '⚠ 看板驗收:沒有驗收結果'
    issues = status.get('issues') or []
    if not issues:
        return '看板驗收:全過' if status.get('checked_links') else '看板驗收:連結尚未檢查'
    names = {'closed': '已下架', 'unverified': '還沒確認', 'files': '檔案'}
    counts = {}
    for issue in issues:
        name = names.get(issue.get('kind'), '其他')
        counts[name] = counts.get(name, 0) + 1
    return '看板驗收:' + '、'.join(f'{name} {count}' for name, count in counts.items())


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('home', nargs='?')
    parser.add_argument('--quick', action='store_true')
    a = parser.parse_args(argv)
    src = os.path.abspath(os.path.expanduser(a.home or os.environ.get('JOBSALVO_HOME', '')))
    if not src or not os.path.isfile(os.path.join(src, 'jobsalvo.json')):
        print('找不到資料夾(給一個含 jobsalvo.json 的資料夾,或設 JOBSALVO_HOME)。')
        return 2
    with tempfile.TemporaryDirectory(prefix='jobsalvo-realdata-') as temporary:
        return _check_copy(src, os.path.join(temporary, 'home'), a.quick)



if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
