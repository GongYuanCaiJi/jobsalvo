"""Per-card resume and attachment customization workflow."""
import argparse
import contextlib
import copy
import difflib
import hashlib
import json
import os
import re
import subprocess
import time
import uuid

import agent_report
import agent_run
import board_doc as bd
import evidence
import card
import config as cf
import jobrun
import settings_api as sa
import ship


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SKILL = os.path.join(HERE, 'default_custom_skill.md')
STATUS = 'customize_status.json'
MAX_FEEDBACK = 2000


def _read_board(path):
    parsed = bd.load(path)
    return parsed['data'], json.loads(parsed['fb'])


def _job(data, url):
    return next((j for j in (data.get('jobs') or []) if j.get('id') == url), None)


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def _run_id():
    return time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]


def _artifact(url, item_id, run_id):
    digest = hashlib.sha256(item_id.encode('utf-8')).hexdigest()[:12]
    return f'custom/{card.card_id_from_url(url)}/generated/{digest}-{run_id}.pdf'


def _read_skill(item):
    rel = item.get('skill') or ''
    if not rel:
        path = DEFAULT_SKILL
    else:
        if (not isinstance(rel, str) or not rel.startswith('custom/skills/') or
                not rel.lower().endswith(('.md', '.txt'))):
            return '', f'指定的改履歷的規則路徑無效:{rel}'
        path = sa.safe_rel(rel)
        if not path or not os.path.isfile(path):
            return '', f'指定的改履歷的規則找不到:{rel}'
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read().strip()
    except (OSError, UnicodeError) as e:
        return '', f'讀取改履歷的規則失敗:{str(e)[:100]}'
    text = re.sub(r'^<!-- jobsalvo-skill: .*? -->\s*', '', text, count=1)
    return text, ''


def feedback_records(fb, jobs=()):
    names = {j.get('id'): card.name(j) for j in jobs or []}
    rows = []
    for url, state in fb.items():
        if not isinstance(state, dict):
            continue
        docs = state.get('custom_docs')
        if not isinstance(docs, dict):
            continue
        for item_id, entry in docs.items():
            if not isinstance(entry, dict):
                continue
            for feedback in entry.get('feedbacks') or []:
                if isinstance(feedback, dict) and not feedback.get('processed_at') and str(feedback.get('text') or '').strip():
                    rows.append({
                        'id': str(feedback.get('id') or ''), 'url': url, 'card': names.get(url, url),
                        'item_id': item_id, 'file': entry.get('name') or item_id,
                        'text': str(feedback.get('text') or '').strip(), 'at': feedback.get('at') or '',
                    })
    return rows


def build_prompt(url, title, items, feedback, report_path, jd=''):
    payload = []
    for item in items:
        skill, error = _read_skill(item)
        if error:
            raise ValueError(error)
        state = item.get('entry') or {}
        own_feedback = [str(x.get('text') or '').strip() for x in state.get('feedbacks') or []
                        if isinstance(x, dict) and str(x.get('text') or '').strip()]
        if item['kind'] == 'resume':
            card_feedback = str((item.get('card_state') or {}).get('rzfb') or '').strip()
            if card_feedback:
                own_feedback.insert(0, card_feedback)
        payload.append({
            'id': item['id'], 'type': '履歷' if item['kind'] == 'resume' else '附件',
            'name': item['name'], 'original_file': item['source'],
            'skill_text': skill, 'feedback_for_this_file': own_feedback,
            'output_pdf': item['output_abs'], 'max_pages': item.get('page_limit_pages'),
        })
    return (
        '你正在 jobsalvo 看板替一張卡客製履歷與附件。\n'
        '下面是程式從職缺網址抓好的 JD 原文，按每份檔案自己的客製 skill 修改。這一輪不上網、不開任何瀏覽器。\n'
        '只能讀原檔與 skill；不要改原檔、設定、skill 或看板資料。不要編造使用者沒有的經歷、能力、數字或成果。\n'
        '每一份檔案都必須輸出一個 PDF 到指定的 output_pdf，頁數不得超過該檔案的 max_pages。Markdown 原稿的頁數是內建排版產生之可投遞 PDF 的頁數。程式會檢查輸出檔及頁數。\n'
        '一張卡的所有檔案由你一次完成。若某份檔案不能完成，仍需說明原因，其他可完成的檔案照常輸出。\n\n'
        f'卡片:{title}\n職缺網址:{url}\n--- JD 原文開始 ---\n{jd}\n--- JD 原文結束 ---\n\n'
        '本輪檔案與使用者指示(JSON):\n'
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + '\n\n其他卡片尚未處理的內容回饋(JSON):\n'
        + json.dumps(feedback, ensure_ascii=False, indent=2)
        + '\n\n請把跨卡回饋中「至少兩張卡都提到的同一個問題」整理到 '
        + report_path + '，格式為 {"reports":[{"issue":"問題","recommendation":"建議調整哪份履歷或哪個 skill",'
        + '"feedback_ids":["提到這個問題的那幾則回饋的 id"]}]}。'
        + '每個問題只列一次，feedback_ids 列出歸進這個問題的回饋 id(提到幾次程式照這個數);只有一張卡提過的問題不要列。'
        + '程式會核對每個 id 都是上面給你的、而且至少兩張卡提到，對不上的那一則不收。'
        + '沒有重複問題也要寫 {"reports":[]}。'
        + '只回報建議，不要修改任何設定或 skill。全部完成後 stdout 只印 @@CUSTOMIZE_DONE@@。\n'
    )


def _card_items(data, fb, url):
    job = _job(data, url)
    if not job:
        raise ValueError('看板找不到這張卡')
    state = fb.get(url) or {}
    items = ship.documents(job, fb)
    for item in items:
        item['card_state'] = state
    return job, state, items


def list_files(url, board=None):
    """回傳卡片實際會附上的檔案,供看板顯示選取清單。"""
    board = board or bd.LIVE
    data, fb = _read_board(board)
    _job_data, _state, items = _card_items(data, fb, url)
    skill_names = {x['path']: x['name'] for x in sa.skill_files()}
    return [{
        'id': item['id'], 'kind': item['kind'], 'name': item['name'],
        'skill': item.get('skill') or '',
        'skill_name': skill_names.get(item.get('skill') or '', ''),
        'default_checked': bool(item.get('skill')),
        'status': (item.get('entry') or {}).get('status') or '',
        'error': (item.get('entry') or {}).get('error') or (item.get('entry') or {}).get('last_error') or '',
    } for item in items]


def swap_files(fb, url, why):
    """這張要寄的檔換了:照狀態表送「換檔」(填好的頁上傳的是舊檔,要重填)。表上不准(正在送出)回原因,
    呼叫的在看板鎖內就不寫(#338:以前換了檔卻默默擋掉事件,送出去的是頁上的舊檔)。"""
    import delivery_state as ds
    import next_step
    busy = next_step.refuse(fb, url, 'files_changed')     # 正在填、正在送出、送出結果不明:不准換檔(#341)
    if busy:
        return busy
    try:
        ds.fire(fb, url, 'files_changed', why=why)
    except ds.Forbidden as e:
        return str(e)


def _put_entries(fb, url, updates):
    """客製紀錄寫進這張卡(呼叫的已經在看板鎖內)。換了檔而狀態表不准就回原因(呼叫的就不寫)。"""
    state = fb.setdefault(url, {})
    docs = state.setdefault('custom_docs', {})
    for item_id, entry in updates.items():
        prev = docs.get(item_id) or {}
        docs[item_id] = copy.deepcopy(entry)
        # 換成另一份檔(新收下、換了檔):agent 已經填好的那一頁上傳的是舊檔,要重填。
        # 只看檔有沒有真的換:已收下的再客製一次沒成功,會退回原本收下的那一份(path 沒變),頁上的就是它
        if entry.get('status') == 'accepted' and prev.get('path') != entry.get('path'):
            why = swap_files(fb, url, f'「{entry.get("name") or item_id}」換成客製版了')
            if why:
                return why


def _set_entries(board, url, updates):
    return bd.set_fb(lambda fb: _put_entries(fb, url, updates), live=board, by='customize')


def _in_lock(board, decide):
    """讀這張卡現在的樣子、判斷、寫回,整段在看板鎖內(#308):以前鎖外讀、鎖內整份寫回,
    兩台裝置同時按收下和退回,後寫的蓋掉先寫的、兩邊都說成功。
    decide(data, fb) 就地改 fb;不能做就回原因(那就不寫)。回 (做了沒, 原因)。"""
    why = []

    def put(d):
        problem = decide(d['data'], d['fb'])
        if problem:
            why.append(problem)
            return bd.SKIP
    bd.rewrite(put, bd.target(board), 'customize')
    return (False, why[0]) if why else (True, '')


def _review(url, item_id, board, change):
    """收下或退回一份在等你看的客製版:在看板鎖內確認它還在等你看,change(entry) 改好(回原因就不做),寫回。"""
    def decide(data, fb):
        _job_data, state, items = _card_items(data, fb, url)
        if not any(item['id'] == item_id for item in items):
            return '這張卡沒有這份檔案'
        entry = copy.deepcopy((state.get('custom_docs') or {}).get(item_id) or {})
        if entry.get('status') != 'review':
            return '這份客製版目前不在等你看'
        problem = change(entry)
        if problem:
            return problem
        return _put_entries(fb, url, {item_id: entry})
    return _in_lock(board, decide)


def _report_invalid(board, url, title, item, reason, reporter=None):
    reporter = reporter or agent_report.report
    try:
        reporter(
            '客製流程', f'{title} 的「{item["name"]}」沒有可收下的客製版：{reason}',
            '查看卡片上的原因，修正原檔或客製 skill 後再重跑。', job=url, live=board,
        )
        return True
    except Exception:  # noqa: BLE001 — 原因已經寫在卡上(這則回報只是再提醒一次),回報寫不進去不擋收尾
        return False


def _page_limit_source(url, item):
    """Return the PDF whose page count is the customization ceiling."""
    source = item.get('source')
    if not source or not os.path.isfile(source):
        return '', '原始檔不存在，無法檢查頁數'
    if source.lower().endswith('.pdf'):
        return source, ''
    directory = ship.folder(url)
    info = ship.info(url)
    sources = info.get('sources') if isinstance(info, dict) else None
    name = sources.get(item.get('id')) if isinstance(sources, dict) else None
    files = info.get('files') if isinstance(info, dict) else None
    if (not directory or not isinstance(name, str) or not name.lower().endswith('.pdf') or
            os.path.basename(name) != name or not isinstance(files, list) or name not in files):
        return '', f'{item.get("name") or "原始檔"} 沒有可用的 pipeline PDF，無法檢查頁數上限'
    path = os.path.join(directory, name)
    if (not os.path.isfile(path) or
            os.path.dirname(os.path.realpath(path)) != os.path.realpath(directory)):
        return '', f'{item.get("name") or "原始檔"} 的 pipeline PDF 不存在或路徑無效'
    return path, ''


def validate_output(source, output, page_counter=None, *, page_source=None, source_pages=None):
    """檢查 agent 產出的 PDF;回 (問題, 原檔頁數, 產出頁數)。"""
    if not os.path.isfile(output):
        return 'agent 沒有產出這份檔案', None, None
    if not output.lower().endswith('.pdf'):
        return '產出檔不是 PDF', None, None
    try:
        with open(output, 'rb') as f:
            if not f.read(5).startswith(b'%PDF-'):
                return '產出檔不是有效的 PDF', None, None
    except OSError:
        return '產出檔讀不到', None, None
    if not source or not os.path.isfile(source):
        return '原始檔不存在，無法檢查頁數', None, None
    page_counter = page_counter or sa.pdf_pages
    if page_source is None:
        if source.lower().endswith('.pdf'):
            page_source = source
        else:
            return '沒有原始檔的 pipeline PDF，無法檢查頁數上限', None, None
    if not page_source or not page_source.lower().endswith('.pdf') or not os.path.isfile(page_source):
        return '原始檔的頁數基準 PDF 不存在或無效', None, None
    try:
        if source_pages is None:
            source_pages = page_counter(page_source)
        output_pages = page_counter(output)
    except Exception as e:  # noqa: BLE001 — 讀不了的原因照實回給呼叫的地方,寫在卡上
        return f'PDF 讀取失敗:{str(e)[:120]}', None, None
    if not output_pages:
        return '客製版 PDF 沒有可讀取的頁面', source_pages, output_pages
    if not source_pages:
        return '頁數基準 PDF 沒有可讀取的頁面', source_pages, output_pages
    if output_pages > source_pages:
        return f'客製版有 {output_pages} 頁，超過原檔 {source_pages} 頁', source_pages, output_pages
    return '', source_pages, output_pages


def _still_running(entry, run_id):
    """這份還是這一輪在客製的。跑的期間他自己上傳、改回原始檔,就以他的為準:這一輪的產出不收、失敗也不算他的。"""
    return entry.get('status') == 'working' and entry.get('run_id') == run_id


def finish_outputs(board, url, items, run_id, feedback_ids=(), report_path=None,
                   page_counter=None, reporter=None, report_writer=None):
    """收下 worker 的檔案,只把通過檢查的版本放到「等你看」。"""
    data, fb = _read_board(board)
    job, state, current = _card_items(data, fb, url)
    by_id = {item['id']: item for item in current}
    title = card.name(job)
    updates = {}
    failures = []
    for original in items:
        item_id = original['id']
        item = by_id.get(item_id) or original
        old = copy.deepcopy((state.get('custom_docs') or {}).get(item_id) or {})
        if not _still_running(old, run_id):
            continue                     # 以前照樣改回「等你看」,收下還會把他上傳的換掉
        output_rel = original['output_rel']
        output = sa.safe_rel(output_rel)
        source = item.get('source')
        reason = ''
        if not item.get('source') or ship._digest(source) != original.get('source_sig'):
            reason = '原始檔在客製期間不存在或有變更，請重新客製'
        elif (not original.get('page_source') or
              ship._digest(original['page_source']) != original.get('page_source_sig')):
            reason = '頁數基準 PDF 在客製期間不存在或有變更，請重新客製'
        else:
            reason, source_pages, output_pages = validate_output(
                source, output or '', page_counter, page_source=original['page_source'],
                source_pages=original['page_limit_pages'],
            )
        if reason:
            _revert(old, reason)
            failures.append((item, reason))
        else:
            old.update({
                'id': item_id, 'kind': item['kind'], 'name': item['name'], 'status': 'review',
                'candidate_path': output_rel, 'candidate_source_sig': original['source_sig'],
                'candidate_pages': output_pages, 'source_pages': source_pages,
                'run_id': run_id, 'updated_at': _now(),
            })
            old.pop('error', None)
            old.pop('last_error', None)
            old.pop('previous_status', None)
        updates[item_id] = old
    if updates:
        _set_entries(board, url, updates)
    for item, reason in failures:
        _report_invalid(board, url, title, item, reason, reporter)
    feedback_ok = process_feedback_reports(board, report_path, feedback_ids, report_writer)
    return {'ok': not failures, 'failures': [reason for _, reason in failures], 'feedback_processed': feedback_ok}


def process_feedback_reports(board, path, feedback_ids=(), report_writer=None):
    """跨卡回饋的整理(交件單)經安檢門:歸進來的回饋都要是這一輪給它的、至少兩張卡提到;對不上的那一則不收、照實回報。
    哪幾則算同一個問題是 agent 判斷:回報裡標明、附那幾則回饋的原文;提到幾次程式照它列的回饋數。"""
    if not feedback_ids or not path or not os.path.isfile(path):
        return False
    import gate
    writer = report_writer or agent_report.report
    try:
        payload, _missing = gate.read(os.path.dirname(path), 'customize', where=path)
        if payload is None or not isinstance(payload.get('reports'), list):
            return False
        data, fb = _read_board(board)
        wanted_ids = set(feedback_ids)
        given = {r['id']: r for r in feedback_records(fb, data.get('jobs') or []) if r['id'] in wanted_ids}
        verdict = gate.inspect('customize', payload, gate.Truth(feedback=given))
        covered = set()
        for row in verdict.rows.get('reports', []):
            if not row.ok:            # 安檢門擋下:這一則不收,講清楚哪一格、agent 說什麼、實際是什麼
                writer('客製回饋', 'agent 交的跨卡回饋整理對不上,這一則不收:' + '；'.join(row.problems)[:300],
                       '不用你處理:那幾則回饋留著,下一輪再整理', live=board)
                continue
            issue = str(row.judged.get('issue') or '').strip()
            recommendation = str(row.facts.get('recommendation') or '').strip()
            ids = list(dict.fromkeys(row.facts.get('feedback_ids') or []))
            if not issue or not recommendation:
                continue
            quotes = '、'.join(f'「{given[i]["text"][:60]}」({given[i]["card"][:20]})' for i in ids[:6])
            msg = f'{gate.JUDGED}這幾則回饋是同一個問題:{issue}(回饋原文:{quotes})'
            for _ in range(min(len(ids), 100)):
                writer('客製回饋', msg, recommendation, live=board)
            covered.update(ids)
    except Exception:  # noqa: BLE001 — agent 交的整理檔什麼樣子都有可能;處理不了就不標成處理過,下一輪照樣交給 agent
        return False
    # 只標歸進回報的那幾則:只提過一次的留著,之後別張卡也提到才湊得到兩次。
    # 以前這一輪交給 agent 的全部標成處理過,同一個問題幾乎永遠湊不到兩次
    wanted = set(feedback_ids) & covered
    if not wanted:
        return True
    def mutate(fb):
        for state in fb.values():
            docs = state.get('custom_docs') if isinstance(state, dict) else None
            if not isinstance(docs, dict):
                continue
            for entry in docs.values():
                if not isinstance(entry, dict):
                    continue
                for feedback in entry.get('feedbacks') or []:
                    if isinstance(feedback, dict) and feedback.get('id') in wanted and not feedback.get('processed_at'):
                        feedback['processed_at'] = _now()
    bd.set_fb(mutate, live=board, by='customize_feedback')
    return True


def _text(path):
    if not path or not os.path.isfile(path):
        return ''
    if path.lower().endswith('.pdf'):
        try:
            return sa.pdf_text(path)
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            # 讀不出來照實說是哪一份:當成空的,差異會變成「整份都刪掉了」
            raise ValueError(f'{os.path.basename(path)} 抽不出文字:{str(e)[:120]}') from e
    if path.lower().endswith(('.md', '.txt')):
        try:
            with open(path, encoding='utf-8') as f:
                return f.read()
        except (OSError, UnicodeError):
            return ''
    return ''


def diff_for(url, item_id, board=None):
    board = board or bd.LIVE
    data, fb = _read_board(board)
    _job_data, _state, items = _card_items(data, fb, url)
    item = next((x for x in items if x['id'] == item_id), None)
    if not item:
        raise ValueError('這張卡沒有這份檔案')
    path = item.get('custom_path')
    entry = item.get('entry') or {}
    if not path or not os.path.isfile(path):
        raise ValueError(entry.get('error') or '找不到客製版')
    before, after = _text(item.get('source')), _text(path)
    if not before and not after:
        raise ValueError('原檔或客製版抽不出文字')
    diff = '\n'.join(difflib.unified_diff(
        before.splitlines(), after.splitlines(), fromfile='原檔', tofile='客製版', lineterm=''))
    return {'name': item['name'], 'diff': diff[:200000] or '兩份檔案的文字相同。'}


def accept(url, item_id, board=None):
    def change(entry):
        path = ship._safe_custom_path(entry.get('candidate_path'))
        if not path or not os.path.isfile(path):
            return '客製版檔案不見了，不能收下'
        entry['path'] = entry.pop('candidate_path')
        entry['source_sig'] = entry.pop('candidate_source_sig', '')
        entry['status'] = 'accepted'
        entry['accepted_at'] = _now()
        entry.pop('error', None)
    return _review(url, item_id, board or bd.LIVE, change)


def reject(url, item_id, note, board=None):
    note = re.sub(r'\s+', ' ', str(note or '')).strip()[:MAX_FEEDBACK]
    if not note:
        return False, '寫一下哪裡不對，agent 才知道要改什麼'

    def change(entry):
        entry['status'] = 'rework'
        entry['error'] = ''
        entry.setdefault('feedbacks', []).append({
            'id': uuid.uuid4().hex[:12],
            'at': _now(), 'text': note,
        })
    return _review(url, item_id, board or bd.LIVE, change)


def clear(url, item_id, board=None):
    """移除一份已收下版本的引用,不刪使用者資料夾裡的原檔。整段在看板鎖內判斷、寫(#308)。"""
    def decide(data, all_fb):
        _job_data, state, items = _card_items(data, all_fb, url)
        legacy_resume = item_id == 'resume:legacy' and bool(state.get('custom_file'))
        current = legacy_resume or any(x['id'] == item_id for x in items)
        # 換了履歷、語言或附件之後留下的舊紀錄(不在這張現在要寄的檔裡)也能清掉:卡上只給這一顆
        if not current and item_id not in (state.get('custom_docs') or {}):
            return '這張卡沒有這份檔案'
        card_state = all_fb.setdefault(url, {})
        docs = card_state.get('custom_docs')
        if isinstance(docs, dict):
            docs.pop(item_id, None)
            if not docs:
                card_state.pop('custom_docs', None)
        if not current:
            return None                  # 舊紀錄本來就沒寄出去:要寄的檔沒變,已填好的頁和核准都不用動
        if item_id.startswith('resume:'):
            card_state.pop('custom_file', None)
        return swap_files(all_fb, url, '客製版改回原始檔了')
    return _in_lock(board or bd.LIVE, decide)


def upload_custom(url, item_id, name, data, board=None):
    """使用者直接上傳一份客製 PDF;這是已收下狀態。檔先存好,卡上的紀錄在看板鎖內照現在的樣子寫(#308)。"""
    board = board or bd.LIVE
    filename = os.path.basename(str(name or 'custom.pdf')).replace('\\', '_')
    if not filename.lower().endswith('.pdf') or not data.startswith(b'%PDF-'):
        return None, '客製版要是 PDF'
    live_data, fb = _read_board(board)
    _job_data, _state, items = _card_items(live_data, fb, url)
    if not any(x['id'] == item_id for x in items):
        return None, '這張卡沒有這份檔案'
    digest = hashlib.sha256(item_id.encode('utf-8')).hexdigest()[:12]
    rel = f'custom/{card.card_id_from_url(url)}/uploaded/{digest}-{uuid.uuid4().hex[:8]}-{filename}'
    saved, error = sa.put_file(rel, data)
    if not saved:
        return None, error

    def decide(board_data, all_fb):
        _job_data, state, items = _card_items(board_data, all_fb, url)
        item = next((x for x in items if x['id'] == item_id), None)
        if not item:
            return '這張卡沒有這份檔案'
        entry = copy.deepcopy((state.get('custom_docs') or {}).get(item_id) or {})
        entry.update({
            'id': item_id, 'kind': item['kind'], 'name': item['name'], 'status': 'accepted',
            'path': saved, 'source_sig': ship._digest(item.get('source')),
            'uploaded_at': _now(),
        })
        entry.pop('candidate_path', None)
        entry.pop('candidate_source_sig', None)
        entry.pop('error', None)
        return _put_entries(all_fb, url, {item_id: entry})
    ok, why = _in_lock(board, decide)
    return (saved, '') if ok else (None, why)


class JobPageUnreadable(RuntimeError):
    pass


def _job_page_text(url):
    """程式抓職缺頁的文字(page_fetch:直接抓 → 閱讀代理 → ego)。抓不到就不派 agent,照實講。"""
    import page_fetch
    page = page_fetch.fetch(url)
    if page.readable:
        return page.text
    why = '職缺已下架' if page.status == 'closed' else '可能要登入,或網站擋程式讀取'
    raise JobPageUnreadable(f'程式讀不到這個職缺頁的 JD({why}),沒有派 agent 客製:'
                            + ('; '.join(page.errors)[:160] or page.status))


def run_customization(board, url, item_ids, sp=None, launcher=None, waiter=None):
    """派一個 agent 處理同一卡勾選的所有檔,並在同一個收尾檢查輸出。"""
    sp = sp or os.environ.get('CUSTOMIZE_TMP') or cf.TMP
    run_id = _run_id()
    run_dir = os.path.join(sp, card.card_id_from_url(url), run_id)
    os.makedirs(run_dir, exist_ok=True)
    status_path = os.path.join(sp, STATUS)
    t0 = time.time()
    data, fb = _read_board(board)
    job, state, all_items = _card_items(data, fb, url)
    by_id = {item['id']: item for item in all_items}
    ids = list(dict.fromkeys(item_ids))
    if not ids or any(item_id not in by_id for item_id in ids):
        raise ValueError('客製清單已過時，請重新打開卡片再選')
    if state.get('app') not in ('ready', 'ship'):
        raise ValueError('只有待你決定或可以投了的卡可以客製')
    selected, updates = [], {}
    for item_id in ids:
        item = copy.deepcopy(by_id[item_id])
        skill, error = _read_skill(item)
        if error:
            raise ValueError(error)
        source = item.get('source')
        page_source, page_error = _page_limit_source(url, item)
        if page_error:
            raise ValueError(page_error)
        try:
            page_limit_pages = sa.pdf_pages(page_source)
        except Exception as e:
            raise ValueError(f'頁數基準 PDF 讀取失敗:{str(e)[:120]}') from e
        if not page_limit_pages:
            raise ValueError('頁數基準 PDF 沒有可讀取的頁面')
        output_rel = _artifact(url, item_id, run_id)
        output_abs = sa.safe_rel(output_rel)
        if not output_abs:
            raise ValueError('客製版輸出路徑不安全')
        os.makedirs(os.path.dirname(output_abs), exist_ok=True)
        item.update({'output_rel': output_rel, 'output_abs': output_abs, 'skill_text': skill,
                     'source_sig': ship._digest(source), 'page_source': page_source,
                     'page_source_sig': ship._digest(page_source),
                     'page_limit_pages': page_limit_pages, 'run_id': run_id})
        old = copy.deepcopy((state.get('custom_docs') or {}).get(item_id) or {})
        item['entry'] = old
        previous = old.get('status') or ''
        old.update({'id': item_id, 'kind': item['kind'], 'name': item['name'],
                    'status': 'working', 'candidate_path': output_rel,
                    'candidate_source_sig': item['source_sig'], 'run_id': run_id,
                    'previous_status': previous, 'updated_at': _now()})
        old.pop('error', None)
        updates[item_id] = old
        selected.append(item)
    _set_entries(board, url, updates)
    _status = lambda value: jobrun.write(status_path, value)
    feedback = feedback_records(fb, data.get('jobs') or [])
    report_path = os.path.join(run_dir, 'feedback_reports.json')
    rnd = None
    prompt_path = os.path.join(run_dir, 'customize_prompt.txt')
    output_log = os.path.join(run_dir, 'customize.out')
    try:
        # graceful:看板按停止時先收工,只停 agent,已經寫好的客製版照常檢查(見 jobrun.control)
        _status({'phase': 'agent', 'pid': os.getpid(), 't0': t0, 'n': len(selected), 'url': url, 'graceful': True})
        # JD 由程式先抓好放進 prompt;客製的 agent 不給操作 Chrome 的能力、也不上網(#287,Codex、Claude 都一樣)。
        # 以前叫它「用可用的 Chrome」自己讀,卻不在代投那把鎖裡,也沒限制只准用 agent 專用的 Chrome
        jd = _job_page_text(url)
        prompt = build_prompt(url, card.name(job), selected, feedback, report_path, jd)
        with open(prompt_path, 'w', encoding='utf-8') as f:
            f.write(prompt)
        launch_override = None
        if launcher is not None:
            def launch_override(prompt_text, outfile, repo, _agent, **options):
                return launcher(prompt_text, outfile, repo, model='main', board=board,
                                chrome=options.get('chrome', False))
        with evidence.opened('customize', 'tailor', [url], board) as rnd:   # 證據記進這張卡(#315)
            result = agent_run.run(
                prompt, output_log, cf.HOME, model='main', board=board, web=False,
                launcher=launch_override, waiter=waiter,
            )
            rnd.handoff(report_path)
    except JobPageUnreadable as e:
        result = None
        worker_error = str(e)
    except FileNotFoundError as e:
        result = None
        worker_error = f'找不到 agent 執行程式:{str(e)[:120]}'
    except Exception as e:  # noqa: BLE001 — 這一輪最外層:原因照實寫進卡上與看板的回報
        result = None
        worker_error = f'客製工作失敗:{str(e)[:120]}'
    else:
        worker_error = '' if result.ok else f'agent 沒完成:{result.message()}'
    stopping = bool(worker_error) and result is not None and jobrun.finishing(status_path)
    if worker_error and not stopping:
        for item in selected:
            output = sa.safe_rel(item['output_rel'])
            if output and os.path.isfile(output):
                os.remove(output)
        _data, fb = _read_board(board)
        docs = (fb.get(url) or {}).get('custom_docs') or {}
        olds = {item['id']: copy.deepcopy(docs.get(item['id']) or {}) for item in selected}
        failed = {i: _revert(old, worker_error) for i, old in olds.items() if _still_running(old, run_id)}
        for item in selected:
            if item['id'] in failed:
                _report_invalid(board, url, card.name(job), item, worker_error)
        if failed:
            _set_entries(board, url, failed)
        _status({'phase': 'failed', 'n': len(selected), 'done': 0, 'msg': worker_error,
                 't0': t0, 'finished_at': time.time(), 'url': url})
        return {'ok': False, 'msg': worker_error}
    with (evidence.activated(rnd) if rnd is not None else contextlib.nullcontext()):   # 安檢門的比對結果記進同一輪
        result_data = finish_outputs(
            board, url, selected, run_id, [row['id'] for row in feedback], report_path,
        )
    if stopping:
        jobrun.clear_finish(status_path)
    _status({'phase': 'stopped' if stopping else 'done', 'n': len(selected),
             'done': len(selected) - len(result_data['failures']),
             'missing': [item['id'] for item in selected if not os.path.isfile(item['output_abs'])],
             'msg': '' if result_data['ok'] else '部分客製版未通過檢查，原因已寫在卡上',
             't0': t0, 'finished_at': time.time(), 'url': url})
    return result_data


def _revert(old, reason):
    """這一輪的客製版不收:有收下過的版本就留著,退回重寫中的照舊,其他標失敗;原因記上。"""
    previous = old.pop('previous_status', '')
    old['status'] = 'accepted' if old.get('path') else ('rework' if previous == 'rework' else 'failed')
    old['error'] = reason
    old.pop('candidate_path', None)
    old.pop('candidate_source_sig', None)
    return old


def main():
    ap = argparse.ArgumentParser(description='客製履歷與附件')
    ap.add_argument('--board', default=bd.LIVE)
    ap.add_argument('--url', required=True)
    ap.add_argument('--item', action='append', default=[])
    a = ap.parse_args()
    run_customization(a.board, a.url, a.item)


if __name__ == '__main__':
    main()
