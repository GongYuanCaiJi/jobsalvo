# -*- coding: utf-8 -*-
"""找缺時間上限:暫停的時間不算;「更深＋更廣」共用同一個時間,前一段用完就不開下一段;設定只收 0~999。"""
import os, sys, tempfile, time, types, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import jobrun  # noqa: E402
import converge  # noqa: E402
import settings_api  # noqa: E402


class FindTime(unittest.TestCase):
    def test_paused_time_is_not_counted(self):
        d = tempfile.mkdtemp(prefix='find-time-')
        path = os.path.join(d, 'converge_status.json')
        self.assertEqual(jobrun.paused_seconds(path), 0)
        mark = path + '.paused'
        open(mark, 'w').close()
        os.utime(mark, (time.time() - 30, time.time() - 30))     # 停了 30 秒、現在還停著
        self.assertAlmostEqual(jobrun.paused_seconds(path), 30, delta=2)
        with open(path + '.pausedsum', 'w') as f:                  # 之前另外停過 100 秒
            f.write('100')
        self.assertAlmostEqual(jobrun.paused_seconds(path), 130, delta=2)
        jobrun.clear_paused(path)
        os.remove(mark)
        self.assertEqual(jobrun.paused_seconds(path), 0)

    def test_both_modes_share_the_time_and_wide_is_skipped_when_deep_used_it_up(self):
        a = types.SimpleNamespace(mode='both', seed_url=[], seed_co=[], dry=False, live='board.html', limit=0, minutes=15)
        clock = {'up': False}
        ran = []

        def fake_run(mode, *args, **kw):
            ran.append((mode, kw['minutes']))
            kw['tally']['found'] += 4
            clock['up'] = True                  # 更深這一段就把時間用完了
            return 2
        done = {}
        with mock.patch('prefs.load', return_value=({}, [])), \
                mock.patch('research.run', side_effect=fake_run), \
                mock.patch('research.pending_count', return_value=0), \
                mock.patch.object(converge.subprocess, 'run'):
            converge._main(a, '', lambda phase, **kw: done.update(kw, phase=phase), lambda: False, lambda: clock['up'])
        self.assertEqual(ran, [('deep', 15)])
        self.assertEqual((done['phase'], done['added'], done['found'], done['timeup']), ('done', 2, 4, True))
        self.assertIn('時間到', done['msg'])

    def test_setting_takes_blank_zero_or_1_to_999(self):
        for ok in (None, '', 0, 15, 999):
            self.assertEqual(settings_api.find_minutes_problem(ok), '', ok)
        for bad in (-1, 1000, '15', 1.5, True):
            self.assertTrue(settings_api.find_minutes_problem(bad), bad)


class TimeUpSparesNoteAgent(unittest.TestCase):
    """時間只限制「找」:時間到停掉找缺的 agent,整理偏好筆記那隻不停(它一停,筆記永遠更新不了)。"""

    def test_only_search_agent_is_stopped(self):
        import subprocess, time as _t
        import research
        search = subprocess.Popen(['sleep', '30'])
        note = subprocess.Popen(['sleep', '30'])
        try:
            with research.stop_agent_when(lambda: True, every=0.1, spare=lambda: [note.pid]):
                _t.sleep(1)
            self.assertIsNotNone(search.poll())    # 找的那隻被停了
            self.assertIsNone(note.poll())         # 整理筆記那隻還在跑
        finally:
            for p in (search, note):
                p.kill()
                p.wait()


if __name__ == '__main__':
    unittest.main()
