"""投遞狀態 × 事件表(docs/adr/0004):每一格在假看板資料上做一次,狀態、該清的、收進歷史的都對,不准的被擋下;
做完再復原,卡片一模一樣。預期的走向照使用者確認過的狀態圖抄(不是讀 tools/delivery_state.json 再比自己)。"""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾

import delivery_state as ds

with open(os.path.join(HERE, 'fixtures', 'delivery-cards.json'), encoding='utf-8') as f:
    FIX = json.load(f)
URL = FIX['url']

# 狀態圖和「每一種狀態:事件 → 走到哪」那張表。沒列的就是不准。
AMBIENT = {'answers_changed', 'files_changed', 'page_lost', 'leave'}      # 不在這個狀態的事,只是順便發生:不影響
SENT_ANYWHERE = {'sent_manual': 'sent', 'platform_found': 'sent'}          # 圖上沒畫、任何狀態都能走的兩條
EXPECTED = {
    'todo': {'fill_start': 'running'},
    'running': {'tab_handed': 'running', 'form_recorded': 'running', 'fill_ok': 'parked', 'fill_bad': 'stuck', 'fill_nopage': 'nopage',
                'fill_submitted': 'sent'},
    'nopage': {'fill_start': 'running'},
    'parked': {'confirm': 'confirmed', 'fix_start': 'running', 'files_changed': 'stale', 'page_lost': 'gone',
               'check_failed': 'stuck'},
    'stuck': {'fix_start': 'running', 'fill_start': 'running', 'files_changed': 'stale', 'page_lost': 'gone',
              'check_failed': 'stuck'},
    'stale': {'fill_start': 'running', 'page_lost': 'gone'},
    'gone': {'fill_start': 'running'},
    'confirmed': {'submit_start': 'sending', 'unconfirm': 'parked', 'answers_changed': 'parked', 'files_changed': 'stale',
                  'page_lost': 'gone', 'leave': 'parked', 'check_failed': 'stuck'},
    'sending': {'submit_ok': 'sent', 'submit_unsure': 'unsure', 'submit_not_started': 'confirmed',
                'submit_not_sent': 'stuck'},      # 按了送出,程式讀那一頁還停在申請表:沒送出,頁還在、寫原因(#316)
    'unsure': {'not_sent': 'parked', 'actually_sent': 'sent'},       # 確認沒送出:頁在 → 停著等你,重新確認
    'sent': {'undo_sent': 'todo', 'retry': 'todo'},
}
# 不准的順便事件(狀態表修正 11、14):agent 正在做的時候不能離開流程;送出結果不明時先確認到底送出沒有
# (送出結果不明時改答案:看板把用到它的答案輸入框停用;答案庫本身的寫入不擋,狀態不變)
NOT_AMBIENT = {'running': {'leave'}, 'sending': {'files_changed', 'leave'}, 'unsure': {'leave'}}
NO_MANUAL_SENT = {'running', 'sending'}         # agent 正在做,等它做完才能標外部送出


def expected(state, event):
    exp = dict(EXPECTED[state])
    if state != 'sent':
        exp.update(SENT_ANYWHERE)
        if state in NO_MANUAL_SENT:
            exp.pop('sent_manual')
    else:
        exp['platform_found'] = 'sent'
    for e in AMBIENT - NOT_AMBIENT.get(state, set()):
        exp.setdefault(e, state)
    return exp.get(event)


def board(state):
    return {URL: copy.deepcopy(FIX['cards'][state]), '__ans__': copy.deepcopy(FIX['ans'])}


def fire(fb, event, **extra):
    return ds.fire(fb, URL, event, **dict(copy.deepcopy(FIX['data'][event]), **extra))


class StateTable(unittest.TestCase):
    def test_every_cell(self):
        """11 種狀態 × 每一種事件:准的走到圖上那一格,不准的擋下、卡片沒被動到;做完復原要一模一樣。"""
        for state in FIX['cards']:
            for event in FIX['data']:
                with self.subTest(state=state, event=event):
                    fb = board(state)
                    before = copy.deepcopy(fb)
                    to = expected(state, event)
                    if to is None:
                        with self.assertRaises(ds.Forbidden):
                            fire(fb, event)
                        self.assertEqual(fb, before)
                        continue
                    prev = fire(fb, event)
                    self.assertEqual(ds.state(fb[URL]), to)
                    fb[URL] = prev                              # 復原:改動前那一張整張放回
                    self.assertEqual(fb, before)


class PageLeftWaiting(unittest.TestCase):
    """停著的頁只看投遞狀態;卡片移除了、退出可以投了、公司被封鎖了的不算(以前 protected_tabs、
    autopilot.held、看板 held 各寫一份:退回、移除、送出的卡留下的舊分頁編號被當成在等他,Chrome 永遠關不掉)。"""
    JOB = {'id': URL, 'target': '**Role · Acme**'}

    def held(self, state, **kw):
        fb = board(state)
        fb[URL].update(kw)
        return ds.held(fb, URL, self.JOB)

    def test_rows(self):
        self.assertTrue(self.held('parked'))
        self.assertTrue(self.held('unsure'))
        self.assertFalse(self.held('parked', rm=1))                 # 移除了
        self.assertFalse(self.held('parked', app='ready'))          # 退出可以投了
        self.assertFalse(self.held('gone'))                         # 頁面不見了
        self.assertFalse(self.held('sent'))                         # 已送出(那頁是「已收到申請」)
        fb = board('parked')
        fb['__block__'] = ['Acme']
        self.assertFalse(ds.held(fb, URL, self.JOB))                 # 公司被封鎖了

    def test_page_up_only_while_left_waiting_with_a_tab(self):
        self.assertTrue(ds.page_up(FIX['cards']['stuck']))
        self.assertFalse(ds.page_up(FIX['cards']['gone']))
        self.assertFalse(ds.page_up(FIX['cards']['sent']))
        self.assertFalse(ds.page_up(FIX['cards']['running']))     # 正在跑:那一輪自己寫


class Checklist(unittest.TestCase):
    """能不能確認送出:狀態允許 + 檢查清單(看板 approvalProblem / approveBlocker 由看板檢查跑同一張)。"""
    PASSED = {'schema_version': 2, 'checked_links': True, 'issues': []}

    def test_cases(self):
        import form_record as fr
        for c in FIX['checklist']:
            with self.subTest(c['name']):
                fb = copy.deepcopy(c['fb'])
                status = c.get('status', self.PASSED)
                self.assertEqual(fr.approval_problem(fb, c['url'], status), c['problem'])
                self.assertEqual(fr.confirm_problem(fb, c['url'], status), c['confirm'])
                self.assertEqual(fb, c['fb'])                     # 試算確認不會留下東西


class CellEffects(unittest.TestCase):
    def test_confirm_stores_what_was_confirmed(self):
        fb = board('parked')
        fire(fb, 'confirm')
        self.assertEqual(fb[URL]['approve'], FIX['data']['confirm']['approve'])

    def test_leaving_voids_the_confirmation(self):
        """額外抓到 2:確認之後移除、封鎖、出錯了、👎,確認作廢(之後放回來,舊的確認不會又變有效)。"""
        fb = board('confirmed')
        fire(fb, 'leave')
        self.assertEqual(ds.state(fb[URL]), 'parked')
        self.assertNotIn('approve', fb[URL])

    def test_marking_it_broken_voids_the_confirmation(self):
        """額外抓到 2:程式判定職缺下架(標出錯了)也一樣作廢確認;放回來不會又變有效。"""
        import cut_tailor
        fb = board('confirmed')
        cut_tailor._techerr(fb, URL)
        self.assertEqual((ds.state(fb[URL]), fb[URL]['s'], fb[URL].get('app')), ('parked', 'techerr', None))
        self.assertNotIn('approve', fb[URL])

    def test_changing_files_after_confirm_voids_it(self):
        fb = board('confirmed')
        fire(fb, 'files_changed')
        self.assertEqual(ds.state(fb[URL]), 'stale')
        self.assertNotIn('approve', fb[URL])
        self.assertEqual(fb[URL]['apply']['stale'], '語言換成「English」')

    def test_page_lost_clears_the_tab_and_the_confirmation(self):
        fb = board('confirmed')
        fire(fb, 'page_lost')
        self.assertEqual(fb[URL]['apply']['tab_id'], '')
        self.assertNotIn('approve', fb[URL])

    def test_refill_starts_clean(self):
        fb = board('stale')
        fire(fb, 'fill_start')
        a = fb[URL]['apply']
        self.assertEqual(ds.state(fb[URL]), 'running')
        self.assertNotIn('stale', a)
        self.assertNotIn('session', a)
        self.assertEqual(a['issues'], ['這一輪還沒跑完'])

    def test_changing_files_while_filling_ends_as_stale(self):
        """正在填的時候換了履歷:跑完直接到「上傳的是舊檔」。"""
        for done in ('fill_ok', 'fill_bad'):
            with self.subTest(done=done):
                fb = board('running')
                fire(fb, 'files_changed')
                self.assertEqual(ds.state(fb[URL]), 'running')
                fire(fb, done)
                self.assertEqual(ds.state(fb[URL]), 'stale')
                self.assertEqual(fb[URL]['apply']['stale'], '語言換成「English」')
                self.assertEqual(fb[URL]['apply']['tab_id'], '9')

    def test_three_ways_to_sent_are_one_move(self):
        """額外抓到 3:agent 送出、你在外部送出、平台對帳,走同一個「已送出」移動,只差證據來源。"""
        out = {}
        for state, event in (('sending', 'submit_ok'), ('parked', 'sent_manual'), ('parked', 'platform_found')):
            fb = board(state)
            fire(fb, event)
            m = fb[URL]
            out[event] = m
            self.assertEqual((m['app'], ds.state(m), m['form'].get('lock')), ('sent', 'sent', 1))
            self.assertFalse(any(x.get('refill') for x in m['form']['f']))
        self.assertEqual([out[e]['sent_by'] for e in ('submit_ok', 'sent_manual', 'platform_found')],
                         ['agent', 'manual', 'platform'])
        self.assertEqual(out['submit_ok']['apply']['sent'], FIX['data']['submit_ok']['evidence'])
        self.assertEqual(out['sent_manual']['history'][0]['apply']['tab_id'], '7')   # 那一頁的紀錄收進歷史

    def test_platform_date_wins(self):
        """投遞日以平台紀錄優先:agent 已經記了送出日,平台對帳找到時換成平台上的日期。"""
        fb = board('sent')
        fire(fb, 'platform_found')
        self.assertEqual(fb[URL]['sent_at'], '2026-01-30')
        fb = board('sending')
        fire(fb, 'submit_ok')
        self.assertEqual(fb[URL]['sent_at'], '2026-02-02')

    def test_platform_found_while_filling(self):
        """額外抓到 6:填表中平台對帳找到,已送出優先,這一輪結果收進歷史。"""
        fb = board('running')
        fire(fb, 'platform_found')
        m = fb[URL]
        self.assertEqual(ds.state(m), 'sent')
        self.assertEqual(m['history'][-1]['apply']['issues'], ['這一輪還沒跑完'])
        self.assertNotIn('apply', m)
        with self.assertRaises(ds.Forbidden):             # 那一輪跑完回來:卡已經送出了,不准再改
            fire(fb, 'fill_ok')

    def test_only_confirmed_reaches_sending(self):
        """額外抓到 4:只有你已確認能走到正在送出;卡一離開就不在這個狀態。"""
        can = [s for s in FIX['cards'] if expected(s, 'submit_start')]
        self.assertEqual(can, ['confirmed'])
        fb = board('confirmed')
        fire(fb, 'leave')
        with self.assertRaises(ds.Forbidden):
            fire(fb, 'submit_start')

    def test_not_sent_goes_back_through_the_checklist(self):
        """確認沒送出,可以重送:不直接回你已確認(修正 3)。頁在 → 停著等你、要重新確認;證據收進歷史。"""
        fb = board('unsure')
        fire(fb, 'not_sent')
        m = fb[URL]
        self.assertEqual(ds.state(m), 'parked')
        self.assertNotIn('approve', m)
        self.assertEqual(m['history'][-1]['apply.submit_fail']['problems'], ['沒看到成功頁面'])
        self.assertNotIn('submit_fail', m['apply'])

    def test_not_sent_after_the_page_was_lost(self):
        fb = board('unsure')
        fire(fb, 'page_lost')
        self.assertEqual(ds.state(fb[URL]), 'unsure')      # Chrome 關過:仍是送出結果不明,只是頁不在了
        self.assertFalse(ds.held(fb, URL))                  # 頁不在:不佔一頁
        fire(fb, 'not_sent')
        self.assertEqual(ds.state(fb[URL]), 'gone')
        self.assertNotIn('approve', fb[URL])

    def test_files_changed_while_unsure_is_remembered(self):
        """送出結果不明時換了檔:記下來、狀態不變;確認沒送出 → 上傳的是舊檔(修正 2)。"""
        fb = board('unsure')
        fire(fb, 'files_changed')
        self.assertEqual(ds.state(fb[URL]), 'unsure')
        fire(fb, 'not_sent')
        self.assertEqual(ds.state(fb[URL]), 'stale')

    def test_leaving_and_coming_back(self):
        """離開流程:確認作廢、不算停著的頁;放回來回原狀態,頁在這段期間不見了 → 頁面不見了(修正 1)。"""
        fb = board('confirmed')
        fb[URL]['rm'] = 1
        fire(fb, 'leave')
        self.assertFalse(ds.held(fb, URL))
        del fb[URL]['rm']
        self.assertEqual(ds.state(fb[URL]), 'parked')
        self.assertTrue(ds.held(fb, URL))
        fb[URL]['rm'] = 1
        fire(fb, 'page_lost')                                # 移除的期間 Chrome 重開過
        del fb[URL]['rm']
        self.assertEqual(ds.state(fb[URL]), 'gone')

    def test_a_stopped_round_without_a_handed_page_did_not_open_one(self):
        """重填開始就把上一輪的分頁收掉:這一輪沒交出分頁就停掉 → 沒填成,舊分頁不再算停著的頁(修正 13)。"""
        fb = board('stuck')
        fire(fb, 'fill_start')
        self.assertNotIn('tab_id', fb[URL]['apply'])
        self.assertFalse(ds.page_up(fb[URL]))

    def test_late_results_go_to_history(self):
        """晚到的結果:卡已經是已送出(對帳或外部送出先到),agent 的結果才回來 → 收進歷史,狀態不變(修正 8)。"""
        fb = board('running')
        fire(fb, 'platform_found')
        self.assertFalse(ds.try_fire(fb, URL, 'fill_ok', **FIX['data']['fill_ok']))
        m = fb[URL]
        self.assertEqual(ds.state(m), 'sent')
        self.assertEqual((m['history'][-1]['event'], m['history'][-1]['apply']['tab_id']), ('fill_ok', '9'))

    def test_backing_out_a_platform_record_remembers_it(self):
        """對帳來的已送出卡被他退回:記下這筆對帳紀錄不算,之後同一筆不再拉回(修正 22)。"""
        fb = board('parked')
        fb[URL]['rm'] = 1                                    # 移除的卡:平台上真的應徵了,照樣記成已送出
        fire(fb, 'platform_found')
        self.assertNotIn('rm', fb[URL])
        fire(fb, 'back')
        self.assertEqual(fb[URL]['sync_no'], [FIX['data']['platform_found']['rec']])
        self.assertNotIn('sent_rec', fb[URL])

    def test_actually_sent_keeps_agent_evidence(self):
        fb = board('unsure')
        fire(fb, 'actually_sent')
        m = fb[URL]
        self.assertEqual((ds.state(m), m['sent_by']), ('sent', 'agent'))
        self.assertEqual(m['apply']['sent']['problems'], ['沒看到成功頁面'])

    def test_undo_sent_moves_evidence_to_history(self):
        fb = board('sent')
        fire(fb, 'undo_sent')
        m = fb[URL]
        self.assertEqual((ds.state(m), m['app']), ('todo', 'ship'))
        for k in ('apply', 'approve', 'sent_at', 'sent_v', 'sent_by', 'oc', 'ev'):
            self.assertNotIn(k, m)
        self.assertNotIn('lock', m['form'])
        h = m['history'][-1]
        self.assertEqual((h['event'], h['at'], h['apply']['sent']['text']), ('undo_sent', '2026-02-04T00:00:00', '已收到申請'))

    def test_back_only_for_cards_you_marked_sent(self):
        fb = board('sent')
        fb[URL]['sent_by'] = 'manual'
        fire(fb, 'back')
        m = fb[URL]
        self.assertEqual((ds.state(m), m['app']), ('todo', 'ship'))
        self.assertNotIn('sent_at', m)
        self.assertNotIn('lock', m['form'])

    def test_retry_keeps_last_round(self):
        fb = board('sent')
        fire(fb, 'retry')
        m = fb[URL]
        self.assertEqual((ds.state(m), m['app']), ('todo', 'ship'))
        self.assertEqual(m['tries'][-1]['sent_at'], '2026-01-02')
        self.assertNotIn('form', m)

    def test_undo_sent_manual_puts_refill_back(self):
        """#333:「我已在外部送出」鎖表單時清掉雇主網頁待重打;看板按復原,重打記號跟著鎖一起回到按之前。"""
        fb = board('parked')
        before = copy.deepcopy(fb[URL])
        fire(fb, 'sent_manual')
        self.assertFalse(any(x.get('refill') for x in fb[URL]['form']['f']))
        ds.undo(fb, URL, ds.part(before), ds.part(fb[URL]))
        self.assertEqual(fb[URL], before)


if __name__ == '__main__':
    unittest.main()


class BrokenCardsAreCaught(unittest.TestCase):
    """狀態表不准的組合,檢查(problems)每一種都抓得到;以前只測過「對的卡沒有問題」,抓的那一邊一條都沒走過。"""

    def card(self, state, change):
        m = copy.deepcopy(FIX['cards'][state])
        change(m)
        return m

    def test_each_forbidden_combination_is_named(self):
        cases = [
            ('狀態不在表上', 'parked', lambda m: m.__setitem__('ds', 'flying'), '不在表上'),
            ('在已送出欄卻不是已送出', 'parked', lambda m: m.__setitem__('app', 'sent'), '欄卻是'),
            ('表單鎖著卻不是已送出', 'parked', lambda m: m['form'].__setitem__('lock', 1), '表單鎖著'),
            ('有送出來源卻不是已送出', 'parked', lambda m: m.__setitem__('sent_by', 'agent'), '送出來源'),
            ('停著等你卻帶著確認', 'parked', lambda m: m.__setitem__('approve', {'at': 'x'}), '卻帶著確認'),
            ('你已確認卻沒有確認紀錄', 'confirmed', lambda m: m.pop('approve'), '沒有確認紀錄'),
            ('有送出證據卻不是已送出', 'parked', lambda m: m['apply'].__setitem__('sent', {'url': 'x'}), '送出證據'),
            ('送出結果不明卻沒有紀錄', 'unsure', lambda m: m['apply'].pop('submit_fail'), '送出結果不明'),
            ('停著等你卻記著換過檔', 'parked', lambda m: m['apply'].__setitem__('stale', '換了'), '記著換過檔'),
            ('舊檔卻沒記換了什麼', 'stale', lambda m: m['apply'].pop('stale'), '沒記換了什麼'),
        ]
        for why, state, change, want in cases:
            with self.subTest(why):
                got = ds.problems(self.card(state, change))
                self.assertTrue(any(want in p for p in got), got)

    def test_not_a_card_has_no_problems(self):
        self.assertEqual(ds.problems('不是一張卡'), [])

    def test_unknown_event_is_refused(self):
        fb = {URL: copy.deepcopy(FIX['cards']['parked'])}
        with self.assertRaises(ValueError):
            ds.fire(fb, URL, 'no_such_event')
        self.assertEqual(fb[URL], FIX['cards']['parked'])

    def test_a_late_result_for_a_card_not_sent_changes_nothing(self):
        # 晚到的結果只有卡已經是已送出才收進歷史;其他狀態不准就是不動
        fb = {URL: copy.deepcopy(FIX['cards']['confirmed'])}
        self.assertFalse(ds.try_fire(fb, URL, 'fill_ok', **copy.deepcopy(FIX['data'].get('fill_ok') or {})))
        self.assertEqual(fb[URL], FIX['cards']['confirmed'])

    def test_event_on_a_card_whose_apply_is_not_a_record(self):
        # 舊資料 apply 存成別的東西:填表結果回來照樣寫得進去(換成一份新的紀錄),不會炸
        m = copy.deepcopy(FIX['cards']['running'])
        m['apply'] = 'broken'
        fb = {URL: m}
        ds.fire(fb, URL, 'fill_submitted', **copy.deepcopy(FIX['data'].get('fill_submitted') or {}))
        self.assertEqual(ds.state(fb[URL]), 'sent')
        self.assertIsInstance(fb[URL]['apply'], dict)


    def test_event_without_its_data_keeps_the_record(self):
        # 交接頁面的事件沒帶這一輪的紀錄:原本的填表紀錄照舊,不被清成空的
        fb = {URL: copy.deepcopy(FIX['cards']['running'])}
        ds.fire(fb, URL, 'tab_handed')
        self.assertEqual(fb[URL]['apply'], FIX['cards']['running']['apply'])


class BoardSaveCannotForge(unittest.TestCase):
    """看板存檔送來的卡:狀態表管的欄位照現在的留著。"""

    def test_board_cannot_move_a_card_into_sent(self):
        out = ds.merge_saved({'s': 'like'}, {'s': 'like', 'app': 'sent'})
        self.assertNotIn('app', out)

    def test_a_refused_click_on_a_whole_card_clear_is_left_alone(self):
        self.assertIsNone(ds.keep_flow({'app': 'ship'}, None))
