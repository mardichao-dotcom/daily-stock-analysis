"""
verify_kline.py — 每日抽 3~5 檔對 TWSE 官方收盤價比對(W1-2,審計 2026-07-07)

tv_collect 取自 TV 私有 API,數值無合約保證(免費帳號降級、還原權息漂移等
「數值錯但格式對」的髒資料,液性護欄全放行)。本檢查以第二來源交叉驗證:
  - 抽 N 檔上市 watchlist(以 data_date 為種子,當日重跑抽同一批)
  - 對 TWSE 官方 rwd STOCK_DAY(證交所官網同源,月檔含每日收盤)
  - |kline.close − 官方 close| / 官方 > 0.5% → Discord 告警 + exit 1
  - 官方源取不到(假日/API 掛/該日未出)→ 軟放行(警告 + exit 0),不誤報

用法:python3 -m src.verify_kline --date 2026-07-06 [--n 5]
"""
from __future__ import annotations
import argparse
import json
import os
import random
import sqlite3
import sys
import time
import urllib.request

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)
from src.load_config import get_all_tw_symbols

KLINE_DB = os.path.join(PROJECT_ROOT, "kline.db")
TOLERANCE_PCT = 0.5
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
STOCK_DAY = ("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
             "?date={ym}01&stockNo={code}&response=json")


def _iso_to_roc(date_iso: str) -> str:
    """'2026-07-06' → '115/07/06'(STOCK_DAY 的日期欄格式)。"""
    y, m, d = date_iso.split("-")
    return f"{int(y) - 1911}/{m}/{d}"


def _num(s):
    t = str(s).replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def fetch_official_close(code: str, date_iso: str, *, fetch=None) -> float | None:
    """TWSE 官方該日收盤價;查無(假日/未出檔)回 None。fetch 可注入測試。"""
    if fetch is None:
        def fetch(url):
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
    j = fetch(STOCK_DAY.format(ym=date_iso[:7].replace("-", ""), code=code))
    if j.get("stat") != "OK":
        return None
    fields = j.get("fields", [])
    try:
        di = fields.index("日期")
        ci = fields.index("收盤價")
    except ValueError:
        return None
    roc = _iso_to_roc(date_iso)
    for row in j.get("data", []):
        if str(row[di]).strip() == roc:
            return _num(row[ci])
    return None


def compare(db_close: float, official: float) -> float:
    """回誤差 %(相對官方值)。"""
    return abs(db_close - official) / official * 100


def sample_symbols(date_iso: str, db_path: str, n: int) -> list[tuple[str, float]]:
    """抽 n 檔『當日有 kline row 的上市』watchlist → [(symbol, db_close)]。以日期為種子,同日重抽一致。"""
    tw = [s for s in get_all_tw_symbols() if s.startswith("TWSE:")]
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    have = []
    for s in tw:
        row = conn.execute("SELECT close FROM kline WHERE symbol=? AND date=?",
                           (s, date_iso)).fetchone()
        if row and row[0]:
            have.append((s, row[0]))
    conn.close()
    random.Random(date_iso).shuffle(have)
    return have[:n]


def run(date_iso: str, db_path: str = KLINE_DB, n: int = 5, *, fetch=None) -> dict:
    picks = sample_symbols(date_iso, db_path, n)
    if not picks:
        return {"status": "skip", "reason": f"{date_iso} 無上市 kline 資料可抽"}
    mismatches, unavailable, ok = [], [], []
    for symbol, db_close in picks:
        code = symbol.split(":")[-1]
        try:
            official = fetch_official_close(code, date_iso, fetch=fetch)
        except Exception as e:                   # noqa: BLE001 — 單檔源失敗 → 視為取不到
            official = None
            print(f"[verify_kline] ⚠️ {symbol} 官方源例外:{str(e)[:60]}", file=sys.stderr)
        if official is None:
            unavailable.append(symbol)
        else:
            err = compare(db_close, official)
            (mismatches if err > TOLERANCE_PCT else ok).append(
                (symbol, db_close, official, round(err, 3)))
        if fetch is None:
            time.sleep(0.6)                      # 對 twse rwd 客氣
    return {"status": "mismatch" if mismatches else "ok",
            "date": date_iso, "checked": len(ok) + len(mismatches),
            "ok": ok, "mismatches": mismatches, "unavailable": unavailable}


def _discord(msg: str):
    try:
        from src.daily_supervisor import _load_webhook, _send
        wh = _load_webhook()
        if wh:
            _send(wh, msg)
    except Exception as e:                       # noqa: BLE001
        print(f"[verify_kline] Discord 告警失敗: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--db", default=KLINE_DB)
    ap.add_argument("--n", type=int, default=5)
    args = ap.parse_args()
    r = run(args.date, args.db, args.n)

    if r["status"] == "skip":
        print(f"[verify_kline] ⏭️ {r['reason']}")
        return 0
    for s, dbv, off, err in r["ok"]:
        print(f"[verify_kline] ✅ {s}:kline {dbv} vs 官方 {off}(誤差 {err}%)")
    for s in r["unavailable"]:
        print(f"[verify_kline] ⚠️ {s}:官方源取不到該日收盤(軟放行)")
    if r["mismatches"]:
        lines = [f"• {s}:kline {dbv} ≠ 官方 {off}(誤差 {err}%)"
                 for s, dbv, off, err in r["mismatches"]]
        print(f"[verify_kline] ❌ {len(r['mismatches'])} 檔與官方收盤價誤差 >{TOLERANCE_PCT}%:")
        print("\n".join(lines))
        _discord(f"❌ [K 線比對] {r['date']} 抽查 {r['checked']} 檔,"
                 f"{len(r['mismatches'])} 檔與 TWSE 官方收盤價誤差 >{TOLERANCE_PCT}%:\n"
                 + "\n".join(lines)
                 + "\nTV 資料源可能漂移(還原權息/降級),請查 tv_collect 與 kline.db。")
        return 1
    print(f"[verify_kline] ✅ 全數通過({r['checked']} 檔比對,{len(r['unavailable'])} 檔源缺)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
