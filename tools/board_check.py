#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_check —— 看板的介面規矩,由程式擋,不靠誰記得。

為什麼要這支:排除清單、抓網頁鐵律、連結驗證那些早就寫死在程式裡,換誰接手都不會壞;
但「介面怎麼行為」這一整套(面板預設關、破壞性動作在哪、選單往哪開、卡片不重複講同一件事)
如果只存在對話裡,下一個改 board.js 的人(或 agent)不會知道,改壞了也沒人擋。
這支把那些規矩變成會失敗的檢查。

做法:用 shot.py 既有的沙箱(複製一份看板、另起一個 server)＋無頭 Chrome 實際載入頁面,
逐條驗行為。動到的是副本,不會碰他真正的看板與標記。

用法:
  uv run python tools/board_check.py            # 全部檢查,有問題回傳非 0
  uv run python tools/board_check.py --quiet    # 只在失敗時輸出
  uv run python tools/board_check.py --fast     # 改介面時先跑的快版:跳過要等假流程/8 秒反悔期的幾條(SLOW)
  uv run python tools/board_check.py --timing   # 最後列出每一條花幾秒
reconcile 換外殼之前會先在副本上跑這支(完整版,不用 --fast),沒過就不換。
等法:用 T.until(條件) / T.idle()(頁面沒有在路上的請求、沒有排著的存檔)/ T.resync(),不要寫死秒數;
機器忙的時候固定秒數等不到,閒的時候又白等。只有要證明「過了多久還是沒發生」才用固定秒數。
改到存檔或看板程式時,也要先跑 python3 -m unittest discover -s tests(commit 時會自動跑)。
"""
import os,sys,json,argparse,tempfile,re,time,subprocess,copy
import urllib.request, urllib.parse
from contextlib import ExitStack
from unittest import mock
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,HERE)

import shot   # 沙箱 + CDP 客戶端(同一份,不另寫)

# 每條規矩 = 一段在頁面裡跑的 JS,回傳空字串代表過,回傳字串代表壞在哪。
CHECKS=[
  ('重整後公司全部收起來',"""
   return [...document.querySelectorAll('#app .cogrp[open]')].length===0 ? ''
     : '有 '+[...document.querySelectorAll('#app .cogrp[open]')].length+' 家一進來就是展開的';
 """),
 # 差不多的東西不應該做出好幾個版本。
 # 每一頁都掃:開合只准是 fold 那一個元件,不准再有自己寫的文字箭頭(下拉選單的 ▾ 是選單,不算)。
 ('整個看板的開合只有一種(每一頁都一樣的箭頭、預設收起)',"""
   var bad=[], tabs=[...document.querySelectorAll('[data-tab]')].map(function(t){return t.getAttribute('data-tab');});
   var cur=(document.querySelector('.tab.on')||{}).getAttribute&&document.querySelector('.tab.on').getAttribute('data-tab');
   for(var i=0;i<tabs.length;i++){
     document.querySelector('[data-tab="'+tabs[i]+'"]').click(); await T.sleep(150);
     var hs=document.querySelectorAll('#app .cogrp>summary'); if(hs[0]&&!hs[0].parentNode.open)hs[0].click();
     var d=[...document.querySelectorAll('details:not(.fold)')];
     if(d.length)bad.push(tabs[i]+' 有 '+d.length+' 個開合不是同一個元件');
     var g=[...document.querySelectorAll('summary,button')].filter(function(el){
       return /[▾▴▸◂]/.test(el.textContent)&&!el.closest('[data-ctl]');});
     if(g.length)bad.push(tabs[i]+' 有 '+g.length+' 個地方還用文字箭頭');
     var op=[...document.querySelectorAll('details.fold[open]')].filter(function(x){return !x.classList.contains('cogrp');});
     if(op.length)bad.push(tabs[i]+' 一進來就開著:'+op.map(function(x){return x.getAttribute('data-fold');}).join('、'));
     if(hs[0]&&hs[0].parentNode.open)hs[0].click();}
   if(cur)document.querySelector('[data-tab="'+cur+'"]').click();
   await T.sleep(150);
   return bad.join('；');
 """),
 ('頂部面板預設都關著',"""
   var bad=[];
   var c=document.querySelector('.cuts-d'); if(c&&c.open)bad.push('切角');
   var n=document.getElementById('noteswrap'); if(n&&n.style.display!=='none')bad.push('我的想法');
   var f=document.getElementById('filterbox'); if(f&&f.style.display!=='none')bad.push('搜尋標籤');
   var fd=document.getElementById('finddet'); if(fd&&fd.open)bad.push('找新職缺');
   return bad.length?bad.join('、')+' 預設是開的':'';
 """),
 ('篩選控制項一進來就看得到且形狀一致',"""
   var b=[...document.querySelectorAll('[data-ctl]')];
   if(b.length<4)return '控制項只有 '+b.length+' 個(應該 4 個以上)';
   var bad=b.filter(function(x){return !x.querySelector('.ctl-k')||!x.querySelector('.ctl-v');});
   return bad.length?bad.length+' 個控制項長得不一樣':'';
 """),
 ('選單一律從按鈕下方展開',"""
   var b=[...document.querySelectorAll('[data-ctl]')], bad=[];
   for(var i=0;i<b.length;i++){
     b[i].click();
     var m=document.getElementById('ctlmenu');
     if(!m){bad.push(b[i].textContent.trim()+':沒展開');continue;}
     var br=b[i].getBoundingClientRect(), mr=m.getBoundingClientRect();
     if(mr.top<br.bottom-2)bad.push(b[i].querySelector('.ctl-k').textContent+':往上開');
     if(mr.right>innerWidth-2||mr.left<2)bad.push(b[i].querySelector('.ctl-k').textContent+':超出畫面');
     b[i].click();
   }
   return bad.join('；');
 """),
 ('破壞性動作固定在卡片右側那條',"""
   var h=document.querySelectorAll('#app .cohead')[0]; if(!h)return '';
   h.click();
   var card=document.querySelector('#app article[data-fid]');
   if(!card){return '展開後沒有卡片';}
   var bad=[];
   if(card.querySelector('.fb-btns [data-rm]')||card.querySelector('.fb-btns [data-s=\\"techerr\\"]'))
     bad.push('心情列又出現一顆破壞性動作');
   var right=card.querySelector('.sr-r');
   if(!right||!right.querySelector('[data-rm]'))bad.push('右側那條沒有移除鈕');
   h.click();
   return bad.join('；');
 """),
 ('卡片不重複講同一件事',"""
   var h=document.querySelectorAll('#app .cohead')[0]; if(!h)return '';
   h.click();
   var card=document.querySelector('#app article[data-fid]'); if(!card){h.click();return '';}
   var bad=[];
   if(card.querySelectorAll('.facts').length!==1)bad.push('事實行 '+card.querySelectorAll('.facts').length+' 個');
   var kk=[...card.querySelectorAll('.srk')].map(function(x){return x.textContent;});
   var dup=kk.filter(function(x,i){return kk.indexOf(x)!==i;});
   if(dup.length)bad.push('摘要欄位重複:'+dup.join(','));
   h.click();
   return bad.join('；');
 """),
 ('主要按鈕摸得到(高度 ≥32px)',"""
   var h=document.querySelectorAll('#app .cohead')[0]; if(!h)return '';
   h.click();
   var card=document.querySelector('#app article[data-fid]'); if(!card){h.click();return '';}
   var small=[...card.querySelectorAll('button')].filter(function(b){
     var r=b.getBoundingClientRect(); return r.height>0&&r.height<32;}).map(function(b){return b.textContent.trim();});
   h.click();
   return small.length?small.join(' / '):'';
 """),
 ('每個分頁都畫得出東西、不丟例外',"""
   var errs=[]; window.onerror=function(m){errs.push(String(m));};
   var tabs=[...document.querySelectorAll('[data-tab]')], bad=[];
   for(var i=0;i<tabs.length;i++){
     tabs[i].click();
     if(!document.getElementById('app').textContent.trim())bad.push(tabs[i].textContent.trim()+' 空白');
   }
   (document.querySelectorAll('[data-tab]')[0]||{click:function(){}}).click();
   if(errs.length)bad.push('例外:'+errs.join('|'));
   return bad.join('；');
 """),
 # 面試題庫是投遞流程的最後一站(🎤 面試準備)。現場要立刻用的東西必須 100% 是有用資訊,
 # 也沒必要把所有東西擠在同一個區塊。所以題目照每題走到哪一站分成流水線(還沒答→答過第一輪→磨合中→定案),一站一列預設收起;
 # 點開一題是逐字稿,題組的子題按鈕只顯示那一問的段落,大字讀稿開得起來、關得掉。
 ('面試準備:流水線四站、題目一題不少;點開是逐字稿;子題只顯示那一問的段落;大字讀稿開得起來',"""
   var t=document.querySelector('[data-tab="iv"]'); if(!t)return '沒有 🎤 面試準備那一籤';
   t.click(); await T.sleep(200);
   var rows=[...document.querySelectorAll('#app details.ivrow[data-iv]')];
   if(!rows.length)return '面試準備那頁沒有題目(題庫沒裝進看板?跑 interview/tools/build_bank.sh)';
   var bad=[];
   var sts=[...document.querySelectorAll('#app details.ivstage')];
   if(sts.length!==4)bad.push('流水線應該是 4 站,現在 '+sts.length+' 站');
   var sum=sts.reduce(function(n,d){return n+(parseInt(((d.querySelector('summary .n')||{}).textContent||'0'),10)||0);},0);
   if(sum!==rows.length)bad.push('每一站的題數加起來是 '+sum+',頁上有 '+rows.length+' 題');
   if(sts.some(function(d){return d.open;})||rows.some(function(r){return r.open;}))bad.push('一進來就有東西是開著的');
   // 不猜面試形式(錄影/真人),實際被哪家考過的才標那家。
   if(document.querySelector('#app .iv-fmt'))bad.push('又出現錄影／真人的標籤');
   if(!document.querySelector('#app details.ivrow .iv-jc'))bad.push('實際考過的題旁邊沒有標是哪一家');
   if(document.querySelector('#app [data-ivscope]'))bad.push('又出現「通用／只給這家」的按鈕(題目一律通用,考過的才標)');
   // 這一步只接上一步做完的:最上面只能列他記成面試中/Offer 的公司,這一頁也不准有回頭改結果的按鈕。
   // 上一步處理完才會跳到這一步,不替單一職缺打專屬補丁。
   var st0=await T.state(), live=Object.keys(st0).filter(function(k){var f=st0[k];
     return f&&typeof f==='object'&&!Array.isArray(f)&&f.app==='sent'&&(f.oc==='iv'||f.oc==='offer')&&!f.rm;});
   var strips=document.querySelectorAll('#app .iv-job').length;
   if(strips!==live.length)bad.push('最上面列了 '+strips+' 家,記成面試中/Offer 的是 '+live.length+' 家');
   // 某一家的研究(JD 重點、落差)不上這一頁。
   if(document.querySelector('#app .iv-doc, [data-ctl="ivjob"]'))bad.push('面試準備頁又出現某一家的參考資料或選公司的選單');
   if(document.querySelector('#app [data-ivoc]'))bad.push('面試準備頁上有回頭改已投遞結果的按鈕');
   var r=rows.filter(function(x){return x.querySelector('.iv-pick');})[0]||rows.filter(function(x){return x.querySelector('.iv-seg');})[0];
   if(!r)return bad.concat('沒有任何一題有逐字稿').join('；');
   var stg=r.closest('details.ivstage'); if(stg&&!stg.open){stg.querySelector('summary').click(); await T.sleep(100);}
   r.querySelector('summary').click(); await T.sleep(100);
   var all=r.querySelectorAll('.iv-seg:not(.off)').length;
   if(!all)bad.push('點開沒有逐字稿');
   var pk=r.querySelector('[data-ivpk="0"]');
   if(pk){pk.click(); await T.sleep(100);
     var n=r.querySelectorAll('.iv-seg:not(.off)').length;
     if(!(n>0&&n<all))bad.push('點子題之後段落沒變少('+all+'→'+n+')');
     r.querySelector('[data-ivpk="-1"]').click(); await T.sleep(100);
     if(r.querySelectorAll('.iv-seg:not(.off)').length!==all)bad.push('按「全部」沒回到全部段落');}
   r.querySelector('[data-ivread]').click(); await T.sleep(100);
   var ov=document.getElementById('rzmodal');
   if(!ov||ov.style.display!=='flex'||!ov.querySelector('.rd-doc .iv-seg'))bad.push('大字讀稿沒打開');
   else{ov.querySelector('.rzm-x').click(); await T.sleep(100); if(ov.style.display!=='none')bad.push('大字讀稿關不掉');}
   if(r.open)r.querySelector('summary').click();
   if(stg&&stg.open)stg.querySelector('summary').click();
   // 題目列上不准出現內部題號(使用者看不懂的代號)。
   var ids=rows.map(function(x){return x.querySelector('summary');}).filter(function(x){
     return /(^|[\\s(（])(AI-\\d+|[QFCG]\\d{1,2})(?=[\\s)）]|$)/.test(x.textContent);});
   if(ids.length)bad.push('題目列上還看得到內部題號('+ids.length+' 題)');
   (document.querySelectorAll('[data-tab]')[0]||{click:function(){}}).click();
   return bad.join('；');
 """),
 # 「留在原地」只對心情標記成立。他按了加入準備,卡片就該離開待評估——
 # 漏掉這半條的時候,他在待評估頁上看到一張掛著「正在準備履歷」的卡,
 # 以為沒生效又按一次。這裡兩半一起驗。
 ('標完心情卡片留在原地,按了加入準備就離開',"""
   var h=document.querySelectorAll('#app .cohead')[0]; if(h)h.click();
   var card=document.querySelector('#app article[data-fid]'); if(!card)return '找不到卡片';
   var fid=card.getAttribute('data-fid');
   var like=[...card.querySelectorAll('.fb-b')].filter(function(b){return b.getAttribute('data-s')==='like';})[0];
   if(!like)return '找不到喜歡鈕';
   like.click();
   var again=document.querySelector('article[data-fid=\"'+CSS.escape(fid)+'\"]');
   var bad=[];
   if(!again)bad.push('標完卡片就消失了(下一步在同一張卡上,不該讓他再找一次)');
   else{
     if(!again.querySelector('.fb-b.like.on')&&!again.querySelector('.sent-badge'))bad.push('看不出已經標過');
     if(!again.querySelector('[data-adv=\"prep\"]'))bad.push('卡上沒有下一步(加入準備)');
     // 同一張卡上按加入準備:這一下要真的讓它離開這一頁
     var add=again.querySelector('[data-adv=\"prep\"]');
     if(add){
       add.click();
       if(document.querySelector('#app article[data-fid=\"'+CSS.escape(fid)+'\"]'))
         bad.push('按了加入準備還留在待評估(他會以為沒生效又按一次)');
       var back=document.querySelector('[data-tab=\"prep\"]'); if(back)back.click();
       var hs=[...document.querySelectorAll('#app .cohead')];
       for(var i=0;i<hs.length;i++){hs[i].click();
         var c2=document.querySelector('#app article[data-fid=\"'+CSS.escape(fid)+'\"]');
         if(c2){var b2=c2.querySelector('.stage-b.back'); if(b2)b2.click(); break;}
         hs[i].click();}
       var n=document.querySelector('[data-tab=\"none\"]'); if(n)n.click();
     }
     var h2=document.querySelectorAll('#app .cohead')[0]; if(h2&&!h2.parentNode.open)h2.click();
     var back2=document.querySelector('#app article[data-fid=\"'+CSS.escape(fid)+'\"]');
     var off=back2&&[...back2.querySelectorAll('.fb-b')].filter(function(b){return b.getAttribute('data-s')==='like';})[0];
     if(off)off.click();   // 再按一次取消,還原
   }
   return bad.join('；');
 """),
 ('破壞性動作做完就地可復原',"""
   var h=document.querySelectorAll('#app .cohead')[0]; if(!h)return '';
   h.click();
   var card=document.querySelector('#app article[data-fid]'); if(!card){h.click();return '';}
   var rm=card.querySelector('[data-rm]'); if(!rm){h.click();return '找不到移除鈕';}
   var s0=document.getElementById('snack'); if(s0)s0.className='';
   rm.click(); await T.sleep(50); await T.idle();   // 按下去等後台回話(#343)
   var s=document.getElementById('snack');
   var ok=s&&s.className==='on'&&/已移到/.test(s.textContent)&&s.querySelector('.snack-undo');
   if(ok){s.querySelector('.snack-undo').click(); await T.sleep(50); await T.idle();}
   return ok?'':'移除後沒有就地可復原的提示';
 """),
]

# ---- 存檔這條路:每一條都是他先撞到才發現、而且會安靜掉資料的 bug ----
# 這幾條要等真的存完才驗得出來,所以是 async;驗的是伺服器上實際存了什麼,不是畫面長怎樣。
# 瀏覽器在背景沒有焦點(focus()/blur() 不會發事件),離開輸入框一律自己送 focusout。
# 無障礙檢查用業界標準的 axe-core(不自己寫「每一格有沒有標籤」):固定版本、核對 sha256,下載一次放快取。
AXE_VERSION, AXE_SHA256 = '4.13.0', 'c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1'


def axe_source():
    import hashlib, urllib.request
    path = os.path.expanduser(f'~/.cache/jobsalvo/axe-{AXE_VERSION}.min.js')
    if not os.path.isfile(path):
        data = urllib.request.urlopen(f'https://cdn.jsdelivr.net/npm/axe-core@{AXE_VERSION}/axe.min.js', timeout=60).read()
        if hashlib.sha256(data).hexdigest() != AXE_SHA256:
            raise RuntimeError('下載的 axe-core 跟固定的版本對不上,不用它')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
    with open(path, encoding='utf-8') as f:
        return f.read()


HELPERS=r"""
window.T={
  sleep:function(ms){return new Promise(function(r){setTimeout(r,ms);});},
  state:function(){return fetch('/api/state',{cache:'no-store'}).then(function(r){return r.json();});},
  card:function(fid){return document.querySelector('#app article[data-fid="'+CSS.escape(fid)+'"]');},
  // 找工作區第一家公司展開,回傳第 n 張卡
  open:function(n){n=n||0; var hs=document.querySelectorAll('#app .cohead');
    for(var i=0;i<hs.length&&document.querySelectorAll('#app article[data-fid]').length<=n;i++)
      if(!hs[i].parentNode.open)hs[i].click();
    return document.querySelectorAll('#app article[data-fid]')[n]||null;},
  type:function(el,v){el.value=v; el.dispatchEvent(new Event('input',{bubbles:true}));},
  leave:function(el){el.dispatchEvent(new FocusEvent('focusout',{bubbles:true}));},
  mood:function(fid,s){var b=T.card(fid)&&T.card(fid).querySelector('.fb-b[data-s="'+s+'"]'); if(b)b.click(); return !!b;},
  snack:function(){var s=document.getElementById('snack'); return s&&s.className==='on'?s.textContent:'';},
  // 把某張卡的心情設回原本的值(測完要還原,後面幾條還要用)
  restore:async function(fid,s0){
    var now=((await T.state())[fid]||{}).s||'';
    if(now!==s0){ if(s0)T.mood(fid,s0); else if(now)T.mood(fid,now); await T.idle();} },
  sync:function(){document.dispatchEvent(new Event('visibilitychange'));},
  // 等條件成立,不等固定秒數:機器忙的時候固定秒數等不到(整批誤報),閒的時候又白等。
  until:async function(fn,ms){var t=Date.now(); ms=ms||8000;
    while(true){var v=await fn(); if(v)return v; if(Date.now()-t>=ms)return v; await T.sleep(100);}},
  // 等頁面安靜:沒有還在路上的請求、看板沒有排著或正在送的存檔,連續 0.3 秒。
  // 取代「按下去 → 等 1.6 秒 → 看伺服器」:存完就往下走,存得慢就多等(最多 ms)。
  idle:async function(ms){var t=Date.now(), quiet=0; ms=ms||15000;
    while(Date.now()-t<ms){
      var now=Date.now(), live=Object.keys(window.__bcInflight||{}).some(function(k){return now-window.__bcInflight[k]<10000;});
      var busy=live||!!(window.__jobsalvoSaveBusy&&window.__jobsalvoSaveBusy());
      quiet=busy?0:quiet+1; if(quiet>=3)return true; await T.sleep(100);}
    return false;},
  // 跟伺服器對一次:先等存檔送完(存檔中的頁面不會去拿),再叫它拿,等拿完。
  resync:async function(){await T.idle(); T.sync(); await T.idle();},
  // 投遞狀態只能靠事件改(看板存檔送來的卡上狀態欄不算數):照狀態表送一串事件給伺服器,回傳被擋下的那幾個
  ds:async function(id,evs){var body={__rev__:1,__events__:evs.map(function(e){return {u:id,ev:e[0],data:e[1]||{}};})};
    var r=await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    return ((await r.json())||{}).rejected||[];},
  // 從還沒填走到某個投遞狀態(看板檢查種卡用);extra 併進填好那一輪的填表紀錄
  toState:async function(id,st,extra){
    var at='2026-01-01T00:00:00', GONE='填好的那一頁不見了(agent 的 Chrome 關掉或重開過),要重填';
    var rec=Object.assign({stage:'fill',at:at,issues:[],tab_id:'1',session:'S-test',where:'chrome',delivery:{method:'direct_upload'}},extra||{});
    var ev=[['fill_start',{apply:{stage:'fill',at:at,issues:['這一輪還沒跑完']}}]];
    if(st==='stuck')ev.push(['fill_bad',{apply:Object.assign({},rec,{issues:(extra&&extra.issues)||['測試用的卡住原因']})}]);
    else if(st==='nopage')ev.push(['fill_nopage',{apply:Object.assign({},rec,{tab_id:'',issues:(extra&&extra.issues)||['測試用的卡住原因']})}]);
    else if(st!=='sent'&&st!=='todo')ev.push(['fill_ok',{apply:rec}]);
    if(st==='gone')ev.push(['page_lost',{issues:[GONE]}]);
    if(st==='stale')ev.push(['files_changed',{why:'履歷換過了'}]);
    if(st==='sent')ev=[['sent_manual',{by:'manual',at:at,sent_at:'2026-01-01'}]];
    if(st==='todo')ev=[];
    var bad=await T.ds(id,ev);
    if(['confirmed','sending','unsure'].indexOf(st)>=0){var fb=await T.state(), bank={};
      (fb.__ans__||[]).forEach(function(e){bank[e.k]=e;});
      var snap={}; ((fb[id]||{}).form||{f:[]}).f.forEach(function(x){var e=x.src==='bank'?bank[x.k]:x; snap[x.q||'']=(e&&e.v)||'';});
      var more=[['confirm',{approve:{at:at,snap:snap,round:rec.at}}]];
      if(st!=='confirmed')more.push(['submit_start',{}]);
      if(st==='unsure')more.push(['submit_unsure',{evidence:{at:at,problems:['沒看到成功頁面'],clicked:true}}]);
      bad=bad.concat(await T.ds(id,more));}
    return bad;}
};
// 記還在路上的請求(T.idle 用)。檢查裡自己包 fetch 再還原時,還原回來的就是這一層。
// 超過 10 秒還沒回來的不算(例如截圖那種慢請求),免得 T.idle 為它一直等到上限。
if(!window.__bcFetchWrapped){window.__bcFetchWrapped=true; window.__bcInflight={}; var __bcN=0;
  var __bcFetch=window.fetch;
  window.fetch=function(){var id=++__bcN; window.__bcInflight[id]=Date.now(); var p=__bcFetch.apply(this,arguments);
    var end=function(){delete window.__bcInflight[id];}; p.then(end,end); return p;};}
function FB0(){return JSON.parse(document.getElementById('data-fb').textContent||'{}');}
window.FB0=FB0;
"""

SAVE_CHECKS=[
 ('改了會自己存,不用按',"""
   var c=T.open(); if(!c)return '找不到卡片';
   var fid=c.getAttribute('data-fid'), s0=((await T.state())[fid]||{}).s||'';
   var want=s0==='grow'?'meh':'grow';
   T.mood(fid,want); await T.idle();
   var got=((await T.state())[fid]||{}).s||'';
   await T.restore(fid,s0);
   return got===want?'':'按了 1.6 秒後伺服器上還是「'+got+'」';
 """),
 ('送出途中又打的字不會被當成已存',"""
   var c=T.open(); if(!c)return '找不到卡片';
   var fid=c.getAttribute('data-fid'), ta=c.querySelector('.fb-t'), orig=ta.value;
   // 攔住送出:存檔請求一發出去就接著打字。不管存檔是誰觸發的(離開輸入框或計時器),
   // 後面那幾個字一定是在送出途中打的,回應還沒回來。
   var of=window.fetch, typed=false;
   window.fetch=function(u){var p=of.apply(this,arguments);
     if(!typed&&String(u).indexOf('/api/save')>=0){typed=true; T.type(ta,orig+'甲乙丙');}
     return p;};
   T.type(ta,orig+'甲'); T.leave(ta);
   await T.until(async function(){return (((await T.state())[fid]||{}).n||'')===orig+'甲乙丙';},15000);
   await T.idle(); window.fetch=of;
   var n=((await T.state())[fid]||{}).n||'';
   var t2=T.card(fid).querySelector('.fb-t'); T.type(t2,orig); T.leave(t2); await T.idle();
   return n===orig+'甲乙丙'?'':'伺服器上停在「'+n.slice(-4)+'」,送出途中打的字沒送出去';
 """),
 ('取消再重標不會被當成衝突',"""
   var c=T.open(); if(!c)return '找不到卡片';
   var fid=c.getAttribute('data-fid'), s0=((await T.state())[fid]||{}).s||'';
   var a=s0==='like'?'grow':'like', b=s0==='meh'?'dislike':'meh';
   T.mood(fid,a); await T.idle();
   T.mood(fid,a); await T.idle();     // 取消
   T.mood(fid,b); await T.idle();     // 再標另一個
   var got=((await T.state())[fid]||{}).s||'', sn=T.snack();
   await T.restore(fid,s0);
   if(/別的裝置/.test(sn))return '跳出「'+sn+'」:取消後的空值被當成別人改過';
   return got===b?'':'伺服器上是「'+got+'」,第三下沒存進去';
 """),
 ('「我的想法」連存兩次都存得進去',"""
   var add=document.getElementById('note-add'); if(!add)return '找不到新增想法';
   add.click();
   var ts=document.querySelectorAll('#notes .note-t'), t=ts[ts.length-1], bad=[];
   function has(x){return T.state().then(function(s){return (s['__notes__']||[]).some(function(n){return n.t===x;});});}
   // 「馬上存」看的是離開輸入框當下有沒有送出存檔(不是等 2 秒的計時器),不看伺服器多快回:
   // 機器忙的時候伺服器慢,以前用「1.9 秒內伺服器上看得到」會誤報。
   var of=window.fetch, sentAt=0, t0;
   window.fetch=function(u){if(!sentAt&&String(u).indexOf('/api/save')>=0)sentAt=Date.now(); return of.apply(this,arguments);};
   try{T.type(t,'測試第一次'); t0=Date.now(); T.leave(t); await T.until(function(){return sentAt;},1500);}
   finally{window.fetch=of;}
   if(!sentAt||sentAt-t0>1000)bad.push('離開輸入框沒有馬上存(要乾等計時器)');
   await T.idle();
   if(!(await has('測試第一次')))bad.push('第一次存的沒進伺服器');
   await T.idle();
   T.type(t,'測試第二次'); T.leave(t); await T.idle();
   if(!(await has('測試第二次')))bad.push('第二次存的沒進伺服器'+(T.snack()?'(跳了:'+T.snack()+')':''));
   var d=t.closest('.note-card').querySelector('.note-del'); if(d)d.click(); await T.idle();
   return bad.join('；');
 """),
 ('別的裝置先改了同一張:兩邊改的格子都留下並告訴他,同一批其他改動照存',"""
   var c1=T.open(0), c2=T.open(1); if(!c1||!c2)return '要兩張卡';
   var A=c1.getAttribute('data-fid'), B=c2.getAttribute('data-fid');
   var st=await T.state(), a0=st[A]||null, b0=st[B]||null;
   var other=Object.assign({},a0||{},{n:'別台手機寫的'});
   var p={__rev__:1,__base__:{}}; p.__base__[A]=a0; p[A]=other;
   await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
   var sa=(a0||{}).s||'', sb=(b0||{}).s||'';
   T.mood(A,sa==='grow'?'meh':'grow'); T.mood(B,sb==='grow'?'meh':'grow');   // 同一批送出
   // 衝突那一包被擋回來、頁面照伺服器的版本改、再把另一張重送:等兩件事都落地
   // 提示條幾秒後會自己收掉:等的時候看到過就算(以前等完才看,機器慢就錯過)
   var saw='';
   await T.until(async function(){if(/別的裝置/.test(T.snack()))saw=T.snack(); var s=await T.state();
     return ((s[A]||{}).n||'')==='別台手機寫的'&&((s[A]||{}).s||'')===(sa==='grow'?'meh':'grow')
       &&((s[B]||{}).s||'')===(sb==='grow'?'meh':'grow')&&saw;},15000);
   await T.idle();
   var st2=await T.state(), sn=saw||T.snack(), bad=[];
   if(((st2[A]||{}).n||'')!=='別台手機寫的')bad.push('別台寫的心得被蓋掉了');
   if(((st2[A]||{}).s||'')!==(sa==='grow'?'meh':'grow'))bad.push('這台改的心情被丟掉了');
   if(!/別的裝置/.test(sn))bad.push('沒告訴他');
   if(((st2[B]||{}).s||'')!==(sb==='grow'?'meh':'grow'))bad.push('同一批另一張沒存進去');
   var ta=T.card(A)&&T.card(A).querySelector('.fb-t');
   if(ta&&ta.value!=='別台手機寫的')bad.push('畫面上還是舊的心得');
   // 還原
   var q={__rev__:1,__base__:{}}; q.__base__[A]=st2[A]||null; q.__base__[B]=st2[B]||null; q[A]=a0; q[B]=b0;
   await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q)});
   await T.resync();
   return bad.join('；');
 """),
 ('別的裝置改了同一格:留這台打的字,按復原換回那邊的',"""
   var c=T.open(0); if(!c)return '要一張卡';
   var A=c.getAttribute('data-fid'), a0=(await T.state())[A]||null, bad=[];
   var p={__rev__:1,__base__:{}}; p.__base__[A]=a0; p[A]=Object.assign({},a0||{},{n:'別台手機寫的'});
   await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
   var ta=T.card(A).querySelector('.fb-t'); if(!ta)return '卡上沒有心得欄';
   T.type(ta,'這台打的字'); T.leave(ta);
   var u=null;
   await T.until(async function(){u=document.querySelector('#snack.on .snack-undo');
     return u&&((await T.state())[A]||{}).n==='這台打的字';},15000);
   await T.idle();
   if(((await T.state())[A]||{}).n!=='這台打的字')bad.push('這台打的字沒存進去');
   if(!u)bad.push('撞到同一格沒告訴他、也沒有復原');
   else{u.click();
     await T.until(async function(){return ((await T.state())[A]||{}).n==='別台手機寫的';},15000);
     await T.idle();
     if(((await T.state())[A]||{}).n!=='別台手機寫的')bad.push('按復原沒換回別台寫的');
     var t2=T.card(A)&&T.card(A).querySelector('.fb-t');
     if(t2&&t2.value!=='別台手機寫的')bad.push('按復原後畫面上還是這台的字');}
   var st=await T.state(), q={__rev__:1,__base__:{}}; q.__base__[A]=st[A]||null; q[A]=a0;
   await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q)});
   await T.resync();
   return bad.join('；');
 """),
 ('外部程式改了看板檔,開著的頁面自己跟上',"""
   T.sync(); await T.until(function(){return (FB0()[P.fid]||{}).s==='grow';});
   var s=(FB0()[P.fid]||{}).s||'';
   return s==='grow'?'':'檔案已經改了,頁面還是「'+s+'」(要他自己重整才看得到)';
 """, 'external_write'),
]

# ---- 已投遞之後的結果 ----
OUTCOME_CHECKS=[
 ('已投遞記結果:卡片不動、統計跟著變、可以復原',"""
   var t=document.querySelector('[data-tab="sent"]'); if(!t)return '沒有已投遞分頁'; t.click();
   var c=T.open(); if(!c){document.querySelector('[data-tab="none"]').click(); return '';}
   var fid=c.getAttribute('data-fid'), y0=c.getBoundingClientRect().top, bad=[];
   var f0=(await T.state())[fid]||{};
   function sum(){var s=document.querySelector('#funnel .fn-sum'); return s?s.textContent:'';}
   var s0=sum(); if(!s0)bad.push('上面沒有成效統計');
   var want=(f0.oc==='iv')?'offer':'iv', b=c.querySelector('[data-oc="'+want+'"]');
   if(!b){document.querySelector('[data-tab="none"]').click(); return '卡上沒有記結果的按鈕';}
   b.click();
   var c2=T.card(fid);
   if(!c2)bad.push('記完卡片不見了');
   else{var dy=Math.round(c2.getBoundingClientRect().top-y0); if(Math.abs(dy)>2)bad.push('卡片移了 '+dy+'px');
     if(!c2.querySelector('[data-oc="'+want+'"].on'))bad.push('看不出記成什麼');}
   if(sum()===s0)bad.push('統計沒跟著變');
   await T.idle();
   if(((await T.state())[fid]||{}).oc!==want)bad.push('沒存進去');
   var u=document.querySelector('#snack .snack-undo'); if(!u)bad.push('沒有復原'); else u.click();
   await T.idle();
   var f1=(await T.state())[fid]||{};
   if((f1.oc||'')!==(f0.oc||'')||JSON.stringify(f1.oc_at||{})!==JSON.stringify(f0.oc_at||{}))bad.push('復原後跟原本不一樣');
   document.querySelector('[data-tab="none"]').click();
   return bad.join('；');
"""),
 ('已投遞成效:來源平台拆分與拒絕理由連回原文',"""
   var t=document.querySelector('[data-tab="sent"]'); if(!t)return '沒有已投遞分頁'; t.click(); await T.sleep(300);
   var funnel=document.querySelector('#funnel'), sum=funnel&&funnel.querySelector('.fn-sum');
   if(!funnel||!sum){document.querySelector('[data-tab="none"]').click(); return '沒有成效統計';}
   if(!funnel.open)sum.click(); await T.sleep(200);
   var rows=[...funnel.querySelectorAll('.fn-t tbody tr')], start=rows.findIndex(function(r){
     return r.classList.contains('fn-g')&&r.textContent.trim()==='來源平台';
   }), platformRows=[];
   if(start<0){document.querySelector('[data-tab="none"]').click(); return '沒有來源平台拆分';}
   for(var i=start+1;i<rows.length&&!rows[i].classList.contains('fn-g');i++)platformRows.push(rows[i]);
   var platforms={}; platformRows.forEach(function(r){platforms[r.cells[0].textContent.trim()]=r.cells[1].textContent.trim();});
   if(platforms['104']!=='1'||platforms['LinkedIn']!=='1'){
     document.querySelector('[data-tab="none"]').click(); return '平台列或各列投出數不符: '+JSON.stringify(platforms);
   }
   var reason=funnel.querySelector('.fn-reasons li'), link=reason&&reason.querySelector('a.fn-source');
   if(!reason||!reason.textContent.includes('示範拒絕理由')){
     document.querySelector('[data-tab="none"]').click(); return '沒有顯示拒絕理由';
   }
   if(!link||link.getAttribute('href')!=='https://mail.example/thread/demo-rejection'||
      link.textContent.trim()!=='查看原文'||link.target!=='_blank'||!link.rel.includes('noopener')){
     document.querySelector('[data-tab="none"]').click(); return '拒絕理由沒有可回原文的連結';
   }
   funnel.open=false; document.querySelector('[data-tab="none"]').click();
   return '';
 """),
 ('已投遞分區:同一家公司的卡分在不同區(等回音、已結束),每區展開看到的都是自己那區的卡,不重複、不跑錯公司',"""
   document.querySelector('[data-tab="sent"]').click(); await T.sleep(300);
   var st=await T.state(), bad=[], seen={};
   var SEC={'🎉 Offer':'offer','🗣 面試中':'iv','⏳ 等回音':'wait','🗂 已結束':'end'}, END={rej:1,ghost:1,wd:1};
   function secOf(f){var s=(f||{}).oc||''; return END[s]?'end':(s||'wait');}
   var grps=[].slice.call(document.querySelectorAll('#app .applygrp'));
   for(var g=0;g<grps.length;g++){
     var h=grps[g].querySelector('h2'), k=null;
     Object.keys(SEC).forEach(function(l){if(h&&h.textContent.indexOf(l)===0)k=SEC[l];});
     if(!k)continue;
     var cos=[].slice.call(grps[g].querySelectorAll('.cogrp'));
     for(var i=0;i<cos.length;i++){if(!cos[i].open){cos[i].querySelector('summary').click(); await T.sleep(150);}
       var co=cos[i].querySelector('.coname').textContent;
       [].slice.call(cos[i].querySelectorAll('article[data-fid]')).forEach(function(a){var id=a.getAttribute('data-fid');
         if(seen[id])bad.push('同一張卡畫了兩次:'+id.slice(-30));
         seen[id]=1;
         if(secOf(st[id])!==k)bad.push('「'+h.textContent.replace(/\\s+\\d+$/,'')+'」的「'+co+'」底下畫出別區的卡:'+id.slice(-30));});}}
   document.querySelector('[data-tab="none"]').click();
   return bad.slice(0,4).join('；');
 """),
 ('結果往前走留著走過的日期,往回改收乾淨',"""
   document.querySelector('[data-tab="sent"]').click();
   var c=T.open(); if(!c){document.querySelector('[data-tab="none"]').click(); return '';}
   var fid=c.getAttribute('data-fid'), f0=(await T.state())[fid]||null, bad=[];
   function oc(v){var b=T.card(fid).querySelector('[data-oc="'+v+'"]'); if(b)b.click();}
   oc('iv'); oc('rej');
   if(!/面試後/.test(T.card(fid).querySelector('.stage-done').textContent))bad.push('面試後沒錄取,看不出曾經面試過');
   oc(''); await T.idle();
   var f=(await T.state())[fid]||{};
   if(f.oc||f.oc_at)bad.push('改回等回音後還留著 '+JSON.stringify({oc:f.oc,oc_at:f.oc_at}));
   var q={__rev__:1,__base__:{}}; q.__base__[fid]=f; q[fid]=f0;
   await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q)});
   T.sync(); await T.sleep(1000);
   document.querySelector('[data-tab="none"]').click();
   return bad.join('；');
 """),
]
CHECKS=CHECKS+OUTCOME_CHECKS
# 使用者只碰網頁就要能把 jobsalvo 設定好、修正分錯的卡(不用叫任何人改檔)。
WEB_CHECKS=[
 ('設定頁:一進來看得到「開始前」清單,每一塊預設收起,點清單那一項就打開那一塊,存檔鈕在',"""
   document.querySelector('[data-tab="cfg"]').click(); await T.idle();
   var bad=[];
   if(!document.querySelector('#app .cfg-check'))bad.push('沒有開始前清單');
   if(document.querySelector('#app details.cfg-d[open]'))bad.push('一進來就有一塊是開著的');
   if(!document.querySelector('#app [data-cfsave]'))bad.push('沒有儲存鈕');
   var go=document.querySelector('#app [data-cfgo="cfg:preferences"]');
   if(go){go.click(); await T.sleep(300);
     if(!document.querySelector('#app details[data-fold="cfg:preferences"][open]'))bad.push('點清單的「偏好筆記」沒有打開那一塊');
     var d=document.querySelector('#app details[data-fold="cfg:preferences"]'); if(d&&d.open)d.querySelector('summary').click();}
   document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
   return bad.join('；');
 """),
 ('設定頁:五種找缺與判斷的做法可各自選取並存回',"""
   document.querySelector('[data-tab="cfg"]').click(); await T.idle();
   var bad=[], original=null, realTimeout=window.setTimeout;
   try{
     var fold=document.querySelector('details[data-fold="cfg:research-skills"]');
     if(!fold)return '找不到找缺與判斷的做法那一區';
     if(!fold.open)fold.querySelector('summary').click();
     var selects=[...document.querySelectorAll('select[data-cf^="research.skills."]')];
     if(selects.length!==5)return '找缺與判斷的做法選單數量不對:'+selects.length;
     if(selects.some(function(s){return s.options[0].value!=='';}))return '空白選項沒有沿用產品預設';
     var settings=await fetch('/api/settings').then(function(r){return r.json();});
     original=JSON.parse(JSON.stringify(settings.settings||{}));
     var tasks=settings.research_skill_tasks||[];
     if(tasks.length!==5||tasks.some(function(x){return !x.default_content||!x.default_content.trim();}))
       return '設定頁沒有拿到五份預設 skill 的完整內容';
     for(var i=0;i<tasks.length;i++){
       var preview=document.querySelector('[data-research-default="'+tasks[i].key+'"]');
       if(!preview||preview.textContent!==tasks[i].default_content)
         return '設定頁看不到「'+tasks[i].label+'」的預設全文';
     }
     var common=tasks.find(function(x){return x.key==='common';});
     var commonCard=document.querySelector('[data-research-skill="common"]');
     var copy=commonCard&&commonCard.querySelector('[data-cfresearch-copy="common"]');
     if(!copy)return '共通 skill 沒有「拿預設改成自己的」';
     copy.click();
     var editor=commonCard.querySelector('[data-cfresearch-editor="common"]');
     var content=commonCard.querySelector('[data-cfresearch-content="common"]');
     if(!editor||editor.hidden||!content||content.value!==common.default_content)
       return '編輯欄沒有以共通預設全文起稿';
     content.value+=' BOARD-CHECK-EDITED-DEFAULT';
     content.dispatchEvent(new Event('input',{bubbles:true}));
     var create=commonCard.querySelector('[data-cfresearch-save="common"]');
     if(!create)return '沒有儲存並選用的按鈕';
     create.click();
     var selected=null, said='';
     for(var n=0;n<150;n++){
       await T.sleep(100); said=T.snack()||said;
       saved=await fetch('/api/settings').then(function(r){return r.json();});
       selected=(((saved.settings||{}).research||{}).skills||{}).common;
       if(selected&&selected!=='')break;
     }
     // 伺服器先存好,頁面接著才重讀設定、重畫選單;等畫面跟上(之前立刻看,慢一點就誤報)
     await T.until(function(){var x=document.querySelector('select[data-cf="research.skills.common"]'); return x&&x.value===selected;},15000);
     var commonSelect=document.querySelector('select[data-cf="research.skills.common"]');
     if(!selected||!selected.startsWith('custom/skills/')||!commonSelect||commonSelect.value!==selected)
       return '儲存後沒有立即選用新建的共通 skill'+(said?'(畫面說:'+said+')':'');
     var path='custom/skills/board-check.md';
     selects=[...document.querySelectorAll('select[data-cf^="research.skills."]')];
     for(var i=0;i<selects.length;i++){
       if(![...selects[i].options].some(function(o){return o.value===path;}))
         return '選單沒有列出使用者 skill; options='+JSON.stringify([...selects[i].options].map(function(o){return o.value;}))+
           '; API='+JSON.stringify((settings.skills||[]).map(function(s){return s.path;}));
       if(selects[i].value!==path){selects[i].value=path; selects[i].dispatchEvent(new Event('change',{bubbles:true}));}
     }
     window.setTimeout=function(fn,ms){return ms===700?0:realTimeout.apply(window,arguments);};
     var save=document.querySelector('#app [data-cfsave]');
     if(!save)return '找不到儲存設定按鈕';
     save.click();
     var saved=null;
     for(var n=0;n<150;n++){
       await T.sleep(100);
       saved=await fetch('/api/settings').then(function(r){return r.json();});
       var values=((saved.settings||{}).research||{}).skills||{};
       if(['common','deep','wide','dir','judge'].every(function(k){return values[k]===path;}))break;
     }
     var values=((saved.settings||{}).research||{}).skills||{};
     if(!['common','deep','wide','dir','judge'].every(function(k){return values[k]===path;}))
       bad.push('五份 skill 沒有各自記住');
   }catch(e){bad.push('操作找缺與判斷的做法出錯:'+e.message);}
   finally{
     await T.idle();   // 同上:存檔請求都回來、重載被擋掉之後才還原 setTimeout
     window.setTimeout=realTimeout;
     if(original!==null)try{
       var restored=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
         body:JSON.stringify({settings:original})});
       var result=await restored.json();
       if(!restored.ok||!result.ok)bad.push('無法還原沙箱設定:'+(result.msg||restored.status));
     }catch(e){bad.push('還原沙箱設定出錯:'+e.message);}
     document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
   }
   return bad.join('；');
 """),
 ('卡片 ⋯ 可以改類別:改了存得進去、卡片換到那一類;選「照關鍵字」就拿掉',"""
   document.querySelector('[data-tab="all"]').click(); await T.sleep(300);
   var c=T.open(); if(!c)return '找不到卡片';
   var fid=c.getAttribute('data-fid'), bad=[];
   var ob=c.querySelector('[data-omore]'); if(ob){ob.click(); await T.sleep(100);}
   var op=c.querySelector('[data-catopen]'); if(!op)return '⋯ 裡沒有「🗂 改類別」';
   op.click(); await T.sleep(100);
   var picks=[...c.querySelectorAll('[data-catpick]')].filter(function(b){return b.getAttribute('data-catpick');});
   if(picks.length<2)return '改類別的選單沒有列出類別';
   var want=picks[picks.length-1].getAttribute('data-catpick'); picks[picks.length-1].click(); await T.idle();
   if(((await T.state())[fid]||{}).cat!==want)bad.push('改的類別沒存進去');
   c=T.card(fid)||document.querySelector('#app article[data-fid="'+CSS.escape(fid)+'"]');
   if(c){var ob2=c.querySelector('[data-omore]'); if(ob2){ob2.click(); await T.sleep(100);}
     var op2=c.querySelector('[data-catopen]'); if(op2){op2.click(); await T.sleep(100);
       var clr=c.querySelector('[data-catpick=""]'); if(clr){clr.click(); await T.idle();}}}
   if(((await T.state())[fid]||{}).cat)bad.push('選「照關鍵字」之後指定的類別還在');
   document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
   return bad.join('；');
 """),
]
WEB_CHECKS.append(('設定頁:偏好筆記分開顯示、編輯後存得住,手改假設會成為使用者自訂',"""
   document.querySelector('[data-tab="cfg"]').click(); await T.idle();
   var bad=[];
   var go=document.querySelector('#app [data-cfgo="cfg:preferences"]');
   if(go)go.click(); else {var f=document.querySelector('#app details[data-fold="cfg:preferences"]'); if(f&&!f.open)f.querySelector('summary').click();}
   await T.sleep(300);
   var custom=document.querySelector('#app textarea[data-cft="preferences_custom"]');
   var agent=document.querySelector('#app textarea[data-cft="preferences_agent"]');
   if(!custom||!agent)return '偏好筆記沒有分開的使用者自訂和 Agent 假設欄位';
   if(custom.value.indexOf('使用者自訂原話')<0||agent.value.indexOf('Agent 假設原文')<0){
     return '使用者自訂與 Agent 假設沒有顯示在各自欄位';
   }
   var save=document.querySelector('#app [data-cfsave]'); if(!save)return '找不到儲存設定按鈕';
   // 儲存鈕會延遲重載頁面;這裡驗證內容存進去,讓同一個 browser session 後續檢查可續跑。
   var oldTimeout=window.setTimeout;
   window.setTimeout=function(fn,delay){
     if(delay===700&&String(fn).indexOf('location.reload')>=0)return 0;
     return oldTimeout(fn,delay);
   };
   try{
     custom.value='使用者自訂已修改'; custom.dispatchEvent(new Event('input',{bubbles:true}));
      agent.value=agent.value+'\\n- 使用者改過的假設'; agent.dispatchEvent(new Event('input',{bubbles:true}));
     save.click();
     var d=null;
     for(var i=0;i<80;i++){
       await T.sleep(100); d=await fetch('/api/settings').then(function(r){return r.json();});
       if(d.texts&&d.texts.preferences_custom.indexOf('使用者改過的假設')>=0)break;
     }
     if(!d.texts||d.texts.preferences_custom.indexOf('使用者自訂已修改')<0)
       bad.push('使用者自訂沒有存好');
     if(!d.texts||d.texts.preferences_agent.indexOf('Agent 假設原文')<0)
       bad.push('原有 Agent 假設沒有保留');
     if(!d.texts||d.texts.preferences_custom.indexOf('使用者改過的假設')<0)
       bad.push('使用者編輯的假設沒有標成使用者自訂');
     if(d.texts&&d.texts.preferences_agent.indexOf('使用者改過的假設')>=0)
       bad.push('使用者編輯的假設仍被標成 Agent 假設');
   }finally{
     // 存檔回來才會排「0.7 秒後重載」;要等請求都回來了才還原 setTimeout,
     // 不然回應慢一點(機器忙),重載就用真的 setTimeout 排上去,下一條檢查跑到一半頁面被換掉。
     await T.idle(); window.setTimeout=oldTimeout;}
   document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
   return bad.join('；');
 """))

CHECKS=CHECKS+WEB_CHECKS
# ── 表單答案庫、代投、agent 回報那幾條(規矩寫法一樣:會失敗的檢查)──
#
# 守的規矩:
#   · 答案只有一個真相:表單答案庫。表單上只記用了哪一條,卡片上的表單只剩一行,
#     要看要改答案一律去答案庫。
#   · 任何推論的東西先進答案庫、標「我推論的」,他確認一次,用到它的每張卡一起算過,分頁 ⚠ 只算一次。
#   · 答案庫每一條都能在看板上改、新增、刪,每一顆按鈕按了都能復原(怕手滑)。改值就是確認;值一改,
#     還沒送出的表單標「雇主網頁待重打」,已投遞的不標。還沒送出的表單在用的,刪除變成「清掉答案」(題目留著、下一輪 agent 代填)。
#   · 每條答案分「共用」和「這缺專用」:我先判斷,他按一下切換(可復原)。這缺專用的連到那個 JD。
#   · 面板和每一條都是看板共用的 fold:預設收起,有待確認的只在標題上亮 ⚠;他展開的那條,重畫後還開著。
#   · 英文答案一定附中文,使用者看到的、能改的是中文。他改了中文,
#     那條標「英文待重翻」(agent 填表或修改時照中文重翻、記回答案庫;不算使用者的 ⚠),還沒送出的表單標「雇主網頁待重打」。
#
# 檢查在副本上跑,自己用 /api/save 種兩張可投遞卡、一張已投遞卡和一條 zz_nat 推論,不依賴看板現在有什麼資料。
# 每一條開頭先等前一條的自動存檔送完、結尾也等,不然種資料會跟前一條還沒送出的存檔撞在一起。



def ans_cards(board):
    """種卡(直接寫副本的看板檔:投遞狀態只能靠事件改,看板存檔改不動):挑四張沒被封鎖的職缺,
    兩張 agent 已經填好、停著等你(投遞狀態 parked),一張已投出,一張讀過表單、agent 還沒填過;表單都有一欄指向 zz_nat。"""
    import board_doc as bd
    jobs = [j for j in bd.load(board)['data']['jobs'] if not j.get('bk')]
    ids = [j['id'] for j in jobs[:4]]

    def form(lock=False, why=False):
        f = {'plat': '測試', 'at': '2026-01-01', 'f': [{'q': 'What is your nationality?', 'src': 'bank', 'k': 'zz_nat'}]}
        if why:
            f['f'].append({'q': 'Why this role?', 'src': 'bank', 'k': 'zz_why'})
        if lock:
            f['lock'] = 1
        return f
    filled = {'stage': 'fill', 'at': '2026-01-01T00:00:00', 'issues': [], 'delivery': {'method': 'direct_upload'},
              'tab_id': '1'}     # 沒有那段對話的 id:叫不回同一隻 agent(要它改的那一條自己補)

    def mut(fb):
        fb[ids[0]] = {'app': 'ship', 'ds': 'parked', 'form': form(why=True), 'apply': dict(filled)}
        fb[ids[1]] = {'app': 'ship', 'ds': 'parked', 'form': form(), 'apply': dict(filled)}
        fb[ids[2]] = {'app': 'sent', 'ds': 'sent', 'sent_by': 'manual', 'sent_at': '2026-01-01', 'form': form(lock=True)}
        fb[ids[3]] = {'app': 'ship', 'form': form()}   # 第四張:讀過表單、agent 還沒填過(雇主網頁上什麼都還沒有)
    bd.set_fb(mut, live=board, by='board_check')
    return {'ids': ids}


ANS_PRE = {'ans_cards': ans_cards}

# 種資料:四張卡由 ans_cards 種好;答案庫加一條「我推論的」。
SEED = r"""
await T.idle();
var ids=P.ids;
var fb=await T.state();
var ans=(fb.__ans__||[]).filter(function(e){return !/^zz_/.test(e.k);});
ans.push({k:'zz_nat',q:'你的國籍(測試)',v:'Taiwan',zh:'台灣',why:'測試用的推論 '+Date.now(),inf:'2026-01-01'});
ans.push({k:'zz_empty',q:'空白測試',v:'',why:'',inf:'2026-01-01'});
ans.push({k:'zz_why',q:'為什麼對這個職位有興趣(測試)',v:'Because.',zh:'因為。',pj:1,pjw:'測試用',why:'',at:'2026-01-01'});   // 這缺專用,只有第一張在用   // 答案還空著:不給 ✓(一按就變成「都確認過了」)   // 每次內容都不同:版本號是內容雜湊,種回一模一樣的內容,頁面會以為沒變
// 兩張可投遞都是 agent 已經填好、停在送出前的樣子:確認的是那一頁,還沒填過的卡不給確認
// (真的送出要叫回填這張的那段對話,沒有就整筆作廢,見 apply_run._no_session)。
var body={__rev__:1,__ans__:ans};
await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
await T.resync();
document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
[].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
await T.sleep(300);
var A=T.card(ids[0]), B=T.card(ids[1]);
if(!A||!B)return '找不到種下去的兩張測試卡';
var badge=function(){var w=document.querySelector('[data-tab="ship"] .warnn'); return w?(+w.textContent.replace(/\D/g,'')||0):0;};
var line=function(c){return c.querySelector('.fm-line');};
var row=function(k){return document.querySelector('#app .ansrow[data-k="'+(k||'zz_nat')+'"]');};
var openRow=async function(k){var r=row(k); if(!r.open){r.querySelector('summary').click(); await T.sleep(200);} return row(k);};
var closeRow=async function(k){var r=row(k); if(r.open){r.querySelector('summary').click(); await T.sleep(200);} return row(k);};
var shown=function(el){return !!el&&(el.checkVisibility?el.checkVisibility():el.offsetParent!==null);};   // 收起的 <details> 內容是 content-visibility 藏的,offsetParent 看不出來
var bad=[];
var done=async function(){await T.idle(); return bad.join('；');};
"""

ANS_CHECKS = [
 ('答案一次確認完:一次一條,空的要寫才能確認,確認一條用到它的卡一起放行;分頁 ⚠ 跟著少', SEED + r"""
   var go=document.querySelector('#app [data-ansq]'); if(!go)return '可投遞有答案等他確認,卻沒有「▶ 答案一次確認完」';
   var errs=[]; window.addEventListener('error',function(ev){errs.push(ev.message);});
   var b0=badge(); go.click(); await T.sleep(300);
   var seen=0;
   for(var i=0;i<10;i++){var m=document.getElementById('rzmodal'); if(!m||m.style.display!=='flex'||!m.querySelector('[data-ansqv]'))break;
     var ta=m.querySelector('[data-ansqv]'), k=ta.getAttribute('data-ansqv'); seen++;
     if(!/^zz_/.test(k)){m.querySelector('[data-ansq-skip]').click(); await T.sleep(200); continue;}
     if(!ta.value.trim()){m.querySelector('[data-ansq-ok]').click(); await T.sleep(200);
       if(document.querySelector('#rzmodal [data-ansqv]')!==ta&&document.querySelector('#rzmodal [data-ansqv]').getAttribute('data-ansqv')!==k)bad.push('空的答案沒寫就能確認');
       ta=document.querySelector('#rzmodal [data-ansqv]'); ta.value='測試答案';}
     document.querySelector('#rzmodal [data-ansq-ok]').click(); await T.sleep(250);}
   if(!seen)bad.push('按了沒有出現第一條');
   var mm=document.getElementById('rzmodal'); if(mm&&mm.style.display==='flex')bad.push('全部確認完,視窗沒有自己關(剩:'+((mm.querySelector('.ansq-q')||{}).textContent||'')+')');
   await T.sleep(1500);
   var st=await T.state(), a=(st.__ans__||[]), g=function(k){return a.filter(function(e){return e.k===k;})[0]||{};};
   if(g('zz_nat').inf)bad.push('確認了,那一條還標著我推論的');
   if(!String(g('zz_empty').v||g('zz_empty').zh||'').trim())bad.push('空的那一條寫了答案沒存進去');
   A=T.card(ids[0]); B=T.card(ids[1]);
   [A,B].forEach(function(c,i){if(c&&!/答案都確認過了/.test((line(c)||{}).textContent||''))bad.push('確認完第 '+(i+1)+' 張卡還在等答案('+((line(c)||{}).textContent||'')+')');});
   if(errs.length)bad.push('頁面丟了例外:'+errs.join('|'));
   if(badge()>=b0)bad.push('分頁 ⚠ 沒有跟著少('+b0+' → '+badge()+')');
   return await done();
 """, 'ans_cards'),
 ('答案庫:預設收起(有待確認也只在標題亮 ⚠),一條一行字,點了那一條才展開,✓ 不用展開就能按,重畫後展開的還開著', SEED + r"""
   var d=document.querySelector('#app .ans-d'); if(!d)return '看不到答案庫面板';
   if(d.open)bad.push('答案庫預設是開的(有待確認也只該在標題亮 ⚠,不該自動展開)');
   if(!/待你確認/.test(d.querySelector('summary').textContent))bad.push('收著的時候,標題上看不出有待確認');
   d.querySelector('summary').click(); await T.sleep(200);
   var r=await closeRow(); if(!r)return bad.concat('找不到測試那一條').join('；');
   if([].slice.call(r.querySelectorAll('input,textarea')).some(shown))bad.push('沒點開的那一條看得到輸入框(收起時只該是一行字)');
   if(!shown(r.querySelector('summary [data-ansok]')))bad.push('待確認的那一條,收起時看不到 ✓');
   var hv=r.querySelector('summary .ans-hv'); if(!hv||hv.textContent!=='台灣')bad.push('英文答案收起時沒給他看中文('+(hv&&hv.textContent)+')');
   var re=row('zz_empty'); if(!re)bad.push('空白的那一條不見了');
   else{if(re.querySelector('[data-ansok]'))bad.push('答案還空著的那一條也給了 ✓');
     if(!/等你寫/.test(re.querySelector('summary').textContent))bad.push('答案還空著的那一條沒寫「等你寫」');}
   r=await openRow();
   if(!r.open||!shown(r.querySelector('[data-ansf="v"]')))bad.push('點了那一條,沒有展開出能改的欄位');
   var v=r.querySelector('[data-ansf="why"]'); v.dispatchEvent(new Event('change',{bubbles:true})); T.leave(v);   // 觸發一次重畫
   await T.idle();
   r=row(); if(!r||!r.open)bad.push('重畫之後,他展開的那一條被收起來了');
   r=await closeRow();
   if(r.open)bad.push('再點一次,沒有收起來');
   r.querySelector('summary [data-ansok]').click(); await T.idle();
   r=row(); if(!r||r.classList.contains('pend'))bad.push('在收起的那一列直接按 ✓,沒有確認');
   else if(r.open)bad.push('按 ✓ 順便把那一列打開了');
   return await done();
 """, 'ans_cards'),
 ('答案只在答案庫:卡上的表單只剩一行、沒有輸入框;按「去答案庫看」跳到那一條;按一次 ✓,用到它的每張卡一起算過', SEED + r"""
   [A,B].forEach(function(c,i){var l=line(c);
     if(!l)bad.push('第 '+(i+1)+' 張卡沒有表單那一行');
     else if(!/1 條答案等你確認/.test(l.textContent))bad.push('第 '+(i+1)+' 張卡沒說還有 1 條答案等他確認('+l.textContent+')');
     if(c.querySelector('.fm-q,.fm-list,.fm-auto,[data-fmt],[data-fmok]'))bad.push('第 '+(i+1)+' 張卡上還攤著表單欄位(答案只該在答案庫)');});
   var go=line(A)&&line(A).querySelector('[data-ansgo]');
   if(!go)bad.push('卡上沒有「去答案庫看」');
   else{go.click(); await T.sleep(400);
     var d=document.querySelector('#app .ans-d'), r=row();
     if(!d||!d.open)bad.push('按「去答案庫看」,答案庫沒打開');
     else if(!r||!shown(r))bad.push('按「去答案庫看」,看不到那一條');
     else{var y=r.getBoundingClientRect().top; if(y<0||y>innerHeight)bad.push('按「去答案庫看」,沒捲到那一條');}}
   var b0=badge();
   row().querySelector('summary [data-ansok]').click(); await T.idle();
   A=T.card(ids[0]); B=T.card(ids[1]);
   [A,B].forEach(function(c,i){if(!/答案都確認過了/.test((line(c)||{}).textContent||''))bad.push('按一次 ✓ 之後,第 '+(i+1)+' 張卡還在等同一條答案');});
   if(badge()!==b0-1)bad.push('分頁 ⚠ 從 '+b0+' 變成 '+badge()+'(同一條答案兩張卡用,只該少 1 件)');
   var un=document.querySelector('#snack .snack-undo');
   if(!un)bad.push('按了 ✓ 沒有「復原」(手滑按錯要能收回)');
   else{un.click(); await T.idle();
     if(!row().classList.contains('pend'))bad.push('按了復原,那一條沒有回到等你確認');
     if(badge()!==b0)bad.push('按了復原,分頁 ⚠ 沒回到 '+b0);}
   return await done();
 """, 'ans_cards'),
 ('答案庫:他改值就是確認,agent 填好、還沒送出的表單標「雇主網頁待重打」,已投遞的、還沒填過的不標;表單在用的按刪除是「清掉答案」(下一輪 agent 代填),沒人用的真的刪,兩種都能復原', SEED + r"""
   var ad=document.querySelector('#app .ans-d'); if(!ad.open){ad.querySelector('summary').click(); await T.sleep(200);}
   var r=await openRow(); var v=r.querySelector('[data-ansf="v"]'); if(!v)return '展開後找不到答案欄';
   T.type(v,'ROC'); v.dispatchEvent(new Event('change',{bubbles:true})); T.leave(v); await T.idle();
   await T.until(function(){return [T.card(ids[0]),T.card(ids[1])].every(function(c){return /待重打/.test((c&&line(c)||{}).textContent||'');});});
   if(row().classList.contains('pend'))bad.push('他改了值,那一條卻還標著「我推論的」');
   A=T.card(ids[0]); B=T.card(ids[1]);
   [A,B].forEach(function(c,i){if(!/待重打/.test((line(c)||{}).textContent||''))bad.push('第 '+(i+1)+' 張卡沒標「雇主網頁待重打」');});
   var st=await T.state();
   if(((st[ids[2]]||{}).form||{f:[]}).f.some(function(x){return x.refill;}))bad.push('已投遞的表單也被標了待重打');
   // 還沒填過的卡:雇主網頁上沒有舊答案,填的時候照新的填。以前也標,卡上變成「照新答案重填」,一堆卡叫他處理
   if(((st[ids[3]]||{}).form||{f:[]}).f.some(function(x){return x.refill;}))bad.push('agent 還沒填過的表單也被標了待重打');
   var D=T.card(ids[3]); if(D&&/重打|照新答案/.test(D.textContent))bad.push('agent 還沒填過的卡,寫了要照新答案重打/重填');
   if(((st.__ans__||[]).filter(function(e){return e.k==='zz_nat';})[0]||{}).v!=='ROC')bad.push('改的值沒存進答案庫');
   r=await openRow(); var cl=r.querySelector('[data-ansd]');
   if(!cl||!/清掉答案/.test(cl.textContent))bad.push('還沒送出的表單在用的那一條,按鈕沒寫「清掉答案」');
   else{cl.click(); await T.idle();
     // #314:清掉之後不叫他重寫,下一輪 agent 代填;要他處理的清單裡不出現這一條
     var sm=row().querySelector('summary').textContent, sn=(document.querySelector('#snack')||{}).textContent||'';
     if(/等你寫/.test(sm)||!/代填/.test(sm))bad.push('清掉答案後,那一條沒寫下一輪 agent 代填(寫成等你寫)');
     if(/等你重寫|等你寫/.test(sn)||!/代填/.test(sn))bad.push('清掉答案的提示還叫他重寫:'+sn.slice(0,60));
     A=T.card(ids[0]); if(/等你確認/.test((line(A)||{}).textContent||''))bad.push('清掉答案後,卡上還叫他處理那一題');
     var zz=((await T.state()).__ans__||[]).filter(function(e){return e.k==='zz_nat';})[0]||{};
     if(!zz.redo||zz.at||zz.v)bad.push('清掉答案後存的不是「等 agent 代填」:'+JSON.stringify(zz).slice(0,80));
     var cu=document.querySelector('#snack .snack-undo');
     if(!cu)bad.push('清掉答案之後沒有「復原」');
     else{cu.click(); await T.idle();
       if(((await T.state()).__ans__||[]).filter(function(e){return e.k==='zz_nat';})[0].v!=='ROC')bad.push('按了復原,答案沒回來');}}
   document.querySelector('#app [data-ansadd]').click(); await T.sleep(300);
   var q=document.activeElement; if(!q||!q.matches('.ans-q'))return bad.concat('按新增之後,游標沒到新那一條的問題欄').join('；');
   var nk=q.getAttribute('data-ansk'); T.type(q,'zz 測試新增'); T.leave(q); await T.sleep(600);
   var del=row(nk)&&row(nk).querySelector('[data-ansd]');
   if(!del)bad.push('沒人用的那一條沒有刪除');
   else{del.click(); await T.sleep(600);
     if(row(nk))bad.push('按了刪除,那一條還在');
     var un=document.querySelector('#snack .snack-undo');
     if(!un)bad.push('刪掉之後沒有「復原」');
     else{un.click(); await T.sleep(600); if(!row(nk))bad.push('按了復原,那一條沒有回來');
       else{row(nk).querySelector('[data-ansd]').click(); await T.sleep(400);}}}
   document.querySelector('[data-tab="sent"]').click(); await T.sleep(300);
   [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
   await T.sleep(300);
   var C=T.card(ids[2]), cl=C&&line(C);
   if(!cl)bad.push('已投遞的卡看不到表單那一行');
   else{if(!/送出/.test(cl.textContent)||/⚠|待重打/.test(cl.textContent))bad.push('已投遞的卡那一行不對('+cl.textContent+')');
     if(C.querySelector('.ans-t,textarea.fm-ta,.fm-q'))bad.push('已投遞的卡上還攤著表單內容');}
   document.querySelector('[data-tab="ship"]').click();
   return await done();
 """, 'ans_cards'),
 ('答案庫:英文答案展開後中文在前、可以改;他改了中文,英文標「待重翻」、不算他的 ⚠,還沒送出的表單標待重打', SEED + r"""
   var ad=document.querySelector('#app .ans-d'); if(!ad.open){ad.querySelector('summary').click(); await T.sleep(200);}
   var r=await openRow(), z=r.querySelector('[data-ansf="zh"]'), v=r.querySelector('[data-ansf="v"]');
   if(!z||!v)return '英文答案展開後沒有中文、英文兩格';
   if(z.compareDocumentPosition(v)&Node.DOCUMENT_POSITION_PRECEDING)bad.push('中文不在英文前面');
   var b0=badge();
   T.type(z,'中華民國'); z.dispatchEvent(new Event('change',{bubbles:true})); T.leave(z); await T.idle();
   // 卡片那一行是離開輸入框 0.45 秒後才就地重畫(免得吃掉他下一下點擊);等它畫好再看
   await T.until(function(){return [T.card(ids[0]),T.card(ids[1])].every(function(c){return /待重打/.test((c&&line(c)||{}).textContent||'');});});
   r=row();
   if(r.classList.contains('pend'))bad.push('他改了中文,那一條還算在等他');
   if(!/英文待重翻/.test(r.querySelector('summary').textContent))bad.push('改了中文,看不出英文要我重翻');
   if(badge()!==b0-1)bad.push('分頁 ⚠ 從 '+b0+' 變成 '+badge()+'(改中文就是確認,「待重翻」是我的事,不算他的)');
   A=T.card(ids[0]); B=T.card(ids[1]);
   [A,B].forEach(function(c,i){if(!/待重打/.test((line(c)||{}).textContent||''))bad.push('第 '+(i+1)+' 張卡沒標「雇主網頁待重打」');});
   var e=((await T.state()).__ans__||[]).filter(function(x){return x.k==='zz_nat';})[0]||{};
   if(e.zh!=='中華民國'||e.tr!==1||e.inf)bad.push('存下來的不對(zh='+e.zh+' tr='+e.tr+' inf='+e.inf+')');
   return await done();
 """, 'ans_cards'),
 ('答案庫:每條分「共用」和「這缺專用」,按一下切換、可以復原;這缺專用的排在那個職缺底下,標題連到 JD', SEED + r"""
   var ad=document.querySelector('#app .ans-d'); if(!ad.open){ad.querySelector('summary').click(); await T.sleep(200);}
   var dn=document.querySelector('#app .ans-done'); if(dn&&!dn.open){dn.querySelector('summary').click(); await T.sleep(200);}
   var w=row('zz_why'); if(!w)return '找不到這缺專用的測試那一條';
   var chip=w.querySelector('summary [data-anspj]');
   if(!chip||!/這缺專用/.test(chip.textContent))bad.push('這缺專用的那一條沒有標「這缺專用」');
   var g=w.closest('.ans-jgrp'), a=g&&g.querySelector('.ans-qgrp-h a.jd-link');
   if(!a||a.getAttribute('href')!==ids[0])bad.push('這缺專用的那一條沒有排在它的職缺底下、連到那個 JD');
   if(!shown(row('zz_nat').querySelector('summary [data-anspj]')))bad.push('共用的那一條看不到「共用」那顆');
   chip.click(); await T.idle();
   w=row('zz_why');
   if(w.open)bad.push('按那顆切換,順便把那一列打開了');
   if(!/共用/.test(w.querySelector('summary [data-anspj]').textContent)||w.closest('.ans-jgrp'))bad.push('按了之後沒有變成共用');
   var un=document.querySelector('#snack .snack-undo');
   if(!un)bad.push('切換之後沒有「復原」');
   else{un.click(); await T.idle();
     var e=((await T.state()).__ans__||[]).filter(function(x){return x.k==='zz_why';})[0]||{};
     if(e.pj!==1||e.pjw!=='測試用')bad.push('按了復原,沒回到這缺專用和我的理由');}
   return await done();
 """, 'ans_cards'),
 ('代投:答案都確認過才有「✅ 核准送出」;核准後可以復原;核准有效才會送,送出後搬到已投遞並鎖表單;核准後答案一改就作廢', SEED + r"""
   var ab=function(c){return c&&c.querySelector('[data-approve]');};
   if(!document.querySelector('#applybar [data-applyrun="fill"]'))bad.push('可投遞那一頁最上面沒有「讓 agent 填表單」');
   if(ab(A)||ab(B))bad.push('還有答案等他確認,卡上就出現了核准送出');
   var ad=document.querySelector('#app .ans-d'); if(!ad.open){ad.querySelector('summary').click(); await T.sleep(200);}
   row().querySelector('summary [data-ansok]').click(); await T.idle();
   A=T.card(ids[0]); B=T.card(ids[1]);
   if(!ab(A)||!ab(B))return bad.concat('答案都確認過了,卡上還是沒有核准送出').join('；');
   ab(B).click(); await T.sleep(600);
   var un=document.querySelector('#snack .snack-undo');
   if(!un)bad.push('按了核准送出沒有「復原」');
   else{un.click(); await T.sleep(9500);
     var s1=await T.state();
     if((s1[ids[1]]||{}).approve)bad.push('按了復原,核准還在');
     if((s1[ids[1]]||{}).app!=='ship')bad.push('按了復原,還是送出去了');}
   // 核准後有 8 秒可以反悔,之後才真的送出(副本跑 job_fake):等它真的搬到已投遞,不乾等 14 秒
   B=T.card(ids[1]); ab(B).click();
   await T.until(async function(){var b=(await T.state())[ids[1]]||{}; return b.app==='sent'&&(b.form||{}).lock;},25000);
   var s2=await T.state(), b2=s2[ids[1]]||{};
   if(b2.app!=='sent'||!(b2.form||{}).lock)bad.push('核准之後沒有送出、搬到已投遞(app='+b2.app+')');
   await T.resync();
   document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
   [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
   await T.sleep(300); A=T.card(ids[0]);
   if(!ab(A))return bad.concat('第一張卡的核准送出不見了').join('；');
   var aa=ab(A); aa.click(); await T.sleep(400);
   // 不按復原,直接改答案:核准要作廢,時間到了也不能送
   ad=document.querySelector('#app .ans-d'); if(!ad.open){ad.querySelector('summary').click(); await T.sleep(200);}
   var r=await openRow(), v=r.querySelector('[data-ansf="v"]'); T.type(v,'ROC');
   v.dispatchEvent(new Event('change',{bubbles:true})); T.leave(v); await T.idle();
   var res=await fetch('/api/run/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage:'submit',url:ids[0]})});
   if(res.status!==400)bad.push('核准之後答案改過,伺服器還是讓它送出(HTTP '+res.status+')');
   await T.sleep(9000);
   var s3=await T.state();
   if((s3[ids[0]]||{}).app==='sent')bad.push('核准之後答案改過,還是被送出去了');
   if((s3[ids[0]]||{}).ds==='confirmed')bad.push('核准之後答案改過,確認還在');
   A=T.card(ids[0]); if(A&&!/答案改過|確認失效/.test(A.textContent))bad.push('確認送出作廢了,卡上沒說');
   return await done();
 """, 'ans_cards'),
 ('頁面不見了(agent 的 Chrome 關過):卡上、「🚀 填表進度」、要你處理的都不給 👀、不給「要 agent 改」,主按鈕是重填', SEED + r"""
   await T.ds(ids[1],[['page_lost',{issues:['填好的那一頁不見了(agent 的 Chrome 關掉或重開過),要重填']}]]);   // Chrome 關過
   await T.resync();
   document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
   [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
   await T.sleep(300); B=T.card(ids[1]);
   if(!B)return '找不到第二張測試卡';
   if(B.querySelector('a[href^="/api/live?"],[data-livego]'))bad.push('頁面不見了,卡上還有 👀');
   if(B.querySelector('[data-applyfixopen]'))bad.push('頁面不見了,卡上還能叫 agent 在那一頁改');
   if(!B.querySelector('[data-runone^="apply|"]')&&!/自動重填/.test(B.textContent))bad.push('頁面不見了,卡上沒有重填(也沒寫會自動重填)');
   var fl=document.getElementById('filllistbar');
   if(fl&&[].slice.call(fl.querySelectorAll('[data-livego]')).some(function(x){return x.getAttribute('data-livego')===ids[1];}))bad.push('「🚀 填表進度」上頁面不見了的那張還有 👀');
   if([].slice.call(document.querySelectorAll('.todo-why [data-livego]')).some(function(x){return x.getAttribute('data-livego')===ids[1];}))bad.push('要你處理的那一條還有 👀');
   return await done();
 """, 'ans_cards'),
 ('代投修改:agent 填好的卡有「👀 看現在的頁面」和「✏️ 要 agent 改」;答案改過、網頁待重打時藏起核准;寫一句話交給同一隻 agent,改好了才又能核准', SEED + r"""
   var ab=function(c){return c&&c.querySelector('[data-approve]');};
   var ad=document.querySelector('#app .ans-d'); if(!ad.open){ad.querySelector('summary').click(); await T.sleep(200);}
   row().querySelector('summary [data-ansok]').click(); await T.idle();
   // 第二張:agent 填好了(有那段對話的 id),之後他改了答案,網頁待重打。對話 id 只能靠填表那一輪記(事件):重填一次
   var rec={stage:'fill',at:'2026-01-01T00:00:00',issues:[],tab_id:'1',session:'S-test',where:'chrome',delivery:{method:'direct_upload'}};
   await T.ds(ids[1],[['page_lost',{issues:['x']}],['fill_start',{apply:{stage:'fill',at:rec.at,issues:[]}}],['fill_ok',{apply:rec}]]);
   var s0=await T.state(), m=s0[ids[1]];
   // 答案改了:看板送 {refill:答案鍵},伺服器在鎖內標在現在那份表單上(表單只有後台寫,#308)
   var body={__rev__:1,__events__:[{refill:m.form.f[0].k}]};
   await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   await T.resync();
   document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
   [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
   await T.sleep(300); B=T.card(ids[1]);
   if(!B)return '找不到第二張測試卡';
   if(!B.querySelector('a[href^="/api/live?"]'))bad.push('agent 填好的卡沒有「👀 看現在的頁面」');
   if(ab(B))bad.push('網頁待重打,卡上還是有核准送出(他會核准到跟答案庫不一樣的那一頁)');
   var fo=B.querySelector('[data-applyfixopen]');
   if(!fo||!/1 欄/.test(fo.textContent))return bad.concat('沒有「✏️ 要 agent 改(1 欄…)」').join('；');
   fo.click(); await T.sleep(300); B=T.card(ids[1]);
   var note=B.querySelector('[data-applynote]');
   if(!shown(note))return bad.concat('按了「要 agent 改」看不到寫話的格子').join('；');
   note.value='電話少一碼'; B.querySelector('[data-applyfix]').click();
   await T.until(async function(){var b=(await T.state())[ids[1]]||{};
     return (b.apply||{}).stage==='fix'&&!((b.form||{}).f||[]).some(function(x){return x.refill;});},25000);
   await T.resync();
   var s1=await T.state(), b1=s1[ids[1]]||{};
   if(((b1.apply||{}).stage)!=='fix')bad.push('交給 agent 之後沒有跑修改(apply.stage='+((b1.apply||{}).stage)+')');
   if((b1.form.f||[]).some(function(x){return x.refill;}))bad.push('改好了,待重打的標記還在');
   // 資料先寫進看板、那一輪才收尾;頁面要等「正在改」結束才會放出核准鈕。等它,不乾等 9 秒。
   await T.until(async function(){
     document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
     [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
     await T.sleep(300); B=T.card(ids[1]); if(ab(B))return true;
     await T.resync(); return false;},20000);
   if(!ab(B))bad.push('agent 改好了,卡上沒有回到可以核准');
   var r=await fetch('/api/run/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage:'fix',url:ids[0],note:'x'})});
   if(r.status!==400)bad.push('沒有 agent 填過的卡也讓它跑修改(HTTP '+r.status+';叫不回同一隻 agent)');
   return await done();
 """, 'ans_cards'),
 ('每一頁只放那一階段的東西:找新職缺只在找職缺的分頁;已投遞有「📬 查回音」;可投遞「要你處理的」點名字會跳到那張卡', SEED + r"""
   var vis=function(id){var el=document.getElementById(id); return !!el&&el.style.display!=='none'&&el.offsetHeight>0;};
   var go=async function(t){document.querySelector('[data-tab="'+t+'"]').click(); await T.sleep(350);};
   await go('none'); if(!vis('findbar'))bad.push('待評估那頁看不到找新職缺');
   await go('sent'); if(vis('findbar'))bad.push('已投遞那頁還有找新職缺');
   if(vis('cutbar'))bad.push('已投遞那頁還有切角');
   if(!document.querySelector('#replybar [data-replyrun]'))bad.push('已投遞那頁沒有「📬 查回音」');
   await go('ship'); if(vis('findbar'))bad.push('可投遞那頁還有找新職缺');
   var li=document.querySelector('#app .todo [data-todogo]');
   if(!li)bad.push('可投遞有答案等他確認,卻沒有「這一頁要你處理的」');
   else{var id=li.getAttribute('data-todogo'); li.click(); await T.sleep(500);
     var card=document.querySelector('article[data-fid="'+CSS.escape(id)+'"]');
     if(!card||!shown(card))bad.push('點「要你處理的」那一條,沒有跳到那張卡');}
   return await done();
 """, 'ans_cards'),
 ('agent 回報:每一頁最上面、預設收起,標題亮待處理件數;每一頁都列全部(這一頁有關的排前面,別頁的有「去那一頁」);按「處理好了」會收進已處理,可以復原', r"""
   await T.idle();
   var jobs=JSON.parse(document.getElementById('data-jobs').textContent).jobs.filter(function(j){return !j.bk;});
   var box=[{id:'rzz1',at:'2026-01-01T09:00:00',from:'代投',msg:'測試:被網站的機器人驗證擋住 '+Date.now(),need:'測試用',n:1},   // 代投的回報屬於可投遞那一頁
            // agent 自己寫、沒有程式自己截的那一頁:列出來但標缺證據,不算要他處理、不寫「你要做的」(#315)
            {id:'rzz2',at:'2026-01-01T09:01:00',from:'查回音',msg:'測試:信箱要登入 '+Date.now(),need:'去登入信箱',n:1,agent:true,noev:'這一輪沒有程式自己截的那一頁'}];
   await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({__rev__:1,__inbox__:box})});
   await T.resync();
   document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
   var bad=[], d=document.querySelector('#inboxbar details.inbox-d');
   if(!d)return '頁面最上面看不到 agent 回報';
   if(d.open)bad.push('agent 回報預設是開的');
   if(!/1 件要你處理/.test(d.querySelector('summary').textContent))bad.push('標題看不出有 1 件要他處理(缺證據的那則不算)');
   if(!/1 件缺證據/.test(d.querySelector('summary').textContent))bad.push('標題沒寫有 1 件缺證據');
   var r2=document.querySelector('#inboxbar [data-ib="rzz2"]');
   if(!r2)bad.push('缺證據的回報沒列出來');
   else{if(/你要做的/.test(r2.querySelector('summary').textContent))bad.push('缺證據的回報還寫「你要做的」');
     if(!/缺證據/.test(r2.querySelector('summary').textContent))bad.push('缺證據的回報沒標缺證據');}
   document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
   // 要他處理的事不能藏在別的分頁:待評估那頁也要看得到代投的回報,而且有一顆直接去「可以投了」
   var dn=document.querySelector('#inboxbar details.inbox-d');
   if(!dn||!/1 件要你處理/.test(dn.querySelector('summary').textContent))bad.push('在待評估那頁看不到代投的回報(要點到可以投了才看得到)');
   else{dn.querySelector('summary').click(); await T.sleep(200);
     var rn=document.querySelector('#inboxbar [data-ib="rzz1"]'); if(rn){rn.querySelector('summary').click(); await T.sleep(150);}
     var go=rn&&rn.querySelector('[data-inboxtab="ship"]');
     if(!go)bad.push('別頁的回報沒有「去可以投了」');
     else{go.click(); await T.sleep(300); if(!document.querySelector('#tabs [data-tab="ship"].on'))bad.push('按了「去那一頁」沒有到可以投了');}}
   document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
   if(!document.querySelector('#inboxbar details.inbox-d'))bad.push('回到可投遞,agent 回報不見了');
   d=document.querySelector('#inboxbar details.inbox-d'); d.querySelector('summary').click(); await T.sleep(200);
   var r=document.querySelector('#inboxbar [data-ib="rzz1"]'); if(!r)return bad.concat('找不到那一則回報').join('；');
   r.querySelector('summary').click(); await T.sleep(200);
   r.querySelector('[data-inboxdone]').click(); await T.idle();
   var st=await T.state(), it=(st.__inbox__||[]).filter(function(x){return x.id==='rzz1';})[0]||{};
   if(!it.done)bad.push('按了處理好了,沒有存下來');
   var un=document.querySelector('#snack .snack-undo');
   if(!un)bad.push('處理好了沒有「復原」');
   else{un.click(); await T.idle(); st=await T.state(); it=(st.__inbox__||[]).filter(function(x){return x.id==='rzz1';})[0]||{};
     if(it.done)bad.push('按了復原,還是標成處理好了');}
   await T.idle();
   return bad.join('；');
 """),
]


CHECKS=CHECKS+ANS_CHECKS

def external_write(board):
    """模擬 cut_tailor/custom_queue 那種不經過伺服器、直接寫檔的改動。"""
    import board_doc as bd
    d=bd.load(board); fb=json.loads(d['fb'])
    # 挑一張還沒標過的;前面的檢查可能把每一張都碰過(留下空的標記),那就挑一張不是 grow、不在管線裡的
    fid=next((j['id'] for j in d['data']['jobs'] if not fb.get(j['id'])),None) or \
        next(j['id'] for j in d['data']['jobs']
             if (fb.get(j['id']) or {}).get('s')!='grow' and not (fb.get(j['id']) or {}).get('app'))
    bd.set_fb(lambda f: f.__setitem__(fid,{'s':'grow'}), live=board)
    return {'fid':fid}

VERIFY_CHECKS=[
 ('驗收重跑之後頁面自己跟上,沒過的原因直接寫在卡上',"""
   if(!P.fid)return '';
   await T.resync();
   document.querySelector('[data-tab="ready"]').click();
   var hs=document.querySelectorAll('#app .cohead'), c=null;
   for(var i=0;i<hs.length&&!c;i++){hs[i].click(); c=T.card(P.fid); if(!c)hs[i].click();}
   var bad=[];
   if(!c)bad.push('找不到那張卡');
   else{var b=c.querySelector('.stage-blocked');
     if(!b)bad.push('驗收重跑出了問題,卡上沒顯示(頁面停在舊的驗收結果)');
     else if(b.textContent.indexOf('測試用的原因')<0)bad.push('卡上只寫驗收未通過,看不到原因');}
   document.querySelector('[data-tab="none"]').click();
   return bad.join('；');
 """, 'status_issue'),
 ('無法確認的連結不能算已下架一鍵移除',"""
   if(!P.sid)return '找不到第二張待決假卡';
   await T.resync();
   document.querySelector('[data-tab="ready"]').click(); await T.sleep(200);
   var bad=document.querySelector('[data-closedrm="ready"]');
   document.querySelector('[data-tab="none"]').click();
   return bad?'soft 連結提示被當成確定下架的一鍵移除候選':'';
 """, 'status_issue'),
]
# 分頁列捲下去會縮成一行。縮了頁面變矮 → 瀏覽器的捲動錨定把位置往回推 → 又判成該展開 → 又變高……
# 頁面只比畫面長一點點時(例如打開的選單超出畫面底部)一秒來回 60 次,整排在最上面狂閃。
SCROLL_CHECKS=[
 ('分頁列縮起來不改頁面高度:頁面只比畫面長一點點時捲下去也不會來回閃',"""
   document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
   window.scrollTo(0,0); document.body.classList.remove('scrolled'); await T.sleep(200);
   var pad=document.createElement('div'); document.getElementById('app').appendChild(pad);
   var room=document.documentElement.scrollHeight-window.innerHeight; pad.style.height=Math.max(0,280-room)+'px';
   await T.sleep(200);
   var h0=document.documentElement.scrollHeight;
   var n=0, mo=new MutationObserver(function(){n++;}); mo.observe(document.body,{attributes:true,attributeFilter:['class']});
   window.scrollTo(0,200); await T.sleep(1000); mo.disconnect();
   var bad=[];
   if(n>2)bad.push('捲到 200 之後 1 秒內縮放切換了 '+n+' 次');
   if(!document.body.classList.contains('scrolled'))bad.push('捲到 200 分頁列沒有縮起來');
   var h1=document.documentElement.scrollHeight;
   if(Math.abs(h1-h0)>1)bad.push('分頁列縮起來頁面高度從 '+h0+' 變成 '+h1);
   pad.remove(); window.scrollTo(0,0); document.body.classList.remove('scrolled'); await T.sleep(200);
   return bad.join('；');
 """),
]
CHECKS=CHECKS+SCROLL_CHECKS

# 職缺名稱、彈藥、備註是 agent 從職缺頁讀來的:網頁裡夾的 HTML 不能在看板上變成真的元素、跑起來
def xss_job(board):
    import board_doc as bd
    evil = 'https://jobs.example.test/xss-probe'
    def mut(data, _fb):
        data['jobs'] = [j for j in data['jobs'] if j.get('id') != evil] + [{
            'id': evil, 'target': '<img src=x onerror="window.__xss=1">危險職缺 · Probe Co',
            'ammo': '<img src=x onerror="window.__xss=2">', 'note': '[點我](javascript:window.__xss=3) <b onmouseover=x>粗</b>',
            'chan': '官方', 'bk': False, 'dead': False, 'added': __import__('datetime').date.today().isoformat(),
            'sum': {'fit': '檢查用'}}]
    bd.set_data(mut, live=board)
    return {'fid': evil}

XSS_CHECKS=[
 ('職缺頁讀來的字不會在看板上變成程式(名稱、彈藥、備註裡的 HTML 照字顯示)',"""
   await T.resync();
   document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
   var hs=document.querySelectorAll('#app .cohead'), c=null;
   for(var i=0;i<hs.length&&!c;i++){if(!hs[i].parentNode.open){hs[i].click(); await T.sleep(50);} c=T.card(P.fid);}
   var bad=[];
   if(!c)return '找不到那張測試卡';
   if(window.__xss)bad.push('卡上的 HTML 被執行了(__xss='+window.__xss+')');
   if(c.querySelector('img[onerror],b[onmouseover]'))bad.push('卡上出現職缺頁夾帶的 HTML 元素');
   if(c.querySelector('a[href^="javascript"]'))bad.push('備註裡的 javascript: 連結變成可以點');
   if(c.textContent.indexOf('<img')<0)bad.push('職缺名稱裡的字沒有照原樣顯示');
   document.querySelector('[data-tab="none"]').click();
   return bad.join('；');
 """, 'xss_job'),
]
CHECKS=CHECKS+XSS_CHECKS
CHECKS=CHECKS+VERIFY_CHECKS

# 跑準備區:副本上跑的是 job_fake(照真的格式寫進度、最後把準備區的卡推到待你決定),
# 不會派 agent。放最後:它會把副本上整個準備區清空。
PREP_CHECKS=[
 ('跑準備區:按了看得到在跑,跑完卡片自己到「待你決定」、告訴他,不用重整',"""
   document.querySelector('[data-tab="prep"]').click();
   var b=document.querySelector('#prepbar [data-prep]'); if(!b)return '準備區最上面沒有按鈕';
   var fb=FB0(), ids=Object.keys(fb).filter(function(k){return fb[k]&&fb[k].app==='prep'&&!fb[k].rm;});
   if(!ids.length){document.querySelector('[data-tab="none"]').click(); return '';}
   b.click(); await T.until(function(){return /正在跑|準備開跑|跑準備區中/.test(document.getElementById('prepbar').textContent);});
   var bad=[];
   if(!/正在跑|準備開跑|跑準備區中/.test(document.getElementById('prepbar').textContent))bad.push('按了看不出在跑');
   if(!document.querySelector('[data-tab="prep"] .ivn'))bad.push('分頁上看不出在跑');
   var b2=document.querySelector('#prepbar [data-prep]'); if(b2&&!b2.disabled)bad.push('跑的時候還按得下去(按兩下會把跑到一半的砍掉)');
   for(var i=0;i<80;i++){await T.sleep(500); if(/上一輪/.test((document.getElementById('prepbar')||{}).textContent||''))break;}
   await T.sleep(800);
   var fb2=FB0(), moved=ids.filter(function(k){return (fb2[k]||{}).app==='ready';}).length;
   if(moved!==ids.length)bad.push('跑完頁面上只有 '+moved+'/'+ids.length+' 張到待你決定(要重整才看得到)');
   if(!/履歷準備好了/.test(T.snack()))bad.push('跑完沒告訴他');
   document.querySelector('[data-tab="none"]').click();
   return bad.join('；');
 """),
]

def status_issue(board):
    """模擬投遞前驗收重跑之後多了一個問題(board_status --write 也是走看板檔唯一的寫入)。"""
    import board_doc as bd
    def put(d):
        fb=d['fb']
        ready=[j['id'] for j in d['data']['jobs'] if (fb.get(j['id']) or {}).get('app')=='ready']
        fid=ready[0] if ready else None
        sid=ready[1] if len(ready)>1 else None
        if not fid: return bd.SKIP
        st=dict(d['data'].get('status') or {},schema_version=2,checked_links=True)
        st['issues']=[x for x in st.get('issues') or [] if x.get('jid') not in ready]+[
            {'jid':fid,'t':'','stage':'ready','msg':'測試用的原因'}]
        if sid:
            st['issues'].append({'jid':sid,'kind':'unverified','soft':True,'stage':'ready',
                                 'msg':'無法確認是否已下架'})
        d['data']['status']=st
        return {'fid':fid,'sid':sid}
    got=bd.rewrite(put,board,by='board_check')
    return {} if got is bd.SKIP else got

# 找新職缺:副本上跑的是 job_fake(照真的格式寫進度、在副本加兩筆假職缺),不會派 agent。
FIND_CHECKS=[
 ('找新職缺:打一句方向按下去,看得到在找,找完新缺自己出現在待評估、告訴他,不用重整',"""
   var d=document.getElementById('finddet'); if(!d)return '頁首沒有找新職缺';
   d.open=true; await T.sleep(400);          // 預設收起來,要先展開才按得到
   var n0=JSON.parse(document.getElementById('data-jobs').textContent).jobs.length;
   var tab=document.querySelector('[data-tab="none"]'), c0=+(tab.querySelector('.n')||{}).textContent||0;
   var ta=document.getElementById('find-dir'); T.type(ta,'測試方向');
   var findNotice='';
   var noticeObserver=new MutationObserver(function(){
     var el=document.getElementById('snack');
     if(el&&el.classList.contains('on')&&/找缺完成/.test(el.textContent))findNotice=el.textContent;
   });
   noticeObserver.observe(document.body,{attributes:true,childList:true,characterData:true,subtree:true});
   document.querySelector('#findrow [data-find="dir"]').click();
   var stage=await T.until(function(){
     var st=document.getElementById('find-st').textContent;
     if(/找缺完成/.test(T.snack()))findNotice=T.snack();
     if(/正在找/.test(st))return {running:true,text:st};
     var count=+(document.querySelector('[data-tab="none"] .n')||{}).textContent||0;
     if(/上一輪/.test(st)&&count===c0+2)return {running:false,text:st,count:count};
     return false;
   },20000);
   var bad=[];
   if(!stage)bad.push('按下後,期限內沒有看到找缺進度或完成結果');
   else if(stage.text.indexOf('測試方向')<0)bad.push('看不出是照哪個方向在找');
   var active=document.getElementById('find-st').textContent;
   if(stage&&/正在找/.test(active)){
     var b=document.querySelector('#findrow [data-find="deep"]'); if(b&&!b.disabled)bad.push('找的時候還按得下去');
   }
   var result=await T.until(function(){
     var st=document.getElementById('find-st').textContent, snack=T.snack();
     if(/找缺完成/.test(snack))findNotice=snack;
     var count=+(document.querySelector('[data-tab="none"] .n')||{}).textContent||0;
     return /上一輪/.test(st)&&count===c0+2&&findNotice ? {text:st,count:count} : false;
   },20000);
   noticeObserver.disconnect();
   if(!result)result={text:document.getElementById('find-st').textContent,
     count:+(document.querySelector('[data-tab="none"] .n')||{}).textContent||0};
   if(!/上一輪/.test(result.text))bad.push('找缺結果沒有在期限內出現('+result.text+')');
   var c1=result.count;
   if(c1!==c0+2)bad.push('待評估從 '+c0+' 變成 '+c1+'(應該多 2 筆,頁面沒跟上)');
   if(!/找缺完成/.test(findNotice||T.snack()))bad.push('找完沒告訴他(狀態:'+result.text+';目前提示:'+T.snack()+')');
   return bad.join('；');
 """),
]

FIND_CHECKS.append(
 ('找新職缺:「__ 分鐘」格子預設 15、清空是不限時、改了存進設定,按找缺這輪帶著那個分鐘數,跑完講找到/清掉/進看板',"""
   var bad=[], d=document.getElementById('finddet'); if(!d)return '頁首沒有找新職缺';
   d.open=true; await T.sleep(400);
   var fm=document.querySelector('#findrow input[data-findmin]'); if(!fm)return '找缺那一列沒有「__ 分鐘」格子';
   if(fm.value!=='15')bad.push('新使用者預設應該是 15 分鐘,格子是「'+fm.value+'」');
   if(fm.placeholder!=='不限時')bad.push('格子空著的淡字應該是「不限時」,是「'+fm.placeholder+'」');
   // 跑完一輪找缺那一列會重畫:每次都重新找格子,不拿舊的
   function q(){return document.querySelector('#findrow input[data-findmin]');}
   async function setMin(v){fm=q(); fm.value=v; fm.dispatchEvent(new Event('change',{bubbles:true})); await T.sleep(600);
     var st=await fetch('/api/settings').then(function(r){return r.json();});
     return (st.effective.search||{}).find_minutes;}
   var got=await setMin('');
   if(got!==0)bad.push('清空之後設定應該存成 0(不限時),是 '+JSON.stringify(got));
   got=await setMin('20');
   if(got!==20)bad.push('改成 20 之後設定是 '+JSON.stringify(got));
   var before=T.snack(); got=await setMin('-3');
   if(got!==20)bad.push('填了 -3 不該存,設定變成 '+JSON.stringify(got));
   if(!/1~999/.test(T.snack()))bad.push('填了 -3 沒講要寫 1~999(提示:'+T.snack()+')');
   // 前一條檢查已經跑過一輪:畫面上本來就寫著「上一輪…」。記下那一輪的結束時間,等到新的一輪結束才算
   var rev=function(){return fetch('/api/rev').then(function(r){return r.json();});};
   var prev=((await rev()).research||{}).finished_at||0, rv=null;
   document.querySelector('#findrow [data-find="deep"]').click();
   for(var i=0;i<100&&!rv;i++){await T.sleep(200); var x=await rev(), r0=x.research||{};
     if(r0.phase==='done'&&(r0.finished_at||0)>prev)rv=x;}
   if(!rv){bad.push('找缺沒有在期限內跑完'); rv=await rev();}
   await T.until(function(){return /上一輪/.test(document.getElementById('find-st').textContent);},8000);
   var result=document.getElementById('find-st').textContent;
   // 副本上的假找缺跑得很快,「正在找… / 最多找 N 分鐘」那一下常常看不到;直接看這輪的進度帶了幾分鐘
   if((rv.research||{}).minutes!==20)bad.push('這輪沒有帶著格子的 20 分鐘出發(進度裡是 '+JSON.stringify((rv.research||{}).minutes)+')');
   else if(!/找到 3 張、清掉 1 張、進看板 2 張/.test(result))bad.push('跑完沒照實講三個數字:'+result);
   // 填完馬上按(不等存設定):這輪也要帶著剛填的值
   prev=((await rev()).research||{}).finished_at||0; rv=null;
   fm=q(); fm.value='7'; fm.dispatchEvent(new Event('change',{bubbles:true}));
   document.querySelector('#findrow [data-find="deep"]').click();
   for(var k=0;k<100&&!rv;k++){await T.sleep(200); var y=await rev(), r1=y.research||{};
     if(r1.phase==='done'&&(r1.finished_at||0)>prev)rv=y;}
   if(!rv||(rv.research||{}).minutes!==7)bad.push('填完馬上按,這輪沒帶著剛填的 7 分鐘(進度裡是 '+JSON.stringify(rv&&(rv.research||{}).minutes)+')');
   await setMin('15');
   return bad.join('；');
 """))

FIND_CHECKS.append(
 ('每顆派 agent 的按鈕旁邊都有「📝 prompt」,點開是彈窗、不是把版面撐開',"""
   var bad=[];
   var fd2=document.getElementById('finddet'); if(fd2)fd2.open=true; await T.sleep(400);
   var b=document.querySelector('#findrow [data-pvopen="find"]');
   if(!b)return '找新職缺那條沒有 📝 prompt';
   b.click(); await T.until(function(){var p=document.getElementById('pv-pre'); return p&&p.textContent.length>=200;});
   var ov=document.getElementById('rzmodal');
   if(!ov||ov.style.display==='none')bad.push('點了沒開彈窗');
   // 直接看 prompt 在哪:要在蓋在上面(position:fixed)的彈窗裡,不能塞進頁面。
   // 以前量整頁高度前後比,背景剛好有別的東西重畫(前一條觸發的找缺跑完)就誤判,偶發失敗(#210)
   var pv=document.getElementById('pv-pre');
   if(pv&&(!pv.closest('#rzmodal')||getComputedStyle(ov).position!=='fixed'||pv.closest('#app')))bad.push('把版面撐開了(應該蓋在上面)');
   var pre=document.getElementById('pv-pre');
   if(!pre||pre.textContent.length<200)bad.push('彈窗裡看不到 prompt 內容');
   else if(pre.textContent.indexOf('抓網頁鐵律')<0)bad.push('顯示的不是真的會送出去的那份');
   var tabs=ov?ov.querySelectorAll('[data-pvtab]').length:0;
   if(tabs<4)bad.push('找缺的四份(更深/更廣/方向/判斷)沒有都在('+tabs+')');
   var t2=ov&&ov.querySelector('[data-pvtab="judge"]');
   if(t2){t2.click(); await T.idle();
     var p2=document.getElementById('pv-pre');
     if(!p2||p2.textContent.length<200)bad.push('切到「逐張判斷」看不到內容');
     // 「這份不是最終樣子」要在最上面看得到,不能是灰字埋在最底下(他因此誤會過)
     var n=document.getElementById('pv-note');
     if(!n||n.style.display==='none'||!n.textContent.trim())bad.push('骨架那份沒講清楚後面還會接東西');
     else if(p2&&n.compareDocumentPosition(p2)!==Node.DOCUMENT_POSITION_FOLLOWING)bad.push('說明沒放在 prompt 上面');}
   var x=ov&&ov.querySelector('.rzm-x'); if(x)x.click(); else bad.push('沒有關掉的按鈕');
   await T.sleep(400);
   if(ov&&ov.style.display!=='none')bad.push('關不掉');
   return bad.join('；');
 """))


SAVE_CHECKS.append(
 ('篩選那一排:只有兩種形狀,而且每一顆按下去都真的有反應',"""
   // 同一個病根踩過好幾次:控制項畫在 #app 裡,處理它的監聽器卻掛在別的容器上,
   // 按了不會報錯、就是沒反應。這條把「按了有沒有事發生」變成會失敗的檢查。
   document.querySelector('[data-tab="all"]').click(); await T.sleep(700);
   var bar=document.querySelector('.ctlbar'); if(!bar)return '找不到那一排控制項';
   var bad=[], kinds={};
   [].slice.call(bar.children).forEach(function(c){kinds[c.className]=1;});
   var ks=Object.keys(kinds).sort();
   if(ks.join(',')!=='ctl'&&ks.join(',')!=='ctl,ctl-act')
     bad.push('這一排出現第三種形狀:'+ks.join('、'));
   // 每一顆篩選:點開必須有選單
   var one=bar.querySelector('[data-ctl]');
   if(one){one.click(); await T.sleep(300);
     if(!document.getElementById('ctlmenu'))bad.push('篩選點了沒展開');
     one.click(); await T.sleep(200);}
   // 動作那幾顆:按下去畫面一定要變(換分頁或清掉條件)
   var act=bar.querySelector('[data-gotab]');
   if(act){var t0=(document.querySelector('.tab.on')||{}).textContent;
     act.click(); await T.sleep(800);
     if((document.querySelector('.tab.on')||{}).textContent===t0)bad.push('「已封鎖」按了沒反應');
     document.querySelector('[data-tab="all"]').click(); await T.sleep(500);}
   return bad.join('；');
 """))

SAVE_CHECKS.append(
 ('找新職缺面板裡每一顆都真的有反應(不是按了沒事)',"""
   // 這幾顆長在 #findbar,處理器如果掛在 #app 上,按了完全沒反應;
   // 開關又用 delete 關掉,而存檔只送還存在的 key,所以永遠存不回去。兩種都看不出錯誤。
   var d=document.getElementById('finddet'); if(!d)return '頁首沒有找新職缺';
   d.open=true; await T.sleep(500);
   var bad=[];
   var sw=d.querySelector('[data-afree]'); if(!sw)return '沒有 agent 數量的開關';
   var a0=(await T.state())['__agentfree__'];
   sw.click(); await T.idle();
   var a1=(await T.state())['__agentfree__'];
   if(String(a0||0)===String(a1||0))bad.push('agent 開關按了存不進去('+a0+'→'+a1+')');
   sw.click(); await T.idle();
   // 指名要找的:加一個、再刪掉,兩邊都要真的動到
   var h=document.querySelectorAll('#app .cohead')[0]; if(h){h.click(); await T.sleep(600);
     var add=h.parentNode.querySelector('[data-coseed]');
     if(add){add.click(); await T.idle();
       var n1=((await T.state())['__seeds__']||[]).length;
       if(!n1)bad.push('「找這家更多」按了沒加進清單');
       var d2=document.getElementById('finddet');   // 加完會整頁重畫,原本那個節點已經被換掉
       if(d2){d2.open=true;} await T.sleep(500);
       var del=document.querySelector('#findrow [data-seeddel]');
       if(!del)bad.push('清單上沒有拿掉的按鈕(加進去之後那一條沒有重畫?)');
       else{del.click(); await T.idle();
         if((((await T.state())['__seeds__'])||[]).length>=n1)bad.push('指名要找的刪不掉');}}
     h.click();}
   return bad.join('；');
 """))

SAVE_CHECKS.append(
 ('「待你決定」一鍵全變可投遞:只推通過驗收的,沒過的一張都不准動,可以復原',"""
   document.querySelector('[data-tab="ready"]').click(); await T.sleep(900);
   var bar=document.getElementById('readybar'); if(!bar)return '「待你決定」沒有那條 bar';
   var b=bar.querySelector('[data-readyall]'); if(!b)return '沒有「全部變成可投遞」';
   var st0=await T.state(), bad=[];
   var ready0=Object.keys(st0).filter(function(k){return st0[k]&&st0[k].app==='ready';});
   if(b.disabled)return '';                       // 這板子上沒有過關的卡,不算錯
   b.click(); await T.idle();
   var st1=await T.state();
   var moved=ready0.filter(function(k){return st1[k]&&st1[k].app==='ship';});
   if(!moved.length)bad.push('按了一張都沒動');
   // 沒過驗收的不准被推走
   var iss={}, D0=JSON.parse(document.getElementById('data-jobs').textContent);
   ((D0.status||{}).issues||[]).forEach(function(x){iss[x.jid]=1;});
   moved.forEach(function(k){if(iss[k])bad.push('把沒過驗收的推進可投遞了');});
   var u=document.querySelector('#snack .snack-undo'); if(!u)return (bad.join('；')||'')+'；沒有復原';
   u.click(); await T.idle();
   var st2=await T.state();
   moved.forEach(function(k){if(!st2[k]||st2[k].app!=='ready')bad.push('復原後沒回到「待你決定」');});
   // 這條動了很多張卡,存檔是延遲送出的。不等它落地就交給下一條檢查,
   // 下一條的存檔會撞版本衝突被擋掉,看起來像那條壞了(這樣誤判過)。
   for(var w=0;w<12;w++){await T.sleep(500);
     if(!document.querySelector('#savebar.dirty')&&!document.querySelector('.fb-st.dirty'))break;}
   await T.sleep(800);
   return bad.join('；');
 """))

SAVE_CHECKS.append(
 ('封鎖一家之後,「🚫 已封鎖」點開的封鎖名單看得到那幾張缺,解得開,真的清掉是另一顆按鈕',"""
   document.querySelector('[data-tab="all"]').click(); await T.sleep(600);
   T.open(0); await T.sleep(600);
   var grp0=document.querySelector('#app details.cogrp[open]'); if(!grp0)return '沒有展開的公司';
   var mb0=grp0.querySelector('[data-omore]'); if(!mb0)return '公司列沒有 ⋯';
   mb0.click(); await T.sleep(300);
   var b=grp0.querySelector('.more-m [data-block]'); if(!b)return '⋯ 裡看不到封鎖';
   var co=b.getAttribute('data-block');
   b.click(); await T.idle();
   // 封鎖名單在篩選列「🚫 已封鎖 N 家」點開的小視窗裡(已移除那一頁只放移除的卡片)
   var chip=document.querySelector('[data-blocklist]'); if(!chip)return '封鎖之後篩選列沒有「🚫 已封鎖」';
   chip.click(); await T.sleep(600);
   var bad=[];
   // 他板上本來就可能有封鎖過的公司,要找的是「剛剛封鎖的那一家」那一組,不是第一組
   var grp=[].slice.call(document.querySelectorAll('#rzmodal .blkgrp')).filter(function(g){
     return (g.querySelector('.coname')||{}).textContent===co;})[0];
   if(!grp){var ub0=document.querySelector('#rzmodal [data-unblock="'+co+'"]'); if(ub0)ub0.click(); else if(typeof toggleBlock==='function')toggleBlock(co);
     return '封鎖名單看不到剛封鎖的那家('+co+')';}
   grp.open=true; await T.sleep(600);
   if(!grp.querySelector('[data-unblock]'))bad.push('沒有解除封鎖的按鈕');
   if(!grp.querySelector('[data-blockdel]'))bad.push('沒有另一顆「真的清掉」');
   if(!grp.querySelector('article'))bad.push('點開看不到這家是哪幾張缺(等於憑空消失)');
   var ub=grp.querySelector('[data-unblock]'); if(ub){ub.click(); await T.idle();}
   var st=await T.state();
   if((st['__block__']||[]).indexOf(co)>=0)bad.push('解除封鎖沒存進去');
   var x=document.querySelector('#rzmodal .rzm-x'); if(x)x.click();
   return bad.join('；');
 """))

SAVE_CHECKS.append(
 ('公司列「全部進準備區」:一次丟進去、存得住,已經在管線裡的不動,可以復原',"""
   document.querySelector('[data-tab="all"]').click(); await T.sleep(600);
   T.open(0); await T.sleep(700);
   var head=document.querySelector('#app summary.cohead'); if(!head)return '找不到公司列';
   var bad0=[];
   // 公司層級的動作一律在 ⋯ 裡:誤點 ⋯ 只是開選單,代價是零;攤在外面誤點就整家不見了
   var mb=head.querySelector('[data-omore]'); if(!mb)return '公司列沒有 ⋯';
   [].slice.call(head.querySelectorAll('button')).forEach(function(x){
     if(!x.hasAttribute('data-omore')&&!x.closest('.more-m'))
       bad0.push('公司列上又出現攤在外面的動作:'+x.textContent.trim());});
   mb.click(); await T.sleep(300);
   var b=head.parentNode.querySelector('.more-m [data-coprep]');
   if(!b)return (bad0.join('；')||'')+'；⋯ 裡看不到「全部進準備區」';
   var st0=await T.state();
   var before=Object.keys(st0).filter(function(k){return st0[k]&&st0[k].app;}).length;
   b.click(); await T.idle();
   var st1=await T.state(), bad=[];
   var after=Object.keys(st1).filter(function(k){return st1[k]&&st1[k].app==='prep';}).length;
   if(after<=0)bad.push('按了沒有任何一張進準備區');
   // 已經在管線裡的不准被拉回來
   Object.keys(st0).forEach(function(k){
     var a=st0[k]&&st0[k].app;
     if(a&&a!=='prep'&&st1[k]&&st1[k].app==='prep')bad.push('把已經在「'+a+'」的卡拉回準備區了');});
   var u=document.querySelector('#snack .snack-undo'); if(!u)return (bad.join('；')||'')+'；沒有復原';
   u.click(); await T.idle();
   var st2=await T.state();
   var back=Object.keys(st2).filter(function(k){return st2[k]&&st2[k].app;}).length;
   if(back!==before)bad.push('復原之後沒回到原樣('+before+' → '+back+')');
   return bad.join('；');
 """))


def research_round(board):
    """模擬找缺跑完一輪(research.run 收尾也是直接寫檔)。"""
    import board_doc as bd
    bd.set_data(lambda data, fb: data.__setitem__('research', [{'round': '2026-09-22 04:30', 'mode': 'dir',
        'direction': '測試方向二', 'judged': 3, 'kept': 0, 'added': [], 'dropped': {}, 'notes': '往 X 找'}]), live=board)
    return {}

CHECK_RESUME = 'board-check'   # 副本唯一那份履歷的 id:要寫客製紀錄的檢查照它寫,才是卡上真的會寄的那份


def seed_check_resume(url):
    """Sandbox 的找缺檢查先放一份假的可讀履歷,不碰真實資料夾。"""
    setup_timeout = 60
    with urllib.request.urlopen(url + '/api/settings', timeout=setup_timeout) as response:
        settings = json.load(response)['settings']
    resume = settings.setdefault('resume', {})
    langs = resume.get('langs') or ['zh']
    lang = 'zh' if 'zh' in langs else langs[0]
    rel = 'resume/board-check.txt'
    upload = urllib.request.Request(url + '/api/file?path=' + rel,
                                    data='示範履歷：供看板找缺規矩檢查。'.encode('utf-8'),
                                    method='PUT', headers={'User-Agent': 'board-check'})
    with urllib.request.urlopen(upload, timeout=setup_timeout) as response:
        if response.status != 200:
            raise RuntimeError('無法建立看板檢查用履歷')
    resume['resumes'] = [{'id': CHECK_RESUME, 'name': '看板檢查履歷', 'enabled': True,
                         'files': {lang: rel}}]
    resume['attachments'] = []
    body = json.dumps({'settings': settings}, ensure_ascii=False).encode('utf-8')
    save = urllib.request.Request(url + '/api/settings', data=body, method='POST',
                                  headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(save, timeout=setup_timeout) as response:
        if response.status != 200:
            raise RuntimeError('無法儲存看板檢查用履歷設定')

def flow_off(home):
    """這些規矩驗的是他手動按的那條路(種資料、按按鈕、看結果)。自動流程開著的話,
    種下去的新卡會被它接走(自動準備、自動填表),驗不到手動那條。所以副本預設關著,
    自動流程那幾條自己開、驗完自己關(見 shot.flow)。"""
    path = os.path.join(home, 'jobsalvo.json')
    data = {}
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    data['flow'] = dict(shot.FLOW_OFF)
    # agent 的 Chrome 用副本自己的資料夾(當成還沒建過):設定頁畫的是第一次設定的樣子,也碰不到真的那一個
    data['browser'] = {**(data.get('browser') or {}),
                       'data_dir': os.path.join(home, 'agent-chrome'), 'state': os.path.join(home, 'agent-chrome.json')}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sandbox(path, body=None, method=None, headers=None, timeout=10, raw=False):
    """打這一輪開的副本看板的 API(shot.sandbox_api,先驗是本機)。"""
    return shot.sandbox_api(shot.SB_URL[0], path, body, method, headers, timeout, raw)


def preference_note(home):
    """設定頁的 browser check 用假的偏好筆記,只寫在本輪臨時 home。"""
    path = os.path.join(home, 'preference-note.md')
    os.makedirs(home, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# 偏好筆記\n\n## 使用者自訂\n\n使用者自訂原話\n\n'
                 '## Agent 假設\n\n- Agent 假設原文｜出處：卡片甲；支持 1；反例 0\n')
    return {}


def check_bank_export_and_history():
    """Exercise the real save route against disposable data and verify both Git states."""
    import copy
    import time
    import threading
    from http.server import ThreadingHTTPServer
    from unittest import mock
    import board_doc as bd
    import board_server as server_app
    import config as cf
    import folder_history
    import settings_api
    previous_cf_home = cf.HOME
    with tempfile.TemporaryDirectory(prefix='boardcheck-history-') as home:
        previous_home = os.environ.get('JOBSALVO_HOME')
        previous_state = server_app.STATE
        cf.reload(home)
        settings = copy.deepcopy(cf.DEFAULTS)
        settings['board']['file'] = 'board.html'
        with open(os.path.join(home, cf.NAME), 'w', encoding='utf-8') as target:
            json.dump(settings, target, ensure_ascii=False)
        cf.reload(home)
        board = cf.LIVE
        with open(board, 'w', encoding='utf-8') as target:
            target.write(bd.assemble(':root{}', '<b id="stat-first">0</b>', '',
                                     {'jobs': [], 'bank': {'items': [], 'cats': ['其他']}}, '{}', '/*app*/'))
        generated = os.path.join(home, 'ship', 'generated.pdf')
        os.makedirs(os.path.dirname(generated), exist_ok=True)
        with open(generated, 'wb') as target:
            target.write(b'generated')
        for directory, filename in (('company-cache', 'company.json'),
                                    ('card-summaries', 'summary.json'),
                                    ('.research', 'round.json')):
            path = os.path.join(home, directory, filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as target:
                target.write('generated')
        with open(os.path.join(home, 'posted-cache.json'), 'w', encoding='utf-8') as target:
            target.write('{}')
        server_app.STATE = board
        listener = ThreadingHTTPServer(('127.0.0.1', 0), server_app.H)
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.dict(os.environ, {'JOBSALVO_HOME': home}):
                body = json.dumps({'op': 'put', 'item': {
                    't': 'Board check question', 'cat': '其他', 'focus': 'synthetic check',
                    'script': 'synthetic answer', 'stage': 'ok',
                }}, ensure_ascii=False).encode('utf-8')
                request = urllib.request.Request(
                    f'http://127.0.0.1:{listener.server_port}/api/bank', data=body,
                    method='POST', headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=10) as response:
                    if json.load(response).get('ok') is not True:
                        raise RuntimeError('題庫存檔 API 未成功')
                export = os.path.join(home, 'interview-bank.md')
                with open(export, encoding='utf-8') as source:
                    if 'Board check question' not in source.read():
                        raise RuntimeError('題庫 Markdown 匯出未更新')
                git = folder_history._git()
                if git:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        msg = folder_history.status(home)['message']
                        if re.search(r'最近一次 \d{4}-\d{2}-\d{2} \d{2}:\d{2}$', msg):
                            break
                        if msg.startswith('版本紀錄失敗'):
                            raise RuntimeError(msg)
                        time.sleep(0.1)
                    else:
                        raise RuntimeError('題庫存檔沒有產生 Git commit')
                    tracked = folder_history._run(git, home, 'ls-files').stdout.splitlines()
                    ignored_generated = {'ship/generated.pdf', 'company-cache/company.json',
                                         'card-summaries/summary.json', '.research/round.json',
                                         'posted-cache.json'}
                    if ('board.html' not in tracked or 'interview-bank.md' not in tracked
                            or ignored_generated.intersection(tracked)):
                        raise RuntimeError('Git 記錄沒有包含題庫與 Markdown 匯出,或包含了可重建產物')
                    if folder_history._run(git, home, 'remote', '-v').stdout:
                        raise RuntimeError('資料夾版本庫不應有遠端')
                with mock.patch.object(folder_history, '_git', return_value=None):
                    if settings_api.get()['git_history']['message'] != '沒有版本紀錄，因為找不到 git':
                        raise RuntimeError('設定頁沒有說明 Git 不存在')

            # 已經是 git repo 的資料夾三種情況(#301):從舊系統搬來的(repo 根目錄就是資料夾、有 jobsalvo.json、
            # 沒有 remote)要接手照常存版;有 remote、只是別的 repo 的子資料夾不存,設定頁照實寫原因和怎麼處理
            git = folder_history._git()
            old_identity = ('-c', 'user.name=Old system', '-c', 'user.email=old@example.invalid')
            for case in ('搬來的資料夾', '有 remote', '別的 repo 的子資料夾'):
                with tempfile.TemporaryDirectory(prefix='boardcheck-existing-repo-') as outer:
                    existing_home = os.path.join(outer, 'jobsearch') if case == '別的 repo 的子資料夾' else outer
                    os.makedirs(existing_home, exist_ok=True)
                    folder_history._run(git, outer, 'init', '--initial-branch=main')
                    settings = copy.deepcopy(cf.DEFAULTS)
                    settings['board']['file'] = 'board.html'
                    with open(os.path.join(existing_home, cf.NAME), 'w', encoding='utf-8') as target:
                        json.dump(settings, target, ensure_ascii=False)
                    if case != '別的 repo 的子資料夾':
                        folder_history._run(git, outer, 'add', '-A')
                        folder_history._run(git, outer, *old_identity, 'commit', '-m', 'old system')
                    if case == '有 remote':
                        folder_history._run(git, outer, 'remote', 'add', 'origin',
                                            'https://example.invalid/project.git')
                    with open(os.path.join(existing_home, 'board.html'), 'w', encoding='utf-8') as target:
                        target.write(bd.assemble(':root{}', '<b id="stat-first">0</b>', '',
                                                 {'jobs': []}, '{}', '/*app*/'))
                    commits_before = folder_history._run(git, outer, 'rev-list', '--all', '--count').stdout.strip()
                    cf.reload(existing_home)
                    try:
                        folder_history.flush_now(existing_home)
                        history = settings_api.get()['git_history']
                        commits = folder_history._run(git, outer, 'rev-list', '--all', '--count').stdout.strip()
                        if case == '搬來的資料夾':
                            if not re.search(r'最近一次 \d{4}-\d{2}-\d{2} \d{2}:\d{2}$', history['message']) \
                                    or commits != '2':
                                raise RuntimeError('搬來的資料夾沒有接手存版:' + history['message'])
                        else:
                            word = 'remote' if case == '有 remote' else '子資料夾'
                            if word not in history['message'] or not history.get('fix'):
                                raise RuntimeError(f'{case}:設定頁沒有照實寫原因和怎麼處理:' + history['message'])
                            if commits != commits_before or os.path.isdir(os.path.join(outer, 'jobsearch', '.git')):
                                raise RuntimeError(f'{case}:jobsalvo 不該存版卻存了')
                    finally:
                        cf.reload(home)
        finally:
            listener.shutdown()
            listener.server_close()
            thread.join(timeout=2)
            server_app.STATE = previous_state
            if previous_home is None:
                os.environ.pop('JOBSALVO_HOME', None)
                cf.reload(previous_cf_home)
            else:
                os.environ['JOBSALVO_HOME'] = previous_home
                cf.reload(previous_home)

FIND_CHECKS.append(
 ('找缺紀錄:看得到最近幾輪,說「別再往這找」真的存下來、下一輪 agent 讀得到',"""
   await T.resync();
   var fd=document.getElementById('finddet'); if(fd)fd.open=true; await T.sleep(400);
   var d=document.querySelector('#findrow [data-fold="findmore"]'); if(!d)return '找新職缺那條沒有「找過幾輪」';
   d.open=true;
   var box=document.getElementById('find-rounds'), bad=[];
   if(!box||box.textContent.indexOf('測試方向二')<0){d.open=false; return '面板上沒有最近幾輪';}
   box.querySelector('[data-fmute]').click(); await T.idle();
   var m=(((await T.state())['__research__']||{}).mute)||[];
   if(m.indexOf('測試方向二')<0)bad.push('按了「別再往這找」沒存進去');
   var u=document.querySelector('#snack .snack-undo'); if(u)u.click(); else bad.push('沒有復原');
   await T.idle();
   m=(((await T.state())['__research__']||{}).mute)||[];
   if(m.indexOf('測試方向二')>=0)bad.push('復原後還在');
   d.open=false;
   return bad.join('；');
 """, 'research_round'))



def lazy_resume(board):
    """在副本的待決與準備卡放 PDF 頁圖,語言挑沒有原檔的那個,驗點開才載入。"""
    import board_doc as bd
    def put(d):
        fb=d['fb']
        fid=next((j['id'] for j in d['data']['jobs'] if (fb.get(j['id']) or {}).get('app')=='ready'),None)
        top_id=next((j['id'] for j in d['data']['jobs'] if (fb.get(j['id']) or {}).get('app')=='ready' and j['id']!=fid),None)
        prep_id=next((j['id'] for j in d['data']['jobs'] if (fb.get(j['id']) or {}).get('app')=='prep'),None)
        if not fid or not top_id or not prep_id: return bd.SKIP
        page='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=='
        for j in d['data']['jobs']:
            if j['id'] in (fid,prep_id):
                j['resume']={'recommend':'board-check','lang':'en',
                             'variants':{'en-board-check':{'pages':[page]}}}
            elif j['id']==top_id:
                j['resume']={'recommend':'board-check','lang':'en','pages':[page],
                             'variants':{'en-board-check':{'motive':{'txt':'混合舊資料'}}}}
        for jid in (fid,top_id,prep_id):
            f=fb.get(jid) or {}; f.pop('lang',None); f.pop('variant',None); f.pop('resume_id',None); fb[jid]=f
        return {'fid':fid,'top_id':top_id,'prep_id':prep_id}
    got=bd.rewrite(put,board,by='board_check')
    return {} if got is bd.SKIP else got

LAZY_CHECKS=[
 ('履歷預覽不跟著頁面一起送,點「📄」才抓,抓到就打開',"""
   if(!P.fid)return '';
   await T.resync();
   var bad=[], j=(await fetch('/api/jobs').then(function(r){return r.json();})).jobs.filter(function(x){return x.id===P.fid;})[0];
   var v=(((j||{}).resume||{}).variants||{})['en-board-check']||{};
   if(v.pages)bad.push('/api/jobs 還是把 PDF 頁圖送出去了');
   if(v.pages_n!==1)bad.push('/api/jobs 沒留 PDF 頁數記號');
   document.querySelector('[data-tab="ready"]').click(); await T.sleep(300);
   var hs=document.querySelectorAll('#app .cohead'), c=null;
   for(var i=0;i<hs.length&&!c;i++){hs[i].click(); c=T.card(P.fid); if(!c)hs[i].click();}
   if(!c)return bad.concat('找不到那張卡').join('；');
   var b=c.querySelector('.rz-open[data-rz]');
   if(!b)return bad.concat('卡上沒有「📄」(抽掉全文之後看不出有預覽)').join('；');
   b.click();
   var ok=await T.until(function(){var m=document.getElementById('rzmodal');
     var img=m&&m.querySelector('img'); return m&&m.style.display!=='none'&&img&&img.complete&&img.naturalWidth>0;});
   if(!ok)bad.push('點了「📄」沒有抓回 PDF 頁圖並載入');
   var x=document.querySelector('#rzmodal .rzm-x'); if(x)x.click();
   document.querySelector('[data-tab="prep"]').click(); await T.sleep(300);
   var pc=T.card(P.prep_id);
   if(!pc){var ph=document.querySelectorAll('#app .cohead');
     for(var q=0;q<ph.length&&!pc;q++){ph[q].click();pc=T.card(P.prep_id);if(!pc)ph[q].click();}}
   if(!pc)bad.push('找不到準備區的 PDF 測試卡');
   else if(!pc.querySelector('.stage-ok'))bad.push('準備區已有 PDF 履歷仍被當成還沒產出');
   else if(!pc.querySelector('[data-adv="ready"]'))bad.push('準備區有 PDF 履歷卻沒有放行按鈕');
   document.querySelector('[data-tab="none"]').click();
   return bad.join('；');
 """, 'lazy_resume'),
 ('舊形狀履歷頁圖也只在點「📄」後載入',"""
   if(!P.top_id)return '找不到舊形狀測試卡';
   await T.resync();
   var bad=[], j=(await fetch('/api/jobs').then(function(r){return r.json();})).jobs.filter(function(x){return x.id===P.top_id;})[0];
   var rz=(j||{}).resume||{};
   if(rz.pages)bad.push('/api/jobs 還是帶著舊形狀頁圖');
   if(rz.pages_n!==1)bad.push('/api/jobs 沒留下舊形狀頁數');
   document.querySelector('[data-tab="ready"]').click(); await T.sleep(300);
   var c=T.card(P.top_id), hs=document.querySelectorAll('#app .cohead');
   for(var i=0;i<hs.length&&!c;i++){hs[i].click();c=T.card(P.top_id);if(!c)hs[i].click();}
   if(!c)return bad.concat('找不到舊形狀測試卡').join('；');
   var b=c.querySelector('.rz-open[data-rz]');
   if(!b)return bad.concat('舊形狀頁圖沒有「📄」').join('；');
   b.click();
   var ok=await T.until(function(){var m=document.getElementById('rzmodal'), img=m&&m.querySelector('img');
     return m&&m.style.display!=='none'&&img&&img.complete&&img.naturalWidth>0;});
   if(!ok)bad.push('舊形狀頁圖沒有載入');
   var x=document.querySelector('#rzmodal .rzm-x'); if(x)x.click();
   document.querySelector('[data-tab="none"]').click();
   return bad.join('；');
 """, 'lazy_resume'),
]
CHECKS=CHECKS+LAZY_CHECKS

# ---- 流程按鍵:不靠人想到情境,每一顆都按一遍,檢查永遠要成立的事 ----
# 他在實際用的時候抓到的:按了「送去準備」卡片還留在喜歡(一張卡算在兩個分頁)、⋯ 選單寫 1 張卻搬了 5 張。
# 這類問題的共同點是「同一件事有兩份規則」,所以檢查的是規則之間要對得上,不是某一個情境。
FLOW_INV = r"""
   function inv(){var F=window.__jobsalvoFlow; if(!F)return ['看板沒有提供流程檢查介面'];
     var c=F.counts(), ks=['none','like','meh','dislike','grow','techerr','prep','ready','ship','sent','rm'], bad=[];
     var sum=ks.reduce(function(a,k){return a+(c[k]||0);},0)+F.hidden();
     if(sum!==F.total())bad.push('各分頁加起來 '+sum+' 張,實際 '+F.total()+' 張(有卡算兩次或沒算到)');
     var find=c.none+c.like+c.meh+c.dislike+c.grow+c.techerr;
     if(c.all!==find)bad.push('「全部」'+c.all+' 張,各表態分頁加起來 '+find+' 張');
     [].forEach.call(document.querySelectorAll('#tabs .tab'),function(t){var k=t.getAttribute('data-tab'), n=t.querySelector('.n');
       if(n&&ks.indexOf(k)>=0&&+n.textContent!==(c[k]||0))bad.push('分頁「'+k+'」寫 '+n.textContent+',實際 '+(c[k]||0));});
     return bad;}
   function openTab(tab){var t=document.querySelector('#tabs [data-tab="'+tab+'"]'); if(!t)return false; t.click();
     [].slice.call(document.querySelectorAll('#app .cohead')).slice(0,3).forEach(function(h){if(!h.parentNode.open)h.click();});
     return true;}
"""
FLOW_CHECKS=[
 ('流程按鍵:每個流程分頁、每張卡的每顆流程按鈕按一遍,每張卡只算在一個分頁、按了會離開這一頁、復原回到原樣', FLOW_INV + r"""
   var bad=inv(), clicks=0, tabs=['none','like','meh','grow','prep','ready','ship','sent','techerr'];
   // 後台回話慢(CI 比較慢、#344 那次紅就是):存檔的回話一律慢 0.3 秒,檢查還是要等後台回了才看,不是靠剛好夠快
   var of=window.fetch; window.fetch=function(u){var p=of.apply(this,arguments);
     return String(u).indexOf('/api/save')>=0?p.then(function(r){return new Promise(function(ok){setTimeout(function(){ok(r);},300);});}):p;};
   // 同一頁上的兩句話不能打架:有卡被擋住,就不能同時說「這一階段都過關」
   for(const tab of ['ready','ship']){
     if(!openTab(tab))continue; await T.sleep(200);
     var txt=document.getElementById('app').textContent, held=(txt.match(/(\d+) 張(沒過驗收|還不能往下走)/)||[])[1];
     if(+held>0&&/都過關/.test(txt))bad.push(tab+':同一頁寫了「'+held+' 張擋住」又寫「都過關」');}
   for(const tab of tabs){
     if(!openTab(tab))continue; await T.sleep(200);
     var ids=[].slice.call(document.querySelectorAll('#app article[data-fid]')).slice(0,3).map(function(a){return a.getAttribute('data-fid');});
     for(const fid of ids){
       var card=T.card(fid); if(!card)continue;
       var sels=[].slice.call(card.querySelectorAll('.stage-b[data-adv],.stage-b[data-back],[data-err],[data-errback]')).map(function(x){
         return x.hasAttribute('data-adv')?'[data-adv="'+x.getAttribute('data-adv')+'"]':x.hasAttribute('data-back')?'[data-back="'+x.getAttribute('data-back')+'"]':
           x.hasAttribute('data-errback')?'[data-errback]':'[data-err]';});
       for(const sel of sels){
         openTab(tab); await T.sleep(120);
         var c2=T.card(fid), b=c2&&c2.querySelector(sel); if(!b||b.disabled)continue;
         var before=window.__jobsalvoFlow.fb(fid), where=tab+' '+fid.slice(-12)+' '+sel;
         b.click(); clicks++; await T.sleep(50); await T.idle();   // 按下去等後台回話(#343):不寫死秒數,後台慢就多等
         inv().forEach(function(x){bad.push(where+':'+x);});
         var now=window.__jobsalvoFlow.tabOf(fid);
         if(now!==tab&&T.card(fid))bad.push(where+':卡已經到「'+now+'」,卻還留在這一頁');
         var u=document.querySelector('#snack.on .snack-undo');
         if(!u){bad.push(where+':按了沒有復原'); continue;}
         u.click(); await T.sleep(50); await T.idle();
         inv().forEach(function(x){bad.push(where+' 復原後:'+x);});
         var after=window.__jobsalvoFlow.fb(fid);
         ['s','app','rm','approve','sent_at','oc'].forEach(function(k){
           if(JSON.stringify(before[k])!==JSON.stringify(after[k]))bad.push(where+':復原後 '+k+' 沒回到原樣('+JSON.stringify(before[k])+' → '+JSON.stringify(after[k])+')');});
       }
     }
   }
   await T.idle(); window.fetch=of; openTab('none');
   if(clicks<8)bad.push('只按到 '+clicks+' 顆,示範看板的卡不夠驗');
   return bad.slice(0,8).join('；');
 """),
 ('流程按鍵:公司 ⋯「這家全部送去準備履歷中(N)」寫幾張就搬幾張', FLOW_INV + r"""
   var bad=[], done=false;
   for(const tab of ['like','none','meh','grow']){
     if(!openTab(tab))continue; await T.sleep(200);
     var btn=document.querySelector('#app [data-coprep]'); if(!btn)continue;
     var n=+((btn.textContent.match(/（(\d+)）/)||[])[1]||0), c0=window.__jobsalvoFlow.counts().prep;
     btn.click(); await T.sleep(200);
     var moved=window.__jobsalvoFlow.counts().prep-c0;
     if(moved!==n)bad.push(tab+':按鈕寫 '+n+' 張,實際搬了 '+moved+' 張');
     inv().forEach(function(x){bad.push(tab+':'+x);});
     var u=document.querySelector('#snack.on .snack-undo'); if(u){u.click(); await T.sleep(200);} else bad.push(tab+':沒有復原');
     if(window.__jobsalvoFlow.counts().prep!==c0)bad.push(tab+':復原後準備履歷中的張數沒回去');
     done=true; break;
   }
   await T.idle(); openTab('none');
   if(!done)bad.push('示範看板上找不到「這家全部送去準備履歷中」可以驗');
   return bad.join('；');
 """),
 ('流程按鍵:流程裡的卡標成出錯了,在「🔧 出錯了」按「放回原處」回到原本的心情和階段(可以復原)', FLOW_INV + r"""
   var bad=[], F=window.__jobsalvoFlow;
   if(!openTab('prep'))return '沒有準備履歷中的卡可以驗'; await T.sleep(200);
   var a=document.querySelector('#app article[data-fid]'); if(!a)return '準備履歷中沒有卡';
   var fid=a.getAttribute('data-fid'), before=F.fb(fid), er=a.querySelector('[data-err]');
   if(!er)return '準備履歷中的卡沒有「🔧 標成出錯了」';
   er.click(); await T.sleep(200);
   if(F.fb(fid).s!=='techerr')return '按了沒標成出錯了';
   openTab('techerr'); await T.sleep(200);
   var c=T.card(fid), b=c&&c.querySelector('[data-errback]');
   if(!b){await T.idle(); openTab('none'); return '「出錯了」頁的卡上沒有「放回原處」';}
   if(before.s&&b.textContent.indexOf({like:'喜歡',meh:'普通',dislike:'不喜歡',grow:'差一點'}[before.s]||before.s)<0)
     bad.push('按鈕沒講會放回哪個心情:'+b.textContent.trim());
   b.click(); await T.sleep(200);
   var now=F.fb(fid);
   if(now.app!==before.app||(now.s||'')!==(before.s||''))bad.push('放回原處沒回到原本的心情和階段('+JSON.stringify([before.s,before.app])+' → '+JSON.stringify([now.s,now.app])+')');
   if('s0' in now||'app0' in now)bad.push('放回之後還留著 s0/app0');
   if(!now.live_ok)bad.push('放回之後沒記「頁面沒壞」(live_ok),要寄的檔案會一直被失效標記擋住');
   if(T.card(fid))bad.push('放回之後卡還留在「出錯了」頁');
   inv().forEach(function(x){bad.push(x);});
   var u=document.querySelector('#snack.on .snack-undo'); if(!u)bad.push('放回原處沒有復原'); else {u.click(); await T.sleep(200);}
   var back=F.fb(fid);
   if(back.s!=='techerr'||back.app||back.live_ok)bad.push('按復原沒回到「出錯了」:'+JSON.stringify(back));
   // 收回原樣:放回原處再把 live_ok 拿掉,等於從沒標過
   openTab('techerr'); await T.sleep(200); var b2=T.card(fid)&&T.card(fid).querySelector('[data-errback]');
   if(b2){b2.click(); await T.sleep(200);}
   await T.idle(); var st=await T.state(), q={__rev__:1,__base__:{}}; q.__base__[fid]=st[fid]; q[fid]=before;
   await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q)});
   await T.resync(); openTab('none');
   return bad.join('；');
 """),
 ('流程按鍵:流程裡的卡改成不喜歡,一起退出流程(可以復原)', FLOW_INV + r"""
   var bad=[];
   if(!openTab('prep'))return '沒有準備履歷中的卡可以驗'; await T.sleep(200);
   var a=document.querySelector('#app article[data-fid]'); if(!a)return '準備履歷中沒有卡';
   var fid=a.getAttribute('data-fid'), before=window.__jobsalvoFlow.fb(fid);
   var dis=a.querySelector('.fb-b[data-s="dislike"]'); if(!dis)return '準備履歷中的卡沒有表態按鈕';
   dis.click(); await T.sleep(200);
   if(window.__jobsalvoFlow.fb(fid).app)bad.push('改成不喜歡還留在流程裡(app='+window.__jobsalvoFlow.fb(fid).app+')');
   inv().forEach(function(x){bad.push(x);});
   var u=document.querySelector('#snack.on .snack-undo'); if(!u)bad.push('沒有復原'); else {u.click(); await T.sleep(200);}
   var after=window.__jobsalvoFlow.fb(fid);
   if(after.app!==before.app||(after.s||'')!==(before.s||''))bad.push('復原後沒回到原樣');
   await T.idle(); openTab('none');
   return bad.join('；');
 """),
]
CHECKS=CHECKS+FLOW_CHECKS

# 介面一致性(可用性原則的「一致性」「使用者能控制」):同一件事在不同地方長得不一樣、不同的事長得一樣,
# 都是他實際用的時候抓到的。寫成檢查:每一頁、每個公司選單、每張卡的按鈕都看一遍。
UX_INV = FLOW_INV + r"""
   // 同一個概念只能有一個圖示(分頁、按鈕、選單、標籤全部算)
   var ICON={'喜歡':'👍','普通':'😐','不喜歡':'👎','差一點':'💪','移除':'🗑','已移除':'🗑','封鎖':'🚫','已封鎖':'🚫','出錯了':'🔧'};
   function iconIssues(where){var bad=[];
     [].forEach.call(document.querySelectorAll('#tabs .tab, #app button, #app .more-m button, #ctlbar button, .ctl-act'),function(el){
       var t=(el.textContent||'').replace(/\s+/g,' ').trim(), m=t.match(/^(\S+?)\s*(不喜歡|喜歡|普通|差一點|已移除|移除|已封鎖|封鎖|出錯了)(?=[\s\d(（]|$)/);
       if(!m||/[\w一-鿿]/.test(m[1]))return;          // 開頭不是圖示的不算
       var want=ICON[m[2]]; if(want&&m[1].replace(/️/g,'')!==want.replace(/️/g,''))bad.push(where+':「'+t.slice(0,16)+'」用 '+m[1]+',「'+m[2]+'」別處是 '+want);});
     return bad;}
   // 按鈕、選單項目的字不能被擠成兩行
   // 量法:暫時不准換行,高度變矮 = 原本被擠成兩行(數字小標字級不同,不能數文字的上緣)
   function wrapIssues(where){var bad=[];
     [].forEach.call(document.querySelectorAll('#tabs .tab, #app .more-m:not([hidden]) button, #app .stage-b'),function(el){
       if(!el.offsetParent)return; var h=el.getBoundingClientRect().height, ws=el.style.whiteSpace;
       el.style.whiteSpace='nowrap'; var h2=el.getBoundingClientRect().height; el.style.whiteSpace=ws;
       if(h-h2>4)bad.push(where+':「'+el.textContent.trim().slice(0,18)+'」擠成兩行');});
     return bad;}
"""

UX_CHECKS=[
 ('介面一致性:同一個概念只有一個圖示、按鈕與選單的字不擠成兩行(每一頁、打開公司選單看一遍)', UX_INV + r"""
   var bad=[], tabs=['none','like','meh','dislike','grow','all','prep','ready','ship','sent','techerr','rm'];
   bad=bad.concat(iconIssues('分頁列'));
   for(const tab of tabs){
     if(!openTab(tab))continue; await T.sleep(200);
     var mb=document.querySelector('#app .cohead .more-b'); if(mb){mb.click(); await T.sleep(120);}
     bad=bad.concat(iconIssues(tab), wrapIssues(tab));
     if(mb)document.body.click();}
   openTab('none'); document.body.click(); await T.sleep(200);
   return [...new Set(bad)].slice(0,12).join('；');
 """),
 ('介面一致性:不同的按鈕不通到同一個地方(捷徑要講清楚它通到哪一頁)', UX_INV + r"""
   var bad=[], names={};
   [].forEach.call(document.querySelectorAll('#tabs .tab'),function(t){names[t.getAttribute('data-tab')]=t.textContent.replace(/[\d\s⚠△]+$/,'').replace(/^[^\w\u4e00-\u9fff]+/,'').trim();});
   // 先封鎖一家,「🚫 已封鎖」那顆才會出現;檢查完解除
   openTab('none'); await T.sleep(200);
   var mb=document.querySelector('#app .cohead .more-b'); if(mb){mb.click(); await T.sleep(120);}
   var blk=document.querySelector('#app .more-m:not([hidden]) [data-block]'), co=blk&&blk.getAttribute('data-block');
   if(blk){blk.click(); await T.sleep(400);}
   [].forEach.call(document.querySelectorAll('[data-gotab]'),function(b){var k=b.getAttribute('data-gotab'), t=b.textContent.trim();
     if(names[k]&&t.indexOf(names[k])<0)bad.push('「'+t.slice(0,16)+'」按下去是「'+names[k]+'」那一頁');});
   if(!co)bad.push('示範看板沒有公司可以封鎖,這條沒驗到');
   else{
     // 「🚫 已封鎖」點開要看得到封鎖名單、能從那裡解除;解除之後這一家回到看板
     var chip=document.querySelector('[data-blocklist]'); if(chip){chip.click(); await T.sleep(300);}
     var box=document.getElementById('rzmodal'), m2=box&&[].find.call(box.querySelectorAll('.cohead'),function(h){return h.textContent.indexOf(co)>=0;});
     if(!m2)bad.push('「🚫 已封鎖」點開沒看到封鎖名單裡的「'+co+'」');
     var mb2=m2&&m2.querySelector('.more-b'); if(mb2){mb2.click(); await T.sleep(120);}
     var ub=box&&box.querySelector('[data-unblock="'+co+'"]');
     if(ub){ub.click(); await T.sleep(400);} else bad.push('封鎖名單裡沒有「解除封鎖」');
     if((window.__jobsalvoFlow||{}).hidden&&window.__jobsalvoFlow.hidden()>0)bad.push('解除封鎖之後還有卡被藏著');
     if(typeof closeModal==='function')closeModal(); var x=document.querySelector('#rzmodal .rzm-x'); if(x)x.click();}
   return bad.join('；');
 """),
 ('介面一致性:卡片上能按的動作,公司那一列都有「這家全部」的版本', UX_INV + r"""
   var bad=[];
   for(const tab of ['none','like','meh','dislike','grow']){
     if(!openTab(tab))continue; await T.sleep(200);
     var g=document.querySelector('#app details.cogrp'); if(!g)continue;
     var cards=g.querySelectorAll('article[data-fid]'); if(!cards.length)continue;
     var cardTxt=[].map.call(cards,function(c){return c.textContent;}).join(' ');
     var menu=g.querySelector('.cohead .more-m'), mt=menu?menu.textContent:'';
     if(/送去準備履歷中/.test(cardTxt)&&!/這家全部送去準備履歷中/.test(mt))bad.push(tab+':卡片能送去準備履歷中,公司選單沒有「這家全部送去準備履歷中」');
     if(cards[0].querySelector('[data-rm="1"]')&&!/這家全部移除/.test(mt))bad.push(tab+':卡片能移除,公司選單沒有「這家全部移除」');}
   openTab('none'); await T.sleep(200);
   return bad.join('；');
 """),
 ('介面一致性:會自動派 agent、會定時跑的,每一個都有勾選框可以關', r"""
   var bad=[]; document.querySelector('[data-tab="cfg"]').click(); await T.sleep(1200);
   var f=document.querySelector('[data-fold="cfg:flow"]'); if(!f)return '設定頁沒有自動流程那一塊';
   if(!f.open)f.querySelector('summary').click(); await T.sleep(300);
   var labels=[].map.call(f.querySelectorAll('label'),function(l){return l.querySelector('input[type=checkbox]')?l.textContent:'';}).join('｜');
   [['按 👍 就送去準備','按讚就送去準備'],['自動準備履歷','新卡自動準備履歷'],['自動進「可以投了」','驗收過自動進可以投了'],
    ['填表','進可以投了就讓 agent 填表'],['查應徵進度','每天自動查應徵進度']].forEach(function(x){
     if(labels.indexOf(x[0])<0)bad.push('「'+x[1]+'」沒有勾選框可以關');});
   document.querySelector('[data-tab="none"]').click(); await T.sleep(300);   // 回到看板,後面的檢查從這裡開始
   return bad.join('；');
 """),
]
# 可以後悔(可用性原則「使用者能控制」):會改資料的按鈕,按下去都要給「復原」,按了要回到原樣
UX_CHECKS.append(
 ('介面一致性:卡片和公司選單上會改資料的按鈕,按了都有「復原」,按復原回到原樣', UX_INV + r"""
   var bad=[], tried=0;
   function snap(){var F=window.__jobsalvoFlow; return JSON.stringify(F.counts())+'|'+(window.__jobsalvoFlow.hidden());}
   async function tryOne(where, getBtn, key){
     var b=getBtn(); if(!b||b.disabled)return; tried++;
     var before=snap(), fid=(b.closest('article')||{}).getAttribute&&b.closest('article').getAttribute('data-fid');
     var f0=fid?JSON.stringify(window.__jobsalvoFlow.fb(fid)):'';
     var sn=document.getElementById('snack'); if(sn)sn.className='';
     b.click(); await T.sleep(50); await T.idle();   // 按下去要等後台回話(#343)
     var u=document.querySelector('#snack.on .snack-undo');
     if(!u){
       // 沒跳復原也可以:卡片留在原地,按原本那顆就回去(表態就是這樣,一次標很多張不用每次跳提示)
       var c=fid&&T.card(fid), was=fid?(JSON.parse(f0).s||''):'';
       var back=c&&c.querySelector(was?'[data-s="'+was+'"]':'[data-s].on');
       if(!back){bad.push(where+'「'+key+'」按了'+(c?'改不回去':'卡片就不見了')+',又沒有復原'); return;}
       back.click(); await T.sleep(250);
       if((window.__jobsalvoFlow.fb(fid).s||'')!==was)bad.push(where+'「'+key+'」在原地按回去沒回到原樣');
       return;}
     u.click(); await T.sleep(50); await T.idle();
     // 比存檔會留下的樣子(空字串、空陣列、空物件都算沒有,後台存的時候就拿掉了)
     function lean(v){if(v===null||v===undefined||v==='')return null; if(Array.isArray(v))return v.length?v:null;
       if(typeof v==='object'){var o={},n=0; Object.keys(v).sort().forEach(function(k){var x=lean(v[k]); if(x!==null){o[k]=x;n++;}}); return n?o:null;} return v;}
     var f1=fid?JSON.stringify(lean(window.__jobsalvoFlow.fb(fid))):'', s1=snap();
     if(s1!==before||(fid&&f1!==JSON.stringify(lean(JSON.parse(f0)))))bad.push(where+'「'+key+'」按復原沒回到原樣'+(s1!==before?'('+before+' → '+s1+')':'('+f0+' → '+f1+')'));}
   for(const tab of ['none','like','meh','dislike','grow']){
     if(!openTab(tab))continue; await T.sleep(200);
     var card=document.querySelector('#app article[data-fid]'); if(!card)continue;
     var fid=card.getAttribute('data-fid'), q=function(sel){return function(){var c=T.card(fid); return c&&c.querySelector(sel);};};
     for(const sv of ['like','meh','dislike','grow']){openTab(tab); await T.sleep(150); await tryOne(tab,q('[data-s="'+sv+'"]:not(.on)'),'表態 '+sv);}
     openTab(tab); await T.sleep(150); await tryOne(tab,q('[data-rm="1"]'),'移除');
     openTab(tab); await T.sleep(150); await tryOne(tab,q('[data-err]'),'出錯了');
     for(const act of ['data-coprep','data-corm','data-block']){
       openTab(tab); await T.sleep(150);
       var mb=document.querySelector('#app .cohead .more-b'); if(mb){mb.click(); await T.sleep(120);}
       await tryOne(tab,function(){return document.querySelector('#app .more-m:not([hidden]) ['+act+']');},act.replace('data-',''));
       document.body.click();}}
   openTab('none'); await T.sleep(200);
   if(!tried)bad.push('一顆都沒試到(示範看板沒有卡?)');
   return [...new Set(bad)].slice(0,12).join('；');
 """))
# 空畫面(empty state):沒有東西的時候,要講為什麼是空的、下一步怎麼做,不能只寫「沒有」
UX_CHECKS.append(
 ('介面一致性:每一頁空的時候都講下一步怎麼做', UX_INV + r"""
   var bad=[], NEXT=/按|去|到「|先|點|找|貼|標|送|等|打開|設定|上傳|放回|清掉|換/;
   var names={}; [].forEach.call(document.querySelectorAll('#tabs .tab'),function(t){names[t.getAttribute('data-tab')]=t.textContent.trim();});
   for(const tab of ['none','like','meh','dislike','grow','all','prep','ready','ship','sent','iv','techerr','rm']){
     if(!openTab(tab))continue; await T.sleep(250);
     if(document.querySelector('#app article[data-fid], #app details.cogrp'))continue;     // 不是空的
     var t=(document.getElementById('app').textContent||'').replace(/\s+/g,' ').trim();
     if(!t)bad.push((names[tab]||tab)+':整頁空白');
     else if(!NEXT.test(t))bad.push((names[tab]||tab)+':空的時候只寫「'+t.slice(0,30)+'」,沒講下一步');}
   openTab('none'); await T.sleep(200);
   return bad.join('；');
 """))
# 設定頁的設計原則:每一個輸入都有看得懂的標籤;填錯按儲存要講哪裡錯、不能默默存進去或默默不存
UX_CHECKS.append(
 ('設定頁:每一個輸入都有標籤,填錯按儲存會講哪裡錯', r"""
   var bad=[]; document.querySelector('[data-tab="cfg"]').click(); await T.sleep(1500);
   [].forEach.call(document.querySelectorAll('#app details.fold:not([open]) > summary'),function(s){s.click();});
   await T.sleep(500);
   // 標籤交給 axe-core 判(螢幕報讀器認得的才算,只有淡字提示不算)
   if(!window.axe)bad.push('axe-core 沒載入');
   else{var ax=await axe.run(document.getElementById('app'),{runOnly:['label','select-name','button-name','aria-input-field-name']});
     ax.violations.forEach(function(v){v.nodes.forEach(function(n){
       bad.push(v.id+':'+n.html.slice(0,90));});});}
   // 填錯:最多停幾張填成字,按儲存
   var fm=document.querySelector('#app [data-cf="flow.fill_max"]');
   if(!fm)bad.push('找不到「最多停幾張」那一格');
   else{fm.value='很多張'; fm.dispatchEvent(new Event('input',{bubbles:true})); fm.dispatchEvent(new Event('change',{bubbles:true}));
     var sv=document.querySelector('[data-cfsave]'); sv.click(); await T.sleep(1200);
     var said=(document.getElementById('cfg-st')||{}).textContent+' '+T.snack();
     if(!/數字/.test(said))bad.push('填錯按儲存,沒講哪裡錯(看到:'+said.trim().slice(0,60)+')');
     var st=await fetch('/api/settings').then(function(r){return r.json();});
     if(String((st.effective.flow||{}).fill_max)==='很多張')bad.push('填錯的值被存進去了');
     // 放回去,不留給後面的檢查
     if(typeof cfgLoad==='function')cfgLoad(function(){});}
   document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
   return [...new Set(bad)].slice(0,10).join('；');
 """))
# 效能(RAIL:按下去 0.1 秒內要有反應)。示範看板只有十幾張;CI 另外拿 demo.py --extra 400 的大看板跑這一條
UX_CHECKS.append(
 ('效能:切分頁、表態、展開公司都在 0.2 秒內畫完(大看板)', UX_INV + r"""
   var bad=[], worst={};
   function time(label, fn){var t=performance.now(); fn(); var ms=performance.now()-t; worst[label]=Math.max(worst[label]||0,ms);}
   for(const tab of ['none','all','like','prep','ready','ship','sent']){
     var t=document.querySelector('#tabs [data-tab="'+tab+'"]'); if(!t)continue;
     time('切分頁',function(){t.click();}); await T.sleep(50);}
   document.querySelector('#tabs [data-tab="all"]').click(); await T.sleep(100);
   var hs=[].slice.call(document.querySelectorAll('#app .cohead')).slice(0,5);
   for(const h of hs){time('展開公司',function(){h.click();}); await T.sleep(30);}
   var b=document.querySelector('#app article[data-fid] [data-s="meh"]:not(.on)');
   if(b){time('表態',function(){b.click();}); await T.sleep(50); var b2=b.closest('article').querySelector('[data-s="meh"]'); if(b2)b2.click();}
   var n=(window.__jobsalvoFlow||{}).total?window.__jobsalvoFlow.total():0;
   Object.keys(worst).forEach(function(k){if(worst[k]>200)bad.push(k+' 花了 '+Math.round(worst[k])+' 毫秒('+n+' 張卡)');});
   console.log('效能 '+n+' 張:'+Object.keys(worst).map(function(k){return k+' '+Math.round(worst[k])+'ms';}).join(', '));
   openTab('none'); await T.sleep(100);
   return bad.join('；');
 """))
CHECKS=CHECKS+UX_CHECKS

PRE={'external_write':external_write,'status_issue':status_issue,'research_round':research_round,
     'lazy_resume':lazy_resume,'xss_job':xss_job}
PRE.update(ANS_PRE)
CHECKS=CHECKS+SAVE_CHECKS+PREP_CHECKS+FIND_CHECKS+[
  ('設定頁:分類、標籤的關鍵字寫錯,存的時候看到的就是後台拒絕的那一句(看板不自己先擋)',r'''
    var bad=[];
    // 後台會拒絕的那一句:第一個類別的關鍵字寫錯,直接送後台(送不進去,不會存)
    var cur=await fetch('/api/settings').then(function(r){return r.json();});
    var cand=JSON.parse(JSON.stringify(cur.settings||{})); cand.board=cand.board||{};
    cand.board.categories=JSON.parse(JSON.stringify(((cur.effective||{}).board||{}).categories||[]));
    if(!cand.board.categories[0])return '設定裡沒有類別';
    cand.board.categories[0].match='(沒關起來';
    var r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:cand})});
    var want=(await r.json()).msg||'';
    if(r.ok)bad.push('後台收了寫錯的關鍵字');
    var name=cand.board.categories[0].name||'';
    if(want.indexOf('類別「'+name+'」的關鍵字寫法不對')<0)bad.push('後台拒絕的那一句沒講是哪個類別:'+want);
    // 設定頁上同一格寫一樣的,按儲存
    var t=document.querySelector('[data-tab="cfg"]'); if(!t)return '沒有設定頁';
    t.click();
    // 先丟掉這一頁手上舊的那一份(前面幾條可能改過設定),從後台現在的讀起
    var rl=await T.until(function(){return document.querySelector('[data-cfreload]');},8000); if(rl)rl.click(); await T.sleep(300);
    var inp=await T.until(function(){return document.querySelector('[data-cfli="categories|0|match"]');},8000);
    if(!inp)return '設定頁找不到第一個類別的關鍵字格';
    inp.value='(沒關起來'; inp.dispatchEvent(new Event('input',{bubbles:true}));
    document.querySelector('[data-cfsave]').click();
    var sn=await T.until(function(){var s=T.snack()||''; return /沒存成/.test(s)&&s;},8000);
    if(!sn)bad.push('按了儲存沒有講沒存成');
    else if(sn.indexOf(want)<0)bad.push('設定頁講的不是後台拒絕的那一句:'+sn+' / 後台:'+want);
    rl=document.querySelector('[data-cfreload]'); if(rl)rl.click(); await T.sleep(300);   // 放棄這一格的改動
    document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
    return bad.join('；');
  '''),
  ('桌機與手機取消後重標同一張卡不會誤報衝突',r"""
    var card=T.open(); if(!card)return '找不到可操作的卡片';
    var id=card.getAttribute('data-fid'), original=((await T.state())[id]||{}).s||'', bad=[];
    async function wait(){await T.idle();return (await T.state())[id]||{};}
    async function clickMark(value){
      var c=T.card(id), b=c&&c.querySelector('.fb-b[data-s="'+value+'"]');
      if(!b)return '找不到 '+value+' 標記按鈕';
      if(b.checkVisibility&&!b.checkVisibility())return value+' 標記按鈕在目前畫面看不見';
      b.click(); return '';
    }
    var first=original==='like'?'meh':'like', second=first==='like'?'meh':'like';
    try{
      if(original){var err=await clickMark(original);if(err)return err;await wait();}
      var err=await clickMark(first);if(err)return err;
      var after=await wait(); if(after.s!==first)bad.push('第一次標記沒有存好');
      await clickMark(first); after=await wait(); if(after.s)bad.push('取消標記後仍有值');
      await clickMark(second); after=await wait();
      if(after.s!==second)bad.push('取消後重標被當成衝突或沒有存入');
      if(/別的裝置改過|衝突/.test(T.snack()||''))bad.push('顯示了假的衝突訊息: '+T.snack());
    }finally{
      var current=(await T.state())[id]||{};
      if(current.s){await clickMark(current.s);await wait();}
      if(original){await clickMark(original);await wait();}
    }
    return bad.join('；');
  """,'no_setup',{'viewports':['desktop','mobile']})]



def custom_profile_reminder_cases(board):
    """在看板副本上安排三種投遞方式,檢查提醒只出現在另開客製履歷。"""
    import board_doc as bd
    data = bd.load(board)
    jobs = (data['data'].get('jobs') or [])[:3]
    if len(jobs) < 3:
        raise ValueError('示範看板少於三張卡,無法驗三種投遞方式')
    routes = [
        ({'method': 'platform_profile', 'profile_kind': 'custom',
          'profile_url': 'https://profiles.example/custom'}, True),
        ({'method': 'direct_upload'}, False),
        ({'method': 'platform_profile', 'profile_kind': 'fixed',
          'profile_url': 'https://profiles.example/fixed'}, False),
    ]
    cards = [
        {'id': job['id'], 'reminder': reminder}
        for job, (_delivery, reminder) in zip(jobs, routes)
    ]

    def mut(fb):
        for job, (delivery, _reminder) in zip(jobs, routes):
            entry = fb.setdefault(job['id'], {})
            entry['custom_docs'] = {
                'resume:' + CHECK_RESUME + ':zh': {'status': 'review', 'name': '客製履歷'},
            }
            entry['resume_id'] = CHECK_RESUME
            entry['lang'] = 'zh'
            entry['app'] = 'ready'
            entry.setdefault('apply', {})['delivery'] = delivery

    bd.set_fb(mut, live=board, by='board_check')
    return {'cards': cards, 'item': 'resume:' + CHECK_RESUME + ':zh'}


PRE['custom_profile_reminder_cases'] = custom_profile_reminder_cases
CHECKS.append((
    '客製履歷另開平台履歷才提醒,且不擋收下',
    """
      var bad=[], cases=P.cards||[], reminder='agent 會在平台上另外開一份履歷。';
      var ready=document.querySelector('[data-tab="ready"]'); if(ready)ready.click();
      T.sync(); await T.sleep(700);
      [...document.querySelectorAll('#app .cohead')].forEach(function(h){
        if(!h.parentNode.open)h.click();
      });
      await T.sleep(250);
      var state=await T.state();
      cases.forEach(function(c){
        var card=T.card(c.id);
        if(!card){bad.push('找不到提醒案例卡 '+c.id+';DOM='+
          [...document.querySelectorAll('#app article[data-fid]')].map(function(x){return x.getAttribute('data-fid');}).join(','));return;}
        var accept=card.querySelector('[data-cust-action="accept"][data-cust-item="'+P.item+'"]');
        var row=accept&&accept.closest('.cust-doc');
        if(!row){bad.push('客製履歷那格沒有收下按鈕:'+
          JSON.stringify(state[c.id]||{}));return;}
        var shown=row.textContent.indexOf(reminder)>=0;
        if(shown!==c.reminder)bad.push((c.reminder?'缺少':'不該出現')+'另開履歷提醒');
        if(accept.disabled)bad.push('提醒擋住客製履歷收下');
      });
      return bad.join('；');
    """,
    'custom_profile_reminder_cases',
))


def attachment_approval_issue_case(board):
    """在示範投遞卡上放一筆平台附件不符,驗核准鈕和點擊處理都會擋下。"""
    import board_doc as bd
    import re
    parsed = bd.load(board)
    jobs = parsed['data'].get('jobs') or []
    if len(jobs) < 9:
        raise ValueError('示範看板少於九張卡,無法驗投遞核准')
    job = jobs[8]
    target = str(job.get('target') or '')
    match = re.search(r'\*\*[^*]+? · ([^*]+)\*\*', target)
    company = match.group(1).strip() if match else '示範公司'
    issue = '固定平台履歷附件「求職信」內容不同（固定版被蓋掉）'
    delivery = {
        'method': 'platform_profile', 'profile_kind': 'custom',
        'profile_url': 'https://profiles.example.invalid/custom',
    }

    def mut(fb):
        entry = fb.setdefault(job['id'], {})
        entry['app'] = 'ship'
        entry['form'] = {'f': []}
        entry.pop('approve', None)
        entry['ds'] = 'stuck'
        entry['apply'] = {
            'stage': 'fill', 'issues': [issue], 'tab_id': '5',
            'delivery': delivery, 'session': 'board-check-e2e',
        }

    bd.set_fb(mut, live=board, by='board_check')
    return {'id': job['id'], 'company': company, 'issue': issue}


PRE['attachment_approval_issue_case'] = attachment_approval_issue_case
CHECKS.append((
    '平台附件不符時核准停用,舊按鈕點擊也拒絕',
    r"""
      var bad=[], tab=document.querySelector('[data-tab="ship"]');
      if(!tab)return '找不到可投遞分頁';
      tab.click(); await T.sync(); await T.sleep(700);
      var head=[...document.querySelectorAll('#app .cohead')].find(function(h){return h.textContent.includes(P.company);});
      if(!head)return '找不到 '+P.company+' 的代投卡';
      if(!head.parentNode.open)head.click(); await T.sleep(250);
      var card=T.card(P.id); if(!card)return '找不到 '+P.id+' 的代投卡';
      if(!card.textContent.includes(P.issue))bad.push('卡片沒有寫清楚附件名稱和內容不符');
      var approve=card.querySelector('[data-approve]');
      if(!approve)bad.push('卡片沒有顯示核准按鈕');
      else{
        if(!approve.disabled)bad.push('附件不符時核准按鈕仍可按');
        if(parseFloat(getComputedStyle(approve).opacity)>=0.9)bad.push('停用核准按鈕看起來仍可按');
        approve.disabled=false; approve.click(); await T.sleep(100);
        var state=await T.state();
        if((state[P.id]||{}).approve)bad.push('點擊處理仍寫入核准');
        if(!(T.snack()||'').includes('還不能確認送出'))bad.push('點擊處理沒有說明確認送出被擋');
      }
      return bad.join('；');
    """,
    'attachment_approval_issue_case',
))

PRE['no_setup'] = lambda _board: None


def review_cards(board):
    """「一張一張看」要至少三張沒表態的卡。以前靠前面「找新職缺」那條在副本加的兩張假職缺,
    單跑或快版跳過那條就只剩一張,檢查一定失敗。自己補,不靠別條的順序。"""
    import board_doc as bd, time as _t
    d=bd.load(board); fb=json.loads(d['fb'])
    have=[j for j in d['data']['jobs'] if not fb.get(j['id']) and not j.get('bk')]
    need=max(0,3-len(have))
    if need:
        today=_t.strftime('%Y-%m-%d')
        new=[{'id':f'https://example.test/review/{int(_t.time())}-{i}','cat':'其他',
              'target':f'(檢查用)一張一張看 {i} · 檢查公司','chan':'官方','ammo':'','note':'',
              'bk':False,'dead':False,'added':today,'sum':{'fit':'檢查用'}} for i in range(need)]
        bd.set_data(lambda data,_fb: data['jobs'].extend(new), live=board)
    return {'added':need}
PRE['review_cards'] = review_cards

CHECKS.append((
    '一張一張看:一次一張、表態存得進去而且自動換下一張,← 回得去看得到剛標的,→ 跳過,結束回到清單',
    r"""
      var bad=[], card=function(){return document.querySelector('#app .rv-card article[data-fid]');};
      if(P&&P.added)await T.resync();
      document.querySelector('[data-tab="none"]').click(); await T.sleep(400);
      var go=document.querySelector('#app [data-rvstart]'); if(!go)return '待評估那頁沒有「一張一張看」';
      go.click(); await T.sleep(300);
      var c1=card(); if(!c1)return '按了之後沒有出現卡片';
      if(document.querySelectorAll('#app article[data-fid]').length!==1)bad.push('一次不只一張');
      if(document.getElementById('tabs').offsetHeight>0)bad.push('分頁列沒有收起來(專注模式只該剩這一張)');
      var fb=c1.querySelector('.fb-btns').getBoundingClientRect();
      if(fb.bottom>innerHeight+1)bad.push('表態鈕不在畫面裡(要黏在畫面底下)');
      // 上一張、跳過跟表態鈕一起在底下黏著那段(拇指搆得到);頂端那條不黏,不會蓋住公司名
      var nav=c1.querySelector('.card>.fb [data-rvgo="1"]');
      if(!nav)bad.push('跳過鈕不在底下黏著那一段');
      else if(nav.getBoundingClientRect().bottom>innerHeight+1)bad.push('跳過鈕不在畫面裡');
      if(getComputedStyle(document.querySelector('#app .rv-top')).position==='sticky')bad.push('頂端那條還黏著(會蓋住公司名)');
      var fid=c1.getAttribute('data-fid'), s0=((await T.state())[fid]||{}).s||'';
      var want=s0==='grow'?'meh':'grow';
      c1.querySelector('.fb-b[data-s="'+want+'"]').click(); await T.idle();
      await T.until(function(){var c=card(); return c&&c.getAttribute('data-fid')!==fid;},4000);
      if((((await T.state())[fid]||{}).s||'')!==want)bad.push('表態沒存進去');
      var c2=card(); if(!c2||c2.getAttribute('data-fid')===fid)bad.push('標完沒有換下一張('+((document.querySelector('#app .rv-pos')||{}).textContent||'')+')');
      var fid2=c2&&c2.getAttribute('data-fid');
      // 標完才想補原因:上面那一條「剛才 ✍️ 補原因」點了回到剛才那張
      var chip=document.querySelector('#app [data-rvlast]');
      if(!chip)bad.push('標完沒有「剛才 ✍️ 補原因」那一條');
      else{chip.click(); await T.sleep(300);
        if(!card()||card().getAttribute('data-fid')!==fid)bad.push('點「補原因」沒回到剛才那張');
        document.querySelector('#app [data-rvgo="1"]').click(); await T.sleep(300);}
      document.querySelector('#app [data-rvgo="-1"]').click(); await T.sleep(300);
      var c3=card();
      if(!c3||c3.getAttribute('data-fid')!==fid)bad.push('按 ← 沒回到剛才那張');
      else if(!c3.querySelector('.fb-b.on[data-s="'+want+'"]'))bad.push('回到剛才那張,看不出標了什麼');
      document.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true})); await T.sleep(300);
      if(((card()||{getAttribute:function(){return '';}}).getAttribute('data-fid'))!==fid2)bad.push('→ 鍵沒有換到下一張');
      // 還原:回到那張再按一次同一顆(再按一次就是取消),待評估的卡原本沒有表態
      document.querySelector('#app [data-rvgo="-1"]').click(); await T.sleep(300);
      var c4=card(); if(c4&&c4.getAttribute('data-fid')===fid&&!s0){c4.querySelector('.fb-b[data-s="'+want+'"]').click(); await T.idle(); await T.sleep(700);}
      document.querySelector('#app [data-rvexit]').click(); await T.sleep(500);
      if(document.querySelector('#app .rv'))bad.push('按結束沒回到清單');
      if(!document.querySelector('#app .cohead'))bad.push('結束後清單不見了');
      if(document.getElementById('tabs').offsetHeight===0)bad.push('結束後分頁列沒回來');
      if(!s0&&(((await T.state())[fid]||{}).s||''))bad.push('再按一次同一顆沒有取消表態');
      return bad.join('；');
    """,
    'review_cards', {'viewports': ['desktop', 'mobile']},
))


CHECKS.append((
    '手機寬度每一頁都不會左右捲(卡片裡一段不換行的字不能把整欄撐寬)',
    r"""
      var bad=[], tabs=['none','prep','ready','ship','sent','iv','rm','cfg'];
      for(var i=0;i<tabs.length;i++){
        var t=document.querySelector('[data-tab="'+tabs[i]+'"]'); if(!t)continue;
        t.click(); await T.sleep(400);
        [].slice.call(document.querySelectorAll('#app .cogrp')).slice(0,4).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
        await T.sleep(300);
        // 示範資料的檔名都很短;塞一個真實長度、不換行的附件名,驗的是版面擋不擋得住
        // (示範卡沒有履歷,照真卡的結構在第一張卡上補一列附件)
        var c0=document.querySelector('#app article[data-fid]');
        if(c0){var r=document.createElement('div'); r.className='vd-ship';
          var pill='<span class="att-pill">Sandbox Attachment With A Deliberately Long Unbroken Title For Layout Testing Only</span>';
          r.innerHTML='<button class="rz-open ship-rz" type="button">📄 English·測試</button><span class="ship-att">📎 '+pill+pill+'</span>';
          var v=document.createElement('div'); v.className='vd y'; v.appendChild(r); c0.appendChild(v);}
        // 手機模擬下內容一撐寬,瀏覽器會把整頁縮小塞進去(innerWidth 跟著變大),要拿螢幕寬比
        var w=document.documentElement.scrollWidth, sw=screen.width;
        if(w>sw+1)bad.push(tabs[i]+' 寬 '+w+'px(螢幕 '+sw+')');
        [].slice.call(document.querySelectorAll('#app .cogrp[open]')).forEach(function(d){d.querySelector('summary').click();});
      }
      document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
      return bad.join('；');
    """,
    'no_setup', {'viewports': ['mobile']},
))


CHECKS.append((
    '公司列 ⋯「跟別家是同一家」:併完只剩一家、張數加總、存進設定的公司別名;可以復原',
    r"""
      var bad=[], name=function(h){return h.querySelector('.coname').textContent;}, cnt=function(h){return +h.querySelector('.con').textContent;};
      // (fresh_page:前面的設定頁檢查可能留下沒存的改動,那時合併會被擋——那是對的,另一條檢查在驗)
      document.querySelector('[data-tab="all"]').click(); await T.sleep(400);
      var hs=[].slice.call(document.querySelectorAll('#app .cohead')).filter(function(h){return name(h)!=='其他';});
      if(hs.length<2)return '示範看板不到兩家公司';
      var A=name(hs[0]), B=name(hs[1]), n=cnt(hs[0])+cnt(hs[1]);
      hs[0].querySelector('[data-omore]').click(); await T.sleep(100);
      var mb=hs[0].querySelector('[data-comerge]'); if(!mb)return '公司列 ⋯ 裡沒有「跟別家是同一家」';
      mb.click(); await T.sleep(300);
      var opt=[].slice.call(document.querySelectorAll('#rzmodal [data-comerge-to]')).filter(function(b){return b.getAttribute('data-comerge-to')===B;})[0];
      if(!opt)return '選單裡找不到「'+B+'」';
      opt.click(); await T.idle();
      // 併完要存設定、再重畫;等畫面上那一家真的不見(不看固定秒數)
      await T.until(function(){return [].slice.call(document.querySelectorAll('#app .cohead')).map(name).indexOf(A)<0;});
      var names=[].slice.call(document.querySelectorAll('#app .cohead')).map(name);
      if(names.indexOf(A)>=0)bad.push('併完「'+A+'」還在');
      var hb=[].slice.call(document.querySelectorAll('#app .cohead')).filter(function(h){return name(h)===B;})[0];
      if(!hb||cnt(hb)!==n)bad.push('併完「'+B+'」的張數不是 '+n+'('+(hb&&cnt(hb))+')');
      var st=await fetch('/api/settings').then(function(r){return r.json();});
      if(((((st.settings||{}).board||{}).company_alias)||{})[A.toLowerCase()]!==B)bad.push('公司別名沒存進設定');
      var un=document.querySelector('#snack .snack-undo');
      if(!un)bad.push('併完沒有「復原」');
      else{un.click(); await T.idle();
        if([].slice.call(document.querySelectorAll('#app .cohead')).map(name).indexOf(A)<0)bad.push('按了復原,「'+A+'」沒回來');
        st=await fetch('/api/settings').then(function(r){return r.json();});
        if(((((st.settings||{}).board||{}).company_alias)||{})[A.toLowerCase()])bad.push('按了復原,設定裡的別名還在');}
      document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
      return bad.join('；');
    """,
    'no_setup', {'fresh_page': True},
))


def filled_ship_case(board):
    """一張 agent 填好、停在送出前的可投遞卡(有那段對話)。"""
    import board_doc as bd
    jobs = bd.load(board)['data'].get('jobs') or []
    job = jobs[11]

    def mut(fb):
        entry = fb.setdefault(job['id'], {})
        entry.pop('approve', None)
        entry['app'] = 'ship'
        entry['form'] = {'plat': '測試', 'f': []}
        entry['ds'] = 'parked'
        entry['apply'] = {'stage': 'fill', 'issues': [], 'session': 'board-check-live', 'tab_id': '12345',
                          'delivery': {'method': 'direct_upload'}, 'at': '2026-01-01T00:00:00'}

    bd.set_fb(mut, live=board, by='board_check')
    return {'id': job['id']}


PRE['filled_ship_case'] = filled_ship_case
CHECKS.append((
    '「👀 看現在的頁面」在看板彈窗開:截得到就顯示圖;那一頁不在了就講清楚、彈窗裡直接給重填',
    r"""
      var bad=[]; document.querySelector('[data-tab="ship"]').click(); T.sync(); await T.sleep(900);
      [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
      await T.sleep(300);
      var c=T.card(P.id); if(!c)return '找不到那張卡';
      var a=c.querySelector('a[data-apshot][href^="/api/live?"]'); if(!a)return '填好的卡沒有「👀 看現在的頁面」';
      var main=c.querySelector('.ap-main'); if(!main||!main.hasAttribute('data-approve'))bad.push('填好、沒被擋的卡,主按鈕不是核准送出');
      var of=window.fetch, cv=document.createElement('canvas'); cv.width=20; cv.height=10;
      var blob=await new Promise(function(r){cv.toBlob(r);});
      window.fetch=function(u){if(String(u).indexOf('/api/live?')===0)return Promise.resolve(new Response(blob,{status:200,headers:{'Content-Type':'image/png'}})); return of.apply(this,arguments);};
      var n0=location.href; a.click(); await T.sleep(500); window.fetch=of;
      var m=document.getElementById('rzmodal');
      if(location.href!==n0)bad.push('點了換頁了(該在看板彈窗開)');
      if(!m||m.style.display!=='flex'||!m.querySelector('img.rzm-pg'))bad.push('截得到的時候彈窗裡沒有那張圖');
      if(m&&/上一輪填好、改好時/.test(m.textContent))bad.push('當場截的圖,彈窗卻說是填好時截的');
      if(m)m.querySelector('.rzm-x').click(); await T.sleep(200);
      // 用 Claude、agent 的 Chrome 正在跑代投或查應徵進度:伺服器先給那一頁填好時截的圖(X-Shot-At),彈窗要講是幾點截的、跑完再按
      window.fetch=function(u){if(String(u).indexOf('/api/live?')===0)return Promise.resolve(new Response(blob,{status:200,headers:{'Content-Type':'image/png','X-Refresh':'0','X-Shot-At':String(Math.floor(Date.now()/1000))}})); return of.apply(this,arguments);};
      T.card(P.id).querySelector('a[data-apshot]').click(); await T.sleep(500); window.fetch=of;
      m=document.getElementById('rzmodal');
      if(!m.querySelector('img.rzm-pg'))bad.push('Chrome 忙時給的填好時的圖沒顯示');
      if(!/上一輪填好、改好時/.test(m.textContent)||!/\d\d:\d\d/.test(m.textContent))bad.push('給的是填好時截的圖,彈窗沒講是幾點截的(會當成現在的頁面)');
      m.querySelector('.rzm-x').click(); await T.sleep(200);
      window.fetch=function(u){if(String(u).indexOf('/api/live?')===0)return Promise.resolve(new Response('x',{status:404})); return of.apply(this,arguments);};
      T.card(P.id).querySelector('a[data-apshot]').click(); await T.sleep(500); window.fetch=of;
      m=document.getElementById('rzmodal');
      if(!/不在了/.test(m.textContent))bad.push('那一頁不在了,彈窗沒講');
      if(!m.querySelector('[data-apshot-refill]'))bad.push('那一頁不在了,彈窗裡沒有「讓 agent 重填這張」');
      m.querySelector('.rzm-x').click(); await T.sleep(200);
      // 只是這次接不上(頁面應該還在):照伺服器的話講,不叫他重填
      window.fetch=function(u){if(String(u).indexOf('/api/live?')===0)return Promise.resolve(new Response('這次接不上,等一下再按一次',{status:503})); return of.apply(this,arguments);};
      T.card(P.id).querySelector('a[data-apshot]').click(); await T.sleep(500); window.fetch=of;
      m=document.getElementById('rzmodal');
      if(!/接不上/.test(m.textContent))bad.push('一時接不上,彈窗沒照伺服器的話講');
      if(m.querySelector('[data-apshot-refill]'))bad.push('只是一時接不上,彈窗卻叫他重填');
      m.querySelector('.rzm-x').click(); await T.sleep(200);
      // agent 正拿著這一頁在送出:伺服器不給截(409),卡上也不該有 👀
      window.fetch=function(u){if(/\/api\/rev/.test(String(u)))return of.apply(this,arguments).then(function(r){return r.json();}).then(function(v){
          v.apply={running:true,url:P.id,stage:'submit',t0:Date.now()/1000}; return new Response(JSON.stringify(v),{status:200,headers:{'Content-Type':'application/json'}});});
        return of.apply(this,arguments);};
      T.sync(); await T.sleep(900); window.fetch=of;
      var cb=T.card(P.id);
      if(cb&&/正在送出/.test(cb.textContent)&&cb.querySelector('a[data-apshot][href^="/api/live?"]'))bad.push('正在送出的卡上還有 👀(按了伺服器會回 409)');
      if(cb&&!/正在送出/.test(cb.textContent))bad.push('模擬送出中,卡上沒寫正在送出');
      T.sync(); await T.sleep(900);
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
    'filled_ship_case',
))

CHECKS.append((
    '「👀 看現在的頁面」開著時重截失敗:舊圖標明是幾點的畫面、之後截不到;頁面不在了或連續截不到就停止重截;再截到就拿掉標示',
    r"""
      var bad=[]; document.querySelector('[data-tab="ship"]').click(); T.sync(); await T.sleep(900);
      [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
      await T.sleep(300);
      var c=T.card(P.id); if(!c)return '找不到那張卡';
      var of=window.fetch, cv=document.createElement('canvas'); cv.width=20; cv.height=10;
      var blob=await new Promise(function(r){cv.toBlob(r);}), n=0, plan=[];
      var ok=function(){return Promise.resolve(new Response(blob,{status:200,headers:{'Content-Type':'image/png','X-Refresh':'0.1'}}));};
      window.fetch=function(u){if(String(u).indexOf('/api/live?')!==0)return of.apply(this,arguments);
        var k=plan[Math.min(n,plan.length-1)]; n++;
        return k==='ok'?ok():Promise.resolve(new Response(k==='gone'?'那一頁已經不在了':'這次接不上,等一下再按一次',{status:k==='gone'?404:503}));};
      var open=async function(p,wait){plan=p; n=0; T.card(P.id).querySelector('a[data-apshot]').click(); await T.sleep(wait);
        return document.getElementById('rzmodal');};
      var close=async function(m){m.querySelector('.rzm-x').click(); await T.sleep(300);};
      try{
        // 截到一張之後一直截不到:舊圖留著,但要標是哪一刻的、之後截不到;連續幾次就不再每 0.1 秒重截
        var m=await open(['ok','fail'],1500);
        if(!m.querySelector('img.rzm-pg'))bad.push('之前截到的圖不見了');
        if(!/截不到/.test(m.textContent))bad.push('之後截不到,還把舊圖當成現在的頁面(沒標是哪一刻的畫面)');
        if(!/\d\d:\d\d/.test(m.textContent))bad.push('沒寫舊圖是幾點的畫面');
        var n1=n; await T.sleep(800); if(n!==n1)bad.push('一直截不到還在重截(已經截了 '+n+' 次)');
        await close(m);
        // 那一頁不在了:第一次就停,講清楚
        m=await open(['ok','gone'],1200);
        if(!/不在了/.test(m.textContent))bad.push('重截時頁面不在了,彈窗沒講');
        if(n>2)bad.push('頁面不在了還一直重截:'+n+' 次');
        await close(m);
        // 一時截不到、之後又截到:標示拿掉
        m=await open(['ok','fail','ok'],1200);
        if(/截不到/.test(m.textContent))bad.push('又截到了,還掛著「之後截不到」');
        await close(m);
      } finally { window.fetch=of; }
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
    'filled_ship_case',
))

CHECKS.append((
    '「這一頁要你處理的」填好的那一行直接有「👀 看頁面」:按了就在看板裡彈出那一頁,不用他去找那張卡',
    r"""
      var bad=[]; document.querySelector('[data-tab="ship"]').click(); T.sync(); await T.sleep(900);
      var b=document.querySelector('.todo [data-livego="'+CSS.escape(P.id)+'"]');
      if(!b)return '「這一頁要你處理的」那一行沒有「👀 看頁面」';
      var of=window.fetch, asked=null;
      window.fetch=function(u,o){if(/\/api\/live\?/.test(String(u))){asked=String(u);
        return Promise.resolve(new Response(new Blob(['x'],{type:'image/jpeg'})));}
        return of.apply(this,arguments);};
      b.click(); await T.sleep(500); window.fetch=of;
      var m=document.getElementById('rzmodal');
      if(!asked)bad.push('按了沒有去截那一頁');
      else if(asked.indexOf(encodeURIComponent(P.id))<0)bad.push('截的不是這張:'+asked);
      if(!m||!m.querySelector('.ap-shotbox'))bad.push('沒有在看板裡彈出那一頁');
      else m.querySelector('.rzm-x').click();
      await T.sleep(200); document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
    'filled_ship_case',
))

CHECKS.append((
    '每一頁最上面「🚀 填表進度」列出填好的卡,不用點進「可以投了」;那一行直接有「👀 看頁面」',
    r"""
      var bad=[]; document.querySelector('[data-tab="none"]').click(); T.sync(); await T.sleep(600);
      var box=document.getElementById('filllistbar');
      if(!box||!box.querySelector('.fl-d'))return '別的頁最上面沒有「🚀 填表進度」';
      if(!/填好 \d+/.test(box.textContent))bad.push('標題沒寫填好幾張:'+box.textContent.slice(0,80));
      var row=box.querySelector('.fl-ok [data-livego="'+CSS.escape(P.id)+'"]');
      if(!row)bad.push('填好的那張不在清單裡,或那一行沒有「👀 看頁面」');
      if(!box.querySelector('.fl-ok [data-fillgo="'+CSS.escape(P.id)+'"]'))bad.push('卡名點不到那張卡');
      return bad.join('；');
    """,
    'filled_ship_case',
))



def human_check_ship_case(board):
    """一張可投遞卡:agent 去填,那個網站停在真人驗證(Cloudflare),填不了。"""
    import apply_tab
    import board_doc as bd
    jobs = bd.load(board)['data'].get('jobs') or []
    job = next(j for j in jobs[12:] if str(j['id']).startswith('http'))

    def mut(fb):
        entry = fb.setdefault(job['id'], {})
        entry.pop('approve', None)
        entry['app'] = 'ship'
        entry['form'] = {'plat': '測試', 'f': []}
        entry['ds'] = 'stuck'
        entry['apply'] = {'stage': 'fill', 'issues': [apply_tab.HUMAN_CHECK], 'session': 'board-check-cf',
                          'tab_id': '777', 'at': '2026-01-01T00:00:00'}

    bd.set_fb(mut, live=board, by='board_check')
    return {'id': job['id']}


PRE['human_check_ship_case'] = human_check_ship_case
CHECKS.append((
    '網站要真人驗證、agent 填不了的卡:照實講,並給「🌐 在我的瀏覽器打開」(開那個職缺頁,讓他自己投)',
    r"""
      var bad=[]; document.querySelector('[data-tab="ship"]').click(); T.sync(); await T.sleep(900);
      [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
      await T.sleep(300);
      var c=T.card(P.id); if(!c)return '找不到那張卡';
      if(!/真人驗證/.test(c.textContent))bad.push('卡上沒講是網站要真人驗證');
      var a=c.querySelector('a[data-humancheck]');
      if(!a)bad.push('沒有「🌐 在我的瀏覽器打開」');
      else{if(a.getAttribute('href')!==P.id)bad.push('打開的不是這個職缺頁:'+a.getAttribute('href'));
        if(a.getAttribute('target')!=='_blank')bad.push('不是開在新分頁(會把看板換掉)');}
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
    'human_check_ship_case',
))

def unfilled_ship_case(board):
    """一張可投遞卡:表單答案都確認過,但 agent 還沒填過(沒有 apply)。"""
    import board_doc as bd
    jobs = bd.load(board)['data'].get('jobs') or []
    if len(jobs) < 11:
        raise ValueError('示範看板少於十一張卡')
    job = jobs[10]

    def mut(fb):
        entry = fb.setdefault(job['id'], {})
        for k in ('apply', 'approve', 's', 'ds'):
            entry.pop(k, None)
        entry['app'] = 'ship'
        entry['form'] = {'plat': '測試', 'f': []}

    bd.set_fb(mut, live=board, by='board_check')
    return {'id': job['id']}


PRE['unfilled_ship_case'] = unfilled_ship_case
CHECKS.append((
    '可投遞:agent 還沒填過的卡不給核准,原因寫在卡上(不是只在滑鼠提示),主按鈕是「讓 agent 填這張」',
    r"""
      var bad=[]; document.querySelector('[data-tab="ship"]').click(); T.sync(); await T.sleep(900);
      [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
      await T.sleep(300);
      var c=T.card(P.id); if(!c)return '找不到那張可投遞卡';
      var ap=c.querySelector('[data-approve]');
      if(!ap)bad.push('沒有核准鈕(該有,只是按不下去)');
      else if(!ap.disabled)bad.push('還沒填過就能核准(真的送出會叫不回那段對話,整筆作廢)');
      var why=c.querySelector('.ap-why');
      if(!why||!/還沒填這張/.test(why.textContent))bad.push('卡上沒寫為什麼不能核准');
      var main=c.querySelector('.ap-main');
      if(!main||!/填這張/.test(main.textContent)||(main.getAttribute('data-runone')||'').indexOf('apply|')!==0)
        bad.push('主按鈕不是「讓 agent 填這張」');
      var vd=c.querySelector('.vd-b');
      if(vd&&vd.offsetParent&&ap&&vd.getBoundingClientRect().top<ap.getBoundingClientRect().top)bad.push('核准鈕排在履歷切換下面(要捲過履歷才按得到)');
      return bad.join('；');
    """,
    'unfilled_ship_case',
))


CHECKS.append((
    '設定頁:四組分好;改了東西存檔列亮起來、黏在畫面底下;沒存就切走會提醒',
    r"""
      var bad=[]; document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      var g=[].slice.call(document.querySelectorAll('#app .cfg-grp')).map(function(x){return x.textContent;});
      if(g.join('|')!=='你的資料|找缺與判斷|投遞|Agent 與系統')bad.push('分組不對:'+g.join('|'));
      var sv=document.getElementById('cfg-save'); if(!sv)return bad.concat('沒有存檔列').join('；');
      if(sv.classList.contains('dirty'))bad.push('沒改東西存檔列就亮著');
      var ag=document.querySelector('details[data-fold="cfg:agent"]'); ag.querySelector('summary').click(); await T.sleep(200);
      var inp=document.querySelector('[data-cfa-model="0"]'), v0=inp.value;
      T.type(inp,v0+'x'); await T.sleep(100);
      if(!document.getElementById('cfg-save').classList.contains('dirty'))bad.push('改了東西存檔列沒亮');
      if(getComputedStyle(document.getElementById('cfg-save')).position!=='sticky')bad.push('改了東西存檔列沒有黏在底下');
      document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
      if(!/設定頁有改動還沒存/.test(T.snack()))bad.push('沒存就切走,沒有提醒');
      document.querySelector('[data-tab="cfg"]').click(); await T.sleep(600);
      inp=document.querySelector('[data-cfa-model="0"]'); if(inp)T.type(inp,v0);
      document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
      return bad.join('；');
    """,
    'no_setup', {'fresh_page': True},   # 從乾淨的頁面開始:前面的檢查可能留下沒存的設定改動
))


# 資料夾沒在存版、舊資料沒有退回點沒轉(#301):不擋找缺,但環境檢查要攤開、標 ⚠️、寫怎麼處理,設定頁版本紀錄那列也要寫
CHECKS.append((
    '設定頁環境檢查:資料夾版本紀錄沒在存、舊資料沒轉,環境檢查攤開標 ⚠️ 寫處理方式,版本紀錄那列也寫',
    r"""
      var bad=[], f0=window.fetch;
      window.fetch=function(u,o){
        if(String(u)!=='/api/settings'||(o&&o.method))return f0.apply(this,arguments);
        return f0.apply(this,arguments).then(function(r){return r.json();}).then(function(st){
          st.doctor={ok:true,checks:[{key:'python',label:'Python',ok:true,detail:'3.12'},
            {key:'folder_history',label:'資料夾版本紀錄',ok:false,required:false,warn:true,
             detail:'bc-history-reason',fix:'bc-history-fix'}]};
          st.git_history={available:true,message:'版本紀錄未啟用：bc-history-reason',fix:'bc-history-fix',
                          conversion:'bc-conversion-skipped',pending:false};
          return new Response(JSON.stringify(st),{headers:{'Content-Type':'application/json'}});});
      };
      try{
        document.querySelector('[data-tab="cfg"]').click(); await T.idle(); await T.sleep(300);
        if(document.querySelector('details[data-fold="cfg:doctor"]'))bad.push('有要處理的提醒,環境檢查還是收成一行');
        var doc=Array.from(document.querySelectorAll('.cfg-check')).filter(function(e){return /環境檢查/.test(e.textContent);})[0];
        var text=doc?doc.textContent:'';
        if(!/有要處理的提醒/.test(text))bad.push('環境檢查標題沒說有要處理的提醒');
        if(!/⚠️ 資料夾版本紀錄｜bc-history-reason/.test(text))bad.push('版本紀錄那一列沒標 ⚠️ 或沒寫原因');
        if(!/處理方式：bc-history-fix/.test(text))bad.push('版本紀錄那一列沒寫處理方式');
        var all=document.body.textContent;
        if(!/⚠️ 版本紀錄未啟用：bc-history-reason/.test(all)||!/bc-conversion-skipped/.test(all)
           ||(all.match(/處理方式：bc-history-fix/g)||[]).length<2)
          bad.push('設定頁版本紀錄那列沒寫原因、沒轉的轉換或處理方式');
      }finally{
        window.fetch=f0;
        document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      }
      return bad.join('；');
    """,
    'no_setup', {'fresh_page': True},
))


# 設定頁手上的是打開時讀的那一份、存的時候送整份:別處剛存的值(找缺那一列的分鐘數、另一個分頁)不能被蓋回去
CHECKS.append((
    '設定頁存檔:別處剛存的找缺分鐘數,回設定頁改別的再存不會蓋回去;手上是舊的就擋下來、給「重新讀取」;第一次打開只讀一次設定',
    r"""
      var bad=[], realTimeout=window.setTimeout, orig=null;
      var post=function(u,b){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(function(r){return r.json();});};
      var minutes=async function(){var st=await fetch('/api/settings').then(function(r){return r.json();}); return (st.effective.search||{}).find_minutes;};
      var flags=function(){var sd=document.querySelector('details[data-fold="cfg:search"]'); if(sd&&!sd.open)sd.querySelector('summary').click();
        return document.querySelector('textarea[data-cfl="search.flag_words"]');};
      try{
        orig=(await fetch('/api/settings').then(function(r){return r.json();})).settings;
        // 第一次打開只讀一次(伺服器每讀一次就跑一遍環境檢查,開 codex、claude 查登入)
        var gets=0, f0=window.fetch;
        window.fetch=function(u,o){if(String(u)==='/api/settings'&&!(o&&o.method))gets++; return f0.apply(this,arguments);};
        try{document.querySelector('[data-tab="cfg"]').click(); await T.idle(); await T.sleep(300);}finally{window.fetch=f0;}
        if(gets!==1)bad.push('第一次打開設定頁讀了 '+gets+' 次設定');
        // 1. 去「🆕 新職缺」把找缺那一列的分鐘數改成 37(同一支 API),回設定頁改別的、按儲存
        document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
        await post('/api/settings/find_minutes',{minutes:37});
        document.querySelector('[data-tab="cfg"]').click(); await T.idle(); await T.sleep(300);
        T.type(flags(),'bc-flag-1');
        window.setTimeout=function(fn,ms){return ms===700?0:realTimeout.apply(window,arguments);};   // 存好後的重新載入先擋掉
        document.querySelector('[data-cfsave]').click(); await T.idle(); await T.sleep(200);
        var m1=await minutes(); if(m1!==37)bad.push('回設定頁改別的再存,找缺分鐘數被蓋回 '+m1);
        // 2. 設定頁改了還沒存,別處又把分鐘數改成 41:按儲存要擋下來,不能拿手上的舊那份蓋回去
        T.type(flags(),'bc-flag-2');
        await post('/api/settings/find_minutes',{minutes:41});
        document.querySelector('[data-cfsave]').click(); await T.idle(); await T.sleep(200);
        var m2=await minutes(); if(m2!==41)bad.push('手上是舊的那份也照存,分鐘數被蓋回 '+m2);
        var said=T.snack()+' '+(document.getElementById('cfg-save')||{}).textContent;
        if(!/別的地方改過/.test(said))bad.push('擋下來沒講原因(看到:'+said.trim().slice(0,60)+')');
        var rl=document.querySelector('[data-cfreload]');
        if(!rl)bad.push('沒有「重新讀取」可以按');
        else{rl.click(); await T.idle(); await T.sleep(200);
          if(document.getElementById('cfg-save').classList.contains('dirty'))bad.push('按了重新讀取還顯示有改動');
          if(flags().value.indexOf('bc-flag-2')>=0)bad.push('按了重新讀取,畫面還是舊的那份');}
      }finally{
        await T.idle(); window.setTimeout=realTimeout;
        if(orig)await post('/api/settings',{settings:orig});
        document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      }
      return bad.join('；');
    """,
    'no_setup', {'fresh_page': True},
))


CHECKS.append((
    '設定頁存檔:重讀設定時沒存的改動留著(按開機自動啟動會重讀);有沒存的改動時卡片上合併公司先擋',
    r"""
      var bad=[], before=(await fetch('/api/settings').then(function(r){return r.json();})).settings;
      var note=function(){var pf=document.querySelector('details[data-fold="cfg:preferences"]'); if(pf&&!pf.open)pf.querySelector('summary').click();
        return document.querySelector('textarea[data-cft="preferences_custom"]');};
      document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      var ta=note(); T.type(ta,ta.value+'\n還沒存的一句 bc-keep');
      // 開機自動啟動那顆按了會重讀設定(副本裡伺服器只回「這是副本」,不會真的裝)
      var sv=document.querySelector('[data-cfservice]'); if(!sv)return '找不到開機自動啟動的按鈕';
      sv.click(); await T.idle(); await T.sleep(300);
      if(note().value.indexOf('bc-keep')<0)bad.push('按了開機自動啟動,打到一半的字不見了');
      if(!document.getElementById('cfg-save').classList.contains('dirty'))bad.push('重讀之後存檔列變成「沒有改動」');
      // 卡片上「🔗 跟別家是同一家」會存一次設定:有沒存的改動時先擋,不能連他還沒按儲存的一起存、也不能丟掉
      document.querySelector('[data-tab="all"]').click(); await T.sleep(300);
      var mg=document.querySelector('#app [data-comerge]');
      if(!mg)bad.push('找不到「跟別家是同一家」');
      else{mg.click(); await T.sleep(200);
        var to=document.querySelector('#rzmodal [data-comerge-to]');
        if(!to)bad.push('合併的選單沒有列公司');
        else{to.click(); await T.idle(); await T.sleep(300);
          var after=(await fetch('/api/settings').then(function(r){return r.json();})).settings;
          if(JSON.stringify((after.board||{}).company_alias||{})!==JSON.stringify((before.board||{}).company_alias||{}))
            bad.push('設定頁有沒存的改動,合併照樣存進設定');
          if(!/還沒存/.test(T.snack()))bad.push('合併被擋沒講原因(看到:'+T.snack().slice(0,40)+')');}}
      document.querySelector('[data-tab="cfg"]').click(); await T.sleep(300);
      if(note().value.indexOf('bc-keep')<0)bad.push('合併之後回設定頁,打到一半的字不見了');
      // 擋合併的訊息叫他「先存或放棄」:設定頁要有「放棄改動」,按了回到沒有改動、剛打的字丟掉
      var dc=document.getElementById('cfg-discard');
      if(!dc||dc.hidden)bad.push('有沒存的改動,設定頁卻沒有「放棄改動」');
      else{dc.click(); await T.idle(); await T.sleep(300);
        if(document.getElementById('cfg-save').classList.contains('dirty'))bad.push('按了放棄改動,存檔列還是有改動');
        if(note().value.indexOf('bc-keep')>=0)bad.push('按了放棄改動,剛打的字還在');}
      var cm=document.getElementById('rzmodal'); if(cm&&cm.style.display==='flex'){var x=cm.querySelector('.rzm-x'); if(x)x.click();}
      await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:before})});
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
    'no_setup', {'fresh_page': True},   # 留下沒存的改動:下一條要從乾淨的頁面開始(fresh_page)
))


def lang_case(_board):
    """一份履歷有中文和英文兩個檔(示範資料只有中文)。透過設定 API 放好,原本的設定回傳給檢查自己還原。"""
    settings = sandbox('/api/settings', timeout=60)['settings']
    original = copy.deepcopy(settings)
    files = {}
    for lang in ('zh', 'en'):
        rel = f'resume/bc-lang/{lang}/bc-lang.md'
        sandbox('/api/file?path=' + rel, f'# {lang}'.encode('utf-8'), method='PUT', headers={'User-Agent': 'board-check'},
                timeout=60, raw=True)
        files[lang] = rel
    resume = settings.setdefault('resume', {})
    resume['langs'] = ['zh', 'en']
    resume['resumes'] = list(resume.get('resumes') or []) + [
        {'id': 'bc-lang', 'name': '雙語檢查版', 'enabled': True, 'files': files}]
    sandbox('/api/settings', {'settings': settings}, timeout=60)
    return {'original': original}


PRE['lang_case'] = lang_case
CHECKS.append((
    '設定頁細節:取消勾一種語言,那個語言的檔一起拿掉(可以復原),存得進去',
    r"""
      var bad=[], realTimeout=window.setTimeout;
      var box=function(){var d=document.querySelector('details[data-fold="cfg:resumes"]'); if(d&&!d.open)d.querySelector('summary').click();
        return document.querySelector('input[data-cflang="en"]');};
      try{
        document.querySelector('[data-tab="cfg"]').click(); await T.idle();
        var en=box(); if(!en||!en.checked)return '示範設定沒有勾英文';
        en.click(); await T.sleep(200);
        if(document.querySelector('[data-cfup="resume|bc-lang|en"]'))bad.push('取消勾英文,英文那一列還在');
        var undo=document.querySelector('#snack.on .snack-undo');
        if(!undo)bad.push('拿掉英文版的檔沒有「復原」');
        else{undo.click(); await T.sleep(200);
          if(!box().checked||!document.querySelector('[data-cfup="resume|bc-lang|en"]'))bad.push('按了復原,英文和它的檔沒回來');
          box().click(); await T.sleep(200);}
        window.setTimeout=function(fn,ms){return ms===700?0:realTimeout.apply(window,arguments);};
        document.querySelector('[data-cfsave]').click(); await T.idle(); await T.sleep(200);
        if(/沒存成/.test(T.snack()))bad.push('取消勾英文後存不進去:'+T.snack().slice(0,80));
        var st=await fetch('/api/settings').then(function(r){return r.json();});
        var r=((st.settings.resume||{}).resumes||[]).filter(function(x){return x.id==='bc-lang';})[0]||{};
        if((r.files||{}).en)bad.push('存好的設定裡英文版的檔還在');
        if(!(r.files||{}).zh)bad.push('中文版的檔被一起拿掉了');
      }finally{
        await T.idle(); window.setTimeout=realTimeout;
        await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:P.original})});
        document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      }
      return bad.join('；');
    """,
    'lang_case', {'fresh_page': True},
))


CHECKS.append((
    '設定頁細節:找缺與判斷的做法跟改履歷的規則分開列;存找缺做法時講的是找缺做法',
    r"""
      var bad=[], orig=(await fetch('/api/settings').then(function(r){return r.json();})).settings;
      try{
        document.querySelector('[data-tab="cfg"]').click(); await T.idle();
        var rs=document.querySelector('details[data-fold="cfg:research-skills"]'); if(!rs.open)rs.querySelector('summary').click(); await T.sleep(100);
        document.querySelector('[data-cfresearch-copy="judge"]').click();
        var nm=document.querySelector('[data-cfresearch-name="judge"]'); T.type(nm,'bc 判斷做法');
        document.querySelector('[data-cfresearch-save="judge"]').click();
        await T.until(function(){return /已存好|失敗/.test(T.snack());},15000); var said=T.snack();
        if(/改履歷的規則/.test(said)||!/找缺與判斷的做法/.test(said))bad.push('存找缺做法時講成:'+said.slice(0,50));
        await T.idle(); await T.sleep(300);
        var res=document.querySelector('details[data-fold="cfg:resumes"]'); if(!res.open)res.querySelector('summary').click();
        var sk=document.querySelector('details[data-fold="cfg:skills"]'); if(!sk.open)sk.querySelector('summary').click(); await T.sleep(100);
        var rsel=document.querySelector('select[data-cf^="resume.resumes."][data-cf$=".skill"]');
        if(!rsel)bad.push('找不到履歷的「改履歷的規則」選單');
        else if([].some.call(rsel.options,function(o){return /bc 判斷做法/.test(o.textContent);}))bad.push('履歷的「改履歷的規則」選單列出了找缺與判斷的做法');
        if(/bc 判斷做法/.test(sk.textContent))bad.push('「🪄 改履歷的規則」清單列出了找缺與判斷的做法');
        var jsel=document.querySelector('select[data-cf="research.skills.judge"]');
        if(!jsel||![].some.call(jsel.options,function(o){return /bc 判斷做法/.test(o.textContent);}))bad.push('判斷的選單沒有剛存的那份');
        var msg=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({settings:Object.assign({},orig,{research:{skills:{judge:'custom/skills/bc-missing.md'}}})})}).then(function(r){return r.json();});
        if(/skill|custom\/skills/.test(msg.msg||''))bad.push('找不到做法的訊息用了 skill 或資料夾路徑:'+(msg.msg||'').slice(0,60));
      }finally{
        await T.idle();
        await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:orig})});
        document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      }
      return bad.join('；');
    """,
    'no_setup', {'fresh_page': True},
))


CHECKS.append((
    '設定頁細節:改了又改回原樣、刪掉再按「復原」,存檔列回到「沒有改動」',
    r"""
      var bad=[], clean=function(){return !document.getElementById('cfg-save').classList.contains('dirty');};
      document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      var ag=document.querySelector('details[data-fold="cfg:agent"]'); if(!ag.open)ag.querySelector('summary').click(); await T.sleep(100);
      var inp=document.querySelector('[data-cfa-model="0"]'), v0=inp.value;
      T.type(inp,v0+'x'); await T.sleep(50);
      if(clean())bad.push('改了東西存檔列沒亮');
      T.type(inp,v0); await T.sleep(50);
      if(!clean())bad.push('改回原樣還顯示有改動');
      var res=document.querySelector('details[data-fold="cfg:resumes"]'); if(!res.open)res.querySelector('summary').click(); await T.sleep(100);
      var del=document.querySelector('[data-cfresdel]');
      if(!del)bad.push('沒有履歷可以刪');
      else{del.click(); await T.sleep(200);
        var undo=document.querySelector('#snack.on .snack-undo'); if(!undo)bad.push('刪履歷沒有復原');
        else{undo.click(); await T.sleep(200); if(!clean())bad.push('刪掉再按復原,還顯示有改動');}}
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
    'no_setup', {'fresh_page': True},
))


CHECKS.append((
    '設定頁細節:手機上點設定頁的輸入框不會自動放大(字級至少 16px)',
    r"""
      document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      document.querySelectorAll('#app details.fold').forEach(function(d){d.open=true;}); await T.sleep(200);
      var small=[].slice.call(document.querySelectorAll('#app .cfg-in')).map(function(x){
        return x.offsetParent?parseFloat(getComputedStyle(x).fontSize):16;}).filter(function(px){return px<16;});
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return small.length?small.length+' 個輸入框字級小於 16px(例:'+small[0]+'px),iOS 點下去會放大':'';
    """,
    'no_setup', {'fresh_page': True, 'viewports': ['mobile']},
))


CHECKS.append((
    '設定頁細節:執行環境、思考強度、速度的選單寫白話(Codex、Claude Code,不是 claude-code、xhigh);快速講明比較耗額度',
    r"""
      var bad=[];
      document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      var ag=document.querySelector('details[data-fold="cfg:agent"]'); if(!ag.open)ag.querySelector('summary').click(); await T.sleep(100);
      var txt=function(sel){var s=document.querySelector(sel); return s?[].map.call(s.options,function(o){return o.textContent;}):[];};
      var rt=txt('[data-cfa-runtime="0"]'), ef=txt('[data-cfa-effort="0"]'), sp=txt('[data-cfa-speed="0"]');
      if(rt.join('|')!=='Codex|Command Code|Claude Code')bad.push('執行環境寫成:'+rt.join('、'));
      if(ef.some(function(x){return /^[a-z]+$/.test(x);}))bad.push('思考強度寫成代號:'+ef.join('、'));
      if(sp.length&&!sp.some(function(x){return /快速/.test(x)&&/額度/.test(x);}))bad.push('「快速」沒講比較耗額度:'+sp.join('、'));
      var vals=[].map.call(document.querySelector('[data-cfa-runtime="0"]').options,function(o){return o.value;});
      if(vals.join('|')!=='codex|command-code|claude-code')bad.push('選項的值被改了(存進設定的要是代號):'+vals.join('、'));
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
    'no_setup', {'fresh_page': True},
))


CHECKS.append((
    '設定頁細節:上傳檔案時其他改動有錯存不進去,講清楚檔案傳好了、設定沒存成(不是「上傳失敗」)',
    r"""
      var bad=[], orig=(await fetch('/api/settings').then(function(r){return r.json();})).settings;
      try{
        document.querySelector('[data-tab="cfg"]').click(); await T.idle();
        var fl=document.querySelector('details[data-fold="cfg:flow"]'); if(!fl.open)fl.querySelector('summary').click(); await T.sleep(100);
        T.type(document.querySelector('[data-cf="flow.fill_max"]'),'很多張');   // 別的地方先填錯、還沒存
        var res=document.querySelector('details[data-fold="cfg:resumes"]'); if(!res.open)res.querySelector('summary').click(); await T.sleep(100);
        var inp=document.querySelector('input[data-cfup^="resume|"]'); if(!inp)return '找不到履歷的上傳鈕';
        var dt=new DataTransfer(); dt.items.add(new File(['# bc'],'bc-upload.md',{type:'text/markdown'}));
        inp.files=dt.files; inp.dispatchEvent(new Event('change',{bubbles:true}));
        await T.until(function(){return /傳好了|失敗|沒存成/.test(T.snack());},15000); await T.idle(); await T.sleep(200);
        var said=T.snack();
        if(/^上傳失敗/.test(said)||!/傳好了/.test(said)||!/沒存成/.test(said))bad.push('畫面說:'+said.slice(0,80));
        if(!/bc-upload\.md/.test(res.textContent+document.querySelector('details[data-fold="cfg:resumes"]').textContent))bad.push('那一列沒顯示剛傳好的檔');
      }finally{
        await T.idle();
        await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:orig})});
        document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      }
      return bad.join('；');
    """,
    'no_setup', {'fresh_page': True},   # 留下沒存的改動:下一條要從乾淨的頁面開始
))


CHECKS.append((
    '設定頁:agent 的 Chrome 從哪個設定檔複製,從清單選(顯示 Chrome 上看到的名字、裝了哪個擴充功能;可以不複製),存的是資料夾名',
    r"""
      var bad=[], f0=window.fetch;
      // 這台機器的 Chrome 設定檔換成假的兩個(CI 上沒有 Chrome 設定檔;本機也不拿真的名字來比)
      window.fetch=function(u,o){var r=f0.apply(this,arguments);
        if(String(u)!=='/api/settings'||(o&&o.method==='POST'))return r;
        return r.then(function(x){return x.json();}).then(function(d){
          d.chrome_profiles=[{dir:'Profile 7',name:'工作用',ext:['codex']},{dir:'Default',name:'個人',ext:[]}];
          return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json'}});});};
      try{
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      var ag=document.querySelector('details[data-fold="cfg:agent"]'); if(!ag.open)ag.querySelector('summary').click(); await T.sleep(200);
      var sel=document.querySelector('select[data-cf="browser.profile"]');
      if(!sel)return '設定檔還是要自己打字,不是選單';
      var txt=[].map.call(sel.options,function(o){return o.textContent;}).join('|');
      if(!/工作用\(已裝 Codex 擴充功能\)/.test(txt))bad.push('選項沒顯示名字和擴充功能:'+txt);
      if(/chrome:\/\/version/.test(ag.textContent))bad.push('說明還叫人去 chrome://version 抄資料夾名');
      if(![].some.call(sel.options,function(o){return o.value==='';}))bad.push('沒有「不複製」可以選');
      if(/新增一個設定檔|右上角新增/.test(ag.textContent))bad.push('說明還叫人在自己的 Chrome 裡新增設定檔(agent 現在是自己一個 Chrome)');
      var v0=sel.value; sel.value='Profile 7'; sel.dispatchEvent(new Event('change',{bubbles:true})); await T.sleep(100);
      if(!document.getElementById('cfg-save').classList.contains('dirty'))bad.push('選了設定檔存檔列沒亮');
      sel=document.querySelector('select[data-cf="browser.profile"]'); if(sel){sel.value=v0; sel.dispatchEvent(new Event('change',{bubbles:true}));}
      if(document.querySelector('[data-cfb="browser.show_window"],[data-cf="browser.tool"]'))bad.push('還有「叫到我面前」或「操作方式」這種會讓 agent 的 Chrome 跳出來的選項');
      } finally { window.fetch=f0; document.querySelector('[data-tab="none"]').click(); await T.sleep(300); }
      return bad.join('；');
    """,    'no_setup', {'fresh_page': True},   # 設定頁第一次打開才讀 /api/settings:重新載入頁面,假的設定檔清單才接得上
))


CHECKS.append((
    '設定頁:只能一個 agent 用 Chrome;新增的 agent 不會也勾上;只畫勾了的那一家的「連接」',
    r"""
      var bad=[];
      try{
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      var ag=document.querySelector('details[data-fold="cfg:agent"]'); if(!ag.open)ag.querySelector('summary').click(); await T.sleep(200);
      var boxes=function(){return [].slice.call(document.querySelectorAll('[data-cfa-browser]'));};
      var checked=function(){return boxes().filter(function(x){return x.checked;}).length;};
      document.querySelector('[data-cfagentadd]').click(); await T.sleep(200);
      if(checked()>1)bad.push('新增的 agent 也自動勾了「用它操作 Chrome」:勾了 '+checked()+' 個');
      var last=boxes()[boxes().length-1]; last.click(); await T.sleep(200);
      if(checked()!==1||!boxes()[boxes().length-1].checked)bad.push('勾另一個之後不是只剩它一個:勾了 '+checked()+' 個');
      var rt=document.querySelectorAll('[data-cfa-runtime]'); var sel=rt[rt.length-1];
      sel.value='claude-code'; sel.dispatchEvent(new Event('change',{bubbles:true})); await T.sleep(200);
      if(!document.querySelector('[data-cfbrowser="claude"]'))bad.push('勾的是 Claude,卻沒有「連接 Claude」');
      if(document.querySelector('[data-cfbrowser="setup"]'))bad.push('勾的是 Claude,還畫「連接 Codex」(讓人以為兩個都要連)');
      var ag2=document.querySelector('details[data-fold="cfg:agent"]');
      if(/用 Claude 代投也要連/.test(ag2.textContent))bad.push('還寫著用 Claude 也要連 Codex');
      boxes()[boxes().length-1].click(); await T.sleep(200);
      if(checked()!==0)bad.push('取消勾選後還有勾著的');
      if(!/還沒有 agent 勾/.test(document.querySelector('details[data-fold="cfg:agent"]').textContent))bad.push('一個都沒勾時沒提醒填表會停著');
      } finally { document.querySelector('[data-tab="none"]').click(); await T.sleep(300); }
      return bad.join('；');
    """,    'no_setup', {'fresh_page': True},
))


CHECKS.append((
    '設定頁「🔌 連接 Codex」:有填好等他核對的頁時,先問他(那幾頁會不見);他不確定就不關,確定了才帶 force 再送',
    r"""
      var bad=[], f0=window.fetch, c0=window.confirm, sent=[], asked=[], answer=false;
      window.fetch=function(u,o){
        if(String(u)==='/api/settings/browser'){var b=JSON.parse(o.body); sent.push(b);
          return Promise.resolve(new Response(JSON.stringify(b.force?{ok:true,msg:'連上了'}:
            {ok:false,confirm:true,msg:'還有 2 頁填好等你核對,這幾頁會不見。確定要連接嗎?'}),
            {status:b.force?200:409,headers:{'Content-Type':'application/json'}}));}
        var r=f0.apply(this,arguments);
        if(String(u)!=='/api/settings'||(o&&o.method==='POST'))return r;
        return r.then(function(x){return x.json();}).then(function(d){   // 勾的是 Codex,才有「連接 Codex」
          d.effective.agent.agents=[{id:'codex',runtime:'codex',model:'',effort:'max',speed:'standard',browser:true}];
          return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json'}});});};
      window.confirm=function(m){asked.push(m); return answer;};
      try{
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      var ag=document.querySelector('details[data-fold="cfg:agent"]'); if(!ag.open)ag.querySelector('summary').click(); await T.sleep(200);
      var btn=document.querySelector('[data-cfbrowser="setup"]'); if(!btn)return '勾 Codex 卻沒有「連接 Codex」';
      btn.click(); await T.sleep(600);
      if(asked.length!==1||!/不見/.test(asked[0]||''))bad.push('有等他的頁,沒先問他就連接');
      if(sent.length!==1||sent[0].force)bad.push('他沒確定,卻還是送出了關掉重開:'+JSON.stringify(sent));
      btn=document.querySelector('[data-cfbrowser="setup"]');
      if(!btn||btn.disabled)bad.push('他不確定之後,連接按鈕沒恢復');
      answer=true; sent=[]; asked=[]; btn.click(); await T.sleep(600);
      if(sent.length!==2||sent[0].force||!sent[1].force)bad.push('他確定之後沒帶 force 再送一次:'+JSON.stringify(sent));
      } finally { window.fetch=f0; window.confirm=c0; document.querySelector('[data-tab="none"]').click(); await T.sleep(300); }
      return bad.join('；');
    """,    'no_setup', {'fresh_page': True},
))


CHECKS.append((
    '設定頁「🔌 連接 Codex」那一列:✅ 寫最近一次確認連得上的日期(不是記過就算連上);連接中寫最多一分半(實際要等那麼久)',
    r"""
      var bad=[], f0=window.fetch;
      window.fetch=function(u,o){
        if(String(u)==='/api/settings/browser')return new Promise(function(){});      // 連接中:一直不回
        var r=f0.apply(this,arguments);
        if(String(u)!=='/api/settings'||(o&&o.method==='POST'))return r;
        return r.then(function(x){return x.json();}).then(function(d){
          d.effective.agent.agents=[{id:'codex',runtime:'codex',model:'',effort:'max',speed:'standard',browser:true}];
          d.browser_ok='2026-09-29T17:06:02';
          return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json'}});});};
      try{
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      var ag=document.querySelector('details[data-fold="cfg:agent"]'); if(!ag.open)ag.querySelector('summary').click(); await T.sleep(200);
      var btn=document.querySelector('[data-cfbrowser="setup"]'); if(!btn)return '勾 Codex 卻沒有「連接 Codex」';
      var row=btn.closest('.cfg-row').textContent;
      if(!/9\/29 確認連得上/.test(row))bad.push('Codex 那一列沒寫最近一次確認連得上的日期:'+row.slice(0,60));
      if(/已連接/.test(row))bad.push('還寫「已連接」(只是記過,外掛斷線也這樣寫)');
      btn.click(); await T.sleep(300);
      btn=document.querySelector('[data-cfbrowser="setup"]');
      if(!/一分半/.test(btn.textContent))bad.push('連接中的按鈕寫「'+btn.textContent+'」,實際最多要等一分半');
      } finally { window.fetch=f0; document.querySelector('[data-tab="none"]').click(); await T.sleep(300); }
      return bad.join('；');
    """,    'no_setup', {'fresh_page': True},
))


CHECKS.append((
    '幽靈職缺:判斷看出來的,卡片上標「👻 可能是幽靈職缺」和理由',
    r"""
      var hit=[];
      for(const tab of ['none','like','meh','dislike','grow','all','prep','ready','ship','sent']){
        var t=document.querySelector('[data-tab="'+tab+'"]'); if(!t)continue; t.click(); await T.sleep(250);
        document.querySelectorAll('#app .cohead').forEach(function(h){if(!h.parentNode.open)h.click();}); await T.sleep(250);
        hit=[].slice.call(document.querySelectorAll('#app .repost-note')).filter(function(n){return /幽靈職缺/.test(n.textContent);});
        if(hit.length)break;}
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      if(!hit.length)return '有幽靈職缺標記的示範卡,卡片上看不到標記';
      if(!/刊登已經/.test(hit[0].textContent))return '標記沒有寫理由';
      return '';
    """,
))


def speech_case(board):
    # 題庫放三題只有一段逐字稿的:整段英文 150 字、中文 42 字、中文夾英文
    import board_doc as bd, interview_bank as ib
    en, zh = ' '.join(['word'] * 150), '字' * 42
    def mut(data, _fb):
        bank = data.get('bank') or {}
        for i, (t, s) in enumerate((('估時英文', en), ('估時中文', zh), ('估時中文夾英文', zh + ' LLM Ops'))):
            bank = ib.apply(bank, 'put', {'id': f'bc_speech{i}', 't': t, 'cat': '其他', 'script': s})
        data['bank'] = bank
    bd.set_data(mut, live=board)
    return {'ids': ['bc_speech0', 'bc_speech1', 'bc_speech2']}


PRE['speech_case'] = speech_case
CHECKS.append((
    '面試講稿估時:有中文的段落照原本數字元,整段英文數單字,英文稿不會被估成好幾倍',
    r'''
      var bad=[], t=document.querySelector('[data-tab="iv"]'); if(!t)return '沒有 🎤 面試準備那一籤';
      t.click(); await T.sleep(300);
      // 有中文的段落照原本數非空白字元(使用者的語速是照這個量的),夾的英文也算字元
      [['英文 150 字',60],['中文 42 字',10],['中文夾英文',11]].forEach(function(c,i){
        var row=document.querySelector('#app details.ivrow[data-iv="'+P.ids[i]+'"]'), d=row&&row.querySelector('[data-ivdur]');
        if(!d){bad.push(c[0]+':題目上沒有估時'); return;}
        var m=/≈(\d+):(\d+)/.exec(d.textContent), got=m?(+m[1])*60+(+m[2]):-1;
        if(Math.abs(got-c[1])>1)bad.push(c[0]+' 估 '+d.textContent+',應該約 '+c[1]+' 秒');});
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    ''',
    'speech_case', {'fresh_page': True},
))


CHECKS.append((
    '一張一張看(寬螢幕):左邊是這一輪的清單,點哪一列就看哪一張;勾「只看標了沒寫原因的」只剩那幾列',
    r'''
      var bad=[], card=function(){return document.querySelector('#app .rv-card article[data-fid]');};
      if(P&&P.added)await T.resync();
      document.querySelector('[data-tab="none"]').click(); await T.sleep(400);
      document.querySelector('#app [data-rvstart]').click(); await T.sleep(300);
      var list=document.querySelector('#app .rv-list');
      if(!list||!list.offsetWidth)return '寬螢幕沒有左邊的清單';
      var rows=list.querySelectorAll('[data-rvat]'), n=+((document.querySelector('#app .rv-pos')||{}).textContent||'/0').split('/')[1];
      if(rows.length!==n)bad.push('清單 '+rows.length+' 列,這一輪是 '+n+' 張');
      if(document.querySelectorAll('#app article[data-fid]').length!==1)bad.push('清單的列被畫成卡片了');
      var cur=list.querySelector('.rv-row.cur'); if(!cur||cur.getAttribute('data-rvat')!=='0')bad.push('目前這張沒有標在清單上');
      var fid0=card().getAttribute('data-fid'), s0=((await T.state())[fid0]||{}).s||'';
      var want=s0==='meh'?'grow':'meh';
      card().querySelector('.fb-b[data-s="'+want+'"]').click(); await T.idle();
      await T.until(function(){var c=card(); return c&&c.getAttribute('data-fid')!==fid0;},4000);
      var r0=document.querySelector('#app [data-rvat="0"]');
      if(!r0||r0.querySelector('.rv-row-m').textContent!=={meh:'😐',grow:'💪'}[want])bad.push('標完清單那一列看不出標了什麼');
      // 點回第一張補原因
      r0.click(); await T.sleep(300);
      if(!card()||card().getAttribute('data-fid')!==fid0)bad.push('點清單那一列沒換到那一張');
      await T.sleep(800);
      if(!card()||card().getAttribute('data-fid')!==fid0)bad.push('點回去之後又被自動換走');
      var f=document.querySelector('#app [data-rvnoreason]'); f.click(); await T.sleep(300);
      var left=[].slice.call(document.querySelectorAll('#app [data-rvat]')).map(function(x){return x.getAttribute('data-rvat');});
      if(!s0&&left.join(',')!=='0')bad.push('勾了只看沒寫原因的,剩下的列不對:'+left.join(','));
      document.querySelector('#app [data-rvnoreason]').click(); await T.sleep(200);
      if(!s0){card().querySelector('.fb-b[data-s="'+want+'"]').click(); await T.idle(); await T.sleep(700);}
      document.querySelector('#app [data-rvexit]').click(); await T.sleep(400);
      return bad.join('；');
    ''',
    'review_cards', {'viewports': ['wide']},
))


CHECKS.append((
    '看完的公司列(整列淡掉)打開 ⋯:選單不跟著變淡、不被下面幾列蓋住,點得到',
    r'''
      var bad=[]; document.querySelector('[data-tab="all"]').click(); await T.sleep(500);
      var hs=document.querySelectorAll('#app summary.cohead'); if(hs.length<2)return '公司列不到兩列,蓋不蓋得住測不出來';
      var head=hs[0]; head.classList.add('done');
      var mb=head.querySelector('[data-omore]'); if(!mb)return '公司列沒有 ⋯';
      mb.click(); await T.sleep(200);
      var m=head.querySelector('.more-m'); if(!m||m.hidden)return '⋯ 點了沒打開';
      var op=1; for(var x=m;x&&x!==document.body;x=x.parentElement)op*=+getComputedStyle(x).opacity;
      if(op<0.99)bad.push('選單是半透明的(opacity '+op.toFixed(2)+')');
      m.scrollIntoView({block:'center'}); await T.sleep(100);
      [].slice.call(m.querySelectorAll('button')).forEach(function(b){
        var r=b.getBoundingClientRect(), hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
        if(!hit||!b.contains(hit))bad.push('「'+b.textContent.trim()+'」被 '+(hit?hit.tagName+'.'+hit.className:'畫面外')+' 蓋住,點不到');});
      document.body.click(); await T.sleep(100); head.classList.remove('done');
      return bad.join('；');
    ''',
))


def settings_preview_case(_board):
    """示範資料沒有可預覽的履歷:透過設定 API 放一份假的 .md。頁面要重新載入才會重讀設定
    (前面的檢查打開過設定頁,頁面上留著舊的那份),原本的設定回傳給檢查自己還原。"""
    url = shot.SB_URL[0]
    settings = sandbox('/api/settings', timeout=60)['settings']
    original = copy.deepcopy(settings)
    put = urllib.request.Request(url + '/api/file?path=resume/bc-preview.md', data='# 示範履歷'.encode('utf-8'),
                                 method='PUT', headers={'User-Agent': 'board-check'})
    urllib.request.urlopen(put, timeout=60)
    resume = settings.setdefault('resume', {})
    lang = (resume.get('langs') or ['zh'])[0]
    resume['resumes'] = list(resume.get('resumes') or []) + [
        {'id': 'bc-preview', 'name': '預覽檢查', 'enabled': True, 'files': {lang: 'resume/bc-preview.md'}}]
    save = urllib.request.Request(url + '/api/settings', data=json.dumps({'settings': settings}, ensure_ascii=False).encode('utf-8'),
                                  method='POST', headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(save, timeout=60)
    return {'original': original}


PRE['settings_preview_case'] = settings_preview_case
CHECKS.append((
    '看板自己的檔(設定頁「👁 預覽」、卡片上的附件)在看板裡彈窗看,不另開分頁、不下載',
    r'''
      var bad=[];
      document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      document.querySelectorAll('#app details.fold').forEach(function(d){d.open=true;}); await T.sleep(300);
      if(document.querySelector('#app a[href*="source-preview"]'))bad.push('預覽還是一個另開分頁的連結');
      var b=document.querySelector('#app [data-srcpv*="bc-preview"]');
      if(!b)bad.push('設定頁找不到那份履歷的「👁 預覽」');
      else{b.click();
        // 馬上看:預覽圖還沒產出時伺服器回 404,彈窗會自己關掉改成一句提示,那是另一件事
        var ov=document.getElementById('rzmodal');
        if(!ov||ov.style.display!=='flex'||!ov.querySelector('.rzm-pg'))bad.push('點「👁 預覽」沒有在看板裡彈出預覽');
        await T.sleep(600);
        if(ov&&ov.style.display==='flex')ov.querySelector('.rzm-x').click();
        else if(!/還沒有 PDF 預覽/.test(T.snack()))bad.push('預覽圖讀不到,沒有說一聲(只剩破圖或什麼都沒有)');}
      await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:P.original})});
      // 每一頁:指向看板自己檔案(/api/…)的連結,不能只是另開分頁(看頁面、預覽有自己的彈窗處理)
      var tabs=['none','all','prep','ready','ship','sent'];
      for(var i=0;i<tabs.length;i++){var t=document.querySelector('[data-tab="'+tabs[i]+'"]'); if(!t)continue;
        t.click(); await T.sleep(300); T.open(0); await T.sleep(300);
        [].slice.call(document.querySelectorAll('#app a[target="_blank"][href^="/api/"]')).forEach(function(a){
          if(!a.hasAttribute('data-apshot')&&!a.hasAttribute('data-srcpv'))bad.push(tabs[i]+' 頁「'+a.textContent.trim()+'」另開分頁');});}
      document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
      return bad.join('；');
    ''',
    'settings_preview_case', {'fresh_page': True},
))


CHECKS.append((
    '可投遞頁:卡片排在參考資料(母版)前面,驗收總表收成一行,不擋在卡片前面',
    r"""
      var bad=[]; document.querySelector('[data-tab="ship"]').click(); await T.sleep(500);
      var list=[].slice.call(document.querySelectorAll('#app .applyhd')).filter(function(h){return /可投遞/.test(h.textContent)&&h.tagName==='H2';})[0];
      var ms=document.querySelector('#app .masters-d');
      if(list&&ms&&(ms.compareDocumentPosition(list)&Node.DOCUMENT_POSITION_FOLLOWING))bad.push('參考資料還排在卡片前面');
      var sf=document.querySelector('#app .stbar.bad');
      // 有清單的總表要收起來(details 沒打開);只有一行字(沒有清單)的本來就是一行
      if(sf&&(sf.tagName==='DETAILS'?sf.open:!!sf.querySelector('ul')))bad.push('驗收總表一進來就攤開');
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
))

def weak_submission_evidence_case(board):
    """在看板副本安排只有送出頁證據的已投遞卡。"""
    import board_doc as bd
    parsed = bd.load(board)
    jobs = parsed['data'].get('jobs') or []
    if not jobs:
        raise ValueError('示範看板沒有職缺,無法驗送出頁證據標記')
    job = jobs[0]
    evidence = '已查信箱與可讀平台應徵紀錄,仍未找到確認信或平台紀錄;目前只有送出頁證據'

    def mut(fb):
        fb.setdefault(job['id'], {}).update(app='sent', ds='sent', sent_by='manual', sent_at='2026-09-20', ev=evidence)

    bd.set_fb(mut, live=board, by='board_check')
    return {'id': job['id'], 'evidence': evidence}


PRE['weak_submission_evidence_case'] = weak_submission_evidence_case
CHECKS.append((
    '只有送出頁證據時顯示弱證據標記',
    r"""
      var bad=[], tab=document.querySelector('[data-tab="sent"]');
      if(!tab)return '找不到已投遞分頁';
      tab.click(); await T.sync(); await T.sleep(600);
      [...document.querySelectorAll('#app .cohead')].forEach(function(h){if(!h.parentNode.open)h.click();});
      await T.sleep(300);
      var card=T.card(P.id);
      if(!card)return '找不到已投遞的測試卡';
      var mark=card.querySelector('.ev-weak');
      if(!mark||!mark.textContent.includes('只有送出頁證據'))bad.push('尚未查到第二來源時沒有顯示弱證據標記');
      var state=await T.state();
      if(!(state[P.id]||{}).ev)bad.push('確認第二來源前,測試卡缺少弱證據資料');
      return bad.join('；');
    """,
    'weak_submission_evidence_case',
))


def checked_reply_date_case(board):
    """在副本放一張已完整查過來源但沒有回音的卡。"""
    import board_doc as bd
    parsed = bd.load(board)
    jobs = parsed['data'].get('jobs') or []
    if not jobs:
        raise ValueError('示範看板沒有職缺,無法驗查過回音日期')
    job = jobs[0]
    checked_at = '2026-09-25'

    def mut(fb):
        fb.setdefault(job['id'], {}).update(
            app='sent', ds='sent', sent_by='manual', sent_at='2026-09-20',
            replies={'items': [], 'at': checked_at},
        )

    bd.set_fb(mut, live=board, by='board_check')
    return {'id': job['id'], 'label': '9/25 查過，沒有回音'}


PRE['checked_reply_date_case'] = checked_reply_date_case
CHECKS.append((
    '沒有回音的卡片顯示最近查過日期',
    r"""
      var tab=document.querySelector('[data-tab="sent"]');
      if(!tab)return '找不到已投遞分頁';
      tab.click(); await T.sync(); await T.sleep(500);
      [...document.querySelectorAll('#app .cohead')].forEach(function(h){if(!h.parentNode.open)h.click();});
      await T.sleep(250);
      var card=T.card(P.id);
      return !card?'找不到已投遞的測試卡':
        (card.textContent.includes(P.label)?'':'卡片沒有顯示「'+P.label+'」');
    """,
    'checked_reply_date_case',
))


def confirmed_submission_evidence_case(board):
    """經過正式回音 parser 與套用函式,在副本加入一封確認信。"""
    import board_doc as bd
    import reply_run as rr
    parsed_board = bd.load(board)
    feedback = json.loads(parsed_board['fb'])
    jobs = parsed_board['data'].get('jobs') or []
    job = next((item for item in jobs if (feedback.get(item['id']) or {}).get('ev')), None)
    if not job:
        weak_submission_evidence_case(board)
        parsed_board = bd.load(board)
        feedback = json.loads(parsed_board['fb'])
        jobs = parsed_board['data'].get('jobs') or []
        job = next((item for item in jobs if (feedback.get(item['id']) or {}).get('ev')), None)
    if not job:
        raise ValueError('找不到只有送出頁證據的測試卡')
    url = job['id']
    source_ref = 'email:board-check'
    finding = {
        'url': url, 'source_ref': source_ref, 'source_type': 'email',
        'source': 'Gmail 合成確認信', 'date': '2026-09-21',
        'summary': '合成確認信', 'link': 'https://mail.google.com/mail/u/0/#all/board-check',
        'kind': 'confirm',
    }
    result = rr.parse_result(
        {'checked': [url], 'findings': [finding]}, [url], source_types={source_ref: 'email'},
    )
    bd.set_fb(lambda fb: rr.apply_findings(fb, result.findings), live=board, by='board_check')
    return {'id': url, 'source_ref': source_ref}


PRE['confirmed_submission_evidence_case'] = confirmed_submission_evidence_case
CHECKS.append((
    '確認信經過回音流程後清除弱證據標記',
    r"""
      var bad=[], tab=document.querySelector('[data-tab="sent"]');
      if(!tab)return '找不到已投遞分頁';
      tab.click(); await T.sync(); await T.sleep(900);
      [...document.querySelectorAll('#app .cohead')].forEach(function(h){if(!h.parentNode.open)h.click();});
      await T.sleep(300);
      var card=T.card(P.id), state=await T.state(), evidence=(state[P.id]||{});
      if(evidence.ev)bad.push('回音流程已確認來源,弱證據資料仍存在');
      if((evidence.replies||{}).items?.[0]?.source_ref!==P.source_ref)
        bad.push('回音流程沒有保留合成確認信來源');
      if(card&&card.querySelector('.ev-weak'))bad.push('回音流程確認來源後看板仍顯示弱證據標記');
      if(!card)bad.push('找不到確認信測試卡');
      return bad.join('；');
    """,
    'confirmed_submission_evidence_case',
))


# 快版(--fast)跳過的:這幾條要等副本上的假流程(job_fake)真的跑完,或等代投核准後 8 秒的反悔期,
# 一條就 10~60 秒,合起來佔完整版一半以上的時間。規矩本身沒變,只是改介面時先不等它們。
SLOW=('代投:','幫你填表:','代投修改:','找新職缺:','找新職缺面板','找缺紀錄:','跑準備區:')


def flow_on(board):
    """開自動流程,並放一份「驗收過、沒有問題」的結果(示範看板沒有;沒有的話推進可投遞一律被擋,那是對的)。"""
    import board_doc as bd
    bd.set_data(lambda d, _fb: d.__setitem__('status', {'schema_version': 2, 'at': '2026-01-01 00:00',
                                                         'checked_links': True, 'issues': []}), live=board)
    # 一條他確認過的答案:檢查裡把它標成「改過、網頁待重打」,看自動流程會不會叫 agent 重打
    # 挑一張還沒碰過的卡,先放一份用到它的表單(agent 填表時照記那幾欄):答案改了才有欄位可標重打。
    # 只放這一張:同一份副本後面的檢查還要用其他卡
    def put(d):
        fb = d['fb']
        fb.setdefault('__ans__', []).append({'k': 'bc_refix', 'q': '檢查用的題目', 'zh': '測試', 'v': 'test'})
        fid = next((j['id'] for j in d['data']['jobs'] if not j.get('bk') and not j.get('dead') and not fb.get(j['id'])), None)
        if fid:
            fb[fid] = {'form': {'plat': '沙箱', 'f': [{'q': '檢查用的題目', 'src': 'bank', 'k': 'bc_refix'}]}}
        return fid
    fid = bd.rewrite(put, board, by='board_check')
    shot.flow(like_to_prep=True, auto_prep=True, auto_advance=True, auto_fill=True)
    return {'fid': fid}


PRE['flow_on'] = flow_on
FLOW_OFF_JS = r"""
      var cur=await fetch('/api/settings').then(function(r){return r.json();}), se=cur.settings||{};
      se.flow={like_to_prep:false,auto_prep:false,auto_advance:false,auto_fill:false,replies_at:''};
      await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:se})});
"""
CHECKS.append((
    '自動流程:按 👍 的新卡自己準備、進可投遞、讓 agent 填好就停在送出前,不會自己送出;填完換了履歷會擋核准、自己重填一次;答案改過會等他停手再自己重打',
    r"""
      var bad=[], m, s;
      try{
      document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
      var fid=P.fid; if(!fid)return '副本上沒有還沒碰過的卡';
      [].slice.call(document.querySelectorAll('#app .cohead')).forEach(function(h){if(!h.parentNode.open)h.click();});
      await T.sleep(300); if(!T.card(fid))return '找不到那張卡';
      T.mood(fid,'like'); await T.sleep(1800);
      s=await T.state(); if((s[fid]||{}).app!=='prep')bad.push('按 👍 沒有加入準備(app='+(s[fid]||{}).app+')');
      var ok=false;
      for(var i=0;i<45;i++){await T.sleep(1000); s=await T.state(); m=s[fid]||{};
        if(m.app==='sent'){bad.push('自己送出了'); break;}
        if(m.app==='ship'&&m.ds==='parked'&&m.form){ok=true; break;}}
      if(!ok)return bad.concat('45 秒內沒有自己走到可投遞、填好(app='+(m||{}).app+')').join('；');
      await T.sleep(6000); s=await T.state(); m=s[fid]||{};
      if(m.app!=='ship'||m.approve)bad.push('填好之後自己往下走了(app='+m.app+(m.approve?',核准了':'')+')');
      T.sync(); await T.sleep(1800);
      document.querySelector('[data-tab="ship"]').click(); await T.sleep(400);
      [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open&&d.querySelector('[data-k]'))d.querySelector('summary').click();});
      await T.sleep(300);
      var card=T.card(fid); if(!card)return bad.concat('可投遞頁找不到那張卡').join('；');
      var main=card.querySelector('.ap-main'); if(!main||!main.hasAttribute('data-approve'))bad.push('填好的卡主按鈕不是「✅ 核准送出」');
      // 填完才換履歷(看板上換履歷／語言、收下客製版都送「換檔」事件 → 上傳的是舊檔):核准要擋,自動流程重填一次
      await T.ds(fid,[['files_changed',{why:'測試:履歷換了'}]]);
      s=await T.state();
      var p=((await fetch('/api/next').then(function(r){return r.json();}))[fid]||{}).send;   // 後台的下一步:確認過的能不能送
      if(!/履歷換過了/.test(p||''))bad.push('換了履歷,核准規則沒擋('+p+')');
      var re=false; for(var k=0;k<25;k++){await T.sleep(1000); s=await T.state(); if(!((s[fid]||{}).apply||{}).stale&&(s[fid]||{}).ds!=='stale'){re=true; break;}}
      if(!re)bad.push('換了履歷之後沒有自己重填');
      if(((s[fid]||{}).app)==='sent')bad.push('重填之後自己送出了');
      // 答案改過(表單上用到那條的欄位標 refill):卡上寫「會自動重打」、不列進要他處理的;停手一下就叫回同一隻 agent 重打
      // (表單上那一欄是 flow_on 先放的,agent 填表時照記;看板改答案只送 {refill:答案鍵},表單只有後台寫,#308)
      m=s[fid];
      if(!((m.form||{}).f||[]).some(function(x){return x.k==='bc_refix';}))bad.push('填好的表單上沒有檢查用的那一欄');
      body={__rev__:1,__events__:[{refill:'bc_refix'}]};
      await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      T.sync(); await T.sleep(1800);
      card=T.card(fid);
      if(!card||!/會自動照新答案重打/.test(card.textContent))bad.push('答案改過,卡上沒寫會自動重打('+(card?card.textContent.replace(/\s+/g,' ').slice(-120):'找不到卡')+')');
      if([].slice.call(document.querySelectorAll('[data-todogo]')).some(function(x){return x.getAttribute('data-todogo')===fid;}))
        bad.push('會自動重打的卡還列在要你處理的');
      var fx=false; for(var k2=0;k2<20;k2++){await T.sleep(1000); s=await T.state(); m=s[fid]||{};
        if(m.app==='sent'){bad.push('重打之後自己送出了'); break;}
        if(((m.apply||{}).stage==='fix')&&!((m.form||{}).f||[]).some(function(x){return x.refill;})){fx=true; break;}}
      if(!fx)bad.push('答案改過之後沒有自己重打(apply.stage='+((m.apply||{}).stage)+')');
      // 核准之後改語言:舊核准須撤銷,自動流程才能照新的履歷檔重填,之後仍要重新核准。
      if(fx){
        await T.until(async function(){s=await T.state(); m=s[fid]||{}; return m.ds==='parked';},15000);
        // 先挑一份履歷(沒挑的話換語言也不會換到要寄的檔案):換了檔,自動流程會先重填一次,等它停著等你
        card=T.card(fid); var rz=card&&card.querySelector('.vd-b[data-vd]:not(.on):not([disabled])');
        if(rz){rz.click(); await T.idle();
          await T.until(async function(){s=await T.state(); m=s[fid]||{}; return m.ds==='parked'&&m.resume_id;},25000);}
        await T.ds(fid,[['confirm',{approve:{at:'2026-01-01T00:00:00',snap:{},round:(m.apply||{}).at}}]]);
        // agent 那一輪還在跑(頁面還沒拿到它結束)時換語言的按鈕是停用的:等它停用解除
        var other=await T.until(async function(){T.sync(); await T.sleep(400); card=T.card(fid);
          return card&&card.querySelector('.vd-b.lg:not(.on):not([disabled])');},15000);
        if(!other)bad.push('找不到可切換的語言');
        else{
          other.click();   // 要寄的檔案當下問後台,回來後真的變了才送換檔事件:等它存進去
          await T.until(async function(){await T.idle(); s=await T.state(); m=s[fid]||{}; return !m.approve&&(m.apply||{}).stale;},10000);
          if(m.approve)bad.push('換語言後仍沿用舊核准');
          if(!(m.apply||{}).stale)bad.push('換語言後沒有標記舊上傳檔失效');
        }
      }
      } finally {
""" + FLOW_OFF_JS + r"""
      }
      return bad.join('；');
    """,
    'flow_on', {'fresh_page': True},
))


def reply_retry_on(board):
    """每天自動查應徵進度開著(時間設 23:59,檢查中不會真的開跑),今天排程那一輪已經跑過、再試過 1 次。"""
    import board_doc as bd, datetime
    today = datetime.date.today().isoformat()
    before = {}

    def mut(fb):
        before['a'] = fb.get('__auto__')
        a = dict(fb.get('__auto__') or {'since': today + 'T00:00:00', 'skip': [], 'tried': [], 'seen': {}})
        a.update(replies_day=today, replies_retry={'day': today, 'n': 1})
        fb['__auto__'] = a
    bd.set_fb(mut, live=board, by='board_check')
    shot.flow(replies_at='23:59')
    return {'auto0': before['a'], 'today': today}


PRE['reply_retry_on'] = reply_retry_on
CHECKS.append((
    '已投出下游:排程的查應徵進度今天沒跑成,那一列照實寫幾點自動再試、第幾次;再試滿 3 次寫「不再試了、明天照排程」',
    r"""
      var of=window.fetch, fake=null, bad=[];
      window.fetch=function(u){var p=of.apply(this,arguments);
        if(fake&&String(u).indexOf('/api/rev')===0)return p.then(function(r){return r.json();}).then(function(v){
          v.replies=fake; return new Response(JSON.stringify(v),{status:200,headers:{'Content-Type':'application/json'}});});
        return p;};
      function bar(){return (document.getElementById('replybar')||{}).textContent||'';}
      try{
        document.querySelector('[data-tab="sent"]').click(); await T.sleep(300);
        var now=Date.now()/1000, mid=new Date(); mid.setHours(0,0,1,0);
        fake={phase:'failed',running:false,msg:'甲 查回音 agent 沒完成',n:2,done:0,finished_at:Math.max(mid.getTime()/1000,now-3500)};
        if(!await T.until(function(){T.sync(); return bar().indexOf('甲 查回音')>=0;},6000))bad.push('那一列看不到沒跑成');
        else if(!/之後自動再試\(今天第 2\/3 次\)/.test(bar()))bad.push('沒寫幾點自動再試、第幾次:'+bar().slice(0,120));
        fake={phase:'died',running:false,n:2,done:0,t0:Math.max(mid.getTime()/1000,now-3500)};
        if(!await T.until(function(){T.sync(); return /停掉了/.test(bar());},6000))bad.push('那一列看不到停掉了');
        else if(!/自動再試/.test(bar()))bad.push('跑到一半死掉的沒寫會自動再試');
        var st=await T.state(), a=JSON.parse(JSON.stringify(st.__auto__)); a.replies_retry={day:P.today,n:3};
        var q={__rev__:1,__base__:{__auto__:st.__auto__},__auto__:a};
        await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q)});
        fake={phase:'failed',running:false,msg:'乙 查回音 agent 沒完成',n:2,done:0,finished_at:Math.max(mid.getTime()/1000,now-3500)};
        if(!await T.until(function(){T.sync(); return bar().indexOf('乙 查回音')>=0&&/不再試了/.test(bar());},6000))
          bad.push('再試滿 3 次沒寫「不再試了」:'+bar().slice(0,120));
        else if(/之後自動再試/.test(bar()))bad.push('再試滿 3 次還寫會自動再試');
      }finally{window.fetch=of;
        var st2=await T.state(), q2={__rev__:1,__base__:{__auto__:st2.__auto__===undefined?null:st2.__auto__},__auto__:P.auto0};
        await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q2)});
""" + FLOW_OFF_JS + r"""
      }
      T.sync(); await T.idle(); document.querySelector('[data-tab="none"]').click();
      return bad.join('；');
    """,
    'reply_retry_on', {'fresh_page': True},
))


def like_prep_only(_board):
    """只開「👍 順手加入準備」,其他自動流程關著:卡會停在準備區,不會被假流程接走。"""
    shot.flow(like_to_prep=True)
    return {}


PRE['like_prep_only'] = like_prep_only
# 兩條連著跑,中間重新載入頁面(#72):「是 👍 順手加的」以前只記在頁面裡,重整後取消 👍 卡會卡在準備區。
CHECKS.append((
    '👍 順手加入準備:按 👍 進準備區,記在卡片標記裡',
    r"""
      document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
      var c=T.open(); if(!c)return '找不到卡';
      var fid=c.getAttribute('data-fid');
      T.mood(fid,'like'); await T.idle();
      var m=(await T.state())[fid]||{};
      sessionStorage.setItem('bc_likeprep',fid);
      if(m.app!=='prep')return '按 👍 沒有加入準備(app='+m.app+')';
      return '';
    """,
    'like_prep_only', {'fresh_page': True},
))
CHECKS.append((
    '👍 順手加入準備:重新整理後取消 👍,卡一起退出準備區',
    r"""
      var bad=[], fid=sessionStorage.getItem('bc_likeprep');
      try{
      if(!fid)return '上一條沒有留下卡';
      document.querySelector('[data-tab="prep"]').click(); await T.sleep(300);
      document.querySelectorAll('#app .cohead').forEach(function(h){if(!h.parentNode.open)h.click();});
      if(!T.card(fid))return '重整後準備區找不到那張卡';
      T.mood(fid,'like'); await T.idle();
      var m=(await T.state())[fid]||{};
      if(m.app)bad.push('重整後取消 👍,卡還留在「'+m.app+'」');
      if(m.likeprep)bad.push('取消 👍 後標記沒清掉');
      } finally {
""" + FLOW_OFF_JS + r"""
      document.querySelector('[data-tab="none"]').click();
      }
      return bad.join('；');
    """,
    'no_setup', {'fresh_page': True, 'with_previous': True},   # 接著上一條按的 👍(同一組、照順序跑)
))


def risky_case(board):
    """一張待評估的卡標成原頁打不開(可能已關)。"""
    import board_doc as bd
    parsed = bd.load(board)
    fb, jobs = json.loads(parsed['fb']), parsed['data']['jobs']
    job = next(j for j in jobs if not (fb.get(j['id']) or {}).get('s') and not (fb.get(j['id']) or {}).get('app')
               and not (fb.get(j['id']) or {}).get('rm') and not j.get('bk'))

    def put(data, _fb):
        for j in data['jobs']:
            if j['id'] == job['id']:
                j['dead'] = True
    bd.set_data(put, live=board)
    return {'id': job['id']}


PRE['risky_case'] = risky_case
CHECKS.append((
    '待評估:可能已關的缺(原頁打不開、掛超過半年、死線已過)一次收進已移除,可以復原;一張一張看時排在最後',
    r"""
      var bad=[]; T.sync(); await T.sleep(1500);
      document.querySelector('[data-tab="none"]').click(); await T.sleep(400);
      var b=document.querySelector('#app [data-riskrm]'); if(!b)return '待評估沒有「可能已關:收掉」';
      b.click(); await T.sleep(1600);
      var s=await T.state(); if(!(s[P.id]||{}).rm)bad.push('收掉之後那張沒有進已移除');
      var un=document.querySelector('#snack .snack-undo'); if(!un)bad.push('收掉之後沒有「復原」');
      else{un.click(); await T.sleep(1600); s=await T.state(); if((s[P.id]||{}).rm)bad.push('按了復原,那張還在已移除');}
      document.querySelector('#app [data-rvstart]').click(); await T.sleep(300);
      var last=null; for(var i=0;i<400;i++){var c=document.querySelector('#app .rv-card article'); if(!c)break; last=c.getAttribute('data-fid');
        document.querySelector('#app [data-rvgo="1"]').click(); await T.sleep(20);}
      if(last!==P.id)bad.push('一張一張看時,可能已關的那張沒排在最後');
      document.querySelector('#app [data-rvexit]').click(); await T.sleep(300);
      return bad.join('；');
    """,
    'risky_case',
))


def closed_ship_case(board):
    """一張可投遞卡,驗收查到它已經下架。"""
    import board_doc as bd
    parsed = bd.load(board)
    job = parsed['data']['jobs'][12]

    def put_fb(fb):
        fb.setdefault(job['id'], {})['app'] = 'ship'
        fb[job['id']].pop('rm', None)
    bd.set_fb(put_fb, live=board, by='board_check')
    bd.set_data(lambda d, _fb: d.__setitem__('status', {'schema_version': 2, 'at': '2026-01-01 00:00', 'checked_links': True,
        'issues': [{'jid': job['id'], 't': 'x', 'stage': 'ship', 'msg': '職缺已下架(HTTP 回 404/410 或重導離開職缺頁)'}]}), live=board)
    return {'id': job['id']}


PRE['closed_ship_case'] = closed_ship_case
CHECKS.append((
    '可投遞:驗收查到已經下架的卡,一次收進已移除,可以復原',
    r"""
      var bad=[]; T.sync(); await T.sleep(1500);
      document.querySelector('[data-tab="ship"]').click(); await T.sleep(400);
      var b=document.querySelector('#app [data-closedrm="ship"]'); if(!b)return '可投遞頁沒有「已經下架:收掉」';
      b.click(); await T.sleep(1600);
      var s=await T.state(); if(!(s[P.id]||{}).rm)bad.push('收掉之後那張沒有進已移除');
      var un=document.querySelector('#snack .snack-undo'); if(!un)bad.push('收掉之後沒有「復原」');
      else{un.click(); await T.sleep(1600); s=await T.state(); if((s[P.id]||{}).rm)bad.push('按了復原,那張還在已移除');}
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
    'closed_ship_case',
))
def judged_closed_case(board):
    """一張待你決定的卡,驗收時 agent 判斷職缺關了(程式核對不了,只核對它抄的那一句在頁面上)(#317)。"""
    import board_doc as bd
    parsed = bd.load(board)
    job = parsed['data']['jobs'][13]

    def put_fb(fb):
        fb.setdefault(job['id'], {})['app'] = 'ready'
        fb[job['id']].pop('rm', None)
        fb[job['id']].pop('judged_no', None)
    bd.set_fb(put_fb, live=board, by='board_check')
    bd.set_data(lambda d, _fb: d.__setitem__('status', {'schema_version': 2, 'at': '2026-01-01 00:00', 'checked_links': True,
        'issues': [{'jid': job['id'], 't': 'x', 'stage': 'ready', 'kind': 'closed', 'judged': 'This position has been filled',
                    'msg': 'agent 判斷職缺已關閉,頁面原文:「This position has been filled」'}]}), live=board)
    return {'id': job['id']}


PRE['judged_closed_case'] = judged_closed_case
CHECKS.append((
    '待你決定:agent 判斷職缺關了,卡上標明是 agent 判斷、附原文;按「不對,職缺還在」就不再因為它擋,可以復原',
    r"""
      var bad=[]; T.sync(); await T.sleep(1500);
      async function open(){document.querySelector('[data-tab="ready"]').click(); await T.sleep(400);
        [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
        await T.sleep(200); return T.card(P.id);}
      var c=await open(); if(!c)return '待你決定頁找不到那張卡';
      var t=c.textContent;
      if(t.indexOf('agent 判斷職缺已關閉')<0)bad.push('卡上沒標明是 agent 判斷');
      if(t.indexOf('This position has been filled')<0)bad.push('卡上沒附 agent 抄的原文');
      var b=c.querySelector('[data-judgedno]'); if(!b)return bad.concat(['卡上沒有「不對,職缺還在」']).join('；');
      b.click(); await T.idle();
      var s=await T.state(); if(!((s[P.id]||{}).judged_no||{}).closed)bad.push('按了之後沒記下「他說職缺還在」');
      c=await open(); if(c&&c.textContent.indexOf('agent 判斷職缺已關閉')>=0&&c.querySelector('[data-judgedno]'))
        bad.push('按了之後還因為 agent 判斷關了而擋');
      var un=document.querySelector('#snack .snack-undo'); if(!un)bad.push('沒有「復原」');
      else{un.click(); await T.idle(); s=await T.state();
        if(((s[P.id]||{}).judged_no||{}).closed)bad.push('按了復原,「他說職缺還在」還在');
        c=await open(); if(!(c&&c.querySelector('[data-judgedno]')))bad.push('復原後沒有回到被 agent 判斷擋住');}
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
    'judged_closed_case',
))
CHECKS.append((
    '可投遞:頁首說幾張沒過驗收、原因在卡上,那幾張卡上真的寫了原因,也按不了確認送出',
    r"""
      var bad=[]; T.sync(); await T.sleep(1500);
      document.querySelector('[data-tab="ship"]').click(); await T.sleep(400);
      [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
      await T.sleep(300);
      var txt=document.getElementById('app').textContent, held=+((txt.match(/(\d+) 張(沒過驗收|還不能往下走)/)||[])[1]||0);
      if(held!==1)bad.push('頁首寫 '+held+' 張擋住,實際 1 張');
      var c=T.card(P.id);
      if(!c)bad.push('找不到那張下架的卡');
      else{if(!/職缺已下架/.test(c.textContent))bad.push('頁首說原因寫在卡上,卡上卻沒寫');
        var ab=c.querySelector('[data-approve]'); if(ab&&!ab.disabled)bad.push('驗收沒過(已下架)的卡還按得了確認送出');}
      document.querySelector('[data-tab="none"]').click(); await T.sleep(200);
      return bad.join('；');
    """,
    'closed_ship_case',
))


def attachment_preview_case(board):
    """示範資料沒有履歷和附件:透過設定 API 放一份假履歷、一份假附件(PDF),一張待你決定的卡指定用那份履歷
    (沒挑履歷的卡不列附件),頁面重新載入後卡上才會出現附件 pill。原本的設定回傳給檢查自己還原。"""
    settings = sandbox('/api/settings', timeout=60)['settings']
    original = copy.deepcopy(settings)
    for rel in ('resume/bc-rz.pdf', 'resume/bc-att.pdf'):
        sandbox('/api/file?path=' + rel, b'%PDF-1.4\n%%EOF\n', method='PUT', headers={'User-Agent': 'board-check'},
                timeout=60, raw=True)
    resume = settings.setdefault('resume', {})
    lang = (resume.get('langs') or ['zh'])[0]
    resume['resumes'] = [{'id': 'bc-rz', 'name': '檢查履歷', 'enabled': True, 'files': {lang: 'resume/bc-rz.pdf'}}]
    resume['attachments'] = [{'id': 'bc-att', 'name': '檢查附件', 'enabled': True, 'files': {lang: 'resume/bc-att.pdf'}}]
    sandbox('/api/settings', {'settings': settings}, timeout=60)
    import board_doc as bd
    card = _ready_cards(board, 1)[0]
    bd.set_fb(lambda f: f[card].update(resume_id='bc-rz', lang=lang), live=board, by='board_check')
    return {'original': original}


PRE['attachment_preview_case'] = attachment_preview_case
CHECKS.append((
    '卡片上的附件點了在看板裡彈窗看,不另開分頁',
    r"""
      var bad=[], pill=null;
      for(var t of ['ready','ship']){
        document.querySelector('[data-tab="'+t+'"]').click(); await T.sleep(500);
        document.querySelectorAll('#app details.cogrp').forEach(function(d){d.open=true;}); await T.sleep(500);
        pill=document.querySelector('#app .att-pill.att-preview'); if(pill)break;}
      if(!pill)bad.push('待你決定、可投遞都找不到可預覽的附件');
      else{
        if(pill.getAttribute('target')==='_blank')bad.push('附件還是另開分頁的連結');
        pill.click();
        var ov=document.getElementById('rzmodal');
        if(!ov||ov.style.display!=='flex'||!ov.querySelector('.rzm-pg,.rzm-pdf'))bad.push('點附件沒有在看板裡彈出預覽');
        await T.sleep(600);
        if(ov&&ov.style.display==='flex')ov.querySelector('.rzm-x').click();
        else if(!/還沒有 PDF 預覽/.test(T.snack()))bad.push('附件預覽讀不到,沒有說一聲');}
      await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:P.original})});
      document.querySelector('[data-tab="none"]').click(); await T.sleep(300);
      return bad.join('；');
    """,
    'attachment_preview_case', {'fresh_page': True},
))











# ---- 已投出之後(查應徵進度、結果、沒下文、再投一次、退回):每條都拿示範看板上那張還在等回音的已投出卡來做,
# 做完放回原樣(後面還有檢查要用它)。
DOWN = {}


def _down(board, fn=None):
    """那張卡第一次看到時的樣子記著;fn(fb, id) 從原樣開始改,回 {id, orig} 給檢查最後放回去。"""
    import board_doc as bd
    if 'id' not in DOWN:
        p = bd.load(board)
        fb = json.loads(p['fb'])
        job = next(j for j in p['data']['jobs']
                   if (fb.get(j['id']) or {}).get('app') == 'sent' and not fb[j['id']].get('oc'))
        DOWN.update(id=job['id'], job=job, orig=fb[job['id']])
    jid = DOWN['id']
    if fn:
        def mut(fb):
            fb[jid] = copy.deepcopy(DOWN['orig'])
            fn(fb, jid)
        bd.set_fb(mut, live=board, by='board_check')
    return {'id': jid, 'orig': DOWN['orig']}


def _days_ago(n):
    import datetime
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def down_auto_ghost(board):
    import reply_run as rr

    def fn(fb, jid):
        fb[jid]['sent_at'] = _days_ago(rr.GHOST_DAYS + 17)
        fb[jid].pop('replies', None)
        rr.apply_ghost(fb, _days_ago(0), checked={jid})
    return dict(_down(board, fn), by=f'送出 {rr.GHOST_DAYS + 17} 天沒有回音')


def down_next_round(board):
    """再跑一輪查應徵進度的「沒下文」那一步(這張本輪查過、還是沒回音)。"""
    import board_doc as bd, reply_run as rr
    got = []
    jid = _down(board)['id']
    bd.set_fb(lambda fb: got.extend(rr.apply_ghost(fb, _days_ago(0), checked={jid})), live=board, by='board_check')
    return dict(_down(board), ghosted=got)


def down_ghost_then_interview(board):
    import reply_run as rr

    def fn(fb, jid):
        fb[jid]['sent_at'] = _days_ago(rr.GHOST_DAYS + 20)
        fb[jid].pop('replies', None)
        rr.apply_ghost(fb, _days_ago(10), checked={jid})
        rr.apply_findings(fb, {jid: [{'src': 'Gmail', 'date': _days_ago(0), 'kind': 'interview',
                                       'link': 'https://mail.example/board-check-iv',
                                       'snippet': '面試邀請(檢查用)'}]}, _days_ago(0))
    return dict(_down(board, fn), ghostAt=_days_ago(10))


def down_reject_without_subject(board):
    import reply_run as rr

    def fn(fb, jid):
        fb[jid].pop('replies', None)
        rr.apply_findings(fb, {jid: [{'src': 'Gmail', 'date': _days_ago(0), 'kind': 'reject',
                                       'link': 'https://mail.example/board-check-rej',
                                       'snippet': 'Unfortunately we moved on(檢查用)'}]}, _days_ago(0))
    return _down(board, fn)


def down_maybe_letter(board):
    """同一封拒絕信也對到另一張卡(#289 決定 1):用 reply_run 真的那一條走一遍,兩張都不自動改。"""
    import reply_run as rr
    other = 'https://example.test/board-check-other-card'

    def fn(fb, jid):
        fb[jid].pop('replies', None)
        letter = {'src': 'Gmail', 'date': _days_ago(0), 'kind': 'reject', 'source_type': 'email',
                  'source_ref': 'email:bcmaybe1', 'link': 'https://mail.google.com/mail/u/0/#all/bcmaybe1',
                  'snippet': '很遺憾(檢查用)'}
        fake = {other: {'app': 'sent'}}
        rr.apply_findings(dict(fake, **{jid: fb[jid]}), {jid: [dict(letter)], other: [dict(letter)]}, _days_ago(0))
    return dict(_down(board, fn), other=other)


def down_ended_with_todo(board):
    def fn(fb, jid):
        fb[jid].update(oc='rej', oc_at={'rej': _days_ago(0)}, replies={'items': [
            {'id': 'bc-iv', 'src': 'Gmail', 'date': _days_ago(3), 'kind': 'interview',
             'snippet': '線上測驗邀請(檢查用)', 'todo': '完成線上測驗(檢查用)'},
            {'id': 'bc-rej', 'src': 'Gmail', 'date': _days_ago(0), 'kind': 'reject', 'snippet': '很遺憾(檢查用)'}]})
    return _down(board, fn)


def down_waiting_with_replies(board):
    def fn(fb, jid):
        fb[jid].update(replies={'at': _days_ago(1), 'items': [
            {'id': 'bc-ok', 'src': 'Gmail', 'date': _days_ago(2), 'kind': 'confirm', 'snippet': '已收到申請(檢查用)'}]},
            ev='送出頁是目前唯一證據(檢查用)', ghost_no=1)
    return _down(board, fn)


def down_ghost_he_undid_before(board):
    def fn(fb, jid):
        fb[jid].update(oc='ghost', oc_at={'ghost': _days_ago(0)}, ghost_no=1)
    return _down(board, fn)


def down_block_its_company(board):
    import board_doc as bd, card
    r = _down(board)
    co = card.company(DOWN['job'])
    before = {}

    def mut(fb):
        before['b'] = fb.get('__block__')
        fb['__block__'] = list(fb.get('__block__') or []) + [co]
    bd.set_fb(mut, live=board, by='board_check')
    return dict(r, co=co, block0=before['b'])


PRE.update(down_auto_ghost=down_auto_ghost, down_next_round=down_next_round,
           down_ghost_then_interview=down_ghost_then_interview,
           down_reject_without_subject=down_reject_without_subject, down_ended_with_todo=down_ended_with_todo,
           down_maybe_letter=down_maybe_letter,
           down_waiting_with_replies=down_waiting_with_replies, down_ghost_he_undid_before=down_ghost_he_undid_before,
           down_block_its_company=down_block_its_company)

# 每條開頭:切到已投出、所有公司展開;結尾:那張卡放回原樣(跟「結果往前走…」那條同一個做法)
DOWN_JS = r"""
  async function sentTab(){await T.resync(); document.querySelector('[data-tab="sent"]').click(); await T.sleep(300);
    [].slice.call(document.querySelectorAll('#app .cohead')).forEach(function(h){if(!h.parentNode.open)h.click();});
    await T.sleep(250);}
  async function putBack(){await T.idle(); var st=await T.state(), q={__rev__:1,__base__:{}};
    q.__base__[P.id]=st[P.id]; q[P.id]=P.orig;
    await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q)});
    await T.resync(); document.querySelector('[data-tab="none"]').click(); await T.sleep(150);}
"""

DOWN_CHECKS=[
 (
    '已投出下游:自動記成沒下文,他在卡上按「⏳ 等回音」,留下「他決定過」的記號',
    DOWN_JS + r"""
      await sentTab(); var c=T.card(P.id), st=(await T.state())[P.id]||{};
      if(st.oc!=='ghost')return '前置沒做成:卡不是沒下文('+st.oc+')';
      var b=c&&c.querySelector('[data-oc=""]'); if(!b)return '卡上沒有「⏳ 等回音」';
      b.click(); await T.idle(); st=(await T.state())[P.id]||{};
      var bad=[];
      if(st.oc)bad.push('按了等回音還是 '+st.oc);
      if(!st.ghost_no)bad.push('按了「⏳ 等回音」沒留下記號,下一輪會再被記成沒下文');
      document.querySelector('[data-tab="none"]').click();
      return bad.join('；');
    """,
    'down_auto_ghost'), (
    '已投出下游:…接著下一輪查應徵進度照樣查過、沒回音,也不會再把他的「等回音」改回沒下文',
    DOWN_JS + r"""
      await T.resync(); var st=(await T.state())[P.id]||{}, bad=[];
      if(P.ghosted.length||st.oc)bad.push('他按過「⏳ 等回音」,下一輪又被記成 '+(st.oc||'沒下文'));
      await putBack(); return bad.join('；');
    """,
    'down_next_round', {'with_previous': True}),
 (
    '已投出下游:「🔁 再投一次」把上一次的「不要記沒下文」記號一起收進歷史,新的一次照常會記沒下文',
    DOWN_JS + r"""
      await sentTab(); var c=T.card(P.id), b=c&&c.querySelector('[data-again]');
      if(!b){await putBack(); return '沒下文的卡上沒有「🔁 再投一次」';}
      b.click(); await T.idle(); var st=(await T.state())[P.id]||{}, bad=[], last=(st.tries||[]).slice(-1)[0]||{};
      if(st.ghost_no)bad.push('再投一次後還留著上一次的 ghost_no,這一次永遠不會自動記沒下文');
      if(!last.ghost_no)bad.push('上一次的 ghost_no 沒收進 tries');
      await putBack(); return bad.join('；');
    """,
    'down_ghost_he_undid_before'),
 (
    '已投出下游:沒下文被回音改成面試,按「不對,復原」回到原本的沒下文與日期,不留面試日期',
    DOWN_JS + r"""
      await sentTab(); var c=T.card(P.id), u=c&&c.querySelector('[data-ocundo]');
      if(!u){await putBack(); return '卡上沒有「不對,復原」';}
      u.click(); await T.idle(); var st=(await T.state())[P.id]||{}, bad=[];
      if(st.oc!=='ghost')bad.push('復原後不是沒下文:'+st.oc);
      if(JSON.stringify(st.oc_at||{})!==JSON.stringify({ghost:P.ghostAt}))
        bad.push('復原後日期不是原本的:'+JSON.stringify(st.oc_at)+'(原本 ghost '+P.ghostAt+')');
      await putBack(); return bad.join('；');
    """,
    'down_ghost_then_interview'),
 (
    '已投出下游:查應徵進度那一列,沒跑成、部分完成、沒東西可查都講得出來,前兩種附「看紀錄」;跑完跳通知',
    r"""
      var of=window.fetch, fake=null, bad=[];
      window.fetch=function(u){var p=of.apply(this,arguments);
        if(fake&&String(u).indexOf('/api/rev')===0)return p.then(function(r){return r.json();}).then(function(v){
          v.replies=fake; return new Response(JSON.stringify(v),{status:200,headers:{'Content-Type':'application/json'}});});
        return p;};
      try{
        document.querySelector('[data-tab="sent"]').click(); await T.sleep(300);
        var cases=[{phase:'incomplete',msg:'甲 1 個來源進不去',log:true},{phase:'failed',msg:'乙 agent 交件無法使用',log:true},
                   {phase:'nothing',msg:'丙 現在沒有在等回音的卡',log:false}];
        for(var i=0;i<cases.length;i++){var k=cases[i];
          fake={phase:k.phase,running:false,msg:k.msg,n:2,done:1,finished_at:Date.now()/1000};
          // 每次看都再叫它拿一次:剛好碰上 20 秒輪詢在路上時,那一次 sync 會被略過
          var ok=await T.until(function(){T.sync(); var b=document.getElementById('replybar'); return b&&b.textContent.indexOf(k.msg)>=0;},6000);
          if(!ok){bad.push(k.phase+' 那一列看不到「'+k.msg+'」'); continue;}
          if(k.log&&!document.querySelector('#replybar [data-showlog="replies"]'))bad.push(k.phase+' 那一列沒有「看紀錄」');}
        fake={phase:'run',running:true,n:2,done:0,t0:Date.now()/1000};
        await T.until(function(){T.sync(); return /查應徵進度中/.test((document.getElementById('replybar')||{}).textContent||'');},6000);
        fake={phase:'incomplete',running:false,msg:'丁 跑完了',n:2,done:1,finished_at:Date.now()/1000};
        if(!await T.until(function(){T.sync(); return /查應徵進度/.test(T.snack());},6000))bad.push('查應徵進度跑完沒有跳通知');
      }finally{window.fetch=of;}
      T.sync(); await T.idle(); document.querySelector('[data-tab="none"]').click();
      return bad.join('；');
    """),
 (
    '已投出下游:已結束的卡(沒錄取)不再掛「要你做」',
    DOWN_JS + r"""
      await sentTab(); var bad=[], c=T.card(P.id), todo=document.querySelector('#app .todo');
      if(!c)bad.push('找不到那張沒錄取的卡');
      if(todo&&todo.textContent.indexOf('完成線上測驗(檢查用)')>=0)bad.push('已投出頁最上面還列著沒錄取那張的待辦');
      if(c&&/要你做/.test(c.textContent))bad.push('沒錄取的卡上還寫「要你做」');
      await putBack(); return bad.join('；');
    """,
    'down_ended_with_todo'),
 (
    '已投出下游:已投出頁的公司選單沒有「這家全部移除」(單張已投出的卡本來就不給移除)',
    r"""
      document.querySelector('[data-tab="sent"]').click(); await T.sleep(300);
      var n=document.querySelectorAll('#app [data-corm]').length;
      document.querySelector('[data-tab="none"]').click();
      return n?'已投出頁有 '+n+' 家的選單還能「這家全部移除」,按下去已投出的卡全被收走':'';
    """),
 (
    '已投出下游:自動記成沒下文那一行寫真的天數;查應徵進度的說明不寫死「Gmail、104、LinkedIn」',
    DOWN_JS + r"""
      await sentTab(); var bad=[], c=T.card(P.id), line=c&&c.querySelector('.rp-auto');
      if(!line)bad.push('卡上沒有自動改狀態那一行');
      else if(line.textContent.indexOf(P.by)<0)bad.push('那一行沒寫「'+P.by+'」:'+line.textContent.trim().slice(0,60));
      var rb=document.querySelector('#replybar [data-replyrun]'), t=rb?rb.getAttribute('title')||'':'';
      if(/LinkedIn|Gmail/.test(t))bad.push('查應徵進度的說明寫死了 Gmail、LinkedIn:'+t.slice(0,40));
      await putBack(); return bad.join('；');
    """,
    'down_auto_ghost'),
 (
    '已投出下游:照回音自動改狀態那一行,回音沒有標題時寫摘要,不寫空的「照「」改成」',
    DOWN_JS + r"""
      await sentTab(); var bad=[], c=T.card(P.id), line=c&&c.querySelector('.rp-auto');
      if(!line)bad.push('卡上沒有自動改狀態那一行');
      else{if(/照「」/.test(line.textContent))bad.push('那一行寫成「照「」改成」');
        if(line.textContent.indexOf('Unfortunately')<0)bad.push('那一行沒講照哪一則回音:'+line.textContent.trim().slice(0,60));
        if(line.textContent.indexOf('agent 判斷')<0)bad.push('那一行沒標明信算哪一種是 agent 判斷');}
      await putBack(); return bad.join('；');
    """,
    'down_reject_without_subject'),
 (
    '已投出下游:同一封信也對到別張卡,不自動改結果,卡上寫「可能是這封」附原文;按「知道了」收掉、可以復原',
    DOWN_JS + r"""
      await sentTab(); var bad=[], c=T.card(P.id), st=(await T.state())[P.id]||{};
      if(st.oc)bad.push('分不出是哪一張還是自動改了結果:'+st.oc);
      var line=c&&c.querySelector('.rp-maybe');
      if(!line){await putBack(); return bad.concat(['卡上沒有「可能是這封」']).join('；');}
      if(!line.querySelector('a[href*="bcmaybe1"]'))bad.push('「可能是這封」沒附原文連結');
      if(line.textContent.indexOf(P.other)<0&&line.textContent.indexOf('other-card')<0)bad.push('沒講同一封也對到哪一張:'+line.textContent.trim().slice(0,80));
      if(!c.querySelector('[data-oc="rej"]'))bad.push('卡上沒有讓他自己按的結果鈕');
      var ok=line.querySelector('[data-rpmaybe]'); if(!ok)bad.push('沒有「知道了」');
      else{ok.click(); await T.idle(); c=T.card(P.id);
        if(c&&c.querySelector('.rp-maybe'))bad.push('按了知道了還在');
        var u=document.querySelector('#snack.on .snack-undo'); if(!u)bad.push('知道了沒有復原');
        else{u.click(); await T.idle(); c=T.card(P.id); if(!(c&&c.querySelector('.rp-maybe')))bad.push('復原後「可能是這封」沒回來');}}
      await putBack(); return bad.join('；');
    """,
    'down_maybe_letter'),
 (
    '已投出下游:「← 退回可以投了」連回音、查過日期、證據說明、沒下文記號一起收;復原全部放回',
    DOWN_JS + r"""
      await sentTab(); var bad=[], c=T.card(P.id), b=c&&c.querySelector('[data-back="ship"]');
      if(!b){await putBack(); return '還在等回音的卡上沒有「← 退回可以投了」';}
      b.click(); await T.idle(); var st=(await T.state())[P.id]||{};
      ['replies','ev','ghost_no'].forEach(function(k){if(k in st)bad.push('退回後還留著 '+k);});
      var u=document.querySelector('#snack .snack-undo'); if(!u)bad.push('沒有復原');
      else{u.click(); await T.idle(); st=(await T.state())[P.id]||{};
        ['replies','ev','ghost_no','sent_at'].forEach(function(k){if(!(k in st))bad.push('復原後 '+k+' 沒放回來');});}
      await putBack(); return bad.join('；');
    """,
    'down_waiting_with_replies'),
 (
    '已投出下游:封鎖一家已經投過的公司,已投出頁照樣列它的卡,頁面張數跟分頁籤數字一樣',
    DOWN_JS + r"""
      await sentTab(); var bad=[], n=document.querySelectorAll('#app article[data-fid]').length,
          want=window.__jobsalvoFlow.counts().sent;
      if(!T.card(P.id))bad.push('封鎖「'+P.co+'」之後,它已投出的卡從已投出頁消失了');
      if(n!==want)bad.push('已投出籤數 '+want+' 張,頁面列 '+n+' 張');
      var st=await T.state(), q={__rev__:1,__base__:{__block__:st.__block__===undefined?null:st.__block__}};
      q.__block__=P.block0||[];     // 頁面自己的封鎖名單至少是 [];送 null 刪掉的話頁面會留著舊的
      await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q)});
      await T.resync(); document.querySelector('[data-tab="none"]').click();
      return bad.join('；');
    """,
    'down_block_its_company'),
]
CHECKS=CHECKS+DOWN_CHECKS
# ── 上游(找缺 → 表態 → 準備履歷 → 待你決定 → 可以投了)的規矩 ──
def orphan_custom_case(board):
    """待你決定兩張卡都用副本那份履歷:一張留著換履歷之前那份的客製紀錄(等你看),一張是現在這份在等你看。"""
    import board_doc as bd
    parsed = bd.load(board)
    fb = json.loads(parsed['fb'])
    ready = [j['id'] for j in parsed['data']['jobs']
             if (fb.get(j['id']) or {}).get('app') == 'ready' and not (fb.get(j['id']) or {}).get('rm')][:2]
    if len(ready) < 2:
        raise ValueError('示範看板待你決定少於兩張')

    def mut(f):
        for u in ready:
            f[u].update(resume_id=CHECK_RESUME, lang='zh')
        f[ready[0]]['custom_docs'] = {'resume:old-gone': {'status': 'review', 'name': '換掉的那份'}}
        f[ready[1]]['custom_docs'] = {'resume:' + CHECK_RESUME + ':zh': {'status': 'review', 'name': '看板檢查履歷'}}
    bd.set_fb(mut, live=board, by='board_check')
    return {'orphan': ready[0], 'pending': ready[1]}


PRE['orphan_custom_case'] = orphan_custom_case
CHECKS.append((
    '上游:換了履歷留下的舊客製紀錄不擋這張、不給收下,卡上給一顆清掉;現在這份在等你看照樣擋',
    r"""
      var bad=[];
      document.querySelector('[data-tab="ready"]').click(); T.sync(); await T.sleep(700);
      document.querySelectorAll('#app .cohead').forEach(function(h){if(!h.parentNode.open)h.click();}); await T.sleep(250);
      var a=T.card(P.orphan), b=T.card(P.pending);
      if(!a||!b)return '待你決定找不到案例卡';
      var gate=(a.querySelector('.stage-blocked')||{}).textContent||'';
      if(/客製/.test(gate))bad.push('換掉的那份客製紀錄還擋著這張('+gate+')');
      if(a.querySelector('[data-cust-action="accept"][data-cust-item="resume:old-gone"]'))bad.push('不會寄的那份還給「收下」');
      var clr=a.querySelector('[data-cust-action="clear"][data-cust-item="resume:old-gone"]');
      if(!clr)bad.push('不會寄的那份沒有清掉的按鈕');
      if(!/等你看/.test((b.querySelector('.stage-blocked')||{}).textContent||''))bad.push('現在這份在等你看,卻沒擋');
      if(clr){clr.click();
        var gone=await T.until(async function(){return !(((await T.state())[P.orphan]||{}).custom_docs);});
        if(!gone)bad.push('按了清掉,紀錄還在('+T.snack()+')');}
      return bad.join('；');
    """,
    'orphan_custom_case', {'fresh_page': True},
))


def _ship_settings(resumes=(), attachments=(), files=()):
    """透過副本的設定 API 加幾份假的履歷/附件(檔案先傳上去);回原本的設定,檢查最後自己還原。
    另外確保語言清單有中文、英文兩個。"""
    settings = sandbox('/api/settings', timeout=60)['settings']
    original = copy.deepcopy(settings)
    for rel, text in files:
        sandbox('/api/file?path=' + rel, text.encode('utf-8'), method='PUT', headers={'User-Agent': 'board-check'},
                timeout=60)
    resume = settings.setdefault('resume', {})
    resume['langs'] = list(dict.fromkeys(list(resume.get('langs') or []) + ['zh', 'en']))
    resume['resumes'] = list(resume.get('resumes') or []) + list(resumes)
    resume['attachments'] = list(resume.get('attachments') or []) + list(attachments)
    sandbox('/api/settings', {'settings': settings}, timeout=60)
    return original


def _ready_cards(board, n):
    import board_doc as bd
    parsed = bd.load(board)
    fb = json.loads(parsed['fb'])
    live = [j['id'] for j in parsed['data']['jobs'] if not (fb.get(j['id']) or {}).get('rm')]
    # 示範看板的待你決定不夠:不夠的從還沒進流程的卡搬進來
    picked = ([u for u in live if (fb.get(u) or {}).get('app') == 'ready'] +
              [u for u in live if not (fb.get(u) or {}).get('app')])[:n]
    if len(picked) < n:
        raise ValueError(f'示範看板湊不到 {n} 張卡')

    def mut(f):
        for u in picked:
            m = f.setdefault(u, {})
            m.update(app='ready', resume_id=CHECK_RESUME, lang='zh')
            for k in ('custom_docs', 'custom_file', 'variant'):
                m.pop(k, None)
    bd.set_fb(mut, live=board, by='board_check')
    return picked


def _upload_custom(url, item):
    """用看板「上傳自己的客製版」那條路收下一份(伺服器記它是從現在哪一份原始檔做的)。"""
    q = urllib.parse.urlencode({'u': url, 'item': item, 'name': 'board-check-custom.pdf'})
    sandbox('/api/card-file?' + q, b'%PDF-1.4 board-check custom', method='PUT', headers={'User-Agent': 'board-check'},
            timeout=60)


def stale_custom_case(board):
    """待你決定三張卡都用副本那份履歷(中文)、都收下了自己上傳的客製版:一張原始檔沒換,
    一張收下之後原始檔換過(簽章對不上),一張收下的是中文、現在換成英文(那份履歷英文有自己的原始檔)。"""
    import board_doc as bd
    original = _ship_settings(files=[('resume/board-check-en.txt', 'English resume for board check.')])
    settings = sandbox('/api/settings', timeout=60)['settings']
    for r in settings['resume']['resumes']:
        if r.get('id') == CHECK_RESUME:
            r.setdefault('files', {})['en'] = 'resume/board-check-en.txt'
    sandbox('/api/settings', {'settings': settings}, timeout=60)
    cards = _ready_cards(board, 3)
    key = 'resume:' + CHECK_RESUME + ':zh'
    for u in cards:
        _upload_custom(u, key)

    def mut(f):
        f[cards[1]]['custom_docs'][key]['source_sig'] = '0' * 64
        f[cards[2]]['lang'] = 'en'
    bd.set_fb(mut, live=board, by='board_check')
    return {'fresh': cards[0], 'stale': cards[1], 'other_lang': cards[2], 'original': original}


PRE['stale_custom_case'] = stale_custom_case
CHECKS.append((
    '上游:已收下的客製版在原始檔換過、或換了語言之後,卡上照實講不寄它,預覽也換回原始檔',
    r"""
      var bad=[];
      try{
      document.querySelector('[data-tab="ready"]').click(); T.sync(); await T.sleep(700);
      document.querySelectorAll('#app .cohead').forEach(function(h){if(!h.parentNode.open)h.click();}); await T.sleep(250);
      function row(id){var c=T.card(id); return c?[...c.querySelectorAll('.cust-doc')].map(function(x){return x.textContent;}).join(' | '):null;}
      // 卡上「📄」那顆預覽的是客製版還是原始檔
      function preview(id){var b=T.card(id)&&T.card(id).querySelector('.ship-rz'); return b?b.getAttribute('data-rzkind'):'';}
      var fresh=row(P.fresh), stale=row(P.stale), other=row(P.other_lang);
      if(fresh===null||stale===null||other===null)return '待你決定找不到案例卡';
      if(!/已收下/.test(fresh))bad.push('原始檔沒換的那張沒寫已收下('+fresh+')');
      if(/已收下/.test(stale)||!/原始檔換過/.test(stale))bad.push('原始檔換過的那張還寫已收下,或沒講原始檔換過('+stale+')');
      if(/已收下/.test(other)||!/中文/.test(other))bad.push('換成英文的那張還寫中文那份已收下,或沒講那是中文的('+other+')');
      if(preview(P.fresh)!=='custom')bad.push('原始檔沒換的那張預覽不是客製版');
      if(preview(P.stale)==='custom')bad.push('原始檔換過的那張預覽還是客製版');
      if(preview(P.other_lang)==='custom')bad.push('換成英文的那張預覽還是中文的客製版');
      } finally {
        await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:P.original})});
      }
      return bad.join('；');
    """,
    'stale_custom_case', {'fresh_page': True},
))


def ship_pick_case(board):
    """待你決定一張卡用副本那份履歷(中文);設定多一份「第二份履歷」(中英文都有)和一份只搭它、只有中文的附件。"""
    original = _ship_settings(
        resumes=[{'id': 'bc-second', 'name': '第二份履歷', 'enabled': True,
                  'files': {'zh': 'resume/bc-second-zh.md', 'en': 'resume/bc-second-en.md'}}],
        attachments=[{'id': 'bc-att', 'name': '只搭第二份的附件', 'enabled': True, 'resume_ids': ['bc-second'],
                      'files': {'zh': 'resume/bc-att-zh.md'}}],
        files=[('resume/bc-second-zh.md', '# 第二份履歷'), ('resume/bc-second-en.md', '# Second resume'),
               ('resume/bc-att-zh.md', '# 附件')])
    return {'id': _ready_cards(board, 1)[0], 'second': 'bc-second', 'att': '只搭第二份的附件', 'original': original}


PRE['ship_pick_case'] = ship_pick_case
CHECKS.append((
    '上游:按了版本或語言,一秒內卡上的要寄的檔案(履歷、語言、附件)就等於後台算的,不等存檔、不等重建',
    r"""
      var bad=[];
      try{
      document.querySelector('[data-tab="ready"]').click(); T.sync(); await T.sleep(700);
      document.querySelectorAll('#app .cohead').forEach(function(h){if(!h.parentNode.open)h.click();}); await T.sleep(250);
      function shown(){var c=T.card(P.id); if(!c)return null;
        var on=c.querySelector('.vd-b[data-vd].on'), lg=c.querySelector('.vd-b.lg.on'), rz=c.querySelector('.ship-rz,.ship-rz-missing');
        return {resume:on?on.getAttribute('data-vv'):'', lang:lg?lg.getAttribute('data-lv'):'', rz:rz?rz.textContent:'',
          atts:[...c.querySelectorAll('.ship-att .att-pill')].map(function(x){return x.getAttribute('title').replace(/^預覽 /,'');}).join('|')};}
      async function backend(){return (await fetch('/api/ship-files?u='+encodeURIComponent(P.id))).json();}
      function differ(s,r){
        var atts=(r.files||[]).filter(function(f){return f.kind==='attachment';}).map(function(f){return f.name;}).join('|');
        return (!s||s.resume!==r.resume_id||s.lang!==r.lang||s.rz.indexOf(r.resume_name)<0||s.atts!==atts)?
          '畫面 '+JSON.stringify(s)+',後台算的是 '+JSON.stringify({resume:r.resume_id,lang:r.lang,name:r.resume_name,atts:atts}):'';}
      var c=T.card(P.id); if(!c)return '待你決定找不到案例卡';
      var b=c.querySelector('.vd-b[data-vv="'+P.second+'"]'); if(!b)return '卡上沒有「第二份履歷」那顆(設定新加的履歷沒出現)';
      var t0=Date.now(); b.click();
      var s=await T.until(function(){var x=shown(); return x&&x.resume===P.second&&x.atts.indexOf(P.att)>=0&&x;},1000);
      if(!s)bad.push('按了第二份履歷,1 秒內卡上的附件沒換('+JSON.stringify(shown())+')');
      // 按下去就送後台、等它回話(#343):存好的就是按的那一份
      await T.idle(); if(((((await T.state())[P.id])||{}).resume_id)!==P.second)bad.push('按了第二份履歷,後台沒存成那一份');
      await T.idle();
      var d=differ(shown(),await backend()); if(d)bad.push('按了履歷之後:'+d);
      var en=T.card(P.id).querySelector('.vd-b.lg[data-lv="en"]');
      if(!en)bad.push('卡上沒有英文那顆');
      else{en.click();
        if(!await T.until(function(){var x=shown(); return x&&x.lang==='en'&&x.atts.indexOf(P.att)<0;},1000))
          bad.push('按了英文,1 秒內只有中文的附件還列著('+JSON.stringify(shown())+')');
        await T.idle();
        d=differ(shown(),await backend()); if(d)bad.push('按了語言之後:'+d);}
      } finally {
        await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:P.original})});
      }
      return bad.join('；');
    """,
    'ship_pick_case', {'fresh_page': True},
))


def source_replaced_case(board):
    """待你決定一張卡用副本那份履歷(中文),收下了自己上傳的客製版(從現在這份原始檔做的)。"""
    original = sandbox('/api/settings', timeout=60)['settings']
    url = _ready_cards(board, 1)[0]
    _upload_custom(url, 'resume:' + CHECK_RESUME + ':zh')
    return {'id': url, 'original': original, 'which': 'resume|' + CHECK_RESUME + '|zh'}


PRE['source_replaced_case'] = source_replaced_case
CHECKS.append((
    '上游:設定頁上傳新的原始履歷之後,不用重新整理,卡上就改成寄原始檔、講客製版不寄了',
    r"""
      var bad=[];
      try{
      function open(){document.querySelector('[data-tab="ready"]').click();
        document.querySelectorAll('#app .cohead').forEach(function(h){if(!h.parentNode.open)h.click();});}
      function row(){var c=T.card(P.id); return c?[...c.querySelectorAll('.cust-doc')].map(function(x){return x.textContent;}).join(' | '):'';}
      function preview(){var b=T.card(P.id)&&T.card(P.id).querySelector('.ship-rz'); return b?b.getAttribute('data-rzkind'):'';}
      open(); await T.sleep(400);
      if(!/已收下/.test(row())||preview()!=='custom')return '案例卡一開始就不是「已收下、預覽客製版」('+row()+')';
      document.querySelector('[data-tab="cfg"]').click(); await T.idle();
      var inp=await T.until(function(){document.querySelectorAll('#app details.fold').forEach(function(x){x.open=true;});
        return document.querySelector('#app input[data-cfup="'+P.which+'"]');},5000);
      if(!inp)return '設定頁找不到那份履歷中文版的上傳';
      var dt=new DataTransfer(); dt.items.add(new File(['換過的原始履歷 '+Date.now()],'board-check-new.txt',{type:'text/plain'}));
      inp.files=dt.files; inp.dispatchEvent(new Event('change',{bubbles:true}));
      if(!await T.until(function(){return /傳好了/.test(T.snack());},8000))bad.push('上傳新的原始檔沒成('+T.snack()+')');
      await T.idle(); open();
      if(!await T.until(function(){return /原始檔換過/.test(row());},3000))bad.push('換了原始檔、沒重新整理,卡上還寫「'+row()+'」');
      if(preview()==='custom')bad.push('換了原始檔、沒重新整理,預覽還是客製版');
      } finally {
        await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:P.original})});
      }
      return bad.join('；');
    """,
    'source_replaced_case', {'fresh_page': True},
))

def auto_prep_tried_case(board):
    """準備區一張卡:自動準備替它跑過一次(tried 有 prep:<id>)、沒產出也沒寫原因。只開自動準備。
    __auto__ 先寫好同一組開關:伺服器不會當成「剛打開」把整區記成不碰的 backlog(那樣卡上本來就不會寫會自動準備)。
    其他在流程裡的卡全記成不碰,副本不會真的跑準備區。"""
    import board_doc as bd
    parsed = bd.load(board)
    fb = json.loads(parsed['fb'])
    jobs = parsed['data']['jobs']
    prep = [j['id'] for j in jobs if (fb.get(j['id']) or {}).get('app') == 'prep'
            and not (fb.get(j['id']) or {}).get('rm') and not j.get('prep_note')]
    if not prep:
        # 前面「跑準備區」那條會把整區推去待你決定:自己放一張還沒進流程的卡進來
        spare = next((j['id'] for j in jobs if not (fb.get(j['id']) or {}).get('app')
                      and not (fb.get(j['id']) or {}).get('rm') and not j.get('prep_note')), None)
        if not spare:
            raise ValueError('示範看板沒有可以放進準備區的卡')
        bd.set_fb(lambda f: f.setdefault(spare, {}).__setitem__('app', 'prep'), live=board, by='board_check')
        fb = json.loads(bd.load(board)['fb'])
        prep = [spare]
    target = prep[0]
    others = sorted(j['id'] for j in jobs if (fb.get(j['id']) or {}).get('app') in ('prep', 'ready', 'ship')
                    and j['id'] != target)

    def mut(f):
        f['__auto__'] = {'since': '2026-01-01T00:00:00', 'skip': others, 'tried': ['prep:' + target], 'seen': {},
                         'flow': {'auto_prep': True, 'auto_advance': False, 'auto_fill': False}}
    bd.set_fb(mut, live=board, by='board_check')
    shot.flow(auto_prep=True)
    return {'id': target}


PRE['auto_prep_tried_case'] = auto_prep_tried_case
CHECKS.append((
    '上游:自動準備替這張跑過一次沒成功,卡上不再寫「會自動準備」,改叫他自己按',
    r"""
      var bad=[];
      try{
      document.querySelector('[data-tab="prep"]').click(); await T.sleep(300);
      document.querySelectorAll('#app .cohead').forEach(function(h){if(!h.parentNode.open)h.click();}); await T.sleep(250);
      var card=T.card(P.id); if(!card)return '準備區找不到那張卡';
      var t=card.textContent;
      if(/會自動準備/.test(t))bad.push('自動準備已經試過一次沒成功,卡上還寫「會自動準備」(自動流程不會再替它跑)');
      if(!/等產履歷/.test(t))bad.push('沒告訴他要自己按「準備履歷」');
      } finally {
""" + FLOW_OFF_JS + r"""
      }
      return bad.join('；');
    """,
    'auto_prep_tried_case', {'fresh_page': True},
))


def approved_lang_case(board):
    """一張可以投了的卡:agent 填好、他確認送出了;語言沒自己選過(卡上亮的是 agent 判的那個)。"""
    import board_doc as bd
    parsed = bd.load(board)
    fb = json.loads(parsed['fb'])
    job = next(j for j in parsed['data']['jobs'] if (fb.get(j['id']) or {}).get('app') == 'ship'
               and not (fb.get(j['id']) or {}).get('rm') and not ((fb.get(j['id']) or {}).get('form') or {}).get('lock'))

    def mut(f):
        m = f[job['id']]
        m['resume_id'] = CHECK_RESUME   # 沒挑履歷的卡兩排按鈕都不亮;這條要的是「有亮著的語言」
        m.pop('lang', None)
        m['form'] = {'plat': '測試', 'f': []}
        m['ds'] = 'confirmed'
        m['apply'] = {'stage': 'fill', 'issues': [], 'session': 'board-check-lang', 'tab_id': '1',
                      'delivery': {'method': 'direct_upload'}, 'at': '2026-01-01T00:00:00'}
        m['approve'] = {'at': '2026-01-01T00:00:00', 'snap': {}}
    bd.set_fb(mut, live=board, by='board_check')
    return {'id': job['id']}


PRE['approved_lang_case'] = approved_lang_case
CHECKS.append((
    '上游:按下本來就亮著的語言,什麼都不變(不取消確認送出、不叫 agent 重填)',
    r"""
      var bad=[];
      document.querySelector('[data-tab="ship"]').click(); T.sync(); await T.sleep(700);
      document.querySelectorAll('#app .cohead').forEach(function(h){if(!h.parentNode.open)h.click();}); await T.sleep(250);
      var card=T.card(P.id); if(!card)return '可以投了找不到那張卡';
      var on=card.querySelector('.vd-b.lg.on'); if(!on)return '卡上沒有亮著的語言';
      on.click(); await T.idle();
      var m=(await T.state())[P.id]||{};
      if(!m.approve)bad.push('確認送出被取消了');
      if((m.apply||{}).stale)bad.push('標成要重填:'+m.apply.stale);
      if(m.lang)bad.push('寫進了他選的語言 '+m.lang+'(他沒換)');
      if(/語言切成/.test(T.snack()))bad.push('跳出「'+T.snack()+'」');
      return bad.join('；');
    """,
    'approved_lang_case',
))
# 代投狀態那幾條(卡上寫的下一步跟真實狀態一致、按鈕按下去資料真的跟著變):排在最後,種的資料不影響別條
# ── 代投狀態那幾條(規矩寫法一樣:會失敗的檢查)──
#
# 守的規矩:卡上、「這一頁要你處理的」、「🚀 填表進度」寫的狀態和下一步,跟看板資料真的狀態一致;
# 按鈕按下去,資料真的跟著變,復原也真的還原。
# 種資料用 PRE:直接改副本看板檔(board_doc.set_fb),再叫頁面跟上。挑示範看板最後面幾張,
# 這幾條排在全部檢查的最後,不影響別條。
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
    running = _filled()     # 「正在填」種不住(沒有真的一輪在跑,伺服器會收尾):檢查裡讓下一步說它正在填,驗看板照下一步畫
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
      var I=P.ids, bad=[], of=window.fetch, N=null;
      function json(v){return new Response(JSON.stringify(v),{status:200,headers:{'Content-Type':'application/json'}});}
      // 輪詢每次都當成卡變了(去拿下一步);下一步裡正在填的那張照實說忙(伺服器上種不住,見上面)
      window.fetch=function(u){var s=String(u);
        if(/\/api\/rev/.test(s))return of.apply(this,arguments).then(function(r){return r.json();}).then(function(v){v.rev='bc'+Date.now(); return json(v);});
        if(/\/api\/next/.test(s))return of.apply(this,arguments).then(function(r){return r.json();}).then(function(n){
          n[I[0]]=Object.assign({},n[I[0]],{busy:'Agent 正在做,等它做完'}); N=n; return json(n);});
        return of.apply(this,arguments);};
      try{
      if(!await openShip(I[0]))return '找不到那張可投遞卡';
      T.sync(); await T.until(function(){return N;},5000); await T.idle(); await cardOf(I[0]);
      if(!N)return '輪詢沒有去拿每張卡的下一步';
      var SEL='[data-back],[data-rm="1"],[data-err],.fb-b[data-s="dislike"],.fb-b[data-s="meh"],[data-adv="sent"],.vd-b[data-vd],.vd-b.lg,'+
        '[data-cust-open],[data-cust-action="accept"],[data-cust-action="clear"]:not([data-cust-orphan])';
      [[I[0],'#11 agent 正在做'],[I[1],'#14 送出結果不明']].forEach(function(x){
        var c=T.card(x[0]), want=(N[x[0]]||{}).busy; if(!c){bad.push(x[1]+':找不到卡'); return;}
        if(!want){bad.push(x[1]+':下一步沒說這張忙'); return;}
        if(!c.querySelector('[data-cust-open]'))bad.push(x[1]+':卡上沒有「要客製 / 上傳自己的客製版」');
        [].slice.call(c.querySelectorAll(SEL)).forEach(function(b){
          if(!b.disabled)bad.push(x[1]+':「'+b.textContent.trim()+'」還按得下去');
          else if((b.title||'')!==want)bad.push(x[1]+':「'+b.textContent.trim()+'」停用的原因跟下一步不一樣('+(b.title||'')+' / '+want+')');});});
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


def confirm_next_case(board):
    """一張填好的卡,用到一條你改了中文、英文還沒照著重翻的答案(卡上給灰掉的確認鈕,不是「答案等你確認」)。"""
    shot.flow()     # 自動流程關著:不然它會自己去重翻
    def ans(fb, ids):
        bank = [e for e in fb.get('__ans__') or [] if e.get('k') != 'bc_wait']
        bank.append({'k': 'bc_wait', 'q': '等你確認那條', 'v': 'Yes', 'zh': '是', 'at': '2026-01-01', 'tr': 1})
        fb['__ans__'] = bank
    card = _filled()
    card['form'] = {'plat': '測試', 'at': '2026-01-01', 'f': [{'q': 'Wait?', 'src': 'bank', 'k': 'bc_wait'}]}
    return _seed(board, [card], ans)


APPLY_PRE['confirm_next_case'] = confirm_next_case
APPLY_CHECKS.append((
    '確認送出照下一步畫:還不能確認的卡,確認鈕灰掉、寫的原因就是後台下一步那一句;在看板外改好了,不重新整理就變成按得下去(#341)',
    OPEN_SHIP + r"""
      var I=P.ids, bad=[];
      async function nextOf(id){return (await (await fetch('/api/next')).json())[id]||{};}
      function btn(){var c=T.card(I[0]); return c&&c.querySelector('[data-approve]');}
      if(!await openShip(I[0]))return '找不到那張可投遞卡';
      var n=await nextOf(I[0]), ap=btn();
      if(!/重翻/.test(n.confirm||''))bad.push('下一步沒說答案還沒重翻('+n.confirm+')');
      if(!ap||!ap.disabled||ap.title!==n.confirm)bad.push('確認鈕沒照下一步灰掉、寫原因('+(ap?ap.title:'沒有確認鈕')+')');
      // 背景把那條答案翻好了(看板外改的):這一頁不重新整理,輪詢拿到新的下一步就照著畫
      var bank=((await T.state()).__ans__||[]).map(function(e){if(e.k!=='bc_wait')return e; var x=Object.assign({},e); delete x.tr; return x;});
      await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({__rev__:1,__ans__:bank})});
      T.sync();
      if(!await T.until(async function(){await cardOf(I[0]); var b=btn(); return b&&!b.disabled;},8000))
        bad.push('答案在看板外翻好了,卡上確認鈕還是灰的('+((btn()||{}).title||'')+';下一步:'+(await nextOf(I[0])).confirm+')');
      return bad.join('；');
    """,
    'confirm_next_case', {'fresh_page': True},
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
    return _seed(board, cards)   # 沒送成之後自動填表開著會排隊:卡上照下一步畫,下一步那一半在 tests/test_next_step.py


# 切到某一頁、展開每一家;每張卡在哪一頁看它自己的 app;按鈕用 window.confirm 問的那一句收下來
SENT_JS = OPEN_SHIP + r"""
  async function openTab(tab,id){await T.resync(); document.querySelector('[data-tab="'+tab+'"]').click(); await T.sleep(300);
    return await cardOf(id);}
  function canon(v){if(Array.isArray(v))return v.map(canon);
    if(v&&typeof v==='object'){var o={}; Object.keys(v).sort().forEach(function(k){o[k]=canon(v[k]);}); return o;} return v;}
  // 比的是存檔會留下的樣子(空字串、空陣列、空物件都算沒有,跟後台 delivery_state.lean 同一個意思)
  function lean(v){if(v===null||v===undefined||v==='')return null; if(Array.isArray(v))return v.length?v:null;
    if(typeof v==='object'){var o={},n=0; Object.keys(v).forEach(function(k){var x=lean(v[k]); if(x!==null){o[k]=x;n++;}}); return n?o:null;} return v;}
  function same(a,b){return JSON.stringify(canon(lean(a)))===JSON.stringify(canon(lean(b)));}
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
      var I=P.ids, bad=[];
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


PRE.update(APPLY_PRE)
CHECKS=CHECKS+APPLY_CHECKS
# 「每顆按鈕按一遍」補上已送出三種來源(agent 送出、外部送出、平台對帳,各有等回音和有結果的)和送出結果不明(頁在、頁不在)(#303):
# 示範看板的卡只有外部送出一種,上面那條按不到「沒送成」「其實送出了」「確認沒送出」「再投一次」
CHECKS.append((
    '流程按鍵:已送出三種來源、送出結果不明的每顆按鈕按一遍,每張卡只算在一個分頁、按了真的改、復原整張回到原樣',
    FLOW_INV + SENT_JS + r"""
      var I=P.ids, bad=[], clicks=0, F=window.__jobsalvoFlow, SEL='[data-undosent],[data-actsent],[data-applyclear],[data-back],[data-again],[data-adv]';
      stubConfirm(); ANSWER=true;
      try{
        for(const fid of I){
          var st0=(await T.state())[fid]||{}, tab=st0.app;
          var card=await openTab(tab,fid); if(!card){bad.push(tab+' 找不到 '+fid.slice(-12)); continue;}
          var sels=[].slice.call(card.querySelectorAll(SEL)).filter(function(x){return !x.disabled;}).map(function(x){
            var a=[].slice.call(x.attributes).filter(function(y){return /^data-/.test(y.name);})[0];
            return '['+a.name+'="'+a.value.replace(/"/g,'\\"')+'"]';});
          for(const sel of sels){
            var c2=await openTab(tab,fid), b=c2&&c2.querySelector(sel); if(!b||b.disabled)continue;
            var before=(await T.state())[fid], where=tab+' '+(st0.ds||'todo')+'/'+(st0.sent_by||'')+(st0.oc?'/'+st0.oc:'')+' '+sel.slice(0,20);
            b.click(); clicks++; await T.idle();
            inv().forEach(function(x){bad.push(where+':'+x);});
            var now=(await T.state())[fid];
            if(same(now,before))bad.push(where+':按了卡沒變');
            if(F.tabOf(fid)!==tab&&T.card(fid))bad.push(where+':卡已經到「'+F.tabOf(fid)+'」,卻還留在這一頁');
            var u=document.querySelector('#snack.on .snack-undo');
            if(!u){bad.push(where+':按了沒有復原'); continue;}
            u.click(); await T.idle();
            inv().forEach(function(x){bad.push(where+' 復原後:'+x);});
            var back=(await T.state())[fid]; if(!same(back,before))bad.push(where+':復原後沒回到原樣('+diff(before,back)+')');
          }
        }
      }finally{unstubConfirm();}
      await T.idle(); document.querySelector('[data-tab="none"]').click();
      if(clicks<9)bad.push('只按到 '+clicks+' 顆(八張卡應該有九顆)');
      return bad.slice(0,8).join('；');
    """,
    'sent_sources_case', {'fresh_page': True},
))



def drawn_case(board):
    """可以投了裡每一種投遞狀態各一張(還沒填、正在填、沒填成、停著等你、答案改過要重打、填了卡住、上傳的是舊檔、
    頁面不見了、你已確認、正在送出、送出結果不明):看板畫的要跟後台下一步的 view 一字不差。"""
    cards = [{'app': 'ship'}, _filled('running'), _filled('nopage', tab_id='', issues=['agent 的 Chrome 沒連上']),
             _filled(), _filled(), _filled('stuck', issues=['必填欄位沒填']),
             _filled('stale', stale='履歷換成「別的」'), _filled('gone', tab_id='', issues=['填好的那一頁不見了']),
             dict(_filled('confirmed'), approve={'at': '2026-01-01T00:07:00', 'snap': {}}), _filled('sending'),
             dict(_filled('unsure', submit_fail={'at': '2026-01-01T00:08:00', 'problems': ['沒看到成功頁面'], 'clicked': True}),
                  approve={'at': '2026-01-01T00:07:00', 'snap': {}})]

    def extra(fb, ids):
        bank = [e for e in fb.get('__ans__') or [] if e.get('k') != 'bc_drawn']
        bank.append({'k': 'bc_drawn', 'q': '畫法檢查用', 'v': 'Yes', 'zh': '是', 'at': '2026-01-01'})
        fb['__ans__'] = bank
        fb[ids[4]]['form']['f'] = [{'q': 'Drawn?', 'src': 'bank', 'k': 'bc_drawn', 'refill': 1}]
    return _seed(board, cards, extra)


PRE['drawn_case'] = drawn_case
CHECKS.append((
    '代投那一塊照後台的下一步畫:每一種投遞狀態,卡上那一行、按鈕(能不能按、為什麼不能)、擋住的原因、要你處理的、🚀 填表進度,'
    '跟 /api/next 的 view 一字不差(看板不自己判斷,#343)',
    r"""
      var I=P.ids, bad=[], n=0;
      await T.resync(); document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
      [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
      await T.until(function(){return T.card(I[I.length-1]);},5000);
      var N=await fetch('/api/next').then(function(r){return r.json();});
      var todo=document.querySelector('#app .todo'), fl=document.getElementById('filllistbar');
      I.forEach(function(id,i){var c=T.card(id), v=(N[id]||{}).view, w='第 '+i+' 張';
        if(!c||!v){bad.push(w+(c?':下一步沒有這張':':畫面上沒有這張')); return;}
        var line=c.querySelector('.ap-line'); if(!line){bad.push(w+':沒畫代投那一塊'); return;} n++;
        var st=[].slice.call(line.querySelectorAll('.ap-st > span, .ap-st > b')).map(function(x){return x.textContent;});
        var want=v.line.map(function(x){return x[1];});
        if(st.join('|')!==want.join('|'))bad.push(w+' 那一行「'+st.join('|')+'」,下一步「'+want.join('|')+'」');
        var why=(line.querySelector('.ap-why')||{}).textContent||'';
        if(why!==(v.why||''))bad.push(w+' 擋住的原因「'+why+'」,下一步「'+v.why+'」');
        var bs=[].slice.call(line.querySelectorAll('.ap-acts button')).map(function(b){return b.textContent+(b.disabled?' ⛔'+b.title:'');});
        var wb=v.buttons.map(function(b){return b.label+(b.off?' ⛔'+b.off:'');});
        if(bs.join('|')!==wb.join('|'))bad.push(w+' 按鈕「'+bs.join('|')+'」,下一步「'+wb.join('|')+'」');
        var tr=todo&&todo.querySelector('[data-todogo="'+CSS.escape(id)+'"]'), tt=tr?tr.closest('li').querySelector('.todo-why').textContent:'';
        if(!v.busy&&v.todo&&tt.indexOf(v.todo)!==0)bad.push(w+' 要你處理的「'+tt+'」,下一步「'+v.todo+'」');
        if((!v.todo||v.busy)&&tr)bad.push(w+' 下一步沒有要你處理,卻列在要你處理的');
        var fr=fl&&fl.querySelector('[data-fillgo="'+CSS.escape(id)+'"]'), row=fr&&fr.closest('.fl-row');
        if(v.fill.kind==='todo'){if(row)bad.push(w+' 還沒填,🚀 卻列了一行');}
        else if(!row||row.className.indexOf('fl-'+v.fill.kind)<0)bad.push(w+' 🚀 沒放在「'+v.fill.kind+'」那一格');
        else if(v.fill.text&&row.querySelector('.fl-st').textContent!==v.fill.text)bad.push(w+' 🚀 寫「'+row.querySelector('.fl-st').textContent+'」,下一步「'+v.fill.text+'」');});
      if(n<I.length)bad.push('只比到 '+n+' 張');
      document.querySelector('[data-tab="none"]').click();
      return bad.slice(0,8).join('；');
    """,
    'drawn_case', {'fresh_page': True},
))




CHECKS.append((
    '按下去等後台回話:卡片在回話前維持原樣、按鈕全部停用,連按兩下只送一次;回話後照後台存好的畫,可以復原(#343、user story 5)',
    r"""
      var I=P.ids, bad=[], id=I[3], sent=[];
      await T.resync(); document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
      [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open)d.querySelector('summary').click();});
      await T.sleep(50);   // 展開記在 toggle 事件裡(下一輪才到):人點完公司列再按按鈕,中間一定隔得到
      var c=await T.until(function(){return T.card(id);},5000); if(!c)return '找不到停著等你的那張';
      var b=c.querySelector('[data-approve]:not([disabled])'); if(!b)return '那張沒有能按的「✅ 確認送出」';
      var before=c.querySelector('.ap-line').textContent, f0=window.fetch;
      window.fetch=function(u,o){if(String(u).indexOf('/api/save')>=0&&/"ev":"confirm"/.test((o&&o.body)||''))sent.push(1); return f0.apply(this,arguments);};
      try{
        b.click(); b.click();
        var c1=T.card(id), line=c1&&c1.querySelector('.ap-line');
        if(!line||line.textContent!==before)bad.push('後台還沒回話,卡上那一行就變了('+before+' → '+(line?line.textContent:'沒有')+')');
        var live=c1?[].slice.call(c1.querySelectorAll('button')).filter(function(x){return !x.disabled;}):[];
        if(live.length)bad.push('等後台回話時還有 '+live.length+' 顆按得下去('+live[0].textContent.trim()+')');
        var again=c1&&c1.querySelector('[data-approve]'); if(again)again.click();
        await T.sleep(50); await T.idle();
      }finally{window.fetch=f0;}
      if(sent.length!==1)bad.push('確認送出送了 '+sent.length+' 次');
      var st=((await T.state())[id]||{}).ds;
      if(st!=='confirmed')bad.push('後台回話後不是你已確認('+st+')');
      var c2=T.card(id); if(!c2||!/你已確認/.test(c2.textContent))bad.push('後台回話後卡上沒照存好的畫(還沒寫你已確認:'+(c2?c2.querySelector('.ap-line').textContent:'沒有卡')+')');
      var u=document.querySelector('#snack.on .snack-undo');
      if(!u)bad.push('確認之後沒有復原'); else{u.click(); await T.sleep(50); await T.idle();
        if(((await T.state())[id]||{}).ds!=='parked')bad.push('復原後沒回到停著等你');}
      document.querySelector('[data-tab="none"]').click();
      return bad.join('；');
    """,
    'drawn_case', {'fresh_page': True},
))


def chains(todo):
    """把「接著上一條」的檢查(opts 的 with_previous)跟上一條綁成一串;分組時一串整個放同一組、照順序跑。"""
    out=[]
    for item in todo:
        if out and len(item)>3 and item[3].get('with_previous'):
            out[-1].append(item)
        else:
            out.append([item])
    return out


# 檢查的尺寸(CDP 模擬,手機連觸控一起);desktop 是不模擬
VIEWPORTS={'mobile':{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True},
           'wide':{'width':1440,'height':900,'deviceScaleFactor':1,'mobile':False}}


def _viewport(cdp, profile):
    if profile in VIEWPORTS:
        cdp.send('Emulation.setDeviceMetricsOverride',VIEWPORTS[profile])
    else:
        cdp.send('Emulation.clearDeviceMetricsOverride')
    cdp.send('Emulation.setTouchEmulationEnabled',{'enabled':True,'maxTouchPoints':1} if profile=='mobile' else {'enabled':False})


def run_shards(n):
    """分成 n 組同時跑(跟 Playwright 的 --shard、pytest-xdist 一樣的做法):每組一個行程,各自開看板副本和瀏覽器。
    回結束碼:任一組失敗就是 1。"""
    logs=[tempfile.TemporaryFile(mode='w+',encoding='utf-8') for _ in range(n)]   # 各寫各的,不會互相卡住
    procs=[subprocess.Popen([sys.executable,os.path.abspath(__file__),*sys.argv[1:],'--shard',f'{k}/{n}'],   # 有 --shard 的不再分組
                            stdout=logs[k],stderr=subprocess.STDOUT) for k in range(n)]
    code=0
    for k,p in enumerate(procs):
        p.wait(); logs[k].seek(0); out=logs[k].read(); logs[k].close()
        if out.strip(): print(f'── 第 {k+1}/{n} 組 ──\n'+out.rstrip())
        code=code or (1 if p.returncode else 0)
    return code


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--board',default='',help='拿哪一份看板的資料來測(只讀它的資料;外殼一律用 board/ 的)。預設是示範看板(demo.py)')
    ap.add_argument('--quiet',action='store_true')
    ap.add_argument('--only',default='',help='只跑名稱含這段字的檢查(除錯用:檢查之間會互相影響,單跑才分得出來)')
    ap.add_argument('--timing',action='store_true',help='最後列出每一條花幾秒(找慢的那幾條)')
    ap.add_argument('--workers',type=int,default=int(os.environ.get('BOARD_CHECK_WORKERS') or 1),
                    help='分成幾組同時跑(每組自己的看板副本、瀏覽器,互不干擾);CI 用 4')
    ap.add_argument('--shard',default='',help=argparse.SUPPRESS)   # 內部用:i/N,只跑第 i 組
    ap.add_argument('--fast',action='store_true',
                    help='改介面時自己先跑的快版:跳過要等假流程跑完、等 8 秒反悔期的那幾條(SLOW)。'
                         'CI 一律跑完整版,不准用這個')
    a=ap.parse_args()
    if a.workers>1 and not a.shard:
        sys.exit(run_shards(a.workers))
    shard,shards=(int(x) for x in a.shard.split('/')) if a.shard else (0,1)
    fails=[]; took=[]; t_all=time.time()
    try:
        if shard==0:
            check_bank_export_and_history()
            if not a.quiet: print('  ✅ 題庫存檔、Markdown 匯出與資料夾版本紀錄')
    except Exception as e:  # noqa: BLE001 — 檢查本身出錯算這一條沒過,照實列進失敗清單
        fails.append(('題庫存檔與資料夾版本紀錄',str(e)[:160]))
    with ExitStack() as stack:
        if a.board:
            board=os.path.abspath(a.board)
            if not os.path.isfile(board): sys.exit('找不到看板:'+board)
        else:
            import demo   # 示範看板:外殼是 board/ 現在的版本,資料全是假的
            demo_dir=stack.enter_context(tempfile.TemporaryDirectory(prefix='boardcheck-demo-'))
            board=demo.build(os.path.join(demo_dir,'board.html'),int(os.environ.get('JOBSALVO_DEMO_EXTRA') or 0))
        boardcheck_home=stack.enter_context(tempfile.TemporaryDirectory(prefix='boardcheck-home-'))
        stack.enter_context(mock.patch.dict(os.environ,JOBSALVO_HOME=boardcheck_home))
        preference_note(boardcheck_home)
        flow_off(boardcheck_home)
        skill=os.path.join(boardcheck_home,'custom','skills','board-check.md')
        os.makedirs(os.path.dirname(skill),exist_ok=True)
        with open(skill,'w',encoding='utf-8') as f: f.write('# 看板檢查用 skill\n')
        sb=shot.Sandbox(board)
        stack.callback(sb.close)
        stack.enter_context(mock.patch.dict(os.environ,AGENT_BOARD=sb.copy))
        # Prepare the fake resume before starting Chrome; both use the same local
        # server, and Chrome startup can otherwise starve this short API request.
        seed_check_resume(sb.url)
        # 開瀏覽器、載入頁面、尺寸模擬、失敗時的錄影交給 Playwright(以前自己用 CDP 寫,Chrome 沒關乾淨、卡住都要自己修)。
        # 用這台機器本來就有的 Chrome(跟截圖、印 PDF 同一個),不另外下載瀏覽器。
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        import chrome_bin
        pw=stack.enter_context(sync_playwright())
        browser=pw.chromium.launch(executable_path=chrome_bin.chrome()[0], args=['--disable-gpu','--hide-scrollbars'])
        stack.callback(browser.close)
        ctx=browser.new_context(no_viewport=True)   # 尺寸照舊用 CDP 模擬(手機要連觸控一起)
        ctx.tracing.start(screenshots=True, snapshots=True)
        page=ctx.new_page()
        page.on('dialog', lambda d: d.accept())      # 例如按停止時的確認框:當成使用者按了確定
        cdp=ctx.new_cdp_session(page)
        shot.SB_URL[0]=sb.url
        # 檢查用的 T/FB0 每次載入頁面都要在:有的動作(存設定)會重新載入頁面。
        page.add_init_script(HELPERS)
        page.add_init_script(axe_source())
        target_url=sb.url.rstrip('/') + '/'
        def ready():   # 到了那個網址、而且載入完(load);逾時講中文
            try: page.wait_for_url(target_url, timeout=60000)
            except PWTimeout: raise TimeoutError('看板頁面載入逾時') from None
        page.goto(sb.url)
        ready()
        trace_dir=os.environ.get('BOARD_CHECK_TRACES') or os.path.join(tempfile.gettempdir(),'board-check-traces')
        todo=[x for x in CHECKS if a.only in x[0]] if a.only else CHECKS
        if a.fast:
            todo=[x for x in todo if not any(x[0].startswith(p) for p in SLOW)]
            print(f'快版:跳過 {len(CHECKS)-len(todo)} 條慢的(完整版才算數,CI 跑完整版)')
        todo=[x for c in chains(todo)[shard::shards] for x in c]
        for item in todo:
            name,body=item[0],item[1]
            profiles=(item[3].get('viewports',['desktop']) if len(item)>3 else [None])
            for profile in profiles:
                t0=time.time()
                ctx.tracing.start_chunk()
                try:
                    ready()
                    if profile is not None:
                        _viewport(cdp,profile); page.wait_for_timeout(200)
                    page.add_script_tag(content=HELPERS)   # 每一條開始前重新裝上 T(跟以前一樣)
                    arg=PRE[item[2]](sb.copy) if len(item)>2 else None
                    if len(item)>3 and item[3].get('fresh_page'):
                        # 頁面是載入時讀設定(自動流程開關);這條要先重新載入頁面(T 由 init script 自動裝上)再跑
                        page.goto(sb.url); page.wait_for_timeout(2500)
                    msg=page.evaluate("(async function(P){%s})(%s)"%(body,json.dumps(arg)))
                except Exception as e:  # noqa: BLE001 — 檢查本身出錯算這一條沒過,照實列進失敗清單
                    msg='檢查本身出錯:'+str(e)[:120]
                report=name+(f' ({profile})' if profile is not None and len(profiles)>1 else '')
                took.append((time.time()-t0,report))
                if msg:
                    fails.append((report,msg))
                    # 失敗的那一條留下 Playwright 錄影(每一步的截圖、DOM、網路),用 playwright show-trace 看
                    os.makedirs(trace_dir,exist_ok=True)
                    ctx.tracing.stop_chunk(path=os.path.join(trace_dir,f'group{shard+1}-{len(fails):02d}.zip'))
                else:
                    ctx.tracing.stop_chunk()
                    if not a.quiet: print(f'  ✅ {report}')
            if 'mobile' in profiles or 'wide' in profiles:
                try:
                    _viewport(cdp,'desktop')
                    page.wait_for_timeout(200)
                except Exception as e:  # noqa: BLE001 — 照實列進失敗清單
                    fails.append((name,'切回桌機尺寸失敗:'+str(e)[:120]))
    if a.timing:
        print(f'\n每一條花幾秒(全部 {time.time()-t_all:.0f} 秒,含開沙箱與 Chrome):')
        for sec,report in sorted(took,reverse=True):
            print(f'  {sec:6.1f}  {report}')
    if fails:
        print('\n看板介面規矩沒守住:')
        for n,m in fails: print(f'  ❌ {n} ← {m}')
        print(f'\n失敗那幾條的錄影在 {trace_dir}(python3 -m playwright show-trace <檔名>)')
        print('\n(規矩寫在 tools/board_check.py,要改行為先改那裡的檢查,不要偷偷改掉行為。)')
        sys.exit(1)
    if not a.quiet: print('看板介面規矩全過。')

if __name__=='__main__': main()
