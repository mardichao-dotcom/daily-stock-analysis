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

  // ── 警戒線門檻(bands_v1,凍結;集中定義,勿散落各處)──────────────────────
  // 各數值為對應訊號在「現有樣本期(多數 2012-06 起)」歷史分布之極端值標注,
  // 僅供辨識罕見狀態,不表方向、不表好壞,與任何內部計分模型無關。
  // ⚠️ 樣本期不含 2000、2008 兩次結構性熊市:VIX 上緣(25.7/29.4)對「日常的高」
  //    準確,對「真正危機的高」偏低估。idx 序列回補至 2000 後需重算、升版 bands_v2。
  const BANDS_V1 = {
    vix:        { watch: [12.1, 25.7],   strong: [11.3, 29.4] },   // 雙側(值序列)
    vixRatio:   { watch: 1.00,           strong: 1.05 },           // 單側上緣(比值,倒掛)
    umich:      { watch: [64.3, 99.2],   strong: [59.2, 105.2] },  // 雙側 + 邊緣觸發
    cpiSurprise:{ watch: 15,             strong: 24 },             // 單側 |surprise| bp
    dgs10Chg:   { watch: [-25, 30],      strong: [-34, 40] },      // 20 日變化圖雙側 bp
    usdtwdChg:  { watch: [-1.44, 1.65],  strong: [-2.00, 2.13] },  // 20 日變化圖雙側 %
    fedwatch:   { watch: 25,             strong: 50 },             // 單側 |bp|(制度單次幅度)
  };
  // 警戒線一律中性色(灰=關注、琥珀=強烈關注),不用紅綠(避免被讀成好壞)。
  const BAND_C = { watch: GRAY, strong: '#d97706' };

  // 在某序列上疊加水平警戒線(createPriceLine;虛線、右軸標題)。
  // lines: [{ price, level:'watch'|'strong', title }]
  function addBands(series, lines) {
    if (!series || !series.createPriceLine) return;
    const dashed = (window.LightweightCharts && LightweightCharts.LineStyle
                    ? LightweightCharts.LineStyle.Dashed : 2);
    lines.forEach(l => {
      if (l.price == null) return;
      series.createPriceLine({ price: l.price, color: BAND_C[l.level], lineWidth: 1,
                               lineStyle: dashed, axisLabelVisible: true, title: l.title });
    });
  }

  // 雙側門檻越線判定文字(關注/強烈關注/在常態區);回空字串代表常態、不亮燈。
  function crossNote(val, band, unit, label) {
    if (val == null) return '';
    const lo = band.watch[0], hi = band.watch[1];
    const sLo = band.strong[0], sHi = band.strong[1];
    let hit = '';
    if (val <= sLo || val >= sHi) hit = '強烈關注';
    else if (val <= lo || val >= hi) hit = '關注';
    if (!hit) return '';
    return `<span class="md-note md-band">⚑ ${label}${hit}(${fmt(val, unit === 'bp' ? 0 : 2)}${unit})</span>`;
  }

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
  // pct_rank 標籤:label 可覆寫(B1 dgs10 需明寫「10Y絕對水準 歷史百分位」以免誤讀為變化速度分位)
  const rank = (c, label) => c && c.pct_rank != null
    ? `<span class="md-note">${label || '歷史百分位'} ${c.pct_rank}%</span>` : '';
  // 日頻資料日標籤(A:讓讀圖者一眼分辨日頻=今天 vs 月頻=1~2月前)
  const dataDay = s => `<span class="md-note">資料日 ${s.last_date}</span>`;

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
      + dataDay(s)
      + legend([['收盤', BLUE], ['MA20', MA_C.ma20], ['MA60', MA_C.ma60], ['MA200', MA_C.ma200]]);
  }

  const RENDER = {
    taiex: idx, spx: idx,
    vix: function (el, s) {
      const c = mkChart(el);
      const vs = line(c, BLUE, 2); vs.setData(pts(s.dates, s.vix));
      line(c, PALE, 1).setData(pts(s.dates, s.vix3m));
      // C:VIX 水準雙側警戒線(畫在 VIX 值序列;bands_v1)
      const b = BANDS_V1.vix;
      addBands(vs, [
        { price: b.watch[0], level: 'watch', title: `關注 ${b.watch[0]}` },
        { price: b.watch[1], level: 'watch', title: `關注 ${b.watch[1]}` },
        { price: b.strong[0], level: 'strong', title: `強烈 ${b.strong[0]}` },
        { price: b.strong[1], level: 'strong', title: `強烈 ${b.strong[1]}` },
      ]);
      // C:期限結構改用「比值」ratio = VIX/VIX3M(尺度可比,不用差值);說明列標示越線
      const ratio = (s.current.vix != null && s.current.vix3m)
        ? s.current.vix / s.current.vix3m : null;
      const rb = BANDS_V1.vixRatio;
      let rtxt = '';
      if (ratio != null) {
        const tag = ratio > rb.strong ? '深度倒掛' : (ratio > rb.watch ? '倒掛' : '正常');
        const flag = ratio > rb.watch ? '⚑ ' : '';
        rtxt = `<span class="md-note${ratio > rb.watch ? ' md-band' : ''}">${flag}期限結構比值 ${fmt(ratio, 3)}(${tag};>1.00 倒掛、>1.05 深度倒掛)</span>`;
      }
      return `VIX <strong>${fmt(s.current.vix)}</strong> · VIX3M <strong>${fmt(s.current.vix3m)}</strong>`
        + rtxt
        + crossNote(s.current.vix, b, '', 'VIX ')
        + rank(s.current) + dataDay(s)
        + legend([['VIX', BLUE], ['VIX3M', PALE]]);
    },
    umich: function (el, s) {
      const c = mkChart(el);
      const us = line(c, BLUE, 2); us.setData(pts(mdates(s.months), s.values));
      // C:密大雙側警戒線(參考線)+ 邊緣觸發(僅「本月跨線」才亮,避免持續破底變死燈)
      const b = BANDS_V1.umich;
      addBands(us, [
        { price: b.watch[0], level: 'watch', title: `關注 ${b.watch[0]}` },
        { price: b.watch[1], level: 'watch', title: `關注 ${b.watch[1]}` },
        { price: b.strong[0], level: 'strong', title: `強烈 ${b.strong[0]}` },
        { price: b.strong[1], level: 'strong', title: `強烈 ${b.strong[1]}` },
      ]);
      const vv = (s.values || []).filter(v => v != null);
      const cur = vv[vv.length - 1], prev = vv[vv.length - 2];
      let edge = '';
      if (cur != null && prev != null) {
        const thr = [b.strong[0], b.watch[0], b.watch[1], b.strong[1]];
        const crossed = thr.find(t => (prev - t) * (cur - t) < 0);
        if (crossed != null) {
          const lvl = (crossed === b.strong[0] || crossed === b.strong[1]) ? '強烈關注' : '關注';
          edge = `<span class="md-note md-band">⚑ 本月跨越 ${crossed}(${fmt(prev,1)}→${fmt(cur,1)},${lvl})</span>`;
        }
      }
      return `<strong>${fmt(s.current.value, 1)}</strong>` + rank(s.current) + edge
        + `<span class="md-note">資料月 ${s.last_date} · 發布 ${s.release_date}</span>`;
    },
    cpi: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(mdates(s.months), s.actual));
      line(c, PALE, 1).setData(pts(mdates(s.months), s.nowcast));
      // C:CPI 驚奇警戒帶對象是「兩線差距」而非任一條線 → 本輪以說明列承載 |surprise|,
      //    surprise 差值子面板待下一輪(避免為單一警戒帶新增整張圖的工程成本)
      const a = s.current.actual, n = s.current.nowcast;
      let sup = '';
      if (a != null && n != null) {
        const bp = Math.abs(a - n) * 100;
        const b = BANDS_V1.cpiSurprise;
        const hit = bp >= b.strong ? '強烈關注' : (bp >= b.watch ? '關注' : '');
        sup = `<span class="md-note${hit ? ' md-band' : ''}">${hit ? '⚑ ' : ''}驚奇 |實際−預測| ${fmt(bp, 0)} bp${hit ? '(' + hit + ';>15 關注、>24 強烈)' : ''}</span>`;
      }
      return `實際 <strong>${fmt(s.current.actual, 3)}%</strong> · 會前預測 <strong>${fmt(s.current.nowcast, 3)}%</strong>`
        + sup
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
      const c1 = speedBars(el, s.dates, s.chg20_bp);   // 主圖:20 日變化 bp
      // C:警戒線畫在「20 日變化」圖(絕對水準圖不畫——它是時間軸代名詞);bands_v1
      const b = BANDS_V1.dgs10Chg;
      const chgSeries = c1.addLineSeries({ color: 'rgba(0,0,0,0)', lastValueVisible: false,
                                           priceLineVisible: false });
      chgSeries.setData(pts(s.dates, s.chg20_bp));
      addBands(chgSeries, [
        { price: b.watch[0], level: 'watch', title: `關注 ${b.watch[0]}` },
        { price: b.watch[1], level: 'watch', title: `關注 +${b.watch[1]}` },
        { price: b.strong[0], level: 'strong', title: `強烈 ${b.strong[0]}` },
        { price: b.strong[1], level: 'strong', title: `強烈 +${b.strong[1]}` },
      ]);
      const c2 = mkChart(el, 140);                    // 副圖:絕對值(不畫警戒線)
      line(c2, GRAY, 1.2).setData(pts(s.dates, s.values));
      return `<strong>${fmt(s.current.value)}%</strong> · 20日 <strong>${fmt(s.current.chg20_bp, 0)} bp</strong>`
        + crossNote(s.current.chg20_bp, b, 'bp', '20日變化 ')
        + rank(s.current, '10Y絕對水準 歷史百分位')     // B1:明寫「絕對水準」,避免誤讀為變化速度分位
        + dataDay(s)
        + legend([['20日變化(bp,上圖)', BLUE], ['絕對殖利率(下圖)', GRAY]]);
    },
    usdtwd: function (el, s) {
      const c1 = speedBars(el, s.dates, s.chg20_pct);  // 主圖:20 日變化 %
      // C:警戒線畫在「20 日變化」圖(絕對匯率圖不畫,同 10Y 理由);bands_v1
      const b = BANDS_V1.usdtwdChg;
      const chgSeries = c1.addLineSeries({ color: 'rgba(0,0,0,0)', lastValueVisible: false,
                                           priceLineVisible: false });
      chgSeries.setData(pts(s.dates, s.chg20_pct));
      addBands(chgSeries, [
        { price: b.watch[0], level: 'watch', title: `關注 ${b.watch[0]}%` },
        { price: b.watch[1], level: 'watch', title: `關注 +${b.watch[1]}%` },
        { price: b.strong[0], level: 'strong', title: `強烈 ${b.strong[0]}%` },
        { price: b.strong[1], level: 'strong', title: `強烈 +${b.strong[1]}%` },
      ]);
      const c2 = mkChart(el, 140);                    // 副圖:絕對匯率(不畫警戒線)
      line(c2, GRAY, 1.2).setData(pts(s.dates, s.rates));
      return `<strong>${fmt(s.current.rate, 3)}</strong> · 20日 <strong>${fmt(s.current.chg20_pct, 2)}%</strong>`
        + crossNote(s.current.chg20_pct, b, '%', '20日變化 ')
        + (s.current.provisional ? '<span class="md-note">最新值為市場價暫代(官方 H.10 到值後覆寫)</span>' : '')
        + dataDay(s)
        + legend([['20日變化(%,上圖;正=台幣貶)', BLUE], ['絕對匯率(下圖)', GRAY]]);
    },
    fedwatch: function (el, s) {
      const c = mkChart(el);
      const fs = line(c, BLUE, 2); fs.setData(pts(s.dates, s.expected_bp));
      // C:用制度單位(不用百分位——52.9% 的日子貼 0 附近,百分位失真)。
      //    25bp = FOMC 單次調整標準幅度,是制度常數;雙側對稱畫線。
      const b = BANDS_V1.fedwatch;
      addBands(fs, [
        { price: b.watch, level: 'watch', title: `關注 +${b.watch}` },
        { price: -b.watch, level: 'watch', title: `關注 -${b.watch}` },
        { price: b.strong, level: 'strong', title: `強烈 +${b.strong}` },
        { price: -b.strong, level: 'strong', title: `強烈 -${b.strong}` },
      ]);
      const bp = s.current.expected_bp;
      const dir = bp == null ? '—' : (bp > 0 ? '偏向升息' : (bp < 0 ? '偏向降息' : '按兵不動'));
      let hit = '';
      if (bp != null) {
        const ab = Math.abs(bp);
        const lv = ab >= b.strong ? '強烈關注' : (ab >= b.watch ? '關注' : '');
        if (lv) hit = `<span class="md-note md-band">⚑ 市場定價 ${fmt(bp, 0)} bp(${lv};±25 一整碼、±50 兩碼)</span>`;
      }
      return `下次會議 ${s.current.next_meeting}:隱含 <strong>${fmt(bp, 1)} bp</strong>(${dir})`
        + hit + dataDay(s)
        + '<span class="md-note">單位:基點;非會議月的歷史段無市場數據屬正常</span>';
    },
    brent: function (el, s) {
      const c = mkChart(el);
      line(c, BLUE, 2).setData(pts(s.dates, s.prices));
      // B2:拿掉 pct_rank 顯示(僅 276 筆、2025-06 起,樣本太淺不具代表性);不畫警戒線(純觀察)
      return `<strong>$${fmt(s.current.price)}</strong>/桶` + dataDay(s)
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
    // A:整頁時間錨——顯示 JSON 的 generated_at(資料生成時刻)
    const stamp = document.querySelector('[data-genstamp]');
    if (stamp && data.generated_at) {
      stamp.textContent = '資料生成:' + String(data.generated_at).slice(0, 16).replace('T', ' ');
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
