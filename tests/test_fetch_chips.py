"""
test_fetch_chips.py — stage9 §3.5 籌碼抓取。上市(T86)/上櫃(TPEx)格式不同,分開測試。
"""
from __future__ import annotations
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import fetch_chips as fc

# T86 官方 19 欄(順序即證交所官網)
T86_FIELDS = ['證券代號', '證券名稱',
              '外陸資買進股數(不含外資自營商)', '外陸資賣出股數(不含外資自營商)',
              '外陸資買賣超股數(不含外資自營商)',
              '外資自營商買進股數', '外資自營商賣出股數', '外資自營商買賣超股數',
              '投信買進股數', '投信賣出股數', '投信買賣超股數',
              '自營商買賣超股數',
              '自營商買進股數(自行買賣)', '自營商賣出股數(自行買賣)', '自營商買賣超股數(自行買賣)',
              '自營商買進股數(避險)', '自營商賣出股數(避險)', '自營商買賣超股數(避險)',
              '三大法人買賣超股數']


def _t86_row(code, foreign, trust, dealer, foreign_dealer="0"):
    r = [code, "測試", "0", "0", foreign, "0", "0", foreign_dealer,
         "0", "0", trust, dealer, "0", "0", "0", "0", "0", "0", "0"]
    return r


class TestHelpers(unittest.TestCase):
    def test_int_parse(self):
        self.assertEqual(fc._int("-3,892,544"), -3892544)
        self.assertEqual(fc._int("98755"), 98755)
        self.assertIsNone(fc._int(""))
        self.assertIsNone(fc._int("--"))

    def test_iso_conversions(self):
        self.assertEqual(fc._iso_ad("20260703"), "2026-07-03")


class TestListedT86(unittest.TestCase):
    """上市:逗號數字、西元日期、精確欄位(自營商 ≠ 外資自營商)。"""
    def _patch(self, payload):
        fc._http = lambda url, timeout=40: json.dumps(payload).encode("utf-8")

    def test_parse_and_dealer_not_foreign_dealer(self):
        self._patch({"stat": "OK", "date": "20260703", "fields": T86_FIELDS,
                     "data": [_t86_row("2345", "-188,279", "30,534", "-4,639",
                                        foreign_dealer="999")]})
        dd, m = fc.fetch_twse_t86("20260703")
        self.assertEqual(dd, "2026-07-03")
        self.assertEqual(m["2345"]["foreign"], -188279)
        self.assertEqual(m["2345"]["trust"], 30534)
        self.assertEqual(m["2345"]["dealer"], -4639)      # 自營商合計,非外資自營商(999)

    def test_stat_not_ok_raises(self):
        self._patch({"stat": "很抱歉，沒有符合條件的資料!"})
        with self.assertRaises(RuntimeError):
            fc.fetch_twse_t86("20260101")


def _dated(date, fields, data, stat="OK"):
    """帶日期端點的回應形狀:{stat, date, tables:[{fields, data}]}。"""
    return {"stat": stat, "date": date, "tables": [{"fields": fields, "data": data}]}


TPEX_3I_FIELDS = ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數合計"]
MI_MARGN_FIELDS = ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額",
                   "次一營業日限額", "買進", "賣出", "現券償還", "前日餘額", "今日餘額"]


class TestDatedEndpoints(unittest.TestCase):
    """2026-10-31:四支皆帶日期 + 自報日期;交易日由來源判定(stat ok + 自報日==請求日 + 有資料)。"""
    def _patch(self, payload):
        fc._http = lambda url, timeout=40: json.dumps(payload).encode("utf-8")

    def test_tpex_3insti_columns(self):
        # 外資(不含外資自營)=[4]、投信=[13]、自營合計=[22]
        row = ["3081", "聯亞"] + [str(i) for i in range(2, 24)]
        self._patch(_dated("20261029", TPEX_3I_FIELDS, [row], stat="ok"))
        self.assertEqual(fc.fetch_tpex_3insti_on("20261029")["3081"],
                         {"foreign": 4, "trust": 13, "dealer": 22})

    def test_tpex_3insti_field_drift_raises(self):
        self._patch(_dated("20261029", TPEX_3I_FIELDS[:-1], [["3081"] * 23], stat="ok"))
        with self.assertRaises(RuntimeError):
            fc.fetch_tpex_3insti_on("20261029")

    def test_twse_margin_takes_first_today_balance(self):
        row = ["2345", "智邦", "1", "2", "0", "1,800", "1,878", "9", "0", "0", "0", "5", "7"]
        payload = {"stat": "OK", "date": "20261029", "tables": [
            {"fields": ["項目", "買進"], "data": [["融資(交易單位)", "1"]]},
            {"fields": MI_MARGN_FIELDS, "data": [row]}]}
        self._patch(payload)
        self.assertEqual(fc.fetch_twse_margin_on("20261029")["2345"], 1878)   # 融資,非融券

    def test_tpex_margin(self):
        self._patch(_dated("20261029", ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額"],
                           [["3081", "聯亞", "6,000", "1", "2", "0", "4,711"]], stat="ok"))
        self.assertEqual(fc.fetch_tpex_margin_on("20261029")["3081"], 4711)

    def test_non_trading_day_returns_none(self):
        # TPEx 非交易日:stat ok 但 0 列;TWSE:stat 非 OK;自報日 ≠ 請求日 也不收
        self._patch(_dated("20261025", ["代號"], [], stat="ok"))
        self.assertIsNone(fc.fetch_tpex_margin_on("20261025"))
        self._patch({"stat": "很抱歉，沒有符合條件的資料"})
        self.assertIsNone(fc.fetch_twse_margin_on("20261025"))
        self._patch(_dated("20261028", ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額"],
                           [["3081", "聯亞", "1", "1", "1", "0", "9"]], stat="ok"))
        self.assertIsNone(fc.fetch_tpex_margin_on("20261029"))
        self._patch({"stat": "很抱歉，沒有符合條件的資料!"})
        self.assertIsNone(fc.fetch_twse_t86_on("20261025"))


class TestRunDated(unittest.TestCase):
    """run():入庫日 = 請求日(來源已驗證);既有非空值不被 NULL 覆蓋;run_auto 從最後齊全日續補。"""
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "k.db")
        self._saved = {k: getattr(fc, k) for k in (
            "get_all_tw_symbols", "fetch_twse_t86_on", "fetch_twse_margin_on",
            "fetch_tpex_3insti_on", "fetch_tpex_margin_on", "SOURCE_GAP_SEC")}
        fc.get_all_tw_symbols = lambda: ["TWSE:2345", "TPEX:3081"]
        fc.SOURCE_GAP_SEC = 0
        self.trading = {"20261029", "20261030"}
        self.margin_published = {"20261029"}
        on = lambda ymd: ymd in self.trading
        fc.fetch_twse_t86_on = lambda ymd: ({"2345": {"foreign": 1, "trust": 2, "dealer": 3}}
                                            if on(ymd) else None)
        fc.fetch_tpex_3insti_on = lambda ymd: ({"3081": {"foreign": 4, "trust": 5, "dealer": 6}}
                                               if on(ymd) else None)
        fc.fetch_twse_margin_on = lambda ymd: ({"2345": 100} if ymd in self.margin_published else None)
        fc.fetch_tpex_margin_on = lambda ymd: ({"3081": 200} if ymd in self.margin_published else None)

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(fc, k, v)
        self.tmp.cleanup()

    def _rows(self):
        import sqlite3
        conn = sqlite3.connect(self.db)
        r = conn.execute("SELECT symbol,date,foreign_net,margin_balance FROM chips "
                         "ORDER BY date,symbol").fetchall()
        conn.close()
        return r

    def test_non_trading_day_writes_nothing(self):
        out = fc.run("2026-10-25", self.db, with_tdcc=False)
        self.assertFalse(out["trading"])
        self.assertEqual(self._rows(), [])

    def test_partial_publish_then_fill_without_null_overwrite(self):
        fc.run("2026-10-30", self.db, with_tdcc=False)            # 19:00:法人有、融資未出
        self.assertEqual(self._rows(), [("TPEX:3081", "2026-10-30", 4, None),
                                        ("TWSE:2345", "2026-10-30", 1, None)])
        self.margin_published.add("20261030")                     # 隔輪:融資已出
        fc.run("2026-10-30", self.db, with_tdcc=False)
        self.assertEqual([r[3] for r in self._rows()], [200, 100])
        self.margin_published.discard("20261030")                 # 再跑一次來源暫缺 → 不被 NULL 蓋掉
        fc.run("2026-10-30", self.db, with_tdcc=False)
        self.assertEqual([r[3] for r in self._rows()], [200, 100])

    def test_run_auto_resumes_after_last_complete_day(self):
        from datetime import date as _d
        fc.run("2026-10-29", self.db, with_tdcc=False)            # 10/29 齊全
        outs = fc.run_auto(self.db, with_tdcc=False, today=_d(2026, 10, 31))
        self.assertEqual([o["date"] for o in outs if o["trading"]], ["2026-10-30"])
        # 10/30 融資未出 → 不算齊全,下一輪會再補 10/30
        self.assertIn(_d(2026, 10, 30), fc._days_to_fetch(self.db, _d(2026, 10, 31)))

    def test_run_auto_refills_incomplete_hole_before_last_complete(self):
        """停更回補情境:中間日只有部分欄位,之後某日已齊全 → 中間日仍要補。"""
        from datetime import date as _d
        self.trading = {"20261028", "20261029", "20261030"}
        fc.run("2026-10-28", self.db, with_tdcc=False)            # 10/28 融資未出(不齊全)
        self.margin_published |= {"20261030"}
        fc.run("2026-10-30", self.db, with_tdcc=False)            # 10/30 齊全
        self.margin_published |= {"20261028"}
        fc.run_auto(self.db, with_tdcc=False, today=_d(2026, 10, 31))
        conn = __import__("sqlite3").connect(self.db)
        n = conn.execute("SELECT COUNT(*) FROM chips WHERE date IN ('2026-10-28','2026-10-29') "
                         "AND margin_balance IS NOT NULL").fetchone()[0]
        conn.close()
        self.assertEqual(n, 4)                                     # 10/28 補上融資、10/29 整日補入


class TestTdcc(unittest.TestCase):
    def test_tdcc_level15_only(self):
        csv_text = ("資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n"
                    "20260703,2345,14,100,50000,10.00\n"
                    "20260703,2345,15,20,300000,57.02\n"
                    "20260703,2345,17,5000,600000,100.00\n")
        fc._http = lambda url, timeout=60: ("﻿" + csv_text).encode("utf-8")
        dd, m = fc.fetch_tdcc_large_holder()
        self.assertEqual(dd, "2026-07-03")
        self.assertEqual(m["2345"], 57.02)     # 只取分級 15,非 14/17


if __name__ == "__main__":
    unittest.main()
