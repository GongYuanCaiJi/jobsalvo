import itertools
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import dev_arch as da  # noqa: E402

FILES = {
    'tools/a.py': 'import b\nopen("shared.json")\n',
    'tools/b.py': 'def f():\n    import a\n',
    'tools/c.py': 'from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import a\nelse:\n    import b\n',
    'tools/d.py': 'import subprocess, urllib.request\nsubprocess.run(["python", "a.py"])\n'
                  'urllib.request.urlopen("http://127.0.0.1/api/x")\nopen("shared.json")\n',
    'tools/e.py': 'import os, sys\nimport b\nos.path.getmtime("b.py")\ncmd = [sys.executable, os.path.join("x", "b.py")]\n',
    'tools/f.py': 'def url(key):\n    return "/api/preview/" + key\n',
    'tools/g.py': 'import os, subprocess, sys\nC = os.path.join("x", "c.py")\nsubprocess.run([sys.executable, C])\n',
    'tools/h.py': 'import subprocess, sys\ndef argv():\n    return [sys.executable, "c.py"]\ndef go(cmd):\n    subprocess.Popen(cmd)\n'
                  'go(argv())\nsubprocess.Popen(["agent", f"run {sys.executable} a.py"])\n',
    'tools/web/__init__.py': '',
    'tools/web/x.py': 'from . import y\nfrom web.y import Z\n',
    'tools/web/y.py': 'import a\n',
    'board/board.js': 'fetch("/api/x")\n',
    'board/board.css': '.a{}\n',
}


class Graph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        for rel, text in FILES.items():
            path = os.path.join(cls.tmp.name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(textwrap.dedent(text))
        cls.g = da.graph(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_kind_of_edge(self):
        self.assertEqual(self.g['edges'], {
            ('a', 'b', 'top'), ('b', 'a', 'lazy'), ('c', 'a', 'type'), ('c', 'b', 'top'),
            ('d', 'a', 'subprocess'), ('d', 'board_server', 'http'), ('board.js', 'board_server', 'http'),
            ('e', 'b', 'top'), ('g', 'c', 'subprocess'), ('h', 'c', 'subprocess'),
            ('web.x', 'web.y', 'top'), ('web.y', 'a', 'top'),
        })
        self.assertEqual(self.g['shared'], {'shared.json': ['a', 'd']})
        self.assertIn('board/board.css', self.g['loc'])

    def test_cycle_only_with_lazy_imports(self):
        nodes = self.g['nodes']
        self.assertEqual(da.sccs(nodes, da.adjacency(self.g['edges'], ('top',))), [])
        self.assertEqual(da.sccs(nodes, da.adjacency(self.g['edges'], ('top', 'lazy'))), [['a', 'b']])


class Numbers(unittest.TestCase):
    def test_feedback_edges_bounds(self):
        adj = {'a': {'b'}, 'b': {'a'}, 'c': {'d'}, 'd': {'c', 'e'}, 'e': set()}
        lower, cut = da.feedback_edges(sorted(adj), adj)
        self.assertEqual(lower, 2)
        self.assertEqual(len(cut), 2)
        rest = {v: adj[v] - {b for a, b in cut if a == v} for v in adj}
        self.assertEqual(da.sccs(sorted(adj), rest), [])

    def test_feedback_edges_is_the_minimum(self):
        # 貪婪排序在這張圖切 3 條,最少是 2 條;逐一排列暴力算對照
        adj = {'a': {'d'}, 'b': {'a', 'd'}, 'c': {'b', 'd'}, 'd': {'a', 'c'}}
        nodes = sorted(adj)
        brute = min(sum(1 for a in nodes for b in adj[a] if order.index(b) < order.index(a))
                    for order in itertools.permutations(nodes))
        lower, cut = da.feedback_edges(nodes, adj)
        self.assertEqual((lower, len(cut)), (brute, brute))
        rest = {v: adj[v] - {b for a, b in cut if a == v} for v in adj}
        self.assertEqual(da.sccs(nodes, rest), [])

    def test_propagation_cost_of_a_chain(self):
        self.assertAlmostEqual(da.propagation_cost(['a', 'b', 'c'], {'a': {'b'}, 'b': {'c'}}), 6 / 9)

    def test_group_commits_counts_recent_multi_file_changes(self):
        sets = [('new', {'p', 'q'}), ('new', {'p'}), ('big', {'p', 'q', *(f'f{i}' for i in range(30))}), ('old', {'q', 'r'})]
        self.assertEqual(da.group_commits(sets, ['p', 'q', 'r'], recent=2), (1, 2))

    def test_co_change_thresholds(self):
        sets = [('x', {'p', 'q'})] * 5 + [('x', {'p'})] * 5 + [('big', {f'f{i}' for i in range(31)} | {'p', 'r'})] * 9
        self.assertEqual(da.co_change(sets, {'p', 'q', 'r'}), [('p', 'q', 5, round(5 / 7.5, 2))])


if __name__ == '__main__':
    unittest.main()
