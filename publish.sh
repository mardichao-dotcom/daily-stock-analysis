#!/bin/bash
# publish.sh — 部署到 GitHub Pages（手動觸發版）
# 執行前請先跑 run_all.sh 確保 docs/ 是最新版本
set -e

cd "$(dirname "$0")"

echo "[1/3] 確認 docs/ 是否有變更..."
if ! git diff --quiet docs/ || git ls-files --others --exclude-standard docs/ | grep -q .; then
  echo "      docs/ 有更新，準備 commit。"
else
  echo "      docs/ 無變更，跳過 commit，直接 push。"
fi

echo "[2/3] git add + commit..."
TODAY=$(date +%Y-%m-%d)
git add docs/ src/ config/ templates/ run_all.sh publish.sh README.md .gitignore
git commit -m "儀表板更新 ${TODAY}" || echo "      無新變更，跳過 commit。"

echo "[3/3] git push..."
git push origin main

echo ""
echo "完成。GitHub Pages 約 1-2 分鐘後更新。"
echo "網址：https://mardichao-dotcom.github.io/daily-stock-analysis/"
