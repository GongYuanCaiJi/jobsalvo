"""直接執行正式輪詢與完成通知,不以靜態字串當作按鈕驗收。"""
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class RunNotificationTest(unittest.TestCase):
    def run_js(self, check):
        source = (ROOT / 'board/board.js').read_text()

        def part(start, end):
            return source[source.index(start):source.index(end, source.index(start))]

        setup = r'''
const assert=require('node:assert/strict');
var PREP={},APPLY={running:true},FIND={},REPLIES={},MISC={},_runDone={},_runTimer=null;
var jobs=[{id:'job-test'}],AGENT='Agent',NEXT={},SHIP_EPOCH=0;
var canSave=true,saving=false,syncing=false,saveEpoch=0,srvGen='old',srvRev='same',tampered='',saveErr='';
var notes=[],opened=[],tabs=[],releaseNext,completed;
function nextOf(id){return NEXT[id]||{};}
function snack(msg,undo,next){notes.push({msg,next});}
function liveHref(id){return '/api/live?u='+id;}
function openShot(...args){opened.push(args);}function switchTab(id){tabs.push(id);}
function renderAll(){}function renderFillList(){}function refreshFindUI(){}function refreshApplyUI(){}
function refreshPrepUI(){}function checkUiVersion(){}function refreshBar(){}
function pullJobs(){return false;}
function fetch(url){
  if(url==='/api/next')return new Promise(resolve=>{releaseNext=data=>resolve({json:()=>Promise.resolve(data)});});
  const data=url==='/api/rev'?{gen:'new',rev:'same',apply:completed}:{};
  return Promise.resolve({json:()=>Promise.resolve(data)});
}
'''
        program = setup + part('  function takeRuns(v){', '  // 按之前先把')
        program += part('  function syncFromServer(back)', '  // 平常每 20 秒')
        program += '\n(async()=>{\n' + check + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        result = subprocess.run(['node', '-e', program], text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fill_completion_waits_for_fresh_review_and_opens_the_actual_page_once(self):
        self.run_js(r'''
NEXT={'job-test':{view:{review:false}}};
completed={running:false,phase:'done',stage:'fill',results:[{url:'job-test',ok:true}]};
const poll=syncFromServer();await new Promise(setImmediate);
assert.equal(typeof releaseNext,'function');assert.equal(notes.length,0,'NEXT 尚未回來不能先發完成入口');
releaseNext({'job-test':{view:{review:true}}});await poll;
assert.equal(notes.length,1);assert.equal(notes[0].next.label,'看頁面');
notes[0].next.fn();assert.deepEqual(opened,[['/api/live?u=job-test','job-test']]);assert.deepEqual(tabs,[]);
assert.equal(_runDone.apply,false);runDoneNote();runDoneNote();assert.equal(notes.length,1);
''')

    def test_stale_review_cannot_override_no_review_or_submit_fallback(self):
        for stage, ok, tab in (('fill', False, 'ship'), ('submit', True, 'sent'), ('submit', False, 'ship')):
            with self.subTest(stage=stage, ok=ok):
                self.run_js(r'''
NEXT={'job-test':{view:{review:true}}};
completed={running:false,phase:'done',stage:STAGE,results:[{ok:OK}]};
const poll=syncFromServer();await new Promise(setImmediate);
assert.equal(notes.length,0,'不能先用舊 NEXT 顯示看頁面,隨後再被覆蓋');
releaseNext({'job-test':{view:{review:STAGE==='submit'}}});await poll;
assert.equal(notes.length,1);assert.equal(notes[0].next.label,'去看');
notes[0].next.fn();assert.deepEqual(opened,[]);assert.deepEqual(tabs,[TAB]);
assert.equal(_runDone.apply,false);runDoneNote();assert.equal(notes.length,1);
'''.replace('STAGE', repr(stage)).replace('OK', str(ok).lower()).replace('TAB', repr(tab)))

    def test_fill_completion_counts_only_this_rounds_cards_not_older_ones_awaiting_review(self):
        self.run_js(r'''
jobs=[{id:'job-old'},{id:'job-new'}];
NEXT={'job-old':{view:{review:true}},'job-new':{view:{review:false}}};
completed={running:false,phase:'done',stage:'fill',results:[{url:'job-new',ok:true},{url:'job-fail',ok:false}]};
const poll=syncFromServer();await new Promise(setImmediate);
releaseNext({'job-old':{view:{review:true}},'job-new':{view:{review:true}}});await poll;
assert.equal(notes.length,1);assert.match(notes[0].msg,/填好了 1 張/);assert.equal(notes[0].next.label,'看頁面');
notes[0].next.fn();assert.deepEqual(opened,[['/api/live?u=job-new','job-new']],'看頁面要開這一輪填好的那張,不是之前就在等的舊卡');
''')
