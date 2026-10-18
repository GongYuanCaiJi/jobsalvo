"""把錄下的頁面轉成 R0 段落並重播；舊紀錄尚未存 readingParts。"""
import copy
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402
import fake_door  # noqa: E402
import profile_sync as ps  # noqa: E402
import sync_profiles as sp  # noqa: E402


ACCEPTANCE = 'JOBSALVO_403B_ACCEPTANCE_EVIDENCE'
SAME_PAGE = 'JOBSALVO_403B_SAME_PAGE_EVIDENCE'
REOPEN = 'JOBSALVO_104_REOPEN_EVIDENCE'
LATEST_104 = 'JOBSALVO_104_LATEST_EVIDENCE'
DRIFT_1111 = 'JOBSALVO_1111_DRIFT_EVIDENCE'


def evidence_root(var):
    """錄下的證據夾只在錄的那台機器上:環境變數沒設就跳過;設了卻找不到那個資料夾是設定錯了,照失敗報。"""
    root = os.environ.get(var)
    if not root:
        raise unittest.SkipTest(f'沒有回放證據:{var} 未設定')
    if not os.path.isdir(root):
        raise AssertionError(f'{var} 指到的證據夾不存在:{root}')
    return Path(root)


def recorded(page):
    """舊快照保有完整 reading 或 DOM 摘要；按當時閱讀順序還原可選段落。"""
    result = copy.deepcopy(page)
    if page.get('reading'):
        parts = re.findall(r'⟦網址 [^⟧]*⟧|\S+', page['reading'])
    else:
        parts = [line.strip() for line in (page.get('text') or '').splitlines() if line.strip()]
        for field in page.get('fields') or []:
            value = field.get('shown') or field.get('value')
            if value:
                parts.append(str(value))
            if field.get('checked') is not None:
                parts.append(f'⟦欄位 {"已勾選" if field["checked"] else "未勾選"}⟧')
        for anchor in page.get('anchors') or []:
            if anchor.get('text'):
                parts.append(anchor['text'])
            if anchor.get('href'):
                parts.append(f'⟦網址 {anchor["href"]}⟧')
        parts.extend(f'⟦網址 {url}⟧' for url in page.get('media') or [])
    result['readingParts'] = parts
    return result


def folder(var, prefix):
    root = evidence_root(var)
    matches = sorted(path for path in root.glob(prefix + '*') if path.is_dir())
    if len(matches) != 1:
        raise AssertionError(f'證據輪數應為一：{root}/{prefix}；找到 {len(matches)}')
    return matches[0]


def snapshot(var, prefix, number):
    path = folder(var, prefix) / f'page-{number}.json'
    with open(path, encoding='utf-8') as stream:
        return recorded(json.load(stream))


class EvidenceRoot(unittest.TestCase):
    def test_an_unset_variable_skips_and_a_path_that_is_not_there_is_a_setup_error(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ACCEPTANCE, None)
            with self.assertRaises(unittest.SkipTest):
                evidence_root(ACCEPTANCE)
        with mock.patch.dict(os.environ, {ACCEPTANCE: '/nonexistent/jobsalvo-evidence'}):
            try:
                with self.assertRaisesRegex(AssertionError, ACCEPTANCE):
                    evidence_root(ACCEPTANCE)
            except unittest.SkipTest as skipped:
                self.fail(f'設了但不存在的證據夾被當成沒設定而跳過:{skipped}')


class RecordedDoor(fake_door.FakeDoor):
    def __init__(self, pages, reload_page=None):
        super().__init__(agent_id='fake')
        self.pages = iter(pages)
        self.last = None
        self.reload_page = reload_page

    def read_profile_page(self, tab_id):
        self._can('read_profile_page', tab_id)
        self.last = next(self.pages, self.last)
        return self.last

    def reload_profile_page(self, tab_id, url=None):
        self._can('reload_profile_page', tab_id, url)
        return {'url': url}


class EvidenceReplay(unittest.TestCase):
    def setUp(self):
        _env.use_home(self, resume={'base': 'resume', 'langs': ['zh'], 'resumes': [
            {'id': 'one', 'name': '通用版', 'files': {'zh': 'resume/one.md'}, 'enabled': True}],
            'attachments': []})
        self.enterContext(mock.patch.object(ps, 'REG', os.path.join(self.home, 'profiles.json')))
        self.board = _env.make_board(os.path.join(self.tmp, 'board.html'))

    def run_pair(self, before, after, ids=(), *, source='# 作品\n- 新影片\n', restored=None,
                 control_before=None, control_after=None):
        with open(os.path.join(self.home, 'resume', 'one.md'), 'w', encoding='utf-8') as out:
            out.write(source)
        ps.remember('replay.test', 'zh', 'one', before['url'])
        ps.remember_sync('replay.test', 'zh', 'one', '# 作品\n- 舊影片\n',
                         [], 'old-fingerprint')
        pages = [before, after]
        if control_before is not None:
            pages.extend([control_before, control_after])
        elif restored is not None:
            pages.append(restored)
        door = RecordedDoor(pages)
        steps = []

        def agent(prompt, log, _home, _board, **kwargs):
            step = Path(kwargs['output_last_message']).stem
            steps.append(step)
            with open(log, 'w', encoding='utf-8') as out:
                out.write(json.dumps({'type': 'thread.started', 'thread_id': 'same-agent'}) + '\n')
            shown = re.search(r'"material_id"\s*:\s*"([0-9a-f]+)"', prompt)
            with open(kwargs['output_last_message'], 'w', encoding='utf-8') as out:
                json.dump({'tab_id': '1:p1', 'ids': list(ids) if step.startswith('edit-') else [],
                           'material_id': shown.group(1) if shown else None, 'handoff': False, 'reason': ''}, out)
            return SimpleNamespace(ok=True, message=lambda: '')

        with fake_door.installed(door), mock.patch.object(sp.apply_run, '_run_agent', side_effect=agent):
            result = sp.sync_slot('replay.test', 'zh', 'one', ps.where('replay.test', 'zh', 'one'),
                                  self.board, verify_only=True)
        return result, door, steps

    def test_preview_then_editor_from_104_redoes_the_item_on_the_editor(self):
        before = snapshot(SAME_PAGE, '20261008-212540-894686-sync_profiles-104_', 1)
        after = snapshot(SAME_PAGE, '20261008-212540-894686-sync_profiles-104_', 2)
        _result, door, steps = self.run_pair(before, after)
        self.assertEqual(steps[:3], ['open-0', 'edit-0', 'open-1'])     # R0 讀在預覽頁:到編輯頁重讀 R0 重做一次
        self.assertEqual([call[0] for call in door.calls].count('reload_profile_page') >= 1, True)

    def test_linkedin_overlay_then_summary_redoes_the_item_once(self):
        before = snapshot(SAME_PAGE, '20261008-212540-894592-sync_profiles-linkedin_', 1)
        after = snapshot(SAME_PAGE, '20261008-212540-894592-sync_profiles-linkedin_', 2)
        _result, _, steps = self.run_pair(before, after)
        self.assertEqual(steps[:3], ['open-0', 'edit-0', 'open-1'])
        self.assertNotIn('open-2', steps)                                  # 只重做一次

    def test_old_104_reopen_case_finishes_at_equal_r1_without_waiting_for_collapsed_r2(self):
        before = snapshot(REOPEN, '20261013-135931-', 1)
        after = snapshot(REOPEN, '20261013-135931-', 2)
        collapsed = snapshot(REOPEN, '20261013-135931-', 3)
        self.assertEqual(before['readingParts'], after['readingParts'])
        self.assertNotEqual(after['readingParts'], collapsed['readingParts'])
        result, door, steps = self.run_pair(before, after)
        self.assertEqual(result, (True, []))
        self.assertEqual(steps, ['open-0', 'edit-0'])
        self.assertEqual([call[0] for call in door.calls].count('read_profile_page'), 2)

    def test_latest_104_uses_current_page_parts_instead_of_earlier_copied_words(self):
        before = snapshot(LATEST_104, '20261013-142928-', 3)
        self.assertTrue(before['readingParts'])
        self.assertNotIn('Github', before['readingParts'])
        result, door, steps = self.run_pair(before, before)
        self.assertEqual(result, (True, []))
        self.assertEqual(steps, ['open-0', 'edit-0'])
        self.assertFalse(any(call[0] == 'reload_profile_page' for call in door.calls))

    def test_acceptance_104_equal_edit_page_needs_only_r0_and_r1(self):
        before = snapshot(ACCEPTANCE, '20261008-192255-735870-sync_profiles-104_', 1)
        result, door, steps = self.run_pair(before, before)
        self.assertEqual(result, (True, []))
        self.assertEqual(steps, ['open-0', 'edit-0'])
        self.assertEqual([call[0] for call in door.calls].count('read_profile_page'), 2)

    def test_1111_plain_url_text_is_a_pending_platform_wording(self):
        edited = snapshot(ACCEPTANCE, '20261008-203659-595164-sync_profiles-www_1111_', 7)
        link = 'https://example.invalid/target'
        self.assertFalse(sp._wording_matches({'new': '觀看', 'links': [link]}, edited['readingParts']))

    def test_1111_other_editor_moves_existing_text_without_damage(self):
        before = snapshot(SAME_PAGE, '20261008-213314-576272-sync_profiles-www_1111_', 1)
        after = snapshot(SAME_PAGE, '20261008-213314-576272-sync_profiles-www_1111_', 2)
        self.assertEqual(before['url'], after['url'])
        self.assertEqual(sp._content_problems(before, after, []), [])

    def test_changing_sidebar_fixture_keeps_selected_profile_fields(self):
        with open(Path(__file__).parent / 'fixtures/sync-403-changing-sidebar.json', encoding='utf-8') as stream:
            pages = json.load(stream)
        before, after = recorded(pages['before']), recorded(pages['edited'])
        ids = [index for index, part in enumerate(before['readingParts'], 1)
               if part in ('Old Candidate', 'old@example.invalid', '1111111111')]
        self.assertEqual(len(ids), 3)
        self.assertEqual(sp._content_problems(before, after, ids), [])
        restored = copy.deepcopy(before)
        restored['readingParts'] = [part.replace('今日推薦職缺批次 2', '今日推薦職缺批次 5')
                                    for part in restored['readingParts']]
        self.assertEqual(sp._readback_problems(before, after, restored, ids, saved=False), [])

    def test_all_recorded_rounds_keep_their_first_r0_r1_page_identity_and_diff(self):
        # 每一輪的 before-0/after-0 都由 events 指向錄下的頁面；舊重抄頁不充當 R0。
        cases = [
            (ACCEPTANCE, '20261008-192255-732485-', True, 1, 1),
            (ACCEPTANCE, '20261008-192255-735870-', True, 3, 3),
            (ACCEPTANCE, '20261008-192255-735908-', False, 26, 41),
            (ACCEPTANCE, '20261008-203659-595164-', True, 3, 1),
            (ACCEPTANCE, '20261008-203659-595255-', False, 30, 45),
            (ACCEPTANCE, '20261008-203659-595328-', True, 3, 3),
            (SAME_PAGE, '20261008-212540-894592-', False, 22, 37),
            (SAME_PAGE, '20261008-212540-894686-', False, 35, 198),
            (SAME_PAGE, '20261008-212540-894718-', True, 0, 0),
            (SAME_PAGE, '20261008-213314-576272-', True, 0, 2),
            (REOPEN, '20261013-135931-', True, 0, 0),
            (LATEST_104, '20261013-142928-', True, 0, 0),
        ]
        for var, prefix, same_page, removed, added in cases:
            with self.subTest(round=prefix):
                path = folder(var, prefix)
                pages = {}
                for line in (path / 'events.jsonl').read_text().splitlines():
                    event = json.loads(line)
                    if event.get('kind') == 'page' and event.get('why') in ('before-0', 'after-0'):
                        pages[event['why']] = recorded(json.loads((path / event['file']).read_text()))
                self.assertEqual(set(pages), {'before-0', 'after-0'})
                before, after = pages['before-0'], pages['after-0']
                self.assertEqual(before['url'] == after['url'], same_page)
                self.assertEqual(len(Counter(before['readingParts']) - Counter(after['readingParts'])), removed)
                self.assertEqual(len(Counter(after['readingParts']) - Counter(before['readingParts'])), added)

    def test_1111_survey_link_closing_during_edit_is_listed_not_blocked(self):
        round_dir = folder(DRIFT_1111, '20261013-180032-')
        events = [json.loads(line) for line in (round_dir / 'events.jsonl').read_text().splitlines()]
        pages = {event['why']: json.loads((round_dir / event['file']).read_text())
                 for event in events if event.get('kind') == 'page'}
        before, after, reopened = (pages[key] for key in ('before-0', 'after-0', 'discard-0'))
        ids = [i for i, part in enumerate(before['readingParts'], 1) if part.startswith('【個人名片】')]
        result, _door, steps = self.run_pair(before, after, ids, source='# 個人名片\n- 更新\n',
                                             restored=reopened)
        self.assertEqual(result, (True, []))
        self.assertNotIn('save-0', steps)


if __name__ == '__main__':
    unittest.main()
