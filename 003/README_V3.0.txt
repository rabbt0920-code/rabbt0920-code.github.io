美股市場情報中心 V3.0｜台指期多來源與過期資料防護

本版以 V2.9 為基礎，保留美股大盤、科技股、台灣企業美股、PWA 等功能，重點更新台指期資料品質。

資料來源分工：
1. TAIFEX OpenAPI DailyMarketReportFut：官方每日行情，TX 近月、盤後時段、Last 欄位作為夜盤正式收盤價。
2. TAIFEX Daily Market Report 網頁：人工核對官方日期、時段、契約月份及 Last；頁面依官方交易日期歸屬規則顯示夜盤。
3. TAIFEX Info Hub：提供盤中日盤／夜盤參考資訊，官方明確提醒資料有抽樣差異，因此只作交叉檢視，不覆蓋正式日報收盤價。
4. Fubon Neo API：可提供即時行情與歷史 K 線，但需使用者自行申請並完成券商帳戶／憑證授權；本免費版不會假設使用者已有憑證，也不會偽裝成已接通即時報價。

重要修正：
- 加入資料新鮮度檢查。若資料來自快取，或夜盤交易日期已落後超過 1 個平日，卡片價格顯示「—」，不再把舊價格當成最新報價。
- 過期時保留歷史圖表供查閱，但技術趨勢標示「資料過期」，暫停操作建議。
- 新增資料狀態、夜盤交易日、契約月份、抓取時間等欄位。
- 卡片價格、漲跌與成交量不會在過期時顯示為最新數據。
- PWA Service Worker cache version 更新為 V3.0，降低手機持續讀取舊版快取的機率。
- Workflow 範本調整為台灣時間 07:15 執行（UTC 23:15，週一至週五 UTC）。

部署：
1. 解壓 ZIP。
2. 將程式檔案覆蓋到 GitHub repository 中原本放置 analyze.py、dashboard.html、config.json 的資料夾。
3. 保留目前已成功執行的 .github/workflows/daily_analysis.yml，不要因為 GitHub 網頁隱藏檔案限制而刪除它。若要改排程，請編輯既有 workflow 的 cron，不必重新建立 workflow。
4. 確認 workflow 的 working-directory 指向專案資料夾（例如 003），requirements 路徑與 git add 路徑也一致。
5. 推送後手動執行 Actions；成功後用手機瀏覽器重新整理網站。若仍見舊版，關閉已安裝 PWA 後重新開啟，必要時移除主畫面捷徑再重新加入。

限制：
- TAIFEX Info Hub 的盤中資訊為抽樣參考，不是逐筆完整成交資料。
- TAIFEX 官方網站 FAQ 表示網站以瀏覽為主，未提供可直接用於程式的即時行情指令。真正即時串流需另接有授權的券商／行情商 API。
- 此版本實際自動讀取的正式價格仍以 TAIFEX OpenAPI 官方日報為準；Info Hub 是人工核對入口，不是被爬取的第二個報價引擎。
- GitHub Actions 排程可能延遲；報告時間不代表行情本身即時。
- 本系統為規則式研究輔助，不構成投資建議。
