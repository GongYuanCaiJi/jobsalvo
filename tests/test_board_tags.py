"""切換標籤後仍能看到同範圍的其他標籤。"""
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class BoardTagFilterTest(unittest.TestCase):
    def test_selected_tag_does_not_hide_other_tag_candidates(self):
        source = (ROOT / 'board/board.js').read_text()
        start = source.index('  function overviewBase(')
        end = source.index('  function renderApp()', start)
        setup = r'''
const assert=require('node:assert/strict');
var active='all',activeFacet='remote',searchQuery='',TAGS=[{name:'remote'},{name:'python'}];
var justMarked={};
var jobs=[{id:'one',tags:['remote']},{id:'two',tags:['remote']},{id:'three',tags:['python']},{id:'other-tab',tags:['python']}];
function tagsOf(j){return j.tags;}
function placeOf(){return 'in';} function inRange(){return true;} function inOpen(){return true;}
function inCrowd(){return true;} function onTab(id){return id!=='other-tab';}
function _matchQ(){return true;}
'''
        check = r'''
var base=overviewBase(false);
assert.deepEqual(base.map(j=>j.id),['one','two']);
var tagItems=overviewTagItems(base);
assert.deepEqual(tagItems,[['','全部',''],['remote','remote',2],['python','python',1]]);
'''
        result = subprocess.run(['node', '-e', setup + source[start:end] + check],
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
