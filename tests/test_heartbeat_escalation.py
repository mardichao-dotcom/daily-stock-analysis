"""
test_heartbeat_escalation.py — 任務二(2026-07-04):外部心跳 + us_refresh 連續失敗升級

- heartbeat.load_ping_url:修正重複前綴(paste error)
- _check_us_refresh_escalation:連續 ≥3 天失敗才告警、不重複計同一 run_date、ok 歸零、
  週末(同 run_date)不灌水
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import heartbeat, daily_supervisor as ds


class TestHeartbeatUrl(unittest.TestCase):
    def _with_secrets(self, url):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "secrets.json")
        json.dump({"healthchecks_ping_url": url}, open(p, "w"))
        return p

    def test_normalizes_doubled_prefix(self):
        orig = heartbeat.SECRETS
        heartbeat.SECRETS = self._with_secrets(
            "https://hc-ping.com/https://hc-ping.com/abc-123")
        try:
            self.assertEqual(heartbeat.load_ping_url(),
                             "https://hc-ping.com/abc-123")
        finally:
            heartbeat.SECRETS = orig

    def test_clean_url_unchanged(self):
        orig = heartbeat.SECRETS
        heartbeat.SECRETS = self._with_secrets("https://hc-ping.com/abc-123")
        try:
            self.assertEqual(heartbeat.load_ping_url(),
                             "https://hc-ping.com/abc-123")
        finally:
            heartbeat.SECRETS = orig

    def test_missing_returns_empty(self):
        orig = heartbeat.SECRETS
        heartbeat.SECRETS = "/tmp/does-not-exist-xyz.json"
        try:
            self.assertEqual(heartbeat.load_ping_url(), "")
            self.assertFalse(heartbeat.ping(body="x"))   # 不 raise
        finally:
            heartbeat.SECRETS = orig


class TestUsRefreshEscalation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = ds.US_STREAK_FILE
        ds.US_STREAK_FILE = os.path.join(self.tmp, "streak.json")

    def tearDown(self):
        ds.US_STREAK_FILE = self._orig

    def _status(self, run_date, overall):
        return {"us_refresh": {"run_date": run_date, "overall": overall}}

    def test_escalates_after_3_consecutive_fails(self):
        self.assertIsNone(ds._check_us_refresh_escalation(self._status("2026-07-01", "fail")))
        self.assertIsNone(ds._check_us_refresh_escalation(self._status("2026-07-02", "fail")))
        msg = ds._check_us_refresh_escalation(self._status("2026-07-03", "fail"))
        self.assertIsNotNone(msg)
        self.assertIn("連續 3 天失敗", msg)
        self.assertIn("🚨", msg)

    def test_same_run_date_not_double_counted(self):
        ds._check_us_refresh_escalation(self._status("2026-07-01", "fail"))
        # 同一天 supervisor 重跑 → 不應加計
        ds._check_us_refresh_escalation(self._status("2026-07-01", "fail"))
        ds._check_us_refresh_escalation(self._status("2026-07-01", "fail"))
        st = json.load(open(ds.US_STREAK_FILE))
        self.assertEqual(st["consecutive_fails"], 1)

    def test_ok_resets_streak(self):
        ds._check_us_refresh_escalation(self._status("2026-07-01", "fail"))
        ds._check_us_refresh_escalation(self._status("2026-07-02", "fail"))
        ds._check_us_refresh_escalation(self._status("2026-07-03", "ok"))   # 恢復
        st = json.load(open(ds.US_STREAK_FILE))
        self.assertEqual(st["consecutive_fails"], 0)
        # 之後再一次失敗不應立刻升級
        self.assertIsNone(ds._check_us_refresh_escalation(self._status("2026-07-06", "fail")))

    def test_weekend_same_rundate_no_inflation(self):
        # 週五失敗;週六/日 supervisor 看到同一 run_date(us_refresh 週末不跑)→ 不灌水
        ds._check_us_refresh_escalation(self._status("2026-07-03", "fail"))  # Fri
        ds._check_us_refresh_escalation(self._status("2026-07-03", "fail"))  # Sat(同 run_date)
        ds._check_us_refresh_escalation(self._status("2026-07-03", "fail"))  # Sun
        st = json.load(open(ds.US_STREAK_FILE))
        self.assertEqual(st["consecutive_fails"], 1)

    def test_re_escalates_daily_while_broken(self):
        for d in ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"):
            msg = ds._check_us_refresh_escalation(self._status(d, "fail"))
        # 第 4 天仍在失敗 → 仍發告警(醒目、不靜默)
        self.assertIsNotNone(msg)
        self.assertIn("連續 4 天", msg)


if __name__ == "__main__":
    unittest.main()
