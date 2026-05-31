"""
grader.py — 分級套門檻(W2.2.5)

純函式 + 純算術:接收 score (float) + thresholds (dict) → 回 "S"/"A"/"B"/"C"/"D"。

設計(per W2.2 設計 review):
  - **floor 規則,字面對齊 weights.json**
    spec §1.8: S ≥ 6 / A = 5 / B = 4 / C = 3 / D ≤ 2
    weights["grade"]: {"S": 6, "A": 5, "B": 4, "C": 3, "D": 0}
  - 5.5 → A(不是 S,因 5.5 < 6)
  - 5.99 → A
  - 6.0  → S(>= 6 inclusive)
  - 字串比較反向陷阱:不直接用 score >= thresh,先按 rank 排序

設計理由(W2.2.5 review 鎖死):
  - 字面對齊 weights.json 數字
  - 未來改門檻只動 JSON
  - if/elif 鏈可預測性高
"""
from __future__ import annotations


# 標準分級順序(高到低)
_GRADE_RANK = ("S", "A", "B", "C", "D")


def grade(score: float, thresholds: dict) -> str:
    """套分級門檻。floor 規則:取「符合條件的最高級」。

    Parameters
    ----------
    score : float
        個股總分(任何 float ≥ 0;純加分制下不會負數)
    thresholds : dict
        {"S": 6, "A": 5, "B": 4, "C": 3, "D": 0}
        從 weights["grade"] 取得

    Returns
    -------
    str : "S" / "A" / "B" / "C" / "D"

    Examples
    --------
    >>> t = {"S": 6, "A": 5, "B": 4, "C": 3, "D": 0}
    >>> grade(6.0, t)  # 邊界 inclusive
    'S'
    >>> grade(5.99, t)  # floor,還沒到 S
    'A'
    >>> grade(5.5, t)
    'A'
    >>> grade(0, t)
    'D'
    """
    for g in _GRADE_RANK:
        if g not in thresholds:
            continue
        if score >= thresholds[g]:
            return g
    return "D"   # safety net(理論上不可達,thresholds["D"]=0 已 catch)
