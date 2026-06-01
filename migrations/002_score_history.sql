-- ============================================================================
-- 002_score_history.sql
-- ----------------------------------------------------------------------------
-- W2.2.6 score_history 表 — 每日分數歷史
-- 建立日期: 2026-05-28
-- 對應程式: src/persistence/score_history_io.py
-- ============================================================================
--
-- 用途:
--   每日跑完 run_pipeline 後,把每檔 stock 的 score / grade 寫入。
--   主要 consumer:rotation tags(過去 5 個交易日的族群均分 vs 今日均分)。
--   次要 consumer:未來 backtest / 個人分析 / Discord 通知歷史。
--
-- Schema 決策:
--   - PK (date, symbol) — 天然 unique
--   - 同 DB (kline.db) — 跟 standing_state 同庫,一個 connection 一次 commit
--   - 不記 tags/details — 那些每天 churn 大且不是 rotation 需要的資料
--   - 不寫 GC — 100 symbol × 365 day × 5 年 ≈ 9 MB,不值得
--
-- 安裝 / 升級:
--   sqlite3 kline.db < migrations/002_score_history.sql
--   或:src/persistence/score_history_io.init_schema(conn)
--
-- 安全:CREATE TABLE / CREATE INDEX 都用 IF NOT EXISTS,重跑無副作用。
-- ============================================================================

CREATE TABLE IF NOT EXISTS score_history (
    date         TEXT NOT NULL,    -- ISO date
    symbol       TEXT NOT NULL,    -- 例 'TPEX:6223'
    score        REAL NOT NULL,    -- 當日 total_score
    grade        TEXT,             -- 'S'/'A'/'B'/'C'/'D' 或 NULL
    last_updated TEXT NOT NULL,    -- ISO datetime

    PRIMARY KEY (date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_score_history_date   ON score_history(date);
CREATE INDEX IF NOT EXISTS idx_score_history_symbol ON score_history(symbol);
