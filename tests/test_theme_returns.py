"""Tests for src/theme_returns.py"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.theme_returns import compute_for_date, get_return, load_subtags, N_THRESHOLD


def _build_kline_db(path, rows):
    """rows = [(symbol, date, close), ...]"""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE kline (
            symbol TEXT NOT NULL,
            date   TEXT NOT NULL,
            open   REAL,
            high   REAL,
            low    REAL,
            close  REAL,
            volume REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    for sym, d, c in rows:
        conn.execute(
            "INSERT INTO kline (symbol, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sym, d, c, c, c, c, 1000)
        )
    conn.commit()
    conn.close()


def _build_subtags(path, stocks):
    """stocks = {code: {name, L2, L3, L4}}"""
    Path(path).write_text(
        json.dumps({"stocks": stocks}, ensure_ascii=False),
        encoding="utf-8"
    )


class TestGetReturn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        _build_kline_db(self.tmp.name, [
            ("A", "2026-06-05", 100.0),
            ("A", "2026-06-08", 105.0),  # +5%
            ("B", "2026-06-08", 50.0),   # 只有 1 bar
            ("C", "2026-06-05", 0.0),    # prev_close=0
            ("C", "2026-06-08", 10.0),
        ])
        self.conn = sqlite3.connect(self.tmp.name)

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink()

    def test_normal_return(self):
        r = get_return(self.conn, "A", "2026-06-08")
        self.assertAlmostEqual(r, 5.0, places=2)

    def test_no_prev_bar(self):
        self.assertIsNone(get_return(self.conn, "B", "2026-06-08"))

    def test_zero_prev_close(self):
        self.assertIsNone(get_return(self.conn, "C", "2026-06-08"))

    def test_no_today_bar(self):
        # A 在 2026-06-09 沒有 bar(最新是 06-08)
        self.assertIsNone(get_return(self.conn, "A", "2026-06-09"))

    def test_unknown_symbol(self):
        self.assertIsNone(get_return(self.conn, "ZZZ", "2026-06-08"))


class TestComputeForDate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.kline = Path(self.tmpdir.name) / "kline.db"
        self.subtags = Path(self.tmpdir.name) / "subtags.json"

        # 3 檔在 TagA(N=3) / 2 檔在 TagB(N=2,N<3 不上榜)
        # 1 個成員停牌
        _build_kline_db(self.kline, [
            ("S1", "2026-06-05", 100.0), ("S1", "2026-06-08", 110.0),  # +10%
            ("S2", "2026-06-05", 100.0), ("S2", "2026-06-08", 105.0),  # +5%
            ("S3", "2026-06-05", 100.0),                                # 停牌
            ("S4", "2026-06-05", 200.0), ("S4", "2026-06-08", 220.0),  # +10%
            ("S5", "2026-06-05", 50.0),  ("S5", "2026-06-08", 49.0),   # -2%
        ])
        _build_subtags(self.subtags, {
            "S1": {"name": "股1", "L2": ["TagA"], "L3": [], "L4": []},
            "S2": {"name": "股2", "L2": ["TagA"], "L3": [], "L4": []},
            "S3": {"name": "股3", "L2": ["TagA"], "L3": [], "L4": []},
            "S4": {"name": "股4", "L2": ["TagB"], "L3": [], "L4": []},
            "S5": {"name": "股5", "L2": ["TagB"], "L3": [], "L4": []},
        })

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_basic_structure(self):
        out = compute_for_date("2026-06-08", kline_db=self.kline, subtags_path=self.subtags)
        self.assertEqual(out["date"], "2026-06-08")
        self.assertEqual(out["rules"]["n_threshold"], N_THRESHOLD)
        self.assertEqual(out["stats"]["total_tags"], 2)

    def test_n_threshold_filter(self):
        out = compute_for_date("2026-06-08", kline_db=self.kline, subtags_path=self.subtags)
        by_tag = {t["tag"]: t for t in out["tags"]}

        # TagA: 3 個成員,1 個停牌 → n_traded=2 → rankable=False(n_traded<3)
        self.assertEqual(by_tag["TagA"]["n"], 3)
        self.assertEqual(by_tag["TagA"]["n_traded"], 2)
        self.assertEqual(by_tag["TagA"]["n_excluded"], 1)
        self.assertFalse(by_tag["TagA"]["rankable"])

        # TagB: 2 個成員全交易 → n_traded=2 → rankable=False(n<3)
        self.assertEqual(by_tag["TagB"]["n"], 2)
        self.assertFalse(by_tag["TagB"]["rankable"])

    def test_equal_weight_average(self):
        out = compute_for_date("2026-06-08", kline_db=self.kline, subtags_path=self.subtags)
        by_tag = {t["tag"]: t for t in out["tags"]}
        # TagA: avg(+10, +5) = 7.5(停牌的 S3 剔除)
        self.assertAlmostEqual(by_tag["TagA"]["return_pct"], 7.5, places=2)
        # TagB: avg(+10, -2) = 4.0
        self.assertAlmostEqual(by_tag["TagB"]["return_pct"], 4.0, places=2)

    def test_excluded_member_marked(self):
        out = compute_for_date("2026-06-08", kline_db=self.kline, subtags_path=self.subtags)
        by_tag = {t["tag"]: t for t in out["tags"]}
        s3 = next(m for m in by_tag["TagA"]["members"] if m["code"] == "S3")
        self.assertTrue(s3["excluded"])
        self.assertIsNone(s3["return_pct"])


class TestRankableThreshold(unittest.TestCase):
    """專測 N>=3 邊界"""
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.kline = Path(self.tmpdir.name) / "kline.db"
        self.subtags = Path(self.tmpdir.name) / "subtags.json"

        _build_kline_db(self.kline, [
            ("X1", "2026-06-05", 100.0), ("X1", "2026-06-08", 101.0),
            ("X2", "2026-06-05", 100.0), ("X2", "2026-06-08", 102.0),
            ("X3", "2026-06-05", 100.0), ("X3", "2026-06-08", 103.0),
            ("X4", "2026-06-05", 100.0), ("X4", "2026-06-08", 104.0),
        ])
        _build_subtags(self.subtags, {
            "X1": {"name": "n1", "L2": ["T3", "T4"], "L3": [], "L4": []},
            "X2": {"name": "n2", "L2": ["T3", "T4"], "L3": [], "L4": []},
            "X3": {"name": "n3", "L2": ["T3", "T4"], "L3": [], "L4": []},
            "X4": {"name": "n4", "L2": ["T4"], "L3": [], "L4": []},
        })

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_exact_threshold(self):
        out = compute_for_date("2026-06-08", kline_db=self.kline, subtags_path=self.subtags)
        by_tag = {t["tag"]: t for t in out["tags"]}
        # T3 剛好 n=3 → rankable
        self.assertEqual(by_tag["T3"]["n"], 3)
        self.assertTrue(by_tag["T3"]["rankable"])
        # T4 n=4 → rankable
        self.assertEqual(by_tag["T4"]["n"], 4)
        self.assertTrue(by_tag["T4"]["rankable"])


class TestL2L3L4MixedScope(unittest.TestCase):
    """確認 L2 + L3 + L4 全混排"""
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.kline = Path(self.tmpdir.name) / "kline.db"
        self.subtags = Path(self.tmpdir.name) / "subtags.json"
        _build_kline_db(self.kline, [
            ("Y1", "2026-06-05", 100.0), ("Y1", "2026-06-08", 110.0),
            ("Y2", "2026-06-05", 100.0), ("Y2", "2026-06-08", 110.0),
            ("Y3", "2026-06-05", 100.0), ("Y3", "2026-06-08", 110.0),
        ])
        # 同名 tag 在 L2 / L3 / L4 出現,應該合併為一個 tag(集合去重)
        _build_subtags(self.subtags, {
            "Y1": {"name": "n1", "L2": ["MIXED"], "L3": [], "L4": []},
            "Y2": {"name": "n2", "L2": [], "L3": ["MIXED"], "L4": []},
            "Y3": {"name": "n3", "L2": [], "L3": [], "L4": ["MIXED"]},
        })

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_tags_dedup_across_levels(self):
        out = compute_for_date("2026-06-08", kline_db=self.kline, subtags_path=self.subtags)
        by_tag = {t["tag"]: t for t in out["tags"]}
        self.assertIn("MIXED", by_tag)
        self.assertEqual(by_tag["MIXED"]["n"], 3)
        self.assertTrue(by_tag["MIXED"]["rankable"])


class TestSortOrder(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.kline = Path(self.tmpdir.name) / "kline.db"
        self.subtags = Path(self.tmpdir.name) / "subtags.json"
        _build_kline_db(self.kline, [
            ("Z1", "2026-06-05", 100.0), ("Z1", "2026-06-08", 110.0),
            ("Z2", "2026-06-05", 100.0), ("Z2", "2026-06-08", 110.0),
            ("Z3", "2026-06-05", 100.0), ("Z3", "2026-06-08", 110.0),
            ("Z4", "2026-06-05", 100.0), ("Z4", "2026-06-08", 95.0),
            ("Z5", "2026-06-05", 100.0), ("Z5", "2026-06-08", 95.0),
            ("Z6", "2026-06-05", 100.0), ("Z6", "2026-06-08", 95.0),
        ])
        _build_subtags(self.subtags, {
            "Z1": {"name": "n", "L2": ["UP"],   "L3": [], "L4": []},
            "Z2": {"name": "n", "L2": ["UP"],   "L3": [], "L4": []},
            "Z3": {"name": "n", "L2": ["UP"],   "L3": [], "L4": []},
            "Z4": {"name": "n", "L2": ["DOWN"], "L3": [], "L4": []},
            "Z5": {"name": "n", "L2": ["DOWN"], "L3": [], "L4": []},
            "Z6": {"name": "n", "L2": ["DOWN"], "L3": [], "L4": []},
        })

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_rankable_sorted_desc(self):
        out = compute_for_date("2026-06-08", kline_db=self.kline, subtags_path=self.subtags)
        tags = [t["tag"] for t in out["tags"] if t["rankable"]]
        # UP +10% 應該排第一,DOWN -5% 排第二
        self.assertEqual(tags, ["UP", "DOWN"])


class TestLoadSubtags(unittest.TestCase):
    def test_loads_real_subtags(self):
        tag_members, code_to_name = load_subtags()
        self.assertGreater(len(tag_members), 0)
        self.assertIn("AI伺服器", tag_members)
        self.assertGreaterEqual(len(tag_members["AI伺服器"]), 25)


if __name__ == "__main__":
    unittest.main()
