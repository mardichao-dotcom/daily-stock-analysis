/* macro_dash.js — 宏觀數據頁(stage12 Day5-6 階段一,2026-07-09)
 *
 * 讀 data/v2/macro_signals.json,以 Lightweight Charts 畫八訊號歷史線。
 * 🔴 紅線:本檔不得含內部模型的任何參數字樣(verify_publish 黑名單 grep 斷言,
 *          清單見 _MD_BLACKLIST——連註解都不能提,此處故意寫得含糊)。
 * 語意鐵律:不用紅綠(漲跌語意保留給行情頁);線色一律藍階+既有 MA 色。
 * 階段二將在此疊加警戒線(addPriceLine)與指針——增量,不重做。
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

  const BLUE = '#2962ff', LBLUE = '#8fb3ff', PALE = '#c9d6f2', GRAY = '#787b86';
  const MA_C = { ma20: '#fbbf24', ma60: '#a855f7', ma200: '#06b6d4' };
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

  function mkChart(el, h) {
    const chart = LightweightCharts.createChart(el, {
      width: el.clientWidth || 800, height: h || 260, ...themeOpts(),
      timeScale: { timeVisible: false },
    });
    _charts.push(chart);
    return chart;
  }

  // 各訊號:當前值列 + 線序列
  const RENDER = {
    taiex: idx, spx: idx,
    vix: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(s.dates, s.vix));
      line(c, PALE, 1).setData(pts(s.dates, s.vix3m));
      return `VIX <strong>${fmt(s.current.vix)}</strong> · VIX3M <strong>${fmt(s.current.vix3m)}</strong>`
        + legend([['VIX', BLUE], ['VIX3M', PALE]]);
    },
    umich: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(mdates(s.months), s.values));
      return `<strong>${fmt(s.current.value, 1)}</strong>`
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
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(mdates(s.months), s.scores));
      return `<strong>${fmt(s.current.score, 0)} 分(${s.current.light}燈)</strong>`
        + `<span class="md-note">資料月 ${s.last_date} · 發布 ${s.release_date}</span>`;
    },
    dgs10: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(s.dates, s.values));
      return `<strong>${fmt(s.current.value)}%</strong>`;
    },
    usdtwd: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(s.dates, s.rates));
      return `<strong>${fmt(s.current.rate, 3)}</strong>`
        + (s.current.provisional ? '<span class="md-note">最新值為市場即時價暫代(官方 H.10 到值後覆寫)</span>' : '');
    },
    fedwatch: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(s.dates, s.expected_bp));
      const bp = s.current.expected_bp;
      const dir = bp == null ? '—' : (bp > 0 ? '偏向升息' : (bp < 0 ? '偏向降息' : '按兵不動'));
      return `下次會議 ${s.current.next_meeting}:隱含 <strong>${fmt(bp, 1)} bp</strong>(${dir})`
        + '<span class="md-note">單位:基點;非會議月的歷史段無市場數據屬正常</span>';
    },
  };

  function idx(el, s) {
    const c = mkChart(el, 300);
    line(c, BLUE, 2).setData(pts(s.dates, s.close));
    ['ma20', 'ma60', 'ma200'].forEach(k => line(c, MA_C[k], 1).setData(pts(s.dates, s[k])));
    return `收盤 <strong>${fmt(s.current.close)}</strong>`
      + ` · MA20 ${fmt(s.current.ma20)} · MA60 ${fmt(s.current.ma60)} · MA200 ${fmt(s.current.ma200)}`
      + legend([['收盤', BLUE], ['MA20', MA_C.ma20], ['MA60', MA_C.ma60], ['MA200', MA_C.ma200]]);
  }

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
    // 主題切換 → 重刷圖表底色(theme.js 派發 themechange)
    window.addEventListener('themechange', () => {
      const o = themeOpts();
      _charts.forEach(ch => { try { ch.applyOptions(o); } catch (e) { /* 已銷毀 */ } });
    });
    window.addEventListener('resize', () => {
      document.querySelectorAll('.md-chart').forEach((el, i) => {
        try { _charts[i].applyOptions({ width: el.clientWidth }); } catch (e) { /* */ }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
