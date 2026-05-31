"""
sector_linkage.py — 族群連動計分(規則 §1-C)

對齊規則 v2.1。**完全砍掉 v1 邏輯**,概念換成「國際長子 → 台股族群」連動。
v1 跟 v2.1 的差異記在 docs/stage8_pending_review.md。

純函式:不讀檔不查 DB。caller 預先算好「國際長子是否發動」+「板塊評級」。

公式(rule §1-C):
  國際長子發動 + 高連動族群(評級 ≥ B 級)→ 給該族群所有台股 +1

國際長子「發動」定義(rule §1-C,語意解讀):
  (a) 漲幅 > 3% 且 量比 > 1.5x,或
  (b) 突破 60 日高
  (c) 漲停 / 跌停 → 本實作跳過(國際 symbol 沒有漲跌停制度,per user C5)

⚠️ 規則 §1-C 字面寫「漲跌幅**絕對值** > 3%」,本實作改為「**只算漲**」(`chg > 3`)。
   語意理由:「國際長子發動」= 資金流入訊號 → 給族群 +1。
   跌 > 3% + 爆量是恐慌出貨(資金流出),不該觸發加分。
   純加分制下不加分是正確處理;跌訊號由規則 §4「🔴 跌破標籤」處理,
   不該誤觸發 sector_linkage。

評級比較:
  weights["sector_linkage"]["level_rank"] 把 A/B/C/D 對應到數值,
  避免 Python 字串比較反向("A" >= "B" 為 False)。
"""
from __future__ import annotations


def is_intl_leader_activated(kline_data: dict, weights: dict) -> bool:
    """判斷一檔國際長子今天是否「發動」。

    Parameters
    ----------
    kline_data : dict
        必含:
          change_pct  float   漲跌幅 %(可正可負)
          vol_ratio   float   量比(today_vol / avg_vol)
          close       float   今日收盤
          high_60d    float   前 60 日(不含今天)的最高價
    weights : dict
        已載入的 weights.json
    """
    trigger = weights["sector_linkage"]["trigger"]

    # 條件 (a):漲幅 > 3% 且 量比 > 1.5x(只算漲,語意決策,見 module docstring)
    chg = kline_data.get("change_pct", 0.0)
    vol = kline_data.get("vol_ratio",  0.0)
    if chg > trigger["price_change_pct"] and vol > trigger["volume_ratio"]:
        return True

    # 條件 (b):突破 60 日高
    close    = kline_data.get("close",    0.0)
    high_60d = kline_data.get("high_60d", float("inf"))
    if close > high_60d:
        return True

    # 條件 (c) 漲停/跌停 — 對國際 symbol 不適用,跳過
    return False


def score(
    symbol: str,
    date: str,
    sector_data: dict,
    weights: dict,
) -> tuple[float, list[dict]]:
    """族群連動計分。

    Parameters
    ----------
    symbol : str
        台股 symbol(僅供 log/debug)
    date : str
        資料日期
    sector_data : dict
        必含:
          sector                    str | None    台股板塊名(來自 watchlist.json)。
                                                  None / "" 代表無板塊歸屬 → 0 分
          sector_level              str | None    板塊評級(來自 sectors.json,A/B/C/D)
          intl_activated            bool          是否有任一國際長子今天發動
        evidence(可選):
          intl_leaders_activated    list[str]     哪些國際長子發動(給 tooltip 用)
    weights : dict
        已載入的 weights.json

    Returns
    -------
    (score, details)
    """
    if not sector_data.get("sector"):
        return 0.0, []   # 個股無板塊歸屬(如京鼎尚未進 watchlist)
    if not sector_data.get("intl_activated"):
        return 0.0, []   # 沒有國際長子發動

    cfg          = weights["sector_linkage"]
    level_rank   = cfg["level_rank"]
    min_level    = cfg["min_level"]
    sector_level = sector_data.get("sector_level")

    # 嚴格模式:未知 level 直接 raise(DD2)
    if sector_level not in level_rank:
        raise ValueError(f"Unknown sector level: {sector_level!r}")
    if min_level not in level_rank:
        raise ValueError(f"Unknown min_level in weights: {min_level!r}")

    if level_rank[sector_level] < level_rank[min_level]:
        return 0.0, []   # 評級未達門檻

    s = cfg["score"]
    return float(s), [{
        "reason":   f"族群連動(板塊評級 {sector_level} ≥ {min_level},國際長子發動)",
        "score":    s,
        "evidence": {
            "sector":             sector_data["sector"],
            "sector_level":       sector_level,
            "activated_leaders":  sector_data.get("intl_leaders_activated", []),
        },
    }]
