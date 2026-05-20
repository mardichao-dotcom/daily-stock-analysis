"""
status_writer.py — 統一狀態回報架構的 helper

用法（由 run_all.sh 呼叫）:
  # 開始一次執行
  python3 src/status_writer.py --init --tool stock_dashboard

  # 寫入單一步驟結果
  python3 src/status_writer.py --tool stock_dashboard \
      --step tv_collect --status ok --duration 312 --note "87 symbols, 0 errors"

  # 步驟失敗時附上 log tail
  python3 src/status_writer.py --tool stock_dashboard \
      --step tv_collect --status fail --duration 65 \
      --note "CDP connect refused" --log-file /tmp/step_tv_collect.log

  # 步驟跳過（因前一步失敗）
  python3 src/status_writer.py --tool stock_dashboard \
      --step daily_update --status skip

  # 結束整次執行（計算 overall）
  python3 src/status_writer.py --finish --tool stock_dashboard

狀態檔路徑: state/automation_status.json
overall 規則:
  全部 step ok           → "ok"
  任一 step fail         → "partial"
  明確傳入 --aborted     → "fail"（整條龍中斷，未呼叫 --finish 的情境）
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
STATUS_FILE  = os.path.join(PROJECT_ROOT, "state", "automation_status.json")
LOG_TAIL_LINES = 12
TZ_TAIPEI = timezone(timedelta(hours=8))

# ── helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")

def _today() -> str:
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")

def _load() -> dict:
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _read_log_tail(log_file: str, n: int = LOG_TAIL_LINES) -> list[str]:
    if not log_file or not os.path.exists(log_file):
        return []
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-n:] if l.strip()]
    except OSError:
        return []

# ── sub-commands ──────────────────────────────────────────────────────────────

def cmd_init(tool: str) -> None:
    data = _load()
    data[tool] = {
        "run_date":    _today(),
        "started_at":  _now_iso(),
        "finished_at": None,
        "overall":     "running",
        "steps":       [],
    }
    _save(data)
    print(f"[status_writer] init  tool={tool}  date={data[tool]['run_date']}")

def cmd_step(tool: str, step: str, status: str,
             duration: int, note: str, log_file: str) -> None:
    data = _load()
    entry = data.setdefault(tool, {"steps": []})
    steps = entry.setdefault("steps", [])

    record: dict = {
        "name":       step,
        "status":     status,       # ok | fail | skip
        "duration_s": duration,
        "note":       note,
    }
    if status == "fail" and log_file:
        tail = _read_log_tail(log_file)
        if tail:
            record["log_tail"] = tail

    # Overwrite if step already present (retry scenario)
    for i, s in enumerate(steps):
        if s["name"] == step:
            steps[i] = record
            break
    else:
        steps.append(record)

    _save(data)
    icon = {"ok": "✅", "fail": "❌", "skip": "⏭️"}.get(status, "?")
    print(f"[status_writer] step  {icon} {step:<18} {status}  {duration}s  {note}")

def cmd_finish(tool: str, aborted: bool = False) -> None:
    data = _load()
    entry = data.setdefault(tool, {"steps": []})

    if aborted:
        overall = "fail"
    else:
        steps = entry.get("steps", [])
        if any(s["status"] == "fail" for s in steps):
            overall = "partial"
        else:
            overall = "ok"

    entry["overall"]     = overall
    entry["finished_at"] = _now_iso()
    _save(data)
    icon = {"ok": "✅", "partial": "⚠️", "fail": "❌"}.get(overall, "?")
    print(f"[status_writer] finish  {icon} overall={overall}")

# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Update automation_status.json")
    parser.add_argument("--tool",     required=True, help="Tool key, e.g. stock_dashboard")
    parser.add_argument("--init",     action="store_true", help="Start a new run")
    parser.add_argument("--finish",   action="store_true", help="Finalize run, compute overall")
    parser.add_argument("--aborted",  action="store_true", help="Mark overall as fail (used with --finish)")
    parser.add_argument("--step",     help="Step name")
    parser.add_argument("--status",   choices=["ok", "fail", "skip"], help="Step status")
    parser.add_argument("--duration", type=int, default=0, help="Duration in seconds")
    parser.add_argument("--note",     default="", help="Short note/summary")
    parser.add_argument("--log-file", default="", dest="log_file",
                        help="Log file path (tail captured on fail)")
    args = parser.parse_args()

    if args.init:
        cmd_init(args.tool)
    elif args.finish:
        cmd_finish(args.tool, aborted=args.aborted)
    elif args.step:
        if not args.status:
            parser.error("--step requires --status")
        cmd_step(args.tool, args.step, args.status,
                 args.duration, args.note, args.log_file)
    else:
        parser.error("Must specify --init, --finish, or --step")

if __name__ == "__main__":
    main()
