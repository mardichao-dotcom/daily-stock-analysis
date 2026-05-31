"""
macd.py — MACD 動能轉換偵測(W2.2.7,2026-05-28 規格修訂)

業界標準參數 (12, 26, 9):
  EMA12 / EMA26 用 close 算
  DIF = EMA12 - EMA26
  DEA = EMA9 of DIF
  OSC = DIF - DEA(柱狀圖)

⚠️ 命名說明(對外用「動能轉多/轉空」,程式內部保留 green_to_red/red_to_green):

  程式內部字串 "green_to_red" / "red_to_green" 沿用台股傳統 MACD 命名,
  實際對應:
    green_to_red = OSC 由負轉正 = **動能轉多** = 買點訊號(計分 +1)
    red_to_green = OSC 由正轉負 = **動能轉空**(純標籤,不計分)

  TradingView 的顏色定義跟台股相反(綠=漲、紅=跌),所以對外標籤
  一律用「動能轉多/轉空」,不用顏色字眼。

暖機(SMA initialization,業界標準):
  EMA12 在索引 11 開始有值(SMA seed)
  EMA26 在索引 25 開始有值
  DIF   在索引 25 開始有值
  DEA   在索引 25+8 = 33 開始有值(EMA9 of DIF 暖機 9 個 DIF 值)
  OSC   在索引 33 開始有值
  → math min 35 天才能 2 天偵測;production strict 50 天

轉換偵測(**當天就報**,2026-05-28 修訂 — 原連續 2 天確認易漏買點):
  動能轉多:osc[-2] < 0 AND osc[-1] > 0(OSC 由負轉正)→ "green_to_red"
  動能轉空:osc[-2] > 0 AND osc[-1] < 0(OSC 由正轉負)→ "red_to_green"
  OSC == 0 視為「無明確方向」,不參與判定(strict `<` / `>`)

純函式:無 IO,可獨立測試。caller(run_filters_v2)決定 lookback 跟串接。
"""
from __future__ import annotations


def compute_ema(values: list[float], period: int) -> list[float | None]:
    """標準 EMA + SMA 暖機。

    回傳 list 長度 = len(values)。
    索引 < period-1 為 None(資料不足)。
    索引 period-1:SMA(first `period` values)當 seed。
    索引 period+:遞迴公式 EMA = α × value + (1-α) × prev_EMA,α = 2/(period+1)。
    """
    n = len(values)
    if n < period or period <= 0:
        return [None] * n

    result: list[float | None] = [None] * (period - 1)
    sma_seed = sum(values[:period]) / period
    result.append(sma_seed)

    alpha = 2.0 / (period + 1)
    prev_ema = sma_seed
    for i in range(period, n):
        ema = alpha * values[i] + (1.0 - alpha) * prev_ema
        result.append(ema)
        prev_ema = ema

    return result


def compute_macd(closes: list[float]) -> dict:
    """計算 MACD 三線。回傳 {"dif": list, "dea": list, "osc": list},
    三個 list 長度 = len(closes)。
    早期索引為 None(資料不足對應 EMA 暖機要求)。
    """
    n = len(closes)
    ema12 = compute_ema(closes, 12)
    ema26 = compute_ema(closes, 26)

    # DIF = EMA12 - EMA26(兩者都有值才算)
    dif: list[float | None] = [None] * n
    for i in range(n):
        if ema12[i] is not None and ema26[i] is not None:
            dif[i] = ema12[i] - ema26[i]

    # DEA = EMA9 of DIF。對「有值的 DIF」做 EMA9(暖機需 9 個 DIF 值)
    first_dif_idx = next((i for i, v in enumerate(dif) if v is not None), None)
    dea: list[float | None] = [None] * n
    if first_dif_idx is not None:
        valid_dif = [v for v in dif[first_dif_idx:] if v is not None]
        dea_partial = compute_ema(valid_dif, 9)
        for j, val in enumerate(dea_partial):
            if val is not None:
                dea[first_dif_idx + j] = val

    # OSC = DIF - DEA(兩者都有值才算)
    osc: list[float | None] = [None] * n
    for i in range(n):
        if dif[i] is not None and dea[i] is not None:
            osc[i] = dif[i] - dea[i]

    return {"dif": dif, "dea": dea, "osc": osc}


def detect_transition(osc_values: list) -> str | None:
    """從 OSC 序列的最後 2 根偵測動能轉換(2026-05-28 規格修訂)。

    當天就報(原連續 2 天確認易漏買點 — 朋友 review 修訂):
      動能轉多:osc[-2] < 0 AND osc[-1] > 0(OSC 由負轉正)→ "green_to_red"
      動能轉空:osc[-2] > 0 AND osc[-1] < 0(OSC 由正轉負)→ "red_to_green"

    OSC == 0 視為「無明確方向」,不參與判定(strict `<` / `>`)。

    ⚠️ 程式內部 green_to_red / red_to_green 字串不改,**對外標籤**改用
       「動能轉多/轉空」(避免台股 vs TradingView 顏色相反混淆)。
       字串對應在 caller(run_filters_v2._compute_macd)轉成正確標籤。

    Returns
    -------
    "green_to_red"(動能轉多)/ "red_to_green"(動能轉空)/ None
    """
    if len(osc_values) < 2:
        return None

    d_prev = osc_values[-2]
    d_curr = osc_values[-1]
    if d_prev is None or d_curr is None:
        return None

    # 動能轉多:OSC 由負轉正
    if d_prev < 0 and d_curr > 0:
        return "green_to_red"
    # 動能轉空:OSC 由正轉負
    if d_prev > 0 and d_curr < 0:
        return "red_to_green"

    return None
