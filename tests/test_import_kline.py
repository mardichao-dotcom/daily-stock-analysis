"""
test_import_kline.py — P0-C 防回歸:import 必須 UPSERT 覆寫(收盤後重抓覆蓋半成品)

舊行為 INSERT OR IGNORE:已存在的 (symbol,date) 被忽略 → 盤中半成品永久殘留。
新行為 INSERT OR REPLACE:重抓的正確收盤值覆寫之。
"""
from __future__ import annotations
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ts(date_str: str) -> int:
    """YYYY-MM-DD → UTC unix timestamp(import 用 utcfromtimestamp 還原日期)。"""
    from datetime import datetime, timezone
    y, m, d = map(int, date_str.split("-"))
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


class TestImportUpsert(unittest.TestCase):

    def _run_import(self, db, bars, extra=()):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"results": {"NASDAQ:NVDA": {"bars": bars}}}, f)
            jpath = f.name
        try:
            subprocess.run(
                [sys.executable, "src/import_kline.py", "--json", jpath,
                 "--db", db, *extra],
                cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
        finally:
            os.unlink(jpath)

    def test_replace_overwrites_halfbaked_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            t = _ts("2026-06-11")
            # 第一次:盤中半成品 close=100
            self._run_import(db, [{"time": t, "open": 90, "high": 110,
                                   "low": 80, "close": 100, "volume": 1}],
                             extra=["--no-data-date"])
            # 第二次:收盤後重抓 close=128(正確值)
            self._run_import(db, [{"time": t, "open": 90, "high": 130,
                                   "low": 80, "close": 128, "volume": 5}],
                             extra=["--no-data-date"])
            conn = sqlite3.connect(db)
            rows = conn.execute(
                "SELECT close, volume FROM kline WHERE symbol='NASDAQ:NVDA' "
                "AND date='2026-06-11'").fetchall()
            conn.close()
            self.assertEqual(len(rows), 1)              # 沒有重複列
            self.assertEqual(rows[0], (128.0, 5.0))      # 被覆寫成收盤正確值

    def test_no_data_date_does_not_write_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            date_file = os.path.join(PROJECT_ROOT, ".data_date")
            before = open(date_file).read() if os.path.exists(date_file) else None
            self._run_import(db, [{"time": _ts("2020-01-02"), "open": 1, "high": 1,
                                   "low": 1, "close": 1, "volume": 1}],
                             extra=["--no-data-date"])
            after = open(date_file).read() if os.path.exists(date_file) else None
            # --no-data-date 不得改動專案的 .data_date
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
