import copy
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: F401 — 測試先隔離資料與瀏覽器設定，再匯入工具。
import apply_run as run
import apply_tab
import config as cf
import delivery_state as ds
import gate
import profile_sync as ps
import reply_run as replies

URL = 'https://platform.example.test/jobs/7'
FIXED = 'https://platform.example.test/profile/fixed'
CUSTOM = 'https://platform.example.test/profile/custom'


class Responsibilities(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.out = Path(self.home) / 'out'
        self.out.mkdir()
        self.source = Path(self.home) / 'resume.md'
        self.source.write_text('# 履歷\n## 專案\nDelta（Repo）：維運排程與部署\n[影片](https://media.example.test/watch/a)\n')
        self.pages = {FIXED: {'url': FIXED, 'text': '專案\nDelta\n維運排程與部署',
                              'fields': [],
                              'links': ['https://repo.example.test/project'],
                              'media': ['https://media.example.test/embed/a']}}
        self.delivery = {'method': 'platform_profile', 'profile_kind': 'fixed', 'profile_url': FIXED}
        self.decision = {'platform': ps.profile_key(URL), 'lang': 'zh', 'variant': 'base',
                         'profile_kind': 'fixed', 'fixed_url': FIXED}
        self.jobs, self.fb = {URL: {'id': URL}}, {URL: {'app': 'ship', 'apply': {'delivery': self.delivery}}}
        self.status = 'complete'
        self.calls = []
        self.fingerprint = 'current'
        self.door = SimpleNamespace(agent_id='original-agent', runtime='codex', native_json_output=True,
                                    profile_reader=lambda logs, board: lambda url: copy.deepcopy(self.pages[url]))
        for target, value in (
            ('apply_run.load', lambda board: (self.jobs, self.fb)),
            ('apply_run.out_dir', lambda url, board: str(self.out)),
            ('profile_sync.decided', lambda *args: dict(self.decision)),
            ('profile_sync.delivery_for', lambda *args: dict(self.delivery)),
            ('profile_sync.attachment_fingerprint', lambda *args: self.fingerprint),
            ('config.master', lambda *args: str(self.source)),
            ('agent_run.session_id', lambda log: 'same-session'),
            ('evidence.active', lambda: None),
        ):
            self.enterContext(patch(target, side_effect=value))
        self.enterContext(patch.object(cf, 'HOME', self.home))
        self.enterContext(patch.object(ps, 'REG', str(Path(self.home) / 'profiles.json')))
        ps.remember(self.decision['platform'], 'zh', 'base', FIXED)

    def judge(self, prompt, log, home, board, **kwargs):
        self.calls.append((prompt, kwargs))
        material = json.loads(prompt.split('【材料】\n', 1)[1].split('\n【交件單】', 1)[0])
        (self.out / 'profile-review.json').write_text(json.dumps({
            'status': self.status, 'reason': '依本輪原稿與頁面判讀',
            'quotes': {url: page['text'] for url, page in self.pages.items()},
            'checks': {p['url']: [{'id': i, 'status': self.status, 'reason': '本輪逐項判讀', 'basis': [i]}
                                   for i, item in p['items'].items() if item['kind'] != 'approved'] for p in material},
        }, ensure_ascii=False))
        return SimpleNamespace(ok=True, message=lambda: '')

    def review(self, judge=None):
        with patch('apply_run._run_agent', side_effect=judge or self.judge):
            return run._review_profile(URL, 'synthetic-board', 'same-session', self.door, self.delivery)

    def test_layout_and_media_variation_does_not_override_content_judgment(self):
        self.assertEqual(self.review(), [])
        prompt, kwargs = self.calls[0]
        self.assertIn('Delta（Repo）', prompt)
        self.assertIn('media.example.test/embed/a', prompt)
        self.assertNotIn('resume', kwargs)            # 判讀開新的一段,不背著整段填表紀錄
        self.assertFalse(kwargs['browser_required'])  # 只看程式給的材料,不開瀏覽器
        self.assertEqual(kwargs['agent_id'], 'original-agent')
        self.assertEqual(kwargs['output_last_message'], str(self.out / 'profile-review.json'))
        self.assertIn('不用另開工具寫檔', prompt)
        for state in ('issues', 'unknown'):
            self.status = state
            self.pages[FIXED]['text'] += '\n' + state
            self.assertTrue(self.review())

    def test_judgment_records_which_master_the_platform_copy_was_checked_against(self):
        with patch('profile_sync._remember_check') as remember:
            for state in ('complete', 'issues', 'unknown'):
                self.status = state
                self.source.write_text(self.source.read_text() + '\n' + state)   # 原稿改了才重判,不沿用上次
                self.review()
        self.assertEqual([c.args for c in remember.call_args_list],
                         [(self.decision['platform'], 'zh', 'base', True),
                          (self.decision['platform'], 'zh', 'base', False)])   # 讀不清楚不算比過

    def test_a_passed_judgment_remembers_which_master_the_platform_now_matches(self):
        self.assertEqual(self.review(), [])
        self.assertEqual(ps.synced_source(self.decision['platform'], 'zh', 'base'), self.source.read_text())

    def test_attachments_are_compared_by_the_program_before_the_agent_is_told_to_reupload(self):
        fixed = {'method': 'platform_profile', 'profile_kind': 'fixed', 'profile_url': FIXED}
        door = SimpleNamespace(download_attachments=lambda *a: None)
        with patch('profile_sync.fixed_profile_delivery', return_value=fixed), \
                patch('profile_sync.decided', return_value={'profile_kind': 'fixed'}), \
                patch('profile_sync.attachment_sources', return_value=[{'id': 'a'}]), \
                patch('profile_sync.attachment_fingerprint', return_value='now'), \
                patch('profile_sync.check_attachments', return_value=[]) as compare:
            with patch('profile_sync.attachment_check', return_value={}):
                run._attachments_before_fill(URL, 'synthetic-board', door)
            self.assertEqual(compare.call_args.kwargs['download_reader'], door.download_attachments)
            compare.reset_mock()
            with patch('profile_sync.attachment_check', return_value={'matched': True, 'fingerprint': 'now'}):
                run._attachments_before_fill(URL, 'synthetic-board', door)      # 用現在的檔比過:不再下載
            compare.assert_not_called()

    def test_only_unchanged_items_whose_basis_is_still_there_are_carried_over(self):
        item = lambda kind, value: {'kind': kind, 'value': value}
        before = {'source:0': item('source', '專案 Delta'), 'text:0': item('text', 'Delta'), 'text:1': item('text', '台北市')}
        prior = {'rules': 'R', 'items': {FIXED: before}, 'sheet': {'status': 'complete', 'checks': {FIXED: [
            {'id': 'source:0', 'status': 'complete', 'reason': 'ok', 'basis': ['text:0']},
            {'id': 'text:0', 'status': 'complete', 'reason': 'ok', 'basis': ['source:0']},
            {'id': 'text:1', 'status': 'complete', 'reason': 'ok', 'basis': ['text:1']}]}}}
        # 頁面多了一行在最前面:編號全部位移,沒變的照樣認得,依據跟著換成新編號
        now = {'source:0': item('source', '專案 Delta'), 'text:0': item('text', '新北市'),
               'text:1': item('text', 'Delta'), 'text:2': item('text', '台北市')}
        carried, pending = run._carry_over(prior, [{'url': FIXED, 'items': now}], 'R')
        self.assertEqual({r['id']: r['basis'] for r in carried[FIXED]},
                         {'source:0': ['text:1'], 'text:1': ['source:0'], 'text:2': ['text:2']})
        self.assertEqual(pending[FIXED], {'text:0'})
        # 依據那一行不見了:靠它的那一項要重判
        gone = {'source:0': item('source', '專案 Delta'), 'text:0': item('text', '台北市')}
        carried, pending = run._carry_over(prior, [{'url': FIXED, 'items': gone}], 'R')
        self.assertEqual(pending[FIXED], {'source:0'})
        self.assertEqual(run._carry_over(prior, [{'url': FIXED, 'items': now}], '新規則'), ({}, None))   # 規則改了:全部重判

    def test_changed_source_and_changed_effective_files_invalidate_review(self):
        def changed_source(*args, **kwargs):
            result = self.judge(*args, **kwargs)
            self.source.write_text('更新後原稿')
            return result
        self.assertTrue(any('改了' in p for p in self.review(changed_source)))
        def changed_files(*args, **kwargs):
            result = self.judge(*args, **kwargs)
            self.fingerprint = 'newly-accepted-custom-file'
            return result
        self.assertTrue(any('改了' in p for p in self.review(changed_files)))

    def test_fixed_and_custom_get_their_own_source_in_one_judgment(self):
        custom = Path(self.home) / 'accepted.md'
        custom.write_text('這張卡已接受的客製內容')
        self.delivery.update(profile_kind='custom', profile_url=CUSTOM)
        self.decision['profile_kind'] = 'custom'
        self.pages[CUSTOM] = {'url': CUSTOM, 'text': '這張卡已接受的客製內容', 'fields': []}
        with patch('ship.documents', return_value=[{'kind': 'resume', 'effective_path': str(custom)}]):
            self.assertEqual(self.review(), [])
        self.assertEqual(len(self.calls), 1)
        self.assertIn('這張卡已接受的客製內容', self.calls[0][0])
        self.assertIn('Delta（Repo）', self.calls[0][0])
        self.delivery['profile_url'] = FIXED
        self.assertTrue(any('不能覆寫固定版' in p for p in self.review()))

    def test_missing_quote_wrong_page_or_missing_source_is_not_complete(self):
        def forged_quote(*args, **kwargs):
            result = self.judge(*args, **kwargs)
            (self.out / 'profile-review.json').write_text(json.dumps({
                'status': 'complete', 'reason': 'claimed', 'quotes': {FIXED: '沒有出現的原文'},
            }))
            return result
        self.assertTrue(self.review(forged_quote))
        for wrong in (CUSTOM, 'https://other.example.invalid/profile', ''):
            self.pages[FIXED]['url'] = wrong
            self.assertTrue(self.review())
        self.source.unlink()
        self.assertTrue(self.review())

    def test_unchanged_inputs_reuse_judgment_but_every_custom_page_needs_a_quote(self):
        self.assertEqual(self.review(), [])
        self.assertEqual(self.review(), [])
        self.assertEqual(len(self.calls), 1)
        self.source.write_text('改過的原稿')  # 輸入變了才重判;Agent 沒交件單就不能放行
        self.assertTrue(self.review(lambda *a, **kw: SimpleNamespace(ok=True, message=lambda: '')))
        verdict = gate.inspect('profile_review', {
            'status': 'complete', 'reason': 'complete', 'quotes': {FIXED: self.pages[FIXED]['text']},
        }, gate.Truth(given={FIXED: self.pages[FIXED]['text'], CUSTOM: '客製版原文'}))
        self.assertTrue(verdict.problems)

    def test_same_text_does_not_hide_changed_fields_context_or_inputs(self):
        self.source.write_text('上班時段：日班\n完成 170 個工單')
        self.pages[FIXED].update(text=self.source.read_text(), fields=[
            {'label': '上班時段', 'section': '求職條件', 'value': ''},
            {'label': '身高', 'value': 0}, {'label': '意願', 'value': False},
            {'label': '自傳', 'value': self.source.read_text() + '\nrecruiter@example.test'},
        ])
        self.assertEqual(self.review(), [])  # mocked Agent；這裡只驗材料／沿用，不能聲稱模型判對。
        self.assertIn('"value": 0', self.calls[-1][0])
        self.assertIn('"value": false', self.calls[-1][0])
        self.assertIn('recruiter@example.test', self.calls[-1][0])
        changes = [lambda: self.pages[FIXED]['fields'][0].update(value='日班'),
                   lambda: self.pages[FIXED]['fields'][0].update(section='過去工作'),
                   lambda: self.source.write_text('新原稿')]
        for mutate in changes:
            before = len(self.calls)
            mutate()
            self.assertEqual(self.review(), [])
            self.assertEqual(len(self.calls), before + 1)
            self.assertIn('這一次只判讀這些編號', self.calls[-1][0])     # 只交有變的那幾項
        before = len(self.calls)
        self.fingerprint = 'new-attachments'      # 附件程式自己逐位元組比;頁面每一項都沒變就不叫 agent
        self.assertEqual(self.review(), [])
        self.assertEqual(len(self.calls), before)
        with patch.object(run, 'PROFILE_REVIEW_RULES', run.PROFILE_REVIEW_RULES + '新版規則'):
            before = len(self.calls)
            self.assertEqual(self.review(), [])
            self.assertEqual(len(self.calls), before + 1)

    def test_coverage_cannot_be_forged_or_overridden_by_global_complete(self):
        self.pages[FIXED]['fields'] = [{'label': '上班時段', 'value': ''}]
        def corrupt(mutate):
            def judge(*args, **kwargs):
                result = self.judge(*args, **kwargs)
                path = self.out / 'profile-review.json'
                sheet = json.loads(path.read_text())
                mutate(sheet['checks'][FIXED])
                path.write_text(json.dumps(sheet))
                return result
            return judge
        for mutate in (lambda rows: rows.pop(), lambda rows: rows.append(rows[0]),
                       lambda rows: rows[-1].update(basis=['invented:0']),
                       lambda rows: rows[-1].update(basis=[]),
                       lambda rows: rows[-1].update(status='issues', reason='實際時段仍空白'),
                       lambda rows: rows[-1].update(status='unknown', reason='讀不到時段')):
            self.assertTrue(self.review(corrupt(mutate)))
        self.assertNotIn('content_review', ps.where(self.decision['platform'], 'zh', 'base'))

    def test_unsourced_content_goes_to_the_report_and_only_his_done_approves_it(self):
        self.pages[FIXED]['fields'] = [{'label': '性別', 'section': '基本資料', 'value': '男'}]
        platform = self.decision['platform']
        def marked(status):
            def judge(*args, **kwargs):
                result = self.judge(*args, **kwargs)
                path = self.out / 'profile-review.json'
                sheet = json.loads(path.read_text())
                sheet['status'] = 'issues'
                for row in sheet['checks'][FIXED]:
                    if row['id'] == 'field:0':
                        row.update(status=status, reason='性別：男')
                path.write_text(json.dumps(sheet, ensure_ascii=False))
                return result
            return judge
        # 原稿寫了卻不一樣是 issues:不會變成可以按「處理好了」核准的東西
        self.assertTrue(self.review(marked('issues')))
        self.assertIsNone(run._unsourced(str(self.out)))
        problems = self.review(marked('unsourced'))
        self.assertIn('要你確認一次', problems[0])
        self.assertIn('「性別：男」', problems)
        pending = run._unsourced(str(self.out))
        self.assertEqual([(p['item'], p['statement']) for p in pending], [('field:0', '性別：男')])
        inbox = self.fb.setdefault('__inbox__', [])
        inbox.append({'id': 'r1', 'approve': pending})                                        # 還沒按
        inbox.append({'id': 'r2', 'approve': pending, 'done': '2026-10-05', 'res': '重填成功'})  # 程式自動收的
        run._take_confirmed(self.fb, platform)
        self.assertEqual(ps.approved_facts(platform), [])
        inbox[0]['done'] = '2026-10-05'                                                        # 他按了「處理好了」
        self.assertEqual(self.review(), [])
        self.assertEqual([f['statement'] for f in ps.approved_facts(platform)], ['性別：男'])
        self.assertIn('性別：男', self.calls[-1][0])                                            # 下一次判讀看得到他確認過

    def test_approval_keeps_original_context_and_only_adds_a_source(self):
        self.pages[FIXED]['fields'] = [{'label': '公司', 'section': '過去經歷', 'value': 'Acme'}]
        self.assertEqual(self.review(), [])
        payload = json.loads((self.out / 'profile-material.json').read_text())
        fact = ps.approve_fact(self.decision['platform'], payload, FIXED, 'field:0', '我過去在 Acme 工作')
        self.assertEqual(fact['context']['item']['value']['section'], '過去經歷')
        self.assertEqual(fact['context']['profile_identity'], ps.identity(FIXED))
        ps.remember(self.decision['platform'], 'en', 'other', CUSTOM)
        self.assertEqual(ps.approved_facts(self.decision['platform']), [fact])
        before = len(self.calls)
        self.assertEqual(self.review(), [])
        self.assertEqual(len(self.calls), before)   # 每一項上次都判過、內容沒變:多一筆核准不用重判
        self.pages[FIXED]['fields'][0]['value'] = 'Acme Inc.'
        self.assertEqual(self.review(), [])
        self.assertEqual(len(self.calls), before + 1)
        self.assertIn('我過去在 Acme 工作', self.calls[-1][0])
        with patch.object(cf, 'HOME', self.home + '-another-user'):
            self.assertEqual(ps.approved_facts(self.decision['platform']), [])
            with self.assertRaises(ValueError):
                ps.approve_fact(self.decision['platform'], payload, FIXED, 'field:0', '另一個人的事實')
        for page, item, statement in ((CUSTOM, 'field:0', 'x'), (FIXED, 'source:0', 'x'), (FIXED, 'field:0', '')):
            with self.assertRaises(ValueError):
                ps.approve_fact(self.decision['platform'], payload, page, item, statement)

    def test_incomplete_capture_is_not_a_pure_text_profile(self):
        self.pages[FIXED].pop('fields')
        self.assertTrue(self.review())
        self.assertEqual(self.calls, [])
        self.pages[FIXED].update(fields=[], readyState='loading')
        self.assertTrue(self.review())
        self.assertEqual(self.calls, [])
        self.pages[FIXED].update(readyState='interactive', _ready=False)   # 讀頁程式自己說沒讀完
        self.assertTrue(self.review())
        self.assertEqual(self.calls, [])

    def test_a_page_the_reader_finished_is_judged_even_if_still_interactive(self):
        # 104 這種網頁內容讀完、穩定了,document 仍停在 interactive;讀完由讀頁程式判斷,判讀不另訂(實站 2026-10-05)
        self.pages[FIXED].update(fields=[], readyState='interactive', _ready=True)
        self.assertEqual(self.review(), [])
        self.assertEqual(len(self.calls), 1)

    def test_shared_native_reader_keeps_field_context_and_selection(self):
        script = """
const assert = require('node:assert/strict');
const group = {getAttribute: () => null, querySelector: () => ({innerText: '過去經歷'})};
const field = (type, value, label) => ({type, value, tagName: 'INPUT', checked: false,
  getAttribute: key => key === 'aria-label' ? label : null,
  closest: () => group, getClientRects: () => [{}]});
const controls = [field('number', '0', '年資'), field('email', 'user@example.test', 'Email'),
                  field('checkbox', 'on', '可遠端')];
global.getComputedStyle = () => ({display: 'block', visibility: 'visible'});global.NodeFilter = {SHOW_TEXT: 4};
global.location = {href: 'https://platform.example.test/profile/fixed'};
global.document = {title: '履歷', readyState: 'complete', body: {innerText: '過去經歷'}, links: [],
  querySelector: () => null, getElementById: () => null, createTreeWalker: () => ({nextNode: () => null}),
  querySelectorAll: selector => selector.startsWith('input,') ? controls : []};
const page = (READER)();
assert.equal(page.fields.length, 3);
assert.equal(page.fields[0].value, '0');
assert.equal(page.fields[0].section, '過去經歷');
assert.equal(page.fields[1].value, 'user@example.test');
assert.equal(page.fields[2].checked, false);
let selected = 'true';
controls[0].getAttribute = key => key === 'role' ? 'option' : key === 'aria-selected' ? selected : null;
const first = (READER)();
selected = 'false';
const second = (READER)();
assert.equal(first.text, second.text);
assert.notDeepEqual(first.fields, second.fields);
""".replace('READER', apply_tab.PROFILE_FN)
        result = subprocess.run(['node', '-'], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_hash_and_pdf_text_share_the_same_captured_bytes(self):
        import hashlib
        pdf = Path(self.home) / 'original.pdf'
        original = b'Original source A'
        pdf.write_bytes(original)
        def extract(captured_path):
            pdf.write_bytes(b'Changed source B')
            text = Path(captured_path).read_bytes().decode()
            pdf.write_bytes(original)
            return text
        with patch('settings_api.pdf_text', side_effect=extract):
            captured = ps.capture_source(str(pdf))
        self.assertEqual(captured, {'sha256': hashlib.sha256(original).hexdigest(), 'text': original.decode()})

    def test_concurrent_cache_write_cannot_erase_a_human_approval(self):
        from concurrent.futures import ThreadPoolExecutor
        import fcntl
        import threading
        self.assertEqual(self.review(), [])
        payload = json.loads((self.out / 'profile-material.json').read_text())
        entered, release = threading.Event(), threading.Event()
        original = ps.registry
        def paused_read():
            reg = original()
            if not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('測試交錯等待逾時')
            return reg
        with patch.object(ps, 'registry', side_effect=paused_read), ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(ps.remember_review, self.decision['platform'], 'zh', 'base', FIXED, 'later', {'status': 'complete'})
            try:
                self.assertTrue(entered.wait(5))
                with open(ps.REG + '.lock', 'w') as lock:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                human = pool.submit(ps.approve_fact, self.decision['platform'], payload, FIXED, 'text:0', '我核准這段內容')
            finally:
                release.set()
            writer.result(timeout=5)
            human.result(timeout=5)
        self.assertEqual(len(ps.approved_facts(self.decision['platform'])), 1)
        self.assertEqual(ps.where(self.decision['platform'], 'zh', 'base')['content_review']['fingerprint'], 'later')

    def test_broken_registry_and_context_are_not_silently_endorsed(self):
        bad = {'statement': '過去在 Acme 工作', 'scope': self.home, 'context': {}}
        ps._save_registry({self.decision['platform']: {'approved_facts': [bad]}})
        with self.assertRaises(ValueError):
            ps.approved_facts(self.decision['platform'])
        Path(ps.REG).write_text('{broken')
        with self.assertRaises(ValueError):
            ps.remember(self.decision['platform'], 'zh', 'base', FIXED)
        self.assertEqual(Path(ps.REG).read_text(), '{broken')

    def test_text_date_meaning_is_judged_but_calendar_and_source_are_checked(self):
        text = '刊登日期：2026年九月七日'
        sheet = {'dates': [{'url': URL, 'posted_at': '2026-09-07', 'source': text}]}
        truth = gate.Truth(given={URL: text}, fetched_on={URL: '2026-10-02'})
        verdict = gate.inspect('posted_at', sheet, truth)
        self.assertFalse(verdict.problems)
        self.assertEqual(verdict.rows['dates'][0].judged['posted_at'], '2026-09-07')
        for field, value in [('posted_at', '2026-02-31'), ('source', '不在原文的日期')]:
            broken = copy.deepcopy(sheet)
            broken['dates'][0][field] = value
            self.assertTrue(gate.inspect('posted_at', broken, truth).problems)

    def test_summary_unit_conversion_needs_real_jd_quote(self):
        import gate_flows
        text = '年薪 NT$100K 至 NT$120K'
        truth = SimpleNamespace(given=text)
        self.assertIsNone(gate_flows._card_summary({'salary': '年薪 100,000–120,000 元', 'quote': text}, {}, truth))
        self.assertTrue(gate_flows._card_summary({'salary': '年薪 100,000–120,000 元', 'quote': '原文不存在'}, {}, truth))

    def test_send_result_is_not_inferred_from_success_words_or_retained_form(self):
        page = {'url': URL, 'lines': ['申請已收到', 'Thank you for your interest'],
                'fields': [{'label': '履歷', 'value': 'base'}]}
        t0 = time.time() - 1
        (self.out / 'submit.png').write_bytes(b'PNG')
        for result, quote, expected in [(True, '申請已收到', True), (None, 'Thank you for your interest', False)]:
            (self.out / 'submit.json').write_text(json.dumps({
                'submitted': result, 'clicked': True, 'reason': '本次結果判讀', 'confirm_url': URL, 'confirm_text': quote,
            }))
            ok, report = run.check_submit(str(self.out), t0, URL, page)
            self.assertEqual(ok, expected)
            self.assertNotIn('not_sent', report)
        ok, report = run.check_submit(str(self.out), t0, URL, None)
        self.assertFalse(ok)
        self.assertNotIn('not_sent', report)

    def test_possible_send_during_fill_prevents_blind_retry(self):
        self.fb[URL].update(ds='running')
        with patch('board_doc.set_fb', side_effect=lambda callback, **kwargs: callback(self.fb)), patch('agent_report.report'):
            run._unsure_while_filling(URL, 'synthetic-board', {'clicked': True}, 'fill.png')
        self.assertEqual(ds.state(self.fb[URL]), 'unsure')
        self.assertFalse(ds.allowed(self.fb[URL], 'fill_start'))
        self.assertFalse(ds.allowed(self.fb[URL], 'fix_start'))

    def test_check_fill_distinguishes_explicit_unknown_from_legacy_missing_result(self):
        for fields, expected in [({}, False), ({'submitted': False, 'clicked': False}, False),
                                 ({'submitted': None}, True), ({'submitted': None, 'clicked': False}, True),
                                 ({'submitted': True}, True), ({'submitted': False, 'clicked': True}, True),
                                 ({'submitted': 'true'}, True), ({'submitted': 'false'}, True),
                                 ({'submitted': 0}, True)]:
            (self.out / 'fill.json').write_text(json.dumps(dict(fields, reason='本輪結果判讀')))
            _bad, report = run.check_fill(self.fb, URL, str(self.out), 0)
            self.assertEqual(report['submit_claimed'], expected, fields)

    def test_category_regex_reuses_the_existing_javascript_compatibility_check(self):
        for expression, accepted in [('(?<role>Engineer)', True), ('(?P<role>Engineer)', False)]:
            sheet = {'categories': [{'name': '工程', 'match': expression}]}
            self.assertEqual(not gate.inspect('suggest_cats', sheet, gate.Truth()).problems, accepted)

    def test_unverified_reply_keeps_watermark_and_can_later_be_verified(self):
        self.fb = {URL: {'app': 'sent', 'sent_at': '2026-09-01', 'ev': '只有送出頁',
                         'replies': {'items': [], 'at': '2026-09-20'}}}
        item = {'src': 'email', 'source_type': 'email', 'source_ref': 'email:abc',
                'date': '2026-09-30', 'subject': '回覆', 'snippet': '拒絕', 'link': 'https://mail.example.test/abc',
                'kind': 'reject', '_source_verified': True, 'unverified': True}
        replies.apply_results(self.fb, {URL: [item]}, [URL], '2026-10-02', unverified=[URL])
        self.assertNotIn('oc', self.fb[URL])
        self.assertEqual(self.fb[URL]['ev'], '只有送出頁')
        self.assertEqual(self.fb[URL]['replies']['at'], '2026-09-20')
        item.pop('unverified')
        replies.apply_results(self.fb, {URL: [item]}, [URL], '2026-10-03')
        self.assertEqual(self.fb[URL]['oc'], 'rej')
        self.assertNotIn('ev', self.fb[URL])
        self.assertEqual(len(self.fb[URL]['replies']['items']), 1)
        self.assertNotIn('unverified', self.fb[URL]['replies']['items'][0])

    def test_verified_upgrade_respects_human_veto_and_ambiguous_legacy_history(self):
        item = {'src': 'email', 'source_type': 'email', 'source_ref': 'email:abc', 'date': '2026-09-30',
                'subject': '回覆', 'snippet': '拒絕', 'kind': 'reject', 'unverified': True,
                'id': 'historical-id', 'maybe_ok': True, 'done': True}
        for flags in ({}, {'effect_pending': True, 'effect_rejected': True}):
            fb = {URL: {'app': 'sent', 'replies': {'items': [dict(item, **flags)]}}}
            verified = dict(item, _source_verified=True)
            verified.pop('unverified')
            replies.apply_findings(fb, {URL: [verified]}, '2026-10-02')
            stored = fb[URL]['replies']['items'][0]
            self.assertNotIn('oc', fb[URL])
            self.assertNotIn('oc_auto', fb[URL])
            self.assertNotIn('unverified', stored)
            self.assertEqual(stored['id'], 'historical-id')
            self.assertTrue(stored['maybe_ok'])
            self.assertTrue(stored['done'])
            self.assertEqual(stored.get('effect_rejected'), flags.get('effect_rejected'))

    def test_board_restore_records_veto_and_snackbar_undo_restores_it(self):
        source = (Path(__file__).resolve().parents[1] / 'board' / 'board.js').read_text()
        start = source.index("var oun=e.target.closest('[data-ocundo]');")
        end = source.index("var rmb=e.target.closest('[data-rpmaybe]');", start)
        script = """
const assert = require('node:assert/strict');
const handler = new Function('e', 'FB', 'setOutcome', 'markDirty', 'patchInPlace', 'snack', 'ocLabel', process.argv[1]);
for (const prior of [undefined, true]) {
  const receipt = {id:'receipt'};
  if (prior !== undefined) receipt.effect_rejected = prior;
  const previous = {from:'', by:'receipt', s:'rej'};
  const card = {oc:'rej', oc_auto:previous, replies:{items:[receipt]}};
  let undo;
  handler({target:{closest:()=>({getAttribute:()=>'card'})}}, {card},
    (item, outcome)=>{if(outcome)item.oc=outcome;else delete item.oc;},
    ()=>{}, ()=>{}, (_message, callback)=>{undo=callback;}, x=>x);
  assert.equal(receipt.effect_rejected, true);
  assert.equal(card.oc, undefined);
  assert.equal(card.oc_auto, undefined);
  undo();
  assert.equal(receipt.effect_rejected, prior);
  assert.equal(card.oc, 'rej');
  assert.equal(card.oc_auto, previous);
}
"""
        result = subprocess.run(['node', '-e', script, source[start:end]], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
