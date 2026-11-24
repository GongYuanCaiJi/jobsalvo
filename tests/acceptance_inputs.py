"""四件網站任務程式看得到的輸入(驗收測試的唯一來源,ADR-0012 第 6 點)。

頁面內容不算維度:文字那一維由 Hypothesis 產生(TEXT)。每個維度列出程式分得出的每一類;程式分不出的情況算同一類
(#591 US33–34)。驗收測試用 itertools.product 組這些維度,從任務入口跑,預期值照 docs/research/601-behaviors.md 推,
不 import 被考的程式。
"""
from hypothesis import strategies as st

# ---- 一次拍照那一刻,一個分頁 ----
READ = 'read'                 # 讀得到字
UNREADABLE = 'unreadable'     # 讀頁丟錯
TOO_LONG = 'too_long'         # 超過讀取上限
BLANK = 'blank'               # 讀到了,一個字都沒有
NO_LABEL = 'no_label'         # 瀏覽器給的分頁沒有標籤,叫不到它讀或截圖
TAB_STATES = (READ, UNREADABLE, TOO_LONG, BLANK, NO_LABEL)
SHOTS = (True, False)         # 截圖成不成功

# ---- 一次拍照那一刻,整個工作區 ----
WORKSPACE_UNREAD = None       # 整個工作區讀不到
WORKSPACE_EMPTY = ()          # 沒有可拍的分頁

# ---- 改前那一頁跟改後那一頁的關係(#601 配對規則)----
SAME = 'same'                               # 同一分頁、網址一字不差
SAME_TAB_OTHER_URL = 'same_tab_other_url'
OTHER_TAB_SAME_URL = 'other_tab_same_url'
ONLY_BEFORE = 'only_before'
ONLY_AFTER = 'only_after'
RELATIONS = (SAME, SAME_TAB_OTHER_URL, OTHER_TAB_SAME_URL, ONLY_BEFORE, ONLY_AFTER)

# ---- agent 這一輪叫了幾次拍改前、拍改後,每一次那一刻工作區是什麼樣子 ----
CALLS = (0, 1, 2)
MOMENTS = ('tabs', 'empty', 'unread')

# ---- agent 這一輪怎麼結束、交件單 ----
HANDED = 'handed'             # 交了完整的交件單
NO_SHEET = 'no_sheet'         # 說做完了,沒交件單
BROKEN_SHEET = 'broken_sheet'  # 交件單不是 JSON 物件
TIMEOUT = 'timeout'
CRASHED = 'crashed'
STOPPED_BY_USER = 'stopped_by_user'
UNAVAILABLE = 'unavailable'   # 沒有可用的 agent,沒啟動
ENDS = (HANDED, NO_SHEET, BROKEN_SHEET, TIMEOUT, CRASHED, STOPPED_BY_USER, UNAVAILABLE)
CELL_SHAPES = ('words', 'missing', 'list', 'object', 'number', 'null', 'true')   # 交件單一格寫成什麼
CITES = ('good', 'none', 'bad_id', 'other_material', 'not_list')                 # 引段那一格
STOPS = (False, True)          # agent 寫了停下的原因
NEEDS_USER = ('none', 'quote_on_page', 'quote_not_on_page', 'tab_gone')          # 要本人處理的那一頁
STEPS_BEFORE_FIRST = (False, True)   # 拍改前之前做過瀏覽器步驟
STEP_SHOTS = (True, False)           # 轉接指令那一步的截圖成不成功

# ---- 同步:登記檔與原始履歷 ----
REGISTRY = ('none', 'page', 'file')                     # 這一份平台履歷沒登記、登記的是網頁、登記的是檔案
MASTER = ('present', 'missing')          # 原始履歷檔讀不讀得到(A-35:開始前的設定問題直接講原因)
SOURCE = ('unchanged', 'text', 'comment_only')          # 原始履歷這次改了什麼
REVIEW = (False, True)                                  # 使用者在最新一版清單按「看過了,沒問題」
INTENDED_WHERE = ('added', 'already', 'unpaired_read', 'unpaired_unread', 'nowhere')   # 原稿改後的字落在哪
REMOVED_WHERE = ('gone', 'still', 'unpaired', 'never')   # 原稿刪掉的字改後那一刻;never:改前那一刻頁上就沒有
REGISTERED_BY_AGENT = (None, 'same_host', 'other_host')
RESTORE_PICKS = ('valid', 'stale_version', 'unknown_change', 'unregistered', 'evidence_gone')

# ---- 填表 ----
STAGES = ('fill', 'fix', 'submit', 'confirm')
DELIVERY = ('todo', 'parked', 'stuck', 'confirmed', 'unsure', 'sent')
FIXED_PROFILE = (None, 'pages_match', 'pages_other_url', 'not_shot')   # 這張卡用固定版平台履歷
FILL_FIELD_SRC = ('bank', 'said', 'unknown')
SUBMIT_SAID = ('submitted_cited', 'submitted_uncited', 'not_submitted', 'silent')
UPLOADED = ('same_name', 'other_name', 'none')
CONFIRM_PAGE = ('same', 'changed', 'unread', 'gone')    # 確認畫面打開時工作區跟填表最後一次拍改後比

# ---- 查應徵進度 ----
SOURCE_PAGES = ('read', 'unreadable', 'not_open', 'agent_says_unread',   # 一個登記的來源(信箱、應徵紀錄頁)
                'other_url', 'unlabeled', 'twice_unreadable')        # 開在別的網址、agent 沒標、開了兩頁都讀不到
REPLY_KINDS = ('確認信', '拒絕', '面試或測驗邀請', '錄取', '其他')
REPLY_CITES = ('verified', 'not_on_page')
CARD_PRIORS = ('waiting', 'unsure', 'sent_long_ago')
CHECKED = (False, True)        # agent 在 checked 列了這張卡
CARDS_PER_MAIL = (1, 2)

# ---- 找缺 ----
URL_READ = ('ok', 'partial', 'unknown', 'http_404')
KEEP = ('keep', 'drop')
FIELD_CITES = ('verified', 'not_on_page', 'missing')
BLOCKED = (None, 'company_exact', 'company_alias', 'company_other_spelling')
DEADLINE = ('2026-12-01', '', '下個月')
CROWD = (12, '沒有上限', '很多人')
TERMS = ('posted_days', 'applicants_max', 'salary_min', 'exp_max', 'location')
TERM_VALUE = ('pass', 'miss', 'unchecked', 'unjudged')     # 核對過且符合、核對過但不符、那一欄沒核對到、核對到但值比不了
EXCLUDE = (None, 'word', 'regex', 'other_word')           # 使用者設的「一定不要」
PRIOR = ('new', 'on_board', 'turned_verified', 'turned_unverified')   # 這個網址之前的樣子

# ---- 文字那一維 ----
_CHARS = st.characters(codec='utf-8', categories=('L', 'N', 'P', 'Zs'), exclude_characters='\n\r\x00')
PARAGRAPH = st.text(_CHARS, min_size=1, max_size=12).filter(lambda text: text.strip() == text and text)
PARAGRAPHS = st.lists(PARAGRAPH, min_size=0, max_size=6)
TEXT = st.tuples(PARAGRAPHS, PARAGRAPHS)    # (改前那一頁的段落, 改後那一頁的段落)
