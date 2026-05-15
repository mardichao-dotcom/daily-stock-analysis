/**
 * chart.js — TradingView Lightweight Charts v5 渲染模組
 * 台股配色：漲紅跌綠
 *
 * renderChart(containerId, chartData)
 *   containerId : HTML 元素 id
 *   chartData   : data/{date}/{code}.json 的內容物件
 */

(function (global) {
  'use strict';

  const C = {
    up:          '#ef5350',
    down:        '#26a69a',
    buyBright:   '#00e676',
    buyDim:      '#388e3c',
    sellBright:  '#ff1744',
    sellDim:     '#c62828',
    bg:          '#131722',
    grid:        '#1e2535',
    text:        '#d1d4dc',
    crosshair:   '#758696',
    tipBg:       'rgba(19,23,34,0.95)',
    tipBorder:   '#2a3a5c',
  };

  // ── 關鍵價配色 ────────────────────────────────────────────────────────────────
  const KP = {
    lineNormal: { color: '#78909c', lineWidth: 1, lineStyle: 2 },
    linePOC:    { color: '#ff9800', lineWidth: 1, lineStyle: 2 },
    zone: {
      '跳空缺口': { fill: 'rgba(255,214,0,0.09)',   text: 'rgba(255,220,80,0.80)' },
      '訂單塊':   { fill: 'rgba(171,71,188,0.12)',  text: 'rgba(206,147,216,0.88)' },
      'POC':      { fill: 'rgba(255,152,0,0.11)',   text: 'rgba(255,183,77,0.88)' },
      'default':  { fill: 'rgba(120,144,156,0.09)', text: 'rgba(176,190,197,0.75)' },
    },
  };

  function _zoneStyle(label, isPoc) {
    if (isPoc) return KP.zone['POC'];
    for (const key of ['跳空缺口', '訂單塊']) {
      if (label.includes(key)) return KP.zone[key];
    }
    return KP.zone['default'];
  }

  // ── ZoneBandPlugin（LW Charts v5 series primitive）────────────────────────────
  class ZoneBandPlugin {
    constructor(low, high, label, fill, textColor) {
      this._low       = low;
      this._high      = high;
      this._label     = label;
      this._fill      = fill;
      this._textColor = textColor;
      this._series    = null;
    }

    attached({ series }) { this._series = series; }
    detached()           { this._series = null;   }

    paneViews() {
      const self = this;
      return [{
        renderer() {
          return {
            draw(target) {
              const s = self._series;
              if (!s) return;
              target.useBitmapCoordinateSpace(({
                context: ctx, bitmapSize,
                verticalPixelRatio:   vpr,
                horizontalPixelRatio: hpr,
              }) => {
                const yH = s.priceToCoordinate(self._high);
                const yL = s.priceToCoordinate(self._low);
                if (yH === null || yL === null) return;

                const y1 = Math.round(Math.min(yH, yL) * vpr);
                const y2 = Math.round(Math.max(yH, yL) * vpr);
                const h  = y2 - y1;
                if (h < 1) return;

                // 半透明填色
                ctx.fillStyle = self._fill;
                ctx.fillRect(0, y1, bitmapSize.width, h);

                // 中央文字（帶高 ≥ 14px 才顯示）
                if (h >= Math.round(14 * vpr)) {
                  ctx.save();
                  ctx.font =
                    `600 ${Math.round(10 * vpr)}px -apple-system,"Segoe UI",sans-serif`;
                  ctx.fillStyle    = self._textColor;
                  ctx.textBaseline = 'middle';
                  ctx.textAlign    = 'left';
                  ctx.fillText(self._label, Math.round(8 * hpr), y1 + h / 2);
                  ctx.restore();
                }
              });
            },
          };
        },
        zOrder() { return 'bottom'; },
      }];
    }
  }

  // ── 關鍵價繪製（水平線 + 區域帶）──────────────────────────────────────────────
  function drawKeyPrices(candleSeries, keyPrices) {
    if (!keyPrices || !keyPrices.marks || keyPrices.marks.length === 0) return;
    for (const mark of keyPrices.marks) {
      if (mark.type === 'line') {
        const isPoc = !!mark.is_poc;
        candleSeries.createPriceLine({
          price:            mark.price,
          color:            isPoc ? KP.linePOC.color : KP.lineNormal.color,
          lineWidth:        1,
          lineStyle:        2,
          axisLabelVisible: true,
          title:            mark.label,
        });
      } else if (mark.type === 'zone') {
        const st = _zoneStyle(mark.label, !!mark.is_poc);
        candleSeries.attachPrimitive(
          new ZoneBandPlugin(mark.low, mark.high, mark.label, st.fill, st.text)
        );
      }
    }
  }

  // ── marker 計算（純函式）─────────────────────────────────────────────────

  function computeMarkers(rawMarkers, enabledEtfs) {
    const result = [];
    for (const m of rawMarkers) {
      // Checkbox is a display filter only — hide marker if none of its ETFs are enabled
      const filteredDetail = m.detail.filter(d => enabledEtfs.has(d.etf));
      if (filteredDetail.length === 0) continue;

      // is_consensus is a historical fact fixed at data generation time; never recalculate
      const isConsensus = m.is_consensus;
      const isBuy       = m.direction === 'buy';

      result.push({
        // LW Charts marker fields
        time:     m.time,
        position: isBuy ? 'belowBar' : 'aboveBar',
        color:    isBuy
          ? (isConsensus ? C.buyBright  : C.buyDim)
          : (isConsensus ? C.sellBright : C.sellDim),
        shape:    isBuy ? 'arrowUp' : 'arrowDown',
        text:     m.action,   // action word only — detail goes in tooltip
        size:     isConsensus ? 2 : 1,
        // extra payload for tooltip
        _time:        m.time,
        _dir:         m.direction,
        _action:      m.action,
        _summary:     m.summary,      // original full summary from data
        _isConsensus: isConsensus,
        _detail:      filteredDetail, // only enabled ETFs shown in tooltip
      });
    }
    result.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    return result;
  }

  // ── range helper ──────────────────────────────────────────────────────────

  function setRange(chart, ohlcv, bars) {
    const n    = ohlcv.length;
    const from = ohlcv[Math.max(0, n - bars)].time;
    const to   = ohlcv[n - 1].time;
    // RAF ensures this runs after LWC's internal auto-fit frame (triggered by
    // setData), so our range wins on initial load and every button click.
    requestAnimationFrame(() => chart.timeScale().setVisibleRange({ from, to }));
  }

  // ── tooltip ───────────────────────────────────────────────────────────────

  function makeTooltip(parent) {
    const el = document.createElement('div');
    el.style.cssText =
      `position:absolute;display:none;z-index:100;` +
      `background:${C.tipBg};border:1px solid ${C.tipBorder};` +
      `border-radius:6px;padding:8px 12px;font-size:12px;` +
      `color:${C.text};pointer-events:none;min-width:180px;line-height:1.7;`;
    parent.appendChild(el);
    return el;
  }

  // ── main ──────────────────────────────────────────────────────────────────

  function renderChart(containerId, chartData) {
    const wrap = document.getElementById(containerId);
    if (!wrap) { console.error('renderChart: #' + containerId + ' not found'); return; }
    wrap.innerHTML = '';
    wrap.style.position = 'relative';

    const { ohlcv, etf_markers: rawMarkers, name, code, grade, key_prices } = chartData;
    const LWC = window.LightweightCharts;

    // ── 狀態 ──
    const allEtfs     = [...new Set(rawMarkers.flatMap(m => m.detail.map(d => d.etf)))].sort();
    const enabledEtfs = new Set(allEtfs);
    let   activeRange = 180;  // 預設 6 個月

    // ── 標題列 ──
    const hdr = document.createElement('div');
    hdr.style.cssText =
      `display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap;`;
    const titleEl = document.createElement('span');
    titleEl.textContent = `${name}（${code}）`;
    titleEl.style.cssText = `font-weight:600;font-size:14px;color:${C.text}`;

    const gradeEl = document.createElement('span');
    gradeEl.textContent = grade;
    gradeEl.style.cssText =
      `padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;` +
      (grade === 'S級'
        ? `background:#ef535033;color:#ef5350`
        : `background:#ff980033;color:#ff9800`);

    const spacer = document.createElement('span');
    spacer.style.flex = '1';

    const makeBtnStyle = active =>
      `padding:3px 10px;border-radius:4px;cursor:pointer;border:none;font-size:12px;` +
      (active ? `background:#2962ff;color:#fff` : `background:#1e2535;color:${C.text}`);

    const btn3m = document.createElement('button');
    const btn6m = document.createElement('button');
    btn3m.textContent = '3 個月';
    btn6m.textContent = '6 個月';

    function syncBtnStyles() {
      btn3m.style.cssText = makeBtnStyle(activeRange === 90);
      btn6m.style.cssText = makeBtnStyle(activeRange === 180);
    }
    syncBtnStyles();

    hdr.append(titleEl, gradeEl, spacer, btn3m, btn6m);
    wrap.appendChild(hdr);

    // ── ETF 勾選框 ──
    if (allEtfs.length > 0) {
      const bar = document.createElement('div');
      bar.style.cssText =
        `display:flex;flex-wrap:wrap;gap:4px 12px;padding:4px 0 8px;` +
        `border-bottom:1px solid ${C.grid};margin-bottom:6px;font-size:12px;color:${C.text};`;
      const lbl0 = document.createElement('span');
      lbl0.textContent = 'ETF：';
      lbl0.style.opacity = '0.55';
      bar.appendChild(lbl0);

      allEtfs.forEach(etf => {
        const lbl = document.createElement('label');
        lbl.style.cssText = `display:flex;align-items:center;gap:3px;cursor:pointer;`;
        const cb = document.createElement('input');
        cb.type = 'checkbox'; cb.checked = true; cb.value = etf;
        cb.style.accentColor = '#2962ff';
        cb.addEventListener('change', () => {
          if (cb.checked) enabledEtfs.add(etf);
          else enabledEtfs.delete(etf);
          refreshMarkers();
        });
        lbl.append(cb, document.createTextNode(etf));
        bar.appendChild(lbl);
      });
      wrap.appendChild(bar);
    }

    // ── 圖表容器 ──
    const chartDiv = document.createElement('div');
    chartDiv.style.cssText = `width:100%;height:400px;position:relative;`;
    wrap.appendChild(chartDiv);

    // ── LW Charts ──
    const chart = LWC.createChart(chartDiv, {
      width:  chartDiv.clientWidth || 800,
      height: 400,
      layout:  { background: { color: C.bg }, textColor: C.text },
      grid:    { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: { vertLine: { color: C.crosshair }, horzLine: { color: C.crosshair } },
      rightPriceScale: { borderColor: C.grid },
      timeScale: { borderColor: C.grid, timeVisible: true, secondsVisible: false },
    });

    new ResizeObserver(() => chart.applyOptions({ width: chartDiv.clientWidth }))
      .observe(chartDiv);

    // 成交量（先加，讓 K 線在上層）
    const volSeries = chart.addSeries(LWC.HistogramSeries, {
      color: C.up, priceFormat: { type: 'volume' }, priceScaleId: 'vol',
    });
    volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    // K 線
    const candleSeries = chart.addSeries(LWC.CandlestickSeries, {
      upColor: C.up, downColor: C.down,
      borderUpColor: C.up, borderDownColor: C.down,
      wickUpColor: C.up, wickDownColor: C.down,
    });
    candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.25 } });

    candleSeries.setData(ohlcv.map(b =>
      ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })
    ));
    volSeries.setData(ohlcv.map(b =>
      ({ time: b.time, value: b.volume, color: b.close >= b.open ? C.up : C.down })
    ));

    // ── 關鍵價水平線 + 區域帶 ──
    drawKeyPrices(candleSeries, key_prices);

    // ── 「需檢查」警告（距更新日 > 30 天）──
    if (key_prices?.需檢查) {
      const warn = document.createElement('div');
      warn.textContent = '⚠ 關鍵價已 30 天未更新，請確認是否仍適用';
      warn.style.cssText =
        `font-size:11px;color:#ff9800;padding:2px 0 6px;`;
      wrap.insertBefore(warn, chartDiv);
    }

    // ── marker 狀態（統一管理）──
    // v5: markers live in a plugin wrapper, not directly on the series
    const markerApi = LWC.createSeriesMarkers(candleSeries, []);
    let currentMarkers = [];

    function refreshMarkers() {
      currentMarkers = computeMarkers(rawMarkers, enabledEtfs);
      markerApi.setMarkers(currentMarkers);
    }
    refreshMarkers();

    // ── hover tooltip ──
    const tooltip = makeTooltip(chartDiv);

    chart.subscribeCrosshairMove(param => {
      if (!param?.time) { tooltip.style.display = 'none'; return; }

      const hits = currentMarkers.filter(m => m._time === param.time);
      if (hits.length === 0) { tooltip.style.display = 'none'; return; }

      let html = `<div style="font-weight:600;margin-bottom:4px">${param.time}</div>`;
      for (const m of hits) {
        const arrow      = m._dir === 'buy' ? '▲' : '▼';
        const consensus  = m._isConsensus ? '（共識）' : '';
        html += `<div style="margin-top:4px;font-weight:500">${arrow} ${m._summary}${consensus}</div>`;
        html += m._detail.map((d, i) => {
          const branch = i === m._detail.length - 1 ? '└' : '├';
          return `<div style="display:flex;justify-content:space-between;gap:14px;opacity:.8">` +
            `<span>${branch} ${d.etf}</span><span>${d.action} ${d.shares.toLocaleString()} 張</span></div>`;
        }).join('');
      }
      tooltip.innerHTML = html;
      tooltip.style.display = 'block';

      const cw = chartDiv.clientWidth;
      const tw = tooltip.offsetWidth || 200;
      const x  = param.point?.x ?? cw / 2;
      const y  = param.point?.y ?? 50;
      tooltip.style.left = (x + tw + 16 > cw ? x - tw - 8 : x + 8) + 'px';
      tooltip.style.top  = Math.max(4, y - 8) + 'px';
    });

    // ── 範圍按鈕事件 ──
    btn3m.addEventListener('click', () => {
      activeRange = 90; syncBtnStyles(); setRange(chart, ohlcv, 90);
    });
    btn6m.addEventListener('click', () => {
      activeRange = 180; syncBtnStyles(); setRange(chart, ohlcv, 180);
    });

    // 預設 6 個月
    setRange(chart, ohlcv, 180);
  }

  // 匯出
  if (typeof module !== 'undefined' && module.exports) module.exports = { renderChart };
  else global.renderChart = renderChart;

}(typeof window !== 'undefined' ? window : this));
