"""
chip_etf.py — ETF 籌碼面計分(規則 §1-A)

完全沿用 v1 行為。語意 review 列入 docs/stage8_pending_review.md。

純函式:不讀檔不查 DB。caller 自行準備 etf_data dict(通常從 load_data.py 來)。

規則(rule §1-A):
  共識加碼  ≥ 4 檔 → +3
            ≥ 2 檔 → +2  (取代,不疊加)
  連續加碼  +1 (額外加)
  異常點火  +1 (額外加)
  最高 +4   (共識 +3 + 連續 +1;此時 ETF 數 ≥ 4,點火依定義不會觸發)

v1 定義(本檔遵循):
  連續加碼 = 今天有任何 ETF 買 AND 7 日內其他天也有任何 ETF 買
            (個股維度,不要求同檔 ETF 重複出現)
  異常點火 = 恰好 1 檔 ETF 買 AND 該 ETF 買超 > 當日股票量 10%
            (跟共識加碼互斥:共識需 ≥ 2 檔,點火需恰好 1 檔)
  7 日窗口 = 自然日(v1 用 timedelta(days=6),非交易日)
"""
from __future__ import annotations


def score(
    symbol: str,
    date: str,
    etf_data: dict,
    weights: dict,
) -> tuple[float, list[dict]]:
    """ETF 籌碼計分。

    Parameters
    ----------
    symbol : str
        股票代號(僅供 log/debug;計分邏輯不依賴)
    date : str
        資料日期 ISO 格式(同上)
    etf_data : dict
        必含欄位:
          buy_count            int        7 日內買進的 ETF 數(unique)
          buy_etfs             list[str]  7 日內買進的 ETF 代號(unique,給 evidence 用)
          is_continuous_buy    bool       v1 寬鬆定義
          is_abnormal_ignition bool       v1 嚴格定義(恰好 1 檔)
        以下為 evidence 欄位(可選,缺失時 details 仍可產出但 evidence 較簡略):
          ignition_etf         str | None
          ignition_shares      int | None
          today_volume         int | None
    weights : dict
        已載入的 weights.json dict

    Returns
    -------
    (score, details)
      score : float    本 stock 的籌碼計分加總(0~4)
      details : list   每個觸發訊號的明細,含 reason / score / evidence
    """
    w = weights["chip_etf"]
    total: float = 0.0
    details: list[dict] = []

    buy_count = etf_data.get("buy_count", 0)
    buy_etfs  = list(etf_data.get("buy_etfs", []))

    # ── 共識加碼(取較高者,不疊加)──────────────────────────────────────────
    if buy_count >= 4:
        s = w["consensus_4"]
        total += s
        details.append({
            "reason":   "ETF 共識加碼(≥ 4 檔)",
            "score":    s,
            "evidence": buy_etfs,
        })
    elif buy_count >= 2:
        s = w["consensus_2"]
        total += s
        details.append({
            "reason":   "ETF 共識加碼(≥ 2 檔)",
            "score":    s,
            "evidence": buy_etfs,
        })

    # ── 連續加碼(額外加)──────────────────────────────────────────────────
    if etf_data.get("is_continuous_buy"):
        s = w["continuous"]
        total += s
        details.append({
            "reason":   "ETF 連續加碼(7 日內多日有買進)",
            "score":    s,
            "evidence": buy_etfs,
        })

    # ── 異常點火(額外加,定義上跟共識互斥但跟連續可共存)─────────────────
    if etf_data.get("is_abnormal_ignition"):
        s = w["abnormal"]
        total += s
        ev: dict = {}
        if etf_data.get("ignition_etf"):
            ev["etf"] = etf_data["ignition_etf"]
        if etf_data.get("ignition_shares") is not None:
            ev["shares"] = etf_data["ignition_shares"]
        if etf_data.get("today_volume") is not None:
            ev["today_volume"] = etf_data["today_volume"]
            if etf_data.get("ignition_shares") is not None and etf_data["today_volume"] > 0:
                ev["ratio"] = round(
                    etf_data["ignition_shares"] / etf_data["today_volume"], 4
                )
        details.append({
            "reason":   "ETF 異常點火(單一 ETF 買超 > 10%)",
            "score":    s,
            "evidence": ev if ev else None,
        })

    return total, details
