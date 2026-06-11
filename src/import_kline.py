"""
import_kline.py — 把 /tmp/tv_daily_data.json 匯入 kline.db（累積式，INSERT OR REPLACE 覆寫）

用法：
    python3 src/import_kline.py [--json /tmp/tv_daily_data.json] [--db kline.db]

輸出：
    prints data_date (最後一根 bar 的日期) to stdout，供 shell 讀取

P0-C(2026-06-11 資料正確性修復):由 INSERT OR IGNORE 改為 INSERT OR REPLACE。
原因:收盤前抓到的半成品 bar(如 19:12 抓哥本哈根 6/11、美股盤中)一旦入庫,
IGNORE 讓之後收盤後重抓的「正確收盤值」被丟棄 → 髒資料永久殘留。
改 REPLACE 後,搭配 tv_collect 對非台股「永遠重抓最近 3 根」,半成品隔天自動被覆寫修正。
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
DEFAULT_JSON = "/tmp/tv_daily_data.json"
DEFAULT_DB   = os.path.join(PROJECT_ROOT, "kline.db")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=DEFAULT_JSON)
    parser.add_argument("--db",   default=DEFAULT_DB)
    # P0-D 美股補跑:只匯入美股 bar,不可用美股 max date 覆寫 .data_date(會回退主跑的日期)
    parser.add_argument("--no-data-date", action="store_true", dest="no_data_date",
                        help="不寫入 .data_date(美股補跑用,沿用主跑的 data_date)")
    args = parser.parse_args()

    with open(args.json, encoding="utf-8") as f:
        data = json.load(f)

    conn = sqlite3.connect(args.db)
    cur  = conn.cursor()
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

    inserted = 0
    last_date = ""
    for symbol, payload in results.items():
        for bar in payload["bars"]:
            dt       = datetime.utcfromtimestamp(bar["time"])
            date_str = dt.strftime("%Y-%m-%d")
            cur.execute(
                # P0-C:REPLACE 覆寫——收盤後重抓的正確值覆蓋盤中半成品
                "INSERT OR REPLACE INTO kline VALUES (?,?,?,?,?,?,?)",
                (symbol, date_str,
                 bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"])
            )
            if date_str > last_date:
                last_date = date_str
            inserted += 1

    conn.commit()
    conn.close()

    print(f"[import_kline] {inserted} rows → {args.db}")
    print(f"[import_kline] data_date={last_date}")

    if not args.no_data_date:
        with open(date_file, "w") as f:
            f.write(last_date)


if __name__ == "__main__":
    main()
