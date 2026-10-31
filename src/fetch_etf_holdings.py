"""
fetch_etf_holdings.py — 每日抓主動式 ETF 持股快照(PCF),存 etf_holdings.db。

2026-09-19 上線:台新(00987A)、統一(00981A/00403A)、群益(00992A)。
          第一金(00994A)/中信(00995A)待各摸 API 後加入 SOURCES。

背景:原 etfedge.xyz 於 2026-07 商業化斷更,改自建——抓各投信法定每日 PCF、
存快照,昨天今天股數/權重相減即得加減碼(比 etfedge 更本源,且 PCF 可回溯歷史)。

設計:
  - 獨立 db(etf_holdings.db),★不碰舊系統 ~/ETF追蹤/etf_operations.db。
  - PK(data_date, etf_code, stock_code)→ 同日同檔同股只一份,重跑冪等。
  - 失敗不擋下游(main 永遠 exit 0);連續 N 個交易日無新資料 → Discord 告警
    (防 etfedge 那種靜默斷更兩個月才發現)。交易日曆取 chips 表(來源自報日)。
  - 2026-10-31:自動補抓「上次有資料之後」漏掉的交易日(停更兩週即靠此補回);
    run_all 中移到健康檢查前([pre]),TV 掛掉照抓。
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
STALE_ALERT_DAYS = 3   # 連續 N 個「交易日」無新資料 → 告警(週末/連假不計)
CATCHUP_MAX_DAYS = 10  # 自動補抓最多回補幾個漏掉的交易日(PCF 可帶日期回溯)
KLINE_DB = os.path.join(PROJECT_ROOT, "kline.db")   # 交易日曆:chips 表(來源自報日)

from src.etf_pcf_taishin import fetch_taishin
from src.etf_pcf_tongyi import fetch_tongyi
from src.etf_pcf_capital import fetch_capital

# ETF → (抓取函式, 來源標記)。加新投信在此擴充。
# 抓取函式簽名統一為 fn(etf, date_iso) → (holdings, fund)。
SOURCES = {
    "00987A": (fetch_taishin, "taishin"),   # 台新:GET 靜態 HTML
    "00981A": (fetch_tongyi, "tongyi"),     # 統一:XLSX(需 session cookie,模組內處理)
    "00403A": (fetch_tongyi, "tongyi"),
    "00992A": (fetch_capital, "capital"),   # 群益:JSON API(免 session)
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


def _last_date(conn, etf):
    row = conn.execute("SELECT MAX(data_date) FROM etf_holdings WHERE etf_code=?",
                       (etf,)).fetchone()
    return row[0] if row and row[0] else None


def _tw_trading_days(after: str, upto: str) -> list[str] | None:
    """台股交易日(after, upto],取自 kline.db chips 表的日期——chips 以來源自報日入庫
    (fetch_chips 在 run_all 早於本步、且不依賴 TV),不自己推算國定假日/颱風假。
    chips 本身停更(最新日比 upto 早 >4 日曆日)或讀不到 → None(呼叫端退回平日計數)。"""
    try:
        conn = sqlite3.connect(f"file:{KLINE_DB}?mode=ro", uri=True)
        latest = conn.execute("SELECT MAX(date) FROM chips").fetchone()[0]
        days = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM chips WHERE date > ? AND date <= ? ORDER BY date",
            (after, upto))]
        conn.close()
    except sqlite3.Error:
        return None
    if not latest or (datetime.strptime(upto, "%Y-%m-%d")
                      - datetime.strptime(latest, "%Y-%m-%d")).days > 4:
        return None
    return days


def _trading_days_since(last: str | None, today: str) -> int:
    """距上次有資料過了幾個交易日(週末/連假不計);日曆不可用時退回平日(週一~五)計數。"""
    if not last:
        return 999
    days = _tw_trading_days(last, today)
    if days is not None:
        return len(days)
    d, n = datetime.strptime(last, "%Y-%m-%d").date(), 0
    end = datetime.strptime(today, "%Y-%m-%d").date()
    while d < end:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def main() -> int:
    """無參數:抓今天 + 自動補「上次有資料之後」漏掉的交易日(PCF 可帶日期回溯,
    2026-10 停更兩週即靠此補回);帶日期參數:只抓該日。"""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    explicit = sys.argv[1] if len(sys.argv) > 1 else None
    now = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    conn = sqlite3.connect(DB)
    _ensure_db(conn)
    per_date: dict[str, tuple[list, list]] = {}
    for etf, (fn, src) in SOURCES.items():
        last = _last_date(conn, etf)
        cal = _tw_trading_days(last, today) if (last and not explicit) else None
        if explicit:
            dates = [explicit]
        elif cal is None:
            dates = [today]                   # 新檔/日曆不可用:退回只抓今天(原行為)
        else:
            dates = cal[-CATCHUP_MAX_DAYS:]   # 來源說的交易日(含今天);週末/假日自然為空
        for d in dates:
            ok, failed = per_date.setdefault(d, ([], []))
            try:
                hold, fund = fn(etf, d)
                if not hold:
                    failed.append(etf)        # 非交易日回空也算「該日無資料」
                    continue
                for h in hold:
                    conn.execute(
                        "INSERT OR IGNORE INTO etf_holdings VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (d, etf, h["code"], h["name"], h["shares"], h["weight"],
                         fund.get("nav"), fund.get("units"), src, now))
                conn.commit()
                ok.append(f"{etf}({len(hold)})")
            except Exception as e:                      # noqa: BLE001
                failed.append(etf)
                print(f"[etf_holdings] ❌ {etf} {d}: {str(e)[:80]}", file=sys.stderr)

    for d, (ok, failed) in sorted(per_date.items()):
        print(f"[etf_holdings] {d}: 存 {ok or '無'} | 失敗 {failed or '無'}")
    if not per_date:
        print(f"[etf_holdings] {today}: 無待抓交易日(各檔已是最新;週末/假日由 chips 日曆判定)")

    # 連續 N 個「交易日」無新資料告警(每檔各自看 db 最新日;週末/連假不誤報)
    for etf in SOURCES:
        gap = _trading_days_since(_last_date(conn, etf), today)
        if gap >= STALE_ALERT_DAYS:
            _discord(f"🚨 [ETF持股] {etf} 已連續 {gap} 個交易日無新持股資料"
                     f"(來源可能斷更,如 2026 年 etfedge)。請查 PCF 端點。")
    conn.close()
    return 0   # ★永遠 0:失敗不擋下游(比照原 daily_update 設計)


if __name__ == "__main__":
    sys.exit(main())
