"""確認答案待辦的跳轉會展開並聚焦實際可編輯欄位。"""
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class AnswerJumpTest(unittest.TestCase):
    def test_answer_jump_opens_pending_and_confirmed_rows_and_focuses_editable_field(self):
        source = (ROOT / 'board/board.js').read_text()
        start = source.index('  function fold(key,sum,body,o)')
        fold = source[start:source.index('  // toggle ', start)]
        start = source.index('  function ansRowHTML(')
        row = source[start:source.index('  // 共用的按題目', start)]
        start = source.index("    var ago=e.target.closest('[data-ansgo]');")
        click = source[start:source.index('    var aadd=', start)]
        setup = r'''
const assert=require('node:assert/strict');
var FOLD={},ansQuery='把指定題目藏起來的舊搜尋',RL='中文',FL='English',NEXT={},entry,rendered,timers=[];
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
var escA=esc,CSS={escape:s=>s},focused=null,scrolled=null,focusOptions=null,classes=new Set();
function ansOf(key){assert.equal(key,entry.k);return entry;}
function ansNeed(e){return !!e.pending;}
var ansPending=ansNeed;
function ansEmpty(e){return !e.v;}
function ansUsers(){return [];}
function ansLim(){return 0;}
function wordsOf(){return 0;}
function ansLocked(){return false;}
function ansBi(){return false;}
function ansMetaText(){return '';}
function ansTxt(e){return e.q;}
function ansHeadHTML(e){return esc(e.q);}
function setTimeout(fn,ms){assert.equal(ms,2400);timers.push(fn);}
const field={scrollIntoView(opts){assert.equal(opts.block,'center');scrolled=this;},
  focus(opts){assert.ok(FOLD['ans:'+entry.k]);focused=this;focusOptions=opts;}};
const grow={scrollIntoView(opts){assert.equal(opts.block,'center');scrolled=this;},
  classList:{add:c=>classes.add(c),remove:c=>classes.delete(c)},
  querySelector(selector){assert.equal(selector,'.ans-t:not([readonly]):not(:disabled)');return field;}};
const document={querySelector(selector){assert.equal(selector,'#app .ansrow[data-k="'+entry.k+'"]');return grow;}};
function renderAll(){rendered=ansRowHTML(entry);}
function jump(){const e={target:{closest:s=>s==='[data-ansgo]'?{getAttribute:()=>entry.k}:null}};
'''
        check = r'''
}
for(const pending of [true,false]){
 entry={k:'adb675787',q:'請補上這個答案',v:pending?'':'已有答案',pending};
 const original=JSON.stringify(entry);FOLD={};ansQuery='舊搜尋';focused=null;scrolled=null;
 jump();
 assert.ok(FOLD.ans);assert.ok(FOLD['ans:'+entry.k]);
 assert.equal(!!FOLD['ans:done'],!pending);assert.equal(ansQuery,'');
 assert.ok(/<details[^>]*data-fold="ans:adb675787"[^>]* open>/.test(rendered));
 assert.equal(focused,field);assert.equal(scrolled,field);assert.deepEqual(focusOptions,{preventScroll:true});
 assert.equal(JSON.stringify(entry),original);assert.ok(classes.has('justhere'));
 timers.shift()();assert.ok(!classes.has('justhere'));
}
'''
        program = setup.replace('function jump(){', fold + row + '\nfunction jump(){') + click + check
        result = subprocess.run(['node', '-e', program], text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
