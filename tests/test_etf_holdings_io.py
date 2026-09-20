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


class TestActiveSummaryAndEvents(unittest.TestCase):
    """顯示層(fetch_etf_active_summary / etf_events)與 chip_etf 同判定的一致性。"""

    WL = {"台股板塊": {"半導體": {"成員": [
        {"code": "TWSE:2330", "name": "台積電"},
        {"code": "TWSE:3661", "name": "世芯"},
    ]}}}

    def test_summary_decrease_2_etfs(self):
        """≥2 檔 ETF 減碼 → 進減碼區(3661 案例:00987A+00992A)"""
        conn = hdb(
            ("2026-09-07", "00987A", "3661", 1_000_000, 2.0),
            ("2026-09-14", "00987A", "3661",   850_000, 1.6),   # 減碼
            ("2026-09-07", "00992A", "3661", 2_000_000, 3.0),
            ("2026-09-14", "00992A", "3661", 1_700_000, 2.5),   # 減碼
        )
        out = hio.fetch_etf_active_summary(conn, DATE, self.WL)
        self.assertEqual(out["increase"], [])
        self.assertEqual(len(out["decrease"]), 1)
        d = out["decrease"][0]
        self.assertEqual(d["symbol"], "TWSE:3661")
        self.assertEqual(d["etf_count"], 2)
        self.assertEqual(d["etfs"], ["00987A", "00992A"])
        self.assertLess(d["total_shares"], 0)                   # 減碼張數為負

    def test_summary_matches_features_threshold(self):
        """summary 的加碼 ≥2 判定與 compute_etf_features(buy_count≥2)一致"""
        conn = hdb(
            ("2026-09-07", "00981A", "2330", 1_000_000, 1.0),
            ("2026-09-14", "00981A", "2330", 1_100_000, 1.5),
            ("2026-09-07", "00987A", "2330", 2_000_000, 2.0),
            ("2026-09-14", "00987A", "2330", 2_200_000, 2.5),
        )
        feat = hio.compute_etf_features(conn, "TWSE:2330", DATE)
        out = hio.fetch_etf_active_summary(conn, DATE, self.WL)
        inc = [x for x in out["increase"] if x["symbol"] == "TWSE:2330"]
        self.assertEqual(feat["buy_count"], 2)
        self.assertEqual(len(inc), 1)                           # summary 也列出
        self.assertEqual(inc[0]["etfs"], feat["buy_etfs"])      # ETF 清單一致

    def test_events_mark_action_start(self):
        """etf_events:動作起始日標一次,action 對齊前端(加碼/建倉/減碼)"""
        conn = hdb(
            ("2026-09-05", "00981A", "2330", 500_000, 0.5),
            ("2026-09-12", "00981A", "2330", 800_000, 1.2),     # 5→12 加碼
        )
        evs = hio.etf_events(conn, "TWSE:2330", "2026-09-01", "2026-09-14")
        self.assertTrue(any(e["action"] in ("加碼", "建倉") and e["etf"] == "00981A"
                            for e in evs))
        for e in evs:                                          # 欄位齊全
            self.assertEqual(set(e), {"time", "etf", "action", "shares"})

    def test_summary_missing_table_safe(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE dummy(x)")
        self.assertEqual(hio.fetch_etf_active_summary(conn, DATE, self.WL),
                         {"increase": [], "decrease": []})
        self.assertEqual(hio.etf_events(conn, "TWSE:2330", "2026-09-01", DATE), [])


if __name__ == "__main__":
    unittest.main()
