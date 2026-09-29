# -*- coding: utf-8 -*-
"""commit 前的私人字串掃描:路徑前綴條目要比對得到;公開鏡像同步前指定的清單讀不到或是空的就算失敗。"""
import os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')))
import privacy_scan as ps  # noqa: E402


class PrivacyScan(unittest.TestCase):
    def words_file(self, text):
        f = tempfile.NamedTemporaryFile('w', suffix='-private-words', delete=False, encoding='utf-8')
        f.write(text)
        f.close()
        self.addCleanup(os.remove, f.name)
        return f.name

    def test_path_prefix_entry_matches_inside_a_path(self):
        # 以前套「完整的詞」規則:路徑前綴後面一定接帳號名,永遠比對不到,帶路徑的截圖就這樣漏進 repo
        pats, _ = ps.words(self.words_file('/srv/private/\nDoe\n'))
        hits = ps.scan_lines([('a.md', 1, '存在 /srv/private/someone/x'), ('b.md', 1, 'Doer 不算')], pats)
        self.assertEqual([(f, w) for f, _n, w, _l in hits], [('a.md', '/srv/private/')])

    def test_given_word_list_missing_or_empty_fails(self):
        for path in (os.path.join(tempfile.gettempdir(), 'no-such-private-words'), self.words_file('# 只有註解\n')):
            with mock.patch.object(sys, 'argv', ['privacy_scan.py', '--all', '--words', path]), \
                    mock.patch.object(ps, 'all_lines', return_value=[]):
                self.assertEqual(ps.main(), 1, path)

    def test_message_is_scanned_like_text_leaving_the_repo(self):
        path = self.words_file('Doe\n')
        with mock.patch.object(sys, 'argv', ['privacy_scan.py', '--all', '--words', path,
                                             '--message', '改了 Doe 的看板']), \
                mock.patch.object(ps, 'all_lines', return_value=[('ok.md', 1, '乾淨')]):
            self.assertEqual(ps.main(), 1)
        with mock.patch.object(sys, 'argv', ['privacy_scan.py', '--all', '--words', path, '--message', '乾淨的標題']), \
                mock.patch.object(ps, 'all_lines', return_value=[('ok.md', 1, '乾淨')]):
            self.assertEqual(ps.main(), 0)


if __name__ == '__main__':
    unittest.main()
