"""一張卡的下一步(#340、docs/adr/0005):後台算一份,看板照畫,事件照它收或擋。

測的接縫(seam)只有三個,都不碰內部怎麼算:
- 狀態表上的分類(tools/delivery_state.json 每種狀態都要標);
- 下一步的 interface:給一份看板(卡片、常用答案、驗收結果),拿回每張卡的下一步(next_step.of);
- 後台收事件和存檔(/api/save、/api/next、頁面):收不收照下一步,擋的原因就是下一步給的那一句。"""
import copy
import datetime
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾

import delivery_state as ds  # noqa: E402

with open(os.path.join(HERE, 'fixtures', 'delivery-cards.json'), encoding='utf-8') as f:
    FIX = json.load(f)
URL = FIX['url']


class Classification(unittest.TestCase):
    def test_every_state_marks_every_classification(self):
        """加一種投遞狀態,沒在表上標算不算停著的頁、算不算忙、能不能換檔、能不能離開流程、算不算填好、
        agent 是不是正在做,就過不了(#340 user story 32)。"""
        for name, x in ds.TABLE['states'].items():
            for flag in ('held', 'busy', 'swap', 'leave', 'filled', 'working'):
                with self.subTest(state=name, flag=flag):
                    self.assertIn(flag, x)

    def test_filling_sending_and_unsure_cannot_swap_or_leave(self):
        """正在填、正在送出、送出結果不明:不能換檔(含上傳客製版)、不能離開流程(#340)。"""
        states = ds.TABLE['states']
        self.assertEqual({s for s, x in states.items() if not x['swap']}, {'running', 'sending', 'unsure'})
        self.assertEqual({s for s, x in states.items() if not x['leave']}, {'running', 'sending', 'unsure'})

    def test_a_card_that_cannot_swap_or_leave_has_a_reason_to_show(self):
        for name, x in ds.TABLE['states'].items():
            if not (x['swap'] and x['leave']):
                with self.subTest(state=name):
                    self.assertTrue(x['busy'])

    def test_a_card_can_leave_exactly_when_the_table_has_a_leave_cell(self):
        """「能不能離開流程」的標記和表上有沒有 leave 那一格是同一件事,不是第二份真相。"""
        for name, x in ds.TABLE['states'].items():
            with self.subTest(state=name):
                self.assertEqual('leave' in ds.TABLE['cells'][name], x['leave'])

    def test_filled_pages_and_the_agent_at_work(self):
        """算填好:頁上有他要看的東西;agent 正在做:正在填或改、正在送出(送出結果不明是等你查,不是 agent 在做)。"""
        states = ds.TABLE['states']
        self.assertEqual({s for s, x in states.items() if x['filled']}, {'parked', 'confirmed', 'stale', 'sending', 'unsure'})
        self.assertEqual({s for s, x in states.items() if x['working']}, {'running', 'sending'})


PASSED = {'schema_version': 2, 'checked_links': True, 'issues': []}     # 投遞前驗收跑完、沒擋
JOB = {'id': URL, 'target': 'Role · Acme'}


def ready_to_confirm():
    """停著等你、答案都好了、網頁也照現在的答案填的:可以確認送出。"""
    m = copy.deepcopy(FIX['cards']['parked'])
    for x in m['form']['f']:
        x.pop('refill', None)
    return {URL: m, '__ans__': copy.deepcopy(FIX['ans'])}


def step(fb, status=PASSED):
    import next_step
    return next_step.of(fb, [JOB], status)[URL]


class ConfirmButton(unittest.TestCase):
    """卡上的「✅ 確認送出」能不能按、為什麼不能(#341、#339 第一條)。"""

    def test_a_filled_card_with_every_answer_ready_can_be_confirmed(self):
        self.assertIsNone(step(ready_to_confirm())['confirm'])

    def test_an_answer_still_waiting_for_you_blocks_it(self):
        fb = ready_to_confirm()
        fb['__ans__'][0]['inf'] = 1                    # agent 推論的,你還沒確認
        self.assertEqual(step(fb)['confirm'], '還有答案等你確認或填寫')

    def test_a_card_the_agent_has_not_filled_cannot_be_confirmed(self):
        fb = ready_to_confirm()
        fb[URL].pop('ds')
        self.assertEqual(step(fb)['confirm'], 'Agent 還沒填這張,先讓它填好、你看過頁面再確認送出')


FLOW = {'auto_prep': True, 'auto_advance': True, 'auto_fill': True, 'fill_max': 0, 'replies_at': ''}
NOW = datetime.datetime(2026, 1, 5, 10, 0)


def flow_board(**cards):
    """自動流程開著的看板:卡號 → 投遞狀態(照 delivery-cards.json 的那張卡,網頁照現在的答案填好);
    'prep'、'ready' 是還在準備區、待你決定的卡。回 (標記, 看板上的卡)。"""
    fb = {'__auto__': {'since': '2026-01-01T00:00:00', 'skip': [], 'tried': [], 'seen': {}, 'rf': {}},
          '__ans__': copy.deepcopy(FIX['ans'])}
    for i, s in cards.items():
        if s in ('prep', 'ready'):
            fb[i] = {'app': s}
            continue
        fb[i] = copy.deepcopy(FIX['cards'][s])
        for x in (fb[i].get('form') or {}).get('f', []):
            x.pop('refill', None)
    return fb, [{'id': i, 'target': 'Role ' + i + ' · Co ' + i} for i in cards]


def steps(fb, jobs, flow=FLOW, now=NOW, real=True, status=PASSED, gen=0, building=False):
    import next_step
    return next_step.of(fb, jobs, status, flow=flow, now=now, real=real, gen=gen, building=building)


def plan(fb, jobs, flow=FLOW, now=NOW, real=True, running=None, gen=0, building=False):
    import autopilot
    return autopilot.plan({'jobs': jobs, 'status': PASSED}, copy.deepcopy(fb), verified_gen=gen, build_running=building,
                          running=running or {}, now=now, cfg=flow, real=real)


def does(fb, jobs, i, **kw):
    """下一步說 agent 會替這張做什麼(沒有 = None)。"""
    return (steps(fb, jobs, **kw)[i]['auto'] or {}).get('do')


class HeldPages(unittest.TestCase):
    """停著的頁上限只有一種算法:看板說會不會自動填新卡,就是自動流程填不填(#339、#340 user story 15、16)。"""

    def test_a_new_card_is_filled_by_the_agent_and_the_flow_picks_it(self):
        fb, jobs = flow_board(new='todo')
        self.assertEqual((does(fb, jobs, 'new'), plan(fb, jobs)['fill']), ('fill', 'new'))

    def test_when_the_held_pages_reach_the_cap_no_new_card_is_filled(self):
        fb, jobs = flow_board(a='parked', b='parked', new='todo')
        cap = dict(FLOW, fill_max=2)
        self.assertEqual((does(fb, jobs, 'new', flow=cap), plan(fb, jobs, flow=cap)['fill']), (None, None))

    def test_the_cap_written_as_a_decimal_or_left_blank_is_read_one_way(self):
        """設定檔手改成小數或空白:看板和自動流程同一個解讀(小數無條件捨去、空的或看不懂照預設 5、負的當不限)。"""
        for cap, held, want in (('2.7', 2, None), (2.7, 1, 'fill'), ('', 4, 'fill'), ('', 5, None), (None, 5, None),
                                ('很多張', 5, None), (' 3 ', 3, None), ('-1', 6, 'fill'), (0, 6, 'fill')):
            with self.subTest(cap=cap, held=held):
                fb, jobs = flow_board(**{'h%d' % n: 'parked' for n in range(held)}, new='todo')
                flow = dict(FLOW, fill_max=cap)
                self.assertEqual(does(fb, jobs, 'new', flow=flow), want)
                self.assertEqual(plan(fb, jobs, flow=flow)['fill'], 'new' if want else None)


def later(seconds):
    return NOW + datetime.timedelta(seconds=seconds)


def settle(fb, pl):
    """自動流程這一輪記下的(看到的新改動、派出去的記號)寫回看板,像 Pilot 做的那樣。"""
    a = fb['__auto__']
    a['rf'] = dict(a.get('rf') or {}, **pl['rf'])
    a['tried'] = list(a['tried']) + pl['tried']


class Refix(unittest.TestCase):
    """答案改過、停著的頁要照新答案重打:卡上寫的重打、重翻範圍就是自動流程真的會做的(#339 第三條、user story 12、14)。"""

    def test_a_card_with_only_an_answer_to_retranslate_is_retyped_automatically(self):
        fb, jobs = flow_board(c='parked')
        fb['__ans__'][0]['tr'] = 1                     # 他改了中文,英文還沒照著重翻;表單上沒有要重打的欄
        nx = steps(fb, jobs)['c']['auto']
        self.assertEqual((nx['do'], nx['line']), ('fix', '答案改過,你停手一分鐘後 Agent 會自動照新答案重翻 1 條'))
        pl = plan(fb, jobs)
        self.assertEqual((pl['fix'], pl['wait']), (None, 60))     # 剛改:等他停手
        settle(fb, pl)
        self.assertEqual(plan(fb, jobs, now=later(61))['fix'], 'c')

    def test_the_line_counts_the_fields_to_retype_and_the_answers_to_retranslate(self):
        fb, jobs = flow_board(c='parked')
        fb['c']['form']['f'].append({'q': 'Where?', 'src': 'bank', 'k': 'k2', 'refill': 1})
        fb['__ans__'].append({'k': 'k2', 'q': 'Where?', 'v': 'Taipei', 'at': '2026-01-01'})
        fb['c']['form']['f'][0]['refill'] = 1
        fb['__ans__'][0]['tr'] = 1
        self.assertEqual(steps(fb, jobs)['c']['auto']['line'],
                         '答案改過,你停手一分鐘後 Agent 會自動照新答案重翻 1 條、重打 2 欄')

    def test_on_a_copy_of_the_board_the_wait_is_short(self):
        fb, jobs = flow_board(c='parked')
        fb['c']['form']['f'][0]['refill'] = 1
        self.assertEqual(steps(fb, jobs, real=False)['c']['auto']['line'],
                         '答案改過,你停手 3 秒後 Agent 會自動照新答案重打 1 欄')


class AgentStops(unittest.TestCase):
    """自動流程本來會接、這次不接的:卡上改成要你處理並講原因,不會一直空等(#339 第二條、user story 11、13)。"""

    def refill(self):
        fb, jobs = flow_board(c='parked')
        fb['c']['form']['f'][0]['refill'] = 1
        fb['__auto__']['rf'] = {}
        return fb, jobs

    def test_an_answer_missing_evidence_is_reported_as_missing_evidence_not_retyped(self):
        import form_record as fr
        fb, jobs = self.refill()
        fb['__ans__'][0].update(inf='2026-01-02', noev='沒有程式自己截的那一頁')
        nx = steps(fb, jobs)['c']
        self.assertEqual((nx['auto'], nx['stop'], nx['ask']), (None, fr.NO_EVIDENCE, []))
        settle(fb, plan(fb, jobs))
        self.assertIsNone(plan(fb, jobs, now=later(999))['fix'])

    def test_an_answer_waiting_for_you_is_listed_and_the_retype_waits_for_it(self):
        fb, jobs = self.refill()
        fb['__ans__'][0]['inf'] = '2026-01-02'
        nx = steps(fb, jobs)['c']
        self.assertEqual((nx['auto'], nx['stop'], nx['ask']), (None, None, ['k1']))

    def test_a_retype_that_left_the_mark_is_not_retried_and_says_why(self):
        import next_step
        fb, jobs = self.refill()
        fb['__auto__']['tried'] = ['fix:c:' + next_step.fix_sig(fb, 'c')]
        nx = steps(fb, jobs)['c']
        self.assertEqual((nx['auto'], nx['stop']),
                         (None, 'Agent 已經自動照新答案重打 1 欄過一次,網頁上還是沒改好,不會再自動重試;看過頁面再叫它改'))
        self.assertIsNone(plan(fb, jobs, now=later(999))['fix'])

    def test_changing_the_answer_again_hands_it_back_to_the_agent(self):
        import next_step
        fb, jobs = self.refill()
        fb['__auto__']['tried'] = ['fix:c:' + next_step.fix_sig(fb, 'c')]
        fb['__ans__'][0]['v'] = 'Because, again.'
        nx = steps(fb, jobs)['c']
        self.assertEqual(((nx['auto'] or {}).get('do'), nx['stop']), ('fix', None))

    def test_with_the_automatic_flow_off_nothing_is_promised_or_stopped(self):
        fb, jobs = self.refill()
        nx = steps(fb, jobs, flow=dict(FLOW, auto_fill=False))['c']
        self.assertEqual((nx['auto'], nx['stop']), (None, None))


def card_view(state, status=PASSED, flow=None, **change):
    """一張「可以投了」的卡在這個投遞狀態(網頁照現在的答案填好),卡上畫什麼(next_step 的 view)。"""
    fb, jobs = flow_board(**{URL: state})
    fb[URL].update(change)
    return steps(fb, jobs, flow=flow or {}, status=status)[URL]['view']


def shown(v):
    """卡上看得到的:那一行的字、按鈕(能不能按)、擋住的原因、要你處理的、填表進度那一格。"""
    return {'line': [t for _, t in v['line']],
            'buttons': [b['label'] + (' ⛔' + b['off'] if b.get('off') else '') for b in v['buttons']],
            'why': v['why'], 'todo': v['todo'], 'fill': v['fill']}


class CardView(unittest.TestCase):
    """卡上幫你填表那一塊畫什麼,全部後台算(#343、user story 4、33):看板照它畫,自己不比投遞狀態名。
    每種投遞狀態一例,加上檢查清單擋住、agent 會接手的幾種。"""

    def test_a_card_not_filled_yet(self):
        why = 'Agent 還沒填這張,先讓它填好、你看過頁面再確認送出'
        self.assertEqual(shown(card_view('todo')), {
            'line': ['🤖 Agent 還沒填這張'], 'buttons': ['▶ 讓 Agent 填這張', '✅ 確認送出 ⛔' + why], 'why': '⛔ 還不能確認送出:' + why,
            'todo': '', 'fill': {'kind': 'todo'}})

    def test_a_card_the_flow_will_fill_says_so_and_asks_nothing(self):
        self.assertEqual(shown(card_view('todo', flow=FLOW)), {
            'line': ['🤖 Agent 還沒填這張', '⏳ 排隊中:Agent 會自動填這張,填好停在送出前'], 'buttons': ['▶ 現在就填'],
            'why': '', 'todo': '', 'fill': {'kind': 'todo'}})

    def test_a_filled_card_ready_to_confirm(self):
        v = card_view('parked')
        self.assertEqual(shown(v), {
            'line': ['🤖 Agent 1/1 填好了,停在送出前'], 'buttons': ['✅ 確認送出', '✏️ 要 Agent 改'], 'why': '',
            'todo': '填好了,看過頁面就能確認送出', 'fill': {'kind': 'ok', 'text': '✅ 填好了,等你確認送出'}})
        self.assertTrue(v['eye'])

    def test_a_filled_card_with_an_answer_waiting_for_you(self):
        fb, jobs = flow_board(**{URL: 'parked'})
        fb['__ans__'][0]['inf'] = '2026-01-02'
        v = steps(fb, jobs, flow={})[URL]['view']
        self.assertEqual(shown(v), {
            'line': ['🤖 Agent 1/1 填好了,停在送出前'], 'buttons': ['✏️ 要 Agent 改'], 'why': '',
            'todo': '1 條答案等你確認', 'fill': {'kind': 'wait', 'text': '⚠ 還有答案等你確認或填寫'}})

    def test_a_filled_card_before_the_check_has_run_cannot_be_confirmed(self):
        why = '投遞前驗收還沒跑完(背景會自己跑,好了這裡會自己更新)'
        self.assertEqual(shown(card_view('parked', status=None))['buttons'], ['✏️ 要 Agent 改', '✅ 確認送出 ⛔' + why])
        self.assertEqual(shown(card_view('parked', status=None))['why'], '⛔ 還不能確認送出:' + why)

    def test_a_confirmed_card_is_sent_with_one_button(self):
        v = card_view('confirmed')
        self.assertEqual(shown(v), {
            'line': ['✅ 你已確認,等送出'], 'buttons': ['▶ 送出', '取消確認'],
            'why': '', 'todo': '你確認了,按「▶ 送出」', 'fill': {'kind': 'ok', 'text': '✅ 你已確認,等送出'}})
        self.assertTrue(v['soon'])

    def test_a_card_the_agent_is_filling_shows_only_that(self):
        v = card_view('running')
        self.assertEqual(shown(v), {'line': ['⏳ Agent 正在填…'], 'buttons': [], 'why': '', 'todo': '', 'fill': {'kind': 'run'}})
        self.assertEqual((v['busy'], v['stage']), (True, 'fill'))

    def test_a_card_sent_without_seeing_the_success_page(self):
        self.assertEqual(shown(card_view('unsure')), {
            'line': ['📤 Agent 1/1 按了送出,沒看到已收到申請頁'], 'buttons': ['確認沒送出,可以重送', '其實送出了'],
            'why': '❌ 送出沒確認成功(Agent 按過送出,可能其實送出去了):沒看到成功頁面',
            'todo': '送出沒確認成功,先去確認到底送出沒有',
            'fill': {'kind': 'unsure', 'text': '❓ 送出結果不明,先去信箱或平台的應徵紀錄查'}})

    def test_a_card_whose_page_is_gone_is_refilled(self):
        self.assertEqual(shown(card_view('gone')), {
            'line': ['📄 ' + ds.GONE], 'buttons': ['▶ 讓 Agent 重填這張', '✅ 確認送出 ⛔' + ds.GONE], 'why': '', 'todo': ds.GONE,
            'fill': {'kind': 'gone', 'text': '📄 填好的那一頁不見了,要重填'}})

    def test_a_card_stuck_says_why(self):
        self.assertEqual(shown(card_view('stuck')), {
            'line': ['🤖 Agent 修改卡住:必填欄位沒填'], 'buttons': ['✏️ 要 Agent 改', '✅ 確認送出 ⛔必填欄位沒填'],
            'why': '⛔ 還不能確認送出:必填欄位沒填',
            'todo': 'Agent 卡住:必填欄位沒填', 'fill': {'kind': 'bad', 'text': '❌ 必填欄位沒填'}})

    def fix_view(self, kind):
        """停著等你、自動填表開著:只剩要重翻(tr)、只缺證據(noev)、自動重打過一次還沒好(tried)(#339 第二、三條、#342)。"""
        import next_step
        fb, jobs = flow_board(**{URL: 'parked'})
        if kind == 'tr':
            fb['__ans__'][0]['tr'] = 1
        else:
            fb[URL]['form']['f'][0]['refill'] = 1
        if kind == 'noev':
            fb['__ans__'][0].update(inf='2026-01-02', noev='沒有程式自己截的那一頁')
        if kind == 'tried':
            fb['__auto__']['tried'] = ['fix:' + URL + ':' + next_step.fix_sig(fb, URL)]
        return shown(steps(fb, jobs, flow=dict(FLOW, auto_fill=True))[URL]['view'])

    def test_only_a_retranslation_left_is_done_by_the_agent_not_by_you(self):
        v = self.fix_view('tr')
        self.assertEqual((v['line'][-1], v['todo']), ('⏳ 答案改過,你停手一分鐘後 Agent 會自動照新答案重翻 1 條', ''))
        self.assertFalse(any('要 Agent 改' in b for b in v['buttons']))

    def test_only_missing_evidence_is_reported_not_promised(self):
        import form_record as fr
        v = self.fix_view('noev')
        self.assertFalse(any('會自動' in t for t in v['line']))
        self.assertEqual((v['why'], v['todo']), ('⚠ ' + fr.NO_EVIDENCE, fr.NO_EVIDENCE))

    def test_a_retype_that_did_not_take_says_why_and_hands_you_the_button(self):
        v = self.fix_view('tried')
        self.assertFalse(any('會自動照新答案' in t for t in v['line']))
        self.assertIn('不會再自動重試', v['why'])
        self.assertEqual(v['buttons'][0], '✏️ 要 Agent 改(1 欄照新答案重打)')

    def test_a_card_sent_back_as_not_sent_is_queued_when_the_flow_fills(self):
        fb, jobs = flow_board(**{URL: 'sent'})
        ds.fire(fb, URL, 'undo_sent')
        self.assertIn('⏳ 排隊中:Agent 會自動填這張,填好停在送出前', shown(steps(fb, jobs)[URL]['view'])['line'])

    def test_a_sent_card_has_no_delivery_block(self):
        self.assertTrue(card_view('sent')['locked'])

    def test_a_sent_card_gives_back_the_button_its_evidence_allows(self):
        """agent 親手送出的給「沒送成」,你在外部送出的給「退回」(狀態表的 guard 看證據來源)。"""
        for by, want in (('agent', 'undo_sent'), ('manual', 'back')):
            with self.subTest(sent_by=by):
                fb, jobs = flow_board(**{URL: 'sent'})
                fb[URL]['sent_by'] = by
                self.assertEqual(steps(fb, jobs, flow={})[URL]['back'], want)

    def test_whether_a_batch_fill_would_take_the_card(self):
        for state, want in (('todo', True), ('parked', False), ('gone', True), ('running', False)):
            with self.subTest(state=state):
                fb, jobs = flow_board(**{URL: state})
                self.assertEqual(steps(fb, jobs, flow={})[URL]['fillable'], want)


class AnswerBank(unittest.TestCase):
    """常用答案每一條等不等你、能不能改、哪幾張表單在用,整份看板後台算一次(#343、user story 23):
    看板的 ⚠、分頁數字、「答案一次確認完」都照它。"""

    def bank(self):
        fb = {URL: copy.deepcopy(FIX['cards']['unsure']), OFF: {'app': 'ship', 'form': {'f': [
            {'q': 'Where?', 'src': 'bank', 'k': 'k2'}, {'q': 'Proof?', 'src': 'bank', 'k': 'k4'}]}},
            'old': {'app': 'sent', 'form': {'lock': 1, 'f': [{'q': 'Salary?', 'src': 'bank', 'k': 'k3'}]}},
            '__ans__': [{'k': 'k1', 'q': 'Why?', 'v': 'Because.', 'inf': '2026-01-02'},
                        {'k': 'k2', 'q': 'Where?', 'v': ''},
                        {'k': 'k3', 'q': 'Salary?', 'v': ''},
                        {'k': 'k4', 'q': 'Proof?', 'v': 'Yes', 'inf': '2026-01-02', 'noev': '沒有程式自己截的那一頁'},
                        {'k': 'k5', 'q': 'Spare?', 'v': 'x', 'redo': '2026-01-03'}]}
        import next_step
        return next_step.answers(fb)

    def test_inferred_and_empty_answers_an_unsent_form_uses_wait_for_you(self):
        self.assertEqual(self.bank()['need'], ['k1', 'k2', 'k4'])      # 只剩已送出的表單在用的空答案是紀錄,不催

    def test_an_answer_missing_evidence_is_not_asked(self):
        self.assertEqual(self.bank()['ask'], ['k1', 'k2'])

    def test_an_answer_a_card_sent_without_confirmation_uses_cannot_be_changed(self):
        self.assertEqual(self.bank()['locked'], ['k1'])

    def test_the_forms_using_each_answer_count_the_whole_board(self):
        self.assertEqual(self.bank()['users']['k3'], [['old', True]])


class BlockedCompany(unittest.TestCase):
    """封鎖一家公司:名字裡有什麼字元都算同一家,那家的卡不會被準備、不會被填表(#339 第五條、user story 27)。"""

    def board(self, stage):
        fb, _ = flow_board(**{URL: stage})
        fb['__block__'] = ['STRASSE']
        return fb, [{'id': URL, 'target': 'Engineer · Straße GmbH'}]

    def test_the_card_says_which_block_it_falls_under(self):
        fb, jobs = self.board('todo')
        self.assertEqual(steps(fb, jobs)[URL]['blocked'], 'STRASSE')

    def test_a_blocked_companys_card_is_neither_prepared_nor_filled(self):
        for stage in ('prep', 'todo'):
            with self.subTest(stage=stage):
                fb, jobs = self.board(stage)
                self.assertIsNone(steps(fb, jobs)[URL]['auto'])
                fb['__block__'] = []
                self.assertIsNotNone(steps(fb, jobs)[URL]['auto'])


class AdvanceWaitsForTheCheck(unittest.TestCase):
    """待你決定的卡,要等它進來之後的那一輪建置和投遞前驗收跑完才自動推進:等不等在下一步裡算,自動流程照它(#340、#342)。"""

    def test_a_card_that_just_arrived_waits_for_the_next_check(self):
        fb, jobs = flow_board(r='ready')
        self.assertEqual(steps(fb, jobs, gen=3)['r']['auto']['wait'], True)          # 還沒記下要等哪一輪
        fb['__auto__']['seen'] = {'r': 3}
        self.assertEqual(steps(fb, jobs, gen=3)['r']['auto']['wait'], True)          # 那一輪還沒跑完
        self.assertEqual(steps(fb, jobs, gen=4, building=True)['r']['auto']['wait'], True)
        self.assertEqual(steps(fb, jobs, gen=4)['r']['auto']['wait'], False)

    def test_a_copy_of_the_board_has_no_check_to_wait_for(self):
        fb, jobs = flow_board(r='ready')
        self.assertEqual(steps(fb, jobs, real=False)['r']['auto']['wait'], False)


CUSTOM_WAITING = '通用版 的客製版等你看，收下或退回後才能送出'


def custom_waiting(m):
    """這張要寄的履歷有一份客製版還在等你看。"""
    m.update(resume_id='general', custom_docs={'resume:general:zh': {'status': 'review', 'name': '履歷'}})


class CustomVersionWaiting(unittest.TestCase):
    """客製版還在等你檢查:卡上和後台都擋住,原因同一句(#340 user story 19)。"""

    def test_a_filled_card_can_be_neither_confirmed_nor_sent(self):
        fb = ready_to_confirm()
        custom_waiting(fb[URL])
        got = step(fb)
        self.assertEqual((got['gate'], got['confirm'], got['send']), (CUSTOM_WAITING,) * 3)

    def test_a_card_waiting_for_you_to_decide_is_not_advanced(self):
        fb, jobs = flow_board(r='ready')
        custom_waiting(fb['r'])
        got = steps(fb, jobs, real=False)['r']
        self.assertEqual((got['gate'], got['auto']), (CUSTOM_WAITING, None))


class Gate(unittest.TestCase):
    """投遞前把關的結果和原因(#340):進「可以投了」那顆按鈕、卡上那一句都照它。"""

    def test_the_agents_closed_verdict_holds_until_you_say_the_job_is_still_open(self):
        fb = {URL: {'app': 'ready'}}
        got = next_step_of(fb, CLOSED)
        self.assertEqual((got['gate'], got['closed'], got['holds']), ('驗收未通過：agent 判斷職缺已關閉', True, CLOSED['issues']))
        fb[URL]['judged_no'] = {'closed': '2026-01-02'}
        got = next_step_of(fb, CLOSED)
        self.assertEqual((got['gate'], got['closed'], got['holds']), ('', False, []))


def next_step_of(fb, status):
    import next_step
    return next_step.of(fb, [JOB], status)[URL]


class FlowPicksExactlyTheNextStep(unittest.TestCase):
    """自動流程選中的卡,正好就是下一步說 agent 會接手的那些,一張不多、一張不少(#342 第一條、user story 1、2)。"""

    def board(self):
        fb, jobs = flow_board(p1='prep', p2='prep', p3='prep', r1='ready', r2='ready', f1='todo', f2='todo', f3='todo',
                              g1='gone', s1='stale', x1='parked', x2='parked', n1='parked', e1='parked', c1='confirmed',
                              u1='unsure', t1='stuck', d1='sent')
        jobs[[j['id'] for j in jobs].index('p2')]['prep_note'] = '抓不到 JD'      # 上一輪沒產出:等他決定
        fb['__auto__']['skip'] = ['p3']                                            # 開啟當下就在流程裡的舊卡
        fb['__auto__']['tried'] = ['adv:r2']                                       # 推過一次、他又退回來的
        fb['f3']['rm'] = 1
        bank = fb['__ans__']
        for i, k, mark in (('x1', 'k1', 'refill'), ('x2', 'k2', 'tr'), ('n1', 'k3', 'noev'), ('e1', 'k4', 'refill')):
            fb[i]['form']['f'] = [{'q': k + '?', 'src': 'bank', 'k': k, 'refill': 1} if mark != 'tr'
                                  else {'q': k + '?', 'src': 'bank', 'k': k}]
            e = {'k': k, 'q': k + '?', 'v': 'v-' + k, 'zh': '中-' + k, 'at': '2026-01-01'}
            if mark == 'tr':
                e['tr'] = 1
            if mark == 'noev':
                e.update(inf='2026-01-02', noev='沒有程式自己截的那一頁')
            bank[:] = [x for x in bank if x['k'] != k] + [e]
        import next_step
        fb['__auto__']['tried'].append('fix:e1:' + next_step.fix_sig(fb, 'e1'))   # 自動重打過、還沒改好的
        return fb, jobs

    def drain(self, fb, jobs, flow=FLOW):
        """真的看板上,自動流程一輪一輪跑到沒事可做(每輪只派一張填表或重打,每輪之間建置和驗收跑完一輪);
        回派過的 (做什麼, 哪張)。"""
        picked, t = set(), NOW
        for gen in range(60):
            pl = plan(fb, jobs, flow=flow, now=t, gen=gen)
            got = {('advance', i) for i in pl['advance']} | {('fix', pl['fix']), ('fill', pl['fill'])} - {('fix', None), ('fill', None)}
            if pl['prep']:
                got |= {('prep', k.split(':', 1)[1]) for k in pl['tried'] if k.startswith('prep:')}
            picked |= got
            settle(fb, pl)
            fb['__auto__']['seen'].update(pl['seen'])
            if not got and pl['wait'] is None and not pl['seen']:
                return picked
            t += datetime.timedelta(seconds=10)
        self.fail('自動流程停不下來')

    def test_the_cards_the_flow_picks_are_the_ones_the_next_step_promises(self):
        fb, jobs = self.board()
        promised = {(x['auto']['do'], i) for i, x in steps(fb, jobs).items() if x['auto']}
        self.assertEqual({d for d, _ in promised}, {'prep', 'advance', 'fill', 'fix'})       # 四種都有,不是空比空
        self.assertEqual(self.drain(fb, jobs), promised)

    def test_with_the_held_pages_full_the_flow_still_picks_exactly_the_promised_ones(self):
        fb, jobs = self.board()
        flow = dict(FLOW, fill_max=3)
        promised = {(x['auto']['do'], i) for i, x in steps(fb, jobs, flow=flow).items() if x['auto']}
        self.assertIn(('fill', 's1'), promised)                         # 已經停著的那張要重填,不算新的
        self.assertNotIn(('fill', 'f1'), promised)
        self.assertEqual(self.drain(fb, jobs, flow), promised)


from test_board import HttpBase, read_fb  # noqa: E402  真的看板伺服器(臨時看板)

SERVER_JOBS = [JOB]


class Server(HttpBase):
    """後台收事件和存檔:繞過畫面直接送,也照下一步收或擋。"""

    def use(self, fb):
        """看板換成這一份標記(第一次連職缺和驗收結果一起寫;之後走正常寫入,不然會被當成有人繞過寫入改檔)。"""
        if not getattr(self, '_made', False):
            _env.make_board(self.path, fb, data={'jobs': SERVER_JOBS, 'status': PASSED})
            self._made = True
            return
        import board_doc as bd

        def put(cur):
            cur.clear()
            cur.update(copy.deepcopy(fb))
        bd.set_fb(put, live=self.path)

    def save(self, body):
        code, raw, _ = self.req('/api/save', dict({'__rev__': 1}, **body))
        self.assertEqual(code, 200, raw)
        return json.loads(raw)

    def next_of(self, url=URL):
        return json.loads(self.req('/api/next')[1])[url]

    def card(self):
        return read_fb(self.path)[URL]


class ConfirmEvent(Server):
    def test_confirming_a_card_with_an_answer_waiting_is_refused_with_the_buttons_reason(self):
        fb = ready_to_confirm()
        fb['__ans__'][0]['inf'] = 1
        self.use(fb)
        shown = self.next_of()['confirm']
        got = self.save({'__events__': [{'u': URL, 'ev': 'confirm', 'data': {'approve': {'snap': {}}}}]})
        self.assertEqual([(r['u'], r['msg']) for r in got['rejected']], [(URL, shown)])
        self.assertEqual(shown, '還有答案等你確認或填寫')
        self.assertEqual(ds.state(self.card()), 'parked')

    def test_the_confirmed_answers_are_the_ones_the_backend_reads_not_the_ones_the_page_sent(self):
        """確認時記下的答案由後台當下算(#340 user story 9):頁面送來的快照不算數。"""
        self.use(ready_to_confirm())
        got = self.save({'__events__': [{'u': URL, 'ev': 'confirm',
                                         'data': {'approve': {'snap': {'Why?': '頁面上的舊答案'}, 'at': '2026-02-02T00:00:00Z'}}}]})
        self.assertEqual(got['rejected'], [])
        m = self.card()
        self.assertEqual((ds.state(m), m['approve']['snap'], m['approve']['at']),
                         ('confirmed', {'Why?': 'Because.'}, '2026-02-02T00:00:00Z'))


BUSY = {'running': 'Agent 正在做,等它做完', 'sending': 'Agent 正在做,等它做完',
        'unsure': '送出結果不明,先確認到底送出沒有'}


def busy_board(state):
    return {URL: copy.deepcopy(FIX['cards'][state]), '__ans__': copy.deepcopy(FIX['ans'])}


class BusyCard(unittest.TestCase):
    """正在填、正在送出、送出結果不明:換檔、上傳客製版、退回、移除都不行,原因寫在按鈕上(#341)。"""

    def test_busy_cards_say_why(self):
        for state, why in BUSY.items():
            with self.subTest(state=state):
                self.assertEqual(step(busy_board(state))['busy'], why)

    def test_a_card_left_waiting_is_not_busy(self):
        self.assertIsNone(step(busy_board('parked'))['busy'])


class BusyEvents(Server):
    """繞過畫面直接送,後台照同一條擋(#340 user story 21)。"""

    def test_removing_a_card_the_agent_is_filling_is_refused_with_the_buttons_reason(self):
        self.use(busy_board('running'))
        shown = self.next_of()['busy']
        got = self.save({URL: dict(self.card(), rm=1), '__events__': [{'u': URL, 'ev': 'leave'}]})
        self.assertEqual([(r['u'], r['msg']) for r in got['rejected']], [(URL, shown)])
        self.assertNotIn('rm', self.card())

    def test_saving_the_card_removed_or_with_another_resume_without_an_event_is_refused_too(self):
        for state in BUSY:
            for change in ({'rm': 1}, {'resume_id': 'tech'}, {'lang': 'en'}, {'app': 'ready'}, {'s': 'techerr'}, {'s': 'dislike'}):
                with self.subTest(state=state, change=change):
                    self.use(busy_board(state))
                    before = self.card()
                    got = self.save({URL: dict(before, **change)})
                    self.assertEqual([(r['u'], r['msg']) for r in got['rejected']], [(URL, BUSY[state])])
                    self.assertEqual(self.card(), before)

    def test_a_note_on_a_busy_card_is_still_saved(self):
        self.use(busy_board('running'))
        got = self.save({URL: dict(self.card(), n='我的筆記')})
        self.assertEqual((got['rejected'], self.card()['n']), ([], '我的筆記'))

    def test_using_your_own_file_while_the_agent_is_filling_is_refused(self):
        import urllib.parse
        for state in BUSY:
            with self.subTest(state=state):
                self.use(busy_board(state))
                before = self.card()
                code, raw, _ = self.req('/api/card-file?u=' + urllib.parse.quote(URL) + '&name=mine.pdf',
                                        data=b'%PDF-1.4 mine', method='PUT')
                self.assertEqual((code, json.loads(raw)['msg']), (409, BUSY[state]))
                self.assertEqual(self.card(), before)


class EveryEvent(unittest.TestCase):
    """後台收到任何事件都先看下一步:擋的原因就是卡上那一句,狀態表擋的也是同一句(#340、審查 Spec 第 6 點)。"""

    def test_confirm_swap_and_leave_are_refused_with_the_cards_own_sentence(self):
        import next_step
        for state in FIX['cards']:
            fb = busy_board(state)
            nx = step(fb)
            with self.subTest(state=state):
                self.assertEqual(next_step.refuse(fb, URL, 'confirm', PASSED), nx['confirm'])
                for ev in ('files_changed', 'leave', 'back', 'sent_manual'):
                    if nx['busy']:
                        self.assertEqual(next_step.refuse(fb, URL, ev), nx['busy'])

    def test_any_other_event_is_refused_with_the_sentence_the_state_table_raises(self):
        """取消確認、再投一次、沒送成、確認沒送出、其實送出了……:准就收,不准的原因跟套表時擋的一字不差。"""
        import next_step
        refused = 0
        for state in FIX['cards']:
            for ev in ds.TABLE['events']:
                if ev == 'confirm' or (ev in ('files_changed', 'leave', 'back', 'sent_manual') and ds.BUSY[state]):
                    continue
                fb = busy_board(state)
                why = next_step.refuse(fb, URL, ev)
                with self.subTest(state=state, ev=ev):
                    try:
                        ds.fire(copy.deepcopy(fb), URL, ev)
                        self.assertIsNone(why)
                    except ds.Forbidden as e:
                        self.assertEqual(why, str(e))
                        refused += 1
        self.assertGreater(refused, 50)                 # 真的比到了擋下的那些,不是空比空

    def test_examples_of_the_sentence(self):
        import next_step
        self.assertEqual(next_step.refuse(busy_board('parked'), URL, 'unconfirm'), '「停著等你」時不能「取消確認、復原」')
        fb = busy_board('sent')
        fb[URL]['sent_by'] = 'manual'
        self.assertEqual(next_step.refuse(fb, URL, 'undo_sent'), '「已送出」這張不能「沒送成」')


class EveryEventOnTheServer(Server):
    def test_a_refused_event_comes_back_with_the_next_steps_sentence(self):
        import next_step
        for state, ev in (('parked', 'retry'), ('confirmed', 'not_sent'), ('unsure', 'leave'), ('sent', 'unconfirm')):
            with self.subTest(state=state, ev=ev):
                fb = busy_board(state)
                why = next_step.refuse(copy.deepcopy(fb), URL, ev, PASSED)
                self.use(fb)
                got = self.save({'__events__': [{'u': URL, 'ev': ev}]})
                self.assertTrue(why)
                self.assertEqual([(r['u'], r['msg']) for r in got['rejected']], [(URL, why)])


CLOSED = dict(PASSED, issues=[{'jid': URL, 'kind': 'closed', 'judged': 'This position has been filled',
                               'msg': 'agent 判斷職缺已關閉'}])


class JobStillOpen(unittest.TestCase):
    """你按「不對,職缺還在」推翻 agent 的判斷:下一步當下就不擋,不等驗收重跑(#339 第四條)。"""

    def test_the_agents_closed_verdict_blocks_confirming(self):
        self.assertEqual(step(ready_to_confirm(), CLOSED)['confirm'], '驗收未通過：agent 判斷職缺已關閉')

    def test_saying_the_job_is_still_open_unblocks_it_at_once(self):
        fb = ready_to_confirm()
        fb[URL]['judged_no'] = {'closed': '2026-01-02'}
        self.assertIsNone(step(fb, CLOSED)['confirm'])


class JobStillOpenOnTheServer(Server):
    def test_saving_the_job_is_still_open_unblocks_the_backend_too(self):
        self.use(ready_to_confirm())
        import board_doc as bd
        bd.rewrite(lambda d: d['data'].__setitem__('status', CLOSED), self.path, 'test')
        self.assertEqual(self.next_of()['confirm'], '驗收未通過：agent 判斷職缺已關閉')
        self.save({URL: dict(self.card(), judged_no={'closed': '2026-01-02'})})
        self.assertIsNone(self.next_of()['confirm'])
        got = self.save({'__events__': [{'u': URL, 'ev': 'confirm', 'data': {'approve': {}}}]})
        self.assertEqual((got['rejected'], ds.state(self.card())), ([], 'confirmed'))


class PageGetsTheNextStep(Server):
    """頁面怎麼拿下一步(#340):載入時拿全部、存檔回傳有變動的卡、背景改了卡就重拿得到新的。"""

    def test_the_page_carries_every_cards_next_step(self):
        import board_doc as bd
        fb = ready_to_confirm()
        fb['__ans__'][0]['inf'] = 1
        self.use(fb)
        page = bd.parse(self.req('/')[1].decode('utf-8'))['data']
        self.assertEqual(page['next'][URL]['confirm'], '還有答案等你確認或填寫')

    def test_the_page_says_what_the_agent_takes_over_with_the_saved_settings(self):
        """卡上寫會自動做的,跟自動流程用同一份設定算(⚙ 設定的自動流程、停著的頁上限)。"""
        from unittest import mock
        import config as cf
        fb, _ = flow_board()
        fb[URL] = copy.deepcopy(FIX['cards']['todo'])
        for on, want in ((True, 'fill'), (False, None)):
            with self.subTest(auto_fill=on), mock.patch.dict(cf.C, {'flow': dict(FLOW, auto_fill=on)}):
                self.use(fb)
                self.assertEqual((self.next_of()['auto'] or {}).get('do'), want)

    def test_saving_returns_the_new_next_step_of_the_changed_card(self):
        self.use(ready_to_confirm())
        got = self.save({'__events__': [{'u': URL, 'ev': 'fix_start', 'data': {'apply': {'stage': 'fix'}}}]})
        self.assertEqual(got['next'][URL]['busy'], 'Agent 正在做,等它做完')

    def test_the_page_and_the_job_poll_carry_the_card_and_company_names(self):
        import board_doc as bd
        self.use(ready_to_confirm())
        page = bd.parse(self.req('/')[1].decode('utf-8'))['data']['jobs'][0]
        poll = json.loads(self.req('/api/jobs')[1])['jobs'][0]
        self.assertEqual([(j['name'], j['co'], j['src_plat']) for j in (page, poll)], [('Role · Acme', 'Acme', 'cells.example')] * 2)

    def test_the_page_is_told_the_rules_it_shows_not_a_copy_of_them(self):
        """看板寫的「幾點自動再試、第幾次」、回報是哪個流程寫的,用後台的那一份(#343:不抄常數)。"""
        import board_doc as bd
        self.use(ready_to_confirm())
        cfg = bd.parse(self.req('/')[1].decode('utf-8'))['data']['cfg']
        self.assertEqual((cfg['reply_retry'], cfg['inbox_from']['代投']), ({'max': 3, 'gap': 3600}, ['幫你填表', 'ship']))

    def test_the_reports_waiting_for_you_leave_out_the_agents_own_without_evidence(self):
        fb = ready_to_confirm()
        fb['__inbox__'] = [{'id': 'a', 'msg': '填不進去'}, {'id': 'b', 'msg': '它說的', 'agent': 1, 'noev': '沒截圖'},
                           {'id': 'c', 'msg': '好了', 'done': '2026-01-02'}]
        self.use(fb)
        self.assertEqual(json.loads(self.req('/api/next')[1])['__inbox__'], {'todo': ['a']})

    def test_saving_an_event_returns_the_card_the_server_stored(self):
        """看板按下去不自己套狀態表:等後台回話,拿它存好的那一張畫(#343、user story 5)。"""
        self.use(ready_to_confirm())
        got = self.save({'__events__': [{'u': URL, 'ev': 'confirm', 'data': {}}]})
        self.assertEqual((got['cards'][URL], ds.state(got['cards'][URL])), (self.card(), 'confirmed'))

    def test_a_refused_event_returns_the_card_as_it_was(self):
        fb = ready_to_confirm()
        fb['__ans__'][0]['inf'] = 1
        self.use(fb)
        before = self.card()
        got = self.save({URL: dict(before, rm=1), '__events__': [{'u': URL, 'ev': 'confirm', 'data': {}}]})
        self.assertEqual((got['cards'][URL], got['undo']), (before, {}))

    def test_undoing_with_what_the_save_returned_puts_the_card_back(self):
        self.use(ready_to_confirm())
        before = self.card()
        got = self.save({'__events__': [{'u': URL, 'ev': 'confirm', 'data': {}}]})
        back = self.save({'__events__': [{'u': URL, 'undo': got['undo'][URL]}]})
        self.assertEqual((back['rejected'], back['cards'][URL]), ([], before))

    def test_a_card_changed_in_the_background_shows_up_in_the_next_poll(self):
        import board_doc as bd
        self.use(ready_to_confirm())
        self.assertIsNone(self.next_of()['busy'])
        bd.set_fb(lambda f: ds.fire(f, URL, 'fix_start', apply={'stage': 'fix'}), live=self.path, by='autopilot')
        self.assertEqual(self.next_of()['busy'], 'Agent 正在做,等它做完')


OFF = 'https://off-board.example/job/1'     # 有表單、但不在看板上畫得出來的卡(重建拿掉、別的看板資料)


class ClearAnAnswer(Server):
    """清掉一條常用答案:清掉還是刪掉、哪幾張表單受影響,後台照整份看板算,不在看板上的卡也算(#342、user story 24、25)。"""

    def board(self, off_locked):
        fb = ready_to_confirm()
        fb['__ans__'].append({'k': 'k2', 'q': 'Where?', 'v': 'Taipei', 'at': '2026-01-01'})
        fb[OFF] = {'app': 'ship', 'form': {'f': [{'q': 'Where?', 'src': 'bank', 'k': 'k2'}]}}
        if off_locked:
            fb[OFF]['form']['lock'] = '2026-01-01'
        return fb

    def bank(self):
        return {e['k']: e for e in read_fb(self.path)['__ans__']}

    def test_an_answer_only_an_off_board_unsent_form_uses_is_cleared_not_deleted(self):
        self.use(self.board(off_locked=False))
        self.save({'__events__': [{'redo': 'k2'}]})
        e = self.bank()['k2']
        self.assertEqual((e['v'], bool(e.get('redo'))), ('', True))      # 題目留著,下一輪 agent 代填

    def test_the_page_says_clear_or_delete_counting_off_board_forms(self):
        """按鈕寫「清掉答案」還是「刪除這條」,跟按下去後台做的一樣(不在看板上的表單也算)。"""
        for locked, want in ((False, ['k1', 'k2']), (True, ['k1'])):
            with self.subTest(off_locked=locked):
                self.use(self.board(off_locked=locked))
                self.assertEqual(json.loads(self.req('/api/next')[1])['__ans__']['in_use'], want)

    def test_an_answer_only_sent_forms_use_is_deleted(self):
        self.use(self.board(off_locked=True))
        self.save({'__events__': [{'redo': 'k2'}]})
        self.assertNotIn('k2', self.bank())

    def test_the_filled_page_using_it_is_marked_to_retype(self):
        self.use(self.board(off_locked=False))
        got = self.save({'__events__': [{'redo': 'k1'}]})
        self.assertTrue(self.card()['form']['f'][0].get('refill'))
        self.assertEqual(got['next'][URL]['confirm'], '有答案改過,網頁上還是舊的,先讓 agent 改')

    def test_a_confirmed_card_using_it_loses_the_confirmation(self):
        fb = self.board(off_locked=False)
        self.use(fb)
        self.save({'__events__': [{'u': URL, 'ev': 'confirm', 'data': {'approve': {}}}]})
        self.assertEqual(ds.state(self.card()), 'confirmed')
        self.save({'__events__': [{'redo': 'k1'}]})
        self.assertEqual(ds.state(self.card()), 'parked')

    def test_the_cleared_answer_survives_an_old_bank_sent_in_the_same_save(self):
        """同一包又帶了這個分頁手上的整份常用答案(別的答案剛改過):清掉照樣算數。"""
        fb = self.board(off_locked=False)
        self.use(fb)
        ans = copy.deepcopy(fb['__ans__'])
        self.save({'__ans__': ans, '__events__': [{'redo': 'k2'}]})
        self.assertEqual(self.bank()['k2']['v'], '')


class NotSent(Server):
    def test_saying_it_was_not_sent_after_the_page_closed_says_the_page_is_gone(self):
        """他查過沒送出、那一頁已經不在了:卡上寫頁面不見了要重填,這一句後台寫,看板不抄(#343)。"""
        fb = busy_board('unsure')
        fb[URL]['apply'].pop('tab_id', None)
        self.use(fb)
        got = self.save({'__events__': [{'u': URL, 'ev': 'not_sent', 'data': {}}]})
        m = self.card()
        self.assertEqual((got['rejected'], ds.state(m), m['apply']['issues']), ([], 'gone', [ds.GONE]))


class ActuallySent(Server):
    def test_saying_it_was_sent_after_all_keeps_the_agents_record_as_the_evidence(self):
        """送出結果不明、他查到其實送出了:證據就是 agent 當時那一份,後台抄,看板不用知道狀態表先清哪一格(#343)。"""
        self.use(busy_board('unsure'))
        fail = copy.deepcopy(self.card()['apply']['submit_fail'])
        got = self.save({'__events__': [{'u': URL, 'ev': 'actually_sent', 'data': {'by': 'agent', 'at': '2026-01-03T00:00:00'}}]})
        m = self.card()
        self.assertEqual((got['rejected'], ds.state(m), m['apply']['sent']),
                         ([], 'sent', dict(fail, you_sent='2026-01-03T00:00:00')))


class ChangeAnAnswer(Server):
    """改了一條答案(看板送新的常用答案和 {refill: 鍵}):用到它的停著的頁標重打、已確認的確認作廢,
    後台照整份看板算,看板不自己標(#343、user story 25)。"""

    def change(self):
        ans = read_fb(self.path)['__ans__']
        ans[0]['v'] = 'Because I care.'
        return self.save({'__ans__': ans, '__events__': [{'refill': 'k1'}]})

    def test_the_filled_page_using_it_is_marked_to_retype(self):
        self.use(ready_to_confirm())
        got = self.change()
        self.assertEqual((got['next'][URL]['confirm'], self.card()['form']['f'][0].get('refill'), got['cards'][URL]),
                         ('有答案改過,網頁上還是舊的,先讓 agent 改', 1, self.card()))

    def test_a_confirmed_card_using_it_loses_the_confirmation(self):
        self.use(ready_to_confirm())
        self.save({'__events__': [{'u': URL, 'ev': 'confirm', 'data': {}}]})
        self.change()
        self.assertEqual(ds.state(self.card()), 'parked')


class SwapMarksThePageOld(Server):
    """換檔被允許時,已經填好的頁一定變成「上傳的是舊檔」(#341、#340 user story 22):
    不管看板有沒有送換檔事件,後台看要寄的檔案真的變了就標,舊檔不會被送出去。"""

    def setUp(self):
        super().setUp()
        from unittest import mock
        import config as cf
        src = os.path.join(self.dir, 'general.pdf')
        with open(src, 'wb') as f:
            f.write(b'%PDF resume')
        resumes = {'general': {'id': 'general', 'name': '通用版', 'files': {'zh': src, 'en': src}, 'enabled': True}}
        for name, value in (('RESUMES', resumes), ('ATTACHMENTS', []), ('LANGS', ['zh', 'en'])):
            patcher = mock.patch.object(cf, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        SERVER_JOBS[:] = [dict(JOB, resume={'recommend': 'general', 'lang': 'zh'})]
        self.addCleanup(lambda: SERVER_JOBS.__setitem__(slice(None), [JOB]))
        self.use(ready_to_confirm())

    def test_switching_the_language_of_a_filled_card_without_an_event_marks_the_page_old(self):
        got = self.save({URL: dict(self.card(), lang='en')})
        m = self.card()
        self.assertEqual((got['rejected'], ds.state(m), m['lang'], m['apply']['stale']), ([], 'stale', 'en', '語言換成「英文」'))

    def test_the_boards_own_event_is_kept_as_the_reason(self):
        got = self.save({URL: dict(self.card(), lang='en'),
                         '__events__': [{'u': URL, 'ev': 'files_changed', 'data': {'why': '語言換成「English」'}}]})
        m = self.card()
        self.assertEqual((got['rejected'], ds.state(m), m['apply']['stale']), ([], 'stale', '語言換成「English」'))

    def test_pressing_the_language_already_in_use_changes_nothing(self):
        self.save({URL: dict(self.card(), lang='zh')})
        self.assertEqual(ds.state(self.card()), 'parked')


if __name__ == '__main__':
    unittest.main()
