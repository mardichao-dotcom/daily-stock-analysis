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
# 不新造重啟系統:重啟一律走 scripts/tv_restart.sh(已含白屏保護 + 與 KeepAlive 共存)。
#
# ★ 訊息策略(2026-07-14,手機監控用):事件進行中【不發任何 Discord】,只寫 log + 狀態檔;
#   由主跑收尾的 daily_supervisor 讀狀態檔、發【一則結論訊息】。避免逐輪 🔧 與白屏 🚨 交錯轟炸。
#   → 呼叫 tv_restart.sh 時設 TV_RESTART_QUIET=1,讓它的白屏/CDP 告警也只記 log 不獨立發。
#   → 狀態檔 state/tv_selfheal_status.json:{date, rounds, outcome} 給 daily_supervisor 組結論。
#
# 冪等/防呆:只在 preflight 確實失敗時才重啟;觸發前 pgrep 既有 tv_restart.sh(如 18:45 plist)
#   正在跑就等它完成、不重複觸發。tv_restart.sh 與 KeepAlive 以同 port 9222 收斂為單一實例。
#
# 退出碼:0 = TV 就緒(可安全進 tv_collect);1 = 自癒 2 輪後仍失敗(需人工)。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

HEAL_ROUNDS="${TV_HEAL_ROUNDS:-2}"          # 重啟輪數上限(可環境變數覆寫,測試用)
STATE_FILE="state/tv_selfheal_status.json"
TS() { date '+%Y-%m-%d %H:%M:%S'; }

write_state() {   # $1=rounds  $2=outcome(ok|fail)
    mkdir -p state
    printf '{"date":"%s","rounds":%s,"outcome":"%s","ts":"%s"}\n' \
        "$(date '+%Y-%m-%d')" "$1" "$2" "$(date '+%Y-%m-%dT%H:%M:%S%z')" > "$STATE_FILE"
}

attempt=0
while : ; do
    if node scripts/tv_collect.mjs --preflight-only 2>&1; then
        # 進行中不發 Discord;只寫狀態檔 + log,結論交 daily_supervisor
        [ "$attempt" -gt 0 ] && echo "[selfheal $(TS)] ✅ 自癒後 preflight 通過(重啟 $attempt 輪後)"
        write_state "$attempt" ok
        exit 0
    fi

    attempt=$((attempt + 1))
    if [ "$attempt" -gt "$HEAL_ROUNDS" ]; then
        echo "[selfheal $(TS)] ❌ 自癒 ${HEAL_ROUNDS} 輪後 preflight 仍失敗,放棄(結論由 daily_supervisor 發)"
        write_state "$HEAL_ROUNDS" fail
        exit 1
    fi

    echo "[selfheal $(TS)] ⚠️ preflight 失敗 → 自癒第 ${attempt}/${HEAL_ROUNDS} 輪:重啟 TV(不發 Discord,收尾統一結論)..."
    if pgrep -f "scripts/tv_restart.sh" >/dev/null 2>&1; then
        echo "[selfheal $(TS)] 偵測到既有 tv_restart 進行中 → 等其完成(不重複觸發)"
        for _ in $(seq 1 30); do
            pgrep -f "scripts/tv_restart.sh" >/dev/null 2>&1 || break
            sleep 5
        done
    else
        # TV_RESTART_QUIET=1:tv_restart.sh 的白屏/CDP 告警只記 log,不獨立發 Discord
        TV_RESTART_QUIET=1 bash scripts/tv_restart.sh 2>&1 || true
    fi
    # 迴圈頂重新 preflight 驗證重啟結果
done
