"""題庫逐字稿裡的 markdown 表格要畫成 <table>,表格以外的文字照原樣。"""
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class IvTableTest(unittest.TestCase):
    def test_table_rendered_and_other_text_kept(self):
        src = (ROOT / 'board/board.js').read_text()
        start = src.index('  function ivSegHTML(')
        fn = src[start:src.index('  function ivBodyHTML(', start)]
        code = r'''
const assert=require('node:assert/strict');
function escA(s){return esc(s).replace(/"/g,'&quot;');}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function ivLinkify(h){return h;}
''' + fn + r'''
const t=ivSegHTML('前言\n| 環節 | 對應 skill |\n|---|---|\n| A | Matt `grill-with-docs` <b> |\n| B | `tdd` |\n後記');
assert.ok(t.startsWith('前言<table class="iv-tb">'));
assert.equal((t.match(/<tr>/g)||[]).length,3);
assert.ok(t.includes('<code>grill-with-docs</code>'));
assert.ok(t.includes('&lt;b&gt;'));
assert.ok(t.endsWith('</table>後記'));
const g=ivSegHTML('前\n![樹](fig:a-1.svg)\n後');
assert.ok(g.includes('<img class="iv-fig" src="/api/iv/fig?f=a-1.svg" alt="樹">'));
assert.ok(g.startsWith('前<a ')&&g.endsWith('</a>後'));
assert.ok(!ivSegHTML('![x](fig:../a.svg)').includes('<img'));
assert.equal(ivSegHTML('| 單獨一行 |\n沒有分隔線'),'| 單獨一行 |\n沒有分隔線');
'''
        r = subprocess.run(['node', '-e', code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
