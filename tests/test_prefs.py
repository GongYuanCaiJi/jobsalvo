import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
import prefs  # noqa: E402


class LikeScores(unittest.TestCase):
    def test_markdown_linked_targets_keep_the_legacy_score_order(self):
        jobs = [
            {'id': 'j0',
             'target': 'Data Platform Engineer [source](https://retail.example.invalid/retail) · CloudWorks, Inc.',
             'sum': {'bar': 'cloud network access control data', 'co': 'security network'}},
            {'id': 'j1',
             'target': 'Data Platform Engineer [source](https://cloud.example.invalid/cloud) · Greenfield GmbH',
             'sum': {'bar': 'machine learning data cloud storage', 'co': 'retail crm'}},
            {'id': 'j3',
             'target': 'Retail Sales Manager [source](https://sales.example.invalid/data) · Greenfield GmbH',
             'sum': {'bar': 'customer retention renewals support ticket', 'co': 'support security'}},
        ]
        fb = {'j0': {'s': 'like'}, 'j1': {'s': 'like'}, 'j3': {'s': 'dislike'}}

        self.assertEqual(
            prefs.card.legacy_score_name_parts(jobs[0], prefs.TITLE_KW),
            ('Data Platform Engineer [source](https://retail.example.invalid/retail) · CloudWorks, Inc.',
             'CloudWorks, Inc.'),
        )
        self.assertEqual(prefs.like_scores(fb, jobs), {'j0': 100, 'j1': 50, 'j3': 0})

    def test_card_reason_cleanup_does_not_depend_on_platform_names(self):
        self.assertEqual(prefs.his_words('我做過 Lever 表單串接'), '我做過 Lever 表單串接')


class AgentNoteMerge(unittest.TestCase):
    """整理偏好筆記的 agent 跟找缺同時跑,可以跑很久;這段時間他在看板上改的筆記以現在的為準。"""

    def _note(self, directory):
        note = os.path.join(directory, 'preference-note.md')
        prefs._write_note('不要純業務', '- 喜歡基礎建設｜出處 A\n- 不喜歡外包｜出處 B', note)
        with open(note, encoding='utf-8') as f:
            return note, f.read()                       # 交給 agent 的那一份

    def test_his_edits_during_the_run_are_kept_and_not_blamed_on_the_agent(self):
        with tempfile.TemporaryDirectory(prefix='prefs-merge-') as directory:
            note, base = self._note(directory)
            # agent 跑的期間:他加了一條自訂、刪了一條假設
            prefs.save_note_from_ui('不要純業務\n只要遠端', '- 喜歡基礎建設｜出處 A', path=note)
            candidate = prefs._render_note('不要純業務',
                                           '- 喜歡基礎建設｜出處 A、C\n- 不喜歡外包｜出處 B\n- 新假設｜出處 D')
            changed = prefs.apply_agent_note(candidate, note, base=base)
            custom, agent = prefs.note_sections(note)
            self.assertFalse(changed)                   # 自訂區跟交給它的一樣:agent 沒動,不能回報它改
            self.assertEqual(custom, '不要純業務\n只要遠端')
            self.assertNotIn('不喜歡外包', agent)       # 他刪掉的不回來
            self.assertIn('- 喜歡基礎建設｜出處 A、C', agent)
            self.assertIn('- 新假設｜出處 D', agent)

    def test_agent_rewriting_the_custom_section_is_still_caught_and_undone(self):
        with tempfile.TemporaryDirectory(prefix='prefs-merge-') as directory:
            note, base = self._note(directory)
            candidate = prefs._render_note('要純業務', '- 喜歡基礎建設｜出處 A')
            self.assertTrue(prefs.apply_agent_note(candidate, note, base=base))
            self.assertEqual(prefs.note_sections(note)[0], '不要純業務')


class ReadingTheNoteDoesNotWriteIt(unittest.TestCase):
    def test_reading_leaves_the_file_alone_and_only_ensure_note_completes_it(self):
        """看設定、📝 prompt 預覽是 GET,讀偏好筆記不能順手改他的檔;真的派 agent 之前 ensure_note 才補齊。"""
        with tempfile.TemporaryDirectory(prefix='prefs-read-') as directory:
            note = os.path.join(directory, 'note.md')
            self.assertEqual(prefs.note_sections(note), ('', ''))
            self.assertFalse(os.path.exists(note))
            with open(note, 'w', encoding='utf-8') as f:
                f.write('只寫了一行,沒有區塊')
            self.assertIn(prefs.NOTE_AGENT, prefs.read_note(note))
            with open(note, encoding='utf-8') as f:
                self.assertEqual(f.read(), '只寫了一行,沒有區塊')
            prefs.ensure_note(note)
            with open(note, encoding='utf-8') as f:
                self.assertIn(prefs.NOTE_AGENT, f.read())


if __name__ == '__main__':
    unittest.main()
