"""模組之間互相繞得到的群只准變少(docs/research/2026-11-26-arch-baseline.md 的排序一項一項拆)。

算載入就用和呼叫到才用的 import。出現新的群、或既有的群多了成員,就是又多了一處「改一個要連帶看一群」:
把新加的那條回頭依賴拆掉,不要把它加進下面的清單。拆掉一群之後,從清單刪掉它。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import dev_arch as da  # noqa: E402

ALLOWED = [
    {'add_job', 'autopilot', 'board_server', 'customize', 'cut_tailor', 'doctor', 'feedback_dump', 'find',
     'find_terms', 'gate', 'gate_flows', 'init', 'prefs', 'reconcile', 'settings_api'},
    {'agent_report', 'board_doc'},
    {'iv', 'iv_sheet'},
]


class ImportCycles(unittest.TestCase):
    def test_cycles_only_shrink(self):
        g = da.graph()
        for kinds, allowed in ((('top',), []), (('top', 'lazy'), ALLOWED)):
            for group in da.sccs(g['nodes'], da.adjacency(g['edges'], kinds)):
                with self.subTest(kinds=kinds, group=group):
                    self.assertTrue(any(set(group) <= a for a in allowed),
                                    f'新的互相依賴:{"、".join(group)}')


if __name__ == '__main__':
    unittest.main()
