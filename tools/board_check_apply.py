# -*- coding: utf-8 -*-
"""
board_check 的「代投狀態」那幾條(由 board_check import,規矩寫法一樣:會失敗的檢查)。

守的規矩:卡上、「這一頁要你處理的」、「🚀 填表進度」寫的狀態和下一步,跟看板資料真的狀態一致;
按鈕按下去,資料真的跟著變,復原也真的還原。
種資料用 PRE:直接改副本看板檔(board_doc.set_fb),再叫頁面跟上。挑示範看板最後面幾張,
這幾條排在全部檢查的最後,不影響別條。
"""
import copy

import board_doc as bd
import shot

APPLY_PRE = {}
APPLY_CHECKS = []
CHECK_RESUME = 'board-check'   # 副本唯一那份履歷的 id:要寫客製紀錄的檢查照它寫,才是卡上真的會寄的那份

# 開啟「可以投了」、展開每一家、等某張卡畫出來
OPEN_SHIP = r"""
  async function openShip(id){
    await T.resync();
    document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
    return await cardOf(id);
  }
  // 按了東西頁面會重畫、公司又收起來:每次要看卡之前先把每一家展開
  async function cardOf(id){
    [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
    return await T.until(function(){return T.card(id);},5000);
  }
"""


def _seed(board, cards, extra=None):
    """cards:依序放到示範看板最後面幾張職缺上的標記(整筆換掉)。回 {'ids': [...]}。"""
    jobs = bd.load(board)['data']['jobs']
    ids = [j['id'] for j in jobs[::-1] if str(j['id']).startswith('http')][:len(cards)]

    def mut(fb):
        for i, c in zip(ids, cards):
            fb[i] = copy.deepcopy(c)
        if extra:
            extra(fb, ids)
    bd.set_fb(mut, live=board, by='board_check')
    return {'ids': ids}


def external_sent_case(board):
    """一張 agent 填好、頁面還在的卡,他改了答案(那一欄標著雇主網頁待重打),然後決定自己在外部投。"""
    def ans(fb, ids):
        bank = [e for e in fb.get('__ans__') or [] if e.get('k') != 'bc_ext']
        bank.append({'k': 'bc_ext', 'q': '外部送出檢查用', 'v': 'Yes', 'zh': '是', 'at': '2026-01-01'})
        fb['__ans__'] = bank
        box = [x for x in fb.get('__inbox__') or [] if x.get('id') != 'bc_ext']
        box.append({'id': 'bc_ext', 'at': '2026-01-01T00:00:00', 'from': '代投', 'msg': '填表沒完成:外部送出那張',
                    'need': '看卡上的原因', 'job': ids[0], 'n': 1})
        fb['__inbox__'] = box
    return _seed(board, [{
        'app': 'ship',
        'form': {'plat': '測試', 'at': '2026-01-01', 'f': [{'q': 'Ext?', 'src': 'bank', 'k': 'bc_ext', 'refill': 1}]},
        'ds': 'parked',
        'apply': {'stage': 'fill', 'issues': [], 'session': 'bc-ext', 'tab_id': '5',
                  'at': '2026-01-01T00:00:00', 'delivery': {'method': 'direct_upload'}},
    }], ans)


APPLY_PRE['external_sent_case'] = external_sent_case
APPLY_CHECKS.append((
    '代投:按「📮 我已在外部送出」鎖表單時一起清掉「雇主網頁待重打」(不然之後每一張都記不進表單);復原放回來',
    OPEN_SHIP + r"""
      var id=P.ids[0], bad=[], c=await openShip(id); if(!c)return '找不到那張可投遞卡';
      var b=c.querySelector('[data-adv="sent"]'); if(!b)return '卡上沒有「📮 我已在外部送出」';
      b.click(); await T.idle();
      var m=(await T.state())[id]||{}, f=m.form||{};
      if(!f.lock)bad.push('表單沒鎖');
      if((f.f||[]).some(function(x){return x.refill;}))bad.push('鎖住的表單還標著雇主網頁待重打');
      var ib=function(st){return (st.__inbox__||[]).filter(function(x){return x.id==='bc_ext';})[0]||{};};
      if(!ib(await T.state()).done)bad.push('#11 他標了已在外部送出,這張之前的代投回報還算「要你處理」(卡上已經看不到原因)');
      var u=document.querySelector('#snack .snack-undo'); if(!u)bad.push('沒有復原');
      else{u.click(); await T.idle(); var st=await T.state(); m=st[id]||{}; f=m.form||{};
        if(m.app!=='ship'||f.lock)bad.push('復原後沒有回到可以投了');
        if(!(f.f||[]).some(function(x){return x.refill;}))bad.push('復原後「雇主網頁待重打」沒放回來');
        if(ib(st).done)bad.push('復原後那則回報沒回到還沒處理');}
      return bad.join('；');
    """,
    'external_sent_case',
))


def _filled(state='parked', **apply):
    """agent 填過的一張(投遞狀態 state,預設停著等你:頁面還在、答案都確認過,沒有答案欄,不會卡在等他確認)。"""
    a = {'stage': 'fill', 'issues': [], 'session': 'bc-s', 'tab_id': '5',
         'at': '2026-01-01T00:00:00', 'delivery': {'method': 'direct_upload'}}
    a.update(apply)
    return {'app': 'ship', 'ds': state, 'form': {'plat': '測試', 'at': '2026-01-01', 'f': []}, 'apply': a}


def next_step_case(board):
    """可投遞裡幾張狀態各不同的卡,看卡上、「這一頁要你處理的」、「🚀 填表進度」、📣 回報講的一不一致。"""
    from agent_chrome import GONE
    cards = [
        _filled('nopage', tab_id='', issues=['agent 的 Chrome 沒連上']),           # 0 沒填成、頁面也不在
        _filled('stale', stale='履歷換過了,網頁上傳的還是舊的,先讓 agent 重填'),     # 1 上傳的是舊檔
        _filled('gone', tab_id='', issues=[GONE], checked_by='agent'),             # 2 頁面不見了(舊資料還有 checked_by)
        {'app': 'ship', 'ds': 'nopage', 'apply': {'stage': 'fill', 'at': '2026-01-01T00:00:00',
                                                  'issues': ['agent 的 Chrome 沒連上']}},   # 3 第一次填就沒成,還沒有表單紀錄
        _filled(),                                                                  # 4 答案改過、網頁待重打
        dict(_filled('unsure', submit_fail={'at': '2026-01-01T00:00:00', 'problems': ['沒看到成功頁面'], 'clicked': True}),
             approve={'at': '2026-01-01T00:00:00', 'snap': {}}),                    # 5 送出結果不明
        _filled(),                                                                  # 6 真的填好了
        dict(_filled('nopage', tab_id=''), rm=1),                                   # 7 已經移除的
    ]

    def extra(fb, ids):
        bank = [e for e in fb.get('__ans__') or [] if e.get('k') != 'bc_step']
        bank.append({'k': 'bc_step', 'q': '下一步檢查用', 'v': 'Yes', 'zh': '是', 'at': '2026-01-01'})
        fb['__ans__'] = bank
        fb[ids[4]]['form']['f'] = [{'q': 'Step?', 'src': 'bank', 'k': 'bc_step', 'refill': 1}]
        box = [x for x in fb.get('__inbox__') or [] if not str(x.get('id', '')).startswith('bc_')]
        box += [{'id': 'bc_rm', 'at': '2026-01-01T00:00:00', 'from': '代投', 'msg': '填表沒完成:移除的那張',
                 'need': '看卡上的原因', 'job': ids[7], 'n': 1},
                {'id': 'bc_open', 'at': '2026-01-01T00:00:00', 'from': '代投', 'msg': '填表沒完成:還在的那張',
                 'need': '檢查用要你做的事', 'job': ids[0], 'n': 1}]
        fb['__inbox__'] = box
    return _seed(board, cards, extra)


APPLY_PRE['next_step_case'] = next_step_case
APPLY_CHECKS.append((
    '代投:卡上、「這一頁要你處理的」、「🚀 填表進度」、📣 回報講的狀態和下一步,跟卡片真的狀態一致',
    OPEN_SHIP + r"""
      var I=P.ids, bad=[]; if(!await openShip(I[6]))return '找不到那幾張可投遞卡';
      function card(i){return T.card(I[i]);}
      function refill(i){var m=card(i)&&card(i).querySelector('.ap-main'); return !!m&&m.getAttribute('data-runone')==='apply|'+I[i];}
      if(!refill(0))bad.push('#9 填表沒成、頁面也不在的卡,主按鈕不是「重填這張」');
      if(!refill(1))bad.push('#10 換過履歷的卡,主按鈕不是重填(卡上說要重填)');
      var c2=card(2)||{textContent:''};
      if(/填表卡住/.test(c2.textContent))bad.push('#17 頁面不見了的卡寫成「填表卡住」');
      if(/頁面在 agent 的 Chrome 裡/.test(c2.textContent))bad.push('#16 頁面不見了,卡上還寫「頁面在 agent 的 Chrome 裡」');
      if(!refill(2))bad.push('頁面不見了的卡(自動關著)主按鈕不是重填');
      var todo=document.querySelector('#app .todo');
      if(!(todo&&todo.querySelector('[data-todogo="'+CSS.escape(I[3])+'"]')))bad.push('#8 第一次填就沒成(還沒有表單紀錄)的卡沒列進「這一頁要你處理的」');
      var fl=document.getElementById('filllistbar');
      function row(i){var b=fl&&fl.querySelector('[data-fillgo="'+CSS.escape(I[i])+'"]'); return b&&b.closest('.fl-row');}
      [[1,'換過履歷'],[4,'答案改過要重打'],[5,'送出沒確認成功']].forEach(function(x){var r=row(x[0]);
        if(!r)bad.push('#5 🚀 沒列「'+x[1]+'」那張'); else if(/填好了/.test(r.textContent))bad.push('#5 🚀 把「'+x[1]+'」的卡寫成填好了');});
      var r6=row(6); if(!r6||!/等你確認送出/.test(r6.textContent))bad.push('#5 真的填好的那張,🚀 沒寫「等你確認送出」('+(r6?r6.textContent:'沒列')+')');
      var r2=row(2); if(!r2||/fl-bad/.test(r2.className)||/沒填成/.test(r2.textContent)||!/不見了/.test(r2.textContent))bad.push('#17 🚀 把頁面不見了的卡算成「沒填成」('+(r2?r2.textContent:'沒列')+')');
      var ib=document.getElementById('inboxbar');
      if(ib&&ib.querySelector('[data-ib="bc_rm"]'))bad.push('#11 已經移除的卡,舊回報還列在「要你處理」');
      var ro=ib&&ib.querySelector('[data-ib="bc_open"]');
      if(!ro)bad.push('還在的那張的回報沒列出來');
      else{var n=ro.textContent.split('檢查用要你做的事').length-1; if(n!==1)bad.push('#18 📣 回報展開後「你要做的」出現 '+n+' 次');}
      return bad.join('；');
    """,
    'next_step_case',
))


FLOW_OFF_JS = r"""
      var cur=await fetch('/api/settings').then(function(r){return r.json();}), se=cur.settings||{};
      se.flow={like_to_prep:false,auto_prep:false,auto_advance:false,auto_fill:false,replies_at:''};
      await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:se})});
"""


def fill_limit_case(board):
    """只開自動填表、上限 1 張:一張填好停著等他(已經滿了)、一張頁面不見了、一張新卡;
    舊卡(開啟當下就在的)兩張:一張還沒填過(按鈕要算它)、一張頁面不見了(按鈕不算它)。"""
    from agent_chrome import GONE
    gone = _filled('gone', tab_id='', issues=[GONE])
    cards = [_filled(), gone, {'app': 'ship'}, {'app': 'ship'}, dict(gone)]

    def extra(fb, ids):
        # 開關先開好再種:開的那一刻伺服器會把當下在可投遞的卡記成舊卡;種完記號跟開關一致,之後不會再動
        fb['__auto__'] = {'since': '2026-01-01T00:00:00', 'skip': [ids[3], ids[4]], 'tried': [], 'seen': {},
                          'flow': {'auto_prep': False, 'auto_advance': False, 'auto_fill': True}}
    shot.flow(auto_fill=True, fill_max=1)
    return _seed(board, cards, extra)


APPLY_PRE['fill_limit_case'] = fill_limit_case
APPLY_CHECKS.append((
    '代投:自動填表停著的頁到上限時,卡上不寫「會自動填、會自動重填」而是給按鈕;「之前的 N 張也交給自動」寫幾張就交幾張',
    OPEN_SHIP + r"""
      var I=P.ids, bad=[];
      try{
        if(!await openShip(I[1]))return '找不到那幾張可投遞卡';
        var c1=await cardOf(I[1]), c2=await cardOf(I[2]);
        if(/會自動重填/.test(c1.textContent))bad.push('A2 停著等他看的頁已經到上限,頁面不見了的卡還寫「會自動重填」(自動流程不會排)');
        var m1=c1.querySelector('.ap-main'); if(!m1||m1.getAttribute('data-runone')!=='apply|'+I[1])bad.push('A2 到上限時,頁面不見了的卡沒有重填鈕');
        if(/排隊中/.test(c2.textContent))bad.push('A2 到上限時,新卡還寫「排隊中:會自動填這張」');
        var b=document.querySelector('#app [data-autotake="ship"]');
        if(!b)bad.push('#7 沒有「之前的 N 張也交給自動」');
        else{var n=+((b.textContent.match(/(\d+) 張/)||[])[1]);
          if(n!==1)bad.push('#7 按鈕寫 '+n+' 張(應該 1 張)');
          b.click(); await T.idle();
          var got=+((T.snack().match(/(\d+) 張/)||[])[1]);
          if(got!==n)bad.push('#7 按鈕寫 '+n+' 張,按了說交了 '+got+' 張');
          var A=(await T.state()).__auto__||{};
          if((A.skip||[]).indexOf(I[4])<0)bad.push('#7 按鈕沒算的那張(頁面不見了的舊卡)也被交給自動重填');
          var un=document.querySelector('#snack .snack-undo'); if(un){un.click(); await T.idle();}}
      }finally{""" + FLOW_OFF_JS + r"""}
      return bad.join('；');
    """,
    'fill_limit_case', {'fresh_page': True},
))


def closed_tab_case(board):
    """按了確認送出、8 秒內關掉看板分頁:確認存了,計時器跟著分頁消失(額外抓到 7)。種一張已經確認、存好的卡。"""
    shot.flow()
    return _seed(board, [dict(_filled('confirmed'), approve={'at': '2026-01-01T00:00:00', 'snap': {}, 'round': '2026-01-01T00:00:00'}),
                         _filled()])


APPLY_PRE['closed_tab_case'] = closed_tab_case
APPLY_CHECKS.append((
    '幫你填表:確認送出後 8 秒內關掉分頁,停在你已確認、卡上給「▶ 送出」,不會自己送;切回來已超過 30 秒的計時器也不自己送',
    OPEN_SHIP + r"""
      var I=P.ids, bad=[]; if(!await openShip(I[0]))return '找不到那張可投遞卡';
      var c=await cardOf(I[0]);
      if(!c||!c.querySelector('[data-applysubmit]'))bad.push('#7 確認存了、分頁重開之後卡上沒有「▶ 送出」');
      await T.sleep(9000);
      var st=(await T.state())[I[0]]||{};
      if(st.ds!=='confirmed')bad.push('#7 重開之後過了 8 秒,卡不在你已確認(自己送了?):'+st.ds);
      // 修正 17:另一張按確認,計時器到的時候已經超過 30 秒(分頁在背景被拖慢):不自己送
      var c2=await cardOf(I[1]), ap=c2&&c2.querySelector('[data-approve]');
      if(!ap||ap.disabled)bad.push('第二張按不了確認送出');
      else{var realNow=Date.now; ap.click(); await T.idle();
        Date.now=function(){return realNow()+31000;};
        try{await T.sleep(9000);}finally{Date.now=realNow;}
        var st2=(await T.state())[I[1]]||{};
        if(st2.ds!=='confirmed')bad.push('#17 計時器切回來已超過 30 秒還是自己送了:'+st2.ds);
        var c3=await cardOf(I[1]); if(!c3||!c3.querySelector('[data-applysubmit]'))bad.push('#17 沒自己送,卻也沒給「▶ 送出」');}
      return bad.join('；');
    """,
    'closed_tab_case', {'fresh_page': True},
))


def busy_buttons_case(board):
    """一張 agent 正在填、一張送出結果不明(用到一條答案):離開流程、換履歷/語言的按鈕要停用、寫原因(修正 11、14)。"""
    shot.flow()
    running = _filled()     # 「正在填」要有一輪真的在跑:檢查裡把伺服器回的進度改成正在填這一張(沒在跑的會被伺服器收尾)
    unsure = dict(_filled('unsure', submit_fail={'at': '2026-01-01T00:00:00', 'problems': ['沒看到成功頁面'], 'clicked': True}),
                  approve={'at': '2026-01-01T00:00:00', 'snap': {}})
    unsure['form'] = {'plat': '測試', 'at': '2026-01-01', 'f': [{'q': 'Busy?', 'src': 'bank', 'k': 'bc_busy'}]}

    def ans(fb, ids):
        bank = [e for e in fb.get('__ans__') or [] if e.get('k') != 'bc_busy']
        bank.append({'k': 'bc_busy', 'q': '送出結果不明那張在用', 'v': 'Yes', 'zh': '是', 'at': '2026-01-01'})
        fb['__ans__'] = bank
    # 換檔(上傳客製版、改回原始檔)也要停用(#338:正在送出時換了檔,送出去的是頁上的舊檔)。
    # 正在送出種不住(沒有真的一輪在送,伺服器會收尾成送出結果不明),後台那一邊由單元測試釘住
    unsure['custom_file'] = 'custom/bc-busy.pdf'
    return _seed(board, [running, unsure], ans)


APPLY_PRE['busy_buttons_case'] = busy_buttons_case
APPLY_CHECKS.append((
    '幫你填表:agent 正在做、送出結果不明時,退回/移除/出錯了/👎/外部送出/換履歷語言/客製或上傳自己的檔/改回原始檔的按鈕停用並寫原因;送出結果不明那張在用的答案先不給改',
    OPEN_SHIP + r"""
      var I=P.ids, bad=[], of=window.fetch;
      window.fetch=function(u){if(/\/api\/rev/.test(String(u)))return of.apply(this,arguments).then(function(r){return r.json();}).then(function(v){
          v.apply={running:true,url:I[0],stage:'fill',t0:Date.now()/1000}; return new Response(JSON.stringify(v),{status:200,headers:{'Content-Type':'application/json'}});});
        return of.apply(this,arguments);};
      try{
      if(!await openShip(I[0]))return '找不到那張可投遞卡';
      T.sync(); await T.sleep(900); await cardOf(I[0]);
      var SEL='[data-back],[data-rm="1"],[data-err],.fb-b[data-s="dislike"],.fb-b[data-s="meh"],[data-adv="sent"],.vd-b[data-vd],.vd-b.lg,'+
        '[data-cust-open],[data-cust-action="accept"],[data-cust-action="clear"]:not([data-cust-orphan])';
      [[I[0],/正在做/,'#11 agent 正在做'],[I[1],/先確認到底送出沒有/,'#14 送出結果不明']].forEach(function(x){
        var c=T.card(x[0]); if(!c){bad.push(x[2]+':找不到卡'); return;}
        if(!c.querySelector('[data-cust-open]'))bad.push(x[2]+':卡上沒有「要客製 / 上傳自己的客製版」');
        [].slice.call(c.querySelectorAll(SEL)).forEach(function(b){
          if(!b.disabled)bad.push(x[2]+':「'+b.textContent.trim()+'」還按得下去');
          else if(!x[1].test(b.title||''))bad.push(x[2]+':「'+b.textContent.trim()+'」停用了卻沒寫原因('+(b.title||'')+')');});});
      var st0=JSON.stringify((await T.state())[I[1]]);
      var r=T.card(I[1]).querySelector('[data-rm="1"]'); if(r){r.disabled=false; r.click(); await T.idle();}   // 舊按鈕硬按也不動
      if(JSON.stringify((await T.state())[I[1]])!==st0)bad.push('#14 送出結果不明的卡硬按移除,卡還是被改了');
      var ad=document.querySelector('#app .ans-d'); if(ad&&!ad.open){ad.querySelector('summary').click(); await T.sleep(200);}
      var row=document.querySelector('#app .ansrow[data-k="bc_busy"]');
      if(row){if(!row.open){row.querySelector('summary').click(); await T.sleep(200);}
        var box=row.querySelector('[data-ansf="v"],[data-ansf="zh"]');
        if(!box||!box.readOnly)bad.push('#14 送出結果不明那張在用的答案還能改');}
      else bad.push('找不到那一條答案');
      }finally{window.fetch=of; T.sync(); await T.idle();}
      return bad.join('；');
    """,
    'busy_buttons_case', {'fresh_page': True},
))


def approve_window_case(board):
    """一張填好、可以確認送出的卡;一張已經確認過、還停在可以投了的卡。"""
    shot.flow()
    return _seed(board, [_filled(), dict(_filled('confirmed'), approve={'at': '2026-01-01T00:00:00', 'snap': {}})])


APPLY_PRE['approve_window_case'] = approve_window_case
APPLY_CHECKS.append((
    '代投:按了確認送出的 8 秒內(還能復原)卡上不給「▶ 送出」;從可以投了退回,之前的確認一起作廢(復原放回來)',
    OPEN_SHIP + r"""
      var I=P.ids, bad=[]; if(!await openShip(I[0]))return '找不到那張可投遞卡';
      var ap=T.card(I[0]).querySelector('[data-approve]'); if(!ap||ap.disabled)return '填好的那張按不了確認送出';
      ap.click(); await T.sleep(300);
      var c=await cardOf(I[0]);
      if(!c)bad.push('按了確認送出之後找不到那張卡');
      else if(c.querySelector('[data-applysubmit]'))bad.push('#13 確認後 8 秒內卡上就有「▶ 送出」(時間到會自己開始送,他照著按反而撞上)');
      var li=document.querySelector('#app .todo [data-todogo="'+CSS.escape(I[0])+'"]');
      if(li&&/▶ 送出/.test(li.closest('li').textContent))bad.push('#13 確認後 8 秒內「這一頁要你處理的」叫他按「▶ 送出」');
      var un=document.querySelector('#snack .snack-undo'); if(un){un.click(); await T.idle();}
      if(((await T.state())[I[0]]||{}).approve)bad.push('按了復原,確認還在');
      var y=await cardOf(I[1]), bk=y&&y.querySelector('[data-back="ready"]');
      if(!bk)bad.push('找不到「← 退回『待你決定』」');
      else{bk.click(); await T.idle();
        var m=(await T.state())[I[1]]||{};
        if(m.approve)bad.push('#15 從可以投了退回,之前的「確認送出」還留著(再推回來就直接是「▶ 送出」)');
        var u2=document.querySelector('#snack .snack-undo');
        if(u2){u2.click(); await T.idle(); m=(await T.state())[I[1]]||{};
          if(m.app!=='ship'||!m.approve)bad.push('退回後按復原,沒回到可以投了、確認也沒回來');}}
      return bad.join('；');
    """,
    'approve_window_case', {'fresh_page': True},
))


# 已送出三種來源、送出結果不明(#303):「沒送成」只給 agent 送出的,「退回」只給外部送出、平台對帳的,
# 有結果的照舊(沒錄取、沒下文給再投一次);送出結果不明的卡「確認沒送出」「其實送出了」並排
SENT_AT = '2026-01-02'
UNSURE_FAIL = {'at': '2026-01-01T00:00:00', 'problems': ['沒看到成功頁面'], 'clicked': True}


def _sent(by, **more):
    m = {'app': 'sent', 'ds': 'sent', 'sent_by': by, 'sent_at': SENT_AT,
         'form': {'plat': '測試', 'at': '2026-01-01', 'f': [], 'lock': 1}}
    if by == 'agent':
        m['apply'] = {'stage': 'fill', 'issues': [], 'session': 'bc-s', 'tab_id': '', 'at': '2026-01-01T00:00:00',
                      'delivery': {'method': 'direct_upload'},
                      'sent': {'at': SENT_AT + 'T00:05:00', 'text': '已收到申請(檢查用)'}}
    if by == 'platform':
        m['sent_rec'] = '104:bc:' + SENT_AT
    m.update(more)
    return m


def sent_sources_case(board):
    """0 agent 送出、1 外部送出、2 平台對帳(都還在等回音);3 agent 送出、沒錄取;4 平台對帳、沒下文;5 agent 送出、面試中;
    6 送出結果不明、頁還在;7 送出結果不明、頁不在了。"""
    shot.flow()
    # 挑得出履歷:按「其實送出了」時後台會補記寄出的是哪一份(sent_v),復原照樣要准。
    # 以前沒釘,記不記要看同一組前面跑過哪幾條有沒有在那張職缺留下寄出那份的紀錄:單跑這一條(--only)挑不出、不記;
    # CI 分 4 組的第 1 組(本機 --shard 0/4 一樣)記得出,復原就被擋
    unsure = dict(_filled('unsure', submit_fail=dict(UNSURE_FAIL)), approve={'at': '2026-01-01T00:00:00', 'snap': {}},
                  resume_id=CHECK_RESUME)
    cards = [_sent('agent'), _sent('manual'), _sent('platform'),
             _sent('agent', oc='rej', oc_at={'rej': '2026-01-05'}), _sent('platform', oc='ghost', oc_at={'ghost': '2026-01-20'}),
             _sent('agent', oc='iv', oc_at={'iv': '2026-01-06'}),
             unsure, copy.deepcopy(unsure)]
    cards[7]['apply']['tab_id'] = ''
    r = _seed(board, cards)
    jobs = {j['id']: j for j in bd.load(board)['data']['jobs']}
    return dict(r, jobs=[jobs[i] for i in r['ids']])


# 切到某一頁、展開每一家;每張卡在哪一頁看它自己的 app;按鈕用 window.confirm 問的那一句收下來
SENT_JS = OPEN_SHIP + r"""
  async function openTab(tab,id){await T.resync(); document.querySelector('[data-tab="'+tab+'"]').click(); await T.sleep(300);
    return await cardOf(id);}
  function canon(v){if(Array.isArray(v))return v.map(canon);
    if(v&&typeof v==='object'){var o={}; Object.keys(v).sort().forEach(function(k){o[k]=canon(v[k]);}); return o;} return v;}
  // 比的是存檔會留下的樣子(看板存檔把空陣列、空物件拿掉,跟後台 _lean 同一個)
  function same(a,b){var L=window.__jobsalvoSharedRules.lean; return JSON.stringify(canon(L(a)))===JSON.stringify(canon(L(b)));}
  // 哪幾欄不一樣(失敗時講出來)
  function diff(a,b){a=a||{}; b=b||{}; return Object.keys(Object.assign({},a,b)).filter(function(k){return !same(a[k],b[k]);}).map(function(k){
    return k+':'+String(JSON.stringify(canon(a[k]))).slice(0,80)+' → '+String(JSON.stringify(canon(b[k]))).slice(0,80);}).join(',');}   // 少一欄時 stringify 回 undefined
  var ASKED=[], ANSWER=true, _confirm=window.confirm;
  function stubConfirm(){window.confirm=function(m){ASKED.push(String(m)); return ANSWER;};}
  function unstubConfirm(){window.confirm=_confirm;}
"""


APPLY_PRE['sent_sources_case'] = sent_sources_case
APPLY_CHECKS.append((
    '幫你填表:「沒送成」只在 agent 送出的已送出卡(外部送出、平台對帳的照舊「退回」,有結果的照舊);先跳確認、取消不動;'
    '確定後回到還沒填、證據收進投遞歷史、卡上多一行「第 2 次投遞」、自動流程會排;復原整張放回',
    SENT_JS + r"""
      var I=P.ids, bad=[], R=window.__jobsalvoSharedRules;
      stubConfirm();
      try{
        if(!await openTab('sent',I[0]))return '已投出找不到 agent 送出的那張';
        function has(i,sel){var c=T.card(I[i]); return !!(c&&c.querySelector(sel));}
        [[0,'agent 送出、等回音',true,false,false],[1,'外部送出、等回音',false,true,false],[2,'平台對帳、等回音',false,true,false],
         [3,'agent 送出、沒錄取',false,false,true],[4,'平台對帳、沒下文',false,false,true],[5,'agent 送出、面試中',false,false,false]].forEach(function(x){
          if(!T.card(I[x[0]])){bad.push(x[1]+':已投出找不到這張'); return;}
          if(has(x[0],'[data-undosent]')!==x[2])bad.push(x[1]+(x[2]?':沒有「沒送成」':':不該有「沒送成」'));
          if(has(x[0],'[data-back]')!==x[3])bad.push(x[1]+(x[3]?':沒有「退回」':':不該有「退回」'));
          if(has(x[0],'[data-again]')!==x[4])bad.push(x[1]+(x[4]?':沒有「再投一次」':':不該有「再投一次」'));});
        var before=(await T.state())[I[0]], b=T.card(I[0]).querySelector('[data-undosent]');
        if(!b)return bad.join('；');
        ANSWER=false; b.click(); await T.idle();
        if(!ASKED.length)bad.push('按「沒送成」沒有先跳確認');
        else if(!/當時看到了已收到申請的頁面,確定沒送成/.test(ASKED[0]))bad.push('確認的那一句不對:'+ASKED[0]);
        if(!same((await T.state())[I[0]],before))bad.push('確認時按取消,卡還是被改了');
        ANSWER=true; b=(await openTab('sent',I[0])).querySelector('[data-undosent]'); b.click(); await T.idle();
        var st=await T.state(), m=st[I[0]]||{}, h=(m.history||[]).slice(-1)[0]||{};
        if(m.app!=='ship'||m.ds)bad.push('沒送成之後不是回到可以投了、還沒填:'+m.app+'/'+m.ds);
        ['sent_at','sent_by','apply'].forEach(function(k){if(k in m)bad.push('沒送成之後還留著 '+k);});
        if((m.form||{}).lock)bad.push('沒送成之後表單還鎖著(重填時記不進去)');
        if(h.event!=='undo_sent')bad.push('投遞歷史沒多一筆沒送成:'+JSON.stringify(h).slice(0,80));
        else if(!((h.apply||{}).sent||{}).text)bad.push('agent 當時的送出證據沒收進投遞歷史');
        var u=document.querySelector('#snack .snack-undo'); if(!u)bad.push('沒送成沒有復原');
        else{u.click(); await T.idle(); var back=(await T.state())[I[0]]; if(!same(back,before))bad.push('按了復原,卡沒回到按之前的樣子('+diff(before,back)+')');}
        // 再按一次(不復原),看它在可以投了長什麼樣子
        b=(await openTab('sent',I[0])).querySelector('[data-undosent]'); if(!b)return bad.concat('復原後已投出的卡上沒有「沒送成」').join('；');
        b.click(); await T.idle(); st=await T.state();
        var c=await openTab('ship',I[0]);
        if(!c)bad.push('沒送成之後「可以投了」找不到這張');
        else if(!/第 2 次投遞/.test(c.textContent)||!/沒送成/.test(c.textContent))bad.push('卡上沒有「第 2 次投遞 · …沒送成」那一行');
        // 自動填表開著時:卡上說會自動填(自動流程照投遞歷史算第幾輪,會再填一次;後台那一半在 tests/test_autopilot.py)
        st.__auto__={since:'2026-01-01T00:00:00',skip:[],tried:[],seen:{}};
        var v=R.cardViews(I[0],st,{job:P.jobs[0],status:{schema_version:2,checked_links:true,issues:[]},apply:{},flow:{auto_fill:true}});
        if(!/排隊中/.test((function(){var d=document.createElement('div'); d.innerHTML=v.line; return d.textContent;})()))
          bad.push('自動填表開著,沒送成的卡沒寫「排隊中:會自動填」(自動流程會填它)');
      }finally{unstubConfirm();}
      return bad.join('；');
    """,
    'sent_sources_case', {'fresh_page': True},
))
APPLY_CHECKS.append((
    '幫你填表:「其實送出了」只在送出結果不明的卡,跟「確認沒送出,可以重送」並排;按了到已送出、agent 當時的證據留著;復原整張放回',
    SENT_JS + r"""
      var I=P.ids, bad=[];
      if(!await openTab('ship',I[6]))return '可以投了找不到送出結果不明的那張';
      [6,7].forEach(function(i){var c=T.card(I[i]); if(!c){bad.push('找不到第 '+i+' 張'); return;}
        var a=c.querySelector('[data-actsent]'), k=c.querySelector('[data-applyclear]');
        if(!a)bad.push((i===6?'頁還在':'頁不在了')+'的送出結果不明卡沒有「其實送出了」');
        else if(!k||a.parentNode!==k.parentNode)bad.push('「其實送出了」沒跟「確認沒送出,可以重送」並排');});
      var others=[].slice.call(document.querySelectorAll('#app article[data-fid] [data-actsent]')).filter(function(b){
        var f=b.closest('article').getAttribute('data-fid'); return f!==I[6]&&f!==I[7];});
      if(others.length)bad.push(others.length+' 張不是送出結果不明的卡也有「其實送出了」');
      await openTab('sent',I[0]);
      if(document.querySelector('#app [data-actsent]'))bad.push('已投出的卡有「其實送出了」');
      var before=(await T.state())[I[6]], c=await openTab('ship',I[6]), b=c&&c.querySelector('[data-actsent]');
      if(!b)return bad.join('；');
      b.click(); await T.idle();
      var m=(await T.state())[I[6]]||{}, a=m.apply||{};
      if(m.app!=='sent'||m.ds!=='sent'||m.sent_by!=='agent')bad.push('按了沒到 agent 送出的已送出:'+[m.app,m.ds,m.sent_by].join('/'));
      if(!m.sent_at)bad.push('沒記投遞日');
      if(((a.sent||{}).problems||[])[0]!=='沒看到成功頁面'||!(a.sent||{}).clicked)bad.push('agent 當時的證據沒留著:'+JSON.stringify(a.sent));
      if(a.submit_fail)bad.push('送出結果不明的記號還在');
      if(m.approve)bad.push('確認送出還留著');
      if(T.card(I[6]))bad.push('按了還留在可以投了');
      var u=document.querySelector('#snack .snack-undo');
      if(!u)bad.push('其實送出了沒有復原');
      else{u.click(); await T.idle(); var back=(await T.state())[I[6]]; if(!same(back,before))bad.push('按了復原,卡沒回到按之前的樣子('+diff(before,back)+')');}
      // 再按一次(不復原):到已投出之後是 agent 送出的卡,給的是「沒送成」
      c=await openTab('ship',I[6]); b=c&&c.querySelector('[data-actsent]');
      if(!b)return bad.concat('復原後送出結果不明的卡沒有「其實送出了」').join('；');
      b.click(); await T.idle();
      var s=await openTab('sent',I[6]);
      if(!s)bad.push('已投出找不到這張');
      else if(!s.querySelector('[data-undosent]'))bad.push('其實送出了的卡(agent 送出)在已投出沒有「沒送成」');
      return bad.join('；');
    """,
    'sent_sources_case', {'fresh_page': True},
))
APPLY_CHECKS.append((
    '幫你填表:正在送出時「幫你填表」那一列沒有 ⏸ 暫停(凍在按下送出的半路他查不到送出去沒有);正在填表時有',
    r"""
      var of=window.fetch, fake=null, bad=[];
      window.fetch=function(u){var p=of.apply(this,arguments);
        if(fake&&String(u).indexOf('/api/rev')===0)return p.then(function(r){return r.json();}).then(function(v){
          v.apply=fake; return new Response(JSON.stringify(v),{status:200,headers:{'Content-Type':'application/json'}});});
        return p;};
      function bar(){return document.getElementById('applybar');}
      function has(sel){var b=bar(); return !!(b&&b.querySelector(sel));}
      try{
        document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
        var t0=Date.now()/1000-60;
        fake={phase:'run',running:true,stage:'submit',n:1,done:0,t0:t0,which:'測試卡'};
        if(!await T.until(function(){T.sync(); return has('[data-runctl="apply:stop"]');},6000))bad.push('正在送出:那一列沒有 ⏹ 停止');
        else if(has('[data-runctl="apply:pause"]'))bad.push('正在送出:那一列還有 ⏸ 暫停');
        fake={phase:'run',running:true,stage:'fill',n:1,done:0,t0:t0,which:'測試卡'};
        if(!await T.until(function(){T.sync(); return has('[data-runctl="apply:pause"]');},6000))bad.push('正在填表:那一列沒有 ⏸ 暫停');
      } finally {
        window.fetch=of; fake=null; T.sync(); await T.sleep(300);
      }
      return bad.join('；');
    """,
))
