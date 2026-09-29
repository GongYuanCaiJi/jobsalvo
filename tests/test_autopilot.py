"""流程自動往下跑(tools/autopilot.py):替他按「跑準備區／可投遞／填表／查回音」的規則。

鎖住四個會出事的地方:開啟當下已在流程裡的卡不碰(不然一開就派幾十隻 agent)、
失敗過的不自動重跑(不然同一張永遠重跑)、驗收要等這張進來之後跑的那一輪、任何路徑都不會替他送出。"""
import datetime, json, os, shutil, tempfile, unittest
from unittest import mock
import _env  # noqa: F401
import agent_chrome
import autopilot as ap
import board_doc as bd
import demo

CFG = {'auto_prep': True, 'auto_advance': True, 'auto_fill': True, 'replies_at': '09:00'}
NOW = datetime.datetime(2026, 1, 5, 10, 0)
OKST = {'schema_version': 2, 'checked_links': True, 'issues': []}


def data(*ids, **kw):
    return {'jobs': [dict({'id': i}, **kw.get(i, {})) for i in ids], 'status': kw.get('status', OKST)}


def run(d, fb, gen=0, building=False, running=None, now=NOW, real=True, cfg=CFG, last=None):
    return ap.plan(d, fb, verified_gen=gen, build_running=building, running=running or {}, now=now, cfg=cfg, real=real,
                   replies_last=last)


def auto(**kw):
    a = {'since': '2026-01-01T00:00:00', 'skip': [], 'tried': [], 'seen': {}}
    a.update(kw)
    return a


class PlanTest(unittest.TestCase):
    def test_first_time_records_backlog_and_does_nothing_else(self):
        fb = {'a': {'app': 'prep'}, 'b': {'app': 'ship'}, 'c': {'s': 'like'}, 'd': {'app': 'ready', 'rm': 1}}
        p = run(data('a', 'b', 'c', 'd'), fb)
        self.assertEqual(p['init']['skip'], ['a', 'b'])
        self.assertIsNone(p['prep']); self.assertIsNone(p['fill']); self.assertEqual(p['advance'], [])

    def test_backlog_is_never_touched(self):
        fb = {'__auto__': auto(skip=['a', 'b']), 'a': {'app': 'prep'}, 'b': {'app': 'ship'}}
        p = run(data('a', 'b'), fb)
        self.assertIsNone(p['prep']); self.assertIsNone(p['fill'])

    def test_prep_batch_when_only_new_cards_single_when_mixed(self):
        fb = {'__auto__': auto(), 'a': {'app': 'prep'}, 'b': {'app': 'prep'}}
        self.assertEqual(run(data('a', 'b'), fb)['prep'], 'all')
        fb['__auto__']['skip'] = ['b']
        self.assertEqual(run(data('a', 'b'), fb)['prep'], ['a'])

    def test_prep_failure_or_tried_is_not_rerun(self):
        fb = {'__auto__': auto(tried=['prep:a']), 'a': {'app': 'prep'}, 'b': {'app': 'prep'}}
        d = data('a', 'b', b={'prep_note': '抓不到 JD'})
        self.assertIsNone(run(d, fb)['prep'])

    def test_prep_waits_while_running(self):
        fb = {'__auto__': auto(), 'a': {'app': 'prep'}}
        self.assertIsNone(run(data('a'), fb, running={'prep': True})['prep'])

    def test_advance_waits_for_a_build_after_the_card_arrived(self):
        fb = {'__auto__': auto(), 'a': {'app': 'ready'}}
        p = run(data('a'), fb, gen=3)
        self.assertEqual(p['advance'], []); self.assertEqual(p['seen'], {'a': 3})
        fb['__auto__']['seen'] = {'a': 3}
        self.assertEqual(run(data('a'), fb, gen=3)['advance'], [])              # 還沒有新的一輪
        self.assertEqual(run(data('a'), fb, gen=4, building=True)['advance'], [])
        self.assertEqual(run(data('a'), fb, gen=4)['advance'], ['a'])

    def test_advance_once_then_manual_back_stays(self):
        fb = {'__auto__': auto(seen={'a': 0}), 'a': {'app': 'ready'}}
        p = run(data('a'), fb, gen=1)
        self.assertEqual(p['advance'], ['a']); self.assertIn('adv:a', p['tried'])
        fb['__auto__']['tried'] = p['tried']
        self.assertEqual(run(data('a'), fb, gen=5)['advance'], [])

    def test_advance_seen_during_a_running_build_waits_for_the_next_one(self):
        fb = {'__auto__': auto(), 'a': {'app': 'ready'}}
        self.assertEqual(run(data('a'), fb, gen=3, building=True)['seen'], {'a': 4})

    def test_blocked_cards_stay(self):
        fb = {'__auto__': auto(seen={'a': 0, 'b': 0}), 'a': {'app': 'ready'},
              'b': {'app': 'ready', 'resume_id': 'general',
                    'custom_docs': {'resume:general:zh': {'status': 'review', 'name': '履歷'}}}}
        st = {'schema_version': 2, 'checked_links': True, 'issues': [{'jid': 'a', 'msg': '職缺已下架'}]}
        self.assertEqual(run(data('a', 'b', status=st), fb, gen=1)['advance'], [])
        self.assertEqual(run(data('a', 'b', status=None), fb, gen=1)['advance'], [])

    def test_custom_record_of_a_file_this_card_no_longer_sends_does_not_block(self):
        # 客製檔等你看的時候換了這張的履歷:舊履歷那筆紀錄留著(換回來還在),但不會寄出去,不能擋推進。
        # 以前擋推進看的是每一筆紀錄,要寄的檔案那邊只看現在這幾份,卡永遠卡在待你決定,也沒有按鈕解得開
        fb = {'__auto__': auto(seen={'b': 0}),
              'b': {'app': 'ready', 'resume_id': 'tech',
                    'custom_docs': {'resume:general:zh': {'status': 'review', 'name': '通用版'}}}}
        self.assertEqual(run(data('b'), fb, gen=1)['advance'], ['b'])
        fb['b']['custom_docs']['resume:tech:zh'] = {'status': 'working', 'name': '技術版'}
        self.assertEqual(run(data('b'), fb, gen=1)['advance'], [])

    def test_blocked_company_stays_in_ready(self):
        url = 'https://jobs.lever.co/acme/example'
        fb = {'__auto__': auto(seen={url: 0}), '__block__': ['Acme, Inc.'],
              url: {'app': 'ready'}}
        self.assertEqual(run(data(url), fb, gen=1)['advance'], [])

    def test_unverified_link_hint_does_not_block_auto_advance(self):
        fb = {'__auto__': auto(seen={'a': 0}), 'a': {'app': 'ready'}}
        st = {'schema_version': 2, 'checked_links': True,
              'issues': [{'jid': 'a', 'stage': 'ready', 'kind': 'unverified', 'soft': True,
                          'msg': '職缺頁擷取失敗,狀態未知'}]}
        self.assertEqual(run(data('a', status=st), fb, gen=1)['advance'], ['a'])

    def test_sandbox_advances_without_a_build(self):
        fb = {'__auto__': auto(), 'a': {'app': 'ready'}}
        self.assertEqual(run(data('a'), fb, real=False)['advance'], ['a'])

    def test_fill_one_unfilled_card_at_a_time(self):
        fb = {'__auto__': auto(), 'a': {'app': 'ship'}, 'b': {'app': 'ship'}}
        p = run(data('a', 'b'), fb)
        self.assertEqual(p['fill'], 'a'); self.assertEqual(p['tried'], ['fill:a:new'])
        fb['__auto__']['tried'] = ['fill:a:new']
        self.assertEqual(run(data('a', 'b'), fb)['fill'], 'b')

    def test_apply_again_is_filled_again(self):
        # 第一次是自動填的(fill:a:new 用掉了),被拒後他按「🔁 再投一次」:表單和填表紀錄收進 tries、回到可投遞。
        # 卡上寫「排隊中:會自動填這張」,自動流程就要真的再填一次
        fb = {'__auto__': auto(tried=['fill:a:new']), 'a': {'app': 'ship', 'tries': [{'app': 'sent', 'form': {'lock': 1}}]}}
        p = run(data('a'), fb)
        self.assertEqual(p['fill'], 'a')
        fb['__auto__']['tried'] += p['tried']
        self.assertIsNone(run(data('a'), fb)['fill'])                        # 這一次也只填一次

    def test_fill_skips_filled_approved_sent_and_running(self):
        fb = {'__auto__': auto(), 'a': {'app': 'ship', 'apply': {'stage': 'fill', 'ok': False, 'at': 't1'}},
              'b': {'app': 'ship', 'approve': {'at': 'x'}}, 'c': {'app': 'ship', 'form': {'lock': 1}},
              'd': {'app': 'sent'}}
        self.assertIsNone(run(data('a', 'b', 'c', 'd'), fb)['fill'])
        fb['e'] = {'app': 'ship'}
        self.assertIsNone(run(data('a', 'b', 'c', 'd', 'e'), fb, running={'apply': True})['fill'])

    def test_fill_stops_when_enough_filled_pages_are_waiting_for_him(self):
        # 每張填好的都開著一個分頁等他看:停滿了就先不填新的,他核准(送出)或退掉一張再接著填
        held = {'app': 'ship', 'apply': {'stage': 'fill', 'ok': True, 'at': 't1', 'tab_id': '7'}}
        fb = {'__auto__': auto(), 'a': dict(held), 'b': dict(held), 'c': {'app': 'ship'}}
        cfg = dict(CFG, fill_max=2)
        self.assertIsNone(run(data('a', 'b', 'c'), fb, cfg=cfg)['fill'])
        # 已經停著、履歷換過要重填的那張不算新的,照樣填
        fb['a'] = {'app': 'ship', 'apply': dict(held['apply'], stale='履歷換了')}
        self.assertEqual(run(data('a', 'b', 'c'), fb, cfg=cfg)['fill'], 'a')
        fb['a'] = {'app': 'sent', 'form': {'lock': 1}}                    # 他送出了一張:空出位子
        self.assertEqual(run(data('a', 'b', 'c'), fb, cfg=cfg)['fill'], 'c')
        fb['a'] = dict(held)
        self.assertEqual(run(data('a', 'b', 'c'), fb, cfg=dict(CFG, fill_max=0))['fill'], 'c')   # 0 = 不限
        self.assertEqual(run(data('a', 'b', 'c'), fb, cfg=dict(CFG, fill_max=None))['fill'], 'c')  # 沒設 = 預設 5,才停 2 張
        self.assertEqual(run(data('a', 'b', 'c'), fb, cfg=dict(CFG, fill_max=''))['fill'], 'c')    # 設定頁那格清空 = 預設 5

    def test_stale_fill_is_refilled_once(self):
        fb = {'__auto__': auto(), 'a': {'app': 'ship', 'apply': {'stage': 'fill', 'ok': True, 'at': 't1', 'stale': '履歷換了'}}}
        p = run(data('a'), fb)
        self.assertEqual(p['fill'], 'a')
        fb['__auto__']['tried'] = p['tried']
        self.assertIsNone(run(data('a'), fb)['fill'])

    def test_page_gone_is_refilled_once(self):
        # agent 的 Chrome 關過:那一頁不在了。卡住的卡平常等他,但頁面不見不是他要處理的事,自動重填
        fb = {'__auto__': auto(), 'a': {'app': 'ship', 'apply': {'stage': 'fill', 'ok': True, 'at': '2026-01-05T09:00:00', 'tab_id': '7'}}}
        with mock.patch('agent_chrome.pid', return_value=None):
            gone = agent_chrome.gone_pages(fb)
        self.assertEqual(gone, ['a'])
        agent_chrome.mark_gone(fb, gone)
        p = run(data('a'), fb)
        self.assertEqual(p['fill'], 'a')
        fb['__auto__']['tried'] = p['tried']
        self.assertIsNone(run(data('a'), fb)['fill'])

    def test_changing_files_after_approval_requires_new_fill_and_approval(self):
        fb = {'__auto__': auto(), 'a': {'app': 'ship', 'approve': {'at': 't1'},
              'apply': {'stage': 'fill', 'ok': True, 'at': 't1'}, 'form': {'f': []}}}
        self.assertTrue(ap.fr.mark_stale(fb, 'a', '履歷換了'))
        self.assertEqual(run(data('a'), fb)['fill'], 'a')
        self.assertNotIn('approve', fb['a'])

    def _filled(self, **apply):
        a = dict({'stage': 'fill', 'ok': True, 'at': 't1', 'session': 's1'}, **apply)
        return {'__ans__': [{'k': 'why', 'q': '為什麼', 'zh': '新的', 'v': 'new'}],
                '__auto__': auto(), 'a': {'app': 'ship', 'apply': a,
                                          'form': {'f': [{'q': '為什麼', 'src': 'bank', 'k': 'why', 'refill': 1}]}}}

    def test_refix_waits_until_he_stops_editing_then_runs_once(self):
        fb = self._filled()
        p = run(data('a'), fb)
        self.assertIsNone(p['fix']); self.assertIsNone(p['fill'])            # 剛改:先等他停手
        self.assertEqual(p['wait'], 60); self.assertIn('a', p['rf'])
        fb['__auto__']['rf'] = p['rf']
        later = NOW + datetime.timedelta(seconds=30)
        self.assertAlmostEqual(run(data('a'), fb, now=later)['wait'], 30)
        fb['__ans__'][0]['zh'] = '又改了'                                    # 又改一次:重新等
        p2 = run(data('a'), fb, now=NOW + datetime.timedelta(seconds=90))
        self.assertIsNone(p2['fix']); self.assertIn('a', p2['rf'])
        fb['__auto__']['rf'] = p2['rf']
        p3 = run(data('a'), fb, now=NOW + datetime.timedelta(seconds=151))
        self.assertEqual(p3['fix'], 'a'); self.assertEqual(p3['tried'], ['fix:a:' + ap.fix_sig(fb, 'a')])
        fb['__auto__']['tried'] = p3['tried']
        self.assertIsNone(run(data('a'), fb, now=NOW + datetime.timedelta(seconds=300))['fix'])

    def test_refix_that_leaves_the_mark_is_not_repeated(self):
        # 重打完 apply.at 換新,但還標著(agent 沒打好):不能再派,不然同一張一直重打、後面的排不到
        fb = self._filled()
        fb['__auto__']['rf'] = {'a': {'sig': ap.fix_sig(fb, 'a'), 'since': '2026-01-01T00:00:00'}}
        p = run(data('a'), fb)
        self.assertEqual(p['fix'], 'a')
        fb['__auto__']['tried'] = p['tried']
        fb['a']['apply'].update(stage='fix', at='t2')
        self.assertIsNone(run(data('a'), fb)['fix'])
        fb['__ans__'][0]['zh'] = '他又改了'                                  # 他再改:再重打一次
        fb['__auto__']['rf'] = run(data('a'), fb)['rf']
        fb['__auto__']['rf']['a']['since'] = '2026-01-01T00:00:00'
        self.assertEqual(run(data('a'), fb)['fix'], 'a')

    def test_refix_skips_stuck_stale_unfilled_and_waits_for_running(self):
        for kw in ({'ok': False}, {'stale': '履歷換了'}, {'session': ''}):
            fb = self._filled(**kw)
            self.assertFalse(run(data('a'), fb, real=False)['rf'], kw)
        fb = self._filled()
        fb['a']['form']['f'].append({'q': '還沒答', 'src': 'bank', 'k': 'nope'})   # 還有答案等他確認
        self.assertFalse(run(data('a'), fb)['rf'])
        fb = self._filled()
        self.assertFalse(run(data('a'), fb, running={'apply': True})['rf'])
        fb['a']['form']['lock'] = 1
        self.assertFalse(run(data('a'), fb)['rf'])

    def test_refix_goes_before_fill_and_blocks_it(self):
        fb = self._filled()
        fb['__auto__']['rf'] = {'a': {'sig': ap.fix_sig(fb, 'a'), 'since': '2026-01-01T00:00:00'}}
        fb['b'] = {'app': 'ship'}
        p = run(data('a', 'b'), fb)
        self.assertEqual(p['fix'], 'a'); self.assertIsNone(p['fill'])

    def test_an_uncertain_submit_is_not_touched_automatically(self):
        # 送出沒確認成功(可能其實送出去了):自動流程不替它重打、也不重填,等他先確認到底送出沒有
        sf = {'at': 't1', 'problems': ['沒看到成功頁面']}
        fb = self._filled(submit_fail=sf)
        fb['__auto__']['rf'] = {'a': {'sig': ap.fix_sig(fb, 'a'), 'since': '2026-01-01T00:00:00'}}
        self.assertIsNone(run(data('a'), fb)['fix'])
        fb = {'__auto__': auto(), 'a': {'app': 'ship', 'apply': {'stage': 'fill', 'ok': False, 'gone': True,
                                                                  'at': 't1', 'session': 's1', 'submit_fail': sf}}}
        self.assertIsNone(run(data('a'), fb)['fill'])
        sf['cleared'] = True                                                 # 他確認過沒送出:照常
        self.assertEqual(run(data('a'), fb)['fill'], 'a')

    def test_replies_once_a_day_after_the_time(self):
        fb = {'__auto__': auto(), 'a': {'app': 'sent'}}
        self.assertTrue(run(data('a'), fb)['replies'])
        self.assertFalse(run(data('a'), fb, now=NOW.replace(hour=8))['replies'])
        fb['__auto__']['replies_day'] = '2026-01-05'
        self.assertFalse(run(data('a'), fb)['replies'])
        self.assertFalse(run(data('a'), {'__auto__': auto(), 'a': {'app': 'sent', 'oc': 'rej'}})['replies'])

    def test_failed_replies_run_is_retried_hourly_that_day_at_most_three_times(self):
        """#289 決定 3:排程那一輪沒跑成(failed,或跑到一半死掉 died),當天每小時再試一次,最多再試 3 次。"""
        def last(phase, mins, key='finished_at'):
            return {'phase': phase, key: (NOW - datetime.timedelta(minutes=mins)).timestamp()}
        fb = {'__auto__': auto(replies_day='2026-01-05'), 'a': {'app': 'sent'}}
        self.assertTrue(run(data('a'), fb, last=last('failed', 61))['replies'])
        self.assertTrue(run(data('a'), fb, last=last('died', 61, 't0'))['replies'])
        self.assertFalse(run(data('a'), fb, last=last('failed', 30))['replies'])        # 還不到一小時
        self.assertFalse(run(data('a'), fb, last=last('done', 120))['replies'])
        self.assertFalse(run(data('a'), fb, last=last('incomplete', 120))['replies'])   # 部分完成不是沒跑成
        self.assertFalse(run(data('a'), fb, last=last('failed', 11 * 60))['replies'])   # 昨天沒跑成的不算今天的
        fb['__auto__']['replies_retry'] = {'day': '2026-01-05', 'n': 2}
        self.assertTrue(run(data('a'), fb, last=last('failed', 61))['replies'])
        fb['__auto__']['replies_retry'] = {'day': '2026-01-05', 'n': 3}
        self.assertFalse(run(data('a'), fb, last=last('failed', 61))['replies'])
        fb['__auto__']['replies_retry'] = {'day': '2026-01-04', 'n': 3}                   # 昨天的次數不算
        self.assertTrue(run(data('a'), fb, last=last('failed', 61))['replies'])

    def test_board_uses_the_same_retry_numbers(self):
        """已投出最上面那一列寫的「幾點自動再試、第幾次」跟這裡的規則同一組數字。"""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'board', 'board.js'),
                  encoding='utf-8') as f:
            js = f.read()
        want = f'var REPLY_RETRY={{max:{ap.REPLY_RETRY_MAX},gap:{ap.REPLY_RETRY_GAP}}};'
        self.assertTrue(want in js, f'board.js 沒有 {want}')

    def test_invalid_saved_replies_time_never_wraps_to_another_hour(self):
        fb = {'__auto__': auto(), 'a': {'app': 'sent'}}
        self.assertFalse(run(data('a'), fb, cfg=dict(CFG, replies_at='99:99'))['replies'])

    def test_switches_off(self):
        fb = {'__auto__': auto(), 'a': {'app': 'prep'}, 'b': {'app': 'ship'}, 'c': {'app': 'sent'}}
        p = run(data('a', 'b', 'c'), fb, cfg={'replies_at': ''})
        self.assertIsNone(p['prep']); self.assertIsNone(p['fill']); self.assertFalse(p['replies'])


from _env import read_board as _read  # noqa: E402


class PilotStepTest(unittest.TestCase):
    """接上一份真的看板檔:寫得進 __auto__、推進得了可投遞、派出去的只有填表,不會送出。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='autopilot-')
        self.board = demo.build(os.path.join(self.dir, 'board.html'))
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def pilot(self, real=False):
        def start_run(kind, args):
            self.calls.append((kind, args)); return 200, {}
        return ap.Pilot(self.board, start_run, lambda k: {}, lambda: {'gen': 0, 'running': False}, lambda: real)

    def fb(self):
        return json.loads(_read(self.board)['fb'])

    def sent(self):
        return {i for i, m in self.fb().items() if isinstance(m, dict) and m.get('app') == 'sent'}

    def test_a_retry_of_a_failed_replies_run_is_counted(self):
        now = datetime.datetime.now()
        if now.hour < 1:
            self.skipTest('剛過午夜:一小時前是昨天,本來就不重試')
        fin = max(now.replace(hour=0, minute=0, second=1).timestamp(), now.timestamp() - 3700)
        if now.timestamp() - fin < 3600:
            self.skipTest('一小時前是昨天')
        st = {'replies': {'phase': 'failed', 'finished_at': fin}}
        self.pilot().step()                                    # 第一次:盤點
        today = datetime.date.today().isoformat()
        bd.set_fb(lambda f: f['__auto__'].update(replies_day=today), live=self.board, by='test')
        calls = []
        p = ap.Pilot(self.board, lambda kind, args: (calls.append(kind), (200, {}))[1], lambda k: st.get(k, {}),
                     lambda: {'gen': 0, 'running': False}, lambda: False)
        with mock.patch.object(ap, 'flow', return_value=dict(CFG, auto_prep=False, auto_advance=False,
                                                             auto_fill=False, replies_at='00:00')):
            p.step()
        self.assertEqual(calls, ['replies'])
        self.assertEqual(self.fb()['__auto__']['replies_retry'], {'day': today, 'n': 1})

    def test_end_to_end_on_a_board_file(self):
        before = self.sent()
        p = self.pilot()
        p.step()
        backlog = self.fb()['__auto__']['skip']
        new = [j['id'] for j in _read(self.board)['data']['jobs']
               if j['id'] not in backlog and not self.fb().get(j['id'], {}).get('app')][0]

        def put(fb):
            fb.setdefault(new, {})['app'] = 'ready'
        bd.set_fb(put, live=self.board)
        bd.set_data(lambda d, fb: d.__setitem__('status', OKST), live=self.board)
        p.step()
        self.assertEqual(self.fb()[new]['app'], 'ship')
        p.step()
        self.assertIn(('apply', {'stage': 'fill', 'url': new}), self.calls)
        self.assertFalse([c for c in self.calls if c[1].get('stage') == 'submit'])
        self.assertEqual(self.sent(), before)          # 不會替他送出

    def test_restart_rebuilds_before_advancing_a_ready_card(self):
        url = _read(self.board)['data']['jobs'][0]['id']
        skipped = [j['id'] for j in _read(self.board)['data']['jobs'] if j['id'] != url]
        bd.set_fb(lambda fb: fb.update({'__auto__': auto(skip=skipped, seen={url: 7}), url: {'app': 'ready'}}),
                  live=self.board)
        bd.set_data(lambda d, fb: d.__setitem__('status', OKST), live=self.board)
        generation = {'gen': 0, 'running': False}
        builds = []
        p = ap.Pilot(self.board, lambda kind, args: (200, {}), lambda kind: {},
                     lambda: generation, lambda: True, build=lambda: builds.append(1))
        with mock.patch.object(ap, 'flow', return_value=dict(CFG, replies_at='')):
            p.step()
            self.assertEqual(builds, [1], '重啟後須補跑這張卡的新一輪驗收')
            self.assertEqual(self.fb()[url]['app'], 'ready')
            generation['gen'] = 1
            p.step()
        self.assertEqual(self.fb()[url]['app'], 'ship')

    def test_enabling_after_a_pause_skips_existing_pipeline_cards(self):
        enabled = {'value': True}
        off = {'auto_prep': False, 'auto_advance': False, 'auto_fill': False, 'replies_at': ''}
        with mock.patch.object(ap, 'flow', side_effect=lambda: CFG if enabled['value'] else off):
            p = self.pilot()
            p.step()
            url = [j['id'] for j in _read(self.board)['data']['jobs']
                   if j['id'] not in self.fb()['__auto__']['skip']
                   and not self.fb().get(j['id'], {}).get('app')][0]
            enabled['value'] = False
            p.step()
            bd.set_fb(lambda fb: fb.setdefault(url, {}).__setitem__('app', 'prep'), live=self.board)
            enabled['value'] = True
            p.step()
        self.assertIn(url, self.fb()['__auto__']['skip'])
        self.assertFalse([call for call in self.calls if call[0] == 'prep'])

    def test_rapid_disable_and_enable_captures_cards_before_timer_runs(self):
        enabled = {'value': True}
        off = {'auto_prep': False, 'auto_advance': False, 'auto_fill': False, 'replies_at': ''}
        with mock.patch.object(ap, 'flow', side_effect=lambda: CFG if enabled['value'] else off):
            p = self.pilot()
            p.step()
            url = [j['id'] for j in _read(self.board)['data']['jobs']
                   if j['id'] not in self.fb()['__auto__']['skip']
                   and not self.fb().get(j['id'], {}).get('app')][0]
            p.save_settings(lambda: enabled.__setitem__('value', False) or [])
            bd.set_fb(lambda fb: fb.setdefault(url, {}).__setitem__('app', 'prep'), live=self.board)
            p.save_settings(lambda: enabled.__setitem__('value', True) or [])
            p.step()
        self.assertIn(url, self.fb()['__auto__']['skip'])
        self.assertFalse([call for call in self.calls if call[0] == 'prep'])

    def test_enabling_only_auto_fill_skips_existing_ship_cards(self):
        enabled = {'fill': False}
        cfg = dict(CFG, auto_prep=False, auto_advance=True, auto_fill=False, replies_at='')
        with mock.patch.object(ap, 'flow', side_effect=lambda: dict(cfg, auto_fill=enabled['fill'])):
            p = self.pilot()
            p.step()
            url = [j['id'] for j in _read(self.board)['data']['jobs']
                   if j['id'] not in self.fb()['__auto__']['skip']
                   and not self.fb().get(j['id'], {}).get('app')][0]
            bd.set_fb(lambda fb: fb.setdefault(url, {}).__setitem__('app', 'ship'), live=self.board)
            p.save_settings(lambda: enabled.__setitem__('fill', True) or [])
            p.step()
        self.assertIn(url, self.fb()['__auto__']['skip'])
        self.assertFalse([call for call in self.calls if call[0] == 'apply'])

    def test_start_records_existing_cards_before_accepting_new_actions(self):
        p = self.pilot()
        p.start()
        self.assertIn('__auto__', self.fb())
        if p._timer: p._timer.cancel()

    def test_start_fails_if_existing_cards_cannot_be_inventoried(self):
        p = self.pilot()
        with mock.patch.object(ap.bd, 'parse', side_effect=ValueError('broken board')):
            with self.assertRaisesRegex(ValueError, 'broken board'):
                p.start()
        self.assertIsNone(p._timer)

    def test_existing_flow_waits_for_server_to_serve_before_dispatch(self):
        p = self.pilot()
        p.step()
        url = [j['id'] for j in _read(self.board)['data']['jobs']
               if j['id'] not in self.fb()['__auto__']['skip']
               and not self.fb().get(j['id'], {}).get('app')][0]
        bd.set_fb(lambda fb: fb.setdefault(url, {}).__setitem__('app', 'ship'), live=self.board)
        self.calls.clear()
        restarted = self.pilot()
        restarted.start()
        self.assertEqual(self.calls, [], 'HTTP 還沒開始服務時不能先派填表 Agent')
        if restarted._timer: restarted._timer.cancel()

    def test_refix_on_a_board_file(self):
        p = self.pilot()
        p.step()
        new = [j['id'] for j in _read(self.board)['data']['jobs'] if j['id'] not in self.fb()['__auto__']['skip']
               and not self.fb().get(j['id'], {}).get('app')][0]

        def put(fb):
            fb.setdefault('__ans__', []).append({'k': 'why', 'q': '為什麼', 'zh': '新的', 'v': 'new'})
            fb.setdefault(new, {}).update(app='ship', apply={'stage': 'fill', 'ok': True, 'at': 't1', 'session': 's1'},
                                          form={'f': [{'q': '為什麼', 'src': 'bank', 'k': 'why', 'refill': 1}]})
        bd.set_fb(put, live=self.board)
        p.step()
        self.assertIn(new, self.fb()['__auto__']['rf'])
        applies = lambda: [c for c in self.calls if c[0] == 'apply']   # 查回音看的是現在幾點,不管它
        self.assertFalse(applies())                    # 剛改,還在等他停手
        bd.set_fb(lambda fb: fb['__auto__']['rf'][new].__setitem__('since', '2026-01-01T00:00:00'), live=self.board)
        p.step()
        self.assertEqual(applies(), [('apply', {'stage': 'fix', 'url': new})])
        self.assertTrue([k for k in self.fb()['__auto__']['tried'] if k.startswith('fix:' + new + ':')])
        self.assertTrue(self.fb()['__auto__']['rf'][new].get('done'))

        # 重打好了(標記清掉)→ 記號跟著清掉;他之後把答案改回同一個值,還是會再重打一次
        def done(fb):
            fb[new]['form']['f'][0].pop('refill', None)
            fb[new]['apply'].update(stage='fix', at='t2')
        bd.set_fb(done, live=self.board)
        p.step()
        self.assertFalse([k for k in self.fb()['__auto__']['tried'] if k.startswith('fix:' + new + ':')])
        self.assertNotIn(new, self.fb()['__auto__'].get('rf') or {})
        bd.set_fb(lambda fb: fb[new]['form']['f'][0].__setitem__('refill', 1), live=self.board)   # 同一個值再標一次
        p.step()
        bd.set_fb(lambda fb: fb['__auto__']['rf'][new].__setitem__('since', '2026-01-01T00:00:00'), live=self.board)
        self.calls.clear()
        p.step()
        self.assertEqual(applies(), [('apply', {'stage': 'fix', 'url': new})])


class StaleAfterFillTest(unittest.TestCase):
    """agent 填好之後換了要上傳的檔:網頁上傳的是舊的,核准要擋(form_record)、收下客製版要標起來(customize)。"""

    def test_mark_stale_only_on_filled_unsent(self):
        import form_record as fr
        fb = {'a': {'apply': {'stage': 'fill', 'ok': True}}, 'b': {'apply': {'stage': 'fill'}, 'form': {'lock': 1}},
              'c': {}}
        self.assertTrue(fr.mark_stale(fb, 'a', '換了'))
        self.assertFalse(fr.mark_stale(fb, 'b', '換了'))
        self.assertFalse(fr.mark_stale(fb, 'c', '換了'))
        self.assertEqual(fb['a']['apply']['stale'], '換了')

    def test_accepting_a_custom_version_marks_the_filled_page_stale(self):
        import customize as cu
        d = tempfile.mkdtemp(prefix='stale-')
        self.addCleanup(shutil.rmtree, d, True)
        board = demo.build(os.path.join(d, 'board.html'))
        url = _read(board)['data']['jobs'][0]['id']
        bd.set_fb(lambda fb: fb.__setitem__(url, {'app': 'ship', 'apply': {'stage': 'fill', 'ok': True}}), live=board)
        cu._set_entries(board, url, {'resume:x': {'status': 'review', 'name': '履歷', 'candidate_path': 'x.pdf'}})
        self.assertNotIn('stale', json.loads(_read(board)['fb'])[url]['apply'])     # 還在等他看,不算換
        cu._set_entries(board, url, {'resume:x': {'status': 'accepted', 'name': '履歷', 'path': 'x.pdf'}})
        self.assertIn('履歷', json.loads(_read(board)['fb'])[url]['apply']['stale'])


class FlowSettingsTest(unittest.TestCase):
    def test_replies_time_format(self):
        import settings_api as sa
        self.assertTrue([b for b in sa._check({'flow': {'replies_at': '9點'}}) if '查應徵進度' in b])
        self.assertTrue([b for b in sa._check({'flow': {'replies_at': '99:99'}}) if '查應徵進度' in b])
        self.assertFalse([b for b in sa._check({'flow': {'replies_at': '08:30'}}) if '查應徵進度' in b])
        self.assertFalse([b for b in sa._check({'flow': {'replies_at': ''}}) if '查應徵進度' in b])


class PilotStopTest(unittest.TestCase):
    """伺服器關掉時:stop() 等正在跑的那一次做完才回,之後不再排下一次(副本資料夾才刪得乾淨)。"""

    def test_stop_waits_for_the_running_step_and_blocks_new_ones(self):
        import threading, time as _time
        from unittest.mock import patch
        p = ap.Pilot('unused.html', None, None, None, lambda: False)
        done, calls = [], []

        def slow_step():
            calls.append(1)
            _time.sleep(0.3)
            done.append(_time.time())

        with patch.object(p, '_step', side_effect=slow_step):
            t = threading.Thread(target=p.step)
            t.start()
            _time.sleep(0.05)                    # 讓它先進去跑
            p.stop()
            stopped_at = _time.time()
            self.assertTrue(done and done[0] <= stopped_at)   # stop 回來時那一次已經寫完
            p.kick(delay=0.01)
            p._safe_step()
            _time.sleep(0.1)
            self.assertEqual(len(calls), 1)      # 停了之後不再跑
            t.join()


if __name__ == '__main__':
    unittest.main()
