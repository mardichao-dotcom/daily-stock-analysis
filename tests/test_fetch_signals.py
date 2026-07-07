"""
test_fetch_signals.py — stage12 訊號資料層(spec §2 + §6 反未來函數/解析正確性)
"""
from __future__ import annotations
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import fetch_signals as fs
from src import fetch_taifex as ft


def tmpdb(case: unittest.TestCase) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    case.addCleanup(os.unlink, f.name)
    return f.name


# ── 燈號發布日規則 ────────────────────────────────────────────────────────────
class TestLightReleaseDate(unittest.TestCase):
    CFG = {"day_of_next_month": 27, "holiday_shift": "next_business_day",
           "overrides": {"2020-03": "2020-04-28"}}

    def test_weekday_plain(self):
        # 2024-05 資料月 → 2024-06-27(四)不順延
        self.assertEqual(fs.light_release_date("2024-05", self.CFG, set()),
                         "2024-06-27")

    def test_weekend_shift(self):
        # 2026-05 資料月 → 2026-06-27(六)→ 順延 06-29(一)
        self.assertEqual(fs.light_release_date("2026-05", self.CFG, set()),
                         "2026-06-29")

    def test_holiday_shift(self):
        # 假日 config 命中 → 再順延
        self.assertEqual(
            fs.light_release_date("2024-05", self.CFG, {"2024-06-27"}),
            "2024-06-28")

    def test_december_cross_year(self):
        self.assertEqual(fs.light_release_date("2023-12", self.CFG, set()),
                         "2024-01-29")           # 01-27 六 → 01-29 一

    def test_override_wins(self):
        self.assertEqual(fs.light_release_date("2020-03", self.CFG, set()),
                         "2020-04-28")


# ── 燈號 CSV 解析(zip fixture)────────────────────────────────────────────────
def _light_zip(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        name = "景氣指標與燈號.csv".encode("big5").decode("cp437")
        z.writestr(name, "﻿" + csv_text)
    return buf.getvalue()


class TestLightParse(unittest.TestCase):
    CSV = ('"Date","領先指標綜合指數","景氣對策信號綜合分數","景氣對策信號"\n'
           "198201,12.3,-,-\n"
           "202605,132.2,39,紅\n"
           "202604,131.0,40,黃藍 \n"          # 尾空白(源檔實況)
           "202603,130.0,99,綠\n")            # 分數超界 → 剔除

    def test_parse(self):
        meta = {"result": {"distribution": [
            {"resourceFormat": "ZIP", "resourceDownloadUrl": "http://x/z.zip"}]}}
        rows = fs.fetch_light_rows(fetch_json=lambda u: meta,
                                   fetch_bytes=lambda u: _light_zip(self.CSV))
        self.assertEqual(rows, [("2026-05", 39.0, "紅"),
                                ("2026-04", 40.0, "黃藍")])   # strip + '-' 跳過 + 超界剔除


# ── 克里夫蘭 CPI events ──────────────────────────────────────────────────────
def _cpi_doc(target="2024-5", labels=None, now=None, actual=None):
    labels = labels or ["06/07", "06/10", "06/11", "06/12",
                        {"label": "CPI May", "vline": "true"}, "06/13"]
    cats = [{"label": l} if isinstance(l, str) else l for l in labels]
    def series(name, vals):
        return {"seriesname": name,
                "data": [{"value": v} for v in vals]}
    return [{"chart": {"subcaption": target},
             "categories": [{"category": cats}],
             "dataset": [series("CPI Inflation", now or ["0.09", "0.085", None, None, None, None]),
                         series("Actual CPI Inflation",
                                actual or [None, "0.0057", None, None, None, None])]}]


class TestCpiEvents(unittest.TestCase):
    def test_release_from_vline_not_actual_point(self):
        # Actual 點在 06/10(圖面布局),發布日必須取 vline 前一 label = 06/12
        rows = fs.parse_cpi_events(_cpi_doc())
        self.assertEqual(len(rows), 1)
        m, rel, act, now, sur = rows[0]
        self.assertEqual((m, rel), ("2024-05", "2024-06-12"))
        self.assertEqual(act, 0.0057)
        self.assertEqual(now, 0.085)              # 發布日前最後一筆 nowcast
        self.assertAlmostEqual(sur, round(0.0057 - 0.085, 4))

    def test_ongoing_month_skipped(self):
        doc = _cpi_doc(actual=[None] * 6,
                       labels=["06/07", "06/10", "06/11", "06/12", "06/13", "06/16"])
        self.assertEqual(fs.parse_cpi_events(doc), [])

    def test_cross_year_window(self):
        # 目標 2023-12(隔年 1 月發布):label 月 < 目標月 → 年 +1
        self.assertEqual(fs._label_to_iso("01/11", 2023, 12), "2024-01-11")
        self.assertEqual(fs._label_to_iso("12/29", 2023, 12), "2023-12-29")

    def test_window_lookback(self):
        # 目標月前 2 個月(窗口起點)不跨年
        self.assertEqual(fs._label_to_iso("08/01", 2013, 9), "2013-08-01")


# ── FOMC 行事曆解析 ──────────────────────────────────────────────────────────
class TestFedParse(unittest.TestCase):
    def test_historical_variants(self):
        html = """
        <h5 class="x">January 30-31 Meeting - 2018</h5>
        <h5 class="x">Jul/Aug 31-1 Meeting - 2018</h5>
        <h5 class="x">October 16 (unscheduled) - 2013</h5>
        <h5 class="x">March 2 (unscheduled) Meeting - 2020</h5>
        <h5 class="x">March 19 (notation vote) - 2020</h5>
        <h5 class="x">March 17-18 (cancelled) Meeting - 2020</h5>
        """
        got = fs.parse_fed_historical(html)
        self.assertIn(("2018-01-30", "2018-01-31", True), got)
        self.assertIn(("2018-07-31", "2018-08-01", True), got)   # 跨月
        self.assertIn(("2013-10-16", "2013-10-16", False), got)  # unscheduled 無 Meeting 字
        self.assertIn(("2020-03-02", "2020-03-02", False), got)
        self.assertEqual(len(got), 4)             # notation vote / cancelled 排除

    def test_calendar_segments(self):
        html = """
        <h4>2025 FOMC Meetings</h4>
        <div class="fomc-meeting__month col"><strong>January</strong></div>
        <div class="fomc-meeting__date col">28-29</div>
        <h4>2026 FOMC Meetings</h4>
        <div class="fomc-meeting__month col"><strong>April</strong></div>
        <div class="fomc-meeting__date col">28-29*</div>
        """
        got = fs.parse_fed_calendar(html)
        self.assertEqual(got, [("2025-01-28", "2025-01-29", True),
                               ("2026-04-28", "2026-04-29", True)])


# ── idx MA 計算 ──────────────────────────────────────────────────────────────
class TestIdxMa(unittest.TestCase):
    def test_ma_windows(self):
        db = tmpdb(self)
        rows = [(f"2026-01-{d:02d}", 100.0 + d) for d in range(1, 26)]
        fs.upsert_index(db, "TAIEX", rows, "test")
        c = sqlite3.connect(db)
        r19 = c.execute("SELECT ma20 FROM idx_daily WHERE date='2026-01-19'").fetchone()
        r20 = c.execute("SELECT ma20, ma60 FROM idx_daily WHERE date='2026-01-20'").fetchone()
        self.assertIsNone(r19[0])                 # 窗口不足 → NULL 不冒充
        # 手算:close 101..120 → 平均 110.5
        self.assertEqual(r20[0], 110.5)
        self.assertIsNone(r20[1])                 # 60 日不足

    def test_incremental_append_recomputes(self):
        db = tmpdb(self)
        rows = [(f"2026-01-{d:02d}", 100.0) for d in range(1, 21)]
        fs.upsert_index(db, "TAIEX", rows, "test")
        fs.upsert_index(db, "TAIEX", [("2026-01-21", 121.0)], "test")
        c = sqlite3.connect(db)
        v = c.execute("SELECT ma20 FROM idx_daily WHERE date='2026-01-21'").fetchone()[0]
        # 手算:19 天 100 + 121 → (19*100+121)/20 = 101.05
        self.assertEqual(v, 101.05)


# ── UMCSENT vintage → 首發日 ─────────────────────────────────────────────────
class TestUmichVintage(unittest.TestCase):
    def test_first_realtime_start_wins(self):
        payload = {"observations": [
            {"date": "2026-04-01", "value": "50.0", "realtime_start": "2026-04-24"},
            {"date": "2026-04-01", "value": "49.8", "realtime_start": "2026-05-10"},
            {"date": "2026-05-01", "value": "44.8", "realtime_start": "2026-05-22"},
            {"date": "2026-05-01", "value": ".", "realtime_start": "2026-06-01"},
        ]}
        rows = fs.fetch_umich_with_releases("2026-01-01", key="x",
                                            fetch=lambda u: payload)
        # 每月取最早 vintage 的值與日期(首次公開=生效日)
        self.assertEqual(rows, [("2026-04", 50.0, "2026-04-24"),
                                ("2026-05", 44.8, "2026-05-22")])


# ── USDTWD 雙源:暫代不覆蓋官方 ───────────────────────────────────────────────
class TestUsdtwdDualSource(unittest.TestCase):
    def test_provisional_never_overwrites_official(self):
        db = tmpdb(self)
        conn = sqlite3.connect(db)
        fs.ensure_tables(conn)
        conn.execute("INSERT INTO usdtwd_daily VALUES ('2026-07-02', 31.86, 'FRED DEXTAUS')")
        conn.commit()
        conn.close()
        orig = fs._yf_history
        fs._yf_history = lambda sym, start: [("2026-07-02", 31.90), ("2026-07-03", 31.93)]
        try:
            n = fs.upsert_usdtwd_provisional(db, "2026-07-01")
        finally:
            fs._yf_history = orig
        self.assertEqual(n, 1)                    # 只補 07-03
        c = sqlite3.connect(db)
        row = c.execute("SELECT rate, source FROM usdtwd_daily WHERE date='2026-07-02'").fetchone()
        self.assertEqual(row, (31.86, "FRED DEXTAUS"))


# ── 期交所 CSV 解析 ──────────────────────────────────────────────────────────
class TestTaifexParse(unittest.TestCase):
    HDR = ("日期,商品名稱,身份別,多方交易口數,多方交易契約金額(千元),空方交易口數,"
           "空方交易契約金額(千元),多空交易口數淨額,多空交易契約金額淨額(千元),"
           "多方未平倉口數,多方未平倉契約金額(千元),空方未平倉口數,"
           "空方未平倉契約金額(千元),多空未平倉口數淨額,多空未平倉契約金額淨額(千元)")

    def test_parse_foreign_only(self):
        txt = (self.HDR + "\n"
               "2026/07/06,臺股期貨,自營商,1,1,1,1,0,0,1,1,1,1,2670,1\n"
               "2026/07/06,臺股期貨,外資及陸資,1,1,1,1,0,0,1,1,1,1,-80087,1\n"
               "2026/07/06,小型臺指期貨,外資及陸資,1,1,1,1,0,0,1,1,1,1,999,1\n")
        self.assertEqual(ft.parse_csv(txt), [("2026-07-06", -80087)])

    def test_sane_cap(self):
        txt = (self.HDR + "\n"
               "2026/07/06,臺股期貨,外資及陸資,1,1,1,1,0,0,1,1,1,1,9999999,1\n")
        self.assertEqual(ft.parse_csv(txt), [])

    def test_empty_or_headerless(self):
        self.assertEqual(ft.parse_csv(""), [])
        self.assertEqual(ft.parse_csv("garbage\n1,2,3"), [])


# ── sanity 閘 ────────────────────────────────────────────────────────────────
class TestSanityGate(unittest.TestCase):
    def test_ranges(self):
        self.assertTrue(fs._sane("TAIEX", 28000))
        self.assertFalse(fs._sane("TAIEX", 1200))
        self.assertFalse(fs._sane("ZQ", 105))
        self.assertFalse(fs._sane("USDTWD", None))


if __name__ == "__main__":
    unittest.main()
