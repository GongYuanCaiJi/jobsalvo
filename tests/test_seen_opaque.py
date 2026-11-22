"""頁面內容型別拿不出字、不能比較(ADR-0012 第 1 點):lint 認名字,這裡驗執行時也擋。"""
import copy
import dataclasses
import json
import os
import pickle
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from web.seen import Quote, Seen, Status  # noqa: E402


class OpaqueTest(unittest.TestCase):
    def test_page_content_cannot_be_read_compared_or_copied_out(self):
        for value in (Seen('1:p1', 'https://a.example/x', Status.READ, parts=('秘密的字',)), Quote('秘密的字', True)):
            for attempt in (lambda: value == value, lambda: hash(value), lambda: str(value), lambda: f'{value}',
                            lambda: '秘密' in value, lambda: list(value), lambda: value[0], lambda: copy.copy(value),
                            lambda: pickle.dumps(value), lambda: json.dumps(value), lambda: vars(value),  # nosemgrep: python.lang.security.deserialization.pickle.avoid-pickle  只驗證打包會失敗,不解開任何資料
                            lambda: dataclasses.asdict(value), lambda: setattr(value, '_seen_text', 'x')):
                with self.subTest(type=type(value).__name__), self.assertRaises(TypeError):
                    attempt()
            self.assertNotIn('秘密', repr(value))
            self.assertFalse(hasattr(value, '__dict__'))


if __name__ == '__main__':
    unittest.main()
