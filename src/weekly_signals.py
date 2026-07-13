"""
weekly_signals.py — 週報機構訊號區資料組裝(stage12 §5.2,純顯示不進計分)

七訊號,分四小區:
  信用與流動性:HY OAS(FRED BAMLH0A0HYM2,一年線+450bp 警戒)
               2s10s(FRED T10Y2Y,零軸+倒掛區間)
  市場內部:台股寬度(watchlist 站上 20/60MA 比例,kline.db 自算)
           外資台指期淨 OI vs 大盤(期交所,雙軸)
  情緒:VIX/VIX3M 比值(1.0 警戒線)
  景氣:Sahm(FRED SAHMREALTIME,0.5 觸發線)、景氣燈號(國發會,色塊時間軸)

輸出進 weekly.json "signals" 區塊;各訊號獨立 try,單項失敗不拖累整包。
"""
from __future__ import annotations
import json
import os
import sqlite3

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MACRO_DB = os.path.join(PROJECT_ROOT, "macro.db")
KLINE_DB = os.path.join(PROJECT_ROOT, "kline.db")
WATCHLIST = os.path.join(PROJECT_ROOT, "config", "watchlist.json")

DAILY_WINDOW = 252          # 日頻訊號顯示近一年
MONTHLY_WINDOW = 36         # 月頻訊號顯示近三年
BREADTH_MIN_SYMBOLS = 60    # 寬度序列起點:至少 N 檔有 MA60(避免早期樣本過少失真)


def _ro(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _daily_series(db_path: str, table: str, value_col: str,
                  window: int = DAILY_WINDOW) -> dict:
    conn = _ro(db_path)
    rows = conn.execute(
        f"SELECT date, {value_col} FROM {table} "
        f"WHERE {value_col} IS NOT NULL ORDER BY date DESC LIMIT ?",
        (window,)).fetchall()
    conn.close()
    rows.reverse()
    return {"dates": [r[0] for r in rows], "values": [r[1] for r in rows]}


# ── 信用與流動性 ──────────────────────────────────────────────────────────────
def hy_oas(db_path: str = MACRO_DB) -> dict:
    """HY OAS 近一年 + 滾動一年均線(以序列內既有觀測計,不足 252 用可得長度)。"""
    conn = _ro(db_path)
    rows = conn.execute(
        "SELECT date, value FROM hy_oas_daily ORDER BY date DESC LIMIT ?",
        (DAILY_WINDOW * 2,)).fetchall()
    conn.close()
    rows.reverse()
    vals = [r[1] for r in rows]
    ma = []
    for i in range(len(vals)):
        lo = max(0, i + 1 - DAILY_WINDOW)
        ma.append(round(sum(vals[lo:i + 1]) / (i + 1 - lo), 3))
    keep = rows[-DAILY_WINDOW:]
    return {"dates": [r[0] for r in keep],
            "values": [r[1] for r in keep],
            "ma1y": ma[-DAILY_WINDOW:],
            "alert_pct": 4.5,                      # 450bp 警戒
            "latest": keep[-1][1] if keep else None,
            "source": "FRED BAMLH0A0HYM2(ICE BofA US High Yield OAS)"}


def t10y2y(db_path: str = MACRO_DB) -> dict:
    s = _daily_series(db_path, "t10y2y_daily", "value")
    return {**s, "latest": s["values"][-1] if s["values"] else None,
            "source": "FRED T10Y2Y(10 年期減 2 年期公債利差)"}


# ── 市場內部 ──────────────────────────────────────────────────────────────────
def _tw_symbols(watchlist_path: str = WATCHLIST) -> list[str]:
    with open(watchlist_path, encoding="utf-8") as f:
        w = json.load(f)
    out = []
    for grp in w.get("台股板塊", {}).values():
        out += [m["code"] for m in grp.get("成員", [])]
    return sorted(set(out))


def breadth(kline_path: str = KLINE_DB, watchlist_path: str = WATCHLIST) -> dict:
    """watchlist 台股站上 MA20/MA60 收盤比例(%);樣本足夠(≥N 檔有 MA60)才起算。"""
    symbols = _tw_symbols(watchlist_path)
    conn = _ro(kline_path)
    per_date: dict[str, list[tuple[float, float | None, float | None]]] = {}
    for sym in symbols:
        rows = conn.execute(
            "SELECT date, close FROM kline WHERE symbol=? ORDER BY date", (sym,)
        ).fetchall()
        closes = [r[1] for r in rows]
        for i, (d, c) in enumerate(rows):
            ma20 = sum(closes[i - 19:i + 1]) / 20 if i >= 19 else None
            ma60 = sum(closes[i - 59:i + 1]) / 60 if i >= 59 else None
            per_date.setdefault(d, []).append((c, ma20, ma60))
    conn.close()
    dates, pct20, pct60 = [], [], []
    for d in sorted(per_date):
        entries = per_date[d]
        with60 = [(c, m20, m60) for c, m20, m60 in entries if m60 is not None]
        if len(with60) < BREADTH_MIN_SYMBOLS:
            continue
        with20 = [(c, m20) for c, m20, _ in entries if m20 is not None]
        dates.append(d)
        pct20.append(round(sum(1 for c, m in with20 if c > m) / len(with20) * 100, 1))
        pct60.append(round(sum(1 for c, _, m in with60 if c > m) / len(with60) * 100, 1))
    dates, pct20, pct60 = (dates[-DAILY_WINDOW:], pct20[-DAILY_WINDOW:],
                           pct60[-DAILY_WINDOW:])
    return {"dates": dates, "pct20": pct20, "pct60": pct60,
            "n_symbols": len(symbols),
            "latest20": pct20[-1] if pct20 else None,
            "latest60": pct60[-1] if pct60 else None,
            "source": f"kline.db 自算(watchlist 台股 {len(symbols)} 檔)"}


def foreign_oi(db_path: str = MACRO_DB) -> dict:
    """外資台指期淨未平倉 vs 加權指數(同日對齊,taiex 缺日留 None)。"""
    conn = _ro(db_path)
    rows = conn.execute(
        "SELECT date, net_oi FROM taifex_foreign_oi ORDER BY date DESC LIMIT ?",
        (DAILY_WINDOW,)).fetchall()
    rows.reverse()
    tx = dict(conn.execute(
        "SELECT date, close FROM taiex_daily WHERE date >= ?",
        (rows[0][0] if rows else "9999",)).fetchall())
    conn.close()
    return {"dates": [r[0] for r in rows],
            "net_oi": [r[1] for r in rows],
            "taiex": [tx.get(r[0]) for r in rows],
            "latest": rows[-1][1] if rows else None,
            "source": "期交所三大法人期貨未平倉(外資)+ TWSE FMTQIK"}


# ── 情緒 ──────────────────────────────────────────────────────────────────────
def vix_ratio(db_path: str = MACRO_DB) -> dict:
    """VIX/VIX3M 期限結構比值;>1.0 倒掛=近月恐慌。"""
    conn = _ro(db_path)
    rows = conn.execute(
        "SELECT date, close, vix3m FROM vix_daily "
        "WHERE vix3m IS NOT NULL AND vix3m > 0 ORDER BY date DESC LIMIT ?",
        (DAILY_WINDOW,)).fetchall()
    conn.close()
    rows.reverse()
    ratio = [round(c / v3, 3) for _, c, v3 in rows]
    return {"dates": [r[0] for r in rows], "values": ratio,
            "alert_line": 1.0,
            "latest": ratio[-1] if ratio else None,
            "source": "CBOE VIX / VIX3M(yfinance)"}


# ── 景氣 ──────────────────────────────────────────────────────────────────────
def sahm(db_path: str = MACRO_DB) -> dict:
    conn = _ro(db_path)
    rows = conn.execute(
        "SELECT month, value FROM sahm_monthly ORDER BY month DESC LIMIT ?",
        (MONTHLY_WINDOW,)).fetchall()
    conn.close()
    rows.reverse()
    return {"months": [r[0] for r in rows], "values": [r[1] for r in rows],
            "trigger": 0.5,
            "latest": rows[-1][1] if rows else None,
            "source": "FRED SAHMREALTIME(Sahm Rule 即時失業率指標)"}


def light(db_path: str = MACRO_DB) -> dict:
    conn = _ro(db_path)
    rows = conn.execute(
        "SELECT month, score, light FROM light_monthly ORDER BY month DESC LIMIT ?",
        (MONTHLY_WINDOW,)).fetchall()
    conn.close()
    rows.reverse()
    return {"months": [r[0] for r in rows], "scores": [r[1] for r in rows],
            "lights": [r[2] for r in rows],
            "latest": rows[-1][2] if rows else None,
            "source": "國發會景氣對策信號"}


BUILDERS = {
    "hy_oas": hy_oas, "t10y2y": t10y2y,
    "breadth": breadth, "foreign_oi": foreign_oi,
    "vix_ratio": vix_ratio,
    "sahm": sahm, "light": light,
}


def build_signals() -> tuple[dict, list[str]]:
    """回傳 (signals dict, errors);單項失敗記名不拖累整包。"""
    out, errors = {}, []
    for name, fn in BUILDERS.items():
        try:
            out[name] = fn()
        except Exception as e:                     # noqa: BLE001
            errors.append(f"signal {name}: {str(e)[:60]}")
    return out, errors
