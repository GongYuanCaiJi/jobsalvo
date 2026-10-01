# -*- coding: utf-8 -*-
"""設定頁存檔:不蓋掉別處剛存的值、擋掉會讓程式做錯事的值。"""
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
import config as cf  # noqa: E402
import settings_api as sa  # noqa: E402


class SettingsSave(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(tempfile.TemporaryDirectory(prefix='settings-save-'))
        old = cf.HOME
        self.addCleanup(lambda: cf.reload(old))
        with open(os.path.join(self.home, cf.NAME), 'w', encoding='utf-8') as f:
            json.dump({'search': {'exclude_words': ['業務']}}, f, ensure_ascii=False)
        cf.reload(self.home)

    def page(self):
        """設定頁打開時讀的那一份(環境檢查換成假的,不跑外部指令)。"""
        with mock.patch('doctor.check_environment', return_value={'ok': True, 'checks': []}):
            return sa.get()

    def saved(self):
        with open(os.path.join(self.home, cf.NAME), encoding='utf-8') as f:
            return json.load(f)

    def test_stale_page_cannot_overwrite_a_value_saved_elsewhere(self):
        page = self.page()
        # 在「🆕 新職缺」找缺那一列把分鐘數改成 30(伺服器照最新的檔只改那一格)
        s = cf.user_settings()
        s.setdefault('search', {})['find_minutes'] = 30
        self.assertEqual(sa.save({'settings': s}), [])
        # 回到設定頁(手上還是打開時那一份)改別的、按儲存:要擋下來,不能把 30 蓋回去
        stale = copy.deepcopy(page['settings'])
        stale['search']['flag_words'] = ['community']
        bad = sa.save({'settings': stale, 'texts': {}, 'version': page['version']})
        self.assertEqual(bad, [sa.CONFLICT])
        self.assertEqual(self.saved()['search'].get('find_minutes'), 30)
        self.assertNotIn('flag_words', self.saved()['search'])

    def test_page_that_is_up_to_date_saves_and_can_save_again(self):
        page = self.page()
        s = copy.deepcopy(page['settings'])
        s['search']['flag_words'] = ['community']
        self.assertEqual(sa.save({'settings': s, 'version': page['version']}), [])
        self.assertEqual(self.saved()['search']['flag_words'], ['community'])
        # 同一頁接著再存一次(例:上傳檔案後自動存):帶存完回來的新版本
        s['search']['flag_words'] = ['community', 'crypto']
        self.assertEqual(sa.save({'settings': s, 'version': sa.version()}), [])
        self.assertEqual(self.saved()['search']['flag_words'], ['community', 'crypto'])

    def test_ghost_days_must_match_the_range_on_the_page(self):
        # 清空會存成 0:下一次查應徵進度就把還沒回音的已投出卡全部記成沒下文
        for bad in (0, 3, -5, 121, '', '30', True):
            with self.subTest(ghost_days=bad):
                self.assertTrue(sa._check({'replies': {'ghost_days': bad}}), bad)
        for ok in (7, 30, 120):
            self.assertEqual(sa._check({'replies': {'ghost_days': ok}}), [], ok)
        self.assertEqual(sa._check({'replies': {}}), [])                  # 沒設 = 預設 30
        s = cf.user_settings()
        s['replies'] = {'ghost_days': 0}
        self.assertTrue(sa.save({'settings': s}))
        self.assertNotIn('replies', self.saved())

    def test_defaults_he_never_set_are_not_frozen_into_the_file(self):
        # 存一次設定就把當時的預設值整份寫進 jobsalvo.json:之後產品改預設(更新跟 main),他拿不到
        with open(os.path.join(self.home, cf.NAME), 'w', encoding='utf-8') as f:
            json.dump({'search': {'exclude_words': ['業務']}, 'replies': {'ghost_days': 30}}, f, ensure_ascii=False)
        cf.reload(self.home)
        page = self.page()
        w = copy.deepcopy(page['settings'])          # 跟 board.js cfgLoad 一樣:這幾塊從實際生效的值(含預設)起頭
        for k in ('agent', 'browser', 'search', 'replies', 'flow'):
            w[k] = copy.deepcopy(page['effective'][k])
        w.setdefault('board', {}).update(categories=copy.deepcopy(page['effective']['board']['categories']),
                                         tags=copy.deepcopy(page['effective']['board']['tags']))
        w['search']['flag_words'] = ['community']   # 這次他改的
        self.assertEqual(sa.save({'settings': w, 'version': page['version']}), [])
        saved = self.saved()
        self.assertEqual(saved['search'], {'exclude_words': ['業務'], 'flag_words': ['community']})
        self.assertEqual(saved['replies'], {'ghost_days': 30})          # 檔裡原本就有的,跟預設一樣也照留
        for k in ('agent', 'browser', 'flow', 'board'):
            self.assertNotIn(k, saved)
        self.assertEqual(cf.C['search']['find_minutes'], cf.DEFAULTS['search']['find_minutes'])

    def test_fill_max_blank_is_described_as_what_the_program_does(self):
        # 那一格清空:自動流程照預設 5 張停(test_autopilot);存檔檢查的訊息、設定註解以前都說「空的是不限」
        msg = sa._check({'flow': {'fill_max': '很多張'}})
        self.assertEqual(len(msg), 1)
        self.assertNotIn('空的是不限', msg[0])
        self.assertIn('空的照預設 5', msg[0])
        self.assertEqual(sa._check({'flow': {'fill_max': ''}}), [])
        with open(cf.__file__, encoding='utf-8') as f:
            line = next(x for x in f if "'fill_max':" in x)
        self.assertNotIn('空的或 0 = 不限', line)

    def test_a_write_that_fails_halfway_says_so_and_leaves_the_settings_as_they_were(self):
        # 以前先寫 jobsalvo.json 再寫文字設定:後面失敗就丟例外、連線斷掉,畫面說沒存成,設定其實已經換了;
        # 他再按一次又因為版本對不上被擋
        page = self.page()
        before = self.saved()
        s = copy.deepcopy(page['settings'])
        s['search']['flag_words'] = ['community']
        with mock.patch.object(sa, '_write', side_effect=OSError(28, 'No space left on device')):
            bad = sa.save({'settings': s, 'texts': {'apply_rules': '連結欄填 GitHub'}, 'version': page['version']})
        self.assertEqual(len(bad), 1)
        self.assertIn('沒存成', bad[0])
        self.assertEqual(self.saved(), before)
        self.assertEqual(sa.save({'settings': s, 'texts': {'apply_rules': '連結欄填 GitHub'}, 'version': page['version']}), [])

    def test_file_of_an_unchecked_language_is_named_by_the_resume_he_named(self):
        # 以前講「履歷 'resume1' 有未設定的語言或無效檔案路徑」:代號不是他取的名字,也看不出是哪個語言
        bad = sa._check({'resume': {'langs': ['zh'], 'resumes': [
            {'id': 'resume1', 'name': '工程版', 'files': {'zh': 'resume/a.md', 'en': 'resume/b.md'}}]}})
        self.assertEqual(len(bad), 1)
        self.assertIn('「工程版」', bad[0])
        self.assertIn('en', bad[0])
        self.assertNotIn("'resume1'", bad[0])

    def test_research_ways_and_resume_rules_are_told_apart(self):
        # 兩種放同一個資料夾、同一份清單:找缺的做法會出現在履歷的「改履歷的規則」選單,存的時候還講成改履歷的規則
        sa.create_skill('我的判斷', '只看 JD。', 'research')
        sa.create_skill('工程履歷重點', '突出成果。')
        self.assertEqual({x['name']: x['kind'] for x in sa.skill_files()},
                         {'我的判斷': 'research', '工程履歷重點': 'resume'})
        self.assertEqual(sa.create_skill('', '只看 JD。', 'research'), (None, '先寫找缺與判斷的做法的名稱'))
        bad = sa._check({'research': {'skills': {'judge': 'custom/skills/nope.md'}}})
        self.assertEqual(len(bad), 1)
        self.assertNotIn('skill', bad[0])                # GLOSSARY:不講 skill、不講資料夾路徑
        self.assertNotIn('custom/skills', bad[0])

    def test_callers_without_a_version_still_save(self):
        # 看板規矩檢查、「改用 X」、找缺分鐘數都是讀最新的檔再存,不帶版本
        s = cf.user_settings()
        s['search']['flag_words'] = ['x']
        self.assertEqual(sa.save({'settings': s}), [])



class LegacySettingsConversion(unittest.TestCase):
    """讀設定時把舊格式寫回成新格式回不了頭:跟看板的轉換走同一個入口(folder_history.convert),
    先留退回點;沒有就不寫回(這一次照樣用記憶體裡轉好的跑)。平常讀設定不碰 git。"""
    LEGACY = {'agent': {'runtime': 'codex', 'alt_runtime': 'command-code'}}

    def setUp(self):
        import folder_history
        self.fh = folder_history
        self.home = self.enterContext(tempfile.TemporaryDirectory(prefix='legacy-settings-'))
        self.path = os.path.join(self.home, cf.NAME)
        self.write(self.LEGACY)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, settings):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(settings, f)

    def on_disk(self, path=None):
        with open(path or self.path, encoding='utf-8') as f:
            return json.load(f)

    def test_the_version_before_rewriting_is_the_old_format(self):
        import subprocess
        loaded = cf.user_settings(self.home)

        git = self.fh._git()
        before = subprocess.run([git, 'log', '-1', '--format=%H', '--grep=轉換前'], cwd=self.home, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()
        self.assertTrue(before)
        old = json.loads(subprocess.run([git, 'show', before + ':' + cf.NAME], cwd=self.home, text=True,
                                        stdout=subprocess.PIPE, check=True).stdout)
        self.assertEqual(old, self.LEGACY)
        self.assertEqual(self.on_disk(), loaded)
        self.assertIn('agents', loaded['agent'])

    def test_without_version_history_the_old_settings_are_backed_up_first(self):
        with mock.patch.object(self.fh, '_git', return_value=None):
            loaded = cf.user_settings(self.home)

        folder = os.path.join(self.home, self.fh.BACKUP_DIR)
        [backup] = os.listdir(folder)
        self.assertEqual(self.on_disk(os.path.join(folder, backup)), self.LEGACY)
        self.assertEqual(self.on_disk(), loaded)

    def test_without_a_restore_point_the_file_is_left_alone(self):
        with open(os.path.join(self.home, self.fh.BACKUP_DIR), 'w', encoding='utf-8') as f:
            f.write('a file where the backup folder should go')
        with mock.patch.object(self.fh, '_git', return_value=None):
            loaded = cf.user_settings(self.home)
            self.assertIn('沒有退回點', self.fh.status(self.home)['conversion'])

        self.assertEqual(self.on_disk(), self.LEGACY)
        self.assertIn('agents', loaded['agent'])   # 這一次照樣用轉好的跑

    def test_reading_settings_that_need_no_conversion_never_runs_git(self):
        self.write({'agent': {'agents': [{'id': 'a', 'runtime': 'codex'}]}})
        with mock.patch('subprocess.run', side_effect=AssertionError('讀設定跑了外部指令')):
            cf.user_settings(self.home)
        self.assertFalse(os.path.exists(os.path.join(self.home, '.git')))


if __name__ == '__main__':
    unittest.main()
