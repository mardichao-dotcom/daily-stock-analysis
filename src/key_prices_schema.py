"""
key_prices_schema.py — key_prices.json 結構驗證(W2-4,審計 2026-07-07)

key_prices 是朋友每週手繪轉檔,輸入錯誤是常態(已有 6 個 .bak 修正輪次)。
在 convert 階段(tools/convert_key_prices.py)先驗,壞資料不落地;
run_filters 端另有壞線隔離 try/except 當第二道(雙保險)。

驗證項(用戶指定):
  - 線:price 可 float、category 在白名單
  - 區域:low/high 可 float、low < high、category 在白名單
白名單來源 = config/weights.json 的 given_price keys(計分權威,單一來源)。
"""
from __future__ import annotations
import json
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEIGHTS_PATH = os.path.join(PROJECT_ROOT, "config", "weights.json")


def load_valid_categories(weights_path: str = WEIGHTS_PATH) -> set:
    with open(weights_path, encoding="utf-8") as f:
        w = json.load(f)
    return {k for k in w.get("given_price", {}) if not k.startswith("_")}


def _floatable(s) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def validate_key_prices(kp: dict, valid_categories: set) -> list[str]:
    """回傳問題清單(空 = 通過)。每條含 symbol + 哪條線/區域 + 原因。"""
    problems: list[str] = []
    for symbol, entry in (kp.get("stocks") or {}).items():
        for i, line in enumerate(entry.get("lines") or []):
            where = f"{symbol} 線[{i}]"
            price = line.get("price")
            if not _floatable(price):
                problems.append(f"{where} price 不可轉 float:{price!r}")
            cat = line.get("category")
            if cat not in valid_categories:
                problems.append(f"{where} category 不在白名單:{cat!r}")
        for i, area in enumerate(entry.get("areas") or []):
            where = f"{symbol} 區域[{i}]"
            low, high = area.get("low"), area.get("high")
            if not _floatable(low) or not _floatable(high):
                problems.append(f"{where} low/high 不可轉 float:low={low!r} high={high!r}")
            elif not float(low) < float(high):
                problems.append(f"{where} low({low}) 未小於 high({high})")
            cat = area.get("category")
            if cat not in valid_categories:
                problems.append(f"{where} category 不在白名單:{cat!r}")
    return problems
