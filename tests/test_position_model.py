"""
test_position_model.py — 計分引擎(spec §3:每條規則一正一反、10Y 速度三情境、
遲滯切檔邊界、反未來函數斷言)
"""
from __future__ import annotations
import copy
import os
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import position_model as pm
from src.fetch_signals import ensure_tables

CFG = pm.load_cfg()


def make_db(case) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    case.addCleanup(os.unlink, f.name)
    conn = sqlite3.connect(f.name)
    ensure_tables(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS dff_daily ("
                 "date TEXT PRIMARY KEY, rate REAL, source TEXT)")
    conn.commit()
    conn.close()
    return f.name


def fill(db, table, rows):
    conn = sqlite3.connect(db)
    q = ",".join("?" * len(rows[0]))
    conn.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({q})", rows)
    conn.commit()
    conn.close()


class Base(unittest.TestCase):
    """最小完整訊號組(單日 2026-01-10 可算)。"""

    def setUp(self):
        self.db = make_db(self)
        d = "2026-01-10"
        fill(self.db, "idx_daily",
             [("TAIEX", d, 21000, 20000, 19000, 18000, "t"),      # 全站上
              ("SPX", d, 6000, 5800, 5600, 5400, "t")])
        fill(self.db, "vix_daily", [(d, 14.0, None, "t")])         # <15 → +1
        fill(self.db, "umich_monthly",
             [(f"2025-{m:02d}", 50.0 + m, f"2025-{m:02d}-25", "t") for m in range(1, 11)]
             + [("2025-11", 99.0, "2025-12-24", "t")])             # 最新=最高 → >80% → −1
        fill(self.db, "light_monthly", [("2025-11", 23, "綠", "2025-12-29", "t")])
        fill(self.db, "dgs10_daily", [(d, 4.0, "t")])              # 3.5~4.5 → 0
        fill(self.db, "usdtwd_daily",
             [(f"2025-12-{dd:02d}", 32.0, "t") for dd in range(1, 29)]
             + [(d, 32.0, "t")])                                   # 平 → 0
        fill(self.db, "cpi_events",
             [("2025-11", "2025-12-10", 0.25, 0.20, 0.05, "t")])   # |0.05|≤0.1 → 0
        fill(self.db, "fomc_meetings",
             [("2025-12-10", "2025-12-09", 1, 3.75, 3.75, 0.0, 0.0, "t")])
        self.date = d

    def data(self):
        return pm.SignalData(self.db)


class TestTechnical(Base):
    def test_all_above(self):
        s, det = pm.score_technical(self.data(), CFG, self.date, {})
        self.assertEqual(s, 2.0)                   # 0.5+1+0.5 兩市場同 → 平均 2.0

    def test_all_below(self):
        fill(self.db, "idx_daily",
             [("TAIEX", self.date, 17000, 20000, 19000, 18000, "t")])
        s, _ = pm.score_technical(self.data(), CFG, self.date, {})
        self.assertEqual(s, 0.0)                   # 台 −2、美 +2 → 平均 0

    def test_buffer_carries_previous_side(self):
        # 前一日站上 → 今日跌到帶內(MA20 −0.3%)→ 沿用 above
        state = {}
        pm.score_technical(self.data(), CFG, self.date, state)
        fill(self.db, "idx_daily",
             [("TAIEX", "2026-01-11", 19940, 20000, 19000, 18000, "t"),
              ("SPX", "2026-01-11", 6000, 5800, 5600, 5400, "t")])
        s, det = pm.score_technical(self.data(), CFG, "2026-01-11", state)
        self.assertEqual(det["TAIEX"], 2.0)        # −0.3% 在 ±0.5% 帶內 → 不翻空

    def test_buffer_break_flips(self):
        state = {}
        pm.score_technical(self.data(), CFG, self.date, state)
        fill(self.db, "idx_daily",
             [("TAIEX", "2026-01-11", 19800, 20000, 19000, 18000, "t"),
              ("SPX", "2026-01-11", 6000, 5800, 5600, 5400, "t")])
        s, det = pm.score_technical(self.data(), CFG, "2026-01-11", state)
        self.assertEqual(det["TAIEX"], 1.0)        # −1% 破帶 → MA20 翻 −0.5(2−1)

    def test_stale_market_dropped(self):
        # SPX 舊於 7 日 → 只算台股(不冒充)
        fill(self.db, "idx_daily",
             [("SPX", "2025-12-20", 6000, 5800, 5600, 5400, "t")])
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM idx_daily WHERE market='SPX' AND date=?", (self.date,))
        conn.commit(); conn.close()
        s, det = pm.score_technical(self.data(), CFG, self.date, {})
        self.assertEqual(list(det), ["TAIEX"])


class TestSentiment(Base):
    def test_low_vix_high_umich(self):
        s, det = pm.score_sentiment(self.data(), CFG, self.date)
        # VIX 14 → +1(×2/3);密大 99 = 當日視角最高 → >80% → −1(×1/3)
        self.assertAlmostEqual(s, 0.6667 * 1 + 0.3333 * -1, places=3)
        self.assertFalse(det["warning"])

    def test_vix_over_40_warning_not_lower(self):
        fill(self.db, "vix_daily", [(self.date, 82.7, None, "t")])
        s, det = pm.score_sentiment(self.data(), CFG, self.date)
        self.assertEqual(det["vix_score"], -2)     # >40 不再降檔
        self.assertTrue(det["warning"])            # 旗標即時

    def test_umich_low_percentile_contrarian(self):
        fill(self.db, "umich_monthly", [("2025-12", 30.0, "2025-12-31", "t")])
        s, det = pm.score_sentiment(self.data(), CFG, self.date)
        self.assertEqual(det["umich_score"], 1)    # 最低 → <20% → +1

    def test_percentile_uses_only_released(self):
        # 未來月(release 在查詢日後)不得進百分位窗口
        fill(self.db, "umich_monthly", [("2026-01", 1.0, "2026-01-23", "t")])
        s, det = pm.score_sentiment(self.data(), CFG, self.date)
        self.assertEqual(det["umich"], 99.0)       # 仍是 2025-11 值


class TestCycle(Base):
    def test_inline(self):
        s, det = pm.score_cycle(self.data(), CFG, self.date)
        self.assertEqual(s, 0)

    def test_hot_and_double(self):
        fill(self.db, "cpi_events",
             [("2025-11", "2025-12-10", 0.55, 0.20, 0.35, "t")])   # +0.35 ≥ 0.3 → −1×2
        s, _ = pm.score_cycle(self.data(), CFG, self.date)
        self.assertEqual(s, -2)

    def test_cool(self):
        fill(self.db, "cpi_events",
             [("2025-11", "2025-12-10", 0.05, 0.20, -0.15, "t")])
        s, _ = pm.score_cycle(self.data(), CFG, self.date)
        self.assertEqual(s, 1)

    def test_locked_until_release(self):
        # 下次公布日 2026-01-13(> 查詢日)→ 仍用 2025-12-10 那筆
        fill(self.db, "cpi_events",
             [("2025-12", "2026-01-13", 0.9, 0.2, 0.7, "t")])
        s, det = pm.score_cycle(self.data(), CFG, self.date)
        self.assertEqual(det["cpi_month"], "2025-11")


class TestMacro(Base):
    def test_light_positive_negative(self):
        s, det = pm.score_macro(self.data(), CFG, self.date, {}, {})
        self.assertIn("綠(+1)", det["light"])
        fill(self.db, "light_monthly", [("2025-11", 40, "紅", "2025-12-29", "t")])
        s2, det2 = pm.score_macro(self.data(), CFG, self.date, {}, {})
        self.assertIn("紅(-1)", det2["light"])
        self.assertLess(s2, s)

    def test_usdtwd_crash(self):
        fill(self.db, "usdtwd_daily", [(self.date, 33.0, "t")])    # 32→33 = +3.1% 急貶
        s, det = pm.score_macro(self.data(), CFG, self.date, {}, {})
        self.assertIn("(-2)", det["usdtwd"])

    def test_usdtwd_appreciate(self):
        fill(self.db, "usdtwd_daily", [(self.date, 31.4, "t")])    # −1.9% 升值
        s, det = pm.score_macro(self.data(), CFG, self.date, {}, {})
        self.assertIn("(+1)", det["usdtwd"])

    def test_fedwatch_daily_and_surprise(self):
        fill(self.db, "fed_expectations_daily",
             [(self.date, "2026-01-28", 3.63, 3.35, -28.0, "{}", "t")])
        fill(self.db, "fomc_meetings",
             [("2025-12-10", "2025-12-09", 1, 4.0, 3.75, -25.0, 15.0, "t")])
        s, det = pm.score_macro(self.data(), CFG, self.date, {}, {})
        # 平日 dovish(−28 ≤ −12.5)→ +1;surprise = −25 − 15 = −40 ≥1碼偏鴿 → +1
        self.assertIn("dovish(+1)", det["fedwatch"])
        self.assertIn("surprise(+1)", det["fedwatch"])

    def test_fedwatch_surprise_null_baseline_skipped(self):
        fill(self.db, "fomc_meetings",
             [("2025-12-10", "2025-12-09", 1, 4.0, 3.75, -25.0, None, "t")])
        s, det = pm.score_macro(self.data(), CFG, self.date, {}, {})
        self.assertNotIn("surprise", det["fedwatch"])   # 無基準 → 不判定(誠實 N/A)

    def test_fedwatch_replay_lock(self):
        # 無當日期望值 → 沿用 state 內鎖定值(方案 a)
        state = {"fed_daily_bp": 20.0}
        s, det = pm.score_macro(self.data(), CFG, self.date, state, {})
        self.assertIn("hawkish(-1)", det["fedwatch"])


class TestDgs10Speed(Base):
    """10Y 速度組合三情境(spec §3 指定)。"""

    def _rise(self):
        fill(self.db, "dgs10_daily",
             [("2025-12-10", 4.1, "t"), (self.date, 4.6, "t")])    # 月升 50bp,4.6 → −1 檔

    def test_double_when_cpi_hot_and_hawkish(self):
        self._rise()
        fill(self.db, "fed_expectations_daily",
             [(self.date, "2026-01-28", 4.6, 4.85, 25.0, "{}", "t")])
        cyc = {"cpi_surprise_pp": 0.2}
        s, det = pm.score_macro(self.data(), CFG, self.date, {}, cyc)
        self.assertIn("速度加倍", det["dgs10"])
        self.assertIn("(-2", det["dgs10"])         # −1 × 2

    def test_exempt_when_mild_and_hold(self):
        self._rise()
        cyc = {"cpi_surprise_pp": 0.0}
        s, det = pm.score_macro(self.data(), CFG, self.date, {}, cyc)
        self.assertIn("速度豁免", det["dgs10"])
        self.assertIn("(-1", det["dgs10"])         # 維持 base

    def test_neutral_mixed_combo(self):
        self._rise()
        fill(self.db, "fed_expectations_daily",
             [(self.date, "2026-01-28", 4.6, 4.85, 25.0, "{}", "t")])   # 鷹但 CPI 溫和
        cyc = {"cpi_surprise_pp": 0.0}
        s, det = pm.score_macro(self.data(), CFG, self.date, {}, cyc)
        self.assertIn("速度中性", det["dgs10"])
        self.assertIn("(-1", det["dgs10"])


class TestHysteresis(unittest.TestCase):
    """遲滯切檔邊界:連續 3 交易日才切;第 2 日折返不切。"""

    def _mk(self, totals):
        """以可控 total 序列驅動 run():用單一訊號(技術面)+權重歸一。"""
        db = make_db(self)
        rows_idx, rows_vix, rows_tw = [], [], []
        base = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05",
                "2026-03-06", "2026-03-09", "2026-03-10"]
        for i, d in enumerate(base[:len(totals)]):
            close = 21000 if totals[i] > 0 else 17000     # ±2 tech
            rows_idx += [("TAIEX", d, close, 20000, 19500, 19000, "t"),
                         ("SPX", d, close, 20000, 19500, 19000, "t")]
            rows_vix.append((d, 17.0, None, "t"))          # 0 分
            rows_tw.append((d, 32.0, "t"))
        fill(db, "idx_daily", rows_idx)
        fill(db, "vix_daily", rows_vix)
        fill(db, "usdtwd_daily", rows_tw)
        fill(db, "umich_monthly", [("2026-01", 50, "2026-01-23", "t"),
                                   ("2026-02", 55, "2026-02-20", "t")])
        fill(db, "light_monthly", [("2025-12", 20, "黃紅", "2026-01-27", "t")])
        fill(db, "dgs10_daily", [("2026-03-01", 4.0, "t")])
        fill(db, "cpi_events", [("2026-01", "2026-02-11", 0.2, 0.2, 0.0, "t")])
        fill(db, "fomc_meetings", [("2026-01-28", "2026-01-27", 1, 3.75, 3.75, 0.0, 0.0, "t")])
        return db

    def test_three_day_switch(self):
        db = self._mk([2, -2, -2, -2, -2])
        rows = pm.run("2026-03-01", "2026-03-31", db_path=db)
        bands = [r["band"] for r in rows]
        self.assertEqual(bands[0], bands[1])       # 第 1 天不切
        self.assertEqual(bands[0], bands[2])       # 第 2 天不切
        self.assertNotEqual(bands[0], bands[3])    # 第 3 天切
        self.assertEqual(rows[3]["entered"], rows[3]["date"])

    def test_bounce_back_resets(self):
        db = self._mk([2, -2, 2, -2, 2])
        rows = pm.run("2026-03-01", "2026-03-31", db_path=db)
        self.assertEqual(len(set(r["band"] for r in rows)), 1)   # 折返 → 永不切


class TestNoLookahead(Base):
    def test_release_date_respected(self):
        # 燈號 12 月值 release 2026-01-27(> 查詢日 01-10)→ 不得使用
        fill(self.db, "light_monthly", [("2025-12", 45, "紅", "2026-01-27", "t")])
        s, det = pm.score_macro(self.data(), CFG, self.date, {}, {})
        self.assertIn("2025-11", det["light"])

    def test_assert_raises_on_violation(self):
        data = self.data()
        with self.assertRaises(AssertionError):
            data.assert_no_lookahead("2026-02-01", "2026-01-10", "test")


if __name__ == "__main__":
    unittest.main()
