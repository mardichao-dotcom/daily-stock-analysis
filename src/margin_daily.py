"""
margin_daily.py — 市場融資餘額(金額,億元)日頻序列(2026-07-07 融資改版立案)

背景:舊制 fetch_margin 加總兩市每股「張數」——張數跨股加總無金額意義。
改版:市場融資一律以**金額(億元)**呈現(個股籌碼維持張,單位分工明確)。

來源(spike 2026-07-07 驗證,皆支援歷史 date 參數,一年回補可行):
  上市:TWSE rwd marginTrading/MI_MARGN?selectType=MS → 「融資金額(仟元)」今日餘額
  上櫃:TPEx www/zh-tw/margin/balance → summary「融資金(仟元)」資餘額

表:macro.db margin_daily(date PK, twse_k, tpex_k, total_yi)   ── 仟元 / 億元
統計(給 macro.json 脈絡字,交接包 §6):
  streak     連增(+N)/連減(−N)天數
  percentile 近一年(≤252 交易日)百分位:今日值 ≥ 歷史值的比例
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

TZ = timezone(timedelta(hours=8))
MACRO_DB = os.path.join(PROJECT_ROOT, "macro.db")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TWSE_MS = ("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
           "?date={ymd}&selectType=MS&response=json")
TPEX_BAL = "https://www.tpex.org.tw/www/zh-tw/margin/balance?date={ymds}&response=json"


def _http_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def fetch_twse_amount_k(date_iso: str, *, fetch=_http_json) -> float | None:
    """上市融資金額今日餘額(仟元);非交易日/未出檔回 None。"""
    j = fetch(TWSE_MS.format(ymd=date_iso.replace("-", "")))
    if j.get("stat") != "OK":
        return None
    for t in j.get("tables", []):
        fields = t.get("fields", [])
        if "今日餘額" not in fields:
            continue
        idx = fields.index("今日餘額")
        for row in t.get("data", []):
            if "融資金額" in str(row[0]):
                return _num(row[idx])
    return None


def fetch_tpex_amount_k(date_iso: str, *, fetch=_http_json) -> float | None:
    """上櫃融資金(仟元)資餘額(summary 列);非交易日回 None。"""
    j = fetch(TPEX_BAL.format(ymds=date_iso.replace("-", "/")))
    if str(j.get("stat", "")).lower() != "ok":
        return None
    for t in j.get("tables", []):
        fields = t.get("fields", [])
        # summary 列:['', '融資金(仟元)', 前餘額, 買, 賣, 償, 資餘額, ...] — 對齊 fields 的「資餘額」位
        try:
            bal_idx = fields.index("資餘額")
        except ValueError:
            continue
        for srow in t.get("summary", []):
            if any("融資金" in str(c) for c in srow[:3]):
                return _num(srow[bal_idx])
    return None


def _ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS margin_daily ("
                 "date TEXT PRIMARY KEY, twse_k REAL, tpex_k REAL, total_yi REAL)")


def upsert_day(db_path: str, date_iso: str, twse_k: float, tpex_k: float) -> float:
    """寫一天(仟元 → 億元 = /100_000)。回 total_yi。"""
    total_yi = round((twse_k + tpex_k) / 100_000, 1)
    conn = sqlite3.connect(db_path)
    _ensure(conn)
    conn.execute("INSERT OR REPLACE INTO margin_daily VALUES (?,?,?,?)",
                 (date_iso, twse_k, tpex_k, total_yi))
    conn.commit()
    conn.close()
    return total_yi


def collect_day(date_iso: str, db_path: str = MACRO_DB, *, fetch=_http_json) -> float | None:
    """抓當日兩市金額並入庫;任一市缺(非交易日/源掛)→ 不入庫回 None(N/A 護欄,不冒充)。"""
    tw = fetch_twse_amount_k(date_iso, fetch=fetch)
    tp = fetch_tpex_amount_k(date_iso, fetch=fetch)
    if tw is None or tp is None:
        return None
    return upsert_day(db_path, date_iso, tw, tp)


def stats(db_path: str, date_iso: str) -> dict | None:
    """{amount_yi, change_yi, change_pct, streak, percentile, days}(脈絡字資料)。
    streak:含今日的連增/連減天數(+N/−N;今日與前日同值 → 0)。
    percentile:近一年(≤252 筆)中 今日值 ≥ 歷史值 的比例(含自身),四捨五入整數。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT date, total_yi FROM margin_daily WHERE date <= ? "
            "ORDER BY date DESC LIMIT 252", (date_iso,)).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not rows or rows[0][0] != date_iso:
        return None
    seq = [v for _, v in rows]                    # 新→舊
    today = seq[0]
    prev = seq[1] if len(seq) >= 2 else None
    change_yi = round(today - prev, 1) if prev is not None else None
    change_pct = round((today - prev) / prev * 100, 2) if prev else None
    # streak
    streak = 0
    if prev is not None and today != prev:
        direction = 1 if today > prev else -1
        streak = direction
        for i in range(1, len(seq) - 1):
            d = seq[i] - seq[i + 1]
            if (d > 0 and direction == 1) or (d < 0 and direction == -1):
                streak += direction
            else:
                break
    percentile = round(sum(1 for v in seq if today >= v) / len(seq) * 100)
    return {"amount_yi": today, "change_yi": change_yi, "change_pct": change_pct,
            "streak": streak, "percentile": percentile, "days": len(seq)}


def backfill(db_path: str = MACRO_DB, days: int = 380) -> int:
    """回補近 days 個日曆日(≈252 交易日);非交易日自動跳過。冪等(REPLACE)。"""
    conn = sqlite3.connect(db_path)
    _ensure(conn)
    have = {r[0] for r in conn.execute("SELECT date FROM margin_daily")}
    conn.close()
    got = 0
    today = datetime.now(TZ).date()
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        wd = (today - timedelta(days=i)).weekday()
        if wd >= 5 or d in have:
            continue
        try:
            v = collect_day(d, db_path)
        except Exception as e:                    # noqa: BLE001 — 單日失敗跳過
            print(f"[backfill] {d} 例外:{str(e)[:50]}", file=sys.stderr)
            v = None
        if v is not None:
            got += 1
            if got % 20 == 0:
                print(f"[backfill] 已補 {got} 日(至 {d} = {v} 億)")
        else:
            # 平日拿不到 = 假日 or 來源限流回「查無」——記 log 讓限流可見
            # (2026-07-07 首輪回補教訓:限流被當非交易日靜默跳過,缺 5 個月)
            print(f"[backfill] {d} 平日無值(假日或限流)", file=sys.stderr)
        time.sleep(0.8)                            # 對官方端點客氣(首輪 0.5s 觸發限流)
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=MACRO_DB)
    ap.add_argument("--backfill", type=int, default=0, metavar="DAYS")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    if args.backfill:
        n = backfill(args.db, args.backfill)
        print(f"✅ 回補 {n} 個交易日")
    d = args.date or datetime.now(TZ).strftime("%Y-%m-%d")
    v = collect_day(d, args.db)
    st = stats(args.db, d)
    print(f"✅ {d}:{v} 億" if v else f"⏭️ {d} 無資料(非交易日/未出檔)")
    if st:
        print(f"   脈絡:{st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
