"""
compare_v1_v2.py — W2.3 並行驗證工具

用法:
    python3 tools/compare_v1_v2.py

跑 v1 / v2 各 5 個交易日(2026-05-14 ~ 2026-05-20),產生 markdown 報告。

設計準則(per W2.3 review):
  - v1 跑 production kline.db / etf_operations.db(grep 已確認 v1 只讀 kline)
  - v2 跑 /tmp/kline_v2_compare.db(copy of production)避免污染 standing_state
    跟 score_history 表
  - v1 順手會寫 state/signal_state.json + output/filtered_result.json,
    本工具 backup + restore
  - subprocess 跑 v1(不 import,維持 v1 凍結)
  - 直接 import 跑 v2(run_pipeline)
  - 聚焦 2026-05-20(最後一天,v2 已累積 5 天 state)
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import run_filters_v2

# ── 配置 ──────────────────────────────────────────────────────────────────────
KLINE_DB_PROD = PROJECT_ROOT / "kline.db"
KLINE_DB_V2   = Path("/tmp/kline_v2_compare.db")
ETF_DB        = Path(os.path.expanduser("~/ETF追蹤/etf_operations.db"))

WATCHLIST     = PROJECT_ROOT / "config" / "watchlist.json"
WEIGHTS       = PROJECT_ROOT / "config" / "weights.json"
SECTORS       = PROJECT_ROOT / "config" / "sectors.json"
KEY_PRICES    = PROJECT_ROOT / "config" / "key_prices.json"

V1_OUTPUT = PROJECT_ROOT / "output" / "filtered_result.json"
V1_STATE  = PROJECT_ROOT / "state" / "signal_state.json"

OUT_DIR = PROJECT_ROOT / "tools" / "compare_output"

DATES = ["2026-05-14", "2026-05-15", "2026-05-18", "2026-05-19", "2026-05-20"]
FOCUS_DATE = "2026-05-20"

TZ_TAIPEI = timezone(timedelta(hours=8))


# ── 備份 / 還原 ──────────────────────────────────────────────────────────────
def backup_file(src: Path) -> Path | None:
    """備份檔案到 /tmp。回傳備份路徑(或 None 若 src 不存在)。"""
    if not src.exists():
        return None
    dst = Path("/tmp") / f"_w23_backup_{src.name}"
    shutil.copy(src, dst)
    return dst


def restore_file(src: Path, backup_path: Path | None) -> None:
    if backup_path is None:
        return
    if backup_path.exists():
        shutil.copy(backup_path, src)
        backup_path.unlink()


# ── 跑 v1 / v2 ───────────────────────────────────────────────────────────────
def run_v1_for_date(date: str) -> dict:
    """subprocess 跑 v1,讀 output/filtered_result.json。"""
    print(f"  [v1 {date}] ", end="", flush=True)
    res = subprocess.run(
        ["python3", str(PROJECT_ROOT / "src" / "run_filters.py"),
         "--date", date,
         "--kline", str(KLINE_DB_PROD),
         "--etf",   str(ETF_DB)],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        print(f"❌ v1 failed for {date}:")
        print(res.stderr)
        raise RuntimeError(f"v1 failed for {date}")
    with open(V1_OUTPUT, encoding="utf-8") as f:
        out = json.load(f)
    print(f"✅ {len(out.get('個股結果', {}))} symbols")
    return out


def load_v2_configs():
    with open(WEIGHTS,    encoding="utf-8") as f: weights    = json.load(f)
    with open(SECTORS,    encoding="utf-8") as f: sectors    = json.load(f)
    with open(KEY_PRICES, encoding="utf-8") as f: key_prices = json.load(f)
    with open(WATCHLIST,  encoding="utf-8") as f: watchlist  = json.load(f)
    return weights, sectors, key_prices, watchlist


def run_v2_for_date(conn_kline, conn_etf, configs, date: str) -> dict:
    weights, sectors, key_prices, watchlist = configs
    print(f"  [v2 {date}] ", end="", flush=True)
    now_iso = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    result = run_filters_v2.run_pipeline(
        date=date, conn_kline=conn_kline, conn_etf=conn_etf,
        weights=weights, sectors=sectors,
        key_prices=key_prices, watchlist=watchlist,
        now_iso=now_iso,
    )
    conn_kline.commit()
    print(f"✅ {len(result.get('stocks', {}))} symbols")
    return result


# ── v1 item pattern 字典(規則 §6 / §7 對齊用)──────────────────────────────
V1_CHIP_ETF_POSITIVE   = ("ETF共識加碼", "ETF連續加碼", "ETF異常")   # parity check 比這些
V1_CHIP_ETF_NEGATIVE   = ("ETF共識減碼", "ETF經理人分歧")             # v2 純加分制不算
V1_KPATTERN_POSITIVE   = ("突破60日高", "漲停", "大漲", "跳空開高", "強紅K")  # §6 砍
V1_KPATTERN_NEGATIVE   = ("跌停", "實體長黑", "長上影線", "大跌", "量縮")     # §6 砍 + 純加分不扣
V1_SECTOR_LEADER       = ("自身是族群長子", "撿漏候選", "強單兵作戰")          # §7 重寫
V1_SECTOR_NEGATIVE     = ("族群長子大跌", "多長子背離")                        # §7 + 純加分不扣
V1_INTL_SYNC_POSITIVE  = ("國際同步發動",)
V1_INTL_NEGATIVE       = ("國際對應族群大跌",)


# ── 分數抽取 / 比對 helpers ───────────────────────────────────────────────────
def v1_score_for_pattern(stock: dict, patterns: tuple[str, ...]) -> float:
    """v1 score_breakdown 中項目名稱含任一 pattern 的分數總和(含 sign)"""
    return sum(item.get("分數", 0) for item in stock.get("score_breakdown", [])
               if any(p in item.get("項目", "") for p in patterns))


def v1_items_for_pattern(stock: dict, patterns: tuple[str, ...]) -> list[dict]:
    """v1 score_breakdown 中項目名稱含任一 pattern 的原始 entries"""
    return [item for item in stock.get("score_breakdown", [])
            if any(p in item.get("項目", "") for p in patterns)]


def v1_chip_etf_score(stock: dict) -> float:
    """**只看加分**:加碼 / 連續加碼 / 異常點火(parity 用,W2.3 review 確認)。
    減碼 / 經理人分歧 是 v1 結構差,另闢一區。"""
    return v1_score_for_pattern(stock, V1_CHIP_ETF_POSITIVE)


def v2_module_score(stock: dict, module: str) -> float:
    """v2 details 中某 module 的分數總和"""
    return sum(d.get("score", 0) for d in stock.get("details", [])
               if d.get("module") == module)


def categorize_diff(v1_stock, v2_stock, v1_score, v2_score) -> list[str]:
    """Heuristic 自動分類 diff 來源(規則 §6/§7 預期改動)"""
    reasons = []

    # v1 純扣分(規則 §6/§7 移除 + 純加分制)
    if v1_score < 0 and v2_score == 0:
        reasons.append("純加分制(v1 扣分→v2 不算)")

    # v1 K 棒型態加分(規則 §6 砍)
    kp_pos = v1_score_for_pattern(v1_stock, V1_KPATTERN_POSITIVE)
    if kp_pos > 0:
        reasons.append(f"v1 K 棒型態 +{kp_pos:.0f}(§6 砍)")

    # v1 族群長子加分(規則 §7 重寫成 sector_linkage)
    sl = v1_score_for_pattern(v1_stock, V1_SECTOR_LEADER)
    if sl > 0:
        reasons.append(f"v1 族群長子 +{sl:.0f}(§7 重寫)")

    # v1 國際同步發動(v2 沒這獨立項,合進 sector_linkage)
    intl = v1_score_for_pattern(v1_stock, V1_INTL_SYNC_POSITIVE)
    if intl > 0:
        reasons.append(f"v1 國際同步 +{intl:.0f}")

    # v2 新標籤
    tags = v2_stock.get("tags", [])
    if any("⭐" in t for t in tags):
        reasons.append("v2 新增 ⭐ 輪動")
    if any("⚡" in t for t in tags):
        reasons.append("v2 新增 ⚡ MACD")

    # v2 新軸計分
    if v2_module_score(v2_stock, "sector_linkage") > 0:
        reasons.append("v2 sector_linkage(只算漲)")
    v2_vol = v2_module_score(v2_stock, "volume")
    if v2_vol > 0:
        reasons.append(f"v2 volume v2.1(+{v2_vol})")
    v2_ma = v2_module_score(v2_stock, "ma")
    if v2_ma > 0:
        reasons.append(f"v2 MA 首次站上(+{v2_ma})")
    v2_gp = v2_module_score(v2_stock, "given_price")
    if v2_gp > 0:
        reasons.append(f"v2 給定價(+{v2_gp:.2f}, v3 資料源)")

    return reasons


def is_pure_technical_drop(v1_stock: dict, v1_score: float, v2_score: float) -> bool:
    """純技術突破股變低分:
    - v1 score ≥ 4 + v2 score ≤ 1
    - K 棒型態加分佔 v1 score 一半以上(主要來源)"""
    if v1_score < 4 or v2_score > 1:
        return False
    kp_pos = v1_score_for_pattern(v1_stock, V1_KPATTERN_POSITIVE)
    return kp_pos >= 0.5 * v1_score and kp_pos >= 3


def is_ma_dominated(v2_stock: dict, v2_score: float) -> bool:
    """MA 首次站上佔比過大:
    - v2 score ≥ 5(衝到 A/S 級)
    - MA 貢獻 ≥ 50% 總分"""
    if v2_score < 5:
        return False
    ma_score = v2_module_score(v2_stock, "ma")
    return ma_score >= 0.5 * v2_score


# ── 報告產生 ──────────────────────────────────────────────────────────────────
def write_daily_report(date: str, v1: dict, v2: dict, focus: bool) -> Path:
    """產 single-day markdown 報告"""
    out_path = OUT_DIR / f"{date}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    v1_stocks = v1.get("個股結果", {})
    v2_stocks = v2.get("stocks", {})

    # 統一 symbol key(v1 用 "TWSE:2330" 同 v2)
    all_symbols = set(v1_stocks) | set(v2_stocks)

    # 收集差異
    rows = []
    chip_parity = []
    only_v1 = []
    only_v2 = []
    new_tag_rows = []
    v1_negative = []
    v1_etf_neg_rows = []       # 🆕 v1 ETF 扣分(減碼/分歧)→ v2 不扣
    pure_tech_drop_rows = []   # 🆕 純技術突破股變低分(§6 後果)
    ma_dominated_rows  = []    # 🆕 MA 首次站上佔比過大(衝 S 級)
    gp_coverage_v1 = 0
    gp_coverage_v2 = 0

    for sym in sorted(all_symbols):
        v1s = v1_stocks.get(sym)
        v2s = v2_stocks.get(sym)
        if v1s is None and v2s is not None:
            only_v2.append((sym, v2s.get("score", 0)))
            continue
        if v2s is None and v1s is not None:
            only_v1.append((sym, v1s.get("score", 0)))
            continue
        # both present
        v1_score = v1s.get("score", 0)
        v2_score = v2s.get("score", 0)
        diff = v2_score - v1_score
        rows.append({
            "symbol":   sym,
            "v1_score": v1_score,
            "v2_score": v2_score,
            "diff":     diff,
            "v1_grade": v1s.get("grade", "?"),
            "v2_grade": v2s.get("grade", "?"),
            "reasons":  categorize_diff(v1s, v2s, v1_score, v2_score),
            "v2_tags":  v2s.get("tags", []),
        })

        # chip_etf parity(只比加分,W2.3 fix 後預期 100%)
        v1c = v1_chip_etf_score(v1s)
        v2c = v2_module_score(v2s, "chip_etf")
        if abs(v1c - v2c) > 0.001:
            chip_parity.append((sym, v1c, v2c))

        # 🆕 v1 ETF 扣分(減碼/經理人分歧)→ v2 純加分不扣
        v1_etf_neg = v1_items_for_pattern(v1s, V1_CHIP_ETF_NEGATIVE)
        if v1_etf_neg:
            v1_etf_neg_rows.append((sym, v1_etf_neg))

        # 🆕 純技術突破股變低分(規則 §6 後果)
        if is_pure_technical_drop(v1s, v1_score, v2_score):
            pure_tech_drop_rows.append({
                "symbol":    sym,
                "v1_score":  v1_score,
                "v2_score":  v2_score,
                "kp_items":  v1_items_for_pattern(v1s, V1_KPATTERN_POSITIVE),
            })

        # 🆕 MA 首次站上佔比過大(衝 S 級)
        if is_ma_dominated(v2s, v2_score):
            ma_score = v2_module_score(v2s, "ma")
            other = v2_score - ma_score
            ma_dominated_rows.append({
                "symbol":     sym,
                "v2_score":   v2_score,
                "ma_score":   ma_score,
                "ma_ratio":   ma_score / v2_score if v2_score else 0,
                "other":      other,
                "ma_details": [d for d in v2s.get("details", []) if d.get("module") == "ma"],
            })

        # 給定價覆蓋(只看「有計分項目」的個股數,不比分數)
        v1_has_kp = any("關鍵價" in item.get("項目", "")
                         for item in v1s.get("score_breakdown", []))
        v2_has_kp = v2_module_score(v2s, "given_price") > 0
        if v1_has_kp: gp_coverage_v1 += 1
        if v2_has_kp: gp_coverage_v2 += 1

        # 新標籤
        new_tags = [t for t in v2s.get("tags", [])
                    if any(emoji in t for emoji in ("⭐", "⚡"))]
        if new_tags:
            new_tag_rows.append((sym, new_tags))

        # v1 負分
        if v1_score < 0:
            v1_negative.append((sym, v1_score, v1s.get("score_breakdown", [])))

    rows.sort(key=lambda r: -abs(r["diff"]))

    # 寫 markdown
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# v1 vs v2 對比 — {date}\n\n")
        if focus:
            f.write(f"⭐ **聚焦日(v2 已累積 5 天 standing_state)**\n\n")

        f.write("## 📊 摘要\n\n")
        n = len(rows)
        v1_avg = sum(r["v1_score"] for r in rows) / n if n else 0
        v2_avg = sum(r["v2_score"] for r in rows) / n if n else 0
        big_diff = [r for r in rows if abs(r["diff"]) >= 2]
        f.write(f"- 對比個股數: {n}\n")
        f.write(f"- v1 平均分: {v1_avg:.2f}\n")
        f.write(f"- v2 平均分: {v2_avg:.2f}\n")
        f.write(f"- `|diff| ≥ 2` 個股: {len(big_diff)} 檔\n")
        f.write(f"- v1 負分個股: {len(v1_negative)} 檔(v2 不可能負,結構差)\n")
        f.write(f"- v2 含 ⭐/⚡ 新標籤: {len(new_tag_rows)} 檔\n")
        f.write(f"- 只在 v1: {len(only_v1)} 檔  只在 v2: {len(only_v2)} 檔\n\n")

        # 給定價覆蓋對比
        f.write("## 🎯 給定價計分覆蓋對比(不比分數,只比有幾檔有計分)\n\n")
        f.write(f"- v1 有給定價計分的個股: {gp_coverage_v1} 檔\n")
        f.write(f"- v2 有給定價計分的個股: {gp_coverage_v2} 檔\n")
        if gp_coverage_v2 < gp_coverage_v1 * 0.5:
            f.write("- ⚠️ v2 覆蓋顯著低於 v1 — 檢查 key_prices.json v3 是否完整載入\n")
        f.write("\n")

        # chip_etf parity
        f.write("## ✅ chip_etf parity check\n\n")
        f.write(f"- v1/v2 chip_etf 應該一致(規則 §7 「全部保留」)\n")
        f.write(f"- 不一致個股: {len(chip_parity)} 檔\n")
        if chip_parity:
            f.write("- ⚠️ **BUG 嫌疑:chip_etf 應 parity 卻有差**\n\n")
            f.write("| symbol | v1 chip_etf | v2 chip_etf | diff |\n")
            f.write("|---|---|---|---|\n")
            for sym, v1c, v2c in chip_parity[:20]:
                f.write(f"| {sym} | {v1c} | {v2c} | {v2c - v1c:+.1f} |\n")
        else:
            f.write("- 🎉 chip_etf 完全 parity\n")
        f.write("\n")

        # 🆕 規則 §6 後果:純技術突破股變低分(朋友 review 重點)
        f.write("## ⚠️ 規則 §6 後果:純技術突破股在 v2 變低分(朋友 review 重點)\n\n")
        f.write("**criteria**:v1 score ≥ 4 + v2 score ≤ 1 + K 棒型態 ≥ 50% v1 分數來源\n\n")
        if pure_tech_drop_rows:
            f.write("| symbol | v1 | v2 | v1 K 棒型態項目 |\n|---|---|---|---|\n")
            for r in pure_tech_drop_rows:
                items_str = ", ".join(
                    f"{it['項目']} {it['分數']:+}" for it in r["kp_items"]
                )
                f.write(f"| {r['symbol']} | {r['v1_score']:.0f} | "
                        f"{r['v2_score']:.0f} | {items_str} |\n")
            f.write("\n**朋友 review 問題**:你當初規則 §6 砍掉 K 棒型態加分,\n")
            f.write("有沒有意識到「純技術突破股(沒 ETF/族群/關鍵價配合)會變 0 分」?\n")
            f.write("要不要保留某些(例如 突破 60 日高 +2)?\n\n")
        else:
            f.write("- 無此類個股\n\n")

        # 🆕 MA 首次站上佔比過大(朋友 review 重點)
        f.write("## ⚠️ MA 首次站上佔比過大(朋友 review 重點)\n\n")
        f.write("**criteria**:v2 score ≥ 5(衝 A/S 級)+ MA 貢獻 ≥ 50% 總分\n\n")
        if ma_dominated_rows:
            f.write("| symbol | v2 總分 | MA 貢獻 | MA 佔比 | 其他維度分數 |\n")
            f.write("|---|---|---|---|---|\n")
            for r in ma_dominated_rows:
                f.write(
                    f"| {r['symbol']} | {r['v2_score']:.1f} | "
                    f"{r['ma_score']:.1f} | {r['ma_ratio']*100:.0f}% | "
                    f"{r['other']:.1f} |\n"
                )
            f.write("\n**朋友 review 問題**:同一天首次站上 20/60/90 三條均線 = +5,\n")
            f.write("讓個股直接衝 S 級。這合理嗎?要不要降權重(例如 ma_90 從 +2 改 +1)\n")
            f.write("或加 cooldown(同週站上多條只算一次)?\n\n")
            f.write("**風險**:S 級會充滿「技術面剛站上均線」的股票,稀釋「強勢共振」本意。\n\n")
        else:
            f.write("- 無此類個股\n\n")

        # 🆕 v1 ETF 扣分 → v2 純加分不扣(結構差)
        f.write("## 🔻 v1 ETF 扣分 → v2 不扣(雙向 → 純加分結構差)\n\n")
        if v1_etf_neg_rows:
            f.write("| symbol | v1 ETF 扣分項目 | v2 chip_etf | 說明 |\n")
            f.write("|---|---|---|---|\n")
            for sym, items in v1_etf_neg_rows:
                items_str = ", ".join(
                    f"{it['項目']} {it['分數']:+}" for it in items
                )
                v2c = v2_module_score(v2_stocks[sym], "chip_etf")
                f.write(f"| {sym} | {items_str} | {v2c:.0f} | 結構差 |\n")
            f.write("\n朋友要知道:**v1 會因 ETF 減碼/分歧壓低分數,v2 不會**。\n\n")
        else:
            f.write("- 無\n\n")

        # 預期內 vs 需人工確認
        f.write("## 📋 差異列表(按 |diff| 由大到小)\n\n")
        if big_diff:
            f.write("### |diff| ≥ 2 個股\n\n")
            f.write("| symbol | v1 | v2 | diff | v1 grade | v2 grade | 可能來源 |\n")
            f.write("|---|---|---|---|---|---|---|\n")
            for r in big_diff[:40]:
                reasons = ", ".join(r["reasons"]) if r["reasons"] else "**⚠️ 無法歸因**"
                f.write(f"| {r['symbol']} | {r['v1_score']:.2f} | {r['v2_score']:.2f} | "
                        f"{r['diff']:+.2f} | {r['v1_grade']} | {r['v2_grade']} | {reasons} |\n")
        else:
            f.write("- 無 |diff| ≥ 2 個股\n")
        f.write("\n")

        # 新標籤
        f.write("## ⭐ v2 新增標籤(v1 沒有)\n\n")
        if new_tag_rows:
            f.write("| symbol | v2 tags |\n|---|---|\n")
            for sym, tags in new_tag_rows[:30]:
                f.write(f"| {sym} | {', '.join(tags)} |\n")
        else:
            f.write("- 無\n")
        f.write("\n")

        # v1 負分(結構差,合理)
        f.write("## 📉 v1 負分(雙向扣分制 → 純加分制,結構差)\n\n")
        if v1_negative:
            f.write("| symbol | v1 score | v1 扣分項目 |\n|---|---|---|\n")
            for sym, score, breakdown in v1_negative[:30]:
                neg = [item["項目"] for item in breakdown if item.get("分數", 0) < 0]
                f.write(f"| {sym} | {score:.2f} | {', '.join(neg[:3])} |\n")
        else:
            f.write("- 無\n")
        f.write("\n")

        # 只在一邊出現
        if only_v1 or only_v2:
            f.write("## 🔀 單側出現\n\n")
            if only_v1:
                f.write(f"### 只在 v1 ({len(only_v1)} 檔)\n")
                f.write(", ".join(s for s, _ in only_v1) + "\n\n")
            if only_v2:
                f.write(f"### 只在 v2 ({len(only_v2)} 檔)\n")
                f.write(", ".join(s for s, _ in only_v2) + "\n\n")

    return out_path


def write_summary(reports: dict, paths: dict) -> Path:
    out_path = OUT_DIR / "summary.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# W2.3 v1 vs v2 並行驗證 — 總覽\n\n")
        f.write(f"**對比基準日**: production kline.db 最新日 = 2026-05-20(今天 = {datetime.now().date()})\n\n")
        f.write("⚠️ production DB 8 天未更新,本驗證以 5/14-5/20 為基準。 W4 上線前需先跑 daily_update。\n\n")
        f.write("## 📅 每日報告\n\n")
        for date in DATES:
            star = " ⭐" if date == FOCUS_DATE else ""
            f.write(f"- [{date}{star}](./{date}.md)\n")
        f.write("\n")

        f.write("## 📊 5 天速覽\n\n")
        f.write("| 日期 | v1 平均 | v2 平均 | |diff|≥2 | v1 負分 | v2 新標籤 | chip 不一致 |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for date in DATES:
            r = reports[date]
            mark = " ⭐" if date == FOCUS_DATE else ""
            f.write(
                f"| {date}{mark} | {r['v1_avg']:.2f} | {r['v2_avg']:.2f} | "
                f"{r['big_diff']} | {r['v1_neg']} | {r['new_tag']} | {r['chip_mis']} |\n"
            )
    return out_path


# ── 統計 helper ───────────────────────────────────────────────────────────────
def collect_stats(v1: dict, v2: dict) -> dict:
    v1_stocks = v1.get("個股結果", {})
    v2_stocks = v2.get("stocks", {})
    common = set(v1_stocks) & set(v2_stocks)
    if not common:
        return {"v1_avg": 0, "v2_avg": 0, "big_diff": 0,
                "v1_neg": 0, "new_tag": 0, "chip_mis": 0}

    v1_avg = sum(v1_stocks[s].get("score", 0) for s in common) / len(common)
    v2_avg = sum(v2_stocks[s].get("score", 0) for s in common) / len(common)
    big_diff = sum(1 for s in common
                   if abs(v2_stocks[s].get("score", 0) - v1_stocks[s].get("score", 0)) >= 2)
    v1_neg = sum(1 for s in common if v1_stocks[s].get("score", 0) < 0)
    new_tag = sum(1 for s in common
                  if any("⭐" in t or "⚡" in t for t in v2_stocks[s].get("tags", [])))
    chip_mis = sum(
        1 for s in common
        if abs(v1_chip_etf_score(v1_stocks[s])
               - v2_module_score(v2_stocks[s], "chip_etf")) > 0.001
    )
    return {"v1_avg": v1_avg, "v2_avg": v2_avg, "big_diff": big_diff,
            "v1_neg": v1_neg, "new_tag": new_tag, "chip_mis": chip_mis}


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("  W2.3 v1 vs v2 並行驗證")
    print("=" * 70)
    print(f"  dates: {DATES}")
    print(f"  focus: {FOCUS_DATE}")
    print()

    # 1. 確認 production 資料
    if not KLINE_DB_PROD.exists():
        print(f"❌ {KLINE_DB_PROD} 不存在"); sys.exit(1)
    if not ETF_DB.exists():
        print(f"❌ {ETF_DB} 不存在"); sys.exit(1)
    print(f"✅ production data:\n   {KLINE_DB_PROD}\n   {ETF_DB}\n")

    # 2. 備份 v1 會寫的 state / output
    print("📦 備份 v1 state / output...")
    backup_state  = backup_file(V1_STATE)
    backup_output = backup_file(V1_OUTPUT)
    print(f"   state:  {backup_state}\n   output: {backup_output}\n")

    # 3. 複製 kline.db 給 v2(隔離 standing_state + score_history 寫入)
    print(f"📋 複製 kline.db → {KLINE_DB_V2}...")
    shutil.copy(KLINE_DB_PROD, KLINE_DB_V2)
    print("   完成\n")

    v1_results = {}
    v2_results = {}

    try:
        # 4. 跑 v1 五天
        print("🔵 跑 v1...")
        for d in DATES:
            v1_results[d] = run_v1_for_date(d)
        print()

        # 5. 跑 v2 五天(順序累積 state)
        print("🟢 跑 v2(順序跑,累積 standing_state)...")
        configs = load_v2_configs()
        conn_kline = sqlite3.connect(str(KLINE_DB_V2))
        conn_etf   = sqlite3.connect(str(ETF_DB))
        try:
            for d in DATES:
                v2_results[d] = run_v2_for_date(conn_kline, conn_etf, configs, d)
        finally:
            conn_kline.close()
            conn_etf.close()
        print()

        # 6. 產生報告
        print("📝 產生報告...")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        report_stats = {}
        for d in DATES:
            path = write_daily_report(d, v1_results[d], v2_results[d], focus=(d == FOCUS_DATE))
            report_stats[d] = collect_stats(v1_results[d], v2_results[d])
            print(f"   {path}")
        summary_path = write_summary(report_stats, None)
        print(f"   {summary_path}\n")

    finally:
        # 7. 還原 production state
        print("♻️  還原 v1 state / output...")
        restore_file(V1_STATE,  backup_state)
        restore_file(V1_OUTPUT, backup_output)

        # 8. 清理 /tmp 複本
        if KLINE_DB_V2.exists():
            KLINE_DB_V2.unlink()
            print(f"   removed {KLINE_DB_V2}")
        print()

    print("=" * 70)
    print(f"✅ 完成。報告:{OUT_DIR}/summary.md")
    print("=" * 70)


if __name__ == "__main__":
    main()
