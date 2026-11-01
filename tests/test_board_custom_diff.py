"""客製文件差異視窗同時呈現原檔預覽與客製檔案。"""
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]

SETUP = r'''
const assert=require('node:assert/strict');
var handler,modalHTML='',modalCount=0,item='resume:general:zh',card='job-1',active='ship',release=[];
var FB={[card]:{custom_docs:{[item]:{candidate_path:'custom/general-zh.pdf'}}}};
var diff={disabled:false,getAttribute:k=>k==='data-cust-url'?card:item};
var rzmodal={style:{display:'none'}};
var document={addEventListener:(name,fn)=>{if(name==='click')handler=fn;},querySelector:()=>null,getElementById:id=>id==='rzmodal'?rzmodal:null};
function modal(html){modalHTML=html;modalCount++;rzmodal.style.display='flex';} function snack(){}
function ic(){return '';}
function esc(x){return String(x||'');}
function escA(x){return String(x||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;');}
var body={ok:true,name:'一般履歷',diff:'客製新增文字'};
function fetch(url){assert.match(url,/^\/api\/customize\/diff\?/);
  return new Promise(resolve=>release.push(()=>resolve({ok:true,json:()=>Promise.resolve(body)})));}
var e={target:{closest:s=>s==='[data-cust-diff]'?diff:null}};
const tick=()=>new Promise(r=>setTimeout(r,0));
'''


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class CustomDiffPreviewTest(unittest.TestCase):
    def run_js(self, check):
        source = (ROOT / 'board/board.js').read_text()
        marker = "    var diff=e.target.closest&&e.target.closest('[data-cust-diff]');"
        pos = source.index(marker)
        start = source.index('  function customAction(')
        end = source.index('\n  // 卡住的頁面', pos)
        end = source.rfind('  });', pos, end) + len('  });')
        program = SETUP + source[start:end] + '\n(async()=>{\n' + check + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        result = subprocess.run(['node', '-e', program], text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_diff_action_shows_source_preview_and_custom_pdf(self):
        self.run_js(r'''
handler(e);release.shift()();await tick();
assert.match(modalHTML,/class="custom-compare-image" src="\/api\/source-preview\?kind=resume/);
assert.match(modalHTML,/class="custom-compare-pdf" src="\/api\/file\?path=custom%2Fgeneral-zh\.pdf"/);
assert.match(modalHTML,/客製新增文字/);
''')

    def test_late_diff_response_does_not_cover_a_screen_the_user_already_left(self):
        for left in ("active='sent';", "modal('<div>別的對話框</div>');"):
            with self.subTest(left=left):
                self.run_js(r'''
handler(e);LEFT
const before=modalCount;release.shift()();await tick();
assert.equal(modalCount,before,'回應晚到時畫面已經換了,不能再彈出差異窗');
'''.replace('LEFT', left))

    def test_each_side_can_be_enlarged_and_returns_to_the_comparison(self):
        self.run_js(r'''
function click(sel,val){return {target:{closest:s=>s===sel?{disabled:false,getAttribute:()=>val}:null}};}
handler(e);release.shift()();await tick();
assert.match(modalHTML,/data-cust-diff-zoom="source"/);assert.match(modalHTML,/data-cust-diff-zoom="custom"/);
handler(click('[data-cust-diff-zoom]','source'));
assert.match(modalHTML,/data-cust-diff-back="source"/,'放大後要有回到對照');
assert.match(modalHTML,/class="custom-compare-full"><img class="custom-compare-image" src="\/api\/source-preview\?kind=resume/);
assert.doesNotMatch(modalHTML,/custom-compare-pdf/,'放大原檔只放原檔');
handler(click('[data-cust-diff-back]','source'));
assert.match(modalHTML,/class="custom-compare-columns"/,'返回後回到兩邊對照');
assert.match(modalHTML,/客製新增文字/);
handler(click('[data-cust-diff-zoom]','custom'));
assert.match(modalHTML,/<iframe class="rzm-pdf" src="\/api\/file\?path=custom%2Fgeneral-zh\.pdf"/);
assert.doesNotMatch(modalHTML,/custom-compare-image/,'放大客製檔只放客製檔');
assert.match(modalHTML,/data-cust-diff-back="custom"/);
''')
