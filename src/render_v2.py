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
sys.path.insert(0, str(PROJECT_ROOT))

from src import site_meta
from src import asset_version


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


def _scorebar_class(score: float) -> str:
    """分數條色階(交接包 §4:滿分→低分四段藍階)。"""
    if score >= 2:
        return "sb-100"
    if score >= 1.5:
        return "sb-75"
    if score >= 1:
        return "sb-50"
    return "sb-25"


def _format_breakdown_by_module(details: list[dict], score: float = 0.0,
                                grade: str = "") -> str:
    """計分明細表(交接包 §4「無黑箱」,2026-07-07 Batch2 對齊):
    每列 = 分數(mono 右對齊)+ 分數條(寬=分值/2 比例,藍階)+ 規則名稱(佐證值接後);
    底部合計列「9.5 → S」;未觸發類別收進註腳(§3 --text-faint)。
    MA 多筆合併維持既有行為(數值內容不變,只改排版)。"""
    by_mod: dict[str, list[dict]] = {}
    for d in details:
        by_mod.setdefault(d.get("module", "?"), []).append(d)

    rows: list[str] = []
    untouched: list[str] = []

    def _row(sc: float, reason_html: str, title: str = "") -> str:
        pct = max(6, min(100, round(sc / 2 * 100)))          # 寬=分值/2,下限保條可見
        t = f' title="{_h(title)}"' if title else ""
        return (f'<div class="bd2-row"{t}>'
                f'<span class="bd2-score">+{sc:.1f}</span>'
                f'<span class="bd2-bar"><i class="{_scorebar_class(sc)}" style="width:{pct}%"></i></span>'
                f'<span class="bd2-name">{reason_html}</span>'
                f'</div>')

    for label, modules in MODULE_CATEGORIES:
        items = [d for m in modules for d in by_mod.get(m, [])]
        if not items:
            untouched.append(label)
            continue
        if label == "關鍵價/MA":
            ma_entries = [d for d in items if d.get("module") == "ma"]
            merged = _merge_ma_entries(ma_entries)
            if merged:
                ma_total = sum(d.get("score", 0) for d in ma_entries)
                rows.append(_row(ma_total, merged))
            for d in items:
                if d.get("module") == "ma":
                    continue
                rows.append(_row(d.get("score", 0), _h(d.get("reason", "")),
                                 title=d.get("reason", "")))
        else:
            for d in items:
                rows.append(_row(d.get("score", 0), _h(d.get("reason", "")),
                                 title=d.get("reason", "")))

    grade_cls = _h(grade) if grade else ""
    total_row = (f'<div class="bd2-total">'
                 f'<span class="bd2-total-label">合計</span>'
                 f'<span class="bd2-total-val">{score:.1f}'
                 f' <span class="bd2-arrow">→</span>'
                 f' <span class="bd2-grade g-{grade_cls}">{grade_cls or "?"}</span></span>'
                 f'</div>')
    foot = (f'<div class="bd2-untouched">未觸發:{_h("、".join(untouched))}</div>'
            if untouched else "")
    return (f'<div class="score-breakdown-v2">'
            f'<div class="bd2-head">計分明細</div>'
            + "".join(rows) + total_row + foot + '</div>')


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
    # §13(Batch4):熱度條 track=scorebar-track,填色按排名 4 段藍階,寬=漲幅/榜首漲幅
    top_ret = max((t.get("return_pct") or 0.0) for t in rankable[:top_n]) or 1.0
    def _heat_cls(i):
        return "sb-100" if i <= 3 else ("sb-75" if i <= 5 else ("sb-50" if i <= 7 else "sb-25"))
    for i, t in enumerate(rankable[:top_n], 1):
        ret = t.get("return_pct") or 0.0
        cls = "gain" if ret > 0 else ("loss" if ret < 0 else "flat")
        sign = "+" if ret > 0 else ""
        n = t.get("n_traded", 0)
        width = max(4, min(100, round(ret / top_ret * 100))) if top_ret > 0 else 4
        name_cls = "th-top" if i <= 3 else ""
        items.append(f"""
  <li>
    <span class="rank">{i}</span>
    <span class="name {name_cls}">{_h(t.get("tag", ""))}</span>
    <span class="theme-heat"><i class="{_heat_cls(i)}" style="width:{width}%"></i></span>
    <span class="theme-return {cls}">{sign}{ret:.2f}%</span>
    <span class="theme-n">n={n}</span>
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


# (render_top10 已由 §9 前十名排行條取代——Batch3;舊卡片區含重複 chart placeholder 一併移除)
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
    # ready(或沒 status entry → 預設 ready);§3 等待態文案 mono 置中
    return (f'<div id="{_h(elem_id)}" class="chart-placeholder"'
            f' data-symbol="{_h(symbol)}" data-date="{_h(date)}">'
            f'點此載入 K 線'
            f'</div>')


# ── 收盤/漲跌顯示資料(§2 折疊列;display-only,read-only 讀 kline.db)─────────
_CLOSE_MAP: dict[str, tuple] = {}


def load_close_map(kline_db: str, date: str) -> dict:
    """{symbol: (close, chg_pct)}——§2 折疊列的收盤+漲跌幅,純顯示,不進任何計分。"""
    import sqlite3
    out: dict[str, tuple] = {}
    try:
        conn = sqlite3.connect(f"file:{kline_db}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT symbol, date, close FROM kline WHERE date <= ? "
            "AND date >= date(?, '-14 day') ORDER BY symbol, date", (date, date)).fetchall()
        conn.close()
    except Exception:
        return out
    by_sym: dict[str, list] = {}
    for sym, d, c in rows:
        by_sym.setdefault(sym, []).append((d, c))
    for sym, seq in by_sym.items():
        seq = [x for x in seq if x[1] is not None]
        if not seq or seq[-1][0] != date:
            continue
        close = seq[-1][1]
        prev = seq[-2][1] if len(seq) >= 2 else None
        chg = round((close - prev) / prev * 100, 2) if prev else None
        out[sym] = (close, chg)
    return out


def _close_cell(symbol: str) -> str:
    """折疊列右側:收盤 + 漲跌幅(唯一紅綠語意);無資料回空。"""
    v = _CLOSE_MAP.get(symbol)
    if not v:
        return ""
    close, chg = v
    close_txt = f"{close:,.1f}" if close >= 100 else f"{close:,.2f}"
    if chg is None:
        return f'<span class="sc-close">{close_txt}</span>'
    cls = "up" if chg > 0 else ("down" if chg < 0 else "flat")
    arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "")
    return (f'<span class="sc-close">{close_txt}</span>'
            f'<span class="sc-chg {cls}">{arrow}{abs(chg):.2f}%</span>')


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

    # §2 折疊列:徽章 → 股名 → 代號 → 標籤 chips → [auto] 收盤+漲跌 → 總分 → ▾
    tag_chips = "".join(f'<span class="tag-chip">{_h(t.split()[0])}</span>' for t in tags[:3])

    # §4 計分明細(左欄)+ §3 右欄(chart 容器;籌碼小區由 chart_v2.js 掛在其後)
    tag_html = "".join(f'<span class="tag">{_h(t)}</span>' for t in tags)
    if not tag_html:
        tag_html = '<span class="tag tag-none">(無標籤)</span>'
    placeholder_html = chart_placeholder_html(symbol, date, status_entry)

    # 零觸發邊界(2026-07-08 用戶實測,達發 6526 例;拍板 b 案):
    # 明細無任何觸發項 → 左欄改一行註腳(不渲染空明細表),卡轉單欄、K 線全寬。
    if details:
        left_html = (f'{_format_breakdown_by_module(details, score=score, grade=grade)}\n'
                     f'        <div class="card-sector">板塊:{_h(sector)}</div>\n'
                     f'        <div class="tags">{tag_html}</div>')
        grid_cls = "card-grid"
        left_cls = "card-left"
    else:
        left_html = (f'<span class="bd2-none">本日無計分觸發</span>\n'
                     f'        <span class="card-sector">板塊:{_h(sector)}</span>\n'
                     f'        <span class="tags">{tag_html}</span>')
        grid_cls = "card-grid single-col"
        left_cls = "card-left card-left-slim"

    return f"""
<details class="stock-card grade-{_h(grade)}" data-symbol="{_h(symbol)}" id="card-{_h(symbol.replace(':','_'))}">
  <summary>
    <span class="grade-badge {_h(grade)}">{_h(grade)}</span>
    <span class="stock-name">{_h(name)}</span>
    <code class="stock-code">{_h(symbol)}</code>
    <span class="stock-tags-inline">{tag_chips}</span>
    <span class="sc-right">{_close_cell(symbol)}<span class="stock-score">{score:.1f}</span></span>
    <span class="sc-caret">▾</span>
  </summary>
  <div class="card-body">
    <div class="{grid_cls}">
      <div class="{left_cls}">
        {left_html}
      </div>
      <div class="card-right">
        {placeholder_html}
      </div>
    </div>
  </div>
</details>
"""


def render_grade_section(grade: str, label: str, stocks_list, date: str,
                           status_map: dict | None = None) -> str:
    """區塊 2-4:S/A/B 級戰區(Batch3:空戰區不渲染——安靜日由狀態列傳達,對齊交接包)"""
    if not stocks_list:
        return ""

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
    # Batch3 §11:「跌破」移出 C 特殊(獨立跌破警示表顯示,避免同資訊雙列)
    C_GROUP_DEFS: list[tuple[str, tuple[str, ...]]] = [
        ("🟢 站穩",          ("站穩",)),
        ("⚡ MACD 動能轉多",  ("MACD 動能轉多",)),
        ("⭐ 個股輪動",       ("個股輪動",)),
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
        # §6.1#2:ETF 掃描到的個股可能不在觀察名單(stocks 無 name)→ fallback 代號 + 標記,
        # 避免空白 <strong></strong>
        nm = stocks.get(sym, {}).get("name", "")
        if nm:
            return nm
        return f'{sym.split(":")[-1]}(非觀察名單)'

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
    """§14 其餘品項(折疊列 surface-sunken;展開 = chip 流式排列,點擊跳 Watchlist)。"""
    if not stocks_list:
        return ""

    chips = []
    for symbol, stock in stocks_list:
        sid = symbol.replace(":", "_")
        chips.append(
            f'<a class="other-chip" href="watchlist_v2.html#card-{_h(sid)}">'
            f'{_h(stock.get("name",""))} <code>{_h(symbol.split(":")[-1])}</code></a>')

    return f"""
<details class="other-fold">
  <summary>📋 其餘品項 · {len(stocks_list)} 檔 · 未觸發任何規則<span class="of-caret">▾</span></summary>
  <div class="other-chips">{''.join(chips)}</div>
</details>
"""


# ─── Batch 3 儀表板版面(交接包 §8/§9/§11/§14)──────────────────────────────

def _parse_breakdown_tags(stocks: dict) -> list[dict]:
    """從 tags_today 撈「🔴 跌破」→ [{symbol,name,pos,score}](§11 跌破警示表資料)。"""
    out = []
    for symbol, st in stocks.items():
        for t in st.get("tags_today", []):
            if "🔴 跌破" not in t:
                continue
            pos = t.replace("🔴 跌破", "").strip()
            out.append({"symbol": symbol, "name": st.get("name", ""),
                        "pos": pos, "score": st.get("score", 0)})
            break
    out.sort(key=lambda x: x["pos"])
    return out


def render_status_bar(buckets: dict, stocks: dict, status_map: dict,
                      etf_active: dict, date: str) -> str:
    """§8 狀態列(儀表板置頂結論):安靜/熱鬧/殘缺三態。
    殘缺 = 任一 symbol waiting_us_close(19:00 常態,05:30 重render 後自動還原)。"""
    n = {g: len(buckets[g]) for g in ("S", "A", "B")}
    n_c = len(buckets["C_special"]) + len(buckets["C_other"])
    waiting = sum(1 for v in (status_map or {}).values()
                  if (v or {}).get("status") == "waiting_us_close")
    breakdowns = _parse_breakdown_tags(stocks)
    inc = len(etf_active.get("increase", []))
    dec = len(etf_active.get("decrease", []))
    top = max(stocks.items(), key=lambda x: x[1].get("score", 0), default=None)

    hot = n["S"] >= 1 or n["A"] >= 1
    if hot:
        zone = buckets["S"] or buckets["A"]
        g = "S" if buckets["S"] else "A"
        names = " · ".join(
            f'<a class="sb-anchor" href="#card-{_h(s.replace(":", "_"))}">{_h(st.get("name",""))}'
            f' {st.get("score",0):.1f}</a>' for s, st in zone[:3])
        dot = '<span class="sb-dot hot">●</span>'
        main = f'今日 {g} 級 {len(zone)} 檔 — {names}'
    elif waiting:
        dot = '<span class="sb-dot broken">◌</span>'
        main = "資料部分缺漏 — 美股待 08:30"
    else:
        dot = '<span class="sb-dot">●</span>'
        main = "今日無 S/A/B 級 — 沒有需要注意的訊號"

    subs = []
    if top and not hot:
        subs.append(f"最高分 {top[1].get('score',0):.1f}({top[1].get('grade','?')} 級)")
    if breakdowns:
        subs.append(f"跌破警示 {len(breakdowns)} 檔")
    subs.append(f"ETF 加碼 {inc} / 減碼 {dec}")
    if buckets["C_other"]:
        subs.append(f"低分折疊 {len(buckets['C_other'])} 檔")
    if waiting and hot:
        subs.append(f"美股 {waiting} 檔待 08:30")

    badges = "".join(
        f'<span class="sb-badge {"g-" + g if cnt else "g-zero"}">{g} {cnt}</span>'
        for g, cnt in (("S", n["S"]), ("A", n["A"]), ("B", n["B"]), ("C", n_c)))
    return f"""
<section class="status-bar{' status-hot' if hot else (' status-broken' if waiting and not hot else '')}">
  <div class="sb-main">{dot}<span class="sb-title">{main}</span>
    <span class="sb-badges">{badges}</span></div>
  <div class="sb-sub">{_h(" · ".join(subs))}</div>
</section>"""


def render_ranking_bar(stocks: dict, buckets: dict) -> str:
    """§9 前十名排行條(取代前十名卡片區;移除舊 top10 重複 chart placeholder)。
    S/A/B 錨點跳本頁戰區卡;C/D 無本頁卡 → 跳 Watchlist 該檔。"""
    ranked = sorted(stocks.items(), key=lambda x: -x[1].get("score", 0))[:10]
    if not ranked:
        return ""
    carded = {s for g in ("S", "A", "B") for s, _ in buckets[g]}
    items = []
    for i, (symbol, st) in enumerate(ranked, 1):
        sid = symbol.replace(":", "_")
        href = f"#card-{sid}" if symbol in carded else f"watchlist_v2.html#card-{sid}"
        dim = ' rb-dim' if st.get("grade") in ("D",) else ""
        items.append(
            f'<span class="rb-item{dim}"><span class="rb-rank">{i}</span>'
            f'<a class="rb-name" href="{_h(href)}" data-rb-target="{_h(sid)}">{_h(st.get("name",""))}</a>'
            f'<span class="rb-score">{st.get("score",0):.1f}</span></span>')
    return (f'<section class="section ranking-bar"><span class="rb-head">🏆 前十名</span>'
            + "".join(items) + '</section>')


def render_breakdown_alerts(stocks: dict) -> str:
    """§11 跌破警示表:grade 徽章合法用綠(空方語意);分數欄 0.0 faint。"""
    rows = _parse_breakdown_tags(stocks)
    if not rows:
        return ""
    trs = []
    for r in rows:
        sym = r["symbol"]
        pos = r["pos"]
        pos_html = f'▼ {_h(pos)}' if not pos.startswith("區域") else f'▼ {_h(pos)}'
        trs.append(
            f'<div class="bk-row">'
            f'<span class="bk-name">{_h(r["name"])}</span>'
            f'<code class="bk-code">{_h(sym)}</code>'
            f'<span class="bk-pos">{pos_html}</span>'
            f'<span class="bk-close">{_close_cell(sym)}</span>'
            f'<span class="bk-score">{r["score"]:.1f}</span>'
            f'</div>')
    return f"""
<section class="section" id="breakdown-alerts">
  <h2>跌破警示 <span class="bk-badge">▼ {len(rows)} 檔</span>
    <span class="bk-note">收盤跌破手繪關鍵價/區域,空方訊號觀察</span></h2>
  <div class="bk-table">
    <div class="bk-row bk-head"><span>股名</span><span>代號</span><span>跌破位置</span>
      <span class="bk-close">收盤 / 漲跌</span><span class="bk-score">分數</span></div>
    {''.join(trs)}
  </div>
</section>"""


def render_news_shell(default_open: bool) -> str:
    """§10 新聞區塊殼(內容由 events.js fetch news.json 填;08:30 更新不需重 render)。
    預設:熱鬧日展開、安靜日收合(模板參數);使用者操作記 localStorage。"""
    return (f'<section class="section" id="news-block" hidden '
            f'data-default-open="{"1" if default_open else "0"}">'
            f'<div class="news-head" id="news-head"><h2>📰 今日新聞 <span class="news-meta" id="news-meta"></span></h2>'
            f'<span class="news-peek" id="news-peek"></span>'
            f'<span class="news-caret" id="news-caret">▾</span></div>'
            f'<div class="news-list" id="news-list"></div>'
            f'<div class="news-kwfoot" id="news-kwfoot"></div>'
            f'</section>')


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


def render(filtered_result: dict, status_map: dict | None = None,
           recompute_note: str = "") -> str:
    """產出完整 HTML 字串。

    status_map: from load_status_map(date),per-symbol status from _index.json
    若 None,則自動讀 docs/data/v2/{date}/_index.json。
    recompute_note: 非空時在頁首插入「事後重算」橫幅(停更期間回補 snapshot 用),
                    live 頁不傳此參數,行為不變。
    """
    date = filtered_result.get("date", "?")
    stocks = filtered_result.get("stocks", {})
    etf_active = filtered_result.get("etf_active", {"increase": [], "decrease": []})
    metadata = filtered_result.get("metadata", {})

    # §6.3 meta 列只從 site_meta 取值(版本/檔數/略過)
    meta = site_meta.load(date) or {}
    sm_rule    = meta.get("rule_version", "v2.2")
    sm_tw      = meta.get("tw_count", len(stocks))
    sm_skipped = meta.get("skipped", [])
    sm_skip_txt = f"(略過 {len(sm_skipped)} 檔)" if sm_skipped else ""

    if status_map is None:
        status_map = load_status_map(date)

    buckets = classify_stocks(stocks)

    etf_delayed = metadata.get("etf_delayed")
    etf_warn = ""
    if etf_delayed:
        etf_max = metadata.get("etf_max_date_in_db", "?")
        etf_warn = f"""
<div style="background:var(--surface-sunken);border-left:4px solid var(--accent);color:var(--text-primary);padding:8px 12px;margin-bottom:12px;border-radius:4px;font-size:13px;">
  ⚠️ ETF 籌碼資料延遲(最新:{_h(etf_max)},顯示資料日:{_h(date)})
</div>
"""

    # Batch3(交接包版面序):狀態列 → [總經橫條=events.js 注入 header] → 事件中樞 →
    # 新聞 → 前十名排行條 → S/A/B 戰區 → C 特殊 → 跌破警示 → ETF → 主題 → 其餘折疊
    hot_day = bool(buckets["S"] or buckets["A"])
    parts = [
        render_status_bar(buckets, stocks, status_map, etf_active, date),
        # 📅 事件中樞(events.js client-side fetch events.json 填入;與 render 解耦)
        '<section class="section" id="events-hub" hidden>'
        '<h2>📅 未來 14 天</h2><div class="events-body"></div></section>',
        render_news_shell(default_open=hot_day),          # §10(熱鬧日預設展開)
        render_ranking_bar(stocks, buckets),              # §9(取代前十名卡片區)
        render_grade_section("S", "🔴 S 級戰區",     buckets["S"], date, status_map),
        render_grade_section("A", "🟡 A 級戰區",     buckets["A"], date, status_map),
        render_grade_section("B", "🟢 B 級戰區",     buckets["B"], date, status_map),
        render_c_special(buckets["C_special"]),
        render_breakdown_alerts(stocks),                  # §11 跌破警示表
        render_etf_active(etf_active, stocks),
        render_themes(date),
        render_other(buckets["C_other"]),                 # §14 折疊 chip 流
    ]

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = "\n".join(parts)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>台股動能儀表板 {_h(date)} — 規則 {_h(sm_rule)}</title>
  {asset_version.head_snippet()}
</head>
<body>
{('<div style="background:var(--surface-sunken);color:var(--text-muted);border:1px dashed var(--border-strong);padding:10px 16px;text-align:center;font-size:13px">⚠️ ' + _h(recompute_note) + '</div>') if recompute_note else ''}
<header class="page-header">
  <div class="container">
    <nav class="page-nav">
      <a href="index.html">← 回首頁</a>
      <span class="page-nav-current">📈 儀表板</span>
      <a href="watchlist_v2.html">📋 Watchlist</a>
      <a href="tags.html">🔥 主題熱度</a>
      <a href="weekly.html">📅 週報</a>
      <a href="macro_dashboard.html">🌐 宏觀</a>
    </nav>
    <h1>🧭 台股右側動能作戰儀表板</h1>
    <div class="meta">
      資料日期 <strong>{_h(date)}</strong> ｜ 規則 {_h(sm_rule)} ｜ 台股 {sm_tw} 檔{sm_skip_txt} ｜ 產出時間 {generated_at}{' ｜ <strong style=color:var(--accent-hi)>' + _h(recompute_note) + '</strong>' if recompute_note else ''}
    </div>
  </div>
</header>

<main class="container">
{etf_warn}
{body}
</main>

<script src="{asset_version.versioned('assets/chart_v2.js')}" defer></script>
<script src="{asset_version.versioned('assets/events.js')}" defer></script>
</body>
</html>"""


def write_summary(filtered_result: dict, outdir: Path,
                  recomputed: str = "") -> Path | None:
    """寫 docs/data/v2/{date}/_summary.json = {S,A,B,etf_inc,etf_dec}(§6.1#1 history 摘要)。
    recomputed 非空時加註記(停更回補),render_history 據此在該日標「事後重算」。"""
    date = filtered_result.get("date")
    if not date:
        return None
    counts = {"S": 0, "A": 0, "B": 0}
    for s in filtered_result.get("stocks", {}).values():
        g = s.get("grade")
        if g in counts:
            counts[g] += 1
    ea = filtered_result.get("etf_active", {})
    summary = {**counts,
               "etf_inc": len(ea.get("increase", [])),
               "etf_dec": len(ea.get("decrease", []))}
    if recomputed:
        summary["recomputed"] = recomputed
    day_dir = outdir / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "_summary.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",   required=True)
    parser.add_argument("--result", default=str(PROJECT_ROOT / "filtered_result_v2.json"))
    parser.add_argument("--output", default=str(PROJECT_ROOT / "docs" / "index_v2.html"))
    parser.add_argument("--recompute-note", dest="recompute_note", default="",
                        help="非空 → 頁首加事後重算橫幅 + _summary 標記(停更回補用)")
    parser.add_argument("--kline", default=str(PROJECT_ROOT / "kline.db"),
                        help="§2 折疊列收盤/漲跌顯示來源(read-only,不進計分)")
    args = parser.parse_args()

    with open(args.result, encoding="utf-8") as f:
        filtered_result = json.load(f)

    # §2:載入收盤/漲跌顯示 map(read-only;kline.db 缺/無該日 → 折疊列不顯示,不擋 render)
    _CLOSE_MAP.clear()
    _CLOSE_MAP.update(load_close_map(args.kline, args.date))

    html = render(filtered_result, recompute_note=args.recompute_note)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 寫入 {out_path}")

    # §6.1#1 history 摘要來源:寫 per-date _summary.json(S/A/B + ETF),供 render_history 讀
    # 回補模式把重算日期記進 _summary,render_history 據此標「事後重算」
    recomputed = "2026-07-04" if args.recompute_note else ""
    write_summary(filtered_result, PROJECT_ROOT / "docs" / "data" / "v2",
                  recomputed=recomputed)


if __name__ == "__main__":
    main()
