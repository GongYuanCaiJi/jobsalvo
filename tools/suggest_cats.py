#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
suggest_cats —— 看板「⚙ 設定 → 分類」的「讓 agent 建議」:照使用者的履歷和他表過態的卡,建議類別與標籤。

類別是單一軸(這份工作在做什麼),標籤是跟它正交的特徵(實習、遠端、某個產業…)。agent 只給建議,
寫進 <tmp>/suggest.json;使用者在設定頁看過、改過、按「套用」才寫進設定。

用法:python3 tools/suggest_cats.py [--board B]
"""
import os, sys, json, time, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import jobrun

SP = os.environ.get('SUGGEST_TMP') or cf.TMP
STATUS = 'suggest_status.json'
OUT = 'suggest.json'


def prompt(out, fb, jobs):
    import prefs
    cs = prefs.cards(fb, jobs)
    seen = '\n'.join(f"- {c['title']}({c['mark']})" + (f"｜他說「{c['note']}」" if c['note'] else '') for c in cs[:150])
    titles = '\n'.join('- ' + prefs.title_of(j) for j in jobs[:300])
    now = json.dumps({'categories': cf.C['board']['categories'], 'tags': cf.C['board']['tags']}, ensure_ascii=False)
    return ('幫使用者設計求職看板的分類。\n\n'
            '類別:單一軸,一張職缺只屬於一類,依「這份工作在做什麼」分,4 到 7 類,最後一類固定叫「其他」。'
            '標籤:跟類別正交的特徵(例如實習、遠端、某個產業),一張卡可以有好幾個,0 到 8 個。\n'
            '每一個都給:name(短、使用者看得懂的中文)、icon(一個 emoji,標籤不用)、'
            'match(JavaScript 正規表示式,不分大小寫,比對職稱與內文;中英文關鍵字都寫進去,用 | 分隔;「其他」的 match 留空)。\n'
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
    try:
        os.remove(out)
    except OSError:
        pass
    st({'phase': 'agent', 'pid': os.getpid(), 't0': t0, 'step': 'agent 在想怎麼分'})
    fb, jobs = prefs.load(a.board)
    result = ar.run(prompt(out, fb, jobs), os.path.join(SP, 'suggest_agent.out'), cf.HOME,
                    timeout=1800, browser_required=False, board=a.board)
    if not result.ok:
        msg = result.message()
        st({'phase': 'failed', 't0': t0, 'finished_at': time.time(), 'msg': msg})
        try:
            import agent_report
            agent_report.report('分類建議', msg, need='在「⚙ 設定 → 分類」按「看紀錄」確認後再重試', live=a.board)
        except Exception:  # noqa: S110
            pass
        return 1
    try:
        with open(out, encoding='utf-8') as f:
            s = json.load(f)
        assert isinstance(s.get('categories'), list) and s['categories']
        st({'phase': 'done', 't0': t0, 'finished_at': time.time(), 'msg': '建議好了,到「⚙ 設定 → 分類」看'})
    except Exception as e:
        st({'phase': 'failed', 't0': t0, 'finished_at': time.time(), 'msg': f'agent 沒交出可用的建議({str(e)[:80]})'})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
