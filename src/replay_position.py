"""
replay_position.py — 持股水位模型回放:三圖一表(stage12 spec §4,2026-07-08)

輸出:專案根目錄 position_qa/(§4 拍板修正:不在 docs 內、不隨 publish 上傳、
     已入 .gitignore——回放產物僅存本機,網站零痕跡)
  1. ladder_main.png    水位階梯 + 大盤 + 60日線(近10年主視圖 / 12.9年全景副圖)
  2. ladder_2020/2022/2024.png  重點年放大
  3. switches.png + switches.csv  逐年切檔統計(模型 vs 60日線基準,遲滯有效性)
  4. contributions.png  四類加權貢獻分解(逐年平均,朋友修參數主工具)
  5. compare.md         比較表:回撤期間平均水位/降檔提前量/切換次數
                        **不輸出報酬率**(spec 紅線:回放目的=校準盤感,防過擬合)

對照基準:60 日季線策略(收盤上季線=100%、下=0%),同區間**同遲滯規則**(3 交易日)。
標注義務(2026-07-08 拍板):FedWatch 平日層在回放中非會議月為鎖定值,
與日常端(逐日更新)行為不同——歷史段呈階梯狀、上線後連續。

改 config 秒級重放:python3 -m src.replay_position [--config 路徑](引擎 0.25s,
含出圖全程 <60s,spec §6)。
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date as dt_date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from src import position_model as pm

OUT_DIR = os.path.join(PROJECT_ROOT, "position_qa")
REPLAY_START = "2013-08-01"

# 中文字型(與 render_weekly 同法:挑系統可用 CJK,避免缺字方框)
_avail = {f.name for f in font_manager.fontManager.ttflist}
for _name in ("Arial Unicode MS", "Heiti TC", "Hiragino Sans GB", "PingFang TC"):
    if _name in _avail:
        plt.rcParams["font.sans-serif"] = [_name]
        break
plt.rcParams["axes.unicode_minus"] = False

BAND_MID = {"100~130%": 115, "70~100%": 85, "40~70%": 55, "0~40%": 20, "-30~0%": -15}
BLUE, GRAY, RED = "#2962ff", "#787b86", "#e03430"


def baseline_60ma(data: pm.SignalData, days: list[str], hysteresis: int) -> dict:
    """60 日線基準:收盤上季線=100%、下=0%;同遲滯(連續 N 日才切)。"""
    out, cur, pending, pend_n = {}, None, None, 0
    dates, rows = data.idx["TAIEX"]
    row_by_date = {r[0]: r for r in rows}
    for d in days:
        r = row_by_date.get(d)
        if r is None or r[3] is None:              # ma60
            continue
        raw = 100 if r[1] >= r[3] else 0
        if cur is None:
            cur = raw
        elif raw != cur:
            if raw == pending:
                pend_n += 1
            else:
                pending, pend_n = raw, 1
            if pend_n >= hysteresis:
                cur, pending, pend_n = raw, None, 0
        else:
            pending, pend_n = None, 0
        out[d] = cur
    return out


def _x(dates):
    return [dt_date.fromisoformat(d) for d in dates]


def _ladder_axes(ax, rows, taiex, base, title):
    dates = [r["date"] for r in rows]
    x = _x(dates)
    mids = [BAND_MID[r["band"]] for r in rows]
    ax.step(x, mids, where="post", color=BLUE, lw=1.8, label="模型水位(檔位中值)")
    bx = [d for d in dates if d in base]
    ax.step(_x(bx), [base[d] for d in bx], where="post", color=RED, lw=1.0,
            alpha=0.55, label="基準:60日線(0/100%)")
    warn = [(xi, m) for xi, m, r in zip(x, mids, rows) if r["warning_vix"]]
    if warn:
        ax.scatter([w[0] for w in warn], [w[1] for w in warn], s=8, color=RED,
                   zorder=5, label="VIX>40 警示日")
    ax.set_ylabel("水位 %", fontsize=9)
    ax.set_ylim(-35, 135)
    ax2 = ax.twinx()
    tx = [d for d in dates if d in taiex]
    ax2.plot(_x(tx), [taiex[d][0] for d in tx], color=GRAY, lw=0.9, alpha=0.8,
             label="加權指數(右軸)")
    ax2.plot(_x(tx), [taiex[d][1] for d in tx], color=GRAY, lw=0.7, ls="--",
             alpha=0.6, label="60日線(右軸)")
    ax2.set_ylabel("加權指數", fontsize=9, color=GRAY)
    ax.set_title(title, fontsize=11)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left", ncol=2)


def plot_ladders(rows, data, base, out_dir):
    taiex = {r[0]: (r[1], r[3]) for r in data.idx["TAIEX"][1]}
    ten_start = (dt_date.fromisoformat(rows[-1]["date"]).replace(day=1)
                 .replace(year=dt_date.fromisoformat(rows[-1]["date"]).year - 10)
                 .isoformat())
    fig, axes = plt.subplots(2, 1, figsize=(13, 8.5),
                             gridspec_kw={"height_ratios": [3, 2]})
    _ladder_axes(axes[0], [r for r in rows if r["date"] >= ten_start], taiex, base,
                 f"水位階梯(近 10 年主視圖,{ten_start[:7]} 起)")
    _ladder_axes(axes[1], rows, taiex, base,
                 f"12.9 年全景副圖({rows[0]['date']} 起;"
                 "注:FedWatch 平日層於回放非會議月為鎖定值,與日常端逐日更新不同)")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "ladder_main.png"), dpi=110)
    plt.close(fig)
    for y in ("2020", "2022", "2024"):
        seg = [r for r in rows if r["date"][:4] == y]
        if not seg:
            continue
        fig, ax = plt.subplots(figsize=(12, 5))
        _ladder_axes(ax, seg, taiex, base, f"{y} 年放大")
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, f"ladder_{y}.png"), dpi=110)
        plt.close(fig)


def switch_stats(rows, base) -> tuple[dict, dict]:
    m_sw, b_sw = Counter(), Counter()
    for i in range(1, len(rows)):
        if rows[i]["band"] != rows[i - 1]["band"]:
            m_sw[rows[i]["date"][:4]] += 1
    bd = sorted(base)
    for i in range(1, len(bd)):
        if base[bd[i]] != base[bd[i - 1]]:
            b_sw[bd[i][:4]] += 1
    return dict(m_sw), dict(b_sw)


def plot_switches(m_sw, b_sw, out_dir):
    years = sorted(set(m_sw) | set(b_sw))
    fig, ax = plt.subplots(figsize=(11, 4))
    xs = range(len(years))
    ax.bar([i - 0.2 for i in xs], [m_sw.get(y, 0) for y in years], 0.4,
           color=BLUE, label="模型")
    ax.bar([i + 0.2 for i in xs], [b_sw.get(y, 0) for y in years], 0.4,
           color=RED, alpha=0.6, label="60日線基準")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(years, fontsize=8)
    ax.set_title("逐年切檔次數(遲滯有效性檢驗;兩者同用 3 交易日遲滯)", fontsize=11)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "switches.png"), dpi=110)
    plt.close(fig)
    with open(os.path.join(out_dir, "switches.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["year", "model_switches", "baseline_switches"])
        for y in years:
            w.writerow([y, m_sw.get(y, 0), b_sw.get(y, 0)])


def plot_contributions(rows, cfg, out_dir):
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["date"][:4]].append(r)
    years = sorted(by_year)
    cats = ["technical", "sentiment", "cycle", "macro"]
    labels = {"technical": "技術 50%", "sentiment": "情緒 15%",
              "cycle": "週期 10%", "macro": "總經 25%"}
    colors = {"technical": BLUE, "sentiment": "#8fb3ff", "cycle": "#c9d6f2",
              "macro": GRAY}
    w = cfg["categories"]
    fig, ax = plt.subplots(figsize=(12, 4.6))
    pos_bottom = [0.0] * len(years)
    neg_bottom = [0.0] * len(years)
    for c in cats:
        vals = [sum(w[c] * r["cat"][c] for r in by_year[y]) / len(by_year[y])
                for y in years]
        bottoms = [pos_bottom[i] if v >= 0 else neg_bottom[i]
                   for i, v in enumerate(vals)]
        ax.bar(years, vals, 0.62, bottom=bottoms, color=colors[c], label=labels[c])
        for i, v in enumerate(vals):
            if v >= 0:
                pos_bottom[i] += v
            else:
                neg_bottom[i] += v
    tot = [sum(w[c] * r["cat"][c] for c in cats for r in by_year[y]) / len(by_year[y])
           for y in years]
    ax.plot(years, tot, color="#111", lw=1.2, marker="o", ms=3, label="加權總分(年均)")
    ax.axhline(0, color="#999", lw=0.6)
    ax.set_title("四類加權貢獻分解(逐年平均;朋友修參數主工具)", fontsize=11)
    ax.legend(fontsize=8, ncol=5)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "contributions.png"), dpi=110)
    plt.close(fig)


def drawdown_episodes(data: pm.SignalData, days: list[str],
                      threshold: float = 0.15) -> list[dict]:
    """大盤峰谷回撤 >threshold 的期間(峰日→谷日)。"""
    closes = {r[0]: r[1] for r in data.idx["TAIEX"][1]}
    eps, peak_d, peak_v, trough_d, trough_v = [], None, -1, None, None
    for d in days:
        c = closes.get(d)
        if c is None:
            continue
        if c > peak_v:
            if (peak_d and trough_d and trough_v is not None
                    and (peak_v - trough_v) / peak_v >= threshold):
                eps.append({"peak": peak_d, "trough": trough_d,
                            "dd_pct": round((peak_v - trough_v) / peak_v * 100, 1)})
            peak_d, peak_v, trough_d, trough_v = d, c, None, None
        elif trough_v is None or c < trough_v:
            trough_d, trough_v = d, c
    if (peak_d and trough_d and trough_v is not None
            and (peak_v - trough_v) / peak_v >= threshold):
        eps.append({"peak": peak_d, "trough": trough_d,
                    "dd_pct": round((peak_v - trough_v) / peak_v * 100, 1)})
    return eps


def compare_table(rows, base, data, out_dir) -> str:
    days = [r["date"] for r in rows]
    by = {r["date"]: r for r in rows}
    eps = drawdown_episodes(data, days)
    lines = ["# 模型 vs 60日線基準 比較表",
             "",
             f"> 區間 {days[0]} → {days[-1]}|遲滯同為 3 交易日|"
             "**不含報酬率**(spec 紅線:回放目的=校準盤感,防過擬合)",
             "> 標注:FedWatch 平日層於回放非會議月為鎖定值(方案 a),"
             "與日常端逐日更新行為不同。",
             "",
             "## 重挫期間(大盤峰谷回撤 ≥15%)平均水位與降檔提前量",
             "",
             "| 期間(峰→谷) | 回撤 | 模型平均水位 | 基準平均水位 |"
             " 模型降檔提前(谷前交易日) | 基準歸零提前 |",
             "|---|---|---|---|---|---|"]
    for e in eps:
        span = [d for d in days if e["peak"] <= d <= e["trough"]]
        m_avg = sum(BAND_MID[by[d]["band"]] for d in span) / len(span)
        b_span = [d for d in span if d in base]
        b_avg = (sum(base[d] for d in b_span) / len(b_span)) if b_span else float("nan")
        def lead(cond):
            hits = [d for d in span if cond(d)]
            if not hits:
                return "未降"
            return str(len([d for d in span if d >= hits[0]]) - 1)
        m_lead = lead(lambda d: BAND_MID[by[d]["band"]] <= 55)
        b_lead = lead(lambda d: base.get(d, 100) == 0)
        lines.append(f"| {e['peak']} → {e['trough']} | −{e['dd_pct']}% "
                     f"| {m_avg:.0f}% | {b_avg:.0f}% | {m_lead} | {b_lead} |")
    m_sw, b_sw = switch_stats(rows, base)
    lines += ["",
              "## 切換次數",
              "",
              f"- 模型:{sum(m_sw.values())} 次(12.9 年,{sum(m_sw.values())/12.9:.1f} 次/年)",
              f"- 基準:{sum(b_sw.values())} 次({sum(b_sw.values())/12.9:.1f} 次/年)",
              "",
              "## FedWatch 回放已知限制(誠實 N/A)",
              "",
              "- 月底/月初跨月會議 37 次無會前期望基準(前月近月合約結構性不可得),"
              "其中利率變動會議 6 次:2019-07-31/2019-10-30/2023-02-01/2025-10-29"
              "(皆為市場高度定價之 25bp)與 2020-03-02/03-15(緊急會議,定義上無會前定價)"
              "→ 該輪 surprise 層不判定。",
              "- 同月兩會議(2020-03)期望層退場,由 VIX>40 警示與技術面承接。"]
    md = "\n".join(lines) + "\n"
    with open(os.path.join(out_dir, "compare.md"), "w", encoding="utf-8") as f:
        f.write(md)
    return md


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=pm.MACRO_DB)
    ap.add_argument("--config", default=pm.CFG_PATH)
    ap.add_argument("--start", default=REPLAY_START)
    ap.add_argument("--end")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    cfg = pm.load_cfg(args.config)
    data = pm.SignalData(args.db)
    end = args.end or dt_date.today().isoformat()
    import time
    t0 = time.time()
    rows = pm.run(args.start, end, cfg, args.db, data)
    if not rows:
        print("無資料")
        return 1
    days = [r["date"] for r in rows]
    base = baseline_60ma(data, days, cfg["hysteresis_trading_days"])
    plot_ladders(rows, data, base, OUT_DIR)
    m_sw, b_sw = switch_stats(rows, base)
    plot_switches(m_sw, b_sw, OUT_DIR)
    plot_contributions(rows, cfg, OUT_DIR)
    compare_table(rows, base, data, OUT_DIR)
    with open(os.path.join(OUT_DIR, "replay_series.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    print(f"✅ 回放完成:{len(rows)} 交易日 → {OUT_DIR}/(耗時 {time.time()-t0:.1f}s)")
    print(f"   模型切檔 {sum(m_sw.values())} 次 vs 基準 {sum(b_sw.values())} 次;"
          f"現檔 {rows[-1]['band']}(自 {rows[-1]['entered']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
