"""
test_exchange_hours.py — P0-C 收盤時刻邏輯 / 半成品 bar 判定單元測試
"""
from __future__ import annotations
import os
import sqlite3
import sys
import unittest
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import exchange_hours as eh


class TestSessionClose(unittest.TestCase):

    def test_tw_same_day(self):
        self.assertEqual(eh.session_close_taipei("TWSE", "2026-06-11"),
                         datetime(2026, 6, 11, 13, 30))

    def test_us_next_day(self):
        # 美股當日場次收在台北隔天凌晨
        self.assertEqual(eh.session_close_taipei("NASDAQ", "2026-06-10"),
                         datetime(2026, 6, 11, 4, 0))

    def test_unknown_exchange_none(self):
        self.assertIsNone(eh.session_close_taipei("LSE", "2026-06-11"))


class TestIntradaySuspect(unittest.TestCase):

    def setUp(self):
        self.run_dt = datetime(2026, 6, 11, 19, 12)   # 典型主跑時刻

    def test_maersk_today_bar_is_suspect(self):
        # 哥本哈根 6/11 場次 23:00 收 > 19:12 抓 → 半成品
        self.assertTrue(eh.is_intraday_suspect("OMXCOP", "2026-06-11", self.run_dt))

    def test_us_yesterday_bar_is_safe(self):
        # NASDAQ 6/10 場次台北 6/11 04:00 收 < 19:12 → 已收盤,安全
        self.assertFalse(eh.is_intraday_suspect("NASDAQ", "2026-06-10", self.run_dt))

    def test_us_today_bar_is_suspect(self):
        # NASDAQ 6/11 場次台北 6/12 04:00 收 > 19:12 → 半成品(其實該場次根本還沒開)
        self.assertTrue(eh.is_intraday_suspect("NASDAQ", "2026-06-11", self.run_dt))

    def test_jp_today_bar_safe_at_1900(self):
        # 東京 14:00 收 < 19:12 → 安全
        self.assertFalse(eh.is_intraday_suspect("TSE", "2026-06-11", self.run_dt))

    def test_unknown_exchange_conservative_suspect(self):
        self.assertTrue(eh.is_intraday_suspect("LSE", "2026-06-11", self.run_dt))


class TestSuspiciousSymbols(unittest.TestCase):

    def _db(self, rows):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE kline (symbol TEXT, date TEXT, open REAL, "
                     "high REAL, low REAL, close REAL, volume REAL, "
                     "PRIMARY KEY (symbol, date))")
        conn.executemany("INSERT INTO kline VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
        return conn

    def test_flags_maersk_excludes_tw_and_safe_us(self):
        run_dt = datetime(2026, 6, 11, 19, 12)
        rows = [
            ("OMXCOP:MAERSK_B", "2026-06-11", 1, 1, 1, 1, 1),   # 半成品 → 標記
            ("NASDAQ:NVDA",     "2026-06-10", 1, 1, 1, 1, 1),   # 已收盤 → 不標記
            ("TWSE:2330",       "2026-06-11", 1, 1, 1, 1, 1),   # 台股 → 排除
            ("TSE:6594",        "2026-06-11", 1, 1, 1, 1, 1),   # 東京已收 → 不標記
        ]
        conn = self._db(rows)
        suspects = eh.suspicious_symbols(conn, run_dt)
        self.assertEqual([s[0] for s in suspects], ["OMXCOP:MAERSK_B"])
        conn.close()

    def test_include_tw_optional(self):
        run_dt = datetime(2026, 6, 11, 12, 0)   # 12:00,台股尚未收盤(13:30)
        rows = [("TWSE:2330", "2026-06-11", 1, 1, 1, 1, 1)]
        conn = self._db(rows)
        self.assertEqual(eh.suspicious_symbols(conn, run_dt, exclude_tw=True), [])
        suspects = eh.suspicious_symbols(conn, run_dt, exclude_tw=False)
        self.assertEqual([s[0] for s in suspects], ["TWSE:2330"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
