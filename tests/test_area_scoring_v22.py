"""
test_area_scoring_v22.py — D1(審計 2026-07-07):區域計分對齊 v2.2 §3-B。

規則原文:「區域類:K 棒碰到當天 +N(v2.2 起區域不需要兩天確認,碰到即算)」
§1-D 觸發 = K 棒與區域交集(K_low ≤ high AND K_high ≥ low,含影線觸及)。
語意(2026-07-08 起):交集當天即 +N;連續在區內只算首日;離開後再進入 = 新觸發。

本檔前四個測試 = 審計復現腳本轉正(region [95,105]、order_block 權重 2.0):
舊制 Day1 給 0.0(晚一天且私設「區間中點」門檻,情境 B 永不加分)——此為回歸防線。
"""
from __future__ import annotations
import os
import sqlite3
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import run_filters_v2
from src.persistence import state_io
from tests.test_run_filters_v2 import load_real_weights

SYMBOL = "TPEX:6223"          # 沿用旺矽 fixture 的 watchlist 身分
AREA = {"low": "95", "high": "105", "category": "order_block", "adjective": None}
KP = {"stocks": {SYMBOL: {"lines": [], "areas": [AREA]}}}
WATCHLIST = {
    "台股板塊": {"半導體設備耗材": {"成員": [{"code": SYMBOL, "name": "旺矽"}],
                                    "長子": [SYMBOL]}},
    "國際族群": {},
}
SECTORS = {"sectors": {"半導體設備耗材": "A"}}
ORDER_BLOCK_N = 2.0           # config/weights.json given_price.order_block


def _db(bars):
    """bars = [(date, o, h, l, c)];volume 統一 1M(vol_ratio≡1,不干擾分數)。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE kline (symbol TEXT, date TEXT, open REAL, high REAL,"
                 " low REAL, close REAL, volume REAL, PRIMARY KEY (symbol, date))")
    for d, o, h, l, c in bars:
        conn.execute("INSERT INTO kline VALUES (?,?,?,?,?,?,?)",
                     (SYMBOL, d, o, h, l, c, 1_000_000))
    conn.commit()
    return conn


class TestAreaV22(unittest.TestCase):
    def setUp(self):
        self.weights = load_real_weights()

    def _run(self, conn, date):
        return run_filters_v2.run_pipeline(
            date=date, conn_kline=conn, conn_etf=None, weights=self.weights,
            sectors=SECTORS, key_prices=KP, watchlist=WATCHLIST,
            now_iso=f"{date}T19:00:00+08:00")

    def _area_score(self, out):
        return sum(d["score"] for d in out["stocks"][SYMBOL]["details"]
                   if d["module"] == "given_price")

    def test_audit_repro_day1_intersect_scores_same_day(self):
        """審計復現①:Day1 K 棒交集且收在區內 → 當天 +2(舊制:0.0)。"""
        conn = _db([("2026-06-01", 100, 102, 98, 101)])       # 全在 [95,105] 內
        out = self._run(conn, "2026-06-01")
        self.assertAlmostEqual(self._area_score(out), ORDER_BLOCK_N)
        st = state_io.read_state(conn, SYMBOL, "order_block", "95-105")
        self.assertEqual(st["state"], "STANDING")
        self.assertEqual(st["standing_date"], "2026-06-01")
        # 事件 + C 級標籤當天成立
        self.assertTrue(any(e["type"] == "standing"
                            for e in out["stocks"][SYMBOL]["events"]))
        self.assertTrue(any("站穩 區域" in t
                            for t in out["stocks"][SYMBOL]["tags_today"]))
        conn.close()

    def test_audit_repro_scenario_b_below_midpoint_still_scores(self):
        """審計復現②(情境 B):整天在區域內但低於中點(open 96/close 97)
        → Day1 即 +2(舊制:兩天都 0,永不加分——「區間中點」私設門檻)。"""
        conn = _db([("2026-06-01", 96, 98, 95.5, 97),
                    ("2026-06-02", 96.5, 99, 96, 97.5)])
        out1 = self._run(conn, "2026-06-01")
        self.assertAlmostEqual(self._area_score(out1), ORDER_BLOCK_N)   # Day1 +2
        out2 = self._run(conn, "2026-06-02")
        self.assertAlmostEqual(self._area_score(out2), 0.0)   # 續留同一 episode 不重複計
        st = state_io.read_state(conn, SYMBOL, "order_block", "95-105")
        self.assertEqual(st["state"], "MAINTAINING")
        conn.close()

    def test_wick_touch_counts(self):
        """含影線觸及:下影線刺入區域上緣(close 在區外)也算交集 → +2。"""
        conn = _db([("2026-06-01", 108, 112, 103, 110)])      # K_low 103 ≤ 105
        out = self._run(conn, "2026-06-01")
        self.assertAlmostEqual(self._area_score(out), ORDER_BLOCK_N)
        conn.close()

    def test_leave_then_reenter_scores_again(self):
        """離開後再進入 = 新觸發,再 +N(§3-B「重新站上可以再 +N」同理)。"""
        conn = _db([("2026-06-01", 100, 102, 98, 101),        # 進入 +2
                    ("2026-06-02", 110, 115, 108, 112),       # 離開(K_low 108 > 105)
                    ("2026-06-03", 104, 106, 100, 103)])      # 再進入 → 再 +2
        self.assertAlmostEqual(self._area_score(self._run(conn, "2026-06-01")), ORDER_BLOCK_N)
        out2 = self._run(conn, "2026-06-02")
        self.assertAlmostEqual(self._area_score(out2), 0.0)
        self.assertEqual(state_io.read_state(conn, SYMBOL, "order_block", "95-105")["state"],
                         "UNTRIGGERED")
        out3 = self._run(conn, "2026-06-03")
        self.assertAlmostEqual(self._area_score(out3), ORDER_BLOCK_N)  # 新觸發再計
        conn.close()

    def test_legacy_triggered_row_treated_as_in_episode(self):
        """換制遷移:舊制 TRIGGERED(昨天碰到、等確認)視為 in-episode,
        今天續留不重複計分(歷史分數不重算,斷點以 commit 為準)。"""
        conn = _db([("2026-06-01", 100, 102, 98, 101),
                    ("2026-06-02", 100, 103, 99, 102)])
        state_io.init_schema(conn)
        state_io.write_state(conn, SYMBOL, "order_block", "95-105",
                             {"state": "TRIGGERED", "trigger_date": "2026-06-01",
                              "standing_date": None},
                             last_updated="t", last_evaluated_date="2026-06-01")
        out = self._run(conn, "2026-06-02")
        self.assertAlmostEqual(self._area_score(out), 0.0)     # 同一次進入不重複計
        self.assertEqual(state_io.read_state(conn, SYMBOL, "order_block", "95-105")["state"],
                         "MAINTAINING")
        conn.close()

    def test_same_day_rerun_idempotent(self):
        """同一日不重複計:重跑當天分數/狀態不變(W2-3 冪等涵蓋區域)。"""
        conn = _db([("2026-06-01", 100, 102, 98, 101)])
        s1 = self._area_score(self._run(conn, "2026-06-01"))
        s2 = self._area_score(self._run(conn, "2026-06-01"))
        self.assertAlmostEqual(s1, ORDER_BLOCK_N)
        self.assertAlmostEqual(s2, s1)
        self.assertEqual(state_io.read_state(conn, SYMBOL, "order_block", "95-105")["state"],
                         "STANDING")
        conn.close()


if __name__ == "__main__":
    unittest.main()
