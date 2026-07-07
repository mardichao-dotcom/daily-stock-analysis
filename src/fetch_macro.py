"""
fetch_macro.py — 每日總經快覽(stage9 Day2,§3.2)

產 docs/data/v2/macro.json:台股加權、美股指數/VIX/日經/美元、融資餘額。
JS fetch 渲染儀表板頂部「總經快覽」橫條,獨立於 data_date。

資料源(§2):
  - 台股加權指數:TWSE OpenAPI MI_INDEX(發行量加權股價指數)
  - 美股 S&P/Nasdaq、VIX、日經、美元指數:yfinance 主源、stooq CSV 備援
  - 融資餘額:TWSE MI_MARGN + 櫃買 tpex_mainboard_margin_balance(市場合計 = 逐檔加總)
  - 恐慌貪婪:已決砍 CNN F&G,以 VIX 替代(§7)

護欄(§3.2):任一數據源失敗 → 該項標 value="N/A" + error,**不用舊值冒充新值**;
errors 收集哪項失敗,供 Discord 早報回報。
"""
from __future__ import annotations
import argparse
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(PROJECT_ROOT, "docs", "data", "v2", "macro.json")
TZ_TAIPEI = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/124.0 Safari/537.36"

# yfinance symbol → (label, stooq_symbol 備援)
YF_ITEMS = {
    "sp500":  ("S&P 500",  "^GSPC",     "^spx"),
    "nasdaq": ("Nasdaq",   "^IXIC",     "^ndq"),
    "vix":    ("VIX 恐慌",  "^VIX",      "^vix"),
    "nikkei": ("日經 225",  "^N225",     "^nkx"),
    "dxy":    ("美元指數",  "DX-Y.NYB",  None),
}


def _http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _item(label, value=None, change=None, change_pct=None, date=None,
          source="", unit="", error=""):
    if error or value is None:
        return {"label": label, "value": "N/A", "error": error or "無資料",
                "source": source}
    d = {"label": label, "value": value, "source": source}
    if change is not None:      d["change"] = change
    if change_pct is not None:  d["change_pct"] = change_pct
    if date:                    d["date"] = date
    if unit:                    d["unit"] = unit
    return d


# ── 台股加權指數(TWSE OpenAPI)────────────────────────────────────────────────
def fetch_taiex() -> dict:
    label = "加權指數"
    src = "TWSE OpenAPI MI_INDEX"
    try:
        rows = json.loads(_http_get("https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"))
        for r in rows:
            if r.get("指數") == "發行量加權股價指數":
                close = float(r["收盤指數"].replace(",", ""))
                sign = -1 if r.get("漲跌", "").strip() in ("-", "–") else 1
                pts = sign * float(r["漲跌點數"].replace(",", ""))
                pct = sign * float(r["漲跌百分比"].replace(",", ""))
                roc = r.get("日期", "")
                iso = (f"{int(roc[:3])+1911}-{roc[3:5]}-{roc[5:7]}"
                       if len(roc) == 7 else "")
                return _item(label, close, pts, pct, iso, src)
        return _item(label, error="MI_INDEX 無發行量加權股價指數列", source=src)
    except Exception as e:                       # noqa: BLE001
        return _item(label, error=str(e)[:80], source=src)


# ── 美股/VIX/日經/美元(yfinance 主、stooq 備)──────────────────────────────
def _fetch_yf(yf_sym: str):
    import yfinance as yf
    h = yf.Ticker(yf_sym).history(period="7d")
    closes = [c for c in h["Close"].tolist() if c == c]   # drop NaN
    if len(closes) >= 2:
        return closes[-1], closes[-2]
    raise ValueError("yfinance 資料不足")


def _fetch_stooq(stooq_sym: str):
    # stooq CSV:s,d2,t2,ohlcv → 用日線 l/(last)
    txt = _http_get(f"https://stooq.com/q/l/?s={stooq_sym}&f=sd2t2ohlc&h&e=csv")
    lines = txt.strip().splitlines()
    if len(lines) < 2:
        raise ValueError("stooq 無資料")
    cols = lines[0].split(","); vals = lines[1].split(",")
    row = dict(zip(cols, vals))
    close = float(row.get("Close", "nan"))
    openp = float(row.get("Open", "nan"))
    if close != close:
        raise ValueError("stooq close 非數值")
    return close, openp   # 以 open 當前收基準(stooq 免費即時只有當日 OHLC)


def fetch_index(key: str) -> dict:
    label, yf_sym, stooq_sym = YF_ITEMS[key]
    # 1) yfinance
    try:
        last, prev = _fetch_yf(yf_sym)
        pct = (last - prev) / prev * 100 if prev else 0.0
        return _item(label, round(last, 2), round(last - prev, 2), round(pct, 2),
                     source=f"yfinance {yf_sym}")
    except Exception as e_yf:                     # noqa: BLE001
        yf_err = str(e_yf)[:50]
    # 2) stooq 備援
    if stooq_sym:
        try:
            last, prev = _fetch_stooq(stooq_sym)
            pct = (last - prev) / prev * 100 if prev else 0.0
            return _item(label, round(last, 2), round(last - prev, 2), round(pct, 2),
                         source=f"stooq {stooq_sym}(yfinance 失敗備援)")
        except Exception as e_st:                 # noqa: BLE001
            return _item(label, error=f"yfinance:{yf_err} / stooq:{str(e_st)[:40]}",
                         source="yfinance+stooq")
    return _item(label, error=f"yfinance:{yf_err}", source=f"yfinance {yf_sym}")


# ── 融資餘額(2026-07-07 融資改版:金額億元 + 脈絡字)─────────────────────────
def fetch_margin() -> dict:
    """市場融資餘額 = 兩市官方彙總「融資金額(仟元)」→ 億元。

    舊制加總兩市每股「張數」——張數跨股加總無金額意義,已廢
    (單位分工:市場=億元、個股籌碼=張)。
    每日寫 macro.db margin_daily,並算 5 日趨勢(streak)與近一年百分位
    供 §6 橫條脈絡字;schema 增欄已於 2026-07-07 拍板批准。
    今日無檔(假日/未出)自動回退最近交易日,不冒充今日值。"""
    label = "融資餘額"
    src = "TWSE MI_MARGN(MS)+ TPEx margin/balance(金額彙總)"
    from src import margin_daily as md
    try:
        today = datetime.now(TZ_TAIPEI).date()
        got_date = None
        for i in range(7):                        # 回看一週內最近交易日
            d = today - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            if md.collect_day(d.isoformat()) is not None:
                got_date = d.isoformat()
                break
        if got_date is None:
            return _item(label, error="兩市金額彙總 7 日內皆無檔", source=src)
        st = md.stats(md.MACRO_DB, got_date)
        item = _item(label, st["amount_yi"], st["change_yi"], st["change_pct"],
                     date=got_date, source=src, unit="億元")
        item["streak"] = st["streak"]             # +N 連增 / −N 連減 / 0
        item["percentile"] = st["percentile"]     # 近一年百分位(≤252 交易日)
        item["history_days"] = st["days"]
        return item
    except Exception as e:                        # noqa: BLE001
        return _item(label, error=str(e)[:80], source=src)


# ── 主流程 ────────────────────────────────────────────────────────────────────
def run() -> dict:
    now_iso = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    # 加權指數日收盤序列(週報雙軸 + §17 大盤欄):當月月檔冪等 upsert,失敗不擋
    try:
        from src import taiex_daily
        taiex_daily.collect_current_month()
    except Exception as e:                        # noqa: BLE001
        print(f"[fetch_macro] taiex_daily upsert 失敗(不擋):{str(e)[:60]}", file=sys.stderr)
    data = {
        "taiex":  fetch_taiex(),
        "sp500":  fetch_index("sp500"),
        "nasdaq": fetch_index("nasdaq"),
        "vix":    fetch_index("vix"),
        "nikkei": fetch_index("nikkei"),
        "dxy":    fetch_index("dxy"),
        "margin": fetch_margin(),
    }
    errors = [f"{k}: {v['error']}" for k, v in data.items() if v.get("value") == "N/A"]
    return {
        "generated_at": now_iso,
        "sources_ok": sum(1 for v in data.values() if v.get("value") != "N/A"),
        "sources_failed": len(errors),
        "errors": errors,
        "data": data,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    out = run()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"✅ macro.json → {args.out}  成功 {out['sources_ok']}/7"
          + (f",失敗:{out['errors']}" if out["errors"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
