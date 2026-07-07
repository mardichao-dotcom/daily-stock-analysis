"""
test_render_v2.py — Stage 8 W3 render_v2 單元測試

覆蓋:
  1. 7 區塊都渲染出對的內容
  2. 分類邏輯(score → grade buckets)
  3. C 級特殊篩選(score < 4 + 任一標籤)
  4. ETF 主動式區塊渲染
  5. 個股卡含 name + sector + chart placeholder
  6. HTML 結構完整(<head>、<link>、<script>)
  7. 空狀態不噴錯
"""
from __future__ import annotations
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import render_v2


def make_stock(*, name="X", sector="S1", score=4.0, grade="B", tags=None,
                details=None, snapshot=None, events=None):
    return {
        "name":   name,
        "sector": sector,
        "score":  score,
        "grade":  grade,
        "tags":   tags or [],
        "details": details or [],
        "key_prices_snapshot": snapshot or {"lines": [], "areas": []},
        "events": events or [],
    }


def make_result(stocks=None, etf_active=None, date="2026-05-20", metadata=None):
    return {
        "date":    date,
        "version": "2.1",
        "metadata": metadata or {"etf_delayed": False, "generated_at": "X"},
        "stocks":  stocks or {},
        "etf_active": etf_active or {"increase": [], "decrease": []},
    }


# ─────────────────────────────────────────────────────────────────────────────
class TestClassifyStocks(unittest.TestCase):

    def test_grade_buckets(self):
        stocks = {
            "A": make_stock(score=7,   grade="S"),
            "B": make_stock(score=5,   grade="A"),
            "C": make_stock(score=4,   grade="B"),
            "D": make_stock(score=3,   grade="C", tags=["🟢 站穩 100"]),
            "E": make_stock(score=2,   grade="C", tags=[]),
            "F": make_stock(score=1,   grade="D", tags=["⚡ MACD 動能轉空"]),
        }
        b = render_v2.classify_stocks(stocks)
        self.assertEqual([s for s, _ in b["S"]], ["A"])
        self.assertEqual([s for s, _ in b["A"]], ["B"])
        self.assertEqual([s for s, _ in b["B"]], ["C"])
        self.assertEqual(set(s for s, _ in b["C_special"]), {"D", "F"})
        self.assertEqual([s for s, _ in b["C_other"]], ["E"])

    def test_sorted_by_score_desc_within_bucket(self):
        stocks = {
            "X": make_stock(score=6.0, grade="S"),
            "Y": make_stock(score=8.5, grade="S"),
            "Z": make_stock(score=7.2, grade="S"),
        }
        b = render_v2.classify_stocks(stocks)
        self.assertEqual([s for s, _ in b["S"]], ["Y", "Z", "X"])


# ─────────────────────────────────────────────────────────────────────────────
class TestRenderTop10(unittest.TestCase):

    def test_top10_section_top_by_score_desc(self):
        stocks = {f"S{i}": make_stock(name=f"N{i}", score=float(i))
                  for i in range(15)}
        html = render_v2.render_top10(stocks)
        self.assertIn("🏆 當日前十名", html)
        # 應該是 N14 (14 分) 第一
        self.assertLess(html.index("N14"), html.index("N13"))
        # 只取 10 個 — N4 不該在,N5 應該在(最低分入榜)
        # 新版用 <span class="stock-name">NX</span>,匹配閉合 tag
        self.assertNotIn(">N4<", html)
        self.assertIn(">N5<", html)

    def test_top10_empty_no_error(self):
        html = render_v2.render_top10({})
        self.assertIn("無資料", html)

    def test_top10_collapsible_card_with_chart_placeholder(self):
        """v2.2 polish:每項是 <details> 卡片,展開含 chart placeholder
        id 用 chart-top10- 前綴,避免跟 S/A/B 級個股卡(chart-)衝突
        """
        stocks = {"TWSE:2382": make_stock(name="廣達", score=7.0, grade="S")}
        html = render_v2.render_top10(stocks, date="2026-06-01")
        # top10 卡帶 data-symbol(stage9 事件徽章 hook)
        self.assertIn('<details class="stock-card top10-card grade-S" data-symbol="TWSE:2382">', html)
        self.assertIn('id="chart-top10-TWSE_2382"', html)
        self.assertIn('data-symbol="TWSE:2382"', html)
        self.assertIn('🥇', html)

    def test_top10_waiting_us_close_uses_amber_placeholder(self):
        """v2.2:status_map 標 waiting_us_close → top10 卡片內顯示 amber 文案"""
        stocks = {"NASDAQ:NVDA": make_stock(name="NVIDIA", score=8.0, grade="S")}
        status_map = {
            "NASDAQ_NVDA": {"status": "waiting_us_close", "exchange": "US",
                             "last_available_date": "2026-05-29"},
        }
        html = render_v2.render_top10(stocks, date="2026-06-01",
                                         status_map=status_map)
        self.assertIn("chart-placeholder awaiting", html)
        self.assertIn("等待 US 收盤資料", html)
        self.assertIn("2026-05-29", html)


# ─────────────────────────────────────────────────────────────────────────────
class TestRenderGradeBlocks(unittest.TestCase):

    def test_render_grade_section_lists_stocks(self):
        stocks_list = [
            ("TPEX:6223", make_stock(name="旺矽", sector="半導體設備耗材",
                                       score=7.0, grade="S",
                                       tags=["🟢 站穩 4640"])),
        ]
        html = render_v2.render_grade_section("S", "🔴 S 級戰區",
                                                stocks_list, "2026-05-20")
        self.assertIn("S 級戰區", html)
        self.assertIn("旺矽", html)
        self.assertIn("TPEX:6223", html)
        self.assertIn("半導體設備耗材", html)
        self.assertIn("🟢 站穩 4640", html)
        # chart placeholder
        self.assertIn('id="chart-TPEX_6223"', html)
        self.assertIn('data-symbol="TPEX:6223"', html)

    def test_render_grade_section_empty(self):
        html = render_v2.render_grade_section("A", "🟡 A 級戰區",
                                                [], "2026-05-20")
        self.assertIn("今日無A級個股", html)


# ─────────────────────────────────────────────────────────────────────────────
class TestRenderCSpecial(unittest.TestCase):

    def test_c_special_filter_requires_any_tag(self):
        """C 級必須含特殊標籤(站穩/跌破/MACD/輪動/ETF 減碼)才入"""
        stocks_list = [
            ("X", make_stock(name="X", grade="C", score=3.5,
                              tags=["⚡ MACD 動能轉多(買點)"])),
            ("Y", make_stock(name="Y", grade="C", score=3.0,
                              tags=["⛔ ETF 減碼(2 檔, -100 張)"])),
        ]
        html = render_v2.render_c_special(stocks_list)
        self.assertIn("X", html)
        self.assertIn("Y", html)
        self.assertIn("⚡ MACD", html)
        self.assertIn("⛔ ETF 減碼", html)

    def test_c_special_empty(self):
        html = render_v2.render_c_special([])
        self.assertIn("無 C 級含特殊標籤個股", html)


# ─────────────────────────────────────────────────────────────────────────────
class TestRenderEtfActive(unittest.TestCase):

    def test_render_etf_active_with_data(self):
        etf_active = {
            "increase": [{
                "symbol": "TPEX:6223", "etf_count": 3,
                "total_shares": 280, "etfs": ["00981A", "00987A", "00994A"],
            }],
            "decrease": [{
                "symbol": "TWSE:3443", "etf_count": 4,
                "total_shares": -725, "etfs": ["00981A", "00987A", "00994A", "00995A"],
            }],
        }
        stocks = {"TPEX:6223": make_stock(name="旺矽"),
                   "TWSE:3443": make_stock(name="創意")}
        html = render_v2.render_etf_active(etf_active, stocks)
        self.assertIn("ETF 主動式雙向掃描", html)
        self.assertIn("加碼區", html)
        self.assertIn("減碼區", html)
        self.assertIn("旺矽", html)
        self.assertIn("創意", html)
        self.assertIn("3 檔", html)        # increase etf_count
        self.assertIn("4 檔", html)        # decrease etf_count
        self.assertIn("+280", html)        # increase shares
        self.assertIn("-725", html)        # decrease shares
        self.assertIn("00981A", html)
        # 近 7 日累計標註
        self.assertIn("近 7 日累計", html)

    def test_render_etf_active_empty_no_error(self):
        """ETF 主動式空時仍正常渲染(顯示「無 ≥ 2 檔共識」)"""
        html = render_v2.render_etf_active(
            {"increase": [], "decrease": []}, {},
        )
        self.assertIn("無 ≥ 2 檔共識", html)
        # 區塊本身仍存在
        self.assertIn("ETF 主動式", html)


# ─────────────────────────────────────────────────────────────────────────────
class TestRenderOther(unittest.TestCase):

    def test_render_other_collapsed_details(self):
        stocks_list = [
            ("S1", make_stock(name="X")),
            ("S2", make_stock(name="Y")),
        ]
        html = render_v2.render_other(stocks_list)
        self.assertIn("<details>", html)
        self.assertNotIn("<details open>", html)   # default closed
        self.assertIn("其餘品項", html)
        self.assertIn("2 檔", html)

    def test_render_other_empty_returns_empty_string(self):
        """無其餘品項時不渲染區塊"""
        html = render_v2.render_other([])
        self.assertEqual(html, "")


# ─────────────────────────────────────────────────────────────────────────────
class TestFullRender(unittest.TestCase):
    """端到端:render() 產整份 HTML"""

    def test_complete_html_structure(self):
        result = make_result(
            stocks={
                "TPEX:6223": make_stock(name="旺矽", sector="X",
                                          score=7.0, grade="S",
                                          tags=["🟢 站穩 4640"]),
            },
        )
        html = render_v2.render(result)
        # HTML 基本骨架
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('<html lang="zh-Hant">', html)
        # Batch1:tokens+style 帶 ?v=(cache-busting),深色 pre-paint script 在前
        self.assertRegex(html, r'href="assets/tokens\.css\?v=[0-9a-f]{8}"')
        self.assertRegex(html, r'href="assets/style_v2\.css\?v=[0-9a-f]{8}"')
        self.assertIn("dataset.theme", html)
        self.assertRegex(html, r'<script src="assets/chart_v2\.js\?v=[0-9a-f]{8}"')  # Batch1
        # 7 區塊都在
        self.assertIn("🏆 當日前十名", html)
        self.assertIn("🔴 S 級戰區", html)
        self.assertIn("🟡 A 級戰區", html)
        self.assertIn("🟢 B 級戰區", html)
        self.assertIn("⭐ C 級特殊", html)
        self.assertIn("⛔ ETF 主動式", html)
        # 旺矽 在 S 級戰區
        self.assertIn("旺矽", html)

    def test_etf_delayed_warning_shown(self):
        result = make_result(
            metadata={"etf_delayed": True, "etf_max_date_in_db": "2026-05-19",
                       "generated_at": "X"},
        )
        html = render_v2.render(result)
        self.assertIn("ETF 籌碼資料延遲", html)
        self.assertIn("2026-05-19", html)


# ─────────────────────────────────────────────────────────────────────────────
class TestSafeId(unittest.TestCase):

    def test_safe_id(self):
        self.assertEqual(render_v2._safe_id("TWSE:2330"), "TWSE_2330")
        self.assertEqual(render_v2._safe_id("TPEX:6223"), "TPEX_6223")
