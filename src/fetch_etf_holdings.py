"""
fetch_etf_holdings.py — 每日抓主動式 ETF 持股快照(PCF),存 etf_holdings.db。

2026-09-19 第一階段:台新(00987A)。群益/統一/第一金/中信 待各摸 API 後加入 SOURCES。

背景:原 etfedge.xyz 於 2026-07 商業化斷更,改自建——抓各投信法定每日 PCF、
存快照,昨天今天股數/權重相減即得加減碼(比 etfedge 更本源,且 PCF 可回溯歷史)。

設計:
  - 獨立 db(etf_holdings.db),★不碰舊系統 ~/ETF追蹤/etf_operations.db。
  - PK(data_date, etf_code, stock_code)→ 同日同檔同股只一份,重跑冪等。
  - 失敗不擋下游(main 永遠 exit 0);連續 N 天無新資料 → Discord 告警
    (防 etfedge 那種靜默斷更兩個月才發現)。
  - 純抓取存快照,不計分(計分邏輯之後另設計、不動現有 chip_etf)。
"""
from __future__ import annotations
import os
import sys
import sqlite3
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
DB = os.path.join(PROJECT_ROOT, "etf_holdings.db")
TZ = timezone(timedelta(hours=8))
STALE_ALERT_DAYS = 3   # 連續 N 天無新資料 → 告警

from src.etf_pcf_taishin import fetch_taishin

# ETF → (抓取函式, 來源標記)。加新投信在此擴充。
SOURCES = {
    "00987A": (fetch_taishin, "taishin"),
}


def _ensure_db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS etf_holdings (
      data_date TEXT NOT NULL, etf_code TEXT NOT NULL, stock_code TEXT NOT NULL,
      stock_name TEXT, shares INTEGER, weight_pct REAL, fund_nav REAL,
      units_issued INTEGER, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
      PRIMARY KEY (data_date, etf_code, stock_code))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_date "
                 "ON etf_holdings(etf_code, data_date)")


def _discord(msg):
    try:
        from src.daily_supervisor import _load_webhook, _send
        wh = _load_webhook()
        if wh:
            _send(wh, msg)
    except Exception as e:                              # noqa: BLE001
        print(f"[etf_holdings] Discord 告警失敗: {e}", file=sys.stderr)


def _days_since_last(conn, etf):
    row = conn.execute("SELECT MAX(data_date) FROM etf_holdings WHERE etf_code=?",
                       (etf,)).fetchone()
    if not row or not row[0]:
        return 999
    last = datetime.strptime(row[0], "%Y-%m-%d").date()
    return (datetime.now(TZ).date() - last).days


def main() -> int:
    date_iso = (sys.argv[1] if len(sys.argv) > 1
                else datetime.now(TZ).strftime("%Y-%m-%d"))
    now = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    conn = sqlite3.connect(DB)
    _ensure_db(conn)
    ok, failed = [], []
    for etf, (fn, src) in SOURCES.items():
        try:
            hold, fund = fn(etf, date_iso)
            if not hold:
                failed.append(etf)            # 非交易日回空也算「今日無資料」
                continue
            for h in hold:
                conn.execute(
                    "INSERT OR IGNORE INTO etf_holdings VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (date_iso, etf, h["code"], h["name"], h["shares"], h["weight"],
                     fund.get("nav"), fund.get("units"), src, now))
            conn.commit()
            ok.append(f"{etf}({len(hold)})")
        except Exception as e:                          # noqa: BLE001
            failed.append(etf)
            print(f"[etf_holdings] ❌ {etf}: {str(e)[:80]}", file=sys.stderr)

    print(f"[etf_holdings] {date_iso}: 存 {ok or '無'} | 失敗 {failed or '無'}")

    # 連續 N 天無新資料告警(每檔各自看 db 最新日;能撐過非交易日/週末)
    for etf in SOURCES:
        gap = _days_since_last(conn, etf)
        if gap >= STALE_ALERT_DAYS:
            _discord(f"🚨 [ETF持股] {etf} 已連續 {gap} 天無新持股資料"
                     f"(來源可能斷更,如 2026 年 etfedge)。請查 PCF 端點。")
    conn.close()
    return 0   # ★永遠 0:失敗不擋下游(比照原 daily_update 設計)


if __name__ == "__main__":
    sys.exit(main())
