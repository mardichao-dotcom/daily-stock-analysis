"""
test_add_symbols_batch.py — 批次新增 watchlist + key_prices 個股 smoke tests
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import add_symbols_batch as asb


# ── fixtures ─────────────────────────────────────────────────────────────────

def make_watchlist(extra_members=None):
    return {
        "更新日期": "2026-06-02",
        "版本": "test",
        "台股板塊": {
            "IC設計": {
                "成員": [{"code": "TWSE:2330", "name": "台積電"}],
                "長子": ["TWSE:2330"],
            },
            "被動元件": {
                "成員": (extra_members or []),
                "長子": [],
            },
        },
        "國際族群": {},
    }


def make_sectors():
    return {
        "version": "1.0",
        "sectors": {
            "IC設計":   {"rating": "A"},
            "被動元件": {"rating": "B"},
            "光通訊":   {"rating": "A"},
        },
    }


def make_key_prices():
    return {
        "version": "1.0",
        "stocks": {
            "TWSE:2330": {
                "name": "台積電",
                "sector": "IC設計",
                "market": "TW",
                "lines": [], "areas": [],
            },
        },
    }


def write_files(tmpdir, watchlist=None, key_prices=None, sectors=None):
    """寫 3 個 config 檔到 tmpdir,回 paths dict。"""
    wl_path = Path(tmpdir) / "watchlist.json"
    kp_path = Path(tmpdir) / "key_prices.json"
    sc_path = Path(tmpdir) / "sectors.json"
    with open(wl_path, "w", encoding="utf-8") as f:
        json.dump(watchlist or make_watchlist(), f, ensure_ascii=False)
    with open(kp_path, "w", encoding="utf-8") as f:
        json.dump(key_prices or make_key_prices(), f, ensure_ascii=False)
    with open(sc_path, "w", encoding="utf-8") as f:
        json.dump(sectors or make_sectors(), f, ensure_ascii=False)
    return wl_path, kp_path, sc_path


def good_entry():
    return {
        "code": "TWSE:2454",
        "name": "聯發科",
        "sector": "IC設計",
        "key_prices": {
            "lines": [
                {"price": 1100, "category": "關鍵價格", "color": "red"},
                {"price": 1050, "category": "MA60",     "color": "black"},
            ],
            "areas": [
                {"low": 1020, "high": 1080, "category": "訂單塊"},
            ],
        },
    }


# ────────────────────────────────────────────────────────────────────────────
# 1. validate: invalid code format
# ────────────────────────────────────────────────────────────────────────────

class TestValidateInvalidCodeFormat(unittest.TestCase):

    def test_validate_invalid_code_format(self):
        bad_entries = [
            {**good_entry(), "code": "2454"},               # missing prefix
            {**good_entry(), "code": "TWSE:"},              # missing number
            {**good_entry(), "code": "BADPREFIX:1234"},     # unknown prefix
        ]
        valid_sectors = {"IC設計"}
        errors = asb.validate(bad_entries, valid_sectors, existing_codes=set())
        # 每 bad entry 至少 1 個 code format error
        format_errs = [e for e in errors if "code 格式錯誤" in e]
        self.assertEqual(len(format_errs), 3)


# ────────────────────────────────────────────────────────────────────────────
# 2. validate: unknown sector
# ────────────────────────────────────────────────────────────────────────────

class TestValidateUnknownSector(unittest.TestCase):

    def test_validate_unknown_sector(self):
        entry = {**good_entry(), "sector": "不存在的板塊"}
        valid_sectors = {"IC設計"}
        errors = asb.validate([entry], valid_sectors, set())
        sector_errs = [e for e in errors if "未知 sector" in e]
        self.assertEqual(len(sector_errs), 1)
        self.assertIn("不存在的板塊", sector_errs[0])

    def test_validate_missing_sector(self):
        entry = {**good_entry()}
        del entry["sector"]
        errors = asb.validate([entry], {"IC設計"}, set())
        self.assertTrue(any("缺 sector" in e for e in errors))


# ────────────────────────────────────────────────────────────────────────────
# 3. dry-run: 不改檔
# ────────────────────────────────────────────────────────────────────────────

class TestDryRunNoChanges(unittest.TestCase):

    def test_dry_run_no_changes(self):
        with tempfile.TemporaryDirectory() as td:
            wl, kp, sc = write_files(td)
            wl_before = wl.read_text()
            kp_before = kp.read_text()

            result = asb.run_pipeline(
                entries=[good_entry()],
                watchlist_path=wl,
                key_prices_path=kp,
                sectors_path=sc,
                apply=False,    # dry-run
                _print=lambda *a, **k: None,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(wl.read_text(), wl_before)
            self.assertEqual(kp.read_text(), kp_before)
            # 無 backup 檔
            backups = list(Path(td).glob("*.bak-*"))
            self.assertEqual(backups, [])


# ────────────────────────────────────────────────────────────────────────────
# 4. apply: watchlist 寫入
# ────────────────────────────────────────────────────────────────────────────

class TestApplyAddsToWatchlist(unittest.TestCase):

    def test_apply_adds_to_watchlist(self):
        with tempfile.TemporaryDirectory() as td:
            wl, kp, sc = write_files(td)
            result = asb.run_pipeline(
                entries=[good_entry()],
                watchlist_path=wl, key_prices_path=kp, sectors_path=sc,
                apply=True, _print=lambda *a, **k: None,
            )
            self.assertTrue(result["ok"])
            data = json.loads(wl.read_text())
            ic_members = data["台股板塊"]["IC設計"]["成員"]
            codes = [m["code"] for m in ic_members]
            self.assertIn("TWSE:2454", codes)
            # 找到那檔 + name 對
            new_entry = next(m for m in ic_members if m["code"] == "TWSE:2454")
            self.assertEqual(new_entry["name"], "聯發科")


# ────────────────────────────────────────────────────────────────────────────
# 5. apply: key_prices 寫入
# ────────────────────────────────────────────────────────────────────────────

class TestApplyAddsToKeyPrices(unittest.TestCase):

    def test_apply_adds_to_key_prices(self):
        with tempfile.TemporaryDirectory() as td:
            wl, kp, sc = write_files(td)
            asb.run_pipeline(
                entries=[good_entry()],
                watchlist_path=wl, key_prices_path=kp, sectors_path=sc,
                apply=True, _print=lambda *a, **k: None,
            )
            data = json.loads(kp.read_text())
            entry = data["stocks"]["TWSE:2454"]
            self.assertEqual(entry["name"], "聯發科")
            self.assertEqual(entry["sector"], "IC設計")
            self.assertEqual(entry["market"], "TW")
            # lines:price 轉成 str + category 中→英 normalize
            self.assertEqual(entry["lines"][0]["price"], "1100")
            self.assertEqual(entry["lines"][0]["category"], "key_price")  # 關鍵價格→key_price
            self.assertEqual(entry["lines"][1]["category"], "ma_60")      # MA60→ma_60
            # areas:low/high 轉 str
            self.assertEqual(entry["areas"][0]["low"], "1020")
            self.assertEqual(entry["areas"][0]["high"], "1080")
            self.assertEqual(entry["areas"][0]["category"], "order_block")


# ────────────────────────────────────────────────────────────────────────────
# 6. apply: backup 檔產生
# ────────────────────────────────────────────────────────────────────────────

class TestApplyBackupFilesCreated(unittest.TestCase):

    def test_apply_backup_files_created(self):
        with tempfile.TemporaryDirectory() as td:
            wl, kp, sc = write_files(td)
            result = asb.run_pipeline(
                entries=[good_entry()],
                watchlist_path=wl, key_prices_path=kp, sectors_path=sc,
                apply=True, _print=lambda *a, **k: None,
            )
            self.assertTrue(result["ok"])
            # backups dict 含兩個檔
            self.assertEqual(len(result["backups"]), 2)
            # 實體檔存在
            for orig, backup in result["backups"].items():
                self.assertTrue(Path(backup).exists(),
                                  f"backup {backup} 不存在")
            # backup 內容 = 修改前(原始 fixture)
            wl_backup = result["backups"][str(wl)]
            wl_backup_data = json.loads(Path(wl_backup).read_text())
            ic_members = wl_backup_data["台股板塊"]["IC設計"]["成員"]
            self.assertNotIn("TWSE:2454", [m["code"] for m in ic_members])


# ────────────────────────────────────────────────────────────────────────────
# 7. apply: 中途失敗自動 rollback
# ────────────────────────────────────────────────────────────────────────────

class TestApplyRollbackOnFailure(unittest.TestCase):

    def test_apply_rollback_on_failure(self):
        """模擬 tv_collect 失敗 → backup 還原 watchlist + key_prices"""
        with tempfile.TemporaryDirectory() as td:
            wl, kp, sc = write_files(td)
            wl_before = wl.read_text()
            kp_before = kp.read_text()

            # mock subprocess.run 直接 throw,模擬 tv_collect 失敗
            import subprocess
            def fake_runner(*a, **kw):
                raise subprocess.CalledProcessError(1, a[0])

            result = asb.run_pipeline(
                entries=[good_entry()],
                watchlist_path=wl, key_prices_path=kp, sectors_path=sc,
                apply=True, do_tv_collect=True,
                _runner=fake_runner,
                _print=lambda *a, **k: None,
            )

            # 標記失敗
            self.assertFalse(result["ok"])
            self.assertIn("rollback", result["ran_steps"])
            # 檔案內容已還原
            self.assertEqual(wl.read_text(), wl_before)
            self.assertEqual(kp.read_text(), kp_before)


# ────────────────────────────────────────────────────────────────────────────
# 8. key_prices 可省略
# ────────────────────────────────────────────────────────────────────────────

class TestOptionalKeyPricesField(unittest.TestCase):

    def test_optional_key_prices_field(self):
        """key_prices 不給 → 視為空(先加 watchlist,之後補)"""
        entry = {
            "code":   "TWSE:2454",
            "name":   "聯發科",
            "sector": "IC設計",
            # 沒 key_prices
        }
        # validate 不該報錯
        errors = asb.validate([entry], {"IC設計"}, set())
        self.assertEqual(errors, [])
        # normalize 給空 lines/areas
        normalized = asb.normalize_entry(entry)
        self.assertEqual(normalized["key_prices"], {"lines": [], "areas": []})

    def test_apply_optional_key_prices(self):
        """apply 模式下,沒 key_prices 也能寫:寫進 stocks 但 lines/areas 為 []"""
        with tempfile.TemporaryDirectory() as td:
            wl, kp, sc = write_files(td)
            entry = {"code": "TWSE:2454", "name": "聯發科", "sector": "IC設計"}
            result = asb.run_pipeline(
                entries=[entry],
                watchlist_path=wl, key_prices_path=kp, sectors_path=sc,
                apply=True, _print=lambda *a, **k: None,
            )
            self.assertTrue(result["ok"])
            data = json.loads(kp.read_text())
            self.assertIn("TWSE:2454", data["stocks"])
            self.assertEqual(data["stocks"]["TWSE:2454"]["lines"], [])
            self.assertEqual(data["stocks"]["TWSE:2454"]["areas"], [])


# ────────────────────────────────────────────────────────────────────────────
# bonus:重複 + 已存在 code 偵測
# ────────────────────────────────────────────────────────────────────────────

class TestValidateDuplicateAndExisting(unittest.TestCase):

    def test_validate_duplicate_in_batch(self):
        entries = [good_entry(), good_entry()]   # 一樣的 code 出現 2 次
        errors = asb.validate(entries, {"IC設計"}, set())
        self.assertTrue(any("重複 code" in e for e in errors))

    def test_validate_already_exists(self):
        existing = {"TWSE:2454"}
        errors = asb.validate([good_entry()], {"IC設計"}, existing)
        self.assertTrue(any("已存在" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
