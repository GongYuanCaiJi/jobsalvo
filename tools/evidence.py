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
note 其他(例如「這一輪沒有交件單」)。查錯第一步打開這裡(AGENTS.md「查錯先看證據」)。

證據夾在資料夾裡(現行看板)或暫存夾裡(副本),不進資料夾的版本紀錄(folder_history.GENERATED)。
記不下來不能拖垮那一輪本身的工作:照實印到 stderr(跟 agent_run._record 一樣)。
"""
import contextlib
import contextvars
import datetime
import json
import os
import re
import shutil
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import card                   # noqa: E402
import config as cf           # noqa: E402

DIR = 'evidence'
EVENTS = 'events.jsonl'
KEEP = 30                     # 每張卡留最近幾輪(local policy:舊的證據對查錯沒用,留著只會越堆越大)
ROUND = re.compile(r'^\d{8}-\d{6}-\d{6}-[a-z0-9_]+-[a-z0-9_-]+$')
FILE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
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
    return os.path.join(root(board), card.card_id_from_url(url))


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
        now = datetime.datetime.now()
        self.flow, self.stage = _slug(flow), _slug(stage)
        self.id = now.strftime('%Y%m%d-%H%M%S-%f') + f'-{self.flow.replace("-", "_")}-{self.stage}'
        self.cards = [u for u in dict.fromkeys(cards or ()) if u]
        homes = [card_dir(u, board) for u in self.cards] or [flow_dir(flow, board)]
        self.dirs = [os.path.join(h, self.id) for h in homes]
        self._of = dict(zip(self.cards, self.dirs))
        self._lock = threading.RLock()     # 找缺的判斷好幾批同時跑,記進同一輪
        self._n = 0
        self.started = now.isoformat(timespec='seconds')
        self.shots = []               # 這一輪程式自己截的圖(檔名,照順序)
        self.pages = []               # 這一輪程式讀到的每一頁(照順序):{why, page, shot, file}
        for home, d in zip(homes, self.dirs):
            try:
                os.makedirs(d, exist_ok=True)
                _prune(home)
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
        return self.file('agent_log', path, 'agent')

    def handoff(self, path, card=None):
        """交件單(agent 交回來的那份,原封不動)。card:只屬於那一張卡的交件單。"""
        return self.file('handoff', path, 'handoff', card)

    def check(self, problems, **data):
        """程式的比對結果:problems 空的就是全部對上。"""
        return self.note('check', problems=list(problems or []), **data)

    def page(self, page, why, shot=None):
        """程式自己讀那一頁看到的樣子;shot 是同一刻程式自己截的圖(沒有就照實記沒有)。回那一筆(shot 是這一輪夾裡的檔名)。"""
        name = None
        if shot and os.path.isfile(shot):
            with self._lock:
                name = self._name('shot', os.path.splitext(shot)[1] or '.png')
                if self._put(name, lambda path: shutil.copyfile(shot, path)):
                    self.shots.append(name)
                else:
                    name = None
        row = self.text('page', 'page', page if page is not None else {}, ext='.json', why=why, shot=name)
        self.pages.append({'why': why, 'page': page, 'shot': name, 'file': row.get('file')})
        return row

    def shot(self, src, why):
        row = self.file('shot', src, 'shot', why=why)
        if row.get('file'):
            self.shots.append(row['file'])
        return row

    def last_shot(self):
        """這一輪最後一張程式自己截的圖(給看板開的 <輪>/<檔名>);沒有回 None。"""
        return self.rel(self.shots[-1]) if self.shots else None


def _prune(home):
    names = sorted(n for n in os.listdir(home) if ROUND.match(n))
    for n in names[:-KEEP]:
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


def run_in(rnd, fn, *args, **kwargs):
    """在那一輪裡跑 fn(同時派好幾批、各在自己的執行緒時用;執行緒不會帶著現在這一輪過去)。"""
    with activated(rnd):
        return fn(*args, **kwargs)


def active():
    """現在開著的那一輪(沒有回 None)。"""
    return _ACTIVE.get()


def path(url, rel, board=None):
    """看板要開的那個檔(<輪>/<檔名>)在哪;不是這張卡證據夾裡的檔回 None。只收固定格式,不收任何路徑。"""
    parts = str(rel or '').split('/')
    if len(parts) != 2 or not ROUND.match(parts[0]) or not FILE.match(parts[1]):
        return None
    p = os.path.join(card_dir(url, board), parts[0], parts[1])
    return p if os.path.isfile(p) else None
