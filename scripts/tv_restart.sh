#!/bin/bash
# tv_restart.sh — 定時重啟 TradingView Desktop(待辦 1b / hotfix 2026-06-12 hyp3)
#
# 背景:長時間運行的 TV Desktop chart 引擎會劣化。6/12 事故:TV uptime 5.7 天 →
#       CDP preflight 仍通(7ms),但 setSymbol / bars() 全卡 → 主跑 tv_collect 整批逾時。
# 對策:每平日 18:45(19:00 主跑前)重啟,讓主跑拿到新鮮的 chart 引擎,uptime 不過夜累積。
#
# 2026-07-04 強化(19 天停更事故後):
#   健康檢查不再只驗「9222 有回應」——TV 更新後可能重啟到 new-tab 而非 chart 頁,
#   此時 CDP 通但 tv_collect 找不到 chart target 會整批 fail。改為驗證「存在 chart 頁
#   target」;找不到時嘗試 CDP 導航到已知 chart URL 自動復原,仍失敗則發 Discord
#   告警「需人工介入」並非 0 退出。
#
# TV binary 不吃 URL 參數(Electron app,非可導航 CLI),故自動復原走 CDP /json/new。
#
# 退出碼:0 = chart target 就緒;1 = CDP 未就緒;2 = CDP 通但 chart 未載入(需人工)。

TV_BINARY="/Applications/TradingView.app/Contents/MacOS/TradingView"
# 已知 chart 版面 URL(復原導航目標);可用環境變數覆寫
CHART_URL="${TV_CHART_URL:-https://tw.tradingview.com/chart/xdySlor8/}"
CDP="http://127.0.0.1:9222"
TS() { date '+%Y-%m-%d %H:%M:%S'; }

# 是否存在 tradingview.com/chart 的 page target(跟 tv_collect connectCDP 同條件)
has_chart_target() {
    curl -s -m 3 "$CDP/json/list" 2>/dev/null \
      | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print(any(t.get('type')=='page' and 'tradingview.com/chart' in (t.get('url') or '') for t in d))
except Exception:
    print('False')" 2>/dev/null | grep -q True
}

alert_manual() {
    local msg="$1"
    echo "[tv-restart $(TS)] ❌ $msg"
    python3 -m src.notify_discord --message "🚨 [TV 重啟] $msg" 2>/dev/null || \
        echo "[tv-restart $(TS)] ⚠️ 連 Discord 告警都發不出去" >&2
}

cd "$(dirname "$0")/.." || exit 1
echo "[tv-restart $(TS)] 重啟 TradingView Desktop..."

# 1) 優雅關閉
osascript -e 'quit app "TradingView"' 2>/dev/null
for i in $(seq 1 20); do
    pgrep -x TradingView >/dev/null 2>&1 || break
    sleep 1
done
# 2) 還沒死 → 強制
if pgrep -x TradingView >/dev/null 2>&1; then
    echo "[tv-restart $(TS)] 仍存活,SIGTERM"
    pkill -x TradingView 2>/dev/null || true
    sleep 3
    pgrep -x TradingView >/dev/null 2>&1 && { pkill -9 -x TradingView 2>/dev/null || true; sleep 2; }
fi

# 3) 帶 CDP flag 重啟(背景)
echo "[tv-restart $(TS)] 以 --remote-debugging-port=9222 重啟"
"$TV_BINARY" --remote-debugging-port=9222 >/dev/null 2>&1 &

# 4) 等 CDP 就緒(最多 ~90s)
CDP_UP=0
for i in $(seq 1 18); do
    sleep 5
    if curl -s -m 3 "$CDP/json/version" >/dev/null 2>&1; then
        echo "[tv-restart $(TS)] CDP 9222 就緒(約 $((i*5))s)"
        CDP_UP=1
        break
    fi
done
if [ "$CDP_UP" -eq 0 ]; then
    alert_manual "TV 重啟後 CDP 9222 90s 內未就緒,需人工介入"
    exit 1
fi

# 5) ★ 驗證存在 chart 頁 target(非只驗 9222)——TV 可能停在 new-tab
for i in $(seq 1 12); do
    if has_chart_target; then
        echo "[tv-restart $(TS)] ✅ chart target 就緒(約 $((i*5))s)"
        exit 0
    fi
    sleep 5
done

# 6) 沒有 chart target → 嘗試 CDP 導航自動復原(/json/new PUT,已驗端點存在)
echo "[tv-restart $(TS)] 無 chart target,嘗試 CDP 導航到 $CHART_URL ..."
curl -s -m 5 -X PUT "$CDP/json/new?${CHART_URL}" >/dev/null 2>&1
for i in $(seq 1 12); do
    sleep 5
    if has_chart_target; then
        echo "[tv-restart $(TS)] ✅ CDP 導航復原成功(chart target 就緒)"
        exit 0
    fi
done

# 7) 仍無 chart target → 人工介入
alert_manual "TV 重啟後圖表未載入(CDP up 但無 chart target,自動導航亦失敗),需人工開啟 chart 頁"
exit 2
