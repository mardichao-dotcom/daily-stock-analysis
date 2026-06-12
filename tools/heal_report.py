"""
heal_report.py — P0-C 自癒實證:比對 6/12 盤中半成品 bar 覆寫前後(hotfix 2026-06-12)

背景:6/12 主跑重跑於 22:00(美股已開盤)抓到 24 檔盤中半成品 6/12 bar。
  - 美股 23 檔:應由 6/13 05:30 us_refresh(REPLACE)覆寫成收盤值 → 驗「補跑側自癒」
  - OMXCOP:MAERSK_B:應由 6/13 19:00 主跑 refresh 最近 3 根覆寫 → 驗「主跑側自癒」

用法(05:30 後 / 19:00 後執行):
    python3 tools/heal_report.py

讀 logs/heal_evidence_2026-06-12.json(覆寫前快照)對照當前 kline.db,列出每檔
close 前→後變化 + 線上 NVDA 6/12 bar + report_suspicious_bars 是否清空。
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from src import exchange_hours as eh

EVID = os.path.join(PROJECT_ROOT, "logs", "heal_evidence_2026-06-12.json")
DATE = "2026-06-12"


def main() -> int:
    if not os.path.exists(EVID):
        print(f"❌ 找不到覆寫前快照 {EVID}")
        return 1
    before = json.load(open(EVID, encoding="utf-8"))["kline_before"]

    conn = sqlite3.connect(f"file:{os.path.join(PROJECT_ROOT,'kline.db')}?mode=ro", uri=True)
    changed, unchanged = [], []
    for sym, b in sorted(before.items()):
        row = conn.execute(
            "SELECT close FROM kline WHERE symbol=? AND date=?", (sym, DATE)).fetchone()
        after = row[0] if row else None
        if after is None:
            unchanged.append((sym, b["close"], "<bar 消失>"))
        elif abs(after - b["close"]) > 1e-9:
            changed.append((sym, b["close"], after))
        else:
            unchanged.append((sym, b["close"], after))
    conn.close()

    print(f"── P0-C 自癒實證({DATE} 盤中半成品覆寫前後)──")
    print(f"覆寫前快照:{len(before)} 檔｜已被覆寫(close 變動):{len(changed)}｜未變:{len(unchanged)}\n")
    print("已覆寫(收盤值已就位):")
    for sym, b, a in changed:
        print(f"  {sym:18} close {b} → {a}")
    if unchanged:
        print("\n尚未覆寫(等對應自癒排程):")
        for sym, b, a in unchanged:
            print(f"  {sym:18} close {b}(現 {a})")

    # 線上 NVDA 6/12 bar
    try:
        u = ("https://mardichao-dotcom.github.io/daily-stock-analysis"
             f"/data/v2/{DATE}/NASDAQ_NVDA.json")
        j = json.load(urllib.request.urlopen(u, timeout=10))
        bar = j["ohlcv"][-1]
        print(f"\n線上 NVDA {DATE} bar:close={bar['close']} "
              f"(覆寫前 {before.get('NASDAQ:NVDA',{}).get('close')})")
    except Exception as e:
        print(f"\n線上 NVDA 取得失敗:{e}")

    # 剩餘半成品嫌疑(理想:美股清空 / MAERSK 視 19:00 是否已跑)
    conn = sqlite3.connect(f"file:{os.path.join(PROJECT_ROOT,'kline.db')}?mode=ro", uri=True)
    suspects = eh.suspicious_symbols(conn, datetime.now())
    conn.close()
    print(f"\nreport_suspicious_bars(現在):{len(suspects)} 檔仍嫌疑"
          + ("" if not suspects else " → " + ", ".join(s[0] for s in suspects)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
