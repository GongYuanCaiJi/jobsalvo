import json
import os
import tempfile
import unittest
from unittest.mock import patch

import _env  # noqa: F401  (載入就設好測試環境)
import realdata_check


class TemporaryCopy(unittest.TestCase):
    def test_status_summary_counts_kinds_without_card_names(self):
        import board_doc as bd

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'board.html')
            status = {'schema_version': 2, 'issues': [
                {'jid': 'https://ex.test/1', 't': 'Secret Title', 'kind': 'closed', 'msg': 'x'},
                {'jid': 'https://ex.test/2', 't': 'Other Title', 'kind': 'unverified', 'soft': True, 'msg': 'x'},
                {'jid': 'https://ex.test/3', 't': 'Third Title', 'kind': 'files', 'msg': 'x'},
            ]}
            with open(path, 'w', encoding='utf-8') as target:
                target.write(bd.assemble('', '', '', {'jobs': [], 'status': status}, '{}', ''))
            line = realdata_check.status_summary(path)

        self.assertIn('已下架 1', line)
        self.assertIn('還沒確認 1', line)
        self.assertIn('檔案 1', line)
        self.assertNotIn('Secret', line)

    def test_status_summary_does_not_call_a_missing_check_passed(self):
        import board_doc as bd

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'board.html')
            with open(path, 'w', encoding='utf-8') as target:
                target.write(bd.assemble('', '', '', {'jobs': []}, '{}', ''))
            line = realdata_check.status_summary(path)

        self.assertIn('沒有驗收結果', line)

    def test_status_summary_says_when_links_were_not_checked(self):
        import board_doc as bd

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'board.html')
            status = {'schema_version': 2, 'checked_links': False, 'issues': []}
            with open(path, 'w', encoding='utf-8') as target:
                target.write(bd.assemble('', '', '', {'jobs': [], 'status': status}, '{}', ''))
            line = realdata_check.status_summary(path)

        self.assertIn('連結尚未檢查', line)

    def test_reconcile_diagnostics_uses_source_count_when_child_output_is_reordered(self):
        output = ('reconcile:比對來源,只重建變了的\n'
                  '來源警告數:0\n'
                  '看板:1 個職缺\n⚠ 1 個問題:\n'
                  '· 看板外殼\n· 可投遞夾\n—— 收尾 ——')

        summary = realdata_check.reconcile_diagnostics(output)

        self.assertIn('來源警告 0', summary)
        self.assertIn('來源警告類型 無', summary)

    def test_reconcile_diagnostics_counts_status_output_before_buffered_stage_headers(self):
        output = ('看板:1 個職缺\n'
                  '⚠ 1 個問題:\n'
                  'reconcile:比對來源,只重建變了的\n'
                  '來源警告數:0\n'
                  '· 看板外殼\n· 可投遞夾\n'
                  '✓ 可投遞夾:job\n'
                  '階段結果:套件失敗 0、看板驗收結束碼 1\n—— 收尾 ——')

        summary = realdata_check.reconcile_diagnostics(output)

        self.assertIn('來源警告 0', summary)
        self.assertIn('看板驗收問題 1', summary)
        self.assertIn('可投遞夾成功 1', summary)
        self.assertIn('看板驗收結束碼 1', summary)

    def test_reconcile_diagnostics_ignores_warning_glyph_inside_changed_filename(self):
        output = ('reconcile:比對來源,只重建變了的\n'
                  '  來源檔變更:resume ⚠ draft.pdf\n'
                  '· 看板外殼\n· 可投遞夾\n'
                  '  ✓ 可投遞夾:resume ⚠ draft\n'
                  '看板:0 個職缺\n—— 收尾 ——')

        summary = realdata_check.reconcile_diagnostics(output)

        self.assertIn('來源警告 0', summary)
        self.assertIn('來源警告類型 無', summary)
        self.assertIn('可投遞夾警告 0', summary)
        self.assertIn('可投遞夾成功 1', summary)

    def test_source_sync_failure_categories_use_manifest_state_only(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
        import config as cf
        import source_sync
        with tempfile.TemporaryDirectory(prefix='realdata-home-') as home:
            previous_home = os.environ.get('JOBSALVO_HOME')
            previous_cf_home = cf.HOME
            os.environ['JOBSALVO_HOME'] = home
            cf.reload(home)
            try:
                path = os.path.join(home, 'resume', 'example.md')
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as target:
                    target.write('synthetic')
                entry = {'kind': 'resume', 'id': 'synthetic', 'lang': 'zh',
                         'path': path, 'style_path': None}
                self.assertEqual(realdata_check.source_sync_failure_kinds([entry], {}),
                                 ['Markdown PDF 未更新'])

                manifest = {
                    source_sync.input_key(entry): source_sync.entry_fingerprint(entry),
                    source_sync.page_key(entry): 1,
                }
                manifest[source_sync.preview_attempt_key(entry)] = source_sync.entry_fingerprint(entry)
                output = source_sync.effective_path(entry)
                os.makedirs(os.path.dirname(output), exist_ok=True)
                with open(output, 'wb') as target:
                    target.write(b'%PDF-synthetic')
                self.assertEqual(realdata_check.source_sync_failure_kinds([entry], manifest),
                                 ['PDF 預覽未生成'])
            finally:
                if previous_home is None:
                    os.environ.pop('JOBSALVO_HOME', None)
                else:
                    os.environ['JOBSALVO_HOME'] = previous_home
                cf.reload(previous_cf_home)

    def test_reconcile_diagnostics_reports_warning_category_without_copied_values(self):
        output = ('reconcile:比對來源,只重建變了的\n'
                  '  ⚠ private-resume.md:PDF 預覽產生失敗:/private/path\n'
                  '來源診斷:PDF preview PreviewError at pdf_preview.py:52 generate\n'
                  '· 看板外殼\n· 可投遞夾\n'
                  '計時:來源 1.0s、外殼 0.1s、套件 2.0s、看板驗收 3.0s\n'
                  '  計時:套件 比對 0.1s、建置 1.2s\n'
                  '看板:0 個職缺\n—— 收尾 ——')

        summary = realdata_check.reconcile_diagnostics(output)

        self.assertIn('來源警告類型 PDF 預覽失敗', summary)
        self.assertIn('計時:來源 1.0s', summary)
        self.assertIn('計時:套件 比對 0.1s', summary)
        self.assertIn('PDF preview PreviewError at pdf_preview.py:52 generate', summary)
        self.assertNotIn('private-resume.md', summary)
        self.assertNotIn('/private/path', summary)

    def test_reconcile_diagnostics_identifies_traceback_without_printing_private_values(self):
        output = ('Traceback (most recent call last):\n'
                  '  File "/tmp/source-copy/jobsearch/tool.py", line 42, in render\n'
                  '    raise ValueError("private resume title in copied data")\n'
                  'ValueError: private resume title in copied data\n')

        summary = realdata_check.reconcile_diagnostics(output)

        self.assertIn('ValueError at tool.py:42 render', summary)
        self.assertNotIn('source-copy', summary)
        self.assertNotIn('private resume title', summary)

    def test_reconcile_diagnostics_reports_tool_frame_in_traceback(self):
        output = ('Traceback (most recent call last):\n'
                  '  File "/repo/tools/board_check.py", line 42, in main\n'
                  '  File "/python/lib/socket.py", line 729, in readinto\n'
                  'TimeoutError\n')

        summary = realdata_check.reconcile_diagnostics(output)

        self.assertIn('TimeoutError at socket.py:729 readinto', summary)
        self.assertIn('from board_check.py:42 main', summary)
        self.assertNotIn('/repo', summary)

    def test_realdata_copy_root_is_removed_automatically(self):
        with tempfile.TemporaryDirectory(prefix='realdata-source-') as source:
            with open(os.path.join(source, 'jobsalvo.json'), 'w', encoding='utf-8') as f:
                json.dump({'board': {'file': 'board.html'}}, f)
            roots = []

            def check_copy(_source, copy, _quick, *_extra):
                roots.append(os.path.dirname(copy))
                return 0

            with patch.object(realdata_check, '_check_copy', side_effect=check_copy):
                self.assertEqual(realdata_check.main([source, '--quick']), 0)

            self.assertEqual(len(roots), 1)
            self.assertFalse(os.path.exists(roots[0]))

    def test_external_writable_paths_and_symlink_targets_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='realdata-home-') as home, \
                tempfile.TemporaryDirectory(prefix='realdata-outside-') as outside:
            self.assertTrue(realdata_check.outside(home, {
                'paths': {'preference_note': os.path.join(outside, 'note.md')},
            }))
            self.assertEqual(realdata_check.outside(home, {
                'paths': {'preference_note': os.path.join(outside, 'note.md')},
            }, skip={('paths', 'preference_note')}), [])
            os.symlink(outside, os.path.join(home, 'linked'))
            self.assertTrue(realdata_check.outside(home, {
                'paths': {'prefs': 'linked/prefs.md'},
            }))
            self.assertTrue(realdata_check.outside(home, {
                'resume': {'ship_dir': '../outside-ship'},
            }))

    def test_absolute_input_note_is_copied_and_rewritten_to_clone(self):
        with tempfile.TemporaryDirectory(prefix='realdata-home-') as home, \
                tempfile.TemporaryDirectory(prefix='realdata-source-') as source:
            external = os.path.join(source, 'preference-note.md')
            with open(external, 'w', encoding='utf-8') as target:
                target.write('synthetic note')
            state = os.path.join(source, 'agent-state.json')
            with open(state, 'w', encoding='utf-8') as target:
                target.write('session state must not be copied')
            settings = {
                'paths': {'preference_note': external, 'tmp': '.realdata-check-tmp'},
                'browser': {'state': state},
            }

            copied = realdata_check._redirect_absolute_inputs(home, settings)

            self.assertEqual(copied, 1)
            self.assertFalse(os.path.isabs(settings['paths']['preference_note']))
            self.assertFalse(os.path.isabs(settings['browser']['state']))
            clone_note = os.path.join(home, settings['paths']['preference_note'])
            with open(clone_note, encoding='utf-8') as target:
                self.assertEqual(target.read(), 'synthetic note')
            self.assertFalse(os.path.exists(os.path.join(home, settings['browser']['state'])))
            self.assertEqual(realdata_check.outside(home, settings), [])


class PreviewDiagnostics(unittest.TestCase):
    def test_missing_preview_names_which_source_without_its_filename(self):
        """預覽沒產生時,診斷要寫出是第幾份來源、錯在哪一步,但不寫檔名(副本會被刪,之後才查得到)。"""
        import tempfile
        from unittest.mock import patch
        import source_sync
        with tempfile.TemporaryDirectory() as home:
            broken = os.path.join(home, 'secret-name.pdf')
            with open(broken, 'wb') as f:
                f.write(b'not a pdf')
            entry = {'kind': 'attachment', 'id': 'a', 'lang': 'zh', 'path': broken}
            diagnostics = []
            with patch.object(source_sync, 'files', return_value=[entry]):
                source_sync.refresh({}, diagnostics=diagnostics)
        self.assertEqual(len(diagnostics), 1)
        self.assertIn('PDF preview', diagnostics[0])
        self.assertIn('attachment #1', diagnostics[0])
        self.assertNotIn('secret-name', diagnostics[0])
