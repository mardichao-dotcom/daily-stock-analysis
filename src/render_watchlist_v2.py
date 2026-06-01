"""
render_watchlist_v2.py — Stage 8 W3 補充:全 watchlist 折疊 K 線頁面

讀 config/watchlist.json + filtered_result_v2(可選,提供 score/grade/tags 摘要),
產出 docs/watchlist_v2.html。

結構:
  📊 台股板塊(16 板塊,58 檔)
  🌏 國際族群(6 板塊,29 檔)

每個板塊 = <details> 折疊
每個個股 = <details> 折疊,展開後 chart_v2.js 自動載入 K 線

CLI:
  python3 src/render_watchlist_v2.py --date 2026-05-19
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.render_v2 import _h, _safe_id   # 複用 escape + id 工具


# ── 結構建構 ─────────────────────────────────────────────────────────────────

def _short_code(full: str) -> str:
    """'TWSE:2330' → '2330'"""
    return full.split(":")[-1]


def _summary_for_member(symbol: str, stocks_index: dict) -> str:
    """根據 filtered_result(若有)生成 summary 摘要。
    格式:'  [B 4.0]  ⚡ MACD ⛔ ETF 減碼'
    沒在 filtered_result 就回空字串。"""
    entry = stocks_index.get(symbol)
    if not entry:
        return ""
    grade = entry.get("grade", "")
    score = entry.get("score", 0)
    tags  = entry.get("tags", [])
    tag_inline = "".join(
        f'<span class="tag">{_h(t.split()[0])}</span>' for t in tags[:3]
    )
    badge = f'<span class="grade-badge {_h(grade)}">{_h(grade)}</span>' if grade else ""
    return (f'<span class="wl-score">{score:.1f} {badge}</span>'
            f'<span class="wl-tags-inline">{tag_inline}</span>')


def render_member(member: dict, leaders_set: set, date: str,
                   stocks_index: dict) -> str:
    """單一個股的 <details> 折疊區。"""
    symbol = member["code"]
    name   = member["name"]
    is_leader = symbol in leaders_set
    sid = _safe_id(symbol)
    summary_extra = _summary_for_member(symbol, stocks_index)
    leader_mark = '<span class="leader-mark" title="族群長子">⭐</span>' if is_leader else ""

    return f"""
<details class="wl-stock">
  <summary>
    {leader_mark}
    <span class="wl-name">{_h(name)}</span>
    <code class="wl-code">{_h(symbol)}</code>
    {summary_extra}
  </summary>
  <div class="wl-stock-body">
    <div id="chart-{_h(sid)}" class="chart-placeholder"
         data-symbol="{_h(symbol)}" data-date="{_h(date)}">
      [點此載入 K 線圖]
    </div>
  </div>
</details>
"""


def render_sector(sector_name: str, sector_raw: dict, date: str,
                   stocks_index: dict) -> str:
    """單一板塊:含長子標記 + 全成員 <details>。"""
    members = sector_raw.get("成員", [])
    leaders = set(sector_raw.get("長子", []))
    member_html = "\n".join(
        render_member(m, leaders, date, stocks_index) for m in members
    )
    leader_names = []
    for lc in sector_raw.get("長子", []):
        for m in members:
            if m["code"] == lc:
                leader_names.append(m["name"])
                break
    leader_inline = (f'<span class="sector-leaders">⭐ 長子:'
                     f'{_h(" / ".join(leader_names))}</span>') if leader_names else ""
    return f"""
<details class="wl-sector" open>
  <summary>
    <h3 class="wl-sector-title">{_h(sector_name)}
      <span class="wl-sector-count">({len(members)} 檔)</span>
      {leader_inline}
    </h3>
  </summary>
  {member_html}
</details>
"""


def render_intl_group(group_name: str, group_raw: dict, date: str,
                        stocks_index: dict) -> str:
    """國際族群:跟台股板塊類似,但加「對應台股族群」說明。"""
    members = group_raw.get("成員", [])
    leaders = set(group_raw.get("長子", []))
    member_html = "\n".join(
        render_member(m, leaders, date, stocks_index) for m in members
    )
    corresp = group_raw.get("對應台股族群", [])
    corresp_html = (f'<div class="wl-intl-corresp">對應台股族群:'
                    f'{_h(" / ".join(corresp))}</div>') if corresp else ""
    return f"""
<details class="wl-sector" open>
  <summary>
    <h3 class="wl-sector-title">{_h(group_name)}
      <span class="wl-sector-count">({len(members)} 檔)</span>
    </h3>
  </summary>
  {corresp_html}
  {member_html}
</details>
"""


# ── 主渲染 ──────────────────────────────────────────────────────────────────

def render(watchlist: dict, date: str, filtered_result: dict | None = None) -> str:
    """產整份 HTML。filtered_result 可選,用來提供個股 score/grade/tags 摘要。"""
    stocks_index = (filtered_result or {}).get("stocks", {})

    tw_raw   = watchlist.get("台股板塊", {})
    intl_raw = watchlist.get("國際族群", {})

    tw_sectors_html = "\n".join(
        render_sector(name, raw, date, stocks_index)
        for name, raw in tw_raw.items()
    )
    intl_groups_html = "\n".join(
        render_intl_group(name, raw, date, stocks_index)
        for name, raw in intl_raw.items()
    )

    tw_total   = sum(len(s.get("成員", [])) for s in tw_raw.values())
    intl_total = sum(len(g.get("成員", [])) for g in intl_raw.values())
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    update_date = watchlist.get("更新日期", "")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>觀察名單 — 台股動能 Watchlist</title>
<link rel="stylesheet" href="assets/style_v2.css">
</head>
<body>

<header class="page-header">
  <div class="container">
    <nav class="page-nav">
      <a href="index_v2.html">📈 儀表板</a>
      <span class="page-nav-current">📋 Watchlist</span>
    </nav>
    <h1>📋 觀察名單 Watchlist</h1>
    <div class="meta">
      資料日期 <strong>{_h(date)}</strong> ｜
      台股 {tw_total} 檔 ｜ 國際 {intl_total} 檔 ｜
      watchlist 更新日 {_h(update_date)} ｜ 產出時間 {generated_at}
    </div>
  </div>
</header>

<main class="container">

<section class="section">
  <h2>📊 台股板塊 ({len(tw_raw)} 板塊 / {tw_total} 檔)</h2>
{tw_sectors_html}
</section>

<section class="section">
  <h2>🌏 國際族群 ({len(intl_raw)} 群 / {intl_total} 檔)</h2>
{intl_groups_html}
</section>

</main>

<script src="assets/chart_v2.js" defer></script>
</body>
</html>
"""


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stage 8 W3 Watchlist 頁面 generator")
    parser.add_argument("--date",      required=True)
    parser.add_argument("--watchlist", default=str(PROJECT_ROOT / "config" / "watchlist.json"))
    parser.add_argument("--result",    default=str(PROJECT_ROOT / "filtered_result_v2.json"),
                         help="(可選)filtered_result_v2.json 提供 score/grade 摘要")
    parser.add_argument("--output",    default=str(PROJECT_ROOT / "docs" / "watchlist_v2.html"))
    args = parser.parse_args()

    with open(args.watchlist, encoding="utf-8") as f:
        watchlist = json.load(f)

    filtered_result = None
    if os.path.exists(args.result):
        with open(args.result, encoding="utf-8") as f:
            filtered_result = json.load(f)

    html = render(watchlist, args.date, filtered_result)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 寫入 {args.output}")


if __name__ == "__main__":
    main()
