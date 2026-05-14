"""
filter_stage4.py — 國際領先指標（第四階）
Maps international leader signals to Taiwan stocks via watchlist.json.
Annotates each TW stock with:
  global_sync    (True/False) — 對應國際長子同步發動
  global_crash   (True/False) — 對應國際長子大跌
  global_detail  (list of dicts) — which global leaders triggered what
"""
from .load_config import get_sector_of, get_global_mapping


def _global_leader_activated(d):
    """
    International leader activation: 漲>3% OR 突破60日高
    """
    chg = d.get("change_pct", 0)
    if chg > 3:
        return True
    if d.get("break_60d_high"):
        return True
    return False


def _global_leader_crashed(d):
    """International leader crash: 跌>3%"""
    return d.get("change_pct", 0) < -3


def build_global_signals(global_data):
    """
    Pre-compute per-international-sector activation/crash state.
    Returns: {global_sector_name: {activated, crashed, leader_changes}}
    """
    from .load_config import get_global_sectors
    sectors = get_global_sectors()
    result = {}

    for g_name, g_data in sectors.items():
        leaders = g_data["長子"]
        any_activated = False
        any_crashed = False
        leader_changes = []

        for sym in leaders:
            d = global_data.get(sym)
            if d is None:
                continue
            chg = d.get("change_pct", 0)
            leader_changes.append((sym, chg))
            if _global_leader_activated(d):
                any_activated = True
            if _global_leader_crashed(d):
                any_crashed = True

        result[g_name] = {
            "activated": any_activated,
            "crashed": any_crashed,
            "leader_changes": leader_changes,
        }

    return result


def run(tw_data, global_data):
    """
    tw_data: {symbol: data_dict} (after stage1 + stage2)
    global_data: {symbol: data_dict} for international stocks
    Adds global signal fields to each TW stock in-place.
    """
    global_signals = build_global_signals(global_data)

    for symbol, d in tw_data.items():
        sector = get_sector_of(symbol)
        g_mappings = get_global_mapping(sector) if sector else []

        any_sync = False
        any_crash = False
        detail = []

        for mapping in g_mappings:
            g_name = mapping["name"]
            sig = global_signals.get(g_name, {})
            if sig.get("activated"):
                any_sync = True
                detail.append({"sector": g_name, "signal": "sync",
                                "leaders": sig["leader_changes"]})
            if sig.get("crashed"):
                any_crash = True
                detail.append({"sector": g_name, "signal": "crash",
                                "leaders": sig["leader_changes"]})

        d["global_sync"] = any_sync
        d["global_crash"] = any_crash
        d["global_detail"] = detail

    return tw_data
