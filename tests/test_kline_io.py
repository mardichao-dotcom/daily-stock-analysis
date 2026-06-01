"""
test_kline_io.py — kline_io 單元測試(W2.2.2)
"""
from __future__ import annotations
import os
import sqlite3
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.persistence import kline_io


def fresh_kline_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE kline ("
        "  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
        "  close REAL, volume REAL, PRIMARY KEY (symbol, date))"
    )
    return conn


class TestComputeKlineMaxDate(unittest.TestCase):

    def test_empty_table_returns_none(self):
        conn = fresh_kline_db()
        self.assertIsNone(kline_io.compute_kline_max_date(conn))
        conn.close()

    def test_returns_max_date_across_symbols(self):
        """跨 symbol 取 max"""
        conn = fresh_kline_db()
        conn.execute("INSERT INTO kline VALUES ('TPEX:6223', '2026-05-13', 0,0,0,0,0)")
        conn.execute("INSERT INTO kline VALUES ('TWSE:2330', '2026-05-20', 0,0,0,0,0)")
        conn.execute("INSERT INTO kline VALUES ('TPEX:6223', '2026-05-18', 0,0,0,0,0)")
        conn.commit()
        self.assertEqual(kline_io.compute_kline_max_date(conn), "2026-05-20")
        conn.close()

    def test_single_row(self):
        conn = fresh_kline_db()
        conn.execute("INSERT INTO kline VALUES ('TPEX:6223', '2026-05-13', 0,0,0,0,0)")
        conn.commit()
        self.assertEqual(kline_io.compute_kline_max_date(conn), "2026-05-13")
        conn.close()
