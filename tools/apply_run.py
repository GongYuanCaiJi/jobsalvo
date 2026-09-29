#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_run —— 「可投遞」這一段:agent 在使用者的 Chrome 設定檔裡填表單,使用者看過真的頁面、在看板上核准,
再由同一段對話在同一頁送出。看板上的「▶ 填表單」「✏️ 要 agent 改」「✅ 核准送出」各跑這支的一個階段。

最後一關絕對不自動投遞,一定要有人類把關:agent 負責把所有資料填好,使用者做最後確認,按了核准 agent 才送出。
prompt 只講目標、唯一真相和規矩,平台上怎麼點由 agent 自己判斷(求職平台五花八門,寫死步驟反而壞事);程式守的是閘門。

瀏覽器:Codex 自己的 Chrome 外掛,在 agent 專用的 Chrome 設定檔裡開背景分頁(登入狀態從使用者的設定檔複製過去)。
不動使用者的滑鼠、不把 Chrome 叫到前面。填好的分頁標 markHandoff() 留著,看板上有填好當下的截圖,
手機也看得到;要看現場就按「👀 看現在的頁面」。

一張職缺一段對話,從填到送出都是它(它記得前面做過什麼、開的是哪個分頁,不用換一隻從頭來):
  fill    開一段新對話。平台上有自己一份履歷的(104、Cake、Yourator、LinkedIn…)先跟本機母稿比,過時就更新;
          開分頁照答案庫填、上傳這張的檔,停在送出前;寫 fill.json,用 form_record --from-fill 把欄位記進看板。
          對話的 id 記在看板(apply.session)。
  fix     codex exec resume 叫回那一段對話,在原本那一頁上改:使用者在答案庫改過的答案(欄位標 refill),
          或他寫給 agent 的話。改完重記表單、重截圖,又回到等他核准。
  submit  只對核准過、核准之後答案沒再變的卡。叫回同一段對話,對過核准快照、在同一頁送出、截確認頁。
          送成功了這張就結束,那段對話不會再被叫回來。
對話不見了(沒記到 id、resume 失敗)就不改也不送:使用者核准的是那一頁,換一段新的對話找不回來,要重新填一次給他看。

程式守的閘門(不靠 agent 自律):
  · submit 只對核准有效的卡啟動(form_record.approval_problem;看板的按鈕、伺服器都用同一套規則)。
  · 填表、修改這兩輪不送出:規矩寫明送出不在這一輪的授權裡,Codex 自己的規則也要求求職送出前一定要當下確認。
    頁面上裝不了擋送出的程式:Codex 外掛不准改頁面(window 是凍結的、沒有 CDP),
    所以改成事後查:填完程式自己讀那一頁,網址要還是填好的那一頁、欄位都還在,不然當成可能被送出了。
  · 填完程式自己對:表單有沒有記進看板、答案有沒有跑出答案庫、頁面上的值是不是答案庫的值、分頁有沒有留著。
  · 送出要有確認頁證據(網址或文字 + 截圖)才搬到已投遞、鎖表單;沒有就照實標「沒送出」,擋住重送。
  這些閘門真的有沒有擋住,是 apply_accept.py 對著本機假表單驗的(證據來自伺服器那一端,不是 agent 的回報)。

用法:
  uv run python tools/apply_run.py --stage fill|fix|submit [--url U] [--note "要改什麼"] [--board B] [--dry]
  (--board 給副本就只動副本;--dry 只印 prompt、不派 agent)
"""
import os, sys, re, json, time, argparse, datetime, subprocess, tempfile, shutil, shlex

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd        # noqa: E402
import form_record as fr      # noqa: E402
import agent_run as ar        # noqa: E402
import jobrun                 # noqa: E402
import agent_report           # noqa: E402
import config as cf           # noqa: E402
import ship                   # noqa: E402
import card                   # noqa: E402

# 可投遞夾。驗收(apply_accept)用環境變數換成暫存夾,放假職缺的檔,不碰真的可投遞夾。
SHIP_ROOT = os.environ.get('APPLY_SHIP_ROOT') or cf.SHIP_DIR
SP = os.environ.get('APPLY_TMP', cf.TMP)             # 進度檔放哪(board_server 會給;跟跑準備區同一套)
STATUS = 'apply_status.json'
TIMEOUT = 40 * 60                                     # 一張最多等 40 分鐘
WRAPUP_TIMEOUT = 8 * 60                               # 時間到之後,叫回同一段對話收尾最多再等這麼久
WRAPUP = ('時間到了。不要再開新分頁、不要再做別的步驟,也絕對不要送出。'
          '表單已經填到哪裡就停在哪裡:對那個分頁呼叫 markHandoff(),照上一輪說的格式把目前的結果寫進 {out}/fill.json,'
          '沒做完、沒核對到的寫進 problems。最後一行印 @@DONE@@。')
STAGES = ('fill', 'fix', 'submit')


def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def load(board):
    with open(board, encoding='utf-8') as f:
        p = bd.parse(f.read())
    return {j['id']: j for j in p['data']['jobs']}, json.loads(p['fb'])


def out_dir(url, board, sp=None):
    """這一輪的截圖和輸出放哪。現行看板放可投遞夾裡的 .apply/(跟著那張走,看板的截圖連結讀這裡);
    副本(測試)放進度目錄底下,不要把測試的截圖留在真的可投遞夾裡、被現行看板當成真的顯示。"""
    live = bd.is_live(board)
    d = ship.folder(url, root=SHIP_ROOT)
    if live and d:
        return os.path.join(d, '.apply')
    return os.path.join(sp or SP, 'apply-out', card.card_id_from_url(url))


def apply_of(fb, url):
    return (fb.get(url) or {}).get('apply') or {}


def eligible(jobs, fb, stage, url=None):
    """這一輪要跑哪幾張。fill:可投遞、還沒送出、核准還沒生效的;fix:填好了、有欄位等著照新答案重打的;
    submit:核准有效的。fix 指定 --url 就一定跑(他寫了話要 agent 改)。"""
    out = []
    for u, m in fb.items():
        if not isinstance(m, dict) or m.get('app') != 'ship' or (url and u != url) or u not in jobs:
            continue
        if ((m.get('form') or {}).get('lock')):
            continue
        prob = fr.approval_problem(fb, u)
        a = m.get('apply') or {}
        if stage == 'submit' and prob is None:
            out.append(u)
        elif stage == 'fix' and a.get('session') and (url or to_translate(fb, u) or any(x.get('refill') for x in (m.get('form') or {}).get('f', []))):
            out.append(u)
        elif stage == 'fill' and prob is not None:
            if not url and a.get('stage') in ('fill', 'fix') and a.get('ok'):
                continue              # 上一輪已經填好、等他核准的不重填(要重填就指定 --url)
            out.append(u)
    return out


RULES = """規矩:
- 答案只有一個真相:表單答案庫。使用者確認過的共用答案程式已經抄在下面(【共用答案】),這張以前記過的欄位也在下面。
  答案庫有的直接用(英文表單填英文 v,中文表單填中文),不要自己改寫。
- 使用者本人的事實(姓名、Email、電話、居住地、學歷、個人連結)照這張要用的履歷母稿:{master}。
- 答案庫沒有、履歷也對不上的題目:每一題都幫他填好,不要留空。他最後一定會自己審核,唯一的硬規定是不准自動送出。
  意見、承諾、時間安排、薪資也一樣:照他的履歷、這個 JD 和他的求職條件,選對他最有利的答案。這些都算你推論的:
  {other}答案一定附{read}翻譯(zh),在 why 寫一句你為什麼這樣填,並判斷這題是「共用」(關於他本人)還是「這缺專用」(針對這個 JD)。
  他在看板上確認之前,核准送出按不下去,所以放心填。
- 推論的答案只准用母稿和答案庫寫到的事實:不補他沒做過的經驗、不加頭銜、不誇大。
- 使用者自己交代的填表做法(照做):
{apply_rules}
- 需要登入、遇到驗證碼、頁面要密碼:停下,照實寫進輸出,不要嘗試繞過。
  要不要登入以實際結果為準:先照正常流程按「應徵/Apply」,真的被帶到登入頁或跳出登入框才算。
  頁首顯示「登入/註冊」不代表沒登入(有些網站職缺頁跟會員中心在不同網域,頁首不會跟著變)。
- 不要動任何跟這張職缺無關的東西,不要改 jobsalvo 的程式和文件。
- 卡上「原因」那格(看板資料的 n)是使用者對這個職缺的話,絕對不要寫進去;投遞紀錄只寫進上面指定的輸出檔。"""


def apply_rules():
    """使用者的填表做法(<home>/apply-rules.md),原文縮排後放進規矩。沒有就寫一句沒有。"""
    try:
        with open(cf.APPLY_RULES, encoding='utf-8') as f:
            t = f.read().strip()
    except OSError:
        t = ''
    return '\n'.join('  ' + l for l in t.splitlines()) if t else '  (沒有另外交代)'


# 使用者對這一輪的授權,照實寫給 agent:要送出去的資料、目的地、到哪一步為止。
AUTH_FILL = """使用者的授權:這張職缺是他自己放進「可投遞」的。他要你把資料填進這個申請表、停在送出前,
等他在看板上看過真的頁面、按核准才送出。
要填進去的資料:他的姓名、Email、電話、居住地、個人連結(照母稿 {master})、答案庫裡的答案、{ship} 裡要上傳的檔。
目的地:{url} 這一個申請表。只填這些、只填進這一個申請表;送出不在這一輪的授權裡。"""

FILL = """你是代投 agent。這一輪只做「填好、不送出」,送出要等使用者在看板上按核准。

職缺:{title}
職缺頁:{url}
這張的可投遞夾:{ship}(ship.json 的 files 是個別檔, merged 是程式產的合併版)
這張之前記過的表單(答案庫的 k 指向哪一條):{prior}

【共用答案】(使用者確認過、每張表單都一樣的答案,程式從答案庫抄的):
{shared}

{auth}

{rules}
{notes}
步驟:
0. 平台上的履歷。{profile_step}
{open_step}
   打開後先判斷:這一頁是不是上面「職缺」那個缺(看板上的名字可能過時或寫法不同,以頁面本身為準)。
   是同一個缺就照做,把頁面上的職稱寫進輸出的 posting;是別的職缺、或職缺已經關了,就不要填,
   posting 的 same_job 寫 false、problems 寫你看到什麼,停下。
2. 每一欄照上面的規矩填好,依最後面的「上傳規則」選一套檔案。
   確定的動作合成一次呼叫:同一頁好幾欄就寫在同一段 js 裡依序填完、最後一起讀回確認,不要填一欄讀一次;
   要看頁面也一次讀完需要的東西。每一次呼叫都要花時間,呼叫次數就是這一輪要多久。
   讀回只讀你填的那幾欄的值;不要把整頁結構(domSnapshot、無障礙樹)或整頁截圖倒出來看:一次十幾萬字,
   後面每一步都要揹著它,只會變慢。填完的畫面程式會自己截圖、自己讀一遍那個分頁。
   上傳欄的 setFiles 沒報錯就算選上了,不用再去讀 input.files 或 FormData(外掛在頁面上跑的程式讀不到它們)。
   正式或遠端平台一律用瀏覽器真正的檔案選擇器上傳:若目前 browser API 有 Playwright,先在目前分頁呼叫 tab.playwright.waitForEvent("filechooser"),再點 input[type="file"] 或它的可見標籤,最後對 chooser 呼叫 setFiles([檔案的絕對路徑]);若只有 CUA,點上傳欄後在實際的系統檔案選擇視窗選取同一個絕對路徑。
   唯一例外是本輪明確標示為本機假驗收頁、網址是 loopback 的測試頁;只有在頁面可見標記且讀到實際表單的 action 和 file input name 後,才可依後續附件規則用 curl multipart 上傳合成測試檔。不可猜 endpoint、呼叫正式平台 API、改頁面程式,或把本機來源檔寫進回報冒充上傳。正式頁面無法用選檔器時,停止並在 problems 照實回報,不可宣稱成功。
   注意:外掛讀回頁面時,type=email / tel / password 的欄位一律讀成空的(外掛把個資藏起來),其實已經打進去了;
   要確認就看截圖,不要一直重打。外掛也不准改頁面上的程式(window 是凍結的),不用試著在頁面上裝東西。
   答案庫的答案如果平台上已經存好一份(例如 104 的自我推薦信,可以從推薦信下拉選存好的那封),直接選那一份,不要重打、不要自己另寫,也不要用平台自帶的預設句。
3. 絕對不要按送出、Submit、確認應徵。填完就停。對這個分頁呼叫 markHandoff():分頁要留著給使用者檢查;
   他核准之後,你(同一段對話)會被叫回來在這一頁送出;他要改東西時,你也會被叫回來在這一頁改。
4. 把結果寫成 {out}/fill.json(截圖不用你存,外掛不准你寫圖檔,程式會自己從那一頁截)。格式照下面寫就好,不用去讀 jobsalvo 的程式原始碼。
   寫完在同一個指令接著跑 {record_cmd} 把這張表單記進看板(只記這一次,不用另外呼叫 form_record);
   有錯它會說哪一欄不對,照訊息改 fill.json 再跑一次。
   fields 每一欄都有 "q"(表單上的題目)和 "value"(頁面上現在的值,照抄全文,長答案也整段照抄,不要寫成說明或字數),再照來源加:
     履歷直接對上的  "src": "rz"
     答案庫現成的    "src": "bank", "k": 那條的 k(上面「之前記過的表單」有 k 和原文)
     刻意不填的      "src": "skip", "why": 為什麼
     新答案          "src": "bank", "zh": 中文(英文答案必附), "why": 依據,
                     "kind": "txt"(短文)/"op"(意見)/"pick"(選項)/"val"(數字日期)/"ck"(勾選), "pj": 1(這缺專用) 或 0, "pjw": 理由,
                     "bank_q": 這題的通用問法(選填;表單問法很特別時給)
    {{"url": ..., "platform": ..., "tab_id": "那個分頁的 id", "tab_url": "那個分頁現在的網址", "handoff": true,
    "posting": {{"title": "頁面上的職稱", "company": "頁面上的公司", "same_job": true/false}},
    "profile": {{"needed": true/false, "updated": ["改了哪幾段"], "url": "看得到全文的那一頁(程式不知道在哪時才要)", "edit": "編輯頁", "application_history_url": "可讀的應徵紀錄頁(有就填)", "equivalents": [{{"master": "程式列出的那一格原文", "platform": "頁面上實際顯示的字", "why": "..."}}], "note": "..."}},
    "uploaded": ["只有申請表實際收到的檔名;平台履歷管理頁的附件只寫在 profile_attachments"], "uploaded_files": [{{"name": "申請表上傳檔名", "path": "下載檔完整路徑"}}], "upload_readback": "downloaded 或 unavailable(平台讀不回上傳檔時)", "uploaded_from": [{{"name": "申請表上顯示的檔名", "path": "交給 setFiles 的完整路徑(讀不回時才填)"}}], "fields": [{{"q": "表單上的題目", "value": "頁面上現在的值", "src": "rz/bank/skip", "k": "答案庫的 k(有才給)"}}],
    "blank_for_him": [], "problems": ["沒填完、卡住的地方(沒有就空陣列)"],
    "notes": ["其他觀察,不影響填表"],
    "platform_notes": ["這一輪在這個平台試出來、下次直接照做會省事的做法,一句一條(例如某個編輯器要怎麼輸入才會存);沒有新發現就空陣列"],
    "platform_notes_remove": ["上面【這個平台以前學到的】裡,這次發現不對的那一句(照抄)"], "submitted": false}}
最後一行印 @@DONE@@。"""

# 平台筆記:agent 在某個平台試出來的做法(例如 104 的自訂經歷編輯器要怎麼輸入才會存),照平台存起來,
# 下一輪同平台直接附在指示裡。以前每一輪都從頭試:改一句話試了 22 次。
# 只當提示;平台改版做法失效時,agent 寫 platform_notes_remove 拿掉那一句。
NOTES_MAX = 12


def note_key(url):
    """平台筆記照平台分:認得的平台用名字(104、cake…),其他用網域最後兩段(job-boards.greenhouse.io → greenhouse.io)。"""
    import profile_sync as ps
    from urllib.parse import urlsplit
    known = ps.platform_of(url)
    if known:
        return known
    host = (urlsplit(str(url or '')).hostname or '').casefold()
    return '.'.join(host.split('.')[-2:]) if host else ''


def _notes_path():
    return cf.path('platform-notes.json')


def platform_notes(url):
    try:
        with open(_notes_path(), encoding='utf-8') as f:
            return [str(x) for x in (json.load(f).get(note_key(url)) or [])][-NOTES_MAX:]
    except (OSError, ValueError, AttributeError):
        return []


def remember_notes(url, add, remove=None):
    """收 agent 這一輪寫的平台筆記:去掉重複、它說不對的那幾句,每個平台留最新的 NOTES_MAX 條。"""
    key = note_key(url)
    add = [str(x).strip()[:200] for x in (add if isinstance(add, list) else []) if str(x).strip()]
    remove = {' '.join(str(x).split()) for x in (remove if isinstance(remove, list) else [])}
    if not key or not (add or remove):
        return
    try:
        with open(_notes_path(), encoding='utf-8') as f:
            store = json.load(f)
        if not isinstance(store, dict):
            store = {}
    except (OSError, ValueError):
        store = {}
    kept = [x for x in store.get(key) or [] if ' '.join(str(x).split()) not in remove]
    for x in add:
        if ' '.join(x.split()) not in {' '.join(y.split()) for y in kept}:
            kept.append(x)
    store[key] = kept[-NOTES_MAX:]
    tmp = _notes_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _notes_path())


# 第 1 步:程式沒幫它開好申請頁時(開不起來),照舊自己開
OPEN_STEP = """1. 用 Codex 的 Chrome 外掛在 agent 專用的 Chrome 開一個背景分頁,打開申請表單。那裡如果還有這張職缺以前留下的分頁,
   先關掉那些(只關這張職缺的),使用者只會看到這一輪填的這一頁。
   若舊分頁屬於另一段 browser session、這一輪無法接管或關閉,保留不動,不要再操作它;另開本輪背景分頁並確認職缺後繼續。
   只要本輪新分頁能正常使用,不能關閉另一段 session 的舊分頁不算 problems;只有新分頁也無法操作才停下回報。"""


def prepared_step(prepared, instance):
    """程式已經開好申請頁、讀好欄位:agent 接手就好,不用自己開分頁、關舊的、從頭讀頁面(以前每輪五到十步)。"""
    page = prepared.get('page') or {}
    fields = []
    for f in (page.get('fields') or [])[:60]:
        value = f.get('shown') or f.get('value')
        value = ', '.join(value) if isinstance(value, list) else str(value or '')
        fields.append(f"   - {str(f.get('label') or f.get('name') or '(沒有標籤)')[:80]} [{f.get('type')}]"
                      + (f" = {value[:80]}" if value else ''))
    lines = [str(x)[:120] for x in (page.get('lines') or [])[:60]]
    return (
        f"1. 申請頁程式已經開好在 agent 專用的 Chrome(背景分頁 id {prepared['tab_id']}),這張職缺以前的分頁也關了。\n"
        f"   接手:先 `await cua.listBrowsers()` 找出 metadata.extensionInstanceId 是 {instance} 的那一個"
        "(瀏覽器編號每段對話不同),再 `const tab = await cua.getTab('" + str(prepared['tab_id']) + "', {browser: 那個編號})`。"
        "不要另開分頁,也不要重新載入或重開這個網址(不要 goto 同一頁、不要 reload):頁面已經是最新的,"
        "重載會多等一次、有的平台還會換掉表單。這一頁不是申請表(要先按「應徵」、或要登入)就在這個分頁裡接著做。\n"
        f"   程式剛讀到這一頁(不用再整頁重讀,要確認哪一格再讀那一格):標題 {str(page.get('title') or '')[:120]};網址 {page.get('url') or ''}\n"
        + ('   欄位:\n' + '\n'.join(fields) + '\n' if fields else '   (這一頁還沒有表單欄位)\n')
        + ('   頁面文字(前 60 行):' + ' / '.join(lines) if lines else '')
    )


FIX = """接著你上一輪填的那張:{title}({url})。使用者看過之後要你改。

他寫給你的話:{note}
答案庫裡改過、網頁上還是舊的題目(照這裡的值改;英文表單填英文 v。答案庫的答案如果平台上已經存好一份,直接選那一份,不要重打、不要自己另寫,也不要用平台自帶的預設句):
{changed}

{translate}
{auth}
{notes}
步驟:
1. 回到你上一輪用 markHandoff() 留著的那個分頁(分頁 id {tab_id},網址 {tab_url})。不要開新分頁、不要重新載入、
   不要 goto:使用者要你在原本那一頁上改。分頁找不到了就不要自己重開,寫進 problems 停下。
2. 只改上面要改的地方,其他欄位不要動。不要送出。改的是答案的話,答案以答案庫為準(不要自己改寫)。
3. 對這個分頁再呼叫 markHandoff(),把 {out}/fill.json 整份重寫(截圖程式會自己截)
   (格式跟上一輪一樣,fields 照改完的頁面寫,再加 "fixed": ["改了哪幾格"]),
   寫完在同一個指令接著跑 {record_cmd} 重新記下這張表單;有錯照訊息改 fill.json 再跑。
最後一行印 @@DONE@@。"""

SUBMIT = """使用者在 {approve_at} 在看板上按了「✅ 核准送出」:他看過你留著的那一頁,核准把那一頁送出到 {url}。
這就是他對這次送出的確認:送出的資料就是下面的核准快照,目的地就是這一個申請表。核准時每一題的答案:
{snap}

職缺:{title}

步驟:
1. 回到你留著的那個分頁(分頁 id {tab_id},網址 {tab_url})。不要重填、不要開新分頁、不要重新載入:
   他核准的是他親眼看過的那一頁。分頁找不到了:不要送,寫進 problems 停下。
2. 讀一次頁面上每一欄現在的值,跟核准的答案逐題對。欄位空了、或有任何一題不同:不要送,寫進 problems 停下。
   外掛讀回頁面時有兩種讀不到,不算空:上傳欄的 input.files / FormData 讀不到(會是 0 個檔),
   要讀 input.value(選了檔會是「C:\\fakepath\\檔名」);type=email / tel / password 一律讀成空的,看截圖對。
3. 一樣才送:按送出,等頁面出現成功的證據(確認頁網址,或「Application submitted」「Thank you」「已送出」「應徵成功」這類字)。
   Greenhouse 成功時網址會變成 .../confirmation;停在「Page not found」或還在原本的職缺頁,就是沒送成。
4. 寫 {out}/submit.json:
   {{"submitted": true/false, "clicked": 有沒有按下送出, "confirm_url": "...", "confirm_text": "頁面上成功的那句話", "problems": [...]}}
5. 不管成功與否,都對那個分頁呼叫 markHandoff():程式會自己從那一頁截圖存證(外掛不准你寫圖檔),
   存完證據、確認送成功後由程式把分頁關掉;沒送成功就留著等使用者處理。
沒看到成功的頁面就算沒送出,照實寫 false。最後一行印 @@DONE@@。"""


def to_translate(fb, url):
    """這張表單用到、使用者改了中文而英文還沒照著重翻的答案(tr):[{k, zh, 舊的英文}]。
    沒有人翻的話核准永遠按不下去(approval_problem 擋 tr),所以填表、修改那一輪交給 agent 翻。"""
    ks = {x.get('k') for x in ((fb.get(url) or {}).get('form') or {}).get('f', []) if x.get('src') == 'bank'}
    return [{'k': e.get('k'), 'zh': e.get('zh') or '', 'old_en': e.get('v') or ''}
            for e in fb.get('__ans__', []) if e.get('tr') and e.get('k') in ks]


TRANSLATE = """要重翻的答案(使用者在看板上改了{read},{other}還是舊的;沒翻好核准按不下去):
{items}
每一條:照{read}翻成表單那一格的語言(意思照{read},不要自己加內容),填進表單那一格,再把翻好的記回答案庫:
   python3 - <<'PY'
   import sys; sys.path.insert(0,{tools!r}); import form_record as fr
   fr.translate('<k>', en='<你翻的英文>', live={board!r})
   PY
"""


def translate_block(fb, url, board):
    tr = to_translate(fb, url)
    if not tr:
        return ''
    read, other = fr.lang_words()
    return TRANSLATE.format(items=json.dumps(tr, ensure_ascii=False, indent=1), tools=HERE, board=board,
                            read=read, other=other) + '\n'


def changed_fields(fb, url):
    """答案改過、網頁上還是舊字的欄位(refill):題目 → 答案庫現在的值。"""
    bank = {e.get('k'): e for e in fb.get('__ans__', [])}
    out = {}
    for x in ((fb.get(url) or {}).get('form') or {}).get('f', []):
        if x.get('refill'):
            e = bank.get(x.get('k')) or {}
            out[x.get('q') or ''] = {'v': e.get('v') or '', 'zh': e.get('zh') or ''}
    return out


PROFILE_NONE = '這次不使用平台履歷,程式略過平台履歷文字比對;直接上傳仍會核對申請表實際收到的檔案。'
# 平台用自己的格式存的格子(下拉選單、日期、自己的用詞)字面永遠比不過:agent 在填表那一輪(平台履歷頁本來就開著)
# 順手回報「母稿這一格 ＝ 頁面上這幾個字」,程式驗過才記下,以後照記下的說法比。
# 以前是填完後另開一輪建「欄位對照」:打開平台編輯頁每一個視窗、列出每一格(104 一次 13 分鐘),
# 改母稿一個字就整份重做,一格沒見過的帳號欄位名稱就整份作廢(2026-09-27)。
EQUIVALENTS_RULE = (
    '\n平台用自己說法寫的格子:上面列的差異裡,如果平台上其實有同樣的資訊、只是寫法不同'
    '(下拉選單的選項、日期格式、平台自己的用詞),不用改平台,在輸出的 profile.equivalents 回報 '
    '[{"master":"「頁面上找不到的格子」裡的那一格(照抄那一格,不要貼整行;同一行好幾格就回報好幾筆)",'
    '"platform":"平台頁面上實際顯示的字(照抄,要是頁面上連在一起的一段)","why":"一句話"}];'
    '平台根本不顯示這一格(例如預覽頁沒有兵役)就寫 {"master":"…","not_shown":true,"why":"…"}。'
    '程式會確認你寫的字真的在頁面上才收下,收下後以後不再列;內容真的不同的照樣要改平台。'
    '格式照這裡寫就好,不用去讀程式原始碼。若平台有應徵紀錄頁,把網址填進 profile.application_history_url。'
)


def profile_step(url, lang, var, master, profile=None, custom=False, delivery=None):
    """只在 agent 回報實際使用平台履歷時提供文字比對指引。"""
    import profile_sync as ps
    if custom:
        return (
            '這張卡有已收下的客製檔。先看實際申請表:能直接上傳就用這張卡的檔;'
            '不能才另外開一份新的客製平台履歷,放這張卡的檔並在投遞時選它。'
            '固定平台履歷只能讀,不可以修改或放客製檔。回報 profile.url 時只填固定平台履歷網址;'
            '新開那份網址只放在 delivery.profile_url。若固定版找不到,或平台格子滿了需要刪檔才能繼續,'
            '停止並回報,不要自行處理。'
        )
    method = (delivery or {}).get('method')
    if method in ('direct_upload', 'no_profile'):
        return PROFILE_NONE
    if method != 'platform_profile':
        return (
            '本輪尚未回報投遞方式。依實際申請頁選擇 direct_upload、no_profile 或 platform_profile,'
            '並照實回報 delivery。程式只在 method=platform_profile 時比平台履歷文字;'
            'direct_upload 和 no_profile 會跳過平台履歷文字比對。'
        )
    plat = ps.profile_key(url)
    if not plat or not lang or not var:
        return '已回報使用平台履歷,但程式缺少申請頁或履歷版本資料,無法比對文字。'
    if profile is None:
        profile_url = str((delivery or {}).get('profile_url') or '').strip()
        if profile_url:
            return (
                f'這次實際使用的平台履歷是 {profile_url};程式會在填表後讀回來比對母稿 {master}。'
                '若這份固定履歷還沒有登記,回報 profile.url 和 profile.edit,讓程式記下位置。' + EQUIVALENTS_RULE
            )
        return '已回報使用平台履歷,但沒有網址,程式無法讀回來比對文字。'
    w, ds, prob = profile
    if not w:
        return (f'這個平台({plat})要在平台上存一份履歷、投遞時選那一份,程式還不知道 {lang}/{var} 那一份在哪。'
                f'打開平台上那一份,逐段跟母稿 {master} 比,不一致就改到一致並存檔。把「看得到全文的那一頁」網址寫進輸出的 '
                'profile.url、編輯頁寫進 profile.edit,程式下次起會自己讀回來比。' + EQUIVALENTS_RULE)
    if prob:
        return (f'程式想讀回平台上那一份({w["read"]})但{prob}。你打開 {w["edit"]} 逐段跟母稿 {master} 比,'
                '不一致就改並存檔。' + EQUIVALENTS_RULE)
    if not ds:
        return f'程式剛把平台上那一份({w["read"]})讀回來跟母稿比過,都對得上,這一步跳過,不要動它。'
    return (f'程式剛把平台上那一份({w["read"]})讀回來跟母稿 {master} 比,下面這幾格對不上。只處理這幾格(在 {w["edit"]}),'
            '其他沒列的不要動。改完存檔,程式會再讀回來比一次。\n'
            + ps.describe(ds) + EQUIVALENTS_RULE)


def _pick(url):
    record = ship.info(url, root=SHIP_ROOT)
    return record.get('lang'), record.get('variant')


def profile_check(url, board=None, delivery=None, reported=None):
    """只有 agent 回報使用平台履歷時,才讀回文字比對。"""
    import profile_sync as ps
    if (delivery or {}).get('method') != 'platform_profile':
        return None
    lang, var = _pick(url)
    platform = ps.profile_key(url)
    if not platform or not lang or not var:
        return None
    try:
        return ps.check(platform, lang, var, board, reported=reported)
    except Exception as e:
        return ps.where(platform, lang, var), [], f'比對出錯({str(e)[:80]})'


def profile_after(url, res, board=None):
    """agent 回報使用平台履歷後,讀回固定版文字;對不上就不放行。
    agent 回報的「平台用自己說法寫」(profile.equivalents)在同一次讀回裡先驗、再比。"""
    import profile_sync as ps
    delivery = res.get('delivery') or {}
    if delivery.get('method') != 'platform_profile':
        return []
    profile_url = str(delivery.get('profile_url') or '').strip()
    profile_kind = delivery.get('profile_kind')
    if not profile_url or profile_kind not in ('fixed', 'custom'):
        return ['agent 沒回報可核對的平台履歷網址或版本']
    platform = ps.profile_key(url)
    lang, var = _pick(url)
    if not platform or not lang or not var:
        return ['程式缺少平台或履歷版本資料,無法讀回平台履歷比對文字']

    custom_profile_url = profile_url if profile_kind == 'custom' else None
    pr = res.get('profile') or {}

    def profile_url_key(value):
        from urllib.parse import urlsplit
        parsed = urlsplit(value or '')
        return (parsed.scheme.lower(), parsed.netloc.lower(),
                parsed.path.rstrip('/'), parsed.query)

    is_custom_profile = (
        custom_profile_url and pr.get('url')
        and profile_url_key(pr['url']) == profile_url_key(custom_profile_url)
    )
    fixed = ps.where(platform, lang, var)
    reported_fixed_url = pr.get('url') or (
        profile_url if profile_kind == 'fixed' else None
    )
    if (reported_fixed_url and not is_custom_profile and not fixed
            and ps._safe_page_url(reported_fixed_url)
            and ps._safe_page_url(pr.get('edit') or reported_fixed_url)):
        ps.remember(platform, lang, var, reported_fixed_url,
                    pr.get('edit') or reported_fixed_url)

    label = '固定平台履歷' if profile_kind == 'custom' else '平台上的履歷'
    if pr.get('application_history_url'):
        ps.remember_application_history(platform, pr['application_history_url'])
    reported = pr.get('equivalents') if not is_custom_profile else None
    result = profile_check(url, board, delivery, reported=reported)
    w, ds, prob = result or (None, [], '')
    if not w:
        return [f'{label}({platform} {lang}/{var})程式不知道在哪,沒辦法讀回來驗']
    if prob:
        return [f'{label}讀不回來驗:{prob}']
    return [f'{label}還有 {len(ds)} 段跟母稿對不上:' + '; '.join(d['where'] for d in ds[:5])] if ds else []


def _upload_rule(directory, record):
    files = [n for n in record.get('files', []) if isinstance(n, str) and n]
    merged = record.get('merged')
    separate = '\n'.join('  ' + os.path.join(directory, n) for n in files) or '  (沒有個別檔)'
    merged_path = os.path.join(directory, merged) if isinstance(merged, str) and merged else None
    if not merged_path or not os.path.isfile(merged_path):
        return '可投遞夾沒有合併版；不要自行合併或上傳個別檔，停止並回報。'
    return f"""上傳規則(依頁面上看得到的獨立上傳欄數):
  只有一個上傳欄:只上傳合併版 {merged_path};不要再上傳個別檔。
  恰好兩個上傳欄、標籤是 Resume 與 Cover Letter、而且沒有附件欄:只把合併版 {merged_path} 放進 Resume;Cover Letter 留空,或只在有文字輸入框時填文字。不可把履歷另存或拼成求職信,也不可把履歷或附件拆開塞進 Cover Letter。
  除上述兩槽特例外,有兩個以上上傳欄:不要上傳合併版;依欄位標籤分別上傳履歷與附件。若附件欄允許多檔,把附件個別放進去。
個別檔案:
{separate}
合併版:{merged_path}"""


def prompt_for(stage, url, j, fb, board, note='', profile=None,
              attachment_download_dir=None, prepared=None):
    d = ship.folder(url, root=SHIP_ROOT)
    record = ship.read_info(d)
    lang, var = record.get('lang'), record.get('variant')
    master = cf.master(var, lang) or '(找不到這張要用的母稿,照可投遞夾裡的履歷檔)'
    out = out_dir(url, board)
    a = apply_of(fb, url)
    kw = dict(title=card.name(j), url=url, ship=d or '(這張還沒有可投遞夾)', out=out, board=board, master=master,
              tools=HERE, apply_rules=apply_rules(), read=fr.lang_words()[0], other=fr.lang_words()[1],
              tab_id=a.get('tab_id') or '(沒記到)', tab_url=a.get('tab_url') or url)
    kw['record_cmd'] = ' '.join(shlex.quote(x) for x in (
        'python3', os.path.join(HERE, 'form_record.py'), '--from-fill', os.path.join(out, 'fill.json'),
        '--url', url, '--board', board))
    kw['rules'] = RULES.format(**kw)
    learned = platform_notes(url)
    kw['notes'] = ('\n【這個平台以前學到的】(前幾輪的 agent 試出來的做法;先照做,不對就照實際情況做,'
                   '並在 fill.json 的 platform_notes_remove 寫出那一句):\n'
                   + '\n'.join('- ' + x for x in learned) + '\n') if learned else ''
    if prepared:
        import agent_chrome
        kw['open_step'] = prepared_step(prepared, agent_chrome.conf().get('instance'))
    else:
        kw['open_step'] = OPEN_STEP
    kw['auth'] = AUTH_FILL.format(**kw)
    import profile_sync as ps
    # 填表、修改只填申請表;平台履歷附件的下載核對放到填完之後,而且只在附件更新過時做
    attachment = ps.attachment_step(j, fb, url, attachment_download_dir, verify_profile=False)
    if stage == 'fill':
        f = (fb.get(url) or {}).get('form') or {}
        # 指向答案庫的欄位連答案原文一起給:只給 k 的話,agent 每一輪都要自己去翻 board.html 找那條(一次四五步)
        bank = {e.get('k'): e for e in fb.get('__ans__', []) if isinstance(e, dict)}
        prior = [dict(x, **{kk: bank[x['k']][kk] for kk in ('v', 'zh') if bank[x['k']].get(kk)})
                 if isinstance(x, dict) and x.get('k') in bank else x for x in f.get('f', [])]
        kw['prior'] = json.dumps(prior, ensure_ascii=False) if f else '(還沒記過)'
        kw['shared'] = _run_text([sys.executable, os.path.join(HERE, 'form_record.py'), '--board', board, '--shared'])
        kw['profile_step'] = profile_step(
            url, lang, var, master, profile,
            custom=ps.has_custom_resume(j, fb),
            delivery=a.get('delivery'),
        )
        return (FILL.format(**kw) + '\n\n' + _upload_rule(d, record)
                + ('\n' + translate_block(fb, url, board) if to_translate(fb, url) else '')
                + attachment), out
    if stage == 'fix':
        ch = changed_fields(fb, url)
        kw['note'] = (note or '').strip() or '(沒有另外寫;照下面改過的答案重打)'
        kw['changed'] = json.dumps(ch, ensure_ascii=False, indent=1) if ch else '(沒有)'
        kw['translate'] = translate_block(fb, url, board)
        return FIX.format(**kw) + attachment, out
    kw['snap'] = json.dumps(fr.snapshot(fb, url), ensure_ascii=False, indent=1)
    kw['approve_at'] = ((fb.get(url) or {}).get('approve') or {}).get('at') or '(沒記到時間)'
    return SUBMIT.format(**kw), out

def pre_submit_prompt(url, j, fb, board, attachment_download_dir, after_fill=False):
    """讀回平台履歷上的所有附件給程式比;這一步絕不送出。
    after_fill:剛填好、附件更新後第一次用這份平台履歷(附件沒變就不用取)。"""
    import profile_sync as ps
    delivery = apply_of(fb, url).get('delivery') or {}
    # 填完後的核對:附件和欄位對照各自看要不要做。以前這裡一律重抓附件,只是欄位對照要重建也把三個附件再下載一次
    step = ps.attachment_step(j, fb, url, attachment_download_dir, force=not after_fill)
    out = out_dir(url, board)
    head = (f'平台履歷核對:{card.name(j)}(附件更新後,第一次用這份平台履歷)\n' if after_fill
            else f'送出前的最後核對:{card.name(j)}\n')
    prompt = (
        head +
        f'核准時回報的平台履歷是 {json.dumps(delivery, ensure_ascii=False)}。\n'
        '在目前申請頁仍保持未送出的狀態,重新打開並檢查同一份平台履歷。'
        '把本次 delivery(下面要下載附件時連同 profile_attachments)回報寫到輸出資料夾的 pre-submit.json。'
        '這次只做檢查,不可按送出、刪除或修改平台上的附件;程式比對通過後會另行要求送出。\n'
        + step
    )
    return prompt, out


PROFILE_CHECK_TIMEOUT = 30 * 60


def _profile_check_after_fill(url, board, sid, agent_id, status=None):
    """填好之後:這張用的平台履歷還沒用「現在的履歷和附件」核對過,就叫回同一段對話核對一次。
    核對過而且指紋沒變(同一份履歷、附件沒改)就不派 agent。回問題清單。"""
    import profile_sync as ps
    jobs, fb = load(board)
    job = jobs.get(url)
    delivery = apply_of(fb, url).get('delivery') or {}
    if job is None or not sid or delivery.get('method') != 'platform_profile' or delivery.get('profile_kind') != 'fixed':
        return []
    if ps.profile_attachments_fresh(job, fb, url):
        return []           # 附件用現在的檔核對過:不用再派 agent
    with tempfile.TemporaryDirectory(prefix='jobsalvo-profile-attachments-') as downloads:
        p, out = pre_submit_prompt(url, job, fb, board, downloads, after_fill=True)
        outcome = _run_agent(
            p, os.path.join(out, 'profile-check.log'), cf.HOME, board, timeout=PROFILE_CHECK_TIMEOUT,
            browser_required=True, browser=ar.apply_overrides(), resume=sid, agent_id=agent_id,
            on_start=(lambda proc: status(proc.pid)) if status else None,
        )
        if not outcome.ok:
            return ['平台履歷核對沒跑完:' + outcome.message()]
        try:
            with open(os.path.join(out, 'pre-submit.json'), encoding='utf-8') as fh:
                report = json.load(fh)
        except Exception:
            return ['平台履歷核對沒有寫出 pre-submit.json']
        bad = _check_delivery_attachments(fb, url, job, report, downloads, expected_delivery=delivery)
    return bad


def _run_text(argv):
    """跑一支固定的程式、拿它印的字塞進 prompt(以前叫 agent 自己跑)。跑不了就照實寫,agent 看得到。"""
    try:
        r = subprocess.run(argv, cwd=cf.HOME, capture_output=True, text=True, timeout=120)
        return (r.stdout.strip() or r.stderr.strip() or '(沒有輸出)')[:12000]
    except Exception as e:
        return f'(程式跑不了:{str(e)[:120]},這一段你自己照母稿判斷)'


def _run_agent(prompt, log, home, board, **kwargs):
    """Give the child agent the same board target used by this run."""
    return ar.run(prompt, log, home, board=board, **kwargs)


def preview(stage, url=None, board=None, note=''):
    """按下去會送給 agent 的那一份 prompt,一字不差(看板按鈕底下顯示用)。不派 agent、不寫檔、不連網。
    url 給空就挑一張現在符合這個階段的卡;沒有卡就回一句說明。前面加的規矩跟 agent_run.argv_for 一樣。"""
    board = board or bd.LIVE
    jobs, fb = load(board)
    if stage not in STAGES:
        return f'不知道「{stage}」是哪一段(只有 fill、fix、submit)'
    if not url:
        todo = eligible(jobs, fb, stage)
        if not todo:
            return {'fill': '現在沒有要填的卡(可以投了裡的都填好在等你確認送出,或已經送出)',
                    'fix': '現在沒有要 agent 改的卡(沒有答案改過、網頁待重打的)',
                    'submit': '現在沒有確認過、可以送出的卡'}[stage]
        url = todo[0]
    if url not in jobs:
        return '看板上沒有這張職缺'
    p, _ = prompt_for(stage, url, jobs[url], fb, board, note)
    return ar.rules_for('main', browser=ar.apply_overrides(), board=board) + p


def preview_meta(stage, board=None):
    """preview(stage) 沒指定 url 時拿來當例子的是哪一張:{'url', 'title'};沒有符合的卡回 None。跟 preview 同一套挑法。"""
    jobs, fb = load(board or bd.LIVE)
    if stage not in STAGES:
        return None
    todo = eligible(jobs, fb, stage)
    return {'url': todo[0], 'title': card.name(jobs[todo[0]])} if todo else None


def rename(url, title, company, board):
    """agent 在申請頁上確認是同一個缺:卡片名字照頁面改(看板上的名字可能是舊的)。後面的連結原樣留著。"""
    new = card.name(title, company)

    def mut(data, fb):
        for j in data['jobs']:
            if j.get('id') == url:
                old = j.get('target') or ''
                if card.name_key(old) != card.name_key(new) and card.name_key(title) not in card.name_key(old):
                    j['target'] = card.with_name(old, new)
                    moved.append((old, j['target']))
    moved = []
    bd.set_data(mut, live=board)
    if moved and bd.is_live(board):
        ship.rename(url, new)   # 可投遞夾照名字取名,跟著改(裡面有這一輪的 .apply/)


def _same(a, b):
    return re.sub(r'\s+', ' ', str(a or '')).strip() == re.sub(r'\s+', ' ', str(b or '')).strip()


def _clean_attachment_downloads(path):
    if not path:
        return ''
    try:
        if os.path.islink(path) or not os.path.isdir(path):
            os.remove(path)
        else:
            shutil.rmtree(path)
    except FileNotFoundError:  # 附件檢查已清過時,外層 finally 重複清理視為成功。
        pass
    except OSError as e:
        return f'平台附件比對暫存檔清理失敗({str(e)[:80]})'
    return ''


def _check_delivery_attachments(fb, url, job, report, download_dir, force=False,
                                expected_delivery=None, verify_profile=True):
    import profile_sync as ps
    try:
        problems = ps.check_attachments(
            job, fb, url, report, download_dir, force=force,
            expected_delivery=expected_delivery, verify_profile=verify_profile,
        )
    except Exception as e:
        problems = [f'平台附件比對出錯({str(e)[:80]})']
    cleanup_problem = _clean_attachment_downloads(download_dir)
    if cleanup_problem:
        problems.append(cleanup_problem)
        delivery = (report or {}).get('delivery') or {}
        ps.invalidate_attachment_check(delivery.get('profile_url'))
        if delivery.get('profile_kind') == 'custom':
            fixed = ps.fixed_profile_delivery(job, fb, url)
            if fixed:
                ps.invalidate_attachment_check(fixed.get('profile_url'))
    return problems


def check_fill(fb, url, out, t0, sid=None, reader=None, job=None,
               attachment_download_dir=None, runtime='codex', log=None):
    """程式自己對一遍 agent 填的結果。回 (問題清單, fill.json);問題清單空的就是沒事。
    除了看 agent 寫的 fill.json,還用 apply_tab 直接去讀它留在他 Chrome 裡的那一頁(不經過 agent):
    分頁在不在、是不是還是填好的那一頁(沒被送出、沒換頁)、答案庫的每個答案是不是真的在頁面上、上傳的檔是不是真的選上了。
    runtime:填這一輪的是哪一種 agent;Codex、Claude 的分頁各用自己家的門路讀(apply_tab.read),驗收標準一樣。
    log:這一輪的紀錄;Claude 的頁面內容從這裡拿(它那一輪最後自己讀一次,見 apply_tab.CLAUDE_SELF_READ)。"""
    bad = []
    f = (fb.get(url) or {}).get('form') or {}
    if f.get('at') != today():
        bad.append('表單沒有記進看板')
    bad += fr.validate(fb)
    try:
        with open(os.path.join(out, 'fill.json'), encoding='utf-8') as fh:
            res = json.load(fh)
    except Exception:
        return bad + ['沒有寫出 fill.json'], {}
    if res.get('submitted'):
        bad.append('⚠ 填表階段回報「已送出」,要人看')
    if (res.get('posting') or {}).get('same_job') is False:
        bad.append('agent 判斷這一頁不是這張卡的職缺(或已經關了),沒有填:' + str((res.get('posting') or {}).get('title') or '')[:60])
    if (not os.path.isfile(os.path.join(out, 'fill.png'))
                          or os.path.getmtime(os.path.join(out, 'fill.png')) < t0):
        bad.append('沒有這一輪的截圖')
    if not res.get('tab_id') or res.get('handoff') is not True:
        bad.append('填好的分頁沒有留在他的 Chrome(他要在真的頁面上檢查)')
    # agent 自己回報的值只在程式讀不到那一頁時拿來比:讀得到的話 page_problems 直接看頁面上答案庫的答案在不在,
    # agent 回報時把長答案寫成「(bank answer, 556 chars)」這種說明也不會被誤判成填錯
    bank = {e.get('k'): e for e in fb.get('__ans__', [])}
    reported = []
    for x in res.get('fields') or []:
        e = bank.get(x.get('k'))
        if e and e.get('v') and not _same(x.get('value'), e.get('v')) and not _same(x.get('value'), e.get('zh')):
            reported.append(f'「{x.get("q")}」頁面上是 {str(x.get("value"))[:40]!r},答案庫是 {str(e.get("v"))[:40]!r}')
    if not (sid and res.get('tab_id')):
        bad += reported
    else:
        import apply_tab
        try:
            page = reader(sid, res['tab_id']) if reader else apply_tab.read(sid, res['tab_id'], runtime=runtime, log=log)
        except Exception as e:
            bad.append(f'程式讀不到留在他 Chrome 的那一頁({str(e)[:80]})')
            bad += reported
        else:
            uploaded_on_form = res.get('uploaded') or []
            if (res.get('delivery') or {}).get('method') == 'platform_profile':
                uploaded_on_form = []
            bad += apply_tab.page_problems(
                page, fb, url, uploaded_on_form, res.get('tab_url'),
            )
    if job is not None:
        bad += _check_delivery_attachments(
            fb, url, job, res, attachment_download_dir, verify_profile=False,
        )
    return bad + [f'卡住:{p}' for p in (res.get('problems') or [])], res   # notes 是不影響填表的觀察,不算問題


def fill_record(stage, prev, res, bad, sid, shot, agent_id=None, note=''):
    """填表/修改這一輪寫進看板的 apply 紀錄(卡上的代投那一行、核准規則都讀它)。
    副本的假流程(job_fake)也用這一支組紀錄:以前兩邊各寫一份,假的少了 delivery 就走不到核准。"""
    prev = prev or {}
    rec = {'stage': stage, 'at': now(), 'ok': not bad, 'issues': bad[:10], 'shot': shot,
           'profile': res.get('profile') if stage == 'fill' else prev.get('profile'),
           'delivery': res.get('delivery'),
           'blank': res.get('blank_for_him') or [], 'uploaded': res.get('uploaded') or prev.get('uploaded') or [],
           'notes': (res.get('notes') or [])[:5], 'tab_id': str(res.get('tab_id') or prev.get('tab_id') or ''),
           'tab_url': res.get('tab_url') or prev.get('tab_url') or '', 'session': sid,
           'agent_id': agent_id or prev.get('agent_id') or 'primary', 'where': 'chrome'}
    if stage == 'fix':
        rec['fixes'] = (prev.get('fixes') or []) + [{'at': rec['at'], 'note': (note or '').strip(),
                                                      'fixed': res.get('fixed') or []}]
    return rec


def submit_evidence(res, shot):
    """送出成功頁的證據(apply_mark_sent 收的那一份);假流程也用它。"""
    return {'at': now(), 'url': res.get('confirm_url'), 'text': res.get('confirm_text'), 'shot': shot}


def check_submit(out, t0):
    try:
        with open(os.path.join(out, 'submit.json'), encoding='utf-8') as fh:
            res = json.load(fh)
    except Exception:
        return False, {'problems': ['沒有寫出 submit.json']}
    shot = os.path.join(out, 'submit.png')
    ok = bool(res.get('submitted') and (res.get('confirm_text') or res.get('confirm_url'))
              and os.path.isfile(shot) and os.path.getmtime(shot) >= t0)
    return ok, res


def shoot(sid, out, stage, tab_id=None, runtime='codex'):
    """截圖由程式自己截:外掛不准 agent 寫圖檔(它截得到但存不下來)。
    從它留著的那一頁當場截,存成 fill.png / submit.png;分頁照樣留著(apply_tab 會重新標 markHandoff)。"""
    try:
        with open(os.path.join(out, ('submit' if stage == 'submit' else 'fill') + '.json'), encoding='utf-8') as fh:
            tid = json.load(fh).get('tab_id')
    except Exception:
        tid = None
    tid = tid or tab_id                        # 送出那一輪 agent 不一定寫 tab_id,用看板上記的那一個
    if not (sid and tid):
        return
    import apply_tab
    import time
    # 按下送出後頁面剛換成確認頁,第一次截常撞上還在載入;多試幾次,真的截不到把原因留在 shot-error.txt 當證據
    for attempt in range(3):
        try:
            apply_tab.shot(sid, tid, os.path.join(out, ('submit' if stage == 'submit' else 'fill') + '.png'), runtime=runtime)
            return
        except Exception as e:
            with open(os.path.join(out, 'shot-error.txt'), 'a', encoding='utf-8') as fh:
                fh.write(f'{stage} 第 {attempt + 1} 次:{type(e).__name__}: {str(e)[:300]}\n')
            time.sleep(3)
    # 三次都截不到就沒有圖,後面的檢查會照實標「沒有這一輪的截圖」


def _no_session(url, board, stage):
    """要叫回的那段對話不在:不改也不送,核准作廢,要重新填一次給他看。"""
    def mut(fbx):
        m = fbx.setdefault(url, {})
        m.pop('approve', None)
        m['apply'] = dict(m.get('apply') or {}, ok=False, at=now(),
                          issues=['agent 填這張的那段對話找不回來了,要重新填一次給你看'])
    bd.set_fb(mut, live=board, by='apply_run')
    return False, ('沒送' if stage == 'submit' else '沒改') + ':那段對話找不回來了,要重新填一次給他看'


def _block_profile_submit(url, board, fb, problems):
    app = dict(apply_of(fb, url))
    app.pop('attachment_cache', None)
    app.update(stage='fix', at=now(), ok=False, issues=problems[:10])
    def mut(d):
        d.setdefault(url, {})['apply'] = app

    bd.set_fb(mut, live=board, by='apply_run')
    agent_report.report(
        '代投', '送出前平台履歷或附件比對沒通過:' + problems[0],
        need='先讓 agent 修好平台履歷欄位或附件,再重新確認送出',
        job=url, live=board,
    )
    return False, '; '.join(problems)


def _run_one(stage, url, board, dry=False, status=None, note='', attachment_download_dir=None):
    jobs, fb = load(board)
    approved_answers = None
    if stage == 'submit':
        problem = fr.approval_problem(fb, url)
        if problem:
            return False, problem
        approved_answers = fr.snapshot(fb, url)
    sid = apply_of(fb, url).get('session') if stage in ('fix', 'submit') else None
    pinned_agent_id = (apply_of(fb, url).get('agent_id') or 'primary') if sid else None
    if stage in ('fix', 'submit') and not sid and not dry:
        return _no_session(url, board, stage)
    delivery = apply_of(fb, url).get('delivery') or {}
    profile = None
    if stage == 'fill' and not dry:
        profile = profile_check(url, board, delivery)
    if stage == 'submit' and delivery.get('method') == 'platform_profile':
        checked = profile_check(url, board, delivery)
        if not checked:
            return _block_profile_submit(url, board, fb, ['程式不知道平台固定履歷在哪,無法重新核對'])
        _profile_entry, differences, problem = checked
        if problem or differences:
            detail = problem or ('欄位與母稿不符:' + '; '.join(
                x['where'] + (':' + '/'.join(x.get('missing') or []) if x.get('missing') else '')
                for x in differences[:5]
            ))
            return _block_profile_submit(url, board, fb, [detail])
    import profile_sync as ps
    # 送出前:這份平台履歷已經用現在的履歷和附件核對過,就不再下載一遍(以前每張 104 送出前都重來一次)
    preflight = (stage == 'submit' and delivery.get('method') == 'platform_profile'
                 and not ps.profile_attachments_fresh(jobs[url], fb, url))
    prepared = None
    if not dry:
        import agent_chrome                    # agent 只在它專用的 Chrome 動:先確認連上、藏在螢幕外
        # 各家用各家的門路確認連上:Codex 看外掛,Claude 看 Claude in Chrome;只裝其中一家也能用
        runtime = ar.browser_runtime(pinned_agent_id)
        if runtime == 'codex':
            up, msg = agent_chrome.ensure(board)
            if not up:
                agent_report.report('代投', msg, need='按「🔌 連接 Codex」', job=url, live=board)
                return False, msg
        if runtime == 'claude-code':             # Chrome 剛開起來,Claude 擴充功能要一陣子才連得上:等到它看得到再派工
            up, msg = agent_chrome.wait_claude()
            if not up:
                agent_report.report('代投', msg, need='按「🔌 連接 Claude」', job=url, live=board)
                return False, msg
        if stage == 'fill' and runtime == 'codex':   # 程式先開好申請頁、讀好欄位,agent 接手就好
            # Claude 只看得到自己分頁群組裡的分頁,接不了程式開的,讓它自己開
            prepared = agent_chrome.open_for_agent(url, old_tab=apply_of(fb, url).get('tab_id'))
    if preflight:
        p, out = pre_submit_prompt(url, jobs[url], fb, board, attachment_download_dir)
    else:
        p, out = prompt_for(stage, url, jobs[url], fb, board, note, profile,
                            attachment_download_dir, prepared=prepared)
        if stage in ('fill', 'fix') and ar.browser_runtime(pinned_agent_id) == 'claude-code':
            # 規矩開頭講過,但接回的修改那一輪 Claude 會漏掉(實測);最後再講一次,程式才讀得到那一頁
            p += ('\n\n最後,寫 fill.json 之前,照最前面【填完、改完的最後一步】在那一頁跑那支唯讀函式'
                  '(先長度、再分段,程式碼照抄不要改)。這一步沒做,程式讀不到那一頁,這一輪就算沒完成。')
    if dry:
        print(p)
        return True, 'dry'
    os.makedirs(out, exist_ok=True)
    if preflight:
        check_log = os.path.join(out, 'submit-check.log')
        check_outcome = _run_agent(
            p, check_log, cf.HOME, board, timeout=TIMEOUT, browser_required=True,
            browser=ar.apply_overrides(), resume=sid, agent_id=pinned_agent_id,
            on_start=(lambda proc: status(proc.pid)) if status else None,
        )
        if not check_outcome.ok:
            return _block_profile_submit(
                url, board, fb, ['送出前的平台履歷附件檢查沒完成:' + check_outcome.message()])
        if ar.session_id(check_log) != sid:
            return _no_session(url, board, stage)
        try:
            with open(os.path.join(out, 'pre-submit.json'), encoding='utf-8') as fh:
                report = json.load(fh)
        except Exception:
            return _block_profile_submit(url, board, fb, ['送出前沒有寫出 pre-submit.json'])
        bad = _check_delivery_attachments(
            fb, url, jobs[url], report, attachment_download_dir,
            force=True, expected_delivery=delivery,
        )
        if bad:
            return _block_profile_submit(url, board, fb, bad)
        jobs, fb = load(board)
        problem = fr.approval_problem(fb, url)
        if problem:
            return False, problem
        p, out = prompt_for(stage, url, jobs[url], fb, board, note, profile)
    t0 = time.time()
    log = os.path.join(out, stage + '.log')
    outcome = _run_agent(
        p, log, cf.HOME, board, timeout=TIMEOUT, browser_required=True,
        browser=ar.apply_overrides(), resume=sid, agent_id=pinned_agent_id,
        on_start=(lambda proc: status(proc.pid)) if status else None,
    )
    if getattr(outcome, 'status', '') == 'timeout' and stage in ('fill', 'fix') and ar.session_id(log):
        # 填完、交接了分頁,卻在寫交件檔前被砍掉,整輪就白做了(2026-09-26 一張 104 就是這樣)。
        # 叫回同一段對話,只把目前結果寫下來;程式照常驗收,沒做完的會列在卡上。
        wrap_sid = ar.session_id(log)
        log = os.path.join(out, stage + '-wrapup.log')
        outcome = _run_agent(
            WRAPUP.format(out=out), log, cf.HOME, board,
            timeout=WRAPUP_TIMEOUT, browser_required=True, browser=ar.apply_overrides(),
            resume=wrap_sid, agent_id=getattr(outcome, 'agent_id', None) or pinned_agent_id,
            on_start=(lambda proc: status(proc.pid)) if status else None,
        )
    if not outcome.ok:
        msg = outcome.message()
        if stage == 'submit':
            failure = {'at': now(), 'problems': [msg], 'clicked': None,
                       'runner_outcome': outcome.status}
            bd.set_fb(lambda d: d.setdefault(url, {}).setdefault('apply', {}).__setitem__('submit_fail', failure),
                      live=board, by='apply_run')
            agent_report.report('代投', '送出沒確認成功:' + msg,
                                need='先去信箱或平台的應徵紀錄確認到底送出沒有,再決定要不要重送',
                                job=url, live=board)
            return False, '送出結果不明:' + msg
        agent_report.report('代投', ('填表' if stage == 'fill' else '修改') + '沒完成:' + msg,
                            need='看「看紀錄」的內容;確認後再重跑', job=url, live=board)
        return False, msg
    if stage == 'fill':
        sid = ar.session_id(log)
    elif ar.session_id(log) != sid:
        # resume 沒接上同一段對話(codex 找不到那段、或開了新的):那一頁不是它開的,不能算數
        return _no_session(url, board, stage)
    runtime = ar.browser_runtime(getattr(outcome, 'agent_id', None) or pinned_agent_id) or 'codex'
    shoot(sid, out, stage, apply_of(fb, url).get('tab_id'), runtime)
    jobs, fb = load(board)
    rel = os.path.relpath(out, cf.HOME) if out.startswith(cf.HOME) else out
    if stage in ('fill', 'fix'):
        bad, res = check_fill(
            fb, url, out, t0, sid, job=jobs[url],
            attachment_download_dir=attachment_download_dir, runtime=runtime, log=log,
        )
        remember_notes(url, res.get('platform_notes'), res.get('platform_notes_remove'))
        if res.get('tab_id'):
            # 先把這一輪填好的分頁記到看板上,再做後面的核對:收尾的 agent_chrome.close_if_idle() 只留看板上記著的分頁,
            # 沒記著的話,填好的那一頁會跟著整個 agent Chrome 一起被關掉
            tab = str(res['tab_id'])
            bd.set_fb(lambda d: d.setdefault(url, {}).setdefault('apply', {}).update(tab_id=tab),
                      live=board, by='apply_run')
        prev = apply_of(fb, url)
        delivery = dict(prev.get('delivery') or {}) if stage == 'fix' else {}
        delivery.update(res.get('delivery') or {})
        checked_result = dict(res, delivery=delivery) if delivery else res
        if (stage in ('fill', 'fix') and delivery.get('method') == 'platform_profile'
                and delivery.get('profile_kind') in ('fixed', 'custom')):
            bad += profile_after(url, checked_result, board)
        if not sid:
            bad.insert(0, '沒拿到 agent 那段對話的 id,之後叫不回同一隻 agent')
        rec = fill_record(stage, prev, checked_result, bad, sid, os.path.join(rel, 'fill.png'),
                          outcome.agent_id, note)
        rec['runtime'] = runtime             # 👀、讀頁、收分頁都要用同一家的門路接回那一頁

        def mut(d):
            d.setdefault(url, {})['apply'] = rec
            if not bad:                      # 網頁已經照答案庫重打了
                fr.apply_clear_refill(d, url)
        bd.set_fb(mut, live=board, by='apply_run')
        pt = res.get('posting') or {}
        if stage == 'fill' and pt.get('same_job') is True and pt.get('title'):
            rename(url, pt['title'], pt.get('company') or '', board)
        if not bad and stage == 'fill':
            # 附件更新後第一次用這份平台履歷:把平台上的附件取回來比一次(不在填表的 40 分鐘裡);沒更新就直接跳過
            later = _profile_check_after_fill(url, board, sid, outcome.agent_id or pinned_agent_id, status)
            if later:
                bad = later
                bd.set_fb(lambda d: d.setdefault(url, {}).setdefault('apply', {}).update(ok=False, issues=later[:10]),
                          live=board, by='apply_run')
        if not bad:   # 這一輪做成了:這張之前的回報(上一輪的問題)都過時了,收進已處理
            agent_report.resolve(url, ('填好了' if stage == 'fill' else '改好了') + ',之前的問題已經解決', live=board)
        if bad:   # 程式驗出來的問題自己回報,不靠 agent 記得
            agent_report.report('代投', ('填表' if stage == 'fill' else '修改') + '沒完成:' + bad[0],
                                need='看卡上的原因;需要你處理的照回報做', job=url, live=board)
        return not bad, '; '.join(bad) or ('填好了' if stage == 'fill' else '改好了') + ',等你確認送出'
    ok, res = check_submit(out, t0)
    ev = submit_evidence(res, os.path.join(rel, 'submit.png'))
    d = ship.folder(url, root=SHIP_ROOT)
    record = ship.read_info(d)
    lang, var = record.get('lang'), record.get('variant')
    changed_questions = []

    def mut(fbx):
        if ok:
            current_answers = fr.snapshot(fbx, url)
            changed_questions.extend(
                q for q in sorted(set(approved_answers) | set(current_answers))
                if approved_answers.get(q) != current_answers.get(q)
            )
            if changed_questions:
                ev['not_sent_questions'] = changed_questions[:]
            fr.apply_mark_sent(fbx, url, ev, today(), f'{lang}-{var}' if lang and var else None)
            fbx[url]['ev'] = '送出頁是目前唯一證據;待查信箱與平台應徵紀錄'
        else:
            fbx.setdefault(url, {}).setdefault('apply', {})['submit_fail'] = dict(
                ev, problems=res.get('problems') or [], clicked=bool(res.get('clicked') or res.get('submitted')))
    bd.set_fb(mut, live=board, by='apply_run')
    if ok:
        agent_report.resolve(url, '送出成功,這張結束了', live=board)
        if changed_questions:
            msg = '這幾題改的內容沒有送出去:' + '、'.join(changed_questions)
            agent_report.report('代投', msg, need='這張已照確認頁送出;新答案不在這次送出的內容裡',
                                job=url, live=board)
        import apply_tab                     # 證據存好了,這張結束:那一頁關掉(這一輪收尾不再標它,外掛會收掉)
        try:
            apply_tab.release(sid, apply_of(fb, url).get('tab_id'), runtime)
        except Exception:  # noqa: S110
            pass
    if not ok:
        agent_report.report('代投', '送出沒確認成功:' + ('; '.join(res.get('problems') or []) or '沒看到成功頁面'),
                            need='先去信箱或平台的應徵紀錄確認到底送出沒有,再決定要不要重送', job=url, live=board)
    if ok:
        msg = '送出了:' + str(res.get('confirm_text') or res.get('confirm_url'))
        if changed_questions:
            msg += ';這幾題改的內容沒有送出去:' + '、'.join(changed_questions)
        return True, msg
    return False, '沒送出:' + '; '.join(res.get('problems') or ['沒看到成功頁面'])


def run_one(stage, url, board, dry=False, status=None, note=''):
    jobs, fb = load(board)
    delivery = apply_of(fb, url).get('delivery') or {}
    needs_download_dir = not dry and (
        stage in ('fill', 'fix')
        or (stage == 'submit' and delivery.get('method') == 'platform_profile')
    )
    if not needs_download_dir:
        return _run_one(stage, url, board, dry, status, note, None)

    temporary = tempfile.TemporaryDirectory(prefix='jobsalvo-profile-attachments-')
    try:
        result = _run_one(stage, url, board, dry, status, note, temporary.name)
    finally:
        cleanup_problem = _clean_attachment_downloads(temporary.name)
        try:
            temporary.cleanup()
        except OSError as e:
            cleanup_problem = cleanup_problem or f'平台附件比對暫存檔清理失敗({str(e)[:80]})'
    if cleanup_problem:
        ok, message = result
        return False, '; '.join(x for x in (message, cleanup_problem) if x)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stage', choices=STAGES, required=True)
    ap.add_argument('--url')
    ap.add_argument('--note', default='', help='fix:他寫給 agent 的話(要改什麼)')
    ap.add_argument('--board', default=bd.LIVE)
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--limit', type=int, default=0, help='這一輪最多跑幾張(看板上的「跑幾張」)')
    a = ap.parse_args()
    board = os.path.abspath(a.board)
    jobs, fb = load(board)
    todo = eligible(jobs, fb, a.stage, a.url)
    if a.limit:
        todo = todo[:a.limit]
    st = os.path.join(SP, STATUS)
    base = {'stage': a.stage, 'n': len(todo), 'done': 0, 't0': time.time(), 'pid': os.getpid()}
    if not todo:
        jobrun.write(st, dict(base, phase='nothing', msg='沒有要跑的卡'))
        print('沒有要跑的卡')
        return
    results = []
    for i, u in enumerate(todo):
        jobrun.write(st, dict(base, phase='run', done=i, which=card.name(jobs[u]), url=u))
        ok, msg = run_one(a.stage, u, board, a.dry, note=a.note)
        results.append({'url': u, 'ok': ok, 'msg': msg})
        print(('✅ ' if ok else '❌ ') + card.name(jobs[u]) + ' — ' + msg)
    if not a.dry:
        import agent_chrome                   # 這一批做完:沒有頁面在等他,就把 agent 的 Chrome 整個關掉
        try:
            print(agent_chrome.close_if_idle(board))
        except Exception as e:
            print('關 agent 的 Chrome 時出錯:', e)
    failed = any(not item['ok'] for item in results)
    jobrun.write(st, dict(base, phase='failed' if failed else 'done', done=len(todo),
                          results=results, finished_at=time.time()))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main() or 0)
