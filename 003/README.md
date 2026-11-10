# 美股市場情報中心 V2

## V2 新增
- 大盤 ETF：SPY、QQQ、DIA、IWM
- 產業／資產 ETF：XLK、SMH、XLF、XLY、XLC、XLI、XLP、XLV、XLE、TLT、GLD
- 大型科技股：NVDA、AAPL、MSFT、AMZN、GOOGL、META、AVGO、TSLA、ORCL、AMD、NFLX
- 宏觀代理：^VIX、^TNX、DX-Y.NYB（資料可能缺失）
- RSI、MACD、SMA20/50/200、ATR、ADX、布林通道、成交量比
- 個股相對 SPY 強弱、20 日高低區間、2×ATR 停損參考
- 市場環境評估、監控樣本廣度、科技股綜合排序
- GitHub Actions 每日排程與 GitHub Pages 儀表板

## 本機執行
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python analyze.py
python -m http.server 8000 --directory docs
```
瀏覽器開啟 http://localhost:8000

## GitHub 部署
1. 建立 GitHub repository，推送本專案。
2. Settings → Actions → General → Workflow permissions 設為 Read and write permissions。
3. Actions → Daily US Market Analysis V2 → Run workflow 手動執行測試。
4. Settings → Pages → Deploy from a branch → main → /docs。
5. GitHub Pages 顯示的網址即為儀表板網址。

## 排程
Cron `45 22 * * 1-5` 為 UTC 週一至週五 22:45，即台灣時間週二至週六 06:45。
美股一般交易日收盤後，這個時間通常留有報告生成緩衝。夏令時間與冬令時間不會影響 UTC 到台灣時間的換算，但美股收盤相對台灣時間會改變。
GitHub Actions 可能延遲，無法保證 08:00 前完成。若遇美股延長交易時段或重大資料延遲，報告仍可能不完整。

## 自訂股票
修改 config.json 的 tickers / market_etfs / sector_etfs / macro_tickers。

## 模型定義與限制
- 大盤分數由 SPY、QQQ、DIA、IWM 技術分數加權，並納入 VIX 與監控大型科技股 SMA20 廣度。
- 廣度只代表這份科技股觀察名單，不是 NYSE/Nasdaq 全市場 advance-decline breadth。
- 個股綜合分數 = 技術分數 + 相對 SPY 強弱加分 + 大盤環境加權。
- 2×ATR 停損參考 = 最新收盤價 - 2×ATR14，僅為波動風險參考，不保證成交，也不依個人持倉成本計算。
- ^TNX 是美國 10 年期殖利率的市場代理報價；不同資料商可能使用不同報價尺度。此版僅呈現原始行情，未把它當作精確利率事件資料。
- 宏觀日曆、CPI/PCE/非農/FOMC 實際公布值尚未整合；不可將 ETF 或指數代理誤當官方宏觀數據。
- Yahoo Finance/yfinance 非官方報價介面，可能延遲、限流、缺漏或變更。
- 新聞僅列來源標題，不做 AI 情緒分析。
- 本系統不是自動下單系統，不構成投資建議。
