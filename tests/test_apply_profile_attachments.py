#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代投檢查依 agent 回報比對平台履歷附件。"""
import copy, json, os, shutil, sys, tempfile, time, unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import apply_run as run       # noqa: E402
import config as cf           # noqa: E402
import form_record as fr      # noqa: E402


URL = 'https://new-platform.example/jobs/1'
# 投遞前驗收跑過、沒有問題(核准規則也看它;這支測的是平台履歷附件那一關,測試的看板路徑讀不到驗收結果)
_passed = patch.object(fr, 'board_status', return_value={'schema_version': 2, 'checked_links': True, 'issues': []})


def setUpModule():
    _passed.start()


def tearDownModule():
    _passed.stop()


class ProfileAttachments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='apply-profile-attachments-')
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(self.home, 'resume'))
        self.old_home = cf.HOME
        self.old_env = os.environ.get('JOBSALVO_HOME')
        self.settings = copy.deepcopy(cf.DEFAULTS)
        self.settings['resume']['base'] = 'resume'
        self.settings['resume']['langs'] = ['zh', 'en']
        self.settings['resume']['resumes'] = [
            {'id': 'general', 'name': '通用版',
             'files': {'zh': 'resume/base.pdf', 'en': 'resume/base-en.pdf'}, 'enabled': True},
            {'id': 'research', 'name': '研究版',
             'files': {'zh': 'resume/research.pdf', 'en': 'resume/research-en.pdf'}, 'enabled': True},
        ]
        self.settings['resume']['attachments'] = [
            {'id': 'cover', 'name': '求職信',
             'files': {'zh': 'resume/cover.pdf', 'en': 'resume/cover-en.pdf'},
             'resume_ids': ['general'], 'enabled': True},
        ]
        with open(os.path.join(self.home, cf.NAME), 'w', encoding='utf-8') as f:
            json.dump(self.settings, f, ensure_ascii=False)
        self._put_home('resume/base.pdf', b'resume')
        self._put_home('resume/base-en.pdf', b'resume en')
        self._put_home('resume/research.pdf', b'research resume')
        self._put_home('resume/research-en.pdf', b'research resume en')
        self.expected = self._put_home('resume/cover.pdf', b'cover bytes')
        self._put_home('resume/cover-en.pdf', b'english cover')
        os.environ['JOBSALVO_HOME'] = self.home
        cf.reload(self.home)
        import profile_sync as ps
        self.ps = ps
        self.old_reg = ps.REG
        ps.REG = os.path.join(self.home, 'profiles.json')
        self.out = os.path.join(self.tmp, 'out')
        os.makedirs(self.out)
        self.downloads = os.path.join(self.tmp, 'downloads')
        self.outside = self._put('outside.pdf', b'not a platform download')
        self.job = {'id': URL, 'resume': {'recommend': 'general', 'lang': 'zh'}}
        self.fb = {URL: {
            'resume_id': 'general', 'lang': 'zh',
            'form': {'plat': 'New Platform', 'at': run.today(), 'f': []},
        }}

    def tearDown(self):
        if self.old_env is None:
            os.environ.pop('JOBSALVO_HOME', None)
        else:
            os.environ['JOBSALVO_HOME'] = self.old_env
        self.ps.REG = self.old_reg
        cf.reload(self.old_home)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _put(self, relative, contents):
        path = os.path.join(self.tmp, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(contents)
        return path

    def _put_home(self, relative, contents):
        path = os.path.join(self.home, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(contents)
        return path

    def _download(self, name, contents):
        os.makedirs(self.downloads, exist_ok=True)
        path = os.path.join(self.downloads, name)
        with open(path, 'wb') as f:
            f.write(contents)
        return path

    def _check(self, delivery=None, attachments=None, include_attachments=True,
               include_delivery=True, uploaded_files=None, fixed_profile=None, extra=None):
        os.makedirs(self.downloads, exist_ok=True)
        report = {
            'fields': [], 'submitted': False, 'tab_id': '7',
            'tab_url': URL + '/apply', 'handoff': True,
        }
        if include_delivery:
            report['delivery'] = delivery or {
                'method': 'platform_profile', 'profile_url': 'https://profiles.example/1',
                'profile_kind': 'fixed',
            }
        if include_attachments:
            report['profile_attachments'] = attachments or []
        if uploaded_files is not None:
            report['uploaded_files'] = uploaded_files
        if fixed_profile is not None:
            report['fixed_profile'] = fixed_profile
        report.update(extra or {})
        if 'delivery' in report:
            self.fb.setdefault(URL, {}).setdefault('apply', {})['delivery'] = report['delivery']
        else:
            (self.fb.get(URL) or {}).get('apply', {}).pop('delivery', None)
        with open(os.path.join(self.out, 'fill.json'), 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False)
        with open(os.path.join(self.out, 'fill.png'), 'wb') as f:
            f.write(b'png')
        # 平台履歷附件核對不在檢查填表裡了(填完後、送出前才做,而且只在履歷/附件更新過時):直接測那一步
        return run._check_delivery_attachments(self.fb, URL, self.job, report, self.downloads)

    def _claude_log(self, url, files, code=None):
        """假的 Claude stream-json 紀錄:它在平台履歷頁跑了算附件雜湊的那段(或被改過的一段)。"""
        import apply_tab
        path = os.path.join(self.tmp, 'claude.log')
        use = {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'u1', 'name': 'mcp__claude-in-chrome__javascript_tool',
                                                               'input': {'action': 'javascript_exec', 'text': code or apply_tab.ATTACH_JS}}]}}
        res = {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'u1', 'content': [
            {'type': 'text', 'text': json.dumps({'url': url, 'files': files}, ensure_ascii=False) + '\n\nTab Context:\n- x'}]}]}}
        with open(path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(use, ensure_ascii=False) + '\n' + json.dumps(res, ensure_ascii=False) + '\n')
        return path

    def test_claude_checks_attachments_by_hash_without_downloading(self):
        # #294:只用 Claude 時沒有把檔取回來的工具。它在平台履歷頁算每個檔的雜湊,程式從紀錄拿、跟本機檔比
        import hashlib
        url = 'https://profiles.example/1'
        same = {'name': 'cover.pdf', 'size': 11, 'sha256': hashlib.sha256(b'cover bytes').hexdigest()}
        other = {'name': 'cover.pdf', 'size': 11, 'sha256': hashlib.sha256(b'Cover bytes').hexdigest()}
        extra = {'name': 'old.pdf', 'size': 3, 'sha256': hashlib.sha256(b'old').hexdigest()}
        report = {'delivery': {'method': 'platform_profile', 'profile_url': url, 'profile_kind': 'fixed'},
                  'profile_attachments': []}
        self.fb.setdefault(URL, {}).setdefault('apply', {})['delivery'] = report['delivery']

        def check(log):
            return run._check_delivery_attachments(self.fb, URL, self.job, report, self.downloads, force=True,
                                                   hash_reader=run.claude_attachment_hashes([log]))
        self.assertEqual(check(self._claude_log(url, [same])), [])
        self.assertTrue(any('內容不同' in p for p in check(self._claude_log(url, [other]))))
        self.assertTrue(any('少了' in p for p in check(self._claude_log(url, []))))
        self.assertTrue(any('多出' in p for p in check(self._claude_log(url, [same, extra]))))
        # 別的網址、被改過的程式碼算出來的,不採信
        self.assertTrue(any('沒核對到' in p for p in check(self._claude_log('https://profiles.example/2', [same]))))
        self.assertTrue(any('沒核對到' in p for p in check(self._claude_log(url, [same], code='(() => "x")()'))))

    def test_fill_check_does_not_download_platform_profile_attachments(self):
        # 填表只填申請表:平台履歷附件沒下載不算問題,說明裡也叫它不用下載
        with open(os.path.join(self.out, 'fill.json'), 'w', encoding='utf-8') as f:
            json.dump({'fields': [], 'submitted': False, 'tab_id': '7', 'tab_url': URL + '/apply',
                       'handoff': True, 'delivery': {'method': 'platform_profile', 'profile_kind': 'fixed',
                                                     'profile_url': 'https://profiles.example/1'}}, f)
        with open(os.path.join(self.out, 'fill.png'), 'wb') as f:
            f.write(b'png')
        problems, _result = run.check_fill(self.fb, URL, self.out, time.time() - 1,
                                           job=self.job, attachment_download_dir=self.downloads)
        self.assertFalse([p for p in problems if '附件' in p or '下載' in p], problems)
        with patch.object(run.ship, 'folder', return_value=''), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run, '_run_text', return_value=''):
            prompt, _out = run.prompt_for('fill', URL, self.job, self.fb, self.out,
                                          attachment_download_dir=self.downloads)
        self.assertIn('不用下載核對', prompt)
        self.assertNotIn('field_mapping', prompt)                 # 欄位對照整套拿掉了
        self.assertNotIn('清單以外的檔案', prompt)                 # 只看檔名判「多出來」會誤判,留到核對那一輪
        self.assertIn('不用去讀 jobsalvo 的程式原始碼', prompt)   # agent 看不懂格式就會翻原始碼,一次花好幾分鐘
        check = self.ps.attachment_step(self.job, self.fb, URL, self.downloads, verify_profile=True)
        self.assertIn('清單以外的檔案', check)

    def test_prepared_tab_is_handed_over_with_the_fields_already_read(self):
        # 程式先開好申請頁、讀好欄位:agent 不用自己開分頁、關舊的、整頁重讀(以前每輪五到十步)
        prepared = {'tab_id': '5296', 'page': {
            'title': 'Job Application', 'url': URL + '/apply', 'lines': ['Apply for this job'],
            'fields': [{'label': 'First Name', 'type': 'text', 'value': ''},
                       {'label': 'Resume', 'type': 'file', 'value': []}]}}
        with patch.object(run.ship, 'folder', return_value=''), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run, '_run_text', return_value=''):
            prompt, _out = run.prompt_for('fill', URL, self.job, self.fb, self.out,
                                          attachment_download_dir=self.downloads, prepared=prepared)
            plain, _out = run.prompt_for('fill', URL, self.job, self.fb, self.out,
                                         attachment_download_dir=self.downloads)
        self.assertIn("cua.getTab('5296'", prompt)
        self.assertIn('First Name [text]', prompt)
        self.assertNotIn('打開申請表單。那裡如果還有', prompt)
        self.assertIn('打開申請表單。那裡如果還有', plain)          # 程式開不起來時照舊自己開
        self.assertIn('確定的動作合成一次呼叫', prompt)

    def test_platform_notes_are_kept_per_platform_and_handed_to_the_next_round(self):
        # agent 在一個平台試出來的做法存起來,下一輪同平台直接附上;說不對的那句拿掉;別的平台看不到
        run.remember_notes(URL, ['自訂經歷的編輯器要先全選再輸入才會存'])
        run.remember_notes(URL, ['自訂經歷的編輯器要先全選再輸入才會存', '推薦信從下拉選單選'])     # 重複的不再加
        run.remember_notes('https://job-boards.greenhouse.io/acme/jobs/1', ['上傳欄要用 filechooser'])
        self.assertEqual(run.platform_notes(URL), ['自訂經歷的編輯器要先全選再輸入才會存', '推薦信從下拉選單選'])
        self.assertEqual(run.note_key('https://boards.greenhouse.io/other/jobs/2'), 'greenhouse.io')
        with patch.object(run.ship, 'folder', return_value=''), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run, '_run_text', return_value=''):
            prompt, _out = run.prompt_for('fill', URL, self.job, self.fb, self.out,
                                          attachment_download_dir=self.downloads)
        self.assertIn('【這個平台以前學到的】', prompt)
        self.assertIn('推薦信從下拉選單選', prompt)
        self.assertNotIn('filechooser 上傳欄要用', prompt)
        run.remember_notes(URL, [], ['推薦信從下拉選單選'])
        self.assertEqual(run.platform_notes(URL), ['自訂經歷的編輯器要先全選再輸入才會存'])

    def test_fill_prompt_carries_answer_text_and_record_format(self):
        # agent 以前每一輪都去翻 board.html 找推薦信原文(只給了 k)、去讀 form_record.py 學記法:都直接寫在 prompt 裡
        self.fb['__ans__'] = [{'k': 'a1b2c3d4', 'q': '自我推薦信', 'v': '我擅長把系統拆開、一次只動一個變數。'}]
        self.fb[URL]['form']['f'] = [{'q': '自我推薦信', 'src': 'bank', 'k': 'a1b2c3d4'}]
        with patch.object(run.ship, 'folder', return_value=''), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run, '_run_text', return_value=''):
            prompt, _out = run.prompt_for('fill', URL, self.job, self.fb, self.out,
                                          attachment_download_dir=self.downloads)
        self.assertIn('我擅長把系統拆開、一次只動一個變數。', prompt)
        self.assertNotIn('寫法看 tools/form_record.py', prompt)
        self.assertIn('"src": "bank", "k": 那條的 k', prompt)
        # 表單只記一次:寫 fill.json 之後從它記進看板,不再另外寫一份 fr.record(...)
        self.assertIn('form_record.py --from-fill', prompt)
        self.assertNotIn('fr.record(', prompt)

    def test_matching_platform_profile_attachment_has_no_problem_and_temp_is_removed(self):
        downloaded = self._download('cover.pdf', b'cover bytes')
        self.assertEqual(self._check(attachments=[{'name': '求職信', 'path': downloaded}]), [])
        self.assertFalse(os.path.exists(self.downloads))

    def test_download_failure_is_not_called_missing(self):
        # 2026-09-29 一張 104:平台上三個附件都在,只是 agent 下載逾時、一個都沒拿到;卡上卻寫三個都「少了」
        got = self._check(attachments=[], extra={'problems': ['第一個附件 downloadMedia 等待 120 秒後逾時,沒有取得檔案']})
        self.assertFalse(any('少了' in p for p in got), got)
        self.assertTrue(any('沒下載到' in p and '逾時' in p for p in got), got)

    def test_missing_changed_and_extra_attachments_are_named(self):
        missing = self._check(attachments=[])
        self.assertTrue(any('求職信' in p and '少了' in p for p in missing), missing)
        self.assertFalse(os.path.exists(self.downloads))

        changed = self._download('cover.pdf', b'Cover bytes')  # 同大小,逐位元組不同
        different = self._check(attachments=[{'name': '求職信', 'path': changed}])
        self.assertTrue(any('求職信' in p and '內容不同' in p for p in different), different)
        self.assertTrue(any('固定版被蓋掉' in p for p in different), different)
        self.assertFalse(os.path.exists(self.downloads))

        current = self._download('cover.pdf', b'cover bytes')
        old = self._download('old.pdf', b'old cover bytes')
        extra = self._check(attachments=[
            {'name': '求職信', 'path': current}, {'name': '舊版求職信', 'path': old},
        ])
        self.assertTrue(any('舊版求職信' in p and '多出' in p for p in extra), extra)
        self.assertFalse(os.path.exists(self.downloads))

    def test_accepted_custom_attachment_is_the_card_baseline(self):
        custom = self._put_home('custom/cover.pdf', b'accepted custom cover')
        self.fb[URL]['custom_docs'] = {
            'attachment:cover:zh': {'status': 'accepted', 'path': 'custom/cover.pdf'}
        }
        downloaded = self._download('cover.pdf', b'cover bytes')
        problems = self._check(attachments=[{'name': '求職信', 'path': downloaded}])
        self.assertTrue(any('求職信' in p and '內容不同' in p for p in problems), problems)
        matching_custom = self._download('cover-custom.pdf', b'accepted custom cover')
        self.assertEqual(self._check(attachments=[{'name': '客製求職信', 'path': matching_custom}]), [])
        self.assertTrue(os.path.isfile(custom))

    def test_reported_profile_is_checked_on_unknown_platform_but_upload_routes_are_skipped(self):
        downloaded = self._download('cover.pdf', b'Xover bytes')
        profile_problems = self._check(attachments=[{'name': '求職信', 'path': downloaded}])
        self.assertTrue(any('內容不同' in p for p in profile_problems), profile_problems)

        direct = self._check(
            delivery={'method': 'direct_upload'}, include_attachments=False,
        )
        self.assertTrue(any('申請表上傳檔' in problem for problem in direct), direct)
        no_profile = self._check(
            delivery={'method': 'no_profile'}, include_attachments=False,
        )
        self.assertEqual(no_profile, [])
        self.assertFalse(os.path.exists(self.downloads))

    def test_missing_report_and_download_paths_outside_temp_are_problems(self):
        missing_route = self._check(include_delivery=False, include_attachments=False)
        self.assertTrue(any('沒回報' in p for p in missing_route), missing_route)

        missing = self._check(include_attachments=False)
        self.assertTrue(any('下載' in p for p in missing), missing)

        outside = self._check(attachments=[{'name': '求職信', 'path': self.outside}])
        self.assertTrue(any('暫存夾外' in p and '求職信' in p for p in outside), outside)
        self.assertTrue(os.path.isfile(self.outside))

    def test_successful_profile_check_is_cached_until_file_or_compatibility_changes(self):
        downloaded = self._download('cover.pdf', b'cover bytes')
        self.assertEqual(self._check(attachments=[{'name': '求職信', 'path': downloaded}]), [])

        # 沒有任何清單或檔案變化時,平台履歷不必再下載。
        self.assertEqual(self._check(include_attachments=False), [])

        # 能搭哪份履歷的清單變了,即使目前仍符合,下一輪也必須重新下載。
        self.settings['resume']['attachments'][0]['resume_ids'] = ['general', 'research']
        with open(os.path.join(self.home, cf.NAME), 'w', encoding='utf-8') as f:
            json.dump(self.settings, f, ensure_ascii=False)
        cf.reload(self.home)
        changed_list = self._check(include_attachments=False)
        self.assertTrue(any('重新下載' in p for p in changed_list), changed_list)

        # 檔案內容變了也要重新驗,不靠檔名或大小。
        self.expected = self._put_home('resume/cover.pdf', b'cover byteS')
        changed_file = self._check(include_attachments=False)
        self.assertTrue(any('重新下載' in p for p in changed_file), changed_file)


    def test_profile_attachment_cache_is_shared_between_cards(self):
        import profile_sync as ps

        profile_url = 'https://profiles.example/1'
        delivery = {
            'method': 'platform_profile',
            'profile_url': profile_url,
            'profile_kind': 'fixed',
        }
        downloaded = self._download('cover.pdf', b'cover bytes')
        self.assertEqual(
            self._check(delivery=delivery, attachments=[
                {'name': '求職信', 'path': downloaded},
            ]),
            [],
        )

        second_url = URL + '/another-card'
        job = dict(self.job, id=second_url)
        fb = {second_url: {'resume_id': 'general', 'lang': 'zh'}}
        ps.remember(ps.profile_key(second_url), 'zh', 'general', profile_url)

        problems = ps.check_attachments(
            job, fb, second_url, {'delivery': delivery}, self.downloads,
        )
        self.assertEqual(problems, [])
        prompt = ps.attachment_step(job, fb, second_url, self.downloads)
        self.assertIn('本輪不必下載附件', prompt)

    def test_extra_platform_attachments_require_problem_and_user_report(self):
        import profile_sync as ps

        delivery = {
            'method': 'platform_profile',
            'profile_url': 'https://profiles.example/1',
            'profile_kind': 'fixed',
        }
        self.fb[URL]['apply'] = {'delivery': delivery}

        prompt = ps.attachment_step(self.job, self.fb, URL, self.downloads)

        self.assertIn('清單以外的檔案（包含舊版）', prompt)
        self.assertIn('JSON 的 problems 寫明', prompt)
        self.assertIn('agent_report.py', prompt)
        self.assertIn('--from 代投', prompt)
        self.assertIn('只列在 profile_attachments', prompt)
        self.assertIn('兩者完成前不得 markHandoff', prompt)

    def test_unchecking_an_attachment_invalidates_the_cached_profile_check(self):
        downloaded = self._download('cover.pdf', b'cover bytes')
        self.assertEqual(self._check(attachments=[{'name': '求職信', 'path': downloaded}]), [])

        self.settings['resume']['attachments'][0]['enabled'] = False
        with open(os.path.join(self.home, cf.NAME), 'w', encoding='utf-8') as f:
            json.dump(self.settings, f, ensure_ascii=False)
        cf.reload(self.home)
        unchecked = self._check(include_attachments=False)
        self.assertTrue(any('重新下載' in p for p in unchecked), unchecked)

    def test_prompt_skips_profile_download_when_the_saved_fingerprint_still_matches(self):
        downloaded = self._download('cover.pdf', b'cover bytes')
        self.assertEqual(self._check(attachments=[{'name': '求職信', 'path': downloaded}]), [])
        with patch.object(run.ship, 'folder', return_value=''), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run, '_run_text', return_value=''):
            prompt, _out = run.prompt_for(
                'fix', URL, self.job, self.fb, self.out,
                attachment_download_dir=self.downloads,
            )
        self.assertIn('本輪不必下載附件', prompt)

    def test_submit_profile_mismatch_is_reported_before_the_send_call(self):
        delivery = {
            'method': 'platform_profile', 'profile_url': 'https://profiles.example/1',
            'profile_kind': 'fixed',
        }
        self.fb[URL]['apply'] = {
            'stage': 'fill', 'ok': True, 'issues': [], 'session': 'S1',
            'agent_id': 'primary', 'delivery': delivery,
        }
        self.fb[URL]['approve'] = {'at': '2026-09-24', 'snap': fr.snapshot(self.fb, URL)}

        def agent_run(prompt, log, *_args, **_kwargs):
            if prompt == 'profile preflight':
                actual = self._download('cover.pdf', b'Cover bytes')
                with open(os.path.join(self.out, 'pre-submit.json'), 'w', encoding='utf-8') as f:
                    json.dump({
                        'delivery': delivery,
                        'profile_attachments': [{'name': '求職信', 'path': actual}],
                    }, f, ensure_ascii=False)
            else:
                self.assertEqual(prompt, 'actual send')
            return SimpleNamespace(ok=True, status='completed', agent_id='primary', message=lambda: '')

        with patch.object(run, 'load', return_value=({URL: self.job}, self.fb)), \
             patch.object(run, 'profile_check', return_value=({'read': delivery['profile_url']},
                    [{'where': '求職經歷', 'missing': ['內容不同']}], '')) , \
             patch.object(run, 'out_dir', return_value=self.out), \
             patch.object(run, 'pre_submit_prompt', return_value=('profile preflight', self.out), create=True), \
             patch.object(run, 'prompt_for', return_value=('actual send', self.out)) as send_prompt, \
             patch.object(run.ar, 'run', side_effect=agent_run) as agent, \
             patch.object(run.ar, 'session_id', return_value='S1'), \
             patch.object(run, 'shoot'), \
             patch.object(run.agent_report, 'report'), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)), \
             patch.object(run, 'tempfile', SimpleNamespace(TemporaryDirectory=lambda **_kw: SimpleNamespace(name=self.downloads, cleanup=lambda: shutil.rmtree(self.downloads, ignore_errors=True))), create=True), \
             patch('agent_chrome.ensure', return_value=(True, '')):
            ok, message = run.run_one('submit', URL, os.path.join(self.tmp, 'board.html'))

        self.assertFalse(ok)
        self.assertIn('內容不同', message)
        agent.assert_not_called()
        send_prompt.assert_not_called()
        self.assertFalse(os.path.exists(self.downloads))
        self.assertTrue(any('內容不同' in p for p in self.fb[URL]['apply']['issues']))

    def test_submit_profile_match_is_rechecked_before_the_send_call(self):
        delivery = {
            'method': 'platform_profile', 'profile_url': 'https://profiles.example/1',
            'profile_kind': 'fixed',
        }
        self.fb[URL]['apply'] = {
            'stage': 'fill', 'ok': True, 'issues': [], 'session': 'S1',
            'agent_id': 'primary', 'delivery': delivery,
        }
        self.fb[URL]['approve'] = {'at': '2026-09-24', 'snap': fr.snapshot(self.fb, URL)}

        def agent_run(prompt, _log, *_args, **_kwargs):
            if prompt == 'profile preflight':
                actual = self._download('cover.pdf', b'cover bytes')
                with open(os.path.join(self.out, 'pre-submit.json'), 'w', encoding='utf-8') as f:
                    json.dump({
                        'delivery': delivery,
                        'profile_attachments': [{'name': '求職信', 'path': actual}],
                    }, f, ensure_ascii=False)
            else:
                self.assertEqual(prompt, 'actual send')
            return SimpleNamespace(ok=True, status='completed', agent_id='primary', message=lambda: '')

        with patch.object(run, 'load', return_value=({URL: self.job}, self.fb)), \
             patch.object(run, 'profile_check', return_value=({'read': delivery['profile_url']}, [], '')) , \
             patch.object(run, 'out_dir', return_value=self.out), \
             patch.object(run, 'pre_submit_prompt', return_value=('profile preflight', self.out), create=True), \
             patch.object(run, 'prompt_for', return_value=('actual send', self.out)) as send_prompt, \
             patch.object(run.ar, 'run', side_effect=agent_run) as agent, \
             patch.object(run.ar, 'session_id', return_value='S1'), \
             patch.object(run, 'check_submit', return_value=(True, {'confirm_text': 'submitted'})), \
             patch.object(run, 'shoot'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.object(run.ship, 'folder', return_value=''), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)), \
             patch.object(run, 'tempfile', SimpleNamespace(TemporaryDirectory=lambda **_kw: SimpleNamespace(name=self.downloads, cleanup=lambda: shutil.rmtree(self.downloads, ignore_errors=True))), create=True), \
             patch('agent_chrome.ensure', return_value=(True, '')), \
             patch('apply_tab.release'):
            ok, message = run.run_one('submit', URL, os.path.join(self.tmp, 'board.html'))

        self.assertTrue(ok, message)
        self.assertEqual(agent.call_count, 2)
        send_prompt.assert_called_once()
        self.assertFalse(os.path.exists(self.downloads))
        # 剛核對過、履歷和附件都沒改:下一次送出不再派 agent 重新下載,直接送
        agent.reset_mock()
        self.fb[URL].pop('form', None)
        self.fb[URL]['form'] = {'at': '2026-09-27', 'f': []}
        self.fb[URL]['apply'] = {'stage': 'fill', 'ok': True, 'issues': [], 'session': 'S1',
                                 'agent_id': 'primary', 'delivery': delivery}
        self.fb[URL]['approve'] = {'at': '2026-09-27', 'snap': fr.snapshot(self.fb, URL)}
        self.assertTrue(self.ps.profile_attachments_fresh(self.job, self.fb, URL))
        # 附件內容改了:指紋不同,就要重新核對
        self._put_home('resume/cover.pdf', b'cover bytes v2')
        self.assertFalse(self.ps.profile_attachments_fresh(self.job, self.fb, URL))
        self._put_home('resume/cover.pdf', b'cover bytes')

    def test_fresh_profile_check_skips_the_download_agent_before_submit(self):
        delivery = {'method': 'platform_profile', 'profile_url': 'https://profiles.example/1',
                    'profile_kind': 'fixed'}
        self.fb[URL]['apply'] = {'stage': 'fill', 'ok': True, 'issues': [], 'session': 'S1',
                                 'agent_id': 'primary', 'delivery': delivery}
        self.fb[URL]['approve'] = {'at': '2026-09-24', 'snap': fr.snapshot(self.fb, URL)}
        self.ps.remember_attachment_check(delivery['profile_url'],
                                          self.ps.attachment_fingerprint(self.job, self.fb, delivery), 'fixed', True)
        prompts = []

        def agent_run(prompt, _log, *_args, **_kwargs):
            prompts.append(prompt)
            return SimpleNamespace(ok=True, status='completed', agent_id='primary', message=lambda: '')

        with patch.object(run, 'load', return_value=({URL: self.job}, self.fb)), \
             patch.object(run, 'profile_check', return_value=({'read': delivery['profile_url']}, [], '')), \
             patch.object(run, 'out_dir', return_value=self.out), \
             patch.object(run, 'pre_submit_prompt', return_value=('profile preflight', self.out)), \
             patch.object(run, 'prompt_for', return_value=('actual send', self.out)), \
             patch.object(run.ar, 'run', side_effect=agent_run), \
             patch.object(run.ar, 'session_id', return_value='S1'), \
             patch.object(run, 'check_submit', return_value=(True, {'confirm_text': 'submitted'})), \
             patch.object(run, 'shoot'), \
             patch.object(run.agent_report, 'resolve'), \
             patch.object(run.ship, 'folder', return_value=''), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run.bd, 'set_fb', side_effect=lambda mut, live=None, by='': mut(self.fb)), \
             patch('agent_chrome.ensure', return_value=(True, '')), \
             patch('apply_tab.release'):
            ok, message = run.run_one('submit', URL, os.path.join(self.tmp, 'board.html'))
        self.assertTrue(ok, message)
        self.assertEqual(prompts, ['actual send'])          # 沒有「送出前下載核對」那一輪

    def test_after_fill_check_runs_only_when_the_profile_is_not_checked_for_current_files(self):
        delivery = {'method': 'platform_profile', 'profile_url': 'https://profiles.example/1',
                    'profile_kind': 'fixed'}
        self.fb[URL]['apply'] = {'stage': 'fill', 'ok': True, 'session': 'S1', 'delivery': delivery}
        calls = []

        def agent_run(prompt, log, *_args, **kw):
            calls.append((prompt, kw.get('resume'), kw.get('timeout')))
            actual = self._download('cover.pdf', b'cover bytes')
            with open(os.path.join(self.out, 'pre-submit.json'), 'w', encoding='utf-8') as f:
                json.dump({'delivery': delivery, 'profile_attachments': [{'name': '求職信', 'path': actual}]},
                          f, ensure_ascii=False)
            return SimpleNamespace(ok=True, status='completed', agent_id='primary', message=lambda: '')

        with patch.object(run, 'load', return_value=({URL: self.job}, self.fb)), \
             patch.object(run, 'out_dir', return_value=self.out), \
             patch.object(run, 'profile_after', return_value=[]), \
             patch.object(run.ar, 'run', side_effect=agent_run), \
             patch.object(run.ship, 'folder', return_value=''), \
             patch.object(run.ship, 'read_info', return_value={}), \
             patch.object(run, 'tempfile', SimpleNamespace(
                 TemporaryDirectory=lambda **_kw: __import__('contextlib').nullcontext(self.downloads))):
            first = run._profile_check_after_fill(URL, 'board', 'S1', 'primary')
            again = run._profile_check_after_fill(URL, 'board', 'S1', 'primary')
        self.assertEqual((first, again), ([], []))
        self.assertEqual(len(calls), 1)                        # 第一次核對過、檔沒變:第二張起不再派 agent
        prompt, resume, timeout = calls[0]
        self.assertEqual(resume, 'S1')
        self.assertEqual(timeout, run.PROFILE_CHECK_TIMEOUT)    # 自己的時間上限,不佔填表那 40 分鐘
        self.assertIn('第一次用這份平台履歷', prompt)
        self.assertIn('不可按送出', prompt)


    def test_direct_upload_of_custom_documents_is_checked_by_downloaded_bytes(self):
        resume = self._put_home('custom/resume.pdf', b'accepted custom resume')
        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {'status': 'accepted', 'path': 'custom/resume.pdf'},
        }
        actual_resume = self._download('resume.pdf', b'accepted custom resume')
        actual_cover = self._download('cover.pdf', b'cover bytes')

        problems = self._check(
            delivery={'method': 'direct_upload'},
            include_attachments=False,
            uploaded_files=[
                {'name': 'resume.pdf', 'path': actual_resume},
                {'name': 'cover.pdf', 'path': actual_cover},
            ],
        )

        self.assertEqual(problems, [])
        self.assertTrue(os.path.isfile(resume))
        self.assertFalse(os.path.exists(self.downloads))

    def test_no_profile_upload_of_custom_documents_is_checked_too(self):
        self._put_home('custom/resume.pdf', b'accepted custom resume')
        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {'status': 'accepted', 'path': 'custom/resume.pdf'},
        }
        uploaded_resume = self._download('resume.pdf', b'wrong custom resume')

        problems = self._check(
            delivery={'method': 'no_profile'},
            include_attachments=False,
            uploaded_files=[{'name': 'resume.pdf', 'path': uploaded_resume}],
        )

        self.assertTrue(any('內容不同' in problem for problem in problems), problems)

    def test_direct_upload_with_a_different_custom_file_is_a_problem(self):
        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {
                'status': 'accepted',
                'path': self._put_home('custom/resume.pdf', b'accepted custom resume'),
            },
        }
        wrong_resume = self._download('resume.pdf', b'another card resume')
        actual_cover = self._download('cover.pdf', b'cover bytes')

        problems = self._check(
            delivery={'method': 'direct_upload'},
            include_attachments=False,
            uploaded_files=[
                {'name': 'resume.pdf', 'path': wrong_resume},
                {'name': 'cover.pdf', 'path': actual_cover},
            ],
        )

        self.assertTrue(any('內容不同' in problem for problem in problems), problems)

    def test_direct_upload_needs_a_downloaded_copy_inside_the_temp_folder(self):
        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {
                'status': 'accepted',
                'path': self._put_home('custom/resume.pdf', b'accepted custom resume'),
            },
        }

        problems = self._check(
            delivery={'method': 'direct_upload'},
            include_attachments=False,
            uploaded_files=[{'name': 'resume.pdf', 'path': self.outside}],
        )

        self.assertTrue(any('暫存夾外' in problem for problem in problems), problems)

    def test_direct_upload_accepts_the_generated_combined_card_file(self):
        import card

        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {
                'status': 'accepted',
                'path': self._put_home('custom/resume.pdf', b'accepted custom resume'),
            },
        }
        os.makedirs(cf.SHIP_DIR, exist_ok=True)
        folder = os.path.join(
            cf.SHIP_DIR, 'job-' + card.card_id_from_url(URL),
        )
        os.makedirs(folder, exist_ok=True)
        combined = self._put_home(
            os.path.relpath(os.path.join(folder, 'combined.pdf'), self.home),
            b'combined card documents',
        )
        with open(os.path.join(folder, 'ship.json'), 'w', encoding='utf-8') as f:
            json.dump({'files': ['resume.pdf', 'cover.pdf'], 'merged': 'combined.pdf'}, f)
        actual = self._download('submitted.pdf', b'combined card documents')

        problems = self._check(
            delivery={'method': 'direct_upload'},
            include_attachments=False,
            uploaded_files=[{'name': 'submitted.pdf', 'path': actual}],
        )

        self.assertEqual(problems, [])
        self.assertTrue(os.path.isfile(combined))


    def _combined_card_file(self, contents=b'combined card documents'):
        import card
        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {
                'status': 'accepted',
                'path': self._put_home('custom/resume.pdf', b'accepted custom resume'),
            },
        }
        folder = os.path.join(cf.SHIP_DIR, 'job-' + card.card_id_from_url(URL))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, 'ship.json'), 'w', encoding='utf-8') as f:
            json.dump({'files': ['resume.pdf', 'cover.pdf'], 'merged': 'combined.pdf'}, f)
        return self._put_home(os.path.relpath(os.path.join(folder, 'combined.pdf'), self.home), contents)

    def test_platform_without_readback_checks_the_local_file_put_into_the_form(self):
        # Greenhouse 傳完只顯示檔名、沒有下載回來的入口:改對 agent 放進上傳欄的那個本機檔
        combined = self._combined_card_file()
        problems = self._check(
            delivery={'method': 'direct_upload'}, include_attachments=False, uploaded_files=[],
            extra={'upload_readback': 'unavailable',
                   'uploaded_from': [{'name': 'combined.pdf', 'path': combined}]},
        )
        self.assertEqual(problems, [])

    def test_platform_without_readback_uses_the_ship_root_apply_run_uses(self):
        # 代投驗收把投遞夾放在自己的暫存資料夾(APPLY_SHIP_ROOT):上傳檔核對也要去那裡找,不能只認正式的投遞夾
        import card
        root = os.path.join(self.home, 'accept-ship')
        folder = os.path.join(root, 'job-' + card.card_id_from_url(URL))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, 'ship.json'), 'w', encoding='utf-8') as f:
            json.dump({'files': ['resume.pdf', 'cover.pdf'], 'merged': 'combined.pdf'}, f)
        self.fb[URL]['custom_docs'] = {'resume:general:zh': {
            'status': 'accepted', 'path': self._put_home('custom/resume.pdf', b'accepted custom resume')}}
        combined = self._put_home(os.path.relpath(os.path.join(folder, 'combined.pdf'), self.home), b'combined')
        with patch.dict(os.environ, {'APPLY_SHIP_ROOT': root}):
            problems = self._check(
                delivery={'method': 'direct_upload'}, include_attachments=False, uploaded_files=[],
                extra={'upload_readback': 'unavailable',
                       'uploaded_from': [{'name': 'combined.pdf', 'path': combined}]},
            )
        self.assertFalse(any('投遞夾外' in p for p in problems), problems)

    def test_platform_without_readback_rejects_a_file_outside_the_card_folder(self):
        self._combined_card_file()
        stray = self._put_home('elsewhere/combined.pdf', b'combined card documents')
        problems = self._check(
            delivery={'method': 'direct_upload'}, include_attachments=False, uploaded_files=[],
            extra={'upload_readback': 'unavailable',
                   'uploaded_from': [{'name': 'combined.pdf', 'path': stray}]},
        )
        self.assertTrue(any('投遞夾外' in p for p in problems), problems)

    def test_direct_upload_without_custom_documents_checks_submitted_card_files(self):
        missing = self._check(
            delivery={'method': 'direct_upload'},
            include_attachments=False,
        )
        self.assertTrue(any('申請表上傳檔' in problem for problem in missing), missing)

        resume = self._download('通用版.pdf', b'resume')
        cover = self._download('求職信.pdf', b'cover bytes')
        matching = self._check(
            delivery={'method': 'direct_upload'},
            include_attachments=False,
            uploaded_files=[
                {'name': '通用版.pdf', 'path': resume},
                {'name': '求職信.pdf', 'path': cover},
            ],
        )
        self.assertEqual(matching, [])

        wrong_resume = self._download('通用版.pdf', b'old resume bytes')
        cover = self._download('求職信.pdf', b'cover bytes')
        mismatch = self._check(
            delivery={'method': 'direct_upload'},
            include_attachments=False,
            uploaded_files=[
                {'name': '通用版.pdf', 'path': wrong_resume},
                {'name': '求職信.pdf', 'path': cover},
            ],
        )
        self.assertTrue(any('通用版' in problem and '內容不同' in problem
                            for problem in mismatch), mismatch)

    def test_new_custom_profile_compares_the_custom_resume_and_attachments(self):
        import profile_sync as ps
        fixed_url = 'https://profiles.example/fixed/1'
        ps.remember(ps.profile_key(URL), 'zh', 'general', fixed_url)
        custom_resume = self._put_home('custom/resume.pdf', b'accepted custom resume')
        custom_cover = self._put_home('custom/cover.pdf', b'accepted custom cover')
        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {'status': 'accepted', 'path': 'custom/resume.pdf'},
            'attachment:cover:zh': {'status': 'accepted', 'path': 'custom/cover.pdf'},
        }
        actual_resume = self._download('resume.pdf', b'accepted custom resume')
        actual_cover = self._download('cover.pdf', b'accepted custom cover')

        fixed_cover = self._download('fixed-cover.pdf', b'cover bytes')
        problems = self._check(
            delivery={
                'method': 'platform_profile',
                'profile_url': 'https://profiles.example/custom/1',
                'profile_kind': 'custom',
            },
            attachments=[
                {'name': 'resume.pdf', 'path': actual_resume},
                {'name': 'cover.pdf', 'path': actual_cover},
            ],
            fixed_profile={'url': fixed_url,
                           'attachments': [{'name': '求職信', 'path': fixed_cover}]},
        )

        self.assertEqual(problems, [])
        self.assertTrue(os.path.isfile(custom_resume))
        self.assertTrue(os.path.isfile(custom_cover))


    def test_custom_profile_rechecks_fixed_attachments_and_names_overwrite(self):
        import profile_sync as ps

        fixed_url = 'https://profiles.example/fixed/1'
        ps.remember(ps.profile_key(URL), 'zh', 'general', fixed_url)
        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {
                'status': 'accepted',
                'path': self._put_home('custom/resume.pdf', b'accepted custom resume'),
            },
        }
        custom_resume = self._download('resume.pdf', b'accepted custom resume')
        custom_cover = self._download('cover.pdf', b'cover bytes')
        wrong_fixed_cover = self._download('fixed-cover.pdf', b'overwritten fixed cover')

        problems = self._check(
            delivery={
                'method': 'platform_profile',
                'profile_url': 'https://profiles.example/custom/1',
                'profile_kind': 'custom',
            },
            attachments=[
                {'name': 'resume.pdf', 'path': custom_resume},
                {'name': 'cover.pdf', 'path': custom_cover},
            ],
            fixed_profile={
                'url': fixed_url,
                'attachments': [{'name': '求職信', 'path': wrong_fixed_cover}],
            },
        )

        self.assertTrue(any(
            '固定平台履歷附件「求職信」內容不同（固定版被蓋掉）' in problem
            for problem in problems
        ), problems)
        self.assertFalse(os.path.exists(self.downloads))

    def test_custom_profile_url_is_not_registered_as_the_fixed_profile(self):
        import unittest.mock as mock
        import profile_sync as ps

        delivery = {
            'method': 'platform_profile',
            'profile_url': 'https://profiles.example/custom/1',
            'profile_kind': 'custom',
        }
        fixed = {'read': 'https://profiles.example/fixed/1',
                 'edit': 'https://profiles.example/fixed/1'}
        with mock.patch.object(ps, 'platform_of', return_value='Example'), \
                mock.patch.object(run, '_pick', return_value=('zh', 'general')), \
                mock.patch.object(ps, 'where', return_value=None), \
                mock.patch.object(ps, 'remember') as remember, \
                mock.patch.object(run, 'profile_check', return_value=(fixed, [], '')):
            problems = run.profile_after(
                URL, {'delivery': delivery,
                      'profile': {'url': delivery['profile_url'] + '/'}}, self.out,
            )

        self.assertEqual(problems, [])
        remember.assert_not_called()

    def test_new_tab_is_on_the_board_before_the_profile_check_tidies_chrome(self):
        # 核對平台履歷前會先整理 agent 的 Chrome(看板上沒記的分頁會被關);Claude 開的分頁不是 Codex 交接的,
        # 以前還沒記上看板就被關掉,填好的那一頁不見了
        import types
        import unittest.mock as mock
        delivery = {'method': 'platform_profile', 'profile_url': 'https://profiles.example/1', 'profile_kind': 'fixed'}
        report = {'delivery': delivery, 'fields': [], 'submitted': False,
                  'tab_id': '529692139', 'tab_url': URL + '/apply', 'handoff': True}
        self.fb[URL]['apply'] = {'tab_id': '111'}                       # 上一輪的舊分頁
        seen = []
        with mock.patch.dict(os.environ, {'AGENT_BOARD': os.path.join(self.tmp, 'board.html')}):
            with mock.patch.object(run, 'load', return_value=({URL: self.job}, self.fb)), \
                    mock.patch.object(run, 'profile_check', return_value=None), \
                    mock.patch.object(run, 'prompt_for', return_value=('prompt', self.out)), \
                    mock.patch.object(run.ar, 'run', return_value=types.SimpleNamespace(ok=True, agent_id='test-agent')), \
                    mock.patch.object(run.ar, 'session_id', return_value='apply-session'), \
                    mock.patch.object(run.ar, 'browser_runtime', return_value='claude-code'), \
                    mock.patch.object(run, 'shoot'), \
                    mock.patch.object(run, 'check_fill', return_value=([], report)), \
                    mock.patch.object(run, 'profile_after', side_effect=lambda *a, **k: seen.append(
                        run.apply_of(self.fb, URL).get('tab_id')) or []), \
                    mock.patch.object(run, '_profile_check_after_fill', return_value=[]), \
                    mock.patch.object(run.bd, 'set_fb', side_effect=lambda mut, live, by: mut(self.fb)), \
                    mock.patch.object(run.agent_report, 'resolve'), \
                    mock.patch.object(run.agent_report, 'report'), \
                    mock.patch('agent_chrome.ensure', return_value=(True, '')), \
                    mock.patch('agent_chrome.wait_claude', return_value=(True, '')):
                run._run_one('fill', URL, os.path.join(self.tmp, 'board.html'),
                             attachment_download_dir=self.downloads)
        self.assertEqual(seen, ['529692139'])

    def test_custom_profile_is_saved_on_its_card_and_fixed_profile_drift_blocks_it(self):
        import types
        import unittest.mock as mock

        delivery = {
            'method': 'platform_profile',
            'profile_url': 'https://profiles.example/custom/1',
            'profile_kind': 'custom',
        }
        report = {
            'delivery': delivery, 'fields': [], 'submitted': False,
            'tab_id': '7', 'tab_url': URL + '/apply', 'handoff': True,
        }
        with mock.patch.dict(os.environ, {'AGENT_BOARD': os.path.join(self.tmp, 'board.html')}):
            with mock.patch.object(run, 'load', return_value=({URL: self.job}, self.fb)), \
                    mock.patch.object(run, 'profile_check', return_value=None), \
                    mock.patch.object(run, 'prompt_for', return_value=('prompt', self.out)), \
                    mock.patch.object(run.ar, 'run', return_value=types.SimpleNamespace(
                        ok=True, agent_id='test-agent')), \
                    mock.patch.object(run.ar, 'session_id', return_value='apply-session'), \
                    mock.patch.object(run, 'shoot'), \
                    mock.patch.object(run, 'check_fill', return_value=([], report)), \
                    mock.patch.object(run, 'profile_after',
                                      return_value=['固定平台履歷跟母稿對不上']) as profile_after, \
                    mock.patch.object(run.bd, 'set_fb',
                                      side_effect=lambda mut, live, by: mut(self.fb)), \
                    mock.patch.object(run.agent_report, 'resolve'), \
                    mock.patch.object(run.agent_report, 'report'), \
                    mock.patch('agent_chrome.ensure', return_value=(True, '')):
                ok, message = run._run_one(
                    'fill', URL, os.path.join(self.tmp, 'board.html'),
                    attachment_download_dir=self.downloads,
                )

        self.assertFalse(ok, message)
        self.assertEqual(run.apply_of(self.fb, URL)['delivery'], delivery)
        self.assertTrue(any('固定平台履歷' in p
                            for p in run.apply_of(self.fb, URL)['issues']))
        profile_after.assert_called_once()

    def test_custom_resume_prompt_preserves_fixed_profiles_and_reports_blockers(self):
        import profile_sync as ps

        custom = self._put_home('custom/resume.pdf', b'accepted custom resume')
        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {'status': 'accepted', 'path': 'custom/resume.pdf'},
        }

        profile = run.profile_step(
            'https://www.104.com.tw/job/1', 'zh', 'general', custom, None,
            custom=True,
        )
        attachments = ps.attachment_step(
            self.job, self.fb, URL, self.downloads,
        )

        self.assertIn('固定平台履歷', profile)
        self.assertIn('另外開一份', profile)
        self.assertIn('uploaded_files', attachments)
        self.assertIn(custom, attachments)
        self.assertIn('不要刪', attachments)
        self.assertIn('格子滿', attachments)
        self.assertIn('多出來', attachments)
        self.assertIn('waitForEvent("filechooser")', attachments)
        self.assertIn('setFiles', attachments)
        self.assertIn('保留原申請頁不動', attachments)
        self.assertNotIn('curl multipart', attachments)

        local_attachments = ps.attachment_step(
            self.job, self.fb, 'http://127.0.0.1:8899/apply', self.downloads,
        )
        self.assertIn('curl multipart', local_attachments)
        self.assertIn('curl -fL', local_attachments)
        self.assertIn('不可改 Chrome 權限', local_attachments)
        self.assertIn('都會顯示「本機假驗收頁」標記', local_attachments)
        self.assertNotIn('curl fallback', attachments)
        self.assertNotIn('不可用頁面程式、fetch/HTTP、直接 multipart 請求', run.FILL)
        self.assertIn('唯一例外是本輪明確標示為本機假驗收頁', run.FILL)
        self.assertIn('舊分頁留著不算 problems', run.OPEN_STEP)
        self.assertIn('不要關任何分頁', run.OPEN_STEP)                      # 關分頁會讓外掛斷線

        no_profile = run.profile_step(
            'https://unknown.example/jobs/1', 'zh', 'general', custom, None,
            custom=True,
        )
        self.assertIn('固定平台履歷只能讀', no_profile)
        self.assertIn('直接上傳', no_profile)

    def test_custom_resume_does_not_reuse_a_fixed_profile_attachment_skip(self):
        import profile_sync as ps

        delivery = {
            'method': 'platform_profile',
            'profile_url': 'https://profiles.example/1',
            'profile_kind': 'fixed',
        }
        old_fingerprint = ps.attachment_fingerprint(self.job, self.fb, delivery)
        self.fb[URL]['apply'] = {
            'delivery': delivery,
            'attachment_cache': {
                delivery['profile_url']: {
                    'matched': True, 'fingerprint': old_fingerprint,
                    'profile_kind': 'fixed',
                },
            },
        }
        self._put_home('custom/resume.pdf', b'accepted custom resume')
        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {'status': 'accepted', 'path': 'custom/resume.pdf'},
        }

        prompt = ps.attachment_step(self.job, self.fb, URL, self.downloads)

        self.assertNotIn('本輪不必下載附件', prompt)
        self.assertIn('profile_attachments', prompt)


    def test_new_custom_profile_with_a_different_resume_is_a_problem(self):
        self._put_home('custom/resume.pdf', b'accepted custom resume')
        self.fb[URL]['custom_docs'] = {
            'resume:general:zh': {
                'status': 'accepted',
                'path': 'custom/resume.pdf',
            },
        }
        wrong_resume = self._download('resume.pdf', b'old fixed resume')
        actual_cover = self._download('cover.pdf', b'cover bytes')

        problems = self._check(
            delivery={
                'method': 'platform_profile',
                'profile_url': 'https://profiles.example/custom/1',
                'profile_kind': 'custom',
            },
            attachments=[
                {'name': 'resume.pdf', 'path': wrong_resume},
                {'name': '求職信', 'path': actual_cover},
            ],
        )

        self.assertTrue(any('通用版' in problem and '內容不同' in problem
                            for problem in problems), problems)

    def test_custom_profile_reads_the_fixed_profile_and_reports_drift(self):
        import unittest.mock as mock
        import profile_sync as ps

        delivery = {
            'method': 'platform_profile',
            'profile_url': 'https://profiles.example/custom/1',
            'profile_kind': 'custom',
        }
        fixed = {'read': 'https://profiles.example/fixed/1',
                 'edit': 'https://profiles.example/fixed/1'}
        diffs = [{'where': '求職經歷'}]
        with mock.patch.object(ps, 'platform_of', return_value='Example'), \
                mock.patch.object(run, '_pick', return_value=('zh', 'general')), \
                mock.patch.object(ps, 'where', return_value=fixed), \
                mock.patch.object(ps, 'remember') as remember, \
                mock.patch.object(run, 'profile_check', return_value=(fixed, diffs, '')):
            problems = run.profile_after(
                URL, {'delivery': delivery,
                      'profile': {'url': delivery['profile_url']}}, self.out,
            )

        self.assertTrue(any('固定平台履歷' in problem and '求職經歷' in problem
                            for problem in problems), problems)
        remember.assert_not_called()


    def test_profile_text_comparison_uses_reported_delivery_method(self):
        import unittest.mock as mock
        import profile_sync as ps

        fixed = {'read': 'https://profiles.example/fixed/1',
                 'edit': 'https://profiles.example/edit/1'}
        report = {
            'delivery': {
                'method': 'platform_profile',
                'profile_url': fixed['read'],
                'profile_kind': 'fixed',
            },
            'profile': {'url': fixed['read'],
                        'equivalents': [{'master': '可上班日：一個月內', 'platform': '一個月內可上班'}]},
        }
        with mock.patch.object(ps, 'platform_of', return_value=None), \
                mock.patch.object(ps, 'where', return_value=fixed), \
                mock.patch.object(ps, 'check', return_value=(fixed, [], '')) as check, \
                mock.patch.object(run, '_pick', return_value=('zh', 'general')):
            self.assertEqual(run.profile_after(URL, report, self.out), [])
            check.assert_called_once_with(ps.profile_key(URL), 'zh', 'general', self.out,
                                          reported=report['profile']['equivalents'], reader=None)

            self.assertEqual(
                run.profile_after(URL, {'delivery': {'method': 'direct_upload'}}, self.out),
                [],
            )
            check.assert_called_once()

    def test_custom_profile_on_the_same_page_as_the_fixed_one_keeps_reported_equivalents(self):
        # 104 的客製版跟固定版是同一頁:agent 回報的「平台用自己說法寫」講的就是程式要讀回的那一頁,要收
        # (以前客製版一律丟掉,全部照原句比,每張都判「個人名片對不上」)
        import unittest.mock as mock
        import profile_sync as ps
        page = {'read': 'https://profiles.example/preview?v=1', 'edit': 'https://profiles.example/edit?v=1'}
        eq = [{'master': '可上班日：一個月內', 'platform': '一個月內可上班'}]
        report = {'delivery': {'method': 'platform_profile', 'profile_url': page['read'], 'profile_kind': 'custom'},
                  'profile': {'url': page['read'], 'equivalents': eq}}
        with mock.patch.object(ps, 'platform_of', return_value=None), \
                mock.patch.object(ps, 'where', return_value=page), \
                mock.patch.object(ps, 'check', return_value=(page, [], '')) as check, \
                mock.patch.object(run, '_pick', return_value=('zh', 'general')):
            run.profile_after(URL, report, self.out)
            self.assertEqual(check.call_args.kwargs['reported'], eq)
            other = dict(page, read='https://profiles.example/preview?v=2')   # 客製版是另一頁:講的不是同一份,不收
            with mock.patch.object(ps, 'where', return_value=other):
                run.profile_after(URL, report, self.out)
            self.assertIsNone(check.call_args.kwargs['reported'])

    def test_first_platform_profile_location_and_history_page_are_saved(self):
        delivery = {'method': 'platform_profile', 'profile_url': 'https://new-platform.example/profile',
                    'profile_kind': 'fixed'}
        with patch.object(run, '_pick', return_value=('en', 'general')), \
                patch.object(run, 'profile_check', return_value=({'read': delivery['profile_url']}, [], '')):
            problems = run.profile_after(URL, {
                'delivery': delivery,
                'profile': {'url': delivery['profile_url'], 'edit': delivery['profile_url'] + '/edit',
                            'application_history_url': 'https://new-platform.example/applications'},
            }, self.out)
        self.assertEqual(problems, [])
        self.assertEqual(self.ps.where('new-platform.example', 'en', 'general'),
                         {'read': delivery['profile_url'], 'edit': delivery['profile_url'] + '/edit'})
        self.assertEqual(self.ps.application_record_pages(),
                         [{'platform': 'new-platform.example', 'url': 'https://new-platform.example/applications'}])

    def test_profile_differences_block_right_after_the_fill(self):
        # 沒有「先不比、等欄位對照」那回事了:填完就比,對不上的格子直接擋
        delivery = {'method': 'platform_profile', 'profile_url': 'https://new-platform.example/profile',
                    'profile_kind': 'fixed'}
        found = ({'read': delivery['profile_url']}, [{'where': '社團[9]', 'missing': ['任期一學年']}], '')
        with patch.object(run, '_pick', return_value=('en', 'general')), \
                patch.object(self.ps, 'where', return_value={'read': delivery['profile_url']}), \
                patch.object(run, 'profile_check', return_value=found):
            problems = run.profile_after(URL, {'delivery': delivery}, self.out)
        self.assertTrue(any('社團[9]' in p for p in problems), problems)

    def test_fill_prompt_does_not_list_fixed_profile_attachments(self):
        self.fb[URL]['apply'] = {'delivery': {'method': 'platform_profile', 'profile_kind': 'fixed',
                                              'profile_url': 'https://profiles.example/1'}}
        step = self.ps.attachment_step(self.job, self.fb, URL, self.downloads, verify_profile=False)
        self.assertNotIn('這張卡要用的平台履歷附件', step)
        self.assertIn('不用看平台履歷上的附件', step)
        self.assertIn('這張卡要用的平台履歷附件',
                      self.ps.attachment_step(self.job, self.fb, URL, self.downloads, verify_profile=True))

    def test_attachments_are_taken_one_per_tab_not_by_chrome_download(self):
        # Mac 上 Chrome 一般下載會跳到最前面、切走使用者畫面;外掛 evaluate 的環境沒有 fetch;
        # 同一頁下載第二個檔會卡在「要下載多個檔案」的詢問。核對、填表的指示都要照「一個檔一個新分頁 downloadMedia」
        self.fb[URL]['apply'] = {'delivery': {'method': 'platform_profile', 'profile_kind': 'fixed',
                                              'profile_url': 'https://profiles.example/1'}}
        for verify in (True, False):
            step = self.ps.attachment_step(self.job, self.fb, URL, self.downloads, verify_profile=verify)
            self.assertIn('downloadMedia()', step)
            self.assertIn('一個檔開一個新的背景分頁', step)
            self.assertIn('不要點下載連結', step)
            self.assertNotIn('fetch(href', step)
            self.assertNotIn('照檔名到那裡找', step)

    def test_the_fetch_rule_is_given_per_runtime(self):
        # 取檔規則是 Codex 外掛的做法(downloadMedia、timeout_ms、REPL、renameSync);Claude 收到同一份再加對照表,
        # 對照表又叫它 base64 回傳(javascript_tool 超過 1000 字會截斷,編碼回傳會被安全過濾擋,ADR 0003):兩邊矛盾。
        # 照執行者給:Claude 照實講取不回、寫進 problems,不叫它試做不到的路
        self.fb[URL]['apply'] = {'delivery': {'method': 'platform_profile', 'profile_kind': 'fixed',
                                              'profile_url': 'https://profiles.example/1'}}
        codex = self.ps.attachment_step(self.job, self.fb, URL, self.downloads, verify_profile=True)
        claude = self.ps.attachment_step(self.job, self.fb, URL, self.downloads, verify_profile=True,
                                         runtime='claude-code')
        self.assertIn('downloadMedia()', codex)
        for word in ('downloadMedia', 'timeout_ms', 'renameSync', 'REPL', 'base64'):
            self.assertNotIn(word, claude)
        self.assertIn('problems', claude)
        self.assertIn('upload_readback', claude)          # 申請表上傳檔走「讀不回、程式核對本機檔」那條
        self.assertIn('profile_attachments(和 fixed_profile 的 attachments)回報空清單', claude)   # 卡上才寫「沒下載到」,不是「沒回報」
        rule = run.ar.apply_rule('claude-code')
        self.assertNotIn('base64', rule)                  # 對照表不再教一條做不到的路
        self.assertNotIn('downloadMedia', self.ps.attachment_step(
            self.job, self.fb, URL, self.downloads, verify_profile=False, runtime='claude-code'))
        with patch.object(run.ar, 'claude_paired_device', return_value='dev'):
            prompt, _out = run.pre_submit_prompt(URL, self.job, self.fb, self.out, self.downloads,
                                                 runtime='claude-code')
        self.assertNotIn('downloadMedia', prompt)

    def test_after_fill_check_does_not_refetch_unchanged_attachments(self):
        # 附件沒變時,填完後的核對不要再叫 agent 把平台上的附件全部下載一次
        delivery = {'method': 'platform_profile', 'profile_url': 'https://profiles.example/1',
                    'profile_kind': 'fixed'}
        self.fb[URL]['apply'] = {'delivery': delivery}
        self.ps.remember_attachment_check(delivery['profile_url'],
                                          self.ps.attachment_fingerprint(self.job, self.fb, delivery), 'fixed', True)
        with patch.object(run, 'out_dir', return_value=self.out):
            after_fill, _ = run.pre_submit_prompt(URL, self.job, self.fb, None, self.downloads, after_fill=True)
            before_submit, _ = run.pre_submit_prompt(URL, self.job, self.fb, None, self.downloads)
        self.assertIn('本輪不必下載附件', after_fill)
        self.assertNotIn('逐一下載到這個暫存資料夾', after_fill)
        self.assertIn('逐一下載到這個暫存資料夾', before_submit)    # 真的要送出前的核對照舊要取
