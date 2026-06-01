/**
 * probe_new_api.mjs — 深入探測 TradingView Desktop 的新 chart API
 *
 * 焦點:用戶確認 window.TradingViewApi.activeChart() 可呼叫,
 *      返回 "BATS:SPY"。但 5/31 失敗的 tv_collect.mjs 用的是
 *      _activeChartWidgetWV.value() — 哪些 method 在新版被改名?
 *
 * 探測對象:
 *   - activeChart() 的回傳物件
 *   - _activeChartWidgetWV 物件
 *   - 找 setSymbol / bars / OHLCV 抓取入口
 *
 * 30 秒整體 timeout。Run: node scripts/probe_new_api.mjs
 */

import CDP from '/Users/mardichao/tradingview-mcp/node_modules/chrome-remote-interface/index.js';

const CDP_PORT = 9222;
const log = (...a) => console.log('[probe]', ...a);

const killer = setTimeout(() => {
  console.error('[probe] ⏱ 30s timeout — bailing');
  process.exit(2);
}, 30000);

async function withTimeout(p, ms, label) {
  return Promise.race([
    p,
    new Promise((_, rej) => setTimeout(() => rej(new Error(`${label} timeout ${ms}ms`)), ms)),
  ]);
}

async function ev(client, expr) {
  const res = await withTimeout(
    client.Runtime.evaluate({ expression: expr, returnByValue: true }),
    3000, `eval(${expr.slice(0,40)})`,
  );
  if (res.exceptionDetails) {
    return { ok: false, err: (res.exceptionDetails.exception?.description
                              || res.exceptionDetails.text).split('\n')[0] };
  }
  return { ok: true, value: res.result?.value };
}

async function probe(client, label, expr) {
  const r = await ev(client, expr);
  if (r.ok) {
    const v = r.value;
    const t = typeof v;
    const display = (t === 'object' || Array.isArray(v))
      ? JSON.stringify(v).slice(0, 200)
      : String(v).slice(0, 200);
    log(`  ✓ ${label.padEnd(48)} ${t.padEnd(8)} ${display}`);
  } else {
    log(`  ✗ ${label.padEnd(48)} ERR  ${r.err.slice(0, 100)}`);
  }
  return r;
}

async function connectChart() {
  const resp = await withTimeout(fetch(`http://localhost:${CDP_PORT}/json/list`),
                                  5000, 'fetch list');
  const targets = await resp.json();
  const t = targets.find(t => t.type === 'page' && /tradingview\.com\/chart/i.test(t.url))
         || targets.find(t => t.type === 'page' && /tradingview/i.test(t.url));
  if (!t) throw new Error('no chart target');
  log(`target: ${t.url.slice(0, 80)}`);
  const c = await withTimeout(CDP({ host: 'localhost', port: CDP_PORT, target: t.id }),
                                5000, 'connect');
  await c.Runtime.enable();
  return c;
}

async function main() {
  const client = await connectChart();

  log('');
  log('═══ A. _activeChartWidgetWV(舊路徑物件還在不在)═══');
  await probe(client, 'typeof window.TradingViewApi._activeChartWidgetWV',
    'typeof window.TradingViewApi._activeChartWidgetWV');
  await probe(client, '_activeChartWidgetWV keys (前30)',
    '(()=>{try{return Object.keys(window.TradingViewApi._activeChartWidgetWV).slice(0,30)}catch(e){return e.message}})()');
  for (const m of ['value', 'activeChart', 'getActiveChart', 'chart', '_chart',
                    'currentValue', 'getValue']) {
    await probe(client, `_activeChartWidgetWV.${m} (typeof)`,
      `typeof window.TradingViewApi._activeChartWidgetWV.${m}`);
  }

  log('');
  log('═══ B. activeChart() — 返回物件結構 ═══');
  await probe(client, 'typeof activeChart()',
    'typeof window.TradingViewApi.activeChart()');
  await probe(client, 'activeChart() keys (前40)',
    '(()=>{try{return Object.keys(window.TradingViewApi.activeChart()).slice(0,40)}catch(e){return e.message}})()');
  // 嘗試常見 method
  for (const m of ['symbol', 'resolution', 'setSymbol', 'setResolution',
                    'onSymbolChanged', 'symbolName', 'getSymbol',
                    'mainSeries', 'getSeries', 'series', 'allStudies',
                    'exportData', 'getVisibleRange', 'visibleRange',
                    'priceFormatter', 'getTimezone']) {
    await probe(client, `activeChart().${m} (typeof)`,
      `(()=>{try{return typeof window.TradingViewApi.activeChart().${m}}catch(e){return 'ERR:'+e.message.slice(0,40)}})()`);
  }
  // 試呼叫
  await probe(client, 'activeChart().symbol() 結果',
    '(()=>{try{return window.TradingViewApi.activeChart().symbol()}catch(e){return e.message}})()');
  await probe(client, 'activeChart().resolution() 結果',
    '(()=>{try{return window.TradingViewApi.activeChart().resolution()}catch(e){return e.message}})()');

  log('');
  log('═══ C. mainSeries / bars 入口 ═══');
  // 舊 BARS_PATH 是 ._chartWidget.model().mainSeries().bars()
  // 試新版能不能直接 activeChart().mainSeries() 之類
  await probe(client, 'typeof activeChart().mainSeries()',
    `(()=>{try{return typeof window.TradingViewApi.activeChart().mainSeries()}catch(e){return 'ERR:'+e.message.slice(0,80)}})()`);
  await probe(client, 'activeChart().mainSeries() keys',
    `(()=>{try{var s=window.TradingViewApi.activeChart().mainSeries();return Object.keys(s).slice(0,30)}catch(e){return 'ERR:'+e.message.slice(0,80)}})()`);
  await probe(client, 'typeof activeChart().mainSeries().bars',
    `(()=>{try{return typeof window.TradingViewApi.activeChart().mainSeries().bars}catch(e){return 'ERR:'+e.message.slice(0,40)}})()`);
  await probe(client, 'activeChart().mainSeries().bars() keys',
    `(()=>{try{var b=window.TradingViewApi.activeChart().mainSeries().bars();return b?Object.keys(b).slice(0,30):'null'}catch(e){return 'ERR:'+e.message.slice(0,80)}})()`);

  log('');
  log('═══ D. 舊路徑完整鏈條測試(看哪段最先 fail)═══');
  await probe(client, '_activeChartWidgetWV.value (直接 typeof)',
    `typeof window.TradingViewApi._activeChartWidgetWV.value`);
  await probe(client, '_activeChartWidgetWV.value()(舊 5/31 失敗點)',
    `(()=>{try{var v=window.TradingViewApi._activeChartWidgetWV.value();return typeof v+':'+(v?Object.keys(v).slice(0,10).join(','):'null')}catch(e){return 'ERR:'+e.message.slice(0,80)}})()`);
  await probe(client, '_chartWidget 存在?(若 value() 回 obj 後)',
    `(()=>{try{var v=window.TradingViewApi._activeChartWidgetWV.value();return typeof v._chartWidget}catch(e){return 'ERR:'+e.message.slice(0,40)}})()`);

  log('');
  log('═══ E. 對比:有 export 之類的 OHLCV 一次抓的 method 嗎 ═══');
  await probe(client, 'activeChart().exportData (typeof)',
    `typeof window.TradingViewApi.activeChart().exportData`);
  // 試 exportData 用法
  await probe(client, 'activeChart().exportData() 呼叫看回什麼',
    `(()=>{try{var p=window.TradingViewApi.activeChart().exportData();return p&&p.then?'Promise':typeof p}catch(e){return 'ERR:'+e.message.slice(0,80)}})()`);

  log('');
  log('═══ F. 當前 chart 狀態(symbol/resolution)═══');
  await probe(client, 'current symbol', `window.TradingViewApi.activeChart().symbol()`);
  await probe(client, 'current resolution', `window.TradingViewApi.activeChart().resolution()`);

  await client.close();
  clearTimeout(killer);
  log('');
  log('✓ done');
}

main().catch(e => {
  console.error('[probe] FATAL:', e.message);
  process.exit(1);
});
