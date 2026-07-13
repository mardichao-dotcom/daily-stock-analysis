#!/bin/bash
# tv_preflight_selfheal.sh — preflight + 自癒(把「檢查」綁到「使用」的同一時間點)
#
# 背景(2026-07-13 事故):18:45 的 tv-restart 成功,但 TV 會在 15 分鐘內再度 wedge,
#   19:00 主跑 preflight 失敗時搆不到那次重啟 → 中間空窗無人補救。主導者 7/20 入營
#   12 天無人值守,這個縫是最大單一故障點。
#
# 解法:主跑要用 TV 的「當下」先 preflight;失敗就用【既有】tv_restart.sh 重啟一次、
#   等它三層驗證(CDP + chart target + 實際渲染 / TradingViewApi ready)通過、再重試 preflight。
#   最多 2 輪(重啟→等→試);兩輪都失敗才判真失敗,交回主跑既有告警流程。
#
# 不新造重啟系統:重啟一律走 scripts/tv_restart.sh(它已含白屏保護 + 與 KeepAlive 共存,
#   每晚 18:45 實證可用)。本檔只是「preflight 迴圈 + 觸發既有重啟」的薄層編排。
#
# 冪等/防呆:
#   - 只在 preflight 「確實失敗」時才重啟——TV 本來就好的正常日,preflight 一次就過、
#     直接 exit 0,不重啟、不延遲。
#   - 觸發前先 pgrep 既有 tv_restart.sh(如 18:45 plist),若正在跑就等它完成、不重複觸發,
#     避免兩個重啟指令並發。tv_restart.sh 本身與 KeepAlive(tv_cdp_launch.sh)以同一 port
#     9222 重啟,macOS/launchd 收斂為單一實例(18:45 每晚實證)。
#
# 退出碼:0 = TV 就緒(可安全進 tv_collect);1 = 自癒 2 輪後仍失敗(需人工)。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

HEAL_ROUNDS="${TV_HEAL_ROUNDS:-2}"          # 重啟輪數上限(可環境變數覆寫,測試用)
TS() { date '+%Y-%m-%d %H:%M:%S'; }
notify() { python3 -m src.notify_discord --message "$1" 2>/dev/null || true; }

attempt=0
while : ; do
    if node scripts/tv_collect.mjs --preflight-only 2>&1; then
        if [ "$attempt" -gt 0 ]; then
            echo "[selfheal $(TS)] ✅ 自癒後 preflight 通過(第 $attempt 輪重啟後)"
            notify "✅ [TV 自癒] 主跑重啟 TV ${attempt} 輪後 preflight 通過,採集續跑。"
        fi
        exit 0
    fi

    attempt=$((attempt + 1))
    if [ "$attempt" -gt "$HEAL_ROUNDS" ]; then
        echo "[selfheal $(TS)] ❌ 自癒 ${HEAL_ROUNDS} 輪後 preflight 仍失敗,放棄"
        notify "🚨 [TV 自癒] 主跑 preflight 自動重啟 ${HEAL_ROUNDS} 輪後仍失敗,今日採集中止,需人工介入。"
        exit 1
    fi

    echo "[selfheal $(TS)] ⚠️ preflight 失敗 → 自癒第 ${attempt}/${HEAL_ROUNDS} 輪..."
    if pgrep -f "scripts/tv_restart.sh" >/dev/null 2>&1; then
        echo "[selfheal $(TS)] 偵測到既有 tv_restart 進行中 → 等其完成(不重複觸發)"
        for _ in $(seq 1 30); do
            pgrep -f "scripts/tv_restart.sh" >/dev/null 2>&1 || break
            sleep 5
        done
    else
        notify "🔧 [TV 自癒] 主跑 preflight 失敗 → 自動重啟 TV(第 ${attempt} 輪)…"
        bash scripts/tv_restart.sh 2>&1 || true   # 既有三層驗證重啟(阻塞至驗證通過或放棄)
    fi
    # 迴圈頂重新 preflight 驗證重啟結果
done
