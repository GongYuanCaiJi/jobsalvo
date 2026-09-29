# -*- coding: utf-8 -*-
"""使用者只碰網頁就能做完的事:設定頁讀寫、上傳、卡片自己的檔、貼網址加入、分類建議、看紀錄、104 對帳。"""
import os, sys, json, time, shutil, unittest, urllib.request
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
        r = urllib.request.Request(self.base + path, data=data, method='PUT', headers={'User-Agent': ua})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b'{}')

    def delete(self, path):
        r = urllib.request.Request(self.base + path, method='DELETE', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status

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

    def test_settings_round_trip(self):
        legacy_resume = cf.path('resume.md')
        with open(legacy_resume, 'w', encoding='utf-8') as f:
            f.write('既有一頁履歷，保留在磁碟上')
        d = self.settings()
        s = d['settings']
        s.setdefault('resume', {})['resumes'] = []
        s['resume']['attachments'] = []
        s.setdefault('resume', {})['variants'] = {
            'mgmt': {'label': '管理版', 'when': '主管職', 'zh': 'resume/mgmt.pdf',
                     'attachments': ['resume/portfolio.pdf']}}
        s['fetch'] = {'cmd': 'obsolete-fetch-command'}
        s.setdefault('search', {})['exclude_words'] = ['業務']
        code, raw, _ = self.req('/api/settings', {'settings': s, 'texts': {'rules': '不要純業務', 'resume': '我是誰', 'apply_rules': '連結填 GitHub'}})
        self.assertEqual(code, 200, raw)
        d2 = self.settings()
        self.assertNotIn('variants', d2['effective']['resume'])
        self.assertEqual(d2['effective']['resume']['resumes'][0]['id'], 'mgmt')
        self.assertEqual(d2['effective']['resume']['resumes'][0]['when'], '主管職')
        self.assertNotIn('base', d2['effective']['resume'])
        self.assertEqual(len(d2['effective']['resume']['attachments']), 1)
        self.assertNotIn('fetch', d2['effective'])
        self.assertEqual(d2['effective']['search']['exclude_words'], ['業務'])
        self.assertEqual(d2['texts']['rules'], '不要純業務')
        self.assertNotIn('resume', d2['texts'])
        self.assertEqual(d2['texts']['apply_rules'], '連結填 GitHub')
        with open(legacy_resume, encoding='utf-8') as f:
            self.assertEqual(f.read(), '既有一頁履歷，保留在磁碟上')
        self.assertIn('mgmt', [v['id'] for v in bs.page_cfg()['resumes']])      # 伺服器這個行程也跟上了
        with open(os.path.join(cf.HOME, cf.NAME), encoding='utf-8') as f:
            saved = json.load(f)
        self.assertNotIn('variants', saved['resume'])
        self.assertNotIn('fetch', saved)
        import prefs
        self.assertIn('不要純業務', prefs.hard_rules())

    def test_settings_show_doctor_results_and_fix_guidance(self):
        doctor = self.settings()['doctor']
        runtimes = {x['runtime'] for x in self.settings()['effective']['agent']['agents']}
        expected = {'python', 'chrome', 'git', 'agent', 'packages', 'browser_agent'}
        import doctor as dr      # 只算環境檢查認得的種類(前面的測試可能留下別的 runtime 名稱)
        expected.update({x.replace('-', '_') for x in runtimes if x in dr.RUNTIMES})
        # 「幫你填表用的 Chrome」只在有一個會用 Chrome、而且真的能用的 agent 時才列
        if any(x.get('browser') is True and x.get('runtime') in ('codex', 'claude-code') and dr.agent_state(x['runtime'])[0]
               for x in self.settings()['effective']['agent']['agents']):
            expected.add('agent_chrome')
        self.assertEqual({x['key'] for x in doctor['checks']}, expected)
        self.assertNotIn('codex_login', {x['key'] for x in doctor['checks']})
        self.assertEqual(doctor['ok'], all(x['ok'] for x in doctor['checks'] if x.get('required', True)))
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
        # 剛好落在這段裡就被誤算成「環境檢查開了子行程」(偶發失敗)
        import threading
        mine = []

        def spawned(*a, **k):
            if threading.current_thread() is threading.main_thread():
                mine.append(a)
            return Mock(returncode=0, stdout='', stderr='')
        with patch('chrome_bin.find', return_value='/fake/chrome'), \
                patch.object(doctor.shutil, 'which', side_effect=lambda name: f'/fake/{name}' if name in installed else None), \
                patch('agent_chrome.conf', return_value={}), \
                patch('agent_run.claude_paired_device', return_value=None), \
                patch('subprocess.run', side_effect=spawned):
            results = {}
            for name, agents, expected_runtimes, browser_ok in cases:
                result = doctor.check_environment(agents)
                checks = {item['key']: item for item in result['checks']}
                results[name] = (result, checks)
                self.assertEqual(set(checks), {'python', 'chrome', 'git', 'agent', 'packages', 'browser_agent'} | expected_runtimes
                                 | ({'agent_chrome'} if browser_ok else set()))
                self.assertTrue(checks['python']['ok'])
                self.assertTrue(checks['chrome']['ok'])
                self.assertTrue(all(checks[key]['ok'] for key in expected_runtimes))
                self.assertEqual(checks['browser_agent']['ok'], browser_ok)
                self.assertEqual(result['ok'], True)  # 瀏覽器功能是提醒,不擋找缺
                self.assertNotIn('codex_login', checks)
            self.assertIn('幫你填表與查應徵進度無法使用', results['only_command_code'][1]['browser_agent']['detail'])
            self.assertIn('找缺不受影響', results['no_browser_agent'][1]['browser_agent']['detail'])
            # 有會用 Chrome 的 agent、但 agent 專用的 Chrome 還沒連過:講清楚要打開 agent 的 Chrome、裝外掛、按連接
            chrome_row = results['only_codex'][1]['agent_chrome']
            self.assertFalse(chrome_row['ok'])
            self.assertFalse(chrome_row['required'])
            for step in ('打開 agent 的 Chrome', 'Codex 的 Chrome 外掛', '連接'):
                self.assertIn(step, chrome_row['fix'])
            # Claude Code 勾了可使用 Chrome 也算有瀏覽器 agent;還沒配對時教學連結要看得到
            self.assertIn('docs/agent-chrome.md', results['claude_code_with_chrome'][1]['agent_chrome']['fix'])
            # 不派 agent 跑一次(那會花額度,額度是使用者自己的事):只准讀本機的登入狀態
            self.assertTrue(all(tuple(argv[0][1:]) in (('login', 'status'), ('auth', 'status')) for argv in mine), mine)

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
             patch('agent_chrome.connected', return_value=False):
            code, raw, _ = self.req('/api/run/replies', {})
        self.assertEqual(code, 409, raw)
        self.assertTrue(json.loads(raw)['needs_browser'])

    def test_preference_note_migrates_rules_and_keeps_assumptions_distinct(self):
        from unittest.mock import patch
        import tempfile
        import shutil
        import prefs

        home = tempfile.mkdtemp(prefix='preference-note-test-')
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        raw_prefs = os.path.join(home, 'prefs.md')
        note = os.path.join(home, 'preference-note.md')
        with patch.object(cf, 'HOME', home), patch.object(cf, 'PREFS', raw_prefs), \
                patch.object(cf, 'PREFERENCE_NOTE', note), patch.object(prefs, 'PREF', note), \
                patch.object(cf, 'APPLY_RULES', os.path.join(home, 'apply-rules.md')):
            with open(raw_prefs, 'w', encoding='utf-8') as f:
                f.write('# 求職偏好\n\n## 硬規則\n\n不要外派\n\n'
                        '<!-- 以下由 feedback_dump.py 自動更新 -->\n舊表態\n'
                        '<!-- feedback_dump.py:end -->\n')
            d = self.settings()
            self.assertIn('不要外派', d['texts']['preferences_custom'])
            self.assertEqual(d['texts']['preferences_agent'], '')

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
        tasks = ('common', 'deep', 'wide', 'dir', 'judge')
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
        self.assertIn('skill', json.loads(raw)['msg'].lower())

        settings['research']['skills'] = {task: '' for task in tasks}
        code, raw, _ = self.req('/api/settings', {'settings': settings})
        self.assertEqual(code, 200, raw)
        self.assertEqual(self.settings()['effective']['research']['skills'],
                         {task: '' for task in tasks})

    def test_settings_exposes_each_product_research_skill(self):
        import config as cf
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
        self.assertEqual(cfg['resumes'], [{
            'id': 'general', 'name': '通用版', 'when': '',
            'file_langs': ['zh', 'en'], 'preview_langs': ['zh', 'en'],
        }])
        self.assertEqual(cfg['attachments'], [{
            'id': 'portfolio', 'name': '作品集', 'short': '',
            'resume_ids': ['general'], 'file_langs': ['zh', 'en'],
            'preview_langs': ['zh', 'en'],
        }])

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

    def test_agent_cannot_change_settings(self):
        code, _, _ = self.req('/api/settings', {'texts': {'rules': 'x'}}, ua='Mozilla/5.0 Claude/1.0')
        self.assertEqual(code, 403)

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
        src, _, own = ship.sources({'id': u}, fb)
        self.assertTrue(own and src.endswith('mine.pdf'))
        self.delete('/api/card-file?u=' + urllib.parse.quote(u))
        self.assertNotIn('custom_file', read_fb(self.path)[u])

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
            self.assertEqual([x['id'] for x in files], ['resume:general'])
            self.assertTrue(files[0]['default_checked'])
            self.assertEqual(files[0]['skill_name'], 'Route test skill')
            self.assertEqual(self.req('/api/run/customize', {'url': u, 'items': []})[0], 400)

            code, raw, _ = self.req('/api/run/customize', {'url': u, 'items': ['resume:general']})
            self.assertEqual(code, 200, raw)
            status = self.wait('customize', lambda s: s.get('phase') == 'done')
            self.assertEqual(status['url'], u)
        finally:
            try:
                os.remove(resume_path)
            except OSError:
                pass

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


class AddJobReal(unittest.TestCase):
    """貼網址加入先擷取頁面文字,一律加;直連 404/410 才不加。"""
    def test_adds_every_live_url_and_reports_gone_ones(self):
        import tempfile, add_job, research, page_fetch
        d = tempfile.mkdtemp(); p = os.path.join(d, 'board.html')
        old_sp = add_job.SP
        add_job.SP = d
        old_dir = research.DIR
        research.DIR = os.path.join(d, 'research')
        try:
            make_board(p, {})
            def judge(cands, *_args, **_kw):
                self.assertEqual(len(cands), 1)
                self.assertEqual(cands[0]['jd'], 'Platform Engineer: build secure systems')
                self.assertEqual(cands[0]['title'], 'Platform Engineer')
                cands[0]['title'] = 'Backend Engineer'
                cands[0]['company'] = 'Acme'
                return {cands[0]['url']: {'keep': False, 'fit': 4, 'why': '符合條件',
                                          'cite': [], 'bad_cite': [], 'cat': '其他',
                                          'readable': True, 'card': {}}}
            from unittest.mock import patch
            def fetch(url):
                if url.endswith('/b'):
                    return page_fetch.PageResult(url, 'closed', via='direct', http_status=410)
                return page_fetch.PageResult(url, 'ok', text='Platform Engineer: build secure systems',
                                             title='Platform Engineer', via='direct', http_status=200)
            with patch.object(page_fetch, 'fetch', side_effect=fetch), \
                 patch.object(research, 'judge', side_effect=judge):
                n = add_job.run(['https://ex.test/a', 'https://ex.test/b', JOBS[0]['id']], p)
            self.assertEqual(n, 1)
            data = read_board(p)
            new = [j for j in data['data']['jobs'] if j['id'] == 'https://ex.test/a'][0]
            self.assertIn('Backend Engineer', new['target'])
            inbox = json.loads(data['fb']).get('__inbox__') or []
            self.assertTrue(any('已下架' in x.get('msg', '') for x in inbox))
        finally:
            add_job.SP = old_sp
            research.DIR = old_dir
            shutil.rmtree(d, ignore_errors=True)

    def test_pasted_jobs_always_get_a_card_summary(self):
        # 使用者自己貼的網址:判成先不送(keep=false)也要寫摘要,不然卡上一整排「無」
        import research
        cand = {'url': 'https://ex.test/x', 'title': 'Engineer', 'company': 'Acme', 'jd': 'JD'}
        pasted = research.judge_prompt([dict(cand)], [], '/tmp/out.json', mode='add')[0]
        found = research.judge_prompt([dict(cand)], [], '/tmp/out.json', mode='wide')[0]
        self.assertIn('card 一律要寫', pasted)
        self.assertNotIn('card 只有 keep 的才要寫', pasted)
        self.assertIn('card 只有 keep 的才要寫', found)

    def test_unreadable_job_is_kept_and_reported(self):
        import tempfile, add_job, research, page_fetch
        from unittest.mock import patch
        d = tempfile.mkdtemp(); p = os.path.join(d, 'board.html')
        old_sp, old_dir = add_job.SP, research.DIR
        add_job.SP = d
        research.DIR = os.path.join(d, 'research')
        try:
            make_board(p, {})
            with patch.object(page_fetch, 'fetch', return_value=page_fetch.PageResult(
                    'https://ex.test/unreadable', 'unknown', errors=('direct unavailable',))), \
                 patch.object(research, 'judge', return_value={}):
                self.assertEqual(add_job.run(['https://ex.test/unreadable'], p), 1)
            data = read_board(p)
            new = next(j for j in data['data']['jobs'] if j['id'] == 'https://ex.test/unreadable')
            self.assertIn('待確認', new['target'])
            inbox = json.loads(data['fb']).get('__inbox__') or []
            self.assertTrue(any('無法確認職缺頁內容' in x.get('msg', '') for x in inbox))
        finally:
            add_job.SP, research.DIR = old_sp, old_dir
            shutil.rmtree(d, ignore_errors=True)


class Translate(unittest.TestCase):
    """使用者改了中文、英文待重翻(tr)的答案:沒人翻核准就永遠按不下去,所以放進代投 agent 的 prompt。"""
    def test_pending_translation_goes_into_the_fix_prompt(self):
        import apply_run as run
        u = JOBS[0]['id']
        fb = {'__ans__': [{'k': 'why', 'q': 'Why?', 'v': 'Old English', 'zh': '新的中文', 'tr': 1}],
              u: {'app': 'ship', 'form': {'f': [{'q': 'Why?', 'src': 'bank', 'k': 'why'}]},
                  'apply': {'session': 'S', 'tab_id': '1'}}}
        self.assertEqual(run.eligible({u: JOBS[0]}, fb, 'fix'), [u])
        p, _ = run.prompt_for('fix', u, JOBS[0], fb, '/tmp/b.html')
        self.assertIn('新的中文', p)
        self.assertIn('fr.translate', p)


class Sync104(unittest.TestCase):
    def test_records_mark_cards_sent(self):
        import tempfile, sync_sent as ss
        d = tempfile.mkdtemp(); p = os.path.join(d, 'board.html')
        try:
            a, b = 'https://www.104.com.tw/job/aaaa1', 'https://www.104.com.tw/job/bbbb2'
            make_board(p, {a: {'app': 'ship'}, b: {'app': 'ready'}}, jobs=[{'id': a, 'target': 'A'}, {'id': b, 'target': 'B'}])
            msg = ss.sync(p, [{'id': 'aaaa1', 'title': 'A', 'applied_at': time.strftime('%m/%d 10:00')}])
            self.assertIn('1 張', msg)
            fb = read_fb(p)
            self.assertEqual((fb[a]['app'], fb[b]['app']), ('sent', 'ready'))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_other_platforms_match_by_platform_and_posting_id(self):
        import sync_sent as ss, datetime
        li = 'https://www.linkedin.com/jobs/view/senior-designer-at-acme-4012345678/'
        lv = 'https://jobs.lever.co/acme/0b5a1c2d-3e4f-4a5b-8c6d-7e8f9a0b1c2d'
        h1 = 'https://www.104.com.tw/job/4012345678'
        jobs = [{'id': u, 'target': t} for u, t in ((li, 'A'), (lv, 'B'), (h1, 'C'))]
        fb = {li: {'app': 'ship'}, lv: {'app': 'ship'}, h1: {'app': 'ship'}}
        today = datetime.date(2026, 10, 1)
        recs = [{'platform': 'linkedin', 'id': '4012345678', 'applied_at': '2026-09-30'},
                {'url': lv + '/apply', 'applied_at': '2026-09-30'}]
        to_mark, unknown, _ = ss.plan(recs, fb, jobs, today)
        # 同一串數字,平台不同就不是同一個缺:LinkedIn 的紀錄不能把 104 那張標成已投遞
        self.assertEqual(sorted(j['id'] for j, _ in to_mark), sorted([li, lv]))
        self.assertEqual(unknown, [])
        # 舊格式只有代號、沒寫平台:照舊當 104
        to_mark, _, _ = ss.plan([{'id': '4012345678', 'applied_at': '2026-09-30'}], fb, jobs, today)
        self.assertEqual([j['id'] for j, _ in to_mark], [h1])

    def test_suspect_only_on_platforms_that_were_read(self):
        import sync_sent as ss, datetime
        li = 'https://www.linkedin.com/jobs/view/4012345678/'
        h1 = 'https://www.104.com.tw/job/abc12'
        jobs = [{'id': li, 'target': 'A'}, {'id': h1, 'target': 'B'}]
        fb = {li: {'app': 'sent', 'sent_at': '2026-09-20'}, h1: {'app': 'sent', 'sent_at': '2026-09-20'}}
        _, _, suspect = ss.plan([{'platform': '104', 'id': 'zzz99', 'applied_at': '2026-09-30'}],
                                fb, jobs, datetime.date(2026, 10, 1))
        self.assertEqual([j['id'] for j, _ in suspect], [h1])   # LinkedIn 這輪沒讀到,不懷疑

    def test_agent_result_keeps_link_and_platform(self):
        import reply_run as rr
        url = 'https://jobs.lever.co/x/1'
        got = rr.parse_result({'checked': [], 'job_ids': [
            {'url': 'https://jobs.lever.co/acme/0b5a1c2d-3e4f-4a5b-8c6d-7e8f9a0b1c2d', 'title': 'B'},
            {'platform': 'linkedin', 'id': '4012345678'}]}, [url]).job_ids
        self.assertEqual(got[0]['url'], 'https://jobs.lever.co/acme/0b5a1c2d-3e4f-4a5b-8c6d-7e8f9a0b1c2d')
        self.assertEqual((got[1]['platform'], got[1]['id']), ('linkedin', '4012345678'))

    def test_agent_checks_104_application_history_on_every_run(self):
        import reply_run as rr
        url = 'https://jobs.lever.co/x/1'
        p = rr.prompt_for({url: {'app': 'sent', 'sent_at': '2026-09-10'}},
                          {url: {'target': 'AI Engineer'}}, [url], '/tmp/replies.json')
        self.assertIn('讀平台應徵紀錄頁時', p)
        self.assertIn('source_type=application_record', p)
        self.assertIn('job_ids', p)


if __name__ == '__main__':
    unittest.main()


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

    def test_wip_without_script_stays_todo(self):
        import interview_bank as ib
        self.assertEqual(ib.from_form({'t': 'x', 'stage': 'ok'})['st'], 'todo')

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
        self.assertEqual(self.req('/api/bank', {'op': 'put', 'item': {'t': 'x'}}, ua='Mozilla/5.0 Claude/1.0')[0], 403)
