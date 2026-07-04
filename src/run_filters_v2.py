"""
run_filters_v2.py — Stage 8 純加分制主幹(W2.1 骨架)

完全新檔,**不繼承不 import v1**(src/run_filters.py / score.py / filter_stage*.py)。

W2.1 範圍:
  ✅ 載 configs + DB 連線 + init_schema
  ✅ 載 K 線歷史(過去 130 個交易日,含今天)
  ✅ 給定價格 line / area 完整 pipeline:
       price_str ↔ given_price 雙層分離
       evaluate_standing → write_state → score_line/area → 累計
  ✅ 站穩 / 跌破標籤、events(給 chart 標記用)
  ✅ 輸出 filtered_result_v2.json

W2.2 進度(全部完成 2026-05-28):
  ✅ chip_etf       接入(W2.2.1,etf_io.py + chip_etf.score)
  ✅ volume         接入(W2.2.2,_compute_volume_features + volume.score)
  ✅ sector_linkage 接入(W2.2.3,_compute_kline_features + activations cache)
  ✅ MA             接入(W2.2.4,_score_ma_features 首次站上 +N,strict history)
  ✅ grader         接入(W2.2.5,floor 規則,5.5→A、6.0→S)
  ✅ rotation       接入(W2.2.6,score_history.db + 巢狀 GROUP BY date,
                          ⭐ 個股輪動 tag 給族群 delta ≥ 2 的成員)
  ✅ MACD           接入(W2.2.7,純標籤不計分,連續 2 天確認,strict 50 天暖機)

對接 5A:
  讀 kline.db.kline / etf_operations.db.operations(既有,只讀不寫)
  寫 kline.db.standing_state(新表,不影響既有)

CLI:
  python3 src/run_filters_v2.py --date 2026-05-26 \\
      --kline kline.db --etf ~/ETF追蹤/etf_operations.db \\
      --output filtered_result_v2.json
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.scoring import chip_etf, volume, sector_linkage, grader, macd
from src.scoring.given_price import score_line, score_area
from src.triggers import standing
from src.persistence import state_io, etf_io, kline_io, score_history_io

# ── 常數 ──────────────────────────────────────────────────────────────────────
KLINE_LOOKBACK_DAYS = 100   # 涵蓋 MA90 + buffer(W2.2.4 MA 計分用)
TZ_TAIPEI           = timezone(timedelta(hours=8))


# ── ETF 減碼純標籤(2026-05-29 朋友 review 後新增,不計分)──────────────────

def _compute_etf_decrease_tag(
    symbol:   str,
    date:     str,
    conn_etf: sqlite3.Connection | None,
) -> list[str]:
    """⛔ ETF 減碼純標籤:當天 ≥2 檔 ETF 減碼/清倉 → 發標籤。

    跟 chip_etf 共識加碼是「同一概念的反向」:
      - chip_etf 共識加碼(7 日窗口)→ 計分加分
      - ETF 減碼純標籤(當天)→ 純風險提醒(不計分)

    純加分制下不扣分,但用 ⛔ 標籤提醒朋友籌碼面有風險。
    Tag 格式:「⛔ ETF 減碼(N 檔, -total 張)」
    """
    if conn_etf is None:
        return []
    code = symbol.split(":")[-1]
    cur = conn_etf.execute(
        "SELECT etf, 張數 FROM operations "
        "WHERE 代號 = ? AND 日期 = ? AND 動作 IN ('減碼', '清倉')",
        (code, date),
    )
    rows = cur.fetchall()
    if not rows:
        return []
    etfs = set(r[0] for r in rows)
    if len(etfs) < 2:
        return []
    total = sum(r[1] for r in rows)
    return [f"⛔ ETF 減碼({len(etfs)} 檔, -{total} 張)"]


# ── MACD 動能轉換(W2.2.7,2026-05-28 規格修訂 — 動能轉多 +1)─────────────

MACD_MIN_HISTORY = 50   # strict:EMA26 + EMA9(DIF) 暖機 + 穩定 buffer


def _compute_macd(
    symbol: str,
    date: str,
    kline_history: list[dict],
    weights: dict,
) -> tuple[list[str], float, list[dict]]:
    """MACD 動能轉換偵測(2026-05-28 規格修訂):

    動能轉多(OSC 由負轉正)→ 計分 +1 + tag「⚡ MACD 動能轉多(買點)」
    動能轉空(OSC 由正轉負)→ 純標籤「⚡ MACD 動能轉空」(不計分)

    當天就報(原連續 2 天確認易漏買點)。
    strict 50 天 history(EMA26 + EMA9(DIF) 暖機 + 些許穩定 buffer)。
    旺矽 8 天 fixture(< 50)→ 自動 skip → 不變承諾保持。

    Returns
    -------
    (tags, score, details) 三元組
    """
    empty: tuple[list[str], float, list[dict]] = ([], 0.0, [])
    if len(kline_history) < MACD_MIN_HISTORY:
        return empty

    closes = [b["close"] for b in kline_history]
    macd_data = macd.compute_macd(closes)
    transition = macd.detect_transition(macd_data["osc"])

    # 動能轉多 — 計分 +1
    if transition == "green_to_red":
        s = weights["macd"]["green_to_red"]
        return (
            ["⚡ MACD 動能轉多(買點)"],
            float(s),
            [{
                "module": "macd",
                "reason": "MACD 動能轉多(OSC 由負轉正)",
                "score":  s,
                "evidence": {
                    "transition":    "green_to_red",
                    "osc_today":     macd_data["osc"][-1],
                    "osc_yesterday": macd_data["osc"][-2],
                },
            }],
        )

    # 動能轉空 — 純標籤,不計分
    if transition == "red_to_green":
        return (["⚡ MACD 動能轉空"], 0.0, [])

    return empty


# ── 載入 ──────────────────────────────────────────────────────────────────────

def load_configs(weights_path, sectors_path, key_prices_path, watchlist_path):
    with open(weights_path,    encoding="utf-8") as f: weights    = json.load(f)
    with open(sectors_path,    encoding="utf-8") as f: sectors    = json.load(f)
    with open(key_prices_path, encoding="utf-8") as f: key_prices = json.load(f)
    with open(watchlist_path,  encoding="utf-8") as f: watchlist  = json.load(f)
    return weights, sectors, key_prices, watchlist


def load_kline_history(
    conn: sqlite3.Connection,
    symbol: str,
    date: str,
    lookback_days: int = KLINE_LOOKBACK_DAYS,
) -> list[dict]:
    """載入 symbol 截至 date(含)往前 `lookback_days` 個交易日的 K 線。

    回傳依日期升冪排序;最後一筆 [-1] 是 today。沒資料回 []。
    """
    cur = conn.execute(
        "SELECT date, open, high, low, close, volume FROM kline "
        "WHERE symbol = ? AND date <= ? "
        "ORDER BY date DESC LIMIT ?",
        (symbol, date, lookback_days),
    )
    rows = cur.fetchall()
    rows.reverse()   # asc
    return [
        {"date": r[0], "open": r[1], "high": r[2], "low": r[3],
         "close": r[4], "volume": r[5]}
        for r in rows
    ]


def iter_tw_symbols(watchlist: dict):
    """從 watchlist 的台股板塊產出所有台股 code(不含國際族群)"""
    for sector_data in watchlist.get("台股板塊", {}).values():
        for member in sector_data.get("成員", []):
            yield member["code"]


def _lookup_stock_meta(symbol: str, watchlist: dict) -> tuple[str, str]:
    """從 watchlist 反查 (name, sector)。W3 加 stocks entry 用。
    台股板塊先找,國際族群其次。找不到回 ("", "")。"""
    for sector_name, sector_data in watchlist.get("台股板塊", {}).items():
        for member in sector_data.get("成員", []):
            if member.get("code") == symbol:
                return member.get("name", ""), sector_name
    for group_name, group_data in watchlist.get("國際族群", {}).items():
        for member in group_data.get("成員", []):
            if member.get("code") == symbol:
                return member.get("name", ""), group_name
    return "", ""


# ── volume features 計算(W2.2.2,純函式)─────────────────────────────────────

VOLUME_WINDOW_DAYS = 20   # rule v2.1 規定 20 日窗口


def _compute_volume_features(
    kline_history: list[dict],
    window: int = VOLUME_WINDOW_DAYS,
) -> dict:
    """從 kline_history 算 volume.score 所需的 features。

    ⚠️ W2.2.4 後 production 不直接呼叫,改透過 _compute_kline_features
       委派(它內部 call 本函式並 merge 進統一 dict)。本函式保留作為
       unit test surface(volume 邊界 + v1 parity)。


    avg_volume = mean(volume of `window` trading days **excluding today**)
                = kline_history[-(window+1):-1]
    vol_ratio  = today_volume / avg_volume

    邊界(對齊 v1 fallback):
      - 沒 history          → 三個欄位全 None
      - 只有 today 一筆      → today_volume 有,avg_volume = None,vol_ratio = None
      - history 不足 window  → 用實際有的(避免新上市股 KeyError)
      - avg_volume == 0     → vol_ratio = None(避免除以零)

    window 參數預設 20(rule v2.1),v1 parity test 用 window=5 驗算式對齊。
    """
    if not kline_history:
        return {"vol_ratio": None, "today_volume": None, "avg_volume": None}

    today_volume = kline_history[-1].get("volume", 0)
    history_excl_today = kline_history[-(window + 1):-1]   # 不含今天

    if not history_excl_today:
        return {"vol_ratio": None, "today_volume": today_volume, "avg_volume": None}

    avg_volume = sum(b.get("volume", 0) for b in history_excl_today) / len(history_excl_today)

    if avg_volume <= 0:
        return {"vol_ratio": None, "today_volume": today_volume, "avg_volume": avg_volume}

    return {
        "vol_ratio":    today_volume / avg_volume,
        "today_volume": today_volume,
        "avg_volume":   avg_volume,
    }


# ── kline 統一特徵(W2.2.3)──────────────────────────────────────────────────

MA_WINDOWS         = (20, 60, 90)
HIGH_60_WINDOW     = 60


def _compute_kline_features(
    kline_history: list[dict],
    volume_window: int = VOLUME_WINDOW_DAYS,
    ma_windows:    tuple[int, ...] = MA_WINDOWS,
    high_window:   int = HIGH_60_WINDOW,
) -> dict:
    """從 kline_history 統一產出所有 K 線衍生特徵。

    給 sector_linkage(W2.2.3)/ MA(W2.2.4)/ 未來其他 module 共用。

    ⚠️ **嚴格模式**(W2.2.4):資料不足對應視窗時回 None,不 fallback。
       理由:fallback 會給 caller 半真半假的 ratio/MA 值,可能誤觸發計分。
       「明確 None」比「偷算半套」安全(尤其新上市股、剛部署的環境)。

    Returns(資料不足對應視窗 → None):
      change_pct    float | None    需要 history ≥ 2
      close         float | None    today.close(history 非空就有)
      high_60d      float | None    需要 history(不含 today)≥ 60
      vol_ratio     float | None    by _compute_volume_features(window=20)
      today_volume  float | None
      avg_volume    float | None
      ma_20         float | None    需要 history ≥ 20
      ma_60         float | None    需要 history ≥ 60
      ma_90         float | None    需要 history ≥ 90
    """
    empty = {
        "change_pct": None, "close": None, "high_60d": None,
        "vol_ratio": None, "today_volume": None, "avg_volume": None,
        **{f"ma_{w}": None for w in ma_windows},
    }
    if not kline_history:
        return empty

    today = kline_history[-1]

    # change_pct(需要昨天)
    if len(kline_history) >= 2:
        prev_close = kline_history[-2].get("close", 0)
        if prev_close and prev_close > 0:
            change_pct = (today["close"] - prev_close) / prev_close * 100
        else:
            change_pct = None
    else:
        change_pct = None

    # high_60d(不含今天,strict:history 不足 60 → None)
    history_excl_today = kline_history[:-1]
    if len(history_excl_today) < high_window:
        high_60d = None
    else:
        high_60d = max(b["high"] for b in history_excl_today[-high_window:])

    # volume features(window=20)
    vol_feats = _compute_volume_features(kline_history, window=volume_window)

    # MA features(含 today,strict:history 不足 window → None)
    ma_features: dict = {}
    for w in ma_windows:
        if len(kline_history) < w:
            ma_features[f"ma_{w}"] = None
        else:
            sub = kline_history[-w:]
            ma_features[f"ma_{w}"] = sum(b["close"] for b in sub) / len(sub)

    return {
        "change_pct":   change_pct,
        "close":        today.get("close"),
        "high_60d":     high_60d,
        "vol_ratio":    vol_feats["vol_ratio"],
        "today_volume": vol_feats["today_volume"],
        "avg_volume":   vol_feats["avg_volume"],
        **ma_features,
    }


# ── MA 計分(W2.2.4,首次站上 +N)──────────────────────────────────────────

def _score_ma_features(
    kline_history: list[dict],
    features: dict,
    weights: dict,
) -> tuple[float, list[dict]]:
    """MA 計分:**首次站上 +N**(prev_above=False → today_above=True 才加分)。

    沒寫 standing_state row;從 kline_history[:-1] 重算 prev_above 判斷「首次」。
    比起套 W1.5 5 狀態狀態機,只需 boolean ABOVE/BELOW 兩態,實作簡潔。

    對每個 MA window(20 / 60 / 90):
      1. today_ma = features["ma_{w}"](可能 None,history < w)
      2. None → 跳過(history 不足,不算分)
      3. today_above = today.close >= today_ma
      4. prev_ma = SMA of kline_history[:-1] 的最後 w 根(history[:-1] 也要 ≥ w)
      5. prev_above = prev_close >= prev_ma(若 prev_ma 為 None,視 prev_above=False)
      6. (today_above AND NOT prev_above)→ 首次站上 → +N
      其他組合(維持站上 / 維持跌破 / 今天跌破)→ 0 分

    不發 tags、不發 events(規則 §4 沒 MA 標籤;每天獨立計分無「事件」概念)。
    """
    if not kline_history or len(kline_history) < 2:
        return 0.0, []

    today_close = kline_history[-1].get("close")
    prev_history = kline_history[:-1]
    prev_close = prev_history[-1].get("close")
    if today_close is None or prev_close is None:
        return 0.0, []

    total: float = 0.0
    details: list[dict] = []

    for w in MA_WINDOWS:
        today_ma = features.get(f"ma_{w}")
        if today_ma is None:
            continue   # 今天 MA 算不出來(history 不足)

        # strict `>`:業界對「站上均線」的標準理解 = 突破。
        # 貼線(close == ma)不算站上,要等到收盤明顯超過才觸發。
        today_above = today_close > today_ma

        # prev_ma:用 prev_history(也就是「昨天視角」)算
        if len(prev_history) < w:
            prev_above = False
        else:
            prev_ma = sum(b.get("close", 0) for b in prev_history[-w:]) / w
            prev_above = prev_close > prev_ma

        if today_above and not prev_above:
            base = weights["given_price"][f"ma_{w}"]
            total += base
            details.append({
                "module":   "ma",
                "reason":   f"首次站上 MA{w}(close={today_close}, ma_{w}={today_ma:.2f})",
                "score":    base,
                "evidence": {
                    "category":    f"ma_{w}",
                    "ma_value":    today_ma,
                    "today_close": today_close,
                },
            })

    return total, details


# ── 個股輪動標籤(W2.2.6,全市場跑完再算)────────────────────────────────

def _compute_rotation_tags(
    results: dict,
    watchlist: dict,
    conn_kline: sqlite3.Connection,
    date: str,
) -> dict[str, list[str]]:
    """對每個台股板塊,檢查「今日均分 - 過去 5 日均分 ≥ 2」→ 全族群 ⭐。

    Returns: dict[symbol, list[str]]
        例:{"TPEX:6223": ["⭐ 個股輪動(族群熱, Δ 2.5)"]}

    詮釋(per W2.2.6 review):
      - 「該族群」= 台股板塊(16 個)
      - 「今日均分」= 該族群所有今天有跑出 result 的成員 score 的算術平均
      - 「過去 5 日均分」= 過去最近 5 個有資料的交易日的「每日族群均分」再平均
                          (巢狀 GROUP BY date,停牌成員那天不會稀釋)
      - 「≥ 2」= inclusive(delta=2.0 觸發)
      - 「全族群 ⭐」= 該族群所有成員的 tags 都加(只加給今天有 result 的)
    """
    tags_by_symbol: dict[str, list[str]] = {}

    for sector_name, sector_data in watchlist.get("台股板塊", {}).items():
        members = [m["code"] for m in sector_data.get("成員", [])]
        if not members:
            continue

        # 今日均分(只算今天有 result 的成員)
        today_scores = [results[s]["score"] for s in members if s in results]
        if not today_scores:
            continue
        today_avg = sum(today_scores) / len(today_scores)

        # 過去 5 日均分(巢狀 GROUP BY date,strict 5 天才算)
        past_5_avg = score_history_io.compute_sector_avg_over_days(
            conn_kline, members, end_date_exclusive=date, n_days=5,
        )
        if past_5_avg is None:
            continue   # 歷史不足

        delta = today_avg - past_5_avg
        if delta >= 2:
            for member in members:
                if member in results:
                    tags_by_symbol.setdefault(member, []).append(
                        f"⭐ 個股輪動(族群熱, Δ {delta:.1f})"
                    )

    return tags_by_symbol


# ── 國際長子發動 cache(W2.2.3,全市場一次)────────────────────────────────

def _compute_intl_leader_activations(
    watchlist: dict,
    conn_kline: sqlite3.Connection,
    weights: dict,
    date: str,
) -> dict[str, list[str]]:
    """對 watchlist["國際族群"] 每個 group 算「哪些長子今天發動」。

    全市場一次性 cache,所有台股 lookup 時直接讀。

    Returns: dict[group_name, list[activated_leader_codes]]
      例:{"AI晶片_IP_代工龍頭": ["NASDAQ:NVDA"], "半導體設備_材料_封測": []}
    """
    activations: dict[str, list[str]] = {}
    for group_name, group_data in watchlist.get("國際族群", {}).items():
        activated: list[str] = []
        for leader_code in group_data.get("長子", []):
            history = load_kline_history(conn_kline, leader_code, date)
            if not history or history[-1].get("date") != date:
                continue   # 該長子今天沒交易資料(美股休市等),跳過
            features = _compute_kline_features(history)
            # 資料不足(無 change_pct 或 vol_ratio)→ 不算發動
            if features["change_pct"] is None or features["vol_ratio"] is None:
                continue
            kline_data = {
                "change_pct": features["change_pct"],
                "vol_ratio":  features["vol_ratio"],
                "close":      features["close"],
                "high_60d":   features["high_60d"] if features["high_60d"] is not None
                               else float("inf"),   # 沒 60 日歷史不能算突破
            }
            if sector_linkage.is_intl_leader_activated(kline_data, weights):
                activated.append(leader_code)
        activations[group_name] = activated
    return activations


# ── sector lookup(W2.2.3,個股維度)─────────────────────────────────────────

def _lookup_sector_data(
    symbol: str,
    watchlist: dict,
    sectors_data: dict,
    activations: dict[str, list[str]],
) -> dict:
    """給定台股 symbol,反查 sector_linkage.score 所需 dict。

    Returns: {sector, sector_level, intl_activated, intl_leaders_activated}
      若 symbol 無 TW 板塊歸屬 → sector=None,intl_activated=False
    """
    # 1. 找 TW 板塊
    tw_sector: str | None = None
    for sector_name, sector_data in watchlist.get("台股板塊", {}).items():
        for member in sector_data.get("成員", []):
            if member.get("code") == symbol:
                tw_sector = sector_name
                break
        if tw_sector:
            break

    if not tw_sector:
        return {"sector": None, "sector_level": None,
                "intl_activated": False, "intl_leaders_activated": []}

    # 2. 找出對應 TW 板塊的國際族群(可能多個)→ 聚合已發動長子
    activated_leaders: list[str] = []
    for group_name, group_data in watchlist.get("國際族群", {}).items():
        if tw_sector in group_data.get("對應台股族群", []):
            activated_leaders.extend(activations.get(group_name, []))

    # 3. 找 TW 板塊評級
    sector_level = sectors_data.get("sectors", {}).get(tw_sector)

    return {
        "sector":                  tw_sector,
        "sector_level":            sector_level,
        "intl_activated":          bool(activated_leaders),
        "intl_leaders_activated":  activated_leaders,
    }


# ── 單一 line / area 計分(含狀態機 + IO)─────────────────────────────────────

def _score_one_line(
    conn: sqlite3.Connection,
    symbol: str,
    line: dict,
    kline_history: list[dict],
    date: str,
    weights: dict,
    now_iso: str,
) -> tuple[float, list[dict], list[str], list[dict]]:
    """處理一條線:read_state → evaluate_standing → write_state → score
    + 站穩/跌破 標籤 + events。
    回傳 (score, details, tags, events)。
    """
    # ── 雙層分離(per W1.5 docstring)──────────────────────────────────────
    price_str   = line["price"]                # 字串穩定識別碼,寫入 standing_state.price_str
    given_price = float(price_str)             # float 給 standing 數學比較用
    category    = line["category"]

    # 1. read prev state(read_state 缺則回 None,evaluate_standing 視為 UNTRIGGERED)
    prev_state = state_io.read_state(conn, symbol, category, price_str)

    # 2. evaluate 狀態機
    new_state, should_score = standing.evaluate_standing(
        kline_history, given_price, prev_state, date,
    )

    # 3. 寫回 standing_state
    state_io.write_state(conn, symbol, category, price_str, new_state, now_iso)

    # 4. 計分(should_score=False 自動回 0)
    score, details = score_line(line, should_score, weights)
    for d in details:
        d["module"] = "given_price"   # 給 W2.4 篩選用

    # 5. 標籤(規則 v2.2 §4)
    #   tags       — 所有 active(STANDING + MAINTAINING + 跌破 event)→ 個股卡 / 完整資訊
    #   tags_today — 只今天新成立(Day 2 STANDING + 跌破 event)→ C 級分組用
    tags: list[str] = []
    tags_today: list[str] = []
    if new_state["state"] in (standing.STANDING, standing.MAINTAINING):
        tags.append(f"🟢 站穩 {price_str}")
    if new_state["state"] == standing.STANDING:
        tags_today.append(f"🟢 站穩 {price_str}")

    yesterday_k = kline_history[-2] if len(kline_history) >= 2 else None
    breakdown_triggered = standing.evaluate_breakdown(
        kline_history[-1], yesterday_k, given_price,
    )
    if breakdown_triggered:
        tags.append(f"🔴 跌破 {price_str}")
        tags_today.append(f"🔴 跌破 {price_str}")

    # 6. Events(給 W2.4 chart 標記用,僅事件發生當天記)
    events: list[dict] = []
    if new_state["state"] == standing.STANDING:
        events.append({"type": "standing", "price": price_str,
                       "category": category, "date": date})
    if breakdown_triggered:
        events.append({"type": "breakdown", "price": price_str,
                       "category": category, "date": date})

    return score, details, tags, tags_today, events


def _score_one_area(
    conn: sqlite3.Connection,
    symbol: str,
    area: dict,
    kline_history: list[dict],
    date: str,
    weights: dict,
    now_iso: str,
) -> tuple[float, list[dict], list[str], list[dict]]:
    """區域處理:price_str = f"{low}-{high}"、given_price = 中點。

    規則 v2.2 §1-D:K 棒「碰到」區域(K_low ≤ high AND K_high ≥ low)即觸發。
    狀態機跟線類共用,但 touch 預檢用 K 棒交集而非中點。
    若交集 → 把今天 K bar 「夾」進區域中點(low ≤ mid ≤ high),
    讓 standing.evaluate_standing 的 line-style touch 條件成立。
    """
    low_str     = area["low"]
    high_str    = area["high"]
    area_low    = float(low_str)
    area_high   = float(high_str)
    price_str   = f"{low_str}-{high_str}"
    given_price = (area_low + area_high) / 2.0
    category    = area["category"]

    prev_state = state_io.read_state(conn, symbol, category, price_str)

    # v2.2 §1-D 區域觸發:K 棒範圍 ∩ 區域範圍
    today_k = kline_history[-1]
    intersects = today_k["low"] <= area_high and today_k["high"] >= area_low

    if intersects:
        # 把今天 K bar low/high 撐開到涵蓋 midpoint,讓 line-style touch 條件成立
        adj_today = {
            **today_k,
            "low":  min(today_k["low"],  given_price),
            "high": max(today_k["high"], given_price),
        }
        # close 也需 ≥ midpoint 才能進入 line-style 站穩流程 — 用 close 跟 midpoint 取大
        if adj_today["close"] < given_price:
            adj_today["close"] = given_price
        history_adj = kline_history[:-1] + [adj_today]
    else:
        history_adj = kline_history

    new_state, should_score = standing.evaluate_standing(
        history_adj, given_price, prev_state, date,
    )

    state_io.write_state(conn, symbol, category, price_str, new_state, now_iso)

    score, details = score_area(area, should_score, weights)
    for d in details:
        d["module"] = "given_price"

    tags: list[str] = []
    tags_today: list[str] = []
    if new_state["state"] in (standing.STANDING, standing.MAINTAINING):
        tags.append(f"🟢 站穩 區域 {price_str}")
    if new_state["state"] == standing.STANDING:
        tags_today.append(f"🟢 站穩 區域 {price_str}")

    yesterday_k = kline_history[-2] if len(kline_history) >= 2 else None
    breakdown_triggered = standing.evaluate_breakdown(
        kline_history[-1], yesterday_k, given_price,
    )
    if breakdown_triggered:
        tags.append(f"🔴 跌破 區域 {price_str}")
        tags_today.append(f"🔴 跌破 區域 {price_str}")

    events: list[dict] = []
    if new_state["state"] == standing.STANDING:
        events.append({"type": "standing", "price": price_str,
                       "category": category, "date": date})
    if breakdown_triggered:
        events.append({"type": "breakdown", "price": price_str,
                       "category": category, "date": date})

    return score, details, tags, tags_today, events


# ── 單檔個股計分 ──────────────────────────────────────────────────────────────

def score_one_symbol(
    conn_kline:       sqlite3.Connection,
    conn_etf:         sqlite3.Connection | None,
    symbol:           str,
    date:             str,
    weights:          dict,
    sectors:          dict,
    key_prices:       dict,
    watchlist:        dict,
    now_iso:          str,
    intl_activations: dict[str, list[str]] | None = None,
) -> dict | None:
    """對單一個股算當日結果。回傳 filtered_result_v2 stocks dict 一個 entry,
    或 None(該檔當日無 K 線資料)。
    """
    kline_history = load_kline_history(conn_kline, symbol, date)
    if not kline_history:
        return None
    if kline_history[-1]["date"] != date:
        # 該 symbol 在 date 沒交易資料(可能停牌、新代號未補抓)
        return None

    total_score: float = 0.0
    all_details: list[dict] = []
    all_tags:    list[str]  = []
    all_events:  list[dict] = []

    # ── chip_etf(W2.2.1 已接入)──────────────────────────────────────────
    if conn_etf is not None:
        today_volume = kline_history[-1].get("volume")
        etf_data = etf_io.compute_etf_features(conn_etf, symbol, date, today_volume)
        s, d = chip_etf.score(symbol, date, etf_data, weights)
        for det in d:
            det["module"] = "chip_etf"
        total_score += s
        all_details.extend(d)

    # ── 統一 K 線特徵(W2.2.3 / W2.2.4 共用)────────────────────────────────
    kline_features = _compute_kline_features(kline_history)

    # ── volume(W2.2.2 已接入)─────────────────────────────────────────────
    if kline_features["vol_ratio"] is not None:
        volume_data = {
            "vol_ratio":    kline_features["vol_ratio"],
            "today_volume": kline_features["today_volume"],
            "avg_volume":   kline_features["avg_volume"],
        }
        s, d = volume.score(symbol, date, volume_data, weights)
        for det in d:
            det["module"] = "volume"
        total_score += s
        all_details.extend(d)

    # ── sector_linkage(W2.2.3 已接入)─────────────────────────────────────
    if intl_activations is not None:
        sector_data = _lookup_sector_data(symbol, watchlist, sectors, intl_activations)
        s, d = sector_linkage.score(symbol, date, sector_data, weights)
        for det in d:
            det["module"] = "sector_linkage"
        total_score += s
        all_details.extend(d)

    # ── MA(W2.2.4 已接入,首次站上 +N)────────────────────────────────────
    s, d = _score_ma_features(kline_history, kline_features, weights)
    total_score += s
    all_details.extend(d)

    # ── MACD 動能轉換(W2.2.7,動能轉多 +1 / 轉空純標籤)─────────────────
    macd_tags, macd_score, macd_details = _compute_macd(
        symbol, date, kline_history, weights,
    )
    total_score += macd_score
    all_details.extend(macd_details)
    all_tags.extend(macd_tags)

    # ── ETF 減碼純標籤(2026-05-29 朋友 review 後新增,不計分)──────────
    decrease_tags = _compute_etf_decrease_tag(symbol, date, conn_etf)
    all_tags.extend(decrease_tags)

    # ── W2.1 主軸:給定價格 ────────────────────────────────────────────────
    stock_kp = key_prices.get("stocks", {}).get(symbol, {})
    lines    = stock_kp.get("lines", [])
    areas    = stock_kp.get("areas", [])

    all_tags_today: list[str] = []

    for line in lines:
        s, d, t, t2, e = _score_one_line(
            conn_kline, symbol, line, kline_history, date, weights, now_iso,
        )
        total_score += s; all_details.extend(d); all_tags.extend(t)
        all_tags_today.extend(t2); all_events.extend(e)

    for area in areas:
        s, d, t, t2, e = _score_one_area(
            conn_kline, symbol, area, kline_history, date, weights, now_iso,
        )
        total_score += s; all_details.extend(d); all_tags.extend(t)
        all_tags_today.extend(t2); all_events.extend(e)

    grade_letter = grader.grade(total_score, weights["grade"])
    name, sector_name = _lookup_stock_meta(symbol, watchlist)

    return {
        "name":                name,
        "sector":              sector_name,
        "score":               round(total_score, 4),
        "grade":               grade_letter,
        "tags":                all_tags,
        "tags_today":          all_tags_today,   # v2.2 §4 C 級分組用(只今天新成立)
        "details":             all_details,
        "key_prices_snapshot": {"lines": lines, "areas": areas},
        "events":              all_events,
    }


# ── 主流程(可測試)────────────────────────────────────────────────────────

def run_pipeline(
    *,
    date:       str,
    conn_kline: sqlite3.Connection,
    conn_etf:   sqlite3.Connection | None,
    weights:    dict,
    sectors:    dict,
    key_prices: dict,
    watchlist:  dict,
    now_iso:    str,
    restrict_symbols: set[str] | None = None,
) -> dict:
    """純流程:接 connections + config dicts,回傳輸出 dict。

    不寫 JSON、不 commit、不 close connections。
    main() 或測試 caller 負責 IO 收尾。

    restrict_symbols(增量模式):
        若給,只對這些 symbols 跑 score_one_symbol + 寫 standing_state /
        score_history。既有 watchlist 其他 symbols 完全不讀不寫。
        典型用途:add_symbols_batch 補新檔歷史 state(N 個新檔 × 121 天)
        而不重跑整批 watchlist。

        在 restrict 模式下也會跳過 rotation_tags 跟 ETF active(全市場計算,
        在 partial set 下意義不大且浪費時間;當天計分要正確仍由 caller 之後
        跑一次「全量 today」)。
    """
    state_io.init_schema(conn_kline)
    score_history_io.init_schema(conn_kline)

    # ── ETF metadata(W2.2.1 接入)───────────────────────────────────────────
    if conn_etf is not None:
        etf_max_date = etf_io.compute_etf_max_date(conn_etf)
        if etf_max_date is None:
            etf_delayed = None   # operations 表空
        else:
            etf_delayed = (etf_max_date != date)
    else:
        etf_max_date = None
        etf_delayed  = None      # 沒接 ETF DB(test 路徑)

    # ── kline metadata(W2.2.2 接入)─────────────────────────────────────────
    data_date_in_db = kline_io.compute_kline_max_date(conn_kline)

    # ── 國際長子發動 cache(W2.2.3,全市場一次)──────────────────────────────
    intl_activations = _compute_intl_leader_activations(
        watchlist, conn_kline, weights, date,
    )

    results:        dict       = {}
    skipped:        list[str]  = []

    # 增量模式:filter target list
    target_iter = iter_tw_symbols(watchlist)
    if restrict_symbols is not None:
        target_iter = (s for s in target_iter if s in restrict_symbols)

    for symbol in target_iter:
        entry = score_one_symbol(
            conn_kline, conn_etf, symbol, date,
            weights, sectors, key_prices, watchlist, now_iso,
            intl_activations=intl_activations,
        )
        if entry is None:
            skipped.append(symbol)
        else:
            results[symbol] = entry

    # ── 個股輪動標籤(W2.2.6,全部跑完才能算族群均分)─────────────────────
    # 增量模式跳過:partial set 算不出有意義的族群均分,且當天計分要正確
    # 由 caller 之後跑一次「全量 today」處理。
    if restrict_symbols is None:
        rotation_tags_by_symbol = _compute_rotation_tags(
            results, watchlist, conn_kline, date,
        )
        for symbol, tags in rotation_tags_by_symbol.items():
            if symbol in results:
                results[symbol]["tags"].extend(tags)

    # ── 寫今日 score 到 score_history(W2.2.6,給隔天 rotation 用)──────────
    # write_batch 是 UPSERT,增量模式只更新 restrict_symbols 的 row,
    # 既有 symbols 的 score_history 不動。
    score_history_io.write_batch(conn_kline, date, results, now_iso)

    # ── ETF 主動式雙向掃描(W3 區塊 6 資料源)─────────────────────────────
    # 增量模式跳過(全市場 7 日累計,partial 沒意義)
    if restrict_symbols is None:
        etf_active = (etf_io.fetch_etf_active_summary(conn_etf, date, watchlist)
                      if conn_etf is not None
                      else {"increase": [], "decrease": []})
    else:
        etf_active = {"increase": [], "decrease": []}

    return {
        "date":    date,
        "version": "2.1",
        "metadata": {
            "etf_delayed":         etf_delayed,
            "etf_max_date_in_db":  etf_max_date,
            "data_date_in_db":     data_date_in_db,
            "generated_at":        now_iso,
            "version":             "2.1",
            "skipped_symbols":     skipped,
            "incremental":         restrict_symbols is not None,
        },
        "stocks":     results,
        "etf_active": etf_active,
    }


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

def _parse_incremental_args(args) -> set[str] | None:
    """從 args.incremental / args.new_symbols 算出 restrict_symbols。

    - 兩 flag 都缺 → None(全量模式)
    - 兩 flag 都給 → set of symbols
    - 只給一個 → raise SystemExit(2)(argparse style)
    """
    has_inc  = getattr(args, "incremental", False)
    new_syms = getattr(args, "new_symbols", None)
    if has_inc and not new_syms:
        raise SystemExit(
            "❌ --incremental 必須搭配 --new-symbols TWSE:XXXX,TPEX:YYYY,..."
        )
    if new_syms and not has_inc:
        raise SystemExit(
            "❌ --new-symbols 必須搭配 --incremental(增量模式)"
        )
    if not has_inc:
        return None
    return {s.strip() for s in new_syms.split(",") if s.strip()}


def main(args) -> None:
    weights, sectors, key_prices, watchlist = load_configs(
        args.weights, args.sectors, args.key_prices, args.watchlist,
    )

    restrict_symbols = _parse_incremental_args(args)

    conn_kline = sqlite3.connect(args.kline)
    conn_etf   = sqlite3.connect(args.etf) if os.path.exists(args.etf) else None
    now_iso    = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    try:
        output = run_pipeline(
            date       = args.date,
            conn_kline = conn_kline,
            conn_etf   = conn_etf,
            weights    = weights,
            sectors    = sectors,
            key_prices = key_prices,
            watchlist  = watchlist,
            now_iso    = now_iso,
            restrict_symbols = restrict_symbols,
        )
        conn_kline.commit()
    finally:
        conn_kline.close()
        if conn_etf is not None:
            conn_etf.close()

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    scored  = len(output["stocks"])
    skipped = len(output["metadata"]["skipped_symbols"])
    mode_tag = " (incremental)" if restrict_symbols else ""

    # 規模略過閘(2026-07-04,停更 19 天事故):全量模式下略過 > 20% 代表資料層出事
    # (6/14:97/98 略過卻標 ✅ 照發,是第一張骨牌)→ 整步 fail、非 0 退出,擋下 publish。
    # 增量模式本來就只跑少數指定 symbol,不套此閘。
    if restrict_symbols is None:
        total = scored + skipped
        ratio = (skipped / total) if total else 0.0
        if ratio > 0.20:
            print(f"❌ {scored} symbols / {skipped} skipped(略過 {ratio:.0%} > 20% 上限)"
                  f" → 資料層疑似異常,整步標 fail、不發布 → {args.output}{mode_tag}",
                  file=sys.stderr)
            sys.exit(1)

    print(f"✅ {scored} symbols / {skipped} skipped → {args.output}{mode_tag}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 8 純加分制主幹(W2.1)")
    parser.add_argument("--date",       required=True)
    parser.add_argument("--kline",      default="kline.db")
    parser.add_argument("--etf",        default=os.path.expanduser("~/ETF追蹤/etf_operations.db"))
    parser.add_argument("--output",     default="filtered_result_v2.json")
    parser.add_argument("--weights",    default="config/weights.json")
    parser.add_argument("--sectors",    default="config/sectors.json")
    parser.add_argument("--key-prices", dest="key_prices", default="config/key_prices.json")
    parser.add_argument("--watchlist",  default="config/watchlist.json")
    parser.add_argument("--incremental", action="store_true",
                         help="增量模式:只處理 --new-symbols 列出的個股,既有不動")
    parser.add_argument("--new-symbols", dest="new_symbols", default=None,
                         help="增量模式下的新個股 list,逗號分隔(例:TWSE:2454,TPEX:6223)")
    main(parser.parse_args())
