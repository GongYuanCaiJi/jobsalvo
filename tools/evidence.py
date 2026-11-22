#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence —— 每張卡每一輪的證據,集中放在那張卡自己的資料夾(#315)。所有會叫 agent 的流程同一種放法。

  證據夾/<卡 id>/<輪>/          一輪一夾,夾名 = 開始時間-流程-階段(照名字排就是照時間排)
      events.jsonl              這一輪發生的每一件事,一行一筆、照時間排:{at, n, kind, file?, ...}
      instruction-*.txt         程式給 agent 的指示(每一次派出去的那一份;換手到另一家各記一份)
      agent-*.log               agent 的動作紀錄(複製進來:原本的檔下一輪會被蓋掉、放在暫存夾重開機就沒了)
      handoff-*.json            交件單(agent 交回來的那份,原封不動)
      page-*.json / shot-*.png  程式自己讀那一頁看到的樣子,和同一刻程式自己截的圖(驗收、之後每一次讀)
  沒有卡的那幾輪(找缺的搜尋、分類建議)放證據夾/_<流程>/,格式一樣。

kind:instruction 指示、agent_log 動作紀錄、handoff 交件單、check 程式的比對結果、page 讀頁、shot 截圖、
note 其他(例如「這一輪沒有交件單」)。查錯第一步打開這裡。

證據夾在資料夾裡(現行看板)或暫存夾裡(副本),不進資料夾的版本紀錄(folder_history.GENERATED)。
記不下來不能拖垮那一輪本身的工作:照實印到 stderr(跟 agent_run._record 一樣)。
"""
import contextlib
import contextvars
import datetime
import fcntl
import json
import os
import re
import shutil
import sys
import threading
import weakref

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import card_identity           # noqa: E402
import config as cf           # noqa: E402

DIR = 'evidence'
EVENTS = 'events.jsonl'
SYNC_PROFILES_REF = '_sync'  # 沒有卡片的全部同步，讓回報能指到這一輪的截圖。
KEEP = 30                     # 每張卡留最近幾輪(local policy:舊的證據對查錯沒用,留著只會越堆越大)
ROUND = re.compile(r'^\d{8}-\d{6}-\d{6}-[a-z0-9_]+-[a-z0-9_-]+$')
FILE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
_CLOCK, _LAST = threading.Lock(), [datetime.datetime.min]
_OPEN = weakref.WeakSet()      # 還被拿著的 Round


def _tick():
    """現在的時間,同一個程式裡不給同一個微秒兩次:輪編號靠它,同時開兩輪(填表同時跑幾張)不會撞同一夾。"""
    with _CLOCK:
        _LAST[0] = max(datetime.datetime.now(), _LAST[0] + datetime.timedelta(microseconds=1))
        return _LAST[0]


_ACTIVE = contextvars.ContextVar('evidence_round', default=None)


def root(board=None):
    """證據夾:現行看板放資料夾裡(跟著使用者的資料走),副本放暫存夾(測試、沙箱不留在真的資料夾;
    EVIDENCE_TMP 可以換一個地方,跟 APPLY_TMP 那幾個一樣)。現行看板照現在的設定認(設定可能重讀過)。"""
    import board_doc as bd
    if board is None or bd.is_live(board, cf.LIVE):
        return os.path.join(cf.HOME, DIR)
    return os.path.join(os.environ.get('EVIDENCE_TMP') or cf.TMP, DIR)


def card_dir(url, board=None):
    """那張卡自己的證據資料夾。"""
    return os.path.join(root(board), card_identity.card_id_from_url(url))


def round_dir(card_id, round_id, board=None):
    """看板來源連結只接受證據夾裡格式正確的一輪。"""
    if not re.fullmatch(r'[0-9a-f]{12}', str(card_id or '')) or not ROUND.fullmatch(str(round_id or '')):
        return None
    base = os.path.realpath(root(board))
    folder = os.path.realpath(os.path.join(base, card_id, round_id))
    if os.path.commonpath((base, folder)) != base or not os.path.isdir(folder):
        return None
    return folder


def flow_round_dir(flow, round_id, board=None):
    """流程共用證據輪次的目錄；只接受格式正確且位於流程資料夾內的輪次。"""
    if not ROUND.fullmatch(str(round_id or '')):
        return None
    base = os.path.realpath(flow_dir(flow, board))
    folder = os.path.realpath(os.path.join(base, str(round_id)))
    if os.path.commonpath((base, folder)) != base or not os.path.isdir(folder):
        return None
    return folder


def flow_dir(flow, board=None):
    """沒有卡的那幾輪放這裡。"""
    return os.path.join(root(board), '_' + _slug(flow))


def _slug(s):
    return re.sub(r'[^a-z0-9_-]+', '_', str(s or '').lower()).strip('_-') or 'x'


def _warn(what, e):
    print(f'證據記不下來({what}):{e}', file=sys.stderr)


class Round:
    """一輪。cards:這一輪關到哪幾張卡(網址);空的就放那個流程自己的夾。每一張卡各一份,內容一樣。"""

    def __init__(self, flow, stage, cards=(), board=None):
        now = _tick()
        self.flow, self.stage = _slug(flow), _slug(stage)
        self.id = now.strftime('%Y%m%d-%H%M%S-%f') + f'-{self.flow.replace("-", "_")}-{self.stage}'
        self.cards = [u for u in dict.fromkeys(cards or ()) if u]
        homes = [card_dir(u, board) for u in self.cards] or [flow_dir(flow, board)]
        self.dirs = [os.path.join(h, self.id) for h in homes]
        self._of = dict(zip(self.cards, self.dirs))
        self._lock = threading.RLock()     # 找缺的判斷好幾批同時跑,記進同一輪
        self._n = 0
        with _CLOCK:
            _OPEN.add(self)
        self.started = now.isoformat(timespec='seconds')
        self.shots = []               # 這一輪程式自己截的圖(檔名,照順序)
        self.pages = []               # 這一輪程式讀到的每一頁(照順序):{why, page, shot, file}
        for home, d in zip(homes, self.dirs):
            try:
                os.makedirs(d, exist_ok=True)
                _prune(home, board)
            except OSError as e:
                _warn(d, e)

    def rel(self, name):
        """給看板開的相對位置:<輪>/<檔名>(/api/evidence 只收這種)。"""
        return f'{self.id}/{name}'

    def _where(self, card):
        """card 給了就只記進那一張卡的夾(例如每張卡各自的交件單),不然每一張都記。"""
        return [self._of[card]] if card in self._of else self.dirs

    def _name(self, stem, ext):
        for k in range(1, 1000):
            name = f'{stem}-{k}{ext}'
            if not any(os.path.exists(os.path.join(d, name)) for d in self.dirs):
                return name
        return f'{stem}-{datetime.datetime.now().strftime("%f")}{ext}'

    def note(self, kind, card=None, **data):
        """記一筆事件(照發生順序)。回那一筆。"""
        with self._lock:
            self._sync_n()
            self._n += 1
            row = {'at': datetime.datetime.now().isoformat(timespec='microseconds'), 'n': self._n, 'kind': kind, **data}
            self._append(row, card)
        return row

    def _append(self, row, card):
        line = json.dumps(row, ensure_ascii=False, default=str) + '\n'
        for d in self._where(card):
            try:
                with open(os.path.join(d, EVENTS), 'a', encoding='utf-8') as f:
                    f.write(line)
            except OSError as e:
                _warn(d, e)

    def _put(self, name, write, card=None):
        first = None
        for d in self._where(card):
            path = os.path.join(d, name)
            try:
                if first:
                    try:
                        os.link(first, path)          # 好幾張卡同一輪:同一份檔不佔好幾份空間
                    except OSError:
                        shutil.copyfile(first, path)
                else:
                    write(path)
                    first = path
            except OSError as e:
                _warn(path, e)
        return bool(first)

    def text(self, kind, stem, text, ext='.txt', card=None, **data):
        def write(path):
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1, default=str))
        with self._lock:
            name = self._name(stem, ext)
            if self._put(name, write, card):
                return self.note(kind, card, file=name, **data)
        return self.note(kind, card, missing=stem, **data)

    def file(self, kind, src, stem, card=None, **data):
        """把一個檔複製進來。檔不在就照實記「這一輪沒有」。"""
        if not src or not os.path.isfile(src):
            return self.note(kind, card, missing=os.path.basename(str(src or stem)), **data)
        with self._lock:
            name = self._name(stem, os.path.splitext(src)[1])
            if self._put(name, lambda path: shutil.copyfile(src, path), card):
                return self.note(kind, card, file=name, src=os.path.basename(src), **data)
        return self.note(kind, card, missing=stem, **data)

    # ---- 各種證據 ----
    def instruction(self, task, agent=None):
        return self.text('instruction', 'instruction', task or '', agent=agent)

    def agent_log(self, path):
        self._sync_n()
        return self.file('agent_log', path, 'agent')

    def _sync_n(self):
        for directory in self.dirs:
            try:
                with open(os.path.join(directory, EVENTS), encoding='utf-8') as f:
                    for line in f:
                        with contextlib.suppress(TypeError, ValueError):
                            self._n = max(self._n, int(json.loads(line).get('n') or 0))
            except OSError:
                pass

    def handoff(self, path, card=None):
        """交件單(agent 交回來的那份,原封不動)。card:只屬於那一張卡的交件單。"""
        return self.file('handoff', path, 'handoff', card)

    def check(self, problems, **data):
        """程式的比對結果:problems 空的就是全部對上。"""
        return self.note('check', problems=list(problems or []), **data)

    def page(self, page, why, shot=None, tab=None):
        """程式自己讀那一頁看到的樣子;shot 是同一刻程式自己截的圖(沒有就照實記沒有)。回那一筆(shot 是這一輪夾裡的檔名)。"""
        name = None
        if shot and os.path.isfile(shot):
            with self._lock:
                name = self._name('shot', os.path.splitext(shot)[1] or '.png')
                if self._put(name, lambda path: shutil.copyfile(shot, path)):
                    self.shots.append(name)
                else:
                    name = None
        row = self.text('page', 'page', page if page is not None else {}, ext='.json', why=why, shot=name,
                        **({'tab_id': str(tab)} if tab is not None else {}))
        self.pages.append({'why': why, 'tab': None if tab is None else str(tab), 'page': page, 'shot': name,
                           'file': row.get('file')})
        return row

    def shot(self, src, why):
        row = self.file('shot', src, 'shot', why=why)
        if row.get('file'):
            self.shots.append(row['file'])
        return row

    def last_shot(self):
        """這一輪最後一張程式自己截的圖(給看板開的 <輪>/<檔名>);沒有回 None。"""
        return self.rel(self.shots[-1]) if self.shots else None


def _pointed_at(board=None):
    """還有人指著的那幾輪(回一個「這一輪的夾名有沒有人指著」的函式):看板文字(題目、卡上、回報附的截圖)
    和平台履歷登記檔(同步版本、看過了的版本、清單附的證據)。這是「誰還指著這一輪」唯一回答的地方。
    任何一邊讀不到就丟例外:分不出哪幾輪還有人用。"""
    import board_doc as bd
    import profile_registry
    text = bd.read_doc(bd.target(board))
    registry = json.dumps(profile_registry.load(), ensure_ascii=False)
    return lambda name: name + '/' in text or name in registry


def _prune(home, board=None):
    """每張卡留最近 KEEP 輪;還有人指著的那幾輪不刪,不然他點開是空的(#447、#601 第 19 條)。
    讀不到就不刪:分不出哪幾輪還有人用。"""
    with _CLOCK:
        running = {r.id for r in _OPEN}     # 這個程式自己還開著的輪:同時開的超過 KEEP 輪時不能被後開的清掉
    names = sorted(n for n in os.listdir(home) if ROUND.match(n) and n not in running)[:-KEEP]
    if not names:
        return
    try:
        pointed = _pointed_at(board)
    except Exception as e:  # noqa: BLE001 — 看板或登記檔讀不到(被改過、不在):這次不清,下一輪再清
        return _warn('清舊的證據', e)
    for n in names:
        if not pointed(n):
            shutil.rmtree(os.path.join(home, n), ignore_errors=True)


@contextlib.contextmanager
def opened(flow, stage, cards=(), board=None):
    """開一輪,這段期間派的 agent(agent_run.run)都記進這一輪。"""
    rnd = Round(flow, stage, cards, board)
    with activated(rnd):
        yield rnd


@contextlib.contextmanager
def activated(rnd):
    """讓已經開好的那一輪成為現在這一段(例如另一條執行緒)的那一輪。"""
    token = _ACTIVE.set(rnd)
    try:
        yield rnd
    finally:
        _ACTIVE.reset(token)


def active():
    """現在開著的那一輪(沒有回 None)。"""
    return _ACTIVE.get()


def record_external(directories, kind, **data):
    for directory in directories:
        try:
            path = os.path.join(directory, EVENTS)
            with open(path, 'a+', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.seek(0)
                number = 0
                for line in f:
                    with contextlib.suppress(TypeError, ValueError):
                        number = max(number, int(json.loads(line).get('n') or 0))
                row = {'at': datetime.datetime.now().isoformat(timespec='microseconds'),
                       'n': number + 1, 'kind': kind, **data}
                f.seek(0, os.SEEK_END)
                f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
                f.flush()
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            continue


def path(url, rel, board=None):
    """看板要開的圖(<輪>/<檔名>)在哪；只收卡片、填表流程或全部同步自己的證據夾。"""
    parts = str(rel or '').split('/')
    if len(parts) != 2 or not ROUND.match(parts[0]) or not FILE.match(parts[1]):
        return None
    home = flow_dir('sync', board) if url == SYNC_PROFILES_REF else card_dir(url, board)
    p = os.path.join(home, parts[0], parts[1])
    if not os.path.isfile(p) and url != SYNC_PROFILES_REF:
        # 填表那一輪的截圖在填表流程的證據夾(拍照工具存的位置),跟 /api/sync-evidence 同一條退路
        folder = flow_round_dir('fill', parts[0], board)
        p = os.path.join(folder, parts[1]) if folder else ''
    return p if p and os.path.isfile(p) else None


def old_shots(fb: dict) -> dict[str, str | None]:
    """回傳舊可投遞夾中尚未搬進證據夾的填表截圖。"""
    return {url: None if record.get('ev') else os.path.join(cf.HOME, record['shot'])
            for url, card_data in fb.items()
            if isinstance(card_data, dict) and isinstance((record := card_data.get('apply')), dict)
            and record.get('shot')}


def keep_old_shots(shots: dict[str, str | None], board=None) -> dict[str, dict | None]:
    """把舊填表截圖搬進對應職缺的證據夾。"""
    kept = {}
    for url, source in shots.items():
        round_ = Round('apply', 'earlier', [url], board) if source else None
        result = round_.last_shot() if round_ and round_.shot(source, '證據夾之前填表時的截圖').get('file') else None
        kept[url] = result
    return kept


def adopt_old_shots(fb: dict, kept: dict[str, dict | None]) -> None:
    """將看板上的舊截圖位置改為證據夾位置。"""
    for url, saved in kept.items():
        record = (fb.get(url) or {}).get('apply')
        if isinstance(record, dict) and record.pop('shot', None) is not None and saved and not record.get('ev'):
            record['ev'] = saved
