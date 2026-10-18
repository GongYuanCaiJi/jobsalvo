#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_run —— 「可投遞」這一段:agent 在它的瀏覽器裡填表單,使用者看過真的頁面、在看板上核准,
再由同一段對話在同一頁送出。看板上的「▶ 填表單」「✏️ 要 agent 改」「✅ 核准送出」各跑這支的一個階段。

最後一關絕對不自動投遞,一定要有人類把關:agent 負責把所有資料填好,使用者做最後確認,按了核准 agent 才送出。
prompt 只講目標、唯一真相和規矩,平台上怎麼點由 agent 自己判斷(求職平台五花八門,寫死步驟反而壞事);程式守的是閘門。

瀏覽器:agent 的瀏覽器(ego lite,docs/adr/0006),一張卡一個工作區;程式先開好申請頁、agent 用 ego-browser 指令填。
不動使用者的滑鼠、不把瀏覽器叫到前面。填好的頁留在工作區,看板上有填好當下的截圖,
手機也看得到;要看現場就按「👀 看現在的頁面」。

一張職缺一段對話,從填到送出都是它(它記得前面做過什麼、開的是哪個分頁,不用換一隻從頭來):
  fill    開一段新對話。指定的平台履歷落後時,先確認申請頁實際用平台履歷還是直接上傳;用平台履歷才經過 sync_profiles.sync_slot;通過後照答案庫填、
          上傳這張的檔,停在送出前;寫 fill.json,用 form_record --from-fill 把欄位記進看板。
          對話的 id 記在看板(apply.session)。
  fix     codex exec resume 叫回那一段對話,在原本那一頁上改:使用者在答案庫改過的答案(欄位標 refill),
          或他寫給 agent 的話。改完重記表單、重截圖,又回到等他核准。
  submit  只對核准過、核准之後答案沒再變的卡。程式先自己讀那一頁跟驗收時核對過的樣子比(變了就不送),
          再叫回同一段對話在同一頁送出;按完送出程式自己讀那一頁判斷送出沒有、截確認頁。
          送成功了這張就結束,那段對話不會再被叫回來。
對話不見了(沒記到 id、resume 失敗)就不改也不送:使用者核准的是那一頁,換一段新的對話找不回來,要重新填一次給他看。

程式守的閘門(不靠 agent 自律):
  · agent 交回來的交件單(fill.json、submit.json)只經安檢門(gate.py)進來:每一格跟程式自己讀的頁面、
    紀錄、檔案比,對不上就停並寫出哪一格、agent 說什麼、實際是什麼;沒登記核對方式的格子程式不用。
  · 他按確認送出前、程式送出前,程式自己讀那一頁,跟驗收時核對過的樣子比;變了就停,寫出哪一格從什麼變成什麼和下一步。
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
import os, sys, json, time, argparse, datetime, tempfile, shutil, shlex, contextlib, copy, uuid

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
import delivery_state as ds   # noqa: E402
import evidence               # noqa: E402
import gate_cells             # noqa: E402

# 可投遞夾。驗收(apply_accept)用環境變數換成暫存夾,放假職缺的檔,不碰真的可投遞夾。
SP = os.environ.get('APPLY_TMP', cf.TMP)             # 進度檔放哪(board_server 會給;跟跑準備區同一套)
STATUS = 'apply_status.json'
TIMEOUT = 40 * 60                                     # 一張最多等 40 分鐘
WRAPUP_TIMEOUT = 8 * 60                               # 時間到之後,叫回同一段對話收尾最多再等這麼久
WRAPUP = ('時間到了。不要再開新分頁、不要再做別的步驟,也絕對不要送出。'
          '表單已經填到哪裡就停在哪裡,保留工作區與頁面,照上一輪的交件方式與格式交回目前結果'
          '(交件單 {out}/fill.json),沒做完、沒核對到的寫進 problems。')
STAGES = ('fill', 'fix', 'submit')
# 幫你填表這一段的回報來源:程式自己回報、agent 回報都記這個(agent_run.run 的 report_from),重填成功時照它收
REPORT_FROM = agent_report.FROM_APPLY   # 這個流程的回報來源(存在資料裡的代號)
UNFINISHED = {'fill': '這一輪填表沒跑完(被停止或中途停掉),頁面可能只填到一半,要重填',
              'fix': '這一輪修改沒跑完(被停止或中途停掉),頁面可能只改到一半,再叫它改一次或重填'}


def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def load(board):
    p = bd.load(board)      # 看板檔被繞過狀態表改過就停(bd.Tampered),不照改過的內容派 agent
    return {j['id']: j for j in p['data']['jobs']}, json.loads(p['fb'])


def out_dir(url, board, sp=None):
    """agent 這一輪寫交件單、程式截圖的工作資料夾(進度目錄底下,每張卡一個)。要留下來的(指示、動作紀錄、
    交件單、截圖)記進那張卡的證據夾,看板從證據夾開;這裡下一輪會蓋掉。"""
    return os.path.join(sp or SP, 'apply-out', card.card_id_from_url(url))


def early_proof(url):
    """form_plan 在選檔那一步截的「公司申請頁上的履歷」(多步驟表單最後一頁看不到時用):程式自己的位置,不在 agent 交件的工作資料夾。"""
    return os.path.join(SP, 'apply-proof', card.card_id_from_url(url), 'proof.png')


def apply_of(fb, url):
    return (fb.get(url) or {}).get('apply') or {}


def eligible(jobs, fb, stage, url=None, status=None):
    """這一輪要跑哪幾張。fill:可投遞、還沒送出、核准還沒生效的;fix:填好了、有欄位等著照新答案重打的;
    submit:核准有效的(status:投遞前驗收的結果)。fix 指定 --url 就一定跑(他寫了話要 agent 改)。"""
    out = []
    for u, m in fb.items():
        if ds.app_of(m) != 'ship' or (url and u != url) or u not in jobs:
            continue                  # 移到「🗑 已移除」的不填也不送(前端的張數本來就不算它)
        a = m.get('apply') or {}
        if stage == 'submit' and fr.approval_problem(fb, u, status) is None:
            out.append(u)
        elif stage == 'fix' and ds.allowed(m, 'fix_start') and a.get('session') and (
                url or to_translate(fb, u) or any(x.get('refill') for x in (m.get('form') or {}).get('f', []))):
            out.append(u)
        elif stage == 'fill' and ds.allowed(m, 'fill_start'):
            out.append(u)             # 停著等你、你已確認的不重填(狀態表不准);卡住的要他按才重填(指定 --url)
    return out


RULES = """規矩:
- 答案只有一個真相:表單答案庫。使用者確認過的共用答案程式已經抄在下面(【共用答案】),這張以前記過的欄位也在下面。
  答案庫有的直接用(英文表單填英文 v,中文表單填中文),不要自己改寫。
- 使用者本人的事實(姓名、Email、電話、居住地、學歷、個人連結)照這張要用的履歷母稿:{master};母稿沒寫的個人連結用程式登記的他自己的平台個人頁(現行的;對外連結只用不用登入就看得到的),兩邊都沒有才列為待確認:
{profile_pages}
- 答案庫沒有、履歷也對不上的題目:個人事實依下列來源規則處理。
  意見、承諾、時間安排、薪資等可推論的題目:照他的履歷、這個 JD 和他的求職條件,選對他最有利的答案。這些都算你推論的:
  {other}答案一定附{read}翻譯(zh),在 why 寫一句你為什麼這樣填,並判斷這題是「共用」(關於他本人)還是「這缺專用」(針對這個 JD)。
  他在看板上確認之前,核准送出按不下去。
- 共用答案跟這家職缺或申請頁寫的要求衝突時(例如提到這張不寄的附件),那一欄改填這缺專用的版本:只改衝突的那幾個字,
  pj 給 1、why 寫是哪一句要求;他確認之前核准送出按不下去。
- 推論的答案只准用母稿和答案庫寫到的事實:不補他沒做過的經驗、不加頭銜、不誇大。
  個人事實須符合題目要求的時間與身分關係;未記載現況的經歷不能當成現況。
  使用者明確要求保留的帶入值依其規則保留;其他網頁自動帶入值不算來源證據。
  資料未提供的個人事實照實回報不明,並遵照使用者的填表做法處理;不要以推論補成事實。
- 使用者自己交代的填表做法(照做):
{apply_rules}
- 申請頁沒有要填的欄位、只剩最後的送出確認(一鍵應徵)時,停在那個確認畫面就是填好了:不按送出,也不要寫成卡住;
  tab_id 寫那個確認畫面所在的頁。
- 平台從帳號帶入、頁面上只顯示成文字不能編輯的資料(姓名、Email、電話等)不是申請表的格子:不要列進 fields。
- 指定的平台履歷在申請表上選不到、但申請表能直接上傳檔時,照上傳規則直接上傳、delivery 寫 direct_upload,不算卡住;
  這時不用再碰平台履歷和它的附件。
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
要填進去的資料:他的姓名、Email、電話、居住地、個人連結(照母稿 {master},母稿沒有的照規矩裡登記的平台個人頁)、答案庫裡的答案、{ship} 裡要上傳的檔。
目的地:{url} 這一個申請表。只填這些、只填進這一個申請表;送出不在這一輪的授權裡。"""

BATCH_FILL = """先完成使用者要求的資料帶入,照他的填表做法處理授權頁與帶入值;要上傳履歷的先上傳、等網站顯示上傳或解析完成
   (很多平台解析履歷會覆寫已填的欄位,先填後傳要再改一輪)。
   接著跑 `{plan} --tab <申請表所在的 工作區編號:Page> --fill`:程式讀這一頁,照答案庫和以前填過的表單配好每一格、自己填好,
   印出還沒對上的格子和「你判斷」的題目。
   「你判斷」的照上面的規矩決定值,一次交給程式填:`{plan} --tab <同一個> --set '[{{"q":"題目","value":"值"}}]'`
   (選項類的 value 寫選項上的字)。選填欄、又沒有能用的答案(例如沒有內部推薦人)就不填,交件單寫 "src": "skip" 和為什麼,
   不算卡住、不寫進 problems。程式印「填不上」的那幾格才自己用 ego 填,選檔照下方規則。
   跑 `{plan} --tab <同一個> --check` 印出 0 個還沒對上的格子,就是這一頁填完了;程式最後會自己讀頁與截圖驗收。
   交件單 fields 直接用清單最後一行程式配好的寫法,再補「你判斷」的那幾格。多步驟表單換到下一頁就在新的一頁從 --fill 再跑一次。
   指令跑不起來(印出錯誤)就照上面的規矩自己填,不用回報。"""

CHOICE_RULE = """答案庫現成答案必須保留該答案的 k。value 與「送出」原文相同時,只寫 k,不加 choice。
choice 只用在原生選項(下拉、radio、checkbox)。文字欄直接填答案原文。
答案庫現成選項(有 k):選的就是「送出」原文,或是原文裡完整的一段(雙語答案選了其中一種),不用加 choice,程式自己比、自己對應。
選的跟原文不是同一句(意思相同的另一種說法)才加 choice;由你判斷是否同義,不同義就如實報問題:
{"source_hash": "上面這條答案的 source_hash", "why": "原文與所選項目同義的理由"}。
q 照抄本輪完整題目,value 照抄真正選中的文字;程式核對來源版本、同一題及實際選取。不能補新事實或修改銀行答案來遷就選項。"""

FILL = """你是代投 agent。這一輪只做「填好、不送出」,送出要等使用者在看板上按核准。

職缺:{title}
職缺頁:{url}
這張的可投遞夾:{ship}(ship.json 的 files 是個別檔, merged 是程式產的合併版)
這張以前用過的常用答案(值是常用答案現在的值;k 指向常用答案那一條):{prior}

{shared}

【這張使用的履歷母稿原文】來源:{master};有原文就直接用,不用再為同一份母稿另跑讀檔:
{master_text}

{auth}

{rules}
{notes}
步驟:
0. 這張的投遞方式已先看申請頁確認；要用平台履歷時，程式已先處理文字同步。這一輪填申請表，附件照下方指示處理。{profile_choice}
{open_step}
   打開後先判斷:這一頁是不是上面「職缺」那個缺(看板上的名字可能過時或寫法不同,以頁面本身為準)。
   是同一個缺就照做,把頁面上的職稱寫進輸出的 posting;是別的職缺、或職缺已經關了,就不要填,
   posting 的 same_job 寫 false、problems 寫你看到什麼,停下。
2. 每一欄照上面的規矩填好,依最後面的「上傳規則」選一套檔案。
   {batch_fill}
   用 ego 的原生選檔,照上傳規則讀回檔案欄或非同步上傳完成狀態;選檔或上傳失敗就回報問題,不可拿本機檔冒充頁面結果。
   答案庫的答案如果平台上已經存好一份(例如推薦信下拉裡存好的那封),直接選那一份,不要重打、不要自己另寫,也不要用平台自帶的預設句。
3. 絕對不要按送出、Submit、確認應徵。若意外可能送出，照實回報 clicked、submitted(true/false/null)、reason、confirm_url、confirm_text，不要重試。
   填完就停,保留本輪工作區與頁面,不 finish、不 handOff;
   他核准之後,你(同一段對話)會被叫回來在這一頁送出;他要改東西時,你也會被叫回來在這一頁改。
4. {record_step}
   截圖不用你存,程式會自己從那一頁截。格式照下面寫就好,不用去讀 jobsalvo 的程式原始碼。
   fields 每一欄都有 "q"(表單上的題目)和 "value"(頁面上現在的值,照抄全文,長答案也整段照抄,不要寫成說明或字數),再照來源加;
   題目不是{read}的,每一欄再加 "qz":題目的{read}翻譯(他在確認畫面對照著看);
   答案庫現成的(有 k)照原文填的那幾欄不用寫 value,程式自己讀頁面核對:
     履歷直接對上的  "src": "rz"
     答案庫現成的    "src": "bank", "k": 那條的 k(上面「以前用過的常用答案」有 k 和原文)
     {choice_rule}
     刻意不填的      "src": "skip", "why": 為什麼
     新答案          "src": "bank", "zh": 中文(英文答案必附), "why": 依據,
                     "kind": "txt"(短文)/"op"(意見)/"pick"(選項)/"val"(數字日期)/"ck"(勾選), "pj": 1(這缺專用) 或 0, "pjw": 理由,
                     "bank_q": 這題的通用問法(選填;表單問法很特別時給)
    {{"url": ..., "platform": ..., "tab_id": "申請表最後所在那一頁的 工作區編號:Page 標籤(申請表開在同一工作區的新分頁就寫那一頁)", "tab_url": "那個分頁現在的網址", "handoff": true,
    "posting": {{"title": "頁面上的職稱", "company": "頁面上的公司", "same_job": true/false}},
    "profile": {{"application_history_url": "可讀的應徵紀錄頁(有就填)"}},
    "uploaded": ["申請表檔案欄已選妥或非同步上傳完成的檔名;平台履歷上的附件不用寫"], "uploaded_files": [{{"name": "申請表上傳檔名", "path": "下載檔完整路徑"}}], "upload_readback": "downloaded 或 unavailable(平台讀不回上傳檔時)", "unmet": [{{"quote": "職缺或申請頁寫的要求原句", "file": "ship.json 的 files 裡哪一份(不寫 merged)", "act": "drop 或 adapt", "why": "哪裡不符"}}](都符合給 []), "uploaded_from": [{{"name": "申請表上顯示的檔名", "path": "交給 setFiles 的完整路徑(讀不回時才填)"}}], "fields": [{{"q": "表單上的題目", "qz": "題目的翻譯(題目不是他讀的語言時)", "value": "頁面上現在的值", "src": "rz/bank/skip", "k": "答案庫的 k(有才給)"}}],
    "blank_for_him": [], "problems": ["沒填完、卡住的地方(沒有就空陣列)"],
    "notes": ["其他觀察,不影響填表"],
    "platform_notes": ["這一輪在這個平台試出來、下次直接照做會省事的做法,一句一條(例如某個編輯器要怎麼輸入才會存);沒有新發現就空陣列"],
    "platform_notes_remove": [上面【這個平台以前學到的】裡這次發現不對的那幾條的編號(整數;沒有就 [])], "submitted": false}}"""

# 平台筆記:agent 在某個平台試出來的做法(例如 104 的自訂經歷編輯器要怎麼輸入才會存),照平台存起來,
# 下一輪同平台直接附在指示裡。以前每一輪都從頭試:改一句話試了 22 次。
# 只當提示;平台改版做法失效時,agent 在 platform_notes_remove 寫那幾條的編號,拿掉那幾條。
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
    """收 agent 這一輪寫的平台筆記:去掉重複、它說不對的那幾條,每個平台留最新的 NOTES_MAX 條。
    remove:安檢門收下的編號(numbered.Cited),拿掉那幾段程式印給它的原字,完全相等才拿。"""
    key = note_key(url)
    add = [str(x).strip()[:200] for x in (add if isinstance(add, list) else []) if str(x).strip()]
    remove = {p.text for p in getattr(remove, 'parts', ())}
    if not key or not (add or remove):
        return
    try:
        with open(_notes_path(), encoding='utf-8') as f:
            store = json.load(f)
        if not isinstance(store, dict):
            store = {}
    except (OSError, ValueError):
        store = {}
    kept = [x for x in store.get(key) or [] if str(x) not in remove]
    for x in add:
        if ' '.join(x.split()) not in {' '.join(y.split()) for y in kept}:
            kept.append(x)
    store[key] = kept[-NOTES_MAX:]
    tmp = _notes_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _notes_path())


def profile_pages(url=''):
    """平台履歷登記裡他自己的頁面(看全文那一頁):母稿沒寫的個人連結程式其實知道。
    投遞的這個平台本身不列:那裡要選哪一份由程式決定(第 0 步),列出別份只會讓 agent 選錯。"""
    import profile_sync as ps
    here = ps.profile_key(url) if url else None
    rows = [f'- {platform} {lang}/{variant}:{entry["read"]}' for platform, lang, variant, entry in ps.slots()
            if platform != here and not ps.is_file_url(entry['read'])]
    return '\n'.join(rows) or '(沒有)'


OPEN_STEP = """1. 程式沒有提供這張卡的工作區與頁面,停止並回報,不要自行另建工作區或猜頁面。"""


def prepared_step(prepared):
    """程式已經開好申請頁、讀好欄位:agent 接手就好,不用自己開分頁、找舊的、從頭讀頁面(以前每輪五到十步)。"""
    page = prepared.get('page') or {}
    fields = []
    for f in (page.get('fields') or [])[:60]:
        value = f.get('shown') or f.get('value')
        value = ', '.join(value) if isinstance(value, list) else str(value or '')
        fields.append(f"   - {str(f.get('label') or f.get('name') or '(沒有標籤)')[:80]} [{f.get('type')}]"
                      + (f" = {value[:80]}" if value else ''))
    lines = [str(x)[:120] for x in (page.get('lines') or [])[:60]]
    workspace = prepared.get('workspace')
    if not workspace:
        return OPEN_STEP
    return (
        '1. ' + workspace_step(workspace) + f"交件 tab_id 照抄 {prepared['tab_id']}。其他卡與使用者的分頁不要動。\n"
        "   不要另開分頁重做申請表,不要重新載入、goto 同一頁或 reload。這一頁不是申請表就在此頁接著做。\n"
        "   同一工作區內可另開必要分頁完成這張卡的履歷同步、附件管理與登入授權;申請表仍用上面指定的 Page。\n"
        f"   程式剛讀到這一頁(不用再整頁重讀,要確認哪一格再讀那一格):標題 {str(page.get('title') or '')[:120]};網址 {page.get('url') or ''}\n"
        + ('   欄位:\n' + '\n'.join(fields) + '\n' if fields else '   (這一頁還沒有表單欄位)\n')
        + ('   頁面文字(前 60 行):' + ' / '.join(lines) if lines else '')
    )


def workspace_step(workspace, subject='這張卡'):
    if not workspace:
        return '沒有程式記錄的工作區,停止並回報。'
    return (f'在 ego 用 `const task = await taskSpace({workspace["id"]}); '
            f'const page = task.page({json.dumps(workspace["page"])});` 接回{subject}的頁面。'
            '不另建工作區,不接手其他工作區。')


FIX = """接著你上一輪填的那張:{title}({url})。使用者看過之後要你改。

他寫給你的話:{note}
答案庫裡改過、網頁上還是舊的題目(照這裡的值改;英文表單填英文 v。答案庫的答案如果平台上已經存好一份,直接選那一份,不要重打、不要自己另寫,也不要用平台自帶的預設句):
{changed}
{shared}
{choice_rule}

{auth}
{notes}
{rules}
步驟:
1. {workspace_step}回到上一輪留著的頁面(交件 tab_id {tab_id},網址 {tab_url})。不要開新分頁、不要重新載入、
   不要 goto:使用者要你在原本那一頁上改。分頁找不到了就不要自己重開,寫進 problems 停下。
2. 只改上面要改的地方,其他欄位不要動。不要送出。改的是答案的話,答案以答案庫為準(不要自己改寫)。
   {batch_fill}
3. 保留工作區與頁面,不 finish、不 handOff。{record_step}
   格式沿用最初填表輪的交件單,fields 照改完的頁面寫,再加 "fixed": ["改了哪幾格"]。"""

SUBMIT = """使用者在 {approve_at} 在看板上按了「✅ 核准送出」:他看過你留著的那一頁,核准把那一頁送出到 {url}。
這就是他對這次送出的確認:送出的資料就是下面的核准快照,目的地就是這一個申請表。核准時每一題的答案:
{snap}

職缺:{title}

步驟:
1. {workspace_step}回到留著的頁面(交件 tab_id {tab_id},網址 {tab_url})。不要重填、不要開新分頁、不要重新載入、不要改任何一格:
   他核准的是他親眼看過的那一頁。分頁找不到了:不要送,寫進 problems 停下。
2. 程式剛自己讀過這一頁、跟他確認時的樣子逐格比過了,一樣才叫你來送;你不用再逐題比對。
3. 按送出，等待本次操作結果，理解這張申請是否已收到。表單還在、網址改了、一般感謝字樣都不單獨代表結果；必要資訊不清楚就回報 unknown，不重試。
4. 寫 {out}/submit.json:
   {{"submitted": true/false/null, "clicked": 有沒有按下送出, "reason": "已收到／明確未送出／無法確認的理由", "confirm_url": "本次結果頁網址", "confirm_text": "本次結果頁逐字引用", "problems": [...]}}
5. 保留工作區與頁面,程式會自己讀頁、截圖存證;確認送成功後由程式收尾,結果不明就留著。
只有清楚知道尚未送出才寫 false；沒法確認就寫 null。跳出驗證碼、真人驗證就停下，照實寫進 reason，不要自己解。
程式會讀本次結果頁，核對你引用的來源與新截圖，再依你的結果判讀寫狀態。最後一行印 @@DONE@@。"""


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
   fr.translate('<k>', en='<你翻的英文>')
   PY
"""


def translate_block(fb, url):
    tr = to_translate(fb, url)
    if not tr:
        return ''
    read, other = fr.lang_words()
    return TRANSLATE.format(items=json.dumps(tr, ensure_ascii=False, indent=1), tools=HERE,
                            read=read, other=other) + '\n'


# 答案作廢、改由 agent 重新代填的題目(form_record.redo:他在看板上清掉,或它過時了)
REDO = '之前的答案作廢了:照規矩重新代填,fields 這一題當新答案寫(不要給 k)'


def changed_fields(fb, url):
    """答案改過、網頁上還是舊字的欄位(refill):題目 → 答案庫現在的值;答案作廢的 → 要它重新代填。"""
    bank = {e.get('k'): e for e in fb.get('__ans__', [])}
    out = {}
    for x in ((fb.get(url) or {}).get('form') or {}).get('f', []):
        if x.get('refill'):
            e = bank.get(x.get('k')) or {}
            out[x.get('q') or ''] = ({'redo': REDO} if e.get('redo') else
                                    {'v': e.get('v') or '', 'zh': e.get('zh') or '', 'k': x.get('k'),
                                     'source_hash': fr.answer_fingerprint(e)})
    return out


def _pages(path):
    """PDF 幾頁(給 agent 對照職缺的頁數上限);讀不出來就不標。"""
    try:
        import pypdf
        return f'({len(pypdf.PdfReader(path, strict=False).pages)} 頁)'
    except Exception:  # noqa: BLE001 — 不是 PDF 或讀不出來:只是少一個參考數字
        return ''


def _upload_rule(directory, record):
    files = [n for n in record.get('files', []) if isinstance(n, str) and n]
    merged = record.get('merged')
    separate = '\n'.join('  ' + os.path.join(directory, n) + _pages(os.path.join(directory, n))
                          for n in files) or '  (沒有個別檔)'
    merged_path = os.path.join(directory, merged) if isinstance(merged, str) and merged else None
    if not merged_path or not os.path.isfile(merged_path):
        return '可投遞夾沒有合併版；不要自行合併或上傳個別檔，停止並回報。'
    return f"""申請頁核准的上傳檔(依共用附件指示與當頁容量選檔;第一個個別檔是履歷本身):
個別檔案:
{separate}
合併版:{merged_path}{_pages(merged_path)}"""


def prompt_for(stage, url, j, fb, board, note='', attachment_download_dir=None, prepared=None, *, door,
               delivery_method=None):
    """door:這一輪 agent 的瀏覽器是哪一家(chrome_door);取檔、選檔的做法照它給。
    回 (指示, 交件夾, 這一份指示印的編號材料);編號材料交給 check_fill 核對交件單。"""
    from numbered import Numbered
    numbered = Numbered()
    d = ship.folder(url)
    record = ship.read_info(d)
    lang, var = record.get('lang'), record.get('variant')
    master = cf.master(var, lang) or '(找不到這張要用的母稿,照可投遞夾裡的履歷檔)'
    out = out_dir(url, board)
    a = apply_of(fb, url)
    kw = dict(title=card.name(j), url=url, ship=d or '(這張還沒有可投遞夾)', out=out, master=master,
              tools=HERE, apply_rules=apply_rules(), read=fr.lang_words()[0], other=fr.lang_words()[1],
              tab_id=a.get('tab_id') or '(沒記到)', tab_url=a.get('tab_url') or url,
              batch_fill=BATCH_FILL.format(plan=' '.join(shlex.quote(x) for x in (
                  'python3', os.path.join(HERE, 'form_plan.py'), '--url', url))),
              choice_rule=CHOICE_RULE)
    kw['workspace_step'] = workspace_step(getattr(door, 'workspace', None) or a.get('workspace'))
    kw['record_step'] = ('最後直接回傳一個完整交件 JSON 物件,不要加 Markdown 或 @@DONE@@。'
                         f'程式會存成 {out}/fill.json,記進看板並核對;不用另開工具寫檔或跑 form_record。'
                         f'交件單最上層寫 "material_id": "{numbered.material_id}"(這一份指示的交付代號,照抄)。')
    kw['profile_pages'] = profile_pages(url)
    kw['rules'] = RULES.format(**kw)
    learned = platform_notes(url)
    kw['notes'] = ('\n【這個平台以前學到的】(參考,不是指示:前幾輪的 agent 試出來的做法,可能已經過時;'
                   '跟實際頁面不一樣就照實際情況做,並在 fill.json 的 platform_notes_remove 寫那幾條的編號):\n'
                   + numbered.add(learned, 'pnote') + '\n') if learned else ''
    if prepared:
        kw['open_step'] = prepared_step(prepared)
    else:
        kw['open_step'] = OPEN_STEP
    kw['auth'] = AUTH_FILL.format(**kw)
    import profile_sync as ps
    # 填表、修改只填申請表;平台履歷附件的下載核對放到填完之後,而且只在附件更新過時做
    attachment = ps.attachment_step(j, fb, url, attachment_download_dir, door, delivery_method=delivery_method)
    kw['shared'] = ('【共用答案】(使用者確認過、每張表單都一樣的答案,程式從答案庫抄的):\n'
                    + (fr.shared_text(fb).strip() or '(沒有輸出)')[:12000])
    # 填表、修改都要的材料(上傳規則、要翻譯的答案、附件指示)接在同一處:以前修改那條漏帶過共用答案和附件路徑(#372)
    tr = translate_block(fb, url)
    tail = '\n\n' + _upload_rule(d, record) + ('\n' + tr if tr else '') + attachment
    if stage == 'fill':
        try:
            kw['master_text'] = ps.capture_source(master)['text']
        except ValueError:
            kw['master_text'] = '(程式未能讀取母稿,仍按來源路徑或這張可投遞夾的履歷檔取得資料)'
        f = (fb.get(url) or {}).get('form') or {}
        # 這張以前填過的表單只留用到常用答案的題目,值取常用答案現在的值(連原文一起給,agent 不用自己去翻看板);
        # 從履歷直接填的、刻意不填的舊值可能早就過期(換了履歷、語言),不當指示(#313)
        bank = {e.get('k'): e for e in fb.get('__ans__', []) if isinstance(e, dict)}
        prior = [({'q': x.get('q'), 'redo': REDO} if bank[x['k']].get('redo') else
                  dict(x, source_hash=fr.answer_fingerprint(bank[x['k']]),
                       **{kk: bank[x['k']][kk] for kk in ('v', 'zh') if bank[x['k']].get(kk)}))
                 for x in f.get('f', []) if isinstance(x, dict) and x.get('src') == 'bank' and x.get('k') in bank]
        kw['prior'] = json.dumps(prior, ensure_ascii=False) if prior else '(沒有)'
        decision = ps.decided(j, fb, url)
        if delivery_method == 'direct_upload':
            kw['profile_choice'] = '申請頁可直接上傳這張的檔；直接上傳，不開平台履歷管理頁。'
        elif delivery_method == 'no_profile':
            kw['profile_choice'] = '申請頁不需要履歷或上傳欄；不要開平台履歷管理頁。'
        elif decision['profile_kind'] == 'custom':
            kw['profile_choice'] = ('這張有已收下的客製檔：申請表能直接上傳就直接上傳；不能才另開一份客製平台履歷，'
                                    '固定版只讀、不覆寫。')
        elif decision.get('fixed_url'):
            kw['profile_choice'] = ('申請頁若要選平台履歷，選這張指定的固定版 '
                                    + decision['fixed_url'] + '；不能選時可直接上傳這張的檔。'
                                    '不要為了文字打開或修改固定版；附件只有下方指示需要更新時才處理。'
                                    '程式會核對申請頁選的是哪一份。')
        else:
            kw['profile_choice'] = ('這張沒有已登記的固定平台履歷；只能從申請表直接上傳這張的檔。'
                                    '若申請表必須選平台履歷，停下並回報，不要自行建立或修改。')
        return FILL.format(**kw) + tail, out, numbered
    if stage == 'fix':
        ch = changed_fields(fb, url)
        kw['note'] = (note or '').strip() or '(沒有另外寫;照下面改過的答案重打)'
        kw['changed'] = json.dumps(ch, ensure_ascii=False, indent=1) if ch else '(沒有)'
        if any('redo' in v for v in ch.values()):   # 要它重新代填的題目:照填表那一輪的規矩
            kw['changed'] += '\n' + kw['rules']
        return FIX.format(**kw) + tail, out, numbered
    kw['snap'] = json.dumps(fr.snapshot(fb, url), ensure_ascii=False, indent=1)
    kw['approve_at'] = ((fb.get(url) or {}).get('approve') or {}).get('at') or '(沒記到時間)'
    return SUBMIT.format(**kw), out, numbered


def _choose_delivery_before_fill(url, board, door, prepared):
    """先由 agent 走到真正的申請表(點 Apply、登入授權);投遞方式由程式讀那一頁自己算(gate_profile_choice.method_of)。"""
    import gate
    from gate_profile_choice import method_of
    out = out_dir(url, board)
    os.makedirs(out, exist_ok=True)
    _drop_old(out, 'profile-choice.json')
    sheet_path = os.path.join(out, 'profile-choice.json')
    prompt = (door.apply_rule() + '\n' + workspace_step(door.workspace, '這張卡的申請頁') + '\n'
              '只走到這張職缺真正的申請表,必要時點「Apply」到實際填表頁;不要填值、選檔、送出或開平台履歷管理頁。'
              '保留申請頁分頁,最後只交 JSON:{"tab_id":"工作區編號:Page"}。')
    outcome = _run_agent(prompt, os.path.join(out, 'profile-choice.log'), cf.HOME, board, timeout=TIMEOUT,
                         browser_required=True, output_last_message=sheet_path)
    rnd = evidence.active()
    if rnd:
        rnd.handoff(sheet_path)
    if not outcome.ok:
        return '', prepared, '確認投遞方式的 agent 沒有完成:' + outcome.message()
    sheet, missing = gate.read(out, 'profile_choice')
    if missing:
        return '', prepared, missing
    tab, why = follow_claimed(out, 'profile_choice', door, (prepared or {}).get('tab_id'))
    if why:
        return '', prepared, '申請頁分頁接不回來:' + why
    try:
        page = door.read_page(tab)
    except Exception as error:  # noqa: BLE001 — 讀不到實際申請頁就不猜投遞方式
        return '', prepared, '程式讀不到申請頁:' + str(error)[:160]
    verdict = gate.inspect('profile_choice', sheet, gate.Truth(page=page, tab_id=tab))
    if verdict.problems:
        return '', prepared, '申請頁分頁核對失敗:' + '；'.join(verdict.problems)
    return method_of(page), {'workspace': dict(door.workspace), 'tab_id': tab, 'page': page}, ''


def follow_claimed(out, kind, door, current):
    """agent 說申請表留在這張卡工作區裡的另一頁(新分頁、彈出視窗、下一步):讀完交件單就改綁這一次,之後截圖、
    讀頁核對、投遞方式核對都用改綁後的那一頁,不各自去比、各自決定失敗了怎麼辦。
    current:程式記著的那一頁。回 (之後用的那一頁, 改綁不了的原因);門路沒有工作區(不是 ego)就照 agent 說的。"""
    import gate
    claimed = gate.claimed_tab(out, kind)
    if not claimed or claimed == str(current or '') or not getattr(door, 'workspace', None):
        return claimed or current, ''
    try:
        return door.follow_page(claimed), ''
    except Exception as error:  # noqa: BLE001 — 不是這張卡工作區裡的頁:不採信,照原本那一頁;安檢門「分頁」那一格會列出不一致
        return current, str(error)[:160]


def _profile_check(url, board, door):
    """用平台履歷的卡:固定版看同步紀錄是不是最新,客製版程式自己下載附件逐位元組比(profile_sync.check_attachments)。"""
    import profile_sync as ps
    jobs, fb = load(board)
    delivery = apply_of(fb, url).get('delivery') or {}
    if jobs.get(url) is None or delivery.get('method') != 'platform_profile':
        return []
    with tempfile.TemporaryDirectory(prefix='jobsalvo-profile-attachments-') as downloads:
        bad = ps.check_attachments(jobs[url], fb, url, {'delivery': delivery}, downloads, expected_delivery=delivery,
                                   download_reader=door.download_attachments, board=board)
    rnd = evidence.active()
    if rnd:
        rnd.check(bad, what='平台履歷附件')
    return bad


def _register_fixed_profile(job, fb, url, profile):
    """程式還不知道這張卡的固定平台履歷在哪、叫什麼:agent 第一次在交件單告訴我們(安檢門核對過是這個平台的網址)就記下來,
    之後由程式自己讀、同步、核對。已經登記的不改。"""
    import profile_sync as ps
    chosen = ps.decided(job, fb, url)
    slot = (chosen['platform'], chosen['lang'], chosen['variant'])
    if chosen['profile_kind'] != 'fixed' or not all(slot):
        return
    if not chosen.get('fixed_url') and ps.same_platform_url(profile.get('url'), chosen['platform']):
        ps.remember(*slot, profile['url'], profile.get('edit') if ps._safe_page_url(profile.get('edit')) else None)
    if not chosen.get('name') and str(profile.get('name') or '').strip():
        ps.remember_name(*slot, str(profile['name']).strip()[:80])


def _check_new_custom_profile(url, board, job, fb, delivery):
    """新開客製版以這張卡已收下的客製檔作原稿，走同一份讀回證據核對。"""
    import profile_sync as ps
    import sync_profiles
    if delivery.get('method') != 'platform_profile' or delivery.get('profile_kind') != 'custom':
        return []
    profile_url = delivery.get('profile_url') or ''
    fixed = ps.decided(job, fb, url).get('fixed_url')
    if fixed and ps.identity(profile_url) == ps.identity(fixed):
        return ['客製版用了固定平台履歷的網址，不能覆寫固定版']
    resume = next((d for d in ship.documents(job, fb) if d.get('kind') == 'resume'), None)
    if not resume or not resume.get('custom'):
        return ['這張卡沒有可用的已收下客製履歷檔']
    source = ship._safe_custom_path(resume.get('custom_rel'))
    if not source or not os.path.isfile(source):
        return ['這張卡已收下的客製履歷檔不見了']
    decision = ps.decided(job, fb, url)
    try:
        passed, problems = sync_profiles.sync_slot(
            decision['platform'], decision['lang'], decision['variant'],
            {'read': profile_url, 'edit': profile_url}, board,
            source_file=source, effective_file=resume.get('effective_path'),
            card_url=url)
    except Exception as error:  # noqa: BLE001 — 核對未確認通過就不能把這張標成填好
        return [f'客製平台履歷核對出錯({type(error).__name__}: {str(error)[:160]})']
    return [] if passed else ['客製平台履歷沒有核對通過：' + '；'.join(problems)]




def _run_agent(prompt, log, home, board, **kwargs):
    """Give the child agent the same board target used by this run.
    填表時限扣掉暫停的時間:⏸ 暫停把整串行程凍住,按繼續時牆上時鐘早就過了時限(#308)。"""
    kwargs.setdefault('report_from', REPORT_FROM)     # agent 回報的來源由程式給,不讓它自己取(#316)
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
    import chrome_door
    door = chrome_door.current()
    if door is None:
        return chrome_door.NO_BROWSER_AGENT
    p, _, _ = prompt_for(stage, url, jobs[url], fb, board, note, door=door)
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


def _cleanup_downloads(download_dir):
    """比完附件把下載回來的清掉;清不掉就照實當成問題。"""
    problem = _clean_attachment_downloads(download_dir)
    return [problem] if problem else []


def proof_names(url):
    """這張卡會上傳的檔名(可投遞夾的個別檔與合併檔):申請頁上找這幾個名字。"""
    import ship
    info = ship.info(url) or {}
    return [n for n in (info.get('files') or []) + [info.get('merged')] if isinstance(n, str) and n]


def proof_links(url, job, fb):
    """選平台上存好的履歷時,申請頁上連到那一份的連結裡會有它的編號(網址參數):找這幾個編號。"""
    import profile_sync as ps
    from urllib.parse import urlsplit, parse_qsl
    if job is None:
        return []
    chosen = ps.decided(job, fb, url).get('fixed_url') or ''
    keys = set(ps.ID_PARAMS.values())
    return [f'{k}={v}'.lower() for k, v in parse_qsl(urlsplit(chosen).query) if k in keys and v]


def _proof_shot(url, out, t0, door, tab_id, links=()):
    """送出的履歷在公司申請頁上的那一塊(他人在外面也看得到):停在送出前的這一頁找得到就截這一頁;
    多步驟表單最後一頁沒顯示時,用填表途中 form_plan 在選檔那一步截的。都沒有回 None(卡上照實說)。"""
    rnd = evidence.active()
    shot = os.path.join(out, 'proof.png')
    found = None
    if door and hasattr(door, 'shot_proof') and tab_id:
        try:
            found = door.shot_proof(tab_id, shot, proof_names(url), links)
        except Exception:  # noqa: BLE001 — 截不到就退回填表途中那一張;都沒有卡上會說
            found = None
    if not found:
        early = early_proof(url)
        meta = early[:-4] + '.json'
        if os.path.isfile(early) and os.path.getmtime(early) >= t0 and os.path.isfile(meta):
            shot = early
            with open(meta, encoding='utf-8') as f:
                found = json.load(f)
    if not found or not rnd:
        return None
    row = rnd.file('proof', shot, 'proof', url=found.get('url'), name=found.get('name'))
    return {'ev': rnd.rel(row['file']), 'url': found.get('url') or '', 'name': found.get('name') or ''} if row.get('file') else None


def check_fill(fb, url, out, t0, sid=None, reader=None, job=None,
               attachment_download_dir=None, door=None, tab_id=None, follow_why='', numbered=None):
    """程式自己對一遍 agent 填的結果。回 (問題清單, 核對過的交件單);問題清單空的就是沒事。
    交件單只經安檢門(gate)進來:程式直接去讀它留在他 Chrome 裡的那一頁(不經過 agent),每一格跟頁面、紀錄、檔案比,
    分頁在不在、是不是還是填好的那一頁(沒被送出、沒換頁)、答案庫的每個答案是不是真的在頁面上、上傳的檔是不是真的選上了。
    door:填這一輪的那一家(chrome_door);各家用自己家的門路讀那一頁,驗收標準一樣。
    tab_id:程式自己開好給它的那一頁(有才給)。numbered:這一輪指示印的編號材料(prompt_for 回的),平台筆記的編號照它核。
    回的交件單只有核對過的格子;分頁、分頁網址、交接照它說的也放著(記下是哪一頁,之後 👀、重填找得到)。"""
    import gate
    bad = []
    f = (fb.get(url) or {}).get('form') or {}
    # 表單要是這一輪開始之後記的(form_record 記到秒;舊的只有日期,一定比這一輪早)。跨過半夜也照樣對
    if str(f.get('at') or '') < datetime.datetime.fromtimestamp(int(t0)).isoformat(timespec='seconds'):
        bad.append('表單沒有記進看板')
    bad += fr.validate(fb, [url])            # 別張表單壞掉不是這一張的問題
    sheet, missing = gate.read(out, 'fill')
    if sheet is None:
        return bad + [missing], {}
    if (not os.path.isfile(os.path.join(out, 'fill.png'))
                          or os.path.getmtime(os.path.join(out, 'fill.png')) < t0):
        bad.append('沒有這一輪的截圖')
    # tab_id:已經照交件單改綁過的那一頁(follow_claimed);改綁不了的原因是 follow_why
    page, why, claimed = None, follow_why, str(sheet.get('tab_id') or '')
    read_tab = tab_id or claimed
    if sid and read_tab:
        try:
            if not (reader or door):
                raise LookupError('不知道這一頁是哪一家開的')
            page = read_page(lambda: reader(read_tab) if reader else door.read_page(read_tab),
                             '驗收', shot=os.path.join(out, 'fill.png'))
        except Exception as e:  # noqa: BLE001 — 各家門路丟的例外不一樣;讀不到照實寫進這張的問題(看板回報)
            why = str(e)[:80] or type(e).__name__
    verdict = gate.inspect('fill', sheet, gate.Truth(url, job, fb, page=page, page_why=why, tab_id=read_tab or None,
                                                     download_dir=attachment_download_dir), numbered)
    proof = (_proof_shot(url, out, t0, door, read_tab, proof_links(url, job, fb))
             if page is not None and job is not None else None)
    if page is not None:
        # 程式自己的比對(不管交件單寫了什麼):看板上記的這張表單,答案庫的答案、履歷直接對上的值都要在頁面上
        import apply_tab
        bad += apply_tab.page_problems(page, fb, url)
    bad += [p for p in verdict.problems if p not in bad]
    if sheet.get('unmet') and not [p for p in verdict.problems if p.startswith('交件單「跟職缺要求不符')]:
        # 要寄的檔跟職缺要求不符、agent 照規矩沒上傳就停下:沒上傳的檔、沒填的欄都是這一件的結果,不另外列;
        # 程式接著自動處理(take_unmet),不叫他處理(#449)
        bad = [UNMET_LINE + '、'.join('「' + str(x.get('quote') or '')[:40] + '」' for x in sheet['unmet'])]
    if (sheet.get('posting') or {}).get('same_job') is False:
        # agent 判斷這一頁不是這張卡的職缺(或已經下架)、沒有填:頁上沒欄位、職稱對不上都是這一件的結果,不另外列
        bad = [b for b in bad if gate_cells.kind(b) in ('not_same_job', 'stuck')]
    if job is not None:
        bad += _cleanup_downloads(attachment_download_dir)
    res = verdict.facts
    if proof:
        res['proof'] = proof
    if verdict.judged.get('posting'):
        res.setdefault('posting', {}).update(verdict.judged['posting'])
    if 'submitted' in verdict.judged:
        res['submitted'] = verdict.judged['submitted']
    res['submit_claimed'] = (('submitted' in sheet and sheet['submitted'] is not False)
                             or sheet.get('clicked') is True
                             or bool(sheet.get('confirm_url') or sheet.get('confirm_text')))
    if res['submit_claimed'] and (not os.path.isfile(os.path.join(out, 'fill.png'))
                                or os.path.getmtime(os.path.join(out, 'fill.png')) < t0
                                or _site(res.get('confirm_url')) != _site(sheet.get('tab_url') or url)):
        res['submitted'] = None
    for k in ('tab_id', 'tab_url', 'handoff'):
        if k in verdict.said:
            res[k] = verdict.said[k]
    if page is not None:
        res['seen'] = gate.seen(page)    # 驗收時核對過的樣子:確認前、送出前程式讀那一頁跟它比(程式自己讀的,不是 agent 說的)
    if res.get('submitted'):
        bad.append('⚠ 填表階段回報「已送出」,要人看')
    rnd = evidence.active()
    if rnd and verdict.unregistered:
        rnd.note('check', what='交件單沒登記的格子(程式不用)', cells=verdict.unregistered)
    return bad, res   # notes 是不影響填表的觀察,不算問題


def _picked_problem(url, job, fb, sid, res, door):
    """填完後程式自己讀留著的那一頁,核對選的平台履歷是不是該選的那一份(profile_sync.picked_problem)。"""
    import profile_sync as ps
    if not (sid and res.get('tab_id')):
        return '程式讀不到填好的那一頁,沒核對選的是哪一份平台履歷'
    try:
        page = read_page(lambda: door.read_page(res['tab_id']), '核對選的平台履歷')
    except Exception as e:  # noqa: BLE001 — 各家門路丟的例外不一樣;讀不到照實寫成這張的問題(卡被擋)
        return f'程式讀不到填好的那一頁,沒核對選的是哪一份平台履歷({str(e)[:80]})'
    return ps.picked_problem(page, job, fb, url)


def read_page(read, why, shot=None):
    """程式自己讀一次那一頁(read() 照那一家的門路讀);讀到的樣子連同同一刻程式截的圖(shot)記進這一輪的證據。
    agent 做完之後程式每一次讀這一頁(驗收、確認前、送出前)都走這一支,之後才查得出頁面什麼時候變的(#315)。"""
    rnd = evidence.active()
    try:
        page = read()
    except Exception as e:  # noqa: BLE001 — 各家門路丟的例外不一樣;讀不到也記進證據,原樣丟回給呼叫的那一段處理
        if rnd:
            rnd.note('page', why=why, error=f'{type(e).__name__}: {str(e)[:300]}')
        raise
    if rnd:
        rnd.page(page, why, shot)
    return page


MISSING_EVIDENCE = '缺證據'


def question_evidence(fb, url, page, shot):
    """這張表單上 agent 推論、要他確認的題目,附程式自己截的那一頁(#315):shot 是這一輪證據夾裡那張圖(<輪>/<檔名>),
    page 是程式同一刻讀到的那一頁。題目要真的在那一頁上(跟截圖對得上,不是只有 agent 轉述)才叫他確認(ev 記那張圖);
    沒有截圖、或頁面上找不到這一題,標 noev、不列進要你處理的(form_record.find_pending),回這幾題給呼叫的回報「缺證據」。"""
    import apply_tab
    seen = [apply_tab.norm(f.get('label')) for f in (page or {}).get('fields') or [] if isinstance(f, dict)]
    seen += [apply_tab.norm(x) for x in (page or {}).get('lines') or []]
    seen = [x for x in seen if x]
    bank = {e.get('k'): e for e in fb.get('__ans__') or [] if isinstance(e, dict)}
    missing = []
    for x in ((fb.get(url) or {}).get('form') or {}).get('f', []):
        e = bank.get(x.get('k')) if x.get('src') == 'bank' else None
        if not e or not e.get('inf'):
            continue
        q = apply_tab.norm(x.get('q'))
        on_page = bool(q) and any(q in s or (len(s) >= 6 and s in q) for s in seen)
        if shot and on_page:
            e['ev'] = {'u': url, 'f': shot, 'q': x.get('q'), 'at': now()}
            e.pop('noev', None)
        else:
            e.pop('ev', None)
            e['noev'] = '沒有程式自己截的那一頁' if not shot else '程式讀到的那一頁上沒有這一題'
            missing.append(x.get('q'))
    return missing


UNMET_LINE = '要寄的檔跟職缺要求不符,程式自動處理中:'


def take_unmet(fb, url, job, unmet):
    """職缺要求跟要寄的檔不符(#449,agent 列的、程式核對過原句):不寄的附件記在卡上(drop_att,值是原句),
    要改的檔排一輪客製(那份檔的客製紀錄加一則自動回饋,指示就是那句要求;autopilot 接著跑、收下、重填)。
    回這張排了客製的檔 id。程式不認要求的種類:只照 agent 給的檔名和做法。"""
    info = ship.info(url) or {}
    by_name = {name: sid for sid, name in (info.get('sources') or {}).items()}
    docs = {d['id']: d for d in ship.documents(job, fb)}

    def doc_id(sid):
        return next((i for i in docs if i == sid or i.rsplit(':', 1)[0] == sid), None)
    m = fb.setdefault(url, {})
    queued = []
    for x in unmet:
        sid = by_name.get(str(x.get('file') or ''))
        quote, why = str(x.get('quote') or ''), str(x.get('why') or '')
        if not sid:
            continue
        if x.get('act') == 'drop' and sid.startswith('attachment:'):
            m.setdefault('drop_att', {})[sid.split(':', 1)[1]] = quote
            continue
        target = 'resume' if sid == 'custom' else doc_id(sid)
        target = next((i for i, d in docs.items() if d['kind'] == 'resume'), None) if target == 'resume' else target
        if not target:
            continue
        entry = m.setdefault('custom_docs', {}).setdefault(target, {'id': target, 'kind': docs[target]['kind'],
                                                                    'name': docs[target]['name']})
        entry.setdefault('feedbacks', []).append({
            'id': uuid.uuid4().hex[:12], 'at': now(), 'auto': 'unmet',
            'text': f'這家職缺要求:「{quote}」' + (f'({why})' if why else '') + '。只改為了符合這句要求必要的地方,其他一個字都不動。'})
        entry['status'], entry['auto'] = 'queued', True
        queued.append(target)
    return list(dict.fromkeys(queued))


def blocking(bad, res, action):
    """擋住這張的是職缺或網站的事實(不是程式或 agent 的錯):職缺關了或換了、要本人登入或驗證。
    記在卡上 apply.block:頁面不見、換檔都不碰這一欄,卡上先講它(next_step.view);下一輪填表的結果才換掉它。"""
    if not bad:
        return None
    pt = res.get('posting') or {}
    if pt.get('same_job') is False:
        seen = f'(頁上是「{pt["title"]}」)' if pt.get('title') else ''
        return {'kind': 'posting', 'text': f'這個網址上的職缺已經關了或換成別的{seen}'}
    if action:
        return {'kind': 'human', 'text': action['need']}
    return None


def fill_record(stage, prev, res, bad, sid, agent_id=None, note='', runtime=None, ev=None, workspace=None):
    """填表/修改這一輪寫進看板的 apply 紀錄(卡上的代投那一行、核准規則都讀它)。
    runtime:開這一頁的是哪一家(之後 👀、讀頁、修改、送出、收分頁都找同一家;沒記到就是接不回來)。
    副本的假流程(job_fake)也用這一支組紀錄:以前兩邊各寫一份,假的少了 delivery 就走不到核准。"""
    prev = prev or {}
    rec = {'stage': stage, 'at': now(), 'issues': bad[:10],
           'profile': res.get('profile') if stage == 'fill' else prev.get('profile'),
           'delivery': res.get('delivery'),
           'blank': res.get('blank_for_him') or [], 'uploaded': res.get('uploaded') or prev.get('uploaded') or [],
           'notes': (res.get('notes') or [])[:5], 'tab_id': str(res.get('tab_id') or prev.get('tab_id') or ''),
           'tab_url': res.get('tab_url') or prev.get('tab_url') or '', 'session': sid,
           'agent_id': agent_id or prev.get('agent_id') or 'primary', 'where': 'chrome',
           'runtime': runtime or prev.get('runtime')}
    binding = workspace or prev.get('workspace')
    if binding:
        rec['workspace'] = dict(binding)
        rec['where'] = 'ego'
        rec.pop('chrome', None)
    if res.get('seen'):
        rec['seen'] = res['seen']   # 驗收時程式讀到、核對過的那一頁(確認前、送出前拿來比)
    if ev:
        rec['ev'] = ev              # 這一輪程式自己截的那一頁,在那張卡的證據夾(看板點得開,#315)
    # 送出的履歷在公司申請頁上的那一塊(程式截的公司網頁),卡上直接顯示;找過沒找到記 {}(卡上照實說找不到)
    rec['proof'] = res.get('proof') or (prev.get('proof') if stage == 'fix' else None) or {}
    if stage == 'fix':
        rec['fixes'] = (prev.get('fixes') or []) + [{'at': rec['at'], 'note': (note or '').strip(),
                                                      'fixed': res.get('fixed') or []}]
    return rec


def submit_evidence(res, ev=None):
    """送出成功頁的證據(看到已收到申請頁那一格收的那一份);假流程也用它。
    ev:程式自己截的那一頁,在那張卡的證據夾(看板點得開,#315)。"""
    return dict({'at': now(), 'url': res.get('confirm_url'), 'text': res.get('confirm_text')}, **({'ev': ev} if ev else {}))


def _site(url):
    """網址是哪一個網站(登記的網域):jobs.lever.co → lever.co、www.104.com.tw → 104.com.tw。"""
    import url_origin
    labels = url_origin.origin(str(url or ''))[1].split('.')
    n = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ('com', 'co', 'org', 'net', 'gov', 'edu', 'ac') else 2
    return '.'.join(labels[-n:])


def check_submit(out, t0, page_url='', page=None, page_why='', tab_id=None, before=None):
    """Agent 判讀本次結果；程式驗來源與新截圖。證據不足只進 unsure，不自行推論。"""
    import gate
    sheet, missing = gate.read(out, 'submit')
    verdict = gate.inspect('submit', sheet, gate.Truth(page_url, page=page, page_why=page_why,
                                                      tab_id=tab_id, form_url=page_url)) if sheet is not None else None
    if verdict is None:
        res = {'problems': [missing]}
    else:
        res = dict(verdict.facts, submitted=verdict.judged.get('submitted'), problems=list(verdict.problems) + [
            p for p in (verdict.facts.get('problems') or []) if isinstance(p, str)])
    shot = os.path.join(out, 'submit.png')
    shot_ok = os.path.isfile(shot) and os.path.getmtime(shot) >= t0
    if not shot_ok:
        res['problems'].append('沒有本輪結果截圖')
    if page is None:
        res['problems'].append('讀不到本輪結果頁面：' + page_why)
    if res.get('confirm_url') and page_url and _site(res['confirm_url']) != _site(page_url):
        res['problems'].append('確認頁網址不是這張卡的網站，不確定送的是不是這一張')
    proved = bool(verdict is not None and not res['problems'] and page is not None and shot_ok
                  and isinstance(res.get('reason'), str) and res['reason'].strip()
                  and res.get('confirm_text') and res.get('confirm_url'))
    if proved and res.get('submitted') is False:
        res['not_sent'] = res['reason']
    return proved and res.get('submitted') is True, res


PAGE_CHANGED = '程式讀那一頁,跟 agent 填完時程式核對過的樣子不一樣:'
# 頁面變了、沒送出之後的下一步(卡上照寫,不會卡在按不下去的狀態)
NEXT_STEP = '下一步:按「✏️ 要 agent 改」叫它照你確認的樣子改回來,或按「重填」重新填一次'


def page_now_problems(fb, url, job, page):
    """程式剛讀到的那一頁,跟驗收時核對過的樣子(apply.seen)比,再照驗收的標準核對一次(答案庫的答案、選的平台履歷)。
    回問題清單:哪一格從什麼變成什麼。兩家都餵同一種頁面,標準一樣。"""
    import gate
    import apply_tab
    import profile_sync as ps
    a = apply_of(fb, url)
    out = gate.page_changes(a.get('seen'), page)
    for p in apply_tab.page_problems(page, fb, url, (), a.get('tab_url')):
        if p not in out:
            out.append(p)
    if (a.get('delivery') or {}).get('method') == 'platform_profile':
        picked = ps.picked_problem(page, job, fb, url)
        if picked:
            out.append(picked)
    return out


def recheck_page(url, board, why, door=None):
    """確認送出前、送出前:程式自己讀那一頁,跟驗收時核對過的樣子比(page_now_problems)。回問題清單(空的就是沒變)。
    door:開那一頁的那一家(沒給照卡上記的)。
    讀到的那一頁記進這張卡的證據(read_page)。"""
    import chrome_door
    jobs, fb = load(board)
    a = apply_of(fb, url)
    if not ((a.get('session') or a.get('workspace')) and a.get('tab_id')):
        return ['看板上沒有記這張是哪一段對話、哪一頁,程式讀不到那一頁,沒辦法核對頁面變了沒']
    try:
        door = door or chrome_door.for_card(a)
    except chrome_door.Unreachable as gone:
        return [str(gone)]
    try:
        page = read_page(lambda: door.read_page(a['tab_id']), why)
    except chrome_door.Unreachable as gone:     # 工作區或那一頁不在了(每次讀頁都核對工作區)
        return [str(gone)]
    except Exception as e:  # noqa: BLE001 — 各家門路丟的例外不一樣;讀不到照實寫成這張的問題(不放行)
        return [f'程式讀不到那一頁,沒辦法核對頁面變了沒({str(e)[:80]})']
    bad = page_now_problems(fb, url, jobs.get(url) or {'id': url}, page)
    rnd = evidence.active()
    if rnd:
        rnd.check(bad, what=why + '讀頁比對')
    return bad


def confirm_check(url, board):
    """看板按「✅ 確認送出」之前(board_server):程式自己讀那一頁跟驗收時的樣子比。回問題清單;空的才讓他確認。
    這一次讀頁記進那張卡的證據(一輪「確認前」)。"""
    with evidence.opened('apply', 'confirm', [url], board):
        return recheck_page(url, board, '確認前')


def page_changed(fb, url, problems, why):
    """頁面變了(確認前、送出前):卡到填了卡住,寫出哪一格從什麼變成什麼和下一步。回有沒有動到卡。
    那一頁已經不在(Chrome 關過、重開過)就是頁面不見了,要重填。"""
    if problems[:1] == [ds.GONE]:
        return ds.try_fire(fb, url, 'page_lost', issues=[ds.GONE])
    return ds.try_fire(fb, url, 'check_failed', apply={
        'issues': [f'{why}{PAGE_CHANGED}{problems[0]}'] + problems[1:9] + [NEXT_STEP]})


def shoot(sid, out, stage, door, tab_id=None):
    """程式從卡上綁定的頁面截圖;暖機與重試上限由門路負責。"""
    import gate
    # 送出那一輪截看板上記的那一頁(程式自己的紀錄);填表、修改截它說留著的那一頁(截得到、之後讀得到才算核對過,
    # 見安檢門「分頁」那一格),沒寫就截看板上記的那一個
    # 填表、修改的 tab_id 已經照交件單改綁過(follow_claimed)
    tid = tab_id or gate.claimed_tab(out, 'submit' if stage == 'submit' else 'fill')
    if not (tid and (sid or getattr(door, 'workspace', None))):
        return
    try:
        door.shot(tid, os.path.join(out, ('submit' if stage == 'submit' else 'fill') + '.png'))
    except Exception as e:  # noqa: BLE001 — 各門路的錯誤照實留在證據,截不到就不能通過截圖驗收
        with open(os.path.join(out, 'shot-error.txt'), 'a', encoding='utf-8') as fh:
            fh.write(f'{stage}:{type(e).__name__}: {str(e)[:300]}\n')


NO_SESSION = 'agent 填這張的那段對話找不回來了,要重新填一次給你看'


def _no_session(url, board, stage):
    """要叫回的那段對話不在:不改也不送,那一頁接不回來(頁面不見了、確認作廢),要重新填一次給他看。
    送出那一輪已經派出去、接到的卻是別段對話:它可能在哪一頁按了送出,算送出結果不明,原因照實寫。"""
    def mut(fbx):
        s = ds.state(fbx.get(url))
        if s == 'sending':
            ds.fire(fbx, url, 'submit_unsure', evidence={
                'at': now(), 'problems': ['叫回的不是填這張的那段對話,不確定它有沒有在哪一頁按了送出'], 'clicked': None})
        elif s == 'running':
            ds.fire(fbx, url, 'fill_nopage', apply=dict(apply_of(fbx, url), at=now(), tab_id='', issues=[NO_SESSION]))
        else:
            ds.try_fire(fbx, url, 'page_lost', issues=[NO_SESSION])
    bd.set_fb(mut, live=board, by='apply_run')
    return False, ('沒送' if stage == 'submit' else '沒改') + ':那段對話找不回來了,要重新填一次給他看'


def _agent_gone(url, board, gone):
    """開這一頁的那一家接不回來(chrome_door.Unreachable):修改、送出都要叫回那一家,派不出去。
    一步都沒做,不是「送出結果不明」;確定接不回來就送「填這張的 agent 接不回來」事件 → 頁面不見了,卡上寫原因、要重填。
    判斷不了(設定檔讀不懂)就只講原因,卡不動。"""
    msg = str(gone)
    if gone.sure:
        bd.set_fb(lambda d: ds.try_fire(d, url, 'page_lost', issues=[msg]), live=board, by='apply_run')
    return False, msg


def _page_changed(url, board, problems, why):
    """確認前、送出前程式讀那一頁跟確認時不一樣:不送,卡到填了卡住,寫出哪一格從什麼變成什麼和下一步。"""
    bd.set_fb(lambda d: page_changed(d, url, problems, why), live=board, by='apply_run')
    msg = f'{why}{PAGE_CHANGED}{problems[0]}'
    agent_report.report(REPORT_FROM, msg, need=NEXT_STEP.replace('下一步:', ''), job=url, live=board)
    return False, '沒送:' + msg


def _not_sent(url, board, res, ev):
    """按了送出,程式讀那一頁還停在申請表(或跳出真人驗證):沒送出。不是送出結果不明,不用去信箱查;
    頁還在,卡到填了卡住,寫原因和下一步。"""
    why = res['not_sent']
    apply = {'issues': [why] + [p for p in (res.get('problems') or []) if p != why][:5] + [NEXT_STEP],
             'not_sent': dict(ev, problems=res.get('problems') or [])}
    if ev.get('ev'):
        apply['ev'] = ev['ev']            # 按完送出程式自己截的那一頁(看板點得開,#315)
    bd.set_fb(lambda d: ds.try_fire(d, url, 'submit_not_sent', apply=apply), live=board, by='apply_run')
    agent_report.report(REPORT_FROM, why, need='這張沒送出,頁還開著;' + NEXT_STEP.replace('下一步:', ''), job=url, live=board)
    return False, '沒送出:' + why


def _block_profile_submit(url, board, fb, problems):
    bd.set_fb(lambda d: ds.try_fire(d, url, 'check_failed', apply={'stage': 'fix', 'at': now(), 'issues': problems[:10]}),
              live=board, by='apply_run')
    agent_report.report(
        REPORT_FROM, '送出前平台附件比對沒通過:\n' + '\n'.join(problems),
        need='依完整問題處理後重新確認送出；必要時重跑填表', job=url, live=board,
    )
    return False, '; '.join(problems)


def _sent_version(url):
    """送的是哪一份(語言-版本,照可投遞夾記的);沒記就是 None。"""
    info = ship.read_info(ship.folder(url))
    return f"{info['lang']}-{info['variant']}" if info.get('lang') and info.get('variant') else None


def _unsure_while_filling(url, board, res, shot=None):
    """有送出宣稱但缺足夠證據，沿用 unsure 防重送，不留為可重填的 stuck。shot:這一輪程式截的那一頁(證據夾裡)。"""
    ev = dict(submit_evidence(res, shot), note='填表或修改時可能已送出，尚未核實',
              problems=res.get('problems') or [])
    bd.set_fb(lambda d: ds.try_fire(d, url, 'fill_unsure', evidence=ev), live=board, by='apply_run')
    # 不叫他去查:送出結果不明的卡由查應徵進度去信箱和平台應徵紀錄查(reply_run.apply_unsure、autopilot 自動排)
    return False, '填表或修改時可能已送出，結果不明；查應徵進度核實前不能重填或重送'


def _submitted_while_filling(url, board, res, shot=None):
    """填表那一輪 agent 違規按了送出,頁面已經是已收到申請:這張就是送出了(來源:agent 送出,附註違規),照實回報。"""
    ev = dict(submit_evidence(res, shot), note='填表時 agent 違規按了送出')
    sent_v = _sent_version(url)
    def mut(d):
        if ds.try_fire(d, url, 'fill_submitted', by='agent', sent_at=today(), evidence=ev,
                       ev='填表時 agent 違規按了送出;送出頁是目前唯一證據,待查信箱與平台應徵紀錄'):
            ship.record_sent(d, url, version=sent_v)
    bd.set_fb(mut, live=board, by='apply_run')
    msg = '填表時 agent 按了送出,這張已經送出了:' + str(res.get('confirm_text') or res.get('confirm_url'))
    agent_report.report(REPORT_FROM, msg, need='去信箱或平台應徵紀錄看一下送出的內容;這張已記成已送出', job=url, live=board)
    return False, msg


def run_failed(fb, url, rec):
    """這一輪填表、修改沒成:那一頁開著(記到分頁)就是填了卡住,沒開到頁就是沒填成。卡已經不在正在填(他標了外部送出、
    平台對帳找到)就不動。"""
    return ds.try_fire(fb, url, 'fill_bad' if rec.get('tab_id') else 'fill_nopage', apply=rec)


def _drop_old(out, name):
    with contextlib.suppress(OSError):        # 沒有就算了
        os.remove(os.path.join(out, name))


def _fail_record(url, board, stage, msg, ev=None):
    """填表、修改還沒走到驗收就失敗(連不上 Chrome、agent 沒跑成):原因照樣寫在卡上。
    以前只留在「📣 回報」,卡上和「🚀 填表進度」看起來像還沒填。原本的對話、分頁、投遞方式留著。
    ev:這一輪沒跑完時程式自己截的那一頁(截得到才有)。"""
    extra = {'ev': ev} if ev else {}
    bd.set_fb(lambda d: run_failed(d, url, dict(apply_of(d, url), stage=stage, at=now(), issues=[msg], **extra)),
              live=board, by='apply_run')


def _shoot_left(out, stage, door, sid, t0, tab_id=None):
    """這一輪沒跑完:那一頁還開著的話,程式自己截它停下來的樣子,記進這一輪的證據。回看板開的位置(截不到回 None)。"""
    rnd = evidence.active()
    if not (rnd and door):
        return None
    shoot(sid, out, stage, door, follow_claimed(out, 'fill', door, tab_id)[0])
    return _keep_shot(rnd, os.path.join(out, 'fill.png'), t0, '沒跑完')


def _keep_shot(rnd, path, t0, why):
    """這一輪程式自己截的圖記進證據;檔是上一輪留下的(比這一輪開始還舊)不算。回看板開的位置或 None。"""
    try:
        fresh = os.path.getmtime(path) >= t0
    except OSError:
        fresh = False
    if not fresh:
        rnd.note('shot', missing=os.path.basename(path), why=why)
        return None
    rnd.shot(path, why)
    return rnd.last_shot()


STOPPED_SUBMIT = '送出途中停掉了,不確定有沒有送出'


def old_shots(fb):
    """證據夾(#315)之前填的卡:填表截圖只在可投遞夾的 .apply/ 裡,卡上記的是 apply.shot。
    回 {卡: 那張截圖(卡上也有證據夾的截圖了就是 None)}。"""
    return {url: None if m['apply'].get('ev') else os.path.join(cf.HOME, m['apply']['shot'])
            for url, m in fb.items() if isinstance(m, dict) and isinstance(m.get('apply'), dict) and m['apply'].get('shot')}


def keep_old_shots(shots, board):
    """old_shots 找到的截圖複製進那張卡的證據夾(一輪 apply-earlier),回 {卡: 看板開的位置(檔不在了是 None)}。"""
    out = {}
    for url, src in shots.items():
        rnd = evidence.Round('apply', 'earlier', [url], board) if src else None
        out[url] = rnd.last_shot() if rnd and rnd.shot(src, '證據夾之前填表時的截圖').get('file') else None
    return out


def adopt_old_shots(fb, kept):
    """卡上的 apply.shot 換成證據夾的位置(keep_old_shots 的結果);看板只從證據夾開截圖。"""
    for url, ev in kept.items():
        a = (fb.get(url) or {}).get('apply')
        if isinstance(a, dict) and a.pop('shot', None) is not None and ev and not a.get('ev'):
            a['ev'] = ev


def settle(fb, busy):
    """卡停在正在填或改、正在送出,那一輪卻已經不在跑了(被按停止、當掉、伺服器重開):照狀態表收尾。
    正在填 → 填了卡住或沒填成(這一輪沒跑完);正在送出 → 送出結果不明(可能其實送出去了)。
    busy:現在在跑的那一張(None 是沒在跑,'*' 是整批在跑、不知道是哪一張:都不動)。回傳收了幾張。"""
    n = 0
    for u, m in list(fb.items()):
        s = ds.state(m) if isinstance(m, dict) and not u.startswith('__') else None
        if s not in ds.WORKING or busy == '*' or busy == u:
            continue
        if s == 'running':
            stage = apply_of(fb, u).get('stage') if apply_of(fb, u).get('stage') in UNFINISHED else 'fill'
            n += run_failed(fb, u, dict(apply_of(fb, u), at=now(), issues=[UNFINISHED[stage]]))
        else:
            n += ds.try_fire(fb, u, 'submit_unsure', evidence={'at': now(), 'problems': [STOPPED_SUBMIT], 'clicked': None})
    return n




def _run_one(stage, url, board, dry=False, status=None, note='', attachment_download_dir=None):
    jobs, fb = load(board)
    rnd = evidence.active()       # 這一輪的證據(run_one 開的)
    approved_answers = None
    if stage == 'submit':
        problem = fr.approval_problem(fb, url, fr.board_status(board))
        if problem:
            return False, problem
        approved_answers = fr.snapshot(fb, url)
    sid = apply_of(fb, url).get('session') if stage in ('fix', 'submit') else None
    if stage in ('fix', 'submit') and not (sid or apply_of(fb, url).get('workspace')) and not dry:
        return _no_session(url, board, stage)
    import chrome_door
    # agent 的 Chrome 用哪一家的門路:填表是現在用 Chrome 的那一家;修改、送出找這張卡記的那一家(開那一頁的)。
    # 那一家停用、移除,或卡上沒記:接不回來,一步都還沒做(不先記「這一輪還沒跑完」)
    try:
        door = chrome_door.for_card(apply_of(fb, url)) if stage in ('fix', 'submit') else chrome_door.current()
    except chrome_door.Unreachable as gone:
        if not dry:
            return _agent_gone(url, board, gone)
        door = chrome_door.current()
    if door is None and dry:
        print(chrome_door.NO_BROWSER_AGENT)
        return True, 'dry'
    pinned_agent_id = door.agent_id if stage in ('fix', 'submit') and door else None
    resume_sid = sid if door and door.runtime == apply_of(fb, url).get('runtime') else None
    delivery = apply_of(fb, url).get('delivery') or {}
    import profile_sync as ps
    platform_profile = stage == 'submit' and delivery.get('method') == 'platform_profile'
    prepared = None
    preflight_method = None
    if not dry and stage in ('fill', 'fix'):
        # 派 agent 之前當場比一次:卡上選的履歷、語言跟可投遞夾裡的是不是同一份。不靠上一次建置的驗收結果:
        # 他剛換履歷、還沒重建時,那份結果還是舊的,agent 會拿舊夾子把舊檔傳上去(#448)
        unbuilt = ship.chosen_not_built(jobs[url], fb)
        if unbuilt:
            return False, '可投遞夾還沒照卡上的選擇建好,先不填:' + unbuilt
    if not dry and stage in ('fill', 'fix'):
        # 這張重新填、改:它之前那幾則回報講的是舊的那幾輪,收掉;這一輪沒成功會再留一則新的。
        # 以前每跑一次失敗就多一則、舊的不收,同一張卡堆到七則
        agent_report.resolve(url, '這張重新' + ('填' if stage == 'fill' else '改') + '了,舊的回報作廢;結果看卡上',
                             live=board, src=REPORT_FROM)

        # 派出去之前先記「這一輪還沒跑完」:跑完由 fill_record 換掉,沒跑成由 _fail_record 換掉。
        # 被按停止(SIGTERM,程式來不及寫)或當掉時卡上就停在這句,不能還是上一輪的「填好了」、還能確認送出
        # (Codex 重填時,程式已經拿那個分頁重開申請頁了)。對話、分頁、投遞方式留著,叫得回去。
        # 重填是新的一段對話、照現在的履歷填:舊對話填的那一頁正被重開,不能再叫它回來改(下一步是重填);
        # 換履歷、頁面不見的記號由這一輪接手。這一輪沒成就停在卡上等他。
        # 以前記號留著、at 換新,自動流程每看一次就當成新的一次,同一張一輪接一輪重派。
        why = []

        def begin(d):
            try:
                ds.fire(d, url, stage + '_start', apply={'stage': stage, 'at': now(), 'issues': [UNFINISHED[stage]]})
            except ds.Forbidden as e:
                why.append(str(e))
        bd.set_fb(begin, live=board, by='apply_run')
        if why:
            return False, why[0]
    if not dry:
        # agent 只在它專用的 Chrome 動:那一家用那一家的門路確認連上(Codex 看外掛;Claude 等 Claude in Chrome 看得到);只裝其中一家也能用
        up, msg, need = door.ready(board) if door else (False, chrome_door.NO_BROWSER_AGENT, '勾一個「用它操作 ego」的 agent')
        if not up:
            agent_report.report(REPORT_FROM, msg, need=need, job=url, live=board)
            if stage in ('fill', 'fix'):
                _fail_record(url, board, stage, msg)
            return False, msg
        if stage == 'fix':
            try:
                door.resume()
            except (chrome_door.NotNow, chrome_door.Unreachable) as e:
                _fail_record(url, board, stage, str(e))
                return False, str(e)
        if stage == 'fill':
            def opened(binding):
                if not binding.get('workspace'):
                    return
                door.workspace = dict(binding['workspace'])
                bd.set_fb(lambda d: ds.try_fire(d, url, 'tab_handed', apply={
                    'workspace': door.workspace, 'tab_id': binding['tab_id'],
                    'runtime': door.runtime, 'agent_id': door.agent_id,
                }), live=board, by='apply_run')
            try:
                prepared = door.open_for_agent(url, old_tab=apply_of(fb, url).get('tab_id'), on_open=opened)
            except (chrome_door.NotNow, chrome_door.Unreachable) as e:
                _fail_record(url, board, stage, str(e))
                agent_report.report(REPORT_FROM, str(e), need='按修改接回同一頁,或重填', job=url, live=board)
                return False, str(e)
            jobs, fb = load(board)
            decision = ps.decided(jobs[url], fb, url)
            slot = (decision.get('platform'), decision.get('lang'), decision.get('variant'))
            entry = ps.where(*slot) if all(slot) and decision.get('profile_kind') == 'fixed' else None
            import sync_profiles
            # 那一份已是最新就直接填(#397 第 8 條);落後才先確認這張真的用平台履歷,再同步
            if entry and sync_profiles.behind(*slot, entry, board):
                preflight_method, prepared, choice_error = _choose_delivery_before_fill(url, board, door, prepared)
                if choice_error:
                    reason = '填表前不能確認投遞方式：' + choice_error
                    _fail_record(url, board, stage, reason)
                    agent_report.report(REPORT_FROM, reason, need='確認申請頁後重填這張卡',
                                        job=url, url=(prepared or {}).get('page', {}).get('url') or url, live=board)
                    return False, reason
                jobs, fb = load(board)
                if ps.decided(jobs[url], fb, url) != decision:
                    reason = '這張卡選的履歷在確認投遞方式途中改了，請重新填表'
                    _fail_record(url, board, stage, reason)
                    return False, reason
            if entry and preflight_method == 'platform_profile':
                try:
                    synced, problems = sync_profiles.sync_slot(*slot, entry, board, card_url=url)
                except Exception as error:  # noqa: BLE001 — 同步未確認通過，不能繼續填表
                    synced, problems = False, [f'平台履歷同步出錯({type(error).__name__}: {str(error)[:160]})']
                jobs, fb = load(board)
                if ps.decided(jobs[url], fb, url) != decision:
                    synced, problems = False, ['這張卡選的履歷在同步途中改了，請重新填表']
                if rnd:
                    rnd.note('profile_sync', platform=slot[0], lang=slot[1], variant=slot[2],
                             url=entry['read'], passed=synced, problems=problems)
                if not synced:      # 沒同步成的原因 sync_slot 已經回報(附那份平台履歷頁),這裡只記在卡上
                    reason = '填表前平台履歷沒有同步通過：' + '；'.join(problems)
                    _fail_record(url, board, stage, reason)
                    return False, reason
            jobs, fb = load(board)
        if platform_profile:
            bad = _profile_check(url, board, door)
            if bad:
                return _block_profile_submit(url, board, fb, bad)
    p, out, numbered = prompt_for(stage, url, jobs[url], fb, board, note,
                        attachment_download_dir=attachment_download_dir, prepared=prepared, door=door,
                        delivery_method=preflight_method)
    if dry:
        print(p)
        return True, 'dry'
    os.makedirs(out, exist_ok=True)
    if stage == 'submit':
        # 送出前程式自己讀那一頁,跟確認時核對過的樣子比;變了就不送(以前是叫 agent 自己比,#316)
        changed = recheck_page(url, board, '送出前', door=door)
        if changed:
            return _page_changed(url, board, changed, '送出前')
        # 送的是哪一份(語言、版本)派出去之前就記下:送出那幾分鐘他換了履歷、可投遞夾重建,
        # 跑完才讀會記成新的那份,成效統計就對錯版本
        sent_v = _sent_version(url)
        # 派 agent 之前先記「正在送出」:中途被按停止(SIGTERM,程式來不及寫)或當掉時停在這裡,
        # 伺服器發現沒有在跑就改成送出結果不明,要他先確認到底送出沒有(不然可能投兩次)
        why = []

        def start(d):
            # 你已確認 → 正在送出 那一刻再跑一次檢查清單;不過就留在你已確認
            problem = fr.start_submit(d, url, fr.board_status(board))
            if problem:
                why.append(problem)
        bd.set_fb(start, live=board, by='apply_run')
        if why:
            return False, why[0]
    # 上一輪留下的交件檔先刪:這一輪 agent 沒重寫的話,程式不能拿舊的當成這一輪的結果
    _drop_old(out, 'submit.json' if stage == 'submit' else 'fill.json')
    t0 = time.time()
    log = os.path.join(out, stage + '.log')

    outcome = _run_agent(
        p, log, cf.HOME, board, timeout=TIMEOUT, browser_required=True,
        browser=ar.apply_overrides(), resume=resume_sid, agent_id=pinned_agent_id,
        on_start=(lambda proc: status(proc.pid)) if status else None,
        **({'output_last_message': os.path.join(out, 'fill.json')} if stage in ('fill', 'fix') else {}),
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
            output_last_message=os.path.join(out, 'fill.json'),
        )
    if rnd:
        rnd.handoff(os.path.join(out, 'submit.json' if stage == 'submit' else 'fill.json'))
    if not outcome.ok:
        msg = outcome.message()
        if stage == 'submit' and outcome.status == 'unavailable' and getattr(outcome, 'pid', None) is None:
            # agent 的行程根本沒開起來:一步都沒做,不是「送出結果不明」。回到你已確認,再按一次就好
            bd.set_fb(lambda d: ds.try_fire(d, url, 'submit_not_started'), live=board, by='apply_run')
            agent_report.report(REPORT_FROM, '送出沒開始:' + msg, need='這張沒送出;看一下原因,好了再按一次「▶ 送出」',
                                job=url, live=board)
            return False, '沒送出:' + msg
        if stage == 'submit':
            failure = {'at': now(), 'problems': [msg], 'clicked': None,
                       'runner_outcome': outcome.status}
            bd.set_fb(lambda d: ds.try_fire(d, url, 'submit_unsure', evidence=failure), live=board, by='apply_run')
            return False, '送出結果不明:' + msg
        agent_report.report(REPORT_FROM, ('填表' if stage == 'fill' else '修改') + '沒完成:' + msg,
                            need='看「看紀錄」的內容;確認後再重跑', job=url, live=board)
        _fail_record(url, board, stage, msg, ev=_shoot_left(out, stage, door, ar.session_id(log) or sid, t0,
                                                            apply_of(fb, url).get('tab_id')))
        return False, msg
    if stage == 'fill' or not resume_sid:
        sid = ar.session_id(log)
    elif ar.session_id(log) != resume_sid:
        # resume 沒接上同一段對話(codex 找不到那段、或開了新的):那一頁不是它開的,不能算數
        return _no_session(url, board, stage)
    # 真的開那一頁的那一家(填表可能換手到另一家;修改、送出就是卡上記的那一家):之後都找它
    used = (chrome_door.of_agent(outcome.agent_id) if stage == 'fill' and getattr(outcome, 'agent_id', None)
            else None) or door
    used.workspace = getattr(door, 'workspace', None) or apply_of(fb, url).get('workspace')
    record_problem = []
    if stage in ('fill', 'fix'):
        for attempt in range(2):
            try:
                fr.record_fill(os.path.join(out, 'fill.json'), url, live=board)
                break
            except ds.Forbidden as e:
                record_problem = ['表單沒有記進看板:' + str(e)]
                break
            except (ValueError, OSError) as e:
                if attempt:
                    record_problem = ['表單沒有記進看板:' + str(e)]
                    break
                repair_log = os.path.join(out, stage + '-handoff.log')
                repaired = _run_agent(
                    '交件單沒有記進看板:' + str(e) + '\n只修正交件 JSON,不要操作瀏覽器、修改網頁或送出。'
                    '沿用剛才讀回的事實與來源,最後直接回傳完整 JSON 物件,不加 Markdown 或 @@DONE@@。',
                    repair_log, cf.HOME, board, timeout=WRAPUP_TIMEOUT, browser_required=True,
                    browser=ar.apply_overrides(), resume=sid, agent_id=outcome.agent_id,
                    output_last_message=os.path.join(out, 'fill.json'),
                    on_start=(lambda proc: status(proc.pid)) if status else None,
                )
                if rnd:
                    rnd.handoff(os.path.join(out, 'fill.json'))
                if not repaired.ok or ar.session_id(repair_log) != sid:
                    record_problem = ['交件單修正沒有完成:' + repaired.message()]
                    break
    follow_tab, follow_why = ((apply_of(fb, url).get('tab_id'), '') if stage == 'submit' else
                              follow_claimed(out, 'fill', used, apply_of(fb, url).get('tab_id')))
    shoot(sid, out, stage, used, follow_tab)
    jobs, fb = load(board)
    if stage in ('fill', 'fix'):
        bad, res = check_fill(
            fb, url, out, t0, sid, job=jobs[url],
            attachment_download_dir=attachment_download_dir, door=used,
            # 程式自己開好給它的那一頁(做得到的那一家):讀這一頁核對,它說的分頁要是這一個
            tab_id=follow_tab if used.workspace else
                   (prepared or {}).get('tab_id') if stage == 'fill' and used is door else None,
            follow_why=follow_why, numbered=numbered,
        )
        bad = record_problem + bad
        remember_notes(url, res.get('platform_notes'), res.get('platform_notes_remove'))
        if res.get('submit_claimed') and res.get('submitted') is not False:
            res = dict(res, problems=bad)
            if res.get('submitted') is True:
                return _submitted_while_filling(url, board, res, rnd.last_shot() if rnd else None)
            return _unsure_while_filling(url, board, res, rnd.last_shot() if rnd else None)
        if res.get('tab_id'):
            # 先把這一輪填好的頁和它的工作區記到看板上,再做後面的核對:收尾的 chrome_door.close_if_idle() 只留看板上記著的工作區。
            # 也記下是哪一家開的:之後修改、送出叫回同一個 agent
            tab = str(res['tab_id'])
            binding = {'workspace': used.workspace}
            res.update(binding)
            bd.set_fb(lambda d: ds.try_fire(d, url, 'tab_handed', apply=dict(
                binding, tab_id=tab, runtime=used.runtime, agent_id=used.agent_id)),
                      live=board, by='apply_run')
        prev = apply_of(fb, url)
        reported = dict(prev.get('delivery') or {}) if stage == 'fix' else {}
        reported.update(res.get('delivery') or {})
        # 交件單只拿投遞方式(和 agent 新開的客製版網址);用哪一份、固定版還是客製版照程式決定的(#313)
        import profile_sync as ps
        delivery = ps.delivery_for(jobs[url], fb, url, reported)
        if stage == 'fill' and preflight_method and delivery.get('method') != preflight_method:
            bad.append('實際填表投遞方式與填表前核對的方式不同，請重新填表')
        checked_result = dict(res, delivery=delivery) if delivery else res
        history = (checked_result.get('profile') or {}).get('application_history_url')
        if history:
            ps.remember_application_history(ps.profile_key(url), history)
        if delivery.get('method') == 'platform_profile':
            _register_fixed_profile(jobs[url], fb, url, res.get('profile') or {})
            # 程式自己讀申請頁,看選的是不是該選的那一份;選錯就停在這裡,不拿錯的那一份去比附件(#313)
            picked = _picked_problem(url, jobs[url], fb, sid, res, used)
            if picked:
                bad = [picked] + [b for b in bad if b != picked]
            if not bad and stage == 'fill' and delivery.get('profile_kind') == 'custom':
                bad.extend(_check_new_custom_profile(url, board, jobs[url], fb, delivery))
        if not sid:
            bad.insert(0, '沒拿到 agent 那段對話的 id,之後叫不回同一隻 agent')
        if rnd:
            rnd.check(bad, what='填表驗收' if stage == 'fill' else '修改驗收')
        action = None
        if used.workspace:
            used.workspace.pop('handoff_page', None)
            if bad:
                try:
                    action = used.human_action()
                except chrome_door.NotNow as error:
                    bad.append('本人接手頁未核實:' + str(error))
                if action:
                    used.workspace['handoff_page'] = action['page']
                    # 停在要本人登入、驗證的頁:表單還沒開始填,沒填的欄位、沒上傳的檔、讀不到的公司名都是這一件的結果,
                    # 不另外列(列出來他會以為要一條一條處理);agent 自己寫的卡住原因留著
                    bad = [action['need'] + ';按 👀 接手,處理完按「修改」接著填'] + [b for b in bad if gate_cells.kind(b) == 'stuck']
        rec = fill_record(stage, prev, checked_result, bad, sid, outcome.agent_id, note, runtime=used.runtime, ev=rnd.last_shot() if rnd else None,
                          workspace=used.workspace)
        block = blocking(bad, checked_result, action)
        if block:
            rec['block'] = block
        unmet = checked_result.get('unmet') if bad and bad[0].startswith(UNMET_LINE) else None
        if unmet:
            rec['unmet'] = unmet       # 卡上寫因為哪幾句要求、自動做了什麼(#449)

        read = next((p for p in reversed(rnd.pages) if p['why'] == '驗收'), None) if rnd else None
        no_evidence = []

        def mut(d):
            # 要他確認的題目附程式自己截的那一頁;沒有就不叫他確認(#315)
            no_evidence[:] = question_evidence(d, url, read and read['page'],
                                               read and read['shot'] and rnd.rel(read['shot']))
            # 這一輪跑的期間他換了履歷:狀態表讓它跑完直接到「上傳的是舊檔」。卡已經不在正在填(他標了外部送出、
            # 平台對帳找到)就不動
            if not (ds.try_fire(d, url, 'fill_ok', apply=rec) if not bad else run_failed(d, url, rec)):
                return
            if unmet:
                take_unmet(d, url, jobs[url], unmet)
            if not bad:
                # 網頁已經照答案庫重打了:只清核對頁面時(fb)就是這個值的欄位。核對要讀頁、比附件,
                # 這段期間他又改的答案,網頁上還是舊字,標記留著
                checked = {e.get('k'): e.get('v') for e in fb.get('__ans__') or []}
                bank = {e.get('k'): e.get('v') for e in d.get('__ans__') or []}
                for x in ((d.get(url) or {}).get('form') or {}).get('f', []):
                    if x.get('refill') and bank.get(x.get('k')) == checked.get(x.get('k')):
                        del x['refill']
        bd.set_fb(mut, live=board, by='apply_run')
        pt = res.get('posting') or {}
        if stage == 'fill' and pt.get('same_job') is True and pt.get('title'):
            rename(url, pt['title'], pt.get('company') or '', board)
        if not bad:
            later = _profile_check(url, board, used)
            if later:
                bad = later
                bd.set_fb(lambda d: ds.try_fire(d, url, 'check_failed', apply={'issues': later[:10]}),
                          live=board, by='apply_run')
        if not bad:   # 這一輪做成了:這張之前的回報(上一輪的問題)都過時了,收進已處理
            if delivery.get('method') == 'platform_profile' and delivery.get('profile_kind') == 'fixed':
                selected = ps.decided(jobs[url], fb, url)
                ps.attach_sync_visuals(selected['platform'], selected['lang'], selected['variant'], url, board)
            agent_report.resolve(url, ('填好了' if stage == 'fill' else '改好了') + ',之前的問題已經解決', live=board,
                                 src=REPORT_FROM)
        # 這一輪自己的回報放在收舊回報之後開:不然這一輪成功時,剛開的「缺證據」跟著被當成上一輪的收掉,他永遠看不到
        if no_evidence:
            agent_report.report(
                REPORT_FROM, MISSING_EVIDENCE + ':這幾題要你確認,可是沒有程式自己截的那一頁可以對(或那一頁上找不到這一題):'
                + '、'.join(str(q)[:40] for q in no_evidence[:5]),
                need='讓 agent 重填這張;有程式自己截的那一頁,這幾題才會交給你確認', job=url, live=board)
        if bad and not unmet:   # 程式驗出來的問題自己回報,不靠 agent 記得;要求不符的程式自己接著處理(#449)
            agent_report.report(REPORT_FROM, ('填表' if stage == 'fill' else '修改') + '沒完成:\n' + '\n'.join(bad),
                                need=(action['need'] + ';按卡上的 👀 接手,處理完按「修改」接著填' if action else
                                      '依完整問題處理後重跑；重跑填表會照本機把平台附件同步'), job=url, live=board)
        return not bad, gate_cells.Problems(bad) if bad else ('填好了' if stage == 'fill' else '改好了') + ',等你確認送出'
    tab = apply_of(fb, url).get('tab_id')     # 送出成功就從卡上拿掉(那一頁是已收到申請);關分頁要用送出前的
    # 按完送出之後程式自己讀那一頁:還停在申請表就是沒送出,不看 agent 說什麼(#316)
    after, after_why = None, ''
    if sid and tab:
        try:
            after = read_page(lambda: used.read_page(tab), '送出後', shot=os.path.join(out, 'submit.png'))
        except Exception as e:  # noqa: BLE001 — 各家門路丟的例外不一樣;讀不到就照截圖和它說的判斷(判斷不了才是送出結果不明)
            after_why = str(e)[:80]
    ok, res = check_submit(out, t0, apply_of(fb, url).get('tab_url') or url, page=after, page_why=after_why, tab_id=tab,
                           before=apply_of(fb, url).get('seen'))
    shot = _keep_shot(rnd, os.path.join(out, 'submit.png'), t0, '送出後') if rnd else None
    if rnd:
        rnd.check(res.get('problems') or [], what='送出', ok=ok)
    ev = submit_evidence(res, shot)          # 送出結果附程式自己截的那一頁
    changed_questions = []
    if not ok and res.get('not_sent'):
        return _not_sent(url, board, res, ev)

    def mut(fbx):
        if ok:
            current_answers = fr.snapshot(fbx, url)
            changed_questions.extend(
                q for q in sorted(set(approved_answers) | set(current_answers))
                if approved_answers.get(q) != current_answers.get(q)
            )
            if changed_questions:
                ev['not_sent_questions'] = changed_questions[:]
            if ds.try_fire(fbx, url, 'submit_ok', by='agent', sent_at=today(), evidence=ev,
                           ev='送出頁是目前唯一證據;待查信箱與平台應徵紀錄'):
                ship.record_sent(fbx, url, version=sent_v)   # 寄出的是哪一份:只有 ship.record_sent 寫
        else:
            ds.try_fire(fbx, url, 'submit_unsure', evidence=dict(
                ev, problems=res.get('problems') or [], clicked=bool(res.get('clicked') or res.get('submitted'))))
    bd.set_fb(mut, live=board, by='apply_run')
    if not ok:
        return False, '沒送出:' + '; '.join(res.get('problems') or ['沒看到成功頁面'])
    if delivery.get('method') == 'platform_profile':
        decision = ps.decided(jobs[url], fb, url)
        if decision.get('profile_kind') == 'fixed':
            ps.clear_sync_visuals(decision['platform'], decision['lang'], decision['variant'], url, board)
    agent_report.resolve(url, '送出成功,這張結束了', live=board)
    if changed_questions:
        msg = '這幾題改的內容沒有送出去:' + '、'.join(changed_questions)
        agent_report.report(REPORT_FROM, msg, need='這張已照確認頁送出;新答案不在這次送出的內容裡',
                            job=url, live=board)
    # submit_ok 已排入這張卡的工作區;收尾失敗保留在看板,下一輪可重試。
    chrome_door.close_if_idle(board)
    msg = '送出了:' + str(res.get('confirm_text') or res.get('confirm_url'))
    if changed_questions:
        msg += ';這幾題改的內容沒有送出去:' + '、'.join(changed_questions)
    return True, msg


# 程式核對頁面沒過、agent 自己改得了的(答案沒打對、選項沒對上):同一隻 agent 在原頁照問題改一次,不停下來等他按「修改」。
# 只有讀頁核對對不上的那幾種(apply_tab.correctable);要本人做的、agent 自己說卡住的、平台履歷、頁已經不在的不自動改;
# 填表那一輪後最多一次,改完還不對才停下來給他看
SELF_FIX = ('程式讀申請頁發現這幾個問題，照答案庫原文改好；只改下面列的表單欄位，'
            '交件單照頁面上實際的值寫，不要送出：\n')


def _self_fixable(board, url, issues):
    """這一輪的問題(帶種類)是不是 agent 在原頁就改得了的;是就回問題清單,不是回空的。"""
    import apply_tab
    fb = load(board)[1]
    m = fb.get(url) or {}
    a = m.get('apply') or {}
    if (not issues or not ds.allowed(m, 'fix_start') or not a.get('session') or not ds.page_up(m)
            or (a.get('workspace') or {}).get('handoff_page')
            # agent 自己回報了要他處理的事(登入、驗證…):等他處理,不自動改,也不把那則回報收掉
            or any(it.get('job') == url and it.get('agent') for it in agent_report.open_items(fb))
            or not all(apply_tab.correctable(x) for x in issues)):
        return []
    return issues


def run_one(stage, url, board, dry=False, status=None, note=''):
    """跑一張卡的一輪。這一輪的證據(指示、動作紀錄、交件單、讀頁和截圖、比對結果)都記進那張卡的證據夾(evidence)。
    填表核對沒過、全是 agent 改得了的(apply_tab.correctable),接著叫同一隻 agent 在原頁照問題改一次(SELF_FIX)。"""
    ok, msg = _run_one_round(stage, url, board, dry, status, note)
    if dry or ok or stage != 'fill':
        return ok, msg
    issues = _self_fixable(board, url, getattr(msg, 'items', []))
    if not issues:
        return ok, msg
    return _run_one_round('fix', url, board, False, status, SELF_FIX + '\n'.join('- ' + x for x in issues))


def _run_one_round(stage, url, board, dry=False, status=None, note=''):
    if dry:
        return _run_one_with_downloads(stage, url, board, dry, status, note)
    with evidence.opened('apply', stage, [url], board) as rnd:
        try:
            return _run_one_with_downloads(stage, url, board, dry, status, note)
        except Exception as error:  # noqa: BLE001 — 一張卡出錯只算這張失敗:原因記在卡上,批次的下一張照跑
            msg = f'這一輪出錯停下:{type(error).__name__}:{str(error)[:200]}'
            rnd.note('round_error', error=msg)
            if stage in ('fill', 'fix'):
                _fail_record(url, board, stage, msg)
            else:   # 已經在送出就可能按下去了:送出結果不明,原因是這次的錯(還沒開始送出,狀態表不准,卡不動)
                failure = {'at': now(), 'problems': [msg], 'clicked': None}
                bd.set_fb(lambda d: ds.try_fire(d, url, 'submit_unsure', evidence=failure), live=board, by='apply_run')
            return False, msg
        finally:
            # 這一輪留給他的回報附上這一輪程式自己截的那一頁(沒有就照實標沒有);沒有要附的就不寫看板
            try:
                if agent_report.apply_attach(copy.deepcopy(load(board)[1]), url, rnd.started, rnd.last_shot()):
                    bd.set_fb(lambda d: agent_report.apply_attach(d, url, rnd.started, rnd.last_shot()),
                              live=board, by='apply_run')
            except (OSError, ValueError, bd.Tampered) as e:   # 不能蓋掉這一輪本身出錯的原因;照實印出來
                print(f'回報附不上這一輪的截圖:{e}', file=sys.stderr)


def _run_one_with_downloads(stage, url, board, dry=False, status=None, note=''):
    jobs, fb = load(board)
    delivery = apply_of(fb, url).get('delivery') or {}
    needs_download_dir = not dry and (
        stage in ('fill', 'fix')
        or (stage == 'submit' and delivery.get('method') == 'platform_profile')
    )
    if not needs_download_dir:
        return _run_one(stage, url, board, dry, status, note, None)

    downloads = tempfile.mkdtemp(prefix='jobsalvo-profile-attachments-')
    try:
        result = _run_one(stage, url, board, dry, status, note, downloads)
    finally:
        cleanup_problem = _clean_attachment_downloads(downloads)
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
    st = os.path.join(SP, STATUS)
    other = jobrun.read(st, ('apply_run.py', 'job_fake.py'))
    if not a.dry and other.get('running') and other.get('pid') not in (os.getpid(), os.getppid()):
        # 同一時間只跑一輪(進度檔、agent 的瀏覽器都只有一份):看板或自動流程正在跑,這一輪就不開,免得把那一輪撞斷
        print(f"另一輪正在跑({other.get('stage') or ''} {other.get('url') or ''}),等它跑完再開")
        return
    jobs, fb = load(board)
    todo = eligible(jobs, fb, a.stage, a.url, fr.board_status(board))
    if a.limit:
        todo = todo[:a.limit]
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
        import chrome_door                   # 這一批做完:沒有頁面在等他,就把 agent 的 Chrome 整個關掉
        try:
            print(chrome_door.close_if_idle(board))
        except Exception as e:  # noqa: BLE001 — 收尾關 Chrome 失敗照實印進這一輪的紀錄(看板上看得到),不影響已經做完的卡
            print('收工作區時出錯:', e)
    failed = any(not item['ok'] for item in results)
    jobrun.write(st, dict(base, phase='failed' if failed else 'done', done=len(todo),
                          results=results, finished_at=time.time()))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main() or 0)
