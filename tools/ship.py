#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ship —— 履歷這一段的接縫:每張卡用哪份履歷、哪個語言,以及它的可投遞夾。

jobsalvo 不管履歷怎麼寫、怎麼排版。它只認三件事:
  1. 履歷 × 語言:設定的 resume.resumes(在看板的「⚙ 設定」上傳),每份履歷一句 when(什麼樣的缺用它)、
     各語言一個檔(PDF 或 markdown 母稿)。跑準備區時 agent 讀 JD 判這張用哪份履歷、哪個語言,
     寫進卡片的 resume.recommend / resume.lang;使用者在看板上點過的(fb.variant / fb.lang)優先。
  2. 可投遞夾:<resume.ship_dir>/<卡名>-<sha1(網址)前12碼>/,含履歷、個別附件和程式產的合併版,
     ship.json 的 files 列個別檔, merged 列合併版。
  3. Markdown 母稿會以 headless Chrome 排成 PDF;既有 PDF 直接複製。可投遞夾再依序合併 files。
     某張卡可在「📎 這張用自己的檔」上傳,存在 fb[url].custom_file,用它代替該卡履歷。
     也可選多份檔交給同一個 agent,依各檔 skill 修改;等使用者收下或直接上傳 PDF 後,
     才替換可投遞夾中對應的檔案。

用法:python3 tools/ship.py            # 對「待你決定」「可投遞」的卡建好可投遞夾(跟 reconcile 做的一樣)
"""
import os, sys, re, json, glob, shutil, hashlib, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import board_doc as bd
import card
import pdf_tools as pdf
import pdf_preview
import markdown_pdf

STAGES = ('ready', 'ship')      # 有可投遞夾的階段
MERGED_FILE = 'merged.pdf'


def _safe(name):
    return re.sub(r'[\\/:*?"<>|\s]+', '_', name).strip('_')[:60] or 'job'


def resolve(j, fb):
    """回 (resume_id, lang)。卡片選擇優先,其次 agent 判斷;只接受已勾選的履歷。"""
    f = fb.get(j['id']) or {}
    rz = j.get('resume') or {}
    active = {rid for rid, item in cf.RESUMES.items() if item.get('enabled', True)}
    picked = f.get('resume_id') or f.get('variant')
    recommended = rz.get('recommend')
    # An explicit card choice is authoritative. If it was unchecked, fail instead of
    # silently substituting the agent's recommendation.
    resume_id = (picked if picked in active else '') if picked else (recommended if recommended in active else '')
    lang = f.get('lang') or rz.get('lang') or (cf.LANGS[0] if cf.LANGS else '')
    if lang not in cf.LANGS:
        lang = cf.LANGS[0] if cf.LANGS else ''
    return resume_id, lang


def _digest(path):
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                h.update(block)
    except OSError:
        return ''
    return h.hexdigest()


_SIG_CACHE = {}


def source_sig(path):
    """原始檔的簽章(跟客製紀錄的 source_sig 同一種算法)。看板每次載入都要算每一份,照大小、修改時間記住。"""
    try:
        st = os.stat(path)
    except (OSError, TypeError):
        return ''
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _SIG_CACHE:
        if len(_SIG_CACHE) > 256:
            _SIG_CACHE.clear()
        _SIG_CACHE[key] = _digest(path)
    return _SIG_CACHE[key]


def item_key(kind, item_id, lang):
    """一筆客製紀錄的 key:每份檔、每個語言各記一筆。換語言不會拿另一個語言的客製版去寄,換回來那一份也還在。"""
    return f'{kind}:{item_id}:{lang}'


def migrate_custom_keys(fb):
    """舊的客製紀錄 key 沒有語言(resume:<id>、attachment:<id>)。照紀錄裡原始檔的簽章認出是哪個語言的檔,
    改成 <kind>:<id>:<語言>;認不出來的(原始檔後來換過)留著不動,看板上列成「這張現在不寄這份」,可以清掉。
    resume:legacy(更早的 custom_file)不分語言,不動。回改了幾筆。"""
    def files_of(kind, item_id):
        if kind == 'resume':
            return (cf.RESUMES.get(item_id) or {}).get('files') or {}
        item = next((a for a in cf.ATTACHMENTS if str(a.get('id') or '') == item_id), None)
        return (item or {}).get('files') or {}

    moved = 0
    for state in fb.values():
        docs = state.get('custom_docs') if isinstance(state, dict) else None
        if not isinstance(docs, dict):
            continue
        for key in list(docs):
            kind, _, item_id = key.partition(':')
            entry = docs[key]
            if kind not in ('resume', 'attachment') or not item_id or ':' in item_id or key == 'resume:legacy' \
                    or not isinstance(entry, dict):
                continue
            want = {entry.get('source_sig'), entry.get('candidate_source_sig')} - {None, ''}
            langs = [lang for lang, rel in files_of(kind, item_id).items()
                     if rel and source_sig(cf.path(rel)) in want]
            new = item_key(kind, item_id, langs[0]) if len(langs) == 1 else ''
            if not new or new in docs:
                continue
            docs[new] = dict(docs.pop(key), id=new) if 'id' in entry else docs.pop(key)
            moved += 1
    return moved


def _custom_entry(fb, url, item_id, kind):
    f = fb.get(url) or {}
    docs = f.get('custom_docs') if isinstance(f.get('custom_docs'), dict) else {}
    entry = docs.get(item_id)
    if isinstance(entry, dict):
        return entry
    # Older cards used one custom_file for the selected resume. Keep reading it
    # as an already accepted resume override until the user replaces it.
    if kind == 'resume' and f.get('custom_file'):
        rel = str(f['custom_file'])
        return {'status': 'accepted', 'path': rel, 'name': os.path.basename(rel), 'legacy': True}
    return {}


def _safe_custom_path(rel):
    if not isinstance(rel, str):
        return None
    rel = rel.replace('\\', '/').lstrip('/')
    if not rel.startswith('custom/') or '..' in rel.split('/'):
        return None
    full = os.path.realpath(os.path.join(cf.HOME, rel))
    home = os.path.realpath(cf.HOME)
    return full if full.startswith(home + os.sep) else None


def _custom_path(entry, source):
    if entry.get('status') != 'accepted' or not entry.get('path'):
        return None
    path = _safe_custom_path(entry['path'])
    if not path or not os.path.isfile(path):
        return None
    expected = entry.get('source_sig')
    if expected and (not source or _digest(source) != expected):
        return None
    return path


def _effective_path(entry, source):
    path = _custom_path(entry, source) or source
    if path and path.lower().endswith(('.md', '.markdown')):
        return markdown_pdf.output_path(path)
    return path


def documents(j, fb):
    """列出這張卡會附上的履歷與附件，以及它們的客製狀態。"""
    resume_id, lang = resolve(j, fb)
    url = j['id']
    out = []
    resume = cf.RESUMES.get(resume_id) or {}
    src = cf.master(resume_id, lang) if resume_id else None
    if src or (fb.get(url) or {}).get('custom_file'):
        item_id = item_key('resume', resume_id, lang) if resume_id else 'resume:legacy'
        entry = _custom_entry(fb, url, item_id, 'resume')
        rel = (resume.get('files') or {}).get(lang, '')
        if not resume_id:
            rel = (fb.get(url) or {}).get('custom_file', '')
            src = cf.path(rel) if rel else None
        skill = str(resume.get('skill') or '')
        out.append({
            'id': item_id, 'kind': 'resume', 'name': str(resume.get('name') or cf.resume_name(resume_id) or '履歷'),
            'source': src, 'source_rel': rel, 'skill': skill, 'entry': entry,
            'custom_path': _safe_custom_path(entry.get('candidate_path') or entry.get('path')),
            'effective_path': _effective_path(entry, src),
            'style_path': cf.path((resume.get('styles') or {}).get(lang) or '') if resume_id else None,
            'stale': bool(entry.get('status') == 'accepted' and entry.get('source_sig') and src
                          and _digest(src) != entry.get('source_sig')),
        })
    for attachment in cf.ATTACHMENTS:
        if not attachment.get('enabled', True):
            continue
        allowed = attachment.get('resume_ids') or []
        if allowed and resume_id not in allowed:
            continue
        rel = (attachment.get('files') or {}).get(lang)
        if not rel:
            continue
        source = cf.path(rel)
        item_id = item_key('attachment', str(attachment.get('id') or ''), lang)
        entry = _custom_entry(fb, url, item_id, 'attachment')
        out.append({
            'id': item_id, 'kind': 'attachment', 'name': str(attachment.get('name') or os.path.basename(rel)),
            'source': source, 'source_rel': rel, 'skill': str(attachment.get('skill') or ''), 'entry': entry,
            'custom_path': _safe_custom_path(entry.get('candidate_path') or entry.get('path')),
            'effective_path': _effective_path(entry, source),
            'style_path': cf.path((attachment.get('styles') or {}).get(lang) or ''),
            'stale': bool(entry.get('status') == 'accepted' and entry.get('source_sig')
                          and _digest(source) != entry.get('source_sig')),
        })
    return out


def customization_problem(j, fb):
    items = documents(j, fb)
    if not items:
        # 算不出這張會寄哪幾份(跟看板 custCurrent 回 null 一樣):每一筆客製紀錄都算,寧可多擋,不要放行
        items = [{'name': (e or {}).get('name') or '有一份檔案', 'entry': e}
                 for e in (((fb.get(j['id']) or {}).get('custom_docs')) or {}).values() if isinstance(e, dict)]
    for item in items:
        status = (item.get('entry') or {}).get('status')
        if status == 'review':
            return f'{item["name"]} 的客製版等你看，收下或退回後才能送出'
        if status == 'rework':
            return f'{item["name"]} 的客製版退回重寫中，完成後才能送出'
        if status == 'working':
            return f'{item["name"]} 正在客製，完成後才能送出'
    return ''


def path_for(j, root=None):
    root = root or cf.SHIP_DIR
    return os.path.join(root, f'{_safe(card.name(j))}-{card.card_id_from_url(j["id"])}')


def _folders(root, suffix):
    return sorted(p for p in glob.glob(os.path.join(root, '*-' + suffix)) if os.path.isdir(p))


def folder(url, root=None, name=None, migrate=True):
    # APPLY_SHIP_ROOT:代投驗收把假的投遞夾放在自己的暫存資料夾;同一個行程裡找投遞夾的每一處都要認它,
    # 以前只有 apply_run 認,上傳檔核對還去正式的投遞夾找,驗收的檔一律被判「在投遞夾外」
    root = root or os.environ.get('APPLY_SHIP_ROOT') or cf.SHIP_DIR
    hits = _folders(root, card.card_id_from_url(url))
    if hits:
        if name:
            canonical = os.path.join(root, f'{_safe(name)}-{card.card_id_from_url(url)}')
            if canonical in hits:
                return canonical
            if len(hits) == 1 and migrate and not os.path.exists(canonical):
                os.rename(hits[0], canonical)
                return canonical
        return hits[0] if len(hits) == 1 else None
    old = _folders(root, card.legacy_id(url))
    if len(old) != 1:
        return None
    if not migrate:
        return old[0]
    prefix = _safe(name) if name else os.path.basename(old[0]).rsplit('-', 1)[0]
    new = os.path.join(root, f'{prefix}-{card.card_id_from_url(url)}')
    if os.path.exists(new):
        return new if os.path.isdir(new) else None
    os.rename(old[0], new)
    return new


def info(url, root=None):
    """可投遞夾裡的 ship.json;沒有回 {}。"""
    return read_info(folder(url, root=root))


def read_info(directory):
    if not directory:
        return {}
    try:
        with open(os.path.join(directory, 'ship.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def rename(url, new_title):
    """卡片改名時,可投遞夾跟著改名(夾名前半是卡名)。"""
    d = folder(url)
    if not d:
        return None
    new = os.path.join(cf.SHIP_DIR, f'{_safe(card.name(new_title))}-{card.card_id_from_url(url)}')
    if new != d and not os.path.exists(new):
        os.rename(d, new)
        return new
    return d


def sources(j, fb):
    """這張卡的可投遞夾要放哪些檔:(履歷檔, [附件…], 是不是這張自己上傳的)。"""
    f = fb.get(j['id']) or {}
    docs = documents(j, fb)
    resume = next((item for item in docs if item['kind'] == 'resume'), None)
    src = resume.get('effective_path') if resume else None
    atts = [item['effective_path'] for item in docs if item['kind'] == 'attachment' and item.get('effective_path')]
    own = bool(f.get('custom_file')) or bool(resume and resume.get('entry', {}).get('status') == 'accepted'
                                              and resume.get('entry', {}).get('path'))
    return src, atts, own


def source_items(j, fb):
    """Return the ordered effective inputs that feed this card's delivery folder."""
    resume_id, lang = resolve(j, fb)
    own = (fb.get(j['id']) or {}).get('custom_file')
    items = []
    for document in documents(j, fb):
        kind = document['kind']
        # 附件在可投遞夾紀錄裡的 id 不帶語言(客製紀錄的 key 才分語言):照舊寫,已建好的可投遞夾不會因為升級整批重建
        item_id = ('custom' if own else f'resume:{resume_id}:{lang}') if kind == 'resume' else document['id'].rsplit(':', 1)[0]
        path = document.get('effective_path')
        if not path:
            continue
        item = {'id': item_id, 'kind': kind, 'path': path}
        if kind == 'attachment':
            item['name'] = document.get('name') or os.path.basename(path)
        items.append(item)
    return items


def _write_merged(directory, info):
    """Add the ordered resume-and-attachments PDF beside the individual package files."""
    files = [n for n in info.get('files', []) if isinstance(n, str) and n]
    old_merged = info.pop('merged', None)
    if old_merged and old_merged not in files:
        name = old_merged
    else:
        name = MERGED_FILE
        stem, ext = os.path.splitext(name)
        number = 2
        while name in files:
            name = f'{stem}-{number}{ext}'
            number += 1
    paths = [os.path.join(directory, n) for n in files]
    missing = [n for n, p in zip(files, paths) if not os.path.isfile(p)]
    if missing:
        problems = [f'{n} 不見了' for n in missing]
    elif not paths:
        problems = ['沒有個別 PDF 可供合併']
    else:
        destination = os.path.join(directory, name)
        temporary = destination + '.tmp'
        try:
            pdf.merge(paths, temporary)
            os.replace(temporary, destination)
            info['files'] = files
            info['merged'] = name
            problems = []
        except Exception as e:
            try:
                os.remove(temporary)
            except OSError:
                pass
            problems = [f'可投遞夾合併 PDF 失敗:{e}']
    with open(os.path.join(directory, 'ship.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return problems
def build_default(j, fb):
    """把所選履歷和相容附件原樣複製進可投遞夾。回 (夾, 問題清單)。"""
    resume_id, lang = resolve(j, fb)
    waiting = customization_problem(j, fb)
    if waiting:
        return folder(j['id'], name=card.name(j)), [waiting]
    src, atts, own = sources(j, fb)
    bad = []
    d = folder(j['id'], name=card.name(j))
    if not resume_id:
        return d, ['沒有已勾選的履歷可供這張卡使用']
    if not own and not cf.RESUMES.get(resume_id):
        return d, [f'設定裡沒有履歷 {resume_id!r}']
    if not src or not os.path.isfile(src):
        return d, ['這張自己上傳的檔不見了' if own else f'履歷「{cf.resume_name(resume_id)}」沒有 {lang} 的檔']
    d = d or path_for(j)
    os.makedirs(d, exist_ok=True)
    keep = {'.apply'}                        # 代投的截圖與輸出,不是這裡建的
    for n in os.listdir(d):
        if n not in keep:
            p = os.path.join(d, n)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    files = []
    source_map = {}
    items = source_items(j, fb)
    for item in items:
        p = item['path']
        if not os.path.isfile(p):
            bad.append(f'附件不見了:{p}')
            continue
        name = os.path.basename(p)
        stem, ext = os.path.splitext(name)
        number = 2
        while name == MERGED_FILE or name in files:
            name = f'{stem}-{number}{ext}'
            number += 1
        destination = os.path.join(d, name)
        shutil.copy2(p, destination)
        files.append(name)
        source_map[item['id']] = name
    info = {'variant': resume_id, 'lang': lang, 'files': files, 'custom': own, 'sources': source_map}
    with open(os.path.join(d, 'ship.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    if not bad:
        bad.extend(_write_merged(d, info))
    return d, bad


def _write_package_info(directory, info):
    path = os.path.join(directory, 'ship.json')
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    os.replace(temporary, path)


def _package_source_map(directory, j, fb, info):
    selected = source_items(j, fb)
    files = [name for name in info.get('files') or [] if isinstance(name, str)]
    source_map = info.get('sources') if isinstance(info.get('sources'), dict) else {}
    source_map = {str(k): v for k, v in source_map.items()
                  if v in files and os.path.isfile(os.path.join(directory, v))}
    unused = [name for name in files if name not in source_map.values()]
    unused_pdfs = [name for name in unused if name.lower().endswith('.pdf')]
    for item in selected:
        if item['id'] in source_map:
            continue
        base = os.path.basename(item['path'])
        candidates = [base, os.path.splitext(base)[0] + '.pdf']
        match = next((name for name in unused_pdfs if name in candidates), None)
        if match:
            source_map[item['id']] = match
            unused_pdfs.remove(match)
    remaining = [item for item in selected if item['id'] not in source_map]
    if len(remaining) == len(unused_pdfs):
        for item, name in zip(remaining, unused_pdfs):
            source_map[item['id']] = name
    else:
        resumes = [item for item in remaining if item['kind'] == 'resume']
        if len(resumes) == len(unused_pdfs):
            for item, name in zip(resumes, unused_pdfs):
                source_map[item['id']] = name
    return selected, source_map


def _apply_previews_to_board(board, job_url, variant_key, selected, previews):
    if not board:
        return

    def mutate(data, _fb):
        job = next((item for item in data.get('jobs') or [] if item.get('id') == job_url), None)
        if not job:
            return
        resume = job.setdefault('resume', {})
        variants = resume.setdefault('variants', {})
        variant = variants.setdefault(variant_key, {})
        resume_source = next((item for item in selected if item['kind'] == 'resume'), None)
        resume_preview = previews.get(resume_source['id']) if resume_source else None
        if resume_preview:
            variant['pages'] = [pdf_preview.url(resume_preview)]
            variant.pop('html', None)
        else:
            variant.pop('pages', None)
            variant.pop('html', None)

        old_attachments = variant.get('attachments')
        old_attachments = old_attachments if isinstance(old_attachments, list) else []
        attachments = []
        for index, item in enumerate(x for x in selected if x['kind'] == 'attachment'):
            old = old_attachments[index] if index < len(old_attachments) else None
            if isinstance(old, dict):
                display = dict(old)
            elif isinstance(old, str):
                display = {'name': old}
            else:
                display = {}
            display.setdefault('name', item.get('name') or os.path.basename(item['path']))
            preview = previews.get(item['id'])
            if preview:
                display['preview'] = pdf_preview.url(preview)
            else:
                display.pop('preview', None)
            attachments.append(display)
        if attachments or old_attachments:
            variant['attachments'] = attachments

    bd.set_data(mutate, live=board)


def _record_package_previews(directory, j, fb, board=None):
    info = read_info(directory)
    if not isinstance(info, dict) or not info.get('files'):
        return
    selected, source_map = _package_source_map(directory, j, fb, info)
    previews = {}
    for item in selected:
        name = source_map.get(item['id'])
        path = os.path.join(directory, name) if name else ''
        if path and os.path.isfile(path):
            key = pdf_preview.generate(path)
            if key:
                previews[item['id']] = key
    info['sources'] = source_map
    info['previews'] = previews
    _write_package_info(directory, info)
    resume_id, lang = resolve(j, fb)
    variant_key = f'{lang}-{resume_id}' if resume_id else f'{lang}-custom'
    _apply_previews_to_board(board, j['id'], variant_key, selected, previews)


def _state_path():
    return os.path.join(cf.HOME, 'ship-build-status.json')


def _build_states():
    try:
        with open(_state_path(), encoding='utf-8') as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _set_build_state(url, ok, message=''):
    states = _build_states()
    states[url] = {'ok': bool(ok), 'message': message, 'at': time.time()}
    os.makedirs(cf.HOME, exist_ok=True)
    tmp = _state_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(states, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _state_path())


def _sync_report(url, message, board):
    import agent_report
    if message and not any(r.get('enabled', True) for r in cf.RESUMES.values()):
        # 設定裡一份履歷都還沒有:設定頁「開始前」清單第一項就是它,不要每張卡各報一次同一件事
        return None
    if message:
        return agent_report.report('可投遞夾建置', '可投遞夾本輪建置失敗', need=message, job=url, live=board)
    return agent_report.resolve(url, '可投遞夾重新建置並驗收通過', live=board,
                                only=lambda it: it.get('from') == '可投遞夾建置')


def _package_problems(j, fb, migrate=True):
    d = folder(j['id'], name=card.name(j), migrate=migrate)
    if not d:
        return ['可投遞夾還沒建']
    s = read_info(d)
    if not s.get('files'):
        return ['可投遞夾裡沒有 ship.json 或沒有檔案']
    resume_id, lang = resolve(j, fb)
    bad = []
    if (s.get('variant'), s.get('lang')) != (resume_id, lang):
        bad.append(f'看板選的是履歷「{cf.resume_name(resume_id)}」/{lang},可投遞夾裡是 '
                   f'履歷「{cf.resume_name(s.get("variant"))}」/{s.get("lang")}')
    for n in s['files']:
        if not os.path.isfile(os.path.join(d, n)):
            bad.append(f'{n} 不見了')
    merged = s.get('merged')
    if not isinstance(merged, str) or not merged:
        bad.append('可投遞夾裡沒有合併版')
    elif merged in s['files']:
        bad.append('合併版不能列為個別上傳檔')
    elif not os.path.isfile(os.path.join(d, merged)):
        bad.append(f'{merged} 不見了')
    return bad


def check(j, fb, migrate=True):
    """投遞前把關;必須有本模組最近一次成功建置的紀錄。"""
    # 客製檔還在等你看/重寫/客製中:這張刻意在等,原因就寫這個(建置那邊不動它,見 reconcile_packages)
    waiting = customization_problem(j, fb)
    if waiting:
        return [waiting]
    state = _build_states().get(j['id'])
    if state is None:
        return ['可投遞夾尚未由本模組建置驗收']
    if not state.get('ok'):
        return [state.get('message') or '可投遞夾本輪建置失敗']
    return _package_problems(j, fb, migrate=migrate)


def _snapshot(url, name=None):
    d = folder(url, name=name)
    if not d:
        return None
    entries = []
    for root, dirs, files in os.walk(d):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            stat = os.stat(path)
            digest = hashlib.sha256()
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    digest.update(chunk)
            entries.append((os.path.relpath(path, d), stat.st_size, stat.st_mtime_ns, digest.hexdigest()))
    return tuple(entries)


def _inputs_hash(paths, extra=''):
    h = hashlib.sha256(extra.encode())
    for path in sorted(paths):
        h.update(path.encode())
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    h.update(chunk)
        else:
            h.update(b'\0MISSING')
    return h.hexdigest()


def _finish_build(j, message, board):
    try:
        _sync_report(j['id'], message, board)
    except Exception as e:
        message = f'{message + "; " if message else ""}📣 回報更新失敗:{e}'
        _set_build_state(j['id'], False, message)
        return message
    _set_build_state(j['id'], not message, message or '')
    return message


def reconcile_packages(man, force, check_only, board, timings=False):
    """建置、驗收與記錄可投遞夾；呼叫端只負責重建其他衍生物。"""
    with open(board, encoding='utf-8') as f:
        parsed = bd.parse(f.read())
    fb = json.loads(parsed['fb'])
    jobs = [j for j in parsed['data']['jobs'] if (fb.get(j['id']) or {}).get('app') in STAGES
            # dead 是程式判的「頁面打不開」;他在「出錯了」按了放回原處(live_ok)就是說沒壞,照常建(頁面真的活著時 board_status 會清 dead)
            and not (j.get('dead') and not (fb.get(j['id']) or {}).get('live_ok'))
            and not (fb.get(j['id']) or {}).get('rm')]
    did, failed = False, []
    elapsed = {'比對': 0.0, '建置': 0.0, '檢查': 0.0, '預覽': 0.0, '回報': 0.0}
    for j in jobs:
        if customization_problem(j, fb):
            # 客製檔在等你看/重寫/客製中是刻意的等待,不是建置壞了:夾子保持原樣、不報失敗,
            # 投遞前把關(check)照樣擋這張。以前算進 failed,整輪結束碼 1,所有卡的自動推進都停住。
            continue
        started = time.perf_counter()
        variant, lang = resolve(j, fb)
        src, attachments, _own = sources(j, fb)
        wanted = _inputs_hash([src or ''] + attachments, f'{variant}/{lang}')
        key = 'ship:' + card.card_id_from_url(j['id'])
        current = not check(j, fb, migrate=not check_only)
        elapsed['比對'] += time.perf_counter() - started
        if not force and man.get(key) == wanted and current:
            continue
        did = True
        if check_only:
            print(f'  可投遞夾過時:{card.name(j)[:40]}')
            continue
        _set_build_state(j['id'], False, '可投遞夾本輪建置尚未完成')
        started = time.perf_counter()
        try:
            directory, problems = build_default(j, fb)
        except Exception as e:
            directory, problems = None, [f'可投遞夾建置失敗:{e}']
        elapsed['建置'] += time.perf_counter() - started
        started = time.perf_counter()
        if not problems and directory:
            problems = _package_problems(j, fb)
        elapsed['檢查'] += time.perf_counter() - started
        started = time.perf_counter()
        if not problems and directory:
            try:
                _record_package_previews(directory, j, fb, board)
            except Exception as e:
                problems = [f'PDF 預覽建置失敗:{e}']
        elapsed['預覽'] += time.perf_counter() - started
        started = time.perf_counter()
        message = _finish_build(j, '；'.join(problems), board)
        elapsed['回報'] += time.perf_counter() - started
        if message:
            failed.append(f'{card.name(j)[:30]}:{message}')
            print(f'  ⚠ {failed[-1]}')
        elif directory:
            man[key] = wanted
            print(f'  ✓ 可投遞夾:{os.path.basename(directory)}')
    if timings:
        print('  計時:套件 ' + '、'.join(f'{name} {seconds:.1f}s' for name, seconds in elapsed.items()))
    return did, failed


def clean_orphans(check_only, board):
    """清除不再對應準備/投遞卡的可重生套件；無法唯一對帳者只報不刪。"""
    root = cf.SHIP_DIR
    if not os.path.isdir(root):
        return [], []
    with open(board, encoding='utf-8') as f:
        parsed = bd.parse(f.read())
    fb = json.loads(parsed['fb'])
    by_id, by_legacy = {}, {}
    for job in parsed['data']['jobs']:
        url = str(job.get('id') or '')
        if not url:
            continue
        by_id.setdefault(card.card_id_from_url(url), []).append(job)
        by_legacy.setdefault(card.legacy_id(url), []).append(job)
        folder(url, name=card.name(job), migrate=not check_only)
    cleaned, unknown = [], []
    root_real = os.path.realpath(root)
    for entry in sorted(os.listdir(root)):
        full = os.path.join(root, entry)
        if not os.path.isdir(full):
            continue
        suffix = entry.rsplit('-', 1)[-1]
        jobs = by_id.get(suffix) or by_legacy.get(suffix) or []
        if not jobs:
            unknown.append(entry)
            continue
        active = any((fb.get(job['id']) or {}).get('app') in STAGES + ('sent',) for job in jobs)
        if active or len(jobs) > 1:
            continue
        if not os.path.realpath(full).startswith(root_real + os.sep):
            continue
        # 代投留下的 .apply(填表截圖、交件紀錄)不是這裡建的、也重生不了,跟 build_default 一樣留著:
        # 卡退出流程後按復原,填表時的截圖還在。只剩它的夾子不算過期套件,不再每輪報「已清」
        apply_dir = os.path.join(full, '.apply')
        rest = [n for n in os.listdir(full) if n != '.apply']
        if os.path.isdir(apply_dir) and not rest:
            continue
        if not check_only:
            if os.path.isdir(apply_dir):
                for n in rest:
                    p = os.path.join(full, n)
                    shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
            else:
                shutil.rmtree(full)
        cleaned.append(entry)
    return cleaned, unknown


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--board', default=cf.LIVE)
    a = ap.parse_args()
    _, bad = reconcile_packages({}, True, False, a.board)
    for m in bad:
        print('  ⚠', m)
    print('可投遞夾建好了' + (f',{len(bad)} 個問題' if bad else ''))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
