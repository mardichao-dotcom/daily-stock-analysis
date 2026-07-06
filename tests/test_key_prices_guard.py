"""
test_key_prices_guard.py — W2-4(審計 2026-07-07):
  1. key_prices schema 驗證(convert 階段:價格可 float、low<high、category 白名單)
  2. run_filters 壞線隔離(一條壞線不拖垮整跑,錯誤列哪檔哪條)
"""
from __future__ import annotations
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.key_prices_schema import validate_key_prices, load_valid_categories
from src import run_filters_v2

CATS = {"key_price", "inner_support", "order_block", "poc", "fvg", "gap"}


class TestSchemaValidation(unittest.TestCase):
    def test_valid_passes(self):
        kp = {"stocks": {"TWSE:2330": {
            "lines": [{"price": "1000", "category": "key_price"}],
            "areas": [{"low": "900", "high": "950", "category": "order_block"}]}}}
        self.assertEqual(validate_key_prices(kp, CATS), [])

    def test_unfloatable_price_caught(self):
        kp = {"stocks": {"TWSE:2330": {
            "lines": [{"price": "1,000", "category": "key_price"}], "areas": []}}}
        probs = validate_key_prices(kp, CATS)
        self.assertEqual(len(probs), 1)
        self.assertIn("不可轉 float", probs[0])
        self.assertIn("TWSE:2330", probs[0])            # 報哪檔

    def test_area_low_not_lt_high_caught(self):
        kp = {"stocks": {"X": {"lines": [], "areas": [
            {"low": "950", "high": "900", "category": "poc"}]}}}
        probs = validate_key_prices(kp, CATS)
        self.assertTrue(any("未小於" in p for p in probs))

    def test_unknown_category_caught(self):
        kp = {"stocks": {"X": {"lines": [
            {"price": "100", "category": "神祕線"}], "areas": []}}}
        probs = validate_key_prices(kp, CATS)
        self.assertTrue(any("白名單" in p for p in probs))

    def test_production_key_prices_passes(self):
        """現行 config/key_prices.json 必須通過(否則 convert 閘會擋掉重轉)。"""
        with open(os.path.join(PROJECT_ROOT, "config", "key_prices.json"),
                  encoding="utf-8") as f:
            kp = json.load(f)
        probs = validate_key_prices(kp, load_valid_categories())
        self.assertEqual(probs, [], f"production key_prices 有 schema 問題:{probs[:5]}")


class TestBadLineIsolation(unittest.TestCase):
    """壞線隔離:run_pipeline 不崩、好線照算、input_errors 列哪檔哪條。"""

    def _pipeline(self, key_prices):
        # 借用 8 天循環 fixture(旺矽 TPEX:6223)
        from tests.test_run_filters_v2 import (
            setup_fixture_kline_db, load_real_weights,
            FIXTURE_SECTORS, FIXTURE_WATCHLIST)
        conn = setup_fixture_kline_db()
        try:
            # 5/13(觸發日)→ 5/14(STANDING 日,好線 +0.7)
            for d in ("2026-05-13", "2026-05-14"):
                out = run_filters_v2.run_pipeline(
                    date=d, conn_kline=conn, conn_etf=None,
                    weights=load_real_weights(), sectors=FIXTURE_SECTORS,
                    key_prices=key_prices, watchlist=FIXTURE_WATCHLIST,
                    now_iso=f"{d}T19:00:00+08:00")
            return out
        finally:
            conn.close()

    def test_bad_line_isolated_good_line_still_scores(self):
        kp = {"stocks": {"TPEX:6223": {"lines": [
            {"price": "壞掉的價", "category": "inner_support"},              # float() 會炸
            {"price": "4640", "color": "black", "category": "inner_support",
             "adjective": "small", "text": "小內撐"},                 # 好線(同 8 天 fixture)
        ], "areas": []}}}
        out = self._pipeline(kp)
        errs = out["metadata"]["input_errors"]
        self.assertEqual(len(errs), 1)                   # 每 run 各自報告(out=最後一天)
        self.assertIn("TPEX:6223", errs[0])              # 報哪檔
        self.assertIn("壞掉的價", errs[0])                # 報哪條
        # 好線照常走完狀態機:5/14 STANDING +0.7(與 8 天循環 fixture 同值)
        self.assertAlmostEqual(out["stocks"]["TPEX:6223"]["score"], 0.7)

    def test_bad_category_isolated(self):
        kp = {"stocks": {"TPEX:6223": {"lines": [
            {"price": "4640", "category": "不存在類別"},
        ], "areas": []}}}
        out = self._pipeline(kp)                         # 不崩
        self.assertTrue(any("不存在類別" in e for e in out["metadata"]["input_errors"]))
        self.assertEqual(out["stocks"]["TPEX:6223"]["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
