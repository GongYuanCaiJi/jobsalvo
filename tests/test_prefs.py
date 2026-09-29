import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import prefs  # noqa: E402


class LegacyHardRules(unittest.TestCase):
    def test_heading_with_explanation_migrates_verbatim_and_leaves_source_untouched(self):
        with tempfile.TemporaryDirectory(prefix='prefs-migration-') as directory:
            legacy = os.path.join(directory, 'legacy-prefs.md')
            note = os.path.join(directory, 'preference-note.md')
            original = ('# 偏好\n\n## 硬規則（保留原話）\n第一條\n第二條  原文\n\n'
                        '## 其他\n不要搬這段\n')
            with open(legacy, 'w', encoding='utf-8') as source:
                source.write(original)

            prefs.ensure_note(path=note, legacy_path=legacy)

            custom, _agent = prefs.note_sections(note)
            self.assertEqual(custom, '第一條\n第二條  原文')
            with open(legacy, encoding='utf-8') as source:
                self.assertEqual(source.read(), original)

    def test_existing_note_is_not_overwritten_by_a_rerun(self):
        with tempfile.TemporaryDirectory(prefix='prefs-migration-') as directory:
            legacy = os.path.join(directory, 'legacy-prefs.md')
            note = os.path.join(directory, 'preference-note.md')
            with open(legacy, 'w', encoding='utf-8') as source:
                source.write('## 硬規則（說明）\n舊內容\n')

            prefs.ensure_note(path=note, legacy_path=legacy)
            prefs.save_custom_text('使用者後來改過', path=note)
            prefs.ensure_note(path=note, legacy_path=legacy)

            custom, _agent = prefs.note_sections(note)
            self.assertEqual(custom, '使用者後來改過')


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


if __name__ == '__main__':
    unittest.main()
