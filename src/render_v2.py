"""
render_v2.py — Stage 8 W3 主體:讀 filtered_result_v2.json,
                                  產出 docs/index_v2.html(7 區塊結構)

完全新檔,不 import v1 render.py(v1 凍結期)。
純 Python 字串拼接,**不用 Jinja2**(per W3 設計決定)。

7 區塊(per stage8_spec.md §5.1 + 朋友 2026-05-29 確認):
  1. 🏆 當日前十名(降冪)
  2. 🔴 S 級戰區(score ≥ 6,個股卡含 K 線預留 div)
  3. 🟡 A 級戰區(5 ≤ score < 6)
  4. 🟢 B 級戰區(4 ≤ score < 5)
  5. ⭐ C 級特殊(有任一標籤的 C 級)
  6. ⛔ ETF 主動式雙向掃描(從 etf_active.increase / decrease)
  7. 📋 其餘品項(C 級無標籤,<details> 折疊)

CLI:
  python3 src/render_v2.py --date 2026-05-20
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


SPECIAL_TAG_KEYWORDS = ("站穩", "跌破", "MACD", "個股輪動", "ETF 減碼")

# 計分明細的 5 大 module 分類(對應朋友規則 §1~§5)
# (label, [module 名]) — 順序就是渲染順序
MODULE_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("量能",       ("volume",)),
    ("關鍵價/MA",  ("given_price", "ma")),
    ("族群連動",   ("sector_linkage",)),
    ("籌碼面",     ("chip_etf",)),
    ("MACD",       ("macd",)),
]


# ─── HTML escape ─────────────────────────────────────────────────────────────
def _h(s) -> str:
    """HTML escape"""
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _safe_id(symbol: str) -> str:
    """TWSE:2330 → TWSE_2330(filesystem + DOM id 安全)"""
    return symbol.replace(":", "_")


def _has_special_tag(tags: list[str]) -> bool:
    """是否含任一特殊標籤(站穩/跌破/MACD/輪動/ETF 減碼)"""
    return any(any(kw in t for kw in SPECIAL_TAG_KEYWORDS) for t in tags)


def _fmt_num(x) -> str:
    """數字格式化:整數去掉 .0,小數兩位"""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{f:.0f}" if f == int(f) else f"{f:.2f}"


def _merge_ma_entries(ma_details: list[dict]) -> str | None:
    """把多筆 ma details 合併成一條描述。回傳 None 表示沒有 ma。
    例:[MA20, MA60, MA90] → '首次站上 MA20/60/90 (close 212.5 > 205.78 / 204.91 / 199.99)'
    例:[MA20] → '首次站上 MA20 (close 212.5 > 205.78)'
    """
    if not ma_details:
        return None
    periods, ma_vals = [], []
    close = None
    for d in ma_details:
        ev = d.get("evidence", {})
        cat = ev.get("category", "")
        p = cat.replace("ma_", "") if cat.startswith("ma_") else cat
        periods.append(p)
        ma_vals.append(_fmt_num(ev.get("ma_value", "?")))
        if close is None:
            close = ev.get("today_close")
    close_str = _fmt_num(close) if close is not None else "?"
    if len(periods) == 1:
        return f"首次站上 MA{periods[0]}(close {close_str} &gt; {ma_vals[0]})"
    return (f"首次站上 MA{'/'.join(periods)}"
            f"(close {close_str} &gt; {' / '.join(ma_vals)})")


def _format_breakdown_by_module(details: list[dict]) -> str:
    """按 5 大 module 分類渲染計分明細表格。空 module 顯示「—」。
    回傳已 escape 的 HTML(table 元素)。"""
    # 先依 module 分桶
    by_mod: dict[str, list[dict]] = {}
    for d in details:
        by_mod.setdefault(d.get("module", "?"), []).append(d)

    rows = []
    for label, modules in MODULE_CATEGORIES:
        items = [d for m in modules for d in by_mod.get(m, [])]
        if not items:
            rows.append(
                f'<tr class="bd-row bd-empty">'
                f'<td class="bd-cat">{_h(label)}</td>'
                f'<td class="bd-score">—</td>'
                f'<td class="bd-desc">—</td>'
                f'</tr>'
            )
            continue

        total = sum(d.get("score", 0) for d in items)

        # 「關鍵價/MA」特殊處理:ma 合併、given_price 各自一行
        if label == "關鍵價/MA":
            descs: list[str] = []
            ma_entries = [d for d in items if d.get("module") == "ma"]
            merged = _merge_ma_entries(ma_entries)
            if merged:
                descs.append(merged)
            for d in items:
                if d.get("module") == "ma":
                    continue
                descs.append(_h(d.get("reason", "")))
            desc_html = "<br>".join(descs)
        else:
            # 其他類別:每筆 reason 一行
            desc_html = "<br>".join(_h(d.get("reason", "")) for d in items)

        rows.append(
            f'<tr class="bd-row">'
            f'<td class="bd-cat">{_h(label)}</td>'
            f'<td class="bd-score">+{total:.1f}</td>'
            f'<td class="bd-desc">{desc_html}</td>'
            f'</tr>'
        )

    return ('<table class="score-breakdown-table">\n'
            + "\n".join("  " + r for r in rows)
            + '\n</table>')


# ─── 個股分類 ─────────────────────────────────────────────────────────────────
def classify_stocks(stocks: dict) -> dict:
    """依 grade 分桶。Returns {'S': [...], 'A': [...], 'B': [...], 'C_special': [...], 'C_other': [...]}
    每個 entry 是 (symbol, stock_dict) 的 list。

    規則 v2.2 §4:C 級特殊用「tags_today」(只今天新成立),
    避免歷史持續站穩灌水(若 stock entry 無 tags_today,fallback tags 維持相容)。
    """
    buckets = {"S": [], "A": [], "B": [], "C_special": [], "C_other": []}
    for symbol, stock in stocks.items():
        grade = stock.get("grade", "D")
        tags_for_c = stock.get("tags_today", stock.get("tags", []))
        if grade == "S":
            buckets["S"].append((symbol, stock))
        elif grade == "A":
            buckets["A"].append((symbol, stock))
        elif grade == "B":
            buckets["B"].append((symbol, stock))
        elif grade in ("C", "D"):
            if _has_special_tag(tags_for_c):
                buckets["C_special"].append((symbol, stock))
            else:
                buckets["C_other"].append((symbol, stock))
    # 各桶內依 score 降冪
    for k in buckets:
        buckets[k].sort(key=lambda x: -x[1].get("score", 0))
    return buckets


# ─── 區塊渲染 ────────────────────────────────────────────────────────────────

def load_theme_returns(date: str) -> dict | None:
    """讀 docs/data/v2/<date>/theme_returns.json,失敗回 None。"""
    path = PROJECT_ROOT / "docs" / "data" / "v2" / date / "theme_returns.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def render_themes(date: str, top_n: int = 10) -> str:
    """區塊 1.5:主題熱度 Top N(L2+L3+L4 N>=3 標籤等權平均漲幅排行)。

    2026-06-09 加入,讀 theme_returns.json。
    JSON 不存在 → 整個 section 跳過(向後相容歷史 snapshot)。
    """
    data = load_theme_returns(date)
    if not data:
        return ""

    rankable = [t for t in data.get("tags", []) if t.get("rankable")]
    if not rankable:
        return ""

    items = []
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, t in enumerate(rankable[:top_n], 1):
        rank = medals.get(i, f"#{i}")
        ret = t.get("return_pct") or 0.0
        cls = "gain" if ret > 0 else ("loss" if ret < 0 else "flat")
        sign = "+" if ret > 0 else ""
        n = t.get("n_traded", 0)
        items.append(f"""
  <li>
    <span class="rank">{rank}</span>
    <span class="theme-return {cls}">{sign}{ret:.2f}%</span>
    <span class="name">{_h(t.get("tag", ""))}</span>
    <span class="theme-n">(n={n})</span>
  </li>""")

    total = data.get("stats", {}).get("rankable_tags", len(rankable))
    return f"""
<section class="section">
  <h2>🔥 主題熱度 Top {min(top_n, len(rankable))}</h2>
  <ul class="themes-list">{''.join(items)}
  </ul>
  <div class="themes-footer">
    <a href="tags.html">查看完整列表(共 {total} 個上榜標籤）→</a>
  </div>
</section>
"""


def render_top10(stocks: dict, date: str = "", status_map: dict | None = None) -> str:
    """區塊 1:當日前十名(by score 降冪)。

    2026-06-02 朋友 review:每項從純文字列表升級為可折疊 <details> 卡片,
    展開後看 K 線。複用 chart_placeholder_html(id_prefix="chart-top10"),
    避免跟 S/A/B 個股卡的 id 衝突(同檔股可能同時出現)。
    """
    ranked = sorted(stocks.items(), key=lambda x: -x[1].get("score", 0))[:10]
    if not ranked:
        return _section_empty("🏆 當日前十名", "無資料")

    status_map = status_map or {}
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    cards = []
    for i, (symbol, stock) in enumerate(ranked, 1):
        name  = stock.get("name", "")
        score = stock.get("score", 0)
        grade = stock.get("grade", "?")
        tags  = stock.get("tags", [])
        sid   = _safe_id(symbol)
        rank_badge = medals.get(i, f"#{i}")
        # tag inline 前 3 個 emoji
        tag_inline = "".join(
            f'<span class="tag">{_h(t.split()[0])}</span>' for t in tags[:3]
        )
        placeholder = chart_placeholder_html(
            symbol, date, status_map.get(sid), id_prefix="chart-top10",
        )
        cards.append(f"""
<details class="stock-card top10-card grade-{_h(grade)}">
  <summary>
    <span class="top10-rank">{rank_badge}</span>
    <span class="stock-name">{_h(name)}</span>
    <code class="stock-code">{_h(symbol)}</code>
    <span class="stock-tags-inline">{tag_inline}</span>
    <span class="stock-score">{score:.1f} <span class="grade-badge {_h(grade)}">{_h(grade)}</span></span>
  </summary>
  <div class="card-body">
    {placeholder}
  </div>
</details>
""")
    return f"""
<section class="section">
  <h2>🏆 當日前十名</h2>
{chr(10).join(cards)}
</section>
"""


def chart_placeholder_html(symbol: str, date: str, status_entry: dict | None,
                              id_prefix: str = "chart") -> str:
    """根據 _index.json 的 status 決定 chart placeholder 樣式。

    id_prefix:用於同頁面多份相同個股 placeholder 時避免 id 衝突
              (例:Top 10 用 "chart-top10",個股卡用 預設 "chart")
    status_entry 結構(from prepare_charts_v2 _index.json):
        {status: "ready" | "waiting_us_close" | "missing",
         exchange: "TW"/"US"/"JP"/"INTL", last_available_date?: ...}
    status_entry=None 時 fallback 預設 ready(向後相容)。
    """
    sid = _safe_id(symbol)
    elem_id = f"{id_prefix}-{sid}"
    status = (status_entry or {}).get("status", "ready")
    if status == "waiting_us_close":
        exchange = (status_entry or {}).get("exchange", "INTL")
        last_d   = (status_entry or {}).get("last_available_date", "")
        last_txt = f"<br><small>最新資料:{_h(last_d)}</small>" if last_d else ""
        return (f'<div id="{_h(elem_id)}" class="chart-placeholder awaiting">'
                f'⏳ 等待 {_h(exchange)} 收盤資料(下個交易日更新){last_txt}'
                f'</div>')
    if status == "missing":
        return (f'<div id="{_h(elem_id)}" class="chart-placeholder errored">'
                f'⚠️ 此檔無 K 線資料,請聯絡管理員'
                f'</div>')
    # ready(或沒 status entry → 預設 ready)
    return (f'<div id="{_h(elem_id)}" class="chart-placeholder"'
            f' data-symbol="{_h(symbol)}" data-date="{_h(date)}">'
            f'[點此載入 K 線圖]'
            f'</div>')


def render_stock_card(symbol: str, stock: dict, date: str,
                        status_entry: dict | None = None) -> str:
    """個股卡(S/A/B 級用)— summary + score breakdown + tags + chart placeholder

    status_entry:from _index.json["symbols"][safe_id],決定 chart 區塊顯示
    """
    name   = stock.get("name", "")
    sector = stock.get("sector", "")
    score  = stock.get("score", 0)
    grade  = stock.get("grade", "?")
    tags   = stock.get("tags", [])
    details = stock.get("details", [])

    # tag inline 顯示前 3 個 emoji(空間有限)
    tag_inline = "".join(f'<span class="tag">{_h(t.split()[0])}</span>' for t in tags[:3])

    # score breakdown:by-module 分組(5 類,空類別顯示 —)
    breakdown_html = _format_breakdown_by_module(details)

    # tag list(完整)
    tag_html = "".join(f'<span class="tag">{_h(t)}</span>' for t in tags)
    if not tag_html:
        tag_html = '<span class="tag" style="color: var(--text-mute);">(無標籤)</span>'

    placeholder_html = chart_placeholder_html(symbol, date, status_entry)

    return f"""
<details class="stock-card grade-{_h(grade)}">
  <summary>
    <span class="stock-name">{_h(name)}</span>
    <code class="stock-code">{_h(symbol)}</code>
    <span class="stock-tags-inline">{tag_inline}</span>
    <span class="stock-score">{score:.1f} <span class="grade-badge {_h(grade)}">{_h(grade)}</span></span>
  </summary>
  <div class="card-body">
    <div style="font-size: 12px; color: var(--text-mute); margin-bottom: 6px;">板塊:{_h(sector)}</div>
    {breakdown_html}
    <div class="tags">{tag_html}</div>
    {placeholder_html}
  </div>
</details>
"""


def render_grade_section(grade: str, label: str, stocks_list, date: str,
                           status_map: dict | None = None) -> str:
    """區塊 2-4:S/A/B 級戰區"""
    if not stocks_list:
        return _section_empty(label, f"今日無{grade}級個股")

    status_map = status_map or {}
    cards = "\n".join(
        render_stock_card(sym, stock, date, status_map.get(_safe_id(sym)))
        for sym, stock in stocks_list
    )
    return f"""
<section class="section grade-{_h(grade)}">
  <h2>{label} ({len(stocks_list)} 檔)</h2>
{cards}
</section>
"""


def render_c_special(stocks_list) -> str:
    """區塊 5:C 級特殊(按標籤類別分群)。
    顯示順序:站穩 → MACD 動能轉多 → 個股輪動 → 跌破 → MACD 動能轉空 → ETF 減碼
    同檔多標籤 = 多次出現(訊號疊加),不去重 → 總數叫「檔次」。
    """
    if not stocks_list:
        return _section_empty("⭐ C 級特殊標籤", "今日無 C 級含特殊標籤個股")

    # (group label, keywords to match)
    C_GROUP_DEFS: list[tuple[str, tuple[str, ...]]] = [
        ("🟢 站穩",          ("站穩",)),
        ("⚡ MACD 動能轉多",  ("MACD 動能轉多",)),
        ("⭐ 個股輪動",       ("個股輪動",)),
        ("🔴 跌破",          ("跌破",)),
        ("⚡ MACD 動能轉空",  ("MACD 動能轉空",)),
        ("⛔ ETF 減碼",       ("ETF 減碼",)),
    ]

    groups: dict[str, list[tuple[str, dict, str]]] = {lbl: [] for lbl, _ in C_GROUP_DEFS}
    for symbol, stock in stocks_list:
        # v2.2 §4:C 級分組用 tags_today(只今天新成立);
        #         若 stock 無 tags_today(舊資料),fallback 用 tags
        for tag in stock.get("tags_today", stock.get("tags", [])):
            for label, kws in C_GROUP_DEFS:
                if any(kw in tag for kw in kws):
                    groups[label].append((symbol, stock, tag))
                    break

    total = sum(len(v) for v in groups.values())

    blocks = []
    for label, _kws in C_GROUP_DEFS:
        entries = groups[label]
        if not entries:
            continue
        lis = []
        for sym, st, tag in entries:
            lis.append(
                f'<li>'
                f'<strong>{_h(st.get("name",""))}</strong> '
                f'<code>{_h(sym)}</code> '
                f'<span class="c-score">{st.get("score",0):.1f}</span> '
                f'<span class="c-tag-detail">{_h(tag)}</span>'
                f'</li>'
            )
        blocks.append(
            f'  <div class="c-group">\n'
            f'    <h3 class="c-group-title">{_h(label)} '
            f'<span class="c-group-count">({len(entries)} 檔)</span></h3>\n'
            f'    <ul class="c-special-list">\n'
            + "\n".join("      " + x for x in lis)
            + '\n    </ul>\n'
            f'  </div>'
        )

    return f"""
<section class="section grade-c">
  <h2>⭐ C 級特殊標籤 ({total} 檔次)</h2>
{chr(10).join(blocks)}
</section>
"""


def render_etf_active(etf_active: dict, stocks: dict) -> str:
    """區塊 6:ETF 主動式雙向掃描"""
    increase = etf_active.get("increase", []) or []
    decrease = etf_active.get("decrease", []) or []

    def _name_of(sym):
        return stocks.get(sym, {}).get("name", "")

    def _table(rows, kind: str) -> str:
        if not rows:
            return '<div class="empty-state">無 ≥ 2 檔共識</div>'
        body = []
        for r in rows:
            sym = r["symbol"]
            shares = r["total_shares"]
            cls = "positive" if kind == "buy" else "negative"
            etfs_str = "、".join(r.get("etfs", []))
            body.append(
                f"<tr>"
                f"<td><strong>{_h(_name_of(sym))}</strong><br>"
                f"  <code style='font-size:11px;'>{_h(sym)}</code></td>"
                f"<td>{r['etf_count']} 檔</td>"
                f"<td class='shares {cls}'>{shares:+,}</td>"
                f"<td class='etfs-list'>{_h(etfs_str)}</td>"
                f"</tr>"
            )
        return f"""
<table class="etf-table">
  <thead>
    <tr><th>個股</th><th>共識</th><th>張數(7 日)</th><th>ETFs</th></tr>
  </thead>
  <tbody>
{chr(10).join("    " + b for b in body)}
  </tbody>
</table>
"""

    return f"""
<section class="section">
  <h2>⛔ ETF 主動式雙向掃描 <span style="font-size: 12px; color: var(--text-mute); font-weight: normal;">(近 7 日累計)</span></h2>
  <div class="etf-active-grid">
    <div class="etf-buy-block">
      <h3>📈 加碼區 ({len(increase)} 檔)</h3>
      {_table(increase, "buy")}
    </div>
    <div class="etf-sell-block">
      <h3>📉 減碼區 ({len(decrease)} 檔)</h3>
      {_table(decrease, "sell")}
    </div>
  </div>
</section>
"""


def render_other(stocks_list) -> str:
    """區塊 7:其餘品項(C 級無標籤,折疊)"""
    if not stocks_list:
        return ""

    items = []
    for symbol, stock in stocks_list:
        name = stock.get("name", "")
        items.append(f'<li>{_h(name)} <code>{_h(symbol)}</code></li>')

    return f"""
<section class="section">
  <details>
    <summary><h2 style="display: inline; border: none;">📋 其餘品項 ({len(stocks_list)} 檔,預設折疊)</h2></summary>
    <ul class="other-list">
{chr(10).join("      " + it for it in items)}
    </ul>
  </details>
</section>
"""


def _section_empty(title: str, msg: str) -> str:
    return f"""
<section class="section">
  <h2>{title}</h2>
  <div class="empty-state">{_h(msg)}</div>
</section>
"""


# ─── 主入口 ──────────────────────────────────────────────────────────────────

def load_status_map(date: str, docs_root: Path | None = None) -> dict:
    """讀 docs/data/v2/{date}/_index.json 拿到 per-symbol status。
    沒檔案或舊格式 → 回空 dict(render 時 fallback 預設 ready)。"""
    root = docs_root or (PROJECT_ROOT / "docs")
    path = root / "data" / "v2" / date / "_index.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("symbols", {}) or {}
    except Exception:
        return {}


def render(filtered_result: dict, status_map: dict | None = None) -> str:
    """產出完整 HTML 字串。

    status_map: from load_status_map(date),per-symbol status from _index.json
    若 None,則自動讀 docs/data/v2/{date}/_index.json。
    """
    date = filtered_result.get("date", "?")
    stocks = filtered_result.get("stocks", {})
    etf_active = filtered_result.get("etf_active", {"increase": [], "decrease": []})
    metadata = filtered_result.get("metadata", {})

    if status_map is None:
        status_map = load_status_map(date)

    buckets = classify_stocks(stocks)

    etf_delayed = metadata.get("etf_delayed")
    etf_warn = ""
    if etf_delayed:
        etf_max = metadata.get("etf_max_date_in_db", "?")
        etf_warn = f"""
<div style="background:#fef3c7;border-left:4px solid #f59e0b;padding:8px 12px;margin-bottom:12px;border-radius:4px;font-size:13px;">
  ⚠️ ETF 籌碼資料延遲(最新:{_h(etf_max)},顯示資料日:{_h(date)})
</div>
"""

    parts = [
        render_top10(stocks, date, status_map),
        render_themes(date),
        render_grade_section("S", "🔴 S 級戰區",     buckets["S"], date, status_map),
        render_grade_section("A", "🟡 A 級戰區",     buckets["A"], date, status_map),
        render_grade_section("B", "🟢 B 級戰區",     buckets["B"], date, status_map),
        render_c_special(buckets["C_special"]),
        render_etf_active(etf_active, stocks),
        render_other(buckets["C_other"]),
    ]

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = "\n".join(parts)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>台股動能儀表板 {_h(date)} — Stage 8 v2.1</title>
  <link rel="stylesheet" href="assets/style_v2.css">
</head>
<body>

<header class="page-header">
  <div class="container">
    <nav class="page-nav">
      <a href="index.html">← 回首頁</a>
      <span class="page-nav-current">📈 儀表板</span>
      <a href="watchlist_v2.html">📋 Watchlist</a>
      <a href="tags.html">🔥 主題熱度</a>
    </nav>
    <h1>🧭 台股右側動能作戰儀表板</h1>
    <div class="meta">
      資料日期 <strong>{_h(date)}</strong> ｜ 版本 v2.1 ｜ 個股 {len(stocks)} 檔 ｜ 產出時間 {generated_at}
    </div>
  </div>
</header>

<main class="container">
{etf_warn}
{body}
</main>

<script src="assets/chart_v2.js" defer></script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",   required=True)
    parser.add_argument("--result", default=str(PROJECT_ROOT / "filtered_result_v2.json"))
    parser.add_argument("--output", default=str(PROJECT_ROOT / "docs" / "index_v2.html"))
    args = parser.parse_args()

    with open(args.result, encoding="utf-8") as f:
        filtered_result = json.load(f)

    html = render(filtered_result)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 寫入 {out_path}")


if __name__ == "__main__":
    main()
