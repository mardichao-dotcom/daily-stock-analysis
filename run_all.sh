#!/bin/bash
# run_all.sh — 台股動能儀表板一條龍
# 前置：node /tmp/tv_collect.mjs 已跑完，/tmp/tv_daily_data.json 存在
# 用法：bash run_all.sh
# 任一步失敗即中止（set -e）

set -e

cd "$(dirname "$0")"

ETF_DB="$HOME/ETF追蹤/etf_operations.db"
KLINE_DB="kline.db"

echo "[0/6] 健康檢查..."

if ! curl -s -m 2 http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
    echo "❌ TradingView CDP 沒開（port 9222）。請先開啟 TV 並進入 CDP 模式。"
    exit 1
fi

if [ ! -f "$KLINE_DB" ]; then
    echo "❌ $KLINE_DB 不存在。請先跑 node scripts/tv_collect.mjs"
    exit 1
fi

if [ ! -f "$ETF_DB" ]; then
    echo "❌ $ETF_DB 不存在。請先跑 python3 ~/ETF追蹤/daily_update.py"
    exit 1
fi

echo "      ✅ 健康檢查通過（CDP OK、kline.db OK、etf_operations.db OK）"

echo "[1/6] 匯入 K 線資料..."
python3 src/import_kline.py --json /tmp/tv_daily_data.json --db "$KLINE_DB"
DATA_DATE=$(cat .data_date)
echo "      data_date=${DATA_DATE}"

echo "[2/6] 跑五階段過濾..."
python3 src/run_filters.py --date "$DATA_DATE" --kline "$KLINE_DB" --etf "$ETF_DB"

echo "[3/6] 準備圖表資料..."
python3 src/prepare_charts.py --date "$DATA_DATE"

echo "[4/6] 渲染儀表板..."
python3 src/render.py

echo "[5/6] 渲染板塊名單..."
python3 src/render_watchlist.py

echo "[6/6] 更新歷史索引..."
python3 src/generate_index.py

echo ""
echo "完成。data_date=${DATA_DATE}  docs/dashboard.html 已產出。"
echo "預覽：python3 -m http.server 8080 → http://127.0.0.1:8080/docs/index.html"
