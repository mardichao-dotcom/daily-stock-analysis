"""
test_import_kline.py — P0-C 防回歸:import 必須 UPSERT 覆寫(收盤後重抓覆蓋半成品)

舊行為 INSERT OR IGNORE:已存在的 (symbol,date) 被忽略 → 盤中半成品永久殘留。
新行為 INSERT OR REPLACE:重抓的正確收盤值覆寫之。
"""
from __future__ import annotations
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ts(date_str: str) -> int:
    """YYYY-MM-DD → UTC unix timestamp(import 用 utcfromtimestamp 還原日期)。"""
    from datetime import datetime, timezone
    y, m, d = map(int, date_str.split("-"))
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


class TestImportUpsert(unittest.TestCase):

    def _run_import(self, db, bars, extra=()):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"results": {"NASDAQ:NVDA": {"bars": bars}}}, f)
            jpath = f.name
        try:
            subprocess.run(
                [sys.executable, "src/import_kline.py", "--json", jpath,
                 "--db", db, *extra],
                cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
        finally:
            os.unlink(jpath)

    def test_replace_overwrites_halfbaked_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            t = _ts("2026-06-11")
            # 第一次:盤中半成品 close=100
            self._run_import(db, [{"time": t, "open": 90, "high": 110,
                                   "low": 80, "close": 100, "volume": 1}],
                             extra=["--no-data-date"])
            # 第二次:收盤後重抓 close=128(正確值)
            self._run_import(db, [{"time": t, "open": 90, "high": 130,
                                   "low": 80, "close": 128, "volume": 5}],
                             extra=["--no-data-date"])
            conn = sqlite3.connect(db)
            rows = conn.execute(
                "SELECT close, volume FROM kline WHERE symbol='NASDAQ:NVDA' "
                "AND date='2026-06-11'").fetchall()
            conn.close()
            self.assertEqual(len(rows), 1)              # 沒有重複列
            self.assertEqual(rows[0], (128.0, 5.0))      # 被覆寫成收盤正確值

    def test_no_data_date_does_not_write_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            date_file = os.path.join(PROJECT_ROOT, ".data_date")
            before = open(date_file).read() if os.path.exists(date_file) else None
            self._run_import(db, [{"time": _ts("2020-01-02"), "open": 1, "high": 1,
                                   "low": 1, "close": 1, "volume": 1}],
                             extra=["--no-data-date"])
            after = open(date_file).read() if os.path.exists(date_file) else None
            # --no-data-date 不得改動專案的 .data_date
            self.assertEqual(before, after)


class TestFutureBarGuard(unittest.TestCase):
    """2026-07-04 停更 19 天事故:週末跑批抓到未來日期 bar(TWSE:2317@6/15)→
    被當 MAX 推進 data_date → 隔天 97/98 檔查無當日 K 線全略過 → 骨牌。
    import 必須拒絕 date > today(台北)的幽靈 bar。"""

    def _run(self, db, bars):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"results": {"TWSE:2317": {"bars": bars}}}, f)
            jpath = f.name
        try:
            r = subprocess.run(
                [sys.executable, "src/import_kline.py", "--json", jpath,
                 "--db", db, "--no-data-date"],
                cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
            return r.stdout
        finally:
            os.unlink(jpath)

    def test_future_bar_rejected_not_inserted(self):
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone(timedelta(hours=8))).date()
        past   = (today - timedelta(days=1)).isoformat()
        future = (today + timedelta(days=3)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            out = self._run(db, [
                {"time": _ts(past),   "open": 1, "high": 1, "low": 1, "close": 10, "volume": 1},
                {"time": _ts(future), "open": 1, "high": 1, "low": 1, "close": 99, "volume": 1},
            ])
            conn = sqlite3.connect(db)
            dates = [r[0] for r in conn.execute(
                "SELECT date FROM kline WHERE symbol='TWSE:2317' ORDER BY date")]
            conn.close()
            self.assertIn(past, dates)              # 過去 bar 正常入庫
            self.assertNotIn(future, dates)         # 未來 bar 被擋
            self.assertIn("擋下", out)              # 有明確 log

    def test_future_bar_not_advancing_data_date(self):
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone(timedelta(hours=8))).date()
        past   = (today - timedelta(days=1)).isoformat()
        future = (today + timedelta(days=5)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            out = self._run(db, [
                {"time": _ts(past),   "open": 1, "high": 1, "low": 1, "close": 10, "volume": 1},
                {"time": _ts(future), "open": 1, "high": 1, "low": 1, "close": 99, "volume": 1},
            ])
            # data_date 應停在過去 bar,不被未來幽靈 bar 推進
            self.assertIn(f"data_date={past}", out)
            self.assertNotIn(f"data_date={future}", out)


class TestSanityGate(unittest.TestCase):
    """W1(審計 2026-07-07):數值 sanity 閘——結構違規/單日跳變 >30%/覆寫差異 >30%
    進 kline_quarantine 隔離區,不入 kline、不覆寫既有正確歷史;可 --approve 核可覆寫。"""

    def _run(self, db, bars, symbol="TWSE:2330", extra=()):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"results": {symbol: {"bars": bars}}}, f)
            jpath = f.name
        env = {**os.environ, "IMPORT_KLINE_NO_ALERT": "1"}
        try:
            r = subprocess.run(
                [sys.executable, "src/import_kline.py", "--json", jpath,
                 "--db", db, "--no-data-date", *extra],
                cwd=PROJECT_ROOT, check=True, capture_output=True, text=True, env=env)
            return r.stdout
        finally:
            os.unlink(jpath)

    def _counts(self, db):
        conn = sqlite3.connect(db)
        k = conn.execute("SELECT COUNT(*) FROM kline").fetchone()[0]
        q = conn.execute("SELECT COUNT(*) FROM kline_quarantine").fetchone()[0]
        conn.close()
        return k, q

    def test_high_lt_low_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            out = self._run(db, [{"time": _ts("2026-06-10"), "open": 100, "high": 90,
                                  "low": 95, "close": 92, "volume": 1}])
            self.assertIn("隔離", out)
            self.assertEqual(self._counts(db), (0, 1))

    def test_negative_price_and_volume_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            self._run(db, [
                {"time": _ts("2026-06-10"), "open": 10, "high": 12, "low": -1, "close": 11, "volume": 1},
                {"time": _ts("2026-06-11"), "open": 10, "high": 12, "low": 9, "close": 11, "volume": -5},
            ])
            self.assertEqual(self._counts(db), (0, 2))

    def test_jump_over_30pct_vs_db_prev_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            self._run(db, [{"time": _ts("2026-06-10"), "open": 100, "high": 101,
                            "low": 99, "close": 100, "volume": 1}])
            # 隔日 close 140 = +40% 跳變 → 隔離
            out = self._run(db, [{"time": _ts("2026-06-11"), "open": 100, "high": 141,
                                  "low": 99, "close": 140, "volume": 1}])
            self.assertIn("跳變", out)
            k, q = self._counts(db)
            self.assertEqual((k, q), (1, 1))            # 既有 row 完好,新 bar 進隔離

    def test_overwrite_existing_over_30pct_quarantined(self):
        """整批平移(還原權息)序列不得靜默覆寫既有正確歷史。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            self._run(db, [{"time": _ts("2026-06-10"), "open": 100, "high": 101,
                            "low": 99, "close": 100, "volume": 1}])
            # same-date 覆寫成 50(-50%)→ 隔離,原值保留
            self._run(db, [{"time": _ts("2026-06-10"), "open": 50, "high": 51,
                            "low": 49, "close": 50, "volume": 1}])
            conn = sqlite3.connect(db)
            close = conn.execute("SELECT close FROM kline WHERE date='2026-06-10'").fetchone()[0]
            conn.close()
            self.assertEqual(close, 100.0)              # 原正確值未被覆寫
            self.assertEqual(self._counts(db), (1, 1))

    def test_normal_move_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            self._run(db, [
                {"time": _ts("2026-06-10"), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
                {"time": _ts("2026-06-11"), "open": 100, "high": 110, "low": 99, "close": 109, "volume": 1},
            ])
            self.assertEqual(self._counts(db), (2, 0))  # +9% 正常,全過

    def test_approve_moves_to_kline(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            self._run(db, [{"time": _ts("2026-06-10"), "open": 100, "high": 101,
                            "low": 99, "close": 100, "volume": 1}])
            self._run(db, [{"time": _ts("2026-06-11"), "open": 100, "high": 141,
                            "low": 99, "close": 140, "volume": 2}])   # 隔離
            # 隔日核可(如確認為合法除權息跳變)
            env = {**os.environ, "IMPORT_KLINE_NO_ALERT": "1"}
            subprocess.run([sys.executable, "src/import_kline.py", "--db", db,
                            "--approve", "TWSE:2330:2026-06-11"],
                           cwd=PROJECT_ROOT, check=True, capture_output=True, text=True, env=env)
            conn = sqlite3.connect(db)
            close = conn.execute("SELECT close FROM kline WHERE date='2026-06-11'").fetchone()[0]
            conn.close()
            self.assertEqual(close, 140.0)
            self.assertEqual(self._counts(db), (2, 0))  # 隔離區清空

    def test_quarantine_not_advancing_data_date(self):
        """整批被隔離時 data_date 退回 DB 既有最大日,不寫空/不推進。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            self._run(db, [{"time": _ts("2026-06-10"), "open": 100, "high": 101,
                            "low": 99, "close": 100, "volume": 1}])
            out = self._run(db, [{"time": _ts("2026-06-11"), "open": 100, "high": 150,
                                  "low": 99, "close": 150, "volume": 1}])   # 全隔離
            self.assertIn("data_date=2026-06-10", out)


if __name__ == "__main__":
    unittest.main()
