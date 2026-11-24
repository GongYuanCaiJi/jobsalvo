#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
看板資料路徑的回歸測試。每一條都是一個真的發生過、而且是他先撞到才發現的 bug。

跑法(repo 根目錄):python3 -m unittest discover -s tests
改到 jobs/tools 或 jobs/board 底下的東西,commit 時 pre-commit 會自己跑。

全部用臨時的小看板(bd.assemble 組出來的),不碰 board-live.html;伺服器在同一個行程裡
起在隨機埠,背景建包與跑準備區都換成假的,不會真的去跑 reconcile 或 agent。
頁面那一半(自動存、送出途中又打字、衝突處理)要真的瀏覽器,在 board_check.py。
"""
import io, os, re, sys, json, gzip, functools, time, shutil, tempfile, threading, unittest, urllib.request, urllib.error, urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import board_doc as bd          # noqa: E402
import board_status             # noqa: E402
import board_server as bs       # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

JOBS = [{'id': 'https://ex.test/job/%d' % i, 'target': 'Job %d' % i} for i in range(1, 4)]


make_board = functools.partial(_env.make_board, jobs=JOBS)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def note_material(prompt, name):
    """整理筆記那隻的 prompt 直接帶材料原文;抓出其中一份。"""
    start, end = f'=== {name}(原文開始)===\n', f'\n=== {name}(原文結束)==='
    m = re.search(re.escape(start) + '(.*?)' + re.escape(end), prompt, re.S)
    return m.group(1) if m else ''


from _env import read_fb  # noqa: E402,F811


def reconcile_process_calls(run_mock):
    return [call for call in run_mock.call_args_list
            if call.args and isinstance(call.args[0], list) and
            any(str(part).endswith('reconcile.py') for part in call.args[0])]


def page_result(url, text='實際 JD 片段', via='fake'):
    import web.fetch
    return web.fetch.PageResult(url, 'ok', text=text, via=via)




def hand(out, texts):
    """準備履歷這一輪交付時存下的:每張卡的 JD 原文編號(handed.json)與當下的可選履歷(resumes.json),存進那一輪的夾。"""
    import prefs
    from numbered import Numbered
    numbered = Numbered()
    for url, text in texts.items():
        numbered.add(text, 'jd', url)
    os.makedirs(out, exist_ok=True)
    for name, value in (('handed.json', numbered.dump()), ('resumes.json', prefs.checked_resumes())):
        with open(os.path.join(out, name), 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False)
    return numbered


def keep_round(out, t0, rows, prompt='prompt'):
    """準備履歷一輪的夾裡 main 存的輪次檔:收尾只讀這些(編號與可選履歷照 hand,沒有 JD)。"""
    hand(out, {})
    for name, value in (('t0', t0), ('rows.json', rows), ('choices.json', {}), ('cached.json', [])):
        with open(os.path.join(out, name), 'w', encoding='utf-8') as f:
            json.dump(value, f)
    with open(os.path.join(out, 'prompt.txt'), 'w', encoding='utf-8') as f:
        f.write(prompt)


def write_fill(out, url, data):
    """一張卡的交件單:<out>/<卡的 jid>/fill.json。"""
    import cut_tailor
    folder = os.path.join(out, cut_tailor.jid(url))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'fill.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory(prefix='boardtest-'))
        self.path = os.path.join(self.dir, 'board.html')
        bs.STATE = self.path
        self.builds = []
        self._tb = bs.trigger_build
        bs.trigger_build = lambda: self.builds.append(1)   # 不要真的跑 reconcile

    def tearDown(self):
        bs.trigger_build = self._tb
        # 存檔排的資料夾版本計時器(folder_history.note_saved)在這條測試裡收掉:還沒響的取消,正在存的等它存完。
        # 以前留著,1 秒後在下一條測試裡跑 git,被那條換掉的 subprocess.run 抓到(#335)
        for t in threading.enumerate():
            if isinstance(t, threading.Timer):
                t.cancel()
                t.join()


class WriteFb(Tmp):
    def test_only_the_fields_sent_are_written(self):
        """以前整包覆蓋:一個擱著沒關的舊分頁一存,別處的新標記全部倒回去。看板只送它改的那幾欄。"""
        make_board(self.path, {JOBS[0]['id']: {'s': 'like'}})
        self.assertEqual(bs.write_fb([{'u': JOBS[1]['id'], 'set': {'s': 'meh'}}]), [])
        fb = read_fb(self.path)
        self.assertEqual(fb[JOBS[0]['id']], {'s': 'like'})
        self.assertEqual(fb[JOBS[1]['id']], {'s': 'meh'})

    def test_a_card_left_empty_is_gone(self):
        make_board(self.path, {JOBS[0]['id']: {'s': 'like'}})
        bs.write_fb([{'u': JOBS[0]['id'], 'set': {'s': None}}])
        self.assertNotIn(JOBS[0]['id'], read_fb(self.path))

    def test_notes_change_one_by_one(self):
        """「我的想法」一則一則照 id 改:這台改一則,別台同時新加的那則留著。"""
        make_board(self.path, {'__notes__': [{'id': 'n1', 't': 'first'}]})
        bs.write_fb([{'u': '__notes__', 'set': {'n2': {'id': 'n2', 't': '別台加的'}}}])
        self.assertEqual(bs.write_fb([{'u': '__notes__', 'set': {'n1': {'id': 'n1', 't': 'second'}}}]), [])
        self.assertEqual(read_fb(self.path)['__notes__'], [{'id': 'n1', 't': 'second'}, {'id': 'n2', 't': '別台加的'}])

    def test_journal_records_only_real_changes(self):
        """流水帳:每次真的改到東西就留一行(改之前、改之後),沒變就不寫。"""
        a = JOBS[0]['id']
        make_board(self.path, {a: {'s': 'like'}})
        bs.write_fb([{'u': a, 'set': {'s': 'meh'}}])
        bs.write_fb([{'u': a, 'set': {'s': 'meh'}}])       # 沒變
        lines = read(bd.journal_path(self.path)).splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec['d'][a], [{'s': 'like'}, {'s': 'meh'}])

    def test_marks_cannot_break_out_of_the_script_block(self):
        a = JOBS[0]['id']
        make_board(self.path, {})
        evil = '</script><script>alert(1)</script>'
        bs.write_fb([{'u': a, 'set': {'s': 'like', 'n': evil}}])
        raw = read(self.path)
        self.assertNotIn(evil, raw)
        self.assertEqual(read_fb(self.path)[a]['n'], evil)


class BuildCompletion(Tmp):
    def test_failed_reconcile_does_not_mark_a_new_verification_complete(self):
        old_live, old_pilot, old_build = bd.LIVE, bs.PILOT, dict(bs._build_state)
        bd.LIVE = self.path
        bs._build_state.update(gen=7, running=False, again=False)
        bs.PILOT = mock.Mock()
        try:
            with mock.patch.object(bs.subprocess, 'run', return_value=mock.Mock(returncode=1)):
                self._tb()
                for _ in range(100):
                    if not bs._build_state['running']: break
                    time.sleep(0.01)
            self.assertFalse(bs._build_state['running'])
            self.assertEqual(bs._build_state['gen'], 7)
            bs.PILOT.kick.assert_not_called()
        finally:
            bd.LIVE, bs.PILOT = old_live, old_pilot
            bs._build_state.clear(); bs._build_state.update(old_build)

    def test_source_check_before_a_run_is_not_blocked_by_one_card_package_problem(self):
        # 跑準備、填表前的來源檢查只管來源檔同步好了沒。來源同步好了、只是某張卡的要寄的檔案建不成(結束碼 4),
        # 不該回「來源檔更新失敗」擋下這一輪(那時也沒有「整理要寄的檔案」回報可看)
        import reconcile
        import source_sync
        self.enterContext(mock.patch.object(bd, 'LIVE', self.path))
        self.enterContext(mock.patch.object(bs, 'STATE', self.path))
        bs.trigger_build = self._tb          # 來源檢查走背景建置那一個入口(tearDown 會換回假的)
        getattr(bs, '_source_fail', {}).clear()
        with mock.patch.object(source_sync, 'stale', return_value=True):
            for code, want in ((reconcile.PACKAGE_PROBLEMS, ''), (1, '來源檔更新失敗')):
                with self.subTest(code=code), \
                        mock.patch.object(bs.subprocess, 'run', return_value=mock.Mock(returncode=code)):
                    got = bs._source_preflight()
                    self.assertTrue(got.startswith(want) if want else got == '', got)

    def test_source_failure_is_not_retried_every_minute(self):
        # 來源檔一直同步失敗(排版要的 Chrome 不見了、原稿壞了):自動流程每分鐘都會走到來源檢查。
        # 同一批來源檔剛失敗過,不再同步重跑一整輪 reconcile,直接回上次的原因;
        # 他改了來源檔就馬上再試;過了一段時間也再試一次(可能是環境修好了)
        import source_sync
        old_live, old_state = bd.LIVE, bs.STATE
        bd.LIVE = bs.STATE = self.path
        bs.trigger_build = self._tb          # 來源檢查走背景建置那一個入口(tearDown 會換回假的)
        getattr(bs, '_source_fail', {}).clear()
        src = os.path.join(os.path.dirname(self.path), 'resume.md')
        with open(src, 'w', encoding='utf-8') as f:
            f.write('# 原稿\n')
        entries = [{'kind': 'resume', 'id': 'r', 'lang': 'zh', 'path': src, 'style_path': None}]
        now = [1000.0]
        try:
            with mock.patch.object(source_sync, 'stale', return_value=True), \
                    mock.patch.object(source_sync, 'files', side_effect=lambda *a, **k: iter(entries)), \
                    mock.patch.object(bs.time, 'time', side_effect=lambda: now[0]), \
                    mock.patch.object(bs.subprocess, 'run', return_value=mock.Mock(returncode=1)) as run:
                first = bs._source_preflight()
                now[0] += 60
                second = bs._source_preflight()
                self.assertEqual(run.call_count, 1, '同一批來源檔一分鐘後又同步重跑了一整輪')
                self.assertTrue(first.startswith('來源檔更新失敗') and second.startswith('來源檔更新失敗'), second)
                with open(src, 'a', encoding='utf-8') as f:
                    f.write('改過了\n')
                os.utime(src, (now[0] + 5, now[0] + 5))
                bs._source_preflight()
                self.assertEqual(run.call_count, 2, '他改了來源檔,沒有馬上再試')
                now[0] += bs.SOURCE_RETRY + 1
                bs._source_preflight()
                self.assertEqual(run.call_count, 3, '過了重試間隔還是不再試')
        finally:
            getattr(bs, '_source_fail', {}).clear()
            bd.LIVE, bs.STATE = old_live, old_state

    def test_one_card_package_problem_still_completes_the_round(self):
        # 只有某幾張卡的要寄的檔案沒建成:這一輪照樣跑完、驗收結果寫進看板(那幾張各自被擋)。
        # 以前一律不算跑完,其他卡的自動推進全部停住,等那一張修好
        import reconcile
        old_live, old_pilot, old_build = bd.LIVE, bs.PILOT, dict(bs._build_state)
        bd.LIVE = self.path
        bs._build_state.update(gen=7, running=False, again=False)
        bs.PILOT = mock.Mock()
        try:
            with mock.patch.object(bs.subprocess, 'run', return_value=mock.Mock(returncode=reconcile.PACKAGE_PROBLEMS)):
                self._tb()
                for _ in range(100):
                    if not bs._build_state['running']: break
                    time.sleep(0.01)
            self.assertFalse(bs._build_state['running'])
            self.assertEqual(bs._build_state['gen'], 8)
            bs.PILOT.kick.assert_called_once()
        finally:
            bd.LIVE, bs.PILOT = old_live, old_pilot
            bs._build_state.clear(); bs._build_state.update(old_build)


class HttpBase(Tmp):
    """起一個真的看板伺服器(臨時看板);只有工具方法,沒有測試,別的檔可以繼承它而不重跑這裡的測試。"""
    def setUp(self):
        super().setUp()
        bs._PV_CACHE.clear()
        self.resume_paste = os.path.join(os.environ['JOBSALVO_HOME'], '.resume-paste.md')
        try:
            with open(self.resume_paste, encoding='utf-8') as f:
                self.resume_paste_before = f.read()
        except FileNotFoundError:
            self.resume_paste_before = None
        make_board(self.path, {JOBS[0]['id']: {'s': 'like'}})
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), bs.H)
        self.base = 'http://127.0.0.1:%d' % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()
        if self.resume_paste_before is None:
            if os.path.isfile(self.resume_paste):
                os.remove(self.resume_paste)
        else:
            with open(self.resume_paste, 'w', encoding='utf-8') as f:
                f.write(self.resume_paste_before)
        super().tearDown()

    def req(self, path, body=None, ua='Mozilla/5.0 (iPhone)', headers=None, method=None, data=None):
        """body 給 dict 就送 JSON;data 是原樣送的位元組。回 (狀態碼, 內容, 標頭)。"""
        h = {'User-Agent': ua}
        h.update(headers or {})
        if body is not None:
            data = json.dumps(body).encode('utf-8'); h['Content-Type'] = 'application/json'
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read(), dict(e.headers)
        except TimeoutError:
            import faulthandler   # 伺服器跟測試同一個程序:逾時當下每條執行緒卡在哪一行,印出來才查得到
            faulthandler.dump_traceback(all_threads=True)
            raise

    def rev(self):
        return json.loads(self.req('/api/rev')[1])


class ForeignSites(HttpBase):
    """別的網站不能冒用看板:你開著看板時逛到的網站,可以對 127.0.0.1 送 POST(不用先問伺服器),
    或用 DNS rebinding 把自己的網域指到 127.0.0.1 讀資料。看板會派有完整權限的 agent,這條一定要擋。"""

    def test_cross_site_writes_and_rebinding_are_refused(self):
        port = self.httpd.server_address[1]
        own = {'Origin': 'http://127.0.0.1:%d' % port, 'Sec-Fetch-Site': 'same-origin'}
        # 惡意網站送出的:Origin 是它自己、瀏覽器標 cross-site
        evil = {'Origin': 'https://evil.example', 'Sec-Fetch-Site': 'cross-site'}
        self.assertEqual(self.req('/api/run/add', {'text': 'https://evil.example/job'}, headers=evil)[0], 403)
        self.assertEqual(self.req('/api/save', {'fb': {}}, headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        # DNS rebinding:網址是對方的網域(Host 帶它),讀也不行
        self.assertEqual(self.req('/api/state', headers={'Host': 'evil.example:%d' % port})[0], 403)
        # 看板自己的頁面、本機程式(不帶 Origin)照常
        self.assertEqual(self.req('/api/state', headers=own)[0], 200)
        self.assertEqual(self.req('/api/rev')[0], 200)
        self.assertNotEqual(self.req('/api/run/add', {'text': 'no url'}, headers=own)[0], 403)
        self.assertEqual(self.req('/api/state', headers={'Host': 'localhost:%d' % port})[0], 200)


class Http(HttpBase):

    def test_page_uses_the_current_board_code_not_the_copy_inside_the_board_file(self):
        # 看板檔裡存的頁面程式是上次重建時的;更新之後以前畫面一直是舊版,要等下一次重建
        import apply_shell as ash
        with open(self.path, encoding='utf-8') as f:
            p = bd.parse(f.read())
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(bd.assemble(p['sty'], p['thdr'], p['tail'], p['data'], p['fb'], '/*舊的頁面程式*/'))
        code, raw, _ = self.req('/')
        page = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        self.assertEqual(code, 200)
        self.assertNotIn('/*舊的頁面程式*/', page)
        self.assertIn(ash.read(ash.JS)[:200], page)
        served = bd.parse(page)
        self.assertEqual([j['id'] for j in served['data']['jobs']], [j['id'] for j in p['data']['jobs']])
        with open(self.path, encoding='utf-8') as f:
            self.assertIn('/*舊的頁面程式*/', f.read())                  # 看板檔本身不動,留給重建去換

    def test_title_and_agent_name_are_not_settings(self):
        # 看板標題、agent 的名字不給改
        import config as cf
        self.assertIn('<title>jobsalvo</title>', self.req('/')[1].decode('utf-8'))
        self.assertEqual(cf.AGENT, 'Agent')

    def test_opening_board_checks_for_source_changes(self):
        with mock.patch.object(bs, 'trigger_source_sync') as sync:
            code, _, _ = self.req('/')
        self.assertEqual(code, 200)
        sync.assert_called_once_with()

    def test_coming_back_to_the_tab_checks_for_source_changes_but_polling_does_not(self):
        with mock.patch.object(bs, 'trigger_source_sync') as sync:
            self.assertEqual(self.req('/api/rev')[0], 200)
            sync.assert_not_called()
            self.assertEqual(self.req('/api/rev?sync=1')[0], 200)
            sync.assert_called_once_with()

    def test_missing_live_board_paths_skip_source_sync(self):
        with mock.patch.object(bs, 'STATE', None), mock.patch.object(bd, 'LIVE', None), \
                mock.patch('source_sync.stale', return_value=True), mock.patch.object(bs, 'trigger_build') as build:
            bs.trigger_source_sync()
        build.assert_not_called()

    def test_live_board_open_rebuilds_only_when_source_inputs_are_stale(self):
        self.enterContext(mock.patch.object(bd, 'LIVE', self.path))
        with mock.patch('source_sync.stale', return_value=False), \
                mock.patch.object(bs, 'trigger_build') as build:
            bs.trigger_source_sync()
            build.assert_not_called()
        with mock.patch('source_sync.stale', return_value=True), \
                mock.patch.object(bs, 'trigger_build') as build:
            bs.trigger_source_sync()
            build.assert_called_once_with()

    def test_preflight_waits_for_the_background_build_instead_of_running_a_second_one(self):
        old_live, old_state = bd.LIVE, bs.STATE
        bd.LIVE = bs.STATE = self.path
        bs._build_state['running'] = True
        seen = []

        def done():
            time.sleep(1.2)
            seen.append('built')
            with bs._BUILD_LOCK:
                bs._build_state['running'] = False
        t = threading.Thread(target=done)
        try:
            with mock.patch('source_sync.stale', side_effect=lambda *a, **k: seen.append('stale') or False), \
                    mock.patch.object(bs.subprocess, 'run') as run:
                t.start()
                self.assertEqual(bs._source_preflight(), '')
            run.assert_not_called()
            self.assertEqual(seen, ['built', 'stale'])      # 等背景建完才比,比完是新的就不再建
        finally:
            t.join()
            bs._build_state['running'] = False
            bd.LIVE, bs.STATE = old_live, old_state

    def test_prep_refuses_to_start_when_source_preflight_fails(self):
        message = '來源檔更新失敗'
        with mock.patch.object(bd, 'LIVE', self.path), \
                mock.patch.object(bs, '_source_preflight', return_value=message) as preflight:
            code, result = bs.start_run('prep', {'mode': 'deep'})
        self.assertEqual(code, 409)
        self.assertEqual(result, {'msg': message})
        preflight.assert_called_once_with()

    def test_confirm_timer_dispatches_the_same_approval_id(self):
        url, approval_id = JOBS[0]['id'], 'confirm-second'
        fb = {url: {'ds': 'confirmed', 'approve': {'at': 'same-second', 'id': approval_id}}}
        with mock.patch.object(bs, 'snapshot', return_value={'fb': fb}), \
                mock.patch.object(bs, 'start_run', return_value=(200, {})) as start:
            bs._send_confirmed(url, 'same-second', approval_id)
        start.assert_called_once_with('apply', {'stage': 'submit', 'url': url}, approval_id=approval_id)

    def test_old_timer_cannot_clear_or_dispatch_over_a_new_confirmation(self):
        url = JOBS[0]['id']
        fb = {url: {'ds': 'confirmed', 'approve': {'at': 'same-second', 'id': 'new'}}}
        bs.QUEUED[url] = (time.time() + 8, 'new')
        try:
            with mock.patch.object(bs, 'snapshot', return_value={'fb': fb}), \
                    mock.patch.object(bs, 'start_run') as start:
                bs._send_confirmed(url, 'same-second', 'old')
            start.assert_not_called()
            self.assertEqual(bs.QUEUED[url][1], 'new')
        finally:
            bs.QUEUED.pop(url, None)

    def test_manual_submit_dispatch_pins_approval_ids_for_one_or_all_cards(self):
        import form_record as fr
        cards = {JOBS[0]['id']: {'app': 'ship', 'approve': {'id': 'confirm-one'}},
                 JOBS[1]['id']: {'app': 'ship', 'approve': {'id': 'confirm-two'}}}
        snap = {'fb': cards, 'parts': {'data': {'status': {}}}}
        with tempfile.TemporaryDirectory(prefix='manual-submit-') as tmp:
            def dispatch(args):
                with mock.patch.object(bs, '_source_preflight', return_value=''), \
                        mock.patch.object(bs, 'is_real', return_value=True), \
                        mock.patch.object(bs, 'snapshot', return_value=snap), \
                        mock.patch.object(fr, 'approval_problem', return_value=None), \
                        mock.patch('chrome_door.configured', return_value=True), \
                        mock.patch.object(bs, 'run_status', return_value={'running': False}), \
                        mock.patch.object(bs, 'run_sp', return_value=tmp), \
                        mock.patch.object(bs.subprocess, 'Popen', return_value=mock.Mock(pid=123)) as launch, \
                        mock.patch.object(bs.jobrun, 'write'), \
                        mock.patch.object(bs, 'PILOT', None):
                    code, _ = bs.start_run('apply', args)
                self.assertEqual(code, 200)
                argv = launch.call_args.args[0]
                return json.loads(argv[argv.index('--approval-ids') + 1])

            self.assertEqual(dispatch({'stage': 'submit', 'url': JOBS[0]['id']}),
                             {JOBS[0]['id']: 'confirm-one'})
            self.assertEqual(dispatch({'stage': 'submit'}),
                             {JOBS[0]['id']: 'confirm-one', JOBS[1]['id']: 'confirm-two'})

    def test_old_handled_report_does_not_start_an_unrequested_fix(self):
        url = JOBS[1]['id']
        fb = {url: {'app': 'ship', 'apply': {'session': 's1'}}, '__inbox__': [
            {'id': 'r1', 'job': url, 'approve': [{'item': 'field:0'}], 'msg': 'x'}]}
        doc = {'fb': json.dumps(fb), 'data': {}}
        def ask():
            with mock.patch.object(bs, 'read_doc', return_value=''), mock.patch.object(bd, 'parse', return_value=doc), \
                    mock.patch('delivery_state.allowed', return_value=True), \
                    mock.patch.object(bs, 'run_status', return_value={'running': True}):
                return bs.start_run('apply', {'stage': 'fix', 'url': url})
        self.assertEqual(ask()[1].get('msg'), '寫一下要 agent 改什麼')
        fb['__inbox__'][0]['done'] = '2026-10-06'
        doc['fb'] = json.dumps(fb)
        self.assertEqual(ask()[1].get('msg'), '寫一下要 agent 改什麼')

    def test_reply_check_of_one_card_accepts_an_unsure_send(self):
        # 自動流程送出滿一小時查這一張(#389):這張還在「可以投了」,不能被「不在已投出」擋掉
        url = JOBS[1]['id']
        fb = {url: {'app': 'ship', 'ds': 'unsure', 'apply': {'submit_fail': {'at': '2026-01-05T08:00:00'}}},
              JOBS[2]['id']: {'app': 'ship'}}
        doc = {'fb': json.dumps(fb), 'data': {}}
        with mock.patch.object(bs, 'read_doc', return_value=''), mock.patch.object(bd, 'parse', return_value=doc), \
                mock.patch.object(bs, 'run_status', return_value={'running': True}):
            self.assertNotEqual(bs.start_run('replies', {'url': url})[0], 400)
            self.assertEqual(bs.start_run('replies', {'url': JOBS[2]['id']})[0], 400)

    def test_old_tab_sending_whole_cards_is_refused(self):
        before = read(self.path)
        self.assertEqual(self.req('/api/save', {'__rev__': 1, JOBS[1]['id']: {'s': 'like'}})[0], 409)
        self.assertEqual(read(self.path), before)

    def test_the_board_sends_events_not_the_delivery_state(self):
        """同時存檔(狀態表修正 9):看板送來的卡上投遞狀態那幾欄不算數,只看它送的事件,照磁碟上現在的狀態套表;
        那一格不准的不做事、回原因。以前看板整張卡寫回,自動流程剛寫的「正在送出」被舊分頁蓋回「你已確認」。"""
        import delivery_state as ds
        a, b = JOBS[0]['id'], JOBS[1]['id']
        filled = {'app': 'ship', 'form': {'plat': 'x', 'f': []},
                  'apply': {'stage': 'fill', 'tab_id': '7', 'delivery': {'method': 'direct_upload'}}}
        make_board(self.path, {a: dict(filled, ds='sending', approve={'snap': {}}), b: dict(filled, ds='parked')},
                   data={'jobs': JOBS, 'status': {'schema_version': board_status.SCHEMA_VERSION, 'issues': []}})   # 驗收跑完、沒擋:能確認
        code, raw, _ = self.req('/api/save', {'cmds': [
            {'u': a, 'set': {'n': '我的筆記', 'ds': 'confirmed', 'approve': {'snap': {}}}},   # 舊分頁想寫的狀態欄不算數
            {'u': a, 'ev': 'unconfirm'},                                    # 8 秒計時器已經開始送:取消確認撞上
            {'u': b, 'ev': 'confirm', 'data': {'approve': {'snap': {}, 'round': None}}}]})
        self.assertEqual(code, 200, raw)
        fb = read_fb(self.path)
        self.assertEqual((ds.state(fb[a]), fb[a]['n']), ('sending', '我的筆記'))   # 狀態照磁碟上的,筆記照收
        self.assertEqual(ds.state(fb[b]), 'confirmed')
        self.assertEqual([r['u'] for r in json.loads(raw)['rejected']], [a])
        self.assertIn('正在送出', json.loads(raw)['rejected'][0]['msg'])

    def save(self, *cmds):
        code, raw, _ = self.req('/api/save', {'cmds': list(cmds)})
        self.assertEqual(code, 200, raw)
        return json.loads(raw)

    def test_undo_only_puts_back_what_nothing_else_moved(self):
        import delivery_state as ds
        a = JOBS[0]['id']
        make_board(self.path, {a: {'app': 'ship', 'ds': 'parked', 'form': {'plat': 'x', 'f': []},
                                   'apply': {'stage': 'fill', 'tab_id': '7', 'delivery': {'method': 'direct_upload'}}}},
                   data={'jobs': JOBS, 'status': {'schema_version': board_status.SCHEMA_VERSION, 'issues': []}})
        got = self.save({'u': a, 'ev': 'confirm'})
        self.assertEqual(ds.state(read_fb(self.path)[a]), 'confirmed')
        self.assertEqual(self.save(*got['undo'][0])['rejected'], [])
        self.assertEqual(ds.state(read_fb(self.path)[a]), 'parked')
        # 復原之前自動流程已經把它改了(這裡換成換了檔):狀態那幾欄不放回
        got = self.save({'u': a, 'ev': 'confirm'})
        bs.bd.set_fb(lambda f: ds.fire(f, a, 'files_changed', why='x'), live=self.path)
        self.assertEqual(self.save(*got['undo'][0])['rejected'][0]['u'], a)
        self.assertEqual(ds.state(read_fb(self.path)[a]), 'stale')
        # 換了履歷、agent 接著開始填:按「復原」換回履歷也算換檔,正在填不准換檔(#341),原因照卡上那一句
        got = self.save({'u': a, 'set': {'resume_id': 'tech'}})
        bs.bd.set_fb(lambda f: ds.fire(f, a, 'fill_start', apply={'stage': 'fill', 'at': 'x', 'issues': []}), live=self.path)
        back = self.save(*got['undo'][0])
        self.assertEqual((back['rejected'][0]['msg'], read_fb(self.path)[a]['resume_id']), ('Agent 正在做,等它做完', 'tech'))

    def test_undo_actually_sent_gives_back_the_card_with_what_the_server_noted(self):
        """送出結果不明 → 其實送出了 → 復原:後台套完事件補記的寄出版本(sent_v)也在復原單上,一起拿掉
        (以前看板照狀態表算的 after 沒有它,就當成「被別處改過了」不准復原,卡停在已投出)。"""
        import config as cf
        a = JOBS[0]['id']
        make_board(self.path, {a: {
            'app': 'ship', 'ds': 'unsure', 'resume_id': next(iter(cf.RESUMES)), 'form': {'plat': 'x', 'f': []},
            'apply': {'stage': 'fill', 'tab_id': '', 'submit_fail': {'problems': ['沒看到成功頁面'], 'clicked': True}},
            'approve': {'snap': {}}}})
        before = read_fb(self.path)[a]
        data = {'by': 'agent', 'at': '2026-10-01T00:00:00Z', 'sent_at': '2026-10-01', 'evidence': {'you_sent': 'x'}}
        got = self.save({'u': a, 'ev': 'actually_sent', 'data': data})
        self.assertTrue(read_fb(self.path)[a].get('sent_v'))
        self.assertEqual(self.save(*got['undo'][0])['rejected'], [])
        self.assertEqual(read_fb(self.path)[a], before)

    def test_undo_of_retry_gives_back_the_locked_form(self):
        """再投一次把整份表單(鎖著的)收進 tries;復原要把表單連鎖一起放回
        (#303 每顆按鈕按一遍抓到:以前表單回來了、鎖沒回來,已投出的表單變成可以改)。"""
        a = JOBS[0]['id']
        card = {'app': 'sent', 'ds': 'sent', 'sent_by': 'manual', 'sent_at': '2026-01-02', 'oc': 'rej',
                'oc_at': {'rej': '2026-01-05'}, 'form': {'plat': 'x', 'f': [{'q': 'Q', 'src': 'bank', 'k': 'k1'}], 'lock': 1}}
        make_board(self.path, {a: card})
        before = read_fb(self.path)[a]
        got = self.save({'u': a, 'ev': 'retry'})
        self.assertNotIn('form', read_fb(self.path)[a])
        self.assertEqual(self.save(*got['undo'][0])['rejected'], [])
        self.assertEqual(read_fb(self.path)[a], before)

    def test_undo_of_retry_preserves_missing_and_existing_sent_snapshots(self):
        """舊寄件紀錄未知就維持未知,復原不能拿目前的履歷和附件補成歷史。"""
        import config as cf, ship
        a = JOBS[0]['id']
        rid = next(iter(cf.RESUMES))
        attachments = [{'id': 'current-%d' % i, 'files': {'en': 'current.pdf'}} for i in range(3)]
        with mock.patch.object(ship, 'read_info', return_value={'variant': rid, 'lang': 'en'}), \
                mock.patch.object(ship, 'attachments_for', return_value=attachments):
            for snapshot in ({}, {'sent_v': 'zh-old', 'sent_att': ['historical-file']}):
                with self.subTest(snapshot=snapshot):
                    card = dict({'app': 'sent', 'ds': 'sent', 'resume_id': rid, 'lang': 'en',
                                 'sent_by': 'manual', 'sent_at': '2026-01-02', 'oc': 'rej',
                                 'replies': {'items': [{'kind': 'confirm'}]},
                                 'form': {'plat': 'x', 'f': [], 'lock': 1}}, **snapshot)
                    bd.rewrite(lambda d: d.update(fb={a: card}), self.path, by='測試')
                    before = read_fb(self.path)[a]
                    got = self.save({'u': a, 'ev': 'retry'})
                    self.assertEqual(got['rejected'], [])
                    self.assertTrue(read_fb(self.path)[a]['tries'])
                    back = self.save(*got['undo'][0])
                    self.assertEqual(back['rejected'], [])
                    self.assertEqual(back['cards'][a], before)
                    self.assertEqual(read_fb(self.path)[a], before)

    def test_real_sent_events_still_record_the_current_package(self):
        import config as cf, ship
        a = JOBS[0]['id']
        rid = next(iter(cf.RESUMES))
        attachments = [{'id': 'current-file', 'files': {'en': 'current.pdf'}}]
        with mock.patch.object(ship, 'read_info', return_value={'variant': rid, 'lang': 'en'}), \
                mock.patch.object(ship, 'attachments_for', return_value=attachments):
            for event, state in (('sent_manual', 'todo'), ('actually_sent', 'unsure'), ('resent', 'sent')):
                with self.subTest(event=event):
                    card = {'app': 'ship', 'ds': state, 'resume_id': rid, 'lang': 'en'}
                    if event == 'resent':
                        card.update(app='sent', sent_v='zh-old', sent_att=['historical-file'])
                    bd.rewrite(lambda d: d.update(fb={a: card}), self.path, by='測試')
                    got = self.save({'u': a, 'ev': event, 'data': {'sent_at': '2026-01-03'}})
                    self.assertEqual(got['rejected'], [])
                    saved = read_fb(self.path)[a]
                    self.assertEqual((saved['ds'], saved['sent_v'], saved['sent_att']),
                                     ('sent', 'en-' + rid, ['current-file']))
                    if event == 'resent':
                        self.assertEqual((saved['history'][-1]['sent_v'], saved['history'][-1]['sent_att']),
                                         ('zh-old', ['historical-file']))

    def test_moving_to_ready_builds_the_pack(self):
        self.req('/api/save', {'cmds': [{'u': JOBS[1]['id'], 'set': {'app': 'ready'}}]})
        self.assertEqual(self.builds, [1])

    def test_gzip(self):
        make_board(self.path, {JOBS[0]['id']: {'s': 'like', 'n': '長' * 3000}})   # 小於 1KB 不壓
        code, raw, h = self.req('/', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(h.get('Content-Encoding'), 'gzip')
        plain = self.req('/')[1]
        self.assertEqual(gzip.decompress(raw), plain)

    def test_resume_previews_are_fetched_on_click_not_with_the_page(self):
        """履歷預覽全文(variants 的 html/pages)不跟頁面、不跟 /api/jobs 一起送,留記號;/api/resume 才給全文。
        檔案本身不動(直接開檔的人照樣看得到)。"""
        big = '<p>' + '履' * 5000 + '</p>'
        jobs = [dict(JOBS[0], resume={'recommend': 'general', 'variants': {
                    'zh-general': {'html': big, 'motive': {'txt': '動機'}, 'attachments': [{'slug': 'a'}]},
                    'en-general': {'pages': ['p1.png', 'p2.png']}}}),
                JOBS[1]]
        make_board(self.path, {}, jobs=jobs)
        page = self.req('/')[1].decode('utf-8')
        self.assertNotIn('履' * 100, page)
        served = bd.parse(page)['data']['jobs'][0]['resume']['variants']
        self.assertEqual(served['zh-general'].get('html_lazy'), 1)
        self.assertEqual(served['zh-general']['motive'], {'txt': '動機'})      # 卡上直接顯示的留著
        self.assertEqual(served['zh-general']['attachments'], [{'slug': 'a'}])
        self.assertEqual(served['en-general'].get('pages_n'), 2)
        self.assertNotIn('pages', served['en-general'])
        polled = json.loads(self.req('/api/jobs')[1])['jobs'][0]['resume']['variants']
        self.assertNotIn('html', polled['zh-general'])
        path = '/api/resume?u=' + urllib.parse.quote(JOBS[0]['id'], safe='')
        zh = json.loads(self.req(path + '&v=zh-general')[1])
        self.assertEqual(zh, {'html': big})
        self.assertNotIn('en-general', zh)
        en = json.loads(self.req(path + '&v=en-general')[1])
        self.assertEqual(en, {'pages': ['p1.png', 'p2.png']})
        self.assertEqual(self.req(path + '&v=missing')[0], 404)
        self.assertEqual(self.req('/api/resume?u=https%3A%2F%2Fex.test%2Fnope&v=zh-general')[0], 404)
        self.assertIn('履' * 100, read(self.path))                             # 檔案沒被改

    def test_malformed_other_card_does_not_put_preview_back_on_page(self):
        big = '<p>' + 'FAKE-PREVIEW-' * 1000 + '</p>'
        jobs = [dict(JOBS[0], resume={'variants': {'en-general': {'html': big}}}),
                dict(JOBS[1], resume='malformed old value')]
        make_board(self.path, {}, jobs=jobs)
        page = self.req('/')[1].decode('utf-8')
        self.assertFalse('FAKE-PREVIEW-' * 100 in page, '格式不完整的卡讓首頁退回原始預覽全文')
        polled = json.loads(self.req('/api/jobs')[1])['jobs']
        self.assertNotIn('html', polled[0]['resume']['variants']['en-general'])
        self.assertEqual(polled[1]['resume'], 'malformed old value')

    def test_top_level_resume_preview_is_lazy_too(self):
        big = '<p>' + 'TOP-FAKE-' * 400 + '</p>'
        jobs = [dict(JOBS[0], resume={'recommend': 'general', 'lang': 'en',
                                     'html': big, 'pages': ['fake-page.png']})]
        make_board(self.path, {}, jobs=jobs)
        page = self.req('/')[1].decode('utf-8')
        self.assertFalse('TOP-FAKE-' * 100 in page, '首頁仍送出舊形狀的履歷全文')
        served = bd.parse(page)['data']['jobs'][0]['resume']
        self.assertNotIn('html', served)
        self.assertNotIn('pages', served)
        self.assertEqual(served['html_lazy'], 1)
        self.assertEqual(served['pages_n'], 1)
        polled = json.loads(self.req('/api/jobs')[1])['jobs'][0]['resume']
        self.assertNotIn('html', polled)
        self.assertNotIn('pages', polled)
        full = json.loads(self.req('/api/resume?u=' + urllib.parse.quote(JOBS[0]['id'], safe='') + '&v=__top__')[1])
        self.assertEqual(full, {'html': big, 'pages': ['fake-page.png']})
        self.assertIn('TOP-FAKE-' * 100, read(self.path))

    def test_rev_moves_when_he_saves(self):
        r1 = self.rev()
        self.req('/api/save', {'cmds': [{'u': JOBS[1]['id'], 'set': {'s': 'meh'}}]})
        r2 = self.rev()
        self.assertNotEqual(r1['rev'], r2['rev'])
        self.assertEqual(r1['gen'], r2['gen'])      # 只動標記,不必重拿 1MB 的職缺資料

    def test_rev_moves_when_a_script_writes_the_file(self):
        """cut_tailor 收尾、custom_queue、進度紀錄對帳都直接寫檔,不經過這支伺服器。
        以前這種改動頁面永遠看不到,要他自己重整。"""
        r1 = self.rev()
        time.sleep(0.01)
        bd.set_fb(lambda fb: fb.__setitem__(JOBS[2]['id'], {'app': 'ready'}), live=self.path)
        r2 = self.rev()
        self.assertNotEqual(r1['rev'], r2['rev'])
        self.assertEqual(r1['gen'], r2['gen'])

    def test_gen_moves_when_jobs_data_changes(self):
        r1 = self.rev()
        time.sleep(0.01)
        bd.set_data(lambda data, fb: data['jobs'].append({'id': 'https://ex.test/job/9', 'target': 'Job 9'}), live=self.path)
        r2 = self.rev()
        self.assertNotEqual(r1['gen'], r2['gen'])
        self.assertEqual(r1['rev'], r2['rev'])

class ShipFilesApi(HttpBase):
    """看板不自己挑履歷:載入時拿到每張卡的要寄的檔案(後台 ship.card_files 算的),
    按了版本/語言就當下問後台這一張(不經存檔、不整個重建)。"""

    def setUp(self):
        super().setUp()
        import config as cf
        self.cf = cf
        src = os.path.join(self.dir, 'general-zh.pdf')
        with open(src, 'wb') as f:
            f.write(b'%PDF resume')
        resumes = {'general': {'id': 'general', 'name': '通用版', 'files': {'zh': src, 'en': src}, 'enabled': True},
                   'tech': {'id': 'tech', 'name': '技術版', 'files': {'zh': src}, 'enabled': True}}
        attachments = [{'id': 'letter', 'name': '求職信', 'files': {'en': src}, 'enabled': True}]
        for name, value in (('RESUMES', resumes), ('ATTACHMENTS', attachments), ('LANGS', ['zh', 'en'])):
            patcher = mock.patch.object(cf, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.jobs = [dict(JOBS[0], resume={'recommend': 'general', 'lang': 'zh'}), JOBS[1],
                     dict(JOBS[2], resume='malformed old value')]
        make_board(self.path, {JOBS[0]['id']: {'app': 'ship'}, JOBS[1]['id']: {'app': 'ready'},
                               JOBS[2]['id']: {'app': 'ready', 'custom_docs': 'broken'}}, jobs=self.jobs)

    def test_page_and_polling_carry_each_cards_files_from_the_backend(self):
        page = bd.parse(self.req('/')[1].decode('utf-8'))['data']
        files = page['ship_files']
        self.assertEqual((files[JOBS[0]['id']]['resume_id'], files[JOBS[0]['id']]['lang']), ('general', 'zh'))
        self.assertEqual(files[JOBS[1]['id']]['problem'], '還沒挑履歷')
        self.assertIn(JOBS[2]['id'], files)                      # 壞掉的卡不拖垮整頁
        self.assertIn('cfg', page)
        polled = json.loads(self.req('/api/jobs')[1])
        self.assertEqual(polled['ship_files'][JOBS[0]['id']], files[JOBS[0]['id']])

    def test_asking_for_one_card_with_the_buttons_just_pressed_does_not_save(self):
        before = read(self.path)
        u = urllib.parse.quote(JOBS[0]['id'], safe='')
        code, raw, _ = self.req(f'/api/ship-files?u={u}&resume_id=general&lang=en')
        self.assertEqual(code, 200)
        got = json.loads(raw)
        self.assertEqual((got['resume_id'], got['lang']), ('general', 'en'))
        self.assertEqual([f['name'] for f in got['files']], ['通用版', '求職信'])
        got = json.loads(self.req(f'/api/ship-files?u={u}&resume_id=tech')[1])
        self.assertEqual((got['resume_id'], got['lang']), ('tech', 'zh'))
        got = json.loads(self.req(f'/api/ship-files?u={u}&resume_id=&lang=')[1])     # 清掉 = 回到 agent 挑的
        self.assertEqual((got['resume_id'], got['lang']), ('general', 'zh'))
        self.assertEqual(read(self.path), before)
        self.assertEqual(self.req('/api/ship-files?u=https%3A%2F%2Fex.test%2Fnope')[0], 404)
        every = json.loads(self.req('/api/ship-files')[1])
        self.assertEqual(sorted(every), sorted(j['id'] for j in JOBS))

    def test_marking_sent_on_the_board_records_what_is_sent(self):
        # 看板不再自己寫 sent_v:存檔時伺服器照要寄的檔案(沒建過就照現在挑的)記一次
        # 標已投出是「我已在外部送出」事件(投遞狀態只靠事件改,#302)
        a = JOBS[0]['id']
        sent = [{'u': a, 'ev': 'sent_manual', 'data': {'by': 'manual', 'sent_at': '2026-01-01'}}]
        self.assertEqual(self.req('/api/save', {'cmds': [{'u': a, 'set': {'app': 'ship', 'lang': 'en'}}]})[0], 200)
        self.assertEqual(self.req('/api/save', {'cmds': sent})[0], 200)
        self.assertEqual(read_fb(self.path)[a]['sent_v'], 'en-general')
        # 之後又改了語言:記過的那份留著
        self.req('/api/save', {'cmds': [{'u': a, 'set': {'app': 'sent', 'lang': 'zh'}}]})
        self.assertEqual(read_fb(self.path)[a]['sent_v'], 'en-general')


class RunButton(HttpBase):
    """看板任務按鈕的共用副本啟動與進度輪詢。"""

    def setUp(self):
        super().setUp()
        os.environ['JOB_FAKE_STEP'] = '0.1'
        os.environ.pop('JOB_FAKE_DIE', None)
        self.assertEqual(os.path.basename(bs.run_argv(self.KIND, {'mode': 'deep'})[1]), 'job_fake.py')

    def tearDown(self):
        for k, p in list(bs._PROC.items()):
            if p is not None and p.poll() is None:
                p.kill(); p.wait()
            bs._PROC[k] = None
        os.environ.pop('JOB_FAKE_DIE', None)
        super().tearDown()

    def wait_run(self, until, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            st = self.rev()[self.KIND]
            if until(st):
                return st
            time.sleep(0.1)
        self.fail('等不到:%r' % st)


class Prep(RunButton):
    """看板上的「跑準備區」。這裡的看板是臨時副本,伺服器一律跑 job_fake(不會派 agent)。"""

    KIND = 'prep'

    def test_runs_once_and_the_page_can_see_the_result(self):
        bd.set_fb(lambda f: f.update({JOBS[1]['id']: {'app': 'prep'}, JOBS[2]['id']: {'app': 'prep'}}),
                  live=self.path)
        r1 = self.rev()
        self.assertEqual(self.req('/api/run/prep', {})[0], 200)
        self.assertTrue(self.rev()['prep']['running'])
        # 按第二下:cut_tailor 一啟動會殺掉上一個 worker,所以跑的時候不准再開一個
        self.assertEqual(self.req('/api/run/prep', {})[0], 409)
        st = self.wait_run(lambda s: s.get('phase') == 'done')
        self.assertFalse(st['running'])
        self.assertEqual(st['ready'], 2)
        fb = read_fb(self.path)
        self.assertEqual(fb[JOBS[1]['id']]['app'], 'ready')
        self.assertNotEqual(self.rev()['rev'], r1['rev'])   # 頁面靠這個知道要重拿

    def test_nothing_to_run_says_why(self):
        self.assertEqual(self.req('/api/run/prep', {})[0], 200)
        st = self.wait_run(lambda s: s.get('phase') == 'nothing')
        self.assertIn('沒有卡', st['msg'])

    def test_a_run_that_died_does_not_block_the_next(self):
        os.environ['JOB_FAKE_DIE'] = '1'
        self.req('/api/run/prep', {})
        st = self.wait_run(lambda s: s.get('phase') == 'died')
        self.assertFalse(st['running'])
        os.environ.pop('JOB_FAKE_DIE')
        self.assertEqual(self.req('/api/run/prep', {})[0], 200)


class CutTailorEarlyExit(unittest.TestCase):
    """cut_tailor 沒東西可跑時要把原因寫進進度,看板上按了鈕的人才看得到為什麼沒動。"""

    def run_ct(self, fb, home=None):
        import subprocess
        d = self.enterContext(tempfile.TemporaryDirectory(prefix='ct-'))
        path = os.path.join(d, 'board.html')
        make_board(path, fb)
        env = dict(os.environ, CUT_TAILOR_TMP=d)
        if home:
            env['JOBSALVO_HOME'] = home
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'cut_tailor.py'), '--board', path],
                           env=env, capture_output=True, text=True, timeout=60)
        with open(os.path.join(d, 'cut_tailor_status.json'), encoding='utf-8') as f:
            return r.returncode, json.load(f)

    def test_empty_resumes_still_prompt_without_a_choice(self):
        import cut_tailor
        text, _numbered = cut_tailor.prompt([('https://example.test/job', 'Engineer')], resumes=[])
        self.assertIn('目前沒有已勾選的履歷', text)
        self.assertIn('這輪照常判斷職缺,不選履歷、不選語言', text)
        self.assertNotIn('  resume ', text)
        self.assertNotIn('  lang ', text)

    def test_removed_cards_in_prep_are_not_sent_to_the_agent(self):
        """準備區裡按了 🗑 移除的卡(app 還是 prep、多一個 rm)不交給 agent 判、不推進待你決定、不佔「跑幾張」的名額。
        以前照樣派 agent,直連 404 還被標成出錯了;看板按鈕上的張數本來就不算它。"""
        import cut_tailor as ct
        d = self.enterContext(tempfile.TemporaryDirectory(prefix='ct-'))
        path = os.path.join(d, 'board.html')
        cases = [({JOBS[0]['id']: {'app': 'prep', 'rm': 1}, JOBS[1]['id']: {'app': 'prep'}}, [JOBS[1]['id']]),
                 ({JOBS[0]['id']: {'app': 'prep', 'rm': 1}}, [])]
        for fb, want in cases:
            with self.subTest(fb=fb):
                make_board(path, fb)
                taken = []

                def take(rows, out_dir=None):
                    taken.extend(u for u, _ in rows)
                    sys.exit('測試:要交給 agent 的卡到這裡就停,不抓頁面、不派 agent')
                with mock.patch.object(ct, '_status'), mock.patch.object(ct, 'skip_approved', side_effect=take), \
                        mock.patch.object(sys, 'argv', ['cut_tailor.py', '--board', path]):
                    with self.assertRaises(SystemExit) as stop:
                        ct.main()
                self.assertEqual(taken, want)
                if not want:
                    self.assertIn('沒有卡', str(stop.exception))

    def test_empty_prep_is_not_called_all_approved(self):
        """準備區是空的,不能說成「要跑的都被標成認可過了」。"""
        rc, st = self.run_ct({})
        self.assertEqual(st['phase'], 'nothing')
        self.assertIn('沒有卡', st['msg'])


class StalePid(unittest.TestCase):
    """pid 會被系統回收。舊的 pid 檔或進度檔指到的號碼,現在可能是完全不相干的程式。"""

    def setUp(self):
        import subprocess
        import cut_tailor as ct
        self.ct = ct
        self.dir = self.enterContext(tempfile.TemporaryDirectory(prefix='pid-'))
        # 一個不相干、活著的行程。自己一個行程群組:萬一保護被改壞,被整組砍的也只有它
        self.other = subprocess.Popen(['sleep', '30'], start_new_session=True)

    def tearDown(self):
        self.other.kill(); self.other.wait()

    def test_stop_previous_does_not_kill_a_stranger(self):
        pidf = os.path.join(self.dir, 'cut_tailor.pid')
        with open(pidf, 'w') as f:
            f.write(str(self.other.pid))
        with mock.patch.object(self.ct, 'PIDF', pidf):
            self.ct.stop_previous()
        time.sleep(0.3)
        self.assertIsNone(self.other.poll(), '把不相干的行程殺掉了')

    def test_progress_does_not_count_a_stranger_as_running(self):
        """不然看板上的按鈕會一直停在「跑準備區中」按不下去。"""
        self.ct._status({'phase': 'agent', 'pid': self.other.pid, 'n': 3, 't0': time.time()}, sp=self.dir)
        st = self.ct.progress(self.dir)
        self.assertEqual(st['phase'], 'died')
        self.assertFalse(st['running'])


class CutTailorProgress(unittest.TestCase):
    """按下「跑準備區」後,看板在 agent 階段顯示「做完幾張」;這個數字從這一輪自己的夾算。"""

    def setUp(self):
        import subprocess
        import cut_tailor as ct
        self.ct = ct
        self.dir = self.enterContext(tempfile.TemporaryDirectory(prefix='progress-'))
        self.round = os.path.join(self.dir, 'runs', 'r1')
        os.makedirs(self.round)
        self.me = subprocess.Popen(['sh', '-c', 'sleep 30; :', 'cut_tailor.py'], start_new_session=True)   # 看板認自己的行程靠指令裡有 cut_tailor.py
        self.addCleanup(lambda: (self.me.kill(), self.me.wait()))
        self.urls = ['https://example.test/jobs/1', 'https://example.test/jobs/2']
        with open(os.path.join(self.round, 'rows.json'), 'w', encoding='utf-8') as f:
            json.dump([[u, f'T{i}'] for i, u in enumerate(self.urls)], f)

    def status(self, **extra):
        st = {'phase': 'agent', 'pid': self.me.pid, 't0': time.time() - 5, 'n': 2}
        self.ct._status(dict(st, **extra), sp=self.dir)

    def test_agent_phase_counts_cards_written_in_this_rounds_folder(self):
        self.status(out_dir=self.round)
        write_fill(self.round, self.urls[0], {'resume': 'a'})
        self.assertEqual(self.ct.progress(self.dir).get('done'), 1)

    def test_agent_phase_without_out_dir_shows_no_count(self):
        self.status()
        self.assertNotIn('done', self.ct.progress(self.dir))


class Research(RunButton):
    """看板上的「🔎 找新職缺」:更深、更廣、指定方向。臨時副本上跑 job_fake(不會派 agent)。"""
    KIND = 'research'

    def test_runs_once_and_the_page_can_see_the_result(self):
        r1 = self.rev()
        self.assertEqual(self.req('/api/run/research', {'mode': 'dir', 'text': '遊戲反作弊', 'resume_text': '測試履歷'})[0], 200)
        st = self.rev()['research']
        self.assertTrue(st['running'])
        self.assertEqual((st['mode'], st['direction']), ('dir', '遊戲反作弊'))
        self.assertEqual(self.req('/api/run/research', {'mode': 'wide', 'resume_text': '測試履歷'})[0], 409)   # 同時只跑一輪
        st = self.wait_run(lambda s: s.get('phase') == 'done')
        self.assertEqual(st['added'], 2)
        self.assertEqual(len(bd.parse(read(self.path))['data']['jobs']), len(JOBS) + 2)
        self.assertNotEqual(self.rev()['gen'], r1['gen'])    # 頁面靠這個知道要重拿職缺

    def test_bad_requests(self):
        self.assertEqual(self.req('/api/run/research', {'mode': 'x'})[0], 400)
        self.assertEqual(self.req('/api/run/research', {'mode': 'dir', 'text': '  '})[0], 400)
        self.assertIsNone(bs._PROC.get('research'))


    def test_a_run_that_died_does_not_block_the_next(self):
        os.environ['JOB_FAKE_DIE'] = '1'
        self.req('/api/run/research', {'mode': 'deep', 'resume_text': '測試履歷'})
        st = self.wait_run(lambda s: s.get('phase') == 'died')
        self.assertFalse(st['running'])
        os.environ.pop('JOB_FAKE_DIE')
        self.assertEqual(self.req('/api/run/research', {'mode': 'deep', 'resume_text': '測試履歷'})[0], 200)




class FeedbackDump(Tmp):
    def test_main_keeps_auto_region_markers_and_is_idempotent(self):
        from unittest.mock import patch
        import config as cf
        import feedback_dump
        import prefs

        note = os.path.join(self.dir, 'preference-note.md')
        legacy = os.path.join(self.dir, 'prefs.md')
        make_board(self.path, {'https://ex.test/job/1': {'s': 'like', 'n': '想多看這類'}})
        with patch.object(feedback_dump, 'OUT', note), \
             patch.object(prefs, 'PREF', note), \
             patch.object(cf, 'PREFS', legacy), \
             patch('sys.argv', ['feedback_dump.py', '--live', self.path]):
            feedback_dump.main()
            first = read(note)
            feedback_dump.main()
            second = read(note)

        self.assertEqual(first, second)
        self.assertEqual(first.count(feedback_dump.START), 1)
        self.assertEqual(first.count(feedback_dump.END), 1)

    def test_a_card_only_added_to_prep_counts_as_new_feedback(self):
        import feedback_dump

        make_board(self.path, {'https://ex.test/job/1': {'app': 1}})
        text, listed, _lines = feedback_dump.feedback_delta(self.path, os.path.join(self.dir, 'note.md'))
        self.assertEqual(['https://ex.test/job/1'], listed)
        self.assertIn('    - 卡片代號=https://ex.test/job/1\n    - 標記=已加入準備區\n', text)




class PrepSkips(Tmp):
    """跑準備區沒產出的卡。網站擋程式時,活的職缺只是抓不到 JD,
    不能因此移出準備區、標成技術錯誤(那會蓋掉原本的喜歡/還好)。"""

    def test_skipped_cards_stay_in_prep_with_marks_untouched(self):
        import cut_tailor as ct
        a, b = JOBS[0]['id'], JOBS[1]['id']
        make_board(self.path, {a: {'s': 'like', 'app': 'prep'}, b: {'s': 'meh', 'app': 'prep'}})
        out = os.path.join(self.dir, 'trial')
        hand(out, {})
        for u, f in ((a, {'skip': True, 'reason': '抓不到 JD:104 擋程式'}), (b, {'variant': 'general', 'lang': 'zh'})):
            write_fill(out, u, f)
        moved = ct._apply_stages([(a, 'A'), (b, 'B')], self.path, out_dir=out)
        fb = read_fb(self.path)
        self.assertEqual(fb[a], {'s': 'like', 'app': 'prep'})       # 一個字都沒動
        self.assertEqual(fb[b], {'s': 'meh', 'app': 'ready'})       # 產出履歷的照舊推到待你決定
        self.assertEqual(moved, {'ready': 1, 'skipped': 1})

    def test_agent_reported_page_status_stays_as_agent_text(self):
        """agent 對頁面或網址的說法只照原文留在卡上,不改階段或標記下架。"""
        import cut_tailor as ct
        a = JOBS[0]['id']
        make_board(self.path, {a: {'s': 'meh', 'app': 'prep'}})
        out = os.path.join(self.dir, 'trial')
        numbered = hand(out, {a: 'Risk Analyst.\nThis job is no longer available.'})
        write_fill(out, a, {'material_id': numbered.material_id, 'skip': True,
                            'reason': '職缺已關:網址導回職缺列表'})
        moved = ct._apply_stages([(a, 'A')], self.path, out_dir=out)
        self.assertEqual(read_fb(self.path)[a], {'s': 'meh', 'app': 'prep'})
        job = bd.parse(read(self.path))['data']['jobs'][0]
        self.assertFalse(job.get('dead'))
        self.assertEqual(job['prep_note'], '職缺已關:網址導回職缺列表')
        self.assertEqual(moved, {'ready': 0, 'skipped': 1})

    def test_agent_skip_is_not_blocked_by_a_page_citation(self):
        """跳過理由保留為 agent 說法,不因頁面文字或缺少引文拒收。"""
        import cut_tailor as ct
        a = JOBS[0]['id']
        make_board(self.path, {a: {'s': 'meh', 'app': 'prep'}})
        out = os.path.join(self.dir, 'trial')
        numbered = hand(out, {a: 'Risk Analyst. Apply now.'})
        write_fill(out, a, {'material_id': numbered.material_id, 'skip': True, 'reason': '職缺已關:已額滿'})
        moved = ct._apply_stages([(a, 'A')], self.path, out_dir=out)
        self.assertEqual(read_fb(self.path)[a], {'s': 'meh', 'app': 'prep'})
        job = bd.parse(read(self.path))['data']['jobs'][0]
        self.assertFalse(job.get('dead'))
        self.assertEqual(job['prep_note'], '職缺已關:已額滿')
        self.assertEqual(moved, {'ready': 0, 'skipped': 1})

    def test_agent_fixes_a_drifted_card_name_and_keeps_the_link(self):
        """agent 判斷「同一個缺、看板上的名字過時」時寫 real_title,程式照改卡片名字,後面的連結留著。"""
        import cut_tailor as ct
        u = JOBS[0]['id']
        make_board(self.path, {u: {'app': 'prep'}}, jobs=[{'id': u, 'target': f'Northwind Graduate · Risk Operations Specialist (SQL)（[Lever]({u})）'}])
        out = os.path.join(self.dir, 'trial')
        numbered = hand(out, {u: 'Northwind Graduate · Risk Analyst\nSQL, Python.'})
        write_fill(out, u, {'material_id': numbered.material_id, 'variant': 'general', 'lang': 'en',
                                'real_title': {'text': 'Northwind Graduate · Risk Analyst', 'cite': [1]}})
        self.assertEqual(ct.apply_real_titles([(u, 'x')], self.path, out_dir=out), 1)
        t = [j for j in bd.parse(read(self.path))['data']['jobs'] if j['id'] == u][0]['target']
        self.assertEqual(t, f'Northwind Graduate · Risk Analyst（[Lever]({u})）')

class PrepRunFinish(Tmp):
    def test_approved_output_is_excluded_from_future_runs(self):
        import cut_tailor as ct
        from unittest.mock import patch
        a, b = JOBS[0]['id'], JOBS[1]['id']
        out = os.path.join(self.dir, 'out')
        write_fill(out, a, {'approved': True, 'variant': 'general'})
        with patch.object(ct, 'OUT', out):
            self.assertEqual(ct.skip_approved([(a, 'Approved'), (b, 'Pending')]), [(b, 'Pending')])

    def test_the_saved_fill_keeps_only_what_the_gate_accepted_and_never_an_approval_the_agent_wrote(self):
        """#313 審查:這一輪的交件單經安檢門,存回正式位置的只有收下的格子加上正式那份裡別人寫的欄位;
        agent 自己寫 approved(或沒登記的格子)不會存下來,下一輪也不會因此被跳過。"""
        import cut_tailor as ct
        from types import SimpleNamespace
        from unittest.mock import patch

        a = JOBS[0]['id']
        make_board(self.path, {a: {'s': 'like', 'app': 'prep'}})
        sp = os.path.join(self.dir, 'state')
        run_out, canonical = os.path.join(sp, 'runs', 'fresh'), os.path.join(sp, 'fills')
        os.makedirs(os.path.join(run_out, ct.jid(a)), exist_ok=True)
        os.makedirs(canonical, exist_ok=True)
        keep_round(run_out, time.time() - 10, [[a, 'Fresh']])
        write_fill(canonical, a, {'resume': 'old', 'tailored': '他的產線寫的客製內容'})
        write_fill(run_out, a, {'resume': 'general', 'lang': 'klingon', 'why': 'fresh', 'approved': True, 'secret': 'x'})
        with patch.multiple(ct, SP=sp, OUT=canonical, PIDF=os.path.join(sp, 'pid'), WORKER_PIDF=os.path.join(sp, 'worker.pid')):
            with patch.object(ct, '_status'), patch.object(ct, '_report'), \
                 patch.object(ct.ar, 'argv_for', return_value=(['codex'], '/repo')), \
                 patch.object(ct.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
                 patch.object(ct.ar, 'wait_done', return_value=[ct.ar.AgentResult('completed', 0, 123)]), \
                 patch.object(ct.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                ct.run_finish(self.path, out_dir=run_out)
            with open(os.path.join(canonical, ct.jid(a), 'fill.json'), encoding='utf-8') as f:
                saved = json.load(f)
            self.assertEqual(saved.get('tailored'), '他的產線寫的客製內容')      # 別人寫的欄位原樣留著
            self.assertNotIn('resume', saved)            # worker 那幾欄只認這一輪收下的:舊的不留,沒收下的(語言不對)不存
            self.assertEqual(saved.get('why'), 'fresh')                        # 收下的格子
            self.assertNotIn('approved', saved)                                # agent 寫的認可不算他的決定
            self.assertNotIn('secret', saved)                                  # 沒登記的格子
            self.assertNotIn('lang', saved)                                    # 安檢門沒收下的格子
            self.assertEqual(ct.skip_approved([(a, 'Fresh')]), [(a, 'Fresh')])

    def test_finish_uses_only_this_runs_outputs_and_reports_missing_rows(self):
        import cut_tailor as ct
        from types import SimpleNamespace
        from unittest.mock import patch

        a, b = JOBS[0]['id'], JOBS[1]['id']
        make_board(self.path, {a: {'s': 'like', 'app': 'prep'}, b: {'s': 'meh', 'app': 'prep'}})
        sp = os.path.join(self.dir, 'state')
        run_out = os.path.join(sp, 'runs', 'fresh')
        canonical = os.path.join(sp, 'fills')
        os.makedirs(os.path.join(sp, 'runs'), exist_ok=True)
        os.makedirs(os.path.join(run_out, ct.jid(a)), exist_ok=True)
        os.makedirs(os.path.join(canonical, ct.jid(b)), exist_ok=True)
        keep_round(run_out, time.time() - 10, [[a, 'Fresh'], [b, 'Missing']])
        write_fill(run_out, a, {'variant': 'general', 'lang': 'zh', 'why': 'fresh'})
        with open(os.path.join(canonical, ct.jid(b), 'fill.json'), 'w', encoding='utf-8') as f:
            json.dump({'variant': 'stale', 'lang': 'en'}, f)

        statuses, reports = [], []
        with patch.multiple(ct, SP=sp, OUT=canonical, PIDF=os.path.join(sp, 'pid'),
                            WORKER_PIDF=os.path.join(sp, 'worker.pid')):
            with patch.object(ct, '_status', side_effect=statuses.append), \
                 patch.object(ct, '_report', side_effect=lambda *args: reports.append(args)), \
                 patch.object(ct.ar, 'argv_for', return_value=(['codex'], '/repo')), \
                 patch.object(ct.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
                 patch.object(ct.ar, 'wait_done', return_value=[ct.ar.AgentResult('completed', 0, 123)]), \
                 patch.object(ct.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as reconcile:
                ct.run_finish(self.path, out_dir=run_out)

        fb = read_fb(self.path)
        self.assertEqual(fb[a]['app'], 'ready')
        self.assertEqual(fb[b]['app'], 'prep')
        data = bd.parse(read(self.path))['data']
        cards = {j['id']: j for j in data['jobs']}
        self.assertEqual(cards[b]['prep_note'], '這一輪沒判到')
        self.assertNotIn('resume', cards[b])
        with open(os.path.join(canonical, ct.jid(b), 'fill.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['variant'], 'stale')
        self.assertEqual(statuses[-1]['phase'], 'incomplete')
        self.assertIn('incomplete', ct.jobrun.END)
        self.assertEqual(statuses[-1]['missing'], [ct.jid(b)])
        self.assertEqual(len(reports), 1)
        self.assertIn('沒判到', reports[0][0])
        self.assertEqual(len(reconcile_process_calls(reconcile)), 1)

    def test_a_round_missing_its_resumes_is_the_programs_error_not_his_task(self):
        """這一輪的夾少了交付時存的可選履歷:不改用當下設定,收尾當成程式出錯停下。"""
        self._finish_without('resumes.json')

    def test_a_round_missing_its_handed_numbers_is_the_programs_error_not_his_task(self):
        """這一輪的夾少了交付時存的編號:不跳過引用的核對,收尾當成程式出錯停下。"""
        self._finish_without('handed.json')

    def _finish_without(self, name):
        """收尾讀不到這一輪夾裡的那個檔:卡不推進;看板上記成程式的錯,不列進「要你處理」。"""
        import agent_report
        import cut_tailor as ct
        from types import SimpleNamespace
        from unittest.mock import patch

        a = JOBS[0]['id']
        make_board(self.path, {a: {'s': 'like', 'app': 'prep'}})
        sp = os.path.join(self.dir, name)
        run_out = os.path.join(sp, 'runs', 'r1')
        keep_round(run_out, time.time() - 10, [[a, 'A']])
        numbered = hand(run_out, {a: 'Risk Analyst\nApply now.'})
        write_fill(run_out, a, {'material_id': numbered.material_id, 'variant': 'general', 'lang': 'zh',
                                'real_title': {'text': 'Risk Analyst', 'cite': [1]}})
        os.remove(os.path.join(run_out, name))
        with patch.multiple(ct, SP=sp, OUT=os.path.join(sp, 'fills'), PIDF=os.path.join(sp, 'pid'),
                            WORKER_PIDF=os.path.join(sp, 'worker.pid'),
                            STATUSF=os.path.join(sp, 'cut_tailor_status.json')), \
             patch.object(ct.ar, 'run', autospec=True, return_value=ct.ar.AgentResult('completed', 0, 123)), \
             patch.object(ct.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')), \
             patch.object(sys, 'argv', ['cut_tailor.py', '--finish', '--board', self.path,
                                        '--out-dir', run_out]):
            with self.assertRaisesRegex(Exception, name):
                ct.main()
            self.assertEqual(ct.progress()['phase'], 'failed')
        fb = read_fb(self.path)
        self.assertEqual(fb[a]['app'], 'prep')
        self.assertEqual(agent_report.todo(fb), [])
        self.assertTrue(any(name in it['msg'] for it in agent_report.open_items(fb)))
        hand(run_out, {a: 'Risk Analyst\nApply now.'})        # 下一輪收得完:那一則程式的錯收掉
        keep_round(os.path.join(sp, 'runs', 'r2'), time.time() - 10, [])
        with patch.multiple(ct, SP=sp, OUT=os.path.join(sp, 'fills'), PIDF=os.path.join(sp, 'pid'),
                            WORKER_PIDF=os.path.join(sp, 'worker.pid'),
                            STATUSF=os.path.join(sp, 'cut_tailor_status.json')), \
             patch.object(ct.ar, 'run', autospec=True, return_value=ct.ar.AgentResult('completed', 0, 123)), \
             patch.object(ct.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')):
            ct.run_finish(self.path, out_dir=os.path.join(sp, 'runs', 'r2'))
        self.assertFalse(any(name in it['msg'] for it in agent_report.open_items(read_fb(self.path))))

    def test_old_program_errors_that_cannot_be_cleared_are_reported(self):
        """收完一輪要收掉之前留下的程式錯誤;收不掉也要在看板上看得到(記成程式的錯),不是默默留著。"""
        import agent_report
        import cut_tailor as ct
        from types import SimpleNamespace
        from unittest.mock import patch

        make_board(self.path, {})
        sp = os.path.join(self.dir, 'state')
        run_out = os.path.join(sp, 'runs', 'r1')
        keep_round(run_out, time.time() - 10, [])
        with patch.multiple(ct, SP=sp, OUT=os.path.join(sp, 'fills'), PIDF=os.path.join(sp, 'pid'),
                            WORKER_PIDF=os.path.join(sp, 'worker.pid'),
                            STATUSF=os.path.join(sp, 'cut_tailor_status.json')), \
             patch.object(ct.ar, 'run', autospec=True, return_value=ct.ar.AgentResult('completed', 0, 123)), \
             patch.object(ct.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')), \
             patch('agent_report.resolve', side_effect=OSError('看板檔被鎖住')):
            ct.run_finish(self.path, out_dir=run_out)
        fb = read_fb(self.path)
        msgs = [it['msg'] for it in agent_report.open_items(fb)]
        self.assertTrue(any('看板檔被鎖住' in m for m in msgs), msgs)
        self.assertEqual(agent_report.todo(fb), [])


class PrepReselection(Tmp):
    def _state_paths(self):
        root = os.path.join(self.dir, 'cut-tailor-state')
        out = os.path.join(self.dir, 'cut-tailor-out')
        os.makedirs(root, exist_ok=True)
        os.makedirs(out, exist_ok=True)
        return {
            'SP': root,
            'OUT': out,
            'STATUSF': os.path.join(root, 'status.json'),
            'PIDF': os.path.join(root, 'pid'),
            'WORKER_PIDF': os.path.join(root, 'worker.pid'),
            'RUNS': os.path.join(root, 'runs'),
        }

    def test_only_never_picked_changed_signature_and_new_feedback_are_sent_to_agent(self):
        import config as cf
        import prefs
        import cut_tailor as ct
        from types import SimpleNamespace
        from unittest.mock import patch

        resume_file = os.path.join(self.dir, 'resume.pdf')
        with open(resume_file, 'wb') as f:
            f.write(b'resume')
        language = list(cf.LANGS)[0]
        languages = list(cf.LANGS)
        resume = {'id': 'general', 'name': 'General', 'enabled': True,
                  'when': 'General jobs', 'files': {language: resume_file}}
        urls = [f'https://ex.test/job/reselect-{n}' for n in range(4)]
        current = prefs.resume_selection_signature([resume])
        old_feedback = prefs.feedback_signature('old feedback')
        empty_feedback = prefs.feedback_signature('')
        jobs = [
            {'id': urls[0], 'target': 'Never picked'},
            {'id': urls[1], 'target': 'Changed signature', 'resume': {
                'recommend': 'general', 'lang': language, 'pick_why': 'previous',
                'selection_signature': 'old-signature', 'feedback_signature': empty_feedback}},
            {'id': urls[2], 'target': 'New feedback', 'resume': {
                'recommend': 'general', 'lang': language, 'pick_why': 'previous',
                'selection_signature': current, 'feedback_signature': old_feedback}},
            {'id': urls[3], 'target': 'Unchanged', 'resume': {
                'recommend': 'general', 'lang': language, 'pick_why': 'still suitable',
                'selection_signature': current, 'feedback_signature': empty_feedback}},
        ]
        fb = {u: {'app': 'prep'} for u in urls}
        fb[urls[2]]['rzfb'] = 'new feedback'
        make_board(self.path, fb, jobs=jobs)
        paths = self._state_paths()

        with patch.object(cf, 'LANGS', languages), patch.object(cf, 'RESUMES', {'general': resume}), \
             patch.multiple(ct, **paths), patch.object(ct, 'stop_previous'), \
             patch('web.fetch.request_page', side_effect=lambda url, **_: page_result(
                 url, 'Active role: expired credentials; unrelated account is 已關閉.')), \
             patch.object(ct.subprocess, 'Popen', return_value=SimpleNamespace(pid=123)), \
             patch.object(sys, 'argv', ['cut_tailor.py', '--board', self.path]):
            ct.main()

        round_dir = json.load(open(paths['STATUSF'], encoding='utf-8'))['out_dir']
        rows = json.load(open(os.path.join(round_dir, 'rows.json'), encoding='utf-8'))
        self.assertEqual([u for u, _ in rows], urls[:3])
        prompt = open(os.path.join(round_dir, 'prompt.txt'), encoding='utf-8').read()
        self.assertTrue(all(u in prompt for u in urls[:3]))
        self.assertIn('Active role: expired credentials; unrelated account is 已關閉.', prompt)
        self.assertIn('不可開啟來源網址或使用瀏覽器', prompt)
        self.assertNotIn(urls[3], prompt)
        self.assertFalse(next(j for j in bd.parse(read(self.path))['data']['jobs']
                              if j['id'] == urls[0]).get('dead', False))
        self.assertEqual(json.load(open(os.path.join(round_dir, 'cached.json'), encoding='utf-8')), [urls[3]])

    def test_finishing_round_one_after_round_two_starts_reads_only_round_one(self):
        """收尾是另一個程序:第二輪開始之後才收第一輪,收尾讀的每一樣都要是第一輪交付時存下的
        (開始時間、列、選擇簽名、沿用的卡、指示、編號與代號、可選履歷),不是第二輪蓋過的共用檔、也不是當下設定。"""
        import config as cf
        import prefs
        import cut_tailor as ct
        from types import SimpleNamespace
        from unittest.mock import patch

        files = {}
        for name in ('a', 'b'):
            files[name] = os.path.join(self.dir, f'resume-{name}.pdf')
            with open(files[name], 'wb') as f:
                f.write(name.encode())
        resume = {name: {'id': name, 'name': name.upper(), 'enabled': True, 'when': '', 'files': {'zh': files[name]}}
                  for name in files}
        with patch.object(cf, 'LANGS', ['zh']):
            sig = {name: prefs.resume_selection_signature([resume[name]]) for name in files}
        u1, u2 = 'https://ex.test/job/round-one', 'https://ex.test/job/round-two'
        c1, c2 = 'https://ex.test/job/cached-one', 'https://ex.test/job/cached-two'
        jd = {u1: 'Round One Analyst\nThis role is open.', u2: 'Round Two Engineer\nWe are hiring.',
              c1: 'cached one', c2: 'cached two'}

        def cached(url, name):
            return {'id': url, 'target': url, 'resume': {
                'recommend': name, 'lang': 'zh', 'pick_why': 'still fits', 'selection_signature': sig[name],
                'feedback_signature': prefs.feedback_signature('')}}
        fb = {u: {'app': 'prep'} for u in jd}
        fb[u1]['rzfb'], fb[u2]['rzfb'] = 'round one note', 'round two note'
        make_board(self.path, fb, jobs=[{'id': u1, 'target': 'One'}, {'id': u2, 'target': 'Two'},
                                        cached(c1, 'a'), cached(c2, 'b')])
        root = os.path.join(self.dir, 'cut-tailor-state')
        os.makedirs(root)
        paths = {'SP': root, 'OUT': os.path.join(self.dir, 'cut-tailor-out'), 'RUNS': os.path.join(root, 'runs'),
                 'STATUSF': os.path.join(root, 'status.json'), 'PIDF': os.path.join(root, 'pid'),
                 'WORKER_PIDF': os.path.join(root, 'worker.pid')}

        def start(name, *urls):
            with patch.object(cf, 'LANGS', ['zh']), patch.object(cf, 'RESUMES', {name: resume[name]}), \
                 patch.multiple(ct, **paths), patch.object(ct, 'stop_previous'), \
                 patch('web.fetch.request_page', side_effect=lambda url, **_: page_result(url, jd[url])), \
                 patch.object(ct.subprocess, 'Popen', return_value=SimpleNamespace(pid=123)), \
                 patch.object(sys, 'argv', ['cut_tailor.py', '--board', self.path,
                                            '--jids', ','.join(ct.jid(u) for u in urls)]):
                ct.main()
            return json.load(open(paths['STATUSF'], encoding='utf-8'))
        one = start('a', u1, c1)
        time.sleep(0.01)
        two = start('b', u2, c2)
        self.assertNotEqual(one['out_dir'], two['out_dir'])

        seen = {}

        def agent(prompt, *_a, **_k):
            seen['prompt'] = prompt
            title = re.search(r'^(\d+)\. Round One Analyst$', prompt, re.M)
            said = re.search(r'"material_id": "(\w+)"', prompt)
            write_fill(one['out_dir'], u1, {'material_id': said and said[1], 'resume': 'a', 'lang': 'zh',
                                            'why': 'fits', 'real_title': {'text': 'Round One Analyst', 'cite': [int(title[1])]}
                                            if title else {'text': '', 'cite': []}})
            return ct.ar.AgentResult('completed', 0, 123)

        def record(name, real):
            def wrapped(*args, **kwargs):
                seen[name] = (args, kwargs)
                return real(*args, **kwargs)
            return wrapped
        with patch.object(cf, 'LANGS', ['zh']), patch.object(cf, 'RESUMES', {'b': resume['b']}), \
             patch.multiple(ct, **paths), patch.object(ct.ar, 'run', autospec=True, side_effect=agent), \
             patch.object(ct, 'check_written', side_effect=record('check_written', ct.check_written)), \
             patch.object(ct, '_apply_stages', side_effect=record('apply', ct._apply_stages)), \
             patch.object(ct, '_promote_cached', side_effect=record('cached', ct._promote_cached)), \
             patch.object(ct, '_report'), \
             patch.object(ct.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
            ct.run_finish(self.path, out_dir=one['out_dir'])

        (rows, t0, _out), _kw = seen['check_written']
        self.assertEqual(rows, [(u1, 'One')])
        self.assertEqual(t0, one['t0'])
        self.assertIn('Round One Analyst', seen['prompt'])
        self.assertNotIn('Round Two Engineer', seen['prompt'])
        _args, kw = seen['apply']
        self.assertEqual(kw['selection_signature'], sig['a'])
        self.assertEqual(kw['feedback_signatures'], {u1: prefs.feedback_signature('round one note')})
        self.assertEqual(kw['checked'][u1], ({'resume': 'a', 'lang': 'zh', 'why': 'fits',
                                              'real_title': {'text': 'Round One Analyst', 'cite': 'Round One Analyst'}}, []))
        self.assertEqual(seen['cached'][0][0], [(c1, '')])
        fb = read_fb(self.path)
        self.assertEqual({u: fb[u]['app'] for u in jd}, {u1: 'ready', c1: 'ready', u2: 'prep', c2: 'prep'})
        card = next(j for j in bd.parse(read(self.path))['data']['jobs'] if j['id'] == u1)
        self.assertEqual((card['target'], card['resume']['recommend']), ('Round One Analyst', 'a'))

    def test_cached_pick_is_promoted_and_reconciled_without_agent(self):
        import config as cf
        import prefs
        import cut_tailor as ct
        from types import SimpleNamespace
        from unittest.mock import patch

        resume_file = os.path.join(self.dir, 'resume.pdf')
        with open(resume_file, 'wb') as f:
            f.write(b'resume')
        language = list(cf.LANGS)[0]
        languages = list(cf.LANGS)
        resume = {'id': 'general', 'name': 'General', 'enabled': True,
                  'when': 'General jobs', 'files': {language: resume_file}}
        url = 'https://ex.test/job/cached'
        signature = prefs.resume_selection_signature([resume])
        make_board(self.path, {url: {'app': 'prep'}}, jobs=[{
            'id': url, 'target': 'Cached choice',
            'resume': {'recommend': 'general', 'lang': language, 'pick_why': 'still suitable',
                       'selection_signature': signature,
                       'feedback_signature': prefs.feedback_signature('')},
        }])
        paths = self._state_paths()
        with patch.object(cf, 'LANGS', languages), patch.object(cf, 'RESUMES', {'general': resume}), \
             patch.multiple(ct, **paths), patch.object(ct, 'stop_previous'), \
             patch('web.fetch.request_page', side_effect=lambda url, **_: page_result(url)), \
             patch.object(ct.subprocess, 'Popen') as launch, \
             patch.object(ct.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as reconcile, \
             patch.object(sys, 'argv', ['cut_tailor.py', '--board', self.path]):
            ct.main()

        self.assertFalse(launch.called)
        self.assertEqual(read_fb(self.path)[url]['app'], 'ready')
        reconciles = reconcile_process_calls(reconcile)
        self.assertEqual(len(reconciles), 1)
        self.assertIn('reconcile.py', reconciles[0].args[0][1])
        status = json.load(open(paths['STATUSF'], encoding='utf-8'))
        self.assertEqual(status['phase'], 'done')
        self.assertEqual(status['ready'], 1)

    def test_reselection_preserves_card_choice_and_marks_content_problem(self):
        import config as cf
        import prefs
        import cut_tailor as ct
        from unittest.mock import patch

        zh_file = os.path.join(self.dir, 'resume-zh.pdf')
        en_file = os.path.join(self.dir, 'resume-en.pdf')
        for path in (zh_file, en_file):
            with open(path, 'wb') as f:
                f.write(b'resume')
        resumes = [
            {'id': 'general', 'name': 'General', 'enabled': True, 'when': '',
             'files': {'zh': zh_file}},
            {'id': 'manual', 'name': 'Manual', 'enabled': True, 'when': '',
             'files': {'en': en_file}},
        ]
        url = 'https://ex.test/job/manual-choice'
        note = '內容需要修正'
        make_board(self.path, {url: {'app': 'prep', 'resume_id': 'manual',
                                     'variant': 'manual', 'lang': 'en', 'rzfb': note}},
                   jobs=[{'id': url, 'target': 'Manual choice',
                          'resume': {'recommend': 'general', 'lang': 'zh',
                                     'pick_why': 'old recommendation'}}])
        out = os.path.join(self.dir, 'fresh')
        write_fill(out, url, {'resume': 'general', 'lang': 'zh', 'why': 'new recommendation', 'content_problem': True})
        with patch.object(cf, 'LANGS', ['zh', 'en']), patch.object(
                cf, 'RESUMES', {item['id']: item for item in resumes}):
            signature = prefs.resume_selection_signature(resumes)
            hand(out, {})
            ct._apply_stages([(url, 'Manual choice')], self.path, out,
                             selection_signature=signature,
                             feedback_signatures={url: prefs.feedback_signature(note)})

        current = read_fb(self.path)[url]
        self.assertEqual(current['resume_id'], 'manual')
        self.assertEqual(current['variant'], 'manual')
        self.assertEqual(current['lang'], 'en')
        self.assertEqual(current['app'], 'ready')
        card_data = bd.parse(read(self.path))['data']['jobs'][0]['resume']
        self.assertEqual(card_data['recommend'], 'general')
        self.assertEqual(card_data['pick_why'], 'new recommendation')
        self.assertTrue(card_data['content_problem'])
        self.assertEqual(card_data['selection_signature'], signature)


class SandboxHome(unittest.TestCase):
    """副本看板的暫存資料夾要帶著設定指到的履歷與附件檔(複製、不連回原檔),不帶建置產物。"""

    def test_sandbox_cli_discards_copied_sources_when_stopped(self):
        import glob, socket, subprocess
        with tempfile.TemporaryDirectory(prefix='sandbox-cli-test-') as root:
            home = os.path.join(root, 'home')
            scratch = os.path.join(root, 'scratch')
            os.makedirs(os.path.join(home, 'resume'))
            os.makedirs(scratch)
            source = os.path.join(home, 'resume', 'cv.md')
            with open(source, 'w', encoding='utf-8') as f:
                f.write('Fake CV source')
            with open(os.path.join(home, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
                json.dump({'resume': {'resumes': [{'id': 'r', 'files': {'en': 'resume/cv.md'}}]}}, f)
            board = os.path.join(root, 'board.html')
            make_board(board)
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            env = dict(os.environ, JOBSALVO_HOME=home, TMPDIR=scratch)
            proc = subprocess.Popen([sys.executable, os.path.join(TOOLS, 'board_server.py'),
                                     '--state', board, '--host', '127.0.0.1', '--port', str(port)],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                for _ in range(50):
                    if proc.poll() is not None:
                        self.fail('沙箱看板沒啟動: ' + proc.stderr.read().decode()[-500:])
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    self.fail('沙箱看板啟動逾時')
                copies = glob.glob(os.path.join(scratch, 'jobsalvo-sandbox-home-*', 'resume', 'cv.md'))
                self.assertEqual(len(copies), 1)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                # 伺服器關之前會等自動流程正在跑的那一輪做完才刪副本(autopilot.Pilot.stop);CI 比本機慢好幾倍,
                # 以前只等 5 秒就強制關掉,刪資料夾那一步根本沒跑到
                killed = False
                try:
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    killed = True
                    proc.kill(); proc.wait()
                err = ('(60 秒沒關掉,強制結束)' if killed else '') + proc.stderr.read().decode(errors='replace')[-2000:]
                proc.stderr.close()
            left = glob.glob(os.path.join(scratch, 'jobsalvo-sandbox-home-*'))
            # 失敗時講清楚剩下哪些檔、伺服器最後說了什麼(只在 CI 的 Linux 上紅過,本機重現不出來)
            files = [os.path.relpath(os.path.join(d, f), scratch) for top in left for d, _, fs in os.walk(top) for f in fs]
            self.assertEqual(left, [], f'剩下的檔:{files[:40]}\n伺服器 stderr:{err}')

    def test_configured_nested_skill_is_available_in_sandbox(self):
        import config as cf
        with tempfile.TemporaryDirectory(prefix='sandbox-skill-test-') as root:
            home = os.path.join(root, 'home')
            skill = 'custom/skills/sub/format.md'
            original = os.path.join(home, skill)
            os.makedirs(os.path.dirname(original))
            with open(original, 'w', encoding='utf-8') as f:
                f.write('Use this fake format.')
            conf = {'resumes': [{'id': 'r', 'skill': skill}], 'attachments': []}
            with mock.patch.object(cf, 'HOME', home), mock.patch.dict(cf.C, {'resume': conf}):
                copy = bs.sandbox_home()
            try:
                with open(os.path.join(copy, skill), encoding='utf-8') as f:
                    self.assertEqual(f.read(), 'Use this fake format.')
            finally:
                shutil.rmtree(copy, ignore_errors=True)

    def test_configured_research_skill_is_available_in_sandbox(self):
        import config as cf
        with tempfile.TemporaryDirectory(prefix='sandbox-research-skill-test-') as root:
            home = os.path.join(root, 'home')
            skill = 'custom/skills/sub/research.md'
            original = os.path.join(home, skill)
            os.makedirs(os.path.dirname(original))
            with open(original, 'w', encoding='utf-8') as f:
                f.write('Research fake jobs.')
            conf = {'skills': {'common': skill}}
            with mock.patch.object(cf, 'HOME', home), mock.patch.dict(cf.C, {'research': conf}):
                copy = bs.sandbox_home()
            try:
                with open(os.path.join(copy, skill), encoding='utf-8') as f:
                    self.assertEqual(f.read(), 'Research fake jobs.')
            finally:
                shutil.rmtree(copy, ignore_errors=True)

    def test_jobs_the_agent_turned_down_are_listed_on_the_sandbox_board(self):
        # PR 預覽與沙箱看板開的是副本:沒帶判過不送的紀錄,「agent 判過不送的職缺」那一塊永遠是空的(J-24)
        import config as cf
        import find
        with tempfile.TemporaryDirectory(prefix='sandbox-turned-test-') as root:
            home = os.path.join(root, 'home')
            os.makedirs(os.path.join(home, '.research'))
            with open(os.path.join(home, '.research', find.TURNED), 'w', encoding='utf-8') as f:
                f.write(json.dumps({'url': 'https://jobs.example/jobs/9', 'why': '不是我要的方向'}) + '\n')
            with mock.patch.object(cf, 'HOME', home), mock.patch.object(cf, 'RESEARCH', os.path.join(home, '.research')):
                copy = bs.sandbox_home()
            try:
                with mock.patch.object(cf, 'RESEARCH', os.path.join(copy, '.research')):
                    self.assertEqual([row['why'] for row in find.turned_down().values()], ['不是我要的方向'])
            finally:
                shutil.rmtree(copy, ignore_errors=True)

    def test_failed_sandbox_copy_discards_partial_files(self):
        import config as cf
        import glob
        with tempfile.TemporaryDirectory(prefix='sandbox-copy-failure-test-') as root:
            home = os.path.join(root, 'home')
            scratch = os.path.join(root, 'scratch')
            os.makedirs(os.path.join(home, 'resume'))
            os.makedirs(scratch)
            with open(os.path.join(home, cf.NAME), 'w', encoding='utf-8') as f:
                f.write('{}')
            with open(os.path.join(home, 'resume', 'good.md'), 'w', encoding='utf-8') as f:
                f.write('Fake source')
            conf = {'resumes': [{'id': 'r', 'files': {'en': 'resume/good.md',
                                                   'zh': 'resume/bad\x00.pdf'}}]}
            with mock.patch.object(cf, 'HOME', home), mock.patch.dict(cf.C, {'resume': conf}), \
                    mock.patch.object(tempfile, 'tempdir', scratch):
                with self.assertRaises(ValueError):
                    bs.sandbox_home()
            self.assertEqual(glob.glob(os.path.join(scratch, 'jobsalvo-sandbox-home-*')), [])

    def test_resume_files_are_copied_not_linked(self):
        import config as cf
        home = cf.HOME
        made = []
        for rel, body in (('resume/sbx-test-cv.pdf', b'%PDF-cv'), ('resume/sbx-test-att.md', b'att'),
                          ('resume/sbx-built/pack.pdf', b'%PDF-pack')):
            path = os.path.join(home, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(body)
            made.append(path)
        conf = dict(cf.C.get('resume') or {})
        conf['resumes'] = [{'id': 'r', 'files': {'en': 'resume/sbx-test-cv.pdf'}}]
        conf['attachments'] = [{'id': 'a', 'files': {'en': 'resume/sbx-test-att.md', 'zh': '../outside.pdf'}}]
        tmp = None
        try:
            with mock.patch.dict(cf.C, {'resume': conf}):
                tmp = bs.sandbox_home()
            copy = os.path.join(tmp, 'resume', 'sbx-test-cv.pdf')
            self.assertTrue(os.path.isfile(copy))
            self.assertFalse(os.path.islink(copy))
            self.assertTrue(os.path.isfile(os.path.join(tmp, 'resume', 'sbx-test-att.md')))
            self.assertFalse(os.path.exists(os.path.join(tmp, 'resume', 'sbx-built')))
            self.assertTrue(os.path.isfile(os.path.join(tmp, cf.NAME)))
            with open(copy, 'wb') as f:
                f.write(b'changed in sandbox')
            with open(made[0], 'rb') as f:
                self.assertEqual(f.read(), b'%PDF-cv')
        finally:
            for path in made:
                os.remove(path)
            shutil.rmtree(os.path.join(home, 'resume', 'sbx-built'), ignore_errors=True)
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_website_and_platform_notes_are_copied_not_linked(self):
        import config as cf
        with tempfile.TemporaryDirectory(prefix='sandbox-notes-test-') as root:
            home = os.path.join(root, 'home')
            os.makedirs(home)
            originals = {
                'website-notes.json': {'schema_version': 1, 'hosts': {'example.test': [
                    {'id': 'note-1', 'host': 'example.test', 'text': '原始網站筆記'}]}},
                'platform-notes.json': {'104': ['舊平台筆記']},
            }
            for name, notes in originals.items():
                with open(os.path.join(home, name), 'w', encoding='utf-8') as f:
                    json.dump(notes, f, ensure_ascii=False)

            copy = None
            try:
                with mock.patch.object(cf, 'HOME', home):
                    copy = bs.sandbox_home()
                for name, notes in originals.items():
                    copied = os.path.join(copy, name)
                    self.assertTrue(os.path.isfile(copied))
                    self.assertFalse(os.path.islink(copied))
                    with open(copied, encoding='utf-8') as f:
                        self.assertEqual(json.load(f), notes)

                website_copy = os.path.join(copy, 'website-notes.json')
                with open(website_copy, 'w', encoding='utf-8') as f:
                    json.dump({'changed': True}, f)
                with open(os.path.join(home, 'website-notes.json'), encoding='utf-8') as f:
                    self.assertEqual(json.load(f), originals['website-notes.json'])
            finally:
                if copy:
                    shutil.rmtree(copy, ignore_errors=True)

    def test_external_source_links_and_styles_are_copied_as_files(self):
        import config as cf
        with tempfile.TemporaryDirectory(prefix='sandbox-source-test-') as root:
            home = os.path.join(root, 'home')
            source = os.path.join(root, 'source')
            os.makedirs(os.path.join(home, 'resume'))
            os.makedirs(source)
            links = {'resume/cv.md': ('cv.md', b'CV source'),
                     'resume/style.css': ('style.css', b'body { color: black; }'),
                     'resume/proof.pdf': ('proof.pdf', b'%PDF-proof')}
            for rel, (name, body) in links.items():
                original = os.path.join(source, name)
                with open(original, 'wb') as f:
                    f.write(body)
                os.symlink(original, os.path.join(home, rel))
            conf = {'resumes': [{'id': 'r', 'files': {'en': 'resume/cv.md'},
                                 'styles': {'en': 'resume/style.css'}}],
                    'attachments': [{'id': 'a', 'files': {'en': 'resume/proof.pdf'}}]}
            with mock.patch.object(cf, 'HOME', home), mock.patch.dict(cf.C, {'resume': conf}):
                copy = bs.sandbox_home()
            try:
                for rel, (name, body) in links.items():
                    copied = os.path.join(copy, rel)
                    self.assertTrue(os.path.isfile(copied), rel)
                    self.assertFalse(os.path.islink(copied), rel)
                    with open(copied, 'rb') as f:
                        self.assertEqual(f.read(), body)
                    with open(copied, 'wb') as f:
                        f.write(b'changed in sandbox')
                    with open(os.path.join(source, name), 'rb') as f:
                        self.assertEqual(f.read(), body)
            finally:
                shutil.rmtree(copy, ignore_errors=True)


class ScriptWritesAreJournaled(Tmp):
    """程式直接改標記也要進流水帳。以前只有看板存檔會記,cut_tailor 蓋掉 14 張的標記查不到。"""

    def test_set_fb_records_old_and_new(self):
        a = JOBS[0]['id']
        make_board(self.path, {a: {'s': 'like', 'app': 'prep'}})
        bd.set_fb(lambda fb: fb[a].update(s='techerr'), live=self.path, by='測試')
        recs = [json.loads(l) for l in read(bd.journal_path(self.path)).splitlines()]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['by'], '測試')
        self.assertEqual(recs[0]['d'][a], [{'s': 'like', 'app': 'prep'}, {'s': 'techerr', 'app': 'prep'}])

    def test_no_change_no_line(self):
        make_board(self.path, {})
        bd.set_fb(lambda fb: None, live=self.path)
        self.assertFalse(os.path.exists(bd.journal_path(self.path)))


class OwnHome(Tmp):
    """資料夾(cf.HOME)也用這個測試自己的暫存資料夾:伺服器起來時的轉換會在資料夾裡留退回點(版本、備份),
    不准落在大家共用的測試資料夾;跑完跟著 Tmp 一起清掉。"""

    def setUp(self):
        super().setUp()
        import config as cf
        with open(os.path.join(self.dir, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
            f.write('{}')
        home = mock.patch.object(cf, 'HOME', self.dir)
        home.start()
        self.addCleanup(home.stop)


class ConversionKeepsARestorePoint(OwnHome):
    """伺服器起來時的資料轉換回不了頭:轉之前一定要有退回點(存一版;存不了版就備份看板檔),沒有就不轉、照實回報。"""

    def setUp(self):
        super().setUp()
        import folder_history
        self.fh = folder_history
        self.enterContext(mock.patch.object(bs, 'run_status', return_value={'running': False}))
        make_board(self.path, {JOBS[0]['id']: {'app': 'ship', 'ds': 'running', 'apply': {'stage': 'fill', 'tab_id': '7'}}})

    def _git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess_run([self.fh._git(), *args], cwd=self.dir, env=env)

    def test_the_version_before_the_conversion_is_the_old_format(self):
        bs.migrate_marks(self.path)

        before = self._git('log', '-1', '--format=%H', '--grep=轉換前').strip()
        self.assertTrue(before)
        old = json.loads(bd.parse(self._git('show', before + ':board.html'))['fb'])
        self.assertEqual(old[JOBS[0]['id']]['ds'], 'running')
        self.assertEqual(read_fb(self.path)[JOBS[0]['id']]['ds'], 'stuck')

    def test_without_version_history_the_old_board_is_backed_up_first(self):
        with mock.patch.object(self.fh, '_git', return_value=None):
            bs.migrate_marks(self.path)

        folder = os.path.join(self.dir, self.fh.BACKUP_DIR)
        [backup] = os.listdir(folder)
        self.assertEqual(json.loads(bd.parse(read(os.path.join(folder, backup)))['fb'])[JOBS[0]['id']]['ds'], 'running')
        self.assertEqual(read_fb(self.path)[JOBS[0]['id']]['ds'], 'stuck')

    def test_without_a_restore_point_nothing_is_converted(self):
        with open(os.path.join(self.dir, self.fh.BACKUP_DIR), 'w', encoding='utf-8') as f:
            f.write('a file where the backup folder should go')
        before = read(self.path)
        with mock.patch.object(self.fh, '_git', return_value=None):
            bs.migrate_marks(self.path)
            self.assertIn('沒有退回點', self.fh.status(self.dir)['conversion'])
        self.assertEqual(read(self.path), before)

    def test_skipped_conversion_shows_up_in_the_environment_check(self):
        """伺服器起來時沒有退回點、跳過轉換:不能只印在伺服器的 log(使用者看不到),
        設定頁的環境檢查要有一列 ⚠️ 攤開,寫哪一種轉換沒做、為什麼、怎麼處理。"""
        import doctor
        with open(os.path.join(self.dir, self.fh.BACKUP_DIR), 'w', encoding='utf-8') as f:
            f.write('a file where the backup folder should go')
        with mock.patch.object(self.fh, '_git', return_value=None), \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            bs.migrate_marks(self.path)
            checks = {c['key']: c for c in doctor.check_environment([])['checks']}
        row = checks['folder_history']
        self.assertFalse(row['ok'])
        self.assertTrue(row['warn'])
        self.assertIn('沒有退回點', row['detail'])
        self.assertIn('沒跑完的填表收尾', row['detail'])
        self.assertIn('轉換還沒轉換', row['detail'])
        self.assertTrue(row['fix'])


def subprocess_run(args, cwd, env):
    import subprocess
    return subprocess.run(args, cwd=cwd, env=env, text=True, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


class ServerStartCleansUpOnDisk(OwnHome):
    def test_leaves_nothing_in_the_shared_test_folder(self):
        """轉換前的退回點(版本、備份)落在這個測試自己的暫存資料夾,跑完跟著清掉;不准留在大家共用的測試資料夾。"""
        shared = os.environ['JOBSALVO_TEST_HOME']

        def everything():
            return {os.path.join(root, n) for root, dirs, files in os.walk(shared) for n in files + dirs}
        before = everything()
        make_board(self.path, {JOBS[0]['id']: {'app': 'ship', 'ds': 'running', 'apply': {'stage': 'fill', 'tab_id': '7'}}})
        with mock.patch.object(bs, 'run_status', return_value={'running': False}):
            bs.migrate_marks(self.path)
        self.assertEqual(read_fb(self.path)[JOBS[0]['id']]['ds'], 'stuck')
        self.assertEqual(sorted(everything() - before), [])

    def test_fill_screenshots_from_before_the_evidence_folder_move_into_it(self):
        """證據夾之前填的卡,填表截圖只在可投遞夾的 .apply/ 裡(可投遞夾會整份重建):伺服器起來時複製進那張卡的
        證據夾,卡上改指過去;檔已經不在的只拿掉舊的位置,已經有證據夾截圖的照舊。"""
        import evidence
        a, b, c = JOBS[0]['id'], JOBS[1]['id'], JOBS[2]['id']
        shot = os.path.join(self.dir, 'ship', 'A-x', '.apply', 'fill.png')
        os.makedirs(os.path.dirname(shot))
        with open(shot, 'wb') as f:
            f.write(b'png')
        make_board(self.path, {a: {'app': 'ship', 'apply': {'stage': 'fill', 'shot': os.path.relpath(shot, self.dir)}},
                               b: {'app': 'ship', 'apply': {'stage': 'fill', 'shot': 'ship/gone/.apply/fill.png'}},
                               c: {'app': 'ship', 'apply': {'stage': 'fill', 'shot': 'x', 'ev': 'r/shot-1.png'}}})
        with mock.patch.object(bs, 'run_status', return_value={'running': False}):
            bs.migrate_marks(self.path)
        fb = read_fb(self.path)
        with open(evidence.path(a, fb[a]['apply']['ev'], self.path), 'rb') as f:
            self.assertEqual(f.read(), b'png')
        self.assertEqual([(u in (a, b, c), 'shot' in fb[u]['apply']) for u in (a, b, c)], [(True, False)] * 3)
        self.assertEqual((fb[b]['apply'].get('ev'), fb[c]['apply']['ev']), (None, 'r/shot-1.png'))

    def test_server_start_settles_interrupted_fills_without_guessing_page_presence(self):
        """中斷的填表不依舊有沒有 tab_id 猜頁面狀態;送出中斷仍標結果不明。"""
        import delivery_state as ds
        a, b, c = JOBS[0]['id'], JOBS[1]['id'], JOBS[2]['id']
        make_board(self.path, {a: {'app': 'ship', 'ds': 'running', 'apply': {'stage': 'fill', 'tab_id': '7'}},
                               b: {'app': 'ship', 'ds': 'running', 'apply': {'stage': 'fill'}},
                               c: {'app': 'ship', 'ds': 'sending', 'approve': {'snap': {}}, 'apply': {'stage': 'fill', 'tab_id': '9'}}})
        with mock.patch.object(bs, 'run_status', return_value={'running': False}):
            bs.migrate_marks(self.path)
        fb = read_fb(self.path)
        self.assertEqual([ds.state(fb[u]) for u in (a, b, c)], ['stuck', 'stuck', 'unsure'])
        self.assertIn('沒跑完', fb[a]['apply']['issues'][0])
        self.assertIn('沒跑完', fb[b]['apply']['issues'][0])


class AgentSandbox(unittest.TestCase):
    def test_agent_runs_outside_the_codex_sandbox(self):
        """沙盒擋掉 Chrome 需要的系統服務(在裡面開 Chrome 一定當掉),也會斷網;派工一律不開沙盒。"""
        import agent_run as ar
        argv, _ = ar.argv_for('main', '嗨', tempfile.gettempdir())
        self.assertEqual(argv[argv.index('-s') + 1], 'danger-full-access')

class BoardStatusExitCode(Tmp):
    def test_card_acceptance_issue_uses_a_distinct_exit_code(self):
        import board_status

        job = JOBS[0]
        make_board(self.path, {job['id']: {'app': 'ready'}}, jobs=[job])
        with mock.patch.object(sys, 'argv', ['board_status.py', '--board', self.path, '--write']), \
             mock.patch('sys.stdout', new=io.StringIO()):
            result = board_status.main()

        self.assertEqual(result, 3)
        self.assertTrue(bd.parse(read(self.path))['data']['status']['issues'])


class ResumeForAgents(unittest.TestCase):
    def test_build_only_markup_never_reaches_the_agent(self):
        """給 agent 的履歷文字只含正文;排版 CSS、照片和產檔標記不會傳進去。"""
        from unittest.mock import patch
        import prefs
        raw = '<style>line-height:1</style><img src="x"><!-- FILL: x ATTACHMENT: y -->\n個人簡介\n為什麼是我'
        with patch.dict(os.environ, {'JOBSALVO_RESUME_TEXT': raw}):
            t = prefs.resume()
        for junk in ('<style', '</style>', '<img', '<!--', 'line-height', 'FILL:', 'ATTACHMENT:'):
            self.assertNotIn(junk, t, junk)
        for keep in ('個人簡介', '為什麼是我'):
            self.assertIn(keep, t, keep)



def prefs_load(path):
    import prefs
    return prefs.load(path)




class SeedRuns(HttpBase):
    """他在卡片/公司列上指名「找類似的」「找這家更多」:走的是既有的更深,範圍由他指定,
    不是第四種找法(以前 --seed-url 會把整輪轉成「指定方向」,同一件事兩套邏輯)。"""
    def test_seeds_run_as_deep_with_scope(self):
        with mock.patch.object(bs, 'is_real', return_value=True):          # 副本跑的是 job_fake,要看的是真的那條指令
            argv = bs.run_argv('research', {'mode': 'deep', 'text': '',
                                            'seeds': [{'k': 'co', 'v': 'Northwind'},
                                                      {'k': 'job', 'v': 'https://ex.test/job/1'}]})
        self.assertIn('--mode', argv)
        self.assertEqual(argv[argv.index('--mode') + 1], 'deep')
        self.assertEqual(argv[argv.index('--seed-co') + 1], 'Northwind')
        self.assertEqual(argv[argv.index('--seed-url') + 1], 'https://ex.test/job/1')
        self.assertNotIn('--direction', argv)

    def test_seed_button_needs_something_to_find(self):
        code, raw, _ = self.req('/api/run/research', {'mode': 'seed', 'seeds': []})
        self.assertEqual(code, 400)
        self.assertIn('指名', json.loads(raw)['msg'])


class TestsLeaveNothingRunning(unittest.TestCase):
    def test_a_saving_test_leaves_no_folder_history_timer(self):
        """#335:存檔會排一個 1 秒後存資料夾版本的計時器;測試結束時沒收掉,它在下一條測試裡跑 git,
        被那條換掉的 subprocess.run 抓到。存過檔的測試一結束,背景不能還有計時器。"""
        result = unittest.TestResult()
        Http('test_undo_only_puts_back_what_nothing_else_moved').run(result)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertEqual([t for t in threading.enumerate() if isinstance(t, threading.Timer)], [])


class PromptPreview(HttpBase):
    """看板上每顆按鈕底下的「會送出去的 prompt」:當場組給他看,不用先跑一輪。"""
    def test_each_find_mode_has_its_own_prompt(self):
        seen = {}
        want = urllib.parse.quote('往鏈上分析挖')
        for mode in ('deep', 'wide', 'dir'):
            code, raw, _ = self.req('/api/prompt?kind=find&mode=%s&text=%s' % (mode, want))
            self.assertEqual(code, 200)
            p = json.loads(raw)['prompt']
            self.assertIn('抓網頁鐵律', p)          # 組的是真的會送出去的那份(含前面的鐵律)
            seen[mode] = p
        self.assertIn('更深', seen['deep'])
        self.assertIn('更廣', seen['wide'])
        self.assertIn('往鏈上分析挖', seen['dir'])   # 他框裡寫的方向要真的進到那一份
        self.assertEqual(len(set(seen.values())), 3, '三種找法應該是三份不同的 prompt')

    def test_judge_and_prep_prompts(self):
        for kind in ('judge', 'prep'):
            code, raw, _ = self.req('/api/prompt?kind=' + kind)
            self.assertEqual(code, 200)
            self.assertIn('抓網頁鐵律', json.loads(raw)['prompt'])

    def test_replies_prompt_note_matches_how_many_it_really_checks(self):
        """查應徵進度真的跑時照「跑幾張」只查前幾張,註記不能說「一次查完所有還在等的卡」。"""
        code, raw, _ = self.req('/api/prompt?kind=replies')
        self.assertEqual(code, 200)
        note = json.loads(raw)['note']
        self.assertNotIn('一次查完', note)
        self.assertIn('跑幾張', note)

    def test_replies_prompt_lists_only_cards_a_real_run_checks(self):
        """看板上沒有那張職缺的已投出卡,真的跑不查;預覽也不列,不能出錯。"""
        gone = 'https://ex.test/job/gone'
        make_board(self.path, {JOBS[0]['id']: {'app': 'sent', 'ds': 'sent', 'sent_at': '2026-11-01'},
                               gone: {'app': 'sent', 'ds': 'sent', 'sent_at': '2026-11-01'}})
        code, raw, _ = self.req('/api/prompt?kind=replies')
        prompt = json.loads(raw)['prompt']
        self.assertEqual(code, 200)
        self.assertNotIn('組這份 prompt 的時候出錯了', prompt)
        self.assertIn(JOBS[0]['id'], prompt)
        self.assertNotIn(gone, prompt)

    def test_every_agent_button_has_a_prompt(self):
        """會派 agent 的按鈕,每一顆都要看得到它會送出去的東西:找缺三種＋判斷、準備區、代投三段。"""
        for q in ('kind=find&mode=deep', 'kind=find&mode=wide', 'kind=find&mode=dir', 'kind=judge', 'kind=prep',
                  'kind=apply&stage=fill', 'kind=apply&stage=fix', 'kind=apply&stage=submit', 'kind=replies'):
            code, raw, _ = self.req('/api/prompt?' + q)
            self.assertEqual(code, 200, q)
            prompt = json.loads(raw)['prompt']
            self.assertTrue(prompt.strip(), q)
            with self.subTest(q):
                self.assertNotIn('組這份 prompt 的時候出錯了', prompt, q)

    def test_cached_prompt_expires_when_the_code_changes(self):
        """記住的那份要跟著「組它的程式」一起作廢。不然別人改了代投的 prompt,
        看板 gen 沒變,他打開看到的還是舊的:看起來像真的,其實不是會送出去的那份。"""
        d = tempfile.mkdtemp(); old = bs.HERE
        try:
            bs.HERE = d
            s1 = bs.pv_sig()
            f = os.path.join(d, 'x.py')
            with open(f, 'w') as fh: fh.write('# 改了組 prompt 的程式')
            os.utime(f, (time.time() + 5, time.time() + 5))
            self.assertNotEqual(bs.pv_sig(), s1)
        finally:
            bs.HERE = old; shutil.rmtree(d, ignore_errors=True)

    def test_same_prompt_is_not_rebuilt(self):
        """組一份要讀看板、算口味索引,不快。同一個看板版本問第二次要用記住的那份,他點開才不會頓。"""
        t0 = time.time(); self.req('/api/prompt?kind=find&mode=wide'); first = time.time() - t0
        t1 = time.time(); self.req('/api/prompt?kind=find&mode=wide'); again = time.time() - t1
        self.assertLess(again, max(first, 0.05))

    def test_example_prompts_say_they_are_examples(self):
        """代投那份是拿某一張卡當例子組的。這句話一定要跟著回來(看板放在最上面),
        不然會以為 prompt 被寫死成那一家公司。"""
        for stage in ('fill', 'fix', 'submit'):
            d = json.loads(self.req('/api/prompt?kind=apply&stage=' + stage)[1])
            self.assertTrue(d.get('note'), stage)
        self.assertIn('骨架', json.loads(self.req('/api/prompt?kind=judge')[1])['note'])
        self.assertTrue(json.loads(self.req('/api/prompt?kind=find&mode=dir')[1])['note'])   # 還沒寫方向
        # 就是那份、沒有「其實不是最終樣子」的,不要硬加一句話煩他
        self.assertFalse(json.loads(self.req('/api/prompt?kind=find&mode=deep')[1])['note'])

    def test_unknown_kind_is_refused(self):
        self.assertEqual(self.req('/api/prompt?kind=nope')[0], 400)

    def test_preview_never_dispatches_an_agent(self):
        """看 prompt 不准真的派 agent 出去。"""
        import agent_run as ar
        launched = []
        with mock.patch.object(ar, 'run', autospec=True, side_effect=lambda *a, **k: launched.append(a)):
            self.req('/api/prompt?kind=find&mode=deep')
            self.req('/api/prompt?kind=prep')
        self.assertEqual(launched, [])


if __name__ == '__main__':
    unittest.main()


class StatusSkipsRemoved(Tmp):
    def test_removed_cards_are_not_checked_or_listed(self):
        # 他已經移除的卡:驗收不再查它,也不列進「N 張沒過驗收」(以前兩頁各掛著看不到的卡的警告)
        import board_status
        urls = ['https://example.invalid/jobs/keep', 'https://example.invalid/jobs/removed']
        make_board(self.path, {urls[0]: {'app': 'ready'}, urls[1]: {'app': 'ready', 'rm': 1}},
                   jobs=[{'id': u, 'target': u} for u in urls])
        with mock.patch.object(board_status.ship, "check", return_value=["缺履歷"]), \
             mock.patch.object(sys, "argv", ["board_status", "--board", self.path, "--write"]):
            board_status.main()
        status = bd.parse(read(self.path))['data']['status']
        self.assertEqual([x['jid'] for x in status['issues']], urls[:1])
