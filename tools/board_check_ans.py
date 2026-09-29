# -*- coding: utf-8 -*-
"""
board_check 的「表單答案庫、代投、agent 回報」那幾條(由 board_check import,規矩寫法一樣:會失敗的檢查)。

守的規矩:
  · 答案只有一個真相:表單答案庫。表單上只記用了哪一條,卡片上的表單只剩一行,
    要看要改答案一律去答案庫。
  · 任何推論的東西先進答案庫、標「我推論的」,他確認一次,用到它的每張卡一起算過,分頁 ⚠ 只算一次。
  · 答案庫每一條都能在看板上改、新增、刪,每一顆按鈕按了都能復原(怕手滑)。改值就是確認;值一改,
    還沒送出的表單標「雇主網頁待重打」,已投遞的不標。還沒送出的表單在用的,刪除變成「清掉答案」(題目留著、等重寫)。
  · 每條答案分「共用」和「這缺專用」:我先判斷,他按一下切換(可復原)。這缺專用的連到那個 JD。
  · 面板和每一條都是看板共用的 fold:預設收起,有待確認的只在標題上亮 ⚠;他展開的那條,重畫後還開著。
  · 英文答案一定附中文,使用者看到的、能改的是中文。他改了中文,
    那條標「英文待重翻」(agent 填表或修改時照中文重翻、記回答案庫;不算使用者的 ⚠),還沒送出的表單標「雇主網頁待重打」。

檢查在副本上跑,自己用 /api/save 種兩張可投遞卡、一張已投遞卡和一條 zz_nat 推論,不依賴看板現在有什麼資料。
每一條開頭先等前一條的自動存檔送完、結尾也等,不然種資料會跟前一條還沒送出的存檔撞在一起。
"""

# 種資料:挑三張沒被封鎖的職缺,兩張放可投遞、一張放已投遞,三張表單都有一欄指向 zz_nat;答案庫加一條「我推論的」。
SEED = r"""
await T.idle();
var jobs=JSON.parse(document.getElementById('data-jobs').textContent).jobs.filter(function(j){return !j.bk;});
var ids=[jobs[0].id,jobs[1].id,jobs[2].id,jobs[3].id];
var fb=await T.state();
var form=function(lock,why){var f={plat:'測試',at:'2026-01-01',f:[{q:'What is your nationality?',src:'bank',k:'zz_nat'}]};
  if(why)f.f.push({q:'Why this role?',src:'bank',k:'zz_why'}); if(lock)f.lock=1; return f;};
var ans=(fb.__ans__||[]).filter(function(e){return !/^zz_/.test(e.k);});
ans.push({k:'zz_nat',q:'你的國籍(測試)',v:'Taiwan',zh:'台灣',why:'測試用的推論 '+Date.now(),inf:'2026-01-01'});
ans.push({k:'zz_empty',q:'空白測試',v:'',why:'',inf:'2026-01-01'});
ans.push({k:'zz_why',q:'為什麼對這個職位有興趣(測試)',v:'Because.',zh:'因為。',pj:1,pjw:'測試用',why:'',at:'2026-01-01'});   // 這缺專用,只有第一張在用   // 答案還空著:不給 ✓(一按就變成「都確認過了」)   // 每次內容都不同:版本號是內容雜湊,種回一模一樣的內容,頁面會以為沒變
// 兩張可投遞都是 agent 已經填好、停在送出前的樣子:核准的是那一頁,還沒填過的卡不給核准
// (真的送出要叫回填這張的那段對話,沒有就整筆作廢,見 apply_run._no_session)。
var filled=function(){return {stage:'fill',ok:true,at:'2026-01-01T00:00:00',issues:[],delivery:{method:'direct_upload'},tab_id:'1'};};
var body={__rev__:1,__ans__:ans}; body[ids[0]]={app:'ship',form:form(0,1),apply:filled()}; body[ids[1]]={app:'ship',form:form(),apply:filled()};
body[ids[2]]={app:'sent',sent_at:'2026-01-01',form:form(1)};
body[ids[3]]={app:'ship',form:form()};   // 第四張:讀過表單、agent 還沒填過(雇主網頁上什麼都還沒有)
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
 """),
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
 """),
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
 """),
 ('答案庫:他改值就是確認,agent 填好、還沒送出的表單標「雇主網頁待重打」,已投遞的、還沒填過的不標;表單在用的按刪除是「清掉答案」,沒人用的真的刪,兩種都能復原', SEED + r"""
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
     if(!/等你寫/.test(row().querySelector('summary').textContent))bad.push('清掉答案後,那一條沒變成等你寫');
     A=T.card(ids[0]); if(!/等你確認/.test((line(A)||{}).textContent||''))bad.push('清掉答案後,卡上沒說要他處理');
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
 """),
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
 """),
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
 """),
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
   A=T.card(ids[0]); if(A&&!/確認失效/.test(A.textContent))bad.push('確認送出作廢了,卡上沒說');
   return await done();
 """),
 ('頁面不見了(agent 的 Chrome 關過):卡上、「🚀 填表進度」、要你處理的都不給 👀、不給「要 agent 改」,主按鈕是重填', SEED + r"""
   var s0=await T.state(), m=s0[ids[1]];
   m.apply={stage:'fill',ok:false,gone:true,at:'2026-01-01T00:00:00',session:'S-test',tab_id:'',where:'chrome',
            issues:['填好的那一頁不見了(agent 的 Chrome 關掉或重開過),要重填'],delivery:{method:'direct_upload'}};
   var body={__rev__:1}; body[ids[1]]=m;
   await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
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
 """),
 ('代投修改:agent 填好的卡有「👀 看現在的頁面」和「✏️ 要 agent 改」;答案改過、網頁待重打時藏起核准;寫一句話交給同一隻 agent,改好了才又能核准', SEED + r"""
   var ab=function(c){return c&&c.querySelector('[data-approve]');};
   var ad=document.querySelector('#app .ans-d'); if(!ad.open){ad.querySelector('summary').click(); await T.sleep(200);}
   row().querySelector('summary [data-ansok]').click(); await T.idle();
   var s0=await T.state(), m=s0[ids[1]];     // 第二張:agent 填好了(有那段對話的 id),之後他改了答案,網頁待重打
   m.apply={stage:'fill',ok:true,at:'2026-01-01T00:00:00',session:'S-test',tab_id:'1',where:'chrome'};
   m.form.f[0].refill=1;
   var body={__rev__:1}; body[ids[1]]=m;
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
 """),
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
 """),
 ('agent 回報:每一頁最上面、預設收起,標題亮待處理件數;每一頁都列全部(這一頁有關的排前面,別頁的有「去那一頁」);按「處理好了」會收進已處理,可以復原', r"""
   await T.idle();
   var jobs=JSON.parse(document.getElementById('data-jobs').textContent).jobs.filter(function(j){return !j.bk;});
   var box=[{id:'rzz1',at:'2026-01-01T09:00:00',from:'代投',msg:'測試:被網站的機器人驗證擋住 '+Date.now(),need:'測試用',n:1}];   // 代投的回報屬於可投遞那一頁
   await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({__rev__:1,__inbox__:box})});
   await T.resync();
   document.querySelector('[data-tab="ship"]').click(); await T.sleep(300);
   var bad=[], d=document.querySelector('#inboxbar details.inbox-d');
   if(!d)return '頁面最上面看不到 agent 回報';
   if(d.open)bad.push('agent 回報預設是開的');
   if(!/1 件要你處理/.test(d.querySelector('summary').textContent))bad.push('標題看不出有 1 件要他處理');
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
