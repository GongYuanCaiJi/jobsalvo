#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent 叫的讀網址工具(page_fetch <網址>,I-13):程式讀這個網址,印出讀到的編號材料或讀不到的原因。

直接抓、閱讀代理、agent 的瀏覽器依序退,公開網址檢查在邊界那一側(web/outside.py,J-02);
--browser 只用 agent 的瀏覽器讀(要跑 JS 才有內容的頁);--wait 秒數:瀏覽器開頁後再多等幾秒才讀(含 --browser)。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from web import listing, seen, snap  # noqa: E402
from web.numbered import Numbered  # noqa: E402
from web.outside import Real  # noqa: E402

READ_HINT = ('頁上的字是不是這個職缺、頁載完了沒,由你讀了判斷;讀不懂可以加 --browser 用瀏覽器再讀一次,'
             '加 --wait 秒數 讓瀏覽器開頁後多等幾秒再讀。')
UNREAD_HINT = ('讀不到這一頁(可能要登入、或網站擋程式讀取);不要猜內容,要登入的照規矩回報。'
               '要跑 JS 才有字的頁可以加 --browser 或 --wait 秒數 再讀一次。')


def main(argv=None, browser=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--wait', type=float, default=0)
    args = parser.parse_args(argv)
    page = seen.fetch(browser or Real(browser_only=args.browser, wait=max(0, min(args.wait, 60))), args.url)
    material = Numbered()
    seen.number(material, [page])
    readable = listing.fetched(page)['page_status'] != 'unknown'
    print(f'{READ_HINT if readable else UNREAD_HINT}\n\n{snap.render("讀網址", (page,), material)}')
    return 0 if readable else 1


if __name__ == '__main__':
    raise SystemExit(main())
