#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
next_step —— 一張卡的下一步(GLOSSARY「下一步」、docs/adr/0005):現在等你(附原因)、agent 會自己做、或沒有要做的。

一張卡的判斷只在這裡算一份:看板照它畫(按鈕能不能按、不能按的原因),後台收到事件也照它決定收不收,
擋的原因就是卡上寫的那一句。只讀不寫、不派東西(測試直接測它)。

用法:
  import next_step
  next_step.of(fb, jobs, status, flow=設定的 flow)   # {卡: 這張的下一步(見 card)}
  next_step.answers(fb)                              # 常用答案整份的:{'in_use', 'need', 'ask', 'locked', 'users'}(見 answers)
  next_step.refuse(fb, url, event, status)           # 這個事件現在收不收:不收回原因

auto 是 agent 會不會接手:自動流程(autopilot.plan)只照它挑卡,卡上寫「會自動…」也只照它,兩邊是同一個欄位。
"""
import contextlib
import datetime
import hashlib
import json
import math
import re

import config as cf
import delivery_state as ds
import form_record as fr


def busy(m):
    """這張忙不忙(狀態表上的 busy):忙就回原因,不忙回 None。"""
    why = ds.BUSY[ds.state(m)]
    return why.format(agent=cf.AGENT) if why else None


def fill_cap(flow):
    """停著的頁最多幾張(flow.fill_max):0 = 不限;空的、看不懂的照預設 5;小數無條件捨去;負的當 0。"""
    v = (flow or {}).get('fill_max')
    try:
        n = float(str(v).strip())
    except ValueError:
        return 5
    return max(0, math.trunc(n)) if math.isfinite(n) else 5


def mine(fb, job):
    """自動流程管不管這張:自動流程開過(有 __auto__)、不是開啟當下就在流程裡的舊卡、沒移除、公司沒被封鎖。"""
    a = fb.get('__auto__')
    m = fb.get(job['id'])
    return (isinstance(a, dict) and job['id'] not in (a.get('skip') or [])
            and not (isinstance(m, dict) and m.get('rm')) and not ds.company_blocked(fb, job))


def _move(fb, job, flow, tried, status, build):
    """準備區、待你決定的卡,自動流程會不會往下推:準備區還沒準備過(上一輪沒產出的等他)就準備;
    待你決定、投遞前把關沒擋的,驗收跑完就推到可以投了。推過一次、他又退回來的不再推。
    推進要等這張進待你決定之後的那一輪建置和投遞前驗收跑完(自動流程記在 __auto__.seen:要等到第幾輪),
    不拿舊的驗收結果放行:還沒跑完 wait 是 True。build:{'gen': 跑完幾輪, 'running': 現在有沒有在跑, 'real': 是不是真的看板}
    (副本沒有建置,不等)。"""
    i = job['id']
    stage = (fb.get(i) or {}).get('app') if isinstance(fb.get(i), dict) else None
    if not mine(fb, job):
        return None
    if stage == 'prep' and flow.get('auto_prep') and not job.get('prep_note') and 'prep:' + i not in tried:
        return {'do': 'prep', 'mark': 'prep:' + i, 'line': '會自動準備,好了自己往下走'}
    if stage == 'ready' and flow.get('auto_advance') and 'adv:' + i not in tried and not fr.ship_blocked(fb, job, status):
        seen = (fb.get('__auto__') or {}).get('seen') or {}
        wait = build['real'] and (i not in seen or build['running'] or build['gen'] <= int(seen[i]))
        return {'do': 'advance', 'mark': 'adv:' + i, 'wait': wait, 'line': '驗收跑完就自動進「可以投了」(不想等可以直接按)'}
    return None


def _fill(fb, job, flow, tried, full):
    """自動流程會不會替這張填、重填:還沒填、上傳的是舊檔、頁面不見了,這一次還沒自動填過,停著的頁沒滿
    (已經停著的那張要重填不算新的)。"""
    i = job['id']
    m = fb.get(i) if isinstance(fb.get(i), dict) else {}
    s = ds.state(m)
    if not flow.get('auto_fill') or m.get('app') != 'ship' or s not in ds.AUTO_FILL or not mine(fb, job):
        return None
    a = m.get('apply') or {}
    # 「🔁 再投一次」把上一次的表單和填表紀錄收進 tries:第幾次投要算進記號,不然跟第一次一樣是 fill:<id>:new
    n = len(m.get('tries') or []) + len(m.get('history') or [])
    mark = 'fill:' + i + ':' + str(a.get('at') or 'new') + (':' + str(n) if n else '')
    if mark in tried or (full and not ds.held(fb, i, job)):
        return None
    return {'do': 'fill', 'mark': mark, 'line': ds.AUTO_FILL[s].format(agent=cf.AGENT, stale=a.get('stale') or '要寄的檔案換過了')}


# 答案改完多久沒再動才叫 agent 重打:他常常一次改好幾條(或同一條改兩次),
# 改一條就派一次,agent 會在頁面上重打好幾輪。副本沒有真的 agent,等短一點。
QUIET = {True: 60, False: 3}


def _refix(fb, i):
    """這張等著照新答案重打的:表單上標 refill 的欄位、要重翻的答案(tr)。"""
    import apply_run                                 # 要重翻的判斷只有一份,在 apply_run
    f = ((fb.get(i) or {}).get('form') or {}).get('f') or []
    return [x for x in f if x.get('refill')], apply_run.to_translate(fb, i)


def fix_sig(fb, i):
    """這張等著照新答案重打的是哪幾條、常用答案現在寫什麼。空字串 = 沒有要重打的。
    值也算進去:同一條又改一次,要重新等他停手。"""
    refill, tr = _refix(fb, i)
    bank = {e.get('k'): [e.get('v'), e.get('zh')] for e in fb.get('__ans__') or [] if isinstance(e, dict)}
    ks = sorted(set(str(x.get('k') or x.get('q')) for x in refill) | set('tr:' + str(t['k']) for t in tr))
    if not ks:
        return ''
    raw = json.dumps([[k, bank.get(k[3:] if k.startswith('tr:') else k)] for k in ks], ensure_ascii=False)
    return hashlib.blake2b(raw.encode('utf-8'), digest_size=8).hexdigest()   # 只是「有沒有又改過」的指紋,不是卡片 ID


def _fix(fb, job, flow, tried, rf, now, real, ctx=None):
    """答案改過、停著的頁(填好了、叫得回那隻 agent)要照新答案重打、重翻:他停手 QUIET 秒後自動叫回那隻 agent。
    回 (auto, stop):stop 是本來會自動重打、這次不打的原因(要你處理)。
    同一組「哪幾條、什麼值」只重打一次(記號 fix:<id>:<指紋>);還有答案等他確認的不重打(確認完再一次打);
    只缺證據的不重打,卡上是缺證據的回報。"""
    i = job['id']
    m = fb.get(i) if isinstance(fb.get(i), dict) else {}
    if (not flow.get('auto_fill') or m.get('app') != 'ship' or ds.state(m) != 'parked'
            or not (m.get('apply') or {}).get('session') or not mine(fb, job)):
        return None, None
    sig = fix_sig(fb, i)
    if not sig:
        return None, None
    if fr.answers_pending(fb, i):
        return None, (None if ask(fb, i, ctx) else fr.NO_EVIDENCE)
    refill, tr = _refix(fb, i)
    what = '、'.join((['重翻 %d 條' % len(tr)] if tr else []) + (['重打 %d 欄' % len(refill)] if refill else []))
    mark = 'fix:' + i + ':' + sig
    if mark in tried:
        return None, '{} 已經自動照新答案{}過一次,網頁上還是沒改好,不會再自動重試;看過頁面再叫它改'.format(cf.AGENT, what)
    r = rf.get(i) or {}
    left = QUIET[bool(real)]
    if r.get('sig') == sig:                          # 自動流程已經看過這一次改動:從那時候開始算
        with contextlib.suppress(TypeError, ValueError):     # 記的時間讀不懂:當成剛看到,從頭等
            left -= (now - datetime.datetime.fromisoformat(r.get('since'))).total_seconds()
    line = '答案改過,你停手{}後 {} 會自動照新答案{}'.format('一分鐘' if real else ' %d 秒' % QUIET[False], cf.AGENT, what)
    return {'do': 'fix', 'mark': mark, 'sig': sig, 'wait': max(0, left), 'line': line}, None


def _ask_ctx(fb):
    """等你的答案鍵、常用答案有的鍵:整份看板算一次(每張卡各算一次會變成卡數 × 答案數 × 表單數)。"""
    return ({e['k'] for e, _ in fr.find_pending(fb)},
            {e.get('k') for e in fb.get('__ans__') or [] if isinstance(e, dict)})


def ask(fb, url, ctx=None):
    """這張表單還在等你的常用答案(鍵,照表單順序):推論的、空的、或常用答案裡沒有那一條;缺證據的不叫你確認。
    已投遞的表單不算。跟常用答案的 ⚠ 同一個算法(form_record.find_pending),在用的表單看整份看板,不只畫得出來的卡。"""
    f = (fb.get(url) or {}).get('form') if isinstance(fb.get(url), dict) else None
    if not f or f.get('lock'):
        return []
    waiting, have = ctx or _ask_ctx(fb)
    out = []
    for x in f.get('f') or []:
        k = x.get('k')
        if k and x.get('src') == 'bank' and (k in waiting or k not in have) and k not in out:
            out.append(k)
    return out


def answers(fb):
    """常用答案整份的下一步:in_use 是還沒送出的表單在用的那幾條(看整份看板,不只畫得出來的卡)——
    按刪除是「清掉答案」(題目留著、下一輪 agent 代填);其他的是整條刪掉(form_record.redo 同一條)。"""
    bank = [e for e in fb.get('__ans__') or [] if isinstance(e, dict) and e.get('k')]
    users = {e['k']: fr.users(fb, e['k']) for e in bank}
    unsure = {u for u, m in fb.items() if isinstance(m, dict) and ds.state(m) == 'unsure'}
    return {'in_use': [e['k'] for e in bank if not all(users[e['k']].values())],
            # 等你的(推論的、空著又有還沒送出的表單在用):need 含缺證據的(確認送出照樣擋),ask 是叫你確認的那幾條
            'need': [e['k'] for e, _ in fr.waiting(fb)], 'ask': [e['k'] for e, _ in fr.find_pending(fb)],
            # 送出結果不明的卡在用的:先不給改(狀態表修正 14),等他查過到底送出沒有
            'locked': [k for k, us in users.items() if unsure & set(us)],
            'users': {k: [[u, lk] for u, lk in us.items()] for k, us in users.items()}}


def card(fb, url, status=None, auto=None, stop=None, ctx=None, job=None):
    """一張卡的下一步。confirm:「✅ 確認送出」不能按的原因;busy:agent 正在做或送出結果不明,
    這張不能換檔、不能離開流程的原因;auto:agent 會自己接手做什麼({do, mark, line}),不會就是 None;
    stop:自動流程本來會接、這次不接的原因(要你處理);ask:這張表單在等你的常用答案;
    send:確認過的這張現在能不能送出(None = 可以);view:卡上幫你填表那一塊畫什麼(見 view);
    gate:投遞前把關擋住的原因(空字串 = 不擋),closed:擋的是 agent 判的職缺關了(給「不對,職缺還在」),
    holds:驗收裡還擋著這張的那幾條;
    fillable:整批填表會不會填這張;blocked:落在封鎖名單的哪一條(沒被封鎖是 None);back:已送出的卡給哪一顆退回(undo_sent = 沒送成、back = 退回、None = 都不給)。"""
    job = job or {'id': url}
    m = fb.get(url) if isinstance(fb.get(url), dict) else {}
    out = {'confirm': fr.confirm_problem(fb, url, status), 'busy': busy(m), 'auto': auto, 'stop': stop,
           'ask': ask(fb, url, ctx), 'send': fr.approval_problem(fb, url, status),
           'gate': fr.ship_blocked(fb, job, status), 'closed': fr.judged_closed(fb, job, status),
           'holds': fr.holding(fb, job, status),
           'fillable': ds.allowed(m, 'fill_start'), 'blocked': ds.company_block(fb, job),
           'back': 'undo_sent' if ds.allowed(m, 'undo_sent') else 'back' if ds.allowed(m, 'back') else None}
    out['view'] = view(fb, url, status, out)
    return out


GONE_SHORT = '填好的那一頁不見了,要重填'


def _month_day(d):
    """日期寫成 月/日(看板一律這樣寫)。"""
    m = re.match(r'^\d{4}-(\d\d)-(\d\d)', str(d or ''))
    return '%d/%d' % (int(m.group(1)), int(m.group(2))) if m else str(d or '')


def view(fb, url, status, nx):
    """卡上幫你填表那一塊(看板照畫,自己不比投遞狀態名,#343):
    line:那一行的字 [[樣子, 字]](樣子:run、bad、ok、okb、idle);shots:看頁面、截圖的連結 [[種類, 值, 字]];
    why:擋住的原因;buttons:按鈕 [{b, label, main, adv, off}](off = 停用的原因);todo:要你處理的那一句;
    fill:🚀 填表進度放哪一格 {kind, text}(已送出的沒有);busy、stage:agent 正在做(狀態表的 working)哪一段;
    up:叫得回那隻 agent 在原頁改;eye:給「👀 看現在的頁面」;rf:幾欄等著照新答案重打;
    soon:確認了、可以送(看板剛按確認的那幾秒改寫成「幾秒後開始送出」);review:填好了停著等你看。"""
    m = fb.get(url) if isinstance(fb.get(url), dict) else {}
    a = m.get('apply') or {}
    f = m.get('form')
    s = ds.state(m)
    if s == 'sent' or (f and f.get('lock')):
        return {'locked': True}
    agent = cf.AGENT
    prob = nx['send']
    done, ok = s != 'todo', s in ds.FILLED
    rf = sum(1 for x in (f or {}).get('f') or [] if x.get('refill'))
    issue = (a.get('issues') or [None])[0]
    auto_do = (nx['auto'] or {}).get('do') or ''
    n_ask = len(nx['ask'])
    working = s in ds.WORKING                        # agent 正在做(送出結果不明是等你查,下面另外畫)
    stage = 'submit' if s == 'sending' else (a.get('stage') or 'fill')
    up = ds.page_up(m) and bool(a.get('session'))
    stuck = (issue or GONE_SHORT) if s == 'gone' else f'{agent} 卡住:{issue or "原因不明"}'
    line, why, todo, auto = [], '', '', False
    autoline = ['run', '⏳ ' + nx['auto']['line']] if auto_do else None
    if working:
        line.append(['run', '⏳ {} {}…'.format(agent, {'submit': '正在送出', 'fix': '正在改'}.get(stage, '正在填'))])
    elif s == 'unsure':
        line.append(['bad', f'📤 {agent} {_month_day((a.get("submit_fail") or {}).get("at") or a.get("at"))} 按了送出,沒看到已收到申請頁'
                     + ('' if a.get('tab_id') else ';那一頁已經不在了')])
    elif done:
        line.append(['ok', f'🤖 {agent} {_month_day(a.get("at"))} {"改好了" if a.get("stage") == "fix" else "填好了"},停在送出前'] if ok
                    else ['bad', '📄 ' + (issue or GONE_SHORT)] if s == 'gone'
                    else ['bad', f'🤖 {agent} {"修改" if a.get("stage") == "fix" else "填表"}卡住:{issue or "原因不明"}'])
    else:
        line.append(['idle', f'🤖 {agent} 還沒填這張'])
    eye = up and bool(f) and not working
    shots = ([['live', '', '👀 看現在的頁面']] if eye else [['ev', a['ev'], '填表時的截圖']] if a.get('ev')
             else [['shot', '', '填表時的截圖']] if a.get('shot') else [])
    if s == 'unsure' and (a.get('submit_fail') or {}).get('ev'):
        shots.append(['ev', a['submit_fail']['ev'], '送出時的截圖'])
    if done and not ok and url.startswith(('http://', 'https://')) and '真人驗證' in ' '.join(a.get('issues') or []):
        shots.append(['human', url, '🌐 在我的瀏覽器打開'])   # 網站要真人驗證:agent 不替他按,給他在自己瀏覽器投

    def fill(label, adv=True):
        return {'b': 'fill', 'label': label, 'main': True, 'adv': adv} if ds.allowed(m, 'fill_start') else None

    def fix(main):
        return {'b': 'fix', 'label': f'✏️ 要 {agent} 改' + (f'({rf} 欄照新答案重打)' if rf else ''), 'main': main, 'adv': main}
    can_fix = up and ds.allowed(m, 'fix_start')
    main, side = [], []
    if working:
        pass
    elif s == 'unsure':
        sf = a.get('submit_fail') or {}
        why = ('❌ 送出沒確認成功' + (f'({agent} 按過送出,可能其實送出去了)' if sf.get('clicked') else '') + ':'
               + ((sf.get('problems') or [None])[0] or '沒看到成功頁面'))
        main = [{'b': 'clear', 'label': '確認沒送出,可以重送', 'main': True, 'adv': False}]
        if ds.allowed(m, 'actually_sent'):
            main.append({'b': 'actsent', 'label': '其實送出了', 'main': False, 'adv': False})
        todo = '送出沒確認成功,先去確認到底送出沒有'
    elif not done and auto_do == 'fill':
        line.append(autoline)
        main, auto = [fill('▶ 現在就填', adv=False)], True
    elif not f:
        if done and ok:
            why = ds.NO_FORM
        main = [fill(f'▶ 讓 {agent} 重填這張' if done else f'▶ 讓 {agent} 填這張')]
        if done and not ok:
            todo = f'{agent} 卡住:{issue or "原因不明"}'
    elif s == 'confirmed' and not prob:
        line = [['okb', '✅ 你已確認,等送出']]
        main = [{'b': 'submit', 'label': '▶ 送出', 'main': True, 'adv': True}]
        todo = '你確認了,按「▶ 送出」'
        side.append({'b': 'unapprove', 'label': '取消確認', 'main': False, 'adv': False})
    else:
        if s == 'confirmed':        # 確認了、之後驗收沒過或網頁要重打:先取消確認才能改
            why, todo = '⚠ 確認失效:' + prob, '確認失效(' + prob + '),先取消確認'
            side.append({'b': 'unapprove', 'label': '取消確認', 'main': False, 'adv': False})
        if n_ask:
            main = []           # 答案要他確認:表單那一行已經有「去常用答案看」,這裡不重複
            todo = stuck if done and not ok else f'{n_ask} 條答案等你確認'
        elif auto_do == 'fix':
            line.append(autoline)
            auto = True
        elif nx['stop']:        # 自動流程本來會接、這次不接:要你處理,照實講原因
            why = '⚠ ' + nx['stop']
            main = [fix(True) if can_fix else fill(f'▶ 讓 {agent} 重填這張')]
            todo = nx['stop']
        elif rf and (can_fix or ds.allowed(m, 'fill_start')):
            main = [fix(True) if can_fix else fill(f'▶ 讓 {agent} 照新答案重填')]
            todo = (stuck if done and not ok else
                    f'答案改過,按「✏️ 要 {agent} 改」' if can_fix else f'答案改過,按「▶ 讓 {agent} 照新答案重填」')
        elif s == 'stale' and auto_do == 'fill':
            line.append(autoline)
            auto = True
        else:
            confirm_why = nx['confirm']
            if not done:
                main = [fill(f'▶ 讓 {agent} 填這張')]
            if confirm_why and not why and s != 'gone':     # 頁面不見了:上面那行已經講了原因和下一步
                why = '⛔ 還不能確認送出:' + confirm_why
            if s != 'confirmed':
                side.insert(0, {'b': 'approve', 'label': '✅ 確認送出', 'main': done and not confirm_why, 'adv': done and not confirm_why,
                                'off': confirm_why})
            if done and not confirm_why and s != 'confirmed':
                main = [side.pop(0)]
            elif done and (s in ds.AUTO_FILL or not ok and not up):     # 頁不見了、上傳的是舊檔:要重填
                if s == 'gone' and auto_do == 'fill':
                    line.append(autoline)
                    auto = True
                else:
                    main = [fill(f'▶ 讓 {agent} 重填這張')]
            elif confirm_why and can_fix:
                main = [fix(True)]      # 被擋住又叫得回那隻 agent:下一步就是要它改
            if not auto and done and s != 'confirmed':
                todo = stuck if not ok else ('還不能確認送出:' + confirm_why if confirm_why else '填好了,看過頁面就能確認送出')
        if can_fix and auto_do != 'fix' and not any(b and b['b'] == 'fix' for b in main):
            side.append(fix(False))     # 會自動重打的不給,不叫他按
    if auto_do and not auto and not working and s != 'unsure':     # agent 會自己接手的,卡上一定寫出來
        line.append(autoline)
        if not n_ask:
            todo = ''           # agent 會做的不算要你處理(答案等你的照舊)
    if not why and not working and nx['gate']:
        why = '⛔ 還不能確認送出:' + nx['gate']
    wait_why = prob if s == 'confirmed' else nx['confirm']
    fill_cell = ({'kind': 'run'} if working
                 else {'kind': 'unsure', 'text': '❓ 送出結果不明,先去信箱或平台的應徵紀錄查'} if s == 'unsure'
                 else ({'kind': 'wait', 'text': '⚠ ' + str(wait_why)[:60]} if wait_why
                       else {'kind': 'ok', 'text': '✅ 你已確認,等送出' if s == 'confirmed' else '✅ 填好了,等你確認送出'}) if done and ok
                 else {'kind': 'gone', 'text': '📄 ' + GONE_SHORT} if s == 'gone'
                 else {'kind': 'bad', 'text': '❌ ' + str(issue or '沒填成')[:60]} if done
                 else {'kind': 'todo'})
    return {'locked': False, 'busy': working, 'stage': stage, 'line': line, 'shots': shots, 'why': why,
            'buttons': [b for b in main + side if b], 'todo': todo, 'fill': fill_cell, 'up': up, 'eye': eye, 'rf': rf,
            'soon': s == 'confirmed' and not prob, 'review': s == 'parked' and bool(a.get('session'))}


SWAPPING = ('files_changed',)                          # 換檔(換履歷、語言、自己的檔、客製版)
LEAVING = ('leave', 'back', 'sent_manual')            # 離開流程(移除、出錯了、👎、退回、標外部送出)


def refuse(fb, url, event, status=None):
    """後台收到這張卡的任何事件:照下一步收或擋。不收回原因,收就回 None。
    原因跟卡上寫的同一句:確認送出照 confirm;忙的卡換檔、離開流程照 busy;其他照狀態表(fire 擋的也是這一句)。"""
    m = fb.get(url) if isinstance(fb.get(url), dict) else {}
    if event == 'confirm':
        return fr.confirm_problem(fb, url, status)
    if (event in SWAPPING and ds.state(m) not in ds.SWAP) or (event in LEAVING and ds.state(m) not in ds.LEAVE):
        return busy(m)
    return ds.why_not(m, event)


def refuse_save(cur, new):
    """看板存檔送來這張卡(沒帶事件也一樣):不能換檔的卡不准跟著存檔換履歷、語言、自己的檔;
    不能離開流程的卡不准移除、換階段、👎 😐、出錯了、整張清掉。不收回原因(busy 那一句),收就回 None。其他欄(筆記、👍)照收。"""
    cur = cur if isinstance(cur, dict) else {}
    s = ds.state(cur)
    if s not in ds.SWAP and isinstance(new, dict) and any(cur.get(k) != new.get(k) for k in ds.SWAP_FIELDS):
        return busy(cur)
    if s not in ds.LEAVE and (not isinstance(new, dict) or cur.get('app') != new.get('app')
                              or bool(cur.get('rm')) != bool(new.get('rm'))
                              or (new.get('s') in ('dislike', 'meh', 'techerr') and new.get('s') != cur.get('s'))):
        return busy(cur)
    return None


def of(fb, jobs, status=None, *, flow=None, now=None, real=True, gen=0, building=False):
    """看板上每張卡的下一步。fb:看板標記;jobs:看板上的卡;status:投遞前驗收的結果;
    flow:自動流程設定(沒給 = 都關著);now:現在幾點;real:是不是真的看板(副本等得短、不等建置);
    gen、building:建置(含投遞前驗收)跑完幾輪、現在有沒有在跑(推進可以投了要等它)。"""
    flow = flow or {}
    jobs = [j for j in jobs or [] if isinstance(j, dict) and j.get('id')]
    a = fb.get('__auto__') if isinstance(fb.get('__auto__'), dict) else {}
    tried = set(a.get('tried') or [])
    rf = a.get('rf') if isinstance(a.get('rf'), dict) else {}
    now = now or datetime.datetime.now()
    cap = fill_cap(flow)
    full = bool(cap) and sum(1 for j in jobs if ds.held(fb, j['id'], j)) >= cap
    ctx = _ask_ctx(fb)
    build = {'gen': gen, 'running': building, 'real': real}
    out = {}
    for j in jobs:
        auto, stop = _fix(fb, j, flow, tried, rf, now, real, ctx)
        out[j['id']] = card(fb, j['id'], status, auto or _fill(fb, j, flow, tried, full) or _move(fb, j, flow, tried, status, build),
                            stop, ctx, j)
    return out
