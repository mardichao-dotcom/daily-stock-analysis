"""
import_kline.py — 把 /tmp/tv_daily_data.json 匯入 kline.db（累積式，INSERT OR REPLACE 覆寫）

用法：
    python3 src/import_kline.py [--json /tmp/tv_daily_data.json] [--db kline.db]
    python3 src/import_kline.py --list-quarantine
    python3 src/import_kline.py --approve SYMBOL:DATE [--db kline.db]   # 隔日核可覆寫
    python3 src/import_kline.py --approve-all

輸出：
    prints data_date (最後一根 bar 的日期) to stdout，供 shell 讀取

P0-C(2026-06-11 資料正確性修復):由 INSERT OR IGNORE 改為 INSERT OR REPLACE。
原因:收盤前抓到的半成品 bar 一旦入庫,IGNORE 讓之後收盤後重抓的「正確收盤值」被丟棄。

W1 數值 sanity 閘(2026-07-07 審計):tv_collect 取自 TV 私有 API,無合約保證——
過去所有護欄只驗「活性」,不驗「數值有效性」。本閘驗:
  ① 結構:high ≥ low ≥ 0、開收盤價非負、成交量非負
  ② 跳變:|close − prev_close| / prev_close > 30%(prev 以 DB 既有值優先錨定,
     整批平移的還原權息序列會在第一根就撞閘,不會靜默覆寫歷史)
  ③ 覆寫保護:same-date 既有 row 存在且新值差 >30% → 不直接 REPLACE
違規 bar 進 kline_quarantine 隔離區 + Discord 告警;既有正確歷史不被覆寫。
隔日人工核可(--approve)後才覆寫入 kline(如除權息、減資等合法跳變)。
"""
from __future__ import annotations
import argparse
import bisect
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)
DEFAULT_JSON = "/tmp/tv_daily_data.json"
DEFAULT_DB   = os.path.join(PROJECT_ROOT, "kline.db")
TZ_TAIPEI    = timezone(timedelta(hours=8))

SANITY_JUMP_PCT = 0.30          # 跳變/覆寫差異閾值(審計 W1 指定 30%)
STALE_LAG_DAYS = 2              # watchlist 檔落後市場 ≥N 交易日 → 落後清單告警

from src.kline_adjust import (classify_adjustment, apply_corporate_action,
                              ADJ_MIN_OVERLAP, ADJ_RATIO_TOL)


def _official(code, date_iso):
    """台股官方收盤(TWSE STOCK_DAY)——除權仲裁用;取不到回 None。"""
    try:
        from src.verify_kline import fetch_official_close
        return fetch_official_close(code, date_iso)
    except Exception:                                   # noqa: BLE001
        return None


def _market_dates(cur):
    """回 (tw_dates, us_dates) 各市場已觀測交易日 sorted list(判落後/卡住用)。"""
    tw = [r[0] for r in cur.execute(
        "SELECT DISTINCT date FROM kline WHERE symbol LIKE 'TWSE:%' "
        "OR symbol LIKE 'TPEX:%' ORDER BY date")]
    us = [r[0] for r in cur.execute(
        "SELECT DISTINCT date FROM kline WHERE symbol LIKE 'NASDAQ:%' "
        "OR symbol LIKE 'NYSE:%' OR symbol LIKE 'AMEX:%' ORDER BY date")]
    return tw, us


def _market_of(symbol, tw_dates, us_dates):
    return us_dates if symbol.split(":")[0] in ("NASDAQ", "NYSE", "AMEX") else tw_dates


def report_stale_symbols(cur, lag_days=STALE_LAG_DAYS):
    """watchlist 檔最新棒落後其市場 ≥lag_days 交易日 → [(symbol, max_date|None, lag)]。
    落後清單(task 3):只在真有落後時才回非空,與每日隔離噪音分開。"""
    from src.load_config import get_all_tw_symbols, get_all_global_symbols
    watch = set(get_all_tw_symbols()) | set(get_all_global_symbols())
    tw, us = _market_dates(cur)
    laggards = []
    for sym in sorted(watch):
        md = _market_of(sym, tw, us)
        if not md:
            continue
        row = cur.execute("SELECT MAX(date) FROM kline WHERE symbol=?", (sym,)).fetchone()
        smax = row[0] if row and row[0] else None
        if smax is None:
            laggards.append((sym, None, len(md)))
            continue
        lag = sum(1 for d in md if d > smax)
        if lag >= lag_days:
            laggards.append((sym, smax, lag))
    return laggards


def _ensure_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kline (
            symbol  TEXT NOT NULL,
            date    TEXT NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kline_quarantine (
            symbol  TEXT NOT NULL,
            date    TEXT NOT NULL,
            open    REAL, high REAL, low REAL, close REAL, volume REAL,
            reason  TEXT,
            quarantined_at TEXT,
            PRIMARY KEY (symbol, date)
        )
    """)


def sanity_reason(o, h, l, c, v, prev_close, existing_close) -> str | None:
    """W1 數值 sanity:回 None(通過)或違規原因。"""
    vals = [x for x in (o, h, l, c) if x is not None]
    if h is None or l is None or c is None:
        return "缺 OHLC 欄位"
    if l < 0 or any(x < 0 for x in vals):
        return f"負價格(low={l})"
    if h < l:
        return f"high({h}) < low({l})"
    if v is not None and v < 0:
        return f"負成交量({v})"
    if prev_close and prev_close > 0:
        jump = abs(c - prev_close) / prev_close
        if jump > SANITY_JUMP_PCT:
            return f"單日跳變 {jump * 100:.1f}%(prev_close={prev_close} → close={c})"
    if existing_close and existing_close > 0:
        diff = abs(c - existing_close) / existing_close
        if diff > SANITY_JUMP_PCT:
            return f"覆寫差異 {diff * 100:.1f}%(既有 close={existing_close} → 新值={c})"
    return None


def _discord(msg: str):
    if os.environ.get("IMPORT_KLINE_NO_ALERT") == "1":     # 測試環境不發
        return
    try:
        from src.daily_supervisor import _load_webhook, _send
        wh = _load_webhook()
        if wh:
            _send(wh, msg)
    except Exception as e:                                 # noqa: BLE001 — 告警失敗不擋匯入
        print(f"[import_kline] Discord 告警失敗: {e}", file=sys.stderr)


def _load_existing(cur, symbol) -> tuple[dict, list]:
    """該 symbol 既有 (date→close) 與排序日期表(跳變檢查的 DB 錨)。"""
    rows = cur.execute("SELECT date, close FROM kline WHERE symbol = ?", (symbol,)).fetchall()
    m = {d: c for d, c in rows}
    return m, sorted(m)


def _prev_close_db(existing: dict, dates_sorted: list, date_str: str):
    """DB 中該日期之前最近一筆 close(無則 None)。"""
    i = bisect.bisect_left(dates_sorted, date_str)
    return existing[dates_sorted[i - 1]] if i > 0 else None


def list_quarantine(db):
    conn = sqlite3.connect(db)
    _ensure_tables(conn.cursor())
    rows = conn.execute(
        "SELECT symbol, date, close, reason, quarantined_at FROM kline_quarantine "
        "ORDER BY quarantined_at DESC").fetchall()
    conn.close()
    if not rows:
        print("[quarantine] 隔離區為空")
        return
    print(f"[quarantine] {len(rows)} 筆待核可:")
    for s, d, c, r, at in rows:
        print(f"  {s} {d} close={c} | {r} | 隔離於 {at}")


def approve(db, target: str | None):
    """核可隔離 bar → REPLACE 入 kline 並移出隔離區。target='SYMBOL:DATE' 或 None(全部)。"""
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    _ensure_tables(cur)
    if target:
        sym, _, date = target.rpartition(":")
        # SYMBOL 本身含冒號(TWSE:2330)→ 允許 SYMBOL:DATE 完整寫法 TWSE:2330:2026-07-06
        rows = cur.execute("SELECT * FROM kline_quarantine WHERE symbol=? AND date=?",
                           (sym, date)).fetchall()
    else:
        rows = cur.execute("SELECT * FROM kline_quarantine").fetchall()
    if not rows:
        print("[quarantine] 無符合的隔離 bar")
        conn.close()
        return
    for r in rows:
        sym, date, o, h, l, c, v = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        cur.execute("INSERT OR REPLACE INTO kline VALUES (?,?,?,?,?,?,?)",
                    (sym, date, o, h, l, c, v))
        cur.execute("DELETE FROM kline_quarantine WHERE symbol=? AND date=?", (sym, date))
        print(f"[quarantine] ✅ 核可覆寫 {sym} {date} close={c}")
    conn.commit()
    conn.close()


def approve_split(db, symbol):
    """人工核可疑似除權(美股/官方源掛時用):以隔離區調整後值算 k、重調整段歷史、
    匯入隔離 bar、清隔離。重疊 ratio 不一致則拒絕(防誤洗)。"""
    import statistics
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    _ensure_tables(cur)
    qrows = cur.execute(
        "SELECT date,open,high,low,close,volume FROM kline_quarantine "
        "WHERE symbol=? ORDER BY date", (symbol,)).fetchall()
    if not qrows:
        print(f"[approve-split] {symbol} 無隔離 bar")
        conn.close()
        return
    existing = {d: c for d, c in
                cur.execute("SELECT date,close FROM kline WHERE symbol=?", (symbol,)).fetchall()}
    incoming = {r[0]: r[4] for r in qrows}
    overlap = sorted(set(incoming) & set(existing))
    ratios = [incoming[d] / existing[d] for d in overlap if existing[d] and existing[d] > 0]
    if len(ratios) < ADJ_MIN_OVERLAP or (max(ratios) / min(ratios) > ADJ_RATIO_TOL):
        print(f"[approve-split] ❌ {symbol} 重疊 ratio 不一致或不足"
              f"(overlap={len(ratios)}),拒絕以免誤洗。請人工查核。")
        conn.close()
        return
    k = statistics.median(ratios)
    n = apply_corporate_action(cur, symbol, k)
    for d, o, h, l, c, v in qrows:
        cur.execute("INSERT OR REPLACE INTO kline VALUES (?,?,?,?,?,?,?)",
                    (symbol, d, o, h, l, c, v))
    cur.execute("DELETE FROM kline_quarantine WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()
    print(f"[approve-split] ✅ {symbol} 重調 {n} 根歷史(k={k:.5f})"
          f" + 匯入 {len(qrows)} 根隔離 bar")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=DEFAULT_JSON)
    parser.add_argument("--db",   default=DEFAULT_DB)
    # P0-D 美股補跑:只匯入美股 bar,不可用美股 max date 覆寫 .data_date(會回退主跑的日期)
    parser.add_argument("--no-data-date", action="store_true", dest="no_data_date",
                        help="不寫入 .data_date(美股補跑用,沿用主跑的 data_date)")
    parser.add_argument("--list-quarantine", action="store_true")
    parser.add_argument("--approve", default=None, metavar="SYMBOL:DATE",
                        help="核可隔離 bar 覆寫入 kline(如 TWSE:2330:2026-07-06)")
    parser.add_argument("--approve-all", action="store_true")
    parser.add_argument("--approve-split", default=None, metavar="SYMBOL",
                        help="人工核可疑似除權:重調整段歷史+匯入隔離 bar(如 TWSE:6669)")
    args = parser.parse_args()

    if args.list_quarantine:
        return list_quarantine(args.db)
    if args.approve or args.approve_all:
        return approve(args.db, args.approve)
    if args.approve_split:
        return approve_split(args.db, args.approve_split)

    with open(args.json, encoding="utf-8") as f:
        data = json.load(f)

    conn = sqlite3.connect(args.db)
    cur  = conn.cursor()
    _ensure_tables(cur)

    results = data.get("results", {})
    date_file = os.path.join(PROJECT_ROOT, ".data_date")

    if not results:
        # 增量模式：所有 symbol 都跳過（已是最新），從 DB 讀最新日期
        print("[import_kline] results 為空（所有 symbol 已是最新），跳過匯入")
        row = cur.execute("SELECT MAX(date) FROM kline").fetchone()
        last_date = row[0] if row and row[0] else ""
        conn.close()
        print(f"[import_kline] data_date={last_date} (from DB)")
        if not args.no_data_date:
            with open(date_file, "w") as f:
                f.write(last_date)
        return

    # 幽靈 bar 防護(2026-07-04,停更 19 天事故):拒絕日期在「今天(台北)」之後的 bar。
    today_taipei = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")
    now_iso = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    inserted = 0
    rejected_future = []          # [(symbol, date_str)] 被擋下的未來 bar
    quarantined = []              # [(symbol, date_str, reason)] W1 sanity 閘
    adjusted = []                 # [(symbol, k, official)] 除權自動重調
    suspected = []                # [(symbol, k, reason)] 疑似除權待人工(美股/官方掛)
    tw_dates, us_dates = _market_dates(cur)
    last_date = ""
    for symbol, payload in results.items():
        existing, dates_sorted = _load_existing(cur, symbol)
        bars = sorted(payload["bars"], key=lambda b: b["time"])

        # ── 除權/分割 偵測(在隔離前;三關全過才動)──────────────────
        if existing:
            incoming_close = {}
            for b in bars:
                ds = datetime.utcfromtimestamp(b["time"]).strftime("%Y-%m-%d")
                if b.get("close") is not None:
                    incoming_close[ds] = b["close"]
            md = _market_of(symbol, tw_dates, us_dates)
            verdict = classify_adjustment(symbol, incoming_close, existing, md,
                                          official_fetch=_official)
            if verdict["action"] == "auto":
                apply_corporate_action(cur, symbol, verdict["k"])
                cur.execute("DELETE FROM kline_quarantine WHERE symbol=?", (symbol,))
                conn.commit()
                existing, dates_sorted = _load_existing(cur, symbol)   # 重載已重調的錨
                adjusted.append((symbol, verdict["k"], verdict.get("official")))
            elif verdict["action"] == "manual":
                suspected.append((symbol, verdict["k"], verdict.get("reason", "")))

        last_batch_close = None                       # 無 DB 錨(新 symbol 首匯)時的批次連續性
        for bar in bars:
            dt       = datetime.utcfromtimestamp(bar["time"])
            date_str = dt.strftime("%Y-%m-%d")
            if date_str > today_taipei:
                rejected_future.append((symbol, date_str))
                continue
            o, h, l = bar.get("open"), bar.get("high"), bar.get("low")
            c, v = bar.get("close"), bar.get("volume")
            # W1:跳變錨定 —— DB 既有 prev 優先(平移序列第一根就撞閘),無則批次連續
            prev = _prev_close_db(existing, dates_sorted, date_str)
            if prev is None:
                prev = last_batch_close
            reason = sanity_reason(o, h, l, c, v, prev, existing.get(date_str))
            if reason:
                quarantined.append((symbol, date_str, reason))
                cur.execute(
                    "INSERT OR REPLACE INTO kline_quarantine VALUES (?,?,?,?,?,?,?,?,?)",
                    (symbol, date_str, o, h, l, c, v, reason, now_iso))
                continue                              # 不入 kline、不推進 data_date
            cur.execute(
                # P0-C:REPLACE 覆寫——收盤後重抓的正確值覆蓋盤中半成品
                "INSERT OR REPLACE INTO kline VALUES (?,?,?,?,?,?,?)",
                (symbol, date_str, o, h, l, c, v))
            last_batch_close = c
            if date_str > last_date:
                last_date = date_str
            inserted += 1

    conn.commit()
    # 保底:整批被隔離/擋下(inserted=0)時,data_date 退回 DB 既有最大日,不寫空值
    if not last_date:
        row = conn.execute("SELECT MAX(date) FROM kline").fetchone()
        last_date = row[0] if row and row[0] else ""
    # 落後清單(task 3):匯入後的最終狀態(除權已重調的檔此時已 current)
    laggards = report_stale_symbols(cur) if not args.no_data_date else []
    conn.close()

    print(f"[import_kline] {inserted} rows → {args.db}")
    if rejected_future:
        print(f"[import_kline] ⚠️ 擋下 {len(rejected_future)} 根未來日期(幽靈)bar,"
              f"未入庫:{rejected_future[:5]}"
              + ("..." if len(rejected_future) > 5 else ""))
    if quarantined:
        head = "; ".join(f"{s} {d}({r})" for s, d, r in quarantined[:5])
        print(f"[import_kline] 🔶 W1 sanity 閘隔離 {len(quarantined)} 根 bar(未入庫):{head}"
              + ("..." if len(quarantined) > 5 else ""))
        _discord(f"🔶 [K 線隔離] import_kline 數值 sanity 閘攔下 {len(quarantined)} 根 bar,"
                 f"已入隔離區、未覆寫既有資料:\n"
                 + "\n".join(f"• {s} {d}:{r}" for s, d, r in quarantined[:8])
                 + ("\n…" if len(quarantined) > 8 else "")
                 + "\n核可覆寫:python3 src/import_kline.py --list-quarantine / --approve SYMBOL:DATE")

    # 除權自動重調(台股三關全過)——與「隔離」告警明確區分
    if adjusted:
        head = "; ".join(f"{s} k={k:.4f}" for s, k, _ in adjusted[:6])
        print(f"[import_kline] 🔧 除權自動重調 {len(adjusted)} 檔:{head}")
        _discord("🔧 [K線除權-自動重調] 偵測到除權/分割,已把整段歷史重調到調整後基準"
                 "(價×k、量÷k),並補回被隔離的新棒:\n"
                 + "\n".join(f"• {s}:k={k:.5f}"
                             + (f"(TWSE 官方 {off} 背書)" if off else "")
                             for s, k, off in adjusted[:8]))

    # 疑似除權待人工(美股無官方仲裁 / 台股官方源掛)——不自動動資料
    if suspected:
        head = "; ".join(f"{s} k≈{k:.4f}" for s, k, _ in suspected[:6])
        print(f"[import_kline] 🔶 疑似除權待人工 {len(suspected)} 檔:{head}")
        _discord("🔶 [K線疑似除權-待人工確認] 以下檔卡住且呈等比重調,疑似除權,但無權威仲裁"
                 "(美股/官方源取不到),未自動處理:\n"
                 + "\n".join(f"• {s}:k≈{k:.5f}({r})" for s, k, r in suspected[:8])
                 + "\n確認後放行:python3 src/import_kline.py --approve-split SYMBOL")

    # 落後清單(task 3):只在真有檔落後 ≥2 交易日時才發,措辭「需要處理」
    if laggards:
        def _fmt(sym, mx, lag):
            return f"• {sym}:落後 {lag} 交易日" + (f"(最新 {mx})" if mx else "(完全無資料)")
        print(f"[import_kline] ⚠️ 落後清單 {len(laggards)} 檔需處理:"
              + "; ".join(f"{s}({lag}d)" for s, _, lag in laggards[:8]))
        _discord("⚠️ [K線落後-需要處理] 以下 watchlist 個股 K 線落後大盤 ≥2 交易日,"
                 "請查明原因(除權未重調/採集失敗/已下市):\n"
                 + "\n".join(_fmt(s, mx, lag) for s, mx, lag in laggards[:12])
                 + ("\n…" if len(laggards) > 12 else ""))

    print(f"[import_kline] data_date={last_date}")

    if not args.no_data_date:
        with open(date_file, "w") as f:
            f.write(last_date)


if __name__ == "__main__":
    main()
