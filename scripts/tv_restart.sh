#!/bin/bash
# tv_restart.sh — 定時重啟 TradingView Desktop(待辦 1b / hotfix 2026-06-12 hyp3)
#
# 背景:長時間運行的 TV Desktop chart 引擎會劣化(6/12:uptime 5.7 天 → setSymbol/bars 全卡)。
# 對策:每平日 18:45(19:00 主跑前)重啟,uptime 不過夜累積。
#
# 2026-07-06 白屏事故強化:
#   目擊——18:45 重啟後 TV 白屏(視窗在、內容空白)→ 渲染程序沒載入 → CDP 殼活著但頁面空白
#   → 舊驗證只驗「chart target 存在」誤判通過 → tv_collect 等永不 ready 的 API 燒滿 30 分。
#   升級為三層驗證:① CDP 可達 → ② chart target 存在 → ③ 注入 JS 確認實際渲染
#   (document.body 有內容 + TradingViewApi 60s 內 ready,見 tv_verify_render.mjs)。
#   白屏 → 自動再重啟(最多 2 次);仍白屏 → Discord 告警「需人工」+ 放棄當晚重啟(不再 thrash)。
#
# 退出碼:0 = 三層驗證通過(已渲染);1 = CDP 始終未就緒;2 = 白屏/未渲染(需人工)。

TV_BINARY="/Applications/TradingView.app/Contents/MacOS/TradingView"
CHART_URL="${TV_CHART_URL:-https://tw.tradingview.com/chart/xdySlor8/}"   # 復原導航目標,可環境變數覆寫
CDP="http://127.0.0.1:9222"
MAX_ATTEMPTS=2
TS() { date '+%Y-%m-%d %H:%M:%S'; }

cd "$(dirname "$0")/.." || exit 1

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
    echo "[tv-restart $(TS)] ❌ $1"
    python3 -m src.notify_discord --message "🚨 [TV 重啟] $1" 2>/dev/null || \
        echo "[tv-restart $(TS)] ⚠️ 連 Discord 告警都發不出去" >&2
}

kill_tv() {
    osascript -e 'quit app "TradingView"' 2>/dev/null
    for i in $(seq 1 20); do pgrep -x TradingView >/dev/null 2>&1 || break; sleep 1; done
    if pgrep -x TradingView >/dev/null 2>&1; then
        echo "[tv-restart $(TS)] 仍存活,SIGTERM"
        pkill -x TradingView 2>/dev/null || true; sleep 3
        pgrep -x TradingView >/dev/null 2>&1 && { pkill -9 -x TradingView 2>/dev/null || true; sleep 2; }
    fi
}

relaunch_wait_cdp() {
    echo "[tv-restart $(TS)] 以 --remote-debugging-port=9222 重啟"
    "$TV_BINARY" --remote-debugging-port=9222 >/dev/null 2>&1 &
    for i in $(seq 1 18); do
        sleep 5
        if curl -s -m 3 "$CDP/json/version" >/dev/null 2>&1; then
            echo "[tv-restart $(TS)] ① CDP 9222 就緒(約 $((i*5))s)"; return 0
        fi
    done
    return 1
}

# ② chart target 存在(必要時 CDP 導航復原);③ 注入 JS 確認實際渲染
ensure_chart_and_render() {
    local i
    for i in $(seq 1 12); do has_chart_target && break; sleep 5; done
    if ! has_chart_target; then
        # json/new(CDP HTTP PUT)在 Chrome 140(TV 3.3.0)已停用 → 改用 Page.navigate 主視窗復原
        echo "[tv-restart $(TS)] 無 chart target → Page.navigate 主視窗復原 $CHART_URL ..."
        TV_CHART_URL="$CHART_URL" node scripts/tv_open_chart.mjs 2>&1 || true
    fi
    if ! has_chart_target; then
        echo "[tv-restart $(TS)] ② 仍無 chart target(疑白屏/停在 new-tab)"; return 1
    fi
    echo "[tv-restart $(TS)] ② chart target 就緒 → ③ 注入 JS 驗證實際渲染..."
    if node scripts/tv_verify_render.mjs 2>&1; then
        echo "[tv-restart $(TS)] ③ 實際渲染確認 ✓"; return 0
    fi
    echo "[tv-restart $(TS)] ③ 未渲染/白屏(chart target 在但頁面空白或 API 未 ready)"; return 2
}

# ── 主流程:最多 MAX_ATTEMPTS 次重啟,三層全過才算成功 ─────────────────────────
cdp_ever_up=0
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    echo "[tv-restart $(TS)] === 重啟嘗試 $attempt/$MAX_ATTEMPTS ==="
    kill_tv
    if ! relaunch_wait_cdp; then
        echo "[tv-restart $(TS)] CDP 90s 內未就緒(attempt $attempt)"; continue
    fi
    cdp_ever_up=1
    ensure_chart_and_render && {
        echo "[tv-restart $(TS)] ✅ 三層驗證通過(CDP + chart target + 實際渲染)"
        exit 0
    }
    echo "[tv-restart $(TS)] attempt $attempt 未通過,$([ "$attempt" -lt "$MAX_ATTEMPTS" ] && echo '再試一次' || echo '已達上限')"
done

if [ "$cdp_ever_up" -eq 0 ]; then
    alert_manual "TV 重啟 ${MAX_ATTEMPTS} 次後 CDP 9222 始終未就緒,需人工介入"
    exit 1
fi
alert_manual "TV 白屏:重啟 ${MAX_ATTEMPTS} 次後仍未渲染(CDP 殼在但頁面空白 / TradingViewApi 未 ready),需人工開啟並確認 chart 正常顯示。已放棄當晚自動重啟,TV 維持現狀。"
exit 2
