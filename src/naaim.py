"""
naaim.py — NAAIM Exposure Index 官方歷史檔全量重建(stage9 Day3 §3.3)

來源:naaim.org 官方「USE_Data since Inception」xlsx(自 2006 至今,每週更新)。
舊 repo 的寫死 NAAIM_SEED 來源不明 → **禁用**,全量從官方檔重建入 macro.db。

流程:抓 naaim.org exposure-index 頁 → 正則出當月 xlsx 連結(檔名含日期,逐月變)→
下載 → 解析 Date + Mean/Average(= 官方揭露的曝險指數)→ 全量 REPLACE 入 macro.db.naaim。
"""
from __future__ import annotations
import io
import os
import re
import sqlite3
import urllib.request
from datetime import datetime

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
MACRO_DB = os.path.join(PROJECT_ROOT, "macro.db")
NAAIM_PAGE = "https://naaim.org/programs/naaim-exposure-index/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _http_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def current_xlsx_url() -> str:
    """從 NAAIM 頁抓出當前『USE_Data-since-Inception』xlsx 連結(檔名逐月變)。"""
    html = _http_bytes(NAAIM_PAGE).decode("utf-8", "replace")
    m = re.search(
        r'https://naaim\.org/wp-content/uploads/[^"\']*USE_Data[^"\']*\.xlsx', html)
    if not m:
        raise RuntimeError("NAAIM 頁找不到 USE_Data xlsx 連結(頁面可能改版)")
    return m.group(0)


def parse_history(xlsx_bytes: bytes) -> list[tuple[str, float]]:
    """解析 xlsx → [(date_iso, exposure), ...]。exposure = Mean/Average 欄。"""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    # 找 Date 與 Mean/Average 欄位索引(容忍欄序變動)
    def _col(name_frag):
        for i, h in enumerate(header):
            if h and name_frag.lower() in str(h).lower():
                return i
        return None
    di = _col("date")
    ei = _col("mean")
    if di is None or ei is None:
        raise RuntimeError(f"NAAIM xlsx 欄位不符(header={header})")
    out: dict[str, float] = {}
    for r in rows:
        d, e = r[di], r[ei]
        if isinstance(d, datetime) and isinstance(e, (int, float)):
            out[d.strftime("%Y-%m-%d")] = round(float(e), 2)   # dedup by date
    return sorted(out.items())


def rebuild(db_path: str = MACRO_DB) -> dict:
    """全量重建 macro.db.naaim。回 {count, latest_date, latest_value, source_url}。"""
    url = current_xlsx_url()
    data = parse_history(_http_bytes(url))
    if not data:
        raise RuntimeError("NAAIM 解析結果為空")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS naaim ("
                 "date TEXT PRIMARY KEY, exposure REAL NOT NULL)")
    conn.execute("DELETE FROM naaim")                          # 全量重建,不留舊 seed
    conn.executemany("INSERT INTO naaim VALUES (?,?)", data)
    conn.commit()
    conn.close()
    return {"count": len(data), "latest_date": data[-1][0],
            "latest_value": data[-1][1], "source_url": url}


def read_series(db_path: str = MACRO_DB, weeks: int = 104) -> list[tuple[str, float]]:
    """讀最近 `weeks` 筆(升冪)給圖表/警報用。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT date, exposure FROM naaim ORDER BY date DESC LIMIT ?", (weeks,)
    ).fetchall()
    conn.close()
    return list(reversed(rows))


if __name__ == "__main__":
    r = rebuild()
    print(f"✅ NAAIM 全量重建:{r['count']} 筆,最新 {r['latest_date']} = {r['latest_value']}")
    print(f"   來源:{r['source_url']}")
