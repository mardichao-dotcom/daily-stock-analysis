"""
fetch_chips.py — 籌碼面資料(stage9 §3.5,進 19:00 主跑;不依賴 TV,健康檢查前先跑)

全市場官方端點過濾 watchlist,寫入 kline.db 的 chips / chips_holder 表。涵蓋:
  - 三大法人買賣超(外資/投信/自營,股數):上市 TWSE rwd T86、上櫃 TPEx dailyTrade(格式不同,分開解析)
  - 融資餘額(張):上市 TWSE rwd MI_MARGN、上櫃 TPEx margin/balance
  (2026-10-31 起四支皆用「帶日期 + 自報日期」端點:入庫日 = 來源自報日、交易日由來源判定、
   自動補洞——取代只給最新日的 openapi,見 TWSE_MARGIN_D 段註解)
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
from datetime import date, datetime, timezone, timedelta

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(PROJECT_ROOT))
from src.load_config import get_all_tw_symbols, symbol_to_code

TZ = timezone(timedelta(hours=8))
KLINE_DB = os.path.join(PROJECT_ROOT, "kline.db")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TWSE_T86 = ("https://www.twse.com.tw/rwd/zh/fund/T86"
            "?date={d}&selectType=ALLBUT0999&response=json")
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


# ── 帶日期端點(2026-10-31 停更事故後改)──────────────────────────────────────
# 原用 openapi 三支(TWSE MI_MARGN / TPEx 法人 / TPEx 融資)只給「最新日」,造成:
#   ① TV 停更兩週時籌碼跟著斷,事後補不回;
#   ② 19:00 主跑時融資 openapi 仍是前一交易日 → 兩市融資長期錯存成「次日」
#      (10/31 實測 10/08–10/15 上市 85/85、上櫃 21/21 皆為前一日值;TPEx 法人亦偶發落後)。
# 下列端點皆可指定日期且自報資料日 → 一律以「來源自報日」入庫,交易日由來源判定:
#   stat ok + 自報日 == 請求日 + 有資料列;否則視為非交易日/尚未發布(不寫、不冒充)。
# 10/30 實測與 openapi 106 檔 × 外資/投信/自營/融資 完全一致。
TWSE_MARGIN_D = ("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
                 "?date={d}&selectType=ALL&response=json")
TPEX_3I_D = ("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
             "?type=Daily&sect=EW&date={d}&response=json")
TPEX_MARGIN_D = "https://www.tpex.org.tw/www/zh-tw/margin/balance?date={d}&response=json"
TPEX_3I_D_FIELDS = 24          # 代號、名稱 + 7 組(買/賣/買賣超)+ 三大法人合計
CATCHUP_MAX_DAYS = 31          # 自動補洞最多回看幾個日曆日
SOURCE_GAP_SEC = 1.0           # 對官方站客氣(0.6s 連打曾被 TWSE 307 限流)


def _slash(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"


def _dated_table(j: dict, yyyymmdd: str) -> dict | None:
    """來源判定:stat ok + 自報日 == 請求日 + 有資料列 → 個股表;否則 None(非交易日/未發布)。"""
    if str(j.get("stat", "")).lower() != "ok" or str(j.get("date", "")) != yyyymmdd:
        return None
    for t in j.get("tables") or []:
        if (t.get("fields") or [""])[0] == "代號" and t.get("data"):
            return t
    return None


def fetch_twse_t86_on(yyyymmdd: str) -> dict | None:
    """上市三大法人(指定日);非交易日/未發布 → None。"""
    try:
        dd, m = fetch_twse_t86(yyyymmdd)
    except RuntimeError as e:
        if str(e).startswith("T86 stat="):
            return None
        raise
    return m if dd == _iso_ad(yyyymmdd) and m else None


def fetch_twse_margin_on(yyyymmdd: str) -> dict | None:
    """上市融資今日餘額(張,指定日);MI_MARGN 第一個「今日餘額」欄是融資(融券在後)。"""
    t = _dated_table(json.loads(_http(TWSE_MARGIN_D.format(d=yyyymmdd))), yyyymmdd)
    if t is None:
        return None
    i = t["fields"].index("今日餘額")
    return {str(r[0]).strip(): _int(r[i]) for r in t["data"]}


def fetch_tpex_3insti_on(yyyymmdd: str) -> dict | None:
    """上櫃三大法人(指定日)。7 組欄位(買/賣/買賣超):外資不含自營[2:5]、外資自營[5:8]、
    外資合計[8:11]、投信[11:14]、自營自行[14:17]、自營避險[17:20]、自營合計[20:23]。
    外資取「不含外資自營商」(對齊上市 T86 與舊 openapi),自營取合計。"""
    t = _dated_table(json.loads(_http(TPEX_3I_D.format(d=_slash(yyyymmdd)))), yyyymmdd)
    if t is None:
        return None
    if len(t["fields"]) != TPEX_3I_D_FIELDS:
        raise RuntimeError(f"TPEx dailyTrade 欄位數 {len(t['fields'])} ≠ {TPEX_3I_D_FIELDS}(格式變了)")
    return {str(r[0]).strip(): {"foreign": _int(r[4]), "trust": _int(r[13]), "dealer": _int(r[22])}
            for r in t["data"]}


def fetch_tpex_margin_on(yyyymmdd: str) -> dict | None:
    """上櫃融資餘額(張,指定日)。"""
    t = _dated_table(json.loads(_http(TPEX_MARGIN_D.format(d=_slash(yyyymmdd)))), yyyymmdd)
    if t is None:
        return None
    i = t["fields"].index("資餘額")
    return {str(r[0]).strip(): _int(r[i]) for r in t["data"]}


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
    """抓「指定日」籌碼 → 寫 chips(全走帶日期端點,入庫日 = 來源自報日)。

    來源判定非交易日/尚未發布 → 該項略過不寫;既有非空值不被 NULL 覆蓋
    (盤後分批發布:19:00 時融資常未出,下一輪 run_auto 補抓填上)。
    當日為交易日且週五(或 with_tdcc)另抓 TDCC → chips_holder。"""
    ymd = date_iso.replace("-", "")
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
        finally:
            time.sleep(SOURCE_GAP_SEC)

    tw3 = guarded(lambda: fetch_twse_t86_on(ymd), "twse_t86")
    twm = guarded(lambda: fetch_twse_margin_on(ymd), "twse_margin")
    otc3 = guarded(lambda: fetch_tpex_3insti_on(ymd), "tpex_3insti")
    otcm = guarded(lambda: fetch_tpex_margin_on(ymd), "tpex_margin")
    sources = {"twse_t86": tw3 is not None, "twse_margin": twm is not None,
               "tpex_3insti": otc3 is not None, "tpex_margin": otcm is not None}

    rows = []
    for code, sym in {**listed, **otc}.items():
        is_listed = sym.startswith("TWSE:")
        rec = ((tw3 if is_listed else otc3) or {}).get(code)
        mg = ((twm if is_listed else otcm) or {}).get(code)
        if rec is None and mg is None:
            continue
        rec = rec or {}
        rows.append((sym, date_iso, rec.get("foreign"), rec.get("trust"),
                     rec.get("dealer"), mg))

    conn = sqlite3.connect(db_path)
    _ensure_tables(conn)
    conn.executemany(
        "INSERT INTO chips (symbol,date,foreign_net,trust_net,dealer_net,margin_balance) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET "
        "foreign_net=COALESCE(excluded.foreign_net, chips.foreign_net), "
        "trust_net=COALESCE(excluded.trust_net, chips.trust_net), "
        "dealer_net=COALESCE(excluded.dealer_net, chips.dealer_net), "
        "margin_balance=COALESCE(excluded.margin_balance, chips.margin_balance)", rows)
    conn.commit()
    conn.close()

    trading = any(sources.values())
    weekday = datetime.strptime(date_iso, "%Y-%m-%d").weekday()   # 4 = Fri
    do_tdcc = with_tdcc if with_tdcc is not None else (trading and weekday == 4)
    holder_n = _run_tdcc(db_path, date_iso, listed, otc, errors) if do_tdcc else 0

    return {"date": date_iso, "trading": trading, "sources": sources,
            "chips_rows": len(rows), "holder_rows": holder_n,
            "listed": len(listed), "otc": len(otc), "errors": errors, "tdcc_ran": do_tdcc}


def _run_tdcc(db_path, date_iso, listed, otc, errors) -> int:
    """TDCC 千張大戶(只給最新週;入庫日 = TDCC 自報日)。"""
    try:
        hd_date, hd_map = fetch_tdcc_large_holder()
    except Exception as e:                     # noqa: BLE001
        errors.append(f"tdcc: {str(e)[:70]}")
        return 0
    hrows = [(sym, hd_date or date_iso, hd_map[code])
             for code, sym in {**listed, **otc}.items() if code in hd_map]
    conn = sqlite3.connect(db_path)
    _ensure_tables(conn)
    conn.executemany(
        "INSERT OR REPLACE INTO chips_holder (symbol,date,ratio) VALUES (?,?,?)", hrows)
    conn.commit()
    conn.close()
    return len(hrows)


def _complete_dates(db_path: str, since: str) -> set[str]:
    """since 之後「法人 + 融資皆齊」的日期(≥半數列兩者皆非空)。"""
    conn = sqlite3.connect(db_path)
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT date FROM chips WHERE date >= ? GROUP BY date HAVING "
        "SUM(foreign_net IS NOT NULL AND margin_balance IS NOT NULL) * 2 >= COUNT(*)",
        (since,)).fetchall()
    conn.close()
    return {r[0] for r in rows}


def _days_to_fetch(db_path: str, today: date) -> list[date]:
    """回看窗(CATCHUP_MAX_DAYS)內要問來源的日子:
      ① 最後齊全日之後的每一天(含週末——是否交易由來源判定,不自己推算);
      ② 窗內不齊全的平日(含有列但缺欄、整日無列):盤後未發布/停更/中途失敗留下的洞。
    平日只用來挑「候選」,國定假日/颱風假也會被問,來源回無交易即略過(每輪多幾個請求)。"""
    start = today - timedelta(days=CATCHUP_MAX_DAYS)
    done = _complete_dates(db_path, start.isoformat())
    last = max(done) if done else None
    days, d = [], start
    while d <= today:
        after_last = last is None or d.isoformat() > last
        if d.isoformat() not in done and (after_last or d.weekday() < 5):
            days.append(d)
        d += timedelta(days=1)
    return days


def run_auto(db_path: str = KLINE_DB, *, with_tdcc: bool | None = None,
             today: date | None = None) -> list[dict]:
    """自動模式(主跑用;不依賴 TV / DATA_DATE):逐日問來源(_days_to_fetch),有交易才寫。
    停更缺口、盤後未發布的欄位、中途失敗的洞都會在後續輪次自動補齊。
    TDCC 只在最後一個交易日為週五(或 with_tdcc)時抓一次。"""
    today = today or datetime.now(TZ).date()
    outs = []
    for d in _days_to_fetch(db_path, today):
        out = run(d.isoformat(), db_path, with_tdcc=False)
        if out["trading"] or out["errors"]:
            outs.append(out)
    traded = [o for o in outs if o["trading"]]
    if traded:
        latest = traded[-1]
        is_fri = date.fromisoformat(latest["date"]).weekday() == 4
        if with_tdcc if with_tdcc is not None else is_fri:
            symbols = get_all_tw_symbols()
            latest["holder_rows"] = _run_tdcc(
                db_path, latest["date"],
                {symbol_to_code(s): s for s in symbols if s.startswith("TWSE:")},
                {symbol_to_code(s): s for s in symbols if s.startswith("TPEX:")},
                latest["errors"])
            latest["tdcc_ran"] = True
    return outs


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


def _fmt(out: dict) -> str:
    src = "".join("✓" if v else "·" for v in out["sources"].values())
    return (f"chips {out['date']}:{out['chips_rows']} 檔(上市 {out['listed']}/上櫃 {out['otc']},"
            f"來源 T86/上市融資/上櫃法人/上櫃融資={src})"
            f" | 大戶 {out['holder_rows']} 檔(TDCC {'跑' if out['tdcc_ran'] else '略'})"
            f" | 失敗 {len(out['errors'])}" + (f" {out['errors']}" if out["errors"] else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="籌碼面抓取(§3.5)")
    ap.add_argument("--date", default=None,
                    help="只抓指定日 YYYY-MM-DD;省略 = 自動模式(入庫日 = 來源自報日,自動補洞)")
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
    outs = ([run(args.date, args.db, with_tdcc=with_tdcc)] if args.date
            else run_auto(args.db, with_tdcc=with_tdcc))
    for out in outs:
        print(("✅ " if out["trading"] else "⚠️ ") + _fmt(out))
    traded = [o["date"] for o in outs if o["trading"]]
    print(f"✅ chips 完成:寫入交易日 {traded or '無(非交易日/尚未發布/已是最新)'}")
    return 0        # 部分失敗不阻斷管線(N/A 護欄);errors 已逐日印出


if __name__ == "__main__":
    sys.exit(main())
