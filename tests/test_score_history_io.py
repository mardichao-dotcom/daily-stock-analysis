"""
test_score_history_io.py — score_history CRUD + 巢狀 GROUP BY 查詢測試(W2.2.6)

重點測試:
  1. init_schema + write_batch + UPSERT
  2. compute_sector_avg_over_days 的「巢狀 GROUP BY date」對停牌成員不稀釋
  3. 「最近 N 個有資料的日期」(不要求連續)
  4. n_days 不足回 None
"""
from __future__ import annotations
import os
import sqlite3
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.persistence import score_history_io


class HistoryIoBase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        score_history_io.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _insert(self, *rows):
        """rows: (date, symbol, score, grade)"""
        for date, symbol, score, grade in rows:
            self.conn.execute(
                "INSERT INTO score_history VALUES (?, ?, ?, ?, ?)",
                (date, symbol, score, grade, "T"),
            )
        self.conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
class TestInitSchema(HistoryIoBase):

    def test_table_exists(self):
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='score_history'"
        )
        self.assertIsNotNone(cur.fetchone())

    def test_idempotent(self):
        score_history_io.init_schema(self.conn)
        score_history_io.init_schema(self.conn)


# ─────────────────────────────────────────────────────────────────────────────
class TestWriteBatch(HistoryIoBase):

    def test_write_batch_inserts_rows(self):
        results = {
            "TPEX:6223": {"score": 0.7, "grade": "D"},
            "TWSE:2330": {"score": 5.5, "grade": "A"},
        }
        n = score_history_io.write_batch(self.conn, "2026-05-14", results, "T")
        self.assertEqual(n, 2)
        cur = self.conn.execute("SELECT COUNT(*) FROM score_history")
        self.assertEqual(cur.fetchone()[0], 2)

    def test_write_batch_upsert_same_day(self):
        """同一 (date, symbol) 重跑 → UPSERT 更新 score / grade"""
        score_history_io.write_batch(
            self.conn, "2026-05-14",
            {"TPEX:6223": {"score": 0.7, "grade": "D"}},
            "T1",
        )
        score_history_io.write_batch(
            self.conn, "2026-05-14",
            {"TPEX:6223": {"score": 3.5, "grade": "C"}},
            "T2",
        )
        cur = self.conn.execute(
            "SELECT score, grade, last_updated FROM score_history "
            "WHERE date='2026-05-14' AND symbol='TPEX:6223'"
        )
        self.assertEqual(cur.fetchone(), (3.5, "C", "T2"))

    def test_write_batch_handles_missing_grade(self):
        """entry 沒 'grade' 也能寫(grade 寫成 NULL)"""
        score_history_io.write_batch(
            self.conn, "2026-05-14",
            {"TPEX:6223": {"score": 0.7}},   # 沒 grade
            "T",
        )
        cur = self.conn.execute(
            "SELECT grade FROM score_history WHERE symbol='TPEX:6223'"
        )
        self.assertIsNone(cur.fetchone()[0])


# ─────────────────────────────────────────────────────────────────────────────
class TestComputeSectorAvg(HistoryIoBase):

    def test_basic_5_days(self):
        """3 個成員 × 5 個交易日,每日族群均分 = (1+2+3)/3 = 2.0,過去 5 日均分 = 2.0"""
        for d in range(13, 18):   # 5/13~5/17
            self._insert(
                (f"2026-05-{d}", "A", 1, None),
                (f"2026-05-{d}", "B", 2, None),
                (f"2026-05-{d}", "C", 3, None),
            )
        avg = score_history_io.compute_sector_avg_over_days(
            self.conn, ["A", "B", "C"], end_date_exclusive="2026-05-18",
        )
        self.assertAlmostEqual(avg, 2.0)

    def test_sector_avg_excludes_today(self):
        """end_date_exclusive 真的排除今天(today 的 row 不算)"""
        # 過去 5 天每天都 score=1
        for d in range(13, 18):
            self._insert((f"2026-05-{d}", "A", 1, None),
                          (f"2026-05-{d}", "B", 1, None))
        # 今天 score=999(應該被排除)
        self._insert(("2026-05-18", "A", 999, None),
                      ("2026-05-18", "B", 999, None))
        avg = score_history_io.compute_sector_avg_over_days(
            self.conn, ["A", "B"], end_date_exclusive="2026-05-18",
        )
        self.assertAlmostEqual(avg, 1.0)   # 不是 (1*5+999)/6

    def test_sector_avg_groups_by_date_first_stop_loss_member_not_diluted(self):
        """
        關鍵測試:停牌成員那天少 row,各日獨立平均才不會被稀釋。

        Day 1: A=10, B=10, C=10  → daily_avg = 10
        Day 2: A=10              → daily_avg = 10  (B、C 停牌)
        Day 3: A=10, B=10        → daily_avg = 10
        Day 4: A=10, B=10, C=10  → daily_avg = 10
        Day 5: A=10, B=10, C=10  → daily_avg = 10

        正確:  mean(10,10,10,10,10) = 10
        錯誤(直接對所有 row AVG):
              (10 + 10 + 10*2 + 10*3 + 10*3) / (1+2+3+3+3) = ...
              其實也是 10 因為都是 10。
        改用不同分數讓差異顯現:
        """
        # Day 1: A=2, B=2, C=2 → daily_avg = 2
        self._insert(("2026-05-13", "A", 2, None),
                      ("2026-05-13", "B", 2, None),
                      ("2026-05-13", "C", 2, None))
        # Day 2: only A=10(B/C 停牌)→ daily_avg = 10
        self._insert(("2026-05-14", "A", 10, None))
        # Day 3: A=2, B=2, C=2 → daily_avg = 2
        self._insert(("2026-05-15", "A", 2, None),
                      ("2026-05-15", "B", 2, None),
                      ("2026-05-15", "C", 2, None))
        # Day 4: A=2, B=2, C=2 → daily_avg = 2
        self._insert(("2026-05-16", "A", 2, None),
                      ("2026-05-16", "B", 2, None),
                      ("2026-05-16", "C", 2, None))
        # Day 5: A=2, B=2, C=2 → daily_avg = 2
        self._insert(("2026-05-19", "A", 2, None),
                      ("2026-05-19", "B", 2, None),
                      ("2026-05-19", "C", 2, None))

        avg = score_history_io.compute_sector_avg_over_days(
            self.conn, ["A", "B", "C"], end_date_exclusive="2026-05-20",
        )
        # 正確: mean(2, 10, 2, 2, 2) = 3.6 (各日 daily_avg 平均)
        # 錯誤: 全 row AVG = (2+2+2 + 10 + 2+2+2 + 2+2+2 + 2+2+2) / 13
        #             = 36 / 13 ≈ 2.77
        # 應該是 3.6
        self.assertAlmostEqual(avg, 3.6)

    def test_picks_recent_5_trading_days_with_gap(self):
        """系統漏跑 5/16 → 仍取最近 5 個有資料的日期(5/13/14/15/17/18)"""
        for d in (13, 14, 15, 17, 18):   # 漏掉 5/16
            self._insert((f"2026-05-{d}", "A", 1, None))
        avg = score_history_io.compute_sector_avg_over_days(
            self.conn, ["A"], end_date_exclusive="2026-05-19",
        )
        self.assertAlmostEqual(avg, 1.0)   # 不要求連續,5 天都有資料就算

    def test_history_insufficient_returns_none(self):
        """只有 3 天歷史 → 不夠 5 天 → None"""
        for d in (13, 14, 15):
            self._insert((f"2026-05-{d}", "A", 1, None))
        avg = score_history_io.compute_sector_avg_over_days(
            self.conn, ["A"], end_date_exclusive="2026-05-19",
        )
        self.assertIsNone(avg)

    def test_empty_members_returns_none(self):
        avg = score_history_io.compute_sector_avg_over_days(
            self.conn, [], end_date_exclusive="2026-05-19",
        )
        self.assertIsNone(avg)


# ─────────────────────────────────────────────────────────────────────────────
class TestReadScores(HistoryIoBase):

    def test_returns_sorted_rows(self):
        self._insert(
            ("2026-05-14", "A", 1.0, "D"),
            ("2026-05-13", "B", 2.0, "C"),
            ("2026-05-13", "A", 1.5, "D"),
        )
        rows = score_history_io.read_scores_for_symbols_over_window(
            self.conn, ["A", "B"], "2026-05-13", "2026-05-14",
        )
        # 依 date asc, symbol asc
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[0]["date"], rows[0]["symbol"]), ("2026-05-13", "A"))
        self.assertEqual((rows[1]["date"], rows[1]["symbol"]), ("2026-05-13", "B"))
        self.assertEqual((rows[2]["date"], rows[2]["symbol"]), ("2026-05-14", "A"))

    def test_empty_symbols_returns_empty_list(self):
        rows = score_history_io.read_scores_for_symbols_over_window(
            self.conn, [], "2026-05-13", "2026-05-14",
        )
        self.assertEqual(rows, [])
