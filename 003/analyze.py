from __future__ import annotations

import json
import math
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
DOCS = ROOT / "docs"
TAIPEI = ZoneInfo("Asia/Taipei")


def number(value, digits=2):
    try:
        x = float(value)
        return round(x, digits) if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def fetch_history(symbol: str, period: str) -> pd.DataFrame:
    df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    if df.empty:
        raise RuntimeError("Yahoo Finance 沒有回傳日線資料")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c = x["Close"].astype(float)
    h = x["High"].astype(float)
    l = x["Low"].astype(float)
    v = x["Volume"].astype(float)

    x["SMA20"] = c.rolling(20).mean()
    x["SMA50"] = c.rolling(50).mean()
    x["SMA200"] = c.rolling(200).mean()

    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    x["RSI"] = 100 - 100 / (1 + rs)

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    x["MACD"] = ema12 - ema26
    x["MACD_SIGNAL"] = x["MACD"].ewm(span=9, adjust=False).mean()
    x["MACD_HIST"] = x["MACD"] - x["MACD_SIGNAL"]

    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    x["ATR14"] = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    x["ATR_PCT"] = x["ATR14"] / c * 100

    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    x["BB_UPPER"] = mid + 2 * std
    x["BB_LOWER"] = mid - 2 * std
    x["VOL_AVG20"] = v.rolling(20).mean()
    x["VOL_RATIO"] = v / x["VOL_AVG20"].replace(0, np.nan)

    # 14-day ADX approximation from directional movement.
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=x.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=x.index)
    atr = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    x["ADX14"] = dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    return x


def get_news(symbol: str, limit: int):
    result = []
    try:
        for item in (yf.Ticker(symbol).news or [])[:limit]:
            content = item.get("content", item)
            title = content.get("title") or item.get("title")
            if not title:
                continue
            provider = content.get("provider", {})
            canonical = content.get("canonicalUrl", {})
            click = content.get("clickThroughUrl", {})
            result.append({
                "title": str(title),
                "publisher": provider.get("displayName", "Yahoo Finance"),
                "published": content.get("pubDate", ""),
                "url": canonical.get("url") or click.get("url") or item.get("link") or ""
            })
    except Exception as exc:
        print(f"News unavailable for {symbol}: {exc}")
    return result


def series_payload(df: pd.DataFrame, count=120):
    out = []
    for idx, r in df.tail(count).iterrows():
        out.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": number(r["Open"]), "high": number(r["High"]),
            "low": number(r["Low"]), "close": number(r["Close"]),
            "volume": int(r["Volume"]) if pd.notna(r["Volume"]) else 0,
            "sma20": number(r["SMA20"]), "sma50": number(r["SMA50"]),
            "bb_upper": number(r["BB_UPPER"]), "bb_lower": number(r["BB_LOWER"])
        })
    return out


def technical_score(row):
    close = float(row["Close"])
    score, reasons = 0, []
    checks = [
        (pd.notna(row["SMA20"]) and close > row["SMA20"], 1, "收盤價高於 SMA20"),
        (pd.notna(row["SMA20"]) and close < row["SMA20"], -1, "收盤價低於 SMA20"),
        (pd.notna(row["SMA50"]) and close > row["SMA50"], 1, "收盤價高於 SMA50"),
        (pd.notna(row["SMA50"]) and close < row["SMA50"], -1, "收盤價低於 SMA50"),
        (pd.notna(row["MACD"]) and pd.notna(row["MACD_SIGNAL"]) and row["MACD"] > row["MACD_SIGNAL"], 1, "MACD 高於訊號線"),
        (pd.notna(row["MACD"]) and pd.notna(row["MACD_SIGNAL"]) and row["MACD"] < row["MACD_SIGNAL"], -1, "MACD 低於訊號線"),
        (pd.notna(row["RSI"]) and 50 <= row["RSI"] < 70, 1, "RSI 位於 50–70"),
        (pd.notna(row["RSI"]) and row["RSI"] < 50, -1, "RSI 低於 50"),
    ]
    for condition, points, label in checks:
        if condition:
            score += points
            reasons.append(label)
    return score, reasons


def fetch_asset(symbol, period, benchmark_close=None, news_limit=0, include_extended=False, ext_period="5d", ext_interval="5m"):
    raw = fetch_history(symbol, period)
    df = indicators(raw)
    r = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else r
    close = float(r["Close"])
    prev_close = float(prev["Close"])
    score, reasons = technical_score(r)

    relative_strength = None
    if benchmark_close is not None and len(df) >= 22:
        asset_return = close / float(df["Close"].iloc[-21]) - 1
        relative_strength = number((asset_return - benchmark_close) * 100)

    atr = number(r["ATR14"])
    stop = number(close - 2 * atr) if atr is not None else None
    support = number(df["Low"].tail(20).min())
    resistance = number(df["High"].tail(20).max())

    if score >= 3:
        trend = "偏多"
        action = "可列入分批布局觀察；等待回檔或突破確認，不宜一次投入"
    elif score <= -2:
        trend = "偏空"
        action = "以觀望及控制部位為主，等待價格重新站回關鍵均線"
    else:
        trend = "中性"
        action = "等待趨勢與動能指標一致，再考慮建立部位"

    rsi = number(r["RSI"])
    if rsi is not None and rsi >= 70:
        action = "短線偏熱，避免追價；等待拉回、量縮或重新整理"
    elif rsi is not None and rsi <= 30:
        action = "短線超賣風險高，不宜只因 RSI 低檔就抄底"

    volume_ratio = number(r["VOL_RATIO"])
    if volume_ratio is not None and volume_ratio >= 1.5:
        reasons.append("成交量達 20 日均量 1.5 倍以上")

    ext_rows, ext_note = [], "未取得盤前／盤後資料"
    if include_extended:
        try:
            ext = yf.Ticker(symbol).history(period=ext_period, interval=ext_interval, prepost=True, auto_adjust=True)
            if not ext.empty:
                for idx, er in ext.tail(120).iterrows():
                    ext_rows.append({"time": idx.isoformat(), "close": number(er["Close"]), "volume": int(er["Volume"]) if pd.notna(er["Volume"]) else 0})
                ext_note = "已取得部分延伸交易時段資料；可用性依 Yahoo Finance 回傳為準"
        except Exception as exc:
            ext_note = f"延伸時段資料不可用：{str(exc)[:120]}"

    return {
        "symbol": symbol, "price": number(close), "change_pct": number((close/prev_close-1)*100),
        "day_high": number(r["High"]), "day_low": number(r["Low"]),
        "volume": int(r["Volume"]) if pd.notna(r["Volume"]) else 0,
        "rsi": rsi, "macd": number(r["MACD"], 4), "macd_signal": number(r["MACD_SIGNAL"], 4),
        "sma20": number(r["SMA20"]), "sma50": number(r["SMA50"]), "sma200": number(r["SMA200"]),
        "atr14": atr, "atr_pct": number(r["ATR_PCT"]), "adx14": number(r["ADX14"]),
        "volume_ratio": volume_ratio, "bb_upper": number(r["BB_UPPER"]), "bb_lower": number(r["BB_LOWER"]),
        "support20": support, "resistance20": resistance, "stop_reference": stop,
        "relative_strength_vs_spy_pct": relative_strength,
        "technical_score": score, "trend": trend, "action": action, "reasons": reasons,
        "daily": series_payload(df), "extended": ext_rows, "extended_note": ext_note,
        "news": get_news(symbol, news_limit) if news_limit else []
    }


def safe_fetch_group(symbols, period, errors, **kwargs):
    result = []
    for symbol in symbols:
        try:
            print(f"Fetching {symbol}")
            result.append(fetch_asset(symbol, period, **kwargs))
            time.sleep(0.6)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
            print(f"FAILED {symbol}: {exc}")
    return result


def market_assessment(etfs, macros, stocks):
    lookup = {x["symbol"]: x for x in etfs}
    score, notes = 0, []
    for symbol, weight in [("SPY", 2), ("QQQ", 2), ("DIA", 1), ("IWM", 1)]:
        item = lookup.get(symbol)
        if item:
            contribution = 1 if item["technical_score"] >= 2 else (-1 if item["technical_score"] <= -1 else 0)
            score += contribution * weight
            notes.append(f"{symbol}：{item['trend']}（技術分數 {item['technical_score']}）")

    macro = {x["symbol"]: x for x in macros}
    vix = macro.get("^VIX", {}).get("price")
    if vix is not None:
        if vix >= 30:
            score -= 3
            notes.append(f"VIX {vix:.1f}：高波動警戒")
        elif vix >= 20:
            score -= 1
            notes.append(f"VIX {vix:.1f}：波動偏高")
        else:
            score += 1
            notes.append(f"VIX {vix:.1f}：波動相對溫和")

    # Market breadth: share of monitored mega-cap stocks above their 20-day average.
    valid = [x for x in stocks if x.get("sma20") is not None and x.get("price") is not None]
    above20 = (sum(x["price"] > x["sma20"] for x in valid) / len(valid) * 100) if valid else None
    if above20 is not None:
        if above20 >= 65:
            score += 2
            notes.append(f"觀察科技股中 {above20:.0f}% 位於 SMA20 上方：廣度偏強")
        elif above20 <= 35:
            score -= 2
            notes.append(f"觀察科技股中僅 {above20:.0f}% 位於 SMA20 上方：廣度偏弱")
        else:
            notes.append(f"觀察科技股 SMA20 上方比例 {above20:.0f}%：廣度中性")

    if score >= 5:
        regime, risk = "偏多", "一般"
        summary = "主要指數與市場廣度整體偏正向，可優先尋找相對強勢標的，但仍需遵守分批與停損紀律。"
    elif score <= -4:
        regime, risk = "偏空", "偏高"
        summary = "大盤趨勢或波動條件不利，應降低追價意願並優先管理既有部位風險。"
    else:
        regime, risk = "震盪／中性", "中等"
        summary = "市場訊號尚未形成一致方向，宜等待突破或回檔確認，避免在盤整區間過度交易。"

    return {
        "score": score, "regime": regime, "risk_level": risk, "summary": summary,
        "vix": vix, "mega_cap_above_sma20_pct": number(above20),
        "notes": notes
    }


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    errors = []
    benchmark_symbol = config.get("benchmark", "SPY")

    # Fetch benchmark first so stock relative strength can be measured over the same lookback.
    try:
        bench_raw = fetch_history(benchmark_symbol, config["history_period"])
        bench_close = bench_raw["Close"].dropna()
        benchmark_return_20d = float(bench_close.iloc[-1] / bench_close.iloc[-21] - 1) if len(bench_close) >= 21 else None
    except Exception as exc:
        benchmark_return_20d = None
        errors.append({"symbol": benchmark_symbol, "error": f"Benchmark return unavailable: {exc}"})

    common = dict(period=config["history_period"], benchmark_close=benchmark_return_20d)
    etfs = safe_fetch_group(config["market_etfs"] + config["sector_etfs"], config["history_period"], errors)
    stocks = safe_fetch_group(
        config["tickers"], config["history_period"], errors,
        benchmark_close=benchmark_return_20d, news_limit=config.get("news_limit", 4),
        include_extended=True, ext_period=config.get("extended_hours_period", "5d"),
        ext_interval=config.get("extended_hours_interval", "5m")
    )
    macros = safe_fetch_group(config["macro_tickers"], config["history_period"], errors)

    if not etfs and not stocks:
        raise RuntimeError("所有主要標的均無法取得資料，請檢查網路或 Yahoo Finance 可用性")

    market = market_assessment(etfs, macros, stocks)

    # Combine market regime with stock technical score; this is a ranking overlay, not a buy signal.
    ranked = []
    for item in stocks:
        relative = item.get("relative_strength_vs_spy_pct")
        relative_bonus = 1 if relative is not None and relative > 0 else (-1 if relative is not None and relative < -2 else 0)
        market_overlay = 1 if market["regime"] == "偏多" else (-1 if market["regime"] == "偏空" else 0)
        combined = item["technical_score"] + relative_bonus + market_overlay
        item["relative_strength_bonus"] = relative_bonus
        item["market_overlay"] = market_overlay
        item["combined_score"] = combined
        item["rank_action"] = (
            "優先觀察強勢回檔／突破" if combined >= 4 and market["regime"] != "偏空"
            else "等待趨勢確認" if combined >= 1
            else "降低優先級，先控管風險"
        )
        ranked.append(item)
    ranked.sort(key=lambda x: x["combined_score"], reverse=True)

    now = datetime.now(timezone.utc).astimezone(TAIPEI)
    report = {
        "version": "2.0",
        "generated_at": now.isoformat(),
        "generated_at_taipei": now.strftime("%Y-%m-%d %H:%M:%S"),
        "market": market,
        "market_etfs": etfs,
        "sector_etfs": [x for x in etfs if x["symbol"] not in config["market_etfs"]],
        "macros": macros,
        "stocks": ranked,
        "errors": errors,
        "data_notes": [
            "資料由 Yahoo Finance/yfinance 提供，並非交易所保證即時報價。",
            "日線指標使用調整後日線資料；盤前盤後 5 分鐘資料僅作延伸行情參考。",
            "市場廣度僅以本系統監控的大型科技股樣本計算，不代表全市場廣度。",
            "VIX、10 年期殖利率及美元指數可能有延遲或缺值；缺值時不會虛構數值。",
            "新聞僅列標題與來源，尚未進行情緒分類或事件驗證。"
        ],
        "disclaimer": "本系統為規則式研究工具，不構成投資建議。停損參考值為收盤價減 2 倍 ATR 的風險參考，不保證成交或適合所有持倉。"
    }

    REPORTS.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (REPORTS / "latest.json").write_text(payload, encoding="utf-8")
    shutil.copy2(REPORTS / "latest.json", DOCS / "latest.json")
    shutil.copy2(ROOT / "dashboard.html", DOCS / "index.html")
    print(f"Generated V2 report at {now.strftime('%Y-%m-%d %H:%M:%S')} Taipei")
    print(f"Market: {market['regime']} | risk: {market['risk_level']}")
    print(f"Stocks: {len(stocks)} | ETF: {len(etfs)} | Macro: {len(macros)} | errors: {len(errors)}")


if __name__ == "__main__":
    main()
