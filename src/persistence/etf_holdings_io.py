"""
etf_holdings_io.py — 從 etf_holdings.db(自建 PCF 日快照)算 chip_etf 特徵。

2026-09-20 取代 etf_io.py(舊 etfedge/etf_operations.operations)。輸出 dict 介面
與 etf_io.compute_etf_features 相容,直接餵 src/scoring/chip_etf.score()。

━━ 設計(2026-09-20 與朋友拍板)━━
主指標:每單位股數 spu = shares / units_issued。
  為什麼不用純股數:主動式 ETF 爆量成長期,淨流入會讓股數一直漲,失真。
  為什麼不用純權重%:權重會被「別檔漲跌」被動影響(別人跌→我被動升)。
  spu 相除中和淨流入 → 純主動決策訊號。權重% 當「雙確認」(需同向 ≥+0.2pp)。

動作判定(比 7 天前 → 今天;窗口右邊界綁 data_date):
  加碼 add  : Δspu ≥ +3% 且 Δweight ≥ +0.2pp
  建倉 new  : 7 天前是「佔位」(weight<0.05% 或 shares≤2000)或未持有 → 今天實倉(weight≥0.2pp)
  減碼 trim : Δspu ≤ −3% 且 Δweight ≤ −0.2pp(★對稱,跟加碼同門檻)
  清倉 exit : 7 天前實倉 → 今天佔位/未持有

聚合(對齊舊 chip_etf 口徑):
  buy_count           = 窗口內「加碼 or 建倉」的 unique ETF 數(同檔多次算 1)
  is_continuous_buy   = 窗口內聚合權重 ≥2 個交易日續增(且 buy_count≥1)
  is_abnormal_ignition= 恰好 1 檔 ETF 有買訊 且 該檔為「建倉」(佔位→實倉,單檔點火)

★安全設計(務必保留):
  1. 窗口綁 data_date 往回算,不是 db 最新日。資料斷更 > 7 天 → end==start → Δ=0 →
     buy_count=0(歸零,不拿舊資料冒充現在)。
  2. 三層防炸:db 檔案不存在 / etf_holdings 表不存在 / 某檔 ETF 窗口內無資料 → 優雅跳過。
"""
from __future__ import annotations
import bisect
import sqlite3
from datetime import datetime, timedelta

# ── 參數(2026-09-20 拍板;密度統計 θ_add=+3% → ≥2共識約 3.25 檔/天)──────────
ETF_WINDOW_DAYS = 7          # 自然日;窗口 = 比「7 天前」
THETA_ADD = 0.03            # 每單位股數變化率門檻(加碼/減碼對稱)
THETA_W   = 0.2             # 權重雙確認 pp
THETA_NEW = 0.2             # 建倉:今日權重下限 pp
TOKEN_W   = 0.05           # 佔位判定:weight < 0.05%
TOKEN_SH  = 2000           #        或 shares ≤ 2000
CONTINUOUS_MIN_RISES = 2    # 連續加碼:窗口內聚合 spu 續增天數
CONTINUOUS_DAY_RISE  = 0.02 # 連續加碼:單日 spu 續增門檻(濾雜訊;2% → ~2.25 檔/天)

# 追蹤中的主動式 ETF(補六檔時在此加;需與 fetch_etf_holdings.SOURCES 對齊)
TRACKED_ETFS = ["00987A", "00981A", "00403A", "00992A"]

_EMPTY = {
    "buy_count": 0, "buy_etfs": [], "is_continuous_buy": False,
    "is_abnormal_ignition": False, "ignition_etf": None,
    "ignition_weight": None, "today_volume": None,
}


def holdings_ready(conn: sqlite3.Connection | None) -> bool:
    """conn 為 None 或 etf_holdings 表不存在 → False(優雅跳過,不炸)。"""
    if conn is None:
        return False
    try:
        conn.execute("SELECT 1 FROM etf_holdings LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def _minus_days(date_str: str, days: int) -> str:
    return (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")


def _is_token(row) -> bool:
    """row = (shares, weight, units) 或 None。未持有 / 佔位 → True。"""
    if row is None:
        return True
    sh, w, _u = row
    return (w is None or w < TOKEN_W) or (sh is None or sh <= TOKEN_SH)


def _spu(row):
    sh, _w, u = row
    return sh / u if (u and u > 0) else None


def _load(conn, code, date):
    """回 (etf_dates, hold):
      etf_dates[etf] = 該 ETF 交易日 sorted list(≤ date,全股票視角;區分 absent vs NOLIST)
      hold[(etf, d)] = 該 ETF 該日對 code 的 (sh, w, u)(未持有則無此鍵)
    """
    qmarks = ",".join("?" * len(TRACKED_ETFS))
    etf_dates: dict[str, list] = {}
    for e, d in conn.execute(
        f"SELECT DISTINCT etf_code, data_date FROM etf_holdings "
        f"WHERE etf_code IN ({qmarks}) AND data_date <= ? ORDER BY etf_code, data_date",
        (*TRACKED_ETFS, date),
    ):
        etf_dates.setdefault(e, []).append(d)
    hold: dict = {}
    for e, d, sh, w, u in conn.execute(
        f"SELECT etf_code, data_date, shares, weight_pct, units_issued FROM etf_holdings "
        f"WHERE stock_code=? AND etf_code IN ({qmarks}) AND data_date <= ?",
        (code, *TRACKED_ETFS, date),
    ):
        hold[(e, d)] = (sh, w, u)
    return etf_dates, hold


def compute_etf_features(
    conn: sqlite3.Connection | None,
    symbol: str,
    date: str,
    today_volume: float | None = None,
) -> dict:
    """算 chip_etf.score() 所需 features dict(介面同舊 etf_io)。

    conn : etf_holdings.db connection(不是舊 etf_operations!)
    symbol : "TWSE:2330" / "TPEX:6223"(自動 strip 前綴)
    date : 資料日期 ISO,窗口右邊界(★綁 data_date,不是 db 最新日)
    today_volume : 僅為介面相容保留(新模型未用,evidence 帶著)
    """
    out = dict(_EMPTY, today_volume=today_volume)
    if not holdings_ready(conn):
        return out                                  # 防炸:表/檔不存在 → 歸零

    code = symbol.split(":")[-1]
    win_start = _minus_days(date, ETF_WINDOW_DAYS - 1)   # [date-6, date] 續增用
    baseline  = _minus_days(date, ETF_WINDOW_DAYS)       # 「7 天前」比較基準

    etf_dates, hold = _load(conn, code, date)

    def state_asof(etf: str, asof: str):
        """該 ETF「≤ asof 最近交易日」對該股的狀態:
          'NOLIST' = ETF 該時無任何快照(未上市/斷更);
          None     = ETF 該日有揭露但未持有該股(absent);
          (sh,w,u) = 有持有。
        ★區分 NOLIST vs absent 是關鍵:absent 才能觸發建倉判定。"""
        ds = etf_dates.get(etf)
        if not ds:
            return "NOLIST"
        i = bisect.bisect_right(ds, asof) - 1
        if i < 0:
            return "NOLIST"
        return hold.get((etf, ds[i]))                # (sh,w,u) 或 None(absent)

    buy_etfs: list[str] = []
    new_etfs: list[str] = []
    trim_etfs: list[str] = []

    for etf in TRACKED_ETFS:
        end = state_asof(etf, date)
        start = state_asof(etf, baseline)
        # ★ETF 未上市/斷更(NOLIST)→ 跳過,不判、不炸、不假訊號
        if end == "NOLIST" or start == "NOLIST":
            continue
        end_tok, start_tok = _is_token(end), _is_token(start)
        if not end_tok and start_tok:                # 佔位/未持有 → 實倉 = 建倉
            if end[1] >= THETA_NEW:
                buy_etfs.append(etf)
                new_etfs.append(etf)
        elif end_tok and not start_tok:              # 實倉 → 佔位/未持有 = 清倉
            trim_etfs.append(etf)
        elif not end_tok and not start_tok:
            spu_e, spu_s = _spu(end), _spu(start)
            if spu_e is None or spu_s is None or spu_s <= 0:
                continue
            dspu = (spu_e - spu_s) / spu_s
            dw = end[1] - start[1]
            if dspu >= THETA_ADD and dw >= THETA_W:
                buy_etfs.append(etf)
            elif dspu <= -THETA_ADD and dw <= -THETA_W:
                trim_etfs.append(etf)

    buy_etfs = sorted(set(buy_etfs))
    buy_count = len(buy_etfs)

    # 連續加碼:窗口 [date-6, date] 內,聚合「每單位股數 spu」續增 ≥ N 個交易日。
    # ★用 spu 不用權重——權重續增可能只是別檔下跌的被動效果,spu 才是真加碼。
    spu_series: dict[str, float] = {}
    for etf in TRACKED_ETFS:
        for d in etf_dates.get(etf, []):
            if win_start <= d <= date:
                h = hold.get((etf, d))
                if h and not _is_token(h):
                    sp = _spu(h)
                    if sp:
                        spu_series[d] = spu_series.get(d, 0.0) + sp
    win_dates = sorted(spu_series)
    rises = sum(1 for i in range(1, len(win_dates))
                if spu_series[win_dates[i - 1]] > 0
                and (spu_series[win_dates[i]] - spu_series[win_dates[i - 1]])
                / spu_series[win_dates[i - 1]] >= CONTINUOUS_DAY_RISE)
    is_continuous = buy_count >= 1 and rises >= CONTINUOUS_MIN_RISES

    # 異常點火:恰好 1 檔有買訊 且 該檔為建倉(單一經理人新建倉)
    is_ignition = buy_count == 1 and len(new_etfs) == 1
    ignition_etf = new_etfs[0] if is_ignition else None
    ignition_weight = None
    if is_ignition:
        st = state_asof(ignition_etf, date)
        ignition_weight = round(st[1], 4) if isinstance(st, tuple) else None

    out.update(
        buy_count=buy_count,
        buy_etfs=buy_etfs,
        is_continuous_buy=is_continuous,
        is_abnormal_ignition=is_ignition,
        ignition_etf=ignition_etf,
        ignition_weight=ignition_weight,
    )
    return out


def compute_etf_decrease_tag(
    conn: sqlite3.Connection | None,
    symbol: str,
    date: str,
) -> list[str]:
    """⛔ ETF 減碼標籤(★對稱 7 日窗口,≥2 檔):同 compute_etf_features 的減碼口徑。
    純標籤、不計分(比照舊行為)。回 ["⛔ ETF 減碼(N 檔)"] 或 []。"""
    if not holdings_ready(conn):
        return []
    feat = _decrease_detail(conn, symbol, date)
    if len(feat) >= 2:
        return [f"⛔ ETF 減碼({len(feat)} 檔:{'、'.join(sorted(feat))})"]
    return []


def _decrease_detail(conn, symbol, date) -> set[str]:
    """回窗口內對該股「減碼 or 清倉」的 ETF 集合(★對稱門檻,同加碼)。"""
    code = symbol.split(":")[-1]
    baseline = _minus_days(date, ETF_WINDOW_DAYS)
    etf_dates, hold = _load(conn, code, date)

    def state_asof(etf, asof):
        ds = etf_dates.get(etf)
        if not ds:
            return "NOLIST"
        i = bisect.bisect_right(ds, asof) - 1
        return "NOLIST" if i < 0 else hold.get((etf, ds[i]))

    out: set[str] = set()
    for etf in TRACKED_ETFS:
        end, start = state_asof(etf, date), state_asof(etf, baseline)
        if end == "NOLIST" or start == "NOLIST":
            continue
        end_tok, start_tok = _is_token(end), _is_token(start)
        if end_tok and not start_tok:
            out.add(etf)                              # 清倉
        elif not end_tok and not start_tok:
            spu_e, spu_s = _spu(end), _spu(start)
            if spu_s and spu_s > 0:
                if (spu_e - spu_s) / spu_s <= -THETA_ADD and (end[1] - start[1]) <= -THETA_W:
                    out.add(etf)                      # 減碼
    return out
