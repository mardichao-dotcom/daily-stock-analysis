"""
render_themes_v2.py — 主題熱度詳情頁

讀 docs/data/v2/<date>/theme_returns.json,產出 docs/tags.html。

結構:
  - sticky 排序 bar(按漲幅 / 按檔數)
  - ✅ 上榜標籤(N>=3,details 折疊展開成員)
  - ⚠ 未上榜標籤(N<3,僅展示用)

純 HTML/CSS/JS,不需 chart_v2.js。

CLI:
  python3 -m src.render_themes_v2 --date 2026-06-08
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.render_v2 import _h
from src import site_meta
from src import asset_version


def _ret_class(ret):
    if ret is None:
        return "flat"
    if ret > 0:
        return "gain"
    if ret < 0:
        return "loss"
    return "flat"


def _ret_text(ret):
    if ret is None:
        return "—"
    sign = "+" if ret > 0 else ""
    return f"{sign}{ret:.2f}%"


def render_member_li(member: dict) -> str:
    code = member.get("code", "")
    name = member.get("name", code)
    ret = member.get("return_pct")
    excluded = member.get("excluded", False)

    if excluded:
        return (f'<li class="theme-member excluded">'
                f'<span class="m-return flat">—</span>'
                f'<code class="m-code">{_h(code)}</code>'
                f'<span class="m-name">{_h(name)}</span>'
                f'<span class="m-note">停牌</span>'
                f'</li>')

    return (f'<li class="theme-member">'
            f'<span class="m-return {_ret_class(ret)}">{_ret_text(ret)}</span>'
            f'<code class="m-code">{_h(code)}</code>'
            f'<span class="m-name">{_h(name)}</span>'
            f'</li>')


def render_tag_card(idx: int, tag: dict, rankable: bool) -> str:
    name = tag.get("tag", "?")
    ret = tag.get("return_pct")
    n = tag.get("n", 0)
    n_traded = tag.get("n_traded", 0)
    n_excluded = tag.get("n_excluded", 0)

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rank_badge = medals.get(idx, f"#{idx}") if rankable else f"n={n}"

    excluded_note = f' <span class="theme-excluded-note">{n_excluded} 停牌</span>' if n_excluded else ""

    members_html = "\n".join(render_member_li(m) for m in tag.get("members", []))

    ret_for_sort = ret if ret is not None else -999
    # §13(Batch4):熱度條(寬由前端以榜首為基準設定,data-heat 已備)
    heat_cls = ("sb-100" if (rankable and idx <= 3) else ("sb-75" if (rankable and idx <= 5)
               else ("sb-50" if (rankable and idx <= 7) else "sb-25")))
    heat = (f'<span class="theme-heat"><i class="{heat_cls}" data-heat="{max(ret or 0, 0):.4f}"></i></span>'
            if rankable else "")
    return f"""
<details class="theme-card" data-return="{ret_for_sort:.4f}" data-n="{n}" data-name="{_h(name)}">
  <summary>
    <span class="theme-rank">{rank_badge}</span>
    <span class="theme-tag-name{' th-top' if rankable and idx <= 3 else ''}">{_h(name)}</span>
    {heat}
    <span class="theme-return {_ret_class(ret)}">{_ret_text(ret)}</span>
    <span class="theme-n">(n={n_traded}{('/' + str(n)) if n_excluded else ''}){excluded_note}</span>
  </summary>
  <ul class="theme-members">
{members_html}
  </ul>
</details>
"""


def render(data: dict) -> str:
    date = data.get("date", "?")
    generated_at = data.get("generated_at", datetime.now().isoformat(timespec="seconds"))
    sm_rule = (site_meta.load(date) or {}).get("rule_version", "v2.2")   # §6.3 版本單一來源
    stats = data.get("stats", {})
    rules = data.get("rules", {})

    tags = data.get("tags", [])
    rankable_tags = [t for t in tags if t.get("rankable")]
    unrankable_tags = [t for t in tags if not t.get("rankable")]

    rankable_html = "\n".join(
        render_tag_card(i, t, rankable=True)
        for i, t in enumerate(rankable_tags, 1)
    ) if rankable_tags else '<div class="empty-state">無上榜標籤</div>'

    unrankable_html = "\n".join(
        render_tag_card(i, t, rankable=False)
        for i, t in enumerate(unrankable_tags, 1)
    ) if unrankable_tags else '<div class="empty-state">無未上榜標籤</div>'

    n_threshold = rules.get("n_threshold", 3)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>主題熱度排行 — 台股動能</title>
{asset_version.head_snippet()}
<style>
  .themes-page-bar {{
    position: sticky;
    top: 0;
    z-index: 100;
    background: var(--bg);
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 12px;
    margin: -12px -8px 12px;
  }}
  .themes-page-bar .sort-label {{
    font-size: 13px;
    color: var(--text-mute);
    flex-shrink: 0;
  }}
  .themes-page-bar button {{
    background: var(--code-bg);
    border: 1px solid var(--border);
    padding: 6px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 13px;
    color: var(--text);
    font-family: inherit;
  }}
  .themes-page-bar button:hover {{
    background: var(--border);
  }}
  .themes-page-bar button.active {{
    background: var(--accent);
    color: var(--grade-s-text);
    border-color: var(--accent);
  }}
  .themes-page-bar .stats {{
    margin-left: auto;
    font-size: 12px;
    color: var(--text-mute);
    text-align: right;
  }}
  details.theme-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    margin: 6px 0;
    transition: background 0.1s;
  }}
  details.theme-card[open] {{
    background: var(--surface-hover);
  }}
  .theme-card > summary {{
    padding: 10px 12px;
    display: flex;
    align-items: center;
    gap: 12px;
    cursor: pointer;
    list-style: none;
    user-select: none;
  }}
  .theme-card > summary::-webkit-details-marker {{ display: none; }}
  .theme-card > summary::after {{
    content: "▼";
    color: var(--text-mute);
    font-size: 10px;
    margin-left: auto;
    transition: transform 0.15s;
  }}
  .theme-card[open] > summary::after {{
    transform: rotate(180deg);
  }}
  .theme-card .theme-rank {{
    font-weight: bold;
    color: var(--text-mute);
    width: 38px;
    text-align: center;
    flex-shrink: 0;
    font-size: 14px;
  }}
  .theme-card .theme-return {{
    font-weight: bold;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    width: 80px;
    text-align: right;
    flex-shrink: 0;
    font-size: 15px;
  }}
  .theme-return.gain {{ color: var(--color-up); }}   /* §6.1#3 台式紅漲 */
  .theme-return.loss {{ color: var(--color-down); }}   /* 綠跌 */
  .theme-return.flat {{ color: var(--text-mute); }}
  .theme-card .theme-tag-name {{
    font-weight: 600;
    flex: 1;
    font-size: 15px;
  }}
  .theme-card .theme-n {{
    color: var(--text-mute);
    font-size: 12px;
    flex-shrink: 0;
  }}
  .theme-excluded-note {{
    color: var(--text-faint);
    margin-left: 4px;
  }}
  ul.theme-members {{
    list-style: none;
    margin: 0;
    padding: 4px 12px 12px 50px;
    border-top: 1px solid var(--border);
  }}
  li.theme-member {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 4px 0;
    font-size: 13px;
  }}
  li.theme-member.excluded {{
    opacity: 0.55;
  }}
  .m-return {{
    width: 64px;
    text-align: right;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    flex-shrink: 0;
  }}
  .m-return.gain {{ color: var(--color-up); }}   /* §6.1#3 台式紅漲 */
  .m-return.loss {{ color: var(--color-down); }}   /* 綠跌 */
  .m-return.flat {{ color: var(--text-mute); }}
  .m-code {{
    color: var(--text-mute);
    font-size: 12px;
    font-family: monospace;
    width: 110px;
    flex-shrink: 0;
  }}
  .m-name {{ flex: 1; }}
  .m-note {{
    color: var(--text-mute);
    font-size: 12px;
  }}
  .section-divider {{
    margin: 24px 0 12px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
    color: var(--text-mute);
    font-size: 13px;
  }}
  .empty-state {{
    padding: 24px;
    text-align: center;
    color: var(--text-mute);
    font-style: italic;
  }}
  @media (max-width: 480px) {{
    .themes-page-bar .stats {{ display: none; }}
    .theme-card .theme-tag-name {{ font-size: 14px; }}
    .m-code {{ width: 90px; font-size: 11px; }}
    ul.theme-members {{ padding-left: 14px; }}
  }}
</style>
</head>
<body>

<header class="page-header">
  <div class="container">
    <nav class="page-nav">
      <a href="index.html">← 回首頁</a>
      <a href="index_v2.html">📈 儀表板</a>
      <a href="watchlist_v2.html">📋 Watchlist</a>
      <span class="page-nav-current">🔥 主題熱度</span>
      <a href="weekly.html">📅 週報</a>
    </nav>
    <h1>🔥 主題熱度排行</h1>
    <div class="meta">
      資料日期 <strong>{_h(date)}</strong> ｜ 規則 {_h(sm_rule)} ｜
      上榜 <strong>{len(rankable_tags)}</strong> 個(N≥{n_threshold})｜
      未上榜 {len(unrankable_tags)} 個 ｜
      產出時間 {generated_at[:16].replace('T', ' ')}
    </div>
  </div>
</header>

<main class="container">

<div class="themes-page-bar">
  <span class="sort-label">排序:</span>
  <button type="button" id="sort-return" class="active">按漲幅 ▼</button>
  <button type="button" id="sort-n">按檔數 ▼</button>
  <button type="button" id="expand-all">全部展開</button>
  <button type="button" id="collapse-all">全部收合</button>
  <span class="stats" id="stats">{len(rankable_tags)} 上榜 / {len(unrankable_tags)} 未上榜</span>
</div>

<section class="section">
  <h2>✅ 上榜標籤(N≥{n_threshold},{len(rankable_tags)} 個)</h2>
  <div id="rankable-list">
{rankable_html}
  </div>
</section>

<section class="section">
  <h2>⚠️ 未上榜標籤(N&lt;{n_threshold},{len(unrankable_tags)} 個 — 展示用,未來支援異常提醒)</h2>
  <div id="unrankable-list">
{unrankable_html}
  </div>
</section>

</main>

<script>
// §13:熱度條寬度 = 漲幅 / 榜首漲幅(負漲幅最小寬)
(function () {{
  var bars = document.querySelectorAll('.theme-heat i[data-heat]');
  var top = 0;
  bars.forEach(function (b) {{ top = Math.max(top, parseFloat(b.dataset.heat) || 0); }});
  bars.forEach(function (b) {{
    var r = parseFloat(b.dataset.heat) || 0;
    b.style.width = (top > 0 ? Math.max(4, Math.min(100, Math.round(r / top * 100))) : 4) + '%';
  }});
}})();
</script>
<script>
(function() {{
  // 排序 + 展開收合(2026-06-09)
  // 對 rankable / unrankable 兩個 list 各別 reorder,並重算 #rank badge。
  const buttons = {{
    "sort-return": document.getElementById("sort-return"),
    "sort-n":      document.getElementById("sort-n"),
  }};
  const lists = [
    document.getElementById("rankable-list"),
    document.getElementById("unrankable-list"),
  ];

  function medalize(idx, isRankable) {{
    if (!isRankable) return null;
    return idx === 1 ? "🥇" : idx === 2 ? "🥈" : idx === 3 ? "🥉" : `#${{idx}}`;
  }}

  function reorder(key) {{
    lists.forEach(list => {{
      if (!list) return;
      const cards = Array.from(list.querySelectorAll("details.theme-card"));
      cards.sort((a, b) => {{
        const va = parseFloat(a.dataset[key]);
        const vb = parseFloat(b.dataset[key]);
        return vb - va; // desc
      }});
      const isRankable = list.id === "rankable-list";
      cards.forEach((card, i) => {{
        list.appendChild(card);
        const rankEl = card.querySelector(".theme-rank");
        if (rankEl) {{
          if (isRankable) {{
            rankEl.textContent = medalize(i + 1, true);
          }}
          // unrankable 維持 n=X 不變
        }}
      }});
    }});
  }}

  Object.entries(buttons).forEach(([id, btn]) => {{
    btn.addEventListener("click", () => {{
      Object.values(buttons).forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      reorder(id === "sort-return" ? "return" : "n");
    }});
  }});

  document.getElementById("expand-all").addEventListener("click", () => {{
    document.querySelectorAll("details.theme-card").forEach(d => d.open = true);
  }});
  document.getElementById("collapse-all").addEventListener("click", () => {{
    document.querySelectorAll("details.theme-card").forEach(d => d.open = false);
  }});
}})();
</script>
</body>
</html>
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--input", default=None,
                   help="theme_returns.json 路徑(預設 docs/data/v2/<date>/theme_returns.json)")
    p.add_argument("--output", default=str(PROJECT_ROOT / "docs" / "tags.html"))
    args = p.parse_args()

    input_path = Path(args.input) if args.input else (
        PROJECT_ROOT / "docs" / "data" / "v2" / args.date / "theme_returns.json"
    )

    if not input_path.exists():
        print(f"⚠ theme_returns.json 不存在:{input_path},跳過 tags.html")
        return

    data = json.loads(input_path.read_text(encoding="utf-8"))
    html = render(data)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    rankable_n = sum(1 for t in data.get("tags", []) if t.get("rankable"))
    unrankable_n = sum(1 for t in data.get("tags", []) if not t.get("rankable"))
    print(f"✅ 寫入 {out}  (上榜 {rankable_n} / 未上榜 {unrankable_n})")


if __name__ == "__main__":
    main()
