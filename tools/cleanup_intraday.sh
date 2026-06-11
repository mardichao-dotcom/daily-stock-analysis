#!/bin/bash
# cleanup_intraday.sh — P0-C 一次性清洗:對所有非台股 symbol 強制重抓最近 5 根並 REPLACE 覆寫
#
# 用途:把過去一週(或更早)在「收盤前抓取」存入的半成品 bar,用收盤後的正確值覆蓋。
# 前提:必須在各市場都已收盤的時段執行(建議搭今晚主跑後或明早 05:30 補跑後),
#       否則重抓到的又是當下盤中半成品。
#
# 步驟:
#   1. 報告清洗前的半成品嫌疑(report_suspicious_bars.py)
#   2. tv_collect 只跑指定交易所,--refresh-bars 5(重抓最近 5 根)
#   3. import_kline --no-data-date(REPLACE 覆寫,不動主跑 .data_date)
#   4. 報告清洗後狀態
#
# 用法:
#   bash tools/cleanup_intraday.sh                 # 預設只洗「已收盤」市場(OMXCOP,TSE,KRX)
#   bash tools/cleanup_intraday.sh NASDAQ,NYSE     # 指定交易所
#
# ⚠️ 重要:絕不可對「場次進行中」的市場跑清洗,否則會抓到盤中半成品=自製 P0-C 案例。
#   美股(NASDAQ/NYSE)台北盤中時段 21:30~04:00 → 預設排除;美股清洗交給 05:30 補跑
#   (那時美股 04:00 已收盤,refresh 最近 3 根 + REPLACE 自然覆寫)。
#
# 注意:本 script 需要 TradingView Desktop CDP(port 9222)在線。

cd "$(dirname "$0")/.."
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

# 預設只洗「平時收盤早於本清洗時段」的市場;美股刻意排除(見上方警告)
EXCHANGES="${1:-OMXCOP,TSE,KRX}"

echo "═══ P0-C 一次性半成品清洗(交易所:${EXCHANGES})═══"
echo ""
echo "[1/4] 清洗前嫌疑 bar:"
python3 tools/report_suspicious_bars.py

if ! curl -s -m 2 http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
    echo "❌ CDP 9222 不通,先確認 TradingView Desktop 在線(scripts/tv_cdp_launch.sh)"
    exit 1
fi

echo ""
echo "[2/4] 重抓最近 5 根(${EXCHANGES})..."
node scripts/tv_collect.mjs --exchanges "$EXCHANGES" --refresh-bars 5
TV_EC=$?
if [[ $TV_EC -ne 0 ]]; then
    echo "❌ tv_collect 失敗 (exit ${TV_EC})"; exit 1
fi

echo ""
echo "[3/4] 匯入(REPLACE 覆寫,--no-data-date)..."
python3 src/import_kline.py --json /tmp/tv_daily_data.json --db kline.db --no-data-date

echo ""
echo "[4/4] 清洗後狀態:"
python3 tools/report_suspicious_bars.py
echo ""
echo "✅ 完成。下一步:抽查 MAERSK / NVDA 最近 3 根與 TradingView 對照(由你在驗收端確認)。"
