# 美股夜盤懶人包｜美股市場情報中心 V2.1

這個版本已依照 GitHub Pages 專案 `003` 的實際結構調整：**HTML 與 `latest.json` 都放在 repository 根目錄**，不再要求 Pages 從 `/docs` 讀取資料。

## 本版已修正
- `美股夜盤懶人包.html`、`dashboard.html`、`index.html` 都位於根目錄。
- `latest.json` 預先附上 **2026-11-09 美國交易日**收盤市場摘要，解壓縮部署後即可先看到分析內容。
- 儀表板以目前 HTML 所在目錄定位 `latest.json`，支援 GitHub Pages 專案子路徑（例如 `/003/`）。
- `analyze.py` 每次執行會同時更新根目錄 `latest.json`、`reports/latest.json` 與 `docs/latest.json`。
- GitHub Actions 會提交根目錄的資料檔與 HTML；保留 `docs/` 副本以相容舊設定。
- 加入 `index.html`，開啟專案首頁也能看到儀表板。

## GitHub 部署（建議照順序）
1. 解壓縮 ZIP。
2. 進入你現有的 GitHub repository `003`。
3. 將 ZIP 解壓後的**所有檔案與資料夾**上傳到 repository 的 `main` 分支根目錄；不要再多包一層 `us_market_monitor_v2` 資料夾。
4. GitHub → **Settings → Pages**：Build and deployment 選 `Deploy from a branch`；Branch 選 `main`；Folder 選 **`/ (root)`**；按 Save。
5. GitHub → **Settings → Actions → General → Workflow permissions**：選 `Read and write permissions`。
6. GitHub → Actions → `Daily US Market Analysis V2` → `Run workflow`，手動執行一次。
7. 等待 workflow 綠色勾勾，再開啟：
   - `https://rabbt0920-code.github.io/003/`
   - `https://rabbt0920-code.github.io/003/美股夜盤懶人包.html`
   - `https://rabbt0920-code.github.io/003/latest.json`

> 若你實際 GitHub 帳號網址中的帳號拼法不同，請沿用你目前已能正常開啟的 Pages 網域；本專案不會更改帳號名稱。

## 本機執行
需要 Python 3.12。於專案根目錄執行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python analyze.py
python -m http.server 8000
```

開啟 `http://localhost:8000/美股夜盤懶人包.html`。

## 自動更新時間
GitHub Actions cron 為 `22:45 UTC`，即台灣時間隔日 `06:45`（美股正常交易日後的台灣週二至週六）。GitHub Actions 可能延遲，不保證一定在 08:00 前完成。夏令／冬令時間會改變美股收盤對應的台灣時間，但排程仍固定於台灣 06:45。

## 2026-11-09 市場快照摘要
- S&P 500：7,549.94（+2.35%）
- Nasdaq Composite：25,253.14（+2.74%）
- Dow Jones：52,389.75（+2.15%）
- Russell 2000：2,840.81（+3.15%）
- VIX：19.92（約 -22.31%）
- 市場解讀：油價下跌帶動風險偏好回升，指數急彈；但 NVIDIA 逆勢下跌，反映大型科技／AI 族群內部分歧。應先視為急跌後反彈，等待後續交易日確認，不宜單憑單日大漲追價。

此為歷史收盤摘要，不是即時報價。詳細來源連結已放在儀表板與 `latest.json`。

## 注意事項
- 行情來源：Yahoo Finance / yfinance；可能延遲、限流、缺漏或調整。
- 盤前盤後資料不保證每個標的均可取得。
- RSI、MACD、SMA、ATR、ADX 等指標由規則式程式計算，沒有使用付費 AI API。
- 本系統僅供研究與資訊整理，不構成投資建議，不會自動下單。
