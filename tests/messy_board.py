"""四件網站任務在本機假網站跑到最亂(#783):真瀏覽器(ego)讀 127.0.0.1 的假網站,agent 照劇本走。

讀頁、截圖、讀網址都是正式程式的真瀏覽器那一側;只有 agent 換成 fake_browser 的劇本(開頁、叫拍照、寫交件單)。
跑完把家目錄留著,用 board_server 開來截圖:

    JOBSALVO_EGO_INTEGRATION=1 uv run python tests/messy_board.py <資料副本家目錄> <輸出資料夾>

MESSY_ROUNDS=3 讓每件在同一個假網站上連跑三輪(網址不變;連續 3 輪沒做完才開「要你做」)。

資料副本家目錄是看板資料的一份副本(會再複製一次,原本那份不動);輸出資料夾底下 home/ 是跑完的家、env.json 是開看板要的環境變數。
不連真實求職網站:瀏覽器邊界只准 127.0.0.1。
"""
import json
import os
import shutil
import sys
import urllib.parse

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)


def _prepare(source, out):
    home = os.path.join(out, 'home')
    if os.path.isdir(out):
        shutil.rmtree(out)
    shutil.copytree(source, home, symlinks=True)
    # 副本的暫存資料夾改到輸出資料夾底下:不跟真的看板共用(設定裡常是 /tmp),
    # 開這個家的看板也從這裡讀填表、查應徵進度、找缺、貼網址的進度(board_server.run_sp)
    board_tmp = os.path.join(out, 'tmp', 'board')
    settings = os.path.join(home, 'jobsalvo.json')
    with open(settings, encoding='utf-8') as f:
        config = json.load(f)
    config.setdefault('paths', {})['tmp'] = board_tmp
    with open(settings, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    env = {'JOBSALVO_HOME': home, 'JOBSALVO_TEST_HOME': home, 'JOBSALVO_EGO_INTEGRATION': '1',
           'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '1',
           **{name: os.path.join(out, 'tmp', name.lower()) for name in ('EVIDENCE_TMP', 'SYNC_TMP')},
           **{name: board_tmp for name in ('APPLY_TMP', 'REPLY_TMP', 'CONVERGE_TMP', 'ADD_TMP')}}
    os.environ.update(env)
    with open(os.path.join(out, 'env.json'), 'w', encoding='utf-8') as target:
        json.dump(env, target, ensure_ascii=False, indent=2)
    return env


ENV = _prepare(os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])) if __name__ == '__main__' else {}

sys.path[:0] = [TESTS, os.path.join(ROOT, 'tools')]
import _env  # noqa: E402
import chrome_door  # noqa: E402
chrome_door.ego_bin = _env.REAL_EGO_BIN
os.environ.update(ENV)      # _env 把證據夾改到測試家底下;換回這一輪的

import acceptance_inputs as I  # noqa: E402
import config as cf  # noqa: E402
from fake_browser import FakeBrowser, Script  # noqa: E402
from web.outside import Real  # noqa: E402
from website_testbench import WebsiteTestBench  # noqa: E402

cf.reload(os.environ['JOBSALVO_HOME'])


def html(title, parts):
    body = ''.join(f'<p>{part}</p>' for part in parts)
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head><body>{body}</body></html>'


def loopback(url):
    host = urllib.parse.urlsplit(str(url)).hostname
    if host != '127.0.0.1':
        raise RuntimeError(f'假網站之外的網址不准開:{url}')
    return url


class Page:
    """劇本裡的一頁:key 是同一個分頁(同 key 換 path = 同一分頁換網址),state 照 acceptance_inputs 的頁面狀態。"""

    def __init__(self, key, path, parts=(), state=I.READ, title=''):
        self.key, self.path, self.parts, self.state, self.title = key, path, tuple(parts), state, title or path


def close_page(bench, page_id):
    label = json.dumps(bench._page_labels[page_id])
    chrome_door._ego(bench.browser.door._task(page=False) + f'await task.page({label}).close();\nreturn 1;')
    bench._closed_pages.add(page_id)
    bench._page_labels.pop(page_id)
    bench._page_urls.pop(page_id)


class BenchBrowser(FakeBrowser):
    """瀏覽器邊界:讀頁、截圖、讀網址、申請表走真瀏覽器讀假網站;agent 走 FakeBrowser 的劇本。"""

    def __init__(self, bench, *scripts, **kwargs):
        self.bench = bench
        self.keys = {}          # 劇本的 key → 測試台的 page_id
        self.unread = False
        super().__init__(*scripts, **kwargs)
        door = bench.browser.door
        self.real = Real(door, validate_url=loopback)

    # 劇本換開著的頁:FakeBrowser.run_agent 指定 self.tabs
    @property
    def tabs(self):
        return None if self.unread else tuple(self.keys)

    @tabs.setter
    def tabs(self, pages):
        if not hasattr(self, 'bench'):
            return
        self.unread = pages is None
        pages = pages or ()
        wanted = {page.key for page in pages}
        for key in [key for key in self.keys if key not in wanted]:
            self._close(self.keys.pop(key))
        self.bench._unreadable_pages.clear()
        for page in pages:
            parts = () if page.state == I.BLANK else (('很長的一段字' * 20000,) if page.state == I.TOO_LONG else page.parts)
            self.bench.server._pages[page.path] = getattr(page, 'form', '') or html(page.title, parts)
            if page.key in self.keys:
                page_id = self.keys[page.key]
                self.bench.reload_page(page_id, self.bench.server.url(page.path))
                self.bench._page_urls[page_id] = self.bench.server.url(page.path)
            else:
                self.keys[page.key] = self.bench.open_page(page.path)
            if page.state == I.UNREADABLE:
                self.bench._unreadable_pages[self.keys[page.key]] = '讀頁的程式在這一頁出錯'

    def _close(self, page_id):
        close_page(self.bench, page_id)

    def tab_of(self, key):
        return self.bench.agent_page_id(self.keys[key])

    def raw_tabs(self):
        self.reads += 1
        if self.unread:
            raise RuntimeError('ego 回:工作區讀不到')
        return self.bench.read_open_pages()

    def raw_shot(self, tab_id, path, quote=''):
        return self.real.raw_shot(tab_id, path, quote)

    def raw_shot_url(self, url, path, quote=''):
        return self.real.raw_shot_url(loopback(url), path, quote)

    def raw_fetch(self, url):
        self.fetches.append(url)
        return self.real.raw_fetch(loopback(url))

    def raw_form(self, tab_id):
        return self.real.raw_form(tab_id)

    def fill_form(self, tab_id, items):
        self.filled.append((tab_id, [dict(item) for item in items]))
        return self.real.fill_form(tab_id, items)

    def open_workspace(self):
        space = self.bench.browser.door.workspace
        return {'id': int(space['id']), 'name': space['name']}

    def hand_to_user(self, tab_id):
        """不真的叫 ego lite 到前面(會搶使用者的畫面);照 ego 交接的回法回分頁標籤。"""
        if not any(self.tab_of(key) == tab_id for key in self.keys):
            raise RuntimeError(f'工作區沒有分頁 {tab_id}')
        self.handed.append(tab_id)
        return tab_id.split(':', 1)[1]


# ---- 同步 ----

def run_sync(bench):
    import profile_sync
    import sync
    import sync_material
    platform, lang, variant = '104.com.tw', 'zh', 'nocex'
    slot = sync.slots(platform, lang, variant)[0]
    source = sync_material.source(slot, cf.LIVE)
    intents = sync.intended(slot, source)
    afters = [row.after for row in intents if row.after]
    befores = [row.before for row in intents if row.before]
    first = afters[:3]
    verbatim, other = first[:1], [text.replace('，', ',').replace('。', '.') for text in first[1:]]
    profile = Page('profile', '/profile', ['我的履歷', *befores[:4], '自傳'], title='平台履歷')
    edited = Page('profile', '/profile', ['我的履歷', *verbatim, *other, *befores[2:4], '自傳'], title='平台履歷')
    moved = Page('profile', '/profile?saved=1', edited.parts, title='平台履歷(已儲存)')

    def sheet(printed):
        return {'changes': [{'item': '經歷', 'before': befores[0] if befores else '', 'after': first[0] if first else ''},
                            {'item': '自傳', 'before': '', 'after': '寫了一段新的自傳'}],
                'rewrites': [{'original': afters[-1] if afters else '', 'rewritten': '平台限 100 字,縮短後的版本',
                              'why': '平台欄位有字數上限'}],
                'registered': '', 'saved': '按了儲存,頁面跳到已儲存',
                'needs_user': ['平台要求重新驗證手機', {'欄位': '學歷', '原因': '下拉選單沒有我的學校'}],
                'needs_user_tab': browser.tab_of('verify'), 'needs_user_quote': '請輸入手機收到的驗證碼',
                'stopped': '學歷那一欄選不到,停下'}

    pages_before = (profile, Page('long', '/edu', state=I.TOO_LONG, title='學歷'),
                    Page('blank', '/blank', state=I.BLANK, title='空白'),
                    Page('broken', '/skills', ['技能'], state=I.UNREADABLE, title='技能'))
    pages_after = (moved, Page('long', '/edu', state=I.TOO_LONG, title='學歷'),
                   Page('verify', '/verify', ['手機驗證', '請輸入手機收到的驗證碼 '], title='手機驗證'),
                   Page('dup', '/profile', edited.parts, title='平台履歷'))
    browser = BenchBrowser(bench, Script((('open', (profile,)), ('before', pages_before), ('after', pages_after)),
                                         sheet=sheet))
    profile_sync.remember(platform, lang, variant, bench.server.url('/profile'))
    code = sync.main(['--board', cf.LIVE, '--platform', platform, '--lang', lang, '--variant', variant], browser=browser)
    return code


def form_html(title, company, rows, extra=()):
    """假申請表:職缺名稱、公司、每一格(題目, 值)、其他段落。"""
    fields = ''.join(f'<p><label>{q}<input name="f{n}" value="{v}"></label></p>' for n, (q, v) in enumerate(rows))
    body = ''.join(f'<p>{part}</p>' for part in (title, company)) + fields + ''.join(f'<p>{part}</p>' for part in extra)
    return f'<!doctype html><html><head><meta charset="utf-8"><title>申請</title></head><body>{body}</body></html>'


class FormPage(Page):
    def __init__(self, key, path, title, company, rows, extra=()):
        super().__init__(key, path, (title, company, *extra), title='申請')
        self.form = form_html(title, company, rows, extra)


# ---- 填表 ----

def fill_cards(fb, jobs):
    """看板「可以投了」裡填表會挑、還沒填過的前三張(跑的當下挑,不寫死哪幾張)。"""
    import fill
    return [url for url in fill.eligible(jobs, fb, 'fill', None, '', None)
            if fb[url].get('app') == 'ship' and not fb[url].get('apply')][:3]


def run_fill(bench):
    import board_doc
    import fill
    state = board_doc.load(cf.LIVE)
    jobs = {row['id']: row for row in state['data']['jobs']}
    messy, silent, broken = fill_cards(json.loads(state['fb']), jobs)
    title, company = str(jobs[messy].get('title') or '職缺'), str(jobs[messy].get('company') or '公司')
    empty = FormPage('form', '/apply', title, company, [('姓名', ''), ('可到職日', ''), ('期望薪資', '')])
    done = FormPage('form', '/apply', title, company, [('姓名', '王小明'), ('可到職日', '一個月內'), ('期望薪資', '')],
                    extra=('已上傳:履歷_合併版.pdf',))
    gone = Page('login', '/login', ['請先登入才能上傳附件'], title='登入')

    def sheet(printed):
        last = printed[-1]
        return {'posting': last.cite(title), 'company': {'material_id': last.material_id, 'ids': [99]},
                'form_tab': browser.tab_of('form'), 'same_job': 'true', 'delivery': 'direct_upload',
                'profile': '我在平台上存的那份', 'fields': [
                    {'q': '姓名', 'src': 'rz', 'value': '王小明'},
                    {'q': '可到職日', 'src': 'bank', 'k': '沒有這一題', 'value': '一個月內'},
                    {'q': '期望薪資', 'src': 'skip', 'why': '依公司規定'}],
                'uploaded': [{'name': '履歷_合併版.pdf'}], 'unmet': ['作品集要另外寄'],
                'problems': [{'text': '期望薪資那格是下拉選單,選不到依公司規定'}, '學歷欄位格式不對'],
                'needs_user': '上傳附件前要你登入', 'needs_user_tab': '999:p9', 'needs_user_quote': '請先登入才能上傳附件',
                'stopped': '', 'submitted': 'false'}

    browser = BenchBrowser(bench, Script((('open', (empty,)), ('before', (empty,)), ('after', (done, gone))), sheet=sheet))
    codes = [fill.main(['--stage', 'fill', '--url', messy, '--board', cf.LIVE], browser=browser)]
    for url, end in ((silent, I.NO_SHEET), (broken, I.BROKEN_SHEET)):
        again = BenchBrowser(bench, Script((('open', (empty,)), ('before', (empty,))), sheet={}, end=end))
        codes.append(fill.main(['--stage', 'fill', '--url', url, '--board', cf.LIVE], browser=again))
    return codes


# ---- 查應徵進度 ----

def run_progress(bench):
    import board_doc
    import profile_sync
    import progress
    profile_sync.remember_application_history('104.com.tw', bench.server.url('/records'))
    fb = json.loads(board_doc.load(cf.LIVE)['fb'])
    jobs = {row['id']: row for row in board_doc.load(cf.LIVE)['data']['jobs']}
    sent = [url for url in progress.waiting(fb) if url in jobs][:3]
    company = str(jobs[sent[0]].get('company') or '貴公司')
    mail = Page('mail', '/mail', ['收件匣', '另一封:您的應徵已收到'], title='信箱')
    records = Page('records', '/records', ['應徵紀錄', f'主旨:{company} 面試邀請', f'感謝您應徵{company},想邀請您於下週二面試',
                                          '另一家:您的應徵已收到'], title='應徵紀錄')
    dead = Page('mail2', '/mail/archive', ['封存'], state=I.UNREADABLE, title='封存信件')

    def nav(_printed):
        return {'pages': [{'tab': browser.tab_of('mail').split(':', 1)[1], 'source': '信箱'},
                          {'tab': browser.tab_of('records').split(':', 1)[1], 'source': '104.com.tw'},
                          {'tab': browser.tab_of('mail2').split(':', 1)[1], 'source': '信箱'}],
                'needs_user': '', 'stopped': ''}

    def cite(printed):
        material = printed[0]
        return {'checked': [{'card': url} for url in sent],
                'replies': [{'card': sent[0], 'source': '104.com.tw', 'kind': '面試或測驗邀請',
                             'subject': material.cite('面試邀請'), 'quote': material.cite('想邀請您'), 'date': '下週二', 'todo': '回信約時間'},
                            {'card': sent[1], 'source': '104.com.tw', 'kind': '確認信',
                             'subject': material.cite('應徵已收到'), 'quote': {'material_id': material.material_id, 'ids': [42]},
                             'date': '', 'todo': ''},
                            {'card': sent[2], 'source': '104.com.tw', 'kind': '其他', 'subject': '看不出來', 'quote': '',
                             'date': '', 'todo': ''}],
                'unreadable': [{'source': '104.com.tw', 'reason': '登入過期'}], 'needs_user': '104 要重新登入',
                'needs_user_tab': browser.tab_of('records'), 'needs_user_quote': '應徵紀錄', 'stopped': ''}

    browser = BenchBrowser(bench, Script((('open', (mail, records, dead)),), sheet=nav), Script((), sheet=cite))
    return progress.main(['--board', cf.LIVE], browser=browser), sent


# ---- 找缺 ----

def run_find(bench):
    import find
    ok = bench.server.url('/jobs/1')
    long = bench.server.url('/jobs/2')
    login = bench.server.url('/jobs/3')
    missing = bench.server.url('/jobs/404')
    title, company = '雲端資安工程師', '假網站科技'
    bench.server._pages['/jobs/1'] = html(f'{title} - {company}', [title, company, '刊登日期 2026-11-01',
                                                                     '應徵人數 12 人', '截止日 2026-12-01'])
    bench.server._pages['/jobs/2'] = html('很長的職缺', ['資料工程師', '另一家科技'] + ['職缺說明很長' * 4000] * 3)
    bench.server._pages['/jobs/3'] = html('登入', ['請先登入'])

    def search(_printed):
        return {'candidates': [{'url': url, 'why': '符合', 'via': '搜尋', 'angle': '資安'} for url in (ok, long, login, missing, ok)],
                'notes': '往資安找', 'stopped': ''}

    def judge(printed):
        material = printed[0]
        return {'candidates': [
            {'url': ok, 'keep': 'true', 'score': '4', 'why': '對味', 'risk': 'none', 'title': material.cite(title, text=title),
             'company': material.cite(title, text='假網站科技股份有限公司'), 'applicants': material.cite('應徵人數', text='很多人'),
             'terms_unknown': [{'term': '可遠端'}]},
            {'url': long, 'keep': 'true', 'score': '9', 'why': '可能', 'risk': 'ghost', 'risk_why': '一直重貼'},
            {'url': login, 'keep': 'maybe'},
            {'url': ok, 'keep': 'false'}],
            'terms_missed': [{'term': '薪資 6 萬以上', 'count': 'three'}], 'stopped': '判斷到一半額度用完'}

    browser = BenchBrowser(bench, Script((), sheet=search), Script(end=I.NO_SHEET), Script((), sheet=judge))
    return find.main(['--board', cf.LIVE, '--mode', 'both'], browser=browser)


def main():
    for name, run in (('同步', run_sync), ('填表', run_fill), ('查應徵進度', run_progress), ('找缺', run_find)):
        with WebsiteTestBench(pages={'/': html('假網站', ['首頁'])}) as bench:     # 每件一個新工作區
            for _ in range(int(os.environ.get('MESSY_ROUNDS') or 1)):    # 同一個假網站連跑幾輪(#794 連三輪才提議)
                print(name, run(bench), flush=True)
                for page_id in [p for p, label in bench._page_labels.items() if label != 'p1']:   # ego 一個工作區最多 8 頁;p1 是工作區本身那頁
                    close_page(bench, page_id)


if __name__ == '__main__':
    main()
