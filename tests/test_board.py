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
import io, os, re, sys, json, gzip, functools, time, shutil, tempfile, threading, unittest, urllib.request, urllib.error, urllib.parse, contextlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import board_doc as bd          # noqa: E402
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


def page_result(url, text='實際 JD 片段', via='fake', posted_at='', posted_source=''):
    import page_fetch
    return page_fetch.PageResult(url, 'ok', text=text, via=via,
                                 posted_at=posted_at, posted_source=posted_source)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='boardtest-')
        self.path = os.path.join(self.dir, 'board.html')
        bs.STATE = self.path
        bs.ALLOW_AGENT[0] = False
        self.builds = []
        self._tb = bs.trigger_build
        bs.trigger_build = lambda: self.builds.append(1)   # 不要真的跑 reconcile
        import board_status   # 職缺頁判斷的快取:每個測試從空的開始,別的測試記的判斷不能帶進來
        with contextlib.suppress(OSError):   # 還沒有快取檔:本來就是空的
            os.remove(board_status._verdict_cache_path())

    def tearDown(self):
        bs.trigger_build = self._tb
        # 存檔排的資料夾版本計時器(folder_history.note_saved)在這條測試裡收掉:還沒響的取消,正在存的等它存完。
        # 以前留著,1 秒後在下一條測試裡跑 git,被那條換掉的 subprocess.run 抓到(#335)
        for t in threading.enumerate():
            if isinstance(t, threading.Timer):
                t.cancel()
                t.join()
        shutil.rmtree(self.dir, ignore_errors=True)


class LeanRules(unittest.TestCase):
    """空的各種寫法都是「沒有」。頁面的 lean() 跟伺服器的 _lean 要是同一套,
    不然「標喜歡 → 取消 → 再標還好」第三下會被當成衝突吃掉,還騙他說別的裝置改過。"""

    def test_empty_forms_are_the_same(self):
        for a in (None, '', [], {}, {'s': ''}, {'s': '', 'n': ''}, {'cutv': {'c1': {}}}):
            self.assertTrue(bs._same(a, None), a)

    def test_key_order_and_empty_fields_do_not_matter(self):
        self.assertTrue(bs._same({'s': 'like', 'n': 'x'}, {'n': 'x', 's': 'like'}))
        self.assertTrue(bs._same({'s': 'like', 'n': ''}, {'s': 'like'}))

    def test_real_differences_are_different(self):
        self.assertFalse(bs._same({'s': 'like'}, {'s': 'meh'}))
        self.assertFalse(bs._same({'s': 'like'}, None))
        self.assertFalse(bs._same(['a'], []))


class WriteFb(Tmp):
    def test_merge_keeps_keys_not_sent(self):
        """以前整包覆蓋:一個擱著沒關的舊分頁一存,別處的新標記全部倒回去。"""
        make_board(self.path, {JOBS[0]['id']: {'s': 'like'}})
        self.assertEqual(bs.write_fb({JOBS[1]['id']: {'s': 'meh'}}), [])
        fb = read_fb(self.path)
        self.assertEqual(fb[JOBS[0]['id']], {'s': 'like'})
        self.assertEqual(fb[JOBS[1]['id']], {'s': 'meh'})

    def test_null_deletes_the_key(self):
        make_board(self.path, {JOBS[0]['id']: {'s': 'like'}})
        bs.write_fb({JOBS[0]['id']: None})
        self.assertNotIn(JOBS[0]['id'], read_fb(self.path))

    def test_conflict_writes_nothing(self):
        """別的裝置先改過其中一筆:整批不寫。寫一半他分不出哪幾筆生效了。"""
        a, b = JOBS[0]['id'], JOBS[1]['id']
        make_board(self.path, {a: {'s': 'like'}})
        before = read(self.path)
        bad = bs.write_fb({a: {'s': 'meh'}, b: {'s': 'grow'}}, base={a: {'s': 'dislike'}, b: None})
        self.assertEqual(bad, [a])
        self.assertEqual(read(self.path), before)

    def test_cancel_then_remark_is_not_a_conflict(self):
        """取消喜歡之後頁面記成 {s:''} 或 {},磁碟上是整筆不見。兩個要算同一件事。"""
        a = JOBS[0]['id']
        make_board(self.path, {})
        for base in ({a: None}, {a: {}}, {a: {'s': ''}}):
            self.assertEqual(bs.write_fb({a: {'s': 'meh'}}, base=base), [], base)
            bs.write_fb({a: None})

    def test_special_keys_save_twice(self):
        """「我的想法」存第二次一定被擋:頁面沒把 __notes__ 的基準更新。伺服器這邊要收得下。"""
        make_board(self.path, {})
        v1 = [{'id': 'n1', 't': 'first'}]
        self.assertEqual(bs.write_fb({'__notes__': v1}, base={'__notes__': None}), [])
        v2 = [{'id': 'n1', 't': 'second'}]
        self.assertEqual(bs.write_fb({'__notes__': v2}, base={'__notes__': v1}), [])
        self.assertEqual(read_fb(self.path)['__notes__'], v2)

    def test_journal_records_only_real_changes(self):
        """流水帳:每次真的改到東西就留一行(改之前、改之後),沒變就不寫。"""
        a = JOBS[0]['id']
        make_board(self.path, {a: {'s': 'like'}})
        bs.write_fb({a: {'s': 'meh'}})
        bs.write_fb({a: {'s': 'meh'}})       # 沒變
        lines = read(bd.journal_path(self.path)).splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec['d'][a], [{'s': 'like'}, {'s': 'meh'}])

    def test_marks_cannot_break_out_of_the_script_block(self):
        a = JOBS[0]['id']
        make_board(self.path, {})
        evil = '</script><script>alert(1)</script>'
        bs.write_fb({a: {'s': 'like', 'n': evil}})
        raw = read(self.path)
        self.assertNotIn(evil, raw)
        self.assertEqual(read_fb(self.path)[a]['n'], evil)


class Install(Tmp):
    def test_install_keeps_live_shell_and_marks(self):
        """管線跑一兩個小時才收尾。以前收尾連樣式與程式也照抄開跑時的快照,
        等於把這段時間改好的看板程式整包倒回舊版。現在只換職缺資料。"""
        live = self.path
        make_board(live, {JOBS[0]['id']: {'s': 'like'}}, app='/*app NEW*/', sty=':root{--new:1}')
        src = os.path.join(self.dir, 'merged.html')
        jobs2 = JOBS + [{'id': 'https://ex.test/job/9', 'target': 'Job 9'}]
        make_board(src, {JOBS[0]['id']: {'s': 'dislike'}}, jobs=jobs2, app='/*app OLD*/', sty=':root{--old:1}')
        bd.install(src, live=live, quiet=True)
        d = bd.parse(read(live))
        self.assertEqual(d['app'], '/*app NEW*/')
        self.assertEqual(d['sty'], ':root{--new:1}')
        self.assertEqual(json.loads(d['fb']), {JOBS[0]['id']: {'s': 'like'}})
        self.assertEqual(len(d['data']['jobs']), 4)
        self.assertIn('>4<', d['thdr'])

    def test_install_keeps_live_interview_bank(self):
        """面試題庫(data.bank)是 bank2board 另外裝進現行看板的,管線產物裡沒有、或是開跑時的舊版。
        收尾換職缺資料時要沿用現行那份,不然他剛磨好的答案會被倒回去。"""
        live = self.path
        make_board(live)
        bd.set_data(lambda data, fb: data.update(bank={'v': 1, 'items': [{'id': 'AI-1'}]}), live=live)
        src = os.path.join(self.dir, 'merged.html')
        make_board(src)
        bd.set_data(lambda data, fb: data.update(bank={'v': 1, 'items': []}), live=src)   # 開跑時的舊版
        bd.install(src, live=live, quiet=True)
        self.assertEqual(bd.parse(read(live))['data']['bank']['items'], [{'id': 'AI-1'}])


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
        # 看板標題、agent 的名字不給改;舊設定檔裡的拿掉,不會照著改
        import config as cf
        self.assertIn('<title>jobsalvo</title>', self.req('/')[1].decode('utf-8'))
        got = cf.migrate_settings({'board': {'title': '我的看板'}, 'agent': {'name': '小幫手', 'agents': []}})
        self.assertNotIn('title', got['board'])
        self.assertNotIn('name', got['agent'])
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

    def test_agent_cannot_write_his_board(self):
        before = read(self.path)
        code, _, _ = self.req('/api/save', {'__rev__': 1, JOBS[1]['id']: {'s': 'meh'}},
                              ua='Mozilla/5.0 Claude/1.0')
        self.assertEqual(code, 403)
        self.assertEqual(read(self.path), before)

    def test_sandbox_lets_agent_write(self):
        bs.ALLOW_AGENT[0] = True
        code, _, _ = self.req('/api/save', {'__rev__': 1, JOBS[1]['id']: {'s': 'meh'}},
                              ua='Mozilla/5.0 Claude/1.0')
        self.assertEqual(code, 200)

    def test_old_tab_sending_everything_is_refused(self):
        body = {'https://ex.test/x/%d' % i: {'s': 'like'} for i in range(41)}
        self.assertEqual(self.req('/api/save', body)[0], 409)

    def test_conflict_names_the_keys(self):
        a = JOBS[0]['id']
        code, raw, _ = self.req('/api/save', {'__rev__': 1, '__base__': {a: {'s': 'meh'}}, a: {'s': 'grow'}})
        self.assertEqual(code, 409)
        self.assertEqual(json.loads(raw)['keys'], [a])

    def test_the_board_sends_events_not_the_delivery_state(self):
        """同時存檔(狀態表修正 9):看板送來的卡上投遞狀態那幾欄不算數,只看它送的事件,照磁碟上現在的狀態套表;
        那一格不准的不做事、回原因。以前看板整張卡寫回,自動流程剛寫的「正在送出」被舊分頁蓋回「你已確認」。"""
        import delivery_state as ds
        a, b = JOBS[0]['id'], JOBS[1]['id']
        filled = {'app': 'ship', 'form': {'plat': 'x', 'f': []}, 'apply': {'stage': 'fill', 'tab_id': '7'}}
        make_board(self.path, {a: dict(filled, ds='sending', approve={'snap': {}}), b: dict(filled, ds='parked')})
        stale_tab = dict(filled, ds='confirmed', approve={'snap': {}}, n='我的筆記')
        code, raw, _ = self.req('/api/save', {'__rev__': 1, a: stale_tab, '__events__': [
            {'u': a, 'ev': 'unconfirm'},                                    # 8 秒計時器已經開始送:取消確認撞上
            {'u': b, 'ev': 'confirm', 'data': {'approve': {'snap': {}, 'round': None}}}]})
        self.assertEqual(code, 200, raw)
        fb = read_fb(self.path)
        self.assertEqual((ds.state(fb[a]), fb[a]['n']), ('sending', '我的筆記'))   # 狀態照磁碟上的,筆記照收
        self.assertEqual(ds.state(fb[b]), 'confirmed')
        self.assertEqual([r['u'] for r in json.loads(raw)['rejected']], [a])
        self.assertIn('正在送出', json.loads(raw)['rejected'][0]['msg'])

    def test_undo_only_when_nothing_else_moved_the_card(self):
        import delivery_state as ds
        a = JOBS[0]['id']
        make_board(self.path, {a: {'app': 'ship', 'ds': 'parked', 'apply': {'stage': 'fill', 'tab_id': '7'}}})
        before = ds.part(read_fb(self.path)[a])
        self.req('/api/save', {'__rev__': 1, '__events__': [{'u': a, 'ev': 'confirm', 'data': {'approve': {'snap': {}}}}]})
        after = ds.part(read_fb(self.path)[a])
        code, raw, _ = self.req('/api/save', {'__rev__': 1, '__events__': [{'u': a, 'undo': {'prev': before, 'after': after}}]})
        self.assertEqual(ds.state(read_fb(self.path)[a]), 'parked')
        self.assertEqual(json.loads(raw)['rejected'], [])
        # 復原之前自動流程已經把它改了(這裡換成換了檔):不准整份放回;換檔的復原就是又換了一次檔
        bs.bd.set_fb(lambda f: ds.fire(f, a, 'files_changed', why='x'), live=self.path)
        code, raw, _ = self.req('/api/save', {'__rev__': 1, '__events__': [{'u': a, 'undo': {'prev': before, 'after': after}}]})
        self.assertEqual(json.loads(raw)['rejected'][0]['u'], a)
        self.assertEqual(ds.state(read_fb(self.path)[a]), 'stale')
        # 正在填的時候按「復原」換回履歷:也算換檔,填完到「上傳的是舊檔」(修正 12)
        bs.bd.set_fb(lambda f: ds.fire(f, a, 'fill_start', apply={'stage': 'fill', 'at': 'x', 'issues': []}), live=self.path)
        code, raw, _ = self.req('/api/save', {'__rev__': 1, '__events__': [
            {'u': a, 'undo': {'prev': before, 'after': after}, 'else': {'ev': 'files_changed', 'data': {'why': '履歷換回來了'}}}]})
        m = read_fb(self.path)[a]
        self.assertEqual((ds.state(m), m['apply']['stale']), ('running', '履歷換回來了'))

    def test_undo_actually_sent_although_the_server_noted_the_sent_version(self):
        """送出結果不明 → 其實送出了 → 復原:後台套完事件會補記寄出的是哪一份(sent_v),看板照狀態表算的 after 沒有它。
        以前復原拿 after 跟後台那張整份比,差這一欄就當成「被別處改過了」不准復原,卡停在已投出
        (CI 的看板檢查抓到:那張卡挑得出履歷才會記 sent_v;單跑那一條挑不出,CI 分組第 1 組前面的檢查讓它挑得出)。"""
        import copy
        import config as cf
        import delivery_state as ds
        a = JOBS[0]['id']
        make_board(self.path, {a: {
            'app': 'ship', 'ds': 'unsure', 'resume_id': next(iter(cf.RESUMES)), 'form': {'plat': 'x', 'f': []},
            'apply': {'stage': 'fill', 'tab_id': '', 'submit_fail': {'problems': ['沒看到成功頁面'], 'clicked': True}},
            'approve': {'snap': {}}}})
        before = read_fb(self.path)[a]
        data = {'by': 'agent', 'at': '2026-10-01T00:00:00Z', 'sent_at': '2026-10-01', 'evidence': {'you_sent': 'x'}}
        board = copy.deepcopy(before)               # 看板手上那一份照同一張表走一步(board.js evFire → dsPart)
        ds.fire({a: board}, a, 'actually_sent', **data)
        self.req('/api/save', {'__rev__': 1, '__events__': [{'u': a, 'ev': 'actually_sent', 'data': data}]})
        self.assertTrue(read_fb(self.path)[a].get('sent_v'))   # 後台真的補記了,看板的 after 沒有
        code, raw, _ = self.req('/api/save', {'__rev__': 1, '__events__': [
            {'u': a, 'undo': {'prev': ds.part(before), 'after': ds.part(board)}}]})
        self.assertEqual((code, json.loads(raw)['rejected']), (200, []))
        self.assertEqual(read_fb(self.path)[a], before)

    def test_undo_of_retry_gives_back_the_locked_form(self):
        """再投一次把整份表單(鎖著的)收進 tries;看板按復原時送復原事件和按之前那張卡,伺服器要把表單連鎖一起放回
        (#303 每顆按鈕按一遍抓到:以前表單回來了、鎖沒回來,已投出的表單變成可以改)。"""
        import delivery_state as ds
        a = JOBS[0]['id']
        card = {'app': 'sent', 'ds': 'sent', 'sent_by': 'manual', 'sent_at': '2026-01-02', 'oc': 'rej',
                'oc_at': {'rej': '2026-01-05'}, 'form': {'plat': 'x', 'f': [{'q': 'Q', 'src': 'bank', 'k': 'k1'}], 'lock': 1}}
        make_board(self.path, {a: card})
        before = read_fb(self.path)[a]
        self.req('/api/save', {'__rev__': 1, '__events__': [{'u': a, 'ev': 'retry'}]})
        after = read_fb(self.path)[a]
        self.assertNotIn('form', after)
        code, raw, _ = self.req('/api/save', {'__rev__': 1, a: before, '__base__': {a: after},
                                              '__events__': [{'u': a, 'undo': {'prev': ds.part(before), 'after': ds.part(after)}}]})
        self.assertEqual((code, json.loads(raw)['rejected']), (200, []))
        self.assertEqual(read_fb(self.path)[a], before)

    def test_moving_to_ready_builds_the_pack(self):
        self.req('/api/save', {'__rev__': 1, JOBS[1]['id']: {'app': 'ready'}})
        self.assertEqual(self.builds, [1])

    def test_a_sandbox_copy_never_runs_the_real_reconcile(self):
        """副本(路徑不是他真正的 board-live.html)上存「可投遞」相關的改動,背景不准真的跑 reconcile:
        它動的是他的看板和可投遞包,不是那份副本,而且會把 CPU 吃滿、頁面一直輪詢等一個不相干的建置。"""
        ran = []
        with mock.patch.object(bs.subprocess, 'run', side_effect=lambda *a, **k: ran.append(a)):
            self._tb()                      # setUp 換掉之前、原本的 trigger_build
            time.sleep(0.2)
        self.assertEqual([a for a in ran if 'reconcile.py' in str(a)], [])   # 只看自己會起的那一支
        self.assertFalse(bs._build_state['running'])

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
        self.req('/api/save', {'__rev__': 1, JOBS[1]['id']: {'s': 'meh'}})
        r2 = self.rev()
        self.assertNotEqual(r1['rev'], r2['rev'])
        self.assertEqual(r1['gen'], r2['gen'])      # 只動標記,不必重拿 1MB 的職缺資料

    def test_rev_moves_when_a_script_writes_the_file(self):
        """cut_tailor 收尾、custom_queue、sync_sent 都直接寫檔,不經過這支伺服器。
        以前這種改動頁面永遠看不到,要他自己重整。"""
        r1 = self.rev()
        time.sleep(0.01)
        bd.set_fb(lambda fb: fb.__setitem__(JOBS[2]['id'], {'app': 'ready'}), live=self.path)
        r2 = self.rev()
        self.assertNotEqual(r1['rev'], r2['rev'])
        self.assertEqual(r1['gen'], r2['gen'])

    def test_gen_moves_when_jobs_data_is_reinstalled(self):
        r1 = self.rev()
        src = os.path.join(self.dir, 'merged.html')
        make_board(src, {}, jobs=JOBS + [{'id': 'https://ex.test/job/9', 'target': 'Job 9'}])
        time.sleep(0.01)
        bd.install(src, live=self.path, quiet=True)
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
        self.assertEqual(self.req('/api/save', {'__rev__': 1, a: {'app': 'ship', 'lang': 'en'}})[0], 200)
        self.assertEqual(self.req('/api/save', {'__rev__': 1, '__events__': sent})[0], 200)
        self.assertEqual(read_fb(self.path)[a]['sent_v'], 'en-general')
        # 頁面手上那份還沒拿到伺服器記的(沒帶 sent_v)、又改了語言:記過的那份留著
        self.req('/api/save', {'__rev__': 1, a: {'app': 'sent', 'lang': 'zh'}})
        self.assertEqual(read_fb(self.path)[a]['sent_v'], 'en-general')


class Prep(Http):
    """看板上的「跑準備區」。這裡的看板是臨時副本,伺服器一律跑 job_fake(不會派 agent)。"""

    KIND = 'prep'

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

    def wait_prep(self, until, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            st = self.rev()[self.KIND]
            if until(st):
                return st
            time.sleep(0.1)
        self.fail('等不到:%r' % st)

    def test_agent_cannot_start_it(self):
        code, _, _ = self.req('/api/run/' + self.KIND, {'mode': 'wide'}, ua='Mozilla/5.0 Claude/1.0')
        self.assertEqual(code, 403)
        self.assertIsNone(bs._PROC.get(self.KIND))

    def test_runs_once_and_the_page_can_see_the_result(self):
        bd.set_fb(lambda f: f.update({JOBS[1]['id']: {'app': 'prep'}, JOBS[2]['id']: {'app': 'prep'}}),
                  live=self.path)
        r1 = self.rev()
        self.assertEqual(self.req('/api/run/prep', {})[0], 200)
        self.assertTrue(self.rev()['prep']['running'])
        # 按第二下:cut_tailor 一啟動會殺掉上一個 worker,所以跑的時候不准再開一個
        self.assertEqual(self.req('/api/run/prep', {})[0], 409)
        st = self.wait_prep(lambda s: s.get('phase') == 'done')
        self.assertFalse(st['running'])
        self.assertEqual(st['ready'], 2)
        fb = read_fb(self.path)
        self.assertEqual(fb[JOBS[1]['id']]['app'], 'ready')
        self.assertNotEqual(self.rev()['rev'], r1['rev'])   # 頁面靠這個知道要重拿

    def test_nothing_to_run_says_why(self):
        self.assertEqual(self.req('/api/run/prep', {})[0], 200)
        st = self.wait_prep(lambda s: s.get('phase') == 'nothing')
        self.assertIn('沒有卡', st['msg'])

    def test_a_run_that_died_does_not_block_the_next(self):
        os.environ['JOB_FAKE_DIE'] = '1'
        self.req('/api/run/prep', {})
        st = self.wait_prep(lambda s: s.get('phase') == 'died')
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
        text = cut_tailor.prompt([('https://example.test/job', 'Engineer')], resumes=[])
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

    def test_techerr_keeps_the_mood_and_stage_it_had_so_it_can_be_put_back(self):
        """閘門標「出錯了」會蓋掉心情、清掉階段:兩個都另存(s0、app0),看板的「放回原處」照原樣放回;
        之前按過放回原處(live_ok)的,再被標出錯了,那個「他說沒壞」就不算了。"""
        import cut_tailor as ct
        fb = {'u': {'s': 'like', 'app': 'prep', 'live_ok': 1}, 'n': {}}
        ct._techerr(fb, 'u')
        ct._techerr(fb, 'n')
        self.assertEqual(fb['u'], {'s': 'techerr', 's0': 'like', 'app0': 'prep'})
        self.assertEqual(fb['n'], {'s': 'techerr'})
        ct._techerr(fb, 'u')                  # 再標一次:不把 techerr 存成原本的心情,原本存的也不丟
        self.assertEqual(fb['u'], {'s': 'techerr', 's0': 'like', 'app0': 'prep'})

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
        self.dir = tempfile.mkdtemp(prefix='pid-')
        # 一個不相干、活著的行程。自己一個行程群組:萬一保護被改壞,被整組砍的也只有它
        self.other = subprocess.Popen(['sleep', '30'], start_new_session=True)

    def tearDown(self):
        self.other.kill(); self.other.wait()
        shutil.rmtree(self.dir, ignore_errors=True)

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


class Research(Prep):
    """看板上的「🔎 找新職缺」:更深、更廣、指定方向。臨時副本上跑 job_fake(不會派 agent)。"""
    KIND = 'research'

    def test_runs_once_and_the_page_can_see_the_result(self):
        r1 = self.rev()
        self.assertEqual(self.req('/api/run/research', {'mode': 'dir', 'text': '遊戲反作弊', 'resume_text': '測試履歷'})[0], 200)
        st = self.rev()['research']
        self.assertTrue(st['running'])
        self.assertEqual((st['mode'], st['direction']), ('dir', '遊戲反作弊'))
        self.assertEqual(self.req('/api/run/research', {'mode': 'wide', 'resume_text': '測試履歷'})[0], 409)   # 同時只跑一輪
        st = self.wait_prep(lambda s: s.get('phase') == 'done')
        self.assertEqual(st['added'], 2)
        self.assertEqual(len(bd.parse(read(self.path))['data']['jobs']), len(JOBS) + 2)
        self.assertNotEqual(self.rev()['gen'], r1['gen'])    # 頁面靠這個知道要重拿職缺

    def test_bad_requests(self):
        self.assertEqual(self.req('/api/run/research', {'mode': 'x'})[0], 400)
        self.assertEqual(self.req('/api/run/research', {'mode': 'dir', 'text': '  '})[0], 400)
        self.assertIsNone(bs._PROC.get('research'))

    def test_nothing_to_run_says_why(self):
        pass    # 找缺不看準備區,沒有「沒東西可跑」這一種(更深沒有種子的情況在 converge 自己判)

    def test_a_run_that_died_does_not_block_the_next(self):
        os.environ['JOB_FAKE_DIE'] = '1'
        self.req('/api/run/research', {'mode': 'deep', 'resume_text': '測試履歷'})
        st = self.wait_prep(lambda s: s.get('phase') == 'died')
        self.assertFalse(st['running'])
        os.environ.pop('JOB_FAKE_DIE')
        self.assertEqual(self.req('/api/run/research', {'mode': 'deep', 'resume_text': '測試履歷'})[0], 200)


class AddJobs(Tmp):
    """找缺收尾只加新缺。以前整份換成開跑時的快照,一輪將近一小時裡準備區產的履歷文字、
    驗收結果都被倒回去。"""

    def test_only_new_jobs_are_added_everything_else_stays_current(self):
        live = self.path
        cur = [dict(JOBS[0], resume={'html': '<p>新產的履歷</p>'}, sum={'fit': '舊', 'cuts': {'c1': {'ok': 'yes'}}}),
               JOBS[1], JOBS[2]]
        make_board(live, {JOBS[0]['id']: {'s': 'like'}}, data={'jobs': cur, 'status': {'at': '新'}})
        src = os.path.join(self.dir, 'merged.html')   # 開跑時的快照 + 這輪的新缺
        make_board(src, {}, jobs=[dict(JOBS[0], sum={'fit': '新摘要'}), JOBS[1], JOBS[2],
                                  {'id': 'https://ex.test/job/9', 'target': 'Job 9'}])
        self.assertEqual(bd.add_jobs(src, live=live, quiet=True), 1)
        d = bd.parse(read(live))
        j0 = d['data']['jobs'][0]
        self.assertEqual(j0['resume'], {'html': '<p>新產的履歷</p>'})
        self.assertEqual(j0['sum'], {'fit': '新摘要', 'cuts': {'c1': {'ok': 'yes'}}})
        self.assertEqual(d['data']['status'], {'at': '新'})
        self.assertEqual(len(d['data']['jobs']), 4)
        self.assertEqual(json.loads(d['fb']), {JOBS[0]['id']: {'s': 'like'}})
        self.assertIn('>4<', d['thdr'])


class ResearchPipeline(unittest.TestCase):
    """找缺一輪:找 → 程式清洗 → 判 → 進板。agent 跟網路都換成假的。"""

    LIKED = 'https://jobs.lever.co/acme/1111'
    # 假的職缺頁:假的判斷寫的職稱、公司、引用都要在頁面原文裡(安檢門拿原文比,#317)
    PAGE = ' '.join(f'Engineer {i}' for i in range(1, 30)) + ' ACME 工作內容包含資安工程'

    def setUp(self):
        import research as rs, converge as cv
        self.rs, self.cv = rs, cv
        self.dir = tempfile.mkdtemp(prefix='rs-')
        self.path = os.path.join(self.dir, 'board.html')
        jobs = [{'id': self.LIKED, 'target': 'Security Engineer·Acme'},
                {'id': 'https://ex.test/job/2', 'target': 'Business Analyst·Foo'}]
        make_board(self.path, {self.LIKED: {'s': 'like', 'n': '很有趣,多找這種'},
                               'https://ex.test/job/2': {'s': 'dislike', 'n': '不要這種'}}, jobs=jobs)
        self.ledger = os.path.join(self.dir, 'ledger.jsonl')
        self.sums = os.path.join(self.dir, 'sums')
        self._dir = rs.DIR
        rs.DIR = self.dir

    def tearDown(self):
        self.rs.DIR = self._dir
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_deep_gives_companies_not_a_prebuilt_list(self):
        """更深給「他喜歡過的公司」,不給程式先打 API 列好的開缺清單。
        那個清單只認四個徵才系統,他喜歡過的公司有 19% 不在上面、整家被跳過;
        而 agent 本來就知道要用 API(它會自己去查 Greenhouse 的 jobs API)。"""
        import prefs
        fb, jobs = prefs_load(self.path)
        cs = prefs.cards(fb, jobs)
        l = self.rs.liked_company_list(cs)
        self.assertTrue(l and l[0]['company'], '沒有把喜歡過的公司交出去')
        p = self.rs.search_prompt('deep', '', cs, [], '', '/x', '/y', l)
        self.assertIn('使用者喜歡過的公司', p)
        self.assertIn('怎麼查由你決定', p)
        self.assertNotIn('程式已經從徵才系統全部列在下面', p)

    def test_search_goals_not_procedures(self):
        import prefs
        fb, jobs = prefs_load(self.path)
        cs = prefs.cards(fb, jobs)
        d = self.rs.search_prompt('dir', '遊戲反作弊,台灣或遠端', cs, [], '', '/x.json', '/x.md')
        self.assertIn('他說:「遊戲反作弊,台灣或遠端」', d)
        self.assertIn('怎麼找由你決定', d)
        self.assertNotIn('stdout', d)
        # 不叫它翻工具清單找別的搜尋工具;一次送好幾個關鍵字(以前一輪 72 步,好幾步花在找工具)
        self.assertIn('一次送好幾個關鍵字', d)
        # 他對舊卡的原話推到檔案、正文只留指標(那一塊佔以前那份 prompt 的六成)
        import tempfile as _t
        rd = _t.mkdtemp()
        files = self.rs.search_files(rd, cs, self.rs.liked_company_list(cs), '')
        d2 = self.rs.search_prompt('deep', '', cs, [], '', '/x', '/y', files=files)
        self.assertNotIn('Security Engineer·Acme', d2)
        self.assertIn('他說「很有趣,多找這種」', d2)      # 職稱只留在檔案裡當索引,正文只放他真的說過的話
        self.assertIn('Security Engineer · Acme', read(files['他喜歡過的職缺.md']))
        self.assertLess(len(d2), 6000, 'prompt 又長回去了')
        # 更廣:只給目標和他本人的材料,不再塞「板上各類職缺的數量」——那張表 52 行、
        # 一半是舊實驗殘留的標籤,是雜訊;而且它要 agent 去跟板上比對,等於又架一個支架。
        w = self.rs.search_prompt('wide', '', cs, [('資安', 3)], '', '/x', '/y')
        self.assertNotIn('資安:3 張', w)
        self.assertIn('想知道自己還能做什麼', w)
        self.assertIn('怎麼找由你決定', w)

    def test_agent_count_is_his_switch_not_mine(self):
        """「自己決定派幾隻」那句在派工的唯一入口加,所有 agent(找缺、判斷、準備區、代投)一起吃得到。
        他撥的是「所有 agent」,以前只有找缺那一段讀得到,是線沒接完。預設關著:一輪一隻是他的規則。"""
        import agent_run as ar, board_doc as bd
        self.assertNotIn('要派幾隻', ar.rules_for('main', board=self.path))   # 預設:一輪一隻
        bd.set_fb(lambda fb: fb.__setitem__('__agentfree__', 1), live=self.path, by='測試')
        on = ar.rules_for('main', board=self.path)
        self.assertIn('要派幾隻', on)
        self.assertIn('multi_agent_v1__spawn_agent', on)                     # agent 才給它自己的工具名
        self.assertNotIn('multi_agent_v1', ar.rules_for('alt', board=self.path))  # alt runtime 不承諾它沒有的

    def test_judge_knows_which_mode_found_it(self):
        """更廣找來的是他沒看過的職能,拿「像不像他喜歡過的」當判準等於用錯的尺。
        判斷那段以前連這張是哪種找法來的都不知道。"""
        import prefs
        fb, jobs = prefs_load(self.path)
        cs = prefs.cards(fb, jobs)
        b = [{'url': 'https://ex.test/x', 'title': 'X', 'company': 'C', 'jd': 'JD'}]
        deep = self.rs.judge_prompt(b, cs, '/o', 'deep')[0]
        wide = self.rs.judge_prompt(b, cs, '/o', 'wide')[0]
        self.assertNotIn('不要拿「像不像他喜歡過的」當判準', deep)
        self.assertIn('不要拿「像不像他喜歡過的」當判準', wide)

    def _stop_run(self, fake_agent, finishing, pending, **kw):
        """跑一輪找缺(假 agent、假抓頁),finishing() 為真 = 他按了停止;kw 照樣交給 research.run(time_up、tally…)。"""
        from unittest.mock import patch
        with patch.object(self.rs.cf, 'HOME', self.dir), \
             patch.object(self.rs.prefs, 'PREF', os.path.join(self.dir, 'prefs.md')), \
             patch.object(self.rs.prefs, 'resume', return_value='CV'), \
             patch.object(self.rs.prefs, 'hard_rules', return_value=''), \
             patch.object(self.rs.prefs, 'checked_resumes', return_value=[]), \
             patch.object(self.rs.prefs, 'resume_selection_signature', return_value='empty'), \
             patch.object(self.rs, 'search_files', return_value={}), \
             patch('feedback_dump.feedback_delta', return_value=('', 0)), \
             patch.object(self.rs.prefs, 'refresh_like'), \
             patch('page_fetch.fetch', side_effect=lambda url: page_result(url, self.PAGE)):
            return self.rs.run('deep', '', live=self.path, run_agent=fake_agent, ledger=self.ledger,
                               sums=self.sums, turned=os.path.join(self.dir, 'turned.jsonl'),
                               finishing=finishing, pending=pending, **kw)

    def _stop_agent(self, found, stop_at):
        """假 agent:找的時候交 found 張;stop_at=('search'|'judge', 第幾批) 那一刻他按了停止(agent 被停掉)。"""
        import json
        import agent_run as ar
        flag = {'stop': False, 'judged': 0}

        def fake_agent(prompt, outfile, _browser):
            if outfile.endswith('/note.out'):          # 整理偏好筆記的那隻(跟找缺同時跑)
                note_path = prompt.split('更新後偏好筆記請寫到 ', 1)[1].splitlines()[0]
                with open(note_path, 'w', encoding='utf-8') as f:
                    f.write('# 偏好筆記\n\n## 使用者自訂\n\n\n## Agent 假設\n\n')
                return None
            searching = '/search_' in outfile
            if searching:
                with open(outfile[:-4] + '.json', 'w', encoding='utf-8') as f:
                    json.dump(found, f, ensure_ascii=False)
                if stop_at == ('search', 0):
                    flag['stop'] = True
                    raise ar.AgentRunError([])
                with open(outfile[:-4] + '.md', 'w', encoding='utf-8') as f:
                    f.write('- 安全職務：符合角度地圖。')
                return None
            if stop_at == ('judge', flag['judged']):
                flag['stop'] = True
                raise ar.AgentRunError([])
            flag['judged'] += 1
            n = prompt.count('=== J')
            rows = [{'id': f'J{i}', 'title': f'Engineer {i}', 'company': 'ACME', 'keep': True, 'fit': 4,
                     'cite': [], 'why': 'ok', 'cat': self.rs.CATS[0], 'card': {'fit': 'ok'},
                     'reasons': [{'text': '職缺提及資安工程', 'citation': '工作內容包含資安工程', 'basis': 'JD 原文'}]}
                    for i in range(1, n + 1)]
            with open(outfile[:-4] + '.json', 'w', encoding='utf-8') as f:
                json.dump(rows, f, ensure_ascii=False)
            return None
        return fake_agent, (lambda: flag['stop'])

    def _found(self, n, tag):
        return [{'url': f'https://ex.test/{tag}/{i}', 'title': f'Engineer {i}', 'company': 'ACME',
                 'why': 'ok', 'via': 'fake', 'angle': '安全職務', 'jd_excerpt': '工作內容包含資安工程'}
                for i in range(n)]

    def test_stop_while_judging_keeps_finished_batches(self):
        """判到第二批時他按停止:第一批判完的照常進看板,第二批那兩張存起來下一輪先判。"""
        pending = os.path.join(self.dir, 'pending.json')
        agent, finishing = self._stop_agent(self._found(7, 'j'), ('judge', 1))
        added = self._stop_run(agent, finishing, pending)
        self.assertEqual(added, 5)
        self.assertEqual(len(self.rs.pending_take(pending)), 2)

    def test_stop_while_searching_saves_found_and_next_round_judges_them_first(self):
        """找到一半他按停止:已經寫進檔的三張存起來,不判、不加卡;下一輪一開始先判這三張。"""
        pending = os.path.join(self.dir, 'pending.json')
        agent, finishing = self._stop_agent(self._found(3, 's'), ('search', 0))
        self.assertEqual(self._stop_run(agent, finishing, pending), 0)
        self.assertEqual(self.rs.pending_count(pending), 3)
        agent, finishing = self._stop_agent([], None)
        self.assertEqual(self._stop_run(agent, finishing, pending), 3)
        self.assertEqual(self.rs.pending_count(pending), 0)

    def test_time_up_while_searching_judges_found_in_the_same_round(self):
        """找到一半時間到:agent 被停掉,已經寫進檔的三張這一輪就判完進看板,不存到下一輪;回報找到幾張。"""
        pending = os.path.join(self.dir, 'pending.json')
        agent, time_up = self._stop_agent(self._found(3, 't'), ('search', 0))
        tally = {}
        added = self._stop_run(agent, lambda: False, pending, minutes=15, time_up=time_up, tally=tally)
        self.assertEqual(added, 3)
        self.assertEqual(self.rs.pending_count(pending), 0)
        self.assertEqual(tally, {'found': 3, 'dropped': 0})
        with open(self.ledger, encoding='utf-8') as f:
            self.assertEqual(json.loads(f.read().splitlines()[-1])['stopped'], 'time')

    def test_time_up_with_nothing_written_adds_nothing(self):
        pending = os.path.join(self.dir, 'pending.json')
        agent, time_up = self._stop_agent([], ('search', 0))
        tally = {}
        self.assertEqual(self._stop_run(agent, lambda: False, pending, time_up=time_up, tally=tally), 0)
        self.assertEqual(tally, {'found': 0, 'dropped': 0})

    def test_no_time_limit_round_is_unchanged_and_agent_is_told_the_budget_only_when_set(self):
        pending = os.path.join(self.dir, 'pending.json')
        prompts = []
        agent, _ = self._stop_agent(self._found(2, 'u'), None)
        seen = lambda p, o, b: (prompts.append(p) if '/search_' in o else None) or agent(p, o, b)
        self.assertEqual(self._stop_run(seen, lambda: False, pending), 2)
        self.assertNotIn('分鐘找', prompts[0])
        prompts.clear()
        agent, _ = self._stop_agent(self._found(2, 'v'), None)
        seen = lambda p, o, b: (prompts.append(p) if '/search_' in o else None) or agent(p, o, b)
        self.assertEqual(self._stop_run(seen, lambda: False, pending, minutes=15, time_up=lambda: False), 2)
        self.assertIn('最多有 15 分鐘找', prompts[0])

    def test_repost_hint_reaches_fake_judge_and_is_kept_on_the_new_card(self):
        import json
        from unittest.mock import patch
        url = 'https://ex.test/reposted'
        candidate = {'url': url, 'title': 'Security Engineer', 'company': 'ACME',
                     'why': 'relevant', 'via': 'fake source', 'angle': '安全職務',
                     'jd_excerpt': '工作內容包含資安工程'}
        prompts, browser_flags = [], []

        def fake_agent(prompt, outfile, browser_required):
            prompts.append(prompt)
            browser_flags.append(browser_required)
            searching = '/search_' in outfile
            payload = ([candidate] if searching else [{
                'id': 'J1', 'title': 'Security Engineer', 'company': 'ACME',
                'keep': True, 'fit': 4, 'cite': [], 'why': 'relevant',
                'cat': self.rs.CATS[0], 'card': {'fit': 'relevant'},
                'reasons': [{'text': '職缺提及資安工程', 'citation': '工作內容包含資安工程',
                             'basis': 'JD 原文'}],
            }])
            with open(outfile[:-4] + '.json', 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
            if searching:
                with open(outfile[:-4] + '.md', 'w', encoding='utf-8') as f:
                    f.write('- 安全職務：符合角度地圖。')
            return None

        turned = os.path.join(self.dir, 'turned.jsonl')
        with patch.object(self.rs.cf, 'HOME', self.dir), \
             patch.object(self.rs.prefs, 'PREF', os.path.join(self.dir, 'prefs.md')), \
             patch.object(self.rs.prefs, 'resume', return_value='CV'), \
             patch.object(self.rs.prefs, 'hard_rules', return_value=''), \
             patch.object(self.rs.prefs, 'checked_resumes', return_value=[]), \
             patch.object(self.rs.prefs, 'resume_selection_signature', return_value='empty'), \
             patch.object(self.rs, 'search_files', return_value={}), \
             patch('feedback_dump.feedback_delta', return_value=('', 0)), \
             patch.object(self.rs.prefs, 'refresh_like'), \
             patch('page_fetch.fetch', side_effect=lambda url: page_result(
                 url, 'Security Engineer · ACME · 工作內容包含資安工程', posted_at='2026-09-01',
                 posted_source='test date')):
            added = self.rs.run('deep', '', live=self.path, run_agent=fake_agent,
                                ledger=self.ledger, sums=self.sums, turned=turned, limit=1)

        self.assertEqual(added, 1, '可能是重貼的職缺仍須進入正常判斷與加卡流程')
        self.assertEqual(browser_flags, [True, False], '搜尋可用瀏覽器,JD 判斷只能讀程式提供的文字')
        judge_prompt = next(prompt for prompt in prompts if '=== J1 ===' in prompt)
        self.assertIn('可能是重貼', judge_prompt)
        self.assertIn(self.LIKED, judge_prompt)
        self.assertIn('喜歡', judge_prompt)
        self.assertIn('很有趣,多找這種', judge_prompt)
        self.assertIn('不要只因為重貼就排除', judge_prompt)
        self.assertIn('工作內容包含資安工程', judge_prompt)
        self.assertNotIn('請直接用瀏覽器', judge_prompt)
        _, jobs = self.rs.prefs.load(self.path)
        new = next(j for j in jobs if j['id'] == url)
        self.assertEqual(new['src']['reposts'][0]['url'], self.LIKED)
        self.assertEqual(new['src']['reposts'][0]['mark'], '喜歡')
        self.assertEqual(new['posted_at'], '2026-09-01')
        self.assertEqual(new['posted_src'], 'test date')

    def test_judge_batches_run_together_when_he_lets_them(self):
        """判斷四批彼此不相干,沒有理由排隊(實測:排隊 37 分鐘,是整輪 84 分鐘裡最大的一塊)。"""
        import prefs
        fb, jobs = prefs_load(self.path)
        cs = prefs.cards(fb, jobs)
        cands = [{'url': 'https://ex.test/%d' % i, 'title': 'T%d' % i, 'company': 'C', 'jd': 'JD'} for i in range(10)]
        sent = []
        rd = tempfile.mkdtemp()
        self.rs.judge(cands, cs, rd, 'main', lambda *a: sent.append('一批'),
                      par=True, launch_all=lambda jobs_, m: sent.append('同時 %d 批' % len(jobs_)))
        self.assertEqual(sent, ['同時 2 批'], '撥了開關還是一批一批排隊')

    def test_unreadable_judgment_is_reported_but_not_remembered_as_rejected(self):
        from unittest.mock import patch
        import config as cf
        url = 'https://ex.test/unreadable'
        candidate = {'url': url, 'title': 'Search result title', 'company': 'Acme',
                     'angle': '安全職務'}
        drop = {'不是單一職缺頁': [], '板上已經有': [], '判過不送': [],
                '硬排除': [], '職缺已下架': []}
        turned = []
        raw_prefs = os.path.join(self.dir, 'prefs.md')
        preference_note = os.path.join(self.dir, 'preference-note.md')
        with open(raw_prefs, 'w', encoding='utf-8') as f:
            f.write('# 使用者逐張表態\n')

        def fake_search(*_args, **kwargs):
            return [candidate], '- 安全職務：符合指定方向。', []

        def fake_note(_files, _rd, note_out, _run_agent):
            with open(note_out, 'w', encoding='utf-8') as f:
                f.write('# 偏好筆記\n\n## 使用者自訂\n\n\n## Agent 假設\n\n')

        with patch.object(self.rs, 'run_search', side_effect=fake_search), \
             patch.object(self.rs, 'run_note', side_effect=fake_note), \
             patch.object(self.rs, 'clean', return_value=([candidate], drop)), \
             patch.object(self.rs, 'judge', return_value={url: {'readable': False, 'keep': False,
                                                                 'why': '頁面讀不到', 'bad_cite': []}}), \
             patch.object(self.rs, 'turned_add', side_effect=lambda rows, _path: turned.extend(rows)), \
             patch.object(self.rs, 'ledger_add'), \
             patch.object(self.rs, '_report') as report, \
             patch.object(self.rs.bd, 'set_data'), \
             patch.object(self.rs.prefs, 'refresh_like'), \
             patch.object(cf, 'PREFS', raw_prefs), \
             patch.object(cf, 'PREFERENCE_NOTE', preference_note, create=True), \
             patch.object(self.rs.prefs, 'PREF', preference_note):
            self.rs.run('dir', 'security', live=self.path, st=lambda *a, **k: None,
                        ledger=self.ledger, sums=self.sums, turned=os.path.join(self.dir, 'turned.jsonl'))
        msgs = [c.args[0] for c in report.call_args_list]
        self.assertTrue(any('沒有可確認的頁面職稱' in m for m in msgs), msgs)
        self.assertFalse(any('agent 沒有完成' in m for m in msgs), msgs)   # 這一輪有判完,不是整輪失敗
        self.assertEqual(turned, [])

    def test_five_default_research_skills_are_product_neutral(self):
        import config as cf

        names = tuple(item['file'] for item in cf.RESEARCH_SKILLS.values())
        self.assertEqual(set(cf.DEFAULTS['research']['skills']), set(cf.RESEARCH_SKILLS))
        root = os.path.join(os.path.dirname(self.rs.__file__), 'research_skills')
        for name in names:
            with self.subTest(name=name):
                with open(os.path.join(root, name), encoding='utf-8') as f:
                    skill = f.read()
                self.assertTrue(skill.strip())
                self.assertNotRegex(skill, r'(?m)(?:^|\s)/[^/\s]+/')
                self.assertNotRegex(skill, r'(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b')
        common_path = os.path.join(root, cf.RESEARCH_SKILLS['common']['file'])
        with open(common_path, encoding='utf-8') as f:
            common = f.read()
        self.assertIn('不設比例', common)
        self.assertIn('不要自創使用者沒說過的禁令或排除條件', common)
        self.assertIn('使用者自訂條目與任何新表態衝突', common)
        self.assertIn('列在交件筆記，請使用者決定是否修改', common)

    def test_find_round_uses_selected_and_default_skills_and_judges_every_candidate(self):
        from copy import deepcopy
        from unittest.mock import patch
        import config as cf
        import re

        home = os.path.join(self.dir, 'home')
        custom_dir = os.path.join(home, 'custom', 'skills')
        os.makedirs(custom_dir)
        task_skills = {
            'common': 'custom/skills/common.md',
            'deep': 'custom/skills/deep.md',
            'wide': '',
            'dir': 'custom/skills/direction.md',
            'judge': 'custom/skills/judge.md',
        }
        markers = {
            'common': 'CUSTOM-COMMON',
            'deep': 'CUSTOM-DEEP',
            'direction': 'CUSTOM-DIRECTION',
            'judge': 'CUSTOM-JUDGE',
        }
        for name, marker in markers.items():
            with open(os.path.join(custom_dir, name + '.md'), 'w', encoding='utf-8') as f:
                f.write(marker)

        settings = deepcopy(cf.DEFAULTS)
        settings.setdefault('research', {})['skills'] = task_skills
        found = {'deep': 25, 'wide': 1, 'dir': 1}
        search_prompts, judge_prompts = {}, []

        def fake_agent(prompt, outfile, _browser_required):
            name = os.path.basename(outfile)
            result_path = os.path.splitext(outfile)[0] + '.json'
            if name == 'note.out':                     # 整理偏好筆記的那隻(跟找缺同時跑)
                note_path = re.search(r'更新後偏好筆記請寫到\s+([^\n]+)', prompt).group(1).strip()
                delta_ids = re.findall(r'卡片代號=([^\n｜]+)', note_material(prompt, '新表態'))
                with open(note_path, 'w', encoding='utf-8') as f:
                    f.write('# 偏好筆記\n\n## 使用者自訂\n\n\n## Agent 假設\n\n'
                            + '\n'.join(f'- 探索平台｜出處卡片代號={i}' for i in delta_ids)
                            + '\n\n### 新表態逐張核對\n'
                            + '\n'.join(f'- 卡片代號={i}：來源假設=探索平台' for i in delta_ids) + '\n')
                return
            if name.startswith('search_'):
                mode = name[len('search_'):-len('.out')]
                search_prompts[mode] = prompt
                rows = [{'url': f'https://jobs.lever.co/acme/issue41-{mode}-{i}',
                         'title': f'Candidate {mode} {i}', 'company': 'Acme',
                         'why': 'fake', 'via': 'fake', 'angle': '探索平台職務',
                         'jd_excerpt': '實際 JD 片段'}
                        for i in range(found[mode])]
                with open(os.path.splitext(outfile)[0] + '.md', 'w', encoding='utf-8') as f:
                    f.write('- 探索平台職務：符合本輪方向與角度地圖。')
            else:
                judge_prompts.append(prompt)
                ids = re.findall(r'^=== J(\d+) ===$', prompt, re.M)
                rows = [{'id': f'J{i}', 'title': f'Confirmed {i}', 'company': 'Acme',
                         'keep': False, 'fit': 3, 'cite': [], 'why': 'fake', 'cat': '其他',
                         'reasons': [{'text': '依據職缺原文', 'citation': '實際 JD 片段',
                                      'basis': 'JD 原文'}]}
                        for i in ids]
            with open(result_path, 'w', encoding='utf-8') as f:
                json.dump(rows, f, ensure_ascii=False)

        prefs_file = os.path.join(home, 'prefs.md')
        preference_file = os.path.join(home, 'preference-note.md')
        with open(prefs_file, 'w', encoding='utf-8') as f:
            f.write('# 使用者逐張表態\n')
        with patch.object(cf, 'HOME', home), patch.object(cf, 'C', settings), \
                patch.object(cf, 'PREFS', prefs_file), \
                patch.object(cf, 'PREFERENCE_NOTE', preference_file, create=True), \
                patch.object(cf, 'RESUMES', {}), \
                patch.object(self.rs.prefs, 'PREF', preference_file), \
                patch.object(self.rs.prefs, 'refresh_like'), \
                patch('page_fetch.fetch', side_effect=lambda url: page_result(url)), \
                patch.dict(os.environ, {'AGENT_BOARD': self.path}):
            for mode, direction in (('deep', ''), ('wide', ''), ('dir', '遊戲反作弊,台灣或遠端')):
                self.rs.run(mode, direction, live=self.path, st=lambda *a, **k: None,
                            run_agent=fake_agent, ledger=self.ledger, sums=self.sums,
                            turned=os.path.join(self.dir, 'turned.jsonl'))

        self.assertEqual(set(search_prompts), {'deep', 'wide', 'dir'})
        deep = search_prompts['deep']
        self.assertIn('CUSTOM-COMMON', deep)
        self.assertIn('CUSTOM-DEEP', deep)
        self.assertLess(deep.index('CUSTOM-COMMON'), deep.index('CUSTOM-DEEP'))
        self.assertLess(deep.index('CUSTOM-DEEP'), deep.index('一頁履歷原文'))
        self.assertIn('CUSTOM-COMMON', search_prompts['wide'])
        self.assertIn('# 更廣', search_prompts['wide'])  # 空白使用產品預設
        self.assertIn('CUSTOM-COMMON', search_prompts['dir'])
        self.assertTrue(search_prompts['dir'].startswith('他說:「遊戲反作弊,台灣或遠端」'))
        self.assertIn('CUSTOM-DIRECTION', search_prompts['dir'])
        self.assertEqual(len(judge_prompts), 7)  # 25 + 1 + 1 張候選分成 7 批
        self.assertEqual(sum(p.count('=== J') for p in judge_prompts), 27)
        self.assertTrue(all('CUSTOM-JUDGE' in p for p in judge_prompts))
        self.assertTrue(all('把結果寫成一個 JSON 檔' in p for p in judge_prompts))
        records = [json.loads(line) for line in open(self.ledger, encoding='utf-8')]
        self.assertEqual([r['judged'] for r in records[-3:]], [25, 1, 1])

    def test_checked_reasons_rejects_citations_missing_from_materials(self):
        valid, bad = self.rs.checked_reasons(
            [{'text': '職缺提到平台維運', 'citation': '平台維運', 'basis': 'JD 原文'}],
            {'JD 原文': ['工作內容包含平台維運'], '使用者原話': [], '偏好筆記': []})
        self.assertFalse(bad)
        self.assertEqual(valid[0]['citation'], '平台維運')

        note_reason, bad = self.rs.checked_reasons(
            [{'text': '偏好筆記列出平台經驗', 'citation': '具備平台經驗',
              'basis': '偏好筆記'}],
            {'JD 原文': [], '使用者原話': [], '偏好筆記': ['假設：具備平台經驗']})
        self.assertFalse(bad)
        self.assertEqual(note_reason[0]['basis'], '偏好筆記')

        invalid, bad = self.rs.checked_reasons(
            [{'text': '職缺要求管理五百台主機', 'citation': '管理五百台主機',
              'basis': 'JD 原文'}],
            {'JD 原文': ['工作內容包含平台維運'], '使用者原話': [], '偏好筆記': []})
        self.assertTrue(bad)
        self.assertEqual(invalid[0]['citation'], '管理五百台主機')

    def test_incomplete_feedback_note_does_not_advance_checkpoint(self):
        from copy import deepcopy
        from unittest.mock import patch
        import config as cf
        import feedback_dump
        import re

        home = os.path.join(self.dir, 'coverage-home')
        os.makedirs(home)
        preference_file = os.path.join(home, 'preference-note.md')
        legacy_file = os.path.join(home, 'prefs.md')
        with open(legacy_file, 'w', encoding='utf-8') as f:
            f.write('# 使用者逐張表態\n')
        reports = []

        def fake_agent(prompt, outfile, _browser_required):
            result_path = os.path.splitext(outfile)[0] + '.json'
            if os.path.basename(outfile) == 'note.out':   # 整理偏好筆記的那隻(跟找缺同時跑)
                note_path = re.search(
                    r'更新後偏好筆記請寫到\s+([^\n]+)', prompt).group(1).strip()
                delta_ids = re.findall(r'卡片代號=([^\n｜]+)', note_material(prompt, '新表態'))
                first = delta_ids[0]
                with open(note_path, 'w', encoding='utf-8') as f:
                    f.write('# 偏好筆記\n\n## 使用者自訂\n\n\n## Agent 假設\n\n'
                            f'- 探索平台｜出處卡片代號={first}\n\n'
                            '### 新表態逐張核對\n'
                            f'- 卡片代號={first}：來源假設=探索平台\n')
                return
            if os.path.basename(outfile).startswith('search_'):
                with open(result_path, 'w', encoding='utf-8') as f:
                    json.dump([], f)
                with open(os.path.splitext(outfile)[0] + '.md', 'w', encoding='utf-8') as f:
                    f.write('本輪沒有新候選。')
                return
            with open(result_path, 'w', encoding='utf-8') as f:
                json.dump([], f)

        settings = deepcopy(cf.DEFAULTS)
        with open(os.path.join(home, 'prefs.json'), 'w', encoding='utf-8') as f:
            f.write('{}')
        with patch.object(cf, 'HOME', home), patch.object(cf, 'C', settings), \
                patch.object(cf, 'PREFS', legacy_file), patch.object(cf, 'SUMS', self.sums), \
                patch.object(cf, 'PREFERENCE_NOTE', preference_file, create=True), \
                patch.object(self.rs.prefs, 'PREF', preference_file), \
                patch.object(self.rs.prefs, 'refresh_like'), \
                patch.object(self.rs, '_report', side_effect=lambda msg, *_: reports.append(msg)), \
                patch('page_fetch.fetch', side_effect=lambda url: page_result(url)), \
                patch.dict(os.environ, {'AGENT_BOARD': self.path}):
            self.rs.run('deep', '', live=self.path, st=lambda *a, **k: None,
                        run_agent=fake_agent, ledger=self.ledger, sums=self.sums,
                        turned=os.path.join(self.dir, 'turned.jsonl'))

        checkpoint = feedback_dump.feedback_state_path(preference_file)
        self.assertFalse(os.path.exists(checkpoint))
        self.assertTrue(any('新表態未交代' in msg for msg in reports))

    def test_find_round_uses_new_feedback_angles_and_protected_preferences(self):
        from copy import deepcopy
        from unittest.mock import patch
        import config as cf
        import card
        import feedback_dump
        import re

        home = os.path.join(self.dir, 'home')
        os.makedirs(home)
        preference_file = os.path.join(home, 'preference-note.md')
        legacy_file = os.path.join(home, 'prefs.md')
        cloud_one = 'https://ex.test/cloud-one'
        cloud_two = 'https://ex.test/cloud-two'
        cloud_unseen = 'https://ex.test/cloud-unseen'
        recent = 'https://ex.test/recent-feedback'
        jobs = [
            {'id': cloud_one, 'target': 'Cloud Platform Engineer·CloudCo',
             'src': {'angle': '雲端基礎設施'},
             'sum': {'co': '代管雲端資料平台', 'bar': '三年平台維運', 'fit': '維護資料服務'}},
            {'id': cloud_two, 'target': 'Cloud Operations Engineer·CloudCo',
             'src': {'angle': '雲端基礎設施'},
             'sum': {'co': '代管雲端資料平台', 'bar': '三年平台維運', 'fit': '維護資料服務'}},
            {'id': cloud_unseen, 'target': 'Cloud Support Engineer·CloudCo',
             'src': {'angle': '雲端基礎設施'},
             'sum': {'co': '代管雲端資料平台', 'bar': '三年平台維運', 'fit': '維護資料服務'}},
            {'id': recent, 'target': 'Data Engineer·FintechCo',
             'src': {'angle': '金融科技'}, 'jd': '近期職缺 JD 全文：即時支付風險資料平台。',
             'sum': {'co': '即時支付服務', 'bar': '熟悉資料平台', 'fit': '風險資料'}},
        ]
        fb = {
            cloud_one: {'s': 'like', 'n': '我喜歡維護雲端平台'},
            cloud_two: {'s': 'dislike', 'n': '我不想只做值班'},
        }
        make_board(self.path, fb, jobs=jobs)
        with open(legacy_file, 'w', encoding='utf-8') as f:
            f.write('# 求職偏好\n\n## 硬規則\n\n不要外派\n\n'
                    '<!-- 以下由 feedback_dump.py 自動更新 -->\n舊表態\n'
                    '<!-- feedback_dump.py:end -->\n')
        os.makedirs(self.sums, exist_ok=True)
        for job in jobs:
            with open(os.path.join(self.sums, card.card_id_from_url(job['id']) + '.json'),
                      'w', encoding='utf-8') as f:
                json.dump(job['sum'], f, ensure_ascii=False)

        settings = deepcopy(cf.DEFAULTS)
        fake_round = [0]
        search_prompts, judge_prompts, reports, deltas, note_prompts = [], [], [], [], []
        raw_dump, summary_dump = '', ''

        note_round = [0]

        def fake_agent(prompt, outfile, _browser_required):
            result_path = os.path.splitext(outfile)[0] + '.json'
            if os.path.basename(outfile) == 'note.out':   # 整理偏好筆記的那隻(跟找缺同時跑)
                note_round[0] += 1
                note_path = re.search(r'更新後偏好筆記請寫到\s+([^\n]+)', prompt).group(1).strip()
                custom = '不要外派' if note_round[0] == 1 else '不要外派（agent 嘗試修改）'
                note_prompts.append(prompt)
                delta = note_material(prompt, '新表態')
                deltas.append(delta)
                delta_ids = re.findall(r'卡片代號=([^\n｜]+)', delta)
                names = {
                    cloud_one: '喜歡雲端平台',
                    cloud_two: '不想只做值班',
                    recent: '最近也關注即時支付',
                }
                assumptions = '\n'.join(
                    f'- {names.get(card_id, "新表態")}｜出處：卡片代號={card_id}；支持 1；反例 0'
                    for card_id in delta_ids)
                tracking = '\n'.join(
                    f'- 卡片代號={card_id}：來源假設={names.get(card_id, "新表態")}'
                    for card_id in delta_ids)
                with open(note_path, 'w', encoding='utf-8') as f:
                    f.write('# 偏好筆記\n\n## 使用者自訂\n\n' + custom +
                            '\n\n## Agent 假設\n\n' + assumptions +
                            '\n\n### 新表態逐張核對\n' + tracking + '\n')
                return
            if os.path.basename(outfile).startswith('search_'):
                fake_round[0] += 1
                n = fake_round[0]
                search_prompts.append(prompt)
                if n == 1:
                    rows = []
                    note = '本輪沒有新候選。'
                else:
                    rows = [{'url': f'https://jobs.lever.co/cloudco/issue42-{i}',
                             'title': f'Candidate deep {i}', 'company': 'CloudCo',
                             'why': 'fake', 'via': 'fake', 'angle': '雲端基礎設施',
                             'jd_excerpt': '平台維運'}
                            for i in range(25)]
                    note = ('## 角度選擇理由\n'
                            '- 雲端基礎設施：地圖已有三張，還有一張未看；沿用舊角度繼續探索。')
                with open(result_path, 'w', encoding='utf-8') as f:
                    json.dump(rows, f, ensure_ascii=False)
                with open(os.path.splitext(outfile)[0] + '.md', 'w', encoding='utf-8') as f:
                    f.write(note)
                return

            judge_prompts.append(prompt)
            titles = re.findall(r'^=== J\d+ ===\n職缺:([^·\n]+)', prompt, re.M)
            rows = []
            for title in titles:
                m = re.search(r'Candidate deep (\d+)', title)
                candidate = int(m.group(1)) if m else -1
                row = {'id': f'J{len(rows) + 1}', 'title': title, 'company': 'CloudCo',
                       'keep': candidate == 0, 'fit': 4, 'cite': [], 'why': '根據實際 JD',
                       'cat': '工程',
                       'card': {'fit': '履歷有平台維運經驗', 'co': '代管雲端資料平台',
                                'loc': '遠端', 'deadline': '無', 'salary': '未公開',
                                'bar': '三年平台維運', 'posted': '無', 'ammo': '直投'},
                       'reasons': [{'text': 'JD 要求平台維運',
                                    'citation': '平台維運', 'basis': 'JD 原文'}]}
                if candidate == 1:
                    row['reasons'] = [{'text': '跟使用者表態相反', 'basis': '使用者原話'}]
                elif candidate == 2:
                    row['reasons'] = [{'text': '公司資訊不足', 'citation': '偏好筆記中的來源'}]
                rows.append(row)
            with open(result_path, 'w', encoding='utf-8') as f:
                json.dump(rows, f, ensure_ascii=False)

        with open(os.path.join(home, 'prefs.json'), 'w', encoding='utf-8') as f:
            f.write('{}')
        with patch.object(cf, 'HOME', home), patch.object(cf, 'C', settings), \
                patch.object(cf, 'PREFS', legacy_file), patch.object(cf, 'SUMS', self.sums), \
                patch.object(cf, 'PREFERENCE_NOTE', preference_file, create=True), \
                patch.object(self.rs.prefs, 'PREF', preference_file), \
                patch.object(self.rs.prefs, 'refresh_like'), \
                patch.object(self.rs, '_report', side_effect=lambda msg, *_: reports.append(msg)), \
                patch('page_fetch.fetch', side_effect=lambda url: page_result(
                    url, 'Candidate deep ' + url.rsplit('-', 1)[-1] + ' · CloudCo · 平台維運 實際 JD 片段')), \
                patch.dict(os.environ, {'AGENT_BOARD': self.path}):
            summary_dump = feedback_dump.build(self.path, only_ids=[cloud_one])
            self.rs.run('deep', '', live=self.path, st=lambda *a, **k: None,
                        run_agent=fake_agent, ledger=self.ledger, sums=self.sums,
                        turned=os.path.join(self.dir, 'turned.jsonl'))
            bd.set_fb(lambda current: current.update({recent: {'s': 'like', 'n': '最近想做支付風險資料'}}),
                      live=self.path, by='看板')
            self.rs.run('deep', '', live=self.path, st=lambda *a, **k: None,
                        run_agent=fake_agent, ledger=self.ledger, sums=self.sums,
                        turned=os.path.join(self.dir, 'turned.jsonl'))
            raw_dump = feedback_dump.build(self.path)

        self.assertEqual(len(search_prompts), 2)
        self.assertEqual(len(deltas), 2)
        # 新表態只給整理筆記那隻(原文直接在 prompt 裡);找的那隻不用讀它
        self.assertTrue(all('新表態' not in re.sub(r'「1. 整理」.*?\n', '', p.split('他是誰')[1]) for p in search_prompts))
        self.assertIn(cloud_one, summary_dump)
        self.assertIn('卡片摘要=公司在做什麼=代管雲端資料平台', summary_dump)
        delta = deltas[1]
        self.assertIn('近期職缺 JD 全文：即時支付風險資料平台。', delta)
        self.assertIn('最近想做支付風險資料', delta)
        self.assertNotIn('Cloud Platform Engineer', delta)
        self.assertIn('我喜歡維護雲端平台', raw_dump)
        self.assertIn('我不想只做值班', raw_dump)
        self.assertIn('最近想做支付風險資料', raw_dump)
        angle_path = re.search(r'- 角度地圖:\s*([^\n]+)', search_prompts[1]).group(1).strip()
        angle_map = read(angle_path)
        self.assertIn('雲端基礎設施：送過 3、喜歡 1、不喜歡 1、還沒看 1', angle_map)
        self.assertTrue(judge_prompts)
        self.assertIn('公司在做什麼：代管雲端資料平台', judge_prompts[0])
        self.assertIn('門檻：三年平台維運', judge_prompts[0])
        self.assertIn('偏好筆記', judge_prompts[0])
        self.assertIn('請使用者決定是否修改', note_prompts[1])

        records = [json.loads(line) for line in open(self.ledger, encoding='utf-8')]
        self.assertEqual(records[-1]['judged'], 25)
        self.assertEqual(records[-1]['bad_evidence'], 2)
        self.assertEqual(records[-1]['angle_reasons'], [
            {'angle': '雲端基礎設施', 'reason': '地圖已有三張，還有一張未看；沿用舊角度繼續探索。'}])
        self.assertGreaterEqual(sum(len(re.findall(r'^=== J\d+ ===$', p, re.M))
                                    for p in judge_prompts), 25)
        board_jobs = bd.parse(read(self.path))['data']['jobs']
        added = next(j for j in board_jobs if j.get('id') == 'https://jobs.lever.co/cloudco/issue42-0')
        self.assertEqual(added['src']['angle'], '雲端基礎設施')
        self.assertEqual(added['src']['reasons'][0]['basis'], 'JD 原文')
        note = read(preference_file)
        self.assertIn('## 使用者自訂\n\n不要外派', note)
        self.assertNotIn('不要外派（agent 嘗試修改）', note)
        self.assertIn('最近也關注即時支付', note)
        checkpoint = json.loads(read(feedback_dump.feedback_state_path(preference_file)))
        self.assertTrue(checkpoint['organized_at'])
        self.assertTrue(any('使用者自訂' in msg for msg in reports))
        self.assertTrue(any('引用' in msg for msg in reports))


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


class ResumeSelection(Tmp):
    def test_find_judgment_reads_checked_resumes_and_rejects_invalid_picks(self):
        import json
        import config as cf
        import research as rs
        import ship
        from unittest.mock import patch
        import agent_run as ar

        zh_file = os.path.join(self.dir, 'zh.pdf')
        en_file = os.path.join(self.dir, 'en.pdf')
        disabled_file = os.path.join(self.dir, 'disabled.pdf')
        for path, text in ((zh_file, 'resume zh'), (en_file, 'resume en'),
                           (disabled_file, 'resume disabled')):
            with open(path, 'wb') as f:
                f.write(text.encode())
        resumes = [
            {'id': 'zh-only', 'name': '中文履歷', 'enabled': True, 'when': '資料職缺',
             'files': {'zh': zh_file}},
            {'id': 'en-only', 'name': 'English resume', 'enabled': True, 'when': 'English roles',
             'files': {'en': en_file}},
            {'id': 'unchecked', 'name': 'Unchecked', 'enabled': False, 'when': '',
             'files': {'zh': disabled_file}},
        ]
        url = 'https://ex.test/job/resume-choice'
        candidate = {'url': url, 'title': 'Engineer', 'company': 'Example'}
        prompts = []

        def runner_for(choice):
            def run_agent(prompt, outfile, _model):
                prompts.append(prompt)
                payload = [{'id': 'J1', 'title': 'Engineer', 'company': 'Example',
                            'keep': True, 'fit': 4, 'cite': [], 'why': '職缺符合方向',
                            'cat': rs.CATS[0], 'resume': choice[0], 'lang': choice[1],
                            'pick_why': '履歷中的經驗符合職缺要求',
                            'card': {'fit': '符合', 'ammo': '直投'}}]
                with open(outfile[:-4] + '.json', 'w', encoding='utf-8') as f:
                    json.dump(payload, f, ensure_ascii=False)
                return ar.AgentResult('completed', 0, 123)
            return run_agent

        with patch.object(cf, 'LANGS', ['zh', 'en']), patch.object(
                cf, 'RESUMES', {item['id']: item for item in resumes}):
            result = rs.judge([dict(candidate)], [], self.dir, 'main',
                              runner_for(('zh-only', 'zh')), resumes=resumes)
            prompt = prompts[-1]
            self.assertIn(zh_file, prompt)
            self.assertIn(en_file, prompt)
            self.assertNotIn(disabled_file, prompt)
            self.assertNotIn('【他的一頁履歷】', prompt)
            entry = rs.job_entry(candidate, result[url], {})
            self.assertEqual(entry['resume']['recommend'], 'zh-only')
            self.assertEqual(entry['resume']['lang'], 'zh')
            self.assertEqual(entry['resume']['pick_why'], '履歷中的經驗符合職缺要求')
            self.assertTrue(entry['resume']['selection_signature'])

            for choice in (('unchecked', 'zh'), ('zh-only', 'en')):
                rejected = rs.judge([dict(candidate)], [], self.dir, 'main',
                                    runner_for(choice), resumes=resumes)
                unpicked = rs.job_entry(candidate, rejected[url], {})
                self.assertNotIn('recommend', unpicked['resume'])
                self.assertNotIn('lang', unpicked['resume'])
                self.assertEqual(ship.resolve(unpicked, {url: {}})[0], '')

    def test_find_completes_without_a_checked_resume_and_stores_no_pick(self):
        import json
        import config as cf
        import research as rs
        from unittest.mock import patch
        import agent_run as ar

        url = 'https://ex.test/job/no-resume'
        candidate = {'url': url, 'title': 'Engineer', 'company': 'Example', 'jd': 'Engineer at Example',
                     'page_status': 'ok'}
        prompts = []

        def run_agent(prompt, outfile, _model):
            prompts.append(prompt)
            with open(outfile[:-4] + '.json', 'w', encoding='utf-8') as f:
                json.dump([{'id': 'J1', 'title': 'Engineer', 'keep': True,
                            'fit': 4, 'why': '符合方向', 'cat': rs.CATS[0],
                            'resume': 'unchecked', 'lang': 'zh',
                            'pick_why': '這個不應被採用'}], f)
            return ar.AgentResult('completed', 0, 123)

        disabled = [{'id': 'unchecked', 'enabled': False, 'files': {'zh': '/missing.pdf'}}]
        with patch.object(cf, 'LANGS', ['zh']), patch.object(
                cf, 'RESUMES', {'unchecked': disabled[0]}):
            judged = rs.judge([dict(candidate)], [], self.dir, 'main', run_agent,
                              resumes=disabled)
            card_entry = rs.job_entry(candidate, judged[url], {})

        self.assertTrue(judged[url]['keep'])
        self.assertIn('目前沒有已勾選且可挑的履歷', prompts[0])
        self.assertNotIn('"resume":', prompts[0])
        self.assertNotIn('recommend', card_entry['resume'])
        self.assertNotIn('lang', card_entry['resume'])

    def test_judge_placeholder_company_keeps_the_search_company(self):
        # 頁面沒抓到時 agent 會在公司欄寫「查無」;不能蓋掉找缺時已經知道的公司名
        import json
        import research as rs
        import agent_run as ar

        url = 'https://ex.test/job/placeholder'

        def run_agent(prompt, outfile, _model):
            with open(outfile[:-4] + '.json', 'w', encoding='utf-8') as f:
                json.dump([{'id': 'J1', 'title': '查無', 'company': '查無', 'keep': True,
                            'fit': 4, 'why': '符合方向', 'cat': rs.CATS[0]}], f, ensure_ascii=False)
            return ar.AgentResult('completed', 0, 123)

        candidate = {'url': url, 'title': 'QA', 'company': '範例科技'}
        res = rs.judge([candidate], [], self.dir, 'main', run_agent, resumes=[])
        self.assertEqual(candidate['company'], '範例科技')
        self.assertEqual(candidate['title'], 'QA')
        self.assertFalse(res[url]['keep'])        # 沒確認到頁面職稱:不送到他眼前,列進沒讀到的
        self.assertFalse(res[url]['readable'])


class ReapplySync(Tmp):
    """再投一次的卡回到可投遞:104 應徵紀錄裡那筆是上一次的,不能把它補標回已投遞;他寫的原因也不能被蓋掉。"""

    def test_old_104_record_does_not_mark_a_reapply_card_sent(self):
        import sync_sent as ss, datetime
        a = 'https://www.104.com.tw/job/abc12'
        b = 'https://www.104.com.tw/job/def34'
        today = datetime.date.today()
        old = (today - datetime.timedelta(days=20)).isoformat()
        make_board(self.path, {a: {'app': 'ship', 'n': '他寫的', 'tries': [{'sent_at': old, 'oc': 'rej'}]},
                               b: {'app': 'ship', 'n': '他寫的'}},
                   jobs=[{'id': a, 'target': 'A'}, {'id': b, 'target': 'B'}])
        d20 = today - datetime.timedelta(days=20)
        recs = [{'id': 'abc12', 'title': 'A', 'applied_at': d20.strftime('%m/%d 10:00')},
                {'id': 'def34', 'title': 'B', 'applied_at': d20.strftime('%m/%d 10:00')}]
        rf = os.path.join(self.dir, 'recs.json')
        with open(rf, 'w', encoding='utf-8') as fh: json.dump(recs, fh)
        ss.main(['--records', rf, '--apply', '--board', self.path])
        fb = read_fb(self.path)
        self.assertEqual(fb[a]['app'], 'ship')          # 上一次的紀錄,不算這一次
        self.assertEqual(fb[b]['app'], 'sent')          # 沒有歷史的照舊補標
        self.assertEqual(fb[b]['n'], '他寫的')           # 原因欄是他的字,不寫程式訊息

    def test_a_record_he_backed_out_is_not_pulled_back_and_platform_date_wins(self):
        """平台對帳:移除的卡照樣記成已送出;他退回那一筆之後同一筆不再拉回(修正 22)。
        agent 送出的卡,投遞日以平台紀錄優先(額外抓到 3)。"""
        import sync_sent as ss, datetime
        import delivery_state as ds
        a = 'https://www.104.com.tw/job/abc12'
        b = 'https://www.104.com.tw/job/def34'
        today = datetime.date.today()
        d3 = today - datetime.timedelta(days=3)
        make_board(self.path, {a: {'app': 'ship', 'rm': 1},
                               b: {'app': 'sent', 'ds': 'sent', 'sent_by': 'agent', 'sent_at': today.isoformat()}},
                   jobs=[{'id': a, 'target': 'A'}, {'id': b, 'target': 'B'}])
        recs = [{'id': 'abc12', 'title': 'A', 'applied_at': d3.isoformat()},
                {'id': 'def34', 'title': 'B', 'applied_at': d3.isoformat()}]
        ss.sync(self.path, recs)
        fb = read_fb(self.path)
        self.assertEqual((fb[a]['app'], fb[a].get('rm'), fb[a]['sent_by']), ('sent', None, 'platform'))
        self.assertEqual(fb[b]['sent_at'], d3.isoformat())          # 平台上的日期
        bd.set_fb(lambda f: ds.fire(f, a, 'back', to='ship'), live=self.path)
        self.assertEqual(ss.sync(self.path, recs), '')              # 同一筆不再拉回
        self.assertEqual(ds.state(read_fb(self.path)[a]), 'todo')


class PrepSkips(Tmp):
    """跑準備區沒產出的卡。網站擋程式時,活的職缺只是抓不到 JD,
    不能因此移出準備區、標成技術錯誤(那會蓋掉原本的喜歡/還好)。"""

    def test_skipped_cards_stay_in_prep_with_marks_untouched(self):
        import cut_tailor as ct
        a, b = JOBS[0]['id'], JOBS[1]['id']
        make_board(self.path, {a: {'s': 'like', 'app': 'prep'}, b: {'s': 'meh', 'app': 'prep'}})
        out = os.path.join(self.dir, 'trial')
        for u, f in ((a, {'skip': True, 'reason': '抓不到 JD:104 擋程式'}), (b, {'variant': 'general', 'lang': 'zh'})):
            os.makedirs(os.path.join(out, ct.jid(u)))
            with open(os.path.join(out, ct.jid(u), 'fill.json'), 'w', encoding='utf-8') as fh:
                json.dump(f, fh, ensure_ascii=False)
        moved = ct._apply_stages([(a, 'A'), (b, 'B')], self.path, out_dir=out)
        fb = read_fb(self.path)
        self.assertEqual(fb[a], {'s': 'like', 'app': 'prep'})       # 一個字都沒動
        self.assertEqual(fb[b], {'s': 'meh', 'app': 'ready'})       # 產出履歷的照舊推到待你決定
        self.assertEqual(moved, {'ready': 1, 'skipped': 1, 'closed': 0})

    def test_agent_says_closed_card_leaves_prep_as_dead(self):
        """agent 讀過頁面判斷「職缺已關」:程式照判斷標下架、移出準備區,不再留給他處理。"""
        import cut_tailor as ct
        a = JOBS[0]['id']
        make_board(self.path, {a: {'s': 'meh', 'app': 'prep'}})
        out = os.path.join(self.dir, 'trial'); os.makedirs(os.path.join(out, ct.jid(a)))
        with open(os.path.join(out, ct.jid(a), 'fill.json'), 'w', encoding='utf-8') as fh:
            json.dump({'skip': True, 'reason': '職缺已關:網址導回職缺列表', 'quote': 'This job is no longer available'},
                      fh, ensure_ascii=False)
        given = {a: {'text': 'Risk Analyst. This job is no longer available.', 'lang': ''}}
        moved = ct._apply_stages([(a, 'A')], self.path, out_dir=out, given=given)
        self.assertEqual(read_fb(self.path)[a], {'s': 'techerr', 's0': 'meh', 'app0': 'prep'})   # 原本的心情、階段都留著,可以放回原處
        job = bd.parse(read(self.path))['data']['jobs'][0]
        self.assertTrue(job['dead'])
        self.assertIn('agent 判斷', job['prep_note'])                         # 標明是 agent 判斷、附它抄的原文
        self.assertIn('This job is no longer available', job['prep_note'])
        self.assertEqual(moved['closed'], 1)

    def test_a_closure_whose_quote_is_not_in_the_jd_leaves_the_card_where_it_was(self):
        """安檢門(#317):agent 說職缺已關,抄的那一句 JD 原文裡沒有:不標出錯、不推進,卡上寫出 agent 說什麼、實際是什麼。"""
        import cut_tailor as ct
        a = JOBS[0]['id']
        make_board(self.path, {a: {'s': 'meh', 'app': 'prep'}})
        out = os.path.join(self.dir, 'trial'); os.makedirs(os.path.join(out, ct.jid(a)))
        with open(os.path.join(out, ct.jid(a), 'fill.json'), 'w', encoding='utf-8') as fh:
            json.dump({'skip': True, 'reason': '職缺已關:已額滿', 'quote': '本職缺已額滿'}, fh, ensure_ascii=False)
        given = {a: {'text': 'Risk Analyst. Apply now.', 'lang': ''}}
        moved = ct._apply_stages([(a, 'A')], self.path, out_dir=out, given=given)
        self.assertEqual(read_fb(self.path)[a], {'s': 'meh', 'app': 'prep'})
        job = bd.parse(read(self.path))['data']['jobs'][0]
        self.assertFalse(job.get('dead'))
        self.assertIn('本職缺已額滿', job['prep_note'])
        self.assertIn('JD 原文裡沒有', job['prep_note'])
        self.assertEqual(moved['closed'], 0)

    def test_agent_fixes_a_drifted_card_name_and_keeps_the_link(self):
        """agent 判斷「同一個缺、看板上的名字過時」時寫 real_title,程式照改卡片名字,後面的連結留著。"""
        import cut_tailor as ct
        u = JOBS[0]['id']
        make_board(self.path, {u: {'app': 'prep'}}, jobs=[{'id': u, 'target': f'Northwind Graduate · Risk Operations Specialist (SQL)（[Lever]({u})）'}])
        out = os.path.join(self.dir, 'trial'); os.makedirs(os.path.join(out, ct.jid(u)))
        with open(os.path.join(out, ct.jid(u), 'fill.json'), 'w', encoding='utf-8') as fh:
            json.dump({'variant': 'general', 'lang': 'en', 'real_title': 'Northwind Graduate · Risk Analyst'}, fh)
        given = {u: {'text': 'Northwind Graduate · Risk Analyst. SQL, Python.', 'lang': ''}}
        self.assertEqual(ct.apply_real_titles([(u, 'x')], self.path, out_dir=out, given=given), 1)
        t = [j for j in bd.parse(read(self.path))['data']['jobs'] if j['id'] == u][0]['target']
        self.assertEqual(t, f'Northwind Graduate · Risk Analyst（[Lever]({u})）')

class PrepRunFinish(Tmp):
    def test_approved_output_is_excluded_from_future_runs(self):
        import cut_tailor as ct
        from unittest.mock import patch
        a, b = JOBS[0]['id'], JOBS[1]['id']
        out = os.path.join(self.dir, 'out')
        os.makedirs(os.path.join(out, ct.jid(a)))
        with open(os.path.join(out, ct.jid(a), 'fill.json'), 'w', encoding='utf-8') as f:
            json.dump({'approved': True, 'variant': 'general'}, f)
        with patch.object(ct, 'OUT', out):
            self.assertEqual(ct.skip_approved([(a, 'Approved'), (b, 'Pending')]), [(b, 'Pending')])

    def test_the_saved_fill_keeps_only_what_the_gate_accepted_and_never_an_approval_the_agent_wrote(self):
        """#313 審查:這一輪的交件單經安檢門,存回正式位置的只有收下的格子加上跑之前別人寫的欄位;
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
        with open(os.path.join(sp, 'cut_tailor_t0'), 'w') as f:
            f.write(str(time.time() - 10))
        with open(os.path.join(sp, 'cut_tailor_prompt.txt'), 'w') as f:
            f.write('prompt')
        with open(os.path.join(sp, 'rows.json'), 'w', encoding='utf-8') as f:
            json.dump([[a, 'Fresh']], f)
        with open(os.path.join(sp, 'keep.json'), 'w', encoding='utf-8') as f:
            json.dump({ct.jid(a): {'tailored': '他的產線寫的客製內容'}}, f, ensure_ascii=False)
        with open(os.path.join(run_out, ct.jid(a), 'fill.json'), 'w', encoding='utf-8') as f:
            json.dump({'resume': 'general', 'lang': 'klingon', 'why': 'fresh', 'approved': True, 'secret': 'x'}, f)
        with patch.multiple(ct, SP=sp, ROWSF=os.path.join(sp, 'rows.json'), KEEPF=os.path.join(sp, 'keep.json'),
                            OUT=canonical, PIDF=os.path.join(sp, 'pid'), WORKER_PIDF=os.path.join(sp, 'worker.pid')):
            with patch.object(ct, '_status'), patch.object(ct, '_report'), \
                 patch.object(ct.ar, 'argv_for', return_value=(['codex'], '/repo')), \
                 patch.object(ct.ar, 'launch', return_value=SimpleNamespace(pid=123)), \
                 patch.object(ct.ar, 'wait_done', return_value=[ct.ar.AgentResult('completed', 0, 123)]), \
                 patch.object(ct.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                ct.run_finish(self.path, out_dir=run_out)
            with open(os.path.join(canonical, ct.jid(a), 'fill.json'), encoding='utf-8') as f:
                saved = json.load(f)
            self.assertEqual(saved.get('tailored'), '他的產線寫的客製內容')      # 別人寫的欄位原樣放回
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
        rows_file = os.path.join(sp, 'rows.json')
        run_out = os.path.join(sp, 'runs', 'fresh')
        canonical = os.path.join(sp, 'fills')
        os.makedirs(os.path.join(sp, 'runs'), exist_ok=True)
        os.makedirs(os.path.join(run_out, ct.jid(a)), exist_ok=True)
        os.makedirs(os.path.join(canonical, ct.jid(b)), exist_ok=True)
        t0 = time.time() - 10
        with open(os.path.join(sp, 'cut_tailor_t0'), 'w') as f:
            f.write(str(t0))
        with open(os.path.join(sp, 'cut_tailor_prompt.txt'), 'w') as f:
            f.write('prompt')
        with open(rows_file, 'w', encoding='utf-8') as f:
            json.dump([[a, 'Fresh'], [b, 'Missing']], f)
        with open(os.path.join(sp, 'keep.json'), 'w', encoding='utf-8') as f:
            json.dump({}, f)
        with open(os.path.join(run_out, ct.jid(a), 'fill.json'), 'w', encoding='utf-8') as f:
            json.dump({'variant': 'general', 'lang': 'zh', 'why': 'fresh'}, f)
        with open(os.path.join(canonical, ct.jid(b), 'fill.json'), 'w', encoding='utf-8') as f:
            json.dump({'variant': 'stale', 'lang': 'en'}, f)

        statuses, reports = [], []
        with patch.multiple(ct, SP=sp, ROWSF=rows_file, KEEPF=os.path.join(sp, 'keep.json'),
                            OUT=canonical, PIDF=os.path.join(sp, 'pid'),
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


class PrepReselection(Tmp):
    def _state_paths(self):
        root = os.path.join(self.dir, 'cut-tailor-state')
        out = os.path.join(self.dir, 'cut-tailor-out')
        os.makedirs(root, exist_ok=True)
        os.makedirs(out, exist_ok=True)
        return {
            'SP': root,
            'OUT': out,
            'ROWSF': os.path.join(root, 'rows.json'),
            'CHOICESF': os.path.join(root, 'choices.json'),
            'CACHEDF': os.path.join(root, 'cached.json'),
            'STATUSF': os.path.join(root, 'status.json'),
            'PIDF': os.path.join(root, 'pid'),
            'WORKER_PIDF': os.path.join(root, 'worker.pid'),
            'RUNS': os.path.join(root, 'runs'),
            'PREV': os.path.join(root, 'previous'),
            'KEEPF': os.path.join(root, 'previous', 'kept.json'),
        }

    def test_only_never_picked_changed_signature_and_new_feedback_are_sent_to_agent(self):
        import json
        import sys
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
             patch.object(ct, 'keep_others') as keep_others, \
             patch('page_fetch.fetch', side_effect=lambda url: page_result(
                 url, 'Active role: expired credentials; unrelated account is 已關閉.')), \
             patch.object(ct.subprocess, 'Popen', return_value=SimpleNamespace(pid=123)), \
             patch.object(sys, 'argv', ['cut_tailor.py', '--board', self.path]):
            ct.main()

        rows = json.load(open(paths['ROWSF'], encoding='utf-8'))
        self.assertEqual([u for u, _ in rows], urls[:3])
        self.assertEqual(list(keep_others.call_args.args[0]), [tuple(row) for row in rows])
        prompt = open(os.path.join(paths['SP'], 'cut_tailor_prompt.txt'), encoding='utf-8').read()
        self.assertTrue(all(u in prompt for u in urls[:3]))
        self.assertIn('Active role: expired credentials; unrelated account is 已關閉.', prompt)
        self.assertIn('不可開啟來源網址或使用瀏覽器', prompt)
        self.assertNotIn(urls[3], prompt)
        self.assertFalse(next(j for j in bd.parse(read(self.path))['data']['jobs']
                              if j['id'] == urls[0]).get('dead', False))
        self.assertEqual(json.load(open(paths['CACHEDF'], encoding='utf-8')), [urls[3]])

    def test_cached_pick_is_promoted_and_reconciled_without_agent(self):
        import json
        import sys
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
             patch('page_fetch.fetch_many', return_value=[SimpleNamespace(status='ok')]), \
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
        import json
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
        os.makedirs(os.path.join(out, ct.jid(url)))
        with open(os.path.join(out, ct.jid(url), 'fill.json'), 'w', encoding='utf-8') as f:
            json.dump({'resume': 'general', 'lang': 'zh',
                       'why': 'new recommendation', 'content_problem': True}, f)
        with patch.object(cf, 'LANGS', ['zh', 'en']), patch.object(
                cf, 'RESUMES', {item['id']: item for item in resumes}):
            signature = prefs.resume_selection_signature(resumes)
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
        make_board(self.path, {JOBS[0]['id']: {'s': 'like', 'ship': True}})

    def _git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess_run([self.fh._git(), *args], cwd=self.dir, env=env)

    def test_the_version_before_the_conversion_is_the_old_format(self):
        bs.migrate_marks(self.path)

        before = self._git('log', '-1', '--format=%H', '--grep=轉換前').strip()
        self.assertTrue(before)
        old = json.loads(bd.parse(self._git('show', before + ':board.html'))['fb'])
        self.assertNotIn('__ds__', old)
        self.assertEqual(read_fb(self.path)['__ds__'], 1)

    def test_without_version_history_the_old_board_is_backed_up_first(self):
        with mock.patch.object(self.fh, '_git', return_value=None):
            bs.migrate_marks(self.path)

        folder = os.path.join(self.dir, self.fh.BACKUP_DIR)
        [backup] = os.listdir(folder)
        self.assertNotIn('__ds__', json.loads(bd.parse(read(os.path.join(folder, backup)))['fb']))
        self.assertEqual(read_fb(self.path)['__ds__'], 1)

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
        self.assertIn('投遞狀態轉換還沒轉換', row['detail'])
        self.assertTrue(row['fix'])


def subprocess_run(args, cwd, env):
    import subprocess
    return subprocess.run(args, cwd=cwd, env=env, text=True, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


class OldShipFlagIsMigratedOnDisk(OwnHome):
    """舊資料的可投遞是 ship 布林(沒有 app)。以前只有看板頁面載入時在記憶體裡改成 app='ship'、從沒存回去:
    卡停在「可以投了」,伺服器那邊的填表、送出、自動流程(都讀檔)卻看不到它;第一次改它還撞 409、卡跳回心情分頁。
    伺服器起來時把它寫回檔案(記進流水帳)。"""

    def test_leaves_nothing_in_the_shared_test_folder(self):
        """轉換前的退回點(版本、備份)落在這個測試自己的暫存資料夾,跑完跟著清掉;不准留在大家共用的測試資料夾。"""
        shared = os.environ['JOBSALVO_TEST_HOME']

        def everything():
            return {os.path.join(root, n) for root, dirs, files in os.walk(shared) for n in files + dirs}
        before = everything()
        make_board(self.path, {JOBS[0]['id']: {'s': 'like', 'ship': True}})
        bs.migrate_marks(self.path)
        self.assertEqual(read_fb(self.path)['__ds__'], 1)
        self.assertEqual(sorted(everything() - before), [])

    def test_server_start_writes_the_migration_back(self):
        import apply_run
        a, b = JOBS[0]['id'], JOBS[1]['id']
        make_board(self.path, {a: {'s': 'like', 'ship': True}, b: {'s': 'like', 'ship': True, 'app': 'sent'}})
        getattr(bs, 'migrate_marks', lambda _p: None)(self.path)
        fb = read_fb(self.path)
        self.assertEqual(fb[a], {'s': 'like', 'app': 'ship'})
        self.assertEqual(fb[b], {'s': 'like', 'app': 'sent', 'ds': 'sent', 'sent_by': 'legacy'})   # 已經往後走的不拉回可投遞
        self.assertEqual(fb['__ds__'], 1)                                 # 投遞狀態也一起轉好了,只轉一次
        jobs = {j['id']: j for j in JOBS}
        self.assertEqual(apply_run.eligible(jobs, fb, 'fill'), [a])     # 讀檔的程式看得到它
        before = read(bd.journal_path(self.path))
        bs.migrate_marks(self.path)                                     # 已經改過:不再動、不再記
        self.assertEqual(read(bd.journal_path(self.path)), before)

    def test_server_start_settles_rounds_that_are_no_longer_running(self):
        """伺服器起來時,卡停在正在填、正在送出,那一輪卻已經不在跑(Mac 重開、當掉):照狀態表收尾(修正 4、13)。"""
        import delivery_state as ds
        a, b, c = JOBS[0]['id'], JOBS[1]['id'], JOBS[2]['id']
        make_board(self.path, {'__ds__': 1,
                               a: {'app': 'ship', 'ds': 'running', 'apply': {'stage': 'fill', 'tab_id': '7'}},
                               b: {'app': 'ship', 'ds': 'running', 'apply': {'stage': 'fill'}},
                               c: {'app': 'ship', 'ds': 'sending', 'approve': {'snap': {}}, 'apply': {'stage': 'fill', 'tab_id': '9'}}})
        with mock.patch.object(bs, 'run_status', return_value={'running': False}):
            bs.migrate_marks(self.path)
        fb = read_fb(self.path)
        self.assertEqual([ds.state(fb[u]) for u in (a, b, c)], ['stuck', 'nopage', 'unsure'])
        self.assertIn('沒跑完', fb[a]['apply']['issues'][0])

    def test_custom_records_get_their_language(self):
        # 客製紀錄以前不分語言(resume:<id>):照紀錄裡原始檔的簽章認出是哪個語言的檔,改成 resume:<id>:<語言>。
        # 認不出來的(原始檔後來換過)留著不動:看板上列成「這張現在不寄這份」,可以清掉
        import hashlib
        import config as cf
        files = {}
        for lang in ('zh', 'en'):
            files[lang] = os.path.join(self.dir, f'base-{lang}.pdf')
            with open(files[lang], 'wb') as f:
                f.write(b'%PDF ' + lang.encode())
        sig = {k: hashlib.sha256(open(v, 'rb').read()).hexdigest() for k, v in files.items()}
        a, b = JOBS[0]['id'], JOBS[1]['id']
        make_board(self.path, {
            a: {'s': 'like', 'app': 'ready', 'custom_docs': {
                'resume:general': {'id': 'resume:general', 'status': 'accepted', 'path': 'custom/a.pdf', 'source_sig': sig['en']},
                'attachment:letter': {'status': 'review', 'candidate_source_sig': sig['zh']}}},
            b: {'s': 'like', 'app': 'ready', 'custom_docs': {
                'resume:general': {'status': 'accepted', 'path': 'custom/b.pdf', 'source_sig': 'changed-since'},
                'resume:legacy': {'status': 'accepted', 'path': 'custom/c.pdf'}}},
        })
        with mock.patch.object(cf, 'RESUMES', {'general': {'id': 'general', 'files': dict(files)}}), \
                mock.patch.object(cf, 'ATTACHMENTS', [{'id': 'letter', 'files': {'zh': files['zh']}}]):
            bs.migrate_marks(self.path)
            fb = read_fb(self.path)
            self.assertEqual(sorted(fb[a]['custom_docs']), ['attachment:letter:zh', 'resume:general:en'])
            self.assertEqual(fb[a]['custom_docs']['resume:general:en']['id'], 'resume:general:en')
            self.assertEqual(sorted(fb[b]['custom_docs']), ['resume:general', 'resume:legacy'])   # 認不出來的不動
            before = read(bd.journal_path(self.path))
            bs.migrate_marks(self.path)                                 # 已經改過:不再動、不再記
            self.assertEqual(read(bd.journal_path(self.path)), before)


class AgentSandbox(unittest.TestCase):
    def test_agent_runs_outside_the_codex_sandbox(self):
        """沙盒擋掉 Chrome 需要的系統服務(在裡面開 Chrome 一定當掉),也會斷網;派工一律不開沙盒。"""
        import agent_run as ar
        argv, _ = ar.argv_for('main', '嗨', tempfile.gettempdir())
        self.assertEqual(argv[argv.index('-s') + 1], 'danger-full-access')

class PreApplyPageReading(Tmp):
    def test_link_gate_fetches_pages_concurrently_and_keeps_prompt_order(self):
        from types import SimpleNamespace
        import board_status, page_fetch

        urls = ['https://example.invalid/jobs/first', 'https://example.invalid/jobs/second']
        jobs = [{'id': url, 'target': f'Job {index}'} for index, url in enumerate(urls, 1)]
        make_board(self.path, {url: {'app': 'ready'} for url in urls}, jobs=jobs)
        pages = {url: page_fetch.PageResult(url, 'ok', text=f'Active role {index}', via='fake')
                 for index, url in enumerate(urls, 1)}
        barrier = threading.Barrier(2)
        fetched = []
        prompts = []

        def fetch_page(url):
            fetched.append(url)
            barrier.wait(timeout=5)
            return pages[url]

        def run_agent(prompt, _log, _browser_required):
            prompts.append(prompt)
            output = prompt.split('把 JSON 寫到 ', 1)[1].split(':{"jobs"', 1)[0]
            with open(output, 'w', encoding='utf-8') as f:
                json.dump({'jobs': [{'id': f'J{index}', 'status': 'live'}
                                    for index in range(1, len(urls) + 1)]}, f)
            return SimpleNamespace(ok=True, message=lambda: 'completed')

        issues, revived, revived_ids = board_status._link_issues(
            {'ready': jobs, 'ship': []}, self.path, fetch_page=fetch_page, run_agent=run_agent,
        )

        self.assertEqual(issues, [])
        self.assertEqual(set(fetched), set(urls))
        self.assertLess(prompts[0].index(f'URL: {urls[0]}'), prompts[0].index(f'URL: {urls[1]}'))
        self.assertEqual(revived, [])
        self.assertEqual(revived_ids, set())

    def test_same_page_text_is_not_asked_again(self):
        # 以前每重建一次就把每一頁全部重問 agent 一次;原文沒變就沿用上次的判斷,變了、過期了才重問
        from types import SimpleNamespace
        import board_status, page_fetch
        url = 'https://example.invalid/jobs/cached'
        asked = []

        def run_agent(prompt, _log, _browser_required):
            asked.append(prompt)
            output = prompt.split('把 JSON 寫到 ', 1)[1].split(':{"jobs"', 1)[0]
            with open(output, 'w', encoding='utf-8') as f:
                json.dump({'jobs': [{'id': 'J1', 'status': 'live', 'reason': '還在徵'}]}, f)
            return SimpleNamespace(ok=True, message=lambda: 'completed')

        page = {url: page_fetch.PageResult(url, 'ok', text='Active role', via='fake')}
        t = 1_000_000.0
        self.assertEqual(board_status._agent_link_verdicts(page, self.path, run_agent, now=t)[url][0], 'live')
        self.assertEqual(board_status._agent_link_verdicts(page, self.path, run_agent, now=t + 60)[url],
                         ('live', '還在徵'))
        self.assertEqual(len(asked), 1)                                   # 同一段原文:沒再問
        changed = {url: page_fetch.PageResult(url, 'ok', text='Active role (updated)', via='fake')}
        board_status._agent_link_verdicts(changed, self.path, run_agent, now=t + 120)
        self.assertEqual(len(asked), 2)                                   # 原文變了:重問
        board_status._agent_link_verdicts(page, self.path, run_agent,
                                          now=t + board_status.LINK_VERDICT_DAYS * 86400 + 1)
        self.assertEqual(len(asked), 3)                                   # 過期了:重問

    def test_only_confirmed_closures_block_the_card(self):
        """#60 第 5 點:404/410 與 agent 判關閉才擋;無法確認、抓不到只提示(soft)。"""
        from types import SimpleNamespace
        import board_status, page_fetch

        urls = {k: f'https://example.invalid/jobs/{k}' for k in ('gone', 'closed', 'unsure', 'unread', 'live')}
        jobs = [{'id': u, 'target': k} for k, u in urls.items()]
        make_board(self.path, {u: {'app': 'ready'} for u in urls.values()}, jobs=jobs)
        pages = {
            urls['gone']: page_fetch.PageResult(urls['gone'], 'closed', http_status=404, via='direct'),
            urls['closed']: page_fetch.PageResult(urls['closed'], 'ok', text='closed role', via='fake'),
            urls['unsure']: page_fetch.PageResult(urls['unsure'], 'ok', text='vague page', via='fake'),
            urls['unread']: page_fetch.PageResult(urls['unread'], 'unknown', via=''),
            urls['live']: page_fetch.PageResult(urls['live'], 'ok', text='open role', via='fake'),
        }
        answer = {urls['closed']: 'closed', urls['unsure']: 'uncertain', urls['live']: 'live'}

        def run_agent(prompt, _log, _browser_required):
            output = prompt.split('把 JSON 寫到 ', 1)[1].split(':{"jobs"', 1)[0]
            rows = []
            for block in prompt.split('=== ')[1:]:
                jid = block.split(' ===', 1)[0]
                url = block.split('URL: ', 1)[1].split('\n', 1)[0]
                rows.append(dict({'id': jid, 'status': answer[url]},
                                 **({'quote': 'closed role'} if answer[url] == 'closed' else {})))
            with open(output, 'w', encoding='utf-8') as f:
                json.dump({'jobs': rows}, f)
            return SimpleNamespace(ok=True, message=lambda: 'completed')

        issues, _revived, _ids = board_status._link_issues(
            {'ready': jobs, 'ship': []}, self.path, fetch_page=pages.get, run_agent=run_agent)
        soft = {x['jid']: bool(x.get('soft')) for x in issues}
        self.assertEqual(soft, {urls['gone']: False, urls['closed']: False,
                                urls['unsure']: True, urls['unread']: True})
        closed = next(x for x in issues if x['jid'] == urls['closed'])
        self.assertIn('agent 判斷', closed['msg'])            # 標明是 agent 判斷、附它抄的原文
        self.assertEqual(closed['judged'], 'closed role')
        # 他在卡上按「不對,職缺還在」:同一個判斷不再擋,只留一行提示
        issues, _r, _i = board_status._link_issues(
            {'ready': jobs, 'ship': []}, self.path, fetch_page=pages.get, run_agent=run_agent,
            fb={urls['closed']: {'judged_no': {'closed': '2026-09-30'}}})
        self.assertTrue(next(x for x in issues if x['jid'] == urls['closed']).get('soft'))

    def test_a_closure_whose_quote_is_not_on_the_page_does_not_block(self):
        """安檢門(#317):agent 說關了,抄的那一句頁面上沒有:不擋,卡上寫出 agent 說什麼、實際是什麼。"""
        from types import SimpleNamespace
        import board_status, page_fetch
        url = 'https://example.invalid/jobs/lie'
        jobs = [{'id': url, 'target': 'lie'}]
        make_board(self.path, {url: {'app': 'ready'}}, jobs=jobs)

        def run_agent(prompt, _log, _browser_required):
            output = prompt.split('把 JSON 寫到 ', 1)[1].split(':{"jobs"', 1)[0]
            with open(output, 'w', encoding='utf-8') as f:
                json.dump({'jobs': [{'id': 'J1', 'status': 'closed', 'quote': 'Position closed'}]}, f)
            return SimpleNamespace(ok=True, message=lambda: 'completed')

        issues, _r, _i = board_status._link_issues(
            {'ready': jobs, 'ship': []}, self.path,
            fetch_page=lambda u: page_fetch.PageResult(u, 'ok', text='Open role, apply now', via='fake'),
            run_agent=run_agent)
        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0].get('soft'))
        self.assertIn('Position closed', issues[0]['msg'])
        self.assertIn('原文裡沒有', issues[0]['msg'])

    def test_link_gate_asks_the_agent_in_small_batches(self):
        """一次把幾十頁全文塞給同一隻 agent,容易整批判不出來;分小批問。"""
        from types import SimpleNamespace
        import board_status, page_fetch

        urls = [f'https://example.invalid/jobs/{i}' for i in range(20)]
        jobs = [{'id': u, 'target': u} for u in urls]
        make_board(self.path, {u: {'app': 'ready'} for u in urls}, jobs=jobs)
        sizes = []

        def run_agent(prompt, _log, _browser_required):
            output = prompt.split('把 JSON 寫到 ', 1)[1].split(':{"jobs"', 1)[0]
            ids = [b.split(' ===', 1)[0] for b in prompt.split('=== ')[1:]]
            sizes.append(len(ids))
            with open(output, 'w', encoding='utf-8') as f:
                json.dump({'jobs': [{'id': i, 'status': 'live'} for i in ids]}, f)
            return SimpleNamespace(ok=True, message=lambda: 'completed')

        issues, _r, _i = board_status._link_issues(
            {'ready': jobs, 'ship': []}, self.path,
            fetch_page=lambda u: page_fetch.PageResult(u, 'ok', text='open role', via='fake'),
            run_agent=run_agent)
        self.assertEqual(issues, [])
        self.assertEqual(sum(sizes), 20)
        self.assertLessEqual(max(sizes), board_status.LINK_AGENT_BATCH)

    def test_link_gate_sends_fetched_text_to_a_browserless_agent(self):
        import io
        import sys
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from unittest.mock import patch
        import board_status, page_fetch

        url = 'https://example.invalid/jobs/security-engineer'
        text = 'The active role handles expired credentials; an unrelated account is 已關閉.'
        job = {'id': url, 'target': 'Security Engineer'}
        make_board(self.path, {url: {'app': 'ready'}}, jobs=[job])
        page = page_fetch.PageResult(url, 'ok', text=text, via='reader')
        calls = []

        def fake_agent(prompt, _log, _home, **kwargs):
            calls.append((prompt, kwargs))
            output = prompt.split('把 JSON 寫到 ', 1)[1].split(':{"jobs"', 1)[0]
            with open(output, 'w', encoding='utf-8') as f:
                json.dump({'jobs': [{'id': 'J1', 'status': 'live', 'reason': '原文說仍在招募'}]}, f)
            return SimpleNamespace(ok=True, message=lambda: 'completed')

        with patch.object(sys, 'argv', ['board_status.py', '--links', '--board', self.path]), \
             patch('page_fetch.fetch', return_value=page), \
             patch('agent_run.run', side_effect=fake_agent), \
             patch.object(board_status.ship, 'check', return_value=[]), \
             redirect_stdout(io.StringIO()) as output:
            result = board_status.main()

        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 1)
        prompt, options = calls[0]
        self.assertIn(text, prompt)
        self.assertIn('expired', prompt)
        self.assertIn('不代表職缺已關', prompt)
        self.assertFalse(options['browser_required'])
        self.assertFalse(options['web'])
        self.assertIn('每一張都過關', output.getvalue())


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

    def test_link_verdicts_have_distinct_kinds_and_unknown_stays_soft(self):
        import board_status, page_fetch

        closed, unknown = JOBS[:2]
        make_board(self.path, {j['id']: {'app': 'ready'} for j in (closed, unknown)},
                   jobs=[closed, unknown])
        pages = {
            closed['id']: page_fetch.PageResult(closed['id'], 'closed', via='104', http_status=200),
            unknown['id']: page_fetch.PageResult(unknown['id'], 'unknown'),
        }
        with mock.patch.object(sys, 'argv', ['board_status.py', '--board', self.path, '--links', '--write']), \
             mock.patch('page_fetch.fetch', side_effect=pages.get), \
             mock.patch('sys.stdout', new=io.StringIO()):
            result = board_status.main()

        issues = bd.parse(read(self.path))['data']['status']['issues']
        links = [x for x in issues if x.get('kind') in ('closed', 'unverified')]
        self.assertEqual(result, 3)
        self.assertEqual([(x['jid'], x['kind'], bool(x.get('soft'))) for x in links],
                         [(closed['id'], 'closed', False), (unknown['id'], 'unverified', True)])

    def test_officially_live_104_job_does_not_need_agent_link_judgement(self):
        import board_status, page_fetch

        job = JOBS[0]
        make_board(self.path, {job['id']: {'app': 'ready'}}, jobs=[job])
        page = page_fetch.PageResult(job['id'], 'ok', text='職缺仍在徵', via='104', verified_live=True)
        with mock.patch.object(sys, 'argv', ['board_status.py', '--board', self.path, '--links', '--write']), \
             mock.patch('page_fetch.fetch', return_value=page), \
             mock.patch('agent_run.run', side_effect=AssertionError('官方仍在徵,不須 agent 判斷')), \
             mock.patch('sys.stdout', new=io.StringIO()):
            board_status.main()

        issues = bd.parse(read(self.path))['data']['status']['issues']
        self.assertFalse([x for x in issues if x['kind'] in ('closed', 'unverified')])


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


class NoRejudging(unittest.TestCase):
    """判過、判定不送的那些,下一輪不要再抓一次 JD、再花一批判斷。結論一定一樣,純浪費。"""
    def setUp(self):
        import research
        self.rs = research
        self.f = os.path.join(tempfile.mkdtemp(), 'judged-no.jsonl')

    def test_rejected_urls_are_dropped_before_the_expensive_part(self):
        import converge
        self.rs.turned_add([{'url': 'https://ex.test/job/9', 'at': '', 'mode': 'deep', 'why': '不對味'}], self.f)
        called = []
        def fetch_page(url):
            called.append(url)
            return page_result(url)
        ok, drop = self.rs.clean([{'url': 'https://ex.test/job/9', 'title': '資安工程師'}],
                                 set(), converge.EXCLUDE_TITLE, turned=self.rs.turned_down(self.f),
                                 fetch_page=fetch_page)
        self.assertEqual(ok, [])
        self.assertEqual(drop['判過不送'], ['https://ex.test/job/9'])
        self.assertEqual(called, [], '判過不送的還去問 HTTP 狀態')

    def test_a_job_still_on_the_board_is_not_double_counted(self):
        """板上已經有的走原本那條,不要因為多了這個機制就算成兩種原因。"""
        import converge
        ok, drop = self.rs.clean([{'url': 'https://ex.test/job/1', 'title': 'X'}],
                                 {'https://ex.test/job/1'}, converge.EXCLUDE_TITLE,
                                 turned=self.rs.turned_down(self.f), fetch_page=page_result)
        self.assertEqual(drop['板上已經有'], ['https://ex.test/job/1'])
        self.assertEqual(drop['判過不送'], [])


class SeedRuns(Http):
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

    def test_company_scope_narrows_to_that_company(self):
        """「找這家更多」= 交給 agent 的公司清單只剩那一家,不是另外寫一支列公司開缺的程式。"""
        import research
        cs = [{'id': 'https://jobs.lever.co/aaa/1', 'title': 'X', 'company': 'AAA', 'pos': True, 'note': ''},
              {'id': 'https://jobs.lever.co/bbb/1', 'title': 'Y', 'company': 'BBB', 'pos': True, 'note': ''}]
        l = research.liked_company_list(cs)
        self.assertEqual(sorted(r['company'] for r in l), ['AAA', 'BBB'])
        self.assertEqual([r['company'] for r in l if r['company'] in {'AAA'}], ['AAA'])


class TestsLeaveNothingRunning(unittest.TestCase):
    def test_a_saving_test_leaves_no_folder_history_timer(self):
        """#335:存檔會排一個 1 秒後存資料夾版本的計時器;測試結束時沒收掉,它在下一條測試裡跑 git,
        被那條換掉的 subprocess.run 抓到。存過檔的測試一結束,背景不能還有計時器。"""
        result = unittest.TestResult()
        Http('test_undo_only_when_nothing_else_moved_the_card').run(result)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertEqual([t for t in threading.enumerate() if isinstance(t, threading.Timer)], [])


class PromptPreview(Http):
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

    def test_every_agent_button_has_a_prompt(self):
        """會派 agent 的按鈕,每一顆都要看得到它會送出去的東西:找缺三種＋判斷、準備區、代投三段。"""
        for q in ('kind=find&mode=deep', 'kind=find&mode=wide', 'kind=find&mode=dir', 'kind=judge', 'kind=prep',
                  'kind=apply&stage=fill', 'kind=apply&stage=fix', 'kind=apply&stage=submit'):
            code, raw, _ = self.req('/api/prompt?' + q)
            self.assertEqual(code, 200, q)
            self.assertTrue(json.loads(raw)['prompt'].strip(), q)

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
        with mock.patch.object(ar, 'run', side_effect=lambda *a, **k: launched.append(a)):
            self.req('/api/prompt?kind=find&mode=deep')
            self.req('/api/prompt?kind=prep')
        self.assertEqual(launched, [])


class NoteTracking(unittest.TestCase):
    """「新表態逐張核對」只給程式交件時核對,不存進筆記、不給判斷的 agent(#76:判斷 prompt 一半以上是它)。"""

    def test_tracking_is_dropped(self):
        import prefs
        note = ('## 使用者自訂\n\n不要外派\n\n## Agent 假設\n\n- 喜歡平台｜出處 A\n\n'
                '### 新表態逐張核對\n- 卡片代號=A：來源假設=喜歡平台\n')
        out = prefs.without_tracking(note)
        self.assertNotIn('逐張核對', out)
        self.assertIn('- 喜歡平台｜出處 A', out)
        self.assertIn('不要外派', out)
        self.assertEqual(prefs.without_tracking('沒有核對段'), '沒有核對段')
        mid = note.replace('### 新表態逐張核對', '### 新表態逐張核對').replace(
            '- 卡片代號=A：來源假設=喜歡平台\n', '- 卡片代號=A：來源假設=喜歡平台\n\n## 其他\n保留\n')
        self.assertIn('## 其他\n保留', prefs.without_tracking(mid))


class FlagWords(unittest.TestCase):
    """設定頁「要小心的字」:職稱中了不丟,標出來交給判斷(以前設定了完全沒效果)。"""

    def test_flag_words_mark_candidate_for_judge(self):
        import re as _re
        import research as rs
        import page_fetch
        cands = [{'url': 'https://jobs.lever.co/acme/1', 'title': 'Sales Engineer', 'company': 'Acme'},
                 {'url': 'https://jobs.lever.co/acme/2', 'title': 'Platform Engineer', 'company': 'Acme'}]
        page = lambda u: page_fetch.PageResult(u, 'ok', text='JD text', via='direct')
        ok, drop = rs.clean(cands, set(), _re.compile('(?!x)x'), fetch_page=page, flag_re=_re.compile('sales', _re.I))
        self.assertEqual(len(ok), 2)                          # 沒有被丟掉
        flagged = {c['title']: c.get('flag') or [] for c in ok}
        self.assertTrue(any('要小心' in f and 'Sales' in f for f in flagged['Sales Engineer']))
        self.assertFalse(any('要小心' in f for f in flagged['Platform Engineer']))


class GhostAndScam(Tmp):
    """#27:詐騙徵兆一律不進看板;幽靈職缺照樣進,但標出來;刊登太久的由程式先提醒。"""

    def _judge(self, risk):
        import json
        import research as rs
        import agent_run as ar
        url = 'https://ex.test/job/risk'
        # 判斷拿程式抓回的 JD 原文和程式提醒核對(#317):它引的那句要在裡面
        candidate = {'url': url, 'title': 'Engineer', 'company': 'Example',
                     'jd': 'Engineer at Example. 錄取前需繳交保證金。', 'page_status': 'ok',
                     'flag': ['刊登已經 120 天(超過 90 天):可能是長期掛著、沒在真的招人的幽靈職缺,判斷時一起看']}

        def run_agent(prompt, outfile, _model):
            payload = [{'id': 'J1', 'title': 'Engineer', 'company': 'Example', 'keep': True, 'fit': 4,
                        'cite': [], 'why': '看起來符合', 'cat': rs.CATS[0], 'risk': risk,
                        'card': {'fit': '符合', 'ammo': '直投'}}]
            with open(outfile[:-4] + '.json', 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
            return ar.AgentResult('completed', 0, 1)
        res = rs.judge([dict(candidate)], [], self.dir, 'main', run_agent, resumes=[])
        return rs, candidate, res[url]

    def test_a_title_that_is_not_on_the_page_keeps_the_card_off_the_board(self):
        """安檢門(#317):判斷寫的職稱在程式抓回的 JD 原文裡沒有:這一張不進板、不算判過,原因寫出 agent 說什麼。"""
        import json
        import research as rs
        import agent_run as ar
        url = 'https://ex.test/job/lie'
        candidate = {'url': url, 'title': 'Engineer', 'company': 'Example', 'jd': 'Engineer at Example.',
                     'page_status': 'ok'}

        def run_agent(prompt, outfile, _model):
            with open(outfile[:-4] + '.json', 'w', encoding='utf-8') as f:
                json.dump([{'id': 'J1', 'title': 'Chief Chef', 'company': 'Example', 'keep': True, 'fit': 5,
                            'cite': [], 'why': '很合', 'cat': rs.CATS[0]}], f, ensure_ascii=False)
            return ar.AgentResult('completed', 0, 1)
        r = rs.judge([dict(candidate)], [], self.dir, 'main', run_agent, resumes=[])[url]
        self.assertFalse(r['keep'])
        self.assertFalse(r['readable'])                   # 不記成「判過不送」,下一輪找到會再判
        self.assertTrue(any('頁面上的職稱' in p and 'Chief Chef' in p for p in r['wrong']), r['wrong'])

    def test_scam_is_never_kept(self):
        _rs, _c, r = self._judge({'kind': 'scam', 'why': 'JD 寫「錄取前需繳交保證金」'})
        self.assertFalse(r['keep'])
        self.assertIn('疑似詐騙', r['why'])

    def test_ghost_is_kept_and_marked_on_card(self):
        rs, c, r = self._judge({'kind': 'ghost', 'why': '刊登已經 120 天'})
        self.assertTrue(r['keep'])
        self.assertEqual(rs.job_entry(c, r, {})['src']['risk']['kind'], 'ghost')

    def test_no_risk_leaves_card_alone(self):
        rs, c, r = self._judge({'kind': '', 'why': ''})
        self.assertNotIn('risk', rs.job_entry(c, r, {})['src'])

    def test_old_posting_is_flagged_for_judge(self):
        import datetime
        import re as _re
        import research as rs
        import page_fetch
        old = (datetime.date.today() - datetime.timedelta(days=120)).isoformat()
        new = datetime.date.today().isoformat()
        cands = [{'url': 'https://jobs.lever.co/a/old', 'title': 'Old', 'company': 'A'},
                 {'url': 'https://jobs.lever.co/a/new', 'title': 'New', 'company': 'A'}]
        pages = {'https://jobs.lever.co/a/old': old, 'https://jobs.lever.co/a/new': new}
        ok, _ = rs.clean(cands, set(), _re.compile('(?!x)x'),
                         fetch_page=lambda u: page_fetch.PageResult(u, 'ok', text='JD', posted_at=pages[u]))
        flags = {c['title']: ' '.join(c.get('flag') or []) for c in ok}
        self.assertIn('幽靈職缺', flags['Old'])
        self.assertNotIn('幽靈職缺', flags['New'])


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
