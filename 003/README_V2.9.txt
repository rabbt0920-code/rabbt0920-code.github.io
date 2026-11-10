美股市場情報中心 V2.9

本版修正台指期夜盤歷史資料混合不同到期月份的問題。使用期交所官方 TX 最近月契約、盤後時段 Last 欄位；每個交易日只保留一筆近月資料。卡片價格直接採官方 Last，並驗證圖表序列末筆一致。舊版歷史資料不沿用，部署後由 GitHub Actions 逐日重新累積。

部署：將本 ZIP 解壓後的檔案覆蓋 GitHub repository 根目錄（保留 .github/workflows/daily_analysis.yml），確認 workflow 的 working-directory 與 repository 結構一致，再執行 Actions。
