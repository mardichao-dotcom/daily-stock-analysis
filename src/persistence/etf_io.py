"""
etf_io.py — ETF 籌碼資料的讀取 + 7 日窗口特徵計算(W2.2.1)

純 SQL 讀取 etf_operations.db,不寫入(5A 既有 schema,凍結)。
計算結果直接餵 src/scoring/chip_etf.score()。

⚠️ 行為對齊 v1 src/load_data.py 但**完全獨立實作**:
   v1 凍結期間(W4.3 之前),v2 不 import v1 任何模組。
   為什麼:強迫看懂 v1 邏輯、避免「以為它這樣其實那樣」。
   對齊由 tests/test_etf_io.py 的 v1 parity test 驗證。

5A schema(只讀,絕不寫):
  etf_operations.db.operations:
    etf  TEXT      ETF 代號(例 '00981A')
    代號  TEXT      股票代號(無 exchange prefix,例 '6223')
    日期  TEXT      ISO date
    動作  TEXT      加碼 / 建倉 / 減碼 / 清倉
    張數  INTEGER

7 日窗口 = 自然日(per v1 行為,記 pending_review)。
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta

ETF_WINDOW_DAYS = 7
BUY_ACTIONS  = {"加碼", "建倉"}
SELL_ACTIONS = {"減碼", "清倉"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _strip_exchange_prefix(symbol: str) -> str:
    """'TPEX:6223' → '6223';'2330' → '2330'"""
    return symbol.split(":")[-1]


def _date_minus_natural_days(date_str: str, days: int) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days)
    return d.strftime("%Y-%m-%d")


# ── 主 API ──────────────────────────────────────────────────────────────────

def compute_etf_features(
    conn: sqlite3.Connection,
    symbol: str,
    date: str,
    today_volume: float | None = None,
) -> dict:
    """計算 chip_etf.score() 所需的 features dict。

    Parameters
    ----------
    conn : etf_operations.db connection
    symbol : "TPEX:6223" 或 "TWSE:2330"(自動 strip exchange prefix)
    date : ISO date 字串,7 日窗口的右邊界
    today_volume : 當日股票成交量(從 kline.db 來),給 abnormal_ignition 用

    Returns
    -------
    dict matching chip_etf.score 的 etf_data shape:
        buy_count, buy_etfs, is_continuous_buy, is_abnormal_ignition,
        ignition_etf, ignition_shares, today_volume

    Behavior(對齊 v1):
        - buy_count = 7 日窗口內 unique 買進 ETF 數
        - is_continuous_buy = 「今天(most_recent_date)有任何 ETF 買」
                              AND 「7 日窗口內其他天也有任何 ETF 買」
                              (個股維度,非同檔 ETF;v1 寬鬆定義)
        - is_abnormal_ignition = 「恰好 1 檔 ETF 買」
                                 AND 「該 ETF 累計買超 > today_volume * 10%」
                                 (跟共識互斥,v1 嚴格定義)
    """
    code = _strip_exchange_prefix(symbol)
    window_start = _date_minus_natural_days(date, ETF_WINDOW_DAYS - 1)

    cur = conn.execute(
        "SELECT etf, 日期, 動作, 張數 FROM operations "
        "WHERE 代號 = ? AND 日期 >= ? AND 日期 <= ? "
        "ORDER BY 日期 ASC",
        (code, window_start, date),
    )
    rows = cur.fetchall()

    buy_events  = [(r[0], r[1], r[3]) for r in rows if r[2] in BUY_ACTIONS]
    sell_events = [(r[0], r[1], r[3]) for r in rows if r[2] in SELL_ACTIONS]

    buy_etfs  = sorted(set(e[0] for e in buy_events))     # sorted 給輸出穩定
    sell_etfs = sorted(set(e[0] for e in sell_events))

    # most_recent_date = 7 日窗口內有任何活動(買或賣)的最新一天
    all_dates = sorted({r[1] for r in rows}, reverse=True)
    most_recent_date = all_dates[0] if all_dates else None

    # single_day_buy = most_recent_date 那天的所有買進 ETF
    single_day_buy_etfs: set[str] = set()
    if most_recent_date is not None:
        for r in rows:
            if r[1] == most_recent_date and r[2] in BUY_ACTIONS:
                single_day_buy_etfs.add(r[0])

    # is_continuous_buy(v1 寬鬆定義):
    #   最新天有任何 ETF 買 AND 其他天也有任何 ETF 買
    prior_buy_etfs = set(e[0] for e in buy_events if e[1] != most_recent_date)
    is_continuous_buy = bool(single_day_buy_etfs) and bool(prior_buy_etfs)

    # is_abnormal_ignition(v1 嚴格定義):
    #   恰好 1 檔 ETF + 該 ETF 累計買超 > today_volume * 10%
    is_abnormal_ignition = False
    ignition_etf:    str | None = None
    ignition_shares: int | None = None
    if len(buy_etfs) == 1 and today_volume is not None and today_volume > 0:
        single_etf = buy_etfs[0]
        total_buy_shares = sum(e[2] for e in buy_events if e[0] == single_etf)
        if total_buy_shares > today_volume * 0.10:
            is_abnormal_ignition = True
            ignition_etf    = single_etf
            ignition_shares = total_buy_shares

    return {
        "buy_count":            len(buy_etfs),
        "buy_etfs":             buy_etfs,
        "is_continuous_buy":    is_continuous_buy,
        "is_abnormal_ignition": is_abnormal_ignition,
        "ignition_etf":         ignition_etf,
        "ignition_shares":      ignition_shares,
        "today_volume":         today_volume,
    }


def fetch_etf_active_summary(
    conn: sqlite3.Connection,
    date: str,
    watchlist: dict,
    window_days: int = ETF_WINDOW_DAYS,
) -> dict:
    """W3 ETF 主動式區塊資料:近 N 日累計 watchlist 內 ≥ 2 檔 ETF 加碼/減碼。

    2026-05-31 朋友確認:窗口期用「近 7 日累計」,跟 chip_etf 計分視角一致。

    **為什麼是 7 日累計而非當天**:
      - etfedge.xyz 不保證每日即時更新(有時盤後,有時隔天)
      - 7 日窗口容忍延遲,訊號穩定
      - 跟既有 chip_etf 共識加碼 7 日窗口邏輯一致(8 個月實戰驗證)

    **同檔 ETF 多日加碼算 1 檔**:
      用 set 去重 etf 代號,不是「事件數」。
      例:00981A 在 5/15 加 100、5/18 加 80、5/20 加 50
          → etf_count = 1(不夠 ≥ 2 共識)、total_shares = 230

    **⛔ ETF 減碼純標籤是例外**(`_compute_etf_decrease_tag`):
      標籤用「當天」窗口,跟此區塊視角不同(見朋友規則 §1-A)。

    Returns
    -------
    dict:{
      "increase": [
        {"symbol": "TPEX:6223", "etf_count": 3, "total_shares": 280,
         "etfs": ["00981A", "00987A", "00994A"]},
        ...
      ],
      "decrease": [
        {"symbol": "TWSE:2308", "etf_count": 2, "total_shares": -450,
         "etfs": ["00981A", "00987A"]},
        ...
      ]
    }
    排序:etf_count 降冪 → |total_shares| 降冪
    """
    if conn is None:
        return {"increase": [], "decrease": []}

    # 1. 一次 SQL 撈 7 日窗口內所有相關 actions
    window_start = _date_minus_natural_days(date, window_days - 1)
    cur = conn.execute(
        "SELECT 代號, etf, 動作, 張數 FROM operations "
        "WHERE 日期 >= ? AND 日期 <= ? "
        "AND 動作 IN ('加碼', '建倉', '減碼', '清倉')",
        (window_start, date),
    )
    rows = cur.fetchall()

    # 2. Aggregate by (代號, 方向)
    buy_by_code:  dict[str, dict] = {}
    sell_by_code: dict[str, dict] = {}
    for code, etf, action, shares in rows:
        bucket = buy_by_code if action in BUY_ACTIONS else sell_by_code
        entry = bucket.setdefault(code, {"etfs": set(), "shares": 0})
        entry["etfs"].add(etf)
        entry["shares"] += shares

    # 3. Build code → symbol map (watchlist 內個股)
    code_to_symbol: dict[str, str] = {}
    for sd in watchlist.get("台股板塊", {}).values():
        for m in sd.get("成員", []):
            sym = m.get("code", "")
            code_to_symbol[sym.split(":")[-1]] = sym

    # 4. Filter to watchlist + ≥2 檔
    increase = []
    decrease = []
    for code, data in buy_by_code.items():
        sym = code_to_symbol.get(code)
        if sym is None or len(data["etfs"]) < 2:
            continue
        increase.append({
            "symbol":       sym,
            "etf_count":    len(data["etfs"]),
            "total_shares": data["shares"],
            "etfs":         sorted(data["etfs"]),
        })
    for code, data in sell_by_code.items():
        sym = code_to_symbol.get(code)
        if sym is None or len(data["etfs"]) < 2:
            continue
        decrease.append({
            "symbol":       sym,
            "etf_count":    len(data["etfs"]),
            "total_shares": -data["shares"],   # 帶負號方便前端顯示
            "etfs":         sorted(data["etfs"]),
        })

    # 5. Sort: etf_count desc, |total_shares| desc
    increase.sort(key=lambda x: (-x["etf_count"], -x["total_shares"]))
    decrease.sort(key=lambda x: (-x["etf_count"], x["total_shares"]))   # 負數 asc = |x| desc

    return {"increase": increase, "decrease": decrease}


def compute_etf_max_date(conn: sqlite3.Connection) -> str | None:
    """回傳 operations 表的 MAX(日期),給 metadata.etf_delayed 判定用。

    None 代表 operations 表為空。
    """
    cur = conn.execute("SELECT MAX(日期) FROM operations")
    row = cur.fetchone()
    return row[0] if row and row[0] else None
