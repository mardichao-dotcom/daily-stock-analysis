"""
formula.py — 規則 v2.1 純加分計分公式(pure function,無 IO,無 config 讀檔)

公式(spec §1.2、rule §2-C):

    final_score = (color_multiplier × base × Π multiply_adjectives)
                  + Σ add_adjectives

其中:
  - color_multiplier 只對「有顏色加成」的線類別(key_price / support_transfer /
    inner_support / whale_cost)生效;其他類別(area / MA)固定為 1.0
  - multiply 形容詞:小 ×0.7、短線 ×1.0、預估 ×0.9
  - add     形容詞:重要 +1(公式末項,在 color × base × multiply 算完之後加)
  - 多形容詞同時存在:multiply 互乘、add 互加

範例(rule §2-C):
  重要紅色關鍵價       (1.5 × 1) + 1                  = 2.5
  小紅色撐轉           (1.5 × 1 × 0.7) + 0            = 1.05
  重要小紅色訂單塊區域 (1.0 × 2 × 0.7) + 1            = 2.4  ← 區域不套顏色
  重要灰色內撐         (0.7 × 1) + 1                  = 1.7
  小黑色 60 日均線     (1.0 × 2 × 0.7) + 0            = 1.4  ← 均線不套顏色

層次設計:
  compute(...)   ← 最純的數學層,單參數型別都是基本數值,完全不知 config 存在
  calculate(...) ← 便利層,接受 (base, color, adjectives, has_color, weights),
                   自己從 weights 查 color_multiplier 跟 adjective 定義
"""
from __future__ import annotations


def compute(
    base: float,
    color_mult: float,
    multiply_factors: list[float],
    add_factors: list[float],
) -> float:
    """純數學層。caller 自己決定所有係數。

    final = (color_mult × base × Π multiply_factors) + Σ add_factors
    """
    multiply_product = 1.0
    for f in multiply_factors:
        multiply_product *= f
    return (color_mult * base * multiply_product) + sum(add_factors)


def calculate(
    base: float,
    color: str | None,
    adjectives: list[str] | None,
    has_color_multiplier: bool,
    weights: dict,
) -> float:
    """便利層。從 weights config 查表後呼叫 compute。

    Parameters
    ----------
    base : float
        基礎分,從 weights["given_price"][<category>] 取得。
    color : str | None
        "red" / "black" / "gray"。當 has_color_multiplier=False 時被忽略,
        傳 None 也可以。
    adjectives : list[str] | None
        形容詞 key 的 list(例:["important", "small"])。None 或空 list 代表無形容詞。
        **嚴格模式**:未知 key 直接 raise ValueError。理由:規則 v2.1 為 FINAL,
        新增形容詞必須走 weights.json 流程,靜默忽略 = 分數錯 = 決策錯。
    has_color_multiplier : bool
        True  = 套顏色加成(線類別:key_price / support_transfer / inner_support / whale_cost)
        False = 不套顏色加成(區域 / MA)
    weights : dict
        已載入的 weights.json dict。
    """
    if has_color_multiplier and color is not None:
        if color not in weights["color_multiplier"]:
            raise ValueError(f"Unknown color: {color!r}")
        color_mult = weights["color_multiplier"][color]
    else:
        color_mult = 1.0

    multiply_factors: list[float] = []
    add_factors:      list[float] = []
    for adj in (adjectives or []):
        if adj not in weights["adjective"]:
            raise ValueError(f"Unknown adjective: {adj!r}")
        adj_def = weights["adjective"][adj]
        if adj_def["type"] == "multiply":
            multiply_factors.append(adj_def["value"])
        elif adj_def["type"] == "add":
            add_factors.append(adj_def["value"])
        else:
            raise ValueError(
                f"Unknown adjective type for {adj!r}: {adj_def['type']!r}"
            )

    return compute(base, color_mult, multiply_factors, add_factors)
