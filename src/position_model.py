"""
position_model.py — 持股水位計分引擎(stage12 spec §3,2026-07-08)

規則所有權:朋友(v1.2)。參數 100% 讀 config/position_model.json,引擎零硬編碼;
朋友改參數 = 改 config 重放,不改此檔。

反未來函數鐵律(spec §2.3):任一日期的計算只用「該日已公布」資料——
  月頻(密大/燈號/CPI)以 release_date 生效;密大百分位以「當日視角的已公布
  歷史窗口」計算;FedWatch 回放非會議月鎖定上次值(2026-07-08 拍板方案 a,
  回放報告須標注與日常端行為差異)。引擎以 date 斷言強制(assert_no_lookahead)。

效能:全表一次預載記憶體 + bisect,12.9 年重放 <60 秒(spec §6)。
"""
from __future__ import annotations
import argparse
import bisect
import json
import os
import sqlite3
import sys
from datetime import date as dt_date, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

MACRO_DB = os.path.join(PROJECT_ROOT, "macro.db")
CFG_PATH = os.path.join(PROJECT_ROOT, "config", "position_model.json")


def load_cfg(path: str = CFG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _band_score(bands: list[dict], value: float) -> float:
    """[{below, score}] 由低到高;below=null = 無上限檔。"""
    for b in bands:
        if b["below"] is None or value < b["below"]:
            return b["score"]
    return bands[-1]["score"]


class SignalData:
    """全訊號一次預載;所有查詢皆 latest-at-or-before(date)語意。"""

    def __init__(self, db_path: str = MACRO_DB):
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        g = conn.execute
        self.idx = {}                              # market → (dates[], rows[])
        for m in ("TAIEX", "SPX"):
            rows = g("SELECT date, close, ma20, ma60, ma200 FROM idx_daily "
                     "WHERE market=? ORDER BY date", (m,)).fetchall()
            self.idx[m] = ([r[0] for r in rows], rows)
        vr = g("SELECT date, close FROM vix_daily ORDER BY date").fetchall()
        self.vix = ([r[0] for r in vr], vr)
        ur = g("SELECT release_date, month, value FROM umich_monthly "
               "ORDER BY release_date").fetchall()
        self.umich = ([r[0] for r in ur], ur)
        lr = g("SELECT release_date, month, score, light FROM light_monthly "
               "ORDER BY release_date").fetchall()
        self.light = ([r[0] for r in lr], lr)
        dr = g("SELECT date, value FROM dgs10_daily ORDER BY date").fetchall()
        self.dgs10 = ([r[0] for r in dr], dr)
        tr = g("SELECT date, rate FROM usdtwd_daily ORDER BY date").fetchall()
        self.usdtwd = ([r[0] for r in tr], tr)
        cr = g("SELECT release_date, target_month, surprise_pp FROM cpi_events "
               "ORDER BY release_date").fetchall()
        self.cpi = ([r[0] for r in cr], cr)
        fr = g("SELECT date, expected_change_bp FROM fed_expectations_daily "
               "ORDER BY date").fetchall()
        self.fedexp = dict(fr)
        mr = g("SELECT decision_date, pre_expected_bp, change_bp FROM fomc_meetings "
               "WHERE change_bp IS NOT NULL ORDER BY decision_date").fetchall()
        self.meetings = ([r[0] for r in mr], mr)
        conn.close()

    @staticmethod
    def _leq(pair, date_iso: str):
        """(dates[], rows[]) → date 當日或之前最近一列;無 → None。"""
        dates, rows = pair
        i = bisect.bisect_right(dates, date_iso)
        return rows[i - 1] if i else None

    @staticmethod
    def _leq_index(pair, date_iso: str) -> int:
        return bisect.bisect_right(pair[0], date_iso) - 1

    def assert_no_lookahead(self, row_date: str, date_iso: str, what: str):
        if row_date > date_iso:
            raise AssertionError(f"反未來函數違規:{what} 用到 {row_date} > {date_iso}")


# ── 各類計分 ─────────────────────────────────────────────────────────────────
def score_technical(data: SignalData, cfg: dict, date_iso: str, state: dict
                    ) -> tuple[float | None, dict]:
    """兩市場 MA 規則平均;±buffer% 帶內沿用前一日判定(防抖)。"""
    tcfg = cfg["technical"]
    per_market, detail = [], {}
    for m in tcfg["markets"]:
        row = SignalData._leq(data.idx[m], date_iso)
        if row is None:
            continue
        data.assert_no_lookahead(row[0], date_iso, f"idx {m}")
        staleness = (dt_date.fromisoformat(date_iso)
                     - dt_date.fromisoformat(row[0])).days
        if staleness > tcfg["stale_days_max"]:
            continue
        close = row[1]
        ma_vals = {"ma20": row[2], "ma60": row[3], "ma200": row[4]}
        s = 0.0
        mstate = state.setdefault("ma", {}).setdefault(m, {})
        for rule in tcfg["ma_rules"]:
            ma = ma_vals.get(rule["ma"])
            if ma is None:
                continue
            ratio_pct = (close / ma - 1) * 100
            if ratio_pct > tcfg["buffer_pct"]:
                side = "above"
            elif ratio_pct < -tcfg["buffer_pct"]:
                side = "below"
            else:
                side = mstate.get(rule["ma"]) or ("above" if ratio_pct >= 0 else "below")
            mstate[rule["ma"]] = side
            s += rule[side]
        per_market.append(s)
        detail[m] = round(s, 3)
    if not per_market:
        return None, detail
    return sum(per_market) / len(per_market), detail


def score_sentiment(data: SignalData, cfg: dict, date_iso: str
                    ) -> tuple[float | None, dict]:
    scfg = cfg["sentiment"]
    vrow = SignalData._leq(data.vix, date_iso)
    if vrow is None:
        return None, {}
    vix = vrow[1]
    v_score = _band_score(scfg["vix_bands"], vix)
    warning = vix > scfg["vix_warning_level"]      # >40 不降檔(檔位分數維持)+旗標
    # 密大:已公布值 + 當日視角百分位
    i = SignalData._leq_index(data.umich, date_iso)
    if i < 0:
        return None, {"vix": vix}
    released = [r[2] for r in data.umich[1][:i + 1]]
    cur = released[-1]
    pct = sum(1 for v in released if v <= cur) / len(released) * 100
    if pct < scfg["umich_percentile_low"]:
        u_score = scfg["umich_scores"]["low"]
    elif pct > scfg["umich_percentile_high"]:
        u_score = scfg["umich_scores"]["high"]
    else:
        u_score = scfg["umich_scores"]["mid"]
    total = scfg["vix_weight"] * v_score + scfg["umich_weight"] * u_score
    return total, {"vix": vix, "vix_score": v_score, "umich": cur,
                   "umich_pct": round(pct, 1), "umich_score": u_score,
                   "warning": warning}


def score_cycle(data: SignalData, cfg: dict, date_iso: str
                ) -> tuple[float, dict]:
    """CPI surprise 對 nowcast;鎖定到下次公布(自然:取最近一次已公布)。"""
    ccfg = cfg["cycle"]
    row = SignalData._leq(data.cpi, date_iso)
    if row is None:
        return 0.0, {"cpi": "無已公布"}
    data.assert_no_lookahead(row[0], date_iso, "cpi_events")
    s = row[2]
    if s > ccfg["band_pp"]:
        score = ccfg["scores"]["hot"]
    elif s < -ccfg["band_pp"]:
        score = ccfg["scores"]["cool"]
    else:
        score = ccfg["scores"]["inline"]
    if abs(s) >= ccfg["double_at_pp"]:
        score *= 2
    return score, {"cpi_surprise_pp": s, "cpi_month": row[1], "cpi_score": score}


def _fed_daily_state(data: SignalData, cfg: dict, date_iso: str, state: dict):
    """平日層:期望變動 → dovish/neutral/hawkish。回放非會議月無值 → 鎖定上次
    (方案 a);從未有值 → neutral。"""
    fcfg = cfg["macro"]["fedwatch"]
    bp = data.fedexp.get(date_iso)
    if bp is None and fcfg["replay_daily_lock"]:
        bp = state.get("fed_daily_bp")
    if bp is not None:
        state["fed_daily_bp"] = bp
    if bp is None:
        return "neutral", None
    if bp <= fcfg["daily_dovish_bp"]:
        return "dovish", bp
    if bp >= fcfg["daily_hawkish_bp"]:
        return "hawkish", bp
    return "neutral", bp


def score_macro(data: SignalData, cfg: dict, date_iso: str, state: dict,
                cycle_detail: dict) -> tuple[float | None, dict]:
    mcfg = cfg["macro"]
    items, detail = [], {}
    # 1) 燈號
    lrow = SignalData._leq(data.light, date_iso)
    if lrow:
        data.assert_no_lookahead(lrow[0], date_iso, "light_monthly")
        ls = mcfg["light_scores"].get(lrow[3])
        if ls is not None:
            items.append(ls)
            detail["light"] = f"{lrow[1]} {lrow[3]}({ls:+g})"
    # 2) 10Y + 速度組合
    drow = SignalData._leq(data.dgs10, date_iso)
    if drow:
        y = drow[1]
        base = _band_score(mcfg["dgs10"]["bands"], y)
        back = (dt_date.fromisoformat(date_iso)
                - timedelta(days=mcfg["dgs10"]["speed_lookback_days"])).isoformat()
        prow = SignalData._leq(data.dgs10, back)
        rise_bp = (y - prow[1]) * 100 if prow else 0.0
        score10 = base
        speed_note = ""
        if rise_bp > mcfg["dgs10"]["speed_rise_bp_month"]:
            cpi_hot = cycle_detail.get("cpi_surprise_pp", 0) > cfg["cycle"]["band_pp"]
            cpi_mild = not cpi_hot
            fed_state, _ = _fed_daily_state(data, cfg, date_iso, state)
            last_meet = SignalData._leq(data.meetings, date_iso)
            fed_on_hold = (fed_state == "neutral"
                           and (last_meet is None or last_meet[2] == 0))
            if cpi_hot and fed_state == "hawkish":
                score10 = base * 2                 # 加倍(組合全中)
                speed_note = "速度加倍"
            elif cpi_mild and fed_on_hold:
                speed_note = "速度豁免"             # 豁免:維持 base
            else:
                speed_note = "速度中性"
        items.append(score10)
        detail["dgs10"] = f"{y}%({score10:+g}{',' + speed_note if speed_note else ''})"
    # 3) 台幣 20 交易日變動(升值=台幣走強=rate 下降)
    ti = SignalData._leq_index(data.usdtwd, date_iso)
    ucfg = mcfg["usdtwd"]
    if ti >= ucfg["window_trading_days"]:
        cur = data.usdtwd[1][ti][1]
        prev = data.usdtwd[1][ti - ucfg["window_trading_days"]][1]
        chg_pct = (cur / prev - 1) * 100           # 正=貶值(USD/TWD 上升)
        if chg_pct >= ucfg["crash_pct"]:
            tw = ucfg["scores"]["crash"]
        elif chg_pct >= ucfg["depreciate_pct"]:
            tw = ucfg["scores"]["depreciate"]
        elif chg_pct <= -ucfg["appreciate_pct"]:
            tw = ucfg["scores"]["appreciate"]
        else:
            tw = ucfg["scores"]["flat"]
        items.append(tw)
        detail["usdtwd"] = f"20日 {chg_pct:+.2f}%({tw:+g})"
    # 4) FedWatch 平日層 + surprise 層(鎖到下次會議)
    fcfg = mcfg["fedwatch"]
    fed_state, bp = _fed_daily_state(data, cfg, date_iso, state)
    fw = fcfg["daily_scores"][fed_state]
    last_meet = SignalData._leq(data.meetings, date_iso)
    sur_score = 0.0
    if last_meet and last_meet[1] is not None:     # pre_expected 有基準才判定
        diff = last_meet[2] - last_meet[1]         # 實際 − 會前期望(正=偏鷹)
        if abs(diff) >= fcfg["surprise_two_step_bp"]:
            sur_score = -fcfg["surprise_scores"]["two_step"] * (1 if diff > 0 else -1)
        elif abs(diff) >= fcfg["surprise_one_step_bp"]:
            sur_score = -fcfg["surprise_scores"]["one_step"] * (1 if diff > 0 else -1)
    items.append(fw + sur_score)
    detail["fedwatch"] = (f"平日 {fed_state}({fw:+g})"
                          + (f" surprise({sur_score:+g})" if sur_score else ""))
    if not items:
        return None, detail
    return sum(items) / len(items), detail


# ── 檔位與遲滯 ────────────────────────────────────────────────────────────────
def band_label(cfg: dict, total: float) -> str:
    for b in cfg["position_bands"]:
        if b["min"] is None or total >= b["min"]:
            return b["label"]
    return cfg["position_bands"][-1]["label"]


def run(start: str, end: str, cfg: dict | None = None,
        db_path: str = MACRO_DB, data: SignalData | None = None) -> list[dict]:
    """逐交易日(TAIEX 日曆)計分 + 遲滯切檔。回 list[dict](spec §3 輸出)。"""
    cfg = cfg or load_cfg()
    data = data or SignalData(db_path)
    days = [d for d in data.idx["TAIEX"][0] if start <= d <= end]
    state: dict = {}
    out = []
    cur_band, entered, pending, pending_n = None, None, None, 0
    for d in days:
        tech, tdet = score_technical(data, cfg, d, state)
        sent, sdet = score_sentiment(data, cfg, d)
        cyc, cdet = score_cycle(data, cfg, d)
        mac, mdet = score_macro(data, cfg, d, state, cdet)
        if tech is None or sent is None or mac is None:
            continue                               # 訊號未齊(回放起點前)不冒充
        w = cfg["categories"]
        total = (w["technical"] * tech + w["sentiment"] * sent
                 + w["cycle"] * cyc + w["macro"] * mac)
        raw = band_label(cfg, total)
        if cur_band is None:
            cur_band, entered = raw, d
        elif raw != cur_band:
            if raw == pending:
                pending_n += 1
            else:
                pending, pending_n = raw, 1
            if pending_n >= cfg["hysteresis_trading_days"]:
                cur_band, entered = raw, d
                pending, pending_n = None, 0
        else:
            pending, pending_n = None, 0
        out.append({
            "date": d, "total": round(total, 4),
            "cat": {"technical": round(tech, 4), "sentiment": round(sent, 4),
                    "cycle": round(cyc, 4), "macro": round(mac, 4)},
            "raw_band": raw, "band": cur_band, "entered": entered,
            "warning_vix": bool(sdet.get("warning")),
            "signals": {**tdet, **sdet, **cdet, **mdet},
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=MACRO_DB)
    ap.add_argument("--config", default=CFG_PATH)
    ap.add_argument("--date", help="單日檢視")
    ap.add_argument("--start", default="2013-08-01")
    ap.add_argument("--end")
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    if args.date:
        rows = run(args.date, args.date, cfg, args.db)
        # 單日需要暖機(MA 緩衝/FedWatch 鎖定態)→ 從 60 交易日前跑到當日取末列
        warm = (dt_date.fromisoformat(args.date) - timedelta(days=120)).isoformat()
        rows = run(warm, args.date, cfg, args.db)
        print(json.dumps(rows[-1] if rows else {}, ensure_ascii=False, indent=2))
        return 0
    end = args.end or dt_date.today().isoformat()
    rows = run(args.start, end, cfg, args.db)
    switches = sum(1 for i in range(1, len(rows))
                   if rows[i]["band"] != rows[i - 1]["band"])
    print(f"✅ {args.start}→{end}:{len(rows)} 交易日,切檔 {switches} 次,"
          f"現檔 {rows[-1]['band']}(自 {rows[-1]['entered']})" if rows else "無資料")
    return 0


if __name__ == "__main__":
    sys.exit(main())
