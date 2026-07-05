"""
parallel_compare.py — Mac 版 vs Actions 版早報並行比對(stage9 §4)

切換退役前置:Mac 版 macro 穩定運行 5 個交易日,與 Actions 版(investment-summary repo)
同日比對指數/融資數值。比對標準(§4.3):
  - 允許不同資料源造成的小數差
  - 單一欄位 |差異| > 0.5% → 標記需查
  - 5 個交易日皆無 >0.5% 未解差異 → 可停用 Actions cron

Ledger:state/parallel_compare.jsonl(每行一筆 {date, side, values, generated_at})。
Mac 側由 run_macro.sh 每日 08:30 自動記錄;Actions 側來源見 config/parallel_actions.json。
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(PROJECT_ROOT))

TZ = timezone(timedelta(hours=8))
LEDGER = os.path.join(PROJECT_ROOT, "state", "parallel_compare.jsonl")
MACRO = os.path.join(PROJECT_ROOT, "docs", "data", "v2", "macro.json")
ACTIONS_CFG = os.path.join(PROJECT_ROOT, "config", "parallel_actions.json")

# 比對欄位(指數 + 融資);§4.3 只要求指數/融資
FIELDS = ["taiex", "sp500", "nasdaq", "vix", "nikkei", "dxy", "margin"]
THRESHOLD_PCT = 0.5


def _today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _append(rec: dict) -> None:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _read_ledger() -> list[dict]:
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _values_from_macro(path: str = MACRO) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    data = d.get("data", {})
    vals = {}
    for k in FIELDS:
        v = data.get(k, {}).get("value")
        if isinstance(v, (int, float)):
            vals[k] = float(v)
    return vals


def record_mac(date: str | None = None, path: str = MACRO) -> dict:
    """自 macro.json 擷取 Mac 側指數/融資 → ledger。"""
    date = date or _today()
    vals = _values_from_macro(path)
    rec = {"date": date, "side": "mac", "values": vals,
           "generated_at": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")}
    _append(rec)
    return rec


def record_note(note: str, date: str | None = None, day: int | None = None) -> dict:
    """記一筆觀察註記(非數值比對日,如颱風假雙方空跑=一種一致)。"""
    date = date or _today()
    rec = {"date": date, "side": "note", "note": note, "day": day,
           "generated_at": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")}
    _append(rec)
    return rec


def record_actions(values: dict, date: str | None = None, source: str = "manual") -> dict:
    """記錄 Actions 側數值(來源:investment-summary)。values 只取 FIELDS 中的數值欄。"""
    date = date or _today()
    vals = {k: float(v) for k, v in values.items() if k in FIELDS and isinstance(v, (int, float))}
    rec = {"date": date, "side": "actions", "values": vals, "source": source,
           "generated_at": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")}
    _append(rec)
    return rec


def _latest_by_side(rows: list[dict], date: str, side: str) -> dict | None:
    cand = [r for r in rows if r.get("date") == date and r.get("side") == side]
    return cand[-1] if cand else None      # 取當日最後一筆


def compare_day(date: str, rows: list[dict] | None = None) -> dict:
    """回傳當日 Mac vs Actions 逐欄 % 差異 + 是否超標。"""
    rows = rows if rows is not None else _read_ledger()
    mac = _latest_by_side(rows, date, "mac")
    act = _latest_by_side(rows, date, "actions")
    result = {"date": date, "has_mac": bool(mac), "has_actions": bool(act),
              "fields": {}, "flags": []}
    if not (mac and act):
        return result
    for k in FIELDS:
        mv, av = mac["values"].get(k), act["values"].get(k)
        if not (isinstance(mv, (int, float)) and isinstance(av, (int, float))):
            continue
        diff_pct = round((mv - av) / av * 100, 4) if av else None
        over = diff_pct is not None and abs(diff_pct) > THRESHOLD_PCT
        result["fields"][k] = {"mac": mv, "actions": av, "diff_pct": diff_pct, "over": over}
        if over:
            result["flags"].append(k)
    return result


def build_summary(need_days: int = 5) -> dict:
    """5 交易日彙總:每日比對 + 每欄差異分佈 + 停用 Actions cron 建議。"""
    rows = _read_ledger()
    dates = sorted({r["date"] for r in rows
                    if any(r2["date"] == r["date"] and r2["side"] == "actions" for r2 in rows)
                    and any(r2["date"] == r["date"] and r2["side"] == "mac" for r2 in rows)})
    days = [compare_day(d, rows) for d in dates]
    # 每欄跨日差異
    per_field: dict[str, dict] = {}
    for k in FIELDS:
        diffs = [d["fields"][k]["diff_pct"] for d in days
                 if k in d["fields"] and d["fields"][k]["diff_pct"] is not None]
        if diffs:
            per_field[k] = {"n": len(diffs),
                            "max_abs_pct": round(max(abs(x) for x in diffs), 4),
                            "mean_abs_pct": round(sum(abs(x) for x in diffs) / len(diffs), 4),
                            "over_count": sum(1 for x in diffs if abs(x) > THRESHOLD_PCT)}
    total_flags = sum(len(d["flags"]) for d in days)
    paired_days = len(days)
    ready = paired_days >= need_days and total_flags == 0
    return {"paired_days": paired_days, "need_days": need_days,
            "total_over_threshold": total_flags, "threshold_pct": THRESHOLD_PCT,
            "per_field": per_field, "days": days,
            "recommend_disable_actions": ready,
            "verdict": ("✅ 建議停用 Actions cron:{}/{} 交易日皆無 >{}% 差異".format(
                            paired_days, need_days, THRESHOLD_PCT) if ready
                        else "⏳ 尚未達標:已比對 {}/{} 日,超標 {} 次".format(
                            paired_days, need_days, total_flags))}


def main() -> int:
    ap = argparse.ArgumentParser(description="Mac vs Actions 早報並行比對(§4)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record-mac", help="自 macro.json 記錄 Mac 側")
    pa = sub.add_parser("record-actions", help="記錄 Actions 側數值")
    pa.add_argument("--values", required=True, help='JSON,例 \'{"taiex":46780.62,"margin":12089437}\'')
    pa.add_argument("--date", default=None)
    pa.add_argument("--source", default="manual")
    pd = sub.add_parser("day", help="顯示某日比對")
    pd.add_argument("--date", default=None)
    pn = sub.add_parser("record-note", help="記觀察註記(如颱風假空跑)")
    pn.add_argument("--note", required=True)
    pn.add_argument("--date", default=None)
    pn.add_argument("--day", type=int, default=None)
    sub.add_parser("summary", help="5 交易日彙總報告")
    args = ap.parse_args()

    if args.cmd == "record-mac":
        r = record_mac()
        print(f"✅ Mac 側記錄 {r['date']}:{ {k: r['values'][k] for k in r['values']} }")
    elif args.cmd == "record-actions":
        vals = json.loads(args.values)
        r = record_actions(vals, date=args.date, source=args.source)
        print(f"✅ Actions 側記錄 {r['date']}(source={r['source']}):{r['values']}")
    elif args.cmd == "record-note":
        r = record_note(args.note, date=args.date, day=args.day)
        print(f"✅ 註記 {r['date']}" + (f"(Day {r['day']})" if r['day'] else "") + f":{r['note']}")
    elif args.cmd == "day":
        d = compare_day(args.date or _today())
        print(json.dumps(d, ensure_ascii=False, indent=2))
    elif args.cmd == "summary":
        s = build_summary()
        print(s["verdict"])
        print(f"門檻 {s['threshold_pct']}% ｜ 已配對 {s['paired_days']}/{s['need_days']} 日 ｜ 超標 {s['total_over_threshold']} 次")
        for k, v in s["per_field"].items():
            print(f"  {k:7s} n={v['n']} 最大|差|={v['max_abs_pct']}% 平均|差|={v['mean_abs_pct']}% 超標={v['over_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
