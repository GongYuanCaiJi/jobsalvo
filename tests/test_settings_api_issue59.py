import copy
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
from _env import read_fb  # noqa: E402
import agent_report  # noqa: E402
import config as cf  # noqa: E402
import settings_api as sa  # noqa: E402


class SettingsApiIssue59(unittest.TestCase):
    def setUp(self):
        _env.use_home(self, board={'file': 'board.html'}, resume={
            'langs': ['zh'], 'resumes': [{'id': 'general', 'name': '通用', 'files': {'zh': 'resume/main.md'}}]})
        with open(os.path.join(self.home, 'resume', 'main.md'), 'w', encoding='utf-8') as target:
            target.write('# Source\n')
        _env.make_board(os.path.join(self.home, 'board.html'))


    def test_legacy_builder_is_removed_and_reported_once_without_execution(self):
        sentinel = os.path.join(self.tmp, 'command-ran')
        settings = copy.deepcopy(self.settings)
        settings['resume']['build_cmd'] = f'python3 -c "open({sentinel!r}, \'w\').write(\'ran\')"'
        path = os.path.join(self.home, cf.NAME)
        with open(path, 'w', encoding='utf-8') as target:
            json.dump(settings, target, ensure_ascii=False)
        self.assertTrue(sa._retire_legacy_builder())
        with open(path, encoding='utf-8') as source:
            saved = json.load(source)
        self.assertNotIn('build_cmd', saved['resume'])
        self.assertFalse(os.path.exists(sentinel))
        fb = read_fb(cf.LIVE)
        reports = agent_report.open_items(fb)
        self.assertEqual(len(reports), 1)
        self.assertIn('Markdown', reports[0]['msg'])
        with open(cf.LIVE, 'rb') as source:
            board_after = source.read()
        with open(path, 'rb') as source:
            config_after = source.read()
        self.assertEqual(sa._retire_legacy_builder(), [])
        with open(cf.LIVE, 'rb') as source:
            self.assertEqual(source.read(), board_after)
        with open(path, 'rb') as source:
            self.assertEqual(source.read(), config_after)

    def test_legacy_builder_notice_survives_config_migration_with_profile_command(self):
        settings = copy.deepcopy(self.settings)
        settings['resume']['build_cmd'] = 'legacy build command'
        settings['resume']['profile_cmd'] = 'legacy profile command'
        path = os.path.join(self.home, cf.NAME)
        with open(path, 'w', encoding='utf-8') as target:
            json.dump(settings, target, ensure_ascii=False)

        cf._PENDING_NOTICES.clear()
        with mock.patch.object(agent_report, 'report') as report:
            cf.reload(self.home)

        with open(path, encoding='utf-8') as source:
            saved = json.load(source)
        self.assertNotIn('build_cmd', saved['resume'])
        self.assertNotIn('profile_cmd', saved['resume'])
        messages = [call.args[1] for call in report.call_args_list]
        self.assertTrue(any('resume.build_cmd' in message and 'Markdown' in message
                            for message in messages))
        self.assertTrue(any('平台履歷指令' in message for message in messages))

    def test_legacy_builder_notice_is_not_repeated_by_the_settings_page(self):
        settings = copy.deepcopy(self.settings)
        settings['resume']['build_cmd'] = 'legacy build command'
        path = os.path.join(self.home, cf.NAME)
        with open(path, 'w', encoding='utf-8') as target:
            json.dump(settings, target, ensure_ascii=False)

        cf._PENDING_NOTICES.clear()
        with mock.patch.object(agent_report, 'report') as report:
            cf.reload(self.home)
            page = sa.get()

        with open(path, encoding='utf-8') as source:
            saved = json.load(source)
        self.assertNotIn('build_cmd', saved['resume'])
        notices = [call.args[1] for call in report.call_args_list
                   if 'resume.build_cmd' in call.args[1]]
        self.assertEqual(notices, [cf.LEGACY_BUILD_CMD_REMOVAL_NOTICE])
        self.assertEqual(page['migration_notices'], [])

    def test_source_symlink_reads_target_but_upload_cannot_write_outside_home(self):
        target = os.path.join(self.tmp, 'external.md')
        with open(target, 'w', encoding='utf-8') as source:
            source.write('external original')
        link = os.path.join(self.home, 'resume', 'external.md')
        os.symlink(target, link)
        text, error = sa.text_of('resume/external.md')
        self.assertEqual((text, error), ('external original', ''))
        self.assertIsNone(sa.safe_rel('resume/external.md'))
        rel, _error = sa.put_file('resume/external.md', b'replacement')
        self.assertIsNone(rel)
        with open(target, encoding='utf-8') as source:
            self.assertEqual(source.read(), 'external original')

    def test_markdown_extension_is_readable_as_resume_material(self):
        path = os.path.join(self.home, 'resume', 'source.markdown')
        with open(path, 'w', encoding='utf-8') as source:
            source.write('# Markdown extension\n\nResume body.')

        text, error = sa.text_of('resume/source.markdown')

        self.assertEqual((text, error), ('# Markdown extension\n\nResume body.', ''))

    def test_settings_page_lists_multi_page_markdown_source(self):
        import source_sync
        entry = next(source_sync.files())
        manifest = {source_sync.page_key(entry): 2}
        with open(os.path.join(self.home, '.reconcile-manifest.json'), 'w', encoding='utf-8') as target:
            json.dump(manifest, target)
        self.assertEqual(sa.markdown_warnings(), [{
            'kind': 'resume', 'id': 'general', 'lang': 'zh', 'name': 'main.md', 'pages': 2,
        }])
        import ship
        got = ship.card_files({'id': 'test://jobs/md', 'resume': {'recommend': 'general', 'lang': 'zh'}}, {})
        self.assertTrue(got['files'][0]['preview'])      # markdown 原始檔照樣有預覽


if __name__ == '__main__':
    unittest.main()
