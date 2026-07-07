"""
test_taiex_daily.py — 加權指數日序列(stage10 後續調整:週報雙軸 + §17 大盤欄)
"""
from __future__ import annotations
import os
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import taiex_daily as td

FMT = {"stat": "OK",
       "fields": ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"],
       "data": [["115/07/04", "1", "1", "1", "46,556.39", "-224.23"],
                ["115/07/07", "1", "1", "1", "45,479.11", "-1,077.28"]]}


class TestFetchMonth(unittest.TestCase):
    def test_parse_roc_and_commas(self):
        rows = td.fetch_month("202607", fetch=lambda u: FMT)
        self.assertEqual(rows[0], ("2026-07-04", 46556.39, -224.23))
        self.assertEqual(rows[1][0], "2026-07-07")

    def test_not_ok_empty(self):
        self.assertEqual(td.fetch_month("202601", fetch=lambda u: {"stat": "查無"}), [])


class TestDbHelpers(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = f.name
        td.upsert_month(self.db, [("2026-07-04", 46556.39, -224.23),
                                  ("2026-07-07", 45479.11, -1077.28)])
        self.addCleanup(os.unlink, self.db)

    def test_closes_for(self):
        m = td.closes_for(self.db, ["2026-07-04", "2026-07-07", "2026-07-05"])
        self.assertEqual(m["2026-07-07"], 45479.11)
        self.assertNotIn("2026-07-05", m)          # 假日缺 → 不冒充

    def test_chg_pct_hand_calc(self):
        # (45479.11 - 46556.39) / 46556.39 * 100 = -2.31(手算)
        self.assertEqual(td.chg_pct(self.db, "2026-07-07"), -2.31)

    def test_chg_pct_missing_none(self):
        self.assertIsNone(td.chg_pct(self.db, "2026-07-06"))   # 該日無列
        self.assertIsNone(td.chg_pct(self.db, "2026-07-04"))   # 無前日


if __name__ == "__main__":
    unittest.main()
