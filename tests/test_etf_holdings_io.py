"""test_etf_holdings_io.py — 新 chip_etf 資料源(etf_holdings.db PCF 快照)特徵計算。
2026-09-20 取代舊 etf_io(operations)。涵蓋:加碼/建倉/雙確認/減碼/點火/連續/
斷更歸零/防炸/NOLIST-vs-absent。窗口 date=09-14 → baseline 09-07、win [09-08,09-14]。"""
import os
import sys
import sqlite3
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.persistence import etf_holdings_io as hio

U = 100_000_000       # units_issued 固定 → spu ∝ shares
DATE = "2026-09-14"   # baseline = 2026-09-07


def hdb(*rows):
    """rows = (data_date, etf_code, stock_code, shares, weight_pct)。"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE etf_holdings ("
        "  data_date TEXT, etf_code TEXT, stock_code TEXT, stock_name TEXT,"
        "  shares INTEGER, weight_pct REAL, fund_nav REAL, units_issued INTEGER,"
        "  source TEXT, fetched_at TEXT,"
        "  PRIMARY KEY (data_date, etf_code, stock_code))"
    )
    for d, e, c, sh, w in rows:
        conn.execute("INSERT INTO etf_holdings VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (d, e, c, "n", sh, w, 0, U, "t", "x"))
    conn.commit()
    return conn


class TestFeatures(unittest.TestCase):

    def feat(self, conn, code="2330"):
        return hio.compute_etf_features(conn, f"TWSE:{code}", DATE)

    # ── 防炸 ──────────────────────────────────────────────────────────────
    def test_none_conn_empty(self):
        f = self.feat(None)
        self.assertEqual(f["buy_count"], 0)
        self.assertFalse(f["is_continuous_buy"] or f["is_abnormal_ignition"])

    def test_missing_table_empty(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE dummy(x)")
        self.assertFalse(hio.holdings_ready(conn))
        self.assertEqual(self.feat(conn)["buy_count"], 0)   # 不炸

    # ── 加碼 / 共識 ───────────────────────────────────────────────────────
    def test_two_etf_consensus(self):
        conn = hdb(
            ("2026-09-07", "00981A", "2330", 1_000_000, 1.0),
            ("2026-09-14", "00981A", "2330", 1_100_000, 1.5),   # spu+10% w+0.5
            ("2026-09-07", "00987A", "2330", 2_000_000, 2.0),
            ("2026-09-14", "00987A", "2330", 2_200_000, 2.5),
        )
        f = self.feat(conn)
        self.assertEqual(f["buy_count"], 2)
        self.assertEqual(f["buy_etfs"], ["00981A", "00987A"])

    def test_add_needs_both_spu_and_weight(self):
        """spu 達標但權重沒同向 +0.2pp → 不算加碼(雙確認)。"""
        conn = hdb(
            ("2026-09-07", "00981A", "2330", 1_000_000, 1.50),
            ("2026-09-14", "00981A", "2330", 1_100_000, 1.55),   # spu+10% 但 w 僅 +0.05
        )
        self.assertEqual(self.feat(conn)["buy_count"], 0)

    def test_spu_neutralizes_inflow(self):
        """股數漲但單位數等比例漲(純淨流入)→ spu 不變 → 不算加碼。"""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE etf_holdings (data_date TEXT, etf_code TEXT, stock_code TEXT,"
            " stock_name TEXT, shares INTEGER, weight_pct REAL, fund_nav REAL,"
            " units_issued INTEGER, source TEXT, fetched_at TEXT,"
            " PRIMARY KEY(data_date,etf_code,stock_code))")
        # 股數 +20%、單位數也 +20% → spu 不變(即使權重升也不算,spu 是主確認)
        conn.execute("INSERT INTO etf_holdings VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("2026-09-07", "00981A", "2330", "n", 1_000_000, 1.0, 0, 100_000_000, "t", "x"))
        conn.execute("INSERT INTO etf_holdings VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("2026-09-14", "00981A", "2330", "n", 1_200_000, 1.5, 0, 120_000_000, "t", "x"))
        conn.commit()
        self.assertEqual(self.feat(conn)["buy_count"], 0)   # spu 持平 → 非加碼

    # ── 建倉 / 點火 ───────────────────────────────────────────────────────
    def test_new_position_counts_as_buy(self):
        """baseline 未持有(ETF 已上市)→ 今日實倉 = 建倉。"""
        conn = hdb(
            ("2026-09-07", "00981A", "9999", 500_000, 3.0),      # filler:ETF 09-07 有揭露
            ("2026-09-14", "00981A", "2330", 800_000, 1.0),      # 2330 新建倉
        )
        f = self.feat(conn)
        self.assertEqual(f["buy_count"], 1)
        self.assertTrue(f["is_abnormal_ignition"])              # 單檔建倉 = 點火
        self.assertEqual(f["ignition_etf"], "00981A")

    def test_token_start_is_new(self):
        """baseline 是佔位(1000 股)→ 今日實倉 = 建倉。"""
        conn = hdb(
            ("2026-09-07", "00981A", "2330", 1_000, 0.00),
            ("2026-09-14", "00981A", "2330", 800_000, 1.0),
        )
        self.assertTrue(self.feat(conn)["is_abnormal_ignition"])

    def test_new_below_theta_new_rejected(self):
        """今日權重 < 0.2pp → 不算建倉。"""
        conn = hdb(
            ("2026-09-07", "00981A", "9999", 500_000, 3.0),
            ("2026-09-14", "00981A", "2330", 800_000, 0.1),     # w 0.1 < θ_new 0.2
        )
        self.assertEqual(self.feat(conn)["buy_count"], 0)

    def test_no_ignition_when_two_buys(self):
        """恰好 1 檔才點火;2 檔(含建倉)→ 非點火。"""
        conn = hdb(
            ("2026-09-07", "00981A", "9999", 500_000, 3.0),
            ("2026-09-14", "00981A", "2330", 800_000, 1.0),     # 建倉
            ("2026-09-07", "00987A", "2330", 2_000_000, 2.0),
            ("2026-09-14", "00987A", "2330", 2_200_000, 2.5),   # 加碼
        )
        f = self.feat(conn)
        self.assertEqual(f["buy_count"], 2)
        self.assertFalse(f["is_abnormal_ignition"])

    def test_nolist_not_false_new(self):
        """ETF 自己在 baseline 前未上市(NOLIST)→ 首次出現不算建倉。"""
        conn = hdb(
            ("2026-09-14", "00403A", "2330", 800_000, 1.0),     # 00403A 只有 09-14 有資料
        )
        f = self.feat(conn)
        self.assertEqual(f["buy_count"], 0)                    # NOLIST → 跳過,不假建倉

    # ── 減碼 ──────────────────────────────────────────────────────────────
    def test_trim_symmetric_and_tag(self):
        conn = hdb(
            ("2026-09-07", "00981A", "2330", 1_000_000, 1.5),
            ("2026-09-14", "00981A", "2330",   900_000, 1.2),   # spu-10% w-0.3
            ("2026-09-07", "00987A", "2330", 2_000_000, 2.5),
            ("2026-09-14", "00987A", "2330", 1_800_000, 2.2),
        )
        # 減碼不進 buy_count
        self.assertEqual(self.feat(conn)["buy_count"], 0)
        tags = hio.compute_etf_decrease_tag(conn, "TWSE:2330", DATE)
        self.assertEqual(len(tags), 1)
        self.assertIn("ETF 減碼", tags[0])

    def test_decrease_needs_two(self):
        conn = hdb(
            ("2026-09-07", "00981A", "2330", 1_000_000, 1.5),
            ("2026-09-14", "00981A", "2330",   900_000, 1.2),   # 只 1 檔減碼
        )
        self.assertEqual(hio.compute_etf_decrease_tag(conn, "TWSE:2330", DATE), [])

    # ── 連續加碼 ─────────────────────────────────────────────────────────
    def test_continuous(self):
        """窗口內 spu 多日續增 ≥2%(≥2 天)+ 有買訊 → 連續。"""
        conn = hdb(
            ("2026-09-07", "00981A", "2330", 1_000_000, 1.0),
            ("2026-09-10", "00981A", "2330", 1_050_000, 1.3),   # win 內
            ("2026-09-12", "00981A", "2330", 1_100_000, 1.5),   # +4.8%
            ("2026-09-14", "00981A", "2330", 1_160_000, 1.7),   # +5.5%
        )
        f = self.feat(conn)
        self.assertTrue(f["is_continuous_buy"])

    def test_not_continuous_single_snapshot(self):
        """窗口內只有一個快照 → 無續增天 → 非連續。"""
        conn = hdb(
            ("2026-09-07", "00981A", "2330", 1_000_000, 1.0),
            ("2026-09-14", "00981A", "2330", 1_100_000, 1.5),   # 加碼但窗口內僅 09-14
        )
        f = self.feat(conn)
        self.assertEqual(f["buy_count"], 1)
        self.assertFalse(f["is_continuous_buy"])

    # ── ★斷更歸零 ────────────────────────────────────────────────────────
    def test_freeze_to_zero(self):
        """資料凍結在窗口之前(>7 天)→ end==start → 全歸零,不冒充。"""
        conn = hdb(
            ("2026-09-01", "00981A", "2330", 1_000_000, 1.0),   # 最新只到 09-01
            ("2026-08-25", "00981A", "2330",   500_000, 0.6),
        )
        f = self.feat(conn)   # 對 09-14 算,窗口 [09-08,09-14] 無資料
        self.assertEqual(f["buy_count"], 0)
        self.assertFalse(f["is_continuous_buy"] or f["is_abnormal_ignition"])


if __name__ == "__main__":
    unittest.main()
