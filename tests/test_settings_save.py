# -*- coding: utf-8 -*-
"""設定頁存檔:不蓋掉別處剛存的值、擋掉會讓程式做錯事的值。"""
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

    def test_two_different_files_cannot_share_a_send_name(self):
        resumes = [{'id': 'a', 'name': 'A', 'files': {}, 'enabled': True, 'send_names': {'zh': '我的履歷'}},
                   {'id': 'b', 'name': 'B', 'files': {}, 'enabled': True, 'send_names': {'zh': '我的履歷.pdf'}}]
        bad = sa.save({'set': {'resume.resumes': resumes}})
        self.assertTrue(any('重複' in x for x in bad))
        resumes[1]['send_names'] = {'zh': '我的履歷 B'}
        self.assertTrue(any('{resume}' in x for x in sa.save({'set': {'resume.resumes': resumes,
                                                                        'resume.merged_name': '合併檔'}})))
        self.assertEqual(sa.save({'set': {'resume.resumes': resumes, 'resume.merged_name': '{resume}+{n}'}}), [])
        self.assertEqual(self.saved()['resume']['merged_name'], '{resume}+{n}')

    def test_stale_page_cannot_overwrite_a_value_saved_elsewhere(self):
        page = self.page()
        # 在「🆕 新職缺」找缺那一列把分鐘數改成 30(伺服器照最新的檔只改那一格)
        self.assertEqual(sa.save({'set': {'search.find_minutes': 30}}), [])
        # 回到設定頁(手上還是打開時那一份)改別的、按儲存:要擋下來,不能把 30 蓋回去
        bad = sa.save({'set': {'search.flag_words': ['community']}, 'version': page['version']})
        self.assertEqual(bad, [sa.CONFLICT])
        self.assertEqual(self.saved()['search'].get('find_minutes'), 30)
        self.assertNotIn('flag_words', self.saved()['search'])

    def test_page_that_is_up_to_date_saves_and_can_save_again(self):
        page = self.page()
        self.assertEqual(sa.save({'set': {'search.flag_words': ['community']}, 'version': page['version']}), [])
        self.assertEqual(self.saved()['search']['flag_words'], ['community'])
        # 同一頁接著再存一次(例:上傳檔案後自動存):帶存完回來的新版本
        self.assertEqual(sa.save({'set': {'search.flag_words': ['community', 'crypto']}, 'version': sa.version()}), [])
        self.assertEqual(self.saved()['search']['flag_words'], ['community', 'crypto'])

    def test_ghost_days_must_match_the_range_on_the_page(self):
        # 清空會存成 0:下一次查應徵進度就把還沒回音的已投出卡全部記成沒下文
        for bad in (0, 3, -5, 121, '', '30', True):
            with self.subTest(ghost_days=bad):
                self.assertTrue(sa._check({'replies': {'ghost_days': bad}}), bad)
        for ok in (7, 30, 120):
            self.assertEqual(sa._check({'replies': {'ghost_days': ok}}), [], ok)
        self.assertEqual(sa._check({'replies': {}}), [])                  # 沒設 = 預設 30
        self.assertTrue(sa.save({'set': {'replies.ghost_days': 0}}))
        self.assertNotIn('replies', self.saved())

    def test_both_supported_agents_can_use_the_shared_browser_sequentially(self):
        settings = {'agent': {'agents': [
            {'id': 'a', 'runtime': 'codex', 'model': '', 'effort': 'high', 'browser': True},
            {'id': 'b', 'runtime': 'claude-code', 'model': 'haiku', 'effort': 'high', 'browser': True},
        ]}}
        self.assertEqual(sa._check(settings), [])
        self.assertEqual([a['browser'] for a in cf.migrate_settings(settings)['agent']['agents']], [True, True])

    def test_page_sends_only_the_field_he_changed_and_only_that_field_is_written(self):
        # 以前設定頁送整份(好幾塊從實際生效的值起頭、含預設),後台再猜哪些是他沒碰過的預設;
        # 猜錯就把當時的預設寫死進 jobsalvo.json,之後產品改預設(更新跟 main)他拿不到
        with open(os.path.join(self.home, cf.NAME), 'w', encoding='utf-8') as f:
            json.dump({'search': {'exclude_words': ['業務']}, 'replies': {'ghost_days': 30}}, f, ensure_ascii=False)
        cf.reload(self.home)
        page = self.page()
        self.assertIn('search.flag_words', page['fields'])
        self.assertEqual(sa.save({'set': {'search.flag_words': ['community']}, 'version': page['version']}), [])
        self.assertEqual(self.saved(), {'search': {'exclude_words': ['業務'], 'flag_words': ['community']},
                                        'replies': {'ghost_days': 30}})
        self.assertEqual(cf.C['search']['find_minutes'], cf.DEFAULTS['search']['find_minutes'])
        # 欄位表以外的路徑不寫(打錯字不會在設定檔裡長出一格沒人讀的設定)
        self.assertTrue(sa.save({'set': {'search.flag_word': ['x']}}))
        self.assertNotIn('flag_word', self.saved()['search'])

    def test_every_field_the_page_shows_is_a_field_it_can_save(self):
        # 設定頁從「實際生效的值」起頭,改哪一格就送哪一條路徑:每一條都要在欄位表裡,不然改了存不進去
        page = self.page()

        def leaves(d, pre=''):
            for k, v in d.items():
                if isinstance(v, dict) and v and pre + k not in page['fields']:
                    yield from leaves(v, pre + k + '.')
                else:
                    yield pre + k
        self.assertEqual(set(leaves(page['effective'])) - set(page['fields']), set())

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
        s = {'search.flag_words': ['community']}
        with mock.patch.object(sa, '_write', side_effect=OSError(28, 'No space left on device')):
            bad = sa.save({'set': s, 'texts': {'apply_rules': '連結欄填 GitHub'}, 'version': page['version']})
        self.assertEqual(len(bad), 1)
        self.assertIn('沒存成', bad[0])
        self.assertEqual(self.saved(), before)
        self.assertEqual(sa.save({'set': s, 'texts': {'apply_rules': '連結欄填 GitHub'}, 'version': page['version']}), [])

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
        path, why = sa.create_skill('', '只看 JD。', 'research')
        self.assertIsNone(path)
        self.assertTrue(why)
        bad = sa._check({'research': {'skills': {'judge': 'custom/skills/nope.md'}}})
        self.assertEqual(len(bad), 1)
        self.assertNotIn('skill', bad[0])                # GLOSSARY:不講 skill、不講資料夾路徑
        self.assertNotIn('custom/skills', bad[0])

    def test_callers_without_a_version_still_save(self):
        # 看板規矩檢查、「改用 X」、找缺分鐘數只改自己那一格,不帶版本
        self.assertEqual(sa.save({'set': {'search.flag_words': ['x']}}), [])
        # 檢查程式還原:整份換回它先前讀到的那一份
        self.assertEqual(sa.save({'settings': {'search': {'exclude_words': ['業務']}}}), [])
        self.assertEqual(self.saved(), {'search': {'exclude_words': ['業務']}})


if __name__ == '__main__':
    unittest.main()
