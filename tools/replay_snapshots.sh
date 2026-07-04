#!/bin/bash
# replay_snapshots.sh — 回補停更期間每日 snapshot(任務一,2026-07-04)
#
# 停更 19 天(6/15~7/3)期間 daily snapshot 缺失/被幽靈污染。用已回補的真實 kline
# 逐交易日 as-of 重算,產出各日 snapshot 頁 + _summary + chart JSON。
#
# ★ 狀態機隔離(關鍵):run_filters_v2 計分依賴持久化 standing_state(前進式)。
#   live kline.db 的 standing_state 已被 7/3 回補跑推進到 7/3,直接 replay 過去日期會
#   讀到未來 trigger_date 而崩潰。故:
#     1. 複製 live kline.db → /tmp 副本(含已回補真實 OHLCV)
#     2. 用 pre-backfill 備份的 standing_state 覆蓋副本(還原 as-of 6/12 狀態種子;
#        排除唯一被幽靈污染的 TWSE:2317 >6/12 row → 該檔自 6/15 重新起算)
#     3. 對副本逐日 ascending replay(狀態逐日正確前進)
#   全程只寫 /tmp 副本 + docs/ snapshot;live kline.db / index_v2.html / data_date 不動。
#
# 前置(呼叫端已驗):計分 config 6/14~7/4 無變更;kline.db 已回補真實資料。

cd "$(dirname "$0")/.." || exit 1
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

LIVE_DB="kline.db"
BACKUP_DB="kline.db.bak-pre-backfill-2026-07-04"
REPLAY_DB="/tmp/kline_replay.db"
ETF_DB="$HOME/ETF追蹤/etf_operations.db"
SEED_CUTOFF="2026-06-12"        # standing_state 種子上界(停更前最後正常日)
NOTE="本頁為 2026-07-04 事後重算(停更期間回補,非當日即時產出)"

[ -f "$BACKUP_DB" ] || { echo "❌ 缺 pre-backfill 備份 $BACKUP_DB,無法還原 6/12 狀態種子"; exit 1; }

echo "=== 1) 建隔離副本 + 還原 as-of ${SEED_CUTOFF} standing_state 種子 ==="
cp "$LIVE_DB" "$REPLAY_DB"
sqlite3 "$REPLAY_DB" <<SQL
DELETE FROM standing_state;
ATTACH DATABASE '$BACKUP_DB' AS bak;
INSERT INTO standing_state
  SELECT * FROM bak.standing_state
  WHERE (trigger_date  IS NULL OR trigger_date  <= '$SEED_CUTOFF')
    AND (standing_date IS NULL OR standing_date <= '$SEED_CUTOFF');
DETACH DATABASE bak;
SQL
echo "副本 standing_state 種子行數:$(sqlite3 "$REPLAY_DB" 'SELECT COUNT(*) FROM standing_state;')"
echo "副本內 >${SEED_CUTOFF} 污染行(應為 0):$(sqlite3 "$REPLAY_DB" "SELECT COUNT(*) FROM standing_state WHERE trigger_date > '$SEED_CUTOFF' OR standing_date > '$SEED_CUTOFF';")"

DATES=$(sqlite3 "$REPLAY_DB" \
  "SELECT DISTINCT date FROM kline WHERE symbol LIKE 'TWSE:%' \
   AND date BETWEEN '2026-06-15' AND '2026-07-02' ORDER BY date;")
echo ""
echo "=== 2) 逐日 replay:$(echo $DATES | wc -w | tr -d ' ') 天(6/19 端午已排除)==="
for D in $DATES; do
    echo "── replay $D ──"
    RESULT="/tmp/replay_result_${D}.json"
    python3 src/run_filters_v2.py --date "$D" --kline "$REPLAY_DB" --etf "$ETF_DB" \
        --output "$RESULT" || { echo "❌ run_filters_v2 $D"; exit 1; }
    python3 src/prepare_charts_v2.py --date "$D" --kline "$REPLAY_DB" \
        --result "$RESULT" --all-watchlist >/dev/null || { echo "❌ prepare_charts $D"; exit 1; }
    python3 -m src.site_meta --date "$D" --result "$RESULT" >/dev/null || { echo "❌ site_meta $D"; exit 1; }
    python3 src/render_v2.py --date "$D" --result "$RESULT" \
        --output "docs/index_v2_${D}.html" --recompute-note "$NOTE" \
        || { echo "❌ render $D"; exit 1; }
done

echo ""
echo "=== 3) 重生 history.html(重算日顯示標記);不碰其他 live 頁 ==="
python3 src/render_history.py

echo ""
echo "=== 4) 清副本 ==="
rm -f "$REPLAY_DB"
echo "✅ 回補完成。live kline.db / index_v2.html / data_date(7/3)未動。"
