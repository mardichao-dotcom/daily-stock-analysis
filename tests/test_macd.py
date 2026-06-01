"""
test_macd.py — MACD 純函式單元測試(W2.2.7)

覆蓋:
  1. compute_ema:暖機 / 已知值 / 邊界
  2. compute_macd:DIF/DEA/OSC 索引 alignment + 行為對齊
  3. detect_transition:7 個轉換 / 邊界情境
"""
from __future__ import annotations
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.scoring import macd


# ─────────────────────────────────────────────────────────────────────────────
class TestComputeEma(unittest.TestCase):

    def test_period_3_known_values(self):
        """period=3, values=[1..6]:
        - idx 0,1 = None(資料不足)
        - idx 2 = SMA([1,2,3]) = 2.0
        - α = 2/4 = 0.5
        - idx 3 = 0.5*4 + 0.5*2 = 3.0
        - idx 4 = 0.5*5 + 0.5*3 = 4.0
        - idx 5 = 0.5*6 + 0.5*4 = 5.0
        """
        result = macd.compute_ema([1, 2, 3, 4, 5, 6], 3)
        self.assertEqual(result[:2], [None, None])
        self.assertAlmostEqual(result[2], 2.0)
        self.assertAlmostEqual(result[3], 3.0)
        self.assertAlmostEqual(result[4], 4.0)
        self.assertAlmostEqual(result[5], 5.0)

    def test_insufficient_history_all_none(self):
        """values 少於 period → 全 None"""
        result = macd.compute_ema([1, 2, 3], 12)
        self.assertEqual(result, [None, None, None])

    def test_constant_input_constant_ema(self):
        """全 100 → SMA seed 100,後續 EMA 也都 100"""
        result = macd.compute_ema([100.0] * 30, 12)
        for v in result[11:]:
            self.assertAlmostEqual(v, 100.0)


# ─────────────────────────────────────────────────────────────────────────────
class TestComputeMacd(unittest.TestCase):
    """compute_macd 索引 alignment + 數學關係"""

    def test_all_outputs_same_length(self):
        closes = [100.0] * 50
        result = macd.compute_macd(closes)
        self.assertEqual(len(result["dif"]), 50)
        self.assertEqual(len(result["dea"]), 50)
        self.assertEqual(len(result["osc"]), 50)

    def test_dif_none_before_index_25(self):
        """DIF 從索引 25 開始有值(EMA26 暖機完成)"""
        closes = list(range(1, 51))   # [1..50]
        result = macd.compute_macd(closes)
        for i in range(25):
            self.assertIsNone(result["dif"][i], msg=f"DIF[{i}] should be None")
        self.assertIsNotNone(result["dif"][25], msg="DIF[25] should be valid")

    def test_dea_osc_none_before_index_33(self):
        """DEA / OSC 從索引 33 開始有值(EMA9 of DIF 暖機完成,25+8=33)"""
        closes = list(range(1, 51))
        result = macd.compute_macd(closes)
        for i in range(33):
            self.assertIsNone(result["dea"][i], msg=f"DEA[{i}] should be None")
            self.assertIsNone(result["osc"][i], msg=f"OSC[{i}] should be None")
        self.assertIsNotNone(result["dea"][33], msg="DEA[33] should be valid")
        self.assertIsNotNone(result["osc"][33], msg="OSC[33] should be valid")

    def test_osc_equals_dif_minus_dea(self):
        """數學關係:OSC = DIF - DEA 在所有有值處成立"""
        closes = [100.0 + i * 0.5 for i in range(50)]   # 平滑上升
        result = macd.compute_macd(closes)
        for i, osc in enumerate(result["osc"]):
            if osc is None:
                continue
            self.assertAlmostEqual(osc, result["dif"][i] - result["dea"][i])

    def test_constant_closes_all_zero(self):
        """全 100 → DIF/DEA/OSC 在 valid 索引都 = 0(EMA12 = EMA26 = 100)"""
        result = macd.compute_macd([100.0] * 50)
        for i in range(25, 50):
            self.assertAlmostEqual(result["dif"][i], 0.0)
        for i in range(33, 50):
            self.assertAlmostEqual(result["dea"][i], 0.0)
            self.assertAlmostEqual(result["osc"][i], 0.0)


# ─────────────────────────────────────────────────────────────────────────────
class TestDetectTransition(unittest.TestCase):
    """2 根偵測邏輯(2026-05-28 規格修訂)。"""

    # ── 觸發情境 ──

    def test_negative_to_positive_triggers_green_to_red(self):
        """OSC 由負轉正(動能轉多)→ "green_to_red" """
        self.assertEqual(macd.detect_transition([-0.5, 0.7]), "green_to_red")

    def test_positive_to_negative_triggers_red_to_green(self):
        """OSC 由正轉負(動能轉空)→ "red_to_green" """
        self.assertEqual(macd.detect_transition([0.5, -0.7]), "red_to_green")

    # ── 同方向不觸發 ──

    def test_consecutive_positive_no_transition(self):
        """昨紅今紅(沒跨零軸)→ None"""
        self.assertIsNone(macd.detect_transition([0.3, 0.7]))

    def test_consecutive_negative_no_transition(self):
        """昨綠今綠(沒跨零軸)→ None"""
        self.assertIsNone(macd.detect_transition([-0.3, -0.7]))

    # ── OSC == 0 邊界(strict skip,2026-05-28 規格沿用)──

    def test_zero_yesterday_no_transition(self):
        """昨天 OSC == 0(貼零軸)→ strict < / > 不滿足 → None"""
        self.assertIsNone(macd.detect_transition([0.0, 0.7]))

    def test_zero_today_no_transition(self):
        """今天 OSC == 0 → None"""
        self.assertIsNone(macd.detect_transition([-0.5, 0.0]))

    def test_both_zero_no_transition(self):
        self.assertIsNone(macd.detect_transition([0.0, 0.0]))

    # ── 資料不足 / None ──

    def test_less_than_2_values_returns_none(self):
        self.assertIsNone(macd.detect_transition([0.1]))
        self.assertIsNone(macd.detect_transition([]))

    def test_none_values_return_none(self):
        """OSC 早期 None(暖機期)→ None"""
        self.assertIsNone(macd.detect_transition([None, 0.7]))
        self.assertIsNone(macd.detect_transition([-0.5, None]))

    # ── 「只看最後 2 根」鎖死(歷史早期值不影響)──

    def test_only_last_2_matter_earlier_history_ignored(self):
        """確認只看最後 2 根,早期值不影響判定。"""
        # 早期穿越過,但最後 2 根都正 → None
        self.assertIsNone(macd.detect_transition([0.3, 0.5, -0.7, 0.1, 0.9]))
        # 早期沒穿越,但最後 2 根剛好負→正 → green_to_red
        self.assertEqual(
            macd.detect_transition([0.3, 0.5, 0.7, -0.3, 0.9]),
            "green_to_red",
        )

    def test_sustained_above_zero_no_transition(self):
        """已在紅柱(OSC>0)持續,不重複觸發。
        (取代原 test_sustained_red_no_duplicate_tag,因新邏輯天然涵蓋。)"""
        # 連續 5 天紅 → 每一天看最後 2 根都是紅紅 → 永遠 None
        for i in range(1, 5):
            sub = [0.3, 0.5, 0.7, 0.9, 1.1][:i + 1]
            self.assertIsNone(macd.detect_transition(sub),
                              msg=f"sub={sub}")
