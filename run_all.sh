#!/bin/bash
# run_all.sh — 台股動能儀表板一條龍（測試版，5/13 資料）
# 用法：bash run_all.sh
# 任一步失敗即中止（set -e）

set -e

cd "$(dirname "$0")"

echo "[1/4] 跑五階段過濾..."
python3 src/run_filters.py

echo "[2/4] 準備圖表資料..."
python3 src/prepare_charts.py

echo "[3/4] 渲染 HTML..."
python3 src/render.py

echo "[4/4] 更新歷史索引..."
python3 src/generate_index.py

echo ""
echo "完成。output/index.html 已產出。"
echo "預覽：python3 -m http.server 8766 → http://127.0.0.1:8766/output/index.html"
