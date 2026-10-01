import hashlib
import pathlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
from _env import read_board  # noqa: E402
import agent_report  # noqa: E402
import board_doc as bd  # noqa: E402
import card  # noqa: E402
import config as cf  # noqa: E402
import reconcile  # noqa: E402
import ship  # noqa: E402
import source_sync  # noqa: E402
import pdf_tools  # noqa: E402
import settings_api  # noqa: E402


def _snapshot(url, name=None):
    """可投遞夾裡每個檔的(路徑, 大小, 修改時間, 雜湊)。"""
    d = ship.folder(url, name=name)
    if not d:
        return None
    entries = []
    for root, dirs, files in os.walk(d):
        dirs.sort()
        for n in sorted(files):
            path = os.path.join(root, n)
            with open(path, 'rb') as f:
                entries.append((os.path.relpath(path, d), os.path.getsize(path), os.stat(path).st_mtime_ns,
                                hashlib.file_digest(f, 'sha256').hexdigest()))
    return tuple(entries)




class NoResumeYet(unittest.TestCase):
    def test_no_resume_at_all_is_not_reported_once_per_card(self):
        # 一份履歷都還沒設定:設定頁「開始前」已經叫他上傳,不要每張卡各多一則「建置失敗」
        with mock.patch.object(cf, 'RESUMES', {}), mock.patch.object(agent_report, 'report') as rep:
            ship._sync_report('https://jobs.example/1', '沒有已勾選的履歷可供這張卡使用', 'b.html')
        rep.assert_not_called()
        with mock.patch.object(cf, 'RESUMES', {'r': {'id': 'r'}}), mock.patch.object(agent_report, 'report') as rep:
            ship._sync_report('https://jobs.example/1', '沒有已勾選的履歷可供這張卡使用', 'b.html')
        rep.assert_called_once()


class PackageReconcile(unittest.TestCase):
    url = 'test://jobs/12'
    job = {'id': url, 'target': 'Engineer · Acme'}

    def setUp(self):
        self.old_manifest = reconcile.MANIFEST
        _env.use_home(self, board={'file': 'board.html'}, resume={
            'base': 'resume', 'ship_dir': 'ship', 'langs': ['zh'],
            'resumes': [{'id': 'general', 'name': '通用版', 'files': {'zh': 'resume/base.pdf'},
                         'enabled': True, 'when': '', 'skill': ''}],
            'attachments': []})
        self.board = os.path.join(self.home, 'board.html')
        self.source = os.path.join(self.home, 'resume', 'base.pdf')
        os.makedirs(os.path.dirname(self.source), exist_ok=True)
        _env.tiny_pdf(self.source, 'resume-v1')
        self._write_board()
        self.manifest = os.path.join(self.home, 'reconcile-hashes.json')
        reconcile.MANIFEST = self.manifest

    def tearDown(self):
        reconcile.MANIFEST = self.old_manifest

    def _write_settings(self):
        """設定寫回檔案,這個行程也跟上。"""
        with open(os.path.join(self.home, cf.NAME), 'w', encoding='utf-8') as f:
            json.dump(self.settings, f, ensure_ascii=False)
        cf.reload(self.home)

    def _write_board(self):
        _env.make_board(self.board, {self.url: {'app': 'ready', 'resume_id': 'general', 'lang': 'zh'}}, jobs=[self.job])

    def _run(self):
        cf.reload(self.home)
        args = SimpleNamespace(board=self.board, force=False, check=False)
        with mock.patch.object(reconcile, 'stage_shell', return_value=False):
            return reconcile.run(args)

    def _board_data(self):
        return read_board(self.board)

    def _assert_blocked_with_one_report(self, reason):
        parsed = self._board_data()
        issues = parsed['data'].get('status', {}).get('issues', [])
        self.assertEqual(len(issues), 1)
        self.assertIn(reason, issues[0]['msg'])
        reports = agent_report.open_items(json.loads(parsed['fb']))
        self.assertEqual(len(reports), 1)

    def _assert_unblocked_and_report_resolved(self):
        parsed = self._board_data()
        self.assertEqual(parsed['data'].get('status', {}).get('issues', []), [])
        self.assertEqual(agent_report.open_items(json.loads(parsed['fb'])), [])

    def test_attachments_can_differ_by_language(self):
        """中文版和英文版的附件各用自己的語言檔。"""
        for n in ('zh-a.pdf', 'en-a.pdf', 'both.pdf', 'en.pdf'):
            _env.tiny_pdf(os.path.join(self.home, 'resume', n), n[:-4])
        self.settings['resume']['langs'] = ['zh', 'en']
        self.settings['resume']['resumes'][0]['files']['en'] = 'resume/en.pdf'
        self.settings['resume']['attachments'] = [
            {'id': 'zh-a', 'name': '中文版附件', 'files': {'zh': 'resume/zh-a.pdf'}, 'enabled': True},
            {'id': 'en-a', 'name': '英文版附件', 'files': {'en': 'resume/en-a.pdf'}, 'enabled': True},
            {'id': 'tech-only', 'name': '技術履歷附件', 'files': {'zh': 'resume/both.pdf'},
             'enabled': True, 'resume_ids': ['tech']},
            {'id': 'unchecked', 'name': '未勾選附件', 'files': {'zh': 'resume/both.pdf'},
             'enabled': False},
        ]
        self._write_settings()
        en_url = 'test://jobs/en'
        _env.make_board(self.board, {self.url: {'app': 'ready', 'resume_id': 'general', 'lang': 'zh'},
                                     en_url: {'app': 'ready', 'resume_id': 'general', 'lang': 'en'}},
                        jobs=[self.job, {'id': en_url, 'target': 'Engineer · Beta'}])
        self.assertEqual(self._run(), 0)
        self.assertEqual(sorted(ship.info(self.url)['files']), ['base.pdf', 'zh-a.pdf'])
        self.assertEqual(sorted(ship.info(en_url)['files']), ['en-a.pdf', 'en.pdf'])

        self.settings['resume']['attachments'] = [
            {'id': 'both', 'name': '雙語附件', 'files': {'zh': 'resume/both.pdf', 'en': 'resume/both.pdf'}, 'enabled': True}]
        self._write_settings()
        self.assertEqual(self._run(), 0)
        self.assertEqual(sorted(ship.info(en_url)['files']), ['both.pdf', 'en.pdf'])

    def test_default_package_has_ordered_merged_pdf_and_rebuilds_on_input_changes(self):
        attachment = os.path.join(self.home, 'resume', 'letter.pdf')
        _env.tiny_pdf(attachment, 'letter-v1')
        self.settings['resume']['attachments'] = [
            {'id': 'letter', 'name': '求職信', 'files': {'zh': 'resume/letter.pdf'}, 'enabled': True}]
        self._write_settings()
        self.assertEqual(self._run(), 0)

        info = ship.info(self.url)
        self.assertEqual(info['files'], ['base.pdf', 'letter.pdf'])
        merged = os.path.join(ship.folder(self.url), info['merged'])
        self.assertTrue(pdf_tools.same_pages(merged, [self.source, attachment]))
        self.assertFalse(pdf_tools.same_pages(merged, [attachment, self.source]))
        with open(merged, 'rb') as f:
            old_hash = hashlib.sha256(f.read()).hexdigest()

        for path, text in ((self.source, 'resume-v2'), (attachment, 'letter-v2')):
            _env.tiny_pdf(path, text)
            self.assertEqual(self._run(), 0)
            info = ship.info(self.url)
            merged = os.path.join(ship.folder(self.url), info['merged'])
            with open(merged, 'rb') as f:
                new_hash = hashlib.sha256(f.read()).hexdigest()
            self.assertNotEqual(new_hash, old_hash)
            self.assertTrue(pdf_tools.same_pages(merged, [self.source, attachment]))

    def _set_mark(self, **kw):
        bd.set_fb(lambda fb: fb[self.url].update(kw), live=self.board)

    def _folder_files(self):
        d = ship.folder(self.url)
        out = {}
        for n in sorted(os.listdir(d)):
            p = os.path.join(d, n)
            if os.path.isfile(p):
                with open(p, 'rb') as f:
                    out[n] = hashlib.sha256(f.read()).hexdigest()
        return out

    def test_a_card_being_filled_is_rebuilt_only_after_the_round(self):
        """正在填(或送出)時 agent 正拿著可投遞夾上傳(#308):那張這一輪不重建,等它做完下一輪再建。"""
        man = {}
        ship.reconcile_packages(man, force=False, check_only=False, board=self.board)
        before = self._folder_files()
        self._set_mark(app='ship', ds='running', apply={'stage': 'fill', 'at': '2026-01-01T00:00:00'})
        _env.tiny_pdf(self.source, 'resume-v2')
        ship.reconcile_packages(man, force=False, check_only=False, board=self.board)
        self.assertEqual(self._folder_files(), before, '正在填的那一張,可投遞夾不能在 agent 手上被換掉')
        self._set_mark(ds='parked')
        ship.reconcile_packages(man, force=False, check_only=False, board=self.board)
        self.assertNotEqual(self._folder_files()['base.pdf'], before['base.pdf'], '做完了就照新的檔重建')

    def test_rebuilding_never_shows_a_half_built_folder(self):
        """重建以前先把整個資料夾刪空、再一份份複製:這之間去拿的人拿到的是缺檔的一份(#308)。現在另外建好整份再換上。"""
        attachment = os.path.join(self.home, 'resume', 'letter.pdf')
        _env.tiny_pdf(attachment, 'letter-v1')
        self.settings['resume']['attachments'] = [
            {'id': 'letter', 'name': '求職信', 'files': {'zh': 'resume/letter.pdf'}, 'enabled': True}]
        self._write_settings()
        ship.reconcile_packages({}, force=True, check_only=False, board=self.board)
        complete = sorted(self._folder_files())
        seen = []
        real = shutil.copy2

        def watch(src, dst, *a, **k):
            seen.append(sorted(self._folder_files()))
            return real(src, dst, *a, **k)
        _env.tiny_pdf(self.source, 'resume-v2')
        with mock.patch('ship.shutil.copy2', side_effect=watch):
            ship.reconcile_packages({}, force=True, check_only=False, board=self.board)
        self.assertTrue(seen)
        self.assertEqual([x for x in seen if x != complete], [], '重建途中,放在那裡的一直是完整的一份')
        self.assertEqual(sorted(self._folder_files()), complete)
        self.assertEqual([n for n in os.listdir(os.path.dirname(ship.folder(self.url))) if n.startswith('.')], [],
                         '建好就換上,不留暫存的資料夾')

    def test_reconcile_does_not_build_a_package_for_a_closed_card(self):
        self.job = {**self.job, 'dead': True}
        self._write_board()

        did, failed = ship.reconcile_packages({}, force=True, check_only=False, board=self.board)

        self.assertFalse(did)
        self.assertEqual(failed, [])
        self.assertIsNone(ship.folder(self.url))

    def test_card_put_back_from_techerr_builds_even_though_the_page_was_marked_dead(self):
        """「🔧 出錯了」按「放回原處」= 他說頁面沒壞(live_ok):dead 是程式當時的判斷,不再擋要寄的檔案;
        頁面真的活著時 board_status 會把 dead 清掉,還是 404 就照樣報在待處理。"""
        self.job = {**self.job, 'dead': True}
        self._write_board()
        parsed = read_board(self.board)
        marks = json.loads(parsed['fb'])
        marks[self.url]['live_ok'] = 1
        _env.make_board(self.board, marks, data=parsed['data'])

        did, failed = ship.reconcile_packages({}, force=True, check_only=False, board=self.board)

        self.assertTrue(did)
        self.assertEqual(failed, [])
        self.assertIsNotNone(ship.folder(self.url))

    def test_reconcile_does_not_build_a_package_for_a_removed_card(self):
        self._write_board()
        parsed = read_board(self.board)
        marks = json.loads(parsed['fb'])
        marks[self.url]['rm'] = True
        _env.make_board(self.board, marks, data=parsed['data'])

        did, failed = ship.reconcile_packages({}, force=True, check_only=False, board=self.board)

        self.assertFalse(did)
        self.assertEqual(failed, [])
        self.assertIsNone(ship.folder(self.url))

    def test_markdown_and_style_are_rendered_to_pdf_before_delivery(self):
        source = os.path.join(self.home, 'resume', 'source.md')
        style = os.path.join(self.home, 'resume', 'style.css')
        os.makedirs(os.path.dirname(source), exist_ok=True)
        pathlib.Path(source).write_text('# Delivery resume\n\n| field | value |\n| --- | --- |\n| role | markdown marker |\n', encoding='utf-8')
        pathlib.Path(style).write_text('@page { size: A4; margin: 0; }', encoding='utf-8')
        attachment = os.path.join(self.home, 'resume', 'letter.md')
        pathlib.Path(attachment).write_text('# Cover letter\n\nattachment markdown marker', encoding='utf-8')
        self.settings['resume']['resumes'][0]['files']['zh'] = 'resume/source.md'
        self.settings['resume']['resumes'][0]['styles'] = {'zh': 'resume/style.css'}
        self.settings['resume']['attachments'] = [
            {'id': 'letter', 'name': 'Cover letter', 'files': {'zh': 'resume/letter.md'}, 'enabled': True}]
        self._write_settings()

        with mock.patch.object(ship.pdf_preview, 'generate', return_value='a' * 64):
            self.assertEqual(self._run(), 0)
            package = ship.folder(self.url, name=card.name(self.job))
            info = ship.read_info(package)
            self.assertEqual(len(info['files']), 2)
            self.assertTrue(all(name.endswith('.pdf') for name in info['files']))
            output = os.path.join(package, info['sources']['resume:general:zh'])
            attachment_output = os.path.join(package, info['sources']['attachment:letter'])
            with open(output, 'rb') as f:
                self.assertEqual(f.read(5), b'%PDF-')
            self.assertIn('markdown marker', settings_api.pdf_text(output))
            self.assertIn('attachment markdown marker', settings_api.pdf_text(attachment_output))
            reader = pdf_tools.pypdf.PdfReader(output)
            size = (round(float(reader.pages[0].mediabox.width)),
                    round(float(reader.pages[0].mediabox.height)))
            self.assertEqual(size, (595, 842))
            attachment_reader = pdf_tools.pypdf.PdfReader(attachment_output)
            attachment_size = (round(float(attachment_reader.pages[0].mediabox.width)),
                               round(float(attachment_reader.pages[0].mediabox.height)))
            self.assertNotEqual(attachment_size, (595, 842))
            rendered = os.path.join(self.home, '.rendered')
            generated = [os.path.join(root, name) for root, _dirs, names in os.walk(rendered)
                         for name in names if name.endswith('.pdf')]
            self.assertEqual(len(generated), 2)
            generated_resume = next(path for path in generated if os.path.basename(path) == 'source.pdf')
            generated_mtime = os.stat(generated_resume).st_mtime_ns

            self.assertEqual(self._run(), 0)
            self.assertEqual(os.stat(generated_resume).st_mtime_ns, generated_mtime)

            pathlib.Path(style).write_text('@page { size: A5; margin: 0; }', encoding='utf-8')
            self.assertEqual(self._run(), 0)
            reader = pdf_tools.pypdf.PdfReader(generated_resume)
            changed_size = (round(float(reader.pages[0].mediabox.width)),
                            round(float(reader.pages[0].mediabox.height)))
            self.assertEqual(changed_size, (420, 595))

    def test_markdown_preview_cache_tracks_source_and_style_changes(self):
        source = os.path.join(self.home, 'resume', 'preview.md')
        style = os.path.join(self.home, 'resume', 'preview.css')
        os.makedirs(os.path.dirname(source), exist_ok=True)
        pathlib.Path(source).write_text('# Preview v1\n\nRendered preview marker.\n', encoding='utf-8')
        pathlib.Path(style).write_text('@page { size: A4; margin: 0; }', encoding='utf-8')
        self.settings['resume']['resumes'][0]['files']['zh'] = 'resume/preview.md'
        self.settings['resume']['resumes'][0]['styles'] = {'zh': 'resume/preview.css'}
        self._write_settings()
        entry = next(source_sync.files())
        manifest = {}
        diagnostics = []

        def generate_preview(path):
            with open(path, 'rb') as source_file:
                key = hashlib.sha256(source_file.read()).hexdigest()
            preview = ship.pdf_preview.cache_path(key)
            os.makedirs(os.path.dirname(preview), exist_ok=True)
            pathlib.Path(preview).write_bytes(b'synthetic preview')
            return key

        with mock.patch.object(source_sync.pdf_preview, 'generate', side_effect=generate_preview) as generate:
            changed, errors = source_sync.refresh(manifest, diagnostics=diagnostics)
            self.assertEqual((len(changed), errors, generate.call_count), (1, [], 1), diagnostics)
            first_preview = manifest[source_sync.preview_key(entry)]
            self.assertEqual(source_sync.refresh(manifest), ([], []))
            self.assertEqual(generate.call_count, 1)

            pathlib.Path(style).write_text('@page { size: A5; margin: 0; }', encoding='utf-8')
            diagnostics.clear()
            changed, errors = source_sync.refresh(manifest, diagnostics=diagnostics)
            self.assertEqual((len(changed), errors, generate.call_count), (1, [], 2), diagnostics)
            style_preview = manifest[source_sync.preview_key(entry)]
            self.assertNotEqual(style_preview, first_preview)

            pathlib.Path(source).write_text('# Preview v2\n\nRendered preview marker.\n', encoding='utf-8')
            diagnostics.clear()
            changed, errors = source_sync.refresh(manifest, diagnostics=diagnostics)
            self.assertEqual((len(changed), errors, generate.call_count), (1, [], 3), diagnostics)
            self.assertNotEqual(manifest[source_sync.preview_key(entry)], style_preview)

    def test_merged_pdf_uses_the_accepted_custom_resume_first(self):
        custom = os.path.join(self.home, 'custom', 'accepted.pdf')
        attachment = os.path.join(self.home, 'resume', 'letter.pdf')
        _env.tiny_pdf(custom, 'accepted-custom-resume')
        _env.tiny_pdf(attachment, 'letter')
        self.settings['resume']['attachments'] = [
            {'id': 'letter', 'name': '求職信', 'files': {'zh': 'resume/letter.pdf'}, 'enabled': True}]
        self._write_settings()

        directory, problems = ship.build_default(self.job, {self.url: {
            'resume_id': 'general', 'lang': 'zh', 'custom_file': 'custom/accepted.pdf'}})

        self.assertEqual(problems, [])
        info = ship.info(self.url)
        self.assertTrue(pdf_tools.same_pages(os.path.join(directory, info['merged']), [custom, attachment]))

    def test_unchecked_card_resume_is_not_replaced_by_agent_pick(self):
        _env.tiny_pdf(os.path.join(self.home, 'resume', 'paused.pdf'), 'paused')
        self.settings['resume']['resumes'] = [
            {'id': 'general', 'name': '通用版', 'files': {'zh': 'resume/base.pdf'}, 'enabled': True},
            {'id': 'paused', 'name': '暫停版', 'files': {'zh': 'resume/paused.pdf'}, 'enabled': False},
        ]
        self._write_settings()
        job = dict(self.job, resume={'recommend': 'general', 'lang': 'zh'})
        _env.make_board(self.board, {self.url: {'app': 'ready', 'variant': 'paused', 'lang': 'zh'}}, jobs=[job])
        self.assertNotEqual(self._run(), 0)
        self._assert_blocked_with_one_report('沒有已勾選的履歷')

    def test_card_acceptance_issues_are_not_a_rebuild_failure(self):
        import board_status
        real_run = reconcile.subprocess.run

        def run(argv, *args, **kwargs):
            if any(str(x).endswith('board_status.py') for x in argv):
                return SimpleNamespace(returncode=board_status.ISSUES_FOUND)
            return real_run(argv, *args, **kwargs)

        with mock.patch.object(reconcile.subprocess, 'run', side_effect=run):
            self.assertEqual(self._run(), 0)

    def _two_ready_cards(self, waiting):
        other = 'test://jobs/99'
        fb = {self.url: dict({'app': 'ready', 'resume_id': 'general', 'lang': 'zh'}, **waiting),
              other: {'app': 'ready', 'resume_id': 'general', 'lang': 'zh'}}
        _env.make_board(self.board, fb, jobs=[self.job, {'id': other, 'target': 'Other · Beta'}])
        return other

    def test_one_card_waiting_on_customization_is_not_a_failed_round(self):
        # 一張卡的客製檔在等你看是刻意的等待,不是建置壞了。以前算成「可投遞夾本輪建置失敗」、整輪結束碼 1,
        # 伺服器的建置輪數不前進,所有卡的「驗收過就自動進可以投了」都停住,準備區每輪也報重建失敗。
        other = self._two_ready_cards({'custom_docs': {'resume:general:zh': {'status': 'review', 'name': '通用版'}}})
        self.assertEqual(self._run(), 0)
        parsed = self._board_data()
        self.assertEqual(agent_report.open_items(json.loads(parsed['fb'])), [])
        issues = parsed['data']['status']['issues']
        self.assertEqual([x['jid'] for x in issues], [self.url])      # 等你看的那張照樣擋著,原因寫客製
        self.assertIn('等你看', issues[0]['msg'])
        self.assertTrue(ship._build_states()[other]['ok'])

    def test_one_card_build_failure_still_finishes_the_round_for_the_others(self):
        # 真的建不成(這張選的語言沒有檔)照舊回報、照舊非 0(dda90c2:可投遞夾失敗不算成功);
        # 但要跟「程式本身壞了」分開,伺服器才知道這一輪跑完了、其他卡的驗收結果是新的
        other = self._two_ready_cards({'lang': 'en'})
        self.settings['resume']['langs'] = ['zh', 'en']
        self._write_settings()
        self.assertEqual(self._run(), reconcile.PACKAGE_PROBLEMS)
        parsed = self._board_data()
        self.assertEqual([x['jid'] for x in parsed['data']['status']['issues']], [self.url])
        self.assertEqual(len(agent_report.open_items(json.loads(parsed['fb']))), 1)
        self.assertTrue(ship._build_states()[other]['ok'])

    def test_default_builder_failure_blocks_stale_package_until_rebuilt(self):
        self.assertEqual(self._run(), 0)
        package = ship.folder(self.url, name=card.name(self.job))
        self.assertTrue(package)
        self.assertEqual(len(card.card_id_from_url(self.url)), 12)

        os.remove(self.source)
        self.assertNotEqual(self._run(), 0)
        self._assert_blocked_with_one_report('沒有 zh 的檔')
        self.assertNotEqual(self._run(), 0)
        self._assert_blocked_with_one_report('沒有 zh 的檔')

        _env.tiny_pdf(self.source, 'resume-v2')
        self.assertEqual(self._run(), 0)
        self._assert_unblocked_and_report_resolved()

    def test_external_custom_card_file_changes_are_tracked(self):
        source = os.path.join(self.tmp, 'outside.md')
        custom = os.path.join(self.tmp, 'card-source.md')
        for path in (source, custom):
            pathlib.Path(path).write_text(path, encoding='utf-8')
        self.settings['resume']['resumes'][0]['files']['zh'] = source
        self._write_settings()
        _env.make_board(self.board, {self.url: {'app': 'ready', 'resume_id': 'general', 'lang': 'zh', 'custom_file': custom}},
                        jobs=[self.job])
        manifest = {}
        source_sync.refresh(manifest, board=self.board)
        self.assertFalse(source_sync.stale(manifest=manifest, board=self.board))
        pathlib.Path(custom).write_text('changed', encoding='utf-8')
        self.assertTrue(source_sync.stale(manifest=manifest, board=self.board))

    def test_markdown_input_change_rerenders_and_rebuilds_package(self):
        source = os.path.join(self.home, 'resume', 'source.md')
        pathlib.Path(source).write_text('# Original v1\n\nA text marker.\n', encoding='utf-8')
        self.settings['resume']['resumes'][0]['files']['zh'] = source
        self._write_settings()
        with mock.patch.object(ship.pdf_preview, 'generate', return_value='a' * 64):
            self.assertEqual(self._run(), 0)
            package = ship.folder(self.url, name=card.name(self.job))
            info = ship.read_info(package)
            output = os.path.join(package, info['files'][0])
            with open(output, 'rb') as f:
                v1 = hashlib.sha256(f.read()).hexdigest()
            self.assertIn('Original v1', settings_api.pdf_text(output))
            self.assertEqual(self._run(), 0)
            rendered = source_sync.effective_path(next(source_sync.files()))
            mtime = os.stat(rendered).st_mtime_ns
            self.assertEqual(self._run(), 0)
            self.assertEqual(os.stat(rendered).st_mtime_ns, mtime)
            pathlib.Path(source).write_text('# Original v2\n\nA text marker.\n', encoding='utf-8')
            self.assertEqual(self._run(), 0)
            info = ship.read_info(package)
            with open(os.path.join(package, info['files'][0]), 'rb') as f:
                v2 = hashlib.sha256(f.read()).hexdigest()
            self.assertNotEqual(v1, v2)
            self.assertIn('Original v2', settings_api.pdf_text(os.path.join(package, info['files'][0])))

    def test_source_file_that_cannot_be_rendered_is_reported_and_cleared_when_fixed(self):
        # Markdown 排不成 PDF(找不到 Chrome、原稿壞了):以前只印在 reconcile 的輸出裡,背景跑的看不到,
        # 代投前的來源檢查叫他去看的「整理要寄的檔案」回報也從來沒人寫;準備和填表就這樣靜靜停著
        source = os.path.join(self.home, 'resume', 'source.md')
        pathlib.Path(source).write_text('# Resume\n', encoding='utf-8')
        self.settings['resume']['resumes'][0]['files']['zh'] = source
        self._write_settings()

        def mine(fb):
            return [x for x in agent_report.open_items(fb) if x.get('from') == '整理要寄的檔案']
        broken = mock.patch.object(source_sync.markdown_pdf, 'render',
                                   side_effect=source_sync.markdown_pdf.RenderError('找不到可以排版的 Chrome'))
        with broken, mock.patch.object(ship.pdf_preview, 'generate', return_value=None):
            self.assertNotEqual(self._run(), 0)
            self.assertNotEqual(self._run(), 0)
        reports = mine(json.loads(self._board_data()['fb']))
        self.assertEqual(len(reports), 1)                       # 同一件事只加次數,不洗版
        self.assertIn('找不到可以排版的 Chrome', reports[0]['msg'])
        self.assertEqual(reports[0]['n'], 2)
        self.assertIn('準備履歷和填表', reports[0]['need'])

        rendered = lambda path, output, *a, **k: _env.tiny_pdf(output, 'rendered')
        with mock.patch.object(source_sync.markdown_pdf, 'render', side_effect=rendered), \
                mock.patch.object(ship.pdf_preview, 'generate', return_value=None):
            self.assertEqual(self._run(), 0)
        self.assertEqual(mine(json.loads(self._board_data()['fb'])), [])

    def test_external_sources_are_copied_and_attachment_change_only_rebuilds_its_packages(self):
        root = os.path.join(self.tmp, 'outside')
        os.makedirs(root)
        external_resume = os.path.join(root, 'general.pdf')
        external_attachment = os.path.join(root, 'writeup.pdf')
        alternate_resume = os.path.join(root, 'alternate.pdf')
        for path, body in ((external_resume, 'general-v1'),
                           (external_attachment, 'writeup-v1'),
                           (alternate_resume, 'alternate-v1')):
            _env.tiny_pdf(path, body)
        self.settings['resume']['resumes'] = [
            {'id': 'general', 'name': '通用版', 'files': {'zh': external_resume},
             'enabled': True, 'when': '', 'skill': ''},
            {'id': 'alternate', 'name': '另一版', 'files': {'zh': alternate_resume},
             'enabled': True, 'when': '', 'skill': ''},
        ]
        self.settings['resume']['attachments'] = [
            {'id': 'writeup', 'name': '作品集', 'files': {'zh': external_attachment},
             'enabled': True, 'resume_ids': ['general'], 'skill': ''}]
        self._write_settings()
        other_url = 'test://jobs/other'
        other = {'id': other_url, 'target': 'Engineer · Other'}
        _env.make_board(self.board, {self.url: {'app': 'ready', 'resume_id': 'general', 'lang': 'zh'},
                                     other_url: {'app': 'ready', 'resume_id': 'alternate', 'lang': 'zh'}},
                        jobs=[self.job, other])
        self.assertEqual(self._run(), 0)
        first_dir = ship.folder(self.url, name=card.name(self.job))
        ship.folder(other_url, name=card.name(other))
        with open(os.path.join(first_dir, 'writeup.pdf'), 'rb') as f:
            copied_writeup = f.read()
        with open(external_attachment, 'rb') as f:
            self.assertEqual(copied_writeup, f.read())
        other_before = _snapshot(other_url, name=card.name(other))

        _env.tiny_pdf(external_attachment, 'writeup-v2')
        self.assertEqual(self._run(), 0)
        with open(os.path.join(first_dir, 'writeup.pdf'), 'rb') as f:
            copied_writeup = f.read()
        with open(external_attachment, 'rb') as f:
            self.assertEqual(copied_writeup, f.read())
        self.assertEqual(_snapshot(other_url, name=card.name(other)), other_before)

    def test_source_sync_diagnostic_reports_error_type_and_tool_frame_only(self):
        source = os.path.join(self.home, 'resume', 'source.md')
        os.makedirs(os.path.dirname(source), exist_ok=True)
        pathlib.Path(source).write_text('# Resume', encoding='utf-8')
        self.settings['resume']['resumes'][0]['files']['zh'] = 'resume/source.md'
        self._write_settings()
        diagnostics = []

        with mock.patch.object(source_sync.markdown_pdf, 'render',
                               side_effect=source_sync.markdown_pdf.RenderError(
                                   'private resume title at /private/path')):
            changed, errors = source_sync.refresh({}, diagnostics=diagnostics)

        self.assertEqual(len(changed), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn('Markdown render RenderError', diagnostics[0])
        self.assertIn('source_sync.py:', diagnostics[0])
        self.assertNotIn('private resume title', diagnostics[0])
        self.assertNotIn('/private/path', diagnostics[0])

    def test_source_sync_diagnostic_includes_chained_error_types_without_messages(self):
        try:
            try:
                raise OSError('private resume title at /private/path')
            except OSError as cause:
                raise source_sync.markdown_pdf.RenderError(
                    'Markdown failed for private resume title') from cause
        except source_sync.markdown_pdf.RenderError as error:
            diagnostic = source_sync._diagnostic('Markdown render', error)

        self.assertIn('RenderError', diagnostic)
        self.assertIn('OSError', diagnostic)
        self.assertNotIn('private resume title', diagnostic)
        self.assertNotIn('/private/path', diagnostic)

    def test_file_hash_manifest_detects_external_markdown_changes(self):
        source = os.path.join(self.tmp, 'outside.md')
        pathlib.Path(source).write_text('v1', encoding='utf-8')
        self.settings['resume']['resumes'][0]['files']['zh'] = source
        self._write_settings()
        manifest = {}
        diagnostics = []
        changed, errors = source_sync.refresh(manifest, diagnostics=diagnostics)
        self.assertEqual(len(changed), 1)
        self.assertEqual(errors, [], diagnostics)
        self.assertFalse(source_sync.stale(manifest=manifest))
        pathlib.Path(source).write_text('v2', encoding='utf-8')
        self.assertTrue(source_sync.stale(manifest=manifest))

    def test_symlink_source_fingerprint_follows_target_content(self):
        target = os.path.join(self.tmp, 'outside-original.md')
        pathlib.Path(target).write_text('# version one', encoding='utf-8')
        link = os.path.join(self.home, 'resume', 'external-link.md')
        os.symlink(target, link)
        self.settings['resume']['resumes'][0]['files']['zh'] = 'resume/external-link.md'
        self._write_settings()
        entry = next(source_sync.files())
        first = source_sync.entry_fingerprint(entry)
        self.assertTrue(os.path.islink(entry['path']))
        pathlib.Path(target).write_text('# version two', encoding='utf-8')
        self.assertNotEqual(source_sync.entry_fingerprint(entry), first)

    def test_unchanged_unrenderable_pdf_does_not_request_reconcile_repeatedly(self):
        source = os.path.join(self.tmp, 'outside.pdf')
        pathlib.Path(source).write_bytes(b'not a PDF')
        self.settings['resume']['resumes'][0]['files']['zh'] = source
        self._write_settings()
        manifest = {}
        _changed, errors = source_sync.refresh(manifest)
        self.assertEqual(errors, [])
        self.assertFalse(source_sync.stale(manifest=manifest))
        pathlib.Path(source).write_bytes(b'changed')
        self.assertTrue(source_sync.stale(manifest=manifest))

    def test_legacy_folder_is_known_and_migrates_to_twelve_character_name(self):
        root = cf.SHIP_DIR
        old = os.path.join(root, f'legacy-{card.legacy_id(self.url)}')
        os.makedirs(old)
        cleaned, unknown = ship.clean_orphans(True, self.board)
        self.assertEqual(cleaned, [])
        self.assertEqual(unknown, [])
        self.assertTrue(os.path.isdir(old))

        migrated = ship.folder(self.url, name=card.name(self.job))
        self.assertEqual(os.path.basename(migrated), f'Engineer_·_Acme-{card.card_id_from_url(self.url)}')
        self.assertTrue(os.path.isdir(migrated))
        self.assertFalse(os.path.exists(old))

    def test_a_build_that_crashed_halfway_leaves_nothing_unknown(self):
        # 重建是另外建好再換上(#308):建到一半當掉留下的暫存夾是這裡建的,下一輪清掉,不報「來歷不明」
        left = os.path.join(cf.SHIP_DIR, '.building-abc123')
        os.makedirs(left)
        cleaned, unknown = ship.clean_orphans(False, self.board)
        self.assertEqual(unknown, [])
        self.assertFalse(os.path.exists(left))

    def test_card_leaving_the_flow_keeps_the_fill_screenshots(self):
        # 可以投了、填過的卡按 😐/👎 退出流程:可重生的要寄的檔案清掉,代投留下的 .apply(填表截圖、交件紀錄)留著,
        # 按復原回到流程後「填表時的截圖」還在。以前整個夾子連 .apply 一起刪
        self.assertEqual(self._run(), 0)
        package = ship.folder(self.url, name=card.name(self.job))
        shot = os.path.join(package, '.apply', 'fill.png')
        os.makedirs(os.path.dirname(shot))
        pathlib.Path(shot).write_bytes(b'png')
        bd.set_fb(lambda fb: fb[self.url].pop('app'), live=self.board, by='test')

        cleaned, _unknown = ship.clean_orphans(False, self.board)
        self.assertEqual(cleaned, [os.path.basename(package)])
        self.assertTrue(os.path.isfile(shot))
        self.assertEqual(os.listdir(package), ['.apply'])
        self.assertEqual(ship.clean_orphans(False, self.board)[0], [])   # 只剩 .apply:不再每輪報「已清」

        bd.set_fb(lambda fb: fb[self.url].update(app='ready'), live=self.board, by='test')
        self.assertEqual(self._run(), 0)
        self.assertTrue(os.path.isfile(shot))
        self.assertTrue(ship.info(self.url).get('files'))

    def test_gate_before_sending_catches_a_folder_changed_after_it_was_built(self):
        # 投遞前把關:建好之後夾子被動過(檔案不見、合併版不見或寫錯、語言不對),每一種都擋下、講是哪裡
        def edit_info(change):
            path = os.path.join(ship.folder(self.url), 'ship.json')
            with open(path, encoding='utf-8') as f:
                info = json.load(f)
            change(info)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(info, f)

        def remove(name):
            os.remove(os.path.join(ship.folder(self.url), name))
        cases = [
            ('個別檔不見', lambda: remove('base.pdf'), 'base.pdf 不見了'),
            ('合併版不見', lambda: remove(ship.info(self.url)['merged']), '不見了'),
            ('沒有合併版', lambda: edit_info(lambda i: i.pop('merged')), '沒有合併版'),
            ('合併版列成個別檔', lambda: edit_info(lambda i: i.__setitem__('merged', i['files'][0])), '不能列為個別上傳檔'),
            ('語言對不上', lambda: edit_info(lambda i: i.__setitem__('lang', 'en')), '可投遞夾裡是'),
            ('沒有檔案清單', lambda: edit_info(lambda i: i.__setitem__('files', [])), '沒有 ship.json'),
        ]
        for why, break_it, want in cases:
            with self.subTest(why):
                shutil.rmtree(cf.SHIP_DIR, ignore_errors=True)
                self.assertEqual(self._run(), 0)
                fb = json.loads(read_board(self.board)['fb'])
                self.assertEqual(ship.check(self.job, fb), [])
                break_it()
                got = ship.check(self.job, fb)
                self.assertTrue(any(want in p for p in got), got)
        shutil.rmtree(cf.SHIP_DIR, ignore_errors=True)
        self.assertEqual(ship.check(self.job, fb, migrate=False), ['可投遞夾還沒建'])

    def test_rebuilding_where_folders_cannot_be_swapped_keeps_the_fill_screenshots(self):
        # 不是 macOS(沒有一次對調兩個資料夾):改成三次改名;代投留下的 .apply 照樣搬到新的那一份
        self.assertEqual(self._run(), 0)
        shot = os.path.join(ship.folder(self.url), '.apply', 'fill.png')
        os.makedirs(os.path.dirname(shot))
        pathlib.Path(shot).write_bytes(b'png')
        _env.tiny_pdf(self.source, 'resume-v2')
        with mock.patch.object(ship.sys, 'platform', 'linux'):
            ship.reconcile_packages({}, force=True, check_only=False, board=self.board)
        package = ship.folder(self.url)
        self.assertTrue(os.path.isfile(os.path.join(package, '.apply', 'fill.png')))
        self.assertEqual([n for n in os.listdir(os.path.dirname(package)) if n.startswith('.')], [])
        with open(os.path.join(package, 'base.pdf'), 'rb') as f:
            self.assertIn(b'resume-v2', f.read())

    def test_a_missing_attachment_is_named_and_nothing_is_merged(self):
        self.settings['resume']['attachments'] = [
            {'id': 'letter', 'name': '求職信', 'files': {'zh': 'resume/letter.pdf'}, 'enabled': True}]
        self._write_settings()
        _folder, problems = ship.build_default(self.job, json.loads(read_board(self.board)['fb']))
        self.assertTrue(any('letter.pdf' in p for p in problems), problems)
        self.assertNotIn('merged', ship.info(self.url))

    def test_a_source_file_named_like_the_merged_pdf_does_not_overwrite_it(self):
        clash = os.path.join(self.home, 'resume', ship.MERGED_FILE)
        _env.tiny_pdf(clash, 'my-own-file')
        self.settings['resume']['resumes'][0]['files']['zh'] = 'resume/' + ship.MERGED_FILE
        self._write_settings()
        _folder, problems = ship.build_default(self.job, json.loads(read_board(self.board)['fb']))
        self.assertEqual(problems, [])
        info = ship.info(self.url)
        self.assertNotIn(ship.MERGED_FILE, info['files'])
        self.assertNotEqual(info['merged'], info['files'][0])

    def test_a_card_leaving_the_flow_loses_its_rebuildable_folder_but_check_only_just_reports(self):
        self.assertEqual(self._run(), 0)
        package = ship.folder(self.url, name=card.name(self.job))
        bd.set_fb(lambda fb: fb[self.url].pop('app'), live=self.board, by='test')
        cleaned, _unknown = ship.clean_orphans(True, self.board)          # 只檢查:照報,不刪
        self.assertEqual(cleaned, [os.path.basename(package)])
        self.assertTrue(os.path.isdir(package))
        cleaned, _unknown = ship.clean_orphans(False, self.board)
        self.assertEqual(cleaned, [os.path.basename(package)])
        self.assertFalse(os.path.exists(package))                         # 沒有 .apply:整個可重生,清掉

    def test_folders_nobody_can_account_for_are_reported_not_deleted(self):
        root = cf.SHIP_DIR
        os.makedirs(root, exist_ok=True)
        mystery = os.path.join(root, 'mystery-zzzzzzzzzzzz')
        os.makedirs(mystery)
        pathlib.Path(os.path.join(root, 'notes.txt')).write_text('他自己放的', encoding='utf-8')
        outside = os.path.join(self.tmp, 'outside')
        os.makedirs(outside)
        link = os.path.join(root, f'link-{card.card_id_from_url(self.url)}')
        os.symlink(outside, link)
        # 這張卡不在流程裡(沒有 app):夾子本來會被清,但它是指到外面的連結,不碰
        bd.set_fb(lambda fb: fb[self.url].pop('app'), live=self.board, by='test')
        bd.set_data(lambda data, fb: data['jobs'].append({'target': '沒有網址的卡'}), live=self.board)
        cleaned, unknown = ship.clean_orphans(False, self.board)
        self.assertEqual(unknown, ['mystery-zzzzzzzzzzzz'])
        self.assertEqual(cleaned, [])
        self.assertTrue(os.path.isdir(mystery))
        self.assertTrue(os.path.isdir(outside))
        self.assertTrue(os.path.isfile(os.path.join(root, 'notes.txt')))

    def test_renaming_a_card_renames_its_folder(self):
        self.assertIsNone(ship.rename(self.url, 'Engineer · Nowhere'))     # 還沒有夾子:沒得改
        self.assertEqual(self._run(), 0)
        before = ship.folder(self.url)
        self.assertEqual(ship.rename(self.url, self.job['target']), before)   # 同名:不動
        after = ship.rename(self.url, 'Staff Engineer · Acme')
        self.assertNotEqual(after, before)
        self.assertTrue(os.path.isdir(after))
        self.assertFalse(os.path.exists(before))
        self.assertEqual(ship.folder(self.url), after)

    def test_two_folders_for_one_card_are_not_guessed(self):
        root = cf.SHIP_DIR
        cid = card.card_id_from_url(self.url)
        for name in (f'a-{cid}', f'b-{cid}'):
            os.makedirs(os.path.join(root, name))
        self.assertIsNone(ship.folder(self.url, name='c'))
        self.assertEqual(sorted(os.listdir(root)), [f'a-{cid}', f'b-{cid}'])

    def test_legacy_folder_is_not_moved_onto_an_existing_one(self):
        root = cf.SHIP_DIR
        old = os.path.join(root, f'legacy-{card.legacy_id(self.url)}')
        os.makedirs(old)
        new = os.path.join(root, f'legacy-{card.card_id_from_url(self.url)}')
        pathlib.Path(new).write_text('不是資料夾', encoding='utf-8')
        self.assertIsNone(ship.folder(self.url))
        self.assertTrue(os.path.isdir(old))


class ShipFiles(unittest.TestCase):
    """要寄的檔案只在後台算(ship.card_files):一張卡用哪份履歷、哪個語言、附哪幾份、每份寄客製版還是原始檔,
    還沒辦法決定時的原因。看板載入和按了之後都拿這一份,不自己挑。"""
    url = 'test://jobs/files'

    def setUp(self):
        _env.use_home(self, board={'file': 'board.html'}, resume={
            'base': 'resume', 'ship_dir': 'ship', 'langs': ['zh', 'en'],
            'resumes': [
                {'id': 'general', 'name': '通用版', 'files': {'zh': 'resume/general-zh.pdf', 'en': 'resume/general-en.pdf'},
                 'enabled': True},
                {'id': 'tech', 'name': '技術版', 'files': {'zh': 'resume/tech-zh.pdf'}, 'enabled': True},
                {'id': 'paused', 'name': '暫停版', 'files': {'zh': 'resume/paused-zh.pdf'}, 'enabled': False}],
            'attachments': [
                {'id': 'letter', 'name': '求職信', 'files': {'zh': 'resume/letter-zh.pdf', 'en': 'resume/letter-en.pdf'},
                 'enabled': True},
                {'id': 'essay', 'name': '英文短文', 'files': {'en': 'resume/essay-en.pdf'}, 'enabled': True}]})
        for name in ('general-zh', 'general-en', 'tech-zh', 'paused-zh', 'letter-zh', 'letter-en', 'essay-en'):
            _env.tiny_pdf(os.path.join(self.home, 'resume', name + '.pdf'), name)

    def job(self, **resume):
        return {'id': self.url, 'target': 'Engineer · Acme', 'resume': resume}

    def files_of(self, job, mark):
        return ship.card_files(job, {self.url: mark})

    def test_pinned_resume_that_was_unchecked_stops_instead_of_switching(self):
        # 你指定的履歷後來被取消勾選:停下來要你重選,不偷換成 agent 推薦的那份,不列檔案
        got = self.files_of(self.job(recommend='general', lang='zh'), {'app': 'ready', 'resume_id': 'paused'})
        self.assertEqual(got['resume_id'], '')
        self.assertTrue(got['problem'])
        self.assertEqual([c['id'] for c in got['choices']], ['general', 'tech'])
        self.assertEqual(got['files'], [])
        self.assertEqual(ship.resolve(self.job(recommend='general', lang='zh'),
                                      {self.url: {'resume_id': 'paused'}})[0], '')

    def test_pinned_resume_unchecked_without_a_recommendation_also_stops(self):
        # 沒推薦也一樣停下來要你重選;以前看板在這裡自己挑第一份勾選的,後台建不出來
        got = self.files_of(self.job(), {'app': 'ready', 'variant': 'paused', 'lang': 'zh'})
        self.assertEqual((got['resume_id'], got['files']), ('', []))
        self.assertTrue(got['problem'])

    def test_no_pin_uses_the_recommendation(self):
        got = self.files_of(self.job(recommend='tech', lang='zh'), {'app': 'ready'})
        self.assertEqual((got['resume_id'], got['lang'], got['problem']), ('tech', 'zh', ''))
        self.assertEqual([(f['kind'], f['name']) for f in got['files']], [('resume', '技術版'), ('attachment', '求職信')])

    def test_recommendation_that_was_unchecked_also_stops(self):
        # agent 挑的那份後來被取消勾選、你也沒指定:一樣停下來要你選,不偷換成別份
        got = self.files_of(self.job(recommend='paused', lang='zh'), {'app': 'ready'})
        self.assertEqual((got['resume_id'], got['files']), ('', []))
        self.assertIn('挑的履歷已經取消勾選', got['problem'])

    def test_neither_pinned_nor_recommended_is_not_picked_yet(self):
        # 沒指定也沒推薦:「還沒挑履歷」,不列附件(以前看板自己挑第一份勾選的)
        got = self.files_of(self.job(), {'app': 'ready'})
        self.assertEqual((got['resume_id'], got['problem'], got['files']), ('', '還沒挑履歷', []))

    def test_language_not_in_the_list_falls_back_to_the_default_and_says_so(self):
        got = self.files_of(self.job(recommend='general', lang='fr'), {'app': 'ready'})
        self.assertEqual((got['lang'], got['lang_from']), ('zh', 'fr'))
        got = self.files_of(self.job(recommend='general', lang='zh'), {'app': 'ready', 'lang': 'en'})
        self.assertEqual((got['lang'], got['lang_from']), ('en', ''))
        self.assertEqual([f['name'] for f in got['files']], ['通用版', '求職信', '英文短文'])

    def custom(self, name, text='custom'):
        _env.tiny_pdf(os.path.join(self.home, 'custom', name), text)
        return 'custom/' + name

    def sig(self, name):
        return ship.source_sig(os.path.join(self.home, 'resume', name))

    def test_custom_record_not_accepted_is_not_sent(self):
        # 客製版只有收下的才算:等你看的那份不寄、也不預覽,這張照舊擋著送出
        path = self.custom('review.pdf')
        mark = {'app': 'ready', 'resume_id': 'general', 'lang': 'zh', 'custom_docs': {
            'resume:general:zh': {'status': 'review', 'name': '通用版', 'candidate_path': path}}}
        got = self.files_of(self.job(), mark)
        resume = got['files'][0]
        self.assertEqual((resume['custom'], resume['path']), (False, ''))
        self.assertIn('通用版', got['pending'])          # 擋著:講是哪一份在等你看

    def test_old_own_file_and_custom_records_follow_one_priority(self):
        # 客製紀錄和舊的「這張用自己的檔」同一個優先順序:收下的紀錄 > 舊的這張自己的檔 > 原始檔。
        # 以前後台只要有一筆紀錄(不論狀態)就蓋掉舊的檔改寄原始檔,看板卻預覽舊的檔
        own = self.custom('own.pdf', 'own')
        accepted = self.custom('accepted.pdf', 'accepted')
        mark = {'app': 'ready', 'resume_id': 'general', 'lang': 'zh', 'custom_file': own, 'custom_docs': {
            'resume:general:zh': {'status': 'review', 'name': '通用版', 'candidate_path': accepted}}}
        resume = self.files_of(self.job(), mark)['files'][0]
        self.assertEqual((resume['custom'], resume['path']), (True, own))
        self.assertEqual(ship.sources(self.job(), {self.url: mark})[0], os.path.realpath(os.path.join(self.home, own)))
        mark['custom_docs']['resume:general:zh'] = {'status': 'accepted', 'name': '通用版', 'path': accepted,
                                                    'source_sig': self.sig('general-zh.pdf')}
        resume = self.files_of(self.job(), mark)['files'][0]
        self.assertEqual((resume['custom'], resume['path']), (True, accepted))

    def test_attachment_with_only_a_custom_version_is_not_listed(self):
        # 附件在這個語言沒有原始檔:就算有收下的客製版也不寄(以前看板照樣列出來,後台不寄)
        path = self.custom('essay-zh.pdf')
        mark = {'app': 'ready', 'resume_id': 'general', 'lang': 'zh', 'custom_docs': {
            'attachment:essay:zh': {'status': 'accepted', 'name': '英文短文', 'path': path}}}
        self.assertEqual([f['name'] for f in self.files_of(self.job(), mark)['files']], ['通用版', '求職信'])

    def test_accepted_custom_version_whose_original_changed_sends_the_original(self):
        # 收下之後在設定頁換了原始檔:這份客製版不寄、預覽也換回原始檔,卡上照實講;
        # 要寄的檔案紀錄(ship.json)也不能再寫「這張用自己的檔」
        path = self.custom('old.pdf')
        mark = {'app': 'ready', 'resume_id': 'general', 'lang': 'zh', 'custom_docs': {
            'resume:general:zh': {'status': 'accepted', 'name': '通用版', 'path': path, 'source_sig': '0' * 64}}}
        got = self.files_of(self.job(), mark)
        self.assertEqual((got['files'][0]['custom'], got['files'][0]['stale']), (False, True))
        self.assertEqual(got['stale_ids'], ['resume:general:zh'])
        fb = {self.url: mark}
        self.assertFalse(ship.sources(self.job(), fb)[2])
        directory, problems = ship.build_default(self.job(), fb)
        self.assertEqual(problems, [])
        self.assertFalse(ship.info(self.url)['custom'])
        self.assertEqual(ship.info(self.url)['files'][0], 'general-zh.pdf')

    def test_replacing_the_original_is_seen_on_the_next_ask_without_restarting(self):
        # 設定頁上傳新的原始檔(同一個伺服器行程、不重新整理):下一次問就改成寄原始檔,不用重開
        path = self.custom('fresh.pdf')
        mark = {'app': 'ready', 'resume_id': 'general', 'lang': 'zh', 'custom_docs': {
            'resume:general:zh': {'status': 'accepted', 'name': '通用版', 'path': path,
                                  'source_sig': self.sig('general-zh.pdf')}}}
        resume = self.files_of(self.job(), mark)['files'][0]
        self.assertEqual((resume['custom'], resume['stale']), (True, False))
        _env.tiny_pdf(os.path.join(self.home, 'resume', 'general-zh.pdf'), 'a-new-longer-original')
        got = self.files_of(self.job(), mark)
        self.assertEqual((got['files'][0]['custom'], got['files'][0]['stale'], got['stale_ids']),
                         (False, True, ['resume:general:zh']))

    def test_sent_card_shows_the_one_actually_sent(self):
        # 已投出:照記下的實際寄出的那一份,之後按了別的履歷/語言也不改寫;退回之後(不是已投出)不再照它
        mark = {'app': 'sent', 'sent_v': 'en-general', 'resume_id': 'tech', 'lang': 'zh'}
        got = self.files_of(self.job(), mark)
        self.assertEqual((got['resume_id'], got['lang'], got['sent']), ('general', 'en', True))
        mark['app'] = 'ship'
        self.assertEqual(self.files_of(self.job(), mark)['resume_id'], 'tech')
        # 沒挑過履歷的已投出卡:統計表算「不明」
        got = self.files_of(self.job(), {'app': 'sent'})
        self.assertEqual((got['resume_id'], got['problem']), ('', '還沒挑履歷'))

    def test_sent_version_is_recorded_in_one_place_from_what_was_built(self):
        # 標成已投出時記實際寄出的那一份:要寄的檔案(ship.json)是哪份就記哪份,沒有才照現在挑的;記過不改
        job = self.job(recommend='general', lang='zh')
        fb = {self.url: {'app': 'ship', 'resume_id': 'tech', 'lang': 'zh'}}
        ship.build_default(job, fb)
        fb[self.url].update(app='sent', resume_id='general', lang='en')
        ship.record_sent(fb, self.url, job)
        self.assertEqual(fb[self.url]['sent_v'], 'zh-tech')
        fb[self.url]['resume_id'] = 'general'
        ship.record_sent(fb, self.url, job, version='en-general')
        self.assertEqual(fb[self.url]['sent_v'], 'zh-tech')
        other = {'id': 'test://jobs/no-package', 'target': 'X · Y', 'resume': {'recommend': 'general', 'lang': 'en'}}
        fb[other['id']] = {'app': 'sent'}
        ship.record_sent(fb, other['id'], other)
        self.assertEqual(fb[other['id']]['sent_v'], 'en-general')
        fb['test://jobs/none'] = {'app': 'sent'}
        ship.record_sent(fb, 'test://jobs/none', {'id': 'test://jobs/none', 'target': 'X · Z'})
        self.assertNotIn('sent_v', fb['test://jobs/none'])


if __name__ == '__main__':
    unittest.main()


class MergedPdf(unittest.TestCase):
    """合併版 PDF(驗收工具 apply_accept 也直接叫這一支):缺檔、沒檔都照實說,不生半份;重建沿用上次的檔名。"""

    def setUp(self):
        self.d = self.enterContext(tempfile.TemporaryDirectory(prefix='merged-'))

    def test_missing_or_no_files_are_named(self):
        missing = ship._write_merged(self.d, {'files': ['a.pdf']})
        self.assertEqual(len(missing), 1)
        self.assertIn('a.pdf', missing[0])
        self.assertEqual(len(ship._write_merged(self.d, {'files': []})), 1)
        self.assertEqual([n for n in os.listdir(self.d) if n.endswith('.pdf')], [])

    def test_rebuild_keeps_the_previous_name_and_avoids_a_clash(self):
        _env.tiny_pdf(os.path.join(self.d, 'a.pdf'), 'a')
        info = {'files': ['a.pdf'], 'merged': '給他的合併版.pdf'}
        self.assertEqual(ship._write_merged(self.d, info), [])
        self.assertEqual(info['merged'], '給他的合併版.pdf')
        _env.tiny_pdf(os.path.join(self.d, ship.MERGED_FILE), 'clash')
        info = {'files': ['a.pdf', ship.MERGED_FILE]}
        self.assertEqual(ship._write_merged(self.d, info), [])
        self.assertNotEqual(info['merged'], ship.MERGED_FILE)
        self.assertTrue(os.path.isfile(os.path.join(self.d, info['merged'])))
