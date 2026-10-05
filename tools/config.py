#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config —— 資料放哪、agent 叫什麼、用哪個模型,全部只從這裡拿。

程式(這個 repo)和資料(你的看板、職缺摘要、履歷)分開放。資料夾叫 home,
找法依序:環境變數 JOBSALVO_HOME → 目前目錄往上找第一個有 jobsalvo.json 的資料夾 → 目前目錄。
home 裡的 jobsalvo.json 蓋過下面 DEFAULTS;沒寫的用預設。相對路徑都相對於 home。
jobsalvo.json 由看板的「⚙ 設定」頁寫(save),不用手改;要手改也行,它就是一般的 JSON。
"""
import os, copy, json

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


def _abs(p, home):
    """設定裡的路徑 → 絕對路徑(~ 展開,相對的接在 home 後面)。"""
    p = os.path.expanduser(p or '')
    return p if os.path.isabs(p) else os.path.join(home, p)


def _dump(f, settings):
    """整份設定寫回 f:先寫暫存檔再換名。"""
    tmp = f + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(settings, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, f)


def migrate_settings(settings, home=None):
    """補上新設定該有的欄位。瀏覽器工作仍依清單順序逐一派工。"""
    settings = copy.deepcopy(settings) if isinstance(settings, dict) else {}
    settings.pop('browser', None)   # 舊的 agent Chrome 設定(資料夾、設定檔、狀態檔);agent 的瀏覽器改成 ego(docs/adr/0006)
    resume = settings.get('resume')
    if not isinstance(resume, dict):
        return settings
    # 空的 skill = 用產品內建的通用 skill
    for key in ('resumes', 'attachments'):
        items = resume.setdefault(key, [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    item.setdefault('skill', '')
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
    """使用者設定(不含預設)。"""
    f = os.path.join(home or HOME, NAME)
    try:
        with open(f, encoding='utf-8') as fh:
            return migrate_settings(json.load(fh))
    except (OSError, ValueError):
        return {}


def upgrade_file(home=None):
    """設定檔裡舊版留下、現在不用的設定(migrate_settings 拿掉的)寫回檔案;沒變、讀不懂都不寫。回有沒有寫。"""
    f = os.path.join(home or HOME, NAME)
    try:
        with open(f, encoding='utf-8') as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return False
    new = migrate_settings(raw)
    if new == raw:
        return False
    _dump(f, new)
    return True


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
    f = os.path.join(HOME, NAME)
    # 原本那份讀不懂:這時畫面上的設定是預設值加這次改的,直接寫會把他手寫、只差一個逗號的整份設定蓋掉。
    # 先原封不動留一份,他還拿得回來。
    if settings_problem(HOME):
        import shutil, time
        shutil.copy2(f, f + '.broken-' + time.strftime('%Y%m%d-%H%M%S'))
    _dump(f, migrate_settings(settings))
    reload()


def path(p):
    """設定裡的路徑 → 絕對路徑(~ 展開,相對的接在 home 後面)。"""
    return _abs(p, HOME)


def _apply(cfg):
    """把設定攤成模組層的常數。長時間跑的行程(看板伺服器)在設定改了之後呼叫 reload() 重攤一次。"""
    g = globals()
    g['C'] = cfg
    g['HOME'] = cfg['home']
    g['LIVE'] = path(cfg['board']['file'])
    g['SUMS'] = path(cfg['paths']['summaries'])
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
    g['PREPARE_DIR'] = path(cfg['resume']['prepare_dir'])
    g['SHIP_DIR'] = path(cfg['resume']['ship_dir'])
    g['LANGS'] = list(cfg['resume']['langs'])
    g['RESUMES'] = {item['id']: item for item in cfg['resume'].get('resumes', [])
                    if isinstance(item, dict) and item.get('id')}
    g['ATTACHMENTS'] = cfg['resume'].get('attachments', [])


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
