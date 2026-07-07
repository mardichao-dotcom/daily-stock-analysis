"""
test_render_landing.py — Stage 8 W3 上線:入口頁 smoke tests
"""
from __future__ import annotations
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import render_landing as rl


def make_watchlist(tw_count=58, intl_count=29):
    """造 mini watchlist:1 板塊 N 檔台股 + 1 群 M 檔國際。"""
    tw_members = [{"code": f"TWSE:{i}", "name": f"股{i}"} for i in range(tw_count)]
    intl_members = [{"code": f"NASDAQ:S{i}", "name": f"國{i}"} for i in range(intl_count)]
    return {
        "更新日期": "2026-06-01",
        "版本": "test",
        "台股板塊": {"板塊A": {"成員": tw_members, "長子": []}},
        "國際族群": {"國際A": {"成員": intl_members, "長子": []}},
    }


class TestCountWatchlist(unittest.TestCase):

    def test_count(self):
        wl = make_watchlist(58, 29)
        tw_secs, tw_n, intl_secs, intl_n = rl.count_watchlist(wl)
        self.assertEqual((tw_secs, tw_n, intl_secs, intl_n), (1, 58, 1, 29))


class TestCountHistorySnapshots(unittest.TestCase):

    def test_counts_only_matching_pattern(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # 符合 pattern 的 3 個
            (d / "index_v2_2026-06-01.html").write_text("x")
            (d / "index_v2_2026-05-20.html").write_text("x")
            (d / "index_v2_2026-05-19.html").write_text("x")
            # 不該被算到的
            (d / "index_v2.html").write_text("x")
            (d / "index_v2_519.html").write_text("x")
            (d / "watchlist_v2.html").write_text("x")
            self.assertEqual(rl.count_history_snapshots(d), 3)

    def test_missing_dir(self):
        self.assertEqual(rl.count_history_snapshots(Path("/nonexistent/xyz")), 0)


class TestRender(unittest.TestCase):

    def test_3_entry_points_present(self):
        html = rl.render(
            watchlist=make_watchlist(),
            latest_date="2026-06-01",
            history_count=3,
        )
        # 3 卡片入口
        self.assertIn('href="index_v2.html"', html)
        self.assertIn('href="watchlist_v2.html"', html)
        self.assertIn('href="history.html"', html)
        # 卡片標題
        self.assertIn("今日儀表板", html)
        self.assertIn("Watchlist", html)
        self.assertIn("歷史儀表板", html)

    def test_meta_includes_counts(self):
        html = rl.render(
            watchlist=make_watchlist(58, 29),
            latest_date="2026-06-01",
            history_count=3,
        )
        self.assertIn("2026-06-01", html)
        self.assertIn("87 檔", html)   # 58 + 29
        self.assertIn("台股 58", html)
        self.assertIn("國際 29", html)
        self.assertIn("3 天可選", html)

    def test_zero_history_shows_starting_blurb(self):
        html = rl.render(
            watchlist=make_watchlist(),
            latest_date="2026-06-01",
            history_count=0,
        )
        self.assertIn("今天開始累積", html)

    def test_css_link_and_doctype(self):
        html = rl.render(watchlist=make_watchlist(), latest_date="?", history_count=0)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertRegex(html, r'href="assets/style_v2\.css\?v=[0-9a-f]{8}"')  # Batch1 cache-busting


if __name__ == "__main__":
    unittest.main()
