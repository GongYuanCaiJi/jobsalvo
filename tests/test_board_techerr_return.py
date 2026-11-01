"""沒有先前表態或階段的新職缺,仍能從出錯頁放回。"""
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class TecherrReturnTest(unittest.TestCase):
    def test_neutral_roundtrip_keeps_return_action_after_undo_expires(self):
        source = (ROOT / 'board/board.js').read_text()

        def part(start, end):
            offset = source.index(start)
            return source[offset:source.index(end, offset)]

        setup = r'''
const assert=require('node:assert/strict');
var FB={},_curId='job',active='techerr',catOrder=[],catIcon={},AGENT='agent';
var justMarked={},calls=[],done=[],busy='';
function esc(v){return String(v??'');}var escA=esc;
function moreHTML(inner){return inner;}
function dsBusyWhy(){return busy;}function snack(){}
function rvDone(id){done.push(id);}
function sentOf(id){return (FB[id]||{}).s||'';}function actRow(html){return html;}function ic(){return '';}
function press(id,event,set,message,opts){
  calls.push({id,event,set,message});FB[id]=FB[id]||{};
  for(const key in set){if(set[key]===null)delete FB[id][key];else FB[id][key]=set[key];}
  if(opts&&opts.then)opts.then();
}
const article={getAttribute(name){assert.equal(name,'data-fid');return 'job';}};
function click(selector){handle({target:{closest(sel){return sel===selector?{closest(sel){assert.equal(sel,'article');return article;}}:null;}}});}
function render(){return stageRowHTML(null,{id:'job'});}
'''
        code = setup + 'var FLOW={auto_advance:false};\n'
        code += part('  var STAGE_LABEL=', '  var TABS=')
        code += part('  function stageRowHTML(', '  // 客製只在待你決定')
        code += 'function handle(e){\n' + part("    var er=e.target.closest('[data-err]');", "    var cop=e.target.closest('[data-catopen]');") + '\n}\n'
        check = r'''
FB.job={};click('[data-err]');assert.deepEqual(FB.job,{s:'techerr'});
const html=render();assert.ok(html.includes('data-errback="1"'));
assert.ok(html.includes('放回原處(新職缺)'));
click('[data-errback]');assert.deepEqual(FB.job,{live_ok:1});
assert.equal(calls[1].set.app,null);assert.equal(calls[1].set.s,null);
assert.ok(!render().includes('data-errback'));assert.deepEqual(done,['job','job']);
assert.ok(!calls.some(x=>x.set.app==='prep'));
'''
        result = subprocess.run(['node', '-e', code + check], capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
