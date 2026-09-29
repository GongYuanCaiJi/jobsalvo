# -*- coding: utf-8 -*-
"""
board_check 的「代投狀態」那幾條(由 board_check import,規矩寫法一樣:會失敗的檢查)。

守的規矩:卡上、「這一頁要你處理的」、「🚀 填表進度」寫的狀態和下一步,跟看板資料真的狀態一致;
按鈕按下去,資料真的跟著變,復原也真的還原。
種資料用 PRE:直接改副本看板檔(board_doc.set_fb),再叫頁面跟上。挑示範看板最後面幾張,
這幾條排在全部檢查的最後,不影響別條。
"""
import board_doc as bd

APPLY_PRE = {}
APPLY_CHECKS = []

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
    import json
    with open(board, encoding='utf-8') as f:
        jobs = bd.parse(f.read())['data']['jobs']
    ids = [j['id'] for j in jobs[::-1] if str(j['id']).startswith('http')][:len(cards)]

    def mut(fb):
        for i, c in zip(ids, cards):
            fb[i] = json.loads(json.dumps(c))
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
        'apply': {'stage': 'fill', 'ok': True, 'issues': [], 'session': 'bc-ext', 'tab_id': '5',
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


def _filled(**apply):
    """agent 填好、頁面還在、答案都確認過的樣子(沒有答案欄,不會卡在等他確認)。"""
    a = {'stage': 'fill', 'ok': True, 'issues': [], 'session': 'bc-s', 'tab_id': '5',
         'at': '2026-01-01T00:00:00', 'delivery': {'method': 'direct_upload'}}
    a.update(apply)
    return {'app': 'ship', 'form': {'plat': '測試', 'at': '2026-01-01', 'f': []}, 'apply': a}


def next_step_case(board):
    """可投遞裡幾張狀態各不同的卡,看卡上、「這一頁要你處理的」、「🚀 填表進度」、📣 回報講的一不一致。"""
    from agent_chrome import GONE
    cards = [
        _filled(ok=False, tab_id='', issues=['agent 的 Chrome 沒連上']),           # 0 填表沒成、頁面也不在
        _filled(stale='履歷換過了,網頁上傳的還是舊的,先讓 agent 重填'),              # 1 換過履歷
        _filled(ok=False, gone=True, tab_id='', issues=[GONE], checked_by='agent'),  # 2 頁面不見了(舊資料還有 checked_by)
        {'app': 'ship', 'apply': {'stage': 'fill', 'ok': False, 'at': '2026-01-01T00:00:00',
                                  'issues': ['agent 的 Chrome 沒連上']}},             # 3 第一次填就沒成,還沒有表單紀錄
        _filled(),                                                                    # 4 答案改過、網頁待重打
        _filled(submit_fail={'at': '2026-01-01T00:00:00', 'problems': ['沒看到成功頁面'], 'clicked': True}),  # 5 送出結果不明
        _filled(),                                                                    # 6 真的填好了
        dict(_filled(ok=False, tab_id=''), rm=1),                                     # 7 已經移除的
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


SB_URL = ['']   # 副本伺服器的網址:board_check 接上時換成它那一份(同一個 list,開好副本後填進去的看得到)
FLOW_OFF = {'like_to_prep': False, 'auto_prep': False, 'auto_advance': False, 'auto_fill': False, 'replies_at': ''}
FLOW_OFF_JS = r"""
      var cur=await fetch('/api/settings').then(function(r){return r.json();}), se=cur.settings||{};
      se.flow={like_to_prep:false,auto_prep:false,auto_advance:false,auto_fill:false,replies_at:''};
      await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:se})});
"""


def _flow(flow):
    """透過副本伺服器的設定 API 改自動流程(跟他在設定頁按儲存同一條路)。"""
    import shot
    shot.sandbox_flow(SB_URL[0], **flow)


def fill_limit_case(board):
    """只開自動填表、上限 1 張:一張填好停著等他(已經滿了)、一張頁面不見了、一張新卡;
    舊卡(開啟當下就在的)兩張:一張還沒填過(按鈕要算它)、一張頁面不見了(按鈕不算它)。"""
    from agent_chrome import GONE
    gone = _filled(ok=False, gone=True, tab_id='', issues=[GONE])
    cards = [_filled(), gone, {'app': 'ship'}, {'app': 'ship'}, dict(gone)]

    def extra(fb, ids):
        # 開關先開好再種:開的那一刻伺服器會把當下在可投遞的卡記成舊卡;種完記號跟開關一致,之後不會再動
        fb['__auto__'] = {'since': '2026-01-01T00:00:00', 'skip': [ids[3], ids[4]], 'tried': [], 'seen': {},
                          'flow': {'auto_prep': False, 'auto_advance': False, 'auto_fill': True}}
    _flow(dict(FLOW_OFF, auto_fill=True, fill_max=1))
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


def approve_window_case(board):
    """一張填好、可以確認送出的卡;一張已經確認過、還停在可以投了的卡。"""
    _flow(dict(FLOW_OFF))
    return _seed(board, [_filled(), dict(_filled(), approve={'at': '2026-01-01T00:00:00', 'snap': {}})])


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
