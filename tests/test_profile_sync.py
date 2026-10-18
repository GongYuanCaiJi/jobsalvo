"""平台履歷登記、選份與同步基準的測試。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402
import config as cf  # noqa: E402
import profile_sync as ps  # noqa: E402


class ProfileIdentity(unittest.TestCase):
    def test_new_single_profile_without_saved_name_can_be_selected_on_any_platform(self):
        with tempfile.TemporaryDirectory() as home, patch.object(ps, 'REG', os.path.join(home, 'profiles.json')):
            ps.remember('platform.example', 'zh', 'one', 'https://platform.example/resume/one')
            page = {'fields': [{'label': 'Resume', 'type': 'select-one',
                                'value': 'resume-1', 'shown': '我的新履歷'}], 'lines': []}
            decision = {'platform': 'platform.example', 'lang': 'zh', 'variant': 'one',
                        'profile_kind': 'fixed', 'fixed_url': 'https://platform.example/resume/one'}
            self.assertEqual(ps.picked_problem(page, {'id': 'https://platform.example/job/one'}, {},
                                               'https://platform.example/job/one', decision), '')

    def test_unselected_option_text_does_not_override_the_observed_profile_selection(self):
        names = {'en/general': '固定平台履歷'}
        page = {'fields': [{'label': 'Platform resume (required)', 'type': 'select-one',
                            'value': 'custom-1', 'shown': '驗收合成履歷'}],
                'lines': ['Select a platform resume...', '固定平台履歷', '驗收合成履歷']}
        self.assertEqual(ps.picked(page, names), set())
        page['fields'][0].update(value='fixed', shown='固定平台履歷')
        self.assertEqual(ps.picked(page, names), {'en/general'})

    def test_platform_is_recognised_from_the_job_url(self):
        self.assertEqual(ps.platform_of('https://www.104.com.tw/job/abc'), '104')
        self.assertEqual(ps.platform_of('https://www.linkedin.com/jobs/view/123'), 'linkedin')

    def test_local_fake_platform_uses_its_explicit_platform_query(self):
        self.assertEqual(ps.platform_of('http://127.0.0.1:8765/apply?platform=104.com.tw'), '104')


class SyncBaseline(unittest.TestCase):
    def setUp(self):
        _env.use_home(self, resume={'base': 'resume', 'langs': ['zh'], 'resumes': [
            {'id': 'one', 'name': '通用版', 'files': {'zh': 'resume/one.md'}, 'enabled': True}],
            'attachments': []})
        with open(os.path.join(self.home, 'resume', 'one.md'), 'w', encoding='utf-8') as out:
            out.write('# 作品\n- 新項目\n')
        self.enterContext(patch.object(ps, 'REG', os.path.join(self.home, 'profiles.json')))
        ps.remember('platform.example', 'zh', 'one', 'https://platform.example/resume/one')

    def test_status_tracks_master_edits_and_sync_baseline(self):
        ps.remember_sync('platform.example', 'zh', 'one', '# 作品\n- 新項目\n', [], ps._master_fp('zh', 'one'))
        self.assertEqual(ps.status()[0]['state'], 'ok')
        with open(os.path.join(self.home, 'resume', 'one.md'), 'w', encoding='utf-8') as out:
            out.write('# 作品\n- 更新的項目\n')
        self.assertEqual(ps.status()[0]['state'], 'master-changed')

    def test_a_check_left_by_the_old_review_does_not_count_as_synced(self):
        # 舊考題(02233a0):舊版逐字比、agent 判讀留下的核對紀錄不算同步過;只有同步存檔成功記下的那一筆算
        reg = ps.registry()
        reg['_profile_checks'] = {'platform.example#zh/one': {'master': ps._master_fp('zh', 'one'), 'matched': True,
                                                              'by': 'review'}}
        ps._save_registry(reg)
        self.assertEqual(ps.status()[0]['state'], 'unchecked')
        ps.remember_sync('platform.example', 'zh', 'one', '# 作品\n- 新項目\n', [])
        self.assertEqual(ps.status()[0]['state'], 'ok')

    def test_an_attachment_change_makes_the_profile_behind_too(self):
        # 附件是平台履歷的一部分(本機是真相):要附的檔換了,這一份就落後,同步時一起處理;不另記一份附件核對
        cover = os.path.join(self.home, 'resume', 'cover.pdf')
        with open(cover, 'wb') as out:
            out.write(b'cover v1')
        cf.ATTACHMENTS[:] = [{'id': 'cover', 'name': '求職信', 'files': {'zh': 'resume/cover.pdf'}, 'enabled': True}]
        self.addCleanup(cf.ATTACHMENTS.clear)
        ps.remember_sync('platform.example', 'zh', 'one', '# 作品\n- 新項目\n', [], ps._master_fp('zh', 'one'))
        self.assertEqual(ps.status()[0]['state'], 'ok')
        with open(cover, 'wb') as out:
            out.write(b'cover v2')
        self.assertEqual(ps.status()[0]['state'], 'master-changed')
        self.assertNotIn('_attachment_checks', ps.registry())
