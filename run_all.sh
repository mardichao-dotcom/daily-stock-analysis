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

# launchd 預設 C locale；強制 UTF-8，確保 grep/cut/tr 正確處理多位元組字元
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

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
echo "[0/10] 健康檢查..."

CDP_RECOVERED=0
if ! curl -s -m 2 http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
    echo "      ⚠️  CDP port 9222 不通，嘗試自動拉起 TradingView..."
    bash scripts/tv_cdp_launch.sh &
    sleep 15
    if curl -s -m 2 http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
        echo "      ⚠️  CDP 曾自動拉起（TradingView 原本沒開或剛剛被關掉）"
        CDP_RECOVERED=1
    else
        echo "❌ TradingView CDP 自動拉起失敗（port 9222 仍不通）"
        echo "   可能原因：TradingView 自動更新中、帳號 session 失效、binary 路徑變動"
        python3 src/status_writer.py --tool "$TOOL" \
            --step tv_collect --status fail --duration 0 \
            --note "CDP port 9222 not responding (auto-recovery failed)"
        for s in daily_update import_kline run_filters prepare_charts render \
                  run_filters_v2 prepare_charts_v2 render_v2 publish; do
            skip_step "$s" "CDP 不通"
        done
        python3 src/status_writer.py --finish --tool "$TOOL"
        python3 src/daily_supervisor.py
        exit 0
    fi
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
echo "[1/10] 採集 K 線資料..."
run_step tv_collect node scripts/tv_collect.mjs
TV_EC=$STEP_EC

# ── [2] daily_update（ETF，與 tv_collect 獨立，不受影響）────────────────────
echo "[2/10] ETF 日更新..."
run_step daily_update python3 "$HOME/ETF追蹤/daily_update.py"
DU_EC=$STEP_EC

# ── [3] import_kline（依賴 tv_collect 成功）──────────────────────────────────
echo "[3/10] 匯入 K 線資料..."
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

# ── [4] run_filters (V1) ──────────────────────────────────────────────────────
echo "[4/10] 跑五階段過濾(V1)..."
try_step run_filters python3 src/run_filters.py \
    --date "$DATA_DATE" --kline "$KLINE_DB" --etf "$ETF_DB"

# ── [5] prepare_charts (V1) ──────────────────────────────────────────────────
echo "[5/10] 準備圖表資料(V1)..."
try_step prepare_charts python3 src/prepare_charts.py \
    --date "$DATA_DATE" --kline "$KLINE_DB"

# ── [6] render (V1,含 watchlist + index) ────────────────────────────────────
echo "[6/10] 渲染儀表板(V1)..."
render_all() {
    python3 src/render.py && \
    python3 src/render_watchlist.py && \
    python3 src/generate_index.py
}
try_step render render_all

# ── [7] run_filters_v2 (V2 純加分制 + v2.2 規則) ─────────────────────────────
# 2026-06-02 補:V1 → V2 串行,V1 失敗自動跳過 V2(try_step 機制)
# V2 失敗也會自動跳過 publish — daily_supervisor 抓到後 Discord 告警
echo "[7/10] 跑 V2 計分(rule v2.2)..."
try_step run_filters_v2 python3 src/run_filters_v2.py \
    --date "$DATA_DATE" --kline "$KLINE_DB" --etf "$ETF_DB" \
    --output filtered_result_v2.json

# ── [8] prepare_charts_v2 (V2 全 watchlist chart JSON + _index.json status) ──
echo "[8/10] 準備 V2 圖表資料(全 watchlist + waiting_us_close 標記)..."
try_step prepare_charts_v2 python3 src/prepare_charts_v2.py \
    --date "$DATA_DATE" --kline "$KLINE_DB" \
    --result filtered_result_v2.json --all-watchlist

# ── [9] render_v2 (live + 日期 snapshot + watchlist + history + landing) ────
echo "[9/10] 渲染 V2 儀表板(7 區塊 + 入口頁 + 歷史索引)..."
render_v2_all() {
    # 9-1 live dashboard
    python3 src/render_v2.py --date "$DATA_DATE" \
        --result filtered_result_v2.json --output docs/index_v2.html && \
    # 9-2 日期化 snapshot(供 history 頁面引用 + 30 天後 publish.sh archive)
    python3 src/render_v2.py --date "$DATA_DATE" \
        --result filtered_result_v2.json \
        --output "docs/index_v2_${DATA_DATE}.html" && \
    # 9-3 全 watchlist 折疊 K 線頁
    python3 src/render_watchlist_v2.py --date "$DATA_DATE" \
        --result filtered_result_v2.json && \
    # 9-4 歷史索引(掃 docs/index_v2_*.html)
    python3 src/render_history.py && \
    # 9-5 入口頁(landing)
    python3 src/render_landing.py --result filtered_result_v2.json
}
try_step render_v2 render_v2_all

# ── [10] publish ──────────────────────────────────────────────────────────────
echo "[10/10] 推上線..."
try_step publish bash publish.sh

python3 src/status_writer.py --finish --tool "$TOOL"

echo ""
echo "完成。data_date=${DATA_DATE}  docs/dashboard.html 已產出。"
echo "預覽：python3 -m http.server 8080 → http://127.0.0.1:8080/docs/index.html"

python3 src/daily_supervisor.py
