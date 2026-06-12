"""
site_meta.py — 渲染單一資料源(P1 §6.3)

render 流程先產 docs/data/v2/{date}/site_meta.json,四個頁面模板的 meta 列
**只准**從這個物件取值,移除所有硬寫數字 / 版本 / 更新日。解決:
  - 檔數 97/98/131 不一致(§6.1 #4):tw_count / intl_count / total_count 單一定義
  - 版本硬寫 v2.1(§6.1 #5):rule_version 由 config/sectors.json 單一來源讀取
  - watchlist 更新日 stale 2026-05-14(§6.1 #6):watchlist_updated 由 watchlist.json
    實際 mtime 動態讀取,不再硬寫、也不依賴未被維護的內嵌欄位
  - skip 透明化(§6.2):skipped 來自 filtered_result_v2 metadata.skipped_symbols

欄位:{data_date, rule_version, tw_count, intl_count, total_count, skipped[],
       generated_at, watchlist_updated}
"""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TZ_TAIPEI = timezone(timedelta(hours=8))

DEFAULT_WATCHLIST = PROJECT_ROOT / "config" / "watchlist.json"
DEFAULT_SECTORS   = PROJECT_ROOT / "config" / "sectors.json"
DEFAULT_RESULT    = PROJECT_ROOT / "filtered_result_v2.json"
DEFAULT_OUTDIR    = PROJECT_ROOT / "docs" / "data" / "v2"


def _count_members(group: dict) -> int:
    return sum(len(sec.get("成員", [])) for sec in group.values())


def _mtime_date(path: Path) -> str:
    """檔案 mtime 的台北日期(YYYY-MM-DD)。"""
    ts = os.path.getmtime(path)
    return datetime.fromtimestamp(ts, TZ_TAIPEI).strftime("%Y-%m-%d")


def build(date: str, *, watchlist: dict, sectors: dict,
          filtered_result: dict | None,
          watchlist_path: Path = DEFAULT_WATCHLIST,
          generated_at: str | None = None) -> dict:
    """組 site_meta dict。rule_version 取 sectors.json(規則設定的權威來源,目前 v2.2);
    watchlist_updated 取 watchlist.json 實際 mtime。"""
    tw_count   = _count_members(watchlist.get("台股板塊", {}))
    intl_count = _count_members(watchlist.get("國際族群", {}))
    skipped    = []
    if filtered_result:
        skipped = filtered_result.get("metadata", {}).get("skipped_symbols", []) or []
    return {
        "data_date":        date,
        "rule_version":     sectors.get("rule_version", "v2.2"),
        "tw_count":         tw_count,
        "intl_count":       intl_count,
        "total_count":      tw_count + intl_count,
        "skipped":          skipped,
        "generated_at":     generated_at or datetime.now(TZ_TAIPEI).strftime(
                                "%Y-%m-%dT%H:%M:%S+08:00"),
        "watchlist_updated": _mtime_date(watchlist_path),
    }


def write(meta: dict, outdir: Path, date: str) -> Path:
    day_dir = outdir / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "site_meta.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return path


def load(date: str, outdir: Path = DEFAULT_OUTDIR) -> dict | None:
    """頁面 render 時讀回 site_meta;不存在回 None(render 端 fallback)。"""
    path = outdir / date / "site_meta.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    ap = argparse.ArgumentParser(description="產 site_meta.json(渲染單一資料源)")
    ap.add_argument("--date", required=True)
    ap.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST))
    ap.add_argument("--sectors", default=str(DEFAULT_SECTORS))
    ap.add_argument("--result", default=str(DEFAULT_RESULT))
    ap.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    args = ap.parse_args()

    watchlist = _load_json(Path(args.watchlist))
    sectors   = _load_json(Path(args.sectors))
    filtered_result = (_load_json(Path(args.result))
                       if os.path.exists(args.result) else None)
    meta = build(args.date, watchlist=watchlist, sectors=sectors,
                 filtered_result=filtered_result,
                 watchlist_path=Path(args.watchlist))
    path = write(meta, Path(args.outdir), args.date)
    print(f"✅ site_meta → {path}  "
          f"(台股 {meta['tw_count']} + 國際 {meta['intl_count']} = {meta['total_count']}, "
          f"略過 {len(meta['skipped'])}, 規則 {meta['rule_version']}, "
          f"watchlist 更新 {meta['watchlist_updated']})")


if __name__ == "__main__":
    main()
