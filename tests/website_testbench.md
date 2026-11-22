# 本機假網站測試台

## 主驗證不開網站

判四件任務對不對的是 `tests/test_accept_{sync,fill,progress,find}.py`:從任務入口(`main(argv, browser=)`)跑,唯一的接縫是瀏覽器邊界(ADR-0012),換成 `tests/fake_browser.py` 的假瀏覽器;輸入組合列在 `tests/acceptance_inputs.py`,每個行為由哪一個斷言守見 `docs/research/601-behaviors-map.md`。假瀏覽器跟真的收一樣的參數(`tests/test_fake_browser.py`)。

## 測試台考什麼

`tests/website_testbench.py` 在 `127.0.0.1` 開一次性假網站,用真瀏覽器考假瀏覽器做不出來的那一側:

- `test_ego_door.py`:真瀏覽器那一側怎麼把每個分頁讀成材料、截圖、沒有標籤的分頁、頁面不見了。
- `test_website_url_reading.py`:讀網址的三條路(直接抓、閱讀代理、瀏覽器)與公開網址檢查。
- `test_fill_page_decisions.py`:`form_plan.py` 在真頁上配答案、自己填格子(`docs/research/page-decisions.md`)。
- `test_website_testbench.py`:測試台自己的紀錄。

HTTP 狀態碼只留在假網站請求紀錄;頁面可讀狀態由讀頁結果決定(`/gone` 回 404、頁面原文仍讀得到 `not found`,不能只因 404 標成讀不到)。

## 執行

一次只跑一個瀏覽器測試檔。先設定 `CHROME_BIN` 指向本機 Chrome headless shell,再用 `lockf` 鎖住 `~/.cache/jobsalvo/569-notes/heavy.lock`:

```sh
lockf -k -w ~/.cache/jobsalvo/569-notes/heavy.lock uv run --frozen python -m unittest discover -s tests -p test_website_testbench.py
```

本機用 ego 時 `CHROME_BIN` 由 Chrome adapter 讀;CI 沒有 ego,用 CI 的 Chrome。測試台只連 `127.0.0.1`、用合成資料,不連真實求職網站。讀網址只在 `JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH=1` 時准 loopback。

`realdata_check.py` 是整批合併前的檢查,由做事的 agent 在最後跑一次,不屬於這裡的測試。
