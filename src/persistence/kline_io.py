"""
kline_io.py — kline.db 讀取 helpers(W2.2.2)

純 SQL 讀取 kline.db,不寫入(5A 既有 schema,凍結)。

對齊 5A schema(只讀):
  kline.db.kline:
    symbol TEXT, date TEXT, open REAL, high REAL, low REAL,
    close REAL, volume REAL
"""
from __future__ import annotations
import sqlite3


def compute_kline_max_date(conn: sqlite3.Connection) -> str | None:
    """回傳 kline 表的 MAX(date),給 metadata.data_date_in_db 用。

    跨所有 symbol 取最大值。None 代表表為空。

    ⚠️ 已知限制(stage8_pending_review.md):
        早盤(09:00 前)跑時,日本 TSE 已開盤所以 MAX(date) 可能是
        「今天」但台股還沒收盤,實際資料不完整。19:00 正式排程不受影響。
        未來修法是 WHERE symbol LIKE 'TWSE:%' OR 'TPEX:%'(待 stage8+)。
    """
    cur = conn.execute("SELECT MAX(date) FROM kline")
    row = cur.fetchone()
    return row[0] if row and row[0] else None
