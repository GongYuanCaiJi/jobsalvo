"""模組之間不准互相繞得到(docs/research/2026-11-26-arch-baseline.md):依賴只准單向。

算載入就用和呼叫到才用的 import。出現互相依賴的群,就是又多了一處「改一個要連帶看一群」:
把新加的那條回頭依賴拆掉。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import dev_arch as da  # noqa: E402


class ImportCycles(unittest.TestCase):
    def test_no_module_depends_back_on_itself(self):
        g = da.graph()
        for kinds in (('top',), ('top', 'lazy')):
            for group in da.sccs(g['nodes'], da.adjacency(g['edges'], kinds)):
                with self.subTest(kinds=kinds, group=group):
                    self.fail(f'新的互相依賴:{"、".join(group)}')


if __name__ == '__main__':
    unittest.main()
