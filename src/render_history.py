"""
render_history.py — Stage 8 W3 上線部署:歷史儀表板選單

掃 docs/index_v2_YYYY-MM-DD.html(snapshot)+ docs/data/v2/{date}/(chart 資料),
列出可選日期清單。每天 entry 含 S/A/B 級簡述(讀對應 filtered_result_{date}.json,
若有保存)。

CLI:
  python3 src/render_history.py
  python3 src/render_history.py --max-days 30
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import datetime, date as dt_date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.render_v2 import _h


SNAPSHOT_PATTERN = re.compile(r"^index_v2_(2\d{3}-\d{2}-\d{2})\.html$")
WEEKDAY_ZH = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


def discover_snapshots(docs_dir: Path) -> list[str]:
    """掃 docs/ 內 index_v2_YYYY-MM-DD.html,回傳排序後的日期字串 list(新→舊)。"""
    if not docs_dir.exists():
        return []
    dates = []
    for f in docs_dir.iterdir():
        if not f.is_file():
            continue
        m = SNAPSHOT_PATTERN.match(f.name)
        if m:
            dates.append(m.group(1))
    return sorted(dates, reverse=True)


def filter_recent(dates: list[str], max_days: int) -> list[str]:
    """限制到 max_days 內(以最新日為基準),避免列出太老的 snapshot。"""
    if not dates or max_days <= 0:
        return dates
    cutoff = dt_date.fromisoformat(dates[0]) - timedelta(days=max_days)
    return [d for d in dates if dt_date.fromisoformat(d) >= cutoff]


def _grade_counts_from_score_history(project_root: Path, date_str: str) -> dict:
    """從 kline.db score_history 算當日 S/A/B 數(retained,所有歷史日皆可回填)。
    §6.1#1:V2 era 不再寫 filtered_result_{date}.json,改以 score_history 為摘要來源。"""
    import sqlite3
    db = project_root / "kline.db"
    if not db.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT grade, COUNT(*) FROM score_history WHERE date=? GROUP BY grade",
            (date_str,)).fetchall()
        conn.close()
    except Exception:
        return {}
    counts = {"S": 0, "A": 0, "B": 0}
    for g, n in rows:
        if g in counts:
            counts[g] = n
    return counts if sum(counts.values()) >= 0 and rows else {}


def load_summary(project_root: Path, date_str: str) -> dict:
    """回 {S, A, B[, etf_inc, etf_dec]}(找不到回空 {})。來源優先序:
      1) docs/data/v2/{date}/_summary.json — render 時寫,含 ETF(今日 + 未來)
      2) filtered_result_{date}.json        — legacy V1 era(~6/05 前)
      3) score_history(kline.db)            — S/A/B 回填(6/08~,V2 era 無 dated 檔)
    """
    # 1) per-date _summary.json(完整,含 ETF)
    sm_path = project_root / "docs" / "data" / "v2" / date_str / "_summary.json"
    if sm_path.exists():
        try:
            return json.loads(sm_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 2) legacy dated filtered_result
    path = project_root / f"filtered_result_{date_str}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            counts = {"S": 0, "A": 0, "B": 0}
            for s in data.get("stocks", {}).values():
                g = s.get("grade")
                if g in counts:
                    counts[g] += 1
            ea = data.get("etf_active", {})
            return {**counts,
                    "etf_inc": len(ea.get("increase", [])),
                    "etf_dec": len(ea.get("decrease", []))}
        except Exception:
            pass

    # 3) score_history 回填(S/A/B,無 ETF)
    return _grade_counts_from_score_history(project_root, date_str)


def weekday_zh(date_str: str) -> str:
    try:
        return WEEKDAY_ZH[dt_date.fromisoformat(date_str).weekday()]
    except ValueError:
        return ""


def render_item(date_str: str, summary: dict) -> str:
    wd = weekday_zh(date_str)
    recomputed = ""
    if summary:
        sab = f"S {summary.get('S', 0)} / A {summary.get('A', 0)} / B {summary.get('B', 0)}"
        if "etf_inc" in summary and "etf_dec" in summary:
            sab += f" ｜ ETF 加 {summary['etf_inc']} 減 {summary['etf_dec']}"
        if summary.get("recomputed"):
            # 審計歸檔 13(2026-07-07 拍板):停更回補批(6/15~7/2)標注升級——
            # 明示「以當前 config 重放,非當日等價」(當日 key_prices 可能不同)
            recomputed = (f'<span class="history-recomputed" style="color:#f59e0b;font-size:11px">'
                          f'⚠️ {_h(summary["recomputed"])} 事後重算(以當前 config 重放,非當日等價)</span>')
    else:
        sab = "(無摘要)"
    return f"""
<a class="history-item" href="index_v2_{_h(date_str)}.html">
  <span class="history-date">{_h(date_str)}</span>
  <span class="history-day">{_h(wd)}</span>
  <span class="history-summary">{_h(sab)}</span>
  {recomputed}
</a>"""


def render(dates: list[str], summaries: dict[str, dict]) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    items_html = "\n".join(render_item(d, summaries.get(d, {})) for d in dates)
    if not dates:
        items_html = '<div class="empty-state">尚無歷史 snapshot(每日自動產出)</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>歷史儀表板 — 台股動能作戰系統</title>
  <link rel="stylesheet" href="assets/style_v2.css">
</head>
<body>

<header class="page-header">
  <div class="container">
    <nav class="page-nav">
      <a href="index.html">← 回首頁</a>
      <a href="index_v2.html">📈 今日儀表板</a>
      <a href="watchlist_v2.html">📋 Watchlist</a>
      <a href="tags.html">🔥 主題熱度</a>
      <a href="weekly.html">📅 週報</a>
      <span class="page-nav-current">📅 歷史</span>
    </nav>
    <h1>📅 歷史儀表板</h1>
    <div class="meta">
      共 <strong>{len(dates)}</strong> 天 snapshot ｜
      保留 30 天(每日自動 archive) ｜ 產出時間 {generated_at}
    </div>
  </div>
</header>

<main class="container">
  <section class="section">
    <div class="history-list">
{items_html}
    </div>
  </section>
</main>

</body>
</html>"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--docs",     default=str(PROJECT_ROOT / "docs"))
    p.add_argument("--output",   default=str(PROJECT_ROOT / "docs" / "history.html"))
    p.add_argument("--max-days", type=int, default=30)
    args = p.parse_args()

    docs_dir = Path(args.docs)
    dates = discover_snapshots(docs_dir)
    dates = filter_recent(dates, args.max_days)

    summaries = {d: load_summary(PROJECT_ROOT, d) for d in dates}

    html = render(dates, summaries)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 寫入 {args.output}  ({len(dates)} snapshots listed)")


if __name__ == "__main__":
    main()
