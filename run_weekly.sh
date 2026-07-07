#!/bin/bash
# run_weekly.sh — 每週市場情緒週報(stage9 Day3 §3.3)
#
# launchd 週六 09:00 觸發。流程:
#   1. fetch_weekly → weekly.json + macro.db(NAAIM 官方全量重建、週融資累積、XLY/XLP、週大盤)
#   2. render_weekly → weekly.html + snapshot weekly_{date}.html + matplotlib PNG(NAAIM / XLY-XLP)
#   3. publish 輕量路徑(★ 含 git pull --rebase):weekly.json/html/snapshot/PNG + style → push
#   4. Discord 週報摘要(警報 + 關鍵值 + 連結)
#
# ★ git pull --rebase --autostash:Actions 並行遷移期防衝突(與 run_macro.sh 同策略)。
# 與 19:00 主跑解耦;失敗不影響主跑。

cd "$(dirname "$0")" || exit 1
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

TS() { date '+%Y-%m-%d %H:%M:%S'; }
PAGES_URL="https://mardichao-dotcom.github.io/daily-stock-analysis/weekly.html"

echo "[weekly $(TS)] 開始每週情緒週報..."

# ── [1] fetch_weekly(NAAIM 全量重建 + 週資料)─────────────────────────────────
echo "[weekly 1/4] 抓週報數據 → weekly.json + macro.db..."
python3 -m src.fetch_weekly 2>&1 | grep -vE 'NotOpenSSL|warnings.warn'
FW_EC=${PIPESTATUS[0]}
if [[ $FW_EC -ne 0 ]]; then
    echo "[weekly $(TS)] ❌ fetch_weekly 失敗(exit $FW_EC)"
    python3 -m src.notify_discord --message "❌ 週報:fetch_weekly 失敗,本週情緒面未更新" || true
    exit 1
fi

# ── [2] render_weekly(HTML + snapshot + PNG)──────────────────────────────────
echo "[weekly 2/4] render weekly.html + PNG..."
python3 -m src.render_weekly 2>&1 | grep -vE 'NotOpenSSL|warnings.warn'
RW_EC=${PIPESTATUS[0]}
if [[ $RW_EC -ne 0 ]]; then
    echo "[weekly $(TS)] ❌ render_weekly 失敗(exit $RW_EC)"
    python3 -m src.notify_discord --message "❌ 週報:render_weekly 失敗" || true
    exit 1
fi

# ── [3] publish 輕量(git pull --rebase 防 Actions 並行衝突)───────────────────
echo "[weekly 3/4] publish 輕量(git pull --rebase)..."
publish_weekly() {
    # W3:git 工作樹殘留檢查 → 告警 + 跳過本輪 publish
    if ! bash scripts/git_worktree_check.sh; then
        python3 -m src.notify_discord --message \
            "🚨 [git 工作樹] weekly publish 偵測到 rebase/merge 殘留,本輪跳過 push。請人工處理。" || true
        return 1
    fi
    git add -f docs/weekly.html docs/weekly_*.html docs/data/v2/weekly.json \
        docs/assets/weekly/*.png docs/assets/style_v2.css 2>/dev/null || true
    if git diff --cached --quiet; then
        echo "      無變更,仍嘗試同步 origin"
    else
        git commit -m "weekly $(date +%Y-%m-%d) 情緒週報更新"
    fi
    git pull --rebase --autostash origin main || { echo "git pull --rebase 失敗"; return 1; }
    git push origin main
}
publish_weekly
[[ $? -ne 0 ]] && echo "[weekly $(TS)] ⚠️ publish 失敗(摘要仍會發,網站待下次)"

# ── [4] Discord 週報摘要 ──────────────────────────────────────────────────────
echo "[weekly 4/4] Discord 週報摘要..."
SUMMARY=$(python3 - <<'PY' 2>/dev/null
import json
d = json.load(open("docs/data/v2/weekly.json", encoding="utf-8"))
n = d.get("naaim", {}); vix = d.get("vix", {}); xx = d.get("xly_xlp", {})
tw = d.get("taiex", {}); mg = d.get("margin", {})
lines = [f"📅 **每週市場情緒週報** {d.get('data_through','')}"]
lines.append(f"• NAAIM 機構曝險:{n.get('latest_value','N/A')}(最新 {n.get('latest_date','')})")
lines.append(f"• VIX 波動率:{vix.get('value','N/A')}")
cross = {"death":"死亡交叉 ⚠️","golden":"黃金交叉","none":("偏多" if xx.get('trend')=='risk_on' else '偏空')}.get(xx.get('cross'),'')
lines.append(f"• XLY/XLP 消費信心:{xx.get('ratio','N/A')}({cross})")
twk = tw.get('week_change_pct')
lines.append(f"• 加權指數:{tw.get('close','N/A')}" + (f"(本週 {twk:+.2f}%)" if isinstance(twk,(int,float)) else ""))
mw = mg.get('wow_pct')
lines.append(f"• 市場融資:{mg.get('total','N/A'):,.1f} 億元" + (f"(週 {mw:+.2f}%)" if isinstance(mw,(int,float)) else "") if isinstance(mg.get('total'),(int,float)) else "• 市場融資:N/A")
alerts = d.get("alerts", [])
lines.append("")
lines.append("🚨 **本週警報**:\n" + ("\n".join(alerts) if alerts else "無極端訊號"))
errs = d.get("errors", [])
if errs:
    lines.append("⚠️ 失敗來源:" + "; ".join(errs))
# W3 慢指標(審計):.git 大小 + publish→verify 耗時(膨脹是跨月曲線,週報盯趨勢)
try:
    rows = [json.loads(l) for l in open("state/slow_metrics.jsonl", encoding="utf-8")
            if l.strip()][-5:]
    if rows:
        secs = sorted(r.get("publish_verify_sec", 0) for r in rows)
        lines.append(f"🩺 慢指標(近 {len(rows)} 跑):.git {rows[-1].get('git_mb','?')}MB"
                     f" ｜ publish→verify 中位 {secs[len(secs)//2]}s / 最大 {secs[-1]}s")
except (OSError, json.JSONDecodeError):
    pass
print("\n".join(lines))
PY
)
if [[ -n "$SUMMARY" ]]; then
    python3 -m src.notify_discord --message "$SUMMARY
🔗 $PAGES_URL" 2>&1 | grep -vE 'NotOpenSSL|warnings.warn'
else
    python3 -m src.notify_discord --message "⚠️ 週報摘要組裝失敗,請查 weekly.json" || true
fi

echo "[weekly $(TS)] 完成。"
