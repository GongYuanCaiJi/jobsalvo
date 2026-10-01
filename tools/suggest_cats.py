#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
suggest_cats —— 看板「⚙ 設定 → 分類」的「讓 agent 建議」:照使用者的履歷和他表過態的卡,建議類別與標籤。

類別是單一軸(這份工作在做什麼),標籤是跟它正交的特徵(實習、遠端、某個產業…)。agent 只給建議,
寫進 <tmp>/suggest.json(交件單);程式經安檢門(gate)核對後寫 <tmp>/suggestion.json 給設定頁看:
整份標明是 agent 判斷的建議,比對規則寫不出來的那一項不收、原因照實列出;「其他」由程式自己加在最後。
使用者在設定頁看過、改過、按「套用」才寫進設定。

用法:python3 tools/suggest_cats.py [--board B]
"""
import os, sys, json, time, argparse, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import jobrun

SP = os.environ.get('SUGGEST_TMP') or cf.TMP
STATUS = 'suggest_status.json'
OUT = 'suggest.json'
SHOWN = 'suggestion.json'      # 安檢門核對過、給設定頁看的那一份(board_server 讀這個)
OTHER = '其他'


def prompt(out, fb, jobs):
    import prefs
    cs = prefs.cards(fb, jobs)
    seen = '\n'.join(f"- {c['title']}({c['mark']})" + (f"｜他說「{c['note']}」" if c['note'] else '') for c in cs[:150])
    titles = '\n'.join('- ' + prefs.title_of(j) for j in jobs[:300])
    now = json.dumps({'categories': cf.C['board']['categories'], 'tags': cf.C['board']['tags']}, ensure_ascii=False)
    return ('幫使用者設計求職看板的分類。\n\n'
            f'類別:單一軸,一張職缺只屬於一類,依「這份工作在做什麼」分,3 到 6 類;最後一類「{OTHER}」程式自己加,你不用寫。'
            '標籤:跟類別正交的特徵(例如實習、遠端、某個產業),一張卡可以有好幾個,0 到 8 個。\n'
            '每一個都給:name(短、使用者看得懂的中文)、icon(一個 emoji,標籤不用)、'
            'match(JavaScript 正規表示式,不分大小寫,比對職稱與內文;中英文關鍵字都寫進去,用 | 分隔)。'
            '程式會檢查每個 match 寫不寫得出來,寫不出來的那一項不收。\n'
            '分法要照他的履歷和他表過態的卡:他在意的方向切細一點,不在意的併在一起。\n\n'
            f'他的上傳履歷文字:\n{prefs.resume()}\n\n他的硬規則:\n{prefs.hard_rules()}\n\n'
            f'他表過態的卡(最多 150 張):\n{seen or "(還沒有)"}\n\n板上的職稱(最多 300 張):\n{titles or "(還沒有)"}\n\n'
            f'現在的設定(可以沿用、改名、合併):\n{now}\n\n'
            f'寫一個 JSON 檔到 {out}:{{"categories": [...], "tags": [...], "why": "一兩句說明這樣分的理由"}}。'
            '寫完 stdout 只印 @@ROUND_DONE@@。')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--board', default=cf.LIVE)
    a = ap.parse_args()
    import prefs, agent_run as ar
    st = lambda d: jobrun.write(os.path.join(SP, STATUS), d)
    t0 = time.time()
    out = os.path.join(SP, OUT)
    for old in (out, os.path.join(SP, SHOWN)):
        with contextlib.suppress(OSError):   # 上一次沒留下
            os.remove(old)
    st({'phase': 'agent', 'pid': os.getpid(), 't0': t0, 'step': 'agent 在想怎麼分'})
    fb, jobs = prefs.load(a.board)
    import evidence
    with evidence.opened('suggest_cats', 'suggest', (), a.board) as rnd:   # 沒有卡:記進這個流程自己的夾(#315)
        result = ar.run(prompt(out, fb, jobs), os.path.join(SP, 'suggest_agent.out'), cf.HOME,
                        timeout=1800, browser_required=False, board=a.board)
        rnd.handoff(out)
    if not result.ok:
        msg = result.message()
        st({'phase': 'failed', 't0': t0, 'finished_at': time.time(), 'msg': msg})
        try:
            import agent_report
            agent_report.report('分類建議', msg, need='在「⚙ 設定 → 分類」按「看紀錄」確認後再重試', live=a.board)
        except Exception:  # noqa: BLE001, S110 — 失敗原因上一行已經寫進進度(設定頁看得到),看板回報只是再提醒一次
            pass
        return 1
    try:
        with evidence.activated(rnd):     # 安檢門的比對結果記進同一輪
            shown = checked(out, cf.C['board'].get('categories') or [])
        with open(os.path.join(SP, SHOWN), 'w', encoding='utf-8') as f:
            json.dump(shown, f, ensure_ascii=False)
        st({'phase': 'done', 't0': t0, 'finished_at': time.time(),
            'msg': '建議好了,到「⚙ 設定 → 分類」看' + (f'({len(shown["problems"])} 項對不上沒收)' if shown['problems'] else '')})
    except Exception as e:  # noqa: BLE001 — agent 交的檔什麼樣子都有可能;原因照實寫進進度(設定頁看得到)
        st({'phase': 'failed', 't0': t0, 'finished_at': time.time(), 'msg': f'agent 沒交出可用的建議({str(e)[:80]})'})
        return 1
    return 0


def checked(out, current=()):
    """交件單經安檢門:比對規則寫不出來的那一項不收(原因列在 problems);「其他」照程式的規矩加在最後。
    整份是 agent 判斷的建議(by),他看過、按套用才生效。沒有一類收得下來就丟 ValueError。"""
    import gate
    sheet, missing = gate.read(os.path.dirname(out), 'suggest_cats', where=out)
    if sheet is None:
        raise ValueError(missing)
    verdict = gate.inspect('suggest_cats', sheet, gate.Truth())
    rows = lambda head: [dict(r.facts, **r.judged) for r in verdict.rows.get(head, []) if r.ok]
    cats = [c for c in rows('categories') if str(c.get('name')).strip() != OTHER]
    if not cats:
        raise ValueError('沒有一類收得下來' + (':' + '；'.join(verdict.problems)[:120] if verdict.problems else ''))
    icon = next((c.get('icon') for c in current if isinstance(c, dict) and c.get('name') == OTHER), '') or '•'
    return {'categories': cats + [{'name': OTHER, 'icon': icon, 'match': ''}], 'tags': rows('tags'),
            'why': str(verdict.facts.get('why') or ''), 'problems': verdict.problems, 'by': gate.JUDGED}


if __name__ == '__main__':
    sys.exit(main())
