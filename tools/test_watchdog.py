"""
test_watchdog.py — 新鮮度告警觸發實證(P1 §6.6)

「邏輯在但沒驗證過會響」。本工具人工製造一次 stale 條件,確認 daily_supervisor 的
freshness watchdog 真的產生 🚨 告警(預設只預覽;--send 真的發 Discord 供截圖)。

做法:用一份「最新 K 線 = N 天前」的暫時 kline.db,把 daily_supervisor.KLINE_DB
指過去 → 不碰正式 kline.db,跑完即丟(天然「驗完還原」)。

用法:
    python3 tools/test_watchdog.py            # 預覽告警(不發 Discord)
    python3 tools/test_watchdog.py --send     # 真的發到 Discord(截圖用)
    python3 tools/test_watchdog.py --days 10  # 製造 10 天 stale
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import daily_supervisor as ds


def _make_stale_db(path: str, stale_days: int) -> str:
    """建一份最新 bar = stale_days 天前的暫時 kline.db。"""
    old_date = (datetime.now(ds.TZ_TAIPEI).date()
                - timedelta(days=stale_days)).isoformat()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE kline (symbol TEXT, date TEXT, close REAL, "
                 "PRIMARY KEY (symbol, date))")
    conn.execute("INSERT INTO kline VALUES ('TWSE:2330', ?, 1000)", (old_date,))
    conn.commit()
    conn.close()
    return old_date


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=ds.FRESHNESS_ALERT_DAYS + 2,
                    help=f"製造幾天 stale(預設 {ds.FRESHNESS_ALERT_DAYS + 2},門檻 {ds.FRESHNESS_ALERT_DAYS})")
    ap.add_argument("--send", action="store_true", help="真的發 Discord(否則只預覽)")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        stale_db = os.path.join(tmp, "kline_stale.db")
        old_date = _make_stale_db(stale_db, args.days)

        # 把 watchdog 的 KLINE_DB 指到 stale 暫時庫(不碰正式庫)
        orig_kline, orig_etf = ds.KLINE_DB, ds.ETF_DB
        ds.KLINE_DB = stale_db
        ds.ETF_DB = os.path.join(tmp, "no_etf.db")   # 不存在 → ETF 不告警,聚焦 K 線
        try:
            warnings = ds._check_data_freshness()
            print(f"製造 stale:最新 K 線 = {old_date}({args.days} 天前,門檻 {ds.FRESHNESS_ALERT_DAYS})")
            if not warnings:
                print("❌ 預期應觸發告警,但 watchdog 沒回任何 warning — 邏輯有問題!")
                return 1
            print(f"✅ watchdog 觸發 {len(warnings)} 則告警:")
            for w in warnings:
                print(f"   {w}")

            # 組一則最小 Discord 訊息(帶 freshness 告警)
            fake_status = {"stock_dashboard": {
                "overall": "ok", "finished_at": "x",
                "steps": [{"name": "tv_collect", "status": "ok", "note": "(stale 測試)"}]}}
            message = ds._build_message(fake_status)
        finally:
            ds.KLINE_DB, ds.ETF_DB = orig_kline, orig_etf

    print("\n── Discord 訊息預覽 ──")
    print(message)

    if args.send:
        webhook = ds._load_webhook()
        if not webhook:
            print("\n⚠️ 未設定 webhook,無法發送。")
            return 1
        ds._send(webhook, "🧪 [watchdog 觸發實證 §6.6] 以下為人工製造 stale 的告警測試\n" + message)
        print("\n✅ 已發送到 Discord — 請截圖,並注意這是測試訊息(正式 kline.db 未被動)。")
    else:
        print("\n(預覽模式,未發送。加 --send 真的發到 Discord 供截圖。)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
