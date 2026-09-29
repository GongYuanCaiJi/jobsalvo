# -*- coding: utf-8 -*-
"""簡體介面:繁→簡照 OpenCC tw2sp,加上這個產品自己的用詞;字典跟著頁面送,資料不轉。"""
import os, sys, json, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
import zhconv  # noqa: E402


class Convert(unittest.TestCase):
    def test_taiwan_words_become_mainland_words(self):
        self.assertEqual(zhconv.to_simplified('軟體設定、網路'), '软件设置、网络')

    def test_our_own_words(self):
        # 字典沒有、或換了會跑掉意思的:「資料夾」是檔案資料夾,不是「數據夾」
        self.assertEqual(zhconv.to_simplified('你的履歷'), '你的简历')
        self.assertEqual(zhconv.to_simplified('資料夾版本紀錄'), '文件夹版本纪录')
        self.assertEqual(zhconv.to_simplified('你的資料'), '你的资料')
        self.assertEqual(zhconv.to_simplified('待評估的職缺'), '待评估的职位')

    def test_ascii_and_simplified_are_left_alone(self):
        self.assertEqual(zhconv.to_simplified('agent 在 http://localhost:8899'), 'agent 在 http://localhost:8899')
        self.assertEqual(zhconv.to_simplified('软件设置'), '软件设置')

    def test_which_settings_mean_simplified(self):
        for lang in ('zh-CN', 'zh-cn', 'zh-Hans', 'zh-SG'):
            self.assertTrue(zhconv.simplified(lang), lang)
        for lang in ('zh', 'zh-TW', 'en', '', None):
            self.assertFalse(zhconv.simplified(lang), lang)

    def test_page_tables_are_json_and_follow_the_same_rules(self):
        tables = json.loads(json.dumps(zhconv.page_tables(), ensure_ascii=False))
        self.assertEqual(len(tables), 2)
        # 用頁面拿到的字典照同一套最長詞規則轉,結果要跟 Python 版一樣
        def conv(text):
            for table, longest in tables:
                out, i = '', 0
                while i < len(text):
                    for L in range(min(longest, len(text) - i), 0, -1):
                        if text[i:i + L] in table:
                            out += table[text[i:i + L]]; i += L; break
                    else:
                        out += text[i]; i += 1
                text = out
            return text
        for s in ('軟體設定、網路', '你的履歷', '資料夾版本紀錄', '待評估的職缺'):
            self.assertEqual(conv(s), zhconv.to_simplified(s))


class ServedPage(unittest.TestCase):
    def test_only_the_simplified_setting_gets_the_tables_and_data_is_untouched(self):
        import board_doc as bd, board_server as bs
        job = {'id': 'https://ex.test/j/1', 'target': '軟體工程師（資料夾）', 'cat': '其他'}
        doc = bd.assemble(':root{}', '<b id="stat-first">0</b>', '', {'jobs': [job]}, '{}', '/*app*/')
        for lang, want in (('zh', False), ('zh-CN', True)):
            with unittest.mock.patch.object(bs, 'page_cfg', return_value={'read_lang': lang}):
                d = bd.parse(bs.serve_doc(doc))['data']
            self.assertEqual('zh_tables' in d['cfg'], want, lang)
            self.assertEqual(d['jobs'][0]['target'], '軟體工程師（資料夾）')   # 資料原封不動


import unittest.mock  # noqa: E402

if __name__ == '__main__':
    unittest.main()
