"""
etf_pcf_capital.py — 群益投信主動式 ETF 申購買回清單(PCF)抓取 + 解析。

群益 ETF 頁是 SPA,但資料來自乾淨的 JSON API(2026-09-19 network 攔截確認):
  POST https://www.capitalfund.com.tw/CFWeb/api/etf/buyback
  body: {"fundId":"<內部id>","date":"YYYY-MM-DD" 或 null(取最新)}
  免 session、可 curl 直抓、可帶日期回溯歷史。
  回 {code, data:{pcf, stocks, futures, bonds, assets, ...}, message}
    pcf.nav        基金淨資產價值
    pcf.totUnit    已發行受益權單位總數
    pcf.date2      實際資料日期
    stocks[].stocNo / stocName / weight / share  代號/名稱/權重%/股數

fundId 內部 id 對照(從 PCF 頁下拉選單 option value 取得):
  00992A(主動群益科技創新)= 500
  (00982A 主動群益台灣強棒=399、00415A 主動群益核心50 另有 id,本輪只收 00992A)

注意:API 回傳 JSON 偶含英文名內的單一反斜線(如 "Receivables\\Accounts"),
      屬非法 JSON 跳脫,需容錯修正後再解析。
"""
import re
import json
import urllib.request
import sys

FUND_IDS = {"00992A": "500"}
_API = "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback"


def _loads_lenient(raw):
    """群益 API 大多回合法 JSON;偶有英文名內單一反斜線(非法跳脫)。
    先直接解析;失敗才逐一把「非合法跳脫的單一反斜線」補成 \\\\ 再解析
    (用 lambda 精準處理,避免誤傷合法的 \\\\ 雙反斜線對)。"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 掃描:偶數個反斜線是合法對,原樣保留;奇數個尾端那顆若後接非法跳脫字元則補一顆
        def fix(m):
            bs = m.group(0)
            nxt = raw[m.end():m.end() + 1]
            if len(bs) % 2 == 1 and nxt not in '"\\/bfnrtu':
                return bs + "\\"
            return bs
        return json.loads(re.sub(r'\\+', fix, raw))


def fetch_capital(etf, date_iso):
    """抓群益 PCF。回 (holdings[list of dict], fund_info[dict])。
    holdings 每筆:{code, name, shares, weight}。無資料(非交易日/未上市)→ ([], {})。"""
    fund_id = FUND_IDS[etf]
    body = json.dumps({"fundId": fund_id, "date": date_iso}).encode("utf-8")
    req = urllib.request.Request(
        _API, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8")
    d = _loads_lenient(raw)
    data = d.get("data") or {}
    stocks = data.get("stocks") or []
    if not stocks:
        return [], {}
    pcf = data.get("pcf") or {}
    fund = {}
    if pcf.get("nav") is not None:
        fund["nav"] = int(pcf["nav"])
    if pcf.get("totUnit") is not None:
        fund["units"] = int(pcf["totUnit"])
    holdings = []
    for s in stocks:
        code = str(s.get("stocNo") or "").strip()
        # 陸股用 "002371 CH" 之類;台股純數字。統一去掉市場後綴。
        code = code.split()[0] if code else code
        if not code:
            continue
        holdings.append({
            "code": code,
            "name": (s.get("stocName") or "").strip(),
            "shares": int(s.get("share") or 0),
            "weight": float(s.get("weight") or 0),
        })
    return holdings, fund


if __name__ == "__main__":
    etf = sys.argv[1] if len(sys.argv) > 1 else "00992A"
    date = sys.argv[2] if len(sys.argv) > 2 else None
    hold, fund = fetch_capital(etf, date)
    nav = fund.get("nav")
    print(f"{etf} @ {date or '最新'}: {len(hold)} 檔, "
          f"淨值={nav:,}" if nav else f"{etf} @ {date}: {len(hold)} 檔")
    for h in hold[:6]:
        print(f"  {h['code']} {h['name']}: {h['shares']:,}股 {h['weight']}%")
