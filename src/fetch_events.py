"""
fetch_events.py — 事件中樞(stage9 Day1,§3.1)

產 docs/data/v2/events.json:未來事件統一 schema
    {date, type: "conference"|"macro", symbol?, name, title, importance, source, fetched_at}

來源:
  - 總經:FRED release/dates(CPI/PPI/Employment/GDP/PCE,未來 14 天)+ FOMC config(未來 14 天)
  - 法說會:Playwright 抓 MOPS(上市+上櫃,未來 30 天;全市場存檔、events.json 只放 watchlist)

四道護欄(§2.1):
  ① 解析後 schema 斷言:欄位/日期不合理 → 視為抓取失敗
  ② verify_publish 抽 3 筆對 MOPS(在 verify_publish.py)
  ③ 解析失敗 → Discord「法說會源需維護」
  ④ 抓取失敗(逾時/崩潰/斷言 fail)→ events.json 保留前一日法說會 + 標 stale,不清空;
     總經段照常更新
Playwright 步驟以 subprocess 包 timeout ≤120s + 失敗重試一次(§ 補充5)。
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta, date as dt_date

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(PROJECT_ROOT))

from src.scrape_conferences import roc_to_iso, ROW_KEYS

TZ_TAIPEI = timezone(timedelta(hours=8))
SECRETS   = os.path.join(PROJECT_ROOT, "config", "secrets.json")
FOMC_CFG  = os.path.join(PROJECT_ROOT, "config", "fomc_dates.json")
WATCHLIST = os.path.join(PROJECT_ROOT, "config", "watchlist.json")
OUT_DIR   = os.path.join(PROJECT_ROOT, "docs", "data", "v2")
EVENTS_JSON = os.path.join(OUT_DIR, "events.json")
CONF_ALL_JSON = os.path.join(OUT_DIR, "events_all_conferences.json")

MACRO_FUTURE_DAYS = 14
CONF_FUTURE_DAYS  = 30
CROSS_YEAR_WINDOW = 30          # 距年底 <30 天 → 查 Y 與 Y+1(跨年邊界)
SCRAPE_TIMEOUT    = 120         # subprocess 硬上限(整步)
SCRAPE_RETRIES    = 2           # 首次 + 重試一次

# FRED release_id → (顯示名, importance)
FRED_RELEASES = {
    10: ("美國 CPI",            "high"),
    50: ("美國就業報告(非農/失業率)", "high"),
    46: ("美國 PPI",            "medium"),
    53: ("美國 GDP",            "medium"),
    54: ("美國 PCE / 個人收支",  "medium"),
}


# ── 純函式(可測試)──────────────────────────────────────────────────────────

def roc_years_for(today: dt_date, window_days: int = CROSS_YEAR_WINDOW) -> list[str]:
    """回需查詢的民國年清單。距年底 window_days 內 → [Y, Y+1](免 12 月漏掉隔年 1 月)。"""
    roc_y = today.year - 1911
    years = [str(roc_y)]
    if (dt_date(today.year, 12, 31) - today).days < window_days:
        years.append(str(roc_y + 1))
    return years


def _load_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _watchlist_codes() -> dict[str, str]:
    """回 {code: exchange_prefix},如 {'2330':'TWSE','6223':'TPEX'}。"""
    w = _load_json(WATCHLIST) or {}
    out: dict[str, str] = {}
    for sec in w.get("台股板塊", {}).values():
        for m in sec.get("成員", []):
            code_full = m.get("code", "")
            if ":" in code_full:
                ex, code = code_full.split(":", 1)
                out[code] = ex
    return out


def validate_conferences(rows: list[dict]) -> bool:
    """護欄①:斷言解析結果 well-formed。空 list 視為合法(可能當年查無);
    非空但欄位缺失/幾乎沒有有效日期 → False(疑似 MOPS 版面變動)。"""
    if not isinstance(rows, list):
        return False
    if not rows:
        return True
    for r in rows[:20]:
        if not all(k in r for k in ROW_KEYS):
            return False
    valid_dates = sum(1 for r in rows if r.get("date"))
    # 版面若變 → date 欄解析全 None;要求至少半數有有效西元日期
    return valid_dates >= max(1, len(rows) // 2)


def build_macro_events(fred_key: str, today: dt_date, now_iso: str,
                       days: int = MACRO_FUTURE_DAYS, *, fetch=None) -> list[dict]:
    """FRED 5 個 release 未來 `days` 天內的發布日 → macro events。fetch 可注入以利測試。"""
    fetch = fetch or _fred_fetch
    horizon = today + timedelta(days=days)
    events: list[dict] = []
    for rid, (name, imp) in FRED_RELEASES.items():
        dates = fetch(fred_key, rid)
        for ds in dates:
            try:
                d = dt_date.fromisoformat(ds)
            except (ValueError, TypeError):
                continue
            if today <= d <= horizon:
                events.append({
                    "date": ds, "type": "macro", "name": name,
                    "title": f"{name} 發布", "importance": imp,
                    "source": f"FRED release_id {rid}", "fetched_at": now_iso,
                })
    return events


def _fred_fetch(key: str, release_id: int) -> list[str]:
    url = (f"https://api.stlouisfed.org/fred/release/dates?release_id={release_id}"
           f"&api_key={key}&file_type=json&sort_order=asc&limit=1000"
           f"&include_release_dates_with_no_data=true")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.load(r)
        return [x["date"] for x in data.get("release_dates", [])]
    except Exception:
        return []


def build_fomc_events(today: dt_date, now_iso: str,
                      days: int = MACRO_FUTURE_DAYS, cfg_path: str = FOMC_CFG) -> list[dict]:
    cfg = _load_json(cfg_path) or {}
    horizon = today + timedelta(days=days)
    events: list[dict] = []
    for m in cfg.get("meetings", []):
        ds = m.get("decision")
        try:
            d = dt_date.fromisoformat(ds)
        except (ValueError, TypeError):
            continue
        if today <= d <= horizon:
            events.append({
                "date": ds, "type": "macro", "name": "FOMC 利率決議",
                "title": "Fed FOMC 利率決議" + (" + 經濟預測(SEP)" if m.get("smp") else ""),
                "importance": "high", "source": "Fed FOMC 年度日程",
                "fetched_at": now_iso,
            })
    return events


def conferences_to_events(rows: list[dict], today: dt_date, now_iso: str,
                          wl_codes: dict[str, str],
                          days: int = CONF_FUTURE_DAYS) -> list[dict]:
    """全市場法說會 rows → watchlist-filtered 未來 `days` 天的 conference events。"""
    horizon = today + timedelta(days=days)
    events: list[dict] = []
    for r in rows:
        iso = r.get("date")
        if not iso:
            continue
        try:
            d = dt_date.fromisoformat(iso)
        except ValueError:
            continue
        if not (today <= d <= horizon):
            continue
        code = r.get("code", "")
        if code not in wl_codes:                 # events.json 只放 watchlist
            continue
        symbol = f"{wl_codes[code]}:{code}"
        place = r.get("place", "")
        summary = r.get("summary", "")
        title = f"{r.get('name','')} 法說會"
        if place:
            title += f"({place[:20]})"
        events.append({
            "date": iso, "type": "conference", "symbol": symbol,
            "name": r.get("name", ""), "title": title,
            "importance": summary[:60], "source": "MOPS 法人說明會一覽表",
            "time": r.get("time", ""), "fetched_at": now_iso,
        })
    return events


# ── Playwright 抓取(subprocess + timeout + retry)────────────────────────────

def scrape_conferences_guarded(years: list[str]) -> list[dict] | None:
    """subprocess 呼叫 scrape_conferences,timeout ≤120s + 重試一次。
    成功且通過斷言 → rows;失敗/逾時/斷言 fail → None(觸發第四道護欄)。"""
    cmd = [sys.executable, "-m", "src.scrape_conferences",
           "--years", ",".join(years), "--markets", "sii,otc"]
    for attempt in range(1, SCRAPE_RETRIES + 1):
        try:
            p = subprocess.run(cmd, cwd=os.path.abspath(PROJECT_ROOT),
                               capture_output=True, text=True, timeout=SCRAPE_TIMEOUT)
            if p.returncode == 0:
                rows = json.loads(p.stdout).get("conferences", [])
                if validate_conferences(rows):
                    return rows
                print(f"[fetch_events] 斷言失敗(疑似 MOPS 版面變動),attempt {attempt}",
                      file=sys.stderr)
            else:
                print(f"[fetch_events] scrape exit {p.returncode} attempt {attempt}: "
                      f"{p.stderr[:200]}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"[fetch_events] scrape 逾時 {SCRAPE_TIMEOUT}s attempt {attempt}",
                  file=sys.stderr)
        except (json.JSONDecodeError, Exception) as e:   # noqa: BLE001
            print(f"[fetch_events] scrape 例外 attempt {attempt}: {e}", file=sys.stderr)
    return None


def _alert_source_needs_maintenance():
    """護欄③:法說會源失敗 → Discord 告警。"""
    try:
        from src.notify_discord import _send  # type: ignore
        from src.daily_supervisor import _load_webhook
        wh = _load_webhook()
        if wh:
            _send(wh, "🔧 [法說會源需維護] MOPS 法人說明會抓取失敗(逾時/崩潰/版面變動)。"
                      "events.json 已保留前一日法說會並標 stale;請檢查 src/scrape_conferences.py。")
    except Exception as e:                       # noqa: BLE001
        print(f"[fetch_events] 告警發送失敗: {e}", file=sys.stderr)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run(*, fred_key: str, today: dt_date | None = None,
        alert=_alert_source_needs_maintenance) -> dict:
    today = today or datetime.now(TZ_TAIPEI).date()
    now_iso = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    macro = build_macro_events(fred_key, today, now_iso) + build_fomc_events(today, now_iso)
    macro.sort(key=lambda e: e["date"])

    years = roc_years_for(today)
    wl = _watchlist_codes()
    rows = scrape_conferences_guarded(years)

    conf_stale = False
    conf_source_date = today.isoformat()
    if rows is None:
        # 第四道護欄:保留前一日 conference,標 stale;macro 照常更新
        conf_stale = True
        prev = _load_json(EVENTS_JSON) or {}
        conf_events = [e for e in prev.get("events", []) if e.get("type") == "conference"]
        conf_source_date = prev.get("conference_source_date")
        if alert:
            alert()
        conf_all = (_load_json(CONF_ALL_JSON) or {}).get("conferences", [])
    else:
        conf_events = conferences_to_events(rows, today, now_iso, wl)
        # 全市場存檔(未來 30 天,不限 watchlist)供日後放寬
        conf_all = [r for r in rows if r.get("date") and
                    today <= dt_date.fromisoformat(r["date"]) <= today + timedelta(days=CONF_FUTURE_DAYS)]

    events = sorted(macro + conf_events, key=lambda e: (e["date"], e["type"]))
    out = {
        "generated_at": now_iso,
        "data_through": today.isoformat(),
        "conference_stale": conf_stale,
        "conference_source_date": conf_source_date,
        "macro_count": len(macro),
        "conference_count": len(conf_events),
        "events": events,
    }
    return out, {"generated_at": now_iso, "conferences": conf_all}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=EVENTS_JSON)
    args = ap.parse_args()
    secrets = _load_json(SECRETS) or {}
    fred_key = secrets.get("fred_api_key", "")
    if not fred_key:
        print("[fetch_events] ⚠️ 無 fred_api_key,總經段將為空", file=sys.stderr)

    out, conf_all = run(fred_key=fred_key)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(CONF_ALL_JSON, "w", encoding="utf-8") as f:
        json.dump(conf_all, f, ensure_ascii=False, indent=2)
    stale = " (法說會 STALE — 走第四道護欄)" if out["conference_stale"] else ""
    print(f"✅ events.json → {args.out}  總經 {out['macro_count']} + 法說會 "
          f"{out['conference_count']}{stale}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
