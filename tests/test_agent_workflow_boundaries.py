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
        self.door = SimpleNamespace(agent_id='original-agent', runtime='codex',
                                    profile_reader=lambda board: lambda url: copy.deepcopy(self.pages[url]))
        for target, value in (
            ('apply_run.load', lambda board: (self.jobs, self.fb)),
            ('apply_run.out_dir', lambda url, board: str(self.out)),
            ('profile_sync.decided', lambda *args: dict(self.decision)),
            ('profile_sync.delivery_for', lambda *args: dict(self.delivery)),
            ('config.master', lambda *args: str(self.source)),
            ('agent_run.session_id', lambda log: 'same-session'),
            ('evidence.active', lambda: None),
        ):
            self.enterContext(patch(target, side_effect=value))
        self.enterContext(patch.object(cf, 'HOME', self.home))
        self.enterContext(patch.object(ps, 'REG', str(Path(self.home) / 'profiles.json')))
        ps.remember(self.decision['platform'], 'zh', 'base', FIXED)

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

    def test_page_readers_survive_a_document_without_body(self):
        # 有些網站載完又整份換掉,那一瞬間 body 是空的(2026-10-06 LinkedIn):讀頁不能整支壞掉
        for fn in (apply_tab.PROFILE_FN,):
            script = """
global.location = {href: 'https://jobs.example.test/1'};
global.NodeFilter = {SHOW_TEXT: 4};
global.getComputedStyle = () => ({display: 'block', visibility: 'visible'});
global.document = {title: 't', readyState: 'interactive', body: null, documentElement: {innerText: '職缺標題'},
  links: [], querySelector: () => null, querySelectorAll: () => [], getElementById: () => null,
  createTreeWalker: () => ({nextNode: () => null})};
const r = (READER)();
if (!JSON.stringify(r).includes('職缺標題')) throw new Error('沒讀到文件裡的字');
""".replace('READER', fn)
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

    def test_concurrent_sync_write_cannot_erase_a_profile_name(self):
        from concurrent.futures import ThreadPoolExecutor
        import fcntl
        import threading
        captured = ps.capture_source(str(self.source))
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
            writer = pool.submit(ps.remember_sync, self.decision['platform'], 'zh', 'base',
                                 captured['text'], [], [], 'current')
            try:
                self.assertTrue(entered.wait(5))
                with open(ps.REG + '.lock', 'w') as lock:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                human = pool.submit(ps.remember_name, self.decision['platform'], 'zh', 'base', '平台名稱')
            finally:
                release.set()
            writer.result(timeout=5)
            human.result(timeout=5)
        self.assertEqual(ps.where(self.decision['platform'], 'zh', 'base')['name'], '平台名稱')
        self.assertEqual(ps.where(self.decision['platform'], 'zh', 'base')['sync_baseline']['source'], captured['text'])

    def test_broken_registry_is_not_silently_overwritten(self):
        Path(ps.REG).write_text('{broken')
        with self.assertRaises(ValueError):
            ps.remember(self.decision['platform'], 'zh', 'base', FIXED)
        self.assertEqual(Path(ps.REG).read_text(), '{broken')

    def test_text_date_meaning_is_judged_but_calendar_and_source_are_checked(self):
        from numbered import Numbered
        numbered = Numbered()
        numbered.add('刊登日期：2026年九月七日', 'date', URL)
        sheet = {'material_id': numbered.material_id, 'dates': [{'url': URL, 'posted_at': '2026-09-07', 'source': [1]}]}
        truth = lambda: gate.Truth(handed={URL}, fetched_on={URL: '2026-10-02'})
        verdict = gate.inspect('posted_at', sheet, truth(), numbered)
        self.assertFalse(verdict.problems)
        self.assertEqual(verdict.rows['dates'][0].judged['posted_at'], '2026-09-07')
        self.assertEqual(verdict.rows['dates'][0].facts['source'], '刊登日期：2026年九月七日')
        for field, value in [('posted_at', '2026-02-31'), ('source', '不在原文的日期'), ('source', [2])]:
            broken = copy.deepcopy(sheet)
            broken['dates'][0][field] = value
            self.assertTrue(gate.inspect('posted_at', broken, truth(), numbered).problems)

    def test_summary_dates_or_salary_need_a_jd_paragraph_number(self):
        # 單位換算、跳著摘都交給 agent;程式只要它引這一張 JD 原文那幾段的編號(編號本身由安檢門的 pick 核)
        import gate_flows
        truth = SimpleNamespace(key='J1', read={('J1', 'jd')})
        said = {'salary': '年薪 100,000–120,000 元'}
        self.assertIsNone(gate_flows._card_summary(dict(said, quote=[2]), {}, truth))
        self.assertTrue(gate_flows._card_summary(dict(said, quote=[]), {}, truth))
        self.assertIs(gate_flows._card_summary(dict(said, quote=[]), {}, SimpleNamespace(key='J1', read=set())),
                      gate_flows.UNUSED)

    def test_placeholder_words_in_a_summary_are_not_dates_or_salary(self):
        # agent 沒照指示留空、寫了「查無」「未公開」這類字:跟沒寫一樣,不要它附原文引用;判斷收職稱、存摘要用同一支
        import gate_flows
        import research
        truth = SimpleNamespace(key='J1', read={('J1', 'jd')})
        for word in ('無', '未公開', '查無', '未提供', '-', 'N/A', '未明載(JD 沒寫)'):
            with self.subTest(word=word):
                self.assertIsNone(gate_flows._card_summary({'deadline': word, 'salary': word, 'posted': word}, {}, truth))
        entry = research.job_entry({'url': URL, 'title': 'Engineer', 'company': 'Co'},
                                   {'cat': '其他', 'fit': 3, 'why': '', 'card': {'salary': '未公開', 'loc': '遠端'}}, {})
        self.assertEqual((entry['sum']['salary'], entry['sum']['deadline'], entry['sum']['loc']), ('', '', '遠端'))

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
            run._unsure_while_filling(URL, 'synthetic-board', {'clicked': True})
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
        """看板「不對,復原」送的指令(記結果 + 那一則回音標成他說不對);snack 的復原把復原單送回去,整張回到按之前。"""
        import board_server as bs
        for prior in (None, True):
            receipt = {'id': 'receipt'}
            if prior is not None:
                receipt['effect_rejected'] = prior
            card = {'app': 'sent', 'ds': 'sent', 'sent_by': 'manual', 'oc': 'rej', 'oc_at': {'rej': '2026-10-01'},
                    'oc_auto': {'from': '', 'by': 'receipt', 's': 'rej'}, 'replies': {'items': [receipt]}}
            d = {'fb': {URL: card}, 'data': {'jobs': [{'id': URL, 'target': 'x'}]}}
            before = copy.deepcopy(d['fb'])
            veto = copy.deepcopy(card['replies'])
            veto['items'][0]['effect_rejected'] = True
            rejected, undo, _ = bs.apply_cmds(d, [{'u': URL, 'ev': 'outcome', 'data': {'s': ''}, 'set': {'replies': veto}}])
            self.assertEqual(rejected, [])
            m = d['fb'][URL]
            self.assertEqual((m.get('oc'), m.get('oc_auto'), m['replies']['items'][0]['effect_rejected']), (None, None, True))
            self.assertEqual(bs.apply_cmds(d, undo[0])[0], [])
            self.assertEqual(d['fb'], before)

if __name__ == '__main__':
    unittest.main()
