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
            ('profile_sync.where', lambda *args: {'read': FIXED, 'edit': FIXED}),
            ('profile_sync.decided', lambda *args: dict(self.decision)),
            ('profile_sync.delivery_for', lambda *args: dict(self.delivery)),
            ('profile_sync.attachment_fingerprint', lambda *args: self.fingerprint),
            ('config.master', lambda *args: str(self.source)),
            ('agent_run.session_id', lambda log: 'same-session'),
            ('evidence.active', lambda: None),
        ):
            self.enterContext(patch(target, side_effect=value))
        self.enterContext(patch.object(cf, 'HOME', self.home))

    def judge(self, prompt, log, home, board, **kwargs):
        self.calls.append((prompt, kwargs))
        (self.out / 'profile-review.json').write_text(json.dumps({
            'status': self.status, 'reason': '依本輪原稿與頁面判讀',
            'quotes': {url: page['text'] for url, page in self.pages.items()},
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
            self.assertTrue(self.review())

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
        self.pages[CUSTOM] = {'url': CUSTOM, 'text': '這張卡已接受的客製內容'}
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

    def test_judgments_are_not_cached_and_every_custom_page_needs_a_quote(self):
        self.assertEqual(self.review(), [])
        self.assertEqual(self.review(), [])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.review(lambda *a, **kw: SimpleNamespace(ok=True, message=lambda: '')))
        verdict = gate.inspect('profile_review', {
            'status': 'complete', 'reason': 'complete', 'quotes': {FIXED: self.pages[FIXED]['text']},
        }, gate.Truth(given={FIXED: self.pages[FIXED]['text'], CUSTOM: '客製版原文'}))
        self.assertTrue(verdict.problems)

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
