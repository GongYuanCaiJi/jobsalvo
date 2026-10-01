#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_doc —— 板子 HTML 的「解析 + 組裝」單一真相。
以前 PRE/M1.../SUF 那段接字串在 build_board / fold_pipeline / publish_board 各抄一份;
現在只有這裡一份,誰要組板都 import。改板結構只改這裡。
"""
import re,json,sys,os,copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as _cf


def pre():
    """文件開頭。"""
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            'initial-scale=1"><title>jobsalvo</title><style id="sty">')
M1='</style></head><body><div class="wrap"><div id="hdr"></div><div id="tabs" class="tabs"></div><div id="app"></div></div><template id="t-hdr">'
M2='</template><template id="tail">'
M3='</template><script id="data-jobs" type="application/json">'
M4='</script><script id="data-fb" type="application/json">'
M5='</script><script id="app-src">'
SUF='</script></body></html>'

def inner(h,p):
    m=re.search(p,h,re.S)
    if not m: sys.exit("找不到區塊 "+p[:40])
    return m.group(1)

def parse(html):
    """把一份板子 html 拆成各區塊。data-jobs 回 dict、data-fb 回原字串(逐字搬他標記,不動)。"""
    return {
      'sty':  inner(html,r'<style id="sty">(.*?)</style>'),
      'thdr': inner(html,r'<template id="t-hdr">(.*?)</template>'),
      'tail': inner(html,r'<template id="tail">(.*?)</template>'),
      'app':  inner(html,r'<script id="app-src">(.*?)</script>'),
      'data': json.loads(inner(html,r'<script id="data-jobs" type="application/json">(.*?)</script>')),
      'fb':   inner(html,r'<script id="data-fb" type="application/json">(.*?)</script>'),
    }

def stat_first(thdr, data):
    """t-hdr 裡 stat-first 數字 = 非 bk 的職缺數。"""
    nonbk=sum(1 for j in data['jobs'] if not j.get('bk'))
    return re.sub(r'(<b id="stat-first">)\d+(</b>)', r'\g<1>%d\g<2>'%nonbk, thdr, count=1)

def assemble(sty, thdr, tail, data, fb_str, app):
    """組回可發布的完整 doc。app 不得含字面 </script>。"""
    assert '</script>' not in app, "app-src 含字面 </script>,會截斷"
    return (pre()+sty+M1+thdr+M2+tail+M3+json.dumps(data,ensure_ascii=False)
            +M4+fb_str+M5+app+SUF)


# ---- 裝回現行看板 ----------------------------------------------------------
# 管線每支都吐一個 merged.html,由這裡把它變成現行看板:驗過形狀才換,而且一律以
# 「現在看板上的標記」為準。管線跑了半小時,這半小時使用者在手機上標的東西不能被半小時前的快照蓋掉。
import os as _os
HOME = _cf.HOME
LIVE = _cf.LIVE

import fcntl as _fcntl
from contextlib import contextmanager as _cm

@_cm
def live_lock(live=LIVE):
    """跨行程序列化對現行看板的『讀→改→寫』。手機存檔(board_server)、裝板(reconcile/ctb)、
    cut_tailor 收尾可能同時想寫同一個檔;os.replace 本身原子,但『讀舊的→改→寫』整段沒鎖
    就會互蓋(A 讀、B 讀、A 寫、B 拿舊的蓋掉 A 的改動)。用檔案鎖把整段包住。"""
    f = open(live + '.lock', 'w')
    try:
        _fcntl.flock(f, _fcntl.LOCK_EX); yield
    finally:
        try: _fcntl.flock(f, _fcntl.LOCK_UN)
        finally: f.close()

def is_live(path, live=None):
    """path 是不是現行看板(不是副本)。任一邊不存在就不是。"""
    try:
        return _os.path.samefile(path, live or LIVE)
    except OSError:
        return False

# ---- 看板檔的指紋 ----
# 卡片的記號只能透過事件改(#307)。每一次正常寫入(write_doc)都在旁邊記下檔案內容的指紋;讀的時候(read_doc)
# 對不上,就是有人不經正常寫入直接改了檔(例如 agent 自己開檔改),程式停下來回報,不照改過的內容做事。
# 還沒有指紋的(升級後第一次、剛複製出來的副本)照常用,下一次正常寫入才開始記。
import hashlib as _hl


class Tampered(RuntimeError):
    """看板檔被不經正常寫入改過了。"""


def _put_file(path, b):
    """整份換上去:先寫旁邊的暫存檔再原子換名,讀的人不會讀到寫一半的。"""
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(b)
    _os.replace(tmp, path)


def _fp_path(live):
    return live + '.sha256'


def _digest(b):
    return _hl.sha256(b).hexdigest()


def _fp_get(live):
    try:
        with open(_fp_path(live), encoding='utf-8') as f:
            return set(f.read().split()) or None      # 空的指紋檔(寫到一半)當成還沒記
    except OSError:
        return None


def _fp_put(live, hashes):
    _put_file(_fp_path(live), ('\n'.join(sorted(hashes)) + '\n').encode('utf-8'))


def write_doc(live, doc):
    """正常寫入看板檔:整份換上去,記下指紋。兩個檔沒辦法一起換,所以先把新舊兩個指紋都記上、換好看板檔再只留新的;
    中途斷掉不管停在哪一步,檔案都對得上指紋。呼叫的人自己拿 live_lock。"""
    b = doc.encode('utf-8')
    new = _digest(b)
    old = _fp_get(live)
    if old is not None:
        _fp_put(live, old | {new})
    _put_file(live, b)
    _fp_put(live, {new})


TAMPERED = ('看板檔被繞過正常寫入改過了(不是看板存檔或程式寫的,可能是 agent 直接改了檔案)。'
            '程式先停下來,不照改過的內容做事。請使用者先看改了什麼(資料夾的歷史紀錄、tools/board_marks.py);'
            '確定現在的內容沒問題,由使用者自己跑 python3 tools/board_doc.py --trust {name} 照現在的內容繼續。')


def read_doc(live, locked=False):
    """讀看板檔,先對指紋。對不上丟 Tampered(訊息只講檔名,不講位置:agent 呼叫的小工具也會印出來)。
    locked:呼叫的人已經拿著 live_lock;沒拿的話對不上時先拿鎖再讀一次(可能剛好撞上別人正在寫)。"""
    with open(live, 'rb') as f:
        b = f.read()
    fp = _fp_get(live)
    if fp is None or _digest(b) in fp:
        return b.decode('utf-8')
    if not locked:
        with live_lock(live):
            return read_doc(live, locked=True)
    raise Tampered(TAMPERED.format(name=_os.path.basename(live)))


def load(board=None):
    """讀一份看板拆好(先對指紋,對不上丟 Tampered):給了 board 就讀那一份,沒給照 target() 找。
    只讀的地方都走這裡,不自己 open 看板檔(繞過指紋就會照被偷改過的內容做事)。"""
    return parse(read_doc(board or target()))


def copy_board(src, dst):
    """複製一份看板(沙箱、驗收副本、管線的工作檔):同一個位置一再複製時,舊副本的指紋跟著換掉。"""
    doc = read_doc(src)
    with live_lock(dst):
        write_doc(dst, doc)


def trust(live):
    """使用者看過、確定現在的內容沒問題:照現在的內容記指紋。agent 不准自己放行。"""
    if _os.environ.get(BOARD_ID) or _os.environ.get('AGENT_BOARD'):
        raise SystemExit('放行被改過的看板要由使用者本人做,agent 不能自己放行。')
    with live_lock(live):
        with open(live, 'rb') as f:
            _fp_put(live, {_digest(f.read())})

# ---- 標記流水帳 ----
# 每一次改到使用者的標記,把真的變了的那幾筆「從什麼變成什麼」附加一行。只附加,不覆寫,不刪。
# 看板只留最後一個狀態,寫壞了沒有第二份可以對;使用者也不可能記得自己標過什麼。
# 有這行就查得出幾點、誰、哪一筆、原本是什麼,而且救得回來(tools/board_marks.py 看紀錄、倒回)。
# 程式直接寫的(cut_tailor 收尾、閘門)也要記,不只看板伺服器的存檔:程式一次蓋掉十幾張標記的時候,
# 沒有這行就只能從舊副本拼回來。
import datetime as _dt

def journal_path(live=LIVE):
    return _os.path.splitext(live)[0] + '-marks.jsonl'

def journal(live, chg, by=''):
    """附加一行;寫不進去丟 OSError(rewrite 照樣存檔,並把這件事寫進看板的回報)。"""
    if not chg: return
    rec = {'t': _dt.datetime.now().isoformat(timespec='seconds'), 'n': len(chg), 'd': chg}
    if by: rec['by'] = by
    with open(journal_path(live), 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')

def fb_diff(old, new):
    return {k: [old.get(k), new.get(k)] for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)}


# ---- 派出去的 agent 用哪一份看板 ----
# agent 不拿看板檔的位置(#307):拿到位置它就能不經狀態表直接改檔。派它的程式只給一個代號(環境變數 BOARD_ID),
# 代號對到哪一份記在程式自己的暫存資料夾;agent 呼叫的小工具照代號找回那一份。
BOARD_ID = 'JOBSALVO_BOARD_ID'


def _ids_dir():
    return _os.path.join(_cf.TMP, 'agent-boards')


def board_id(path):
    """登記這份看板、回傳它的代號(同一份看板永遠同一個代號,不用清)。"""
    real = _os.path.realpath(path)
    tok = _digest(real.encode('utf-8'))[:16]
    d = _ids_dir()
    _os.makedirs(d, exist_ok=True)
    f = _os.path.join(d, tok)
    try:
        with open(f, encoding='utf-8') as fh:
            if fh.read() == real:
                return tok
    except OSError:
        pass
    _put_file(f, real.encode('utf-8'))
    return tok


def _by_id():
    tok = _os.environ.get(BOARD_ID)
    if not tok:
        return None
    try:
        if not re.fullmatch(r'[0-9a-f]{16}', tok):
            raise OSError
        with open(_os.path.join(_ids_dir(), tok), encoding='utf-8') as fh:
            return fh.read()
    except OSError:
        # 找不到就停:不能默默改到現行看板(這一輪可能是在副本上跑)
        raise SystemExit(f'找不到這一輪的看板(代號 {tok});請從看板重新開始這一步。') from None


def target(live=None):
    """要寫哪一份看板:派 agent 的程式指定的(AGENT_BOARD、agent 拿到的代號)優先,再來是呼叫的人給的,最後是現行看板。
    派出去的那一輪(和它叫的小工具)不管呼叫的人給了哪個路徑,都只寫派它的程式指定的那一份(#307)。
    set_fb、set_data 都先照這裡挑;rewrite 是最底層,給它哪一份就寫哪一份。"""
    return _os.environ.get('AGENT_BOARD') or _by_id() or live or LIVE


SKIP = object()      # rewrite 的 fn 回這個:這次不寫(例如看板存檔撞到別的裝置先改過)


def _fb_json(fb):
    """標記序列化:跳脫 <,內容絕不可能生出字面 </script> 提早關掉 script 區塊(JSON.parse 會還原,語意不變)。"""
    return json.dumps(fb, ensure_ascii=False).replace('<', '\\u003c')


def rewrite(fn, live=None, by=''):
    """看板檔唯一的寫入(#308):拿看板鎖 → 讀現行的 → fn 改 → 組回 → 原子換檔 → 標記有變就記流水帳。
    所有寫看板檔的地方(看板存檔、程式改標記、裝職缺資料、重灌外殼)都走這裡:鎖外讀、鎖內寫的
    「讀舊的 → 改 → 寫回」會蓋掉這之間別人寫的東西。
    fn(parts) 就地改 parts(sty、thdr、tail、app 是字串,data、fb 是 dict),回傳值原樣回給呼叫的;
    回 SKIP 就不寫。頁首數字一律跟著職缺數走;標記沒變就逐字保留原本那段。
    live 給了就寫那一份,不再看 AGENT_BOARD(要照派 agent 的程式指定的那一份走,先用 target() 挑);沒給照 target()。"""
    live = live or target()
    with live_lock(live):
        p = parse(read_doc(live, locked=True))
        raw = p['fb']
        before = json.loads(raw)
        p['fb'] = json.loads(raw)
        out = fn(p)
        if out is SKIP:
            return out
        fb_str = raw if p['fb'] == before else _fb_json(p['fb'])
        write_doc(live, assemble(p['sty'], stat_first(p['thdr'], p['data']), p['tail'], p['data'], fb_str, p['app']))
        try:
            journal(live, fb_diff(before, p['fb']), by or _os.path.basename(sys.argv[0] or '') or 'script')
        except OSError as e:
            # 流水帳壞掉不能擋住存檔(改動已經寫進去了),但他要知道:標記寫壞時就是靠它救。回報在同一把鎖內補寫進看板
            import agent_report
            agent_report.apply_report(p['fb'], '看板', f'標記流水帳寫不進去,這次的改動沒有留紀錄:{e.strerror or e}',
                                      need=f'看 {_os.path.basename(journal_path(live))} 是不是被刪、被鎖或磁碟滿了')
            write_doc(live, assemble(p['sty'], stat_first(p['thdr'], p['data']), p['tail'], p['data'],
                                     _fb_json(p['fb']), p['app']))
    return out


def set_fb(mutate, live=None, by=''):
    """改使用者的標記(data-fb)。
    mutate 收到 fb dict、就地改、不用回傳。每一次都記進流水帳(by 寫是誰改的,預設是程式名)。"""
    def fn(p):
        mutate(p['fb'])
        return p['fb']
    return rewrite(fn, target(live), by)


def set_data(mutate, live=None):
    """改職缺資料(data-jobs)。在鎖裡讀現行看板、改、寫回,不拿舊快照蓋。
    mutate(data, fb) 就地改 data;fb 只給它讀。"""
    def fn(p):
        mutate(p['data'], copy.deepcopy(p['fb']))
    rewrite(fn, target(live))


def main():
    import argparse
    ap = argparse.ArgumentParser(description='看板檔被繞過正常寫入改過、你看過確定沒問題時:照現在的內容繼續。')
    ap.add_argument('--trust', metavar='BOARD', nargs='?', const='', required=True,
                    help='哪一份看板(檔名或路徑;不給就是現行看板)')
    a = ap.parse_args()
    live = a.trust or LIVE
    if not _os.path.isabs(live) and not _os.path.exists(live):
        live = _os.path.join(_os.path.dirname(LIVE), live)
    trust(live)
    print(f'照現在的內容繼續:{_os.path.basename(live)}')


if __name__ == '__main__':
    main()
