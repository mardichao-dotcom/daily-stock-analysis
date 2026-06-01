"""
test_volume.py — 異常成交量計分單元測試

覆蓋:
  1. 門檻分流(0 / 1 / 2 分)
  2. 邊界 inclusivity(>= 1.6, >= 2.0,跟 v1 的 strict > 不同)
  3. 取較大者(不疊加)
  4. evidence 結構
  5. v1 vs v2 對比 fixture(W2.3 並行驗證 baseline)

執行:python3 -m unittest tests.test_volume
"""
from __future__ import annotations
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.scoring.volume import score


def load_weights() -> dict:
    with open(os.path.join(PROJECT_ROOT, "config", "weights.json"), encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
class TestThresholds(unittest.TestCase):
    """門檻分流 + 邊界 inclusivity"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_ratio_below_small_threshold(self):
        s, d = score("X", "2026-05-26", {"vol_ratio": 1.59}, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_ratio_at_small_threshold_inclusive(self):
        """規則 v2.1 用 >= 1.6,邊界 inclusive(跟 v1 的 > 1.5 不同)"""
        s, d = score("X", "2026-05-26", {"vol_ratio": 1.6}, self.W)
        self.assertEqual(s, 1)
        self.assertEqual(d[0]["score"], 1)

    def test_ratio_between_thresholds(self):
        s, d = score("X", "2026-05-26", {"vol_ratio": 1.85}, self.W)
        self.assertEqual(s, 1)

    def test_ratio_at_big_threshold_inclusive(self):
        """規則 v2.1 用 >= 2.0,邊界 inclusive
        (跟 v1 的 > 2.0 不同 — v1 ratio=2.0 會落到 elif > 1.5 變 +1)"""
        s, d = score("X", "2026-05-26", {"vol_ratio": 2.0}, self.W)
        self.assertEqual(s, 2)
        self.assertEqual(d[0]["score"], 2)

    def test_ratio_well_above_big(self):
        s, _ = score("X", "2026-05-26", {"vol_ratio": 3.5}, self.W)
        self.assertEqual(s, 2)

    def test_ratio_zero(self):
        s, d = score("X", "2026-05-26", {"vol_ratio": 0}, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_missing_ratio_returns_zero(self):
        """vol_ratio 缺失 → 0 分(防 KeyError)"""
        s, d = score("X", "2026-05-26", {}, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])


# ─────────────────────────────────────────────────────────────────────────────
class TestNoStacking(unittest.TestCase):
    """規則明文「取較大者,不疊加」"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_high_ratio_only_triggers_big_not_both(self):
        """ratio=2.5 應只觸發大爆量 +2,不疊加(+1+2=3)"""
        s, d = score("X", "2026-05-26", {"vol_ratio": 2.5}, self.W)
        self.assertEqual(s, 2)
        self.assertEqual(len(d), 1)   # 只觸發一條,不是兩條
        self.assertIn("大", d[0]["reason"])


# ─────────────────────────────────────────────────────────────────────────────
class TestEvidence(unittest.TestCase):
    """evidence 結構驗證"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_evidence_has_ratio_when_minimal_input(self):
        _, d = score("X", "2026-05-26", {"vol_ratio": 1.75}, self.W)
        ev = d[0]["evidence"]
        self.assertEqual(ev["vol_ratio"], 1.75)
        self.assertNotIn("today_volume", ev)

    def test_evidence_includes_today_and_avg(self):
        _, d = score("X", "2026-05-26", {
            "vol_ratio":    2.5,
            "today_volume": 1_500_000,
            "avg_volume":   600_000,
        }, self.W)
        ev = d[0]["evidence"]
        self.assertEqual(ev["today_volume"],   1_500_000)
        self.assertEqual(ev["avg_20d_volume"], 600_000)
        self.assertEqual(ev["vol_ratio"],      2.5)


# ─────────────────────────────────────────────────────────────────────────────
class TestV1V2Comparison(unittest.TestCase):
    """v1 (5 日 / >1.5 / >2.0) vs v2 (20 日 / >=1.6 / >=2.0) 對比 fixture。

    給 W2.3 並行驗證當 baseline:同樣的歷史 K 線資料,兩種算法分數差多少。
    """

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    # ── 模擬器(test-only,不放進 src/)─────────────────────────────────────
    @staticmethod
    def _simulate_v1(vol_history: list[int], today_vol: int) -> int:
        """V1 行為:5 日窗口(不含今天)、strict `>` 1.5 / 2.0"""
        window = vol_history[-5:] if len(vol_history) >= 5 else vol_history
        avg = sum(window) / len(window) if window else 0
        ratio = today_vol / avg if avg > 0 else 1.0
        if ratio > 2.0:
            return 2
        if ratio > 1.5:
            return 1
        return 0

    def _simulate_v2(self, vol_history: list[int], today_vol: int) -> int:
        """V2 行為:20 日窗口(不含今天)、inclusive `>=` 1.6 / 2.0,
        透過 src/scoring/volume.score() 真正跑一次"""
        window = vol_history[-20:] if len(vol_history) >= 20 else vol_history
        avg = sum(window) / len(window) if window else 0
        ratio = today_vol / avg if avg > 0 else 1.0
        s, _ = score("X", "2026-05-26",
                     {"vol_ratio": ratio, "today_volume": today_vol, "avg_volume": avg},
                     self.W)
        return int(s)

    # ── 情境 A:邊界 inclusivity 差異(ratio=2.0)─────────────────────────
    def test_scenario_A_boundary_2x(self):
        """25 日恆定 800k,今天 1.6M → 兩窗口的 ratio 都是 2.0
        但 v1 strict `>` → elif 1.5 → +1
        v2 inclusive `>=` → +2"""
        hist = [800_000] * 25
        today = 1_600_000
        v1 = self._simulate_v1(hist, today)
        v2 = self._simulate_v2(hist, today)
        self.assertEqual(v1, 1)
        self.assertEqual(v2, 2)

    # ── 情境 B:近期上升趨勢,長窗口才抓得到 ────────────────────────────
    def test_scenario_B_recent_uptrend(self):
        """過去 20 日低量 500k,近 5 日突然漲到 1.2M,今天 1.8M
        v1 5 日 avg = 1.2M → ratio 1.5 → 0(strict >)
        v2 20 日 avg = (15×500k + 5×1.2M) / 20 = 675k → ratio 2.67 → +2"""
        hist = [500_000] * 20 + [1_200_000] * 5
        today = 1_800_000
        v1 = self._simulate_v1(hist, today)
        v2 = self._simulate_v2(hist, today)
        self.assertEqual(v1, 0)
        self.assertEqual(v2, 2)

    # ── 情境 C:門檻提高(1.5 → 1.6)的中間地帶 ────────────────────────
    def test_scenario_C_threshold_gap(self):
        """25 日恆定 1M,今天 1.55M → 兩窗口的 ratio 都是 1.55
        v1 strict > 1.5 → +1
        v2 >= 1.6 → 0"""
        hist = [1_000_000] * 25
        today = 1_550_000
        v1 = self._simulate_v1(hist, today)
        v2 = self._simulate_v2(hist, today)
        self.assertEqual(v1, 1)
        self.assertEqual(v2, 0)

    # ── 情境 D:大爆量,兩種算法都抓到 ─────────────────────────────────
    def test_scenario_D_same_outcome(self):
        """25 日恆定 500k,今天 1.5M → ratio 3.0
        v1 +2,v2 +2 — 兩者皆 trigger 大爆量"""
        hist = [500_000] * 25
        today = 1_500_000
        v1 = self._simulate_v1(hist, today)
        v2 = self._simulate_v2(hist, today)
        self.assertEqual(v1, 2)
        self.assertEqual(v2, 2)
