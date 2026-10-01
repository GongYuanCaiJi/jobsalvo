#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo —— 產一份全部是假資料的看板:五家虛構公司、各階段都有卡、答案庫與面試題庫也有東西。

兩個用途:
  1. 第一次試用、截圖、寫文件:python3 tools/demo.py /tmp/demo.html,再用
     uv run python tools/board_server.py --state /tmp/demo.html --port 8898 打開。
  2. 看板介面規矩的檢查(board_check)一律跑在這份上面,不跑在使用者的資料上:
     檢查結果不會因為誰的看板剛好是空的、或剛好沒有某種卡而變。
"""
import os, sys, json, datetime, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd
import config as cf

COMPANIES = [
    ('acme-cloud', 'Acme Cloud', 'lever', ['Backend Engineer', 'Site Reliability Engineer', 'Data Engineer']),
    ('nimbus-data', 'Nimbus Data', 'greenhouse', ['Machine Learning Engineer', 'Data Analyst', 'Product Manager']),
    ('orbit-pay', 'Orbit Pay', 'ashby', ['Risk Analyst', 'Platform Engineer', 'Operations Specialist']),
    ('kite-labs', 'Kite Labs', 'lever', ['Frontend Engineer', 'Software Engineer Intern']),
    ('harbor-health', 'Harbor Health', 'greenhouse', ['Security Engineer', 'Product Designer', 'Customer Success Manager']),
]
HOSTS = {'lever': 'jobs.lever.co', 'greenhouse': 'job-boards.greenhouse.io', 'ashby': 'jobs.ashbyhq.com'}


def jobs():
    today = datetime.date.today()
    out, n = [], 0
    for slug, name, ats, titles in COMPANIES:
        for t in titles:
            n += 1
            url = f'https://{HOSTS[ats]}/{slug}/{1000 + n}'
            out.append({
                'id': url, 'target': f'**{t} · {name}**（[{ats.title()}]({url})）', 'chan': '直投', 'ammo': '', 'note': '',
                'bk': False, 'dead': False,
                'sum': {'fit': f'{name} 的 {t}:工作內容跟履歷上的經驗相近(示範資料)。',
                        'co': f'{name} 是一家虛構公司,只用來示範。', 'loc': 'Taipei / Remote',
                        'salary': '面議', 'bar': f'{2 + n % 4} 年以上相關經驗;英文可溝通', 'deadline': '未公開'},
                'added': (today - datetime.timedelta(days=n % 9)).isoformat(),
                'posted_at': (today - datetime.timedelta(days=3 + n * 2)).isoformat(),
                'posted_src': ats,
                'source_platform': '104.com.tw' if n == 10 else 'linkedin.com' if n == 11 else '',
            })
            if n == 1:   # 判斷那一段看出來的幽靈職缺(示範卡片上的標記)
                out[-1]['src'] = {'risk': {'kind': 'ghost', 'why': '刊登已經 120 天,JD 寫「持續招募」(示範資料)'}}
    return out


def marks(js, resumes):
    ids = [j['id'] for j in js]
    resume_id = resumes[0]['id'] if resumes else ''
    fb = {
        ids[0]: {'s': 'like', 'n': '喜歡這種做基礎建設的'},
        ids[1]: {'s': 'grow', 'n': '要補 SRE 經驗,但方向對'},
        ids[2]: {'s': 'meh'},
        ids[3]: {'s': 'dislike', 'n': '太偏研究'},
        ids[4]: {'s': 'like', 'app': 'prep'},
        ids[5]: {'s': 'like', 'app': 'prep'},
        ids[6]: {'s': 'like', 'app': 'ready'},
        ids[7]: {'s': 'grow', 'app': 'ready'},
        ids[8]: {'s': 'like', 'app': 'ship'},
        ids[9]: {'s': 'like', 'app': 'sent', 'ds': 'sent', 'sent_by': 'manual',
                 'sent_at': (datetime.date.today() - datetime.timedelta(days=5)).isoformat(),
                 'oc': 'rej',
                 'oc_at': {'rej': (datetime.date.today() - datetime.timedelta(days=2)).isoformat()},
                 'replies': {'items': [{
                     'id': 'demo-rejection', 'src': 'mail',
                     'date': (datetime.date.today() - datetime.timedelta(days=2)).isoformat(),
                     'subject': 'Application update',
                     'snippet': 'A fictional rejection with a recorded reason.',
                     'link': 'https://mail.example/thread/demo-rejection',
                     'kind': 'reject',
                     'reason': '示範拒絕理由：職務需要的經驗與目前背景不同。',
                 }]}},
        ids[10]: {'s': 'like', 'app': 'sent', 'ds': 'sent', 'sent_by': 'manual',
                  'sent_at': (datetime.date.today() - datetime.timedelta(days=12)).isoformat()},
        '__notes__': [{'id': 'n1', 't': '想多看遠端的缺(示範)'}],
        '__ans__': [{'k': 'nationality', 'q': '國籍', 'v': 'Taiwan', 'zh': '台灣', 'at': '2026-01-01'},
                    {'k': 'notice', 'q': '最快什麼時候可以到職', 'v': 'Within one month of an offer', 'zh': '錄取後一個月內',
                     'at': '2026-01-01'}],
    }
    if resume_id:
        import ship   # 寄出的是哪一份只由 ship.record_sent 寫
        ship.record_sent(fb, ids[9], version=f'zh-{resume_id}')
        ship.record_sent(fb, ids[10], version=f'en-{resume_id}')
    return fb


def bank():
    items = []
    for i, (st, raw) in enumerate((('ok', True), ('wip', True), ('todo', True), ('todo', False))):
        items.append({'id': f'q{i}', 't': ['自我介紹', '為什麼想換工作', '講一個你解決過的難題', '你有什麼想問我們的'][i],
                      'cat': ['自我介紹', '職涯方向與動機', '你做過的事', '反問面試官'][i], 'sub': '',
                      'ask': '<p>(示範題目)</p>', 'focus': '', 'covers': '', 'lim': 60 if i == 0 else 0,
                      'st': st, 'raw': raw, 'asked': ['Nimbus Data'] if i == 1 else [],
                      'b': [['h', '重點'], ['s', ['第一段逐字稿(示範)。', '第二段逐字稿(示範)。']]] if st != 'todo' else [],
                      'picks': []})
    return {'cps': 4.2, 'cats': ['自我介紹', '職涯方向與動機', '你做過的事', '反問面試官'], 'items': items}


def extra_jobs(n):
    """效能檢查用:再多 n 張假卡,分散在 40 家虛構公司(看板跟真的在用的一樣大)。"""
    today = datetime.date.today()
    out = []
    for i in range(n):
        co = f'示範公司 {i % 40 + 1:02d}'
        url = f'https://jobs.lever.co/demo-{i % 40 + 1:02d}/{9000 + i}'
        out.append({'id': url, 'target': f'**示範職缺 {i + 1} · {co}**([Lever]({url}))', 'chan': '直投', 'ammo': '', 'note': '',
                    'bk': False, 'dead': False,
                    'sum': {'fit': '示範資料', 'co': f'{co} 是虛構公司', 'loc': 'Taipei', 'salary': '面議',
                            'bar': '示範', 'deadline': '未公開'},
                    'added': (today - datetime.timedelta(days=i % 30)).isoformat()})
    return out


def build(path, extra=0):
    read = lambda n: open(os.path.join(cf.BOARD_SRC, n), encoding='utf-8').read()
    js = jobs()
    resumes = [item for item in cf.RESUMES.values() if item.get('enabled', True)]
    for j in js[6:11]:
        if resumes:
            j['resume'] = {'recommend': resumes[0]['id'], 'lang': 'zh', 'pick_why': '示範:這份履歷最符合 JD'}
    marked = marks(js, resumes)
    js = js + extra_jobs(extra)
    # 投遞前驗收跑過、都過關:真的看板在卡進「可以投了」之前一定跑過(沒跑過連待你決定都推不過去)
    status = {'schema_version': 2, 'at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'checked_links': True, 'issues': []}
    data = {'jobs': js, 'status': status, 'research': [], 'bank': bank()}
    doc = bd.assemble(read('board.css'), bd.stat_first(read('header.html'), data), '', data,
                      json.dumps(marked, ensure_ascii=False), read('board.js'))
    bd.write_doc(path, doc)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out')
    ap.add_argument('--extra', type=int, default=0, help='再多幾張假卡(效能檢查用)')
    a = ap.parse_args()
    print('示範看板 →', build(a.out, a.extra))


if __name__ == '__main__':
    main()
