"""封鎖清單的批次移除要等後台回覆,才能關閉覆蓋復原操作的視窗。"""
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class BlockUndoTest(unittest.TestCase):
    def run_js(self, check):
        source = (ROOT / 'board/board.js').read_text()
        start = source.index('  function blockDelete(')
        end = source.index('  // 一次移除的', start)
        setup = r'''
const assert=require('node:assert/strict');
var FB={__block__:['公司甲']},jobs=[{id:'a'},{id:'b'}],listener,resolveBatch,undoVisible=false;
var status={hidden:true,textContent:''},button={disabled:false,getAttribute(){return '公司甲';}};
var ov={style:{display:'none'},cls:'',classList:{contains(c){return ov.cls===c;}},
  querySelector(s){return s==='[data-block-status]'?status:s==='[data-blockdel]'?button:null;}};
function $(id){return id==='rzmodal'?ov:null;}
function modal(_html,cls){ov.style.display='flex';ov.cls=cls||'';button={disabled:false,getAttribute(){return '公司甲';}};status={hidden:true,textContent:''};}
function closeModal(){ov.style.display='none';}
function blockedGroupHTML(){return '<div>公司甲</div>';}
function ic(){return '';}function esc(x){return String(x);}
function coJobs(){return jobs;}function removed(){return false;}function today(){return '2026-10-23';}
function pressAll(_list,_msg){return new Promise(resolve=>{resolveBatch=results=>{if(results.length)undoVisible=true;resolve(results);};});}
const document={addEventListener(_type,fn){listener=fn;},querySelector(s){return s==='#rzmodal [data-block-status]'?status:null;}};
function click(){listener({target:{closest(s){return s==='#rzmodal'?ov:s==='[data-blockdel]'?button:null;}},preventDefault(){}});}
'''
        program = setup + source[start:end] + '\n' + check
        result = subprocess.run(['node', '-e', program], text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_success_closes_only_after_acceptance_and_exposes_undo(self):
        self.run_js(r'''
(async()=>{
openBlockList();click();assert.equal(ov.style.display,'flex');assert.equal(button.disabled,true);
resolveBatch([{u:'a',undo:[{u:'a'}]},{u:'b',undo:[{u:'b'}]}]);await new Promise(setImmediate);
assert.equal(ov.style.display,'none');assert.equal(undoVisible,true);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_rejection_keeps_list_open_and_shows_reason(self):
        self.run_js(r'''
(async()=>{
openBlockList();click();resolveBatch([]);await new Promise(setImmediate);
assert.equal(ov.style.display,'flex');assert.equal(button.disabled,false);
assert.equal(status.hidden,false);assert.ok(status.textContent.includes('後台未接受清除'));
assert.equal(undoVisible,false);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')
