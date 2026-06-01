-- ============================================================================
-- 001_standing_state.sql
-- ----------------------------------------------------------------------------
-- W1.5 standing_state 表 — 站穩狀態機的持久化
-- 建立日期: 2026-05-26
-- 對應程式: src/triggers/standing.py
-- ============================================================================
--
-- Schema 設計決策 (參照 W1.5 review):
--   - PRIMARY KEY 用 composite (symbol, category, price_str) — 不用 hash
--     避免 hash 函式版本/平台/語言差異造成的 ID 漂移
--   - price_str 是 TEXT,從 key_prices.json 原始字串保留
--     例: 線 = "5380"; 區域 = "2130-2180"
--   - state 5 種列舉值對應 standing.py 的 UNTRIGGERED / TRIGGERED /
--     STANDING / MAINTAINING / CANCELLED 字串常數
--
-- 安裝 / 升級流程:
--   sqlite3 kline.db < migrations/001_standing_state.sql
--
-- 安全:CREATE TABLE / CREATE INDEX 都用 IF NOT EXISTS,重跑無副作用。
-- ============================================================================

CREATE TABLE IF NOT EXISTS standing_state (
    symbol         TEXT NOT NULL,
    category       TEXT NOT NULL,   -- key_price / inner_support / order_block / ma_60 ...
    price_str      TEXT NOT NULL,   -- 線: 原始 price 字串;區域: f"{low}-{high}"
    state          TEXT NOT NULL,   -- UNTRIGGERED / TRIGGERED / STANDING / MAINTAINING / CANCELLED
    trigger_date   TEXT,            -- ISO date;UNTRIGGERED / CANCELLED 時為 NULL
    standing_date  TEXT,            -- ISO date;首次 STANDING 達成日
    last_updated   TEXT NOT NULL,   -- ISO datetime;每次 evaluate 寫入時更新

    PRIMARY KEY (symbol, category, price_str)
);

CREATE INDEX IF NOT EXISTS idx_standing_state_symbol
    ON standing_state(symbol);

CREATE INDEX IF NOT EXISTS idx_standing_state_state
    ON standing_state(state);
