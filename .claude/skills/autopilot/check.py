#!/usr/bin/env python3
"""自動流程「同一段只按一次」「推進要用新的驗收」的模型檢查,加上用真程式重演。
用法:python3 .claude/skills/autopilot/check.py [--out 資料夾]
1. 從 tools/delivery_state.json 產生 Table.tla,每條規則各跑一次 TLC,印「通過」或「違規」。
2. 跑 replay.py:照模型的路徑呼叫真的 Pilot,印每一條重演的結果。
結果跟同步點不一樣(EXPECTED、replay.EXPECTED)exit 1,一樣就 exit 0。--out 的資料夾放著 TLC 跑的那一份(給 /statechart 讀)。"""
import argparse, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import delivery_state as ds  # noqa: E402  照程式自己的 _steps 算一格最後到哪

MODELED = ('todo', 'running', 'parked', 'stuck', 'gone', 'stale', 'nopage')   # 其他狀態在模型裡都是 "left"
EVENTS = ('fill_start', 'fill_ok', 'fill_bad', 'fill_nopage', 'page_lost')
# 同步點上每條規則該看到的結果(三條都是已知違規,各有一條重演,見 SKILL.md)。修好一個就改成「通過」
EXPECTED = {'OncePerMark': '違規', 'StopsAfterFailure': '違規', 'FreshVerdict': '違規'}   # #743、#742、#741
RULES = tuple(EXPECTED)
JAVA = 'java@temurin-25'
TLC = ('http:tla2tools[url=https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar,'
       'checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88]@1.7.4')


def m(s):
    return s if s in MODELED else 'left'


def q(xs):
    return '{%s}' % ', '.join('"%s"' % x for x in xs)


def table():
    """模型的狀態 × 模型用到的事件 → 可能到的模型狀態(when 分支都算);自動流程會自己填的狀態(AutoFill)。"""
    cells = []
    for s in MODELED:
        for ev, cell in ds.TABLE['cells'][s].items():
            if ev not in EVENTS:
                continue
            to = s
            for step in ds._steps(cell):
                to = step.get('to', to)
            cells.append('<<"%s", "%s">> :> %s' % (s, ev, q(sorted({m(to)} | {m(w['to']) for w in cell.get('when') or []}))))
    missing = set(ds.AUTO_FILL) - set(MODELED)
    if missing:
        sys.exit(f'delivery_state.json 多了自動流程會自己填的狀態 {sorted(missing)}:加進 check.py 的 MODELED 和模型')
    return ('---- MODULE Table ----\nEXTENDS TLC\n\\* check.py 從 tools/delivery_state.json 產生,不要手改\n'
            'ModelStates == %s\nAutoFill == %s\nCell ==\n     %s\n====\n'
            % (q(MODELED + ('left',)), q(sorted(ds.AUTO_FILL)), '\n  @@ '.join(cells)))


def mise(*args):
    r = subprocess.run(['mise', *args], cwd=HERE, capture_output=True, text=True,
                       env=dict(os.environ, MISE_CEILING_PATHS=HERE))
    return r.stdout.strip() if r.returncode == 0 else None


def tools():
    if not (mise('where', TLC) and mise('which', 'java', '--tool', JAVA)):
        subprocess.run(['mise', 'install', JAVA, TLC], cwd=HERE, check=True)
    return mise('which', 'java', '--tool', JAVA), os.path.join(mise('where', TLC), 'tla2tools.jar')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', help='放這一次模型的資料夾(預設開一個暫存的)')
    ap.add_argument('--cfg', default=os.path.join(HERE, 'AutoPilot.cfg'))
    a = ap.parse_args()
    out = a.out or tempfile.mkdtemp(prefix='autopilot-')
    os.makedirs(out, exist_ok=True)
    shutil.copy(os.path.join(HERE, 'AutoPilot.tla'), out)
    with open(os.path.join(out, 'Table.tla'), 'w', encoding='utf-8') as f:
        f.write(table())
    base = open(a.cfg, encoding='utf-8').read()
    shutil.copy(a.cfg, os.path.join(out, 'AutoPilot.cfg'))
    java, jar = tools()
    off = 0
    for rule in RULES:
        cfg = os.path.join(out, rule + '.cfg')
        with open(cfg, 'w', encoding='utf-8') as f:   # 一條規則一份:TLC 停在第一個違規,其他規則才看得到各自的結果
            f.write(re.sub(r'INVARIANTS[\s\S]*', 'INVARIANTS\n  TypeOK\n  %s\n' % rule, base))
        r = subprocess.run([java, '-XX:+UseParallelGC', '-cp', jar, 'tlc2.TLC', '-workers', 'auto',
                            '-cleanup', '-metadir', os.path.join(out, 'states-' + rule),
                            '-config', cfg, 'AutoPilot.tla'], cwd=out, capture_output=True, text=True)
        with open(os.path.join(out, rule + '.out'), 'w', encoding='utf-8') as f:
            f.write(r.stdout + r.stderr)
        states = re.findall(r'([\d,]+) distinct states found', r.stdout)
        passed = 'No error has been found' in r.stdout
        violated = re.search(r'Invariant \w+ is violated', r.stdout)
        got = '通過' if passed else '違規' if violated else 'TLC 沒跑完'
        note = '' if got == EXPECTED[rule] else f'  ← 同步點上是{EXPECTED[rule]}'
        off += bool(note)
        print(f'{rule}: {got}' + (f'({states[-1]} 個狀態)' if passed and states else '') + note)
        if not passed:
            print(f'  {"路徑" if violated else "輸出"}在 {os.path.join(out, rule + ".out")}')
    print('模型資料夾:', out)
    print('--- 用真程式重演 ---', flush=True)
    rp = subprocess.run(['uv', 'run', 'python', os.path.join(HERE, 'replay.py')], cwd=REPO)
    sys.exit(1 if off or rp.returncode else 0)


if __name__ == '__main__':
    main()
