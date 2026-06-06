"""
add_symbols_batch.py — 批次新增 watchlist + key_prices 個股

業務背景:
  朋友規則 v2.2 上線後 5 週擴 200 檔台股(週 30 檔)。
  手動 7 步驟流程 30 分鐘/檔太慢,本腳本一次處理整批。

CLI(也由 scripts/add_symbols_batch.sh wrap):
  python3 -m src.add_symbols_batch new_symbols.json
      → dry-run(只 validate + 印 plan,不動檔案)
  python3 -m src.add_symbols_batch new_symbols.json --apply
      → 真執行(過 confirm prompt 才繼續)
  python3 -m src.add_symbols_batch new_symbols.json --apply --no-confirm
      → 不問,直接跑(供 CI/script 用)

預設關閉昂貴步驟(tv_collect / rebuild / re-render),用 --do-tv-collect /
--do-rebuild / --do-rerender 開啟,讓 dry-run 跟 --apply 都能快速完成
檔案層面的變更,昂貴後處理交給用戶決定何時跑。

輸入 JSON 結構(用戶寫):
  [
    {
      "code": "TWSE:2454",
      "name": "聯發科",
      "sector": "IC設計",
      "key_prices": {
        "lines": [
          {"price": 1100, "category": "關鍵價格", "color": "red"},
          {"price": 1050, "category": "MA60", "color": "black"}
        ],
        "areas": [
          {"low": 1020, "high": 1080, "category": "訂單塊"}
        ]
      }
    }
  ]

  - key_prices 可省略(先加 watchlist,之後補)
  - category 接受中文或英文(內部 normalize 到英文 key)
  - price/low/high 接受 int / float / str(內部轉 str 跟既有 key_prices.json 一致)
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHLIST  = PROJECT_ROOT / "config" / "watchlist.json"
DEFAULT_KEY_PRICES = PROJECT_ROOT / "config" / "key_prices.json"
DEFAULT_SECTORS    = PROJECT_ROOT / "config" / "sectors.json"


CODE_RE = re.compile(r"^(TWSE|TPEX|NASDAQ|NYSE|TSE|OMXCOP|KRX|KOSPI|KOSDAQ):[\w\.\-]+$")


# 朋友規則使用的 category(中→英)。input 接受中文,內部寫英文 key。
CATEGORY_MAP = {
    "關鍵價格":     "key_price",     "key_price":        "key_price",
    "內撐":         "inner_support", "inner_support":    "inner_support",
    "撐轉":         "support_transfer", "support_transfer": "support_transfer",
    "大戶成本":     "whale_cost",    "whale_cost":       "whale_cost",
    "MA20":         "ma_20",         "ma_20":            "ma_20",
    "MA60":         "ma_60",         "ma_60":            "ma_60",
    "MA90":         "ma_90",         "ma_90":            "ma_90",
    "訂單塊":       "order_block",   "order_block":      "order_block",
    "POC":          "poc",           "poc":              "poc",
    "FVG":          "fvg",           "fvg":              "fvg",
    "跳空缺口":     "gap",           "gap":              "gap",
}


# ── exceptions ───────────────────────────────────────────────────────────────

class ValidationError(Exception):
    """所有 validate 錯誤集合。"""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


# ── IO helpers ───────────────────────────────────────────────────────────────

def load_json(path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json_atomic(path, data) -> None:
    """temp file + rename 原子寫,避免半寫狀態。"""
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


# ── normalize ────────────────────────────────────────────────────────────────

def normalize_category(name: str) -> str:
    """Chinese → English;未知 category 原樣 passthrough(validate 不阻擋)。"""
    return CATEGORY_MAP.get(name, name)


def normalize_entry(entry: dict) -> dict:
    """把用戶 input 轉為內部格式(price 變字串 / category 變英文 key)。"""
    code   = entry["code"].strip()
    name   = entry.get("name", "").strip()
    sector = entry.get("sector", "").strip()
    kp_raw = entry.get("key_prices") or {}

    lines = []
    for ln in kp_raw.get("lines", []):
        lines.append({
            "price":     str(ln["price"]),
            "color":     ln.get("color", "black"),
            "text":      ln.get("text"),
            "adjective": ln.get("adjective"),
            "category":  normalize_category(ln.get("category", "key_price")),
        })
    areas = []
    for a in kp_raw.get("areas", []):
        areas.append({
            "low":       str(a["low"]),
            "high":      str(a["high"]),
            "text":      a.get("text"),
            "adjective": a.get("adjective"),
            "category":  normalize_category(a.get("category", "order_block")),
        })

    return {
        "code":       code,
        "name":       name,
        "sector":     sector,
        "key_prices": {"lines": lines, "areas": areas},
    }


# ── validate ─────────────────────────────────────────────────────────────────

def collect_existing_codes(watchlist: dict) -> set[str]:
    codes: set[str] = set()
    for sec in watchlist.get("台股板塊", {}).values():
        for m in sec.get("成員", []):
            codes.add(m["code"])
    for grp in watchlist.get("國際族群", {}).values():
        for m in grp.get("成員", []):
            codes.add(m["code"])
    return codes


def collect_valid_sectors(watchlist: dict, sectors: dict) -> set[str]:
    """合法 sector = sectors.json 內的 sectors keys ∪ watchlist 既有板塊名"""
    s = set(sectors.get("sectors", {}).keys())
    s |= set(watchlist.get("台股板塊", {}).keys())
    return s


def validate(entries: list[dict], valid_sectors: set[str],
              existing_codes: set[str]) -> list[str]:
    """Return list of error strings. Empty list = valid。"""
    errors: list[str] = []
    seen_in_batch: set[str] = set()

    for i, e in enumerate(entries):
        prefix = f"#{i}"
        code = (e.get("code") or "").strip()
        if not code:
            errors.append(f"{prefix}: 缺 code 欄位")
            continue
        if not CODE_RE.match(code):
            errors.append(
                f"{prefix}: code 格式錯誤 {code!r}"
                f"(預期 TWSE:1234 / TPEX:1234 / NASDAQ:NVDA 等)"
            )
        if code in seen_in_batch:
            errors.append(f"{prefix}: 重複 code {code!r}(同批次出現多次)")
        seen_in_batch.add(code)
        if code in existing_codes:
            errors.append(f"{prefix}: {code!r} 已存在於 watchlist")

        if not (e.get("name") or "").strip():
            errors.append(f"{prefix}: 缺 name")

        sector = (e.get("sector") or "").strip()
        if not sector:
            errors.append(f"{prefix}: 缺 sector")
        elif sector not in valid_sectors:
            errors.append(
                f"{prefix}: 未知 sector {sector!r}"
                f"(請看 config/sectors.json `sectors` keys 或 config/watchlist.json 板塊名)"
            )

        kp = e.get("key_prices")
        if kp is not None:
            if not isinstance(kp, dict):
                errors.append(f"{prefix}: key_prices 必須是 dict 或省略")
            else:
                for j, ln in enumerate(kp.get("lines", [])):
                    if "price" not in ln:
                        errors.append(f"{prefix} line[{j}]: 缺 price")
                for j, a in enumerate(kp.get("areas", [])):
                    if "low" not in a or "high" not in a:
                        errors.append(f"{prefix} area[{j}]: 缺 low/high")

    return errors


# ── mutate ───────────────────────────────────────────────────────────────────

def add_to_watchlist(watchlist: dict, entries: list[dict]) -> dict:
    """Append 新個股到對應 sector 的 成員 list(in-place + return)。"""
    for entry in entries:
        sector_name = entry["sector"]
        sec = watchlist["台股板塊"].get(sector_name)
        if sec is None:
            raise ValueError(f"sector {sector_name!r} 不在 台股板塊 內")
        sec["成員"].append({"code": entry["code"], "name": entry["name"]})
    return watchlist


def add_to_key_prices(key_prices: dict, entries: list[dict]) -> dict:
    """Add 新 entry 到 stocks dict(in-place + return)。"""
    stocks = key_prices.setdefault("stocks", {})
    for entry in entries:
        code = entry["code"]
        market = "TW" if code.startswith(("TWSE:", "TPEX:")) else "INTL"
        stocks[code] = {
            "name":   entry["name"],
            "sector": entry["sector"],
            "market": market,
            "lines":  entry["key_prices"]["lines"],
            "areas":  entry["key_prices"]["areas"],
        }
    return key_prices


# ── backup / rollback ────────────────────────────────────────────────────────

def backup_files(paths, ts: str | None = None) -> dict[str, str]:
    """copy2 各檔到 <path>.bak-<ts>。回 {orig: backup} dict。"""
    ts = ts or datetime.now().strftime("%Y%m%d-%H%M%S")
    backups: dict[str, str] = {}
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        backup = Path(str(p) + f".bak-{ts}")
        shutil.copy2(p, backup)
        backups[str(p)] = str(backup)
    return backups


def rollback(backups: dict[str, str]) -> None:
    """Restore 原檔。失敗的 backup(已被刪)skip。"""
    for orig, backup in backups.items():
        if os.path.exists(backup):
            shutil.copy2(backup, orig)


# ── pipeline ─────────────────────────────────────────────────────────────────

def run_pipeline(
    *,
    entries:       list[dict],
    watchlist_path = DEFAULT_WATCHLIST,
    key_prices_path = DEFAULT_KEY_PRICES,
    sectors_path    = DEFAULT_SECTORS,
    apply:         bool = False,
    do_tv_collect: bool = False,
    do_rebuild:    bool = False,
    do_rerender:   bool = False,
    _runner = subprocess.run,           # 給 tests mock
    _print  = print,
) -> dict:
    """主流程。回傳 {ok, validated, normalized, backups, errors, ran_steps}.

    failures 中途 → 嘗試 rollback,errors 內含明細。
    """
    result: dict = {"ok": True, "errors": [], "ran_steps": [], "backups": {}}

    # ── step 1: load + validate ────────────────────────────────────────────
    watchlist  = load_json(watchlist_path)
    key_prices = load_json(key_prices_path)
    sectors    = load_json(sectors_path)
    valid_sectors  = collect_valid_sectors(watchlist, sectors)
    existing_codes = collect_existing_codes(watchlist)

    errors = validate(entries, valid_sectors, existing_codes)
    if errors:
        result["ok"] = False
        result["errors"] = errors
        return result
    result["ran_steps"].append("validate")

    normalized = [normalize_entry(e) for e in entries]
    result["normalized"] = normalized

    if not apply:
        _print(f"[dry-run] 通過 validate({len(normalized)} 檔)。未實際修改。")
        for e in normalized:
            _print(f"  + {e['code']:14s} {e['name']:14s} [{e['sector']}]  "
                   f"lines={len(e['key_prices']['lines'])} areas={len(e['key_prices']['areas'])}")
        return result

    # ── step 2: backup ─────────────────────────────────────────────────────
    backups = backup_files([watchlist_path, key_prices_path])
    result["backups"] = backups
    result["ran_steps"].append("backup")
    _print(f"[backup] {len(backups)} 檔 backup 完成")

    # ── step 3+4: 寫 watchlist + key_prices(失敗 → rollback)──────────────
    try:
        watchlist  = add_to_watchlist(watchlist, normalized)
        save_json_atomic(watchlist_path, watchlist)
        result["ran_steps"].append("watchlist_written")
        _print(f"[watchlist] +{len(normalized)} 檔 → {watchlist_path}")

        key_prices = add_to_key_prices(key_prices, normalized)
        save_json_atomic(key_prices_path, key_prices)
        result["ran_steps"].append("key_prices_written")
        _print(f"[key_prices] +{len(normalized)} 檔 → {key_prices_path}")
    except Exception as ex:
        result["ok"] = False
        result["errors"].append(f"寫檔失敗:{ex}")
        rollback(backups)
        result["ran_steps"].append("rollback")
        _print(f"[rollback] 已從 backup 還原:{ex}")
        return result

    # ── step 5: tv_collect 新 symbols(慢,預設 off)─────────────────────
    if do_tv_collect:
        for entry in normalized:
            try:
                _runner(
                    ["node", str(PROJECT_ROOT / "scripts" / "tv_collect.mjs"),
                     "--symbol", entry["code"], "--timeout-min", "10"],
                    check=True, cwd=str(PROJECT_ROOT),
                )
            except subprocess.CalledProcessError as ex:
                result["ok"] = False
                result["errors"].append(f"tv_collect {entry['code']} 失敗:{ex}")
                rollback(backups)
                result["ran_steps"].append("rollback")
                _print(f"[rollback] tv_collect 失敗,還原:{ex}")
                return result
            # 2026-06-03 bug fix:tv_collect 每跑一檔就把 /tmp/tv_daily_data.json
            # 整個覆寫(不是 append)。loop 結束才呼叫 import_kline 會只 import
            # 最後一檔。修補:每跑完一檔立刻 import。
            try:
                _runner(
                    ["python3", str(PROJECT_ROOT / "src" / "import_kline.py")],
                    check=True, cwd=str(PROJECT_ROOT),
                )
            except subprocess.CalledProcessError as ex:
                result["ok"] = False
                result["errors"].append(
                    f"import_kline ({entry['code']}) 失敗:{ex}"
                )
                rollback(backups)
                result["ran_steps"].append("rollback")
                _print(f"[rollback] import_kline 失敗,還原:{ex}")
                return result
        result["ran_steps"].append("tv_collect")

    # ── step 6: 增量補新檔的歷史 standing_state(慢,預設 off)─────────
    # 2026-06-02:從「全量重跑(刪所有 state + 重跑全 watchlist × 121 天)」
    # 改用 run_filters_v2 --incremental --new-symbols X,Y,既有 symbols 完全
    # 不動。對 N 個新檔 N << watchlist 時節省約 watchlist/N 倍時間。
    # 之後仍要對最後一天(今天)跑一次全量,讓族群連動 / 排名正確 — 由
    # step 7 (--do-rerender) 順帶處理(它的 render 自然會帶到正確的當日計分)。
    if do_rebuild:
        try:
            import sqlite3 as _sql
            conn = _sql.connect(PROJECT_ROOT / "kline.db")
            # 只取新檔在 kline.db 內有資料的日期(節省「沒這個 symbol 的日子」浪費)
            placeholders = ",".join("?" * len(normalized))
            codes = [e["code"] for e in normalized]
            dates = [r[0] for r in conn.execute(
                f"SELECT DISTINCT date FROM kline WHERE symbol IN ({placeholders}) "
                f"ORDER BY date",
                codes,
            )]
            conn.close()
            new_syms_arg = ",".join(codes)
            _print(f"[rebuild] 對 {len(codes)} 個新檔增量跑 {len(dates)} 天")
            for d in dates:
                _runner(["python3", "-m", "src.run_filters_v2",
                         "--date", d, "--output", f"/tmp/filt_{d}.json",
                         "--incremental", "--new-symbols", new_syms_arg],
                        check=True, cwd=str(PROJECT_ROOT),
                        stdout=subprocess.DEVNULL)
            result["ran_steps"].append("rebuild")
        except subprocess.CalledProcessError as ex:
            result["ok"] = False
            result["errors"].append(f"rebuild 失敗:{ex}")
            return result

    # ── step 7: 重 render 最近 10 天 + 入口 ─────────────────────────────
    if do_rerender:
        try:
            # 最近 10 個交易日 snapshot
            import sqlite3
            conn = sqlite3.connect(PROJECT_ROOT / "kline.db")
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT date FROM kline ORDER BY date DESC LIMIT 10"
            )]
            conn.close()
            for d in reversed(dates):
                _runner(["python3", "-m", "src.render_v2",
                         "--date", d, "--output", f"docs/index_v2_{d}.html"],
                        check=True, cwd=str(PROJECT_ROOT),
                        stdout=subprocess.DEVNULL)
            # live + watchlist + history + landing
            # 注意 per-module CLI 不同:
            #   render_watchlist_v2 接 --date
            #   render_history      不接 --date(掃 docs/ 內 snapshot)
            #   render_landing      不接 --date(讀 filtered_result_v2.json)
            latest = dates[0]
            shutil.copy(PROJECT_ROOT / "docs" / f"index_v2_{latest}.html",
                        PROJECT_ROOT / "docs" / "index_v2.html")
            _runner(["python3", "-m", "src.render_watchlist_v2",
                     "--date", latest],
                    check=True, cwd=str(PROJECT_ROOT),
                    stdout=subprocess.DEVNULL)
            _runner(["python3", "-m", "src.render_history"],
                    check=True, cwd=str(PROJECT_ROOT),
                    stdout=subprocess.DEVNULL)
            _runner(["python3", "-m", "src.render_landing"],
                    check=True, cwd=str(PROJECT_ROOT),
                    stdout=subprocess.DEVNULL)
            result["ran_steps"].append("rerender")
        except subprocess.CalledProcessError as ex:
            result["ok"] = False
            result["errors"].append(f"re-render 失敗:{ex}")
            return result

    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description="批次新增 watchlist + key_prices 個股")
    p.add_argument("input_json", help="新個股 JSON 檔(陣列)")
    p.add_argument("--apply",       action="store_true",
                    help="真執行(預設 dry-run)")
    p.add_argument("--no-confirm",  action="store_true",
                    help="--apply 時跳過 y/N 確認(供 CI/script 用)")
    p.add_argument("--do-tv-collect", action="store_true",
                    help="加完檔後跑 tv_collect 抓新 symbol 的 K 線歷史(慢)")
    p.add_argument("--do-rebuild",    action="store_true",
                    help="加完檔後重跑全歷史 standing_state(很慢)")
    p.add_argument("--do-rerender",   action="store_true",
                    help="加完檔後重 render 最近 10 天 snapshot + live")
    p.add_argument("--watchlist",  default=str(DEFAULT_WATCHLIST))
    p.add_argument("--key-prices", default=str(DEFAULT_KEY_PRICES))
    p.add_argument("--sectors",    default=str(DEFAULT_SECTORS))
    args = p.parse_args(argv)

    try:
        entries = load_json(args.input_json)
    except FileNotFoundError:
        print(f"❌ 找不到輸入檔:{args.input_json}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as ex:
        print(f"❌ JSON 解析失敗:{ex}", file=sys.stderr)
        return 2

    if not isinstance(entries, list):
        print("❌ 輸入必須是陣列(list of dicts)", file=sys.stderr)
        return 2

    print(f"輸入 {len(entries)} 檔。{'執行模式 ⚠️' if args.apply else '🟡 dry-run 模式'}")

    # validate 通過再走 confirm
    if args.apply and not args.no_confirm:
        # 先做一次 dry-run 顯示計畫,再 confirm
        dry = run_pipeline(
            entries=entries,
            watchlist_path=args.watchlist,
            key_prices_path=args.key_prices,
            sectors_path=args.sectors,
            apply=False,
        )
        if not dry["ok"]:
            print("\n❌ Validate 失敗:")
            for e in dry["errors"]:
                print(f"  • {e}")
            return 1
        print()
        ans = input(f"⚠️ 將新增 {len(entries)} 檔。確認嗎? [y/N] ").strip().lower()
        if ans != "y":
            print("已取消。")
            return 0

    result = run_pipeline(
        entries=entries,
        watchlist_path=args.watchlist,
        key_prices_path=args.key_prices,
        sectors_path=args.sectors,
        apply=args.apply,
        do_tv_collect=args.do_tv_collect,
        do_rebuild=args.do_rebuild,
        do_rerender=args.do_rerender,
    )

    if not result["ok"]:
        print("\n❌ 失敗:")
        for e in result["errors"]:
            print(f"  • {e}")
        return 1

    print(f"\n✅ 完成。Steps: {result['ran_steps']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
