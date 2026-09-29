import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')))
import interview_bank


class InterviewBankExport(unittest.TestCase):
    def test_readable_export_is_one_way_and_replaced_from_canonical_bank(self):
        bank = {
            'cats': ['經驗'],
            'items': [interview_bank.from_form({
                'id': 'q1', 't': '怎麼拆問題？', 'cat': '經驗', 'ask': '描述一個例子',
                'focus': '先確認條件', 'stage': 'ok', 'script': '## 先釐清\n問清楚輸入',
            })],
        }
        with tempfile.TemporaryDirectory(prefix='bank-export-') as home:
            path = interview_bank.export_file(bank, home)
            with open(path, encoding='utf-8') as source:
                content = source.read()
            self.assertIn('# 面試題庫', content)
            self.assertIn('怎麼拆問題？', content)
            self.assertIn('問清楚輸入', content)
            mtime = os.stat(path).st_mtime_ns
            interview_bank.export_file(bank, home)
            self.assertEqual(os.stat(path).st_mtime_ns, mtime)
            with open(path, 'w', encoding='utf-8') as target:
                target.write('edited by hand')
            interview_bank.export_file(bank, home)
            with open(path, encoding='utf-8') as source:
                self.assertEqual(source.read(), content)
            self.assertEqual(bank['items'][0]['t'], '怎麼拆問題？')


if __name__ == '__main__':
    unittest.main()
