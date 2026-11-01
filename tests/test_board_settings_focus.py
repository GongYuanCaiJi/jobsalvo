"""設定欄位在儲存列浮起時仍保留可見焦點。"""
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class SettingsFocusTest(unittest.TestCase):
    def test_first_dirty_change_scrolls_focused_field_above_save_bar(self):
        source = (ROOT / 'board/board.js').read_text()
        start = source.find('  var _cfgViewportObserver=')
        if start < 0:
            start = source.index('  function cfgMark()')
        end = source.index('  function cfgSave(', start)
        setup = r'''
const assert=require('node:assert/strict');
var active='cfg',scrolls=0,dirty=false,vars={};
function rect(top,height,width=300){return {top,bottom:top+height,height,width,left:250,right:250+width};}
var saveRect=rect(1146,52),inputRect=rect(1171,13);
const save={getBoundingClientRect:()=>saveRect,classList:{contains:()=>dirty,toggle:(_k,v)=>dirty=v}};
const input={getBoundingClientRect:()=>inputRect,closest:s=>s==='#app'?{}:null,
  scrollIntoView:options=>{scrolls++;assert.deepEqual(options,{block:'nearest',inline:'nearest'});}};
var document={activeElement:input,documentElement:{style:{setProperty:(k,v)=>vars[k]=v}},
  querySelectorAll:()=>[],querySelector:()=>null,addEventListener(){}};
var window={innerHeight:1208,getComputedStyle:el=>el.style,addEventListener(){}};
function $(id){return id==='cfg-save'?save:null;}
var CFGDIRTY=false,CFG0='before';function cfgSnap(){return 'after';}function refreshBar(){}
'''
        check = r'''
cfgMark();assert.equal(dirty,true);assert.equal(CFGDIRTY,true);
assert.equal(scrolls,1);assert.equal(vars['--cfg-scroll-bottom'],'70px');
'''
        result = subprocess.run(['node', '-e', setup + source[start:end] + check],
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
