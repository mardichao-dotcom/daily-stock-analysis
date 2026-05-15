#!/bin/bash
# run_all.sh — 台股動能儀表板一條龍
# 用法：bash run_all.sh
# 任一步失敗即中止（set -e）

set -e

cd "$(dirname "$0")"

echo "[1/5] 跑五階段過濾..."
python3 src/run_filters.py

echo "[2/5] 準備圖表資料..."
python3 src/prepare_charts.py

echo "[3/5] 渲染儀表板..."
python3 src/render.py

echo "[4/5] 渲染板塊名單..."
python3 src/render_watchlist.py

echo "[5/5] 更新歷史索引..."
python3 src/generate_index.py

echo ""
echo "完成。docs/dashboard.html 已產出。"
echo "預覽：python3 -m http.server 8080 → http://127.0.0.1:8080/docs/index.html"
