"""
fetch_chips.py — 籌碼面資料(stage9 §3.5,進 19:00 主跑)

全市場 OpenAPI 過濾 watchlist,寫入 kline.db 的 chips / chips_holder 表。涵蓋:
  - 三大法人買賣超(外資/投信/自營,股數):上市 TWSE rwd T86、上櫃 TPEx openapi(格式不同,分開解析)
  - 融資餘額(張):上市 MI_MARGN、上櫃 tpex_mainboard_margin_balance
  - 千張大戶比(%,每週五):TDCC 集保股權分散表 持股分級 15(>1000 張)占比

準確性優先:任一來源失敗 → 該市場該項略過 + errors 記錄,不用舊值冒充(N/A 護欄)。
不進積分、不進排行,純顯示。上市數字驗收對證交所官網 T86(同源)。
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(PROJECT_ROOT))
from src.load_config import get_all_tw_symbols, symbol_to_code

TZ = timezone(timedelta(hours=8))
KLINE_DB = os.path.join(PROJECT_ROOT, "kline.db")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TWSE_T86 = ("https://www.twse.com.tw/rwd/zh/fund/T86"
            "?date={d}&selectType=ALLBUT0999&response=json")
TWSE_MARGIN = "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"
TPEX_3I = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
TPEX_MARGIN = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"
TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
LARGE_HOLDER_LEVEL = "15"          # TDCC 持股分級 15 = 1,000,001 股以上(>1000 張)


# ── 工具 ──────────────────────────────────────────────────────────────────────
def _http(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _int(s) -> int | None:
    """'47,477,669' / '-3,892,544' / '98755' / '' / '--' → int|None。"""
    if s is None:
        return None
    t = str(s).replace(",", "").replace(" ", "").strip()
    if t in ("", "--", "-", "N/A"):
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def _iso_ad(yyyymmdd: str) -> str:
    s = str(yyyymmdd).strip()
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _iso_roc(roc: str) -> str:
    """'1150703' → '2026-07-03'(民國前 3 碼 + MMDD)。"""
    s = str(roc).strip()
    y = int(s[:-4]) + 1911
    return f"{y:04d}-{s[-4:-2]}-{s[-2:]}"


# ── 三大法人:上市(TWSE rwd T86,證交所官網同源)─────────────────────────────
def fetch_twse_t86(yyyymmdd: str) -> tuple[str, dict]:
    j = json.loads(_http(TWSE_T86.format(d=yyyymmdd)))
    if j.get("stat") != "OK":
        raise RuntimeError(f"T86 stat={j.get('stat')}")
    fields = j["fields"]

    def col(name: str) -> int:
        # 先精確匹配(避免「自營商買賣超股數」誤中「外資自營商買賣超股數」),再退回子字串
        for i, f in enumerate(fields):
            if f.strip() == name:
                return i
        for i, f in enumerate(fields):
            if name in f:
                return i
        raise RuntimeError(f"T86 欄位缺 {name}(fields={fields})")

    ci = col("證券代號")
    fi = col("外陸資買賣超股數(不含外資自營商)")
    ti = col("投信買賣超股數")
    di = col("自營商買賣超股數")       # 精確欄:自營商合計(≠ 外資自營商)
    out = {}
    for row in j["data"]:
        code = str(row[ci]).strip()
        out[code] = {"foreign": _int(row[fi]), "trust": _int(row[ti]), "dealer": _int(row[di])}
    return _iso_ad(j.get("date", yyyymmdd)), out


# ── 三大法人:上櫃(TPEx openapi,英文 key、民國日期、無逗號)────────────────────
def fetch_tpex_3insti() -> tuple[str, dict]:
    arr = json.loads(_http(TPEX_3I))
    if not isinstance(arr, list) or not arr:
        raise RuntimeError("TPEx 3insti 空回應")

    def norm(k: str) -> str:
        return "".join(str(k).split()).lower()

    F = norm("Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference")
    T = norm("SecuritiesInvestmentTrustCompanies-Difference")
    D = norm("Dealers-Difference")

    def pick(row: dict, target: str):
        for k, v in row.items():
            if norm(k) == target:
                return v
        return None

    out, dd = {}, None
    for r in arr:
        code = str(r.get("SecuritiesCompanyCode", "")).strip()
        if not code:
            continue
        out[code] = {"foreign": _int(pick(r, F)), "trust": _int(pick(r, T)),
                     "dealer": _int(pick(r, D))}
        if dd is None and r.get("Date"):
            dd = _iso_roc(r["Date"])
    return dd or "", out


# ── 融資餘額(張):上市 / 上櫃 ────────────────────────────────────────────────
def fetch_twse_margin() -> dict:
    arr = json.loads(_http(TWSE_MARGIN))
    return {str(r["股票代號"]).strip(): _int(r.get("融資今日餘額"))
            for r in arr if r.get("股票代號")}


def fetch_tpex_margin() -> dict:
    arr = json.loads(_http(TPEX_MARGIN))
    return {str(r["SecuritiesCompanyCode"]).strip(): _int(r.get("MarginPurchaseBalance"))
            for r in arr if r.get("SecuritiesCompanyCode")}


# ── 千張大戶比(%):TDCC 每週五 ───────────────────────────────────────────────
def fetch_tdcc_large_holder() -> tuple[str, dict]:
    raw = _http(TDCC_URL, timeout=60).decode("utf-8-sig", "replace")
    reader = csv.reader(io.StringIO(raw))
    next(reader, None)                         # header
    out, dd = {}, None
    for row in reader:
        if len(row) < 6:
            continue
        date, code, level, _people, _shares, ratio = row[:6]
        if str(level).strip() == LARGE_HOLDER_LEVEL:
            try:
                out[str(code).strip()] = float(ratio)
            except ValueError:
                continue
            if dd is None:
                dd = _iso_ad(date)
    return dd or "", out


# ── DB ────────────────────────────────────────────────────────────────────────
def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS chips ("
                 "symbol TEXT NOT NULL, date TEXT NOT NULL, "
                 "foreign_net INTEGER, trust_net INTEGER, dealer_net INTEGER, "
                 "margin_balance INTEGER, "
                 "PRIMARY KEY (symbol, date))")
    conn.execute("CREATE TABLE IF NOT EXISTS chips_holder ("
                 "symbol TEXT NOT NULL, date TEXT NOT NULL, ratio REAL, "
                 "PRIMARY KEY (symbol, date))")


def run(date_iso: str, db_path: str = KLINE_DB, *, with_tdcc: bool | None = None) -> dict:
    """抓當日籌碼 → 寫 chips;週五(或 with_tdcc)另抓 TDCC → chips_holder。"""
    yyyymmdd = date_iso.replace("-", "")
    symbols = get_all_tw_symbols()
    listed = {symbol_to_code(s): s for s in symbols if s.startswith("TWSE:")}
    otc = {symbol_to_code(s): s for s in symbols if s.startswith("TPEX:")}
    errors: list[str] = []

    def guarded(fn, label):
        try:
            return fn()
        except Exception as e:                 # noqa: BLE001 — 任何來源失敗都標記,不冒充
            errors.append(f"{label}: {str(e)[:70]}")
            return None

    tw3 = guarded(lambda: fetch_twse_t86(yyyymmdd), "twse_t86")
    otc3 = guarded(fetch_tpex_3insti, "tpex_3insti")
    twm = guarded(fetch_twse_margin, "twse_margin")
    otcm = guarded(fetch_tpex_margin, "tpex_margin")

    tw3_date, tw3_map = (tw3 if tw3 else ("", {}))
    otc3_date, otc3_map = (otc3 if otc3 else ("", {}))
    twm_map = twm or {}
    otcm_map = otcm or {}

    # 日期一致性檢查(來源自報日 vs 管線 DATA_DATE)
    for lbl, d in (("twse_t86", tw3_date), ("tpex_3insti", otc3_date)):
        if d and d != date_iso:
            print(f"[fetch_chips] ⚠️ {lbl} 自報日 {d} ≠ DATA_DATE {date_iso},仍以 DATA_DATE 入庫")

    rows = []
    for code, sym in {**listed, **otc}.items():
        is_listed = sym.startswith("TWSE:")
        rec = (tw3_map if is_listed else otc3_map).get(code)
        mg = (twm_map if is_listed else otcm_map).get(code)
        if rec is None and mg is None:
            continue
        rec = rec or {}
        rows.append((sym, date_iso, rec.get("foreign"), rec.get("trust"),
                     rec.get("dealer"), mg))

    conn = sqlite3.connect(db_path)
    _ensure_tables(conn)
    conn.executemany(
        "INSERT OR REPLACE INTO chips "
        "(symbol,date,foreign_net,trust_net,dealer_net,margin_balance) VALUES (?,?,?,?,?,?)",
        rows)
    conn.commit()

    # TDCC 千張大戶(每週五抓;date_iso 週五 or with_tdcc=True)
    holder_n = 0
    weekday = datetime.strptime(date_iso, "%Y-%m-%d").weekday()   # 4 = Fri
    do_tdcc = with_tdcc if with_tdcc is not None else (weekday == 4)
    if do_tdcc:
        hd = guarded(fetch_tdcc_large_holder, "tdcc")
        if hd:
            hd_date, hd_map = hd
            hrows = [(sym, hd_date or date_iso, hd_map[code])
                     for code, sym in {**listed, **otc}.items() if code in hd_map]
            conn.executemany(
                "INSERT OR REPLACE INTO chips_holder (symbol,date,ratio) VALUES (?,?,?)", hrows)
            conn.commit()
            holder_n = len(hrows)
    conn.close()

    return {"date": date_iso, "chips_rows": len(rows), "holder_rows": holder_n,
            "listed": len(listed), "otc": len(otc), "errors": errors,
            "tdcc_ran": do_tdcc}


# ── 上市歷史回補(T86 rwd 支援 date 參數;上櫃 openapi 只有最新日,無法回補)──────
def backfill_listed(db_path: str = KLINE_DB, trading_days: int = 20) -> int:
    """回補上市近 N 個交易日 T86(讓首版直方圖不空);跳過非交易日。"""
    symbols = {symbol_to_code(s): s for s in get_all_tw_symbols() if s.startswith("TWSE:")}
    conn = sqlite3.connect(db_path)
    _ensure_tables(conn)
    got, cur = 0, datetime.now(TZ)
    tried = 0
    while got < trading_days and tried < trading_days + 20:
        tried += 1
        cur -= timedelta(days=1)
        ymd = cur.strftime("%Y%m%d")
        try:
            dd, m = fetch_twse_t86(ymd)
        except Exception:                       # noqa: BLE001 — 非交易日 stat!=OK
            continue
        iso = _iso_ad(ymd)
        rows = [(sym, iso, m[c].get("foreign"), m[c].get("trust"), m[c].get("dealer"), None)
                for c, sym in symbols.items() if c in m]
        # 只補三大法人,不覆蓋既有 margin(用 COALESCE 保留)
        for sym, d, f, t, de, _ in rows:
            conn.execute(
                "INSERT INTO chips (symbol,date,foreign_net,trust_net,dealer_net,margin_balance) "
                "VALUES (?,?,?,?,?,NULL) ON CONFLICT(symbol,date) DO UPDATE SET "
                "foreign_net=excluded.foreign_net, trust_net=excluded.trust_net, "
                "dealer_net=excluded.dealer_net", (sym, d, f, t, de))
        conn.commit()
        got += 1
        time.sleep(0.6)                         # 對 twse rwd 客氣
        print(f"[backfill] {iso} 上市 {len(rows)} 檔")
    conn.close()
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description="籌碼面抓取(§3.5)")
    ap.add_argument("--date", required=True, help="DATA_DATE,YYYY-MM-DD")
    ap.add_argument("--db", default=KLINE_DB)
    ap.add_argument("--tdcc", dest="tdcc", action="store_true", help="強制抓 TDCC(不限週五)")
    ap.add_argument("--no-tdcc", dest="no_tdcc", action="store_true")
    ap.add_argument("--backfill-listed", type=int, default=0, metavar="N",
                    help="回補上市近 N 交易日 T86(首次建置用)")
    args = ap.parse_args()

    if args.backfill_listed:
        n = backfill_listed(args.db, args.backfill_listed)
        print(f"✅ 回補上市 {n} 個交易日")

    with_tdcc = True if args.tdcc else (False if args.no_tdcc else None)
    out = run(args.date, args.db, with_tdcc=with_tdcc)
    print(f"✅ chips {out['date']}:{out['chips_rows']} 檔(上市 {out['listed']}/上櫃 {out['otc']})"
          f" | 大戶 {out['holder_rows']} 檔(TDCC {'跑' if out['tdcc_ran'] else '略'})"
          f" | 失敗 {len(out['errors'])}" + (f" {out['errors']}" if out['errors'] else ""))
    return 0 if not out["errors"] else 0        # 部分失敗不阻斷管線(N/A 護欄)


if __name__ == "__main__":
    sys.exit(main())
