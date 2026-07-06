#!/bin/bash
# run_macro.sh — 每日總經快覽 + Discord 早報(stage9 Day2 §3.2)
#
# launchd 平日 08:30 觸發。流程:
#   1. fetch_macro → docs/data/v2/macro.json(指數/VIX/融資餘額,N/A 護欄)
#   2. publish 輕量路徑(★ 含 git pull --rebase):macro.json + 前端資產 + 新聞關鍵字 → push
#   3. macro_report → Discord 早報散文(Haiku,無 key 退回結構化模板)
#
# ★ git pull --rebase(constraint):Actions 並行遷移期兩邊都會 push,先 rebase 防衝突。
# 與 19:00 主跑解耦;失敗不影響主跑。

cd "$(dirname "$0")" || exit 1
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

TOOL="macro_daily"
LOG_DIR="logs"; mkdir -p "$LOG_DIR"
TS() { date '+%Y-%m-%d %H:%M:%S'; }

echo "[macro $(TS)] 開始每日總經快覽..."

# ── [1] fetch_macro ───────────────────────────────────────────────────────────
echo "[macro 1/3] 抓總經數據 → macro.json..."
python3 -m src.fetch_macro 2>&1 | grep -vE 'NotOpenSSL|warnings.warn'
FM_EC=${PIPESTATUS[0]}
if [[ $FM_EC -ne 0 ]]; then
    echo "[macro $(TS)] ❌ fetch_macro 失敗(exit $FM_EC)"
    python3 -m src.notify_discord --message "❌ macro 早報:fetch_macro 失敗,今日總經未更新" || true
    exit 1
fi

# ── [1.5] fetch_news(新聞資料層,只做資料;過濾共用 news_keywords.json,非致命)──
echo "[macro 1.5] 抓新聞 RSS → news.json(關鍵字過濾,只存標題+連結)..."
python3 -m src.fetch_news 2>&1 | grep -vE 'NotOpenSSL|warnings.warn' || \
    echo "[macro $(TS)] ⚠️ fetch_news 失敗(不影響早報與總經)"

# ── [2] publish 輕量(git pull --rebase 防 Actions 並行衝突)───────────────────
echo "[macro 2/3] publish 輕量(git pull --rebase)..."
publish_macro() {
    # W3:git 工作樹殘留檢查(rebase 撞車後遺症)→ 告警 + 跳過本輪 publish
    if ! bash scripts/git_worktree_check.sh; then
        python3 -m src.notify_discord --message \
            "🚨 [git 工作樹] macro publish 偵測到 rebase/merge 殘留,本輪跳過 push。請人工處理。" || true
        return 1
    fi
    # 先 commit 本輪 macro 檔(fetch_macro 剛改了 macro.json),再 rebase:
    git add -f docs/data/v2/macro.json docs/data/v2/news.json docs/assets/events.js \
        docs/assets/style_v2.css config/news_keywords.json 2>/dev/null || true
    if git diff --cached --quiet; then
        echo "      無變更,仍嘗試同步 origin"
    else
        git commit -m "macro $(date +%Y-%m-%d) 總經快覽更新"
    fi
    # ★ pull --rebase --autostash:並行期防衝突;--autostash 處理無關的未暫存檔(如 state/)
    git pull --rebase --autostash origin main || { echo "git pull --rebase 失敗"; return 1; }
    git push origin main
}
publish_macro
[[ $? -ne 0 ]] && echo "[macro $(TS)] ⚠️ publish 失敗(早報仍會發,網站待下次)"

# ── [2.5] 並行比對:記錄 Mac 側指數/融資(§4,非致命)─────────────────────────
echo "[macro 2.5] 並行比對記錄 Mac 側..."
python3 -m src.parallel_compare record-mac 2>&1 | grep -vE 'NotOpenSSL|warnings.warn' || \
    echo "[macro $(TS)] ⚠️ 並行比對記錄失敗(不影響早報)"

# ── [3] Discord 早報(Haiku 散文 / 模板 fallback)──────────────────────────────
echo "[macro 3/3] Discord 早報..."
python3 -m src.macro_report 2>&1 | grep -vE 'NotOpenSSL|warnings.warn'

echo "[macro $(TS)] 完成。"
