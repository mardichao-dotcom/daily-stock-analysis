"""
score.py — 積分計算（依積分制定案 v1 第 1、2 節）

ENABLE_KEY_PRICE = False  # 第一版關閉，待關鍵價清單交付後改 True

score(d) → (total_score: int, breakdown: list of {項目, 分數})
"""
from .load_config import get_sector_of, get_leaders_of

ENABLE_KEY_PRICE = False  # 1-D / 2-D 開關


def score(d):
    """
    d: full stock data dict after all filter stages.
    Returns (total, breakdown).
    """
    breakdown = []
    total = 0

    def add(項目, 分數):
        nonlocal total
        total += 分數
        breakdown.append({"項目": 項目, "分數": 分數})

    # ── 1-A 籌碼加分 ──────────────────────────────────────────────────────────
    buy_count = d.get("etf_consensus_buy_count", 0)
    if buy_count >= 4:
        add("ETF共識加碼（強，≥4檔）", 3)
    elif buy_count >= 2:
        add("ETF共識加碼（≥2檔）", 2)

    if d.get("is_continuous_buy"):
        add("ETF連續加碼（雙軌）", 1)

    if d.get("is_abnormal_ignition"):
        add("ETF異常點火", 1)

    # ── 1-B 族群加分 ──────────────────────────────────────────────────────────
    sector = get_sector_of(d["symbol"])
    leaders = get_leaders_of(sector) if sector else []
    is_leader = d["symbol"] in leaders

    from .filter_stage2 import _is_activated

    if is_leader and _is_activated(d):
        add("自身是族群長子且發動", 3)
    elif d.get("sector_activated") and d.get("is_pickup_candidate"):
        add("族群已啟動，自身為撿漏候選", 2)
    elif d.get("is_lone_wolf"):
        add("強單兵作戰", 1)

    # ── 1-C 技術加分 ──────────────────────────────────────────────────────────
    chg = d.get("change_pct", 0)
    vol_ratio = d.get("vol_ratio", 0)
    body_ratio = d.get("body_ratio", 0)

    if d.get("is_limit_up"):
        add("漲停", 2)
    elif chg > 5:
        add("大漲（>5%）", 1)

    if d.get("break_60d_high"):
        add("突破60日高", 2)

    if d.get("is_gap_up"):
        add("跳空開高", 1)

    if vol_ratio > 2:
        add("大爆量（>2x）", 2)
    elif vol_ratio > 1.5:
        add("爆量（>1.5x）", 1)

    close = d.get("close", 0)
    open_ = d.get("open", 0)
    if close > open_ and body_ratio > 0.6:
        add("強紅K（實體比>60%，收紅）", 1)

    # ── 1-D 關鍵價加分（⏸️ 不啟用）────────────────────────────────────────────
    if ENABLE_KEY_PRICE:
        key_price_state = d.get("key_price_state")
        if key_price_state == "新突破":
            add("突破關鍵價（新突破）", 3)
        elif key_price_state == "站穩":
            add("站穩關鍵價", 1)

    # ── 1-E 國際連動加分 ──────────────────────────────────────────────────────
    if d.get("global_sync"):
        add("國際同步發動", 1)

    # ── 2-A 籌碼風險 ──────────────────────────────────────────────────────────
    sell_count = d.get("etf_consensus_sell_count", 0)
    divergence = d.get("manager_divergence", False)
    net_sign = d.get("divergence_net_sign", 0)

    if sell_count >= 2:
        add("ETF共識減碼（≥2檔）", -3)
    elif divergence:
        if net_sign < 0:
            add("ETF經理人分歧（淨流出）", -2)
        else:
            add("ETF經理人分歧（淨流入）", -1)

    # ── 2-B 技術風險 ──────────────────────────────────────────────────────────
    k_pat = d.get("k_pattern", "")

    if k_pat == "跌停":
        add("跌停", -3)
    elif k_pat == "實體長黑":
        add("實體長黑", -3)
    elif k_pat == "長上影線":
        add("長上影線", -2)
    elif chg < -5:
        add("大跌（>5%）", -2)

    if vol_ratio < 0.5:
        add("量縮（<0.5x）", -1)

    # ── 2-C 族群風險 ──────────────────────────────────────────────────────────
    # leader_crashed: skip when sector IS activated (strong leader offsets weak one)
    if d.get("leader_crashed") and not d.get("sector_activated"):
        add("族群長子大跌（>3%）", -2)

    # multi_leader_divergence: only for leader stocks (non-leaders already penalized
    # or benefited by the sector signal; the divergence tag is their own context)
    if d.get("multi_leader_divergence") and is_leader:
        add("多長子背離", -1)

    # ── 2-D 關鍵價風險（⏸️ 不啟用）────────────────────────────────────────────
    if ENABLE_KEY_PRICE:
        if d.get("key_price_state") == "跌破":
            add("跌破關鍵價（假突破/破線）", -3)

    # ── 2-E 國際風險 ──────────────────────────────────────────────────────────
    if d.get("global_crash"):
        add("國際對應族群大跌（>3%）", -1)

    # ── 2-F 法說會風險（⏸️ 不啟用）────────────────────────────────────────────
    # add("7日內法說會", -1)  # disabled

    return total, breakdown


def score_all(tw_data):
    """Adds score + score_breakdown to each entry. Returns tw_data."""
    for symbol, d in tw_data.items():
        total, breakdown = score(d)
        d["score"] = total
        d["score_breakdown"] = breakdown
    return tw_data
