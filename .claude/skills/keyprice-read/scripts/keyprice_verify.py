#!/usr/bin/env python3
"""
keyprice_verify.py — keyprice-read skill 的確定性驗證 helper。

判讀(看圖、下判斷)由 Claude 視覺負責;本 helper 只做「精確查表/查庫」,供三重驗證:
  1. 代號是否在 watchlist(真值 = config/watchlist.json,不是 md 快照)
  2. 該代號 watchlist 名稱(給 Claude 比對圖上名稱)
  3. kline.db 最近收盤(給 Claude 比對圖上現價,>10% → 疑讀錯代號)
     + 近一年 high/low(給價位交叉驗證,>2x 外 → 疑小數點位移/看錯行)

用法:
  python keyprice_verify.py verify 2377 6669 NVDA ...   # 每碼一筆 JSON
  python keyprice_verify.py gen-md                       # 從 watchlist.json 重生 md 快照

★只回事實,不做任何判定/修正——判定與標記由 Claude 依 SKILL.md 規則做。
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]          # …/台股儀表板
WATCHLIST = ROOT / "config" / "watchlist.json"
KLINE = ROOT / "kline.db"
MD_OUT = ROOT / "docs" / "keyprice" / "watchlist_名單.md"
ONE_YEAR_BARS = 250                                 # 近一年交易日近似


def _load_watchlist():
    """回 (entries, meta):entries = [{symbol, code, name, market, group}]。"""
    w = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    entries = []
    for sector, sd in w.get("台股板塊", {}).items():
        for m in sd.get("成員", []):
            sym = m.get("code", "")
            entries.append({"symbol": sym, "code": sym.split(":")[-1],
                            "name": m.get("name", ""),
                            "market": sym.split(":")[0], "group": sector})
    for grp, gd in w.get("國際族群", {}).items():
        for m in gd.get("成員", []):
            sym = m.get("code", "")
            entries.append({"symbol": sym, "code": sym.split(":")[-1],
                            "name": m.get("name", ""),
                            "market": sym.split(":")[0], "group": grp})
    meta = {"更新日期": w.get("更新日期"), "版本": w.get("版本")}
    return entries, meta


def _kline_stats(symbol):
    """回 (latest_date, latest_close, high_1y, low_1y);查無回 (None,None,None,None)。"""
    if not KLINE.exists():
        return (None, None, None, None)
    try:
        conn = sqlite3.connect(f"file:{KLINE}?mode=ro", uri=True)
    except sqlite3.Error:
        return (None, None, None, None)
    row = conn.execute(
        "SELECT date, close FROM kline WHERE symbol=? ORDER BY date DESC LIMIT 1",
        (symbol,)).fetchone()
    rng = conn.execute(
        "SELECT MAX(high), MIN(low) FROM (SELECT high, low FROM kline WHERE symbol=? "
        "ORDER BY date DESC LIMIT ?)", (symbol, ONE_YEAR_BARS)).fetchone()
    conn.close()
    if not row:
        return (None, None, None, None)
    return (row[0], row[1], rng[0] if rng else None, rng[1] if rng else None)


def verify(codes):
    entries, _ = _load_watchlist()
    by_code = {}
    for e in entries:
        by_code.setdefault(e["code"], []).append(e)      # 同碼多市場防護
    out = []
    for raw in codes:
        code = raw.split(":")[-1].strip().upper()
        matches = by_code.get(code) or by_code.get(raw.split(":")[-1].strip()) or []
        if not matches:
            out.append({"input": raw, "code": code, "in_watchlist": False,
                        "watchlist_name": None, "note": "★不在 watchlist(真值 json)→ 需人工確認"})
            continue
        if len(matches) > 1:
            out.append({"input": raw, "code": code, "in_watchlist": True,
                        "ambiguous": [m["symbol"] for m in matches],
                        "note": "★同碼多市場,需人工確認是哪個"})
            continue
        e = matches[0]
        ld, lc, hi, lo = _kline_stats(e["symbol"])
        out.append({
            "input": raw, "symbol": e["symbol"], "code": e["code"],
            "in_watchlist": True, "watchlist_name": e["name"],
            "market": e["market"], "group": e["group"],
            "kline_latest_date": ld, "kline_latest_close": lc,
            "high_1y": hi, "low_1y": lo,
        })
    return out


def gen_md():
    """從 watchlist.json(真值)重生 docs/keyprice/watchlist_名單.md,避免快照過期。"""
    w = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    tw = w.get("台股板塊", {})
    g = w.get("國際族群", {})
    n_tw = sum(len(v.get("成員", [])) for v in tw.values())
    n_g = sum(len(v.get("成員", [])) for v in g.values())
    lines = ["# 追蹤標的名單(watchlist)", "",
             f"> ★由 keyprice_verify.py gen-md 從 config/watchlist.json 自動重生(勿手改)。"
             f"更新日期:{w.get('更新日期','?')} ｜ 共 {n_tw + n_g} 檔"
             f"(台股 {n_tw} + 國際 {n_g})", "",
             "## 🇹🇼 台股(依板塊)", ""]
    for sector, sd in tw.items():
        mem = sd.get("成員", [])
        lines += [f"### {sector}({len(mem)})", "", "| 代號 | 名稱 |", "|---|---|"]
        lines += [f"| {m.get('code','')} | {m.get('name','')} |" for m in mem]
        lines.append("")
    lines += ["## 🌏 國際族群", ""]
    for grp, gd in g.items():
        mem = gd.get("成員", [])
        lines += [f"### {grp}({len(mem)})", "", "| 代號 | 名稱 |", "|---|---|"]
        lines += [f"| {m.get('code','')} | {m.get('name','')} |" for m in mem]
        lines.append("")
    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    MD_OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 重生 {MD_OUT.relative_to(ROOT)}(台股 {n_tw} + 國際 {n_g} = {n_tw + n_g} 檔)")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "gen-md":
        gen_md()
    elif cmd == "verify":
        print(json.dumps(verify(sys.argv[2:]), ensure_ascii=False, indent=2))
    else:
        print(f"未知指令:{cmd}(用 verify / gen-md)", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
