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

from src.render_v2 import (_h, _safe_id, chart_placeholder_html, load_status_map,
                            load_close_map)
from src import site_meta
from src import asset_version


# ── 結構建構 ─────────────────────────────────────────────────────────────────

def _short_code(full: str) -> str:
    """'TWSE:2330' → '2330'"""
    return full.split(":")[-1]


def _grade_badge(symbol: str, stocks_index: dict) -> str:
    """§15 列首等級徽章;未計分(國際股)顯示中性 C 樣式空缺不佔位。"""
    grade = (stocks_index.get(symbol) or {}).get("grade", "")
    return f'<span class="grade-badge {_h(grade)}">{_h(grade)}</span>' if grade else ""


def _tag_chips(symbol: str, stocks_index: dict) -> str:
    tags = (stocks_index.get(symbol) or {}).get("tags", [])
    return "".join(f'<span class="tag-chip">{_h(t.split()[0])}</span>' for t in tags[:3])


def _close_cells(symbol: str, close_map: dict) -> str:
    """§15:收盤(右對齊)+ 漲跌幅(唯一紅綠);無資料留空欄保持對位。"""
    v = close_map.get(symbol)
    if not v:
        return '<span class="wlc-close"></span><span class="wlc-chg"></span>'
    close, chg = v
    ctxt = f"{close:,.1f}" if close >= 100 else f"{close:,.2f}"
    if chg is None:
        return f'<span class="wlc-close">{ctxt}</span><span class="wlc-chg"></span>'
    cls = "up" if chg > 0 else ("down" if chg < 0 else "flat")
    arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "")
    return (f'<span class="wlc-close">{ctxt}</span>'
            f'<span class="wlc-chg {cls}">{arrow}{abs(chg):.2f}%</span>')


def _etf_summary(symbol: str, etf_map: dict) -> str:
    """§15 ETF 摘要欄([auto] 靠右;<900 隱藏):▲+N·K檔 / ▽-N·K檔。"""
    e = etf_map.get(symbol)
    if not e:
        return ""
    shares = e.get("total_shares", 0)
    n = e.get("etf_count", 0)
    if shares >= 0:
        return f'<span class="wl-etf etf-b">▲ +{shares:,} · {n}檔</span>'
    return f'<span class="wl-etf etf-s">▽ {shares:,} · {n}檔</span>'



def load_subtags_index(subtags_path=None) -> dict:
    """讀 config/subtags.json,回 code -> {chips: [...], all_tags: [...]}。
    chips = L2 前 3 + L4 前 2(混排,最多 5 個)— 顯示用
    all_tags = L2 + L3 + L4 全部 — 搜尋用(寫進 data-tags)
    """
    path = Path(subtags_path) if subtags_path else (PROJECT_ROOT / "config" / "subtags.json")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    index = {}
    for code, info in (data.get("stocks") or {}).items():
        l2 = info.get("L2", []) or []
        l3 = info.get("L3", []) or []
        l4 = info.get("L4", []) or []
        chips = l2[:3] + l4[:2]
        all_tags = l2 + l3 + l4
        index[code] = {"chips": chips, "all_tags": all_tags}
    return index


def render_member(member: dict, leaders_set: set, date: str,
                   stocks_index: dict,
                   status_map: dict | None = None,
                   subtags_map: dict | None = None,
                   close_map: dict | None = None,
                   etf_map: dict | None = None) -> str:
    """單一個股的 <details> 折疊區。status_map 給 chart_placeholder_html 區分
    ready / waiting_us_close / missing。
    subtags_map: from load_subtags_index(),per-code chips + all_tags(2026-06-09)。
    """
    symbol = member["code"]
    name   = member["name"]
    is_leader = symbol in leaders_set
    sid = _safe_id(symbol)
    close_map = close_map if close_map is not None else {}
    etf_map = etf_map if etf_map is not None else {}
    leader_mark = '<span class="leader-mark" title="族群長子">⭐</span>' if is_leader else ""
    placeholder = chart_placeholder_html(
        symbol, date, (status_map or {}).get(sid)
    )

    sub = (subtags_map or {}).get(symbol, {})
    chips = sub.get("chips", [])
    all_tags = sub.get("all_tags", [])

    if chips:
        subtags_html = (
            '<span class="wl-subtags">'
            + " · ".join(f'<span class="wl-subtag">{_h(t)}</span>' for t in chips)
            + '</span>'
        )
    else:
        subtags_html = ""

    # data-code / data-name / data-tags 給前端 search 用(大小寫無關,前端 toLowerCase)
    tags_attr = _h(" ".join(all_tags))
    return f"""
<details class="wl-stock" data-code="{_h(symbol)}" data-symbol="{_h(symbol)}" id="card-{_h(symbol.replace(':','_'))}" data-name="{_h(name)}" data-tags="{tags_attr}">
  <summary>
    {_grade_badge(symbol, stocks_index)}
    {leader_mark}
    <span class="wl-name">{_h(name)}</span>
    <code class="wl-code">{_h(symbol.split(":")[-1])}</code>
    {_close_cells(symbol, close_map)}
    {subtags_html}
    {_tag_chips(symbol, stocks_index)}
    <span class="wl-right">{_etf_summary(symbol, etf_map)}<span class="wl-caret">▾ K線</span></span>
  </summary>
  <div class="wl-stock-body">
    {placeholder}
  </div>
</details>
"""


def render_sector(sector_name: str, sector_raw: dict, date: str,
                   stocks_index: dict, status_map: dict | None = None,
                   subtags_map: dict | None = None,
                   close_map: dict | None = None,
                   etf_map: dict | None = None) -> str:
    """單一板塊:含長子標記 + 全成員 <details>。"""
    members = sector_raw.get("成員", [])
    leaders = set(sector_raw.get("長子", []))
    member_html = "\n".join(
        render_member(m, leaders, date, stocks_index, status_map, subtags_map,
                      close_map, etf_map)
        for m in members
    )
    # §15 板塊標頭:當日均漲跌(成員 close_map 有值者等權平均)
    chgs = [close_map[m["code"]][1] for m in members
            if close_map and m["code"] in close_map and close_map[m["code"]][1] is not None]
    avg = sum(chgs) / len(chgs) if chgs else None
    if avg is None:
        avg_html = ""
    else:
        cls = "up" if avg > 0 else ("down" if avg < 0 else "flat")
        sign = "+" if avg > 0 else ""
        avg_html = f'<span class="wl-sector-avg {cls}">{sign}{avg:.2f}%</span>'
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
      {avg_html}
      {leader_inline}
    </h3>
  </summary>
  {member_html}
</details>
"""


def render_intl_group(group_name: str, group_raw: dict, date: str,
                        stocks_index: dict,
                        status_map: dict | None = None,
                        subtags_map: dict | None = None,
                        close_map: dict | None = None,
                        etf_map: dict | None = None) -> str:
    """國際族群:跟台股板塊類似,但加「對應台股族群」說明。"""
    members = group_raw.get("成員", [])
    leaders = set(group_raw.get("長子", []))
    member_html = "\n".join(
        render_member(m, leaders, date, stocks_index, status_map, subtags_map,
                      close_map, etf_map)
        for m in members
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

def render(watchlist: dict, date: str, filtered_result: dict | None = None,
            status_map: dict | None = None) -> str:
    """產整份 HTML。filtered_result 可選,用來提供個股 score/grade/tags 摘要。
    status_map: from load_status_map(date),per-symbol 資料狀態。
    若 None,自動讀 docs/data/v2/{date}/_index.json。
    """
    stocks_index = (filtered_result or {}).get("stocks", {})
    if status_map is None:
        status_map = load_status_map(date)
    subtags_map = load_subtags_index()
    # §15:收盤/漲跌(read-only 讀 kline.db)+ ETF 摘要(etf_active 雙向表)
    close_map = load_close_map(str(PROJECT_ROOT / "kline.db"), date)
    etf_map = {}
    for side in ("increase", "decrease"):
        for e in (filtered_result or {}).get("etf_active", {}).get(side, []):
            etf_map[e.get("symbol")] = e

    tw_raw   = watchlist.get("台股板塊", {})
    intl_raw = watchlist.get("國際族群", {})

    tw_sectors_html = "\n".join(
        render_sector(name, raw, date, stocks_index, status_map, subtags_map,
                      close_map, etf_map)
        for name, raw in tw_raw.items()
    )
    intl_groups_html = "\n".join(
        render_intl_group(name, raw, date, stocks_index, status_map, subtags_map,
                          close_map, etf_map)
        for name, raw in intl_raw.items()
    )

    # §6.3 meta 列只從 site_meta 取值;watchlist 更新日改用實際 mtime(不再用 stale 內嵌欄位)
    sm = site_meta.load(date) or {}
    tw_total   = sm.get("tw_count", sum(len(s.get("成員", [])) for s in tw_raw.values()))
    intl_total = sm.get("intl_count", sum(len(g.get("成員", [])) for g in intl_raw.values()))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    update_date = sm.get("watchlist_updated", watchlist.get("更新日期", ""))
    sm_rule = sm.get("rule_version", "v2.2")
    sm_skipped = sm.get("skipped", [])
    skip_txt = f" (略過 {len(sm_skipped)} 檔)" if sm_skipped else ""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>觀察名單 — 台股動能 Watchlist</title>
{asset_version.head_snippet()}
<style>
  /* sticky 搜尋 bar(2026-06-07 朋友 review 後加)*/
  .wl-search-bar {{
    position: sticky;
    top: 0;
    z-index: 100;
    background: var(--bg);
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 8px;
    margin: -12px -8px 12px;   /* 反 padding,撐到 .container 邊界 */
  }}
  .wl-search-bar .wl-search-icon {{
    font-size: 16px;
    opacity: 0.6;
    flex-shrink: 0;
  }}
  .wl-search-bar input {{
    flex: 1;
    font-size: 14px;
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--code-bg);
    color: var(--text);
    font-family: inherit;
  }}
  .wl-search-bar input:focus {{
    outline: none;
    border-color: var(--etf-buy);
    background: var(--surface-panel);
  }}
  .wl-search-bar .wl-search-clear {{
    background: var(--code-bg);
    border: 1px solid var(--border);
    padding: 6px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
    color: var(--text-mute);
    line-height: 1;
  }}
  .wl-search-bar .wl-search-clear:hover {{
    background: var(--border);
    color: var(--text);
  }}
  .wl-search-stats {{
    font-size: 11px;
    color: var(--text-mute);
    flex-shrink: 0;
    min-width: 60px;
    text-align: right;
  }}
  .wl-no-results {{
    text-align: center;
    padding: 32px 16px;
    color: var(--text-mute);
    font-style: italic;
    font-size: 14px;
  }}
  /* 副標籤 chip(2026-06-09)— L2 前 3 + L4 前 2,灰色小字 */
  .wl-subtags {{
    color: var(--text-mute);
    font-size: 11px;
    margin-left: 4px;
    word-break: keep-all;
  }}
  .wl-subtag {{
    display: inline;
  }}
  .wl-subtag + .wl-subtag {{
    margin-left: 0;
  }}
  /* 手機收緊 */
  @media (max-width: 480px) {{
    .wl-search-stats {{ display: none; }}
    .wl-subtags {{
      display: block;
      flex-basis: 100%;
      margin-left: 0;
      margin-top: 2px;
      font-size: 10px;
    }}
  }}
</style>
</head>
<body>

<header class="page-header">
  <div class="container">
    <nav class="page-nav">
      <a href="index.html">← 回首頁</a>
      <a href="index_v2.html">📈 儀表板</a>
      <span class="page-nav-current">📋 Watchlist</span>
      <a href="tags.html">🔥 主題熱度</a>
      <a href="weekly.html">📅 週報</a>
    </nav>
    <h1>📋 觀察名單 Watchlist</h1>
    <div class="meta">
      資料日期 <strong>{_h(date)}</strong> ｜ 規則 {_h(sm_rule)} ｜
      台股 {tw_total} 檔<span title="{_h('略過: ' + ', '.join(sm_skipped)) if sm_skipped else ''}">{_h(skip_txt)}</span> ｜ 國際 {intl_total} 檔 ｜
      watchlist 更新日 {_h(update_date)} ｜ 產出時間 {generated_at}
    </div>
  </div>
</header>

<main class="container">

<div class="wl-search-bar">
  <span class="wl-search-icon">🔍</span>
  <input type="text" id="wl-search" placeholder="搜尋名稱 / 代號 / 副標籤(例:廣達、2382、NVDA、HBM、老AI)"
         autocomplete="off" spellcheck="false">
  <button type="button" class="wl-search-clear" id="wl-search-clear" hidden>×</button>
  <span class="wl-search-stats" id="wl-search-stats"></span>
</div>

<div class="wl-no-results" id="wl-no-results" hidden>找不到符合的個股</div>

<section class="section" id="wl-section-tw">
  <h2>📊 台股板塊 ({len(tw_raw)} 板塊 / <span id="wl-stats-tw">{tw_total}</span> 檔)</h2>
{tw_sectors_html}
</section>

<section class="section" id="wl-section-intl">
  <h2>🌏 國際族群 ({len(intl_raw)} 群 / <span id="wl-stats-intl">{intl_total}</span> 檔)</h2>
{intl_groups_html}
</section>

</main>

<script src="{asset_version.versioned('assets/chart_v2.js')}" defer></script>
<script src="{asset_version.versioned('assets/events.js')}" defer></script>
<script>
(function() {{
  // 純前端 search(2026-06-07):toLowerCase 大小寫無關;名稱 / 代號雙欄掃描;
  // 板塊全空 → 隱藏整個 sector;結果為 0 → 顯示「找不到」橫幅。
  const input    = document.getElementById('wl-search');
  const clearBtn = document.getElementById('wl-search-clear');
  const statsEl  = document.getElementById('wl-search-stats');
  const twStats  = document.getElementById('wl-stats-tw');
  const intlStats= document.getElementById('wl-stats-intl');
  const twSec    = document.getElementById('wl-section-tw');
  const intlSec  = document.getElementById('wl-section-intl');
  const noResults= document.getElementById('wl-no-results');
  const stocks   = document.querySelectorAll('details.wl-stock');
  const sectors  = document.querySelectorAll('details.wl-sector');

  function applyFilter() {{
    const q = input.value.trim().toLowerCase();
    clearBtn.hidden = !q;

    let twHit = 0, intlHit = 0;
    stocks.forEach(el => {{
      const code = (el.dataset.code || '').toLowerCase();
      const name = (el.dataset.name || '').toLowerCase();
      const tags = (el.dataset.tags || '').toLowerCase();
      const hit = !q || code.includes(q) || name.includes(q) || tags.includes(q);
      el.hidden = !hit;
      if (hit) {{
        if (twSec.contains(el)) twHit++;
        else if (intlSec.contains(el)) intlHit++;
      }}
    }});

    // 板塊全空 → 隱藏整個 sector
    sectors.forEach(sec => {{
      const anyVisible = Array.from(
        sec.querySelectorAll('details.wl-stock')
      ).some(s => !s.hidden);
      sec.hidden = !anyVisible;
    }});

    const total = twHit + intlHit;
    twStats.textContent = twHit;
    intlStats.textContent = intlHit;
    statsEl.textContent = q ? `${{total}} 檔` : '';
    twSec.hidden = twHit === 0;
    intlSec.hidden = intlHit === 0;
    noResults.hidden = total > 0;
  }}

  input.addEventListener('input', applyFilter);
  clearBtn.addEventListener('click', () => {{
    input.value = '';
    applyFilter();
    input.focus();
  }});
  // Esc 清空
  input.addEventListener('keydown', e => {{
    if (e.key === 'Escape' && input.value) {{
      input.value = '';
      applyFilter();
    }}
  }});
}})();
</script>
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
