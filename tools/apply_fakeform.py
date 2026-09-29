#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_fakeform —— 驗收代投用的假職缺表單(只開在本機 127.0.0.1)。

代投驗收不能拿 agent 自己的回報當證據,也不能對真的職缺按送出。這個假表單長得像 Lever 的申請表,
頁面上的小程式會一直把「現在每一格的值」回報給這個伺服器,所以驗收程式看得到伺服器這邊的真相:
  · 頁面上每一格現在是什麼值、選了哪個上傳檔(不是 agent 說它填了什麼)。
  · 是不是同一個頁面:每次載入頁面都有一個新的 inst;接手修改時 inst 沒變、GET 次數沒變,才是在原本那頁上改。
  · 分頁還在不在:頁面每 3 秒回報一次,分頁關掉就停(背景分頁 Chrome 會放慢到約每分鐘一次)。
  · 有沒有搶畫面:回報帶著 visibilityState / hasFocus 和所在視窗的位置大小,分頁被切到前面會看到 visible,
    對照螢幕上的視窗清單就知道它是蓋在他正在用的那個 Chrome 視窗上,還是另開的視窗。
  · 有沒有被送出、送出了什麼:POST /submit 伺服器自己記,連上傳的檔名、大小、sha1。

用法(給 apply_accept 呼叫):
  srv = FakeForm(); srv.start(); url = srv.url('accept'); ...; srv.events(); srv.stop()
"""
import json, time, hashlib, threading, email.parser, email.policy
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 表單上的題目。name 是欄位名,q 是頁面上看到的題目(agent 記進答案庫用的也是這句)。
FIELDS = [
    ('name', 'Full name', 'text'),
    ('email', 'Email', 'email'),
    ('phone', 'Phone', 'tel'),
    ('location', 'Current location', 'text'),
    ('links', 'GitHub or portfolio URL', 'url'),
    ('nationality', 'What is your nationality?', 'text'),
    ('visa', 'Will you require visa sponsorship to work in Taiwan?', 'select:|Yes|No'),
    ('years', 'How many years of professional experience do you have in security or risk work?', 'text'),
    ('why', 'Why are you interested in this role? (required)', 'textarea'),
]

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Acceptance Test Co — Risk Analyst (TEST FORM, not a real job)</title>
<style>body{font:15px/1.5 system-ui;max-width:720px;margin:24px auto;padding:0 16px}
label{display:block;margin:14px 0 4px;font-weight:600}input,select,textarea{width:100%;padding:8px;font:inherit;box-sizing:border-box}
textarea{height:120px}.banner{background:#fee;border:1px solid #c33;padding:8px;margin-bottom:16px}
button{margin-top:20px;padding:10px 18px;font:inherit}</style></head><body>
<div class="banner" data-local-acceptance="true">本機假驗收頁 (127.0.0.1). This is a local acceptance-test form; it is not a real job and submissions go nowhere.</div>
<h1>Risk Analyst — Acceptance Test Co</h1>
<form id="app" method="post" action="/submit?job={job}" enctype="multipart/form-data">
{upload_fields}{fields}
<button type="submit" id="submit">Submit application</button>
</form>
<script>
(function(){
  var inst=Math.random().toString(36).slice(2,10);
  function vals(){var o={};document.querySelectorAll('input,select,textarea').forEach(function(el){
    if(el.type==='file')o[el.name]=[].map.call(el.files,function(f){return {n:f.name,s:f.size};});
    else o[el.name]=el.value;});return o;}
  function send(ev){var b=JSON.stringify({inst:inst,ev:ev,vals:vals(),vis:document.visibilityState,focus:document.hasFocus(),
    url:location.href,t:Date.now(),
    win:[screenX,screenY,outerWidth,outerHeight]});
    if(ev==='hide'&&navigator.sendBeacon){navigator.sendBeacon('/beacon',b);return;}
    fetch('/beacon',{method:'POST',body:b,keepalive:true}).catch(function(){});}
  document.addEventListener('input',function(){send('input');},true);
  document.addEventListener('change',function(){send('change');},true);
  document.addEventListener('visibilitychange',function(){send('vis');});
  document.getElementById('app').addEventListener('submit',function(){send('submit');},true);
  window.addEventListener('pagehide',function(){send('hide');});
  setInterval(function(){send('tick');},3000);
  send('load');
})();
</script></body></html>"""

THANKS = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Application submitted</title></head>
<body style="font:15px system-ui;max-width:720px;margin:24px auto"><h1>Application submitted</h1>
<p>Thank you for applying to Acceptance Test Co. (local test form — nothing was sent anywhere)</p></body></html>"""


def _field_html(name, q, kind):
    if kind.startswith('select:'):
        opts = ''.join(f'<option value="{o}">{o or "Select..."}</option>' for o in kind[7:].split('|'))
        ctl = f'<select id="{name}" name="{name}">{opts}</select>'
    elif kind == 'textarea':
        ctl = f'<textarea id="{name}" name="{name}" required></textarea>'
    else:
        ctl = f'<input id="{name}" name="{name}" type="{kind}">'
    return f'<label for="{name}">{q}</label>{ctl}'


class FakeForm:
    def __init__(self, port=0, upload_fields=1, upload_slots=None):
        self.log, self.lock = [], threading.Lock()
        self.upload_slots = list(upload_slots) if upload_slots is not None else [
            ('resume', 'Resume/CV')
        ] + [(f'attachments-{i}', 'Additional documents')
             for i in range(2, max(1, upload_fields) + 1)]
        self.fields = FIELDS
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype='text/html; charset=utf-8', extra=None):
                b = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(b)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                u = urlparse(self.path)
                job = (parse_qs(u.query).get('job') or [''])[0]
                if u.path == '/apply':
                    srv._add({'ev': 'GET', 'job': job})
                    return self._send(200, PAGE.replace('{job}', job).replace(
                        '{upload_fields}', ''.join(_field_html(name, label, 'file')
                                                   for name, label in srv.upload_slots)).replace(
                        '{fields}', ''.join(_field_html(*f) for f in srv.fields)))
                if u.path == '/thanks':
                    srv._add({'ev': 'GET-thanks', 'job': job})
                    return self._send(200, THANKS)
                self._send(404, 'not found')

            def do_POST(self):
                u = urlparse(self.path)
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                if u.path == '/beacon':
                    try:
                        d = json.loads(body.decode() or '{}')
                    except ValueError:
                        return self._send(400, 'bad')
                    d['srv_t'] = time.time()
                    srv._add(d)
                    return self._send(204, b'')
                if u.path == '/submit':
                    job = (parse_qs(u.query).get('job') or [''])[0]
                    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                        b'Content-Type: ' + self.headers.get('Content-Type', '').encode() + b'\r\n\r\n' + body)
                    fields, files = {}, {}
                    for part in msg.iter_parts():
                        name = part.get_param('name', header='content-disposition')
                        fn = part.get_filename()
                        data = part.get_payload(decode=True) or b''
                        if fn is not None:
                            files[name] = {'n': fn, 's': len(data), 'sha1': hashlib.sha1(data).hexdigest()}
                        else:
                            fields[name] = data.decode('utf-8', 'replace')
                    srv._add({'ev': 'SUBMIT', 'job': job, 'fields': fields, 'files': files})
                    return self._send(303, b'', extra={'Location': '/thanks?job=' + job})
                self._send(404, 'not found')

        self.httpd = ThreadingHTTPServer(('127.0.0.1', port), H)
        self.port = self.httpd.server_address[1]

    def _add(self, d):
        d.setdefault('srv_t', time.time())
        with self.lock:
            self.log.append(d)

    def url(self, job):
        return f'http://127.0.0.1:{self.port}/apply?job={job}'

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()

    def events(self, since=0.0):
        with self.lock:
            return [e for e in self.log if e.get('srv_t', 0) >= since]

    # ---- 驗收要問的事 ----
    def gets(self, since=0.0):
        return [e for e in self.events(since) if e.get('ev') == 'GET']

    def submits(self, since=0.0):
        return [e for e in self.events(since) if e.get('ev') == 'SUBMIT']

    def beacons(self, since=0.0):
        return [e for e in self.events(since) if 'inst' in e]

    def latest(self, inst=None):
        """某個頁面(預設:最後載入的那個)最新一次回報。"""
        bs = self.beacons()
        if inst is None and bs:
            inst = [b for b in bs if b.get('ev') == 'load'][-1]['inst'] if any(b.get('ev') == 'load' for b in bs) else bs[-1]['inst']
        mine = [b for b in bs if b.get('inst') == inst]
        return mine[-1] if mine else None


if __name__ == '__main__':
    s = FakeForm(8911).start()
    print('假表單:', s.url('demo'))
    try:
        while True:
            time.sleep(5)
            b = s.latest()
            if b:
                print(b['inst'], b['ev'], b['vis'], json.dumps(b['vals'], ensure_ascii=False)[:200])
    except KeyboardInterrupt:
        s.stop()
