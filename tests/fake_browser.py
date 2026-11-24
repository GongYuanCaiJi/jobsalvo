"""驗收測試唯一的接縫:任務入口的假瀏覽器邊界(ADR-0012 第 2、6 點)。

FakeBrowser 照 web.outside.Browser 回讀頁那一側的原始資料;它的 run_agent 是假 agent,照 Script 一步一步做:
開頁、叫拍照工具(跟真 agent 一樣讀工具印出來的編號材料)、記瀏覽器步驟、寫交件單。
"""
import dataclasses
import json
import os
import re
import sys
import threading
import time
from types import SimpleNamespace

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [TESTS, os.path.join(os.path.dirname(TESTS), 'tools')]
import _env  # noqa: E402,F401
import acceptance_inputs as I  # noqa: E402
import chrome_door  # noqa: E402
import page_noise  # noqa: E402

UNREAD_WHY = '假瀏覽器:整個工作區讀不到'
READ_FAIL = '假瀏覽器:讀頁失敗'
SHOT_FAIL = '假瀏覽器:截圖失敗'
OPEN_FAIL = '假瀏覽器:開不了工作區'
WORKSPACE = 7
# 頁面變形開關(#776):JOBSALVO_PAGE_NOISE=<種子> 開(預設方案),或測試把 page_noise.Noise 設進來;None 是關。
NOISE = page_noise.from_env(os.environ.get('JOBSALVO_PAGE_NOISE'))


SNAP_LOCK = threading.Lock()   # 真 agent 的拍照是另一個程序;假的在本程序改環境變數與 stdout,同時跑幾張時一次只准一張在拍
SALT = ''   # 驗收 driver 每條測試換一個:同一條測試裡改前改後(含另開的假瀏覽器)抽到同樣的字,不同測試抽不同的字


def _noisy(key, parts):
    """原有的段一字不動,只在頭尾放這一頁固定的雜訊段。沒有字的頁(讀到空白)不加:加了就不是空白頁。"""
    if NOISE is None or not parts:
        return list(parts)
    head, tail = NOISE.paragraphs(SALT + key)
    middle = []
    for part in parts:
        middle.append(part)
        if NOISE.between and (extra := NOISE.between_noise(SALT + key, part)) is not None:
            middle.append(extra)
    return [*head, *middle, *tail]


def is_noise(part):
    return NOISE is not None and part in page_noise.known()


def numbered_texts(output):
    return [text for _n, text in re.findall(r'^(\d+)\. (.*)$', output, re.M)]


def faithful(output, expected, what):
    """程式印給 agent 的編號材料(任何一條路:拍照、指示、讀網址)要把瀏覽器給的那一頁的段落一字不差、依序、一段不少地連著印出
    (C-04、A-26)。只在變形開關開著時查:雜訊段是程式不認得的字,丟掉、改寫、跳過都會被抓到。"""
    if NOISE is None:
        return
    wanted = [part.strip() for part in expected if part.strip()]
    shown = [text.strip() for text in numbered_texts(output)]
    if wanted and not any(shown[i:i + len(wanted)] == wanted for i in range(len(shown) - len(wanted) + 1)):
        raise AssertionError(f'{what}:瀏覽器給 {len(wanted)} 段,程式印出的編號材料裡沒有連著、一字不差、依序的這 {len(wanted)} 段'
                             f'(共印 {len(shown)} 段)')


def denoise(output):
    """程式印給 agent 的整段,拿掉變形開關加的東西(無關分頁的標題行、雜訊段)、其餘段重新編號:
    驗收斷言比的是原頁的樣子;agent 引段用的號碼仍是程式印的原號(Printed.parts)。"""
    if NOISE is None:
        return output
    lines, dropped = [], 0
    for line in output.split('\n'):
        match = re.match(r'^(\d+)\. (.*)$', line)
        if match is None:
            dropped = 0
            if not line.startswith((f'[{WORKSPACE}:noise', f'[{WORKSPACE}:dup')):
                lines.append(line)
        elif is_noise(match.group(2)):
            dropped += 1
        else:
            lines.append(f'{int(match.group(1)) - dropped}. {match.group(2)}')
    return '\n'.join(lines)


@dataclasses.dataclass(frozen=True)
class Tab:
    """一個分頁在一次拍照那一刻。label 空的是瀏覽器沒給標籤的分頁。"""
    label: str
    url: str
    state: str = I.READ
    parts: tuple = ('甲',)
    shot: bool = True

    @property
    def tab_id(self):
        return f'{WORKSPACE}:{self.label}'


def _raw(tab):
    """瀏覽器讀一個分頁回來的原始樣子(chrome_door.workspace_pages 的輸入)。"""
    row = {'tab_id': tab.tab_id, 'url': tab.url, 'title': NOISE.title(SALT + tab.tab_id + tab.url) if NOISE else ''}
    if tab.state == I.NO_LABEL:
        return dict(row, error=chrome_door.NO_TAB_LABEL, screenshot_error=chrome_door.NO_TAB_LABEL)
    if tab.state == I.UNREADABLE:
        return dict(row, error=READ_FAIL)
    parts = _noisy(tab.tab_id + tab.url, tab.parts) if tab.state in (I.READ, I.TOO_LONG) else []
    data = {'url': tab.url, 'title': row['title'], 'readingParts': parts, 'reading': ' '.join(parts), 'text': '\n'.join(parts)}
    size = chrome_door.PAGE_MATERIAL_CHAR_LIMIT + 1 if tab.state == I.TOO_LONG else len(json.dumps(data))
    return dict(row, size=size, data=data)


@dataclasses.dataclass(frozen=True)
class Script:
    """假 agent 這一輪做的事。events 依序:('open', 分頁們)只換開著的頁;('before'|'after', 分頁們 或 None)
    先換開著的頁再叫拍照;('step', 指令, 截圖成不成功)記一步瀏覽器指令;('tool', fn) 叫 fn(瀏覽器) 收它印的字;('wait', 秒) 假 agent 在這裡等那麼久(同時跑幾張的測試用)。sheet:dict 照寫成 JSON、str 照原字寫、
    None 不交;callable 的照 (這一輪拍照印出的材料清單) 算。end 是 acceptance_inputs.ENDS 之一。"""
    events: tuple = ()
    sheet: object = None
    end: str = I.HANDED


_STATUS = {I.HANDED: 'completed', I.NO_SHEET: 'completed', I.BROKEN_SHEET: 'completed', I.TIMEOUT: 'timeout',
           I.CRASHED: 'failed', I.STOPPED_BY_USER: 'stopped', I.UNAVAILABLE: 'unavailable'}


class Printed:
    """程式交給 agent 的一次編號材料(拍照工具印的、或指示裡的,同一個印法):material_id=… 與每段「n. 文字」。"""

    def __init__(self, output):
        self.text = denoise(output)
        self.material_id = re.search(r'material_id=(\w+)', output).group(1)
        self.parts = [(int(n), text) for n, text in re.findall(r'^(\d+)\. (.*)$', output, re.M) if not is_noise(text)]

    @staticmethod
    def has(output):
        return 'material_id=' in output

    def cite(self, wanted, /, **extra):
        """引第一段含 wanted 的;extra 照寫進引用(例如 text= agent 寫的值)。"""
        ids = [n for n, part in self.parts if wanted in part][:1]
        return {'material_id': self.material_id, 'ids': ids, **extra}


class FakeBrowser:
    def __init__(self, *scripts, can_open=True, fetched=None):
        self.scripts = list(scripts)
        self.tabs = ()
        self.can_open = can_open
        self.fetched = dict(fetched or {})   # 網址 → raw_fetch 回的樣子
        self.handed = []                     # 交到使用者面前的分頁
        self.prompts = []                    # agent 收到的整段指示
        self.printed = []                    # 每一次拍照工具印出的材料
        self.forms = {}                      # 分頁 → 申請表(raw_form 回的樣子:fields、lines)
        self.filled = []                     # 程式替 agent 填的格子 (分頁, [{backend, how, value}])
        self.fetches = []                    # 程式叫讀的網址
        self.reads = 0                       # 程式讀工作區的次數
        self.url_shot_fail = set()           # 截圖會失敗的網址
        self.timeouts = []                   # 每一次派 agent 給的時限(秒)
        self.resumed = []                    # 每一次派 agent 帶的 (resume, agent_id)
        self.outputs = []                    # 假 agent 叫的工具印出來的字(拍照以外)
        self.fetch_parts = {}                # 網址 → 程式讀網址時瀏覽器給的每一段(含變形開關加的)
        self.read_parts = []                 # 程式最近一次讀工作區,瀏覽器給的每一段(含變形開關加的)
        self.hand_fail = ''                  # 交給使用者那一步失敗的原因(空的是交得出去)
        self.rates = {'TWD': 1, 'USD': 0.03125}   # 一元新台幣換多少外幣(1 美元 = 32 元)

    # ---- 讀頁(只有 web/ 核心叫)----
    def raw_tabs(self):
        self.reads += 1
        self.read_parts = []
        if self.tabs is None:
            raise RuntimeError(UNREAD_WHY)
        rows = [_raw(tab) for tab in self.seen_tabs()]
        self.read_parts = [part for row in rows if 'data' in row and row['size'] <= chrome_door.PAGE_MATERIAL_CHAR_LIMIT
                           for part in row['data']['readingParts']]
        return chrome_door.workspace_pages(rows)

    def seen_tabs(self):
        """工作區裡瀏覽器看得到的分頁:測試開的,加上變形開關多開的無關分頁(同一頁改前改後都在)。"""
        extra = [Tab(label, url, parts=(text,)) for label, url, text in NOISE.extra_tabs(SALT + 'tabs')] if NOISE and self.tabs else []
        if NOISE and NOISE.sameurl and self.tabs:    # 跟第一個原有分頁同網址、標籤不同:配對只看 (分頁, 網址) 都一樣(B09–B11 那類)
            extra.append(Tab('dup1', self.tabs[0].url, parts=(NOISE.rng(SALT + 'dup').choice(page_noise.pool()),)))
        at = (0, len(self.tabs) // 2, len(self.tabs))[NOISE.where] if NOISE else 0
        return (*self.tabs[:at], *extra, *self.tabs[at:])

    def raw_shot(self, tab_id, path):
        tab = next(tab for tab in self.seen_tabs() if tab.tab_id == tab_id)
        if not tab.shot or tab.state == I.NO_LABEL:
            raise OSError(SHOT_FAIL)
        with open(path, 'wb') as target:
            target.write(b'png')
        return path

    def raw_shot_url(self, url, path):
        if url in self.url_shot_fail:
            raise OSError(SHOT_FAIL)
        with open(path, 'wb') as target:
            target.write(b'png')
        return path

    def exchange_rates(self):
        return dict(self.rates)

    def raw_fetch(self, url):
        self.fetches.append(url)
        page = self.fetched.get(url)
        if not page:
            return {'url': url, 'status': 'unknown', 'errors': ['假瀏覽器:沒有這個網址']}
        if NOISE is not None and page.get('text'):
            page = dict(page, text='\n'.join(_noisy(url, page['text'].split('\n'))), title=NOISE.title(SALT + url))
            if page.get('status') == 'ok':
                self.fetch_parts[url] = page['text'].split('\n')
        return page

    def raw_form(self, tab_id):
        form = self.forms.get(tab_id, [])
        if NOISE is not None and isinstance(form, dict) and form.get('lines'):
            form = dict(form, lines=_noisy(tab_id, form['lines']))
        if NOISE is not None and isinstance(form, dict) and form.get('fields') is not None and NOISE.fields:
            form = dict(form, fields=[*form['fields'], *NOISE.noise_fields(SALT + tab_id)])
        return form

    # ---- 動作 ----
    def open_workspace(self):
        if not self.can_open:
            raise RuntimeError(OPEN_FAIL)
        self.tabs = ()
        return {'id': WORKSPACE, 'name': 'accept'}

    def hand_to_user(self, tab_id):
        if not any(tab.tab_id == tab_id for tab in self.tabs or ()):
            raise RuntimeError(f'工作區沒有分頁 {tab_id}')
        if self.hand_fail:
            raise RuntimeError(self.hand_fail)
        self.handed.append(tab_id)
        return tab_id.split(':', 1)[1]

    def fill_form(self, tab_id, items):
        self.filled.append((tab_id, [dict(item) for item in items]))
        return [{'backend': item.get('backend'), 'ok': True} for item in items]

    def run_agent(self, prompt, *, directories, timeout, resume=None, agent_id=None):
        import evidence
        script = self.scripts.pop(0) if self.scripts else Script(end=I.UNAVAILABLE)
        self.prompts.append(denoise(prompt))
        self.resumed.append((resume, agent_id))
        self.timeouts.append(timeout)
        status = _STATUS[script.end]
        if status == 'unavailable':
            return SimpleNamespace(status=status, session=None, why='假瀏覽器:沒有可用的 agent', agent_id=None)
        printed = [Printed(prompt)] if Printed.has(prompt) else []
        if Printed.has(prompt):        # 指示裡印的材料(查進度、判斷職缺):讀工作區的段落、讀網址的段落都要原樣在裡面
            if f'[{WORKSPACE}:' in prompt:
                faithful(prompt, self.read_parts, '指示裡的工作區材料')
            for url, lines in self.fetch_parts.items():
                if url in prompt:
                    faithful(prompt, lines, f'指示裡 {url} 的材料')
        for n, event in enumerate(script.events, 1):
            kind = event[0]
            if kind == 'open':
                self.tabs = event[1]
            elif kind == 'wait':
                time.sleep(event[1])
            elif kind == 'tool':           # ('tool', 叫的函式):agent 叫的其他工具(例如 form_plan),印出的字收進 outputs
                self.outputs.append(event[1](self))
            elif kind == 'step':
                for tab in self.tabs or ():
                    data = {'command_id': f'c{n}', 'action': event[1], 'workspace': str(WORKSPACE),
                            'page': tab.label, 'url': tab.url}
                    if event[2]:
                        evidence.record_external(directories, 'shot', file=f'step-{n}.png', **data)
                    else:
                        evidence.record_external(directories, 'note', what='ego_screenshot_failed',
                                                 reason=SHOT_FAIL, **data)
            else:
                self.tabs = event[1]
                printed.append(Printed(self.snap(kind, directories)))
        self.printed.extend(printed)
        sheet = script.sheet(printed) if callable(script.sheet) else script.sheet
        if script.end == I.BROKEN_SHEET:
            sheet = '[1, 2'
        if sheet is not None and script.end in (I.HANDED, I.BROKEN_SHEET):
            with open(os.path.join(directories[0], 'handoff.json'), 'w', encoding='utf-8') as target:
                target.write(sheet if isinstance(sheet, str) else json.dumps(sheet, ensure_ascii=False))
        return SimpleNamespace(status=status, session='fake-session', why=f'假 agent:{script.end}',
                               agent_id='fake-agent')

    def snap(self, kind, directories):
        """跟真 agent 一樣在轉接環境裡叫 jobsalvo-before / jobsalvo-after,讀它印出來的字。"""
        import contextlib
        import io
        from unittest import mock
        from web import outside, snap
        out = io.StringIO()
        env = {'JOBSALVO_EGO_WORKSPACE': str(WORKSPACE), 'JOBSALVO_EGO_EVIDENCE_DIRS': json.dumps(directories)}
        with SNAP_LOCK, mock.patch.dict(os.environ, env), \
                mock.patch.object(outside.Real, 'for_workspace', return_value=self) as real, contextlib.redirect_stdout(out):
            code = snap.main([f'/bin/jobsalvo-{kind}'])
        assert (code, real.call_args.args) == (0, (WORKSPACE,)), code
        shown = out.getvalue().removesuffix('\n')
        if NOISE is not None:   # 程式照實印:瀏覽器給的每一段(含變形開關加的)一字不差、依序、一段不少(C-04、A-26)
            printed = numbered_texts(shown)
            if printed != self.read_parts:      # 只講段數與第幾段不同,不印頁上的字
                first = next((n for n, pair in enumerate(zip(printed, self.read_parts), 1) if pair[0] != pair[1]),
                             min(len(printed), len(self.read_parts)) + 1)
                raise AssertionError(f'印出 {len(printed)} 段、瀏覽器給 {len(self.read_parts)} 段,第 {first} 段起不同')
        return shown
