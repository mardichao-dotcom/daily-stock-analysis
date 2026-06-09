"""
偵測 key_prices.json 內「線 vs 區域邊緣 重複」的疑點。

Pattern:朋友畫一個區域(area)有上下緣,然後 lines 又重複寫了上緣 / 下緣的價格。
判讀 prompt 應該擇一(線 OR 區域),不該兩個都寫。

容忍誤差:±0.1(避免浮點誤差誤判)

輸出:
  TWSE:6531 愛普:
    線 1305 (black, key_price) 跟 區域 1240-1305 (賣盤訂單塊) 邊緣重複
    線 791 (black, key_price) 跟 區域 711-791 (買盤訂單塊) 邊緣重複
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KEY_PRICES = PROJECT_ROOT / "config" / "key_prices.json"

TOLERANCE = 0.1  # 容忍誤差(浮點)

def main():
    data = json.loads(KEY_PRICES.read_text(encoding="utf-8"))
    stocks = data.get("stocks", {})

    problems_by_code = []

    for code, info in stocks.items():
        name = info.get("name", "")
        lines = info.get("lines", []) or []
        areas = info.get("areas", []) or []

        if not lines or not areas:
            continue  # 沒線或沒區域,不可能重複

        overlaps = []
        for area in areas:
            try:
                a_low = float(area.get("low", 0))
                a_high = float(area.get("high", 0))
            except (TypeError, ValueError):
                continue

            a_text = area.get("text") or area.get("category", "")

            for line in lines:
                try:
                    l_price = float(line.get("price", 0))
                except (TypeError, ValueError):
                    continue

                l_color = line.get("color", "")
                l_cat = line.get("category", "")

                # 容忍 ±0.1 比對
                if abs(l_price - a_low) < TOLERANCE:
                    overlaps.append({
                        "line_price": l_price,
                        "line_color": l_color,
                        "line_cat": l_cat,
                        "area_low": a_low,
                        "area_high": a_high,
                        "area_text": a_text,
                        "match": "下緣"
                    })
                elif abs(l_price - a_high) < TOLERANCE:
                    overlaps.append({
                        "line_price": l_price,
                        "line_color": l_color,
                        "line_cat": l_cat,
                        "area_low": a_low,
                        "area_high": a_high,
                        "area_text": a_text,
                        "match": "上緣"
                    })

        if overlaps:
            problems_by_code.append((code, name, overlaps))

    if not problems_by_code:
        print("✅ 沒找到「線 vs 區域邊緣 重複」的疑點")
        return

    print(f"找到 {len(problems_by_code)} 檔有疑點:\n")
    print(f"{'':3} {'代號':16} {'名稱':12} {'疑點':3}")
    print("─" * 60)

    for idx, (code, name, overlaps) in enumerate(problems_by_code, 1):
        print(f"{idx:2}. {code:16} {name:12} {len(overlaps)} 個")
        for op in overlaps:
            print(f"     線 {op['line_price']:>10} ({op['line_color']:6}, {op['line_cat']:18}) "
                  f"= 區域 {op['area_low']:>8}-{op['area_high']:<8} ({op['area_text']:12}) "
                  f"{op['match']}重複")
        print()

    print(f"\n總計:{len(problems_by_code)} 檔疑點 / {sum(len(p[2]) for p in problems_by_code)} 個重複 entry")

if __name__ == "__main__":
    main()
