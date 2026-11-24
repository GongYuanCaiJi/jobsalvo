"""核對:agent 引的字一字不差在頁上(C-10、C-11 等)。回程式自己的分類或是否,不回頁上的字。"""
from __future__ import annotations

import dataclasses

from web import seen
from web.marks import Mark, Source, say


def hand_over(browser, tab: str, quote: str) -> tuple[str, tuple[Mark, ...]]:
    """交出 agent 指的那一分頁:分頁在工作區就當場叫到使用者面前,重讀標引文一字不差在頁上沒有;
    分頁不在、交不出去才是沒有交接(A-28、C-11、C-24)。回 (交出的分頁標籤, 標記)。"""
    pages = seen.read_workspace(browser)
    if not seen.has_tab(pages, tab):
        return '', (say('HANDOFF_UNVERIFIED', page='那一頁'),)
    page = next(page for page in pages if page._seen_tab == str(tab))
    try:
        tab_label = browser.hand_to_user(tab)
    except Exception as error:  # noqa: BLE001 - a failed handover is listed with its reason
        return '', (say('HANDOFF_UNVERIFIED', page='那一頁', why=str(error)[:160] or type(error).__name__),)
    page_label = page._seen_url or '那一頁'   # 交接訊息只寫網址(#662 第四點):標題是網站自己會換的字,不拿來指那一頁
    if page._seen_status is not seen.Status.READ:
        return tab_label, (say('HANDED_QUOTE_UNMATCHED', page=page_label, quote=quote, actual='那一頁讀不到'),)
    if not quote or not any(quote in part for part in page._seen_parts):
        return tab_label, (say('HANDED_QUOTE_UNMATCHED', page=page_label, quote=quote,
                               actual='頁上沒有一字不差的這段字'),)
    return tab_label, (say('HANDED_OVER', page=page_label, quote=quote, url=page._seen_url),)


def fixed_profile(record, url: str):
    """固定版平台履歷那一份:只留拍到網址跟登記網址一字不差的頁;一頁都沒有就標沒有固定履歷頁(C-15、F-10)。
    回 (只含那一頁的這一輪, 標記)。"""
    befores, afters = at_url(record.befores, url), at_url(record.afters, url)
    found = any(snapshot.pages for snapshot in (*befores, *afters))
    return (dataclasses.replace(record, befores=befores, afters=afters),
            () if found else (say('NO_FIXED_PROFILE_PAGE'),))


def at_url(snapshots, url: str) -> tuple:
    """每次拍照只留網址跟 url 一字不差的頁:固定版平台履歷那一份的清單與它的恢復都只比這幾頁(F-10、F-11)。"""
    return tuple(dataclasses.replace(snapshot, pages=tuple(page for page in snapshot.pages if page._seen_url == url))
                 for snapshot in snapshots)


def shot_showing(afters, text: str) -> str:
    """最後一次拍改後(答案填上去那一刻)頁上有這一段字一字不差的那一頁的截圖;沒有是空字串(C-18、F-08)。"""
    last = max(afters, key=lambda snapshot: snapshot.seq, default=None)
    return next((page._seen_shot for page in reversed(last.pages if last else ())
                 if page._seen_shot and text and any(text in part for part in page._seen_parts)), '')


def source_unread(pages, name: str, *, labels=(), url: str = '') -> tuple[Mark, ...]:
    """一個登記的來源這一輪程式讀到沒有(P-08–P-11、C-17):給了 url(應徵紀錄頁)看那個網址一字不差在不在工作區,
    沒給看 agent 標成這個來源的分頁(Page 標籤);一頁都沒有、或那一頁沒讀到,就標沒查過這個來源。讀到是空的。"""
    found = [page for page in pages if (page._seen_url == url if url else
                                        bool(page._seen_tab) and page._seen_tab.partition(':')[2] in labels)]
    if not found:
        why = '應徵紀錄頁不在程式讀到的工作區' if url else '交件單沒有把工作區的分頁標成這個來源'
        return (say('SOURCE_UNREAD', source=Source.NOT_COMPARED, name=name, why=why, url=url),)
    return tuple(say('SOURCE_UNREAD', source=Source.NOT_COMPARED, name=name,
                     why=page._seen_why or page._seen_status.value, url=page._seen_url)
                 for page in found if page._seen_status is not seen.Status.READ)
