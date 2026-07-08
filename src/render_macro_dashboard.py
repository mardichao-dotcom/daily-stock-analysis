"""
render_macro_dashboard.py — 宏觀數據頁骨架(stage12 Day5-6 階段一,2026-07-09)

八訊號歷史線圖,資料全取自 macro.db 既有表(零新增):
  台股/美股+MA20/60/200、VIX(+VIX3M)、密大信心、CPI 實際vs克里夫蘭預測、
  景氣燈號、美債 10Y、台幣、FedWatch 下次會議期望
產出:docs/data/v2/macro_signals.json(數值序列)+ docs/macro_dashboard.html(頁殼)。
混頻:08:30 run_macro 隨 fetch_signals --daily 後重產(日頻項當日、月頻項照公布節律,
     每圖標「更新至」);19:00 主跑亦冪等重跑。

🔴 紅線(spec §0):本頁/JS/JSON 不得出現任何水位模型分數、權重、打分閾值、檔位字樣
——verify_publish._check_macro_dashboard 以黑名單 grep 強制。中性描述僅說明
「數據是什麼、市場如何解讀」,不含 v1.2 任何分界值。
階段二(等拍板/凍結)將於既有圖上疊加警戒線與水位指針,不重做本頁。
語意鐵律:本頁不用紅綠(漲跌語意保留給行情);燈號以分數線+燈色文字呈現。
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from src import asset_version

TZ = timezone(timedelta(hours=8))
MACRO_DB = os.path.join(PROJECT_ROOT, "macro.db")
OUT_JSON = os.path.join(PROJECT_ROOT, "docs", "data", "v2", "macro_signals.json")
OUT_HTML = os.path.join(PROJECT_ROOT, "docs", "macro_dashboard.html")

# (id, 標題, 中性市場意義描述 —— 不得含任何模型閾值/分界)
SIGNALS = [
    ("taiex", "台股加權指數與均線",
     "收盤價與 20/60/200 日移動平均。均線反映不同期間的平均持有成本,"
     "價格與均線的相對位置常被市場用來衡量趨勢方向與強度。"),
    ("spx", "美股 S&P 500 與均線",
     "美股大盤與其 20/60/200 日均線。美股為全球風險資產的領頭市場,"
     "其趨勢對台股外資動向有顯著的傳導效果。"),
    ("vix", "VIX 波動率(含 VIX3M)",
     "S&P 500 選擇權隱含的未來 30 天年化波動率(俗稱恐慌指數);VIX3M 為 3 個月期版本。"
     "數值升高通常伴隨避險情緒升溫,兩者的相對高低反映短期恐慌與中期預期的差異。"),
    ("umich", "密大消費者信心(月頻)",
     "密西根大學消費者信心指數終值。反映美國家庭對自身財務與整體經濟的預期,"
     "是消費動能與衰退討論中最常被引用的軟數據之一。"),
    ("cpi", "美國 CPI:實際 vs 克里夫蘭預測(月頻)",
     "CPI 月增率實際值與克里夫蘭聯儲 Inflation Nowcasting 的會前預測。"
     "兩者的差距(通膨驚奇)反映公布當下對市場通膨預期的衝擊方向。"),
    ("light", "台灣景氣對策信號(月頻)",
     "國發會景氣對策信號綜合分數,由貨幣總計數、股價指數、出口等 9 項構成項目合成,"
     "以五色燈號呈現台灣總體景氣位置,於資料月的次月下旬發布。"),
    ("dgs10", "美債 10 年期殖利率",
     "美國 10 年期公債殖利率(日頻)。全球資產定價之錨,同時反映市場對長期通膨與"
     "實質利率的綜合預期;其變動速度常比水準本身更受市場關注。"),
    ("usdtwd", "美元兌新台幣",
     "USD/TWD 匯率,上升代表台幣貶值。台幣匯率與外資對台股的資金進出高度相關,"
     "歷史序列採 Fed H.10 官方統計,最近數日以市場即時價暫代、官方值到後覆寫。"),
    ("fedwatch", "FedWatch:下次 FOMC 會議市場預期",
     "由聯邦基金期貨自算的下次會議利率變動隱含預期(基點)。正值代表市場偏向升息、"
     "負值偏向降息。歷史段僅會議月有可觀測的市場定價,非會議月無數據屬正常。"),
]


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def build_data(db_path: str = MACRO_DB) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    g = conn.execute
    out: dict = {"generated_at": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                 "signals": {}}

    for market, key in (("TAIEX", "taiex"), ("SPX", "spx")):
        rows = g("SELECT date, close, ma20, ma60, ma200 FROM idx_daily "
                 "WHERE market=? ORDER BY date", (market,)).fetchall()
        out["signals"][key] = {
            "last_date": rows[-1][0], "freq": "daily",
            "current": {"close": _r(rows[-1][1]), "ma20": _r(rows[-1][2]),
                        "ma60": _r(rows[-1][3]), "ma200": _r(rows[-1][4])},
            "dates": [r[0] for r in rows],
            "close": [_r(r[1]) for r in rows],
            "ma20": [_r(r[2]) for r in rows],
            "ma60": [_r(r[3]) for r in rows],
            "ma200": [_r(r[4]) for r in rows]}

    rows = g("SELECT date, close, vix3m FROM vix_daily ORDER BY date").fetchall()
    out["signals"]["vix"] = {
        "last_date": rows[-1][0], "freq": "daily",
        "current": {"vix": _r(rows[-1][1]), "vix3m": _r(rows[-1][2])},
        "dates": [r[0] for r in rows],
        "vix": [_r(r[1]) for r in rows],
        "vix3m": [_r(r[2]) for r in rows]}

    rows = g("SELECT month, value, release_date FROM umich_monthly "
             "WHERE month >= '1978-01' ORDER BY month").fetchall()
    out["signals"]["umich"] = {
        "last_date": rows[-1][0], "release_date": rows[-1][2], "freq": "monthly",
        "current": {"value": _r(rows[-1][1])},
        "months": [r[0] for r in rows],
        "values": [_r(r[1]) for r in rows]}

    rows = g("SELECT target_month, release_date, actual_mom, nowcast_mom "
             "FROM cpi_events ORDER BY target_month").fetchall()
    out["signals"]["cpi"] = {
        "last_date": rows[-1][0], "release_date": rows[-1][1], "freq": "monthly",
        "current": {"actual": _r(rows[-1][2], 3), "nowcast": _r(rows[-1][3], 3)},
        "months": [r[0] for r in rows],
        "actual": [_r(r[2], 3) for r in rows],
        "nowcast": [_r(r[3], 3) for r in rows]}

    rows = g("SELECT month, score, light, release_date FROM light_monthly "
             "ORDER BY month").fetchall()
    out["signals"]["light"] = {
        "last_date": rows[-1][0], "release_date": rows[-1][3], "freq": "monthly",
        "current": {"score": _r(rows[-1][1], 0), "light": rows[-1][2]},
        "months": [r[0] for r in rows],
        "scores": [_r(r[1], 0) for r in rows],
        "lights": [r[2] for r in rows]}

    rows = g("SELECT date, value FROM dgs10_daily ORDER BY date").fetchall()
    out["signals"]["dgs10"] = {
        "last_date": rows[-1][0], "freq": "daily",
        "current": {"value": _r(rows[-1][1])},
        "dates": [r[0] for r in rows], "values": [_r(r[1]) for r in rows]}

    rows = g("SELECT date, rate, source FROM usdtwd_daily ORDER BY date").fetchall()
    out["signals"]["usdtwd"] = {
        "last_date": rows[-1][0], "freq": "daily",
        "current": {"rate": _r(rows[-1][1], 3),
                    "provisional": "暫代" in (rows[-1][2] or "")},
        "dates": [r[0] for r in rows], "rates": [_r(r[1], 3) for r in rows]}

    rows = g("SELECT date, next_meeting, expected_change_bp "
             "FROM fed_expectations_daily ORDER BY date").fetchall()
    out["signals"]["fedwatch"] = {
        "last_date": rows[-1][0], "freq": "daily",
        "current": {"next_meeting": rows[-1][1], "expected_bp": _r(rows[-1][2], 1)},
        "dates": [r[0] for r in rows],
        "expected_bp": [_r(r[2], 1) for r in rows]}

    conn.close()
    return out


def render_html() -> str:
    gen = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    sections = []
    for sid, title, desc in SIGNALS:
        sections.append(f"""
<section class="md-signal" id="sig-{sid}">
  <div class="md-head">
    <h2>{title}</h2>
    <span class="md-updated" data-updated="{sid}">—</span>
  </div>
  <div class="md-current" data-current="{sid}">載入中…</div>
  <div class="md-chart" data-signal="{sid}"></div>
  <p class="md-desc">{desc}</p>
</section>""")
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>宏觀數據 — 台股儀表板</title>
<link rel="stylesheet" href="{asset_version.versioned('assets/tokens.css')}">
<link rel="stylesheet" href="{asset_version.versioned('assets/style_v2.css')}">
<script>(function(){{var t=localStorage.getItem('theme');if(t)document.documentElement.setAttribute('data-theme',t);}})();</script>
</head>
<body>
<header class="page-header">
  <div class="container">
    <nav class="page-nav">
      <a href="index.html">← 回首頁</a>
      <a href="index_v2.html">📈 儀表板</a>
      <a href="watchlist_v2.html">📋 Watchlist</a>
      <a href="tags.html">🔥 主題熱度</a>
      <a href="weekly.html">📅 週報</a>
      <span class="page-nav-current">🌐 宏觀數據</span>
    </nav>
    <h1>🌐 宏觀數據</h1>
    <div class="meta">八項宏觀訊號的歷史序列與當前值 ｜ 日頻項每日 08:30 更新、月頻項照各自公布節律 ｜ 頁面產出 {gen}</div>
  </div>
</header>
<main class="container md-container">
{''.join(sections)}
</main>
<script src="{asset_version.versioned('assets/theme.js')}" defer></script>
<script src="{asset_version.versioned('assets/macro_dash.js')}" defer></script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=MACRO_DB)
    args = ap.parse_args()
    data = build_data(args.db)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(render_html())
    kb = os.path.getsize(OUT_JSON) // 1024
    print(f"✅ macro_dashboard.html + macro_signals.json({kb}KB,"
          f"{len(data['signals'])} 訊號)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
