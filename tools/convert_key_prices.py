"""
convert_key_prices.py — 從 key_prices_clean_v3.md 產出 config/key_prices.json

用法:
  python3 tools/convert_key_prices.py
  python3 tools/convert_key_prices.py --md path/to/v4.md --out path/to/out.json

輸入: markdown(### [CODE NAME] + 線/區域 表)
輸出: JSON(stocks 內含每檔的 lines + areas + market + sector)

設計:
  - line 沒匹配關鍵字 → 預設 key_price(per spec §3.3)
  - area 沒匹配關鍵字 → skip 並列入 stats(讓使用者抽查)
  - 顯式排除清單(per rule §5):大戶空單成本價、多頭起漲點、起漲K、重要起始K棒、谷貼
  - 排除用「精確子字串」避免「起漲」誤殺「起漲跳空缺口」
  - market 由 symbol prefix 判定:TWSE/TPEX → TW,其他(NYSE/NASDAQ/TSE/OMXCOP) → INTL
  - sector 反查 watchlist.json(台股有,國際留空字串)
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MD   = os.path.join(PROJECT_ROOT, "key_prices_clean_v3.md")
DEFAULT_OUT  = os.path.join(PROJECT_ROOT, "config", "key_prices.json")
WATCHLIST    = os.path.join(PROJECT_ROOT, "config", "watchlist.json")

# ── 類別推斷關鍵字 ────────────────────────────────────────────────────────────
LINE_KEYWORDS = {
    "support_transfer": [
        "撐轉", "前高", "壓力位", "賣壓", "價格轉換", "中繼",
        "支撐", "阻力", "歷史高點", "撐轉位", "深底",
        "短線買盤底部",  # rule B3:短線買盤底部 = 撐轉類
    ],
    "inner_support": ["內撐", "內部撐轉"],
    "whale_cost":    ["大戶成本", "多單大戶成本", "預估大戶成本"],
}

AREA_KEYWORDS = {
    "order_block": ["訂單塊"],   # 各種前綴(賣盤/買盤/底部/起漲/小賣壓...)都會包含「訂單塊」
    "poc":         ["POC", "籌碼集中區"],
    "fvg":         ["FVG"],       # 涵蓋「FVG」「跳空 FVG」「FVG 跳空」
    "gap":         ["跳空缺口"],  # 「多頭跳空缺口」「起漲跳空缺口」都包含「跳空缺口」
    # 2026-07-20 朋友決策:新詞彙區域 + 空文字色塊 一律歸此類,權重照 FVG/POC(=1)
    "break_block": ["破壞塊", "賣壓", "重要撐轉", "短線買盤", "多頭最後防線"],
}

# 排除規則(per rule §5)。用精確子字串,避免誤殺「起漲跳空缺口」
EXCLUDE_SUBSTRINGS = [
    "大戶空單成本價",
    "多頭起漲點",
    "起漲K",
    "起漲 K",
    "重要起始K棒",
    "重要起始 K 棒",
    "谷貼",
]

ADJECTIVE_MAP = {
    "(無)":  None,
    "重要":  "important",
    "小":    "small",
    "短線":  "short_term",
    "預估":  "estimated",
}

COLOR_MAP = {"紅": "red", "黑": "black", "灰": "gray"}

# ── 數字解析 ──────────────────────────────────────────────────────────────────
def parse_number_str(s: str) -> str | None:
    """
    '2,025' → '2025'; '~1,150' → '1150'; '1,235.5' → '1235.5'

    回傳「字串」而非 float。理由(spec W1.5):
      key_prices.json 的 price 是 standing_state 表 composite PK 的識別碼,
      必須 string-stable。原始字串保留(去千分位、波浪號),不轉 float。
    """
    s = s.strip().lstrip("~").replace(",", "").strip()
    # 驗證能解析成數字,但不轉換
    try:
        float(s)
        return s
    except ValueError:
        return None

# ── 形容詞 + 註記拆解 ────────────────────────────────────────────────────────
def parse_adjective(s: str) -> tuple[str | None, list[str]]:
    """'(無) ★邊界不確定' → (None, ['邊界不確定'])"""
    s = s.strip()
    annotations = re.findall(r"★([^★]+)", s)
    annotations = [a.strip() for a in annotations]
    # 去掉所有 ★... 後再判斷形容詞
    core = re.sub(r"★[^★]+", "", s).strip()
    adj = ADJECTIVE_MAP.get(core, None)
    return adj, annotations

# ── 類別推斷 ──────────────────────────────────────────────────────────────────
def is_excluded(text: str) -> str | None:
    for kw in EXCLUDE_SUBSTRINGS:
        if kw in text:
            return kw
    return None

def infer_line_category(text: str) -> str:
    """無匹配時預設 key_price"""
    if not text or text == "(無)":
        return "key_price"
    for cat, keywords in LINE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return cat
    # 純數字 / 「重要 503」這種無類別線索 → key_price
    return "key_price"

def infer_area_category(text: str) -> str | None:
    """無匹配時回 None,讓 caller 跳過並記入 skipped。
    2026-07-20 朋友決策:空文字色塊(無標籤)歸 break_block、文字留空;
    非空但無對映的文字仍回 None(維持安全網,新詞不會被靜默吸收)。"""
    if not text or text == "(無)":
        return "break_block"
    for cat, keywords in AREA_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return cat
    return None

# ── Watchlist 反查 ────────────────────────────────────────────────────────────
def load_sector_lookup() -> dict:
    """code → 台股板塊名(僅台股有,國際回空)"""
    with open(WATCHLIST, encoding="utf-8") as f:
        wl = json.load(f)
    lookup = {}
    for sector_name, sector_data in wl.get("台股板塊", {}).items():
        for member in sector_data.get("成員", []):
            lookup[member["code"]] = sector_name
    return lookup

def determine_market(code: str) -> str:
    return "TW" if code.startswith(("TWSE:", "TPEX:")) else "INTL"

# ── Markdown 解析 ────────────────────────────────────────────────────────────
BLOCK_HEADER_RE = re.compile(r"^### \[([A-Z:0-9_]+)\s+(.+?)\]")
LINE_FIELD_RE   = re.compile(r"價格:\s*([^|]+)\s*\|\s*顏色:\s*([^|]+)\s*\|\s*文字:\s*([^|]*?)\s*\|\s*形容詞:\s*(.+?)\s*$")
AREA_FIELD_RE   = re.compile(r"區域下緣:\s*([^|]+)\s*\|\s*區域上緣:\s*([^|]+)\s*\|\s*文字:\s*([^|]*?)\s*\|\s*形容詞:\s*(.+?)\s*$")

def parse_markdown(md_path: str) -> tuple[dict, dict]:
    """回傳 (stocks_dict, stats_dict)"""
    sector_lookup = load_sector_lookup()
    stocks = {}
    stats = {
        "total_blocks": 0,
        "total_lines": 0,
        "total_areas": 0,
        "skipped_lines": [],   # [(code, text, reason)]
        "skipped_areas": [],   # [(code, text, reason)]
        "defaulted_to_key_price": [],  # [(code, text)] 給人工抽查
        "category_dist_lines": Counter(),
        "category_dist_areas": Counter(),
        "missing_sector": [],  # 在 md 但 watchlist 找不到板塊的台股
        "intl_count": 0,
    }

    current_code = None
    current_section = None   # "lines" / "areas" / None

    with open(md_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")

            # Block header
            m = BLOCK_HEADER_RE.match(line)
            if m:
                code, name = m.group(1).strip(), m.group(2).strip()
                stats["total_blocks"] += 1
                current_code = code
                market = determine_market(code)
                sector = sector_lookup.get(code, "")
                if market == "TW" and not sector:
                    stats["missing_sector"].append(code)
                if market == "INTL":
                    stats["intl_count"] += 1
                stocks[code] = {
                    "name":   name,
                    "sector": sector,
                    "market": market,
                    "lines":  [],
                    "areas":  [],
                }
                current_section = None
                continue

            if current_code is None:
                continue

            # Subsection markers
            if "線(實線)" in line:
                current_section = "lines"
                continue
            if line.strip().startswith("區域:"):
                current_section = "areas"
                continue
            if line.strip().startswith("---"):
                current_code = None
                current_section = None
                continue
            if line.strip().startswith("備註:"):
                continue

            # Data row
            if current_section == "lines" and "價格:" in line:
                m = LINE_FIELD_RE.search(line)
                if not m:
                    continue
                price_s, color_s, text, adj_s = m.groups()
                price = parse_number_str(price_s)
                if price is None:
                    continue
                text = text.strip()
                excl = is_excluded(text)
                if excl:
                    stats["skipped_lines"].append((current_code, text, f"excluded: {excl}"))
                    continue
                category = infer_line_category(text)
                if category == "key_price" and text and text != "(無)":
                    # 非空白非「(無)」但 fallback → 列入抽查清單
                    stripped = re.sub(r"重要\s*", "", text).strip()
                    if not stripped or stripped.replace(".", "").isdigit():
                        pass  # 純數字 fallback 是預期行為,不噴
                    else:
                        stats["defaulted_to_key_price"].append((current_code, text))
                adj, annotations = parse_adjective(adj_s)
                rec = {
                    "price":     price,
                    "color":     COLOR_MAP.get(color_s.strip(), "black"),
                    "text":      text if text and text != "(無)" else None,
                    "adjective": adj,
                    "category":  category,
                }
                if annotations:
                    rec["annotations"] = annotations
                stocks[current_code]["lines"].append(rec)
                stats["total_lines"] += 1
                stats["category_dist_lines"][category] += 1

            elif current_section == "areas" and "區域下緣:" in line:
                m = AREA_FIELD_RE.search(line)
                if not m:
                    continue
                low_s, high_s, text, adj_s = m.groups()
                low, high = parse_number_str(low_s), parse_number_str(high_s)
                if low is None or high is None:
                    continue
                text = text.strip()
                excl = is_excluded(text)
                if excl:
                    stats["skipped_areas"].append((current_code, text, f"excluded: {excl}"))
                    continue
                category = infer_area_category(text)
                if category is None:
                    stats["skipped_areas"].append((current_code, text, "no category match"))
                    continue
                adj, annotations = parse_adjective(adj_s)
                rec = {
                    "low":       low,
                    "high":      high,
                    "text":      text if text and text != "(無)" else None,
                    "adjective": adj,
                    "category":  category,
                }
                if annotations:
                    rec["annotations"] = annotations
                stocks[current_code]["areas"].append(rec)
                stats["total_areas"] += 1
                stats["category_dist_areas"][category] += 1

    return stocks, stats

# ── 寫出 + Stats ──────────────────────────────────────────────────────────────
def write_json(stocks: dict, out_path: str) -> None:
    data = {
        "version":      "v3",
        "updated_at":   "2026-07-20",
        "rule_version": "v2.1",
        "source":       "key_prices_clean_v3.md",
        "_note":        "由 tools/convert_key_prices.py 生成。重跑覆寫。手動編輯會被覆蓋。",
        "stocks":       stocks,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def print_stats(stocks: dict, stats: dict) -> None:
    print("═" * 60)
    print("  convert_key_prices.py 執行摘要")
    print("─" * 60)
    print(f"  總個股 block:    {stats['total_blocks']}")
    print(f"  其中國際:        {stats['intl_count']}")
    print(f"  其中台股:        {stats['total_blocks'] - stats['intl_count']}")
    print(f"  總線數:          {stats['total_lines']}")
    print(f"  總區域數:        {stats['total_areas']}")
    print(f"  排除線:          {len(stats['skipped_lines'])}")
    print(f"  排除/未匹配區域: {len(stats['skipped_areas'])}")
    print("─" * 60)
    print("  線類別分布:")
    for cat, n in stats["category_dist_lines"].most_common():
        print(f"    {cat:<20} {n}")
    print("  區域類別分布:")
    for cat, n in stats["category_dist_areas"].most_common():
        print(f"    {cat:<20} {n}")
    print("─" * 60)

    if stats["missing_sector"]:
        print(f"⚠️  {len(stats['missing_sector'])} 檔台股在 watchlist 找不到板塊:")
        for code in stats["missing_sector"]:
            print(f"    {code}")
        print("─" * 60)

    if stats["defaulted_to_key_price"]:
        print(f"⚠️  {len(stats['defaulted_to_key_price'])} 條線 fallback 為 key_price(請抽查):")
        for code, text in stats["defaulted_to_key_price"]:
            print(f"    {code:<18} text=\"{text}\"")
        print("─" * 60)

    if stats["skipped_lines"]:
        print(f"  排除的線(per rule §5):")
        for code, text, reason in stats["skipped_lines"]:
            print(f"    {code:<18} text=\"{text}\"  ({reason})")
        print("─" * 60)

    if stats["skipped_areas"]:
        print(f"  排除/未匹配的區域:")
        for code, text, reason in stats["skipped_areas"]:
            print(f"    {code:<18} text=\"{text}\"  ({reason})")
        print("─" * 60)

    # 每檔個股的線/區域數
    print("  每檔個股的線/區域數:")
    for code, stock in stocks.items():
        n_lines = len(stock["lines"])
        n_areas = len(stock["areas"])
        if n_lines + n_areas == 0:
            print(f"    ⚠️  {code:<18} {stock['name']:<8} 0 lines / 0 areas (空檔)")
    print("═" * 60)

# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--md",  default=DEFAULT_MD)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()

    if not os.path.exists(args.md):
        print(f"❌ md 檔不存在: {args.md}", file=sys.stderr)
        sys.exit(1)

    stocks, stats = parse_markdown(args.md)

    # W2-4 schema 驗證(審計 2026-07-07):壞資料不落地。
    # 價格可 float、區域 low<high、category 在白名單(config/weights.json given_price)。
    sys.path.insert(0, PROJECT_ROOT)
    from src.key_prices_schema import load_valid_categories, validate_key_prices
    problems = validate_key_prices({"stocks": stocks}, load_valid_categories())
    if problems:
        print(f"\n❌ schema 驗證失敗 {len(problems)} 條,未寫出(修正 md 後重跑):",
              file=sys.stderr)
        for p in problems:
            print(f"   • {p}", file=sys.stderr)
        sys.exit(1)

    write_json(stocks, args.out)
    print_stats(stocks, stats)
    print(f"\n✅ 寫入: {args.out}(schema 驗證通過)")

if __name__ == "__main__":
    main()
