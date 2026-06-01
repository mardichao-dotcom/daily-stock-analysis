"""
test_run_filters_v2.py — 端到端 smoke test(W2.1 Phase 3)

旺矽 6223 + 「4640 內撐」一條線 + 8 天 fixture K 線,
覆蓋完整循環:TRIGGERED → STANDING → MAINTAINING ×3 → CANCELLED+跌破 →
TRIGGERED(新)→ STANDING(再 +N)

驗證每天的:
  - standing_state row(state / trigger_date / standing_date)
  - filtered_result_v2 score(只在 STANDING 那天有 0.7,其他日是 0)
  - tags(🟢 站穩 / 🔴 跌破)
  - events(standing / breakdown 事件)

執行:python3 -m unittest tests.test_run_filters_v2
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
import json


# ── Fixtures ──────────────────────────────────────────────────────────────────

WANGXI_KLINE_8DAYS = [
    # (date, open, high, low, close, volume),圍繞 p=4640 設計。
    # volume 全部統一 1_000_000:保證 vol_ratio ≡ 1.0(全 8 天 volume.score = 0),
    # 讓 8 天循環的 state machine + given_price 分數測試不受 W2.2.2 volume 接入影響。
    # 「任何重構不變」的承諾針對 state / given_price / tags / events,
    # 不針對 volume 分數(volume 是 v2.1 新加的軸)。
    ("2026-05-13", 4620, 4680, 4630, 4660, 1_000_000),   # TRIGGERED
    ("2026-05-14", 4670, 4720, 4655, 4700, 1_000_000),   # STANDING(+0.7)
    ("2026-05-15", 4690, 4710, 4660, 4680, 1_000_000),   # MAINTAINING
    ("2026-05-16", 4680, 4700, 4650, 4670, 1_000_000),   # MAINTAINING
    ("2026-05-19", 4670, 4690, 4640, 4660, 1_000_000),   # MAINTAINING
    ("2026-05-20", 4620, 4640, 4580, 4600, 1_000_000),   # CANCELLED + 🔴
    ("2026-05-21", 4610, 4660, 4615, 4650, 1_000_000),   # TRIGGERED(新循環)
    ("2026-05-22", 4660, 4710, 4640, 4690, 1_000_000),   # STANDING(+0.7 再)
]

FIXTURE_WATCHLIST = {
    "台股板塊": {
        "半導體設備耗材": {
            "成員": [{"code": "TPEX:6223", "name": "旺矽"}],
            "長子": ["TPEX:6223"],
        },
    },
    "國際族群": {},
}

FIXTURE_KEY_PRICES = {
    "version":      "fixture-smoke-test",
    "updated_at":   "2026-05-26",
    "rule_version": "v2.1",
    "stocks": {
        "TPEX:6223": {
            "name":   "旺矽",
            "sector": "半導體設備耗材",
            "market": "TW",
            "lines": [{
                "price":     "4640",
                "color":     "black",
                "category":  "inner_support",
                "adjective": "small",
                "text":      "小內撐",
            }],
            "areas": [],
        },
    },
}

FIXTURE_SECTORS = {"sectors": {"半導體設備耗材": "A"}}


def load_real_weights() -> dict:
    """smoke test 用 production weights.json(才能算到 inner_support / small)"""
    with open(os.path.join(PROJECT_ROOT, "config", "weights.json"),
              encoding="utf-8") as f:
        return json.load(f)


def setup_fixture_kline_db() -> sqlite3.Connection:
    """In-memory kline.db,只含旺矽 8 天 OHLCV"""
    conn = sqlite3.connect(":memory:")
    # 模擬 5A kline 表 schema
    conn.execute(
        "CREATE TABLE kline ("
        "  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, "
        "  volume REAL, PRIMARY KEY (symbol, date))"
    )
    for row in WANGXI_KLINE_8DAYS:
        conn.execute(
            "INSERT INTO kline VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("TPEX:6223",) + row,
        )
    conn.commit()
    return conn


# ─────────────────────────────────────────────────────────────────────────────
class TestWangXiEightDayCycle(unittest.TestCase):
    """
    旺矽 6223 + 4640 內撐 + 8 天循環的端到端 smoke test。

    ⚠️ 此測試是 Stage 8 最核心的整合驗證,
       任何重構必須保證這 8 天結果不變。
       對應規則 §3-B 範例。如果這個測試紅了,代表狀態機 / 持久化 /
       計分公式任何一處出了問題。修 bug 不能修這個測試,要修產品碼。
    """

    def setUp(self):
        self.conn = setup_fixture_kline_db()
        self.weights = load_real_weights()

    def tearDown(self):
        self.conn.close()

    def _run_day(self, date: str) -> dict:
        return run_filters_v2.run_pipeline(
            date       = date,
            conn_kline = self.conn,
            conn_etf   = None,
            weights    = self.weights,
            sectors    = FIXTURE_SECTORS,
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = f"{date}T19:00:00+08:00",
        )

    def _read_4640_state(self) -> dict | None:
        return state_io.read_state(
            self.conn, "TPEX:6223", "inner_support", "4640",
        )

    # ── 預期狀態表(8 天逐日)──────────────────────────────────────────────
    # date          state         trigger_date  standing_date  score  has_tag_standing  has_tag_breakdown  event
    EXPECTED = [
        ("2026-05-13", "TRIGGERED",   "2026-05-13", None,         0.0, False, False, None),
        ("2026-05-14", "STANDING",    "2026-05-13", "2026-05-14", 0.7, True,  False, "standing"),
        ("2026-05-15", "MAINTAINING", "2026-05-13", "2026-05-14", 0.0, True,  False, None),
        ("2026-05-16", "MAINTAINING", "2026-05-13", "2026-05-14", 0.0, True,  False, None),
        ("2026-05-19", "MAINTAINING", "2026-05-13", "2026-05-14", 0.0, True,  False, None),
        ("2026-05-20", "CANCELLED",   None,         "2026-05-14", 0.0, False, True,  "breakdown"),
        ("2026-05-21", "TRIGGERED",   "2026-05-21", None,         0.0, False, False, None),
        ("2026-05-22", "STANDING",    "2026-05-21", "2026-05-22", 0.7, True,  False, "standing"),
    ]

    def test_eight_day_full_cycle(self):
        """8 天逐日跑 run_pipeline,驗證 standing_state + score + tags + events"""
        for (date, exp_state, exp_trigger, exp_standing, exp_score,
             has_standing_tag, has_breakdown_tag, exp_event) in self.EXPECTED:

            with self.subTest(date=date):
                result = self._run_day(date)
                stock = result["stocks"]["TPEX:6223"]

                # ── 分數 ──
                self.assertAlmostEqual(
                    stock["score"], exp_score,
                    msg=f"{date}: score expected {exp_score} got {stock['score']}"
                )

                # ── standing_state row ──
                row = self._read_4640_state()
                self.assertIsNotNone(row, f"{date}: standing_state row missing")
                self.assertEqual(row["state"], exp_state,
                                 f"{date}: state expected {exp_state} got {row['state']}")
                self.assertEqual(row["trigger_date"], exp_trigger,
                                 f"{date}: trigger_date mismatch")
                self.assertEqual(row["standing_date"], exp_standing,
                                 f"{date}: standing_date mismatch")

                # ── tags ──
                tags = stock["tags"]
                has_standing = any("🟢 站穩" in t for t in tags)
                has_breakdown = any("🔴 跌破" in t for t in tags)
                self.assertEqual(has_standing, has_standing_tag,
                                 f"{date}: 🟢 tag expected {has_standing_tag} got {has_standing}")
                self.assertEqual(has_breakdown, has_breakdown_tag,
                                 f"{date}: 🔴 tag expected {has_breakdown_tag} got {has_breakdown}")

                # ── events ──
                if exp_event is None:
                    self.assertEqual(stock["events"], [],
                                     f"{date}: 沒事件但 events 非空")
                else:
                    types = [e["type"] for e in stock["events"]]
                    self.assertIn(exp_event, types,
                                  f"{date}: expected event type {exp_event!r} not in {types}")


# ─────────────────────────────────────────────────────────────────────────────
class TestOutputStructure(unittest.TestCase):
    """輸出 JSON 結構驗證(對齊 spec §4.4 + 5.2)"""

    def setUp(self):
        self.conn = setup_fixture_kline_db()
        self.weights = load_real_weights()
        # 先跑 5/13 讓狀態進 TRIGGERED,再跑 5/14 才會 STANDING + 觸發 event
        for d in ("2026-05-13", "2026-05-14"):
            self.result = run_filters_v2.run_pipeline(
                date       = d,
                conn_kline = self.conn,
                conn_etf   = None,
                weights    = self.weights,
                sectors    = FIXTURE_SECTORS,
                key_prices = FIXTURE_KEY_PRICES,
                watchlist  = FIXTURE_WATCHLIST,
                now_iso    = f"{d}T19:00:00+08:00",
            )

    def tearDown(self):
        self.conn.close()

    def test_top_level_keys(self):
        self.assertEqual(self.result["date"],    "2026-05-14")
        self.assertEqual(self.result["version"], "2.1")
        self.assertIn("metadata", self.result)
        self.assertIn("stocks",   self.result)

    def test_metadata_keys(self):
        md = self.result["metadata"]
        self.assertIn("etf_delayed",     md)
        self.assertIn("generated_at",    md)
        self.assertIn("skipped_symbols", md)
        self.assertIn("data_date_in_db", md)
        self.assertIn("version",         md)

    def test_metadata_has_etf_delayed_placeholder(self):
        """W2.1 etf_delayed 是 None(未接 ETF),W2.2 才填 bool"""
        self.assertIsNone(self.result["metadata"]["etf_delayed"])

    def test_metadata_has_data_date_filled(self):
        """W2.2.2 data_date_in_db 從 kline.db MAX(date) 填(旺矽 fixture max=5/22)"""
        self.assertEqual(self.result["metadata"]["data_date_in_db"], "2026-05-22")

    def test_event_has_category(self):
        """W2.4 chart.js 要靠 category 決定線型 + 標記顏色"""
        # 跑 5/14 那天確認 standing event 帶 category
        stock = self.result["stocks"]["TPEX:6223"]
        self.assertTrue(stock["events"], "5/14 應該有 standing event")
        event = stock["events"][0]
        self.assertEqual(event["category"], "inner_support")
        self.assertEqual(event["type"], "standing")

    def test_stock_entry_has_required_fields(self):
        """W3 加 name/sector 後,8 個必填欄位"""
        stock = self.result["stocks"]["TPEX:6223"]
        required = {"name", "sector", "score", "grade", "tags",
                    "details", "key_prices_snapshot", "events"}
        self.assertEqual(set(stock.keys()), required)

    def test_stock_entry_name_sector_from_watchlist(self):
        """name / sector 來自 watchlist 反查(2026-05-31 加)"""
        stock = self.result["stocks"]["TPEX:6223"]
        self.assertEqual(stock["name"],   "旺矽")
        self.assertEqual(stock["sector"], "半導體設備耗材")

    def test_etf_active_in_output(self):
        """W3 區塊 6 資料源:etf_active 進 output 頂層"""
        self.assertIn("etf_active", self.result)
        self.assertIn("increase", self.result["etf_active"])
        self.assertIn("decrease", self.result["etf_active"])

    def test_details_have_module_field(self):
        """detail 帶 module 欄位,W2.4 chart 用 module 篩選"""
        stock = self.result["stocks"]["TPEX:6223"]
        for d in stock["details"]:
            self.assertIn("module", d)
            self.assertEqual(d["module"], "given_price")

    def test_key_prices_snapshot_present(self):
        """key_prices_snapshot 給 W2.4 chart 用,避免 chart 再讀 config"""
        snapshot = self.result["stocks"]["TPEX:6223"]["key_prices_snapshot"]
        self.assertIn("lines", snapshot)
        self.assertIn("areas", snapshot)
        self.assertEqual(len(snapshot["lines"]), 1)
        self.assertEqual(snapshot["lines"][0]["price"], "4640")

    def test_grade_is_D_for_low_score(self):
        """W2.2.5 grader 接入後,旺矽 5/14 score=0.7 → D 級(< 3)"""
        self.assertEqual(self.result["stocks"]["TPEX:6223"]["grade"], "D")


# ─────────────────────────────────────────────────────────────────────────────
# W2.2.7 之後 placeholders 全部替換完成,本 class 移除。


# ─────────────────────────────────────────────────────────────────────────────
class TestComputeKlineFeatures(unittest.TestCase):
    """W2.2.3 _compute_kline_features 純函式"""

    def test_empty_returns_all_none(self):
        feats = run_filters_v2._compute_kline_features([])
        for key in ("change_pct", "close", "high_60d",
                    "vol_ratio", "today_volume", "avg_volume",
                    "ma_20", "ma_60", "ma_90"):
            self.assertIsNone(feats[key], msg=f"{key} should be None")

    def test_change_pct_basic(self):
        """昨天 100,今天 105 → change_pct = 5.0"""
        history = [
            {"date": "X", "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
            {"date": "Y", "open": 102, "high": 110, "low": 100, "close": 105, "volume": 1000},
        ]
        feats = run_filters_v2._compute_kline_features(history)
        self.assertAlmostEqual(feats["change_pct"], 5.0)
        self.assertEqual(feats["close"], 105)

    def test_change_pct_negative(self):
        """跌 → change_pct 為負"""
        history = [
            {"date": "X", "open": 0, "high": 0, "low": 0, "close": 100, "volume": 0},
            {"date": "Y", "open": 0, "high": 0, "low": 0, "close": 90,  "volume": 0},
        ]
        feats = run_filters_v2._compute_kline_features(history)
        self.assertAlmostEqual(feats["change_pct"], -10.0)

    def test_single_day_no_change_pct(self):
        """單筆 history → change_pct = None"""
        history = [{"date": "X", "open": 0, "high": 0, "low": 0,
                    "close": 100, "volume": 1000}]
        feats = run_filters_v2._compute_kline_features(history)
        self.assertIsNone(feats["change_pct"])
        self.assertEqual(feats["close"], 100)

    def test_high_60d_excludes_today(self):
        """high_60d 不含 today,即使 today 是最高也不算。
        strict 模式需要 history(不含 today)≥ 60,所以用 61 棒 fixture。"""
        # 前 60 棒 high 全是 100,中間有 1 棒 110;today 是 999
        highs = [100.0] * 30 + [110.0] + [100.0] * 29 + [999.0]   # 61 棒
        history = [{"date": f"d{i}", "open": 0, "high": h, "low": 0,
                    "close": 0, "volume": 0}
                   for i, h in enumerate(highs)]
        feats = run_filters_v2._compute_kline_features(history)
        self.assertEqual(feats["high_60d"], 110.0)   # 不是 999

    def test_ma_20_computed_with_full_window(self):
        """MA20 需要 history ≥ 20(strict)。給 20 個 K 棒驗算式對。"""
        history = [{"date": f"d{i}", "open": 0, "high": 0, "low": 0,
                    "close": float(v), "volume": 0}
                   for i, v in enumerate(range(100, 120))]   # 20 closes:100..119
        feats = run_filters_v2._compute_kline_features(history)
        # SMA(20) of 100..119 = 109.5
        self.assertAlmostEqual(feats["ma_20"], 109.5)

    def test_ma_20_none_when_history_under_20(self):
        """strict:19 個 K 棒 → ma_20 = None"""
        history = [{"date": f"d{i}", "open": 0, "high": 0, "low": 0,
                    "close": 100, "volume": 0} for i in range(19)]
        feats = run_filters_v2._compute_kline_features(history)
        self.assertIsNone(feats["ma_20"])

    def test_ma_60_none_when_history_under_60(self):
        history = [{"date": f"d{i}", "open": 0, "high": 0, "low": 0,
                    "close": 100, "volume": 0} for i in range(59)]
        feats = run_filters_v2._compute_kline_features(history)
        self.assertIsNone(feats["ma_60"])

    def test_ma_90_none_when_history_under_90(self):
        history = [{"date": f"d{i}", "open": 0, "high": 0, "low": 0,
                    "close": 100, "volume": 0} for i in range(89)]
        feats = run_filters_v2._compute_kline_features(history)
        self.assertIsNone(feats["ma_90"])

    def test_high_60d_none_when_history_under_60(self):
        """strict:history(不含 today)< 60 → high_60d = None"""
        history = [{"date": f"d{i}", "open": 0, "high": 100, "low": 0,
                    "close": 100, "volume": 0} for i in range(59)]
        feats = run_filters_v2._compute_kline_features(history)
        self.assertIsNone(feats["high_60d"])


class TestComputeVolumeFeatures(unittest.TestCase):
    """W2.2.2 _compute_volume_features 純函式測試"""

    def test_empty_history(self):
        feats = run_filters_v2._compute_volume_features([])
        self.assertIsNone(feats["vol_ratio"])
        self.assertIsNone(feats["today_volume"])
        self.assertIsNone(feats["avg_volume"])

    def test_only_today_no_baseline(self):
        history = [{"date": "X", "open": 0, "high": 0, "low": 0,
                    "close": 0, "volume": 1000}]
        feats = run_filters_v2._compute_volume_features(history)
        self.assertEqual(feats["today_volume"], 1000)
        self.assertIsNone(feats["avg_volume"])
        self.assertIsNone(feats["vol_ratio"])

    def test_full_20_day_window(self):
        """25 天 history,window=20:取 [-21:-1] 的 20 天平均"""
        history = [{"volume": 1000} for _ in range(20)]   # 過去 20 天
        history.insert(0, {"volume": 999_999})            # 第 21 天舊資料(不該入窗)
        history.append({"volume": 2000})                  # today
        # history 共 22 筆,kline_history[-21:-1] = 20 筆,全 1000(不含 today、不含最舊)
        feats = run_filters_v2._compute_volume_features(history)
        self.assertEqual(feats["today_volume"], 2000)
        self.assertEqual(feats["avg_volume"], 1000)
        self.assertEqual(feats["vol_ratio"], 2.0)

    def test_short_history_fallback(self):
        """history 不足 21 天 → 用實際有的(避免新上市股 KeyError)"""
        # 6 天 history,window=20 → 取 [-21:-1] = 前 5 天
        history = [{"volume": v} for v in [800, 900, 1000, 1100, 1200, 2000]]
        feats = run_filters_v2._compute_volume_features(history)
        self.assertEqual(feats["today_volume"], 2000)
        # avg = mean(800, 900, 1000, 1100, 1200) = 1000
        self.assertEqual(feats["avg_volume"], 1000)
        self.assertEqual(feats["vol_ratio"], 2.0)

    def test_zero_avg_volume_returns_none_ratio(self):
        """avg = 0(全停盤)→ vol_ratio = None,避免除以零"""
        history = [{"volume": 0} for _ in range(20)]
        history.append({"volume": 100})
        feats = run_filters_v2._compute_volume_features(history)
        self.assertIsNone(feats["vol_ratio"])


class TestV1VolumeParity(unittest.TestCase):
    """W2.2.2 v1 vol_ratio 計算公式對齊驗證(用同 window=5,證明算式一致)。
    v2.1 的 window=20 是規則改動,parity 用 v1 window 確認 (sum/len、除法) 沒漂。"""

    @classmethod
    def setUpClass(cls):
        from src import load_data as v1_load_data
        cls.v1_module = v1_load_data

    def _v1_vol_ratio(self, bars):
        """重現 v1 src/load_data:91-95 的算式"""
        today_volume = bars[-1][5]   # bars 是 tuples 形式
        vol_bars = bars[:-1]
        vol_5_bars = vol_bars[-5:] if len(vol_bars) >= 5 else vol_bars
        if not vol_5_bars:
            return 1.0
        vol_5 = sum(b[5] for b in vol_5_bars) / len(vol_5_bars)
        return today_volume / vol_5 if vol_5 > 0 else 1.0

    def _v2_vol_ratio(self, history, window=5):
        feats = run_filters_v2._compute_volume_features(history, window=window)
        return feats["vol_ratio"]

    def test_parity_typical_6_day_history(self):
        v1_bars = [
            # (date, open, high, low, close, volume)
            ("2026-05-13", 0, 0, 0, 0, 800),
            ("2026-05-14", 0, 0, 0, 0, 900),
            ("2026-05-15", 0, 0, 0, 0, 1000),
            ("2026-05-16", 0, 0, 0, 0, 1100),
            ("2026-05-19", 0, 0, 0, 0, 1200),
            ("2026-05-20", 0, 0, 0, 0, 2000),   # today
        ]
        v2_history = [{"volume": b[5]} for b in v1_bars]
        v1_r = self._v1_vol_ratio(v1_bars)
        v2_r = self._v2_vol_ratio(v2_history, window=5)
        self.assertAlmostEqual(v1_r, v2_r)

    def test_parity_short_history(self):
        """history 不足 5 天的 fallback 都該一致"""
        v1_bars = [("X", 0, 0, 0, 0, v) for v in [1000, 1500, 2000]]
        v2_history = [{"volume": b[5]} for b in v1_bars]
        v1_r = self._v1_vol_ratio(v1_bars)
        v2_r = self._v2_vol_ratio(v2_history, window=5)
        self.assertAlmostEqual(v1_r, v2_r)

    def test_parity_volume_spike(self):
        v1_bars = [("X", 0, 0, 0, 0, v) for v in
                   [500, 500, 500, 500, 500, 1500]]
        v2_history = [{"volume": b[5]} for b in v1_bars]
        v1_r = self._v1_vol_ratio(v1_bars)
        v2_r = self._v2_vol_ratio(v2_history, window=5)
        self.assertAlmostEqual(v1_r, v2_r)
        self.assertAlmostEqual(v1_r, 3.0)


class TestVolumeIntegration(unittest.TestCase):
    """volume 整合測試:長 K 線 fixture + 旺矽 4640 → 預期分數"""

    def _setup_long_kline_db(self, daily_volumes: list[int]) -> tuple[sqlite3.Connection, str]:
        """建立 N 天 K 線 fixture,price 圍繞旺矽 4640 設計。
        回傳 (conn, last_date)。"""
        from datetime import date, timedelta
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE kline ("
            "  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
            "  close REAL, volume REAL, PRIMARY KEY (symbol, date))"
        )
        start = date(2026, 5, 13)
        last_iso = ""
        for i, vol in enumerate(daily_volumes):
            d = start + timedelta(days=i)
            last_iso = d.isoformat()
            conn.execute(
                "INSERT INTO kline VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("TPEX:6223", last_iso, 4680, 4710, 4660, 4690, vol),
            )
        conn.commit()
        return conn, last_iso

    def _run_last_day(self, conn_kline, last_day):
        return run_filters_v2.run_pipeline(
            date       = last_day,
            conn_kline = conn_kline,
            conn_etf   = None,                # 不接 ETF
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = "X",
        )

    def test_volume_below_threshold_no_score(self):
        """20 天平均 1000,今天 1100 → ratio=1.1 → 0 分。
        ⚠ K 線 low=4660 > 4640,NO TOUCH → standing 不貢獻分數,total 只剩 volume 0。"""
        volumes = [1000] * 20 + [1100]
        conn, last_day = self._setup_long_kline_db(volumes)
        result = self._run_last_day(conn, last_day)
        stock = result["stocks"]["TPEX:6223"]
        self.assertEqual(stock["score"], 0)
        conn.close()

    def test_volume_at_small_threshold_scores_1(self):
        """ratio=1.6 剛好 → +1"""
        volumes = [1000] * 20 + [1600]
        conn, last_day = self._setup_long_kline_db(volumes)
        result = self._run_last_day(conn, last_day)
        stock = result["stocks"]["TPEX:6223"]
        self.assertEqual(stock["score"], 1)
        modules = [d.get("module") for d in stock["details"]]
        self.assertIn("volume", modules)
        conn.close()

    def test_volume_at_large_threshold_scores_2(self):
        """ratio=2.0 剛好 → +2"""
        volumes = [1000] * 20 + [2000]
        conn, last_day = self._setup_long_kline_db(volumes)
        result = self._run_last_day(conn, last_day)
        stock = result["stocks"]["TPEX:6223"]
        self.assertEqual(stock["score"], 2)
        conn.close()


class TestDataDateInDb(unittest.TestCase):
    """metadata.data_date_in_db 從 kline.db MAX(date) 填入"""

    def test_data_date_matches_max_in_db(self):
        conn = setup_fixture_kline_db()   # 旺矽 8 天 fixture,最大 5/22
        result = run_filters_v2.run_pipeline(
            date       = "2026-05-22",
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = "X",
        )
        self.assertEqual(result["metadata"]["data_date_in_db"], "2026-05-22")
        conn.close()

    def test_data_date_none_when_kline_empty(self):
        """kline 為空 → data_date_in_db = None"""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE kline ("
            "  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
            "  close REAL, volume REAL, PRIMARY KEY (symbol, date))"
        )
        conn.commit()
        result = run_filters_v2.run_pipeline(
            date       = "2026-05-22",
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = "X",
        )
        self.assertIsNone(result["metadata"]["data_date_in_db"])
        conn.close()


class TestChipEtfIntegration(unittest.TestCase):
    """W2.2.1 chip_etf 整合 — 旺矽 5/14 + ETF 加碼 fixture → 預期分數"""

    def _setup_etf_db(self, *ops):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE operations ("
            "  etf TEXT, 代號 TEXT, 日期 TEXT, 動作 TEXT, 張數 INTEGER)"
        )
        for o in ops:
            conn.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?)", o)
        conn.commit()
        return conn

    def _run_two_days_with_etf(self, conn_etf):
        """跑 5/13 + 5/14 兩天,回傳 5/14 result"""
        conn_kline = setup_fixture_kline_db()
        W = load_real_weights()
        for d in ("2026-05-13", "2026-05-14"):
            result = run_filters_v2.run_pipeline(
                date       = d,
                conn_kline = conn_kline,
                conn_etf   = conn_etf,
                weights    = W,
                sectors    = FIXTURE_SECTORS,
                key_prices = FIXTURE_KEY_PRICES,
                watchlist  = FIXTURE_WATCHLIST,
                now_iso    = f"{d}T19:00:00+08:00",
            )
        conn_kline.close()
        return result

    def test_no_etf_data_score_only_given_price(self):
        """沒 ETF 加碼 → 分數只有 given_price 0.7"""
        conn_etf = self._setup_etf_db()   # 空表
        result = self._run_two_days_with_etf(conn_etf)
        stock = result["stocks"]["TPEX:6223"]
        self.assertAlmostEqual(stock["score"], 0.7)
        conn_etf.close()

    def test_two_etfs_consensus_adds_2(self):
        """2 檔 ETF 加碼 → 共識 +2,加上 given_price 0.7 = 2.7"""
        conn_etf = self._setup_etf_db(
            ("00981A", "6223", "2026-05-14", "加碼", 100),
            ("00987A", "6223", "2026-05-14", "加碼",  50),
        )
        result = self._run_two_days_with_etf(conn_etf)
        stock = result["stocks"]["TPEX:6223"]
        # 0.7 (given_price) + 2 (共識) = 2.7
        self.assertAlmostEqual(stock["score"], 2.7)
        # details 應該含 chip_etf module
        modules = [d.get("module") for d in stock["details"]]
        self.assertIn("chip_etf", modules)
        conn_etf.close()

    def test_continuous_plus_consensus(self):
        """連續 + 共識 → +2 + +1 = +3,加 0.7 = 3.7"""
        conn_etf = self._setup_etf_db(
            ("00981A", "6223", "2026-05-12", "加碼", 100),   # 7 天前
            ("00987A", "6223", "2026-05-14", "加碼",  50),   # 今天
            ("00992A", "6223", "2026-05-14", "加碼",  30),   # 今天
        )
        result = self._run_two_days_with_etf(conn_etf)
        stock = result["stocks"]["TPEX:6223"]
        # 0.7 (given_price) + 2 (共識) + 1 (連續) = 3.7
        self.assertAlmostEqual(stock["score"], 3.7)
        conn_etf.close()

    def test_etf_delayed_metadata_when_max_date_lags(self):
        """etf 最新 date != 跑的 date → etf_delayed=True"""
        conn_etf = self._setup_etf_db(
            ("00981A", "6223", "2026-05-13", "加碼", 100),   # 最新只到 5/13
        )
        result = self._run_two_days_with_etf(conn_etf)   # 跑到 5/14
        self.assertTrue(result["metadata"]["etf_delayed"])
        self.assertEqual(result["metadata"]["etf_max_date_in_db"], "2026-05-13")
        conn_etf.close()

    def test_etf_delayed_false_when_up_to_date(self):
        """etf max date 等於 today → 不延遲"""
        conn_etf = self._setup_etf_db(
            ("00981A", "6223", "2026-05-14", "加碼", 100),
        )
        result = self._run_two_days_with_etf(conn_etf)
        self.assertFalse(result["metadata"]["etf_delayed"])
        conn_etf.close()

    def test_etf_delayed_none_when_no_etf_conn(self):
        """conn_etf=None → etf_delayed=None(本來就 W2.1 行為)"""
        conn_kline = setup_fixture_kline_db()
        result = run_filters_v2.run_pipeline(
            date       = "2026-05-13",
            conn_kline = conn_kline,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = "X",
        )
        self.assertIsNone(result["metadata"]["etf_delayed"])
        conn_kline.close()


class TestScoreMaFeatures(unittest.TestCase):
    """W2.2.4 _score_ma_features 純函式 — 首次站上 +N"""

    @classmethod
    def setUpClass(cls):
        cls.W = load_real_weights()

    def _history(self, closes: list[float]) -> list[dict]:
        return [{"date": f"d{i}", "open": 0, "high": 0, "low": 0,
                 "close": float(c), "volume": 0}
                for i, c in enumerate(closes)]

    def test_first_cross_above_ma20_scores(self):
        """前 20 天 close=100(平盤,prev_close 沒 strict > prev_ma),
        第 21 天 close=105(大漲穿越)→ 首次站上 → +1"""
        history = self._history([100.0] * 20 + [105.0])
        # 第 21 天:
        #   prev_close = 100, prev_ma_20 = mean([100]*20) = 100
        #   prev_above = 100 > 100 = False(strict,平盤不算已站上)
        #   today_close = 105, today_ma_20 = (19*100+100+105)/20 = 100.25
        #   today_above = 105 > 100.25 = True
        # → 首次站上 → +1
        feats = run_filters_v2._compute_kline_features(history)
        s, d = run_filters_v2._score_ma_features(history, feats, self.W)
        self.assertEqual(s, 1)
        self.assertEqual(d[0]["module"], "ma")
        self.assertIn("MA20", d[0]["reason"])

    def test_close_equals_ma_does_NOT_score(self):
        """貼線(close == ma)不算站上,strict > 是業界共識。
        21 天全 close=100 → 任何天 close == ma → 永遠不算 above → 0"""
        history = self._history([100.0] * 21)
        feats = run_filters_v2._compute_kline_features(history)
        s, d = run_filters_v2._score_ma_features(history, feats, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_dip_then_recross_ma20_scores_1(self):
        """前 19 天 close=100,第 20 天 close=90(dip 跌破),第 21 天 close=105(重新站上)→ +1"""
        history = self._history([100.0] * 19 + [90.0, 105.0])
        # 第 21 天 prev_history = days 1-20,closes = [100]*19 + [90]
        #   prev_ma_20 = (19*100 + 90)/20 = 99.5
        #   prev_close = 90
        #   prev_above = 90 >= 99.5 = False
        # 今天:today_close = 105
        #   today_ma_20 = (18*100 + 90 + 105)/20 = 99.75
        #   today_above = 105 >= 99.75 = True
        # → 首次站上 → +1
        feats = run_filters_v2._compute_kline_features(history)
        s, d = run_filters_v2._score_ma_features(history, feats, self.W)
        self.assertEqual(s, 1)
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["module"], "ma")
        self.assertIn("MA20", d[0]["reason"])

    def test_maintaining_above_no_score(self):
        """連續多日都在 MA20 之上 → 維持中 → 不再加分"""
        # 第 20 天首次站上、第 21 天仍站上 → 第 21 天評估時 prev_above=True
        history = self._history([95.0] * 19 + [110.0, 115.0])
        feats = run_filters_v2._compute_kline_features(history)
        s, d = run_filters_v2._score_ma_features(history, feats, self.W)
        # 第 21 天 prev_ma_20 = (19*95 + 110)/20 = 95.75
        #   prev_close = 110 >= 95.75 → prev_above = True
        # → 不算首次
        self.assertEqual(s, 0)

    def test_below_ma_no_score(self):
        """收盤未站上 MA → 0"""
        history = self._history([100.0] * 19 + [90.0, 92.0])
        feats = run_filters_v2._compute_kline_features(history)
        s, d = run_filters_v2._score_ma_features(history, feats, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_short_history_skips_all_mas(self):
        """history < 20 → ma_20/60/90 全 None → 跳過全部 MA"""
        history = self._history([100.0] * 15)
        feats = run_filters_v2._compute_kline_features(history)
        s, d = run_filters_v2._score_ma_features(history, feats, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_first_cross_all_three_mas_scores_5(self):
        """history 90+ 天,第一次同時站上三條 MA → +1 +2 +2 = +5
        構造:前 89 天都 100,第 90 天跌到 80(讓 prev_close < prev_ma),
              第 91 天大漲到 120(站上)"""
        history = self._history([100.0] * 89 + [80.0, 120.0])
        feats = run_filters_v2._compute_kline_features(history)
        # 確認 prev_close=80,所有 prev_ma > 80(都在 99+ 區間)→ prev_above 全 False
        # today_close=120,所有 today_ma 在 100 多 → today_above 全 True
        s, d = run_filters_v2._score_ma_features(history, feats, self.W)
        self.assertEqual(s, 5)   # 1 + 2 + 2
        modules = [item["module"] for item in d]
        self.assertEqual(modules, ["ma", "ma", "ma"])
        categories = [item["evidence"]["category"] for item in d]
        self.assertEqual(set(categories), {"ma_20", "ma_60", "ma_90"})

    def test_empty_history_returns_zero(self):
        s, d = run_filters_v2._score_ma_features([], {}, self.W)
        self.assertEqual(s, 0)
        self.assertEqual(d, [])

    def test_single_day_returns_zero(self):
        """單天 history → 無法比 prev → return 0"""
        s, d = run_filters_v2._score_ma_features(
            self._history([100.0]), {"ma_20": 100.0, "ma_60": None, "ma_90": None}, self.W
        )
        self.assertEqual(s, 0)

    def test_evidence_has_ma_value_and_close(self):
        """evidence 必含 ma_value + today_close"""
        history = self._history([100.0] * 19 + [90.0, 105.0])
        feats = run_filters_v2._compute_kline_features(history)
        _, d = run_filters_v2._score_ma_features(history, feats, self.W)
        ev = d[0]["evidence"]
        self.assertEqual(ev["category"],    "ma_20")
        self.assertEqual(ev["today_close"], 105.0)
        self.assertIsNotNone(ev["ma_value"])


class TestMaIntegration(unittest.TestCase):
    """W2.2.4 integration:全 pipeline 跑長 K 線 fixture 看 MA 分數"""

    def test_long_uptrend_first_cross_scores(self):
        """從跌破到站上 → MA20 +1"""
        from datetime import date, timedelta
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE kline ("
            "  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
            "  close REAL, volume REAL, PRIMARY KEY (symbol, date))"
        )
        # 21 天:前 19 天 close=100、第 20 天 90(dip 跌破)、第 21 天 105(重新站上)
        # OHL 保持在 4640 之上(避免 TOUCH 干擾 standing)
        start = date(2026, 4, 1)
        closes = [100.0] * 19 + [90.0, 105.0]
        last_iso = ""
        for i, c in enumerate(closes):
            d = start + timedelta(days=i)
            last_iso = d.isoformat()
            # 設 OHL 全在 4640 之上,避免 standing(p=4640)觸發
            conn.execute(
                "INSERT INTO kline VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("TPEX:6223", last_iso, 4700, 4720, 4660, c, 1_000_000),
            )
        conn.commit()

        result = run_filters_v2.run_pipeline(
            date       = last_iso,
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = {"stocks": {}},   # 沒給定價,避免干擾
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = "X",
        )
        stock = result["stocks"]["TPEX:6223"]
        # 只有 MA20 觸發(only ma_20 has enough history)→ +1
        self.assertEqual(stock["score"], 1)
        modules = [d.get("module") for d in stock["details"]]
        self.assertIn("ma", modules)
        conn.close()

    def test_wangxi_8day_fixture_no_ma_contribution(self):
        """旺矽 8 天 fixture history < 20 → MA 全 None → MA score = 0
        確保 W2.2.4 不破壞旺矽 8 天 cycle 的「不變承諾」"""
        conn = setup_fixture_kline_db()
        result = run_filters_v2.run_pipeline(
            date       = "2026-05-14",
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = "X",
        )
        stock = result["stocks"]["TPEX:6223"]
        # 應該還是 0.7 (給定價 4640 站穩,沒 MA 貢獻)
        # 5/14 評估時需要 5/13 已 TRIGGERED,所以先跑 5/13
        # ↑ 已經跑了 5/13 嗎?run_pipeline 是單天的,prev_state 從 DB 撈
        # 但這是 fresh in-memory DB,5/13 沒先跑 → prev_state=None → today 是 5/14 評估
        # 5/14 TOUCH? low=4655 ≤ 4640? 否! → state 還是 UNTRIGGERED → no given_price 分數
        # 加上 MA 全 None → 總分 0
        modules = [d.get("module") for d in stock["details"]]
        self.assertNotIn("ma", modules)   # 重點:MA 不出現在 details
        conn.close()


class TestSectorLinkageIntegration(unittest.TestCase):
    """W2.2.3 sector_linkage 端到端:國際長子發動 → TW 族群所有成員 +1"""

    def _setup_kline_with_intl(
        self,
        tw_data:   list[tuple],   # [(date, o, h, l, c, v)] for TPEX:6223
        asml_data: list[tuple],
    ) -> sqlite3.Connection:
        """fixture kline.db 含 旺矽 + ASML 兩個 symbol"""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE kline ("
            "  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
            "  close REAL, volume REAL, PRIMARY KEY (symbol, date))"
        )
        for row in tw_data:
            conn.execute("INSERT INTO kline VALUES (?, ?, ?, ?, ?, ?, ?)",
                         ("TPEX:6223",) + row)
        for row in asml_data:
            conn.execute("INSERT INTO kline VALUES (?, ?, ?, ?, ?, ?, ?)",
                         ("NASDAQ:ASML",) + row)
        conn.commit()
        return conn

    @staticmethod
    def _watchlist_with_intl() -> dict:
        return {
            "台股板塊": {
                "半導體設備耗材": {
                    "成員": [{"code": "TPEX:6223", "name": "旺矽"}],
                    "長子": ["TPEX:6223"],
                },
            },
            "國際族群": {
                "半導體設備_材料_封測": {
                    "成員": [{"code": "NASDAQ:ASML", "name": "ASML"}],
                    "長子": ["NASDAQ:ASML"],
                    "對應台股族群": ["半導體設備耗材"],
                },
            },
        }

    def test_asml_activated_wangxi_gets_plus_1(self):
        """ASML 漲 5% 量 2x → 半導體設備_材料_封測 發動 → 旺矽 +1"""
        # ASML 21 天:前 20 天 close=100 volume=1M,today close=105 volume=2M
        # → change_pct=5%, vol_ratio=2.0 → 條件 a 觸發發動
        asml = [(f"2026-04-{(i+1):02d}", 99, 101, 98, 100, 1_000_000)
                for i in range(20)]
        asml.append(("2026-04-21", 100, 110, 100, 105, 2_000_000))
        # 旺矽:同樣 21 天,但不 TOUCH 4640(不貢獻 given_price)
        wangxi = [(f"2026-04-{(i+1):02d}", 4700, 4720, 4660, 4700, 1_000_000)
                  for i in range(20)]
        wangxi.append(("2026-04-21", 4700, 4720, 4660, 4700, 1_000_000))

        conn = self._setup_kline_with_intl(wangxi, asml)
        result = run_filters_v2.run_pipeline(
            date       = "2026-04-21",
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = {"sectors": {"半導體設備耗材": "A"}},
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = self._watchlist_with_intl(),
            now_iso    = "X",
        )
        stock = result["stocks"]["TPEX:6223"]
        self.assertEqual(stock["score"], 1)   # 純 sector_linkage +1
        modules = [d.get("module") for d in stock["details"]]
        self.assertIn("sector_linkage", modules)
        conn.close()

    def test_asml_not_activated_wangxi_zero(self):
        """ASML 幾乎沒動 → 不發動 → 旺矽 0"""
        asml = [(f"2026-04-{(i+1):02d}", 99, 101, 98, 100, 1_000_000)
                for i in range(20)]
        asml.append(("2026-04-21", 100, 102, 99, 101, 1_100_000))   # 漲 1% 量 1.1x
        wangxi = [(f"2026-04-{(i+1):02d}", 4700, 4720, 4660, 4700, 1_000_000)
                  for i in range(20)]
        wangxi.append(("2026-04-21", 4700, 4720, 4660, 4700, 1_000_000))

        conn = self._setup_kline_with_intl(wangxi, asml)
        result = run_filters_v2.run_pipeline(
            date       = "2026-04-21",
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = {"sectors": {"半導體設備耗材": "A"}},
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = self._watchlist_with_intl(),
            now_iso    = "X",
        )
        stock = result["stocks"]["TPEX:6223"]
        self.assertEqual(stock["score"], 0)
        conn.close()

    def test_asml_drops_5pct_does_NOT_activate(self):
        """ASML 跌 5% 量 2x → 不發動(W1.4 「只算漲」決策)→ 旺矽 0"""
        asml = [(f"2026-04-{(i+1):02d}", 99, 101, 98, 100, 1_000_000)
                for i in range(20)]
        asml.append(("2026-04-21", 100, 100, 94, 95, 2_000_000))   # 跌 5% 量 2x
        wangxi = [(f"2026-04-{(i+1):02d}", 4700, 4720, 4660, 4700, 1_000_000)
                  for i in range(20)]
        wangxi.append(("2026-04-21", 4700, 4720, 4660, 4700, 1_000_000))

        conn = self._setup_kline_with_intl(wangxi, asml)
        result = run_filters_v2.run_pipeline(
            date       = "2026-04-21",
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = {"sectors": {"半導體設備耗材": "A"}},
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = self._watchlist_with_intl(),
            now_iso    = "X",
        )
        stock = result["stocks"]["TPEX:6223"]
        self.assertEqual(stock["score"], 0)
        conn.close()

    def test_sector_below_threshold_no_score(self):
        """半導體設備耗材評級 C → 即使 ASML 發動也 0"""
        asml = [(f"2026-04-{(i+1):02d}", 99, 101, 98, 100, 1_000_000)
                for i in range(20)]
        asml.append(("2026-04-21", 100, 110, 100, 105, 2_000_000))
        wangxi = [(f"2026-04-{(i+1):02d}", 4700, 4720, 4660, 4700, 1_000_000)
                  for i in range(20)]
        wangxi.append(("2026-04-21", 4700, 4720, 4660, 4700, 1_000_000))

        conn = self._setup_kline_with_intl(wangxi, asml)
        result = run_filters_v2.run_pipeline(
            date       = "2026-04-21",
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = {"sectors": {"半導體設備耗材": "C"}},   # 低於 B
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = self._watchlist_with_intl(),
            now_iso    = "X",
        )
        stock = result["stocks"]["TPEX:6223"]
        self.assertEqual(stock["score"], 0)
        conn.close()


class TestLookupSectorData(unittest.TestCase):
    """W2.2.3 _lookup_sector_data 純函式"""

    @staticmethod
    def _wl():
        return {
            "台股板塊": {
                "半導體設備耗材": {
                    "成員": [{"code": "TPEX:6223", "name": "旺矽"}],
                    "長子": ["TPEX:6223"],
                },
            },
            "國際族群": {
                "半導體設備_材料_封測": {
                    "成員": [{"code": "NASDAQ:ASML", "name": "ASML"}],
                    "長子": ["NASDAQ:ASML"],
                    "對應台股族群": ["半導體設備耗材"],
                },
            },
        }

    def test_symbol_in_sector_with_activated_leader(self):
        data = run_filters_v2._lookup_sector_data(
            "TPEX:6223", self._wl(),
            {"sectors": {"半導體設備耗材": "A"}},
            {"半導體設備_材料_封測": ["NASDAQ:ASML"]},
        )
        self.assertEqual(data["sector"], "半導體設備耗材")
        self.assertEqual(data["sector_level"], "A")
        self.assertTrue(data["intl_activated"])
        self.assertEqual(data["intl_leaders_activated"], ["NASDAQ:ASML"])

    def test_symbol_not_in_any_sector(self):
        data = run_filters_v2._lookup_sector_data(
            "TWSE:9999", self._wl(),
            {"sectors": {}},
            {},
        )
        self.assertIsNone(data["sector"])
        self.assertFalse(data["intl_activated"])

    def test_no_activated_leaders(self):
        data = run_filters_v2._lookup_sector_data(
            "TPEX:6223", self._wl(),
            {"sectors": {"半導體設備耗材": "A"}},
            {"半導體設備_材料_封測": []},   # 沒長子發動
        )
        self.assertEqual(data["sector"], "半導體設備耗材")
        self.assertFalse(data["intl_activated"])


class TestRotationTags(unittest.TestCase):
    """W2.2.6 _compute_rotation_tags 純函式 + integration"""

    def _setup_conn_with_history(self, *rows):
        """rows: (date, symbol, score, grade)"""
        from src.persistence import score_history_io
        conn = sqlite3.connect(":memory:")
        score_history_io.init_schema(conn)
        for date, symbol, score, grade in rows:
            conn.execute(
                "INSERT INTO score_history VALUES (?, ?, ?, ?, ?)",
                (date, symbol, score, grade, "T"),
            )
        conn.commit()
        return conn

    def _two_member_watchlist(self):
        return {
            "台股板塊": {
                "TestSector": {
                    "成員": [
                        {"code": "TWSE:111", "name": "A"},
                        {"code": "TWSE:222", "name": "B"},
                    ],
                    "長子": ["TWSE:111"],
                },
            },
            "國際族群": {},
        }

    def test_delta_2_0_triggers_rotation(self):
        """過去 5 日均分 1.0,今日均分 3.0 → delta=2.0 ≥ 2 → 全族群 ⭐"""
        rows = []
        for d in range(13, 18):
            rows.append((f"2026-05-{d}", "TWSE:111", 1.0, "D"))
            rows.append((f"2026-05-{d}", "TWSE:222", 1.0, "D"))
        conn = self._setup_conn_with_history(*rows)
        results = {
            "TWSE:111": {"score": 3.0, "grade": "C", "tags": []},
            "TWSE:222": {"score": 3.0, "grade": "C", "tags": []},
        }
        tags = run_filters_v2._compute_rotation_tags(
            results, self._two_member_watchlist(), conn, "2026-05-18",
        )
        self.assertIn("TWSE:111", tags)
        self.assertIn("TWSE:222", tags)
        self.assertIn("⭐ 個股輪動", tags["TWSE:111"][0])
        conn.close()

    def test_delta_1_99_no_rotation(self):
        """delta=1.99 < 2 → no rotation"""
        rows = []
        for d in range(13, 18):
            rows.append((f"2026-05-{d}", "TWSE:111", 1.0, "D"))
        conn = self._setup_conn_with_history(*rows)
        results = {"TWSE:111": {"score": 2.99, "grade": "D", "tags": []}}
        tags = run_filters_v2._compute_rotation_tags(
            results,
            {"台股板塊": {"S": {"成員": [{"code": "TWSE:111", "name": "A"}], "長子": []}},
             "國際族群": {}},
            conn, "2026-05-18",
        )
        self.assertEqual(tags, {})
        conn.close()

    def test_history_insufficient_no_rotation(self):
        """只有 3 天歷史 → skip rotation"""
        rows = []
        for d in (13, 14, 15):
            rows.append((f"2026-05-{d}", "TWSE:111", 1.0, "D"))
        conn = self._setup_conn_with_history(*rows)
        results = {"TWSE:111": {"score": 999, "grade": "S", "tags": []}}
        tags = run_filters_v2._compute_rotation_tags(
            results,
            {"台股板塊": {"S": {"成員": [{"code": "TWSE:111", "name": "A"}], "長子": []}},
             "國際族群": {}},
            conn, "2026-05-18",
        )
        self.assertEqual(tags, {})
        conn.close()

    def test_skipped_members_dont_pollute(self):
        """today 某成員 skip(不在 results)→ today_avg 只算其他成員"""
        rows = []
        for d in range(13, 18):
            rows.append((f"2026-05-{d}", "TWSE:111", 1.0, "D"))
            rows.append((f"2026-05-{d}", "TWSE:222", 1.0, "D"))
        conn = self._setup_conn_with_history(*rows)
        # 只有 TWSE:111 有 result(TWSE:222 今天停牌)
        results = {"TWSE:111": {"score": 4.0, "grade": "B", "tags": []}}
        tags = run_filters_v2._compute_rotation_tags(
            results, self._two_member_watchlist(), conn, "2026-05-18",
        )
        # today_avg = 4.0,past_5_avg = 1.0,delta = 3.0 ≥ 2 → 觸發
        # 但 ⭐ 只加給今天有 result 的成員(TWSE:222 沒 result,跳過)
        self.assertIn("TWSE:111", tags)
        self.assertNotIn("TWSE:222", tags)
        conn.close()

    def test_wangxi_8day_fixture_no_rotation(self):
        """旺矽 8 天 fixture(1 個成員 + 低分)永遠不觸發 rotation。
        鎖死 W2.2.6 不破壞「不變承諾」。"""
        conn = setup_fixture_kline_db()
        # 跑完整 8 天累積 history,每天 result 餵進 score_history
        for d in ("2026-05-13", "2026-05-14", "2026-05-15", "2026-05-16",
                  "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"):
            result = run_filters_v2.run_pipeline(
                date       = d,
                conn_kline = conn,
                conn_etf   = None,
                weights    = load_real_weights(),
                sectors    = FIXTURE_SECTORS,
                key_prices = FIXTURE_KEY_PRICES,
                watchlist  = FIXTURE_WATCHLIST,
                now_iso    = f"{d}T19:00:00+08:00",
            )
        # 最後一天的 result 不該有 ⭐
        stock = result["stocks"]["TPEX:6223"]
        self.assertNotIn("⭐ 個股輪動", " ".join(stock["tags"]))
        # 但 score_history 應該有 8 筆旺矽紀錄
        cur = conn.execute(
            "SELECT COUNT(*) FROM score_history WHERE symbol='TPEX:6223'"
        )
        self.assertEqual(cur.fetchone()[0], 8)
        conn.close()


class TestMacdIntegration(unittest.TestCase):
    """W2.2.7 MACD 整合測試"""

    def test_wangxi_8day_fixture_no_macd_tag(self):
        """旺矽 8 天 fixture(< 50 天)→ MACD 永不觸發 → 不變承諾保持"""
        conn = setup_fixture_kline_db()
        result = run_filters_v2.run_pipeline(
            date       = "2026-05-14",
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = "X",
        )
        tags_joined = " ".join(result["stocks"]["TPEX:6223"]["tags"])
        self.assertNotIn("MACD", tags_joined)
        conn.close()

    def _setup_kline_with_closes(self, closes: list[float]) -> sqlite3.Connection:
        """建 in-memory kline.db 含旺矽 N 天 K 線(close 自訂)"""
        from datetime import date, timedelta
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE kline ("
            "  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
            "  close REAL, volume REAL, PRIMARY KEY (symbol, date))"
        )
        start = date(2026, 1, 1)
        for i, c in enumerate(closes):
            d = (start + timedelta(days=i)).isoformat()
            # OHL 設成偏離 4640 避免 standing 干擾
            conn.execute(
                "INSERT INTO kline VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("TPEX:6223", d, c, c, c, c, 1_000_000),
            )
        conn.commit()
        return conn

    def test_strict_50_days_minimum_history(self):
        """49 天 history → MACD 不發 tag(strict 50)"""
        conn = self._setup_kline_with_closes([100.0] * 49)
        result = run_filters_v2.run_pipeline(
            date       = "2026-02-18",   # 49th day from 2026-01-01
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = {"stocks": {}},
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = "X",
        )
        tags = result["stocks"]["TPEX:6223"]["tags"]
        self.assertEqual([t for t in tags if "MACD" in t], [])
        conn.close()

    def test_macd_green_to_red_scores_1_and_emits_tag(self):
        """動能轉多(OSC 負→正)→ tag + 計分 +1 + details。
        2026-05-28 規格修訂:用 2 根 OSC 偵測,當天就報。"""
        import unittest.mock as mock
        weights = load_real_weights()
        kline_history = [{"close": 100.0} for _ in range(50)]
        mocked_osc = [None] * 48 + [-0.5, 0.7]   # 負→正
        with mock.patch.object(
            run_filters_v2.macd, "compute_macd",
            return_value={"dif": [], "dea": [], "osc": mocked_osc},
        ):
            tags, score, details = run_filters_v2._compute_macd(
                "X", "X", kline_history, weights,
            )
        self.assertIn("⚡ MACD 動能轉多(買點)", tags)
        self.assertEqual(score, 1)
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["module"], "macd")
        self.assertEqual(details[0]["evidence"]["transition"], "green_to_red")

    def test_macd_red_to_green_tag_only_no_score(self):
        """動能轉空(OSC 正→負)→ 純標籤,score=0,無 details。"""
        import unittest.mock as mock
        weights = load_real_weights()
        kline_history = [{"close": 100.0} for _ in range(50)]
        mocked_osc = [None] * 48 + [0.5, -0.7]   # 正→負
        with mock.patch.object(
            run_filters_v2.macd, "compute_macd",
            return_value={"dif": [], "dea": [], "osc": mocked_osc},
        ):
            tags, score, details = run_filters_v2._compute_macd(
                "X", "X", kline_history, weights,
            )
        self.assertIn("⚡ MACD 動能轉空", tags)
        self.assertEqual(score, 0)
        self.assertEqual(details, [])

    def test_macd_no_transition_returns_empty(self):
        """無轉換 → 三個 empty"""
        import unittest.mock as mock
        weights = load_real_weights()
        kline_history = [{"close": 100.0} for _ in range(50)]
        mocked_osc = [None] * 48 + [0.3, 0.7]   # 紅紅(無轉換)
        with mock.patch.object(
            run_filters_v2.macd, "compute_macd",
            return_value={"dif": [], "dea": [], "osc": mocked_osc},
        ):
            tags, score, details = run_filters_v2._compute_macd(
                "X", "X", kline_history, weights,
            )
        self.assertEqual(tags, [])
        self.assertEqual(score, 0)
        self.assertEqual(details, [])

    def test_constant_closes_no_transition(self):
        """全 100 → OSC 全 0 → strict <、> 不滿足 → 永不觸發 MACD tag"""
        conn = self._setup_kline_with_closes([100.0] * 60)
        from datetime import date, timedelta
        last_date = (date(2026, 1, 1) + timedelta(days=59)).isoformat()
        result = run_filters_v2.run_pipeline(
            date       = last_date,
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = {"stocks": {}},
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = "X",
        )
        tags = result["stocks"]["TPEX:6223"]["tags"]
        self.assertEqual([t for t in tags if "MACD" in t], [])
        conn.close()


class TestLookupStockMeta(unittest.TestCase):
    """_lookup_stock_meta(2026-05-31 加,name/sector 寫進 stocks entry 用)"""

    def _watchlist(self):
        return {
            "台股板塊": {
                "半導體設備耗材": {
                    "成員": [{"code": "TPEX:6223", "name": "旺矽"}],
                    "長子": [],
                },
            },
            "國際族群": {
                "AI 龍頭": {
                    "成員": [{"code": "NASDAQ:NVDA", "name": "NVIDIA"}],
                    "長子": [],
                    "對應台股族群": [],
                },
            },
        }

    def test_lookup_tw_stock(self):
        name, sector = run_filters_v2._lookup_stock_meta(
            "TPEX:6223", self._watchlist(),
        )
        self.assertEqual(name, "旺矽")
        self.assertEqual(sector, "半導體設備耗材")

    def test_lookup_missing_returns_empty(self):
        name, sector = run_filters_v2._lookup_stock_meta(
            "TWSE:9999", self._watchlist(),
        )
        self.assertEqual(name, "")
        self.assertEqual(sector, "")


class TestEtfDecreaseTag(unittest.TestCase):
    """⛔ ETF 減碼純標籤(2026-05-29 朋友 review 後新增)
    純標籤不計分,符合「ETF 減碼純標籤,不影響純加分制」決定。"""

    def _setup_etf(self, *ops) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE operations ("
            "  etf TEXT, 代號 TEXT, 日期 TEXT, 動作 TEXT, 張數 INTEGER)"
        )
        for o in ops:
            conn.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?)", o)
        conn.commit()
        return conn

    def test_etf_decrease_2_or_more_triggers_tag(self):
        """≥2 檔 ETF 減碼 → 發 ⛔ 標籤"""
        conn = self._setup_etf(
            ("00981A", "6223", "2026-05-20", "減碼", 200),
            ("00987A", "6223", "2026-05-20", "減碼", 250),
        )
        tags = run_filters_v2._compute_etf_decrease_tag(
            "TPEX:6223", "2026-05-20", conn,
        )
        self.assertEqual(len(tags), 1)
        self.assertIn("⛔ ETF 減碼", tags[0])
        conn.close()

    def test_etf_decrease_only_1_no_tag(self):
        """只 1 檔 ETF 減碼 → 不發標籤"""
        conn = self._setup_etf(
            ("00981A", "6223", "2026-05-20", "減碼", 200),
        )
        tags = run_filters_v2._compute_etf_decrease_tag(
            "TPEX:6223", "2026-05-20", conn,
        )
        self.assertEqual(tags, [])
        conn.close()

    def test_etf_decrease_format_correct(self):
        """標籤格式:⛔ ETF 減碼(2 檔, -450 張)。
        含 清倉 算入(跟 v1 chip_etf SELL_ACTIONS 一致)。"""
        conn = self._setup_etf(
            ("00981A", "6223", "2026-05-20", "減碼", 200),
            ("00987A", "6223", "2026-05-20", "清倉", 250),
        )
        tags = run_filters_v2._compute_etf_decrease_tag(
            "TPEX:6223", "2026-05-20", conn,
        )
        self.assertEqual(tags, ["⛔ ETF 減碼(2 檔, -450 張)"])
        conn.close()

    def test_etf_decrease_does_not_affect_score(self):
        """⛔ 是純標籤,total score 不變。
        旺矽 fixture 5/14 給定價 +0.7,加 2 檔 ETF 減碼 → 仍 0.7(不扣)。"""
        kline_conn = setup_fixture_kline_db()
        etf_conn = self._setup_etf(
            ("00981A", "6223", "2026-05-14", "減碼", 200),
            ("00987A", "6223", "2026-05-14", "減碼", 250),
            # 5/13 也加一筆,讓 5/13 不受干擾
        )
        # 先跑 5/13 累積 TRIGGERED state
        run_filters_v2.run_pipeline(
            date="2026-05-13", conn_kline=kline_conn, conn_etf=etf_conn,
            weights=load_real_weights(), sectors=FIXTURE_SECTORS,
            key_prices=FIXTURE_KEY_PRICES, watchlist=FIXTURE_WATCHLIST,
            now_iso="X",
        )
        # 5/14:STANDING +0.7,同時 2 檔 ETF 減碼觸發 ⛔
        result = run_filters_v2.run_pipeline(
            date="2026-05-14", conn_kline=kline_conn, conn_etf=etf_conn,
            weights=load_real_weights(), sectors=FIXTURE_SECTORS,
            key_prices=FIXTURE_KEY_PRICES, watchlist=FIXTURE_WATCHLIST,
            now_iso="X",
        )
        stock = result["stocks"]["TPEX:6223"]
        # 分數仍 0.7(ETF 減碼不扣)
        self.assertAlmostEqual(stock["score"], 0.7)
        # 但 ⛔ 標籤要在
        self.assertTrue(any("⛔ ETF 減碼" in t for t in stock["tags"]),
                        f"⛔ tag missing in {stock['tags']}")
        kline_conn.close()
        etf_conn.close()


class TestMissingData(unittest.TestCase):
    """邊界:沒 K 線資料的 symbol 跳過,不算進 results"""

    def test_symbol_not_in_kline_is_skipped(self):
        # fixture watchlist 有 TPEX:6223,但 fixture kline 加入「不存在」的 symbol
        watchlist = {
            "台股板塊": {
                "X 板塊": {"成員": [{"code": "TWSE:9999", "name": "不存在"}], "長子": []},
            },
            "國際族群": {},
        }
        conn = setup_fixture_kline_db()   # 只含旺矽
        result = run_filters_v2.run_pipeline(
            date       = "2026-05-14",
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = {"stocks": {}},
            watchlist  = watchlist,
            now_iso    = "X",
        )
        conn.close()
        self.assertNotIn("TWSE:9999", result["stocks"])
        self.assertIn("TWSE:9999", result["metadata"]["skipped_symbols"])

    def test_symbol_without_data_for_date_is_skipped(self):
        """symbol 有舊資料但 date 當天沒有(停牌)→ skip"""
        conn = setup_fixture_kline_db()
        result = run_filters_v2.run_pipeline(
            date       = "2026-05-23",   # fixture 最大到 5/22
            conn_kline = conn,
            conn_etf   = None,
            weights    = load_real_weights(),
            sectors    = FIXTURE_SECTORS,
            key_prices = FIXTURE_KEY_PRICES,
            watchlist  = FIXTURE_WATCHLIST,
            now_iso    = "X",
        )
        conn.close()
        self.assertNotIn("TPEX:6223", result["stocks"])
        self.assertIn("TPEX:6223", result["metadata"]["skipped_symbols"])
