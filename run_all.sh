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
        for s in daily_update import_kline \
                  run_filters_v2 fetch_chips prepare_charts_v2 site_meta render_v2 publish; do
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
# ── API-ready preflight(7/6 事故修補)──────────────────────────────────────────
# 不只驗「chart 頁存在」,還驗 TradingViewApi 在 N 秒內可用。7/6 事故:chart 頁在
# 但 API 永不 ready,tv_collect 卡在單檔迴圈之前燒滿 30 分。此處提前 fail-fast(≤120s)。
echo "      驗 TradingViewApi ready(preflight)..."
if ! node scripts/tv_collect.mjs --preflight-only 2>&1; then
    echo "❌ TradingViewApi preflight 失敗（chart 頁在但 API 未 ready / 初始化卡死）"
    python3 src/status_writer.py --tool "$TOOL" \
        --step tv_collect --status fail --duration 0 \
        --note "TradingViewApi preflight failed (chart present but API not ready)"
    for s in daily_update import_kline \
              run_filters_v2 fetch_chips prepare_charts_v2 site_meta render_v2 publish; do
        skip_step "$s" "API preflight 失敗"
    done
    python3 src/status_writer.py --finish --tool "$TOOL"
    python3 src/daily_supervisor.py
    exit 0
fi
echo "      ✅ 健康檢查通過（CDP OK、TradingViewApi ready、kline.db OK、etf_operations.db OK）"

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

# ── 資料取得層屏障(2026-06-04 修正)──────────────────────────────────────
# 原本 daily_update 失敗也擋下游 — 但 daily_update 經常在「資料寫入後」的
# 周邊步驟失敗(例:Pine script 產生時 KeyError、etfedge 偶爾抓不到),
# 此時 etf_operations.db 仍有資料,V2 計分照常用得到。
# 改為:只 tv_collect / import_kline(K 線資料路徑)失敗才擋下游;
# daily_update 失敗只影響當日 ETF 共識的「新鮮度」,V2 仍會跑(用最新可
# 取得的 ETF 資料),由 daily_supervisor 的 freshness watchdog 後續告警。
if [[ $TV_EC -ne 0 || $IK_EC -ne 0 ]]; then
    echo ""
    echo "⚠️  K 線資料層失敗(tv_collect/import_kline)— 跳過後續資料處理與發佈步驟。"
    ABORT_AFTER="K 線資料層"
fi
if [[ $DU_EC -ne 0 ]]; then
    echo ""
    echo "⚠️  ETF daily_update 失敗 — 但 etf_operations.db 可能仍有新資料"
    echo "    繼續跑 V2 計分(會用 DB 內最新可得 ETF 共識資料)。"
    echo "    daily_supervisor freshness watchdog 會獨立告警 ETF 新鮮度。"
fi

# ── [4~6] V1 舊管線已停產(hotfix 2026-06-11 §6.7)─────────────────────────────
# 用戶裁決:線上唯一指向 v1 archives/ 的連結在孤兒頁 dashboard.html(無入口可達),
# 故從 run_all.sh 移除 v1 run_filters / prepare_charts / render 三步。
# docs/ 下既有 v1 檔案(dashboard.html / watchlist.html / archives/)保留不刪,
# dashboard.html 頂部已加 deprecated 註記導向 index.html。
# V2 管線(run_filters_v2 起)不依賴 v1 任何輸出,移除後行為不變。

# ── [7] run_filters_v2 (V2 純加分制 + v2.2 規則) ─────────────────────────────
# 2026-06-02 補:V1 → V2 串行,V1 失敗自動跳過 V2(try_step 機制)
# V2 失敗也會自動跳過 publish — daily_supervisor 抓到後 Discord 告警
echo "[7/10] 跑 V2 計分(rule v2.2)..."
try_step run_filters_v2 python3 src/run_filters_v2.py \
    --date "$DATA_DATE" --kline "$KLINE_DB" --etf "$ETF_DB" \
    --output filtered_result_v2.json

# ── [7.5] fetch_chips (stage9 §3.5 籌碼:三大法人+融資券+千張大戶 → kline.db chips 表)─
# 非阻斷:籌碼是個股卡純顯示功能,任一來源失敗標 N/A 不冒充,不擋主儀表板發布。
# 須在 prepare_charts_v2 之前(圖表 JSON 會嵌入 chips)。TDCC 每週五自動抓。
echo "[7.5/10] 抓籌碼面(三大法人/融資/千張大戶,全市場過濾 watchlist)..."
run_step fetch_chips python3 -m src.fetch_chips --date "$DATA_DATE"

# ── [8] prepare_charts_v2 (V2 全 watchlist chart JSON + _index.json status) ──
echo "[8/10] 準備 V2 圖表資料(全 watchlist + waiting_us_close 標記)..."
try_step prepare_charts_v2 python3 src/prepare_charts_v2.py \
    --date "$DATA_DATE" --kline "$KLINE_DB" \
    --result filtered_result_v2.json --all-watchlist

# ── [8.5] site_meta (P1 §6.3 渲染單一資料源:版本/檔數/略過/更新日)──────────
echo "[8.5/10] 產 site_meta.json(渲染單一資料源)..."
try_step site_meta python3 -m src.site_meta --date "$DATA_DATE"

# ── [8.6] fetch_events (stage9 §3.1 事件中樞:FRED 總經 + FOMC + Playwright 法說會)─
# 非阻斷:events 是 client-fetch 側功能,第四道護欄保證 events.json 一定產出
# (抓取失敗保留前一日 + stale),故用 run_step 不設 ABORT_AFTER,失敗不擋主儀表板發布。
echo "[8.6/10] 抓事件中樞(events.json:總經日曆 + 法說會)..."
run_step fetch_events python3 -m src.fetch_events

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
    # 9-4 theme_returns(L2+L3+L4 標籤每日等權平均漲幅,N>=3 上榜)
    python3 -m src.theme_returns --date "$DATA_DATE" && \
    # 9-5 主題熱度詳情頁 tags.html
    python3 -m src.render_themes_v2 --date "$DATA_DATE" && \
    # 9-6 歷史索引(掃 docs/index_v2_*.html)
    python3 src/render_history.py && \
    # 9-7 入口頁(landing)
    python3 src/render_landing.py --result filtered_result_v2.json
}
try_step render_v2 render_v2_all

# ── [10] publish ──────────────────────────────────────────────────────────────
echo "[10/10] 推上線..."
try_step publish bash publish.sh

# ── 外部心跳(任務二):verify_publish 全綠(publish 未被 abort)才 ping ──────────
# healthchecks 在逾時未收到 ping 時主動告警,補「整台當掉/排程沒跑」的死角。
# 失敗只記 log 不擋流程(heartbeat 模組永遠 exit 0)。
if [[ -z "$ABORT_AFTER" ]]; then
    echo "[心跳] 主跑全綠 → ping healthchecks..."
    python3 -m src.heartbeat --body "main-run ${DATA_DATE} ok" || true
else
    echo "[心跳] 主跑未全綠(${ABORT_AFTER} 失敗)→ 不 ping(讓 healthchecks 逾時告警)"
fi

python3 src/status_writer.py --finish --tool "$TOOL"

echo ""
echo "完成。data_date=${DATA_DATE}  docs/dashboard.html 已產出。"
echo "預覽：python3 -m http.server 8080 → http://127.0.0.1:8080/docs/index.html"

python3 src/daily_supervisor.py
