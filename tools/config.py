#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config —— 資料放哪、agent 叫什麼、用哪個模型,全部只從這裡拿。

程式(這個 repo)和資料(你的看板、職缺摘要、履歷)分開放。資料夾叫 home,
找法依序:環境變數 JOBSALVO_HOME → 目前目錄往上找第一個有 jobsalvo.json 的資料夾 → 目前目錄。
home 裡的 jobsalvo.json 蓋過下面 DEFAULTS;沒寫的用預設。相對路徑都相對於 home。
jobsalvo.json 由看板的「⚙ 設定」頁寫(save),不用手改;要手改也行,它就是一般的 JSON。
"""
import os, copy, json, hashlib

NAME = 'jobsalvo.json'

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)            # jobsalvo 程式本身
BOARD_SRC = os.path.join(ROOT, 'board')  # 看板外殼(css/js/頁首)

RESEARCH_SKILLS = {
    'common': {'label': '找缺共通', 'file': 'find-common.md'},
    'deep': {'label': '更深', 'file': 'deep.md'},
    'wide': {'label': '更廣', 'file': 'wide.md'},
    'dir': {'label': '指定方向', 'file': 'direction.md'},
    'judge': {'label': '判斷', 'file': 'judge.md'},
}


DEFAULTS = {
    'board': {
        'file': 'board.html',           # 現行看板:外殼＋全部職缺資料＋你的標記,單一真相
        'port': 8899,
        'sandbox_port': 8898,
        # 職缺類別(單一軸:這份工作在做什麼)。依序比對職稱,都沒中再比內文;最後一個是其他。
        # match 是 JavaScript 正規表示式(不分大小寫)。
        'categories': [
            # 預設給各行各業大致分得開的幾類;設定頁「🗂 分類」可以讓 agent 照你的履歷和表態建議一份自己的。
            {'name': '技術', 'icon': '⚙', 'match': r'engineer|developer|工程師|開發|架構師|sre|devops|\bai\b|\bml\b|llm|machine learning|data|資料|數據|scientist'},
            {'name': '設計', 'icon': '🎨', 'match': r'design|設計|\bux\b|\bui\b'},
            {'name': '產品／營運', 'icon': '📦', 'match': r'product|產品|operations|營運|project manager|專案'},
            {'name': '業務／行銷', 'icon': '📣', 'match': r'sales|業務|marketing|行銷|business development|account executive'},
            {'name': '行政／財務', 'icon': '🗂', 'match': r'admin|行政|finance|財務|accountant|會計|\bhr\b|人資|legal|法務'},
            {'name': '醫療／教育', 'icon': '🩺', 'match': r'nurse|護理|physician|醫師|pharmac|藥師|teacher|教師|老師|tutor|instructor|講師'},
            {'name': '其他', 'icon': '•', 'match': ''},
        ],
        # 公司名別名:網址或標題裡的寫法(小寫)→ 顯示名稱。例:{'openai': 'OpenAI'}
        'company_alias': {},
        # 自己領域的職稱字(一般文字,一行一個):內建清單(card.TITLE_WORDS)認不出的職稱加在這裡,
        # 不然「公司 · 職稱」的職稱那段會被當成公司名。
        'title_words': [],
        # 跟類別正交的標籤(一張卡可以有好幾個),比對職稱＋內文。
        'tags': [
            {'name': '實習／計畫', 'match': r'intern|實習|儲備|management trainee|fellowship|graduate program'},
            {'name': '遠端', 'match': r'remote|遠端|在家工作'},
        ],
    },
    'paths': {
        'summaries': 'card-summaries',  # 每個職缺研究到什麼,<sha1(url)前12碼>.json
        'company_cache': 'company-cache',
        'research': '.research',        # 找缺每一輪的紀錄
        'prefs': 'prefs.md',            # 看板逐張表態的原話匯出
        'preference_note': 'preference-note.md',  # 使用者自訂與 agent 假設分開保存
        'apply_rules': 'apply-rules.md',  # 你自己的填表做法(連結欄填什麼、哪些勾選框要勾…),代投時原文交給 agent
        'posted_cache': 'posted-cache.json',
        'log': 'board-server.log',
        'tmp': '/tmp/jobsalvo',         # 進度檔、沙箱、截圖這類跑完就丟的東西
    },
    'agent': {
        'agents': [
            {'id': 'primary', 'runtime': 'codex', 'model': '', 'effort': 'max', 'speed': 'standard', 'browser': True},
        ],
    },
    'browser': {
        # agent 專用的 Chrome:自己一個資料夾、自己一個程序,跟你的 Chrome 分開(docs/adr/0003)。
        'data_dir': '~/Library/Application Support/jobsalvo/agent-chrome',
        # 第一次建 agent 的 Chrome 時,從你 Chrome 的哪個設定檔複製登入狀態和擴充功能(選填)。
        'profile': '',                  # 預設不複製:要複製由他在設定頁選(以前預設「Agent」是作者自己的設定檔名,別人選到的是不相干的設定檔)
        'state': '~/.cache/jobsalvo/agent-chrome.json',
    },
    'resume': {
        'prepare_dir': 'prepare',       # 跑準備區每個職缺一夾,agent 寫 fill.json
        'ship_dir': 'ship',             # 可投遞夾:每個職缺一夾,代投上傳的檔都在這裡
        'langs': ['zh', 'en'],
        # 你看得懂、在看板上改答案庫用的語言。送出去的答案跟它不是同一套文字(例:中文使用者、英文表單),
        # 答案庫才附一份這個語言的翻譯給你看、給你改;一樣的就不用翻。
        'read_lang': 'zh',
        'resumes': [],                  # {id, name, files:{語言:路徑}, enabled, when, skill}
        'attachments': [],              # {id, name, files:{語言:路徑}, enabled, resume_ids, skill}
    },
    'search': {
        # 職稱一中就不送(不花 agent)/容易誤中、標出來交給判斷的字。一行一個字,不分大小寫。
        'exclude_words': [],
        'flag_words': [],
        # 進階:直接寫 Python 正規表示式(跟上面的字一起用)
        'exclude_title': '',
        'flag_title': '',
        # 找缺那一段最多跑幾分鐘(看板找缺那一列的格子改它);0 = 不限時,agent 自己決定找多久
        'find_minutes': 15,
    },
    'research': {
        # 找缺與判斷各自可指定客製 skill;空白用產品附的預設。
        'skills': {task: '' for task in RESEARCH_SKILLS},
    },
    'replies': {
        'ghost_days': 30,               # 送出後幾天沒有任何回音,記成沒下文
        # 查回音的信箱網址。Gmail 任一個帳號(…/mail/u/1/)程式自己搜尋;其他信箱交給 agent 用它的 Chrome 打開查。
        'mail_url': 'https://mail.google.com/mail/u/0/',
    },
    # 流程自動往下跑(tools/autopilot.py):他只在要做決定的地方被叫到(表態、核准)。
    # 任何一條都不會自動送出:送出永遠要他在卡上按「✅ 核准送出」。
    'flow': {
        'like_to_prep': True,           # 按 👍 就等於要投:同時加入準備
        'auto_prep': True,              # 準備區有新卡就自動跑準備區
        'auto_advance': True,           # 準備好、驗收過的卡自動進可投遞(被擋的留在「還不能投」並寫原因)
        'auto_fill': True,              # 進可投遞就讓 agent 填表、答案改過就重打,停在送出前等他核准
        'fill_max': 5,                  # 自動填表最多先填幾張停著等他核准(每張佔一個開著的分頁);0 = 不限,空的照預設 5
        'replies_at': '09:00',          # 每天幾點自動查回音;空字串 = 不自動查
    },
    'accept': {
        'facts': {},                    # 代投驗收(apply_accept)用來確認「照母稿填」的事實,例:{"name": ["Your Name"]}
    },
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def _paths(value):
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(p) for p in value if isinstance(p, str) and p]
    return []


_MIGRATION_HASH_CACHE = {}
_PENDING_NOTICES = []
LEGACY_BUILD_CMD_REMOVAL_NOTICE = (
    '舊版 resume.build_cmd 已停用，已從設定移除；現在由內建流程處理 Markdown 與 PDF。'
)


def queue_profile_cmd_removal_notice():
    notice = '已移除舊版平台履歷指令設定;平台欄位現在由 jobsalvo 通用欄位對照驗證。'
    if notice not in _PENDING_NOTICES:
        _PENDING_NOTICES.append(notice)


def queue_build_cmd_removal_notice():
    if LEGACY_BUILD_CMD_REMOVAL_NOTICE not in _PENDING_NOTICES:
        _PENDING_NOTICES.append(LEGACY_BUILD_CMD_REMOVAL_NOTICE)


def _file_digest(path, home):
    full_path = os.path.expanduser(path)
    if not os.path.isabs(full_path):
        full_path = os.path.join(home, full_path)
    try:
        stat = os.stat(full_path)
    except OSError:
        return ''
    cache_key = (full_path, stat.st_dev, stat.st_ino, stat.st_size,
                 stat.st_mtime_ns, stat.st_ctime_ns)
    cached = _MIGRATION_HASH_CACHE.get(cache_key)
    if cached:
        return cached
    digest = hashlib.sha256()
    try:
        with open(full_path, 'rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
    except OSError:
        return ''
    result = digest.hexdigest()
    if len(_MIGRATION_HASH_CACHE) >= 1024:
        _MIGRATION_HASH_CACHE.clear()
    _MIGRATION_HASH_CACHE[cache_key] = result
    return result


def _legacy_merged_name(path):
    name = os.path.basename(os.path.expanduser(path)).casefold()
    return name.endswith((
        ' + technical write-ups.pdf', '-combined.pdf', '_combined.pdf', ' combined.pdf',
    ))




def migrate_settings(settings, home=None):
    """Convert the old resume-variant settings once, in memory, to the two file lists."""
    settings = copy.deepcopy(settings) if isinstance(settings, dict) else {}
    # Page fetching is built into the app; old copies of this setting are obsolete.
    settings.pop('fetch', None)
    # 看板標題、agent 的名字不再讓人改(改名字沒幫到找工作,只多一個要想的設定):舊設定檔裡的一律拿掉
    board = settings.get('board')
    if isinstance(board, dict):
        board.pop('title', None)
    if isinstance(settings.get('agent'), dict):
        settings['agent'].pop('name', None)
    # agent 的 Chrome 只有一種開法(正常 Chrome、背景),也不再有「叫到我面前」:舊設定檔裡的拿掉
    if isinstance(settings.get('browser'), dict):
        for k in ('tool', 'show_window'):
            settings['browser'].pop(k, None)
    # 同一時間只准一個 agent 用 agent 的 Chrome(兩個 AI 搶同一個瀏覽器會互相干擾):舊設定勾了好幾個的,只留最上面那個
    agents = (settings.get('agent') or {}).get('agents') if isinstance(settings.get('agent'), dict) else None
    if isinstance(agents, list):
        first = True
        for a in agents:
            if isinstance(a, dict) and a.get('browser') is True:
                a['browser'] = first
                first = False
    resume = settings.get('resume')
    if not isinstance(resume, dict):
        return settings
    resume.pop('profile_cmd', None)
    # Per-user build commands are retired; Markdown conversion is built in.
    resume.pop('build_cmd', None)
    # Retire the old free-text field; settings_api still reads resume.md as a fallback.
    resume.pop('base', None)

    # Keep the new per-file customization field present on both fresh and old
    # settings. An empty skill means the product's general skill is used when
    # the user chooses to customize that file.
    for key in ('resumes', 'attachments'):
        items = resume.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    item.setdefault('skill', '')

    legacy = resume.pop('variants', None)
    if legacy is None:
        resume.setdefault('resumes', [])
        resume.setdefault('attachments', [])
        return settings
    if 'resumes' in resume and (resume.get('resumes') or not legacy):
        # A partially migrated file's explicit new lists win over its old copy.
        resume.setdefault('attachments', [])
        return settings

    legacy = legacy if isinstance(legacy, dict) else {}
    langs = resume.get('langs') or DEFAULTS['resume']['langs']
    home = home or HOME
    ids = list(legacy)
    resumes = []
    files_by_content = {}
    legacy_merged_paths = set()
    page_cache = {}
    for rid, old in legacy.items():
        old = old if isinstance(old, dict) else {}
        files = {lang: old[lang] for lang in langs if isinstance(old.get(lang), str) and old[lang]}
        resumes.append({
            'id': str(rid),
            'name': str(old.get('label') or rid),
            'files': files,
            'enabled': bool(old.get('enabled', True)),
            'when': str(old.get('when') or ''),
            'skill': str(old.get('skill') or ''),
        })

        attachments = old.get('attachments') or []
        if isinstance(attachments, dict):
            ordered_langs = list(dict.fromkeys(list(langs) + sorted(set(attachments) - set(langs))))
            by_lang = {lang: _paths(attachments.get(lang)) for lang in ordered_langs}
        else:
            shared = _paths(attachments)
            by_lang = {lang: shared for lang in langs}

        # Drop named legacy packages cheaply. The page-content fallback preserves migration
        # for older packages that did not use one of those names.
        merged_paths = set()
        try:
            import pdf_tools as pdf
            for lang, paths in by_lang.items():
                named = {candidate for candidate in paths
                         if candidate in legacy_merged_paths or _legacy_merged_name(candidate)}
                if named:
                    merged_paths.update(named)
                    continue
                master = files.get(lang)
                if not master or not master.lower().endswith('.pdf'):
                    continue
                master_path = os.path.expanduser(master)
                if not os.path.isabs(master_path):
                    master_path = os.path.join(home, master_path)
                for candidate in paths:
                    if candidate in legacy_merged_paths:
                        merged_paths.add(candidate)
                        continue
                    if candidate == master or not candidate.lower().endswith('.pdf'):
                        continue
                    others = [p for p in paths if p != candidate]
                    if not others:
                        continue
                    part_paths = []
                    for part in [master] + others:
                        part_path = os.path.expanduser(part)
                        if not os.path.isabs(part_path):
                            part_path = os.path.join(home, part_path)
                        part_paths.append(part_path)
                    candidate_path = os.path.expanduser(candidate)
                    if not os.path.isabs(candidate_path):
                        candidate_path = os.path.join(home, candidate_path)
                    if all(os.path.isfile(p) for p in [candidate_path] + part_paths) and \
                            pdf.same_pages(candidate_path, part_paths, page_cache):
                        merged_paths.add(candidate)
        except Exception:  # noqa: S110
            # If a PDF cannot be inspected, keep it as an attachment rather than risk data loss.
            pass
        if merged_paths:
            legacy_merged_paths.update(merged_paths)
            by_lang = {lang: [p for p in paths if p not in merged_paths]
                       for lang, paths in by_lang.items()}

        for lang, paths in by_lang.items():
            for path in paths:
                digest = _file_digest(path, home)
                identity = ('content', digest) if digest else ('path', path)
                item = files_by_content.setdefault(
                    identity, {'files': {}, 'resume_ids': [], 'digest': digest})
                item['files'].setdefault(lang, path)
                if rid not in item['resume_ids']:
                    item['resume_ids'].append(rid)

    attachments = []
    for identity, old in files_by_content.items():
        digest = old['digest'] or hashlib.sha256(identity[1].encode('utf-8')).hexdigest()
        allowed = old['resume_ids']
        attachments.append({
            'id': 'att-' + digest[:24],
            'name': os.path.basename(next(iter(old['files'].values())).rstrip('/')) or identity[1],
            'files': old['files'],
            'enabled': True,
            'resume_ids': [] if set(allowed) == set(ids) else allowed,
            'skill': '',
        })

    resume['resumes'] = resumes
    # Preserve an already authored attachments list if a migration was interrupted.
    current = resume.get('attachments')
    if isinstance(current, list):
        known = {item.get('id') for item in current if isinstance(item, dict)}
        attachments = current + [item for item in attachments if item['id'] not in known]
    resume['attachments'] = attachments
    return settings


def find_home(start=None):
    env = os.environ.get('JOBSALVO_HOME')
    if env:
        return os.path.abspath(os.path.expanduser(env))
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(d, NAME)):
            return d
        up = os.path.dirname(d)
        if up == d:
            return os.path.abspath(start or os.getcwd())
        d = up


def user_settings(home=None):
    """使用者設定(不含預設);舊的履歷格式只在記憶體中轉成新格式。"""
    home = home or HOME
    f = os.path.join(home, NAME)
    try:
        with open(f, encoding='utf-8') as fh:
            raw = json.load(fh)
            legacy_profile_cmd = isinstance(raw, dict) and isinstance(raw.get('resume'), dict) \
                and 'profile_cmd' in raw['resume']
            legacy_build_cmd = isinstance(raw, dict) and isinstance(raw.get('resume'), dict) \
                and 'build_cmd' in raw['resume']
            settings = migrate_settings(raw, home=home)
    except (OSError, ValueError):
        return {}
    settings, migrated = _migrate_agents(settings)
    if migrated or legacy_profile_cmd or legacy_build_cmd:
        tmp = f + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(settings, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, f)
    if legacy_profile_cmd and os.path.realpath(home) == os.path.realpath(HOME):
        queue_profile_cmd_removal_notice()
    if legacy_build_cmd and os.path.realpath(home) == os.path.realpath(HOME):
        queue_build_cmd_removal_notice()
    return settings


def _migrate_agents(settings):
    """Convert the old primary/secondary fields once; new agent lists are already canonical."""
    agent = settings.get('agent') if isinstance(settings, dict) else None
    legacy = ('runtime', 'model', 'effort', 'alt_runtime', 'alt_model')
    if not isinstance(agent, dict) or isinstance(agent.get('agents'), list):
        return settings, False
    if not any(key in agent for key in legacy):
        return settings, False

    runtime = agent.get('runtime') or 'codex'
    effort = agent.get('effort') or 'max'
    agents = [{
        'id': 'primary', 'runtime': runtime, 'model': agent.get('model') or '',
        'effort': effort, 'speed': 'standard', 'browser': runtime == 'codex',
    }]
    if 'alt_runtime' in agent or 'alt_model' in agent:
        agents.append({
            'id': 'secondary', 'runtime': agent.get('alt_runtime') or 'command-code',
            'model': agent.get('alt_model') or '', 'effort': effort, 'speed': 'standard', 'browser': False,
        })
    for key in legacy:
        agent.pop(key, None)
    agent['agents'] = agents
    return settings, True


def load(home=None):
    home = home or find_home()
    cfg = _merge(copy.deepcopy(DEFAULTS), migrate_settings(user_settings(home)))
    for entry in (cfg.get('agent') or {}).get('agents') or []:
        if isinstance(entry, dict):
            entry.setdefault('speed', 'standard')
    cfg['home'] = home
    return cfg


def settings_problem(home=None):
    """jobsalvo.json 讀不懂(JSON 格式壞了)就回原因,沒問題或沒有這個檔回 ''。
    讀不懂的時候程式照預設值跑(user_settings 回 {}),不說的話他不會知道自己的設定全沒生效。"""
    f = os.path.join(home or HOME, NAME)
    try:
        with open(f, encoding='utf-8') as fh:
            json.load(fh)
    except OSError:
        return ''
    except ValueError as e:
        return str(e)
    return ''


def save(settings):
    """寫回 jobsalvo.json(整份,就是使用者設定的那些),再讓這個行程的設定跟上。"""
    settings = copy.deepcopy(settings)
    settings, _ = _migrate_agents(settings)
    f = os.path.join(HOME, NAME)
    # 原本那份讀不懂:這時畫面上的設定是預設值加這次改的,直接寫會把他手寫、只差一個逗號的整份設定蓋掉。
    # 先原封不動留一份,他還拿得回來。
    if settings_problem(HOME):
        import shutil, time
        shutil.copy2(f, f + '.broken-' + time.strftime('%Y%m%d-%H%M%S'))
    tmp = f + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(migrate_settings(settings), fh, ensure_ascii=False, indent=2)
    os.replace(tmp, f)
    reload()


def path(p):
    """設定裡的路徑 → 絕對路徑(~ 展開,相對的接在 home 後面)。"""
    p = os.path.expanduser(p or '')
    return p if os.path.isabs(p) else os.path.join(HOME, p)


def _apply(cfg):
    """把設定攤成模組層的常數。長時間跑的行程(看板伺服器)在設定改了之後呼叫 reload() 重攤一次。"""
    g = globals()
    g['C'] = cfg
    g['HOME'] = cfg['home']
    g['LIVE'] = path(cfg['board']['file'])
    g['SUMS'] = path(cfg['paths']['summaries'])
    g['COMPANY_CACHE'] = path(cfg['paths']['company_cache'])
    g['RESEARCH'] = path(cfg['paths']['research'])
    g['PREFS'] = path(cfg['paths']['prefs'])
    g['PREFERENCE_NOTE'] = path(cfg['paths']['preference_note'])
    g['APPLY_RULES'] = path(cfg['paths']['apply_rules'])
    g['POSTED_CACHE'] = path(cfg['paths']['posted_cache'])
    g['LOG'] = path(cfg['paths']['log'])
    g['TMP'] = path(cfg['paths']['tmp'])
    _private_dir(g['TMP'])
    g['PORT'] = int(cfg['board']['port'])
    g['SANDBOX_PORT'] = int(cfg['board']['sandbox_port'])
    g['AGENT'] = 'Agent'                  # 看板和 prompt 裡怎麼稱呼它(固定,不給改)
    g['BROWSER_STATE'] = path(cfg['browser']['state'])
    g['BROWSER_DIR'] = path(cfg['browser']['data_dir'])
    g['PREPARE_DIR'] = path(cfg['resume']['prepare_dir'])
    g['SHIP_DIR'] = path(cfg['resume']['ship_dir'])
    g['LANGS'] = list(cfg['resume']['langs'])
    g['RESUMES'] = {item['id']: item for item in cfg['resume'].get('resumes', [])
                    if isinstance(item, dict) and item.get('id')}
    g['ATTACHMENTS'] = cfg['resume'].get('attachments', [])
    if _PENDING_NOTICES:
        try:
            import agent_report
            for notice in _PENDING_NOTICES:
                agent_report.report('設定', notice, need='無需本人處理', live=g['LIVE'])
        except Exception:
            return
        _PENDING_NOTICES.clear()


def _private_dir(d):
    """暫存資料夾只有自己讀得到:裡面有給 agent 的指示、履歷片段、Email。/tmp 是全機器共用的,
    預設建出來是 0755,同一台電腦的其他帳號讀得到。建立時就用 0700,已經有的(自己的)也改成 0700。"""
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        if os.stat(d).st_uid == os.getuid() and os.stat(d).st_mode & 0o077:
            os.chmod(d, 0o700)
    except OSError:
        pass                        # 建不了或改不了(別人的資料夾、唯讀):照舊用,跑的時候再報錯


def reload(home=None):
    _apply(load(home or HOME))


RESUMES = {}   # _apply 照設定填(用 globals() 設的,這裡先宣告給讀程式的人和工具看)
HOME = find_home()
_apply(load(HOME))


def master(resume_id, lang):
    """某份履歷、某個語言的母稿路徑;沒設定回 None。"""
    files = (RESUMES.get(resume_id) or {}).get('files') or {}
    return path(files[lang]) if files.get(lang) else None


def resume_name(resume_id):
    return (RESUMES.get(resume_id) or {}).get('name') or resume_id


def tool(name):
    return os.path.join(TOOLS, name)
