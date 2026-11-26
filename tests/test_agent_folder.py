#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""派出去的 agent 從空資料夾開始(#831、#838):看不到程式原始碼,也看不到使用者資料夾裡前幾輪 agent 的產物。

測法:跑正式的入口(分類建議、準備區收尾、客製版),換掉開行程那一支,記下 agent 拿到的工作資料夾在開工那一刻長怎樣。
四件網站任務走 web.outside 同一支 agent_run.run,由這裡的同一條規則管。
"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import agent_run as ar        # noqa: E402
import config as cf           # noqa: E402
import evidence               # noqa: E402

REPO = os.path.dirname(HERE)


def seen_folders(folders, handoffs=None):
    """換掉開行程、等行程那兩支:記下 agent 開工那一刻的工作資料夾(路徑與裡面有什麼)。"""
    def launch(_task, outfile, repo, _agent, **kw):
        folders.append((repo, sorted(os.listdir(repo))))
        with open(outfile, 'a' if kw.get('append') else 'w', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'turn.completed'}) + '\n')
        for path, data in (handoffs or {}).items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        return SimpleNamespace(pid=4242)
    return (patch.object(ar, 'launch', side_effect=launch),
            patch.object(ar, 'wait_done', return_value=[SimpleNamespace(status='completed', returncode=0, pid=4242)]))


class EmptyFolder(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(tempfile.TemporaryDirectory(prefix='agent-folder-'))
        self.enterContext(patch.object(evidence, 'root', return_value=os.path.join(self.home, 'evidence')))
        with open(os.path.join(self.home, 'old-agent-output.json'), 'w', encoding='utf-8') as f:
            f.write('{"from": "上一輪的 agent"}')       # 使用者資料夾裡前幾輪留下的東西

    def assertStartsEmpty(self, folders):
        self.assertEqual(len(folders), 1)
        [(folder, inside)] = folders
        self.assertEqual(inside, [])
        for outside in (self.home, cf.HOME, REPO):
            self.assertNotEqual(os.path.commonpath([os.path.realpath(folder), os.path.realpath(outside)]),
                                os.path.realpath(outside), (folder, outside))

    def test_category_suggestions_start_in_an_empty_folder(self):
        import suggest_cats
        board_path = _env.make_board(os.path.join(self.home, 'board.html'), None,
                                     jobs=[{'id': 'https://ex.test/job/1', 'target': 'Engineer · Acme'}])
        folders = []
        a, b = seen_folders(folders, {os.path.join(self.home, 'suggest.json'): {'categories': [{'name': '後端'}]}})
        with a, b, patch.object(suggest_cats, 'SP', self.home), patch.object(suggest_cats.jobrun, 'write'), \
             patch.object(suggest_cats, 'prompt', return_value='幫他分類'), \
             patch('sys.argv', ['suggest_cats.py', '--board', board_path]):
            suggest_cats.main()
        self.assertStartsEmpty(folders)

    def test_a_relative_work_folder_still_gets_the_hand_in_back(self):
        # 交件路徑給相對的,agent 會寫進它自己的空資料夾,程式在原處讀不到(Sol review-838 #4)
        import suggest_cats
        board_path = _env.make_board(os.path.join(self.home, 'board.html'), None,
                                     jobs=[{'id': 'https://ex.test/job/1', 'target': 'Engineer · Acme'}])

        def agent(task, outfile, repo, _agent, **kw):
            with open(outfile, 'w', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'turn.completed'}) + '\n')
            with open(os.path.join(repo, task.split('寫到 ', 1)[1]), 'w', encoding='utf-8') as f:   # agent 照指示的路徑寫
                json.dump({'categories': [{'name': '後端', 'icon': '🛠', 'match': 'backend'}], 'tags': []}, f)
            return SimpleNamespace(pid=4242)
        os.makedirs(os.path.join(self.home, 'sp'))
        with patch.object(ar, 'launch', side_effect=agent), \
             patch.object(ar, 'wait_done', return_value=[SimpleNamespace(status='completed', returncode=0, pid=4242)]), \
             patch.object(suggest_cats, 'SP', 'sp'), patch.object(suggest_cats.jobrun, 'write'), \
             patch.object(suggest_cats, 'prompt', side_effect=lambda out, *_a: f'寫到 {out}'), \
             patch('sys.argv', ['suggest_cats.py', '--board', board_path]):
            cwd = os.getcwd()
            os.chdir(self.home)
            try:
                code = suggest_cats.main()
            finally:
                os.chdir(cwd)
        self.assertEqual(code, 0)

    def test_preparing_resumes_starts_in_an_empty_folder(self):
        import cut_tailor
        rows = [['https://ex.test/job/1', 'A']]
        sp = os.path.join(self.home, 'sp')
        out = os.path.join(self.home, 'prepare')
        os.makedirs(sp)
        os.makedirs(out)
        for name, data in (('rows.json', rows), ('t0', 0), ('choices.json', {}), ('cached.json', [])):
            with open(os.path.join(out, name), 'w', encoding='utf-8') as f:
                json.dump(data, f)
        with open(os.path.join(out, 'prompt.txt'), 'w', encoding='utf-8') as f:
            f.write('準備這張')
        folders = []
        a, b = seen_folders(folders)
        with a, b, patch.multiple(cut_tailor, SP=sp, STATUSF=os.path.join(sp, 's.json'), PIDF=os.path.join(sp, 'p'),
                                  WORKER_PIDF=os.path.join(sp, 'w')), \
             patch.object(ar, 'wait_done', return_value=[SimpleNamespace(status='failed', returncode=1, pid=4242)]), \
             patch.object(cut_tailor, '_report'):
            cut_tailor.run_finish(None, out_dir=out)
        self.assertStartsEmpty(folders)


if __name__ == '__main__':
    unittest.main()
