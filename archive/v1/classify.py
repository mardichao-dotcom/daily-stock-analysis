"""
classify.py — 分級 + 特殊規則（依積分制定案 v1 第 3、5 節）

Grade thresholds:
  ≥ 6   → S級
  4~5   → A級
  0~3   → 中性
  -1~-3 → 警報
  ≤ -4  → 黑名單

Special rules (override score):
  ETF共識減碼 ≥ 2 → 強制黑名單
  跌停 → 強制不進 S/A （最高中性）
  is_lone_wolf + S/A → 附加「單兵作戰」標籤（不改分級）
"""


def _score_to_grade(score):
    if score >= 6:
        return "S級"
    if score >= 4:
        return "A級"
    if score >= 0:
        return "中性"
    if score >= -3:
        return "警報"
    return "黑名單"


def classify(d):
    """
    Returns (grade: str, tags: list[str])
    """
    score = d.get("score", 0)
    tags = []

    # Special rule 1: ETF共識減碼 ≥ 2 → 強制黑名單
    if d.get("etf_consensus_sell_count", 0) >= 2:
        return "黑名單", ["ETF共識減碼≥2"]

    grade = _score_to_grade(score)

    # Special rule 2: 跌停 → 強制不進 S/A
    if d.get("is_limit_down"):
        if grade in ("S級", "A級"):
            grade = "中性"
            tags.append("跌停降級")

    # Special rule 3: 單兵作戰標記
    if d.get("is_lone_wolf") and grade in ("S級", "A級"):
        tags.append("單兵作戰")

    return grade, tags


def classify_all(tw_data):
    """Adds grade + tags to each entry. Returns tw_data."""
    for symbol, d in tw_data.items():
        grade, tags = classify(d)
        d["grade"] = grade
        d["tags"] = tags
    return tw_data
