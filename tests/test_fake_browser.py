"""驗收唯一的接縫(ADR-0012):假瀏覽器跟真的收一樣的參數,任務入口用錯參數叫邊界時,驗收劇本跟真的一樣丟錯。"""
import inspect
import unittest

from fake_browser import FakeBrowser
from web.outside import Browser, Real


def shape(fn):
    """位置參數只看個數與種類(名字可以不同),具名參數連名字一起比。"""
    return [(p.kind, p.name if p.kind is p.KEYWORD_ONLY else '') for p in inspect.signature(fn).parameters.values()]


class SameArgumentsTest(unittest.TestCase):
    def test_fake_browser_takes_the_same_arguments_as_the_real_one(self):
        names = [name for name in vars(Browser) if not name.startswith('_')]
        self.assertTrue(names)
        for name in names:
            self.assertEqual(shape(getattr(FakeBrowser, name)), shape(getattr(Real, name)), name)


if __name__ == '__main__':
    unittest.main()
