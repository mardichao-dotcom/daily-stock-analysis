"""
render_macro_dashboard.py — 宏觀數據頁骨架(stage12 Day5-6 階段一,2026-07-09 規格版)

分區:技術面 / 情緒面 / 週期 / 總經 / 觀察指標(分區僅為導覽,不含任何權重與小計)。
畫法鐵律(spec):可交易標的(大盤、油價)可 K 線;觀察指標一律線圖/點/柱,
  避免紅綠漲跌語意與指標意義衝突(VIX 漲=利空)。速度類(10Y、台幣)主圖=
  20 日變化柱狀零軸置中,絕對值圖保留上下疊。燈號=燈色時間軸(官方五色)。
資料:全取 macro.db;唯一新增源=油價 brent_daily(FRED DCOILBRENTEU,純觀察不進計分)。
  註:DCOILBRENTEU 為日單值現貨價,無 OHLC——K 線不可得,以線圖呈現
  (要 K 線需改期貨源,屬資料層變更待拍板);台股/美股同理(FMTQIK 僅收盤),
  spec 允許「K線或線圖」→ 線圖。
混頻:08:30 run_macro / 19:00 run_all 皆重產;月頻項標「資料月+發布日」。
🔴 保密紅線:頁/JS/JSON 不得含權重數字組合、打分閾值、類別小計、水位公式
  ——verify_publish._MD_BLACKLIST grep 強制,凍結後更新清單。
階段二預留:每張圖 data-signal 容器即警戒線(addPriceLine)與指針的疊加位,不重做。
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

# 分區(導覽用途;描述不得含分數/權重/所屬占比)
SECTIONS = [
    ("技術面", [
        ("taiex", "台股加權指數與均線",
         "收盤價與 20/60/200 日移動平均。均線反映不同期間的平均持有成本,"
         "價格與均線的相對位置常被市場用來衡量趨勢方向與強度。"),
        ("spx", "美股 S&P 500 與均線",
         "美股大盤與其 20/60/200 日均線。美股為全球風險資產的領頭市場,"
         "其趨勢對台股外資動向有顯著的傳導效果。"),
    ]),
    ("情緒面", [
        ("vix", "VIX 波動率(含 VIX3M 期限結構)",
         "S&P 500 選擇權隱含的未來 30 天年化波動率;VIX3M 為 3 個月期。"
         "VIX 高於 VIX3M(期限結構倒掛)通常出現在急跌恐慌段,"
         "數值升高伴隨避險情緒升溫——此為觀察指標,升跌不以紅綠呈現。"),
        ("umich", "密大消費者信心(月頻)",
         "密西根大學消費者信心指數終值。反映美國家庭對自身財務與整體經濟的預期,"
         "是消費動能與衰退討論最常引用的軟數據之一。"),
    ]),
    ("週期", [
        ("cpi", "美國 CPI:實際 vs 克里夫蘭預測(月頻)",
         "CPI 月增率實際值與克里夫蘭聯儲 Inflation Nowcasting 的會前預測對照。"
         "兩線差距(通膨驚奇)反映公布當下對市場通膨預期的衝擊方向。"),
    ]),
    ("總經", [
        ("light", "台灣景氣對策信號(月頻,燈色時間軸)",
         "國發會景氣對策信號,由 9 項構成項目合成,五色燈號由熱至冷:"
         "紅、黃紅、綠、黃藍、藍;於資料月次月下旬發布。"),
        ("dgs10", "美債 10Y:20 日變化(bp)與絕對水準",
         "上圖為殖利率 20 個交易日變化(基點,零軸置中)——市場對利率『變動速度』"
         "的敏感常高於水準本身;下圖為絕對殖利率,全球資產定價之錨。"),
        ("usdtwd", "台幣:20 日變化(%)與絕對匯率",
         "上圖為 USD/TWD 的 20 個交易日變化率(正=台幣貶值),反映資金進出速度;"
         "下圖為絕對匯率。歷史採 Fed H.10 官方統計,最近數日以市場價暫代、官方到值覆寫。"),
        ("fedwatch", "FedWatch:下次 FOMC 會議市場預期",
         "由聯邦基金期貨自算的下次會議利率變動隱含預期(基點)。正值代表市場"
         "偏向升息、負值偏向降息;非會議月的歷史段無市場定價屬正常。"),
    ]),
    ("觀察指標", [
        ("brent", "布蘭特原油(純觀察,不進任何計分)",
         "Brent 現貨價(FRED/EIA,發布滯後數日)。油價同時牽動通膨預期與"
         "能源類股;此項僅供對照觀察。資料源為日單值現貨價、無開高低收,故以線圖呈現。"),
    ]),
]
ALL_IDS = [sid for _, sigs in SECTIONS for sid, _, _ in sigs]


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def _pct_rank(vals, cur):
    """歷史百分位(≤ 當前值比例;純數據描述,非任何規則)。"""
    xs = [v for v in vals if v is not None]
    if not xs or cur is None:
        return None
    return round(sum(1 for v in xs if v <= cur) / len(xs) * 100)


def _chg_series(dates, vals, n, scale=1.0, nd=1):
    """20 交易日變化序列(速度圖用):out[i] = (v[i]−v[i−n])×scale。"""
    out = []
    for i in range(len(vals)):
        if i < n or vals[i] is None or vals[i - n] is None:
            out.append(None)
        else:
            out.append(round((vals[i] - vals[i - n]) * scale, nd))
    return out


def build_data(db_path: str = MACRO_DB) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    g = conn.execute
    out: dict = {"generated_at": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                 "signals": {}}
    S = out["signals"]

    for market, key in (("TAIEX", "taiex"), ("SPX", "spx")):
        rows = g("SELECT date, close, ma20, ma60, ma200 FROM idx_daily "
                 "WHERE market=? ORDER BY date", (market,)).fetchall()
        S[key] = {"last_date": rows[-1][0], "freq": "daily",
                  "current": {"close": _r(rows[-1][1]), "ma20": _r(rows[-1][2]),
                              "ma60": _r(rows[-1][3]), "ma200": _r(rows[-1][4])},
                  "dates": [r[0] for r in rows],
                  "close": [_r(r[1]) for r in rows],
                  "ma20": [_r(r[2]) for r in rows],
                  "ma60": [_r(r[3]) for r in rows],
                  "ma200": [_r(r[4]) for r in rows]}

    rows = g("SELECT date, close, vix3m FROM vix_daily ORDER BY date").fetchall()
    vixv = [_r(r[1]) for r in rows]
    S["vix"] = {"last_date": rows[-1][0], "freq": "daily",
                "current": {"vix": vixv[-1], "vix3m": _r(rows[-1][2]),
                            "pct_rank": _pct_rank(vixv, vixv[-1])},
                "dates": [r[0] for r in rows], "vix": vixv,
                "vix3m": [_r(r[2]) for r in rows]}

    rows = g("SELECT month, value, release_date FROM umich_monthly "
             "WHERE month >= '1978-01' ORDER BY month").fetchall()
    umv = [_r(r[1], 1) for r in rows]
    S["umich"] = {"last_date": rows[-1][0], "release_date": rows[-1][2],
                  "freq": "monthly",
                  "current": {"value": umv[-1], "pct_rank": _pct_rank(umv, umv[-1])},
                  "months": [r[0] for r in rows], "values": umv}

    rows = g("SELECT target_month, release_date, actual_mom, nowcast_mom "
             "FROM cpi_events ORDER BY target_month").fetchall()
    S["cpi"] = {"last_date": rows[-1][0], "release_date": rows[-1][1],
                "freq": "monthly",
                "current": {"actual": _r(rows[-1][2], 3), "nowcast": _r(rows[-1][3], 3)},
                "months": [r[0] for r in rows],
                "actual": [_r(r[2], 3) for r in rows],
                "nowcast": [_r(r[3], 3) for r in rows]}

    rows = g("SELECT month, score, light, release_date FROM light_monthly "
             "ORDER BY month").fetchall()
    S["light"] = {"last_date": rows[-1][0], "release_date": rows[-1][3],
                  "freq": "monthly",
                  "current": {"score": _r(rows[-1][1], 0), "light": rows[-1][2]},
                  "months": [r[0] for r in rows],
                  "scores": [_r(r[1], 0) for r in rows],
                  "lights": [r[2] for r in rows]}

    rows = g("SELECT date, value FROM dgs10_daily ORDER BY date").fetchall()
    dts, vals = [r[0] for r in rows], [_r(r[1]) for r in rows]
    chg = _chg_series(dts, vals, 20, scale=100, nd=0)     # bp
    S["dgs10"] = {"last_date": dts[-1], "freq": "daily",
                  "current": {"value": vals[-1], "chg20_bp": chg[-1],
                              "pct_rank": _pct_rank(vals, vals[-1])},
                  "dates": dts, "values": vals, "chg20_bp": chg}

    rows = g("SELECT date, rate, source FROM usdtwd_daily ORDER BY date").fetchall()
    dts = [r[0] for r in rows]
    rates = [_r(r[1], 3) for r in rows]
    chgp = []
    for i in range(len(rates)):
        if i < 20 or rates[i] is None or rates[i - 20] in (None, 0):
            chgp.append(None)
        else:
            chgp.append(round((rates[i] / rates[i - 20] - 1) * 100, 2))
    S["usdtwd"] = {"last_date": dts[-1], "freq": "daily",
                   "current": {"rate": rates[-1], "chg20_pct": chgp[-1],
                               "provisional": "暫代" in (rows[-1][2] or "")},
                   "dates": dts, "rates": rates, "chg20_pct": chgp}

    rows = g("SELECT date, next_meeting, expected_change_bp "
             "FROM fed_expectations_daily ORDER BY date").fetchall()
    S["fedwatch"] = {"last_date": rows[-1][0], "freq": "daily",
                     "current": {"next_meeting": rows[-1][1],
                                 "expected_bp": _r(rows[-1][2], 1)},
                     "dates": [r[0] for r in rows],
                     "expected_bp": [_r(r[2], 1) for r in rows]}

    rows = g("SELECT date, price FROM brent_daily ORDER BY date").fetchall()
    pv = [_r(r[1]) for r in rows]
    S["brent"] = {"last_date": rows[-1][0], "freq": "daily",
                  "current": {"price": pv[-1], "pct_rank": _pct_rank(pv, pv[-1])},
                  "dates": [r[0] for r in rows], "prices": pv}

    conn.close()
    return out


def render_html() -> str:
    gen = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    body = []
    for sec_name, sigs in SECTIONS:
        body.append(f'<h2 class="md-sec">{sec_name}</h2>')
        for sid, title, desc in sigs:
            body.append(f"""
<section class="md-signal" id="sig-{sid}">
  <div class="md-head">
    <h3>{title}</h3>
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
    <div class="meta">宏觀訊號歷史序列與當前值 ｜ 日頻項每日 08:30 更新、月頻項照各自公布節律 ｜ 頁面產出 {gen}</div>
  </div>
</header>
<main class="container md-container">
  <div class="md-intro">
    <span class="md-genstamp" data-genstamp>資料生成:—</span>
    <p class="md-bands-note">警戒區為各訊號歷史分布之極端值標注(bands_v1),僅供辨識罕見狀態,不代表漲跌方向,與本站任何內部計分模型無關。門檻以現有樣本期(多數 2012 年起)計算,尚不含 2000、2008 兩次結構性熊市;VIX 上緣對「真正危機的高」偏低估,序列回補至 2000 年後需重算並升版 bands_v2。</p>
  </div>
{''.join(body)}
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
          f"{len(data['signals'])} 訊號 / {len(SECTIONS)} 分區)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
