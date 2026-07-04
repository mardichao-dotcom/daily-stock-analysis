"""
test_discord_send.py — 2026-07-04 停更 19 天事故防回歸

根因之一:97 檔略過明細把 Discord 訊息撐爆 2000 上限 → 送失敗只印 stderr(不可見)
→ 用戶 19 天沒被告警。修法:skip 明細只列前 5 + 共 N、_send 超限截斷 + 重試 + 記 log。
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
import urllib.error

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import daily_supervisor as ds


class TestSkippedDetail(unittest.TestCase):
    def test_97_skipped_is_short(self):
        d = {"metadata": {"skipped_symbols": [f"TWSE:{i:04d}" for i in range(97)]}}
        tmp = tempfile.mkdtemp()
        json.dump(d, open(os.path.join(tmp, "filtered_result_v2.json"), "w"))
        orig = ds.PROJECT_ROOT
        ds.PROJECT_ROOT = tmp
        try:
            lines = ds._skipped_detail()
        finally:
            ds.PROJECT_ROOT = orig
        self.assertEqual(len(lines), 1)
        self.assertLess(len(lines[0]), 120)          # 舊版是 ~1090
        self.assertIn("共 97 檔", lines[0])
        self.assertIn("TWSE:0000", lines[0])         # 仍列前幾檔


class TestSendGuards(unittest.TestCase):
    def _capture_send(self, content, statuses):
        """以 statuses 序列模擬每次 urlopen 結果;回 (ok, sent_len, attempts)。"""
        import urllib.request
        state = {"i": 0, "len": None, "attempts": 0}

        class FakeResp:
            def __init__(self, st): self.status = st
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_open(req, timeout=10):
            state["attempts"] += 1
            state["len"] = len(json.loads(req.data)["content"])
            st = statuses[min(state["i"], len(statuses) - 1)]
            state["i"] += 1
            if isinstance(st, Exception):
                raise st
            return FakeResp(st)

        orig = urllib.request.urlopen
        urllib.request.urlopen = fake_open
        try:
            ok = ds._send("https://discord.test/webhook", content)
        finally:
            urllib.request.urlopen = orig
        return ok, state["len"], state["attempts"]

    def test_truncates_over_2000(self):
        ok, sent_len, _ = self._capture_send("x" * 5000, [204])
        self.assertTrue(ok)
        self.assertLessEqual(sent_len, ds.DISCORD_MAX_LEN)

    def test_retries_then_succeeds(self):
        # 前兩次 URLError,第三次 204 → 應成功且試了 3 次
        err = urllib.error.URLError("boom")
        ok, _, attempts = self._capture_send("hi", [err, err, 204])
        self.assertTrue(ok)
        self.assertEqual(attempts, 3)

    def test_final_failure_returns_false(self):
        err = urllib.error.URLError("down")
        ok, _, attempts = self._capture_send("hi", [err, err, err])
        self.assertFalse(ok)                          # 最終失敗要能被 caller 看見
        self.assertEqual(attempts, 3)


if __name__ == "__main__":
    unittest.main()
