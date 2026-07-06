"""
standing.py — 站穩 / 跌破 純函式(規則 v2.2 §3-A, §3-B)

2026-06-02 升級 v2.2(原 v2.1 邏輯見 git history pre commit a1e8c4c):
  1. 取消條件雙向:leave_up 也算(全在上方)+ 原 leave_down + consec_down
  2. TRIGGERED 視窗嚴格 1 個交易日(Day 2 = 隔天才算)
  3. 跌破改 event:Day 1 碰到 + close ≤ → Day 2 open ≤ AND close ≤,
     不需 prev state 是 STANDING

兩個函式:
  evaluate_standing  — 站穩狀態機(5 狀態)
  evaluate_breakdown — 跌破 event 判定(Day1+Day2 check,獨立 state)

純函式:不讀寫 DB。狀態持久化由 caller 負責。

────────────────────────────────────────────────────────────────────────────
狀態定義(5 種,維持 v2.1 schema 不變):

  UNTRIGGERED   未觸發
  TRIGGERED     觸發中(只等隔天一次,non-stand_day 立即作廢)
  STANDING      站穩成立日(should_score=True,Day 2 當天)
  MAINTAINING   維持中
  CANCELLED     取消(從 STANDING/MAINTAINING 因取消條件離開)

────────────────────────────────────────────────────────────────────────────
條件定義(v2.2):

  TOUCH       = today.low <= p AND today.high >= p AND today.close >= p
                (今天 K 棒涵蓋 p 且收盤站線上)
  STAND_DAY   = today.open >= p AND today.close >= p
                (Day 2:開+收都站線上)
  LEAVE_UP    = today.open > p+ε AND today.close > p+ε
                (全在上方,v2.2 新增:active 期間出現 → CANCELLED)
  LEAVE_DOWN  = today.open < p−ε AND today.close < p−ε
                (全在下方,等同 v2.1 down_day)
  CONSEC_DOWN = today.close < p AND prev_day.close < p
                (連 2 天收盤 < p)

  ε = 0.01(v2.2 §3-A 原文:業務容差一個 tick;審計 D3 由 1e-6 對齊)

────────────────────────────────────────────────────────────────────────────
轉移規則(prev_state → new_state):

  From UNTRIGGERED / CANCELLED(後者視同 UNTRIGGERED):
    TOUCH       → TRIGGERED(trigger_date=today)
    not TOUCH   → UNTRIGGERED

  From TRIGGERED(嚴格 1 天視窗):
    days_since == 1(隔天):
      STAND_DAY → STANDING(should_score=True,standing_date=today)
      else      → UNTRIGGERED(觸發作廢);若今天又 TOUCH 則重起 TRIGGERED
    days_since > 1(視窗到期):
      → UNTRIGGERED;若今天又 TOUCH 則重起 TRIGGERED

  From STANDING / MAINTAINING:
    LEAVE_UP / LEAVE_DOWN / CONSEC_DOWN → CANCELLED
    其他                                  → MAINTAINING

────────────────────────────────────────────────────────────────────────────
跌破判定(evaluate_breakdown,v2.2 改 event):

  yesterday(Day 1)碰到 p 且收盤 ≤ p,且
  today    (Day 2)open ≤ p AND close ≤ p
    → True(觸發 🔴 跌破標籤 + chart marker)

  跟 prev_state 無關(規則 v2.2 §3-A:跌破 event 自成)。

────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations


UNTRIGGERED = "UNTRIGGERED"
TRIGGERED   = "TRIGGERED"
STANDING    = "STANDING"
MAINTAINING = "MAINTAINING"
CANCELLED   = "CANCELLED"

ALL_STATES = {UNTRIGGERED, TRIGGERED, STANDING, MAINTAINING, CANCELLED}

# v2.2:Day 2 必須是隔天(days_since==1)。> 1 視為視窗到期。
MAX_TRIGGER_WINDOW_DAYS = 1

# ε = 0.01:規則 v2.2 §3-A 原文「ε = 0.01(避免浮點)」——業務容差(一個 tick),
# 非浮點 epsilon。open/close 落在 given±0.01 內視為「未離開」。
# (審計 D3:曾誤用 1e-6,對 tick=0.01 的低價股會出現與規則不符的取消;2026-07-07 對齊)
EPS = 0.01


# ── pure helpers ──────────────────────────────────────────────────────────────

def _days_since_in_history(price_history: list[dict], trigger_date: str) -> int:
    """從 price_history 算 trigger_date 到 today 之間共經過幾個交易日。
    today = price_history[-1].date,trigger_date 在 history 內。
    """
    dates = [k["date"] for k in price_history]
    if trigger_date not in dates:
        raise ValueError(f"trigger_date {trigger_date!r} not in price_history "
                         f"(dates: {dates})")
    idx_trigger = dates.index(trigger_date)
    idx_today   = len(dates) - 1
    return idx_today - idx_trigger


def _make_state(state: str, trigger_date: str | None = None,
                  standing_date: str | None = None) -> dict:
    return {
        "state":         state,
        "trigger_date":  trigger_date,
        "standing_date": standing_date,
    }


# ── evaluate_standing(v2.2)────────────────────────────────────────────────────

def evaluate_standing(
    price_history: list[dict],
    given_price: float,
    prev_state: dict | None,
    today_date: str,
) -> tuple[dict, bool]:
    """評估今天的站穩狀態(規則 v2.2)。

    Parameters
    ----------
    price_history : list of K-bar dicts
        排序由舊到新,最後一個是 today。每個 K bar 必含 date / open / high /
        low / close。若 prev_state.state == TRIGGERED,必須涵蓋 trigger_date
        到 today 之間所有交易日。
    given_price : float
    prev_state : dict | None
        None 視為 UNTRIGGERED 起點。
    today_date : str
        今天日期(ISO)

    Returns
    -------
    (new_state_dict, should_score)
    """
    if prev_state is None:
        prev_state = _make_state(UNTRIGGERED)

    state = prev_state.get("state")
    if state not in ALL_STATES:
        raise ValueError(f"Unknown prev_state.state: {state!r}")

    today    = price_history[-1]
    prev_day = price_history[-2] if len(price_history) >= 2 else None
    p = given_price

    # 條件
    touch      = (today["low"] <= p) and (today["high"] >= p) and (today["close"] >= p)
    stand_day  = (today["open"] >= p) and (today["close"] >= p)
    leave_up   = (today["open"]  > p + EPS) and (today["close"]  > p + EPS)
    leave_down = (today["open"]  < p - EPS) and (today["close"]  < p - EPS)
    consec_down = (
        today["close"] < p
        and prev_day is not None
        and prev_day["close"] < p
    )

    # ── CANCELLED 視為 UNTRIGGERED 重新評估 ────────────────────────────────
    if state == CANCELLED:
        state = UNTRIGGERED

    # ── From UNTRIGGERED ───────────────────────────────────────────────────
    if state == UNTRIGGERED:
        if touch:
            return _make_state(TRIGGERED, trigger_date=today_date), False
        return _make_state(UNTRIGGERED), False

    # ── From TRIGGERED(v2.2:嚴格隔天視窗)─────────────────────────────────
    if state == TRIGGERED:
        days_since = _days_since_in_history(price_history, prev_state["trigger_date"])

        # 視窗到期(D2+,即 days_since > 1)— 上一輪觸發作廢
        # 評估今天是否重新 TOUCH 開新循環
        if days_since > MAX_TRIGGER_WINDOW_DAYS:
            if touch:
                return _make_state(TRIGGERED, trigger_date=today_date), False
            return _make_state(UNTRIGGERED), False

        # days_since == 1(Day 2,隔天)
        if days_since == 1:
            if stand_day:
                return _make_state(
                    STANDING,
                    trigger_date=prev_state["trigger_date"],
                    standing_date=today_date,
                ), True
            # Day 2 沒符合 → 觸發作廢;規則 v2.2「等下次重新碰到再起算」
            # 若今天 K 又 TOUCH,重新起一個 Day 1
            if touch:
                return _make_state(TRIGGERED, trigger_date=today_date), False
            return _make_state(UNTRIGGERED), False

        # days_since == 0 不該發生(每日 evaluate 一次,trigger_date 不會是 today
        # 同時又走進 TRIGGERED 分支),保險起見當留 TRIGGERED
        return _make_state(TRIGGERED, trigger_date=prev_state["trigger_date"]), False

    # ── From STANDING / MAINTAINING ─────────────────────────────────────────
    if state in (STANDING, MAINTAINING):
        # v2.2 新增 leave_up(雙向取消)
        if leave_up or leave_down or consec_down:
            return _make_state(
                CANCELLED,
                standing_date=prev_state.get("standing_date"),
            ), False
        return _make_state(
            MAINTAINING,
            trigger_date=prev_state.get("trigger_date"),
            standing_date=prev_state.get("standing_date"),
        ), False

    raise RuntimeError(f"Unreachable: state={state!r}")


# ── evaluate_breakdown(v2.2:event-based)──────────────────────────────────────

def evaluate_breakdown(
    today_k: dict,
    yesterday_k: dict | None,
    given_price: float,
) -> bool:
    """跌破 event 判定(規則 v2.2 §3-A,純 event,不需 prev state)。

    Day 1(yesterday):碰到 + close ≤ p
    Day 2(today)   :open ≤ p AND close ≤ p
    → True(觸發 🔴 跌破標籤)

    Parameters
    ----------
    today_k : dict        — 今天 K bar
    yesterday_k : dict|None — 昨天 K bar;None 視為 Day 1 不成立
    given_price : float
    """
    if yesterday_k is None:
        return False

    p = given_price

    # Day 1:碰到 p 且收盤 ≤ p
    day1_touch     = (yesterday_k["low"] <= p) and (yesterday_k["high"] >= p)
    day1_close_low = yesterday_k["close"] <= p
    if not (day1_touch and day1_close_low):
        return False

    # Day 2:open ≤ p AND close ≤ p
    return (today_k["open"] <= p) and (today_k["close"] <= p)
