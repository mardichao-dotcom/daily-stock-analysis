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
from datetime import datetime
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

    # ── 6:events.json 新鮮度 + 結構(stage9 §3.1/§5)──
    errors.extend(_check_events(base, fetch))

    # ── 7:weekly.json/html 週報斷言(stage9 §3.3)──
    errors.extend(_check_weekly(base, fetch))

    # ── 8:chips 籌碼結構斷言(stage9 §3.5)──
    errors.extend(_check_chips(base, data_date, fetch))

    # ── 9:news.json 新聞資料層(欄位齊全 + 版權紅線:無內文)──
    errors.extend(_check_news(base, fetch))

    return errors


def _check_news(base: str, fetch) -> list[str]:
    """news.json:200 + 欄位齊全 + 版權紅線(只有標題+連結,無內文欄位)。
    缺檔(macro 08:30 產出)→ 警示不 fail(不讓主跑耦合 macro 管線健康)。"""
    code, body = fetch(f"{base}/data/v2/news.json")
    if code != 200:
        print(f"[verify_publish] ⚠️ news.json HTTP {code}(macro 08:30 產出,N/A 護欄放行)")
        return []
    try:
        nj = json.loads(body)
    except json.JSONDecodeError:
        return ["news.json 非合法 JSON"]
    items = nj.get("items")
    if not isinstance(items, list):
        return ["news.json items 非 list"]
    REQ = {"title", "source", "published_at", "fetched_at", "url", "matched_keywords"}
    BODY_KEYS = {"description", "content", "summary", "body", "encoded", "content_encoded"}
    errs: list[str] = []
    for it in items[:20]:
        miss = REQ - set(it.keys())
        if miss:
            errs.append(f"news.json 條目缺欄位 {sorted(miss)}")
            break
        leak = BODY_KEYS & set(it.keys())
        if leak:                                   # 版權紅線:不得有內文欄位
            errs.append(f"news.json 含內文欄位 {sorted(leak)}(版權紅線)")
            break
        if not it.get("url"):
            errs.append("news.json 條目缺 url")
            break
    ga = nj.get("generated_at", "")
    try:
        gen = datetime.fromisoformat(ga)
        if (datetime.now(gen.tzinfo) - gen).total_seconds() > 5 * 86400:
            print(f"[verify_publish] ⚠️ news.json generated_at 逾 5 天({ga})")
    except (ValueError, TypeError):
        pass
    if not errs:
        print(f"[verify_publish] ✅ news.json {len(items)} 條,欄位齊全無內文")
    return errs


def _check_events(base: str, fetch) -> list[str]:
    """events.json 斷言:200、generated_at 24h 內、結構完整、conference 抽驗欄位。
    (§2.1 護欄②「抽 3 筆對 MOPS」為人工覆核;此處做機器可驗的結構/新鮮度。)"""
    errs: list[str] = []
    code, body = fetch(f"{base}/data/v2/events.json")
    if code != 200:
        return [f"events.json HTTP {code}"]
    try:
        ev = json.loads(body)
    except json.JSONDecodeError:
        return ["events.json 非合法 JSON"]
    # 新鮮度:generated_at 在 24h 內
    ga = ev.get("generated_at", "")
    try:
        gen = datetime.fromisoformat(ga)
        now = datetime.now(gen.tzinfo)
        if (now - gen).total_seconds() > 24 * 3600:
            errs.append(f"events.json generated_at 超過 24h({ga})")
    except (ValueError, TypeError):
        errs.append(f"events.json generated_at 格式異常({ga!r})")
    # 結構:events 為 list;conference 抽 3 筆有 date/symbol/name
    events = ev.get("events")
    if not isinstance(events, list):
        return errs + ["events.json events 非 list"]
    confs = [e for e in events if e.get("type") == "conference"]
    for e in confs[:3]:
        if not (e.get("date") and e.get("symbol") and e.get("name")):
            errs.append(f"events.json 法說會條目缺欄位:{e.get('symbol') or e}")
    # 事件擴充(§3.5):type/date/level 合法;個股類(dividend)須帶 symbol
    VALID_TYPES = {"conference", "macro", "macro_tw", "settlement", "dividend"}
    VALID_LEVELS = {"high", "medium_high", "medium"}
    for e in events[:80]:
        t = e.get("type")
        if t not in VALID_TYPES:
            errs.append(f"events.json 未知 type:{t}")
        if not e.get("date"):
            errs.append(f"events.json 事件缺 date:{e.get('name') or e}")
        if e.get("level") and e["level"] not in VALID_LEVELS:
            errs.append(f"events.json 非法 level:{e.get('level')}")
    for e in [x for x in events if x.get("type") == "dividend"][:3]:
        if not (e.get("date") and e.get("symbol") and e.get("name")):
            errs.append(f"events.json 除權息條目缺欄位:{e.get('symbol') or e}")
    # 若標記 stale,提示(不算 fail — 第四道護欄設計為保留舊資料)
    if ev.get("conference_stale"):
        print(f"[verify_publish] ⚠️ events.json 法說會為 stale"
              f"(source {ev.get('conference_source_date')})— 第四道護欄啟用")
    return errs


def _check_weekly(base: str, fetch) -> list[str]:
    """weekly.json/html 週報斷言(§3.3):200、8 天內新鮮(週頻+緩衝)、結構完整、
    NAAIM 官方錨點比對(值永不修訂;錨點若落在已發佈 series 窗內須吻合,窗外自動略過)。"""
    errs: list[str] = []
    code, body = fetch(f"{base}/data/v2/weekly.json")
    if code != 200:
        return [f"weekly.json HTTP {code}"]
    try:
        wk = json.loads(body)
    except json.JSONDecodeError:
        return ["weekly.json 非合法 JSON"]
    # 新鮮度:週頻,允許 8 天(7 + 1 緩衝)
    ga = wk.get("generated_at", "")
    try:
        gen = datetime.fromisoformat(ga)
        now = datetime.now(gen.tzinfo)
        if (now - gen).total_seconds() > 8 * 86400:
            errs.append(f"weekly.json generated_at 超過 8 天({ga})")
    except (ValueError, TypeError):
        errs.append(f"weekly.json generated_at 格式異常({ga!r})")
    # 結構:五大區塊齊全
    for k in ("naaim", "vix", "xly_xlp", "margin", "taiex"):
        if k not in wk:
            errs.append(f"weekly.json 缺區塊 {k}")
    # NAAIM:狀態 ok、最新值為數、全量筆數合理(2006→今 > 1000 週)
    n = wk.get("naaim", {})
    if n.get("status") == "ok":
        if not isinstance(n.get("latest_value"), (int, float)):
            errs.append("weekly.json NAAIM latest_value 非數值")
        if not isinstance(n.get("count"), int) or n.get("count", 0) < 1000:
            errs.append(f"weekly.json NAAIM count 異常({n.get('count')} < 1000,疑非全量重建)")
        # 官方錨點比對(3 點對官方)
        series = n.get("series", {})
        dv = dict(zip(series.get("dates", []), series.get("exposure", [])))
        anchors = _load_weekly_cfg().get("naaim_anchors", {}).get("points", {})
        matched = 0
        for d, v in anchors.items():
            if d in dv:
                if abs(dv[d] - v) > 0.01:
                    errs.append(f"weekly.json NAAIM 錨點 {d} 不符官方(線上 {dv[d]} ≠ 官方 {v})")
                else:
                    matched += 1
        if anchors and matched == 0 and dv:
            print("[verify_publish] ⚠️ NAAIM 錨點全落在 series 窗外,建議更新 config 錨點")
        else:
            print(f"[verify_publish] ✅ NAAIM 官方錨點比對 {matched}/{len(anchors)} 吻合")
    # weekly.html 存在且含標題
    hcode, hbody = fetch(f"{base}/weekly.html")
    if hcode != 200:
        errs.append(f"weekly.html HTTP {hcode}")
    elif "每週市場情緒週報" not in hbody:
        errs.append("weekly.html 缺週報標題(渲染異常)")
    return errs


def _check_chips(base: str, data_date: str, fetch) -> list[str]:
    """§3.5 籌碼:抽一檔上市 watchlist chart JSON。含 chips → 驗陣列長度一致;
    缺 chips → 警示不 fail(N/A 護欄:當日 fetch_chips 失敗不應擋整體發布)。
    「抽 3 檔對證交所官網」屬人工覆核(同 events 對 MOPS)。"""
    code, body = fetch(f"{base}/data/v2/{data_date}/_index.json")
    if code != 200:
        return []                              # 缺 index 由既有檢查涵蓋,不重複報
    try:
        idx = json.loads(body)
    except json.JSONDecodeError:
        return []
    syms = idx.get("symbols", {}) or {}
    tw = sorted(k for k, v in syms.items()
                if k.startswith("TWSE_") and (v or {}).get("status") == "ready")
    if not tw:
        tw = sorted(s for s in idx.get("stocks", []) if str(s).startswith("TWSE_"))
    if not tw:
        return []
    sample = tw[0]
    c2, b2 = fetch(f"{base}/data/v2/{data_date}/{sample}.json")
    if c2 != 200:
        return [f"chips 抽樣 {sample}.json HTTP {c2}"]
    try:
        j = json.loads(b2)
    except json.JSONDecodeError:
        return [f"chips 抽樣 {sample}.json 非合法 JSON"]
    chips = j.get("chips")
    if not chips:
        print(f"[verify_publish] ⚠️ {sample} 無 chips(fetch_chips 當日失敗/尚未建),N/A 護欄放行")
        return []
    errs: list[str] = []
    dates = chips.get("dates", [])
    if not dates:
        errs.append(f"chips {sample} dates 空")
    for key in ("foreign_net", "trust_net"):
        arr = chips.get(key)
        if not isinstance(arr, list) or len(arr) != len(dates):
            errs.append(f"chips {sample} {key} 長度不符 dates({len(dates)})")
    if not errs:
        print(f"[verify_publish] ✅ chips 抽樣 {sample}:{len(dates)} 日,外資/投信長度一致")
    return errs


def _load_weekly_cfg() -> dict:
    try:
        with open(PROJECT_ROOT / "config" / "weekly_alerts.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


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
