# -*- coding: utf-8 -*-
"""PR 預覽(#809):從看板伺服器的預覽 API 看外部行為。

本機假的 GitHub:一個 bare repo 放 PR 的 commit;PATH 上的假 gh 照一份 JSON 回 PR 清單與權限,假 uv 直接用 python 跑。
PR 的程式是一個小的假看板伺服器,回它收到的啟動參數、環境和自己是哪一版。"""
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

from tests.test_board import HttpBase
import board_doc as bd
import board_server as bs
import config as cf
import pr_preview as pp

NO_HOOKS = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.hooksPath', 'GIT_CONFIG_VALUE_0': '/dev/null',
            'GIT_AUTHOR_NAME': 'test', 'GIT_AUTHOR_EMAIL': 'test@example.test',
            'GIT_COMMITTER_NAME': 'test', 'GIT_COMMITTER_EMAIL': 'test@example.test'}

FAKE_SERVER = r'''
import argparse, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
ap = argparse.ArgumentParser(); ap.add_argument('--state'); ap.add_argument('--port', type=int)
a = ap.parse_args()
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(here, 'VERSION')) as f: version = f.read().strip()
class H(BaseHTTPRequestHandler):
    def log_message(self, *x): pass
    def do_GET(self):
        body = {'app': 'jobsalvo', 'state': a.state, 'version': version,
                'home': os.environ.get('JOBSALVO_HOME'), 'preview': os.environ.get('JOBSALVO_PREVIEW'),
                'launchd': os.environ.get('JOBSALVO_LAUNCHD')}
        out = json.dumps(body).encode(); self.send_response(200); self.end_headers(); self.wfile.write(out)
HTTPServer(('127.0.0.1', a.port), H).serve_forever()
'''

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['FAKE_GH']) as f: d = json.load(f)
a = sys.argv[1:]
if a[:2] == ['repo', 'view']: print(d['permission']); sys.exit(0)
if a[:2] == ['pr', 'list']: print(json.dumps(d['prs'])); sys.exit(0)
if a[:2] == ['pr', 'view']:
    if int(a[2]) in d.get('closed', []): print(json.dumps({'number': int(a[2]), 'state': 'CLOSED'})); sys.exit(0)
    pr = [p for p in d['prs'] if str(p['number']) == a[2]]
    if not pr: print('no such PR', file=sys.stderr); sys.exit(1)
    print(json.dumps(dict(pr[0], state='OPEN'))); sys.exit(0)
sys.exit(2)
'''

FAKE_UV = r'''#!/usr/bin/env python3
import os, sys
a = sys.argv[1:]
rest = a[a.index('python') + 1:]
os.execv(sys.executable, [sys.executable] + rest)
'''


def git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, *args], check=True, capture_output=True, text=True,
                          env=dict(os.environ, **NO_HOOKS)).stdout.strip()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


class PrPreview(HttpBase):
    def setUp(self):
        super().setUp()
        root = self.enterContext(tempfile.TemporaryDirectory(prefix='pr-preview-'))
        self.cache = os.path.join(root, 'cache')
        self.remote = os.path.join(root, 'remote.git')
        self.work = os.path.join(root, 'work')
        env = dict(os.environ, **NO_HOOKS)
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', self.remote], check=True, env=env)
        subprocess.run(['git', 'init', '-q', '-b', 'main', self.work], check=True, env=env)
        os.makedirs(os.path.join(self.work, 'tools'))
        with open(os.path.join(self.work, 'tools', 'board_server.py'), 'w') as f: f.write(FAKE_SERVER)
        self.sha = {7: self.push_pr(7, 'pr7-v1')}
        bin_dir = os.path.join(root, 'bin'); os.makedirs(bin_dir)
        for name, text in (('gh', FAKE_GH), ('uv', FAKE_UV)):
            path = os.path.join(bin_dir, name)
            with open(path, 'w') as f: f.write(text)
            os.chmod(path, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions  暫存夾裡的假指令要能執行
        self.gh_data = os.path.join(root, 'gh.json')
        self.set_gh('WRITE', [self.pr(7), dict(self.pr(8), isCrossRepository=True)])
        self.enterContext(mock.patch.dict(os.environ, {'PATH': bin_dir + os.pathsep + os.environ['PATH'],
                                                       'FAKE_GH': self.gh_data, 'JOBSALVO_LAUNCHD': '1'}))
        self.enterContext(mock.patch.object(pp, 'root', return_value=self.cache))
        self.enterContext(mock.patch.object(pp, 'remote_url', return_value=self.remote))
        self.enterContext(mock.patch.object(cf, 'LIVE', self.path))
        self.enterContext(mock.patch.object(bs, 'is_real', return_value=True))
        pp._PERM.clear(); pp._JOBS.clear()
        self.addCleanup(self.stop_all)

    def stop_all(self):
        for n in list(pp._load()):
            pp.close_preview(int(n))

    def push_pr(self, n, version):
        with open(os.path.join(self.work, 'VERSION'), 'w') as f: f.write(version)
        git(self.work, 'add', '-A'); git(self.work, 'commit', '-q', '-m', version)
        git(self.work, 'push', '-q', '-f', self.remote, f'HEAD:refs/pull/{n}/head')
        return git(self.work, 'rev-parse', 'HEAD')

    def pr(self, n):
        return {'number': n, 'title': f'PR {n} 標題', 'headRefName': f'claude/{n}-x', 'headRefOid': self.sha.get(n, 'f' * 40),
                'updatedAt': '2026-11-23T00:00:00Z', 'url': f'https://github.com/owner/app/pull/{n}',
                'body': f'Closes #{n}\n\n## Summary\n在做第 {n} 件事', 'isCrossRepository': False}

    def set_gh(self, permission, prs, closed=()):
        with open(self.gh_data, 'w') as f: json.dump({'permission': permission, 'prs': prs, 'closed': list(closed)}, f)

    def api(self, path='/api/pr-previews', body=None):
        code, raw, _ = self.req(path, body=body)
        return code, json.loads(raw)

    def page_cfg(self):
        return bd.parse(self.req('/')[1].decode('utf-8'))['data']['cfg']

    def open_until_ready(self, n):
        code, out = self.api(body={'op': 'open', 'number': n})
        self.assertEqual(code, 200, out)
        deadline = time.monotonic() + 60
        while out.get('state') != 'ready':
            self.assertNotEqual(out.get('state'), 'failed', out)
            self.assertLess(time.monotonic(), deadline, out)
            time.sleep(0.2)
            out = self.api('/api/pr-previews?status=1')[1]['states'].get(str(n), {})
        return out

    def test_only_a_real_board_whose_github_account_can_push_the_dev_repo_has_previews(self):
        self.assertTrue(self.page_cfg()['pr_previews'])
        self.assertEqual(self.api()[0], 200)
        for case in ('no gh', 'read only', 'copy'):
            with self.subTest(case):
                pp._PERM.clear()
                self.set_gh('READ' if case == 'read only' else 'WRITE', [self.pr(7)])
                with mock.patch.object(bs, 'is_real', return_value=case != 'copy'), \
                        mock.patch.object(pp.shutil, 'which', return_value=None if case == 'no gh' else pp.shutil.which('gh')):
                    code, out = self.api()
                    self.assertEqual((code, out['msg']), (404, '沒有這個功能'))
                    self.assertEqual(self.api(body={'op': 'open', 'number': 7})[0], 404)
                    self.assertEqual(self.page_cfg()['pr_previews'], case != 'copy')   # 正式看板都在背景問,問不到就不出現

    def test_lists_open_pull_requests_of_the_same_repo_with_what_they_do(self):
        code, out = self.api()
        self.assertEqual(code, 200)
        self.assertEqual([x['number'] for x in out['prs']], [7])    # fork 來的 #8 不列
        pr = out['prs'][0]
        self.assertEqual(pr['diff'], 'https://github.com/owner/app/pull/7/files')
        self.assertEqual(pr['summary'], '在做第 7 件事')
        self.assertEqual(pr['preview'], {'state': 'off'})
        self.assertEqual(self.api(body={'op': 'open', 'number': 8})[0], 400)

    def test_preview_runs_the_pushed_commit_on_a_copy_of_the_real_data(self):
        with open(self.path, 'rb') as f: real_before = f.read()
        ready = self.open_until_ready(7)
        seen = get(f'http://127.0.0.1:{ready["port"]}/')
        self.assertEqual(seen['version'], 'pr7-v1')
        self.assertNotEqual(os.path.realpath(seen['state']), os.path.realpath(self.path))
        self.assertTrue(seen['state'].startswith(self.cache))
        self.assertEqual(bd.load(seen['state'])['data']['jobs'], bd.load(self.path)['data']['jobs'])
        self.assertEqual(seen['home'], cf.HOME)                     # 設定、履歷由預覽照副本規則從真的資料夾複製
        self.assertIsNone(seen['launchd'])                         # 不冒充開機服務
        self.assertEqual(json.loads(seen['preview'])['number'], 7)
        with open(self.path, 'rb') as f: self.assertEqual(f.read(), real_before)
        listed = self.api()[1]['prs'][0]['preview']
        self.assertEqual((listed['state'], listed['port']), ('ready', ready['port']))

    def test_reopening_reuses_the_same_commit_and_a_new_push_replaces_it(self):
        first = self.open_until_ready(7)
        pid = pp._load()['7']['pid']
        self.assertEqual(self.api(body={'op': 'open', 'number': 7})[1]['port'], first['port'])
        self.assertEqual(pp._load()['7']['pid'], pid)
        self.sha[7] = self.push_pr(7, 'pr7-v2')
        self.set_gh('WRITE', [self.pr(7)])
        second = self.open_until_ready(7)
        self.assertEqual(get(f'http://127.0.0.1:{second["port"]}/')['version'], 'pr7-v2')
        self.assertFalse(pp._answers(first['port']) and first['port'] != second['port'])

    def test_closing_stops_it_and_a_pull_request_that_is_no_longer_open_is_cleaned_up(self):
        ready = self.open_until_ready(7)
        self.assertEqual(self.api(body={'op': 'close', 'number': 7})[1]['state'], 'off')
        deadline = time.monotonic() + 5
        while pp._answers(ready['port']) and time.monotonic() < deadline: time.sleep(0.1)
        self.assertFalse(pp._answers(ready['port']))
        self.open_until_ready(7)
        self.set_gh('WRITE', [])                                   # 清單沒列到(清單有上限)不算關了
        self.assertEqual(self.api()[1]['prs'], [])
        self.assertIn('7', pp._load())
        self.set_gh('WRITE', [], closed=[7])                       # 合併或關掉了
        self.assertEqual(self.api()[1]['prs'], [])
        self.assertEqual(pp._load(), {})
        self.assertFalse(os.path.exists(os.path.join(self.cache, 'pr-7')))

    def test_closing_while_preparing_never_starts_it(self):
        closed = threading.Event()   # 關掉之後預覽才答應:沒處理取消的話,它會在關掉之後登記成開著
        real_answers = pp._answers
        with mock.patch.object(pp, '_answers', side_effect=lambda port: closed.is_set() and real_answers(port)):
            self.assertEqual(self.api(body={'op': 'open', 'number': 7})[1]['state'], 'preparing')
            time.sleep(0.5)
            self.api(body={'op': 'close', 'number': 7})
            closed.set()
            time.sleep(3)
        self.assertEqual(pp._load(), {})
        self.assertEqual(self.api('/api/pr-previews?status=1')[1]['states'].get('7', {'state': 'off'}), {'state': 'off'})

    def test_a_preview_that_cannot_start_says_why(self):
        with open(os.path.join(self.work, 'tools', 'board_server.py'), 'w') as f: f.write('raise SystemExit("壞掉的分支")\n')
        self.sha[7] = self.push_pr(7, 'broken')
        self.set_gh('WRITE', [self.pr(7)])
        self.api(body={'op': 'open', 'number': 7})
        deadline = time.monotonic() + 30
        while (state := self.api('/api/pr-previews?status=1')[1]['states'].get('7', {})).get('state') == 'preparing':
            self.assertLess(time.monotonic(), deadline); time.sleep(0.2)
        self.assertEqual(state['state'], 'failed')
        self.assertIn('壞掉的分支', state['msg'])
        self.assertEqual(self.api('/api/health')[0], 200)          # 正式看板照常


if __name__ == '__main__':
    unittest.main()
