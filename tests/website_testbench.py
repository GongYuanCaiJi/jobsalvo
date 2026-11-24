import contextlib
import copy
import datetime
import html
import http.server
import json
import os
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools'))
sys.path.insert(0, TOOLS)

import apply_tab
from chrome_door import PROFILE_FN
import dev_browser


def _crop_quote(page, path, quote):
    try:
        clip = page.evaluate(dev_browser.chrome_door._quote_clip_function(), quote)
        if not clip:
            return False
        page.screenshot(path=os.path.abspath(path), clip=clip, type='png')
        return True
    except Exception:  # noqa: BLE001 - a failed crop falls back to the full-page screenshot
        return False


# 假的 door 用來量產品讀頁材料多大(read_workspace_pages)。
MATERIAL_SIZE = f"""() => {{
                try {{ return JSON.stringify(({PROFILE_FN})()).length; }} catch (error) {{ return -1; }}
}}"""




class FakeWebsiteServer:
    def __init__(self, initial_text='old text', pages=None, initial_pages=None):
        self._lock = threading.Lock()
        page_content = dict(initial_pages or {})
        page_content.setdefault('/', initial_text)
        self._state = {'content': page_content['/'], 'pages': page_content,
                       'saved': False, 'changes': []}
        self.discard_saves = False
        self._pages = dict(pages or {})
        self._pages.setdefault('/', self._home_page())
        self._server = None
        self._thread = None
        self._requests = []

    @staticmethod
    def _home_page():
        return ("<!doctype html><html><head><title>本機假網站</title></head><body>"
                "<h1>本機假網站</h1><p>伺服器端狀態測試</p>"
                '<label>內容<input name="content" value="{{content}}"></label>'
                '</body></html>')

    def start(self):
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _send(self, status, body, content_type):
                path = self.path.split('?', 1)[0]
                if path != '/state' and not path.startswith('/__testbench/'):
                    with owner._lock:
                        owner._requests.append({
                            'method': self.command, 'url': owner.url(self.path), 'status': status,
                            'at': datetime.datetime.now().astimezone().isoformat(timespec='microseconds')})
                data = body.encode('utf-8') if isinstance(body, str) else body
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = self.path.split('?', 1)[0]
                if path == '/state':
                    with owner._lock:
                        state = copy.deepcopy(owner._state)
                    return self._send(200, json.dumps(state), 'application/json; charset=utf-8')
                page = owner._pages.get(path)
                if page is None:
                    return self._send(404, 'not found', 'text/plain; charset=utf-8')
                with owner._lock:
                    value = copy.deepcopy(owner._state['pages'].get(path, ''))
                if isinstance(value, dict):
                    for key, text in value.items():
                        page = page.replace('{{' + str(key) + '}}', html.escape(str(text), quote=True))
                else:
                    page = page.replace('{{content}}', html.escape(str(value), quote=True))
                return self._send(200, page, 'text/html; charset=utf-8')

            def do_POST(self):
                if self.path != '/save':
                    return self._send(404, 'not found', 'text/plain; charset=utf-8')
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    body = json.loads(self.rfile.read(length) or b'{}')
                    path = str(body.get('path') or '/')
                    value = ({str(key): str(text) for key, text in body['values'].items()}
                             if isinstance(body.get('values'), dict) else str(body['content']))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    return self._send(400, 'invalid state', 'text/plain; charset=utf-8')
                with owner._lock:
                    before = copy.deepcopy(owner._state['pages'].get(path, ''))
                    change = {'before': before, 'after': value}
                    if path != '/':
                        change['path'] = path
                    if owner.discard_saves:
                        change['persisted'] = False
                    else:
                        owner._state['pages'][path] = value
                        if path == '/':
                            owner._state['content'] = value
                        owner._state['saved'] = True
                    owner._state['changes'].append(change)
                return self._send(204, b'', 'text/plain; charset=utf-8')

        self._server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def url(self, path='/'):
        if self._server is None:
            raise RuntimeError('假網站尚未啟動')
        return f'http://127.0.0.1:{self._server.server_port}{path}'

    def requests(self):
        """Every request the fake site answered, oldest first, except the testbench's own /state and
        /__testbench/ calls: [{'method', 'url' (full, with query), 'status', 'at' (ISO time)}]."""
        with self._lock:
            return copy.deepcopy(self._requests)

    def state(self):
        with urllib.request.urlopen(self.url('/state'), timeout=5) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected  本機假伺服器
            return json.loads(response.read())

class ChromeTestDoor(dev_browser.chrome_door.EgoDoor):
    """Expose the product's browser door over the Playwright pages owned by this testbench."""

    def __init__(self, bench):
        super().__init__(agent_id='website-testbench', runtime=dev_browser.chrome_door.CODEX)
        self.bench = bench
        self.workspace = {'id': 1, 'name': 'website-testbench', 'page': 'p1'}
        self.workspace_pages = []

    def _page_id(self, tab_id):
        label = str(tab_id).rsplit(':', 1)[-1]
        for page_id, page_label in self.bench._page_labels.items():
            if label in (page_id, page_label):
                if page_id in self.bench._closed_pages:
                    raise dev_browser.chrome_door.Unreachable(dev_browser.chrome_door.GONE)
                return page_id
        raise ValueError(f'測試台找不到這個分頁：{tab_id}')

    def agent_page_id(self, page):
        if not page:
            return ''
        label = self.bench._page_labels.get(str(page), str(page))
        return f'{self.workspace["id"]}:{label}'

    def ready(self, _board=None):
        return True, '', ''

    def open_for_agent(self, url, old_tab=None, on_open=None, **_kwargs):
        target = urlsplit(url)
        path = target.path + (f'?{target.query}' if target.query else '')
        page_id = next((key for key, current in self.bench._page_urls.items() if current == url), None)
        if page_id is None:
            page_id = next((key for key, current in self.bench._page_urls.items()
                            if urlsplit(current).path == target.path), None)
        if page_id is None:
            page_id = self.bench.open_page(path)
        self.workspace['page'] = self.bench._page_labels[page_id]
        opened = {'workspace': dict(self.workspace), 'tab_id': self.agent_page_id(self.workspace['page']),
                  'page': self.read_page(self.agent_page_id(self.workspace['page']))}
        if on_open:
            on_open(opened)
        return opened

    def follow_page(self, tab_id):
        page_id = self._page_id(tab_id)
        self.workspace['page'] = self.bench._page_labels[page_id]
        self.bench._page_id = page_id
        return self.agent_page_id(self.workspace['page'])

    def read_page(self, tab_id):
        return self.bench._read_form(self._page_id(tab_id))

    def fill_fields(self, tab_id, items):
        return self.bench._fill_fields(self._page_id(tab_id), items)

    def read_profile_page(self, tab_id):
        return self.bench.read_profile_page(self._page_id(tab_id))

    def read_profile(self, url, _board=None):
        target = urlsplit(url)
        page_id = next((key for key, current in self.bench._page_urls.items()
                        if current == url or urlsplit(current).path == target.path), None)
        if page_id is None:
            raise ValueError(f'測試台沒有這個網址：{url}')
        return self.read_profile_page(self.agent_page_id(self.bench._page_labels[page_id]))

    def read_workspace_pages(self):
        return self._open_pages()

    def _open_pages(self):
        """Read each open Playwright page the way ego reads a tab; the product classifies the result."""
        tabs = []
        for page_id in self.bench._page_labels:
            if page_id in self.bench._closed_pages:
                continue
            row = {'tab_id': self.agent_page_id(page_id), 'url': self.bench._current_url(page_id), 'title': ''}
            if page_id in self.bench._unreadable_pages:
                row['error'] = self.bench._unreadable_pages[page_id]
            else:
                try:
                    data = dict(self.bench.read_profile_page(page_id), tab_id=row['tab_id'])
                except Exception as error:  # noqa: BLE001 — a page reader error is what ego reports for that tab
                    row['error'] = str(error)
                else:
                    row.update(url=data.get('url') or row['url'], title=data.get('title') or '',
                               size=self.bench.material_chars(page_id, row['tab_id']))
                    if row['size'] <= dev_browser.chrome_door.PAGE_MATERIAL_CHAR_LIMIT:
                        row['data'] = data
            tabs.append(row)
        if self.bench._blank_left:   # ego 給的這一頁沒有標籤,程式讀工作區時照 chrome_door 的讀法記
            tabs.append({'tab_id': f'{self.workspace["id"]}:', 'url': 'about:blank', 'title': '',
                         'error': dev_browser.chrome_door.NO_TAB_LABEL,
                         'screenshot_error': dev_browser.chrome_door.NO_TAB_LABEL})
        return dev_browser.chrome_door.workspace_pages(tabs)

    def read_pages(self, urls, board=None, ready=None):
        return [self.read_profile(url, board) for url in urls]

    def reload_profile_page(self, tab_id, url=None):
        page_id = self._page_id(tab_id)
        self.bench.reload_page(page_id, url or self.bench._page_urls[page_id])

    def shot(self, tab_id, path, quote=''):
        return self.bench.screenshot(self._page_id(tab_id), path, quote)

    def release(self, _tab_id=None):
        self.workspace_pages = self._open_pages()
        return 'released'

    def hand_off(self):
        return None

    def _hand_off_page(self):
        page = self.workspace.get('handoff_page') or self.workspace['page']
        self.bench.handed_off.append(page)
        return {'ownership': 'user', 'page': page}

    def _rules(self):
        door = dev_browser.chrome_door.EgoDoor(self.agent_id, self.runtime)
        door.workspace = self.workspace
        return door

    def apply_rule(self):
        return self._rules().apply_rule()

    def fetch_task_tools(self):
        return self._rules().fetch_task_tools()


class WebsiteTestBench:
    def __init__(self, initial_text='old text', pages=None, initial_pages=None, evidence_dir=None):
        self.server = FakeWebsiteServer(initial_text, pages, initial_pages)
        self.handed_off = []
        self.browser = None
        self._browser_context = None
        self._page_id = None
        self._page_labels = {}
        self._page_urls = {}
        self._page_refs = {}
        self._closed_pages = set()
        self._evidence = None
        self._evidence_dir = os.path.abspath(evidence_dir) if evidence_dir else None
        self._round = None
        self._agent_script_n = 0
        self._unreadable_pages = {}
        self._broken_screenshots = {}
        self._blank_left = False       # Chrome 路徑照 ego:關掉最後一頁後留一個沒有標籤的空白分頁

    def __enter__(self):
        self.server.start()
        if self._evidence_dir:
            os.makedirs(self._evidence_dir, exist_ok=True)
        else:
            self._evidence = tempfile.TemporaryDirectory(prefix='jobsalvo-website-testbench-')
        self._browser_context = dev_browser.page()
        try:
            self.browser = self._browser_context.__enter__()
            if isinstance(self.browser, dev_browser.Chrome):
                self.browser.door = ChromeTestDoor(self)
        except BaseException:
            self.server.stop()
            if self._evidence:
                self._evidence.cleanup()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            return self._browser_context.__exit__(exc_type, exc, traceback)
        finally:
            self.server.stop()
            if self._evidence:
                self._evidence.cleanup()

    @property
    def evidence_dir(self):
        return self._evidence_dir or self._evidence.name

    @staticmethod
    def _with_tab_id(material, tab_id):
        return material + len(',"tab_id":') + len(json.dumps(tab_id or '', ensure_ascii=False))

    def material_chars(self, page_id, tab_id):
        """Size of the page reader's material plus its tab id, as JSON.stringify counts it in the page (the
        public PAGE_MATERIAL_CHAR_LIMIT is defined on that); -1 when the page reader cannot produce material."""
        material = self._evaluate(page_id, MATERIAL_SIZE)
        return material if material < 0 else self._with_tab_id(material, tab_id)

    def open_page(self, path='/'):
        self._blank_left = False      # 新開的頁頂替那個空白分頁
        page_id = f'p{len(self._page_labels) + 1}'
        url = self.server.url(path)
        if not self._page_labels:
            self.browser.goto(url)
            label = 'p1'
            self._page_refs[page_id] = None
        elif isinstance(self.browser, dev_browser.Ego):
            door = self.browser.door
            label = dev_browser.chrome_door._ego(door._task(page=False) +
                                                 'const page = await task.newPage();\n'
                                                 f'await page.goto({json.dumps(url)},{{waitUntil:"load"}});\n'
                                                 'return page.label;')
            page_id = label
            self._page_refs[page_id] = None
        else:
            page = self.browser.page.context.new_page()
            page.goto(url, wait_until='load')
            label = page_id
            self._page_refs[page_id] = page
        self._page_labels[page_id] = label
        self._page_urls[page_id] = url
        self._page_id = page_id
        if isinstance(self.browser, dev_browser.Chrome):
            self.browser.door.workspace['page'] = label
        return page_id

    def agent_page_id(self, page_id):
        if page_id not in self._page_labels:
            raise ValueError('測試台找不到這個分頁')
        if isinstance(self.browser, dev_browser.Ego):
            return f'{self.browser.door.workspace["id"]}:{self._page_labels[page_id]}'
        return self.browser.door.agent_page_id(self._page_labels[page_id])

    def _evaluate(self, page_id, expression):
        if page_id not in self._page_labels:
            raise ValueError('測試台找不到這個分頁')
        if isinstance(self.browser, dev_browser.Ego):
            door = self.browser.door
            label = json.dumps(self._page_labels[page_id])
            return dev_browser.chrome_door._ego(door._task(page=False) +
                                                f'const page = task.page({label});\n'
                                                f'return await page.evaluate({expression});')
        page = self._page_refs[page_id] or self.browser.page
        return page.evaluate(expression)

    def read_page(self, page_id):
        return self.browser.door.read_page(self.agent_page_id(page_id))

    def read_profile_page(self, page_id):
        return self._evaluate(page_id, PROFILE_FN)

    def _read_form(self, page_id):
        """Playwright 的測試門把 CDP 回應交給正式 READ_FORM 函式處理。"""
        from playwright.sync_api import Error as PlaywrightError

        if page_id not in self._page_labels:
            raise ValueError('測試台找不到這個分頁')
        page = self._page_refs[page_id] or self.browser.page
        cdp = self.browser.cdp if page is self.browser.page else page.context.new_cdp_session(page)
        detach = cdp is not self.browser.cdp
        try:
            frame_tree = None
            with contextlib.suppress(PlaywrightError):
                frame_tree = cdp.send('Page.getFrameTree', {}).get('frameTree')

            frames = []

            def walk(tree):
                if not tree:
                    return
                frames.append(tree['frame']['id'])
                for child in tree.get('childFrames') or []:
                    walk(child)

            walk(frame_tree)
            if not frames:
                frames = [None]
            trees, descriptions, files = {}, {}, {}
            for frame_id in frames:
                nodes = []
                with contextlib.suppress(PlaywrightError):
                    params = {'frameId': frame_id} if frame_id else {}
                    nodes = cdp.send('Accessibility.getFullAXTree', params).get('nodes') or []
                trees[str(frame_id or '')] = nodes
                for node in nodes:
                    backend_id = node.get('backendDOMNodeId')
                    if backend_id is None or str(backend_id) in descriptions:
                        continue
                    description = {}
                    with contextlib.suppress(PlaywrightError):
                        description = cdp.send('DOM.describeNode', {'backendNodeId': backend_id})
                    descriptions[str(backend_id)] = description
                    attributes = (description.get('node') or {}).get('attributes') or []
                    if dict(zip(attributes[::2], attributes[1::2])).get('type') == 'file':
                        with contextlib.suppress(PlaywrightError):
                            handle = cdp.send('DOM.resolveNode', {'backendNodeId': backend_id})
                            files[str(backend_id)] = cdp.send('Runtime.callFunctionOn', {
                                'objectId': handle['object']['objectId'], 'returnByValue': True,
                                'functionDeclaration': 'function(){return [...(this.files || [])].map(f => f.name);}'
                            })['result'].get('value') or []
            reader = """async ({frameTree, trees, descriptions, files}) => {
                const page = {
                    cdp: async (method, params={}) => {
                        if (method === 'Page.getFrameTree') {
                            if (!frameTree) throw new Error('Page.getFrameTree unavailable');
                            return {frameTree};
                        }
                        if (method === 'Accessibility.getFullAXTree')
                            return {nodes: trees[String(params.frameId || '')] || []};
                        if (method === 'DOM.describeNode')
                            return descriptions[String(params.backendNodeId)] || {};
                        if (method === 'DOM.resolveNode' && files[String(params.backendNodeId)])
                            return {object: {objectId: String(params.backendNodeId)}};
                        if (method === 'Runtime.callFunctionOn' && files[params.objectId])
                            return {result: {value: files[params.objectId]}};
                        throw new Error('Unsupported CDP method: ' + method);
                    },
                    url: async () => location.href,
                    title: async () => document.title
                };
                return await (__READ_FORM__)(page);
            }""".replace('__READ_FORM__', apply_tab.READ_FORM)
            return page.evaluate(reader, {'frameTree': frame_tree, 'trees': trees,
                                          'descriptions': descriptions, 'files': files})
        finally:
            if detach:
                cdp.detach()

    def _fill_fields(self, page_id, items):
        """正式的 apply_tab.FILL_FN 在測試台的分頁上跑。同步版 Playwright 不能在頁面回呼裡再操作頁面,
        所以 FILL_FN 收到的 page 是頁內的替身:backend 編號先由 CDP 換成元素上的標記,滑鼠與鍵盤在頁內派發事件。"""
        page = self._page_refs[page_id] or self.browser.page
        cdp = self.browser.cdp if page is self.browser.page else page.context.new_cdp_session(page)
        try:
            for item in items:
                handle = cdp.send('DOM.resolveNode', {'backendNodeId': item['backend']})
                cdp.send('Runtime.callFunctionOn', {
                    'objectId': handle['object']['objectId'], 'arguments': [{'value': str(item['backend'])}],
                    'functionDeclaration': 'function(id){this.setAttribute("data-testbench-backend", id);}'})
        finally:
            if cdp is not self.browser.cdp:
                cdp.detach()
        shim = """async (items) => {
            const at = id => document.querySelector(`[data-testbench-backend="${id}"]`);
            const page = {
                cdp: async (method, params) => {
                    if (method === 'DOM.resolveNode') return {object: {objectId: String(params.backendNodeId)}};
                    if (method !== 'Runtime.callFunctionOn') throw new Error('Unsupported CDP method: ' + method);
                    const fn = (0, eval)('(' + params.functionDeclaration + ')');
                    return {result: {value: await fn.apply(at(params.objectId), (params.arguments || []).map(a => a.value))}};
                },
                mouse: {click: async (x, y) => {
                    const target = document.elementFromPoint(x, y);
                    if (!target) return;
                    const at = {bubbles: true, cancelable: true, clientX: x, clientY: y};
                    target.dispatchEvent(new PointerEvent('pointerdown', at));
                    target.dispatchEvent(new MouseEvent('mousedown', at));
                    if (typeof target.focus === 'function') target.focus();
                    target.dispatchEvent(new PointerEvent('pointerup', at));
                    target.dispatchEvent(new MouseEvent('mouseup', at));
                    target.dispatchEvent(new MouseEvent('click', at));
                }},
                keyboard: {
                    type: async text => { for (const c of text) document.execCommand('insertText', false, c); },
                    insertText: async text => { document.execCommand('insertText', false, text); },
                },
                waitForTimeout: ms => new Promise(done => setTimeout(done, ms)),
                evaluate: async (fn, arg) => fn(arg),
            };
            return await (__FILL_FN__)(page, items);
        }""".replace('__FILL_FN__', apply_tab.FILL_FN)
        return page.evaluate(shim, items)

    def reload_page(self, page_id, url):
        if page_id not in self._page_labels:
            raise ValueError('測試台找不到這個分頁')
        if isinstance(self.browser, dev_browser.Ego):
            label = json.dumps(self._page_labels[page_id])
            target = json.dumps(url)
            dev_browser.chrome_door._ego(self.browser.door._task(page=False) +
                                         f'const page = task.page({label});\n'
                                         f'return await page.goto({target},{{waitUntil:"load"}});')
        else:
            page = self._page_refs[page_id] or self.browser.page
            page.goto(url, wait_until='load')

    def break_screenshots(self, page_id, reason):
        """A fake-site state: this page stays readable, but every screenshot of it fails with ``reason``.
        CI only: under ego the screenshots are taken by ego itself, which the testbench cannot make fail."""
        if isinstance(self.browser, dev_browser.Ego):
            raise RuntimeError('ego 模式的截圖由 ego 自己拍,測試台造不出截圖失敗')
        self._broken_screenshots[page_id] = str(reason)

    def screenshot(self, page_id, path, quote=''):
        if page_id not in self._page_labels:
            raise ValueError('測試台找不到這個分頁')
        if page_id in self._broken_screenshots:
            raise RuntimeError(self._broken_screenshots[page_id])
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if isinstance(self.browser, dev_browser.Ego):
            label = json.dumps(self._page_labels[page_id])
            dev_browser.chrome_door._ego(self.browser.door._task(page=False) +
                                         f'const page = task.page({label});\n'
                                         + dev_browser.chrome_door._screenshot_script(path, quote))
        else:
            page = self._page_refs[page_id] or self.browser.page
            cropped = quote and _crop_quote(page, path, quote)
            if not cropped:
                page.screenshot(path=os.path.abspath(path), full_page=True, type='png')
        return path

    def run_agent_script(self, page_id, script, *, finish_after=False):
        if isinstance(self.browser, dev_browser.Ego):
            import agent_run
            import evidence

            workspace = self.browser.door.workspace['id']
            label = self._page_labels[page_id]
            source = (f'const task=await taskSpace({json.dumps(workspace)});\n'
                      f'const page=task.page({json.dumps(label)});\n'
                      f'const result=await page.evaluate({script});\n'
                      + ('await task.finish({keep:[]});\n' if finish_after else '')
                      + 'console.log("@@website-testbench-result@@"+JSON.stringify(result??null));\n')
            if self._round is None:
                with patch.dict(os.environ, {'EVIDENCE_TMP': self.evidence_dir}):
                    self._round = evidence.Round(
                        'website-testbench', 'agent-script', board=os.path.join(self.evidence_dir, 'board.html'))
            self._agent_script_n += 1
            outfile = os.path.join(self.evidence_dir, f'agent-{self._agent_script_n}.log')
            fake_agent = ('import subprocess,sys; '
                          "result=subprocess.run(['ego-browser','nodejs'],input=sys.stdin.buffer.read()); "
                          'raise SystemExit(result.returncode)')
            with evidence.activated(self._round), \
                    patch.object(agent_run, '_agent_entry', return_value={'id': 'website-testbench', 'runtime': 'codex'}), \
                    patch.object(agent_run, 'argv_for', return_value=([sys.executable, '-c', fake_agent], TOOLS)), \
                    patch.object(agent_run, 'prompt_stdin', return_value=source):
                process = agent_run.launch(source, outfile, TOOLS, chrome=True)
                process.wait(timeout=90)
            output = Path(outfile).read_text(encoding='utf-8').splitlines()
            if process.returncode:
                raise RuntimeError('\n'.join(output))
            marker = '@@website-testbench-result@@'
            line = next((line for line in reversed(output) if marker in line), None)
            if line is None:
                raise RuntimeError('\n'.join(output))
            return json.loads(line.split(marker, 1)[1])
        return self._evaluate(page_id, script)

    def bridge_events(self):
        if self._round is None:
            return []
        path = Path(self._round.dirs[0]) / 'events.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

    def bridge_shot_path(self, filename):
        return Path(self._round.dirs[0]) / filename

    def read_open_pages(self):
        """The open pages exactly as the product's workspace reader lists them."""
        pages = self.browser.door.read_workspace_pages()
        if isinstance(self.browser, dev_browser.Ego):
            unreadable = {self.agent_page_id(page_id): reason for page_id, reason in self._unreadable_pages.items()}
            pages = [dev_browser.chrome_door.workspace_pages([{
                         'tab_id': page['tab_id'], 'url': page.get('url') or '', 'title': page.get('title') or '',
                         'error': unreadable[page['tab_id']]}])[0]
                     if page.get('tab_id') in unreadable else page for page in pages]
        return pages

    def _current_url(self, page_id):
        if isinstance(self.browser, dev_browser.Ego):
            return self._page_urls.get(page_id, '')
        return (self._page_refs[page_id] or self.browser.page).url

    def capture_product_profile(self, phase, directories=None):
        """Run the same reader and capture implementation against either browser path."""
        if directories is None:
            import evidence

            active = evidence.active()
            directories = list(active.dirs) if active else [self.evidence_dir]
        from web.outside import Real
        from web.snap import render, take

        snapshot = take(Real(self.browser.door), phase, directories)
        return {'snapshot': snapshot, 'output': render(snapshot.phase, snapshot.pages, snapshot.material)}

    def server_state(self):
        return self.server.state()
