"""
exchange_hours.py — 各交易所收盤時刻(台北時間)+ 半成品 K 棒判定(P0-C)

用途:判斷某「日期的場次」在台北某時刻是否已收盤,用來標記「收盤前抓取」的半成品 bar。
增量抓取若在交易所收盤前跑(典型:19:00 台北跑美股/歐股),抓到的當日 bar 是盤中值。
這些半成品須在收盤後重抓覆寫(import_kline 已改 INSERT OR REPLACE)。本模組:
  - is_intraday_suspect():某 bar 所屬場次收盤(台北)是否晚於抓取時刻 → 半成品嫌疑
  - suspicious_symbols():掃 kline.db,列出最後一根為半成品嫌疑的 symbol(供一次性清洗 + 回報)

時刻為「當地收盤 → 台北時間」換算(夏令時 2026-06 為準,取保守值;本模組僅用於
標記與驅動「重抓覆寫」,偏向多標記是安全的——頂多多重抓幾根)。
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta

# 各交易所「當地收盤」換算成台北時間(HH, MM)。next_day=True 代表收盤落在台北隔天凌晨。
EXCHANGE_CLOSE_TAIPEI: dict[str, dict] = {
    "TWSE":   {"hhmm": (13, 30), "next_day": False},   # 台股 13:30
    "TPEX":   {"hhmm": (13, 30), "next_day": False},
    "TSE":    {"hhmm": (14, 0),  "next_day": False},    # 東京 15:00 JST = 台北 14:00
    "KRX":    {"hhmm": (14, 30), "next_day": False},    # 首爾 15:30 KST = 台北 14:30
    "OMXCOP": {"hhmm": (23, 0),  "next_day": False},    # 哥本哈根 17:00 CEST = 台北 23:00
    "NASDAQ": {"hhmm": (4, 0),   "next_day": True},     # 美東 16:00 EDT = 台北次日 04:00
    "NYSE":   {"hhmm": (4, 0),   "next_day": True},
}

TW_EXCHANGES = ("TWSE", "TPEX")


def exchange_of(symbol: str) -> str:
    """TWSE:2330 → TWSE;NASDAQ:NVDA → NASDAQ。"""
    return symbol.split(":")[0]


def session_close_taipei(exchange: str, bar_date_str: str) -> datetime | None:
    """某交易所、某交易日(YYYY-MM-DD)場次的收盤時刻(台北 naive datetime)。
    未知交易所回 None。"""
    info = EXCHANGE_CLOSE_TAIPEI.get(exchange)
    if info is None:
        return None
    y, m, d = map(int, bar_date_str.split("-"))
    ch, cm = info["hhmm"]
    close = datetime(y, m, d, ch, cm)
    if info["next_day"]:
        close += timedelta(days=1)
    return close


def is_intraday_suspect(exchange: str, bar_date_str: str,
                        run_dt_taipei: datetime) -> bool:
    """該 bar 所屬場次的收盤(台北)是否晚於抓取時刻 → 半成品嫌疑(收盤前就被抓進來)。

    例:OMXCOP 6/11 場次收盤 = 台北 6/11 23:00;若 run_dt = 6/11 19:12 → 23:00 > 19:12 → 嫌疑。
        NASDAQ 6/10 場次收盤 = 台北 6/11 04:00;若 run_dt = 6/11 19:12 → 04:00 < 19:12 → 安全(已收)。
    未知交易所保守回 True(會被納入清洗)。"""
    close = session_close_taipei(exchange, bar_date_str)
    if close is None:
        return True
    return close > run_dt_taipei


def suspicious_symbols(conn: sqlite3.Connection, run_dt_taipei: datetime,
                       exclude_tw: bool = True) -> list[tuple[str, str, str]]:
    """掃 kline.db,回傳最後一根 bar 為半成品嫌疑的 [(symbol, last_date, exchange), ...]。
    exclude_tw:台股 19:00 跑時早已收盤,預設排除。"""
    rows = conn.execute(
        "SELECT symbol, MAX(date) FROM kline GROUP BY symbol"
    ).fetchall()
    out: list[tuple[str, str, str]] = []
    for sym, last_date in rows:
        if not last_date:
            continue
        ex = exchange_of(sym)
        if exclude_tw and ex in TW_EXCHANGES:
            continue
        if is_intraday_suspect(ex, last_date, run_dt_taipei):
            out.append((sym, last_date, ex))
    return out
