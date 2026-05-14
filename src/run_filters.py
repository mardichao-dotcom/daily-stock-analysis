"""
run_filters.py — 總調度

Usage:
    python3 src/run_filters.py [--date YYYY-MM-DD] [--kline PATH] [--etf PATH]

Defaults (for testing with 5/13 data):
    --date  2026-05-13
    --kline test/test_kline.db
    --etf   test/test_etf_operations.db

Production:
    --kline kline.db  --etf ~/ETF追蹤/etf_operations.db
"""
import argparse
import json
import os
import sys
from datetime import datetime, date

# Allow running as `python3 src/run_filters.py` from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.load_config import (
    get_all_tw_symbols, get_all_global_symbols,
    get_tw_sectors, get_sector_of, symbol_to_code
)
from src.load_data import load_all
from src.filter_stage1 import run as stage1
from src.filter_stage2 import run as stage2
from src.filter_stage4 import run as stage4
from src.score import score_all
from src.classify import classify_all

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
STATE_FILE = os.path.join(PROJECT_ROOT, "state", "signal_state.json")
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "output", "filtered_result.json")


# ── state management ──────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"最後更新": "", "訊號追蹤": {}, "關鍵價狀態": {}}


def update_state(state, tw_data, data_date):
    tracking = state.get("訊號追蹤", {})
    active_sa = {sym for sym, d in tw_data.items() if d.get("grade") in ("S級", "A級")}

    # Remove stocks that fell out of S/A
    for sym in list(tracking.keys()):
        if sym not in active_sa:
            del tracking[sym]

    # Add new or keep existing
    for sym in active_sa:
        if sym not in tracking:
            tracking[sym] = {
                "首次發出日": data_date,
                "目前分級": tw_data[sym]["grade"]
            }
        else:
            tracking[sym]["目前分級"] = tw_data[sym]["grade"]

    state["訊號追蹤"] = tracking
    state["最後更新"] = data_date
    return state


def compute_tn(symbol, data_date, state):
    tracking = state.get("訊號追蹤", {})
    if symbol not in tracking:
        return "T+0"
    first = tracking[symbol]["首次發出日"]
    try:
        delta = datetime.strptime(data_date, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")
        return f"T+{delta.days}"
    except Exception:
        return "T+0"


# ── output builder ────────────────────────────────────────────────────────────

def build_output(tw_data, global_data, data_date, state):
    tw_sectors = get_tw_sectors()

    # Activated sectors: any leader in sector triggered activation
    from src.filter_stage2 import _is_activated
    activated_sectors = []
    for sname, sdata in tw_sectors.items():
        for sym in sdata["長子"]:
            d = tw_data.get(sym)
            if d and _is_activated(d):
                activated_sectors.append(sname)
                break

    grade_groups = {"S級": [], "A級": [], "中性": [], "警報": [], "黑名單": []}
    individual = {}

    for symbol, d in tw_data.items():
        grade = d.get("grade", "中性")
        grade_groups.setdefault(grade, []).append(symbol)

        tn = compute_tn(symbol, data_date, state)

        individual[symbol] = {
            "板塊": get_sector_of(symbol) or "",
            "score": d.get("score", 0),
            "score_breakdown": d.get("score_breakdown", []),
            "grade": grade,
            "tags": d.get("tags", []),
            "價量": {
                "close": d.get("close"),
                "change_pct": round(d.get("change_pct", 0), 2),
                "vol_ratio": round(d.get("vol_ratio", 0), 2),
                "k_pattern": d.get("k_pattern", ""),
                "break_60d_high": d.get("break_60d_high", False),
                "is_gap_up": d.get("is_gap_up", False),
            },
            "籌碼": {
                "etf_consensus_buy_count": d.get("etf_consensus_buy_count", 0),
                "etf_consensus_sell_count": d.get("etf_consensus_sell_count", 0),
                "is_continuous_buy": d.get("is_continuous_buy", False),
            },
            "T+N": tn,
        }

    return {
        "資料日期": data_date,
        "產出時間": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "籌碼基準": f"ETF Edge 近7日累計（至 {data_date[5:]}）",
        "個股結果": individual,
        "分級彙整": grade_groups,
        "啟動族群": activated_sectors,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-05-13")
    parser.add_argument("--kline", default=os.path.join(PROJECT_ROOT, "test", "test_kline.db"))
    parser.add_argument("--etf", default=os.path.join(PROJECT_ROOT, "test", "test_etf_operations.db"))
    args = parser.parse_args()

    data_date = args.date
    print(f"[run_filters] date={data_date}  kline={args.kline}")

    tw_symbols = get_all_tw_symbols()
    global_symbols = get_all_global_symbols()

    print(f"[load_data] {len(tw_symbols)} TW + {len(global_symbols)} global symbols")
    tw_data, global_data = load_all(data_date, args.kline, args.etf, tw_symbols, global_symbols)
    print(f"[load_data] loaded {len(tw_data)} TW, {len(global_data)} global")

    stage1(tw_data)
    stage2(tw_data)
    stage4(tw_data, global_data)
    score_all(tw_data)
    classify_all(tw_data)

    state = load_state()
    update_state(state, tw_data, data_date)

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    output = build_output(tw_data, global_data, data_date, state)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Summary print
    summary = output["分級彙整"]
    print(f"\n=== 分級彙整 ({data_date}) ===")
    for grade in ("S級", "A級", "中性", "警報", "黑名單"):
        syms = summary.get(grade, [])
        if syms:
            print(f"  {grade}: {syms}")
    print(f"\n啟動族群: {output['啟動族群']}")
    print(f"\n[output] {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
