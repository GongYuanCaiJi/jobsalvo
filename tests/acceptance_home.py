"""驗收測試的家目錄:設定、原始履歷、空看板、登記檔、任務的狀態檔資料夾,都在這一個測試的暫存裡。"""
import copy
import json
import os
import shutil
import tempfile
from unittest import mock

import fake_browser  # noqa: F401  (先設好 sys.path 與 _env)
import board_doc
import config as cf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANG = 'zh'
PLATFORM = 'jobs.example'
PROFILE_URL = 'https://jobs.example/profile/1'


def board_html(jobs=(), fb=None, **data):
    return board_doc.assemble(':root{--a:1}', '<b id="stat-first">0</b>', '', {'jobs': list(jobs), **data},
                              json.dumps(fb or {}, ensure_ascii=False), '/* acceptance */')


class Home:
    def __init__(self, test, resume='# 經歷\n舊的一行\n', attachments=False):
        self.dir = test.enterContext(tempfile.TemporaryDirectory(prefix='jobsalvo-accept-'))
        test.addCleanup(cf.reload, cf.HOME)
        self.home = os.path.join(self.dir, 'home')
        self.board = os.path.join(self.home, 'board.html')
        self.tmp = {name: os.path.join(self.dir, name) for name in ('sync', 'apply', 'reply', 'converge', 'add', 'evidence')}
        test.enterContext(mock.patch.dict(os.environ, JOBSALVO_HOME=self.home, EVIDENCE_TMP=self.tmp['evidence'],
                                          SYNC_TMP=self.tmp['sync'], APPLY_TMP=self.tmp['apply'],
                                          REPLY_TMP=self.tmp['reply'], CONVERGE_TMP=self.tmp['converge'],
                                          ADD_TMP=self.tmp['add']))
        os.makedirs(os.path.join(self.home, 'resume'))
        for name in ('prefs.md', 'apply-rules.md', 'resume.md'):
            shutil.copyfile(os.path.join(ROOT, 'examples', name), os.path.join(self.home, name))
        settings = copy.deepcopy(cf.DEFAULTS)
        settings['board']['file'] = 'board.html'
        listed = []
        if attachments:
            with open(os.path.join(self.home, 'resume', 'report.pdf'), 'wb') as target:
                target.write(b'attachment')
            listed = [{'id': 'report', 'name': 'report.pdf', 'files': {LANG: 'resume/report.pdf'}, 'enabled': True}]
        settings['resume'].update({'base': 'resume', 'langs': [LANG], 'attachments': listed,
                                   'resumes': [{'id': 'one', 'name': '履歷', 'files': {LANG: 'resume/one.md'},
                                                'enabled': True}]})
        self.settings = settings
        with open(os.path.join(self.home, cf.NAME), 'w', encoding='utf-8') as target:
            json.dump(settings, target, ensure_ascii=False)
        cf.reload(self.home)
        self.write_resume(resume)
        self.write_board()

    def write_resume(self, text):
        with open(os.path.join(self.home, 'resume', 'one.md'), 'w', encoding='utf-8') as target:
            target.write(text)

    def write_board(self, jobs=(), fb=None, **data):
        """照正常寫入換看板(記指紋):同一個家重寫看板不算被繞過改過(A-36)。"""
        with board_doc.live_lock(self.board):
            board_doc.write_doc(self.board, board_html(jobs, fb, **data))

    def fb(self):
        return json.loads(board_doc.load(self.board)['fb'])

    def jobs(self):
        return {row['id']: row for row in board_doc.load(self.board)['data']['jobs']}

    def registry(self):
        try:
            with open(os.path.join(self.home, 'profiles.json'), encoding='utf-8') as source:
                return json.load(source)
        except FileNotFoundError:
            return {}

    def status_file(self, task):
        name = {'sync': 'sync_status.json', 'apply': 'apply_status.json', 'reply': 'reply_status.json',
                'converge': 'converge_status.json', 'add': 'add_status.json'}[task]
        with open(os.path.join(self.tmp[task], name), encoding='utf-8') as source:
            return json.load(source)
