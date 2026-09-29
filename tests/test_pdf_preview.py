import os
import subprocess
import tempfile
import unittest
from unittest import mock

import _env  # noqa: F401

import pdf_preview


class PreviewRenderer(unittest.TestCase):
    def test_uses_pdftoppm_when_quick_look_is_missing(self):
        """不是 Mac(沒有 Quick Look)時,改用跨平台的 pdftoppm 做預覽圖。"""
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            with open(cmd[-1] + '.png', 'wb') as f:
                f.write(b'\x89PNG synthetic')
            return subprocess.CompletedProcess(cmd, 0, '', '')

        tools = {'qlmanage': None, 'pdftoppm': '/usr/bin/pdftoppm'}
        with tempfile.TemporaryDirectory() as home:
            pdf = os.path.join(home, 'a.pdf')
            with open(pdf, 'wb') as f:
                f.write(b'%PDF-1.4 synthetic\n%%EOF')
            with mock.patch.object(pdf_preview.shutil, 'which', side_effect=tools.get), \
                 mock.patch.object(pdf_preview.subprocess, 'run', side_effect=run):
                key = pdf_preview.generate(pdf, home)
            self.assertTrue(key)
            self.assertEqual(os.path.basename(calls[0][0]), 'pdftoppm')
            self.assertTrue(os.path.getsize(pdf_preview.cache_path(key, home)))

    def test_no_renderer_at_all_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as home:
            pdf = os.path.join(home, 'a.pdf')
            with open(pdf, 'wb') as f:
                f.write(b'%PDF-1.4 synthetic\n%%EOF')
            with mock.patch.object(pdf_preview.shutil, 'which', return_value=None):
                with self.assertRaises(pdf_preview.PreviewError):
                    pdf_preview.generate(pdf, home)


if __name__ == '__main__':
    unittest.main()
