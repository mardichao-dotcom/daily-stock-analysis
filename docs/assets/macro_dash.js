/* macro_dash.js — 宏觀數據頁(stage12 Day5-6 階段一,2026-07-09 規格版)
 *
 * 讀 data/v2/macro_signals.json,以 Lightweight Charts 畫十圖(9 訊號,速度類上下疊)。
 * 🔴 保密紅線:本檔不得含內部模型的任何參數字樣(verify_publish 黑名單 grep 斷言,
 *              清單見 _MD_BLACKLIST——連註解都不能提,此處故意寫得含糊)。
 * 畫法鐵律(spec):觀察指標一律線/點/柱不用 K 線;速度類主圖=20 日變化柱狀零軸置中;
 *   燈號=官方五色時間軸(此為官方領域色,非漲跌語意);其餘線色藍階+既有 MA 色。
 * 階段二:警戒線用 series.createPriceLine、指針掛 .md-current——增量疊加,不重做。
 */
(function () {
  'use strict';
  const LWC_CDN = 'https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js';
  let lwcP = null;
  function loadLWC() {
    if (window.LightweightCharts) return Promise.resolve();
    if (lwcP) return lwcP;
    lwcP = new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = LWC_CDN;
      s.onload = res;
      s.onerror = () => rej(new Error('LWC 載入失敗'));
      document.head.appendChild(s);
    });
    return lwcP;
  }

  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  function themeOpts() {
    return {
      layout: { background: { color: css('--surface-panel') || '#131722' },
                textColor: css('--text-muted') || '#787b86' },
      grid: { vertLines: { color: css('--border') || '#2a2e39' },
              horzLines: { color: css('--border') || '#2a2e39' } },
      rightPriceScale: { borderColor: css('--border') || '#2a2e39' },
      timeScale: { borderColor: css('--border') || '#2a2e39' },
    };
  }

  const BLUE = '#2962ff', PALE = '#c9d6f2', GRAY = '#787b86';
  const MA_C = { ma20: '#fbbf24', ma60: '#a855f7', ma200: '#06b6d4' };
  // 燈號官方五色(領域語意,非漲跌):紅/黃紅/綠/黃藍/藍
  const LIGHT_C = { '紅': '#e03430', '黃紅': '#f59e0b', '綠': '#22c55e',
                    '黃藍': '#38bdf8', '藍': '#2962ff' };
  const _charts = [];

  function line(chart, color, width) {
    return chart.addLineSeries({ color: color, lineWidth: width || 1.5,
                                 priceLineVisible: false, lastValueVisible: false });
  }
  function pts(dates, vals) {
    const out = [];
    for (let i = 0; i < dates.length; i++)
      if (vals[i] != null) out.push({ time: dates[i], value: vals[i] });
    return out;
  }
  const mdates = months => months.map(m => m + '-01');
  const fmt = (v, nd) => v == null ? '—' :
    Number(v).toLocaleString('en-US', { minimumFractionDigits: nd == null ? 2 : nd,
                                        maximumFractionDigits: nd == null ? 2 : nd });
  const rank = c => c && c.pct_rank != null
    ? `<span class="md-note">歷史百分位 ${c.pct_rank}%</span>` : '';

  function mkChart(el, h) {
    const div = document.createElement('div');
    el.appendChild(div);
    const chart = LightweightCharts.createChart(div, {
      width: el.clientWidth || 800, height: h || 240, ...themeOpts(),
      timeScale: { timeVisible: false },
    });
    _charts.push({ chart: chart, host: el });
    return chart;
  }

  // 20 日變化柱狀(零軸置中;單色藍,速度非漲跌)
  function speedBars(el, dates, vals, h) {
    const c = mkChart(el, h || 200);
    const s = c.addHistogramSeries({ color: BLUE, priceLineVisible: false,
                                     lastValueVisible: false, base: 0 });
    s.setData(pts(dates, vals).map(p => ({ ...p, color: BLUE })));
    c.priceScale('right').applyOptions({ scaleMargins: { top: 0.15, bottom: 0.15 } });
    return c;
  }

  function idx(el, s) {
    const c = mkChart(el, 300);
    line(c, BLUE, 2).setData(pts(s.dates, s.close));
    ['ma20', 'ma60', 'ma200'].forEach(k => line(c, MA_C[k], 1).setData(pts(s.dates, s[k])));
    return `收盤 <strong>${fmt(s.current.close)}</strong>`
      + ` · MA20 ${fmt(s.current.ma20)} · MA60 ${fmt(s.current.ma60)} · MA200 ${fmt(s.current.ma200)}`
      + legend([['收盤', BLUE], ['MA20', MA_C.ma20], ['MA60', MA_C.ma60], ['MA200', MA_C.ma200]]);
  }

  const RENDER = {
    taiex: idx, spx: idx,
    vix: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(s.dates, s.vix));
      line(c, PALE, 1).setData(pts(s.dates, s.vix3m));
      const inv = (s.current.vix != null && s.current.vix3m != null
                   && s.current.vix > s.current.vix3m);
      return `VIX <strong>${fmt(s.current.vix)}</strong> · VIX3M <strong>${fmt(s.current.vix3m)}</strong>`
        + `<span class="md-note">期限結構${inv ? '倒掛(短端恐慌高於中期)' : '正常(短端低於中期)'}</span>`
        + rank(s.current) + legend([['VIX', BLUE], ['VIX3M', PALE]]);
    },
    umich: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(mdates(s.months), s.values));
      return `<strong>${fmt(s.current.value, 1)}</strong>` + rank(s.current)
        + `<span class="md-note">資料月 ${s.last_date} · 發布 ${s.release_date}</span>`;
    },
    cpi: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(mdates(s.months), s.actual));
      line(c, PALE, 1).setData(pts(mdates(s.months), s.nowcast));
      return `實際 <strong>${fmt(s.current.actual, 3)}%</strong> · 會前預測 <strong>${fmt(s.current.nowcast, 3)}%</strong>`
        + `<span class="md-note">資料月 ${s.last_date} · 發布 ${s.release_date}</span>`
        + legend([['實際 m/m', BLUE], ['克里夫蘭預測', PALE]]);
    },
    light: function (el, s) {
      // 燈色時間軸:柱高=綜合分數、柱色=官方燈色
      const c = mkChart(el, 220);
      const hs = c.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      const data = [];
      for (let i = 0; i < s.months.length; i++) {
        if (s.scores[i] == null) continue;
        data.push({ time: s.months[i] + '-01', value: s.scores[i],
                    color: LIGHT_C[s.lights[i]] || GRAY });
      }
      hs.setData(data);
      return `<strong>${fmt(s.current.score, 0)} 分(${s.current.light}燈)</strong>`
        + `<span class="md-note">資料月 ${s.last_date} · 發布 ${s.release_date}</span>`
        + legend(Object.keys(LIGHT_C).map(k => [k, LIGHT_C[k]]));
    },
    dgs10: function (el, s) {
      speedBars(el, s.dates, s.chg20_bp);            // 主圖:20 日變化 bp
      const c2 = mkChart(el, 140);                    // 副圖:絕對值
      line(c2, GRAY, 1.2).setData(pts(s.dates, s.values));
      return `<strong>${fmt(s.current.value)}%</strong> · 20日 <strong>${fmt(s.current.chg20_bp, 0)} bp</strong>`
        + rank(s.current)
        + legend([['20日變化(bp,上圖)', BLUE], ['絕對殖利率(下圖)', GRAY]]);
    },
    usdtwd: function (el, s) {
      speedBars(el, s.dates, s.chg20_pct);           // 主圖:20 日變化 %
      const c2 = mkChart(el, 140);                    // 副圖:絕對匯率
      line(c2, GRAY, 1.2).setData(pts(s.dates, s.rates));
      return `<strong>${fmt(s.current.rate, 3)}</strong> · 20日 <strong>${fmt(s.current.chg20_pct, 2)}%</strong>`
        + (s.current.provisional ? '<span class="md-note">最新值為市場價暫代(官方 H.10 到值後覆寫)</span>' : '')
        + legend([['20日變化(%,上圖;正=台幣貶)', BLUE], ['絕對匯率(下圖)', GRAY]]);
    },
    fedwatch: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(s.dates, s.expected_bp));
      const bp = s.current.expected_bp;
      const dir = bp == null ? '—' : (bp > 0 ? '偏向升息' : (bp < 0 ? '偏向降息' : '按兵不動'));
      return `下次會議 ${s.current.next_meeting}:隱含 <strong>${fmt(bp, 1)} bp</strong>(${dir})`
        + '<span class="md-note">單位:基點;非會議月的歷史段無市場數據屬正常</span>';
    },
    brent: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(s.dates, s.prices));
      return `<strong>$${fmt(s.current.price)}</strong>/桶` + rank(s.current)
        + '<span class="md-note">FRED/EIA 現貨,發布滯後數日;純觀察不進計分</span>';
    },
  };

  function legend(items) {
    return '<span class="md-legend">' + items.map(
      it => `<span><i style="background:${it[1]}"></i>${it[0]}</span>`).join('') + '</span>';
  }

  async function boot() {
    let data;
    try {
      const r = await fetch('data/v2/macro_signals.json', { cache: 'no-store' });
      data = await r.json();
      await loadLWC();
    } catch (e) {
      document.querySelectorAll('.md-current').forEach(el => {
        el.textContent = '⚠️ 資料載入失敗:' + e.message;
      });
      return;
    }
    document.querySelectorAll('.md-chart').forEach(el => {
      const sid = el.dataset.signal;
      const s = data.signals[sid];
      const cur = document.querySelector(`[data-current="${sid}"]`);
      const upd = document.querySelector(`[data-updated="${sid}"]`);
      if (!s) { if (cur) cur.textContent = '無資料'; return; }
      if (upd) upd.textContent = '更新至 ' + s.last_date;
      try {
        const html = RENDER[sid](el, s);
        if (cur) cur.innerHTML = html;
      } catch (e) {
        if (cur) cur.textContent = '⚠️ 圖表渲染失敗:' + e.message;
      }
    });
    window.addEventListener('themechange', () => {
      const o = themeOpts();
      _charts.forEach(c => { try { c.chart.applyOptions(o); } catch (e) { /* */ } });
    });
    window.addEventListener('resize', () => {
      _charts.forEach(c => {
        try { c.chart.applyOptions({ width: c.host.clientWidth }); } catch (e) { /* */ }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
