"""平台履歷同步的 R0/R1/R2 公開流程。"""
import glob
import json
import os
import re
import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402
import fake_door  # noqa: E402
import board_doc as bd  # noqa: E402
import evidence  # noqa: E402
import profile_sync as ps  # noqa: E402
import sync_profiles as sp  # noqa: E402


URL = 'https://example.test/profile/edit'


def page(*parts, document='doc-1', url=URL, fields=()):
    return {'url': url, 'documentKey': document, 'readyState': 'complete',
            'readingParts': list(parts), 'reading': ' '.join(parts),
            'fields': list(fields), 'text': ' '.join(parts), 'links': []}


class Pages(fake_door.FakeDoor):
    def __init__(self, reads, reloads=(), shown=None):
        super().__init__(agent_id='fake')
        self.reads = iter(reads)
        self.reloads = iter(reloads)
        self.last = None
        self.shown = shown

    def read_shown_page(self, url):
        self.calls.append(('read_shown_page', url))
        return self.shown

    def read_profile_page(self, tab_id):
        self._can('read_profile_page', tab_id)
        self.last = next(self.reads, self.last)
        return self.last

    def reload_profile_page(self, tab_id, url=None):
        self._can('reload_profile_page', tab_id, url)
        self.last = next(self.reloads, self.last)
        return self.last

    def follow_page(self, tab_id):
        self._can('follow_page', tab_id)
        self.workspace['page'] = tab_id.partition(':')[2]
        return tab_id

    def shot_clip(self, tab_id, out, clip):
        self._can('shot_clip', tab_id, clip)
        with open(out, 'wb') as image:
            image.write(blank_png(int(clip['width']), int(clip['height'])))
        return out


def blank_png(width, height):
    """與截圖範圍同大小的空白 PNG，讓裁切照真的流程跑。"""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = b''.join(b'\x00' + b'\xff' * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class ParagraphIds(unittest.TestCase):
    def setUp(self):
        _env.use_home(self, resume={'base': 'resume', 'langs': ['zh'], 'resumes': [
            {'id': 'one', 'name': '通用版', 'files': {'zh': 'resume/one.md'}, 'enabled': True}],
            'attachments': []})
        self.enterContext(mock.patch.object(ps, 'REG', os.path.join(self.home, 'profiles.json')))
        self.board = _env.make_board(os.path.join(self.tmp, 'board.html'))
        ps.remember('example.test', 'zh', 'one', URL)

    def change_records(self):
        paths = glob.glob(os.path.join(evidence.flow_dir('sync_profiles', self.board), '*', 'events.jsonl'))
        self.assertTrue(paths)
        with open(max(paths), encoding='utf-8') as source:
            return [row for row in (json.loads(line) for line in source) if row.get('kind') == 'change']

    def run_flow(self, source, before, after, ids, *, restored=None, verify_only=True,
                 old_source='# 作品\n- 舊影片\n', read_pages=None, on_step=None,
                 baseline_answers=(), handoff_step=None, edit_tab='1:p1', shown=None, translation=None,
                 material=None):
        """material(step, 每一步指示印的交付代號) → 這一步交回的代號;沒給就抄這一步自己指示上的。"""
        with open(os.path.join(self.home, 'resume', 'one.md'), 'w', encoding='utf-8') as out:
            out.write(source)
        ps.remember_sync('example.test', 'zh', 'one', old_source, list(baseline_answers), 'old-fingerprint')
        door = Pages(read_pages if read_pages is not None else [before, after, restored],
                     [restored] if restored else [], shown=shown)
        prompts, printed = [], {}

        def agent(prompt, log, _home, _board, **kwargs):
            step = os.path.basename(kwargs['output_last_message']).removesuffix('.json')
            prompts.append((step, prompt))
            shown = re.search(r'"material_id"\s*:\s*"([0-9a-f]+)"', prompt)
            if shown:
                printed[step] = shown.group(1)
            if on_step:
                on_step(step)
            with open(log, 'w', encoding='utf-8') as out:
                out.write(json.dumps({'type': 'thread.started', 'thread_id': 'same-agent'}) + '\n')
            if step == 'translate':
                with open(kwargs['output_last_message'], 'w', encoding='utf-8') as out:
                    json.dump({'zh': translation or {}}, out, ensure_ascii=False)
                return SimpleNamespace(ok=True, message=lambda: '')
            with open(kwargs['output_last_message'], 'w', encoding='utf-8') as out:
                selected = (ids.get(int(step.split('-')[1]), []) if isinstance(ids, dict) else ids) if step.startswith(('edit-', 'select-')) else []
                reply = {'tab_id': edit_tab if step.startswith('edit-') else '1:p1', 'ids': selected,
                         'handoff': step == handoff_step, 'reason': '需要本人驗證' if step == handoff_step else ''}
                said = material(step, printed) if material else printed.get(step)
                if said is not None:
                    reply['material_id'] = said
                json.dump(reply, out)
            return SimpleNamespace(ok=True, message=lambda: '')

        with fake_door.installed(door), mock.patch.object(sp.apply_run, '_run_agent', side_effect=agent):
            result = sp.sync_slot('example.test', 'zh', 'one', ps.where('example.test', 'zh', 'one'),
                                  self.board, verify_only=verify_only)
        return result, door, prompts

    def test_agent_selects_from_program_read_and_verify_restores_without_save(self):
        before = page('作品', '舊影片', '其他內容')
        after = page('作品', '新影片', '其他內容')
        result, door, prompts = self.run_flow('# 作品\n- 新影片\n', before, after, [2], restored=before)
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0', 'restored-reopen-0'])
        self.assertIn('2. 舊影片', prompts[1][1])
        self.assertEqual([call[0] for call in door.calls].count('read_profile_page'), 3)
        self.assertEqual([call[0] for call in door.calls].count('reload_profile_page'), 1)

    def test_other_editor_closes_without_changing_its_content(self):
        before = page('作品', '舊影片', '其他內容', fields=[{'value': '其他內容'}])
        after = page('作品', '新影片', '其他內容', fields=[])
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2], restored=before)
        self.assertEqual(result, (True, []))

    def test_other_editor_reorders_existing_parts_without_creating_a_substitute(self):
        before = page('作品', '舊影片', '其他內容', '旁欄')
        after = page('旁欄', '作品', '舊影片', '其他內容')
        result, door, prompts = self.run_flow('# 作品\n- 舊影片\n', before, after, [2],
                                              verify_only=False,
                                              old_source='# 作品\n- 更舊影片\n')
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0'])
        self.assertFalse(_env.read_fb(self.board).get('__ans__'))
        self.assertFalse(any(call[0] == 'reload_profile_page' for call in door.calls))

    def test_same_url_in_field_and_link_passes_when_both_positions_are_selected(self):
        old = 'https://video.example/old'
        new = 'https://video.example/new'
        before = page('作品', old, f'⟦網址 {old}⟧')
        after = page('作品', new, f'⟦網址 {new}⟧')
        source = f'# 作品\n- {new}\n'
        result, _, _ = self.run_flow(source, before, after, [2, 3], restored=before)
        self.assertEqual(result, (True, []))

    def run_on_platform(self, source, old_source, before, after, ids):
        """正式同步,假平台:存了檔重新打開是改後,沒存就回到改前。"""
        saved = []

        def platform():
            yield before
            yield after
            yield after if saved else before
        result, _, prompts = self.run_flow(source, before, after, ids, verify_only=False, old_source=old_source,
                                           read_pages=platform(),
                                           on_step=lambda step: saved.append(step) if step.startswith('save-') else None)
        return result, [step for step, _ in prompts]

    def media_case(self, after_media, old='https://video.example/watch?v=old', elsewhere=()):
        # elsewhere:這一處以外(「其他作品」後面)新放上的媒體
        new = 'https://video.example/watch?v=new'
        old_source = f'# 作品\n- 舊影片 [觀看]({old})\n'
        before = dict(page('作品', '舊影片', f'⟦網址 {old}⟧', f'⟦網址 {old}⟧', '其他作品'), links=[old], media=[old])
        after = dict(page('作品', '新影片', *[f'⟦網址 {new}⟧'] * (1 + len(after_media)), '其他作品',
                          *[f'⟦網址 {url}⟧' for url in elsewhere]),
                     links=[new], media=[*after_media, *elsewhere])
        result, steps = self.run_on_platform(f'# 作品\n- 新影片 [觀看]({new})\n', old_source, before, after, [2, 3, 4])
        return result, steps, old, old_source

    def test_replacing_a_link_does_not_authorize_deleting_media_with_the_same_url(self):
        # 1c8571b 的考題:同一網址另外掛在媒體上;換掉連結時把媒體刪了(不是換成新值),存檔前擋下、基準不動
        result, steps, old, old_source = self.media_case([])
        self.assertFalse(result[0])
        self.assertIn(f'原有媒體被移除或改動：{old}', str(result[1]))
        self.assertNotIn('save-0', steps)
        self.assertEqual(ps.where('example.test', 'zh', 'one')['sync_baseline']['source'], old_source)

    def test_new_media_elsewhere_on_the_page_does_not_stand_in_for_media_deleted_here(self):
        # 2f83d02 以為頁面上任何一個新媒體都算換上了;放在這一處以外的新媒體不是這一處的新值
        result, steps, old, _source = self.media_case([], elsewhere=['https://video.example/watch?v=other'])
        self.assertFalse(result[0])
        self.assertIn(f'原有媒體被移除或改動：{old}', str(result[1]))
        self.assertNotIn('save-0', steps)

    def test_a_deleted_media_whose_url_has_an_escaped_space_is_still_caught(self):
        # 2f83d02 以為媒體清單和閱讀段落的網址照存的樣子就比得起來;%20 一邊解開一邊沒解,刪光了也看不到
        result, steps, _old, _source = self.media_case([], old='https://video.example/watch?v=old%20cut')
        self.assertFalse(result[0])
        self.assertIn('原有媒體被移除或改動：https://video.example/watch?v=old cut', str(result[1]))
        self.assertNotIn('save-0', steps)

    def test_media_with_the_same_url_replaced_by_the_new_one_is_saved(self):
        result, steps, _old, _source = self.media_case(['https://video.example/watch?v=new'])
        self.assertEqual(result, (True, []))
        self.assertIn('save-0', steps)

    def test_a_line_inserted_above_untouched_old_text_is_not_this_change(self):
        # 1c8571b 的考題:要把舊內容換成新內容,agent 卻在上面插了一行、舊內容原封不動;存檔前擋下、基準不動
        result, steps = self.run_on_platform('# 作品\n- 新內容\n', '# 作品\n- 舊內容\n',
                                             page('作品', '舊內容', '保留'), page('作品', '插入', '舊內容', '保留'), [2])
        self.assertFalse(result[0])
        self.assertIn('原文沒有換掉', str(result[1]))
        self.assertNotIn('save-0', steps)
        self.assertEqual(ps.where('example.test', 'zh', 'one')['sync_baseline']['source'], '# 作品\n- 舊內容\n')

    def test_old_text_kept_as_the_start_of_the_new_text_with_a_line_added_is_saved(self):
        result, steps = self.run_on_platform('# 作品\n- 專案A 加了說明\n', '# 作品\n- 專案A\n',
                                             page('作品', '專案A', '保留'), page('作品', '專案A', '加了說明', '保留'), [2])
        self.assertEqual(result, (True, []))
        self.assertIn('save-0', steps)

    def test_an_answer_seen_only_elsewhere_on_the_page_is_not_used_by_this_profile(self):
        # 5350388(#407 R7):用在這一份的常用答案只看以前就用的、這一輪改到的、程式核對過的這一處原文;
        # 頁面別處剛好有同一個字、代號剛好是 p 開頭,都不算。不算的答案改了,這一份不落後
        answers = [{'k': 'unrelated', 'q': '興趣', 'v': '爬山', 'at': '2026-10-08', 'zh': ''},
                   {'k': 'phone', 'q': '電話', 'v': '0900', 'at': '2026-10-08', 'zh': ''}]
        bd.set_fb(lambda fb: fb.update(__ans__=answers), live=self.board, by='test')
        result, _ = self.run_on_platform('# 作品\n- 新影片\n', '# 作品\n- 舊影片\n',
                                         page('爬山', '作品', '舊影片'), page('爬山', '作品', '新影片'), [3])
        self.assertEqual(result, (True, []))
        self.assertEqual(ps.where('example.test', 'zh', 'one')['sync_baseline']['used_answers'], [])
        bd.set_fb(lambda fb: fb['__ans__'][0].update(v='游泳'), live=self.board, by='test')
        self.assertFalse(sp.behind('example.test', 'zh', 'one', ps.where('example.test', 'zh', 'one'), self.board))

    def test_an_answer_inside_the_checked_text_and_the_platform_wording_are_used_by_this_profile(self):
        answer = {'k': 'availability', 'q': '可到職時間', 'v': '兩週內', 'at': '2026-10-08', 'zh': ''}
        bd.set_fb(lambda fb: fb.update(__ans__=[answer]), live=self.board, by='test')
        result, _ = self.run_on_platform('# 作品\n- 新影片\n', '# 作品\n- 舊影片\n',
                                         page('作品', '舊影片'), page('作品', '平台寫法', '兩週內'), [2])
        self.assertEqual(result, (True, []))
        substitute, = [a['k'] for a in _env.read_fb(self.board)['__ans__'] if a.get('platform')]
        self.assertEqual(set(ps.where('example.test', 'zh', 'one')['sync_baseline']['used_answers']),
                         {'availability', substitute})

    def test_new_text_inside_an_unselected_paragraph_does_not_count_as_loss(self):
        before = page('作品', '舊影片', '其他內容')
        after = page('作品', '新影片', '其他內容也補充')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                     restored=before)
        self.assertEqual(result, (True, []))

    def test_reload_during_input_redoes_once_and_a_second_reload_stops(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片', document='doc-2')
        again = page('作品', '新影片', document='doc-3')
        result, _, prompts = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                           read_pages=[before, after, before, again])
        self.assertFalse(result[0])
        self.assertIn('重新載入', str(result[1]))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0', 'open-1', 'edit-1'])
        self.assertIn('不要離開或重新載入', dict(prompts)['open-1'])

    def test_input_found_on_another_page_redoes_the_item_there_once(self):
        other = URL + '/contact'
        before = page('作品', '平台短版')
        moved = page('聯絡', '新影片', url=other)
        there = page('聯絡', '新影片', url=other)
        result, door, prompts = self.run_flow('# 作品\n- 新影片\n', before, moved, [2],
                                              read_pages=[before, moved, there, there], verify_only=False)
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0', 'open-1', 'edit-1'])
        self.assertIn(other, dict(prompts)['open-1'])
        self.assertIn(('reload_profile_page', '1:p1', URL), [call[:3] for call in door.calls])
        first, second = self.change_records()
        self.assertEqual(first['platform_result'], '換到輸入的那一頁重做')
        self.assertEqual(second['platform_result'], '平台沒變／跟原稿相同')

    def test_moving_to_another_page_twice_is_an_agent_error(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片', url='https://example.test/profile/preview')
        again = page('作品', '新影片', url='https://example.test/profile/other')
        result, _, prompts = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                           read_pages=[before, after, after, again])
        self.assertFalse(result[0])
        self.assertIn('不是同一頁', str(result[1]))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0', 'open-1', 'edit-1'])

    def test_same_url_in_a_different_tab_is_an_agent_error(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片')
        result, _, prompts = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                           edit_tab='1:p2')
        self.assertFalse(result[0])
        self.assertIn('換了分頁', str(result[1]))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0'])

    def test_equal_source_is_read_but_neither_saved_nor_reloaded(self):
        before = page('作品', '新影片', '其他內容')
        result, door, prompts = self.run_flow('# 作品\n- 新影片\n', before, before, [2],
                                              verify_only=False)
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0'])
        self.assertFalse(any(call[0] == 'reload_profile_page' for call in door.calls))
        self.assertFalse(_env.read_fb(self.board).get('__ans__'))

    def test_verify_only_reads_a_current_profile_without_advancing_baseline(self):
        source = '# 作品\n- 新影片\n'
        before = page('作品', '新影片')
        result, door, prompts = self.run_flow(source, before, before, [2],
                                              old_source=source, verify_only=True)
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0'])
        self.assertFalse(any(call[0] == 'reload_profile_page' for call in door.calls))
        self.assertEqual(ps.where('example.test', 'zh', 'one')['sync_baseline']['source'], source)

    def test_unchanged_platform_wording_is_pending_without_saving(self):
        before = page('作品', '平台短版')
        result, _, prompts = self.run_flow('# 作品\n- 新影片\n', before, before, [2],
                                           verify_only=False)
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0'])
        self.assertEqual(_env.read_fb(self.board)['__ans__'][0]['v'], '平台短版')
        change, = self.change_records()
        self.assertEqual(change['platform_result'], '平台沒變')

    def test_one_profile_is_edited_by_one_flow_at_a_time_and_the_next_sees_the_new_baseline(self):
        import threading
        stale = dict(ps.where('example.test', 'zh', 'one'), sync_baseline={'source': 'old'})
        seen, started = [], threading.Event()

        def first(*args, **kw):
            started.set()
            ps.remember_sync('example.test', 'zh', 'one', 'new', [], 'fp')
            release.wait(5)
            seen.append('first')
            return True, []

        def second(_platform, _lang, _variant, entry, _board, **kw):
            seen.append(entry['sync_baseline']['source'])
            return True, []
        release = threading.Event()
        with mock.patch.object(sp, '_sync_slot', side_effect=first):
            one = threading.Thread(target=sp.sync_slot, args=('example.test', 'zh', 'one', stale, self.board))
            one.start()
            started.wait(5)
        with mock.patch.object(sp, '_sync_slot', side_effect=second):
            two = threading.Thread(target=sp.sync_slot, args=('example.test', 'zh', 'one', stale, self.board))
            two.start()
            two.join(0.3)
            self.assertTrue(two.is_alive())          # 第一個還在編輯這一份:第二個等著
            release.set()
            one.join(5)
            two.join(5)
        self.assertEqual(seen, ['first', 'new'])     # 接手時用登記裡最新的同步基準

    def test_markdown_marks_left_on_the_platform_are_not_the_original_wording(self):
        before = page('作品', '收錄 `v3.1.0`')
        result, _, _ = self.run_flow('# 作品\n- 收錄 v3.1.0\n', before, before, [2], verify_only=False)
        self.assertEqual(result, (True, []))
        change, = self.change_records()
        self.assertEqual(change['verdicts'], ['agent 代填待確認'])

    def test_replacing_a_whole_field_with_one_item_is_stopped_and_rolled_back(self):
        about = page('Trained as a physicist I take a system apart and chase anomalies to their root cause',
                     'Why me I break anomalies into questions that can be tested then design experiments',
                     '舊影片')
        only = page('新影片')
        result, door, prompts = self.run_flow('# 作品\n- 新影片\n', about, only, [1, 2, 3],
                                              restored=about, verify_only=False)
        self.assertFalse(result[0])
        self.assertIn('刪掉了不屬於它的段落', str(result[1]))
        self.assertNotIn('save-0', [step for step, _ in prompts])
        self.assertIn('reload_profile_page', [call[0] for call in door.calls])     # 撤回這次沒存的輸入

    def test_a_rollback_is_confirmed_on_the_shown_page_when_the_reopened_editor_differs(self):
        # 2026-10-14 104 實測:撤回後重新打開的編輯處沒展開專案名稱那一格,平台上其實都在
        about = page('Trained as a physicist I take a system apart and chase anomalies to their root cause',
                     'Why me I break anomalies into questions that can be tested then design experiments',
                     '舊影片')
        only = page('新影片')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', about, only, [1, 2, 3],
                                     restored=page('別的編輯狀態'), verify_only=False, shown=about)
        self.assertFalse(result[0])
        self.assertIn('刪掉了不屬於它的段落', str(result[1]))
        self.assertNotIn('改壞的內容', str(result[1]))
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', about, only, [1, 2, 3],
                                     restored=page('別的編輯狀態'), verify_only=False, shown=page('新影片'))
        self.assertIn('改壞的內容', str(result[1]))           # 對外頁面也沒有原文:照實報

    def test_editing_inside_one_long_paragraph_is_not_an_unrelated_removal(self):
        long_old = '個人名片 GitHub 學歷 淡江大學 物理學系 https://x.test 求職條件 台北市 自傳 碰到說不通的東西我沒辦法放著不管'
        long_new = long_old.replace(' https://x.test', '')
        self.assertEqual(sp._unrelated_removals(page(long_old), page(long_new), [1], 'GitHub 學歷'), [])

    def test_markdown_code_and_bold_marks_are_not_part_of_the_text(self):
        (_item, text, links, body, labels), = ps.md_sections('# 開源\n- [repo](https://x.test/1)（收錄 `v3.1.0`，**併入** `main`）\n')
        self.assertEqual(text, 'repo（收錄 v3.1.0，併入 main）')
        self.assertEqual((links, labels), (['https://x.test/1'], ['repo']))
        self.assertNotIn('`', body)

    def test_platform_splitting_title_and_description_is_still_the_original_wording(self):
        before = page('作品', 'Hooks failure (CWE-807)', 'after the upgrade, every hook failed.')
        result, _, prompts = self.run_flow('# 作品\n- Hooks failure (CWE-807): after the upgrade every hook failed\n',
                                           before, before, [2, 3], verify_only=False)
        self.assertEqual(result, (True, []))
        self.assertNotIn('translate', [step for step, _ in prompts])
        self.assertEqual(_env.read_fb(self.board).get('__ans__') or [], [])
        change, = self.change_records()
        self.assertEqual(change['verdicts'], ['照原文'])

    def test_english_platform_wording_is_remembered_with_its_translation(self):
        before = page('作品', 'Short platform version')
        result, _, prompts = self.run_flow('# 作品\n- 新影片\n', before, before, [2], verify_only=False,
                                           translation={'0': '平台短版'})
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0', 'translate'])
        fb = _env.read_fb(self.board)
        self.assertEqual((fb['__ans__'][0]['v'], fb['__ans__'][0]['zh']), ('Short platform version', '平台短版'))
        self.assertEqual(sp.fr.validate(fb, []), [])

    def test_missing_translation_leaves_no_untranslated_answer(self):
        before = page('作品', 'Short platform version')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, before, [2], verify_only=False)
        self.assertFalse(result[0])
        self.assertIn('常用答案缺翻譯', str(result[1]))
        self.assertEqual(sp.fr.validate(_env.read_fb(self.board), []), [])

    def test_nothing_selected_and_nothing_added_is_listed_not_a_substitute(self):
        other = page('名', '姓')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', other, other, [], verify_only=False)
        self.assertEqual(result, (True, []))
        change, = self.change_records()
        self.assertEqual(change['verdicts'], ['這一頁找不到這一段，沒放上去'])
        self.assertEqual(_env.read_fb(self.board).get('__ans__') or [], [])

    def test_save_requires_new_content_on_one_fresh_read(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片')
        result, door, prompts = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                              restored=after, verify_only=False)
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0', 'save-0', 'saved-reopen-0'])
        self.assertEqual([call[0] for call in door.calls].count('read_profile_page'), 3)
        self.assertEqual(ps.where('example.test', 'zh', 'one')['sync_baseline']['source'],
                         '# 作品\n- 新影片\n')

    def test_a_ticking_line_outside_the_selection_does_not_count_as_this_edit(self):
        before = page('8分鐘', '作品', '舊影片')
        after = page('10分鐘', '作品', '新影片')
        saved = page('12分鐘', '作品', '新影片')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [3],
                                     restored=saved, verify_only=False)
        self.assertEqual(result, (True, []))
        change, = self.change_records()
        self.assertEqual((change['after_text'], change['platform_result']), ('新影片', '已存上平台'))
        self.assertEqual(change['outside_losses'], [])     # 只有數字在跳的計數不是內容消失
        self.assertNotIn('範圍外有內容消失', change['verdicts'])

    def test_save_fails_if_fresh_page_has_only_old_content(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                     restored=before, verify_only=False)
        self.assertFalse(result[0])
        self.assertIn('存檔後沒有這次新增', str(result[1]))
        change, = self.change_records()
        self.assertEqual((change['before_text'], change['after_text']), ('舊影片', '新影片'))
        self.assertEqual(change['platform_result'], '沒做成')
        self.assertEqual(change['step'], '存檔後比對')
        self.assertIn('存檔後沒有這次新增', change['reason'])

    def changes(self):
        """最新一輪的改動紀錄（證據夾可能留著同一個 home 的前幾輪）。"""
        path = max(glob.glob(os.path.join(evidence.flow_dir('sync_profiles', self.board), '*', 'events.jsonl')),
                   key=os.path.getmtime)
        return [row for row in map(json.loads, open(path, encoding='utf-8'))
                if row.get('kind') in ('change', 'change_update')]

    def test_edit_page_draft_is_not_reported_as_saved_when_the_shown_page_is_old(self):
        before = page('作品', '舊影片描述文字一二三')
        after = page('作品', '新影片描述文字一二三')
        result, door, _ = self.run_flow('# 作品\n- 新影片描述文字一二三\n', before, after, [2],
                                        restored=after, shown=page('作品', '舊影片描述文字一二三'))
        self.assertEqual(result, (True, []))
        self.assertIn(('read_shown_page', URL), door.calls)
        self.assertEqual([c['platform_result'] for c in self.changes() if c['kind'] == 'change_update'], ['平台沒變（編輯頁留有網站草稿）'])

    def test_autosave_seen_on_the_shown_page_is_reported_as_saved(self):
        before = page('作品', '舊影片描述文字一二三')
        after = page('作品', '新影片描述文字一二三')
        result, _, _ = self.run_flow('# 作品\n- 新影片描述文字一二三\n', before, after, [2],
                                     restored=after, shown=page('作品', '新影片描述文字一二三'))
        self.assertEqual(result, (True, []))
        self.assertEqual([c['platform_result'] for c in self.changes() if c['kind'] == 'change_update'], ['平台已存（對外頁面看得到）'])
        change, = self.change_records()
        self.assertEqual(change['platform_result'], '平台自己存了')
        self.assertIn('平台自動存檔', change['verdicts'])

    def test_verify_lists_a_reload_that_differs_without_keeping_the_edit(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                     restored=page('作品', '別的內容'))
        self.assertEqual(result, (True, []))
        change, = self.change_records()
        self.assertEqual(change['platform_result'], '只驗收、已撤回')
        self.assertIn('重新載入後與改前不同', change['verdicts'])

    def test_verify_rejects_a_reload_that_keeps_part_of_the_edit(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片', '新連結')
        result, _, _ = self.run_flow('# 作品\n- 新影片 新連結\n', before, after, [2],
                                     restored=page('作品', '新影片'))
        self.assertFalse(result[0])
        self.assertIn('撤回後', str(result[1]))

    def test_verify_fails_on_one_loading_r2_without_polling(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片')
        loading = page('載入中')
        loading['readyState'] = 'interactive'
        result, door, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                        restored=loading)
        self.assertFalse(result[0])
        self.assertIn('讀頁失敗', str(result[1]))
        self.assertEqual([call[0] for call in door.calls].count('read_profile_page'), 3)

    def test_verify_accepts_changed_sidebar_after_reload(self):
        before = page('作品', '舊影片', '推薦 A')
        after = page('作品', '新影片', '推薦 A')
        restored = page('作品', '舊影片', '推薦 B', document='doc-2')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                     restored=restored)
        self.assertEqual(result, (True, []))

    def test_saved_profile_accepts_changed_sidebar_after_reload(self):
        before = page('作品', '舊影片', '推薦 A')
        after = page('作品', '新影片', '推薦 A')
        saved = page('作品', '新影片', '推薦 B', document='doc-2')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                     restored=saved, verify_only=False)
        self.assertEqual(result, (True, []))

    def test_empty_selection_allows_a_new_paragraph(self):
        before = page('作品', '其他內容')
        after = page('作品', '其他內容', '新影片')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [],
                                     restored=before)
        self.assertEqual(result, (True, []))

    def test_verify_reloads_even_when_the_edit_only_removes_selected_text(self):
        before = page('作品', '舊影片')
        after = page('作品')
        result, door, prompts = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                              restored=before)
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0', 'restored-reopen-0'])
        self.assertEqual([call[0] for call in door.calls].count('reload_profile_page'), 1)

    def test_two_selected_duplicates_cover_both_positions(self):
        before = page('作品', '同一句', '同一句')
        after = page('作品', '新影片', '新影片')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2, 3],
                                     restored=before)
        self.assertEqual(result, (True, []))

    def test_plain_text_url_is_pending_when_source_requires_a_link(self):
        old = 'https://video.example/old'
        new = 'https://video.example/new'
        before = page('作品', '舊影片', '觀看', f'⟦網址 {old}⟧')
        after = page('作品', '新影片', '觀看', new)
        source = f'# 作品\n- 新影片 [觀看]({new})\n'
        result, _, _ = self.run_flow(source, before, after, [2, 3, 4],
                                     restored=after, verify_only=False,
                                     old_source=f'# 作品\n- 舊影片 [觀看]({old})\n')
        self.assertEqual(result, (True, []))
        self.assertEqual(_env.read_fb(self.board)['__ans__'][0]['v'], '新影片\n觀看\nhttps://video.example/new')

    def test_a_field_shown_twice_on_the_page_keeps_the_selected_copy_as_this_edit(self):
        # 單一大欄位的平台：編輯框和預覽各顯示一份全文；agent 只選編輯框那份
        before = page('作品', '全文 舊影片', '10/8000', '預覽', '全文 舊影片')
        after = page('作品', '全文 新影片', '10/8000', '預覽', '全文 新影片')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2, 3],
                                     restored=after, verify_only=False)
        self.assertEqual(result, (True, []))
        change, = self.change_records()
        self.assertEqual((change['after_text'], change['platform_result']), ('全文 新影片\n10/8000', '已存上平台'))

    def test_an_item_edited_on_one_line_keeps_its_untouched_lines_in_the_record_and_answer(self):
        before = page('開源', '甲專案 `v1`', '乙專案', '丙專案 `main`')
        after = page('開源', '甲專案 v1', '乙專案', '丙專案 main')
        result, _, _ = self.run_flow('# 開源\n- 甲專案 v1、乙專案、丙專案 main 平台寫法\n', before, after, [2, 3, 4],
                                     restored=after, verify_only=False)
        self.assertEqual(result, (True, []))
        change, = self.change_records()
        self.assertEqual(change['after_text'], '甲專案 v1\n乙專案\n丙專案 main')
        self.assertEqual(_env.read_fb(self.board)['__ans__'][0]['v'], '甲專案 v1\n乙專案\n丙專案 main')

    def test_ids_from_another_page_redo_the_item_on_that_page(self):
        other = URL + '/about'
        before = page('名', 'Someone')
        here = page('作品', '平台短版', url=other)
        result, _, prompts = self.run_flow('# 作品\n- 新影片\n', before, here, {0: [112], 1: [2]},
                                           read_pages=[before, here, here, here], verify_only=False)
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0', 'open-1', 'edit-1'])
        self.assertIn(other, dict(prompts)['open-1'])

    def test_invalid_selected_id_stops_before_save(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片')
        result, _, prompts = self.run_flow('# 作品\n- 新影片\n', before, after, [99])
        self.assertFalse(result[0])
        self.assertIn('段落編號無效', str(result[1]))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0'])

    def test_invalid_id_names_the_id_and_the_range(self):
        before = page('作品', '舊影片')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, page('作品', '新影片'), [99])
        self.assertFalse(result[0])
        self.assertIn('99', str(result[1]))
        self.assertIn('1-2', str(result[1]))

    def test_numbers_written_for_another_spots_r0_are_refused(self):
        """兩處的 R0 都有 2 號、字不同:第二處交回的代號是第一處的、或沒寫,2 號就不能解成第二處的字(#465 R3)。"""
        source, old = '# 作品\n- 新影片\n# 日期\n- 新日期\n', '# 作品\n- 舊影片\n# 日期\n- 舊日期\n'
        r0a, r1a = page('作品', '舊影片', '日期', '舊日期'), page('作品', '新影片', '日期', '舊日期')
        r0b = page('日期', '舊日期', '作品', '新影片', document='doc-2')
        r1b = page('日期', '新日期', '作品', '新影片', document='doc-2')
        reads = [r0a, r1a, r1a, r0b, r1b, r1b]
        result, _, _ = self.run_flow(source, r0a, r1a, {0: [2], 1: [2]}, old_source=old, verify_only=False,
                                     read_pages=list(reads))
        self.assertEqual(result, (True, []))
        for name, stale in (('第一處的代號', lambda seen: seen['edit-0']), ('沒寫代號', lambda seen: None)):
            with self.subTest(name):
                result, _, prompts = self.run_flow(
                    source, r0a, r1a, {0: [2], 1: [2]}, old_source=old, verify_only=False, read_pages=list(reads),
                    material=lambda step, seen, stale=stale: stale(seen) if step == 'edit-1' else seen.get(step))
                self.assertFalse(result[0])
                self.assertEqual([step for step, _ in prompts][-2:], ['open-1', 'edit-1'])
                printed = re.search(r'"material_id"\s*:\s*"([0-9a-f]+)"', dict(prompts)['edit-1']).group(1)
                self.assertIn(printed, str(result[1]))

    def test_selected_paragraphs_can_span_separate_contact_fields(self):
        before = page('個人名片', '小明', 'sample@example.test', '0912345678')
        source = '# 個人名片\n- 小明 sample@example.test 0912345678\n'
        result, _, prompts = self.run_flow(source, before, before, [2, 3, 4],
                                           verify_only=False, old_source='# 個人名片\n')
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts], ['open-0', 'edit-0'])
        self.assertFalse(_env.read_fb(self.board).get('__ans__'))

    def test_restore_missing_an_old_paragraph_is_listed_for_review(self):
        before = page('作品', '舊影片', '其他內容')
        after = page('作品', '新影片', '其他內容')
        restored = page('作品', '其他內容')
        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2], restored=restored)
        self.assertEqual(result, (True, []))
        notes = [json.loads(line) for path in glob.glob(os.path.join(
                     evidence.flow_dir('sync_profiles', self.board), '*', 'events.jsonl'))
                 for line in open(path, encoding='utf-8')]
        self.assertTrue(any(row.get('kind') == 'restored_differs_pending' for row in notes))

    def test_two_source_sections_each_get_a_separate_r0_r1_r2(self):
        source = '# 作品\n- 新影片\n# 日期\n- 新日期\n'
        old = '# 作品\n- 舊影片\n# 日期\n- 舊日期\n'
        r0a = page('作品', '舊影片', '日期', '舊日期')
        r1a = page('作品', '新影片', '日期', '舊日期')
        r0b = page('作品', '新影片', '日期', '舊日期', document='doc-2')
        r1b = page('作品', '新影片', '日期', '新日期', document='doc-2')
        result, door, prompts = self.run_flow(source, r0a, r1a, {0: [2], 1: [4]},
                                              old_source=old, verify_only=False,
                                              read_pages=[r0a, r1a, r1a, r0b, r1b, r1b])
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts],
                         ['open-0', 'edit-0', 'save-0', 'saved-reopen-0',
                          'open-1', 'edit-1', 'save-1', 'saved-reopen-1'])
        self.assertEqual([call[0] for call in door.calls].count('read_profile_page'), 6)

    def test_master_changed_during_sync_does_not_advance_baseline(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片')

        def change_master(step):
            if step == 'save-0':
                with open(os.path.join(self.home, 'resume', 'one.md'), 'a', encoding='utf-8') as out:
                    out.write('# 其他\n- 後來新增\n')

        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                     restored=after, verify_only=False, on_step=change_master)
        self.assertFalse(result[0])
        self.assertIn('原始履歷又改了', str(result[1]))
        self.assertEqual(ps.where('example.test', 'zh', 'one')['sync_baseline']['source'],
                         '# 作品\n- 舊影片\n')

    def test_user_answer_changed_during_sync_does_not_advance_baseline(self):
        answer = {'k': 'shared-availability', 'q': '可到職時間', 'v': '兩週內',
                  'at': '2026-10-08', 'zh': ''}
        bd.set_fb(lambda fb: fb.update(__ans__=[answer]), live=self.board, by='test')
        before = page('作品', '舊影片', '兩週內')
        after = page('作品', '新影片', '兩週內')

        def change_answer(step):
            if step == 'save-0':
                bd.set_fb(lambda fb: fb['__ans__'][0].update(v='一個月內'),
                          live=self.board, by='test')

        result, _, _ = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                     restored=after, verify_only=False,
                                     baseline_answers=[answer], on_step=change_answer)
        self.assertFalse(result[0])
        self.assertIn('常用答案又改了', str(result[1]))
        self.assertEqual(ps.where('example.test', 'zh', 'one')['sync_baseline']['source'],
                         '# 作品\n- 舊影片\n')

    def test_agent_instruction_uses_only_numbered_parts_and_four_report_fields(self):
        before = page('作品', '舊影片')
        _, _, prompts = self.run_flow('# 作品\n- 新影片\n', before, before, [2])
        self.assertIn('R0 段落：\n1. 作品\n2. 舊影片', prompts[1][1])
        self.assertIn('跟原稿相同也要挑', prompts[1][1])
        for _, prompt in prompts:
            self.assertIn('"tab_id"', prompt)
            self.assertIn('"ids"', prompt)
            self.assertIn('"handoff"', prompt)
            self.assertIn('"reason"', prompt)
            self.assertNotIn('"quotes"', prompt)
            self.assertNotIn('"urls"', prompt)

    def test_confirmed_platform_wording_is_the_expected_value(self):
        confirmed = {'k': 'confirmed', 'q': '作品[0]', 'v': '用戶確認短版',
                     'platform': 'example.test', 'profile': 'zh/one', 'at': '2026-10-08'}
        bd.set_fb(lambda fb: fb.update(__ans__=[confirmed]), live=self.board, by='test')
        before = page('作品', '舊影片')
        after = page('作品', '用戶確認短版')
        result, _, prompts = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                           restored=after, verify_only=False,
                                           baseline_answers=[confirmed])
        self.assertEqual(result, (True, []))
        self.assertIn('用戶確認短版', prompts[1][1])
        self.assertEqual(_env.read_fb(self.board)['__ans__'], [confirmed])

    def test_wrong_actual_link_is_pending_even_with_the_right_visible_label(self):
        old = 'https://video.example/old'
        expected = 'https://video.example/new'
        wrong = 'https://video.example/wrong'
        before = page('作品', '舊影片', '觀看', f'⟦網址 {old}⟧')
        after = page('作品', '新影片', '觀看', f'⟦網址 {wrong}⟧')
        result, _, _ = self.run_flow(f'# 作品\n- 新影片 [觀看]({expected})\n',
                                     before, after, [2, 3, 4], restored=after,
                                     verify_only=False,
                                     old_source=f'# 作品\n- 舊影片 [觀看]({old})\n')
        self.assertEqual(result, (True, []))
        answer, = _env.read_fb(self.board)['__ans__']
        self.assertIn(wrong, answer['v'])
        self.assertNotIn(expected, answer['v'])
        self.assertNotIn('⟦網址', answer['v'])

    def test_correct_link_is_recorded_as_original(self):
        old = 'https://video.example/old'
        new = 'https://video.example/new'
        before = page('作品', '舊影片', '觀看', f'⟦網址 {old}⟧')
        after = page('作品', '新影片', '觀看', f'⟦網址 {new}⟧')
        result, _, _ = self.run_flow(f'# 作品\n- 新影片 [觀看]({new})\n',
                                     before, after, [2, 3, 4], restored=after,
                                     verify_only=False,
                                     old_source=f'# 作品\n- 舊影片 [觀看]({old})\n')
        self.assertEqual(result, (True, []))
        self.assertFalse(_env.read_fb(self.board).get('__ans__'))

    def test_login_handoff_keeps_the_page_and_does_not_read_r1(self):
        before = page('作品', '舊影片')
        result, door, prompts = self.run_flow('# 作品\n- 新影片\n', before, before, [2],
                                              handoff_step='open-0')
        self.assertFalse(result[0])
        self.assertIn('本人驗證', str(result[1]))
        self.assertEqual([getattr(problem, 'kind', None) for problem in result[1]], ['login'])
        self.assertEqual([step for step, _ in prompts], ['open-0'])
        self.assertIn(('_hand_off_page',), door.calls)

    def test_a_login_asked_when_reopening_the_editor_is_his_to_log_in(self):
        # 存檔後、撤回後重新打開編輯處時才要登入:一樣是他要登入,不因為包在讀頁失敗那句話裡就變成程式的錯
        for step, verify_only, kinds in (('saved-reopen-0', False, ['login']), ('restored-reopen-0', True, ['login']),
                                         ('discard-reopen-0', False, ['login', 'damage'])):
            with self.subTest(step=step):
                before = page('作品', '舊內容', '保留')
                after = page('作品', '新內容', '保留') if step != 'discard-reopen-0' else page('作品', '插入', '舊內容', '保留')
                result, _, _ = self.run_flow('# 作品\n- 新內容\n', before, after, [2], restored=after,
                                             verify_only=verify_only, handoff_step=step, old_source='# 作品\n- 舊內容\n')
                self.assertFalse(result[0])
                self.assertEqual([kind for kind in (getattr(p, 'kind', None) for p in result[1]) if kind], kinds)

    @unittest.skipUnless(shutil.which('sips'), '改前截圖用 macOS 內建的 sips 裁(產品只支援 Mac);這台沒有')
    def test_formal_sync_keeps_two_shots_for_the_next_card(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片')
        for snapshot in (before, after):
            snapshot['readingRects'] = [{'x': 10, 'y': 20, 'width': 40, 'height': 15},
                                        {'x': 20, 'y': 100, 'width': 80, 'height': 20}]
            snapshot['documentSize'] = {'width': 300, 'height': 400}
        result, door, prompts = self.run_flow('# 作品\n- 新影片\n', before, after, [2],
                                     restored=after, verify_only=False)
        self.assertEqual(result, (True, []))
        self.assertEqual([step for step, _ in prompts][:3], ['open-0', 'edit-0', 'save-0'])
        card = 'https://example.test/job/one'
        visuals = ps.attach_sync_visuals('example.test', 'zh', 'one', card, self.board)
        self.assertEqual(len(visuals), 1)
        self.assertEqual(visuals[0]['platform_result'], '已存上平台')
        self.assertEqual((visuals[0]['before_text'], visuals[0]['after_text']), ('舊影片', '新影片'))
        self.assertEqual([call[0] for call in door.calls].count('shot_clip'), 2)
        paths = [evidence.path(card, visuals[0][phase + '_shot'], self.board) for phase in ('before', 'after')]
        self.assertTrue(all(os.path.isfile(path) for path in paths))
        ps.clear_sync_visuals('example.test', 'zh', 'one', card, self.board)
        self.assertNotIn('sync_visuals', ps.where('example.test', 'zh', 'one'))
        self.assertTrue(all(os.path.exists(path) for path in paths))

    def test_loss_outside_selection_is_listed_for_review_not_blocked(self):
        before = page('作品', '舊影片', '其他內容')
        after = page('作品', '新影片', '其他資訊')
        result, _, prompts = self.run_flow('# 作品\n- 新影片\n', before, after, [2], restored=before)
        self.assertEqual(result, (True, []))
        self.assertFalse(any(step.startswith('save-') for step, _ in prompts))
        notes = [json.loads(line) for path in glob.glob(os.path.join(
                     evidence.flow_dir('sync_profiles', self.board), '*', 'events.jsonl'))
                 for line in open(path, encoding='utf-8')]
        self.assertTrue(any(row.get('kind') == 'outside_loss_pending' and '其他內容' in row.get('parts', [])
                            for row in notes))
        change, = self.change_records()
        self.assertIn('範圍外有內容消失', change['verdicts'])
        self.assertIn('其他內容', change['outside_losses'])

    def test_answer_change_records_the_question_used(self):
        old_answer = {'k': 'availability', 'q': '可到職時間', 'v': '一個月', 'at': '2026-10-08', 'zh': ''}
        new_answer = dict(old_answer, v='兩週')
        bd.set_fb(lambda fb: fb.update(__ans__=[new_answer]), live=self.board, by='test')
        before = page('可到職時間', '一個月')
        after = page('可到職時間', '兩週')
        source = '# 作品\n- 舊影片\n'
        result, _, _ = self.run_flow(source, before, after, [2], restored=before,
                                     old_source=source, baseline_answers=[old_answer])
        self.assertEqual(result, (True, []))
        change, = self.change_records()
        self.assertEqual(change['answer_question'], '可到職時間')
        self.assertEqual((change['before_text'], change['after_text']), ('一個月', '兩週'))

    def test_visual_registry_write_failure_marks_round_failed(self):
        before = page('作品', '舊影片')
        after = page('作品', '新影片')
        with mock.patch.object(ps, 'remember_sync_visuals', side_effect=OSError('登記檔無法寫入')):
            with self.assertRaisesRegex(OSError, '登記檔無法寫入'):
                self.run_flow('# 作品\n- 新影片\n', before, after, [2], restored=before)
        paths = glob.glob(os.path.join(evidence.flow_dir('sync_profiles', self.board), '*', 'events.jsonl'))
        with open(max(paths), encoding='utf-8') as source:
            rows = [json.loads(line) for line in source]
        self.assertEqual([row['kind'] for row in rows].count('change'), 1)
        terminal, = (row for row in rows if row['kind'] == 'sync_result')
        self.assertFalse(terminal['ok'])
        self.assertIn('逐處紀錄未存入', terminal['problems'][0])

    def test_trailing_slash_or_fragment_is_the_same_page(self):
        self.assertTrue(sp._same_page_url('https://x.test/a/edit/', 'https://x.test/a/edit#top'))
        self.assertFalse(sp._same_page_url('https://x.test/a/edit', 'https://x.test/a/preview'))


class ClipBounds(unittest.TestCase):
    def test_clip_uses_document_coordinates_padding_and_page_edges(self):
        snapshot = page('甲', '乙', '丙')
        snapshot['readingRects'] = [
            {'x': 3, 'y': 5, 'width': 20, 'height': 10},
            {'x': 35, 'y': 25, 'width': 20, 'height': 10},
            {'x': 85, 'y': 85, 'width': 20, 'height': 15}]
        snapshot['documentSize'] = {'width': 100, 'height': 100}
        self.assertEqual(sp._clip_for_parts(snapshot, [1, 3]),
                         {'x': 0, 'y': 0, 'width': 100, 'height': 100})
        self.assertEqual(sp._clip_for_parts(snapshot, [2]),
                         {'x': 23, 'y': 13, 'width': 44, 'height': 34})

    def test_missing_position_does_not_guess(self):
        snapshot = page('甲', '乙')
        self.assertIsNone(sp._clip_for_parts(snapshot, [1]))
        snapshot['readingRects'] = [None, None]
        self.assertIsNone(sp._clip_for_parts(snapshot, [1]))

    def test_after_location_follows_selected_paragraph_when_text_repeats(self):
        before = page('相同的新文', '標題', '舊文')
        after = page('相同的新文', '標題', '相同的新文')
        self.assertEqual(sp._after_part_ids(before, after, [3], ['相同的新文']), [3])

    def test_after_location_tracks_shift_from_other_disappearing_text(self):
        before = page('會消失', '標題', '舊文')
        after = page('標題', '新文')
        self.assertEqual(sp._after_part_ids(before, after, [3], ['新文']), [2])


if __name__ == '__main__':
    unittest.main()
