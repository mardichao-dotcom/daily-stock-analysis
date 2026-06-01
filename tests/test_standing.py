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

    def test_d2_stand_day_still_works(self):
        """D2 STAND_DAY 仍視窗內 → STANDING"""
        new, score = evaluate_standing(
            [k("2026-05-13", 95, 102, 94, 100),
             k("2026-05-14", 99, 102, 98, 101),   # mixed
             k("2026-05-15", 102, 105, 101, 104)],  # D2 STAND_DAY
            given_price=100,
            prev_state=state(TRIGGERED, trigger_date="2026-05-13"),
            today_date="2026-05-15",
        )
        self.assertEqual(new["state"], STANDING)
        self.assertTrue(score)

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


class TestQ1Q2TriggeredMixedDays(unittest.TestCase):
    """Q1 / Q2:TRIGGERED + 混合(只滿足開或收一邊)→ 留 TRIGGERED"""

    def test_q1_open_above_close_below_stays_triggered(self):
        """Q1:開 ≥ 收 < → 既不滿足成立、也不滿足當天取消 → 留 TRIGGERED"""
        new, score = evaluate_standing(
            [k("2026-05-13", 95, 102, 94, 100),
             k("2026-05-14", 102, 103, 95, 98)],   # 開 102 ≥ 100, 收 98 < 100
            given_price=100,
            prev_state=state(TRIGGERED, trigger_date="2026-05-13"),
            today_date="2026-05-14",
        )
        self.assertEqual(new["state"], TRIGGERED)
        self.assertEqual(new["trigger_date"], "2026-05-13")   # 保留 trigger_date
        self.assertFalse(score)

    def test_q2_open_below_close_above_stays_triggered(self):
        """Q2:開 < 收 ≥ → 留 TRIGGERED"""
        new, score = evaluate_standing(
            [k("2026-05-13", 95, 102, 94, 100),
             k("2026-05-14", 98, 102, 97, 101)],   # 開 98 < 100, 收 101 ≥ 100
            given_price=100,
            prev_state=state(TRIGGERED, trigger_date="2026-05-13"),
            today_date="2026-05-14",
        )
        self.assertEqual(new["state"], TRIGGERED)
        self.assertFalse(score)


class TestFromStanding(unittest.TestCase):
    """From STANDING(一日狀態,下一個 evaluate 必定離開)"""

    def test_standing_to_maintaining_normal(self):
        new, score = evaluate_standing(
            [k("2026-05-14", 102, 105, 101, 104),
             k("2026-05-15", 103, 105, 102, 104)],
            given_price=100,
            prev_state=state(STANDING, trigger_date="2026-05-13",
                             standing_date="2026-05-14"),
            today_date="2026-05-15",
        )
        self.assertEqual(new["state"], MAINTAINING)
        self.assertEqual(new["standing_date"], "2026-05-14")   # 保留
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

class TestWangXiEndToEnd(unittest.TestCase):
    """規則 §3-B 旺矽範例完整 trace,p=5380"""

    def test_full_trace(self):
        p = 5380
        history = []
        cur = state(UNTRIGGERED)

        def step(date, o, h, low, c, expected_state, expected_score):
            history.append(k(date, o, h, low, c))
            new, score = evaluate_standing(history, p, cur, date)
            self.assertEqual(new["state"], expected_state,
                             f"{date}: state expected {expected_state} got {new['state']}")
            self.assertEqual(score, expected_score,
                             f"{date}: should_score expected {expected_score} got {score}")
            return new

        # 5/13 收 5400 觸發 5380 → TRIGGERED
        cur = step("2026-05-13", 5350, 5420, 5370, 5400, TRIGGERED, False)
        self.assertEqual(cur["trigger_date"], "2026-05-13")

        # 5/14 開+收都 ≥ → STANDING +N
        cur = step("2026-05-14", 5450, 5520, 5440, 5500, STANDING, True)
        self.assertEqual(cur["standing_date"], "2026-05-14")

        # 5/15-5/19 收都在 5380 上 → MAINTAINING
        cur = step("2026-05-15", 5460, 5475, 5440, 5450, MAINTAINING, False)
        cur = step("2026-05-16", 5450, 5470, 5430, 5440, MAINTAINING, False)
        cur = step("2026-05-19", 5440, 5460, 5430, 5450, MAINTAINING, False)

        # 5/20 開+收都 < → CANCELLED + 跌破標籤(獨立函式)
        cur = step("2026-05-20", 5350, 5360, 5290, 5300, CANCELLED, False)
        # 同一天驗證跌破標籤觸發
        self.assertTrue(evaluate_breakdown(
            k("2026-05-20", 5350, 5360, 5290, 5300), p,
            state(MAINTAINING),   # 用 5/19 的 prev_state(MAINTAINING)
        ))

        # 5/21 又 TOUCH → 新 TRIGGERED
        cur = step("2026-05-21", 5320, 5410, 5310, 5400, TRIGGERED, False)
        self.assertEqual(cur["trigger_date"], "2026-05-21")

        # 5/22 STAND_DAY → STANDING again(+N 再次)
        cur = step("2026-05-22", 5440, 5520, 5420, 5500, STANDING, True)


# ═════════════════════════════════════════════════════════════════════════════
# 3. 跌破判定(Q6 + 相關)
# ═════════════════════════════════════════════════════════════════════════════

class TestBreakdown(unittest.TestCase):
    """evaluate_breakdown:獨立函式,只在 STANDING/MAINTAINING 才回 True"""

    P = 100

    def test_untriggered_no_breakdown(self):
        """沒站上過談跌破無意義 → False"""
        self.assertFalse(evaluate_breakdown(
            k("X", 90, 95, 89, 92), self.P, state(UNTRIGGERED)
        ))

    def test_triggered_no_breakdown(self):
        """TRIGGERED 還沒站穩 → False"""
        self.assertFalse(evaluate_breakdown(
            k("X", 90, 95, 89, 92), self.P, state(TRIGGERED, trigger_date="X")
        ))

    def test_cancelled_no_breakdown(self):
        """CANCELLED(剛被取消)再跌一天 → 不再發標籤"""
        self.assertFalse(evaluate_breakdown(
            k("X", 90, 95, 89, 92), self.P, state(CANCELLED)
        ))

    def test_standing_close_below_triggers(self):
        """STANDING + 今天收 < → True"""
        self.assertTrue(evaluate_breakdown(
            k("X", 102, 105, 95, 98), self.P, state(STANDING)
        ))

    def test_maintaining_close_below_triggers(self):
        """MAINTAINING + 今天收 < → True"""
        self.assertTrue(evaluate_breakdown(
            k("X", 102, 105, 95, 98), self.P, state(MAINTAINING)
        ))

    def test_standing_open_below_close_below_triggers(self):
        """規則 §3-A 第二條件:開 < 且 收 < → True(實際被第一條件包含)"""
        self.assertTrue(evaluate_breakdown(
            k("X", 95, 99, 90, 95), self.P, state(STANDING)
        ))

    def test_maintaining_close_above_no_breakdown(self):
        """收還在線上 → False"""
        self.assertFalse(evaluate_breakdown(
            k("X", 98, 105, 95, 102), self.P, state(MAINTAINING)
        ))

    def test_breakdown_with_none_prev(self):
        """prev=None → False"""
        self.assertFalse(evaluate_breakdown(
            k("X", 90, 95, 89, 92), self.P, None
        ))

    def test_breakdown_does_not_modify_state(self):
        """evaluate_breakdown 不改 prev_state(獨立軌道)"""
        prev = state(STANDING, standing_date="2026-05-14")
        evaluate_breakdown(k("X", 90, 95, 89, 92), self.P, prev)
        self.assertEqual(prev["state"], STANDING)
        self.assertEqual(prev["standing_date"], "2026-05-14")


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
