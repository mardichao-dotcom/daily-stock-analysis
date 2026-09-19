"""
kline_adjust.py — 除權/分割/減資 自動偵測與重調整(W1 閘配套,2026-09-19)。

問題:除權時 TV 把整段歷史價等比例重調,W1 sanity 閘拿「調整後新價 vs 庫裡舊價」
比對 → 誤判單日暴跌 → 永久隔離、每天重隔離、根數遞增(如 6669 8/19 起卡死)。

但不能只看「ratio 一致就當除權」——TV 偶發的錯基準 glitch(如 NVDA 222→46)
也連續多天、ratio 也一致,但 DB 本身是對且 current 的,閘門擋掉它才是對的。

判別三關(全過才算除權;任一不過 → 照常隔離,留 --approve 人工):
  1. 落後關:DB 最新棒落後該市場 ≥ STALE_TRADING_DAYS 個交易日
     (真除權 → TV 只給調整後 → DB 停止進棒 → 卡住;
      glitch → 好棒照進 → DB 仍 current → 不會卡 → 直接排除,這是關鍵區分)
  2. 等比關:重疊區 incoming/DB 的 ratio 一致(max/min < ADJ_RATIO_TOL)且 k 顯著≠1
  3. 權威關:台股用 TWSE 官方 STOCK_DAY 收盤仲裁(誤差 < OFFICIAL_TOL_PCT)

裁決:
  台股三關全過        → 'auto'  (自動重調)
  台股卡住+等比但官方源取不到 → 'manual'(保守,待人工)
  美股(無官方仲裁)卡住+等比 → 'manual'(首階段一律待人工 --approve-split;
                                證明穩定後再放行自動)
  其餘                → 'none'  (照常隔離)
"""
from __future__ import annotations
import statistics

STALE_TRADING_DAYS = 3      # DB 落後市場 ≥ N 交易日才算「卡住」
ADJ_RATIO_TOL = 1.02        # 重疊 ratio max/min 上限(2%)
ADJ_MIN_OVERLAP = 3         # 最少重疊天數
ADJ_MIN_FACTOR_DIFF = 0.05  # k 需與 1 差 ≥5% 才算重調(排除同基準)
OFFICIAL_TOL_PCT = 0.5      # 台股官方仲裁誤差容忍


def _is_tw(symbol: str) -> bool:
    return symbol.split(":")[0] in ("TWSE", "TPEX")


def apply_corporate_action(cur, symbol, k):
    """整段歷史等比重調(除權/分割/減資):價 ×k、量 ÷k。回重調列數。
    等同 TV 調整後基準,且保住 180 天以外的深度(比重抓更完整)。"""
    if not k or k <= 0:
        return 0
    cur.execute(
        "UPDATE kline SET open=open*?, high=high*?, low=low*?, close=close*?, "
        "volume=volume/? WHERE symbol=?", (k, k, k, k, k, symbol))
    return cur.rowcount


def classify_adjustment(symbol, incoming, existing, market_dates,
                        official_fetch=None):
    """
    incoming: {date_iso: close} 這批要匯入的(調整後)收盤
    existing: {date_iso: close} DB 既有收盤
    market_dates: 該市場已觀測到的交易日 sorted list(判落後用)
    official_fetch: fn(code, date_iso)->float|None,台股官方收盤(可注入測試)

    回 verdict dict:
      {'action':'none', 'reason':...}
      {'action':'auto',   'k':k, 'overlap':n, 'official':off, 'sample_date':d}
      {'action':'manual', 'k':k, 'overlap':n, 'reason':...}
    """
    if not existing or not incoming:
        return {"action": "none", "reason": "無既有或無新資料"}

    db_max = max(existing)
    # ── 關 1:落後(DB 停止進棒)──────────────────────────────────
    lag = sum(1 for d in market_dates if d > db_max)
    if lag < STALE_TRADING_DAYS:
        return {"action": "none",
                "reason": f"未卡住(DB 落後市場僅 {lag} 交易日 < {STALE_TRADING_DAYS})"}

    # ── 關 2:等比重調 ─────────────────────────────────────────
    overlap = sorted(set(incoming) & set(existing))
    if len(overlap) < ADJ_MIN_OVERLAP:
        return {"action": "none",
                "reason": f"重疊 {len(overlap)} 天 < {ADJ_MIN_OVERLAP},無法確認等比"}
    ratios = []
    for d in overlap:
        e = existing[d]
        if not e or e <= 0:
            return {"action": "none", "reason": f"{d} 既有值異常({e})"}
        ratios.append(incoming[d] / e)
    if min(ratios) <= 0:
        return {"action": "none", "reason": "ratio 非正"}
    if max(ratios) / min(ratios) > ADJ_RATIO_TOL:
        return {"action": "none",
                "reason": f"ratio 不一致(max/min={max(ratios)/min(ratios):.4f})→ 疑真異常"}
    k = statistics.median(ratios)
    if abs(k - 1) < ADJ_MIN_FACTOR_DIFF:
        return {"action": "none", "reason": f"k={k:.4f} 太接近 1,非重調"}

    # ── 關 3:權威仲裁(台股)/ 美股一律人工 ───────────────────
    # ╔═══════════════════════════════════════════════════════════════════╗
    # ║ ★★★ 隱蔽陷阱,改動前必讀 ★★★                                     ║
    # ║ 官方仲裁一定要用除權「後」的日期(max(incoming)),               ║
    # ║ 絕不可用重疊日(overlap,那是除權「前」)。理由:                 ║
    # ║   • TV 的價是「回溯調整」——除權前的歷史價也被 ×k(如 6669       ║
    # ║     9/01 顯示 2615 = 7800×0.335)。                                ║
    # ║   • TWSE 官方 STOCK_DAY 是「實際成交價」——除權前就是 7800,       ║
    # ║     不回溯調整。                                                   ║
    # ║   → 除權「前」的日子,TV(調整後) ≠ 官方(未調整),拿去比會誤判  ║
    # ║     成 66% 誤差而退回 none;唯有除權「後」的日子兩者才相等。       ║
    # ║   (2026-09-19 first-fix 就踩過這坑:用 overlap[-1] 讓 6669       ║
    # ║    被誤判 none。用 max(incoming)=9/18 才對上官方 2140。)          ║
    # ╚═══════════════════════════════════════════════════════════════════╝
    sample = max(incoming)
    if _is_tw(symbol):
        if official_fetch is None:
            return {"action": "manual", "k": k, "overlap": len(overlap),
                    "reason": "台股但未提供官方仲裁,保守待人工"}
        code = symbol.split(":")[1]
        off = official_fetch(code, sample)
        if off is None:
            return {"action": "manual", "k": k, "overlap": len(overlap),
                    "reason": f"官方源取不到 {sample},保守待人工"}
        err = abs(incoming[sample] - off) / off * 100
        if err < OFFICIAL_TOL_PCT:
            return {"action": "auto", "k": k, "overlap": len(overlap),
                    "official": off, "sample_date": sample}
        return {"action": "none",
                "reason": f"官方 {off} 與調整後 {incoming[sample]:.2f} 差 {err:.2f}% "
                          f">{OFFICIAL_TOL_PCT}% → 疑 glitch,續擋"}
    # 美股:無 TWSE 官方仲裁,首階段一律待人工確認
    return {"action": "manual", "k": k, "overlap": len(overlap),
            "reason": "美股無官方仲裁,待人工 --approve-split"}
