#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jobrun —— 看板上那幾顆「跑」的按鈕(跑準備區、找新職缺)共用的進度格式。

每一支長時間的工作把進度寫成一個 JSON 檔,看板伺服器讀它回報給頁面,人或 agent 也不用一直問「好了沒」。
  phase:start → 各自的步驟 → done;沒東西可跑是 nothing,出錯是 failed。
  跑的中間一定帶 pid,讀的人才分得出「還在跑」跟「死在半路」(讀出來是 died)。
"""
import os, json, time, subprocess

END = ('', 'done', 'nothing', 'failed', 'incomplete', 'died', 'stopped')


def write(path, d):
    try:
        d = dict(d); d['at'] = time.time()
        with open(path + '.tmp', 'w', encoding='utf-8') as o:
            json.dump(d, o, ensure_ascii=False)
        os.replace(path + '.tmp', path)
    except Exception:  # noqa: S110
        pass


def is_ours(pid, names):
    """這個 pid 現在是不是我們的行程。只問「活著嗎」不夠:pid 會被系統回收給別的程式,
    重開機之後更是一定。只看活著就去殺,殺到的是不相干的行程;只看活著就當成還在跑,
    看板上的按鈕會永遠按不下去。"""
    try:
        cmd = subprocess.run(['ps', '-o', 'command=', '-p', str(int(pid))],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    return any(n in cmd for n in names)


def read(path, names):
    try:
        with open(path, encoding='utf-8') as f:
            st = json.load(f)
    except Exception:
        return {'phase': '', 'running': False}
    if st.get('phase') not in END and not is_ours(st.get('pid') or 0, names):
        st['phase'] = 'died'          # 狀態說在跑、行程卻不在了:半路被殺或當掉
    st['running'] = st.get('phase') not in END
    st['paused'] = st['running'] and os.path.exists(path + '.paused')
    st['finishing'] = st['running'] and finishing(path)
    return st


def finishing(path):
    """他按了停止、這一輪正在收工:跑的那一支看到這個就別再派新的 agent,把做完的收下就結束。"""
    return os.path.exists(path + '.finish')


def paused_seconds(path):
    """這一輪到現在一共暫停了幾秒(找缺時間上限不算暫停的時間)。"""
    try:
        with open(path + '.pausedsum', encoding='utf-8') as f:
            total = float(f.read().strip() or 0)
    except (OSError, ValueError):
        total = 0.0
    try:
        total += max(0.0, time.time() - os.path.getmtime(path + '.paused'))   # 現在還停著
    except OSError:   # 沒有暫停記號 = 現在沒停著,不用加
        pass
    return total


def clear_paused(path):
    try:
        os.remove(path + '.pausedsum')
    except OSError:   # 這一輪沒暫停過
        pass


def clear_finish(path):
    try:
        os.remove(path + '.finish')
    except OSError:   # 沒有收工記號是正常的(沒按過停止)
        pass


def tree(pid):
    """這個行程跟它底下所有的子孫(agent 的 codex、codex 開的外掛程式…)。"""
    try:
        out = subprocess.run(['ps', '-A', '-o', 'pid=,ppid='], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return [pid]
    kids = {}
    for line in out.splitlines():
        try:
            a, b = (int(x) for x in line.split())
        except ValueError:
            continue
        kids.setdefault(b, []).append(a)
    got, todo = [], [int(pid)]
    while todo:
        x = todo.pop()
        got.append(x)
        todo += kids.get(x, [])
    return got


def control(path, names, action, hold=None):
    """看板上的「⏸ 暫停」「▶ 繼續」「⏹ 停止」。回 (做了沒, 一句話)。
    暫停是把整串行程凍住(SIGSTOP),agent 停在原地、開著的頁面不動,按繼續從停下的地方接著做;
    停止:會收工的流程(狀態帶 graceful)第一次按只停掉正在跑的 agent,主程式看到收工記號就把已經做完的收下、
    正常結束;收工中再按一次,或流程不會收工,才整串收掉(做到一半的留著,卡上沒記到的下次再按一次)。"""
    import signal
    st = read(path, names)
    if not st.get('running'):
        return False, '現在沒有在跑'
    pids = tree(st.get('pid') or 0)
    mark = path + '.paused'

    def send(sig, order):
        for p in order:
            try:
                os.kill(p, sig)
            except OSError:
                pass
    if action == 'pause':
        # hold:凍住的那一下手上拿著看板的鎖(board_server 給 live_lock)。以前直接凍,剛好凍在它寫看板的半路上,
        # 鎖就一直在它手上:暫停多久,他在看板上的每一次存檔就卡多久。先拿到鎖,它就不可能凍在鎖裡。
        from contextlib import nullcontext
        with (hold or nullcontext()):
            send(signal.SIGSTOP, pids)              # 先凍住上面的,它才不會在中間再開新的子行程
        open(mark, 'w').close()
        return True, '暫停了'
    if action == 'resume':
        send(signal.SIGCONT, list(reversed(pids)))
        total = paused_seconds(path)      # 含這一段還停著的時間,記起來再清掉暫停記號
        try:
            with open(path + '.pausedsum', 'w', encoding='utf-8') as f:
                f.write(str(total))
            os.remove(mark)
        except OSError:
            pass
        return True, '繼續跑了'
    if action == 'stop' and st.get('graceful') and not finishing(path):
        open(path + '.finish', 'w').close()
        send(signal.SIGCONT, pids)                  # 凍住的先解凍,收工要它動
        send(signal.SIGTERM, pids[1:])              # 只停底下的 agent,主程式留著收工
        try:
            os.remove(mark)
        except OSError:   # 沒有暫停過就沒有暫停記號
            pass
        return True, '收工中:已經做完的會收下'
    if action == 'stop':
        clear_finish(path)
        send(signal.SIGCONT, pids)                  # 凍住的行程收不到結束訊號,先解凍
        send(signal.SIGTERM, pids)
        try:
            os.remove(mark)
        except OSError:
            pass
        write(path, dict({k: v for k, v in st.items() if k not in ('running', 'paused', 'at')},
                         phase='stopped', msg='你按了停止', finished_at=time.time()))
        return True, '停掉了'
    return False, '不知道要做什麼'
