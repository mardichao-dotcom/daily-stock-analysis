"""
test_verify_kline.py — W1-2 K 線抽查對官方收盤(解析/容差/軟放行)
"""
from __future__ import annotations
import os
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import verify_kline as vk

STOCK_DAY_OK = {
    "stat": "OK",
    "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價",
               "收盤價", "漲跌價差", "成交筆數", "註記"],
    "data": [
        ["115/07/03", "1,000", "1", "100", "101", "99", "2,735.00", "+1", "10", ""],
        ["115/07/06", "2,283,004", "1", "2,825", "2,825", "2,600", "2,645.00", "-90", "7,154", ""],
    ],
}


class TestFetchOfficial(unittest.TestCase):
    def test_finds_date_and_parses_comma(self):
        v = vk.fetch_official_close("2345", "2026-07-06", fetch=lambda url: STOCK_DAY_OK)
        self.assertEqual(v, 2645.0)

    def test_date_not_in_month_returns_none(self):
        v = vk.fetch_official_close("2345", "2026-07-10", fetch=lambda url: STOCK_DAY_OK)
        self.assertIsNone(v)                         # 假日/未出檔 → None(軟放行)

    def test_stat_not_ok_returns_none(self):
        v = vk.fetch_official_close("2345", "2026-07-06",
                                    fetch=lambda url: {"stat": "很抱歉,沒有符合條件的資料!"})
        self.assertIsNone(v)


class TestCompareTolerance(unittest.TestCase):
    def test_within_half_percent_ok(self):
        self.assertLessEqual(vk.compare(2645.0, 2645.0), 0.5)
        self.assertLessEqual(vk.compare(2650.0, 2645.0), 0.5)    # 0.19%

    def test_over_half_percent_flagged(self):
        self.assertGreater(vk.compare(2700.0, 2645.0), 0.5)     # 2.08%


class TestRunEndToEnd(unittest.TestCase):
    def test_mismatch_detected_and_official_missing_soft(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE kline (symbol TEXT, date TEXT, open REAL, high REAL,"
                         " low REAL, close REAL, volume REAL, PRIMARY KEY(symbol,date))")
            # 兩檔 watchlist 上市:2345 值差 5%(應 flag)、2330 官方源查無(soft)
            conn.execute("INSERT INTO kline VALUES ('TWSE:2345','2026-07-06',0,0,0,2777.0,0)")
            conn.execute("INSERT INTO kline VALUES ('TWSE:2330','2026-07-06',0,0,0,1000.0,0)")
            conn.commit(); conn.close()

            def fake_fetch(url):
                if "stockNo=2345" in url:
                    return STOCK_DAY_OK               # 官方 2645 vs db 2777 → 5%
                return {"stat": "很抱歉"}              # 2330 取不到
            orig = vk.get_all_tw_symbols
            vk.get_all_tw_symbols = lambda: ["TWSE:2345", "TWSE:2330"]
            try:
                r = vk.run("2026-07-06", db, n=5, fetch=fake_fetch)
            finally:
                vk.get_all_tw_symbols = orig
            self.assertEqual(r["status"], "mismatch")
            self.assertEqual(len(r["mismatches"]), 1)
            self.assertEqual(r["mismatches"][0][0], "TWSE:2345")
            self.assertEqual(r["unavailable"], ["TWSE:2330"])   # 軟放行,不算 mismatch

    def test_sampling_deterministic_by_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE kline (symbol TEXT, date TEXT, open REAL, high REAL,"
                         " low REAL, close REAL, volume REAL, PRIMARY KEY(symbol,date))")
            for i in range(10):
                conn.execute("INSERT INTO kline VALUES (?,?,0,0,0,100,0)",
                             (f"TWSE:{1000+i}", "2026-07-06"))
            conn.commit(); conn.close()
            orig = vk.get_all_tw_symbols
            vk.get_all_tw_symbols = lambda: [f"TWSE:{1000+i}" for i in range(10)]
            try:
                a = vk.sample_symbols("2026-07-06", db, 5)
                b = vk.sample_symbols("2026-07-06", db, 5)
            finally:
                vk.get_all_tw_symbols = orig
            self.assertEqual(a, b)                    # 同日重跑抽同一批(可重現)


if __name__ == "__main__":
    unittest.main()
