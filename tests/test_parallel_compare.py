"""
test_parallel_compare.py — stage9 §4 Mac vs Actions 並行比對邏輯
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import parallel_compare as pc


class TestParallelCompare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        self.tmp.close()
        self._orig = pc.LEDGER
        pc.LEDGER = self.tmp.name

    def tearDown(self):
        pc.LEDGER = self._orig
        os.unlink(self.tmp.name)

    def test_diff_under_threshold_no_flag(self):
        pc.record_mac(date="2026-07-06", path=self._macro({"taiex": 46780.62, "margin": 12089437}))
        pc.record_actions({"taiex": 46781.0, "margin": 12089500}, date="2026-07-06")
        d = pc.compare_day("2026-07-06")
        self.assertEqual(d["flags"], [])
        self.assertFalse(d["fields"]["taiex"]["over"])

    def test_diff_over_threshold_flagged(self):
        pc.record_mac(date="2026-07-06", path=self._macro({"nasdaq": 25832.67}))
        pc.record_actions({"nasdaq": 26050.0}, date="2026-07-06")   # -0.83%
        d = pc.compare_day("2026-07-06")
        self.assertIn("nasdaq", d["flags"])
        self.assertTrue(d["fields"]["nasdaq"]["over"])

    def test_missing_side_no_pairing(self):
        pc.record_mac(date="2026-07-06", path=self._macro({"taiex": 46780.62}))
        d = pc.compare_day("2026-07-06")
        self.assertTrue(d["has_mac"])
        self.assertFalse(d["has_actions"])
        self.assertEqual(d["fields"], {})

    def test_summary_ready_when_5_clean_days(self):
        for i in range(5):
            date = f"2026-07-0{i+1}"
            pc.record_mac(date=date, path=self._macro({"taiex": 100.0, "margin": 200.0}))
            pc.record_actions({"taiex": 100.1, "margin": 200.1}, date=date)  # <0.5%
        s = pc.build_summary(need_days=5)
        self.assertEqual(s["paired_days"], 5)
        self.assertEqual(s["total_over_threshold"], 0)
        self.assertTrue(s["recommend_disable_actions"])

    def test_summary_not_ready_with_flag(self):
        pc.record_mac(date="2026-07-06", path=self._macro({"taiex": 100.0}))
        pc.record_actions({"taiex": 105.0}, date="2026-07-06")   # +5% over
        s = pc.build_summary(need_days=5)
        self.assertFalse(s["recommend_disable_actions"])
        self.assertGreaterEqual(s["total_over_threshold"], 1)

    def test_latest_actions_record_wins(self):
        pc.record_mac(date="2026-07-06", path=self._macro({"taiex": 100.0}))
        pc.record_actions({"taiex": 999.0}, date="2026-07-06")   # 早先誤記
        pc.record_actions({"taiex": 100.0}, date="2026-07-06")   # 更正
        d = pc.compare_day("2026-07-06")
        self.assertEqual(d["fields"]["taiex"]["actions"], 100.0)

    # helper:寫一個臨時 macro.json 供 record_mac 讀
    def _macro(self, values: dict) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"data": {k: {"value": v} for k, v in values.items()}}, f, ensure_ascii=False)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name


if __name__ == "__main__":
    unittest.main()
