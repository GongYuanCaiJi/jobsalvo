
(function(){
  function $(id){return document.getElementById(id);}
  var D=JSON.parse($('data-jobs').textContent);
  var FB=JSON.parse($('data-fb').textContent||'{}');
  var jobs=D.jobs, catIcon=D.catIcon, catOrder=D.catOrder;
  var masters=D.masters||[];   // 原始履歷的預覽改在設定頁「你的履歷」看;這裡只剩彈窗查不到職缺時的後備
  // 設定(jobsalvo.json,在「⚙ 設定」頁改)由伺服器送頁面時注入 D.cfg:agent 名字、語言、履歷清單、類別、標籤。
  // 簡體介面:設定裡「你看得懂的」選简体中文(zh-CN),伺服器把繁→簡字典(OpenCC tw2sp)附在 cfg.zh_tables。
  // 只轉畫面上的字(文字、提示、標題、彈窗),程式與資料都不轉:程式裡有拿中文比對資料的地方,
  // 資料轉了再存回去版本會對不上。輸入框裡的字是他自己打的,也不動。
  (function zhView(T){if(!T)return;
    // done:轉出來的結果。畫面上的字被換掉會再觸發一次,轉過的不能再轉:「文件夹」再過一次台灣用詞表會變「文档夹」
    var CJK=/[\u3400-\u9fff\uf900-\ufaff]/, memo=new Map(), done=new Set();
    function conv(t){if(!t||!CJK.test(t)||done.has(t))return t; var r=memo.get(t); if(r!==undefined)return r; r=t;
      T.forEach(function(st){var tb=st[0], L=st[1], out='', i=0, n=r.length;
        while(i<n){var k=Math.min(L,n-i), hit=null;
          for(;k>0;k--){var v=tb[r.substr(i,k)]; if(v!==undefined){hit=v;break;}}
          if(hit!==null){out+=hit; i+=k;} else {out+=r[i]; i++;}}
        r=out;});
      if(memo.size>20000){memo.clear(); done.clear();} memo.set(t,r); done.add(r); return r;}
    var SKIP=/^(SCRIPT|STYLE|TEXTAREA|CODE|PRE)$/, ATTR=['title','placeholder','aria-label','data-tip'];
    function skip(el){for(;el&&el.nodeType===1;el=el.parentNode){if(SKIP.test(el.tagName)||el.isContentEditable)return true;}return false;}
    function text(nd){var p=nd.parentNode; if(!p||skip(p))return; var c=conv(nd.data); if(c!==nd.data)nd.data=c;}
    function attrs(el){ATTR.forEach(function(a){var v=el.getAttribute&&el.getAttribute(a); if(v){var c=conv(v); if(c!==v)el.setAttribute(a,c);}});}
    function walk(root){if(root.nodeType===3){text(root);return;} if(root.nodeType!==1||skip(root))return; attrs(root);
      var w=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT), nd;
      while((nd=w.nextNode())){if(nd.nodeType===3)text(nd); else if(!skip(nd))attrs(nd);}}
    new MutationObserver(function(ms){ms.forEach(function(m){
      if(m.type==='characterData')text(m.target);
      else if(m.type==='attributes')attrs(m.target);
      else m.addedNodes.forEach(walk);});})
      .observe(document.documentElement,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:ATTR});
    walk(document.body); document.title=conv(document.title);
    ['alert','confirm','prompt'].forEach(function(f){var o=window[f]; window[f]=function(msg){
      var a=[].slice.call(arguments); a[0]=conv(String(msg==null?'':msg)); return o.apply(window,a);};});
  })((D.cfg||{}).zh_tables);
  // 每張卡的要寄的檔案(用哪份履歷、語言、附哪幾份、客製版還是原始檔、還沒辦法決定的原因):後台 ship.card_files 算的,
  // 看板不自己挑。載入時拿到全部;按了版本、語言、收下客製版就當下問那一張(askShip)
  var SHIP=D.ship_files||{};
  // 每張卡的下一步(後台 next_step 算的,docs/adr/0005):「✅ 確認送出」能不能按、忙的卡為什麼不能換檔或離開流程、
  // agent 會不會自己接手(auto:準備、推進、填表、重打;卡上寫「會自動…」只照它,自動流程挑卡也只照它)、
  // 本來會接這次不接的原因(stop)、這張表單在等你的答案(ask)。
  // 看板不自己算,照它畫;載入時拿全部、存檔回傳有變動的卡、輪詢看到版本變了就重拿
  var NEXT=D.next||{};
  function nextOf(id){return NEXT[id]||{};}
  function autoDo(id){return (nextOf(id).auto||{}).do||'';}
  function autoLine(id){return '<span class="ap-run">⏳ '+esc(nextOf(id).auto.line)+'</span>';}
  // 「✅ 確認送出」不能按的原因(null = 可以按);後台收到確認也照這一句擋
  function confirmWhy(id){var c=nextOf(id).confirm; return c===undefined?'還沒拿到這張的下一步,等一下會自己更新':c;}
  var CFG=D.cfg||{}, AGENT=String(CFG.agent||'Agent').replace(/[<>&"']/g,''), LANGS=CFG.langs||['zh','en'], RESUMES=CFG.resumes||[];
  var CATS=(CFG.categories&&CFG.categories.length)?CFG.categories:[{name:'其他',icon:'•',match:''}];
  var TAGS=CFG.tags||[];
  // 流程設定(⚙ 設定 → 🔁 自動流程):按 👍 就加入準備、準備區／可投遞／填表自動往下跑(tools/autopilot.py)
  var FLOW=CFG.flow||{};
  catOrder=CATS.map(function(c){return c.name;});
  catIcon={}; CATS.forEach(function(c){catIcon[c.name]=c.icon||'•';});
  function _b(j){return ((j.target||'')+' '+(j.note||'')+' '+(j.ammo||'')).toLowerCase();}
  function normalizedCardName(j){return cardName(j).replace(/[（(][^）)]*[）)]/g,'').replace(/·/g,' ').trim().toLowerCase();}
  function _rx(p){try{return p?new RegExp(p,'i'):null;}catch(e){return null;}}
  var _CRX=CATS.map(function(c){return _rx(c.match);}), _TRX=TAGS.map(function(t){return _rx(t.match);});
  // 類別是單一軸(這份工作在做什麼):依序比職稱,都沒中再比內文;都沒中就是最後一個。
  function wt(j){var t=normalizedCardName(j),b=_b(j),i;
    for(i=0;i<CATS.length;i++)if(_CRX[i]&&_CRX[i].test(t))return CATS[i].name;
    for(i=0;i<CATS.length;i++)if(_CRX[i]&&_CRX[i].test(b))return CATS[i].name;
    return CATS[CATS.length-1].name;}
  // 顯示用的類別:使用者在卡上改過的(fb.cat)優先,其次 agent 找缺時判的(j.cat,要在設定的類別裡),最後才是關鍵字。
  function catOf(j){var f=FB[j.id]||{};
    if(f.cat&&catOrder.indexOf(f.cat)>=0)return f.cat;
    if(j.cat&&catOrder.indexOf(j.cat)>=0)return j.cat;
    return wt(j);}
  // 標籤跟類別正交,一張卡可以有好幾個。
  function tagsOf(j){var b=_b(j),t=[];
    for(var i=0;i<TAGS.length;i++)if(_TRX[i]&&_TRX[i].test(b))t.push(TAGS[i].name);
    return t;}
  var dirty={}, active='none', activeCat='all', activeFacet='', searchQuery='';
  var justMarked={};   // 這一輪剛標過的:先留在畫面上,切分頁或重整才收起來
  // 「我剛才在看哪裡」要跨重整記住:哪個分頁、哪一類、哪個標籤、搜尋什麼、捲到哪。
  // 這是內容位置,跟頂部那些控制面板不同(面板每次都關著)。
  var VIEW_KEY='sts_view', _restoreY=0;
  // 「自動展開公司」只在他這次自己動了篩選、而且結果夠少的時候才做。
  // 以前只要有條件生效就全展開,加上我又記住上次的篩選,於是一重整=條件還原=整片攤開。
  // 重整永遠從收起來開始,這是他要的預設。
  var userFiltered=false;
  function saveView(){
    try{localStorage.setItem(VIEW_KEY,JSON.stringify({a:active,c:activeCat,f:activeFacet,q:searchQuery,
      y:Math.round(window.pageYOffset||document.documentElement.scrollTop||0)}));}catch(e){}
  }
  try{
    var _v=JSON.parse(localStorage.getItem(VIEW_KEY)||'{}');
    if(_v.a)active=_v.a; if(_v.c)activeCat=_v.c; if(_v.f)activeFacet=_v.f; if(_v.q)searchQuery=_v.q;
    _restoreY=_v.y||0;
  }catch(e){}
  // 進板時間範圍:0=全部,其餘是「最近 N 天」。記在 localStorage,下次打開還在。
  var rangeDays=0; try{rangeDays=parseInt(localStorage.getItem('sts_range')||'0',10)||0;}catch(e){}
  var RANGES=[[1,'今天'],[3,'3 天內'],[7,'7 天內'],[30,'30 天內'],[-3,'躺超過兩週'],[0,'全部']];
  // ---- 開合:整個看板只有這一種 ----
  // 公司列、頂部面板(參考資料/答案庫/履歷/找新職缺/成效)、答案庫每一條、卡片裡的「詳細」,
  // 全部是同一個 <details class="fold">:同一個箭頭(左邊,打開轉朝下)、點整列開合、預設收起。
  // 開合只在這一次瀏覽期間記著(重畫不會收掉他開的那個),不寫進瀏覽器:記住的話第一屏
  // 會「有時候開有時候關」。規矩是:永遠預設關起來,想看的人自己點開。
  // 以前每一塊各寫一套(箭頭有左有右、有的用 ▾▴ 字、有的記進瀏覽器、有的每次重畫就收掉),
  // 他看得出來同樣是開合卻長得不一樣:「不應該有各種版本」。
  var FOLD={};   // 開著的 key → 1
  function fold(key,sum,body,o){o=o||{};
    return '<details class="fold'+(o.cls?' '+o.cls:'')+'" data-fold="'+escA(key)+'"'+(o.id?' id="'+o.id+'"':'')+
      (o.attr||'')+(FOLD[key]?' open':'')+'><summary class="fold-h'+(o.hcls?' '+o.hcls:'')+'"'+(o.hattr||'')+'>'+
      sum+'</summary>'+body+'</details>';}
  // toggle 不冒泡,所以在 document 上用捕捉階段收;#app 外面的履歷、找新職缺也收得到。
  document.addEventListener('toggle',function(e){
    var d=e.target; if(!d||d.tagName!=='DETAILS'||!d.classList.contains('fold'))return;
    var k=d.getAttribute('data-fold'); if(d.open)FOLD[k]=1; else delete FOLD[k];
    if(d.open){if(d.classList.contains('cogrp'))fillCo(d); autogrowAll(d);}
    else if(d.classList.contains('ansrow')&&ansPurgeBlank(d.getAttribute('data-k'))){renderAll(); refreshBar();}
  },true);
  // 上架多久:0=不限,其餘是「上架 N 天內」。查不到上架日的缺在有選範圍時會被排掉,
  // 所以另外給一顆「查不到」讓他單獨看那批,不要讓它們無聲消失。
  var openDays=0; try{openDays=parseInt(localStorage.getItem('sts_open')||'0',10)||0;}catch(e){}
  // 主流(Indeed/LinkedIn/Cake)只給正面的時間窗,沒有「超過 N 天」這種負向窗;
  // 「掛很久」是排序的事(見 SORTS 的「開最久」),不是篩選的事。查不到那顆留著:
  // 那是我們資料自己產的必然產物,主流的資料來自雇主 API 不會有,不能讓它們無聲消失。
  var OPENS=[[7,'7 天內'],[30,'30 天內'],[90,'90 天內'],[-2,'查不到'],[0,'不限']];
  // 排序:以前只有篩選沒有排序,結果新缺被埋在幾百張裡。預設「剛進板的先看」。
  var sortBy='added'; try{sortBy=localStorage.getItem('sts_sort')||'added';}catch(e){}
  var SORTS=[['added','剛進板'],['fit','最可能喜歡'],['posted','剛上架'],['oldest','開最久'],['co','公司']];
  // 「最可能喜歡」:找缺那一輪判斷過的(對照他的原話給 1-5,j.src.fit)排前面;
  // 其餘照 j.like(職稱加摘要像他喜歡過的程度 − 像不喜歡過的程度,0-100,prefs.like_scores;
  // 拿他表過態的卡驗過 AUC 0.82)。
  function fitOf(j){return (j.src&&+j.src.fit)?100+(+j.src.fit):(+j.like||0);}
  // 找缺那一輪判斷的結果:對味幾分、怎麼找到的、根據他哪幾句(判斷時引用的是他的原話,理由照抄)。
  function srcLineHTML(j){var s=j.src; if(!s||!s.fit)return '';
    return '<div class="srcline">🎯 對味 '+s.fit+'/5 · '+esc((FIND_MODE[s.mode]||s.mode)+(s.direction?'「'+s.direction+'」':''))+
      (s.why?'<span class="srcwhy">'+esc(s.why)+'</span>':'')+'</div>';}
  // 判斷那一段看出來的幽靈職缺(掛著但看起來沒在真的招人):照樣進看板,標出來讓他自己決定。詐騙的不會進看板。
  function riskHTML(j){
    var r=j.src&&j.src.risk; if(!r||r.kind!=='ghost')return '';
    return '<div class="repost-note"><strong>👻 可能是幽靈職缺</strong><div>'+esc(r.why||'')+'</div></div>';
  }
  function repostHTML(j){
    var hits=(j.src&&j.src.reposts)||[];
    if(!hits.length)return '';
    return '<div class="repost-note"><strong>↻ 可能是重貼</strong>'+hits.map(function(x){
      var title=x.title||'舊卡', link=x.url?'<a href="'+escA(safeUrl(x.url))+'" target="_blank" rel="noopener">'+esc(title)+'</a>':esc(title);
      return '<div>'+link+' · 當時表態：'+esc(x.mark||'未表態')+
        (x.note?' · 原話：「'+esc(x.note)+'」':'')+'</div>';
    }).join('')+'</div>';
  }
  function sortJobs(l){
    var f={
      added:function(a,b){return (daysSince(a.added)==null?9e9:daysSince(a.added))-(daysSince(b.added)==null?9e9:daysSince(b.added));},
      posted:function(a,b){return (daysSince(a.posted_at)==null?9e9:daysSince(a.posted_at))-(daysSince(b.posted_at)==null?9e9:daysSince(b.posted_at));},
      oldest:function(a,b){var x=daysSince(a.posted_at),y=daysSince(b.posted_at);
        return (y==null?-1:y)-(x==null?-1:x);},   // 掛最久的先看:常設缺/沒人管的先攤出來
      co:function(a,b){return companyOf(a).localeCompare(companyOf(b));},
      fit:function(a,b){return fitOf(b)-fitOf(a);}
    }[sortBy];
    return f?l.slice().sort(f):l;
  }
  var filtersOpen=false;
  function _matchQ(j,q){var s=j.sum||{}; var hay=((j.target||'')+' '+(j.note||'')+' '+(s.fit||'')+' '+(s.co||'')+' '+(s.bar||'')+' '+(s.ammo||'')).toLowerCase(); return hay.indexOf(q)>=0;}
  function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function escA(s){return esc(s).replace(/"/g,'&quot;');}   // 放進 attr="…" 用
  // 連結只收 http(s):職缺名稱、回音的連結都是 agent 從網頁讀來的,javascript: 之類的一點就在看板裡跑
  function safeUrl(u){u=String(u||'').trim(); return /^https?:\/\//i.test(u)?u:'#';}
  function inl(s){
    // 先整段跳脫再套 markdown:職缺名稱、彈藥、備註是 agent 從職缺頁讀來的,網頁裡夾的 <img onerror> 會在看板上執行
    s=escA(s);
    // 標題後面那串「（[現行頁](url)）」拿掉:整個標題本身就是連結(見 headHTML),
    // 可點範圍是一整行,比一個小圖示好按。這裡只負責把那串標籤去掉。
    s=s.replace(/（\[[^\]]*\]\((https?:\/\/[^)]+)\)）/g,'');
    s=s.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,function(m,t,u){return '<a href="'+u+'" target="_blank" rel="noopener">'+t+'</a>';});
    s=s.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>'); return s;
  }
  function pill(ch){var c='ch-direct'; if(ch.indexOf('暖網')>=0)c='ch-warm'; else if(ch.indexOf('owner')>=0)c='ch-owner'; return c;}
  function sentOf(id){return (FB[id]&&FB[id].s)||'';}
  function removed(id){return !!(FB[id]&&FB[id].rm);}
  // 封鎖名單存在 FB.__block__(跟他的標記同一份檔,手機電腦同步、agent 也讀得到)
  if(!FB['__block__'])FB['__block__']=[];
  // 這張的公司在不在封鎖名單上、落在哪一條:後台算(下一步的 blocked;大小寫、特殊字元不同也算同一家)
  function isBlocked(j){return !!nextOf(j.id).blocked;}
  // 這家(公司列上的名字,或封鎖名單上的那一條)的卡:後台給的公司名一樣,或落在名單的這一條
  function coJobs(co){return jobs.filter(function(j){return companyOf(j)===co||nextOf(j.id).blocked===co;});}
  function markBlockDirty(){dirty['__block__']=true;renderAll();scheduleSave(900);}
  // 「這家全部進準備區」會動到哪幾張:還沒進管線、也沒被移除的才算。
  // 已經在準備區/待你決定/可投遞/已投遞的一律不動——把投出去的拉回準備區是破壞,不是方便。
  function coPrepList(cc){
    return cc.filter(function(j){var f=FB[j.id]||{}; return !f.app&&!removed(j.id);});
  }
  function coPrepN(cc){return coPrepList(cc).length;}
  // 只搬這一列畫面上數到的那幾張(LAZY 存的就是這一列、這一頁、篩過的卡)。以前按下去重掃這家「全部」:
  // 👎、😐、出錯了、被篩掉的也一起搬進去,按鈕上寫 1 張、實際搬了 5 張。
  function coPrepAll(key){
    var cc=LAZY[key]||[], co=cc.length?companyOf(cc[0]):'';
    var todo=coPrepList(cc);
    if(!todo.length){snack('「'+co+'」這裡沒有可以送的缺(都已經在流程裡了)'); return;}
    var ids=todo.map(function(j){return j.id;});
    ids.forEach(function(id){FB[id]=FB[id]||{}; FB[id].app='prep'; delete justMarked[id]; touch(id);});
    renderAll(); refreshBar(); scheduleSave(900);
    snack('已把「'+co+'」的 '+ids.length+' 個缺送去「準備履歷中」',function(){
      ids.forEach(function(id){if(FB[id])delete FB[id].app; touch(id);});
      renderAll(); refreshBar(); scheduleSave(900);});
  }
  // 公司選單「這家全部移除」:只動這一列畫面上的那幾張(跟「全部送去準備履歷中」同一個範圍),可以復原
  // 已投出的單張本來就不給移除(投都投了,留著當紀錄),整家移除也不收它們
  function coRmList(cc){return cc.filter(function(j){return !removed(j.id)&&(FB[j.id]||{}).app!=='sent';});}
  // 一次移除好幾張:agent 正在做、送出結果不明的那幾張不動(狀態表修正 11、14);其他的確認送出一起作廢。
  // 後台收了才算,回 Promise [{id, undo, back}](收了的那幾張,復原用)
  function rmMany(ids,val){
    return Promise.all(ids.filter(function(id){return !dsBusyWhy(id);}).map(function(id){var f=FB[id]||{}, back={rm:f.rm===undefined?null:f.rm};
      delete justMarked[id];
      return act(id,f.app==='ship'?{ev:'leave'}:null,{rm:val}).then(function(r){return r.ok?{id:id,undo:r.undo,back:back}:null;});}))
      .then(function(rs){return rs.filter(Boolean);});}
  function rmManyUndo(done){done.forEach(function(d){act(d.id,d.undo?{undo:d.undo}:null,d.back);});}
  function coRmAll(key){
    var cc=LAZY[key]||[], co=cc.length?companyOf(cc[0]):'';
    rmMany(coRmList(cc).map(function(j){return j.id;}),1).then(function(done){
      if(!done.length)return;
      snack('已把「'+co+'」的 '+done.length+' 個缺移到「🗑 已移除」',function(){rmManyUndo(done);});});
  }
  // 封鎖、解除封鎖一家。解除時拿掉名單上落到這家的每一條(後台說這家的卡落在哪一條)
  function toggleBlock(co){
    if(!Array.isArray(FB['__block__']))FB['__block__']=[];
    var cc=coJobs(co), hits=[co].concat(cc.map(function(j){return nextOf(j.id).blocked;}).filter(Boolean));
    var l=FB['__block__'], before=l.slice(), blocking=!l.some(function(x){return hits.indexOf(x)>=0;});
    // 這家有卡 agent 正在做、或送出結果不明:先擋下(狀態表修正 11、14)
    var busy=blocking&&cc.filter(function(j){return dsBusyWhy(j.id);})[0];
    if(busy){snack('「'+cardName(busy)+'」'+dsBusyWhy(busy.id)); return;}
    FB['__block__']=blocking?l.concat([co]):l.filter(function(x){return hits.indexOf(x)<0;}); markBlockDirty();
    // 封鎖了就不會再投這家:流程裡還沒投出的卡一起退出(確認送出一起作廢),不然看不見的卡還在背景被準備、被填表。
    var outs=blocking?cc.filter(function(j){var f=FB[j.id]; return f&&['prep','ready','ship'].indexOf(f.app)>=0;}):[];
    Promise.all(outs.map(function(j){var app=FB[j.id].app;
      return act(j.id,app==='ship'?{ev:'leave'}:null,{app:null}).then(function(r){return r.ok?{id:j.id,undo:r.undo,back:{app:app}}:null;});}))
    .then(function(rs){var out=rs.filter(Boolean);
      snack(blocking?('已封鎖「'+co+'」,找缺也不會再挖'+(out.length?';流程裡的 '+out.length+' 張一起退出':'')):('已解除封鎖「'+co+'」'),
        function(){FB['__block__']=before; markBlockDirty(); rmManyUndo(out);});});
  }
  var NEW_DAYS=7;
  function daysSince(d){
    if(!d)return null;
    var t=Date.parse(d+'T00:00:00'); if(isNaN(t))return null;
    return Math.floor((Date.now()-t)/86400000);
  }
  function isNew(j){var n=daysSince(j.added); return n!==null&&n<=NEW_DAYS;}
  function inOpen(j){
    if(!openDays)return true;
    var n=daysSince(j.posted_at);
    if(openDays===-2)return n===null;      // 只看查不到上架日的
    if(n===null)return false;
    return n<=openDays;
  }
  function inRange(j){
    if(!rangeDays)return true;
    var n=daysSince(j.added);
    if(rangeDays===-3)return n!==null&&n>14&&!sentOf(j.id);   // 進板兩週還沒表態的
    return n!==null&&n<rangeDays;   // 今天=0 天前,所以「今天」用 <1
  }
  // 這個缺在站台上開多久了(上架日由 posted_age.py 從站台官方端點抓,不是模型猜的)。
  // 開太久常常是長期掛著的常設缺,值得他一眼看到。
  function openedBadge(j){
    var n=daysSince(j.posted_at);
    if(n===null)return '';
    var cls=n>180?'opened old':'opened';
    return '<span class="'+cls+'" title="'+esc(j.posted_at)+' 上架（'+esc(j.posted_src||'')+'）">開了 '+n+' 天</span>';
  }
  // 104 顯示「企業最後處理履歷時間」、Indeed 自動標 Response seems unlikely。我們沒有雇主端資料,
  // 但手上三個訊號合起來能講同一件事:掛超過半年、死線已過、連結檢查沒過。
  // 死線那一天整天都還來得及:後台認出是哪一天(card.deadline → j.dl),這裡比當地的今天。以前用 Date.parse('2026-09-27')
  // 是 UTC 零點,台灣早上 8 點就顯示「死線已過」
  function deadlinePassed(j){return !!j.dl&&today()>j.dl;}
  function riskBadge(j){
    var r=[],n=daysSince(j.posted_at);
    if(n!==null&&n>180)r.push('掛超過半年');
    if(deadlinePassed(j))r.push('死線已過');
    // 連結失效在標題旁已經有一顆「原頁失效(本卡內容已留存)」,這裡不再重複講

    if(!r.length)return '';
    return '<span class="risk" title="'+esc(r.join('、'))+':雇主可能早就沒在看這個缺">⚠ '+esc(r[0])+(r.length>1?' +'+(r.length-1):'')+'</span>';
  }
  function addedBadge(j){
    var n=daysSince(j.added);
    if(n===null)return '<span class="added" title="不知道什麼時候進板的">進板日期不明</span>';
    var txt=n<=0?'今天進板':(n===1?'昨天進板':n+' 天前進板');
    var cls=n<=NEW_DAYS?' fresh':((n>14&&!sentOf(j.id))?' stale':'');
    return '<span class="added'+cls+'" title="'+esc(j.added)+' 進板'+(cls===' stale'?'(放兩週還沒表態)':'')+'">'+txt+'</span>';
  }
  // 公司列的開合跟其他開合一樣記在 FOLD(key = 'co:'+coKey),重整就全部收起來。
  function coKey(cat,co){return cat+'|'+co;}
  $('hdr').innerHTML=$('t-hdr').innerHTML;
  function renderFacetInd(){if(typeof applyFilterUI==='function')applyFilterUI(); var el=$('facetf'); if(!el)return; el.innerHTML=activeFacet?('<span class="ff">篩選:'+esc(activeFacet)+' <b>✕</b></span>'):'';}
  // 頁首這兩顆是一排並列的開關(底下的框是整排寬),不能做成 <details>;箭頭和開合規矩跟 fold 同一套:
  // aria-expanded 帶動同一個 .chev 箭頭,預設關,只在這次瀏覽期間記著。
  function applyFilterUI(){var fb=$('filterbox'); if(fb)fb.style.display=filtersOpen?'flex':'none';
    var on=(active==='iv')?ivQuery:(activeFacet||searchQuery);
    var b=$('ftog'); if(b){b.classList.toggle('on',filtersOpen||!!on); b.setAttribute('aria-expanded',filtersOpen?'true':'false');}
    var st=$('ftogst'); if(st){st.textContent=(on&&!filtersOpen?'●':'');}}
  if($('ftog')){$('ftog').addEventListener('click',function(){filtersOpen=!filtersOpen; applyFilterUI(); renderApp(); if(filtersOpen&&$('q'))$('q').focus();});}
  applyFilterUI();
  if($('q')){$('q').addEventListener('input',function(e){var v=(e.target.value||'').trim().toLowerCase();
    if(active==='iv'){ivQuery=v;renderApp();applyFilterUI();return;}   // 面試準備那頁:只搜題庫
    searchQuery=v;userFiltered=true;renderApp();saveView();});}
  if($('facetf')){$('facetf').addEventListener('click',function(){activeFacet='';renderFacetInd();renderApp();});}
  if(FB['__scratch__']&&!FB['__notes__']){FB['__notes__']=[{id:'n0',t:FB['__scratch__']}];delete FB['__scratch__'];}
  if(!Array.isArray(FB['__notes__']))FB['__notes__']=[];
  // textarea 跟著內容長高:以前固定兩行,他寫的想法會被切掉一半看不到。
  // 收合中的公司量不到高度(scrollHeight=0),量到 0 就別寫,不然展開後會是一條縫。
  function autogrow(ta){if(!ta||ta.offsetParent===null)return;ta.style.height='auto';
    var h=ta.scrollHeight; if(!h)return; ta.style.height=Math.max(h+2,38)+'px';}
  function autogrowAll(root){(root||document).querySelectorAll('.note-t,.fb-t,.rz-fb,.ans-ta').forEach(autogrow);}
  var notesOpen=false;
  function noteCount(){return (FB['__notes__']||[]).filter(function(x){return (x.t||'').trim();}).length;}
  function applyNotesUI(){var w=$('noteswrap'); if(w)w.style.display=notesOpen?'':'none';
    var b=$('ntog'); if(b){b.classList.toggle('on',notesOpen); b.setAttribute('aria-expanded',notesOpen?'true':'false');}
    var st=$('ntogst'); if(st){var n=noteCount(); st.textContent=((n&&!notesOpen)?String(n):'');}
    if(notesOpen)autogrowAll($('noteswrap'));}
  function renderNotes(){var box=$('notes'); if(!box)return; box.innerHTML=FB['__notes__'].map(function(n){return '<div class="note-card" data-nid="'+n.id+'"><textarea class="note-t" rows="2" placeholder="記個想法…">'+esc(n.t||'')+'</textarea><div class="note-actions"><button class="note-save" type="button">儲存</button><button class="note-del" type="button">刪除</button></div></div>';}).join(''); autogrowAll(box); applyNotesUI();}
  // 「我的想法」是打字:跟卡片上的心得一樣,停 2 秒或離開輸入框才存,也不在每個鍵重寫整份標記
  function markNotesDirty(){dirty['__notes__']=true; if(typeof refreshBar==='function')refreshBar(); scheduleSave(2000);}
  if($('ntog')){$('ntog').addEventListener('click',function(){notesOpen=!notesOpen; applyNotesUI(); if(notesOpen){var t=$('noteswrap').querySelector('.note-t'); if(t)t.focus();}});}
  var _nb=$('notes');
  if(_nb){ _nb.addEventListener('input',function(e){var ta=e.target.closest('.note-t'); if(!ta)return; autogrow(ta); var id=ta.closest('.note-card').getAttribute('data-nid'); var n=FB['__notes__'].filter(function(x){return x.id===id;})[0]; if(n){n.t=ta.value; markNotesDirty();}});
    _nb.addEventListener('click',function(e){ if(e.target.closest('.note-save')){doSave();return;} var d=e.target.closest('.note-del'); if(d){var id=d.closest('.note-card').getAttribute('data-nid'); FB['__notes__']=FB['__notes__'].filter(function(x){return x.id!==id;}); renderNotes(); markNotesDirty();}}); }
  var _na=$('note-add'); if(_na)_na.addEventListener('click',function(){FB['__notes__'].push({id:'n'+Date.now(),t:''}); renderNotes(); markNotesDirty(); var ts=$('notes').querySelectorAll('.note-t'); if(ts.length)ts[ts.length-1].focus();});
  renderNotes();

  // ---- 履歷 × 語言:設定(「⚙ 設定」頁)定義可用履歷,agent 先判,使用者最終決定 ----
  var inApplyView=false;
  function resumeName(resumeId){for(var i=0;i<RESUMES.length;i++)if(RESUMES[i].id===resumeId)return RESUMES[i].name||resumeId;return resumeId||'?';}
  // 語言名稱用那個語言自己的寫法(Intl.DisplayNames);常見的三種照原本的字。
  function langLabel(l){var t={zh:'中文',en:'English',ja:'日本語'}[l]; if(t)return t;
    try{return new Intl.DisplayNames([l],{type:'language'}).of(l)||l;}catch(e){return l;}}
  function shipOf(j){return (j&&SHIP[j.id])||null;}
  // 這張用哪一份履歷、哪個語言:照後台算的(規則在 tools/ship.py 的 pick,看板沒有自己的一套)。
  // 選不出來(你指定的被取消勾選、還沒挑)回 null,原因在 shipOf(j).problem
  function pickOf(j){var r=shipOf(j); return r&&r.resume_id?{variant:r.resume_id,lang:r.lang}:null;}
  // 客製紀錄每份檔、每個語言各一筆(key <種類>:<id>:<語言>,跟 tools/ship.py 的 item_key 同一種)
  function custKey(kind,id,lang){return kind+':'+id+':'+lang;}
  // 已收下的客製版,原始檔後來換過了(後台比對簽章):不會寄出去,寄的是新的原始檔
  function custStale(j,key){var r=shipOf(j); return !!(r&&(r.stale_ids||[]).indexOf(key)>=0);}
  // 這張卡這份檔現在預覽、寄的是哪一份(客製版或原始檔):後台說的
  function cardFile(j,kind,id,lang){
    var r=shipOf(j), key=custKey(kind,id,lang), x=null;
    ((r&&r.files)||[]).forEach(function(f){if(f.id===key)x=f;});
    if(!x)return null;
    if(x.custom)return {kind:'custom',url:'/api/file?path='+encodeURIComponent(x.path),path:x.path};
    var url=x.preview?'/api/source-preview?kind='+encodeURIComponent(kind)+'&id='+encodeURIComponent(id)+'&lang='+encodeURIComponent(lang):'';
    return {kind:'source',url:url,preview:!!x.preview};
  }
  function cardAttachments(j){
    var r=shipOf(j);
    return ((r&&r.files)||[]).filter(function(f){return f.kind==='attachment';}).map(function(f){
      var doc=cardFile(j,'attachment',f.item,r.lang), item={name:f.name||f.item,short:f.short||'',slug:f.item};
      if(doc&&doc.url)item.preview=doc.url;
      return item;
    });
  }
  // 按了版本、語言(還沒存)或收下客製版之後,當下問後台這一張;回來之前又按了別的,舊的那個回覆丟掉
  var SHIP_ASK={};
  function askShip(id){
    var f=FB[id]||{}, n=SHIP_ASK[id]=(SHIP_ASK[id]||0)+1;
    return fetch('/api/ship-files?u='+encodeURIComponent(id)+'&resume_id='+encodeURIComponent(f.resume_id||'')+
                 '&lang='+encodeURIComponent(f.lang||''))
      .then(function(r){if(!r.ok)throw new Error('http '+r.status); return r.json();})
      .then(function(d){if(SHIP_ASK[id]!==n)return; SHIP[id]=d; renderAll(id);})
      .catch(function(){if(SHIP_ASK[id]===n)snack('要寄的檔案沒更新到,等一下會自己再對一次');});
  }
  // 設定改了、別的裝置改了標記、背景重建完:整批重拿。他手上還沒存的那幾張照他按的再問一次
  function takeShip(all){if(!all)return; SHIP=all; Object.keys(dirty).forEach(function(k){if(SHIP[k])askShip(k);});}
  function refreshShip(){
    return fetch('/api/ship-files').then(function(r){if(!r.ok)throw new Error('http '+r.status); return r.json();})
      .then(function(d){takeShip(d); renderAll(); return true;}).catch(function(){return false;});
  }
  // 設定裡沒寫短名稱時,附件用的是完整檔名(「王小明_某某分析報告.pdf」),一個 pill 在卡上佔三行。
  // 畫面上自己收:去掉副檔名、去掉開頭「人名_」那一段;完整名稱放在 title。
  function attShort(x){if(x&&x.short)return x.short;
    var n=String((x&&(x.name||x.slug))||x||'').replace(/\.(pdf|docx?|png|jpe?g)$/i,''), i=n.indexOf('_');
    return (i>0&&i<24&&n.length-i>6)?n.slice(i+1):n;}
  function attFull(x){return (x&&x.name)||(x&&x.slug)||x;}
  // 不標號碼:排的順序就是對方收到檔案的順序。
  function attPills(atts){return (atts||[]).map(function(x){
    var title=esc(attFull(x)),label=esc(attShort(x)),preview=x&&typeof x==='object'&&x.preview;
    return preview?'<a class="att-pill att-preview" data-srcpv="'+escA(preview)+'" href="'+escA(preview)+'" title="預覽 '+title+'">'+label+'</a>':
      '<span class="att-pill" title="'+title+'">'+label+'</span>';}).join(' ');}
  // 兩個決定(版本、語言)agent 先判,使用者最終決定。長相要一樣、要相鄰,而且中間不要塞
  // 任何說明:agent 判了什麼、憑什麼判,全部收進下面那條摺疊行。
  function verdictBlockHTML(j){
    var rz=j.resume;
    var contentHint=rz&&rz.content_problem?'<div class="stage-blocked">⚠ agent 判斷這是履歷內容問題;要改內容請開客製</div>':'';
    if(!inApplyView){
      if(!rz||!rz.recommend)return contentHint;
      var ch=!!((FB[j.id]||{}).variant||(FB[j.id]||{}).lang);
      return '<div class="vdmini y">📄 '+esc(AGENT)+' 判：'+esc(resumeName(rz.recommend))+
             ' · '+langLabel(rz.lang||LANGS[0])+(ch?'<b>· 你改過</b>':'')+'</div>'+
             (rz.pick_why?'<div class="vdmini-why">挑選理由：'+esc(rz.pick_why)+'</div>':'')+contentHint;
    }
    var r=shipOf(j); if(!r)return '<div class="vd vd-none">這張卡的要寄的檔案還沒算好,等一下會自己出來</div>'+contentHint;
    var p=pickOf(j);
    // 履歷、語言兩個切換排在同一列(窄螢幕放不下才換行):以前各佔一列,再加一列 Agent 判的,手機上光這塊就 300px。
    // 能選哪幾份、哪幾個語言照後台現在的設定(設定頁改了不用重新整理);選不出履歷時兩排都不亮
    var h='<div class="vd'+(rz&&rz.recommend?' y':'')+'">'+
      '<div class="vd-row vd-picks"><span class="vd-pick"><span class="vd-lb">履歷</span><span class="vd-seg">'+(r.choices||[]).map(function(v){
        return '<button class="vd-b'+(p&&p.variant===v.id?' on':'')+'" data-vd="'+esc(j.id)+'" data-vv="'+esc(v.id)+'" type="button">'+esc(v.name||v.id)+'</button>';}).join('')+
      '</span></span><span class="vd-pick"><span class="vd-lb">語言</span><span class="vd-seg">'+(r.langs||LANGS).map(function(l){
        return '<button class="vd-b lg'+(p&&p.lang===l?' on':'')+'" data-lg="'+esc(j.id)+'" data-lv="'+esc(l)+'" type="button">'+langLabel(l)+'</button>';}).join('')+
      '</span></span></div>';
    if(r.lang_from)h+='<div class="vd-problem">⚠ 「'+esc(langLabel(r.lang_from))+'」不在你的語言清單上,改用 '+esc(langLabel(r.lang))+'</div>';
    if(!p)return h+'<div class="vd-problem">⚠ '+esc(r.problem||'還沒挑履歷')+'</div>'+contentHint+'</div>';
    var vkey=p.lang+'-'+p.variant;
    var pv=(rz&&rz.variants&&rz.variants[vkey])||{}, file=cardFile(j,'resume',p.variant,p.lang);
    var hasBuilt=!file&&!!(pv.html||pv.html_lazy||(pv.pages&&pv.pages.length)||pv.pages_n||
                           (rz&&(rz.html||rz.html_lazy||(rz.pages&&rz.pages.length)||rz.pages_n)));
    var hasPreview=!!(file&&(file.kind==='custom'||file.preview))||hasBuilt;
    // 履歷來源預覽由來源追蹤更新;卡片保留 agent 對選擇的說明。
    if(rz&&rz.variants){
      if(pv.motive&&pv.motive.txt){
        h+='<div class="vd-mot">'+(pv.motive.lbl?'<span class="vm-k">'+esc(pv.motive.lbl)+'</span>':'')+
           '<span class="vm-t">'+esc(pv.motive.txt)+'</span></div>';
      }
    }
    h+='<div class="vd-ship">'+
       (hasPreview?'<button class="rz-open ship-rz" type="button" data-rz="'+esc(j.id)+'" data-var="'+esc(vkey)+'" data-rzkind="'+esc(file?file.kind:'built')+'">📄 '+langLabel(p.lang)+'·'+esc(r.resume_name||resumeName(p.variant))+'</button>':
         '<span class="ship-rz-missing">'+(file?'這份檔案沒有 PDF 預覽':'這份履歷尚未上傳')+'</span>')+
       '<span class="ship-att">📎 '+attPills(cardAttachments(j))+'</span></div>';
    if(rz&&rz.recommend){
      // agent 原本判什麼,要看得到、不用點開:寫在「憑什麼」那一行的標題上,不另佔一列。
      var aLang=rz.lang||LANGS[0], vSame=(rz.recommend===p.variant), lgSame=(aLang===p.lang);
      h+=fold('vwhy:'+j.id,'<span class="vd-agent">🤖 '+esc(AGENT)+' 判 '+
         '<b class="'+(vSame?'':'ch')+'">'+esc(resumeName(rz.recommend))+'</b>'+
         '<b class="'+(lgSame?'':'ch')+'">'+langLabel(aLang)+'</b>'+
         ((!vSame||!lgSame)?'<i class="ch">你改過</i>':'')+'<span class="vd-why">憑什麼</span></span>',
         '<div class="vd-f"><span class="vd-k">版本</span><span>'+esc(rz.pick_why||'(沒寫)')+'</span></div>'+
         '<div class="vd-f"><span class="vd-k">語言</span><span>JD 原文是'+langLabel(aLang)+',所以用'+langLabel(aLang)+'的版本。</span></div>',
         {cls:'vwhy'});
    }else h+='<div class="vd-row"><span class="vd-agent">🤖 '+esc(AGENT)+' 還沒判(還沒準備履歷)</span></div>';
    return h+contentHint+'</div>';
  }
  // 收起來那一行:寄哪份履歷、什麼語言、幾份附件;跟 agent 判的不一樣就標出來。
  function vdSumHTML(j){
    var p=pickOf(j), r=shipOf(j)||{}, rz=j.resume||{};
    if(!p)return '<span class="vdsum">📄 '+esc(r.problem||'還沒挑履歷')+'</span>';
    var n=cardAttachments(j).length, ch=rz.recommend&&(rz.recommend!==p.variant||(rz.lang||LANGS[0])!==p.lang);
    return '<span class="vdsum">📄 '+esc(langLabel(p.lang))+' · '+esc(r.resume_name||resumeName(p.variant))+
      (n?'<span class="vdsum-n">＋附件 '+n+' 份</span>':'')+(ch?'<b class="vdsum-ch">你改過</b>':'')+'</span>';
  }
  // ---- 📋 申請表單與 🗂 答案庫 ----
  // 答案只有一個真相:答案庫(FB.__ans__)。表單上的欄位只是指標 {q, src:'bank', k},
  // 值、依據、中文對照、題型都在答案庫那一條。履歷直接對上的(src:'rz')和刻意不填的(src:'skip')
  // 留在表單上,它們的真相是履歷。以前表單自己也帶一份值、卡上攤一長串,答案庫又一份,
  // 兩個地方兩份真相;他的話:「全部以一個地方為主就好,也就是以表單答案庫為主」。
  // 所以卡上的表單只剩一行,要看要改答案一律去答案庫。記錄只走 tools/form_record.py。
  if(!FB['__ans__'])FB['__ans__']=[];
  function ansList(){return FB['__ans__']||[];}
  function ansOf(k){var a=ansList();for(var i=0;i<a.length;i++)if(a[i].k===k)return a[i];return null;}
  function ansIndex(k){var a=ansList();for(var i=0;i<a.length;i++)if(a[i].k===k)return i;return -1;}
  function today(){var d=new Date();
    return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
  function mdOf(d){var m=/^\d{4}-(\d\d)-(\d\d)/.exec(d||''); return m?(+m[1])+'/'+(+m[2]):(d||'');}
  function formOf(j){return (FB[j.id]&&FB[j.id].form)||null;}
  // 卡名、公司名、來源平台是後台算好跟著職缺送來的(tools/card.py label_jobs),看板不自己算
  function cardName(j){return typeof j==='string'?j:String((j&&(j.name||j.target))||'');}
  function clone(v){return v===undefined?undefined:JSON.parse(JSON.stringify(v));}
  // ---- 投遞狀態(docs/adr/0004、0005、GLOSSARY「投遞狀態」):只在後台套用。看板的按鈕送事件,後台照狀態表和這張卡的
  // 下一步收或擋,存好的那一張和新的下一步回來,看板才照著畫(#343)。按下去到後台回話之間,這張卡維持原樣、按鈕停用(PEND),
  // 不會連按兩次、也不會先畫一個後台不收的樣子再跳回來
  var EVENTS=[], INFLIGHT=[];   // 答案改了({refill: 鍵})、清掉答案({redo: 鍵}):跟下一次存檔一起送
  var PEND={};   // 按了、等後台回話的卡 → [{e, set, done, sent}]
  var REDO_DONE={};   // 清掉答案送出去、等後台回話的那幾條:拿回新的常用答案才講是清掉還是刪掉(鍵 → 要做的)
  // 按一下要後台收的:e = 事件 {ev, data} 或復原 {undo, else}(沒有就只改欄位);set = 跟著那一下改的欄位(null = 拿掉)。
  // 回 Promise {ok, why(不收的原因), undo(要復原就送回去的)}
  function act(id,e,set){
    return new Promise(function(done){(PEND[id]=PEND[id]||[]).push({e:e||null,set:set||{},done:done}); renderAll(id); scheduleSave(0);});}
  // 按一下:回話後講 msg、給復原(送後台給的復原,跟著改的欄位改回按之前;這張已經被別處改過就改送 o.fallback)。
  // o.then:收了之後要做的;o.undone:復原收了之後要做的;o.next:snack 上另一顆
  function press(id,e,set,msg,o){o=o||{}; set=set||{};
    var before=clone(FB[id]||{});
    // 復原:整張放回按之前(事件順手收走的欄位也放回,例如退回時收掉的回音);投遞那一部分照後台給的復原
    function back(){var o={}; Object.keys(Object.assign({},FB[id]||{},before)).forEach(function(k){o[k]=before[k]===undefined?null:before[k];}); return o;}
    return act(id,e,set).then(function(r){
      if(!r.ok){snack(r.why); return r;}
      if(o.then)o.then(r);
      if(msg)snack(msg,function(){act(id,r.undo?{undo:r.undo,'else':o.fallback}:null,back()).then(function(u){if(!u.ok)snack(u.why); else if(o.undone)o.undone();});},o.next);
      return r;});}
  // 這張現在不能離開流程、不能換檔的原因(狀態表上的 busy):agent 正在做,或送出結果不明、要他先查。照下一步,後台擋的是同一句
  function dsBusyWhy(id){return nextOf(id).busy||null;}
  // 把卡片 HTML 裡離開流程、換檔(履歷、語言、客製或上傳自己的檔、改回原始檔)的按鈕停用並寫原因
  // (按鈕的處理那一邊也會再擋一次;換檔在正在送出時後台照狀態表不收,#338)。按了還在等後台回話的卡,每一顆都先停用
  function lockLeaving(id,h){
    if(PEND[id])return h.replace(/<button(?![^>]*\sdisabled)/g,'<button disabled title="等後台回話"');
    var why=dsBusyWhy(id);
    if(!why)return h;
    return h.replace(/<button([^>]*?)(data-back="[^"]*"|data-rm="1"|data-err="1"|data-s="(?:dislike|meh)"|data-adv="sent"|data-vd="[^"]*"|data-lg="[^"]*"|data-cust-open="[^"]*"|data-cust-action="(?:accept|clear)"(?! data-cust-orphan))/g,
      '<button disabled title="'+escA(why)+'"$1$2');}
  // 常用答案整份的下一步(後台 next_step.answers):哪幾條等你、叫你確認、先不給改、哪幾張表單在用
  function ansNext(){return NEXT.__ans__||{};}
  // 送出結果不明的卡在用的答案:先不給改(狀態表修正 14),等他查過到底送出沒有
  function ansLocked(k){return (ansNext().locked||[]).indexOf(k)>=0;}
  // 職缺標題＋開職缺原頁的連結。卡片標題和答案庫「這缺專用」的分組標題共用這一支。
  function jdTitleHTML(j){var t=inl(cardName(j));
    return /^https?:/.test(j.id)?'<a class="jd-link" href="'+escA(j.id)+'" target="_blank" rel="noopener" title="開職缺原頁">'+t+'<span class="jd-go">↗</span></a>':t;}
  // 哪幾張表單在用這條:[{j, lock}](後台照整份看板算的,這裡只列看板上畫得出來的卡)
  function ansUsers(k){var u=[]; ((ansNext().users||{})[k]||[]).forEach(function(x){var j=jobOf(x[0]); if(j)u.push({j:j,lock:!!x[1]});}); return u;}
  // 「Acme ×5 · Northwind 🔒」:同一家合起來,全都已投遞的標 🔒。
  function ansUseLabel(k){var g={},o=[];
    ansUsers(k).forEach(function(u){var c=companyOf(u.j); if(!g[c]){g[c]={n:0,l:0};o.push(c);} g[c].n++; if(u.lock)g[c].l++;});
    return o.map(function(c){return c+(g[c].n>1?' ×'+g[c].n:'')+(g[c].l===g[c].n?' 🔒':'');}).join(' · ');}
  // 有 inf(我推論的那天)= 等他確認;他按 ✓ 或動手改,inf 換成 at。
  function ansPending(e){return !!(e&&e.inf);}
  function ansConfirm(e){delete e.inf;e.at=today();dirty['__ans__']=true;}
  // 還要他動手的答案:我推論的等他確認,或答案還空著等他寫(空的不給 ✓,不然一按就變成「都確認過了」)。
  function ansEmpty(e){return !String((e&&e.v)||'').trim()&&!String((e&&e.zh)||'').trim();}
  // 英文表單的答案:他看、他改的是中文(zh),英文(v)是送出用的。他改了中文,那條標 tr,
  // 我送出前照中文重翻英文(form_record.translate),雇主網頁也跟著重打。英文答案一定要附中文。
  // 他看得懂的語言(設定 resume.read_lang):送出的答案跟它不是同一套文字,才附一份他看得懂的(zh 欄位)。
  // 跟 form_record.needs_translation 同一條。中文使用者:有英文字母、沒有中文字的答案。
  var READ=String(CFG.read_lang||'zh'), READ_CJK=/^(zh|ja|ko)(-|$)/i.test(READ),
      RL={zh:'中文',en:'英文',ja:'日文',ko:'韓文'}[READ.split('-')[0].toLowerCase()]||READ, FL=READ_CJK?'英文':'原文';
  var CJK_RE=/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/;
  function isEn(s){s=String(s||''); return READ_CJK?(/[A-Za-z]/.test(s)&&!CJK_RE.test(s)):CJK_RE.test(s);}
  function ansBi(e){if(e.zh||isEn(e.v)||(e.qs||[]).some(isEn))return true;
    return ansUsers(e.k).some(function(u){return (formOf(u.j).f||[]).some(function(x){return x.k===e.k&&isEn(x.q);});});}
  // 還要他動手的答案:後台照整份看板算(next_step.answers 的 need:推論的、空著又有還沒送出的表單在用;
  // 等 agent 重新代填的不算)。他在這一頁剛確認、剛填的,存檔回來之前也先不算
  function ansNeed(e){return !!e&&!e.redo&&(ansPending(e)||ansEmpty(e))&&(ansNext().need||[]).indexOf(e.k)>=0;}
  // 共用 / 這缺專用。pj=1 是這缺專用:針對某個 JD 寫的(例如「為什麼你適合這個職位」),不會被接到別的職缺;
  // 沒有 pj 是共用:關於他本人的事、他的看法,同一題直接沿用。我先判斷(pjw 寫理由),他按那顆切換就算他決定。
  function ansWhere(e){if(!e.pj)return ansUseLabel(e.k); var us=ansUsers(e.k); if(!us.length)return '還沒有表單在用';
    return cardName(us[0].j)+(us.length>1?(' 等 '+us.length+' 個職缺'):'');}
  // 分頁籤、常用答案標題數的是這一份:叫你確認的(後台的 ask;缺證據的不叫你確認,#315)
  function ansAsk(e){return ansNeed(e)&&(ansNext().ask||[]).indexOf(e.k)>=0;}
  function ansTodo(){return ansList().filter(ansAsk);}
  // 答案改了值:用到它的停著的頁標重打(雇主網頁上還是舊字)、你已確認的確認作廢,後台照整份看板做(form_record.changed);
  // 這裡只排一個 {refill: 鍵} 跟著存檔送,存好的表單和卡回來才畫(#308、#343)
  function ansRefill(k){if(!EVENTS.some(function(x){return x.refill===k;}))EVENTS.push({refill:k}); refreshBar();}
  // ---- 代投:agent 填表單 → 他核准 → agent 送出(tools/apply_run.py) ----
  // 確認時記下的答案由後台算(form_record.approval),之後答案一改確認就作廢(後台照整份看板做)
  var APPLY={};   // 幫你填表那一輪的進度(伺服器 /api/rev 帶回來)
  function applyBusy(id){return !!(APPLY.running&&(!APPLY.url||APPLY.url===id));}
  // 卡上代投那一行:agent 填好了沒、截圖、要改、核准/送出。最後一關一定是他按「✅ 核准送出」。
  // 一張一隻 agent:填、改、送出都是同一段對話(apply.session),它記得自己開的是哪一頁。
  var APFIX={};   // 哪幾張正在寫「要 agent 改什麼」(只在這個分頁,不存)
  var SUBMIT_SOON={};   // 剛按確認送出、8 秒後自己開始送的那幾張(計時器;只在這個分頁)
  // 卡上代投那一塊:現在走到哪、下一步是哪一顆(只有一顆主按鈕)、被什麼擋住(原因直接寫出來,手機沒有滑鼠提示)。
  // 卡上那一行、按鈕、要不要列進「這一頁要你處理的」、🚀 填表進度放哪一格,全部是後台下一步的 view(next_step.view),
  // 三處只照它畫,看板不自己比投遞狀態(#293、#343)。這裡只疊上這個分頁自己知道的兩件事:
  // 幫你填表正在跑這一張(進度從 /api/rev 來)、剛按確認送出的 8 秒內(SUBMIT_SOON)
  var AP_BTN={fill:'data-runone="apply|',fix:'data-applyfixopen="',clear:'data-applyclear="',actsent:'data-actsent="',
              submit:'data-applysubmit="',unapprove:'data-unapprove="',approve:'data-approve="'};
  function apBtnHTML(b,u){
    var tip=b.off?' disabled title="'+escA(b.off)+'"':b.b==='approve'?' title="先看過頁面再按;按了之後同一隻 '+AGENT+' 在那一頁送出,8 秒內可以復原"':'';
    return '<button class="stage-b'+(b.b==='approve'?' ap-approve':'')+(b.adv?' adv':'')+(b.main?' ap-main':'')+'" type="button" '+
      AP_BTN[b.b]+u+'"'+tip+'>'+esc(b.label)+'</button>';}
  function apShotHTML(x,id){
    if(x[0]==='live')return '<a class="ap-shot" data-apshot="1" href="/api/live?u='+encodeURIComponent(id)+'" target="_blank" rel="noopener">'+esc(x[2])+'</a>';
    if(x[0]==='ev')return evLink(id,x[1],x[2]);
    if(x[0]==='shot')return '<a class="ap-shot" data-apshot="1" href="/api/shot?s=fill&amp;u='+encodeURIComponent(id)+'" target="_blank" rel="noopener">'+esc(x[2])+'</a>';
    return '<a class="ap-shot" data-humancheck="1" href="'+escA(x[1])+'" target="_blank" rel="noopener">'+esc(x[2])+'</a>';}
  var NO_VIEW={line:[['idle','還沒拿到這張的下一步,等一下會自己更新']],buttons:[],shots:[],fill:{kind:'todo'}};
  function cardState(j){
    var v=nextOf(j.id).view||NO_VIEW, u=escA(j.id), live=applyBusy(j.id), busy=live||!!v.busy, rs=live?APPLY.stage:v.stage;
    var S={locked:!!v.locked, busy:busy, rf:v.rf||0, up:!!v.up, eye:!!v.eye&&!live, todo:busy?'':(v.todo||''),
           fill:live?{kind:'run'}:v.fill, runStage:rs};
    if(S.locked)return S;
    var line=v.line||[], btns=v.buttons||[], shots=v.shots||[], why=busy?'':(v.why||'');
    if(live){line=[['run','⏳ '+AGENT+' '+({submit:'正在送出',fix:'正在改'}[rs]||'正在填')+'…']]; btns=[];
      shots=shots.filter(function(x){return x[0]!=='live';});}
    // 剛按確認的 8 秒內時間到會自己開始送(還能按復原):這時不給「▶ 送出」,他照著按反而跟計時器撞上
    else if(v.soon&&SUBMIT_SOON[j.id]){line=[['okb','✅ 你已確認,幾秒後 '+AGENT+' 開始送出(按下面的「復原」可以取消)']];
      btns=btns.filter(function(b){return b.b!=='submit';}); S.todo='';}
    var st=line.map(function(x){return x[0]==='okb'?'<b class="ap-ok">'+esc(x[1])+'</b>':'<span class="ap-'+x[0]+'">'+esc(x[1])+'</span>';}).join('');
    S.html='<div class="ap-line'+(btns.some(function(b){return b.main;})&&!why?' go':'')+(why?' blocked':'')+'"><div class="ap-st">'+st+
      shots.map(function(x){return apShotHTML(x,j.id);}).join('')+'</div>'+
      (why?'<div class="ap-why">'+esc(why)+'</div>':'')+
      (btns.length?'<div class="ap-acts">'+btns.map(function(b){return apBtnHTML(b,u);}).join('')+'</div>':'');
    return S;
  }
  function applyLineHTML(j){
    var S=cardState(j); if(S.locked)return '';
    var a=(FB[j.id]||{}).apply||{}, u=escA(j.id);
    var fix=(APFIX[j.id]&&S.up)?'<div class="ap-fix"><input class="ap-note" type="text" data-applynote="'+u+
        '" placeholder="要 '+AGENT+' 改什麼'+(S.rf?'(只是照新答案重打可以不寫)':'')+'"><button class="stage-b adv" type="button" data-applyfix="'+u+
        '">交給 '+AGENT+'</button><button class="stage-b" type="button" data-applyfixopen="'+u+'">取消</button></div>':'';
    return S.html+fix+'</div>';
  }
  // 卡上的表單只剩一行:哪個平台、幾欄、還差什麼。
  function formLineHTML(j,ap){
    if(ap!=='ship'&&ap!=='sent')return '';
    var fm=formOf(j);
    if(!fm)return ap==='ship'?'<div class="fm-line">📋 表單還沒填</div>'+applyLineHTML(j):'';
    var head=esc(fm.plat||'表單')+' · '+(fm.f||[]).length+' 欄';
    if(fm.lock)return '<div class="fm-line">🔒 '+head+' · '+esc(mdOf(fm.at))+' 送出</div>';
    var td=nextOf(j.id).ask||[], rf=(fm.f||[]).filter(function(x){return x.refill;}).length;
    // 答案確認完也不等於可以投:最後那一步永遠是我把要上傳的 PDF 傳給他、等他說「可以投遞」。
    return '<div class="fm-line '+(td.length?'need':'ok')+'">📋 '+head+' · '+
      (td.length?'<b>⚠ '+td.length+' 條答案等你確認</b><button class="fm-go" type="button" data-ansgo="'+escA(td[0])+'">去常用答案看</button>'
                :'✅ 答案都確認過了')+
      (rf?'<span class="fm-rf">✎ '+rf+' 欄雇主網頁待重打</span>':'')+'</div>'+applyLineHTML(j);
  }
  // 🗂 答案庫面板。收起時一條一行字(題目或在用的公司、答案前段、「我推論的」、✓),點那一列才展開能改的欄位。
  // 同一題有好幾個答案(各職缺的「為什麼對這個職位有興趣」)排在一起,題目只寫一次。
  var ansQuery='';
  function ansTxt(e){return [e.q,e.v,e.zh,e.why,(e.qs||[]).join(' '),ansUseLabel(e.k),ansWhere(e)].join(' ').toLowerCase();}
  function wordsOf(s){s=s||''; return /[一-鿿]/.test(s)?s.length:s.split(/\s+/).filter(Boolean).length;}
  // 英文表單的上限都是字數(words),中文的算字元。取用到它的表單裡最緊的那個。
  function ansLim(k){var m=0; ansUsers(k).forEach(function(u){(formOf(u.j).f||[]).forEach(function(x){
    if(x.k===k&&x.lim&&(!m||x.lim<m))m=x.lim;});}); return m;}
  // 一條答案現在是哪一種(順序就是優先順序):收起那一行的標籤(ansHeadHTML)和展開後的說明(ansMetaText)共用這一個判斷
  function ansState(e){var p=ansPending(e); return e.redo?'redo':ansEmpty(e)?'empty':(p&&e.noev)?'noev':p?'pending':'';}
  function ansMetaText(e){var use=ansUseLabel(e.k), st=ansState(e);
    var what=st==='redo'?'↻ 答案清掉了,下一輪 '+AGENT+' 會代填':st==='empty'?'⚠ 答案還空著,等你寫'
      :st==='noev'?'⚠ 缺證據,不叫你確認('+e.noev+');讓 '+AGENT+' 重填那張':st==='pending'?'⚠ 我 '+mdOf(e.inf)+' 推論的,等你確認'
      :e.at?'你 '+mdOf(e.at)+' 確認':'你自己加的';
    return what+
      (use?('　用在:'+use):'　還沒有表單在用')+((e.qs&&e.qs.length)?('　見過的問法:'+e.qs.join(' / ')):'');}
  // 收起時那一行(整列重畫和打字當下就地更新共用這一支):題目、狀態、共用/這缺專用、用在哪、中文優先的答案前段、✓。
  // where=false:已經在那個職缺的分組底下,不再重複寫職缺名稱。
  function ansHeadHTML(e,title,where){
    var p=ansPending(e), em=ansEmpty(e), use=where===false?'':ansWhere(e), st=ansState(e);
    var tag={redo:'等 '+AGENT+' 代填',empty:'等你寫',noev:'缺證據',pending:'我推論的'}[st]||(e.tr?'✎ '+FL+'待重翻(幫你填表時會翻)':'');
    return '<span class="ans-hmain"><span class="ans-hq"><span class="ans-hqt">'+esc(title)+'</span>'+
        (tag?'<span class="ans-tag'+(e.tr&&!p&&!em?' tr':'')+'">'+tag+'</span>':'')+
        '<button class="ans-pj'+(e.pj?' job':'')+'" type="button" data-anspj="'+escA(e.k)+'">'+(e.pj?'這缺專用':'共用')+'</button>'+
        (use&&use!==title?'<span class="ans-use">'+esc(use)+'</span>':'')+'</span>'+
        '<span class="ans-hv">'+(em?'<i>(空白,點開用'+RL+'寫就好)</i>':esc(e.zh||e.v))+'</span></span>'+
      (p&&!em&&e.ev?evLink(e.ev.u,e.ev.f,'那一頁的截圖'):'')+
      (p&&!em&&!e.noev?'<button class="fm-b take ans-ok" type="button" data-ansok="'+escA(e.k)+'">✓ 這樣可以</button>':'');
  }
  // 程式自己截的那一頁(那張卡的證據夾,#315):要他確認或處理的事都附一張,點了開圖
  function evLink(u,f,label){return (u&&f)?'<a class="ap-shot" href="/api/evidence?u='+encodeURIComponent(u)+'&amp;f='+encodeURIComponent(f)+
    '" target="_blank" rel="noopener">'+esc(label)+'</a>':'';}
  function ansRowHTML(e,title,where){
    var p=ansPending(e), em=ansEmpty(e), k=escA(e.k), us=ansUsers(e.k), lim=ansLim(e.k), n=wordsOf(e.v);
    var long=/^(txt|op)$/.test(e.kind||'')||(e.v||e.zh||'').length>70||/\n/.test(e.v||e.zh||'');
    var ro=ansLocked(e.k)?' readonly title="有一張用到這條的卡送出結果不明,先確認到底送出沒有再改"':'';
    function box(f,val,ph,cls){return long
      ?'<textarea class="ans-t ans-ta'+(cls||'')+'" rows="3" data-ansk="'+k+'" data-ansf="'+f+'" placeholder="'+ph+'"'+ro+'>'+esc(val||'')+'</textarea>'
      :'<input class="ans-t'+(cls||'')+'" data-ansk="'+k+'" data-ansf="'+f+'" value="'+escA(val||'')+'" placeholder="'+ph+'"'+ro+'>';}
    var body='<div class="ans-body">'+
      '<label class="ans-f"><span>問題</span><input class="ans-t ans-q" data-ansk="'+k+'" data-ansf="q" value="'+escA(e.q||'')+'" placeholder="這題怎麼問"></label>'+
      (ansBi(e)
        ?'<label class="ans-f"><span>'+RL+'</span>'+box('zh',e.zh,'用'+RL+'寫就好,我送出前翻成'+FL)+'</label>'+
         '<label class="ans-f"><span>'+FL+'</span>'+box('v',e.v,'送出用,我照上面的'+RL+'翻',' ans-en')+'</label>'
        :'<label class="ans-f"><span>答案</span>'+box('v',e.v,'答案')+'</label>')+
      (lim?'<div class="ans-lim'+(n>lim?' over':'')+'">'+(ansBi(e)?FL+'上限 ':'上限 ')+lim+' · 現 '+n+'</div>':'')+
      '<label class="ans-f"><span>依據</span><textarea class="ans-t ans-why ans-ta" rows="1" data-ansk="'+k+'" data-ansf="why" placeholder="依據 / 備註(選填)">'+esc(e.why||'')+'</textarea></label>'+
      '<div class="ans-sc">'+(e.pj?'這缺專用:只給下面這個職缺用,不會被接到別的表單。':'共用:同一題的表單直接沿用。')+
        (e.pjw?'<span class="ans-scw">我這樣分的理由:'+esc(e.pjw)+'(不對就按上面那顆切換)</span>':'')+'</div>'+
      (e.pj&&us.length?'<div class="ans-jd">'+us.map(function(u){return '<div>'+jdTitleHTML(u.j)+(u.lock?' 🔒 已投出':'')+'</div>';}).join('')+'</div>':'')+
      '<div class="ans-meta">'+esc(ansMetaText(e))+'</div>'+
      // 刪除永遠按得到,也都能復原。還沒送出的表單在用的,按了是「清掉答案」:題目留著,下一輪 agent 代填
      // (在不在用照下一步的 __ans__:後台看整份看板算,不在看板上的卡也算)。
      '<div class="ans-acts"><button class="ans-del" type="button" data-ansd="'+k+'">'+
        (((NEXT.__ans__||{}).in_use||[]).indexOf(e.k)>=0?'清掉答案':'刪除這條')+'</button></div></div>';
    return fold('ans:'+e.k,ansHeadHTML(e,title||e.q||'(還沒寫問題)',where),body,{cls:'ansrow'+(p||em?' pend':''),attr:' data-k="'+k+'" data-txt="'+escA(ansTxt(e))+'"'});
  }
  // 共用的按題目排在一起(同一題好幾個答案,題目只寫一次);這缺專用的按職缺排在一起,標題就是那個職缺,點了開職缺原頁。
  function ansGroupsHTML(list){
    var g={},o=[]; list.filter(function(e){return !e.pj;}).forEach(function(e){var q=e.q||''; if(!g[q]){g[q]=[];o.push(q);} g[q].push(e);});
    var h=o.map(function(q){var es=g[q]; if(es.length===1||!q)return es.map(function(e){return ansRowHTML(e);}).join('');
      return '<div class="ans-qgrp"><div class="ans-qgrp-h">'+esc(q)+' <span class="n">'+es.length+'</span></div>'+
        es.map(function(e){return ansRowHTML(e,ansUseLabel(e.k)||'還沒有表單在用');}).join('')+'</div>';}).join('');
    var jg={},jo=[]; list.filter(function(e){return e.pj;}).forEach(function(e){var u=ansUsers(e.k)[0], id=u?u.j.id:'';
      if(!jg[id]){jg[id]={j:u&&u.j,es:[]};jo.push(id);} jg[id].es.push(e);});
    return h+jo.map(function(id){var G=jg[id];
      return '<div class="ans-qgrp ans-jgrp"><div class="ans-qgrp-h">'+(G.j?jdTitleHTML(G.j):'還沒有表單在用')+'</div>'+
        G.es.map(function(e){var n=ansUsers(e.k).length;   // 同一個答案好幾個職缺在用(地點選單):排在第一個底下,標題寫共幾個
          return ansRowHTML(e,(e.q||'(還沒寫問題)')+(n>1?' · 共 '+n+' 個職缺':''),false);}).join('')+'</div>';}).join('');
  }
  function ansSectionHTML(){
    var a=ansList(), td=ansTodo().length;
    // 用得多的在前;待確認的放上面,確認過的收在「已確認」裡。
    var srt=a.map(function(e,i){return {e:e,i:i,u:ansUsers(e.k).length};})
      .sort(function(x,y){return (y.u-x.u)||(x.i-y.i);}).map(function(o){return o.e;});
    var pl=srt.filter(ansNeed), dl=srt.filter(function(e){return !ansNeed(e);});
    var body='<p class="ans-hint">填表單的答案只放這裡,表單上只記用了哪一條。「共用」的同一題直接沿用,「這缺專用」的只給那個職缺;是哪一種我先判斷,按那顆就能切換。我推論的標「我推論的」,你確認一次,用到它的每張表單一起算過。</p>'+
      '<div class="ans-tools">'+(a.length>8?'<input class="ans-find" type="search" placeholder="搜尋常用答案(題目、答案、公司)" value="'+escA(ansQuery)+'">':'')+
      '<button class="fm-b" type="button" data-ansadd="1">＋ 新增一條</button></div>'+
      (a.length?'':'<p class="ans-empty">還是空的。我在表單裡推論出來的答案會先放進來,你也可以自己按「新增」。</p>')+
      (pl.length?'<div class="ans-grp">'+ansGroupsHTML(pl)+'</div>':'')+
      (dl.length?fold('ans:done','已確認 <span class="n">'+dl.length+'</span>',ansGroupsHTML(dl),{cls:'ans-done'}):'');
    return fold('ans','🗂 常用答案 <span class="n">'+a.length+'</span>'+(td?'<span class="ans-pendn">⚠ '+td+' 條待你確認</span>':''),
      body,{cls:'applygrp ans-d',hcls:'applyhd'});
  }
  // 搜尋:只藏起不符的列(不重畫,游標不會跑);同一題的整組都藏起來就連題目一起藏。
  function ansApplyFilter(){var q=(ansQuery||'').trim().toLowerCase();
    document.querySelectorAll('#app .ans-d .ansrow').forEach(function(r){r.hidden=!!q&&(r.getAttribute('data-txt')||'').indexOf(q)<0;});
    document.querySelectorAll('#app .ans-d .ans-qgrp').forEach(function(g){g.hidden=!g.querySelector('.ansrow:not([hidden])');});
    var dn=document.querySelector('#app .ans-done'); if(dn&&q)dn.open=true;}
  // 按鈕的復原:記下那一條原本的樣子,復原就整條放回去(同一個物件,表單指向它的 k 不變)。
  function ansUndo(e){var was=JSON.parse(JSON.stringify(e));
    return function(){Object.keys(e).forEach(function(p){delete e[p];}); Object.keys(was).forEach(function(p){e[p]=was[p];});};}
  function ansDone(msg,undo){dirty['__ans__']=true; renderAll(); refreshBar(); scheduleSave(900);
    snack(msg,function(){undo(); dirty['__ans__']=true; renderAll(); refreshBar(); scheduleSave(900);});}
  // 收起一條時,如果是他剛按「新增」什麼都沒寫的空白條,順手丟掉,免得答案庫堆一堆空列。
  function ansPurgeBlank(k){var e=ansOf(k);
    if(!e||(e.q||'').trim()||(e.v||'').trim()||(e.why||'').trim()||ansUsers(k).length)return false;
    ansList().splice(ansIndex(k),1); delete FOLD['ans:'+k]; dirty['__ans__']=true; scheduleSave(900); return true;}
  // 「正在準備」最上面:有哪些履歷、agent 照什麼挑。履歷在「⚙ 設定」頁改,這裡只顯示。
  function cutsSectionHTML(){
    var list=RESUMES.map(function(v){
      return '<div class="cutcard on"><div class="cutcard-h"><span class="cutcard-nm">'+esc(v.name||v.id)+'</span></div>'+
        '<div class="cutcard-f"><span class="cutcard-k">什麼時候用</span><span>'+esc(v.when||'(沒寫)')+'</span></div></div>';
    }).join('')||'<p class="cuts-empty">設定裡還沒有履歷。在看板的「⚙ 設定」新增。</p>';
    var body='<div class="cuts"><p class="cuts-help">按「▶ 準備履歷」時,'+esc(AGENT)+' 讀每張的 JD,照「什麼時候用」挑履歷和語言;判完你可以在卡上改。</p>'+
      '<div class="cutlist">'+list+'</div></div>';
    return fold('cuts','📄 你的履歷 <span class="cuts-active">'+RESUMES.length+' 份</span>',body,{cls:'cuts-d',hcls:'cuts-sum'});
  }

  // ready 的名字講的是「你要做的決定」,不是機器狀態:履歷產出來只是前提,那一階真正在等的是
  // 你決定直接投、還是先把履歷客製過。內部 key 不動('ready'),不搬既有資料。
  var STAGE_LABEL={prep:'📝 準備履歷中',ready:'🤔 待你決定：直接投，還是先客製',ship:'🚀 可以投了',sent:'📮 已投出'};
  var MOOD_WORD={like:'👍 喜歡',meh:'😐 普通',dislike:'👎 不喜歡',grow:'💪 差一點'};
  // 原頁失效:程式判過打不開(dead),而且他沒在「出錯了」按放回原處說頁面沒壞(live_ok)
  function isDead(j){return !!(j&&j.dead&&!(FB[j.id]||{}).live_ok);}
  // 自動推進可投遞時,這一階只剩「被擋住的」和「驗收還沒跑完的」:名字講這件事,不再是一個要他決定的關卡。
  // 直接投還是先客製,改到可投遞那張卡上決定(看過 agent 填好的頁面再決定)。
  if(FLOW.auto_advance)STAGE_LABEL.ready='🤔 待你決定:驗收過的會自動進「可以投了」;留在這裡的,卡上寫著是被擋住還是等你按';
  // 有序管線階段('' = 找工作區,不在管線內)
  // [key, icon, 標籤]:捲動後只留 icon + 數字,10 個分頁縮成一列還是全部按得到。
  // 以前開了自動推進時叫「還不能投/卡住了」,但留在這裡的不只被擋住的,還有開自動之前就在這、等你按的:
  // 分頁叫卡住了、底下卻寫「都過關」。一律叫「待你決定」,卡上各自講是被擋住還是等你按。
  var READY_NAME='待你決定';
  function tabLabel(k){for(var i=0;i<TABS.length;i++)if(TABS[i][0]===k)return TABS[i][2]; return k;}
  // 準備好的卡去哪:自動推進開著就是驗收過自動進「可以投了」,不然是送去「待你決定」
  function preparedTo(n){return FLOW.auto_advance?(n+' 張準備好了,驗收過就自動進「可以投了」'):(n+' 張送去「待你決定」');}
  var TABS=[['none','🆕','新職缺'],['like','👍','喜歡'],['meh','😐','普通'],['dislike','👎','不喜歡'],['grow','💪','差一點'],['all','☰','全部'],['prep','📝','準備履歷中'],['ready','🤔','待你決定'],['ship','🚀','可以投了'],['sent','📮','已投出'],['iv','🎤','面試準備'],['techerr','🔧','出錯了'],['rm','🗑','已移除'],['cfg','⚙','設定']];
  var STAGES=['prep','ready','ship','sent'];
  var PREP={};   // 準備履歷的進度(伺服器 /api/rev 帶回來);函式在 syncFromServer 旁邊
  // 投出去之後的結果。不是新的管線階段:投過就是投過,卡片的 app 一直是 'sent'
  // (可投遞包封存、reconcile、104 對帳都靠這個認),結果另外記在 oc 這一欄。
  // 分法照主流求職追蹤:Huntr/Teal 是「面試、offer」往前走,結案分「沒錄取、沒回音、我退出」;
  // Greenhouse 另外記在哪一關被刷,所以每個結果第一次出現的日期都留著(oc_at),
  // 面試後沒錄取跟連面試都沒有,算得出來。
  var OC=[['','⏳','等回音'],['iv','🗣','面試中'],['offer','🎉','Offer'],['rej','✗','沒錄取'],['ghost','🔕','沒下文'],['wd','↩︎','我不去了']];
  var OC_RANK={'':0,iv:1,offer:2}, OC_END={rej:1,ghost:1,wd:1};
  // 再投一次:被拒(或一直沒下文)過一陣子、履歷也更新了,同一個缺再投。上一次那一輪(投遞日、寄出的版本、結果、查回音、鎖住的表單)整份收進 tries,
  // 不刪;卡片回到可投遞,答案庫照舊,表單重填。「退回可投遞」是「當作沒投成」,會把紀錄洗掉,兩個不一樣。
  var AGAIN_OC={rej:1,ghost:1};
  // ghost_no(他說過「這張不要記沒下文」)是上一次那一輪的事:留在外面,新的一次永遠不會自動記沒下文
  // (上一次收進 tries 的是哪幾欄,寫在投遞狀態表的「再投一次」那一格)
  // 「沒送成」收進投遞歷史的那幾輪也算一次投遞(自動流程算第幾輪也是 tries + history):上一次是沒送成,就寫他說 agent 看錯了
  function triesHTML(f){
    var t=(f&&f.tries)||[], u=((f&&f.history)||[]).filter(function(h){return h&&h.event==='undo_sent';});
    if(!t.length&&!u.length)return '';
    var last=t[t.length-1], lu=u[u.length-1], lua=lu&&(lu.sent_at||((lu.apply||{}).sent||{}).at||'');
    var n='<div class="fm-line">🔁 第 '+(t.length+u.length+1)+' 次投遞 · 上次 ';
    // 兩種都有時看哪一次比較晚(沒送成那一輪沒有日期的舊資料當成比較早)
    if(lu&&(!last||String(lua).slice(0,10)>=String(last.sent_at||'').slice(0,10)&&lua))
      return n+(lua?esc(mdOf(lua))+' ':'')+esc(AGENT)+' 以為送出了,你說沒送成(它看錯了,當時的證據收在投遞歷史)</div>';
    var end=last.oc&&last.oc_at&&last.oc_at[last.oc];
    return n+esc(mdOf(last.sent_at||''))+' 投'+(last.oc?(end?','+esc(mdOf(end)):'')+' '+esc(ocLabel(last.oc)):'')+'</div>';
  }
  function ocLabel(k){for(var i=0;i<OC.length;i++)if(OC[i][0]===k)return OC[i][2];return k;}
  // 往前走(面試→offer)留著走過的日期;往回改(記錯了)就把後面的日期一起收掉;
  // 結案保留之前走到哪,換另一種結案則取代。
  function setOutcome(f,s){
    var at=f.oc_at?JSON.parse(JSON.stringify(f.oc_at)):{};
    Object.keys(at).forEach(function(k){
      if(OC_END[s]?(OC_END[k]&&k!==s):(OC_END[k]||(OC_RANK[k]||0)>OC_RANK[s]))delete at[k];});
    if(s&&!at[s])at[s]=today();
    if(s)f.oc=s; else delete f.oc;
    if(Object.keys(at).length)f.oc_at=at; else delete f.oc_at;
  }
  function reachedIv(f){var a=f.oc_at||{};return !!(a.iv||a.offer||f.oc==='iv'||f.oc==='offer');}
  function reachedOffer(f){return !!((f.oc_at||{}).offer||f.oc==='offer');}
  function replied(f){return reachedIv(f)||!!(f.oc_at||{}).rej||f.oc==='rej';}   // 沒錄取也是一種回音
  function mmdd(d){return d?String(d).slice(5).replace('-','/'):'';}
  function counts(){var c={like:0,meh:0,dislike:0,grow:0,none:0,all:0,prep:0,ready:0,ship:0,sent:0,techerr:0,rm:0,live:0};
    jobs.forEach(function(j){var f=FB[j.id]||{};
      if(f.rm){c.rm++;return;}                    // 移除掉的不進任何其他分頁與數字
      if(f.app==='sent'&&(f.oc==='iv'||f.oc==='offer'))c.live++;
      // 一張卡只在一個地方:進了流程就算在那一階,不再算在「喜歡」(以前兩邊都有,按了送去準備卡片不會離開喜歡)
      if(STAGES.indexOf(f.app)>=0){c[f.app]++; return;}
      if(isBlocked(j))return;                     // 封鎖的公司清單上不列,數字也不算
      var s=sentOf(j.id)||'none';c[s]++;c.all++;});
    c.iv=c.live;   // 🎤 那一籤的數字＝正在面試(或拿到 offer)的幾家:那是他現在要準備的事
    return c;}
  // 規矩檢查(tools/board_check.py)用:每張卡該在哪一頁、數字怎麼算,跟畫面用同一套。只讀。
  window.__jobsalvoFlow={counts:counts,
    tabOf:function(id){var f=FB[id]||{}, j=jobOf(id); if(f.rm)return 'rm'; if(STAGES.indexOf(f.app)>=0)return f.app;
      if(j&&isBlocked(j))return ''; return sentOf(id)||'none';},
    hidden:function(){return jobs.filter(function(j){var f=FB[j.id]||{}; return !f.rm&&STAGES.indexOf(f.app)<0&&isBlocked(j);}).length;},
    total:function(){return jobs.length;}, fb:function(id){return JSON.parse(JSON.stringify(FB[id]||{}));}};
  // 分頁分三組,長相就講清楚它們是不同的東西:
  //  找工作 = 照你的表態篩(待評估、喜歡…);投遞流程 = 一條往右走的線(準備 › 決定 › 可投遞 › 已投遞 › 面試);
  //  其他 = 技術錯誤、已移除、設定。以前 14 顆長一樣排一排,看不出流程往哪走。
  var TAB_GRP={none:'find',like:'find',meh:'find',dislike:'find',grow:'find',all:'find',
    prep:'flow',ready:'flow',ship:'flow',sent:'flow',iv:'flow',techerr:'misc',rm:'misc',cfg:'misc'};
  var TAB_GRP_T={find:'找工作',flow:'投遞流程',misc:''};
  function renderTabs(){
    var c=counts(), h='', grp='';
    TABS.forEach(function(t){
      var key=t[0];
      if(TAB_GRP[key]!==grp){if(grp)h+='</div>'; if(grp==='find')h+='<div class="tabrow2">'; grp=TAB_GRP[key];
        h+='<div class="tabgrp g-'+grp+'">'+(TAB_GRP_T[grp]?'<span class="tabgrp-k">'+TAB_GRP_T[grp]+'</span>':'');}
      else if(grp==='flow')h+='<span class="tab-arr" aria-hidden="true">›</span>';
      var cls='tab'+(active===key?(' on '+(key)):'');
      // 可投遞那一籤多帶一個 ⚠:答案庫裡還有幾條我推論的在等他確認。不另開分頁,比照客製工單的做法。
      var wn=(key==='ship')?ansTodo().length:0;
      // 「還在進行中的面試/offer」以前掛在已投遞那一籤當 🗣 小徽章;現在 🎤 面試準備那一籤的數字就是它,不重複講。
      var run=(key==='prep'&&PREP.running)?'<span class="ivn">⏳</span>':'';
      h+='<button class="'+cls+'" data-tab="'+key+'" title="'+t[2]+'"><span class="tico">'+t[1]+'</span>'+
         '<span class="tlbl">'+t[2]+'</span>'+(key==='cfg'?'':'<span class="n">'+(c[key]||0)+'</span>')+
         (wn?'<span class="warnn">⚠'+wn+'</span>':'')+run+'</button>';
    });
    $('tabs').innerHTML=h+'</div></div>';
    // 手機上是單列橫捲,選中的那個可能整個在畫面外,一進來會看不出自己在哪一頁。
    // 只能捲分頁列自己,不能用 scrollIntoView:分頁列是 sticky,瀏覽器會連整頁一起垂直捲,
    // 而且每叫一次就把目前的捲軸值再加一次(量到 3119→6238→12476)。每一次重畫都會發生,
    // 畫面因此被扔到完全不同的位置——他說的「按一下整個不見了」就是這個。
    centerTab();
  }
  // 把選中的分頁橫向置中。只動 #tabs 自己的 scrollLeft,不碰頁面捲軸。
  function centerTab(){
    var t=$('tabs'); if(!t)return;
    var on=t.querySelector('.tab.on'); if(!on)return;
    // 手機上每一組自己一列橫捲;捲下去縮成一列時是整條 #tabs 在捲。捲得動的那一層才動。
    var box=on.parentNode; while(box!==t&&!(box.scrollWidth>box.clientWidth+1))box=box.parentNode;
    if(box!==t)t.scrollLeft=0;
    var want=box.scrollLeft+(on.getBoundingClientRect().left-box.getBoundingClientRect().left)-(box.clientWidth-on.offsetWidth)/2;
    box.scrollLeft=Math.max(0,Math.min(want,box.scrollWidth-box.clientWidth));
  }
  // 標籤顏色照設定裡的順序輪流用四種,同一個標籤永遠同一色。
  function _tagCls(x){for(var i=0;i<TAGS.length;i++)if(TAGS[i].name===x)return 'c'+(i%4);return 'c3';}
  function _tagHTML(j){var t=tagsOf(j);if(!t.length)return '';return '<div class="tags">'+t.map(function(x){return '<span class="tag t-'+_tagCls(x)+(activeFacet===x?' on':'')+'" data-f="'+esc(x)+'">'+esc(x)+'</span>';}).join('')+'</div>';}
  // 從 agent 寫的門檻整段裡,機械抽出年資/學歷/語言做成徽章。抽不到就沒有,絕不推測。
  var BAR_PATS=[
    [/(\d+)\s*年以上/, function(m){return m[1]+'+ 年';}],
    [/(\d+)\s*[-~到]\s*(\d+)\s*年/, function(m){return m[1]+'-'+m[2]+' 年';}],
    [/(\d+)\+?\s*(?:years?|yrs?)/i, function(m){return m[1]+'+ 年';}],
    [/經[歷驗]不拘|不限年資|無年資|entry.?level|new ?grad|應屆/i, function(){return '不限年資';}],
    [/碩士以上|碩士|master'?s|\bMSc\b|\bPhD\b|博士/i, function(m){return /博士|PhD/i.test(m[0])?'要博士':'要碩士';}],
    [/大學以上|學士|bachelor'?s/i, function(){return '大學以上';}],
    [/學歷不拘|不限學歷/, function(){return '學歷不拘';}],
    [/英文[^,;。]{0,6}(流利|精通|母語|商務)|fluent english|english.{0,10}(required|proficien)/i, function(){return '英文要好';}]
  ];
  function barChips(bar){
    if(!bar)return '';
    var out=[],seen={};
    BAR_PATS.forEach(function(p){
      var m=String(bar).match(p[0]); if(!m)return;
      var t=p[1](m); if(seen[t])return; seen[t]=1;
      var cls=/博士|碩士|英文/.test(t)?'barchip hard':'barchip';
      out.push('<span class="'+cls+'">'+esc(t)+'</span>');
    });
    return out.length?'<span class="barchips">'+out.join('')+'</span>':'';
  }
  // 104 那種固定順序的一行事實:地點｜年資學歷｜薪資。每張卡位置都一樣,用掃的不用讀的。
  // 地點常常是整串地址(台中市西區忠明南路497號…),看板只需要到市或區。
  function shortLoc(x){
    x=String(x).trim();
    var m=x.match(/^[^,;，、]{0,4}?[縣市][^,;，、]{0,3}?區/)||x.match(/^[^,;，、]{0,6}?[縣市]/);
    if(m)return m[0];
    return x.length>12?x.slice(0,12)+'…':x;
  }
  function factsLine(j){
    var s=j.sum||{},b=[];
    function ok(x){return x&&!_EMPTY.test(String(x).trim());}
    function cut(x,n){x=String(x);return x.length>n?x.slice(0,n)+'…':x;}
    if(ok(s.loc))b.push('<span class="fact">📍'+esc(shortLoc(s.loc))+'</span>');
    var ch=barChips(s.bar); if(ch)b.push(ch);
    if(ok(s.salary))b.push('<span class="fact">💰'+esc(cut(s.salary,14))+'</span>');
    if(ok(s.deadline))b.push('<span class="fact dl">⏳'+esc(cut(s.deadline,12))+'</span>');
    var rk=riskBadge(j); if(rk)b.push(rk);
    return b.length?'<div class="facts">'+b.join('')+'</div>':'';
  }
  // 待你決定、正在準備:「為什麼適合你」是這一階做決定的依據,攤在標題下面(兩行,點一下看全文)。
  function fitLineHTML(j){var s=j.sum||{};
    if(!s.fit||_EMPTY.test(String(s.fit).trim()))return '';
    return '<div class="fitline"><span class="fitline-k">為什麼適合你'+(s.by?'('+esc(s.by)+')':'')+'</span><span class="srv clamp c2" title="點一下看全文">'+esc(s.fit)+'</span></div>';}
  function _sumHTML(j,noFit){var s=j.sum;
    if(!s){return '<div class="ammo"><span class="k">彈藥</span>'+inl(j.ammo)+'</div><p class="note">'+inl(j.note)+'</p>';}
    function v(x){return (x==null||x==='')?'無':x;}
    // 每一段都先收兩三行:一張卡攤開五段全文,手機上一張就是一整屏。點一下看全文。
    function rowc(l,x){return '<div class="sr"><span class="srk">'+l+'</span><span class="srv clamp c2" title="點一下看全文">'+esc(v(x))+'</span></div>';}
    // 摘要是 agent 讀 JD 寫的(找缺的判斷):標明是 agent 判斷,不是程式核對過的事實
    return '<div class="sum">'+(s.by?'<div class="sr"><span class="srk">🤖</span><span class="srv">以下是 agent 讀 JD 寫的摘要('+esc(s.by)+')</span></div>':'')+
      (noFit?'':'<div class="sr fitrow"><span class="srk">為什麼適合你</span><span class="srv clamp" title="點一下看全文">'+esc(v(s.fit))+'</span></div>')+
      rowc('公司在做什麼',s.co)+
      // 地點/薪/死線已經在上面那行事實講過(factsLine),這裡不再重複一次。
      rowc('門檻',s.bar)+rowc('彈藥',s.ammo)+
    '</div>';}
  // 履歷用文字顯示(不是頁圖):多份履歷只差動機這一行,存多張全頁圖是浪費——
  // 30 缺會變 28MB,他手機載不動。文字四份共 17KB,而且在手機上可選取、會換行。
  // 母版那區還是頁圖(它只有 7 筆,而且是給人看排版的參考)。
  function openResume(id,variant){var j=null;for(var i=0;i<jobs.length;i++){if(jobs[i].id===id){j=jobs[i];break;}}
    var mm=null;if(!j&&typeof masters!=='undefined'){for(var k=0;k<masters.length;k++){if(masters[k].id===id){mm=masters[k];break;}}}
    if(!j&&!mm)return;
    var html=null,pages=null;
    if(j){
      var pick=pickOf(j); if(!pick)return;
      var file=cardFile(j,'resume',pick.variant,pick.lang);
      if(file&&file.kind==='custom'){previewPage(file.url); return;}
      if(file&&file.preview){previewPage(file.url); return;}
      if(file)return;
      var rz=j.resume||{}, key=pick.lang+'-'+pick.variant;
      var selected=rz.variants&&rz.variants[key];
      var nested=selected&&(selected.html||selected.html_lazy||(selected.pages&&selected.pages.length)||selected.pages_n);
      var vv=nested?selected:rz, lazyKey=nested?key:'__top__';
      html=vv.html||null; pages=vv.pages||null;
      // 伺服器送頁面時把履歷預覽抽掉了(board_server.lean_jobs),只留記號:點開這一下才去拿
      if(!html&&!(pages&&pages.length)&&(vv.html_lazy||vv.pages_n)){
        if(openResume._busy)return; openResume._busy=true;
        fetch('/api/resume?u='+encodeURIComponent(id)+'&v='+encodeURIComponent(lazyKey)).then(function(r){if(!r.ok)throw new Error('http '+r.status);return r.json();})
          .then(function(preview){openResume._busy=false;
            var rz2=j.resume=j.resume||{}; rz2.variants=rz2.variants||{};
            if(lazyKey==='__top__')Object.assign(rz2,preview);
            else rz2.variants[key]=Object.assign({},rz2.variants[key]||{},preview);
            var got=lazyKey==='__top__'?rz2:rz2.variants[key];
            if(got.html||(got.pages&&got.pages.length))openResume(id,variant); else snack('這份履歷預覽讀不到:到設定頁「你的履歷」重新上傳這一份再試');})
          .catch(function(){openResume._busy=false; snack('履歷預覽載不下來,等一下再按一次');});
        return;
      }
    }
    else {pages=mm[variant]||LANGS.map(function(l){return mm[l];}).filter(function(x){return x&&x.length;})[0];}
    if(!html&&(!pages||!pages.length))return;
    var head='<button class="rzm-x" type="button">✕ 關閉</button>';
    var body;
    if(html){var A=D.rz||{};
      body='<div class="rzm-doc"><style>'+(A.css||'')+'</style>'+
           html+'</div>';}
    else body='<p class="rzm-hint on">點頁面可放大／縮小</p>'+
              pages.map(function(p){return '<img class="rzm-pg" src="'+p+'" alt="履歷頁">';}).join('');
    modal('<div class="rzm-box">'+head+body+'</div>','');}
  // 看板自己的檔(履歷、附件、客製版)一律在看板裡彈窗看,不另開分頁、不下載:卡片、設定頁、附件都走這一個。
  // 來源檔給的是預覽圖(/api/source-preview),還沒產出時伺服器回 404,不要留一張破圖;其他是檔案本身(PDF)。
  function previewPage(url){
    if(!/^\/api\/source-preview\?/.test(url)){
      modal('<div class="rzm-box"><button class="rzm-x" type="button">✕ 關閉</button>'+
        '<iframe class="rzm-pdf" src="'+escA(url)+'" title="檔案預覽"></iframe></div>','');
      return;}
    modal('<div class="rzm-box"><button class="rzm-x" type="button">✕ 關閉</button>'+
      '<img class="rzm-pg" src="'+escA(url)+'" alt="原檔預覽"></div>','');
    var im=document.querySelector('#rzmodal .rzm-pg');
    if(im)im.onerror=function(){closeModal(); snack('這份還沒有 PDF 預覽');};}
  // 全螢幕檢視(履歷、大字讀稿)共用這一個:點外面或 ✕ 關掉、Esc 關掉、開著時背景不跟著捲。
  function modal(inner,cls){
    var ov=document.getElementById('rzmodal');
    if(!ov){ov=document.createElement('div');ov.id='rzmodal';document.body.appendChild(ov);
      ov.addEventListener('click',function(e){
        if(e.target===ov||e.target.classList.contains('rzm-x')){closeModal();return;}
        // 母版是整頁圖,在手機上被壓成 5px 的字。點一下切原寸,再點一下切回來。
        if(e.target.classList.contains('rzm-pg')){ov.classList.toggle('zoom');ov.scrollLeft=0;}});}
    ov.className=cls||''; ov.innerHTML=inner;
    ov.scrollTop=0; ov.style.display='flex'; document.body.classList.add('modal-open');}
  function closeModal(){var ov=document.getElementById('rzmodal'); if(!ov)return;
    ov.style.display='none'; ov.classList.remove('zoom'); document.body.classList.remove('modal-open'); rdStop();
    if(_resumePromptCancel){var cancel=_resumePromptCancel;_resumePromptCancel=null;cancel();}}
  // 管線階段控制:一步一步走,只給「下一步／退回」,不會誤點掉出去。
  // prep→ready 不用手動:履歷一產出,收尾就自動把這輪的卡推到「待你決定」(有履歷就該他決定了)。
  // 還停在 prep 又有履歷的,是他刻意拉回來磨的,只顯示狀態、不自動再推。ready→ship→sent 他手動點。
  var _curId=null;
  // 「⋯」:罕用和破壞性的動作收在這裡,不攤在平面上。
  // 版面的三條規矩:一列只有一個主動作;罕用與破壞性一律進 ⋯;位置固定。
  // 以前一張卡上八顆一樣大的按鈕,他最常按的心情跟「移除」長得一樣重,按錯的代價差很多。
  // 選單就放在自己這張卡/這家公司的 DOM 裡,所以既有的處理器(靠 closest('article'))原封不動。
  function moreHTML(inner){
    if(!inner)return '';
    return '<span class="more"><button class="more-b" type="button" data-omore="1" aria-label="其他動作">⋯</button>'+
           '<span class="more-m" hidden>'+inner+'</span></span>';
  }
  function stageRowHTML(ap,j){
    if(ap==='prep'){
      var _rzv=(j&&j.resume&&j.resume.variants)||null;
      var _hasRz=_rzv&&Object.keys(_rzv).some(function(k){var v=_rzv[k];
        return v&&(v.html||v.html_lazy||(v.pages&&v.pages.length)||v.pages_n);});
      // 有履歷卻停在這一階 = 他刻意拉回來磨的,給一顆明確的出口讓他自己放行。
      // 沒履歷的則要講清楚「不會自己跑」,免得他以為背景有人在做。
      // 上一輪沒產出的原因(抓不到 JD、標題對不上…):卡還在準備區,原因寫在這裡,要不要丟由他決定。
      var _pn=(j&&j.prep_note)?'<span class="stage-blocked">⚠ 上一輪沒產出：'+esc(j.prep_note)+'</span>':'';
      return actRow(_pn+(_hasRz
          ? '<span class="stage-ok">✅ 履歷已產出</span><button class="stage-b adv" data-adv="ready" type="button">'+(FLOW.auto_advance?'🚀 送去驗收,過了自動進「可以投了」 →':'🤔 送去「待你決定」 →')+'</button>'
          : (PREP.running?'<span class="stage-wait">⏳ 正在準備履歷</span>'
             :(j&&autoDo(j.id)==='prep'?'<span class="stage-wait">⏳ '+esc(nextOf(j.id).auto.line)+'</span>'
               :'<span class="stage-wait" title="不會自己啟動,要按這一頁最上面那顆">⏳ 等產履歷 · 按上面「▶ 準備履歷」</span>')))+
        '<button class="stage-b back" data-back="" type="button">← 退出流程</button>',{one:'prep|'+_curId});
    }
    // 掃履歷當下最常發現的就是「這個缺已經關了/這個 URL 指到別的缺」。原本技術錯誤鈕
  // 藏在「詳細」那個收起來的區塊裡,看履歷時按不到,所以直接放到階段列上。
  // 按下去跟 cut_tailor 的關站閘門同一個動作:標技術錯誤 + 清掉階段(不然有 app 就還算在
  // 管線裡,照階段顯示,不會進技術錯誤區),客製旗標一起收掉。
  function errBtnHTML(){return active==='techerr'?'':'<button class="stage-b err" data-err="1" type="button" title="連結壞了或指到別的缺">🔧 標成出錯了</button>';}
  // 已投遞那一行後面接現在的結果;還在等的講等了幾天,他才判斷得了要不要記成沒下文。
  function ocStatusHTML(f){
    var a=f.oc_at||{}, s=f.oc||'', how=reachedIv(f)?'（面試後）':'';
    if(!s){var d=daysSince(f.sent_at); return d===null?'':'<span class="oc-st">　· 等了 '+d+' 天</span>';}
    if(s==='iv')return '<span class="oc-st iv">　· 🗣 '+mmdd(a.iv)+' 開始面試</span>';
    if(s==='offer')return '<span class="oc-st offer">　· 🎉 '+mmdd(a.offer)+' 拿到 offer</span>';
    if(s==='rej')return '<span class="oc-st end">　· ✗ '+mmdd(a.rej)+' 沒錄取'+how+'</span>';
    if(s==='ghost')return '<span class="oc-st end">　· 🔕 '+mmdd(a.ghost)+' 記成沒下文</span>';
    if(s==='wd')return '<span class="oc-st end">　· ↩︎ '+mmdd(a.wd)+' 我不去了'+how+'</span>';
    return '';
  }
  function ocChipsHTML(f){
    var s=f.oc||'';
    return '<span class="oc-row"><span class="oc-k">結果</span>'+OC.map(function(o){
      return '<button class="oc-b'+(o[0]===s?' on':'')+(OC_END[o[0]]?' end':'')+'" data-oc="'+o[0]+'" type="button">'+
        o[1]+' '+o[2]+'</button>';}).join('')+'</span>';
  }
  // 移除:除了已投遞(投都投了,留著當紀錄),每一階段都給。按了收進「🗑 已移除」,可以再放回來。
  function rmBtnHTML(){return '<button class="stage-b rm" data-rm="1" type="button" title="從看板收走,之後在「🗑 已移除」可以放回來">🗑 移除</button>';}
  // 每張卡的動作列都用這個組:左邊是這一階要做的事,右邊固定是「有問題/移除」。
  // 「找類似這張的」:他看 JD 看到有趣的當場點,加進「指名要找的」清單,之後一起跑。
  function seedBtnHTML(id){
    return '<button class="stage-b seedb" data-seed="'+escA(id)+'" type="button"'+
      ' title="把這張加進「指名要找的」;到最上面「🔎 找新職缺」一起跑,找類似這張、適合你的缺">🔎 找類似的</button>';
  }
  // 「🗂 改類別」:關鍵字分錯的,使用者在這裡直接指定;選「照關鍵字」就拿掉指定。
  function catBtnHTML(){
    return '<span class="catpick"><button class="stage-b" data-catopen="1" type="button">🗂 改類別</button>'+
      '<span class="catpick-m" hidden>'+catOrder.map(function(c){return '<button class="stage-b" data-catpick="'+escA(c)+'" type="button">'+
      esc((catIcon[c]||'')+' '+c)+'</button>';}).join('')+'<button class="stage-b" data-catpick="" type="button">↺ 照關鍵字</button></span></span>';
  }
  function actRow(left,opts){
    opts=opts||{};
    // 主動作留在原位,其餘(找類似的、有問題、移除)收進 ⋯
    var rare=(opts.one?'<button class="stage-b" type="button" data-runone="'+escA(opts.one)+'">'+
      {prep:'▶ 只準備這張',apply:'▶ 只讓 '+AGENT+' 填這張',replies:'📬 只查這張的應徵進度'}[opts.one.split('|')[0]]+'</button>':'')+
      (opts.seed?seedBtnHTML(opts.seed):'')+catBtnHTML()+(opts.noErr?'':errBtnHTML())+(opts.noRm?'':rmBtnHTML());
    // 開頭是狀態文字的那幾階:文字自己佔一行,按鈕接在下一行。不然手機上左右兩邊
    // 互相擠,狀態句會被壓成一條 4 行的細長柱。
    var lc='sr-l'+(/^<span class="stage-(wait|ok|done|blocked)/.test(left)?' flow':'');
    return '<div class="stage-row"><span class="'+lc+'">'+left+'</span>'+
           (rare?'<span class="sr-r">'+moreHTML(rare)+'</span>':'')+'</div>'+
           (opts.sub?'<div class="stage-row sub">'+opts.sub+'</div>':'');
  }
  function shipGateHTML(j){
    var why=shipBlocked(j);
    // agent 判斷職缺關了(程式核對不了):可以一鍵說它判錯了
    if(why)return '<span class="stage-blocked">⛔ '+esc(why)+'</span>'+(judgedClosed(j)?
      '<button class="ap-undo" type="button" data-judgedno="'+escA(j.id)+'">不對,職缺還在</button>':'');
    // 自動推過一次、你又退回來的(或開自動之前就在這一階的),自動流程不會再推它:照實講,不要掛著「會自動進」
    return (autoDo(j.id)==='advance'?'<span class="stage-wait">⏳ '+esc(nextOf(j.id).auto.line)+'</span>'
                :(FLOW.auto_advance?'<span class="stage-wait">這張不會自動往下走(你退回來過,或開自動之前就在這),要投就按右邊</span>':''))+
      '<button class="stage-b adv" data-adv="ship" type="button">🚀 可以投了 →</button>'+linkNoteHTML(j);
  }
  // 一個決定點、兩個出口:直接投(可投遞)或先客製。客製走完卡會回到這一階,
    // 讓他再看一次,所以客製不是另一個階段,是這一步的另一個出口。
    if(ap==='ready')return actRow(shipGateHTML(j)+
      '<button class="stage-b back" data-back="prep" type="button">← 退回「準備履歷中」</button>',{sub:custPartHTML(j)});
    // 程式不會替他送出。這一顆只是「我已經在外部送出了」的紀錄,文案要講明白。
    // 以前要按兩次(先確認再生效);主流是按下去就生效、當場給復原(Gmail 寄信也是這樣),
    // 誤按只要點一下復原,不用每次都多按一下。
    // 這一階的主線是上面代投那一塊(填表 → 看頁面 → 核准送出);自己在外部投的紀錄是另一條路,用次要的樣子。
    if(ap==='ship')return actRow('<button class="stage-b" data-adv="sent" type="button" '+
      'title="程式不會替你送出;這是你自己在外部送出後的紀錄">📮 我已在外部送出</button>'+
      '<button class="stage-b back" data-back="ready" type="button">← 退回「待你決定」</button>',{sub:triesHTML(FB[_curId])+custPartHTML(j),one:'apply|'+_curId});
    if(ap==='sent'){var sf=FB[_curId]||{}, at=sf.sent_at||'';
      // 證據只有一種的要標出來:已投遞不等於「外面也對得上」。ev 是查證後留下的說明,
      // 有值就代表這張只有送出頁的證據(沒有確認信、104 紀錄之類的第二個來源)。
      var ev=sf.ev||'';
      return actRow('<span class="stage-done">📮 已投出'+(at?'　'+esc(at):'')+ocStatusHTML(sf)+'</span>'+
        (ev?'<span class="ev-weak" title="'+esc(ev)+'">⚠ 只有送出頁證據</span>':'')+
        // 在面試、或題庫裡有這家的專屬題:給一顆直接去準備那一家
        (ivHas(_curId)?'<button class="stage-b adv" data-ivgo="1" type="button">🎤 準備面試 →</button>':'')+
        // 有結果(面試、offer、沒錄取…)的就是真的投出去了,不給「退回」:一按結果跟日期全清。沒錄取、沒下文的給「再投一次」。
        // 還在等回音的:agent 親手送出、看到過已收到申請頁的給「沒送成」(先跳確認,證據收進投遞歷史);
        // 你在外部送出、平台對帳進來的給「退回」。給哪一顆照投遞狀態表(guard 看證據來源)
        (AGAIN_OC[sf.oc]?'<button class="stage-b adv" data-again="1" type="button" title="上一次的紀錄留在歷史裡,這張回到「可以投了」、用現在的履歷重填">🔁 再投一次</button>'
          :sf.oc?''
          :nextOf(_curId).back==='undo_sent'?'<button class="stage-b back" data-undosent="1" type="button" title="'+escA(AGENT)+' 當時看到了已收到申請的頁面;你查過其實沒送成,就按這裡:證據收進投遞歷史,這張回到「可以投了」重填">↩︎ 沒送成</button>'
          :nextOf(_curId).back==='back'?'<button class="stage-b back" data-back="ship" type="button" title="其實沒投成:回到「可以投了」重來,之前的確認送出也作廢">← 退回「可以投了」</button>':''),
        {noErr:true,noRm:true,one:['rej','wd'].indexOf(sf.oc||'')<0?'replies|'+_curId:'',sub:triesHTML(sf)+ocChipsHTML(sf)+replyLineHTML(_curId,sf)});}
    // 「🔧 出錯了」:標的時候(閘門或他自己按)原本的心情、階段另存在 s0、app0,給一顆照原樣放回去
    var _tf=FB[_curId]||{};
    if(_tf.s==='techerr'&&(_tf.s0||_tf.app0))return actRow('<button class="stage-b adv" data-errback="1" type="button" '+
      'title="不是出錯:回到標出錯了之前的樣子;頁面失效的標記也不算了(之後頁面真的打不開,會照樣報在待處理)">↩︎ 不是出錯,放回原處('+
      esc([MOOD_WORD[_tf.s0],(STAGE_LABEL[_tf.app0]||'').split('：')[0]].filter(Boolean).join(' · '))+')</button>'+
      '<button class="stage-b add" data-adv="prep" type="button">📝 送去準備履歷中 →</button>',{seed:_curId});
    // 還沒進管線的卡(待評估、找工作區):他就是在這裡看 JD,「找類似的」放這裡最有用
    return actRow('<button class="stage-b add" data-adv="prep" type="button">📝 送去準備履歷中 →</button>',{seed:_curId});
  }
  // 客製只在待你決定、可投遞出現(已投遞的投出去就定了);每份檔可用 agent 客製或直接上傳 PDF。
  // 每份檔獨立收下/退回,只有已收下的版本會替換可投遞夾裡原本那份。
  function custPartHTML(j){
    var f=FB[j.id]||{}, docs=f.custom_docs||{}, delivery=(f.apply||{}).delivery||{},
      newCustomProfile=delivery.method==='platform_profile'&&delivery.profile_kind==='custom',
      cur=custCurrent(j),
      rows=Object.keys(docs).map(function(id){
      var x=docs[id]||{}, state=x.status||'', label={review:'⏳ 等你看',accepted:'✅ 已收下',rework:'↻ 退回重寫',working:'⏳ 正在客製',failed:'⚠ 產出未通過'}[state]||'';
      // 收下之後原始檔換過(在設定頁重新上傳):這份客製版不會寄出去,寄的是新的原始檔。照實講,給重跑、上傳、改回
      if(custStale(j,id))label='⚠ 原始檔換過了,這份客製版不寄(現在寄原始檔);要的話重新客製或上傳新的';
      // 換了履歷、語言或附件之後留下的舊紀錄:不會寄出去、不擋這張,只給清掉(收下、退回、看差異都對不到現在的檔)
      // 同一份檔另一個語言的紀錄:換回那個語言會再用它,照實講是哪個語言的
      var kp=String(id).split(':'), otherLang=cur&&kp.length===3&&cur.some(function(c){return c.indexOf(kp[0]+':'+kp[1]+':')===0;});
      if(cur&&cur.indexOf(id)<0)return '<div class="cust-doc"><span class="cust-st">'+esc(x.name||id)+' · '+
        (otherLang?esc(langLabel(kp[2]))+' 的客製版:這張現在寄別的語言,換回 '+esc(langLabel(kp[2]))+' 會再用它':'這張現在不寄這份(換過履歷、語言或附件)')+'</span>'+
        '<button class="cust-b off" type="button" data-cust-action="clear" data-cust-orphan="1" data-cust-item="'+escA(id)+'" data-cust-url="'+escA(j.id)+'">清掉這筆紀錄</button></div>';
      var html='<div class="cust-doc"><span class="cust-st">'+esc(x.name||id)+' · '+label+'</span>'+
        (x.error||x.last_error?'<span class="cust-why">'+esc(x.error||x.last_error)+'</span>':'')+
        ((x.candidate_path||x.path)?'<button class="cust-b" type="button" data-cust-diff="'+escA(id)+'" data-cust-url="'+escA(j.id)+'">看原檔與客製版差異</button>':'');
      if(newCustomProfile&&id.indexOf('resume:')===0)html+='<span class="cust-why cust-route-reminder">agent 會在平台上另外開一份履歷。</span>';
      if(state==='review')html+='<textarea class="cust-feedback" rows="2" data-cust-feedback="'+escA(id)+'" data-cust-url="'+escA(j.id)+'" placeholder="哪裡不對？寫下要改什麼"></textarea>'+
        '<button class="cust-b" type="button" data-cust-action="accept" data-cust-item="'+escA(id)+'" data-cust-url="'+escA(j.id)+'">收下</button>'+
        '<button class="cust-b" type="button" data-cust-action="reject" data-cust-item="'+escA(id)+'" data-cust-url="'+escA(j.id)+'">退回重寫</button>';
      if(custStale(j,id))html+='<button class="cust-b" type="button" data-cust-open="'+escA(j.id)+'" data-cust-only="'+escA(id)+'">重新客製 / 上傳</button>';
      if(state==='accepted')html+='<button class="cust-b off" type="button" data-cust-action="clear" data-cust-item="'+escA(id)+'" data-cust-url="'+escA(j.id)+'">改回原始檔</button>';
      if(state==='rework'||state==='failed')html+='<button class="cust-b" type="button" data-cust-open="'+escA(j.id)+'" data-cust-only="'+escA(id)+'">重跑這份</button>';
      return html+'</div>';
    }).join('');
    var legacy=f.custom_file&&!Object.keys(docs).some(function(id){return id.indexOf('resume:')===0;});
    if(legacy)rows+='<div class="cust-doc"><span class="cust-st">📎 履歷已收下:'+esc(f.custom_file.split('/').pop())+'</span>'+
      '<button class="cust-b off" type="button" data-cust-action="clear" data-cust-item="resume:legacy" data-cust-url="'+escA(j.id)+'">改回原始檔</button></div>';
    return '<div class="cust-part">'+rows+'<button class="cust-b ask" type="button" data-cust-open="'+escA(j.id)+'">✍️ 要客製 / 上傳自己的客製版</button></div>';
  }

  function sentBtnsHTML(sent,id){
    if(id&&removed(id))return '<div class="fb-btns"><button class="stage-b back" data-rm="0" type="button">↩︎ 放回看板</button></div>';
    return '<div class="fb-btns">'+
      '<button class="fb-b like'+(sent==='like'?' on':'')+'" data-s="like" type="button">👍 喜歡</button>'+
      '<button class="fb-b meh'+(sent==='meh'?' on':'')+'" data-s="meh" type="button">😐 普通</button>'+
      '<button class="fb-b dislike'+(sent==='dislike'?' on':'')+'" data-s="dislike" type="button">👎 不喜歡</button>'+
      '<button class="fb-b grow'+(sent==='grow'?' on':'')+'" data-s="grow" type="button">💪 差一點</button>'+
      '</div>';}   // 出錯了與移除一律走卡片右側那條(errBtnHTML/rmBtnHTML),不在心情列重複一顆
  // 管線階段用的一行事實:死線最要緊,地點和薪只給一眼。
  var _EMPTY=/^(無|未明載|未公開|不公開|未提供|未列|查無|n\/a|none|-)([（(].*)?$/i;
  // 這張能不能進可投遞:不能就回原因,能就回空字串(後台的投遞前把關,下一步的 gate:客製還沒處理完、驗收還沒跑完、
  // 驗收沒過;你按「不對,職缺還在」當下就不擋)。單張那顆按鈕和「全部變成可投遞」共用同一份,不要一邊擋一邊放行
  function shipBlocked(j){return nextOf(j.id).gate||'';}
  // 這張卡現在會寄的那幾份檔的客製紀錄 id(後台算的要寄的檔案)。
  // 換了履歷、語言或附件設定之後,舊的那筆紀錄留著(換回來還在),但它不會寄出去:不擋、也不能收下。
  // 算不出來(後台還沒給、選不出履歷)回 null = 每一筆都算:寧可多擋,不要放行。
  function custCurrent(j){
    var r=shipOf(j); if(!r||!r.resume_id||!(r.files||[]).length)return null;
    return r.files.map(function(f){return f.id;});
  }
  // 驗收的這一條還擋不擋(後台的下一步 holds:soft 不擋;agent 判的關閉,他按過「不對,職缺還在」就不擋)
  function issueHolds(x){var k=JSON.stringify(x); return (nextOf(x.jid).holds||[]).some(function(h){return JSON.stringify(h)===k;});}
  // 擋住這張的是 agent 判的職缺關了:給「不對,職缺還在」
  function judgedClosed(j){return !!nextOf(j.id).closed;}
  // 還沒確認職缺還在不在:不擋,只在卡上提示一行。
  function linkNoteHTML(j){
    var st=D.status, n=((st&&st.issues)||[]).filter(function(x){return x.jid===j.id&&x.soft;});
    return n.length?'<span class="stage-note" title="'+escA(n.map(function(x){return x.msg;}).join('；'))+'">ℹ 還沒確認職缺還在不在</span>':'';
  }
  // 「待你決定」整頁:通過驗收的一次全部推到可投遞。沒過驗收的一張都不動(那是機械檢查,不是他的判斷)。
  function readyPassList(){
    return jobs.filter(function(j){var f=FB[j.id];
      return f&&f.app==='ready'&&!removed(j.id)&&!isBlocked(j)&&!shipBlocked(j);});
  }
  function readyBarHTML(){
    var all=jobs.filter(function(j){var f=FB[j.id];return f&&f.app==='ready'&&!removed(j.id)&&!isBlocked(j);});
    if(!all.length)return '';
    var n=readyPassList().length, stuck=all.length-n;
    return '<div id="readybar" class="prepbar">'+
      '<button class="stage-b adv" type="button" data-readyall="1"'+(n?'':' disabled')+
        ' title="通過投遞前驗收的全部送去「🚀 可以投了」;沒過驗收的一張都不動">'+
        '🚀 全部送去「可以投了」（'+n+' 張）</button>'+
      (stuck?'<span class="prep-st">'+stuck+' 張沒過驗收，留在這裡（原因寫在各張卡上）</span>':
              '<span class="prep-st ok">這一階都過關</span>')+'</div>';
  }
  function readyAllToShip(){
    var pass=readyPassList();
    if(!pass.length){snack('沒有通過驗收的卡可以推'); return;}
    var ids=pass.map(function(j){return j.id;});
    ids.forEach(function(id){FB[id]=FB[id]||{}; FB[id].app='ship'; delete justMarked[id]; touch(id);});
    renderAll(); refreshBar(); scheduleSave(900);
    snack('已把 '+ids.length+' 張送去「🚀 可以投了」',function(){
      ids.forEach(function(id){if(FB[id])FB[id].app='ready'; touch(id);});
      renderAll(); refreshBar(); scheduleSave(900);});
  }
  function cardHTML(j){
    var st=FB[j.id]||{}, sent=st.s||'', note=st.n||'', ap=st.app||'';
    var apbadge=ap?('<span class="app-badge '+ap+'">'+(STAGE_LABEL[ap]||ap)+'</span>'):'';
    // 這顆徽章一律畫出來,沒標就是空的。標下去只換裡面的字,不改版面:
    // 以前沒標不畫,一標就多一個元素,標題那一列重排,底下整排按鈕跟著跳 32px
    // (他的手指還停在原處,第二下就按到別的東西)。寬度固定,四種標籤都放得下。
    var badge='<span class="sent-badge'+(sent?' on '+sent:'')+'">'+
      (sent?(sent==='like'?'👍 喜歡':sent==='meh'?'😐 普通':sent==='grow'?'💪 差一點':sent==='techerr'?'🔧 出錯了':'👎 不喜歡'):'')+'</span>';
    // Teal/Huntr 都把「職缺會被下架」當核心賣點。我們本來就把 JD 摘要留在 card-summaries,
    // 但卡片上沒講,他可能以為連結掛了資料就沒了。
    var dead=isDead(j)?'<span class="deadtag" title="原始職缺頁打不開了,但這張卡的摘要與門檻是當時抓下來留存的">⚠ 原頁失效（本卡內容已留存）</span>':'';
    _curId=j.id;
    // 標題整行就是「去看這個職缺」:一整條可點,行尾補一個 ↗ 當提示。
    function headHTML(withStage){
      var h3='<h3>'+jdTitleHTML(j)+dead+'</h3>';
      return '<div class="card-top">'+h3+badge+(withStage?apbadge:'')+openedBadge(j)+addedBadge(j)+'</div>';}
    var chan='<span class="pill '+pill(j.chan)+'">'+esc(j.chan)+'</span>';
    var note_t='<textarea class="fb-t" rows="1" placeholder="原因(喜歡/普通/不喜歡都可寫)">'+esc(note)+'</textarea>';
    // 履歷回饋:這份履歷哪裡不對。跟上面那格分開,因為那格講的是這個職缺你喜不喜歡,
    // 這格講的是產出來的東西好不好——兩種要分開才讀得出「哪些該回頭修 prompt」。
    var rzfb=(FB[j.id]&&FB[j.id].rzfb)||'';
    // 客製的內容怎麼來的。跟「agent 憑什麼這樣判」分開:那條講的是選哪份履歷的理由,
    // 這條講的是客製版為什麼這樣寫。使用者要看得到才給得出回饋。
    function motiveWhyHTML(j){
      var rz=j.resume||{}, tr=rz.trait||'', ev=rz.ev||'', wr=rz.why||'';
      if(!rz.custom)return '';
      if(!tr&&!ev&&!wr)return '';
      var b='';
      if(tr)b+='<div class="mw-row"><span class="mw-k">這缺在找什麼樣的人</span><span class="mw-v">'+esc(tr)+'</span></div>';
      if(ev)b+='<div class="mw-row"><span class="mw-k">對應的 JD 原句</span><span class="mw-v">'+esc(ev)+'</span></div>';
      if(wr)b+='<div class="mw-row"><span class="mw-k">為什麼這樣客製</span><span class="mw-v">'+esc(wr)+'</span></div>';
      return fold('mwhy:'+j.id,'為什麼這樣客製',b,{cls:'mwhy'});
    }
    var rz_t='<textarea class="rz-fb'+(rzfb?' has':'')+'" rows="1" placeholder="✍️ 這份履歷哪裡不對?(重新準備履歷時 agent 會照著重挑)" title="重新準備履歷時 agent 會照著重挑版本和語言;內容要改用「要客製 / 上傳自己的客製版」">'+esc(rzfb)+'</textarea>';
    var save='<div class="fb-save"><span class="fb-st"></span></div>';  // 只標這張有沒有未存,存檔鈕全站只有底下那一顆
    // 管線階段(正在準備／待你決定／可投遞)要決定的是「這份履歷跟附件對不對、可不可以投」。
    // 為什麼適合你／門檻／彈藥是上游已經做完的判斷,收進「詳細」,要看再點。
    if(inApplyView){
      // 階段徽章在階段分頁裡是廢話(整頁都是同一個階段),不畫,省一列。
      // 每一階先放「這一階要他做的決定」需要的東西,其餘收進「詳細」:
      //  待你決定(直接投還是先客製):決定的依據是「為什麼適合你」,攤開在標題下面。
      //  可投遞(核准送出):代投那一塊放在履歷上面,核准鈕不用捲過履歷切換和附件清單才按得到。
      var fitTop=(ap==='ready'||ap==='prep'||(ap==='ship'&&FLOW.auto_advance))?fitLineHTML(j):'';
      var apply=formLineHTML(j,ap), vdb=verdictBlockHTML(j);
      // 用哪份履歷、哪個語言是「待你決定」那一階的事;到了可投遞、已投遞只要一行看得到寄的是哪份,要改再點開。
      if((ap==='ship'||ap==='sent')&&vdb&&shipOf(j))vdb=fold('vd:'+j.id,vdSumHTML(j),vdb,{cls:'vdfold'});
      return lockLeaving(j.id,'<article class="card compact'+(j.bk?' bk':'')+'" data-fid="'+esc(j.id)+'">'+
        headHTML(false)+factsLine(j)+repostHTML(j)+riskHTML(j)+fitTop+(ap==='ship'?apply+vdb:vdb+apply)+
        // 兩格筆記:「這份履歷哪裡不對」是準備區重跑時 agent 要讀的,只在正在準備、待你決定攤開;
        // 「原因」是當初表態的理由,跟改標記放在一起收進詳細。
        '<div class="fb">'+motiveWhyHTML(j)+stageRowHTML(ap,j)+(fitTop&&ap!=='ship'?rz_t:'')+
        fold('cmore:'+j.id,'詳細（'+(fitTop?'':'適合理由・')+'門檻・彈藥・改標記'+(fitTop?'':'・筆記')+'）',
          '<div class="chrow">'+_tagHTML(j)+chan+'</div>'+_sumHTML(j,!!fitTop)+sentBtnsHTML(sent,j.id)+(fitTop&&ap!=='ship'?'':rz_t)+note_t,{cls:'cmore'})+
        save+'</div></article>');
    }
    return lockLeaving(j.id,'<article class="card'+(j.bk?' bk':'')+'" data-fid="'+esc(j.id)+'">'+
      headHTML(true)+factsLine(j)+srcLineHTML(j)+repostHTML(j)+riskHTML(j)+'<div class="chrow">'+_tagHTML(j)+chan+'</div>'+_sumHTML(j)+verdictBlockHTML(j)+
      '<div class="fb">'+sentBtnsHTML(sent,j.id)+(removed(j.id)?'':stageRowHTML(ap,j))+note_t+save+'</div></article>');
  }
  // 卡歸在哪一家:後台算好跟著職缺送來的(card.label_jobs:別名、職稱字、法律字尾、同一家只有一個寫法)
  function companyOf(j){return (j&&j.co)||'其他';}
  // 公司列不用點開就看得出裡面被標成什麼:一眼知道哪家還沒看過。
  var CODOT=[['like','👍'],['grow','💪'],['meh','😐'],['dislike','👎'],['techerr','🔧'],['prep','📝'],['ready','✅'],['ship','🚀'],['own','📎'],
             ['oc_offer','🎉'],['oc_iv','🗣'],['oc_rej','✗'],['oc_ghost','🔕'],['oc_wd','↩︎']];
  // 收起來的公司先不畫卡片,只記著待會要畫誰。「待評估」有 308 張卡分在 192 家公司底下,
  // 全部畫出來是一萬多個 DOM 節點、三萬多像素的頁面,手機光捲動就卡,而他一次只展開一家。
  var LAZY={};
  function coGroupHTML(cat,co,cc){
    var m={},seen=0;
    // 「這張用自己的檔」跟階段垂直,不能共用 f.app||f.s 那一個 key,分開數。
    cc.forEach(function(j){var f=FB[j.id]||{},k=f.app||f.s; if(k){m[k]=(m[k]||0)+1;seen++;} if(f.custom_file)m.own=(m.own||0)+1;
      if(f.app==='sent'&&f.oc)m['oc_'+f.oc]=(m['oc_'+f.oc]||0)+1;});
    var dots=CODOT.map(function(p){return m[p[0]]?'<span class="codot '+p[0]+'">'+p[1]+m[p[0]]+'</span>':'';}).join('');
    var _k=coKey(cat,co);
    var _op=!!FOLD['co:'+_k];   // 一律預設關著,他自己點才開
    // 收起來的公司列也要看得出新舊:幾筆是七天內的、最新那筆多久前。
    var _fresh=cc.filter(isNew).length;
    var _days=cc.map(function(j){return daysSince(j.added);}).filter(function(x){return x!==null;});
    var _min=_days.length?Math.min.apply(null,_days):null;
    var _age=(_min===null)?'':'<span class="coage">'+(_min<=0?'今天':(_min===1?'昨天':_min+' 天前'))+'</span>';
    var _newdot=_fresh?'<span class="conew">🆕'+_fresh+'</span>':'';
    var body='';
    if(_op) cc.forEach(function(j){body+=cardHTML(j);});
    LAZY[_k]=cc;
    // 公司層級的動作全部收進標題列最右的 ⋯。誤點 ⋯ 只是打開選單,代價是零;
    // 以前那三顆攤在外面(或自己佔一排),誤點「封鎖這家」整家就不見了,而且把卡片往下推一排。
    var acts=moreHTML(
      '<button class="coseed" type="button" data-coseed="'+esc(co)+'">🔎 找這家更多</button>'+
      (coPrepN(cc)?'<button class="coprep" type="button" data-coprep="'+escA(_k)+'">📝 這家全部送去準備履歷中（'+coPrepN(cc)+'）</button>':'')+
      '<button class="coseed" type="button" data-comerge="'+esc(co)+'">🔗 跟別家是同一家…</button>'+
      (coRmList(cc).length?'<button class="coblock" type="button" data-corm="'+escA(_k)+'">🗑 這家全部移除（'+coRmList(cc).length+'）</button>':'')+
      '<button class="coblock" type="button" data-block="'+esc(co)+'">🚫 封鎖這家</button>');
    return fold('co:'+_k,'<span class="coname">'+esc(co)+'</span>'+_newdot+
      (dots?'<span class="codots">'+dots+'</span>':'')+_age+
      '<span class="con">'+cc.length+'</span>'+acts,
      '<div class="cards cocards" data-lk="'+esc(_k)+'">'+body+'</div>',
      {cls:'cogrp',hcls:'cohead'+(seen===cc.length?' done':''),hattr:' data-k="'+esc(_k)+'"'});
  }
  // 公司第一次展開才畫卡片(收著的 LAZY 先記著要畫誰)。點下去那一刻就畫,不等 toggle,
  // 點完馬上看得到卡;程式直接把它打開(toggle)也會走到這裡。
  function fillCo(d){var box=d.querySelector('.cocards'), k=(d.getAttribute('data-fold')||'').slice(3);
    if(!box||box.firstChild||!LAZY[k])return;
    box.innerHTML=LAZY[k].map(function(j){return cardHTML(j);}).join('');
    LAZY[k].forEach(function(j){refreshCard(j.id);}); autogrowAll(box);
  }
  // 公司清單:整頁一個順序,同一家合成一列。找工作頁和管線那幾頁都走這一支。
  // 排序是整頁的事,類別是上面的篩選,版面不替他分段(以前先切類別再各自排,選了「剛進板」
  // 還要滑完某一類的幾十筆才看得到別的類別最新的)。
  // 管線頁也不按類別分段:用哪份履歷看履歷與語言,跟類別無關。
  // 順序只在條件變了才重算:標記會改變排序值(把一家最新的那缺移走,它就從「今天」掉到
  // 「19 天前」),不鎖的話他按一下,正在看的那家就從第 10 列飛到第 108 列。
  var ORD2={};
  // 已投出一頁分四區,同一家公司可能同時在兩區(一張等回音、一張沒錄取):各區的公司列要用各自的 key,
  // 不然展開時畫的是後畫那一區的卡(LAZY 被蓋掉)、開合也連動。其他頁一頁只有一組,照舊用公司名。
  function coCat(sec){return /^sent-/.test(sec||'')?sec:'';}
  function _grouped(list,sec){
    var g=_coOrder(list,sec), out='<div class="cogrid">';
    g.cos.forEach(function(co){out+=coGroupHTML(coCat(sec),co,sortJobs(g.byCo[co]));});
    return out+'</div>';}
  // 公司的順序(畫公司列、「一張一張看」排卡片共用,兩邊看到的順序一樣)
  function _coOrder(list,sec){
    sec=sec||active;
    var sig=[active,activeCat,activeFacet,searchQuery,rangeDays,openDays,sortBy].join('\u0001');
    var byCo={},coOrder=[];
    list.forEach(function(j){var co=companyOf(j);if(!byCo[co]){byCo[co]=[];coOrder.push(co);}byCo[co].push(j);});
    coOrder.sort(function(a,b){
      if((a==='其他')!==(b==='其他'))return a==='其他'?1:-1;
      if(sortBy==='co')return a.localeCompare(b);
      // 排序看「這家最新那筆多久前」,最新的公司排前面
      function newest(co){return Math.min.apply(null,byCo[co].map(function(j){
        var d=daysSince(sortBy==='posted'?j.posted_at:j.added); return d==null?9e9:d;}));}
      function oldest(co){return Math.max.apply(null,byCo[co].map(function(j){
        var d=daysSince(j.posted_at); return d==null?-1:d;}));}
      if(sortBy==='oldest')return oldest(b)-oldest(a)||a.localeCompare(b);
      if(sortBy==='fit'){var fa=Math.max.apply(null,byCo[a].map(fitOf)), fb2=Math.max.apply(null,byCo[b].map(fitOf));
        if(fa!==fb2)return fb2-fa;}
      return newest(a)-newest(b)||byCo[b].length-byCo[a].length||a.localeCompare(b);});
    var fz=ORD2[sec];
    if(fz&&fz.sig===sig){var _p={};fz.cos.forEach(function(c,i){_p[c]=i;});   // 新出現的排在後面
      coOrder.sort(function(a,b){var ia=_p.hasOwnProperty(a)?_p[a]:9e9,ib=_p.hasOwnProperty(b)?_p[b]:9e9;return ia-ib;});}
    ORD2[sec]={sig:sig,cos:coOrder.slice()};
    return {cos:coOrder,byCo:byCo};}
  // 「現在卡在哪」:board_status.py --write 把機械檢查的結果寫進 data.status,
  // 這裡只負責顯示。他打開看板就知道欠什麼,不用來問人。
  function statusHTML(kind){
    var st=D.status; if(!st)return '';
    // 驗收報告是上次重建時寫的:之後移除、搬到別階的卡不算(不然這一頁掛著看不到那張卡的警告)
    var here=function(x){var f=FB[x.jid]||{}; return x.stage===kind&&f.app===kind&&!removed(x.jid);};
    var mine=(st.issues||[]).filter(function(x){return here(x)&&issueHolds(x);});
    var soft=(st.issues||[]).filter(function(x){return here(x)&&!issueHolds(x);});
    var what='履歷頁數、附件檔案與頁數'+(st.checked_links?'、連結':'');
    var note=soft.length?'<div class="stbar ok">ℹ '+soft.length+' 張還沒確認職缺還在不在(不擋)</div>':'';
    // 全過關是「沒事」,佔一整塊等於把好消息當成通知在推。縮成一行小字,細節 title 裡有。
    // 驗收報告沒列問題,不代表這一階的卡都能往下走(例如驗收還沒跑完):照卡上實際擋住的張數講,
    // 不然上面寫「2 張沒過驗收」、下面寫「都過關」,兩句打架
    var held=(kind==='ready'||kind==='ship')?jobs.filter(function(j){var f=FB[j.id]||{};
      return f.app===kind&&!removed(j.id)&&shipBlocked(j);}).length:0;
    if(!mine.length&&held)return '<div class="stbar bad">⚠ '+held+' 張還不能往下走 · 原因寫在各張卡上</div>'+note;
    if(!mine.length)return '<div class="stbar ok" title="'+esc(st.at)+' 查過 '+what+'">✅ 這一階段都過關</div>'+note;
    // 同一個原因卡住 22 張就印 22 行一模一樣的字,等於把唯一的訊息埋在重複裡。
    // 同因歸成一行,只在張數少的時候才列出是哪幾張。
    var by={},order=[];
    mine.forEach(function(x){if(!by[x.msg]){by[x.msg]=[];order.push(x.msg);}by[x.msg].push(x.t);});
    var li=order.map(function(m){var ts=by[m];
      if(ts.length===1)return '<li>'+esc(ts[0])+'<em>'+esc(m)+'</em></li>';
      return '<li><b>'+ts.length+' 張</b><em>'+esc(m)+'</em>'+
        (ts.length<=6?'<span class="stwho">'+ts.map(esc).join('、')+'</span>':'')+'</li>';}).join('');
    // 收成一行:每張卡上本來就寫了自己的原因,這裡是總表。攤開的話手機上一整塊擋在卡片前面。
    return fold('st:'+kind,'<span class="sthd">⚠ '+mine.length+' 張沒過驗收 · 原因寫在各張卡上</span>',
      '<div class="stwhen">'+esc(st.at)+' 查過 '+what+'</div><ul>'+li+'</ul>',{cls:'stbar bad stfold'})+note;
  }
  // 「正在準備」「待你決定」各自獨立分頁,點哪個只看哪塊。
  // ---- 🌱 指名要找的:他在卡片/公司列上點「找類似的」「找這家更多」,先累積成一張清單 ----
  // 不是第四種找法:就是「更深」,範圍由他指定(converge 那邊統一走 deep)。
  // 為什麼累積不是點了就跑:找缺那隻 agent 的固定成本是十幾分鐘,而且一次只開一隻;
  // 分開點本來就會排隊,與其隱形排隊,不如給他一張看得見、可以刪的清單,按一次一起跑。
  // 找缺那隻 agent 要不要放手自己派幾隻。預設關著(他原本的規則:一輪一隻,平行吃不到 cache
  // 又拖垮電腦);他實測「少約束品質反而好」,所以給他自己撥,不由我改他的規則。
  function agentFree(){return !!FB['__agentfree__'];}
  function agentFreeHTML(){
    var on=agentFree();
    return '<div class="pb-more afree"><button class="afree-b'+(on?' on':'')+'" type="button" data-afree="1"'+
      ' title="開:prompt 會加一句「要派幾隻 agent、怎麼找、怎麼判斷都你自己決定」。關:照原本的規矩,一輪只派一隻">'+
      '要派幾隻 agent <b>'+(on?'它自己決定':'只派一隻')+'</b>'+'</button></div>';
  }
  function toggleAgentFree(){
    var on=!agentFree();
    // 關掉要寫 0,不能 delete:存檔只送「還存在的 key」,delete 掉的那一筆永遠送不出去,
    // 伺服器上會一直停在舊值(看起來就是按了開關沒反應)。
    FB['__agentfree__']=on?1:0;
    dirty['__agentfree__']=true; renderFind(); renderAll(); refreshBar(); scheduleSave(900);
    snack(on?'找缺時讓 agent 自己決定要派幾隻':'找缺時只派一隻 agent',toggleAgentFree);
  }
  function seedList(){return Array.isArray(FB['__seeds__'])?FB['__seeds__']:[];}
  function hasSeed(k,v){return seedList().some(function(x){return x.k===k&&x.v===v;});}
  function seedAdd(k,v,label){
    if(hasSeed(k,v)){snack('這個已經在清單裡了'); return;}
    var l=seedList().slice(); l.push({k:k,v:v,t:label||v});
    FB['__seeds__']=l; dirty['__seeds__']=true; renderFind(); renderAll(); refreshBar(); scheduleSave(900);
    snack('加進「指名要找的」('+l.length+' 個),到最上面「🔎 找新職缺」按一次一起跑',function(){
      seedDel(k,v,true);});
  }
  function seedDel(k,v,quiet){
    FB['__seeds__']=seedList().filter(function(x){return !(x.k===k&&x.v===v);});
    dirty['__seeds__']=true; renderFind(); renderAll(); refreshBar(); scheduleSave(900);
    if(!quiet)snack('拿掉了');
  }
  function seedRowsHTML(){
    var l=seedList(); if(!l.length)return '';
    return '<div class="seedbox"><div class="seedhd">🌱 指名要找的 <span class="n">'+l.length+'</span></div>'+
      '<div class="seedlist">'+l.map(function(x){
        return '<div class="seedrow"><span class="seedk">'+(x.k==='co'?'這家更多':'類似這張')+'</span>'+
          '<span class="seedt">'+esc(x.t||x.v)+'</span>'+
          '<button class="seeddel" type="button" data-seeddel="'+escA(x.k+'|'+x.v)+'" aria-label="拿掉">✕</button></div>';
      }).join('')+'</div>'+
      '<button class="stage-b adv" type="button" data-find="seed"'+(FIND.running?' disabled':'')+
        '>▶ 照這 '+l.length+' 個找</button></div>';
  }
  // 封鎖區裡的一家:點開看得到這家每一張缺,兩顆按鈕分開——解除封鎖、真的清掉。
  // 清掉走既有的「🗑 已移除」(那裡一樣放得回來),不另外做一種刪除。
  function blockedGroupHTML(co){
    var cc=coJobs(co).filter(function(j){return !removed(j.id);});
    var body='<div class="cards">'+(cc.length?cc.map(function(j){return cardHTML(j);}).join('')
              :'<p class="applyempty">這家目前沒有還在板上的缺。</p>')+'</div>';
    // 動作跟公司列同一個作法:放在展開後的內容最上面,不擠在那一行(那一行是開合用的)
    var acts=moreHTML('<button class="coseed" type="button" data-unblock="'+esc(co)+'">↩︎ 解除封鎖</button>'+
      (cc.length?('<button class="coblock" type="button" data-blockdel="'+esc(co)+'">🗑 真的清掉這 '+cc.length+' 張</button>'):''));
    return fold('blk:'+coKey('',co),
      '<span class="coname">'+esc(co)+'</span><span class="con">'+cc.length+'</span>'+acts,
      body,{cls:'cogrp blkgrp',hcls:'cohead'});
  }
  function blockDelete(co){
    var cc=coJobs(co).filter(function(j){return !removed(j.id);});
    if(!cc.length){snack('「'+co+'」沒有可以清的缺'); return;}
    var ids=cc.map(function(j){return j.id;});
    ids.forEach(function(id){FB[id]=FB[id]||{}; FB[id].rm=today(); touch(id);});
    renderAll(); refreshBar(); scheduleSave(900);
    snack('已把「'+co+'」的 '+ids.length+' 張收進「🗑 已移除」',function(){
      ids.forEach(function(id){if(FB[id])delete FB[id].rm; touch(id);});
      renderAll(); refreshBar(); scheduleSave(900);});
  }
  // 「🚫 已封鎖 N 家」:封鎖名單(每家點開看得到是哪幾張,可以解除或真的清掉)
  function openBlockList(){
    var bl=FB['__block__']||[];
    if(!bl.length){closeModal(); return;}
    modal('<div class="rzm-box"><button class="rzm-x" type="button">✕ 關閉</button>'+
      '<h2 class="applyhd">🚫 已封鎖的公司 <span class="n">'+bl.length+'</span></h2>'+
      '<p class="applyempty">這幾家的缺不顯示在任何分頁,找缺也不會再挖。解除封鎖就回到原本的分頁。</p>'+
      bl.map(blockedGroupHTML).join('')+'</div>','');
  }
  // 封鎖名單蓋在畫面上(不在 #app 裡):解除、清掉在這裡接,做完名單跟著更新
  document.addEventListener('click',function(e){
    if(!(e.target.closest&&e.target.closest('#rzmodal')))return;
    var ub=e.target.closest('[data-unblock]'), bd=e.target.closest('[data-blockdel]');
    if(ub){e.preventDefault(); toggleBlock(ub.getAttribute('data-unblock')); openBlockList(); return;}
    if(bd){e.preventDefault(); blockDelete(bd.getAttribute('data-blockdel')); openBlockList();}
  });
  function renderRemoved(){
    inApplyView=false;
    var arr=jobs.filter(function(j){return removed(j.id);});
    var bl=FB['__block__']||[];
    // 封鎖不是刪掉:那幾家的缺還在,只是收在這裡。以前這裡只列公司名,缺本身一張都看不到,
    // 誤按就等於整家憑空消失、也沒得檢查按錯沒有。現在一家一組、點開看得到每一張,
    // 要真的清掉是另一顆按鈕(而且是收進「已移除」,那裡也放得回來)。
    // 封鎖的公司不放這裡:封鎖是「公司」,已移除是「卡片」;封鎖名單在篩選列「🚫 已封鎖 N 家」點開看
    var h='<div class="applygrp"><h2 class="applyhd">🗑 已移除 <span class="n">'+arr.length+'</span></h2>'+
      '<p class="applyempty">按「↩︎ 放回看板」就會回到它原本的分頁。</p>'+
      (arr.length?_grouped(arr,'rm'):'<p class="applyempty">沒有移除任何職缺</p>')+'</div>';
    $('app').innerHTML=h; refreshAllCards(); autogrowAll($('app'));
  }
  // 已投遞一頁分四區:Offer、面試中、等回音、已結束。卡在哪一區在他進這一頁時定下來,
  // 在這一頁上改結果只換卡上的字,不會把卡搬到別區(他手指還停在原處);切走再回來才重排。
  var SECSNAP={};
  function secOf(f){var s=f.oc||''; return OC_END[s]?'end':(s||'wait');}
  // 這一頁要你處理的:一條一行,點名字跳到那張卡(打開那家、捲過去、閃一下)。沒事就不畫。
  function todoHTML(kind,arr){
    var rows=[];
    arr.forEach(function(j){var m=FB[j.id]||{}, a=m.apply||{}, f=m.form, why='', btn='';
      if(kind==='ship'){
        // 要不要列、列什麼,跟卡上同一個判斷(cardState);只有頁面還在、叫得回那段對話才給 👀
        var S=cardState(j); if(S.locked||S.busy)return;
        why=S.todo;
        if(why&&S.eye)btn='<button class="ap-undo" type="button" data-livego="'+escA(j.id)+'">👀 看頁面</button>';
      }
      // 已投遞只列對方要他本人動手的事。看懂回音、改狀態是 agent 的事,不丟「看一下」給他。
      if(kind==='sent'){var td=rpTodos(m); if(td.length){why='要你做:'+td.map(function(x){return x.todo;}).join(';');
        btn='<button class="ap-undo" type="button" data-rpdone="'+escA(j.id)+'">做好了</button>';}}
      if(why)rows.push('<li><button class="todo-go" type="button" data-todogo="'+escA(j.id)+'">'+esc(cardName(j))+'</button>'+
        '<span class="todo-why">'+esc(why)+btn+'</span></li>');});
    if(!rows.length)return '';
    // 一次最多攤開三件:十件全攤,手機上一整屏都是它,卡片被推到第二屏後面。其餘收在同一個開合裡。
    var more=rows.length>4?fold('todo:'+kind,'還有 '+(rows.length-3)+' 件','<ul>'+rows.slice(3).join('')+'</ul>',{cls:'todo-more'}):'';
    var aq=kind==='ship'&&ansTodo().length?'<button class="stage-b adv ansq-go" type="button" data-ansq="1">▶ 答案一次確認完('+ansTodo().length+' 條)</button>':'';
    return '<div class="todo"><div class="todo-top"><b class="todo-h">這一頁要你處理的 '+rows.length+' 件</b>'+aq+'</div><ul>'+(more?rows.slice(0,3):rows).join('')+'</ul>'+more+'</div>';
  }
  // 📬 查應徵進度(tools/reply_run.py):查設定的信箱和登記過應徵紀錄頁的平台,程式照回音改狀態,送出太久沒回音記沒下文(天數在設定)。
  var REPLIES={};
  function sentWaitingN(){return jobs.filter(function(j){var f=FB[j.id];return f&&f.app==='sent'&&!removed(j.id)&&
    ['rej','wd'].indexOf(f.oc||'')<0;}).length;}
  // 每條「跑」的那一列:自動流程開著就標出來;開啟之前就在這一階的舊卡要不要也交給它,一顆按鈕
  // 開啟自動流程那一刻就在這一階的舊卡裡,按「之前的 N 張也交給自動」會交出去的那幾張。按鈕的張數跟按下去交的是同一份:
  // 以前按鈕只算沒填過的,按下去卻把這一階全部交出去(頁面不見了的舊卡也被自動重填)
  function autoOld(stage){var skip=(FB['__auto__']||{}).skip||[];
    return jobs.filter(function(j){var f=FB[j.id]||{}; return f.app===stage&&!removed(j.id)&&skip.indexOf(j.id)>=0&&
      !(stage==='ship'&&((f.form||{}).lock||f.apply))&&!(stage==='prep'&&j.prep_note);}).map(function(j){return j.id;});}
  function autoBarHTML(stage){
    var key={prep:'auto_prep',ship:'auto_fill',sent:'replies_at'}[stage]; if(!FLOW[key])return '';
    var A=FB['__auto__']||{}, old=autoOld(stage).length;
    var what={prep:'有新卡就自動準備',ship:'進來就讓 '+AGENT+' 填表,停在送出前',sent:'每天 '+esc(FLOW.replies_at)+' 自動查'}[stage];
    return '<span class="auto-chip" title="⚙ 設定 → 🔁 自動流程可以關">🤖 自動:'+what+'</span>'+
      (A.blocked&&stage!=='prep'?(/Chrome/.test(A.blocked)
        ?'<button class="stage-b" type="button" data-gocfg="cfg:agent" title="⚙ 設定 → 🤖 Agent 與瀏覽器:開設定檔、裝擴充功能、按連接">⚠ 自動停著:'+esc(A.blocked)+' → 去連接</button>'
        :'<span class="prep-st bad">⚠ 自動停著:'+esc(A.blocked)+'</span>'):'')+
      (stage!=='sent'&&old?'<button class="stage-b" type="button" data-autotake="'+stage+'">之前的 '+old+' 張也交給自動</button>':'');
  }
  // 排程的查應徵進度今天沒跑成:自動流程(autopilot.plan)隔一段時間再試,當天最多再試幾次:數字是後台設定送來的(cfg.reply_retry)。
  // 照 __auto__ 記的「今天再試過幾次」照實講會不會再試、幾點;不是排程的那天(他自己按的、排程沒開)不講
  var REPLY_RETRY=CFG.reply_retry||{max:0,gap:0};
  function replyRetryHTML(p){
    var A=FB['__auto__']||{}, t=p.finished_at||p.t0, d=today();
    if(!FLOW.replies_at||!t||A.replies_day!==d)return '';
    function dayOf(x){return x.getFullYear()+'-'+('0'+(x.getMonth()+1)).slice(-2)+'-'+('0'+x.getDate()).slice(-2);}
    if(dayOf(new Date(t*1000))!==d)return '';
    var r=A.replies_retry||{}, n=r.day===d?(+r.n||0):0, next=new Date((t+REPLY_RETRY.gap)*1000);
    if(n<REPLY_RETRY.max&&dayOf(next)===d)
      return '<span class="prep-st rp-retry">🔁 '+('0'+next.getHours()).slice(-2)+':'+('0'+next.getMinutes()).slice(-2)+
        ' 之後自動再試(今天第 '+(n+1)+'/'+REPLY_RETRY.max+' 次)</span>';
    return '<span class="prep-st rp-retry">'+(n>=REPLY_RETRY.max?'今天自動再試 '+n+' 次都沒成,不再試了':'今天來不及再試了')+
      ';明天 '+esc(FLOW.replies_at)+' 照排程再查,要現在查就按「📬 查應徵進度」</span>';
  }
  function repliesBarHTML(){
    var p=REPLIES||{}, n=sentWaitingN(), st='';
    if(p.running)st='<span class="prep-st run">⏳ '+AGENT+' 正在查應徵進度'+(p.n?'('+p.n+' 張)':'')+'　· 已 '+minsOf(p)+' 分鐘</span>';
    else if(p.phase==='done')st='<span class="prep-st ok">上一輪 '+hhmm(p.finished_at)+' 查完:'+esc(p.msg||'沒有新回音')+'</span>';
    // 部分完成(有來源進不去、有卡這輪沒查完)、沒跑成都要講,附「看紀錄」;📣 回報叫他按的就是這一顆
    else if(p.phase==='incomplete')st='<span class="prep-st bad">⚠ 上一輪 '+hhmm(p.finished_at)+' 部分完成:'+esc(p.msg||'有卡這一輪沒查完')+'</span>'+'<button class="cfg-b" type="button" data-showlog="replies">看紀錄</button>';
    else st=phaseSt(p,'replies');
    return '<div id="replybar" class="prepbar'+(p.running?' run':'')+'">'+
      (p.running?'':runNHTML('replies',n))+
      '<button class="stage-b adv" data-replyrun="1" type="button"'+((p.running||!n)?' disabled':'')+
      ' title="查你設定的信箱和登記過應徵紀錄頁的平台,照回音改狀態(可以復原);送出後太久沒回音的記成沒下文">'+
      (p.running?AGENT+' 查應徵進度中…':'📬 查應徵進度('+runNOf('replies',n)+' 張)')+'</button>'+st+autoBarHTML('sent')+runCtlHTML('replies',p)+pvBtn('replies')+'</div>';
  }
  function refreshRepliesUI(){var b=$('replybar'); if(b){var x=document.createElement('div'); x.innerHTML=repliesBarHTML(); b.replaceWith(x.firstElementChild);}}
  // 卡上的回音:agent 自動改了狀態就講依據、可以復原;回音一則一行,點開看原文連結。
  var RP_KIND={confirm:'已收到申請',reject:'沒錄取',interview:'面試/測驗邀請',offer:'錄取',needinfo:'其他',other:'其他'};
  // 結案了(沒錄取、沒下文、我不去了)對方要的事也不用做了,不再掛「要你做」;按復原回到等回音、面試,待辦跟著回來
  function rpMaybe(f){return ((f.replies||{}).items||[]).filter(function(x){return x.maybe&&x.maybe.length&&!x.maybe_ok;});}
  function rpTodos(f){if(OC_END[f.oc])return []; return ((f.replies||{}).items||[]).filter(function(x){return x.todo&&!x.done;});}
  function replyLineHTML(id,f){
    var r=f.replies||{}, items=(r.items||[]).slice().sort(function(a,b){return String(b.date).localeCompare(String(a.date));}), h='';
    var checkedAt=r.at;
    if(checkedAt)h+='<div class="fm-line rp-checked">📅 '+esc(mdOf(checkedAt))+' 查過'+
      (items.length?'':'，沒有回音')+'</div>';
    var oa=f.oc_auto, by=oa&&(r.items||[]).filter(function(x){return x.id===oa.by;})[0];
    // 信算哪一種是 agent 判斷(程式核對不了):標明、附它抄的那一句原文(程式核對過在信裡);
    // 程式沒讀到原文的(agent 補查的來源)照實講。程式自己記的沒下文不是 agent 判斷
    if(oa)h+='<div class="fm-line rp-auto">🤖 '+esc(mdOf(oa.at))+(oa.s==='ghost'?' 照':' agent 判斷,照')+
      // 有它抄的那一句就用那一句;舊的回音沒有,用標題、摘要開頭,再沒有就用種類
      (oa.s==='ghost'?'「'+esc(oa.by||'送出太久沒回音')+'」':by?'「'+esc(by.quote||by.subject||String(by.snippet||'').slice(0,40)||RP_KIND[by.kind]||'')+'」':'回音')+
      '改成「'+esc(ocLabel(oa.s))+'」'+(by&&by.unverified?'(agent 補查的來源,程式沒讀到原文)':'')+
      '<button class="ap-undo" type="button" data-ocundo="'+escA(id)+'">不對,復原</button></div>';
    // 同一封信也對到別張卡(reply_run 分不出是哪一張,沒自動改):附原文讓他自己看、自己按結果
    rpMaybe(f).forEach(function(x){
      h+='<div class="fm-line rp-maybe">❓ 可能是這封:'+esc(RP_KIND[x.kind]||x.kind||'')+' '+esc(mmdd(x.date))+' · '+
        (x.link?'<a href="'+escA(safeUrl(x.link))+'" target="_blank" rel="noopener">'+esc(x.subject||String(x.snippet||'').slice(0,40)||'打開原文')+'</a>':esc(x.subject||x.snippet||''))+
        '<span class="n">　同一封也對到「'+esc(x.maybe.map(function(u){return cardName(jobOf(u)||u);}).join('」「'))+'」,分不出是哪一張,沒自動改;是這張就在下面按結果</span>'+
        '<button class="ap-undo" type="button" data-rpmaybe="'+escA(id)+'">知道了</button></div>';});
    var td=rpTodos(f);
    if(td.length)h+='<div class="fm-line"><span class="ap-bad">✋ 要你做:'+esc(td.map(function(x){return x.todo;}).join(';'))+'</span>'+
      '<button class="ap-undo" type="button" data-rpdone="'+escA(id)+'">做好了</button></div>';
    if(items.length)h+=fold('rp:'+id,'📬 回音 '+items.length+' 則　最新:'+esc(RP_KIND[items[0].kind]||'')+' '+esc(mmdd(items[0].date))+
      (r.at?'<span class="n">　'+esc(mmdd(r.at))+' 查的</span>':''),
      '<ul class="rp-list">'+items.map(function(x){return '<li><span class="rp-k">'+esc(RP_KIND[x.kind]||x.kind||'')+'</span>'+
        esc(mmdd(x.date))+' · '+esc(x.src||'')+' · '+(x.link?'<a href="'+escA(safeUrl(x.link))+'" target="_blank" rel="noopener">'+esc(x.subject||'打開')+'</a>':esc(x.subject||''))+
        (x.snippet?'<div class="rp-snip">'+esc(x.snippet)+'</div>':'')+
        (x.kind==='reject'&&x.reason?'<div class="rp-reason"><b>拒絕理由原文：</b>'+esc(x.reason)+'</div>':'')+'</li>';}).join('')+'</ul>',{cls:'rp-d'});
    return h;
  }
  function renderSent(arr){
    var SEC=[['offer','🎉 Offer'],['iv','🗣 面試中'],['wait','⏳ 等回音'],['end','🗂 已結束']];
    var by={offer:[],iv:[],wait:[],end:[]};
    arr.forEach(function(j){var k=SECSNAP[j.id]||secOf(FB[j.id]||{}); SECSNAP[j.id]=k; by[k].push(j);});
    var h=repliesBarHTML()+todoHTML('sent',arr)+funnelHTML();
    SEC.forEach(function(s){var list=by[s[0]]; if(!list.length)return;
      h+='<div class="applygrp"><h2 class="applyhd">'+s[1]+' <span class="n">'+list.length+'</span></h2>'+
         _grouped(list,'sent-'+s[0])+'</div>';});
    if(!arr.length)h+='<div class="applygrp"><h2 class="applyhd">'+STAGE_LABEL.sent+' <span class="n">0</span></h2>'+
      '<p class="applyempty">還沒有投出去的缺。</p></div>';
    $('app').innerHTML=h; refreshAllCards(); autogrowAll($('app'));
  }
  // 成效:投出去的有多少有回音、走到面試、拿到 offer,再按「用了什麼做法」拆開看。
  // 只拆系統自己決定的東西(哪一份履歷、哪個語言、通用或客製版)跟職缺類別。
  // 樣本小,一律寫幾張裡有幾張,不只給百分比。
  function funnelHTML(){
    var sent=jobs.filter(function(j){var f=FB[j.id];return f&&f.app==='sent'&&!removed(j.id);});
    if(!sent.length)return '';
    function tally(list){var t={n:list.length,reply:0,iv:0,offer:0,wait:0};
      list.forEach(function(j){var f=FB[j.id]||{};
        if(replied(f))t.reply++; if(reachedIv(f))t.iv++; if(reachedOffer(f))t.offer++; if(!f.oc)t.wait++;});
      return t;}
    var all=tally(sent), pct=Math.round(all.reply*100/all.n);
    // 投出到第一個回音(面試、offer 或沒錄取)隔了幾天,取中位數
    var days=[];
    sent.forEach(function(j){var f=FB[j.id]||{}, a=f.oc_at||{};
      var first=[a.iv,a.offer,a.rej].filter(Boolean).sort()[0];
      if(first&&f.sent_at){var d=Math.round((Date.parse(first+'T00:00:00')-Date.parse(f.sent_at+'T00:00:00'))/86400000);
        if(d>=0)days.push(d);}});
    days.sort(function(a,b){return a-b;});
    var DIMS=[
      // 已投出的卡照記下的實際寄出的那一份(後台算的);沒挑過履歷的算「不明」
      ['履歷',function(j){var r=shipOf(j);return r&&r.resume_id?(r.resume_name||resumeName(r.resume_id)):'不明';}],
      ['語言',function(j){var p=pickOf(j);return p?langLabel(p.lang):'不明';}],
      ['履歷檔',function(j){return (FB[j.id]||{}).custom_file?'這張自己的檔':'原始履歷檔';}],
      ['類別',function(j){return catOf(j)||'其他';}],
      ['來源平台',function(j){return j.src_plat||'不明';}]];   // 後台算的(card.platform)
    var rows=DIMS.map(function(d){
      var g={},order=[];
      sent.forEach(function(j){var k=d[1](j); if(!g[k]){g[k]=[];order.push(k);} g[k].push(j);});
      order.sort(function(a,b){return g[b].length-g[a].length;});
      return '<tr class="fn-g"><td colspan="5">'+d[0]+'</td></tr>'+order.map(function(k){var t=tally(g[k]);
        return '<tr><td>'+esc(k)+'</td><td>'+t.n+'</td><td>'+t.reply+'</td><td>'+t.iv+'</td><td>'+t.offer+'</td></tr>';}).join('');
    }).join('');
    var reasons=[];
    sent.forEach(function(j){var items=((FB[j.id]||{}).replies||{}).items||[];
      items.forEach(function(x){if(x.kind==='reject'&&x.reason)reasons.push(
        '<li><strong>'+esc(cardName(j))+' · '+esc(x.date||'日期不明')+'</strong><div>'+esc(x.reason)+'</div>'+
        (x.link?'<a class="fn-source" href="'+escA(safeUrl(x.link))+'" target="_blank" rel="noopener noreferrer">查看原文</a>':'')+
        '</li>');});
    });
    var reasonHTML=reasons.length?'<section class="fn-reasons"><h4>有理由的拒絕</h4><ul>'+reasons.join('')+'</ul></section>':'';
    return fold('funnel','📊 投出 <b>'+all.n+'</b> · 有回音 <b>'+all.reply+'</b>（'+pct+'%）· 面試 <b>'+all.iv+
        '</b> · Offer <b>'+all.offer+'</b> · 還在等 <b>'+all.wait+'</b>',
      '<div class="fn-body"><table class="fn-t"><thead><tr><th></th><th>投出</th><th>有回音</th><th>面試</th><th>Offer</th></tr></thead>'+
        '<tbody>'+rows+'</tbody></table>'+
        (days.length?'<p class="fn-note">有回音的,投出後中位數 '+days[Math.floor((days.length-1)/2)]+' 天回（'+days.length+' 筆）。</p>':'')+reasonHTML+
      '</div>',{id:'funnel',cls:'applygrp funnel',hcls:'fn-sum'});
  }
  function renderApply(kind){
    inApplyView=true;
    // 封鎖一家不動已投出的卡:投都投了、面試可能還在跑,留著當紀錄(分頁數字、成效、面試準備也都照算它)
    var arr=jobs.filter(function(j){return FB[j.id]&&FB[j.id].app===kind&&!removed(j.id)&&(kind==='sent'||!isBlocked(j));});
    if(kind==='sent'){renderSent(arr);return;}
    var t=STAGE_LABEL[kind]||kind;
    // 流水線那段說明他早就知道了,每次進來先吃掉一屏。留在 title,滑到才看得到。
    var EMPTY_HINT={prep:'還沒有卡在準備。去找工作那頁,在想做的缺按「📝 送去準備履歷中」。',
      ready:FLOW.auto_advance?'沒有卡住的卡。準備好的卡驗收一過就自動進「可以投了」。':'還沒有卡在這一階。「準備履歷中」的缺產出履歷後會走到這裡。',
      ship:FLOW.auto_advance?'目前沒有可以投的卡。準備好的卡驗收一過就會自動進來。':'目前沒有可以投的卡。「待你決定」的缺通過投遞前驗收、你再按「可以投了」才會進來。'};
    // 0 張的時候先講清楚狀況,不要先畫「這一階段都過關」那種會誤導的驗收摘要。
    var h=arr.length?statusHTML(kind):'', mh='';
    // 原始履歷各版本的預覽不放在流程分頁:這些分頁在處理卡片,看原始履歷在「設定 → 你的履歷」(每份都有 👁 預覽)。
    // 答案庫只在「可投遞」這一階出現:表單就是在這一階填的,別的階段擺著是雜訊。
    if(kind==='prep')h=prepBarHTML()+h;
    if(kind==='ready')h=readyBarHTML()+h;
    // 可投遞:跑的按鈕 → 要你處理的 → 答案庫(「去答案庫看」會跳到這裡)→ 驗收總表 → 卡片
    if(kind==='ship')h=applyBarHTML()+todoHTML('ship',arr)+ansSectionHTML()+h;
    var cl=(kind==='ship'||kind==='ready')?closedIn(kind).length:0;
    if(cl)h+='<div class="rvbar"><span class="rvbar-t">驗收查到 <b>'+cl+'</b> 張已經下架,投不了了</span><button class="stage-b rv-risk" type="button" data-closedrm="'+kind+'">🗑 收掉這 '+cl+' 張</button></div>';
    h+='<div class="applygrp"><h2 class="applyhd">'+t+' <span class="n">'+arr.length+'</span></h2>'+
      (arr.length?_grouped(arr,kind):'<p class="applyempty">'+esc(EMPTY_HINT[kind]||'還沒有')+'</p>')+'</div>'+mh;
    $('app').innerHTML=h; refreshAllCards(); autogrowAll($('app'));
    ansApplyFilter();
  }

  // 篩選控制項長一樣、排同一列、永遠看得到(找工作區那四個、面試準備那兩個都是它)。
  // 按下去開自己畫的選單,不用自己做開合狀態(以前那個「點一下才展開的面板」就是多出來的狀態)。
  function sel(name,label,items,cur){
    var now=items.filter(function(o){return String(o[0])===String(cur);})[0]||items[0];
    CTL_ITEMS[name]=items;
    return '<button type="button" class="ctl" data-ctl="'+name+'">'+
      '<span class="ctl-k">'+label+'</span>'+
      '<span class="ctl-v">'+esc(now[1])+(now[2]===undefined||now[2]===''?'':'（'+now[2]+'）')+'</span>'+
      '<span class="ctl-caret">▾</span></button>';
  }

  // ---- 🎤 面試準備:面試題庫(外部程式把題庫寫進看板資料的 bank,格式見 docs/interview-bank.md) ----
  // 投遞流程的最後一站。面試準備本身也是一條流水線,跟題庫檔頭〈面試進行方式〉一樣:
  // 還沒答 → 答過第一輪(照錄原話)→ 磨合中 → 定案(上場照念)。一站一列、預設收起,點開那一站才列題目,
  // 站裡再照「這題在問你什麼」分(自我介紹、你這個人、職涯方向與動機、你怎麼做事、你做過的事一件一區…)。
  // 題目一律通用;實際被哪家考過的旁邊標那家(<!--考過--> 標記),不猜形式(錄影/真人)也不把題目分給哪一家。
  // 畫面不顯示內部題號(使用者看不懂的代號不上畫面)。
  // 點開一題是要唸的逐字稿,題組有子題按鈕;(b) 原話、(d) 磨稿筆記不上來(「現場要立刻處理的東西必須是 100% 有用資訊」)。
  var BANK=D.bank||null, ivQuery='', IVPICK={}, _ivHay={};
  function ivLive(f){return !!(f&&f.app==='sent'&&(f.oc==='iv'||f.oc==='offer'));}
  function ivItem(id){var l=(BANK&&BANK.items)||[];for(var i=0;i<l.length;i++)if(l[i].id===id)return l[i];return null;}
  // 這一步只接上一步做完的:已投遞、而且他在那張卡上記成「🗣 面試中」或「🎉 Offer」的公司才會出現。
  // 題庫裡有沒有它的專屬題不算數。以前會把「有專屬題的」也列進來,還在這一頁放一顆回頭改結果的按鈕,
  // 他的話:「理論上要上步驟處理完才會跳這步,你等於是幫這職缺打了個專屬補丁」。
  function ivHas(id){return ivLive(FB[id])&&!removed(id);}
  function ivJobIds(){return jobs.filter(function(j){return ivHas(j.id);}).map(function(j){return j.id;});}
  function ivPlain(h){return String(h||'').replace(/<[^>]+>/g,'').replace(/&nbsp;/g,' ').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');}
  // 長度:一段裡有中日韓文字,就照原本的算法數非空白字元,每秒 BANK.cps 個(預設 4.2;使用者的語速是照這個算法量的,
  // 不能改);整段沒有中日韓文字(英文等用空白分字的語言)才數單字,每秒 BANK.wps 個(預設 2.5,約每分鐘 150 字)。
  // 以前英文稿也數字元,一個字母算一個字,時間估成好幾倍。
  var IV_CJK=/[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]/;
  function ivLen(s){s=String(s);
    return IV_CJK.test(s)?{c:s.replace(/\s/g,'').length,w:0}:{c:0,w:(s.match(/\S+/g)||[]).length};}
  function ivAdd(a,b){return {c:a.c+b.c, w:a.w+b.w};}
  function ivLenText(n){return [n.c?n.c+' 字':'', n.w?n.w+' words':''].filter(Boolean).join(' + ')||'0 字';}
  function ivClock(sec){sec=Math.round(sec);var m=Math.floor(sec/60),s=sec%60;return m+':'+(s<10?'0':'')+s;}
  function ivSecs(n){return n.c/((BANK&&BANK.cps)||4.2)+n.w/((BANK&&BANK.wps)||2.5);}
  function ivPicked(it){var k=IVPICK[it.id]; return (k!=null&&k>=0&&it.picks[k])?it.picks[k][1]:null;}
  function ivScripts(it){return (it.b||[]).filter(function(b){return b[0]==='s';});}
  // 只有一段逐字稿、或選了子題,才給一個總長;好幾段的(60/90/180 三版、A/B 版)每段自己標,
  // 那多半是擇一唸,加起來沒有意義。題面寫了限時(AI 錄影每題都有)就並排標出來,念起來超過就標紅:
  // 限時題的稿子太長,常常是到了錄影當下才發現要臨時砍一段。
  function ivDurInfo(it){var keep=ivPicked(it), sc=ivScripts(it), n={c:0,w:0}, k=0, lim=it.lim||0;
    if(!sc.length)return {t:lim?'限 '+ivClock(lim):'',over:false};
    if(!keep&&sc.length>1)return {t:sc.length+' 段'+(lim?'／限 '+ivClock(lim):''),over:false};
    sc.forEach(function(b){b[1].forEach(function(p){k++; if(!keep||keep.indexOf(k)>=0)n=ivAdd(n,ivLen(p));});});
    var s=ivSecs(n);
    return {t:'≈'+ivClock(s)+(lim?'／'+ivClock(lim):''),over:!!lim&&Math.round(s)>lim};}
  function ivDurHTML(it,cls){var d=ivDurInfo(it);
    return d.t?'<span class="'+cls+(d.over?' over':'')+'" data-ivdur="1"'+(d.over?' title="照你的語速念會超過限時"':'')+'>'+d.t+'</span>':'';}
  // 逐字稿段落跨圍欄連續編號,PICK 的段落編號就是這個。
  function ivBodyHTML(it){
    var keep=ivPicked(it), multi=ivScripts(it).length>1, n=0, h='';
    (it.b||[]).forEach(function(b){
      if(b[0]==='h'){h+='<div class="iv-h">'+b[1]+'</div>';return;}
      var len={c:0,w:0}, ps='';
      b[1].forEach(function(p){n++; var on=!keep||keep.indexOf(n)>=0; if(on)len=ivAdd(len,ivLen(p));
        ps+='<p class="iv-seg'+(on?'':' off')+'" data-n="'+n+'">'+esc(p)+'</p>';});
      h+='<div class="iv-script">'+(multi?'<div class="iv-bd">≈'+ivClock(ivSecs(len))+' · '+ivLenText(len)+'</div>':'')+ps+'</div>';
    });
    return h;}
  function ivPickHTML(it){if(!it.picks||!it.picks.length)return '';
    var k=IVPICK[it.id]; if(k==null)k=-1;
    return '<div class="iv-pick">'+it.picks.map(function(p,i){
      return '<button type="button" class="iv-pk'+(k===i?' on':'')+'" data-ivpk="'+i+'">'+esc(p[0])+'</button>';}).join('')+
      '<button type="button" class="iv-pk'+(k<0?' on':'')+'" data-ivpk="-1">全部</button></div>';}
  // 搜尋:題目、題面、考點、涵蓋、子題、逐字稿都算。這一頁的搜尋字跟找工作區那個分開記。
  function ivHay(it){if(_ivHay[it.id])return _ivHay[it.id];
    var s=[it.t,it.cat,it.sub,ivPlain(it.ask),ivPlain(it.focus),it.covers].concat((it.picks||[]).map(function(p){return p[0];}));
    (it.b||[]).forEach(function(b){s.push(b[0]==='s'?b[1].join(' '):ivPlain(b[1]));});
    var raw=s.join(' ').replace(/\s+/g,' ');
    return (_ivHay[it.id]={raw:raw,low:raw.toLowerCase()});}
  // 命中的地方不在題目上,就在題目下面帶一小段上下文,不用一題一題點開找
  function ivHitHTML(it){var q=ivQuery; if(!q||it.t.toLowerCase().indexOf(q)>=0)return '';
    var hy=ivHay(it), j=hy.low.indexOf(q); if(j<0)return '';
    var a=Math.max(0,j-12), b=Math.min(hy.raw.length,j+q.length+18);
    return '<span class="iv-hit">'+(a?'…':'')+esc(hy.raw.slice(a,j))+'<b>'+esc(hy.raw.slice(j,j+q.length))+'</b>'+
      esc(hy.raw.slice(j+q.length,b))+(b<hy.raw.length?'…':'')+'</span>';}
  // 每一題走到哪一站由題庫內容決定(有沒有原話、稿子標了定案沒),不是另外記的:[代號, 短名, 站名]
  var IV_STAGES=[['new','還沒答','還沒答'],['first','第一輪','答過第一輪，等磨'],['wip','磨合中','磨合中'],['final','定案','定案，可以上場']];
  function ivStage(it){return it.st==='ok'?'final':it.st==='wip'?'wip':(it.raw?'first':'new');}
  function ivStageShort(k){for(var i=0;i<IV_STAGES.length;i++)if(IV_STAGES[i][0]===k)return IV_STAGES[i][1];return k;}
  function ivRowHTML(it){
    var stg=ivStage(it);
    // 收起來一行:題目,右邊只放會影響他怎麼用的:哪家實際考過、多長(搜尋時多標在哪一站)
    var sum='<span class="iv-t">'+esc(it.t)+ivHitHTML(it)+'</span>'+
      '<span class="iv-meta">'+(it.asked||[]).map(function(a){return '<span class="iv-jc" title="'+escA(a)+' 實際考過">'+esc(a)+'</span>';}).join('')+
      (ivQuery?'<span class="iv-st">'+esc(ivStageShort(stg))+'</span>':'')+ivDurHTML(it,'iv-dur')+'</span>';
    var ask=it.ask?'<div class="iv-ask">'+it.ask+'</div>':'';
    var focus=it.focus?'<div class="iv-focus"><b>考點</b> '+it.focus+'</div>':'';
    var body;
    if(it.st==='todo'){
      body=ask+focus+(it.covers?'<div class="iv-focus"><b>涵蓋</b> '+esc(it.covers)+'</div>':'')+
        '<p class="iv-empty">'+(stg==='first'?'第一輪答過了。想磨成定案，按「✏️ 編輯」把逐字稿寫進來。'
          :'還沒答過。按「✏️ 編輯」寫逐字稿。')+'</p>'+
        '<div class="iv-acts"><button type="button" class="iv-read" data-ivedit="'+escA(it.id)+'">✏️ 編輯</button></div>';
    } else {
      // 題面跟題目一樣(「學經歷自我介紹。」)就不再摺一次
      var same=ivPlain(it.ask).replace(/[。？?！!\s]/g,'')===it.t.replace(/[。？?！!\s]/g,'');
      if(same)ask='';
      body=ivPickHTML(it)+'<div class="iv-say">'+ivBodyHTML(it)+'</div>'+
        '<div class="iv-acts"><button type="button" class="iv-read" data-ivread="1">📖 大字讀稿</button>'+
        '<button type="button" class="iv-read" data-ivedit="'+escA(it.id)+'">✏️ 編輯</button></div>'+
        (ask||focus?fold('ivq:'+it.id,'題目原文與考點','<div class="iv-q">'+ask+focus+'</div>',{cls:'iv-qf'}):'');
    }
    return fold('iv:'+it.id,sum,'<div class="iv-body">'+body+'</div>',{cls:'ivrow',attr:' data-iv="'+escA(it.id)+'"'});
  }
  // 在面試的公司列在最上面:只講狀態、點標題開職缺原頁,不放改結果的按鈕(結果在已投遞那張卡上記)
  function ivJobStripHTML(id){
    var j=jobOf(id), f=FB[id]||{}, at=f.oc_at||{}, o=OC.filter(function(x){return x[0]===(f.oc||'');})[0];
    var st='📮 已投出'+(f.sent_at?' '+mmdd(f.sent_at):'')+(o&&f.oc?' · '+o[1]+' '+o[2]+(at[f.oc]?'('+mmdd(at[f.oc])+' 起)':''):'');
    return '<div class="iv-job"><div class="iv-job-t">'+(j?jdTitleHTML(j):esc(id))+'</div>'+
      '<div class="iv-job-st"><span>'+st+'</span></div></div>';}
  function ivListHTML(list){return '<div class="ivlist">'+list.map(ivRowHTML).join('')+'</div>';}
  // 一站裡照「在問你什麼」排:大類順序照 bank.cats,小類(你做過的事一件一區)照題庫裡出現的順序
  function ivGroupedHTML(list){var cats=BANK.cats||['其他'], out='';
    cats.forEach(function(c){var l=list.filter(function(it){return (it.cat||'其他')===c;}); if(!l.length)return;
      var subs=[], by={}; l.forEach(function(it){var x=it.sub||''; if(!by[x]){by[x]=[];subs.push(x);} by[x].push(it);});
      subs.forEach(function(x){out+='<div class="iv-co">'+esc(c)+(x?' · '+esc(x):'')+'</div>'+ivListHTML(by[x]);});});
    return out;}
  function renderIv(){
    inApplyView=true;
    if(!BANK||!(BANK.items||[]).length){
      $('app').innerHTML='<div class="emptytab">題庫還是空的。把你被問過、或覺得會被問的題目加進來,'+
        '寫好逐字稿、磨到定案,面試前在這裡照著唸。<br><button class="ctl-act" type="button" data-ivnew="1">＋ 新增第一題</button></div>';
      return;}
    var items=BANK.items, q=ivQuery, h='<div class="ctlbar"><button class="ctl-act" type="button" data-ivnew="1">＋ 新增題目</button></div>';
    // 題目都是通用的。某一家的研究(JD 重點、落差)放別的地方,不上這一頁。
    h+=ivJobIds().map(ivJobStripHTML).join('');
    if(q)h+='<div class="ctlbar"><button class="ctl-act" data-ivclr="1">✕ 清掉搜尋「'+esc(q)+'」</button></div>';
    if(q){
      // 搜尋:不分站一次列出來,每題標它在哪一站
      var hits=items.filter(function(it){return ivHay(it).low.indexOf(q)>=0;});
      h+=hits.length?'<div class="applygrp"><h2 class="applyhd">搜尋結果 <span class="n">'+hits.length+'</span></h2>'+ivGroupedHTML(hits)+'</div>'
        :'<div class="emptytab">沒有題目或答案提到「'+esc(q)+'」。<br><button class="rchip clr" data-ivclr="1">清掉搜尋</button></div>';
    } else {
      // 流水線:一站一列、預設收起,一眼看到每一站有幾題;點開那一站才列題目
      IV_STAGES.forEach(function(st,i){var list=items.filter(function(it){return ivStage(it)===st[0];});
        h+=fold('ivst:'+st[0],'<span class="ivst-n">'+(i+1)+'</span>'+esc(st[2])+' <span class="n">'+list.length+'</span>',
          list.length?ivGroupedHTML(list):'<p class="applyempty">這一站現在沒有題目。</p>',
          {cls:'applygrp ivstage',hcls:'applyhd',attr:' data-ivst="'+st[0]+'"'});});
    }
    $('app').innerHTML=h;
  }
  // 搜尋框只有頁首那一個:在這一頁打的字只搜題庫,切回找工作區換回那邊的字
  function syncQ(){var qi=$('q'); if(!qi)return; var want=active==='iv'?ivQuery:searchQuery;
    if((qi.value||'').trim().toLowerCase()!==want)qi.value=want;
    qi.placeholder=active==='iv'?'🔍 搜題目／答案…':'🔍 搜職缺／公司／理由…';}
  // 大字讀稿:錄影、視訊時照著唸。手動捲(他不要會自己滾的提詞機),字級自己調,
  // 碼錶看自己講了多久;題組選了子題就只放那一問的段落。字級記在這台裝置。
  var RD={fs:22,on:false,t0:0,acc:0,tick:null};
  try{RD.fs=parseInt(localStorage.getItem('iv_fs')||'22',10)||22;}catch(e){}
  function rdTime(){var el=document.querySelector('#rzmodal .rd-tm'); if(!el)return;
    el.textContent=(RD.on?'⏸ ':'⏱ ')+ivClock(Math.floor((RD.acc+(RD.on?Date.now()-RD.t0:0))/1000));}
  function rdStop(){if(RD.on){RD.acc+=Date.now()-RD.t0;RD.on=false;} clearInterval(RD.tick); RD.tick=null;}
  function openRead(id){var it=ivItem(id); if(!it)return;
    rdStop(); RD.acc=0;
    var k=IVPICK[id], sub=(k!=null&&k>=0&&it.picks[k])?it.picks[k][0]:'';
    modal('<div class="rzm-box rd-box"><div class="rd-bar">'+
      '<button class="rzm-x" type="button">✕ 關閉</button>'+
      '<span class="rd-t">'+esc(sub||it.t)+'</span>'+
      '<span class="rd-tools"><button type="button" class="rd-b" data-rdfs="-2" aria-label="字小一點">A−</button>'+
      '<button type="button" class="rd-b" data-rdfs="2" aria-label="字大一點">A＋</button>'+
      '<button type="button" class="rd-b rd-tm" data-rdtm="1">⏱ 0:00</button>'+
      ivDurHTML(it,'rd-est')+'</span></div>'+
      '<div class="rd-doc" style="font-size:'+RD.fs+'px">'+ivBodyHTML(it)+'</div></div>','rd');}
  document.addEventListener('click',function(e){
    var fs=e.target.closest('#rzmodal [data-rdfs]');
    if(fs){RD.fs=Math.max(16,Math.min(40,RD.fs+parseInt(fs.getAttribute('data-rdfs'),10)));
      try{localStorage.setItem('iv_fs',String(RD.fs));}catch(e2){}
      var d=document.querySelector('#rzmodal .rd-doc'); if(d)d.style.fontSize=RD.fs+'px'; return;}
    if(e.target.closest('#rzmodal [data-rdtm]')){
      if(RD.on)rdStop(); else {RD.t0=Date.now(); RD.on=true; RD.tick=setInterval(rdTime,250);}
      rdTime();}
  });

  // 每一頁只放那一階段該注意的東西。
  // 找新職缺只在找職缺那幾頁;履歷另外給「正在準備」(跑準備區照它判);回報只列跟這一頁有關的。
  var DISCOVER=['none','like','meh','dislike','grow','all'];
  function scopeTop(){
    var d=DISCOVER.indexOf(active)>=0, fb=$('findbar'), cb=$('cutbar');
    if(fb)fb.style.display=d?'':'none';
    if(cb)cb.style.display=(active==='prep')?'':'none';
    renderInbox();
  }
  function renderApp(){
    inApplyView=false;
    scopeTop();
    syncQ();
    if(active!=='cfg')CFGAWAY=true;   // 看過別的分頁:回設定頁時重讀(renderCfg)
    if(active==='iv'){renderIv();return;}
    if(active==='rm'){renderRemoved();return;}
    if(active==='cfg'){renderCfg();return;}
    if(STAGES.indexOf(active)>=0){renderApply(active);return;}
    if(REVIEW){renderReview();return;}
    var base=jobs.filter(function(j){if(removed(j.id)||isBlocked(j))return false;
      if(justMarked[j.id])return true;   // 剛標的先留著,讓他在同一張卡上接著做下一步
      if(!inRange(j))return false; if(!inOpen(j))return false; if(FB[j.id]&&FB[j.id].app)return false; var s=sentOf(j.id)||'none'; if(active!=='all'&&s!==active)return false; if(activeFacet&&tagsOf(j).indexOf(activeFacet)<0)return false; if(searchQuery&&!_matchQ(j,searchQuery))return false; return true;});
    var catCount={}; base.forEach(function(j){var _w=catOf(j);catCount[_w]=(catCount[_w]||0)+1;});
    var pool=jobs.filter(function(j){return !removed(j.id)&&!(FB[j.id]&&FB[j.id].app);});
    var pool2=pool.filter(inRange);
    function nRange(d){return pool.filter(function(j){var k=daysSince(j.added);
      if(!d)return true;
      if(d===-3)return k!==null&&k>14&&!sentOf(j.id);
      return k!==null&&k<d;}).length;}
    function nOpen(d){return pool2.filter(function(j){var k=daysSince(j.posted_at);
      if(!d)return true; if(d===-2)return k===null; if(k===null)return false;
      return k<=d;}).length;}
    var catItems=[['all','全部',base.length]];
    catOrder.forEach(function(c){ if(catCount[c]) catItems.push([c,(catIcon[c]||'')+c,catCount[c]]); });
    Object.keys(catCount).forEach(function(c){ if(catOrder.indexOf(c)<0) catItems.push([c,c,catCount[c]]); });
    var fc={}; base.forEach(function(j){tagsOf(j).forEach(function(x){fc[x]=(fc[x]||0)+1;});});
    var forder=TAGS.map(function(t){return t.name;});
    var fk=forder.filter(function(x){return fc[x];}); Object.keys(fc).forEach(function(x){if(fk.indexOf(x)<0)fk.push(x);});
    var tagItems=[['','全部','']].concat(fk.map(function(x){return [x,x,fc[x]];}));
    var anyFilter=(rangeDays||openDays||activeCat!=='all'||activeFacet||searchQuery);
    var bl=(FB['__block__']||[]).length;
    var chips='<div class="ctlbar">'+
      sel('range','進板',RANGES.map(function(r){return [r[0],r[1],nRange(r[0])];}),rangeDays)+
      sel('open','上架',OPENS.map(function(r){return [r[0],r[1],nOpen(r[0])];}),openDays)+
      sel('cat','類別',catItems,activeCat)+
      (tagItems.length>1?sel('tag','標籤',tagItems,activeFacet):'')+
      sel('sort','排序',SORTS.map(function(x){return [x[0],x[1]];}),sortBy)+
      (anyFilter?'<button class="ctl-act" data-clearall="1">✕ 清掉條件</button>':'')+
      (bl?'<button class="ctl-act" data-blocklist="1" title="看封鎖了哪幾家、解除封鎖">🚫 已封鎖 '+bl+' 家</button>':'')+
      '</div>';
    var list=base.filter(function(j){return activeCat==='all'||catOf(j)===activeCat;});
    if(!list.length){
      // 空的時候要講是哪個條件造成的,並給一鍵清掉。以前只說「這一頁沒有職缺」,
      // 他會以為真的沒缺,其實是自己按了某個篩選。
      var conds=[];
      if(rangeDays)conds.push('進板時間：'+(RANGES.filter(function(r){return r[0]===rangeDays;})[0]||[,''])[1]);
      if(openDays)conds.push('上架多久：'+(OPENS.filter(function(r){return r[0]===openDays;})[0]||[,''])[1]);
      if(activeCat!=='all')conds.push('類別：'+activeCat);
      if(activeFacet)conds.push('標籤：'+activeFacet);
      if(searchQuery)conds.push('搜尋：'+searchQuery);
      $('app').innerHTML=chips+'<div class="emptytab">'+
        (conds.length?('目前的條件把這一頁篩空了：<br><b>'+conds.map(esc).join('　·　')+'</b>'+
          '<br><button class="rchip clr" data-clearall="1">清掉這些條件</button>')
         :(!jobs.length?'看板上還沒有職缺。按上面「🔎 找新職缺」的「▶ 更廣」讓 '+esc(AGENT)+' 去找,或貼自己找到的職缺網址。'+
            '<br><button class="rchip" type="button" data-openfind="1">🔎 打開找新職缺</button>'
           :emptyNext(active)))+'</div>';
      return;}
    var out=_grouped(list,'browse'); LASTLIST=list;
    $('app').innerHTML=chips+rvBarHTML(list)+out; refreshAllCards(); autogrowAll($('app'));
  }
  // 這一頁空的時候講下一步(空畫面不能只寫「沒有」)
  function emptyNext(tab){
    var nm={like:'👍 喜歡',meh:'😐 普通',dislike:'👎 不喜歡',grow:'💪 差一點'}[tab];
    if(tab==='none')return '新職缺都看完了。要更多就按上面「🔎 找新職缺」。';
    if(nm)return '還沒有標成「'+nm+'」的卡。到「🆕 新職缺」一張一張看,按卡上的表態就會放到這裡。';
    if(tab==='techerr')return '沒有標成「出錯了」的卡。卡片資料抓錯、網址打不開時,按卡上的「🔧 標成出錯了」會放到這裡。';
    return '這一頁還沒有職缺。到「🆕 新職缺」看新的,或按上面「🔎 找新職缺」。';
  }
  // ---- ▶ 一張一張看:表態用的專注模式 ----
  // 他每天要一張一張看過幾百張缺。清單模式要先點開公司、再捲過一整張卡(手機上一張 ~750px、表態鈕在 575px),
  // 標完還要自己找下一張。這裡照清單同一個順序一次放一張,表態鈕黏在畫面底下,標完自動換下一張,
  // 桌機有鍵盤(1-4 表態、P 加入準備、←→ 上下一張)。卡片是同一個 cardHTML,按鈕走同一套處理器,存法完全一樣。
  // 順序在按下去那一刻定下來:標完的卡會離開這一頁,清單不能跟著重排。
  var REVIEW=null, _rvTimer=null, LASTLIST=[];
  // 可能已經關了的缺:原頁打不開、掛超過半年、死線已過(卡上 ⚠ 那幾個訊號)。一張一張看時排到最後,也可以一次收掉。
  function isRisky(j){if(isDead(j))return true; var n=daysSince(j.posted_at); if(n!==null&&n>180)return true;
    return deadlinePassed(j);}
  function rvList(list){var g=_coOrder(list,'browse'), ids=[], late=[];
    g.cos.forEach(function(co){sortJobs(g.byCo[co]).forEach(function(j){if(!justMarked[j.id])(isRisky(j)?late:ids).push(j.id);});});
    return ids.concat(late);}
  function rvBarHTML(list){
    var n=rvList(list).length; if(!n)return '';
    var risky=active==='none'?list.filter(function(j){return !justMarked[j.id]&&isRisky(j);}).length:0;
    var oldMeh=active==='meh'?mehOld(list).length:0;
    return '<div class="rvbar"><span class="rvbar-t">'+(active==='none'?'<b>'+n+'</b> 張等你表態':'這一頁 <b>'+n+'</b> 張')+'</span>'+
      (risky?'<button class="stage-b rv-risk" type="button" data-riskrm="1" title="原頁打不開、掛超過半年或死線已過的,收進「🗑 已移除」(放得回來)">⚠ '+risky+' 張可能已關:收掉</button>':'')+
      (oldMeh?'<button class="stage-b rv-risk" type="button" data-mehrm="1" title="標「普通」、進板超過一個月還沒往下走的,收進「🗑 已移除」(放得回來;表態照樣留著給找缺學你的口味)">⏳ '+oldMeh+' 張「普通」放超過一個月:收掉</button>':'')+
      '<button class="stage-b adv rv-start" type="button" data-rvstart="1">▶ 一張一張看</button></div>';}
  // 「還好」是不上不下:進板超過一個月還停在這裡的,多半不會再回頭看了
  function mehOld(list){return list.filter(function(j){var n=daysSince(j.added); return sentOf(j.id)==='meh'&&!(FB[j.id]||{}).app&&n!==null&&n>30;});}
  // 一次收進「🗑 已移除」(放得回來):待評估的可能已關、放太久的還好、流程裡確定下架的共用這一支
  function bulkRemove(ids,what){
    rmMany(ids,today()).then(function(done){
      if(!done.length)return;
      snack('收掉 '+done.length+' 張'+what+'(在「🗑 已移除」)',function(){rmManyUndo(done);});});
  }
  function riskRemove(){
    bulkRemove(LASTLIST.filter(function(j){return !justMarked[j.id]&&isRisky(j)&&!(FB[j.id]||{}).app;}).map(function(j){return j.id;}),'可能已關的缺');
  }
  // 流程裡(還不能投、可投遞)驗收確定下架的:投不了了
  function closedIn(stage){var st=D.status||{};
    return (st.issues||[]).filter(function(x){return issueHolds(x)&&(x.kind==='closed'||(!x.kind&&/已下架/.test(x.msg||'')))&&(FB[x.jid]||{}).app===stage&&!removed(x.jid);})
      .map(function(x){return x.jid;}).filter(function(v,i,a){return a.indexOf(v)===i;});}
  function rvStart(){
    var ids=rvList(LASTLIST); if(!ids.length){snack('這一頁沒有卡可以看');return;}
    REVIEW={ids:ids,i:0,tab:active,marked:0,noreason:false}; justMarked={}; rvSave();
    document.body.classList.add('reviewing'); renderApp(); window.scrollTo(0,0);
  }
  function rvExit(){REVIEW=null; clearTimeout(_rvTimer); rvSave(); document.body.classList.remove('reviewing'); justMarked={}; ORD2={};
    renderAll(); window.scrollTo(0,0);}
  function rvGo(d){if(!REVIEW)return; clearTimeout(_rvTimer);
    REVIEW.i=Math.max(0,Math.min(REVIEW.ids.length,REVIEW.i+d)); rvSave(); renderApp(); window.scrollTo(0,0);}
  // 看到第幾張要跨重整記住(手機切出去再回來,分頁常被系統重載):順序、位置都記在這台裝置,半天內有效。
  var RV_KEY='sts_review';
  function rvSave(){try{if(REVIEW)localStorage.setItem(RV_KEY,JSON.stringify({ids:REVIEW.ids,i:REVIEW.i,tab:REVIEW.tab,marked:REVIEW.marked,t:Date.now()}));
    else localStorage.removeItem(RV_KEY);}catch(e){}}
  function rvRestore(){try{var v=JSON.parse(localStorage.getItem(RV_KEY)||'null');
    if(!v||!Array.isArray(v.ids)||Date.now()-(v.t||0)>12*3600e3||DISCOVER.indexOf(v.tab)<0)return;
    var cur=v.ids[v.i], ids=v.ids.filter(function(id){return !!jobOf(id);}); if(!ids.length)return;
    var i=cur&&ids.indexOf(cur)>=0?ids.indexOf(cur):Math.min(v.i,ids.length);
    active=v.tab; REVIEW={ids:ids,i:i,tab:v.tab,marked:v.marked||0}; document.body.classList.add('reviewing');}catch(e){}}
  // 標完才想到要寫原因:回到剛才那張、游標放進原因那格(不會再自動換張,寫完按 → 繼續)
  function rvBackToLast(){var L=REVIEW&&REVIEW.last; if(!L)return; var k=REVIEW.ids.indexOf(L.id); if(k<0)return;
    clearTimeout(_rvTimer); REVIEW.i=k; REVIEW.last=null; rvSave(); renderApp(); window.scrollTo(0,0);
    var ta=document.querySelector('#app .rv-card .fb-t'); if(ta){try{ta.focus({preventScroll:true});}catch(e){}}}
  function rvCur(){return REVIEW&&REVIEW.ids[REVIEW.i];}
  // 這張處理掉了(表態、加入準備、移除):停一下讓他看到按下去的樣子,再換下一張
  function rvDone(id,ms){if(!REVIEW||rvCur()!==id)return; clearTimeout(_rvTimer);
    _rvTimer=setTimeout(function(){if(REVIEW&&rvCur()===id)rvGo(1);},ms==null?600:ms);}
  // 手機:頂端只剩結束和進度,而且不黏著(以前黏在頂端,蓋住公司名,拇指也搆不到);
  // 上一張、跳過跟表態鈕一起放在黏在底下的那一段。
  // 寬螢幕:左邊是這一輪的清單,右邊是目前這張。標完才想補原因,點清單哪一列都回得去。
  function rvNavHTML(){var R=REVIEW, n=R.ids.length;
    return '<div class="rv-navrow">'+
      '<button class="stage-b rv-nav" type="button" data-rvgo="-1"'+(R.i?'':' disabled')+'>← 上一張</button>'+
      '<button class="stage-b rv-nav" type="button" data-rvgo="1"'+(R.i<n?'':' disabled')+'>跳過 →</button></div>';}
  var RV_MOOD={like:'👍',meh:'😐',dislike:'👎',grow:'💪'};
  function rvListHTML(){var R=REVIEW;
    var rows=R.ids.map(function(id,k){var j=jobOf(id); if(!j)return '';
      var f=FB[id]||{}, m=RV_MOOD[f.s]||'', note=String(f.n||'').trim();
      if(R.noreason&&!(m&&!note))return '';
      return '<button class="rv-row'+(k===R.i?' cur':'')+(m?' done':'')+'" type="button" data-rvat="'+k+'">'+
        '<span class="rv-row-m">'+(m||'·')+'</span><span class="rv-row-t">'+esc(cardName(j))+'</span>'+
        (note?'<span class="rv-row-n">'+esc(note)+'</span>':'')+'</button>';}).join('');
    return '<aside class="rv-list"><label class="rv-flt"><input type="checkbox" data-rvnoreason="1"'+(R.noreason?' checked':'')+'> 只看標了沒寫原因的</label>'+
      '<div class="rv-rows">'+(rows||'<p class="rv-none">都寫了原因</p>')+'</div></aside>';}
  function rvAt(k){if(!REVIEW)return; clearTimeout(_rvTimer);
    REVIEW.i=Math.max(0,Math.min(REVIEW.ids.length,k)); rvSave(); renderApp();
    var ta=REVIEW.noreason&&document.querySelector('#app .rv-card .fb-t'); if(ta){try{ta.focus({preventScroll:true});}catch(e){}}}
  function renderReview(){
    var R=REVIEW, n=R.ids.length, id=R.ids[R.i], j=id&&jobOf(id);
    var top='<div class="rv-top"><button class="stage-b rv-x" type="button" data-rvexit="1">✕ 結束</button>'+
      '<span class="rv-pos"><b>'+Math.min(R.i+1,n)+'</b> / '+n+'</span>'+
      '<span class="rv-bar"><i style="width:'+Math.round(Math.min(R.i,n)*100/n)+'%"></i></span>'+
      '<span class="rv-keys">1–4 表態 · P 加入準備 · ← → 上一張/跳過 · Esc 結束</span></div>';
    var main;
    if(!j){
      main='<div class="rv-end"><p>這一輪 <b>'+n+'</b> 張看完了'+(R.marked?',表態 '+R.marked+' 次':'')+'。</p>'+
        '<button class="stage-b adv" type="button" data-rvexit="1">回到清單</button>'+rvNavHTML()+'</div>';
    }else{
      var L=R.last&&R.last.id!==id&&jobOf(R.last.id), lastChip=L?'<button class="rv-last" type="button" data-rvlast="1">'+
        '<span class="rv-last-m">剛才 '+esc(RV_MOOD[R.last.s]||'')+'</span>'+
        '<span class="rv-last-t">'+esc(cardName(L))+'</span><b>✍️ 補原因</b></button>':'';
      var f=FB[id]||{}, where=f.app?'已進「'+({prep:'準備履歷中',ready:READY_NAME,ship:'可以投了',sent:'已投出'}[f.app]||f.app)+'」':(f.rm?'已移除':'');
      main='<div class="rv-co">'+esc(companyOf(j))+(where?'<span class="rv-where">'+esc(where)+'</span>':'')+'</div>'+
        lastChip+'<div class="rv-card">'+cardHTML(j)+'</div>';
    }
    $('app').innerHTML='<div class="rv">'+top+rvListHTML()+'<div class="rv-main">'+main+'</div></div>';
    if(!j)return;
    var fb=document.querySelector('#app .rv-card .card>.fb'), sv=fb&&fb.querySelector(':scope>.fb-save');
    if(sv)sv.insertAdjacentHTML('beforebegin',rvNavHTML()); else if(fb)fb.insertAdjacentHTML('beforeend',rvNavHTML());
    var ta=document.querySelector('#app .rv-card .fb-t');
    if(ta)ta.placeholder='原因(先寫再表態;標完才想補,馬上點這格)';
    var cur=document.querySelector('#app .rv-row.cur'), box=cur&&cur.parentNode;
    if(cur&&box.offsetParent&&(cur.offsetTop<box.scrollTop||cur.offsetTop+cur.offsetHeight>box.scrollTop+box.clientHeight))
      box.scrollTop=cur.offsetTop-box.clientHeight/3;
    refreshAllCards(); autogrowAll($('app'));
  }
  // 標完正要換下一張的那 0.4 秒內點了原因那格:他要補寫原因,留在這張(寫完按 → 換下一張)
  document.addEventListener('focusin',function(e){
    if(REVIEW&&e.target.closest&&e.target.closest('#app .rv-card .fb-t'))clearTimeout(_rvTimer);});
  document.addEventListener('keydown',function(e){
    if(!REVIEW||e.metaKey||e.ctrlKey||e.altKey)return;
    var t=e.target; if(t&&(/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)||t.isContentEditable))return;
    var ov=$('rzmodal'); if(ov&&ov.style.display==='flex')return;
    var card=document.querySelector('#app .rv-card article'), k=e.key.toLowerCase();
    var mood={'1':'like','2':'meh','3':'dislike','4':'grow'}[k];
    if(mood&&card){var b=card.querySelector('.fb-b[data-s="'+mood+'"]'); if(b){e.preventDefault(); b.click();} return;}
    if(k==='p'&&card){var p=card.querySelector('[data-adv="prep"]'); if(p){e.preventDefault(); p.click();} return;}
    if(k==='arrowright'||k==='j'){e.preventDefault(); rvGo(1); return;}
    if(k==='arrowleft'||k==='k'){e.preventDefault(); rvGo(-1); return;}
    if(k==='escape'&&!document.querySelector('.more-m:not([hidden])')&&!$('ctlmenu')){rvExit();}
  });
  function switchTab(k){if(active==='cfg'&&k!=='cfg'&&CFGDIRTY)snack('設定頁有改動還沒存',null,{label:'回去存',fn:function(){switchTab('cfg');}});if(REVIEW){REVIEW=null; rvSave(); document.body.classList.remove('reviewing');} active=k; justMarked={}; ORD2={}; SECSNAP={}; renderTabs(); renderApp(); window.scrollTo(0,0); saveView();}
  // 「去看已投遞」不能只是切過去:那一頁按公司收著,剛搬過去的那張還藏在收起來的公司裡,
  // 等於點了還是看不到。切過去就把它那家打開、捲到卡上、閃一下告訴他人在這。
  function goToJob(id,k){
    var j=null; for(var i=0;i<jobs.length;i++){if(jobs[i].id===id){j=jobs[i];break;}}
    if(j)FOLD['co:'+coKey(k==='sent'?coCat('sent-'+secOf(FB[id]||{})):'',companyOf(j))]=1;   // 要跟 _grouped 畫公司群組用的 key 一樣,不然那家打不開、跳不過去
    if(REVIEW){REVIEW=null; rvSave(); document.body.classList.remove('reviewing');}
    active=k; justMarked={}; SECSNAP={}; renderTabs(); renderApp(); saveView();
    var el=document.querySelector('article[data-fid="'+CSS.escape(id)+'"]');
    if(!el){window.scrollTo(0,0);return;}
    el.scrollIntoView({block:'center'});
    el.classList.add('justhere');
    setTimeout(function(){el.classList.remove('justhere');},2400);
  }


  // 存檔只有一條路:本機 server 的 /api/save,改完自動存(防抖)。
  // 以前還有一條另外發布的路徑,為了它在這裡抄了一份文件骨架
  // (PRE/M1…SUF),必須跟 board_doc.py 那份逐字一致。artifact 不用了,骨架的
  // 單一真相回到 tools/board_doc.py 一份。
  var canSave=false, saving=false;
  var saveEpoch=0;   // 每成功存一次 +1;輪詢用它判斷「我拿到的伺服器狀態是不是存檔之前的舊版」
  // 存檔前的樣子。改了又改回來就等於沒改,不能只看「他碰過沒」。
  var SAVED=JSON.parse(JSON.stringify(FB));
  if(location.protocol==='http:'||location.protocol==='https:'){
    canSave=true; refreshBar();
  } else { readonly(); }  // 直接開檔案(file://)沒有 server 可存,轉唯讀
  function readonly(){$('savebar').className='savebar warn';$('savebar-lbl').textContent='此裝置無法存檔(唯讀)';}
  // 比對用的正規化字串:鍵排序,空值當成沒有。少了這個,{a:1,b:2} 跟 {b:2,a:1} 會被當成不一樣。
  function sig(v){
    if(v===null||v===undefined||v==='')return '';
    // 空物件、空陣列要跟「沒有這個欄位」一樣。不然把某個巢狀欄位拿掉之後
    // 剩一個 {} ,跟基準的「完全沒有這欄」比起來還是不一樣,那筆就永遠回不到已存。
    if(Array.isArray(v)){var el=v.map(sig).filter(function(x){return x!=='';});
      return el.length?'['+el.join(',')+']':'';}
    if(typeof v==='object'){var ks=Object.keys(v).filter(function(k){return sig(v[k])!=='';}).sort();
      return ks.length?'{'+ks.map(function(k){return k+':'+sig(v[k]);}).join(',')+'}':'';}
    return String(v);
  }
  function cardEls(id){return document.querySelector('article[data-fid="'+CSS.escape(id)+'"]');}
  function refreshCard(id){var card=cardEls(id);if(!card||!canSave)return;var stt=card.querySelector('.fb-st');if(!stt)return;
    if(saveErr&&dirty[id]){stt.className='fb-st dirty';stt.textContent='● 這筆沒存到';}   // 平常不標,自動存會處理
    else{stt.className='fb-st';stt.textContent='';}}
  function refreshAllCards(){document.querySelectorAll('article[data-fid]').forEach(function(c){refreshCard(c.getAttribute('data-fid'));});}
  function unsavedCount(){var n=0;for(var k in dirty){if(dirty[k])n++;}
    for(var p in PEND){if(PEND[p].some(function(x){return !x.sent;}))n++;}
    return n+(EVENTS.length?1:0);}
  var _okTimer=null, saveErr='';
  // 看板檔被繞過正常寫入改過(伺服器回 423 tampered):照伺服器講的原因顯示在存檔列,存檔停著,等他處理好
  var tampered='';
  function takeTampered(msg){tampered=msg||'看板檔被繞過正常寫入改過了,程式先停下來';saveErr=tampered;refreshAllCards();refreshBar();}
  // 存檔是背景的事:改完自己存,不催他、不要他按。所以這裡只做兩件事——
  // 頁首給一個安靜的狀態字,以及「只有出事的時候」才把底下那條浮出來要他處理。
  // (主流做法都是這樣:Google Docs / Notion / Linear 都沒有存檔按鈕,只有狀態,
  //  而且狀態平常安靜、存不起來才變成需要動作的東西。)
  function refreshBar(flash){var bar=$('savebar'),lbl=$('savebar-lbl');if(!canSave)return;
    var n=unsavedCount(), dk=$('savedock'), dlbl=$('savedock-lbl'), dbtn=$('save-dock');
    if(saveErr){bar.className='savebar warn';lbl.textContent=saveErr;}
    else if(n>0){bar.className='savebar dirty';lbl.textContent='已存';}   // 字不換,CSS 把它淡掉,不會一直翻
    else{bar.className='savebar clean';lbl.textContent='已存';}
    if(!dk)return;
    if(_okTimer){clearTimeout(_okTimer);_okTimer=null;}
    if(saveErr){   // 唯一會浮出來的情況:存不起來。這時它是「再試一次」,不是「存檔」。
      dk.className='savedock on err';dlbl.textContent=saveErr;
      dbtn.style.display='';dbtn.textContent='再試一次';dbtn.disabled=false;}
    else dk.className='savedock';
    // 訊息條要疊在存檔條上面而不是底下,所以把存檔條量到的高度往上讓
    document.documentElement.style.setProperty('--dock-h',
      dk.classList.contains('on') ? (Math.min(dk.offsetHeight,220)+10)+'px' : '0px');
  }
  // 改完不是「碰過就算未存」,是「跟存檔前不一樣才算未存」。切到中文再切回英文,
  // 第二次比對出來一樣,那筆就不必再送。
  // 比對前先把「跟預設一樣的值」拿掉。某張卡原本沒存過版本,按一下別的版本就多一個欄位,
  // 再按回去仍然是「多一個欄位」,跟原本的空白不一樣,那筆就永遠回不到已存。
  function jobOf(id){for(var i=0;i<jobs.length;i++)if(jobs[i].id===id)return jobs[i];return null;}
  function defaultsOf(id){
    var j=jobOf(id); if(!j)return {};
    var rz=j.resume||{};
    return {lang:rz.lang||'', variant:rz.recommend||''};
  }
  function norm(id,src){
    var v=(src||{})[id]; if(!v)return '';
    var o={},k; for(k in v)o[k]=v[k];
    var d=defaultsOf(id);
    if(o.lang&&o.lang===d.lang)delete o.lang;
    if(o.variant&&o.variant===d.variant)delete o.variant;
    return sig(o);
  }
  // 每個按鍵都會走到這裡,所以只做最便宜的事:記下哪筆跟存檔版本不一樣。
  // 以前每個鍵都把整份標記(52KB)序列化一次、重建整條分頁列、翻頁首、點亮卡片「未存」,
  // 他打字時整頁一直閃,那就是他說的「瘋狂存檔」。
  // 分頁上的數字只有心情/階段會動,打字不會,所以打字時不重建分頁列。
  function touch(id,kind){
    if(norm(id,FB)===norm(id,SAVED))delete dirty[id]; else dirty[id]=true;
    refreshBar();
    if(kind!=='text')renderTabs();
  }
  // 自動存。節奏照主流分兩種:
  //  · 按鈕(心情/階段/版本/語言):900ms,連按很多張併成一次。誤觸靠每個動作的「復原」,
  //    不是靠拖著不存。(6262664 那次把自動存拿掉,原因是誤觸語言鈕沒得反悔;
  //    那是缺復原,不是自動存的錯。)
  //  · 打字:停 2 秒才存,或離開輸入框立刻存。不在他打字打到一半時一直送。
  //  · 切到背景、關分頁:立刻送,用 keepalive——iOS 會直接回收背景分頁,
  //    這一步才是資料不會掉的保證,也是敢把打字的間隔拉長的理由。
  var _auto=null;
  function scheduleSave(ms){
    if(!canSave)return;
    clearTimeout(_auto);
    _auto=setTimeout(function(){_auto=null; if(unsavedCount()>0)doSave();},ms);
  }
  // 規矩檢查(tools/board_check.py 的 T.idle)用:還有排著(計時器)或正在送的存檔就是「忙」。
  // 沒排也沒在送、卻還有沒存的(例如撞到版本衝突),等下去也不會自己動,不算忙。
  // 檢查等它變閒再看伺服器,不再乾等固定秒數。只讀,不改任何狀態。
  // 清掉答案、按了的按鈕還在等後台回話也算
  window.__jobsalvoSaveBusy=function(){return saving||!!_auto||Object.keys(REDO_DONE).length>0||Object.keys(PEND).length>0;};
  function flushSave(keepalive){
    clearTimeout(_auto); _auto=null;
    if(unsavedCount()>0)doSave({keepalive:!!keepalive});
  }
  function markDirty(id,kind){
    touch(id,kind);
    scheduleSave(kind==='text'?2000:900);
  }
  // 動到任何一張卡的標記,一律走這裡:記未存 + 重畫現在這一頁(捲動位置與展開的公司群組都留著)。
  // 以前只有「換階段」會重畫,按心情/語言/版本是手動補卡片外觀,卡片就留在原分頁不動,
  // 數字先變、卡片後動,他得重整才看得到。改法是所有改動走同一條路。
  function applyChange(id){ markDirty(id); renderAll(id); }
  // 只換一張卡,不重建整頁。整頁重建是所有跳動與焦點消失的來源:一次 40ms、
  // 把每個節點都換掉(正在打字的那格也一起),然後再靠捲動錨點把位置救回來。
  // 標心情是最常按的動作,而且規矩是「標完留在原地」(justMarked),所以它一定
  // 還在這一頁——這種情況直接換那一張就好,頁面其餘部分完全不動。
  function patchCard(id){
    if(!justMarked[id])return false;                       // 只有「留在原地」那條規矩保證得了
    if(STAGES.indexOf(active)>=0||active==='cfg'||active==='rm')return false;
    var el=document.querySelector('#app article[data-fid="'+CSS.escape(id)+'"]');
    if(!el)return false;
    var j=null; for(var i=0;i<jobs.length;i++){if(jobs[i].id===id){j=jobs[i];break;}}
    if(!j)return false;
    var box=document.createElement('div'); box.innerHTML=cardHTML(j);
    var nw=box.firstElementChild; if(!nw)return false;
    el.replaceWith(nw); refreshCard(id); autogrowAll(nw);
    return true;
  }
  // 已投遞記結果:那張卡換成新的樣子、上面的統計重算,其他東西一律不動。
  function patchInPlace(id){
    var el=document.querySelector('#app article[data-fid="'+CSS.escape(id)+'"]'), j=jobOf(id);
    if(el&&j){var box=document.createElement('div'); box.innerHTML=cardHTML(j);
      var nw=box.firstElementChild; if(nw){el.replaceWith(nw); refreshCard(id); autogrowAll(nw);}}
    var fn=$('funnel');
    if(fn){var b2=document.createElement('div'); b2.innerHTML=funnelHTML();
      if(b2.firstElementChild)fn.replaceWith(b2.firstElementChild);}
    renderTabs(); refreshBar();
  }
  // 破壞性動作(移除/技術錯誤/封鎖)做完,就地跳一條可以按回去的訊息。
  // 以前要自己切到別的分頁才找得回來,等於做完就失聯。
  var _snapTimer=null;
  function snack(msg, undoFn, next){
    var el=$('snack'); if(!el){el=document.createElement('div'); el.id='snack'; document.body.appendChild(el);}
    el.innerHTML='<span>'+esc(msg)+'</span>'+
      (next?'<button type="button" class="snack-next">'+esc(next.label)+'</button>':'')+
      (undoFn?'<button type="button" class="snack-undo">復原</button>':'');
    el.className='on';
    if(undoFn){el.querySelector('.snack-undo').onclick=function(){undoFn(); el.className='';};}
    if(next){el.querySelector('.snack-next').onclick=function(){next.fn(); el.className='';};}
    clearTimeout(_snapTimer); _snapTimer=setTimeout(function(){el.className='';},8000);
  }
  // 畫面有幾塊會跟著標記變:分頁數字、卡片列。
  // 一律一起畫,免得又出現「這塊變了那塊沒變」。捲動位置留著。
  function renderAll(_fid){
    var y=window.pageYOffset||document.documentElement.scrollTop;
    // 只記捲軸數字不夠:被標掉的那張卡如果在畫面上方,重畫後它消失,下面的東西整個
    // 往上跳一張卡的高度,他正在看的那張就被抽走了(他的話:「它就直接整個關起來,
    // 我旁邊不是還有在看的嗎」)。所以另外記一個看得見的錨點(畫面上第一條公司列)
    // 在視窗裡的高度,重畫後把它放回同一個高度。
    // 錨點要抓「被動到那張卡下面」的公司列。抓上面的沒有用:卡片被抽走,上面的東西
    // 本來就不會動,會動的是下面所有東西(整個往上跳一張卡的高度,他正在看的就飛走了)。
    // 記好幾個候選,因為那一列自己也可能消失(那家只剩這一個缺,標掉整列就不見)。
    var from=-1e9;
    if(_fid){var _c=document.querySelector('article[data-fid="'+CSS.escape(_fid)+'"]');
             if(_c)from=_c.getBoundingClientRect().top;}
    // 候選包含卡片本身,不是只有公司列:同一家裡面被標掉的那張如果在他正在看的那張
    // 上面,整組會往上滑,釘公司列救不到他眼睛盯著的那張卡。
    function _sel(el){return el.hasAttribute('data-k')
      ? '#app .cohead[data-k="'+CSS.escape(el.getAttribute('data-k'))+'"]'
      : '#app article[data-fid="'+CSS.escape(el.getAttribute('data-fid'))+'"]';}
    // 桌機是兩欄,他正在看的那張常常跟被標掉的那張同一排(top 一樣),所以是 >= 不是 >;
    // 被標掉的那張自己不算候選(它馬上就不見了)。
    var cands=[], hs=document.querySelectorAll('#app .cohead, #app article[data-fid]');
    for(var i=0;i<hs.length&&cands.length<8;i++){var r=hs[i].getBoundingClientRect();
      if(hs[i].getAttribute('data-fid')===_fid)continue;
      if(r.top>=from)cands.push([_sel(hs[i]), r.top]);}
    if(!cands.length){for(var i2=0;i2<hs.length&&cands.length<8;i2++){var r2=hs[i2].getBoundingClientRect();
      if(r2.bottom>0)cands.push([_sel(hs[i2]), r2.top]);}}
    // 重建會把正在打字的那格也換掉:文字本身在 FB 裡不會掉,但焦點與游標位置會。
    // 手機上就是鍵盤突然收起來、游標跳回開頭。先記下來,畫完放回去。
    var ae=document.activeElement, keep=null;
    if(ae&&(ae.tagName==='TEXTAREA'||ae.tagName==='INPUT')){
      var art=ae.closest?ae.closest('article[data-fid]'):null;
      keep={id:ae.id||'', fid:art?art.getAttribute('data-fid'):'', cls:(ae.className||'').split(' ')[0],
            s:ae.selectionStart, e:ae.selectionEnd};
    }
    renderTabs(); renderCuts(); renderInbox(); renderApp();
    var _fs=$('find-score'); if(_fs)_fs.innerHTML=findScoreHTML();   // 標記一變,各找法的命中數跟著變
    var _fr=$('find-rounds'); if(_fr)_fr.innerHTML=findRoundsHTML();
    window.scrollTo(0,y);
    if(keep){
      var q=keep.id?('#'+keep.id)
        :(keep.fid?('#app article[data-fid="'+CSS.escape(keep.fid)+'"] .'+keep.cls):('.'+keep.cls));
      var fe=document.querySelector(q);
      if(fe&&fe!==document.activeElement){
        try{fe.focus({preventScroll:true});}catch(e1){}   // preventScroll:不然對焦又把頁面捲走
        try{if(fe.setSelectionRange)fe.setSelectionRange(keep.s,keep.e);}catch(e2){}
      }
    }
    for(var j=0;j<cands.length;j++){
      var el=document.querySelector(cands[j][0]);
      if(el){var d=Math.round(el.getBoundingClientRect().top-cands[j][1]); if(d)window.scrollTo(0,y+d); break;}
    }
  }
  // 只送這次真的改過的那幾筆,不要把整份標記倒過去。
  // 整包送的話,一個停在舊狀態的分頁(手機擱著沒關)一自動存,就會把別處的新改動蓋掉。
  // 空字串、空物件不要寫進檔案。清掉一則心得留下 "n":"" 只是垃圾,而且下次比對還得
  // 特別處理。整筆都空了就送 null,伺服器會把那個 key 刪掉。
  function lean(v){
    if(v===null||v===undefined||v==='')return undefined;
    if(Array.isArray(v))return v.length?v:undefined;
    if(typeof v==='object'){var o={},n=0;Object.keys(v).forEach(function(k){var x=lean(v[k]);if(x!==undefined){o[k]=x;n++;}});
      return n?o:undefined;}
    return v;
  }
  // 送出去的除了新值,還要附上「我這個分頁以為伺服器上是什麼」(__base__)。
  // 伺服器比對過才寫:別的裝置先改過同一筆,這次就整批擋下來,不會安靜蓋掉。
  function changedOnly(){var o={__rev__:1,__base__:{}},k,v;
    for(k in dirty){if(dirty[k]&&FB.hasOwnProperty(k)){
      v=lean(FB[k]); o[k]=(v===undefined?null:v);
      var _b=SAVED.hasOwnProperty(k)?lean(SAVED[k]):undefined;   // 跟送出去的值同一套「空」的寫法
      o.__base__[k]=(_b===undefined?null:_b);}}
    return o;}
  // 409 之後:被別的裝置(或 agent、程式)搶先存了同一筆。後台照它現在的版本,把他手上的改動疊上去
  // (board_server.merge_edit:各改各的格子都留下,同一格撞到先留這台的並回報撞到哪幾格),拿回來當新的基準再送一次。
  // 以前是直接丟掉他這幾筆、換成那邊的:另一台只改了心情,他這台打了半天的心得也一起不見。
  // 送的是他手上的(FB),不是這一包:按了還沒回話的按鈕(PEND)不能先合進卡片(#343)
  var resolving=false;
  function resolveConflict(keys){
    // 合併完之前誰都別送,連切到背景的 keepalive 也不行:基準還是舊的,送出去只會再撞一次,
    // 兩次合併同時改同一份標記。
    saving=true; resolving=true;
    var base={}, mine={};
    keys.forEach(function(k){if(SAVED.hasOwnProperty(k))base[k]=SAVED[k]; if(FB.hasOwnProperty(k))mine[k]=FB[k];});
    return fetch('/api/merge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keys:keys,base:base,mine:mine})})
    .then(function(r){if(!r.ok)throw new Error('merge'); return r.json();}).then(function(got){
      var srv=got.fb, hit=[], back={};
      keys.forEach(function(k){
        var r=got.merged[k];
        if(r.clash.length){hit.push(k); back[k]={v:srv[k],f:r.clash,key:r.key};}
        if(srv.hasOwnProperty(k))SAVED[k]=JSON.parse(JSON.stringify(srv[k])); else delete SAVED[k];
        if(r.value==null)delete FB[k]; else FB[k]=r.value;
        if(norm(k,FB)===norm(k,SAVED))delete dirty[k]; else dirty[k]=true;
      });
      saving=false; resolving=false; saveEpoch++;
      pullFB(srv); srvRev=null;
      $('data-fb').textContent=JSON.stringify(FB);
      saveErr=''; renderAll(); refreshBar();
      // 撞到的格子:先留他這台的,「復原」換回那邊的
      if(hit.length)snack('有 '+hit.length+' 筆別的裝置或 '+AGENT+' 也改了同一格,先留你這邊的',function(){
        hit.forEach(function(k){var b=back[k];
          if(b.key&&b.f[0]!=='*'){   // 照 id 合的:只把撞到的那幾條換回那邊的(那邊刪了就刪)
            var th={}; (b.v||[]).forEach(function(x){th[x[b.key]]=x;});
            FB[k]=(FB[k]||[]).filter(function(x){return b.f.indexOf(x[b.key])<0;})
              .concat(b.f.filter(function(id){return th[id];}).map(function(id){return th[id];}));
            return;}
          if(b.f[0]==='*'||!b.v||typeof b.v!=='object'){if(b.v===undefined)delete FB[k]; else FB[k]=b.v; return;}
          // 撞到的那幾欄換回那邊的:卡片一層(n)或填表紀錄這種兩層(apply.stale);更深的整格換回那邊的
          var o=JSON.parse(JSON.stringify(FB[k]||{})), bv=b.v||{};
          b.f.forEach(function(f){var ps=f.split('.'), top=ps[0], sub=ps[1];
            if(ps.some(function(x){return x==='__proto__'||x==='constructor'||x==='prototype';}))return;   // 欄位名來自資料:不准碰物件原型
            if(ps.length===2&&bv[top]&&typeof bv[top]==='object'){var cur=JSON.parse(JSON.stringify(o[top]&&typeof o[top]==='object'?o[top]:{}));
              if(bv[top][sub]===undefined)delete cur[sub]; else cur[sub]=bv[top][sub]; o[top]=cur; return;}
            if(bv[top]===undefined)delete o[top]; else o[top]=bv[top];});
          FB[k]=o;});
        hit.forEach(function(k){touch(k,'mark');}); renderAll(); scheduleSave(900);});
      else snack('別的裝置或 '+AGENT+' 剛改過同一張,兩邊的改動都留下了');
      if(unsavedCount()>0)scheduleSave(900);
    }).catch(function(){saving=false; resolving=false; srvRev=null;
      saveErr='存不起來，點「再試一次」'; refreshAllCards(); refreshBar();});
  }
  function doSave(opts){opts=opts||{};
    if(!canSave){readonly();return;}
    if(resolving||(saving&&!opts.keepalive))return;   // 正在存的那包回來後會再排下一次
    var payload=changedOnly(), acts=[], evs=[];
    // 按了的按鈕(PEND):那張卡照「現在的樣子 + 跟著那一下改的欄位」送,畫面上的卡先不動;事件排在答案的事件前面
    Object.keys(PEND).forEach(function(id){var q=PEND[id].filter(function(x){return !x.sent;}); if(!q.length)return;
      var v=clone(FB[id])||{};
      q.forEach(function(x){Object.keys(x.set).forEach(function(k){if(x.set[k]===null)delete v[k]; else v[k]=clone(x.set[k]);});
        x.sent=true; acts.push([id,x]); if(x.e)evs.push(Object.assign({u:id},x.e));});
      if(!dirty[id]&&!q.some(function(x){return Object.keys(x.set).length;})){delete payload[id]; delete payload.__base__[id]; return;}
      var lv=lean(v), b=SAVED.hasOwnProperty(id)?lean(SAVED[id]):undefined;
      payload[id]=lv===undefined?null:lv; payload.__base__[id]=b===undefined?null:b;});
    // 答案的事件跟著這一包送(正在送的那一包還沒回來就先留著,不重送)
    var sentAns=[];
    if(EVENTS.length&&!INFLIGHT.length){sentAns=INFLIGHT=EVENTS; EVENTS=[];}
    if(evs.length||sentAns.length)payload.__events__=evs.concat(sentAns);
    if(Object.keys(payload).length<3){refreshBar();return;}
    // 只排除兩個控制欄位。以前寫成「開頭不是 __」,連 __notes__(我的想法)、__block__(封鎖)、
    // __ans__(答案庫)這類真資料也一起排除:它們存完之後基準永遠不更新,
    // 第二次存就被版本檢查當成衝突擋掉,畫面還被換回伺服器上的舊版。
    var sending=Object.keys(payload).filter(function(k){return k!=='__rev__'&&k!=='__base__'&&k!=='__events__'&&!PEND[k];});
    var redone=sentAns.filter(function(x){return x&&x.redo!==undefined;}).map(function(x){return x.redo;});
    var refilled=sentAns.some(function(x){return x&&x.refill!==undefined;});
    function eventsBack(){if(sentAns.length){EVENTS=sentAns.concat(EVENTS); INFLIGHT=[];} acts.forEach(function(p){p[1].sent=false;});}   // 沒送成:下一次再送
    // 確認送出之後答案改了,後台把確認作廢:回來時講一聲(看板自己不判斷,看下一步前後)
    var soon={}; if(refilled)jobs.forEach(function(j){if((nextOf(j.id).view||{}).soon)soon[j.id]=1;});
    saving=true;
    $('save-dock').disabled=true;
    fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},
                       body:JSON.stringify(payload),keepalive:!!opts.keepalive})
      .then(function(r){if(r.status===403)throw new Error('agent');
        if(r.status===423)return r.json().then(function(j){var e=new Error('tampered');e.msg=j.msg;throw e;});
        if(r.status===409)return r.json().then(function(j){var e=new Error('conflict');e.keys=j.keys||[];throw e;});
        if(!r.ok)throw new Error('http'+r.status);saving=false;saveEpoch++;
        if(sentAns.length)INFLIGHT=[];
        // 新的基準是「這次真的送出去的值」,不是現在畫面上的值。
        // 以前用畫面上的值:送出途中他又打了幾個字,那幾個字根本沒到伺服器,卻被標成已存,
        // 重整就不見。自動存一頻繁,這個窗口就常常碰得到。
        sending.forEach(function(k){
          var sent=payload[k];
          // 清空要維持原本的型別:陣列還是陣列、物件還是物件(封鎖名單/想法是陣列,
          // 以前一律給 {},下次重畫 .indexOf 直接爆掉)。
          SAVED[k]=(sent===null)?(Array.isArray(FB[k])?[]:{}):JSON.parse(JSON.stringify(sent));
          if(norm(k,FB)===norm(k,SAVED))delete dirty[k]; else dirty[k]=true;   // 途中又改的留著
        });
        saveErr='';refreshAllCards();refreshBar('ok');
        return r.json().catch(function(){return {};});})
      .then(function(res){res=res||{}; if(res.building) watchBuild();
        // 後台存好的卡(按了的、改答案動到的):照它畫。按了的那幾張整張換成後台的;其他的他途中又改過就不動
        var cards=res.cards||{}, mine={};
        acts.forEach(function(p){mine[p[0]]=1;});
        // 就地換:復原那些按鈕手上拿的是這張卡本身,換成新物件它們就改到舊的那份
        // 只換按了的那幾張、和這一包沒送的(改答案動到的表單):照常存的卡留著手上這份,復原那些按鈕手上拿的是裡面的東西
        Object.keys(cards).forEach(function(k){if(!mine[k]&&(dirty[k]||sending.indexOf(k)>=0))return;
          if(cards[k]===null){delete FB[k]; delete SAVED[k];}
          else{var o=FB[k]&&typeof FB[k]==='object'&&!Array.isArray(FB[k])?FB[k]:(FB[k]={});
            Object.keys(o).forEach(function(x){delete o[x];}); Object.assign(o,clone(cards[k])); SAVED[k]=clone(cards[k]);}
          if(norm(k,FB)===norm(k,SAVED))delete dirty[k]; else dirty[k]=true;});
        if(Object.keys(cards).length)$('data-fb').textContent=JSON.stringify(FB);
        var nx0=JSON.stringify(NEXT);
        if(res.next)NEXT=Object.assign({},NEXT,res.next);   // 有變動的卡的新下一步(按鈕能不能按、為什麼不能)
        var rej={}; (res.rejected||[]).forEach(function(x){if(!rej[x.u])rej[x.u]=x.msg;});
        acts.forEach(function(p){var id=p[0], x=p[1], q=PEND[id]||[], i=q.indexOf(x); if(i>=0)q.splice(i,1); if(!q.length)delete PEND[id];
          x.done(rej[id]?{ok:false,why:rej[id]}:{ok:true,undo:(res.undo||{})[id]});});
        Object.keys(soon).forEach(function(id){if(!(nextOf(id).view||{}).soon&&!mine[id]){clearTimeout(SUBMIT_SOON[id]); delete SUBMIT_SOON[id];
          var j=jobOf(id); snack('「'+cardName(j||{id:id})+'」確認之後答案改過,確認送出作廢了:重新看過再確認');}});
        if(acts.length||Object.keys(cards).length||JSON.stringify(NEXT)!==nx0)renderAll();
        if(unsavedCount()>0)scheduleSave(900);      // 途中又改的,再送一次
        // 清掉答案:後台改了常用答案和表單(重打的標記、作廢的確認),拿回來才講是清掉還是刪掉
        if(redone.length)redoSynced(function(){redone.forEach(function(k){var f=REDO_DONE[k]; delete REDO_DONE[k]; if(f)f();});},20);
        // 不是按鈕帶的(直接存檔被擋,例如正在送出時換檔):照它講的原因說,重拿伺服器的版本
        var other=(res.rejected||[]).filter(function(x){return !mine[x.u];});
        if(other.length){snack(other[0].msg); srvRev=null; syncFromServer();} })
      .catch(function(e){saving=false; eventsBack();
        // 409 = 這幾筆在別的裝置上先改過了。兩邊合併後再送;伺服器是整批擋下,
        // 同一批裡其他沒衝突的改動也跟著重送。安靜蓋掉(任何一邊)才是最糟的結果。
        if(e&&e.message==='conflict'){resolveConflict(e.keys||[]); return;}
        if(e&&e.message==='tampered'){takeTampered(e.msg); return;}
        refreshAllCards();
        // 403 = 這是 AI 助理的瀏覽器在點。看板的標記只有使用者能寫,這裡講清楚不要說成「沒存到」。
        saveErr=(e&&e.message==='agent')?'這個看板不收 agent 的寫入':'存不起來，點「再試一次」';
        refreshBar();});}

  // 外部改動一律靠這個同步:背景 reconcile 改了卡片資料(jobs),或別的裝置/腳本改了標記(FB)。
  // 兩者都不用他重整。規則:他自己還沒存的那幾筆永遠不覆蓋,其餘跟伺服器對齊。
  // ---- 看板上的「跑」:跑準備區、找新職缺 ----
  // 按鈕叫本機伺服器啟動:準備區是 cut_tailor(agent 判版本與語言、產履歷、建可投遞包,跑完自己把卡
  // 送去「待你決定」),找缺是 converge(更深/更廣/指定方向,找到的放進「🆕 待評估」)。
  // 進度跟著 /api/rev 那個小包回來,不另開一條輪詢;有東西在跑的時候改成 4 秒問一次。
  var PREP_PH={start:'準備開跑',fetching_pages:'抓取職缺頁文字',checking_links:'抓取職缺頁文字',agent:AGENT+' 判版本與語言',reconcile:'整理要寄的檔案'};
  var FIND_PH={start:'準備開跑',agent:'agent 在找',fold:'清洗:去重、硬排除、抓 JD',judge:'逐張對照你的原話判斷'};
  var FIND_MODE={deep:'更深',wide:'更廣',dir:'指定方向',both:'更深＋更廣'};
  var FIND={}, _runTimer=null, _runDone={}, findDraft='';
  var MISC={};   // 貼網址加入(add)、分類建議(suggest)的進度
  function prepCount(){return jobs.filter(function(j){var f=FB[j.id];return f&&f.app==='prep'&&!removed(j.id);}).length;}
  function hhmm(t){if(!t)return ''; var d=new Date(t*1000);
    return (d.getMonth()+1)+'/'+d.getDate()+' '+('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);}
  function prepBarHTML(){
    var n=prepCount(), p=PREP||{}, st='';
    if(p.running){
      var mins=p.t0?Math.max(0,Math.floor((Date.now()/1000-p.t0)/60)):0;
      st='<span class="prep-st run">⏳ 正在跑：'+esc(PREP_PH[p.phase]||p.phase)+
         (p.phase==='agent'&&p.n?'　'+(p.done||0)+'/'+p.n:'')+'　· 已 '+mins+' 分鐘</span>';
    } else if(p.phase==='done'){
      // 舊版的進度檔沒記推了幾張(ready),用「這輪張數 − 沒產出的」
      var got=(p.ready!=null)?p.ready:Math.max(0,(p.n||0)-((p.missing||[]).length));
      st='<span class="prep-st ok">✅ 上一輪 '+hhmm(p.finished_at)+' 跑完：'+preparedTo(got)+
         (p.skipped?'，'+p.skipped+' 張沒產出（原因寫在卡上，還在準備履歷中）':'')+
         (p.techerr?'，'+p.techerr+' 張標成「出錯了」':'')+((p.missing&&p.missing.length)?'，'+p.missing.length+' 張沒產出來':'')+'</span>';
    } else if(p.phase==='incomplete'){
      st='<span class="prep-st bad">⚠ 上一輪部分完成：'+esc(p.msg||'有卡片這一輪沒判到，仍留在「準備履歷中」。')+'</span>'+'<button class="cfg-b" type="button" data-showlog="prep">看紀錄</button>';
    } else st=phaseSt(p,'prep');
    return '<div id="prepbar" class="prepbar'+(p.running?' run':'')+'">'+
      (p.running?'':runNHTML('prep',n))+
      '<button class="stage-b adv prep-go" data-prep="1" type="button"'+((p.running||!n)?' disabled':'')+
      ' title="'+AGENT+' 讀每張的 JD 判履歷版本與語言,再整理要寄的檔案,跑完'+(FLOW.auto_advance?'驗收過就自動進「可以投了」':'自己送去「待你決定」')+'">'+
      (p.running?'準備履歷中…':'▶ 準備履歷（'+runNOf('prep',n)+' 張）')+'</button>'+st+autoBarHTML('prep')+runCtlHTML('prep',p)+pvBtn('prep')+'</div>';
  }
  // 可投遞那一頁最上面:讓 agent 填表單(填好停在送出前)、送出已核准的。跟跑準備區同一個形狀。
  // 整批填表會填的、確認過可以送的:照下一步(fillable:投遞狀態准「讓 agent 填」;send:確認過的檢查清單)
  function applyFillN(){return jobs.filter(function(j){var m=FB[j.id]; return m&&m.app==='ship'&&!removed(j.id)&&!!nextOf(j.id).fillable;}).length;}
  function applyReadyN(){return jobs.filter(function(j){var m=FB[j.id];
    return m&&m.app==='ship'&&!removed(j.id)&&nextOf(j.id).send===null;}).length;}
  function applyBarHTML(){
    var n=applyFillN(), r=applyReadyN(), p=APPLY||{}, st='', what=p.stage==='submit'?'送出':'填表';
    if(p.running)st='<span class="prep-st run">⏳ '+AGENT+' 正在'+(p.stage==='submit'?'送出':'填')+(p.which?':'+esc(p.which):'')+
      (p.n>1?'　'+(p.done||0)+'/'+p.n:'')+'　· 已 '+minsOf(p)+' 分鐘</span>';
    else if(p.phase==='done'||(p.phase==='failed'&&p.results)){var rs=p.results||[], ok=rs.filter(function(x){return x.ok;}).length;
      st='<span class="prep-st '+(ok===rs.length?'ok':'bad')+'">上一輪'+what+'('+hhmm(p.finished_at)+'):'+ok+'/'+rs.length+' 張成功'+
        (ok<rs.length?',沒成功的原因寫在卡上':'')+'</span>';}
    else st=phaseSt(p,'apply');
    return '<div id="applybar" class="prepbar'+(p.running?' run':'')+'">'+
      (p.running?'':runNHTML('apply',n))+
      '<button class="stage-b adv" data-applyrun="fill" type="button"'+((p.running||!n)?' disabled':'')+
      ' title="'+AGENT+' 用它自己的瀏覽器把表單填好,停在送出前;你看過再按卡上的「✅ 確認送出」">'+
      (p.running&&p.stage!=='submit'?AGENT+' 填表中…':'▶ 讓 '+AGENT+' 填表單('+runNOf('apply',n)+' 張)')+'</button>'+
      (r&&!p.running?'<button class="stage-b adv" data-applyrun="submit" type="button">▶ 送出已確認的('+r+' 張)</button>':'')+st+autoBarHTML('ship')+runCtlHTML('apply',p)+pvBtn('apply')+'</div>';
  }
  function refreshApplyUI(){
    var b=$('applybar'); if(b){var x=document.createElement('div'); x.innerHTML=applyBarHTML(); b.replaceWith(x.firstElementChild);}
    renderTabs();
  }
  function refreshPrepUI(){
    var b=$('prepbar'); if(b){var x=document.createElement('div'); x.innerHTML=prepBarHTML(); b.replaceWith(x.firstElementChild);}
    renderTabs();
  }
  // 每一個會派 agent 的流程,跑著的時候都有這一組:⏸ 暫停(agent 停在原地,開著的頁面不動)、▶ 繼續、⏹ 停止。
  // 每一個派 agent 的流程都有暫停/繼續/停止。四處共用這一份,不各做一套。
  // 會收工的流程(p.graceful,目前是找新職缺):第一次按停止只停 agent、做完的收下;收工中再按一次才強制停。
  function runCtlHTML(kind,p){
    if(!p||!p.running)return '';
    if(p.finishing)return '<span class="runctl"><span class="prep-st">⏳ 收工中:把做完的收下</span>'+
      '<button class="stage-b" type="button" data-runctl="'+kind+':stop" data-force="1">⏹ 強制停止</button></span>';
    // 正在送出時沒有暫停(伺服器也擋):凍在按下送出的半路,他查不到送出去沒有(#308)
    var noPause=kind==='apply'&&p.stage==='submit';
    return '<span class="runctl">'+(p.paused?'<span class="prep-st bad">⏸ 暫停中</span>'+
      '<button class="stage-b" type="button" data-runctl="'+kind+':resume">▶ 繼續</button>':
      noPause?'':'<button class="stage-b" type="button" data-runctl="'+kind+':pause">⏸ 暫停</button>')+
      '<button class="stage-b" type="button" data-runctl="'+kind+':stop"'+(p.graceful?' data-graceful="1"':'')+'>⏹ 停止</button></span>';
  }
  // 「跑幾張」:每個派 agent 的流程最上面那排都有同一格,自己打數字,空著就是全部;主按鈕的張數邊打邊變。
  // 每一個派 agent 的流程都能選跑幾張;特定職缺在卡片 ⋯ 的「只跑這張」。
  // 預設一律是全部,不記上次打的;張數自己打數字,不是一長串下拉。
  // 只記在這一頁的記憶體裡,讓重畫畫面時不會清掉;重新整理就回到全部。
  var RUNN={};
  function runN(kind){return +RUNN[kind]||0;}
  function runNOf(kind,total){var n=runN(kind), v=n&&n<total?n:total;
    return '<span data-runn-n="'+kind+'" data-total="'+total+'">'+v+'</span>';}
  function runNInput(kind,max,ph,label){
    var cur=runN(kind);
    return '<input class="runn" type="number" inputmode="numeric" min="1"'+(max?' max="'+max+'"':'')+
      ' data-runn="'+kind+'" placeholder="'+escA(ph)+'" aria-label="'+escA(label)+'" value="'+(cur||'')+'">';
  }
  function runNHTML(kind,total){return total<2?'':runNInput(kind,total,'全部 '+total+' 張','跑幾張,空著是全部');}
  function likedAny(){return Object.keys(FB).some(function(k){var s=(FB[k]||{}).s; return s==='like'||s==='grow';});}
  function minsOf(p){return p.t0?Math.max(0,Math.floor((Date.now()/1000-p.t0)/60)):0;}
  // 四個執行列(準備、填表、找缺、查應徵進度)上一輪沒東西可跑/沒跑成/停掉/按了停止那幾種:一張表,各列只差字(冒號全形半形照原樣)
  function phaseSt(p,kind){
    var w={prep:['：','上一輪沒東西可跑：','上一輪跑到一半停掉了，沒跑完。可以再按一次。','要跑'],
           apply:[':','上一輪沒東西可跑:','上一輪跑到一半停掉了,沒跑完。可以再按一次。','要跑'],
           research:['：','上一輪沒有找：','上一輪跑到一半停掉了，沒跑完。可以再按一次。'],
           replies:[':','上一輪沒東西可查:','上一輪查到一半停掉了。可以再按一次。','要查']}[kind],
        tail=(kind==='replies'?replyRetryHTML(p):'')+'<button class="cfg-b" type="button" data-showlog="'+kind+'">看紀錄</button>';
    if(p.phase==='nothing')return '<span class="prep-st">'+w[1]+esc(p.msg||'')+'</span>';
    if(p.phase==='failed')return '<span class="prep-st bad">❌ 上一輪沒跑成'+w[0]+esc(p.msg||'')+'</span>'+tail;
    if(p.phase==='died')return '<span class="prep-st bad">⚠ '+w[2]+'</span>'+tail;
    if(p.phase==='stopped')return '<span class="prep-st">⏹ 上一輪你按了停止。'+w[3]+'再按一次。</span>';
    return '';}
  function findWhat(p){return (FIND_MODE[p.mode]||'')+(p.mode==='dir'&&p.direction?'「'+p.direction+'」':'');}
  // 照實講這輪:找到幾張、清掉幾張、進看板幾張(舊版的進度只有新增幾筆)
  function findTally(p){
    var add='進看板 '+(p.added||0)+' 張，在「🆕 新職缺」';
    if(p.found==null)return add;
    if(!p.found&&p.timeup)return '這輪沒找到，可以把時間放寬';
    return '找到 '+p.found+' 張、清掉 '+(p.dropped||0)+' 張、'+add;
  }
  function findStatusHTML(){
    var p=FIND||{};
    if(p.running)return '<span class="prep-st run">⏳ 正在找（'+esc(findWhat(p))+'）：'+esc(p.step||FIND_PH[p.phase]||p.phase)+'　· 已 '+minsOf(p)+' 分鐘'+
      (p.minutes?' / 最多找 '+p.minutes+' 分鐘':'')+'</span>'+runCtlHTML('research',p);
    if(p.phase==='done')return '<span class="prep-st ok">✅ 上一輪（'+esc(findWhat(p))+'）'+hhmm(p.finished_at)+' '+
      (p.timeup?'時間到':'找完')+'：'+findTally(p)+'</span>';
    if(p.phase==='stopped')return '<span class="prep-st">⏹ 上一輪你按了停止'+(p.graceful?':新增 '+(p.added||0)+' 筆'+
      (p.pending?',還有 '+p.pending+' 張找到沒判,下一輪先判':''):'')+'。要找再按一次。</span>';
    return phaseSt(p,'research');
  }
  // ---- 「📝 prompt」:每一顆會派 agent 出去的按鈕,旁邊都有同一顆,按下去會拿什麼去問,點開就看 ----
  // 三處(找新職缺、跑準備區、代投)共用同一顆按鈕、同一個位置、同一個彈窗,不各做一套。
  // 內容先在背景抓好放著,點開是即時的(以前是展開才去要,每次都頓一下);
  // 用看板既有的 modal() 蓋在上面,不把 8000 字塞進版面裡(以前一展開整頁被推下去)。
  var PVC={}, PVQ={}, PVOPEN=null;
  // 「這一份哪裡不是最終樣子」由伺服器隨著 prompt 一起回(note),顯示在最上面的醒目橫條。
  // 以前寫成灰字放在最底下,他看了代投那份就以為 prompt 被寫死成某一家公司。
  var PV_GROUP={
    find:{t:'🔎 找新職缺',tabs:[['find:deep','更深'],['find:wide','更廣'],['find:dir','照這個方向找'],['judge','逐張判斷']]},
    prep:{t:'📝 準備履歷',tabs:[['prep','準備履歷']]},
    apply:{t:'🚀 幫你填表',tabs:[['apply:fill','讓 '+AGENT+' 填表單'],['apply:fix','讓 '+AGENT+' 改'],['apply:submit','送出已確認的']]},
    replies:{t:'📬 查應徵進度',tabs:[['replies','查應徵進度']]}
  };
  function pvQuery(key){
    var p=key.split(':');
    if(key==='prep'||key==='judge'||key==='replies')return 'kind='+key;
    if(p[0]==='apply')return 'kind=apply&stage='+encodeURIComponent(p[1]);
    // 指定方向那一份會把他現在寫在框裡的那句帶進去:改了字再打開就是新的那一份
    var t=(key==='find:dir'?(findDraft||''):'');
    return 'kind=find&mode='+encodeURIComponent(p[1])+(t?'&text='+encodeURIComponent(t):'');
  }
  function pvGet(key){
    var q=pvQuery(key);
    if(PVC[key]&&PVC[key].q===q)return Promise.resolve(PVC[key]);
    if(PVQ[key]&&PVQ[key].q===q)return PVQ[key].pr;
    var pr=fetch('/api/prompt?'+q).then(function(r){return r.json();}).then(function(d){
      PVC[key]={q:q,p:d.prompt||'(空)',note:d.note||''}; delete PVQ[key]; return PVC[key];
    }).catch(function(){delete PVQ[key]; return {p:'(載入失敗,關掉再打開一次)',note:''};});
    PVQ[key]={q:q,pr:pr}; return pr;
  }
  function pvWarm(keys){   // 先抓起來放著:他點開的時候就不必等
    (window.requestIdleCallback||function(f){setTimeout(f,1200);})(function(){keys.forEach(pvGet);});
  }
  function pvBtn(group){
    return '<button class="pv-b" type="button" data-pvopen="'+group+'"'+
      ' title="這顆按鈕按下去,會拿什麼去問 agent" aria-label="看會送出去的 prompt">📝<span class="pv-t"> 看會送出去的 prompt</span></button>';
  }
  function pvFill(key){
    pvGet(key).then(function(c){
      if(!PVOPEN||PVOPEN.k!==key)return;
      var el=$('pv-pre'); if(el){el.textContent=c.p; el.scrollTop=0;}
      var n=$('pv-note'); if(n){n.textContent=c.note||''; n.style.display=c.note?'':'none';}});
  }
  function pvModal(group,key){
    var g=PV_GROUP[group]; if(!g)return;
    key=key||g.tabs[0][0]; PVOPEN={g:group,k:key};
    var tabs=g.tabs.length>1?'<div class="pv-tabs">'+g.tabs.map(function(t){
      return '<button class="pv-tab'+(t[0]===key?' on':'')+'" type="button" data-pvtab="'+escA(t[0])+'">'+
        esc(t[1])+'</button>';}).join('')+'</div>':'';
    modal('<div class="rzm-box pv-box"><button class="rzm-x" type="button">✕ 關掉</button>'+
      '<div class="pv-hd"><b>'+esc(g.t)+'</b><span>會送出去的 prompt</span>'+
        '<button class="pv-copy" type="button" data-pvcopy="1">複製</button></div>'+
      tabs+'<p class="pv-note" id="pv-note" style="display:none"></p>'+
      '<pre class="pv-pre" id="pv-pre">載入中…</pre></div>','pvm');
    pvFill(key);
    pvWarm(g.tabs.map(function(t){return t[0];}));   // 同一組其他頁也先抓好,切過去不頓
  }
  // ⋯ 選單:點開、點外面關、Esc 關、一次只開一個。選單本身在卡片/公司列自己的 DOM 裡,
  // 所以裡面那幾顆照原本的處理器走,不用為了搬位置重接一次線。
  function closeMore(except){
    document.querySelectorAll('.more-m:not([hidden])').forEach(function(m){
      if(m!==except)m.hidden=true;});
  }
  document.addEventListener('click',function(e){
    var blb=e.target.closest&&e.target.closest('[data-blocklist]');
    if(blb){openBlockList(); return;}
    var gt=e.target.closest&&e.target.closest('[data-gotab]');
    if(gt){switchTab(gt.getAttribute('data-gotab')); return;}
    var mb=e.target.closest&&e.target.closest('[data-omore]');
    if(mb){e.preventDefault(); e.stopPropagation();
      var m=mb.parentNode.querySelector('.more-m');
      var willOpen=m.hidden; closeMore(m); m.hidden=!willOpen; return;}
    // 選了裡面某一顆:動作照舊由原本的處理器做,這裡只負責把選單收起來
    if(e.target.closest&&e.target.closest('.more-m')){closeMore(); return;}
    closeMore();
  },true);
  document.addEventListener('keydown',function(e){ if(e.key==='Escape')closeMore(); });
  // 新履歷、新附件的名稱格:按 Enter 等於按旁邊的「＋ 新增」(以前 Enter 沒反應,一定要點按鈕)
  document.addEventListener('keydown',function(e){
    if(e.key!=='Enter'||e.isComposing||!e.target||!/^cfg-(rnew|anew)$/.test(e.target.id))return;
    var b=document.querySelector(e.target.id==='cfg-rnew'?'[data-cfresadd]':'[data-cfattadd]');
    if(b){e.preventDefault(); b.click();}
  });

  document.addEventListener('click',function(e){
    // 這幾顆長在 #findbar 裡(不在 #app),處理器一定要掛在 document 上。
    // 掛在 #app 上的話,「指名要找的」刪不掉、agent 開關按了沒反應。
    var _sx=e.target.closest&&e.target.closest('[data-seeddel]');
    if(_sx){var kv=_sx.getAttribute('data-seeddel'), i=kv.indexOf('|');
      seedDel(kv.slice(0,i),kv.slice(i+1)); return;}
    var _af=e.target.closest&&e.target.closest('[data-afree]');
    if(_af){e.preventDefault(); toggleAgentFree(); return;}
    var o=e.target.closest&&e.target.closest('[data-pvopen]');
    if(o){pvModal(o.getAttribute('data-pvopen')); return;}
    var t=e.target.closest&&e.target.closest('[data-pvtab]');
    if(t&&PVOPEN){
      var k=t.getAttribute('data-pvtab'); PVOPEN.k=k;
      document.querySelectorAll('#rzmodal [data-pvtab]').forEach(function(b){
        b.classList.toggle('on',b.getAttribute('data-pvtab')===k);});
      var el=$('pv-pre'); if(el)el.textContent='載入中…';
      var n0=$('pv-note'); if(n0){n0.textContent=''; n0.style.display='none';}
      pvFill(k); return;}
    if(e.target.closest&&e.target.closest('[data-pvcopy]')){
      var pre=$('pv-pre'); if(!pre)return;
      var done=function(){snack('prompt 複製好了');};
      if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(pre.textContent).then(done,function(){});
      else{var r=document.createRange(); r.selectNodeContents(pre);
        var s=getSelection(); s.removeAllRanges(); s.addRange(r); try{document.execCommand('copy'); done();}catch(_e){}}
    }
  });
  // 三種找法是他自己歸納的:更深、更廣、指定方向(converge.py 的 --mode)。一次只跑一輪。
  // 版面跟「跑準備區」「代投」同一個元件(.prepbar):按鈕 → 狀態 → 📝 prompt,三處長一樣、排一樣、
  // 說明一律放 title。以前這裡是另一套(摺疊面板、每個選項一行、說明文字掛旁邊),他要統一。
  // 這一條在每一頁都看得到(找缺跟哪一個分頁都沒關係),所以預設收成一行,
  // 跟隔壁的「📣 回報」「📄 你的履歷」同一個形狀。以前攤開放在最上面:
  // 手機上量到 336px,螢幕才 812px,等於每一頁先被它吃掉四成,他說「看了好躁」。
  // 正在跑的時候狀態直接寫在收起來的那一行,不必展開也知道跑到哪。
  // 找新職缺不是以卡為單位:能選的是「這一輪最多判幾張」(判越多越久)。指定特定職缺用卡片上的「找類似的」。
  // 找的那一段最多跑幾分鐘:跟「全部 N 張」同一個長相;空著 = 不限時(agent 自己決定找多久)。
  // 改了就存進設定(跨重整、跨裝置都一樣),同一格同時是「這輪」也是「以後」,設定頁不另外放。
  var FINDMIN=+CFG.find_minutes||0;
  function findNHTML(){
    return runNInput('research',0,'判幾張:全部','最多判幾張,空著是全部')+
      '<input class="runn" type="number" inputmode="numeric" min="1" max="999" data-findmin placeholder="不限時"'+
      ' title="找的那一段最多跑幾分鐘;空著是不限時(AI 自己決定)。時間到就停止找新的,找到的照樣判完進看板"'+
      ' aria-label="找幾分鐘,空著是不限時" value="'+(FINDMIN||'')+'"><span class="runn-u">分鐘</span>';
  }
  function findPanelHTML(){
    var run=!!FIND.running, dis=run?' disabled':'', n=seedList().length;
    var sum='🔎 找新職缺'+
      (run?'<span class="find-chip">⏳ '+esc(findWhat(FIND))+'・'+minsOf(FIND)+' 分鐘</span>'
          :(n?'<span class="n">指名 '+n+' 個待找</span>':''));
    return '<div id="findrow">'+fold('find',sum,
      '<div class="findbody">'+
        '<div class="findrow1">'+
          '<button class="stage-b adv" data-find="deep" type="button"'+(dis||(likedAny()?'':' disabled'))+
            ' title="'+(likedAny()?'照你按過喜歡、差一點的,挖同一間公司的其他缺、別家的同型缺':'還沒有按過 👍 喜歡或 💪 差一點的卡,更深沒有公司可以挖;先用「更廣」')+'">▶ 更深</button>'+
          '<button class="stage-b adv" data-find="wide" type="button"'+dis+
            ' title="找板上還沒出現過的職能,讓你有機會表態">▶ 更廣</button>'+
        '</div>'+
        '<div class="findrow1 dir">'+
          '<textarea id="find-dir" class="find-in" rows="1" maxlength="300" placeholder="想往哪個方向挖？寫一句就好">'+esc(findDraft)+'</textarea>'+
          '<button class="stage-b adv" data-find="dir" type="button"'+dis+'>▶ 照這個方向找</button>'+
        '</div>'+
        '<div id="find-st" class="find-st">'+findStatusHTML()+'</div>'+
        '<div class="findrow1 dir">'+
          '<textarea id="add-urls" class="find-in" rows="1" placeholder="自己找到的職缺:貼網址(一次可以貼好幾個)"></textarea>'+
          '<button class="stage-b adv" data-addurls="1" type="button"'+((MISC.add||{}).running?' disabled':'')+'>'+
            ((MISC.add||{}).running?'⏳ 加入中…':'➕ 加進新職缺')+'</button>'+
        '</div>'+
        seedRowsHTML()+
        '<div class="findset">'+findNHTML()+agentFreeHTML()+pvBtn('find')+'</div>'+
        fold('findmore','找過幾輪 · 各找法的成績',
          '<div id="find-score">'+findScoreHTML()+'</div><div id="find-rounds">'+findRoundsHTML()+'</div>',
          {cls:'pb-morefold'})+
      '</div>',{id:'finddet',cls:'cuts-d find-d',hcls:'cuts-sum'})+'</div>';
  }
  // 每種找法的成效:找進板的職缺裡,你看過幾張、喜歡幾張(含加進準備區的)、不喜歡幾張。
  // 靠每張新缺記的出處(j.src)算;以前沒記,那一批歸在「更早進板」。
  // 最近幾輪:往哪找、判了幾張、送進板幾張、你後來怎麼表態、那輪 agent 自己寫的方向。
  // 指定方向的那幾輪可以直接說「別再往這找」(FB.__research__.mute),下一輪找缺的 agent 會看到。
  function findRoundsHTML(){
    var rs=(D.research||[]).slice(-5).reverse(); if(!rs.length)return '';
    var mute=((FB['__research__']||{}).mute)||[];
    return '<div class="find-rounds"><div class="find-rh">最近幾輪</div>'+rs.map(function(r){
      var seen=0,like=0,dis=0;
      (r.added||[]).forEach(function(u){var f=FB[u]||{};
        if(f.s||f.app||f.rm)seen++; if(f.app||f.s==='like'||f.s==='grow')like++; else if(f.s==='dislike')dis++;});
      var what=(FIND_MODE[r.mode]||r.mode)+(r.direction?'「'+r.direction+'」':''), muted=!!r.direction&&mute.indexOf(r.direction)>=0;
      return '<div class="find-r"><div class="find-r1"><b>'+esc(what)+'</b><span class="find-rt">'+esc(r.round||'')+'</span></div>'+
        '<div class="find-r2">判了 '+(r.judged||0)+' 張、送進板 '+(r.added||[]).length+' 張;你看過 '+seen+'、喜歡 '+like+'、不喜歡 '+dis+
        (r.direction?'<button class="cust-b '+(muted?'take':'ask')+'" type="button" data-fmute="'+escA(r.direction)+'">'+
          (muted?'✓ 說過別再找':'別再往這找')+'</button>':'')+'</div>'+
        (r.notes?fold('frn:'+(r.round||''),'那輪 agent 自己寫的','<div>'+esc(r.notes)+'</div>',{cls:'find-rn'}):'')+'</div>';
    }).join('')+'</div>';
  }
  function findScoreHTML(){
    var g={}, order=[];
    jobs.forEach(function(j){
      var s=j.src, k=s?(s.mode==='dir'?'dir:'+(s.direction||''):(s.mode||'?')):'old';
      if(!g[k]){g[k]={n:0,seen:0,like:0,dis:0};order.push(k);}
      var f=FB[j.id]||{}, t=g[k]; t.n++;
      if(f.s||f.app||f.rm)t.seen++;
      if(f.app||f.s==='like'||f.s==='grow')t.like++; else if(f.s==='dislike')t.dis++;});
    if(order.length===1&&order[0]==='old')return '';
    order.sort(function(a,b){return (a==='old')-(b==='old')||g[b].n-g[a].n;});
    function name(k){return k==='old'?'更早進板（沒記出處）':k.indexOf('dir:')===0?'方向「'+k.slice(4)+'」':(FIND_MODE[k]||k);}
    return '<table class="fn-t find-t"><thead><tr><th>找法</th><th>進板</th><th>你看過</th><th>喜歡</th><th>不喜歡</th></tr></thead><tbody>'+
      order.map(function(k){var t=g[k];
        return '<tr><td>'+esc(name(k))+'</td><td>'+t.n+'</td><td>'+t.seen+'</td><td>'+t.like+'</td><td>'+t.dis+'</td></tr>';}).join('')+
      '</tbody></table>';
  }
  function renderFind(){
    var fb=$('findbar'); if(!fb)return; fb.innerHTML=findPanelHTML();
  }
  // 進度一直在變,但他可能正在打方向:只換狀態字與按鈕,不重畫輸入框(游標與打到一半的字都留著)。
  function refreshFindUI(){
    var st=$('find-st'); if(!st){renderFind();return;}
    st.innerHTML=findStatusHTML();
    var row=$('findrow'); if(row)row.classList.toggle('run',!!FIND.running);
    document.querySelectorAll('#findrow [data-find]').forEach(function(b){b.disabled=!!FIND.running;});
  }
  // 伺服器帶回新的進度。準備區跑起來、跑完的那一下整頁重畫(卡片上的字要換),中間只換最上面那條。
  function takeRuns(v){
    ['prep','research','apply'].forEach(function(k){
      var p=v[k]; if(!p)return;
      var cur=(k==='prep')?PREP:(k==='apply'?APPLY:FIND);
      if(!p.running&&JSON.stringify(p)===JSON.stringify(cur))return;   // 沒在跑又沒變:不動
      var was=!!cur.running;
      if(k==='prep')PREP=p; else if(k==='apply')APPLY=p; else FIND=p;
      if(was&&!p.running)_runDone[k]=true;
      if(k==='apply'&&was&&!p.running){   // 填好了:通知裡直接給「👀 看頁面」,不用他去找那張卡
        var ready=jobs.filter(function(j){return !!(nextOf(j.id).view||{}).review;});   // 填好停著等你看的(照下一步)
        if(ready.length)snack(AGENT+' 填好了 '+ready.length+' 張,等你看過再確認送出',null,
          {label:'👀 看頁面',fn:function(){openShot(liveHref(ready[0].id),ready[0].id);}});}
      if(k==='prep'||k==='apply'){ if(was!==!!p.running)renderAll(); else if(k==='prep')refreshPrepUI(); else refreshApplyUI(); }
      if(k==='apply')renderFillList();
      else refreshFindUI();
    });
    var rp=v.replies;
    if(rp&&(rp.running||JSON.stringify(rp)!==JSON.stringify(REPLIES))){var rwas=!!REPLIES.running; REPLIES=rp;
      if(rwas&&!rp.running)_runDone.replies=true;
      if(rwas!==!!rp.running)renderAll(); else refreshRepliesUI();}
    // 貼網址加入、分類建議、客製:跑完講一聲;客製也要更新卡片上的檔案狀態。
    ['add','suggest','customize'].forEach(function(k){var p=v[k]; if(!p)return; var cur=MISC[k]||{};
      if(!p.running&&JSON.stringify(p)===JSON.stringify(cur))return;
      var was=!!cur.running; MISC[k]=p; if(k==='add')renderFind();
      if(was&&!p.running){
        var ok=p.phase==='done';
        if(k==='add')snack(ok?(p.msg||'加好了'):('貼網址加入沒做完:'+(p.msg||'')),null,
          ok?{label:'去看',fn:function(){switchTab('none');}}:{label:'看紀錄',fn:function(){showLog('add');}});
        else if(k==='suggest') {snack(ok?'分類建議好了,在「⚙ 設定 → 🗂 分類」':('分類建議沒做完:'+(p.msg||'')),null,
          ok?null:{label:'看紀錄',fn:function(){showLog('suggest');}}); if(active==='cfg')cfgLoad(renderCfg);}
        else _runDone.customize=true;
        srvGen=null;
      } else if(k==='customize'&&was!==!!p.running)renderAll();
      else if(active==='cfg'&&k==='suggest'&&CFGD)renderCfg();
    });
    clearTimeout(_runTimer); _runTimer=null;
    if(PREP.running||FIND.running||APPLY.running||REPLIES.running||(MISC.add||{}).running||(MISC.suggest||{}).running||(MISC.customize||{}).running)_runTimer=setTimeout(function(){_runTimer=null; syncFromServer();},4000);
  }
  // 按之前先把還沒送出的標記存完:cut_tailor 讀的是伺服器上的檔,他剛加進準備區、
  // 還在自動存計時裡的那幾張不先存,這一輪就會漏掉。
  function whenSaved(){
    flushSave(false);
    return new Promise(function(res){var n=0;(function w(){
      if(!saving&&unsavedCount()===0)return res(true);
      if(saveErr||n++>60)return res(false);
      setTimeout(w,100);})();});
  }
  var _resumePromptCancel=null;
  function askResumeText(message,onSubmit,onCancel,buttonLabel){
    var done=false, textId='resume-paste-text';
    function cancel(){if(done)return;done=true;_resumePromptCancel=null;onCancel&&onCancel();}
    _resumePromptCancel=cancel;
    modal('<div class="rzm-box" style="max-width:760px;padding:24px;box-sizing:border-box">'+
      '<button class="rzm-x" type="button">✕ 關閉</button><h2>貼上履歷內容</h2><p>'+esc(message)+'</p>'+
      '<textarea id="'+textId+'" class="cfg-in big" rows="14" style="width:100%;box-sizing:border-box" placeholder="貼上履歷文字"></textarea>'+
      '<div class="cfg-row end"><button class="cfg-b" id="resume-paste-cancel" type="button">取消</button>'+
      '<button class="cfg-b go" id="resume-paste-submit" type="button">'+esc(buttonLabel||'繼續')+'</button></div></div>','');
    var area=$(textId), submit=$('resume-paste-submit'), cancelBtn=$('resume-paste-cancel');
    if(cancelBtn)cancelBtn.addEventListener('click',closeModal);
    if(submit)submit.addEventListener('click',function(){
      var text=area?area.value.trim():'';
      if(!text){snack('請先貼上履歷內容');return;}
      done=true;_resumePromptCancel=null;closeModal();onSubmit(text);
    });
  }
  function startRun(kind,body,btn){
    function back(){ if(kind==='prep')refreshPrepUI(); else if(kind==='apply'||kind==='replies'||kind==='customize')renderAll(); else refreshFindUI(); }
    if(btn){btn.disabled=true; btn.textContent='送出中…';}
    whenSaved().then(function(ok){
      if(!ok){snack('有改動還沒存進去，先等存好再按'); back(); return;}
      return fetch('/api/run/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})})
        .then(function(r){
          if(r.status===403){snack('這個看板不收 agent 的操作'); back(); return;}
          return r.json().then(function(st){
            if(r.status===400){snack(st.msg||'沒送出去'); back(); return;}
            if(r.status===422&&st.paste_resume){askResumeText(st.msg,function(text){
              startRun(kind,Object.assign({},body||{},{resume_text:text}),btn);},back,
              kind==='suggest'?'繼續建議':'繼續找缺');return;}
            if(r.status===409){snack(st.needs_browser||st.other?st.msg:(kind==='apply'?AGENT+' 還在跑上一輪,跑完再按卡上的「▶ 送出」':'正在跑,等它跑完')); back(); return;}
            var v={}; v[kind]=st; takeRuns(v);
            if(kind==='research')renderFind();});});   // 按鈕字從「送出中…」換回來
    }).catch(function(){snack('沒送出去，再按一次'); back();});
  }
  var srvGen=null, srvRev=null, syncing=false;
  function pullJobs(d){
    if(!d.jobs||!d.jobs.length)return false;
    jobs=d.jobs; LAZY={}; takeShip(d.ship_files);
    if(d.status)D.status=d.status;   // 投遞前驗收也會重跑;只換職缺不換它,卡上的 ⛔ 會停在舊的那一次
    if(d.research)D.research=d.research;   // 找缺那幾輪的紀錄(🔎 面板「最近幾輪」)
    return true;
  }
  function pullFB(serverFB){
    var changed=false;
    Object.keys(serverFB).forEach(function(k){
      if(dirty[k])return;                                  // 他手上還沒存的,不動
      if(JSON.stringify(FB[k])===JSON.stringify(serverFB[k]))return;
      FB[k]=serverFB[k]; SAVED[k]=JSON.parse(JSON.stringify(serverFB[k])); changed=true;
    });
    Object.keys(FB).forEach(function(k){
      if(dirty[k]||serverFB.hasOwnProperty(k)||k.indexOf('__')===0)return;  // __notes__/__cuts__ 是本機預設,沒存過不能刪
      delete FB[k]; delete SAVED[k]; changed=true;          // 別處刪掉的也跟著刪
    });
    if(changed)$('data-fb').textContent=JSON.stringify(FB);
    return changed;
  }
  // 清掉答案之後等拿回後台的版本:正在存或別的輪詢在跑就等一下再拿(最多 n 次)
  function redoSynced(f,n){srvRev=null;
    syncFromServer().then(function(){if((srvRev===null||saving)&&n>0)setTimeout(function(){redoSynced(f,n-1);},400); else f();});}
  function syncFromServer(back){
    if(!canSave||saving||syncing)return Promise.resolve(false);
    syncing=true; var ep=saveEpoch;
    // 輪詢先問一個小包(rev/gen),真的變了才去拿大的。以前每 20 秒抓一次 /api/jobs
    // 只為了讀 gen,那一包 1.26MB——他手機上一分鐘白流 3.8MB,還要解析同樣大小的 JSON。
    // null 的意思是「不知道伺服器上是哪一版,一定要拿」。以前寫成「null 就不比」,
    // 衝突之後設成 null 想逼它重拿,結果反而跳過:那張卡的伺服器版本永遠拿不回來,
    // 他之後每改一次都再被擋一次、改的東西再被丟一次,直到重整。
    // back:他剛切回這個分頁(可能剛在別的程式改完母稿),請伺服器順便比對原稿、變了就在背景先建材料
    return fetch(back?'/api/rev?sync=1':'/api/rev').then(function(r){return r.json();}).then(function(v){
      if(v&&v.err==='tampered'){takeTampered(v.msg); syncing=false; return false;}
      if(tampered){if(saveErr===tampered)saveErr=''; tampered=''; refreshBar();}   // 他處理好了
      var needJobs=(srvGen===null||v.gen!==srvGen), needFB=(srvRev===null||v.rev!==srvRev);
      srvGen=v.gen; srvRev=v.rev;
      takeRuns(v);   // 跑的時候每次都更新(「已 N 分鐘」要走);沒在跑就只在有變的時候
      if(!needJobs&&!needFB){syncing=false; runDoneNote(); return false;}
      var chain=Promise.resolve(false);
      if(needJobs)chain=chain.then(function(did){
        return fetch('/api/jobs').then(function(r){return r.json();}).then(function(d){
          return pullJobs(d)||did;});});
      if(needFB)chain=chain.then(function(did){
        return fetch('/api/state').then(function(r){return r.json();}).then(function(fb){
          // 這包是輪詢開始時拿的;如果在等它的時候他剛好存了檔(或還在存),它就是存檔之前的舊版,
          // 照它去覆蓋剛存好的東西會讓他剛改的答案退回去。丟掉,srvRev=null 讓下一次輪詢重拿。
          if(saving||ep!==saveEpoch){srvRev=null; return did;}
          // 別處改了標記(別的裝置按了版本、收下客製版…):要寄的檔案也整批重拿
          return pullFB(fb)?refreshShip().then(function(){return true;}):did;});});
      // 卡或驗收結果變了(背景改了卡、驗收跑完):每張卡的下一步也重拿,不用他重新整理(#341)
      chain=chain.then(function(did){
        return fetch('/api/next').then(function(r){return r.json();}).then(function(n){
          if(saving||ep!==saveEpoch||!n||typeof n!=='object')return did;   // 存檔途中拿的是存之前的,存檔回來會帶新的
          var before=JSON.stringify(NEXT); NEXT=n; return did||JSON.stringify(NEXT)!==before;});});
      return chain.then(function(did){syncing=false; if(did)renderAll(); runDoneNote(); return did;});
    }).catch(function(){syncing=false;return false;});
  }
  // 跑完的那一下:等卡片資料拉下來之後才講,「去看」點下去才看得到東西。
  function runDoneNote(){
    if(_runDone.prep){ _runDone.prep=false; var p=PREP||{};
      if(p.phase==='done')snack('履歷準備好了：'+preparedTo((p.ready!=null)?p.ready:(p.n||0)),null,{label:'去看',fn:function(){switchTab('ready');}});
      else if(p.phase==='failed'||p.phase==='died')snack('準備履歷沒跑完，詳情在「準備履歷中」最上面');}
    if(_runDone.apply){ _runDone.apply=false; var a=APPLY||{}, rs=a.results||[], ok=rs.filter(function(x){return x.ok;}).length;
      if(a.phase==='done')snack(a.stage==='submit'?('送出 '+ok+'/'+rs.length+' 張成功'+(ok?',已搬到「已投出」':'')):
        (AGENT+' 填好 '+ok+'/'+rs.length+' 張,看過沒問題就按卡上的「✅ 確認送出」'),null,{label:'去看',fn:function(){switchTab(a.stage==='submit'&&ok?'sent':'ship');}});
      else if(a.phase==='failed'||a.phase==='died')snack(AGENT+' 沒跑完,詳情在「可以投了」最上面');}
    if(_runDone.research){ _runDone.research=false; var f=FIND||{};
      if(f.phase==='done')snack((f.timeup?'找缺時間到：':'找缺完成：')+findTally(f),null,{label:'去看',fn:function(){switchTab('none');}});
      else if(f.phase==='nothing')snack('這輪沒有找：'+(f.msg||''));
      else if(f.phase==='failed'||f.phase==='died')snack('找缺沒跑完，詳情在「🔎 找新職缺」');}
    if(_runDone.replies){ _runDone.replies=false; var q=REPLIES||{};
      if(q.phase==='done'||q.phase==='incomplete')snack((q.phase==='done'?'查應徵進度查完了:':'查應徵進度部分完成:')+(q.msg||'沒有新回音'),null,
        {label:'去看',fn:function(){switchTab('sent');}});
      else if(q.phase==='failed'||q.phase==='died')snack('查應徵進度沒跑完,詳情在「已投出」最上面');}
    if(_runDone.customize){ _runDone.customize=false; var c=MISC.customize||{};
      var ok=c.phase==='done'&&!c.msg&&c.done===c.n;
      snack(ok?'客製完成，等你檢查':('客製沒完成：'+(c.msg||'卡片上有原因')),null,
        {label:'去看',fn:function(){switchTab('ship');}});}
  }
  // 平常每 20 秒對一次;分頁切回前景時立刻對一次(手機擱著回來就是最新的)。
  setInterval(syncFromServer,20000);
  document.addEventListener('visibilitychange',function(){if(!document.hidden)syncFromServer(true);});
  syncFromServer();

  var buildGen=null, watching=false;
  function watchBuild(){
    if(watching)return; watching=true;
    var tries=0;
    $('savebar-lbl').textContent='背景整理要寄的檔案…';
    (function poll(){
      tries++;
      if(tries>120){watching=false;refreshBar();return;}   // 最多等 6 分鐘
      fetch('/api/jobs').then(function(r){return r.json();}).then(function(d){
        if(buildGen===null)buildGen=d.gen;
        if(d.building||d.gen===buildGen){setTimeout(poll,3000);return;}
        buildGen=d.gen; watching=false;
        srvGen=null;                       // 逼下一次 sync 把新的卡片資料拉下來
        syncFromServer().then(function(){refreshBar('ok');});
      }).catch(function(){watching=false;refreshBar();});
    })();
  }

  $('tabs').addEventListener('click',function(e){var t=e.target.closest('.tab');
    if(!t)return; switchTab(t.getAttribute('data-tab'));
    // 手機上分頁籤是單列橫捲的,按到半露的那個要把它捲進來,不然下次找不到。
    // 同樣只能動分頁列自己的橫向捲軸(理由見 centerTab)。
    centerTab();});
  // 自己畫的下拉:永遠貼著按鈕下緣開,點外面或 Esc 關掉。
  var CTL_ITEMS={}, _menuFor=null;
  function closeMenu(){var m=$('ctlmenu'); if(m)m.remove(); _menuFor=null;}
  function openMenu(btn){
    var name=btn.getAttribute('data-ctl'), items=CTL_ITEMS[name]||[];
    var cur={range:rangeDays,open:openDays,cat:activeCat,tag:activeFacet,sort:sortBy}[name];
    closeMenu();
    var m=document.createElement('div'); m.id='ctlmenu'; m.className='ctlmenu';
    m.innerHTML=items.map(function(o){
      var on=String(o[0])===String(cur);
      return '<button type="button" class="ctlopt'+(on?' on':'')+'" data-val="'+esc(String(o[0]))+'">'+
        '<span>'+esc(o[1])+'</span>'+(o[2]===undefined||o[2]===''?'':'<b>'+o[2]+'</b>')+'</button>';}).join('');
    document.body.appendChild(m);
    var r=btn.getBoundingClientRect();
    m.style.left=Math.max(8,Math.min(r.left,window.innerWidth-m.offsetWidth-8))+'px';
    m.style.top=(r.bottom+6)+'px';                    // 一律往下開
    var over=(r.bottom+6+m.offsetHeight)-(window.innerHeight-8);
    if(over>0)m.style.maxHeight=Math.max(160,m.offsetHeight-over)+'px';   // 空間不夠就自己捲,不翻上去
    _menuFor=name;
  }
  document.addEventListener('click',function(e){
    if(e.target.closest('#ctlmenu')||e.target.closest('[data-ctl]'))return;
    closeMenu();
  });
  document.addEventListener('keydown',function(e){ if(e.key==='Escape')closeMenu(); });
  window.addEventListener('resize',closeMenu);
  document.addEventListener('click',function(e){
    var opt=e.target.closest('#ctlmenu .ctlopt'); if(!opt)return;
    applyCtl(_menuFor,opt.getAttribute('data-val')); closeMenu();
  });
  function applyCtl(k,v){
    userFiltered=true;
    if(k==='range')rangeDays=parseInt(v,10)||0;
    else if(k==='open')openDays=parseInt(v,10)||0;
    else if(k==='cat')activeCat=v;
    else if(k==='tag'){activeFacet=v; if($('facetf'))renderFacetInd();}
    else if(k==='sort')sortBy=v;
    try{localStorage.setItem('sts_range',String(rangeDays));localStorage.setItem('sts_open',String(openDays));
        localStorage.setItem('sts_sort',sortBy);}catch(e2){}
    renderApp(); saveView();
  }
  // 卡上換履歷版本(resume_id)/換語言(lang):同一套
  function switchFile(id,field,val,label){
    var what=field==='lang'?'語言':'履歷';
    // 按的是原本就亮著的那顆(沒自己選過時亮的是 agent 判的):什麼都沒變。比實際生效的選擇,
    // 以前語言只比他自己選過的,點了亮著的那顆也會取消確認送出、叫 agent 整張重填
    if((pickOf(jobOf(id)||{id:id})||{})[field==='lang'?'lang':'variant']===val)return;
    if(dsBusyWhy(id)){snack(dsBusyWhy(id)); return;}
    var set={}; set[field]=val;
    // 後台自己看要寄的檔案真的變了沒,變了已填好的頁標「上傳的是舊檔」(#341);存好的卡回來才畫。
    // 誤觸要能當場反悔:換回來時,這張沒被別處動過就整張放回;已經開始重填了就算又換了一次檔(修正 12)
    press(id,null,set,what+'切成「'+label+'」',{then:function(){askShip(id);},undone:function(){askShip(id);},
      fallback:{ev:'files_changed',data:{why:what+'換回來了'}}});
  }
  $('app').addEventListener('click',function(e){
    if(e.target.closest('[data-rvstart]')){rvStart(); return;}
    var atk=e.target.closest('[data-autotake]');
    if(atk){var stg=atk.getAttribute('data-autotake'), A=FB['__auto__']; if(!A)return;
      var prevSkip=(A.skip||[]).slice(), take=autoOld(stg);
      A.skip=prevSkip.filter(function(i){return take.indexOf(i)<0;});
      dirty['__auto__']=true; renderAll(); refreshBar(); scheduleSave(300);
      snack('交給自動了:'+(prevSkip.length-A.skip.length)+' 張',function(){A.skip=prevSkip; dirty['__auto__']=true; renderAll(); refreshBar(); scheduleSave(300);});
      return;}
    if(e.target.closest('[data-riskrm]')){riskRemove(); return;}
    if(e.target.closest('[data-mehrm]')){bulkRemove(mehOld(LASTLIST).filter(function(j){return !justMarked[j.id];}).map(function(j){return j.id;}),'放太久的「普通」'); return;}
    var clr=e.target.closest('[data-closedrm]'); if(clr){bulkRemove(closedIn(clr.getAttribute('data-closedrm')),'已下架的缺'); return;}
    if(e.target.closest('[data-rvexit]')){rvExit(); return;}
    if(e.target.closest('[data-rvlast]')){rvBackToLast(); return;}
    var rva=e.target.closest('[data-rvat]'); if(rva){rvAt(+rva.getAttribute('data-rvat')); return;}
    var rvg=e.target.closest('[data-rvgo]'); if(rvg){if(!rvg.disabled)rvGo(+rvg.getAttribute('data-rvgo')); return;}
    var vdb=e.target.closest('.vd-b[data-vd]');   // 版本那組;語言鈕(.lg)另外處理
    if(vdb){switchFile(vdb.getAttribute('data-vd'),'resume_id',vdb.getAttribute('data-vv'),vdb.textContent.trim()); return;}
    // 語言由使用者最終決定,存進他的標記裡(agent 的判斷只是預設值)
    var lgb=e.target.closest('.vd-b.lg');
    if(lgb){switchFile(lgb.getAttribute('data-lg'),'lang',lgb.getAttribute('data-lv'),lgb.textContent.trim()); return;}
    var _cl=e.target.closest('.srv.clamp');
    if(_cl){_cl.classList.toggle('open'); return;}
    // ---- 🎤 面試準備 ----
    var ivpk=e.target.closest('[data-ivpk]');
    if(ivpk){var ivrow=ivpk.closest('[data-iv]'), ivid=ivrow.getAttribute('data-iv'), ivit=ivItem(ivid); if(!ivit)return;
      IVPICK[ivid]=parseInt(ivpk.getAttribute('data-ivpk'),10);
      // 只換這一題裡面,不重畫整頁:他的手指還停在這一題上
      ivrow.querySelector('.iv-pick').outerHTML=ivPickHTML(ivit);
      ivrow.querySelector('.iv-say').innerHTML=ivBodyHTML(ivit);
      var ivd=ivrow.querySelector('[data-ivdur]'); if(ivd)ivd.outerHTML=ivDurHTML(ivit,'iv-dur');
      return;}
    var ivr=e.target.closest('[data-ivread]'); if(ivr){openRead(ivr.closest('[data-iv]').getAttribute('data-iv')); return;}
    if(e.target.closest('[data-ivclr]')){ivQuery=''; syncQ(); applyFilterUI(); renderApp(); return;}
    var ivg=e.target.closest('[data-ivgo]');
    if(ivg){switchTab('iv'); return;}
    var _spv=e.target.closest('[data-srcpv]');   // 按著 ⌘/Ctrl 點附件還是照瀏覽器的另開分頁
    if(_spv&&!e.metaKey&&!e.ctrlKey){e.preventDefault(); previewPage(_spv.getAttribute('data-srcpv')); return;}
    var _ro=e.target.closest('.rz-open'); if(_ro){openResume(_ro.getAttribute('data-rz'),_ro.getAttribute('data-var')); return;}
    if(e.target.closest('[data-openfind]')){FOLD['find']=1; renderAll(); var fr=$('findrow'); if(fr)fr.scrollIntoView({block:'start'}); return;}
    var ca=e.target.closest('[data-clearall]');
    if(ca){rangeDays=0;openDays=0;activeCat='all';activeFacet='';searchQuery='';userFiltered=false;
      if($('q'))$('q').value='';
      try{localStorage.setItem('sts_range','0');localStorage.setItem('sts_open','0');}catch(e5){}
      renderApp(); return;}
    var ctl=e.target.closest('[data-ctl]');
    if(ctl){ if(_menuFor===ctl.getAttribute('data-ctl'))closeMenu(); else openMenu(ctl); return;}
    var cc=e.target.closest('.catchip'); if(cc){activeCat=cc.getAttribute('data-cat'); userFiltered=true; renderApp(); saveView(); return;}
    var _fg=e.target.closest('[data-f]'); if(_fg){var fv=_fg.getAttribute('data-f'); activeFacet=(activeFacet===fv?'':fv); userFiltered=true; if($('facetf'))renderFacetInd(); renderApp(); saveView(); return;}
    var _ra=e.target.closest('[data-readyall]');
    if(_ra&&!_ra.disabled){readyAllToShip(); return;}
    var _sd=e.target.closest('[data-seed]');
    if(_sd){var sid=_sd.getAttribute('data-seed'), sj=jobOf(sid)||{id:sid};
      seedAdd('job',sid,(sj.target||sid)); return;}
    var _cs=e.target.closest('[data-coseed]');
    if(_cs){e.preventDefault(); var sco=_cs.getAttribute('data-coseed'); seedAdd('co',sco,sco); return;}
    var _ub=e.target.closest('[data-unblock]');
    if(_ub){e.preventDefault(); toggleBlock(_ub.getAttribute('data-unblock')); return;}
    var _bd=e.target.closest('[data-blockdel]');
    if(_bd){e.preventDefault(); blockDelete(_bd.getAttribute('data-blockdel')); return;}
    var _cp=e.target.closest('[data-coprep]');
    if(_cp){e.preventDefault(); coPrepAll(_cp.getAttribute('data-coprep')); return;}
    var _cr=e.target.closest('[data-corm]');
    if(_cr){e.preventDefault(); coRmAll(_cr.getAttribute('data-corm')); return;}   // 在公司列(summary)上:不擋會順便開合
    var _bb=e.target.closest('[data-block]');
    if(_bb){e.preventDefault(); toggleBlock(_bb.getAttribute('data-block')); return;}   // 同上
    // 公司列要打開:點下去就先把卡畫好(開合本身交給 <details>)
    var _co=e.target.closest('summary.cohead');
    if(_co&&!_co.parentNode.open)fillCo(_co.parentNode);
    // 代投:讓 agent 填表單 / 核准送出 / 取消核准 / 送出。最後一關一定是他按「✅ 核准送出」:
    // 核准存下當下每一題的答案快照,之後答案一改核准就作廢(伺服器和 apply_run 也各擋一次)。
    var arun=e.target.closest('[data-applyrun]');
    if(arun){var ast=arun.getAttribute('data-applyrun'); startRun('apply',{stage:ast,limit:ast==='fill'?runN('apply'):0},arun); return;}
    var afo=e.target.closest('[data-applyfixopen]');
    if(afo){var fo=afo.getAttribute('data-applyfixopen'); APFIX[fo]=!APFIX[fo]; renderAll(fo);
      if(APFIX[fo]){var fin=document.querySelector('[data-applynote="'+CSS.escape(fo)+'"]'); if(fin)fin.focus();} return;}
    var afx=e.target.closest('[data-applyfix]');
    if(afx){var fu=afx.getAttribute('data-applyfix'), fin2=afx.parentNode.querySelector('[data-applynote]'),
        fnote=((fin2&&fin2.value)||'').trim(), frf=((formOf({id:fu})||{}).f||[]).some(function(x){return x.refill;});
      if(!fnote&&!frf){snack('寫一下要 '+AGENT+' 改什麼'); if(fin2)fin2.focus(); return;}
      APFIX[fu]=false; startRun('apply',{stage:'fix',url:fu,note:fnote},afx); return;}
    var apv=e.target.closest('[data-approve]');
    if(apv){var au=apv.getAttribute('data-approve'), aj=jobOf(au), ablk=confirmWhy(au);
      if(ablk){snack('這張還不能確認送出:'+ablk); return;}
      // 確認時記下的答案由後台當下算(#340 user story 9),這裡只送按下去的時間;後台照同一份檢查清單再擋一次
      act(au,{ev:'confirm',data:{approve:{at:new Date().toISOString()}}}).then(function(r){
        if(!r.ok){snack(r.why); return;}
        // 確認後 8 秒內按「復原」就不送(Gmail 的「復原傳送」);時間到才叫 agent 去送。
        // 這段時間卡上不給「▶ 送出」(SUBMIT_SOON);時間到時確認已經不在(取消確認、退回、移除)就不送。
        // 分頁在背景時計時器會被拖慢:時間到的時候已經超過 30 秒(切回來才跑到),就不自己送,停在你已確認、給「▶ 送出」(修正 17)
        var at0=Date.now();
        var atm=SUBMIT_SOON[au]=setTimeout(function(){delete SUBMIT_SOON[au];
          if(Date.now()-at0<=30000&&nextOf(au).send===null)startRun('apply',{stage:'submit',url:au},null); else renderAll(au);},8500);
        renderAll(au);
        snack('已確認「'+(aj?cardName(aj):'')+'」,8 秒後 '+AGENT+' 開始送出',function(){clearTimeout(atm); delete SUBMIT_SOON[au];
          act(au,{undo:r.undo}).then(function(u){if(!u.ok)snack(u.why);});});});
      return;}
    var aun=e.target.closest('[data-unapprove]');
    if(aun){var uu=aun.getAttribute('data-unapprove');
      press(uu,{ev:'unconfirm'},null,'已取消確認',{then:function(){clearTimeout(SUBMIT_SOON[uu]); delete SUBMIT_SOON[uu];}}); return;}
    var acl=e.target.closest('[data-applyclear]');
    // 確認沒送出:不直接回你已確認(修正 3):頁還在 → 重新看過、再按確認送出;頁不在 → 重填;換過檔 → 上傳的是舊檔。
    // 這張跟送出有關的回報(送出沒確認成功、去信箱查)後台一起收掉(#316)
    if(acl){var cu=acl.getAttribute('data-applyclear');
      press(cu,{ev:'not_sent',data:{at:new Date().toISOString()}},null,'好,這張重新看過再確認送出'); return;}
    // 送出結果不明、他查到其實送出了:跟 agent 送出走同一個「已送出」移動,證據就是 agent 當時存的那份(沒看到成功頁的紀錄)。
    var acs=e.target.closest('[data-actsent]');
    if(acs){var su=acs.getAttribute('data-actsent'), now=new Date().toISOString(),
        sib=inboxList().filter(function(x){return x.job===su&&!x.done;});
      delete justMarked[su];
      // 這張之前還開著的回報(送出沒確認成功)已經有答案了
      press(su,{ev:'actually_sent',data:{by:'agent',at:now,sent_at:today()}},null,   // 證據就是 agent 當時那一份,後台抄
        '好,這張移到「已投出」,'+AGENT+' 當時的紀錄留著',
        {then:function(){if(sib.length){sib.forEach(function(x){x.done=today(); x.res='你確認其實送出了';}); dirty['__inbox__']=true; scheduleSave(900);}},
         undone:function(){if(sib.length){sib.forEach(function(x){delete x.done; delete x.res;}); dirty['__inbox__']=true; scheduleSave(900);}},
         next:{label:'去看「已投出」',fn:function(){goToJob(su,'sent');}}});
      return;}
    var rc=e.target.closest('[data-runctl]');
    if(rc){var ra=rc.getAttribute('data-runctl').split(':');
      if(ra[1]==='stop'&&!confirm(rc.hasAttribute('data-graceful')?'停掉這一輪?已經判完的會進看板;找到還沒判的存起來,下一輪先判。':
        rc.hasAttribute('data-force')?'強制停掉?還在收的就不收了。':'停掉這一輪?做到一半的會留著,下次再按一次。'))return;
      rc.disabled=true;
      fetch('/api/run/'+ra[0]+'/'+ra[1],{method:'POST'}).then(function(r){return r.json();}).then(function(st){
        var v={}; v[ra[0]]=st; takeRuns(v); renderAll(); if(ra[0]==='research')renderFind();
        snack(st.ok?(ra[1]==='stop'&&st.finishing?'收工中:做完的會收下':{pause:'暫停了,'+AGENT+' 停在原地',resume:'繼續跑了',stop:'停掉了'}[ra[1]]):(st.msg||'沒做成'));
      }).catch(function(){snack('沒送出去,再按一次'); rc.disabled=false;});
      return;}
    var rrun=e.target.closest('[data-replyrun]');
    if(rrun){startRun('replies',{limit:runN('replies')},rrun); return;}
    var one=e.target.closest('[data-runone]');
    if(one){var ok_=one.getAttribute('data-runone').split('|'), ou=ok_[1];
      if(ok_[0]==='apply')startRun('apply',{stage:'fill',url:ou},null); else startRun(ok_[0],{url:ou},null);
      snack({prep:'只準備這一張,'+(FLOW.auto_advance?'好了驗收過就自動進「可以投了」':'跑完會自己送去「待你決定」'),apply:AGENT+' 只填這一張',replies:'只查這一張的應徵進度'}[ok_[0]]); return;}
    var tgo=e.target.closest('[data-todogo]');
    if(tgo){goToJob(tgo.getAttribute('data-todogo'),active); return;}
    // agent 照回音自動改的狀態:復原(回到改之前,之後不會再因為同一則回音改回來)或確認(收起提示)
    var oun=e.target.closest('[data-ocundo]');
    if(oun){var ou=oun.getAttribute('data-ocundo'), om=FB[ou]||{}, oa=om.oc_auto; if(!oa)return;
      var _pv={oc:om.oc, at:om.oc_at&&JSON.parse(JSON.stringify(om.oc_at)), g:om.ghost_no};
      // 程式改之前的結果和日期存在 from_at(reply_run):原封放回。只照 from 重算的話,
      // 沒下文 → 面試 → 復原會留著面試日期、沒下文日期變成今天,成效表把它算成有回音、面試過
      if(oa.from_at){if(oa.from)om.oc=oa.from; else delete om.oc; om.oc_at=JSON.parse(JSON.stringify(oa.from_at));}
      else setOutcome(om,oa.from||'');
      delete om.oc_auto; if(oa.s==='ghost')om.ghost_no=1;
      markDirty(ou); patchInPlace(ou);
      snack('改回「'+ocLabel(oa.from||'')+'」',function(){om.oc_auto=oa;
        if(_pv.oc)om.oc=_pv.oc; else delete om.oc; if(_pv.at)om.oc_at=_pv.at; else delete om.oc_at;
        if(_pv.g)om.ghost_no=_pv.g; else delete om.ghost_no; markDirty(ou); patchInPlace(ou);});
      return;}
    var rmb=e.target.closest('[data-rpmaybe]');
    if(rmb){var mu=rmb.getAttribute('data-rpmaybe'), mt=rpMaybe(FB[mu]||{}); if(!mt.length)return;
      mt.forEach(function(x){x.maybe_ok=1;}); markDirty(mu); patchInPlace(mu); if(active==='sent')renderApp();
      snack('收掉了',function(){mt.forEach(function(x){delete x.maybe_ok;}); markDirty(mu); patchInPlace(mu); if(active==='sent')renderApp();}); return;}
    // agent 判斷職缺關了,他說判錯了:這張不再因為這個判斷擋(board_status 之後也照這個記號不擋);可以復原
    var jno=e.target.closest('[data-judgedno]');
    if(jno){var ju=jno.getAttribute('data-judgedno'), jf=FB[ju]; if(!jf)return;
      var _jp=jf.judged_no; jf.judged_no=Object.assign({},_jp||{},{closed:today()}); markDirty(ju); patchInPlace(ju);
      snack('記下了:職缺還在,不再因為 agent 判斷關了而擋',function(){if(_jp)jf.judged_no=_jp; else delete jf.judged_no;
        markDirty(ju); patchInPlace(ju);}); return;}
    var rdn=e.target.closest('[data-rpdone]');
    if(rdn){var ru=rdn.getAttribute('data-rpdone'), rt=rpTodos(FB[ru]||{}); if(!rt.length)return;
      rt.forEach(function(x){x.done=1;}); markDirty(ru); patchInPlace(ru); if(active==='sent')renderApp();
      snack('收掉了',function(){rt.forEach(function(x){delete x.done;}); markDirty(ru); patchInPlace(ru); if(active==='sent')renderApp();}); return;}
    var asb=e.target.closest('[data-applysubmit]');
    if(asb){startRun('apply',{stage:'submit',url:asb.getAttribute('data-applysubmit')},asb); return;}
    // 答案庫:確認、從卡片跳過來、新增、刪(可復原)。開合交給 <details>,這裡不管。
    // 答案庫每一顆按鈕按了都能復原(他怕手滑)。ansUndo 記下那一條原本的樣子,復原就整條放回去。
    var aok=e.target.closest('[data-ansok]');
    if(aok){e.preventDefault();   // ✓ 在那一列(summary)上:不擋的話按 ✓ 會順便把那一列打開
      var aoe=ansOf(aok.getAttribute('data-ansok')); if(!aoe)return;
      var aun=ansUndo(aoe); ansConfirm(aoe); ansDone('已確認「'+(aoe.q||aoe.k)+'」',aun); return;}
    var apj=e.target.closest('[data-anspj]');
    if(apj){e.preventDefault();
      var ape=ansOf(apj.getAttribute('data-anspj')); if(!ape)return;
      var pun=ansUndo(ape); if(ape.pj)delete ape.pj; else ape.pj=1; delete ape.pjw;   // 他決定了,我的理由就不留
      ansDone('「'+(ape.q||ape.k)+'」改成'+(ape.pj?'這缺專用':'共用'),pun); return;}
    var ago=e.target.closest('[data-ansgo]');
    if(ago){var gk=ago.getAttribute('data-ansgo'), ge=ansOf(gk);
      FOLD['ans']=1; if(ge&&!ansNeed(ge))FOLD['ans:done']=1; ansQuery=''; renderAll();
      var grow=document.querySelector('#app .ansrow[data-k="'+CSS.escape(gk)+'"]');
      if(grow){grow.scrollIntoView({block:'center'}); grow.classList.add('justhere');
        setTimeout(function(){grow.classList.remove('justhere');},2400);}
      return;}
    var aadd=e.target.closest('[data-ansadd]');
    if(aadd){var nk='u'+Date.now().toString(36);
      FB['__ans__'].push({k:nk,q:'',v:'',why:'',at:today()});
      FOLD['ans']=1; FOLD['ans:done']=1; FOLD['ans:'+nk]=1;
      dirty['__ans__']=true; renderAll(); refreshBar(); scheduleSave(2000);
      var nq=document.querySelector('#app .ansrow[data-k="'+CSS.escape(nk)+'"] .ans-q'); if(nq)nq.focus(); return;}
    var ansd=e.target.closest('[data-ansd]');
    if(ansd){var dk=ansd.getAttribute('data-ansd'), di=ansIndex(dk), de=ansList()[di]; if(!de)return;
      var dl=de.q||de.k;
      // 剛新增、還沒存過的:後台沒有這一條,這裡直接拿掉
      if(!(SAVED['__ans__']||[]).some(function(x){return x&&x.k===dk;})){ansList().splice(di,1); delete FOLD['ans:'+dk];
        ansDone('已刪掉「'+dl+'」',function(){ansList().splice(Math.min(di,ansList().length),0,de);}); return;}
      // 清掉還是刪掉、哪幾張表單要重打、哪幾張確認作廢,後台照整份看板算(form_record.redo;不在看板上的卡也算)。
      // 這裡只送 {redo: 鍵},等後台回話、拿回新的常用答案才更新,再講是清掉還是刪掉
      var was=JSON.parse(JSON.stringify(de)); ansd.disabled=true;
      REDO_DONE[dk]=function(){var now=ansOf(dk);
        snack(now?'已清掉「'+dl+'」的答案,下一輪 '+AGENT+' 會代填':'已刪掉「'+dl+'」',function(){
          if(now){Object.keys(now).forEach(function(p){delete now[p];}); Object.keys(was).forEach(function(p){now[p]=was[p];});}
          else ansList().splice(Math.min(di,ansList().length),0,was);
          dirty['__ans__']=true; renderAll(); refreshBar(); scheduleSave(900);});};
      EVENTS.push({redo:dk}); refreshBar(); scheduleSave(300);
      return;}
    // 移動階段會讓這張卡離開目前分頁,所以要重畫;但不要捲回最上面——
    // 一路標下來會被丟回頂端,446 張要重找位置。留在原地。
    var adv=e.target.closest('[data-adv]');
    if(adv){var aid=adv.closest('article').getAttribute('data-fid'), av=adv.getAttribute('data-adv');
      FB[aid]=FB[aid]||{};
      if(av==='sent'&&dsBusyWhy(aid)){snack(dsBusyWhy(aid)); return;}
      delete justMarked[aid];   // 進了管線就該離開這一頁;留在原地只對「剛標完心情」有意義
      var LBL={prep:'準備履歷中',ready:READY_NAME,ship:'可以投了',sent:'已投出'}, nxt={label:'去看'+LBL[av],fn:function(){goToJob(aid,av);}};
      if(av==='sent'){
        // 跟 agent 送出、平台對帳走同一個「已送出」移動(表單鎖住、待重打清掉、那一頁的紀錄收進歷史),只差證據來源。
        // 寄出去的是哪一份(sent_v)由伺服器存檔時記(ship.record_sent),看板不自己寫。
        // 他自己在外部送出了:這張之前還開著的回報(填表沒成、送出沒確認成功…)都過時了,卡上也不再畫原因
        var _prevIb=inboxList().filter(function(x){return x.job===aid&&!x.done;});
        press(aid,{ev:'sent_manual',data:{by:'manual',at:new Date().toISOString(),sent_at:today()}},null,'已移到「'+LBL[av]+'」',
          {then:function(){if(_prevIb.length){_prevIb.forEach(function(x){x.done=today(); x.res='你標了已在外部送出';}); dirty['__inbox__']=true; scheduleSave(900);}
             rvDone(aid,250);},
           undone:function(){if(_prevIb.length){_prevIb.forEach(function(x){delete x.done; delete x.res;}); dirty['__inbox__']=true; scheduleSave(900);}},
           next:nxt});
        return;}
      var _prevApp=FB[aid].app;
      FB[aid].app=av;
      applyChange(aid);
      snack('已移到「'+LBL[av]+'」',function(){if(_prevApp)FB[aid].app=_prevApp; else delete FB[aid].app; applyChange(aid);},nxt);
      rvDone(aid,250);
      return;}
    var ag=e.target.closest('[data-again]');
    if(ag){var gid=ag.closest('article').getAttribute('data-fid');
      delete justMarked[gid];
      press(gid,{ev:'retry'},null,'上一次的紀錄留著,這張回到「可以投了」,會用現在的履歷重填',{next:{label:'去看「可以投了」',fn:function(){goToJob(gid,'ship');}}});
      return;}
    // 沒送成:agent 親手送出、看到過已收到申請頁的卡,他查過其實沒送成。先跳確認(手滑按下去就會對同一家再投一次);
    // 送出證據連同這一輪的填表紀錄收進投遞歷史(標明是他說 agent 看錯),卡回到可以投了、還沒填,自動流程開著就重填
    var us=e.target.closest('[data-undosent]');
    if(us){var nid=us.closest('article').getAttribute('data-fid');
      if(!confirm(AGENT+' 當時看到了已收到申請的頁面,確定沒送成?'))return;
      delete justMarked[nid];
      press(nid,{ev:'undo_sent',data:{at:new Date().toISOString()}},null,'好,送出紀錄收進投遞歷史,這張回到「可以投了」重填',
        {next:{label:'去看「可以投了」',fn:function(){goToJob(nid,'ship');}}});
      return;}
    var bk=e.target.closest('[data-back]');
    if(bk){var bid=bk.closest('article').getAttribute('data-fid');
      FB[bid]=FB[bid]||{};
      var _to=bk.getAttribute('data-back'), _bw=dsBusyWhy(bid), _app=FB[bid].app, _BL={prep:'準備履歷中',ready:READY_NAME,ship:'可以投了'};
      if(_bw){snack(_bw); return;}
      delete justMarked[bid];
      var _bmsg=_to?'已退回「'+(_BL[_to]||_to)+'」':'已退出流程,回到「'+tabLabel(sentOf(bid)||'none')+'」';
      // 從已投出退回 = 當作這一步沒發生過(手動搬錯、平台對帳配錯):投遞日、寄出的版本、回音都收掉,表單解凍。
      // 只給你在外部送出、平台對帳進來的卡;agent 親手送出的給「沒送成」(狀態表不准退回)
      if(_app==='sent')press(bid,{ev:'back',data:{at:new Date().toISOString(),to:_to||'ship'}},null,_bmsg);
      // 退出可以投了:你之前的「確認送出」一起作廢。以前留著,再推回來卡上立刻是「▶ 送出」,一按就送
      else press(bid,_app==='ship'?{ev:'leave'}:null,{app:_to||null},_bmsg);
      return;}
    // 已投遞的結果:只換那張卡上的字跟上面那排數字,卡片不搬家(規矩跟標心情一樣)。
    var pg=e.target.closest('[data-prep]');
    if(pg){if(!pg.disabled)startRun('prep',{limit:runN('prep')},pg); return;}
    var ocb=e.target.closest('[data-oc]');
    if(ocb){var oid2=ocb.closest('article').getAttribute('data-fid'), ov=ocb.getAttribute('data-oc');
      FB[oid2]=FB[oid2]||{};
      if((FB[oid2].oc||'')===ov)return;
      var _po={oc:FB[oid2].oc, at:FB[oid2].oc_at&&JSON.parse(JSON.stringify(FB[oid2].oc_at)), g:FB[oid2].ghost_no};
      // 從沒下文改成別的,就是他說「這張還有下文」:跟「不對,復原」一樣留 ghost_no,下一輪查應徵進度不會再自動記回沒下文
      if(FB[oid2].oc==='ghost')FB[oid2].ghost_no=1;
      var _pa=FB[oid2].oc_auto; setOutcome(FB[oid2],ov); delete FB[oid2].oc_auto;
      markDirty(oid2); patchInPlace(oid2);
      snack('已記成「'+ocLabel(ov)+'」',function(){
        if(_po.oc)FB[oid2].oc=_po.oc; else delete FB[oid2].oc;
        if(_po.at)FB[oid2].oc_at=_po.at; else delete FB[oid2].oc_at;
        if(_po.g)FB[oid2].ghost_no=_po.g; else delete FB[oid2].ghost_no;
        if(_pa)FB[oid2].oc_auto=_pa; markDirty(oid2); patchInPlace(oid2);});
      return;}
    var rm=e.target.closest('[data-rm]');
    if(rm){var rid=rm.closest('article').getAttribute('data-fid');
      FB[rid]=FB[rid]||{};
      var was=rm.getAttribute('data-rm')==='1';
      if(was&&dsBusyWhy(rid)){snack(dsBusyWhy(rid)); return;}
      delete justMarked[rid];
      // 移除:你之前按的確認送出一起作廢(放回來不會又變有效;狀態表的「離開流程」)
      press(rid,was?{ev:'leave'}:null,{rm:was?1:null},was?'已移到「🗑 已移除」':'已放回看板',{then:function(){if(was)rvDone(rid,250);}});
      return;}
    var er=e.target.closest('[data-err]');
    if(er){var eid=er.closest('article').getAttribute('data-fid');
      FB[eid]=FB[eid]||{};
      if(dsBusyWhy(eid)){snack(dsBusyWhy(eid)); return;}
      delete justMarked[eid];
      // 出錯了:確認送出一起作廢。跟程式的閘門(cut_tailor._techerr)同一組:原本的心情、階段另存 s0、app0,「出錯了」頁可以放回原處
      var ef=FB[eid], eset={s:'techerr',app:null,live_ok:null};
      if(ef.s&&ef.s!=='techerr')eset.s0=ef.s;
      if(ef.app)eset.app0=ef.app;
      press(eid,{ev:'leave'},eset,'已標成「出錯了」（進「🔧 出錯了」分頁）',{then:function(){rvDone(eid,250);}});
      return;}
    var eb=e.target.closest('[data-errback]');
    if(eb){var bid=eb.closest('article').getAttribute('data-fid'), was=JSON.parse(JSON.stringify(FB[bid]||{}));
      var nf=FB[bid]=FB[bid]||{};
      if(nf.s0)nf.s=nf.s0; else delete nf.s;
      if(nf.app0)nf.app=nf.app0;
      delete nf.s0; delete nf.app0;
      nf.live_ok=1;
      applyChange(bid);
      snack('已放回原處'+(nf.app?'(「'+(STAGE_LABEL[nf.app]||'').split('：')[0]+'」)':''),function(){
        FB[bid]=was; applyChange(bid);});
      rvDone(bid,250);
      return;}
    var cop=e.target.closest('[data-catopen]');
    if(cop){var cm=cop.parentNode.querySelector('.catpick-m'); if(cm)cm.hidden=!cm.hidden; return;}
    var cpk=e.target.closest('[data-catpick]');
    if(cpk){var cpid=cpk.closest('article').getAttribute('data-fid'), cpv=cpk.getAttribute('data-catpick');
      FB[cpid]=FB[cpid]||{}; var _pcat=FB[cpid].cat;
      if(cpv)FB[cpid].cat=cpv; else delete FB[cpid].cat;
      applyChange(cpid);
      snack(cpv?'類別改成「'+cpv+'」':'類別改回照關鍵字',function(){
        if(_pcat===undefined)delete FB[cpid].cat; else FB[cpid].cat=_pcat; applyChange(cpid);});
      return;}
    var b=e.target.closest('.fb-b');
    if(b){var id=b.closest('article').getAttribute('data-fid'),s=b.getAttribute('data-s');
      FB[id]=FB[id]||{};
      var was=FB[id].s, now=(was===s?'':s);
      // 還在流程裡(還沒投出)的卡改成 👎 或 😐:不想投了,一起退出流程(確認送出一起作廢,退回那顆同一條)。
      // 以前卡留在流程裡,agent 照樣替它準備、填表。後台收了才算
      if((now==='dislike'||now==='meh')&&['prep','ready','ship'].indexOf(FB[id].app)>=0){
        var lw=dsBusyWhy(id); if(lw){snack(lw); return;}
        press(id,{ev:'leave'},{s:now,app:null,likeprep:null},'改成'+(now==='dislike'?'不喜歡':'普通')+',這張一起退出流程了',
          {then:function(){justMarked[id]=1; if(REVIEW){REVIEW.marked++; REVIEW.last={id:id,s:now}; rvSave(); rvDone(id);}}});
        return;}
      FB[id].s=now;
      var autoPrep=false;
      // 👍 就是要投:同時加入準備(自動流程會接著準備、填表)。取消 👍 或改成別的,這一下替他加的也一起收回。
      // 「是 👍 順手加的」記在卡片標記裡(likeprep)跟著存檔:以前只記在頁面記憶體,重整後再取消 👍 就收不回來(#72)。
      if(FLOW.like_to_prep){
        if(now==='like'&&!FB[id].app&&!removed(id)){FB[id].app='prep'; FB[id].likeprep=1; autoPrep=true;}
        else if(now!=='like'&&FB[id].likeprep&&FB[id].app==='prep'){delete FB[id].app;}}
      if(now!=='like')delete FB[id].likeprep;
      if(now)justMarked[id]=1; else delete justMarked[id];
      markDirty(id);
      if(patchCard(id)){renderTabs(); refreshBar();}   // 只換那一張,頁面不動
      else renderAll(id);
      if(REVIEW&&now){REVIEW.marked++; REVIEW.last={id:id,s:now}; rvSave(); rvDone(id);}   // 一張一張看:標完換下一張;按錯按 ← 回來
      else if(autoPrep)snack('👍 也送去準備履歷中了,'+AGENT+' 會接著準備',function(){delete FB[id].app; delete FB[id].likeprep; applyChange(id);});
      return;}
  });
  $('app').addEventListener('input',function(e){var ta=e.target.closest('.fb-t');if(!ta)return;autogrow(ta);var id=ta.closest('article').getAttribute('data-fid');FB[id]=FB[id]||{};FB[id].n=ta.value;markDirty(id,'text');});
  $('app').addEventListener('input',function(e){var ta=e.target.closest('.rz-fb');if(!ta)return;autogrow(ta);var id=ta.closest('article').getAttribute('data-fid');FB[id]=FB[id]||{};FB[id].rzfb=ta.value;ta.classList.toggle('has',!!ta.value);markDirty(id,'text');});
  // 答案庫的文字。打字當下不重畫整頁(捲動位置和游標都會跑掉),只就地更新那一列。
  $('app').addEventListener('input',function(e){
    if(e.target.classList&&e.target.classList.contains('ans-find')){ansQuery=e.target.value; ansApplyFilter(); return;}
    var a=e.target.closest('[data-ansk]');
    if(a){var en=ansOf(a.getAttribute('data-ansk')); if(!en)return;
      var af=a.getAttribute('data-ansf')||'v', row=a.closest('.ansrow');
      en[af]=a.value;
      // 改英文:雇主網頁要重打,英文以他打的為準。改中文:英文我送出前照著重翻(tr),雇主網頁一樣要重打。
      if(af==='v'){delete en.tr; ansRefill(en.k);}
      if(af==='zh'){en.tr=1; ansRefill(en.k);}
      // 他動手改問題或答案,就是他看過了:「我推論的」在這一刻拿掉。
      if(af!=='why'){delete en.inf; en.at=today();}
      if(row){var sm=row.querySelector(':scope>summary'), tt=sm.querySelector('.ans-hqt');
        var jg=!!row.closest('.ans-jgrp');
        sm.innerHTML=ansHeadHTML(en,(af==='q'&&(jg||!row.closest('.ans-qgrp')))?(en.q||'(還沒寫問題)'):(tt?tt.textContent:(en.q||'')),jg?false:undefined);
        row.classList.toggle('pend',ansNeed(en));
        // 簡體介面上畫面的字是「现」(zhView 轉過),兩種都要認
        var lm=row.querySelector('.ans-lim'); if(lm&&af==='v'){var ln=wordsOf(en.v);
          lm.textContent=lm.textContent.replace(/([現现]) \d+/,'$1 '+ln); lm.classList.toggle('over',ln>ansLim(en.k));}
        var am=row.querySelector('.ans-meta'); if(am)am.textContent=ansMetaText(en); row.setAttribute('data-txt',ansTxt(en));}
      if(a.classList.contains('ans-ta'))autogrow(a);
      dirty['__ans__']=true; renderTabs(); refreshBar(); scheduleSave(2000);}
  });
  // 答案庫一條改完,面板、有用到這條的卡片(那一行的 ⚠ 與待重打)、⚠ 數字要跟著更新。
  // 打字當下不動畫面(游標會跑掉),等他離開輸入框、而且過了 450ms 才重畫:
  // 他離開輸入框的那一下常常是去按旁邊的按鈕,馬上重畫會把那顆按鈕換掉、那一下點擊就被吃掉。
  // 只換有用到這條(同一個 k)的卡:他可能已經在點別張卡的輸入框了,不能把整頁都換掉。
  var ansStale={};
  function ansMarkStale(k){ansStale[k||'*']=1; setTimeout(ansRefreshIfIdle,450);}
  function ansRefreshIfIdle(){
    var ks=Object.keys(ansStale); if(!ks.length)return;
    var ae=document.activeElement;
    if(ae&&/^(INPUT|TEXTAREA)$/.test(ae.tagName)&&ae.closest('#app'))return;   // 還在打字;他離開時會再來一次
    var all=!!ansStale['*']; ansStale={};
    var ad=document.querySelector('#app .ans-d');
    if(ad){var bx=document.createElement('div'); bx.innerHTML=ansSectionHTML();
      if(bx.firstElementChild){ad.replaceWith(bx.firstElementChild); ansApplyFilter(); autogrowAll(document.querySelector('#app .ans-d'));}}
    document.querySelectorAll('#app article[data-fid]').forEach(function(el){
      var id=el.getAttribute('data-fid'), j=jobOf(id); if(!j)return;
      if(!all){var fm=formOf(j); if(!fm||!(fm.f||[]).some(function(x){return ks.indexOf(x.k)>=0;}))return;}
      var box=document.createElement('div'); box.innerHTML=cardHTML(j);
      var nw=box.firstElementChild; if(nw){el.replaceWith(nw); refreshCard(id); autogrowAll(nw);}});
    renderTabs();
  }
  $('app').addEventListener('change',function(e){
    var at=e.target.closest('[data-ansk]'); if(at)ansMarkStale(at.getAttribute('data-ansk'));
    if(REVIEW&&e.target.hasAttribute('data-rvnoreason')){REVIEW.noreason=e.target.checked; renderApp();}
  });
  $('save-dock').addEventListener('click',function(){saveErr='';refreshBar();doSave();});   // 只剩「再試一次」這個用途
  // 打完字離開輸入框就存,不用等 2 秒。掛在整頁上:「我的想法」不在 #app 裡面,
  // 以前掛在 #app,那幾格離開了也不會存,要乾等計時器。
  document.addEventListener('focusout',function(e){
    var t=e.target; if(t&&(t.tagName==='TEXTAREA'||t.tagName==='INPUT')){flushSave(false); setTimeout(ansRefreshIfIdle,450);}});
  // 切到背景 / 關分頁:iOS 可能直接把分頁回收掉,這時候一定要送出去(keepalive 保證送完)
  document.addEventListener('visibilitychange',function(){if(document.hidden)flushSave(true);});
  // 找缺「__ 分鐘」那一格:打完(離開格子或按 Enter)才存進設定,不是每打一個字存一次
  document.addEventListener('change',function(e){
    var fm=e.target.closest&&e.target.closest('input[data-findmin]');
    if(fm){var raw=String(fm.value).trim(), v=raw===''?0:Number(raw);
      if(!(Number.isInteger(v)&&v>=0&&v<=999)){snack('找缺時間要寫 1~999 的分鐘數,空著是不限時'); return;}
      fetch('/api/settings/find_minutes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({minutes:v})})
        .then(function(r){return r.json();}).then(function(d){
          if(d.ok)FINDMIN=v; else snack(d.msg||'沒存起來,再試一次');}).catch(function(){snack('沒存起來,再試一次');});
      }
  });
  // 「跑幾張」那一格:只記在這一頁(重新整理回到全部)。邊打邊改主按鈕上的張數,不重畫(重畫會把鍵盤收掉)。
  document.addEventListener('input',function(e){
    var inp=e.target.closest&&e.target.closest('input[data-runn]'); if(!inp)return;
    var k=inp.getAttribute('data-runn'), v=Math.floor(+inp.value)||0;
    RUNN[k]=v>0?v:0;
    document.querySelectorAll('[data-runn-n="'+k+'"]').forEach(function(sp){
      var t=+sp.getAttribute('data-total'); sp.textContent=RUNN[k]&&RUNN[k]<t?RUNN[k]:t;});
  });
  window.addEventListener('pagehide',function(){flushSave(true);});
  // 關頁前:先送出去。只有「確定存不起來」(剛才就失敗了、或這台根本不能存)才攔他。
  window.addEventListener('beforeunload',function(e){
    if(unsavedCount()>0)flushSave(true);
    if(((saveErr||!canSave)&&unsavedCount()>0)||CFGDIRTY){e.preventDefault();e.returnValue='';}   // 設定頁改了沒按儲存也攔
  });

  // ---- 捲動狀態:分頁列縮成一列 + 回頂鈕 ----
  (function(){
    var tt=document.createElement('button'); tt.id='totop'; tt.type='button';
    tt.title='回到最上面'; tt.textContent='↑';
    tt.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
    document.body.appendChild(tt);
    var tick=false;
    // 捲過一段就把分頁列縮成一行。縮起來頁面會變矮,瀏覽器的捲動錨定就把位置往回推、又判成該展開、
    // 頁面變高、位置推回去——一秒來回 60 次,整排在最上面狂閃(頁面只比畫面長一點點時,例如選單超出畫面底部)。
    // 根本做法:縮起來少掉的高度,在分頁列後面用一個佔位補回去,頁面排版高度不變,位置不會被推、可捲的空間也不變。
    // 用佔位元素、不用外距:外距會跟後面空著的那條(例如沒有回報時的 📣)合併,補回去的高度被吃掉一截。
    // 另外兩道:①進出用不同門檻(遲滯);②頁面本來就沒多高就不縮(縮了也沒意義)。
    function setScrolled(on){var tb=$('tabs'), sp=$('tabs-spacer');
      if(tb&&!sp){sp=document.createElement('div'); sp.id='tabs-spacer'; tb.parentNode.insertBefore(sp,tb.nextSibling);}
      if(!on){document.body.classList.remove('scrolled'); if(sp)sp.style.height=''; return;}
      var h0=tb?tb.offsetHeight:0; document.body.classList.add('scrolled');
      if(sp)sp.style.height=Math.max(0,h0-tb.offsetHeight)+'px';}
    function onScroll(){ if(tick)return; tick=true;
      requestAnimationFrame(function(){tick=false;
        var y=window.pageYOffset||document.documentElement.scrollTop;
        var room=document.documentElement.scrollHeight-window.innerHeight;
        var on=document.body.classList.contains('scrolled');
        if(room<260){ if(on)setScrolled(false); return; }
        if(!on&&y>160)setScrolled(true);
        else if(on&&y<90)setScrolled(false);
      });}
    window.addEventListener('scroll',onScroll,{passive:true}); onScroll();
    var _sv=null;   // 捲動位置防抖存,不要每一幀都寫
    window.addEventListener('scroll',function(){clearTimeout(_sv);_sv=setTimeout(saveView,400);},{passive:true});
  })();
  // ---- 上傳:卡片的「📎 這張用自己的檔」;檔案本身就是 PUT 的 body(不用 multipart) ----
  // 上傳時保留原檔名,只拿掉會變成路徑的字元;副檔名統一小寫(伺服器照副檔名判斷能不能收)
  function uploadName(name,fallbackExt){
    var n=String(name||'').split(/[\\/]/).pop().replace(/[\x00-\x1f]/g,'').replace(/^\.+/,'').trim();
    var m=n.match(/^(.*?)(\.[A-Za-z0-9]+)$/);
    return m&&m[1]?m[1]+m[2].toLowerCase():(n||'file')+fallbackExt;}
  function putFile(url,file){
    return fetch(url,{method:'PUT',body:file}).then(function(r){return r.json().then(function(d){
      if(!r.ok||!d.ok)throw new Error(d.msg||('http '+r.status)); return d;});});
  }
  function customizeModalHTML(url,files,only){
    var rows=(files||[]).map(function(x){var checked=only?x.id===only:!!x.default_checked;
      return '<div class="cust-pick"><label><input type="checkbox" data-custom-select="'+escA(x.id)+'"'+(checked?' checked':'')+'> '+
        esc(x.kind==='resume'?'履歷':'附件')+'：'+esc(x.name)+'</label><span class="cust-pick-skill">'+
        (x.skill?(esc(x.skill_name||fileName(x.skill))):'產品附的通用規則')+'</span>'+
        (x.status?'<span class="cust-pick-state">'+esc(x.status==='review'?'等你看':x.status==='accepted'?'已收下':x.status==='rework'?'退回重寫':x.status==='working'?'正在客製':x.status)+'</span>':'')+
        '<label class="cust-b">上傳自己的客製 PDF<input type="file" accept="application/pdf,.pdf" data-custom-upload="'+escA(x.id)+'" data-custom-url="'+escA(url)+'" hidden></label></div>';}).join('');
    return '<div class="rzm-box customize-box"><button class="rzm-x" type="button">✕ 關閉</button><h3>要客製哪些檔？</h3>'+
      '<p>預設勾選有指定改履歷的規則的檔；其他檔也可以臨時加進來。直接上傳自己的客製 PDF 會算已收下。</p>'+
      (rows||'<p>這張卡目前沒有可以投了的履歷或附件。</p>')+
      '<div class="cust-modal-actions"><button class="stage-b adv" type="button" data-customize-start="'+escA(url)+'"'+(files&&files.length?'':' disabled')+'>開始客製勾選的檔</button></div></div>';
  }
  function openCustomize(url,only){
    fetch('/api/customize/files?u='+encodeURIComponent(url)).then(function(r){return r.json().then(function(d){if(!r.ok||!d.ok)throw new Error(d.msg||('http '+r.status));return d;});})
      .then(function(d){modal(customizeModalHTML(url,d.files||[],only),'');})
      .catch(function(err){snack('讀不到這張卡的檔案('+err.message+'),重新整理再試一次');});
  }
  function customAction(url,item,op,feedback,button){
    if(op==='reject'&&!feedback){snack('寫一下哪裡不對，再退回重寫');return;}
    if(button)button.disabled=true;
    fetch('/api/customize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:op,url:url,item:item,feedback:feedback||''})})
      .then(function(r){return r.json().then(function(d){if(!r.ok||!d.ok)throw new Error(d.msg||('http '+r.status));return d;});})
      .then(function(){snack(op==='accept'?'收下了，要寄的檔案正在換成客製版':op==='reject'?'退回重寫，下一輪會帶上你的回饋':
        (button&&button.hasAttribute('data-cust-orphan'))?'舊的客製紀錄清掉了':'改回原始檔');askShip(url);syncFromServer();})
      .catch(function(err){snack(err.message);if(button)button.disabled=false;});
  }
  document.addEventListener('change',function(e){
    var inp=e.target.closest&&e.target.closest('input[data-custom-upload]'); if(!inp||!inp.files||!inp.files[0])return;
    var u=inp.getAttribute('data-custom-url'), item=inp.getAttribute('data-custom-upload'), f=inp.files[0];
    snack('上傳中…');
    putFile('/api/card-file?u='+encodeURIComponent(u)+'&item='+encodeURIComponent(item)+'&name='+encodeURIComponent(f.name),f)
      .then(function(){closeModal();snack('已收下「'+f.name+'」，要寄的檔案正在重建'); askShip(u); syncFromServer();})
      .catch(function(err){snack('上傳失敗:'+err.message);});
  });
  document.addEventListener('click',function(e){
    var open=e.target.closest&&e.target.closest('[data-cust-open]');
    if(open){openCustomize(open.getAttribute('data-cust-open'),open.getAttribute('data-cust-only')||'');return;}
    var start=e.target.closest&&e.target.closest('[data-customize-start]');
    if(start){var url=start.getAttribute('data-customize-start'), selected=Array.prototype.slice.call(document.querySelectorAll('#rzmodal [data-custom-select]:checked')).map(function(x){return x.getAttribute('data-custom-select');});
      if(!selected.length){snack('至少勾選一份檔案');return;} closeModal(); startRun('customize',{url:url,items:selected},null); snack('客製工作已送出');return;}
    var action=e.target.closest&&e.target.closest('[data-cust-action]');
    if(action){var row=action.closest('.cust-doc'), note=row&&row.querySelector('[data-cust-feedback]');
      customAction(action.getAttribute('data-cust-url'),action.getAttribute('data-cust-item'),action.getAttribute('data-cust-action'),note?note.value.trim():'',action);return;}
    var diff=e.target.closest&&e.target.closest('[data-cust-diff]');
    if(diff){fetch('/api/customize/diff?u='+encodeURIComponent(diff.getAttribute('data-cust-url'))+'&item='+encodeURIComponent(diff.getAttribute('data-cust-diff')))
      .then(function(r){return r.json().then(function(d){if(!r.ok||!d.ok)throw new Error(d.msg||('http '+r.status));return d;});})
      .then(function(d){modal('<div class="rzm-box custom-diff-box"><button class="rzm-x" type="button">✕ 關閉</button><h3>'+esc(d.name)+' · 原檔與客製版差異</h3><pre>'+esc(d.diff)+'</pre></div>','');})
      .catch(function(err){snack('讀不到差異('+err.message+'),重新整理再試一次');});return;}
  });
  // 「👀 看現在的頁面」在看板上的彈窗開,不開新分頁:截圖要等十幾秒,
  // 以前開新分頁,頁面不在了就是一張只有一行字的空白分頁,手機上還要自己關回來。
  document.addEventListener('click',function(e){
    var sh=e.target.closest&&e.target.closest('a[data-apshot]'); if(!sh||e.metaKey||e.ctrlKey)return;
    e.preventDefault();
    var art=sh.closest('article[data-fid]'); openShot(sh.getAttribute('href'),art&&art.getAttribute('data-fid'));
  });
  // 「👀 看現在的頁面」:卡上、「這一頁要你處理的」那一行、填好時的通知都叫這一支,不用他去找卡
  function liveHref(jid){return '/api/live?u='+encodeURIComponent(jid);}
  function openShot(href,jid){
    var j=jid&&jobOf(jid);
    modal('<div class="rzm-box ap-shotbox"><button class="rzm-x" type="button">✕ 關閉</button>'+
      '<p class="ap-shotttl">'+esc(j?cardName(j):'')+'</p><p class="ap-shotmsg" id="ap-shotmsg">⏳ 正在截 '+esc(AGENT)+' 開著的那一頁…</p></div>','');
    var live=/\/api\/live\?/.test(href), sh={getAttribute:function(){return href;}};
    var every=4, shotAt=null;
    fetch(href).then(function(r){
      if(r.status===404&&live)throw new Error('gone');
      if(!r.ok)return r.text().then(function(t){throw new Error(t||('http '+r.status));});
      every=+(r.headers.get('X-Refresh')||4); shotAt=r.headers.get('X-Shot-At');
      return r.blob();
    }).then(function(b){var el=$('ap-shotmsg'); if(!el)return;
      var img=document.createElement('img'); img.className='rzm-pg'; img.alt='幫你填表頁面現在的樣子'; img.src=URL.createObjectURL(b);
      el.replaceWith(img);
      // 用 Claude、agent 的 Chrome 正在跑別的:伺服器給的是那一頁填好時截的圖(同一時間只能一個 Claude 在裡面),講是幾點截的
      if(shotAt){var d=new Date(+shotAt*1000), note=document.createElement('p'); note.className='ap-shotmsg';
        note.textContent='這是 '+('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)+' 截的畫面(上一輪填好、改好時):'+AGENT+
          ' 的 Chrome 正在幫你填表或查應徵進度,用 Claude 時同一時間只能一個在裡面。跑完再按 👀 看現在的。';
        img.before(note);}
      // 「現在的頁面」開著就每幾秒重截一次:agent 還在填時看得到它填到哪(關掉彈窗就停)
      // Claude 那一頁每截一次要十幾秒、算一次用量:伺服器說 0 就不自動重截
      // 重截失敗時舊圖留著,但要標是幾點的畫面、之後截不到(以前舊圖一直當成「現在的頁面」);
      // 頁面不在了、或連續三次截不到就不再重截(以前每幾秒一直叫伺服器截)。再截到就拿掉標示
      if(live&&every>0)(function(){var fails=0, at=new Date(), note=null;
        var hm=function(d){return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)+':'+('0'+d.getSeconds()).slice(-2);};
        (function again(){setTimeout(function(){if(!img.isConnected)return;
          fetch(sh.getAttribute('href')).then(function(r){
            if(r.ok)return r.blob();
            return r.text().then(function(t){throw {gone:r.status===404,msg:t};});})
          .then(function(b2){if(!img.isConnected)return; URL.revokeObjectURL(img.src); img.src=URL.createObjectURL(b2);
            at=new Date(); fails=0; if(note){note.remove(); note=null;} again();})
          .catch(function(e){if(!img.isConnected)return; fails++; var stop=!!(e&&e.gone)||fails>=3;
            if(!note){note=document.createElement('p'); note.className='ap-shotmsg bad'; img.before(note);}
            note.textContent='⚠ 下面是 '+hm(at)+' 的畫面,之後截不到'+
              (e&&e.gone?':那一頁已經不在了('+AGENT+' 的 Chrome 關掉或重開過)':(e&&e.msg?'('+String(e.msg).slice(0,80)+')':''))+
              (stop?'。不再自動更新,要再看就關掉重按 👀。':'');
            if(!stop)again();});
        },every*1000);})();})();
    }).catch(function(err){var el=$('ap-shotmsg'); if(!el)return; el.classList.add('bad');
      // 那一頁不在了(被關掉、Chrome 重開):講清楚,並在這裡直接給重填,不用關掉彈窗再去卡上找
      if(err.message==='gone'&&jid){el.textContent=AGENT+' 填這張時開著的那一頁已經不在了('+AGENT+' 的 Chrome 關掉或重開過)。要再看,讓它重填一次。';
        var b2=document.createElement('button'); b2.type='button'; b2.className='stage-b adv'; b2.setAttribute('data-apshot-refill',jid);
        b2.textContent='▶ 讓 '+AGENT+' 重填這張'; el.after(b2);}
      else el.textContent=err.message;});
  }
  document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-livego]'); if(!b)return;
    var id=b.getAttribute('data-livego'); openShot(liveHref(id),id);});
  document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-apshot-refill]'); if(!b)return;
    closeModal(); startRun('apply',{stage:'fill',url:b.getAttribute('data-apshot-refill')},null); snack(AGENT+' 重填這一張,好了會再告訴你');});
  // ---- 🔗 同一家公司兩個名字(「甲科技」「甲科技股份有限公司」):在看板上直接併 ----
  // 寫進設定的公司別名(board.company_alias,原本就有、以前只能手改 jobsalvo.json),存完重新載入。
  // 候選照「兩個名字共有的最長一段字」排,同一家通常排在最前面。
  function _lcs(a,b){a=a.toLowerCase();b=b.toLowerCase();var m=0;
    for(var i=0;i<a.length;i++)for(var j=0;j<b.length;j++){var k=0;while(a[i+k]&&a[i+k]===b[j+k])k++; if(k>m)m=k;} return m;}
  function mergeModal(co){
    var names={}; jobs.forEach(function(j){var c=companyOf(j); if(c!==co&&c!=='其他')names[c]=(names[c]||0)+1;});
    var list=Object.keys(names).map(function(n){return {n:n,k:_lcs(n,co),c:names[n]};})
      .sort(function(a,b){return b.k-a.k||b.c-a.c||a.n.localeCompare(b.n);});
    modal('<div class="rzm-box merge-box"><button class="rzm-x" type="button">✕ 關閉</button>'+
      '<h3>「'+esc(co)+'」跟哪一家是同一家?</h3><p class="cfg-help">選了之後,「'+esc(co)+'」的缺都會排在那一家底下(存在設定的公司別名,⚙ 設定裡可以拿掉)。</p>'+
      '<input class="cfg-in merge-q" type="search" placeholder="搜公司名" data-mergeq="1">'+
      '<div class="merge-list">'+list.map(function(x){return '<button class="merge-opt" type="button" data-comerge-to="'+escA(x.n)+'" data-comerge-from="'+escA(co)+'"'+
        ' data-n="'+escA(x.n.toLowerCase())+'"><span>'+esc(x.n)+'</span><b>'+x.c+' 張</b></button>';}).join('')+'</div></div>','');
  }
  // 合併/分開會存一次設定:設定頁有沒存的改動時先擋,不然會連他還沒按儲存的改動一起存進去
  function mergeBlocked(){
    if(CFGDIRTY)snack('設定頁有改動還沒存,先存或放棄再合併',null,{label:'去設定頁',fn:function(){switchTab('cfg');}});
    return CFGDIRTY;}
  function mergeInto(from,to){
    if(mergeBlocked())return;
    snack('存進設定中…');
    cfgLoad(function(){
      CFGW.board=CFGW.board||{}; var al=Object.assign({},CFGW.board.company_alias||{});
      al[String(from).toLowerCase()]=to; CFGW.board.company_alias=al; CFGDIRTY=true;
      // 存完不重新載入:重拿後台照新別名算的公司名、重畫就好
      cfgSave(false).then(function(){
        refreshJobs().then(function(){snack('「'+from+'」併進「'+to+'」了',function(){mergeUndo(from);});});
      }).catch(function(err){snack('沒存成('+err.message+'),再按一次;一直不行就重新整理');});
    });
  }
  function mergeUndo(from){if(mergeBlocked())return; cfgLoad(function(){var al=Object.assign({},(CFGW.board||{}).company_alias||{}); delete al[String(from).toLowerCase()];
    CFGW.board.company_alias=al; CFGDIRTY=true;
    cfgSave(false).then(function(){return refreshJobs();}).then(function(){snack('已分開');});});}
  // 公司名、落在封鎖名單哪一條是後台照設定的公司別名算的:改了別名就重拿職缺和下一步再畫
  function refreshJobs(){
    return Promise.all([fetch('/api/jobs').then(function(r){return r.json();}),fetch('/api/next').then(function(r){return r.json();})])
      .then(function(x){pullJobs(x[0]); if(x[1]&&typeof x[1]==='object')NEXT=x[1]; LAZY={}; ORD2={}; renderAll();});}
  document.addEventListener('click',function(e){
    var m=e.target.closest&&e.target.closest('[data-comerge]'); if(m){e.preventDefault(); mergeModal(m.getAttribute('data-comerge')); return;}
    var t=e.target.closest&&e.target.closest('[data-comerge-to]');
    if(t){closeModal(); mergeInto(t.getAttribute('data-comerge-from'),t.getAttribute('data-comerge-to'));}
  });
  document.addEventListener('input',function(e){if(!e.target.matches||!e.target.matches('[data-mergeq]'))return;
    var q=e.target.value.trim().toLowerCase();
    document.querySelectorAll('#rzmodal .merge-opt').forEach(function(b){b.hidden=!!q&&b.getAttribute('data-n').indexOf(q)<0;});});
  // ---- ▶ 答案一次確認完 ----
  // 答案要他確認的,以前散在每張卡的「⚠ 1 條答案等你確認 → 去答案庫看」,要一張一張點、一條一條展開。
  // 這裡一次一條:看題目、看我推論的、改或直接 ✓,確認一條就放行用到它的每一張表單。
  var ANSQ=null;
  function ansQStart(){var ks=ansTodo().map(function(e){return e.k;});
    if(!ks.length){snack('沒有等你確認的答案');return;} ANSQ={ks:ks,i:0,done:0}; ansQRender();}
  function ansQEnd(){var n=ANSQ?ANSQ.done:0; ANSQ=null; closeModal(); renderAll(); refreshBar(); if(n)snack('確認了 '+n+' 條');}
  function ansQRender(){
    if(!ANSQ)return;
    while(ANSQ.i<ANSQ.ks.length&&!ansNeed(ansOf(ANSQ.ks[ANSQ.i])))ANSQ.i++;
    var k=ANSQ.ks[ANSQ.i]; if(k===undefined){ansQEnd(); return;}
    var e=ansOf(k), bi=ansBi(e), f=bi?'zh':'v', em=ansEmpty(e), use=ansUseLabel(k);
    modal('<div class="rzm-box ansq-box"><button class="rzm-x" type="button">✕ 結束</button>'+
      '<div class="ansq-pos">第 '+(ANSQ.i+1)+' / '+ANSQ.ks.length+' 條</div>'+
      '<h3 class="ansq-q">'+esc(e.q||'(沒寫問題)')+'</h3>'+
      (use?'<div class="ansq-use">用在:'+esc(use)+'</div>':'')+
      (e.why?'<div class="ansq-why">'+esc(AGENT)+' 的依據:'+esc(e.why)+'</div>':'')+
      '<textarea class="cfg-in ansq-ta" rows="4" data-ansqv="'+escA(k)+'" placeholder="'+(bi?'用'+RL+'寫就好,送出前照著翻成'+FL:'答案')+'">'+esc(e[f]||'')+'</textarea>'+
      (bi&&e.v?'<div class="ansq-en">'+FL+'(送出用):'+esc(e.v)+'</div>':'')+
      '<div class="ansq-acts"><button class="stage-b adv" type="button" data-ansq-ok="1">'+(em?'✓ 用這個答案':'✓ 這樣可以')+'</button>'+
      '<button class="stage-b" type="button" data-ansq-skip="1">先跳過</button></div></div>','ansq');
  }
  document.addEventListener('click',function(e){
    if(e.target.closest&&e.target.closest('[data-ansq]')){e.preventDefault(); ansQStart(); return;}
    if(!ANSQ)return;
    if(e.target.closest('#rzmodal .rzm-x')||e.target.id==='rzmodal'){ANSQ=null; renderAll(); refreshBar(); return;}
    if(e.target.closest('[data-ansq-skip]')){ANSQ.i++; ansQRender(); return;}
    if(e.target.closest('[data-ansq-ok]')){
      var ta=document.querySelector('#rzmodal [data-ansqv]'), k=ta.getAttribute('data-ansqv'), en=ansOf(k); if(!en)return;
      var f=ansBi(en)?'zh':'v', val=ta.value.trim();
      if(!val){snack('先寫答案'); ta.focus(); return;}
      if(val!==String(en[f]||'').trim()){   // 改了:跟常用答案裡直接改同一套(改中文 → 英文待重翻、還沒送出的表單待重打)
        en[f]=val; if(f==='zh')en.tr=1; else delete en.tr; ansRefill(k); delete en.inf; en.at=today(); dirty['__ans__']=true;}
      else ansConfirm(en);
      scheduleSave(900); renderTabs(); ANSQ.done++; ANSQ.i++; ansQRender();}
  });
  // ---- 履歷檢視:Esc 關掉,開著時背景不跟著捲 ----
  document.addEventListener('keydown',function(e){
    if(e.key!=='Escape')return; var ov=document.getElementById('rzmodal');
    if(ov&&ov.style.display==='flex')closeModal();});

  // ---- 「正在準備」最上面的履歷欄 ----
  function renderCuts(){var cb=$('cutbar'); if(cb)cb.innerHTML=cutsSectionHTML();}
  // ---- 📣 回報:agent 做不到、需要使用者處理的事(tools/agent_report.py 寫進 FB.__inbox__) ----
  // 找缺、跑準備區、代投都交給 agent 之後,它碰到的問題不能只寫在紀錄檔裡。放在每一頁最上面;跟其他開合同一個元件,有待處理的只在標題亮 ⚠。
  // 回報來源是存在資料裡的代號(舊的回報也是這樣存的);畫面上照 GLOSSARY 現在的叫法顯示
  // 來源代號 → [現在的叫法, 去哪一頁](後台 agent_report.FROM,cfg.inbox_from)
  var IB_FROM=CFG.inbox_from||{};
  function inboxList(){return Array.isArray(FB['__inbox__'])?FB['__inbox__']:[];}
  // 要你處理的:後台算(agent_report.todo,下一步的 __inbox__;agent 自己寫、又沒有程式自己截的那一頁的不算,#315)
  function inboxTodo(it){return ((NEXT.__inbox__||{}).todo||[]).indexOf(it.id)>=0;}
  function inboxRowHTML(it){
    var id=escA(it.id), j=it.job?jobOf(it.job):null, when=String(it.at||'').replace('T',' ').slice(5,16), todo=inboxTodo(it);
    var head='<span class="ans-hmain"><span class="ans-hq"><span class="ib-from">'+esc((IB_FROM[it.from]||[])[0]||it.from||AGENT)+'</span>'+esc(it.msg)+
        (it.n>1?'<span class="ans-use">×'+it.n+'</span>':'')+(todo?'':'<span class="ans-tag">缺證據</span>')+'</span>'+
        (it.need&&!it.done&&todo?'<span class="ans-hv">你要做的:'+esc(it.need)+'</span>':'')+'</span>'+
        (it.ev?evLink(it.job,it.ev,'那一頁的截圖'):it.noev?'<span class="ans-use">'+esc(it.noev)+'</span>':'');
    // 還沒處理的「你要做的」標題列已經寫了,展開不再寫一次;處理好的標題不寫,展開才看得到。
    // 缺證據的:只是 agent 說的,照原文放在展開裡,標明不要照做
    var body='<div class="ans-body">'+(it.need&&it.done?'<p class="ib-need">你要做的:'+esc(it.need)+'</p>':'')+
      (it.need&&!it.done&&!todo?'<p class="ib-need">'+AGENT+' 說要你做(沒有程式自己截的那一頁,先別照做):'+esc(it.need)+'</p>':'')+
      '<div class="ans-meta">'+esc(when)+(it.done?(it.res?'　'+esc(mdOf(it.done))+' 自動收掉:'+esc(it.res):'　你 '+esc(mdOf(it.done))+' 處理好了'):'')+'</div>'+
      (j?'<div class="ib-job">'+jdTitleHTML(j)+'<button class="ap-undo" type="button" data-inboxgo="'+escA(it.job)+'">去看那張卡</button></div>'
        :(!inboxHere(it)?'<div class="ib-job"><button class="ap-undo" type="button" data-inboxtab="'+escA(inboxTab(it))+'">去「'+esc(tabLabel(inboxTab(it)==='discover'?'none':inboxTab(it)))+'」</button></div>':''))+
      '<div class="ans-acts"><button class="'+(it.done?'ap-undo':'fm-go')+'" type="button" data-inboxdone="'+id+'">'+
        (it.done?'改回還沒處理':'處理好了')+'</button></div></div>';
    return fold('ib:'+it.id,head,body,{cls:'ansrow ibrow'+(it.done?'':' pend'),attr:' data-ib="'+id+'"'});
  }
  function inboxTab(it){
    if(it.job){var f=FB[it.job]||{}; return f.app||'discover';}
    return (IB_FROM[it.from]||[])[1]||'';
  }
  function inboxHere(it){var t=inboxTab(it); return !t||(t==='discover'?DISCOVER.indexOf(active)>=0:t===active);}
  function inboxPanelHTML(){
    // 處理好的就清掉:不留「已處理」那一疊,沒有待處理的整塊不出現。
    // 每一頁都列全部要你處理的:以前只列跟這一頁有關的,代投的問題要先點到「可以投了」才看得到。跟這一頁有關的排前面。
    // 卡已經移除的不算要你處理(卡上的原因也看不到了);放回看板就又列出來
    var open=inboxList().filter(function(x){return !x.done&&!(x.job&&removed(x.job));}).sort(function(a,b){
      return (inboxHere(b)-inboxHere(a))||String(b.at).localeCompare(String(a.at));});
    if(!open.length)return '';
    var todo=open.filter(inboxTodo).length, noev=open.length-todo;
    return fold('inbox','📣 '+AGENT+' 回報'+(todo?'<span class="ans-pendn">⚠ '+todo+' 件要你處理</span>':'')+
        (noev?'<span class="ans-use">'+noev+' 件缺證據</span>':''),
      '<div class="cuts">'+open.map(inboxRowHTML).join('')+'</div>',
      {cls:'cuts-d inbox-d',hcls:'cuts-sum'});
  }
  function renderInbox(){var b=$('inboxbar'); if(b)b.innerHTML=inboxPanelHTML(); renderFillList();}
  // ---- 🚀 填表進度:「可以投了」的卡哪幾張填好、哪張正在填、哪幾張沒填成,放在每一頁最上面 ----
  // 以前要點進「可以投了」、一張一張看卡才知道填過沒有;填好的直接在這一行按 👀 看那一頁。
  // 資料直接看每張卡的填表紀錄(手動按的、自動流程填的都算),正在填的那張看伺服器帶回來的進度。
  var FL_OPEN=true;
  function fillListHTML(){
    var rows={unsure:[],run:[],ok:[],wait:[],gone:[],bad:[]}, todo=0, SS={}, ORDER=['unsure','run','ok','wait','gone','bad'];
    // 放哪一格跟卡上同一個判斷(cardState);送出結果不明自己一格、排最前面
    jobs.forEach(function(j){var m=FB[j.id]; if(!m||m.app!=='ship'||removed(j.id))return;
      var S=cardState(j); if(S.locked)return; SS[j.id]=S;
      if(S.fill.kind==='todo')todo++; else rows[S.fill.kind].push(j);});
    if(!ORDER.some(function(k){return rows[k].length;}))return '';
    function row(j,kind){var S=SS[j.id], id=escA(j.id), rs=S.runStage;
      var st=kind==='run'?'⏳ 正在'+(rs==='submit'?'送出':rs==='fix'?'改':'填')+(APPLY.running&&APPLY.url===j.id?' · 已 '+minsOf(APPLY)+' 分鐘':''):esc(S.fill.text);
      return '<div class="fl-row fl-'+kind+'"><button class="fl-name" type="button" data-fillgo="'+id+'">'+esc(cardName(j))+'</button>'+
        '<span class="fl-st">'+st+'</span>'+
        (S.eye?'<button class="ap-undo" type="button" data-livego="'+id+'">👀 看頁面</button>':'')+'</div>';}
    var n=function(k,t){return rows[k].length?t+' '+rows[k].length:'';};
    var head='🚀 填表進度<span class="fl-sum">'+[n('unsure','送出結果不明'),n('ok','填好'),n('run',APPLY.stage==='submit'?'正在送出':APPLY.stage==='fix'?'正在改':'正在填'),
      n('wait','填好但還有事'),n('gone','頁面不見'),n('bad','沒填成'),todo?'還沒填 '+todo:''].filter(Boolean).join(' · ')+'</span>';
    return '<details class="fold cuts-d fl-d"'+(FL_OPEN?' open':'')+'><summary class="fold-h cuts-sum">'+head+'</summary><div class="cuts">'+
      ORDER.map(function(k){return rows[k].map(function(j){return row(j,k);}).join('');}).join('')+'</div></details>';
  }
  function renderFillList(){var b=$('filllistbar'); if(b)b.innerHTML=fillListHTML();}
  // 每頁才有的幾條(回報、找新職缺、你的履歷)放在分頁籤「下面」:放上面的話,切頁時它們一出一沒,
  // 分頁籤整排跟著上下跳,手指/滑鼠剛好點不到下一顆。
  (function(){var fb=document.createElement('div'); fb.id='findbar';
    var t=$('app'); if(t&&t.parentNode)t.parentNode.insertBefore(fb,t);
    fb.addEventListener('input',function(e){if(e.target.id==='find-dir')findDraft=e.target.value;});
    fb.addEventListener('click',function(e){
      var mu=e.target.closest('[data-fmute]');
      if(mu){var dir=mu.getAttribute('data-fmute');
        function flip(){FB['__research__']=FB['__research__']||{};
          var m=(FB['__research__'].mute||[]).slice(), i=m.indexOf(dir);
          if(i>=0)m.splice(i,1); else m.push(dir);
          FB['__research__'].mute=m; dirty['__research__']=true; refreshBar(); scheduleSave(900);
          var fr=$('find-rounds'); if(fr)fr.innerHTML=findRoundsHTML(); return i<0;}
        var on=flip();
        snack(on?'已記下:下一輪別再往「'+dir+'」找':'已改回:這個方向可以再找',flip);
        return;}
      var b=e.target.closest('[data-find]'); if(!b||b.disabled)return;
      var m=b.getAttribute('data-find'), body={mode:m};
      if(m==='dir'){body.text=(findDraft||'').trim();
        if(!body.text){var ta=$('find-dir'); if(ta)ta.focus(); snack('先寫一句要往哪個方向挖'); return;}}
      if(m==='seed'){var sl=seedList();
        if(!sl.length){snack('還沒指名要找什麼'); return;}
        body.seeds=sl.map(function(x){return {k:x.k,v:x.v};});
        // 送出去就清掉清單:這一輪已經帶走了,留著他會不知道跑過沒有(要再找就再點一次)
        FB['__seeds__']=[]; dirty['__seeds__']=true; renderFind(); renderAll(); refreshBar(); scheduleSave(900);}
      body.limit=runN('research');
      // 格子的值直接帶上:填完馬上按,存設定那一個請求可能還沒到,不能只靠伺服器讀設定
      var fm=document.querySelector('#findrow input[data-findmin]');
      if(fm){var fv=String(fm.value).trim(); body.minutes=fv===''?0:Number(fv);}
      startRun('research',body,b);});
    renderFind();
  })();
  (function(){var cb=document.createElement('div'); cb.id='cutbar';
    var t=$('app'); if(t&&t.parentNode)t.parentNode.insertBefore(cb,t);
  })();
  (function(){var fl=document.createElement('div'); fl.id='filllistbar';
    var t=$('findbar')||$('app'); if(t&&t.parentNode)t.parentNode.insertBefore(fl,t);
    fl.addEventListener('toggle',function(e){if(e.target.classList&&e.target.classList.contains('fl-d'))FL_OPEN=e.target.open;},true);
    fl.addEventListener('click',function(e){var g=e.target.closest('[data-fillgo]'); if(g)goToJob(g.getAttribute('data-fillgo'),'ship');});
  })();
  (function(){var ib=document.createElement('div'); ib.id='inboxbar';
    var t=$('findbar')||$('app'); if(t&&t.parentNode)t.parentNode.insertBefore(ib,t);
    ib.addEventListener('click',function(e){
      var d=e.target.closest('[data-inboxdone]');
      if(d){var id=d.getAttribute('data-inboxdone'), it=inboxList().filter(function(x){return x.id===id;})[0]; if(!it)return;
        var was=it.done; if(it.done)delete it.done; else it.done=today();
        dirty['__inbox__']=true; renderInbox(); refreshBar(); scheduleSave(900);
        snack(it.done?'標成處理好了':'改回還沒處理',function(){if(was)it.done=was; else delete it.done;
          dirty['__inbox__']=true; renderInbox(); refreshBar(); scheduleSave(900);});
        return;}
      var gt=e.target.closest('[data-inboxtab]');
      if(gt){var tk=gt.getAttribute('data-inboxtab'); switchTab(tk==='discover'?'none':tk); return;}
      var g=e.target.closest('[data-inboxgo]');
      if(g){var gid=g.getAttribute('data-inboxgo'), ga=(FB[gid]||{}).app;
        goToJob(gid, ga&&(STAGES.indexOf(ga)>=0||ga==='sent')?ga:(sentOf(gid)||'none'));}
    });
    renderInbox();})();
  // ---- ⚙ 設定:使用者只碰網頁就能把 jobsalvo 設定好(履歷、硬規則、填表做法、分類、agent、Chrome) ----
  // 資料在伺服器的 /api/settings;這裡改的是一份工作副本(CFGW 設定、CFGT 三份文字),按「💾 儲存設定」才寫回。
  // 上傳檔案例外:檔一傳上去就連同設定一起存,不用再按儲存(不然使用者以為傳好了,其實沒記進設定)。
  var CFGD=null, CFGW=null, CFGT=null, CFGDIRTY=false, CFGAWAY=false, CFG0='';
  // 有沒有改動 = 工作副本跟讀進來(或剛存好)那一刻比:改了又改回去、刪掉再按復原,都算沒有改動
  function cfgSnap(){return JSON.stringify([CFGW,CFGT]);}
  function _clone(o){return JSON.parse(JSON.stringify(o===undefined?null:o));}
  function cfgLoad(cb){
    fetch('/api/settings').then(function(r){return r.json();}).then(function(d){
      // 還有沒存的改動(例:等分類建議時在打字、開關開機啟動):只換伺服器那邊的狀態(環境檢查、檔案、建議…),
      // 他的工作副本和它根據的那一版留著。以前整份換掉,打到一半的字就沒了;存的時候版本對不上會擋下來,不會蓋掉別處的改動
      if(CFGDIRTY&&CFGD){['settings','effective','texts','version','conflict'].forEach(function(k){d[k]=CFGD[k];}); CFGD=d; return;}
      CFGD=d; var e=d.effective||{}, w=_clone(d.settings||{});
      // 這幾塊一律從「實際生效的值」起頭,使用者看到的就是現在在用的(包括預設)
      w.board=w.board||{}; w.board.categories=_clone(e.board.categories); w.board.tags=_clone(e.board.tags);
      w.agent=_clone(e.agent); w.browser=_clone(e.browser); w.search=_clone(e.search); w.replies=_clone(e.replies);
      w.flow=_clone(e.flow||{});
      w.resume=w.resume||{};
      w.resume.resumes=_clone(Array.isArray(w.resume.resumes)?w.resume.resumes:(e.resume.resumes||[]));
      w.resume.attachments=_clone(Array.isArray(w.resume.attachments)?w.resume.attachments:(e.resume.attachments||[]));
      w.resume.langs=_clone(w.resume.langs||e.resume.langs||['zh','en']);
      CFGW=w; CFGT=_clone(d.texts||{}); CFGDIRTY=false; CFG0=cfgSnap();
    }).then(function(){cb&&cb();},function(){$('app').innerHTML='<div class="emptytab">讀不到設定(伺服器沒回應)</div>';});
  }
  function cfgGet(path){var o=CFGW;path.split('.').forEach(function(k){o=(o==null)?undefined:o[k];});return o;}
  function cfgSet(path,v){var ks=path.split('.'),o=CFGW;
    ks.slice(0,-1).forEach(function(k){if(o[k]==null||typeof o[k]!=='object')o[k]={}; o=o[k];}); o[ks[ks.length-1]]=v; CFGDIRTY=true;}
  function langName(l){return langLabel(l);}
  function fileName(p){return p?String(p).split('/').pop():'';}
  // 打開設定頁的某一段並捲過去;從別頁過來時設定還在讀,讀好才捲得到。
  function cfgGo(g){
    FOLD[g]=1;
    if(active!=='cfg')switchTab('cfg'); else renderCfg();
    var n=0;(function go(){var d=document.querySelector('[data-fold="'+g+'"]');
      if(d)d.scrollIntoView({block:'start'}); else if(n++<40)setTimeout(go,100);})();
  }
  document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-gocfg]');
    if(b)cfgGo(b.getAttribute('data-gocfg'));});
  function cfgChecklist(){
    var resumes=cfgGet('resume.resumes')||[];
    var available=CFGD.files||[];
    var hasFile=resumes.some(function(x){return x.enabled!==false&&(CFGW.resume.langs||[]).some(function(l){var p=(x.files||{})[l];return p&&available.indexOf(p)>=0;});});
    var br=cfgBrowserRuntime(), chromeOk=br==='codex'?CFGD.browser_ok:br==='claude-code'?!!CFGD.claude_paired:false;
    return '<div class="cfg-check'+(hasFile?' done':'')+'"><div class="cfg-ch-h">🚦 開始前 '+(hasFile?'1/1 ✓':'0/1')+'</div>'+
      '<button class="cfg-ch'+(hasFile?' ok':'')+'" type="button" data-cfgo="cfg:resumes">'+
      (hasFile?'✅ ':'⬜ ')+'上傳至少一份履歷(📄 你的履歷)</button>'+
      '<button class="cfg-ch'+(chromeOk?' ok':'')+'" type="button" data-cfgo="cfg:agent">'+
      (chromeOk?'✅ ':'⬜ ')+'(選用)要幫你填表:連接'+(br==='claude-code'?' Claude ':br==='codex'?' Codex ':'')+'操作的 Chrome(🤖 Agent 與瀏覽器)</button></div>';
  }
  // 勾了「用它操作 Chrome」的那一個 agent 是哪一種;沒有回 ''(只准勾一個)
  function cfgBrowserRuntime(){
    var a=((CFGW.agent||{}).agents||[]).filter(function(x){return x.browser&&x.runtime!=='command-code';})[0];
    return a?(a.runtime==='claude-code'?'claude-code':'codex'):'';
  }
  function cfgDoctorRow(x){
    var mark=x.ok?'✅ ':x.required===false&&!x.warn?'ℹ️ ':'⚠️ ', act=x.action||{};
    return '<div class="cfg-row"><span class="cfg-file">'+mark+esc(x.label)+'｜'+esc(x.detail||'')+
      (x.fix?'<br>處理方式：'+esc(x.fix):'')+'</span>'+
      (act.use_runtime?'<button class="cfg-b" type="button" data-cfuseagent="'+escA(act.use_runtime)+'">'+esc(act.label||'改用')+'</button>':'')+'</div>';
  }
  function cfgDoctorHTML(){
    var d=CFGD.doctor||{}, checks=d.checks||[], optionalMissing=checks.some(function(x){return x.required===false&&!x.ok;});
    // warn:不擋找缺、但他該知道的(資料夾沒在存版、舊資料沒轉):跟必要的一樣攤開,不收進「部分功能未設定」
    var warned=checks.some(function(x){return x.warn&&!x.ok;});
    var summary=d.ok?(warned?'找缺可用；有要處理的提醒':optionalMissing?'找缺可用；部分功能未設定':'通過'):'需要處理';
    var rows=checks.map(cfgDoctorRow).join('');
    // 必要的都過就收成一行(選用的沒設定由上面「開始前」清單帶他去做,這裡不重講一遍);必要的有缺才攤開,只列要處理的
    if(d.ok&&!warned)return fold('cfg:doctor','🩺 環境檢查:'+summary+' <span class="n">'+checks.length+' 項</span>',rows,{cls:'cfg-d cfg-doc'});
    var bad=checks.filter(function(x){return !x.ok;}), good=checks.filter(function(x){return x.ok;});
    return '<div class="cfg-check"><div class="cfg-ch-h">🩺 環境檢查：'+summary+'</div>'+bad.map(cfgDoctorRow).join('')+
      (good.length?fold('cfg:doctor-ok','其他 '+good.length+' 項通過',good.map(cfgDoctorRow).join(''),{cls:'cfg-d'}):'')+'</div>';
  }
  function cfgMigrationHTML(){
    var messages=CFGD.migration_notices||[];
    return messages.length?'<div class="cfg-check"><div class="cfg-ch-h">已更新舊設定</div>'+messages.map(function(m){
      return '<div class="cfg-row"><span class="cfg-file">'+esc(m)+'</span></div>';}).join('')+'</div>':'';
  }
  function cfgHistoryHTML(){      // 放在「⚙ 其他」:第一次用的人用不到,不佔設定頁最上面
    var h=CFGD.git_history||{};
    return '<div class="cfg-row"><label class="cfg-k">版本紀錄</label><span class="cfg-file">'+
      (h.fix||h.conversion?'⚠️ ':'')+esc(h.message||'尚無版本紀錄')+(h.pending?'｜正在記錄':'')+
      (h.conversion?'<br>'+esc(h.conversion):'')+(h.fix?'<br>處理方式：'+esc(h.fix):'')+'</span></div>';
  }
  // 「⚙ 其他」的版本那一列:跟遠端 main(tools/update.py);切在別的分支、有沒提交變更的只說原因,不給按鈕。
  // 有新版就在標題「求職儀表板」旁邊出現一個小小的「⬆ 更新」(手機只留箭頭),按了就更新。
  // 以前只在設定頁最底下「版本」那一列,沒人會去看;做成大按鈕又太搶眼。
  // 不能從這裡更新的(切在別的分支、有沒提交的改動)也亮,按了帶去設定頁看原因。
  (function(){var tt=document.querySelector('#hdr .topttl'); if(!tt)return;
    var b=document.createElement('button'); b.type='button'; b.id='updchip'; b.className='upd-chip'; b.hidden=true;
    tt.appendChild(b);
    function fill(){fetch('/api/update').then(function(r){return r.json();}).then(function(u){
      if(!u.new){b.hidden=true; return;}
      b.hidden=false; b.disabled=false; b.blocked=!!u.blocked;
      b.innerHTML='⬆<span class="upd-txt"> 更新</span>';
      b.title=u.blocked?('有新版,但'+u.blocked):('有新版,按了更新到 '+(u.latest||'最新版'));
      b.setAttribute('aria-label',b.title);}).catch(function(){});}
    b.addEventListener('click',function(){
      if(b.blocked){cfgGo('cfg:sys'); return;}
      b.disabled=true; b.innerHTML='⬆<span class="upd-txt"> 更新中…</span>';
      doUpdate(function(ok){fill(); if(ok)cfgUpdateFill();});});
    window.__updFill=fill;   // 設定頁「現在檢查」查完順便更新這顆
    fill(); setInterval(fill,3600*1000);   // 伺服器那邊每小時最多問一次遠端,這裡跟著一小時看一次
  })();
  // now:按了「現在檢查」,不等每小時那一次,當場去問
  function cfgUpdateFill(now){var row=$('cfg-update'); if(!row)return;
    fetch('/api/update'+(now?'?now=1':'')).then(function(r){return r.json();}).then(function(u){
      if(now&&window.__updFill)window.__updFill();
      var cur=u.current||'讀不到', t;
      if(u.latest===null)t=esc(cur)+' · 查不到最新版(沒網路?)';
      else if(!u.new)t=esc(cur)+(u.latest?' · 已經是最新'+(u.checked?'('+new Date(u.checked*1000).toTimeString().slice(0,5)+' 查的)':''):'');
      else t=esc(cur)+' → 有新版 <b>'+esc(u.latest)+'</b>';
      var btn=u.new&&!u.blocked?'<button class="cfg-b" type="button" data-cfupdate="1">更新到 '+esc(u.latest)+'</button>':'';
      row.innerHTML='<label class="cfg-k">版本</label><span class="cfg-file">'+t+(u.blocked&&u.new?'<br>'+esc(u.blocked):'')+'</span>'+btn+
        (u.new?'':'<button class="cfg-b" type="button" data-cfupcheck="1">🔄 現在檢查</button>');
    }).catch(function(){row.querySelector('.cfg-file').textContent='查不到版本';});}
  document.addEventListener('click',function(e){var c=e.target.closest&&e.target.closest('[data-cfupcheck]'); if(!c)return;
    c.disabled=true; c.textContent='檢查中…'; cfgUpdateFill(true);});
  document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-cfupdate]'); if(!b)return;
    b.disabled=true; b.textContent='更新中…';
    doUpdate(function(){cfgUpdateFill();});});
  // 按了更新:問後台更新、講結果;after(ok) 收尾(成功才是 true)
  function doUpdate(after){fetch('/api/update',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    snack(d.msg||(d.ok?'更新好了':'沒更新成功')); after(true);}).catch(function(){snack('沒更新成功'); after(false);});}
  function cfgLangFileRows(kind,id,files,styles){
    var langs=CFGW.resume.langs||[];
    return langs.map(function(l){var f=(files||{})[l], which=kind+'|'+id+'|'+l;
      var preview=f&&/\.(pdf|md|markdown)$/i.test(f)?'<button class="cfg-b" type="button" data-srcpv="'+escA('/api/source-preview?kind='+
        encodeURIComponent(kind)+'&id='+encodeURIComponent(id)+'&lang='+encodeURIComponent(l))+'">👁 預覽</button>':'';
      // 設定裡記著檔名、檔案卻不在資料夾裡(被搬走或刪掉):講出來,不然看起來像是好的
      var gone=f&&(CFGD.files||[]).indexOf(f)<0;
      var markdown=f&&/\.(md|markdown)$/i.test(f), style=(styles||{})[l], warning=(CFGD.render_warnings||[]).find(function(w){
        return w.kind===kind&&w.id===id&&w.lang===l;});
      var styleRow=markdown?'<div class="cfg-row"><label class="cfg-k">PDF 樣式</label><span class="cfg-file">'+
        (style?'🎨 '+esc(fileName(style)):'<i>未指定，使用瀏覽器原生排版</i>')+'</span><label class="cfg-b">'+
        (style?'換樣式':'上傳 CSS')+'<input type="file" accept=".css,text/css" data-cfstyle="'+escA(which)+'" hidden></label></div>':'';
      var pageWarning=warning?'<div class="cfg-row"><span class="cfg-file">⚠ '+esc(fileName(f))+' 排成 '+warning.pages+' 頁；建議檢查版面。</span></div>':'';
      return '<div class="cfg-row"><label class="cfg-k">'+esc(langName(l))+'版</label><span class="cfg-file'+(gone?' gone':'')+'">'+(f?(gone?'⚠ 找不到檔案:':'📄 ')+esc(fileName(f)):'<i>還沒上傳</i>')+'</span>'+
        (gone?'':preview)+'<label class="cfg-b">'+(f&&!gone?'換一份':(gone?'重新上傳':'上傳'))+'<input type="file" data-cfup="'+escA(which)+'" hidden></label></div>'+styleRow+pageWarning;}).join('');
  }
  // 他看得懂的語言:附翻譯用它,選简体中文時看板介面也整個換成簡體(zhView)。清單外的舊值照樣留著、選得到。
  // 履歷有哪些語言:勾選,不用他打「zh, en」這種代碼;設定檔裡原本就有的其他語言也列出來
  function cfgLangChecks(cur){
    var codes=['zh','en','ja','ko']; cur.forEach(function(l){if(codes.indexOf(l)<0)codes.push(l);});
    return codes.map(function(l){return '<label class="cfg-file"><input type="checkbox" data-cflang="'+escA(l)+'"'+
      (cur.indexOf(l)>=0?' checked':'')+'> '+esc(langName(l))+'</label>';}).join(' ');}
  function cfgReadLang(cur){
    var opts=[['zh','繁體中文'],['zh-CN','简体中文'],['en','English'],['ja','日本語'],['ko','한국어']];
    if(!opts.some(function(o){return o[0]===cur;}))opts.push([cur,cur]);
    return '<select class="cfg-in" data-cf="resume.read_lang">'+opts.map(function(o){
      return '<option value="'+escA(o[0])+'"'+(o[0]===cur?' selected':'')+'>'+esc(o[1])+'</option>';}).join('')+'</select>';}
  // 找缺與判斷的做法、改履歷的規則放同一個資料夾:各自的選單只列自己那一種(沒記號的舊檔兩邊都列,已經選的照樣列)
  function cfgSkillSelect(path,selected,label,kind){
    var value=selected||'', skills=(CFGD.skills||[]).filter(function(s){return !s.kind||s.kind===(kind||'resume')||s.path===value;}),
      known=skills.some(function(s){return s.path===value;});
    var options='<option value="">用產品附的通用規則</option>'+
      (!known&&value?'<option value="'+escA(value)+'" selected>找不到:'+esc(value)+'</option>':'')+
      skills.map(function(s){return '<option value="'+escA(s.path)+'"'+(value===s.path?' selected':'')+'>'+esc(s.name||s.path)+'</option>';}).join('');
    return '<div class="cfg-row"><label class="cfg-k">'+esc(label||'改履歷的規則')+'</label><select class="cfg-in" data-cf="'+escA(path)+'">'+options+'</select></div>';
  }
  function cfgResearchSkillHTML(task,selected){
    var key=task.key, label=task.label||key, content=task.default_content||'';
    return '<div class="cfg-card cfg-research-skill" data-research-skill="'+escA(key)+'">'+
      cfgSkillSelect('research.skills.'+key,selected,label,'research')+
      fold('cfg:research-default:'+key,'看產品附的預設內容',
        '<pre data-research-default="'+escA(key)+'">'+esc(content)+'</pre>',{cls:'cfg-d cfg-research-default'})+
      '<div class="cfg-row"><button class="cfg-b" type="button" data-cfresearch-copy="'+escA(key)+'">拿這份預設改一份</button></div>'+
      '<div data-cfresearch-editor="'+escA(key)+'" hidden>'+
      '<div class="cfg-row"><label class="cfg-k">名稱</label><input class="cfg-in" data-cfresearch-name="'+escA(key)+'" value="'+escA(label+'（我的版本）')+'"></div>'+
      '<div class="cfg-row"><label class="cfg-k">做法</label><textarea class="cfg-in big" rows="8" data-cfresearch-content="'+escA(key)+'">'+esc(content)+'</textarea></div>'+
      '<div class="cfg-row"><button class="cfg-b go" type="button" data-cfresearch-save="'+escA(key)+'">儲存並選上這份</button></div>'+
      '</div></div>';
  }
  function cfgResumeHTML(r,index){
    var id=String(r.id||''), files=r.files||{};
    return '<div class="cfg-card"><div class="cfg-row"><label class="cfg-k">名稱</label><input class="cfg-in" data-cf="resume.resumes.'+index+'.name" value="'+escA(r.name||'')+'" placeholder="例:工程履歷"></div>'+
      '<label class="cfg-row"><input type="checkbox" data-cfcheck="resume.resumes.'+index+'.enabled"'+(r.enabled===false?'':' checked')+'> 使用這份履歷</label>'+
      '<div class="cfg-row"><label class="cfg-k">什麼時候用</label><textarea class="cfg-in" rows="2" data-cf="resume.resumes.'+index+'.when" placeholder="例:工程、資料這類要看技術細節的職缺">'+esc(r.when||'')+'</textarea></div>'+
      cfgSkillSelect('resume.resumes.'+index+'.skill',r.skill)+
      cfgLangFileRows('resume',id,files,r.styles)+
      '<div class="cfg-row end"><button class="cfg-b warn" type="button" data-cfresdel="'+escA(id)+'">🗑 刪掉這份履歷</button></div></div>';
  }
  function cfgAttachmentHTML(a,index,resumes){
    var id=String(a.id||''), files=a.files||{}, allowed=Array.isArray(a.resume_ids)?a.resume_ids:[], all=!allowed.length;
    var choices=resumes.map(function(r){return '<label class="cfg-pick"><input type="checkbox" data-cfeligible="'+escA(id+'|'+r.id)+'"'+(all||allowed.indexOf(r.id)>=0?' checked':'')+'> '+esc(r.name||r.id)+'</label>';}).join('')||
      '<i>先新增履歷</i>';
    return '<div class="cfg-card"><div class="cfg-row"><label class="cfg-k">名稱</label><input class="cfg-in" data-cf="resume.attachments.'+index+'.name" value="'+escA(a.name||'')+'" placeholder="例:作品集"></div>'+
      '<label class="cfg-row"><input type="checkbox" data-cfcheck="resume.attachments.'+index+'.enabled"'+(a.enabled===false?'':' checked')+'> 附上這份附件</label>'+
      '<div class="cfg-row"><span class="cfg-k">能搭哪幾份履歷</span><span class="cfg-file">沒勾就是全部</span></div>'+
      '<div class="cfg-picks">'+choices+'</div>'+cfgSkillSelect('resume.attachments.'+index+'.skill',a.skill)+cfgLangFileRows('attachment',id,files,a.styles)+
      '<div class="cfg-row end"><button class="cfg-b warn" type="button" data-cfattdel="'+escA(id)+'">🗑 刪掉這份附件</button></div></div>';
  }
  function cfgListHTML(key,withIcon){
    var list=cfgGet('board.'+key)||[];
    return list.map(function(c,i){
      return '<div class="cfg-li">'+(withIcon?'<input class="cfg-in ico" data-cfli="'+key+'|'+i+'|icon" value="'+escA(c.icon||'')+'" maxlength="4" placeholder="圖示" aria-label="圖示(一個表情符號)">':'')+
        '<input class="cfg-in nm" data-cfli="'+key+'|'+i+'|name" value="'+escA(c.name||'')+'" placeholder="名稱" aria-label="名稱">'+
        '<input class="cfg-in mt" data-cfli="'+key+'|'+i+'|match" value="'+escA(c.match||'')+'" placeholder="關鍵字,用 | 分隔(職稱和內文裡有就算)" aria-label="關鍵字">'+
        '<button class="cfg-x" type="button" data-cflidel="'+key+'|'+i+'" aria-label="刪掉這一項" title="刪掉這一項">✕</button></div>';}).join('')+
      '<button class="cfg-b" type="button" data-cfliadd="'+key+'">＋ 新增'+(key==='categories'?'類別':'標籤')+'</button>';
  }
  function cfgSuggestHTML(){
    var s=CFGD.suggest, m=MISC.suggest||{};
    var run=m.running?'<span class="prep-st run">⏳ '+esc(AGENT)+' 在想怎麼分…</span>':
      '<button class="cfg-b" type="button" data-cfsuggest="1">🤖 讓 '+esc(AGENT)+' 照我的履歷和表態建議</button>';
    if(!s||!s.categories)return run;
    // 整份是 agent 判斷的建議(程式只核對比對規則寫不寫得出來):標明是誰判斷的,對不上沒收的照實列出
    return run+'<div class="cfg-sug"><div class="cfg-k">'+esc(s.by||'agent 判斷')+'的建議(還沒套用)</div>'+
      '<div>類別:'+s.categories.map(function(c){return esc((c.icon||'')+' '+c.name);}).join('、')+'</div>'+
      '<div>標籤:'+((s.tags||[]).map(function(t){return esc(t.name);}).join('、')||'(沒有)')+'</div>'+
      (s.why?'<div class="cfg-why">'+esc(s.why)+'</div>':'')+
      ((s.problems||[]).length?'<div class="cfg-why">對不上、沒收進建議:'+esc(s.problems.join('；'))+'</div>':'')+
      '<button class="cfg-b go" type="button" data-cfsugapply="1">套用這份建議</button></div>';
  }
  // 每一格都要有螢幕報讀器認得的名字(axe-core 檢查):同一列左邊那個標題就是它的名字。
  // 一列只有一格就用 <label for> 接上;一列好幾格,用標題加淡字提示當名字。已經有名字的不動。
  var cfgFieldSeq=0;
  function cfgNameFields(root){
    [].forEach.call(root.querySelectorAll('.cfg-row'),function(row){
      var k=row.querySelector('.cfg-k'), title=k?k.textContent.trim():'';
      var fields=[].filter.call(row.querySelectorAll('input:not([type=hidden]):not([type=file]), select, textarea'),function(el){
        return !el.getAttribute('aria-label')&&!el.closest('label')&&!(el.id&&root.querySelector('label[for="'+el.id+'"]'));});
      if(!title||!fields.length)return;
      if(fields.length===1&&k.tagName==='LABEL'){var el=fields[0]; if(!el.id)el.id='cfgf'+(++cfgFieldSeq); k.htmlFor=el.id; return;}
      fields.forEach(function(el){el.setAttribute('aria-label',title+((el.placeholder||'').trim()?':'+el.placeholder.trim():''));});
    });
  }
  // 第一次建 agent 的 Chrome 時從哪個設定檔複製登入狀態:顯示 Chrome 右上角看到的名字、裝了哪個官方擴充功能;存的是資料夾名。
  // 以前要去 chrome://version 抄資料夾名,新使用者幾乎一定填錯(#159)。清單讀不到才退回手填。
  function chromeProfilePick(cur){
    var ps=CFGD.chrome_profiles||[], EXT={codex:'Codex',claude:'Claude'};
    if(!ps.length)return '<input class="cfg-in" id="cfg-chprof" data-cf="browser.profile" value="'+escA(cur)+'" placeholder="Chrome 設定檔的資料夾名(chrome://version 看得到)">';
    var known=ps.some(function(p){return p.dir===cur;});
    return '<select class="cfg-in" id="cfg-chprof" data-cf="browser.profile">'+
      '<option value=""'+(cur?'':' selected')+'>不複製(自己在 agent 的 Chrome 裡登入)</option>'+
      (known||!cur?'':'<option value="'+escA(cur)+'" selected>'+esc(cur)+'(Chrome 裡找不到,請重選)</option>')+
      ps.map(function(p){var ex=(p.ext||[]).map(function(k){return EXT[k]||k;});
        return '<option value="'+escA(p.dir)+'"'+(p.dir===cur?' selected':'')+'>'+esc(p.name)+
          (ex.length?'(已裝 '+esc(ex.join('、'))+' 擴充功能)':'')+'</option>';}).join('')+'</select>';}
  function renderCfg(){
    inApplyView=true;
    if(!CFGD){CFGAWAY=false; $('app').innerHTML='<div class="emptytab">讀取設定中…</div>'; cfgLoad(renderCfg); return;}   // 第一次讀就是最新的,讀好不用再讀
    // 從別的分頁回來、這頁沒有沒存的改動:背景重讀一次。別處剛存的(找缺那一列的分鐘數、另一個分頁)才看得到,
    // 他接著改、存也不會被擋成「設定在別的地方改過了」。先照舊的畫,讀好再重畫(他已經開始改就不重畫,免得搶走游標)
    if(CFGAWAY&&!CFGDIRTY)cfgLoad(function(){if(active==='cfg'&&!CFGDIRTY)renderCfg();});
    CFGAWAY=false;
    var resumes=CFGW.resume.resumes||[], attachments=CFGW.resume.attachments||[];
    var a=CFGW.agent||{}, agents=a.agents||[], b=CFGW.browser||{}, s=CFGW.search||{};
    var h=cfgMigrationHTML()+cfgChecklist()+cfgDoctorHTML();
    var RES=fold('cfg:resumes','📄 你的履歷 <span class="n">'+resumes.length+'</span>',
      '<p class="cfg-help">勾選要使用的履歷,寫一句「什麼時候用」幫 '+esc(AGENT)+' 挑選;卡片上也能改用哪一份。每份履歷可放各語言的檔案。</p>'+
      resumes.map(function(r,i){return cfgResumeHTML(r,i);}).join('')+
      '<div class="cfg-row"><input class="cfg-in" id="cfg-rnew" placeholder="新履歷名稱,例:工程版"><button class="cfg-b" type="button" data-cfresadd="1">＋ 新增履歷</button></div>'+
      '<div class="cfg-row"><label class="cfg-k">履歷有哪些語言</label>'+cfgLangChecks(CFGW.resume.langs||[])+'</div>'+
      '<div class="cfg-row"><label class="cfg-k">你看得懂的</label>'+cfgReadLang(CFGW.resume.read_lang||'zh')+'</div>'+
      '<p class="cfg-help">常用答案裡跟這個語言不是同一套文字的答案(例:你看中文、表單是英文),會多附一份這個語言的給你看、給你改。</p>',
      {cls:'cfg-d'});
    var ATT=fold('cfg:attachments','📎 你的附件 <span class="n">'+attachments.length+'</span>',
      '<p class="cfg-help">勾選要附上的檔案,並選擇能搭哪幾份履歷;不勾任何履歷就能搭全部。每份附件可放各語言的檔案。</p>'+
      attachments.map(function(a,i){return cfgAttachmentHTML(a,i,resumes);}).join('')+
      '<div class="cfg-row"><input class="cfg-in" id="cfg-anew" placeholder="新附件名稱,例:作品集"><button class="cfg-b" type="button" data-cfattadd="1">＋ 新增附件</button></div>',
      {cls:'cfg-d'});
    var skills=(CFGD.skills||[]).filter(function(x){return x.kind!=='research';});   // 找缺與判斷的做法另外列
    var SKL=fold('cfg:skills','🪄 改履歷的規則<span class="n">'+skills.length+'</span>',
      '<p class="cfg-help">每份履歷、附件可指定自己的改履歷的規則。卡片勾選要客製的檔時，預設勾有指定規則的檔；沒指定仍可客製，會使用產品附的通用規則。</p>'+
      (skills.length?'<div class="cfg-picks">'+skills.map(function(x){return '<span class="cfg-pick">'+esc(x.name||x.path)+'</span>';}).join('')+'</div>':'<p class="cfg-file">還沒有改履歷的規則</p>')+
      '<div class="cfg-row"><label class="cfg-k">名稱</label><input class="cfg-in" id="cfg-skill-name" placeholder="例:工程履歷重點"></div>'+
      '<div class="cfg-row"><label class="cfg-k">做法</label><textarea class="cfg-in" id="cfg-skill-content" rows="5" placeholder="寫這份檔要怎麼配合職缺修改"></textarea></div>'+
      '<div class="cfg-row"><button class="cfg-b" type="button" data-cfskilladd="1">＋ 新增改履歷的規則</button></div>',{cls:'cfg-d'});
    var researchTasks=CFGD.research_skill_tasks||[], researchSkills=cfgGet('research.skills')||{};
    var RSK=fold('cfg:research-skills','🔎 找缺與判斷的做法',
      '<p class="cfg-help">五份做法可以分開替換。展開可看產品預設;按「拿這份預設改一份」可直接修改、儲存並選用。每輪仍由程式接上材料位置與交件格式。</p>'+
      researchTasks.map(function(x){return cfgResearchSkillHTML(x,researchSkills[x.key]);}).join(''),{cls:'cfg-d'});
    var PRF=fold('cfg:preferences','🧭 你的喜好','<p class="cfg-help">這裡分開顯示使用者自訂與 agent 假設。找缺和判斷會讀整份筆記;agent 不會改使用者自訂。你改過的假設會轉成使用者自訂。</p>'+
      '<div class="cfg-row"><label class="cfg-k">使用者自訂</label><textarea class="cfg-in big" rows="8" data-cft="preferences_custom" placeholder="使用者自己寫或改過的內容">'+esc(CFGT.preferences_custom||'')+'</textarea></div>'+
      '<div class="cfg-row"><label class="cfg-k">Agent 假設</label><textarea class="cfg-in big" rows="8" data-cft="preferences_agent" placeholder="目前還沒有 agent 假設">'+esc(CFGT.preferences_agent||'')+'</textarea></div>',{cls:'cfg-d'});
    var APL=fold('cfg:apply','📝 填表做法','<p class="cfg-help">幫你填表時原文交給 '+esc(AGENT)+':連結欄填什麼、哪些勾選框要勾、地址怎麼寫…</p>'+
      '<textarea class="cfg-in big" rows="8" data-cft="apply_rules" placeholder="例:連結欄填 GitHub:https://github.com/你的帳號">'+esc(CFGT.apply_rules||'')+'</textarea>',{cls:'cfg-d'});
    var CAT=fold('cfg:cats','🗂 分類','<p class="cfg-help">類別是一張職缺在做什麼(一張只屬一類,從上往下比,都沒中就是最後一類);標籤是額外的特徵(一張可以有好幾個)。'+
      '卡片分錯了,在卡上 ⋯ 的「🗂 改類別」直接改。</p>'+cfgSuggestHTML()+
      '<div class="cfg-k">類別</div>'+cfgListHTML('categories',true)+'<div class="cfg-k">標籤</div>'+cfgListHTML('tags',false),{cls:'cfg-d'});
    var al=(CFGW.board&&CFGW.board.company_alias)||{}, alk=Object.keys(al);
    var ALS=fold('cfg:alias','🏢 公司別名 <span class="n">'+alk.length+'</span>','<p class="cfg-help">同一家公司的不同寫法。看板上公司列 ⋯ 的「🔗 跟別家是同一家」會加到這裡。</p>'+
      (alk.length?alk.map(function(k){return '<div class="cfg-li"><span class="cfg-file">'+esc(k)+' → <b>'+esc(al[k])+'</b></span>'+
        '<button class="cfg-x" type="button" data-cfaliasdel="'+escA(k)+'" aria-label="拿掉">✕</button></div>';}).join(''):'<p class="cfg-file">還沒有</p>')+
      '<p class="cfg-help">卡名「公司 · 職稱」裡,哪一段是職稱靠一份常見職稱字的清單認。你這一行的職稱它認不出來(公司被分成職稱名),加在這裡,一行一個。</p>'+
      '<div class="cfg-row"><label class="cfg-k">職稱字</label><textarea class="cfg-in" rows="3" data-cfl="board.title_words" placeholder="例:Barista">'+esc(((CFGW.board||{}).title_words||[]).join('\n'))+'</textarea></div>',{cls:'cfg-d'});
    var SRC=fold('cfg:search','🔎 找缺','<p class="cfg-help">職稱一出現就不送的字(不花 '+esc(AGENT)+');容易誤中、要交給判斷那一段看的字。一行一個。</p>'+
      '<div class="cfg-row"><label class="cfg-k">一定不要</label><textarea class="cfg-in" rows="4" data-cfl="search.exclude_words" placeholder="例:業務">'+esc((s.exclude_words||[]).join('\n'))+'</textarea></div>'+
      '<div class="cfg-row"><label class="cfg-k">要小心</label><textarea class="cfg-in" rows="3" data-cfl="search.flag_words" placeholder="例:community">'+esc((s.flag_words||[]).join('\n'))+'</textarea></div>',{cls:'cfg-d'});
    var RPL=fold('cfg:replies','📬 查應徵進度','<p class="cfg-help">投出去之後,'+esc(AGENT)+' 去信箱和平台看有沒有回音;超過這麼多天都沒消息就標成沒下文。</p>'+
      '<div class="cfg-row"><label class="cfg-k">沒下文天數</label><input class="cfg-in num" type="number" min="7" max="120" data-cfn="replies.ghost_days" value="'+escA((CFGW.replies||{}).ghost_days==null?30:CFGW.replies.ghost_days)+'"></div>'+   // 照實顯示:存成 0 的舊值要看得到才改得了
      '<p class="cfg-help">查應徵進度的信箱。Gmail 的第二個帳號是 …/mail/u/1/;不是 Gmail(Outlook、公司信箱)也行,'+esc(AGENT)+' 會用它的 Chrome 打開這個網址查,記得先在那個 Chrome 登入。</p>'+
      '<div class="cfg-row"><label class="cfg-k">信箱網址</label><input class="cfg-in" data-cf="replies.mail_url" value="'+escA((CFGW.replies||{}).mail_url||'')+'" placeholder="https://mail.google.com/mail/u/0/"></div>',{cls:'cfg-d'});
    var agentRows=agents.map(function(x,i){var runtime=['command-code','claude-code'].indexOf(x.runtime)>=0?x.runtime:'codex';
      return '<div class="cfg-card"><div class="cfg-row"><strong class="cfg-k">第 '+(i+1)+' 個 agent</strong>'+
        '<button class="cfg-b" type="button" data-cfagentmove="'+i+'|up" aria-label="上移"'+(i===0?' disabled':'')+'>↑</button>'+
        '<button class="cfg-b" type="button" data-cfagentmove="'+i+'|down" aria-label="下移"'+(i===agents.length-1?' disabled':'')+'>↓</button>'+
        '<button class="cfg-b" type="button" data-cfagentdel="'+i+'" aria-label="刪除 agent"'+(agents.length<=1?' disabled':'')+'>刪除</button></div>'+
        '<div class="cfg-row"><label class="cfg-k">執行環境</label><select class="cfg-in" data-cfa-runtime="'+i+'">'+[['codex','Codex'],['command-code','Command Code'],['claude-code','Claude Code']].map(function(o){return '<option value="'+o[0]+'"'+(runtime===o[0]?' selected':'')+'>'+o[1]+'</option>';}).join('')+'</select>'+
        '<input class="cfg-in" data-cfa-model="'+i+'" value="'+escA(x.model||'')+'" placeholder="模型(空的=預設)"></div>'+
        (runtime==='codex'?'<div class="cfg-row"><label class="cfg-k">速度</label><select class="cfg-in" data-cfa-speed="'+i+'">'+['standard','fast'].map(function(v){return '<option value="'+v+'"'+((x.speed||'standard')===v?' selected':'')+'>'+(v==='fast'?'快速(比較耗額度)':'標準')+'</option>';}).join('')+'</select></div>'
          :'<div class="cfg-row"><span class="cfg-k">速度</span><span class="cfg-help">Claude Code 沒有這個選項</span></div>')+
        '<div class="cfg-row"><label class="cfg-k">思考強度</label><select class="cfg-in" data-cfa-effort="'+i+'">'+[['low','低'],['medium','中'],['high','高'],['xhigh','很高'],['max','最高']].map(function(o){return '<option value="'+o[0]+'"'+((x.effort||'max')===o[0]?' selected':'')+'>'+o[1]+'</option>';}).join('')+'</select>'+
        '<label class="cfg-file"><input type="checkbox" data-cfa-browser="'+i+'"'+(x.browser?' checked':'')+(runtime==='command-code'?' disabled':'')+'> 用它操作 Chrome(只能選一個)</label></div></div>';}).join('');
    var br=cfgBrowserRuntime();
    var AGT=fold('cfg:agent','🤖 Agent 與瀏覽器','<p class="cfg-help">先用第 1 個;它額度用完、被限流或開不起來,才換下一個(逾時、其他錯誤不換)。'+
      '要開 Chrome 的工作(填表、查應徵進度)只交給勾了「用它操作 Chrome」的那一個,它不行也不會換別的。</p>'+agentRows+
      '<div class="cfg-row"><button class="cfg-b" type="button" data-cfagentadd="1">＋ 新增 agent</button></div>'+
      // 幫你填表用的 Chrome:只畫勾了的那一家的連接(另一家用不到,畫出來只會讓人以為兩個都要連);只在第一次建立時才有意義的欄位才畫
      '<p class="cfg-help"><b>幫你填表用的 Chrome</b>:jobsalvo 自己開的另一個正常的 Chrome,一直在背景跑,不會跳到你面前,也碰不到你平常用的 Chrome。'+
        '要看它填的頁,按卡上的「👀 看現在的頁面」'+(br==='claude-code'?'(Claude 的頁每按一次截一次)':'(開著會一直更新)')+'。'+
        '<a href="https://github.com/GongYuanCaiJi/jobsalvo/blob/main/docs/agent-chrome.md" target="_blank" rel="noopener">📖 設定教學</a></p>'+
      // agent 的 Chrome 第一次建立(按連接或 🔑 時)才用得到:放在連接之前,先選再按
      (CFGD.agent_chrome_made?'':'<div class="cfg-row"><label class="cfg-k" for="cfg-chprof">從哪個設定檔複製</label>'+chromeProfilePick(b.profile||'')+
        '<span class="cfg-help">第一次按連接或「🔑 打開」時會建立 agent 的 Chrome,把你選的 Chrome 設定檔的登入狀態和擴充功能複製過去(選填;不複製就自己在它裡面登入)</span></div>')+
      (br==='codex'?'<div class="cfg-row"><label class="cfg-k">Codex</label><span class="cfg-file">'+(CFGD.browser_ok?'✅ '+(CFGD.browser_ok===true?'連接過':esc(mdOf(CFGD.browser_ok))+' 確認連得上'):'⬜ 還沒連接')+'</span>'+
        '<button class="cfg-b" type="button" data-cfbrowser="setup">🔌 連接 Codex</button>'+
        '<span class="cfg-help">要先在 agent 的 Chrome 裝 Codex 的擴充功能(商店上叫 ChatGPT);上傳履歷、下載附件前還要在 ~/.codex/browser/config.toml 允許那些網站(看設定教學)</span></div>':'')+
      (br==='claude-code'?'<div class="cfg-row"><label class="cfg-k">Claude</label><span class="cfg-file">'+(CFGD.claude_paired?'✅ '+esc(mdOf(CFGD.claude_paired))+' 確認連得上':'⬜ 還沒連接')+'</span>'+
        '<button class="cfg-b" type="button" data-cfbrowser="claude">🔌 連接 Claude</button>'+
        '<span class="cfg-help">要先在 agent 的 Chrome 登入 claude.ai;還沒登入,按下去會把它開在你面前</span></div>':'')+
      (br?'':'<p class="cfg-help">⚠ 還沒有 agent 勾「用它操作 Chrome」:填表、查應徵進度會先停著。</p>')+
      '<div class="cfg-row"><label class="cfg-k">打開它</label><button class="cfg-b" type="button" data-cfbrowser="show">🔑 打開 agent 的 Chrome</button>'+
        '<span class="cfg-help">登入 Gmail、104、LinkedIn,或親手看它開著的頁;只有你按了才會出現</span></div>',{cls:'cfg-d'});
    var SYS=fold('cfg:sys','⚙ 其他','<div class="cfg-row"><label class="cfg-k">資料夾</label><span class="cfg-file">'+esc(CFGD.home||'')+'</span></div>'+
      '<div class="cfg-row"><label class="cfg-k">開機自動啟動</label><span class="cfg-file">'+(CFGD.service?'✅ 已開':'⬜ 沒開')+'</span>'+
      '<button class="cfg-b" type="button" data-cfservice="'+(CFGD.service?'remove':'install')+'">'+(CFGD.service?'關掉':'打開')+'</button></div>'+
      '<div class="cfg-row" id="cfg-update"><label class="cfg-k">版本</label><span class="cfg-file">查詢中…</span></div>'+cfgHistoryHTML(),{cls:'cfg-d'});
    // 十一塊照「在做哪件事」分四組,不再一字排開
    function grp(t,parts){return '<h3 class="cfg-grp">'+t+'</h3>'+parts.join('');}
    var fl=CFGW.flow||{}, chk=function(k,t,d){return '<label class="cfg-row"><input type="checkbox" data-cfcheck="flow.'+k+'"'+(fl[k]?' checked':'')+'> '+t+
      (d?'<span class="cfg-file">'+d+'</span>':'')+'</label>';};
    var FLW=fold('cfg:flow','🔁 自動流程','<p class="cfg-help">勾起來的步驟,看板會替你自動做(一次一張,失敗了就停,原因寫在卡上)。你只要做兩件事:表態,和最後按「✅ 確認送出」。<b>永遠不會自動送出。</b></p>'+
      chk('like_to_prep','按 👍 就送去準備履歷中')+chk('auto_prep','有新卡要準備就自動準備履歷')+
      chk('auto_advance','準備好、驗收過的卡自動進「可以投了」','被擋住的留在「待你決定」;要不要客製改到「可以投了」那張卡上決定')+
      chk('auto_fill','進「可以投了」就讓 '+esc(AGENT)+' 填表,答案改過就照新的重打;都停在送出前')+
      '<div class="cfg-row"><label class="cfg-k">最多停幾張等你確認送出</label><input class="cfg-in num" data-cf="flow.fill_max" value="'+escA(fl.fill_max==null?5:fl.fill_max)+'" placeholder="5(0=不限,空的照 5)"></div>'+
      '<p class="cfg-help">每一張填好的都開著一個分頁等你看。停滿了就先不填新的,你確認送出或退掉一張它就接著填;手動按的不受這個限制。</p>'+
      '<label class="cfg-row"><input type="checkbox" data-cfreplies="1"'+(fl.replies_at?' checked':'')+'> 每天自動查應徵進度'+
        (fl.replies_at?'<input class="cfg-in num" data-cf="flow.replies_at" value="'+escA(fl.replies_at)+'" placeholder="09:00" aria-label="每天幾點查">':'')+'</label>'+
      '<p class="cfg-help">打開某一項之前就已經在那一步的卡不會自動處理;到那一頁按「之前的 N 張也交給自動」。</p>',{cls:'cfg-d'});
    h+=grp('你的資料',[RES,ATT,SKL])+grp('找缺與判斷',[SRC,RSK,PRF,CAT,ALS])+grp('投遞',[FLW,APL,RPL])+grp('Agent 與系統',[AGT,SYS]);
    // 有改動才黏在畫面底下、亮起來;沒改動就安靜待在最底下
    h+='<div class="cfg-save'+(CFGDIRTY?' dirty':'')+'" id="cfg-save"><button class="cfg-b go" type="button" data-cfsave="1">💾 儲存設定</button><span class="cfg-file" id="cfg-st">'+(CFGDIRTY?'● 有改動還沒存':'沒有改動')+'</span>'+
      // 放棄改動:合併公司、改用 X 這些會存設定的動作遇到沒存的改動會叫他「先存或放棄」,這裡就要有放棄那一顆
      (CFGD.conflict?'<button class="cfg-b" type="button" data-cfreload="1">重新讀取(這頁沒存的改動會丟掉)</button>'
        :'<button class="cfg-b" type="button" data-cfreload="1" id="cfg-discard"'+(CFGDIRTY?'':' hidden')+'>放棄改動</button>')+'</div>';
    $('app').innerHTML=h; autogrowAll($('app')); cfgUpdateFill(); cfgNameFields($('app'));
  }
  function cfgMark(){CFGDIRTY=cfgSnap()!==CFG0; var st=$('cfg-st'); if(st)st.textContent=CFGDIRTY?'● 有改動還沒存':'沒有改動';
    var sv=$('cfg-save'); if(sv)sv.classList.toggle('dirty',CFGDIRTY);
    var dc=$('cfg-discard'); if(dc)dc.hidden=!CFGDIRTY;}
  function cfgSave(reload){
    // 規則寫錯(分類、標籤的關鍵字):後台照它真的會拒絕的那幾條回原因(settings_api),設定頁照著顯示,不自己先擋
    return fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({settings:CFGW,texts:CFGT,version:CFGD.version})}).then(function(r){return r.json().then(function(d){
        // 別處改過設定(409):存檔列多一顆「重新讀取」,他自己決定要不要丟掉這頁沒存的改動
        if(r.status===409){CFGD.conflict=true; if(active==='cfg')renderCfg();}
        if(!r.ok||!d.ok)throw new Error(d.msg||('http '+r.status));
        CFGDIRTY=false; CFGD.version=d.version; CFG0=cfgSnap();
        refreshShip();   // 換了原始檔、勾選、語言:每張卡的要寄的檔案照新的設定重拿,不用重新整理
        if(reload){snack('設定存好了,重新載入看板…'); setTimeout(function(){location.reload();},700);}
        return d;});});
  }
  // 檔已經傳上去、記進工作副本了,接著存設定。存不進去(別的地方有錯)要分開講:以前一律說「上傳失敗」,那一列也不重畫,他以為檔沒傳好
  function cfgSaveUploaded(name,okMsg){
    return cfgSave(false).then(function(){snack(okMsg); cfgLoad(renderCfg);},
      function(err){cfgMark(); renderCfg(); snack('「'+name+'」傳好了,但設定沒存成('+err.message+'),改好再按「💾 儲存設定」');});}
  function cfgSlug(name,list,prefix){var s=String(name||'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,24);
    var used={}; (list||[]).forEach(function(x){used[x.id]=1;});
    if(!s||!/^[a-z0-9]/.test(s))s=(prefix||'item')+(list.length+1);
    var base=s,i=2; while(used[s])s=base+'-'+(i++); return s;}
  document.addEventListener('input',function(e){
    if(active!=='cfg'||!CFGW)return;
    var t=e.target, k;
    if((k=t.getAttribute('data-cfa-model'))!=null){CFGW.agent.agents[+k].model=t.value; return cfgMark();}
    if((k=t.getAttribute('data-cf'))){cfgSet(k,t.value); return cfgMark();}
    if((k=t.getAttribute('data-cft'))){CFGT[k]=t.value; return cfgMark();}
    if((k=t.getAttribute('data-cfn'))){cfgSet(k,parseInt(t.value,10)||0); return cfgMark();}
    if((k=t.getAttribute('data-cfl'))){cfgSet(k,t.value.split(/[\n,]+/).map(function(x){return x.trim();}).filter(Boolean)); return cfgMark();}
    if((k=t.getAttribute('data-cfli'))){var p=k.split('|'); CFGW.board[p[0]][+p[1]][p[2]]=t.value; return cfgMark();}
  });
  document.addEventListener('change',function(e){
    if(active!=='cfg'||!CFGW)return;
    var t=e.target, k;
    var selected=e.target.closest&&e.target.closest('select[data-cf]');
    if(selected){cfgSet(selected.getAttribute('data-cf'),selected.value); cfgMark(); return;}
    if((k=t.getAttribute('data-cfb'))){cfgSet(k,t.checked); cfgMark(); return;}
    var check=e.target.closest&&e.target.closest('input[data-cfcheck]');
    if(check){cfgSet(check.getAttribute('data-cfcheck'),check.checked); cfgMark(); return;}
    // 每天自動查應徵進度:關掉 = 時間清空(程式本來就把空的當成不查);打開先給 09:00,可以改
    var rq=e.target.closest&&e.target.closest('input[data-cfreplies]');
    if(rq){cfgSet('flow.replies_at',rq.checked?(CFGW.flow.replies_at||'09:00'):''); cfgMark(); renderCfg(); return;}
    var eligible=t.closest&&t.closest('input[data-cfeligible]');
    if(eligible){
      var ep=eligible.getAttribute('data-cfeligible').split('|'), aid=ep[0], rid=ep[1];
      var a=(CFGW.resume.attachments||[]).find(function(x){return x.id===aid;});
      if(!a)return;
      var all=(CFGW.resume.resumes||[]).map(function(x){return x.id;});
      var ids=(a.resume_ids&&a.resume_ids.length)?a.resume_ids.filter(function(x){return all.indexOf(x)>=0;}):all.slice();
      ids=ids.filter(function(x){return x!==rid;});
      if(eligible.checked)ids.push(rid);
      a.resume_ids=ids.length===all.length?[]:ids;
      cfgMark(); renderCfg(); return;
    }
    if((k=t.getAttribute('data-cfa-runtime'))!=null){var entry=CFGW.agent.agents[+k]; entry.runtime=t.value;
      if(entry.runtime==='command-code')entry.browser=false; cfgMark(); renderCfg(); return;}
    if((k=t.getAttribute('data-cfa-speed'))!=null){CFGW.agent.agents[+k].speed=t.value; cfgMark(); return;}
    if((k=t.getAttribute('data-cfa-effort'))!=null){CFGW.agent.agents[+k].effort=t.value; cfgMark(); return;}
    if((k=t.getAttribute('data-cflang'))!=null){var langs0=(CFGW.resume.langs||[]).slice(), ls=CFGW.resume.langs=langs0.filter(function(x){return x!==k;});
      if(t.checked)ls.push(k); else if(!ls.length){t.checked=true; ls.push(k); snack('至少要留一種語言'); return;}
      // 取消勾:那個語言的檔一起拿掉(存了才算數,可以復原)。以前檔留在設定裡、畫面上不畫,存檔被擋又找不到是哪個檔
      var taken=[];
      if(!t.checked)(CFGW.resume.resumes||[]).concat(CFGW.resume.attachments||[]).forEach(function(x){
        ['files','styles'].forEach(function(f){if(x[f]&&x[f][k]!=null){taken.push([x[f],x[f][k]]); delete x[f][k];}});});
      cfgMark(); renderCfg();
      if(taken.length)snack('拿掉了'+langName(k)+'版的 '+taken.length+' 個檔(存了才算數)',function(){
        taken.forEach(function(e){e[0][k]=e[1];}); CFGW.resume.langs=langs0; cfgMark(); renderCfg();});
      return;}
    if((k=t.getAttribute('data-cfa-browser'))!=null){CFGW.agent.agents.forEach(function(x,i){x.browser=t.checked&&i===+k;}); cfgMark(); renderCfg(); return;}
    var styleInput=e.target.closest&&e.target.closest('input[data-cfstyle]');
    if(styleInput&&styleInput.files&&styleInput.files[0]){
      var sp=styleInput.getAttribute('data-cfstyle').split('|'), skind=sp[0], sid=sp[1], slang=sp[2], sf=styleInput.files[0];
      var slist=skind==='resume'?CFGW.resume.resumes:CFGW.resume.attachments;
      var srecord=(slist||[]).find(function(x){return x.id===sid;}); if(!srecord)return;
      if(!/\.css$/i.test(sf.name)){snack('樣式檔請選 CSS');return;}
      var srel='resume/'+sid+(skind==='attachment'?'-att':'')+'/'+slang+'/'+uploadName(sf.name,'.css');
      snack('上傳樣式中…');
      putFile('/api/file?path='+encodeURIComponent(srel),sf).then(function(d){
        srecord.styles=srecord.styles||{}; srecord.styles[slang]=d.path; return cfgSaveUploaded(sf.name,'PDF 樣式已存好');
      }).catch(function(err){snack('上傳失敗:'+err.message);});
      return;
    }
    var inp=e.target.closest&&e.target.closest('input[data-cfup]'); if(!inp||!inp.files||!inp.files[0])return;
    var p=inp.getAttribute('data-cfup').split('|'), kind=p[0], id=p[1], lang=p[2], f=inp.files[0];
    var list=kind==='resume'?CFGW.resume.resumes:CFGW.resume.attachments;
    var record=(list||[]).find(function(x){return x.id===id;}); if(!record)return;
    // 保留使用者原本的檔名:代投上傳給雇主的就是這個檔,以前改成「<id>-<語言>.pdf」,中文名的履歷變成 resume-2-zh.pdf 這種。
    // 每份、每個語言一個資料夾,兩個語言同名也不會互蓋。
    var rel='resume/'+id+(kind==='attachment'?'-att':'')+'/'+lang+'/'+uploadName(f.name,'.pdf');
    snack('上傳中…');
    putFile('/api/file?path='+encodeURIComponent(rel),f).then(function(d){
      record.files=record.files||{}; record.files[lang]=d.path;
      return cfgSaveUploaded(f.name,'「'+f.name+'」傳好了,也存進設定了');
    }).catch(function(err){snack('上傳失敗:'+err.message);});
  });
  $('app').addEventListener('click',function(e){
    if(active!=='cfg'||!CFGW)return;
    var t=e.target.closest('button'); if(!t)return;
    var g;
    if((g=t.getAttribute('data-cfgo'))){cfgGo(g); return;}
    if(t.hasAttribute('data-cfsave')){cfgSave(true).catch(function(err){snack('沒存成('+err.message+'),照上面說的改好再按一次「💾 儲存設定」');}); return;}
    if(t.hasAttribute('data-cfreload')){CFGDIRTY=false; cfgLoad(renderCfg); return;}
    if(t.hasAttribute('data-cfagentadd')){var entries=CFGW.agent.agents, id;
      do{id='agent-'+Date.now().toString(36)+'-'+Math.floor(Math.random()*1e9).toString(36);}
      while(entries.some(function(x){return x.id===id;}));
      // 只准一個 agent 用 Chrome:已經有人勾了,新的就不勾
      entries.push({id:id,runtime:'codex',model:'',effort:'max',speed:'standard',browser:!entries.some(function(x){return x.browser;})});
      cfgMark(); renderCfg(); return;}
    if((g=t.getAttribute('data-cfagentdel'))!=null){if(CFGW.agent.agents.length<=1){snack('至少要留一個 agent');return;}
      var removed=CFGW.agent.agents.splice(+g,1)[0]; cfgMark(); renderCfg();
      snack('刪掉了 agent「'+removed.id+'」(存了才算數)',function(){CFGW.agent.agents.splice(+g,0,removed); cfgMark(); renderCfg();}); return;}
    if((g=t.getAttribute('data-cfagentmove'))){var move=g.split('|'), from=+move[0], to=from+(move[1]==='up'?-1:1);
      if(to<0||to>=CFGW.agent.agents.length)return;
      var moved=CFGW.agent.agents.splice(from,1)[0]; CFGW.agent.agents.splice(to,0,moved); cfgMark(); renderCfg(); return;}
    if((g=t.getAttribute('data-cfresearch-copy'))){
      var editor=document.querySelector('[data-cfresearch-editor="'+g+'"]');
      if(!editor)return;
      editor.hidden=false;
      var nameField=editor.querySelector('[data-cfresearch-name="'+g+'"]');
      if(nameField)nameField.focus();
      return;
    }
    if((g=t.getAttribute('data-cfresearch-save'))){
      var nameField=document.querySelector('[data-cfresearch-name="'+g+'"]');
      var contentField=document.querySelector('[data-cfresearch-content="'+g+'"]');
      var skillName=((nameField||{}).value||'').trim(), skillContent=((contentField||{}).value||'').trim();
      if(!skillName){snack('先寫這份做法的名稱');return;}
      if(!skillContent){snack('先寫這份做法的內容');return;}
      t.disabled=true;
      cfgSave(false).then(function(){return fetch('/api/settings/skill',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:skillName,content:skillContent,kind:'research'})});})
        .then(function(r){return r.json().then(function(d){if(!r.ok||!d.ok)throw new Error(d.msg||('http '+r.status));return d;});})
        .then(function(d){
          CFGW.research=CFGW.research||{}; CFGW.research.skills=CFGW.research.skills||{};
          CFGW.research.skills[g]=d.skill.path; CFGDIRTY=true;
          return cfgSave(false);
        })
        .then(function(){
          FOLD['cfg:research-skills']=1;
          snack('找缺與判斷的做法已存好並選用');
          cfgLoad(renderCfg);
        })
        .catch(function(err){snack('新增失敗('+err.message+'),改一下再按一次');t.disabled=false;});
      return;
    }
    if(t.hasAttribute('data-cfskilladd')){
      var sn=(($('cfg-skill-name')||{}).value||'').trim(), sc=(($('cfg-skill-content')||{}).value||'').trim();
      if(!sn){snack('先寫改履歷的規則的名稱');return;} if(!sc){snack('先寫改履歷的規則的內容');return;}
      t.disabled=true;
      cfgSave(false).then(function(){return fetch('/api/settings/skill',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:sn,content:sc})});}).then(function(r){return r.json().then(function(d){if(!r.ok||!d.ok)throw new Error(d.msg||('http '+r.status));return d;});})
        .then(function(){snack('改履歷的規則已存好，現在可以在履歷或附件上選它');cfgLoad(renderCfg);})
        .catch(function(err){snack('新增失敗('+err.message+'),改一下再按一次');t.disabled=false;});
      return;}
    if(t.hasAttribute('data-cfresadd')){var nm=($('cfg-rnew')||{}).value||''; if(!nm.trim()){snack('先寫新履歷的名稱');return;}
      var resumes=CFGW.resume.resumes||(CFGW.resume.resumes=[]), id=cfgSlug(nm,resumes,'resume');
      resumes.push({id:id,name:nm.trim(),files:{},enabled:true,when:'',skill:''}); cfgMark(); FOLD['cfg:resumes']=1; renderCfg(); return;}
    if(t.hasAttribute('data-cfattadd')){var nm=($('cfg-anew')||{}).value||''; if(!nm.trim()){snack('先寫新附件的名稱');return;}
      var attachments=CFGW.resume.attachments||(CFGW.resume.attachments=[]), aid=cfgSlug(nm,attachments,'attachment');
      attachments.push({id:aid,name:nm.trim(),files:{},enabled:true,resume_ids:[],skill:''}); cfgMark(); FOLD['cfg:attachments']=1; renderCfg(); return;}
    if((g=t.getAttribute('data-cfresdel'))){
      var resumes=CFGW.resume.resumes||[], ri=resumes.findIndex(function(x){return x.id===g;}); if(ri<0)return;
      var old=resumes.splice(ri,1)[0];
      cfgMark(); renderCfg();
      snack('刪掉履歷「'+(old.name||g)+'」(存了才算數)',function(){
        resumes.splice(ri,0,old); cfgMark(); renderCfg();});
      return;}
    if((g=t.getAttribute('data-cfattdel'))){
      var attachments=CFGW.resume.attachments||[], ai=attachments.findIndex(function(x){return x.id===g;}); if(ai<0)return;
      var old=attachments.splice(ai,1)[0]; cfgMark(); renderCfg();
      snack('刪掉附件「'+(old.name||g)+'」(存了才算數)',function(){attachments.splice(ai,0,old); cfgMark(); renderCfg();}); return;}
    if((g=t.getAttribute('data-cfliadd'))){CFGW.board[g].splice(g==='categories'?Math.max(0,CFGW.board[g].length-1):CFGW.board[g].length,0,{name:'',icon:'',match:''}); cfgMark(); renderCfg(); return;}
    if((g=t.getAttribute('data-cfaliasdel'))!=null){var al2=Object.assign({},CFGW.board.company_alias||{}), was=al2[g]; delete al2[g];
      CFGW.board.company_alias=al2; cfgMark(); renderCfg();
      snack('拿掉「'+g+'」(存了才算數)',function(){CFGW.board.company_alias[g]=was; cfgMark(); renderCfg();}); return;}
    if((g=t.getAttribute('data-cflidel'))){var q2=g.split('|'); CFGW.board[q2[0]].splice(+q2[1],1); cfgMark(); renderCfg(); return;}
    if(t.hasAttribute('data-cfsuggest')){startRun('suggest',{},t); return;}
    if(t.hasAttribute('data-cfsugapply')){var sg=CFGD.suggest; CFGW.board.categories=_clone(sg.categories); CFGW.board.tags=_clone(sg.tags||[]);
      cfgMark(); renderCfg(); snack('套用了,按「💾 儲存設定」才會生效'); return;}
    if((g=t.getAttribute('data-cfbrowser'))){
      var go=function(force){t.disabled=true; t.textContent=g==='setup'?'連接中…(最多一分半)':g==='claude'?'確認中…(最多 2 分鐘)':'打開中…';
        fetch('/api/settings/browser',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({act:g,force:!!force})})
          .then(function(r){return r.json();}).then(function(d){
            // 連接要把 agent 的 Chrome 關掉重開,填好等他核對的頁會一起不見:伺服器先問,他確定了才關
            if(d.confirm){if(confirm(d.msg))go(true); else cfgLoad(renderCfg); return;}
            snack(d.msg||(d.ok?'好了':'沒成功')); cfgLoad(renderCfg);
            // Claude 要他在跳出來的視窗登入:伺服器登入好會自己記下,這裡每 20 秒重讀一次,連上了就換成「確認連得上」
            if(g==='claude'&&!d.ok)(function poll(n){setTimeout(function(){if(active!=='cfg'||n>45)return; if(CFGDIRTY){poll(n+1);return;}
              cfgLoad(function(){if(CFGD.claude_paired)renderCfg(); else poll(n+1);});},20000);})(0);});};
      if(CFGDIRTY)cfgSave(false).then(function(){go();}).catch(function(err){snack('設定沒存成('+err.message+'),照上面說的改好再按一次');}); else go();
      return;}
    if((g=t.getAttribute('data-cfuseagent'))){
      if(CFGDIRTY){snack('設定頁有改動還沒存,先存或放棄再按'); return;}
      t.disabled=true;
      fetch('/api/settings/use_agent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({runtime:g})})
        .then(function(r){return r.json();}).then(function(d){snack(d.msg||(d.ok?'好了':'沒成功')); cfgLoad(renderCfg);})
        .catch(function(){snack('沒成功,再試一次'); t.disabled=false;});
      return;}
    if((g=t.getAttribute('data-cfservice'))){
      fetch('/api/settings/service',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({act:g})})
        .then(function(r){return r.json();}).then(function(d){snack(d.msg||(d.ok?'好了':'沒成功')); cfgLoad(renderCfg);});
      return;}
  });
  // 「看紀錄」:派 agent 的那幾種跑到一半失敗時,使用者不用開檔案,在這裡看最近的輸出。
  function showLog(kind){
    fetch('/api/log?kind='+encodeURIComponent(kind)).then(function(r){return r.text();}).then(function(t){
      modal('<div class="rzm-box"><button class="rzm-x" type="button">✕ 關閉</button><pre class="log-pre">'+esc(t)+'</pre></div>','');});
  }
  document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-addurls]');
    if(!b)return; var ta=$('add-urls'), txt=(ta&&ta.value||'').trim();
    if(!/https?:\/\//.test(txt)){snack('先貼職缺網址(http 開頭)'); if(ta)ta.focus(); return;}
    startRun('add',{text:txt},b); if(ta)ta.value='';
    snack('加入中:程式抓 JD、'+AGENT+' 寫卡片摘要,好了會出現在「🆕 新職缺」');});
  document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-showlog]');
    if(b){e.preventDefault(); showLog(b.getAttribute('data-showlog'));}});
  // ---- 🎤 題目編輯:新增/改/刪一題都在網頁上做(伺服器 /api/bank,純文字進、看板格式出) ----
  var IV_EDIT_ST=[['todo','還沒答'],['first','答過第一輪'],['wip','磨合中'],['ok','定案,可以上場']];
  function ivFormHTML(f,cats){
    var cs=(cats||[]).slice(); if(f.cat&&cs.indexOf(f.cat)<0)cs.push(f.cat);
    function row(k,inner){return '<div class="cfg-row"><label class="cfg-k">'+k+'</label>'+inner+'</div>';}
    return '<div class="rzm-box iv-edit"><button class="rzm-x" type="button">✕ 關閉</button>'+
      '<h3>'+(f.id?'編輯題目':'新增題目')+'</h3><input type="hidden" id="ive-id" value="'+escA(f.id||'')+'">'+
      row('題目','<input class="cfg-in" id="ive-t" value="'+escA(f.t||'')+'" placeholder="例:請簡單自我介紹">')+
      row('大類','<select class="cfg-in" id="ive-cat">'+cs.map(function(c){return '<option'+(c===f.cat?' selected':'')+'>'+esc(c)+'</option>';}).join('')+'</select>'+
        '<input class="cfg-in" id="ive-sub" value="'+escA(f.sub||'')+'" placeholder="小類(可空)">')+
      row('走到哪','<select class="cfg-in" id="ive-stage">'+IV_EDIT_ST.map(function(x){return '<option value="'+x[0]+'"'+(x[0]===f.stage?' selected':'')+'>'+x[1]+'</option>';}).join('')+'</select>'+
        '<input class="cfg-in num" type="number" min="0" id="ive-lim" value="'+escA(f.lim||0)+'" title="限時秒數,0=不限">')+
      row('考過的公司','<input class="cfg-in" id="ive-asked" value="'+escA(f.asked||'')+'" placeholder="實際被問過的才寫,逗號分隔">')+
      row('題面','<textarea class="cfg-in big" rows="2" id="ive-ask">'+esc(f.ask||'')+'</textarea>')+
      row('考點','<textarea class="cfg-in big" rows="2" id="ive-focus">'+esc(f.focus||'')+'</textarea>')+
      '<div class="cfg-help">逐字稿:空一行就是下一段;一行用「## 」開頭是小標(例如「## 60 秒版」「## 90 秒版」)。</div>'+
      '<textarea class="cfg-in big" rows="12" id="ive-script">'+esc(f.script||'')+'</textarea>'+
      '<div class="cfg-row end">'+(f.id?'<button class="cfg-b warn" type="button" data-ivedel="1">🗑 刪掉這題</button>':'')+
      '<button class="cfg-b go" type="button" data-ivesave="1">💾 儲存</button></div></div>';
  }
  function ivFormVal(){var g=function(i){return ($(i)||{}).value||'';};
    return {id:g('ive-id'),t:g('ive-t'),cat:g('ive-cat'),sub:g('ive-sub'),stage:g('ive-stage'),lim:parseInt(g('ive-lim'),10)||0,
            asked:g('ive-asked'),ask:g('ive-ask'),focus:g('ive-focus'),script:g('ive-script')};}
  function ivPost(body){
    return fetch('/api/bank',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){return r.json().then(function(d){if(!r.ok||!d.ok)throw new Error(d.msg||('http '+r.status));
        BANK=d.bank; D.bank=d.bank; _ivHay={}; return d;});});
  }
  function ivOpenEditor(id){
    if(!id){modal(ivFormHTML({stage:'todo',cat:''},(BANK&&BANK.cats)||['自我介紹','你這個人','職涯方向與動機','你怎麼做事','你做過的事','反問面試官','其他']),'');return;}
    fetch('/api/bank/form?id='+encodeURIComponent(id)).then(function(r){return r.json();}).then(function(d){
      if(!d.ok){snack('找不到這一題(可能剛被刪掉),重新整理再看');return;} modal(ivFormHTML(d.form,d.cats),'');});
  }
  document.addEventListener('click',function(e){
    var t=e.target.closest&&e.target.closest('[data-ivnew],[data-ivedit],[data-ivesave],[data-ivedel]'); if(!t)return;
    if(t.hasAttribute('data-ivnew')){ivOpenEditor('');return;}
    if(t.hasAttribute('data-ivedit')){e.preventDefault(); ivOpenEditor(t.getAttribute('data-ivedit'));return;}
    var f=ivFormVal();
    if(t.hasAttribute('data-ivesave')){
      if(!f.t.trim()){snack('先寫題目');return;}
      ivPost({op:'put',item:f}).then(function(){closeModal(); renderApp(); snack('存好了');}).catch(function(err){snack('沒存成('+err.message+'),再按一次;一直不行就重新整理');});
      return;}
    if(t.hasAttribute('data-ivedel')){
      ivPost({op:'del',id:f.id}).then(function(){closeModal(); renderApp();
        snack('刪掉了「'+f.t+'」',function(){ivPost({op:'put',item:f}).then(function(){renderApp();});});})
        .catch(function(err){snack('沒刪成:'+err.message);});
      return;}
  });
  // 全新的資料夾(沒有職缺、也還沒設履歷):直接落在設定頁,先把履歷、硬規則設好
  if(!jobs.length&&!RESUMES.length)active='cfg';
  rvRestore();
  renderAll();
  // 三條 bar 上的「📝 prompt」先把第一頁抓好放著:他點開就有,不會頓一下才出來。
  pvWarm(['find:deep','prep','apply:fill']);
  // 把上次看到的位置捲回去(哪個分頁/哪一類已經在載入時吃進去了)。
  // 等一拍讓卡片先定位,不然會捲到錯的地方。
  if(_restoreY>2){ if($('q')&&searchQuery)$('q').value=searchQuery;
    setTimeout(function(){
      var max=Math.max(0,document.documentElement.scrollHeight-window.innerHeight);
      window.scrollTo(0,Math.min(_restoreY,max));   // 公司收起來後頁面變矮,不要捲過頭
    },60); }
})();
