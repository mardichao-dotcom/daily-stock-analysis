"""
scrape_conferences.py — MOPS 法人說明會一覽表 Playwright 抓取(stage9 Day1)

獨立 headless chromium(不碰 TV Desktop CDP)。MOPS 已改版 SPA:填表 → SPA POST
/mops/api/redirectToOld → 回官方簽章 URL(mopsov)→ 同 context goto 取 HTML 表格 → 解析。
(機制詳見 stage9_spec.md §2.1;簽章 URL 是官方 SPA 簽發跳轉,屬官方流程。)

**設計為獨立子行程呼叫**:fetch_events.py 以 subprocess timeout=120s 包住 + 失敗重試,
故本檔只負責「抓一次、印 JSON 到 stdout」;逾時/崩潰由父行程處理(第四道護欄)。

用法:
    python3 -m src.scrape_conferences --years 115,116 --markets sii,otc
    → stdout 印 JSON: {"conferences":[{code,name,date_roc,date,time,place,summary,market}, ...]}

exit 0 = 成功(可能 0 筆);exit != 0 = 抓取/解析失敗。
"""
from __future__ import annotations
import argparse
import json
import sys

SPA_URL = "https://mops.twse.com.tw/mops/#/web/t100sb02_1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MARKET_LABEL = {"sii": "上市", "otc": "上櫃"}

# 解析後每筆的欄位(schema 斷言用)
ROW_KEYS = ("code", "name", "date_roc", "time", "place", "summary")


def roc_to_iso(roc: str) -> str | None:
    """民國日期 '115/06/26' → '2026-06-26'。無法解析或非合法日期回 None。
    用 datetime.date 建構驗證 → 潤年邊界正確(如 117/02/29→2028-02-29 合法,
    116/02/29→2027 非潤年 → None)。"""
    from datetime import date as _date
    roc = (roc or "").strip()
    parts = roc.replace("-", "/").split("/")
    if len(parts) != 3:
        return None
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        year = y + 1911
        if not (1990 <= year <= 2100):
            return None
        return _date(year, m, d).isoformat()   # 無效日期(含潤年)→ ValueError
    except ValueError:
        return None


def _scrape_one(page, market_code: str, year: str) -> list[dict]:
    """抓單一市場單一民國年,回 raw rows。"""
    signed = {}

    def on_resp(r):
        if "redirectToOld" in r.url:
            try:
                signed["url"] = r.json().get("result", {}).get("url")
            except Exception:
                pass

    page.on("response", on_resp)
    page.goto(SPA_URL, wait_until="networkidle", timeout=45000)
    page.wait_for_timeout(2500)
    page.locator("select[name=市場別]").select_option(label=MARKET_LABEL[market_code])
    page.locator("input#year").fill(str(year))
    page.get_by_role("button", name="查詢").first.click()
    page.wait_for_timeout(3500)
    page.remove_listener("response", on_resp)

    url = signed.get("url")
    if not url:
        return []
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1200)

    out: list[dict] = []
    for t in page.locator("table").all():
        rows = t.locator("tr")
        if rows.count() < 2:
            continue
        header = rows.nth(0).locator("th,td").all_inner_texts()
        if not any("召開" in h for h in header):
            continue
        for i in range(1, rows.count()):
            c = [x.strip() for x in rows.nth(i).locator("td").all_inner_texts()]
            if len(c) < 5 or not c[0]:
                continue
            iso = roc_to_iso(c[2])
            out.append({
                "code":     c[0],
                "name":     c[1],
                "date_roc": c[2],
                "date":     iso,
                "time":     c[3] if len(c) > 3 else "",
                "place":    c[4] if len(c) > 4 else "",
                "summary":  c[5] if len(c) > 5 else "",
                "market":   market_code,
            })
        break
    return out


def scrape(years: list[str], markets: list[str]) -> list[dict]:
    from playwright.sync_api import sync_playwright
    conferences: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, locale="zh-TW")
        try:
            for mk in markets:
                for yr in years:
                    page = ctx.new_page()
                    try:
                        conferences.extend(_scrape_one(page, mk, yr))
                    finally:
                        page.close()
        finally:
            browser.close()
    return conferences


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", required=True, help="逗號分隔民國年,如 115,116")
    ap.add_argument("--markets", default="sii,otc")
    args = ap.parse_args()
    years = [y.strip() for y in args.years.split(",") if y.strip()]
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    try:
        rows = scrape(years, markets)
    except Exception as e:
        print(f"[scrape_conferences] FAIL: {e}", file=sys.stderr)
        return 1
    json.dump({"conferences": rows}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
