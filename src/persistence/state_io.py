"""
state_io.py — standing_state 表的 CRUD 純 SQL 層(W2.1 Phase 1)

設計原則:
  - 純 SQL,不依賴 src/triggers/standing.py 的 state machine 邏輯。
    雙向解耦:standing.py 用 dict 表示狀態,state_io 知道怎麼存讀 dict。
  - caller 傳 connection,本檔不管 connection 生命週期。
    測試傳 :memory:、production 傳 kline.db,同一份 code。
  - state 欄位不做 enum 驗證(規則 v2.1 已固定 5 種,caller 負責)。
    如果未來規則加狀態,本檔不用改。

4 個 helper(W4 上線前可能加 delete_orphaned_states):
  init_schema             跑 migration SQL,建表 + index
  read_state              讀單一 row(給 evaluate_standing 當 prev_state)
  write_state             UPSERT(evaluate_standing 算完後寫回)
  read_states_for_symbol  讀單檔所有 row(網站 tooltip / debug 用)

Schema:見 migrations/001_standing_state.sql。
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "001_standing_state.sql"
)


def init_schema(conn: sqlite3.Connection) -> None:
    """套用 migrations/001_standing_state.sql。

    idempotent — 重跑無副作用(SQL 內全是 CREATE IF NOT EXISTS)。
    Production 跑一次,unittest 每次 setUp 跑。
    """
    with open(MIGRATION_PATH, encoding="utf-8") as f:
        sql = f.read()
    conn.executescript(sql)
    conn.commit()


def read_state(
    conn: sqlite3.Connection,
    symbol: str,
    category: str,
    price_str: str,
) -> dict | None:
    """讀單一 row。

    回傳格式跟 evaluate_standing 期望的 prev_state 一致:
        {"state": str, "trigger_date": str|None, "standing_date": str|None}

    若 (symbol, category, price_str) 從未評估過 → 回 None
    (caller 傳給 evaluate_standing 時,None 會被視為 UNTRIGGERED)。
    """
    cur = conn.execute(
        "SELECT state, trigger_date, standing_date "
        "FROM standing_state "
        "WHERE symbol = ? AND category = ? AND price_str = ?",
        (symbol, category, price_str),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "state":         row[0],
        "trigger_date":  row[1],
        "standing_date": row[2],
    }


def write_state(
    conn: sqlite3.Connection,
    symbol: str,
    category: str,
    price_str: str,
    state_dict: dict,
    last_updated: str,
) -> None:
    """UPSERT。新 row 直接 INSERT,既有 row 更新 5 個欄位。

    state_dict 必含: "state"(5 種 enum 之一)
    state_dict 可選: "trigger_date" / "standing_date"(可為 None)
    last_updated 由 caller 傳 ISO datetime(讓測試可控時間)。

    不 commit;caller 視 batch 邊界決定 commit 時機。
    """
    conn.execute(
        "INSERT INTO standing_state "
        "(symbol, category, price_str, state, trigger_date, standing_date, last_updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol, category, price_str) DO UPDATE SET "
        "  state         = excluded.state, "
        "  trigger_date  = excluded.trigger_date, "
        "  standing_date = excluded.standing_date, "
        "  last_updated  = excluded.last_updated",
        (
            symbol,
            category,
            price_str,
            state_dict["state"],
            state_dict.get("trigger_date"),
            state_dict.get("standing_date"),
            last_updated,
        ),
    )


def read_states_for_symbol(
    conn: sqlite3.Connection,
    symbol: str,
) -> list[dict]:
    """單檔所有 row,給網站 tooltip / debug 用。

    回傳 list of dict,每個含完整欄位(category / price_str / state /
    trigger_date / standing_date / last_updated)。
    依 (category, price_str) 排序,輸出穩定方便 debug。

    若該 symbol 沒有任何 row → 回空 list。
    """
    cur = conn.execute(
        "SELECT category, price_str, state, trigger_date, standing_date, last_updated "
        "FROM standing_state "
        "WHERE symbol = ? "
        "ORDER BY category, price_str",
        (symbol,),
    )
    return [
        {
            "category":      row[0],
            "price_str":     row[1],
            "state":         row[2],
            "trigger_date":  row[3],
            "standing_date": row[4],
            "last_updated":  row[5],
        }
        for row in cur.fetchall()
    ]
