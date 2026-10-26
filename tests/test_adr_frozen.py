"""已定案的 ADR 只准改檔頭狀態(#549)。"""
import os, sys, unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')))
from adr_frozen import rewritten  # noqa: E402

A = 'docs/adr/0001-a.md'
ADR = '# 決定\n\n內文。\n'


class AdrFrozen(unittest.TestCase):
    def test_only_the_status_changes_passes(self):
        self.assertEqual(rewritten({A: ADR}, {A: '---\nstatus: superseded by ADR-0010\n---\n\n' + ADR}), [])

    def test_body_rewrite_is_blocked(self):
        self.assertEqual(rewritten({A: ADR}, {A: '# 決定\n\n改過的內文。\n'}), [A])

    def test_deleting_an_adr_is_blocked(self):
        self.assertEqual(rewritten({A: ADR}, {}), [A])

    def test_proposed_adr_can_still_change(self):
        self.assertEqual(rewritten({A: '---\nstatus: proposed\n---\n\n' + ADR}, {A: '# 決定\n\n改過。\n'}), [])

    def test_renaming_alone_passes(self):
        self.assertEqual(rewritten({A: ADR}, {'docs/adr/0001-renamed.md': ADR}), [])

    def test_renaming_and_rewriting_is_blocked(self):
        self.assertEqual(rewritten({A: ADR}, {'docs/adr/0001-renamed.md': '# 決定\n\n改過的內文。\n'}), [A])

    def test_new_adr_passes(self):
        self.assertEqual(rewritten({A: ADR}, {A: ADR, 'docs/adr/0002-b.md': '# 新決定\n'}), [])


if __name__ == '__main__':
    unittest.main()
