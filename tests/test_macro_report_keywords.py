"""
test_macro_report_keywords.py — news_keywords.json 讀取防呆(手滑改壞→沿用上一份+告警)
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import macro_report as mr


class TestNewsKeywordsGuard(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.kw = os.path.join(self.d, "news_keywords.json")
        self.cache = os.path.join(self.d, ".cache.json")
        self._orig = (mr.NEWS_KW, mr.NEWS_KW_CACHE)
        mr.NEWS_KW, mr.NEWS_KW_CACHE = self.kw, self.cache

    def tearDown(self):
        mr.NEWS_KW, mr.NEWS_KW_CACHE = self._orig
        for p in (self.kw, self.cache):
            if os.path.exists(p):
                os.unlink(p)
        os.rmdir(self.d)

    def _write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_valid_updates_cache_no_alert(self):
        self._write(self.kw, json.dumps({"keywords": ["台積電", "關稅"]}))
        kws, alert = mr._load_news_keywords()
        self.assertEqual(kws, ["台積電", "關稅"])
        self.assertIsNone(alert)
        self.assertTrue(os.path.exists(self.cache))       # 快取已落地

    def test_malformed_falls_back_to_cache_with_alert(self):
        self._write(self.cache, json.dumps({"keywords": ["舊清單A", "舊清單B"]}))
        self._write(self.kw, '{"keywords": ["壞掉,少引號]}')    # 手滑壞 JSON
        kws, alert = mr._load_news_keywords()
        self.assertEqual(kws, ["舊清單A", "舊清單B"])          # 沿用上一份
        self.assertIsNotNone(alert)
        self.assertIn("解析失敗", alert)

    def test_malformed_no_cache_empty_with_alert(self):
        self._write(self.kw, "{ not json")
        kws, alert = mr._load_news_keywords()
        self.assertEqual(kws, [])
        self.assertIn("無有效快取", alert)

    def test_keywords_not_list_treated_invalid(self):
        self._write(self.cache, json.dumps({"keywords": ["fallback"]}))
        self._write(self.kw, json.dumps({"keywords": "台積電"}))   # 非 list
        kws, alert = mr._load_news_keywords()
        self.assertEqual(kws, ["fallback"])
        self.assertIsNotNone(alert)


if __name__ == "__main__":
    unittest.main()
