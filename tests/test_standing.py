"""
test_standing.py — 站穩 / 跌破 純函式狀態機單元測試(W1.5)

覆蓋:
  1. 5 個 prev_state × 3+ 情境 = 至少 15 個基本轉移
  2. 旺矽 §3-B 範例 7 天端到端
  3. 4 個邊界 case(Q1-Q4)
  4. Q5/Q6/Q7 對應測試
  5. 跌破判定的 prev_state 限制
  6. State dict round-trip(JSON 序列化來回)

執行:python3 -m unittest tests.test_standing
"""
from __future__ import annotations
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.triggers.standing import (
    evaluate_standing, evaluate_breakdown,
    UNTRIGGERED, TRIGGERED, STANDING, MAINTAINING, CANCELLED,
)


def k(date: str, open: float, high: float, low: float, close: float) -> dict:
    """K bar fixture builder"""
    return {"date": date, "open": open, "high": high, "low": low, "close": close}


def state(s: str, trigger_date: str | None = None,
          standing_date: str | None = None) -> dict:
    return {"state": s, "trigger_date": trigger_date, "standing_date": standing_date}


# ═════════════════════════════════════════════════════════════════════════════
# 1. 5 個 prev_state 路徑 × 3+ 情境
# ═════════════════════════════════════════════════════════════════════════════

class TestFromUntriggered(unittest.TestCase):
    """From UNTRIGGERED"""

    def test_touch_goes_triggered(self):
        """碰到關鍵價 + 收盤站上 → TRIGGERED,trigger_date=today"""
        new, score = evaluate_standing(
            [k("2026-05-13", open=95, high=102, low=94, close=100)],
            given_price=100,
            prev_state=state(UNTRIGGERED),
            today_date="2026-05-13",
        )
        self.assertEqual(new["state"], TRIGGERED)
        self.assertEqual(new["trigger_date"], "2026-05-13")
        self.assertFalse(score)

    def test_no_touch_stays_untriggered(self):
        """收盤未過 → UNTRIGGERED"""
        new, score = evaluate_standing(
            [k("2026-05-13", open=95, high=99, low=94, close=98)],
            given_price=100,
            prev_state=state(UNTRIGGERED),
            today_date="2026-05-13",
        )
        self.assertEqual(new["state"], UNTRIGGERED)
        self.assertFalse(score)

    def test_stand_day_but_not_touch_stays_untriggered(self):
        """整根 K 在線上(low > p),沒「碰到」→ 不觸發
        例:開 102 收 103 low 101 — 完全在 100 以上"""
        new, score = evaluate_standing(
            [k("2026-05-13", open=102, high=105, low=101, close=103)],
            given_price=100,
            prev_state=state(UNTRIGGERED),
            today_date="2026-05-13",
        )
        self.assertEqual(new["state"], UNTRIGGERED)
        self.assertFalse(score)

    def test_prev_state_none_treated_as_untriggered(self):
        """新個股 prev_state=None → 視為 UNTRIGGERED"""
        new, score = evaluate_standing(
            [k("2026-05-13", open=95, high=102, low=94, close=100)],
            given_price=100,
            prev_state=None,
            today_date="2026-05-13",
        )
        self.assertEqual(new["state"], TRIGGERED)
        self.assertFalse(score)


class TestQ5UntriggeredPlusStandDayCanTouch(unittest.TestCase):
    """Q5:UNTRIGGERED + 今天 STAND_DAY 且 low ≤ p → TRIGGERED(不跳 STANDING)"""

    def test_stand_day_with_touch_still_only_triggered_not_standing(self):
        """today: open=101, low=99(碰), close=101 → 雖然 STAND_DAY,
        仍只能 TRIGGERED,不能跳 STANDING(規則「隔天判定」精神)"""
        new, score = evaluate_standing(
            [k("2026-05-13", open=101, high=102, low=99, close=101)],
            given_price=100,
            prev_state=state(UNTRIGGERED),
            today_date="2026-05-13",
        )
        self.assertEqual(new["state"], TRIGGERED)
        self.assertFalse(score)   # 不該 +N

    def test_q5_strict_trigger_day_with_stand_day_still_only_triggered(self):
        """旺矽範例的觸發日:open=5400, low=5370, close=5500, p=5380
        同時滿足 TOUCH(low ≤ p)跟 STAND_DAY(open ≥ p, close ≥ p),
        但因為是觸發當天(prev_state=None),只能 TRIGGERED 不能跳 STANDING。
        鎖死「觸發日不等於判定日」這個規則精神,防止未來重構誤改。"""
        new, score = evaluate_standing(
            [k("2026-05-13", open=5400, high=5550, low=5370, close=5500)],
            given_price=5380,
            prev_state=None,   # 新個股,沒任何前置狀態
            today_date="2026-05-13",
        )
        self.assertEqual(new["state"], TRIGGERED)
        self.assertEqual(new["trigger_date"], "2026-05-13")
        self.assertFalse(score)   # 觸發日絕對不可 +N


class TestFromTriggered(unittest.TestCase):
    """From TRIGGERED — D1 / D2 / D3(視窗到期)"""

    def test_d1_stand_day_goes_standing_score(self):
        """D1 STAND_DAY → STANDING + should_score=True"""
        new, score = evaluate_standing(
            [k("2026-05-13", 95, 102, 94, 100),
             k("2026-05-14", 102, 105, 101, 104)],  # D1: STAND_DAY
            given_price=100,
            prev_state=state(TRIGGERED, trigger_date="2026-05-13"),
            today_date="2026-05-14",
        )
        self.assertEqual(new["state"], STANDING)
        self.assertEqual(new["standing_date"], "2026-05-14")
        self.assertTrue(score)   # ← +N

    def test_d1_down_day_goes_untriggered(self):
        """D1 DOWN_DAY → UNTRIGGERED"""
        new, score = evaluate_standing(
            [k("2026-05-13", 95, 102, 94, 100),
             k("2026-05-14", 95, 99, 90, 95)],   # 開+收都 <
            given_price=100,
            prev_state=state(TRIGGERED, trigger_date="2026-05-13"),
            today_date="2026-05-14",
        )
        self.assertEqual(new["state"], UNTRIGGERED)
        self.assertFalse(score)

    def test_d2_window_expired_under_v22(self):
        """v2.2:Day 2 嚴格隔天。D2 視窗過後若今天 touch → 重啟 TRIGGER
        (注意 touch 需 K_low ≤ p ≤ K_high)
        """
        new, score = evaluate_standing(
            [k("2026-05-13", 95, 102, 94, 100),
             k("2026-05-14", 99, 102, 98, 101),    # mixed Day 2 → 觸發作廢
             k("2026-05-15", 99, 105, 98, 104)],   # today K touches p=100 (low=98 ≤ 100 ≤ high=105)
            given_price=100,
            prev_state=state(TRIGGERED, trigger_date="2026-05-13"),
            today_date="2026-05-15",
        )
        # v2.2:days_since=2 > 1 → 視窗到期;今天 touch → 重啟新 TRIGGER
        self.assertEqual(new["state"], TRIGGERED)
        self.assertEqual(new["trigger_date"], "2026-05-15")
        self.assertFalse(score)

    def test_d3_window_expired_no_touch_goes_untriggered(self):
        """D3 視窗到期 + 今天沒 TOUCH → UNTRIGGERED"""
        new, score = evaluate_standing(
            [k("2026-05-13", 95, 102, 94, 100),
             k("2026-05-14", 99, 101, 98, 100.5),  # mixed,留 TRIGGERED
             k("2026-05-15", 99, 101, 98, 100.5),  # mixed,留 TRIGGERED
             k("2026-05-16", 90, 95, 89, 92)],     # D3: 沒 TOUCH
            given_price=100,
            prev_state=state(TRIGGERED, trigger_date="2026-05-13"),
            today_date="2026-05-16",
        )
        self.assertEqual(new["state"], UNTRIGGERED)
        self.assertFalse(score)

    def test_d3_window_expired_but_touch_today_restarts(self):
        """D3 視窗到期但今天又 TOUCH → 開新 TRIGGERED 循環"""
        new, score = evaluate_standing(
            [k("2026-05-13", 95, 102, 94, 100),
             k("2026-05-14", 99, 101, 98, 100.5),
             k("2026-05-15", 99, 101, 98, 100.5),
             k("2026-05-16", 95, 102, 94, 100)],   # D3:又 TOUCH
            given_price=100,
            prev_state=state(TRIGGERED, trigger_date="2026-05-13"),
            today_date="2026-05-16",
        )
        self.assertEqual(new["state"], TRIGGERED)
        self.assertEqual(new["trigger_date"], "2026-05-16")   # 新循環
        self.assertFalse(score)


class TestQ1Q2TriggeredMixedDaysV22(unittest.TestCase):
    """v2.2:TRIGGERED + Day 2 沒 stand_day → 觸發作廢,UNTRIGGERED 或重新 TRIGGER"""

    def test_q1_open_above_close_below_d2_fails(self):
        """Q1 在 v2.2:Day 2 open ≥ 但 close < → stand_day=False → 觸發作廢
        今天 K touch(low=95 ≤ 100 ≤ high=103)且 close=98 < 100 → 不重啟 TRIGGER
        """
        new, score = evaluate_standing(
            [k("2026-05-13", 95, 102, 94, 100),
             k("2026-05-14", 102, 103, 95, 98)],   # 開 102 ≥ 100, 收 98 < 100
            given_price=100,
            prev_state=state(TRIGGERED, trigger_date="2026-05-13"),
            today_date="2026-05-14",
        )
        # Day 2 fail → 觸發作廢。touch 需 close ≥ p,而 close=98 < 100 → 不 touch
        self.assertEqual(new["state"], UNTRIGGERED)
        self.assertFalse(score)

    def test_q2_open_below_close_above_d2_fails_but_retouch(self):
        """Q2 在 v2.2:Day 2 open < p → stand_day=False → 觸發作廢
        但今天 K touch(low=97, close=101)→ 重新起 TRIGGER
        """
        new, score = evaluate_standing(
            [k("2026-05-13", 95, 102, 94, 100),
             k("2026-05-14", 98, 102, 97, 101)],   # 開 98 < 100, 收 101 ≥ 100
            given_price=100,
            prev_state=state(TRIGGERED, trigger_date="2026-05-13"),
            today_date="2026-05-14",
        )
        # Day 2 fail → 觸發作廢。但 today touch(low ≤ p ≤ high, close ≥ p)
        # → 開新 TRIGGER 循環,trigger_date=today
        self.assertEqual(new["state"], TRIGGERED)
        self.assertEqual(new["trigger_date"], "2026-05-14")
        self.assertFalse(score)


class TestFromStanding(unittest.TestCase):
    """From STANDING(一日狀態,下一個 evaluate 必定離開)"""

    def test_standing_close_at_p_stays_maintaining_v22(self):
        """v2.2:STANDING → MAINTAINING 要求今天「沒離開」p。
        即 NOT (open > p AND close > p) AND NOT (open < p AND close < p) AND NOT consec_down。
        典型場景:今天 K bar 跨越 p(low < p < high),open 或 close 恰好 = p,或一邊
        在上一邊在下 → 仍 MAINTAINING。
        """
        new, score = evaluate_standing(
            [k("2026-05-14", 102, 105, 101, 104),
             k("2026-05-15", 100, 105, 95, 100)],   # open=close=100 == p → 不算 leave_up
            given_price=100,
            prev_state=state(STANDING, trigger_date="2026-05-13",
                             standing_date="2026-05-14"),
            today_date="2026-05-15",
        )
        self.assertEqual(new["state"], MAINTAINING)
        self.assertEqual(new["standing_date"], "2026-05-14")
        self.assertFalse(score)

    def test_standing_to_cancelled_down_day(self):
        """STANDING 隔天 DOWN_DAY → CANCELLED"""
        new, score = evaluate_standing(
            [k("2026-05-14", 102, 105, 101, 104),
             k("2026-05-15", 95, 99, 90, 95)],
            given_price=100,
            prev_state=state(STANDING, standing_date="2026-05-14"),
            today_date="2026-05-15",
        )
        self.assertEqual(new["state"], CANCELLED)
        self.assertFalse(score)


class TestQ3Q4FromMaintaining(unittest.TestCase):
    """Q3 / Q4:MAINTAINING 的取消條件邊界"""

    def test_q3_one_day_close_below_open_above_NOT_cancelled(self):
        """Q3:一天「收 < 但開 ≥」→ 不算「開+收都 <」→ 留 MAINTAINING"""
        new, score = evaluate_standing(
            [k("2026-05-15", 103, 105, 102, 104),   # prev_day close ≥ p
             k("2026-05-16", 101, 102, 95, 98)],    # 開 101 ≥, 收 98 <
            given_price=100,
            prev_state=state(MAINTAINING),
            today_date="2026-05-16",
        )
        self.assertEqual(new["state"], MAINTAINING)   # 不取消
        self.assertFalse(score)

    def test_q4_two_consecutive_close_below_open_above_CANCELLED(self):
        """Q4:連 2 天「收 < 但開 ≥」→ 算「連 2 天收 <」→ CANCELLED"""
        new, score = evaluate_standing(
            [k("2026-05-15", 101, 102, 95, 98),    # 收 98 < (前一天)
             k("2026-05-16", 101, 102, 96, 99)],   # 收 99 < (今天)
            given_price=100,
            prev_state=state(MAINTAINING),
            today_date="2026-05-16",
        )
        self.assertEqual(new["state"], CANCELLED)   # 連 2 天收 < → 取消
        self.assertFalse(score)


class TestFromCancelled(unittest.TestCase):
    """From CANCELLED → 視為 UNTRIGGERED 重新評估,可重啟"""

    def test_cancelled_plus_touch_restarts_new_cycle(self):
        """CANCELLED 後又 TOUCH → 新 TRIGGERED 循環"""
        new, score = evaluate_standing(
            [k("2026-05-20", 95, 99, 90, 95),
             k("2026-05-21", 96, 102, 94, 100)],
            given_price=100,
            prev_state=state(CANCELLED, standing_date="2026-05-14"),
            today_date="2026-05-21",
        )
        self.assertEqual(new["state"], TRIGGERED)
        self.assertEqual(new["trigger_date"], "2026-05-21")   # 新循環
        self.assertFalse(score)

    def test_cancelled_no_touch_stays_untriggered(self):
        new, score = evaluate_standing(
            [k("2026-05-20", 95, 99, 90, 95),
             k("2026-05-21", 90, 95, 89, 92)],
            given_price=100,
            prev_state=state(CANCELLED),
            today_date="2026-05-21",
        )
        self.assertEqual(new["state"], UNTRIGGERED)
        self.assertFalse(score)


# ═════════════════════════════════════════════════════════════════════════════
# 2. 旺矽 §3-B 範例 7 天端到端
# ═════════════════════════════════════════════════════════════════════════════

class TestWangXiEndToEndV22(unittest.TestCase):
    """規則 v2.2 §3-B 旺矽範例,p=5380。

    跟 v2.1 差異:
      - 5/14 STANDING 後,5/15 open=5460/close=5450(全部 > 5380)→ leave_up → CANCELLED
        (v2.1 是 MAINTAINING,v2.2 強勢漲離就 cancel)
      - 後續日子若還碰不到 5380 → 都 UNTRIGGERED
      - 直到價格回踩 5380 才能重啟新一輪
    """

    def test_v22_strong_rally_cancels_quickly(self):
        p = 5380
        history = []
        cur = state(UNTRIGGERED)

        def step(date, o, h, low, c, expected_state, expected_score, label=""):
            history.append(k(date, o, h, low, c))
            new, score = evaluate_standing(history, p, cur, date)
            self.assertEqual(new["state"], expected_state,
                f"{date} {label}: state expected {expected_state} got {new['state']}")
            self.assertEqual(score, expected_score,
                f"{date} {label}: should_score expected {expected_score} got {score}")
            return new

        # 5/13 收 5400(touch 5380,close ≥)→ TRIGGERED
        cur = step("2026-05-13", 5350, 5420, 5370, 5400, TRIGGERED, False, "Day 1 touch")
        self.assertEqual(cur["trigger_date"], "2026-05-13")

        # 5/14 open=5450/close=5500 全 ≥ 5380 → STANDING +N
        cur = step("2026-05-14", 5450, 5520, 5440, 5500, STANDING, True, "Day 2 stand")
        self.assertEqual(cur["standing_date"], "2026-05-14")

        # 5/15 open=5460/close=5450 — v2.2 全 > 5380(open > p+ε AND close > p+ε)
        #       → leave_up → CANCELLED
        cur = step("2026-05-15", 5460, 5475, 5440, 5450, CANCELLED, False,
                    "leave_up cancel")

    def test_v22_retouch_after_cancel_can_restart(self):
        """v2.2:CANCELLED 後 K bar 跌回 p → 重啟新 TRIGGER 循環"""
        p = 5380
        history = [
            k("2026-05-14", 5450, 5520, 5440, 5500),   # STANDING
            k("2026-05-15", 5460, 5475, 5440, 5450),   # leave_up → CANCELLED
            k("2026-05-19", 5320, 5410, 5310, 5400),   # 回踩 5380,close ≥
        ]
        new, score = evaluate_standing(
            history, p,
            prev_state=state(CANCELLED, standing_date="2026-05-14"),
            today_date="2026-05-19",
        )
        # CANCELLED 視同 UNTRIGGERED;touch → TRIGGERED
        self.assertEqual(new["state"], TRIGGERED)
        self.assertEqual(new["trigger_date"], "2026-05-19")
        self.assertFalse(score)


# ─────────────────────────────────────────────────────────────────────────────
# v2.2 新增測試:bidirectional cancel + breach event(已在 TestBreakdownV22)
# ─────────────────────────────────────────────────────────────────────────────

class TestV22LeaveUpCancel(unittest.TestCase):
    """v2.2 新增:雙向取消的 leave_up 分支"""

    def test_standing_then_strong_rally_cancels(self):
        """STANDING + 隔天整根 > p → CANCELLED(全在上方)"""
        history = [
            k("2026-05-13", 95, 102, 94, 100),
            k("2026-05-14", 100, 105, 99, 103),   # STANDING (open=100,close=103)
            k("2026-05-15", 110, 115, 108, 112),  # 全 > 100 → leave_up
        ]
        new, score = evaluate_standing(
            history, 100,
            prev_state=state(STANDING, trigger_date="2026-05-13",
                              standing_date="2026-05-14"),
            today_date="2026-05-15",
        )
        self.assertEqual(new["state"], CANCELLED)
        self.assertFalse(score)

    def test_maintaining_then_strong_rally_cancels(self):
        """MAINTAINING + 一天全在上方 → CANCELLED"""
        history = [
            k("2026-05-19", 102, 105, 99, 103),
            k("2026-05-20", 110, 115, 108, 112),
        ]
        new, score = evaluate_standing(
            history, 100,
            prev_state=state(MAINTAINING, standing_date="2026-05-14"),
            today_date="2026-05-20",
        )
        self.assertEqual(new["state"], CANCELLED)
        self.assertFalse(score)

    def test_close_exactly_at_p_no_leave_up(self):
        """close 恰好 = p → leave_up 不成立(避免 ε 誤判)"""
        history = [
            k("2026-05-13", 102, 105, 99, 103),
            k("2026-05-14", 102, 105, 99, 100),   # close=100 == p,不算 leave_up
        ]
        new, score = evaluate_standing(
            history, 100,
            prev_state=state(MAINTAINING),
            today_date="2026-05-14",
        )
        self.assertEqual(new["state"], MAINTAINING)
        self.assertFalse(score)

    def test_open_above_close_below_not_leave(self):
        """open > p, close < p → 一邊上一邊下,K 棒穿過 p,不算 leave"""
        history = [
            k("2026-05-13", 102, 105, 99, 103),
            k("2026-05-14", 101, 105, 95, 98),    # open=101 > p, close=98 < p
        ]
        new, score = evaluate_standing(
            history, 100,
            prev_state=state(MAINTAINING),
            today_date="2026-05-14",
        )
        # consec_down 需 prev close 也 < p,prev close=103 ≥ p → 不 consec_down
        # leave_down 需 open AND close 都 < p,open=101 ≥ p → 不 leave_down
        # leave_up 需 open AND close 都 > p,close=98 < p → 不 leave_up
        # → MAINTAINING
        self.assertEqual(new["state"], MAINTAINING)
        self.assertFalse(score)


# ═════════════════════════════════════════════════════════════════════════════
# 3. 跌破判定(Q6 + 相關)
# ═════════════════════════════════════════════════════════════════════════════

class TestBreakdownV22(unittest.TestCase):
    """規則 v2.2 §3-A:跌破 event = Day 1(碰到 + close ≤ p)+ Day 2(open ≤ p AND close ≤ p)
    新簽名:evaluate_breakdown(today_k, yesterday_k, given_price)
    跟 prev_state 無關(event-based)。
    """

    P = 100

    # ── 正例:Day1 + Day2 都成立 ──
    def test_full_breakdown_event(self):
        """Day 1 碰到 + close ≤ p,Day 2 open ≤ AND close ≤ → True"""
        yesterday = k("D1", 102, 105, 98, 100)   # low ≤ 100 ≤ high,close=100 ≤ 100
        today     = k("D2", 99, 100, 90, 95)     # open ≤ 100, close ≤ 100
        self.assertTrue(evaluate_breakdown(today, yesterday, self.P))

    def test_clear_below_after_touch(self):
        """經典跌破:Day 1 收剛好線上,Day 2 整根跌破"""
        yesterday = k("D1", 101, 102, 99, 100)   # touch + close=100
        today     = k("D2", 95, 99, 90, 92)
        self.assertTrue(evaluate_breakdown(today, yesterday, self.P))

    # ── 反例:Day 1 沒碰到 ──
    def test_day1_no_touch_no_breakdown(self):
        """Day 1 K 棒沒涵蓋 given price → False"""
        yesterday = k("D1", 110, 120, 105, 115)   # 整根 > 100
        today     = k("D2", 95, 99, 90, 92)
        self.assertFalse(evaluate_breakdown(today, yesterday, self.P))

    def test_day1_close_above_no_breakdown(self):
        """Day 1 碰到但 close > p → 站穩雛形,不是跌破"""
        yesterday = k("D1", 99, 102, 95, 101)    # touch but close=101 > 100
        today     = k("D2", 95, 99, 90, 92)
        self.assertFalse(evaluate_breakdown(today, yesterday, self.P))

    # ── 反例:Day 2 沒符合 ──
    def test_day2_open_above_fails(self):
        """Day 2 open > p → 防假跌破"""
        yesterday = k("D1", 101, 102, 99, 100)
        today     = k("D2", 101, 105, 99, 95)    # open=101 > 100
        self.assertFalse(evaluate_breakdown(today, yesterday, self.P))

    def test_day2_close_above_fails(self):
        """Day 2 close > p → 反彈,跌破不成立"""
        yesterday = k("D1", 101, 102, 99, 100)
        today     = k("D2", 95, 102, 90, 101)    # close=101 > 100
        self.assertFalse(evaluate_breakdown(today, yesterday, self.P))

    # ── prev_state 無關(v2.2 改 event)──
    def test_no_prior_standing_still_emits(self):
        """v2.2 改成 event:即使從沒站穩過,Day1+Day2 條件滿足就觸發"""
        yesterday = k("D1", 101, 102, 99, 100)
        today     = k("D2", 95, 99, 90, 92)
        self.assertTrue(evaluate_breakdown(today, yesterday, self.P))

    def test_yesterday_none_no_breakdown(self):
        """沒昨天的 K → 無法判 Day1,回 False"""
        today = k("D2", 95, 99, 90, 92)
        self.assertFalse(evaluate_breakdown(today, None, self.P))

    # ── 邊界:exact equality ──
    def test_exact_equal_satisfies_touch_and_close(self):
        """open = close = p 邊界:條件用 ≤(允許等號)"""
        yesterday = k("D1", 100, 105, 95, 100)   # close=100 ≤ 100 ✓
        today     = k("D2", 100, 102, 90, 100)   # open=100 ≤, close=100 ≤
        self.assertTrue(evaluate_breakdown(today, yesterday, self.P))


# ═════════════════════════════════════════════════════════════════════════════
# 4. State round-trip(JSON 序列化來回,給 W2.1 持久化做 baseline)
# ═════════════════════════════════════════════════════════════════════════════

class TestStateRoundTrip(unittest.TestCase):
    """state dict → JSON → dict 來回一致(future W2.1 SQLite 持久化的前置驗證)"""

    def test_all_five_states_serialize(self):
        for s in (UNTRIGGERED, TRIGGERED, STANDING, MAINTAINING, CANCELLED):
            original = state(s, trigger_date="2026-05-13",
                             standing_date="2026-05-14")
            roundtripped = json.loads(json.dumps(original))
            self.assertEqual(original, roundtripped)

    def test_evaluate_then_serialize_then_continue(self):
        """模擬 W2.1 的工作流:evaluate → 寫 DB → 隔天讀 DB → evaluate"""
        history = [k("2026-05-13", 95, 102, 94, 100)]
        new, _ = evaluate_standing(history, 100, None, "2026-05-13")
        self.assertEqual(new["state"], TRIGGERED)

        # 模擬寫 DB + 讀回(用 JSON 模擬)
        persisted = json.dumps(new)
        new_from_db = json.loads(persisted)

        # 隔天用讀出來的 state 繼續
        history.append(k("2026-05-14", 102, 105, 101, 104))
        new2, score = evaluate_standing(history, 100, new_from_db, "2026-05-14")
        self.assertEqual(new2["state"], STANDING)
        self.assertTrue(score)


# ═════════════════════════════════════════════════════════════════════════════
# 5. 嚴格模式
# ═════════════════════════════════════════════════════════════════════════════

class TestStrictMode(unittest.TestCase):
    """未知 state → raise(per DD2)"""

    def test_unknown_state_raises(self):
        with self.assertRaises(ValueError) as cm:
            evaluate_standing(
                [k("X", 95, 105, 94, 100)],
                100, state("ZOMBIE"), "X",
            )
        self.assertIn("ZOMBIE", str(cm.exception))

    def test_triggered_without_trigger_date_raises(self):
        """TRIGGERED 但 trigger_date=None → days_since 計算錯誤,raise"""
        with self.assertRaises(ValueError):
            evaluate_standing(
                [k("X", 95, 105, 94, 100)],
                100, state(TRIGGERED, trigger_date=None), "X",
            )

    def test_triggered_with_trigger_date_not_in_history_raises(self):
        """trigger_date 不在 price_history → raise(caller 沒撈足夠 K 線)"""
        with self.assertRaises(ValueError) as cm:
            evaluate_standing(
                [k("2026-05-14", 95, 105, 94, 100)],  # 只有 5/14
                100, state(TRIGGERED, trigger_date="2026-05-10"), "2026-05-14",
            )
        self.assertIn("2026-05-10", str(cm.exception))
