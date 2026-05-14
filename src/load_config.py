"""
load_config.py — reads config/watchlist.json and provides query helpers.
All symbol lookups go through here; no symbol codes are hard-coded elsewhere.

watchlist.json v2: 成員 is now [{code, name}, ...]; 長子 stays as [code, ...].
"""
import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "watchlist.json")

_config = None


def _load():
    global _config
    if _config is None:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _config = json.load(f)
    return _config


# ── public API ────────────────────────────────────────────────────────────────

def get_all_tw_symbols():
    cfg = _load()
    result = []
    for sector_data in cfg["台股板塊"].values():
        result.extend(m["code"] for m in sector_data["成員"])
    return result


def get_all_global_symbols():
    cfg = _load()
    result = []
    for sector_data in cfg["國際族群"].values():
        result.extend(m["code"] for m in sector_data["成員"])
    return result


def get_tw_sectors():
    """Return dict: sector_name → {成員: [{code,name},...], 長子: [code,...]}"""
    return _load()["台股板塊"]


def get_global_sectors():
    """Return dict: sector_name → {成員, 長子, 對應台股族群}"""
    return _load()["國際族群"]


def get_sector_of(symbol):
    """台股板塊 name for a given symbol code, or None if not found."""
    cfg = _load()
    for name, data in cfg["台股板塊"].items():
        if any(m["code"] == symbol for m in data["成員"]):
            return name
    return None


def get_leaders_of(tw_sector):
    """List of leader codes for a Taiwan sector."""
    cfg = _load()
    return cfg["台股板塊"].get(tw_sector, {}).get("長子", [])


def is_leader(symbol):
    cfg = _load()
    for data in cfg["台股板塊"].values():
        if symbol in data["長子"]:
            return True
    return False


def get_global_mapping(tw_sector):
    """
    Returns list of dicts: [{name, leaders, members}]
    for every international sector whose 對應台股族群 includes tw_sector.
    members is a list of code strings.
    """
    cfg = _load()
    result = []
    for g_name, g_data in cfg["國際族群"].items():
        if tw_sector in g_data.get("對應台股族群", []):
            result.append({
                "name": g_name,
                "leaders": g_data["長子"],
                "members": [m["code"] for m in g_data["成員"]],
            })
    return result


def get_sector_members(tw_sector):
    """Return list of code strings for all members of a TW sector."""
    cfg = _load()
    return [m["code"] for m in cfg["台股板塊"].get(tw_sector, {}).get("成員", [])]


def get_name(symbol):
    """Return Chinese/English name for a symbol. Falls back to code suffix."""
    cfg = _load()
    for sector_data in cfg["台股板塊"].values():
        for m in sector_data["成員"]:
            if m["code"] == symbol:
                return m["name"]
    for sector_data in cfg["國際族群"].values():
        for m in sector_data["成員"]:
            if m["code"] == symbol:
                return m["name"]
    return symbol.split(":")[-1]


def symbol_to_code(symbol):
    """Strip exchange prefix: 'TWSE:2368' → '2368'"""
    return symbol.split(":")[-1]
