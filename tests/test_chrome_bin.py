import json
import os
import tempfile
import unittest
from unittest import mock

import _env  # noqa: F401

import chrome_bin


class ChromeBinary(unittest.TestCase):
    def test_google_chrome_is_preferred_over_chromium(self):
        """Ubuntu 的 chromium 是 snap:它有自己的 /tmp,讀不到程式放在 /tmp 的網頁,PDF 也寫不回來。"""
        found = {'chromium': '/usr/bin/chromium', 'google-chrome': '/usr/bin/google-chrome'}
        with mock.patch.object(chrome_bin.os.path, 'isfile', return_value=False), \
             mock.patch.object(chrome_bin.shutil, 'which', side_effect=found.get):
            self.assertEqual(chrome_bin.chrome()[0], '/usr/bin/google-chrome')

    def test_chromium_is_used_when_it_is_the_only_one(self):
        with mock.patch.object(chrome_bin.os.path, 'isfile', return_value=False), \
             mock.patch.object(chrome_bin.shutil, 'which', side_effect={'chromium': '/usr/bin/chromium'}.get):
            self.assertEqual(chrome_bin.chrome()[0], '/usr/bin/chromium')


class FindChrome(unittest.TestCase):
    """開 agent 的 Chrome、印 PDF、環境檢查用同一套找法(#159:以前開 agent 的 Chrome 寫死 /Applications)。"""

    def test_chrome_bin_env_wins(self):
        with tempfile.NamedTemporaryFile() as exe, mock.patch.dict(os.environ, {'CHROME_BIN': exe.name}):
            self.assertEqual(chrome_bin.find(), exe.name)

    def test_chrome_in_home_applications_is_found(self):
        home_app = os.path.expanduser('~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        env = {k: v for k, v in os.environ.items() if k != 'CHROME_BIN'}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(chrome_bin.os.path, 'isfile', side_effect=lambda p: p == home_app), \
             mock.patch.object(chrome_bin.shutil, 'which', return_value=None):
            self.assertEqual(chrome_bin.find(), home_app)

    def test_no_chrome_is_empty(self):
        env = {k: v for k, v in os.environ.items() if k != 'CHROME_BIN'}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(chrome_bin.os.path, 'isfile', return_value=False), \
             mock.patch.object(chrome_bin.shutil, 'which', return_value=None):
            self.assertEqual(chrome_bin.find(), '')


class Profiles(unittest.TestCase):
    """設定檔清單:讀 Chrome 自己的 Local State,顯示右上角看到的名字、標出裝了哪個官方擴充功能。"""

    def test_lists_names_and_extensions(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, 'Local State'), 'w', encoding='utf-8') as f:
                json.dump({'profile': {'info_cache': {'Profile 7': {'name': '工作用'}, 'Default': {'name': 'A 個人'}}}}, f)
            os.makedirs(os.path.join(root, 'Profile 7', 'Extensions', chrome_bin.EXTENSIONS['codex'][0]))
            os.makedirs(os.path.join(root, 'Default'))
            got = chrome_bin.profiles(root)
        self.assertEqual(got, [{'dir': 'Default', 'name': 'A 個人', 'ext': []},
                               {'dir': 'Profile 7', 'name': '工作用', 'ext': ['codex']}])

    def test_deleted_profiles_are_left_out_and_same_names_get_their_folder(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, 'Local State'), 'w', encoding='utf-8') as f:
                json.dump({'profile': {'info_cache': {'Profile 1': {'name': 'me'}, 'Profile 2': {'name': 'me'},
                                                      'Gone': {'name': '舊的'}}}}, f)
            for d in ('Profile 1', 'Profile 2'):
                os.makedirs(os.path.join(root, d))             # Gone 的資料夾已經刪了,Chrome 還記著名字
            got = [p['name'] for p in chrome_bin.profiles(root)]
        self.assertEqual(got, ['me(Profile 1)', 'me(Profile 2)'])

    def test_no_chrome_is_empty_list(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(chrome_bin.profiles(root), [])


if __name__ == '__main__':
    unittest.main()
