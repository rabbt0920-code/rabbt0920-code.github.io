from __future__ import annotations

import json
import math
import re
import shutil
import time
from datetime import datetime, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
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



def fetch_taifex_night_session(config: dict) -> dict:
    """
    Parse the TAIFEX DailyMarketReportFut schema strictly.

    Official field names:
      Date, Contract, ContractMonth(Week), TradingSession,
      Open, High, Low, Last, Change, %, Volume, OpenInterest.

    Important: Volume is for the selected contract/month row, not the
    aggregated volume of all TX contract months and not the continuous
    Yahoo WTX& series.
    """
    api_url = config.get("api_url", "https://openapi.taifex.com.tw/v1/DailyMarketReportFut")
    response = requests.get(
        api_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MarketMonitor/2.8)"},
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("Data") or payload.get("result")
    else:
        rows = None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("TAIFEX DailyMarketReportFut 回傳格式錯誤或沒有資料")

    required = {
        "Date", "Contract", "ContractMonth(Week)", "TradingSession",
        "Open", "High", "Low", "Last", "Change", "%", "Volume",
    }
    sample = next((r for r in rows if isinstance(r, dict)), None)
    if sample is None:
        raise RuntimeError("TAIFEX API 資料列不是 JSON 物件")
    missing = sorted(required - set(sample.keys()))
    if missing:
        raise RuntimeError(
            "TAIFEX API 欄位與預期 schema 不符，缺少：" + ", ".join(missing)
            + "；本次停止更新，避免錯誤欄位被當成行情。"
        )

    def parse_num(value, field):
        if value is None:
            return None
        text_value = str(value).strip().replace(",", "").replace("%", "")
        if text_value in {"", "-", "－", "NULL", "null", "N/A"}:
            return None
        try:
            result = float(text_value)
        except (TypeError, ValueError):
            raise RuntimeError(f"TAIFEX 欄位 {field} 無法轉成數字：{value!r}")
        if not math.isfinite(result):
            raise RuntimeError(f"TAIFEX 欄位 {field} 不是有限數值")
        return result

    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        contract = str(row.get("Contract", "")).strip().upper()
        session = str(row.get("TradingSession", "")).strip()
        if contract != "TX" or session != "盤後":
            continue

        month = str(row.get("ContractMonth(Week)", "")).strip()
        # TX monthly futures only: exclude weekly contracts and calendar spreads.
        if not re.fullmatch(r"\d{6}", month):
            continue

        date_raw = str(row.get("Date", "")).strip()
        date_digits = re.sub(r"\D", "", date_raw)
        if len(date_digits) != 8:
            continue
        date_key = f"{date_digits[:4]}-{date_digits[4:6]}-{date_digits[6:8]}"

        record = {
            "date": date_key,
            "contract": contract,
            "contract_month": month,
            "open": parse_num(row.get("Open"), "Open"),
            "high": parse_num(row.get("High"), "High"),
            "low": parse_num(row.get("Low"), "Low"),
            # Do not silently fall back to settlement/close/other fields.
            "close": parse_num(row.get("Last"), "Last"),
            "change": parse_num(row.get("Change"), "Change"),
            "change_pct": parse_num(row.get("%"), "%"),
            "volume": parse_num(row.get("Volume"), "Volume"),
            "open_interest": parse_num(row.get("OpenInterest"), "OpenInterest"),
            "session": "盤後",
            "source": "TAIFEX OpenAPI DailyMarketReportFut",
            "source_url": "https://openapi.taifex.com.tw/",
            "retrieved_at_taipei": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
            "volume_scope": "單一 TX 契約月份；非所有月份合計",
            "raw_field_mapping": {
                "open": "Open", "high": "High", "low": "Low",
                "close": "Last", "change": "Change", "change_pct": "%",
                "volume": "Volume",
            },
        }

        o, h, l, c, v = (
            record["open"], record["high"], record["low"],
            record["close"], record["volume"],
        )
        if any(x is None for x in (o, h, l, c, v)):
            continue
        if min(o, h, l, c) <= 0 or v < 0:
            continue
        if h < max(o, l, c) or l > min(o, h, c) or h < l:
            continue
        # Reject malformed price rows; never publish a questionable quote.
        if not (20000 <= c <= 60000):
            continue
        candidates.append(record)

    if not candidates:
        raise RuntimeError(
            "TAIFEX 日報沒有通過驗證的 TX 盤後月契約資料；"
            "請檢查官方 API 欄位、夜盤日報是否已發布。"
        )

    latest_date = max(x["date"] for x in candidates)
    same_date = [x for x in candidates if x["date"] == latest_date]
    # The closest expiry is the smallest YYYYMM among listed monthly TX contracts.
    same_date.sort(key=lambda x: x["contract_month"])
    selected = same_date[0]

    # If the adjacent listed month is implausibly far from the selected month,
    # stop instead of silently selecting a malformed or mismapped row.
    if len(same_date) > 1:
        next_month = same_date[1]
        gap_pct = abs(selected["close"] / next_month["close"] - 1) * 100
        if gap_pct > 5:
            raise RuntimeError(
                "TX 近月與次近月收盤價差異超過 5%，疑似欄位或契約資料異常；"
                "本次不更新夜盤行情。"
            )

    selected["contract_count_for_date"] = len(same_date)
    selected["validated"] = True
    selected["validation_note"] = (
        "已確認 Contract=TX、TradingSession=盤後，"
        "並使用官方 Last 作收盤、Volume 作該契約月份成交量。"
    )
    return selected


def load_taifex_night_history(config: dict) -> tuple[pd.DataFrame, dict]:
    archive_path = REPORTS / "taifex_night_history.json"
    history = []
    if archive_path.exists():
        try:
            history = json.loads(archive_path.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []
    # V2.9 migration: discard pre-2.9 history because earlier builds could mix
    # delivery months when constructing the indicator series. Only retain rows
    # explicitly written by this parser version and mapped from official Last.
    clean_history = []
    for item in history:
        if not isinstance(item, dict) or item.get("parser_version") not in {"2.9", "3.0"}:
            continue
        try:
            o, h, l, c, v = [float(item.get(k)) for k in ("open", "high", "low", "close", "volume")]
            valid_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(item.get("date", ""))))
            valid_month = bool(re.fullmatch(r"\d{6}", str(item.get("contract_month", ""))))
            mapping_ok = (item.get("raw_field_mapping") or {}).get("close") == "Last"
            valid = (valid_date and valid_month and mapping_ok and item.get("contract") == "TX"
                     and item.get("session") == "盤後" and 20000 <= c <= 60000
                     and min(o, h, l, c) > 0 and v >= 0
                     and h >= max(o, l, c) and l <= min(o, h, c))
            if valid:
                clean_history.append(item)
        except (TypeError, ValueError):
            continue
    # One observation per session date. If an old archive contains several
    # expiries for one date, keep the nearest monthly contract only.
    by_date = {}
    for item in clean_history:
        key = item["date"]
        if key not in by_date or item["contract_month"] < by_date[key]["contract_month"]:
            by_date[key] = item
    history = list(by_date.values())
    fresh_status = "fresh"
    try:
        latest = fetch_taifex_night_session(config.get("taiwan_futures", {}))
        latest["parser_version"] = "3.0"
        # The latest official near-month record replaces every expiry for that date.
        history = [x for x in history if x.get("date") != latest["date"]]
        history.append(latest)
        history = sorted(history, key=lambda x: x.get("date", ""))[-260:]
    except Exception:
        if not history:
            raise RuntimeError("期交所夜盤資料目前無法取得，且沒有 V2.9 已驗證歷史資料；停止發布，避免顯示錯誤報價。")
        latest = sorted(history, key=lambda x: x.get("date", ""))[-1]
        fresh_status = "cached"
    REPORTS.mkdir(exist_ok=True)
    archive_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    frame = pd.DataFrame(history)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    frame = frame.rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"
    })
    if "Volume" not in frame:
        frame["Volume"] = 0
    frame["Volume"] = frame["Volume"].fillna(0)
    # Fail-safe freshness gate: never label an old cached close as the latest quote.
    # One missed weekday is tolerated (weekends/holidays can delay publication);
    # two or more weekdays old is stale and must not produce a live-looking card.
    today_tw = datetime.now(TAIPEI).date()
    session_day = datetime.strptime(latest["date"], "%Y-%m-%d").date()
    weekday_age = sum(1 for i in range((today_tw - session_day).days) if (session_day + pd.Timedelta(days=i+1)).weekday() < 5)
    is_stale = (fresh_status == "cached") or weekday_age > 1
    latest["_fresh_status"] = "stale" if is_stale else fresh_status
    latest["is_stale"] = is_stale
    latest["weekday_age"] = int(weekday_age)
    latest["latest_status_note"] = (
        f"官方夜盤資料交易日期 {latest['date']}；距台灣今日已過 {weekday_age} 個平日。"
        + ("資料已過期，保留歷史參考但不提供最新報價／操作建議。" if is_stale else "資料日期通過新鮮度檢查。")
    )
    return frame[["Open", "High", "Low", "Close", "Volume"]], latest


def build_taifex_night_asset(config: dict) -> dict:
    frame, latest = load_taifex_night_history(config)
    df = indicators(frame)
    row = df.iloc[-1]
    previous = df.iloc[-2] if len(df) > 1 else row
    score, reasons = technical_score(row)
    # The headline quote must be the official Last from the selected near-month
    # night-session row. The indicator series must end at exactly the same value.
    close = float(latest["close"])
    series_close = float(row["Close"])
    if abs(close - series_close) > 0.01:
        raise RuntimeError(
            f"TX 夜盤價格一致性檢查失敗：官方 Last={close:.2f}，指標序列末筆={series_close:.2f}；停止發布。"
        )
    change_pct = latest.get("change_pct")
    if change_pct is None and float(previous["Close"]) != 0:
        change_pct = (close / float(previous["Close"]) - 1) * 100
    if latest.get("is_stale"):
        trend, action = "資料過期", "目前顯示的是最後一筆已驗證歷史夜盤資料，不是最新行情；暫停依此價格產生操作建議，請等待期交所更新。"
    elif score >= 3:
        trend, action = "偏多", "夜盤技術面偏強，可觀察回測 SMA20／前低是否有支撐；避免追逐單根急漲。"
    elif score <= -2:
        trend, action = "偏空", "夜盤技術面偏弱，優先控管槓桿與部位；等待重新站回關鍵均線再評估。"
    else:
        trend, action = "中性", "夜盤方向尚未明確，等待價格、均線與 MACD 同向，不宜僅憑單一夜盤漲跌追單。"
    if pd.notna(row.get("RSI")) and row["RSI"] >= 70:
        action += " RSI 偏熱，短線避免追價。"
    elif pd.notna(row.get("RSI")) and row["RSI"] <= 30:
        action += " RSI 處於低檔不代表止跌，避免單憑超賣訊號抄底。"
    daily = series_payload(df, count=260)
    # Preserve session dates as YYYY-MM-DD for chart labels.
    return {
        "symbol": "TX", "name": "臺股期貨近月（官方夜盤）",
        "price": None if latest.get("is_stale") else number(close),
        "last_verified_price": number(close), "quote_status": "stale" if latest.get("is_stale") else "fresh",
        "retrieved_at_taipei": latest.get("retrieved_at_taipei"),
        "weekday_age": latest.get("weekday_age", 0),
        "latest_status_note": latest.get("latest_status_note", ""),
        "change_pct": None if latest.get("is_stale") else number(change_pct),
        "day_high": None if latest.get("is_stale") else number(row["High"]),
        "day_low": None if latest.get("is_stale") else number(row["Low"]),
        "volume": None if latest.get("is_stale") else int(latest.get("volume") or 0),
        "rsi": number(row.get("RSI")), "macd": number(row.get("MACD"), 4),
        "macd_signal": number(row.get("MACD_SIGNAL"), 4),
        "sma20": number(row.get("SMA20")), "sma50": number(row.get("SMA50")),
        "sma200": number(row.get("SMA200")), "atr14": number(row.get("ATR14")),
        "atr_pct": number(row.get("ATR_PCT")), "adx14": number(row.get("ADX14")),
        "volume_ratio": number(row.get("VOL_RATIO")), "bb_upper": number(row.get("BB_UPPER")),
        "bb_lower": number(row.get("BB_LOWER")),
        "support20": number(df["Low"].tail(20).min()),
        "resistance20": number(df["High"].tail(20).max()),
        "stop_reference": number(close - 2 * float(row["ATR14"])) if pd.notna(row.get("ATR14")) else None,
        "technical_score": None if latest.get("is_stale") else score, "trend": trend, "action": action, "reasons": reasons,
        "daily": daily, "extended": [], "extended_note": "官方收盤資料採 TAIFEX OpenAPI；Info Hub 僅作盤中參考交叉檢視，不覆蓋官方收盤值。",
        "session": "盤後（夜盤）", "session_date": latest["date"],
        "contract_month": latest["contract_month"], "source": latest["source"],
        "open_interest": latest.get("open_interest"),
        "volume_scope": latest.get("volume_scope", "單一 TX 契約月份；非所有月份合計"),
        "validated": bool(latest.get("validated", False)),
        "is_stale": bool(latest.get("is_stale", False)),
        "validation_note": latest.get("validation_note", ""),
        "contract_count_for_date": latest.get("contract_count_for_date"),
        "history_count": len(frame),
        "history_note": ("本次取得期交所最新夜盤資料。" if latest.get("_fresh_status") == "fresh" else "官方 API 暫無新資料或回傳日期過舊；僅保留最後有效歷史供圖表參考，報價卡不顯示為最新值。") + "歷史夜盤資料由 GitHub Actions 每次執行後累積；首次部署前不會自動補齊完整歷史。",
    }

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

    # Assess the US market before generating Taiwan-issuer action observations.
    market = market_assessment(etfs, macros, stocks)

    # US-listed Taiwanese issuers are analyzed separately from the US mega-cap universe.
    taiwan_us_stocks = []
    tw_us_cfg = config.get("taiwan_us_stocks", [])
    for company in tw_us_cfg:
        symbol = company["symbol"]
        try:
            print(f"Fetching Taiwan issuer {symbol}")
            item = fetch_asset(
                symbol, config["history_period"],
                benchmark_close=benchmark_return_20d,
                news_limit=config.get("news_limit", 4),
                include_extended=True,
                ext_period=config.get("extended_hours_period", "5d"),
                ext_interval=config.get("extended_hours_interval", "5m")
            )
            item.update({
                "name": company["name"],
                "tw_symbol": company.get("tw_symbol", "—"),
                "sector": company.get("sector", ""),
                "exchange": company.get("exchange", ""),
                "profile": company.get("profile", "")
            })
            rel = item.get("relative_strength_vs_spy_pct")
            rel_text = (
                f"近 20 個交易日相對 SPY {'強勢' if rel > 0 else '弱勢'} {abs(rel):.2f} 個百分點"
                if rel is not None else "相對 SPY 資料不足"
            )
            if item["technical_score"] >= 3 and market["regime"] != "偏空":
                action = "偏多觀察：可等待回檔守住 SMA20／SMA50，或帶量突破近 20 日壓力後再分批評估；不追單日急漲。"
            elif item["technical_score"] <= -2 or market["regime"] == "偏空":
                action = "保守觀察：暫不急著加碼，先確認是否重新站回 SMA20／SMA50；若持有，依個人風險承受度檢查停損與部位。"
            else:
                action = "中性觀察：等待價格、均線與 MACD 同向；可先列觀察清單，不因單一指標或單日漲跌進場。"
            if item.get("rsi") is not None and item["rsi"] >= 70:
                action += " RSI 已進入偏熱區，短線避免追價。"
            elif item.get("rsi") is not None and item["rsi"] <= 30:
                action += " RSI 處於低檔不代表已止跌，避免只憑超賣訊號抄底。"
            item["relative_strength_note"] = rel_text
            item["action"] = action
            item["investment_view"] = (
                "強勢候選" if item["technical_score"] >= 3 and market["regime"] != "偏空"
                else "風險優先" if item["technical_score"] <= -2 or market["regime"] == "偏空"
                else "等待確認"
            )
            taiwan_us_stocks.append(item)
            time.sleep(0.6)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"Taiwan US-listed issuer unavailable: {exc}"})
            print(f"FAILED Taiwan issuer {symbol}: {exc}")

    taiwan_us_stocks.sort(
        key=lambda x: (x.get("technical_score", -99), x.get("relative_strength_vs_spy_pct") or -999),
        reverse=True
    )

    # Taiwan index futures: official TAIFEX after-hours report, separate from US market scoring.
    taiwan_futures = None
    tw_cfg = config.get("taiwan_futures", {})
    try:
        print("Fetching official TAIFEX TX after-hours session (with freshness validation)")
        taiwan_futures = build_taifex_night_asset(tw_cfg)
        taiwan_futures["description"] = tw_cfg.get(
            "description", "臺灣期貨交易所官方 TX 盤後（夜盤）資料"
        )
        taiwan_futures["market"] = "TAIFEX"
    except Exception as exc:
        errors.append({"symbol": "TX-NIGHT", "error": f"TAIFEX night session unavailable: {exc}"})
        print(f"FAILED TAIFEX night session: {exc}")

    if not etfs and not stocks:
        raise RuntimeError("所有主要標的均無法取得資料，請檢查網路或 Yahoo Finance 可用性")

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
        "version": "3.0",
        "taiwan_futures": taiwan_futures,
        "taiwan_us_stocks": taiwan_us_stocks,
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
            "新聞僅列標題與來源，尚未進行情緒分類或事件驗證。",
            "台指期夜盤官方收盤以 TAIFEX OpenAPI DailyMarketReportFut 的 TX 近月盤後 Last 為準；TAIFEX Info Hub 僅作盤中參考（官方註明抽樣差異），不得覆蓋官方收盤。",
            "台指期資料加入平日新鮮度檢查；官方 API 失敗或日期落後時，只保留歷史圖表，不把快取價格標示為最新報價，也暫停產生操作建議。",
            "TAIFEX 官方網站說明其網站供瀏覽使用，未提供可直接程式化取得的即時行情指令；需要正式即時行情串流時，須另接有授權的期貨行情供應商（例如券商 API），並由使用者自行完成帳戶／憑證授權。",
            "台灣企業美股分頁包含美國市場交易的台灣企業股票／存託憑證；以美元報價，與台股普通股價格、交易時段及匯率不同。",
            "AUOTY 為友達光電美國 OTC Pink ADR，並非 NYSE 上市股票；OTC 流動性、報價延遲及歷史資料完整度可能較低。",
            "台灣企業美股操作觀察由技術指標及美股大盤環境規則式產生，未納入完整財報估值、ADR費用、匯率與個人持倉成本。"
        ],
        "disclaimer": "本系統為規則式研究工具，不構成投資建議。停損參考值為收盤價減 2 倍 ATR 的風險參考，不保證成交或適合所有持倉。"
    }

    REPORTS.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (REPORTS / "latest.json").write_text(payload, encoding="utf-8")
    history_file = REPORTS / "taifex_night_history.json"
    if history_file.exists():
        shutil.copy2(history_file, ROOT / "taifex_night_history.json")
    # GitHub Pages for this repository is configured to publish from the repository root.
    # Keep latest.json beside the HTML files so relative fetch('./latest.json') always resolves.
    (ROOT / "latest.json").write_text(payload, encoding="utf-8")
    shutil.copy2(REPORTS / "latest.json", DOCS / "latest.json")
    shutil.copy2(ROOT / "dashboard.html", DOCS / "index.html")
    shutil.copy2(ROOT / "dashboard.html", ROOT / "index.html")
    if (ROOT / "美股夜盤懶人包.html").exists():
        shutil.copy2(ROOT / "dashboard.html", ROOT / "美股夜盤懶人包.html")
    print(f"Generated V2 report at {now.strftime('%Y-%m-%d %H:%M:%S')} Taipei")
    print(f"Market: {market['regime']} | risk: {market['risk_level']}")
    print(f"Stocks: {len(stocks)} | Taiwan US issuers: {len(taiwan_us_stocks)} | ETF: {len(etfs)} | Macro: {len(macros)} | errors: {len(errors)}")


if __name__ == "__main__":
    main()
