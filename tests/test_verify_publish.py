"""
test_verify_publish.py — P1 §6.4 發布後線上驗證的斷言邏輯(注入 mock fetch)
"""
from __future__ import annotations
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import verify_publish as vp

BASE = "https://example.test"
DATE = "2026-06-11"

SITE_META = {"data_date": DATE, "rule_version": "v2.2", "tw_count": 98,
             "intl_count": 33, "total_count": 131, "skipped": []}

GOOD_PAGE = '<div class="meta">資料日期 2026-06-11 ｜ 規則 v2.2 ｜ 台股 98 檔 ｜ 國際 33 檔</div>'
GOOD_HISTORY = '<span class="history-summary">S 3 / A 2 / B 2</span>'
US_JSON = json.dumps({"key_prices": {"lines": [1, 2, 3, 4, 5, 6, 7, 8, 9]}})
OK_JSON = json.dumps({"key_prices": {"lines": []}})


def _fresh_events_json():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return json.dumps({
        "generated_at": now, "conference_stale": False,
        "conference_source_date": DATE,
        "events": [
            {"date": "2026-07-16", "type": "conference", "symbol": "TWSE:2330",
             "name": "台積電", "title": "法說會", "importance": ""},
            {"date": "2026-07-14", "type": "macro", "name": "CPI",
             "title": "CPI", "importance": "high"},
        ],
    })


def make_fetch(overrides=None):
    """回一個 fetch(url)->(code, body);overrides 可覆寫特定 URL 的回應。"""
    overrides = overrides or {}

    def fetch(url, timeout=15):
        if url in overrides:
            return overrides[url]
        if url.endswith("site_meta.json"):
            return 200, json.dumps(SITE_META)
        if url.endswith("events.json"):
            return 200, _fresh_events_json()
        if url.endswith("history.html"):
            return 200, GOOD_HISTORY
        if url.endswith("NYSE_TSM.json"):
            return 200, US_JSON
        if url.endswith(".json"):
            return 200, OK_JSON
        if url.endswith(".html"):
            return 200, GOOD_PAGE
        return 404, ""
    return fetch


class TestVerifyPublish(unittest.TestCase):

    def test_all_green(self):
        errors = vp.run_checks(BASE, DATE, fetch=make_fetch())
        self.assertEqual(errors, [], f"應全綠,卻有: {errors}")

    def test_page_404_caught(self):
        f = make_fetch({f"{BASE}/tags.html": (404, "")})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("tags.html HTTP 404" in e for e in errors))

    def test_stale_version_caught(self):
        bad = '<div class="meta">資料日期 X ｜ 版本 v2.1 ｜ 台股 98 檔</div>'
        f = make_fetch({f"{BASE}/index_v2.html": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("v2.1" in e for e in errors))

    def test_us_lines_zero_caught(self):
        f = make_fetch({f"{BASE}/data/v2/{DATE}/NYSE_TSM.json": (200, OK_JSON)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("key_prices.lines = 0" in e for e in errors))

    def test_history_no_summary_caught(self):
        f = make_fetch({f"{BASE}/history.html":
                        (200, '<span class="history-summary">(無摘要)</span>')})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("無摘要" in e for e in errors))

    def test_blank_etf_strong_caught(self):
        f = make_fetch({f"{BASE}/index_v2.html": (200, GOOD_PAGE + "<strong></strong>")})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("空白 <strong>" in e for e in errors))

    def test_tw_count_mismatch_caught(self):
        bad = '<div class="meta">規則 v2.2 ｜ 台股 77 檔 ｜ 國際 33 檔</div>'
        f = make_fetch({f"{BASE}/watchlist_v2.html": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("台股檔數" in e for e in errors))

    def test_events_404_caught(self):
        f = make_fetch({f"{BASE}/data/v2/events.json": (404, "")})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("events.json HTTP 404" in e for e in errors))

    def test_events_stale_generated_at_caught(self):
        stale = json.dumps({"generated_at": "2020-01-01T00:00:00+08:00",
                            "events": []})
        f = make_fetch({f"{BASE}/data/v2/events.json": (200, stale)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("generated_at 超過 24h" in e for e in errors))

    def test_events_conference_missing_field_caught(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        bad = json.dumps({"generated_at": now, "events": [
            {"date": "2026-07-16", "type": "conference", "name": "缺symbol"}]})
        f = make_fetch({f"{BASE}/data/v2/events.json": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("法說會條目缺欄位" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
