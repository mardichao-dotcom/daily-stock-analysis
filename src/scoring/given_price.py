"""
given_price.py — 給定價格計分(規則 §1-D)

純函式。本層**不做狀態機判定**,狀態機(觸發 / 維持 / 取消)由 W1.5 standing.py 處理,
本層接受 should_score 旗標,呼叫者決定要不要算分。

9 種給定價格類別(spec §1.3 / rule §1-D):

  線(有顏色加成):
    key_price          +1   (套顏色 × 形容詞公式)
    support_transfer   +1   (套顏色 × 形容詞公式)
    inner_support      +1   (套顏色 × 形容詞公式)
    whale_cost         +1   (套顏色 × 形容詞公式)

  線(無顏色加成,均線):
    ma_20              +1
    ma_60              +2
    ma_90              +2

  區域(無顏色加成):
    order_block        +2
    poc                +1
    fvg                +1
    gap                +1

「有顏色加成」名單由 weights["color_multiplier"]["_lines_with_color"] 控制,
未來加新類別只動 weights.json。

嚴格模式:未知 category → ValueError(per DD2)
"""
from __future__ import annotations
from . import formula


def _wrap_adj(adj: str | None) -> list[str]:
    """key_prices.json 一條 row 只有單一形容詞(可能 None);包成 list 給 formula 用"""
    return [adj] if adj else []


def _has_color_multiplier(category: str, weights: dict) -> bool:
    """category 是否落在「套顏色公式」清單內"""
    return category in weights["color_multiplier"]["_lines_with_color"]


def _validate_category(category: str, weights: dict) -> None:
    """嚴格模式:未知 category 立刻 raise"""
    if category not in weights["given_price"]:
        raise ValueError(
            f"Unknown given_price category: {category!r} "
            f"(known: {sorted(weights['given_price'].keys())})"
        )


def score_line(
    line: dict,
    should_score: bool,
    weights: dict,
) -> tuple[float, list[dict]]:
    """單一條線的計分。

    Parameters
    ----------
    line : dict
        必含: category (str), price (float), color (str | None), adjective (str | None)
        選含: text (str | None) — 給 evidence 用
    should_score : bool
        通常由 W1.5 standing 狀態機決定(首次站穩當天為 True,其他為 False)。
        本層不檢查條件,純粹遵照旗標。
    weights : dict
        已載入的 weights.json
    """
    if not should_score:
        return 0.0, []

    category = line["category"]
    _validate_category(category, weights)

    base      = weights["given_price"][category]
    has_color = _has_color_multiplier(category, weights)
    color     = line.get("color") if has_color else None
    adj       = line.get("adjective")

    s = formula.calculate(
        base=base,
        color=color,
        adjectives=_wrap_adj(adj),
        has_color_multiplier=has_color,
        weights=weights,
    )

    return s, [{
        "reason":   _build_reason(line, category, color, adj, kind="line"),
        "score":    s,
        "evidence": {
            "kind":      "line",
            "category":  category,
            "price":     line.get("price"),
            "color":     color,
            "adjective": adj,
            "text":      line.get("text"),
        },
    }]


def score_area(
    area: dict,
    should_score: bool,
    weights: dict,
) -> tuple[float, list[dict]]:
    """單一區域的計分。

    區域固定不套顏色加成(rule §2-B),即使資料有 color 欄位也忽略。

    Parameters
    ----------
    area : dict
        必含: category, low, high, adjective(可 None)
        選含: text
    should_score : bool
        同 score_line
    weights : dict
        已載入的 weights.json
    """
    if not should_score:
        return 0.0, []

    category = area["category"]
    _validate_category(category, weights)

    base = weights["given_price"][category]
    adj  = area.get("adjective")

    s = formula.calculate(
        base=base,
        color=None,                # 區域不套顏色
        adjectives=_wrap_adj(adj),
        has_color_multiplier=False,
        weights=weights,
    )

    return s, [{
        "reason":   _build_reason(area, category, None, adj, kind="area"),
        "score":    s,
        "evidence": {
            "kind":      "area",
            "category":  category,
            "low":       area.get("low"),
            "high":      area.get("high"),
            "adjective": adj,
            "text":      area.get("text"),
        },
    }]


def _build_reason(item: dict, category: str, color: str | None,
                  adj: str | None, kind: str) -> str:
    """組 tooltip 用的中文 reason 字串"""
    text = item.get("text") or "(無標注)"
    parts = [f"{category}({text})"]
    if color:
        parts.append(f"顏色={color}")
    if adj:
        parts.append(f"形容詞={adj}")
    parts.append(f"[{kind}]")
    return " ".join(parts)
