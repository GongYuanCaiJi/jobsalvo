#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reconcile —— 改完任何東西,只跑這一個。

它比對每個衍生物的來源雜湊,只重建來源真的變了的,而且冪等:什麼都沒變,跑它不動任何檔。
取代「記得照順序跑哪幾支」這種要靠人記的步驟。

  · 看板外殼  board/{board.css,board.js,header.html} 變了 → 換上現行看板(看板檢查在 CI 跑,合進 main 之前就驗過了)
  · 可投遞夾  「待你決定」「可投遞」的卡建好可投遞夾(ship.reconcile_packages)
  · 驗收狀態  board_status --links --write:可投遞之前的機械把關,結果寫進看板
  · 排序分數  prefs.refresh_like:照最新的表態重算「最可能喜歡」
  · 清過期夾  卡片離開準備/投遞階段,它的可投遞夾就是過期快照,刪掉;對不到任何卡的只報不刪

用法:
    uv run python tools/reconcile.py           # 對齊全部,只重建變了的
    uv run python tools/reconcile.py --check   # 只報哪裡漂了,不動手
    uv run python tools/reconcile.py --force   # 無視雜湊,全部重建

事件驅動的步驟(找缺 converge、跑準備區 cut_tailor、手機上標階段)跑完會自己呼叫這支。
"""
import sys, os, json, hashlib, subprocess, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import ship
import source_sync

MANIFEST = os.path.join(cf.HOME, '.reconcile-manifest.json')
BOARD_SRC = [os.path.join(cf.BOARD_SRC, f) for f in ('board.css', 'board.js', 'header.html')]
APPLY_SHELL = os.path.join(HERE, 'apply_shell.py')


def sha(paths, extra=''):
    """一組檔案內容(含路徑名)的合併 sha256。缺檔也算進去(從有到無也是一種變)。"""
    h = hashlib.sha256(extra.encode())
    for p in sorted(paths):
        h.update(p.encode())
        if os.path.isfile(p):
            with open(p, 'rb') as f:
                h.update(f.read())
        else:
            h.update(b'\0MISSING')
    return h.hexdigest()


# ── 看板外殼 ─────────────────────────────────────────────────────────────
def stage_shell(man, force, check, live):
    key = 'shell'
    want = sha(BOARD_SRC + [APPLY_SHELL, os.path.join(HERE, 'board_doc.py')])
    if not (force or man.get(key) != want):
        return False
    if check:
        print('  看板外殼(css/js/頁首)過時')
        return True
    # 以前這裡先在使用者電腦上跑一次完整的看板檢查(無頭 Chrome、約 5 分鐘)才換。現在看板檢查是 CI 的必過項目,
    # main 上的外殼都驗過了(docs/adr/0001:CI 是唯一的關卡);而且看板伺服器送的一直是程式碼裡現在的外殼(#190),
    # 這裡換的只是看板檔裡留的那一份。使用者電腦不再為這件事跑 Chrome。
    subprocess.run([sys.executable, APPLY_SHELL, live], check=True)
    man[key] = want
    print('  ✓ 看板外殼重灌')
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--board', default=cf.LIVE)
    ap.add_argument('--check', action='store_true', help='只報漂移,不動任何檔')
    ap.add_argument('--force', action='store_true', help='無視雜湊,全部重建')
    ap.add_argument('--timings', action='store_true', help='列出重建各階段耗時')
    a = ap.parse_args()
    if a.check:
        return run(a)
    # 同一時間只跑一個。看板伺服器每標一張待你決定/可投遞就在背景跑一次,cut_tailor 收尾也跑;
    # 兩個一起跑會一起擷取職缺頁、一起寫看板檔。
    # 後到的等前一個跑完再跑,不是跳過:它要處理的改動可能是前一個開跑之後才發生的。
    import fcntl
    with open(os.path.join(cf.HOME, '.reconcile.lock'), 'w') as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print('另一個 reconcile 正在跑,等它跑完再接著跑…')
            fcntl.flock(lk, fcntl.LOCK_EX)
        return run(a)


def run(a):
    live = a.board
    show_timings = getattr(a, 'timings', False)
    timings = {}
    try:
        with open(MANIFEST, encoding='utf-8') as f:
            man = json.load(f)
    except Exception:
        man = {}
    print('reconcile:比對來源,只重建變了的' + ('(--check:只報不動手)' if a.check else ''))
    if not os.path.isfile(live):
        print(f'· 沒有看板檔({live}),先跑 python3 tools/init.py。')
        return 1
    started = time.perf_counter()
    source_diagnostics = []
    source_changes, source_errors = source_sync.refresh(
        man, check_only=a.check, board=live,
        diagnostics=source_diagnostics if show_timings else None,
    )
    timings['來源'] = time.perf_counter() - started
    for entry in source_changes:
        print(f'  來源檔變更:{os.path.basename(entry["path"])}')
    for message in source_errors:
        print(f'  ⚠ {message}')
    for diagnostic in source_diagnostics:
        print(f'來源診斷:{diagnostic}')
    if show_timings:
        print(f'來源警告數:{len(source_errors)}')
    print('· 看板外殼', flush=True)
    started = time.perf_counter()
    shell = stage_shell(man, a.force, a.check, live)
    timings['外殼'] = time.perf_counter() - started
    print('· 可投遞夾', flush=True)
    started = time.perf_counter()
    pkg, package_failures = ship.reconcile_packages(man, a.force, a.check, live, timings=show_timings)
    timings['套件'] = time.perf_counter() - started
    status_code = 0
    if not a.check:
        # 看板的「可投遞」按鈕只在最新機械驗收沒有問題時放行;連結也一起驗(只驗待你決定/可投遞那幾張)。
        started = time.perf_counter()
        status_command = [sys.executable, os.path.join(HERE, 'board_status.py'),
                          '--links', '--write', '--board', live]
        if show_timings:
            status_command.append('--timings')
        sys.stdout.flush()
        status_code = subprocess.run(status_command, check=False).returncode
        timings['看板驗收'] = time.perf_counter() - started
        started = time.perf_counter()
        try:
            import prefs
            prefs.refresh_like(live)
        except Exception as e:
            print(f'  ⚠ 排序分數沒算成:{e}')
        with open(MANIFEST, 'w', encoding='utf-8') as f:
            json.dump(man, f, ensure_ascii=False, indent=2)
        timings['收尾'] = time.perf_counter() - started

    if show_timings:
        print(f'階段結果:套件失敗 {len(package_failures)}、看板驗收結束碼 {status_code}')
        print('計時:' + '、'.join(f'{name} {seconds:.1f}s' for name, seconds in timings.items()))

    print('\n—— 收尾 ——')
    if not (shell or pkg):
        print('  全部已對齊,沒有要動的。')
    elif a.check:
        print('  以上是過時清單,--check 不動手。拿掉 --check 重跑才會真的重建。')
    else:
        print('  看板服務直接讀這個檔,重整就看得到。')
    cleaned, unknown = ship.clean_orphans(a.check, live)
    if cleaned:
        print(f'  {"會清" if a.check else "已清"} {len(cleaned)} 個過期可投遞夾(卡片已離開準備/投遞階段,可隨時重生):')
        for d in cleaned:
            print(f'      {d}')
    if unknown:
        print(f'  ⓘ {len(unknown)} 個可投遞夾對不到看板上的卡(來歷不明,不敢自動刪,你看一下):')
        for d in unknown:
            print(f'      {d}')
    from board_status import ISSUES_FOUND
    status_failed = status_code not in (0, ISSUES_FOUND)
    if status_code == ISSUES_FOUND:
        print('  ⓘ 有卡未過投遞前驗收,原因已寫在看板;本輪重建正常。')
    elif status_failed:
        print(f'  ⚠ 投遞前驗收程式失敗(結束碼 {status_code})')
    return 1 if package_failures or status_failed or source_errors or (a.check and source_changes) else 0


if __name__ == '__main__':
    sys.exit(main())
