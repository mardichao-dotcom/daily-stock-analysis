#!/bin/bash
# add_symbols_batch.sh — 批次新增 watchlist + key_prices 個股
#
# 朋友規則 v2.2 上線後,每週 30 檔擴 200 檔台股流程自動化。
# 本檔是純薄 wrapper,所有邏輯在 src/add_symbols_batch.py(testable)。
#
# 用法:
#   bash scripts/add_symbols_batch.sh new_symbols.json              # dry-run
#   bash scripts/add_symbols_batch.sh new_symbols.json --apply      # 真執行
#   bash scripts/add_symbols_batch.sh new_symbols.json --apply --no-confirm
#                                                                    # 不問
#
# 加完後選擇性後處理(費時):
#   --do-tv-collect   抓新 symbol 的 K 線歷史
#   --do-rebuild      重跑全歷史 standing_state
#   --do-rerender     重 render 最近 10 天 snapshot + live
#
# 範例(完整流程):
#   bash scripts/add_symbols_batch.sh new.json --apply \
#        --do-tv-collect --do-rebuild --do-rerender

set -e
cd "$(dirname "$0")/.."
exec python3 -m src.add_symbols_batch "$@"
