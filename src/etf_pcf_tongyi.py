"""
etf_pcf_tongyi.py — 統一投信主動式 ETF 申購買回清單(PCF)抓取 + 解析。

統一是 Vue SPA,持股 JS 動態渲染 curl 抓不到;但「匯出 XLSX」是獨立 GET 端點
(2026-09-19 探測確認):
  GET https://www.ezmoney.com.tw/ETF/Transaction/PCFExcelNPOI
      ?fundCode={內部碼}&date={民國 YYY/MM/DD}&specificDate=true
  需 session cookie:先 GET /ETF/Transaction/PCF 頁拿 3 個 cookie(免登入)。
  回傳 .xlsx(shared-strings 型);歷史可回溯(00981A 實測至少到 2025-06-02)。

fundCode 內部碼對照(從頁面下拉選單 option value 取得):
  00981A(主動統一台股增長)= 49YTW
  00403A(主動統一升級50)  = 63YTW

XLSX 結構:股票段每列固定 4 格 shared-string:代號 / 名稱 / 股數 / 權重%。
基金資訊在開頭:基金淨資產價值 / 已發行受益權單位總數。
"""
import io
import re
import zipfile
import urllib.request
import http.cookiejar
import sys

FUND_CODES = {"00981A": "49YTW", "00403A": "63YTW"}
_PCF_PAGE = "https://www.ezmoney.com.tw/ETF/Transaction/PCF"
_XLSX = ("https://www.ezmoney.com.tw/ETF/Transaction/PCFExcelNPOI"
         "?fundCode={fc}&date={mg}&specificDate=true")


def _mingguo(date_iso):
    """2026-09-18 → 115/09/18(民國)。"""
    y, m, d = date_iso.split("-")
    return f"{int(y) - 1911:03d}/{m}/{d}"


def _new_opener():
    """建帶 cookie jar 的 opener,先 GET PCF 頁拿 session cookie(免登入)。"""
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0")]
    op.open(_PCF_PAGE, timeout=20).read()
    return op


def _shared_strings(z):
    ss = z.read("xl/sharedStrings.xml").decode("utf-8")
    return re.findall(r"<t[^>]*>(.*?)</t>", ss, re.S)


def _num(s):
    return int(re.sub(r"[^\d]", "", s) or 0)


def fetch_tongyi(etf, date_iso, _opener=None):
    """抓統一 PCF。回 (holdings[list of dict], fund_info[dict])。
    holdings 每筆:{code, name, shares, weight}。無資料 → ([], {})。
    _opener 可傳共用的 opener(回補時省重複拿 cookie)。"""
    fc = FUND_CODES[etf]
    op = _opener or _new_opener()
    url = _XLSX.format(fc=fc, mg=_mingguo(date_iso))
    data = op.open(url, timeout=20).read()
    if data[:2] != b"PK":                       # 非 xlsx(錯誤頁/無資料)
        return [], {}
    z = zipfile.ZipFile(io.BytesIO(data))
    strs = _shared_strings(z)
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")

    # 基金資訊:掃 sharedStrings 找關鍵字,值在下一格
    fund = {}
    for i, s in enumerate(strs):
        if "基金淨資產價值" in s and i + 1 < len(strs):
            fund["nav"] = _num(strs[i + 1])
        if "已發行受益權單位總數" in s and i + 1 < len(strs):
            fund["units"] = _num(strs[i + 1])

    # 股票段:每列 A/B/C/D 皆 shared-string;代號=4~6碼數字、股數為數字、權重含 %
    holdings = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
        vals = []
        for m in re.finditer(r'<c[^>]*?(?:\st="s")?[^>]*>(?:<v>(\d+)</v>)?</c>', row):
            idx = m.group(1)
            vals.append(strs[int(idx)] if idx is not None else "")
        # 只認「t="s" 且值可解析」的列;取前 4 格
        cells = re.findall(r'<c[^>]*t="s"[^>]*><v>(\d+)</v></c>', row)
        cells = [strs[int(i)] for i in cells]
        if len(cells) < 4:
            continue
        code, name, sh, w = cells[0], cells[1], cells[2], cells[3]
        if not re.fullmatch(r"\d{4,6}[A-Z]?", code):
            continue
        if "%" not in w:                         # 期貨列/表頭/未持有列排除
            continue
        holdings.append({"code": code, "name": name,
                         "shares": _num(sh),
                         "weight": float(re.sub(r"[^\d.]", "", w) or 0)})
    return holdings, fund


if __name__ == "__main__":
    etf, date = sys.argv[1], sys.argv[2]
    hold, fund = fetch_tongyi(etf, date)
    print(f"{etf} @ {date}: {len(hold)} 檔, 淨值={fund.get('nav'):,}, "
          f"單位數={fund.get('units'):,}")
    for h in hold[:6]:
        print(f"  {h['code']} {h['name']}: {h['shares']:,}股 {h['weight']}%")
