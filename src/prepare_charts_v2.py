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

        # 2. 跌破判定(v2.2 event-based:Day1=yesterday + Day2=today)
        yesterday_k = normalized[-2] if len(normalized) >= 2 else None
        if standing.evaluate_breakdown(normalized[-1], yesterday_k, given_price):
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


def write_index(outdir: Path, date: str,
                  symbols: list[str],
                  status_map: dict[str, dict] | None = None) -> Path:
    """寫 _index.json。

    新格式(v2.2):per-symbol status,給 render_v2 / render_watchlist_v2 判斷
        - ready              chart JSON 已產出
        - waiting_us_close   symbol 有歷史 kline 但無當日(台北跑時美股未收盤)
        - missing            symbol 在 kline.db 完全沒資料(setup 問題)

    舊格式 `stocks: [...]` 並列保留,供既有未升級 consumer 用。
    """
    day_dir = outdir / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "_index.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "date":   date,
            "stocks": [_safe_filename(s) for s in symbols],   # 舊格式(只列 ready)
            "symbols": status_map or {},                       # 新格式(per-symbol status)
            "version": "2.2",
            "generated_at": datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        }, f, ensure_ascii=False, indent=2)
    return path


def _classify_exchange(symbol: str) -> str:
    """從 symbol prefix 推斷交易所類別。"""
    if symbol.startswith(("TWSE:", "TPEX:")):
        return "TW"
    if symbol.startswith(("NASDAQ:", "NYSE:")):
        return "US"
    if symbol.startswith("TSE:"):
        return "JP"
    if symbol.startswith("OMXCOP:"):
        return "DK"
    return "INTL"


def _has_kline_any(conn: sqlite3.Connection, symbol: str) -> bool:
    """檢查 symbol 在 kline.db 是否有任何歷史資料。"""
    cur = conn.execute("SELECT 1 FROM kline WHERE symbol = ? LIMIT 1", (symbol,))
    return cur.fetchone() is not None


def _last_kline_date(conn: sqlite3.Connection, symbol: str) -> str | None:
    cur = conn.execute(
        "SELECT MAX(date) FROM kline WHERE symbol = ?", (symbol,)
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


# ── 主流程 ────────────────────────────────────────────────────────────────────

def filter_sab_stocks(filtered_result: dict) -> dict:
    """從 filtered_result_v2["stocks"] 取出 S/A/B 級個股。"""
    return {
        sym: entry
        for sym, entry in filtered_result.get("stocks", {}).items()
        if entry.get("grade") in SAB_GRADES
    }


def collect_all_watchlist_symbols(watchlist: dict) -> list[tuple[str, dict]]:
    """從 watchlist.json 收集全部 symbols(台股板塊 + 國際族群)。
    回傳 [(symbol, minimal_entry), ...] — minimal_entry 含 name/sector,沒 key_prices/score。
    """
    items: list[tuple[str, dict]] = []
    for sector_name, sec in watchlist.get("台股板塊", {}).items():
        for m in sec.get("成員", []):
            items.append((m["code"], {"name": m["name"], "sector": sector_name}))
    for group_name, grp in watchlist.get("國際族群", {}).items():
        for m in grp.get("成員", []):
            items.append((m["code"], {"name": m["name"], "sector": group_name}))
    return items


def run(
    *,
    date:        str,
    filtered_result: dict,
    conn_kline:  sqlite3.Connection,
    conn_etf:    sqlite3.Connection | None,
    outdir:      Path,
    all_watchlist: dict | None = None,    # 給 watchlist_v2 用:全 87 檔
) -> dict:
    """產 chart JSON。預設只產 S/A/B,給 all_watchlist=watchlist.json 內容則產全部。
    全模式下:有 filtered_result entry 就用(含 key_prices_snapshot),沒則用 minimal。
    """
    if all_watchlist is None:
        targets = list(filter_sab_stocks(filtered_result).items())
    else:
        sab_index = filtered_result.get("stocks", {}) if filtered_result else {}
        # 全 watchlist + 補上 filtered_result 內的(若 watchlist 跟它有 symbol 差異)
        watchlist_items = collect_all_watchlist_symbols(all_watchlist)
        seen = set()
        targets = []
        for sym, minimal in watchlist_items:
            if sym in seen:
                continue
            seen.add(sym)
            # 優先用 filtered_result entry(含 key_prices_snapshot / score),
            # 回退到 minimal(只 name/sector)
            targets.append((sym, sab_index.get(sym, minimal)))

    written: list[str] = []
    skipped: list[str] = []
    status_map: dict[str, dict] = {}

    for symbol, entry in targets:
        chart = build_chart_for_stock(
            symbol, entry, conn_kline, conn_etf, date,
        )
        safe = _safe_filename(symbol)
        meta = {
            "symbol":   symbol,
            "name":     entry.get("name", ""),
            "sector":   entry.get("sector", ""),
            "exchange": _classify_exchange(symbol),
        }
        if chart is not None:
            write_chart(outdir, date, symbol, chart)
            written.append(symbol)
            status_map[safe] = {**meta, "status": "ready"}
        else:
            skipped.append(symbol)
            if _has_kline_any(conn_kline, symbol):
                # 有歷史 K 線但無當日 → 等資料(典型:台北跑時美股未收盤)
                status_map[safe] = {
                    **meta,
                    "status":              "waiting_us_close",
                    "last_available_date": _last_kline_date(conn_kline, symbol),
                }
            else:
                # 完全沒資料 — setup 問題,不該發生於 watchlist 個股
                status_map[safe] = {**meta, "status": "missing"}

    write_index(outdir, date, written, status_map)

    return {
        "date":      date,
        "sab_total": len(filter_sab_stocks(filtered_result)) if filtered_result else 0,
        "written":   written,
        "skipped":   skipped,
        "status_map": status_map,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stage 8 chart producer(W2.4)")
    parser.add_argument("--date",   required=True)
    parser.add_argument("--kline",  default=str(PROJECT_ROOT / "kline.db"))
    parser.add_argument("--etf",    default=os.path.expanduser("~/ETF追蹤/etf_operations.db"))
    parser.add_argument("--result", default=str(PROJECT_ROOT / "filtered_result_v2.json"))
    parser.add_argument("--outdir", default=str(PROJECT_ROOT / "docs" / "data" / "v2"))
    parser.add_argument("--all-watchlist", action="store_true",
                         help="產全 watchlist 87 檔 chart JSON(給 watchlist_v2.html)")
    parser.add_argument("--watchlist", default=str(PROJECT_ROOT / "config" / "watchlist.json"))
    args = parser.parse_args()

    with open(args.result, encoding="utf-8") as f:
        filtered_result = json.load(f)

    all_watchlist = None
    if args.all_watchlist:
        with open(args.watchlist, encoding="utf-8") as f:
            all_watchlist = json.load(f)

    conn_kline = sqlite3.connect(args.kline)
    conn_etf   = sqlite3.connect(args.etf) if os.path.exists(args.etf) else None

    try:
        stats = run(
            date=args.date,
            filtered_result=filtered_result,
            conn_kline=conn_kline,
            conn_etf=conn_etf,
            outdir=Path(args.outdir),
            all_watchlist=all_watchlist,
        )
    finally:
        conn_kline.close()
        if conn_etf is not None:
            conn_etf.close()

    mode = "全 watchlist" if args.all_watchlist else "S/A/B 級"
    print(f"✅ {len(stats['written'])} charts written ({mode}) → {args.outdir}/{args.date}")
    if stats["skipped"]:
        print(f"⚠️ {len(stats['skipped'])} skipped(無 K 線資料): {stats['skipped'][:10]}"
              + ("..." if len(stats["skipped"]) > 10 else ""))


if __name__ == "__main__":
    main()
