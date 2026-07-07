"""
fetch_signals.py — stage12 持股水位模型訊號資料層(spec §2,2026-07-08 拍板)

十訊號統一抓取 → macro.db 新表,12.9 年回補(2013-08 起;MA 前置緩衝自 2012-06)。
來源(spike 2026-07-07 全數驗證):
  idx_daily    TAIEX=TWSE FMTQIK 月檔(復用 taiex_daily.fetch_month)/ SPX=yfinance ^GSPC
               20/60/200MA 算好入庫(回放免重算;窗口不足 → NULL 不冒充)
  vix_daily    yfinance ^VIX(+^VIX3M 欄,週報 §5.2 比值用)
  umich_monthly  FRED UMCSENT + ALFRED 全 vintage → 每月值 + **發布日**(反未來函數)
  light_monthly  data.gov.tw dataset 6099 → 國發會 zip「景氣指標與燈號.csv」
               發布日=規則近似(次月27日遇假順延,config/signals.json,拍板 2026-07-08)
  dgs10_daily  FRED DGS10
  usdtwd_daily 雙源:歷史=FRED DEXTAUS(H.10 延遲3~5工作日)/尾端=yfinance TWD=X 暫代,
               官方值到後覆寫(source 欄可辨識暫代列)
  cpi_events   克里夫蘭聯儲 nowcast_month.json(2013-08 起逐月:發布日+實際+會前 nowcast)
  ff_futures_daily  回放=Yahoo ZQ=F 連續近月(會議月=近月,兩次 FOMC 手驗中)
               日常=個別月份合約(當月起 13 個月,FedWatch 自算用)
  fomc_meetings  federalreserve.gov 會議行事曆頁 + FRED DFEDTARU 實際決策交叉驗證

護欄:全項 sanity 範圍閘(超界不入庫+stderr);來源失敗該項跳過、run_daily 回非零
(run_all 非阻斷掛載,daily_supervisor 告警);冪等(INSERT OR REPLACE)。
反未來函數:月頻表一律帶 release_date;引擎(Day 3-4)只准用 release_date <= 當日的列。
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone, timedelta, date as dt_date

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from src import taiex_daily

TZ = timezone(timedelta(hours=8))
MACRO_DB = os.path.join(PROJECT_ROOT, "macro.db")
SECRETS = os.path.join(PROJECT_ROOT, "config", "secrets.json")
SIGNALS_CFG = os.path.join(PROJECT_ROOT, "config", "signals.json")
TW_HOLIDAYS = os.path.join(PROJECT_ROOT, "config", "tw_holidays.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

FRED_OBS = ("https://api.stlouisfed.org/fred/series/observations"
            "?series_id={sid}&api_key={key}&file_type=json{extra}")
NDC_DATASET = "https://data.gov.tw/api/v2/rest/dataset/6099"
CLEV_NOWCAST = ("https://www.clevelandfed.org/-/media/files/webcharts/"
                "inflationnowcasting/nowcast_month.json?sc_lang=en")
FED_CAL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FED_HIST = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm"

BACKFILL_START = "2012-06-01"      # MA200 前置緩衝(回放起點 2013-08)
REPLAY_START = "2013-08-01"

# sanity 範圍閘(超界不入庫;來源異常值防線)
SANE = {
    "TAIEX": (2000, 80000), "SPX": (400, 25000), "VIX": (5, 150),
    "VIX3M": (5, 150), "DGS10": (0, 20), "USDTWD": (20, 45),
    "ZQ": (90, 100.5), "CPI_MOM": (-3, 3), "UMICH": (20, 130),
    "LIGHT_SCORE": (9, 45),
}


def _sane(key: str, v) -> bool:
    lo, hi = SANE[key]
    return v is not None and lo <= v <= hi


def _http_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _http_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fred_key() -> str:
    with open(SECRETS, encoding="utf-8") as f:
        return json.load(f)["fred_api_key"]


def _cfg() -> dict:
    with open(SIGNALS_CFG, encoding="utf-8") as f:
        return json.load(f)


# ── schema ────────────────────────────────────────────────────────────────────
def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS idx_daily (
        market TEXT, date TEXT, close REAL,
        ma20 REAL, ma60 REAL, ma200 REAL, source TEXT,
        PRIMARY KEY (market, date));
    CREATE TABLE IF NOT EXISTS vix_daily (
        date TEXT PRIMARY KEY, close REAL, vix3m REAL, source TEXT);
    CREATE TABLE IF NOT EXISTS umich_monthly (
        month TEXT PRIMARY KEY, value REAL, release_date TEXT, source TEXT);
    CREATE TABLE IF NOT EXISTS light_monthly (
        month TEXT PRIMARY KEY, score REAL, light TEXT,
        release_date TEXT, source TEXT);
    CREATE TABLE IF NOT EXISTS dgs10_daily (
        date TEXT PRIMARY KEY, value REAL, source TEXT);
    CREATE TABLE IF NOT EXISTS usdtwd_daily (
        date TEXT PRIMARY KEY, rate REAL, source TEXT);
    CREATE TABLE IF NOT EXISTS cpi_events (
        target_month TEXT PRIMARY KEY, release_date TEXT,
        actual_mom REAL, nowcast_mom REAL, surprise_pp REAL, source TEXT);
    CREATE TABLE IF NOT EXISTS ff_futures_daily (
        date TEXT, contract TEXT, settle REAL, source TEXT,
        PRIMARY KEY (date, contract));
    CREATE TABLE IF NOT EXISTS fed_expectations_daily (
        date TEXT PRIMARY KEY, next_meeting TEXT, pre_rate REAL,
        expected_post_rate REAL, expected_change_bp REAL,
        path_json TEXT, source TEXT);
    CREATE TABLE IF NOT EXISTS fomc_meetings (
        decision_date TEXT PRIMARY KEY, start_date TEXT, scheduled INTEGER,
        tgt_upper_before REAL, tgt_upper_after REAL, change_bp REAL,
        pre_expected_bp REAL, source TEXT);
    """)


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    ensure_tables(conn)
    return conn


# ── FRED 泛用 ─────────────────────────────────────────────────────────────────
def fetch_fred(sid: str, start: str, *, key: str | None = None,
               fetch=_http_json) -> list[tuple[str, float]]:
    """FRED 日/月頻觀測 [(date, value)];'.'(缺值)列剔除。"""
    key = key or _fred_key()
    url = FRED_OBS.format(sid=sid, key=key,
                          extra=f"&observation_start={start}&limit=100000")
    out = []
    for o in fetch(url).get("observations", []):
        if o.get("value") not in (".", "", None):
            try:
                out.append((o["date"], float(o["value"])))
            except ValueError:
                continue
    return out


# ── yfinance 泛用(日常尾端 + 回補)────────────────────────────────────────────
def _yf_history(symbol: str, start: str) -> list[tuple[str, float]]:
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf
    h = yf.Ticker(symbol).history(start=start, auto_adjust=False)
    return [(i.date().isoformat(), float(c))
            for i, c in zip(h.index, h["Close"]) if c == c]


# ── idx_daily(TAIEX + SPX + MA)───────────────────────────────────────────────
def _ma(closes: list[float], i: int, n: int):
    """closes[0..i] 含當日的 n 日均;窗口不足回 None(不冒充)。"""
    if i + 1 < n:
        return None
    return round(sum(closes[i - n + 1:i + 1]) / n, 2)


def upsert_index(db_path: str, market: str, rows: list[tuple[str, float]],
                 source: str) -> int:
    """rows=[(date, close)](已 sanity 過濾)。與庫內既有序列合併後重算 MA 並覆寫
    受影響列(新增尾端日只會影響其後的 MA,故重算「最早新日期之前 200 列」起)。"""
    if not rows:
        return 0
    conn = _conn(db_path)
    old = dict(conn.execute(
        "SELECT date, close FROM idx_daily WHERE market=?", (market,)).fetchall())
    src_old = dict(conn.execute(
        "SELECT date, source FROM idx_daily WHERE market=?", (market,)).fetchall())
    merged = dict(old)
    merged.update(dict(rows))
    dates = sorted(merged)
    closes = [merged[d] for d in dates]
    new_dates = {d for d, _ in rows if d not in old or old[d] != merged[d]}
    if not new_dates:
        conn.close()
        return 0
    first_new_i = min(dates.index(d) for d in new_dates)
    recompute_from = max(0, first_new_i - 200)
    out = []
    for i in range(recompute_from, len(dates)):
        d = dates[i]
        out.append((market, d, closes[i], _ma(closes, i, 20), _ma(closes, i, 60),
                    _ma(closes, i, 200), source if d in new_dates else
                    src_old.get(d, source)))
    conn.executemany("INSERT OR REPLACE INTO idx_daily VALUES (?,?,?,?,?,?,?)", out)
    conn.commit()
    conn.close()
    return len(new_dates)


def backfill_taiex(db_path: str = MACRO_DB, start_ym: str = "201206",
                   skip_existing: bool = False) -> int:
    """FMTQIK 月檔逐月。TWSE CDN 限流以 **307(無 Location)** 呈現(2026-07-08
    實測 ~20 請求後觸發)→ 每月最多重試 3 次、退避 45s;連 5 月耗盡重試即中止
    (限流窗未過,硬打只會更久)。skip_existing:已有 ≥15 列的月直接跳過(補跑用)。"""
    cur = datetime.now(TZ).replace(day=1)
    months = []
    while cur.strftime("%Y%m") >= start_ym:
        months.append(cur.strftime("%Y%m"))
        cur = (cur - timedelta(days=1)).replace(day=1)
    have_counts = {}
    if skip_existing:
        conn = _conn(db_path)
        have_counts = dict(conn.execute(
            "SELECT substr(date,1,4)||substr(date,6,2), COUNT(*) FROM idx_daily "
            "WHERE market='TAIEX' GROUP BY 1").fetchall())
        conn.close()
    total, consec_fail = 0, 0
    for ym in months:
        if skip_existing and have_counts.get(ym, 0) >= 15:
            continue
        rows = None
        for attempt in range(3):
            try:
                rows = [(d, c) for d, c, _ in taiex_daily.fetch_month(ym)
                        if _sane("TAIEX", c)]
                break
            except Exception as e:                 # noqa: BLE001 — 含 307 限流
                print(f"[signals] TAIEX {ym} 第{attempt + 1}次失敗:{str(e)[:50]}"
                      f"{',退避 45s' if attempt < 2 else ''}", file=sys.stderr)
                if attempt < 2:
                    time.sleep(45)
        if rows is None:
            consec_fail += 1
            if consec_fail >= 5:
                print(f"[signals] TAIEX 連 {consec_fail} 月重試耗盡,中止本輪"
                      "(限流窗未過,稍後以 --backfill-taiex 補跑)", file=sys.stderr)
                break
            continue
        consec_fail = 0
        total += upsert_index(db_path, "TAIEX", rows, "TWSE FMTQIK")
        print(f"[signals] TAIEX {ym}:{len(rows)} 日")
        time.sleep(2.0)
    return total


def daily_taiex(db_path: str = MACRO_DB) -> int:
    ym = datetime.now(TZ).strftime("%Y%m")
    rows = [(d, c) for d, c, _ in taiex_daily.fetch_month(ym) if _sane("TAIEX", c)]
    return upsert_index(db_path, "TAIEX", rows, "TWSE FMTQIK")


def daily_spx(db_path: str = MACRO_DB, start: str | None = None) -> int:
    start = start or (datetime.now(TZ) - timedelta(days=14)).strftime("%Y-%m-%d")
    rows = [(d, c) for d, c in _yf_history("^GSPC", start) if _sane("SPX", c)]
    return upsert_index(db_path, "SPX", rows, "yfinance ^GSPC")


# ── vix_daily ────────────────────────────────────────────────────────────────
def upsert_vix(db_path: str, start: str) -> int:
    vix = {d: c for d, c in _yf_history("^VIX", start) if _sane("VIX", c)}
    v3m = {d: c for d, c in _yf_history("^VIX3M", start) if _sane("VIX3M", c)}
    conn = _conn(db_path)
    rows = [(d, vix[d], v3m.get(d), "yfinance ^VIX/^VIX3M") for d in sorted(vix)]
    conn.executemany("INSERT OR REPLACE INTO vix_daily VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


# ── umich_monthly(ALFRED 全 vintage → 發布日)─────────────────────────────────
def fetch_umich_with_releases(start: str, *, key: str | None = None,
                              fetch=_http_json) -> list[tuple[str, float, str]]:
    """[(month, value, release_date)]。realtime 全域展開後,每個資料月取
    **最早 realtime_start** = 該月值首次公開日(反未來函數的生效日)。"""
    key = key or _fred_key()
    url = FRED_OBS.format(sid="UMCSENT", key=key,
                          extra=(f"&observation_start={start}"
                                 "&realtime_start=1998-07-31&realtime_end=9999-12-31"
                                 "&limit=100000"))
    first: dict[str, tuple[str, float]] = {}
    for o in fetch(url).get("observations", []):
        if o.get("value") in (".", "", None):
            continue
        m = o["date"][:7]
        rt = o["realtime_start"]
        if m not in first or rt < first[m][0]:
            first[m] = (rt, float(o["value"]))
    out = [(m, v, rt) for m, (rt, v) in sorted(first.items())
           if _sane("UMICH", v)]
    return out


def upsert_umich(db_path: str, rows: list[tuple[str, float, str]]) -> int:
    conn = _conn(db_path)
    conn.executemany(
        "INSERT OR REPLACE INTO umich_monthly VALUES (?,?,?,?)",
        [(m, v, rd, "FRED UMCSENT+ALFRED vintage") for m, v, rd in rows])
    conn.commit()
    conn.close()
    return len(rows)


# ── light_monthly(國發會燈號)────────────────────────────────────────────────
def _tw_holidays() -> set[str]:
    try:
        with open(TW_HOLIDAYS, encoding="utf-8") as f:
            return set(json.load(f).get("holidays", {}).keys())
    except OSError:
        return set()


def light_release_date(month: str, cfg: dict | None = None,
                       holidays: set[str] | None = None) -> str:
    """資料月 'YYYY-MM' → 發布日(次月 cfg.day 日,遇週末/已知假日順延次一營業日)。
    歷史年度無假日 config → 只避週末(拍板:±3 天誤差對月頻慢變數影響趨近零)。
    overrides 單點修正優先。"""
    cfg = cfg or _cfg()["light_release"]
    if month in cfg.get("overrides", {}):
        return cfg["overrides"][month]
    holidays = _tw_holidays() if holidays is None else holidays
    y, m = int(month[:4]), int(month[5:7])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    d = dt_date(y, m, int(cfg.get("day_of_next_month", 27)))
    while d.weekday() >= 5 or d.isoformat() in holidays:
        d += timedelta(days=1)
    return d.isoformat()


def fetch_light_rows(*, fetch_json=_http_json, fetch_bytes=_http_bytes
                     ) -> list[tuple[str, float, str]]:
    """data.gov.tw dataset API → zip URL(每月變動,動態取)→ [(month, score, light)]。
    燈色 strip(源檔「黃藍 」帶尾空白);分數 '-'(1984 前)列跳過。"""
    meta = fetch_json(NDC_DATASET)
    url = None
    for dist in meta["result"]["distribution"]:
        u = dist.get("resourceDownloadUrl") or dist.get("resourceAccessUrl")
        if u and dist.get("resourceFormat", "").upper() == "ZIP":
            url = u
            break
    if not url:
        raise RuntimeError("dataset 6099 無 ZIP distribution")
    import io
    z = zipfile.ZipFile(io.BytesIO(fetch_bytes(url)))
    target = None
    for info in z.infolist():
        try:
            fixed = info.filename.encode("cp437").decode("big5")
        except (UnicodeDecodeError, UnicodeEncodeError):
            fixed = info.filename
        if "景氣指標與燈號" in fixed and "schema" not in fixed:
            target = info
            break
    if target is None:
        raise RuntimeError("zip 內無 景氣指標與燈號.csv")
    txt = z.read(target).decode("utf-8-sig")
    lines = txt.splitlines()
    header = [h.strip().strip('"') for h in lines[0].split(",")]
    si = header.index("景氣對策信號綜合分數")
    li = header.index("景氣對策信號")
    out = []
    for line in lines[1:]:
        cells = [c.strip().strip('"') for c in line.split(",")]
        if len(cells) <= max(si, li) or not re.match(r"^\d{6}$", cells[0]):
            continue
        month = f"{cells[0][:4]}-{cells[0][4:6]}"
        light = cells[li].strip()
        try:
            score = float(cells[si])
        except ValueError:
            continue                                # '-'(1984 前無燈號)
        if _sane("LIGHT_SCORE", score) and light in ("藍", "黃藍", "綠", "黃紅", "紅"):
            out.append((month, score, light))
    return out


def upsert_light(db_path: str, rows: list[tuple[str, float, str]]) -> int:
    cfg = _cfg()["light_release"]
    holidays = _tw_holidays()
    conn = _conn(db_path)
    conn.executemany(
        "INSERT OR REPLACE INTO light_monthly VALUES (?,?,?,?,?)",
        [(m, s, l, light_release_date(m, cfg, holidays),
          "NDC dataset 6099(發布日=規則近似)") for m, s, l in rows])
    conn.commit()
    conn.close()
    return len(rows)


def light_needs_update(db_path: str) -> bool:
    """庫內最新資料月 < 「上上月」→ 該抓了(燈號次月底發布:7 月時應有 5 月)。"""
    conn = _conn(db_path)
    row = conn.execute("SELECT MAX(month) FROM light_monthly").fetchone()
    conn.close()
    now = datetime.now(TZ)
    y, m = now.year, now.month - 2
    if m <= 0:
        y, m = y - 1, m + 12
    return (row[0] or "") < f"{y:04d}-{m:02d}"


# ── dgs10_daily ──────────────────────────────────────────────────────────────
def upsert_dgs10(db_path: str, start: str, *, key: str | None = None) -> int:
    rows = [(d, v, "FRED DGS10") for d, v in fetch_fred("DGS10", start, key=key)
            if _sane("DGS10", v)]
    conn = _conn(db_path)
    conn.executemany("INSERT OR REPLACE INTO dgs10_daily VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


# ── usdtwd_daily(雙源:DEXTAUS 官方 + TWD=X 尾端暫代)─────────────────────────
def upsert_usdtwd_official(db_path: str, start: str, *, key: str | None = None) -> int:
    """DEXTAUS 覆寫窗口內所有列(含先前 TWD=X 暫代列 → 官方值到即轉正)。"""
    rows = [(d, v, "FRED DEXTAUS") for d, v in fetch_fred("DEXTAUS", start, key=key)
            if _sane("USDTWD", v)]
    conn = _conn(db_path)
    conn.executemany("INSERT OR REPLACE INTO usdtwd_daily VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


def upsert_usdtwd_provisional(db_path: str, start: str) -> int:
    """TWD=X 只補「官方尚無值」的日期(不覆蓋 DEXTAUS 列)。"""
    conn = _conn(db_path)
    have_official = {r[0] for r in conn.execute(
        "SELECT date FROM usdtwd_daily WHERE source LIKE 'FRED%'")}
    rows = [(d, v, "yfinance TWD=X(暫代)") for d, v in _yf_history("TWD=X", start)
            if _sane("USDTWD", v) and d not in have_official]
    conn.executemany("INSERT OR REPLACE INTO usdtwd_daily VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


# ── cpi_events(克里夫蘭 nowcast)─────────────────────────────────────────────
def _label_to_iso(label: str, target_year: int, target_month: int) -> str | None:
    """窗口內 'MM/DD' → ISO(年份由目標月推斷;窗口可跨 M-2..M+1)。"""
    m_ = re.match(r"^(\d{2})/(\d{2})$", label.strip())
    if not m_:
        return None
    mo, day = int(m_.group(1)), int(m_.group(2))
    delta = mo - target_month
    if delta > 6:
        delta -= 12
    elif delta < -6:
        delta += 12
    if not -4 <= delta <= 2:
        return None
    total = target_year * 12 + (target_month - 1) + delta
    y, mo2 = total // 12, total % 12 + 1
    try:
        return dt_date(y, mo2, day).isoformat()
    except ValueError:
        return None


_CPI_ABBR = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
             7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}


def parse_cpi_events(doc: list) -> list[tuple[str, str, float, float, float]]:
    """nowcast_month.json → [(target_month, release_date, actual, nowcast, surprise)]。
    發布日 = 目標月「CPI <月縮寫>」vline 的**前一個日期 label**——Actual 點畫在發布前
    最後交易日(圖面布局),不可當發布日,早 2 天會洩漏未來資訊
    (2026-07-08 對 BLS 官方 3 點驗證:2023-08-10/2022-07-13/2024-06-12 全中,153/153 月規則成立)。
    nowcast = 發布日前最後一筆 nowcast(會前視角);未發布月(無該 vline+Actual)跳過。"""
    out = []
    for entry in doc:
        sub = str(entry.get("chart", {}).get("subcaption", ""))
        m_ = re.match(r"^(\d{4})-(\d{1,2})$", sub)
        if not m_:
            continue
        ty, tm = int(m_.group(1)), int(m_.group(2))
        cats = entry.get("categories", [{}])[0].get("category", [])
        dates = [_label_to_iso(str(c.get("label", "")), ty, tm) for c in cats]
        real_dates = [d for d in dates if d]
        if real_dates != sorted(real_dates):
            print(f"[signals] cpi {sub} 窗口日期非遞增,丟棄", file=sys.stderr)
            continue
        series = {s.get("seriesname"): [x.get("value") for x in s.get("data", [])]
                  for s in entry.get("dataset", [])}
        actual_row = series.get("Actual CPI Inflation", [])
        now_row = series.get("CPI Inflation", [])
        actual = next((float(v) for v in actual_row if v not in (None, "")), None)
        want = f"CPI {_CPI_ABBR[tm]}"
        vline_i = next((i for i, c in enumerate(cats)
                        if c.get("vline") and str(c.get("label", "")).strip() == want),
                       None)
        if actual is None or vline_i is None:
            continue                               # 尚未發布(進行中的月)
        release = next((dates[j] for j in range(vline_i - 1, -1, -1) if dates[j]),
                       None)
        nowcast = None
        for i in range(min(vline_i, len(now_row)) - 1, -1, -1):
            v = now_row[i]
            if v not in (None, "") and dates[i] and dates[i] < release:
                nowcast = float(v)
                break
        if (release is None or nowcast is None
                or not (_sane("CPI_MOM", actual) and _sane("CPI_MOM", nowcast))):
            continue
        out.append((f"{ty:04d}-{tm:02d}", release, round(actual, 4),
                    round(nowcast, 4), round(actual - nowcast, 4)))
    return sorted(out)


def update_cpi_events(db_path: str = MACRO_DB, *, fetch_bytes=_http_bytes) -> int:
    doc = json.loads(fetch_bytes(CLEV_NOWCAST))
    rows = parse_cpi_events(doc)
    conn = _conn(db_path)
    conn.executemany(
        "INSERT OR REPLACE INTO cpi_events VALUES (?,?,?,?,?,?)",
        [r + ("Cleveland Fed nowcast archive",) for r in rows])
    conn.commit()
    conn.close()
    return len(rows)


# ── ff_futures_daily ─────────────────────────────────────────────────────────
_MONTH_CODE = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
               7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}


def backfill_zq_front(db_path: str = MACRO_DB, start: str = BACKFILL_START) -> int:
    """ZQ=F 連續近月:近月=該日曆月(spike 手驗 2022-06/2019-07 換月正確),
    contract 欄=該日所在 YYYYMM。"""
    rows = [(d, d[:4] + d[5:7], c, "yfinance ZQ=F(連續近月)")
            for d, c in _yf_history("ZQ=F", start) if _sane("ZQ", c)]
    conn = _conn(db_path)
    conn.executemany("INSERT OR REPLACE INTO ff_futures_daily VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


def daily_zq_contracts(db_path: str = MACRO_DB, months: int | None = None,
                       lookback_days: int = 7) -> int:
    """個別月份合約近 N 日結算(當月起 forward_months 個月;FedWatch 自算用)。"""
    months = months or int(_cfg()["ff_futures"]["forward_months"])
    start = (datetime.now(TZ) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    cur = datetime.now(TZ).replace(day=1)
    rows = []
    for _ in range(months):
        code = f"ZQ{_MONTH_CODE[cur.month]}{cur.strftime('%y')}.CBT"
        contract = cur.strftime("%Y%m")
        try:
            for d, c in _yf_history(code, start):
                if _sane("ZQ", c):
                    rows.append((d, contract, c, f"yfinance {code}"))
        except Exception as e:                     # noqa: BLE001
            print(f"[signals] {code} 例外:{str(e)[:50]}", file=sys.stderr)
        cur = (cur + timedelta(days=32)).replace(day=1)
    conn = _conn(db_path)
    # 個別合約優先於連續近月:同 (date,contract) 覆寫
    conn.executemany("INSERT OR REPLACE INTO ff_futures_daily VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


# ── fomc_meetings(官方行事曆 + DFEDTARU 交叉驗證)────────────────────────────
_FED_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}


def _fed_month(name: str) -> int | None:
    name = name.strip()
    if name in _FED_MONTHS:
        return _FED_MONTHS[name]
    for full, i in _FED_MONTHS.items():
        if full.startswith(name):                  # 縮寫(Jul/Aug)
            return i
    return None


def _fed_span(mon_raw: str, day_txt: str, year: int) -> tuple[str, str] | None:
    """('April/May', '30-1') → (start, decision);單日 → 兩者同日。跨年不會發生
    (FOMC 無 12→1 月跨月會議),故 mo2<mo1 即視為解析錯誤丟棄。"""
    mons = [m for m in (_fed_month(x) for x in mon_raw.split("/")) if m]
    if not mons:
        return None
    m_ = re.match(r"^(\d{1,2})(?:\s*-\s*(\d{1,2}))?$", day_txt)
    if not m_:
        return None
    d1 = int(m_.group(1))
    d2 = int(m_.group(2)) if m_.group(2) else d1
    mo1 = mons[0]
    mo2 = mons[-1] if (len(mons) > 1 and d2 < d1) else mo1
    if mo2 < mo1:
        return None
    try:
        return dt_date(year, mo1, d1).isoformat(), dt_date(year, mo2, d2).isoformat()
    except ValueError:
        return None


def parse_fed_historical(html: str) -> list[tuple[str, str, bool]]:
    """歷史頁 h5:'January 30-31 Meeting - 2018' / 'October 16 (unscheduled) - 2013'
    / 'March 2 (unscheduled) Meeting - 2020'。排除 (notation vote) 與 (cancelled)。"""
    out = []
    for h5 in re.findall(r"<h5[^>]*>([^<]+)</h5>", html):
        t = h5.strip()
        if "notation vote" in t.lower() or "cancelled" in t.lower():
            continue
        m_ = re.match(
            r"^([A-Za-z/]+)\s+(\d{1,2}(?:\s*-\s*\d{1,2})?)\s*"
            r"(\(unscheduled\))?\s*(?:Meeting)?\s*-\s*(\d{4})$", t)
        if not m_:
            continue
        span = _fed_span(m_.group(1), m_.group(2).replace(" ", ""), int(m_.group(4)))
        if span:
            out.append((span[0], span[1], m_.group(3) is None))
    return sorted(set(out))


def parse_fed_calendar(html: str) -> list[tuple[str, str, bool]]:
    """現行行事曆頁:以「YYYY FOMC Meetings」切年段,段內
    fomc-meeting__month(月名,可含 '/')+ fomc-meeting__date('DD-DD',可含 '*')。"""
    heads = [(m.start(), int(m.group(1)))
             for m in re.finditer(r"(20\d{2})\s+FOMC\s+Meetings", html)]
    out = []
    for i, (pos, year) in enumerate(heads):
        seg = html[pos:heads[i + 1][0]] if i + 1 < len(heads) else html[pos:]
        blocks = re.findall(
            r'fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([A-Za-z/]+)\s*'
            r'(?:</strong>)?\s*<.*?'
            r'fomc-meeting__date[^>]*>\s*(?:<strong>)?\s*([^<]+)',
            seg, re.S)
        for mon_raw, day_raw in blocks:
            unsched = "unscheduled" in day_raw.lower()
            if "cancelled" in day_raw.lower() or "notation" in day_raw.lower():
                continue
            day_txt = re.sub(r"\(.*?\)|\*", "", day_raw).strip().replace(" ", "")
            span = _fed_span(mon_raw, day_txt, year)
            if span:
                out.append((span[0], span[1], not unsched))
    return sorted(set(out))


def build_fomc_meetings(db_path: str = MACRO_DB, year_from: int = 2013,
                        *, key: str | None = None) -> int:
    """抓 2013→今官方會議日 + DFEDTARU 決策;交叉驗證:每次目標區間變動日
    都須落在某會議 decision_date 後 7 日內,否則丟例外(缺會議=回放作廢級錯誤)。"""
    this_year = datetime.now(TZ).year
    meetings: list[tuple[str, str, bool]] = []
    seen_years: set[int] = set()
    # 歷史頁(fomchistoricalYYYY,涵蓋約 5 年前以前;近年 404 由行事曆頁補)
    for y in range(year_from, this_year + 1):
        try:
            html = _http_bytes(FED_HIST.format(y=y)).decode("utf-8", "replace")
        except Exception:                          # noqa: BLE001 — 近年無歷史頁
            continue
        got = [m for m in parse_fed_historical(html) if m[0][:4] == str(y)]
        if got:
            meetings += got
            seen_years.add(y)
            print(f"[signals] FOMC {y}(historical):{len(got)} 次")
        time.sleep(0.4)
    # 現行行事曆頁(近 ~6 年,含未來)
    html = _http_bytes(FED_CAL).decode("utf-8", "replace")
    for start, dec, sched in parse_fed_calendar(html):
        y = int(start[:4])
        if (start, dec, sched) not in meetings and y >= year_from:
            meetings.append((start, dec, sched))
            seen_years.add(y)
    print(f"[signals] FOMC 行事曆頁合計 {len(meetings)} 次(含未來)")
    missing = [y for y in range(year_from, this_year + 1) if y not in seen_years]
    if missing:
        raise RuntimeError(f"FOMC 行事曆缺年度:{missing}(頁面版型變動?)")
    # DFEDTARU:決策 → 實際變動
    tar = fetch_fred("DFEDTARU", f"{year_from - 1}-12-01", key=key)
    tar_map = dict(tar)
    tar_dates = [d for d, _ in tar]

    def upper_at(iso: str):
        i = None
        for j, d in enumerate(tar_dates):
            if d <= iso:
                i = j
            else:
                break
        return tar[i][1] if i is not None else None

    rows = []
    for start, dec, sched in sorted(set(meetings)):
        if dec > datetime.now(TZ).strftime("%Y-%m-%d"):
            continue                                # 未來會議不入表(引擎只看歷史)
        # before 取決策日**前一日**:DFEDTARU 新值標記日不一致(2015/2016 標決策
        # 當日、2017 起標次日),前一日兩制皆為會前水準(會議間隔 ≥6 週,無污染)
        before = upper_at((dt_date.fromisoformat(dec) - timedelta(days=1)).isoformat())
        after_d = dt_date.fromisoformat(dec) + timedelta(days=7)
        after = upper_at(after_d.isoformat())
        change = (round((after - before) * 100)
                  if before is not None and after is not None else None)
        rows.append((dec, start, int(sched), before, after, change, None,
                     "federalreserve.gov + FRED DFEDTARU"))
    # 交叉驗證:每個 DFEDTARU 變動日須在某決策日後 7 日內
    dec_dates = [r[0] for r in rows]
    prev = None
    for d, v in tar:
        if prev is not None and v != prev and d >= f"{year_from}-01-01":
            ok = any(0 <= (dt_date.fromisoformat(d)
                           - dt_date.fromisoformat(dd)).days <= 7
                     for dd in dec_dates)
            if not ok:
                raise RuntimeError(f"DFEDTARU 變動 {d} 無對應 FOMC 會議(缺會議!)")
        prev = v
    conn = _conn(db_path)
    conn.executemany("INSERT OR REPLACE INTO fomc_meetings VALUES (?,?,?,?,?,?,?,?)",
                     rows)
    conn.commit()
    conn.close()
    return len(rows)


# ── 日常 / 回補 ───────────────────────────────────────────────────────────────
def run_daily(db_path: str = MACRO_DB) -> int:
    """19:00 主跑掛鉤(非阻斷)。逐項獨立 try,任一失敗記名 → exit 1(Discord 可見)。"""
    failed = []
    key = None
    try:
        key = _fred_key()
    except Exception as e:                         # noqa: BLE001
        print(f"[signals] FRED key 不可用:{e}", file=sys.stderr)
    recent_fred = (datetime.now(TZ) - timedelta(days=21)).strftime("%Y-%m-%d")
    recent_yf = (datetime.now(TZ) - timedelta(days=14)).strftime("%Y-%m-%d")
    steps = [
        ("idx TAIEX", lambda: daily_taiex(db_path)),
        ("idx SPX", lambda: daily_spx(db_path, recent_yf)),
        ("vix", lambda: upsert_vix(db_path, recent_yf)),
        ("dgs10", lambda: upsert_dgs10(db_path, recent_fred, key=key)),
        ("usdtwd 官方", lambda: upsert_usdtwd_official(db_path, recent_fred, key=key)),
        ("usdtwd 暫代", lambda: upsert_usdtwd_provisional(db_path, recent_yf)),
        ("ff 期貨", lambda: daily_zq_contracts(db_path)),
        ("umich", lambda: upsert_umich(
            db_path, fetch_umich_with_releases(recent_fred[:8] + "01", key=key))),
        ("cpi nowcast", lambda: update_cpi_events(db_path)),
    ]
    if light_needs_update(db_path):
        steps.append(("light 燈號", lambda: upsert_light(db_path, fetch_light_rows())))
    for name, fn in steps:
        try:
            n = fn()
            print(f"[signals] {name}:upsert {n}")
        except Exception as e:                     # noqa: BLE001
            failed.append(name)
            print(f"[signals] ❌ {name}:{str(e)[:80]}", file=sys.stderr)
    if failed:
        print(f"[signals] 失敗項:{','.join(failed)}", file=sys.stderr)
        return 1
    print("✅ fetch_signals 日更全數完成")
    return 0


def backfill_all(db_path: str = MACRO_DB) -> None:
    key = _fred_key()
    print("== [1/9] TAIEX(FMTQIK 月檔,~170 請求 × 0.8s)==")
    print(f"   {backfill_taiex(db_path)} 日")
    print("== [2/9] SPX(yfinance ^GSPC)==")
    rows = [(d, c) for d, c in _yf_history("^GSPC", BACKFILL_START) if _sane("SPX", c)]
    print(f"   {upsert_index(db_path, 'SPX', rows, 'yfinance ^GSPC')} 日")
    print("== [3/9] VIX + VIX3M ==")
    print(f"   {upsert_vix(db_path, BACKFILL_START)} 日")
    print("== [4/9] UMCSENT + 發布日(ALFRED)==")
    print(f"   {upsert_umich(db_path, fetch_umich_with_releases('2012-01-01', key=key))} 月")
    print("== [5/9] 景氣燈號(1982 起全量,便宜)==")
    print(f"   {upsert_light(db_path, fetch_light_rows())} 月")
    print("== [6/9] DGS10 ==")
    print(f"   {upsert_dgs10(db_path, BACKFILL_START, key=key)} 日")
    print("== [7/9] USDTWD(DEXTAUS 官方 + TWD=X 尾端)==")
    print(f"   官方 {upsert_usdtwd_official(db_path, BACKFILL_START, key=key)} 日 / "
          f"暫代 {upsert_usdtwd_provisional(db_path, (datetime.now(TZ) - timedelta(days=14)).strftime('%Y-%m-%d'))} 日")
    print("== [8/9] CPI events(克里夫蘭 nowcast 存檔)==")
    print(f"   {update_cpi_events(db_path)} 月")
    print("== [9/9] 聯邦基金期貨(ZQ=F 連續近月)+ 個別合約 + FOMC 會議 ==")
    print(f"   ZQ=F {backfill_zq_front(db_path)} 日 / "
          f"合約 {daily_zq_contracts(db_path, lookback_days=3650)} 列 / "
          f"FOMC {build_fomc_meetings(db_path, key=key)} 次")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=MACRO_DB)
    ap.add_argument("--backfill-all", action="store_true")
    ap.add_argument("--backfill-taiex", action="store_true",
                    help="TAIEX 缺月補跑(跳過已完整月;限流補救)")
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--fomc-only", action="store_true")
    args = ap.parse_args()
    if args.backfill_all:
        backfill_all(args.db)
        return 0
    if args.backfill_taiex:
        print(f"TAIEX 補跑:{backfill_taiex(args.db, skip_existing=True)} 日")
        return 0
    if args.fomc_only:
        print(f"FOMC:{build_fomc_meetings(args.db)} 次")
        return 0
    return run_daily(args.db)


if __name__ == "__main__":
    sys.exit(main())
