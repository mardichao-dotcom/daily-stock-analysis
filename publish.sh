#!/bin/bash
# publish.sh — 部署到 GitHub Pages（手動觸發版）
# 執行前請先跑 run_all.sh 確保 docs/ 是最新版本
set -e

cd "$(dirname "$0")"

# ── Stage 8 W3 上線:30 天前的歷史 snapshot 自動清理 ────────────────────
# 同時清理:
#   1. docs/data/v2/YYYY-MM-DD/  (chart JSON 目錄,每天 87 檔 ~3.7MB)
#   2. docs/index_v2_YYYY-MM-DD.html (對應的 snapshot HTML)
echo "[0/3] Archive 30 天前的歷史 snapshot..."
ARCHIVE_COUNT=0
# 1. chart data 目錄
for d in $(find docs/data/v2/ -maxdepth 1 -type d -mtime +30 \
            -name '2[0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' 2>/dev/null); do
    echo "      [archive] rm -rf $d"
    rm -rf "$d"
    ARCHIVE_COUNT=$((ARCHIVE_COUNT + 1))
done
# 2. snapshot HTML
for f in $(find docs/ -maxdepth 1 -type f -mtime +30 \
            -name 'index_v2_2[0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html' 2>/dev/null); do
    echo "      [archive] rm $f"
    rm "$f"
    ARCHIVE_COUNT=$((ARCHIVE_COUNT + 1))
done
if [ "$ARCHIVE_COUNT" -eq 0 ]; then
    echo "      無 30 天前歷史,無需清理"
else
    echo "      共清理 $ARCHIVE_COUNT 個檔案/目錄"
fi
echo ""

echo "[1/3] 確認 docs/ 是否有變更..."
if ! git diff --quiet docs/ || git ls-files --others --exclude-standard docs/ | grep -q .; then
  echo "      docs/ 有更新，準備 commit。"
else
  echo "      docs/ 無變更，跳過 commit，直接 push。"
fi

echo "[2/3] git add + commit..."
TODAY=$(date +%Y-%m-%d)
git add docs/ src/ config/ templates/ run_all.sh publish.sh README.md .gitignore
# V2 chart JSONs (docs/data/v2/{date}/*.json) 受 .gitignore 規則保護,
# 需用 -f 強制加進來;Pages 才有 K 線 JSON 可服務(2026-06-02 加)
git add -f docs/data/v2/ 2>/dev/null || true
git commit -m "儀表板更新 ${TODAY}" || echo "      無新變更，跳過 commit。"

echo "[3/3] git push..."
git push origin main

echo ""
echo "完成。GitHub Pages 約 1-2 分鐘後更新。"
echo "網址：https://mardichao-dotcom.github.io/daily-stock-analysis/"
