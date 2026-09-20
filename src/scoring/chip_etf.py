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

特徵語意(2026-09-20 起改由 etf_holdings_io 依 etf_holdings.db PCF 快照計算;
本計分函式維持資料源無關,只吃 etf_data dict):
  加碼      = 每單位股數 Δ ≥ +3% 且 權重 Δ ≥ +0.2pp(spu 中和淨流入 + 權重雙確認)
  建倉      = 7 天前佔位/未持有 → 今日實倉(權重 ≥ 0.2pp)
  連續加碼  = 7 日窗口內聚合權重 ≥2 個交易日續增(且有買訊)
  異常點火  = 恰好 1 檔 ETF 有買訊 且 該檔為「建倉」(單一經理人新建倉)
  共識加碼  = 窗口內「加碼 or 建倉」的 unique ETF 數(≥4→+3、≥2→+2,取高不疊加)
  7 日窗口  = 自然日,右邊界綁 data_date(斷更 >7 日 → 歸零,不冒充)
(舊 etfedge/etf_operations 口徑見 git 史;evidence 舊欄位仍相容。)
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
            "reason":   "ETF 連續加碼(7 日窗口內多日權重續增)",
            "score":    s,
            "evidence": buy_etfs,
        })

    # ── 異常點火(額外加,定義上跟共識互斥但跟連續可共存)─────────────────
    # 2026-09-20 新資料源:點火 = 恰好 1 檔 ETF「建倉」(佔位→實倉,單一經理人新建倉)。
    # (舊 etfedge 口徑為「單一 ETF 買超 > 當日量 10%」,張數口徑,已不適用。)
    # 舊欄位 ignition_shares/today_volume 若仍提供則相容顯示;新源改帶 ignition_weight。
    if etf_data.get("is_abnormal_ignition"):
        s = w["abnormal"]
        total += s
        ev: dict = {}
        if etf_data.get("ignition_etf"):
            ev["etf"] = etf_data["ignition_etf"]
        if etf_data.get("ignition_weight") is not None:
            ev["weight_pct"] = etf_data["ignition_weight"]
        if etf_data.get("ignition_shares") is not None:            # 舊源相容
            ev["shares"] = etf_data["ignition_shares"]
        if etf_data.get("today_volume") is not None and etf_data.get("ignition_shares") is not None \
                and etf_data["today_volume"] > 0:
            ev["ratio"] = round(etf_data["ignition_shares"] / etf_data["today_volume"], 4)
        details.append({
            "reason":   "ETF 建倉點火(單一 ETF 新建倉)",
            "score":    s,
            "evidence": ev if ev else None,
        })

    return total, details
