#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_report —— agent 回報給使用者的唯一管道:做不到、需要本人處理的事,寫進看板最上面的「📣 回報」。

找新職缺、跑準備區、代投都交給 agent 之後,它碰到做不到的事(例如某些權限、要本人登入)不能只寫在
自己的紀錄檔裡,使用者看不到。兩條路都寫進同一個地方(看板資料的 __inbox__):
  · agent 自己回報:agent_run 在每一個 agent 的 prompt 前面都放了回報規矩,叫它跑這支。
  · 程式替它回報:跑完之後程式自己驗出的問題(apply_run 的填表檢查、送出沒確認成功…)直接呼叫 report(),
    不靠 agent 記得。
同一件事(誰回報、哪張職缺、同一句話)還沒處理之前再回報一次,只把次數加一、時間更新,不會洗版。

用法:
  uv run python tools/agent_report.py --from 代投 [--job 職缺網址] [--need 他要做什麼] "發生了什麼"
  import agent_report; agent_report.report('代投', '發生了什麼', need='…', job=url)
看板用哪一份:--board,沒給就看環境變數 AGENT_BOARD(派 agent 的程式會設),再沒有就是現行看板。
"""
import os, sys, argparse, datetime, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd      # noqa: E402

KEY = '__inbox__'
KEEP_DONE = 200        # 處理好的只留最近這麼多則:看板每次都整份讀寫這一格,永遠不清會越來越肥


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def apply_report(fb, src, msg, need='', job='', now=None):
    """純函式版(測試用)。回傳那一筆。"""
    now = now or _now()
    msg, need, job, src = (msg or '').strip(), (need or '').strip(), (job or '').strip(), (src or 'agent').strip()
    if not msg:
        raise ValueError('回報要寫發生了什麼')
    box = fb.setdefault(KEY, [])
    for it in box:
        if not it.get('done') and (it.get('from'), it.get('job', ''), it.get('msg')) == (src, job, msg):
            it['n'] = it.get('n', 1) + 1
            it['at'] = now
            if need:
                it['need'] = need
            return it
    it = {'id': 'r' + hashlib.sha1(f'{src}|{job}|{msg}|{now}'.encode()).hexdigest()[:10],
          'at': now, 'from': src, 'msg': msg, 'n': 1}
    if need:
        it['need'] = need
    if job:
        it['job'] = job
    box.append(it)
    done = sorted((x for x in box if x.get('done')), key=lambda x: (str(x.get('done')), str(x.get('at'))))
    if len(done) > KEEP_DONE:
        old = {id(x) for x in done[:len(done) - KEEP_DONE]}
        box[:] = [x for x in box if id(x) not in old]
    return it


def report(src, msg, need='', job='', live=None):
    live = live or os.environ.get('AGENT_BOARD') or bd.LIVE
    out = []
    bd.set_fb(lambda fb: out.append(apply_report(fb, src, msg, need, job)), live=live, by='agent_report')
    return out[0]


def apply_resolve(fb, job, why, day=None, only=None):
    """那張卡後來做成功了:它之前還開著的回報都收進「已處理」,寫上為什麼(看板照舊可以改回還沒處理)。回收了幾則。
    only(回報) 回 True 的才收:例如可投遞夾建好只收建置的回報,不要把「送出沒確認成功」一起收掉。"""
    day = day or datetime.date.today().isoformat()
    n = 0
    for it in fb.get(KEY, []):
        if not it.get('done') and it.get('job') == job and (only is None or only(it)):
            it['done'] = day
            it['res'] = why
            n += 1
    return n


def resolve(job, why, live=None, only=None):
    live = live or os.environ.get('AGENT_BOARD') or bd.LIVE
    out = []
    bd.set_fb(lambda fb: out.append(apply_resolve(fb, job, why, only=only)), live=live, by='agent_report')
    return out[0]


def resolve_from(src, why, before, live=None):
    """同一段流程重跑成功:before(這一輪開始的時間,ISO)之前留下、還開著的回報都收掉。
    這一輪自己報的不收;上一輪的問題如果還在,這一輪會再報一次。"""
    day = datetime.date.today().isoformat()
    n = [0]
    def f(fb):
        for it in fb.get(KEY, []):
            if not it.get('done') and str(it.get('from', '')).startswith(src) and str(it.get('at', '')) < before:
                it['done'], it['res'] = day, why
                n[0] += 1
    bd.set_fb(f, live=live or os.environ.get('AGENT_BOARD') or bd.LIVE, by='agent_report')
    return n[0]


def open_items(fb):
    return [it for it in fb.get(KEY, []) if not it.get('done')]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from', dest='src', required=True, help='哪一段在回報:代投、找新職缺、跑準備區…')
    ap.add_argument('--job', default='', help='跟哪一張職缺有關(網址)')
    ap.add_argument('--need', default='', help='他要做什麼')
    ap.add_argument('--board', default='')
    ap.add_argument('msg', help='發生了什麼(繁體中文、白話、一句講清楚)')
    a = ap.parse_args()
    it = report(a.src, a.msg, a.need, a.job, live=a.board or None)
    print(f"回報了({it['id']},第 {it['n']} 次):{it['msg']}")


if __name__ == '__main__':
    main()
