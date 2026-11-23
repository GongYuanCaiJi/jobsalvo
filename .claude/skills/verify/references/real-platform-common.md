# 真實平台共用驗收

## 執行順序

先跑 [Automated coverage](../SKILL.md#automated-coverage) 列的對應測試，再在副本操作相同入口，最後依功能頁執行真實平台命令。平台清單取自副本 `profiles.json` 的平台鍵（不以 `_` 開頭的鍵）。命令在前景執行；等待程序結束並收集結果後才清理副本。

## 操作邊界與完成條件

使用者處理登入與驗證碼，並保留登入狀態。需要填表的流程只填寫欄位並停在送出前；不送出申請，也不輸入密碼。

每個流程至少執行一輪。不同平台的 `fill` 可平行；同一平台的流程依序執行，其他重檢查一次執行一項。

Agent 完成後重新讀取同一平台頁面：程式保留 TaskSpace 時（填表的 `apply.workspace`）在那個 TaskSpace 讀，程式已關閉 TaskSpace 時用本輪驗證 TaskSpace 讀；記錄 TaskSpace ID 和流程前後的申請、訊息、平台履歷寫入數。程式證據、頁面重讀及平台前後比較一致時標為 `VERIFIED`；結果不一致或無法確認時記錄實際結果並停止該平台本輪。

## 證據位置

平台證據沿用 `tools/evidence.py` 的格式。原始頁面、動作紀錄與截圖留在 `$RUN/home/evidence/`；去識別證據保存於 `$EVIDENCE/`。程式的輪次照 `tools/evidence.py`：有職缺卡片的輪在 `<卡片 ID>/<輪次>/`，沒有卡的輪在 `_<流程>/<輪次>/`（找缺 `_find`、履歷同步 `_sync`）；交付證據照同樣的相對路徑放在 `$EVIDENCE/` 下。

保存命令、結束碼、TaskSpace ID、頁面截圖和狀態摘要；隱私遮罩依主 skill 的 [Evidence](../SKILL.md#evidence)。完整平台回報和暫存輸出留在副本內，不放入交付證據。
