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
    args = parser.parse_args()

    if args.list_quarantine:
        return list_quarantine(args.db)
    if args.approve or args.approve_all:
        return approve(args.db, args.approve)

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
    last_date = ""
    for symbol, payload in results.items():
        existing, dates_sorted = _load_existing(cur, symbol)
        last_batch_close = None                       # 無 DB 錨(新 symbol 首匯)時的批次連續性
        bars = sorted(payload["bars"], key=lambda b: b["time"])
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
    print(f"[import_kline] data_date={last_date}")

    if not args.no_data_date:
        with open(date_file, "w") as f:
            f.write(last_date)


if __name__ == "__main__":
    main()
