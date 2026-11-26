#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_report —— agent 回報給使用者的唯一管道:做不到、需要本人處理的事,寫進看板最上面的「📣 回報」。

找新職缺、跑準備區、代投都交給 agent 之後,它碰到做不到的事(例如某些權限、要本人登入)不能只寫在
自己的紀錄檔裡,使用者看不到。兩條路都寫進同一個地方(看板資料的 __inbox__):
  · agent 自己回報:agent_run 在每一個 agent 的 prompt 前面都放了回報規矩,叫它跑這支。
  · 程式替它回報:任務程式自己記下的交件問題直接呼叫 report(),
    不靠 agent 記得。
同一件事(誰回報、哪張職缺、同一句話)還沒處理之前再回報一次,只把次數加一、時間更新,不會洗版。

用法:
  uv run python tools/agent_report.py --from 代投 [--job 職缺網址] [--need 他要做什麼] [--url 要去的那一頁] "發生了什麼"
  import agent_report; agent_report.report('代投', '發生了什麼', need='…', job=url)
看板用哪一份:派 agent 的程式指定的那一份(board_doc.target;agent 只拿到代號、不拿位置),再來是 --board,再沒有就是現行看板。
"""
import os, sys, argparse, datetime, hashlib, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd      # noqa: E402

# 回報的來源由派 agent 的程式給(#316):agent_run 把這一輪的來源放進這個環境變數,agent 跑這支時照它記,自己寫的 --from 不算數
REPORT_FROM_ENV = 'JOBSALVO_REPORT_FROM'
# 幫你填表那個流程的來源代號。存在資料裡的是舊叫法「代投」(舊回報也這樣存,改掉要搬資料);看板照 GLOSSARY 顯示「幫你填表」
FROM_APPLY = '代投'
# 回報來源是存在資料裡的代號(舊的回報也是這樣存的):畫面上照 GLOSSARY 現在的叫法,和「去看」跳到哪一頁(看板照這份畫)
FROM = {'找新職缺': ['找新職缺', 'discover'], '逐張判斷': ['逐張判斷', 'discover'], '跑準備區': ['跑準備區', 'prep'],
        '平台履歷同步': ['全部同步', 'cfg'],
        '查回音': ['查應徵進度', 'sent'], FROM_APPLY: ['幫你填表', 'ship']}
RETRY = '__retry__'    # 每件事連續沒做完幾輪:{件: 輪數}
RETRY_ROUNDS = 3       # 本地規則:連續幾輪沒做完才告訴他;主流沒有適用的數字(docs/research/board-decision-load.md)


def settle(fb, src, scope, marks, *, head, need, job='', url='', now=None):
    """一件事(scope:'sync:平台/語言/履歷'、'fill:卡'、'progress')做完一輪,要不要打斷他(#807)。
    marks:這一輪沒乾淨收尾的原因(web.round.stuck);被他自己停掉的那一輪不算一輪,不要叫這支。
      · 交出的那一頁程式一字不差核對到引句(HANDED_OVER):當輪進要你做;同一種任務、同一個網站、交件單寫的同一件事(原字)只一則,
        寫出卡住幾件,點下去打開那一頁。不同任務分開:處理好之後要重跑的不一樣(from、need 各自的)。
      · 其他沒做完:記一輪、不打擾,下一輪重做;連續 RETRY_ROUNDS 輪才開一則,寫出試了幾輪、卡在哪。
      · 乾淨收尾:輪數歸零。
    這件事上一輪開的、這一輪不成立的收掉;升級前開頭是 head 的舊回報一併收掉。回連續沒做完幾輪。"""
    now = now or bd.stamp()
    handed = next((dict(m.params) for m in marks if m.code == 'HANDED_OVER'), None)
    rounds = 0 if not marks or handed else (fb.get(RETRY) or {}).get(scope, 0) + 1
    if rounds:
        fb.setdefault(RETRY, {})[scope] = rounds
    else:
        (fb.get(RETRY) or {}).pop(scope, None)
    if handed:
        said = [dict(m.params)['why'] for m in marks if m.code == 'NEEDS_USER'] or [handed['quote']]
        host, todo = _host(handed['url']), '；'.join(said)
        key, line, page = '\n'.join(('handoff:' + src, host, todo)), f'{head}：{todo}', handed['url']
    elif rounds >= RETRY_ROUNDS:
        key, page = 'retry:' + scope, url or bd.page_of(fb, job)
        line = f'{head}連續 {rounds} 輪沒做完：\n' + '\n'.join(dict.fromkeys(m.text for m in marks))
    else:
        key = None
    for it in open_items(fb):
        parts = it.get('parts')
        if parts is None and it.get('from') == src and str(it.get('msg', '')).startswith(head):
            it.update(done=now[:10], res='這件事後來重新跑過了')
        elif parts and scope in parts and it.get('key') != key:
            del parts[scope]
            _show(it) if parts else it.update(done=now[:10], res='後來重新跑過,這件事不再卡住')
    if key:
        it = next((x for x in open_items(fb) if x.get('key') == key), None)
        if it is None:
            it = {'id': 'r' + hashlib.sha256(f'{key}|{now}'.encode()).hexdigest()[:10], 'from': src, 'key': key,
                  'need': need, 'parts': {}, 'n': 1}
            if handed:
                it['host'] = host
            fb.setdefault(bd.KEY, []).append(it)
        it['parts'][scope] = {'line': line, 'job': job, 'url': page}
        it['at'] = now
        _show(it)
    return rounds


def _host(url):
    """同一個網站:主機名(有埠就連埠,本機假網站一個埠是一個站)。"""
    parts = urllib.parse.urlsplit(str(url or ''))
    return (parts.hostname or '') + (f':{parts.port}' if parts.port else '')


def _show(it):
    """照還卡住的那幾件重寫這一則的字與要去的那一頁(最早卡住的那件);只卡一張卡時掛在那張卡上(看板「去看那張卡」)。"""
    parts = list(it['parts'].values())
    jobs = [p['job'] for p in parts if p.get('job')]
    it.pop('job', None)
    it.update({'job': jobs[0]} if len(jobs) == 1 == len(parts) else {}, url=parts[0]['url'])
    if it['key'].startswith('retry:'):
        it['msg'] = next(iter(parts))['line']
        return
    count = f'，卡住 {len(parts)} {"張卡" if len(jobs) == len(parts) else "件"}' if len(parts) > 1 else ''
    it['msg'] = f'{it["host"]} 要你本人處理{count}：\n' + '\n'.join(p['line'] for p in parts)


def report(src, msg, need='', job='', live=None, by_agent=False, url='', info=False, ev=None):
    live = bd.target(live)
    out = []
    bd.set_fb(lambda fb: out.append(bd.apply_report(fb, src, msg, need, job, by_agent=by_agent,
                                                 url=url, info=info, ev=ev)),
              live=live, by='agent_report')
    return out[0]


def tell(src, msg, need='', **kwargs):
    """report,寫不進看板就印進這一輪的紀錄(看板上「看紀錄」看得到),不讓一輪因為回報寫不進去而中斷。"""
    try:
        return report(src, msg, need, **kwargs)
    except Exception as error:  # noqa: BLE001 — 回報寫不進看板:印進這一輪的紀錄
        print(f'⚠ 這則回報寫不進看板({str(error)[:120]}):{msg} → {need}')
        return None


def apply_resolve(fb, job, why, day=None, src=None, before=None, only=None):
    """收掉還開著的回報(收進「已處理」,寫上為什麼;看板照舊可以改回還沒處理)。回收了幾則。
    job:哪一張卡的(None 是不分卡;'' 是沒掛在卡上的)。src:只收這個流程開的(來源開頭一樣)。
    before:只收這個時間(ISO)之前開的:同一個流程重跑完,這一輪又報的(時間是這一輪的)留著,上一輪的收掉。
    only(回報) 回 True 的才收:同一個來源裡只有某一種回報講的事不成立了(當掉的那幾則、某一份平台履歷的)。
    併了好幾張卡的那一則(settle 的 parts):只拿掉這張卡那幾件,其他卡還卡著就留著、重寫字。"""
    day = day or datetime.date.today().isoformat()
    n = 0
    for it in fb.get(bd.KEY, []):
        if (it.get('done') or src is not None and not str(it.get('from', '')).startswith(src)
                or before is not None and str(it.get('at', '')) >= before or only is not None and not only(it)):
            continue
        parts = it.get('parts')
        if job is not None and parts:
            mine = [scope for scope, part in parts.items() if part.get('job') == job]
            for scope in mine:
                del parts[scope]
            if not mine or parts:
                _show(it) if mine else None
                continue
        elif job is not None and it.get('job', '') != job:
            continue
        it['done'] = day
        it['res'] = why
        n += 1
    return n


def resolve(job, why, live=None, src=None, before=None, only=None):
    out = []
    bd.set_fb(lambda fb: out.append(apply_resolve(fb, job, why, src=src, before=before, only=only)), live=bd.target(live),
              by='agent_report')
    return out[0]


def open_items(fb):
    return [it for it in fb.get(bd.KEY, []) if not it.get('done')]


def todo(fb):
    """要他處理的:還開著的回報,除了 agent 自己寫、又沒有程式自己截的那一頁的(缺證據,不叫他照做,#315)。
    看板的「要你處理」照它(下一步的 __inbox__)。"""
    return [it for it in open_items(fb) if not it.get('info') and not (it.get('agent') and it.get('noev'))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from', dest='src', required=True, help='哪一段在回報:代投、找新職缺、跑準備區…')
    ap.add_argument('--job', default='', help='跟哪一張職缺有關(網址)')
    ap.add_argument('--need', default='', help='他要做什麼')
    ap.add_argument('--url', default='', help='他要去哪一頁做(網址);跟某張卡有關又沒給,就附 agent 填表停在的那一頁')
    ap.add_argument('--board', default='')
    ap.add_argument('msg', help='發生了什麼(繁體中文、白話、一句講清楚)')
    a = ap.parse_args()
    # 派 agent 的程式給了來源就照它記:agent 自己寫的 --from 不算數(#316;它取的名字程式認不得、收不掉)
    src = os.environ.get(REPORT_FROM_ENV) or a.src
    it = report(src, a.msg, a.need, a.job, live=a.board or None, by_agent=True, url=a.url)
    print(f"回報了({it['id']},第 {it['n']} 次):{it['msg']}")


if __name__ == '__main__':
    main()
