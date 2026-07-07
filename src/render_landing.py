"""
render_landing.py — Stage 8 W3 上線部署:版本 A 入口頁

讀 config/watchlist.json + filtered_result_v2.json + 掃 docs/data/v2/,
產出 docs/index.html(取代舊 v1 dashboard 作為主入口)。

3 個進入點:
  📈 今日儀表板 → index_v2.html
  📋 Watchlist  → watchlist_v2.html
  📅 歷史儀表板 → history.html

CLI:
  python3 src/render_landing.py [--result filtered_result_v2.json]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.render_v2 import _h
from src import site_meta
from src import asset_version


HISTORY_PATTERN = re.compile(r"^index_v2_(2\d{3}-\d{2}-\d{2})\.html$")


def count_history_snapshots(docs_dir: Path) -> int:
    """掃 docs/index_v2_YYYY-MM-DD.html,回傳天數。"""
    if not docs_dir.exists():
        return 0
    return sum(1 for f in docs_dir.iterdir()
                if f.is_file() and HISTORY_PATTERN.match(f.name))


def count_watchlist(watchlist: dict) -> tuple[int, int, int, int]:
    """回 (台股板塊數, 台股檔數, 國際族群數, 國際檔數)。"""
    tw_sectors = watchlist.get("台股板塊", {})
    intl_groups = watchlist.get("國際族群", {})
    tw_n = sum(len(s.get("成員", [])) for s in tw_sectors.values())
    intl_n = sum(len(g.get("成員", [])) for g in intl_groups.values())
    return len(tw_sectors), tw_n, len(intl_groups), intl_n


def render(*,
            watchlist: dict,
            latest_date: str,
            history_count: int,
            filtered_result: dict | None = None) -> str:
    tw_secs, tw_n, intl_secs, intl_n = count_watchlist(watchlist)
    # §6.3 meta 列只從 site_meta 取值(版本/檔數)
    sm = site_meta.load(latest_date) or {}
    tw_n   = sm.get("tw_count", tw_n)
    intl_n = sm.get("intl_count", intl_n)
    total_n = sm.get("total_count", tw_n + intl_n)
    sm_rule = sm.get("rule_version", "v2.2")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    history_blurb = f"{history_count} 天可選" if history_count > 0 else "今天開始累積"

    # §16:結論狀態列(§8 同款,重用 render_v2;無 filtered_result 時省略)
    status_html = ""
    if filtered_result:
        from src import render_v2 as rv
        stocks = filtered_result.get("stocks", {})
        buckets = rv.classify_stocks(stocks)
        status_map = rv.load_status_map(latest_date)
        etf_active = filtered_result.get("etf_active", {"increase": [], "decrease": []})
        status_html = rv.render_status_bar(buckets, stocks, status_map, etf_active, latest_date)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>台股動能作戰系統 — 規則 {_h(sm_rule)}</title>
  {asset_version.head_snippet()}
</head>
<body class="page-landing">

<header class="page-header landing-header">
  <div class="container">
    <h1>🧭 台股動能作戰系統</h1>
    <div class="meta">
      資料日期 <strong>{_h(latest_date)}</strong> ｜ 規則 {_h(sm_rule)} ｜
      個股 {total_n} 檔(台股 {tw_n} + 國際 {intl_n}) ｜
      產出時間 {generated_at}
    </div>
    <div class="landing-subtitle">找買點的雷達 + ETF 籌碼面雙向掃描</div>
  </div>
</header>

<main class="container landing-main">
{status_html}
  <div class="landing-index">
    <a class="li-row" href="index_v2.html"><span class="li-name">📈 今日儀表板</span>
      <span class="li-sum">S/A/B 戰區 · ETF 雙向 · 跌破警示 ｜ 資料日 {_h(latest_date)}</span>
      <span class="li-go">→</span></a>
    <a class="li-row" href="watchlist_v2.html"><span class="li-name">📋 Watchlist</span>
      <span class="li-sum">全 {total_n} 檔 K 線+籌碼 ｜ {tw_secs} 板塊 + {intl_secs} 國際族群</span>
      <span class="li-go">→</span></a>
    <a class="li-row" href="tags.html"><span class="li-name">🔥 主題熱度</span>
      <span class="li-sum">L2+L3+L4 標籤等權漲幅排行(N≥3 上榜)</span>
      <span class="li-go">→</span></a>
    <a class="li-row" href="history.html"><span class="li-name">📅 歷史儀表板</span>
      <span class="li-sum">{_h(history_blurb)} · 每日 snapshot</span>
      <span class="li-go">→</span></a>
    <a class="li-row" href="weekly.html"><span class="li-name">🗂 週報</span>
      <span class="li-sum">NAAIM · VIX · XLY/XLP · 週融資(億元)· 每週六 09:00</span>
      <span class="li-go">→</span></a>
  </div>

  <details class="landing-settings">
    <summary>⚙ 設定</summary>
    <div class="ls-body">
      <div class="ls-item"><span class="ls-label">🔑 新聞關鍵字管理</span>
        <span class="ls-kws" id="ls-kws">載入中…</span>
        <a class="nwk-add" href="https://github.com/mardichao-dotcom/daily-stock-analysis/edit/main/config/news_keywords.json"
           target="_blank" rel="noopener">✏️ 編輯</a></div>
      <div class="ls-note">關鍵字同時作用於:新聞命中過濾(儀表板新聞區塊)與 Discord 早報關注列。</div>
    </div>
  </details>
</main>

<script>
(function () {{
  // §16 IA 提案一:關鍵字管理移入口頁——顯示現行清單(raw github,08:30 後即最新)
  fetch('https://raw.githubusercontent.com/mardichao-dotcom/daily-stock-analysis/main/config/news_keywords.json',
        {{ cache: 'no-cache' }})
    .then(r => r.ok ? r.json() : null)
    .then(d => {{
      var el = document.getElementById('ls-kws');
      if (!el) return;
      el.textContent = (d && d.keywords) ? d.keywords.join('、') : '(讀取失敗)';
    }})
    .catch(function () {{
      var el = document.getElementById('ls-kws');
      if (el) el.textContent = '(讀取失敗)';
    }});
}})();
</script>

<footer class="landing-footer">
  <div class="container">
    個股 {total_n} 檔 ｜ 產出時間 {generated_at}
  </div>
</footer>

</body>
</html>"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--watchlist", default=str(PROJECT_ROOT / "config" / "watchlist.json"))
    p.add_argument("--result",    default=str(PROJECT_ROOT / "filtered_result_v2.json"))
    p.add_argument("--docs",      default=str(PROJECT_ROOT / "docs"))
    p.add_argument("--output",    default=str(PROJECT_ROOT / "docs" / "index.html"))
    args = p.parse_args()

    with open(args.watchlist, encoding="utf-8") as f:
        watchlist = json.load(f)

    latest_date = "?"
    filtered_result = None
    if os.path.exists(args.result):
        with open(args.result, encoding="utf-8") as f:
            filtered_result = json.load(f)
        latest_date = filtered_result.get("date", "?")

    history_count = count_history_snapshots(Path(args.docs))

    html = render(
        watchlist=watchlist,
        latest_date=latest_date,
        filtered_result=filtered_result,
        history_count=history_count,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 寫入 {args.output}  (history snapshots: {history_count})")


if __name__ == "__main__":
    main()
