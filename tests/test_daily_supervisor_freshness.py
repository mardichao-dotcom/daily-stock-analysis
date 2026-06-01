"""
test_daily_supervisor_freshness.py — Stage 8 第三階段補:資料新鮮度 watchdog

驗證 _check_data_freshness() 在不同情境下會 / 不會出告警。
5/21~5/31 那種「step ok 但資料 11 天沒前進」的靜默不能再發生。
"""
from __future__ import annotations
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import daily_supervisor as ds


def _make_kline_db(path: str, max_date: str | None) -> None:
    """建一個 mini kline.db,只放一筆指定 max_date 的 row。
    max_date=None → 建空表(沒任何資料)。"""
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE kline (
        symbol TEXT, date TEXT, open REAL, high REAL,
        low REAL, close REAL, volume REAL,
        PRIMARY KEY(symbol, date)
    )""")
    if max_date:
        conn.execute(
            "INSERT INTO kline VALUES (?,?,?,?,?,?,?)",
            ("TWSE:2330", max_date, 1, 2, 3, 4, 5),
        )
    conn.commit()
    conn.close()


def _make_etf_db(path: str, max_date: str | None) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE operations (
        etf TEXT, 日期 TEXT, 代號 TEXT, 名稱 TEXT,
        動作 TEXT, 張數 INTEGER, 權重 REAL DEFAULT 0
    )""")
    if max_date:
        conn.execute(
            "INSERT INTO operations VALUES (?,?,?,?,?,?,?)",
            ("00981A", max_date, "2330", "台積電", "加碼", 100, 0.05),
        )
    conn.commit()
    conn.close()


def _today() -> str:
    return datetime.now(ds.TZ_TAIPEI).date().strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now(ds.TZ_TAIPEI).date() - timedelta(days=n)).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────────────
class TestFreshnessWatchdog(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.kline = os.path.join(self.tmpdir, "kline.db")
        self.etf   = os.path.join(self.tmpdir, "etf.db")

    def tearDown(self):
        for p in (self.kline, self.etf):
            if os.path.exists(p):
                os.unlink(p)
        os.rmdir(self.tmpdir)

    def _patch_paths(self):
        return patch.multiple(ds, KLINE_DB=self.kline, ETF_DB=self.etf)

    # ── 兩 DB 都新鮮(今天)→ 無告警 ──
    def test_both_fresh_today_no_warnings(self):
        _make_kline_db(self.kline, _today())
        _make_etf_db(self.etf, _today())
        with self._patch_paths():
            self.assertEqual(ds._check_data_freshness(), [])

    # ── 兩 DB 都晚 1 天(< 3 天門檻)→ 無告警 ──
    def test_one_day_old_no_warnings(self):
        _make_kline_db(self.kline, _days_ago(1))
        _make_etf_db(self.etf, _days_ago(1))
        with self._patch_paths():
            self.assertEqual(ds._check_data_freshness(), [])

    # ── kline.db 晚 3 天(剛好門檻)→ 出告警 ──
    def test_kline_at_threshold_warns(self):
        _make_kline_db(self.kline, _days_ago(3))
        _make_etf_db(self.etf, _today())
        with self._patch_paths():
            w = ds._check_data_freshness()
            self.assertEqual(len(w), 1)
            self.assertIn("kline.db", w[0])
            self.assertIn("3 天", w[0])

    # ── 模擬 5/21~5/31 那個情境:kline.db 11 天沒新資料 ──
    def test_kline_eleven_days_stale_warns(self):
        _make_kline_db(self.kline, _days_ago(11))
        _make_etf_db(self.etf, _today())
        with self._patch_paths():
            w = ds._check_data_freshness()
            self.assertEqual(len(w), 1)
            self.assertIn("11 天", w[0])

    # ── 兩 DB 都過期 → 兩條告警 ──
    def test_both_stale_two_warnings(self):
        _make_kline_db(self.kline, _days_ago(5))
        _make_etf_db(self.etf, _days_ago(7))
        with self._patch_paths():
            w = ds._check_data_freshness()
            self.assertEqual(len(w), 2)
            kline_w = next(x for x in w if "kline.db" in x)
            etf_w   = next(x for x in w if "etf_operations.db" in x)
            self.assertIn("5 天", kline_w)
            self.assertIn("7 天", etf_w)

    # ── DB 不存在不會炸 ──
    def test_missing_db_no_crash(self):
        # 不建任何 db
        with self._patch_paths():
            self.assertEqual(ds._check_data_freshness(), [])

    # ── DB 存在但空表 → 不告警(MAX 是 None) ──
    def test_empty_db_no_warning(self):
        _make_kline_db(self.kline, None)
        _make_etf_db(self.etf, None)
        with self._patch_paths():
            self.assertEqual(ds._check_data_freshness(), [])


# ─────────────────────────────────────────────────────────────────────────────
class TestMessageIncludesWarning(unittest.TestCase):
    """確認告警會被嵌進 Discord 訊息頂部"""

    def test_warning_section_prepended(self):
        with patch.object(ds, "_check_data_freshness",
                            return_value=["🚨 kline.db 11 天沒新資料(最新 2026-05-20 / 今天 2026-06-01)"]):
            msg = ds._build_message({"stock_dashboard": {"overall": "ok", "steps": []}})
            self.assertIn("🚨 資料新鮮度告警", msg)
            self.assertIn("11 天沒新資料", msg)
            # 告警應該在 header 之前(或頂部附近)
            warn_idx = msg.index("🚨 資料新鮮度告警")
            header_idx = msg.index("每日自動化回報")
            self.assertLess(warn_idx, header_idx)

    def test_no_warning_no_section(self):
        with patch.object(ds, "_check_data_freshness", return_value=[]):
            msg = ds._build_message({"stock_dashboard": {"overall": "ok", "steps": []}})
            self.assertNotIn("資料新鮮度告警", msg)


if __name__ == "__main__":
    unittest.main()
