"""
notify_discord.py — 發一則簡短 Discord 訊息(P0-D 美股補跑回報用)

沿用 daily_supervisor 的 webhook 載入 + 發送邏輯,不重造輪子。
用法:
    python3 -m src.notify_discord --message "✅ us-refresh 23 檔美股已更新"
    python3 -m src.notify_discord --message "..." --dry-run   # 只預覽不發
webhook 未設定 → 靜默跳過(exit 0),不擋上游流程。
"""
from __future__ import annotations
import argparse
import sys

from src.daily_supervisor import _load_webhook, _send, _preview


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a short Discord message")
    parser.add_argument("--message", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        _preview(args.message)
        return

    webhook = _load_webhook()
    if not webhook:
        print("[notify_discord] 未設定 Discord webhook,跳過發送。")
        return
    _send(webhook, args.message)


if __name__ == "__main__":
    main()
