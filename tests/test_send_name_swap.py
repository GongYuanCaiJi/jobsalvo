# -*- coding: utf-8 -*-
"""送出檔名改了:填好、還沒送出的卡,申請頁上的檔名是舊的,要算「換檔」(重填)。"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
import board_server as bs  # noqa: E402

URL = 'https://jobs.example/1'


class SendNameSwap(unittest.TestCase):
    def snapshot(self, send_name, merged=''):
        doc = {'data': {'jobs': [{'id': URL}]}, 'fb': json.dumps({URL: {}})}
        docs = [{'id': 'resume:a:en', 'effective_path': '', 'send_name': send_name}]
        with mock.patch.object(bs, 'read_doc', return_value=''), \
                mock.patch('board_doc.parse', return_value=doc), \
                mock.patch('delivery_state.state', return_value='parked'), \
                mock.patch('delivery_state.FILES_MATTER', frozenset({'parked'})), \
                mock.patch('ship.documents', return_value=docs), \
                mock.patch.dict(bs.cf.C, {'resume': {'merged_name': merged}}):
            return bs.sent_files()

    def test_a_renamed_send_name_or_merged_template_counts_as_changed_files(self):
        self.assertNotEqual(self.snapshot('Resume A'), self.snapshot('Resume B'))
        self.assertNotEqual(self.snapshot('Resume A'), self.snapshot('Resume A', '{resume}+{n}'))
        self.assertEqual(self.snapshot('Resume A'), self.snapshot('Resume A'))


if __name__ == '__main__':
    unittest.main()
