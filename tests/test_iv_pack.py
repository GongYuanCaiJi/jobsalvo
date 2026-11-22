import os, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import iv_pack


class SlotTest(unittest.TestCase):
    def test_slot_path_is_per_card_under_home(self):
        p = iv_pack.slot_path('https://www.104.com.tw/job/96vzh', '/h')
        self.assertTrue(p.startswith('/h/平台履歷/') and p.endswith('.pdf'))

    def test_order_and_empty_platform_slot(self):
        """順序:自己的履歷、平台履歷、附件、面試備忘;平台沒放就有一頁「尚未放入」,不跳過。"""
        seen = []
        with tempfile.TemporaryDirectory() as home:
            for name in ('own', 'att'):
                open(os.path.join(home, name + '.pdf'), 'wb').write(b'%PDF')
            docs = [{'kind': 'resume', 'id': 'r', 'name': 'r', 'effective_path': os.path.join(home, 'own.pdf')},
                    {'kind': 'attachment', 'id': 'a', 'name': 'a', 'effective_path': os.path.join(home, 'att.pdf')}]

            def fake_page(title, body, work, name):
                seen.append(title)
                return os.path.join(home, name + '.pdf')

            with mock.patch.object(iv_pack.iv_sheet, '_job', return_value={}), \
                 mock.patch.object(iv_pack.ship, 'documents', return_value=docs), \
                 mock.patch.object(iv_pack, '_empty_page', side_effect=fake_page), \
                 mock.patch.object(iv_pack.iv_sheet, 'pdf_bytes', return_value=b'%PDF'), \
                 mock.patch.object(iv_pack.pdf_tools, 'merge',
                                   side_effect=lambda files, out: (seen.extend(os.path.basename(f) for f in files),
                                                                   open(out, 'wb').write(b'x'))):
                iv_pack.pack_bytes({}, {}, 'https://www.104.com.tw/job/96vzh', home)
        self.assertEqual(seen[0], '平台履歷:尚未放入')
        self.assertEqual(seen[1:], ['own.pdf', 'platform.pdf', 'att.pdf', 'memo.pdf'])

    def test_put_places_pdf_in_slot_and_rejects_non_pdf(self):
        with tempfile.TemporaryDirectory() as home:
            good, bad = os.path.join(home, 'g.pdf'), os.path.join(home, 'b.txt')
            open(good, 'wb').write(b'%PDF-1.4 x')
            open(bad, 'wb').write(b'hello')
            dest = iv_pack.put('https://x/job/1', good, home)
            self.assertEqual(dest, iv_pack.slot_path('https://x/job/1', home))
            self.assertTrue(os.path.isfile(dest))
            with self.assertRaises(ValueError):
                iv_pack.put('https://x/job/1', bad, home)


class WriteRerendersMemoTest(unittest.TestCase):
    def test_every_iv_write_rerenders_the_memos(self):
        """命令列改題庫,小抄(面試備忘 PDF)要跟著重排,不靠記得另外排。"""
        import iv
        with mock.patch.object(iv.bd, 'set_data', side_effect=lambda mut: mut({}, {})), \
             mock.patch.object(iv.ib, 'apply', return_value={}), mock.patch.object(iv.ib, 'export_file'), \
             mock.patch('folder_history.note_saved'), mock.patch.object(iv, '_rerender_memos') as rerender:
            iv.write('put', {}, None, None)
        rerender.assert_called()


if __name__ == '__main__':
    unittest.main()
