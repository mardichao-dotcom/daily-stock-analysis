"""
render_watchlist.py — 讀 config/watchlist.json，渲染 docs/watchlist.html
"""
import json
import os

from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _short_code(full_code: str) -> str:
    """'TWSE:2330' → '2330', 'NASDAQ:NVDA' → 'NVDA'"""
    return full_code.split(":")[-1]


def _build_sector(name: str, raw: dict) -> dict:
    leaders_set = set(raw.get("長子", []))
    members = [
        {
            "code": _short_code(m["code"]),
            "name": m["name"],
            "is_leader": m["code"] in leaders_set,
        }
        for m in raw["成員"]
    ]
    leaders = [
        {"code": _short_code(c), "name": next(m["name"] for m in raw["成員"] if m["code"] == c)}
        for c in raw.get("長子", [])
    ]
    return {"name": name, "members": members, "leaders": leaders}


def _build_intl_group(name: str, raw: dict) -> dict:
    leaders_set = set(raw.get("長子", []))
    members = [
        {
            "code": _short_code(m["code"]),
            "name": m["name"],
            "is_leader": m["code"] in leaders_set,
        }
        for m in raw["成員"]
    ]
    leaders = [
        {"code": _short_code(c), "name": next(m["name"] for m in raw["成員"] if m["code"] == c)}
        for c in raw.get("長子", [])
    ]
    return {
        "name": name,
        "members": members,
        "leaders": leaders,
        "corresponding_tw": raw.get("對應台股族群", []),
    }


def main():
    watchlist_path = os.path.join(PROJECT_ROOT, "config", "watchlist.json")
    out_path = os.path.join(PROJECT_ROOT, "docs", "watchlist.html")

    with open(watchlist_path, encoding="utf-8") as f:
        wl = json.load(f)

    tw_raw = wl.get("台股板塊", {})
    intl_raw = wl.get("國際族群", {})

    tw_sectors = [_build_sector(name, raw) for name, raw in tw_raw.items()]
    intl_groups = [_build_intl_group(name, raw) for name, raw in intl_raw.items()]

    tw_stock_count = sum(len(s["members"]) for s in tw_sectors)
    intl_stock_count = sum(len(g["members"]) for g in intl_groups)

    templates_dir = os.path.join(PROJECT_ROOT, "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=False)
    tmpl = env.get_template("watchlist.html.j2")

    ctx = {
        "tw_sectors": tw_sectors,
        "intl_groups": intl_groups,
        "tw_sector_count": len(tw_sectors),
        "tw_stock_count": tw_stock_count,
        "intl_group_count": len(intl_groups),
        "intl_stock_count": intl_stock_count,
        "update_date": wl.get("更新日期", ""),
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(tmpl.render(**ctx))
    print(f"[OK] {out_path}")


if __name__ == "__main__":
    main()
