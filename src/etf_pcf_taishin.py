"""
etf_pcf_taishin.py — 台新投信主動式 ETF 申購買回清單(PCF)抓取 + 解析。

台新 PCF 是乾淨的 GET 端點(2026-09-19 探測確認):
  https://www.tsit.com.tw/ETF/Home/Pcf/{代號}?FundType=ALL&DataDate={YYYY-MM-DD}
  回傳靜態 HTML(不需 session/JS),3 個 table:
    table[0] 基金資訊(淨資產/已發行單位數/與前日差異…)
    table[1] 期貨(若有)
    table[2] 股票(代號/名稱/股數/持股權重%)
  歷史可回溯(00987A 上市日約 2025-12-24);非交易日回空表。
"""
import re
import html
import urllib.request
import sys


def fetch_taishin(etf, date_iso):
    """抓台新 PCF。回 (holdings[list of dict], fund_info[dict])。
    holdings 每筆:{code, name, shares, weight}。無資料(非交易日/未上市)→ ([], {})。"""
    url = f"https://www.tsit.com.tw/ETF/Home/Pcf/{etf}?FundType=ALL&DataDate={date_iso}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    h = urllib.request.urlopen(req, timeout=20).read().decode("utf-8")
    tables = re.findall(r"<table.*?</table>", h, re.S)

    # 基金資訊(table[0]):th/td 成對
    fund = {}
    if tables:
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tables[0], re.S)
        cells = [re.sub(r"<[^>]+>", "", html.unescape(c)).strip() for c in cells]
        for i in range(0, len(cells) - 1, 2):
            k, v = cells[i], cells[i + 1]
            if "基金淨資產" in k:
                fund["nav"] = int(re.sub(r"[^\d]", "", v) or 0)
            if "已發行受益權單位總數" in k:
                fund["units"] = int(re.sub(r"[^\d]", "", v) or 0)

    # 股票表:找表頭含「股數」+「代號」的那張
    holdings = []
    for t in tables:
        heads = [re.sub(r"<[^>]+>", "", x).strip()
                 for x in re.findall(r"<th[^>]*>(.*?)</th>", t, re.S)]
        if "股數" in heads and "代號" in heads:
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
                tds = [re.sub(r"<[^>]+>", "", html.unescape(x)).strip()
                       for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
                if len(tds) >= 4 and tds[0] and tds[0] != "代號":
                    code = tds[0].replace(" TT", "").replace(" tt", "").strip()
                    shares = int(re.sub(r"[^\d]", "", tds[2]) or 0)
                    wpct = float(re.sub(r"[^\d.]", "", tds[3]) or 0)
                    holdings.append({"code": code, "name": tds[1],
                                     "shares": shares, "weight": wpct})
    return holdings, fund


if __name__ == "__main__":
    etf, date = sys.argv[1], sys.argv[2]
    hold, fund = fetch_taishin(etf, date)
    print(f"{etf} @ {date}: {len(hold)} 檔, 淨值={fund.get('nav')}, 單位數={fund.get('units')}")
    for h in hold[:5]:
        print(f"  {h['code']} {h['name']}: {h['shares']:,}股 {h['weight']}%")
