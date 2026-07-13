"""
fetch_weekly.py — 週報資料(stage9 Day3 §3.3)

產 docs/data/v2/weekly.json + 寫 macro.db。涵蓋:
  - 情緒面:NAAIM 曝險指數(官方全量重建)+ VIX(AAII 已砍,§7)
  - XLY/XLP 消費信心比值 + 均線交叉(死亡交叉警報)
  - 週融資趨勢(市場融資餘額,逐週累積入 macro.db)
  - 週大盤回顧(加權指數週漲跌)
警報閾值一律讀 config/weekly_alerts.json,不寫死。
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta, date as dt_date

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(PROJECT_ROOT))

from src import naaim as naaim_mod
from src.fetch_macro import fetch_index, fetch_margin, _http_get, _item  # reuse

TZ_TAIPEI = timezone(timedelta(hours=8))
MACRO_DB  = os.path.join(PROJECT_ROOT, "macro.db")
OUT       = os.path.join(PROJECT_ROOT, "docs", "data", "v2", "weekly.json")
ALERTS    = os.path.join(PROJECT_ROOT, "config", "weekly_alerts.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── yfinance 收盤序列(含 stooq 概念的容錯:失敗回空)────────────────────────
def _yf_history(symbol: str, period: str = "1y") -> list[tuple[str, float]]:
    import yfinance as yf
    h = yf.Ticker(symbol).history(period=period)
    out = []
    for idx, close in zip(h.index, h["Close"].tolist()):
        if close == close:                                     # drop NaN
            out.append((idx.strftime("%Y-%m-%d"), round(float(close), 2)))
    return out


def _ma(series: list[float], w: int) -> list[float | None]:
    out: list[float | None] = [None] * len(series)
    for i in range(len(series)):
        if i + 1 >= w:
            out[i] = sum(series[i + 1 - w:i + 1]) / w
    return out


# ── XLY/XLP 消費信心比值 + 均線交叉 ──────────────────────────────────────────
def xly_xlp_signal(cfg: dict) -> dict:
    try:
        xly = dict(_yf_history("XLY", "1y"))
        xlp = dict(_yf_history("XLP", "1y"))
        dates = sorted(set(xly) & set(xlp))
        if len(dates) < cfg["ma_long"] + 2:
            return {"status": "N/A", "error": "XLY/XLP 資料不足"}
        ratio = [xly[d] / xlp[d] for d in dates]
        ma_s = _ma(ratio, cfg["ma_short"])
        ma_l = _ma(ratio, cfg["ma_long"])
        # 死叉:短均今日 < 長均,昨日 >= 長均
        cross_down = (ma_s[-1] is not None and ma_l[-1] is not None
                      and ma_s[-2] is not None and ma_l[-2] is not None
                      and ma_s[-1] < ma_l[-1] and ma_s[-2] >= ma_l[-2])
        cross_up = (ma_s[-1] is not None and ma_l[-1] is not None
                    and ma_s[-2] is not None and ma_l[-2] is not None
                    and ma_s[-1] > ma_l[-1] and ma_s[-2] <= ma_l[-2])
        return {
            "status": "ok", "date": dates[-1],
            "ratio": round(ratio[-1], 4),
            "ma_short": round(ma_s[-1], 4), "ma_long": round(ma_l[-1], 4),
            "cross": "death" if cross_down else ("golden" if cross_up else "none"),
            "trend": "risk_on" if ma_s[-1] > ma_l[-1] else "risk_off",
            "series": {"dates": dates[-52:], "ratio": [round(r, 4) for r in ratio[-52:]]},
        }
    except Exception as e:                       # noqa: BLE001
        return {"status": "N/A", "error": str(e)[:80]}


# ── 週融資餘額(2026-07-07 融資改版:金額億元,讀 margin_daily 日頻序列)────────
# 舊制 margin_weekly(張數週頻)已廢——張數跨股加總無金額意義;
# 週報一律億元(單位分工:市場=億元、個股籌碼=張)。
def weekly_margin(db_path: str = MACRO_DB) -> dict:
    from src import margin_daily as md
    today = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")
    try:
        md.collect_day(today, db_path)                          # 今日有檔就順手入庫
    except Exception:                             # noqa: BLE001 — 週六跑,今日多半非交易日
        pass
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT date, total_yi FROM margin_daily WHERE date <= ? "
            "ORDER BY date DESC LIMIT 30", (today,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    if not rows:
        return {"status": "N/A", "error": "margin_daily 無資料(需先回補)"}
    rows = list(reversed(rows))                                 # 舊→新
    latest_d, latest = rows[-1]
    # 週對週:取 5 個交易日前
    wow = None
    if len(rows) >= 6 and rows[-6][1]:
        wow = round((latest - rows[-6][1]) / rows[-6][1] * 100, 2)
    return {"status": "ok", "date": latest_d, "total": latest, "unit": "億元",
            "wow_pct": wow, "series": {"dates": [r[0] for r in rows],
                                        "total": [r[1] for r in rows]}}


# ── 週大盤回顧(加權指數週漲跌,yfinance ^TWII)────────────────────────────────
def weekly_taiex() -> dict:
    try:
        h = _yf_history("^TWII", "1mo")
        if len(h) < 6:
            return {"status": "N/A", "error": "TWII 資料不足"}
        last = h[-1][1]
        wk_ago = h[-6][1]                                       # 約 5 交易日前
        return {"status": "ok", "date": h[-1][0], "close": last,
                "week_change_pct": round((last - wk_ago) / wk_ago * 100, 2) if wk_ago else 0,
                "series": {"dates": [d for d, _ in h], "close": [c for _, c in h]}}
    except Exception as e:                       # noqa: BLE001
        return {"status": "N/A", "error": str(e)[:80]}


# ── 警報(閾值全讀 config)──────────────────────────────────────────────────
def build_alerts(naaim_latest, vix, xly_xlp, cfg) -> list[str]:
    a = []
    n = cfg.get("naaim", {})
    if naaim_latest is not None:
        if naaim_latest > n.get("extreme_high", 90):
            a.append(f"🔴 NAAIM 曝險 {naaim_latest} > {n['extreme_high']}:機構過度樂觀,逆向警戒")
        elif naaim_latest < n.get("extreme_low", 20):
            a.append(f"🟢 NAAIM 曝險 {naaim_latest} < {n['extreme_low']}:機構過度悲觀,逆向機會")
    if vix.get("value") not in (None, "N/A") and vix["value"] > cfg.get("vix", {}).get("high", 25):
        a.append(f"🔴 VIX {vix['value']} > {cfg['vix']['high']}:市場恐慌升溫")
    if xly_xlp.get("cross") == "death":
        a.append("🔴 XLY/XLP 死亡交叉:消費信心轉弱,避險訊號")
    elif xly_xlp.get("cross") == "golden":
        a.append("🟢 XLY/XLP 黃金交叉:消費信心回升")
    return a


def run(db_path: str = MACRO_DB) -> dict:
    now_iso = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    cfg = _load(ALERTS) or {}
    errors = []

    # NAAIM 全量重建(官方檔)
    try:
        r = naaim_mod.rebuild(db_path)
        naaim_series = naaim_mod.read_series(db_path, weeks=104)
        naaim_latest = r["latest_value"]
        naaim_info = {"status": "ok", "latest_date": r["latest_date"],
                      "latest_value": r["latest_value"], "count": r["count"],
                      "source": r["source_url"],
                      "series": {"dates": [d for d, _ in naaim_series],
                                 "exposure": [v for _, v in naaim_series]}}
    except Exception as e:                       # noqa: BLE001
        naaim_latest = None
        naaim_info = {"status": "N/A", "error": str(e)[:100]}
        errors.append(f"naaim: {str(e)[:60]}")

    vix = fetch_index("vix")
    if vix.get("value") == "N/A":
        errors.append(f"vix: {vix.get('error','')}")
    xx = xly_xlp_signal(cfg.get("xly_xlp", {"ma_short": 20, "ma_long": 50}))
    if xx.get("status") == "N/A":
        errors.append(f"xly_xlp: {xx.get('error','')}")
    mg = weekly_margin(db_path)
    if mg.get("status") == "N/A":
        errors.append(f"margin: {mg.get('error','')}")
    tw = weekly_taiex()
    if tw.get("status") == "N/A":
        errors.append(f"taiex: {tw.get('error','')}")

    # 機構訊號區(stage12 §5.2,七訊號;單項失敗記名不拖累)
    from src.weekly_signals import build_signals
    signals, sig_errors = build_signals()
    errors += sig_errors

    alerts = build_alerts(naaim_latest, vix, xx, cfg)
    return {
        "generated_at": now_iso,
        "data_through": datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d"),
        "errors": errors,
        "alerts": alerts,
        "naaim": naaim_info,
        "vix": vix,
        "xly_xlp": xx,
        "margin": mg,
        "taiex": tw,
        "signals": signals,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    out = run()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    nz = out["naaim"].get("latest_value", "N/A")
    print(f"✅ weekly.json → {args.out}  NAAIM {nz} | 警報 {len(out['alerts'])} | 失敗 {len(out['errors'])}"
          + (f" {out['errors']}" if out["errors"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
