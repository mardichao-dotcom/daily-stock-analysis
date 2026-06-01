#!/bin/bash
# tv_cdp_launch.sh — Called by LaunchAgent at login
# Ensures TradingView is running with --remote-debugging-port=9222

TV_BINARY="/Applications/TradingView.app/Contents/MacOS/TradingView"

# Port already responding — TradingView CDP is already up, nothing to do
if curl -s -m 3 http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
    echo "[tv-cdp] $(date '+%Y-%m-%d %H:%M:%S') Port 9222 already active, skipping launch."
    exit 0
fi

# TradingView is running but CDP is not on port 9222
# (e.g., user launched from Dock without the flag — can't add CDP to a live process)
if pgrep -x "TradingView" > /dev/null 2>&1; then
    echo "[tv-cdp] $(date '+%Y-%m-%d %H:%M:%S') WARNING: TradingView is running but port 9222 is not responding."
    echo "[tv-cdp] Cannot inject CDP into a running instance."
    echo "[tv-cdp] Fix: quit TradingView, then re-login or manually run:"
    echo "[tv-cdp]   $TV_BINARY --remote-debugging-port=9222"
    exit 1
fi

# Port is free and TradingView is not running — launch with CDP
echo "[tv-cdp] $(date '+%Y-%m-%d %H:%M:%S') Launching TradingView with --remote-debugging-port=9222"
exec "$TV_BINARY" --remote-debugging-port=9222
