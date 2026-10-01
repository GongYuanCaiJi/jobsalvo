#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
settings_api —— 看板「⚙ 設定」頁背後的讀寫。使用者只碰網頁,不用開任何檔。

  get()                  設定(使用者設過的＋實際生效的)、硬規則、填表做法、狀態
  save(body)             寫回 jobsalvo.json 與文字設定;設定改了,看板伺服器這個行程也跟著換新
  put_file(rel, data)    上傳檔案(只准寫進資料夾的 resume/、custom/,只收履歷類副檔名)
  text_of(rel)           從上傳的履歷抽文字(md/txt 直接讀;pdf 用 pypdf,第一次會自己裝進 venv)
  tail_log(kind, sp)     某一種「跑」最近的輸出,出錯時看板上按「看紀錄」看這個

檔案一律照資料夾裡的相對路徑存,設定裡記的也是相對路徑:整個資料夾搬家也不會壞。
"""
import os, re, sys, json, copy, subprocess, hashlib, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import chrome_bin

UPLOAD_DIRS = ('resume/', 'custom/')
UPLOAD_EXT = ('.pdf', '.md', '.markdown', '.css', '.txt', '.docx', '.doc', '.rtf', '.odt', '.png', '.jpg', '.jpeg')
MAX_UPLOAD = 20 * 1024 * 1024


def _read(p):
    try:
        with open(p, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return ''


def write_hard_rules(text):
    import prefs
    prefs.save_custom_text(str(text))


def _write(p, data, mode='w'):
    """先寫旁邊的暫存檔再換名;寫壞了暫存檔不留。mode='wb' 寫二進位。"""
    os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
    tmp = p + '.tmp'
    try:
        with open(tmp, mode, encoding=None if 'b' in mode else 'utf-8') as f:
            f.write(data)
        os.replace(tmp, p)
    finally:
        with contextlib.suppress(OSError):   # 換好名就沒有暫存檔了
            os.remove(tmp)


def _files():
    """設定頁可重用的上傳檔;custom/ 子資料夾留給 skill 和逐卡客製產物。"""
    out = []
    for d in UPLOAD_DIRS:
        root = cf.path(d)
        for dp, dirs, fs in os.walk(root):
            # custom/skills and per-card generated/uploaded PDFs are not source
            # files for the global resume settings page.
            if d == 'custom/':
                dirs[:] = []
            for f in sorted(fs):
                if f.lower().endswith(UPLOAD_EXT):
                    rel = os.path.relpath(os.path.join(dp, f), cf.HOME).replace('\\', '/')
                    out.append(rel)
    return sorted(out)


def skill_files():
    """使用者新增的做法(改履歷的規則、找缺與判斷的做法);設定頁要路徑、顯示名稱、哪一種。
    兩種放同一個資料夾,靠第一行的記號分:kind 是 'resume' 或 'research';沒記號的(自己放進去的舊檔)是 '',兩邊都列。"""
    root = cf.path('custom/skills')
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        if not name.lower().endswith(('.md', '.txt')):
            continue
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        rel = os.path.relpath(path, cf.HOME).replace('\\', '/')
        label, kind = os.path.splitext(name)[0], ''
        try:
            with open(path, encoding='utf-8') as f:
                first = f.readline().rstrip('\r\n')
            match = re.match(r'<!-- jobsalvo-(research-)?skill: (.*?) -->$', first)
            if match:
                label, kind = match.group(2), 'research' if match.group(1) else 'resume'
        except (OSError, UnicodeError):
            pass
        out.append({'path': rel, 'name': label, 'kind': kind})
    return out


def create_skill(name, content, kind=''):
    """在使用者資料夾新增一份文字做法,回傳 (資料, 問題)。kind='research' 是找缺與判斷的做法,其他是改履歷的規則。"""
    kind = 'research' if kind == 'research' else 'resume'
    word = '找缺與判斷的做法' if kind == 'research' else '改履歷的規則'
    name = re.sub(r'\s+', ' ', str(name or '')).strip()
    content = str(content or '').strip()
    if not name:
        return None, f'先寫{word}的名稱'
    if not content:
        return None, f'先寫{word}的內容'
    if len(content.encode('utf-8')) > 256 * 1024:
        return None, f'{word}不能超過 256 KB'
    slug = re.sub(r'[^a-z0-9_-]+', '-', name.lower()).strip('-_')[:48]
    if not slug:
        slug = 'skill-' + hashlib.blake2s(name.encode('utf-8'), digest_size=6).hexdigest()
    root = cf.path('custom/skills')
    os.makedirs(root, exist_ok=True)
    base = slug
    suffix = 2
    while os.path.exists(os.path.join(root, slug + '.md')) or os.path.exists(os.path.join(root, slug + '.txt')):
        slug = f'{base}-{suffix}'
        suffix += 1
    rel = f'custom/skills/{slug}.md'
    full = safe_rel(rel)
    if not full:
        return None, f'{word}路徑不安全'
    mark = 'jobsalvo-research-skill' if kind == 'research' else 'jobsalvo-skill'
    try:
        _write(full, f'<!-- {mark}: {name} -->\n\n{content}\n')
    except OSError as e:
        return None, f'{word}存檔失敗:{str(e)[:100]}'
    return {'path': rel, 'name': name, 'kind': kind}, ''


def get():
    import agent_chrome
    import doctor
    import prefs
    migration_notices = _retire_legacy_builder()
    prefs.ensure_note(legacy_path=cf.PREFS)
    preferences_custom, preferences_agent = prefs.note_sections()
    C = cf.C
    render_warnings = markdown_warnings()
    return {
        'settings': cf.user_settings(),
        'effective': dict({k: C[k] for k in ('board', 'agent', 'browser', 'resume', 'search', 'research', 'replies')},
                          flow=dict(cf.DEFAULTS.get('flow') or {}, **(C.get('flow') or {}))),
        'texts': {'rules': preferences_custom,
                  'preferences_custom': preferences_custom, 'preferences_agent': preferences_agent,
                  'apply_rules': _read(cf.APPLY_RULES)},
        'files': _files(),
        'skills': skill_files(),
        'research_skill_tasks': [
            {'key': key, 'label': item['label'],
             'default_content': _read(os.path.join(HERE, 'research_skills', item['file']))}
            for key, item in cf.RESEARCH_SKILLS.items()
        ],
        'home': cf.HOME,
        # 最近一次實際連上的時間(跟 Claude 那一列一樣);以前只記了外掛身分、沒記時間的,講「連接過」
        'browser_ok': (agent_chrome.conf().get('codex_checked') or True) if agent_chrome._mine() else '',
        'agent_chrome_made': os.path.isdir(os.path.join(agent_chrome.data_dir(), 'Default')),
        'chrome_profiles': chrome_bin.profiles(),
        # 只講「上次按連接時確認看得到」的時間:沒按過或沒確認過就是還沒連接,不拿「記過編號」當成連得上
        'claude_paired': agent_chrome.conf().get('claude_checked') or '',
        'doctor': doctor.check_environment(),
        'service': os.path.exists(__import__('install_service').plist_path()),
        'migration_notices': migration_notices,
        'render_warnings': render_warnings,
        'git_history': _git_history_status(),
        'version': version(),          # 放最後:上面讀設定時可能順手把舊格式改寫進檔
    }


# 設定頁手上的是打開時讀的那一份,按儲存送回整份。中間別處存過(另一個分頁、找缺那一列的分鐘數、「改用 X」),
# 整份寫回就把人家剛存的蓋掉,畫面上什麼都看不出來。存檔時帶回打開時的版本,對不上就擋下來叫他重讀。
CONFLICT = '設定在別的地方改過了(另一個分頁、找缺那一列的分鐘數、「改用 X」…),這一頁手上的是舊的:按「重新讀取」拿最新的再改'


def version():
    """jobsalvo.json 現在這一版的代號(內容的雜湊);沒有這個檔回空字串。"""
    try:
        with open(os.path.join(cf.HOME, cf.NAME), 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ''


def _retire_legacy_builder():
    """Persist the one-time removal of resume.build_cmd and report why it disappeared."""
    path = os.path.join(cf.HOME, cf.NAME)
    try:
        with open(path, encoding='utf-8') as source:
            settings = json.load(source)
    except (OSError, ValueError):
        return []
    resume = settings.get('resume') if isinstance(settings, dict) else None
    if not isinstance(resume, dict) or 'build_cmd' not in resume:
        return []
    resume.pop('build_cmd', None)
    cf.save(settings)
    message = cf.LEGACY_BUILD_CMD_REMOVAL_NOTICE
    if os.path.isfile(cf.LIVE):
        try:
            import agent_report
            agent_report.report('設定遷移', message, live=cf.LIVE)
        except Exception as exc:  # noqa: BLE001 — 寫不進看板就把失敗併進回給設定頁的訊息
            message += f' 看板提醒寫入失敗({type(exc).__name__})。'
    return [message]


def _git_history_status():
    import folder_history
    return folder_history.status(cf.HOME)


def markdown_warnings():
    import source_sync
    manifest = source_sync.read_manifest()
    out = []
    for entry in source_sync.files():
        if not entry['path'].lower().endswith(('.md', '.markdown')):
            continue
        pages = manifest.get(source_sync.page_key(entry))
        if isinstance(pages, int) and pages > 1:
            out.append({**{key: entry[key] for key in ('kind', 'id', 'lang')},
                        'name': os.path.basename(entry['path']), 'pages': pages})
    return out


def resume_material():
    """Return readable uploaded text or the saved pasted/legacy fallback."""
    resumes = (cf.C.get('resume') or {}).get('resumes') or []
    langs = (cf.C.get('resume') or {}).get('langs') or cf.DEFAULTS['resume']['langs']
    parts, problems, configured = [], [], False
    for item in resumes:
        if item.get('enabled', True) is False:
            continue
        files = item.get('files') or {}
        for lang in langs:
            rel = files.get(lang)
            if not rel:
                continue
            configured = True
            text, problem = text_of(rel)
            if text.strip():
                parts.append(f"【履歷：{item.get('name') or item.get('id') or '未命名'}｜{lang}】\n{text.strip()}")
            elif problem:
                problems.append(f"{rel}：{problem}")
    if parts:
        return '\n\n'.join(parts), ''
    home = os.path.realpath(cf.HOME)
    for filename, label in (('.resume-paste.md', '你貼上的履歷'),
                            ('resume.md', '既有的一頁履歷原文')):
        path = os.path.realpath(os.path.join(cf.HOME, filename))
        if path.startswith(home + os.sep) and os.path.isfile(path):
            text = _read(path).strip()
            if text:
                return f'【履歷：{label}】\n{text}', ''
    if not configured:
        return '', '請先到「⚙ 設定 → 📄 你的履歷」上傳並啟用至少一份履歷。'
    return '', '讀不到已啟用履歷的文字；請把履歷內容貼上來。' + ('\n' + '\n'.join(problems) if problems else '')


def _skill_ok(value):
    if value in (None, ''):
        return True
    if not isinstance(value, str) or not value.startswith('custom/skills/') or not value.lower().endswith(('.md', '.txt')):
        return False
    full = safe_rel(value)
    return bool(full and os.path.isfile(full))


SLUG = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')


def _expand_lookbehind_alternatives(pattern):
    def class_end(text, start):
        i = start + 1
        if i < len(text) and text[i] == '^':
            i += 1
        if i < len(text) and text[i] == ']':
            i += 1
        while i < len(text):
            if text[i] == '\\':
                i += 2
            elif text[i] == ']':
                return i
            else:
                i += 1
        return None

    def group_end(text, start):
        depth = 1
        i = start
        while i < len(text):
            ch = text[i]
            if ch == '\\':
                i += 2
            elif ch == '[':
                end = class_end(text, i)
                if end is None:
                    return None
                i = end + 1
            elif ch == '(':
                depth += 1
                i += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return i
                i += 1
            else:
                i += 1
        return None

    def alternatives(body):
        parts = []
        start = 0
        depth = 0
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == '\\':
                i += 2
            elif ch == '[':
                end = class_end(body, i)
                if end is None:
                    return [body]
                i = end + 1
            elif ch == '(':
                depth += 1
                i += 1
            elif ch == ')':
                depth -= 1
                i += 1
            elif ch == '|' and depth == 0:
                parts.append(body[start:i])
                start = i + 1
                i += 1
            else:
                i += 1
        if not parts:
            return [body]
        parts.append(body[start:])
        return parts

    def expand(text):
        out = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == '\\':
                out.append(text[i:i + 2])
                i += 2
                continue
            if ch == '[':
                end = class_end(text, i)
                if end is None:
                    out.append(text[i:])
                    break
                out.append(text[i:end + 1])
                i = end + 1
                continue
            if text.startswith(('(?<=', '(?<!'), i):
                opener = text[i:i + 4]
                end = group_end(text, i + len(opener))
                if end is not None:
                    branches = alternatives(text[i + len(opener):end])
                    if len(branches) > 1:
                        branches = [expand(branch) for branch in branches]
                        if opener == '(?<=':
                            out.append('(?:' + '|'.join(f'(?<={branch})' for branch in branches) + ')')
                        else:
                            out.append(''.join(f'(?<!{branch})' for branch in branches))
                    else:
                        out.append(text[i:end + 1])
                    i = end + 1
                    continue
            out.append(ch)
            i += 1
        return ''.join(out)

    return expand(pattern)


def compile_match(pattern):
    """Compile the Python approximation of a board RegExp (case-insensitive).

    The settings page does the authoritative syntax check with the browser's
    RegExp constructor before POSTing. This server-side check catches Python
    extensions that would otherwise pass Python but silently fail in the
    board, while translating JavaScript named groups for Python's validator.
    """
    pattern = _expand_lookbehind_alternatives(str(pattern))
    out = []
    i = 0
    in_class = False
    while i < len(pattern):
        ch = pattern[i]
        if ch == '\\':
            if i + 1 >= len(pattern):
                out.append(ch)
                i += 1
                continue
            nxt = pattern[i + 1]
            if not in_class:
                if nxt in ('A', 'Z'):
                    raise ValueError(r'Python 的 \A、\Z 錨點不是看板正規式語法')
                if pattern.startswith((r'\N{', r'\g<'), i):
                    raise ValueError('Python 專用跳脫不是看板正規式語法')
                if pattern.startswith(r'\k<', i):
                    m = re.match(r'\\k<([A-Za-z_][A-Za-z0-9_]*)>', pattern[i:])
                    if not m:
                        raise ValueError('命名群組參照格式不對')
                    out.append(f'(?P={m.group(1)})')
                    i += len(m.group(0))
                    continue
            out.extend((ch, nxt))
            i += 2
            continue

        if ch == '[':
            in_class = True
        elif ch == ']' and in_class:
            in_class = False

        if not in_class and pattern.startswith('(?', i):
            if pattern.startswith(('(?P<', '(?P='), i):
                raise ValueError('Python 命名群組不支援；請改用 JavaScript 的 (?<name>...)')
            if pattern.startswith(('(?:', '(?=', '(?!', '(?<=', '(?<!'), i):
                prefix = next(p for p in ('(?<=', '(?<!', '(?:', '(?=', '(?!')
                              if pattern.startswith(p, i))
                out.append(prefix)
                i += len(prefix)
                continue
            m = re.match(r'\(\?<([A-Za-z_][A-Za-z0-9_]*)>', pattern[i:])
            if m:
                out.append(f'(?P<{m.group(1)}>')
                i += len(m.group(0))
                continue
            raise ValueError('Python 專用群組語法不是看板正規式語法')

        if not in_class and ch in '*+?}' and i + 1 < len(pattern) and pattern[i + 1] == '+':
            raise ValueError('Python 專用 possessive quantifier 不是看板正規式語法')
        out.append(ch)
        i += 1
    return re.compile(''.join(out), re.IGNORECASE | re.ASCII)


def _check_files(item, label, langs, bad):
    files = item.get('files') or {}
    if not isinstance(files, dict):
        bad.append(f'{label} 的語言檔格式不對')
    elif any(lang not in langs for lang in files):
        extra = '、'.join(lang for lang in files if lang not in langs)
        bad.append(f'{label} 有「履歷有哪些語言」沒勾的語言({extra})的檔:把那個語言勾回來再存')
    elif any(not isinstance(path, str) for path in files.values()):
        bad.append(f'{label} 有無效的檔案路徑')
    styles = item.get('styles') or {}
    if not isinstance(styles, dict) or any(lang not in langs or not isinstance(path, str) or
                                           not path.lower().endswith('.css') for lang, path in styles.items()):
        bad.append(f'{label} 的樣式檔只能用已設定語言的 CSS 路徑')


def _check(settings):
    """擋掉會讓程式壞掉的設定;回問題清單。"""
    bad = []
    normalized = copy.deepcopy(settings)
    normalized, _ = cf._migrate_agents(normalized)
    agent = normalized.get('agent')
    if agent is not None and not isinstance(agent, dict):
        bad.append('Agent 設定格式不對')
    agents = agent.get('agents') if isinstance(agent, dict) else None
    if isinstance(agent, dict) and 'agents' in agent:
        if not isinstance(agents, list) or not agents:
            bad.append('至少要留一個 agent')
        else:
            seen = set()
            for i, entry in enumerate(agents, 1):
                if not isinstance(entry, dict):
                    bad.append(f'第 {i} 個 agent 格式不對')
                    continue
                aid = str(entry.get('id') or '')
                if not SLUG.match(aid):
                    bad.append(f'第 {i} 個 agent 代號只能用小寫英文、數字、- 和 _')
                elif aid in seen:
                    bad.append(f'agent 代號重複: {aid}')
                seen.add(aid)
                if entry.get('runtime') not in ('codex', 'command-code', 'claude-code'):
                    bad.append(f'第 {i} 個 agent 執行環境不支援')
                if not isinstance(entry.get('model', ''), str):
                    bad.append(f'第 {i} 個 agent 模型格式不對')
                if not isinstance(entry.get('effort'), str) or not entry.get('effort'):
                    bad.append(f'第 {i} 個 agent 思考強度格式不對')
                if not isinstance(entry.get('browser'), bool):
                    bad.append(f'第 {i} 個 agent 瀏覽器能力格式不對')
                if entry.get('runtime') == 'command-code' and entry.get('browser') is True:
                    bad.append(f'第 {i} 個 Command Code agent 目前不支援瀏覽器')
                # Haiku 過不了 Claude in Chrome 的權限檢查(回「requires permission」):選了每次填表都會失敗
                if (entry.get('runtime') == 'claude-code' and entry.get('browser') is True
                        and 'haiku' in str(entry.get('model') or '').lower()):
                    bad.append(f'第 {i} 個 agent 用 Claude 操作 Chrome 時不能選 Haiku(Claude in Chrome 會擋):模型改成 Sonnet、Opus 或留空')
                if entry.get('runtime') == 'codex' and entry.get('speed', 'standard') not in ('standard', 'fast'):
                    bad.append(f'第 {i} 個 Codex agent 速度要選標準或快速')
            if sum(1 for e in agents if isinstance(e, dict) and e.get('browser') is True) > 1:
                bad.append('只能有一個 agent 可使用 Chrome')
    research = settings.get('research') or {}
    if not isinstance(research, dict):
        bad.append('找缺與判斷設定格式不對')
    else:
        skills = research.get('skills') or {}
        if not isinstance(skills, dict):
            bad.append('找缺與判斷的做法設定格式不對')
        else:
            for task, item in cf.RESEARCH_SKILLS.items():
                if not _skill_ok(skills.get(task, '')):
                    bad.append(f'「{item["label"]}」選的做法找不到了:重新選一份,或選產品附的預設')

    r = settings.get('resume') or {}
    langs = r.get('langs')
    if langs is not None and (not isinstance(langs, list) or not langs or
                              any(not isinstance(x, str) or not x for x in langs)):
        bad.append('至少要有一個有效語言')
    else:
        langs = langs or cf.DEFAULTS['resume']['langs']

    resumes = r.get('resumes', [])
    if not isinstance(resumes, list):
        bad.append('履歷清單格式不對')
        resumes = []
    resume_ids = set()
    for item in resumes:
        if not isinstance(item, dict):
            bad.append('每份履歷格式不對')
            continue
        rid = str(item.get('id') or '')
        if not SLUG.match(rid):
            bad.append(f'履歷代號 {rid!r} 只能用小寫英文、數字、- 和 _')
        if rid in resume_ids:
            bad.append(f'履歷代號重複:{rid}')
        resume_ids.add(rid)
        if not str(item.get('name') or '').strip():
            bad.append(f'履歷 {rid!r} 要有名稱')
        if not _skill_ok(item.get('skill', '')):
            bad.append(f'履歷「{item.get("name") or rid}」選的改履歷的規則找不到了:重新選一份,或用產品附的通用規則')
        _check_files(item, f'履歷「{item.get("name") or rid}」', langs, bad)

    attachments = r.get('attachments', [])
    if not isinstance(attachments, list):
        bad.append('附件清單格式不對')
        attachments = []
    attachment_ids = set()
    for item in attachments:
        if not isinstance(item, dict):
            bad.append('每份附件格式不對')
            continue
        aid = str(item.get('id') or '')
        if not SLUG.match(aid):
            bad.append(f'附件代號 {aid!r} 只能用小寫英文、數字、- 和 _')
        if aid in attachment_ids:
            bad.append(f'附件代號重複:{aid}')
        attachment_ids.add(aid)
        if not str(item.get('name') or '').strip():
            bad.append(f'附件 {aid!r} 要有名稱')
        if not _skill_ok(item.get('skill', '')):
            bad.append(f'附件「{item.get("name") or aid}」選的改履歷的規則找不到了:重新選一份,或用產品附的通用規則')
        _check_files(item, f'附件「{item.get("name") or aid}」', langs, bad)
        eligible = item.get('resume_ids') or []
        if not isinstance(eligible, list) or any(not isinstance(rid, str) or not SLUG.match(rid) for rid in eligible):
            bad.append(f'附件 {aid!r} 的履歷限制格式不對')

    fl = settings.get('flow') or {}
    if not isinstance(fl, dict):
        bad.append('自動流程設定格式不對')
    else:
        if fl.get('replies_at'):
            at = re.fullmatch(r'([0-9]{1,2}):([0-9]{2})', str(fl['replies_at']))
            if not at or int(at.group(1)) > 23 or int(at.group(2)) > 59:
                bad.append('每天查應徵進度的時間要寫成 09:00 這種格式,或留空不自動查')
        cap = fl.get('fill_max')
        if cap not in (None, '') and not re.fullmatch(r'[0-9]{1,3}', str(cap).strip()):
            bad.append('自動填表最多停幾張要寫數字(0 是不限,空的照預設 5)')
    if find_minutes_problem((settings.get('search') or {}).get('find_minutes')):
        bad.append(find_minutes_problem((settings.get('search') or {}).get('find_minutes')))
    # 跟設定頁那一格的範圍一樣。以前不驗:清空存成 0,下一次查應徵進度就把還沒回音的已投出卡全部記成沒下文
    ghost = (settings.get('replies') or {}).get('ghost_days')
    if ghost is not None and not (isinstance(ghost, int) and not isinstance(ghost, bool) and 7 <= ghost <= 120):
        bad.append('沒下文天數要寫 7~120 的整數')
    mail_url = str((settings.get('replies') or {}).get('mail_url') or '').strip()
    if mail_url and not re.match(r'https://[^\s/]+', mail_url):
        bad.append('信箱網址要是 https:// 開頭的完整網址')
    b = settings.get('board') or {}
    for key in ('categories', 'tags'):
        for c in b.get(key) or []:
            if not isinstance(c, dict) or not str(c.get('name') or '').strip():
                bad.append(f'{"類別" if key == "categories" else "標籤"}每一個都要有名字')
            elif c.get('match'):
                try:
                    compile_match(c['match'])
                except (re.error, ValueError) as e:
                    label = '類別' if key == 'categories' else '標籤'
                    detail = str(e) or '正規式語法不對'
                    bad.append(f'{label}「{c["name"]}」的關鍵字寫法不對: {detail}')
    if b.get('categories') == []:
        bad.append('至少要有一個類別(最後一個是「其他」)')
    return bad


def find_minutes_problem(value):
    """找缺時間上限:空的、0(不限時)或 1~999 的整數。回問題;沒問題回空字串。"""
    if value in (None, '') or (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 999):
        return ''
    return '找缺時間要寫 1~999 的分鐘數,空著是不限時'


def save(body):
    """body = {settings: 整份使用者設定, texts: {rules, apply_rules}, version: 設定頁打開時的版本}。回問題清單。
    沒帶 version 的(讀了最新的檔才改的:「改用 X」、找缺分鐘數、檢查程式)不比對。"""
    if 'version' in body and body['version'] != version():
        return [CONFLICT]
    s = None
    if 'settings' in body:
        s = body['settings']
        if not isinstance(s, dict):
            return ['設定格式不對']
        legacy_profile_cmd = isinstance(s.get('resume'), dict) and 'profile_cmd' in s['resume']
        s = cf.migrate_settings(s)
        bad = _check(s)
        if bad:
            return bad
    # 先驗完、再寫文字、最後寫 jobsalvo.json:寫到一半失敗時設定檔還是原來那版,他改好再按一次不會被版本擋下來。
    # 以前先寫設定檔,後面寫文字失敗就丟例外、連線斷掉,畫面說沒存成,設定其實已經換了
    t = body.get('texts') or {}
    try:
        if 'preferences_custom' in t or 'preferences_agent' in t:
            import prefs
            old_custom, old_agent = prefs.note_sections()
            prefs.save_note_from_ui(t.get('preferences_custom', old_custom),
                                    t.get('preferences_agent', old_agent))
        elif 'rules' in t:
            write_hard_rules(str(t['rules']))
        if 'apply_rules' in t:
            _write(cf.APPLY_RULES, str(t['apply_rules']))
    except OSError as e:
        return [f'「你的喜好」或「填表做法」沒存成({e.strerror or e}),設定也還沒動;處理好再按一次']
    if s is not None:
        if legacy_profile_cmd:
            cf.queue_notice(cf.PROFILE_CMD_REMOVAL_NOTICE)
        try:
            cf.save(_without_untouched_defaults(s, cf.user_settings()))
        except OSError as e:
            return [f'設定檔沒存成({e.strerror or e});「你的喜好」和「填表做法」已經存好,處理好再按一次']
    return []


def _without_untouched_defaults(new, old):
    """設定頁送來的是整份(找缺、agent、分類…從實際生效的值起頭,含預設)。他沒設過、值又跟預設一樣的欄位不寫進檔:
    寫了就變成「他自己設的」,之後產品改了預設(docs/adr/0001 更新跟 main)他拿不到,也分不出哪些是自己設的。
    檔裡原本就有的照留。"""
    new = copy.deepcopy(new)
    for sec, defaults in cf.DEFAULTS.items():
        cur = new.get(sec)
        if not isinstance(defaults, dict) or not isinstance(cur, dict):
            continue
        mine = old.get(sec) if isinstance(old.get(sec), dict) else {}
        for key in [k for k in cur if k not in mine and k in defaults and cur[k] == defaults[k]]:
            del cur[key]
        if not cur and sec not in old:
            del new[sec]
    return new


def safe_rel(rel, allow_external_symlink=False):
    """上傳路徑只准在資料夾的 resume/、custom/ 底下,只收履歷類副檔名。回絕對路徑;不准回 None。"""
    rel = (rel or '').replace('\\', '/').lstrip('/')
    if not rel.startswith(UPLOAD_DIRS) or not rel.lower().endswith(UPLOAD_EXT) or '..' in rel.split('/'):
        return None
    home = os.path.realpath(cf.HOME)
    full = os.path.abspath(os.path.join(cf.HOME, rel))
    parent = os.path.realpath(os.path.dirname(full))
    if os.path.commonpath((home, parent)) != home:
        return None
    target = os.path.realpath(full)
    if os.path.commonpath((home, target)) != home and not allow_external_symlink:
        return None
    if allow_external_symlink and not os.path.isfile(full):
        return None
    return target


def put_file(rel, data):
    full = safe_rel(rel)
    if not full:
        return None, '只能上傳到 resume/ 或 custom/,而且要是履歷類的檔(pdf、md、docx…)'
    if len(data) > MAX_UPLOAD:
        return None, '檔案太大(上限 20 MB)'
    _write(full, data, 'wb')
    return os.path.relpath(full, os.path.realpath(cf.HOME)), ''


def delete_file(rel):
    full = safe_rel(rel)
    if full and os.path.isfile(full):
        os.remove(full)
        return True
    return False


def pdf_pages(full):
    """讀取 PDF 頁數;full 由程式從設定或卡片資料解析,不是網頁傳入的路徑。"""
    r = subprocess.run([sys.executable, '-c', 'import sys,pypdf;print(len(pypdf.PdfReader(sys.argv[1]).pages))', full],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise ValueError((r.stderr or 'PDF 無法讀取')[-300:])
    return int(r.stdout.strip())


def pdf_text(full):
    """抽取 PDF 文字;full 由程式解析,可指向使用者資料夾外的原檔。"""
    r = subprocess.run([sys.executable, '-c', 'import sys,pypdf;print("\\n".join((p.extract_text() or "") for p in pypdf.PdfReader(sys.argv[1]).pages))', full],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise ValueError((r.stderr or 'PDF 無法讀取')[-300:])
    return re.sub(r'\n{3,}', '\n\n', r.stdout).strip()


def text_of(rel):
    """把啟用的上傳履歷抽成純文字,供 agent 使用。回 (文字, 問題)。"""
    full = safe_rel(rel, allow_external_symlink=True)
    if not full or not os.path.isfile(full):
        return '', '找不到這個檔'
    low = full.lower()
    if low.endswith(('.md', '.markdown', '.txt')):
        return _read(full), ''
    if not low.endswith('.pdf'):
        return '', '只抽得出 pdf、md、markdown、txt 的文字,其他格式請直接把內容貼進框裡'
    try:
        t = pdf_text(full)
    except (OSError, subprocess.SubprocessError, ValueError) as e:   # 讀不了、逾時、pypdf 讀不懂:照實回給設定頁
        return '', f'抽不出文字({str(e)[:80]}),請直接把內容貼進框裡'
    return (t, '') if t else ('', '這份 PDF 抽不出文字(可能是掃描的圖),請直接把內容貼進框裡')



def remember_pasted_resume(text):
    """Keep the user's pasted fallback available to every later agent task."""
    text = str(text or '').strip()[:100000]
    if text:
        _write(os.path.join(cf.HOME, '.resume-paste.md'), text + '\n')


LOGS = {'prep': ('prep_launch.out', 'cut_tailor_finish.out', 'cut_tailor.out'),
        'research': ('research_launch.out',),
        'apply': ('apply_launch.out',),
        'replies': ('replies_launch.out', 'replies.log'),
        'suggest': ('suggest_launch.out', 'suggest_agent.out'),
        'add': ('add_launch.out',)}


def tail_log(kind, sp, n=12000):
    """某一種「跑」最近的輸出(每個檔最後 n 個字)。"""
    out = []
    for name in LOGS.get(kind, ()):
        f = os.path.join(sp, name)
        t = _read(f)
        if t:
            out.append(f'==== {name} ====\n' + t[-n:])
    return '\n\n'.join(out) or '(還沒有紀錄)'
