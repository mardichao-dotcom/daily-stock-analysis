"""
test_grader.py — 分級門檻單元測試(W2.2.5)

floor 規則鎖死:
  - 5.5  → A(< 6,還沒到 S)
  - 5.99 → A
  - 6.0  → S(>= 6 inclusive)
  - 0    → D
"""
from __future__ import annotations
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.scoring.grader import grade


# 真實 weights.json 的門檻,作為 default 測試 fixture
def load_real_thresholds() -> dict:
    with open(os.path.join(PROJECT_ROOT, "config", "weights.json"),
              encoding="utf-8") as f:
        return json.load(f)["grade"]


class TestGradeBoundaries(unittest.TestCase):
    """各分級邊界 + 5.5 / 5.99 / 6.0 三個關鍵邊界"""

    @classmethod
    def setUpClass(cls):
        cls.T = load_real_thresholds()

    # ── S 級邊界 ──
    def test_6_dot_0_grades_S(self):
        """6.0 是 S(>= 6 inclusive)"""
        self.assertEqual(grade(6.0, self.T), "S")

    def test_999_grades_S(self):
        """任意大值 → S"""
        self.assertEqual(grade(999, self.T), "S")

    # ── A 級邊界(5.5 / 5.99 是用戶 review 鎖死的)──
    def test_5_dot_99_grades_A(self):
        """5.99 仍是 A,floor 嚴格(5.99 < 6)"""
        self.assertEqual(grade(5.99, self.T), "A")

    def test_5_dot_5_grades_A(self):
        """5.5 → A(因 5.5 < 6,floor 規則)"""
        self.assertEqual(grade(5.5, self.T), "A")

    def test_5_dot_0_grades_A(self):
        """5.0 邊界 inclusive → A"""
        self.assertEqual(grade(5.0, self.T), "A")

    # ── B 級邊界 ──
    def test_4_dot_99_grades_B(self):
        self.assertEqual(grade(4.99, self.T), "B")

    def test_4_dot_0_grades_B(self):
        self.assertEqual(grade(4.0, self.T), "B")

    # ── C 級邊界 ──
    def test_3_dot_0_grades_C(self):
        self.assertEqual(grade(3.0, self.T), "C")

    def test_3_dot_5_grades_C(self):
        """3.5 → C(< 4)"""
        self.assertEqual(grade(3.5, self.T), "C")

    # ── D 級邊界 ──
    def test_2_dot_99_grades_D(self):
        self.assertEqual(grade(2.99, self.T), "D")

    def test_0_grades_D(self):
        self.assertEqual(grade(0, self.T), "D")

    def test_0_dot_7_grades_D(self):
        """旺矽 fixture 5/14 那天的 0.7 分 → D 級"""
        self.assertEqual(grade(0.7, self.T), "D")


class TestGradeWithCustomThresholds(unittest.TestCase):
    """thresholds 從外部傳,不依賴 weights.json 結構"""

    def test_custom_thresholds(self):
        """模擬未來規則改門檻"""
        t = {"S": 10, "A": 7, "B": 5, "C": 3, "D": 0}
        self.assertEqual(grade(11, t), "S")
        self.assertEqual(grade(8,  t), "A")
        self.assertEqual(grade(5,  t), "B")
        self.assertEqual(grade(3,  t), "C")
        self.assertEqual(grade(1,  t), "D")

    def test_thresholds_with_added_top_tier(self):
        """模擬未來加 'X' 級"""
        t = {"X": 10, "S": 6, "A": 5, "B": 4, "C": 3, "D": 0}
        # 預期:_GRADE_RANK 沒包含 'X' → grader 不 recognize → 漏判
        # 這個測試鎖死「grader 只認 _GRADE_RANK 列表內的級別」
        # 未來想加 X 級,要先改 grader._GRADE_RANK
        self.assertEqual(grade(15, t), "S")   # X 被跳過,15>=6 落到 S

    def test_missing_threshold_safely_skipped(self):
        """thresholds 缺某級(例如刪掉 'C')→ 跳過,降到下一級"""
        t = {"S": 6, "A": 5, "B": 4, "D": 0}   # 沒 C
        self.assertEqual(grade(3.5, t), "D")   # 沒 C,3.5 落到 D
