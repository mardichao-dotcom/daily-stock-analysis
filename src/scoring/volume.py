"""
volume.py — 異常成交量計分(規則 §1-B)

對齊規則 v2.1(§7 標記「改名為異常成交量,權重簡化」屬於「調整」項目)。
跟 v1 行為的差異記在 docs/stage8_pending_review.md。

純函式:不讀檔不查 DB。caller 自己計算 vol_ratio,本函式只做門檻判定。

設計:
  門檻、分數、label 全部來自 weights.json["volume"],
  程式碼用「sorted by threshold desc + 第一個符合就 return」實作「取較大者」,
  未來想加 x3_0、x4_0 只動 weights.json,不動程式碼。

規則(rule §1-B):
  異常量(小) 當日量 ≥ 20 日平均量 × 1.6 → +1
  異常量(大) 當日量 ≥ 20 日平均量 × 2.0 → +2
  取較大者,不疊加(weights["volume"]["stacking"] = false)
"""
from __future__ import annotations


def score(
    symbol: str,
    date: str,
    volume_data: dict,
    weights: dict,
) -> tuple[float, list[dict]]:
    """異常成交量計分。

    Parameters
    ----------
    symbol : str
        股票代號(僅供 log/debug)
    date : str
        資料日期 ISO 格式(同上)
    volume_data : dict
        必含欄位:
          vol_ratio    float    當日量 / 20 日平均量(caller 預先算好)
        以下為 evidence 欄位(可選):
          today_volume    int | None
          avg_volume      float | None    20 日平均量
    weights : dict
        已載入的 weights.json dict

    Returns
    -------
    (score, details)
    """
    w = weights["volume"]
    ratio = volume_data.get("vol_ratio")
    if ratio is None:
        return 0.0, []

    # ── 從 weights 撈所有 tier(忽略 "stacking" 之類的非 dict 旗標)──────────
    tiers = [(k, v) for k, v in w.items() if isinstance(v, dict)]
    # 取較大者:依 threshold 降冪排,第一個符合就 return
    tiers.sort(key=lambda kv: -kv[1]["threshold"])

    for _, cfg in tiers:
        if ratio >= cfg["threshold"]:
            ev: dict = {"vol_ratio": round(ratio, 4)}
            if volume_data.get("today_volume") is not None:
                ev["today_volume"] = volume_data["today_volume"]
            if volume_data.get("avg_volume") is not None:
                ev["avg_20d_volume"] = volume_data["avg_volume"]
            return float(cfg["score"]), [{
                "reason":   f"{cfg['label']} ≥ {cfg['threshold']}x",
                "score":    cfg["score"],
                "evidence": ev,
            }]

    return 0.0, []
