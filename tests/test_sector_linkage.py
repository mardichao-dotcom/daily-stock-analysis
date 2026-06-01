"""
test_sector_linkage.py — 族群連動計分單元測試

覆蓋:
  1. is_intl_leader_activated 三條件(各觸發 + 都不觸發)
  2. score 的評級門檻比較(B 以上 vs B 以下)
  3. score 的早退路徑(無板塊、無國際發動)
  4. 嚴格模式(未知 level → ValueError)
  5. evidence 結構

執行:python3 -m unittest tests.test_sector_linkage
"""
from __future__ import annotations
import copy
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.scoring.sector_linkage import is_intl_leader_activated, score


def load_weights() -> dict:
    with open(os.path.join(PROJECT_ROOT, "config", "weights.json"), encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
class TestIsIntlLeaderActivated(unittest.TestCase):
    """國際長子發動判定:abs(漲跌)>3% 且 量比>1.5x,或 突破 60 日高"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_no_movement_no_activation(self):
        self.assertFalse(is_intl_leader_activated({
            "change_pct": 0.5, "vol_ratio": 1.0,
            "close": 100, "high_60d": 110,
        }, self.W))

    def test_condition_a_big_up_high_volume(self):
        """漲 5% + 量比 1.8 → 發動"""
        self.assertTrue(is_intl_leader_activated({
            "change_pct": 5.0, "vol_ratio": 1.8,
            "close": 100, "high_60d": 110,
        }, self.W))

    def test_condition_a_big_down_does_NOT_activate(self):
        """跌 5% + 量比 1.8 → 不發動。
        規則字面用 abs(漲跌),但語意上「發動 = 資金流入」,跌 = 資金流出,
        不該觸發族群加分。詳見 sector_linkage.py module docstring。"""
        self.assertFalse(is_intl_leader_activated({
            "change_pct": -5.0, "vol_ratio": 1.8,
            "close": 100, "high_60d": 110,
        }, self.W))

    def test_condition_a_big_move_low_volume_no_activation(self):
        """漲 5% 但量比 1.2 → 不發動(量比未過 1.5x)"""
        self.assertFalse(is_intl_leader_activated({
            "change_pct": 5.0, "vol_ratio": 1.2,
            "close": 100, "high_60d": 110,
        }, self.W))

    def test_condition_a_small_move_high_volume_no_activation(self):
        """漲 2.5% + 量比 2.0 → 不發動(漲跌幅未過 3%)"""
        self.assertFalse(is_intl_leader_activated({
            "change_pct": 2.5, "vol_ratio": 2.0,
            "close": 100, "high_60d": 110,
        }, self.W))

    def test_condition_a_boundary_3pct_strict(self):
        """漲 3.0% 剛好不過(規則用 strict >,3.0 不算超過 3.0)"""
        self.assertFalse(is_intl_leader_activated({
            "change_pct": 3.0, "vol_ratio": 1.8,
            "close": 100, "high_60d": 110,
        }, self.W))

    def test_condition_b_breakout(self):
        """突破 60 日高 → 發動(即使量比平、漲幅小也成立)"""
        self.assertTrue(is_intl_leader_activated({
            "change_pct": 1.5, "vol_ratio": 0.9,
            "close": 115, "high_60d": 110,
        }, self.W))

    def test_condition_b_equal_high_no_activation(self):
        """收盤 = 60 日高,沒突破(strict >)"""
        self.assertFalse(is_intl_leader_activated({
            "change_pct": 0.5, "vol_ratio": 0.9,
            "close": 110, "high_60d": 110,
        }, self.W))

    def test_both_conditions_either_works(self):
        """同時滿足 (a) + (b) 仍然 True"""
        self.assertTrue(is_intl_leader_activated({
            "change_pct": 5.0, "vol_ratio": 2.0,
            "close": 120, "high_60d": 110,
        }, self.W))


# ─────────────────────────────────────────────────────────────────────────────
class TestScore(unittest.TestCase):
    """族群連動計分主路徑"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_no_sector_returns_zero(self):
        """個股無板塊歸屬(如京鼎尚未進 watchlist)→ 0 分"""
        s, d = score("TWSE:2367", "2026-05-26", {
            "sector": None, "sector_level": None, "intl_activated": True,
        }, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_empty_sector_returns_zero(self):
        """sector 是 "" 也視為無歸屬"""
        s, _ = score("X", "X", {
            "sector": "", "sector_level": "A", "intl_activated": True,
        }, self.W)
        self.assertEqual(s, 0)

    def test_intl_not_activated_returns_zero(self):
        """國際長子今天沒發動 → 0 分"""
        s, d = score("TWSE:2330", "2026-05-26", {
            "sector": "晶圓代工", "sector_level": "A", "intl_activated": False,
        }, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_level_a_with_activation_scores(self):
        """A 級板塊 + 國際發動 → +1"""
        s, d = score("TWSE:2330", "2026-05-26", {
            "sector": "晶圓代工", "sector_level": "A", "intl_activated": True,
            "intl_leaders_activated": ["NYSE:TSM"],
        }, self.W)
        self.assertEqual(s, 1)
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["score"], 1)
        self.assertEqual(d[0]["evidence"]["activated_leaders"], ["NYSE:TSM"])

    def test_level_b_at_threshold_scores(self):
        """B 級板塊 + 國際發動 → +1(B 是門檻 inclusive)"""
        s, _ = score("TWSE:X", "X", {
            "sector": "光通訊", "sector_level": "B", "intl_activated": True,
        }, self.W)
        self.assertEqual(s, 1)

    def test_level_c_below_threshold_no_score(self):
        """C 級板塊 + 國際發動 → 0(未達 B 門檻)"""
        s, d = score("TWSE:X", "X", {
            "sector": "X", "sector_level": "C", "intl_activated": True,
        }, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_level_d_no_score(self):
        s, _ = score("TWSE:X", "X", {
            "sector": "X", "sector_level": "D", "intl_activated": True,
        }, self.W)
        self.assertEqual(s, 0)

    def test_level_s_scores(self):
        """假如未來加 S 級,也能通過(level_rank 從 weights 來,自然涵蓋)"""
        s, _ = score("TWSE:X", "X", {
            "sector": "X", "sector_level": "S", "intl_activated": True,
        }, self.W)
        self.assertEqual(s, 1)


# ─────────────────────────────────────────────────────────────────────────────
class TestStrictMode(unittest.TestCase):
    """未知 level 必須 raise(per DD2)"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_unknown_sector_level_raises(self):
        with self.assertRaises(ValueError) as cm:
            score("X", "X", {
                "sector": "X", "sector_level": "Z", "intl_activated": True,
            }, self.W)
        self.assertIn("Z", str(cm.exception))

    def test_bad_min_level_in_weights_raises(self):
        """如果 weights.json 被改錯,也要噴錯"""
        bad_W = copy.deepcopy(self.W)
        bad_W["sector_linkage"]["min_level"] = "ZZZ"
        with self.assertRaises(ValueError) as cm:
            score("X", "X", {
                "sector": "X", "sector_level": "A", "intl_activated": True,
            }, bad_W)
        self.assertIn("min_level", str(cm.exception))


# ─────────────────────────────────────────────────────────────────────────────
class TestEvidenceStructure(unittest.TestCase):
    """evidence 結構驗證"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_evidence_has_all_fields(self):
        _, d = score("TPEX:6223", "2026-05-26", {
            "sector":                  "半導體設備耗材",
            "sector_level":            "A",
            "intl_activated":          True,
            "intl_leaders_activated":  ["NASDAQ:ASML", "TSE:8035"],
        }, self.W)
        ev = d[0]["evidence"]
        self.assertEqual(ev["sector"], "半導體設備耗材")
        self.assertEqual(ev["sector_level"], "A")
        self.assertEqual(ev["activated_leaders"], ["NASDAQ:ASML", "TSE:8035"])

    def test_evidence_works_without_leader_list(self):
        """intl_leaders_activated 缺失時,activated_leaders 是空 list"""
        _, d = score("X", "X", {
            "sector": "X", "sector_level": "A", "intl_activated": True,
        }, self.W)
        self.assertEqual(d[0]["evidence"]["activated_leaders"], [])
