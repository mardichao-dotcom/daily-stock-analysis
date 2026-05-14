"""
load_data.py — reads kline.db + etf_operations.db and returns per-symbol data dicts.

load_all(data_date, kline_db, etf_db, symbols)
    → {symbol: StockData dict}

Each StockData dict contains:
  symbol, name, date, open, high, low, close, volume,
  prev_close, prev_high, change_pct, vol_ratio,
  amplitude_pct, body_ratio, is_limit_up, is_limit_down,
  is_gap_up, high_60d, break_60d_high, k_pattern,
  etf_buy_events, etf_sell_events,
  etf_buy_etfs, etf_sell_etfs,
  single_day_buy_etfs, single_day_sell_etfs,
  etf_consensus_buy_count, etf_consensus_sell_count,
  is_continuous_buy, is_abnormal_ignition,
  manager_divergence, divergence_net_sign
"""
import sqlite3
from datetime import datetime, timedelta
from .load_config import symbol_to_code

ETF_WINDOW_DAYS = 7


# ── K-bar pattern ─────────────────────────────────────────────────────────────

def _k_pattern(change_pct, body_ratio, amplitude_pct, close, open_):
    if change_pct >= 9.5:
        return "漲停"
    if change_pct <= -9.5:
        return "跌停"
    if body_ratio < 1 / 3 and amplitude_pct > 3:
        return "長上影線"
    if close < open_ and body_ratio > 0.6 and change_pct < -2:
        return "實體長黑"
    if close > open_ and body_ratio > 0.6:
        return "強紅K"
    if close > open_:
        return "中紅"
    if close < open_:
        return "中黑"
    return "十字"


# ── kline helpers ─────────────────────────────────────────────────────────────

def _fetch_bars(cur, symbol, data_date, n):
    """Fetch last n bars ≤ data_date ordered ascending."""
    cur.execute(
        "SELECT date, open, high, low, close, volume FROM kline "
        "WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT ?",
        (symbol, data_date, n)
    )
    rows = cur.fetchall()
    return list(reversed(rows))  # ascending order


def _load_kline_data(cur, symbol, data_date):
    # Need: today + yesterday for prev_close/prev_high, 5 days before today for vol_5,
    # 60 days before today for high_60d. Fetch 65 bars total.
    bars = _fetch_bars(cur, symbol, data_date, 65)
    if not bars:
        return None

    today = bars[-1]
    if today[0] != data_date:
        # Most recent bar might be an earlier date (e.g., US stocks on 5/12)
        # Use latest available bar as "today"
        pass

    if len(bars) < 2:
        return None

    yesterday = bars[-2]
    prev_close = yesterday[4]   # yesterday close
    prev_high = yesterday[2]    # yesterday high

    today_open = today[1]
    today_high = today[2]
    today_low = today[3]
    today_close = today[4]
    today_volume = today[5]

    # Derived
    change_pct = (today_close - prev_close) / prev_close * 100 if prev_close else 0.0
    span = today_high - today_low
    body_ratio = abs(today_close - today_open) / span if span > 0 else 0.0
    amplitude_pct = span / prev_close * 100 if prev_close else 0.0

    # vol_5: mean of 5 bars before today
    vol_bars = bars[:-1]   # exclude today
    vol_5_bars = vol_bars[-5:] if len(vol_bars) >= 5 else vol_bars
    vol_5 = sum(b[5] for b in vol_5_bars) / len(vol_5_bars) if vol_5_bars else today_volume
    vol_ratio = today_volume / vol_5 if vol_5 > 0 else 1.0

    # high_60d: max high of up to 60 bars before today
    prior_bars = bars[:-1]
    high_60d_bars = prior_bars[-60:] if len(prior_bars) >= 60 else prior_bars
    high_60d = max(b[2] for b in high_60d_bars) if high_60d_bars else today_high

    is_limit_up = change_pct >= 9.5
    is_limit_down = change_pct <= -9.5
    is_gap_up = today_open > prev_high
    break_60d_high = today_close >= high_60d * 0.99

    k_pat = _k_pattern(change_pct, body_ratio, amplitude_pct, today_close, today_open)

    return {
        "date": today[0],
        "open": today_open,
        "high": today_high,
        "low": today_low,
        "close": today_close,
        "volume": today_volume,
        "prev_close": prev_close,
        "prev_high": prev_high,
        "change_pct": round(change_pct, 4),
        "vol_ratio": round(vol_ratio, 4),
        "amplitude_pct": round(amplitude_pct, 4),
        "body_ratio": round(body_ratio, 4),
        "is_limit_up": is_limit_up,
        "is_limit_down": is_limit_down,
        "is_gap_up": is_gap_up,
        "high_60d": high_60d,
        "break_60d_high": break_60d_high,
        "k_pattern": k_pat,
    }


# ── ETF helpers ───────────────────────────────────────────────────────────────

def _date_minus(date_str, days):
    d = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days)
    return d.strftime("%Y-%m-%d")


def _load_etf_data(etf_cur, code, data_date, today_volume):
    """
    code: plain stock code without exchange prefix (e.g. '2368')
    Returns ETF signal fields for this stock.
    """
    window_start = _date_minus(data_date, ETF_WINDOW_DAYS - 1)
    BUY_ACTIONS = {"加碼", "建倉"}
    SELL_ACTIONS = {"減碼", "清倉"}

    etf_cur.execute(
        "SELECT etf, 日期, 動作, 張數 FROM operations "
        "WHERE 代號=? AND 日期>=? AND 日期<=? ORDER BY 日期 ASC",
        (code, window_start, data_date)
    )
    rows = etf_cur.fetchall()

    buy_events = [(r[0], r[1], r[3]) for r in rows if r[2] in BUY_ACTIONS]
    sell_events = [(r[0], r[1], r[3]) for r in rows if r[2] in SELL_ACTIONS]

    # Unique ETFs buying / selling in the 7-day window
    buy_etfs = set(e[0] for e in buy_events)
    sell_etfs = set(e[0] for e in sell_events)

    # Single-day events (most recent date with ETF activity)
    all_dates = sorted(set(r[1] for r in rows), reverse=True)
    most_recent_date = all_dates[0] if all_dates else None

    single_day_buy_etfs = set()
    single_day_sell_etfs = set()
    if most_recent_date:
        for etf, date, action, shares in [(r[0], r[1], r[2], r[3]) for r in rows]:
            if date == most_recent_date:
                if action in BUY_ACTIONS:
                    single_day_buy_etfs.add(etf)
                elif action in SELL_ACTIONS:
                    single_day_sell_etfs.add(etf)

    etf_consensus_buy_count = len(buy_etfs)
    etf_consensus_sell_count = len(sell_etfs)

    # is_continuous_buy: buying on the most recent day AND on at least one prior day
    prior_buy_etfs = set(e[0] for e in buy_events if e[1] != most_recent_date)
    is_continuous_buy = bool(single_day_buy_etfs) and bool(prior_buy_etfs)

    # is_abnormal_ignition: exactly 1 ETF buying, and that ETF's shares > 10% of today's volume
    is_abnormal_ignition = False
    if len(buy_etfs) == 1 and today_volume > 0:
        single_etf = next(iter(buy_etfs))
        total_buy_shares = sum(e[2] for e in buy_events if e[0] == single_etf)
        is_abnormal_ignition = total_buy_shares > today_volume * 0.10

    # manager_divergence: both buying and selling ETFs exist
    manager_divergence = bool(buy_etfs) and bool(sell_etfs)
    divergence_net_sign = 0
    if manager_divergence:
        total_buy = sum(e[2] for e in buy_events)
        total_sell = sum(e[2] for e in sell_events)
        net = total_buy - total_sell
        divergence_net_sign = 1 if net > 0 else -1

    return {
        "etf_buy_events": buy_events,
        "etf_sell_events": sell_events,
        "etf_buy_etfs": buy_etfs,
        "etf_sell_etfs": sell_etfs,
        "single_day_buy_etfs": single_day_buy_etfs,
        "single_day_sell_etfs": single_day_sell_etfs,
        "etf_consensus_buy_count": etf_consensus_buy_count,
        "etf_consensus_sell_count": etf_consensus_sell_count,
        "is_continuous_buy": is_continuous_buy,
        "is_abnormal_ignition": is_abnormal_ignition,
        "manager_divergence": manager_divergence,
        "divergence_net_sign": divergence_net_sign,
    }


# ── public entry point ────────────────────────────────────────────────────────

def load_all(data_date, kline_db, etf_db, tw_symbols, global_symbols=None):
    """
    Returns:
        tw_data  → {symbol: data_dict}  (Taiwan stocks, full ETF fields)
        global_data → {symbol: data_dict}  (international stocks, no ETF fields)
    """
    all_symbols = list(tw_symbols) + (list(global_symbols) if global_symbols else [])

    kline_conn = sqlite3.connect(kline_db)
    k_cur = kline_conn.cursor()

    etf_conn = sqlite3.connect(etf_db)
    e_cur = etf_conn.cursor()

    tw_data = {}
    global_data = {}

    tw_set = set(tw_symbols)
    global_set = set(global_symbols) if global_symbols else set()

    for symbol in all_symbols:
        # For international stocks, use latest available date ≤ data_date
        query_date = data_date
        kd = _load_kline_data(k_cur, symbol, query_date)
        if kd is None:
            continue

        entry = {"symbol": symbol, **kd}

        if symbol in tw_set:
            code = symbol_to_code(symbol)
            etf_fields = _load_etf_data(e_cur, code, data_date, kd["volume"])
            entry.update(etf_fields)
            tw_data[symbol] = entry
        elif symbol in global_set:
            global_data[symbol] = entry

    kline_conn.close()
    etf_conn.close()

    return tw_data, global_data
