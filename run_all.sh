#!/bin/bash
# run_all.sh — 台股動能儀表板一條龍（5B 自動化版）
# 用法：bash run_all.sh
#
# 步驟失敗邏輯：
#   資料取得層（tv_collect / daily_update / import_kline）任一失敗
#     → 後續資料處理步驟全部 skip（不用不完整資料 publish）
#   資料處理層（run_filters → prepare_charts → render → publish）
#     → 串行 skip：前一步失敗則後一步自動跳過

cd "$(dirname "$0")"

ETF_DB="$HOME/ETF追蹤/etf_operations.db"
KLINE_DB="kline.db"
TOOL="stock_dashboard"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# ── 從 step log 擷取單行摘要 note ─────────────────────────────────────────────
extract_note() {
    local log=$1
    grep -E "(成功\s*：|rows →|S級:|A級:|files →|\[OK\]|完成。|pushed|symbols|新增)" \
        "$log" 2>/dev/null \
        | grep -v "^$" | tail -2 | tr '\n' ' ' | sed 's/  */ /g' | cut -c1-100
}

# ── run_step <name> <cmd...> ─────────────────────────────────────────────────
# 執行指令，輸出同時寫 log；STEP_EC 設為 exit code
STEP_EC=0

run_step() {
    local name=$1; shift
    local log="${LOG_DIR}/step_${name}.log"
    local t0
    t0=$(date +%s)

    echo ""
    echo "[$(date '+%H:%M:%S')] ▶ ${name}"

    "$@" 2>&1 | tee "$log"
    STEP_EC=${PIPESTATUS[0]}

    local dur=$(( $(date +%s) - t0 ))
    local note
    note=$(extract_note "$log")

    if [[ $STEP_EC -eq 0 ]]; then
        python3 src/status_writer.py --tool "$TOOL" \
            --step "$name" --status ok --duration "$dur" --note "$note"
    else
        python3 src/status_writer.py --tool "$TOOL" \
            --step "$name" --status fail --duration "$dur" \
            --note "$note" --log-file "$log"
        echo "[$(date '+%H:%M:%S')] ❌ ${name} 失敗 (exit ${STEP_EC})"
    fi
}

# ── skip_step <name> [reason] ────────────────────────────────────────────────
skip_step() {
    local name=$1 reason=${2:-"前置步驟失敗，跳過"}
    python3 src/status_writer.py --tool "$TOOL" \
        --step "$name" --status skip --note "$reason"
    echo "[$(date '+%H:%M:%S')] ⏭️  ${name} 跳過（${reason}）"
}

# ── try_step <name> <cmd...> ─────────────────────────────────────────────────
# ABORT_AFTER 非空則直接 skip；執行失敗則設 ABORT_AFTER，後續步驟自動串行跳過
ABORT_AFTER=""

try_step() {
    local name=$1; shift
    if [[ -n "$ABORT_AFTER" ]]; then
        skip_step "$name" "${ABORT_AFTER} 失敗"
        return
    fi
    run_step "$name" "$@"
    if [[ $STEP_EC -ne 0 ]]; then
        ABORT_AFTER="$name"
    fi
}

# ────────────────────────────────────────────────────────────────────────────
python3 src/status_writer.py --init --tool "$TOOL"

# ── [0] 健康檢查 ─────────────────────────────────────────────────────────────
echo ""
echo "[0/7] 健康檢查..."

if ! curl -s -m 2 http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
    echo "❌ TradingView CDP 沒開（port 9222）。"
    python3 src/status_writer.py --tool "$TOOL" \
        --step tv_collect --status fail --duration 0 \
        --note "CDP port 9222 not responding"
    for s in daily_update import_kline run_filters prepare_charts render publish; do
        skip_step "$s" "CDP 不通"
    done
    python3 src/status_writer.py --finish --tool "$TOOL"
    python3 src/daily_supervisor.py
    exit 0
fi
if [[ ! -f "$KLINE_DB" ]]; then
    echo "❌ $KLINE_DB 不存在"
    python3 src/status_writer.py --finish --tool "$TOOL" --aborted
    python3 src/daily_supervisor.py
    exit 1
fi
if [[ ! -f "$ETF_DB" ]]; then
    echo "❌ $ETF_DB 不存在"
    python3 src/status_writer.py --finish --tool "$TOOL" --aborted
    python3 src/daily_supervisor.py
    exit 1
fi
echo "      ✅ 健康檢查通過（CDP OK、kline.db OK、etf_operations.db OK）"

# ── [1] tv_collect（K 線採集）────────────────────────────────────────────────
echo "[1/7] 採集 K 線資料..."
run_step tv_collect node scripts/tv_collect.mjs
TV_EC=$STEP_EC

# ── [2] daily_update（ETF，與 tv_collect 獨立，不受影響）────────────────────
echo "[2/7] ETF 日更新..."
run_step daily_update python3 "$HOME/ETF追蹤/daily_update.py"
DU_EC=$STEP_EC

# ── [3] import_kline（依賴 tv_collect 成功）──────────────────────────────────
echo "[3/7] 匯入 K 線資料..."
if [[ $TV_EC -ne 0 ]]; then
    skip_step import_kline "tv_collect 失敗，無可靠 K 線資料"
    IK_EC=1
else
    run_step import_kline python3 src/import_kline.py \
        --json /tmp/tv_daily_data.json --db "$KLINE_DB"
    IK_EC=$STEP_EC
fi

DATA_DATE=$(cat .data_date 2>/dev/null || echo "")
[[ -n "$DATA_DATE" ]] && echo "      data_date=${DATA_DATE}"

# ── 資料取得層屏障 ────────────────────────────────────────────────────────────
if [[ $TV_EC -ne 0 || $DU_EC -ne 0 || $IK_EC -ne 0 ]]; then
    echo ""
    echo "⚠️  資料取得層有失敗，跳過後續資料處理與發佈步驟。"
    ABORT_AFTER="資料取得層"
fi

# ── [4] run_filters ───────────────────────────────────────────────────────────
echo "[4/7] 跑五階段過濾..."
try_step run_filters python3 src/run_filters.py \
    --date "$DATA_DATE" --kline "$KLINE_DB" --etf "$ETF_DB"

# ── [5] prepare_charts ────────────────────────────────────────────────────────
echo "[5/7] 準備圖表資料..."
try_step prepare_charts python3 src/prepare_charts.py \
    --date "$DATA_DATE" --kline "$KLINE_DB"

# ── [6] render（含 watchlist + index）────────────────────────────────────────
echo "[6/7] 渲染儀表板..."
render_all() {
    python3 src/render.py && \
    python3 src/render_watchlist.py && \
    python3 src/generate_index.py
}
try_step render render_all

# ── [7] publish ───────────────────────────────────────────────────────────────
echo "[7/7] 推上線..."
try_step publish bash publish.sh

python3 src/status_writer.py --finish --tool "$TOOL"

echo ""
echo "完成。data_date=${DATA_DATE}  docs/dashboard.html 已產出。"
echo "預覽：python3 -m http.server 8080 → http://127.0.0.1:8080/docs/index.html"

python3 src/daily_supervisor.py
