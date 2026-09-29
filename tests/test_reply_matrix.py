"""查應徵進度的狀態矩陣(#293):已投出一張卡的狀態 × 這一輪查到的回音,所有組合都跑一次 apply_results,
驗不變量,不再一個情況一個測試:

- 程式記成沒下文,只能是:這一輪真的查過而且程式核實過、送出滿天數、前後都沒有回音、原本在等回音、他沒說過別記
- 程式改了結果,一定留下 oc_auto(改之前是什麼),他才按得了「不對,復原」
- 「我不去了」是他的決定,程式不改;他自己按的沒下文,確認信不會把它改回去
- 已移除、不在已投出的卡,程式一個字都不動
"""
import copy, datetime, itertools, unittest
import _env  # noqa: F401
import reply_run as rr

DAY = '2026-06-30'
U = 'https://matrix.example/sent/1'


def ago(n):
    return (datetime.date.fromisoformat(DAY) - datetime.timedelta(days=n)).isoformat()


OC = ['', 'iv', 'rej', 'ghost', 'wd', 'offer']
AUTO = [None, 'ghost', 'reply']
FINDING = [None, 'reject', 'confirm', 'interview']


def card(oc, auto, ghost_no, age, prior, rm, app):
    m = {'app': app, 'sent_at': ago(age)}
    if oc:
        m['oc'] = oc
        m['oc_at'] = {oc: ago(3)} if oc in ('iv', 'offer', 'rej') else {}
    if auto == 'ghost' and oc == 'ghost':
        m['oc_auto'] = {'s': 'ghost', 'from': '', 'at': ago(3), 'by': '送出太久沒回音'}
    elif auto == 'reply' and oc:
        m['oc_auto'] = {'s': oc, 'from': '', 'at': ago(3), 'by': 'r0'}
    if ghost_no:
        m['ghost_no'] = 1
    if prior:
        m['replies'] = {'items': [{'id': 'r0', 'src': 'Gmail', 'kind': 'confirm', 'date': ago(age - 1),
                                   'source_type': 'email', 'source_ref': 'email:t0', 'link': 'https://mail.example/t0'}]}
    if rm:
        m['rm'] = 1
    return m


def finding(kind):
    return {'src': 'Gmail', 'date': ago(1), 'kind': kind, 'source_type': 'email', 'source_ref': 'email:t1',
            'link': 'https://mail.example/t1', 'snippet': '矩陣測試'}


class ReplyMatrix(unittest.TestCase):
    def test_every_combination_keeps_the_invariants(self):
        bad, n = {}, 0
        for oc, auto, ghost_no, age, checked, unverified, prior, kind, rm, app in itertools.product(
                OC, AUTO, [False, True], [5, rr.GHOST_DAYS + 1], [False, True], [False, True],
                [False, True], FINDING, [False, True], ['sent', 'ship']):
            n += 1
            m0 = card(oc, auto, ghost_no, age, prior, rm, app)
            fb = {U: copy.deepcopy(m0)}
            rr.apply_results(fb, {U: [finding(kind)]} if kind else {}, {U} if checked else set(), day=DAY,
                             unverified={U} if unverified else set())
            m = fb[U]
            c = dict(oc=oc, auto=auto, ghost_no=ghost_no, age=age, checked=checked, unverified=unverified,
                     prior=prior, kind=kind, rm=rm, app=app)

            def fail(what):
                bad.setdefault(what, []).append(c)
            before, after = m0.get('oc') or '', m.get('oc') or ''
            if rm or app != 'sent':
                if m != m0:
                    fail('已移除或不在已投出的卡被動到')
                continue
            if after == 'ghost' and before != 'ghost':
                if not (checked and not unverified and age >= rr.GHOST_DAYS and not prior and not kind
                        and before == '' and not ghost_no):
                    fail('不該記成沒下文卻記了')
            if after != before:
                oa = m.get('oc_auto') or {}
                if oa.get('s') != after or oa.get('from') != before:
                    fail('程式改了結果,卻沒留下改之前是什麼(按不了復原)')
            if before == 'wd' and after != 'wd':
                fail('「我不去了」被程式改掉')
            if before == 'ghost' and not m0.get('oc_auto') and kind == 'confirm' and after != 'ghost':
                fail('他自己按的沒下文被確認信改回去')
        self.assertEqual({k: (len(v), v[:2]) for k, v in bad.items()}, {}, f'共 {n} 種組合')


if __name__ == '__main__':
    unittest.main()
