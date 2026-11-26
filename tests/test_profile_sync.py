"""平台履歷登記、選份與同步基準的測試。"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402
import config as cf  # noqa: E402
import board_doc  # noqa: E402
import markdown_pdf  # noqa: E402
import profile_registry  # noqa: E402
import source_sync  # noqa: E402
import profile_sync as ps  # noqa: E402
import sync_seed  # noqa: E402
import sync_material  # noqa: E402
import sync_records  # noqa: E402


class ProfileIdentity(unittest.TestCase):
    def setUp(self):
        _env.use_home(self)
        self.enterContext(patch.object(profile_registry, 'path', return_value=os.path.join(self.home, 'profiles.json')))

    def test_a_url_belongs_to_the_platform_whose_hosts_the_user_listed(self):
        self.assertIsNone(ps.platform_of('https://www.board.example/job/1'))
        self.assertEqual(ps.profile_key('https://www.board.example/job/1'), 'www.board.example')

        ps.set_hosts('my board', ['board.example'])

        self.assertEqual(ps.platform_of('https://www.board.example/job/1'), 'my board')
        self.assertEqual(ps.profile_key('https://board.example/job/1'), 'my board')
        self.assertEqual(ps.profile_key('https://other.example/job/1'), 'other.example')

    def test_registered_profile_urls_count_as_that_platforms_hosts(self):
        # 新登記、不在出廠預設裡的平台:清單帶入登記網址的主機;旁邊的子網域不猜,使用者編輯清單才算
        ps.remember('legacy', 'zh', 'general', 'https://pda.board.example/profile?vno=1')
        self.assertEqual(ps.platform_of('https://pda.board.example/other'), 'legacy')
        self.assertIsNone(ps.platform_of('https://www.board.example/job/1'))

        ps.set_hosts('legacy', ['board.example'])

        self.assertEqual(ps.platform_of('https://www.board.example/job/1'), 'legacy')

    # 答案來源:升級前(e7ad454)程式寫死的平台表 PLATFORMS,主機或它的子網域都算那個平台。
    OLD_TABLE = {'104': 'www.104.com.tw', 'cake': 'www.cakeresume.com', 'yourator': 'www.yourator.co',
                 'linkedin': 'www.linkedin.com'}

    def test_platforms_known_before_the_upgrade_are_still_known_without_registering_anything(self):
        for platform, host in self.OLD_TABLE.items():
            with self.subTest(platform=platform):
                self.assertEqual(ps.platform_of(f'https://{host}/job/1'), platform)
                self.assertEqual(ps.profile_key(f'https://{host}/job/1'), platform)
        self.assertEqual(ps.platform_of('https://pda.104.com.tw/profile'), '104')
        self.assertIsNone(ps.platform_of('https://www.board.example/job/1'))

    def test_an_old_registry_without_a_host_list_keeps_the_old_subdomains(self):
        with open(profile_registry.path(), 'w', encoding='utf-8') as file:
            json.dump({'104': {'zh/general': {'read': 'https://pda.104.com.tw/profile?vno=1',
                                              'edit': 'https://pda.104.com.tw/profile?vno=1'}}}, file)
        self.assertEqual(ps.platform_of('https://www.104.com.tw/job/1'), '104')

    def test_registering_a_platform_fills_its_editable_host_list(self):
        ps.remember('board', 'zh', 'general', 'https://pda.board.example/profile?vno=1')
        self.assertEqual(ps.registry()['board']['hosts'], ['pda.board.example'])
        ps.remember('104', 'zh', 'general', 'https://pda.104.com.tw/profile?vno=1')
        self.assertEqual(ps.registry()['104']['hosts'], ['104.com.tw'])
        ps.remember('jobs.site.example', 'zh', 'general', 'https://profiles.example/1')
        self.assertEqual(ps.registry()['jobs.site.example']['hosts'], ['profiles.example'])
        self.assertEqual(ps.platform_of('https://jobs.site.example/job/1'), 'jobs.site.example')
        self.assertEqual(ps.platform_of('https://profiles.example/1'), 'jobs.site.example')

        ps.set_hosts('board', ['board.example'])
        self.assertEqual(ps.registry()['board']['hosts'], ['board.example'])
        self.assertEqual(ps.platform_of('https://www.board.example/job/1'), 'board')

    def test_a_query_parameter_never_chooses_the_platform(self):
        # 7c15401 以前在 127.0.0.1 上讀 ?platform=104.com.tw,把測試頁當成 104
        url = 'http://127.0.0.1:8765/apply?platform=104.com.tw'
        self.assertIsNone(ps.platform_of(url))
        self.assertEqual(ps.profile_key(url), '127.0.0.1')

    def test_a_host_listed_by_two_platforms_is_not_guessed(self):
        ps.set_hosts('one', ['board.example'])
        ps.set_hosts('two', ['board.example'])
        self.assertIsNone(ps.platform_of('https://www.board.example/job/1'))
        ps.set_hosts('two', ['jobs.board.example'])
        self.assertEqual(ps.platform_of('https://jobs.board.example/job/1'), 'two')
        self.assertEqual(ps.platform_of('https://www.board.example/job/1'), 'one')


class ConfirmScreenSyncNotice(unittest.TestCase):
    """#591 US49:填好、等確認送出的卡用落後或清單沒看過的平台履歷時,確認送出畫面照實標出;用的是卡選的那一份。"""

    CARD = 'https://jobs.example/1'
    SHARED = 'https://platform.example/resume/shared'

    def setUp(self):
        _env.use_home(self, resume={'base': 'resume', 'langs': ['zh'], 'resumes': [
            {'id': variant, 'name': variant, 'files': {'zh': f'resume/{variant}.md'}, 'enabled': True}
            for variant in ('one', 'two')], 'attachments': []})
        for variant in ('one', 'two'):
            with open(os.path.join(self.home, 'resume', f'{variant}.md'), 'w', encoding='utf-8') as out:
                out.write(f'# {variant}\n- current\n')
        _env.make_board(board_doc.target())
        self.enterContext(patch.object(profile_registry, 'path', return_value=os.path.join(self.home, 'profiles.json')))
        self.platform = ps.profile_key(self.CARD)

    def confirm_lines(self, variant):
        import next_step
        fb = {self.CARD: {'app': 'ship', 'ds': 'parked', 'resume_id': variant, 'lang': 'zh', 'apply': {
            'delivery': {'method': 'platform_profile', 'profile_url': self.SHARED}}}}
        job = {'id': self.CARD, 'resume': {'recommend': variant, 'lang': 'zh'}}
        view = next_step.card(fb, self.CARD, job=job)['view']
        return [line[1] for line in view.get('line') or []]

    def test_the_notice_follows_the_selected_profile_when_two_profiles_share_a_read_url(self):
        # 答案來源:one 的基準是現在的母稿(跟上),two 的基準是舊稿(落後);兩份登記同一個讀頁網址
        for variant in ('one', 'two'):
            ps.remember(self.platform, 'zh', variant, self.SHARED)
        sync_seed.seed_sync_baseline(self.platform, 'zh', 'one', '# one\n- current\n', [], source_sync.master_fingerprint('zh', 'one'))
        sync_seed.seed_sync_baseline(self.platform, 'zh', 'two', '# two\n- old\n', [], 'old-two')

        self.assertIn('⚠ 平台履歷落後', self.confirm_lines('two'))
        self.assertFalse(any('落後' in line for line in self.confirm_lines('one')), self.confirm_lines('one'))

    def test_a_behind_profile_with_an_unreviewed_list_shows_both(self):
        # 答案來源:基準是舊稿(落後),最新一份清單沒人按看過
        ps.remember(self.platform, 'zh', 'one', self.SHARED)
        sync_seed.seed_sync_baseline(self.platform, 'zh', 'one', '# one\n- old\n', [], 'old-one')
        sync_records.remember_version(self.platform, 'zh', 'one', 'round-new', 'target-new', {})
        sync_records.remember_visuals(self.platform, 'zh', 'one', [{'evidence': 'round-new', 'item': '履歷'}])

        notice, = [line for line in self.confirm_lines('one') if line.startswith('⚠ ')]
        self.assertIn('最新同步清單尚未看過', notice)
        self.assertIn('平台履歷落後', notice)


class SyncBaseline(unittest.TestCase):
    def setUp(self):
        _env.use_home(self, resume={'base': 'resume', 'langs': ['zh'], 'resumes': [
            {'id': 'one', 'name': '通用版', 'files': {'zh': 'resume/one.md'}, 'enabled': True}],
            'attachments': []})
        with open(os.path.join(self.home, 'resume', 'one.md'), 'w', encoding='utf-8') as out:
            out.write('# 作品\n- 新項目\n')
        _env.make_board(board_doc.target())     # 同步要讀看板的常用答案;沒有看板就是讀不到、不算跟上
        self.enterContext(patch.object(profile_registry, 'path', return_value=os.path.join(self.home, 'profiles.json')))
        ps.remember('platform.example', 'zh', 'one', 'https://platform.example/resume/one')

    def test_reads_reuse_the_parsed_registry_until_a_write_replaces_it(self):
        # 登記表很大:沒換檔就不重新解析;寫入(換檔)之後讀到的是新內容(#607)
        first = ps.registry()
        self.assertIs(ps.registry(), first)
        ps.remember_name('platform.example', 'zh', 'one', '平台上的名字')
        self.assertIsNot(ps.registry(), first)
        self.assertEqual(ps.where('platform.example', 'zh', 'one')['name'], '平台上的名字')

    def test_status_tracks_master_edits_and_sync_baseline(self):
        sync_seed.seed_sync_baseline('platform.example', 'zh', 'one', '# 作品\n- 新項目\n', [], source_sync.master_fingerprint('zh', 'one'))
        self.assertEqual(ps.status()[0]['state'], 'ok')
        with open(os.path.join(self.home, 'resume', 'one.md'), 'w', encoding='utf-8') as out:
            out.write('# 作品\n- 更新的項目\n')
        self.assertEqual(ps.status()[0]['state'], 'master-changed')

    def test_a_legacy_baseline_without_a_master_fingerprint_uses_the_current_master_file(self):
        # 很舊的基準沒記原始履歷的指紋:轉換時拿現在那份原始履歷檔的指紋補上,加上附件的指紋
        import source_sync
        reg = ps._load_registry()
        entry = reg['platform.example']['zh/one']
        entry['sync_versions'] = []
        entry.update(sync_version='legacy-reviewed', sync_reviewed_version='legacy-reviewed',
                     sync_baseline={'source': '# 作品\n- 新項目\n', 'answers': []})
        ps._save_registry(reg)

        row, = sync_records.versions_for('platform.example', 'zh', 'one')
        master = source_sync.fingerprint(os.path.join(self.home, 'resume', 'one.md'))
        self.assertEqual(row['target_text']['__target_fingerprint__'],
                         f"{master}+{sync_material.attachments_fingerprint('zh', 'one')}")

    def test_status_migrates_a_reviewed_legacy_baseline_without_losing_it(self):
        reg = ps._load_registry()
        entry = reg['platform.example']['zh/one']
        entry['sync_versions'] = []
        entry.update(sync_version='legacy-reviewed', sync_reviewed_version='legacy-reviewed',
                     sync_baseline={'source': '# 作品\n- 新項目\n', 'answers': [], 'used_answers': [],
                                    'master': source_sync.master_fingerprint('zh', 'one'),
                                    'attachments': sync_material.attachments_fingerprint('zh', 'one')})
        ps._save_registry(reg)

        status, = ps.status()
        migrated = ps.where('platform.example', 'zh', 'one')

        self.assertEqual(status['state'], 'ok')
        self.assertEqual(status['sync_review'], {'version': 'legacy-reviewed', 'reviewed': True})
        self.assertEqual(migrated['sync_versions'][-1]['round_id'], 'legacy-reviewed')
        self.assertEqual(migrated['sync_versions'][-1]['reviewed_at'], 'legacy-reviewed')
        self.assertIn('sync_baseline', migrated)
        self.assertIn('sync_reviewed_version', migrated)

    def test_a_legacy_sync_nobody_reviewed_is_not_shown_as_caught_up_after_the_upgrade(self):
        # 答案來源:#601 第 19 條(沒按看過了就顯示跟上算沒過)、#591 US24。main 上同步過、本人沒按過「看過這一版」的登記,
        # 升級後要等本人按過才跟上;同步過的內容照樣保留。
        reg = ps._load_registry()
        entry = reg['platform.example']['zh/one']
        entry.pop('sync_versions', None)
        for key in ('sync_version', 'sync_reviewed_version', 'sync_pending_baseline', 'sync_visuals'):
            entry.pop(key, None)   # main 只存 sync_baseline:沒有「看過這一版」,也沒有版本號
        entry.update(sync_baseline={'source': '# 作品\n- 新項目\n', 'answers': [], 'used_answers': [],
                                    'master': source_sync.master_fingerprint('zh', 'one'),
                                    'attachments': sync_material.attachments_fingerprint('zh', 'one')})
        ps._save_registry(reg)

        status, = ps.status()

        self.assertFalse(status['sync_review']['reviewed'], status['sync_review'])
        self.assertNotEqual(status['state'], 'ok')

        ps.mark_sync_reviewed('platform.example', 'zh', 'one', status['sync_review']['version'])   # 本人按「看過這一版」
        self.assertTrue(ps.status()[0]['sync_review']['reviewed'])

    def test_the_first_new_sync_keeps_the_reviewed_legacy_baseline_before_it(self):
        reg = ps._load_registry()
        entry = reg['platform.example']['zh/one']
        entry.pop('sync_versions', None)
        entry.update(sync_version='legacy-reviewed', sync_reviewed_version='legacy-reviewed',
                     sync_baseline={'source': '# 作品\n- 新項目\n', 'answers': [], 'used_answers': [],
                                    'master': source_sync.master_fingerprint('zh', 'one'),
                                    'attachments': sync_material.attachments_fingerprint('zh', 'one')})
        ps._save_registry(reg)

        sync_records.remember_version('platform.example', 'zh', 'one', 'round-one', 'target-one', {})
        versions = ps.where('platform.example', 'zh', 'one')['sync_versions']

        self.assertEqual([(row['round_id'], row['reviewed_at']) for row in versions],
                         [('legacy-reviewed', 'legacy-reviewed'), ('round-one', None)])

    def test_latest_sync_review_marker_is_tracked(self):
        card = 'https://jobs.example/1'
        first = {'evidence': 'round-one', 'item': '作品', 'card': card,
                 'before_text': 'old', 'after_text': 'new', 'verdicts': ['同步改動']}
        sync_records.remember_version('platform.example', 'zh', 'one', 'round-one', 'target-one', {})
        sync_records.remember_visuals('platform.example', 'zh', 'one', [first])
        self.assertEqual(ps.status()[0]['sync_review'], {'version': 'round-one', 'reviewed': False})
        self.assertTrue(ps.mark_sync_reviewed('platform.example', 'zh', 'one', 'round-one'))
        self.assertEqual(ps.status()[0]['sync_review'], {'version': 'round-one', 'reviewed': True})

        latest = dict(first, evidence='round-two', before_text='new', after_text='newer')
        sync_records.remember_version('platform.example', 'zh', 'one', 'round-two', 'target-two', {})
        sync_records.remember_visuals('platform.example', 'zh', 'one', [latest])
        self.assertEqual(ps.status()[0]['sync_review'], {'version': 'round-two', 'reviewed': False})
        status = ps.status()[0]
        self.assertEqual(status['sync_visuals'], [{**latest, 'pages_compared': []}])

    def test_a_check_left_by_the_old_review_does_not_count_as_synced(self):
        # 舊考題(02233a0):舊版逐字比、agent 判讀留下的核對紀錄不算同步過;只有同步存檔成功記下的那一筆算
        reg = ps.registry()
        reg['_profile_checks'] = {'platform.example#zh/one': {'master': source_sync.master_fingerprint('zh', 'one'), 'matched': True,
                                                              'by': 'review'}}
        ps._save_registry(reg)
        self.assertEqual(ps.status()[0]['state'], 'unchecked')
        sync_seed.seed_sync_baseline('platform.example', 'zh', 'one', '# 作品\n- 新項目\n', [])
        self.assertEqual(ps.status()[0]['state'], 'ok')

    def test_an_attachment_change_makes_the_profile_behind_too(self):
        # 附件是平台履歷的一部分(本機是真相):要附的檔換了,這一份就落後,同步時一起處理;不另記一份附件核對
        cover = os.path.join(self.home, 'resume', 'cover.pdf')
        with open(cover, 'wb') as out:
            out.write(b'cover v1')
        cf.ATTACHMENTS[:] = [{'id': 'cover', 'name': '求職信', 'files': {'zh': 'resume/cover.pdf'}, 'enabled': True}]
        self.addCleanup(cf.ATTACHMENTS.clear)
        sync_seed.seed_sync_baseline('platform.example', 'zh', 'one', '# 作品\n- 新項目\n', [], source_sync.master_fingerprint('zh', 'one'))
        self.assertEqual(ps.status()[0]['state'], 'ok')
        with open(cover, 'wb') as out:
            out.write(b'cover v2')
        self.assertEqual(ps.status()[0]['state'], 'master-changed')
        self.assertNotIn('_attachment_checks', ps.registry())

    def _markdown_cover(self):
        """附件原稿是 Markdown,reconcile 已經把它排成 .rendered 裡的 PDF。回 (原稿, 排好的 PDF)。"""
        md = os.path.join(self.home, 'resume', 'cover.md')
        with open(md, 'w', encoding='utf-8') as out:
            out.write('# 求職信\n')
        pdf = markdown_pdf.output_path(md)
        os.makedirs(os.path.dirname(pdf), exist_ok=True)
        with open(pdf, 'wb') as out:
            out.write(b'%PDF-1 rendered')
        cf.ATTACHMENTS[:] = [{'id': 'cover', 'name': '求職信', 'files': {'zh': 'resume/cover.md'}, 'enabled': True}]
        self.addCleanup(cf.ATTACHMENTS.clear)
        return md, pdf

    def test_markdown_attachment_fingerprint_uses_the_pdf_the_card_sends(self):
        # 卡片寄的是排好的 PDF,同步基準也應該指向同一份 PDF。
        _md, pdf = self._markdown_cover()
        self.assertEqual(sync_material.slot_attachments('zh', 'one'), [('求職信', pdf)])

    def test_markdown_attachment_fingerprint_follows_the_rendered_pdf_bytes(self):
        # 比的是排好的 PDF,落後與否也看它:PDF 重排了就落後;原稿改了但還沒重排,平台上該有的檔還沒變
        md, pdf = self._markdown_cover()
        sync_seed.seed_sync_baseline('platform.example', 'zh', 'one', '# 作品\n- 新項目\n', [], source_sync.master_fingerprint('zh', 'one'))
        self.assertEqual(ps.status()[0]['state'], 'ok')
        with open(pdf, 'wb') as out:
            out.write(b'%PDF-1 rendered again')
        self.assertEqual(ps.status()[0]['state'], 'master-changed')
        sync_seed.seed_sync_baseline('platform.example', 'zh', 'one', '# 作品\n- 新項目\n', [], source_sync.master_fingerprint('zh', 'one'))
        with open(md, 'w', encoding='utf-8') as out:
            out.write('# 求職信(改過,還沒重排)\n')
        self.assertEqual(ps.status()[0]['state'], 'ok')
