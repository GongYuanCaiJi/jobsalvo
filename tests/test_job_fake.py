"""副本上的假流程(job_fake)要跟真的一樣照「跑幾張」「只跑這張」,填表要留下表單紀錄與交履歷方式。
不然副本上走介面時,選了 1 張卻整頁都被填掉,新卡也永遠走不到核准。"""
import json, os, tempfile, unittest
import _env  # noqa: F401
import board_doc as bd
import demo
import job_fake


from _env import read_board as _read  # noqa: E402


class _FakeBoard(unittest.TestCase):
    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory(prefix='jobfake-'))
        self.board = demo.build(os.path.join(self.dir, 'board.html'))
        self.ids = [j['id'] for j in _read(self.board)['data']['jobs']][:4]

    def _fb(self):
        return json.loads(_read(self.board)['fb'])

    def _set(self, app):
        def mut(fb):
            for i in self.ids:
                entry = fb.setdefault(i, {})
                entry['app'] = app
                entry.pop('apply', None)
                entry.pop('form', None)
        bd.set_fb(mut, live=self.board)


class JobFakeLimitTest(_FakeBoard):
    def test_fill_honors_limit_and_leaves_form_and_delivery(self):
        self._set('ship')
        job_fake.apply(self.board, self.dir, 0, 'fill', '', limit=1)
        fb = self._fb()
        filled = [i for i in self.ids if (fb.get(i) or {}).get('apply')]
        self.assertEqual(len(filled), 1)
        m = fb[filled[0]]
        self.assertEqual(m['apply']['delivery']['method'], 'direct_upload')
        self.assertIn('form', m)

    def test_prep_one_card_only(self):
        self._set('prep')
        job_fake.prep(self.board, self.dir, 0, url=self.ids[1])
        fb = self._fb()
        self.assertEqual(fb[self.ids[1]]['app'], 'ready')
        self.assertEqual(fb[self.ids[0]]['app'], 'prep')

    def test_prep_limit(self):
        self._set('prep')
        job_fake.prep(self.board, self.dir, 0, limit=2)
        fb = self._fb()
        self.assertEqual(sum(1 for i in self.ids if fb[i]['app'] == 'ready'), 2)


class JobFakeMatchesApplyRun(_FakeBoard):
    """副本上的假代投要寫出跟真的 apply_run 同一個形狀,並且過得了真的核准規則(form_record)。
    這一輪已經漂移三次(少 apply.delivery、沒留表單、不照張數),每次都是副本上走不完才發現。"""

    def test_fake_fill_then_approve_then_submit_passes_real_rules(self):
        import form_record as fr
        self._set('ship')
        u = self.ids[0]
        job_fake.apply(self.board, self.dir, 0, 'fill', u)
        fb = self._fb()
        self.assertEqual(fr.validate(fb), [])
        self.assertEqual(fr.approval_problem(fb, u, fr.board_status(self.board)), '還沒確認送出')
        import delivery_state as ds
        bd.set_fb(lambda f: ds.fire(f, u, 'confirm', approve={'snap': fr.snapshot(f, u)}), live=self.board)
        self.assertIsNone(fr.approval_problem(self._fb(), u, fr.board_status(self.board)))
        job_fake.apply(self.board, self.dir, 0, 'submit', '')
        m = self._fb()[u]
        self.assertEqual(m['app'], 'sent')
        self.assertTrue(m['form']['lock'])
        # 沒核准的那幾張一張都不動
        self.assertTrue(all(self._fb()[i]['app'] == 'ship' for i in self.ids[1:]))

    def test_fill_makes_old_approval_void_like_form_record(self):
        """真的填表會用 form_record 重記表單,舊的核准就作廢;假的也一樣。"""
        self._set('ship')
        u = self.ids[0]
        bd.set_fb(lambda f: f[u].update(form={'plat': 'x', 'f': [], 'at': '2000-01-01'},
                                        approve={'snap': {}}), live=self.board)
        job_fake.apply(self.board, self.dir, 0, 'fill', u)
        m = self._fb()[u]
        self.assertNotIn('approve', m)
        import apply_run
        self.assertEqual(m['form']['at'], apply_run.today())


if __name__ == '__main__':
    unittest.main()
