#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feedback_dump —— 把「他對每個職缺的原話」從現行看板倒成一份檔,給所有找缺/寫摘要的 agent 當判準。

為什麼要這支:判準只能有一份,而且必須是使用者的原話。手抄或蒸餾過的偏好檔會失真,
  而且板上後來標的東西它們永遠不知道;好幾個版本並存,agent 讀到哪份全看運氣。
所以這份檔只保留逐張原話與職缺上下文;偏好筆記另外保存使用者自訂與 agent 假設。
跑它是冪等的,板沒變就寫出一樣的東西。

用法:
  uv run python tools/feedback_dump.py            # 讀現行看板,重寫偏好檔的自動區
  uv run python tools/feedback_dump.py --print    # 只印出來不寫檔
"""
import os,sys,json,argparse
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import board_doc as bd
import prefs
import config as cf
import card
OUT=cf.PREFS
START='<!-- 以下由 tools/feedback_dump.py 生成,不要手改 -->'
END='<!-- 自動區結束 -->'
TEMPLATE=('# 使用者逐張表態\n\n'+START+'\n'+END+'\n')

BUCKETS=[('like','★ 喜歡(要多找這種)'),
         ('grow','💪 可努力(方向對、他願意搆)'),
         ('meh','😐 還好(不夠好玩,別再送這種)'),
         ('dislike','✗ 不喜歡(絕對不要再送這種,含同型同公司)'),
         ('techerr','🔧 技術錯誤(連結壞了,不是不喜歡;要重找同一個缺)')]

def _cardsum(url):
    """那份缺真實長怎樣(對著真 JD 生成的摘要)。看他的反應「對著什麼樣的缺」才學得到他的邏輯。"""
    f=os.path.join(cf.SUMS,card.card_id_from_url(url)+'.json')
    try: return json.load(open(f,encoding='utf-8'))
    except Exception: return {}

def build(live=None, only_ids=None, heading=True):
    with open(live or bd.LIVE, encoding='utf-8') as f:
        p = bd.parse(f.read())
    fb = json.loads(p['fb']); jobs = p['data']['jobs']
    byid = {j['id']: j for j in jobs}
    only_ids = set(only_ids) if only_ids is not None else None
    bkt = {k: [] for k, _ in BUCKETS}; rm = []
    for k, v in fb.items():
        if k.startswith('__') or not isinstance(v, dict):
            continue
        if only_ids is not None and k not in only_ids:
            continue
        n = prefs.his_words(v.get('n'))    # 「原因」格裡 agent 接著寫的投遞紀錄不是他的話,切掉
        j = byid.get(k, {})
        summary = _cardsum(k) or (j.get('sum') if isinstance(j.get('sum'), dict) else {})
        if v.get('rm'):
            rm.append((k, j, card.name(j), n, summary)); continue
        s = v.get('s')
        if s not in bkt: continue
        bkt[s].append((k, j, card.name(j), n, summary))

    L = (['# 使用者逐張表態', ''] if heading else []) + [
         '每筆保留使用者標記、原話與職缺上下文。判斷時以原話為證據,不要把它改寫成規則。', '']

    def add_entry(card_id, job, title, words, summary):
        jd = _full_jd(job)
        L.append(f'- **{title or "(無題)"}**｜卡片代號={card_id}｜標記={_feedback_mark(job, fb)}'
                 + (f'｜使用者原話「{words}」' if words else ''))
        if jd:
            L.append('    - JD 全文:')
            L.extend('      ' + line for line in jd.splitlines())
        elif card_id:
            L.append('    - 職缺網址=' + card_id)
        bits = []
        if summary.get('co'): bits.append('公司在做什麼=' + summary['co'])
        if summary.get('bar'): bits.append('門檻=' + summary['bar'])
        if summary.get('fit'): bits.append('工作內容/對位=' + summary['fit'])
        if bits: L.append('    - 卡片摘要=' + '；'.join(bits))

    for status, label in BUCKETS:
        L.append(f'## {label}（{len(bkt[status])}）')
        if not bkt[status]:
            L.append('(目前沒有)')
        for row in bkt[status]:
            add_entry(*row)
        L.append('')

    L.append(f'## 他親手從看板移除的（{len(rm)}）')
    for row in rm:
        add_entry(*row)
    L.append('')
    if only_ids is None:
        notes = [x.get('t', '').strip() for x in (fb.get('__notes__') or []) if x.get('t', '').strip()]
        L.append(f'## 他在看板上寫的即時想法（{len(notes)}）')
        for note in notes:
            L.append('- ' + note.replace('\n', '\n  '))
        L.append('')
    return '\n'.join(L)


def _feedback_mark(job, fb):
    v = fb.get(job.get('id')) or {}
    if v.get('rm'): return '親手移除'
    return {'like': '喜歡', 'grow': '可努力', 'meh': '還好',
            'dislike': '不喜歡', 'techerr': '技術錯誤'}.get(v.get('s'), '已加入準備區' if v.get('app') else '')


def _full_jd(job):
    for key in ('jd', 'jd_text', 'job_description', 'description', 'description_text',
                'job_text', 'raw_jd'):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def feedback_state_path(note_path=None):
    note_path = note_path or prefs.PREF
    root, _ext = os.path.splitext(note_path)
    return root + '-feedback-state.json'


def feedback_delta(live=None, note_path=None):
    """回傳上次整理後有表態的卡,以及本次讀到的流水帳行數。"""
    journal = bd.journal_path(live or bd.LIVE)
    try:
        with open(journal, encoding='utf-8') as f:
            rows = f.readlines()
    except OSError:
        rows = []
    try:
        with open(feedback_state_path(note_path), encoding='utf-8') as f:
            after = int(json.load(f).get('journal_lines', 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        after = None
    if after is None or after > len(rows):
        ids = None
    else:
        ids = set()
        for raw in rows[max(after, 0):]:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            ids.update(k for k in (rec.get('d') or {}) if not k.startswith('__'))
    return build(live, only_ids=ids), len(rows)


def save_feedback_checkpoint(journal_lines, note_path=None, organized_at=None):
    path = feedback_state_path(note_path)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    if organized_at is None:
        import datetime
        organized_at = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'journal_lines': int(journal_lines), 'organized_at': organized_at},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--live',default=None); ap.add_argument('--print',dest='pr',action='store_true')
    a=ap.parse_args()
    body='\n'.join((START, build(a.live, heading=False), END))
    if a.pr: print(body); return
    prefs.ensure_note(legacy_path=cf.PREFS)
    try: doc=open(OUT,encoding='utf-8').read()
    except FileNotFoundError: doc=TEMPLATE
    if START not in doc or END not in doc: doc=doc.rstrip()+'\n\n'+START+'\n'+END+'\n'
    s=doc.index(START); e=doc.index(END)+len(END)
    new=doc[:s]+body+doc[e:]
    if new!=doc: open(OUT,'w',encoding='utf-8').write(new); print(f'更新 {OUT}')
    else: print('沒變動(板上回饋跟檔案一致)')

if __name__=='__main__': main()
