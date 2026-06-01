"""
test_chip_etf.py — ETF 籌碼計分單元測試

覆蓋:
  1. 共識加碼分流(0/1/2/3/4/6 檔)
  2. 連續加碼疊加(共存於共識)
  3. 異常點火(獨立觸發、跟連續可共存)
  4. 上限 4 分驗證
  5. details 結構與 evidence 欄位
  6. 真實情境 fixture(模擬 v1 load_data 產出的 dict)

執行:python3 -m unittest tests.test_chip_etf
"""
from __future__ import annotations
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.scoring.chip_etf import score


def load_weights() -> dict:
    with open(os.path.join(PROJECT_ROOT, "config", "weights.json"), encoding="utf-8") as f:
        return json.load(f)


# ── Fixture helpers ──────────────────────────────────────────────────────────
def make_etf_data(
    *,
    buy_count: int = 0,
    buy_etfs: list | None = None,
    is_continuous_buy: bool = False,
    is_abnormal_ignition: bool = False,
    ignition_etf: str | None = None,
    ignition_shares: int | None = None,
    today_volume: int | None = None,
) -> dict:
    """簡化測試 fixture 建構。"""
    return {
        "buy_count":            buy_count,
        "buy_etfs":             buy_etfs if buy_etfs is not None else [],
        "is_continuous_buy":    is_continuous_buy,
        "is_abnormal_ignition": is_abnormal_ignition,
        "ignition_etf":         ignition_etf,
        "ignition_shares":      ignition_shares,
        "today_volume":         today_volume,
    }


# ─────────────────────────────────────────────────────────────────────────────
class TestConsensusBuy(unittest.TestCase):
    """共識加碼分流"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_zero_etfs_no_score(self):
        s, d = score("2330", "2026-05-26", make_etf_data(buy_count=0), self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_one_etf_no_consensus(self):
        s, d = score("2330", "2026-05-26",
                     make_etf_data(buy_count=1, buy_etfs=["00981A"]), self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_two_etfs_consensus_2(self):
        s, d = score("2330", "2026-05-26",
                     make_etf_data(buy_count=2, buy_etfs=["00981A", "00987A"]),
                     self.W)
        self.assertEqual(s, 2)
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["score"], 2)
        self.assertIn("≥ 2 檔", d[0]["reason"])

    def test_three_etfs_still_consensus_2(self):
        """3 檔仍是 +2(未達 ≥ 4)"""
        s, _ = score("2330", "2026-05-26",
                     make_etf_data(buy_count=3,
                                   buy_etfs=["00981A", "00987A", "00994A"]),
                     self.W)
        self.assertEqual(s, 2)

    def test_four_etfs_consensus_4_takes_over(self):
        """4 檔升級成 +3,不是 +2+1"""
        s, d = score("2330", "2026-05-26",
                     make_etf_data(buy_count=4,
                                   buy_etfs=["00981A", "00987A", "00994A", "00992A"]),
                     self.W)
        self.assertEqual(s, 3)
        self.assertEqual(len(d), 1)   # 只觸發強共識,不疊加
        self.assertEqual(d[0]["score"], 3)
        self.assertIn("≥ 4 檔", d[0]["reason"])

    def test_six_etfs_capped_at_consensus_4(self):
        """6 檔仍是 +3,不再向上疊加"""
        s, _ = score("2330", "2026-05-26",
                     make_etf_data(buy_count=6,
                                   buy_etfs=["A", "B", "C", "D", "E", "F"]),
                     self.W)
        self.assertEqual(s, 3)


# ─────────────────────────────────────────────────────────────────────────────
class TestContinuous(unittest.TestCase):
    """連續加碼:+1,跟共識可共存"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_continuous_with_consensus_2(self):
        s, d = score("2330", "2026-05-26",
                     make_etf_data(buy_count=2,
                                   buy_etfs=["00981A", "00987A"],
                                   is_continuous_buy=True),
                     self.W)
        self.assertEqual(s, 3)   # 2 + 1
        self.assertEqual(len(d), 2)

    def test_continuous_with_consensus_4_hits_max(self):
        """+3 共識 + +1 連續 = 4,本系統上限"""
        s, _ = score("2330", "2026-05-26",
                     make_etf_data(buy_count=4,
                                   buy_etfs=["A", "B", "C", "D"],
                                   is_continuous_buy=True),
                     self.W)
        self.assertEqual(s, 4)

    def test_continuous_no_consensus(self):
        """理論上不太可能(連續加碼需要 ETF 有買),但驗證 chip_etf 不阻擋這個輸入"""
        s, _ = score("2330", "2026-05-26",
                     make_etf_data(buy_count=0, is_continuous_buy=True), self.W)
        self.assertEqual(s, 1)


# ─────────────────────────────────────────────────────────────────────────────
class TestIgnition(unittest.TestCase):
    """異常點火:+1,沿用 v1 嚴格定義(恰好 1 檔)"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_ignition_alone(self):
        """1 檔 ETF + 點火 → +1(共識未達)"""
        s, d = score("2330", "2026-05-26",
                     make_etf_data(buy_count=1,
                                   buy_etfs=["00981A"],
                                   is_abnormal_ignition=True,
                                   ignition_etf="00981A",
                                   ignition_shares=300,
                                   today_volume=2000),
                     self.W)
        self.assertEqual(s, 1)
        self.assertEqual(len(d), 1)
        ev = d[0]["evidence"]
        self.assertEqual(ev["etf"], "00981A")
        self.assertEqual(ev["ratio"], 0.15)   # 300 / 2000

    def test_ignition_plus_continuous(self):
        """連續 + 點火 共存(共識未達)→ 2 分"""
        s, _ = score("2330", "2026-05-26",
                     make_etf_data(buy_count=1,
                                   buy_etfs=["00981A"],
                                   is_continuous_buy=True,
                                   is_abnormal_ignition=True,
                                   ignition_etf="00981A",
                                   ignition_shares=400,
                                   today_volume=3000),
                     self.W)
        self.assertEqual(s, 2)

    def test_ignition_with_partial_evidence(self):
        """evidence 欄位缺失也能算分,evidence dict 只含已知"""
        s, d = score("2330", "2026-05-26",
                     make_etf_data(buy_count=1,
                                   buy_etfs=["00981A"],
                                   is_abnormal_ignition=True),
                     self.W)
        self.assertEqual(s, 1)
        # evidence 缺失 → 應為 None
        self.assertIsNone(d[0]["evidence"])


# ─────────────────────────────────────────────────────────────────────────────
class TestMaxScore(unittest.TestCase):
    """最高分 +4 驗證"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_max_consensus_4_plus_continuous(self):
        s, _ = score("2330", "2026-05-26",
                     make_etf_data(buy_count=5,
                                   buy_etfs=["A", "B", "C", "D", "E"],
                                   is_continuous_buy=True),
                     self.W)
        self.assertEqual(s, 4)

    def test_consensus_2_plus_continuous_plus_ignition_theoretically_4(self):
        """退化情境:caller 同時 mark consensus + ignition(實務上 v1 不會這樣產出)。
        chip_etf.py 不檢查衝突,信 caller 的 dict → 2+1+1 = 4"""
        s, _ = score("2330", "2026-05-26",
                     make_etf_data(buy_count=2,
                                   buy_etfs=["A", "B"],
                                   is_continuous_buy=True,
                                   is_abnormal_ignition=True,
                                   ignition_etf="A",
                                   ignition_shares=500,
                                   today_volume=4000),
                     self.W)
        self.assertEqual(s, 4)


# ─────────────────────────────────────────────────────────────────────────────
class TestDetailsFormat(unittest.TestCase):
    """details 結構驗證(網站 hover tooltip 用)"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_details_has_reason_score_evidence(self):
        _, d = score("2330", "2026-05-26",
                     make_etf_data(buy_count=2,
                                   buy_etfs=["00981A", "00987A"]),
                     self.W)
        self.assertEqual(set(d[0].keys()), {"reason", "score", "evidence"})

    def test_consensus_evidence_is_etf_list(self):
        _, d = score("2330", "2026-05-26",
                     make_etf_data(buy_count=2,
                                   buy_etfs=["00981A", "00987A"]),
                     self.W)
        self.assertEqual(d[0]["evidence"], ["00981A", "00987A"])

    def test_ignition_evidence_has_ratio(self):
        _, d = score("2330", "2026-05-26",
                     make_etf_data(buy_count=1,
                                   buy_etfs=["00981A"],
                                   is_abnormal_ignition=True,
                                   ignition_etf="00981A",
                                   ignition_shares=350,
                                   today_volume=2000),
                     self.W)
        ev = d[0]["evidence"]
        self.assertEqual(ev["ratio"], 0.175)
        self.assertEqual(ev["shares"], 350)


# ─────────────────────────────────────────────────────────────────────────────
class TestRealWorldScenarios(unittest.TestCase):
    """模擬幾個典型情境(從近期實際資料抽出來的形狀)"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_weights()

    def test_hot_stock_3_etfs_with_continuity(self):
        """旺矽情境(模擬):3 檔 ETF 買 + 連續 → 共識 +2 + 連續 +1 = 3"""
        s, d = score("TPEX:6223", "2026-05-20",
                     make_etf_data(buy_count=3,
                                   buy_etfs=["00981A", "00987A", "00994A"],
                                   is_continuous_buy=True),
                     self.W)
        self.assertEqual(s, 3)
        self.assertEqual([item["score"] for item in d], [2, 1])

    def test_lone_ignition_no_consensus(self):
        """單一 ETF 大買 → 共識未達(0)+ 點火 +1 = 1"""
        s, _ = score("TWSE:2330", "2026-05-26",
                     make_etf_data(buy_count=1,
                                   buy_etfs=["00992A"],
                                   is_abnormal_ignition=True,
                                   ignition_etf="00992A",
                                   ignition_shares=1500,
                                   today_volume=8000),
                     self.W)
        self.assertEqual(s, 1)

    def test_no_etf_activity(self):
        """完全沒 ETF 活動:0 分,details 空"""
        s, d = score("TWSE:2330", "2026-05-26", make_etf_data(), self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])


if __name__ == "__main__":
    unittest.main()
