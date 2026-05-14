"""
filter_stage2.py — 族群長子撿漏（第二階）
Annotates each TW stock with sector-related flags:
  sector_activated, is_pickup_candidate, is_lone_wolf,
  multi_leader_divergence, leader_crashed, leader_max_change_pct
"""
from .load_config import get_tw_sectors, get_leaders_of, get_sector_of


def _is_activated(d):
    """
    True if the stock's data dict satisfies any leader activation condition.
    發動條件: 漲停 / (量比>1.5x AND 漲幅>3%) / 跳空開高 / ETF異常加碼
    量豁免: 漲幅>7% 時，量比未達1.5x 仍算爆量發動
    """
    if d.get("is_limit_up"):
        return True
    chg = d.get("change_pct", 0)
    vol = d.get("vol_ratio", 0)
    if chg > 7:                          # volume exemption
        return True
    if vol > 1.5 and chg > 3:
        return True
    if d.get("is_gap_up"):
        return True
    if d.get("is_abnormal_ignition"):
        return True
    return False


def run(tw_data):
    """
    tw_data: {symbol: data_dict} (after stage1)
    Adds sector-flag fields to each entry in-place.
    """
    sectors = get_tw_sectors()

    # ── Step 1: determine per-sector activation and leader states ─────────────
    sector_activated = {}          # sector_name → bool
    sector_leader_changes = {}     # sector_name → [change_pct of leaders]
    sector_leader_down = {}        # sector_name → bool (any leader < 0)

    for sector_name, sector_data in sectors.items():
        leaders = sector_data["長子"]
        activated = False
        leader_changes = []
        any_leader_down = False

        for sym in leaders:
            d = tw_data.get(sym)
            if d is None:
                continue
            chg = d.get("change_pct", 0)
            leader_changes.append(chg)
            if chg < 0:
                any_leader_down = True
            if _is_activated(d):
                activated = True

        # True divergence: one leader up, one leader down
        has_up = any(c >= 0 for c in leader_changes)
        has_down = any(c < 0 for c in leader_changes)
        true_divergence = has_up and has_down and len(leaders) >= 2

        sector_activated[sector_name] = activated
        sector_leader_changes[sector_name] = leader_changes
        sector_leader_down[sector_name] = true_divergence

    # ── Step 2: annotate each stock ───────────────────────────────────────────
    for symbol, d in tw_data.items():
        sector = get_sector_of(symbol)
        if sector is None:
            d["sector_activated"] = False
            d["is_pickup_candidate"] = False
            d["is_lone_wolf"] = False
            d["multi_leader_divergence"] = False
            d["leader_crashed"] = False
            d["leader_max_change_pct"] = None
            continue

        activated = sector_activated.get(sector, False)
        leaders = get_leaders_of(sector)
        leader_changes = sector_leader_changes.get(sector, [])

        # max leader change pct (for pickup candidate threshold)
        max_leader_chg = max(leader_changes) if leader_changes else 0
        d["leader_max_change_pct"] = max_leader_chg

        d["sector_activated"] = activated

        # multi_leader_divergence: sector has ≥2 leaders, one going up and one going down
        d["multi_leader_divergence"] = sector_leader_down.get(sector, False)

        # leader_crashed: own sector's leader fell > 3%
        d["leader_crashed"] = any(chg < -3 for chg in leader_changes)

        self_chg = d.get("change_pct", 0)
        is_self_leader = symbol in leaders

        if activated and not is_self_leader:
            # pickup candidate: sector activated, own change < leader / 2
            d["is_pickup_candidate"] = self_chg < max_leader_chg / 2
        else:
            d["is_pickup_candidate"] = False

        # lone wolf: sector NOT activated, but self is strong
        if not activated and _is_activated(d):
            d["is_lone_wolf"] = True
        else:
            d["is_lone_wolf"] = False

    return tw_data
