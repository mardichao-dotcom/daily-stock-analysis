"""
test_given_price.py — 給定價格計分單元測試

覆蓋:
  1. 4 種線類別(key_price / support_transfer / inner_support / whale_cost)
     × 3 種顏色(red/black/gray)× 形容詞情境
  2. 3 種均線(ma_20 / ma_60 / ma_90)無顏色,有形容詞時仍正確
  3. 4 種區域(order_block / poc / fvg / gap)無顏色
  4. should_score=False 各類別都跳過(回 0)
  5. 嚴格模式:未知 category → ValueError
  6. evidence 結構

執行:python3 -m unittest tests.test_given_price
"""
from __future__ import annotations
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.scoring.given_price import score_line, score_area


def load_weights() -> dict:
    with open(os.path.join(PROJECT_ROOT, "config", "weights.json"), encoding="utf-8") as f:
        return json.load(f)


def line(*, category, price=100.0, color=None, adjective=None, text=None) -> dict:
    return {"category": category, "price": price, "color": color,
            "adjective": adjective, "text": text}


def area(*, category, low=100.0, high=110.0, adjective=None, text=None) -> dict:
    return {"category": category, "low": low, "high": high,
            "adjective": adjective, "text": text}


# ─────────────────────────────────────────────────────────────────────────────
class TestLineWithColor(unittest.TestCase):
    """4 種有顏色加成的線類別"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_key_price_red_no_adj(self):
        """紅色關鍵價:1 × 1.5 = 1.5"""
        s, _ = score_line(line(category="key_price", color="red"), True, self.W)
        self.assertAlmostEqual(s, 1.5)

    def test_key_price_black_important(self):
        """重要黑色關鍵價:(1.0 × 1) + 1 = 2.0"""
        s, _ = score_line(line(category="key_price", color="black", adjective="important"),
                          True, self.W)
        self.assertAlmostEqual(s, 2.0)

    def test_key_price_gray_small(self):
        """小灰色關鍵價:0.7 × 1 × 0.7 = 0.49"""
        s, _ = score_line(line(category="key_price", color="gray", adjective="small"),
                          True, self.W)
        self.assertAlmostEqual(s, 0.49)

    def test_support_transfer_red_important(self):
        """重要紅色撐轉:(1.5 × 1) + 1 = 2.5 (對照 rule §2-C 範例 1 的 key_price)"""
        s, _ = score_line(line(category="support_transfer", color="red", adjective="important"),
                          True, self.W)
        self.assertAlmostEqual(s, 2.5)

    def test_inner_support_gray_important(self):
        """rule §2-C 範例 5:重要灰色內撐 → (0.7 × 1) + 1 = 1.7"""
        s, _ = score_line(line(category="inner_support", color="gray", adjective="important"),
                          True, self.W)
        self.assertAlmostEqual(s, 1.7)

    def test_whale_cost_red_no_adj(self):
        """紅色大戶成本:1 × 1.5 = 1.5"""
        s, _ = score_line(line(category="whale_cost", color="red"), True, self.W)
        self.assertAlmostEqual(s, 1.5)

    def test_whale_cost_black_small(self):
        """小黑色大戶成本:1 × 1 × 0.7 = 0.7"""
        s, _ = score_line(line(category="whale_cost", color="black", adjective="small"),
                          True, self.W)
        self.assertAlmostEqual(s, 0.7)


# ─────────────────────────────────────────────────────────────────────────────
class TestMovingAverage(unittest.TestCase):
    """均線(無顏色加成)"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_ma_20_plain(self):
        """MA20:1 × 1 = 1.0"""
        s, _ = score_line(line(category="ma_20"), True, self.W)
        self.assertAlmostEqual(s, 1.0)

    def test_ma_60_plain(self):
        """MA60:1 × 2 = 2.0"""
        s, _ = score_line(line(category="ma_60"), True, self.W)
        self.assertAlmostEqual(s, 2.0)

    def test_ma_60_small(self):
        """rule §2-C 範例 6:小黑色 60 日均線 → (1 × 2 × 0.7) + 0 = 1.4
        傳 color 也會被忽略"""
        s, _ = score_line(line(category="ma_60", color="black", adjective="small"),
                          True, self.W)
        self.assertAlmostEqual(s, 1.4)

    def test_ma_90_important(self):
        """重要 MA90:(1 × 2) + 1 = 3.0"""
        s, _ = score_line(line(category="ma_90", adjective="important"), True, self.W)
        self.assertAlmostEqual(s, 3.0)

    def test_ma_color_ignored(self):
        """傳 color='red' 給均線,色彩維度被忽略"""
        s_red,  _ = score_line(line(category="ma_60", color="red"), True, self.W)
        s_none, _ = score_line(line(category="ma_60", color=None), True, self.W)
        self.assertEqual(s_red, s_none)
        self.assertAlmostEqual(s_red, 2.0)


# ─────────────────────────────────────────────────────────────────────────────
class TestArea(unittest.TestCase):
    """區域(無顏色加成)"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_order_block_plain(self):
        """訂單塊:1 × 2 = 2.0"""
        s, _ = score_area(area(category="order_block"), True, self.W)
        self.assertAlmostEqual(s, 2.0)

    def test_order_block_important_small(self):
        """rule §2-C 範例 4:重要小紅色訂單塊 → (1 × 2 × 0.7) + 1 = 2.4
        ※ key_prices.json schema 單一形容詞,此範例只能取 important 或 small 之一,
           完整 2 形容詞驗證在 test_formula.py 已涵蓋"""
        # 取 important 那半邊:(1 × 2 × 1) + 1 = 3.0
        s_imp, _ = score_area(area(category="order_block", adjective="important"),
                              True, self.W)
        self.assertAlmostEqual(s_imp, 3.0)
        # 取 small 那半邊:(1 × 2 × 0.7) + 0 = 1.4
        s_sml, _ = score_area(area(category="order_block", adjective="small"),
                              True, self.W)
        self.assertAlmostEqual(s_sml, 1.4)

    def test_poc_plain(self):
        s, _ = score_area(area(category="poc"), True, self.W)
        self.assertAlmostEqual(s, 1.0)

    def test_fvg_plain(self):
        s, _ = score_area(area(category="fvg"), True, self.W)
        self.assertAlmostEqual(s, 1.0)

    def test_gap_plain(self):
        s, _ = score_area(area(category="gap"), True, self.W)
        self.assertAlmostEqual(s, 1.0)

    def test_area_short_term(self):
        """短線形容詞 ×1.0 不變值"""
        s, _ = score_area(area(category="poc", adjective="short_term"), True, self.W)
        self.assertAlmostEqual(s, 1.0)

    def test_area_estimated(self):
        """預估 ×0.9"""
        s, _ = score_area(area(category="poc", adjective="estimated"), True, self.W)
        self.assertAlmostEqual(s, 0.9)


# ─────────────────────────────────────────────────────────────────────────────
class TestShouldScoreFalse(unittest.TestCase):
    """should_score=False 各類別都跳過"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_line_skipped(self):
        s, d = score_line(line(category="key_price", color="red", adjective="important"),
                          False, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_area_skipped(self):
        s, d = score_area(area(category="order_block", adjective="important"),
                          False, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_skipped_does_not_validate_category(self):
        """should_score=False 提早返回,不檢查 category
        ※ 設計取捨:讓 caller 可以對「未匹配類別的 line」(W1.2 跳過的那些)
           安全呼叫 should_score=False 而不噴錯"""
        s, _ = score_line({"category": "bogus_made_up", "price": 100,
                           "color": None, "adjective": None}, False, self.W)
        self.assertEqual(s, 0)


# ─────────────────────────────────────────────────────────────────────────────
class TestStrictMode(unittest.TestCase):
    """嚴格模式:未知 category 必須 raise(per DD2)"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_unknown_line_category_raises(self):
        with self.assertRaises(ValueError) as cm:
            score_line(line(category="bogus_cat"), True, self.W)
        self.assertIn("bogus_cat", str(cm.exception))

    def test_unknown_area_category_raises(self):
        with self.assertRaises(ValueError) as cm:
            score_area(area(category="totally_made_up"), True, self.W)
        self.assertIn("totally_made_up", str(cm.exception))


# ─────────────────────────────────────────────────────────────────────────────
class TestEvidence(unittest.TestCase):
    """evidence 結構驗證"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_line_evidence_has_all_fields(self):
        _, d = score_line(line(category="key_price", price=2025, color="red",
                                adjective="important", text="2025"), True, self.W)
        ev = d[0]["evidence"]
        self.assertEqual(ev["kind"],      "line")
        self.assertEqual(ev["category"],  "key_price")
        self.assertEqual(ev["price"],     2025)
        self.assertEqual(ev["color"],     "red")
        self.assertEqual(ev["adjective"], "important")
        self.assertEqual(ev["text"],      "2025")

    def test_area_evidence_has_all_fields(self):
        _, d = score_area(area(category="fvg", low=1865, high=1935, text="FVG"),
                          True, self.W)
        ev = d[0]["evidence"]
        self.assertEqual(ev["kind"],     "area")
        self.assertEqual(ev["category"], "fvg")
        self.assertEqual(ev["low"],      1865)
        self.assertEqual(ev["high"],     1935)
        self.assertEqual(ev["text"],     "FVG")

    def test_ma_evidence_color_is_none(self):
        """MA 即使傳 color,evidence 也應該記 None(色彩維度被忽略)"""
        _, d = score_line(line(category="ma_60", color="red"), True, self.W)
        self.assertIsNone(d[0]["evidence"]["color"])


# ─────────────────────────────────────────────────────────────────────────────
class TestRealKeyPricesIntegration(unittest.TestCase):
    """跑幾條 key_prices.json 真實 row 驗證,確認結構對接 OK"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()
        with open(os.path.join(PROJECT_ROOT, "config", "key_prices.json"),
                  encoding="utf-8") as f:
            cls.KP = json.load(f)

    def test_taipei_tsmc_first_red_line(self):
        """台積電的紅色關鍵價 2025"""
        tsmc = self.KP["stocks"]["TWSE:2330"]
        red_lines = [l for l in tsmc["lines"] if l["color"] == "red"]
        self.assertTrue(red_lines)
        s, _ = score_line(red_lines[0], True, self.W)
        self.assertAlmostEqual(s, 1.5)   # red key_price = 1.5

    def test_wangxi_inner_support_small(self):
        """旺矽的「小內撐」(adjective=small, category=inner_support, color=black):
        1 × 1 × 0.7 = 0.7"""
        wangxi = self.KP["stocks"]["TPEX:6223"]
        sml_inner = [l for l in wangxi["lines"]
                     if l["category"] == "inner_support" and l["adjective"] == "small"]
        self.assertTrue(sml_inner)
        s, _ = score_line(sml_inner[0], True, self.W)
        self.assertAlmostEqual(s, 0.7)
