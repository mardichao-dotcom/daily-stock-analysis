"""
prepare_charts.py — 把每檔 S/A 級個股（+ 額外測試股）整理成圖表 JSON。

輸出: data/{data_date}/{EXCHANGE}_{CODE}.json

Usage:
    python3 src/prepare_charts.py [--date YYYY-MM-DD] [--kline PATH] [--etf PATH]
    python3 src/prepare_charts.py --extra TWSE:3443   # 強制加入非 S/A 股（測試用）
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.load_config import get_name, get_sector_of, symbol_to_code

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
BUY_ACTIONS  = {"加碼", "建倉"}
SELL_ACTIONS = {"減碼", "清倉"}


# ── helpers ───────────────────────────────────────────────────────────────────

def symbol_to_filename(symbol):
    """'TPEX:6223' → 'TPEX_6223'"""
    return symbol.replace(":", "_")


def get_ohlcv(kline_cur, symbol):
    """Return list of {time, open, high, low, close, volume}, ordered ascending."""
    kline_cur.execute(
        "SELECT date, open, high, low, close, volume FROM kline "
        "WHERE symbol=? ORDER BY date ASC",
        (symbol,)
    )
    rows = kline_cur.fetchall()
    return [
        {"time": r[0], "open": r[1], "high": r[2],
         "low": r[3], "close": r[4], "volume": r[5]}
        for r in rows
    ]


def get_etf_events(etf_cur, code, valid_dates):
    """
    Fetch ALL ETF events for this stock code.
    Only keep events whose date falls within valid_dates (the 70-bar date set).
    Returns merged markers list.
    """
    etf_cur.execute(
        "SELECT etf, 日期, 名稱, 動作, 張數 FROM operations "
        "WHERE 代號=? ORDER BY 日期 ASC",
        (code,)
    )
    rows = etf_cur.fetchall()

    # Group by (date, action) — 同日同動作才合併
    grouped = {}  # (date, action) → list of {etf, action, shares}
    for etf, date, name, action, shares in rows:
        if date not in valid_dates:
            continue
        if action not in BUY_ACTIONS and action not in SELL_ACTIONS:
            continue
        key = (date, action)
        grouped.setdefault(key, []).append(
            {"etf": etf, "action": action, "shares": shares}
        )

    # Build markers, sorted by (date, action)
    markers = []
    for (date, action), detail in sorted(grouped.items()):
        direction = "buy" if action in BUY_ACTIONS else "sell"
        etf_count = len(set(d["etf"] for d in detail))
        is_consensus = etf_count >= 2

        if is_consensus:
            summary = f"{etf_count} 檔{action}"
        else:
            summary = f"{detail[0]['etf']} {action}"

        markers.append({
            "time":         date,
            "action":       action,
            "direction":    direction,
            "is_consensus": is_consensus,
            "summary":      summary,
            "detail":       detail,
        })

    return markers


def build_json(symbol, grade, kline_cur, etf_cur):
    ohlcv = get_ohlcv(kline_cur, symbol)
    if not ohlcv:
        return None

    valid_dates = {bar["time"] for bar in ohlcv}
    code = symbol_to_code(symbol)
    markers = get_etf_events(etf_cur, code, valid_dates)

    return {
        "code":        symbol,
        "name":        get_name(symbol),
        "sector":      get_sector_of(symbol) or "",
        "grade":       grade,
        "ohlcv":       ohlcv,
        "etf_markers": markers,
        "key_prices":  [],
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",   default="2026-05-13")
    parser.add_argument("--kline",  default=os.path.join(PROJECT_ROOT, "test", "test_kline.db"))
    parser.add_argument("--etf",    default=os.path.expanduser("~/ETF追蹤/etf_operations.db"))
    parser.add_argument("--result", default=os.path.join(PROJECT_ROOT, "output", "filtered_result.json"))
    parser.add_argument("--outdir", default=os.path.join(PROJECT_ROOT, "docs", "data"))
    parser.add_argument("--extra",  nargs="*", default=["TWSE:3443"],
                        help="Extra symbols to include regardless of grade (for ETF marker testing)")
    args = parser.parse_args()

    # Read filtered_result.json → S/A symbols
    with open(args.result, encoding="utf-8") as f:
        result = json.load(f)

    targets = {}  # symbol → grade
    for sym, d in result["個股結果"].items():
        if d["grade"] in ("S級", "A級"):
            targets[sym] = d["grade"]

    # Add extra test symbols
    for sym in (args.extra or []):
        if sym not in targets:
            d = result["個股結果"].get(sym, {})
            targets[sym] = d.get("grade", "測試")

    out_dir = os.path.join(args.outdir, args.date)
    os.makedirs(out_dir, exist_ok=True)

    kline_conn = sqlite3.connect(args.kline)
    k_cur = kline_conn.cursor()
    etf_conn = sqlite3.connect(args.etf)
    e_cur = etf_conn.cursor()

    written = []
    for symbol, grade in sorted(targets.items()):
        data = build_json(symbol, grade, k_cur, e_cur)
        if data is None:
            print(f"[SKIP] {symbol}: no kline data")
            continue
        fname = symbol_to_filename(symbol) + ".json"
        fpath = os.path.join(out_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        n_markers = len(data["etf_markers"])
        print(f"[OK] {symbol} ({grade}) → {fname}  "
              f"{len(data['ohlcv'])} bars, {n_markers} ETF markers")
        written.append(fname)

    kline_conn.close()
    etf_conn.close()
    print(f"\n[done] {len(written)} files → {out_dir}/")


if __name__ == "__main__":
    main()
