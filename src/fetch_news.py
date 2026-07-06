"""
fetch_news.py — 新聞資料層(只做資料,不做網頁渲染;呈現等 Design 定稿)

進 08:30 macro 排程。抓公開 RSS 源 → 依 news_keywords.json 過濾 → 產 docs/data/v2/news.json。
每條:title, source, published_at, fetched_at, url, matched_keywords。保留最近 3 天(跨日累積、去重)。

版權紅線:**只取標題與連結,絕不存 RSS 內文/摘要(description/content)**。
單一來源:過濾關鍵字與 Discord 早報共用 config/news_keywords.json(兩個消費端)。
金十 Telegram 不進 news.json(留 Discord 專用)。
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(PROJECT_ROOT))
from src.macro_report import _load_news_keywords          # 單一來源:共用關鍵字 + 防呆

TZ = timezone(timedelta(hours=8))
OUT = os.path.join(PROJECT_ROOT, "docs", "data", "v2", "news.json")
RETAIN_DAYS = 3
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 來源清單(2026-07-06 spike 驗證可用;工商時報 403/無 feed 暫略,Yahoo 條目太少略)。
# 新增來源請先 spike 可用性與格式。
SOURCES = [
    {"name": "中央社財經", "url": "https://feeds.feedburner.com/rsscna/finance"},
    {"name": "經濟日報",   "url": "https://money.udn.com/rssfeed/news/1001/5591?ch=money"},
    {"name": "鉅亨網",     "url": "https://news.cnyes.com/rss/v1/news/category/tw_stock"},
]


def _now_iso() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _local(tag: str) -> str:
    return tag.split("}")[-1]                              # 去 XML namespace


def _text(item, name: str) -> str:
    for ch in item:
        if _local(ch.tag) == name and ch.text:
            return ch.text.strip()
    return ""


def _to_iso(pubdate: str) -> str | None:
    try:
        return parsedate_to_datetime(pubdate).astimezone(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    except (TypeError, ValueError, IndexError):
        return None


def match_keywords(title: str, keywords: list[str]) -> list[str]:
    low = title.lower()
    return [k for k in keywords if k and k.lower() in low]


def _http(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_source(src: dict, keywords: list[str], now_iso: str) -> tuple[list[dict], str | None]:
    """抓單一 RSS 源 → 只留有命中關鍵字的條目(只取 title/url/published_at)。回 (items, error)。"""
    try:
        root = ET.fromstring(_http(src["url"]))
    except Exception as e:                                  # noqa: BLE001 — 單源失敗不致命
        return [], f"{src['name']}: {str(e)[:60]}"
    out = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        title = _text(item, "title")
        link = _text(item, "link")
        if not title or not link:
            continue
        matched = match_keywords(title, keywords)
        if not matched:                                    # 過濾:無命中不收
            continue
        out.append({
            "title": title, "source": src["name"],
            "published_at": _to_iso(_text(item, "pubDate")),
            "fetched_at": now_iso, "url": link,
            "matched_keywords": matched,
        })
    return out, None


def _merge_retain(new_items: list[dict], prev_items: list[dict], now: datetime) -> list[dict]:
    """跨日累積:new + prev 去重(by url)→ 保留最近 RETAIN_DAYS 天 → 依時間新到舊。
    去重時保留『首次抓到』的 fetched_at(prev 優先)。"""
    cutoff = (now - timedelta(days=RETAIN_DAYS)).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    by_url: dict[str, dict] = {}
    for it in prev_items + new_items:                      # prev 先進,new 補未見過的
        u = it.get("url")
        if u and u not in by_url:
            by_url[u] = it
    kept = [it for it in by_url.values()
            if (it.get("published_at") or it.get("fetched_at") or "") >= cutoff]
    kept.sort(key=lambda it: it.get("published_at") or it.get("fetched_at") or "", reverse=True)
    return kept


def _load_prev(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("items", [])
    except (OSError, json.JSONDecodeError):
        return []


def run(out_path: str = OUT) -> dict:
    now = datetime.now(TZ)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    keywords, kw_alert = _load_news_keywords()             # 共用 Discord 早報那套 + 防呆
    errors = []
    new_items: list[dict] = []
    ok_sources = []
    for src in SOURCES:
        items, err = fetch_source(src, keywords, now_iso)
        if err:
            errors.append(err)
        else:
            ok_sources.append(src["name"])
            new_items.extend(items)

    merged = _merge_retain(new_items, _load_prev(out_path), now)
    return {
        "generated_at": now_iso,
        "sources": ok_sources,
        "sources_failed": errors,
        "keyword_alert": kw_alert,
        "retained_days": RETAIN_DAYS,
        "count": len(merged),
        "items": merged,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    out = run(args.out)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"✅ news.json → {args.out}  {out['count']} 條(近 {RETAIN_DAYS} 天)"
          f" | 來源 {out['sources']}"
          + (f" | 失敗 {out['sources_failed']}" if out['sources_failed'] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
