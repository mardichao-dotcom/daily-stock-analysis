"""
prepare_charts_v2.py — 產出 S/A/B 級個股的 chart JSON(W2.4)

完全新檔,不 import v1 src/prepare_charts.py(v1 凍結期)。

輸入:
  filtered_result_v2.json (run_filters_v2 產出) — 含 stock entries + key_prices_snapshot
  kline.db                 — 5A 既有,只讀
  ~/ETF追蹤/etf_operations.db — 5A 既有,只讀

輸出:
  docs/data/v2/{date}/_index.json          ← {"stocks": [...]} for chart.js discovery
  docs/data/v2/{date}/{TWSE_2330}.json    ← per-stock chart data

CLI:
  python3 src/prepare_charts_v2.py --date 2026-05-26

W2.4 設計決策(per W2.4 review):
  - 只輸出 S/A/B 級(spec §5.1 嚴格)
  - CHART_LOOKBACK_DAYS = 180(獨立常數,scoring 用 KLINE_LOOKBACK=100)
  - MA arrays 算好放 JSON(前端不重算,單一真相)
  - events 從 kline_history **重算**(不從 standing_state 撈,得歷史完整)
  - 顏色/線型分離:JSON 放 category + color(資料),chart.js 查 visual.json(樣式)
  - per-stock file + _index.json(lazy load 友善)
  - market: "TW" 預埋(Stage 9 美股)
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.triggers import standing


CHART_LOOKBACK_DAYS = 180       # ≈ 6 個月,獨立於 scoring 的 KLINE_LOOKBACK=100
MA_WINDOWS          = (20, 60, 90)
SAB_GRADES          = {"S", "A", "B"}

TZ_TAIPEI = timezone(timedelta(hours=8))


# ── K 線載入 ─────────────────────────────────────────────────────────────────

def load_chart_kline(conn: sqlite3.Connection, symbol: str, date: str,
                      lookback: int = CHART_LOOKBACK_DAYS) -> list[dict]:
    """載入 chart 用的 OHLCV(預設 180 個交易日,升冪)。"""
    cur = conn.execute(
        "SELECT date, open, high, low, close, volume FROM kline "
        "WHERE symbol = ? AND date <= ? "
        "ORDER BY date DESC LIMIT ?",
        (symbol, date, lookback),
    )
    rows = cur.fetchall()
    rows.reverse()   # asc
    return [
        {"time": r[0], "open": r[1], "high": r[2], "low": r[3],
         "close": r[4], "volume": r[5]}
        for r in rows
    ]


def load_etf_events(conn: sqlite3.Connection, symbol: str,
                     start_date: str, end_date: str) -> list[dict]:
    """載入該 symbol 在日期範圍內的 ETF 操作 events。"""
    code = symbol.split(":")[-1]   # strip exchange prefix
    cur = conn.execute(
        "SELECT etf, 日期, 動作, 張數 FROM operations "
        "WHERE 代號 = ? AND 日期 >= ? AND 日期 <= ? "
        "ORDER BY 日期 ASC",
        (code, start_date, end_date),
    )
    return [
        {"time": r[1], "etf": r[0], "action": r[2], "shares": r[3]}
        for r in cur.fetchall()
    ]


# ── MA arrays 計算 ────────────────────────────────────────────────────────────

def compute_ma_arrays(kline: list[dict],
                       windows: tuple[int, ...] = MA_WINDOWS) -> dict:
    """對每個 MA window,算出跟 ohlcv 同長度的 array(前期 None,暖機未完成)。"""
    n = len(kline)
    closes = [bar["close"] for bar in kline]
    result = {}
    for w in windows:
        arr: list[float | None] = [None] * n
        for i in range(n):
            if i + 1 >= w:   # 第 (w-1) 索引開始(累計 w 個 closes)
                window_slice = closes[i + 1 - w : i + 1]
                arr[i] = sum(window_slice) / w
        result[f"ma_{w}"] = arr
    return result


# ── 事件重算(W2.4 核心:不從 DB 撈,純從 kline 重跑狀態機)──────────────────

def replay_events_for_given_price(
    kline_history: list[dict],
    given_price: float,
    category: str,
    price_str: str,
) -> list[dict]:
    """對單一條 line/area 重跑 180 天狀態機,收集所有 standing + breakdown events。

    跟 standing_state DB 完全解耦:用今天的 key_price 對過去 K 線重算。
    這是「用今天的關鍵價,看過去 180 天這條線被站穩 / 跌破幾次」的歷史驗證。
    """
    events: list[dict] = []
    prev_state: dict | None = None

    for i in range(len(kline_history)):
        sub_history = kline_history[:i + 1]
        today_date  = kline_history[i]["time"]

        # 把 "time" 翻譯成 "date"(standing 內部用 "date" key,本檔 chart 用 "time")
        # standing 需要 K bar 含 date / open / high / low / close
        normalized = [
            {"date":  b["time"], "open": b["open"], "high": b["high"],
             "low":   b["low"],  "close": b["close"]}
            for b in sub_history
        ]

        # 1. 站穩判定
        new_state, _ = standing.evaluate_standing(
            normalized, given_price, prev_state, today_date,
        )
        if new_state["state"] == standing.STANDING:
            events.append({
                "time":     today_date,
                "type":     "standing",
                "category": category,
                "price":    price_str,
            })

        # 2. 跌破判定(獨立函式,用 prev_state)
        if standing.evaluate_breakdown(normalized[-1], given_price, prev_state):
            events.append({
                "time":     today_date,
                "type":     "breakdown",
                "category": category,
                "price":    price_str,
            })

        prev_state = new_state

    return events


def replay_all_events(kline_history: list[dict],
                       lines: list[dict],
                       areas: list[dict]) -> list[dict]:
    """對所有 line + area 跑重算,合併 events 並按日期排序。"""
    all_events: list[dict] = []

    for line in lines:
        try:
            gp = float(line["price"])
        except (ValueError, TypeError):
            continue
        all_events.extend(replay_events_for_given_price(
            kline_history, gp, line["category"], line["price"],
        ))

    for area in areas:
        try:
            low  = float(area["low"])
            high = float(area["high"])
        except (ValueError, TypeError):
            continue
        gp = (low + high) / 2
        price_str = f"{area['low']}-{area['high']}"
        all_events.extend(replay_events_for_given_price(
            kline_history, gp, area["category"], price_str,
        ))

    all_events.sort(key=lambda e: (e["time"], e["type"]))
    return all_events


# ── 主 builder ───────────────────────────────────────────────────────────────

def build_chart_for_stock(
    symbol:     str,
    stock_entry: dict,           # filtered_result_v2 stocks[symbol] (含 name/sector)
    conn_kline: sqlite3.Connection,
    conn_etf:   sqlite3.Connection | None,
    date:       str,
) -> dict | None:
    """產生單一個股的 chart JSON。回 None 代表沒 K 線資料 → skip。

    name / sector 直接從 stock_entry 讀(2026-05-31 改進:
    run_filters_v2 已把這兩欄寫進 stocks entry)。"""
    kline = load_chart_kline(conn_kline, symbol, date)
    if not kline or kline[-1]["time"] != date:
        return None

    # ETF events(若 conn_etf 存在)
    start_date = kline[0]["time"]
    etf_events = (load_etf_events(conn_etf, symbol, start_date, date)
                  if conn_etf is not None else [])

    # MA arrays
    ma = compute_ma_arrays(kline)

    # key_prices snapshot(從 filtered_result_v2 抄,保證 chart 跟 score 一致)
    kp = stock_entry.get("key_prices_snapshot", {"lines": [], "areas": []})

    # events 重算(W2.4 review 確認方法)
    events = replay_all_events(kline, kp.get("lines", []), kp.get("areas", []))

    # market 預埋(Stage 9 美股)
    market = "TW" if symbol.startswith(("TWSE:", "TPEX:")) else "INTL"

    # name / sector 從 stocks entry 讀(run_filters_v2 已寫入)
    name   = stock_entry.get("name", "")
    sector = stock_entry.get("sector", "")

    return {
        "code":      symbol.split(":")[-1],
        "symbol":    symbol,
        "name":      name,
        "sector":    sector,
        "market":    market,
        "data_date": date,
        "version":   "2.1",
        "ohlcv":     kline,
        "ma":        ma,
        "etf_events": etf_events,
        "key_prices": kp,
        "events":    events,
    }


# ── 輸出 ──────────────────────────────────────────────────────────────────────

def _safe_filename(symbol: str) -> str:
    """TWSE:2330 → TWSE_2330(filesystem-safe + 跟 v1 命名一致)"""
    return symbol.replace(":", "_")


def write_chart(outdir: Path, date: str, symbol: str, chart_data: dict) -> Path:
    day_dir = outdir / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{_safe_filename(symbol)}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(chart_data, f, ensure_ascii=False, indent=2)
    return path


def write_index(outdir: Path, date: str, symbols: list[str]) -> Path:
    day_dir = outdir / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "_index.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "date":   date,
            "stocks": [_safe_filename(s) for s in symbols],
            "version": "2.1",
            "generated_at": datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        }, f, ensure_ascii=False, indent=2)
    return path


# ── 主流程 ────────────────────────────────────────────────────────────────────

def filter_sab_stocks(filtered_result: dict) -> dict:
    """從 filtered_result_v2["stocks"] 取出 S/A/B 級個股。"""
    return {
        sym: entry
        for sym, entry in filtered_result.get("stocks", {}).items()
        if entry.get("grade") in SAB_GRADES
    }


def run(
    *,
    date:        str,
    filtered_result: dict,
    conn_kline:  sqlite3.Connection,
    conn_etf:    sqlite3.Connection | None,
    outdir:      Path,
) -> dict:
    """產生所有 S/A/B 個股的 chart JSON。回傳統計 dict。
    (2026-05-31 簡化:不再需要 watchlist 參數,name/sector 從 stocks entry 來)"""
    sab = filter_sab_stocks(filtered_result)

    written: list[str] = []
    skipped: list[str] = []

    for symbol, entry in sab.items():
        chart = build_chart_for_stock(
            symbol, entry, conn_kline, conn_etf, date,
        )
        if chart is None:
            skipped.append(symbol)
            continue
        write_chart(outdir, date, symbol, chart)
        written.append(symbol)

    write_index(outdir, date, written)

    return {
        "date":    date,
        "sab_total": len(sab),
        "written": written,
        "skipped": skipped,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stage 8 chart producer(W2.4)")
    parser.add_argument("--date",   required=True)
    parser.add_argument("--kline",  default=str(PROJECT_ROOT / "kline.db"))
    parser.add_argument("--etf",    default=os.path.expanduser("~/ETF追蹤/etf_operations.db"))
    parser.add_argument("--result", default=str(PROJECT_ROOT / "filtered_result_v2.json"))
    parser.add_argument("--outdir", default=str(PROJECT_ROOT / "docs" / "data" / "v2"))
    args = parser.parse_args()

    with open(args.result, encoding="utf-8") as f:
        filtered_result = json.load(f)

    conn_kline = sqlite3.connect(args.kline)
    conn_etf   = sqlite3.connect(args.etf) if os.path.exists(args.etf) else None

    try:
        stats = run(
            date=args.date,
            filtered_result=filtered_result,
            conn_kline=conn_kline,
            conn_etf=conn_etf,
            outdir=Path(args.outdir),
        )
    finally:
        conn_kline.close()
        if conn_etf is not None:
            conn_etf.close()

    print(f"✅ {len(stats['written'])} charts written → {args.outdir}/{args.date}")
    if stats["skipped"]:
        print(f"⚠️ {len(stats['skipped'])} skipped(無 K 線資料): {stats['skipped']}")


if __name__ == "__main__":
    main()
