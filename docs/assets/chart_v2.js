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

  // ─── 渲染單張 chart ─────────────────────────────────────────────────────
  async function renderChart(container, data, visual) {
    container.innerHTML = '';
    container.style.minHeight = '420px';

    // 建立 chart 容器(扣掉 ETF filter bar 的高度)
    const chartEl = document.createElement('div');
    chartEl.style.width = '100%';
    chartEl.style.height = '420px';

    // 先掛進 DOM,讓 clientWidth 量得到(details 剛展開時若仍是 0,退到 660)
    container.appendChild(chartEl);

    const chart = LightweightCharts.createChart(chartEl, {
      width:  chartEl.clientWidth || 660,
      height: 420,
      layout: { background: { color: '#fff' }, textColor: '#333' },
      grid:   { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
      timeScale: { timeVisible: false, secondsVisible: false },
      rightPriceScale: { borderColor: '#e5e7eb' },
    });

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
    legend.innerHTML =
      `<span class="legend-item"><span class="legend-dot up">🟢</span> 站穩</span>`
      + `<span class="legend-item"><span class="legend-dot down">🔴</span> 跌破</span>`
      + etfNote
      + `<span class="legend-count">(${eventCount} 筆站穩/跌破事件)</span>`;
    // P0-A:資料晚於 data_date(美股 19:00 台北跑時晚一個交易日)→ 灰色標註「資料至 MM-DD」
    if (data.data_through && data.data_date && data.data_through < data.data_date) {
      legend.innerHTML +=
        `<span class="legend-stale">資料至 ${data.data_through}</span>`;
    }
    container.appendChild(legend);

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
