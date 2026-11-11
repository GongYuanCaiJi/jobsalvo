"""Regression coverage for a stuck application whose ego page disappears."""
import json, os, tempfile, unittest
from unittest import mock

import _env  # noqa: F401
import autopilot as ap
import board_doc as bd
import chrome_door
import demo
import delivery_state as ds


FLOW = {'auto_prep': False, 'auto_advance': True, 'auto_fill': True, 'replies_at': ''}


class StuckPageLostTest(unittest.TestCase):
    def test_stuck_page_loss_stays_held_until_user_hands_it_back(self):
        with tempfile.TemporaryDirectory(prefix='issue-742-') as tmp:
            path = demo.build(os.path.join(tmp, 'board.html'))
            jobs = bd.parse(bd.read_doc(path))['data']['jobs']
            url = jobs[0]['id']
            skip = [job['id'] for job in jobs[1:]]
            original = '卡在必填欄'
            bd.set_fb(lambda fb: fb.update({
                '__auto__': {'since': '2026-01-01T00:00:00', 'skip': skip,
                             'tried': ['fill:' + url + ':new'], 'seen': {}, 'flow': FLOW},
                url: {'app': 'ship', 'ds': 'stuck', 'apply': {
                    'stage': 'fill', 'at': '2026-01-05T10:05:00', 'tab_id': 't1',
                    'workspace': {'id': 'w1', 'name': 'job', 'page': 'apply'}, 'issues': [original]}}
            }), live=path)

            with mock.patch.object(bd, 'is_live', return_value=True), \
                 mock.patch.object(chrome_door, 'gone_pages', return_value=[url]):
                changed = chrome_door.sweep_gone(path)
            self.assertIn(url, changed)
            fb = json.loads(bd.load(path)['fb'])
            self.assertEqual(ds.state(fb[url]), 'stuck')
            self.assertEqual(fb[url]['apply'].get('tab_id'), '')
            self.assertEqual(fb[url]['apply']['issues'], [original])
            self.assertFalse(ds.held(fb, url))          # 頁不在了,不佔停著的頁上限

            other = json.loads(json.dumps(fb))          # 別的入口(👀 換過、apply_run)帶自己的原因,也不蓋掉
            ds.try_fire(other, url, 'page_lost', issues=['別的原因'])
            self.assertEqual(other[url]['apply']['issues'], [original])

            calls = []
            def start(kind, args):
                calls.append((kind, {key: value for key, value in args.items() if not key.startswith('_')}))
                return 200, {}

            with mock.patch.object(ap, 'flow', return_value=FLOW):
                p = ap.Pilot(path, start,
                             lambda kind: {}, lambda: {'built': 0.0, 'tried': 0.0, 'running': False},
                             lambda: True, build=lambda: None)
                p.step()
            self.assertEqual(calls, [])

            # Only an explicit user handback makes the failed card eligible again.
            bd.set_fb(lambda current: current.setdefault('__inbox__', []).append({
                'id': 'report-1', 'job': url, 'done': True, 'done_at': '2026-01-05T10:06:00'
            }), live=path)
            with mock.patch.object(ap, 'flow', return_value=FLOW):
                p.step()
            self.assertIn(('apply', {'stage': 'fill', 'url': url}), calls)


if __name__ == '__main__':
    unittest.main()
