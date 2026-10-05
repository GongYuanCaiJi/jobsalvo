#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shot —— 自己把看板截圖出來看,不依賴任何人幫忙開視窗。

為什麼要有這支:改完 UI 要自己看過才算數,不能靠別人幫忙開視窗、或靠一個會被收起來就不繪製的窗格。

為什麼不用 `chrome --headless --screenshot` 就好:--window-size 的版面寬度有 500px
的下限,手機是 390px。差這 110px 正好是「會不會折行」的分界,量不到就等於沒驗。
所以用 Playwright 開瀏覽器(https://playwright.dev/python/):任意寬度、行動裝置模擬,
而且可以先把畫面操作到指定狀態(切分頁、展開公司、打開詳細)再截。

**一律開在看板的副本上。** 對著正在用的看板點按鈕做測試,會把使用者選好的履歷版本、
語言點掉。測試不可以碰使用者的資料:這支自己複製一份、用自己的 server 起在隨機埠、
截完就關,原檔完全不動。

用法:
  uv run python tools/shot.py --tab ready --width 390            # 手機
  uv run python tools/shot.py --tab ready --width 1440 --expand  # 桌機
  uv run python tools/shot.py --tab ready --focus 1245204b       # 捲到某個職缺
  uv run python tools/shot.py --tab ready --full                 # 整頁
  uv run python tools/shot.py --js "…"                           # 截圖前先跑一段自己的 JS
"""
import sys, os, json, argparse, subprocess, tempfile, shutil, time, urllib.parse, urllib.request, socket, contextlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


import board_doc as bd

from chrome_bin import chrome as _chrome   # 用哪個 Chrome 的單一真相(見 tools/chrome_bin.py)


def sandbox_api(base, path, body=None, method=None, headers=None, timeout=10, raw=False):
    """看板檢查打副本看板的 API 只走這一支:網址一定是自己開在本機的那一份(先驗過),不會打到別的地方。
    body 是 dict 就送 JSON、bytes 就原樣送;回 JSON(raw=True 回位元組)。"""
    base = str(base or '').rstrip('/')
    u = urllib.parse.urlsplit(base)
    if u.scheme != 'http' or u.hostname not in ('127.0.0.1', 'localhost') or not path.startswith('/api/'):
        raise ValueError(f'看板檢查只打本機副本看板的 /api/:{base}{path}')
    data = json.dumps(body).encode('utf-8') if isinstance(body, dict) else body
    hdr = dict(headers or {}, **({'Content-Type': 'application/json'} if isinstance(body, dict) else {}))
    req = urllib.request.Request(base + path, data=data, method=method or ('POST' if data is not None else 'GET'), headers=hdr)
    # 上面驗過只打本機副本看板的 /api/
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        out = r.read()
    return out if raw else json.loads(out)


# 這一輪開的副本看板網址:board_check 開好副本後填進來。放這裡(不放 board_check):board_check 是 __main__,
# 別的地方另外 import 它會拿到沒填網址的第二份
SB_URL = ['']
FLOW_OFF = {'like_to_prep': False, 'auto_prep': False, 'auto_advance': False, 'auto_fill': False, 'replies_at': ''}


def flow(**on):
    """副本的自動流程:全部關著,只開 on 給的那幾樣。透過副本伺服器的設定 API 改(跟他在設定頁按儲存同一條路)。"""
    settings = sandbox_api(SB_URL[0], '/api/settings').get('settings') or {}
    settings['flow'] = dict(FLOW_OFF, **on)
    return sandbox_api(SB_URL[0], '/api/settings', {'settings': settings})


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


class Sandbox:
    """把看板複製一份,用同一支 board_server 起在隨機埠。點按鈕只會改到副本。"""

    def __init__(self, board):
        self.dir = tempfile.mkdtemp(prefix='shot-board-')
        self.copy = os.path.join(self.dir, 'board.html')
        shutil.copyfile(board, self.copy)
        self.port = free_port()
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, 'board_server.py'),
             '--port', str(self.port), '--host', '127.0.0.1', '--state', self.copy],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # 副本產 PDF 用開發用的 Chrome(dev_pdf),不去叫使用者的 ego;CI 的 Linux 上也沒有 ego
            env=dict(os.environ, AGENT_BOARD=self.copy, JOBSALVO_DEV_PDF='1'))
        self.url = f'http://localhost:{self.port}'
        for _ in range(80):
            try:
                urllib.request.urlopen(self.url, timeout=1); return
            except OSError:   # 還沒起來(連不上、逾時都是 OSError)
                time.sleep(0.1)
        self.close(); sys.exit('副本 server 起不來')

    def close(self):
        try: self.proc.terminate(); self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):   # 叫不停:直接收掉
            with contextlib.suppress(OSError):   # 已經結束了
                self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


SETUP = """(async function(){
  var C=%s;
  function sleep(ms){return new Promise(function(r){setTimeout(r,ms);});}
  if(C.tab){var t=document.querySelector('.tab[data-tab="'+C.tab+'"]'); if(t)t.click(); await sleep(400);}
  if(C.expand){[].slice.call(document.querySelectorAll('.cohead')).slice(0,C.expandMax).forEach(function(h){
      if(h.nextElementSibling&&h.nextElementSibling.style.display==='none')h.click();}); await sleep(400);}
  if(C.detail){document.querySelectorAll('.cmore,.vwhy').forEach(function(d){d.open=true;}); await sleep(250);}
  if(C.focus){var c=[].slice.call(document.querySelectorAll('article[data-fid]'))
      .filter(function(x){return x.getAttribute('data-fid').indexOf(C.focus)>=0;})[0];
    if(c){c.scrollIntoView({block:'start'}); window.scrollBy(0,-C.offset);} await sleep(300);}
  else if(C.scroll){window.scrollTo(0,C.scroll); await sleep(250);}
  return {cards:document.querySelectorAll('article[data-fid]').length,
          w:document.documentElement.clientWidth,
          h:document.documentElement.scrollHeight};
})()"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--board', default=bd.LIVE)
    ap.add_argument('--url', help='改截這個網址(預設:自動開一份看板副本,不動原檔)')
    ap.add_argument('--tab'); ap.add_argument('--width', type=int, default=390)
    ap.add_argument('--height', type=int, default=844)
    ap.add_argument('--expand', action='store_true'); ap.add_argument('--expand-max', type=int, default=40)
    ap.add_argument('--detail', action='store_true')
    ap.add_argument('--focus'); ap.add_argument('--offset', type=int, default=80)
    ap.add_argument('--scroll', type=int, default=0)
    ap.add_argument('--full', action='store_true', help='整頁截圖')
    ap.add_argument('--js', help='截圖前再跑一段自己的 JS')
    ap.add_argument('--out', default='/tmp/board-shot.png')
    a = ap.parse_args()

    # 一律開副本:截圖過程會點按鈕,絕不能動到他正在用的看板
    sandbox = None
    if not a.url:
        sandbox = Sandbox(a.board)
        url = sandbox.url
    else:
        url = a.url
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=_chrome()[0], args=['--disable-gpu', '--hide-scrollbars'])
            mobile = a.width < 720
            ctx = browser.new_context(viewport={'width': a.width, 'height': a.height},
                                      device_scale_factor=2, is_mobile=mobile, has_touch=mobile)
            page = ctx.new_page()
            page.goto(url, wait_until='load', timeout=60000)
            page.wait_for_timeout(900)
            cfg = {'tab': a.tab, 'expand': a.expand, 'expandMax': a.expand_max, 'detail': a.detail,
                   'focus': a.focus, 'offset': a.offset, 'scroll': a.scroll}
            info = page.evaluate(SETUP % json.dumps(cfg, ensure_ascii=False))
            if a.js:
                page.evaluate(a.js)
            page.screenshot(path=a.out, full_page=a.full)
            browser.close()
        kb = os.path.getsize(a.out) // 1024
        print(f"{a.tab or '首頁'} · 版面 {info['w']}px(要求 {a.width})"
              f"{'·行動裝置' if mobile else ''} · 卡片 {info['cards']} 張"
              f"{' · 整頁' if a.full else ''} → {a.out}({kb} KB)")
        if info['w'] != a.width:
            print(f"  ⚠ 版面寬度不是要求的 {a.width},這一張不能拿來判斷折行")
    finally:
        if sandbox: sandbox.close()


if __name__ == '__main__':
    main()
