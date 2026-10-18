"""逐處證據報告只用程式讀頁與事件，不從 agent 文字猜結果。"""
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import sync_report  # noqa: E402


class SyncReport(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())

    def folder(self, name, rows):
        folder = Path(self.tmp) / name
        folder.mkdir()
        with open(folder / 'events.jsonl', 'w', encoding='utf-8') as target:
            for row in rows:
                target.write(json.dumps(row, ensure_ascii=False) + '\n')
        return folder

    def test_groups_rounds_counts_verdicts_and_escapes_read_text(self):
        folder = self.folder('20261014-100000-000001-sync_profiles-example_test_zh_one', [
            {'kind': 'slot_start', 'platform': 'example.test', 'lang': 'zh', 'variant': 'one'},
            {'kind': 'change', 'item': '作品', 'verdicts': ['agent 代填待確認', '範圍外有內容消失'],
             'platform_result': '只驗收、已撤回', 'before_text': '<舊文>', 'after_text': '新文',
             'outside_losses': ['旁欄消失'], 'answer_question': '常用答案題目'},
            {'kind': 'change', 'item': '技能', 'verdicts': ['照原文'],
             'platform_result': '平台沒變／跟原稿相同', 'before_text': '原文', 'after_text': '原文',
             'before_shot': 'round/before.png', 'after_shot': 'round/after.png'},
            {'kind': 'sync_result', 'ok': True, 'problems': []},
        ])
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY3sAAAAASUVORK5CYII=')
        for name in ('before.png', 'after.png'):
            (folder / name).write_bytes(png)
        output = Path(sync_report.write_report([folder], Path(self.tmp) / 'report.html'))
        page = output.read_text(encoding='utf-8')
        self.assertIn('example.test · zh/one：通過', page)
        self.assertIn('agent 代填待確認 1', page)
        self.assertIn('範圍外有內容消失 1', page)
        self.assertIn('&lt;舊文&gt;', page)
        self.assertNotIn('<舊文>', page)
        self.assertLess(page.index('作品：agent 代填待確認'), page.index('技能：照原文'))
        self.assertIn('用到的常用答案：常用答案題目', page)
        self.assertIn('這輪沒有局部截圖', page)
        self.assertEqual(page.count('<img src="data:image/png;base64,'), 2)

    def test_old_round_rebuilds_text_and_marks_missing_local_shots(self):
        folder = self.folder('20261014-100000-000002-sync_profiles-example_test_zh_one', [
            {'kind': 'page', 'why': 'before-0', 'file': 'page-1.json', 'n': 1},
            {'kind': 'page', 'why': 'after-0', 'file': 'page-2.json', 'n': 2},
            {'kind': 'check', 'phase': 'compare', 'item': '作品[0]', 'ids': [2], 'problems': [], 'n': 3},
            {'kind': 'verification', 'item': '作品[0]', 'verdict': '照原文', 'n': 4},
        ])
        for name, text in (('page-1.json', '舊影片'), ('page-2.json', '舊影片')):
            (folder / name).write_text(json.dumps({'readingParts': ['作品', text]}), encoding='utf-8')
        output = Path(sync_report.write_report([folder], Path(self.tmp) / 'old.html'))
        page = output.read_text(encoding='utf-8')
        self.assertIn('無法判定（既有比對未見失敗；舊輪無終局標記）', page)
        self.assertIn('舊影片', page)
        self.assertIn('這輪沒有局部截圖', page)

    def test_multiple_rounds_group_by_platform_and_variant_with_status(self):
        first = self.folder('20261014-100000-000003-sync_profiles-alpha_zh_one', [
            {'kind': 'slot_start', 'platform': 'alpha', 'lang': 'zh', 'variant': 'one'},
            {'kind': 'change', 'item': '作品', 'verdicts': ['照原文'],
             'platform_result': '已存上平台', 'before_text': '舊', 'after_text': '新'},
            {'kind': 'sync_result', 'ok': True, 'problems': []},
        ])
        second = self.folder('20261014-100000-000004-sync_profiles-beta_en_two', [
            {'kind': 'slot_start', 'platform': 'beta', 'lang': 'en', 'variant': 'two'},
            {'kind': 'change', 'item': 'Summary', 'verdicts': ['未判定'],
             'platform_result': '沒做成', 'step': '讀取改後頁面', 'reason': '逾時'},
            {'kind': 'sync_result', 'ok': False, 'problems': ['逾時']},
        ])
        page = Path(sync_report.write_report([second, first], Path(self.tmp) / 'many.html')).read_text()
        self.assertIn('alpha · zh/one：通過', page)
        self.assertIn('beta · en/two：未通過', page)
        self.assertIn('平台實際結果</b><span>沒做成（停在讀取改後頁面：逾時）', page)


if __name__ == '__main__':
    unittest.main()
