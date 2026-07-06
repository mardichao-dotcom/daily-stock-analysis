"""
test_fetch_news.py — 新聞資料層:關鍵字過濾、只取標題+連結(無內文)、跨日去重保留 3 天。
"""
from __future__ import annotations
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import fetch_news as fn

TZ = timezone(timedelta(hours=8))

RSS = ("""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>台積電 AI 需求強</title>
    <link>https://ex.com/a</link>
    <pubDate>Mon, 06 Jul 2026 17:02:08 +0800</pubDate>
    <description>這是內文摘要不該出現在 news.json</description>
  </item>
  <item>
    <title>無關新聞</title>
    <link>https://ex.com/b</link>
    <pubDate>Mon, 06 Jul 2026 16:00:00 +0800</pubDate>
  </item>
</channel></rss>""").encode("utf-8")


class TestPure(unittest.TestCase):
    def test_match_keywords_ci(self):
        self.assertEqual(fn.match_keywords("AI 與 CPI 齊漲", ["AI", "CPI", "黃金"]), ["AI", "CPI"])
        self.assertEqual(fn.match_keywords("台積電擴廠", ["輝達"]), [])

    def test_to_iso(self):
        self.assertEqual(fn._to_iso("Mon, 06 Jul 2026 17:02:08 +0800"),
                         "2026-07-06T17:02:08+08:00")
        self.assertIsNone(fn._to_iso("not a date"))


class TestFetchSource(unittest.TestCase):
    def setUp(self):
        self._orig = fn._http
        fn._http = lambda url, timeout=20: RSS

    def tearDown(self):
        fn._http = self._orig

    def test_only_matched_and_no_body(self):
        items, err = fn.fetch_source({"name": "測試", "url": "x"}, ["AI"], "NOW")
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)                 # 只留命中 AI 的
        it = items[0]
        self.assertEqual(it["url"], "https://ex.com/a")
        self.assertEqual(it["matched_keywords"], ["AI"])
        self.assertEqual(it["published_at"], "2026-07-06T17:02:08+08:00")
        # 版權紅線:只有標題+連結,無任何內文欄位
        self.assertEqual(set(it.keys()),
                         {"title", "source", "published_at", "fetched_at", "url", "matched_keywords"})
        self.assertFalse(any("內文" in str(v) for v in it.values()))


class TestMergeRetain(unittest.TestCase):
    def test_dedupe_by_url_prev_fetched_at_wins(self):
        now = datetime(2026, 7, 6, 20, 0, tzinfo=TZ)
        prev = [{"url": "u1", "title": "舊", "published_at": "2026-07-06T09:00:00+08:00",
                 "fetched_at": "2026-07-06T08:30:00+08:00"}]
        new = [{"url": "u1", "title": "舊", "published_at": "2026-07-06T09:00:00+08:00",
                "fetched_at": "2026-07-06T20:00:00+08:00"},
               {"url": "u2", "title": "新", "published_at": "2026-07-06T19:00:00+08:00",
                "fetched_at": "2026-07-06T20:00:00+08:00"}]
        merged = fn._merge_retain(new, prev, now)
        self.assertEqual(len(merged), 2)
        u1 = [m for m in merged if m["url"] == "u1"][0]
        self.assertEqual(u1["fetched_at"], "2026-07-06T08:30:00+08:00")   # 首見時間保留

    def test_drops_older_than_retain(self):
        now = datetime(2026, 7, 6, 20, 0, tzinfo=TZ)
        prev = [{"url": "old", "title": "太舊", "published_at": "2026-07-01T09:00:00+08:00",
                 "fetched_at": "2026-07-01T09:00:00+08:00"}]      # >3 天
        new = [{"url": "fresh", "title": "新", "published_at": "2026-07-06T19:00:00+08:00",
                "fetched_at": "2026-07-06T20:00:00+08:00"}]
        merged = fn._merge_retain(new, prev, now)
        self.assertEqual([m["url"] for m in merged], ["fresh"])


if __name__ == "__main__":
    unittest.main()
