"""
fetch_taifex.py — 外資台指期淨未平倉口數(stage12 §5.2 F1,待辦 §5-2b 落地)

來源:期交所「三大法人-區分各期貨契約」CSV 直下(POST futContractsDateDown,
     Big5 編碼,支援日期區間查詢;spike 2026-07-08 驗證單日 956B 即回)。
表:macro.db taifex_foreign_oi(date PK, net_oi 口, source)
    net_oi = 臺股期貨(TXF)「外資及陸資」多空未平倉口數淨額。
用途:週報訊號區「外資台指期淨 OI vs 大盤(雙軸)」;一年回補即足。
護欄:非交易日/未出檔回空;|net_oi| > 500,000 口視為異常不入庫。
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

TZ = timezone(timedelta(hours=8))
MACRO_DB = os.path.join(PROJECT_ROOT, "macro.db")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
SANE_ABS = 500_000


def _http_post_csv(start_iso: str, end_iso: str, timeout: int = 40) -> str:
    body = urllib.parse.urlencode({
        "queryStartDate": start_iso.replace("-", "/"),
        "queryEndDate": end_iso.replace("-", "/"),
        "commodityId": "TXF",
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("big5", "replace")


def parse_csv(text: str) -> list[tuple[str, int]]:
    """CSV → [(date_iso, net_oi)];取 商品=臺股期貨、身份別含「外資」列的
    「多空未平倉口數淨額」欄(依 header 定位,不寫死欄序)。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(",")]
    try:
        di = header.index("日期")
        pi = header.index("商品名稱")
        ii = header.index("身份別")
        ni = header.index("多空未平倉口數淨額")
    except ValueError:
        return []
    out = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(",")]
        if len(cells) <= max(di, pi, ii, ni):
            continue
        if cells[pi] != "臺股期貨" or "外資" not in cells[ii]:
            continue
        try:
            net = int(float(cells[ni].replace(",", "")))
        except ValueError:
            continue
        iso = cells[di].replace("/", "-")
        if len(iso) == 10 and abs(net) <= SANE_ABS:
            out.append((iso, net))
    return out


def _ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS taifex_foreign_oi ("
                 "date TEXT PRIMARY KEY, net_oi INTEGER, source TEXT)")


def upsert(db_path: str, rows: list[tuple[str, int]]) -> int:
    conn = sqlite3.connect(db_path)
    _ensure(conn)
    conn.executemany(
        "INSERT OR REPLACE INTO taifex_foreign_oi VALUES (?,?,?)",
        [(d, n, "TAIFEX futContractsDateDown TXF 外資") for d, n in rows])
    conn.commit()
    conn.close()
    return len(rows)


def collect_range(start_iso: str, end_iso: str, db_path: str = MACRO_DB,
                  *, fetch=_http_post_csv) -> int:
    return upsert(db_path, parse_csv(fetch(start_iso, end_iso)))


def backfill(db_path: str = MACRO_DB, days: int = 400) -> int:
    """逐月區間查詢回補(~14 請求,0.8s 禮貌間隔)。"""
    end = datetime.now(TZ).date()
    start = end - timedelta(days=days)
    total = 0
    cur = start
    while cur <= end:
        seg_end = min(end, (cur.replace(day=1) + timedelta(days=40)).replace(day=1)
                      - timedelta(days=1))
        try:
            n = collect_range(cur.isoformat(), seg_end.isoformat(), db_path)
            total += n
            print(f"[taifex] {cur}~{seg_end}:{n} 日")
        except Exception as e:                     # noqa: BLE001
            print(f"[taifex] {cur}~{seg_end} 例外:{str(e)[:60]}", file=sys.stderr)
        cur = seg_end + timedelta(days=1)
        time.sleep(0.8)
    return total


def run_daily(db_path: str = MACRO_DB) -> int:
    """近 7 日補抓(冪等;假日自然無列)。
    期交所對「尚無資料的結束日」回 DateTime error 網頁(當日約 15:00 才出檔,
    半夜/早晨跑會中招)→ 結束日逐日回退最多 3 次。"""
    end = datetime.now(TZ).date()
    n = 0
    for back in range(4):
        e = end - timedelta(days=back)
        n = collect_range((e - timedelta(days=7)).isoformat(), e.isoformat(), db_path)
        if n:
            break
    print(f"✅ taifex 外資淨 OI upsert {n} 日")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=MACRO_DB)
    ap.add_argument("--backfill", type=int, default=0, metavar="DAYS")
    args = ap.parse_args()
    if args.backfill:
        print(f"✅ 回補 {backfill(args.db, args.backfill)} 日")
        return 0
    return run_daily(args.db)


if __name__ == "__main__":
    sys.exit(main())
