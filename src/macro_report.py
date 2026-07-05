"""
macro_report.py — 每日總經 Discord 早報散文(stage9 Day2 §3.2)

讀 macro.json + news_keywords.json → 產一段繁中早報散文 → 推 Discord。
散文用 Haiku 級模型(claude-haiku-4-5,用戶 2026-07-04 拍板);需 ANTHROPIC_API_KEY
(secrets.json 的 anthropic_api_key 或環境變數)。缺 key → 退回結構化模板(不擋流程)。

護欄:數據源失敗(macro.json errors 非空)→ 早報明確點名哪項 N/A,不假裝有值。
"""
from __future__ import annotations
import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(PROJECT_ROOT))

SECRETS = os.path.join(PROJECT_ROOT, "config", "secrets.json")
MACRO   = os.path.join(PROJECT_ROOT, "docs", "data", "v2", "macro.json")
NEWS_KW = os.path.join(PROJECT_ROOT, "config", "news_keywords.json")
NEWS_KW_CACHE = os.path.join(PROJECT_ROOT, "config", ".news_keywords.last_valid.json")
HAIKU_MODEL = "claude-haiku-4-5"   # 用戶拍板 Haiku 級


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return None


def _load_news_keywords() -> tuple[list, str | None]:
    """讀 news_keywords.json 的防呆版:
      - 解析成功且 keywords 為 list → 更新 last_valid 快取,回 (keywords, None)
      - 解析失敗(手滑改壞 JSON)→ 沿用上一份有效清單 + 回 Discord 告警字串
      - 連快取都沒有 → 回 ([], 告警)。絕不因單次手滑開天窗。"""
    try:
        with open(NEWS_KW, encoding="utf-8") as f:
            data = json.load(f)
        kws = data.get("keywords")
        if not isinstance(kws, list):
            raise ValueError("keywords 欄非 list")
        try:                                   # 有效 → 落地快取
            with open(NEWS_KW_CACHE, "w", encoding="utf-8") as f:
                json.dump({"keywords": kws}, f, ensure_ascii=False)
        except OSError:
            pass
        return kws, None
    except (json.JSONDecodeError, OSError, FileNotFoundError, ValueError) as e:
        cached = _load(NEWS_KW_CACHE)
        if cached and isinstance(cached.get("keywords"), list):
            return cached["keywords"], (
                f"⚠️ news_keywords.json 解析失敗({str(e)[:50]}),"
                f"已沿用上一份有效清單({len(cached['keywords'])} 個關鍵字)——請修正檔案")
        return [], (f"⚠️ news_keywords.json 解析失敗且無有效快取({str(e)[:50]}),"
                    f"本次早報暫無新聞關鍵字")


def _anthropic_key() -> str:
    return (os.environ.get("ANTHROPIC_API_KEY", "")
            or (_load(SECRETS) or {}).get("anthropic_api_key", ""))


def _fmt_items(macro: dict) -> str:
    """把 macro.json 數據攤成一行文字給模型/模板用。"""
    out = []
    for k, v in (macro.get("data") or {}).items():
        if v.get("value") == "N/A":
            out.append(f"{v.get('label', k)}: N/A(來源失敗)")
        else:
            chg = v.get("change_pct")
            u = v.get("unit", "")
            out.append(f"{v.get('label', k)}: {v['value']}{u}"
                       + (f"({chg:+.2f}%)" if isinstance(chg, (int, float)) else ""))
    return "；".join(out)


def _template_report(macro: dict, keywords: list[str]) -> str:
    """無 API key 的退回版:結構化摘要(非 AI 散文,但資訊完整)。"""
    lines = ["📈 **今日總經快覽**", _fmt_items(macro)]
    if macro.get("errors"):
        lines.append("⚠️ 失敗來源:" + "；".join(macro["errors"]))
    if keywords:
        lines.append("📰 今日新聞關注:" + "、".join(keywords[:8]))
    lines.append("_(未設 ANTHROPIC_API_KEY,顯示結構化摘要;設定後升級為 AI 散文)_")
    return "\n".join(lines)


def _ai_report(macro: dict, keywords: list[str], key: str) -> str | None:
    """Haiku 產繁中早報散文。失敗回 None(caller 退回模板)。"""
    try:
        import anthropic
    except ImportError:
        return None
    items = _fmt_items(macro)
    errs = ("；".join(macro.get("errors", [])) or "無")
    kw = "、".join(keywords[:10]) or "無"
    prompt = (
        "你是台股晨間盤前的總經播報員。用繁體中文寫一段 3-5 句、口語但專業的早報散文,"
        "涵蓋以下數據的重點與盤前氛圍。不要條列、不要 markdown 標題、不要杜撰數字。\n\n"
        f"數據:{items}\n"
        f"失敗未取得的項目(如有,請如實說『X 今日無資料』,勿假裝有值):{errs}\n"
        f"今日新聞關注關鍵字:{kw}\n"
    )
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return "📈 **總經早報**\n" + text.strip() if text.strip() else None
    except Exception as e:                       # noqa: BLE001
        print(f"[macro_report] Haiku 失敗,退回模板: {str(e)[:80]}", file=sys.stderr)
        return None


def build_report() -> tuple[str, bool, str | None]:
    """回 (訊息文字, used_ai, 關鍵字告警或 None)。"""
    macro = _load(MACRO) or {"data": {}, "errors": ["macro.json 讀取失敗"]}
    keywords, kw_alert = _load_news_keywords()
    key = _anthropic_key()
    if key:
        ai = _ai_report(macro, keywords, key)
        if ai:
            return ai, True, kw_alert
    return _template_report(macro, keywords), False, kw_alert


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    msg, used_ai, kw_alert = build_report()
    tag = "Haiku AI 散文" if used_ai else "結構化模板(無 API key)"
    print(f"[macro_report] 早報產出({tag})")
    if kw_alert:
        print(f"[macro_report] {kw_alert}")
        msg = msg + "\n\n" + kw_alert          # 告警隨早報一起推 Discord,不另開天窗

    if args.dry_run:
        print("── 早報預覽 ──\n" + msg)
        return 0

    from src.daily_supervisor import _load_webhook, _send
    wh = _load_webhook()
    if not wh:
        print("[macro_report] 無 Discord webhook,跳過發送")
        return 0
    _send(wh, msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
