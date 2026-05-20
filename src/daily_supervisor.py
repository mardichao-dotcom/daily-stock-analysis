"""
daily_supervisor.py — 讀狀態檔，彙整成一則 Discord 訊息並發送。

用法:
  python3 src/daily_supervisor.py            # 讀狀態 → 發 Discord
  python3 src/daily_supervisor.py --dry-run  # 只印到 terminal，不發送

Discord webhook URL 讀取順序:
  1. 環境變數 DISCORD_WEBHOOK_URL
  2. config/secrets.json 的 "discord_webhook_url" 欄位
  (兩者都沒有 → dry-run 模式，印出訊息但不發送)

設計原則:
  - 讀不到狀態檔 / 狀態檔損壞 → 仍發一則「⚠️ 總管讀不到狀態」告警（不靜默）
  - 本程式自身例外不中斷通知流程：任何錯誤都 fallback 到告警訊息
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
STATUS_FILE  = os.path.join(PROJECT_ROOT, "state", "automation_status.json")
DASHBOARD_URL = "https://mardichao-dotcom.github.io/daily-stock-analysis/"
TZ_TAIPEI = timezone(timedelta(hours=8))

# ── Discord 工具 ──────────────────────────────────────────────────────────────

def _load_webhook() -> str:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if url:
        return url
    secrets_path = os.path.join(PROJECT_ROOT, "config", "secrets.json")
    if os.path.exists(secrets_path):
        try:
            with open(secrets_path, encoding="utf-8") as f:
                return json.load(f).get("discord_webhook_url", "")
        except (json.JSONDecodeError, OSError):
            pass
    return ""

def _preview(content: str) -> None:
    print("\n" + "─" * 50)
    print("[dry-run] Discord 訊息預覽：")
    print("─" * 50)
    print(content)
    print("─" * 50)

def _send(webhook_url: str, content: str) -> None:
    payload = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url.strip(),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "stock-dashboard-supervisor/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 204):
                print(f"[supervisor] Discord 回傳非 2xx: {resp.status}", file=sys.stderr)
            else:
                print("[supervisor] Discord 訊息發送成功")
    except urllib.error.URLError as e:
        print(f"[supervisor] Discord 發送失敗: {e}", file=sys.stderr)

# ── 格式化工具 ────────────────────────────────────────────────────────────────

def _fmt_duration(seconds: int) -> str:
    if seconds >= 60:
        m, s = divmod(seconds, 60)
        return f"{m}m{s:02d}s"
    return f"{seconds}s"

def _total_duration(entry: dict) -> str:
    try:
        fmt = "%Y-%m-%dT%H:%M:%S+08:00"
        t0 = datetime.strptime(entry["started_at"],  fmt)
        t1 = datetime.strptime(entry["finished_at"], fmt)
        return _fmt_duration(int((t1 - t0).total_seconds()))
    except Exception:
        return "?"

def _step_icon(status: str) -> str:
    return {"ok": "✅", "fail": "❌", "skip": "⏭️", "partial": "⚠️", "running": "🔄"}.get(status, "❓")

def _overall_icon(overall: str) -> str:
    return {"ok": "✅ 全部成功", "partial": "⚠️ 部分失敗", "fail": "❌ 整體失敗"}.get(
        overall, f"❓ {overall}"
    )

# ── 訊息組裝 ──────────────────────────────────────────────────────────────────

def _build_message(status: dict) -> str:
    today = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")
    lines = []

    # ── 整體 header ──
    overall_text = "❓ 狀態未知"
    total_dur    = ""

    # 找 stock_dashboard entry（未來可迭代多工具）
    sd = status.get("stock_dashboard")
    if sd:
        overall_text = _overall_icon(sd.get("overall", ""))
        total_dur    = _total_duration(sd) if sd.get("finished_at") else "(進行中)"

    lines.append(f"📊 每日自動化回報 {today}  {overall_text}")
    lines.append("─" * 33)

    # ── 選股儀表板區塊 ──
    if sd:
        lines.append(f"【選股儀表板】 {_step_icon(sd.get('overall',''))}  ({total_dur})")
        for step in sd.get("steps", []):
            name     = step["name"]
            st       = step["status"]
            dur_s    = step.get("duration_s", 0)
            note     = step.get("note", "")
            icon     = _step_icon(st)
            dur_str  = _fmt_duration(dur_s) if dur_s else ""

            # 組行：  ✅ tv_collect     5m12s  87 symbols, 0 errors
            row = f"  {icon} {name:<18} {dur_str:<8} {note}".rstrip()
            lines.append(row)

            # 失敗時附 log_tail
            if st == "fail":
                tail = step.get("log_tail", [])
                if tail:
                    lines.append(f"     └ log:")
                    for t_line in tail[-6:]:   # 最多顯示 6 行
                        lines.append(f"       {t_line}")
    else:
        lines.append("【選股儀表板】 ❓ 無資料")

    # 未來其他工具在此追加（總經情緒、podcast…）

    # ── dashboard 連結 ──
    publish_step = None
    if sd:
        for s in sd.get("steps", []):
            if s["name"] == "publish":
                publish_step = s
                break

    if publish_step and publish_step["status"] == "ok":
        lines.append(f"🔗 {DASHBOARD_URL}")
    else:
        lines.append(f"🔗 {DASHBOARD_URL}（今日未更新）")

    return "\n".join(lines)

def _fallback_message(reason: str) -> str:
    today = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")
    return (
        f"⚠️ 每日自動化回報 {today}  ── 總管告警\n"
        f"─────────────────────────────────\n"
        f"無法讀取狀態檔，請手動確認！\n"
        f"原因：{reason}\n"
        f"🔗 {DASHBOARD_URL}（今日狀態未知）"
    )

# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="印到 terminal，不發 Discord")
    args = parser.parse_args()

    webhook = _load_webhook()

    try:
        if not os.path.exists(STATUS_FILE):
            raise FileNotFoundError(f"狀態檔不存在：{STATUS_FILE}")
        with open(STATUS_FILE, encoding="utf-8") as f:
            status = json.load(f)
        message = _build_message(status)
    except Exception as e:
        message = _fallback_message(str(e))

    if args.dry_run:
        # 明確 --dry-run：秀完整預覽
        _preview(message)
    elif not webhook:
        # 未設定 webhook：靜默跳過，不發送也不秀預覽
        print("[supervisor] 未設定 Discord webhook，跳過發送。"
              "（可設定 config/secrets.json 或環境變數 DISCORD_WEBHOOK_URL）")
    else:
        _send(webhook, message)

if __name__ == "__main__":
    main()
