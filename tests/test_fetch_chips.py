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
        self.assertEqual(fc._iso_roc("1150703"), "2026-07-03")     # 民國115=2026


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


class TestOtcTpex(unittest.TestCase):
    """上櫃:英文 key(亂空格)、無逗號、民國日期。"""
    def _patch(self, arr):
        fc._http = lambda url, timeout=40: json.dumps(arr).encode("utf-8")

    def test_parse_messy_keys(self):
        self._patch([{
            "Date": "1150703", "SecuritiesCompanyCode": "3081", "CompanyName": "聯亞",
            "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "98755",
            "SecuritiesInvestmentTrustCompanies-Difference": "-129477",
            "Dealers-Difference": "-19077"}])
        dd, m = fc.fetch_tpex_3insti()
        self.assertEqual(dd, "2026-07-03")
        self.assertEqual(m["3081"], {"foreign": 98755, "trust": -129477, "dealer": -19077})

    def test_empty_raises(self):
        self._patch([])
        with self.assertRaises(RuntimeError):
            fc.fetch_tpex_3insti()


class TestMarginAndTdcc(unittest.TestCase):
    def test_twse_margin(self):
        fc._http = lambda url, timeout=40: json.dumps(
            [{"股票代號": "2345", "融資今日餘額": "1,878"}]).encode("utf-8")
        self.assertEqual(fc.fetch_twse_margin()["2345"], 1878)

    def test_tpex_margin(self):
        fc._http = lambda url, timeout=40: json.dumps(
            [{"SecuritiesCompanyCode": "3081", "MarginPurchaseBalance": "4711"}]).encode("utf-8")
        self.assertEqual(fc.fetch_tpex_margin()["3081"], 4711)

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
