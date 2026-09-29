#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
browser —— 同一個程式裡共用瀏覽器:讀網頁文字、排 PDF 不再每件事開一整個 Chrome。

Playwright 的標準用法(https://playwright.dev/python/docs/browser-contexts):一個瀏覽器開著,
每件事開一個 browser context(像一個獨立的無痕視窗,cookie、代理各自一份),做完就關。
Playwright 的同步 API 只能在開它的那條執行緒用,所以固定 WORKERS 條執行緒各自養一個瀏覽器,
工作排隊交給它們;程式結束時一起關。同時開的 Chrome 數量 = WORKERS(以前讀網頁最多 2 個、排 PDF 另外開)。
"""
import atexit
import os
import queue
import threading
import urllib.parse

WORKERS = 2
_jobs = queue.Queue()
_started = []
_lock = threading.Lock()
_broken = []      # 開不了瀏覽器(沒裝 Playwright、沒有 Chrome):排隊的工作直接拿到這個錯誤,不要乾等到逾時


def _worker():
    try:
        from playwright.sync_api import sync_playwright
        from chrome_bin import chrome
        pw_cm = sync_playwright()
        pw = pw_cm.__enter__()
    except BaseException as error:
        _broken.append(error)
        while True:                      # 把排著的、之後才來的工作都退回去
            job = _jobs.get()
            if job is None:
                return
            job[2]['error'] = error
            job[1].set()
    browser = None
    try:
        while True:
            job = _jobs.get()
            if job is None:
                break
            fn, done, box = job
            try:
                if browser is None or not browser.is_connected():
                    # 拿掉 Playwright 無頭模式預設加的字型 hinting 設定:它會改字寬,英文履歷換行跟一般 Chrome 印的不一樣
                    browser = pw.chromium.launch(executable_path=chrome()[0],
                                                 args=['--headless=new', '--disable-gpu', '--hide-scrollbars'],
                                                 ignore_default_args=['--font-render-hinting=none'])
                box['value'] = fn(browser)
            except BaseException as error:   # 交回給排隊的那一方處理
                box['error'] = error
            finally:
                done.set()
        if browser is not None:
            browser.close()
    finally:
        pw_cm.__exit__(None, None, None)


def _stop():
    for _ in _started:
        _jobs.put(None)
    for t in _started:
        t.join(timeout=10)


def run(fn, timeout):
    """在共用的瀏覽器上跑 fn(browser),回傳它的結果;逾時丟 TimeoutError。fn 自己開 context、用完關。"""
    with _lock:
        if not _started:
            for _ in range(WORKERS):
                t = threading.Thread(target=_worker, daemon=True)
                t.start()
                _started.append(t)
            atexit.register(_stop)
    if _broken:
        raise RuntimeError(f'開不了瀏覽器:{_broken[0]}')
    done, box = threading.Event(), {}
    _jobs.put((fn, done, box))
    if not done.wait(timeout):
        raise TimeoutError('瀏覽器逾時')
    if 'error' in box:
        raise box['error']
    return box.get('value')


def only_public(route):
    """讀別人網頁時,每個請求(含頁面自己再發的)都要是公開網址;內網、本機一律擋。"""
    import page_fetch
    url = route.request.url
    try:
        host = urllib.parse.urlsplit(url).hostname
        local_test = os.environ.get('JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH') == '1'
        if not (local_test and host in ('127.0.0.1', '::1')):
            page_fetch._assert_public_url(url)
    except Exception:
        route.abort('blockedbyclient')
        return
    route.continue_()


def page_text(url, proxy, timeout):
    """經受控代理讀一頁的可見文字(讀網頁的最後一條路)。"""
    def job(browser):
        ctx = browser.new_context(proxy={'server': proxy, 'bypass': '<-loopback>'}, viewport={'width': 1440, 'height': 1000})
        try:
            page = ctx.new_page()
            page.route('**/*', only_public)      # 代理之外的第二道:每個請求再擋一次內網
            page.goto(url, wait_until='load', timeout=timeout * 1000)
            page.wait_for_timeout(900)
            return page.evaluate("document.body ? document.body.innerText : ''") or ''
        finally:
            ctx.close()
    return run(job, timeout + 15)


def print_pdf(html_path, pdf_path, timeout=120):
    """把本機 HTML 印成 PDF(照 CSS 的 @page 版面、不加頁首頁尾);只准讀本機檔,不連網。"""
    def job(browser):
        ctx = browser.new_context(offline=True)
        try:
            page = ctx.new_page()
            page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(('file:', 'data:')) else r.abort())
            page.goto('file://' + html_path, wait_until='load', timeout=timeout * 1000)
            # 邊界照 Chrome 列印的預設(0.4 吋,CSS 的 @page margin 會蓋過它):跟以前 chrome --print-to-pdf 排出來一樣
            page.pdf(path=pdf_path, prefer_css_page_size=True, display_header_footer=False,
                     margin={'top': '0.4in', 'bottom': '0.4in', 'left': '0.4in', 'right': '0.4in'})
        finally:
            ctx.close()
    return run(job, timeout + 15)
