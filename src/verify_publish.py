"""
verify_publish.py — 發布後對「線上 URL」自動驗證(P1 §6.4)

publish.sh 最後一步呼叫。GitHub Pages 有部署延遲 → 先 sleep(預設 90s),失敗 retry 1 次。
任一斷言失敗 → exit non-zero(publish.sh / daily_supervisor 標 ❌ 並發 Discord)。

斷言(全部對線上,不看本機檔案):
  1. 四頁 HTTP 200(index.html / index_v2.html / watchlist_v2.html / tags.html)
  2. 四頁 meta 版本一致(都含 site_meta.rule_version,且無殘留硬寫「版本 v2.1」)
     + index_v2 / watchlist 台股檔數 == site_meta.tw_count
  3. 當日 data 目錄抽查:台股 / 日韓 / 美股 chart JSON 各 200,且美股 key_prices.lines > 0
  4. history.html 最新一筆摘要非「(無摘要)」
  5. index_v2 ETF 表無空白 <strong></strong>
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

BASE_URL = "https://mardichao-dotcom.github.io/daily-stock-analysis"
PAGES = ["index.html", "index_v2.html", "watchlist_v2.html", "tags.html"]
# 抽查樣本:(標籤, 檔名, 是否檢查美股 key_prices.lines>0)
CHART_SAMPLES = [("台股", "TWSE_8996", False),
                 ("日韓", "TSE_6594", False),
                 ("美股", "NYSE_TSM", True)]


def _fetch(url: str, timeout: int = 15) -> tuple[int, str]:
    req = urllib.request.Request(
        url, headers={"User-Agent": "verify-publish/1.0", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:                       # noqa: BLE001 — 任何取得失敗都算斷言失敗
        return 0, f"__ERR__ {e}"


def run_checks(base: str, data_date: str, *, fetch=_fetch) -> list[str]:
    """回傳錯誤訊息 list;空 list = 全綠。fetch 可注入以利測試。"""
    errors: list[str] = []

    # ── site_meta(權威值)──
    sm: dict = {}
    code, body = fetch(f"{base}/data/v2/{data_date}/site_meta.json")
    if code != 200:
        errors.append(f"site_meta.json HTTP {code}")
    else:
        try:
            sm = json.loads(body)
        except json.JSONDecodeError:
            errors.append("site_meta.json 非合法 JSON")
    rule = sm.get("rule_version", "")
    tw   = sm.get("tw_count")

    # ── 1 + 2:四頁 200 + 版本一致 ──
    page_html: dict[str, str] = {}
    for p in PAGES:
        code, html = fetch(f"{base}/{p}")
        if code != 200:
            errors.append(f"{p} HTTP {code}")
            continue
        page_html[p] = html
        if rule and f"規則 {rule}" not in html:
            errors.append(f"{p} 缺少版本「規則 {rule}」")
        if "版本 v2.1" in html:
            errors.append(f"{p} 仍含硬寫「版本 v2.1」")
    if tw is not None:
        for p in ("index_v2.html", "watchlist_v2.html"):
            if p in page_html and f"台股 {tw} 檔" not in page_html[p]:
                errors.append(f"{p} 台股檔數 != site_meta({tw})")

    # ── 3:chart JSON 抽查 ──
    for label, fname, check_lines in CHART_SAMPLES:
        code, body = fetch(f"{base}/data/v2/{data_date}/{fname}.json")
        if code != 200:
            errors.append(f"{label} {fname}.json HTTP {code}")
            continue
        if check_lines:
            try:
                j = json.loads(body)
                if len(j.get("key_prices", {}).get("lines", [])) == 0:
                    errors.append(f"美股 {fname} key_prices.lines = 0")
            except json.JSONDecodeError:
                errors.append(f"美股 {fname}.json 非合法 JSON")

    # ── 4:history 最新一筆非「(無摘要)」──
    code, html = fetch(f"{base}/history.html")
    if code != 200:
        errors.append(f"history.html HTTP {code}")
    else:
        m = re.search(r'history-summary">([^<]*)<', html)
        if not m:
            errors.append("history.html 找不到 summary")
        elif "無摘要" in m.group(1):
            errors.append("history.html 最新一筆為(無摘要)")

    # ── 5:ETF 表無空白 <strong></strong> ──
    if "<strong></strong>" in page_html.get("index_v2.html", ""):
        errors.append("index_v2.html 含空白 <strong></strong>(ETF 名稱缺漏)")

    return errors


def _notify_fail(errors: list[str], data_date: str) -> None:
    try:
        from src.daily_supervisor import _load_webhook, _send
        webhook = _load_webhook()
        if webhook:
            msg = (f"❌ verify_publish {data_date} 失敗({len(errors)} 項):\n"
                   + "\n".join(f"  - {e}" for e in errors[:10]))
            _send(webhook, msg)
    except Exception as e:                       # noqa: BLE001
        print(f"[verify_publish] Discord 通知失敗: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE_URL)
    ap.add_argument("--data-date", default=None)
    ap.add_argument("--delay", type=int,
                    default=int(os.environ.get("VERIFY_DELAY", "90")),
                    help="首次檢查前等 Pages 部署的秒數")
    args = ap.parse_args()

    data_date = args.data_date or (PROJECT_ROOT / ".data_date").read_text().strip()

    if args.delay:
        print(f"[verify_publish] 等 Pages 部署 {args.delay}s...")
        time.sleep(args.delay)

    errors = run_checks(args.base, data_date)
    if errors:
        print(f"[verify_publish] 首次 {len(errors)} 項失敗,retry 1 次(再等 90s)...")
        time.sleep(90)
        errors = run_checks(args.base, data_date)

    if errors:
        print("❌ verify_publish 失敗:")
        for e in errors:
            print(f"  - {e}")
        _notify_fail(errors, data_date)
        return 1

    print(f"✅ verify_publish 全綠({data_date}):四頁 + chart 抽查 + history + ETF 表皆通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
