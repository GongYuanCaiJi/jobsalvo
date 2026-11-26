import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import _env  # noqa: E402  測試跑在暫存資料夾
import board_doc as bd  # noqa: E402


class JournalFails(unittest.TestCase):
    def test_a_journal_that_cannot_be_written_keeps_the_change_and_leaves_a_report_on_the_board(self):
        with tempfile.TemporaryDirectory() as d:
            path = _env.make_board(os.path.join(d, 'board.html'))
            with mock.patch.object(bd, 'journal', side_effect=PermissionError(13, '沒有權限')):
                bd.set_fb(lambda fb: fb.__setitem__('J', {'app': 'ready'}), live=path, by='測試')
            fb = _env.read_fb(path)
            self.assertEqual(fb['J'], {'app': 'ready'})
            [it] = fb['__inbox__']
            self.assertEqual((it['from'], it['msg']), ('看板', '標記流水帳寫不進去,這次的改動沒有留紀錄:沒有權限'))
            self.assertIn(os.path.basename(bd.journal_path(path)), it['need'])


if __name__ == '__main__':
    unittest.main()
