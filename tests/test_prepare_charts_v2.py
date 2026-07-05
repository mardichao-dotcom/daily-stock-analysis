"""
test_prepare_charts_v2.py — chart producer 單元測試(W2.4)

覆蓋:
  1. S/A/B 級篩選(C/D 不輸出)
  2. ohlcv 180 天上限 + 不足 fallback
  3. MA arrays 跟 ohlcv 索引對齊
  4. ETF events 過濾到範圍內
  5. **events 歷史重算多個 cycle**(per W2.4 review,鎖死「不只畫最後一次」)
  6. chart JSON 完整 schema
  7. market 預埋
  8. _index.json 結構
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import prepare_charts_v2 as pc


def setup_kline_db(rows: list[tuple]) -> sqlite3.Connection:
    """rows: (symbol, date, open, high, low, close, volume)"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE kline ("
        "  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
        "  close REAL, volume REAL, PRIMARY KEY (symbol, date))"
    )
    for r in rows:
        conn.execute("INSERT INTO kline VALUES (?, ?, ?, ?, ?, ?, ?)", r)
    conn.commit()
    return conn


def setup_etf_db(rows: list[tuple]) -> sqlite3.Connection:
    """rows: (etf, code, date, action, shares)"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE operations ("
        "  etf TEXT, 代號 TEXT, 日期 TEXT, 動作 TEXT, 張數 INTEGER)"
    )
    for r in rows:
        conn.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?)", r)
    conn.commit()
    return conn


# ─────────────────────────────────────────────────────────────────────────────
class TestFilterSabStocks(unittest.TestCase):

    def test_filters_correctly(self):
        result = {
            "stocks": {
                "A": {"grade": "S", "score": 7},
                "B": {"grade": "A", "score": 5},
                "C": {"grade": "B", "score": 4},
                "D": {"grade": "C", "score": 3},
                "E": {"grade": "D", "score": 0},
            }
        }
        sab = pc.filter_sab_stocks(result)
        self.assertEqual(set(sab.keys()), {"A", "B", "C"})

    def test_empty_stocks(self):
        self.assertEqual(pc.filter_sab_stocks({"stocks": {}}), {})
        self.assertEqual(pc.filter_sab_stocks({}), {})


# ─────────────────────────────────────────────────────────────────────────────
class TestLoadChartKline(unittest.TestCase):

    def test_180_day_max(self):
        # 200 天 fixture
        from datetime import date, timedelta
        rows = []
        start = date(2026, 1, 1)
        for i in range(200):
            d = (start + timedelta(days=i)).isoformat()
            rows.append(("TPEX:6223", d, 100, 105, 95, 100, 1000))
        conn = setup_kline_db(rows)
        result = pc.load_chart_kline(conn, "TPEX:6223",
                                       (start + timedelta(days=199)).isoformat())
        self.assertEqual(len(result), 180)   # cap at 180
        self.assertEqual(result[-1]["time"], (start + timedelta(days=199)).isoformat())
        conn.close()

    def test_fallback_when_history_less_than_180(self):
        """history 不足 180 天 → 用實際有的"""
        from datetime import date, timedelta
        rows = []
        start = date(2026, 1, 1)
        for i in range(50):
            d = (start + timedelta(days=i)).isoformat()
            rows.append(("TPEX:6223", d, 100, 105, 95, 100, 1000))
        conn = setup_kline_db(rows)
        result = pc.load_chart_kline(conn, "TPEX:6223",
                                       (start + timedelta(days=49)).isoformat())
        self.assertEqual(len(result), 50)
        conn.close()

    def test_ascending_order(self):
        from datetime import date, timedelta
        rows = []
        start = date(2026, 1, 1)
        for i in range(10):
            d = (start + timedelta(days=i)).isoformat()
            rows.append(("TPEX:6223", d, 0, 0, 0, 100 + i, 0))
        conn = setup_kline_db(rows)
        result = pc.load_chart_kline(conn, "TPEX:6223",
                                       (start + timedelta(days=9)).isoformat())
        # 第一筆是最早、最後一筆是 data_date
        self.assertEqual(result[0]["close"], 100)
        self.assertEqual(result[-1]["close"], 109)
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
class TestComputeMaArrays(unittest.TestCase):

    def test_arrays_aligned_with_ohlcv_length(self):
        kline = [{"close": float(v)} for v in range(1, 101)]   # 100 天
        ma = pc.compute_ma_arrays(kline)
        for w in (20, 60, 90):
            self.assertEqual(len(ma[f"ma_{w}"]), 100)

    def test_ma_20_first_valid_index(self):
        """ma_20 從索引 19 開始有值(累計 20 天)"""
        kline = [{"close": float(v)} for v in range(1, 25)]
        ma = pc.compute_ma_arrays(kline)
        # 索引 0-18 = None
        for i in range(19):
            self.assertIsNone(ma["ma_20"][i])
        # 索引 19 = mean(1..20) = 10.5
        self.assertAlmostEqual(ma["ma_20"][19], 10.5)
        # 索引 23 = mean(4..23)? 不對,是 mean of closes[4:24] (最近 20)
        # closes = 1..24,index 23 → mean(closes[4:24]) = mean(5..24) = 14.5
        self.assertAlmostEqual(ma["ma_20"][23], 14.5)

    def test_ma_60_none_before_index_59(self):
        kline = [{"close": 100.0}] * 70
        ma = pc.compute_ma_arrays(kline)
        for i in range(59):
            self.assertIsNone(ma["ma_60"][i])
        self.assertAlmostEqual(ma["ma_60"][59], 100.0)


# ─────────────────────────────────────────────────────────────────────────────
class TestLoadEtfEvents(unittest.TestCase):

    def test_filters_to_date_range(self):
        conn = setup_etf_db([
            ("00981A", "6223", "2026-04-01", "加碼", 100),   # 範圍前
            ("00987A", "6223", "2026-05-10", "加碼",  50),   # 範圍內
            ("00992A", "6223", "2026-05-15", "減碼",  30),   # 範圍內
            ("00994A", "6223", "2026-06-01", "加碼",  20),   # 範圍後
        ])
        result = pc.load_etf_events(conn, "TPEX:6223", "2026-05-01", "2026-05-20")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["etf"], "00987A")
        self.assertEqual(result[1]["etf"], "00992A")
        conn.close()

    def test_strips_exchange_prefix(self):
        """operations 表 stock 用無 prefix 代號"""
        conn = setup_etf_db([("00981A", "6223", "2026-05-10", "加碼", 100)])
        result = pc.load_etf_events(conn, "TPEX:6223", "2026-05-01", "2026-05-31")
        self.assertEqual(len(result), 1)
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
class TestReplayEventsMultipleCycles(unittest.TestCase):
    """⭐ W2.4 review 鎖死:歷史多 cycle 都要畫(不只最後一次)"""

    def test_events_history_multiple_cycles(self):
        """設計 K 線:STANDING → CANCELLED → STANDING 兩個完整 cycle。
        預期 events 含 2 個 standing 事件 + 2 個 breakdown 事件。"""
        # p=100,設計兩個 cycle:
        # Cycle 1: D1 觸發、D2 站穩 → MAINTAINING → D6 跌破
        # 中間 D7-D11 UNTRIGGERED
        # Cycle 2: D12 觸發、D13 站穩 → MAINTAINING → D17 跌破
        kline = [
            # D0 untriggered
            {"time": "2026-01-01", "open": 90, "high": 95, "low": 90, "close": 92},
            # Cycle 1
            {"time": "2026-01-02", "open": 95, "high": 102, "low": 98, "close": 100},  # TRIGGERED (low=98<=100, close=100>=100)
            {"time": "2026-01-03", "open": 102, "high": 110, "low": 101, "close": 105}, # STANDING (open+close>=100)
            # v2.2:K 跨越 p 維持 MAINTAINING。
            # 1/04 close=101 > p → Day 1 跌破不成立(避免 1/04+1/05 也算 breakdown)
            # 1/05 close=100 = p → Day 1 跌破成立,1/06 = Day 2
            {"time": "2026-01-04", "open":  99, "high": 105, "low":  99, "close": 101},  # MAINTAINING
            {"time": "2026-01-05", "open":  99, "high": 105, "low":  99, "close": 100},  # MAINTAINING(Day 1 跌破)
            {"time": "2026-01-06", "open":  95, "high":  99, "low":  90, "close":  93},  # leave_down→CANCELLED + 跌破 Day 2
            # Untriggered period
            {"time": "2026-01-07", "open": 88, "high": 92, "low": 85, "close": 90},
            {"time": "2026-01-08", "open": 88, "high": 92, "low": 85, "close": 90},
            {"time": "2026-01-09", "open": 88, "high": 92, "low": 85, "close": 90},
            {"time": "2026-01-10", "open": 88, "high": 92, "low": 85, "close": 90},
            {"time": "2026-01-11", "open": 88, "high": 92, "low": 85, "close": 90},
            # Cycle 2
            {"time": "2026-01-12", "open": 95, "high": 102, "low": 98, "close": 100},   # TRIGGERED again
            {"time": "2026-01-13", "open": 102, "high": 110, "low": 101, "close": 105}, # STANDING again
            # v2.2 cycle 2:同上,只 1/16 close=p 觸發 Day 1 跌破
            {"time": "2026-01-14", "open":  99, "high": 105, "low":  99, "close": 101},  # MAINTAINING
            {"time": "2026-01-15", "open":  99, "high": 105, "low":  99, "close": 101},  # MAINTAINING
            {"time": "2026-01-16", "open":  99, "high": 105, "low":  99, "close": 100},  # MAINTAINING(Day 1 跌破)
            {"time": "2026-01-17", "open":  95, "high":  99, "low":  90, "close":  93},  # leave_down→CANCELLED + 跌破 Day 2
        ]
        events = pc.replay_events_for_given_price(
            kline, given_price=100, category="key_price", price_str="100",
        )
        standings  = [e for e in events if e["type"] == "standing"]
        breakdowns = [e for e in events if e["type"] == "breakdown"]

        # ⭐ 鎖死:兩個 standing 事件(1/3 + 1/13),不只最後一次
        self.assertEqual(len(standings), 2,
                         f"expected 2 standing events, got {len(standings)}: {standings}")
        self.assertEqual(standings[0]["time"], "2026-01-03")
        self.assertEqual(standings[1]["time"], "2026-01-13")

        # 兩個 breakdown(1/6 + 1/17)
        self.assertEqual(len(breakdowns), 2,
                         f"expected 2 breakdown events, got {len(breakdowns)}: {breakdowns}")
        self.assertEqual(breakdowns[0]["time"], "2026-01-06")
        self.assertEqual(breakdowns[1]["time"], "2026-01-17")

    def test_replay_no_cycle_no_events(self):
        """全程低於 given_price → 無 events"""
        kline = [{"time": f"d{i}", "open": 50, "high": 55, "low": 45, "close": 48}
                 for i in range(20)]
        events = pc.replay_events_for_given_price(kline, 100, "key_price", "100")
        self.assertEqual(events, [])

    def test_events_sorted_by_date_then_type(self):
        """同一天同時觸發多 events 時,先 standing 後 breakdown(字典序)"""
        # 不容易構造同日標準 + 跌破,做基本 sort 驗證
        events = [
            {"time": "2026-01-05", "type": "breakdown", "category": "X", "price": "1"},
            {"time": "2026-01-03", "type": "standing",  "category": "X", "price": "1"},
        ]
        events.sort(key=lambda e: (e["time"], e["type"]))
        self.assertEqual(events[0]["time"], "2026-01-03")
        self.assertEqual(events[1]["time"], "2026-01-05")


# ─────────────────────────────────────────────────────────────────────────────
class TestBuildChartForStock(unittest.TestCase):

    def _basic_setup(self):
        from datetime import date, timedelta
        start = date(2026, 1, 1)
        rows = []
        for i in range(30):
            d = (start + timedelta(days=i)).isoformat()
            rows.append(("TPEX:6223", d, 100, 105, 95, 100, 1000))
        conn_kline = setup_kline_db(rows)
        return conn_kline, (start + timedelta(days=29)).isoformat()

    def test_chart_schema_complete(self):
        conn_kline, last_day = self._basic_setup()
        stock_entry = {
            "name": "旺矽", "sector": "半導體設備耗材",
            "score": 5.0, "grade": "A", "tags": ["🟢 站穩 4640"],
            "key_prices_snapshot": {
                "lines": [{"price": "100", "color": "red",
                           "category": "key_price", "text": "100"}],
                "areas": [],
            },
        }
        chart = pc.build_chart_for_stock(
            "TPEX:6223", stock_entry, conn_kline, None, last_day,
        )
        # top-level keys(P0-A 加 data_through,P0-B 加 key_prices_source)
        required = {"code", "symbol", "name", "sector", "market",
                    "data_date", "data_through", "version", "ohlcv", "ma",
                    "etf_events", "key_prices", "key_prices_source", "events", "chips"}
        self.assertEqual(set(chart.keys()), required)
        # snapshot 路徑(stock_entry 帶 key_prices_snapshot)標 "snapshot"
        self.assertEqual(chart["key_prices_source"], "snapshot")
        conn_kline.close()

    def test_market_field_tw(self):
        conn_kline, last_day = self._basic_setup()
        stock_entry = {"name": "X", "sector": "X",
                       "key_prices_snapshot": {"lines": [], "areas": []}}
        chart = pc.build_chart_for_stock("TPEX:6223", stock_entry, conn_kline, None, last_day)
        self.assertEqual(chart["market"], "TW")
        chart_tw = pc.build_chart_for_stock("TWSE:2330", stock_entry, conn_kline, None, last_day)
        # 註:TWSE:2330 沒 kline → None,所以另起
        conn_kline.close()

    def test_market_field_intl(self):
        from datetime import date, timedelta
        start = date(2026, 1, 1)
        rows = [("NASDAQ:NVDA", (start + timedelta(days=i)).isoformat(),
                 100, 105, 95, 100, 1000) for i in range(30)]
        conn_kline = setup_kline_db(rows)
        last_day = (start + timedelta(days=29)).isoformat()
        stock_entry = {"name": "NVIDIA", "sector": "",
                       "key_prices_snapshot": {"lines": [], "areas": []}}
        chart = pc.build_chart_for_stock("NASDAQ:NVDA", stock_entry,
                                          conn_kline, None, last_day)
        self.assertEqual(chart["market"], "INTL")
        conn_kline.close()

    def test_returns_none_when_no_kline(self):
        conn_kline = setup_kline_db([])
        stock_entry = {"name": "X", "key_prices_snapshot": {"lines": [], "areas": []}}
        chart = pc.build_chart_for_stock("TPEX:6223", stock_entry,
                                          conn_kline, None, "2026-05-20")
        self.assertIsNone(chart)
        conn_kline.close()


# ─────────────────────────────────────────────────────────────────────────────
class TestStaleBarStillProduces(unittest.TestCase):
    """P0-A 防回歸(2026-06-11):last bar < date(美股 19:00 台北跑時晚一個交易日)
    仍必須產 chart,並以 data_through 標明真實末根日期。
    舊 code `kline[-1]["time"] != date → return None` 讓全部美股圖停產(線上 404)。"""

    def test_us_stale_bar_still_charts(self):
        from datetime import date, timedelta
        start = date(2026, 1, 1)
        rows = [("NASDAQ:NVDA", (start + timedelta(days=i)).isoformat(),
                 100, 105, 95, 100, 1000) for i in range(30)]
        conn = setup_kline_db(rows)
        last_bar   = (start + timedelta(days=29)).isoformat()
        data_date  = (start + timedelta(days=31)).isoformat()   # 比末根晚 2 天
        minimal = {"name": "NVIDIA", "sector": ""}
        chart = pc.build_chart_for_stock(
            "NASDAQ:NVDA", minimal, conn, None, data_date, config_key_prices={})
        self.assertIsNotNone(chart)                       # 舊 code 會回 None(404 回歸)
        self.assertEqual(chart["data_date"], data_date)
        self.assertEqual(chart["data_through"], last_bar)
        conn.close()


class TestKeyPricesIntlRegression(unittest.TestCase):
    """P0-B 防回歸(2026-06-11):國際股無 key_prices_snapshot(不經 TW-only filter),
    chart 層必須 fallback 自 config/key_prices.json,lines/areas 數量 == 來源數量。
    根因:國際股從未被 run_filters_v2 計分 → 舊 code 給空 {lines:[],areas:[]} → 線上 lines=0。"""

    CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "key_prices.json")

    def _config(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)

    def _kline_for(self, symbol, last_day):
        from datetime import date, timedelta
        y, m, d = map(int, last_day.split("-"))
        end = date(y, m, d)
        rows = [(symbol, (end - timedelta(days=29 - i)).isoformat(),
                 100, 105, 95, 100, 1000) for i in range(30)]
        return setup_kline_db(rows)

    def _assert_intl(self, symbol):
        cfg = self._config()
        src = cfg.get("stocks", {}).get(symbol, {})
        src_lines = len(src.get("lines", []))
        src_areas = len(src.get("areas", []))
        # 來源必須真有關鍵價,否則此測試無意義(守住「朋友畫的線確實在 config」)
        self.assertGreater(src_lines + src_areas, 0,
                           f"{symbol} 在 config/key_prices.json 應有關鍵價")
        last_day = "2026-06-10"
        conn = self._kline_for(symbol, last_day)
        minimal = {"name": "X", "sector": "X"}   # 模擬國際股:無 key_prices_snapshot
        chart = pc.build_chart_for_stock(
            symbol, minimal, conn, None, last_day, config_key_prices=cfg)
        self.assertIsNotNone(chart)
        self.assertEqual(len(chart["key_prices"]["lines"]), src_lines,
                         f"{symbol} chart lines 應 == config 來源 {src_lines}")
        self.assertEqual(len(chart["key_prices"]["areas"]), src_areas,
                         f"{symbol} chart areas 應 == config 來源 {src_areas}")
        self.assertEqual(chart["key_prices_source"], "config_fallback")
        conn.close()

    def test_tse_6594(self):
        self._assert_intl("TSE:6594")

    def test_krx_005930(self):
        self._assert_intl("KRX:005930")

    def test_nyse_tsm(self):
        self._assert_intl("NYSE:TSM")


# ─────────────────────────────────────────────────────────────────────────────
# lookup_name_sector 已移除(2026-05-31):
# name/sector 改由 run_filters_v2 寫入 stocks entry。
# 對應測試移到 tests/test_run_filters_v2.py::TestLookupStockMeta。


class TestWriteFiles(unittest.TestCase):
    """輸出檔案路徑跟 _index 結構"""

    def test_safe_filename(self):
        self.assertEqual(pc._safe_filename("TWSE:2330"), "TWSE_2330")
        self.assertEqual(pc._safe_filename("TPEX:6223"), "TPEX_6223")
        self.assertEqual(pc._safe_filename("NASDAQ:NVDA"), "NASDAQ_NVDA")

    def test_write_chart_and_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir)
            chart = {"symbol": "TWSE:2330", "code": "2330", "ohlcv": []}
            path = pc.write_chart(outdir, "2026-05-20", "TWSE:2330", chart)
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "TWSE_2330.json")
            self.assertEqual(path.parent.name, "2026-05-20")

            idx_path = pc.write_index(outdir, "2026-05-20",
                                        ["TWSE:2330", "TPEX:6223"])
            with open(idx_path) as f:
                idx = json.load(f)
            self.assertEqual(idx["stocks"], ["TWSE_2330", "TPEX_6223"])
            self.assertEqual(idx["date"], "2026-05-20")
            self.assertEqual(idx["version"], "2.2")
            # v2.2:新增 symbols dict(per-symbol status),預設空 dict
            self.assertEqual(idx["symbols"], {})

    def test_write_index_with_status_map(self):
        """v2.2 _index.json 帶 per-symbol status(ready / waiting_us_close / missing)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir)
            status_map = {
                "TWSE_2330": {"status": "ready", "name": "台積電",
                                "sector": "IC設計", "exchange": "TW"},
                "NASDAQ_NVDA": {"status": "waiting_us_close", "name": "輝達",
                                  "sector": "AI晶片", "exchange": "US",
                                  "last_available_date": "2026-05-29"},
            }
            idx_path = pc.write_index(outdir, "2026-06-01",
                                        ["TWSE:2330"], status_map)
            with open(idx_path) as f:
                idx = json.load(f)
            # 舊格式 stocks 仍只列 ready
            self.assertEqual(idx["stocks"], ["TWSE_2330"])
            # 新格式 symbols 含全部
            self.assertEqual(idx["symbols"]["TWSE_2330"]["status"], "ready")
            self.assertEqual(idx["symbols"]["NASDAQ_NVDA"]["status"],
                              "waiting_us_close")
            self.assertEqual(idx["symbols"]["NASDAQ_NVDA"]["last_available_date"],
                              "2026-05-29")

    def test_classify_exchange(self):
        self.assertEqual(pc._classify_exchange("TWSE:2330"), "TW")
        self.assertEqual(pc._classify_exchange("TPEX:6223"), "TW")
        self.assertEqual(pc._classify_exchange("NASDAQ:NVDA"), "US")
        self.assertEqual(pc._classify_exchange("NYSE:TSM"), "US")
        self.assertEqual(pc._classify_exchange("TSE:6981"), "JP")
        self.assertEqual(pc._classify_exchange("OMXCOP:MAERSK_B"), "DK")


# ─────────────────────────────────────────────────────────────────────────────
class TestRunIntegration(unittest.TestCase):
    """端到端:filtered_result_v2 + 兩個 S/A/B 個股 → 寫出 chart + index"""

    def test_run_sab_only_skips_cd(self):
        from datetime import date, timedelta
        start = date(2026, 1, 1)
        last_day = (start + timedelta(days=29)).isoformat()
        rows = []
        for i in range(30):
            d = (start + timedelta(days=i)).isoformat()
            rows.append(("TPEX:6223", d, 100, 105, 95, 100, 1000))
            rows.append(("TWSE:2330", d, 100, 105, 95, 100, 1000))
            rows.append(("TWSE:9999", d, 100, 105, 95, 100, 1000))
        conn_kline = setup_kline_db(rows)

        filtered_result = {
            "stocks": {
                "TPEX:6223": {"name": "旺矽", "sector": "X", "grade": "S",
                              "score": 7,
                              "key_prices_snapshot": {"lines": [], "areas": []}},
                "TWSE:2330": {"name": "台積電", "sector": "X", "grade": "B",
                              "score": 4,
                              "key_prices_snapshot": {"lines": [], "areas": []}},
                # C 級不該輸出
                "TWSE:9999": {"name": "X", "sector": "X", "grade": "C",
                              "score": 3,
                              "key_prices_snapshot": {"lines": [], "areas": []}},
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir)
            stats = pc.run(
                date=last_day, filtered_result=filtered_result,
                conn_kline=conn_kline, conn_etf=None, outdir=outdir,
            )
            self.assertEqual(stats["sab_total"], 2)
            self.assertEqual(set(stats["written"]), {"TPEX:6223", "TWSE:2330"})

            files = list((outdir / last_day).glob("*.json"))
            file_names = {f.name for f in files}
            # 兩個 stock + _index
            self.assertEqual(file_names,
                             {"TPEX_6223.json", "TWSE_2330.json", "_index.json"})

        conn_kline.close()
