import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: F401 — 測試先隔離資料與瀏覽器設定，再匯入工具。
import config as cf
import gate
import profile_registry  # noqa: E402
import profile_sync as ps
import sync_seed
import sync_material
from chrome_door import PROFILE_FN

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
        self.door = SimpleNamespace(agent_id='original-agent', runtime='codex')
        for target, value in (
            ('profile_sync.decided', lambda *args: dict(self.decision)),
            ('config.master', lambda *args: str(self.source)),
            ('agent_run.session_id', lambda log: 'same-session'),
            ('evidence.active', lambda: None),
        ):
            self.enterContext(patch(target, side_effect=value))
        self.enterContext(patch.object(cf, 'HOME', self.home))
        self.enterContext(patch.object(profile_registry, 'path', return_value=str(Path(self.home) / 'profiles.json')))
        ps.remember(self.decision['platform'], 'zh', 'base', FIXED)

    def test_shared_native_reader_keeps_field_context_and_selection(self):
        script = """
const assert = require('node:assert/strict');
const group = {getAttribute: () => null, querySelector: () => ({innerText: '過去經歷'})};
const field = (type, value, label, files = []) => ({type, value, files, tagName: 'INPUT', checked: false,
  getAttribute: key => key === 'aria-label' ? label : null,
  closest: () => group, getClientRects: () => [{}]});
const controls = [field('number', '0', '年資'), field('email', 'user@example.test', 'Email'),
                  field('checkbox', 'on', '可遠端'),
                  field('file', String.raw`C:\\fakepath\\report.pdf`, '附件', [{name: 'report.pdf'}])];
global.getComputedStyle = () => ({display: 'block', visibility: 'visible'});global.NodeFilter = {SHOW_TEXT: 4};
global.location = {href: 'https://platform.example.test/profile/fixed'};
global.document = {title: '履歷', readyState: 'complete', body: {innerText: '過去經歷'}, links: [],
  querySelector: () => null, getElementById: () => null, createTreeWalker: () => ({nextNode: () => null}),
  querySelectorAll: selector => selector.startsWith('input,') ? controls : []};
const page = (READER)();
assert.equal(page.fields.length, 4);
assert.equal(page.fields[0].value, '0');
assert.equal(page.fields[0].section, '過去經歷');
assert.equal(page.fields[1].value, 'user@example.test');
assert.equal(page.fields[2].checked, false);
assert.equal(page.fields[3].value, 'report.pdf');
let selected = 'true';
controls[0].getAttribute = key => key === 'role' ? 'option' : key === 'aria-selected' ? selected : null;
const first = (READER)();
selected = 'false';
const second = (READER)();
assert.equal(first.text, second.text);
assert.notDeepEqual(first.fields, second.fields);
""".replace('READER', PROFILE_FN)
        result = subprocess.run(['node', '-'], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_page_readers_survive_a_document_without_body(self):
        # 有些網站載完又整份換掉,那一瞬間 body 是空的(2026-10-06 LinkedIn):讀頁不能整支壞掉
        for fn in (PROFILE_FN,):
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
        with patch('pdf_text.extract', side_effect=extract):
            captured = sync_material.capture_source(str(pdf))
        self.assertEqual(captured, {'sha256': hashlib.sha256(original).hexdigest(), 'text': original.decode()})

    def test_concurrent_sync_write_cannot_erase_a_profile_name(self):
        from concurrent.futures import ThreadPoolExecutor
        import fcntl
        import threading
        captured = sync_material.capture_source(str(self.source))
        entered, release = threading.Event(), threading.Event()
        original = ps._load_registry   # 寫入者讀登記表走這個(registry 是只讀的快取)
        def paused_read():
            reg = original()
            if not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('測試交錯等待逾時')
            return reg
        with patch.object(ps, '_load_registry', side_effect=paused_read), ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(sync_seed.seed_sync_baseline, self.decision['platform'], 'zh', 'base',
                                 captured['text'], [], 'current')
            try:
                self.assertTrue(entered.wait(5))
                with open(profile_registry.path() + '.lock', 'w') as lock:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                human = pool.submit(ps.remember_name, self.decision['platform'], 'zh', 'base', '平台名稱')
            finally:
                release.set()
            writer.result(timeout=5)
            human.result(timeout=5)
        self.assertEqual(ps.where(self.decision['platform'], 'zh', 'base')['name'], '平台名稱')
        version = ps.where(self.decision['platform'], 'zh', 'base')['sync_versions'][-1]
        expected_text = {f'base: {where}': text for where, text, *_rest in sync_material.md_sections(captured['text'])}
        self.assertEqual({key: value for key, value in version['target_text'].items()
                          if key.startswith('base:')}, expected_text)

    def test_broken_registry_is_not_silently_overwritten(self):
        Path(profile_registry.path()).write_text('{broken')
        with self.assertRaises(ValueError):
            ps.remember(self.decision['platform'], 'zh', 'base', FIXED)
        self.assertEqual(Path(profile_registry.path()).read_text(), '{broken')


    def test_category_regex_reuses_the_existing_javascript_compatibility_check(self):
        for expression, accepted in [('(?<role>Engineer)', True), ('(?P<role>Engineer)', False)]:
            sheet = {'categories': [{'name': '工程', 'match': expression}]}
            self.assertEqual(not gate.inspect('suggest_cats', sheet, gate.Truth()).problems, accepted)

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
