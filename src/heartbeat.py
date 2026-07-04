"""
heartbeat.py — 外部心跳(healthchecks.io ping,任務二 2026-07-04)

主跑 verify_publish 全綠後 / us_refresh 成功後呼叫,對 healthchecks.io 發成功 ping。
外部監控在「該 check 逾時未收到 ping」時主動告警——補上「Mac 整台當掉 / 排程沒跑」
這類 in-process 告警照不到的死角(19 天停更就是這種:排程有跑但結果沒被看見)。

設計:
- ping URL 讀 config/secrets.json 的 healthchecks_ping_url;防禦性修正重複前綴(paste error)
- 失敗只記 logs/heartbeat.log,永遠 exit 0(不擋主流程——ping 失敗不該讓發布失敗)
- healthchecks 一個 UUID check 不吃任意子路徑(實測 /us-refresh → 400);us_refresh 與
  主跑共用同一 check(主跑為權威、每日保活),以 POST body 標記來源供 log 區分

用法:
    python3 -m src.heartbeat --body "main-run 2026-07-04 ok"
    python3 -m src.heartbeat --action fail --body "..."   # 主動報失敗(選用)
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
SECRETS = os.path.join(PROJECT_ROOT, "config", "secrets.json")
LOG = os.path.join(PROJECT_ROOT, "logs", "heartbeat.log")
TZ_TAIPEI = timezone(timedelta(hours=8))
VALID_ACTIONS = ("", "start", "fail")   # healthchecks 認得的 UUID 子動作


def _log(line: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(TZ_TAIPEI).strftime('%Y-%m-%dT%H:%M:%S+08:00')}\t{line}\n")
    except OSError:
        pass


def load_ping_url() -> str:
    """讀 healthchecks_ping_url;防禦性去除重複前綴(paste error)。缺 → 回 ''。"""
    if not os.path.exists(SECRETS):
        return ""
    try:
        with open(SECRETS, encoding="utf-8") as f:
            url = json.load(f).get("healthchecks_ping_url", "")
    except (json.JSONDecodeError, OSError):
        return ""
    if not url:
        return ""
    # 修正 'https://hc-ping.com/https://hc-ping.com/<uuid>' 這種重複前綴
    return re.sub(r'^(https://hc-ping\.com/)+', 'https://hc-ping.com/', url.strip())


def ping(action: str = "", body: str = "", retries: int = 3) -> bool:
    """發 ping。回 True 成功 / False 失敗(失敗只記 log,不 raise)。"""
    url = load_ping_url()
    if not url:
        _log("SKIP 未設定 healthchecks_ping_url")
        return False
    if action not in VALID_ACTIONS:
        action = ""
    target = url + (f"/{action}" if action else "")
    data = body.encode("utf-8") if body else b""

    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                target, data=data,
                headers={"User-Agent": "stock-dashboard-heartbeat/1.0"},
                method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201):
                    _log(f"OK action={action or 'success'} attempt={attempt} body={body[:60]}")
                    return True
                last_err = f"HTTP {resp.status}"
        except urllib.error.URLError as e:
            last_err = f"URLError: {e}"
        except Exception as e:                       # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(2 * attempt)
    _log(f"FAIL action={action or 'success'} after {retries} attempts: {last_err} body={body[:60]}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="healthchecks.io 心跳 ping")
    ap.add_argument("--action", default="", choices=list(VALID_ACTIONS),
                    help="'' 成功(預設)/ start / fail")
    ap.add_argument("--body", default="", help="POST body(healthchecks log 可見,標記來源)")
    args = ap.parse_args()
    ok = ping(action=args.action, body=args.body)
    print(f"[heartbeat] {'✅ ping 成功' if ok else '⚠️ ping 失敗(已記 log,不擋流程)'}")
    return 0   # 永遠 0:心跳失敗不擋主流程


if __name__ == "__main__":
    sys.exit(main())
