"""
test_fetch_events.py — stage9 Day1 事件中樞純函式

鎖住:ROC 轉換(含潤年邊界)、跨年民國年清單、FRED/FOMC 視窗過濾、
watchlist 過濾、schema 斷言、第四道護欄(抓取失敗保留前一日 + stale + 告警)。
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.scrape_conferences import roc_to_iso
from src import fetch_events as fe


class TestRocToIso(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(roc_to_iso("115/06/26"), "2026-06-26")
        self.assertEqual(roc_to_iso("115/01/01"), "2026-01-01")
        self.assertEqual(roc_to_iso("100/12/31"), "2011-12-31")

    def test_dash_separator(self):
        self.assertEqual(roc_to_iso("115-06-26"), "2026-06-26")

    def test_leap_year_boundary(self):
        # 民國117 = 2028(潤年)→ 02/29 合法
        self.assertEqual(roc_to_iso("117/02/29"), "2028-02-29")
        # 民國116 = 2027(非潤年)→ 02/29 非法
        self.assertIsNone(roc_to_iso("116/02/29"))
        # 一般 2 月底
        self.assertEqual(roc_to_iso("116/02/28"), "2027-02-28")

    def test_january_boundary(self):
        self.assertEqual(roc_to_iso("116/01/31"), "2027-01-31")
        self.assertIsNone(roc_to_iso("116/01/32"))

    def test_garbage(self):
        for bad in ("", "abc", "115/13/01", "115/00/05", "115/06", "115/6/31"):
            self.assertIsNone(roc_to_iso(bad), f"{bad!r} 應回 None")


class TestRocYearsCrossBoundary(unittest.TestCase):
    def test_midyear_single(self):
        self.assertEqual(fe.roc_years_for(date(2026, 7, 5)), ["115"])

    def test_near_yearend_two(self):
        # 12/15 距 12/31 = 16 天 < 30 → 查 115 + 116(免漏隔年 1 月法說會)
        self.assertEqual(fe.roc_years_for(date(2026, 12, 15)), ["115", "116"])

    def test_exact_30_days_boundary(self):
        # 12/01 距 12/31 = 30 天,not < 30 → 只 115
        self.assertEqual(fe.roc_years_for(date(2026, 12, 1)), ["115"])
        # 12/02 距 12/31 = 29 天 < 30 → 兩年
        self.assertEqual(fe.roc_years_for(date(2026, 12, 2)), ["115", "116"])

    def test_dec31(self):
        self.assertEqual(fe.roc_years_for(date(2026, 12, 31)), ["115", "116"])


class TestMacroEvents(unittest.TestCase):
    def test_fred_window_and_importance(self):
        today = date(2026, 7, 5)
        def fake_fetch(key, rid):
            return {10: ["2026-07-14", "2026-08-30"],   # CPI: 一在窗內一在窗外
                    50: ["2026-07-10"], 46: [], 53: [], 54: []}.get(rid, [])
        ev = fe.build_macro_events("k", today, "now", days=14, fetch=fake_fetch)
        dates = {e["date"] for e in ev}
        self.assertIn("2026-07-14", dates)        # 窗內
        self.assertNotIn("2026-08-30", dates)     # 窗外(>14天)
        cpi = next(e for e in ev if e["date"] == "2026-07-14")
        self.assertEqual(cpi["importance"], "high")
        self.assertEqual(cpi["type"], "macro")

    def test_fomc_filter(self):
        today = date(2026, 7, 20)   # 7/29 FOMC 在 14 天內
        ev = fe.build_fomc_events(today, "now", days=14)
        self.assertTrue(any("FOMC" in e["name"] for e in ev))
        self.assertTrue(all(e["importance"] == "high" for e in ev))


class TestConferenceEvents(unittest.TestCase):
    def test_watchlist_filter_and_window(self):
        today = date(2026, 7, 5)
        wl = {"2330": "TWSE", "6223": "TPEX"}
        rows = [
            {"code": "2330", "name": "台積電", "date": "2026-07-10",
             "time": "14:00", "place": "線上", "summary": "Q2 法說"},
            {"code": "9999", "name": "非觀察", "date": "2026-07-11",   # 不在 watchlist
             "time": "", "place": "", "summary": ""},
            {"code": "6223", "name": "旺矽", "date": "2026-09-01",      # 窗外(>30天)
             "time": "", "place": "", "summary": ""},
        ]
        ev = fe.conferences_to_events(rows, today, "now", wl)
        syms = {e["symbol"] for e in ev}
        self.assertEqual(syms, {"TWSE:2330"})      # 只留 watchlist + 窗內
        self.assertEqual(ev[0]["type"], "conference")


class TestValidate(unittest.TestCase):
    def test_empty_ok(self):
        self.assertTrue(fe.validate_conferences([]))

    def test_wellformed(self):
        rows = [{"code": "1", "name": "n", "date_roc": "115/07/10",
                 "date": "2026-07-10", "time": "", "place": "", "summary": ""}]
        self.assertTrue(fe.validate_conferences(rows))

    def test_layout_change_all_dates_none(self):
        # MOPS 版面變動 → date 全 None → 斷言 fail
        rows = [{"code": "x", "name": "y", "date_roc": "?", "date": None,
                 "time": "", "place": "", "summary": ""} for _ in range(5)]
        self.assertFalse(fe.validate_conferences(rows))

    def test_missing_keys(self):
        self.assertFalse(fe.validate_conferences([{"code": "1"}]))


class TestGuardrail4(unittest.TestCase):
    """抓取失敗 → 保留前一日 conference + stale + 告警;macro 照常。"""
    def test_preserves_prev_and_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = {
                "conference_source_date": "2026-07-04",
                "events": [
                    {"date": "2026-07-10", "type": "conference", "symbol": "TWSE:2330",
                     "name": "台積電", "title": "法說", "importance": "", "fetched_at": "x"},
                    {"date": "2026-07-14", "type": "macro", "name": "CPI",
                     "title": "CPI", "importance": "high", "fetched_at": "x"},
                ],
            }
            evp = os.path.join(tmp, "events.json")
            with open(evp, "w", encoding="utf-8") as f:
                json.dump(prev, f, ensure_ascii=False)

            orig_ev, orig_scrape = fe.EVENTS_JSON, fe.scrape_conferences_guarded
            fe.EVENTS_JSON = evp
            fe.scrape_conferences_guarded = lambda years: None   # 模擬抓取失敗
            alerted = []
            try:
                out, _ = fe.run(fred_key="", today=date(2026, 7, 5),
                                alert=lambda: alerted.append(1))
            finally:
                fe.EVENTS_JSON, fe.scrape_conferences_guarded = orig_ev, orig_scrape

            self.assertTrue(out["conference_stale"])
            self.assertEqual(out["conference_source_date"], "2026-07-04")
            # 前一日的 conference 被保留
            conf = [e for e in out["events"] if e["type"] == "conference"]
            self.assertEqual(len(conf), 1)
            self.assertEqual(conf[0]["symbol"], "TWSE:2330")
            self.assertEqual(alerted, [1])          # 有發告警


if __name__ == "__main__":
    unittest.main()
