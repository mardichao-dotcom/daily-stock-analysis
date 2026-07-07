"""
render_weekly.py — 週報頁(stage9 Day3 §3.3)

讀 weekly.json → 產 matplotlib PNG(NAAIM 曝險曲線、XLY/XLP 比值)→ render weekly.html
+ 日期化 snapshot(weekly_{date}.html,同 history 模式)。第一版圖用 PNG。
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")                                           # headless
import matplotlib.pyplot as plt


def _set_cjk_font():
    """圖表標題含中文 → 挑一個系統可用的 CJK 字型,避免缺字方框。"""
    import matplotlib.font_manager as fm
    avail = {f.name for f in fm.fontManager.ttflist}
    for name in ("Arial Unicode MS", "Heiti TC", "Hiragino Sans GB", "PingFang TC"):
        if name in avail:
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False
            return name
    return None


_set_cjk_font()

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(PROJECT_ROOT))
from src.render_v2 import _h
from src import asset_version

DOCS = os.path.join(PROJECT_ROOT, "docs")
WEEKLY_JSON = os.path.join(DOCS, "data", "v2", "weekly.json")
IMG_DIR = os.path.join(DOCS, "assets", "weekly")
ALERTS_CFG = os.path.join(PROJECT_ROOT, "config", "weekly_alerts.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── PNG 圖 ────────────────────────────────────────────────────────────────────
def _plot_naaim(naaim: dict, cfg: dict, path: str):
    s = naaim.get("series", {})
    dates, vals = s.get("dates", []), s.get("exposure", [])
    if len(vals) < 2:
        return False
    fig, ax = plt.subplots(figsize=(7, 2.6), dpi=110)
    ax.plot(range(len(vals)), vals, color="#2563eb", lw=1.3)
    hi = cfg.get("naaim", {}).get("extreme_high", 90)
    lo = cfg.get("naaim", {}).get("extreme_low", 20)
    ax.axhline(hi, color="#ef4444", ls="--", lw=0.8, alpha=0.7)
    ax.axhline(lo, color="#10b981", ls="--", lw=0.8, alpha=0.7)
    ax.fill_between(range(len(vals)), hi, 100, color="#ef4444", alpha=0.05)
    ax.fill_between(range(len(vals)), 0, lo, color="#10b981", alpha=0.05)
    step = max(1, len(dates) // 6)
    ax.set_xticks(range(0, len(dates), step))
    ax.set_xticklabels([dates[i][2:] for i in range(0, len(dates), step)],
                       fontsize=7, rotation=0)
    ax.set_title(f"NAAIM 曝險指數(近 {len(vals)} 週,最新 {vals[-1]})", fontsize=9)
    ax.tick_params(labelsize=7); ax.grid(alpha=0.15)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return True


def _plot_margin(mg: dict, path: str):
    """§18 + 融資改版:市場融資 30 日趨勢(億元)。"""
    s = mg.get("series", {})
    dates, vals = s.get("dates", []), s.get("total", [])
    if len(vals) < 5:
        return False
    fig, ax = plt.subplots(figsize=(7, 2.2), dpi=110)
    ax.plot(range(len(vals)), vals, color="#2962ff", lw=1.4)
    ax.fill_between(range(len(vals)), min(vals), vals, color="#2962ff", alpha=0.08)
    step = max(1, len(dates) // 5)
    ax.set_xticks(range(0, len(dates), step))
    ax.set_xticklabels([dates[i][5:] for i in range(0, len(dates), step)], fontsize=7)
    ax.set_title(f"市場融資餘額(億元,近 {len(vals)} 交易日,最新 {vals[-1]:,.1f})", fontsize=9)
    ax.tick_params(labelsize=7); ax.grid(alpha=0.15)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return True


def _plot_xly_xlp(xx: dict, path: str):
    s = xx.get("series", {})
    dates, ratio = s.get("dates", []), s.get("ratio", [])
    if len(ratio) < 2:
        return False
    fig, ax = plt.subplots(figsize=(7, 2.4), dpi=110)
    ax.plot(range(len(ratio)), ratio, color="#7c3aed", lw=1.3, label="XLY/XLP")
    step = max(1, len(dates) // 6)
    ax.set_xticks(range(0, len(dates), step))
    ax.set_xticklabels([dates[i][5:] for i in range(0, len(dates), step)], fontsize=7)
    tr = "risk_on 偏多" if xx.get("trend") == "risk_on" else "risk_off 偏空"
    ax.set_title(f"XLY/XLP 消費信心比值(近 52 週,{tr})", fontsize=9)
    ax.tick_params(labelsize=7); ax.grid(alpha=0.15)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return True


# ── HTML ──────────────────────────────────────────────────────────────────────
def _sentiment_card(label, value, sub="", color=""):
    style = f"color:{color}" if color else ""
    return (f'<div class="wk-card"><div class="wk-card-label">{_h(label)}</div>'
            f'<div class="wk-card-val" style="{style}">{_h(str(value))}</div>'
            f'<div class="wk-card-sub">{_h(sub)}</div></div>')


def render(data: dict, cfg: dict, has_naaim_png: bool, has_xx_png: bool,
           has_mg_png: bool = False) -> str:
    gen = data.get("generated_at", "")[:16].replace("T", " ")
    date = data.get("data_through", "")
    naaim = data.get("naaim", {})
    vix = data.get("vix", {})
    xx = data.get("xly_xlp", {})
    mg = data.get("margin", {})
    tw = data.get("taiex", {})

    # 警報
    alerts = data.get("alerts", [])
    alert_html = ("".join(f'<li>{_h(a)}</li>' for a in alerts)
                  if alerts else '<li class="wk-noalert">本週無極端訊號</li>')

    def _pct_color(v):
        return "var(--color-up)" if (isinstance(v, (int, float)) and v > 0) else \
               ("var(--color-down)" if (isinstance(v, (int, float)) and v < 0) else "var(--text-mute)")

    # 情緒卡
    naaim_card = _sentiment_card(
        "NAAIM 機構曝險", naaim.get("latest_value", "N/A"),
        f"最新 {naaim.get('latest_date','')}",
        "var(--color-up)" if isinstance(naaim.get("latest_value"), (int, float)) and
        naaim["latest_value"] > cfg.get("naaim", {}).get("extreme_high", 90) else "")
    vix_card = _sentiment_card("VIX 波動率", vix.get("value", "N/A"),
                               "恐慌指標(替代恐慌貪婪)")
    xx_card = _sentiment_card(
        "XLY/XLP 比值", xx.get("ratio", "N/A"),
        {"death": "死亡交叉 ⚠️", "golden": "黃金交叉", "none":
         ("偏多" if xx.get("trend") == "risk_on" else "偏空")}.get(xx.get("cross"), ""))
    mg_wow = mg.get("wow_pct")
    mg_card = _sentiment_card(
        "市場融資餘額(億元)",                                     # 2026-07-07 融資改版:市場=億元
        f"{mg.get('total','N/A'):,.1f}" if isinstance(mg.get("total"), (int, float)) else "N/A",
        (f"週增減 {mg_wow:+.2f}%" if isinstance(mg_wow, (int, float)) else "序列累積中"),
        _pct_color(mg_wow))
    tw_wk = tw.get("week_change_pct")
    tw_close = f"{tw['close']:,.2f}" if isinstance(tw.get("close"), (int, float)) else "N/A"
    tw_card = _sentiment_card("加權指數(週)", tw_close,
                              (f"本週 {tw_wk:+.2f}%" if isinstance(tw_wk, (int, float)) else ""),
                              _pct_color(tw_wk))

    naaim_img = ('<img class="wk-chart" src="assets/weekly/naaim.png" alt="NAAIM 曝險曲線">'
                 if has_naaim_png else '')
    xx_img = ('<img class="wk-chart" src="assets/weekly/xly_xlp.png" alt="XLY/XLP 比值">'
              if has_xx_png else '')
    err_html = (f'<div class="wk-errors">⚠️ 部分數據源失敗:{_h("; ".join(data.get("errors", [])))}</div>'
                if data.get("errors") else '')

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>週報 {_h(date)} — 情緒面 + 籌碼</title>
  {asset_version.head_snippet()}
</head>
<body>
<header class="page-header">
  <div class="container">
    <nav class="page-nav">
      <a href="index.html">← 回首頁</a>
      <a href="index_v2.html">📈 儀表板</a>
      <a href="watchlist_v2.html">📋 Watchlist</a>
      <a href="tags.html">🔥 主題熱度</a>
      <span class="page-nav-current">📅 週報</span>
    </nav>
    <h1>📅 每週市場情緒週報</h1>
    <div class="meta">資料日期 <strong>{_h(date)}</strong> ｜ 情緒面 NAAIM + VIX（AAII 已停用）｜ 產出時間 {_h(gen)}</div>
  </div>
</header>
<main class="container wk-narrow">
{err_html}
  <section class="section">
    <h2>🚨 本週警報</h2>
    <ul class="wk-alerts">{alert_html}</ul>
  </section>
  <section class="section">
    <h2>😱 情緒面</h2>
    <div class="wk-cards">{naaim_card}{vix_card}</div>
    {naaim_img}
  </section>
  <section class="section">
    <h2>🛒 消費信心(XLY/XLP)</h2>
    <div class="wk-cards">{xx_card}</div>
    {xx_img}
  </section>
  <section class="section">
    <h2>💰 週融資 + 大盤回顧</h2>
    <div class="wk-cards">{mg_card}{tw_card}</div>
    {'<img class="wk-chart" src="assets/weekly/margin.png" alt="市場融資餘額趨勢(億元)">' if has_mg_png else ''}
  </section>
  <p class="wk-src">NAAIM 來源:官方 USE_Data since Inception({naaim.get('count','?')} 週全量);US 指數/VIX/ETF:yfinance;融資:證交所 OpenAPI。</p>
</main>
</body>
</html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", default=WEEKLY_JSON)
    ap.add_argument("--out", default=os.path.join(DOCS, "weekly.html"))
    args = ap.parse_args()
    data = _load(args.weekly)
    if not data:
        print("❌ 讀不到 weekly.json", file=sys.stderr); return 1
    cfg = _load(ALERTS_CFG) or {}
    os.makedirs(IMG_DIR, exist_ok=True)
    has_naaim = _plot_naaim(data.get("naaim", {}), cfg, os.path.join(IMG_DIR, "naaim.png"))
    has_xx = _plot_xly_xlp(data.get("xly_xlp", {}), os.path.join(IMG_DIR, "xly_xlp.png"))
    has_mg = _plot_margin(data.get("margin", {}), os.path.join(IMG_DIR, "margin.png"))
    html = render(data, cfg, has_naaim, has_xx, has_mg)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    # 日期化 snapshot
    date = data.get("data_through", "")
    snap = os.path.join(DOCS, f"weekly_{date}.html")
    with open(snap, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 寫入 {args.out} + snapshot weekly_{date}.html(PNG naaim={has_naaim} xly/xlp={has_xx})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
