"""
taiex_daily.py — 台股加權指數日收盤序列(2026-07-07 stage10 後續調整,schema 已批准)

用途(一魚兩吃):
  1. 週報融資圖雙軸:左軸融資(億元,margin_daily)、右軸加權指數(本表)
  2. §17 歷史列「大盤漲跌幅」欄(先前落差清單待裁項,序列到位後補上)

來源:TWSE rwd afterTrading/FMTQIK(每日市場成交資訊,**月檔**——一年僅 ~13 請求,
     不會踩融資回補的限流雷)。民國日期、逗號數字。
表:macro.db taiex_daily(date PK, close, change_pts)——與 margin_daily 同構同庫。
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
FMTQIK = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={ym}01&response=json"


def _http_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _roc_to_iso(roc: str) -> str | None:
    """'115/07/07' → '2026-07-07'。"""
    try:
        y, m, d = str(roc).strip().split("/")
        return f"{int(y) + 1911:04d}-{m}-{d}"
    except (ValueError, AttributeError):
        return None


def fetch_month(ym: str, *, fetch=_http_json) -> list[tuple[str, float, float]]:
    """ym='202607' → [(date_iso, close, change_pts)];非交易月/未出回 []。"""
    j = fetch(FMTQIK.format(ym=ym))
    if j.get("stat") != "OK":
        return []
    fields = j.get("fields", [])
    try:
        di = fields.index("日期")
        ci = fields.index("發行量加權股價指數")
        pi = fields.index("漲跌點數")
    except ValueError:
        return []
    out = []
    for row in j.get("data", []):
        iso = _roc_to_iso(row[di])
        close = _num(row[ci])
        if iso and close:
            out.append((iso, close, _num(row[pi]) or 0.0))
    return out


def _ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS taiex_daily ("
                 "date TEXT PRIMARY KEY, close REAL, change_pts REAL)")


def upsert_month(db_path: str, rows: list) -> int:
    conn = sqlite3.connect(db_path)
    _ensure(conn)
    conn.executemany("INSERT OR REPLACE INTO taiex_daily VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


def collect_current_month(db_path: str = MACRO_DB) -> int:
    """抓當月月檔 upsert(冪等;08:30 fetch_macro 每日順手呼叫,1 請求)。"""
    ym = datetime.now(TZ).strftime("%Y%m")
    return upsert_month(db_path, fetch_month(ym))


def backfill(db_path: str = MACRO_DB, months: int = 14) -> int:
    """回補近 months 個月(月檔冪等 REPLACE;~13 請求覆蓋一年)。"""
    total = 0
    cur = datetime.now(TZ).replace(day=1)
    for _ in range(months):
        ym = cur.strftime("%Y%m")
        try:
            n = upsert_month(db_path, fetch_month(ym))
            total += n
            print(f"[taiex] {ym}:{n} 日")
        except Exception as e:                    # noqa: BLE001
            print(f"[taiex] {ym} 例外:{str(e)[:50]}", file=sys.stderr)
        cur = (cur - timedelta(days=1)).replace(day=1)
        time.sleep(0.8)
    return total


def closes_for(db_path: str, dates: list[str]) -> dict[str, float]:
    """給定日期清單 → {date: close}(週報雙軸圖對齊 margin 序列用)。"""
    if not dates:
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        q = ",".join("?" * len(dates))
        rows = conn.execute(
            f"SELECT date, close FROM taiex_daily WHERE date IN ({q})", dates).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return dict(rows)


def chg_pct(db_path: str, date_iso: str) -> float | None:
    """該日大盤漲跌 %(close 與前一交易日比;§17 歷史列用)。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT date, close FROM taiex_daily WHERE date <= ? "
            "ORDER BY date DESC LIMIT 2", (date_iso,)).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if len(rows) < 2 or rows[0][0] != date_iso or not rows[1][1]:
        return None
    return round((rows[0][1] - rows[1][1]) / rows[1][1] * 100, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=MACRO_DB)
    ap.add_argument("--backfill", type=int, default=0, metavar="MONTHS")
    args = ap.parse_args()
    if args.backfill:
        n = backfill(args.db, args.backfill)
        print(f"✅ 回補共 {n} 日")
    else:
        print(f"✅ 當月 upsert {collect_current_month(args.db)} 日")
    return 0


if __name__ == "__main__":
    sys.exit(main())
