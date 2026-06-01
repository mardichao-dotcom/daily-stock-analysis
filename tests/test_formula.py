"""
test_formula.py — 規則 v2.1 計分公式單元測試

覆蓋:
  1. rule §2-C 的 6 個官方範例(寫死數字,鎖死公式行為)
  2. 邊界情境:空白形容詞、灰色、區域無顏色、均線無顏色、多形容詞、未知形容詞
  3. 純數學層(compute)直接呼叫

執行: python3 -m unittest tests.test_formula
"""
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.scoring.formula import calculate, compute


def load_weights() -> dict:
    with open(os.path.join(PROJECT_ROOT, "config", "weights.json"), encoding="utf-8") as f:
        return json.load(f)


class TestRuleExamples(unittest.TestCase):
    """rule §2-C 的 6 個官方範例。任何一個錯了 = 公式壞了。"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    # ── 範例 1:重要紅色關鍵價 → (1.5 × 1) + 1 = 2.5 ────────────────────────
    def test_example_1_important_red_key_price(self):
        score = calculate(
            base=self.W["given_price"]["key_price"],
            color="red",
            adjectives=["important"],
            has_color_multiplier=True,
            weights=self.W,
        )
        self.assertAlmostEqual(score, 2.5)

    # ── 範例 2:小紅色關鍵價 → (1.5 × 1 × 0.7) + 0 = 1.05 ──────────────────
    def test_example_2_small_red_key_price(self):
        score = calculate(
            base=self.W["given_price"]["key_price"],
            color="red",
            adjectives=["small"],
            has_color_multiplier=True,
            weights=self.W,
        )
        self.assertAlmostEqual(score, 1.05)

    # ── 範例 3:小紅色撐轉 → (1.5 × 1 × 0.7) + 0 = 1.05 ────────────────────
    def test_example_3_small_red_support_transfer(self):
        score = calculate(
            base=self.W["given_price"]["support_transfer"],
            color="red",
            adjectives=["small"],
            has_color_multiplier=True,
            weights=self.W,
        )
        self.assertAlmostEqual(score, 1.05)

    # ── 範例 4:重要小紅色訂單塊 → (1 × 2 × 0.7) + 1 = 2.4(區域不套顏色)
    def test_example_4_important_small_red_order_block(self):
        score = calculate(
            base=self.W["given_price"]["order_block"],
            color="red",                  # 故意傳 red,但 has_color_multiplier=False 會忽略
            adjectives=["important", "small"],
            has_color_multiplier=False,
            weights=self.W,
        )
        self.assertAlmostEqual(score, 2.4)

    # ── 範例 5:重要灰色內撐 → (0.7 × 1) + 1 = 1.7 ─────────────────────────
    def test_example_5_important_gray_inner_support(self):
        score = calculate(
            base=self.W["given_price"]["inner_support"],
            color="gray",
            adjectives=["important"],
            has_color_multiplier=True,
            weights=self.W,
        )
        self.assertAlmostEqual(score, 1.7)

    # ── 範例 6:小黑色 60 日均線 → (1 × 2 × 0.7) + 0 = 1.4(均線不套顏色) ───
    def test_example_6_small_black_ma_60(self):
        score = calculate(
            base=self.W["given_price"]["ma_60"],
            color="black",                # 故意傳 black,但 has_color_multiplier=False
            adjectives=["small"],
            has_color_multiplier=False,
            weights=self.W,
        )
        self.assertAlmostEqual(score, 1.4)


class TestEdgeCases(unittest.TestCase):
    """邊界情境。"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_no_adjective_red_line(self):
        """空白形容詞 + 紅色線 → 純顏色加成"""
        score = calculate(base=1, color="red", adjectives=None,
                          has_color_multiplier=True, weights=self.W)
        self.assertAlmostEqual(score, 1.5)

    def test_no_adjective_black_line(self):
        """空白形容詞 + 黑色線 → 不變"""
        score = calculate(base=1, color="black", adjectives=None,
                          has_color_multiplier=True, weights=self.W)
        self.assertAlmostEqual(score, 1.0)

    def test_no_adjective_gray_line(self):
        """空白形容詞 + 灰色線 → ×0.7"""
        score = calculate(base=1, color="gray", adjectives=[],
                          has_color_multiplier=True, weights=self.W)
        self.assertAlmostEqual(score, 0.7)

    def test_area_color_ignored(self):
        """區域:顏色被忽略,色彩維度恆 1.0"""
        score = calculate(base=2, color="red", adjectives=None,
                          has_color_multiplier=False, weights=self.W)
        self.assertAlmostEqual(score, 2.0)

    def test_area_with_none_color(self):
        """區域:顏色傳 None 也 OK"""
        score = calculate(base=2, color=None, adjectives=None,
                          has_color_multiplier=False, weights=self.W)
        self.assertAlmostEqual(score, 2.0)

    def test_multiple_multiply_adjectives(self):
        """多 multiply 形容詞相乘"""
        # (1.5 × 1 × 0.7 × 1.0 × 0.9) + 0 = 0.945
        score = calculate(
            base=1, color="red",
            adjectives=["small", "short_term", "estimated"],
            has_color_multiplier=True, weights=self.W,
        )
        self.assertAlmostEqual(score, 0.945)

    def test_short_term_is_neutral(self):
        """短線 ×1.0 不改值,但語意上有差(rule §2-A)"""
        score = calculate(base=1, color="red", adjectives=["short_term"],
                          has_color_multiplier=True, weights=self.W)
        self.assertAlmostEqual(score, 1.5)

    def test_estimated_multiplies(self):
        """預估 ×0.9"""
        # (1.5 × 1 × 0.9) + 0 = 1.35
        score = calculate(base=1, color="red", adjectives=["estimated"],
                          has_color_multiplier=True, weights=self.W)
        self.assertAlmostEqual(score, 1.35)

    def test_unknown_adjective_raises(self):
        """嚴格模式:未知形容詞 raise ValueError(防靜默誤算)"""
        with self.assertRaises(ValueError) as cm:
            calculate(base=1, color="red",
                      adjectives=["important", "bogus_adjective"],
                      has_color_multiplier=True, weights=self.W)
        self.assertIn("bogus_adjective", str(cm.exception))

    def test_unknown_color_raises(self):
        """嚴格模式:未知顏色 raise ValueError(防靜默誤算)"""
        with self.assertRaises(ValueError) as cm:
            calculate(base=1, color="purple", adjectives=None,
                      has_color_multiplier=True, weights=self.W)
        self.assertIn("purple", str(cm.exception))

    def test_unknown_color_ignored_when_no_color_multiplier(self):
        """has_color_multiplier=False 時不查 color,即使是垃圾值也不噴錯"""
        # 區域不套顏色 → color 參數整段被跳過,不該觸發 strict check
        score = calculate(base=2, color="purple", adjectives=None,
                          has_color_multiplier=False, weights=self.W)
        self.assertAlmostEqual(score, 2.0)


class TestComputePureLayer(unittest.TestCase):
    """直接測純數學層 compute(),確認跟 calculate 解耦。"""

    def test_simple(self):
        # 1.5 × 1 × 1 + 0 = 1.5
        self.assertAlmostEqual(compute(1, 1.5, [], []), 1.5)

    def test_multiply_factors(self):
        # 1.5 × 1 × 0.7 × 1.0 = 1.05
        self.assertAlmostEqual(compute(1, 1.5, [0.7, 1.0], []), 1.05)

    def test_add_factors(self):
        # 1.5 × 1 + 1 + 1 = 3.5
        self.assertAlmostEqual(compute(1, 1.5, [], [1, 1]), 3.5)

    def test_both(self):
        # 1.5 × 1 × 0.7 + 1 = 2.05
        self.assertAlmostEqual(compute(1, 1.5, [0.7], [1]), 2.05)

    def test_zero_base(self):
        # 任何乘法都 0,但 add 可以救回來
        self.assertAlmostEqual(compute(0, 1.5, [0.7], [1]), 1.0)

    def test_empty_lists(self):
        # 退化情境:product 為 1.0,sum 為 0
        self.assertAlmostEqual(compute(2, 1.0, [], []), 2.0)


if __name__ == "__main__":
    unittest.main()
