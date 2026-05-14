"""
key_price_state.py — 關鍵價狀態機（stage 6）

apply_key_price_states(tw_data, kp_state)
  - 讀取每檔股票的收盤價 vs 水平線關鍵價
  - 更新 kp_state["關鍵價狀態"]
  - 在 d["key_price_results"] 寫入每條線的事件與分數

狀態轉移：
  未觸及 → (close ≥ price) → 已突破
  已突破 → (close <  price) → 已跌破
  已跌破 → (close ≥  price) → 已突破（重新站上）

計分（非冷啟動）：
  未觸及 → 已突破   : +3 新突破
  維持已突破        : +1 站穩
  已突破 → 已跌破   : -3 跌破
  已跌破 → 已突破   : +1 站穩（重新站上，非首次突破）
  其餘              :  0

冷啟動（price_key 在狀態機裡不存在）：
  close ≥ price → 初始「已突破」，計 +1 站穩（不給 +3）
  close <  price → 初始「未觸及」，計 0
"""
from .load_key_prices import get_key_prices


def _price_key(price):
    """float → JSON-safe string key（避免浮點 key 不一致）"""
    return str(price)


def _compute_events(close, marks, prev_symbol_state):
    """
    回傳 (events, new_symbol_state)
    events: list of {price, label, event, delta, is_cold_start}
    """
    events = []
    new_state = dict(prev_symbol_state)

    for mark in marks:
        if mark["type"] != "line":
            continue  # zone 不計分

        price  = mark["price"]
        label  = mark["label"]
        is_poc = bool(mark.get("is_poc"))
        key    = _price_key(price)
        above  = close >= price
        prev   = prev_symbol_state.get(key)

        if prev is None:
            # ── 冷啟動 ──────────────────────────────────────────────────────
            if above:
                new_state[key] = {"狀態": "已突破", "曾突破": True}
                # POC 線：狀態記錄但不計分
                events.append({"price": price, "label": label, "is_poc": is_poc,
                               "event": "站穩", "delta": 0 if is_poc else 1,
                               "cold_start": True})
            else:
                new_state[key] = {"狀態": "未觸及", "曾突破": False}
                events.append({"price": price, "label": label, "is_poc": is_poc,
                               "event": "無", "delta": 0, "cold_start": True})
        else:
            # ── 正常轉移 ─────────────────────────────────────────────────────
            prev_status = prev["狀態"]

            if above:
                if prev_status == "未觸及":
                    new_state[key] = {"狀態": "已突破", "曾突破": True}
                    events.append({"price": price, "label": label, "is_poc": is_poc,
                                   "event": "新突破", "delta": 0 if is_poc else 3,
                                   "cold_start": False})
                else:
                    # 已突破（維持）或 已跌破→重新站上，皆視為站穩
                    new_state[key] = {"狀態": "已突破", "曾突破": True}
                    events.append({"price": price, "label": label, "is_poc": is_poc,
                                   "event": "站穩", "delta": 0 if is_poc else 1,
                                   "cold_start": False})
            else:
                if prev_status == "已突破":
                    new_state[key] = {"狀態": "已跌破", "曾突破": True}
                    events.append({"price": price, "label": label, "is_poc": is_poc,
                                   "event": "跌破", "delta": 0 if is_poc else -3,
                                   "cold_start": False})
                else:
                    # 未觸及（維持）或 已跌破（維持）
                    events.append({"price": price, "label": label, "is_poc": is_poc,
                                   "event": "無", "delta": 0, "cold_start": False})

    return events, new_state


def apply_key_price_states(tw_data, state):
    """
    tw_data: {symbol: d, ...}  —— 每個 d 必須有 "close" 欄位
    state:   signal_state dict（會直接修改 state["關鍵價狀態"]）

    副作用：
      - 每個有關鍵價的 d 新增 "key_price_results"
      - state["關鍵價狀態"] 更新
    """
    kp_state = state.setdefault("關鍵價狀態", {})

    for symbol, d in tw_data.items():
        marks = get_key_prices(symbol)
        if not marks:
            d["key_price_results"] = []
            continue

        close = d.get("close", 0) or 0
        prev_sym_state = kp_state.get(symbol, {})

        events, new_sym_state = _compute_events(close, marks, prev_sym_state)

        kp_state[symbol]        = new_sym_state
        d["key_price_results"]  = events
