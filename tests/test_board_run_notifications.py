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
var syncPromise=Promise.resolve(false),nextStale=false,SYNC_WAIT_MS=15000;
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
        program += part('  function syncFromServer(back', '  // 平常每 20 秒')
        program += '\n(async()=>{\n' + check + '\nprocess.stdout.write("DONE");\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        result = subprocess.run(['node', '-e', program], text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, 'DONE', '檢查沒有跑到最後(有 promise 一直沒結束):' + result.stderr)

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

    SYNC_STUBS = r"""
APPLY={};globalThis.pullFB=()=>false;globalThis.refreshShip=()=>Promise.resolve(false);globalThis.takeTampered=()=>{};
const ok=data=>Promise.resolve({json:()=>Promise.resolve(data)});
let jobsCalls=0,stateCalls=0,revCalls=0;
"""

    def test_waiting_sync_reports_a_save_in_progress_instead_of_false_success(self):
        for state in ('saving=true;', 'canSave=false;'):
            with self.subTest(state=state):
                self.run_js(state + r"""
await assert.rejects(syncFromServer(false,true), /無法重新讀取最新狀態/);
""")

    def test_failed_jobs_read_after_new_gen_is_retried_next_round(self):
        self.run_js(self.SYNC_STUBS + r"""
fetch=url=>{
  if(url==='/api/rev')return ok({gen:'new',rev:'same'});
  if(url==='/api/jobs'){jobsCalls++;return jobsCalls===1?Promise.reject(new Error('jobs down')):ok({});}
  return ok({});
};
await syncFromServer();await syncFromServer();
assert.equal(jobsCalls,2,'職缺讀取失敗後下一輪要重拿');
""")

    def test_failed_state_read_after_new_rev_is_retried_next_round(self):
        self.run_js(self.SYNC_STUBS + r"""
fetch=url=>{
  if(url==='/api/rev')return ok({gen:'old',rev:'new'});
  if(url==='/api/state'){stateCalls++;return stateCalls===1?Promise.reject(new Error('state down')):ok({});}
  return ok({});
};
await syncFromServer();await syncFromServer();
assert.equal(stateCalls,2,'標記讀取失敗後下一輪要重拿');
""")

    def test_failed_state_read_does_not_refetch_the_jobs_that_already_arrived(self):
        self.run_js(self.SYNC_STUBS + r"""
let nextCalls=0;
fetch=url=>{
  if(url==='/api/rev')return ok({gen:'new',rev:'new'});
  if(url==='/api/jobs')jobsCalls++;
  if(url==='/api/state'){stateCalls++;if(stateCalls===1)return Promise.reject(new Error('state down'));}
  if(url==='/api/next')nextCalls++;
  return ok({});
};
await syncFromServer();await syncFromServer();
assert.equal(jobsCalls,1,'職缺這一包已經拿到,標記失敗不該連它一起重抓');
assert.equal(stateCalls,2);assert.equal(nextCalls,1,'標記沒拿到之前還沒輪到下一步');
""")

    def test_failed_next_read_only_refetches_next(self):
        self.run_js(self.SYNC_STUBS + r"""
let nextCalls=0;
fetch=url=>{
  if(url==='/api/rev')return ok({gen:'new',rev:'new'});
  if(url==='/api/jobs')jobsCalls++;
  if(url==='/api/state')stateCalls++;
  if(url==='/api/next'){nextCalls++;if(nextCalls===1)return Promise.reject(new Error('next down'));}
  return ok({});
};
await syncFromServer();await syncFromServer();
assert.equal(jobsCalls,1);assert.equal(stateCalls,1,'職缺、標記都拿到了,只有下一步失敗只重抓下一步');
assert.equal(nextCalls,2);
""")

    def test_failed_rev_read_does_not_refetch_jobs(self):
        self.run_js(self.SYNC_STUBS + r"""
fetch=url=>{
  if(url==='/api/rev'){revCalls++;return revCalls===1?Promise.reject(new Error('offline')):ok({gen:'old',rev:'same'});}
  if(url==='/api/jobs')jobsCalls++;
  if(url==='/api/state')stateCalls++;
  return ok({});
};
await syncFromServer();await syncFromServer();
assert.equal(revCalls,2);assert.equal(jobsCalls,0,'/api/rev 自己失敗不該多抓 /api/jobs');assert.equal(stateCalls,0);
""")

    def test_waiting_sync_rejects_when_the_read_fails(self):
        self.run_js(self.SYNC_STUBS + r"""
fetch=url=>url==='/api/rev'?ok({gen:'old',rev:'new'}):url==='/api/state'?Promise.reject(new Error('state down')):ok({});
await assert.rejects(syncFromServer(false,true),/state down/);
""")

    def test_waiting_sync_runs_again_after_the_one_in_flight(self):
        self.run_js(self.SYNC_STUBS + r"""
let release;
fetch=url=>{
  if(url==='/api/rev'){revCalls++;return revCalls===1?new Promise(r=>{release=()=>r({json:()=>Promise.resolve({gen:'old',rev:'same'})});}):ok({gen:'old',rev:'after-save'});}
  if(url==='/api/state')stateCalls++;
  return ok({});
};
const running=syncFromServer();await new Promise(setImmediate);
const waited=syncFromServer(false,true);release();
assert.equal(await running,false);assert.equal(await waited,false);
assert.equal(revCalls,2,'等它跑完還要再讀一次');assert.equal(stateCalls,1,'再讀的那次拿到存好之後的標記');
""")

    def test_waiting_sync_gives_up_when_the_one_in_flight_never_finishes(self):
        self.run_js(self.SYNC_STUBS + r"""
SYNC_WAIT_MS=30;
fetch=url=>url==='/api/rev'?new Promise(()=>{}):ok({});
syncFromServer();await new Promise(setImmediate);
await assert.rejects(syncFromServer(false,true),/等太久/);
""")

    def test_waiting_sync_gives_up_when_its_own_read_never_finishes(self):
        self.run_js(self.SYNC_STUBS + r"""
SYNC_WAIT_MS=30;
fetch=url=>url==='/api/rev'?ok({gen:'old',rev:'new'}):url==='/api/state'?new Promise(()=>{}):ok({});
await assert.rejects(syncFromServer(false,true),/等太久/);
""")

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
