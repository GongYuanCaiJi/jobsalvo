#!/usr/bin/env python3
"""確認送出 8 秒反悔期的模型檢查:從 tools/delivery_state.json 產生 Table.tla,每條規則各跑一次 TLC。
用法:python3 .claude/skills/confirm-send-window/check.py [--out 資料夾]
每條規則印「通過」或「違規」;有違規 exit 1。--out 的資料夾放著 TLC 跑的那一份(給 /statechart 讀)。"""
import argparse, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import delivery_state as ds  # noqa: E402  照程式自己的 _steps 算一格最後到哪

MODELED = ('parked', 'confirmed', 'sending', 'sent', 'unsure')   # 其他狀態在模型裡都是 "left"
RULES = ('CancelledNeverSent', 'NoDoubleSend', 'WindowKept')
JAVA = 'java@temurin-25'
TLC = ('http:tla2tools[url=https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar,'
       'checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88]@1.7.4')


def m(s):
    return s if s in MODELED else 'left'


def table():
    """狀態表上「模型的狀態 × 事件」→ 可能到的模型狀態(when 分支都算)。"""
    cells = []
    for s in MODELED:
        for ev, cell in ds.TABLE['cells'][s].items():
            to = s
            for step in ds._steps(cell):
                to = step.get('to', to)
            tos = sorted({m(to)} | {m(w['to']) for w in cell.get('when') or []})
            cells.append('<<"%s", "%s">> :> {%s}' % (s, ev, ', '.join('"%s"' % t for t in tos)))
    return ('---- MODULE Table ----\nEXTENDS TLC\n\\* check.py 從 tools/delivery_state.json 產生,不要手改\n'
            'ModelStates == {%s, "left"}\nCell ==\n     %s\n====\n'
            % (', '.join('"%s"' % s for s in MODELED), '\n  @@ '.join(cells)))


def from_code():
    """模型的常數照程式現在寫的:8 秒、15 秒重排、settle 對正在送出的卡送哪個事件。讀不到就停,不猜。"""
    src = open(os.path.join(REPO, 'tools', 'board_server.py'), encoding='utf-8').read()
    nums = {k: re.search(r'^%s\s*=\s*(\d+)' % k, src, re.M) for k in ('SEND_AFTER', 'SEND_RETRY')}
    settle = re.search(r'^def settle\(.*?(?=^def )', open(os.path.join(REPO, 'tools', 'apply_run.py'),
                                                             encoding='utf-8').read(), re.M | re.S)
    evs = re.findall(r"ds\.try_fire\(fb, u, '(\w+)'", settle.group(0)) if settle else []
    if not all(nums.values()) or len(evs) != 1:
        sys.exit('check.py 讀不到 board_server.SEND_AFTER/SEND_RETRY 或 apply_run.settle 送的那一個事件:'
                 '程式改過了,照 SKILL.md「改到模型涵蓋的程式」更新 from_code 和模型')
    return '  SendAfter = %s\n  SendRetry = %s\n  SettleEvent = "%s"\n' % (
        nums['SEND_AFTER'].group(1), nums['SEND_RETRY'].group(1), evs[0])


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
    ap.add_argument('--cfg', default=os.path.join(HERE, 'ConfirmSend.cfg'))
    a = ap.parse_args()
    out = a.out or tempfile.mkdtemp(prefix='confirm-send-window-')
    os.makedirs(out, exist_ok=True)
    shutil.copy(os.path.join(HERE, 'ConfirmSend.tla'), out)
    with open(os.path.join(out, 'Table.tla'), 'w', encoding='utf-8') as f:
        f.write(table())
    base = open(a.cfg, encoding='utf-8').read().replace('CONSTANTS\n', 'CONSTANTS\n' + from_code())
    with open(os.path.join(out, 'ConfirmSend.cfg'), 'w', encoding='utf-8') as f:
        f.write(base)
    java, jar = tools()
    bad = 0
    for rule in RULES:
        cfg = os.path.join(out, rule + '.cfg')
        with open(cfg, 'w', encoding='utf-8') as f:   # 一條規則一份:TLC 停在第一個違規,其他規則才看得到各自的結果
            f.write(re.sub(r'INVARIANTS[\s\S]*', 'INVARIANTS\n  TypeOK\n  %s\n' % rule, base))
        r = subprocess.run([java, '-XX:+UseParallelGC', '-cp', jar, 'tlc2.TLC', '-workers', 'auto',
                            '-cleanup', '-metadir', os.path.join(out, 'states-' + rule),
                            '-config', cfg, 'ConfirmSend.tla'], cwd=out, capture_output=True, text=True)
        with open(os.path.join(out, rule + '.out'), 'w', encoding='utf-8') as f:
            f.write(r.stdout + r.stderr)
        states = re.findall(r'([\d,]+) distinct states found', r.stdout)
        if 'No error has been found' in r.stdout:
            print(f'{rule}: 通過({states[-1] if states else "?"} 個狀態)')
        else:
            bad += 1
            why = re.search(r'Error: (.*)', r.stdout)
            print(f'{rule}: 違規 — {why.group(1) if why else "TLC 沒跑完,看 " + rule + ".out"}')
            print(f'  路徑在 {os.path.join(out, rule + ".out")}')
    print('模型資料夾:', out)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
