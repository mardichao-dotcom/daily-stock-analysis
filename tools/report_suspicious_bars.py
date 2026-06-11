"""
report_suspicious_bars.py — 列出 kline.db 中「半成品嫌疑」的最後一根 bar(P0-C)

唯讀。判定:某 symbol 最後一根 bar 所屬交易所場次,在指定台北時刻時尚未收盤
→ 該 bar 可能是收盤前抓進來的盤中半成品(見 src/exchange_hours.py)。

用法:
    python3 tools/report_suspicious_bars.py                       # 以現在時刻判定
    python3 tools/report_suspicious_bars.py --at "2026-06-11 19:12"
    python3 tools/report_suspicious_bars.py --include-tw          # 連台股一起看
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import exchange_hours as eh


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(PROJECT_ROOT, "kline.db"))
    ap.add_argument("--at", default=None,
                    help="YYYY-MM-DD HH:MM 台北時間;預設現在")
    ap.add_argument("--include-tw", action="store_true")
    args = ap.parse_args()

    run_dt = (datetime.strptime(args.at, "%Y-%m-%d %H:%M")
              if args.at else datetime.now())
    conn = sqlite3.connect(args.db)
    suspects = eh.suspicious_symbols(conn, run_dt, exclude_tw=not args.include_tw)
    conn.close()

    print(f"判定時刻(台北): {run_dt:%Y-%m-%d %H:%M}")
    if not suspects:
        print("✅ 無半成品嫌疑 bar。")
        return 0
    print(f"⚠️  半成品嫌疑 {len(suspects)} 檔(最後一根所屬場次尚未收盤):")
    for sym, last, ex in suspects:
        print(f"  {sym:18} last={last} ({ex})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
