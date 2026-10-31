"""
test_daily_supervisor_uncommitted.py — 2026-10-31:程式/設定未提交 ≥3 天 → 每日回報一行提醒。

publish.sh 改為只提交 docs/ 後,程式/設定改手動提交;這行防「改了忘了交」,平常不出現。
"""
from __future__ import annotations
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import daily_supervisor as ds

DAY = 86400


class TestUncommittedReminder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.p = patch.object(ds, "PROJECT_ROOT", self.tmp.name)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def _file(self, rel, age_days):
        full = os.path.join(self.tmp.name, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").close()
        t = self.now - age_days * DAY
        os.utime(full, (t, t))

    def _status(self, text):
        return lambda *a, **k: types.SimpleNamespace(stdout=text)

    def test_quiet_when_nothing_or_all_recent(self):
        self.assertIsNone(ds._uncommitted_reminder(self.now, self._status("")))
        self._file("src/a.py", 1)
        self.assertIsNone(ds._uncommitted_reminder(self.now, self._status(" M src/a.py\n")))

    def test_lists_stale_files_with_days_oldest_first(self):
        self._file("src/a.py", 5)
        self._file("tests/test_x.py", 3)
        self._file("config/w.json", 1)
        line = ds._uncommitted_reminder(
            self.now, self._status(" M src/a.py\n?? tests/test_x.py\n M config/w.json\n"))
        self.assertEqual(line, "📝 未提交變更超過 3 天:src/a.py(5 天)、tests/test_x.py(3 天)")

    def test_skips_bak_and_deleted(self):
        self._file("config/key_prices.json.bak-20261031", 9)
        line = ds._uncommitted_reminder(
            self.now, self._status("?? config/key_prices.json.bak-20261031\n D src/gone.py\n"))
        self.assertIsNone(line)

    def test_git_unavailable_is_silent(self):
        def boom(*a, **k):
            raise OSError("no git")
        self.assertIsNone(ds._uncommitted_reminder(self.now, boom))


if __name__ == "__main__":
    unittest.main()
