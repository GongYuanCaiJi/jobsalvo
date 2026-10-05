#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""背景服務(launchd)啟動的看板:補上使用者 shell 裡的設定(#384);設定檔裡舊版留下的設定寫回拿掉。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  測試跑在暫存資料夾
import config as cf  # noqa: E402
import shell_env  # noqa: E402


class ShellEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.addCleanup(setattr, shell_env, 'STATUS', shell_env.STATUS)   # 別的測試的環境檢查不受影響

    def shell(self, body):
        path = os.path.join(self.tmp, 'fake-shell')
        with open(path, 'w') as f:
            f.write('#!/bin/sh\n' + body)
        os.chmod(path, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions — 測試用的假 shell 要能執行,只給自己
        return path

    def test_service_picks_up_what_the_login_shell_sets(self):
        # rc 檔印出的雜訊(歡迎詞)不能混進來;JOBSALVO_* 留服務自己的
        shell = self.shell('echo "Welcome back"\n'
                           'export CODEX_HOME=/elsewhere/codex JOBSALVO_HOME=/from/shell\n'
                           'shift; eval "$1"\n')
        environ = {'JOBSALVO_HOME': '/service/home', 'PATH': '/usr/bin'}
        self.assertEqual(shell_env.adopt(environ, shell), '')
        self.assertEqual(environ['CODEX_HOME'], '/elsewhere/codex')
        self.assertEqual(environ['JOBSALVO_HOME'], '/service/home')
        self.assertNotIn('Welcome', ''.join(environ))

    def test_a_shell_that_cannot_start_is_reported_not_hidden(self):
        environ = {'PATH': '/usr/bin'}
        problem = shell_env.adopt(environ, self.shell('exit 3\n'))
        self.assertIn('結束碼 3', problem)
        self.assertEqual(shell_env.STATUS, problem)                 # 環境檢查照實寫
        self.assertEqual(environ, {'PATH': '/usr/bin'})


class UpgradeSettingsFile(unittest.TestCase):
    def test_settings_no_longer_used_are_removed_from_the_file_and_nothing_else_changes(self):
        home = tempfile.mkdtemp()
        f = os.path.join(home, cf.NAME)
        mine = {'board': {'port': 8899}, 'browser': {'profile': 'Default', 'state': '~/x.json'}}
        with open(f, 'w', encoding='utf-8') as fh:
            json.dump(mine, fh)
        self.assertTrue(cf.upgrade_file(home))
        with open(f, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh), {'board': {'port': 8899}})
        self.assertFalse(cf.upgrade_file(home))                      # 沒有要拿掉的就不寫


if __name__ == '__main__':
    unittest.main()
