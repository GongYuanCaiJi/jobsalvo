"""頁面內容的不透明型別與讀頁(ADR-0012)。

拍到的頁(Seen)與 agent 照抄的頁上字(Quote)只有 web/ 核心讀得到:欄位一律 `_seen_` 開頭,
相等、雜湊、字串化、逐項取出、複製、序列化一律丟錯。任務檔拿到它只能交給核心的函式。
瀏覽器回的原始資料在這裡轉成段落;真的去讀頁、讀網址的是 web/outside.py。
"""
from __future__ import annotations

import enum
import urllib.parse


class Status(enum.Enum):
    READ = 'read'
    UNREADABLE = 'unreadable'
    NO_TEXT = 'no_text'
    TOO_LONG = 'too_long'


class _Opaque:
    __slots__ = ()

    def __init__(self, **fields):
        for name, value in fields.items():
            object.__setattr__(self, '_seen_' + name, value)

    def _refuse(self, *_args, **_kwargs):
        raise TypeError(f'{type(self).__name__} 是頁面內容,只能交給 web/ 核心的函式(ADR-0012)')

    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __hash__ = _refuse
    __str__ = __format__ = __iter__ = __contains__ = __getitem__ = _refuse
    __reduce__ = __reduce_ex__ = __getstate__ = __setattr__ = __delattr__ = _refuse

    def __repr__(self):
        return f'<{type(self).__name__} 頁面內容>'


class Seen(_Opaque):
    """拍到的一頁:分頁、網址、讀取狀態與原因、段落、截圖、讀頁那一側回的原始資料。沒有分頁編號的是整個工作區讀不到。"""
    __slots__ = ('_seen_tab', '_seen_url', '_seen_status', '_seen_why', '_seen_parts', '_seen_shot',
                 '_seen_shot_why', '_seen_via', '_seen_title', '_seen_raw')

    def __init__(self, tab, url, status, why='', parts=(), shot='', shot_why='', via='', title='', raw=None):
        super().__init__(tab=str(tab), url=str(url), status=status, why=str(why), parts=tuple(parts),
                         shot=str(shot), shot_why=str(shot_why), via=str(via), title=str(title), raw=dict(raw or {}))


class Quote(_Opaque):
    """agent 照抄的頁上字;verified 是程式在它引的段裡一字不差核對到。"""
    __slots__ = ('_seen_text', '_seen_verified')

    def __init__(self, text, verified):
        super().__init__(text=str(text), verified=bool(verified))


def with_shot(seen: Seen, shot: str, why: str) -> Seen:
    return Seen(seen._seen_tab, seen._seen_url, seen._seen_status, seen._seen_why, seen._seen_parts,
                shot, why, seen._seen_via, seen._seen_title, seen._seen_raw)


def host(url: str) -> str:
    """網址的主機名(C-12、C-13);主機名大小寫不分是網址規格本身(RFC 3986 §3.2.2)。"""
    return (urllib.parse.urlsplit(str(url)).hostname or '').rstrip('.').casefold()


def platform_of(url: str, hosts: dict[str, list[str]]) -> str | None:
    """主機在使用者登記的網域清單裡就是那個平台;最長的網域贏,同長度兩個平台就不算(C-12)。"""
    name = host(url)
    matches = [(len(domain), platform) for platform, domains in hosts.items() for domain in domains
               if name == domain.casefold() or name.endswith('.' + domain.casefold())]
    if not matches:
        return None
    longest = max(length for length, _platform in matches)
    platforms = {platform for length, platform in matches if length == longest}
    return platforms.pop() if len(platforms) == 1 else None


_STATUS = {'readable': Status.READ, 'too_large': Status.TOO_LONG, 'no_text': Status.NO_TEXT}


def _from_row(row: dict) -> Seen:
    """讀頁那一側回的一列轉成一頁:段落照讀到的原樣,一段都沒有字就是讀不出字(C-01)。"""
    data = row.get('data') if isinstance(row.get('data'), dict) else row
    parts = data.get('readingParts')
    if not isinstance(parts, list):
        parts = [data.get('reading') or data.get('text') or '']
    parts = tuple(part for part in parts if isinstance(part, str) and part)
    status = _STATUS.get(row.get('read_status', 'readable'), Status.UNREADABLE)
    why = str(row.get('read_error') or row.get('error') or '')
    if status is Status.READ and not any(part.strip() for part in parts):
        status, why = Status.NO_TEXT, why or '工作區分頁沒有可讀文字'
    return Seen(row.get('tab_id') or '', data.get('url') or row.get('url') or '', status, why, parts,
                shot_why=row.get('screenshot_error') or '', title=data.get('title') or row.get('title') or '',
                raw=data)


def read_workspace(browser) -> tuple[Seen, ...]:
    """讀工作區每一頁;一頁失敗不影響別頁,整個讀不到就是一筆沒有分頁編號的(C-01)。"""
    try:
        rows = browser.raw_tabs()
    except Exception as error:  # noqa: BLE001 - any reader failure is recorded as the workspace state
        return (Seen('', '', Status.UNREADABLE, f'工作區讀取失敗:{error}'),)
    return tuple(_from_row(row if isinstance(row, dict) else {}) for row in rows or ())


def record_reads(pages: tuple[Seen, ...], evidence_round) -> None:
    """把交件時讀到的頁面狀態留在本輪紀錄,包括失敗原因(P-17)。"""
    for page in pages:
        evidence_round.note('page_read', tab=page._seen_tab, url=page._seen_url, title=page._seen_title,
                            status=page._seen_status.value, reason=page._seen_why)


def fetch(browser, url: str, shot: str = '') -> Seen:
    """讀 agent 交回的網址(直接抓、閱讀代理、瀏覽器依序退,公開網址檢查在邊界那一側,C-02、C-03);
    給了 shot 就同時截圖存到那裡,截圖的任何失敗都記成截圖沒取得(J-10)。"""
    page = _fetched(browser.raw_fetch(url), url)
    if not shot:
        return page
    try:
        browser.raw_shot_url(url, shot)
    except Exception as error:  # noqa: BLE001 - 截圖沒取得照實記,這一頁照樣用
        return with_shot(page, '', f'{type(error).__name__}: {error}')
    return with_shot(page, shot, '')


def _fetched(result: dict, url: str) -> Seen:
    lines = tuple(line for line in str(result.get('text') or '').splitlines() if line)
    via, title, final = result.get('via') or '', result.get('title') or '', result.get('url') or url
    why = '; '.join(result.get('errors') or ())
    raw = {'http_status': result['http_status']} if result.get('http_status') else {}
    if result.get('status') == 'partial':
        return Seen('', final, Status.TOO_LONG, why, lines, via=via, title=title, raw=raw)
    if result.get('status') != 'ok':
        return Seen('', final, Status.UNREADABLE, why, via=via, title=title, raw=raw)
    return Seen('', final, Status.READ, '', lines, via=via, title=title, raw=raw)


def has_tab(pages, tab: str) -> bool:
    """那一分頁還在不在讀到的工作區,只比分頁編號(C-24)。"""
    return any(page._seen_tab == str(tab) for page in pages if str(tab))


def form(browser, tab: str):
    """讀申請表每一格瀏覽器給的名稱與值(C-23);讀不到就是空的。"""
    return browser.raw_form(tab) or {}


def number(material, pages, owner: str | None = None) -> str:
    """每一頁的段落接著編號,印給 agent(ADR-0009,C-04);回印出來的那一塊。owner:段落屬於哪一列(預設是分頁)。"""
    return '\n'.join(material.add(list(page._seen_parts), 'line', owner=owner or page._seen_tab) for page in pages)


def dump(seen: Seen) -> dict:
    """存進證據夾的格式(起點格式不變)。"""
    return {'tab': seen._seen_tab, 'url': seen._seen_url, 'status': seen._seen_status.value, 'why': seen._seen_why,
            'parts': list(seen._seen_parts), 'shot': seen._seen_shot, 'shot_why': seen._seen_shot_why,
            'via': seen._seen_via, 'title': seen._seen_title, 'material': seen._seen_raw}


def load(row: dict) -> Seen:
    return Seen(row['tab'], row['url'], Status(row['status']), row.get('why', ''), row.get('parts') or (),
                row.get('shot', ''), row.get('shot_why', ''), row.get('via', ''), row.get('title', ''),
                row.get('material'))
