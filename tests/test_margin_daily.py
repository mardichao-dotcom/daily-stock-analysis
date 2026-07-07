"""
test_margin_daily.py — 融資改版(2026-07-07):金額解析 / streak / 百分位 / N-A 護欄
"""
from __future__ import annotations
import os
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import margin_daily as md

TWSE_MS = {"stat": "OK", "tables": [{
    "fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"],
    "data": [["融資(交易單位)", "1", "1", "1", "1", "9,579,335"],
             ["融資金額(仟元)", "1", "1", "1", "1", "631,344,856"]]}]}
TPEX_BAL = {"stat": "ok", "tables": [{
    "fields": ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額"],
    "data": [],
    "summary": [["", "合計(張)", "1", "1", "1", "1", "2,467,821"],
                ["", "融資金(仟元)", "1", "1", "1", "1", "214,564,154"]]}]}


class TestParsers(unittest.TestCase):
    def test_twse_amount(self):
        v = md.fetch_twse_amount_k("2026-07-06", fetch=lambda u: TWSE_MS)
        self.assertEqual(v, 631344856.0)

    def test_twse_non_trading_none(self):
        v = md.fetch_twse_amount_k("2026-07-05", fetch=lambda u: {"stat": "查無資料"})
        self.assertIsNone(v)

    def test_tpex_amount_from_summary(self):
        v = md.fetch_tpex_amount_k("2026-07-06", fetch=lambda u: TPEX_BAL)
        self.assertEqual(v, 214564154.0)

    def test_total_yi_conversion(self):
        """仟元 → 億元:(631,344,856 + 214,564,154) / 100,000 = 8,459.1 億。"""
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "m.db")
            yi = md.upsert_day(db, "2026-07-06", 631344856, 214564154)
            self.assertEqual(yi, 8459.1)


class TestStats(unittest.TestCase):
    def _db(self, seq):
        """seq = [(date, total_yi)] 舊→新。"""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.execute("CREATE TABLE margin_daily (date TEXT PRIMARY KEY,"
                     " twse_k REAL, tpex_k REAL, total_yi REAL)")
        for dt, v in seq:
            conn.execute("INSERT INTO margin_daily VALUES (?,0,0,?)", (dt, v))
        conn.commit(); conn.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_streak_up(self):
        db = self._db([("2026-07-01", 100), ("2026-07-02", 101), ("2026-07-03", 99),
                       ("2026-07-04", 100), ("2026-07-05", 102), ("2026-07-06", 105)])
        st = md.stats(db, "2026-07-06")
        self.assertEqual(st["streak"], 3)         # 99→100→102→105 = 3 日連增
        self.assertEqual(st["amount_yi"], 105)
        self.assertEqual(st["change_yi"], 3.0)

    def test_streak_down(self):
        db = self._db([("2026-07-04", 105), ("2026-07-05", 103), ("2026-07-06", 100)])
        st = md.stats(db, "2026-07-06")
        self.assertEqual(st["streak"], -2)

    def test_streak_flat_zero(self):
        db = self._db([("2026-07-05", 100), ("2026-07-06", 100)])
        self.assertEqual(md.stats(db, "2026-07-06")["streak"], 0)

    def test_percentile_hand_calc(self):
        """手算對照:10 筆序列,今日 108 ≥ 其中 9 筆(含自身)→ 90%。"""
        seq = [(f"2026-06-{d:02d}", v) for d, v in
               zip(range(1, 10), [100, 101, 99, 103, 105, 102, 104, 106, 110])]
        seq.append(("2026-06-10", 108))
        db = self._db(seq)
        st = md.stats(db, "2026-06-10")
        self.assertEqual(st["percentile"], 90)    # 108 ≥ 9/10 筆
        self.assertEqual(st["days"], 10)

    def test_percentile_lowest(self):
        db = self._db([("2026-07-05", 200), ("2026-07-06", 100)])
        self.assertEqual(md.stats(db, "2026-07-06")["percentile"], 50)  # ≥ 自身 1/2

    def test_missing_date_returns_none(self):
        db = self._db([("2026-07-05", 100)])
        self.assertIsNone(md.stats(db, "2026-07-06"))   # 該日無列 → 不冒充


class TestCollectGuard(unittest.TestCase):
    def test_one_market_missing_no_insert(self):
        """N/A 護欄:任一市缺 → 不入庫回 None(不冒充半市場值)。"""
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "m.db")
            def fetch(url):
                return TWSE_MS if "twse" in url else {"stat": "查無"}
            v = md.collect_day("2026-07-06", db, fetch=fetch)
            self.assertIsNone(v)
            conn = sqlite3.connect(db)
            try:
                n = conn.execute("SELECT COUNT(*) FROM margin_daily").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
            conn.close()
            self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
