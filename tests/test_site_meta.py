"""
test_site_meta.py — P1 §6.3 渲染單一資料源
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import site_meta


WATCHLIST = {
    "更新日期": "2026-05-14",   # 內嵌欄位故意 stale,驗證改用 mtime
    "台股板塊": {"半導體": {"成員": [{"code": "TWSE:2330", "name": "台積電"},
                                    {"code": "TPEX:6223", "name": "旺矽"}]},
                 "金融": {"成員": [{"code": "TWSE:2881", "name": "富邦金"}]}},
    "國際族群": {"美股": {"成員": [{"code": "NASDAQ:NVDA", "name": "輝達"}]}},
}
SECTORS = {"rule_version": "v2.2"}


class TestBuild(unittest.TestCase):

    def _watchlist_file(self, tmp):
        p = Path(tmp) / "watchlist.json"
        p.write_text(json.dumps(WATCHLIST, ensure_ascii=False), encoding="utf-8")
        return p

    def test_counts_and_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            wpath = self._watchlist_file(tmp)
            meta = site_meta.build("2026-06-11", watchlist=WATCHLIST, sectors=SECTORS,
                                   filtered_result=None, watchlist_path=wpath)
            self.assertEqual(meta["tw_count"], 3)
            self.assertEqual(meta["intl_count"], 1)
            self.assertEqual(meta["total_count"], 4)

    def test_rule_version_from_sectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            wpath = self._watchlist_file(tmp)
            meta = site_meta.build("2026-06-11", watchlist=WATCHLIST, sectors=SECTORS,
                                   filtered_result=None, watchlist_path=wpath)
            self.assertEqual(meta["rule_version"], "v2.2")

    def test_watchlist_updated_from_mtime_not_embedded(self):
        # 內嵌 更新日期 是 2026-05-14,但應改用 mtime(不等於 stale 值)
        with tempfile.TemporaryDirectory() as tmp:
            wpath = self._watchlist_file(tmp)
            os.utime(wpath, (1_750_000_000, 1_750_000_000))  # 固定 mtime
            meta = site_meta.build("2026-06-11", watchlist=WATCHLIST, sectors=SECTORS,
                                   filtered_result=None, watchlist_path=wpath)
            self.assertNotEqual(meta["watchlist_updated"], "2026-05-14")
            self.assertRegex(meta["watchlist_updated"], r"^\d{4}-\d{2}-\d{2}$")

    def test_skipped_from_filtered_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            wpath = self._watchlist_file(tmp)
            fr = {"metadata": {"skipped_symbols": ["TWSE:9999"]}}
            meta = site_meta.build("2026-06-11", watchlist=WATCHLIST, sectors=SECTORS,
                                   filtered_result=fr, watchlist_path=wpath)
            self.assertEqual(meta["skipped"], ["TWSE:9999"])

    def test_write_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            wpath = self._watchlist_file(tmp)
            meta = site_meta.build("2026-06-11", watchlist=WATCHLIST, sectors=SECTORS,
                                   filtered_result=None, watchlist_path=wpath)
            outdir = Path(tmp) / "v2"
            site_meta.write(meta, outdir, "2026-06-11")
            loaded = site_meta.load("2026-06-11", outdir)
            self.assertEqual(loaded, meta)

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(site_meta.load("2099-01-01", Path(tmp)))


if __name__ == "__main__":
    unittest.main()
