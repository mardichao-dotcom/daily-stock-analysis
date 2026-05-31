"""
standing.py — 站穩 / 跌破 純函式狀態機(規則 §3-A、§3-B)

兩個函式:
  evaluate_standing  — 站穩狀態機(5 狀態)
  evaluate_breakdown — 跌破標籤判定(獨立路徑,不修改狀態)

純函式:不讀寫 DB。狀態持久化由 W2.1 caller 負責。

────────────────────────────────────────────────────────────────────────────
狀態定義(5 種):

  UNTRIGGERED   未觸發(尚未碰到關鍵價)
  TRIGGERED     觸發中(等隔天判定),最多停 3 天視窗(D0+D1+D2)
  STANDING      站穩成立日(should_score=True,一日狀態,隔天必離開)
  MAINTAINING   維持中(站穩後的持續狀態,不再 +N)
  CANCELLED     取消(從 STANDING/MAINTAINING 因取消條件離開),
                下次評估時視為 UNTRIGGERED(可重啟新循環)

────────────────────────────────────────────────────────────────────────────
條件定義:

  TOUCH       = today.low <= p AND today.close >= p
                (今天 K 棒範圍涵蓋 p 且收盤站在線上)
  STAND_DAY   = today.open >= p AND today.close >= p
                (今天開盤+收盤都站在線上)
  DOWN_DAY    = today.open <  p AND today.close <  p
                (今天開盤+收盤都跌破)
  CONSEC_DOWN = today.close < p AND prev_day.close < p
                (連 2 天收盤跌破)

────────────────────────────────────────────────────────────────────────────
轉移規則(prev_state → new_state):

  From UNTRIGGERED / CANCELLED(後者視同 UNTRIGGERED 重新評估):
    TOUCH       → TRIGGERED (trigger_date=today)
    not TOUCH   → UNTRIGGERED

  From TRIGGERED:
    days_since_trigger 1 / 2(視窗內):
      STAND_DAY → STANDING (standing_date=today, should_score=True)
      DOWN_DAY  → UNTRIGGERED
      mixed     → TRIGGERED(留)
    days_since_trigger 3(視窗到期):
      → UNTRIGGERED,然後再評估今天是否 TOUCH 開新循環

  From STANDING / MAINTAINING:
    DOWN_DAY 或 CONSEC_DOWN → CANCELLED
    其他                      → MAINTAINING

────────────────────────────────────────────────────────────────────────────
跌破判定(evaluate_breakdown,獨立函式):

  prev_state ∉ {STANDING, MAINTAINING} → False(沒站上過談跌破無意義)
  prev_state ∈ {STANDING, MAINTAINING}:
    today.close < p, 或
    today.open  < p AND today.close < p
      → True (觸發 🔴 跌破標籤)

  本函式 *不修改* standing 狀態。標籤跟狀態機是獨立軌道。

────────────────────────────────────────────────────────────────────────────
給 W2.1 caller 的分離規則(重要):

  state machine 的數學比較需要 float,但持久化的識別碼必須字串穩定。
  caller 在每一條給定價格的處理中,要做兩層分離:

    line 例(price="5380"):
      price_str    = line["price"]          # "5380"  ← 寫入 standing_state.price_str
      given_price  = float(line["price"])   # 5380.0  ← 傳給 evaluate_standing

    area 例(low="2130", high="2180"):
      price_str    = f"{area['low']}-{area['high']}"            # "2130-2180"
      given_price  = (float(area['low']) + float(area['high'])) / 2   # 2155.0

  原因:
    - standing_state 表用 (symbol, category, price_str) 作 composite PK,
      字串穩定避免 float 比較或 hash 漂的問題
    - evaluate_standing 內部對 today.open / low / close 做大小比較,
      需要 float 數值
    - 若 caller 兩邊用同一個 float,可能因為 key_prices.json 的 price
      未來變成「2025.0」vs「2025」這種小差異而失聯舊狀態

────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations


UNTRIGGERED = "UNTRIGGERED"
TRIGGERED   = "TRIGGERED"
STANDING    = "STANDING"
MAINTAINING = "MAINTAINING"
CANCELLED   = "CANCELLED"

ALL_STATES = {UNTRIGGERED, TRIGGERED, STANDING, MAINTAINING, CANCELLED}

MAX_TRIGGER_WINDOW_DAYS = 2   # 觸發後最多再等 2 天 = D0 + D1 + D2 共 3 天視窗


# ── pure helpers ──────────────────────────────────────────────────────────────

def _days_since_in_history(price_history: list[dict], trigger_date: str) -> int:
    """回傳 trigger_date 到 today(price_history[-1])之間經過的「交易日」數。

    0 = trigger_date 是 today;1 = 隔天;2 = 再隔天;...

    用 price_history 的索引差計算,自然跳過週末(K 線本身只含交易日)。
    若 trigger_date 不在 price_history 中 → raise。caller 必須確保
    price_history 涵蓋 trigger_date 到 today 的所有交易日。
    """
    if trigger_date is None:
        raise ValueError("trigger_date is None when expected non-None")
    for i, k in enumerate(price_history):
        if k.get("date") == trigger_date:
            return len(price_history) - 1 - i
    raise ValueError(
        f"trigger_date {trigger_date!r} not in price_history "
        f"(dates: {[k.get('date') for k in price_history]})"
    )


def _make_state(
    state: str,
    trigger_date: str | None = None,
    standing_date: str | None = None,
) -> dict:
    return {
        "state":         state,
        "trigger_date":  trigger_date,
        "standing_date": standing_date,
    }


# ── evaluate_standing ─────────────────────────────────────────────────────────

def evaluate_standing(
    price_history: list[dict],
    given_price: float,
    prev_state: dict | None,
    today_date: str,
) -> tuple[dict, bool]:
    """評估今天的站穩狀態。

    Parameters
    ----------
    price_history : list of K-bar dicts
        排序由舊到新,最後一個是 today。每個 K bar 必含 "date" / "open" /
        "high" / "low" / "close"。若 prev_state.state == TRIGGERED,必須涵蓋
        trigger_date 到 today 之間所有交易日。
    given_price : float
        給定價格(線:該線價;區域:中點)
    prev_state : dict | None
        前一日狀態。None 視為新個股的 UNTRIGGERED 起點。結構:
            {"state": "...", "trigger_date": "...", "standing_date": "..."}
    today_date : str
        今天日期(ISO format)

    Returns
    -------
    (new_state_dict, should_score)
      new_state_dict : 同 prev_state 結構,給 caller 寫回 DB
      should_score   : True 代表「首次站穩當天 +N」應該觸發
    """
    # 預設值(新個股)
    if prev_state is None:
        prev_state = _make_state(UNTRIGGERED)

    state = prev_state.get("state")
    if state not in ALL_STATES:
        raise ValueError(f"Unknown prev_state.state: {state!r}")

    today = price_history[-1]
    prev_day = price_history[-2] if len(price_history) >= 2 else None
    p = given_price

    touch       = (today["low"]  <= p) and (today["close"] >= p)
    stand_day   = (today["open"] >= p) and (today["close"] >= p)
    down_day    = (today["open"] <  p) and (today["close"] <  p)
    consec_down = (
        today["close"] < p
        and prev_day is not None
        and prev_day["close"] < p
    )

    # ── CANCELLED 視為 UNTRIGGERED 重新評估 ─────────────────────────────────
    if state == CANCELLED:
        state = UNTRIGGERED

    # ── From UNTRIGGERED ────────────────────────────────────────────────────
    if state == UNTRIGGERED:
        if touch:
            return _make_state(TRIGGERED, trigger_date=today_date), False
        return _make_state(UNTRIGGERED), False

    # ── From TRIGGERED ──────────────────────────────────────────────────────
    if state == TRIGGERED:
        days_since = _days_since_in_history(price_history, prev_state["trigger_date"])

        if days_since > MAX_TRIGGER_WINDOW_DAYS:
            # 視窗到期(D3 以後)— 先 UNTRIGGERED,再評估今天 K 是否重啟
            if touch:
                return _make_state(TRIGGERED, trigger_date=today_date), False
            return _make_state(UNTRIGGERED), False

        # 視窗內(D1 或 D2)
        if stand_day:
            return _make_state(
                STANDING,
                trigger_date=prev_state["trigger_date"],
                standing_date=today_date,
            ), True
        if down_day:
            return _make_state(UNTRIGGERED), False
        # mixed — 留 TRIGGERED 等下一天
        return _make_state(TRIGGERED, trigger_date=prev_state["trigger_date"]), False

    # ── From STANDING / MAINTAINING ─────────────────────────────────────────
    if state in (STANDING, MAINTAINING):
        if down_day or consec_down:
            return _make_state(
                CANCELLED,
                standing_date=prev_state.get("standing_date"),
            ), False
        return _make_state(
            MAINTAINING,
            trigger_date=prev_state.get("trigger_date"),
            standing_date=prev_state.get("standing_date"),
        ), False

    # 不可能走到這裡(上面 ALL_STATES 已驗證)
    raise RuntimeError(f"Unreachable: state={state!r}")


# ── evaluate_breakdown ────────────────────────────────────────────────────────

def evaluate_breakdown(
    today_k: dict,
    given_price: float,
    prev_state: dict | None,
) -> bool:
    """跌破標籤判定(獨立函式,不修改狀態機狀態)。

    僅當 prev_state.state ∈ {STANDING, MAINTAINING} 才會回 True。
    其他狀態回 False(沒站上過的關鍵價,跌破無意義,避免每天對股價上方
    所有關鍵價發大量無意義跌破標籤)。

    觸發條件(rule §3-A):
        today.close < p, 或
        today.open  < p AND today.close < p

    Notes
    -----
    第二個條件(open + close 都 <)在邏輯上是第一個條件的 strict subset,
    保留兩個條件是為了「字面對齊規則 §3-A」,實際行為等同單看 close < p。
    """
    if prev_state is None:
        return False
    if prev_state.get("state") not in (STANDING, MAINTAINING):
        return False

    p = given_price

    # 規則 §3-A 跌破條件:
    #   條件 a: 收盤 < 給定價                  ← 任何 close < p 都成立
    #   條件 b: 開盤 + 收盤都 < 給定價          ← close < p 已涵蓋,strict subset
    # 邏輯上條件 b 冗餘,但保留為「字面對齊規則文字」,朋友 review 時容易對照
    # 原文。請勿刪除這段「重複的」判斷。
    if today_k["close"] < p:
        return True
    if today_k["open"] < p and today_k["close"] < p:
        return True
    return False
