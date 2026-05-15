"""
import_kline.py — 把 /tmp/tv_daily_data.json 匯入 kline.db（累積式，INSERT OR IGNORE）

用法：
    python3 src/import_kline.py [--json /tmp/tv_daily_data.json] [--db kline.db]

輸出：
    prints data_date (最後一根 bar 的日期) to stdout，供 shell 讀取
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

    inserted = 0
    last_date = ""
    for symbol, payload in data["results"].items():
        for bar in payload["bars"]:
            dt       = datetime.utcfromtimestamp(bar["time"])
            date_str = dt.strftime("%Y-%m-%d")
            cur.execute(
                "INSERT OR IGNORE INTO kline VALUES (?,?,?,?,?,?,?)",
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

    # 寫 data_date 到暫存檔，供 shell 讀取
    date_file = os.path.join(PROJECT_ROOT, ".data_date")
    with open(date_file, "w") as f:
        f.write(last_date)


if __name__ == "__main__":
    main()
