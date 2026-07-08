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

# 資產版本 mock(Batch1 cache-busting):V = 資產內容 md5 前 8 碼
import hashlib as _hashlib
MOCK_ASSETS = {"tokens.css": ":root{--x:1}", "style_v2.css": "body{}",
               "theme.js": "//t", "chart_v2.js": "//c", "events.js": "//e",
               "macro_dash.js": "//m"}
_h5 = _hashlib.md5()
for _n in ["tokens.css", "style_v2.css", "theme.js", "chart_v2.js", "events.js",
           "macro_dash.js"]:
    _h5.update(MOCK_ASSETS[_n].encode())
MOCK_V = _h5.hexdigest()[:8]
_VLINK = f'<link rel="stylesheet" href="assets/style_v2.css?v={MOCK_V}">'

GOOD_PAGE = (_VLINK +
             '<a href="macro_dashboard.html">🌐 宏觀</a>'
             '<div class="meta">資料日期 2026-06-11 ｜ 規則 v2.2 ｜ 台股 98 檔 ｜ 國際 33 檔</div>')
MACRO_DASH_PAGE = _VLINK + '<a href="macro_dashboard.html">x</a>' + (
    '<div class="md-chart" data-signal="x"></div>' * 10)


def _fresh_macro_signals():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return json.dumps({"generated_at": now, "signals": {
        k: {} for k in ("taiex", "spx", "vix", "umich", "cpi", "light",
                        "dgs10", "usdtwd", "fedwatch", "brent")}})
GOOD_HISTORY = _VLINK + '<span class="history-summary">S 3 / A 2 / B 2</span>'
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


def _fresh_weekly_json():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return json.dumps({
        "generated_at": now, "data_through": DATE, "errors": [], "alerts": [],
        "naaim": {"status": "ok", "latest_date": "2026-07-01", "latest_value": 84.69,
                  "count": 1043,
                  "series": {"dates": ["2025-07-02", "2025-12-31", "2026-07-01"],
                             "exposure": [99.3, 92.93, 84.69]}},   # 含 3 官方錨點
        "vix": {"value": 16.15}, "xly_xlp": {"ratio": 1.378, "cross": "none", "trend": "risk_off"},
        "margin": {"total": 12089437, "wow_pct": None},
        "taiex": {"close": 46780.62, "week_change_pct": 4.96},
    })


WEEKLY_HTML = '<h1>📅 每週市場情緒週報</h1>'

CHIPS_INDEX = json.dumps({"date": DATE, "symbols": {"TWSE_2345": {"status": "ready"}},
                          "stocks": ["TWSE_2345"]})


def _chips_chart_json(foreign=None):
    return json.dumps({"symbol": "TWSE:2345", "chips": {
        "dates": ["2026-07-01", "2026-07-02", "2026-07-03"],
        "foreign_net": foreign if foreign is not None else [10, -5, 3],
        "trust_net": [1, 2, -1], "margin": [100, 101, 102],
        "trust_markers": [{"time": "2026-07-01", "value": 1}],
        "large_holder": {"date": DATE, "ratio": 57.0}}})


def _fresh_news_json():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return json.dumps({"generated_at": now, "sources": ["中央社財經"], "count": 1,
                       "items": [{"title": "台積電 AI 需求強", "source": "中央社財經",
                                  "published_at": now, "fetched_at": now,
                                  "url": "https://ex.com/a", "matched_keywords": ["AI"]}]})


def _fresh_macro_json():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return json.dumps({"generated_at": now, "sources_ok": 7, "sources_failed": 0,
                       "errors": [],
                       "data": {"taiex": {"label": "加權指數", "value": 46780.62,
                                          "change_pct": 0.08}}})


def make_fetch(overrides=None):
    """回一個 fetch(url)->(code, body);overrides 可覆寫特定 URL 的回應。"""
    overrides = overrides or {}

    def fetch(url, timeout=15):
        if url in overrides:
            return overrides[url]
        if "/assets/" in url:
            name = url.split("/assets/")[1].split("?")[0]
            return (200, MOCK_ASSETS[name]) if name in MOCK_ASSETS else (404, "")
        if url.endswith("macro.json"):
            return 200, _fresh_macro_json()
        if url.endswith("macro_signals.json"):
            return 200, _fresh_macro_signals()
        if url.endswith("macro_dashboard.html"):
            return 200, MACRO_DASH_PAGE
        if url.endswith("news.json"):
            return 200, _fresh_news_json()
        if url.endswith("site_meta.json"):
            return 200, json.dumps(SITE_META)
        if url.endswith("events.json"):
            return 200, _fresh_events_json()
        if url.endswith("weekly.json"):
            return 200, _fresh_weekly_json()
        if url.endswith("_index.json"):
            return 200, CHIPS_INDEX
        if url.endswith("TWSE_2345.json"):
            return 200, _chips_chart_json()
        if url.endswith("history.html"):
            return 200, GOOD_HISTORY
        if url.endswith("weekly.html"):
            return 200, WEEKLY_HTML
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

    # ── 週報斷言(§3.3)──
    def test_weekly_404_caught(self):
        f = make_fetch({f"{BASE}/data/v2/weekly.json": (404, "")})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("weekly.json HTTP 404" in e for e in errors))

    def test_weekly_stale_over_8d_caught(self):
        stale = json.dumps({"generated_at": "2020-01-01T00:00:00+08:00",
                            "naaim": {"status": "N/A"}, "vix": {}, "xly_xlp": {},
                            "margin": {}, "taiex": {}})
        f = make_fetch({f"{BASE}/data/v2/weekly.json": (200, stale)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("weekly.json generated_at 超過 8 天" in e for e in errors))

    def test_weekly_naaim_anchor_mismatch_caught(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        bad = json.dumps({"generated_at": now, "naaim": {
            "status": "ok", "latest_value": 84.69, "count": 1043,
            "series": {"dates": ["2026-07-01"], "exposure": [50.0]}},  # 官方應為 84.69
            "vix": {}, "xly_xlp": {}, "margin": {}, "taiex": {}})
        f = make_fetch({f"{BASE}/data/v2/weekly.json": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("NAAIM 錨點 2026-07-01 不符官方" in e for e in errors))

    def test_weekly_naaim_count_not_full_caught(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        bad = json.dumps({"generated_at": now, "naaim": {
            "status": "ok", "latest_value": 84.69, "count": 12,  # 疑非全量(舊 seed)
            "series": {"dates": [], "exposure": []}},
            "vix": {}, "xly_xlp": {}, "margin": {}, "taiex": {}})
        f = make_fetch({f"{BASE}/data/v2/weekly.json": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("NAAIM count 異常" in e for e in errors))

    def test_weekly_html_missing_title_caught(self):
        f = make_fetch({f"{BASE}/weekly.html": (200, "<h1>錯的頁</h1>")})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("weekly.html 缺週報標題" in e for e in errors))

    # ── 事件擴充斷言(§3.5 新事件類型)──
    def test_events_unknown_type_caught(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        bad = json.dumps({"generated_at": now, "events": [
            {"date": "2026-07-15", "type": "bogus", "name": "怪"}]})
        f = make_fetch({f"{BASE}/data/v2/events.json": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("未知 type" in e for e in errors))

    def test_events_dividend_missing_symbol_caught(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        bad = json.dumps({"generated_at": now, "events": [
            {"date": "2026-07-15", "type": "dividend", "name": "缺symbol", "level": "medium"}]})
        f = make_fetch({f"{BASE}/data/v2/events.json": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("除權息條目缺欄位" in e for e in errors))

    def test_events_bad_level_caught(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        bad = json.dumps({"generated_at": now, "events": [
            {"date": "2026-07-15", "type": "settlement", "name": "結算", "level": "超高"}]})
        f = make_fetch({f"{BASE}/data/v2/events.json": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("非法 level" in e for e in errors))

    # ── 資產版本斷言(stage10 Batch1)──
    def test_assets_hash_mismatch_caught(self):
        # style_v2.css 內容被換(部署滯後)→ 內容 hash ≠ 頁面引用 v
        f = make_fetch({f"{BASE}/assets/style_v2.css?v={MOCK_V}": (200, "body{OLD}")})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("資產版本" in e and "≠" in e for e in errors))

    def test_assets_missing_version_ref_caught(self):
        # index_v2.html 沒帶 ?v=(回退到無版本化)
        nov = GOOD_PAGE.replace(f"?v={MOCK_V}", "")
        f = make_fetch({f"{BASE}/index_v2.html": (200, nov)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("無 ?v= 引用" in e for e in errors))

    def test_assets_inconsistent_versions_caught(self):
        torn = GOOD_PAGE.replace(MOCK_V, "deadbeef")
        f = make_fetch({f"{BASE}/tags.html": (200, torn)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("v 不一致" in e for e in errors))

    # ── macro.json 斷言(審計 D2)──
    def test_macro_404_caught(self):
        f = make_fetch({f"{BASE}/data/v2/macro.json": (404, "")})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("macro.json HTTP 404" in e for e in errors))

    def test_macro_stale_over_24h_caught(self):
        stale = json.dumps({"generated_at": "2020-01-01T08:30:00+08:00",
                            "data": {"taiex": {"value": 1}}})
        f = make_fetch({f"{BASE}/data/v2/macro.json": (200, stale)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("macro.json generated_at 超過 24h" in e for e in errors))

    def test_macro_empty_data_caught(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        bad = json.dumps({"generated_at": now, "data": {}})
        f = make_fetch({f"{BASE}/data/v2/macro.json": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("macro.json data 缺或為空" in e for e in errors))

    # ── 新聞資料層斷言 ──
    def test_news_missing_field_caught(self):
        bad = json.dumps({"generated_at": "2026-07-06T08:30:00+08:00", "items": [
            {"title": "缺url", "source": "x", "published_at": "p", "fetched_at": "f",
             "matched_keywords": []}]})
        f = make_fetch({f"{BASE}/data/v2/news.json": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("news.json 條目缺欄位" in e for e in errors))

    def test_news_body_leak_caught(self):
        bad = json.dumps({"generated_at": "2026-07-06T08:30:00+08:00", "items": [
            {"title": "t", "source": "s", "published_at": "p", "fetched_at": "f",
             "url": "u", "matched_keywords": ["AI"], "description": "全文內容"}]})
        f = make_fetch({f"{BASE}/data/v2/news.json": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("版權紅線" in e for e in errors))

    def test_news_absent_is_soft(self):
        f = make_fetch({f"{BASE}/data/v2/news.json": (404, "")})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertEqual([e for e in errors if "news" in e], [])

    # ── 籌碼斷言(§3.5)──
    def test_chips_malformed_length_caught(self):
        bad = _chips_chart_json(foreign=[10, -5])   # 長度 2 ≠ dates 3
        f = make_fetch({f"{BASE}/data/v2/{DATE}/TWSE_2345.json": (200, bad)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertTrue(any("chips TWSE_2345 foreign_net 長度不符" in e for e in errors))

    def test_chips_absent_is_soft(self):
        nochips = json.dumps({"symbol": "TWSE:2345"})   # 無 chips → N/A 護欄放行
        f = make_fetch({f"{BASE}/data/v2/{DATE}/TWSE_2345.json": (200, nochips)})
        errors = vp.run_checks(BASE, DATE, fetch=f)
        self.assertEqual([e for e in errors if "chips" in e], [])


if __name__ == "__main__":
    unittest.main()
