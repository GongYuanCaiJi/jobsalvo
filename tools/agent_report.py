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
看板用哪一份:派 agent 的程式指定的那一份(board_doc.target;agent 只拿到代號、不拿位置),再來是 --board,再沒有就是現行看板。
"""
import os, sys, argparse, datetime, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd      # noqa: E402

KEY = '__inbox__'
# 回報的來源由派 agent 的程式給(#316):agent_run 把這一輪的來源放進這個環境變數,agent 跑這支時照它記,自己寫的 --from 不算數
REPORT_FROM_ENV = 'JOBSALVO_REPORT_FROM'
# 幫你填表那個流程的來源代號。存在資料裡的是舊叫法「代投」(舊回報也這樣存,改掉要搬資料);看板照 GLOSSARY 顯示「幫你填表」
FROM_APPLY = '代投'
# 回報來源是存在資料裡的代號(舊的回報也是這樣存的):畫面上照 GLOSSARY 現在的叫法,和「去看」跳到哪一頁(看板照這份畫)
FROM = {'找新職缺': ['找新職缺', 'discover'], '逐張判斷': ['逐張判斷', 'discover'], '跑準備區': ['跑準備區', 'prep'],
        '查回音': ['查應徵進度', 'sent'], FROM_APPLY: ['幫你填表', 'ship']}
NO_SHOT = '這一輪沒有程式自己截的那一頁'
KEEP_DONE = 200        # 處理好的只留最近這麼多則:看板每次都整份讀寫這一格,永遠不清會越來越肥


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def apply_report(fb, src, msg, need='', job='', now=None, by_agent=False, approve=None):
    """純函式版(測試用)。回傳那一筆。
    by_agent:agent 自己寫的(不是程式驗出來的):標 agent;還沒附程式自己截的那一頁就標 noev(缺證據),
    不叫他照做(todo 不列),等程式附上截圖(apply_attach)才算數(#315)。
    approve:他按「處理好了」就算確認的平台內容(apply_run._take_confirmed 下次判讀時收下)。"""
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
            if approve:
                it['approve'] = approve
            if by_agent:            # 同一句又報一次:這一次要附的是這一輪的截圖
                it['agent'] = True
                it.pop('ev', None)
                it['noev'] = NO_SHOT
            return it
    it = {'id': 'r' + hashlib.sha1(f'{src}|{job}|{msg}|{now}'.encode()).hexdigest()[:10],
          'at': now, 'from': src, 'msg': msg, 'n': 1}
    if need:
        it['need'] = need
    if job:
        it['job'] = job
    if approve:
        it['approve'] = approve
    if by_agent:
        it['agent'], it['noev'] = True, NO_SHOT
    box.append(it)
    done = sorted((x for x in box if x.get('done')), key=lambda x: (str(x.get('done')), str(x.get('at'))))
    if len(done) > KEEP_DONE:
        old = {id(x) for x in done[:len(done) - KEEP_DONE]}
        box[:] = [x for x in box if id(x) not in old]
    return it


def report(src, msg, need='', job='', live=None, by_agent=False, approve=None):
    live = bd.target(live)
    out = []
    bd.set_fb(lambda fb: out.append(apply_report(fb, src, msg, need, job, by_agent=by_agent, approve=approve)), live=live,
              by='agent_report')
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
    live = bd.target(live)
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
    bd.set_fb(f, live=bd.target(live), by='agent_report')
    return n[0]


def apply_attach(fb, job, since, ev):
    """這一輪(since 之後)留給他的、這張卡的回報,附上程式自己截的那一頁(ev:證據夾裡的 <輪>/<檔名>,#315)。
    這一輪沒有截圖就照實標出來(noev),不假裝有。回附了幾則。"""
    n = 0
    for it in fb.get(KEY, []):
        if it.get('done') or it.get('job') != job or str(it.get('at') or '') < since or it.get('ev'):
            continue
        if ev:
            it['ev'] = ev
            it.pop('noev', None)
        else:
            it['noev'] = NO_SHOT
        n += 1
    return n


def open_items(fb):
    return [it for it in fb.get(KEY, []) if not it.get('done')]


def todo(fb):
    """要他處理的:還開著的回報,除了 agent 自己寫、又沒有程式自己截的那一頁的(缺證據,不叫他照做,#315)。
    看板的「要你處理」照它(下一步的 __inbox__)。"""
    return [it for it in open_items(fb) if not (it.get('agent') and it.get('noev'))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from', dest='src', required=True, help='哪一段在回報:代投、找新職缺、跑準備區…')
    ap.add_argument('--job', default='', help='跟哪一張職缺有關(網址)')
    ap.add_argument('--need', default='', help='他要做什麼')
    ap.add_argument('--board', default='')
    ap.add_argument('msg', help='發生了什麼(繁體中文、白話、一句講清楚)')
    a = ap.parse_args()
    # 派 agent 的程式給了來源就照它記:agent 自己寫的 --from 不算數(#316;它取的名字程式認不得、收不掉)
    src = os.environ.get(REPORT_FROM_ENV) or a.src
    it = report(src, a.msg, a.need, a.job, live=a.board or None, by_agent=True)
    print(f"回報了({it['id']},第 {it['n']} 次):{it['msg']}")


if __name__ == '__main__':
    main()
