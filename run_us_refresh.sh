#!/bin/bash
# run_us_refresh.sh — 美股凌晨補跑(P0-D / hotfix 2026-06-11)
#
# 目的:19:00 主跑時美股尚未收盤(資料到 T-1)。本 script 在台北 05:30(美股已收盤後)
#       只補美股:重抓 + 匯入 + 重產美股 chart JSON + publish。
#
# 重要設計:
#   - 只跑 NASDAQ/NYSE(從 watchlist 過濾)
#   - 不重算分數(run_filters_v2)、不重 render:分數/分級/頁面 HTML 維持前晚快照一致,
#     chart JSON 是前端 fetch 載入,覆寫即生效
#   - import_kline --no-data-date:不可用美股 max date 覆寫 .data_date(會回退主跑日期)
#   - prepare_charts_v2 --only-exchanges:只覆寫美股 chart JSON,不動 _index.json
#   - 失敗不影響 19:00 主跑(主跑本來就會抓到同樣資料)
#   - 美股休市日抓到 0 新 bar 屬正常,回報「0 新增」不算失敗

cd "$(dirname "$0")"
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

TOOL="us_refresh"
KLINE_DB="kline.db"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# data_date 沿用主跑(務必在 import 前讀,import --no-data-date 不會改它)
DATA_DATE=$(cat .data_date 2>/dev/null || echo "")
if [[ -z "$DATA_DATE" ]]; then
    echo "❌ .data_date 不存在,無法決定補跑目錄"; exit 1
fi
echo "[us-refresh] data_date=${DATA_DATE}"

STEP_EC=0
run_step() {
    local name=$1; shift
    local log="${LOG_DIR}/us_${name}.log"
    local t0; t0=$(date +%s)
    echo ""; echo "[$(date '+%H:%M:%S')] ▶ us-${name}"
    "$@" 2>&1 | tee "$log"
    STEP_EC=${PIPESTATUS[0]}
    local dur=$(( $(date +%s) - t0 ))
    if [[ $STEP_EC -eq 0 ]]; then
        python3 src/status_writer.py --tool "$TOOL" --step "$name" --status ok --duration "$dur"
    else
        python3 src/status_writer.py --tool "$TOOL" --step "$name" --status fail --duration "$dur" --log-file "$log"
        echo "[$(date '+%H:%M:%S')] ❌ us-${name} 失敗 (exit ${STEP_EC})"
    fi
}

notify() { python3 -m src.notify_discord --message "$1" || true; }
finish_fail() {
    python3 src/status_writer.py --finish --tool "$TOOL" --aborted
    notify "❌ us-refresh ${DATA_DATE} 失敗於 $1(美股圖維持前晚資料,19:00 主跑會補上)"
    exit 1
}

python3 src/status_writer.py --init --tool "$TOOL"

# ── [0] 健康檢查(沿用主跑:CDP port 9222,不通則嘗試拉起)──────────────────
echo "[us-refresh 0/4] 健康檢查..."
if ! curl -s -m 2 http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
    echo "      ⚠️  CDP 9222 不通,嘗試拉起 TradingView..."
    bash scripts/tv_cdp_launch.sh &
    sleep 15
    if ! curl -s -m 2 http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
        echo "❌ CDP 自動拉起失敗"
        python3 src/status_writer.py --tool "$TOOL" --step tv_collect --status fail --note "CDP 9222 不通"
        finish_fail "健康檢查(CDP)"
    fi
fi
echo "      ✅ CDP OK"

# ── [1] tv_collect:只美股 ─────────────────────────────────────────────────────
echo "[us-refresh 1/4] 採集美股 K 線(NASDAQ/NYSE)..."
run_step tv_collect node scripts/tv_collect.mjs --exchanges NASDAQ,NYSE
[[ $STEP_EC -ne 0 ]] && finish_fail "tv_collect"

# ── [2] import_kline:REPLACE 覆寫,不動 .data_date ────────────────────────────
echo "[us-refresh 2/4] 匯入美股 K 線(REPLACE,--no-data-date)..."
run_step import_kline python3 src/import_kline.py \
    --json /tmp/tv_daily_data.json --db "$KLINE_DB" --no-data-date
[[ $STEP_EC -ne 0 ]] && finish_fail "import_kline"

# ── [3] prepare_charts_v2:只重產美股 chart JSON(不寫 _index)─────────────────
echo "[us-refresh 3/4] 重產美股 chart JSON(--only-exchanges NASDAQ,NYSE)..."
run_step prepare_charts_v2 python3 src/prepare_charts_v2.py \
    --date "$DATA_DATE" --kline "$KLINE_DB" \
    --result filtered_result_v2.json --all-watchlist \
    --only-exchanges NASDAQ,NYSE
[[ $STEP_EC -ne 0 ]] && finish_fail "prepare_charts_v2"

# ── [4] publish:只 commit 美股 chart JSON,訊息標 us-refresh ──────────────────
echo "[us-refresh 4/4] publish(us-refresh)..."
US_COUNT=$(ls docs/data/v2/"$DATA_DATE"/NASDAQ_*.json docs/data/v2/"$DATA_DATE"/NYSE_*.json 2>/dev/null | wc -l | tr -d ' ')
publish_us() {
    git add -f docs/data/v2/"$DATA_DATE"/NASDAQ_*.json docs/data/v2/"$DATA_DATE"/NYSE_*.json 2>/dev/null || true
    if git diff --cached --quiet; then
        echo "      無 chart JSON 變更(可能美股休市,0 新增)— 跳過 commit"
    else
        git commit -m "us-refresh ${DATA_DATE} (${US_COUNT} 檔美股)"
    fi
    git push origin main
}
run_step publish publish_us
[[ $STEP_EC -ne 0 ]] && finish_fail "publish"

python3 src/status_writer.py --finish --tool "$TOOL"
notify "✅ us-refresh ${DATA_DATE} 完成:${US_COUNT} 檔美股圖已更新(分數/分級維持前晚快照)"
# 外部心跳(任務二):us_refresh 與主跑共用同一 check(主跑為權威),body 標記來源供 log 區分
python3 -m src.heartbeat --body "us-refresh ${DATA_DATE} ok (${US_COUNT} 檔)" || true
echo ""
echo "[us-refresh] 完成。${US_COUNT} 檔美股 chart JSON 已更新並 push。"
