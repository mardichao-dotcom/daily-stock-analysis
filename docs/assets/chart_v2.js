/**
 * chart_v2.js — Stage 8 W3 K 線圖 (TradingView Lightweight Charts)
 *
 * 流程:
 *   1. 找 .chart-placeholder DOM 元素
 *   2. 點擊或父 <details> 展開 → fetch chart data + visual.json
 *   3. 渲染 K 棒 / MA / 關鍵價 / 區域 / 事件 markers / ETF 箭頭
 *   4. ETF 勾選框控制顯示
 *   5. Hover 顯示 tooltip (subscribeCrosshairMove + 自畫 div)
 *
 * 顏色/線型全部從 visual.json,不在 JS hardcode。
 */

(function() {
  'use strict';

  const LWC_CDN = 'https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js';
  const VISUAL_PATH = 'data/v2/visual.json';
  let visualCache = null;
  let lwcLoaded = false;
  let lwcLoadingPromise = null;

  // ─── 載入 Lightweight Charts 一次 ───────────────────────────────────────
  function loadLightweightCharts() {
    if (lwcLoaded) return Promise.resolve();
    if (lwcLoadingPromise) return lwcLoadingPromise;
    lwcLoadingPromise = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = LWC_CDN;
      s.onload = () => { lwcLoaded = true; resolve(); };
      s.onerror = () => reject(new Error('Failed to load Lightweight Charts'));
      document.head.appendChild(s);
    });
    return lwcLoadingPromise;
  }

  // ─── 載入 visual.json 一次 ──────────────────────────────────────────────
  async function loadVisual() {
    if (visualCache) return visualCache;
    const resp = await fetch(VISUAL_PATH);
    if (!resp.ok) throw new Error(`visual.json ${resp.status}`);
    visualCache = await resp.json();
    return visualCache;
  }

  // ─── 載入 stock chart data ─────────────────────────────────────────────
  async function loadChartData(symbol, date) {
    const safeId = symbol.replace(':', '_');
    const path = `data/v2/${date}/${safeId}.json`;
    const resp = await fetch(path);
    if (!resp.ok) throw new Error(`chart ${path} ${resp.status}`);
    return await resp.json();
  }

  // ─── 線型轉 LineStyle enum ──────────────────────────────────────────────
  function styleNameToEnum(styleName) {
    // LightweightCharts.LineStyle: 0=Solid, 1=Dotted, 2=Dashed, 3=LargeDashed, 4=SparseDotted
    const map = { solid: 0, dotted: 1, dashed: 2 };
    return map[styleName] ?? 0;
  }

  // ─── 渲染 ETF 勾選框 ────────────────────────────────────────────────────
  function buildEtfFilter(container, etfEvents, onChange) {
    const etfs = [...new Set(etfEvents.map(e => e.etf))].sort();
    if (etfs.length === 0) return null;

    const bar = document.createElement('div');
    bar.className = 'etf-filter-bar';
    bar.innerHTML = '<strong>ETF:</strong>';
    const checked = new Set(etfs);
    etfs.forEach(etf => {
      const label = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = true;
      cb.value = etf;
      cb.addEventListener('change', () => {
        if (cb.checked) checked.add(etf); else checked.delete(etf);
        onChange(checked);
      });
      label.appendChild(cb);
      label.append(' ' + etf);
      bar.appendChild(label);
    });
    container.appendChild(bar);
    return checked;
  }

  function esc0(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  // ─── 主題感知(stage10 Batch2)────────────────────────────────────────────
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v || '').trim() || fallback;
  }
  function chartThemeOptions() {
    return {
      layout: { background: { color: cssVar('--surface-panel', '#fff') },
                textColor: cssVar('--text-muted', '#333') },
      grid: { vertLines: { color: cssVar('--divider', '#f0f0f0') },
              horzLines: { color: cssVar('--divider', '#f0f0f0') } },
      rightPriceScale: { borderColor: cssVar('--border', '#e5e7eb') },
    };
  }
  const _liveCharts = [];
  window.addEventListener('themechange', function () {
    const opts = chartThemeOptions();
    _liveCharts.forEach(c => { try { c.applyOptions(opts); } catch (e) { /* 已銷毀 */ } });
  });

  // ─── 渲染單張 chart ─────────────────────────────────────────────────────
  async function renderChart(container, data, visual) {
    container.innerHTML = '';
    container.classList.add('chart-loaded');   // §3:載入後容器轉 block/實底(等待態斜紋只給未載入)
    container.style.minHeight = '420px';

    // 建立 chart 容器(扣掉 ETF filter bar 的高度)
    const chartEl = document.createElement('div');
    chartEl.style.width = '100%';
    chartEl.style.height = '420px';

    // 先掛進 DOM,讓 clientWidth 量得到(details 剛展開時若仍是 0,退到 660)
    container.appendChild(chartEl);

    // §3(stage10 Batch2):圖表容器底/文字/格線跟 tokens 走(深淺主題皆正確);
    // K 棒/均線/關鍵價/箭頭顏色照舊(紅線:圖表內部邏輯不動)。
    const chart = LightweightCharts.createChart(chartEl, {
      width:  chartEl.clientWidth || 660,
      height: 420,
      ...chartThemeOptions(),
      timeScale: { timeVisible: false, secondsVisible: false },
    });
    _liveCharts.push(chart);

    // K 棒
    const candleSeries = chart.addCandlestickSeries({
      upColor: '#ef4444', downColor: '#10b981',
      borderUpColor: '#ef4444', borderDownColor: '#10b981',
      wickUpColor: '#ef4444', wickDownColor: '#10b981',
    });
    candleSeries.setData(data.ohlcv);

    // 成交量(下方副圖)
    const volumeSeries = chart.addHistogramSeries({
      color: '#94a3b8',
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });
    volumeSeries.setData(data.ohlcv.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? '#fca5a5' : '#86efac',
    })));

    // MA 三條(visual.line_styles)
    [20, 60, 90].forEach(w => {
      const arr = data.ma?.[`ma_${w}`];
      if (!arr) return;
      const styleName = visual.line_styles?.[`ma_${w}`] ?? 'dotted';
      const series = chart.addLineSeries({
        color: w === 20 ? '#fbbf24' : (w === 60 ? '#a855f7' : '#06b6d4'),
        lineWidth: visual.line_width?.[`ma_${w}`] ?? 1,
        lineStyle: styleNameToEnum(styleName),
        title: `MA${w}`,
        crosshairMarkerVisible: false,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      series.setData(
        arr.map((v, i) => v !== null ? { time: data.ohlcv[i].time, value: v } : null)
            .filter(x => x !== null)
      );
    });

    // 關鍵價水平線(沒 text 就不顯示 title,避免「key_price」字串擠在頂部)
    (data.key_prices?.lines || []).forEach(line => {
      const colorKey = line.color || 'black';
      const color = visual.line_colors?.[colorKey] || '#1f2937';
      const styleName = visual.line_styles?.[line.category] || 'solid';
      candleSeries.createPriceLine({
        price: parseFloat(line.price),
        color: color,
        lineWidth: 1,
        lineStyle: styleNameToEnum(styleName),
        axisLabelVisible: true,
        title: line.text || '',
      });
    });

    // 區域:用 SVG overlay 畫水平色帶(LWC 4.1 沒原生 area-band primitive)
    // 監聽 visibleTimeRangeChange + ResizeObserver redraw,跟 chart scroll/zoom 同步
    const areas = (data.key_prices?.areas || []).filter(
      a => visual.area_colors?.[a.category]
    );
    let areasOverlay = null;
    if (areas.length > 0) {
      chartEl.style.position = 'relative';
      areasOverlay = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      areasOverlay.setAttribute('class', 'chart-area-overlay');
      areasOverlay.style.position = 'absolute';
      areasOverlay.style.left = '0';
      areasOverlay.style.top = '0';
      areasOverlay.style.width = '100%';
      areasOverlay.style.height = '100%';
      areasOverlay.style.pointerEvents = 'none';
      areasOverlay.style.zIndex = '2';   // 蓋在 LWC canvas 上(預設 z-index 0/auto)
      chartEl.appendChild(areasOverlay);
    }

    function redrawAreas() {
      if (!areasOverlay) return;
      // 清空
      while (areasOverlay.firstChild) areasOverlay.removeChild(areasOverlay.firstChild);
      const widthAttr = chartEl.clientWidth || 660;
      areasOverlay.setAttribute('width',  widthAttr);
      areasOverlay.setAttribute('height', chartEl.clientHeight || 420);
      // 扣掉右側 priceScale 寬度(預設 ~54),避免色帶溢出進價格軸
      const priceScaleWidth = chart.priceScale('right').width?.() ?? 54;
      const drawWidth = Math.max(0, widthAttr - priceScaleWidth);

      for (const a of areas) {
        const colorObj = visual.area_colors[a.category];
        const yHigh = candleSeries.priceToCoordinate(parseFloat(a.high));
        const yLow  = candleSeries.priceToCoordinate(parseFloat(a.low));
        if (yHigh == null || yLow == null) continue;
        const top    = Math.min(yHigh, yLow);
        const height = Math.abs(yHigh - yLow);

        // 半透明色塊(把 area_colors.top 的 alpha 提到 0.22 才看得清)
        const fill = (colorObj.top || 'rgba(156,163,175,0.22)').replace(
          /([\d.]+)\)$/,
          (_, _alpha) => '0.22)'
        );
        const stroke = (colorObj.top || 'rgba(156,163,175,0.5)').replace(
          /([\d.]+)\)$/,
          (_, _alpha) => '0.55)'
        );

        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', '0');
        rect.setAttribute('y', String(top));
        rect.setAttribute('width', String(drawWidth));
        rect.setAttribute('height', String(height));
        rect.setAttribute('fill', fill);
        rect.setAttribute('stroke', stroke);
        rect.setAttribute('stroke-width', '1');
        rect.setAttribute('stroke-dasharray', '4 3');
        areasOverlay.appendChild(rect);

        // 標籤(area.text)放在區域左上角內側
        const text = a.text || a.category;
        if (text) {
          const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          label.setAttribute('x', '6');
          label.setAttribute('y', String(top + 12));
          label.setAttribute('fill', stroke.replace('0.55)', '0.9)'));
          label.setAttribute('font-size', '10');
          label.setAttribute('font-family', 'system-ui, sans-serif');
          label.textContent = text;
          areasOverlay.appendChild(label);
        }
      }
    }

    // 訂閱:時間軸 / 價格軸變動 → redraw
    if (areasOverlay) {
      chart.timeScale().subscribeVisibleTimeRangeChange(redrawAreas);
      // 首次 chart fitContent 之後再 redraw 一次(座標才穩定)
      requestAnimationFrame(redrawAreas);
    }

    // 事件 markers(站穩/跌破)+ ETF 箭頭整合到 markers
    let allMarkers = [];

    // standing/breakdown events
    (data.events || []).forEach(ev => {
      if (ev.type === 'standing') {
        allMarkers.push({
          time: ev.time, position: 'belowBar',
          color: visual.event_markers?.standing?.color || '#22c55e',
          shape: 'arrowUp', text: '🟢',
        });
      } else if (ev.type === 'breakdown') {
        allMarkers.push({
          time: ev.time, position: 'aboveBar',
          color: visual.event_markers?.breakdown?.color || '#ef4444',
          shape: 'arrowDown', text: '🔴',
        });
      }
    });

    // 投信買超 K 線標記(§3.5,橘色圓點——不與紅漲綠跌 K 棒/事件、ETF 藍箭頭衝突)
    const trustMarkColor = visual.chip_trust_buy || '#f97316';
    (data.chips?.trust_markers || []).forEach(m => {
      allMarkers.push({
        time: m.time, position: 'belowBar', color: trustMarkColor,
        shape: 'circle', text: '投',
      });
    });

    // ETF 箭頭(▲ 買單色 / ▼ 賣單色,2026-06-01 朋友確認不分 ETF 顏色)
    const etfBuyColor  = visual.etf_arrow_buy  || '#3b82f6';
    const etfSellColor = visual.etf_arrow_sell || '#ef4444';
    const BUY_ACTIONS  = new Set(['加碼', '建倉']);
    const SELL_ACTIONS = new Set(['減碼', '清倉']);

    function buildEtfMarkers(activeEtfs) {
      return (data.etf_events || [])
        .filter(e => activeEtfs.has(e.etf))
        .map(e => ({
          time: e.time,
          position: BUY_ACTIONS.has(e.action) ? 'belowBar' : 'aboveBar',
          color: BUY_ACTIONS.has(e.action) ? etfBuyColor : etfSellColor,
          shape: BUY_ACTIONS.has(e.action) ? 'arrowUp' : 'arrowDown',
          text: '',
          // 自訂屬性給 tooltip 用
          id: `${e.time}_${e.etf}_${e.action}`,
          _etf: e.etf, _action: e.action, _shares: e.shares,
        }));
    }

    function applyAllMarkers(activeEtfs) {
      const etfMarkers = buildEtfMarkers(activeEtfs);
      const combined = [...allMarkers, ...etfMarkers]
        .sort((a, b) => a.time < b.time ? -1 : 1);
      candleSeries.setMarkers(combined);
    }

    // ETF filter bar(在 chart 上方,但已 append chartEl;改插入到前面)
    const allEtfs = [...new Set((data.etf_events || []).map(e => e.etf))];
    if (allEtfs.length > 0) {
      const initialActive = new Set(allEtfs);
      const filterBar = document.createElement('div');
      filterBar.className = 'etf-filter-bar';
      filterBar.innerHTML = '<strong>ETF:</strong>';
      allEtfs.sort().forEach(etf => {
        const label = document.createElement('label');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = true;
        cb.value = etf;
        cb.addEventListener('change', () => {
          if (cb.checked) initialActive.add(etf);
          else initialActive.delete(etf);
          applyAllMarkers(initialActive);
        });
        label.appendChild(cb);
        label.append(' ' + etf);
        filterBar.appendChild(label);
      });
      container.insertBefore(filterBar, chartEl);
      applyAllMarkers(initialActive);
    } else {
      applyAllMarkers(new Set());
    }

    // Legend(說明圖標含義 + 顯示資料密度)
    const eventCount = (data.events || []).length;
    const etfEventCount = (data.etf_events || []).length;
    const legend = document.createElement('div');
    legend.className = 'chart-legend';
    const etfNote = etfEventCount > 0
      ? `<span class="legend-item"><span class="legend-arrow" style="color:${etfBuyColor}">▲</span> ETF 加碼</span>`
        + `<span class="legend-item"><span class="legend-arrow" style="color:${etfSellColor}">▼</span> ETF 減碼</span>`
        + `<span class="legend-count">(${etfEventCount} 筆 ETF 動作)</span>`
      : `<span class="legend-note">過去 ${data.ohlcv.length} 天無 ETF 動作</span>`;
    const trustMarkCount = (data.chips?.trust_markers || []).length;
    const trustNote = trustMarkCount > 0
      ? `<span class="legend-item"><span class="legend-arrow" style="color:${trustMarkColor}">●</span> 投信買超</span>`
      : '';
    legend.innerHTML =
      `<span class="legend-item"><span class="legend-dot up">🟢</span> 站穩</span>`
      + `<span class="legend-item"><span class="legend-dot down">🔴</span> 跌破</span>`
      + etfNote
      + trustNote
      + `<span class="legend-count">(${eventCount} 筆站穩/跌破事件)</span>`;
    // P0-A:資料晚於 data_date(美股 19:00 台北跑時晚一個交易日)→ 灰色標註「資料至 MM-DD」
    if (data.data_through && data.data_date && data.data_through < data.data_date) {
      legend.innerHTML +=
        `<span class="legend-stale">資料至 ${data.data_through}</span>`;
    }
    // §3 legend 移圖上方 + 股名代號+週期標題(MA 圓點沿用實際線色,忠於圖表);
    // 內容(計數/stale)保留不動,只改位置與樣式
    legend.innerHTML =
      `<span class="legend-title">${esc0(data.name || '')} ${esc0(data.code || '')} · 1D</span>`
      + `<span class="legend-item"><span style="color:#fbbf24">●</span> MA20</span>`
      + `<span class="legend-item"><span style="color:#a855f7">●</span> MA60</span>`
      + `<span class="legend-item"><span style="color:#06b6d4">●</span> MA90</span>`
      + legend.innerHTML;
    container.insertBefore(legend, container.firstChild);

    // ─── 籌碼小區 v2(交接包 §5:4 格數字列 + 三大法人合計 20 日純 CSS 柱)───
    // 紅=買超、綠=賣超(唯一紅綠語意);ETF 走 --etf-buy/--etf-sell;純顯示不進計分。
    function renderChips(chips) {
      if (!chips || !Array.isArray(chips.dates) || chips.dates.length === 0) return;
      const host = container.closest('.card-body') || container.parentElement || container;
      host.querySelectorAll('.chips2').forEach(e => e.remove());   // 去重(重渲染)
      const wrap = document.createElement('div');
      wrap.className = 'chips2';

      const n = chips.dates.length;
      const F = chips.foreign_net || [], T = chips.trust_net || [];
      const D = chips.dealer_net || [];                 // 舊 chart JSON 無此欄 → 顯示 —
      const hasDealer = D.some(v => v != null);
      const sum = chips.dates.map((_, i) =>
        (F[i] || 0) + (T[i] || 0) + (hasDealer ? (D[i] || 0) : 0));

      function lot(v) { return v == null ? null : v; }
      function cellVal(v, kind) {
        if (v == null) return '<span class="c2-val na">—</span>';
        const cls = v > 0 ? 'up' : (v < 0 ? 'down' : 'na');
        const sign = v > 0 ? '+' : '';
        return `<span class="c2-val ${kind || cls}">${sign}${Number(v).toLocaleString()}</span>`;
      }
      // ETF 7 日:由 etf_events 就地彙總(±張數、檔數;無 → 「— 無共識」)
      let etfCell = '<span class="c2-val na">— 無共識</span>';
      try {
        const thru = new Date((data.data_through || data.data_date) + 'T00:00:00+08:00');
        const from = new Date(thru.getTime() - 6 * 86400000);
        const BUY = new Set(['加碼', '建倉']);
        let net = 0; const etfs = new Set();
        (data.etf_events || []).forEach(e => {
          const d = new Date(e.time + 'T00:00:00+08:00');
          if (d < from || d > thru) return;
          net += (BUY.has(e.action) ? 1 : -1) * (Number(e.shares) || 0);
          etfs.add(e.etf);
        });
        if (etfs.size > 0) {
          const arrow = net >= 0 ? '▲' : '▽';
          const cls = net >= 0 ? 'etf-buy' : 'etf-sell';
          etfCell = `<span class="c2-val ${cls}">${arrow} ${net > 0 ? '+' : ''}${net.toLocaleString()} · ${etfs.size}檔</span>`;
        }
      } catch (e) { /* etf events 缺 → 保持無共識 */ }

      const cells = document.createElement('div');
      cells.className = 'chips2-cells';
      cells.innerHTML =
        `<div class="chips2-cell"><span class="c2-label">外資</span>${cellVal(lot(F[n-1]))}</div>`
        + `<div class="chips2-cell"><span class="c2-label">投信</span>${cellVal(lot(T[n-1]))}</div>`
        + `<div class="chips2-cell"><span class="c2-label">自營</span>${cellVal(hasDealer ? lot(D[n-1]) : null)}</div>`
        + `<div class="chips2-cell"><span class="c2-label">ETF 7日</span>${etfCell}</div>`;
      wrap.appendChild(cells);

      // 2026-07-07 調整:三張 20 日柱垂直堆疊——外資 / 投信 / 三大法人合計
      // (資料零新增:chips 已含 foreign_net/trust_net;紅買綠賣、零軸置中、±刻度同現版)
      const kfmt = x => (Math.abs(x) >= 1000 ? (x / 1000).toFixed(x % 1000 ? 1 : 0) + 'k' : String(x));
      const mmdd = s => (s || '').slice(5).replace('-', '/');
      function barChart(title, series) {
        const vals = series.map(v => v || 0);
        const today = vals[n - 1] || 0;
        const head = document.createElement('div');
        head.className = 'chips2-barhead';
        head.innerHTML =
          `<span>${title} · ${n} 日(張)</span>`
          + `<span class="chips2-today ${today >= 0 ? 'up' : 'down'}">今日 ${today > 0 ? '+' : ''}${today.toLocaleString()}</span>`;
        wrap.appendChild(head);
        const maxAbs = Math.max.apply(null, vals.map(Math.abs).concat([1]));
        const chart = document.createElement('div');
        chart.className = 'chips2-chart chips2-multi';
        const bars = document.createElement('div');
        bars.className = 'chips2-bars';
        vals.forEach(v => {
          const b = document.createElement('div');
          b.className = 'chips2-bar';
          const i = document.createElement('i');
          i.className = v >= 0 ? 'up' : 'down';
          i.style.height = Math.max(2, Math.round(Math.abs(v) / maxAbs * 50)) + '%';
          b.appendChild(i);
          bars.appendChild(b);
        });
        const scale = document.createElement('div');
        scale.className = 'chips2-scale';
        scale.innerHTML = `<span>+${kfmt(maxAbs)}</span><span>0</span><span>-${kfmt(maxAbs)}</span>`;
        chart.appendChild(bars); chart.appendChild(scale);
        wrap.appendChild(chart);
      }
      barChart('外資買賣超', F.slice(0, n));
      barChart('投信買賣超', T.slice(0, n));
      barChart(`三大法人${hasDealer ? '' : '(外資+投信)'}合計`, sum);

      const dates = document.createElement('div');
      dates.className = 'chips2-dates';
      dates.innerHTML = `<span>${mmdd(chips.dates[0])}</span><span>${mmdd(chips.dates[n-1])}</span>`;
      wrap.appendChild(dates);

      // 千張大戶/融資(stage9 §3.5 既有資訊,補充列保留——設計落差清單項,待朋友 review)
      const holder = chips.large_holder;
      const marginVals = (chips.margin || []).filter(v => v != null);
      const marginLast = marginVals.length ? marginVals[marginVals.length - 1] : null;
      const extras = [];
      if (holder) extras.push(`千張大戶 ${holder.ratio}%`);
      if (marginLast != null) extras.push(`融資餘額 ${Number(marginLast).toLocaleString()} 張`);
      if (extras.length) {
        const ex = document.createElement('div');
        ex.className = 'chips2-extra';
        ex.textContent = extras.join(' · ');
        wrap.appendChild(ex);
      }

      // 展開態右欄(.card-right)優先;無(watchlist 舊結構)退回 card-body 區塊流
      const right = container.closest('.card-right');
      (right || host).appendChild(wrap);
    }
    renderChips(data.chips);

    // Tooltip for ETF events on hover
    const tooltip = document.createElement('div');
    tooltip.className = 'chart-tooltip';
    tooltip.style.display = 'none';
    document.body.appendChild(tooltip);

    // Lightweight Charts 把字串 time 轉成 BusinessDay {year,month,day} 物件,
    // 但 etf_events.time 仍是字串 — 不正規化會永遠 === false。
    function timeToStr(t) {
      if (typeof t === 'string') return t;
      if (t && typeof t === 'object' && 'year' in t) {
        const m = String(t.month).padStart(2, '0');
        const d = String(t.day).padStart(2, '0');
        return `${t.year}-${m}-${d}`;
      }
      if (typeof t === 'number') {
        return new Date(t * 1000).toISOString().slice(0, 10);
      }
      return String(t);
    }

    chart.subscribeCrosshairMove(param => {
      if (!param || !param.time) { tooltip.style.display = 'none'; return; }
      const timeStr = timeToStr(param.time);
      const matches = (data.etf_events || []).filter(e => e.time === timeStr);
      if (matches.length === 0) { tooltip.style.display = 'none'; return; }
      const lines = matches.map(m => `${m.etf} ${m.action} ${m.shares} 張`);
      tooltip.innerHTML = lines.join('<br>');
      tooltip.style.display = 'block';
      // position:absolute 接 body → 用頁面座標(viewport rect + scroll)
      const rect = chartEl.getBoundingClientRect();
      tooltip.style.left = (rect.left + window.scrollX + (param.point?.x || 0) + 12) + 'px';
      tooltip.style.top  = (rect.top  + window.scrollY + (param.point?.y || 0) + 12) + 'px';
    });

    // 隱藏 tooltip when leaving chart
    chartEl.addEventListener('mouseleave', () => { tooltip.style.display = 'none'; });

    // Resize 處理 + 同步 area overlay 尺寸
    new ResizeObserver(entries => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width });
      }
      redrawAreas();
    }).observe(chartEl);

    chart.timeScale().fitContent();
    // fitContent 之後 priceScale range 才穩定,再 redraw 一次
    requestAnimationFrame(redrawAreas);
  }

  // ─── 為每個 placeholder 綁定載入邏輯 ───────────────────────────────────
  function setupPlaceholder(placeholder) {
    const symbol = placeholder.dataset.symbol;
    const date   = placeholder.dataset.date;
    if (!symbol || !date) return;

    let loaded = false;
    // 找包住 placeholder 的最近 <details>(index_v2 是 .stock-card,
    // watchlist_v2 是 .wl-stock;closest 走上來抓最近的,不會搶到板塊那層)
    const parent = placeholder.closest('details');
    if (!parent) return;

    async function load() {
      if (loaded) return;
      loaded = true;
      placeholder.classList.add('loading');
      placeholder.textContent = '⏳ 載入中...';
      try {
        await loadLightweightCharts();
        const [data, visual] = await Promise.all([
          loadChartData(symbol, date),
          loadVisual(),
        ]);
        await renderChart(placeholder, data, visual);
      } catch (e) {
        console.error(e);
        // 404 多半是:美股在台北時間下午跑時還沒收盤 / 新加個股當日 JSON 還沒部署
        const is404 = /404/.test(e.message);
        const isUS = /^(NASDAQ|NYSE):/.test(symbol);
        placeholder.classList.remove('loading');
        placeholder.classList.add(is404 ? 'awaiting' : 'errored');
        if (is404) {
          placeholder.textContent = isUS
            ? '⏳ 美股收盤資料尚未更新(將於下個交易日更新)'
            : '⏳ K 線資料尚未上線(可能剛新增,請稍候重整)';
        } else {
          placeholder.textContent = `⚠️ 載入失敗:${e.message}`;
        }
        loaded = false;   // 允許再試
      }
    }

    parent.addEventListener('toggle', () => {
      if (parent.open) load();
    });

    // 初始檢查:DOMContentLoaded 時若 details 已展開(session restore / open 屬性),立刻 load
    if (parent.open) load();

  }

  // ─── 初始化 ────────────────────────────────────────────────────────────
  function init() {
    document.querySelectorAll('.chart-placeholder').forEach(setupPlaceholder);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
