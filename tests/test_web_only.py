# -*- coding: utf-8 -*-
"""使用者只碰網頁就能做完的事:設定頁讀寫、上傳、卡片自己的檔、貼網址加入、分類建議、看紀錄與應徵紀錄對帳。"""
import os, sys, json, time, shutil, unittest, urllib.request, contextlib
from unittest.mock import Mock, patch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
from _env import read_board  # noqa: E402
import config as cf            # noqa: E402
import board_server as bs      # noqa: E402
import test_board as tb         # noqa: E402
JOBS, make_board, read_fb = tb.JOBS, tb.make_board, tb.read_fb

KEEP = ('jobsalvo.json', 'prefs.md', 'preference-note.md', 'apply-rules.md', 'resume.md', '.resume-paste.md')


class WebOnly(tb.HttpBase):
    def setUp(self):
        super().setUp()
        self.snap = {}
        for n in KEEP:
            if os.path.exists(os.path.join(cf.HOME, n)):
                with open(os.path.join(cf.HOME, n), encoding='utf-8') as f:
                    self.snap[n] = f.read()
        os.environ['JOB_FAKE_STEP'] = '0.1'

    def tearDown(self):
        for n in KEEP:
            path = os.path.join(cf.HOME, n)
            if n in self.snap:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(self.snap[n])
            elif os.path.exists(path):
                os.remove(path)
        cf.reload()
        for k, p in list(bs._PROC.items()):
            if p is not None and p.poll() is None:
                p.kill(); p.wait()
            bs._PROC[k] = None
        shutil.rmtree(cf.path('custom'), ignore_errors=True)
        super().tearDown()

    def put(self, path, data, ua='Mozilla/5.0 (iPhone)'):
        code, raw, _ = self.req(path, ua=ua, method='PUT', data=data)
        return code, json.loads(raw or b'{}')

    def settings(self):
        return json.loads(self.req('/api/settings')[1])

    def test_sandbox_never_checks_or_applies_updates(self):
        code, raw, _ = self.req('/api/update')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)['blocked'], '副本不檢查更新')
        code, raw, _ = self.req('/api/update', {})
        self.assertEqual(code, 400)
        self.assertFalse(json.loads(raw)['ok'])

    def test_sandbox_cannot_touch_the_boot_service(self):
        """開機啟動是整台電腦的服務:副本按了會停掉或換掉真的那一個。
        擋不住的話不能真的去跑 install_service(會把這台電腦真的看板停掉),所以把它換成一叫就失敗。"""
        called = []
        with patch.object(bs.subprocess, 'run', side_effect=lambda *a, **k: called.append(a) or (_ for _ in ()).throw(
                AssertionError('副本不該去跑 install_service'))):
            code, raw, _ = self.req('/api/settings/service', {'act': 'remove'})
        self.assertEqual(called, [])
        self.assertEqual(code, 400)
        self.assertIn('副本', json.loads(raw)['msg'])

    def test_turning_on_autostart_hands_the_background_board_over(self):
        """安裝指令在背景起的看板沒有終端機:打開開機自動啟動後,不叫他按 Ctrl-C,告訴他看板會自己關、交給 launchd
        (看到裝好了自己關的是 board_server._watch)。install_service 換成假的,不真的裝。"""
        ok = Mock(returncode=0, stdout='已裝好:x.plist', stderr='')
        with patch.object(bs, 'is_real', return_value=True), patch.object(bs.subprocess, 'run', return_value=ok), \
                patch.dict(os.environ, {'JOBSALVO_LAUNCHD': ''}):
            code, raw, _ = self.req('/api/settings/service', {'act': 'install'})
        msg = json.loads(raw)['msg']
        self.assertEqual(code, 200)
        self.assertNotIn('Ctrl-C', msg)
        self.assertIn('自己關掉', msg)

    def test_settings_round_trip(self):
        legacy_resume = cf.path('resume.md')
        with open(legacy_resume, 'w', encoding='utf-8') as f:
            f.write('既有一頁履歷，保留在磁碟上')
        d = self.settings()
        s = d['settings']
        s.setdefault('resume', {})['resumes'] = [
            {'id': 'mgmt', 'name': '管理版', 'when': '主管職', 'files': {'zh': 'resume/mgmt.pdf'}, 'enabled': True}]
        s['resume']['attachments'] = [
            {'id': 'att-1', 'name': 'portfolio.pdf', 'files': {'zh': 'resume/portfolio.pdf'}, 'enabled': True}]
        s.setdefault('search', {})['exclude_words'] = ['業務']
        code, raw, _ = self.req('/api/settings', {'settings': s, 'texts': {'preferences_custom': '不要純業務', 'resume': '我是誰', 'apply_rules': '連結填 GitHub'}})
        self.assertEqual(code, 200, raw)
        d2 = self.settings()
        self.assertEqual(d2['effective']['resume']['resumes'][0]['id'], 'mgmt')
        self.assertEqual(d2['effective']['resume']['resumes'][0]['when'], '主管職')
        self.assertNotIn('base', d2['effective']['resume'])
        self.assertEqual(len(d2['effective']['resume']['attachments']), 1)
        self.assertEqual(d2['effective']['search']['exclude_words'], ['業務'])
        self.assertEqual(d2['texts']['preferences_custom'], '不要純業務')
        self.assertNotIn('resume', d2['texts'])
        self.assertEqual(d2['texts']['apply_rules'], '連結填 GitHub')
        with open(legacy_resume, encoding='utf-8') as f:
            self.assertEqual(f.read(), '既有一頁履歷，保留在磁碟上')
        self.assertIn('mgmt', [v['id'] for v in bs.page_cfg()['resumes']])      # 伺服器這個行程也跟上了
        with open(os.path.join(cf.HOME, cf.NAME), encoding='utf-8') as f:
            saved = json.load(f)
        self.assertEqual(saved['resume']['resumes'][0]['id'], 'mgmt')
        import prefs
        self.assertIn('不要純業務', prefs.hard_rules())

    def test_possible_closed_days_round_trip_through_the_settings_api(self):
        before = self.settings()
        self.assertEqual(before['effective']['board']['risk_days'], 180)
        code, raw, _ = self.req('/api/settings', {'set': {'board.risk_days': 365}})
        self.assertEqual(code, 200, raw)
        after = self.settings()
        self.assertEqual(after['effective']['board']['risk_days'], 365)
        self.assertEqual(bs.page_cfg()['risk_days'], 365)
        self.assertTrue(bs.page_cfg()['risk_days_configured'])

    def test_settings_show_doctor_results_and_fix_guidance(self):
        doctor = self.settings()['doctor']
        for check in doctor['checks']:
            self.assertTrue(check['label'])
            self.assertTrue(check['detail'])
            if check.get('required', True):
                self.assertEqual(bool(check['fix']), not check['ok'])

    def test_agent_runtime_lists_and_no_quota_spending_checks(self):
        import doctor
        cases = (
            ('only_codex', [{'runtime': 'codex', 'browser': True}], {'codex'}, True),
            ('only_command_code', [{'runtime': 'command-code', 'browser': False}], {'command_code'}, False),
            ('both', [{'runtime': 'codex', 'browser': True},
                      {'runtime': 'command-code', 'browser': False}], {'codex', 'command_code'}, True),
            ('only_claude_code', [{'runtime': 'claude-code', 'browser': False}], {'claude_code'}, False),
            ('claude_code_with_chrome', [{'runtime': 'claude-code', 'browser': True}], {'claude_code'}, True),
            ('no_browser_agent', [{'runtime': 'codex', 'browser': False},
                                  {'runtime': 'command-code', 'browser': False}],
             {'codex', 'command_code'}, False),
        )
        installed = {'codex', 'command-code', 'claude', 'git'}
        # 只算檢查本身(主執行緒)開的子行程。前面的測試存過檔,資料夾版本紀錄會在背景執行緒跑 git 快照,
        # 剛好落在這段裡就被誤算成「環境檢查開了子行程」(偶發失敗)。
        # 那個快照也會吃到下面假的 git 結果而失敗,環境檢查就多出一列「版本紀錄失敗」(偶發失敗):
        # 先把前面排著的快照取消、正在跑的等它跑完,這段裡就沒有背景的 git
        import threading
        import folder_history
        for timer in threading.enumerate():
            if isinstance(timer, threading.Timer) and getattr(timer, 'function', None) is folder_history._flush:
                timer.cancel()
                timer.join()
        mine = []

        def spawned(*a, **k):
            if threading.current_thread() is threading.main_thread():
                mine.append(a)
            return Mock(returncode=0, stdout='', stderr='')
        with patch.object(doctor.shutil, 'which', side_effect=lambda name: f'/fake/{name}' if name in installed else None), \
                patch('subprocess.run', side_effect=spawned):
            results = {}
            for name, agents, expected_runtimes, browser_ok in cases:
                result = doctor.check_environment(agents)
                checks = {item['key']: item for item in result['checks']}
                results[name] = (result, checks)
                self.assertEqual(set(checks), {'python', 'git', 'agent', 'packages', 'browser_agent'} | expected_runtimes
                                 | ({'ego'} if browser_ok else set()))
                self.assertTrue(checks['python']['ok'])
                self.assertTrue(all(checks[key]['ok'] for key in expected_runtimes))
                self.assertEqual(checks['browser_agent']['ok'], browser_ok)
                self.assertEqual(result['ok'], True)  # 瀏覽器功能是提醒,不擋找缺
                self.assertNotIn('codex_login', checks)
            self.assertIn('幫你填表與查應徵進度無法使用', results['only_command_code'][1]['browser_agent']['detail'])
            self.assertIn('找缺不受影響', results['no_browser_agent'][1]['browser_agent']['detail'])
            # 有會用瀏覽器的 agent、但 ego 還沒裝:講清楚缺什麼、怎麼補,兩家一樣(不擋找缺)
            for name in ('only_codex', 'claude_code_with_chrome'):
                ego_row = results[name][1]['ego']
                self.assertFalse(ego_row['ok'])
                self.assertFalse(ego_row['required'])
                self.assertIn('沒有安裝', ego_row['detail'])
                self.assertIn('安裝 ego lite', ego_row['fix'])
            # 不派 agent 跑一次(那會花額度,額度是使用者自己的事):只准讀本機的登入狀態
            # (資料夾版本紀錄那一列只用 git 讀本機 repo 狀態,不花額度)
            # 防火牆那一項讀本機的 Tailscale 位址(只讀、不花額度,#295)
            agent_calls = [argv for argv in mine if os.path.basename(argv[0][0]) not in ('git', 'Tailscale', 'tailscale')]
            self.assertTrue(all(tuple(argv[0][1:]) in (('login', 'status'), ('auth', 'status')) for argv in agent_calls), mine)
            self.assertTrue(all(argv[0][1] in ('rev-parse', 'remote', 'log') for argv in mine
                                if os.path.basename(argv[0][0]) == 'git'), mine)

            installed.remove('command-code')
            missing_command = doctor.check_environment(
                [{'runtime': 'command-code', 'browser': False}]
            )
        self.assertFalse(missing_command['ok'])
        missing = {item['key']: item for item in missing_command['checks']}
        self.assertFalse(missing['command_code']['ok'])
        self.assertNotIn('codex', missing)

    def test_use_agent_puts_the_usable_runtime_first_and_refuses_unusable(self):
        import doctor
        with patch.object(doctor, 'agent_state', side_effect=lambda rt: (rt == 'claude-code', '')):
            code, raw, _ = self.req('/api/settings/use_agent', {'runtime': 'codex'})
            self.assertEqual(code, 400)
            before = [a['runtime'] for a in self.settings()['effective']['agent']['agents']]
            code, raw, _ = self.req('/api/settings/use_agent', {'runtime': 'claude-code'})
            self.assertEqual(code, 200, raw)
        after = self.settings()['effective']['agent']['agents']
        self.assertEqual([a['runtime'] for a in after], ['claude-code'] + before)   # 原本的清單留著,只是排到後面
        self.assertEqual(len({a['id'] for a in after}), len(after))

    def test_use_agent_without_chrome_leaves_who_uses_chrome_alone(self):
        # 改用一個不能操作 Chrome 的:原本用 Chrome 的那一個照舊(只有換成能用 Chrome 的才把舊的關掉)
        import doctor
        before = self.settings()['effective']['agent']['agents']
        with patch.object(doctor, 'agent_state', side_effect=lambda rt: (True, '')):
            code, raw, _ = self.req('/api/settings/use_agent', {'runtime': 'command-code'})
        self.assertEqual(code, 200, raw)
        after = self.settings()['effective']['agent']['agents']
        self.assertEqual((after[0]['runtime'], after[0]['browser']), ('command-code', False))
        self.assertEqual([a.get('browser') for a in after[1:]], [a.get('browser') for a in before])

    def test_find_minutes_saves_blank_as_no_limit_and_refuses_nonsense(self):
        code, raw, _ = self.req('/api/settings/find_minutes', {'minutes': ''})
        self.assertEqual(code, 200, raw)
        self.assertEqual(self.settings()['effective']['search']['find_minutes'], 0)
        code, raw, _ = self.req('/api/settings/find_minutes', {'minutes': '很久'})
        self.assertEqual(code, 400, raw)
        self.assertEqual(self.settings()['effective']['search']['find_minutes'], 0)

    def test_health_endpoint_identifies_jobsalvo(self):
        code, raw, _ = self.req('/api/health')
        self.assertEqual(code, 200, raw)
        health = json.loads(raw)
        self.assertEqual({'ok': health.get('ok'), 'app': health.get('app')},
                         {'ok': True, 'app': 'jobsalvo'})
        self.assertEqual(health.get('version'), bs.code_version())

    def test_find_uses_uploaded_resume_and_prompts_when_extraction_fails(self):
        for name in ('resume.md', '.resume-paste.md'):
            path = os.path.join(cf.HOME, name)
            if os.path.exists(path):
                os.remove(path)
        settings = self.settings()['settings']
        settings['resume']['resumes'] = []
        self.assertEqual(self.req('/api/settings', {'settings': settings})[0], 200)
        code, raw, _ = self.req('/api/run/research', {'mode': 'wide'})
        self.assertEqual(code, 400, raw)
        self.assertIn('上傳', json.loads(raw)['msg'])

        resume_text = '具備 Python 資料工程與測試經驗'
        code, raw = self.put('/api/file?path=resume/feature-48.txt', resume_text.encode('utf-8'))
        self.assertEqual(code, 200, raw)
        settings = self.settings()['settings']
        settings['resume']['langs'] = ['zh']
        settings['resume']['attachments'] = []
        settings['resume']['resumes'] = [{
            'id': 'feature-48', 'name': '測試履歷', 'enabled': True,
            'files': {'zh': 'resume/feature-48.txt'},
        }]
        self.assertEqual(self.req('/api/settings', {'settings': settings})[0], 200)
        import prefs, settings_api
        material, problem = settings_api.resume_material()
        self.assertFalse(problem)
        self.assertIn(resume_text, material)
        with patch.dict(os.environ, {'JOBSALVO_RESUME_TEXT': material}):
            self.assertIn(resume_text, prefs.resume())
        with patch.object(bs.subprocess, 'Popen') as popen:
            popen.return_value.poll.return_value = None
            code, raw, _ = self.req('/api/run/research', {'mode': 'wide'})
        self.assertEqual(code, 200, raw)
        self.assertEqual(popen.call_args.kwargs['env']['JOBSALVO_RESUME_TEXT'], material)

        # 抽不出字的 PDF:抽字換成直接說讀不了(真的開子程序讀,CI 加量覆蓋率時光啟動就可能超過請求的 10 秒)
        self.enterContext(patch.object(settings_api, 'pdf_text', side_effect=ValueError('PDF 無法讀取')))
        code, raw = self.put('/api/file?path=resume/scanned.pdf', b'%PDF-not-readable')
        self.assertEqual(code, 200, raw)
        settings['resume']['resumes'][0]['files']['zh'] = 'resume/scanned.pdf'
        self.assertEqual(self.req('/api/settings', {'settings': settings})[0], 200)
        code, raw, _ = self.req('/api/run/research', {'mode': 'wide'})
        self.assertEqual(code, 422, raw)
        self.assertTrue(json.loads(raw)['paste_resume'])
        self.assertIn('貼', json.loads(raw)['msg'])
        pasted_text = '測試貼上的 Python 經歷'
        with patch.object(bs.subprocess, 'Popen') as popen:
            popen.return_value.poll.return_value = None
            code, raw, _ = self.req('/api/run/research', {'mode': 'wide', 'resume_text': pasted_text})
        self.assertEqual(code, 200, raw)
        self.assertEqual(popen.call_args.kwargs['env']['JOBSALVO_RESUME_TEXT'], pasted_text)
        material, problem = settings_api.resume_material()
        self.assertFalse(problem)
        self.assertIn(pasted_text, material)
        with patch.object(bs.subprocess, 'Popen') as popen:
            popen.return_value.poll.return_value = None
            code, raw, _ = self.req('/api/run/suggest', {})
        self.assertEqual(code, 200, raw)
        self.assertEqual(popen.call_args.kwargs['env']['JOBSALVO_RESUME_TEXT'], material)

    def test_legacy_resume_md_remains_agent_material(self):
        legacy_text = '舊版一頁履歷中的資料工程經驗'
        with open(os.path.join(cf.HOME, 'resume.md'), 'w', encoding='utf-8') as f:
            f.write(legacy_text)
        import prefs, settings_api
        material, problem = settings_api.resume_material()
        self.assertFalse(problem)
        self.assertIn(legacy_text, material)
        with patch.dict(os.environ, {'JOBSALVO_RESUME_TEXT': ''}):
            self.assertIn(legacy_text, prefs.resume())

    def test_apply_or_replies_prompt_for_a_live_browser_only_when_needed(self):
        with patch.object(bs, 'is_real', return_value=True), \
             patch('chrome_door.configured', return_value=False):
            code, raw, _ = self.req('/api/run/replies', {})
        self.assertEqual(code, 409, raw)
        self.assertTrue(json.loads(raw)['needs_browser'])

    def test_eye_on_a_page_whose_workspace_is_gone_marks_the_card_for_refill(self):
        # 👀 截不到、而且那張卡的工作區已經不在 ego 裡:那一頁一定不在了,卡上改成要重填(不再寫「填好了」)
        import chrome_door
        u = 'https://jobs.example/1'
        fb = {u: {'app': 'ship', 'ds': 'parked', 'apply': {'stage': 'fill', 'at': '2026-09-29T14:00:00', 'tab_id': '7:p1',
                                                            'workspace': {'id': 7, 'name': 'w', 'page': 'p1'}}}}
        with patch('board_doc.is_live', return_value=True), \
             patch('board_doc.load', return_value={'fb': json.dumps(fb)}), \
             patch.object(chrome_door, 'close_if_idle'), \
             patch.object(bs.bd, 'set_fb', side_effect=lambda fn, live=None, by='': fn(fb)):
            with patch.object(chrome_door, 'gone_pages', return_value=[]):
                self.assertFalse(bs.page_gone(u))                 # 工作區還在:可能只是一時截不到,不動
            with patch.object(chrome_door, 'gone_pages', return_value=[u]):
                self.assertTrue(bs.page_gone(u))
        self.assertEqual(fb[u]['ds'], 'gone')
        self.assertIn('要重填', fb[u]['apply']['issues'][0])

    def test_preference_note_keeps_his_rules_and_agent_assumptions_distinct(self):
        import tempfile
        import prefs

        home = self.enterContext(tempfile.TemporaryDirectory(prefix='preference-note-test-'))
        raw_prefs = os.path.join(home, 'prefs.md')
        note = os.path.join(home, 'preference-note.md')
        with patch.object(cf, 'HOME', home), patch.object(cf, 'PREFS', raw_prefs), \
                patch.object(cf, 'PREFERENCE_NOTE', note), patch.object(prefs, 'PREF', note), \
                patch.object(cf, 'APPLY_RULES', os.path.join(home, 'apply-rules.md')):
            with open(note, 'w', encoding='utf-8') as f:
                f.write('# 偏好筆記\n\n## 使用者自訂\n\n不要外派\n\n'
                        '## Agent 假設\n\n'
                        '- 穩定假設｜出處：卡片甲；支持 1；反例 0\n'
                        '- 待修正假設｜出處：卡片乙；支持 1；反例 0\n')
            d = self.settings()
            self.assertIn('穩定假設', d['texts']['preferences_agent'])
            self.assertNotIn('穩定假設', d['texts']['preferences_custom'])

            edited_agent = ('- 穩定假設｜出處：卡片甲；支持 1；反例 0\n'
                            '- 我改過的假設｜出處：卡片乙；支持 2；反例 0')
            code, raw, _ = self.req('/api/settings', {
                'texts': {'preferences_custom': '不要外派\n不要值班',
                          'preferences_agent': edited_agent}})
            self.assertEqual(code, 200, raw)
            saved = self.settings()['texts']
            self.assertIn('不要值班', saved['preferences_custom'])
            self.assertIn('我改過的假設', saved['preferences_custom'])
            self.assertNotIn('待修正假設', saved['preferences_agent'])
            self.assertIn('穩定假設', saved['preferences_agent'])

    def test_each_research_skill_can_be_saved_and_bad_paths_are_refused(self):
        import settings_api as sa
        tasks = tuple(cf.RESEARCH_SKILLS)
        skills = {}
        for task in tasks:
            rel = f'custom/skills/research-{task}.md'
            path = sa.safe_rel(rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f'Custom {task} skill')
            skills[task] = rel

        settings = self.settings()['settings']
        settings['research'] = {'skills': skills}
        code, raw, _ = self.req('/api/settings', {'settings': settings})
        self.assertEqual(code, 200, raw)
        saved = self.settings()
        self.assertEqual(saved['settings']['research']['skills'], skills)
        self.assertEqual(saved['effective']['research']['skills'], skills)

        settings['research']['skills']['deep'] = 'custom/skills/missing.md'
        code, raw, _ = self.req('/api/settings', {'settings': settings})
        self.assertEqual(code, 400)
        self.assertIn('選的做法找不到', json.loads(raw)['msg'])        # 不講 skill(GLOSSARY)

        settings['research']['skills'] = {task: '' for task in tasks}
        code, raw, _ = self.req('/api/settings', {'settings': settings})
        self.assertEqual(code, 200, raw)
        self.assertEqual(self.settings()['effective']['research']['skills'],
                         {task: '' for task in tasks})

    def test_settings_exposes_each_product_research_skill(self):
        import settings_api as sa

        tasks = {item['key']: item for item in self.settings()['research_skill_tasks']}
        self.assertEqual(set(tasks), set(cf.RESEARCH_SKILLS))
        for key, definition in cf.RESEARCH_SKILLS.items():
            with self.subTest(key=key):
                source = os.path.join(os.path.dirname(sa.__file__), 'research_skills',
                                      definition['file'])
                with open(source, encoding='utf-8') as f:
                    self.assertEqual(tasks[key].get('default_content'), f.read())
    def test_page_cfg_exposes_resume_and_attachment_preview_sources(self):
        settings = self.settings()['settings']
        resume = settings.setdefault('resume', {})
        resume['langs'] = ['zh', 'en']
        resume['resumes'] = [
            {'id': 'general', 'name': '通用版', 'enabled': True,
             'files': {'zh': 'resume/general-zh.pdf', 'en': 'resume/general-en.pdf'}},
            {'id': 'unused', 'name': '停用版', 'enabled': False,
             'files': {'zh': 'resume/unused.pdf'}},
        ]
        resume['attachments'] = [
            {'id': 'portfolio', 'name': '作品集', 'enabled': True,
             'resume_ids': ['general'],
             'files': {'zh': 'resume/portfolio-zh.pdf', 'en': 'resume/portfolio-en.md'}},
            {'id': 'unused', 'name': '停用附件', 'enabled': False,
             'files': {'zh': 'resume/unused-attachment.pdf'}},
        ]
        code, raw, _ = self.req('/api/settings', {'settings': settings})
        self.assertEqual(code, 200, raw)

        page = self.req('/')[1].decode('utf-8')
        self.assertNotIn('一頁履歷原文', page)
        payload = page.split('<script id="data-jobs" type="application/json">', 1)[1].split('</script>', 1)[0]
        cfg = json.loads(payload)['cfg']
        self.assertEqual(cfg['resumes'], [{'id': 'general', 'name': '通用版', 'when': ''}])
        # 卡上能不能預覽原始檔(PDF、markdown 都可以)由後台的要寄的檔案給;停用的履歷、附件不出現
        import ship
        for lang in ('zh', 'en'):
            got = ship.card_files({'id': 'test://jobs/preview', 'resume': {'recommend': 'general', 'lang': lang}}, {})
            self.assertEqual([(f['name'], f['preview']) for f in got['files']], [('通用版', True), ('作品集', True)])
            self.assertEqual([c['id'] for c in got['choices']], ['general'])

    def test_changing_what_a_filled_card_sends_means_the_page_has_the_old_file(self):
        """改原始履歷檔、設定裡刪掉語言:要寄的檔案真的變了的卡 → 上傳的是舊檔;沒變的不動
        (狀態表額外抓到 5、修正 6、16)。"""
        import delivery_state as ds
        settings = self.settings()['settings']
        resume = settings.setdefault('resume', {})
        resume['langs'] = ['zh', 'en']
        resume['resumes'] = [{'id': 'general', 'name': '通用版', 'enabled': True,
                              'files': {'zh': 'resume/general-zh.pdf', 'en': 'resume/general-en.pdf'}}]
        resume['attachments'] = []
        for name in ('general-zh.pdf', 'general-en.pdf'):
            self.addCleanup(lambda n=name: os.path.exists(cf.path('resume/' + n)) and os.remove(cf.path('resume/' + n)))
        self.assertEqual(self.put('/api/file?path=resume/general-zh.pdf', b'%PDF-1.4 zh')[0], 200)
        self.assertEqual(self.put('/api/file?path=resume/general-en.pdf', b'%PDF-1.4 en')[0], 200)
        code, raw, _ = self.req('/api/settings', {'settings': settings})
        self.assertEqual(code, 200, raw)
        zh, en = JOBS[0]['id'], JOBS[1]['id']
        filled = {'app': 'ship', 'ds': 'parked', 'resume_id': 'general', 'apply': {'stage': 'fill', 'tab_id': '7'}}
        bs.bd.set_fb(lambda f: (f.__setitem__(zh, dict(filled, lang='zh')), f.__setitem__(en, dict(filled, lang='en'))),
                     live=self.path)
        self.assertEqual(self.put('/api/file?path=resume/general-zh.pdf', b'%PDF-1.4 zh again')[0], 200)
        fb = read_fb(self.path)
        self.assertEqual((ds.state(fb[zh]), ds.state(fb[en])), ('stale', 'parked'))
        settings = self.settings()['settings']
        settings['resume']['langs'] = ['zh']
        settings['resume']['resumes'][0]['files'] = {'zh': 'resume/general-zh.pdf'}
        code, raw, _ = self.req('/api/settings', {'settings': settings})
        self.assertEqual(code, 200, raw)
        self.assertEqual(ds.state(read_fb(self.path)[en]), 'stale')          # 英文拿掉了:改寄中文

    def test_ordered_agent_settings_round_trip_and_browser_validation(self):
        s = self.settings()['settings']
        s['agent'] = {'name': 'Beacon', 'agents': [
            {'id': 'text-first', 'runtime': 'command-code', 'model': 'model-a', 'effort': 'high', 'speed': 'standard', 'browser': False},
            {'id': 'browser-next', 'runtime': 'codex', 'model': 'model-b', 'effort': 'max', 'speed': 'standard', 'browser': True},
        ]}
        code, raw, _ = self.req('/api/settings', {'settings': s})
        self.assertEqual(code, 200, raw)
        self.assertEqual(self.settings()['effective']['agent']['agents'], s['agent']['agents'])

        s['agent']['agents'][0]['browser'] = True
        code, raw, _ = self.req('/api/settings', {'settings': s})
        self.assertEqual(code, 400)
        self.assertIn('Command Code', json.loads(raw)['msg'])

        s['agent']['agents'][0]['browser'] = False
        s['agent']['agents'][1]['id'] = 'text-first'
        code, _, _ = self.req('/api/settings', {'settings': s})
        self.assertEqual(code, 400)

    def test_agent_speed_defaults_and_round_trips_through_settings(self):
        initial = self.settings()
        self.assertEqual(initial['effective']['agent']['agents'][0]['speed'], 'standard')

        settings = initial['settings']
        settings['agent'] = {'name': 'Beacon', 'agents': [
            {'id': 'codex-first', 'runtime': 'codex', 'model': '', 'effort': 'max', 'browser': False},
            {'id': 'text-next', 'runtime': 'command-code', 'model': '', 'effort': 'high', 'browser': False},
        ]}
        code, raw, _ = self.req('/api/settings', {'settings': settings})
        self.assertEqual(code, 200, raw)
        entries = self.settings()['effective']['agent']['agents']
        self.assertEqual([entry['speed'] for entry in entries], ['standard', 'standard'])

        settings = self.settings()['settings']
        settings['agent']['agents'][0]['speed'] = 'fast'
        code, raw, _ = self.req('/api/settings', {'settings': settings})
        self.assertEqual(code, 200, raw)
        entries = self.settings()['effective']['agent']['agents']
        self.assertEqual([entry['speed'] for entry in entries], ['fast', 'standard'])

    def test_invalid_codex_speed_is_refused(self):
        settings = self.settings()['settings']
        settings['agent'] = {'name': 'Beacon', 'agents': [
            {'id': 'codex', 'runtime': 'codex', 'model': '', 'effort': 'max',
             'speed': 'turbo', 'browser': False},
        ]}
        code, _, _ = self.req('/api/settings', {'settings': settings})
        self.assertEqual(code, 400)

    def test_bad_settings_are_refused(self):
        s = self.settings()['settings']
        s.setdefault('resume', {})['resumes'] = [{'id': 'Bad Id', 'name': 'x'}]
        code, raw, _ = self.req('/api/settings', {'settings': s})
        self.assertEqual(code, 400)
        self.assertIn('履歷代號', json.loads(raw)['msg'])

    def test_uploads_stay_inside_resume_and_custom(self):
        self.assertEqual(self.put('/api/file?path=../evil.pdf', b'x')[0], 400)
        self.assertEqual(self.put('/api/file?path=resume/run.sh', b'x')[0], 400)
        self.assertEqual(self.put('/api/file?path=board.html', b'x')[0], 400)
        code, d = self.put('/api/file?path=resume/test-zh.pdf', b'%PDF-1.4')
        self.assertEqual((code, d['path']), (200, 'resume/test-zh.pdf'))
        self.assertTrue(os.path.isfile(cf.path('resume/test-zh.pdf')))
        os.remove(cf.path('resume/test-zh.pdf'))

    def test_card_can_use_its_own_file(self):
        u = JOBS[0]['id']
        code, d = self.put('/api/card-file?u=' + urllib.parse.quote(u) + '&name=mine.pdf', b'%PDF-1.4 mine')
        self.assertEqual(code, 200, d)
        fb = read_fb(self.path)
        self.assertEqual(fb[u]['custom_file'], d['path'])
        self.assertEqual(fb[u]['s'], 'like')                      # 原本的標記不動
        import ship
        # 這張挑的履歷用他自己傳的檔代替(沒挑履歷的卡什麼都不寄,見 ship.pick)
        with patch.object(cf, 'RESUMES', {'general': {'id': 'general', 'files': {}, 'enabled': True}}):
            src, _, own = ship.sources({'id': u, 'resume': {'recommend': 'general'}}, fb)
        self.assertTrue(own and src.endswith('mine.pdf'))

    def test_own_file_after_filling_means_the_page_has_the_old_one(self):
        # 狀態表額外抓到 5:舊版「這張用自己的檔」上傳,以前不會把已填好的卡標成舊檔
        import delivery_state as ds
        u = JOBS[0]['id']
        bs.bd.set_fb(lambda f: f[u].update(app='ship', ds='parked', apply={'stage': 'fill', 'tab_id': '7'}), live=self.path)
        code, d = self.put('/api/card-file?u=' + urllib.parse.quote(u) + '&name=mine.pdf', b'%PDF-1.4 mine')
        self.assertEqual(code, 200, d)
        self.assertEqual(ds.state(read_fb(self.path)[u]), 'stale')

    def test_customize_settings_files_dispatch_and_run_status(self):
        import settings_api as sa
        u = JOBS[0]['id']
        resume_rel = 'resume/customize-route-test.pdf'
        resume_path = sa.safe_rel(resume_rel)
        os.makedirs(os.path.dirname(resume_path), exist_ok=True)
        with open(resume_path, 'wb') as f:
            f.write(b'%PDF-1.4\npages=1\nsource')
        try:
            code, raw, _ = self.req('/api/settings/skill', {
                'name': 'Route test skill', 'content': 'Keep only supported facts.'})
            self.assertEqual(code, 200, raw)
            skill = json.loads(raw)['skill']
            settings = self.settings()['settings']
            settings['resume']['langs'] = ['zh']
            settings['resume']['resumes'] = [{
                'id': 'general', 'name': '通用版', 'files': {'zh': resume_rel},
                'enabled': True, 'skill': skill['path'],
            }]
            settings['resume']['attachments'] = []
            self.assertEqual(self.req('/api/settings', {'settings': settings})[0], 200)
            make_board(self.path, {u: {'app': 'ready', 'resume_id': 'general', 'lang': 'zh'}})

            code, raw, _ = self.req('/api/customize/files?u=' + urllib.parse.quote(u))
            self.assertEqual(code, 200, raw)
            files = json.loads(raw)['files']
            self.assertEqual([x['id'] for x in files], ['resume:general:zh'])
            self.assertTrue(files[0]['default_checked'])
            self.assertEqual(files[0]['skill_name'], 'Route test skill')
            self.assertEqual(self.req('/api/run/customize', {'url': u, 'items': []})[0], 400)

            code, raw, _ = self.req('/api/run/customize', {'url': u, 'items': ['resume:general:zh']})
            self.assertEqual(code, 200, raw)
            status = self.wait('customize', lambda s: s.get('phase') == 'done')
            self.assertEqual(status['url'], u)
        finally:
            with contextlib.suppress(OSError):   # 測試半路失敗時可能還沒寫出來
                os.remove(resume_path)

    def wait(self, kind, until, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            st = self.rev()[kind]
            if until(st):
                return st
            time.sleep(0.1)
        self.fail('等不到:%r' % st)

    def test_paste_urls_adds_cards(self):
        code, raw, _ = self.req('/api/run/add', {'text': 'https://ex.test/new/1\nhttps://ex.test/new/2 還有字'})
        self.assertEqual(code, 200, raw)
        self.wait('add', lambda s: s.get('phase') == 'done')
        ids = {j['id'] for j in read_board(self.path)['data']['jobs']}
        self.assertTrue({'https://ex.test/new/1', 'https://ex.test/new/2'} <= ids)

    def test_paste_needs_a_url(self):
        self.assertEqual(self.req('/api/run/add', {'text': '沒有網址'})[0], 400)

    def test_suggest_categories_lands_in_settings_page(self):
        self.assertEqual(self.req('/api/run/suggest', {})[0], 200)
        self.wait('suggest', lambda s: s.get('phase') == 'done')
        self.assertTrue(self.settings()['suggest']['categories'])

    def test_log_is_readable_from_the_board(self):
        code, raw, _ = self.req('/api/log?kind=prep')
        self.assertEqual(code, 200)
        self.assertEqual(self.req('/api/log?kind=../../etc')[1].decode(), '(沒有這一種)')


class InterviewBank(unittest.TestCase):
    def test_script_round_trip_is_stable(self):
        import interview_bank as ib
        t = '開場\n\n## 60 秒版\n第一段\n\n第二段\n\n## 90 秒版\n另一版'
        once = ib.blocks_to_script(ib.script_to_blocks(t))
        self.assertEqual(ib.blocks_to_script(ib.script_to_blocks(once)), once)
        self.assertEqual([b[0] for b in ib.script_to_blocks(t)], ['s', 'h', 's', 'h', 's'])

    def test_no_html_gets_in(self):
        import interview_bank as ib
        it = ib.from_form({'t': 'x', 'ask': '<script>alert(1)</script>', 'script': '## <b>h</b>\n\n<img src=x>'})
        self.assertNotIn('<script', it['ask'])
        self.assertNotIn('<b>', it['b'][0][1])

    def test_links_survive_an_edit(self):
        import interview_bank as ib
        url = 'https://youtu.be/x?t=866&a=1'
        old = {'id': 'q1', 't': 'x', 'focus': f'第一集 <a href="{url.replace("&", "&amp;")}" target="_blank">14:26</a></p>'}
        form = ib.to_form(old)
        self.assertEqual(form['focus'], f'第一集 [14:26]({url})')   # 舊的 <a> 打開編輯框是 [文字](網址)
        self.assertEqual(ib.to_form(ib.from_form(form, old))['focus'], form['focus'])   # 存回去再打開，一字不差

    def test_later_stage_without_script_is_rejected(self):
        import interview_bank as ib
        with self.assertRaisesRegex(ValueError, '還沒有逐字稿'):
            ib.from_form({'t': 'x', 'stage': 'ok'})

    def test_put_edit_delete(self):
        import interview_bank as ib
        b = ib.apply(None, 'put', {'t': '自我介紹', 'stage': 'wip', 'script': 'hi', 'cat': '新類別'})
        qid = b['items'][0]['id']
        self.assertIn('新類別', b['cats'])
        b = ib.apply(b, 'put', dict(ib.to_form(b['items'][0]), t='改過'))
        self.assertEqual((len(b['items']), b['items'][0]['t'], b['items'][0]['id']), (1, '改過', qid))
        self.assertEqual(ib.apply(b, 'del', item_id=qid)['items'], [])


class BankHttp(tb.HttpBase):
    def test_bank_endpoint_writes_board_data(self):
        code, raw, _ = self.req('/api/bank', {'op': 'put', 'item': {'t': '為什麼換工作', 'stage': 'todo'}})
        self.assertEqual(code, 200, raw)
        bank = read_board(self.path)['data']['bank']
        self.assertEqual(bank['items'][0]['t'], '為什麼換工作')
        qid = bank['items'][0]['id']
        code, raw, _ = self.req('/api/bank/form?id=' + qid)
        self.assertEqual(json.loads(raw)['form']['t'], '為什麼換工作')


class FileWritesFromTheBoard(tb.HttpBase):
    """看板上傳、刪除他資料夾裡的檔:每一條擋的路和寫的路都走過(以前刪檔這支一次都沒測過)。"""

    def setUp(self):
        super().setUp()
        self.addCleanup(shutil.rmtree, cf.path('custom'), True)

    def send(self, method, path, data=None, headers=None):
        return self.req(path, ua='Mozilla/5.0', headers=headers, method=method, data=data)[:2]

    def test_delete_removes_only_an_existing_file(self):
        rel = 'custom/delete-me.pdf'
        self.assertEqual(self.send('PUT', '/api/file?path=' + rel, b'%PDF-1.4')[0], 200)
        code, raw = self.send('DELETE', '/api/file?path=' + rel)
        self.assertEqual((code, json.loads(raw)['ok']), (200, True))
        self.assertFalse(os.path.exists(cf.path(rel)))
        code, raw = self.send('DELETE', '/api/file?path=' + rel)
        self.assertEqual((code, json.loads(raw)['ok']), (200, False))
        self.assertEqual(self.send('DELETE', '/api/nothing')[0], 404)

    def test_other_sites_cannot_write_or_delete(self):
        rel = 'custom/keep-me.pdf'
        self.assertEqual(self.send('PUT', '/api/file?path=' + rel, b'%PDF-1.4')[0], 200)
        evil = {'Origin': 'http://evil.example'}
        self.assertEqual(self.send('DELETE', '/api/file?path=' + rel, headers=evil)[0], 403)
        self.assertEqual(self.send('PUT', '/api/file?path=' + rel, b'x', headers=evil)[0], 403)
        with open(cf.path(rel), 'rb') as f:
            self.assertEqual(f.read(), b'%PDF-1.4')

    def test_card_file_needs_a_card_and_a_resume_like_file(self):
        u = JOBS[0]['id']
        self.assertEqual(self.send('PUT', '/api/card-file?name=x.pdf', b'%PDF-1.4')[0], 400)
        self.assertEqual(self.send('PUT', '/api/card-file?u=%s&name=run.sh' % urllib.parse.quote(u, safe=''), b'x')[0], 400)
        self.assertEqual(self.send('PUT', '/api/elsewhere', b'x')[0], 404)
        self.assertNotIn('custom_file', read_fb(self.path).get(u, {}))

    def test_customized_file_upload_goes_through_the_customize_rules(self):
        u = urllib.parse.quote(JOBS[0]['id'], safe='')
        path = '/api/card-file?u=%s&name=x.pdf&item=resume:general:zh' % u
        with patch('customize.upload_custom', return_value=(None, '這份檔現在不收')):
            code, raw = self.send('PUT', path, b'%PDF-1.4')
        self.assertEqual((code, json.loads(raw)['msg']), (400, '這份檔現在不收'))
        with patch('customize.upload_custom', return_value=('custom/x.pdf', '')) as up:
            code, raw = self.send('PUT', path, b'%PDF-1.4')
        self.assertEqual((code, json.loads(raw)['path']), (200, 'custom/x.pdf'))
        self.assertEqual(up.call_args.args[:4], (JOBS[0]['id'], 'resume:general:zh', 'x.pdf', b'%PDF-1.4'))

    def test_interview_bank_refuses_an_unknown_operation(self):
        code, raw = self.send('POST', '/api/bank', json.dumps({'op': 'wipe'}).encode('utf-8'),
                              headers={'Content-Type': 'application/json'})
        self.assertEqual(code, 400)
        self.assertIn('不知道要做什麼', json.loads(raw)['msg'])


class SaveKicksTheAutoFlow(tb.HttpBase):
    def test_a_save_with_a_click_wakes_the_auto_flow(self):
        # 看板上按了一下(送事件)存檔:自動流程馬上看一次,不用等下一分鐘
        pilot = Mock()
        u = JOBS[0]['id']
        with patch.object(bs, 'PILOT', pilot):
            code, raw, _ = self.req('/api/save', {'cmds': [{'u': u, 'ev': 'leave', 'set': {'s': 'like', 'n': '備註'}}]})
        self.assertEqual(code, 200, raw)
        pilot.kick.assert_called_once()
        self.assertEqual(read_fb(self.path)[u]['n'], '備註')


class CustomizeAndControlFromTheBoard(tb.HttpBase):
    """看板上收下、退回、清掉客製版,和暫停/停止跑到一半的工作:伺服器把每一種送到對的地方,不認得的擋下。"""

    def post(self, path, body, ua='Mozilla/5.0'):
        return self.req(path, body, ua=ua)

    def test_each_customize_decision_goes_to_its_own_rule(self):
        u = JOBS[0]['id']
        for op, fn, rebuild in (('accept', 'accept', True), ('reject', 'reject', False), ('clear', 'clear', True)):
            with self.subTest(op):
                self.builds.clear()
                with patch('customize.' + fn, return_value=(True, '好了')) as call:
                    code, raw, _ = self.post('/api/customize', {'op': op, 'url': u, 'item': 'resume:general:zh'})
                self.assertEqual((code, json.loads(raw)['ok']), (200, True))
                self.assertEqual(call.call_args.args[:2], (u, 'resume:general:zh'))
                self.assertEqual(bool(self.builds), rebuild)
        with patch('customize.accept', return_value=(False, '這份不在等你看')):
            code, raw, _ = self.post('/api/customize', {'op': 'accept', 'url': u, 'item': 'x'})
        self.assertEqual((code, json.loads(raw)['msg']), (400, '這份不在等你看'))
        code, raw, _ = self.post('/api/customize', {'op': 'wipe'})
        self.assertEqual(code, 400)

    def test_unknown_paths_are_404(self):
        self.assertEqual(self.post('/api/nowhere', {})[0], 404)
