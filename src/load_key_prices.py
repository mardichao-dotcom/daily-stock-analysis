"""
load_key_prices.py — 解析 config/key_prices.txt

get_key_prices(code)  → list of mark dicts，沒有則回 []
get_update_date(code) → "YYYY-MM-DD" 或 None
get_all_key_prices()  → full dict
"""
import os
import re

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "key_prices.txt")

_cache = None


def _parse(path=_CONFIG_PATH):
    result = {}
    current_code = None
    current_marks = []
    current_date = None
    current_section = None

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    def _flush():
        if current_code:
            result[current_code] = {
                "更新日期": current_date or "",
                "marks": list(current_marks),
            }

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # --- 分隔符：flush 目前股票
        if line == "---":
            _flush()
            current_code = None
            current_marks = []
            current_date = None
            current_section = None
            continue

        # # 股票代號: TPEX:6223 旺矽
        m = re.match(r"#\s*股票代號\s*[:：]\s*(\S+)", line)
        if m:
            current_code = m.group(1)
            continue

        # # 更新日期: 2026-05-15
        m = re.match(r"#\s*更新日期\s*[:：]\s*(\S+)", line)
        if m:
            current_date = m.group(1)
            continue

        # ## 水平線 / ## 區域帶 / ## POC（必須在純 # 判斷之前）
        if line.startswith("##"):
            section_name = line.lstrip("#").strip()
            if "水平線" in section_name:
                current_section = "line"
            elif "區域帶" in section_name:
                current_section = "zone"
            elif "POC" in section_name:
                current_section = "poc"
            else:
                current_section = None
            continue

        # 其他 # 開頭的行（一般註解）
        if line.startswith("#"):
            continue

        if current_section is None or "|" not in line:
            continue

        value_part, label = [x.strip() for x in line.split("|", 1)]

        if current_section == "line":
            try:
                price = float(value_part)
                current_marks.append({"type": "line", "price": price, "label": label})
            except ValueError:
                pass

        elif current_section == "zone":
            # 下緣-上緣
            m = re.match(r"([\d.]+)-([\d.]+)", value_part)
            if m:
                low, high = float(m.group(1)), float(m.group(2))
                current_marks.append({"type": "zone", "low": low, "high": high, "label": label})

        elif current_section == "poc":
            # POC 可能是單值或區間
            m = re.match(r"([\d.]+)-([\d.]+)", value_part)
            if m:
                low, high = float(m.group(1)), float(m.group(2))
                current_marks.append({"type": "zone", "low": low, "high": high,
                                      "label": label, "is_poc": True})
            else:
                try:
                    price = float(value_part)
                    current_marks.append({"type": "line", "price": price,
                                          "label": label, "is_poc": True})
                except ValueError:
                    pass

    _flush()
    return result


def _get_cache():
    global _cache
    if _cache is None:
        _cache = _parse()
    return _cache


def get_all_key_prices():
    return _get_cache()


def get_key_prices(code):
    return _get_cache().get(code, {}).get("marks", [])


def get_update_date(code):
    return _get_cache().get(code, {}).get("更新日期", None)
