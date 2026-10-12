#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dev_browser —— 開發工具(看板檢查、介面截圖)開瀏覽器的唯一入口。
本機跟產品走同一條路:ego(chrome_door.EgoDoor,開自己的測試工作區,跑完收掉)。
沒有 ego 的地方(CI 的 Linux)用 Playwright + 那台機器的 Chrome(chrome_bin)。
兩邊都只給開發工具用得到的幾件事:goto、run(一條檢查一次呼叫:尺寸 → 裝上輔助腳本 → 跑)、screenshot。"""
import contextlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chrome_door  # noqa: E402

# 檢查的尺寸(CDP 模擬,手機連觸控一起);desktop / None 是不模擬
VIEWPORTS = {'mobile': {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True},
             'wide': {'width': 1440, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False}}
# 頁面裡的確認框一律當成按了確定(例如按停止時的確認框)
DIALOGS = 'window.confirm=function(){return true;};window.alert=function(){};window.prompt=function(m,d){return d||"";};'


# 在頁面裡開跑、結果寫進 window.__dev(ego 的 page.evaluate 有 15 秒安全逾時,長的檢查要另外等)
START = 'window.__dev=undefined;Promise.resolve(%s).then(function(v){window.__dev={v:v};},function(e){window.__dev={e:String(e)};});1'


def _size(viewport):
    """要送的 CDP 指令:(方法, 參數) 清單。viewport 是 VIEWPORTS 的名字或 {'width','height','deviceScaleFactor','mobile'}。"""
    m = VIEWPORTS.get(viewport) if isinstance(viewport, str) else viewport
    return ([('Emulation.setDeviceMetricsOverride', m) if m else ('Emulation.clearDeviceMetricsOverride', {})]
            + [('Emulation.setTouchEmulationEnabled',
                {'enabled': True, 'maxTouchPoints': 1} if m and m.get('mobile') else {'enabled': False})])


class Ego:
    """ego 的一頁。每次呼叫是一支 ego-browser nodejs:CDP 的尺寸模擬不跨呼叫,所以尺寸每次跟著送。
    ego 的 page.evaluate 有 15 秒安全逾時:檢查先在頁面裡開跑,結果寫進 window,再等它出現。"""

    def __init__(self):
        self.door = chrome_door.EgoDoor()
        self.door.open_for_agent('about:blank')

    def _js(self, script, timeout=60):
        return chrome_door._ego(self.door._task() + script, timeout)

    @staticmethod
    def _sized(viewport):
        return ''.join(f'await page.cdp({json.dumps(m)},{json.dumps(p)});\n' for m, p in _size(viewport))

    def goto(self, url):
        self._js(f'await page.goto({json.dumps(url)},{{waitUntil:"load",timeout:60000}});\n')

    def run(self, expr, prelude='', viewport=None, goto=None, timeout=600):
        """(可選)先開 goto 那一頁,照 viewport 設尺寸,裝上 prelude,跑 expr(可以回 Promise),回它的值。"""
        return self._js(
            (f'await page.goto({json.dumps(goto)},{{waitUntil:"load",timeout:60000}});\n'
             'await page.waitForTimeout(2500);\n' if goto else '')
            + 'await page.waitForFunction(()=>document.readyState==="complete",undefined,{timeout:60000});\n'
            + self._sized(viewport) + ('await page.waitForTimeout(200);\n' if viewport else '')
            + f'await page.evaluate({json.dumps(DIALOGS + prelude + ";1")});\n'
            'const t0=await page.evaluate("performance.timeOrigin");\n'
            f'await page.evaluate({json.dumps(START % expr)});\n'
            f'await page.waitForFunction(t=>window.__dev!==undefined||performance.timeOrigin!==t,t0,{{timeout:{timeout * 1000}}});\n'
            'const r=await page.evaluate("window.__dev");\n'
            'if(!r)throw new Error("頁面在檢查途中重新載入了");\n'
            'if(r.e)throw new Error(r.e);\n'
            'return r.v;', timeout + 30)

    def screenshot(self, path, full=False, viewport=None):
        self._js(self._sized(viewport)
                 + f'await page.screenshot({{path:{json.dumps(os.path.abspath(path))},fullPage:{json.dumps(full)},raw:true}});\n')


class Chrome:
    """Playwright + Chrome 的一頁(CI)。"""

    def __init__(self, page, cdp):
        self.page, self.cdp = page, cdp

    def goto(self, url):
        self.page.goto(url, wait_until='load', timeout=60000)

    def run(self, expr, prelude='', viewport=None, goto=None, timeout=600):
        if goto:
            self.goto(goto)
            self.page.wait_for_timeout(2500)
        self.page.wait_for_load_state('load', timeout=60000)
        for m, p in _size(viewport):
            self.cdp.send(m, p)
        if viewport:
            self.page.wait_for_timeout(200)
        self.page.evaluate(DIALOGS + prelude + ';1')
        return self.page.evaluate(expr)

    def screenshot(self, path, full=False, viewport=None):
        for m, p in _size(viewport):
            self.cdp.send(m, p)
        self.page.screenshot(path=path, full_page=full)


@contextlib.contextmanager
def page():
    """開一頁,用完收掉。有 ego 用 ego(這一輪開的工作區照名字全部收掉,中途出錯也收),沒有才用 Chrome。"""
    if chrome_door.ego_bin():
        with chrome_door.test_workspaces():
            yield Ego()
        return
    from playwright.sync_api import sync_playwright
    import chrome_bin
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chrome_bin.chrome()[0], args=['--disable-gpu', '--hide-scrollbars'])
        try:
            p = browser.new_context(no_viewport=True).new_page()   # 尺寸一律用 CDP 模擬(手機要連觸控一起)
            yield Chrome(p, p.context.new_cdp_session(p))
        finally:
            browser.close()
