"""離頁存檔與自動存檔倒序抵達時，同一分頁的舊值不可覆蓋新值。"""
import json
import pathlib
import shutil
import subprocess
from unittest import mock

from tests.test_board import HttpBase, bs


ROOT = pathlib.Path(__file__).resolve().parents[1]


def browser_saves(responses=None, revert=False):
    """執行正式 doSave，攔住兩包請求並可倒序送回伺服器回覆。"""
    source = (ROOT / 'board/board.js').read_text()

    def part(start, end):
        return source[source.index(start):source.index(end, source.index(start))]

    setup = r'''
const requests=[];
var FB={'__notes__':[{id:'n1',t:'existing'}]},SAVED=JSON.parse(JSON.stringify(FB));
var dirty={},WANT={},PEND={},EVENTS=[],INFLIGHT=[],REDO_DONE={},NEXT={},jobs=[];
var canSave=false,saving=false,saveEpoch=0,saveErr='',_auto=null,AGENT='Agent';
function $(id){return {disabled:false,textContent:'',style:{},classList:{contains:()=>false}};}
function readonly(){} function refreshBar(){} function refreshAllCards(){} function renderAll(){} function scheduleSave(){}
const snacks=[];function snack(msg){snacks.push(msg);}
function nextOf(){return {};} function clone(v){return v===undefined?undefined:JSON.parse(JSON.stringify(v));}
function norm(k,src){return sig(src[k]);}
function fetch(url,opts){return new Promise((resolve,reject)=>requests.push({body:JSON.parse(opts.body),keepalive:opts.keepalive,resolve,reject}));}
'''
    program = setup + part('  var canSave=false, saving=false;', '  // 存檔前的樣子。') + '\ncanSave=true;\n'
    program += part('  function sig(v){', '  function cardEls')
    program += part('  function unsavedCount(){', '  var _okTimer=')
    program += part('  function flushSave(keepalive){', '  function markDirty')
    program += part('  var KEYED=', '  // 在手上改完一份')
    do_save = part('  function doSave(opts){', '  // 外部改動一律靠這個同步:')
    program += do_save
    program += (r'''
FB.__notes__[0].t='edited';dirty.__notes__=true;doSave();
FB.__notes__[0].t='existing';dirty={};flushSave(true);
''' if revert else r'''
FB.__notes__.push({id:'n2',t:''});dirty.__notes__=true;doSave();
FB.__notes__[1].t='latest text';dirty.__notes__=true;doSave({keepalive:true});
''')
    if responses is None:
        program += 'console.log(JSON.stringify(requests.map(x=>({body:x.body,keepalive:x.keepalive}))));'
    else:
        program += r'''
(async()=>{
for(const index of [1,0]){
  if(RESPONSES[index]===null)requests[index].reject(Error('network'));
  else requests[index].resolve({ok:true,status:200,json:()=>Promise.resolve(RESPONSES[index])});
  await new Promise(resolve=>setImmediate(resolve));
}
console.log(JSON.stringify({fb:FB,saved:SAVED,dirty,saveErr,snacks,requests:requests.map(x=>x.body)}));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''.replace('RESPONSES', json.dumps(responses))
    result = subprocess.run(['node', '-e', program], capture_output=True, text=True,
                            check=True, timeout=10)
    return json.loads(result.stdout)


class SaveOrderTest(HttpBase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(bs, '_SAVE_ORDER', {}, create=True))

    def state(self):
        return json.loads(self.req('/api/state')[1])

    def test_server_rejects_older_packet_for_same_tab_field(self):
        for seq, text in ((2, 'latest text'), (1, '')):
            code, body, _ = self.req('/api/save', {'client_id': 'synthetic-tab', 'save_seq': seq,
                                                   'cmds': [{'u': '__notes__', 'set': {'n2': {'id': 'n2', 't': text}}}]})
            self.assertEqual(code, 200, body)
        notes = {note['id']: note['t'] for note in self.state()['__notes__']}
        self.assertEqual(notes['n2'], 'latest text')

    def test_browser_tags_overlapping_keepalive_saves_with_one_increasing_sequence(self):
        if not shutil.which('node'):
            self.skipTest('需要 Node.js')
        packets = browser_saves()
        self.assertEqual(len(packets), 2)
        first, second = packets
        self.assertTrue(first['body'].get('client_id'), '請求要帶同分頁識別碼')
        self.assertEqual(first['body']['client_id'], second['body']['client_id'])
        self.assertLess(first['body'].get('save_seq', 0), second['body'].get('save_seq', 0))
        self.assertFalse(first['keepalive'])
        self.assertTrue(second['keepalive'])

    def test_older_reply_cannot_regress_browser_saved_state(self):
        if not shutil.which('node'):
            self.skipTest('需要 Node.js')
        self.req('/api/save', {'cmds': [{'u': '__notes__', 'set': {'n1': {'id': 'n1', 't': 'existing'}}}]})
        packets = browser_saves()
        responses = []
        for packet in packets:
            code, body, _ = self.req('/api/save', packet['body'])
            self.assertEqual(code, 200, body)
            responses.append(json.loads(body))
        self.assertLess(int(responses[0]['save_order']), int(responses[1]['save_order']))
        browser = browser_saves(responses=responses)
        self.assertEqual({n['id']: n['t'] for n in browser['fb']['__notes__']}['n2'], 'latest text')
        self.assertEqual({n['id']: n['t'] for n in browser['saved']['__notes__']}['n2'], 'latest text', browser)
        self.assertEqual(browser['dirty'], {})

    def test_pagehide_sends_reverted_text_after_pending_edit(self):
        if not shutil.which('node'):
            self.skipTest('需要 Node.js')
        packets = browser_saves(revert=True)
        self.assertEqual(len(packets), 2)
        self.assertLess(packets[0]['body']['save_seq'], packets[1]['body']['save_seq'])
        self.assertTrue(packets[1]['keepalive'])
        self.assertEqual(packets[1]['body']['cmds'][0]['set']['n1']['t'], 'existing')
        for packet in reversed(packets):
            code, body, _ = self.req('/api/save', packet['body'])
            self.assertEqual(code, 200, body)
        self.assertEqual({note['id']: note['t'] for note in self.state()['__notes__']}['n1'], 'existing')

    def test_older_network_failure_does_not_override_newer_saved_text(self):
        if not shutil.which('node'):
            self.skipTest('需要 Node.js')
        self.req('/api/save', {'cmds': [{'u': '__notes__', 'set': {'n1': {'id': 'n1', 't': 'existing'}}}]})
        packets = browser_saves()
        responses = []
        for packet in packets:
            code, body, _ = self.req('/api/save', packet['body'])
            self.assertEqual(code, 200, body)
            responses.append(json.loads(body))
        browser = browser_saves(responses=[None, responses[1]])
        self.assertEqual({note['id']: note['t'] for note in browser['saved']['__notes__']}['n2'], 'latest text')
        self.assertEqual(browser['saveErr'], '', browser)
        self.assertEqual(browser['dirty'], {})

    def test_own_earlier_packet_is_not_reported_as_another_device_edit(self):
        if not shutil.which('node'):
            self.skipTest('需要 Node.js')
        for other in (None, 'other device'):
            with self.subTest(other=other):
                self.req('/api/save', {'cmds': [{'u': '__notes__', 'set': {'n1': {'id': 'n1', 't': 'existing'}, 'n2': None}}]})
                packets = browser_saves()
                responses = []
                for i, packet in enumerate(packets):
                    if i and other:   # 第 1 包存好之後、第 2 包之前,別處把同一格改掉
                        self.req('/api/save', {'cmds': [{'u': '__notes__', 'set': {'n2': {'id': 'n2', 't': other}}}]})
                    code, body, _ = self.req('/api/save', packet['body'])
                    self.assertEqual(code, 200, body)
                    responses.append(json.loads(body))
                browser = browser_saves(responses=responses)
                clash = [m for m in browser['snacks'] if '別的裝置' in m]
                if other:
                    self.assertEqual(len(clash), 1, '別處真的改了同一格還是要講')
                else:
                    self.assertEqual(clash, [], '之前的值是這個分頁自己較早那包送的,不是別的裝置改的')
