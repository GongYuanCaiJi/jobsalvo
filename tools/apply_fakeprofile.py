"""apply_fakeprofile — 驗收平台履歷附件流程用的本機假平台頁。"""

import copy
import email.parser
import email.policy
import hashlib
import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from apply_fakeform import FIELDS, PAGE, THANKS, _field_html


CASES = ('slots-full', 'extra-old', 'download-error', 'upload-error', 'normal')
LOCAL_ACCEPTANCE_BANNER = (
    '<div class="banner" data-local-acceptance="true">'
    '本機假驗收頁 (127.0.0.1) / Local acceptance-test platform. '
    'Changes affect only disposable test data.</div>'
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def acceptance_scenarios(support, custom_resume, old_version, profile_text=''):
    """建立驗收用平台狀態；每個 scenario 都是可獨立變更的副本。"""
    fixed = lambda files: {
        'id': 'fixed', 'kind': 'fixed', 'name': '固定平台履歷',
        'text': profile_text, 'attachments': files,
    }
    support_file = {'id': 'support', 'name': 'support.pdf', 'content': support}
    return {
        'slots-full': {
            'slot_limit': 1,
            'requires_custom_resume': True,
            'profiles': [fixed([support_file])],
        },
        'extra-old': {
            'slot_limit': 3,
            'requires_custom_resume': False,
            'profiles': [fixed([
                support_file,
                {'id': 'old-version', 'name': 'old-version.pdf', 'content': old_version},
            ])],
        },
        'download-error': {
            'slot_limit': 3,
            'requires_custom_resume': False,
            'profiles': [fixed([support_file])],
            'failures': {'download': ['support']},
        },
        'upload-error': {
            'slot_limit': 2,
            'requires_custom_resume': True,
            'profiles': [fixed([support_file])],
            'failures': {'upload': True},
        },
        'normal': {
            'slot_limit': 2,
            'requires_custom_resume': True,
            'profiles': [fixed([support_file])],
        },
    }


class FakePlatformProfile:
    """只綁 127.0.0.1；保留所有頁面操作與檔案雜湊供驗收查證。"""

    def __init__(self, port=0, scenarios=None):
        self.log = []
        self.lock = threading.Lock()
        raw = scenarios or acceptance_scenarios(b'support', b'resume', b'old')
        self.scenarios = copy.deepcopy(raw)
        for case, state in self.scenarios.items():
            state.setdefault('slot_limit', 6)
            state.setdefault('failures', {})
            state.setdefault('profiles', [])
            state.setdefault('requires_custom_resume', False)
            state['profiles'] = {
                str(p['id']): {
                    **p,
                    'attachments': [dict(a) for a in p.get('attachments', [])],
                }
                for p in state['profiles']
            }
            for p in state['profiles'].values():
                for attachment in p['attachments']:
                    attachment['content'] = bytes(attachment.get('content', b''))
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body, ctype='text/html; charset=utf-8', extra=None):
                data = body.encode('utf-8') if isinstance(body, str) else body
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def _event(self, **data):
                srv._add(data)

            def _body(self):
                return self.rfile.read(int(self.headers.get('Content-Length') or 0))

            def _form(self, body):
                ctype = self.headers.get('Content-Type', '')
                if ctype.startswith('multipart/form-data'):
                    message = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                        b'Content-Type: ' + ctype.encode() + b'\r\n\r\n' + body
                    )
                    fields, files = {}, []
                    for part in message.iter_parts():
                        name = part.get_param('name', header='content-disposition') or ''
                        data = part.get_payload(decode=True) or b''
                        filename = part.get_filename()
                        if filename is None:
                            fields[name] = data.decode('utf-8', 'replace')
                        else:
                            files.append({'field': name, 'name': filename, 'content': data})
                    return fields, files
                fields = {k: v[-1] for k, v in parse_qs(body.decode(), keep_blank_values=True).items()}
                return fields, []

            def do_GET(self):
                parsed = urlparse(self.path)
                parts = [p for p in parsed.path.split('/') if p]
                query = parse_qs(parsed.query)
                if parsed.path == '/apply':
                    case = (query.get('job') or [''])[0]
                    state = srv.scenarios.get(case)
                    if state is None:
                        return self._send(404, 'unknown scenario')
                    self._event(action='VIEW_APPLICATION', case=case, status=200)
                    text_fields = [item for item in FIELDS if item[2] != 'file']
                    page = PAGE.replace('{job}', html.escape(case)).replace(
                        '{fields}', ''.join(_field_html(*field) for field in text_fields)
                    )
                    page = page.replace(
                        '<label for="resume">Resume/CV (required)</label>'
                        '<input id="resume" type="file" name="resume" required>',
                        '',
                    )
                    profiles = ''.join(
                        f'<option value="{html.escape(str(profile_id), quote=True)}">'
                        f'{html.escape(profile["name"])}</option>'
                        for profile_id, profile in state['profiles'].items()
                    )
                    selector = (
                        '<label for="profile_id">Platform resume (required)</label>'
                        '<select id="profile_id" name="profile_id" required>'
                        '<option value="">Select a platform resume...</option>'
                        + profiles + '</select>'
                    )
                    page = page.replace('</form>', (
                        selector
                        + f'<p>此職缺使用平台履歷，申請表沒有直接附件上傳欄。'
                        f'<a href="{srv.manager_url(case)}">管理平台履歷</a></p></form>'
                    ))
                    return self._send(200, page)
                if len(parts) == 2 and parts[0] == 'profiles':
                    case = parts[1]
                    state = srv.scenarios.get(case)
                    if state is None:
                        return self._send(404, 'unknown scenario')
                    self._event(action='LIST_PROFILES', case=case, status=200)
                    return self._send(200, srv._manager_html(case, state))
                if len(parts) == 3 and parts[0] == 'profile':
                    _, case, profile_id = parts
                    state = srv.scenarios.get(case)
                    profile = state and state['profiles'].get(profile_id)
                    if profile is None:
                        return self._send(404, 'profile not found')
                    self._event(action='VIEW_PROFILE', case=case, profile=profile_id,
                                profile_kind=profile.get('kind'), status=200)
                    return self._send(200, srv._profile_html(case, profile))
                if len(parts) == 4 and parts[0] == 'download':
                    _, case, profile_id, attachment_id = parts
                    profile = srv._profile(case, profile_id)
                    attachment = srv._attachment(profile, attachment_id)
                    if profile is None or attachment is None:
                        self._event(action='DOWNLOAD', case=case, profile=profile_id,
                                    attachment=attachment_id, sha256=None, status=404, ok=False)
                        return self._send(404, 'attachment not found')
                    content = attachment['content']
                    failed = srv._fails(case, 'download', attachment_id, attachment.get('name'))
                    self._event(
                        action='DOWNLOAD', case=case, profile=profile_id,
                        profile_kind=profile.get('kind'), attachment=attachment_id,
                        name=attachment.get('name'), sha256=None if failed else sha256(content),
                        stored_sha256=sha256(content), status=503 if failed else 200,
                        ok=not failed,
                    )
                    if failed:
                        return self._send(503, 'configured download failure')
                    filename = str(attachment['name'])
                    fallback = ''.join(
                        char if char.isascii() and (char.isalnum() or char in '._-') else '_'
                        for char in filename
                    ) or 'attachment'
                    disposition = (
                        f'attachment; filename="{fallback}"; '
                        f"filename*=UTF-8''{quote(filename, safe='!#$&+-.^_`|~')}"
                    )
                    return self._send(200, content, 'application/octet-stream', {
                        'Content-Disposition': disposition,
                        'X-Content-SHA256': sha256(content),
                    })
                if parsed.path == '/thanks':
                    self._event(action='VIEW_THANKS', case=(query.get('job') or [''])[0], status=200)
                    return self._send(200, THANKS)
                self._send(404, 'not found')

            def do_POST(self):
                parsed = urlparse(self.path)
                parts = [p for p in parsed.path.split('/') if p]
                query = parse_qs(parsed.query)
                body = self._body()
                if parsed.path == '/beacon':
                    try:
                        item = json.loads(body.decode() or '{}')
                    except ValueError:
                        return self._send(400, 'bad beacon')
                    beacon_url = urlparse(str(item.get('url') or ''))
                    case = (parse_qs(beacon_url.query).get('job') or [''])[0]
                    self._event(action='BEACON', case=case, **item)
                    return self._send(204, b'')
                if parsed.path == '/submit':
                    case = (query.get('job') or [''])[0]
                    fields, files = self._form(body)
                    self._event(action='SUBMIT', case=case, status=303,
                                fields=fields,
                                files=[{'name': f['name'], 'sha256': sha256(f['content'])}
                                       for f in files])
                    return self._send(303, b'', extra={'Location': '/thanks?job=' + quote(case)})
                if len(parts) == 2 and parts[0] == 'create':
                    case = parts[1]
                    state = srv.scenarios.get(case)
                    if state is None:
                        return self._send(404, 'unknown scenario')
                    fields, _ = self._form(body)
                    full = len(state['profiles']) >= int(state['slot_limit'])
                    blocked = full or bool(state['failures'].get('create_profile'))
                    if blocked:
                        self._event(action='CREATE_PROFILE', case=case,
                                    name=fields.get('name', ''), status=409 if full else 503,
                                    ok=False, reason='slots_full' if full else 'configured_failure')
                        return self._send(409 if full else 503, 'profile slot unavailable')
                    number = 1
                    while f'custom-{number}' in state['profiles']:
                        number += 1
                    profile_id = f'custom-{number}'
                    state['profiles'][profile_id] = {
                        'id': profile_id, 'kind': 'custom',
                        'name': fields.get('name') or '卡片客製平台履歷', 'attachments': [],
                    }
                    self._event(action='CREATE_PROFILE', case=case, profile=profile_id,
                                name=state['profiles'][profile_id]['name'], status=201, ok=True)
                    return self._send(303, b'', extra={'Location': srv.profile_url(case, profile_id)})
                if len(parts) == 3 and parts[0] == 'upload':
                    _, case, profile_id = parts
                    fields, files = self._form(body)
                    profile = srv._profile(case, profile_id)
                    if profile is None or not files:
                        self._event(action='UPLOAD', case=case, profile=profile_id,
                                    status=404, ok=False, reason='profile_or_file_missing')
                        return self._send(404, 'profile or file missing')
                    file = files[0]
                    filename = file['name'].replace('\\', '/').rsplit('/', 1)[-1]
                    content = file['content']
                    if not filename or not content:
                        self._event(action='UPLOAD', case=case, profile=profile_id,
                                    profile_kind=profile.get('kind'), name=filename,
                                    sha256=sha256(content), status=400, ok=False,
                                    reason='empty_file')
                        return self._send(400, 'select a non-empty file first')
                    failed = srv._fails(case, 'upload', filename, fields.get('attachment'))
                    previous = next((a for a in profile['attachments']
                                     if a.get('name', '').casefold() == filename.casefold()), None)
                    old_sha = sha256(previous['content']) if previous else None
                    if failed:
                        self._event(action='UPLOAD', case=case, profile=profile_id,
                                    profile_kind=profile.get('kind'), name=filename,
                                    sha256=sha256(content), previous_sha256=old_sha,
                                    status=503, ok=False, reason='configured_failure')
                        return self._send(503, 'configured upload failure')
                    if previous:
                        previous['content'] = content
                    else:
                        aid = f'upload-{len(profile["attachments"]) + 1}'
                        profile['attachments'].append({'id': aid, 'name': filename, 'content': content})
                    self._event(action='UPLOAD', case=case, profile=profile_id,
                                profile_kind=profile.get('kind'), name=filename,
                                sha256=sha256(content), previous_sha256=old_sha,
                                status=200, ok=True)
                    return self._send(200, 'upload saved')
                if len(parts) == 4 and parts[0] == 'delete-attachment':
                    _, case, profile_id, attachment_id = parts
                    profile = srv._profile(case, profile_id)
                    attachment = srv._attachment(profile, attachment_id)
                    if attachment is None:
                        self._event(action='DELETE_ATTACHMENT', case=case, profile=profile_id,
                                    attachment=attachment_id, sha256=None, status=404, ok=False)
                        return self._send(404, 'attachment not found')
                    old_sha = sha256(attachment['content'])
                    profile['attachments'].remove(attachment)
                    self._event(action='DELETE_ATTACHMENT', case=case, profile=profile_id,
                                profile_kind=profile.get('kind'), attachment=attachment_id,
                                name=attachment.get('name'), sha256=old_sha, status=200, ok=True)
                    return self._send(303, b'', extra={'Location': srv.manager_url(case)})
                if len(parts) == 3 and parts[0] == 'delete-profile':
                    _, case, profile_id = parts
                    profile = srv._profile(case, profile_id)
                    if profile is None:
                        self._event(action='DELETE_PROFILE', case=case, profile=profile_id,
                                    status=404, ok=False)
                        return self._send(404, 'profile not found')
                    profile_hash = srv._profile_hash(profile)
                    del srv.scenarios[case]['profiles'][profile_id]
                    self._event(action='DELETE_PROFILE', case=case, profile=profile_id,
                                profile_kind=profile.get('kind'), sha256=profile_hash,
                                status=200, ok=True)
                    return self._send(303, b'', extra={'Location': srv.manager_url(case)})
                self._send(404, 'not found')

        self.httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = None

    def _add(self, event):
        event.setdefault('srv_t', time.time())
        with self.lock:
            self.log.append(event)

    def _profile(self, case, profile_id):
        state = self.scenarios.get(case)
        return (state or {}).get('profiles', {}).get(profile_id)

    @staticmethod
    def _attachment(profile, attachment_id):
        if profile is None:
            return None
        return next((a for a in profile['attachments']
                     if str(a.get('id')) == attachment_id or a.get('name') == attachment_id), None)

    def _fails(self, case, action, *keys):
        state = self.scenarios.get(case) or {}
        configured = (state.get('failures') or {}).get(action, False)
        if configured is True:
            return True
        if isinstance(configured, (list, tuple, set)):
            return any(str(key) in configured for key in keys if key is not None)
        return False

    @staticmethod
    def _profile_hash(profile):
        content = '\n'.join(
            f'{item.get("id")}:{item.get("name")}:{sha256(item["content"])}'
            for item in sorted(profile['attachments'], key=lambda x: str(x.get('id')))
        )
        return sha256((str(profile.get('kind')) + '\n' + content).encode())

    def _manager_html(self, case, state):
        profiles = []
        for profile in state['profiles'].values():
            pid = profile['id']
            files = ''.join(
                f'<li>{html.escape(item["name"])} '
                f'<a href="{self.download_url(case, pid, item["id"])}">下載</a> '
                f'<form method="post" action="/delete-attachment/{quote(case)}/{quote(pid)}/{quote(str(item["id"]))}">'
                '<button type="submit">刪除附件</button></form></li>'
                for item in profile['attachments']
            ) or '<li>沒有附件</li>'
            profiles.append(
                f'<section><h2>{html.escape(profile["name"])}</h2>'
                f'<p>類型:{html.escape(profile["kind"])}</p><ul>{files}</ul>'
                f'<a href="{self.profile_url(case, pid)}">開啟履歷</a> '
                f'<form method="post" action="/delete-profile/{quote(case)}/{quote(pid)}">'
                '<button type="submit">刪除履歷</button></form></section>'
            )
        return (
            f'<html><meta charset="utf-8"><title>平台履歷管理</title>'
            + LOCAL_ACCEPTANCE_BANNER
            + f'<h1>平台履歷管理</h1>'
            f'<p>格子:{len(state["profiles"])}/{state["slot_limit"]}</p>'
            + ''.join(profiles)
            + f'<form method="post" action="/create/{quote(case)}">'
            '<label>新履歷名稱<input name="name"></label>'
            '<button type="submit">新增平台履歷</button></form></html>'
        )

    def _profile_html(self, case, profile):
        pid = profile['id']
        profile_text = profile.get('text')
        profile_text_html = (
            f'<pre class="profile-text">{html.escape(profile_text)}</pre>'
            if isinstance(profile_text, str) and profile_text else ''
        )
        files = ''.join(
            f'<li>{html.escape(item["name"])} '
            f'<a href="{self.download_url(case, pid, item["id"])}">下載</a> '
            f'<form method="post" action="/delete-attachment/{quote(case)}/{quote(pid)}/{quote(str(item["id"]))}">'
            '<button type="submit">刪除附件</button></form></li>'
            for item in profile['attachments']
        ) or '<li>沒有附件</li>'
        return (
            f'<html><meta charset="utf-8"><title>{html.escape(profile["name"])}</title>'
            + LOCAL_ACCEPTANCE_BANNER
            + f'<h1>{html.escape(profile["name"])}</h1>'
            + profile_text_html
            + f'<p>類型:{html.escape(profile["kind"])}</p><ul>{files}</ul>'
            f'<form method="post" enctype="multipart/form-data" action="/upload/{quote(case)}/{quote(pid)}">'
            '<label>上傳或取代同名附件<input type="file" name="attachment"></label>'
            '<button type="submit">上傳附件</button></form>'
            f'<a href="{self.manager_url(case)}">返回履歷清單</a></html>'
        )

    def _scenario_hash(self, case, kind=None):
        state = self.scenarios.get(case) or {}
        return {
            pid: self._profile_hash(profile)
            for pid, profile in state.get('profiles', {}).items()
            if kind is None or profile.get('kind') == kind
        }

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=2)

    def url(self, case):
        return f'http://127.0.0.1:{self.port}/apply?job={quote(case)}&platform=104.com.tw'

    def manager_url(self, case):
        return f'http://127.0.0.1:{self.port}/profiles/{quote(case)}'

    def profile_url(self, case, profile_id='fixed'):
        return f'http://127.0.0.1:{self.port}/profile/{quote(case)}/{quote(str(profile_id))}'

    def download_url(self, case, profile_id, attachment_id):
        return (f'http://127.0.0.1:{self.port}/download/{quote(case)}/'
                f'{quote(str(profile_id))}/{quote(str(attachment_id))}')

    def events(self, since=0.0, case=None):
        with self.lock:
            return [copy.deepcopy(e) for e in self.log
                    if e.get('srv_t', 0) >= since and (case is None or e.get('case') == case)]

    def fixed_snapshot(self, case):
        return self._scenario_hash(case, kind='fixed')

    def scenario_snapshot(self, case):
        return self._scenario_hash(case)
