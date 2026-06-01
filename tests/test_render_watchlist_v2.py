"""
test_render_watchlist_v2.py — Stage 8 W3 補充:全 watchlist 折疊 K 線頁面 smoke 測試

覆蓋:
  1. 板塊正確列出(台股 + 國際分區)
  2. 每檔個股有 chart placeholder + data-symbol / data-date
  3. 長子(⭐)標記正確
  4. 個股 summary 含 filtered_result 摘要(score/grade/tags)
  5. 板塊空成員不會炸
  6. 國際族群「對應台股族群」顯示
  7. nav 連回 index_v2
"""
from __future__ import annotations
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import render_watchlist_v2 as rwl


def make_watchlist(tw=None, intl=None, update_date="2026-05-19"):
    return {
        "更新日期": update_date,
        "版本":   "test",
        "台股板塊": tw or {},
        "國際族群": intl or {},
    }


def make_result(stocks=None):
    return {"date": "2026-05-19", "stocks": stocks or {}}


# ─────────────────────────────────────────────────────────────────────────────
class TestRenderMember(unittest.TestCase):

    def test_member_has_chart_placeholder(self):
        m = {"code": "TWSE:2330", "name": "台積電"}
        html = rwl.render_member(m, set(), "2026-05-19", {})
        self.assertIn('id="chart-TWSE_2330"', html)
        self.assertIn('data-symbol="TWSE:2330"', html)
        self.assertIn('data-date="2026-05-19"', html)
        self.assertIn("台積電", html)

    def test_leader_marked(self):
        m = {"code": "TWSE:2345", "name": "智邦"}
        html = rwl.render_member(m, {"TWSE:2345"}, "2026-05-19", {})
        self.assertIn("⭐", html)
        self.assertIn("族群長子", html)

    def test_non_leader_no_mark(self):
        m = {"code": "TWSE:3081", "name": "聯亞"}
        html = rwl.render_member(m, {"TWSE:2345"}, "2026-05-19", {})
        self.assertNotIn("⭐", html)

    def test_summary_with_filtered_result(self):
        m = {"code": "TWSE:2327", "name": "國巨"}
        stocks_index = {
            "TWSE:2327": {"grade": "B", "score": 4.0,
                            "tags": ["🟢 站穩 3000"]},
        }
        html = rwl.render_member(m, set(), "2026-05-19", stocks_index)
        self.assertIn("4.0", html)
        # grade badge
        self.assertIn('grade-badge B', html)
        # tag 第一個 emoji
        self.assertIn("🟢", html)

    def test_summary_without_filtered_result(self):
        """個股不在 filtered_result(例如國際股)→ summary 區仍渲染但無分數"""
        m = {"code": "NASDAQ:NVDA", "name": "輝達"}
        html = rwl.render_member(m, set(), "2026-05-19", {})
        self.assertIn("輝達", html)
        self.assertIn("NASDAQ:NVDA", html)
        self.assertNotIn("grade-badge", html)


# ─────────────────────────────────────────────────────────────────────────────
class TestRenderSector(unittest.TestCase):

    def test_sector_lists_all_members(self):
        sec = {
            "成員": [
                {"code": "TWSE:2345", "name": "智邦"},
                {"code": "TPEX:3081", "name": "聯亞"},
            ],
            "長子": ["TWSE:2345"],
        }
        html = rwl.render_sector("光通訊", sec, "2026-05-19", {})
        self.assertIn("光通訊", html)
        self.assertIn("(2 檔)", html)
        self.assertIn("智邦", html)
        self.assertIn("聯亞", html)
        # 長子 inline
        self.assertIn("長子:智邦", html)

    def test_sector_empty_members_no_error(self):
        html = rwl.render_sector("空板塊", {"成員": []}, "2026-05-19", {})
        self.assertIn("空板塊", html)
        self.assertIn("(0 檔)", html)


# ─────────────────────────────────────────────────────────────────────────────
class TestRenderIntlGroup(unittest.TestCase):

    def test_intl_group_with_corresp(self):
        grp = {
            "成員": [{"code": "NASDAQ:NVDA", "name": "輝達"}],
            "長子": ["NASDAQ:NVDA"],
            "對應台股族群": ["IC設計", "AI伺服器"],
        }
        html = rwl.render_intl_group("AI 龍頭", grp, "2026-05-19", {})
        self.assertIn("AI 龍頭", html)
        self.assertIn("輝達", html)
        self.assertIn("對應台股族群", html)
        self.assertIn("IC設計", html)


# ─────────────────────────────────────────────────────────────────────────────
class TestFullRender(unittest.TestCase):

    def test_complete_html_with_nav(self):
        wl = make_watchlist(
            tw={"光通訊": {"成員": [{"code": "TWSE:2345", "name": "智邦"}],
                            "長子": []}},
            intl={"AI龍頭": {"成員": [{"code": "NASDAQ:NVDA", "name": "輝達"}],
                              "長子": [], "對應台股族群": ["IC設計"]}},
        )
        html = rwl.render(wl, "2026-05-19")
        # HTML 骨架
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('<link rel="stylesheet" href="assets/style_v2.css">', html)
        self.assertIn('<script src="assets/chart_v2.js"', html)
        # nav 連回 index_v2
        self.assertIn('href="index_v2.html"', html)
        self.assertIn("📈 儀表板", html)
        self.assertIn("Watchlist", html)
        # 兩大區塊
        self.assertIn("📊 台股板塊", html)
        self.assertIn("🌏 國際族群", html)
        # 個股
        self.assertIn("智邦", html)
        self.assertIn("輝達", html)
        # meta
        self.assertIn("2026-05-19", html)
        self.assertIn("台股 1 檔", html)
        self.assertIn("國際 1 檔", html)

    def test_with_filtered_result_summary(self):
        wl = make_watchlist(
            tw={"被動": {"成員": [{"code": "TWSE:2327", "name": "國巨"}],
                          "長子": []}},
        )
        result = make_result(stocks={
            "TWSE:2327": {"grade": "B", "score": 4.0, "tags": ["🟢 站穩 3000"]},
        })
        html = rwl.render(wl, "2026-05-19", result)
        self.assertIn("國巨", html)
        self.assertIn("4.0", html)
        self.assertIn("grade-badge B", html)

    def test_empty_watchlist_doesnt_crash(self):
        wl = make_watchlist()
        html = rwl.render(wl, "2026-05-19")
        self.assertIn("台股 0 檔", html)
        self.assertIn("國際 0 檔", html)


# ─────────────────────────────────────────────────────────────────────────────
class TestShortCode(unittest.TestCase):

    def test_short_code(self):
        self.assertEqual(rwl._short_code("TWSE:2330"), "2330")
        self.assertEqual(rwl._short_code("NASDAQ:NVDA"), "NVDA")


if __name__ == "__main__":
    unittest.main()
