"""
test_fetch_etf_holdings.py — 2026-10-31:ETF PCF 自動補抓漏掉的交易日 + 交易日告警。

交易日曆取 kline.db chips 表(來源自報日),不自己推算國定假日/颱風假;
日曆不可用時退回只抓今天 / 平日計數。
"""
from __future__ import annotations
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import fetch_etf_holdings as feh


def _chips_db(path, dates):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE chips (symbol TEXT, date TEXT, foreign_net INTEGER, "
                 "trust_net INTEGER, dealer_net INTEGER, margin_balance INTEGER)")
    conn.executemany("INSERT INTO chips VALUES ('TWSE:2330', ?, 1, 1, 1, 1)", [(d,) for d in dates])
    conn.commit()
    conn.close()


class TestTradingDays(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kline = os.path.join(self.tmp.name, "k.db")
        self.p = mock.patch.object(feh, "KLINE_DB", self.kline)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_weekend_and_holiday_not_counted(self):
        # 10/23(五)休市、10/24-25 週末、10/26(一)休市 → 10/22 之後到 10/27 只有 1 個交易日
        _chips_db(self.kline, ["2026-10-22", "2026-10-27"])
        self.assertEqual(feh._trading_days_since("2026-10-22", "2026-10-27"), 1)
        self.assertEqual(feh._tw_trading_days("2026-10-22", "2026-10-27"), ["2026-10-27"])

    def test_stale_calendar_falls_back_to_weekdays(self):
        # chips 本身停在 10/16(停更)→ 不可當日曆,退回平日計數:10/16(五)→10/31(六)= 10 個平日
        _chips_db(self.kline, ["2026-10-16"])
        self.assertIsNone(feh._tw_trading_days("2026-10-16", "2026-10-31"))
        self.assertEqual(feh._trading_days_since("2026-10-16", "2026-10-31"), 10)

    def test_no_history_is_stale(self):
        self.assertEqual(feh._trading_days_since(None, "2026-10-31"), 999)


class TestMainCatchUp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kline = os.path.join(self.tmp.name, "k.db")
        self.etf = os.path.join(self.tmp.name, "e.db")
        self.calls = []

        def fake(etf, d):
            self.calls.append((etf, d))
            return [{"code": "2330", "name": "台積電", "shares": 1, "weight": 1.0}], {}

        self.patches = [mock.patch.object(feh, "KLINE_DB", self.kline),
                        mock.patch.object(feh, "DB", self.etf),
                        mock.patch.object(feh, "SOURCES", {"00987A": (fake, "t")}),
                        mock.patch.object(feh, "_discord", lambda m: self.calls.append(("ALERT", m))),
                        mock.patch.object(sys, "argv", ["fetch_etf_holdings"])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _seed_etf(self, d):
        conn = sqlite3.connect(self.etf)
        feh._ensure_db(conn)
        conn.execute("INSERT INTO etf_holdings VALUES (?, '00987A', '2330', 'x', 1, 1, 0, 0, 't', 'n')", (d,))
        conn.commit()
        conn.close()

    def _run_on(self, today):
        class _DT(feh.datetime):
            @classmethod
            def now(cls, tz=None):
                return feh.datetime.strptime(today + " 19:05", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        with mock.patch.object(feh, "datetime", _DT):
            feh.main()

    def test_fetches_all_missed_trading_days(self):
        _chips_db(self.kline, ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-30"])
        self._seed_etf("2026-10-16")
        self._run_on("2026-10-30")
        self.assertEqual([d for e, d in self.calls if e == "00987A"],
                         ["2026-10-19", "2026-10-20", "2026-10-30"])
        self.assertFalse([c for c in self.calls if c[0] == "ALERT"])

    def test_weekend_run_fetches_nothing_and_no_alert(self):
        # 週六:最新 10/30(五)已抓 → 無交易日可抓,也不誤報「N 天無資料」
        _chips_db(self.kline, ["2026-10-29", "2026-10-30"])
        self._seed_etf("2026-10-30")
        self._run_on("2026-10-31")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
