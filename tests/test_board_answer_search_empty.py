"""答案搜尋沒有結果時,實際更新可讀的空結果提示。"""
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class AnswerSearchEmptyTest(unittest.TestCase):
    def test_zero_results_explains_query_and_clear_removes_message(self):
        source = (ROOT / 'board/board.js').read_text()
        start = source.index('  function ansApplyFilter(){')
        end = source.index('  // 按鈕的復原:', start)
        setup = r'''
const assert=require('node:assert/strict');
var ansQuery='x <script>',matches=[false,false],rows=[{},{}],groups=[{},{}],done={open:false};
var empty={hidden:true,textContent:''};
const document={querySelectorAll(selector){
  if(selector==='#app .ans-d .ansrow')return rows;
  if(selector==='#app .ans-d .ans-qgrp')return groups;
  throw Error('unexpected selector '+selector);
},querySelector(selector){
  if(selector==='#app .ans-done')return done;
  if(selector==='#app .ans-d .ans-no-results')return empty;
  throw Error('unexpected selector '+selector);
}};
rows.forEach((row,i)=>row.getAttribute=key=>key==='data-txt'?(matches[i]?'match':'other'):null);
groups.forEach((group,i)=>group.querySelector=()=>rows[i].hidden?null:rows[i]);
'''
        check = r'''
ansApplyFilter();
assert.deepEqual(rows.map(row=>row.hidden),[true,true]);
assert.deepEqual(groups.map(group=>group.hidden),[true,true]);
assert.equal(empty.hidden,false);assert.ok(empty.textContent.includes('x <script>'));
ansQuery='';ansApplyFilter();
assert.equal(empty.hidden,true);assert.deepEqual(rows.map(row=>row.hidden),[false,false]);
'''
        result = subprocess.run(['node', '-e', setup + source[start:end] + check],
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
