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
CBC_CFG   = os.path.join(PROJECT_ROOT, "config", "cbc_dates.json")
TW_MACRO_CFG = os.path.join(PROJECT_ROOT, "config", "tw_macro_dates.json")
TW_HOLIDAYS = os.path.join(PROJECT_ROOT, "config", "tw_holidays.json")
WATCHLIST = os.path.join(PROJECT_ROOT, "config", "watchlist.json")
OUT_DIR   = os.path.join(PROJECT_ROOT, "docs", "data", "v2")
EVENTS_JSON = os.path.join(OUT_DIR, "events.json")
CONF_ALL_JSON = os.path.join(OUT_DIR, "events_all_conferences.json")
DIV_ALL_JSON = os.path.join(OUT_DIR, "events_all_dividends.json")

MACRO_FUTURE_DAYS = 14
CONF_FUTURE_DAYS  = 30
CROSS_YEAR_WINDOW = 30          # 距年底 <30 天 → 查 Y 與 Y+1(跨年邊界)
SCRAPE_TIMEOUT    = 120         # subprocess 硬上限(整步)
SCRAPE_RETRIES    = 2           # 首次 + 重試一次

# 重要度分級(渲染密度用;中高以上 = {high, medium_high},超量時只留這兩級)
LV_HIGH, LV_MH, LV_MED = "high", "medium_high", "medium"

# FRED release_id → (顯示名, importance, level)
FRED_RELEASES = {
    10: ("美國 CPI",            "high",   LV_HIGH),
    50: ("美國就業報告(非農/失業率)", "high", LV_HIGH),
    46: ("美國 PPI",            "medium", LV_MED),
    53: ("美國 GDP",            "medium", LV_MED),
    54: ("美國 PCE / 個人收支",  "medium", LV_MED),
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
    for rid, (name, imp, lv) in FRED_RELEASES.items():
        dates = fetch(fred_key, rid)
        for ds in dates:
            try:
                d = dt_date.fromisoformat(ds)
            except (ValueError, TypeError):
                continue
            if today <= d <= horizon:
                events.append({
                    "date": ds, "type": "macro", "name": name,
                    "title": f"{name} 發布", "importance": imp, "level": lv,
                    "source": f"FRED release_id {rid}", "fetched_at": now_iso,
                })
    return events


# ── A 批:純規則 / config 事件(零外部依賴)──────────────────────────────────

def third_wednesday(year: int, month: int) -> dt_date:
    """該月第三個週三(台指期月結算日)。"""
    first = dt_date(year, month, 1)
    offset = (2 - first.weekday()) % 7          # 週三=2;到第一個週三的天數
    return first + timedelta(days=offset + 14)  # +14 = 第三個


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def build_settlement_events(today: dt_date, now_iso: str,
                            days: int = MACRO_FUTURE_DAYS) -> list[dict]:
    """台指期月結算日(每月第三個週三,程式直接推算,不需資料源)。"""
    horizon = today + timedelta(days=days)
    events: list[dict] = []
    for y, m in (( today.year, today.month), _next_month(today.year, today.month)):
        d = third_wednesday(y, m)
        if today <= d <= horizon:
            events.append({
                "date": d.isoformat(), "type": "settlement", "name": "台指期結算",
                "title": "台指期(TX)月結算日", "importance": "期貨/選擇權結算,留意尾盤波動",
                "level": LV_MED, "source": "程式推算(每月第三個週三)", "fetched_at": now_iso,
            })
    return events


def build_cbc_events(today: dt_date, now_iso: str, days: int = MACRO_FUTURE_DAYS,
                     cfg_path: str = CBC_CFG) -> list[dict]:
    """台灣央行理監事會(季度,官方年度時程 config;同 FOMC 模式)。"""
    cfg = _load_json(cfg_path) or {}
    horizon = today + timedelta(days=days)
    events: list[dict] = []
    for m in cfg.get("meetings", []):
        try:
            d = dt_date.fromisoformat(m.get("date", ""))
        except (ValueError, TypeError):
            continue
        if today <= d <= horizon:
            q = m.get("quarter", "")
            events.append({
                "date": d.isoformat(), "type": "macro_tw", "name": "央行理監事會",
                "title": f"中央銀行理監事會{('(' + q + ')') if q else ''}——利率決議 + 記者會",
                "importance": "台灣利率政策", "level": LV_MH,
                "source": "央行官方年度日程", "fetched_at": now_iso,
            })
    return events


def _load_holidays(path: str = TW_HOLIDAYS) -> tuple[set, list]:
    cfg = _load_json(path) or {}
    return set((cfg.get("holidays") or {}).keys()), cfg.get("years", [])


def next_business_day(d: dt_date, holidays: set) -> dt_date:
    """d 起(含)第一個營業日:跳過週六日與 holidays。"""
    while d.weekday() >= 5 or d.isoformat() in holidays:
        d += timedelta(days=1)
    return d


def build_monthly_revenue_events(today: dt_date, now_iso: str,
                                 days: int = MACRO_FUTURE_DAYS,
                                 holidays_path: str = TW_HOLIDAYS) -> list[dict]:
    """月營收公布截止:法定每月 10 日前,遇假日順延次一營業日;單一事件呈現。"""
    holidays, _yrs = _load_holidays(holidays_path)
    horizon = today + timedelta(days=days)
    events: list[dict] = []
    seen = set()
    for y, m in ((today.year, today.month), _next_month(today.year, today.month)):
        deadline = next_business_day(dt_date(y, m, 10), holidays)
        if deadline.isoformat() in seen:
            continue
        seen.add(deadline.isoformat())
        if today <= deadline <= horizon:
            events.append({
                "date": deadline.isoformat(), "type": "macro_tw",
                "name": "台股月營收公布截止",
                "title": "上市櫃全體月營收公布截止(每月 10 日前,遇假順延)",
                "importance": "當月營收動能揭曉", "level": LV_MH,
                "source": "證交法/公司法 每月 10 日前", "fetched_at": now_iso,
            })
    return events


def build_tw_macro_events(today: dt_date, now_iso: str, days: int = MACRO_FUTURE_DAYS,
                          cfg_path: str = TW_MACRO_CFG) -> list[dict]:
    """台灣 CPI(主計總處)+ 出口統計(財政部),config 年度時程(官方無機器可讀行事曆)。"""
    cfg = _load_json(cfg_path) or {}
    horizon = today + timedelta(days=days)
    spec = [("cpi", "台灣 CPI", "台灣 CPI 消費者物價指數發布(主計總處)"),
            ("export", "台灣出口統計", "財政部 進出口貿易統計發布")]
    events: list[dict] = []
    for key, name, title in spec:
        for ds in cfg.get(key, []):
            try:
                d = dt_date.fromisoformat(ds)
            except (ValueError, TypeError):
                continue
            if today <= d <= horizon:
                events.append({
                    "date": ds, "type": "macro_tw", "name": name, "title": title,
                    "importance": "台灣總經", "level": LV_MH,
                    "source": "官方月度時程(config,標準日推估待校正)", "fetched_at": now_iso,
                })
    return events


# ── B 批:除權息(TWSE/TPEx 官方預告表,全市場存檔 + watchlist 過濾)──────────

XD_TWSE = "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"
XD_TPEX = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _roc_compact_to_iso(s) -> str | None:
    """'1150709'(民國 YYYMMDD)→ '2026-07-09';非法回 None(用 date() 驗證)。"""
    s = str(s or "").strip()
    if len(s) < 7 or not s.isdigit():
        return None
    try:
        return dt_date(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:])).isoformat()
    except ValueError:
        return None


def _http_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_dividends_guarded() -> list[dict] | None:
    """全市場除權息預告 → [{code, name, date, kind, market}]。
    上市失敗 → None(觸發護欄保留前值);上櫃失敗僅少上櫃(上市仍有值)。"""
    out: list[dict] = []
    try:
        for r in _http_json(XD_TWSE):
            iso = _roc_compact_to_iso(r.get("Date"))
            code = str(r.get("Code", "")).strip()
            if iso and code:
                out.append({"code": code, "name": str(r.get("Name", "")).strip(),
                            "date": iso, "kind": str(r.get("Exdividend", "")).strip(),
                            "market": "TWSE"})
    except Exception as e:                       # noqa: BLE001
        print(f"[fetch_events] 除權息 TWSE 失敗: {e}", file=sys.stderr)
        return None
    try:
        for r in _http_json(XD_TPEX):
            iso = _roc_compact_to_iso(r.get("ExRrightsExDividendDate"))
            code = str(r.get("SecuritiesCompanyCode", "")).strip()
            if iso and code:
                out.append({"code": code, "name": str(r.get("CompanyName", "")).strip(),
                            "date": iso, "kind": str(r.get("ExRrightsExDividend", "")).strip(),
                            "market": "TPEX"})
    except Exception as e:                       # noqa: BLE001 — 上櫃失敗不致命
        print(f"[fetch_events] 除權息 TPEx 失敗(僅少上櫃): {e}", file=sys.stderr)
    return out


def dividends_to_events(rows: list[dict], today: dt_date, now_iso: str,
                        wl_codes: dict[str, str], days: int = CONF_FUTURE_DAYS) -> list[dict]:
    """全市場除權息 rows → watchlist-filtered 未來 `days` 天 dividend events(同法說會架構)。"""
    horizon = today + timedelta(days=days)
    events: list[dict] = []
    for r in rows:
        iso = r.get("date")
        code = r.get("code", "")
        if not iso or code not in wl_codes:
            continue
        try:
            d = dt_date.fromisoformat(iso)
        except ValueError:
            continue
        if not (today <= d <= horizon):
            continue
        kind = r.get("kind", "")                 # 息/權/權息/除權/除息
        icon_kind = "除息" if "息" in kind and "權" not in kind else \
                    ("除權" if "權" in kind and "息" not in kind else "除權息")
        events.append({
            "date": iso, "type": "dividend", "symbol": f"{wl_codes[code]}:{code}",
            "name": r.get("name", ""), "title": f"{r.get('name','')} {icon_kind}日",
            "importance": kind or "除權息", "level": LV_MED,
            "source": "TWSE/TPEx 除權除息預告表", "fetched_at": now_iso,
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
                "importance": "high", "level": LV_HIGH, "source": "Fed FOMC 年度日程",
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
            "importance": summary[:60], "level": LV_MED, "source": "MOPS 法人說明會一覽表",
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
    _discord("🔧 [法說會源需維護] MOPS 法人說明會抓取失敗(逾時/崩潰/版面變動)。"
             "events.json 已保留前一日法說會並標 stale;請檢查 src/scrape_conferences.py。")


def _discord(msg: str):
    try:
        from src.notify_discord import _send  # type: ignore
        from src.daily_supervisor import _load_webhook
        wh = _load_webhook()
        if wh:
            _send(wh, msg)
    except Exception as e:                       # noqa: BLE001
        print(f"[fetch_events] Discord 發送失敗: {e}", file=sys.stderr)


def config_expiry_warnings(today: dt_date) -> list[str]:
    """config 年度時程過期檢查(央行/CPI/出口/假日表):最後一筆 < today 或年度未涵蓋 → 提醒更新。"""
    warns: list[str] = []
    cbc = _load_json(CBC_CFG) or {}
    cbc_d = [m.get("date") for m in cbc.get("meetings", []) if m.get("date")]
    if cbc_d and max(cbc_d) < today.isoformat():
        warns.append(f"央行理監事會 config 已過期(最後 {max(cbc_d)}),請更新 cbc_dates.json 隔年日程")
    tm = _load_json(TW_MACRO_CFG) or {}
    for k, label in (("cpi", "台灣 CPI"), ("export", "台灣出口")):
        ds = [x for x in tm.get(k, []) if x]
        if ds and max(ds) < today.isoformat():
            warns.append(f"{label} config 已過期(最後 {max(ds)}),請更新 tw_macro_dates.json")
    hol = _load_json(TW_HOLIDAYS) or {}
    yrs = hol.get("years", [])
    horizon_year = (today + timedelta(days=MACRO_FUTURE_DAYS)).year
    if yrs and (today.year not in yrs or horizon_year not in yrs):
        warns.append(f"假日表 tw_holidays.json 未涵蓋 {today.year}~{horizon_year},"
                     f"月營收順延可能失準,請更新")
    return warns


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run(*, fred_key: str, today: dt_date | None = None,
        alert=_alert_source_needs_maintenance, config_alert=_discord) -> dict:
    today = today or datetime.now(TZ_TAIPEI).date()
    now_iso = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    # 規則/config 事件(美國 FRED/FOMC + A批:結算/央行/月營收/CPI/出口)
    macro = (build_macro_events(fred_key, today, now_iso)
             + build_fomc_events(today, now_iso)
             + build_settlement_events(today, now_iso)
             + build_cbc_events(today, now_iso)
             + build_monthly_revenue_events(today, now_iso)
             + build_tw_macro_events(today, now_iso))
    macro.sort(key=lambda e: e["date"])

    years = roc_years_for(today)
    wl = _watchlist_codes()

    # 法說會(Playwright,第四道護欄)
    rows = scrape_conferences_guarded(years)
    conf_stale = False
    conf_source_date = today.isoformat()
    if rows is None:
        conf_stale = True
        prev = _load_json(EVENTS_JSON) or {}
        conf_events = [e for e in prev.get("events", []) if e.get("type") == "conference"]
        conf_source_date = prev.get("conference_source_date")
        if alert:
            alert()
        conf_all = (_load_json(CONF_ALL_JSON) or {}).get("conferences", [])
    else:
        conf_events = conferences_to_events(rows, today, now_iso, wl)
        conf_all = [r for r in rows if r.get("date") and
                    today <= dt_date.fromisoformat(r["date"]) <= today + timedelta(days=CONF_FUTURE_DAYS)]

    # 除權息(OpenAPI,同法說會架構:失敗→保留前值+stale)
    div_rows = fetch_dividends_guarded()
    div_stale = False
    div_source_date = today.isoformat()
    if div_rows is None:
        div_stale = True
        prev = _load_json(EVENTS_JSON) or {}
        div_events = [e for e in prev.get("events", []) if e.get("type") == "dividend"]
        div_source_date = prev.get("dividend_source_date")
        div_all = (_load_json(DIV_ALL_JSON) or {}).get("dividends", [])
    else:
        div_events = dividends_to_events(div_rows, today, now_iso, wl)
        div_all = [r for r in div_rows if r.get("date") and
                   today <= dt_date.fromisoformat(r["date"]) <= today + timedelta(days=CONF_FUTURE_DAYS)]

    # config 年度時程過期提醒(央行/CPI/出口/假日表)
    warns = config_expiry_warnings(today)
    if warns and config_alert:
        config_alert("🗓️ [事件 config 需更新]\n" + "\n".join("• " + w for w in warns))

    events = sorted(macro + conf_events + div_events, key=lambda e: (e["date"], e["type"]))
    out = {
        "generated_at": now_iso,
        "data_through": today.isoformat(),
        "conference_stale": conf_stale,
        "conference_source_date": conf_source_date,
        "dividend_stale": div_stale,
        "dividend_source_date": div_source_date,
        "macro_count": len(macro),
        "conference_count": len(conf_events),
        "dividend_count": len(div_events),
        "config_warnings": warns,
        "events": events,
    }
    return out, {"generated_at": now_iso, "conferences": conf_all}, \
        {"generated_at": now_iso, "dividends": div_all}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=EVENTS_JSON)
    args = ap.parse_args()
    secrets = _load_json(SECRETS) or {}
    fred_key = secrets.get("fred_api_key", "")
    if not fred_key:
        print("[fetch_events] ⚠️ 無 fred_api_key,總經段將為空", file=sys.stderr)

    out, conf_all, div_all = run(fred_key=fred_key)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(CONF_ALL_JSON, "w", encoding="utf-8") as f:
        json.dump(conf_all, f, ensure_ascii=False, indent=2)
    with open(DIV_ALL_JSON, "w", encoding="utf-8") as f:
        json.dump(div_all, f, ensure_ascii=False, indent=2)
    flags = (" (法說會 STALE)" if out["conference_stale"] else "") + \
            (" (除權息 STALE)" if out["dividend_stale"] else "")
    print(f"✅ events.json → {args.out}  總經/TW {out['macro_count']} + 法說會 "
          f"{out['conference_count']} + 除權息 {out['dividend_count']}{flags}"
          + (f"  ⚠️ config:{out['config_warnings']}" if out["config_warnings"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
