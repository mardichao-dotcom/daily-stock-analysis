#!/bin/bash
# tv_restart.sh — 定時重啟 TradingView Desktop(待辦 1b / hotfix 2026-06-12 hyp3)
#
# 背景:長時間運行的 TV Desktop chart 引擎會劣化。6/12 事故:TV uptime 5.7 天 →
#       CDP preflight 仍通(7ms),但 setSymbol / bars() 全卡 → 主跑 tv_collect 整批逾時。
# 對策:每平日 18:45(19:00 主跑前)重啟,讓主跑拿到新鮮的 chart 引擎,uptime 不過夜累積。
#
# 退出碼:0 = 重啟後 CDP 就緒;1 = CDP 未在時限內就緒。

TV_BINARY="/Applications/TradingView.app/Contents/MacOS/TradingView"
TS() { date '+%Y-%m-%d %H:%M:%S'; }

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
for i in $(seq 1 18); do
    sleep 5
    if curl -s -m 3 http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
        echo "[tv-restart $(TS)] ✅ CDP 就緒(約 $((i*5))s)"
        exit 0
    fi
done
echo "[tv-restart $(TS)] ⚠️ CDP 90s 內未就緒"
exit 1
