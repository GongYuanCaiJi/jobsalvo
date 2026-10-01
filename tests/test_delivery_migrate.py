"""舊資料轉成投遞狀態(伺服器起來時做一次):各種舊記號組合轉完狀態都對,再轉一次不變。
轉完之後看板照後台的下一步畫(看板不自己判斷,docs/adr/0005)。"""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾

import delivery_state as ds
import legacy_marks as lm

U = 'https://old.example/job/1'
FORM = {'plat': 'x', 'f': [{'q': 'Why?', 'src': 'bank', 'k': 'k1'}]}
DONE = {'stage': 'fill', 'ok': True, 'issues': [], 'at': '2026-01-01T00:00:00', 'tab_id': '7', 'session': 's1',
        'delivery': {'method': 'direct_upload'}}
APPROVE = {'at': '2026-01-01T00:07:00', 'snap': {'Why?': 'Because.'}}


def card(**kw):
    m = {'app': 'ship', 'form': copy.deepcopy(FORM)}
    a = kw.pop('apply', None)
    if a is not None:
        m['apply'] = a
    m.update(kw)
    return m


# 舊記號 → 該是哪一個狀態(照 GLOSSARY 和狀態圖一張一張想過的,不是跑程式抄回來的)
CASES = [
    ('還沒填', card(), 'todo'),
    ('填好停著', card(apply=dict(DONE)), 'parked'),
    ('填好、按了確認', card(apply=dict(DONE), approve=APPROVE), 'confirmed'),
    ('填了卡住', card(apply=dict(DONE, ok=False, issues=['必填沒填'])), 'stuck'),
    ('沒開到頁就失敗', card(apply=dict(DONE, ok=False, issues=['連不上'], tab_id='')), 'nopage'),
    ('換過履歷', card(apply=dict(DONE, stale='履歷換過了')), 'stale'),
    ('頁面不見了', card(apply=dict(DONE, ok=False, gone=True, tab_id='', issues=[ds.GONE])), 'gone'),
    ('頁面不見了、舊資料分頁編號沒清', card(apply=dict(DONE, ok=False, gone=True, issues=[ds.GONE])), 'gone'),
    ('頁面不見了、還帶著確認', card(apply=dict(DONE, ok=False, gone=True, tab_id='', issues=[ds.GONE]), approve=APPROVE), 'gone'),
    ('送出途中停掉', card(apply=dict(DONE, submit_fail={'at': 'x', 'pending': True, 'problems': ['停掉了']}), approve=APPROVE), 'unsure'),
    ('送出沒看到成功頁', card(apply=dict(DONE, submit_fail={'at': 'x', 'problems': ['沒看到'], 'clicked': True}), approve=APPROVE), 'unsure'),
    ('確認過沒送出、可以重送', card(apply=dict(DONE, submit_fail={'at': 'x', 'problems': ['x'], 'cleared': '2026-01-01'}), approve=APPROVE), 'confirmed'),
    ('agent 送出的', dict(card(apply=dict(DONE, sent={'at': 'x', 'text': '已收到'})), app='sent', sent_at='2026-01-02',
                         form=dict(FORM, lock=1)), 'sent'),
    ('你在外部送出的', dict(card(), app='sent', sent_at='2026-01-02', sent_v='zh-A', form=dict(FORM, lock=1)), 'sent'),
    ('分不出是外部送出還是對帳的舊資料', dict(card(), app='sent', sent_at='2026-01-02', form=dict(FORM, lock=1)), 'sent'),
    ('#297:從已投出退回、帶著 agent 送出的證據', card(apply=dict(DONE, sent={'at': 'x', 'text': '已收到'})), 'todo'),
    ('從已投出退回、表單還鎖著', card(form=dict(FORM, lock=1)), 'todo'),
    ('確認了、卻還沒填過', card(approve=APPROVE), 'todo'),
]


class FromLegacy(unittest.TestCase):
    def test_each_old_combination_lands_in_one_state(self):
        for name, m, want in CASES:
            with self.subTest(name):
                fb = {U: copy.deepcopy(m)}
                ds.migrate(fb)
                self.assertEqual(ds.state(fb[U]), want)

    def test_sent_evidence_on_a_ready_card_goes_to_history(self):
        """「在可以投了、卻帶著已送出紀錄」照「沒送成」轉:證據收進投遞歷史、回到還沒填。"""
        fb = {U: card(apply=dict(DONE, sent={'at': 'x', 'text': '已收到'}), form=dict(FORM, lock=1), approve=APPROVE)}
        ds.migrate(fb)
        m = fb[U]
        self.assertNotIn('apply', m)
        self.assertNotIn('approve', m)
        self.assertNotIn('lock', m['form'])
        self.assertEqual(m['history'][-1]['apply']['sent']['text'], '已收到')

    def test_who_sent_it(self):
        """agent 送出的有證據(拿掉確認和分頁編號,證據保留);沒證據有記寄哪一份的是你在外部送出;
        兩樣都沒有的分不出來(可以退回,投遞日照舊)。"""
        agent, manual, legacy = (dict(CASES[i][1]) for i in (12, 13, 14))
        agent['approve'] = APPROVE
        fb = {U: copy.deepcopy(agent), 'b': copy.deepcopy(manual), 'c': copy.deepcopy(legacy)}
        ds.migrate(fb)
        self.assertEqual([fb[k]['sent_by'] for k in (U, 'b', 'c')], ['agent', 'manual', 'legacy'])
        self.assertNotIn('approve', fb[U])
        self.assertEqual((fb[U]['apply']['tab_id'], fb[U]['apply']['sent']['text']), ('', '已收到'))
        self.assertEqual(fb['c']['sent_at'], '2026-01-02')
        ds.fire(fb, 'c', 'back', to='ship')                  # 分不出來的可以退回
        self.assertEqual(ds.state(fb['c']), 'todo')

    def test_no_old_flags_left_and_only_once(self):
        """所有舊組合:轉完沒有舊記號、「可以投了」沒有一張帶著送出證據、確認只在確認過的狀態;再轉一次不變。"""
        for c in lm.combos():
            fb, _status = lm.state_of(c)
            ds.migrate(fb)
            m = fb[lm.URL]
            a = m.get('apply') or {}
            with self.subTest(c=c):
                self.assertFalse({'ok', 'gone'} & set(a))
                self.assertFalse({'pending', 'cleared'} & set(a.get('submit_fail') or {}))
                self.assertNotIn('sent', a)
                self.assertFalse((m.get('form') or {}).get('lock'))
                if 'approve' in m:
                    self.assertIn(ds.state(m), ('confirmed', 'unsure'))
                if a.get('stale'):
                    self.assertEqual(ds.state(m), 'stale')
                self.assertEqual(ds.problems(m), [])           # 存的資料對得回狀態表
                again = copy.deepcopy(fb)
                self.assertFalse(ds.migrate(again))
                self.assertEqual(again, fb)

    def test_new_cards_are_left_alone(self):
        fb = {U: {'app': 'ship', 'ds': 'parked', 'apply': dict(DONE, ok=True)}}
        fb['__ds__'] = 1
        before = copy.deepcopy(fb)
        self.assertFalse(ds.migrate(fb))
        self.assertEqual(fb, before)


if __name__ == '__main__':
    unittest.main()
