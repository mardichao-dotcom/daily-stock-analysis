/**
 * diagnose_tv_collect.mjs — 探測 TradingView Desktop 現在的內部 JS API 結構
 *
 * 不抓資料,只看物件路徑。5/31 失敗的 root cause 是 _activeChartWidgetWV
 * 不見了 — 這個腳本確認現在還有什麼可用、什麼被改名。
 *
 * 30 秒整體 timeout(別像 tv_collect 沒上限)。
 *
 * Run: node scripts/diagnose_tv_collect.mjs
 */

import CDP from '/Users/mardichao/tradingview-mcp/node_modules/chrome-remote-interface/index.js';

const CDP_PORT = 9222;
const GLOBAL_TIMEOUT_MS = 30000;

const log = (...a) => console.log('[diag]', ...a);

// ── 30s 整體 timeout ────────────────────────────────────────────────────────
const killer = setTimeout(() => {
  console.error('[diag] ⏱  30s timeout — bailing out');
  process.exit(2);
}, GLOBAL_TIMEOUT_MS);

async function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, rej) => setTimeout(() => rej(new Error(`${label} timed out after ${ms}ms`)), ms)),
  ]);
}

async function fetchJsonList() {
  const resp = await withTimeout(
    fetch(`http://localhost:${CDP_PORT}/json/list`),
    5000, 'fetch /json/list',
  );
  return await resp.json();
}

async function connectChartTarget() {
  const targets = await fetchJsonList();
  log(`/json/list returned ${targets.length} targets`);
  const tvTargets = targets.filter(t => /tradingview/i.test(t.url || ''));
  log(`  tradingview-related: ${tvTargets.length}`);
  for (const t of tvTargets) log(`    [${t.type}] ${t.title?.slice(0,40) || '(no title)'} — ${t.url?.slice(0,80)}`);
  const chartTarget = targets.find(t => t.type === 'page' && /tradingview\.com\/chart/i.test(t.url))
                   || targets.find(t => t.type === 'page' && /tradingview/i.test(t.url));
  if (!chartTarget) throw new Error('No TradingView chart page target found');
  log(`✓ picked target: ${chartTarget.url}`);
  const client = await withTimeout(
    CDP({ host: 'localhost', port: CDP_PORT, target: chartTarget.id }),
    5000, 'CDP.connect',
  );
  await client.Runtime.enable();
  return client;
}

async function ev(client, expr) {
  const res = await withTimeout(
    client.Runtime.evaluate({ expression: expr, returnByValue: true }),
    3000, `eval(${expr.slice(0,40)})`,
  );
  if (res.exceptionDetails) {
    return { ok: false, err: res.exceptionDetails.exception?.description || res.exceptionDetails.text };
  }
  return { ok: true, value: res.result?.value };
}

async function probe(client, label, expr) {
  const r = await ev(client, expr);
  if (r.ok) {
    const v = r.value;
    const t = typeof v;
    const display = t === 'object' ? JSON.stringify(v).slice(0, 80) : String(v).slice(0, 80);
    log(`  ✓ ${label.padEnd(40)} → ${t} :: ${display}`);
  } else {
    log(`  ✗ ${label.padEnd(40)} → ERR: ${r.err.split('\n')[0].slice(0, 100)}`);
  }
  return r;
}

async function main() {
  log(`probing TradingView Desktop CDP at port ${CDP_PORT}`);
  const client = await connectChartTarget();

  log('');
  log('── Step 1: 看 window.TradingViewApi 本體 ──────────────────────');
  await probe(client, 'typeof window.TradingViewApi',
    'typeof window.TradingViewApi');
  await probe(client, 'Object.keys(window.TradingViewApi).slice(0,30)',
    '(()=>{try{return Object.keys(window.TradingViewApi).slice(0,30)}catch(e){return e.message}})()');

  log('');
  log('── Step 2: 5/31 失敗的舊路徑 _activeChartWidgetWV ─────────────');
  await probe(client, 'typeof window.TradingViewApi._activeChartWidgetWV',
    'typeof window.TradingViewApi._activeChartWidgetWV');

  log('');
  log('── Step 3: 試找新的 chart widget 入口 ─────────────────────────');
  await probe(client, 'window.TradingViewApi.activeChart?.symbol?.()',
    'window.TradingViewApi.activeChart && window.TradingViewApi.activeChart().symbol && window.TradingViewApi.activeChart().symbol()');
  await probe(client, 'TradingView.TradingViewWidget?.constructor?.name',
    'TradingView && TradingView.TradingViewWidget && TradingView.TradingViewWidget.constructor.name');
  await probe(client, 'document.querySelector(\\".chart-container\\")?.tagName',
    'document.querySelector(".chart-container")?.tagName');
  await probe(client, 'document.querySelectorAll(\\"iframe\\").length',
    'document.querySelectorAll("iframe").length');

  log('');
  log('── Step 4: window 上跟 chart/widget 相關的 key ─────────────────');
  await probe(client, 'window keys matching chart/widget/tv/symbol',
    '(()=>{try{return Object.keys(window).filter(k=>/chart|widget|tv|symbol/i.test(k)).slice(0,30)}catch(e){return e.message}})()');

  log('');
  log('── Step 5: 當前頁面 URL + title(看是 chart 還是登入 / 首頁) ──');
  await probe(client, 'document.title',     'document.title');
  await probe(client, 'location.href',      'location.href');
  await probe(client, 'document.body.innerText.length', 'document.body.innerText.length');

  log('');
  log('── Step 6: 可能的 React/Vue root 存在嗎(版本判定線索) ───────');
  await probe(client, 'document.querySelector(\\"[data-name]\\")?.dataset?.name',
    'document.querySelector("[data-name]")?.dataset?.name');
  await probe(client, 'TVAPP version key', 'window.TVAPP?.version || window.__TV_APP_VERSION__ || "no version key"');

  await client.close();
  clearTimeout(killer);
  log('');
  log('✓ done');
}

main().catch(e => {
  console.error('[diag] FATAL:', e.message);
  process.exit(1);
});
