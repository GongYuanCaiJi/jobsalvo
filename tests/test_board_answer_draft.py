"""一次確認答案的未確認文字應保留在本機。"""
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class AnswerDraftPersistenceTest(unittest.TestCase):
    def test_keep_answer_draft_writes_local_storage(self):
        source = (ROOT / 'board/board.js').read_text()
        start = source.index("  var ANSQ=null, ANSWER_DRAFT_KEY='jobsalvo_answer_drafts_v1'")
        helper_end = source.index('  // 這條確認了能放行幾張還沒投出的表單', start)
        render_start = source.index('  function ansQRender(){', helper_end)
        keep_start = source.index('  function ansQKeep(){', helper_end)
        render_end = keep_start
        keep_end = source.index('  function ansQGo(', keep_start)
        setup = r'''
const assert=require('node:assert/strict');
const vm=require('node:vm');
var stored=null,localStorage={getItem:()=>stored,setItem:(_k,v)=>stored=v,removeItem:()=>stored=null};
var entry={k:'answer-1',q:'問題',v:'原答案'},ta={value:'改過但尚未確認',getAttribute:()=> 'answer-1',setSelectionRange(){}};
var capture={html:''};
function context(){return vm.createContext({localStorage,entry,ta,ansOf:k=>k==='answer-1'?entry:null,ansBi:()=>false,
  ANSQ:null,document:{querySelector:()=>ta},ansNeed:()=>false,ansEmpty:()=>false,ansUsers:()=>[],
  esc:x=>String(x||''),escA:x=>String(x||''),ic:()=>'',AGENT:'agent',RL:'中文',FL:'英文',autogrow:()=>{},
  modal:html=>{capture.html=html;},capture});}
'''
        check = r'''
var first=context();
vm.runInContext(sourceHelpers+'ANSQ={};'+sourceKeep+'ansQKeep();',first);
assert.ok(stored,'輸入答案後沒有寫入 localStorage');
var second=context();
vm.runInContext(sourceHelpers+sourceRender+"ANSQ={ks:['answer-1'],i:0,back:false,hist:[]};ansQRender();",second);
assert.equal(JSON.stringify(vm.runInContext('ansQDraftKeys()',second)),'["answer-1"]');
assert.match(capture.html,/>改過但尚未確認<\/textarea>/);
'''
        script = setup + "const sourceHelpers=" + repr(source[start:helper_end]) + ";\n" + \
            "const sourceRender=" + repr(source[render_start:render_end]) + ";\n" + \
            "const sourceKeep=" + repr(source[keep_start:keep_end]) + ";\n" + check
        result = subprocess.run(['node', '-e', script],
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
