"""
fed_expectations.py — 聯邦基金期貨自算 FedWatch(stage12 spec §2.1 衍生表,2026-07-08)

CME FedWatch 標準演算法(單一會議、月內兩段):
  會議月 N 天、決策日 = 第 k 日(升降息生效日 = k+1 起):
    月均隱含 R_avg = 100 − P(會議月合約)
    R_avg = (k/N)·r_pre + ((N−k)/N)·r_post
    → r_post = (N·R_avg − k·r_pre) / (N − k)
  期望變動 bp = (r_post − r_pre)·100;r_pre = 會前 EFFR(FRED DFF 最近可得值)。
  機率(CME 兩檔模型):期望變動落在相鄰 25bp 檔位間線性分配。

回放與日常共用同一演算法(spec §0 紅線);差異只在資料源:
  日常:個別月份合約(當月起 13 個月)→ 全曲線 path + 下次會議機率
  回放:ZQ=F 連續近月(會議月=近月)→ 只有「會議月內」可算會前期望;
       非會議月鎖定上次會議後值(2026-07-08 拍板方案 a,回放報告須標注
       「非會議月為鎖定值,與日常端逐日更新行為不同」)

已知限制:同月兩次會議(2020-03:3/2 + 3/15)兩段模型失效 → 該月 NULL,
引擎退用 DFEDTARU 實際變動(surprise 層不受影響,decision 直接可得)。

驗收(spec §6):對 CME FedWatch 網頁 3 個會議 ≤5pp;對亞特蘭大聯儲 MPT
方向一致(2026-07-08 拍板採納,寫進日常驗收)。
歷史抽驗(spike 2026-07-07):2022-06-14 會前 +72bp(WSJ 洩露 75bp)、
2024-09-17 −39bp(五五波定價)——演算法已中兩點。
"""
from __future__ import annotations
import argparse
import calendar
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta, date as dt_date

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

TZ = timezone(timedelta(hours=8))
MACRO_DB = os.path.join(PROJECT_ROOT, "macro.db")


def _conn_ro(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


# ── 基礎查詢 ─────────────────────────────────────────────────────────────────
def load_meetings(db_path: str) -> list[dict]:
    conn = _conn_ro(db_path)
    rows = conn.execute(
        "SELECT decision_date, start_date, scheduled, tgt_upper_before,"
        " tgt_upper_after, change_bp FROM fomc_meetings ORDER BY decision_date"
    ).fetchall()
    conn.close()
    return [{"decision": r[0], "start": r[1], "scheduled": bool(r[2]),
             "before": r[3], "after": r[4], "change_bp": r[5]} for r in rows]


def next_meeting(meetings: list[dict], date_iso: str) -> dict | None:
    for m in meetings:
        if m["decision"] > date_iso:
            return m
    return None


def _dff_at(conn, date_iso: str) -> float | None:
    """DFF(實際 EFFR)在 date 當日或之前最近值。表:dgs10 同構的… DFF 未入庫,
    改用 fomc 目標上緣 − 0.07 近似?→ 否:直接查 FRED 已回補的…
    ——DFF 存於 ff_dff 快取表(本模組 ensure_dff 回補)。"""
    row = conn.execute("SELECT rate FROM dff_daily WHERE date <= ? "
                       "ORDER BY date DESC LIMIT 1", (date_iso,)).fetchone()
    return row[0] if row else None


def ensure_dff(db_path: str, start: str = "2012-06-01") -> int:
    """DFF(EFFR)日序列快取表(r_pre 用;冪等)。"""
    from src.fetch_signals import fetch_fred
    rows = [(d, v, "FRED DFF") for d, v in fetch_fred("DFF", start)
            if 0 <= v <= 20]
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS dff_daily ("
                 "date TEXT PRIMARY KEY, rate REAL, source TEXT)")
    conn.executemany("INSERT OR REPLACE INTO dff_daily VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


def settle_at(conn, contract: str, date_iso: str, max_back: int = 5) -> float | None:
    """合約在 date(含往前 max_back 個日曆日內最近)的結算價。"""
    row = conn.execute(
        "SELECT settle FROM ff_futures_daily WHERE contract=? AND date<=? AND date>=? "
        "ORDER BY date DESC LIMIT 1",
        (contract, date_iso,
         (dt_date.fromisoformat(date_iso) - timedelta(days=max_back)).isoformat())
    ).fetchone()
    return row[0] if row else None


# ── 核心演算法 ────────────────────────────────────────────────────────────────
def implied_post_rate(avg_rate: float, r_pre: float, decision_iso: str) -> float | None:
    """月均隱含 → 會後利率(兩段模型)。決策日=當月第 k 日,生效 k+1。
    月底會議(N−k < 3)槓桿過大 → 回 None(CME 同樣以次月合約處理,回放無次月)。"""
    d = dt_date.fromisoformat(decision_iso)
    n = calendar.monthrange(d.year, d.month)[1]
    k = d.day
    if n - k < 3:
        return None
    return (n * avg_rate - k * r_pre) / (n - k)


def two_bucket_prob(expected_change_bp: float) -> dict:
    """CME 兩檔模型:期望變動 → 相鄰 25bp 檔位機率。"""
    import math
    lower = math.floor(expected_change_bp / 25) * 25
    upper = lower + 25
    p_upper = (expected_change_bp - lower) / 25
    return {"buckets": {f"{int(lower):+d}bp": round(1 - p_upper, 3),
                        f"{int(upper):+d}bp": round(p_upper, 3)},
            "expected_bp": round(expected_change_bp, 1)}


def expectation_for(db_path: str, date_iso: str, meetings: list[dict] | None = None
                    ) -> dict | None:
    """date 當日視角的「下次會議」期望(反未來函數:只用 ≤date 的結算與 DFF)。
    會議月合約無值(回放非會議月)→ None(引擎鎖定上次值,方案 a)。"""
    conn = _conn_ro(db_path)
    try:
        meetings = meetings or load_meetings(db_path)
        m = next_meeting(meetings, date_iso)
        if m is None:
            return None
        dec = m["decision"]
        # 同月兩會議 → 模型失效(2020-03)
        same_month = [x for x in meetings if x["decision"][:7] == dec[:7]]
        if len(same_month) > 1:
            return {"date": date_iso, "next_meeting": dec, "degraded": "同月兩會議"}
        contract = dec[:4] + dec[5:7]
        p = settle_at(conn, contract, date_iso)
        r_pre = _dff_at(conn, date_iso)
        if p is None or r_pre is None:
            return None
        r_post = implied_post_rate(100 - p, r_pre, dec)
        if r_post is None:
            # 月底會議(N−k<3,兩段模型槓桿過大)→ CME 同法:次月合約整月
            # 皆為會後 → r_post ≈ 次月隱含月均(次月無會議時成立;FOMC 連續
            # 兩月開會僅 2020-03 特例,已在上方 degraded 分支擋掉)。
            # 回放端(僅近月連續)無次月合約 → 自然回 None,引擎鎖定上次值
            # + 該輪 surprise 不判定(誠實 N/A,受影響的變動會議僅
            # 2019-07-31 / 2019-10-30,皆為市場高度預期的 −25bp)。
            d_ = dt_date.fromisoformat(dec)
            nxt = (d_.replace(day=1) + timedelta(days=32)).replace(day=1)
            p2 = settle_at(conn, nxt.strftime("%Y%m"), date_iso)
            if p2 is None:
                return None
            r_post = 100 - p2
        chg = (r_post - r_pre) * 100
        out = {"date": date_iso, "next_meeting": dec, "pre_rate": round(r_pre, 3),
               "expected_post_rate": round(r_post, 3), **two_bucket_prob(chg)}
        return out
    finally:
        conn.close()


def meeting_tree(db_path: str, date_iso: str, n_meetings: int = 3) -> list[dict]:
    """未來 n 次會議的逐會議期望與**非條件**檔位分布(CME FedWatch 網頁同構,
    驗收 §6 用;日常端才有全曲線)。逐會議兩檔 + 獨立步進捲積。"""
    meetings = load_meetings(db_path)
    conn = _conn_ro(db_path)
    try:
        upcoming = [m for m in meetings if m["decision"] > date_iso][:n_meetings]
        r_pre0 = _dff_at(conn, date_iso)
        if r_pre0 is None or not upcoming:
            return []
        steps, r_prev = [], r_pre0
        for m in upcoming:
            dec = m["decision"]
            p = settle_at(conn, dec[:4] + dec[5:7], date_iso)
            if p is None:
                break
            r_post = implied_post_rate(100 - p, r_prev, dec)
            if r_post is None:
                d_ = dt_date.fromisoformat(dec)
                nxt = (d_.replace(day=1) + timedelta(days=32)).replace(day=1)
                p2 = settle_at(conn, nxt.strftime("%Y%m"), date_iso)
                if p2 is None:
                    break
                r_post = 100 - p2
            steps.append({"decision": dec, "step_bp": (r_post - r_prev) * 100})
            r_prev = r_post
        # 捲積:每步 25bp 兩檔(p=step/25 向上;負值同理向下)
        dist = {0: 1.0}
        out = []
        for s in steps:
            import math
            lo = math.floor(s["step_bp"] / 25) * 25
            p_hi = (s["step_bp"] - lo) / 25
            new = {}
            for total, pr in dist.items():
                new[total + lo] = new.get(total + lo, 0) + pr * (1 - p_hi)
                new[total + lo + 25] = new.get(total + lo + 25, 0) + pr * p_hi
            dist = new
            out.append({"decision": s["decision"],
                        "step_bp": round(s["step_bp"], 1),
                        "dist_bp_from_now": {f"{int(k):+d}": round(v, 3)
                                             for k, v in sorted(dist.items())
                                             if v >= 0.001}})
        return out
    finally:
        conn.close()


def path_for(db_path: str, date_iso: str, months: int = 12) -> dict:
    """未來一年隱含路徑 {YYYYMM: 隱含月均}(日常端全曲線;回放端只有近月)。"""
    conn = _conn_ro(db_path)
    cur = dt_date.fromisoformat(date_iso).replace(day=1)
    path = {}
    for _ in range(months + 1):
        c = cur.strftime("%Y%m")
        p = settle_at(conn, c, date_iso)
        if p is not None:
            path[c] = round(100 - p, 3)
        cur = (cur + timedelta(days=32)).replace(day=1)
    conn.close()
    return path


# ── 衍生表寫入 ────────────────────────────────────────────────────────────────
def derive_daily(db_path: str = MACRO_DB, start: str = "2013-06-01",
                 end: str | None = None) -> int:
    """逐日衍生 fed_expectations_daily(冪等)。回放段自然只在會議月有值。"""
    end = end or datetime.now(TZ).strftime("%Y-%m-%d")
    meetings = load_meetings(db_path)
    conn_r = _conn_ro(db_path)
    trade_dates = [r[0] for r in conn_r.execute(
        "SELECT DISTINCT date FROM ff_futures_daily WHERE date>=? AND date<=? "
        "ORDER BY date", (start, end))]
    conn_r.close()
    rows = []
    for d in trade_dates:
        e = expectation_for(db_path, d, meetings)
        if e is None or e.get("degraded"):
            continue
        rows.append((d, e["next_meeting"], e["pre_rate"], e["expected_post_rate"],
                     e["expected_bp"], json.dumps(path_for(db_path, d), sort_keys=True),
                     "自算 FedWatch(CME 兩段模型)"))
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT OR REPLACE INTO fed_expectations_daily VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


def fill_pre_expected(db_path: str = MACRO_DB) -> int:
    """歷史會議 pre_expected_bp = 決策日前一交易日視角的期望變動(surprise 層基準)。"""
    meetings = load_meetings(db_path)
    conn = sqlite3.connect(db_path)
    n = 0
    for m in meetings:
        if m["change_bp"] is None:                 # 未來會議
            continue
        eve = (dt_date.fromisoformat(m["decision"]) - timedelta(days=1)).isoformat()
        e = expectation_for(db_path, eve, meetings)
        # eve 的「下次會議」必須就是本會議(前一日必然如此)
        if e and not e.get("degraded") and e["next_meeting"] == m["decision"]:
            conn.execute("UPDATE fomc_meetings SET pre_expected_bp=? "
                         "WHERE decision_date=?", (e["expected_bp"], m["decision"]))
            n += 1
    conn.commit()
    conn.close()
    return n


def run_daily(db_path: str = MACRO_DB) -> int:
    """19:00 掛鉤:補 DFF 近月 + 衍生近 10 日 + 未來會議 pre_expected 快照。"""
    try:
        ensure_dff(db_path, (datetime.now(TZ) - timedelta(days=30)).strftime("%Y-%m-%d"))
        n = derive_daily(db_path,
                         (datetime.now(TZ) - timedelta(days=10)).strftime("%Y-%m-%d"))
        print(f"✅ fed_expectations 日更 {n} 列")
        return 0
    except Exception as e:                         # noqa: BLE001
        print(f"[fed_expectations] ❌ {str(e)[:80]}", file=sys.stderr)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=MACRO_DB)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--show", metavar="DATE", help="顯示某日期望(驗證用)")
    args = ap.parse_args()
    if args.backfill:
        print(f"DFF:{ensure_dff(args.db)} 日")
        print(f"衍生:{derive_daily(args.db)} 列")
        print(f"會議 pre_expected:{fill_pre_expected(args.db)} 次")
        return 0
    if args.show:
        e = expectation_for(args.db, args.show)
        print(json.dumps(e, ensure_ascii=False, indent=2))
        print("path:", json.dumps(path_for(args.db, args.show)))
        return 0
    return run_daily(args.db)


if __name__ == "__main__":
    sys.exit(main())
