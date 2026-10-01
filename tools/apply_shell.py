#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_shell —— 把版控裡的看板外殼(CSS/JS/頁首)灌回板子 HTML。

看板是一個自帶資料的 HTML 檔,很大、不進版控。樣式和程式如果只存在那個檔裡,
(1)外觀改動沒有可 diff 的來源,(2)一支補丁腳本寫錯一個欄位就把整段 CSS 蓋成 JSON,救不回來。
所以來源是這三個檔,板子只是它們的投影:

  board/board.css    → <style id="sty">
  board/board.js     → <script id="app-src">
  board/header.html  → <template id="t-hdr">

職缺資料(data-jobs)和使用者的標記(data-fb)逐字保留,不碰。

改了 board/ 的外殼後,reconcile 會自動呼叫這支(它按外殼雜湊決定要不要灌)。
一般不用手跑;要單獨灌某個 html 才直接用。

用法:python3 tools/apply_shell.py            # 灌現行看板
     uv run python tools/apply_shell.py <html>…   # 指定檔案
"""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_doc as bd
import config as cf

SRC = cf.BOARD_SRC
CSS, JS, HDR = (os.path.join(SRC, f) for f in ('board.css', 'board.js', 'header.html'))


def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def problem(css, js, hdr):
    """擋掉「灌錯欄位」這一類事故:去年就是把 fb JSON 寫進 <style> 才整版沒樣式。沒問題回 ''。"""
    if ':root{' not in css.replace(' ', ''):
        return 'board.css 看起來不是 CSS(找不到 :root{),不敢灌。'
    if 'JSON.parse' not in js or 'renderApp' not in js:
        return 'board.js 看起來不是看板程式,不敢灌。'
    if '</script>' in js:
        return 'board.js 含字面 </script>,會把文件截斷。'
    if 'id="savebar"' not in hdr or 'id="tabs"' in hdr:
        return 'header.html 不像頁首模板(要有 savebar、不該有 tabs),不敢灌。'
    return ''


def current():
    """程式碼裡現在的外殼 (css, js, hdr);讀不到或不像看板外殼回 None。看板伺服器送頁面用。"""
    try:
        css, js, hdr = read(CSS), read(JS), read(HDR)
    except OSError:
        return None
    return None if problem(css, js, hdr) else (css, js, hdr)


def apply_to(path, css, js, hdr):
    """外殼灌進 path 那一份看板:走看板檔唯一的寫入(同一把鎖),重灌途中他存的標記不會被讀舊的那份蓋掉。"""
    def put(p):
        p['sty'], p['thdr'], p['app'] = css, hdr, js
        return p
    p = bd.rewrite(put, path, by='apply_shell')
    marks = sum(1 for k, v in p['fb'].items() if isinstance(v, dict) and v.get('s'))
    print(f'  {os.path.basename(path)}: 職缺 {len(p["data"]["jobs"])} · '
          f'標記(保) {marks} · {os.path.getsize(path)//1024} KB')


def main():
    css, js, hdr = read(CSS), read(JS), read(HDR)
    if bad := problem(css, js, hdr):
        sys.exit(bad)
    targets = sys.argv[1:] or [cf.LIVE]
    todo = [t for t in targets if os.path.isfile(t)]
    if not todo:
        sys.exit('沒有可灌的板子檔。')
    print(f'外殼 CSS {len(css)} · JS {len(js)} · 頁首 {len(hdr)} 字元 →')
    for t in todo:
        apply_to(t, css, js, hdr)


if __name__ == '__main__':
    main()
