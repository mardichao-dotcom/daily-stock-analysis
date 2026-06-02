#!/bin/bash
# tv_cdp_launch.sh — Called by LaunchAgent (com.user.tradingview-cdp)
# Ensures TradingView is running with --remote-debugging-port=9222
#
# 2026-06-02 改造為 launchd KeepAlive 模式:
#   - 既有的「沒 flag 就 exit 1」會讓 KeepAlive 無限重啟
#   - 改成「先嘗試 quit 沒 flag 的 TV → 等死 → 用 flag 重起」
#   - exec 最後接管,launchd 監控的 process 是 TradingView 本身,死掉自動重啟

TV_BINARY="/Applications/TradingView.app/Contents/MacOS/TradingView"
LOG_PREFIX="[tv-cdp] $(date '+%Y-%m-%d %H:%M:%S')"

# Case 1: Port 9222 已 responsive — CDP up,什麼都不用做
if curl -s -m 3 http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
    echo "$LOG_PREFIX Port 9222 already active. Sleeping (waiting for TV process to be monitored by launchd)."
    # KeepAlive 需要這個 script 持續存在(KeepAlive 監控的是 script process,
    # 不是 TV)。如果 launchd 已經有別的 instance 在 exec TradingView,
    # 我們就 sleep 直到被 launchd 顯式停止,避免 KeepAlive 重啟風暴。
    # 實際上 launchd 只跑一個 instance,正常路徑不會走到這裡。
    # 為保險:sleep 並 watch CDP,失效就 exit 讓 launchd 重起。
    while curl -s -m 3 http://127.0.0.1:9222/json/version > /dev/null 2>&1; do
        sleep 30
    done
    echo "$LOG_PREFIX Port 9222 dropped. Exiting so launchd can re-launch."
    exit 0
fi

# Case 2: TV 在跑但沒帶 flag(port 9222 不通)— quit 它,等死,重起
if pgrep -x "TradingView" > /dev/null 2>&1; then
    echo "$LOG_PREFIX TradingView running without CDP flag. Sending graceful quit."
    osascript -e 'quit app "TradingView"' 2>/dev/null
    # 等待最多 15 秒讓 TV 真的死掉
    for i in $(seq 1 15); do
        if ! pgrep -x "TradingView" > /dev/null 2>&1; then
            echo "$LOG_PREFIX TradingView exited after ${i}s."
            break
        fi
        sleep 1
    done
    # 還沒死就 SIGTERM
    if pgrep -x "TradingView" > /dev/null 2>&1; then
        echo "$LOG_PREFIX TradingView still alive, sending SIGTERM."
        pkill -x TradingView || true
        sleep 3
    fi
fi

# Case 3: Port 空 + TradingView 沒在跑 → 用 CDP flag 啟動
echo "$LOG_PREFIX Launching TradingView with --remote-debugging-port=9222"
exec "$TV_BINARY" --remote-debugging-port=9222
