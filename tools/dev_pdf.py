#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dev_pdf —— 開發工具(看板檢查、介面截圖)的副本產 PDF 用:Playwright + 這台的 Chrome(跟換 ego 之前一樣排版)。

使用者會跑到的程式一律用 ego 產 PDF(chrome_door.EgoDoor.print_pdf);只有設了 JOBSALVO_DEV_PDF=1 的副本才走這裡
(board_check、shot 開副本前設)。CI 在 Linux 上跑看板檢查,那裡沒有 ego。
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def print_pdf(html_path, pdf_path, timeout=120):
    """把本機 HTML 印成 PDF(照 CSS 的 @page 版面、不加頁首頁尾);只准讀本機檔,不連網。"""
    from playwright.sync_api import sync_playwright
    from chrome_bin import chrome
    with sync_playwright() as pw:
        # 拿掉 Playwright 無頭模式預設加的字型 hinting 設定:它會改字寬,英文履歷換行跟一般 Chrome 印的不一樣
        browser = pw.chromium.launch(executable_path=chrome()[0], args=['--headless=new', '--disable-gpu', '--hide-scrollbars'],
                                     ignore_default_args=['--font-render-hinting=none'])
        try:
            ctx = browser.new_context(offline=True)
            page = ctx.new_page()
            page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(('file:', 'data:')) else r.abort())
            page.goto('file://' + html_path, wait_until='load', timeout=timeout * 1000)
            page.pdf(path=pdf_path, prefer_css_page_size=True, display_header_footer=False,
                     margin={'top': '0.4in', 'bottom': '0.4in', 'left': '0.4in', 'right': '0.4in'})
        finally:
            browser.close()
