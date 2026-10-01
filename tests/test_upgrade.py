# -*- coding: utf-8 -*-
"""升級相容:舊版的設定檔(tests/fixtures/settings-history/,全是假資料)換成新程式也要能用。
每一份:讀得懂、轉完驗證會過、再轉一次不會變、看板真的能用它起來。"""
import glob, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
import config as cf  # noqa: E402
import settings_api  # noqa: E402

FIXTURES = sorted(glob.glob(os.path.join(HERE, 'fixtures', 'settings-history', '*.json')))


class Upgrade(unittest.TestCase):
    def test_there_are_old_versions_to_upgrade(self):
        self.assertGreaterEqual(len(FIXTURES), 3)

    def test_every_old_settings_file_still_works(self):
        for path in FIXTURES:
            with self.subTest(os.path.basename(path)):
                with open(path, encoding='utf-8') as f:
                    old = json.load(f)
                home = self.enterContext(tempfile.TemporaryDirectory(prefix='upgrade-'))
                new = cf.migrate_settings(old, home=home)
                self.assertEqual(settings_api._check(new), [], '舊設定轉完驗證不過')
                self.assertEqual(cf.migrate_settings(new, home=home), new, '轉第二次又變了(轉換不穩)')
                for gone in ('fetch',):
                    self.assertNotIn(gone, new)
                self.assertNotIn('variants', new.get('resume') or {})
                # 放進資料夾,用設定檔讀進來,要讀得到、不當成壞掉的
                with open(os.path.join(home, cf.NAME), 'w', encoding='utf-8') as f:
                    json.dump(old, f, ensure_ascii=False)
                old_home = cf.HOME
                try:
                    cf.reload(home)
                    self.assertFalse(cf.settings_problem(), '舊設定被當成壞掉的')
                    self.assertNotIn('title', cf.C['board'])      # 看板標題不再是設定;舊檔裡有的拿掉
                finally:
                    cf.reload(old_home)

    def test_old_variants_become_resume_list_without_losing_files(self):
        with open(os.path.join(HERE, 'fixtures', 'settings-history', '2026-09-early-variants.json'), encoding='utf-8') as f:
            old = json.load(f)
        new = cf.migrate_settings(old, home=tempfile.mkdtemp(prefix='upgrade-'))
        rs = {r['id']: r for r in new['resume']['resumes']}
        self.assertEqual(rs['general']['files'], {'zh': 'resume/general-zh.pdf', 'en': 'resume/general-en.pdf'})
        self.assertFalse(rs['data']['enabled'])
        self.assertNotIn('title', new.get('board') or {})      # 舊版預設標題不當成他取的名字

    def test_only_one_agent_keeps_the_browser(self):
        # 舊設定勾了兩個 agent 都能用 Chrome:只留最上面那個,另一個關掉;兩個都勾的存不進去
        old = {'agent': {'agents': [
            {'id': 'a', 'runtime': 'claude-code', 'model': '', 'effort': 'low', 'browser': True},
            {'id': 'b', 'runtime': 'codex', 'model': '', 'effort': 'max', 'browser': True}]}}
        new = cf.migrate_settings(old, home=tempfile.mkdtemp(prefix='upgrade-'))
        self.assertEqual([a['browser'] for a in new['agent']['agents']], [True, False])
        self.assertIn('只能有一個 agent 可使用 Chrome', settings_api._check(old))
        self.assertNotIn('只能有一個 agent 可使用 Chrome', settings_api._check(new))


if __name__ == '__main__':
    unittest.main()
