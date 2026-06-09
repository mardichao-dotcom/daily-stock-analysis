"""
計算每日標籤平均漲幅(等權平均)。
排行成員:L2 + L3 + L4 全標籤,N >= 3 上榜。

用法:
  python3 -m src.theme_returns --date 2026-06-08

輸出:
  docs/data/v2/<date>/theme_returns.json
"""

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUBTAGS = PROJECT_ROOT / "config" / "subtags.json"
KLINE_DB = PROJECT_ROOT / "kline.db"
OUTPUT_DIR = PROJECT_ROOT / "docs" / "data" / "v2"

N_THRESHOLD = 3


def load_subtags(path=None):
    """讀 subtags.json,建 tag → [codes] 索引"""
    data = json.loads(Path(path or SUBTAGS).read_text(encoding="utf-8"))
    stocks = data.get("stocks", {})

    tag_members = defaultdict(set)
    code_to_name = {}

    for code, info in stocks.items():
        code_to_name[code] = info.get("name", code)
        for level in ("L2", "L3", "L4"):
            for tag in info.get(level, []):
                tag_members[tag].add(code)

    return tag_members, code_to_name


def get_return(conn, code, date):
    """
    取得個股當日漲幅。
    return = (close - prev_close) / prev_close * 100

    回傳 None 表示:
    - 當日無報價(停牌)
    - 沒有前一交易日(K 線太少)
    """
    cur = conn.execute(
        "SELECT close, date FROM kline "
        "WHERE symbol = ? AND date <= ? "
        "ORDER BY date DESC LIMIT 2",
        (code, date)
    )
    rows = cur.fetchall()

    if len(rows) < 2:
        return None

    today_close, today_date = rows[0]
    prev_close, prev_date = rows[1]

    if today_date != date:
        return None

    if not prev_close or prev_close == 0:
        return None

    return (today_close - prev_close) / prev_close * 100


def compute_for_date(date_str, kline_db=None, subtags_path=None):
    """計算指定日期的所有標籤漲幅"""
    tag_members, code_to_name = load_subtags(subtags_path)

    conn = sqlite3.connect(kline_db or KLINE_DB)

    tags_output = []

    for tag, codes in tag_members.items():
        n = len(codes)

        members = []
        returns = []
        excluded = 0

        for code in sorted(codes):
            r = get_return(conn, code, date_str)
            name = code_to_name.get(code, code)

            if r is None:
                excluded += 1
                members.append({
                    "code": code,
                    "name": name,
                    "return_pct": None,
                    "excluded": True
                })
            else:
                members.append({
                    "code": code,
                    "name": name,
                    "return_pct": round(r, 2),
                    "excluded": False
                })
                returns.append(r)

        n_traded = len(returns)

        if n_traded == 0:
            avg_return = None
            rankable = False
        else:
            avg_return = round(sum(returns) / n_traded, 2)
            rankable = n_traded >= N_THRESHOLD

        tags_output.append({
            "tag": tag,
            "n": n,
            "n_traded": n_traded,
            "n_excluded": excluded,
            "return_pct": avg_return,
            "rankable": rankable,
            "members": sorted(
                members,
                key=lambda m: (m["return_pct"] is None, -(m["return_pct"] or 0))
            )
        })

    tags_output.sort(key=lambda t: (
        not t["rankable"],
        -(t["return_pct"] if t["return_pct"] is not None else -999)
    ))

    conn.close()

    return {
        "date": date_str,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rules": {
            "n_threshold": N_THRESHOLD,
            "calculation": "equal_weight_average",
            "scope": "L2 + L3 + L4 標籤混排"
        },
        "stats": {
            "total_tags": len(tags_output),
            "rankable_tags": sum(1 for t in tags_output if t["rankable"]),
            "tags_with_no_data": sum(1 for t in tags_output if t["return_pct"] is None),
        },
        "tags": tags_output
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--output", default=None,
                        help="輸出路徑(預設 docs/data/v2/<date>/theme_returns.json)")
    args = parser.parse_args()

    output = compute_for_date(args.date)

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = OUTPUT_DIR / args.date / "theme_returns.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )

    print(f"✅ 寫入 {out_path}")
    print(f"   日期: {args.date}")
    print(f"   標籤數: {output['stats']['total_tags']}")
    print(f"   可上榜(N>=3): {output['stats']['rankable_tags']}")
    print(f"   無資料: {output['stats']['tags_with_no_data']}")

    rankable = [t for t in output["tags"] if t["rankable"]]
    print(f"\n   📊 上榜 Top 10:")
    for t in rankable[:10]:
        ret = f"{t['return_pct']:+.2f}%"
        n = t["n_traded"]
        print(f"     {ret:>8}  {t['tag']:<20}(n={n})")

    print(f"\n   📉 上榜 Bottom 5:")
    for t in rankable[-5:]:
        ret = f"{t['return_pct']:+.2f}%"
        n = t["n_traded"]
        print(f"     {ret:>8}  {t['tag']:<20}(n={n})")


if __name__ == "__main__":
    main()
