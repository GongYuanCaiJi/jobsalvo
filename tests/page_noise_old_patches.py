"""把以前的「依頁面內容判斷」補丁放回 tools/,看頁面變形開關開著時四件驗收測試紅不紅(#776)。

補丁照 docs/research/601-exam.md 的 S 表(來源 tests/fixtures/past_patches 是掃描器考題的片段,不能直接執行),
改成會真的在讀頁那一側執行的樣子。每個補丁各跑兩次:開關關(基準)、開關開;開關開的要紅,關的紅了表示原本的測試就抓得到。
網址、分頁標籤、行程設定這類補丁(S01、S17–S19、S22、B04、B09–B11、L01–L03)頁面變形碰不到(網址與標籤不准變),不在這裡。
用法:python3 tests/page_noise_old_patches.py [補丁名 ...]  (一個行程跑,重;結果印在標準輸出)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE]
SEEN = os.path.join(os.path.dirname(HERE), 'tools', 'web', 'seen.py')
ANCHOR = "    parts = tuple(part for part in parts if isinstance(part, str) and part)\n"
FETCH_ANCHOR = "    lines = tuple(line for line in str(result.get('text') or '').splitlines() if line)\n"
PATCHES = {
    # S03 r2_placeholder:依「未公開、查無」等字把段落當空白丟掉
    'S03-placeholder': ANCHOR + "    import re\n    parts = tuple(p for p in parts if not re.fullmatch("
                       "r'(無|未明載|未公開|不公開|未提供|未列|查無|n/a|none|-)([（(].*)?', p.strip(), re.I))\n",
    # S02 r2_capture_delta:頁上有「紀錄讀取失敗」就當讀不到
    'S02-read-failed-text': ANCHOR + "    if any('紀錄讀取失敗' in p for p in parts):\n        parts = ()\n",
    # S20 zh_in_text:頁上有「已下架」就當沒有文字
    'S20-taken-down-text': ANCHOR + "    if any('下架' in p for p in parts):\n        parts = ()\n",
    # S05 r4_01 寫死日期格式:先把 2026/1/2 改寫成 2026-1-2 再比
    'S05-date-format': ANCHOR + "    import re\n    parts = tuple(re.sub(r'(\\d{4})/(\\d{1,2})/(\\d{1,2})', r'\\1-\\2-\\3', p) for p in parts)\n",
    # S03 讀網址那條路:r2_placeholder 的丟段落放在 seen._fetched
    'S03-placeholder-fetch': (FETCH_ANCHOR, FETCH_ANCHOR + "    import re\n    lines = tuple(p for p in lines if not re.fullmatch("
                              "r'(無|未明載|未公開|不公開|未提供|未列|查無|n/a|none|-)([（(].*)?', p.strip(), re.I))\n"),
    # S05 讀網址那條路:r4_01 的日期改寫放在 seen._fetched
    'S05-date-format-fetch': (FETCH_ANCHOR, FETCH_ANCHOR + "    import re\n    lines = tuple(re.sub(r'(\\d{4})/(\\d{1,2})/(\\d{1,2})', r'\\1-\\2-\\3', p) for p in lines)\n"),
    # S06 r4_02 寫死網站的人數 class:有這個 class 的段落不當一段
    'S06-applicant-class': ANCHOR + "    import re\n    parts = tuple(p for p in parts if not re.search(r'class=\"num-applicants__[^\"]*\"[^>]*>(.*?)</', p, re.S))\n",
    # S07 r4_03 寫死網站 JSON 的 applicant_count 欄位:有這個欄位的段落不當一段
    'S07-applicant-json': ANCHOR + "    import re\n    parts = tuple(p for p in parts if not re.search(r'applicant_count\\\\?\"\\s*:\\s*(\\d+)', p))\n",
}


def child(noise_on):
    """子行程:用磁碟上現在的 tools/ 跑一遍,印紅的測試(JSON)。"""
    import json
    import page_noise
    import test_accept_noise as driver
    noise = page_noise.from_env(f"{os.environ.get('JOBSALVO_NOISE_SEED', 1)}:where={os.environ.get('JOBSALVO_NOISE_WHERE', 2)}")
    print('RESULT' + json.dumps(driver.red(noise) if noise_on else driver.red_plain(), ensure_ascii=False))


def run(name, noise_on):
    import json
    import subprocess
    with open(SEEN, encoding='utf-8') as source:
        original = source.read()
    assert ANCHOR in original
    try:
        anchor, text = PATCHES[name] if isinstance(PATCHES[name], tuple) else (ANCHOR, PATCHES[name])
        assert anchor in original
        with open(SEEN, 'w', encoding='utf-8') as target:
            target.write(original.replace(anchor, text, 1))
        out = subprocess.run([sys.executable, __file__, '--child', 'on' if noise_on else 'off'], capture_output=True, text=True,
                             cwd=HERE).stdout
    finally:
        with open(SEEN, 'w', encoding='utf-8') as target:
            target.write(original)
    return json.loads(out[out.rindex('RESULT') + 6:])


def main(names):
    for name in names or PATCHES:
        for label, noise_on in (('開關關', False), ('開關開', True)):
            wrong = run(name, noise_on)
            print(f'{name} {label}: {"綠" if not wrong else f"紅 {len(wrong)} 條"}', flush=True)
            for test, message in wrong[:6]:
                print(f'    {test}\n      {message}', flush=True)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--child']:
        child(sys.argv[2] == 'on')
    else:
        main(sys.argv[1:])
