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
import os,sys,json,argparse,tempfile,re,time,subprocess
import urllib.request
from contextlib import ExitStack
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
   rm.click();
   var s=document.getElementById('snack');
   var ok=s&&s.className==='on'&&s.querySelector('.snack-undo');
   if(ok)s.querySelector('.snack-undo').click();
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
  resync:async function(){await T.idle(); T.sync(); await T.idle();}
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
from board_check_ans import ANS_CHECKS   # 表單答案庫那幾條,獨立一支檔
CHECKS=CHECKS+ANS_CHECKS

def external_write(board):
    """模擬 cut_tailor/custom_queue 那種不經過伺服器、直接寫檔的改動。"""
    import board_doc as bd
    d=bd.parse(open(board,encoding='utf-8').read()); fb=json.loads(d['fb'])
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
    """模擬投遞前驗收重跑之後多了一個問題(board_status --write 也是直接寫檔)。"""
    import board_doc as bd
    with bd.live_lock(board):
        d=bd.parse(open(board,encoding='utf-8').read()); fb=json.loads(d['fb'])
        ready=[j['id'] for j in d['data']['jobs'] if (fb.get(j['id']) or {}).get('app')=='ready']
        fid=ready[0] if ready else None
        sid=ready[1] if len(ready)>1 else None
        if not fid: return {}
        st=dict(d['data'].get('status') or {},schema_version=2,checked_links=True)
        st['issues']=[x for x in st.get('issues') or [] if x.get('jid') not in ready]+[
            {'jid':fid,'t':'','stage':'ready','msg':'測試用的原因'}]
        if sid:
            st['issues'].append({'jid':sid,'kind':'unverified','soft':True,'stage':'ready',
                                 'msg':'無法確認是否已下架'})
        d['data']['status']=st
        doc=bd.assemble(d['sty'],d['thdr'],d['tail'],d['data'],d['fb'],d['app'])
        open(board+'.tmp','w',encoding='utf-8').write(doc); os.replace(board+'.tmp',board)
    return {'fid':fid,'sid':sid}

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
    resume['resumes'] = [{'id': 'board-check', 'name': '看板檢查履歷', 'enabled': True,
                         'files': {lang: rel}}]
    resume['attachments'] = []
    body = json.dumps({'settings': settings}, ensure_ascii=False).encode('utf-8')
    save = urllib.request.Request(url + '/api/settings', data=body, method='POST',
                                  headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(save, timeout=setup_timeout) as response:
        if response.status != 200:
            raise RuntimeError('無法儲存看板檢查用履歷設定')

FLOW_OFF = {'like_to_prep': False, 'auto_prep': False, 'auto_advance': False, 'auto_fill': False, 'replies_at': ''}
FLOW_ON = {'like_to_prep': True, 'auto_prep': True, 'auto_advance': True, 'auto_fill': True, 'replies_at': ''}


def flow_off(home):
    """這些規矩驗的是他手動按的那條路(種資料、按按鈕、看結果)。自動流程開著的話,
    種下去的新卡會被它接走(自動準備、自動填表),驗不到手動那條。所以副本預設關著,
    自動流程那幾條自己開、驗完自己關(見 flow_set)。"""
    path = os.path.join(home, 'jobsalvo.json')
    data = {}
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    data['flow'] = dict(FLOW_OFF)
    # agent 的 Chrome 用副本自己的資料夾(當成還沒建過):設定頁畫的是第一次設定的樣子,也碰不到真的那一個
    data['browser'] = {**(data.get('browser') or {}),
                       'data_dir': os.path.join(home, 'agent-chrome'), 'state': os.path.join(home, 'agent-chrome.json')}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


SB_URL = ['']


def flow_set(on):
    """透過副本伺服器的設定 API 開關自動流程(跟使用者在設定頁按儲存同一條路)。"""
    base = SB_URL[0]
    cur = json.load(urllib.request.urlopen(base + '/api/settings', timeout=10))
    settings = cur.get('settings') or {}
    settings['flow'] = dict(FLOW_ON if on else FLOW_OFF)
    req = urllib.request.Request(base + '/api/settings', data=json.dumps({'settings': settings}).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    json.load(urllib.request.urlopen(req, timeout=10))


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

            with tempfile.TemporaryDirectory(prefix='boardcheck-foreign-history-') as foreign_home:
                git = folder_history._git()
                folder_history._run(git, foreign_home, 'init', '--initial-branch=main')
                folder_history._run(git, foreign_home, 'remote', 'add', 'origin',
                                    'https://example.invalid/project.git')
                settings = copy.deepcopy(cf.DEFAULTS)
                settings['board']['file'] = 'board.html'
                with open(os.path.join(foreign_home, cf.NAME), 'w', encoding='utf-8') as target:
                    json.dump(settings, target, ensure_ascii=False)
                with open(os.path.join(foreign_home, 'board.html'), 'w', encoding='utf-8') as target:
                    target.write(bd.assemble(':root{}', '<b id="stat-first">0</b>', '',
                                             {'jobs': []}, '{}', '/*app*/'))
                cf.reload(foreign_home)
                try:
                    folder_history.flush_now(foreign_home)
                    history_message = settings_api.get()['git_history']['message']
                    if '不是 jobsalvo 建立' not in history_message:
                        raise RuntimeError('設定頁沒有說明外部 Git repo 不會被 jobsalvo 提交')
                    if folder_history._run(git, foreign_home, 'rev-parse', '--verify', 'HEAD',
                                            check=False).returncode == 0:
                        raise RuntimeError('jobsalvo 在外部 Git repo 建立了 commit')
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

def shared_rule_cases(_board):
    path=os.path.join(HERE,'..','tests','fixtures','board-rule-cases.json')
    with open(path,encoding='utf-8') as f: return json.load(f)


def lazy_resume(board):
    """在副本的待決與準備卡放 PDF 頁圖,語言挑沒有原檔的那個,驗點開才載入。"""
    import board_doc as bd
    with bd.live_lock(board):
        d=bd.parse(open(board,encoding='utf-8').read()); fb=json.loads(d['fb'])
        fid=next((j['id'] for j in d['data']['jobs'] if (fb.get(j['id']) or {}).get('app')=='ready'),None)
        top_id=next((j['id'] for j in d['data']['jobs'] if (fb.get(j['id']) or {}).get('app')=='ready' and j['id']!=fid),None)
        prep_id=next((j['id'] for j in d['data']['jobs'] if (fb.get(j['id']) or {}).get('app')=='prep'),None)
        if not fid or not top_id or not prep_id: return {}
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
        doc=bd.assemble(d['sty'],d['thdr'],d['tail'],d['data'],json.dumps(fb,ensure_ascii=False),d['app'])
        open(board+'.tmp','w',encoding='utf-8').write(doc); os.replace(board+'.tmp',board)
    return {'fid':fid,'top_id':top_id,'prep_id':prep_id}

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
       var sels=[].slice.call(card.querySelectorAll('.stage-b[data-adv],.stage-b[data-back],[data-err]')).map(function(x){
         return x.hasAttribute('data-adv')?'[data-adv="'+x.getAttribute('data-adv')+'"]':x.hasAttribute('data-back')?'[data-back="'+x.getAttribute('data-back')+'"]':'[data-err]';});
       for(const sel of sels){
         openTab(tab); await T.sleep(120);
         var c2=T.card(fid), b=c2&&c2.querySelector(sel); if(!b||b.disabled)continue;
         var before=window.__jobsalvoFlow.fb(fid), where=tab+' '+fid.slice(-12)+' '+sel;
         b.click(); clicks++; await T.sleep(150);
         inv().forEach(function(x){bad.push(where+':'+x);});
         var now=window.__jobsalvoFlow.tabOf(fid);
         if(now!==tab&&T.card(fid))bad.push(where+':卡已經到「'+now+'」,卻還留在這一頁');
         var u=document.querySelector('#snack.on .snack-undo');
         if(!u){bad.push(where+':按了沒有復原'); continue;}
         u.click(); await T.sleep(150);
         inv().forEach(function(x){bad.push(where+' 復原後:'+x);});
         var after=window.__jobsalvoFlow.fb(fid);
         ['s','app','rm','approve','sent_at','oc'].forEach(function(k){
           if(JSON.stringify(before[k])!==JSON.stringify(after[k]))bad.push(where+':復原後 '+k+' 沒回到原樣('+JSON.stringify(before[k])+' → '+JSON.stringify(after[k])+')');});
       }
     }
   }
   await T.idle(); openTab('none');
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
     b.click(); await T.sleep(250);
     var u=document.querySelector('#snack.on .snack-undo');
     if(!u){
       // 沒跳復原也可以:卡片留在原地,按原本那顆就回去(表態就是這樣,一次標很多張不用每次跳提示)
       var c=fid&&T.card(fid), was=fid?(JSON.parse(f0).s||''):'';
       var back=c&&c.querySelector(was?'[data-s="'+was+'"]':'[data-s].on');
       if(!back){bad.push(where+'「'+key+'」按了'+(c?'改不回去':'卡片就不見了')+',又沒有復原'); return;}
       back.click(); await T.sleep(250);
       if((window.__jobsalvoFlow.fb(fid).s||'')!==was)bad.push(where+'「'+key+'」在原地按回去沒回到原樣');
       return;}
     u.click(); await T.sleep(250);
     if(snap()!==before||(fid&&JSON.stringify(window.__jobsalvoFlow.fb(fid))!==f0))bad.push(where+'「'+key+'」按復原沒回到原樣');}
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
     'shared_rule_cases':shared_rule_cases,'lazy_resume':lazy_resume,'xss_job':xss_job}
CHECKS=CHECKS+SAVE_CHECKS+PREP_CHECKS+FIND_CHECKS+[
  ('共用規則案例表:空值、核准與核准前預覽、卡名、公司分組與同一家、分類關鍵字、死線(當地時間)、存檔衝突合併',r"""
    var R=window.__jobsalvoSharedRules, bad=[];
    if(!R)return '看板沒有提供共用規則介面';
    function same(a,b){return JSON.stringify(a)===JSON.stringify(b);}
    (P.mark_values||[]).forEach(function(c){
      var value=R.lean(c.value), base=R.lean(c.base);
      if(!same(value,c.normalized))bad.push('標記空值/'+c.name+':正規化為 '+JSON.stringify(value)+'，預期 '+JSON.stringify(c.normalized));
      function sorted(v){if(Array.isArray(v))return v.map(sorted);if(v&&typeof v==='object'){
        var o={};Object.keys(v).sort().forEach(function(k){o[k]=sorted(v[k]);});return o;}return v;}
      var equal=JSON.stringify(sorted(value))===JSON.stringify(sorted(base));
      if(equal!==c.same)bad.push('標記比對/'+c.name+':same='+equal+'，預期 '+c.same);
    });
    (P.approvals||[]).forEach(function(c){
      var problem=R.approvalProblem(c.url,c.state), blocked=problem!==null;
      if(blocked!==c.blocked)bad.push('核准/'+c.name+':blocked='+blocked+'，預期 '+c.blocked+' ('+problem+')');
      if(c.problem&&c.name!=='page refill needed'&&problem!==c.problem)bad.push('核准/'+c.name+':原因 '+problem+'，預期 '+c.problem);
    });
    (P.approve_blockers||[]).forEach(function(c){
      var r=R.approveBlocker(c.url,JSON.parse(JSON.stringify(c.state)));
      if(c.problem_contains){if(!r.problem||r.problem.indexOf(c.problem_contains)<0)bad.push('核准前預覽/'+c.name+':'+r.problem+'，預期含 '+c.problem_contains);}
      else if(r.problem!==c.problem)bad.push('核准前預覽/'+c.name+':'+r.problem+'，預期 '+c.problem);
      if(r.approve_after)bad.push('核准前預覽/'+c.name+':預覽完把核准留下來了');
    });
    (P.companies||[]).forEach(function(c){
      var co=R.companyOf({id:c.id||'',target:c.target},c.alias||{});
      if(co!==c.expected)bad.push('公司分組/'+c.target+': '+co+'，預期 '+c.expected);
    });
    (P.company_same||[]).forEach(function(c){
      if(R.sameCo(c.a,c.b)!==c.same)bad.push('同一家公司/'+c.a+' vs '+c.b+'：預期 '+c.same);
    });
    if(!R.mergeEdit)bad.push('看板沒有提供存檔衝突合併');
    else (P.merges||[]).forEach(function(c){
      var r=R.mergeEdit(c.base,c.mine,c.theirs);
      if(!same(R.lean(r.value),R.lean(c.value))||!same(r.clash,c.clash))
        bad.push('合併/'+c.name+':得到 '+JSON.stringify(r)+',預期 '+JSON.stringify({value:c.value,clash:c.clash}));
    });
    if(!R.deadlinePassed)bad.push('看板沒有提供死線判斷');
    else (P.deadlines||[]).forEach(function(c){
      var passed=R.deadlinePassed(c.deadline,c.now);
      if(passed!==c.passed)bad.push('死線/'+c.name+':passed='+passed+',預期 '+c.passed);
    });
    (P.card_names||[]).forEach(function(c){
      var name=R.cardName({target:c.target});
      if(name!==c.expected)bad.push('卡名/'+c.target+': '+name+'，預期 '+c.expected);
    });
    var categories=function(c){return c.kind==='category'?
      [{name:c.setting_name,icon:'•',match:c.pattern},{name:'其他',icon:'•',match:''}]:
      [{name:'其他',icon:'•',match:''}];};
    var tags=function(c){return c.kind==='tag'?[{name:c.setting_name,match:c.pattern}]:[];};
    (P.regex||[]).forEach(function(c){
      var board={categories:categories(c),tags:tags(c)}, problems=R.regexProblems(board);
      var valid=problems.length===0, label=c.kind==='category'?'類別':'標籤';
      if(valid!==c.browser_valid)bad.push('關鍵字/'+c.name+':瀏覽器有效='+valid+'，預期 '+c.browser_valid);
      if(!valid&&(!problems.join('；').includes(label)||!problems.join('；').includes(c.setting_name)))
        bad.push('關鍵字/'+c.name+':拒絕訊息沒有指出'+label+'名稱 '+c.setting_name);
      if(valid){var got=R.classify(board,{target:c.target,note:c.body,ammo:''});
        if(got.category!==c.category)bad.push('分類/'+c.name+': '+got.category+'，預期 '+c.category);
        if(!same(got.tags,c.tags))bad.push('標籤分類/'+c.name+': '+JSON.stringify(got.tags)+'，預期 '+JSON.stringify(c.tags));}
    });
    var categoryCase=(P.regex||[]).find(function(c){return c.kind==='category'&&c.browser_valid&&c.expected_match;});
    var tagCase=(P.regex||[]).find(function(c){return c.kind==='tag'&&c.browser_valid&&c.expected_match;});
    if(!categoryCase||!tagCase)bad.push('案例表缺少有效的類別或標籤分類案例');
    else{
      var original=null;
      try{
        var current=await fetch('/api/settings').then(function(r){return r.json();});
        original=JSON.parse(JSON.stringify(current.settings||{}));
        var candidate=JSON.parse(JSON.stringify(original)); candidate.board=candidate.board||{};
        candidate.board.categories=categories(categoryCase); candidate.board.tags=tags(tagCase);
        var response=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({settings:candidate})});
        var saved=await response.json();
        if(!response.ok||!saved.ok)bad.push('分類關鍵字儲存失敗: '+(saved.msg||response.status));
        else{
          var persisted=await fetch('/api/settings').then(function(r){return r.json();});
          var active=(persisted.effective||{}).board||{};
          var category=R.classify(active,{target:categoryCase.target,note:categoryCase.body,ammo:''});
          var tag=R.classify(active,{target:tagCase.target,note:tagCase.body,ammo:''});
          if(category.category!==categoryCase.category)bad.push('已存的類別關鍵字沒有用來分類: '+category.category);
          if(!same(tag.tags,tagCase.tags))bad.push('已存的標籤關鍵字沒有用來分類: '+JSON.stringify(tag.tags));
        }
      }catch(e){bad.push('分類關鍵字存檔檢查出錯: '+e.message);}
      finally{if(original!==null)try{
        var restored=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({settings:original})});
        var restoreResult=await restored.json();
        if(!restored.ok||!restoreResult.ok)bad.push('無法還原沙箱設定: '+(restoreResult.msg||restored.status));
      }catch(e){bad.push('還原沙箱設定出錯: '+e.message);}}
    }
    return bad.join('；');
  """,'shared_rule_cases'),
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
  """,'shared_rule_cases',{'viewports':['desktop','mobile']})]



def custom_profile_reminder_cases(board):
    """在看板副本上安排三種投遞方式,檢查提醒只出現在另開客製履歷。"""
    import board_doc as bd
    data = bd.parse(open(board, encoding='utf-8').read())
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
                'resume:general': {'status': 'review', 'name': '客製履歷'},
            }
            entry['app'] = 'ready'
            entry.setdefault('apply', {})['delivery'] = delivery

    bd.set_fb(mut, live=board, by='board_check')
    return {'cards': cards}


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
        var accept=card.querySelector('[data-cust-action="accept"][data-cust-item="resume:general"]');
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
    parsed = bd.parse(open(board, encoding='utf-8').read())
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
        entry['apply'] = {
            'stage': 'fill', 'ok': False, 'issues': [issue],
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
    d=bd.parse(open(board,encoding='utf-8').read()); fb=json.loads(d['fb'])
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
))


def filled_ship_case(board):
    """一張 agent 填好、停在送出前的可投遞卡(有那段對話)。"""
    import board_doc as bd
    jobs = bd.parse(open(board, encoding='utf-8').read())['data'].get('jobs') or []
    job = jobs[11]

    def mut(fb):
        entry = fb.setdefault(job['id'], {})
        entry.pop('approve', None)
        entry['app'] = 'ship'
        entry['form'] = {'plat': '測試', 'f': []}
        entry['apply'] = {'stage': 'fill', 'ok': True, 'issues': [], 'session': 'board-check-live', 'tab_id': '12345',
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
      if(m)m.querySelector('.rzm-x').click(); await T.sleep(200);
      window.fetch=function(u){if(String(u).indexOf('/api/live?')===0)return Promise.resolve(new Response('x',{status:404})); return of.apply(this,arguments);};
      T.card(P.id).querySelector('a[data-apshot]').click(); await T.sleep(500); window.fetch=of;
      m=document.getElementById('rzmodal');
      if(!/找不到/.test(m.textContent))bad.push('那一頁不在了,彈窗沒講');
      if(!m.querySelector('[data-apshot-refill]'))bad.push('那一頁不在了,彈窗裡沒有「讓 agent 重填這張」');
      m.querySelector('.rzm-x').click(); await T.sleep(200);
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
    jobs = bd.parse(open(board, encoding='utf-8').read())['data'].get('jobs') or []
    job = next(j for j in jobs[12:] if str(j['id']).startswith('http'))

    def mut(fb):
        entry = fb.setdefault(job['id'], {})
        entry.pop('approve', None)
        entry['app'] = 'ship'
        entry['form'] = {'plat': '測試', 'f': []}
        entry['apply'] = {'stage': 'fill', 'ok': False, 'issues': [apply_tab.HUMAN_CHECK], 'session': 'board-check-cf',
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
    jobs = bd.parse(open(board, encoding='utf-8').read())['data'].get('jobs') or []
    if len(jobs) < 11:
        raise ValueError('示範看板少於十一張卡')
    job = jobs[10]

    def mut(fb):
        entry = fb.setdefault(job['id'], {})
        for k in ('apply', 'approve', 's'):
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


CHECKS.append((
    '面試講稿估時:有中文的段落照原本數字元,整段英文數單字,英文稿不會被估成好幾倍',
    r'''
      var R=window.__jobsalvoSharedRules, bad=[]; if(!R||!R.speechSecs)return '看板沒有提供講稿估時';
      var en=Array(151).join('word ').trim(), zh=Array(43).join('字');
      // 有中文的段落照原本數非空白字元(使用者的語速是照這個量的),夾的英文也算字元
      var cases=[['英文 150 字',en,60],['中文 42 字',zh,10],['中文夾英文',zh+' LLM Ops',12]];
      cases.forEach(function(c){var got=Math.round(R.speechSecs(c[1]));
        if(Math.abs(got-c[2])>1)bad.push(c[0]+' 估 '+got+' 秒,應該約 '+c[2]+' 秒');});
      return bad.join('；');
    ''',
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
    url = SB_URL[0]
    settings = json.load(urllib.request.urlopen(url + '/api/settings', timeout=60))['settings']
    original = json.loads(json.dumps(settings))
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
    with open(board, encoding='utf-8') as source:
        parsed = bd.parse(source.read())
    jobs = parsed['data'].get('jobs') or []
    if not jobs:
        raise ValueError('示範看板沒有職缺,無法驗送出頁證據標記')
    job = jobs[0]
    evidence = '已查信箱與可讀平台應徵紀錄,仍未找到確認信或平台紀錄;目前只有送出頁證據'

    def mut(fb):
        fb.setdefault(job['id'], {}).update(app='sent', sent_at='2026-09-20', ev=evidence)

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
    parsed = bd.parse(open(board, encoding='utf-8').read())
    jobs = parsed['data'].get('jobs') or []
    if not jobs:
        raise ValueError('示範看板沒有職缺,無法驗查過回音日期')
    job = jobs[0]
    checked_at = '2026-09-25'

    def mut(fb):
        fb.setdefault(job['id'], {}).update(
            app='sent', sent_at='2026-09-20',
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
    with open(board, encoding='utf-8') as f:
        parsed_board = bd.parse(f.read())
    feedback = json.loads(parsed_board['fb'])
    jobs = parsed_board['data'].get('jobs') or []
    job = next((item for item in jobs if (feedback.get(item['id']) or {}).get('ev')), None)
    if not job:
        weak_submission_evidence_case(board)
        with open(board, encoding='utf-8') as f:
            parsed_board = bd.parse(f.read())
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


def _wait_for_page_ready(page, target_url, timeout=60):
    try:
        page.wait_for_url(target_url, timeout=timeout * 1000)
        page.wait_for_load_state('load', timeout=timeout * 1000)
        return True
    except Exception:   # Playwright 的 TimeoutError:回 False,呼叫的人講「載入逾時」
        return False


# 快版(--fast)跳過的:這幾條要等副本上的假流程(job_fake)真的跑完,或等代投核准後 8 秒的反悔期,
# 一條就 10~60 秒,合起來佔完整版一半以上的時間。規矩本身沒變,只是改介面時先不等它們。
SLOW=('代投:','代投修改:','找新職缺:','找新職缺面板','找缺紀錄:','跑準備區:')


def flow_on(board):
    """開自動流程,並放一份「驗收過、沒有問題」的結果(示範看板沒有;沒有的話推進可投遞一律被擋,那是對的)。"""
    import board_doc as bd
    bd.set_data(lambda d, _fb: d.__setitem__('status', {'schema_version': 2, 'at': '2026-01-01 00:00',
                                                         'checked_links': True, 'issues': []}), live=board)
    # 一條他確認過的答案:檢查裡把它標成「改過、網頁待重打」,看自動流程會不會叫 agent 重打
    bd.set_fb(lambda fb: fb.setdefault('__ans__', []).append({'k': 'bc_refix', 'q': '檢查用的題目', 'zh': '測試', 'v': 'test'}),
              live=board)
    flow_set(True)
    return {}


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
      var c=T.open(); if(!c)return '找不到卡';
      var fid=c.getAttribute('data-fid');
      T.mood(fid,'like'); await T.sleep(1800);
      s=await T.state(); if((s[fid]||{}).app!=='prep')bad.push('按 👍 沒有加入準備(app='+(s[fid]||{}).app+')');
      var ok=false;
      for(var i=0;i<45;i++){await T.sleep(1000); s=await T.state(); m=s[fid]||{};
        if(m.app==='sent'){bad.push('自己送出了'); break;}
        if(m.app==='ship'&&m.apply&&m.apply.ok&&m.form){ok=true; break;}}
      if(!ok)return bad.concat('45 秒內沒有自己走到可投遞、填好(app='+(m||{}).app+')').join('；');
      await T.sleep(6000); s=await T.state(); m=s[fid]||{};
      if(m.app!=='ship'||m.approve)bad.push('填好之後自己往下走了(app='+m.app+(m.approve?',核准了':'')+')');
      T.sync(); await T.sleep(1800);
      document.querySelector('[data-tab="ship"]').click(); await T.sleep(400);
      [].slice.call(document.querySelectorAll('#app .cogrp')).forEach(function(d){if(!d.open&&d.querySelector('[data-k]'))d.querySelector('summary').click();});
      await T.sleep(300);
      var card=T.card(fid); if(!card)return bad.concat('可投遞頁找不到那張卡').join('；');
      var main=card.querySelector('.ap-main'); if(!main||!main.hasAttribute('data-approve'))bad.push('填好的卡主按鈕不是「✅ 核准送出」');
      // 填完才換履歷(看板上換履歷／語言、收下客製版都會標 apply.stale):核准要擋,自動流程重填一次
      var a=JSON.parse(JSON.stringify(m.apply)); a.stale='測試:履歷換了';
      var body={__rev__:1,__base__:{}}; body.__base__[fid]=m; body[fid]=Object.assign({},m,{apply:a});
      await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      var p=window.__jobsalvoSharedRules.approvalProblem(fid,(function(){var o={}; o[fid]=body[fid]; o.__ans__=s.__ans__; return o;})());
      if(!/履歷換過了/.test(p||''))bad.push('換了履歷,核准規則沒擋('+p+')');
      var re=false; for(var k=0;k<25;k++){await T.sleep(1000); s=await T.state(); if(!((s[fid]||{}).apply||{}).stale){re=true; break;}}
      if(!re)bad.push('換了履歷之後沒有自己重填');
      if(((s[fid]||{}).app)==='sent')bad.push('重填之後自己送出了');
      // 答案改過(表單上用到那條的欄位標 refill):卡上寫「會自動重打」、不列進要他處理的;停手一下就叫回同一隻 agent 重打
      m=s[fid]; var f=JSON.parse(JSON.stringify(m.form||{f:[]}));
      f.f=(f.f||[]).concat([{q:'檢查用的題目',src:'bank',k:'bc_refix',refill:1}]);
      body={__rev__:1,__base__:{}}; body.__base__[fid]=m; body[fid]=Object.assign({},m,{form:f});
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
        body={__rev__:1,__base__:{}}; body.__base__[fid]=m;
        body[fid]=Object.assign({},m,{approve:{at:'2026-01-01T00:00:00',snap:{}}});
        await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        T.sync(); await T.sleep(500); card=T.card(fid);
        var other=card&&card.querySelector('.vd-b.lg:not(.on)');
        if(!other)bad.push('找不到可切換的語言');
        else{
          other.click(); await T.sleep(1400); s=await T.state(); m=s[fid]||{};
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


def like_prep_only(_board):
    """只開「👍 順手加入準備」,其他自動流程關著:卡會停在準備區,不會被假流程接走。"""
    cur = json.load(urllib.request.urlopen(SB_URL[0] + '/api/settings', timeout=10))
    settings = cur.get('settings') or {}
    settings['flow'] = dict(FLOW_OFF, like_to_prep=True)
    req = urllib.request.Request(SB_URL[0] + '/api/settings', data=json.dumps({'settings': settings}).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    json.load(urllib.request.urlopen(req, timeout=10))
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
    fb = json.loads(bd.parse(open(board, encoding='utf-8').read())['fb'])
    jobs = bd.parse(open(board, encoding='utf-8').read())['data']['jobs']
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
    parsed = bd.parse(open(board, encoding='utf-8').read())
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


def attachment_preview_case(_board):
    """示範資料沒有履歷和附件:透過設定 API 放一份假履歷、一份假附件(PDF),
    頁面重新載入後,待你決定/可投遞的卡上才會出現附件 pill。原本的設定回傳給檢查自己還原。"""
    url = SB_URL[0]
    settings = json.load(urllib.request.urlopen(url + '/api/settings', timeout=60))['settings']
    original = json.loads(json.dumps(settings))
    for rel in ('resume/bc-rz.pdf', 'resume/bc-att.pdf'):
        put = urllib.request.Request(url + '/api/file?path=' + rel, data=b'%PDF-1.4\n%%EOF\n', method='PUT',
                                     headers={'User-Agent': 'board-check'})
        urllib.request.urlopen(put, timeout=60)
    resume = settings.setdefault('resume', {})
    lang = (resume.get('langs') or ['zh'])[0]
    resume['resumes'] = [{'id': 'bc-rz', 'name': '檢查履歷', 'enabled': True, 'files': {lang: 'resume/bc-rz.pdf'}}]
    resume['attachments'] = [{'id': 'bc-att', 'name': '檢查附件', 'enabled': True, 'files': {lang: 'resume/bc-att.pdf'}}]
    save = urllib.request.Request(url + '/api/settings', data=json.dumps({'settings': settings}, ensure_ascii=False).encode('utf-8'),
                                  method='POST', headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(save, timeout=60)
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


def chains(todo):
    """把「接著上一條」的檢查(opts 的 with_previous)跟上一條綁成一串;分組時一串整個放同一組、照順序跑。"""
    out=[]
    for item in todo:
        if out and len(item)>3 and item[3].get('with_previous'):
            out[-1].append(item)
        else:
            out.append([item])
    return out


def run_shards(n):
    """分成 n 組同時跑(跟 Playwright 的 --shard、pytest-xdist 一樣的做法):每組一個行程,各自開看板副本和瀏覽器。
    回結束碼:任一組失敗就是 1。"""
    args,skip=[],False
    for x in sys.argv[1:]:                 # 拿掉 --workers N,其餘參數照傳
        if skip: skip=False; continue
        if x=='--workers': skip=True; continue
        if not x.startswith('--workers='): args.append(x)
    logs=[tempfile.TemporaryFile(mode='w+',encoding='utf-8') for _ in range(n)]   # 各寫各的,不會互相卡住
    procs=[subprocess.Popen([sys.executable,os.path.abspath(__file__),*args,'--shard',f'{k}/{n}'],
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
    except Exception as e:
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
        old_home=os.environ.get('JOBSALVO_HOME')
        os.environ['JOBSALVO_HOME']=boardcheck_home
        if old_home is None:
            stack.callback(os.environ.pop,'JOBSALVO_HOME',None)
        else:
            stack.callback(os.environ.__setitem__,'JOBSALVO_HOME',old_home)
        preference_note(boardcheck_home)
        flow_off(boardcheck_home)
        skill=os.path.join(boardcheck_home,'custom','skills','board-check.md')
        os.makedirs(os.path.dirname(skill),exist_ok=True)
        with open(skill,'w',encoding='utf-8') as f: f.write('# 看板檢查用 skill\n')
        sb=shot.Sandbox(board)
        stack.callback(sb.close)
        previous_agent_board=os.environ.get('AGENT_BOARD')
        os.environ['AGENT_BOARD']=sb.copy
        if previous_agent_board is None:
            stack.callback(os.environ.pop,'AGENT_BOARD',None)
        else:
            stack.callback(os.environ.__setitem__,'AGENT_BOARD',previous_agent_board)
        # Prepare the fake resume before starting Chrome; both use the same local
        # server, and Chrome startup can otherwise starve this short API request.
        seed_check_resume(sb.url)
        # 開瀏覽器、載入頁面、尺寸模擬、失敗時的錄影交給 Playwright(以前自己用 CDP 寫,Chrome 沒關乾淨、卡住都要自己修)。
        # 用這台機器本來就有的 Chrome(跟截圖、印 PDF 同一個),不另外下載瀏覽器。
        from playwright.sync_api import sync_playwright
        import chrome_bin
        pw=stack.enter_context(sync_playwright())
        browser=pw.chromium.launch(executable_path=chrome_bin.chrome()[0], args=['--disable-gpu','--hide-scrollbars'])
        stack.callback(browser.close)
        ctx=browser.new_context(no_viewport=True)   # 尺寸照舊用 CDP 模擬(手機要連觸控一起)
        ctx.tracing.start(screenshots=True, snapshots=True)
        page=ctx.new_page()
        page.on('dialog', lambda d: d.accept())      # 例如按停止時的確認框:當成使用者按了確定
        cdp=ctx.new_cdp_session(page)
        SB_URL[0]=sb.url
        # 檢查用的 T/FB0 每次載入頁面都要在:有的動作(存設定)會重新載入頁面。
        page.add_init_script(HELPERS)
        page.add_init_script(axe_source())
        target_url=sb.url.rstrip('/') + '/'
        page.goto(sb.url)
        if not _wait_for_page_ready(page, target_url):
            raise TimeoutError('看板頁面載入逾時')
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
                    if not _wait_for_page_ready(page, target_url):
                        raise TimeoutError('看板頁面重載後載入逾時')
                    if profile=='mobile':
                        cdp.send('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                        cdp.send('Emulation.setTouchEmulationEnabled',{'enabled':True,'maxTouchPoints':1})
                    elif profile=='wide':
                        cdp.send('Emulation.setDeviceMetricsOverride',{'width':1440,'height':900,'deviceScaleFactor':1,'mobile':False})
                        cdp.send('Emulation.setTouchEmulationEnabled',{'enabled':False})
                    elif profile=='desktop':
                        cdp.send('Emulation.clearDeviceMetricsOverride')
                        cdp.send('Emulation.setTouchEmulationEnabled',{'enabled':False})
                    if profile is not None: page.wait_for_timeout(200)
                    page.add_script_tag(content=HELPERS)   # 每一條開始前重新裝上 T(跟以前一樣)
                    arg=PRE[item[2]](sb.copy) if len(item)>2 else None
                    if len(item)>3 and item[3].get('fresh_page'):
                        # 頁面是載入時讀設定(自動流程開關);這條要先重新載入頁面(T 由 init script 自動裝上)再跑
                        page.goto(sb.url); page.wait_for_timeout(2500)
                    msg=page.evaluate("(async function(P){%s})(%s)"%(body,json.dumps(arg)))
                except Exception as e:
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
                    cdp.send('Emulation.clearDeviceMetricsOverride')
                    cdp.send('Emulation.setTouchEmulationEnabled',{'enabled':False})
                    page.wait_for_timeout(200)
                except Exception as e:
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
