"""
test_render_history.py — Stage 8 W3 上線:歷史儀表板列表 smoke tests
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import render_history as rh


class TestDiscoverSnapshots(unittest.TestCase):

    def test_filters_pattern_and_sorts_desc(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "index_v2_2026-05-19.html").write_text("x")
            (d / "index_v2_2026-06-01.html").write_text("x")
            (d / "index_v2_2026-05-20.html").write_text("x")
            (d / "index_v2.html").write_text("x")   # 不該被算
            dates = rh.discover_snapshots(d)
            self.assertEqual(dates, ["2026-06-01", "2026-05-20", "2026-05-19"])

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(rh.discover_snapshots(Path(td)), [])


class TestFilterRecent(unittest.TestCase):

    def test_cutoff_by_max_days(self):
        # 最新 6/01,30 天 cutoff → 5/02
        dates = ["2026-06-01", "2026-05-20", "2026-05-01", "2026-04-15"]
        recent = rh.filter_recent(dates, 30)
        # 5/02 之前的 5/01 跟 4/15 該被砍
        self.assertEqual(recent, ["2026-06-01", "2026-05-20"])

    def test_max_days_zero_or_negative_passthrough(self):
        dates = ["2026-06-01", "2025-01-01"]
        self.assertEqual(rh.filter_recent(dates, 0), dates)

    def test_empty_list(self):
        self.assertEqual(rh.filter_recent([], 30), [])


class TestLoadSummary(unittest.TestCase):

    def test_loads_grade_counts_and_etf(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = {
                "date": "2026-06-01",
                "stocks": {
                    "X": {"grade": "S"}, "Y": {"grade": "A"},
                    "Z": {"grade": "A"}, "W": {"grade": "C"},
                },
                "etf_active": {"increase": [1, 2, 3], "decrease": [1, 2]},
            }
            (root / "filtered_result_2026-06-01.json").write_text(
                json.dumps(data), encoding="utf-8")
            s = rh.load_summary(root, "2026-06-01")
            self.assertEqual(s["S"], 1)
            self.assertEqual(s["A"], 2)
            self.assertEqual(s["B"], 0)
            self.assertEqual(s["etf_inc"], 3)
            self.assertEqual(s["etf_dec"], 2)

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(rh.load_summary(Path(td), "2026-06-01"), {})


class TestWeekday(unittest.TestCase):

    def test_known_weekdays(self):
        self.assertEqual(rh.weekday_zh("2026-06-01"), "週一")   # Monday
        self.assertEqual(rh.weekday_zh("2026-05-29"), "週五")   # Friday


class TestRender(unittest.TestCase):

    def test_lists_dates_with_summaries(self):
        dates = ["2026-06-01", "2026-05-20"]
        summaries = {
            "2026-06-01": {"S": 1, "A": 1, "B": 2, "etf_inc": 13, "etf_dec": 6},
            "2026-05-20": {"S": 1, "A": 0, "B": 0, "etf_inc": 4,  "etf_dec": 6},
        }
        html = rh.render(dates, summaries)
        self.assertIn("2026-06-01", html)
        self.assertIn("2026-05-20", html)
        self.assertIn("S 1 · A 1 · B 2", html)   # §17 Batch4 分隔符
        self.assertIn("ETF 加 13 減 6", html)
        # nav 連結
        self.assertIn('href="index.html"', html)
        self.assertIn('href="index_v2.html"', html)
        # 個別歷史 entry 連結
        self.assertIn('href="index_v2_2026-06-01.html"', html)
        self.assertIn('href="index_v2_2026-05-20.html"', html)

    def test_empty_state(self):
        html = rh.render([], {})
        self.assertIn("尚無歷史 snapshot", html)


if __name__ == "__main__":
    unittest.main()
