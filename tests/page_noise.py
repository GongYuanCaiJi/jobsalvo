"""頁面變形開關(#776):假瀏覽器讀頁的三個出口(_raw、raw_fetch、raw_form)把每一頁加雜訊,證明程式不依頁面內容判斷。

變形五種:前後插段落、雜訊段換順序、同段重複、改標題、多開無關分頁。
不准變的:網址、分頁標籤、agent 引的那句話(原有的段一字不動)、改前改後有沒有變。
所以雜訊只放在原有段落的頭尾,而且同一頁(分頁+網址)改前改後放一模一樣的:共同的頭尾不影響逐處比,改前改後有沒有變跟原來一樣。

字的來源:
  1. tests/page_noise_words.json:以前程式拿來判斷頁面的寫死字與格式(page_noise_extract.py 從程式行、semgrep 違規、注錯補丁抽出);
  2. 10/28 重播證據夾裡真平台頁面的原文(設了 JOBSALVO_NOISE_REAL=1 才讀;JOBSALVO_NOISE_EVIDENCE 指定資料夾,
     預設 ~/jobsearch/evidence/_sync_profiles/20261028-16*):只在測試執行時讀進記憶體;資料夾不在就跳過。
"""
import ast
import dataclasses
import functools
import glob
import json
import os
import random
import re

from hypothesis import strategies as st

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = os.environ.get('JOBSALVO_NOISE_EVIDENCE') or os.path.expanduser('~/jobsearch/evidence/_sync_profiles/20261028-16*')
MAX_PARAGRAPH = 300
SHOWN = 'https://noise.example'


@functools.cache
def repo_words():
    with open(os.path.join(HERE, 'page_noise_words.json'), encoding='utf-8') as source:
        return json.load(source)


@functools.cache
def real_words():
    """真平台頁面的段落。預設不讀(本機與 CI 同種子抽到同樣的字,失敗訊息也不會有個資);
    JOBSALVO_NOISE_REAL=1 才讀,資料夾不在、檔案壞就是沒有這個來源。"""
    found = set()
    if os.environ.get('JOBSALVO_NOISE_REAL') != '1':
        return ()
    for path in sorted(glob.glob(os.path.join(EVIDENCE, 'prof-*.json'))):
        try:
            with open(path, encoding='utf-8') as source:
                pages = json.load(source)['pages']
        except (OSError, ValueError, KeyError):
            continue
        for page in pages.values():
            found.update(part for part in (page.get('page') or {}).get('readingParts', ()) if isinstance(part, str))
    return tuple(sorted(found))


def usable(word):
    word = word.strip()
    return bool(word) and len(word) <= MAX_PARAGRAPH and '\n' not in word and '\r' not in word


@functools.cache
def vocabulary():
    """驗收測試自己寫的字(測試檔裡的字串,CJK 兩字以上、英數四字以上的詞):雜訊段不含這些,
    不然頁上真的有那個字,程式標「頁上有」是對的,不是錯。"""
    words = set()
    for path in glob.glob(os.path.join(HERE, '*.py')):
        if os.path.basename(path).startswith(('test_accept_', 'acceptance_', 'fake_browser')):
            with open(path, encoding='utf-8') as source:
                tree = ast.parse(source.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    words.update(re.findall(r'[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9._-]{3,}', node.value))
    return tuple(sorted(words))


def clean(word):
    return usable(word) and not any(token in word for token in vocabulary())


@functools.cache
def old_words():
    return tuple(dict.fromkeys(w.strip() for w in repo_words()['past_patches'] if usable(w)))


@functools.cache
def pool(old=True):
    sources = repo_words()
    # 注錯補丁的字就是當年程式拿來判斷的字,不過濾(過濾會把要考的字濾掉);其他來源避開測試自己的字
    return tuple(dict.fromkeys(word.strip() for word in (
        *(w for w in sources['past_patches'] if old and usable(w)),
        *(w for name in ('deleted_lines', 'start_semgrep_violations') for w in sources[name] if clean(w)),
        *(w for w in real_words() if clean(w)))))


@functools.cache
def known():
    return frozenset(pool())


@dataclasses.dataclass(frozen=True)
class Noise:
    """一個變形方案。seed 決定每一頁抽哪些字;其他是各種變形的量(Hypothesis 縮的時候往 0 縮)。"""
    seed: int = 0
    head: int = 1          # 原有段落前面插幾段
    tail: int = 1          # 後面插幾段
    shuffle: bool = False  # 雜訊段換順序
    repeat: int = 0        # 雜訊段多重複幾次
    retitle: bool = True   # 改標題
    tabs: int = 1          # 多開幾個無關分頁
    where: int = 2         # 無關分頁排在哪:0 最前面、1 中間、2 最後面
    between: bool = False  # 原有段落之間也插(跟在某些段落後面,照那一段的原文定位,改前改後一致)
    sameurl: bool = False  # 多開一個跟原有分頁同網址、標籤不同的分頁(改前改後都在)
    fields: int = 0        # 申請表多幾個雜訊欄位(原有欄位不動)
    old: bool = True       # 每一頁至少有一段取自以前程式拿來判斷的字(注錯補丁那一源),不靠抽籤碰到

    def rng(self, key):
        return random.Random(f'{self.seed}|{key}')

    def paragraphs(self, key):
        """(前面的段, 後面的段):同一個 key 永遠一樣,改前改後才會對得上。"""
        return _drawn(self, key)

    def title(self, key):
        return self.rng(f'title|{key}').choice(pool(self.old)) if self.retitle else ''

    def between_noise(self, key, part):
        """跟在 part 後面的雜訊段,或 None:只由這一段的原文決定。"""
        rng = self.rng(f'between|{key}|{part}')
        return rng.choice(pool(self.old)) if rng.random() < 0.4 else None

    def noise_fields(self, key):
        return [{'type': 'text', 'label': self.rng(f'field|{key}|{n}').choice(pool(self.old)), 'value': '',
                 'backend': f'noise-b{n}', 'context': self.rng(f'fieldctx|{key}|{n}').choice(pool(self.old))}
                for n in range(1, self.fields + 1)]

    def extra_tabs(self, key):
        return [(f'noise{n}', f'{SHOWN}/{n}', self.rng(f'tab|{key}|{n}').choice(pool(self.old))) for n in range(1, self.tabs + 1)]


@functools.lru_cache(maxsize=None)
def _drawn(noise, key):
    rng = noise.rng(key)
    chosen = [rng.choice(pool(noise.old)) for _ in range(noise.head + noise.tail)]
    if noise.old and chosen:
        chosen[0] = rng.choice(old_words())
    chosen += [rng.choice(chosen) for _ in range(noise.repeat) if chosen]
    if noise.shuffle:
        rng.shuffle(chosen)
    cut = min(noise.head, len(chosen))
    return tuple(chosen[:cut]), tuple(chosen[cut:])


def noises():
    return st.builds(Noise, seed=st.integers(0, 2**16), head=st.integers(0, 3), tail=st.integers(0, 3), shuffle=st.booleans(),
                     repeat=st.integers(0, 2), retitle=st.booleans(), tabs=st.integers(0, 2), where=st.integers(0, 2), old=st.booleans(),
                     between=st.booleans(), sameurl=st.booleans(), fields=st.integers(0, 2))


def from_env(text):
    """JOBSALVO_PAGE_NOISE 的寫法:`種子` 或 `種子:欄=值,欄=值`(欄名同 Noise)。空的是關。"""
    if not text:
        return None
    seed, _, rest = text.partition(':')
    knobs = dict(head=2, tail=2, shuffle=True, repeat=1, retitle=True, tabs=2, old=True, where=2, between=True, sameurl=True, fields=2)
    for item in filter(None, rest.split(',')):
        name, value = item.split('=')
        knobs[name] = value in ('1', 'True', 'true') if isinstance(knobs[name], bool) else int(value)
    return Noise(seed=int(seed), **knobs)
