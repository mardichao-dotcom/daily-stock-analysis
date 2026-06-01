"""
test_etf_io.py — etf_io 單元測試 + v1 parity(W2.2.1)

兩塊覆蓋:
  1. 行為測試:fixture etf_operations.db → 預期 features
  2. **v1 parity**:同 fixture 跑 v1 跟 v2,確認 booleans + counts 一致

v1 parity 是 W2.2 的重點 — 「沿用 v1」的承諾必須有測試把關。
test_v1_parity_* 系列直接 import v1 src/load_data._load_etf_data
(僅在 test 內 import,src/ 不依賴 v1)。

執行:python3 -m unittest tests.test_etf_io
"""
from __future__ import annotations
import os
import sqlite3
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.persistence import etf_io


# ── fixture ──────────────────────────────────────────────────────────────────

def fresh_etf_db() -> sqlite3.Connection:
    """In-memory etf_operations.db,空 operations 表"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE operations ("
        "  etf TEXT, 代號 TEXT, 日期 TEXT, 動作 TEXT, 張數 INTEGER"
        ")"
    )
    return conn


def insert_ops(conn: sqlite3.Connection, *ops):
    """ops: tuples of (etf, code, date, action, shares)"""
    for o in ops:
        conn.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?)", o)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
class TestComputeEtfFeatures(unittest.TestCase):
    """基本行為測試"""

    def setUp(self):
        self.conn = fresh_etf_db()

    def tearDown(self):
        self.conn.close()

    def test_no_data_returns_zero_features(self):
        feats = etf_io.compute_etf_features(self.conn, "TPEX:6223", "2026-05-20",
                                             today_volume=1000)
        self.assertEqual(feats["buy_count"], 0)
        self.assertEqual(feats["buy_etfs"], [])
        self.assertFalse(feats["is_continuous_buy"])
        self.assertFalse(feats["is_abnormal_ignition"])

    def test_one_etf_one_day_no_consensus(self):
        insert_ops(self.conn, ("00981A", "6223", "2026-05-20", "加碼", 100))
        feats = etf_io.compute_etf_features(self.conn, "TPEX:6223", "2026-05-20",
                                             today_volume=2000)
        self.assertEqual(feats["buy_count"], 1)
        self.assertEqual(feats["buy_etfs"], ["00981A"])
        self.assertFalse(feats["is_continuous_buy"])   # 只有一天
        # 100 / 2000 = 5% < 10% → 不點火
        self.assertFalse(feats["is_abnormal_ignition"])

    def test_two_etfs_one_day_consensus(self):
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-20", "加碼", 100),
                   ("00987A", "6223", "2026-05-20", "加碼",  50))
        feats = etf_io.compute_etf_features(self.conn, "TPEX:6223", "2026-05-20",
                                             today_volume=2000)
        self.assertEqual(feats["buy_count"], 2)
        self.assertFalse(feats["is_continuous_buy"])   # 只有一天
        self.assertFalse(feats["is_abnormal_ignition"])  # 共識下不點火

    def test_continuous_buy_two_days(self):
        """最新天 + 早一天都有 ETF 買 → continuous"""
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-15", "加碼", 100),
                   ("00987A", "6223", "2026-05-20", "加碼",  50))
        feats = etf_io.compute_etf_features(self.conn, "TPEX:6223", "2026-05-20",
                                             today_volume=2000)
        self.assertEqual(feats["buy_count"], 2)
        self.assertTrue(feats["is_continuous_buy"])   # 跨天 ETF 活動

    def test_same_etf_same_day_only_no_continuous(self):
        """同一天同一檔多次加碼 → 不算 continuous"""
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-20", "加碼", 100),
                   ("00981A", "6223", "2026-05-20", "加碼",  50))
        feats = etf_io.compute_etf_features(self.conn, "TPEX:6223", "2026-05-20",
                                             today_volume=2000)
        self.assertEqual(feats["buy_count"], 1)
        self.assertFalse(feats["is_continuous_buy"])   # 只有一天

    def test_ignition_triggered(self):
        """單一 ETF + 累計買超 > 10% volume → ignition"""
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-20", "加碼", 250))
        feats = etf_io.compute_etf_features(self.conn, "TPEX:6223", "2026-05-20",
                                             today_volume=2000)   # 250/2000=12.5%
        self.assertTrue(feats["is_abnormal_ignition"])
        self.assertEqual(feats["ignition_etf"], "00981A")
        self.assertEqual(feats["ignition_shares"], 250)

    def test_ignition_exact_10_percent_not_triggered(self):
        """剛好 10% → 不觸發(strict >)"""
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-20", "加碼", 200))
        feats = etf_io.compute_etf_features(self.conn, "TPEX:6223", "2026-05-20",
                                             today_volume=2000)   # 200/2000=10%
        self.assertFalse(feats["is_abnormal_ignition"])

    def test_ignition_requires_exactly_one_etf(self):
        """2 檔 ETF 即使單檔 > 10% volume 也不觸發點火(v1 嚴格定義)"""
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-20", "加碼", 300),
                   ("00987A", "6223", "2026-05-20", "加碼",  50))
        feats = etf_io.compute_etf_features(self.conn, "TPEX:6223", "2026-05-20",
                                             today_volume=2000)
        self.assertFalse(feats["is_abnormal_ignition"])
        self.assertIsNone(feats["ignition_etf"])

    def test_strip_exchange_prefix(self):
        """符合 5A schema:operations 表用無 prefix 代號"""
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-20", "加碼", 100))
        # 'TPEX:6223' 跟 'TWSE:6223' 都該查到同一筆(只看 :6223 部分)
        for sym in ("TPEX:6223", "TWSE:6223", "6223"):
            feats = etf_io.compute_etf_features(self.conn, sym, "2026-05-20",
                                                 today_volume=2000)
            self.assertEqual(feats["buy_count"], 1, msg=f"failed for {sym}")

    def test_7_day_window_boundary(self):
        """date - 6 自然日 (含當日) 才算 in-window"""
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-13", "加碼", 100),  # 邊界 in
                   ("00987A", "6223", "2026-05-12", "加碼", 100),  # 邊界 out
                   ("00992A", "6223", "2026-05-19", "加碼", 100))  # in
        feats = etf_io.compute_etf_features(self.conn, "TPEX:6223", "2026-05-19",
                                             today_volume=2000)
        # 5/13 (5/19 - 6 days) IN, 5/12 OUT
        self.assertEqual(set(feats["buy_etfs"]), {"00981A", "00992A"})

    def test_sell_actions_dont_count_as_buy(self):
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-20", "減碼", 100))
        feats = etf_io.compute_etf_features(self.conn, "TPEX:6223", "2026-05-20",
                                             today_volume=2000)
        self.assertEqual(feats["buy_count"], 0)


# ─────────────────────────────────────────────────────────────────────────────
class TestFetchEtfActiveSummary(unittest.TestCase):
    """W3 區塊 6 ETF 主動式雙向掃描資料"""

    def _watchlist(self):
        return {
            "台股板塊": {
                "S1": {
                    "成員": [
                        {"code": "TPEX:6223", "name": "旺矽"},
                        {"code": "TWSE:2330", "name": "台積電"},
                        {"code": "TWSE:2308", "name": "台達電"},
                    ],
                    "長子": [],
                },
            },
            "國際族群": {},
        }

    def test_basic_increase_and_decrease(self):
        """2 個 ETF 加碼 → increase / 2 個 ETF 減碼 → decrease"""
        conn = fresh_etf_db()
        insert_ops(conn,
                   ("00981A", "6223", "2026-05-20", "加碼", 100),
                   ("00987A", "6223", "2026-05-20", "加碼", 180),
                   ("00981A", "2308", "2026-05-20", "減碼", 200),
                   ("00987A", "2308", "2026-05-20", "減碼", 250))
        result = etf_io.fetch_etf_active_summary(
            conn, "2026-05-20", self._watchlist(),
        )
        self.assertEqual(len(result["increase"]), 1)
        self.assertEqual(result["increase"][0]["symbol"], "TPEX:6223")
        self.assertEqual(result["increase"][0]["etf_count"], 2)
        self.assertEqual(result["increase"][0]["total_shares"], 280)
        self.assertEqual(len(result["decrease"]), 1)
        self.assertEqual(result["decrease"][0]["symbol"], "TWSE:2308")
        self.assertEqual(result["decrease"][0]["total_shares"], -450)
        conn.close()

    def test_only_1_etf_no_entry(self):
        """單一 ETF 不夠 ≥2,不入 list"""
        conn = fresh_etf_db()
        insert_ops(conn, ("00981A", "6223", "2026-05-20", "加碼", 100))
        result = etf_io.fetch_etf_active_summary(
            conn, "2026-05-20", self._watchlist(),
        )
        self.assertEqual(result["increase"], [])
        self.assertEqual(result["decrease"], [])
        conn.close()

    def test_filters_to_watchlist(self):
        """非 watchlist 內個股,即使 ≥2 ETF 加碼也不列"""
        conn = fresh_etf_db()
        insert_ops(conn,
                   ("00981A", "9999", "2026-05-20", "加碼", 100),
                   ("00987A", "9999", "2026-05-20", "加碼", 100))
        result = etf_io.fetch_etf_active_summary(
            conn, "2026-05-20", self._watchlist(),
        )
        self.assertEqual(result["increase"], [])
        conn.close()

    def test_sorting_etf_count_desc_then_shares_desc(self):
        """排序:先 etf_count 降冪、再 |total_shares| 降冪"""
        conn = fresh_etf_db()
        # 旺矽 3 檔 ETF 加碼共 300 張
        insert_ops(conn,
                   ("00981A", "6223", "2026-05-20", "加碼", 100),
                   ("00987A", "6223", "2026-05-20", "加碼", 100),
                   ("00992A", "6223", "2026-05-20", "加碼", 100))
        # 台積電 2 檔 ETF 加碼共 1000 張(雖然張數多但 etf_count 少)
        insert_ops(conn,
                   ("00981A", "2330", "2026-05-20", "加碼", 500),
                   ("00987A", "2330", "2026-05-20", "加碼", 500))
        result = etf_io.fetch_etf_active_summary(
            conn, "2026-05-20", self._watchlist(),
        )
        # 旺矽應該排第一(etf_count=3 > 2)
        self.assertEqual(result["increase"][0]["symbol"], "TPEX:6223")
        self.assertEqual(result["increase"][1]["symbol"], "TWSE:2330")
        conn.close()

    def test_includes_qingcang_jiancang(self):
        """清倉算減碼、建倉算加碼(跟 v1 BUY_ACTIONS / SELL_ACTIONS 一致)"""
        conn = fresh_etf_db()
        insert_ops(conn,
                   ("00981A", "6223", "2026-05-20", "建倉", 200),
                   ("00987A", "6223", "2026-05-20", "加碼", 100),
                   ("00981A", "2308", "2026-05-20", "清倉", 150),
                   ("00987A", "2308", "2026-05-20", "減碼", 200))
        result = etf_io.fetch_etf_active_summary(
            conn, "2026-05-20", self._watchlist(),
        )
        # 旺矽建倉+加碼 → +300
        self.assertEqual(result["increase"][0]["total_shares"], 300)
        # 台達電清倉+減碼 → -350
        self.assertEqual(result["decrease"][0]["total_shares"], -350)
        conn.close()

    # ── 2026-05-31 朋友確認:窗口期改 7 日累計 ──

    def test_window_excludes_outside_dates(self):
        """7 日窗口外的 operations 不入(end_date=5/20 → 窗口 5/14~5/20)"""
        conn = fresh_etf_db()
        insert_ops(conn,
                   # 5/13 在窗口外(5/20 - 6 = 5/14 為起點)
                   ("00981A", "6223", "2026-05-13", "加碼", 100),
                   ("00987A", "6223", "2026-05-13", "加碼", 100),
                   # 5/14 起算窗口內
                   ("00981A", "2308", "2026-05-14", "減碼", 200),
                   ("00987A", "2308", "2026-05-20", "減碼", 250))
        result = etf_io.fetch_etf_active_summary(
            conn, "2026-05-20", self._watchlist(),
        )
        # 旺矽 5/13 那兩筆在窗口外 → 不入 increase
        self.assertEqual(len(result["increase"]), 0)
        # 台達電 5/14 + 5/20 跨日累計 2 檔 → 入 decrease
        self.assertEqual(len(result["decrease"]), 1)
        self.assertEqual(result["decrease"][0]["symbol"], "TWSE:2308")
        conn.close()

    def test_custom_window_days(self):
        """window_days=3 → 只算 5/18 ~ 5/20"""
        conn = fresh_etf_db()
        insert_ops(conn,
                   # 5/15 在 3 日窗口外
                   ("00981A", "6223", "2026-05-15", "加碼", 100),
                   ("00987A", "6223", "2026-05-15", "加碼", 100),
                   # 5/18-5/20 在窗口內
                   ("00981A", "2330", "2026-05-18", "加碼", 100),
                   ("00987A", "2330", "2026-05-20", "加碼", 100))
        result = etf_io.fetch_etf_active_summary(
            conn, "2026-05-20", self._watchlist(), window_days=3,
        )
        # 旺矽窗口外
        symbols = [r["symbol"] for r in result["increase"]]
        self.assertNotIn("TPEX:6223", symbols)
        # 台積電在窗口內
        self.assertIn("TWSE:2330", symbols)
        conn.close()

    def test_aggregates_same_etf_multiple_days(self):
        """同檔 ETF 多日加碼 → 用 set 去重算 1 檔(不到 ≥ 2 共識門檻),
        但 total_shares 累加。本測試鎖死「跨日累計」邏輯。"""
        conn = fresh_etf_db()
        insert_ops(conn,
                   # 同一檔 ETF 三天加碼旺矽
                   ("00981A", "6223", "2026-05-15", "加碼", 100),
                   ("00981A", "6223", "2026-05-18", "加碼",  80),
                   ("00981A", "6223", "2026-05-20", "加碼",  50))
        result = etf_io.fetch_etf_active_summary(
            conn, "2026-05-20", self._watchlist(),
        )
        # 1 檔 ETF 不夠 ≥ 2 共識 → 不入 increase
        self.assertEqual(result["increase"], [])

        # 反例:加第二檔 ETF → 跨日累計 2 檔,total_shares=230+100=330
        insert_ops(conn,
                   ("00987A", "6223", "2026-05-16", "加碼", 100))
        result = etf_io.fetch_etf_active_summary(
            conn, "2026-05-20", self._watchlist(),
        )
        self.assertEqual(len(result["increase"]), 1)
        entry = result["increase"][0]
        self.assertEqual(entry["etf_count"],    2)            # 兩檔不同 ETF
        self.assertEqual(entry["total_shares"], 330)          # 100+80+50+100
        self.assertEqual(entry["etfs"], ["00981A", "00987A"])  # 去重 + sorted
        conn.close()


class TestComputeEtfMaxDate(unittest.TestCase):

    def test_empty_table_returns_none(self):
        conn = fresh_etf_db()
        self.assertIsNone(etf_io.compute_etf_max_date(conn))
        conn.close()

    def test_returns_max_date(self):
        conn = fresh_etf_db()
        insert_ops(conn,
                   ("00981A", "6223", "2026-05-15", "加碼", 100),
                   ("00987A", "2330", "2026-05-20", "加碼",  50),
                   ("00992A", "6223", "2026-05-18", "減碼", 100))
        self.assertEqual(etf_io.compute_etf_max_date(conn), "2026-05-20")
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
class TestV1Parity(unittest.TestCase):
    """v1 src/load_data._load_etf_data vs v2 etf_io.compute_etf_features 比結果。

    僅 test 內 import v1(不污染 src/)。
    多種情境跑兩邊,assert booleans + counts 一致。
    """

    @classmethod
    def setUpClass(cls):
        # v1 module 在 src/load_data.py (僅 test 內 import,src/ 不依賴 v1)
        from src import load_data as v1_load_data
        cls.v1_load_etf_data = staticmethod(v1_load_data._load_etf_data)

    def setUp(self):
        self.conn = fresh_etf_db()

    def tearDown(self):
        self.conn.close()

    def _run_both(self, code, date, today_volume):
        """跑 v1 跟 v2,回傳兩個 dict 給 caller assert"""
        v1_result = type(self).v1_load_etf_data(
            self.conn.cursor(), code, date, today_volume,
        )
        v2_result = etf_io.compute_etf_features(self.conn, code, date, today_volume)
        return v1_result, v2_result

    def _assert_parity(self, v1, v2):
        """booleans + counts 必須一致"""
        self.assertEqual(v1["etf_consensus_buy_count"], v2["buy_count"])
        self.assertEqual(v1["is_continuous_buy"],       v2["is_continuous_buy"])
        self.assertEqual(v1["is_abnormal_ignition"],    v2["is_abnormal_ignition"])
        self.assertEqual(set(v1["etf_buy_etfs"]),       set(v2["buy_etfs"]))

    def test_parity_empty(self):
        v1, v2 = self._run_both("6223", "2026-05-20", 2000)
        self._assert_parity(v1, v2)

    def test_parity_single_etf_no_ignition(self):
        insert_ops(self.conn, ("00981A", "6223", "2026-05-20", "加碼", 100))
        v1, v2 = self._run_both("6223", "2026-05-20", 2000)
        self._assert_parity(v1, v2)

    def test_parity_consensus_two_etfs(self):
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-20", "加碼", 100),
                   ("00987A", "6223", "2026-05-20", "加碼",  50))
        v1, v2 = self._run_both("6223", "2026-05-20", 2000)
        self._assert_parity(v1, v2)

    def test_parity_continuous_buy(self):
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-15", "加碼", 100),
                   ("00987A", "6223", "2026-05-20", "加碼",  50))
        v1, v2 = self._run_both("6223", "2026-05-20", 2000)
        self._assert_parity(v1, v2)

    def test_parity_abnormal_ignition(self):
        insert_ops(self.conn, ("00981A", "6223", "2026-05-20", "加碼", 250))
        v1, v2 = self._run_both("6223", "2026-05-20", 2000)
        self._assert_parity(v1, v2)

    def test_parity_realistic_mixed(self):
        """混合 buy + sell + 多 ETF + 多天"""
        insert_ops(self.conn,
                   ("00981A", "6223", "2026-05-15", "加碼", 100),
                   ("00987A", "6223", "2026-05-17", "加碼",  80),
                   ("00992A", "6223", "2026-05-18", "減碼",  30),
                   ("00994A", "6223", "2026-05-20", "加碼", 200),
                   ("00995A", "6223", "2026-05-20", "建倉", 150))
        v1, v2 = self._run_both("6223", "2026-05-20", 5000)
        self._assert_parity(v1, v2)
