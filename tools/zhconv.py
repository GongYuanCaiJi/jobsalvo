#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zhconv —— 看板介面的繁體(台灣)→ 簡體。照 OpenCC 的 tw2sp:先把台灣用詞換成大陸用詞(軟體→軟件、網路→網絡),
再把繁體字換成簡體字。字典是 OpenCC 的(third_party/opencc,Apache-2.0),不用另外裝套件。

轉在看板畫面那一層(board.js 的 zhView):字典跟著頁面送過去,頁面把畫面上的字轉掉。
程式碼和資料都不轉:程式裡有拿中文去比對資料的地方(「暖網」「碩士」),轉了就對不上;
資料轉了再存回去,存檔的版本比對也會跟伺服器上的對不起來。這支的 to_simplified 是同一套規則的 Python 版,測試與指令列用。

  uv run python tools/zhconv.py '軟體設定'      # 软件设置
"""
import os, sys, functools

HERE = os.path.dirname(os.path.abspath(__file__))
DICT = os.path.join(os.path.dirname(HERE), 'third_party', 'opencc')
# 照 OpenCC 的 tw2sp.json:兩段,每段一組字典,段內取最長的詞
CHAIN = (('TWPhrasesRev', 'TWVariantsRevPhrases', 'TWVariantsRev'), ('TSPhrases', 'TSCharacters'))
# 這個產品自己的用詞:字典沒有、或換了意思會跑掉的(「資料夾」是檔案資料夾,不是「數據夾」)
OURS = {'履歷': '簡歷', '資料夾': '文件夾', '資料': '資料', '職缺': '職位', '看板': '看板'}
SIMPLIFIED = ('zh-cn', 'zh-hans', 'zh-sg', 'zh-my')


def simplified(lang):
    """這個語言設定要不要看簡體。"""
    return str(lang or '').strip().lower() in SIMPLIFIED


@functools.lru_cache(maxsize=None)
def _stages():
    stages = []
    for i, names in enumerate(CHAIN):
        table = {}
        for name in names:
            with open(os.path.join(DICT, name + '.txt'), encoding='utf-8') as f:
                for line in f:
                    k, _, v = line.rstrip('\n').partition('\t')
                    if k and v:
                        table.setdefault(k, v.split(' ')[0])
        if i == 0:
            table.update(OURS)
        stages.append((table, max(map(len, table))))
    return stages


@functools.lru_cache(maxsize=1)
def page_tables():
    """送到頁面的字典:[[{詞: 換成}, 最長詞長], …],照順序一段一段轉。"""
    return [[table, longest] for table, longest in _stages()]


def _stage(text, table, longest):
    out, i, n = [], 0, len(text)
    while i < n:
        for L in range(min(longest, n - i), 0, -1):
            v = table.get(text[i:i + L])
            if v is not None:
                out.append(v)
                i += L
                break
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


@functools.lru_cache(maxsize=64)
def to_simplified(text):
    if not text or text.isascii():
        return text
    for table, longest in _stages():
        text = _stage(text, table, longest)
    return text


if __name__ == '__main__':
    print(to_simplified(' '.join(sys.argv[1:])))
