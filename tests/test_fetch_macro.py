"""
test_fetch_macro.py — stage9 Day2 §3.2 總經數據 N/A 護欄

鎖住:數據源失敗 → 該項 value="N/A" + error,不用舊值冒充,errors 收集哪項失敗。
"""
from __future__ import annotations
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import fetch_macro as fm


class TestItem(unittest.TestCase):
    def test_ok_item(self):
        it = fm._item("加權指數", 46780.62, 36.46, 0.08, "2026-07-03", "TWSE")
        self.assertEqual(it["value"], 46780.62)
        self.assertEqual(it["change_pct"], 0.08)
        self.assertNotIn("error", it)

    def test_error_item_is_na(self):
        it = fm._item("VIX", error="timeout", source="yfinance")
        self.assertEqual(it["value"], "N/A")
        self.assertEqual(it["error"], "timeout")
        # N/A 不帶 change(不冒充)
        self.assertNotIn("change", it)

    def test_none_value_is_na(self):
        it = fm._item("X", value=None, source="s")
        self.assertEqual(it["value"], "N/A")


class TestRunGuardrail(unittest.TestCase):
    """run():部分源失敗 → 該源 N/A + 進 errors,其他源照常;不用舊值。"""

    def setUp(self):
        self._orig = {
            "taiex": fm.fetch_taiex, "margin": fm.fetch_margin,
            "index": fm.fetch_index,
        }

    def tearDown(self):
        fm.fetch_taiex, fm.fetch_margin, fm.fetch_index = (
            self._orig["taiex"], self._orig["margin"], self._orig["index"])

    def test_partial_failure_marks_na_not_stale(self):
        fm.fetch_taiex = lambda: fm._item("加權指數", 46780.62, 1.0, 0.01, "d", "TWSE")
        fm.fetch_margin = lambda: fm._item("融資餘額", error="TPEx 503", source="s")
        def fake_index(key):
            if key == "vix":
                return fm._item("VIX", error="yfinance timeout", source="yf")
            return fm._item(fm.YF_ITEMS[key][0], 100.0, 1.0, 1.0, source="yf")
        fm.fetch_index = fake_index

        out = fm.run()
        self.assertEqual(out["data"]["taiex"]["value"], 46780.62)   # 好的照常
        self.assertEqual(out["data"]["margin"]["value"], "N/A")     # 失敗標 N/A
        self.assertEqual(out["data"]["vix"]["value"], "N/A")
        self.assertEqual(out["sources_failed"], 2)
        # errors 明確列出哪項失敗(供 Discord 回報)
        joined = " ".join(out["errors"])
        self.assertIn("margin", joined)
        self.assertIn("vix", joined)
        self.assertIn("TPEx 503", joined)


if __name__ == "__main__":
    unittest.main()
