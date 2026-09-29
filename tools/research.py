#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
research —— 找缺一輪:找 → 程式清洗 → 判 → 進板。converge.py 照看板上按的那一顆(更深/更廣/指定方向)呼叫這裡。

為什麼拆:一隻 agent 一開頭就讀完整份偏好檔、板上幾百筆清單,再自己找、自己判、自己寫卡,
讀不完,讀完了注意力也被稀釋。所以每一段只拿那一段用得到的東西:

  找    更深:程式用各家徵才系統的資料介面(Lever/Greenhouse/Ashby/104 搜尋)把他喜歡的公司
             現在的開缺全部列出來,照職稱跟他喜歡過的像不像排前面;再派一隻 agent 找同型的缺在別家。
        更廣、指定方向:一隻 agent。
        給 agent 的只有目標與依據(一頁履歷、他的硬規則原文、這輪的方向、以前幾輪往哪找過),
        怎麼找由它決定,只交候選網址。
  清洗  全部程式做:網址是不是單一職缺頁、板上是不是已經有、硬排除與頁面文字擷取。
  判    一批幾張,agent 只讀程式附上的 JD 原文,再對照他對最像的舊卡的表態與原話(prefs.py)+ 硬規則 + 一頁履歷。
        送不送、對味程度、根據他哪幾則,順便把卡片欄位從原文寫出來。
  進板  只把送的那幾張加進看板,記出處(哪一輪、哪種找法、哪個方向、哪個網站、怎麼找到的)與對味程度。
  紀錄  每輪一行寫進 <paths.research>/ledger.jsonl,下一輪找的那隻 agent 看得到以前往哪找過、結果如何。
"""
import os, re, sys, json, time, urllib.parse, contextlib, threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd      # noqa: E402
import config as cf         # noqa: E402
import prefs                # noqa: E402
import card as cards        # noqa: E402

DIR = cf.RESEARCH
LEDGER = os.path.join(DIR, 'ledger.jsonl')
TURNED = os.path.join(DIR, 'judged-no.jsonl')   # 判過、他不會想看的:下一輪別再抓一次、再判一次
PENDING = os.path.join(DIR, 'pending.json')      # 他按停止時已經找到、還沒判的:下一輪一開始先判,不重找
SUMS = cf.SUMS
BATCH = 5
CATS = [c['name'] for c in cf.C['board']['categories']]
MODE_NAME = {'deep': '更深', 'wide': '更廣', 'dir': '指定方向'}





def listing_text(listing):
    return '\n'.join(f"- {r.get('company') or ''}|{r['url']}" for r in listing)


def liked_company_list(cs):
    """他喜歡過的公司:一家一行,附他看過的其中一個職缺網址(agent 從那裡找得到徵才頁)。
    不分那家用哪個徵才系統——以前只認 lever/greenhouse/ashby/104,其餘 19% 整家被跳過,
    agent 連「他喜歡過這家」都不知道。查開缺的方法交給 agent,它自己會用 API。"""
    seen, out = set(), []
    for c in cs:
        if not c['pos']:
            continue
        name = (c['company'] or '').strip()
        key = name.lower() or c['id']
        if key in seen:
            continue
        seen.add(key)
        out.append({'company': name or '(看網址)', 'url': c['id']})
    return out


def research_skill(name):
    """讀使用者指定的做法；空白或不可讀時用產品附的通用 skill。"""
    configured = (cf.C.get('research') or {}).get('skills') or {}
    selected = configured.get(name, '') if isinstance(configured, dict) else ''
    if selected:
        try:
            import settings_api as sa
            path = sa.safe_rel(selected)
            if path:
                with open(path, encoding='utf-8') as f:
                    return f.read()
        except (OSError, TypeError, ValueError, UnicodeError):  # 舊設定的檔案失效時沿用產品預設。
            pass
    path = os.path.join(os.path.dirname(__file__), 'research_skills', cf.RESEARCH_SKILLS[name]['file'])
    with open(path, encoding='utf-8') as f:
        return f.read()


def search_files(rd, cs, listing, ledger_txt):
    """把參考材料寫成檔,prompt 只放指標。它們是 prompt 裡最大的幾塊
    (對上百張舊卡的原話、幾十家公司),但不是每一條路都要讀完;
    正文只留「這一步要做什麼」,要讀多少由 agent 自己決定。原文照抄,不做摘要——摘要可能是錯的。"""
    out = {}
    liked = '\n'.join(f"- {c['title']}" + (f"｜他說「{c['note']}」" if c['note'] else '')
                       for c in cs if c['pos'])   # 完整索引留在檔案裡
    for name, body in (('他喜歡過的職缺.md', liked),
                       ('他喜歡過的公司.md', listing_text(listing)),
                       ('以前幾輪往哪找過.md', ledger_txt)):
        if not body:
            continue
        f = os.path.join(rd, name)
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(body + '\n')
        out[name] = f
    return out


def search_prompt(mode, direction, cs, cats, ledger_txt, out_json, out_md, listing=(), seeds=(),
                  model='', files=None, note_out=None, minutes=0):
    files = files or {}
    parts = []
    if minutes:
        parts += [f'這一輪你最多有 {minutes} 分鐘找;時間到會直接停掉你,已經寫進交件檔的候選照樣會判。'
                  '所以一找到就寫進檔,不要留到最後。\n\n']
    if mode == 'dir' and direction:
        parts += [f'他說:「{direction}」\n\n']
    parts += [research_skill('common'), '\n\n', research_skill(mode), '\n\n']
    if mode == 'deep' and seeds:
        parts += ['他剛剛在看板上指名要找的,這一輪就是為了這幾個:\n',
                  '\n'.join('- ' + s for s in seeds), '\n\n']
    parts += ['他是誰(一頁履歷原文):\n', prefs.resume(),
              '\n\n他自己定的硬規則(使用者自訂區原文):\n', prefs.hard_rules(),
              '\n\n完整偏好筆記在使用者自訂與 Agent 假設兩區,以檔案原文為準。\n\n']
    # 他真的開口說過話的那幾張(原話照抄)。職稱清單不進正文:職稱只是索引,判準是他的話;
    # 標過的卡多半只有職稱,真的寫了話的才是訊號。
    said = '\n'.join(f"- {c['company'] or c['title'][:20]}｜他說「{c['note']}」" for c in cs if c['pos'] and c['note'])
    if said:
        parts += ['他對其中這幾個說過的話(原話,沒有被改寫過):\n', said, '\n\n']
    if files:
        parts += ['其餘材料在這幾個檔,都是原文,要讀哪些、讀多少由你決定:\n']
        for name, f in files.items():
            parts += [f'- {name.replace(".md", "")}:{f}\n']
        parts += ['\n']
    # 整理偏好筆記由另一個 agent 同時做(note_prompt):這一輪整段時間都拿來找
    parts += ['這一輪不用做「1. 整理」,偏好筆記另外有人在整理;直接做「2. 找」。\n\n']
    # 以前要它每張都用瀏覽器打開確認:一頁倒回來三到二十幾萬字,十分鐘裡一半的步數花在這,只交得出幾張。
    # 交件後 clean() 本來就會同時讀每一頁、丟掉下架的,判的 agent 拿到的是整頁原文;這一步不用它再做一次。
    parts += ['不用逐張打開職缺頁確認:交件後程式會同時去讀每一頁,下架的直接丟掉,判的人拿到的是整頁原文。'
              '搜尋結果看得出是單一職缺頁、可能對味就交。瀏覽器留給搜尋結果不夠用的時候'
              '(例如公司的職缺列表要打開才看得到有哪些缺)。\n\n',
              # 以前找缺一輪 72 步,有好幾步在翻工具清單找別的搜尋工具、讀其他 skill 的說明;每一步都要花時間
              '網路搜尋用你手上現成的搜尋工具就好,不用去翻工具清單找別的、也不用讀其他 skill 的說明。'
              '一次送好幾個關鍵字、一次打開好幾頁:每一次呼叫都要花時間,呼叫次數就是這一輪要多久。\n\n',
              '怎麼找由你決定。覺得可能對味的就交出來,不用讀完整 JD、不用寫摘要:後面有人逐張對照他的原話判斷,',
              '板上已經有的、網址不是單一職缺頁的,程式也會擋掉。\n\n',
              f'交件:寫一個 JSON 檔到 {out_json}。每找到一張就把目前找到的全部重寫進這個檔,不要等全部找完才寫'
              '(使用者可能中途按停止,已經寫進檔的會先判、不浪費):\n',
              '[{"url":"單一職缺頁網址","title":"職稱","company":"公司","why":"一句為什麼可能對味",'
              '"via":"在哪裡、怎麼找到的","angle":"在做什麼的角度名稱",'
              '"jd_excerpt":"職缺頁上一句與這個角度相關的原文,最多 240 字"}]\n',
              'JD 摘錄逐字抄你看到的職缺頁或搜尋結果摘要；沒看到原文就留空，不可編造或改寫。\n',
              f'再寫 {out_md}:逐一列出這輪每個角度為何選它,並引用角度地圖或偏好筆記。格式:「- 角度名稱：理由與依據」。\n',
              '同一切入點沿用角度地圖的名稱;只有新切入點才取新名,名字描述「在做什麼」。\n',
              '交付的每張候選都要有一個角度。']
    return ''.join(parts)


def normalize_angle(value, known=()):
    """清掉角度名稱多餘空白;與地圖名稱相同時保留地圖原字。"""
    if not isinstance(value, str):
        return ''
    angle = ' '.join(value.split())
    lookup = {str(x).casefold(): str(x) for x in known if str(x).strip()}
    return lookup.get(angle.casefold(), angle)


def angle_counts(fb, jobs):
    """從看板已有卡片計算每個角度送過、喜歡、不喜歡和未表態數。"""
    out = {}
    for job in jobs:
        src = job.get('src') if isinstance(job.get('src'), dict) else {}
        angle = normalize_angle(src.get('angle'))
        if not angle:
            continue
        row = out.setdefault(angle, {'sent': 0, 'liked': 0, 'disliked': 0, 'unseen': 0})
        row['sent'] += 1
        mark = fb.get(job.get('id'))
        if not isinstance(mark, dict):
            mark = {}
        seen = bool(mark.get('s') or mark.get('app') or mark.get('rm'))
        if mark.get('app') or mark.get('s') in ('like', 'grow'):
            row['liked'] += 1
        if mark.get('s') == 'dislike':
            row['disliked'] += 1
        if not seen:
            row['unseen'] += 1
    return out


def render_angle_map(counts):
    lines = ['# 角度地圖']
    for angle, row in counts.items():
        lines.append(f"- {angle}：送過 {row['sent']}、喜歡 {row['liked']}、"
                     f"不喜歡 {row['disliked']}、還沒看 {row['unseen']}")
    return '\n'.join(lines) + '\n'


def parse_angle_reasons(notes, used_angles):
    """只收 agent 明確交出的角度理由,依候選角度出現順序記錄。"""
    used = list(dict.fromkeys(normalize_angle(x) for x in used_angles if normalize_angle(x)))
    canonical = {x.casefold(): x for x in used}
    found = {}
    for line in str(notes or '').splitlines():
        match = re.match(r'^\s*[-*]\s*(.+?)\s*[：:]\s*(.*?)\s*$', line)
        if not match:
            continue
        angle = canonical.get(normalize_angle(match.group(1)).casefold())
        reason = match.group(2).strip()
        if angle and reason:
            found.setdefault(angle, reason)
    return [{'angle': angle, 'reason': found[angle]} for angle in used if angle in found]


EVIDENCE_BASES = {'JD 原文', '使用者原話', '偏好筆記', '推估'}


def checked_reasons(value, citation_sources=None):
    """保留 judge 理由,並確認引用能在本輪提供的材料中找到。"""
    rows = value if isinstance(value, list) else []
    out = []
    bad = not rows
    citation_sources = citation_sources or {}

    def matches(citation, sources):
        quote = ' '.join(citation.strip('「」『』"“”` ').split())
        return bool(quote) and any(quote in ' '.join(str(source or '').split())
                                   for source in sources)

    for row in rows:
        if not isinstance(row, dict):
            bad = True
            continue
        text = str(row.get('text') or '').strip()
        citation = str(row.get('citation') or '').strip()
        basis = str(row.get('basis') or '').strip()
        prefix = basis + '：'
        quote = citation[len(prefix):].strip() if citation.startswith(prefix) else citation
        if basis == '推估':
            citation_ok = quote == '筆記裡沒有相關的' or matches(quote, sum(citation_sources.values(), []))
        else:
            citation_ok = matches(quote, citation_sources.get(basis, []))
            if quote == '筆記裡沒有相關的':
                citation_ok = True
        if not text or basis not in EVIDENCE_BASES or not citation_ok:
            bad = True
        out.append({'text': text, 'citation': citation, 'basis': basis})
    return out, bad


def missing_feedback_coverage(delta, candidate_note):
    """回傳未在假設出處或待釐清原因中逐張交代的新表態卡片代號。"""
    card_ids = set(re.findall(r'卡片代號=([^\n｜]+)', delta))
    if not card_ids:
        return []
    agent_note = prefs._note_section(candidate_note, prefs.NOTE_AGENT)
    if agent_note is None:
        return sorted(card_ids)
    evidence, marker, tracking = agent_note.partition('### 新表態逐張核對')
    if not marker:
        return sorted(card_ids)

    seen = {}
    covered = set()
    for line in tracking.splitlines():
        match = re.fullmatch(r'- 卡片代號=([^：]+)：(.+)', line.strip())
        if not match:
            continue
        card_id, status = match.groups()
        if card_id not in card_ids:
            continue
        seen[card_id] = seen.get(card_id, 0) + 1
        if status.startswith('來源假設='):
            hypothesis = status[len('來源假設='):].strip()
            if hypothesis and card_id in evidence and hypothesis in evidence:
                covered.add(card_id)
        elif status.startswith('還看不出來=') and status[len('還看不出來='):].strip():
            covered.add(card_id)
    return sorted(card_id for card_id in card_ids
                  if seen.get(card_id) != 1 or card_id not in covered)

def note_prompt(files, note_out):
    """整理偏好筆記的 agent:只做「找缺共通」的「1. 整理」,不上網、不找缺。
    以前跟找缺塞在同一個 agent,它先讀完一堆材料、重寫整份筆記才開始找,真正在找的時間被吃掉一大半(#76)。"""
    # 材料原文直接放進 prompt:以前只給檔名,它 cat 出來的字超過工具輸出上限被截斷,
    # 只好再分段讀好幾次;一輪十步裡一半在重讀,每一步都要重送一次越來越長的對話。
    parts = [research_skill('common'), '\n\n',
             '這一輪你只做上面的「1. 整理」,不要找缺、不要上網。材料原文都在下面,不用再去讀檔。\n\n']
    for name, f in files.items():
        with open(f, encoding='utf-8') as fh:
            parts += [f'=== {name.replace(".md", "")}(原文開始)===\n', fh.read().rstrip('\n'),
                      f'\n=== {name.replace(".md", "")}(原文結束)===\n\n']
    parts += ['\n請把完整新版偏好筆記寫到指定檔案。使用者自訂區必須逐字保留;不要修改原始筆記檔。\n',
              f'更新後偏好筆記請寫到 {note_out}\n',
              '新版筆記的「Agent 假設」區最後附「### 新表態逐張核對」,每張新表態恰好一行：\n',
              '- 卡片代號=<代號>：來源假設=<上方假設名稱>（代號也須出現在該假設的出處）；或\n',
              '- 卡片代號=<代號>：還看不出來=<原因>。\n未列齊就不能推進整理進度。\n']
    return ''.join(parts)


def run_note(files, rd, note_out, run_agent):
    """整理偏好筆記(跟找缺同時跑)。回 None 表示交了;失敗回一句原因,這一輪照樣判,只是筆記不更新。"""
    p = note_prompt(files, note_out)
    with open(os.path.join(rd, 'note.prompt.txt'), 'w', encoding='utf-8') as f:
        f.write(p)
    import agent_run as ar
    try:
        ar.require_success(run_agent(p, os.path.join(rd, 'note.out'), False))
    except Exception as e:
        return str(e)[:200] or type(e).__name__
    return None


def run_search(mode, direction, cs, cats, ledger_txt, rd, browser_required, run_agent,
                listing=(), seeds=(), extra_files=None, note_out=None, known_angles=(), minutes=0):
    """回 (候選, 筆記)。agent 交什麼就是什麼,程式只校驗交件欄位。"""
    oj, om = os.path.join(rd, f'search_{mode}.json'), os.path.join(rd, f'search_{mode}.md')
    files = search_files(rd, cs, listing, ledger_txt)
    files.update(extra_files or {})
    p = search_prompt(mode, direction, cs, cats, ledger_txt, oj, om, listing, seeds, None,
                      files=files, note_out=note_out, minutes=minutes)
    with open(os.path.join(rd, f'search_{mode}.prompt.txt'), 'w', encoding='utf-8') as f:
        f.write(p)
    import agent_run as ar
    ar.require_success(run_agent(p, os.path.join(rd, f'search_{mode}.out'), browser_required))
    return read_search(oj, om, known_angles)


@contextlib.contextmanager
def stop_agent_when(time_up, every=2, spare=lambda: ()):
    """找的那一段:時間到就停掉底下的 agent(跟看板按停止時停 agent 同一招:SIGTERM 子孫行程,主程式留著)。
    agent 被停掉會丟 AgentRunError,呼叫的人看 time_up() 分得出是時間到。
    spare():不受時間限制的 agent 行程(整理偏好筆記那隻),它和它底下的都不停。"""
    done = threading.Event()

    def watch():
        while not done.wait(every):
            if time_up():
                import jobrun, signal
                keep = {p for pid in spare() for p in jobrun.tree(pid)}
                for pid in [p for p in jobrun.tree(os.getpid())[1:] if p not in keep]:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except OSError:   # 已經自己結束了
                        pass
                return
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    try:
        yield
    finally:
        done.set()


def read_search(oj, om, known_angles=()):
    """讀找缺 agent 交的候選(停在半路時也讀得到它已經寫進檔的那幾張)。"""
    try:
        with open(oj, encoding='utf-8') as f:
            arr = json.load(f)
    except Exception:   # 沒寫檔、寫到一半被停掉:當作沒有候選
        arr = []
    notes = ''
    try:
        with open(om, encoding='utf-8') as f:
            notes = f.read().strip()
    except OSError:   # 筆記是找完才寫的,停在半路就沒有
        pass
    out = []
    for x in arr if isinstance(arr, list) else []:
        if isinstance(x, dict) and str(x.get('url', '')).startswith('http'):
            out.append({'url': x['url'].strip(), 'title': str(x.get('title') or ''),
                        'company': str(x.get('company') or ''), 'why': str(x.get('why') or ''),
                        'via': 'agent:' + str(x.get('via') or '')[:120],
                        'jd_excerpt': str(x.get('jd_excerpt') or '').strip()[:240],
                        'angle': normalize_angle(x.get('angle'), known_angles)})
    return out, notes


# ---------------------------------------------------------------- 清洗
BAD_URL = re.compile(r'104\.com\.tw/company/|/jobs/search|[?&]keyword=|[?&]roleJobCat=|[?&]page=\d|/careers/?$|/jobs/?$', re.I)


def pending_take(path=PENDING):
    """上一輪按停止時找到、還沒判的候選;拿出來就從檔案清掉(這輪沒判完的會再存回去)。"""
    try:
        with open(path, encoding='utf-8') as f:
            rows = json.load(f)
        os.remove(path)
    except (OSError, ValueError):   # 沒有(上一輪沒按停止)就是空的
        return []
    return [r for r in rows if isinstance(r, dict) and str(r.get('url', '')).startswith('http')]


def pending_count(path=PENDING):
    try:
        with open(path, encoding='utf-8') as f:
            return len(json.load(f))
    except (OSError, ValueError):   # 沒有待判的
        return 0


def pending_save(rows, path=PENDING):
    seen, out = set(), []
    for r in rows:
        if r.get('url') and r['url'] not in seen:
            seen.add(r['url']); out.append(r)
    if not out:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    return len(out)


def turned_down(path=TURNED):
    """以前判過、判定不送的網址。每 5 張職缺由一隻 agent 判斷,
    同一張被不同輪重複找到就重複付一次,而且結論一定一樣。"""
    out = set()
    try:
        with open(path, encoding='utf-8') as f:
            for l in f:
                try: out.add(json.loads(l)['url'])
                except Exception: pass  # noqa: S110
    except OSError:
        pass
    return out


def turned_add(rows, path=TURNED):
    if not rows: return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


# 幽靈職缺(掛著但沒在真的招人)常見的徵兆是刊登很久、一直重貼。主流說法沒有給固定天數,
# 60 天是本地的門檻(local policy);重貼已經由 add_repost_hints 標出來。
GHOST_DAYS = 60


def _age_days(posted_at):
    import datetime
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(str(posted_at)[:10])).days
    except ValueError:
        return None


def clean(cands, have, exclude_re, turned=(), fetch_page=None, flag_re=None):
    """去重並預抓頁面;只有直連 HTTP 404/410 可直接丟棄。
    flag_re:設定頁「要小心的字」(search.flag_words / flag_title),職稱中了不丟,標出來交給判斷那一段看。"""
    drop = {'不是單一職缺頁': [], '板上已經有': [], '判過不送': [],
            '硬排除': [], '職缺已下架': []}
    seen, keep = set(), []
    for c in cands:
        url = c['url']
        if BAD_URL.search(url):
            drop['不是單一職缺頁'].append(url)
            continue
        if url in have or url in seen:
            drop['板上已經有'].append(url)
            continue
        if url in turned:
            drop['判過不送'].append(url)
            continue
        if exclude_re.search(c.get('title') or ''):
            drop['硬排除'].append(url)
            continue
        hit = flag_re.search(c.get('title') or '') if flag_re else None
        if hit:
            c['flag'] = list(c.get('flag') or []) + [f'職稱有使用者設定「要小心」的字「{hit.group(0)}」:照他的原話判斷,不要只看職稱']
        seen.add(url)
        keep.append(c)

    if fetch_page is None:
        import page_fetch
        fetch_page = page_fetch.fetch
    import page_fetch
    out = []
    for c, fetched in zip(keep, page_fetch.fetch_many([c['url'] for c in keep], fetch_page)):
        if fetched.status == 'closed':
            drop['職缺已下架'].append(c['url'])
            continue
        c['page_status'] = fetched.status
        c['page_via'] = fetched.via
        c['page_http_status'] = fetched.http_status
        c['jd'] = fetched.text if fetched.readable else ''
        c['posted_at'] = fetched.posted_at
        c['posted_src'] = fetched.posted_source
        age = _age_days(fetched.posted_at)
        if age is not None and age > GHOST_DAYS:
            c['flag'] = list(c.get('flag') or []) + [
                f'刊登已經 {age} 天(超過 {GHOST_DAYS} 天):可能是長期掛著、沒在真的招人的幽靈職缺,判斷時一起看'
            ]
        if not fetched.readable:
            c['flag'] = list(c.get('flag') or []) + [
                '程式無法取得職缺頁文字;請依提供的擷取狀態判斷,不要猜測頁面內容'
            ]
            c['page_errors'] = list(fetched.errors)
        out.append(c)
    return out, drop





# ---------------------------------------------------------------- 判
# 更廣找來的是「他沒看過的職能」,定義上就不會像他喜歡過的。拿「像不像舊卡」當判準,
# 等於用一把量不到它的尺去量,再怎麼對味也過不了。
# 這裡只講清楚這張的出身和那把尺不適用,不替他編新的判準——他的口味只能用他自己的話。
WIDE_NOTE = ('【這一批的出身】這幾個是「更廣」找來的:他沒看過這種職能,所以下面附的「他對最像的舊職缺的表態」'
             '多半只是勉強湊出來的鄰居,不是真的同一型。**不要拿「像不像他喜歡過的」當判準**,那把尺量不到這一批。'
             '請照他的一頁履歷(他做得來什麼)、他自己定的硬規則、以及他對「工作性質」講過的原話去判斷:'
             '這個職能他會不會有興趣、值不值得讓他看一眼、給他一個表態的機會。\n\n')


def _card_repost_identity(job):
    title = str(job.get('title') or '').strip()
    company = str(job.get('company') or '').strip()
    if title and company:
        return cards.name_key(company), cards.name_key(title)

    parts = [x.strip() for x in re.split(r'[·・]', cards.name(job)) if x.strip()]
    company = company or prefs.company_segment(job)
    company_key = cards.name_key(company)
    if not company_key:
        return None
    indexes = [i for i, part in enumerate(parts) if cards.name_key(part) == company_key]
    if not indexes:
        return None
    title = ' · '.join(part for i, part in enumerate(parts) if i != indexes[-1])
    title_key = cards.name_key(title)
    return (company_key, title_key) if title_key else None


def add_repost_hints(cands, jobs, fb):
    """把完全相同公司與正規化職稱、不同網址的舊卡附給判斷材料與新卡。"""
    by_identity = {}
    for old in jobs:
        url = str(old.get('id') or '')
        identity = _card_repost_identity(old)
        if url and identity:
            by_identity.setdefault(identity, []).append(old)

    for candidate in cands:
        identity = (cards.name_key(candidate.get('company')),
                    cards.name_key(candidate.get('title')))
        url = str(candidate.get('url') or '')
        matches = []
        if all(identity):
            for old in by_identity.get(identity, []):
                old_url = str(old.get('id') or '')
                if not old_url or old_url == url:
                    continue
                state = fb.get(old_url) if isinstance(fb.get(old_url), dict) else {}
                mark = prefs.MARK.get(state.get('s'), '')
                if state.get('app'):
                    mark = (mark + '、' if mark else '') + '加進準備區'
                if state.get('rm'):
                    mark = (mark + '、' if mark else '') + '已移除'
                matches.append({
                    'url': old_url,
                    'title': cards.name(old),
                    'mark': mark or '未表態',
                    'note': prefs.his_words(state.get('n')),
                })
        if matches:
            candidate['reposts'] = matches
        else:
            candidate.pop('reposts', None)


def judge_prompt(batch, cs, out, mode='', resumes=None):
    resumes = prefs.checked_resumes(resumes)
    if resumes:
        choices = ('【使用者勾選的履歷】每份檔案都可以直接讀取;只從以下履歷中挑,不可挑附件:\n'
                   + prefs.resume_choice_lines(resumes) + '\n\n'
                   '若職缺 JD 的原文語言沒有任何一份履歷檔,不要填履歷、語言或 pick_why。')
        selection_fields = ('"resume":"上面履歷的 id","lang":"JD 原文語言,只能是 ' + '/'.join(cf.LANGS)
                            + '","pick_why":"一句話,為什麼挑這份履歷",')
    else:
        choices = '【履歷選擇】目前沒有已勾選且可挑的履歷;照常判斷職缺,不選履歷、不選語言。'
        selection_fields = ''
    note_text = prefs.without_tracking(prefs.ensure_note())   # 舊筆記裡還留著核對段的,判的時候不給
    parts = [research_skill('judge'), '\n\n', (WIDE_NOTE if mode == 'wide' else ''), choices,
             '\n\n【完整偏好筆記】(使用者自訂與 Agent 假設分開;原話優先於假設)\n',
             note_text, '\n\n']
    cites = {}
    for i, c in enumerate(batch):
        nb = prefs.neighbors(c['title'], c.get('jd') or '', c.get('company') or '', cs)
        cites[f'J{i + 1}'] = {
            'ids': {cid for cid, _ in nb},
            'sources': {
                'JD 原文': [c.get('jd') or '', c.get('jd_excerpt') or ''],
                '使用者原話': [card.get('note') or '' for _, card in nb],
                '偏好筆記': [note_text],
            },
        }
        reposts = []
        for old in c.get('reposts') or []:
            line = f"- {old['title']}（{old['url']}）｜當時表態：{old['mark']}"
            if old.get('note'):
                line += f"｜原話：「{old['note']}」"
            reposts.append(line)
        repost_hint = (
            '程式找到可能是重貼的舊卡（同公司、正規化職稱相同、網址不同）：\n'
            + '\n'.join(reposts)
            + '\n這只是提示；請依實際職缺判斷，不要只因為重貼就排除。\n'
        ) if reposts else ''
        fetched = c.get('jd') if 'jd' in c else c.get('jd_excerpt')
        if c.get('page_status') == 'unknown' or ('jd' in c and not fetched):
            retrieval = (f"程式擷取狀態: unknown; 嘗試路徑: {c.get('page_via') or 'none'}; "
                         f"HTTP 狀態: {c.get('page_http_status') or '無回應'}; "
                         f"錯誤: {'; '.join(c.get('page_errors') or []) or '沒有可讀文字'}\n"
                         '沒有取得職缺頁原文;不可猜測頁面職稱、公司或職缺狀態。\n')
        elif fetched:
            retrieval = (f"程式擷取狀態: ok; 路徑: {c.get('page_via') or '候選摘錄'}; "
                         f"HTTP 狀態: {c.get('page_http_status') or '未提供'}\n"
                         f"以下是程式提供的職缺頁原文:\n--- JD 原文開始 ---\n{fetched}\n--- JD 原文結束 ---\n")
        else:
            retrieval = ('程式沒有取得職缺頁原文;不可猜測頁面職稱、公司或職缺狀態。\n')
        parts += [f"=== J{i + 1} ===\n職缺:{c['title']}·{c.get('company') or ''}\n網址:{c['url']}\n"
                  + retrieval
                  + f"找缺時保留的 JD 摘錄:{c.get('jd_excerpt') or '沒有可核對的摘錄'}\n"
                  + ''.join(f"程式提醒:{x}\n" for x in c.get('flag') or []),
                  repost_hint
                  + '只能依照上方程式提供的職缺頁文字判斷;不可開瀏覽器或自行請求來源網址。'
                  '若頁面文字未取得,不要猜測或依關鍵字判定職缺關閉。\n'
                  f"他對相似舊職缺的表態:\n{prefs.render(nb)}\n\n"]
    parts += [f'把結果寫成一個 JSON 檔到 {out}(只寫這個檔,其他都不要改):\n',
              '[{"id":"J1","title":"頁面上的職稱","company":"頁面上的公司","keep":true,"fit":4,'
              '"cite":["c12"],"why":"一句結論",'
              '"risk":{"kind":"scam / ghost / 空字串","why":"引用 JD 原文或程式提醒;沒有就空字串"},'
              '"reasons":[{"text":"一段理由","citation":"引用偏好筆記條目、舊卡原話或 JD 原文；引用不到寫「筆記裡沒有相關的」",'
              '"basis":"JD 原文/使用者原話/偏好筆記/推估"}],',
              selection_fields,
              '"cat":"' + '/'.join(CATS) + ' 挑一個",',
              '"card":{"fit":"為什麼適合他:他的履歷哪一段對上 JD 哪個需求,1-2 句","co":"公司在做什麼,一句",'
              '"loc":"地點與遠端","deadline":"截止日,沒寫填無","salary":"薪資,沒寫填未公開",'
              '"bar":"門檻(年資/學歷/技能,只記不篩)","posted":"刊登日,沒寫填無",'
              '"ammo":"打法:直投或找誰;不指定附件,附件由程式照勾選組"}}, ...]\n',
              '每段理由都要有引用和根據類型;引用不到時寫「筆記裡沒有相關的」。引用必須能在本輪給你的筆記、相似舊卡原話或 JD 摘錄中逐字找到。\n',
              'keep = 要不要送到他眼前。fit 1-5:5 = 他幾乎一定會喜歡,1 = 他一定不要。',
              # 使用者自己貼進來的(貼網址加入):他已經決定要看這張,判成先不送也要寫摘要,不然卡上一整排「無」
              ('card 一律要寫(這些是使用者自己貼進來的職缺,keep 只是你的建議),' if mode == 'add'
               else 'card 只有 keep 的才要寫,') + '內容照 JD 原文,查不到就寫查無。\n']
    return ''.join(parts), cites


def preview(mode, direction='', live=None, ledger=LEDGER, model='main'):
    """組出「這一種找法現在按下去,會送給 agent 的那一份 prompt」。不跑任何東西、不派 agent。
    prompt 是程式寫的,不必等跑完才知道它拿什麼去問:看板上按鈕底下點開就是這一份。
    mode='judge' 是找回來之後逐張判斷那一段(每批 5 張職缺的 JD 與他對相似舊卡的原話會插在後面)。"""
    import agent_run as ar
    fb, jobs = prefs.load(live or bd.LIVE)
    cs = prefs.cards(fb, jobs)
    if mode == 'judge':
        p = judge_prompt([], cs, '<這一批的結果檔>')[0]
    else:
        p = search_prompt(mode, direction, cs, cat_counts(jobs), ledger_view(fb, ledger),
                          '<這一輪的候選清單檔>', '<這一輪的筆記檔>')
    return ar.rules_for(model, board=live) + p   # 前綴跟真的派出去時走同一支,不各自拼一份


def _launch_all(jobs, browser_required=False):
    """幾批同時派出去;每一批各自依清單換手。"""
    from concurrent.futures import ThreadPoolExecutor
    import agent_run as ar
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(ar.run, p, of, cf.HOME, timeout=4 * 3600,
                               browser_required=browser_required, web=False)
                   for p, of in jobs]
        results = [future.result() for future in futures]
    ar.require_success(results)
    return results


# 頁面沒抓到時 agent 照規矩在職稱、公司欄寫「查無」這類字:這不是名字。公司不能蓋掉找缺時已經知道的;
# 職稱等於沒確認到頁面,跟沒寫一樣不送到他眼前(看板 _EMPTY 同一組)
_PLACEHOLDER = re.compile(r'^(無|未明載|未公開|不公開|未提供|未列|查無|n/a|none|-)([（(].*)?$', re.I)


def judge(cands, cs, rd, browser_required, run_agent, on_batch=None, mode='', par=False,
          launch_all=None, resumes=None, selection_signature=None, finishing=None):
    """用程式提供的 JD 文字判斷;判斷階段固定不開瀏覽器。他按了停止(finishing)就不再派下一批,
    已經判完的照常回傳;被停掉的那一批沒有結果,呼叫的人把它存回待判。"""
    finishing = finishing or (lambda: False)
    import agent_run as ar
    browser_required = False
    resumes = prefs.checked_resumes(resumes)
    signature = selection_signature or prefs.resume_selection_signature(resumes)
    empty_feedback_signature = prefs.feedback_signature('')
    res = {}
    batches = [cands[i:i + BATCH] for i in range(0, len(cands), BATCH)]
    prepared = []
    for bi, b in enumerate(batches):
        out = os.path.join(rd, f'judge_{bi}.json')
        p, cites = judge_prompt(b, cs, out, mode, resumes)
        with open(os.path.join(rd, f'judge_{bi}.prompt.txt'), 'w', encoding='utf-8') as f:
            f.write(p)
        prepared.append((bi, b, out, p, cites))
    if par and len(prepared) > 1:
        if on_batch: on_batch(0, len(batches), par=True)
        outcome = (launch_all or _launch_all)([(p, os.path.join(rd, f'judge_{bi}.out')) for bi, _b, _o, p, _c in prepared], browser_required)
        try:
            ar.require_success(outcome)
        except ar.AgentRunError:
            if not finishing():
                raise                             # 收工時被停掉的那幾批只是沒有結果,判完的照讀
    for bi, b, out, p, cites in prepared:
        if not par:
            if finishing():
                break
            if on_batch: on_batch(bi, len(batches))
            try:
                ar.require_success(run_agent(p, os.path.join(rd, f'judge_{bi}.out'), browser_required))
            except ar.AgentRunError:
                if finishing():
                    break                         # 正在判的這一批被停掉;前面判完的照常回傳
                raise
        try:
            with open(out, encoding='utf-8') as f:
                arr = json.load(f)
        except Exception:
            arr = []
        for x in arr if isinstance(arr, list) else []:
            m = re.match(r'J(\d+)$', str((x or {}).get('id', '')))
            if not m or not (0 < int(m.group(1)) <= len(b)):
                continue
            c = b[int(m.group(1)) - 1]
            title = str(x.get('title') or '').strip()
            company = str(x.get('company') or '').strip()
            title = '' if _PLACEHOLDER.match(title) else title
            company = '' if _PLACEHOLDER.match(company) else company
            if title:
                c['title'] = title
            if company:
                c['company'] = company
            citation_context = cites[f'J{m.group(1)}']
            given = citation_context['ids']
            cite = [str(k) for k in (x.get('cite') or [])]
            try:
                fit = max(1, min(5, int(round(float(x.get('fit') or 0)))))
            except (TypeError, ValueError):
                fit = 0
            resume_id = str(x.get('resume') or x.get('variant') or '').strip()
            lang = str(x.get('lang') or '').strip()
            pick_why = str(x.get('pick_why') or '').strip()
            selection = {
                'selection_signature': signature,
                'feedback_signature': empty_feedback_signature,
            }
            if prefs.valid_resume_pick(resume_id, lang, resumes) and pick_why:
                selection.update(recommend=resume_id, lang=lang, pick_why=pick_why)
            reasons, bad_evidence = checked_reasons(
                x.get('reasons'), citation_context['sources'])
            risk = x.get('risk') if isinstance(x.get('risk'), dict) else {}
            risk = {'kind': str(risk.get('kind') or '').strip(), 'why': str(risk.get('why') or '').strip()[:300]}
            scam = risk['kind'] == 'scam'
            res[c['url']] = {'keep': bool(x.get('keep')) and bool(title) and not scam,   # 詐騙徵兆:一律不送
                             'fit': fit, 'why': (f"疑似詐騙:{risk['why']}" if scam else str(x.get('why') or '')),
                             'risk': risk if risk['kind'] in ('scam', 'ghost') else None,
                             'cite': [k for k in cite if k in given], 'bad_cite': [k for k in cite if k not in given],
                             'reasons': reasons, 'bad_evidence': bad_evidence,
                             'cat': x.get('cat') if x.get('cat') in CATS else '其他',
                             'readable': bool(title) and c.get('page_status') != 'unknown',
                             'card': x.get('card') if isinstance(x.get('card'), dict) else {},
                             'resume': selection}
    return res


# ---------------------------------------------------------------- 進板
CARD_KEYS = ('fit', 'co', 'loc', 'deadline', 'salary', 'bar', 'posted', 'ammo')


def job_entry(c, r, src):
    card = {k: str((r.get('card') or {}).get(k) or '無') for k in CARD_KEYS}
    t = cards.name(c['title'], c.get('company'))
    source = dict(src, site=urllib.parse.urlparse(c['url']).netloc.replace('www.', ''),
                  how=c.get('via', ''), fit=r['fit'], why=r['why'])
    angle = normalize_angle(c.get('angle'))
    if angle:
        source['angle'] = angle
    if r.get('reasons'):
        source['reasons'] = r['reasons']
    entry = {'id': c['url'], 'cat': r['cat'], 'target': t, 'chan': '官方', 'ammo': card['ammo'], 'note': card['fit'],
              'bk': False, 'dead': False, 'added': time.strftime('%Y-%m-%d'), 'sum': card, 'src': source}
    if c.get('reposts'):
        entry['src']['reposts'] = [dict(old) for old in c['reposts']]
    if (r.get('risk') or {}).get('kind') == 'ghost':
        entry['src']['risk'] = dict(r['risk'])
    if isinstance(r.get('resume'), dict):
        entry['resume'] = dict(r['resume'])
    if c.get('posted_at'):
        entry['posted_at'] = c['posted_at']
        if c.get('posted_src'):
            entry['posted_src'] = c['posted_src']
    return entry


def add_entries(entries, live=bd.LIVE, sums=SUMS):
    """把新職缺加進現行看板(已經有的不動),順便寫 card-summaries。回新增幾筆。"""
    for e in entries:
        os.makedirs(sums, exist_ok=True)
        with open(os.path.join(sums, cards.card_id_from_url(e['id']) + '.json'), 'w', encoding='utf-8') as f:
            json.dump(e['sum'], f, ensure_ascii=False, indent=1)
    n = [0]

    def mut(data, fb):
        have = {j.get('id') for j in data['jobs']}
        for e in entries:
            if e['id'] not in have:
                data['jobs'].append(e); have.add(e['id']); n[0] += 1
    with open(os.path.join(cf.HOME, '.reconcile.lock'), 'w') as lk:   # 跟 reconcile 同一把(見 board_doc.add_jobs)
        import fcntl
        fcntl.flock(lk, fcntl.LOCK_EX)
        bd.set_data(mut, live=live)
    return n[0]


# ---------------------------------------------------------------- 紀錄
def ledger_add(rec, path=LEDGER):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def ledger_view(fb, path=LEDGER, n=8):
    """給下一輪找的那隻 agent 看:最近幾輪往哪找、送進板幾張、他後來怎麼表態。"""
    try:
        with open(path, encoding='utf-8') as f:
            recs = [json.loads(l) for l in f if l.strip()]
    except OSError:
        return ''
    mute = set(((fb.get('__research__') or {}).get('mute')) or [])
    L = []
    for r in recs[-n:]:
        seen = like = dis = 0
        for u in r.get('added', []):
            v = fb.get(u) or {}
            if v.get('s') or v.get('app') or v.get('rm'):
                seen += 1
            if v.get('app') or v.get('s') in ('like', 'grow'):
                like += 1
            elif v.get('s') == 'dislike':
                dis += 1
        what = MODE_NAME.get(r.get('mode'), r.get('mode')) + (f"「{r['direction']}」" if r.get('direction') else '')
        L.append(f"- {r.get('round')} {what}:判了 {r.get('judged', 0)} 張、送進板 {len(r.get('added', []))} 張;"
                 f"他看過 {seen}、喜歡 {like}、不喜歡 {dis}。" +
                 (f"\n  那輪自己寫的:{r['notes'][:400]}" if r.get('notes') else ''))
    if mute:
        L.append('他說不用再往這幾個方向找:' + '、'.join(sorted(mute)))
    return '\n'.join(L)


def _report(msg, need, live):
    """程式自己驗出、要他知道的事,寫進看板最上面的「📣 agent 回報」(見 agent_report)。"""
    try:
        import agent_report
        agent_report.report('找新職缺', msg, need=need, live=live)
    except Exception:  # noqa: S110
        pass


# ---------------------------------------------------------------- 一輪
def cat_counts(jobs):
    c = {}
    for j in jobs:
        k = (j.get('cat') or '其他').strip(); c[k] = c.get(k, 0) + 1
    return sorted(c.items(), key=lambda kv: -kv[1])


def run(mode, direction, live=bd.LIVE, browser_required=True, st=None, run_agent=None,
        ledger=LEDGER, sums=SUMS, seeds=None, turned=TURNED, limit=0, finishing=None, pending=PENDING,
        minutes=0, time_up=None, tally=None):
    """跑一輪。回新增幾筆。st(phase, **kw) 回報進度;run_agent(prompt, outfile, browser_required) 派 agent。
    finishing() 為真 = 他按了停止:停在找 → 已經寫進檔的候選存起來,下一輪先判;停在判 → 判完的照常進板,
    沒判到的存起來。
    time_up() 為真 = 找缺時間上限到了(只限「找」那一段):停掉 agent,已經寫進檔的候選這一輪就清洗、判斷、進板。
    tally:{'found','dropped'} 累加這輪找到、清洗掉幾張,converge 跑完照實講。"""
    started = time.strftime('%Y-%m-%dT%H:%M:%S')
    finishing = finishing or (lambda: False)
    time_up = time_up or (lambda: False)
    cut = False                   # 時間到停在找:偏好筆記沒交齊,這輪不更新
    import converge as cv       # 硬排除的唯一真相在那邊(EXCLUDE_TITLE)
    import agent_run as ar
    import feedback_dump
    note_pids = set()        # 整理偏好筆記那隻:時間限制只管「找」,不停它
    if run_agent is None:
        # required 是「這一步要上網」:能開瀏覽器的 agent 先用;只會上網搜尋的也可以,不用非瀏覽器不可
        run_agent = lambda p, of, required: ar.run(
            p, of, cf.HOME, prefer_browser=required, web=required, board=live,
        )
        note_agent = lambda p, of, required: ar.run(
            p, of, cf.HOME, web=False, board=live, on_start=lambda proc: note_pids.add(proc.pid),
        )
    else:
        note_agent = run_agent
    st = st or (lambda *a, **k: None)
    t0 = time.time()
    rd = os.path.join(DIR, 'rounds', time.strftime('%Y%m%d-%H%M%S')); os.makedirs(rd, exist_ok=True)
    prefs.ensure_note(legacy_path=cf.PREFS)
    fb, jobs = prefs.load(live)
    have = {j['id'] for j in jobs}
    cs = prefs.cards(fb, jobs)
    counts = angle_counts(fb, jobs)
    blocked = set(fb.get('__block__') or [])
    delta_path = os.path.join(rd, '新表態.md')
    angle_path = os.path.join(rd, '角度地圖.md')
    note_out = os.path.join(rd, '新版偏好筆記.md')
    delta, journal_lines = feedback_dump.feedback_delta(live, prefs.PREF)
    with open(delta_path, 'w', encoding='utf-8') as f:
        f.write(delta)
    with open(angle_path, 'w', encoding='utf-8') as f:
        f.write(render_angle_map(counts))
    extra_files = {'偏好筆記.md': prefs.PREF, '新表態.md': delta_path, '角度地圖.md': angle_path}
    # 他在看板上指名的種子:{'co':[公司名], 'job':[職缺網址]}.走的還是「更深」那一條,
    # 只是範圍由他指定:公司種子把程式列開缺的範圍縮到那幾家,職缺種子寫進 prompt 當方向。
    seeds = seeds or {}
    seed_co = [c for c in (seeds.get('co') or []) if c]
    byid = {j['id']: j for j in jobs}
    seed_txt = ([f'這家公司的其他缺:{c}' for c in seed_co] +
                [f"跟這張同型的:{cards.name(byid.get(u) or {'target': u})}({u})"
                 for u in (seeds.get('job') or []) if u])
    cands, notes = pending_take(pending), ''      # 上一輪按停止時找到、還沒判的,先判
    carried = len(cands)
    if mode == 'deep':
        st('agent', which='deep', step='整理他喜歡過的公司')
        listing = liked_company_list(cs)
        if seed_co:
            # 種子存的是看板上的公司名;兩邊都用 card.company 那條規則,舊種子可能還帶法律字尾,去完再比。
            listing = [r for r in listing if any(cards.same_company(r['company'], c) for c in seed_co)]
    else:
        listing = []
    st('agent', which=mode, step='agent 在找')
    # 整理偏好筆記跟找缺同時跑(沒有新表態就不用整理);判之前等它交件,判的時候用新筆記
    note_box = {}
    note_thread = None
    if delta.strip():
        note_thread = threading.Thread(
            target=lambda: note_box.__setitem__('error', run_note(
                {k: extra_files[k] for k in ('偏好筆記.md', '新表態.md')}, rd, note_out, note_agent)),
            daemon=True)
        note_thread.start()

    def note_done():
        if note_thread is not None:
            note_thread.join()
        return note_thread is not None and note_box.get('error') is None
    def finish_note():
        if note_done():
            # 筆記有問題只回報、這輪不更新,找到的照樣判(以前跟找缺綁在一起,筆記沒交齊整輪就失敗)
            try:
                with open(note_out, encoding='utf-8') as f:
                    candidate_note = f.read()
                missing_feedback = missing_feedback_coverage(delta, candidate_note)
                if missing_feedback:
                    raise ValueError(f'新版偏好筆記有 {len(missing_feedback)} 張新表態未交代')
                changed = prefs.apply_agent_note(candidate_note, prefs.PREF)
            except (OSError, ValueError) as e:
                _report(f'這一輪偏好筆記沒更新:{str(e)[:160]}', '下一輪會再整理一次;檢查這一輪找缺紀錄', live)
            else:
                if changed:
                    _report('Agent 嘗試修改使用者自訂偏好，程式已還原並回報',
                            '檢查偏好筆記與這一輪紀錄', live)
                feedback_dump.save_feedback_checkpoint(journal_lines, prefs.PREF)
        elif note_thread is not None:
            _report(f'這一輪偏好筆記沒更新:{note_box.get("error")}', '下一輪會再整理一次;檢查這一輪找缺紀錄', live)

    try:
        try:
            with stop_agent_when(time_up, spare=lambda: list(note_pids)):
                got, notes = run_search(mode, direction, cs, cat_counts(jobs), ledger_view(fb, ledger), rd,
                                        browser_required, run_agent, listing, seed_txt,
                                        # 新表態是整理筆記那隻的材料(每張附整份 JD,幾萬字);找的這隻用不到
                                        extra_files={k: extra_files[k] for k in ('偏好筆記.md', '角度地圖.md')},
                                        known_angles=counts, minutes=minutes)
        except ar.AgentRunError:
            if time_up() and not finishing():
                cut = True
                got, notes = read_search(os.path.join(rd, f'search_{mode}.json'),
                                         os.path.join(rd, f'search_{mode}.md'), counts)
                st('agent', which=mode, step=f'時間到,停止找新的,正在判找到的 {len(got)} 張')
            elif not finishing():
                raise
            else:
                # 他按了停止、停在找:agent 已經寫進檔的候選不丟,存起來下一輪先判;偏好筆記這輪不更新
                note_done()
                got, _ = read_search(os.path.join(rd, f'search_{mode}.json'), '', counts)
                n = pending_save(cands + [c for c in got if c.get('angle')], pending)
                ledger_add({'round': time.strftime('%Y-%m-%d %H:%M', time.localtime(t0)), 'mode': mode,
                            'direction': direction, 't0': t0, 't1': time.time(), 'stopped': 'search',
                            'pending': n, 'judged': 0, 'kept': 0, 'added': [], 'dir': rd}, ledger)
                st('agent', which=mode, step=f'你按了停止:找到的 {n} 張存起來,下一輪先判', pending=n)
                return 0
        missing_angle = [c for c in got if not c.get('angle')]
        if missing_angle:
            _report(f'有 {len(missing_angle)} 張候選缺少找缺角度，已略過',
                    '檢查這一輪找缺紀錄', live)
        got = [c for c in got if c.get('angle')]
        used_angles = list(dict.fromkeys(c['angle'] for c in got))
        angle_reasons = parse_angle_reasons(notes, used_angles)
        reasoned = {row['angle'] for row in angle_reasons}
        missing_reasons = [angle for angle in used_angles if angle not in reasoned]
        if missing_reasons and not cut:      # 角度理由是找完才寫的,時間到停在半路本來就沒有
            _report(f'有 {len(missing_reasons)} 個找缺角度沒有交理由:' + '、'.join(missing_reasons[:3]),
                    '檢查這一輪找缺紀錄', live)

        cands += got
        cands = [c for c in cands if not (c.get('company')
                                          and any(cards.same_company(c['company'], b) for b in blocked))]
        st('fold', step='清洗:去重、硬排除、抓取職缺頁文字')
        ok, drop = clean(cands, have, cv.EXCLUDE_TITLE, turned_down(turned), flag_re=cv.FLAG_TITLE)
        if tally is not None:
            tally['found'] = tally.get('found', 0) + len(cands)
            tally['dropped'] = tally.get('dropped', 0) + sum(len(v) for v in drop.values())
        if limit:
            ok = ok[:limit]                   # 他在看板上選的「最多判幾張」
        add_repost_hints(ok, jobs, fb)
        par = bool(fb.get('__agentfree__'))     # 他撥了「愛派幾隻就派幾隻」,判斷那幾批就同時跑
        res = judge(ok, cs, rd, False, run_agent, mode=mode, par=par, finishing=finishing,
                    on_batch=lambda i, n, par=False: st('judge', n=n, done=i,
                        step=(f'逐張判斷 {n} 批同時跑' if par else f'逐張判斷 {i + 1}/{n} 批'))) if ok else {}
        left = pending_save([c for c in ok if c['url'] not in res], pending) if finishing() else 0
        add_repost_hints(ok, jobs, fb)
        unreadable = [c['url'] for c in ok if not res.get(c['url'], {}).get('readable')]
        if unreadable:
            _report(f'有 {len(unreadable)} 個職缺頁沒有可確認的頁面職稱:' + '、'.join(unreadable[:3]),
                    '確認職缺連結與 agent 瀏覽器登入狀態後再重跑', live)
        bad_evidence = [c['url'] for c in ok if res.get(c['url'], {}).get('bad_evidence')]
        if bad_evidence:
            _report(f'有 {len(bad_evidence)} 個職缺的判斷理由缺少引用或根據類型:'
                    + '、'.join(bad_evidence[:3]),
                    '檢查這一輪找缺紀錄', live)
    except (ar.AgentRunError, ValueError) as e:
        finish_note()                                 # 整理筆記的 agent 不能丟著不管;它交得出來就照樣套用
        pending_save(cands[:carried], pending)        # 帶進來還沒判的放回去,下一輪再判
        msg = f'這一輪 agent 沒有完成:{e}'
        _report(msg, '在「🔎 找新職缺」按「看紀錄」確認後再重跑', live)
        st('failed', step='agent', msg=msg, finished_at=time.time())
        return 0
    # 偏好筆記:判的時候用現有的(不等它);判完才等它交件、套用,下一輪就用新的
    if note_thread is not None:
        if note_thread.is_alive():
            st('agent', which=mode, step='等偏好筆記整理完')
        finish_note()
    src = {'round': time.strftime('%Y-%m-%d %H:%M', time.localtime(t0)), 'mode': mode, 'via': 'research'}
    if direction:
        src['direction'] = direction
    keep = [job_entry(c, res[c['url']], src) for c in ok if res.get(c['url'], {}).get('keep')]
    # 判過、判定不送的記下來:下一輪找到同一張就在清洗那關擋掉,不再花一批判斷
    turned_add([{'url': c['url'], 'at': src['round'], 'mode': mode,
                 'why': (res.get(c['url']) or {}).get('why', '')[:200]}
                for c in ok if res.get(c['url'], {}).get('readable')
                and not res.get(c['url'], {}).get('keep')], turned)
    added = add_entries(keep, live, sums) if keep else 0
    rec = {'round': src['round'], 'mode': mode, 'direction': direction, 't0': t0, 't1': time.time(),
           'candidates': len(cands), 'dropped': {k: len(v) for k, v in drop.items()}, 'judged': len(res),
           'kept': len(keep), 'added': [e['id'] for e in keep],
           'bad_cite': sum(len(r['bad_cite']) for r in res.values()),
           'bad_evidence': sum(bool(r.get('bad_evidence')) for r in res.values()),
           'stopped': 'time' if cut else ('judge' if finishing() else ''), 'pending': left, 'carried': carried,
           'angle_reasons': angle_reasons, 'notes': notes[:1500], 'dir': rd}
    ledger_add(rec, ledger)
    with open(os.path.join(rd, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    # 看板上「最近幾輪」讀這個:往哪找、判幾張、送幾張、那輪 agent 自己寫的方向(最近 20 輪)
    brief = {k: rec[k] for k in ('round', 'mode', 'direction', 'judged', 'kept', 'added', 'dropped')}
    brief['notes'] = notes[:600]
    bd.set_data(lambda data, fb: data.__setitem__('research', (data.get('research') or [])[-19:] + [brief]), live=live)
    prefs.refresh_like(live)      # 新缺進來了,「最可能喜歡」的排序分數跟著重算
    try:                          # 這一輪跑完了:上一輪留下的「找新職缺」回報收掉(這一輪自己報的留著)
        import agent_report
        agent_report.resolve_from('找新職缺', '後來那一輪找新職缺跑完了', started, live=live)
    except Exception:  # noqa: S110
        pass
    return added
