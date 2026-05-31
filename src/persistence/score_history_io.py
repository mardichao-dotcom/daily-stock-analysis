"""
score_history_io.py — score_history 表的 CRUD + rotation 用的彙總查詢(W2.2.6)

跟 standing_state_io / etf_io / kline_io 同 pattern:
  - 純 SQL,不知道 scoring 邏輯
  - caller 管 connection
  - 不 commit(交給 caller batch transaction)

主要 API:
  init_schema                     — 跑 002 migration
  write_batch                     — 每日跑完 batch UPSERT 所有 stocks
  compute_sector_avg_over_days    — rotation 用的「過去 N 個交易日族群均分」
  read_scores_for_symbols_over_window — debug / backtest 用
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "002_score_history.sql"
)


def init_schema(conn: sqlite3.Connection) -> None:
    """套用 migrations/002_score_history.sql。idempotent。"""
    with open(MIGRATION_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


def write_batch(
    conn: sqlite3.Connection,
    date: str,
    results: dict,
    last_updated: str,
) -> int:
    """Batch UPSERT 今日跑完的所有 stocks。

    Parameters
    ----------
    conn         : kline.db connection(同庫 transaction)
    date         : ISO date(今天)
    results      : dict[symbol, stock_entry] from run_pipeline
                   每個 entry 必含 "score";可選 "grade"
    last_updated : ISO datetime(caller 統一傳,讓測試可控時間)

    Returns
    -------
    int : 寫入的 row 數(等於 len(results))

    不 commit;caller 決定 transaction 邊界。
    """
    n = 0
    for symbol, entry in results.items():
        conn.execute(
            "INSERT INTO score_history (date, symbol, score, grade, last_updated) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(date, symbol) DO UPDATE SET "
            "  score        = excluded.score, "
            "  grade        = excluded.grade, "
            "  last_updated = excluded.last_updated",
            (
                date,
                symbol,
                float(entry["score"]),
                entry.get("grade"),
                last_updated,
            ),
        )
        n += 1
    return n


def compute_sector_avg_over_days(
    conn: sqlite3.Connection,
    members: list[str],
    end_date_exclusive: str,
    n_days: int = 5,
) -> float | None:
    """rotation 主查詢:該族群過去 N 個有資料的交易日的「每日族群均分」再平均。

    ⚠️ **關鍵 SQL 設計**:必須先 GROUP BY date 算「各日族群均分」,
       再對這些 daily averages 平均。不可以對所有 row 直接 AVG,
       否則停牌成員少的那天會被權重不均(per W2.2.6 review)。

    「過去 N 個交易日」=「最近 N 個有 score_history 記錄的日期」,
    **不要求連續**(系統偶爾漏跑一天不該讓 rotation 完全失效)。
    用 ORDER BY date DESC LIMIT N 自然取最近 N 個有資料的日期。

    Parameters
    ----------
    conn               : kline.db connection
    members            : 該族群所有成員 symbol(從 watchlist 來)
    end_date_exclusive : 「今天」— 嚴格排除(不含)
    n_days             : 預設 5

    Returns
    -------
    float | None : 若可用日期 < n_days → None(history 不足,rotation skip)
    """
    if not members:
        return None

    placeholders = ",".join("?" * len(members))
    # 先算每天的族群均分(各日獨立),再對這些 daily averages 平均
    sql = f"""
        SELECT date, AVG(score) AS daily_avg
        FROM score_history
        WHERE symbol IN ({placeholders})
          AND date < ?
        GROUP BY date
        ORDER BY date DESC
        LIMIT {int(n_days)}
    """
    cur = conn.execute(sql, (*members, end_date_exclusive))
    rows = cur.fetchall()  # [(date, daily_avg), ...]
    if len(rows) < n_days:
        return None
    return sum(r[1] for r in rows) / len(rows)


def read_scores_for_symbols_over_window(
    conn: sqlite3.Connection,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Debug / backtest helper:讀某 symbol 們在某日期範圍的所有 score。

    回傳 list[dict],依 (date asc, symbol asc) 排序。
    """
    if not symbols:
        return []
    placeholders = ",".join("?" * len(symbols))
    cur = conn.execute(
        f"SELECT date, symbol, score, grade, last_updated "
        f"FROM score_history "
        f"WHERE symbol IN ({placeholders}) "
        f"  AND date >= ? AND date <= ? "
        f"ORDER BY date ASC, symbol ASC",
        (*symbols, start_date, end_date),
    )
    return [
        {"date": r[0], "symbol": r[1], "score": r[2],
         "grade": r[3], "last_updated": r[4]}
        for r in cur.fetchall()
    ]
